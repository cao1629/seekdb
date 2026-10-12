# 基础设施 API 文档对抗审查：跨模块一致性与流程符合性

> - **审查对象**：`migration/infra/api/01-mem.md` … `09-parser-sys-generators.md`（9 份，约 1.56 万行）、`migration/infra/amendments.tsv`（77 行）；对照 `migration/RULEBOOK.md` v0.2（已签字，有约束力）、`migration/infra/README.md`、1c 的草稿 `migration/design/units.tsv` 与 `migration/scripts/inventory/README.md`。基线 `7d907abfa`。
> - **只管一类错误**：跨模块一致性与流程符合性。具体是：两份文档对同一 C++ 类型或单元的归属、Rust 名字、签名、语义假设互相矛盾；1d 模块之间的依赖成环；文档与 RULEBOOK 不一致而没有排修订；修订之间互相矛盾；文档 §1 的处置与 `units.tsv` 不一致；改名表之外的新名字；RULEBOOK §2 点名的设施没有文档提供或有两家提供；以及 77 行修订的逐行分诊。单个模块内部的正确性、并发、性能不在本文范围内，除非它影响别的模块。
> - **方式**：只读，没有构建，没有 git 写操作。9 份文档全文通读（07、08、09 由本文作者逐行读，01–06 由同一会话的分身逐行读并抽取交叉引用，本文作者对其中被本文引用的行号逐条回查）；77 行修订里的事实全部由本文作者回源码核对（第 4 节；`amendments.tsv` 在 2026-10-11 17:14 之后没有变化）。另一位审查者的 `review/fidelity-01-mem.md`、`review/fidelity-03-thread-signal.md` 只作为旁证引用，标"旁证"。临时脚本在 `/tmp/infra-consistency-review/`（`src.py` 读源码片段与计数，`errsim_scan.py` 按预处理分支统计注入点），没有入库。
> - **引用约定**：`01:81` 指 `migration/infra/api/01-mem.md` 第 81 行，其余文档同理；`RULEBOOK:N` 指 `migration/RULEBOOK.md` 第 N 行；源码路径省略 `src/` 前缀时以 `src/` 为根。**修订编号** `AM-NN` = `amendments.tsv` 的第 NN 个数据行（表头不算，文件行号 = NN+1）；各文档内部的修订编号（如 04 的 #3、05-A2、08 的 A4）在第 4 节的表里一并给出。

## 1. 结论

1. **9 份文档单独看大多自洽，问题集中在模块之间的接缝。其中 4 条会让 1d 写不出来、编不过：**
   - **C-01 crate 成环**：09 让 `zstd138_sys`、`zlib1213_sys`（以及经它依赖的 `libxml2_sys`）依赖 oblib，而调用这些库的单元就在 oblib 里照常翻译。这也与 0.2 的波次相反。
   - **C-02 1d 依赖第 3 步的单元**：`ObObjType`、`ObCollationType`、`ObNumber`、宽整数、crc64、`ob_define` 的常量、时钟、`ObAddr`、`ObRandom` 等进了 1d 的签名，没有文档在 1d 提供；07 与 09、07 与 06 还互相指认对方提供。
   - **C-03 处置表分歧**：文档 §1 与 `units.tsv` 约 150 个单元不一致，处置取值也各写各的，没有规定以谁为准。队列以"文件存在即完成"判定，两种方向的不一致都会让单元被跳过或写两遍。
   - **C-04 时钟三套方案**：02 自建私有时钟，03 和 04 的两条修订又要把 `src/oblib/lib/time/` 交给 03。
2. **同一设施两家认领，或互相推给对方**：trace id（03 与 04）、RetireStation（四种说法）、`ob_define`（六家各取一块）、`ob_backtrace`（三家）、`ob_thread_hook`（02、03 互推）、`ob_external_ref`（01 推给 05，05 没接）、解析器的内存检查（01 与 09 相反，基线支持 09）。
3. **接口不对齐**：`check_stack_overflow` 的形状、15 处名字与签名、prelude 缺 trait（每个 `arr.count()` 都解析不了）、`main` 开头的步骤散在 4 份文档里、`generated/` 的文件清单、cargo 特性名、replace 单元的重载规则。冻结前把 9 份文档 §2 的代码块拼成桩工作区跑一次 `cargo check`，这类问题能一次找全。
4. **流程**：约 30 处偏离没排修订；04、05 没排改名修订，各文档登记新名字的口径也不一；三条修订指派给别的文档的事，对方没接；1d 模块在 oblib 内互相依赖，文档没有给实现顺序。
5. **修订分诊**（第 4 节）：77 行里，事实更正 30 行、规则修改 30 行、改名 7 行、流程 10 行。
   - 核对结果：CONFIRMED 59 行，PARTIAL 6 行（AM-08、AM-10、AM-11、AM-12、AM-22、AM-66），REFUTED 0 行，另有 12 行没有可核对的源码事实。30 行事实更正里 28 行完全成立。
   - 两处前提有误：AM-12 依据的裁判用例读反了（`join_many_table.test:13` 是把工作区调大到 100）；AM-66 要改的错名在调查备忘里，不在 RULEBOOK。
   - 可合并成 10 组（涉及 29 行），其中严格重复 2 对；冲突 11 处。最要紧的冲突：AM-26 与 AM-72（slotmap 去留）、AM-15 与 AM-18（内存探测）、AM-48/AM-76 与 04（重载名以谁为准）、AM-70 与 04（trace id）。
6. 共 31 条发现：**高 4、中 17、低 10**。最该先改的是 C-01、C-02、C-03，以及第 4.2 节的冲突裁决。

## 2. 发现

严重度：**高**＝不改就会让 1d 写不出来、编不过，或第 2、3 步的翻译者拿到互相矛盾的指令；**中**＝两份文档或文档与 RULEBOOK/修订之间有明确矛盾，影响签名、行为或归属，但范围有限；**低**＝措辞、登记口径、少数站点。

### 2.1 crate 图、1d 范围与时钟

| ID | 严重度 | 文档 | 发现 | 证据 | 建议改法 |
|---|---|---|---|---|---|
| C-01 | 高 | 09、01；RULEBOOK 0.2、0.3、0.14 | **oblib 与 -sys crate 互相依赖，Cargo 不允许。** 09 让 `zstd138_sys`、`zlib1213_sys` 依赖 oblib（为了 01 的 `ForeignBlock`），`libxml2_sys` 依赖 `zlib1213_sys`，并声明"oblib 本身不依赖它们"；可调用这些库的 C++ 单元就在 oblib 里、照常翻译：`ob_zstd_wrapper`、`ob_zlib_compressor`、`ob_libxml2_sax_handler`（后者按 RULEBOOK 0.14 还要实现 libxml2_sys 的回调 trait）。这也与 0.2 的波次（-sys/shim 第 1 波、oblib 第 2 波）相反。 | 09:237-240,246,251；09:2146-2160（例 14 就是 oblib 的 `ob_zstd_wrapper.cpp:56-61`）；09:994；01:94；units.tsv：`src/oblib/lib/compress/zstd_1_3_8/ob_zstd_wrapper.h`、`src/oblib/lib/compress/zlib/ob_zlib_compressor.h`、`src/oblib/common/xml/ob_libxml2_sax_handler.h` 都是 `oblib`/`translate`；RULEBOOK:79-80,107,534 | -sys 不依赖 oblib：分配钩子在 -sys 里只暴露 C 的形状（`customMem`、`zalloc`）或一个本 crate 的 trait，`ForeignBlock` 的蹦床放在 oblib 的白名单模块里，由 oblib 注册；0.3 的 crate 表写明 `oblib → zstd138_sys、zlib1213_sys、libxml2_sys、openssl_sys`（`utility.cpp` 的证书到期也在 oblib）；09 §2.0 改依赖列；排修订。 |
| C-02 | 高 | 07、08、04、03、05、01、09、06 | **1d 的签名用到第 3 步才翻译的类型和单元，没有任何文档在 1d 提供；几处还互相指认对方提供。** 主要的：(1) `ObObjType`、`ObCollationType`：07 的 datum/object 模块到处用，09 说"由 07 提供"，07 只使用、并把 `ob_obj_type` 记为 translate；(2) `ObNumber`/`ObCompactNumber`/`ObNumberDesc`、`int256_t`/`int512_t`、`DecimalIntRaw`：07 说由 06 提供，06 没有（`DecimalIntRaw` 在任何文档里都没有定义）；(3) `ob_crc64_sse42`/`ObBatchChecksum`、wyhash、xxhash：07、08 要用，06 把 crc64、wyhash 留在第 3 步，xxhash 只用 crate、不出 oblib 接口；(4) 时钟（C-04）；(5) `ob_define` 的常量（`OB_MAX_CPU_NUM`、`OB_MALLOC_*_BLOCK_SIZE`、`OB_LOG_KEEP_SIZE` 等，见 C-09）；(6) `ObAddr`（03 的 `TraceId::init`）；(7) `ObRandom`（04 的 errsim、05 的测试）；(8) `ObBinaryHeap`（04 的日志压缩器要它，05 把 `ob_heap` 记为 translate）；(9) `get_cpu_count`（05 指望 03，03 没有）；(10) `is_trace_log_enabled`（`ob_lib_config`）、`ObCompressorPool`（04）；(11) `ObErrsimModuleType`（03 用，源码里没有定义，只在 `worker.h` 的 `#ifdef ERRSIM` 下出现）；(12) 08 的 5 个值类型（AM-60 已排，但它们又依赖 murmurhash、`ob_yson`、序列化宏，08:1360）。已排的补救只覆盖一部分（AM-23、AM-35、AM-60），06 Q8 的预翻译没排修订（C-14 的流程问题见 C-26）。 | 07:243,322-335,402-405,489-493,744,1569；09:13,591-594,725,2189；08:291,1350,1360；06:80-84,541,1327；04:55,389,1394,1670；05:42,2315,2317；03:171,664,695；01:133；units.tsv：`ob_obj_type.h`、`lib/charset/ob_charset.h`（`ObCollationType` 在 `:73`）、`common/number/ob_number_v2.h`、`common/wide_integer/*`、`lib/checksum/ob_crc64.h`（`ObBatchChecksum` 在 `:84`）、`hash_func/wyhash.h`、`random/ob_random.h`、`net/ob_addr.h`、`cpu/ob_cpu_topology.h`、`ob_lib_config.h`、`compress/ob_compressor_pool.h`、`ob_define.h` 全是 `translate` | 冻结前做一次"1d 闭包"：从 9 份文档 §2 的签名出发做类型闭包，列出每个要在 1d 写出的第 3 步单元（或单元里的类型、函数），各指定一个负责文档；`units.tsv` 增加一种处置（如 `pretranslate:<模块>`），让队列知道它已完成；用一条修订覆盖（合并 AM-23、AM-35、AM-60 与 06 Q8）。暂时拿不准的签名先用原始整数（照 08 §2.1 末尾的备选）。 |
| C-03 | 高 | 01–09；`units.tsv`；RULEBOOK §4 | **文档 §1 的处置与 `units.tsv` 约 150 个单元不一致，处置取值也不统一，没有规定以谁为准、谁来对齐。** RULEBOOK §4 说 `units.tsv` 是"唯一来源"，README 又让每份文档逐个写处置。队列以"目标文件存在"判完成：文档写 replace、`units.tsv` 写 translate 的单元，1d 写下的桩会让第 3 步把它当作已完成；文档写 translate、`units.tsv` 写 replace 的单元（05 的 `ob_bitmap`/`ob_heap`/`ob_loser_tree`/`ob_tuple`，03 的 `ob_async_task_queue`/`ob_dedup_queue`/`ob_thread_lease`）既没人写桩，也不进翻译队列。文档用了 §4 和 `units.tsv` 都没有的取值：`replace:error`、`replace:parser`、`generate:latch_define`、`generate:ir_enums`、`generate:sysvar`、`generate:bitpacking`、`rewrite:thread`、`replace:…（吸收）`、`delete（吸收）`；`units.tsv` 用了文档不认的 `rewrite:object`（07 明确不用）、`rewrite:share_cache`、`replace:prelude`。 | 第 3.2 节逐单元列出；04:19-25；09:44,197,201；02:61；07:29,45-46；03:1611；05:30-40,62-73；RULEBOOK:975-976；kit `queue_runner.mjs` 的完成判定（见 `design/review/process-consistency.md` P-01） | 冻结前一次对齐：以文档 §1 为输入改 `make_units.py` 的规则（或在 `units.tsv` 加 `doc_disposition` 列由脚本比对），差异逐条交人；§4 的处置取值表补全 `generate:<生成器>` 的名字和 `rewrite:share_cache`/`rewrite:object` 的取舍，删掉"吸收"的写法（改记 delete 或 replace，桩里写替代物）。 |
| C-04 | 高 | 02、03、04、05、01 | **时钟有三套互不相容的方案，1d 的代码都没有可编译的时钟来源。** 02 在 `oblib::sync` 自建 `pub(crate) current_time()`（05 已在用），并在 Q5 建议 03、05 复用它（没排修订）；03 的 AM-23 把 `src/oblib/lib/time/` 的 5 个单元以 `rewrite:thread` 拿进 1d；04 的 AM-35 要把时间门面和 `ObBasicTimeGuard` 交给 03。锁超时（02）、日志时间戳与 `reach_*` 宏（04）、`WorkerCtx::is_timeout` 和定时器（03）、01 经 `reach_time_interval!` 都要时钟；两个时钟并存时，锁的截止时间与日志、超时的时基可能不同。 | 02:965-966,986,1059,1154,1574；03:1611,1632；04:389,1596,1666,1675,1679,1882；05:2314；AM-23、AM-35；units.tsv `time/*` 全是 `translate` | 合并 AM-23、AM-35 为一条：03 提供 `ObTimeUtility::current_time()`、`ObClockGenerator::getClock()`、`OB_TSC_TIMESTAMP`（C++ 名）；02 的 `sync::clock` 改为调用它（或删除），02 Q5 撤回。 |

### 2.2 归属：两家认领，或互相推给对方

| ID | 严重度 | 文档 | 发现 | 证据 | 建议改法 |
|---|---|---|---|---|---|
| C-05 | 中 | 01、09；AM-10 | **解析器的内存检查两份文档说法相反，基线支持 09。** 01 说 `parse_node.c:177-178` 的 weak 缺省实现"由 09 的回调蹦床取代"，即解析器每 1024 个节点调一次 01 的 `try_check_mem_status`；09 说保持 weak 缺省版本（恒返回成功），parser 不提供强定义，并在 Q5 指出 01 的假设与基线不符。核对：`src/sql/parser/ob_memory_tracker_wrapper.cpp` 不在任何 CMakeLists 里，`compile_commands.json` 里 0 次；`src/sql/executor/ob_memory_tracker_wrapper.cpp` 只有两行 include；全部目标文件里只有 `parse_node.c.o` 定义这两个符号，且是 weak。所以基线的解析器从不检查内存。按 01 实现，Rust 版会在超限时让 `new_node` 返回空节点、报错，是新的用户可见行为。AM-10 把"解析器的两个 C 回调"算进 9 个检查点，这一部分前提不成立。 | 01:81,879-881；09:57,391,781,1740,2213-2215；`sql/parser/parse_node.c:177-178`（`OB_WEAK_SYMBOL`）；`nm -m build_release/src/sql/parser/CMakeFiles/ob_sql_server_parser_objects.dir/parse_node.c.o` → `weak external _check_mem_status`、`_try_check_mem_status`；对 `build_release/src` 下全部 `.o` 做 `nm -m`，只有这两条 | 照 09 Q5 的推荐：01:81 改为"基线未使用；`src/sql/parser/ob_memory_tracker_wrapper` 记 delete"；AM-10 删去解析器回调一句（第 4 节）。 |
| C-06 | 中 | 03、04；AM-70 | **`ObCurTraceId` 两种设计并存。** 03 把 `ob_trace_id` 定为 `replace:thread`，提供 `get_trace_id() -> TraceId`（拷贝）、`get_trace_id_str_0() -> ObTraceIdStr`，并说 04 的日志行头直接调用它；04 把它定为 `translate`，日志行头经 `TRACE_ID_STR_HOOK: OnceLock<fn(&mut [u8]) -> usize>` 读取，未注册时写全零 trace id。AM-70 选了 03 的读法，04 没有随之修改。同目录的 `ob_trace_id_adaptor.h` 两家都没提。 | 03:48,741-763,1595；04:54,918-919,1175；AM-70；units.tsv：`profile/ob_trace_id.h`、`profile/ob_trace_id_adaptor.h` 都是 `translate` | 接受 AM-70；04 删去 `TRACE_ID_STR_HOOK`，日志行头直接调 `oblib::thread::ObCurTraceId`；`ob_trace_id_adaptor` 交 03 定处置。 |
| C-07 | 中 | 01、02、08；`units.tsv`；AM-62、AM-63 | **`ob_retire_station.h` 有四种说法。** 01：照 RULEBOOK 0.8，storage_core 和 share::cache 各移植一份；02 O16：在 storage_core（08）；08：`replace:sync`，只移植一份，由 08 写在 `rust/port/oblib/src/sync/ob_retire_station.rs`（AM-63）；`units.tsv`：`rewrite:storage_core`，目标路径却在 `oblib/src/lib_m/allocator/`。连带问题：08 在这个文件里定义对外可见的 `pub struct ObLink { pub next_: *mut ObLink }`，而 05 说 `ObLink` 一律不提供（C-13）；槽数要用 `OB_MAX_CPU_NUM`（AM-62），可 02 只有 `pub(crate)` 的 `SYNC_MAX_CPU_NUM`，`ob_define` 又是 translate。 | 01:41；02:76,799；08:29,446,459-461,467,1364；05:94,1465；`units.tsv` 第 201 行 | 接受 AM-63 的"只一份、在 `oblib::sync`"，同时改 01:41、02:76、`units.tsv`；02 的审查者参与审这个文件；`ObLink` 改为 `pub(crate)` 或 `HazardList` 的内部类型；`OB_MAX_CPU_NUM` 并入 C-02 的闭包表。 |
| C-08 | 中 | 02、03、01、05、06 | **几个单元或设施没人认领。** (1) `src/oblib/lib/thread/ob_thread_hook.cpp`（`futex_hook`、`ob_pthread_cond_timedwait_us`）：03 说归 02，02 说它的强定义在 03、不由 02 移植；`ob_pthread_cond_timedwait_us` 有 3 个调用方，RULEBOOK §2.7 和 02 §6 都没有译法。(2) `src/oblib/lib/allocator/ob_external_ref.h`：01 说"随 05 的封装处理"，05 没有这一行。(3) `src/oblib/lib/task/ob_timer_monitor.h`：02 定为 translate（给出 `Latch<TimerRecord>`），03 定为 `replace:thread`。(4) `ob_trace_id_adaptor.h`：无人提。(5) `ob_write_string` 的带 padding 重载：01 指给 06，06 拒绝（没有调用者）；`charset_malloc`：01 指给 06，06 未提。 | 02:45,74；03:28,40；01:39,735,969；05（无 `ob_external_ref`）；06:17,777 | 第 3.2 节逐个定主：`ob_thread_hook` → 02，记 delete，并在 02 §6 补 `ob_pthread_cond_timedwait_us` → `Monitor` 的译法；`ob_external_ref` → 05，delete；`ob_timer_monitor` → 03；01 的两处指派改写。 |
| C-09 | 中 | 06、03、04、08、05、02、01 | **`ob_define.h` 被多处各取一块，整体预翻译后会重名。** 03 把 `:1875-1971`（gettid、线程名）函数级搬进 `oblib::thread`；06 Q8 打算在 1d 整体预翻译这个单元（2,014 行）；04 说"本模块提供" `OB_LOG_KEEP_SIZE`、`OB_ASYNC_LOG_KEEP_SIZE`；08 要把 `REPLAY_TASK_QUEUE_SIZE` 下移；05 本地另定 `OB_DEFAULT_BITSET_SIZE`；02 另定 `pub(crate) SYNC_MAX_CPU_NUM`。RULEBOOK §4 让 crate 根对所有单元模块做 `pub use <模块>::*`，同名常量在 glob 导出里冲突。 | 03:62；06:63,541,1327；04:1555,1587；08:55,132；05:669；02:799；RULEBOOK:1002 | `ob_define` 由 06 在 1d 整体预翻译（排修订）；03 的函数级搬移写进 `seams.tsv`；其余文档删掉自定义的常量，改引 `ob_define` 的译文。 |
| C-10 | 低 | 04、03、09 | **`ob_backtrace` 三分。** 04 定为 `replace:log`（`lbt`、`parray`、`light_backtrace`，用 `backtrace` crate）；03 把 `ob_backtrace.cpp:37-111` 的帧指针遍历搬进 `oblib::arch`，并替 04 申请 `force-frame-pointers`（Q10）；09 认为它要到第 3 步才翻译，所以让 `ob_backtrace_c`、`parray_c` 经 sql 注册的 `ParserHost` 槽位。 | 04:40,1015-1027；03:64,1615-1617；09:135-136,720,739-741；units.tsv `utility/ob_backtrace.h` `translate` | 04 为唯一归属；`light_backtrace` 的实现（crate 还是帧指针）二选一；09 的两个回调直接调 `oblib::log`，`ParserHost` 删去这两项。 |
| C-11 | 低 | 04、06；`units.tsv` | **`ob_macro_utils.h` 的归属和桩没定。** `units.tsv`：`replace:prelude`（REVIEW）；04：`replace:error`；06：单元归 04，但由 06 提供 `lst_do_code!`、`ob_unis_*!`；04 又说 `LST_DO`/`LST_DO_CODE` 不对应 Rust 宏。 | units.tsv；04:20,425；06:64,621 | 单元归 04（§4 补 `replace:error` 取值）；`lst_do_code!` 留在 06，04:425 改写。 |
| C-12 | 低 | 05、06、09 | **`ob_hash_func.h` 与 murmurhash。** 05 主张 `ob_hash_func.h` 记 `replace:hash`（trait `DoHash`），06 与 `units.tsv` 记 translate；murmurhash 由 06 在 1d 预翻译、05 在 `oblib::hash` 再导出、09 按 `oblib::hash::murmurhash` 引用，这依赖第 3 步的模块树 `crate::lib_m::hash_func` 在 1d 已存在。 | 05:115,931,2337；06:80,82；09:129 | `ob_hash_func` 记 `replace:hash`（05）；murmur_hash 的预翻译并入 C-02 的闭包表。 |
| C-13 | 低 | 05、08 | **`ObLink` 的口径不一。** 05：`ob_link.h` 里的 `ObLink`/`ol_*`/`dl_*` 不提供给翻译代码，`ObLink` 基类一律删除，名单里含 `ob_keybtree_deps.h:183`；08：在 `oblib::sync` 定义 `pub struct ObLink`，并在 keybtree 节点头里保留它（S31）。 | 05:94,116,1465；08:80,461,903 | 同 C-07，`ObLink` 只作 08 的内部类型；05:116 把 keybtree 从"删除基类"名单里拿掉（它是 rewrite 单元）。 |

### 2.3 接口、签名与名字

| ID | 严重度 | 文档 | 发现 | 证据 | 建议改法 |
|---|---|---|---|---|---|
| C-14 | 中 | 03、07、09；RULEBOOK 0.8 | **`check_stack_overflow` 的形状不一致，且没排修订。** RULEBOOK 0.8：`check_stack_overflow(reserved)`，不足返回 `OB_SIZE_OVERFLOW`；03 提供 C++ 的三参数版 `check_stack_overflow(is_overflow: &mut bool, reserved_stack_size: i64, used_size: Option<&mut i64>) -> ObResult`（溢出时返回 Ok、置标志）和无参的 `check_stack_overflow_0()`；07、09 都按 RULEBOOK 的形状调用（09 的两个解析器回调写明 `check_stack_overflow(reserved = 32 KB)`）。 | RULEBOOK:355；03:850-852；07:1357,1566；09:133-134,1745,2185 | 03 补 RULEBOOK 形状的 `check_stack_overflow_reserved(reserved: i64) -> ObResult` 一类入口并写进改名表，或修订 0.8 改用 C++ 形状、同时改 07、09。 |
| C-15 | 中 | 01–09 | **模块之间的名字和签名对不上**（单条都小，合起来让文档里的例子编不过）：(1) 04 用 `crate::sync::LatchGuard`，02 只有 `ObLatchMutexGuard`，且 02 Q10 明确不用 `LatchGuard`；(2) 04 用 `oblib::thread::Timer`，03 按 AM-18 叫 `ObTimer`；(3) 02 例 2 用 `this_worker_timeout_ts()`，03 是 `this_worker!().get_timeout_ts()`；(4) 03 说 `update_loop_ts` 转调 02 的 `ObLatch::clear_lock()`，02 删除了 `clear_lock`/`current_locks`，`ObLatch` 也只是 `pub(crate)`；(5) 01 的 `ArenaVec::pop_back -> Option<T>`，05 的 `ObIArray::pop_back(&mut self)` 无返回值，05 却说两者"语义一致"；(6) 01 例 12 的 vsag 分配器方法叫 `allocate`/`deallocate`，09 是 `Allocate`/`Deallocate`/`Reallocate`；01 例 15 的 `ZstdAllocCtx`、`ob_zstd_malloc(ctx: Option<&ZstdAllocCtx>, …)` 在 09 里不存在（09 是 `OB_ZSTD_customMem { has_opaque }`）；(7) 09 例 2 写 `ObBuf::new()`、`nbuf.as_bytes()`，04 定义 `pub type ObBuf = [u8]`；(8) 02 把 `ObLockedPool` 译成 `Latch<ObPool<…>>`，01 不提供 `ObPool`；(9) 01 §7 说 07 的批次 arena 用 `Arena`，07 用自己的 `ChunkArena`；01 例 7、11 写 `exec_ctx.get_mem_ctx()`，07 的 `ObExecContext` 里没有 `MemCtx`；(10) 09 说 jemalloc 版本断言是"01 的启动检查"，01 没有；(11) 05 说 `ParseNode` 的 `HashFunc` 由 09 实现，09 说默认不提供；(12) 06 的 `ObStringExt::hash(seed)` 委托"05 的哈希接口"，05 的 `HashFunc` 没有带种子的版本；(13) 03 用 `mprint!`，没有文档定义；(14) 02 的 `Latch::new` 要求 `ObLatchIds`，04 新建的 latch 都没给；(15) 06 的 AM-45 让 C++ 判断 NULL 的位置用 `Option<ObStr<'q>>`，01 的 `Arena::try_str` 对空源和 NULL 源都返回 `Ok(&[])`，C++ 的 `ob_write_string` 此时写的是 NULL（`ob_string.h:811-815`），两份文档拼起来表达不了 NULL。 | (1) 04:1235，02:204,1579；(2) 04:1144,1670，03:492；(3) 02:1351，03:1403；(4) 03:254,1037，02:27；(5) 01:244-263，05:246,2313；(6) 01 例 12、例 15，09:871,1299-1301；(7) 09:1965-1968，04:529；(8) 02:28，01:69；(9) 01:1352，07:536,573，07 全文无 `MemCtx`；(10) 09:1667；(11) 05:882,2318，09:2187；(12) 06:122，05:873-883；(13) 03:988,1534；(14) 02:198，04:404,1232；(15) 01:204-207，AM-45，旁证 fidelity-01 F-06 | 各文档按提供方的签名改用法。冻结前把 9 份文档 §2 的代码块拼成一个桩工作区跑一次 `cargo check`（1d 允许编译，DEV-014），这类问题可以一次找全。 |
| C-16 | 中 | 06、05、02 | **prelude 不全。** RULEBOOK §4 要求每个单元第一条 `use` 是 `oblib::prelude::*`。06 §2.8 的清单只有 `crate::container::ObIArray`；05 要求的 `ObIArrayWrap`（`count`/`at` 在它上面）、`HashFunc`、`DoHash`、`LinkHashKey`、`SpHashNode`、`ObDLinkBaseVirt`、`HashMapPair`、`HashNullObj` 都不在；02 要求的 `oblib::atomic`（`inc_update`、`cas128` 等）也不在。§2.5 把 `const ObIArray<T> &` 参数译成 `&[T]`，于是翻译代码里的每个 `arr.count()`、`arr.at(i)` 都解析不了。 | 06:722-760；05:164,223-242；02:92；RULEBOOK:693,1006 | 06 冻结前向各文档收清单，prelude 取并集。 |
| C-17 | 中 | 03、01、04、09、06；RULEBOOK 0.5 | **`main` 开头的序列没人管全。** RULEBOOK 0.5：第 1 步屏蔽信号，"早于任何其他代码"。01 要在"第 1 步，屏蔽信号之前"调 `init_malloc_hook()`，还有 macOS zone 配置、fork 后恢复；04（AM-42）要在 main 开头做 4 次 `setlocale`，并提供 `log::sys::setlocale_like_main`；09 要在第 3 步做版本断言、布局自检、绑定 `PARSER_HOST_SLOT`。03 生成的 `main` 只有 09 的那部分，没有 01 的三件和 04 的 setlocale；它调用的 `observer::generated::register_hooks()` 不在 09 的生成文件清单里；06 说 setlocale 归 03，03 Q13 说没有提供者。日志写线程的启动位置还取决于 AM-34。 | RULEBOOK:220-227；01:536-546；04:1462,1775,1867,1881,1889；09:1611-1626,1811；03:984-1000,1147,1619；06:833 | 0.5 的 `main` 序列合成一条修订（合并 AM-34、AM-42，补 01 的三步并定顺序），由 03 的生成脚本负责；09 的生成文件清单补 `register_hooks`。 |
| C-18 | 中 | 07、09、02、04 | **`generated/` 的文件清单与生成器归属对不上。** 07：ExtraInfo 的 enum 生成在 **query**（`query/src/generated/extra_info.rs`），sql 下有 `expr_ops`、`op_state`、`expr_op_impls`、`expr_op_registry`、`expr_op_ctx`、`ir_enums`、`qa_slots`；09：把 `extra_info` 放 **sql**，另列 `log_op`、`path`、`op_spec`，没有 `expr_ops`、`expr_op_impls`、`ir_enums`、`qa_slots`。02 新增 `latch_define` 生成器（`generate:latch_define`），09 的生成器表和 RULEBOOK 0.13 都没有。`errsim_registry.rs` 的扫描脚本：04 说归 09，09 说归 04。 | 07:26,1236-1250,1313；09:1609-1626；02:61,112-114,1562；04:1870；09:1618 | 以 07 为准改 09 §2.6.4；`latch_define` 补进 09 的生成器表和 0.13；errsim 扫描脚本归 04。 |
| C-19 | 中 | 01、03、06、07、04；AM-32、AM-14 | **cargo 特性的名字和 errsim 的开关各写各的。** RULEBOOK 0.15 说"同名的 cargo feature"（`ERRSIM`、`ENABLE_DEBUG_LOG`、`ENABLE_SANITY`），§2.1 又写 `cfg(feature = "errsim")`；AM-32 统一为大写 `ERRSIM`，并让 `ob_e!`/`errsim_point!` 始终编译。04 写大写；01（`ERRSIM_SMART_VAR`）、06（`EN_CHECK_SORT_CMP`）写小写 `errsim` 并把注入点门控掉，而 C++ 这两处都不在 `#ifdef ERRSIM` 里，release 也生效；03 写小写 `errsim`；07 写 `enable_debug_log`；01 写 `enable_sanity`，AM-14 写 `ENABLE_SANITY`。 | RULEBOOK:556,617；04:332,939,1392,1581；01:978,1406；06:895；03:664-665,677；07:1220；`oblib/lib/utility/ob_smart_var.cpp:28`、`oblib/lib/utility/ob_sort.h:30` | 接受 AM-32；特性名一律照 C++ 宏原样（大写）；01:978、06:895 改为始终编译；03、07、01 改名。 |
| C-20 | 中 | 04；AM-48、AM-76 | **replace 单元的重载名以谁为准，修订与文档相反。** AM-48、AM-76 都要求 `overloads.tsv` 照抄 API 文档的名字；04:1285 却说"与 1c 生成的 `overloads.tsv` 不一致时以 `overloads.tsv` 为准、本文随之改"。 | 04:1285；AM-48；AM-76 | 合并 AM-48、AM-76 为一条（API 文档为准），04:1285 改写。 |
| C-21 | 低 | 07；AM-71 | 07 让 `OpState` 状态 struct 里的 `ObSEArray` 成员按 RULEBOOK §2.5"IR 节点"一行译成 `SmallVec`/`Vec`；AM-71 要求翻译代码一律写 oblib 的 `ObSEArray` 等新类型（理由正是 `Vec` 固有方法的遮蔽问题，同样适用于这里）。 | 07:1211；AM-71 | AM-71 通过时 07:1211 改用 `ObSEArray<T, N>`。 |
| C-22 | 低 | 02、03 | `pub fn pause()` 在 `oblib::sync`（02）和 `oblib::arch`（03）各有一个，crate 根 glob 导出时重名；RULEBOOK §2.7 点名的是 `oblib::sync::pause()`。 | 02:796；03:962；RULEBOOK:749,1002 | `arch::pause` 改 `pub(crate)`。 |
| C-23 | 低 | 04、05 | `ObSEArray`/`ObArray` 的 `to_string` 在缓冲将满时：04 说写 `...` 加 `, ` 后停止；05 说之后仍打印最后一个元素。源码与 05 一致。04 负责切片钩子的默认格式，照 04 实现会改字节。 | 04:1555；05:1789；`oblib/lib/container/ob_array.h:754-764` | 04:1555 照 05 改（AM-75 也让 05 的表为准）。 |

### 2.4 依赖方向与 1d 的执行顺序

| ID | 严重度 | 文档 | 发现 | 证据 | 建议改法 |
|---|---|---|---|---|---|
| C-24 | 中 | 01–06；RULEBOOK D16 | **oblib 内的 1d 模块互相依赖成环，文档没有给 1d 的实现和测试顺序。** 02↔03（itid、`icpu_id`、`arch` ↔ `Latch`、`Monitor`、`ObCond`）、02↔04（错误码、日志宏 ↔ `Latch`、`FutexCond`、`CacheAligned`）、03↔04（tid、线程名、定时器、trace id ↔ `ObToString`、日志）、01↔04（`ObCStringHelper` 里的 `Arena` ↔ 错误码、日志）、01↔02（`Latch<Arena>`）。同一个 crate 能编译，但 D16 的做法是每个模块一名实现者、两名审查者；02 的 loom 测试还要求 04 的日志宏在 `cfg(loom)` 下为空，04 没被告知。 | 02:969,1559-1561；03:1593-1597；04:1847-1870；01:1345-1355；RULEBOOK:68 | 1d 计划里先由一名实现者写"骨架层"（04 的 `ObError`、日志宏的空实现，03 的 `get_itid`，02 的 `Latch`，01 的 `Arena` 签名），其余模块在骨架上并行；写进 README 的阶段规则。 |
| C-25 | 中 | 08、02、01；README；AM-60、AM-63 | **08 要在 oblib 里写代码，模块边界和审查归属没定。** AM-60 把 5 个值类型下移到 oblib、在 1d 由 08 写；AM-63 把 `ob_retire_station.rs` 放在 02 的目录里、由 08 写。README 的模块表只给 08 `storage_core` 与 `share::cache`；02 的文档不知道自己的目录里有 08 的文件；§3 白名单第 2 项、loom 测试与审查者归属都没说。 | README 模块表；08:29,112-135,1346；02:76；01:41 | AM-60、AM-63 通过时同时改 README 的模块表，并指定 02 的审查者参与 `ob_retire_station.rs`。 |

### 2.5 与 RULEBOOK 的偏离、修订流程

| ID | 严重度 | 文档 | 发现 | 证据 | 建议改法 |
|---|---|---|---|---|---|
| C-26 | 中 | 各文档 | **没排修订的偏离**（README 要求"与 RULEBOOK 冲突…在 amendments.tsv 追加一行"）：<br>• 02：0.8 的"17 项纯自旋"实际只有 13 项真自旋；`Quiescence` 不提供 0.8 写的 `leave`；`Monitor` 改用单调时钟（Q2 说"建议进 A.4"）；`ob_pthread_cond_timedwait_us` 没有译法；`ObLatchIds` 只给关联常量（§2.12 要求同时有模块级常量）；`oblib::atomic` 公开导出 `portable_atomic::AtomicU128`（§1 把 portable-atomic 限定在几个模块内部）。<br>• 03：`check_status` 改成线程局部的 `WorkerVirt`，不是 0.5 说的"启动时注册的钩子"；`ObDITls::get_instance` 在用户闭包期间持有 `RefCell` 借用（0.5 禁止借用跨函数调用）；例 15 调换了两个空检查的顺序（§2.1 逐行保留）。<br>• 05：加锁模式的 `get`/`iter` 取 `&mut self`（§2.13：const → `&self`）；数组 `Clone` 在 OOM 时 abort（0.7：大小取决于用户数据的分配必须可失败）；§3.16 的多处行为差异没有 `FIXED`/`DIVERGE` 标记；不提供 Pin 形式的侵入式链表（§2.5 写了两种契约）。<br>• 06：`ObUnisBody` 加了 `<'de>`；`Slot` 二次 `bind` 返回 `OB_INIT_TWICE`（C++ 覆盖）；死代码类别扩到旧的 `ObObj` 编码族；在 1d 预翻译 4 个 translate 单元（Q8）。<br>• 01：`MemBudget` 一律 `SeqCst`，C++ `MemoryUsageTracker` 是显式 relaxed（0.8"29 处显式弱序照原样写"）；`init_malloc_hook` 的位置（C-17）。<br>• 04：`user_msg::OB_X` 多一个 `loc` 参数、`%s` 收 `impl ObPrintfStr`；`kpc!` 收 `Option<&T>`（§2.2 写 `kpc!(*g)`）；新增死代码候选。<br>• 07：在 translate 单元里做类级替换（`ObRawExprFactory`、`ObStmtFactory`、`ObLogOperatorFactory`），§4 没有这种处置形态；`generate:ir_enums`、向量格式的 `rewrite:frame`。<br>• 08：`storage_core` 的 `lib.rs` 手写（§4：lib.rs 由脚本生成）。<br>• 09：`parse_node.h` 的目标改到 parser crate，是 §4 路径例外表之外的新例外；Q1 的生成器"共存"做法与 0.13 字面不同，只作为开放问题。 | 02:119,400-425,846,983,1155,1571；03:780,1103,1117,1568-1583；05:310,392,766,1031-1032,1073,1825-1842；06:60,561,603,964,1327；01:453,540,886；04:189-201,989；07:51-53；08:97；09:44,74,1636 | 逐条补排修订；或在冻结关口把这些列成一张"已知偏差"清单，交人一次确认。 |
| C-27 | 中 | 04、05、01、02、03、06、07、08、09；AM-06、AM-17、AM-18、AM-29、AM-48、AM-58、AM-64 | **改名表的登记口径不一。** 01、02、03、06、07、08、09 各排了改名修订，04、05 没排（04 §2.7、05 §8 Q1 只请审查者确认）。已排的修订里，01、02、06 连新方法名也登记，07、08、09 只登记类型名；crate 名（`zlib1213_sys`、`build_support`）、cargo 特性名、钩子静态量名（`ALLOW_NEXT_SYSLOG_HOOK`、`TRACE_ID_STR_HOOK`）没有规则。RULEBOOK 正文自己也用了改名表外的名字（`EvalFn`、`ObStringExt`、`ObStringWriter`、`ObResultExt`、`with_locked`、`lock_until`）。 | 04:1283-1302；05:2324；AM-06、AM-17、AM-18、AM-29、AM-48、AM-58、AM-64；RULEBOOK:267,610,704-705,746,1021-1046 | 合并成一条改名表修订：表只登记类型、trait、宏、crate、模块级 static；新方法名以 API 文档为准、不入表；04、05 补清单；RULEBOOK 正文里的名字补进表。 |
| C-28 | 中 | 01、05、07、06；AM-11、AM-12、AM-23 | **修订把事情指派给别的文档，对方没有接。** AM-11 要 07 决定每处 `CURRENT_CONTEXT` 的 `MemCtx` 来源，07 全文没有 `MemCtx`；AM-12 请 05 给 `ObArray`/`ObSEArray` 加可选的 tracker，05 没有任何 tracker；AM-23 把 `ObAddr` 交给 06，06 只在例 11 用到它。 | AM-11、AM-12、AM-23；07、05 全文检索 0 处；06:1182-1213 | 冻结前要求被指派方在 §8 回应；不接就改修订的写法。 |
| C-29 | 低 | 03、05、07；AM-15、AM-18、AM-26、AM-72 | **修订与别的文档或修订冲突**：AM-15 要删去 0.8 的内存探测，03 照 0.8 实现了 `oblib::signal::probe_read_memory`，AM-18 还把它列进改名表；AM-26 说白名单里的 `slotmap` 可以删掉，05 的 `ObList`（AM-72）正要用 `slotmap`。 | AM-15、AM-18、AM-26、AM-72；03:916-953；05:615-660,2304 | 见第 4.2 节的冲突处理。 |
| C-30 | 低 | 05、03；RULEBOOK §4 | **"吸收"和空桩没有定义。** 05 的"replace:…（吸收）""delete（吸收）"不是 §4 的取值，桩内容也没写；`queue/ob_link.h` 记 `replace:queue` 却不导出任何名字；03 的 `ob_link_task`、`co_var` 桩也是空的。§4 要求桩是"只含 `pub use <替代物>`"。 | 05:15,30-40,62-73,94；03:25,45；RULEBOOK:984 | §4 写明允许空桩（只有 `Disposition` 头和状态行），或把这些单元改记 delete。 |
| C-31 | 低 | 08、02、09、01；README | **同一件事在两份文档里规定。** README 规定"只写自己那一份 API 文档"；08 为 02 的目录规定了一个文件的 API（`ob_retire_station.rs`），02 文档里没有；`seekdb-jemalloc-sys` 的 Cargo 特性，01 和 09 各写一遍。后改的一方容易漏。 | 08:443-480；02:76；09:1165-1181；01:118-125 | 跨模块落点的 API 只写在负责实现的文档里，另一方写一行指针。 |

## 3. 交叉引用与单元归属

### 3.1 争议设施交叉引用（含 RULEBOOK §2 点名设施的归属核对）

只列有争议的和任务里点名要查的；其余 RULEBOOK §2 点名的设施（`ObError`/`ObResult` 与错误链宏、日志宏、`ob_printf!` 一族、`Arena`/`ArenaVec`/`MemBudget`/`try_box`、锁原语、并发容器与队列、`ObStringExt`/`ObStringWriter`、`ob_sort`、序列化、`ob_bitfield!`、`c_*` 数值函数、`QueryArena`/id、`RtExpr`/`StoreTable`、`NodeRef`/`RowGuard`、`KvBlockHandle`、`ParseNode` 一族、`ObItemType`）各有且只有一家提供，签名与使用方一致，不再列出。

| 设施 | RULEBOOK | 提供方 | 使用方的假设 | 结论 |
|---|---|---|---|---|
| `get_itid()` | 0.8、§2.7 | 03：`oblib::thread::get_itid() -> i64`，0..65535，最小空闲号（03:150,1008-1014） | 02：`pub(crate)` 包装，写者 id = itid+1（02:969,979）；08：QClock/RetireStation/hazard 槽取模（08:79,897-898,935） | 一致 |
| `GETTID` | 0.8 写"锁字里记 itid" | 03：`ob_gettid() -> i64`（03:176-184） | 02 记 itid+1，不用 tid（AM-01）；04 日志行头（03:1595） | 一致（以 AM-01 为条件） |
| `ObLink`/`dl_*` | — | 05 不提供（05:94）；08 自定义 `pub struct ObLink`（08:461） | 08 KV store 内部的 `dl_*`（08:80） | C-13 |
| QClock/RetireStation | 0.8 | 08（`oblib::sync`，AM-63） | 01、02 写的是旧落点；units.tsv 又一种 | C-07 |
| `hash_func`/murmurhash | 0.11.3、§2.5 | 05：`HashFunc`、`DoHash`、再导出 `murmurhash`（05:873-945）；06：预翻译 `murmur_hash`（06:80） | 07：murmur/wy/xx 由 06 给（07:1569）；09：`oblib::hash::murmurhash`（09:129）；03：06 | murmur 一致；wyhash、xxhash 无人提供（C-02）；`ob_hash_func` 归属两说（C-12） |
| 时间工具 | §1"时间源" | 无（02 私有 `current_time`） | 02、03、04、05、01 | C-04 |
| trace id | 0.5 | 03 与 04 两套 | 04 的日志行头 | C-06 |
| `ObCond` | §2.7（AM-08） | 02（02:577-592） | 03 同意归 02（03:29,1617） | 一致；`units.tsv` 记 `replace:thread`（第 3.2 节 U-15） |
| `atomic128` | 0.9、§2.7 | 02：`oblib::atomic::{AtomicU128, uint128_t, cas128, load128}`（02:846-861） | 08 的 LSN 分配器（08:681,925）；04 的 REACH/EXECUTE_COUNT_PER_SEC 改用 `Latch`（AM-41） | 一致 |
| 日志写线程的启动 | 0.5 第 4 步、§1 | 04：`ObLogger::init(&'static self, …)` 启动写线程（04:824-905） | 03 生成的 `main` 第 4 步留空，等 AM-34（03:995-996） | 一致（以 AM-34 为条件）；序列问题见 C-17 |
| `MemCtx` 与 `CHECK_MEM_STATUS` | §2.4、0.7 | 01：`MemCtx::check_status`、自由函数 `check_mem_status()`/`try_check_mem_status()`、线程局部 tracker（01:389,440-442；AM-10） | 07：`QueryArena` 每 1024 次调 `check_mem_status()`（07:1099）；03：`ObThWorker::check_status` 经 01 的线程局部 tracker（03:1593）；09：不接（09:781）；01 例子：`exec_ctx.get_mem_ctx()` | C-05、C-15(9)、C-28 |
| `check_stack_overflow` | 0.8 | 03：三参数 + `_0`（03:850-852） | 07、09 按 RULEBOOK 形状 | C-14 |
| `Slot` | 0.5、§4 | 06：`Slot<T: ?Sized + 'static>`、`const fn new()`、`bind`/`get`/`get_or`/`is_bound`（06:702-713） | 08：`Slot<dyn ObMemstoreAllocHook>`（08:228）；09：`PARSER_HOST_SLOT`、`generated/slots.rs`（09:744,1614） | 一致 |
| `ObToString`/`ObBuf` | §2.11 | 04：`fn to_string(&self, buf: &mut ObBuf) -> i64`，`ObBuf = [u8]`（04:529,609） | 02、03、05、07、09 同签名；09 例 2 把 `ObBuf` 当拥有型缓冲 | 基本一致；C-15(7) |
| `Latch` | §2.7 | 02：`Latch::new(ObLatchIds, T)`、`lock() -> ObLatchMutexGuard`（02:198,204） | 01 `Latch<Arena>`；04 `LatchGuard`；08、05 | C-15(1)(14) |
| `ObIArray` | §2.5 | 05：`ObIArrayWrap` + `ObIArray`（05:223-263） | 07、08 用 `&mut dyn ObIArray<…>`；prelude 只有 `ObIArray` | C-16 |
| prelude | §4 | 06（06:722-760） | 02、05 要求更多 | C-16 |
| `ob_define` 常量 | — | 06 Q8（未排修订） | 01、02、03、04、05、08 | C-09 |
| 序列化 trait | §2.11 | 06：`ObSerialize`、`ObDeserialize<'de>`、`ObDeserializeIn<'q>`、`ObUnisBody<'de>`（06:547-561） | 03、05、07、08 | 一致（`ObUnisBody<'de>` 未排修订，C-26） |
| `ObObjType`/`ObCollationType` | §2.12 | 无（09 说 07 提供） | 07、09 | C-02 |
| `WorkerCtx::check_status` | 0.5 | 03：线程局部 `WorkerVirt`（03:603-665） | 02 例子用别的名字 | C-15(3)、C-26 |
| `main` 开头 | 0.5 | 03 生成 `bin/seekdb.rs`（03:984-1000） | 01、04、09 各加步骤 | C-17 |
| `ForeignBlock` | 0.7、0.14 | 01（01:590-607） | 09 的 -sys（成环） | C-01 |
| `KvBlockHandle` | 0.8、§4 | 08：`Send`、只给 `&[u8]`、Drop 解保护（08:755,940） | 07：`StoreTable::register_pinned` 要求 `Send`（07:543,1425） | 一致 |

### 3.2 单元归属与处置分歧表

比对口径：各文档 §1（以及正文里给出处置的地方）对单元的处置，与 `migration/design/units.tsv` 第 5 列逐行比较；同一组处置相同的单元合并成一行。"类别"取：**处置不同**（文档与 `units.tsv` 不同）、**两家冲突**（两份文档说法不同）、**无人认领**、**提议中**（只在修订或开放问题里）、**取值不规范**（处置值不在 §4 或 `units.tsv` 的取值里）、**缺行**（`units.tsv` 没有这一行）。共 64 行、约 160 个单元，其中处置不同约 150 个。

| # | 单元（`src/` 下） | `units.tsv` | 文档主张（文档:行） | 其他文档 | 类别 | 建议 |
|---|---|---|---|---|---|---|
| U-01 | `oblib/lib/allocator/{ob_allocator_v2, ob_page_manager, ob_simple_fifo_alloc, ob_fixed_size_block_allocator, ob_heavy_ref}.h` | replace:mem | 01：delete（01:25,26,34,35,38） | 05:94 仍说 `ob_simple_fifo_alloc` 用 `dl_*` | 处置不同 | 照 01；05:94 删去这一句 |
| U-02 | `oblib/lib/allocator/{ob_memory_sanity.h, ob_jemalloc_sanity.cpp, ob_sanity_libc_wrap.cpp}` | replace:mem | 01：delete（01:43，AM-14） | — | 处置不同 | 照 01 |
| U-03 | `oblib/lib/alloc/{alloc_interface, alloc_failed_reason, abit_set, block_set, object_set, object_mgr, ob_ctx_define, ob_context_allocator, ob_ctx_allocator, ob_malloc_callback}.h`（10 个） | replace:mem | 01：delete（01:52,53,55） | — | 处置不同 | 照 01 |
| U-04 | `oblib/lib/objectpool/{ob_server_object_pool, ob_cached_allocator, ob_resource_pool, ob_global_factory, ob_tc_factory}.h`（5 个） | replace:mem | 01：delete（01:70,71） | 02:28 把 `ObLockedPool`（`ob_pool.h:102`）译成 `Latch<ObPool<…>>` | 处置不同 | 照 01；02:28 改写 |
| U-05 | `oblib/lib/resource/{ob_resource_mgr, ob_cache_washer}.h` | translate | 01：delete（01:66,67） | 08:81 依赖 01 删掉 `ObMemoryMgr` | 处置不同 | 照 01 |
| U-06 | `oblib/lib/resource/achunk_mgr.h` | replace:mem（REVIEW） | 01：delete（01:66） | — | 处置不同 | 照 01 |
| U-07 | `oblib/lib/allocator/ob_hazard_ref.h` | replace:hash | 01：delete（01:37）；02：不移植（02:76） | 05 未提 | 处置不同 | delete |
| U-08 | `oblib/lib/allocator/ob_external_ref.h` | replace:mem | 01："随 05 的封装处理"（01:39） | 05 无此行 | 无人认领 | 交 05，记 delete（机制吸收进 `ObLinearHashMap`、`ObExtendibleRingBuffer`） |
| U-09 | `oblib/lib/allocator/ob_retire_station.h` | rewrite:storage_core，目标在 `oblib/src/lib_m/allocator/` | 08：replace:sync，`oblib/src/sync/ob_retire_station.rs`（08:29，AM-63） | 01:41（两份）；02:76（storage_core） | 两家冲突 | 见 C-07 |
| U-10 | `oblib/lib/utility/{ob_smart_var, ob_mod_define}.h` | translate | 01：replace:mem（01:72,73）；03:51 同 | — | 处置不同 | 照 01 |
| U-11 | `sql/executor/{ob_memory_tracker, ob_memory_tracker_wrapper}.h` | translate | 01：replace:mem（01:80,81） | — | 处置不同 | 照 01；`ob_memory_tracker_wrapper.cpp` 没有定义，桩为空 |
| U-12 | `sql/parser/ob_memory_tracker_wrapper.h` | translate（REVIEW） | 01：replace:mem（01:81） | 09:57 交 01 负责，但不提供强定义 | 处置不同、语义冲突 | 记 delete（`.cpp` 不参与构建），见 C-05 |
| U-13 | `oblib/lib/utility/alloc_assist.h` | translate | 06：replace:string（06:20） | 01:74 交 06；03:241 说 `ACHUNK_PRESERVE_SIZE` 由 01 导出 | 处置不同 | 照 06；03:241 改 |
| U-14 | `oblib/lib/stat/ob_latch_define.h` | translate（REVIEW） | 02：generate:latch_define（02:61） | 09、RULEBOOK 0.13 无此生成器 | 处置不同、取值不规范 | 照 02，09 补生成器（C-18） |
| U-15 | `oblib/lib/thread/ob_queue_thread.h` | replace:thread | 02：replace:sync（02:64）；03：replace:sync（03:29） | — | 处置不同（两文档一致） | replace:sync |
| U-16 | `oblib/lib/thread/ob_thread_hook.cpp` | replace:thread | 03：replace:sync，归 02（03:28） | 02:45：不由 02 移植 | 无人认领 | 见 C-08 |
| U-17 | `oblib/lib/task/ob_timer_monitor.h` | replace:thread | 02：translate（02:74） | 03:40：replace:thread | 两家冲突 | replace:thread（03）；02:74 改 |
| U-18 | `share/ob_thread_pool.h` | translate | 03：replace:thread（03:19） | — | 处置不同 | 照 03 |
| U-19 | `oblib/lib/thread/{ob_balance_filter, ob_priority_scheduler, ob_map_queue, ob_test_util}.h`、`thread_local/thread_buffer.h` | replace:thread | 03：delete（03:30-34,44） | — | 处置不同 | 照 03（第 16 类确认） |
| U-20 | `oblib/lib/thread/{ob_async_task_queue, ob_dedup_queue, ob_thread_lease}.h` | replace:thread | 03：translate（03:35-37） | — | 处置不同 | 照 03 |
| U-21 | `oblib/lib/stat/ob_di_tls.h`、`coro/co_var.h`（REVIEW）、`lib/worker.h` | translate | 03：replace:thread（03:43,45,47） | — | 处置不同 | 照 03 |
| U-22 | `oblib/lib/profile/ob_trace_id.h` | translate | 03：replace:thread（03:48） | 04:54：translate + 钩子 | 两家冲突 | 见 C-06 |
| U-23 | `oblib/lib/utility/ob_common_utility.h` | translate | 03：replace:thread（03:50） | 04:56 "由其所属模块决定"，Q5 指 03 | 处置不同 | 照 03 |
| U-24 | `oblib/lib/signal/{ob_signal_utils, ob_libunwind}.h` | replace:signal | 03：delete（03:54,55） | — | 处置不同 | 照 03 |
| U-25 | `oblib/lib/time/{ob_time_utility, ob_clock_generator, Time, ob_cur_time, ob_tsc_timestamp}.h` | translate | 03 提议 rewrite:thread（AM-23）；04 提议归 03（AM-35） | 02 自建时钟（02 Q5） | 提议中 | 见 C-04 |
| U-26 | `oblib/lib/net/ob_addr.h` | translate | 03 提议归 06（AM-23） | 06 未接 | 无人认领 | 见 C-28 |
| U-27 | `oblib/lib/ob_define.h` | translate | 06 Q8：1d 整体预翻译（06:1327）；03：函数级搬走 `:1875-1971`（03:62） | 02、04、05、08 各自定义常量 | 多家认领 | 见 C-09 |
| U-28 | `share/mysql_errno.h` | translate | 04：generate:errno（04:18） | 09:195 同 | 处置不同 | 照 04 |
| U-29 | `oblib/lib/oblog/ob_log_user_msg.h` | replace:log | 04：replace:error（04:19） | — | 取值不规范 | §4 补 `replace:error` 或改回 `replace:log` |
| U-30 | `oblib/lib/utility/ob_macro_utils.h` | replace:prelude（REVIEW） | 04：replace:error（04:20） | 06:64：单元归 04 | 取值不规范 | 见 C-11 |
| U-31 | `oblib/lib/{ob_check_macros, ob_abort}.h`、`utility/{ob_defer, ob_tracepoint, ob_tracepoint_def}.h` | translate | 04：replace:error（04:21-25） | — | 处置不同 | 照 04 |
| U-32 | `oblib/lib/utility/ob_rate_limiter.h` | translate | 04：replace:log（04:35） | — | 处置不同 | 照 04 |
| U-33 | `oblib/lib/utility/ob_backtrace.h` | translate | 04：replace:log（04:40） | 03:64 帧指针部分归 `arch`；09:135 当作第 3 步单元 | 两家冲突 | 见 C-10 |
| U-34 | `oblib/lib/oblog/{ob_base_log_buffer, ob_easy_log}.h` | replace:log | 04：delete（04:43,44） | — | 处置不同 | 照 04（第 16 类确认） |
| U-35 | `oblib/lib/container/{ob_bitmap, ob_heap, ob_loser_tree, ob_tuple}.h` | replace:container | 05：translate（05:41-44） | 04:1670 要 05 在 1d 提供 `ObBinaryHeap`；06:54 "05 或 translate" | 处置不同 | 照 05；`ob_heap` 进 C-02 的闭包表 |
| U-36 | `oblib/lib/container/{ob_concurrent_bitset, ob_id_map, ob_int_flags, ob_rbtree, ob_mask_set2, ob_paringheap, ob_ring_buffer}.h`（7 个） | replace:container | 05：delete（05:45-50,52） | — | 处置不同 | 照 05（第 16 类确认） |
| U-37 | `oblib/lib/container/{ob_array_iterator, ob_se_array_iterator, ob_fixed_array_iterator, ob_ext_ring_buffer_impl}.h` | replace:container | 05："replace:container（吸收）"（05:30-32,40） | — | 取值不规范 | 见 C-30 |
| U-38 | `oblib/lib/hash/{ob_serialization, mprotect, ob_darray, ob_hash}.h` | replace:hash | 05："delete（吸收）"（05:62,63,67,70） | — | 处置不同 | delete |
| U-39 | `oblib/lib/hash/{ob_iteratable_hashmap, ob_cuckoo_hashmap, ob_ptr_array_map, ob_multi_mod_ref_mgr, ob_refered_map}.h` | replace:hash | 05：delete（05:75,80-83） | — | 处置不同 | 照 05 |
| U-40 | `oblib/lib/hash/{ob_link_hashmap_deps, ob_dchash, ob_placement_hashutils}.h` | replace:hash | 05："replace:hash（吸收）"（05:65,66,73） | — | 取值不规范 | 见 C-30 |
| U-41 | `oblib/lib/list/{ob_intrusive_list, ob_atomic_list, ob_link}.h` | replace:container | 05：delete（05:106-108） | — | 处置不同 | 照 05 |
| U-42 | `oblib/lib/hash_func/ob_hash_func.h` | translate | 05 提议 replace:hash（05:115） | 06:82：translate | 两家冲突 | 见 C-12 |
| U-43 | `oblib/lib/hash_func/murmur_hash.h` | translate | 06：1d 预翻译（06:80） | 05:931 再导出；09:129 | 预翻译未登记 | 见 C-02 |
| U-44 | `oblib/lib/utility/ob_fast_convert.h`、`string/ob_sensitive_string.h` | translate | 06：1d 预翻译（06:21,31） | 04:57-58 依赖 | 预翻译未登记 | 见 C-02 |
| U-45 | `oblib/common/object/ob_object.h` | rewrite:object | 07：rewrite:datum（07:29,36） | — | 取值不规范 | §4 定一个名字 |
| U-46 | `sql/engine/expr/ob_expr.h`（含 `.cpp`） | translate（R-frame-adjacent-REVIEW） | 07：rewrite:frame，单元目标是桩（07:40） | — | 处置不同 | 照 07；搬到 sql 的函数写进 `seams.tsv` |
| U-47 | `query/api/query/engine/vector/*.h`（12 个） | translate | 07：rewrite:frame（07:42） | — | 处置不同 | 照 07 |
| U-48 | `query/api/query/engine/expr/{ob_i_expr_extra_info, ob_expr_extra_info_factory}.h`（含 sql 的 `ob_expr_extra_info_factory.cpp`） | translate | 07：generate:ir_enums（07:45,46） | 09 的生成器表无 `ir_enums` | 处置不同、取值不规范 | 照 07；09 补生成器 |
| U-49 | `sql/resolver/expr/ob_raw_expr.h`、`sql/resolver/ob_stmt.h`、`sql/optimizer/ob_log_operator_factory.h` | translate | 07：translate，其中工厂类"类级替换为 `QueryArena`"（07:51-53） | — | §4 无此形态 | 作为类级重映射写进 `seams.tsv` |
| U-50 | `storage/memtable/mvcc/{ob_mvcc_row, ob_tx_callback_list}.h` | rewrite:storage_core | 08：拆分（机制 rewrite:storage_core，策略 translate）（08:16,21，AM-61） | — | 处置不同 | 照 08 |
| U-51 | `storage/memtable/mvcc/ob_mvcc.h` | rewrite:storage_core | 08：translate，链接成员重映射进核心（08:22） | — | 处置不同 | 照 08 |
| U-52 | `storage/allocator/ob_handle_list.h` | translate | 08：rewrite:storage_core（08:25） | — | 处置不同 | 照 08 |
| U-53 | `share/cache/{ob_kvcache_struct, ob_kvcache_store}.h` | translate（REVIEW） | 08：rewrite:share_cache（08:34,35） | — | 处置不同 | 照 08 |
| U-54 | `share/cache/{ob_kv_storecache, ob_kvcache_inst_map, ob_cache_utils}.h` | translate | 08：rewrite:share_cache（08:37-39） | — | 处置不同 | 照 08 |
| U-55 | `share/scn.h`、`share/transaction/ob_tx_id.h`、`data_plane/api/data_plane/transaction/ob_tx_seq.h`、`share/log/palf/lsn.h`、`ob_datum_row.h` 的 `ObDmlFlag`、`log_define.h:86-129` 的常量、`share/ob_define.h:311` | translate | 08 提议下移到 oblib、1d 由 08 写（AM-60） | — | 提议中 | 见 C-02、C-25 |
| U-56 | `query/api/query/parser/parse_node.h` | keep-c，目标在 query | 09：replace:parser，目标改为 `rust/port/parser/src/parse_node.rs`（09:44,74） | — | 处置与路径都不同 | 照 09，并把路径例外排进 §4 的修订 |
| U-57 | `sql/parser/parse_define.h`、`sql/pl/parser/{parse_stmt_node, parse_stmt_item_type}.h` | keep-c | 09：replace:parser（09:53,67,68） | — | 处置不同 | 照 09 |
| U-58 | `sql/parser/{parse_malloc, parse_node_hash}.h` | translate（REVIEW） | 09：replace:parser（09:54,55） | — | 处置不同 | 照 09 |
| U-59 | `sql/parser/{ob_parser_charset_utils, parser_utility}.h` | translate（REVIEW） | 09：delete（09:56,58） | — | 处置不同 | 照 09（第 16 类确认） |
| U-60 | `share/geo/ob_srs_wkt_parser.h`；`share/geo/{ob_s2adapter, ob_geo_to_s2_visitor}.h`；`oblib/lib/vector/{ob_vsag_adaptor, ob_vector_util}.h` | translate（REVIEW） | 09：keep-cxx:bgk_shim、s2_shim、vsag_shim（09:187） | — | 处置不同 | 照 09 |
| U-61 | `share/geo/ob_geo_tree.h` | keep-cxx:bgk_shim | 09 Q2 推荐 translate（09:201-202） | RULEBOOK §4 写 keep-cxx | 提议中 | 交人（09 Q2） |
| U-62 | `sql/resolver/cmd/{ob_sql_topic, ob_sql_category}.def` | data:def_table | 09：delete（09:221，AM-55） | — | 处置不同 | 照 AM-55 |
| U-63 | `share/system_variable/` 下 6 个生成文件、`sql/session/ob_system_variable_factory.{h,cpp}`、`oblib/lib/codec/ob_generated_*_bp_func.{h,cpp}` | 无（depmap 记为 generated） | 09：补进 `units.tsv`，处置 generate:sysvar、generate:bitpacking（09:197,201） | — | 缺行 | 1c 补 |
| U-64 | `sql/engine/expr/ob_expr_to_pinyin_tab.h` | translate（REVIEW） | 09 Q7 推荐 data:pinyin_table（09:2219-2221） | — | 提议中 | 交人（09 Q7） |

**合计**：01 相关 35 个单元（U-01–U-13），02 相关 4 个，03 相关 23 个（含提议），04 相关 12 个，05 相关 32 个，06 相关 3 个，07 相关 19 个，08 相关约 14 个，09 相关 17 个（另有 U-63 的缺行）。两家冲突或多家认领 8 处（U-09、U-17、U-22、U-27、U-33、U-42、U-30 的标签、U-12 的语义）；无人认领 3 处（U-08、U-16、U-26），另加 C-02 列出的第 3 步单元。

## 4. 修订分诊（`amendments.tsv`，77 行）

- **类别**：事实更正（RULEBOOK 关于 C++ 或数字的说法错了）、规则修改（事实可能没错，提议换规则或补规则）、改名（补 §4 改名表）、流程（白名单、lint、范围、清单、生成表等）。一行可能兼有几类，按主要的一类计。
- **核对**：本文作者逐行回源码复核。CONFIRMED＝引用的事实全部成立；PARTIAL＝核心成立、部分事实或计数不对；REFUTED＝核心前提不成立；"无可核对事实"＝纯规则、改名或流程，只核对了它对 RULEBOOK 原文的引述。
- **建议**：接受／修改后接受／合并到 M-xx／交人决定／不改 RULEBOOK。
- 文档内部编号 06-A4 不存在：06 自己撤回了（06:1331），没有漏排。

### 4.1 分类与核对结果

| 类别 | 行数 | CONFIRMED | PARTIAL | REFUTED | 无可核对事实 |
|---|---|---|---|---|---|
| 事实更正 | 30 | 28 | 2（AM-08、AM-66） | 0 | 0 |
| 规则修改 | 30 | 23 | 2（AM-10、AM-12） | 0 | 5 |
| 改名 | 7 | 1 | 0 | 0 | 6 |
| 流程 | 10 | 7 | 2（AM-11、AM-22） | 0 | 1 |
| 合计 | 77 | 59 | 6 | 0 | 12 |

### 4.2 重复、重叠与冲突

**重复与同题**：严格重复 2 对，即 AM-35 与 AM-23（时间工具进 1d、归 03），AM-76 第 1 部分与 AM-48 的重载句（replace 单元的方法名以 API 文档为准）。另有 8 组同题，可以合成一条：AM-05 与 AM-16；AM-20 与 AM-36；AM-31 与 AM-75；改名的 7 行；AM-62、AM-63 与 AM-68；AM-34 与 AM-42；A.4 的 5 行；AM-32 与 AM-14。合计 10 组、29 行，见 4.4。

**冲突**：

| ID | 双方 | 冲突 | 裁决建议 |
|---|---|---|---|
| K-01 | AM-26 ↔ AM-72 | AM-26 提议从 §1 白名单删掉 slotmap；05 的 `ObList`（AM-72）正要用它 | 保留 slotmap；AM-26 只接受墓碑方案 |
| K-02 | AM-15 ↔ AM-18、03 §2.13 | AM-15 删去 0.8 的内存探测；03 照 0.8 实现了 `probe_read_memory`，AM-18 还把它登记进改名表 | 交人；推荐接受 AM-15，03 删函数、AM-18 删名字 |
| K-03 | AM-48、AM-76 ↔ 04:1285 | 修订说 replace 单元的重载名以 API 文档为准，04 说以 `overloads.tsv` 为准 | 修订为准，改 04 |
| K-04 | AM-70 ↔ 04:54,919 | 修订让日志直接调 03 的 trace id，04 经钩子读 | 修订为准，改 04 |
| K-05 | AM-23、AM-35 ↔ 02 Q5（02:1574） | 时钟归 03，还是用 02 的私有时钟 | 修订为准，02 撤回 Q5 |
| K-06 | AM-32 ↔ 01:978、03:677、06:895、07:1220 | 大写特性名、注入点始终编译；文档用小写并把注入点门控掉 | 修订为准，改文档 |
| K-07 | AM-10 ↔ 09 Q5（09:2213-2215） | 解析器回调是否检查内存 | 09 为准（基线事实），改 AM-10 与 01:81 |
| K-08 | AM-63 ↔ 01:41、02:76、`units.tsv` | RetireStation 的落点 | 修订为准，三处同步改 |
| K-09 | AM-12 ↔ 05 | 请 05 给数组加 tracker，05 没有 | 05 在 §8 回应，否则 AM-12 删去这一请求 |
| K-10 | AM-71 ↔ 07:1211 | IR 节点状态里的 `ObSEArray` 写 `SmallVec`/`Vec`，还是 oblib 的新类型 | 修订为准，改 07 |
| K-11 | AM-20 ↔ AM-36、04 Q6 | 系统调用封装的落点：AM-20、AM-36 的备选都提到"由 03 持有的 `oblib::sys`"，04 Q6 的备选写 `oblib::signal::sys` | 合并为 M-03，只定一个落点 |

### 4.3 逐行分诊

| 行 | 类别 | 核对 | 证据 | 并入 | 建议 |
|---|---|---|---|---|---|
| AM-01（02 A1） | 事实更正 | CONFIRMED | `ob_latch.cpp:67-72,91-98` 用 `GETTID()`，`uid >= WRITE_MASK`（`ob_latch.h:66`，`1<<30`）时报 `OB_INVALID_ARGUMENT`；`ob_tc_rwlock.h:187,234` 记 `itid+1`；macOS 的 tid 是 `pthread_threadid_np`（`ob_define.h:1911-1922`），在加锁处截成 u32 | — | 接受：0.8 注明 Rust 记 itid+1 是有意的内部差异（只影响锁字数值），不进 A.4 |
| AM-02（02 A2） | 事实更正 | CONFIRMED | `ob_bucket_lock.cpp:66` `latch_cnt_ = bucket_cnt_/8+1`；`ob_bucket_lock.h:332-334` 取 `bucket_idx/8`；多桶 guard 按 latch 下标记录（`ob_bucket_lock.cpp:315-342`） | — | 接受；05 的 `BucketedHashMap`（05:1088）以它为前提 |
| AM-03（02 A3） | 事实更正 | CONFIRMED | `ob_qsync.h:45-52`（`get_id() % ref_num_`）、`:65-74`（逐槽忙等、不 PAUSE）、`:121`（`ObQSyncWrapper<256>`）；`ob_qsync_lock.h:43-46`（48 槽）；`itid_slots[1024]`×64 = 65536（`ob_tsi_utils.h:36`） | — | 接受；同时把 0.8 的 `enter/leave` 改为 C++ 名（02 不提供 `leave`，C-26） |
| AM-04（02 A4） | 事实更正 | CONFIRMED | `ObFutex` 带 `CACHE_ALIGNED`（`ob_futex.h:130`）；`TCRWLock` 的成员（`ob_tc_rwlock.h:298-303`）排下来 x86 192 字节、ARM 384 字节 | — | 接受 |
| AM-05（02 A5） | 流程 | CONFIRMED | §1 白名单（RULEBOOK:582-590）没有 loom，§3（:949）要求 loom 测试 | M-01 | 合并接受 |
| AM-06（02 A6） | 改名 | 无可核对事实 | 改名表 `oblib::sync` 一行只有锁类型名（RULEBOOK:1027） | M-05 | 合并接受 |
| AM-07（02 A7） | 流程 | 无可核对事实 | `let _ = x.lock()` 立即释放 guard 属实；`clippy::let_underscore_must_use` 是默认关闭的 restriction lint，`-D warnings` 不会启用，"误伤"一句不成立 | — | 修改后接受：保留 `disallowed_methods` 与 grep 规则，删去 clippy 一句 |
| AM-08（02 A8） | 事实更正 | PARTIAL | 成立：`ObCond`（`ob_queue_thread.h:42-57`）、`SCondTemp`（`ob_scond.h:131-215`，`ob_priority_queue.h:292` 用 `SCondTemp<3>`）、`ObNullLock`（`ob_spin_lock.h:94`，`ob_pool.h:35,106`）。不成立：`ObAtomicReference` 在 src 里没有使用者（只有自身的 `.h/.cpp`，08:82 同此结论），不是"KV cache 用"；`obsys::ObRWLock` 用在 4 个文件（`ob_tablet_gc_service.h:125`、`ob_empty_shell_task.h:59`、`ob_compaction_schedule_util.h:110`、`ob_log_allocator_mgr.h:53`），不是 3 个 | — | 修改后接受：`ObAtomicReference` 改记 delete（第 16 类确认），02 不必提供 |
| AM-09（02 A9） | 事实更正 | CONFIRMED | 读锁路径 `ob_tx_ctx.cpp:5975-5978`；`CtxLock::unlock_ctx` 无条件调 `before_unlock`（`ob_trans_ctx_lock.cpp:161-165`）；`before_unlock` 写 `opid_`、`has_pending_callback_`（`ob_trans_ctx.cpp:46-60`） | — | 接受 |
| AM-10（01 Q1） | 规则修改 | PARTIAL | 成立：tracker 是 `thread_local`（`ob_memory_tracker.cpp:26`），唯一装入点 `ob_th_worker.cpp:245`；嵌套请求上下文 `obmp_query.cpp:475` 不受保护。不成立：解析器的两个 C 回调在基线里是恒成功的 weak 版本（`parse_node.c:177-178`）；`src/sql/parser/ob_memory_tracker_wrapper.cpp` 不参与构建，全部目标文件里只有 weak 定义 | — | 修改后接受：删去解析器回调一句（C-05，K-07） |
| AM-11（01 Q2） | 流程 | PARTIAL | `CURRENT_CONTEXT` 119 行、`->CREATE_CONTEXT` 52、`->get_arena_allocator()` 31、`->get_malloc_allocator()` 10，均复现；`ObCtxIds::WORK_AREA` 复数为 66 行、31 个文件，不是 83 行 | — | 修改后接受（改计数）；07 须回应 `MemCtx` 的来源（C-28） |
| AM-12（01 Q3） | 规则修改 | PARTIAL | A.4 没有对应项属实。裁判依据读反：`join_many_table.test:13` 把 `ob_sql_work_area_percentage` 调到 100（缺省 5，`:86` 恢复），裁判里没有调低工作区的用例（旁证 fidelity-01 F-04）；口径还漏了 `ObSEArrayImpl::reserve` 按块取整（旁证 fidelity-01 F-01）；给 05 的请求没人接（K-09） | M-09 | 修改后接受：保留 A.4 `mem-accounting`，删去裁判改动，补取整口径 |
| AM-13（01 Q5） | 规则修改 | CONFIRMED | `malloc_hook` 只在非 Apple、非 Windows、非 Android 构建（`src/oblib/lib/CMakeLists.txt:34-41`）；jemalloc 只在 Linux/macOS 且非 ASAN 时构建（`deps/external/CMakeLists.txt:17`）；macOS zone（`ob_malloc.cpp:177-194`） | — | 接受；`init_malloc_hook` 的位置并入 main 序列（M-08、C-17） |
| AM-14（01 Q6） | 规则修改 | CONFIRMED | `build_release/compile_commands.json` 363 条命令，0 条含 `ENABLE_SANITY` | M-10（特性名） | 接受；01 的 `enable_sanity` 改为 `ENABLE_SANITY` |
| AM-15（01 Q7） | 规则修改 | CONFIRMED | `sigsetjmp`/`siglongjmp` 的段错误捕获只在 `memory_dump.cpp:41-75`，用于 `:395,648,788` 三处扫描；`ob_dump_task_generator.cpp:95` 取用户给的地址 | — | 交人决定；推荐接受，03 随之删 `probe_read_memory`（K-02） |
| AM-16（01 Q8） | 流程 | CONFIRMED | 工具链钉 1.98.1（RULEBOOK:130）；§1 无 trybuild；Miri 与 `-Zsanitizer` 只在 nightly | M-01 | 合并接受 |
| AM-17（01 Q9） | 改名 | 无可核对事实 | 改名表 `oblib::mem` 一行只有 6 个名字（RULEBOOK:1026） | M-05 | 合并接受 |
| AM-18（03 R1） | 改名 | CONFIRMED | RULEBOOK:349,750-751,809 用了 `Timer`、`TimerService`、`SimplePool<T>`、`ReentrantThread`、`AdaptivePool`、`TimerTask`、`Runnable`，不在改名表；对应的 C++ 名 `ObTimer`、`ObTimerService`、`ObSimpleThreadPoolBase`、`ObReentrantThread`、`ObAdaptiveWorkerPool`、`ObTimerTask` 都存在 | M-05 | 合并接受；先对齐 03 §1.4、§8.2 R1 与本行的名单；AM-15 通过则删 `probe_read_memory` |
| AM-19（03 R2） | 规则修改 | CONFIRMED | Rust 的可分派接收者只有 `&Self`、`&mut Self`、`Box`/`Rc`/`Arc<Self>`、`Pin<…>`；`self: &Arc<Self>` 不实现 `DispatchFromDyn`，带它的 trait 做不成 `dyn`；旁证 fidelity-03（R2 核对正确） | — | 接受 |
| AM-20（03 R3） | 流程 | CONFIRMED | §3 白名单只有 `thread/stack/**`（RULEBOOK:931）；std 只能在建线程时命名，拿不到 OS tid | M-03 | 合并接受 |
| AM-21（03 R4） | 事实更正 | CONFIRMED | `ob_timer_service.cpp:233-235` 先 `init(MIN_WORKER_THREAD_NUM, TASK_NUM_LIMIT, "TimerWK")` 再 `set_thread_count(MAX_WORKER_THREAD_NUM)`；`ob_simple_thread_pool.ipp:63-68,196-199`；旁证 fidelity-03 | — | 接受 |
| AM-22（03 R5） | 流程 | PARTIAL | 放开清单没有 `non_snake_case`（RULEBOOK:967）；`ObSysTime::toMicroSeconds` 存在（`Time.cpp:134`）。但 `runTimerTask` 的实现约 77 处（类外定义 72、类内定义 5），145 是声明、定义、调用合计（全部出现 153 行） | — | 接受（改计数） |
| AM-23（03 R6） | 流程 | CONFIRMED | `src/oblib/lib/time/` 5 个单元合计 1,570 行（`wc -l`）；`units.tsv` 全是 translate；`ob_addr.h` 也是 translate | M-02 | 合并接受；`ObAddr` 须有接手方（C-28）；02 撤回 Q5（K-05） |
| AM-24（03 R7） | 规则修改 | 无可核对事实 | 0.5"生命周期方法一律取 `&self`"（RULEBOOK:208）；交给线程的闭包要 `'static`，属实 | — | 接受；04 的 `ObLogger::init(&'static self)` 已按此写 |
| AM-25（07 A1） | 规则修改 | CONFIRMED | `OB_MAX_LONGTEXT_LENGTH = 2^29-1`（`ob_define.h:1420`）；`len_:29`、`flag_:2`、`null_:1`（`ob_datum.h:143-148`），12 字节里没有空位放判别位 | — | 接受 |
| AM-26（07 A2） | 规则修改 | CONFIRMED | RULEBOOK:251（`NonZeroU32`）与 :262、:725（slotmap 代际 key）互相矛盾；`free_stmt` 只对 `ObSelectStmt`（`ob_stmt.cpp:207-226`） | — | 修改后接受：接受墓碑方案，拒绝"白名单删 slotmap"一句（K-01） |
| AM-27（07 A3） | 规则修改 | CONFIRMED | R1 的类型表只有 `ObDMLStmt`（RULEBOOK:718）；sql 里 `create_stmt<` 101 行，派生自 `Ob*Stmt` 的类声明 112 行 | — | 接受；四种形态的判定脚本交 1c |
| AM-28（07 A4） | 规则修改 | CONFIRMED | `TempAllocGuard` 374 行（去掉 `ob_expr.h` 为 370）；"守卫内继续求值 66 处"是 07 附录 A 的启发式，未独立复数；`ob_expr_repeat.cpp:253-262` 的写法属实 | — | 接受；计量差别归 A.4 `query-mem-limit` |
| AM-29（07 A5） | 改名 | 无可核对事实 | 改名表没有 `StoreTable` 等 6 个名字 | M-05 | 合并接受；补 07 的 `EvalBatchFn`、`EvalEnumSetFn`、`BitWord`（C-27） |
| AM-30（07 A6） | 规则修改 | CONFIRMED | 安全 Rust 从字节切片借 `&[u32]` 要求 4 字节对齐；C++ 经 `ObDatum::to_obj` 直接用载荷里的 digit | — | 接受 |
| AM-31（04 #1） | 事实更正 | CONFIRMED | `N_NULL "null"`、`N_TRUE "true"`（`ob_name_def.h:22-24`）；KV 路径空指针写 `%s:null`（`ob_print_utils.h:718-760`），bool 写 true/false（`:858-868`），float/double 带引号（`:849-857`）；`[cnt:N, …]`（`ob_iarray.h:68-86`）；`ob_array.h:748-764` | M-04 | 合并接受 |
| AM-32（04 #2） | 事实更正 | CONFIRMED | `OB_E`、`ERRSIM_POINT_DEF`、`EventTable` 不在 `#ifdef ERRSIM` 里（`ob_tracepoint.h:108-128`）；按预处理分支统计，`#ifdef ERRSIM` 之外的 `OB_E(` 92 处、`ERRSIM_POINT_DEF(` 92 处（之内各 99、6）；`ob_service.cpp:866-868` 可在线设置；RULEBOOK:556 写 `ERRSIM`、:617 写 `errsim` | M-10 | 接受；01、03、06、07 照改（K-06） |
| AM-33（04 #3） | 规则修改 | CONFIRMED | `obmp_query.cpp:361` `setup_wb`；`obmp_base.cpp:370` `ob_setup_tsi_warning_buffer(NULL)`；`ob_virtual_warning.cpp:83-85`、`ob_mysql_end_trans_cb.cpp:262,267` 直接读会话的缓冲区 | — | 接受 |
| AM-34（04 #4） | 规则修改 | CONFIRMED | `ob_server.cpp:650` `OB_LOGGER.init(log_cfg)`；未初始化时 `ob_log.cpp:1666-1667` 返回 `OB_NOT_INIT` | M-08 | 合并接受 |
| AM-35（04 #5） | 流程 | CONFIRMED | `ObBasicTimeGuard` 在 `ob_common_utility.h:67`；03 已把该单元定为 `replace:thread`（03:50） | M-02（与 AM-23 重复） | 并入 AM-23 |
| AM-36（04 #6） | 流程 | CONFIRMED | `dup2`（`ob_async_log_struct.cpp:93-104`、`ob_log.cpp:820-821`）、`statfs`（`ob_log.cpp:1465-1466`、`ob_log_compressor.cpp:605-606`）、`localtime_r`（`utility.cpp:2204`） | M-03 | 合并接受 |
| AM-37（04 #7） | 事实更正 | CONFIRMED | 用到 `snprintf` 返回值的：`ob_log.cpp:402`、`ob_string.h:633`、`ob_backtrace.cpp:168` | — | 接受 |
| AM-38（04 #8） | 规则修改 | 无可核对事实 | A.4（RULEBOOK:1211-1221）只有 `log-prefix`，没有"日志内容"一项 | M-09 | 接受 |
| AM-39（04 #9） | 事实更正 | CONFIRMED | `abort_unless` 只调 `ob_abort()`（`ob_abort.h:35-43`）；`ob_abort` 写 stderr 后 `abort()`，不写日志（`ob_abort.cpp:31-44`） | — | 接受 |
| AM-40（04 #10） | 事实更正 | CONFIRMED | Windows 改写 `%l` 的分支：`ob_print_utils.cpp:460-505`、`ob_log.cpp:149` 起；`log_user_message` 直接 `vsnprintf`（`ob_log.cpp:663`） | — | 接受 |
| AM-41（04 #11） | 事实更正 | CONFIRMED | `EXECUTE_COUNT_PER_SEC` 用 `LOAD128`/`CAS128`（`ob_macro_utils.h:891-916`），7 个调用点；`REACH_COUNT_PER_SEC` 2 个 | — | 接受 |
| AM-42（04 #12） | 规则修改 | CONFIRMED | `main.cpp:693-698` 四次 `setlocale` | M-08 | 合并接受；包装函数的归属一并定（C-17） |
| AM-43（06-A1） | 事实更正 | CONFIRMED | `MacOSX15.4.sdk/usr/include/c++/v1/__tree:1673-1704` 的 `__find_equal`：两次比较都为假就当相等，所以 NaN 等于第一个比较到的节点；`ob_array_cast.cpp:989`（fast_float）；`ob_expr_map.cpp:235-252`（`std::map<Element_Type, …>`） | — | 交人决定（推荐 `CppFloatMap`） |
| AM-44（06-A2） | 事实更正 | CONFIRMED | `libc-0.2.190` 只在 `src/teeos/mod.rs:1283` 声明 `frexp`，没有 `ldexp`、`modf`；`remainder` 8 处、`lround` 6 处、`lrint` 1 处 | — | 接受 |
| AM-45（06-A3） | 规则修改 | CONFIRMED | `ob_core_table_proxy.cpp:855-866`（NULL 拼成 `NULL`、空串拼成 `''`）；`ob_string.h:313-320`；对 `.ptr()` 的显式判空，按窄口径复数为 119 行（原文"约 172"口径更宽） | M-09 | 交人决定；同时定 01 的 `try_str` 怎么表达 NULL（C-15(15)） |
| AM-46（06-A5） | 规则修改 | CONFIRMED | `errno` 446 行；`utility.cpp:2086-2097` 靠 errno 判溢出 | — | 接受（归 06 的 `oblib::num`） |
| AM-47（06-A6） | 规则修改 | CONFIRMED | `qsort` 恰好 2 处 | — | 接受 |
| AM-48（06-A7） | 改名 | 无可核对事实 | 名字清单见 06 §2.10；重载一句与 AM-76 第 1 部分相同 | M-05、M-06 | 合并接受（K-03） |
| AM-49（06-A8） | 规则修改 | 无可核对事实 | 0.7 要求用户数据大小的分配可失败（RULEBOOK:335） | M-09 | 交人决定 |
| AM-50（06-A9） | 事实更正 | CONFIRMED | src 里没有 `ob_strtol`；`ob_ctype_simple.cc:60,144,224,309,405`（`ob_strnto*_8bit`、`ob_strntoull10rnd_8bit`）、`:754,759`（给 C 解析器的包装）；`ob_dtoa.cc:344,389-397`（fast_float 快路径加回退） | — | 接受 |
| AM-51（09 A1） | 事实更正 | CONFIRMED | `parse_node.c:551-553`（`#if defined(__GNUC__) && defined(__x86_64__)`）；`ob_parse_simd.cpp:204` | — | 接受 |
| AM-52（09 A2） | 事实更正 | CONFIRMED | `sql_parser_base.h:774-796`、`:797-811` 都在注释里；活代码只把 `charset_info_` 交给回调（如 `:771-773` 的 `check_real_escape`） | — | 接受 |
| AM-53（09 A3） | 规则修改 | CONFIRMED | `pl_ns_` 每次解析时设置（`ob_parser.cpp:1090`） | — | 接受 |
| AM-54（09 A4） | 事实更正 | CONFIRMED | `gen_ob_sys_variables.py:1170-1197` 写 share 6 个文件、`sql/session` 2 个；JSON 里钩子键 94 处；钩子类从 `ob_system_variable.h:775` 起 | — | 接受 |
| AM-55（09 A5） | 事实更正 | CONFIRMED | `ob_lib_cache_register.cpp:98-116` 三次 include `ob_lib_cache_register.h`（该头在 `:18` include 这个 `.def`）；`ob_sql_topic.def`、`ob_sql_category.def` 没有包含点 | — | 接受 |
| AM-56（09 A6） | 事实更正 | CONFIRMED | `ob_geo_dispatcher.h:1314-1366`：每个平台 20 个 catch 子句、18 个不同错误码，Windows 分支在 `:1347-1351`；RULEBOOK:516 写的是 17 | — | 接受 |
| AM-57（09 A7） | 规则修改 | 无可核对事实 | RULEBOOK:535"回调上下文类型必须 Sync" | — | 接受 |
| AM-58（09 A8） | 改名 | 无可核对事实 | — | M-05 | 合并接受；`zlib1213_sys`、`build_support` 也要登记（C-27） |
| AM-59（09 A9） | 事实更正 | CONFIRMED | `ob_expr_regexp_context.cpp` 用 15 个 ICU 函数（12 个 `uregex_*`，加 `u_strFromUTF8`、`u_strToUTF8`、`u_errorName`）；`ob_libxml2_sax_handler.cpp:29-61` 的非空回调 11 个；`oceanbase.android.arm64.deps:11` 是 libcurl 8.2.1 | — | 接受 |
| AM-60（08 A1） | 规则修改 | CONFIRMED | 字段类型见 `ob_mvcc_row.h:85-90,280-292`；行数 scn 640（154+486）、ob_tx_id 113、ob_tx_seq 190、lsn 217 | M-02 | 交人决定（推荐项）；同时改 README 模块表（C-25） |
| AM-61（08 A2） | 规则修改 | 无可核对事实 | 08 §1.2 列出的策略函数确实引用 storage/tx 的类型 | — | 接受；函数级重映射写进 `seams.tsv` |
| AM-62（08 A3） | 事实更正 | CONFIRMED | `MAX_RETIRE_SLOT_NUM = OB_MAX_CPU_NUM * 32`（`ob_retire_station.h:198`），按 `get_itid() % …` 取槽（`:258`）；`OB_MAX_CPU_NUM` x86-64 为 64、aarch64 为 128（`ob_define.h:1483-1487`）；`MAX_QCLOCK_SLOT_NUM = OB_MAX_CPU_NUM * 4`（`:33`） | M-07 | 接受；`OB_MAX_CPU_NUM` 进 M-02 |
| AM-63（08 A4） | 规则修改 | CONFIRMED | RULEBOOK:370-371 把两份回收放在两个互不依赖的 crate；share::cache 确实用 `QClockGuard`（`ob_kvcache_store.cpp:636,774,1178,1197`）和 `RetireStation::retire`（`:1226`） | M-07 | 修改后接受：02 共审，同时改 01:41、02:76、`units.tsv`（K-08） |
| AM-64（08 A5） | 改名 | 无可核对事实 | — | M-05 | 合并接受 |
| AM-65（08 A6） | 规则修改 | CONFIRMED | `log_group_buffer.cpp:461-477` 先 `memcpy` 再复查 `readable_begin_lsn_` | — | 接受 |
| AM-66（08 A7） | 事实更正 | PARTIAL | 类名确是 `ObExtInfoCallback`（`storage/lob/ob_ext_info_callback.h:84`）；但 RULEBOOK 0.9 只写"共三种"，没有出现 `ObExtInfoLogCallback`；错名在 `migration/design/survey/storage-txn.md:84` | — | 不改 RULEBOOK；改调查备忘 |
| AM-67（08 A8） | 事实更正 | CONFIRMED | `ob_tablet_ddl_kv.h:83-85` 的第二个实例化；`ob_keybtree.cpp:1742-1761` 函数内静态的回收域 | — | 接受 |
| AM-68（08 A9） | 事实更正 | CONFIRMED | 块句柄经 `get_retire_station().retire(…)`（`ob_kvcache_store.cpp:1226`）退休，要等 QClock 的宽限期；hazard pointer 持有者和 map 读者不被等待 | M-07 | 接受 |
| AM-69（08 A10） | 规则修改 | CONFIRMED | `is_fragment`/`internal_map_replace`（`ob_kvcache_map.cpp:193,385,616,706`）；`KVCacheRep` 定时器（`ob_kv_storecache.cpp:215`） | M-09 | 接受（推荐扩写 `mem-view`） |
| AM-70（03 R8） | 规则修改 | CONFIRMED | RULEBOOK:234 的"同理"有两种读法；`ObCurTraceId::get_trace_id(` 159 行；v0.1 原文见 `design/review/soundness.md` S-11（:191） | — | 接受；04 删 `TRACE_ID_STR_HOOK`（K-04） |
| AM-71（05-A1） | 规则修改 | CONFIRMED | Rust 的方法解析先选固有方法：`Vec::reserve(usize)`、`Vec::remove(usize)` 遮蔽同名 trait 方法，实参是 `i64` 时直接报错；`ObFixedArray::reserve` 返回 `OB_SIZE_OVERFLOW`（`ob_fixed_array.h:159-170`） | — | 接受；07:1211 随之改（K-10） |
| AM-72（05-A2） | 规则修改 | CONFIRMED | `ob_ik_token.h:94-95` 的迭代器类型；`ob_ik_arbitrator.cpp:226-241` 把迭代器存进 `conflict_stack` 以后再用 | — | 接受；与 AM-26 冲突时保留 slotmap（K-01） |
| AM-73（05-A3） | 事实更正 | CONFIRMED | `typedef uint32_t BitSetWord`（`ob_bit_set.h:39,188`） | — | 接受 |
| AM-74（05-A4） | 事实更正 | CONFIRMED | `top`：`ob_log_apply_service.cpp:175`、`ob_load_data_impl.cpp:1455,1457`；`push_front`：`ob_tmp_file_thread_wrapper.cpp:1043,1151`、`ob_priority_queue.h:65-72` | — | 接受 |
| AM-75（05-A5） | 事实更正 | CONFIRMED | `ob_array.h:748-764`（省略号之后仍打印最后一个元素）；`ob_list.h:537`、`ob_hashset.h:202` 各有自己的 `to_string` | M-04 | 合并接受；04:1555 先改（C-23） |
| AM-76（05-A6） | 流程 | CONFIRMED | `ObDLinkBase` 有虚函数 `reset`/`to_string`（`ob_dlink_node.h:60-61`）；第 1 部分与 AM-48 的重载句重复 | M-06 | 合并接受（K-03） |
| AM-77（05-A7） | 事实更正 | CONFIRMED | `ObVector::at` 越界记 "invalid index"（`ob_vector.h:127-133`）；`ObSEArray::operator[]` 记 "idx out of range"（`ob_array.h:323-336`） | — | 接受 |

### 4.4 合并后的修订清单

| ID | 主题 | 成员 | 合并后的改法 | 建议 |
|---|---|---|---|---|
| M-01 | 测试工具链 | AM-05、AM-16 | §1 加 loom、trybuild（只作 dev-dependency）；0.3 注明 Miri、ASan 用固定日期的 nightly 在 CI 单独跑 | 接受 |
| M-02 | 1d 范围与"1d 闭包" | AM-23、AM-35、AM-60，另含 06 Q8 与 C-02 的清单 | 0.2 的 1d 范围列出要在 1d 写出的第 3 步单元及负责文档；`units.tsv` 新增 `pretranslate:<模块>` | 接受方向，清单交人确认；02 撤回 Q5 |
| M-03 | 系统调用的白名单路径 | AM-20、AM-36，另含 03 Q13（observer 的 libc 调用） | §3 加一个 `rust/port/oblib/src/sys/**`，由 03 持有，04 与 observer 调用 | 接受 |
| M-04 | TO_STRING 的格式 | AM-31、AM-75 | §2.11 的格式条目改为"按 04 §3.7 的路径表与 05 §3.13 的容器表逐字节复刻" | 接受 |
| M-05 | 改名表 | AM-06、AM-17、AM-18、AM-29、AM-48（名字部分）、AM-58、AM-64 | 按 C-27 的口径合成一条：表只登记类型、trait、宏、crate、模块级 static；补上 04、05 的名单 | 接受 |
| M-06 | replace 单元的生成表 | AM-48（重载句）、AM-76 | `overloads.tsv`、`types.tsv` 对 `replace:*` 单元取 API 文档的名字和形状 | 接受；改 04:1285 |
| M-07 | 回收方案 | AM-62、AM-63、AM-68 | 0.8 改写槽数、单一落点，以及"不等读者"的边界 | 修改后接受（02 共审；改 README 模块表） |
| M-08 | `main` 的开头 | AM-34、AM-42，另含 01 的 `init_malloc_hook`、macOS zone 与 09 的启动断言 | 0.5 的 `main` 序列重写，给出完整顺序和每一步的负责方 | 接受 |
| M-09 | A.4 增补 | AM-12（改写后）、AM-38、AM-45、AM-49、AM-69 | A.4 新增或扩写：`mem-accounting`、`log-content`、`obstring-null`、`oom-abort` 的范围、`mem-view` | 逐项交人 |
| M-10 | errsim 与 cargo 特性名 | AM-32、AM-14（特性名） | 特性名一律照 C++ 宏原样；`ob_e!`/`errsim_point!` 始终编译 | 接受 |

其余 48 行单独处理：直接接受 38 行；修改后接受 5 行（AM-07、AM-08、AM-10、AM-11、AM-26）；交人决定 2 行（AM-15、AM-43）；接受但须先解决冲突的 2 行（AM-70 须改 04、AM-72 须保留 slotmap）；不改 RULEBOOK 1 行（AM-66）。另外，第 2.5 节 C-26 列出的约 30 处偏离应补排修订，并入同一轮审批。
