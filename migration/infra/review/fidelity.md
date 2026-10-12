# 对抗审查：行为一致性（基础设施 API 文档，1d）

> 审查范围只有一类问题：按这 9 份 API 文档写出来的代码，会不会在 MySQL 客户端或 00b 裁判能观察到的地方与 C++ 不同。
>
> 方法：
> - 输入：`migration/infra/README.md`、`migration/infra/api/01-mem.md` … `09-parser-sys-generators.md`、`migration/infra/amendments.tsv`（77 行），对照已签字的 `migration/RULEBOOK.md` v0.2（0.11 的逐字节面、A.4 的接受偏差），标准参照 `migration/design/review/behavioral-fidelity.md`。基线 `7d907abfa`。
> - 分工：01、03 由子审查完成，原文存档在本目录的 `fidelity-01-mem.md`、`fidelity-03-thread-signal.md`，本文重新编号吸收，并抽查了其中的关键事实；02、08 的子审查结果直接交回本审查者（没有单独存档），其中的中级结论本人回源码复核过；04、05、06、07、09 由本人通读、核对。
> - 复现：用原版二进制 `build_release/src/observer/seekdb` 经 `migration/judge/runner/judge.py try` 跑了 3 个小用例（作业临时目录 `review-fid/repro/`）；用基线工具链（`deps/3rd/.../clang++` 17.0.6、`MacOSX15.sdk`）和 rustc 1.98.1 做了几个探针：`std::map<double>` 的 NaN 键、Rust 与 C 的定精度浮点格式、BSD sed、edition 2021 的临时量寿命、macOS futex 超时截断；在临时目录重跑 `gen_errno.pl`，与入库的三个生成物逐字节比对。
> - 没有构建项目，没有 git 写操作，没有改动仓库里除本文件以外的任何东西。
> - 另一位审查者的 `consistency.md` 已覆盖的行为问题不重复，见第 7 节。

## 1. 结论

1. **文档里的 C++ 事实大体准确。** 共抽查 208 条 file:line：CONFIRMED 179、PARTIAL 17、WRONG 12。决定用户可见字节的机制都写对了，大多数还配了以 C++ 为准的金标准测试：
   - errno 的各张表（重跑 `gen_errno.pl` 的产物与入库文件逐字节相同）；
   - printf 的两种方言、TO_STRING 的五条路径、`send_error_packet` 的文本选择；
   - `ObHashMap` 的桶数、头插、遍历序，DCHash 序，`FixedHash2` 的唤醒序；
   - libc++ 19.1.2 的 introsort 与堆（相等时选左子）、`std::map<double>` 的 NaN 行为（本人用基线工具链复现）；
   - unis 序列化、`ObDatum` 的位布局；11049 的算法与报错文本（01 子审查逐行核对）。
2. **两条高级问题，都已用原版二进制复现：**
   - **B-01** RULEBOOK 0.6 和 07 让 `eval_args::<N>`"先全部求值"，并且"覆盖二元算子的 `args_[0]/args_[1]->eval`"。可 IFNULL、BETWEEN、AND/OR/COALESCE、位运算等 C++ 是短路求值。原版上 `ifnull(a, json_extract(concat('{', a), '$'))` 正常返回，照文档译出来会报 Invalid JSON text。
   - **B-02** 09 要生成 776 个系统变量。C++ 生成器先滤掉 57 个 placeholder，实际是 719 个。照文档生成，裁判 `minitest` 的 `show variables where Variable_name like 'ob%'` 会多出几行；`SELECT @@ob_bnl_join_cache_size` 会从报错变成成功。
3. **中级问题 5 条：**
   - 计账口径决定落盘点，从而决定并列行序（B-03、B-04）；拟议的 A.4 `mem-accounting` 依据读反了，而且与 D5 冲突；
   - edition 2021 里，`if let`/`match` 被匹配表达式中的锁 guard 会活到整条语句结束，可能自锁（B-05）；
   - 栈的余量常数不随帧膨胀系数缩放，扩展段也没有保护页（B-06）；
   - SCN、ObTxSEQ 的比较语义和编码没有规格，也没有测试（B-07）。
4. **A.4**：现有的接受偏差够用。新提议里，`mem-accounting` 必须收窄并作为 D5 的签字例外；`obstring-null` 的影响面写窄了（B-08）。另有约 15 处偏差既不在 A.4，也没标 `FIXED`/`BUG`（第 3 节）。
5. 共 25 条发现：**高 2、中 5、低 18**。另有缺失的规则 3 条（第 6 节）。

## 2. 发现

### B-01【高】`eval_args::<N>` 把 C++ 的短路求值改成全部求值

- **文档**：
  - 07:840-843：`eval_args::<N>`"先全部求值，再统一借出前 N 个参数的视图。覆盖 `eval_param_value(ctx, a, b, …)`（约 134 处）和二元算子的 `args_[0]/args_[1]->eval`"；
  - 07:1361："`eval_args::<N>` 同样求值全部参数，只是借出前 N 个"；
  - 07:854-857：`batch_eval_map::<N>` 对每一行借出 N 个参数，例 6（07:1516）用它译二元算术的批量函数；
  - RULEBOOK:292（0.6）的原文是"覆盖二元算子（306 处 `args_[i]->eval`）"；
  - 07 §5 第 8 条只测"求值顺序和遇错即停"。
- **C++ 的行为**：306 处 `args_[i]->eval` 里有一批是条件求值。
  - `ObExprIfNull::calc_ifnull_expr`（`ob_expr_ifnull.cpp:130-145`，注释是 "MySQL ifnull is short-circuiting"）：arg0 不为 NULL 就不求 arg1。
  - BETWEEN（`ob_expr_between.cpp:41-46`）：被比较的值为 NULL 时不求上下界。
  - 位运算（`ob_expr_operator.cpp:4281-4285`）：左边为 NULL 时不求右边。
  - AND、OR、COALESCE 的循环（`ob_expr_and.cpp:71-92`、`ob_expr_or.cpp:92-100`、`ob_expr_coalesce.cpp:101-110`）：遇到能决定结果的值就停。
  - 启发式扫描：`src/sql/engine/expr` 下 150 处 `args_[0]->eval` 中，有 26 处是先判断 NULL 或真假，再在 `else if` 里求 `args_[1]`，涉及数组函数、`from_unix_time`、`nullif`、`uuid` 等。
  - 无条件全部求值的典型是 `eval_param_value` 和 `get_arith_operand`（`ob_expr_operator.h:1633-1645`），以及两次 `eval` 之间没有任何判断的写法。07 的例 1 属于这一类，没有问题。
- **原版复现**（`judge.py try`，`review-fid/repro/fid_probe3.result`）：表 `t(a int, b int)` 有两行 `(1,NULL)`、`(2,NULL)`。
  - `select ifnull(a, json_extract(concat('{', a), '$'))` → 1、2；
  - `select ifnull(b, json_extract(concat('{', a), '$'))` → `ERROR 22032: Invalid JSON text in argument.`。这证明第二个参数一旦被求值就报错。
  - `coalesce(a, …)` → 1、2；`(a > 5) and (json_extract(...) = 1)` → 0、0；`b between 0 and json_extract(...)` → NULL、NULL。
  - 反例要排除：`ifnull(1, (多行子查询))` 不报错，是改写器（`remove_dummy_nvl`）做的；`coalesce(1, (多行子查询))` 在原版也报 1242，因为子查询先算了。这两条不能当证据。
- **按文档写出来的行为**：IFNULL、BETWEEN 这类二元、三元的写法会被译成 `let [a0, a1] = ctx.eval_args::<2>(e)?`，被跳过的参数也会被求值。
  - 上面四条在 Rust 里都会报 Invalid JSON text；
  - 第二个参数里的警告（如 CAST 截断）会多出来，`SHOW WARNINGS` 和 `@@warning_count` 随之不同；
  - 子查询、`@x :=` 赋值、`RAND()` 的副作用也会多出来。
- **用户或裁判在哪里看到**：任何 IFNULL、BETWEEN、AND、OR、COALESCE、NULLIF 或位运算，只要后面的参数在被跳过的行上会报错或告警，就能看到。裁判里有没有恰好依赖短路的语句，没有逐一核对。
- **建议改法**：
  1. 修订 RULEBOOK 0.6 和 07：`eval_args::<N>`、`batch_eval_map::<N>` 只用于 C++ 无条件求值全部参数的站点（`eval_param_value(...)`、`get_arith_operand`、中间没有判断的连续 `eval`）。C++ 里有条件的 `args_[i]->eval` 一律逐个写 `eval_arg(e, i)`，顺序和条件照抄。批量函数照 C++ 的 skip 位图逐个参数 `eval_batch`。
  2. 1c 用脚本给 306 处站点打"无条件/有条件"的标签，写进清单。
  3. 07 §5 第 8 条加金标准：用一个"求值即报错"的参数，测 IFNULL、BETWEEN、AND、OR、COALESCE、NULLIF、位运算，结果与 C++ 比对。

### B-02【高】系统变量生成器没写 placeholder 的过滤，也没写数组顺序

- **文档**：
  - 09:197"776 个变量"；
  - 09:1419：`ObSysVarClassType` 有"776 个"常量；
  - 09:1423：`pub static ObSysVars: [ObSysVarFromJson; 776]`；
  - §3.4（09:1769-1771）只说"逐字段照 JSON"，没提 placeholder，也没规定数组顺序。
- **C++ 的行为**：
  - `parse_json` 先过滤掉 `placeholder: true` 的项，再排序（`gen_ob_sys_variables.py:66-72`）。JSON 的 776 项里有 57 项是 placeholder，生成物只有 719 项（`ALL_SYS_VARS_COUNT = 99 + 620`，`ob_sys_var_meta.h:31-33`）。placeholder 的名字在 `ob_system_variable_init.cpp`、`ob_sys_var_class_type.h`、`ob_system_variable_factory.cpp` 里出现 0 次。
  - `ObSysVars` 按 id 排（`make_cpp_file` 用的是 `list_sorted_by_id`，`:1186`）。会话的存储下标就是它的下标（`ObSysVarsToIdxMap`，`ob_sys_var_meta.cpp:2236-2256`），会话变量的虚拟表都按这个下标顺序出行（`ob_information_session_variables_table.cpp:75`、`ob_session_variables.cpp:66`）。另有几张表按小写名字排（`:69`、`:691`、`:702`）。
  - 57 项 placeholder 里有 30 项不是 INVISIBLE，其中 17 项以 `ob` 开头，例如 `ob_interm_result_mem_limit`、`ob_bnl_join_cache_size`、`ob_org_cluster_id`、`ob_route_policy`、`ob_compatibility_control`、`ob_enable_rich_error_msg`。
- **原版复现**（`review-fid/repro/fid_probe.result`）：
  - `select @@ob_bnl_join_cache_size` → `ERROR HY000: Unknown system variable 'ob_bnl_join_cache_size'`；
  - `show variables like 'ob_bnl_join_cache_size'` 没有行；
  - `information_schema.session_variables` 里三个 placeholder 的 `count(*)` 为 0。
- **按文档写出来的行为**：
  - 生成 776 项，57 个变量凭空出现：`SELECT @@x`、`SET x = …` 由 1193 变成成功，`SHOW VARIABLES` 多出行。
  - 如果用 serde_json 缺省的 `BTreeMap` 顺序，数组按字节序排（`Ndb_system_name` 排第一），与 C++ 的 id 序不同。`SHOW VARIABLES` 自带 `ORDER BY`（`ob_show_resolver.cpp:2362-2371`），不受影响；不带 `ORDER BY` 的 `information_schema` 查询会变。
- **用户或裁判在哪里看到**：
  - 裁判 `cases/minitest.result:233-258`：`show variables where Variable_name like 'ob%'` 的 24 行里会多出最多 17 行（会话作用域的那部分），`ob_bnl_join_cache_size` 等；`:266` 起的 `show global variables where …` 同理；
  - 用户脚本里的 `SELECT @@…`、`SET …`。
- **建议改法**：
  - §2.5.3 和 §3.4 写明：先按 `gen_ob_sys_variables.py:66-68` 去掉 placeholder；`ObSysVars`、`ObSysVarClassType` 按 id 排，名字表按小写名字排（`:69-70`），与 C++ 生成物逐项相同；数组长度写成 `ALL_SYS_VARS_COUNT`（719）。
  - G5（09:1930）写明双向全表比对：Rust 多出或少了任何一项都失败。G5 如果按全表比对，本来能在 1d 抓住这个问题；但规格正文本身是错的。

### B-03【中】决定落盘和限额的计账口径与 C++ 不同；拟议的 A.4 `mem-accounting` 依据读反，而且与 D5 冲突

- **文档**：
  - 01:1379-1388（Q3，AM-12）：列出池按对象计费、容器缓冲不计入、`vec_idx_used_` 不再计数、结构体大小不同四项，承认"落盘时机……可能与 C++ 不同"。提议 A.4 新增 `mem-accounting`，裁判"对这两个用例检查行序是否稳定，必要时加 `--sorted_result`"，理由是"裁判有两个用例靠调低 `ob_sql_work_area_percentage` 强制落盘（`join_many_table.test:13`）"。
  - 05:1639（A4）和 05:1825（§3.16）把数组的容量增长写成"只影响 `get_capacity()` 的值和内存""不可观察"。
  - 08:893（S27）：memstore 用量的差别"不另设 A.4 项"。
- **C++ 的行为**：
  - 排序算子是否落盘由 `need_dump()` 决定：`get_data_size() > get_mem_bound() || mem_context_->used() >= get_max_bound()`（`ob_sort_op_impl.h:713-717`）。`mem_context_->used()` 包括经 `ModulePageAllocator(get_malloc_allocator())` 分配的数组（`ob_sort_op_impl.cpp:762-763`，再经 `:1027` 的 `update_used_mem_size` 上报），而数组按 `block_size_` 取整（`ob_array.h:365-393`，见 B-04）。
  - 落不落盘决定并列行的先后：不落盘是一次 introsort，落盘是分段排序再多路归并；哈希算子落盘后各分区的处理顺序也会变。D5（RULEBOOK 0.11.3）要求并列行序一致。
  - 裁判依据读反了：`join_many_table.test:13`、`join_many_table_single_field.test:12` 是把工作区调**大**到 100（缺省是 5，`ob_system_variable_init.json:1798-1801`），用例末尾（`:86`、`:100`）再恢复成 5。裁判里没有任何调低工作区的用例。这一点与 01 子审查原 F-04、`consistency.md` 对 AM-12 的判断相同。
  - memstore（08 子审查）：4030 和限流都按 memstore 用量判断（`ob_access_service.cpp:655-657`、`ob_memstore_freezer.cpp:1721-1737`），键按 `sizeof(ObStoreRowkey)` 加每个 16 字节的 ObObj 计（`ob_memtable.h:81-99`）。
- **按文档写出来的行为**：Rust 的 `used()` 漏了不计账的容器，多了 bumpalo 块尾的空闲，结构体大小也不同。所以落盘点、4030 和冻结的时机、query range 的退化点（01 原 F-01）、向量限额（B-11）都会与 C++ 不同。
- **用户或裁判在哪里看到**：
  - 用户：接近工作区上限的大排序和哈希连接，并列行的顺序、没有 ORDER BY 时哈希输出的顺序；大批量写入时 4030 出现的时机。
  - 裁判：看不到，因为没有落盘、11049、4030 的用例。
- **建议改法**：
  1. 凡是喂给落盘或限额判定的计账，一律按 C++ 的字节数记。从基线的 DWARF（构建带 `-g`）生成 `cxx_sizeof.tsv`（类型 → C++ 的 `sizeof`），计账点用它，不用 `size_of::<T>()`。05 的数组提供带 tracker、按 C++ 的增长和取整规则记账的存储（B-04）。
  2. 做不到的部分进 A.4 `mem-accounting`，条文点名"工作区落盘点、query range 退化、memstore 的冻结/限流/4030、向量限额"，并在 D5 旁注明"接近内存边界时不保证并列行序"，作为需要签字的例外。
  3. 删掉 AM-12 的裁判改动：`--sorted_result` 是弱化，而且没有可加的对象。05 的 A4、§3.16 和 08 的 S27 改为引用这一项。

### B-04【中】`ArenaVec` 和计账容器漏了 `ObSEArrayImpl::reserve` 的按块取整（原 01 F-01）

- **文档**：01:249"扩到 max(2×cap, cap+additional)，最少 1，与 ObArray 的增长相同（ob_array.h:610-614）"；S7（:819）、S3（:782）；T3（:1034）把增长序列写成"1、2、4、8…"并作为测试期望。
- **C++ 的行为**：
  - `reserve` 把字节数向上取整到 `block_size_` 的整数倍，容量取 `new_size / sizeof(T)`（`ob_array.h:365-393`，取整在 `:369-372`，本人复核）；
  - `block_size_` 缺省是 `OB_MALLOC_NORMAL_BLOCK_SIZE` = 7936（`:287`），并取 `max(sizeof(T), bs)`（`:594`）。所以 `ObArray<int64_t>` 第一次 push 就从块分配器拿 7936 字节，容量 992。
  - 反方向也有：用缺省 `ModulePageAllocator` 的 ObSEArray 走全局堆，不进任何 arena（`page_arena.h:116-131`）；按 RULEBOOK §2.5 改成 `ArenaVec` 后反而计入 arena。
- **按文档写出来的行为**：统计按 8、16、32… 字节记；01 自身也前后不一：§2.2 说 auto_free 数组由 `ArenaVec` 承载，§2.12（:752）又说走全局堆，C++ 实际绑定的是 `CURRENT_CONTEXT` 的 arena（`ob_array.h:84-91,484-497`）。
- **会改变的决策**：query range 的退化（KeyPart 的数组在 range arena 上，`ob_key_part.cpp:376-377,514`，`ObExprRangeConverter` 用 `allocator_.used() - mem_used_` 与 `range_optimizer_max_mem_size` 比较，`ob_expr_range_converter.cpp:111`）；排序落盘（B-03）。
- **用户或裁判在哪里看到**：只在接近内存边界的大查询上，用户看得到；裁判看不到。
- **建议改法**：`ArenaVec` 和带计账的容器照抄 `reserve` 的取整（构造时可传 `block_size`，缺省 7936），统计按取整后的字节记；`ModulePageAllocator(WORK_AREA)` 加 `set_allocator(malloc)` 的写法（`ob_sort_op_impl.cpp:562,582,761`）同时记两处：工作区预算记请求大小，上下文的 malloc 计数记 `usable_size(size + 32)`；清单第 11 类按 C++ 的块分配器归类；T3 改为由 C++ 产出的金标准。

### B-05【中】`if let`/`match` 被匹配表达式里取的锁 guard 活到整条语句结束，可能自锁（02 子审查）

- **文档**：02 §2.0（:93-102）让 `lock()/rdlock()/wrlock()` 返回可解引用的 guard；§6.2（:1283、:1287）只要求"绑定到具名变量，不得写 `let _ =`"，并示范 `if let Ok(g) = …`；§5.5 S2 和修订 A7 只防 `let _ =`。
- **C++ 的行为**：guard 的作用域是显式块，例如 `{ SpinRLockGuard g(lock_); v = map_.get(k); }`，出块就放锁。
- **按文档写出来的行为**：RULEBOOK 0.3 定的是 edition 2021。在这个版本里，`if let`、`match`、`while let` 被匹配表达式中产生的临时 guard 活到整个语句结束。探针 `review-fid/02/scrutinee.rs`（rustc 1.98.1，本人复跑）：`if let` 体内 `try_lock` 失败，`match` 分支内同样失败；换成普通 `if`，或经辅助函数取值，就能成功。`match self.x_.lock().state_ { … }`、`if let Some(v) = self.map_.rdlock().get(k)` 这类写法对译者很自然，分支里再锁同一把 latch 就自锁：`Latch`/`RwLatch` 会睡死，带截止时间的报 4012。
- **用户或裁判在哪里看到**：语句挂到超时（4012），或实例挂住；裁判单条语句有 10 秒超时，整个用例失败。
- **建议改法**：§6.2 加一条"加锁调用只能出现在 `let` 语句右侧或 `with_locked` 闭包里，不得出现在 `if let`/`match`/`while let` 的被匹配表达式中"；各 guard 类型标 `#[clippy::has_significant_drop]`，打开 `clippy::significant_drop_in_scrutinee`（deny）；CI 在 A7 的 grep 之外再查 `(match|if let|while let)[^{]*\.(lock|rdlock|wrlock|trylock|try_\w+lock|\w+_until)\(`。

### B-06【中】栈的余量常数不随帧膨胀系数缩放，扩展段没有保护页（原 03 B1）

- **文档**：03 §2.4（:240-245）的 `NATIVE_STACK_FACTOR` 只乘原生栈；§2.11（:840-847）常数写死；§3.11 第 6、7 条（:1129-1130）"段同样不加保护页"；§8 Q9。
- **C++ 的行为**：各余量按 C++ 的栈帧标定：`check_stack_overflow` 默认 32 KiB（`ob_common_utility.cpp:35`），`SMART_CALL` 64 KiB、`_LARGE` 192 KiB，每段 2 MiB − 34 KiB，累计上限 10 MiB（`ob_smart_call.h:28-36`，本人复核；`:31-35` 的注释明说 (256−64) KiB 是"那种布局上大致可用的跨度"）；扩展段不加保护页（`protected_stack_allocator.cpp:69-72,95-135`）。
- **按文档写出来的行为**：RULEBOOK A.3 本身假定 Rust 栈帧比 C++ 大 k 倍，却只放大原生栈。两个检查点之间的实际用量可能超过 32/64 KiB 的余量：在原生栈上撞保护页崩溃，在 psm 扩展段上越界写到相邻的堆块、静默损坏；靠扩展段能达到的最大深度缩到 C++ 的约 1/k，深嵌套 SQL 提前报 4019，解析器回调报 `OB_PARSER_ERR_SIZE_OVERFLOW`。
- **用户或裁判在哪里看到**：A.3 点名的三个用例（`cases/join_many_table`、`sqlancer_optimizer_regressions.test:19`、`subquery.optimizer_subquery_bug.test:297`）；崩溃会让用例余下的语句全部失败。
- **建议改法**：同一个系数同时乘到全部字节常数（三个余量、每段大小、累计上限、原生栈），段数仍是 4，日志里打印 C++ 的值；缓存的扩展段在第一次分配时 `mprotect` 一页保护页（之后复用）；第 5 步用栈涂色量两个检查点之间的最高水位。需要修订 RULEBOOK 0.8 和 A.3。

### B-07【中】SCN、ObTxSEQ 的比较语义和编码没有规格，也没有测试（08 子审查）

- **文档**：08 §2.1（:114-133）只写"逐行移植进 oblib（名字、方法名、序列化、TO_STRING 不变）"，比较规则只在备选方案里出现（:135）；第 5 节的 T1–T8、G1–G6 都不覆盖值类型。
- **C++ 的行为**（本人复核）：
  - SCN 的无效值是 `UINT64_MAX`（`scn.h:25,33`）；`<`、`<=`、`>`、`>=` 把无效值当成最小、按 `ts_ns_` 比较（`scn.cpp:328-386`），`==` 比原始 `val_`（`scn.h:107`）；`inc_update` 用 `<` 判断要不要推进（`scn.cpp:168-178`），所以初值无效的 SCN 第一次 `inc_update` 就会推进。
  - ObTxSEQ 的 `==` 比 `raw_val_`，`>`、`>=`、`<`、`<=` 只比 `get_seq()`（`src/data_plane/api/data_plane/transaction/ob_tx_seq.h:63-74`）。
  - SCN 有三种编码（定长 `encode_i64`、可变长、不做转换的定长反序列化），反序列化后还调 `transform_max_()` 把旧数据的 `INT64_MAX` 改成 `max_scn`（`scn.cpp:388-482`）。
- **按文档写出来的行为**：最顺手的 `#[derive(PartialOrd, Ord)]` 会把无效 SCN（`u64::MAX`）当成最大，初值无效的 `inc_update` 永不推进；TxSEQ 比较时把 branch 位也算进去。编码选错一种或漏掉 `transform_max_`，C++ 写的 clog、slog、事务表就读不对。
- **用户或裁判在哪里看到**：提交或回放的进度停住；并行 DML 的 ROLLBACK TO SAVEPOINT 回滚掉的节点集合不同；裁判的跨二进制场景（C++ 写、Rust 读，A.4 裁判改动第 1 项）。具体触发路径没有逐一核实。
- **建议改法**：2.1 写明 SCN、ObTxSEQ 手写 `PartialOrd`（覆写 `lt/le/gt/ge`，严格按 C++ 四个运算符的真值表），`PartialEq` 比原始位，禁止 derive `PartialOrd`/`Ord`、不实现 `Ord`；LSN、ObTransID 比原始值，可以 derive。加单测（无效、最小、最大、同 seq 不同 branch 的真值表，`inc_update` 从无效初值起步）和金标准 G7（四个值类型对这组值的 `serialize`、`fixed_serialize`、`deserialize`、`to_string` 字节，由 C++ 产出）。

### B-08【低】NULL 的、或不以 NUL 结尾的 ObString 进入 `%s` 时，输出字节与 C++ 不同；`obstring-null` 的影响面写窄了

- **文档**：04:1441"实参是切片时也在切片末尾停（C 会越过末尾读到 NUL 为止，那是越界）"；04:1455 自己写了 macOS 上 `%s` 遇空指针打印 `(null)`；06:176 `ObStringWriter::new()`"没有缓冲，写入一律返回 0"；06:235 `ObSqlString::ptr()`"未分配过时是空切片"；06:803（S-STR-2）与 06:1309（Q1，AM-45）把 NULL 当空串，称其余差别"只影响 `case_compare` 一类比较"，提议 A.4 `obstring-null`。
- **C++ 的行为**：
  - `ob_resolver_utils.cpp:152-156`：`ObString err; err.write(...); err.write(...); LOG_USER_ERROR(OB_NOT_SUPPORTED, err.ptr());`。默认构造的 ObString 没有缓冲，`write` 什么也不写（`ob_string.h:134-147`），`err.ptr()` 是 NULL，消息格式是 `"%s not supported"`（`ob_errno.def:77`），用户看到的是 `(null) not supported`。
  - `ObSqlString::ptr()` 在从未分配时返回 NULL（`ob_sql_string.h:67`），交给 `%s` 同样打印 `(null)`。
  - `ob_expr_tokenize.cpp:189`：`message.append_fmt("config: %s", str.ptr())`，`str` 是 JSON 对象的键，不以 NUL 结尾；C++ 读到下一个 NUL 为止，读到的是同一块缓冲里的后续字节，不一定越出分配，结果确定但不是键本身（后续字节是什么没有核实）。
  - 粗扫：`LOG_USER_*` 里直接把 `.ptr()` 交给 `%s` 的约 30 处。
  - 01 原 F-06 是同一类：`ob_write_string` 在源为 NULL、长度不大于 0 或分配失败时写 `dst.assign(NULL, 0)`（`ob_string.h:811-815`），`try_str` 返回 `Ok(&[])`，放在 AM-45 提议的 `Option<ObStr>` 位置上会把 NULL 变成 `Some(空串)`；C++ 区分两者的点如 `ob_core_table_proxy.cpp:855-866`（NULL 拼成 SQL 的 NULL，空串拼成 `''`）。这条与 `consistency.md` C-15(15) 相同。
- **按文档写出来的行为**：`&[u8]` 表达不了 NULL，Rust 打印 ` not supported`；不以 NUL 结尾的键只打到切片末尾。
- **用户或裁判在哪里看到**：上面这类报错文本（裁判没有覆盖）；内部表里 NULL 与 `''` 之别。
- **建议改法**：1c 第 4 类除了"显式判断 NULL"的成员和参数，再把"可能为 NULL、又流进 `%s` 或 SQL 拼接"的 ObString 标成 `Option`，`ObPrintfStr for Option<&[u8]>` 照 C 打印 `(null)`；不以 NUL 结尾的 `%s` 实参逐处判定：读到的字节在同一分配内且确定的，照 C++ 传入延伸后的切片并标 `BUG(port)`，否则标 `FIXED(port)`。04:1441 的"那是越界"改为"多数是越界"。AM-45 的影响面改写，`try_str_opt` 与 06-A3 一起定。

### B-09【低】`snprintf!` 丢掉了 C 的 `n` 参数

- **文档**：04:573-575：`snprintf(buf, n, fmt, …) / sprintf(buf, fmt, …) → snprintf!(buf, "fmt", …)`，宏体是 `vsnprintf(&mut $buf[..], …)`，用整个缓冲的长度；AM-37（04 修订 #7）同样写法，`consistency.md` 只核对了返回值的事实。
- **C++ 的行为**：有站点故意传比缓冲小的 `n`：`ob_ctype_uca.cc:1338`（`errsize - 1`，排序规则的错误文本）、`ob_trans_factory.h:64`、`ob_time_wheel.cpp:108,370`（`sizeof - 1`）、`ob_server_runtime.cpp:220`、`ob_all_virtual_tmp_file.cpp:163`（固定数字）。
- **按文档写出来的行为**：译者照规则丢掉 `n`，截断点后移一个或几个字节。
- **用户或裁判在哪里看到**：上面几处多是日志和内部名字；`ob_ctype_uca.cc` 的错误文本只在加载排序规则定义时出现。
- **建议改法**：规则改为 `snprintf(buf, n, …) → snprintf!(buf[..n], …)`；`n` 大于缓冲（C++ 越界写）时取缓冲长度并标 `FIXED(port)`。

### B-10【低】macOS 基线上 `gen_parser.sh` 的 sed 没有生效，09 却要求逐条复刻

- **文档**：09:1685"sed 用 Rust 实现，逐条复刻 sed 的范围语义……第 4 条改变了 OOM 时 `parse_init` 的返回值……必须保留"；09:1884（P1）要求 build.rs 的产物在 macOS arm64 和 Linux x86-64 上都与 `gen_parser.sh` 逐字节相同。
- **C++ 的行为**：
  - macOS 上 `sed_inplace` 是 `/usr/bin/sed -i ''`（`gen_parser.sh:18-27`），devtools/bin 里没有 sed；脚本不检查 sed 的退出码。
  - BSD sed 拒绝 `{/re/d}`、`{s/…/…/g}` 这种不带分号的块：探针 `review-fid/09b/` 实测报 `extra characters at the end of d command`、`bad flag in substitute command: '}'`，文件不变。
  - 入库的生成物证实：`src/sql/parser/sql_parser_mysql_mode_lex.c:73339-73354` 仍有 `int i;`、for 循环和 `buf[i] = yybytes[i]`，`:73578-73618` 仍是 `return 1`；不带花括号的那条（fts 的删行）生效了。
  - 所以基线的 `yylex_init_extra` 在分配失败时返回 1，`parse_init`（`sql_parser_base.c:45-56`）报 `OB_PARSER_ERR_PARSE_SQL`，而不是 `OB_PARSER_ERR_NO_MEMORY`。Linux 上 GNU sed 生效，两个平台的 C++ 本来就不同。
- **按文档写出来的行为**：Rust 按 GNU 语义复刻，macOS 上生成物与基线不同，P1 做不到；扫描器初始化分配失败时，报错从 1064 变成 4013。
- **用户或裁判在哪里看到**：只在解析器初始化分配失败时。
- **建议改法**：§3.1.1 写明"按平台复刻 C++ 的实际结果：macOS 跳过三条带花括号的 sed（BSD sed 失败），Linux 全部执行"，标 `PARITY(port)`；P1 改为"与该平台 `gen_parser.sh` 的实际产物逐字节相同"。或者交人决定统一成 GNU 语义，记一条偏差。

### B-11【低】`ObMallocHookAttrGuard` 并非"jemalloc 下不影响任何分配"：VIB 标签决定 CRoaring 的内存是否计入向量限额（原 01 F-02）

- **文档**：01 §2.12（:743）"删掉整行……属性只在 obmalloc 的 hook 里读"；E13（:1292）。
- **C++ 的行为**：`roaring_malloc/roaring_free` 读 `get_tl_mem_attr()`，标签以 "VIB" 开头时把分配记进 `vec_idx_used_`（`ob_rb_memory_mgr.cpp:39-50,77-89`）；钩子在 server 初始化时无条件安装（`ob_server.cpp:2341`）；适配器里约 20 处 `ObMallocHookAttrGuard(ObMemAttr("VIBitmap…"))`；`ObVectorAllocator::hold()` = `all_used_mem_ + vec_idx_used_`（`ob_vector_allocator.cpp:29-45`），`ObVectorMemContext::alloc` 据此卡限额（`:135-146`）。
- **按文档写出来的行为**：删掉 guard、CRoaring 又换成 roaring crate，`vec_idx_used_` 恒为 0，限额更晚触发，`V$OB_VECTOR_MEMORY` 的数值也不同。
- **用户或裁判在哪里看到**：调小 `vector_memory_limit` 或大索引逼近限额时，C++ 分配失败（日志 -7603），Rust 成功；客户端最终收到的错误码没有核实。裁判的 18 个向量用例都设为 0（即 50% 内存），碰不到。
- **建议改法**：改正理由；保留"向量位图作用域"的线程局部标志，位图包装在作用域内把增量记进向量预算，或者在 B-03 的 `mem-accounting` 里点名这一项。

### B-12【低】`WITH_CONTEXT`、`CREATE_WITH_TEMP_CONTEXT` 的译法在错误路径上与 C++ 不同（原 01 F-03）

- **文档**：01 §2.12（:749-750）、E5（:1147-1154）、E8（:1198-1203）。
- **C++ 的行为**：`CONTEXT_P` 展开成 `for (_S _s{…}; OB_SUCC(ret) && _s.i_-- > 0; _s.i_--) if (OB_SUCC(_s.get_ret()))`（`context.h:64-66`）：进入时 `ret` 已失败，body 不执行；`WITH` 遇到空 ctx 只把宏内部的 `_s.ret_` 设成 `OB_INVALID_ARGUMENT`（`:806-820`），调用方的 `ret` 不变，body 被跳过。
- **按文档写出来的行为**：`WITH_CONTEXT` 遇空 ctx 写 `ret = OB_INVALID_ARGUMENT`（-4002）；`CREATE_WITH_TEMP_CONTEXT` 译成无条件执行的块。`ob_sql_utils.cpp:510-516` 在 `init_datum_param_store` 失败后进入这个宏，C++ 跳过 body、返回原错误，按文档会执行 body，`ret` 可能被覆盖；`ob_physical_plan.cpp:268,298`、`ob_inner_sql_result.cpp:189,215` 在上下文为空时 C++ 返回成功，Rust 返回 -4002。
- **用户或裁判在哪里看到**：只在错误路径上，客户端收到的错误码不同。
- **建议改法**：两个宏都译成 `if ret.is_ok() { … }`；空 ctx 时跳过 body、不改 `ret`。

### B-13【低】`SMART_VAR` 的两条说法不对（原 01 F-05）

- **文档**：S21（01:977）"数组形式 C++ 不清零"；（01:978）errsim 只在 `cfg(feature = "errsim")` 下、只对 `heap_var!` 和大于 128 KB 的对象生效。
- **C++ 的行为**：用 `new (ptr) T{args}`、`new (buf) T{args}`（`ob_smart_var.h:175,178`），参数为空时是值初始化，基线 clang 17 探针显示两条路径都是全零；`ERRSIM_POINT_DEF` 在 release 里也生效（04 修订 #2，AM-32），并覆盖所有走堆的 SMART_VAR，包括因栈余量不足而走堆的（`ob_smart_var.h:40-66`）。
- **按文档写出来的行为**：清零没有行为差异，只是事实写错；errsim 的门控让 `ALTER SYSTEM SET_TP` 注入的效果不同（与 `consistency.md` C-19 同一问题）。
- **建议改法**：改正 S21 的描述；errsim 点始终编译，照 AM-32。

### B-14【低】`MemCtx` 的口径与"与 C++ 相同"不符（原 01 F-07）

- **C++ 的行为**：`create_context` 用父上下文的 freeable 分配器分配子上下文的结构体（`context.h:528`），父的 hold、tree_mem_hold 都包含它；`MemoryContext::metadata_size()`（`context.cpp:65-68`）被计划缓存的计账直接加上（`ob_plan_cache.cpp:1665,1673`）。
- **按文档写出来的行为**：子 MemCtx 由拥有者持有，不计入父；没有 `metadata_size` 的对应物，译者只能自己编一个值。
- **用户或裁判在哪里看到**：11049（已在 A.4 `query-mem-limit` 名下）；计划缓存的淘汰时机（A.4 `plan-mem` 只写了"视图数值归一化"）。
- **建议改法**：S11 改为承认这处差异；给出常量 `METADATA_SIZE`，取基线的 `sizeof`，由金标准测出；`plan-mem` 的条文补上"淘汰时机可能不同"。

### B-15【低】11049 只保护首条语句，显式传参时要钉住这条路径（原 01 F-08）

- **C++ 的行为**：非重试、且是多语句里第 1 条的语句，直接在 worker 的上下文里执行（`obmp_query.cpp:399-415`）；重试和后续语句走 `process_with_tmp_context` 新建的根上下文（`:417,461-489`），不在 tracker 的树里。
- **按文档写出来的行为**：E5 只示范了重试路径，Q2 的 `mem_ctx_sources.tsv` 没有规定首条语句的 MemCtx 来源。一律传新的 MemCtx，11049 就永远不触发；一律传 worker 的，重试也会触发。
- **建议改法**：S12 写明两条路径，把 `obmp_query.cpp:410` 这一路钉到 worker 的 MemCtx，加一个单测。

### B-16【低】`usize` 形参和 `try_bytes_zeroed` 的用途说明会改变 0 和负数的语义（原 01 F-09）

- **C++ 的行为**：`ob_malloc` 和 arena 的 `alloc` 在 `len <= 0` 时返回 NULL，调用方报 4013；`reserve(n)` 在 `n` 不大于当前容量（含负数）时是成功的空操作（`ob_array.h:368`）。
- **按文档写出来的行为**：`try_bytes_zeroed(len: usize)` 被列为"ob_malloc + MEMSET"的译法（01 §2.3 :295），`len = 0` 时成功；`try_vec`、`ArenaVec::try_reserve` 收 `usize`，`i64` 经 `as usize` 后负数变成巨大的请求，报 4013。
- **建议改法**：原始字节接口收 `i64`，照 C++ 判 `<= 0`；`reserve` 一类收 `i64` 的绝对容量；删掉"ob_malloc + MEMSET"这条用途说明。

### B-17【低】"SIGTERM → 退出状态 137，生命周期场景会测到"不成立（原 03 B2）

- **文档**：03:1140、第 43 条测试（:1256）；RULEBOOK 0.8 同样的说法。
- **裁判的事实**：`lifecycle.py:91-92` 的 `stop` 调 `sdb.py stop`；`terminate_pid` 先发 SIGTERM，等 `STOP_TIMEOUT = 20` 秒（`sdb.py:21`，本人复核），没退出就发 SIGKILL；退出状态拿不到（实例不是它的子进程）。
- **后果**：Rust 版忽略 SIGTERM 或做优雅停机，也会在 20 秒后被杀，裁判照样通过；A.4 裁判改动第 5 项（SIGUSR1 探针）的"以状态 0 退出"同样观察不到。用户看得到差别：`kill <pid>` 是立即死亡、优雅停机还是没反应。
- **建议改法**：生命周期场景直接发 SIGTERM，断言约 2 秒内 pid 消失；要看退出状态，让实例在一个会 `waitpid` 并把状态写进文件的小包装程序下启动，记进 A.4 的裁判改动清单；改正 RULEBOOK 0.8 的"会测到"。

### B-18【低】trace id 的文本解析是用户可见面，规格和测试漏了 sscanf 的边界（原 03 B3）

- **C++ 的行为**：`ob_trace_id.h:158-176` 用 `"Y%llX-%016llX-%llx-%llx"`（`:30-31`）做 `sscanf`；用户入口有 `DECODE_TRACE_ID(str)`（`ob_expr_decode_trace_id.h:41-51`，传的是不以 NUL 结尾的 `ptr()`）、`ALTER SYSTEM CANCEL TASK`（`ob_alter_system_executor.cpp:418-440`）、DDL 任务重启后读回 trace id（`ob_ddl_task.cpp:3143`）。基线工具链实测：`"Y"` 返回 −1（报 `OB_INVALID_ARGUMENT`）；`"Yz"` 返回 0（算成功，全零）；`"Y-1"` 得 `FFFFFFFFFFFFFFFF`；前导空白、`0x`、`+` 都接受；第二段宽度上限 16；上溢饱和；尾部垃圾忽略。
- **按文档写出来的行为**：文档只写"匹配项数既不是 0 也不是 4 时报错"，手写的 `&[u8]` 解析器很可能把 EOF 当 0、拒绝空白和符号、不守宽度 16，`DECODE_TRACE_ID` 和 `CANCEL TASK` 的结果就不同；越过 ObString 末尾读到 NUL 的行为没有标注。
- **建议改法**：规格写成"本格式的 vsscanf 语义（EOF 返回 −1；跳过空白；可选符号和 `0x`；第二段宽度 16；按 strtoull 饱和；`%X` 截成 u32；遇 NUL 或切片末尾即停）"，标 `FIXED(port): 越过 ObString 末尾的读`；由 C++ 产出这组输入的金标准。

### B-19【低】`ObDITls` 构造期间递归的描述与源码不符（原 03 B4）

- **C++ 的行为**：`ob_di_tls.h:101-113` 的占位值让 `is_valid()` 为假，递归调用会再次进入 `!is_valid()` 分支，嵌套 `OB_NEW` 出第二个 T 并返回它；外层构造完成后覆盖 `instance_`，内层那个泄漏。0x1 不会被返回，也没有未定义行为。
- **按文档写出来的行为**：03:1117 改成"返回 None 并记 ERROR"，是没有登记的行为改动，不符合 `FIXED(port)` 的条件。目前没有已知会递归取自身的 T，用户不可见。
- **建议改法**：改正描述；照 C++ 复刻，或标 `DIVERGE` 并进 A.4。

### B-20【低】Rust std 在 `main` 之前装的信号处置会造成 A.4 之外的偏差（原 03 B5）

- **实测**（rustc 1.98.1，macOS arm64）：普通 `fn main` 下 SIGPIPE 是 `SIG_IGN`，SIGSEGV、SIGBUS 装了 std 的处理函数，每个线程有 128 KiB 的 `sigaltstack`；`#![no_main]` 加 C 的 `main` 时三者都是默认，与 C++ 相同。
- **C++ 的行为**：SIGPIPE 只是被屏蔽、处置是默认，由信号线程记 INFO "received signal"（`ob_signal_handle.cpp:124`）；`install_ob_signal_handler` 是死代码，真的栈溢出时是 SIGSEGV（退出状态 139）并生成 core。
- **按文档写出来的行为**：03:1138 称"与 C++ 等价"；实际撞保护页时打印 "has overflowed its stack" 后 abort（134），SIGPIPE 的 INFO 日志消失。只在崩溃时用户可见。
- **建议改法**：生成的 `seekdb.rs` 用 `#![no_main]` 加 `#[no_mangle] extern "C" fn main(argc, argv)`；不采用就在 A.4 新增 `crash-mode`。

### B-21【低】macOS 上 futex 超时只取低 32 位，文档前后矛盾，不可失败的 `lock()` 没写怎么处理（02 子审查）

- **C++ 的行为**（本人复核）：`ob_futex.cpp:63-77` 把剩余时间截成 `uint32_t` 微秒交给 `__ulock_wait`，0 表示不限时；`ObLatchMutex::wait`、`ObLatchWaitQueue::wait` 收到 ETIMEDOUT 就返回 `OB_TIMEOUT`（`ob_latch.cpp:150-157,258-264,286-288`）。所以基线在 macOS 上"不限时"的加锁，等满约 71.6 分钟的某个余数就返回 `OB_TIMEOUT`；`ObLatchMutexGuard`/`ObLockGuard` 只记一条 ERROR，然后不持锁执行临界区（`ob_latch.h:267-281`、`ob_lock_guard.h:50-67`）。
- **文档的问题**：§3.9 F5 要求照搬截断；§2.0 的 `lock()` 是"截止 INT64_MAX、不可失败"，没说底层返回 `OB_TIMEOUT` 时怎么办；§1.4（:84）"永不超时"在 macOS 上不成立；§2.9（:623）说 `i64::MAX` 视为不限时，与 F5 矛盾。
- **用户可见性**：只在 macOS 上长时间等锁时出现，裁判看不到；有限截止时间（裁判常设 `ob_query_timeout` 为 1e12 或更大）下，DDL 串行锁会按"剩余时间 mod 2^32"提前报 4012，概率很小。
- **建议改法**：有限截止时间照 F5 复刻截断，标 `BUG(port)`；不可失败的 `lock()` 遇 `OB_TIMEOUT` 重新等待，标 `FIXED(port)`（C++ 不持锁执行临界区是数据竞争）；改正 :84、:623 的措辞；用伪时钟加单测。

### B-22【低】02 §1.4 列出的"用户可见的 4012"不全（02 子审查）

- 漏了 `ALTER SYSTEM SET` 的配置锁：`set_config_lock_.wrlock(ObLatchIds::CONFIG_LOCK, THIS_WORKER.get_timeout_ts())`（`ob_local_management_service.cpp:2312`，ObLatch，READ_PREFER），以及 `ob_tablet_autoincrement_service.cpp:330` 的 `ObMutexGuardWithTimeout`。API（`wrlock_until`、`lock_until`）本身能覆盖，问题是文档宣称只有"两个"，译者可能因此忽略这两条路径。
- **建议改法**：§1.4 改成 grep 得到的清单，并给 `admin_set_config` 补一个例子。

### B-23【低】02 有几处偏差既不在 A.4，也没标 `FIXED`/`BUG`，也没排修订（02 子审查）

- Q2：`Monitor` 改用单调时钟，C++ 在 Linux 上按 `gettimeofday` 算绝对截止时间（`ob_thread_cond.cpp:113-141`）；文档写了"建议进 A.4"，`amendments.tsv` 没有对应行。
- K6：C++ 多桶加锁失败时，`for` 的 `++next_lock_idx` 照样执行，回滚循环因此也解开那把加锁失败的 latch（`ob_bucket_lock.cpp:366-386`，本人复核）；文档写成"释放已拿到的"，等于悄悄修掉，没有标 `FIXED(port)`。
- Q4/K7：destroy 之后以不同桶数再 init，Rust 返回 `OB_INIT_TWICE`，C++ 可以（`ob_bucket_lock.cpp:84-97`）。
- 以上都不可见。**建议改法**：Q2 排修订（A.4 `monotonic-wait`，裁判无需处理）；K6 标 `FIXED(port)`；Q4 改成 `debug_assert` 加 `TODO(port)`。

### B-24【低】`NodeBatch::remaining()` 的口径没定，与"链入过的槽位不复用"一起用，最后几行可能越过批尾（08 子审查）

- **C++ 的行为**：批的大小按剩余行数精确分配（`ob_memtable.cpp:2508-2510`）；`pos` 只在成功时前移（`:2577`）；`insert_rows_with_fetch_dup` 的批量路径下主键重复后 `ret` 复位、继续下一行（`:2537-2545`），被 `mvcc_undo` 摘掉的节点所在槽位由下一行原地复用（`ob_mvcc_row.cpp:1036-1051,1088-1098`）。
- **按文档写出来的行为**：08 S8（:868）说链入过的槽位跳过、在下一次 `init_tx_node` 时前移，没说 `remaining()` 是否已扣掉这个槽；连续两行以上主键重复、行长不同时可能越过批尾，文档没规定此时返回什么。C++ 正常执行。
- **用户或裁判在哪里看到**：多行的 `INSERT … ON DUPLICATE KEY UPDATE`、`REPLACE`、`INSERT IGNORE`；裁判里这类用例是否走到多行批量路径，没有核实。
- **建议改法**：规定 `remaining()` 返回跳过待跳过槽之后的剩余量，或空间不够时 `init_tx_node` 返回专用错误码、调用方重新 `alloc_node_batch`；T1 加"连续两行主键重复、行长递增"的用例。

### B-25【低】08 S23"已冻结 → OB_EAGAIN"会误导：C++ 不把它往外传（08 子审查）

- **C++ 的行为**（本人复核）：`ObMemstoreAllocator::alloc` 的 `ret = OB_EAGAIN` 只是局部变量，函数返回 `void*`，已冻结和已满都返回 NULL（`ob_memstore_freezer.cpp:1659-1703`），`dup_data` 据此一律报 4013（`ob_memtable.h:126-129`）。
- **按文档写出来的行为**：内部分配函数若返回 `ObResult` 并透传 `OB_EAGAIN`，storage 里按 `OB_EAGAIN` 重试的循环会改变行为。
- **建议改法**：写明内部分配返回 `Option`，对外的分配接口失败一律 `OB_ALLOCATE_MEMORY_FAILED`。

## 3. A.4 提议与未登记偏差的评估

**新提议的 A.4 项**：

| 提议 | 出处 | 用户可见？ | 结论 |
|---|---|---|---|
| `mem-accounting` | 01 Q3、AM-12 | 是：落盘下的并列行序、哈希输出序、4030、query range、向量限额；裁判看不到 | 需要，但先按 B-03、B-04 把能对齐的对齐，剩下的作为 D5 的签字例外；删掉裁判改动 |
| memstore 计量（S27 写"不另设 A.4 项"） | 08 S27 | 是：4030、冻结、限流的时机 | 必须登记，并入 `mem-accounting`（B-03） |
| `log-content` | 04 Q8、AM-38 | 否，只影响日志 | 需要；02 的 5 秒日志、03 的任务类型名也依赖它 |
| `obstring-null` | 06 Q1、AM-45 | 是：`%s` 打印 `(null)`、内部表的 NULL 与 `''` | 按现在的口径不能接受，先做 B-08 的标注 |
| `float-key-nan`（06 Q2 的备选） | AM-43 | 是：MAP() 含 NaN 键时 | 不需要：推荐的 `CppFloatMap` 已能复刻（本人用基线工具链确认：NaN 先插时吞掉全部键，后插时覆盖当时根节点的值，±0 保留先插的） |
| `oom-abort` 扩到 `c_*` 和 `sort_by` 的缓冲 | 06 Q5、AM-49 | 只在 OOM 时 | 可以接受 |
| `malloc-backend` 扩到 Windows/Android | 01 Q5、AM-13 | 否，裁判只在 macOS/Linux 上跑 | 可以接受 |
| `mem-view` 扩到 KV cache 的碎片搬迁 | 08 S55、AM-69 | 否 | 可以接受 |
| `monotonic-wait` | 02 Q2（未排修订） | 只在墙上时钟跳变时 | 可以接受，补排修订（B-23） |
| `crash-mode`（不用 `no_main` 时） | 03 原 B5 | 只在崩溃时 | 推荐用 `#![no_main]` 消除，不新增 |
| 解析器内存检查的强定义（09 Q5 的备选） | 09 Q5 | 是：新增 4013 | 不需要：基线是 weak 空实现（`consistency.md` C-05） |

**用了 A.4 之外的偏差、又没标 `FIXED`/`BUG`/`DIVERGE` 的地方**（按 README"与 C++ 不同只能是 A.4 或 FIXED(port)"）：

- 05 §3.16：数组容量增长（标"不可观察"，见 B-03、B-04）；`ObDList::clear` 释放节点（C++ 只重置表头，D8）；`ObLightyQueue::size()` 不为负、"满"按实际元素数（Q2）；`ObLinearHashMap` 改成分片表（迭代序不进入输出，可以接受）。后两项不可见，补标即可。
- 02：K6、Q4/K7、Q2（B-23）。
- 03：`ObDITls` 递归返回 None（B-19）；QoS 只在线程内设置（只影响调度）。
- 01：`WITH_CONTEXT` 的错误码（B-12）；`ObVsagSearchAlloc::Deallocate` 真正释放（S20，只影响内存）。
- 06：`Slot` 第二次 `bind` 返回 `OB_INIT_TWICE`，C++ 是覆盖（S-SLOT-1）；正常路径只绑一次，不可见，应标出。
- 08：S11、S17、S18、S41 标的是"新增保护"，不是 A.4，也不是 `FIXED(port)`；其中 S18 对应 C++ 真实存在的释放后使用路径（服务未运行时 `remove_callback_for_uncommited_txn` 返回成功，`ob_trans_service.cpp:342-343`），应改标 `FIXED(port)`。

## 4. 引用抽查

共 208 条：**CONFIRMED 179、PARTIAL 17、WRONG 12**。

| 文档 | 抽查 | CONFIRMED | PARTIAL | WRONG | 核对者 |
|---|---|---|---|---|---|
| 01 | 43 | 35 | 3 | 5 | 子审查（原表在 `fidelity-01-mem.md`；本人复核 `ob_array.h:365-393`、`:287`、`:594`） |
| 02 | 33 | 30 | 3 | 0 | 子审查（本人复核 `ob_futex.cpp:63-77`、`ob_bucket_lock.cpp:355-386`、探针） |
| 03 | 31 | 26 | 3 | 2 | 子审查（原表在 `fidelity-03-thread-signal.md`；本人复核 `ob_smart_call.h:28-36`、`ob_common_utility.cpp:35`、`sdb.py:21`） |
| 04 | 19 | 18 | 1 | 0 | 本人 |
| 05 | 14 | 13 | 0 | 1 | 本人 |
| 06 | 16 | 14 | 2 | 0 | 本人 |
| 07 | 8 | 6 | 1 | 1 | 本人 |
| 08 | 34 | 31 | 3 | 0 | 子审查（本人复核 `scn.cpp:328-386`、`ob_tx_seq.h:63-74`、`ob_memstore_freezer.cpp:1659-1703`、`ob_kv_storecache.cpp:219-223`） |
| 09 | 10 | 6 | 1 | 3 | 本人 |

**01（WRONG、PARTIAL 全部列出；CONFIRMED 的 35 条见存档原表）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 01:750 | WITH 空 ctx 时设 ret=-4002 并跳过 body | `context.h:64-66,806-820` | WRONG | 调用方的 ret 不变（B-12） |
| 01:249、:819 | 增长"与 ObArray 相同" | `ob_array.h:365-393,287,594` | WRONG | 按 block_size 取整（B-04） |
| 01:977 | 数组形式 C++ 不清零 | `ob_smart_var.h:175,178` | WRONG | 值初始化，探针全零（B-13） |
| 01:743 | 属性只在 obmalloc 的 hook 里读 | `ob_rb_memory_mgr.cpp:39-50,77-89`；`ob_server.cpp:2341` | WRONG | VIB 标签计入向量限额（B-11） |
| 01:1385 | `join_many_table.test:13` 调低工作区强制落盘 | 该文件 `:13,:41,:86`；`ob_system_variable_init.json:1798-1801` | WRONG | 调大到 100（B-03） |
| 01:879-880、:81 | `ob_memory_tracker_wrapper.cpp:20-28` | executor 下的同名文件只有 22 行 | PARTIAL | 函数在 `src/sql/parser/` 下，且不参与构建（C-05） |
| 01:882、:752 | 563 处 auto_free 数组 | `ob_array.h:484-497`；grep 559 处 | PARTIAL | 绑定的是 arena，§2.2 与 §2.12 矛盾 |
| 01:852 | 页大小在 `obmp_query.cpp:472` | 实际 `:473` | PARTIAL | 行号差 1 |

**02（子审查的全表）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 81-83 | 两个串行锁、`DEFAULT_MUTEX`、用语句截止时间 | `ob_local_ddl_serial_call.h:29-47`、`ob_root_service_serialization.h:34-55`、`ob_mutex.h:31,36` | CONFIRMED | 清单不全（B-22） |
| 84 | `timeout_ts_` 初值 INT64_MAX，永不超时 | `worker.cpp:57` | PARTIAL | macOS 上不成立（B-21） |
| 85 | `"latch: <name> wait"` | `ob_wait_event.cpp:37-42` | CONFIRMED | |
| 981 | 204 行：FIFO 180、READ_PREFER 24…… | `ob_latch_define.h` | CONFIRMED | 重数一致 |
| 986 | `abs<=0` → OB_INVALID_ARGUMENT；超时前先自旋 | `ob_latch.cpp:93-98,649-656,100-142,658-704` | CONFIRMED | |
| 995 | 剩余 ≤0 → OB_TIMEOUT；等过的线程拿锁带 WAIT 位 | `ob_latch.cpp:144-176` | CONFIRMED | |
| 997 | 只有 WAIT 位时 trylock 也失败，返回 OB_EAGAIN | `ob_latch.cpp:62-81` | CONFIRMED | |
| 999 | `ObSpinLock::lock(t)` 相对、`ObMutex::lock` 绝对 | `ob_spin_lock.h:68-71`、`ob_mutex.h:36` | CONFIRMED | |
| 1000 | guard 加锁失败记 ERROR，析构不解锁 | `ob_lock_guard.h:50-67` | CONFIRMED | |
| 1006 | `try_rdlock` 的三种返回 | `ob_latch.cpp:480-518` | CONFIRMED | |
| 1009 | READ_PREFER 的读等待者插到表头 | `ob_latch.cpp:456-471` | CONFIRMED | |
| 1013 | wake_up 只唤醒队首写者或连续读者 | `ob_latch.cpp:327-420` | CONFIRMED | |
| 1014 | 写锁降级要求本线程持写锁 | `ob_latch.cpp:586-603` | CONFIRMED | |
| 1023 | DRWLock 写锁 0→N−1、失败倒序释放 | `ob_drw_lock.h:138-169` | CONFIRMED | |
| 1032 | TCRWLock 写锁 `ttl<0` → OB_EAGAIN | `ob_tc_rwlock.h:183-208` | CONFIRMED | `ttl` 只在有读者时计算 |
| 1036 | 重试写锁永不失败 | `ob_tc_rwlock.h:119-140,209-222` | CONFIRMED | |
| 1043 | Quiescence：N 槽、`itid%N`、sync 忙等不 PAUSE | `ob_qsync.h:45-87,121` | CONFIRMED | |
| 1046-1049 | QSyncLatch 的 try 返回 OB_EAGAIN；最多 16 次 `try_sync` | `ob_qsync_lock.cpp:34-111`、`.h:42-46` | CONFIRMED | |
| 1055-1056 | 锁数 n/8+1；latch = 桶/8 | `ob_bucket_lock.cpp:66`、`.h:331-344` | CONFIRMED | |
| 1060 | 空数组 → OB_INVALID_ARGUMENT；失败只释放已拿到的 | `ob_bucket_lock.cpp:336-389` | PARTIAL | 回滚也解开失败的那把（B-23） |
| 1066 | `wait(ms)`=`wait_us(ms*1000)`；0 不限时；超时 OB_TIMEOUT | `ob_thread_cond.h:71-75`、`.cpp:79-147` | CONFIRMED | |
| 1069 | `timed_wait` 超时也返回 true；负值返回 false | `cond.h:101-127`、`ob_trans_result.cpp:46-71` | CONFIRMED | |
| 1071 | ObCond 的 signal、wait、timedwait | `ob_queue_thread.cpp:30-115` | CONFIRMED | |
| 1076-1077 | SimpleCond 只有超时返回 OB_TIMEOUT；`signal(limit)` | `ob_scond.h:37-61`、`ob_futex.cpp:119-145` | CONFIRMED | |
| 1081 | Linux：≤0 时立即超时 | `ob_futex.h:54-61` | PARTIAL | 只有 0 超时；负值走 EINVAL 被当成功 |
| 1082 | macOS：取低 32 位，0 不限时 | `ob_futex.cpp:63-85` | CONFIRMED | 探针证实 |
| 1084 | Windows 唤醒 1 个时恒返回 1 | `ob_futex.h:68-82` | CONFIRMED | |
| 1085 | SCondTemp 的 `loop_cnt > LOOP_LIMIT` 永不成立 | `ob_scond.h:151-164` | CONFIRMED | |
| 1090 | bitlock 两阶段自旋与 usleep | `ob_small_spin_lock.h:126-167` | CONFIRMED | |
| 1101 | `CtxLock::lock` 的截止时间与倒序释放 | `ob_trans_ctx_lock.cpp:90-110,161-176` | CONFIRMED | |
| 1102 | 读锁路径也执行 `before_unlock` | `ob_tx_ctx.cpp:5975-5978`、`ob_trans_ctx.cpp:46-60` | CONFIRMED | |
| 1131-1138 | `inc_update` 返回 `nv`；AtomicReference；aarch64 `FAA(0)` 读 | `ob_atomic.h:97-121`、`ob_atomic_reference.*` | CONFIRMED | |
| 1241 | 错误码数值 −4023/−4016/−4006/−4002/−4005 | 生成的 errno 头文件 | CONFIRMED | |

**03（WRONG、PARTIAL 全部列出；CONFIRMED 的 26 条见存档原表）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 1117 | ObDITls 递归时返回 0x1 | `ob_di_tls.h:101-113` | WRONG | 嵌套新建第二个实例（B-19） |
| 1140 | "生命周期场景会测到" 137 | `sdb.py:370-403`、`lifecycle.py:91-92` | WRONG | 裁判拿不到退出状态（B-17） |
| 1028 | 子线程依次设 QoS、登记、写 `create_ret_` | `thread.cpp:96,341-349,383` | PARTIAL | macOS 在登记前写；attr 上也设 QoS；不可见 |
| 1110 | `set_char` 的语义 | `ob_trace_id.h:158-176` | PARTIAL | sscanf 边界没写（B-18） |
| 1128 | macOS 原生栈 229,376，最多扩 4 段 | `pthread_get_stacksize_np` 实测 241,664 | PARTIAL | 4 段的结论不变 |

**04（本人）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 252 | `ck!`：只有原来成功才置 OB_ERR_UNEXPECTED，KV 再次求值条件 | `ob_check_macros.h:22-61` | CONFIRMED | |
| 320 | `ob_assert!` 记两行再 `ob_abort` | `ob_macro_utils.h:704-711` | CONFIRMED | |
| 364 | `retry_func_on_error!` | `ob_macro_utils.h:665-680` | CONFIRMED | |
| 1318 | 9 条正则的顺序；非 `_EXT` 的用户消息就是 M3 | `gen_errno.pl:40-70` | CONFIRMED | `store_error(…,$5,$5,…)` |
| 1343 | 查表函数逐格（未登记 0、越界 −1 等） | `gen_errno.pl:360-463` | CONFIRMED | |
| 1347 | 表与 `g_all_ob_errnos` 与入库的 `ob_errno.cpp` 逐项相同 | 临时目录重跑 `gen_errno.pl` | CONFIRMED | 三个生成物逐字节相同 |
| 163 | `DEFINE_OTHER_MSG_FMT` 的两条 | `ob_errno.def:612,250` | CONFIRMED | |
| 1369 | `%.ld` 只出现在 `OB_EST_DEVIA_TOO_LARGE` | `ob_errno.def:1862` | CONFIRMED | |
| 1410 | Linux/macOS 上两种方言相同 | `ob_print_utils.cpp:456-521` | CONFIRMED | 只有 `_WIN32` 改写 |
| 1431 | C++ 关了格式检查 | `compile_commands.json`（363 条都在 `-Wformat` 之后带 `-Wno-format`） | CONFIRMED | |
| 1441 | `%s` 读过切片末尾"那是越界" | `ob_expr_tokenize.cpp:189` 等 | PARTIAL | 多数越界，也有同一分配内的确定读（B-08） |
| 1481 | `cmp_meta()` 返回 `ObCStringHelper::convert` 的结果 | `ob_expr_collation.cpp:373-387` | CONFIRMED | |
| 1505 | f64：A `%.18le`、C `k:"%.18le"` | `ob_print_utils.cpp:326-334`、`.h:853-857` | CONFIRMED | float 同理 `%.9e` |
| 1506 | bool：A `True/False`、C `true/false` | `ob_print_utils.cpp:347-356`、`.h:858-868` | CONFIRMED | |
| 1704 | 用户消息按 `vsnprintf(buf, 512)` | `ob_log.cpp:654-666` | CONFIRMED | |
| 1705 | 格式化结果为空串时不写缓冲区 | `ob_log.cpp:1164-1182` | CONFIRMED | `data_len > 0` |
| 1708 | 警告缓冲的 set/append/reset 规则 | `ob_warning_buffer.h:60-82,155-211` | CONFIRMED | 覆盖旧项时不清 SQLSTATE |
| 1724 | `send_error_packet` 的文本选择 | `obmp_packet_sender.cpp:553-571` | CONFIRMED | |
| 1725 | 转码后截到 512（不是 511） | `obmp_packet_sender.cpp:586-588` | CONFIRMED | |

**05（本人）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 877 | 各 `hash_func` 特化（整数恒等、`pair<int,u32>` 是 u32 加法、`const char*` 按 strlen） | `ob_hashutils.h:773-859` | CONFIRMED | |
| 1639、1825 | 容量增长只影响 `get_capacity()` 和内存，不可观察 | `ob_array.h:365-393`；`ob_sort_op_impl.h:713-717`、`.cpp:762-763,1027` | WRONG | 进入 `mem_context_->used()`，决定落盘（B-03） |
| 1643 | `reserve` 的参数是总容量 | `ob_array.h:365-394` | CONFIRMED | |
| 1705 | 桶数 = `cal_next_prime`；28 个素数 | `ob_hashutils.h:674-687`、`ob_hashutils.cpp:21-29` | CONFIRMED | |
| 1709 | 新节点头插 | `ob_hashtable.h:1043-1046` | CONFIRMED | |
| 1716 | 遍历：桶 0..n、桶内头到尾，空桶不加锁 | `ob_hashtable.h:1490-1518` | CONFIRMED | |
| 1733 | DCHash 序：`hash` 或上 2、清掉最低位，再加 `compare`，跳过哑节点 | `ob_dchash.h:181-192,320-329`；`ob_link_hashmap.h:417-436` | CONFIRMED | |
| 1733 | 会话键的 `hash()` 是 `murmurhash(&sessid_, 4, 0)` | `ob_sql_session_info.h:103-110` | CONFIRMED | |
| 1752 | 锁等待节点的比较序 | `ob_lock_wait_node.cpp:69-91`；`ob_fixed_hash2.h:28-35`（`hash_` 是 u64） | CONFIRMED | |
| 1769 | `ObLightyQueue::pop` 超时返回 OB_ENTRY_NOT_EXIST | `ob_lighty_queue.cpp:109-133` | CONFIRMED | |
| 1772 | `ObPriorityQueue2` 余量 2048/1024/0、共享计数、按类 signal | `ob_priority_queue.h:191-225` | CONFIRMED | |
| 1789 | ObSEArray 的 `to_string`（32 个、省略号后仍打第 n-1 个、无 to_string 时 `[cnt:N]`） | `ob_array.h:741-771` | CONFIRMED | 04:1555 写反（C-23） |
| 1796 | ObHashSet 每个键后跟逗号 | `ob_hashset.h:202-211` | CONFIRMED | |
| 1810 | ObList 序列化用定长 `encode_i64` | `ob_list.h:477-500` | CONFIRMED | |

**06（本人）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 6 | `_LIBCPP_VERSION 190102` | `MacOSX15.4.sdk/usr/include/c++/v1/__config:30` | CONFIRMED | |
| 28 | `ob_strtod` = fast_float 快路径 + dtoa | `ob_dtoa.cc:344-405` | CONFIRMED | 快路径对 inf/nan 置 0、`end` 退回 |
| 32 | fast_float 的调用点 | `ob_array_cast.cpp:705-713,985-1006` | CONFIRMED | |
| 35 | 以浮点为键的 `std::map` | `ob_expr_map.cpp:235-252` | CONFIRMED | |
| 235 | `ObSqlString::ptr()`"C++ 是以 NUL 结尾的 C 串" | `ob_sql_string.h:67` | PARTIAL | 未分配时是 NULL（B-08） |
| 448 | `ob_sort` 就是 `std::sort` 加 errsim 检查 | `ob_sort.h:27-55` | CONFIRMED | |
| 803 | `case_compare(_equal)` 对一边 NULL 的返回值 | `ob_string.h:310-352` | CONFIRMED | |
| 807 | 空串返回 seed，否则 murmurhash | `ob_string.h:295-302` | CONFIRMED | |
| 833 | main 里的四次 `setlocale`（`main.cpp:693-698`） | `main.cpp:692-697` | PARTIAL | 行号差 1 |
| 854 | `std::map<double>` 的 NaN 吞键、覆盖根、±0 保留先插 | 基线工具链探针 | CONFIRMED | |
| 865 | `ROUND` 用 `rint` | `ob_expr_util.cpp:390-405` | CONFIRMED | |
| 876 | `fnv_hash2` 按 `char` 符号扩展 | `murmur_hash.cpp:51-66` | CONFIRMED | |
| 893 | 走默认比较器路径的只有两处 uint64 排序 | `ob_ddl_service.cpp:1236`、`ob_schema_service_sql_impl.cpp:382` | CONFIRMED | |
| 896 | 堆：两个子节点相等时选左子 | `__algorithm/sift_down.h:47,71,98` | CONFIRMED | |
| 903 | 定长大端、变长 7 位、`encode_fixed_bytes_i64` 恒 5 字节 | `serialization.h:244-343` | CONFIRMED | |
| 924 | unis 头：预留 5 字节、`pos = pos_orig + len` | `ob_unify_serialize.h:143-216` | CONFIRMED | |

**07（本人）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 220 | `sizeof(ObDatum) == 12` | `ob_datum.h:1027` | CONFIRMED | |
| 840 | `eval_args` 覆盖二元算子的 `args_[0]/args_[1]->eval` | `ob_expr_ifnull.cpp:130-145`、`ob_expr_between.cpp:41-46` | WRONG | 有一批是短路求值（B-01） |
| 1337 | 位域 `len_` 29 位、`flag_` 2 位、`null_` 1 位，从低位起 | `ob_datum.h:134-165` | CONFIRMED | |
| 1338 | `set_null`/`set_none`/`set_ext` 的写法 | `ob_datum.h:152-156` | CONFIRMED | |
| 1374 | DRB：`next_pow2`、超过 `UINT32_MAX` 返回 NULL、旧内存不释放 | `ob_expr.cpp:286-315` | CONFIRMED | |
| 1388 | release 下只在 `used() > OB_MALLOC_MIDDLE_BLOCK_SIZE` 时 reset | `query/api/query/engine/expr/ob_expr.h:260-272` | CONFIRMED | |
| 1410 | `REG_SAME_OP` 先查原类型已注册 | `ob_expr_operator_factory.cpp:445-463` | CONFIRMED | |
| 1410 | `get_type_by_name`"跳过"区间外的项 | `ob_expr_operator_factory.cpp:468-487` | PARTIAL | C++ 是 `break`；注册连续，结果相同 |

**08（子审查的全表）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 866 | `buf_` 在偏移 90，节点 96 字节 | `ob_mvcc_row.h:85-99` | CONFIRMED | |
| 374、869 | ObMvccRow 112 字节，C++ 只断言 ≤120 | `ob_mvcc_row.h:268-299` | CONFIRMED | |
| 861 | 节点标志位取值 | `ob_mvcc_row.cpp:36-43` | CONFIRMED | |
| 861 | `need_elr` 整字节相等 | `ob_mvcc_row.h:187-195` | CONFIRMED | |
| 862 | 先写版本、再置 committed、再写 `tx_end_scn` | `ob_mvcc_row.cpp:135-141` | CONFIRMED | |
| 874 | `update_max_*`、DF_LOCK 不改 dml flag | `ob_mvcc_row.cpp:722-741,778-791` | CONFIRMED | |
| 875 | 回放索引越界静默忽略 | `ob_mvcc_row.cpp:324-352` | CONFIRMED | |
| 868 | 失败不前移 `pos`；先头插再 undo | `ob_memtable.cpp:2505-2577`、`ob_mvcc_row.cpp:1036-1098` | CONFIRMED | |
| 903 | 15/128/64/1024，节点 272 字节，`MAX_DEPTH=16` | `ob_keybtree_deps.h:38-50,333-342,352`、`ob_keybtree.cpp:188-198` | CONFIRMED | |
| 903 | WeightEstimate 按 `NODE_KEY_COUNT/2` 的幂 | `ob_keybtree_deps.h:161-180` | CONFIRMED | |
| 906 | 每批最多 225 个 | `ob_keybtree.h:143-160`、`ob_keybtree.cpp:1212-1244` | CONFIRMED | |
| 909 | `MAX_SAMPLE_LEAF_COUNT=500` 等 | `ob_keybtree.cpp:952-990` | CONFIRMED | |
| 909 | `split_range`、`estimate_*` | `ob_keybtree.cpp:1357-1478` | CONFIRMED | 只依赖树结构 |
| 909 | `update_split_info` | `ob_keybtree.cpp:1732-1740` | CONFIRMED | |
| 910 | `get_allocated()` 恒为 sizeof 常数 | `ob_keybtree.h:92-94`、`ob_query_engine.cpp:122-127` | CONFIRMED | Rust 的 size_of 不同（B-03） |
| 904 | 写锁 uid 为 `(uint16_t)(itid+1)` | `ob_keybtree.cpp:717` | CONFIRMED | |
| 915 | 回调链表的锁模式 | `ob_tx_callback_list.cpp:980-1052` | CONFIRMED | |
| 915 | ObByteLock：501 次后 `usleep(100)` | `ob_small_spin_lock.h:126-165,213` | CONFIRMED | |
| 917 | `next == end` 时临时加 append 锁 | `ob_tx_callback_list.cpp:331-343` | CONFIRMED | |
| 925 | LSN 分配器的位域与阈值 | `lsn_allocator.h:82-101` | CONFIRMED | |
| 924 | `fill` 的检查顺序与返回码 | `log_group_buffer.cpp:206-221` | CONFIRMED | |
| 923 | `read_data` 拷完再复查 | `log_group_buffer.cpp:436-481` | CONFIRMED | |
| 898 | 退休站槽数、每 64 次试一次、`purge` 两遍 | `ob_retire_station.h:194-258` | CONFIRMED | |
| 897 | QClock `OB_MAX_CPU_NUM*4` 槽 | `ob_retire_station.h:30-58` | CONFIRMED | |
| 930 | slot 的 retire 释放整条删除表 | `ob_kvcache_hazard_version.cpp:75-104` | CONFIRMED | |
| 935 | 1024 槽、阈值 512、转 3 圈 OB_ENTRY_NOT_EXIST | `ob_kvcache_map.h:38-39`、`ob_kvcache_hazard_version.cpp:209-238` | CONFIRMED | |
| 937 | map 节点 88 字节、pair 头 24 字节 | `ob_kvcache_map.h:77-103`、`ob_kvcache_struct.*` | CONFIRMED | |
| 937 | 节点 seq 是 i32、比较时符号扩展 | `ob_kvcache_hazard_pointer.cpp:24-38` | CONFIRMED | |
| 939 | `put_and_fetch` 返回 OB_ENTRY_EXIST 时句柄仍有效 | `ob_kv_storecache.cpp:314-341` | CONFIRMED | |
| 939 | 覆盖写不减旧块计数 | `ob_kvcache_map.cpp:233-251,387-389` | PARTIAL | 行号写成 `:389-390` |
| 939 | `get_hit_rate` 恒为 0.8 | `ob_kv_storecache.h:468-472` | CONFIRMED | 无调用者 |
| 938 | 清理、替换的批大小是 20 万、10 万 | `ob_kv_storecache.cpp:219-223,529,544` | PARTIAL | 是 `bucket_num/50`、`/100` 加上限（本人复核） |
| 889 | 已冻结 → OB_EAGAIN | `ob_memstore_freezer.cpp:1659-1703` | PARTIAL | 只是局部 ret（B-25） |
| 135、79 | SCN 的 `<`、TxSEQ 的 `>`；`icpu_id` 回退实现 | `scn.cpp:330-342`、`ob_tx_seq.h:63-74`、`ob_tsi_utils.h:70-77` | CONFIRMED | |

**09（本人）**

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 197 | 系统变量 JSON 有 776 个变量 | `ob_system_variable_init.json` | PARTIAL | JSON 有 776 项，其中 57 项 placeholder（B-02） |
| 1419、1423 | 生成 776 个常量和 `ObSysVars` | `gen_ob_sys_variables.py:66-72`、`ob_sys_var_meta.h:31-33` | WRONG | C++ 生成物 719 项（B-02） |
| 1684 | 生成顺序与 bison/flex 参数 | `sql/parser/gen_parser.sh:62-96`、`pl/parser/gen_parser.sh:61-62` | CONFIRMED | |
| 1685 | 4 条 sed 都生效，第 4 条改变 `parse_init` 的 OOM 返回值 | `sql_parser_mysql_mode_lex.c:73339-73354,73578-73618` | WRONG | macOS 上 BSD sed 失败，生成物没改（B-10） |
| 1696 | 服务端解析器不定义 `SQL_PARSER_COMPILATION` | `compile_commands.json`（14 个解析器 C 文件） | CONFIRMED | |
| 1703 | `parse_init` 的错误码映射 | `sql_parser_base.c:40-56` | CONFIRMED | |
| 1761-1765 | errno 生成器保证表与 `ob_errno.cpp` 逐项相同 | 重跑 `gen_errno.pl` | CONFIRMED | |
| 1799 | zlib 静态链接 deps 的 `libz.a` | `otool -L` 无 libz | CONFIRMED | |
| 1815 | 异常映射 20 个子句、18 个码 | `ob_geo_dispatcher.h:1314-1366` | CONFIRMED | 21 行 catch，其中一对按平台二选一 |
| 1884 | P1：macOS、Linux 都与 `gen_parser.sh` 逐字节相同 | 同 1685 | WRONG | macOS 做不到（B-10） |

## 5. 无金标准的"一致"说法

- **01**：S7/T3 的 `ArenaVec` 增长（T3 把与 C++ 不同的序列写成了期望，B-04）；S11/T7 的 MemCtx 三个数只有 Rust 自洽的期望（B-14）；S14"工作区预算按页计原始页大小，与 C++ 相同"没有测试；S12 的"首条语句受限、重试不受限"没有测试（B-15）；S18 的 macOS MPRINT 文本、S19 的 libc 覆盖只测了 Linux 的 3 个点。
- **02**：F5 的三套平台超时规则和 B12（macOS `lcond_.wait(0)` 不限时）没有测试；B5"截止时间已过但没有读者时写锁成功"（`ObOBJLock::unlock` 可达，`ob_obj_lock.cpp:614-618`）不在 T6 里；R4/R8 的 READ_PREFER 排队与唤醒只有 Rust 自写的期望。
- **03**：SIGTERM 137、SIGUSR1 0 的退出状态在现有裁判上观察不到（B-17）；`set_char` 的金标准缺 EOF、空白/符号/`0x`、宽度、饱和、遇 NUL 截断（B-18）；"SIGPIPE 设成 SIG_IGN 与 C++ 等价"实测不同（B-20）；ObBasicTimeGuard 的"固定时间源"金标准做不到（`get_cur_ts()` 直接读 TSC，`ob_common_utility.h:62`）；第 34 条测试的原生栈大小应改用 `pthread_get_stacksize_np` 报告的 241,664。
- **04**：`%s` 的切片语义（04:1441）没有覆盖 NULL 的 ObString 和不以 NUL 结尾的实参（B-08）；`send_error_packet` 的支持只有 Rust 表驱动测试（§5.3 第 15 条），没有 C++ 产出的期望。
- **05**：§3.16"数组容量增长不可观察"没有测试，而且不成立（B-03）；`ObLightyQueue`、`ObSpLinkQueue` 的语义（Q2、Q6）只有 Rust 自写的期望。
- **06**：S-SLOT-1"正常路径只绑一次所以没有差别"没有测试；其余数值、排序、序列化都有金标准。
- **07**：§5 第 8 条只测求值顺序和遇错即停，不测短路（B-01）。
- **08**：2.1 值类型"序列化、TO_STRING 不变"没有任何测试（B-07）；S50"`store_size`、每块能放的键值数与 C++ 一致（G4）"只在键值大小相同时成立；S6"memstore 用量不变"只对节点成立；S38"`get_allocated()` 照抄"与 Rust 的 `size_of` 不符。
- **09**：G5 没写明双向全表比对（B-02）；P1 在 macOS 上做不到（B-10）。

## 6. 缺失的规则

### M-1【低】`std::stoi`/`stoll`/`to_string`/`ostringstream` 没有译法

- **现状**：06 只管 C 库的数值函数。基线里还有 `std::stoi`（`ob_collection_type.cpp:386,616`）、`std::stoll`（`ob_dump_task_generator.cpp:95,102`）、`std::to_string`（`ob_all_virtual_session_ps_info.cpp:82`）、`std::ostringstream`（`Time.cpp:171`）。基线开着异常（363 条编译命令都没有 `-fno-exceptions`），`std::stoi` 越界抛 `std::out_of_range`，没人捕获就终止进程。
- **建议补充的规则**：`std::stoi`/`stoll` → `c_strtol`/`c_strtoll` 加 errno 判断；C++ 会抛异常的条件照 C++ 终止（`PARITY(port)`），或由清单逐处判定；`std::to_string(整数)` → `ob_printf!("%d"/"%ld")`。

### M-2【低】运行时库的版本没有钉住

- **现状**：ENV-1 只钉了 SDK，即头文件。printf、strtod、libm 走运行时的 libsystem；算术类型用默认比较器的 `std::sort` 走系统的 `libc++.1.dylib`（06 S-SORT-3；基线二进制记录的是 `libc++.1.dylib` current version 1900.178）。04 的 printf 金标准、06 的 `c_*` 与 libm 对照，都在"本机"生成。
- **建议补充的规则**：ENV-1 加 macOS 版本，或 libsystem_c、libsystem_m、libc++ 的版本号；金标准文件头记录生成机的这些版本，裁判运行前比对。

### M-3【低】构建脚本静默失败时以哪个产物为准

- **现状**：B-10 说明，基线的生成步骤可能在某个平台上静默失败（sed 的退出码没人检查），"复刻脚本"和"复刻产物"给出的结果不同。
- **建议补充的规则**：1d 的生成器一律以"该平台基线实际产出的文件"为对齐目标，build.rs 的产物与基线产物逐字节比对；两者不同时以基线产物为准，并在 `PARITY(port)` 里写明原因。

## 7. 与 `consistency.md` 的分工

下列问题也会改变用户可见的行为，`consistency.md` 已经提出，本文不重复：

- **C-05**：解析器的内存检查。基线链接的是 weak 空实现，按 01 实现会新增 4013。
- **C-15(15)**：`try_str` 表达不了 `ob_write_string` 产出的 NULL。本文 B-08 只补充 `%s` 打印 `(null)` 这一面。
- **C-19**：`ob_e!`/`errsim_point!` 的特性门控；01 的 `ERRSIM_SMART_VAR`、06 的 `EN_CHECK_SORT_CMP` 在 release 里也应生效。
- **C-21**：IR 节点里的 `SmallVec`/`Vec` 仍有固有方法遮蔽 trait 方法的问题（05-A1 的同类）。
- **C-23**：04:1555 把 ObSEArray 的省略号规则写反了，源码与 05:1789 一致。照 04 实现会改变 `to_string` 的字节。
