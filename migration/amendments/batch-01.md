# 修订批次 01：1d 之后的 RULEBOOK 与 API 文档修订

> - **逐行明细**在 `migration/amendments/batch-01.tsv`（112 行，`AB1-001`…`AB1-112`）。本文是它的可读版，供你快速拍板。
> - **输入**：
>   - `migration/infra/amendments.tsv` 77 行；
>   - 四份审查 `migration/infra/review/{consistency,soundness,fidelity,concurrency}.md`：consistency 附带分诊 `amendments-triage.tsv`，fidelity 吸收了 `fidelity-01-mem.md` 和 `fidelity-03-thread-signal.md`；
>   - `migration/inventory/amendments.tsv` 10 行；
>   - 前 20 行的全部审查材料：提议 `first20.proposal.md`、怀疑者 `first20.k1.md`/`k2.md`（第 1 轮）、`first20.k1.r2.md`/`k2.r2.md`（第 2 轮）、修订稿 `first20.revision.md` §2 的修订 A–P。
> - **本批只写了这两个文件**，RULEBOOK 和 API 文档都没改。你签字之后，在批次之间按 tsv 的 `change` 列逐行应用。
> - **来源编号**（tsv 的 `sources` 列）：
>   - `AM-NN`：infra 修订表的第 NN 个数据行，写成 `<doc>@L<NN+1>(AM-NN)`，`@L` 后是物理行号，与前 20 行的写法相同；
>   - `INV@Ln`：inventory 修订表的第 n 行（表头算第 1 行）；
>   - consistency 用小写 c 前缀，与另外两份审查的同名编号区分：发现 `C-NN`、合并组 `cM-NN`、冲突 `cK-NN`、单元表 `cU-NN`；
>   - soundness：发现 `U-NN`、缺失规则 `uR-n`；
>   - fidelity：发现 `B-NN`、缺失规则 `bM-n`。B-xx 已经吸收了 fidelity-01 的 F-01…F-09 和 fidelity-03 的 B1…B5；
>   - concurrency：发现 `K-NN`、缺失规则 `kM-NN`；
>   - `<doc> Qn`：该 API 文档 §8 里的开放问题；
>   - **前 20 行**：
>     - `F20-Pn`、`F20-Qn`：提议**修订前**版本（20:47）末尾的问题第 n 条、交人问题第 n 条；修订后的提议改为引用修订 A–P；
>     - `F20R-X`：`first20.revision.md` §2 的修订 X；
>     - `F20K1-n`、`F20K2-n`：`first20.k1.md`、`first20.k2.md` §4 的第 n 条；
>     - `F20K1r2`、`F20K2r2`：两人的第 2 轮附注；
>     - `F20H-n`：修订后提议"交给人的问题"里第 n 行那一问；
>     - `first20 第 n 行`：一律按 `first20.tsv` 的顺序编号。第一版用的是提议"总表"的编号，已全部改过来。

## 1. 一屏摘要

**规模**：112 行，覆盖全部来源，脚本已核对每个来源编号至少出现在一行里。83 行改 RULEBOOK，其余只改 API 文档、生成表、脚本、清单行或裁判。46 行会改动翻译者照着写的规则，即 tsv 的 `impact` 列标"翻译者面：是"的行。

| 类别 | decide | accept | accept-modified | 合计 |
|---|---|---|---|---|
| decision | 7 | | | 7 |
| fact-correction | | 9 | 3 | 12 |
| rule-change | | 36 | 11 | 47 |
| new-rule | | 9 | | 9 |
| naming | | 1 | 2 | 3 |
| api-fix | | 13 | 3 | 16 |
| process | | 14 | 3 | 17 |
| judge-change | | 1 | | 1 |
| **合计** | **7** | **83** | **22** | **112** |

整行 `reject` 是 0。不采纳的内容都是某条修订里的一部分，单列在第 6 节。

### 1.1 要你回答的 7 个决定（都推荐 A；回复"全部按推荐"即可）

| # | 行 | 问题 | 选项（推荐在前） |
|---|---|---|---|
| D1 | AB1-001 | **1d 闭包**：1d 的签名用到第 3 步才翻译的类型（含高严重度 C-02、C-04） | **A** 新增处置 `pretranslate:<模块>`，冻结前出"1d 闭包表"、逐项指定负责文档，时钟归 03；SCN/ObTransID/ObTxSEQ/LSN/ObDmlFlag 下移到 oblib，由 08 在 1d 写（约 1,100 行）。**B** 1d 一律用原始整数，第 3 步之后回改 API。**C** 值类型留在原 crate（不可行） |
| D2 | AB1-002 | **计账口径**：落盘点、4030、range 退化取决于 C++ 的字节计账，落盘又决定并列行序（D5） | **A** 能对齐的照 C++ 字节数对齐（由 DWARF 生成 `cxx_sizeof.tsv`、照抄 ObSEArray 按块取整、容器带计账），余下进 A.4 `mem-accounting`，并在 D5 旁注"接近内存边界时不保证并列行序"。**B** 不对齐，全部进 A.4。**C** 逐行移植 C++ 的上下文树计账（与 D7 冲突） |
| D3 | AB1-003 | **ObString 的 NULL 与空串** | **A** 默认把 NULL 当空串；清单第 4 类把需要区分的、或会流进 `%s`/SQL 拼接的标成 `Option`，`%s` 遇 None 打印 `(null)`；A.4 的 `obstring-null` 只剩比较。**B** `ObStr` 一律是 `Option`（约 1.7 万处） |
| D4 | AB1-004 | **0.8 的内存探测**（冲突 cK-02） | **A** 删去那一句，ObMemoryDump 只留门面，03 删 `probe_read_memory`。**B** 照 0.8 移植按用户地址的内存转储 |
| D5 | AB1-005 | **CtxLock 三把 latch 怎么分数据**（前 20 行第 9 行，仍是 UNKNOWN） | **A** 逐字段审计（一轮，约 32 个字段），审计后再定结构是单把带数据的 `ctx_lock_` 还是多把。**B** 不审计，维持现行默认（单 `ctx_lock_` 加两把令牌锁、三条排除），会新增 C++ 没有的加锁 |
| D6 | AB1-006 | **ObResultSet 的生命周期类别**（前 20 行第 8 行的 TODO，修订 D） | **A** 一律拥有型：SPI、内部 SQL、PL prepare 都能持有，标 PERF(port)；已确认的第 7 行随之机械更新（AB1-111）。**B** 拆成请求作用域的 `ObResultSet<'q>` 与一个拥有型类型 |
| D7 | AB1-110 | **新增，MySQL 请求 `rpc::ObRequest` 归谁所有**（前 20 行第 19 行，仍是 UNKNOWN，修订 B） | **A** 照 RULEBOOK §2.7 的 Box 移动：会话的请求槽借出、收回 `Box<ObRequest>` 和包缓冲，`handle_ctx_` 改成会话句柄。**B** `Arc<ObRequest>` 加 Latch（现行默认；要修订 §2.7，ObRequest 的可变字段全部改内部可变）。**C** 请求留在会话里，容器只存会话 id |

D5、D6、D7 就是前 20 行留给你的三个问题；另外两问（第 3 行的来源表、第 16 行的旁路表）不需要你选，已并进 AB1-095 和 AB1-089。

### 1.2 高严重度发现的去向

| 发现 | 行 | 一句话 |
|---|---|---|
| U-01 | AB1-007 | `Latch<Arena>` 能经 `lock()` 拿到 `&mut Arena` 去 reset：改为不暴露内部的 `ObSafeArena`/`ObSafeArenaAllocator`（C++ 原名） |
| B-01 | AB1-008 | `eval_args` 会把 IFNULL、BETWEEN、AND/OR 等的短路求值改成全部求值（已用原版复现）：只用于无条件站点，1c 给 306 处打标签 |
| B-02 | AB1-009 | 系统变量要先滤掉 57 个 placeholder（共 719 项）、按 id 排，否则 `SHOW VARIABLES` 会多出行 |
| K-01 | AB1-010 | 锁内 upgrade 出的 `Arc` 成为最后一份时析构回到同一把锁，memstore 句柄表会自锁：新增"临界区内不得发生最后一次 drop" |
| C-01 | AB1-011 | -sys 依赖 oblib，crate 成环：反过来，oblib → -sys |
| C-02、C-04 | AB1-001（D1）、AB1-016 | 1d 闭包与时钟 |
| C-03 | AB1-012 | 约 150 个单元的处置不一致：冻结前对齐，`units.tsv` 是唯一来源，补全处置取值 |

### 1.3 11 处冲突的裁决（每处一行）

| 冲突 | 行 | 裁决 |
|---|---|---|
| cK-01 slotmap 的去留 | AB1-013 | 语句回收改为墓碑；slotmap 保留，给 ObList 用 |
| cK-02 内存探测 | AB1-004 | 交你决定（D4），推荐删 |
| cK-03 重载名以谁为准 | AB1-014 | replace 单元和类级重映射以 API 文档为准，04:1285 改写；translate 单元的普通方法照 RULEBOOK（`create_expr_op_ctx` 见 AB1-106） |
| cK-04 trace id | AB1-015 | 线程局部，日志直接调用，04 删钩子 |
| cK-05 时钟 | AB1-016 | 03 提供，02 撤回私有时钟 |
| cK-06 errsim 与特性名 | AB1-017 | 特性名照 C++ 宏原样（大写），注入点始终编译 |
| cK-07 解析器的内存检查 | AB1-018 | 以 09 为准（基线是 weak 空实现）；AM-10 删去那一句 |
| cK-08 RetireStation 的落点 | AB1-019 | 只有一份，放在 `oblib::sync`，08 写、02 共审 |
| cK-09 05 的计账存储 | AB1-020 | 随 D2：选 A 则 05 提供 |
| cK-10 IR 里的数组 | AB1-021 | 用 oblib 的新类型，07 照改 |
| cK-11 系统调用的落点 | AB1-022 | 只设一个 `oblib::sys`，03 持有；改动进程状态的封装一律 `unsafe fn` |

### 1.4 分诊里标"交人"的 5 行

- AM-15 → D4；AM-45 → D3；AM-60 → D1。
- **AM-43 不再交人**，见 AB1-058。fidelity 已用基线工具链确认，逐行移植 libc++ 红黑树的 `CppFloatMap` 能复刻 NaN 吞键，约 150 行，没有需要取舍的地方。
- **AM-49 改了推荐**，见 AB1-061。本批用 rustc 1.98.1 做了探针：`slice::sort_by` 遇到不是全序的比较器会 panic（随机比较器 200/200 次，C++ 式的 NaN 比较器 3 次中 1 次），在 `panic = "abort"` 下就是进程终止。所以 `std::stable_sort` 改为逐行移植 libc++ 的 `stable_sort`（约 270 行；拿不到缓冲时与 C++ 一样退化为原地归并），不再"扩 oom-abort"。`c_*` 的 NUL 拷贝照原提议扩进 oom-abort，05 数组的 `Clone`（C-26）也一并扩进去。

### 1.5 本批自己核对的事实

这些事实来自未经审查的来源，或者是本批新引用的；每条都给 file:line：

- seams.tsv 里 `type-lower-files=0` 的 991 行，主要是 query→sql 554 行、data_plane→storage 391 行（AB1-024）。
- `print_service_status` 要查 observer 的槽位（ob_server_runtime_controller.cpp:1127）（AB1-025）。
- ObTxDesc：sql 里 27 个成员；arc-swap 只接受 Sized 的 T（arc-swap-1.7.1/src/ref_cnt.rs:91）（AB1-026）。
- C++ 的 `main` 的实际顺序（main.cpp:647-698、877-886）（AB1-027）。
- 纯自旋是 13 项，另 4 项自旋 2000 万次后照常等待（ob_latch_define.h）（AB1-036）。
- replace_questionmarks 只有自身递归（ob_sql_utils.cpp:224）（AB1-047）。
- 规则 5 的名单：ob_tx_ctx.cpp:1169 是 to_string 的打印判断，漏了 :5855-5857（AB1-064）。
- 基线 363 条编译命令都带 `-fno-omit-frame-pointer`（AB1-085）。
- CURRENT_CONTEXT 119 行（另 1 行是宏定义，context.h:46）；get_cur_exec_ctx() 145 处读点，分布在 73 个文件（AB1-095）。
- c23：23 个编译单元以 objit 的副本生效，其中 19 个用到 T_MAX（AB1-097）。
- make_types.py:28-29 把"有原子成员"当成"共享"；errcode_ 的读写都在请求处理线程上（ob_result_set.cpp:694-704、ob_sql_trans_control.cpp:895）（AB1-098）。
- symdeps_seekdb.py:47 的正则丢掉 `[no dead strip]` 的符号（AB1-100）。
- eval_enumset 的 C++ 副本与原表达式只差 `args_[0] = args_[4]`（ob_expr_column_conv.cpp:763-774）（AB1-109）。
- 第 7 行的分片译法仍写 `ObResultSet<'q>`（inventory.c05-self-arena.tsv）（AB1-111）。
- sort_by 会 panic 的探针（AB1-061；探针在 `/tmp/ab1_probe/`，未入库）。
- **更正**：第一版 AB1-096 称"脚本 README 没有 S3b 一节"并标了"本批核对"，这是错的。核对时 grep 的输出被截断了，实际上 S3b 从 README.md:1174 开始，c07、c08 的码在 :1274-1281、:1309-1318。AB1-096 已改写。

## 2. RULEBOOK 改动（按章节；"译"表示翻译者照写的规则会变）

**0.2、0.3、§1（范围、crate、白名单）**
- AB1-001（D1）：新增 `pretranslate`，加 1d 闭包表；5 个值类型下移 oblib。
- AB1-011：oblib → zstd/zlib/libxml2/openssl 的 -sys；-sys 不依赖任何移植 crate。
- AB1-023：loom、trybuild 进白名单（只作 dev-dependency），Miri、ASan 用固定日期的 nightly。
- AB1-013：slotmap 留在白名单。
- AB1-061（译）：std::stable_sort 改为 `oblib::sort::stable_sort`，禁用 `slice::sort*`。

**0.4（seam）**
- AB1-024：新增拆法"上移类型"，类型在下层没有任何使用者时用。这是对 D3 的扩展，不改原决定。
- AB1-025：print_service_status 改由 observer 注册钩子，不再下移。
- AB1-026（译）：ObTxDesc 走 data_plane 的句柄；0.5 的 `ArcSwapOption<ObTxDesc>` 改为 `ArcSwapOption<TxDescRef>`。

**0.5（进程级状态、main、线程局部）**
- AB1-027：`main` 的完整序列。
  - 入口是 `#![no_main]` 的 C 入口：不让 Rust std 改 SIGPIPE、SIGSEGV 的处置；
  - `process_init_before_threads()` 收拢 malloc hook、zone、信号屏蔽、setlocale；
  - `register_hooks()` 做启动断言；
  - 日志写线程照 C++ 在 `ObServer::init` 启动。
- AB1-015（译）：trace id 是 `oblib::thread` 的线程局部；check_status 走每线程的 `WorkerVirt`。
- AB1-028（译）：警告缓冲区改为共享句柄 `Arc<Latch<…>>`。
- AB1-029（译）：要交给线程的生命周期方法取 `&'static self` 或 `self: &Arc<Self>`。
- AB1-018（译）：线程局部可以装 `Arc<ObMemTracker>`。
- AB1-007：共享宿主的 `destroy` 不清 arena。
- AB1-037：每种线程局部缓存写明上限。

**0.6（IR、帧、datum）**
- AB1-008（译）：`eval_args` 只用于无条件站点。
- AB1-030（译）：DataRef 没有内联值，结果参数用 `ObDatumBuf`。
- AB1-032（译）：TempAllocGuard 自带 arena，从线程局部空闲表复用。
- AB1-013：StmtId 留墓碑，不用 slotmap。
- AB1-021（译）：计划里的定长数组用 `ObFixedArray`。
- AB1-034：临时上下文只在 ret 成功时进入。
- AB1-109（译）：eval_enumset 的两个分支都传 `arg0`，`EvalEnumSetFn` 加参数，有 id 的分支不再建副本。

**0.7（内存）**
- AB1-007（译）：线程安全的 arena 用 `ObSafeArena`/`ObSafeArenaAllocator`。
- AB1-033：Linux 照抄 libc 符号覆盖、macOS 照抄 zone 提升；Windows、Android 用系统分配器。
- AB1-018：11049 的首条语句与重试走不同的 MemCtx；解析器的检查点不检查。
- AB1-002（D2）：限额的计账口径。

**0.8（并发）**
- AB1-036：写者 itid+1、13+4 项自旋、TimerWK 下限 0。
- AB1-019：回收只有一份；槽数按 OB_MAX_CPU_NUM；Quiescence 为 256/48 槽；块句柄的退休等 QClock 宽限期。
- AB1-037：栈常数全部乘膨胀系数；扩展段和 main 的新栈加保护页；每线程只缓存 1 段。同时改 A.3。
- AB1-038（译）：`check_stack_overflow` 用 C++ 的三参数形状。
- AB1-039：futex 垫片暂不改，记 PERF(port)。
- AB1-040：删去"生命周期场景会测到"。
- AB1-004（D4）：内存探测一句。

**0.9（存储）**
- AB1-041：palf 组缓冲改用 `[AtomicU64]` 加栅栏。
- AB1-042：keybtree 泛型化；回收域在构造时取定；不依赖安全 trait 的行为；scan_batch 失败时 reset。
- AB1-043（译）：存储核心三个混合单元按函数拆分，策略函数放进 `<类名>Ext` trait。

**0.11（逐字节一致）**
- AB1-044（译）：`ob_printf!` 分 Libc 与 Ob 两种方言，Windows 上 `%ld` 的宽度不同。
- AB1-058（译）：浮点键的 map 用 `CppFloatMap`。
- AB1-002：D5 旁注。
- AB1-105：ENV-1 钉住运行时库的版本。

**0.12（解析器）**
- AB1-045：回调数 19/20；字符集不打补丁；回调的分工；内存检查回调保持 weak。
- AB1-046（译）：LocalParseNode 改为借住槽位的 `init(&'p mut self)`。
- AB1-047：删去死代码例子。

**0.13（生成器）**
- AB1-048：系统变量分到 share 和 sql 两处。
- AB1-049：拆分范围删去 4 个 .def，加上拼音表。
- AB1-050：补 `latch_define` 生成器；generated/ 的清单以 07 为准。
- AB1-051：新增规则：以该平台基线实际产出的文件为准（macOS 上 sed 没生效）。

**0.14（第三方库）**
- AB1-052：计数更正：20 个 catch、ICU 15 个函数、libxml2 11 个回调、Android 上 curl 8.2.1。
- AB1-053：Sync 只要求外部线程回调，跨线程移动的还要 Send。
- AB1-054：几何 bin/tree 类型照常翻译。

**0.15（预处理）**
- AB1-017（译）：特性名大写原名；ENABLE_SANITY 是空特性。

**§2.1–§2.3（错误、日志、格式化）**
- AB1-017（译）：`ob_e!`、`errsim_point!` 始终编译。
- AB1-055（译）：abort_unless、ob_abort 保留同名函数。
- AB1-078（译）：`kpc!(Some(&*g))`；`%s` 收 `impl ObPrintfStr`；user_msg 带 `loc`。
- AB1-056（译）：`snprintf!(&mut buf[..n], …) -> i32`。
- AB1-028（译）：警告缓冲区。
- AB1-064：§2.2 换一个例子。

**§2.4（内存与所有权）**
- AB1-018（译）：`check_mem_status()`。
- AB1-034（译）：WITH_CONTEXT 的写法 `if ret.is_ok() {…}`。
- AB1-035（译）：轮转缓冲只留"写进调用方缓冲"一种译法。
- AB1-003（D3）：NULL。
- AB1-007（译）：线程安全的 arena。
- AB1-095（译）：新增一行"登记活动子对象"（如会话的 `cur_exec_ctx_`）：删字段、显式传参，配来源表。

**§2.5（容器与字符串）**
- AB1-021（译）：一律写 `ObArray`/`ObSEArray`/`ObFixedArray` 新类型。
- AB1-057：ObList 用 slotmap 链表，位集按 32 位字。
- AB1-058（译）：CppFloatMap。
- AB1-059（译）：libm 包装；MySQL 一族的真实名字。
- AB1-060（译）：errno、qsort、`std::stoi` 的译法。
- AB1-061（译）：stable_sort。
- AB1-108（译）：就地改写的 `ObString &` 参数写成 `&mut ObStringWriter<'_>`。
- AB1-078：ObDList 只留拥有节点一种契约。

**§2.6（IR）**
- AB1-031（译）：R1 分四种形态，类型集合改用 ObStmt。
- AB1-013：R8 用墓碑。

**§2.7（并发）**
- AB1-062：锁表四处更正（BucketLatch 锁数 = 桶数/8+1，等）。
- AB1-063（译）：CtxLock 的读锁路径不执行钩子。
- AB1-107（译）：`after_unlock` 只读 Arg 和锁外字段。
- AB1-005（D5）：CtxLock 的数据划分。
- AB1-064（译）：规则 5 的重入名单。
- AB1-065：队列照 C++ 的票号算法，一次只唤醒一个。
- AB1-110（D7）：队列项与 MySQL 请求的所有权。
- AB1-066（译）：`runTimerTask(self: Arc<Self>)`；宿主拆除次序。
- AB1-067（译）：加锁调用不得出现在 `if let`/`match` 的被匹配表达式里。
- AB1-068：慢路径自锁检测。
- AB1-069（译）：纯统计的 `++` 写成 load+store。
- AB1-010（译）：临界区内不得发生最后一次 drop。

**§2.9、§2.11–§2.13**
- AB1-046（译）：栈上的 ParseNode。
- AB1-070：TO_STRING 以 04、05 的路径表为准。
- AB1-071：ObObj 的数字用字节切片。
- AB1-078（译）：`impl<'de> ObUnisBody<'de>`；内部同步容器取 `&mut self`。
- AB1-014：types.tsv 不为被删除的基类生成字段。

**§3（逃生口）**
- AB1-022：白名单加 `oblib::sys`。
- AB1-072：7 条 unsafe API 的健全性规则。
- AB1-073：裸解锁不得是安全的 pub 函数。
- AB1-074：Dekker 握手用 fence，loom 用 fence 版本建模。
- AB1-067：lint 加 significant_drop_in_scrutinee。
- AB1-075：放开 non_snake_case。
- AB1-061：禁用 `slice::sort*`。

**§4（命名与输出路径）**
- AB1-076（译）：改名表的登记口径与补登，含 `<类名>Ext`、`<类名>Locked`、`_HOOK` 等命名模式。
- AB1-014（译）：replace 单元与类级重映射的名字以 API 文档为准；translate 单元照 RULEBOOK。
- AB1-077：同单元重名的嵌套类叫 `外层_内层`。
- AB1-012：处置取值补全，允许空桩。
- AB1-078：1d 手写的 crate 自写 lib.rs；parse_node.h 的路径例外。
- AB1-043：存储核心单元按函数拆分。
- AB1-054：几何类型。

**§5（清单与生成表）**
- AB1-107（译）：§5.3"锁保护不明"的默认不适用于自带同步的成员。
- AB1-095（译）：新增 `mem_ctx_sources.tsv`、`exec_ctx_sources.tsv`；后者也覆盖 26 个 `LinkExecCtxGuard` 的父上下文。
- AB1-008：新增 `eval_sites.tsv`。
- AB1-097：第 17 类规模改为实际条数。
- AB1-003：第 4 类的判定口径。

**附录 A**
- A.1 D3：AB1-024。
- A.3：AB1-079（operator[] 的前提检查也 grep "invalid index"）、AB1-037（膨胀系数作用于全部栈常数）。
- A.4 接受的偏差：
  - 新增 `mem-accounting`（D2）、`obstring-null`（D3）、`log-content`（AB1-080）、`monotonic-wait`（AB1-082）；
  - 扩写 `oom-abort`（AB1-061）、`malloc-backend`（AB1-033）、`mem-view`（AB1-081，含 D4）、`query-mem-limit`（AB1-032）、`plan-mem`（D2）；
  - 不新增 `float-key-nan` 和 `crash-mode`。
- A.4 裁判改动：第 5 项改写（AB1-040）；AM-12 提的 `--sorted_result` 不采纳（D2）。

## 3. 只改 API 文档（在 API 修订那一步应用）

| 文档 | 行 | 要点 |
|---|---|---|
| 01 | AB1-083；另见 007、018、020、002、003 | 字节接口收 i64；SMART_VAR 的说法；11049 的路径；MemBudget 用 Relaxed；与 05、07、09 的名字对齐；提供 0.7(b) 的分块缓冲与 RowId（修订 D）；补金标准 |
| 02 | AB1-084、AB1-016；另见 107 | macOS 的 futex 截断照 BUG 复刻，lock() 遇 OB_TIMEOUT 重等标 FIXED；4012 清单补全；桶锁回滚标 FIXED；ObLatchIds 补模块级常量；不公开导出 portable_atomic；补 `ob_pthread_cond_timedwait_us` 的译法；validate 用 SeqCst；删私有时钟；§2.11 写明钩子只读 Arg |
| 03 | AB1-085 | walk_frame_pointers、pthread_kill 收紧；trace id 的 sscanf 语义；ObDITls 照 C++；`force-frame-pointers`；arch::pause 改 pub(crate)；测试改正 |
| 04 | AB1-086 | ob_backtrace 唯一归属；ob_macro_utils 归 04；名字对齐；errsim 脚本归 04；dup2、setlocale 收紧；send_error_packet 补金标准 |
| 05 | AB1-087、AB1-020；另见 110 | ObLinearHashMap 的分片计数；FixedHash2 的非空位图；ArcSwap 用 `load()`；ObLightyQueue 用票号；锁内回调交回旧值；§3.16 补标记；处置对齐；FixedHash2 的节点形态随 D7 |
| 06 | AB1-088；另见 108 | prelude 取并集；带种子的哈希；Slot 二次 bind 写明不可达；EN_CHECK_SORT_CMP 始终编译；加 stable_sort；`ObStringWriter` 承接就地改写的参数 |
| 07 | AB1-089、AB1-090、AB1-106；另见 109 | **API 形状要在第 3 步前定**：批量视图 `DatumBatch`、ObExecContext 的旁路表、`QueryArena<'q>`、ObExecContext 不共享、MemCtx 的来源、借用计划常量区、ObTempExprCtx 只缓存数据、不带 query ctx 的 `qa.scope`（修订 E、F、G、N、O）；`create_expr_op_ctx` 照 RULEBOOK 拆回两个函数（修订 A）；另有短路、ObSEArray、批次 arena 的名字、DataRef 的代号宽度、`EvalEnumSetFn` 加 arg0 |
| 08 | AB1-091、AB1-092 | MemtableSelf 持 Weak；NodeBatch 封口即消费；回调链表带 `list_id`、`linked` 标志，`state_` 私有；alloc_index 校验身份；SCN/TxSEQ 手写比较并补 G7；分配失败一律 4013；KV 退休加节流；写明内存序；pin 按链表×memtable；S18 改标 FIXED |
| 09 | AB1-009、AB1-093、AB1-094 | **系统变量 719 项**；z_stream 每次传入输入；AioRequest 的缓冲地址要稳定；CURL/vsag 的 Send；蹦床改 `unsafe extern "C"`；no_param_sql 绑在 `&self` 上；布局自检挪到构建期；`new_with` 用 HRTB（修订 I）；依赖列；生成文件清单；例子去掉 `?`；sed 按平台复刻；ParseNode 加 `has_children_array()`（修订 M） |

## 4. 裁判、清单与流程

**裁判**：AB1-040。SIGTERM/SIGUSR1 在现有裁判上观察不到（stop 先发 SIGTERM，20 秒后 SIGKILL，拿不到退出状态）。生命周期场景改为直接发信号、断言 pid 在时限内消失；改完照 A.4 用故意改坏的代码重新验证。AM-12 提的 `--sorted_result` 不采纳：裁判里没有调低工作区的用例。

**单元与生成表**：
- AB1-012：处置对齐，`units.tsv` 是唯一来源；
- AB1-102：没人认领的单元定主；
- AB1-098：types.tsv 的"共享"不能只凭"有原子成员"；
- AB1-099：前向声明的向上类型照 0.4 处理；
- AB1-100：依赖图脚本的正则。

**清单**：
- AB1-095：两张读点来源表，`exec_ctx_sources.tsv` 也覆盖 26 个 guard 构造点；
- AB1-096：**已更正**：c07、c08 用 README 现有的码，只补 c19 的 `SINGLE_OWNER_BOX_MOVE`（随 D7）和 c07 的 `OWN_SYNC`；
- AB1-097：第 17 类加 6 条；
- AB1-104：第 16 类待确认的死代码；
- AB1-111：已确认的第 7 行随 D6 机械更新；
- AB1-112：前 20 行证据层的附注（第 4、13、15、18 行）。

**1d 执行**：
- AB1-103：骨架层先行；跨模块 API 只写一处；被指派方须在 §8 回应；冻结前把 9 份文档 §2 拼成桩工作区跑一次 `cargo check`；
- AB1-023：测试工具链；
- AB1-075：lint；
- AB1-101：调查备忘的笔误。

**本批没有覆盖的 API 开放问题**：没有排修订，审查也没有表态，留给 API 冻结关口。

| 文档 | 问题 |
|---|---|
| 01 | Q4：共存期 jemalloc 怎么链接 |
| 02 | Q1：futex 字是否按缓存行对齐；Q8：ObLatchDesc 的函数级重映射 |
| 04 | Q13：IR 指针成员打印成 id；Q14：跟踪宏的归属；Q15：ObBaseLogWriter |
| 05 | Q4：1d 产出金标准的 C++ 驱动程序；Q5：ObLinearHashMap 的分片数 |
| 07 | 8.9：ObObjParamOwned |
| 08 | Q5：KV cache 值的字节布局；Q6：DDL keybtree 的键设计（回收域部分已在 AB1-042） |
| 09 | Q1：生成器怎么共存；Q3：query 签名里的 ParseNode；Q4：回调重入的核实；Q6：build_flags |

## 5. 前 20 行审查的结果与并入情况

**结果**：
- 20 行里 18 行 `confirmed`，2 行 `unknown`：第 9 行 `ObTransCtx::lock_`，问题是字段怎么划分，即 D5；第 19 行 `ObLockWaitNode`，问题是请求归谁所有，即 D7。
- 第 1 轮两名审查者共驳回 13 行（含证据缺陷）。提议者只修订了一次，第 2 轮两人对 13 行修订全部确认。
- 提议者在修订稿里认错：初稿说"README 没有 S3b、c07/c08 没有码"不属实。本批第一版的 AB1-096 照抄了这个说法，现已更正。

**并入**：新材料包括修订 A–P 16 项、K1 §4 14 项、K2 §4 10 项、两份第 2 轮附注（共 8 条）、修订后提议的 5 个交人问题。
- **11 行现有修订补充了内容或来源**：AB1-005（D5）、006（D6）、035、064、083、089、093、094、095、096、098；
- AB1-014 撤回了为 `create_expr_op_ctx` 登记的函数级重映射，AB1-047 改正了行号；
- **新增 7 行**：AB1-106…112，其中 AB1-110 是新的决定 D7。

| 来源 | 内容 | 去向 |
|---|---|---|
| 修订 A；K1-1；K2-10；K1 第 2 轮第 1 行 | api/07 合并 `create_expr_op_ctx` 两个重载、返回表槽借用，与 §2.1/§4 冲突 | **AB1-106（新）**：照 RULEBOOK 拆回两个函数，api/07 改；不加"并入"列 |
| 修订 B；K1-6；K2-2、K2-4；两人第 2 轮第 19 行 | 请求的所有权；c19 缺 Box 移动码 | **AB1-110（新，D7）**；码见 AB1-096 |
| 修订 C；K2-3 | 自带同步的成员、`after_unlock` 能读什么、`OWN_SYNC` | **AB1-107（新）** |
| 修订 D；K1-12；K2-5；K2 第 2 轮第 7/8 行 | ObResultSet 的生命周期类别；0.7(b) 的分块缓冲 | AB1-006（D6，改写）、AB1-083；**AB1-111（新，协调者要求的依赖行）** |
| 修订 E；K1-4 | ObTempExprCtx 缓存的形态 | AB1-089 |
| 修订 F；K1-10 | QueryArena 与 IR 节点带 `'q` | AB1-089（第一版已有） |
| 修订 G；K1-2；K2-8 | 不带 query ctx 的 `qa.scope` | AB1-089 |
| 修订 H；K1-9；K1 第 2 轮第 17 行 | 就地改写的 `ObString &` 参数 | **AB1-108（新）** |
| 修订 I | `ObPCParam` 两种形态；`new_with` 的 HRTB | AB1-093（HRTB）；AB1-112（`ObPCParam` 交它自己的 c03 行） |
| 修订 J；K1-14；K2-9；两人第 2 轮第 3 行 | §2.4 单列"登记活动子对象"；读点与 26 个 guard 的来源表 | AB1-095（扩写；含活会话、PL 嵌套 SQL 传调用方上下文、`Option<&mut>` 读点） |
| 修订 K；K1-13 | 规则 5 的重入名单 | AB1-064（第一版已有） |
| 修订 L；K1-3；K2-7 | eval_enumset_with_arg0 写不出来 | **AB1-109（新）** |
| 修订 M；K1-5、K1-8；K2-6 | ParseNode 缺 `children_` 是否为空；`ObNumber::format()` 定一种 | AB1-094；AB1-035（第一版已有） |
| 修订 N；K1-11 | ObExecContext 是否共享；`set_my_session` 的计数 | AB1-098（按证据定为不共享，不交人）；计数进 AB1-112 |
| 修订 O；交人问题第 16 行 | ObExecContext 旁路表的 API | AB1-089（第一版已有） |
| 修订 P | 第 15 行"ctx_ 字段必须是拥有型" | AB1-112 |
| K1-7；K2-1 | c07/c08 的码以 README 为准 | AB1-096（更正） |
| 交人问题第 3、8、9、19 行 | 来源表、ObResultSet、CtxLock、请求所有权 | AB1-095、D6、D5、D7 |

## 6. 不采纳的内容与理由

| 来源 | 不采纳 | 理由 |
|---|---|---|
| AM-26 | 从 §1 白名单删掉 slotmap | ObList 要用它（AB1-057）；墓碑方案照常采纳 |
| AM-12 | 裁判加 `--sorted_result` | 依据读反了：join_many_table.test:13 是把工作区调大到 100，裁判里没有调低工作区的用例。`--sorted_result` 只是弱化裁判，也没有可加的对象 |
| AM-10 | 把解析器的两个回调算作检查点 | 基线是恒成功的 weak 版本，`ob_memory_tracker_wrapper.cpp` 不参与构建；照做会新增 4013 |
| AM-07 | "clippy 的 let_underscore_must_use 会误伤" | 那是默认关闭的 restriction lint，`-D warnings` 不启用；`let _ =` 改用 grep（AB1-067） |
| AM-08 | 为 ObAtomicReference 补一行 | src 里没有使用者，记 delete |
| AM-66 | 改 RULEBOOK | 错名在调查备忘里，RULEBOOK 0.9 没有它；只改备忘 |
| AM-11、AM-22 | 原计数（83 行、145 处） | 实为 66 行、31 个文件，约 77 处实现；采纳规则，改计数 |
| AM-49 | 把 `sort_by` 的临时缓冲扩进 oom-abort | `sort_by` 对非全序比较器会 panic，改为移植 libc++ 的 stable_sort |
| AM-43 的备选 | BTreeMap 加 A.4 `float-key-nan` | `CppFloatMap` 能逐字节复刻，代价小 |
| INV@L10 | 嵌套类用"外层类作模块路径" | 模块与类型同在一个命名空间，会与类型本身或单元模块重名；改用 `外层_内层`（AB1-077） |
| INV@L5 的备选 | print_service_status 用 trait+槽位 | 只需要一个"槽位是否已绑定"的布尔钩子（AB1-025） |
| F20-P1 与本批第一版 AB1-096 | 给 README 补 c07、c08 的码 | README 早有这两张码表；只补 c19 的 Box 移动码和 c07 的 `OWN_SYNC` |
| 本批第一版 AB1-014、07 §2.10.2 | `create_expr_op_ctx` 合并重载并返回表槽借用，登记为函数级重映射 | 已签字的 §2.1/§4 能直接写出，且第 1 行已照 RULEBOOK 确认；改 api/07（AB1-106） |
| K2-10 | overloads.tsv 加"并入"列 | 照 RULEBOOK 写就不合并重载，用不着这一列 |
| K1-3 的另一法、K2-7 | 帧模块造"替换了参数的浅拷贝 RtExpr"；ObExecContext 加取出/放回 ExprOpCtx 的接口 | 两个分支都显式传 `arg0` 更简单、行为相同，不需要复制 RtExpr（AB1-109） |
| K1-11 | ObExecContext 是否共享"交人决定" | 证据足以判定：原子访问都在请求处理线程上；按 §2.13 定为不共享（AB1-098） |
| D6 第一版的 B | ObResultSet 带内存来源参数 | 0.7(b) 不许持有 `&Arena`，K1 已指出；改为"拆成两个类型" |
| K-09 | 现在就把 futex 垫片改为直调系统调用 | 性能项（唤醒往返多 5–15%），只在垫片内部，对齐之后凭 profile 再换，先记 PERF(port)（AB1-039） |
| 05 Q6 的推荐 | ObLightyQueue 用 ArrayQueue 加单个 FutexCond | 会全唤醒，size 与"满"的口径也与 C++ 不同；改为照 C++ 的票号算法（AB1-065） |
| 03 Q8 的推荐 | 每线程缓存全部 4 段扩展栈 | 常驻内存只增不减；只缓存 1 段（AB1-037） |
| fidelity 的备选 | A.4 新增 `crash-mode` | 用 `#![no_main]` 消除这处差异（AB1-027） |
| 09 Q5、Q2、Q7 的备选 | 解析器提供强定义的内存检查；几何类型整体 keep-cxx；拼音表照常翻译 | 分别会新增 4013、让 Rust 侧没有树类型可用、把 1,502 条数据交给逐条翻译 |
| B-10 的备选 | 统一成 GNU sed 的语义再记一条偏差 | 以该平台的基线产物为准，用不着偏差（AB1-051） |
