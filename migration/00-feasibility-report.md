# seekdb → Rust 迁移可行性报告

> 本文是 code-migration kit 第 0 步（`prompts/00-feasibility.md`）的产出。
>
> - **对象**：cao1629/seekdb `master` 上的 `834bbee1e`（2026-09-22）。上游 oceanbase/seekdb 的 master 已到 `7d907abfa`，比它多 9 个提交，本报告没有覆盖这 9 个提交。
> - **分支**：`rust-migration`　**日期**：2026-10-10
> - **方式**：只读调查，没有改动任何源码。Claude Opus 5.5 主导，5 个只读调查子代理分头取证。
> - **证据**：每个数字都附了文件路径、提交号或可复现的命令；分析脚本和精简结果在 `migration/feasibility/`。推断和估算都标成「假设」。
> - **状态**：模型分配已由你在 2026-10-10 定下（见第 5 节）。**是否迁移，仍等你在可行性关口签字。** 签字之前不启动任何后续步骤。

## 0. 六步流程（索引）

1. **建立地图和规则**：用确定性脚本生成依赖图，决定翻译顺序；用缺口清单逐处记下 Rust 要求、而 C++ 允许你"记在脑子里"的东西（所有权、生命周期等）；用规则手册把每个"两个 agent 可能答得不一样"的问题定一次。
2. **压力测试规则**：在少量最难的文件上做双译者对照（bakeoff）和试点，唯一留下的产出是规则修订。
3. **全量翻译**：每个单元配一个实现者、两个对抗审查者、一个修复者，按磁盘上的队列扇出；这一步不跑编译器。
4. **编译**：一次 survey build 把编译错误变成按模块切片的队列，修复者在没有编译器权限的情况下逐轮清零。
5. **跑起来**：先 hello world，再冒烟测试。
6. **行为对齐**：用第 1 步之前就建好的裁判对比新旧实现，直到全部通过，并且原有测试在原代码上没有继承失败。

## 摘要

| 问题 | 结论 |
|---|---|
| 离开 C++ 的理由 | **中等。** 一年内 368 个修复里约 8% 是 Rust 编译期能挡住的，把藏在普通标题下的同类 bug 算上估计 15–20%；约 71% 是 Rust 解决不了的逻辑、兼容、死锁和性能问题。团队已经在按组件往 Rust 迁（网络层、配置框架）。 |
| 判断 1：保持结构还是重新设计 | **重新设计所有权和数据结构层，保留模块架构。** 照原结构直译，约 45% 的函数会变成写在 unsafe 里的 C++。 |
| 判断 2：验证成本 | 今天的 C++ 没有独立的类型检查，**完整构建就是检查**：CI 冷构建约 9–10 分钟（32 槽）。Rust 移植后冷 `cargo check` 估计 1–10 分钟，**不够便宜，第 4 步要保留**。 |
| 判断 3：测试能不能带走 | **不能直接当裁判。** CI 跑的 272 个 mysqltest 只有 89 个是干净的公开 SQL；obtest 几乎全部依赖内部实现，在公开仓库里也跑不起来；C++ 单元测试已经从公开仓库删除。**第 1 步之前必须先建裁判（`00b`）。** |
| 规模 | 需翻译 2,298,401 行，3,753 个单元；另有约 50 万行是生成代码、内嵌数据、语法文件和第三方源码，应该重新生成或保留，不翻译。 |
| 成本 | 按你定的"所有 agent 都用 max effort"：token **约 5×10⁹–5×10¹⁰**（约 1 万到 20 万美元）；日历时间约 5–13 个月；你的专注时间约 130–280 小时。 |
| **结论** | **以后再迁，不是现在。** 前提是裁判建好并通过验证、关键设计决定拍板、和上游的关系弄清楚。真做时按子系统逐个迁（团队已经在走这条路），不做一次性全量翻译。 |

## 1. 离开 C++ 的理由：本仓库里的实际痛点

### 1.1 修复记录

方法：
- `git log --no-merges` 共 1,314 个提交，按修复类关键词筛出 548 个。
- 去掉 38 个内外部同步提交，用 `git patch-id` 合并内外部双胞胎提交，只留改动 `src/` 下 C/C++ 的。
- 补上 3 个标题没命中关键词的内存修复，得到 **368 个修复提交**。
- 凡是命中内存、并发、崩溃关键词的提交都读了 diff（约 75 个），另外随机抽 30 个"逻辑"类提交读了 diff。

| 类别（读 diff 后） | 数量 | Rust 编译期能否挡住 |
|---|---|---|
| 内存安全：释放后使用 7、悬垂指针/生命周期 7、漏释放 5、空指针 3、越界 3、重复释放 1、读未初始化 1、错误的向下转型 1 | 28 | 25 个能；2 个越界会变成边界检查 panic；1 个不能（`54123f13e`） |
| 数据竞争 | 4 | 能 |
| 死锁 7、先检查后操作的竞态 3、线程池饿死 1 | 11 | 不能 |
| 非内存原因的崩溃（逻辑 3、平台 4、原因未定 4） | 11 | 2 个能（定宽整数） |
| 长生命周期 arena 上的内存堆积（如 `752f5950d`：每次 PL 调用约 8 KB，累积到 GB 级） | 4 | 不能 |
| 栈耗尽 | 3 | 不能 |
| 逻辑 / SQL 兼容 | 231 | 不能 |
| 性能 7，构建/打包/CI 50，测试 19 | 76 | 不能 / 不适用 |

- **比例**：31/368（8%）编译期能挡住，262 个（71%）不能，69 个（19%）不适用。
- **藏在普通标题下的同类 bug**：30 个"逻辑"抽样里有 3 个其实是内存 bug、3 个是平台或未定义行为 bug。外推各约 23 个（95% 置信区间 8–59），所以实际能挡住的比例约 **15–20%**。

典型例子：

| 提交 | 文件 | 问题 | Rust 能否挡住 |
|---|---|---|---|
| `b4ad89151`（#923） | `src/sql/engine/px/ob_dfo_scheduler.cpp` | 异步 lambda 用裸指针捕获了 arena 里的缓冲区，造成释放后使用。是七天前的死锁修复 `cb5a24ce5` 引入的，当时的注释还写着"缓冲区活得比异步任务长"。对应的 issue #920 至今没关 | 能 |
| `40fcfa901` → `328253969` | `ob_simple_thread_pool.ipp`、`ob_tenant.cpp` | 先修了工作线程的释放后使用和不加锁的链表插入，八天后同一处生命周期代码又出了重复释放 | 能（如果直译侵入式链表则需要 unsafe） |
| `0059d1906` | `src/share/ob_ddl_common.cpp` | 输出指针指向了函数内局部 schema guard 里的对象；标题里没有任何内存关键词 | 能 |
| `1e4f589b8` | `src/sql/engine/expr/ob_expr.cpp` | 读了未初始化的指针和长度数组。修复注释说"Linux 上 arena 内存经常碰巧是零……Windows 上就崩" | 能 |
| `479fb2ad4` | `ompk_handshake_response.cpp` | 对未认证握手包里不可信的字节调用 `strlen()`，越界读。正是后来迁到 Rust 的那个组件 | 能 |
| `54123f13e`（#871） | KV cache | 自研的内存回收方案释放了其他线程还在读的条目 | 不能（这类代码在 Rust 里也得写成 unsafe） |
| `834bbee1e`（#1420） | 事务层 | 从首次导入起就存在的锁顺序死锁 | 不能 |

### 1.2 团队为弥补 C++ 搭的设施

- **自研内存检查器**：`sanity` 构建模式（定义在 `CMakeLists.txt:29` 的 `ENABLE_SANITY`，运行时在 `src/oblib/lib/allocator/ob_jemalloc_sanity.cpp`）用影子内存和毒化红区，实现了一套类似 AddressSanitizer 的东西。CI 里没有接入 `-fsanitize`、TSan 或 valgrind。
- **分配器体系**：51 个类直接继承 `ObIAllocator`，有 2,059 处 placement new、1,266 处手动调用析构函数。编码规范明确禁止裸 `new`/`delete`（`docs/developer-guide/en/coding-convention.md:130`："Memory management is a very troublesome issue in C/C++ programs…"）。
- **核心数组越界只记日志**：`src/oblib/lib/container/ob_array.h:323` 的 `operator[]` 注释写着 `// dangerous`，越界时记一条错误日志，然后照样返回元素。
- **GitHub issue**：oceanbase/seekdb 共 851 个 issue，去重后有 101 个是崩溃或泄漏/OOM 类（90 个崩溃，11 个泄漏/OOM），其中 11 个引用了 `memory_sanity_abort`。

### 1.3 团队自己的方向

- **sql-nio（PR #1246，2026-08-04 合并）**：用 Rust 重写了 MySQL 协议和网络 IO，通过 cbindgen 生成的 C 接口接回 C++（`rust/sql-nio/include/nio.h`：33 个 `nio_*` 函数加 4 个回调）。
  - PR 说明的理由是统一各平台的连接分发方式："all platform uses one thread to accept incoming connections and dispatch them to multiple io threads"。结果是 "same performance on sysbench compared to baseline"。
  - **说明里没有提内存安全**，但 PR 评论提到它对应一个内部的 "Migrate seekdb to Rust" 事项。
- **之后上游还在继续迁**：#1457 "move instance parameters and persistence to Rust" 在 2026-10-08 合并，晚于本 fork 的基线；#1412 "migrate embedding response parsing to Rust" 还没合并。
- **sql-nio 迁移本身的代价**：删除 37 个 C++ 文件（7,443 行），新增约 8,500 行 Rust。跨 C 接口用了 186 处 `unsafe`（120 个 unsafe 块、62 个 unsafe fn、4 个 unsafe impl），跨边界的生命周期只靠 C++ 注释约束。迁移之后没有出过内存类修复，但出了 **2 次行为回归**：
  - `7d3574e6b`：检查用户权限时把 TLS 句柄传成了 `NULL`。**有 9 天，要求 SSL/X509 的账户登录不上，证书 CN 白名单检查也被跳过。**
  - `c57aaccd1`（#1321）：Rust 版的预处理语句解析拒绝了 mysqlnd 5.x 发送的填充字节，而 C++ 版是接受的。

### 1.4 小结

**支持离开 C++ 的证据**：
1. 生命周期 bug 反复出现，而且常常是团队自己的重构引入的（`cb5a24ce5` → `b4ad89151`，`40fcfa901` → `328253969`）。
2. 为了弥补 C++ 搭了一整套设施：自研 sanitizer、51 个分配器、越界只记日志的数组。
3. 迁到 Rust 的组件没有再出过内存类修复，团队也在继续扩大这条路。

**反对的证据**：
1. 约 71% 的修复是 Rust 解决不了的问题。
2. sql-nio 的 C 接口边界本身就需要 186 处 unsafe，迁移还带来了认证回归。
3. 整个代码库"内存都归 arena 管"的设计，和安全 Rust 很不匹配（见判断 1）。

结论：**这些理由足以支撑"按组件逐个迁"，但单靠 bug 数据不足以支撑"一次性全量重写"。**

## 2. 三个判断

### 判断 1：保持结构，还是重新设计？

**能一对一翻译的部分**：
- **错误处理**：`int ret = OB_SUCCESS;` 有 37,299 处，`OB_FAIL(` 约 10 万处（宏定义在 `src/oblib/lib/utility/ob_macro_utils.h:654-655`），天然对应 Rust 的 `Result<T, ObError>` 加 `?`。异常几乎不用：throw/try/catch 一共 118 处，集中在 15 个文件里。
- **宏 DSL**：序列化（`OB_SERIALIZE_MEMBER`、`OB_UNIS_*` 等，约 4,100 处）、`TO_STRING_KV`、日志宏都可以改成 derive 宏或过程宏。
- **运行时**：线程模型是普通 OS 线程，没有协程（ucontext 只出现在 `ob_signal_handlers.cpp` 和 libeasy 里）；`src/objit` 只剩一个头文件，LLVM JIT 已经删掉了；PL 是树遍历解释器。

**不能一对一翻译的部分（决定了结论）**：
- **内存归分配器管，不归对象管**：`ObIAllocator` 出现 9,197 次，有 60 个实现；2,074 处 placement new，1,265 处手动析构；`ObArenaAllocator::free` 什么也不做（`page_arena.h:1020`）。结构体大量借用而不拥有：6,120 个裸指针成员、9,457 个 `T *&` 输出参数、1,025 个 `ObString` 视图成员。
- **安全 Rust 表达不了的指针图**（不用 arena+索引、`Rc<RefCell>` 或 unsafe 就写不出来）：
  - 算子树的父子节点互相指向（`src/query/api/query/engine/ob_operator.h:319-326`）。
  - 运行时的表达式 DAG 有反向边（`src/query/api/query/engine/expr/ob_expr.h:678-681`）。
  - 解析阶段的表达式 DAG 通过 `ObRawExpr *&get_param_expr` 原地改写（`src/sql/resolver/expr/ob_raw_expr.h:2000`，2,362 处调用）；语句和表达式也互相指向（`ob_dml_stmt.h:278` ↔ `ob_raw_expr.h:2892`）。
  - MVCC 版本链是裸 `prev_`/`next_` 双向链表，读者无锁遍历，写者原地拼接（`src/storage/memtable/mvcc/ob_mvcc_row.h:91-99`）；同时还被事务回调的环形链表引用（`ob_tx_callback_list.h:230-233`）。
  - 无锁哈希表用指针最低位做删除标记，靠 QClock 和 retire station 回收内存（`src/oblib/lib/queue/ob_link.h:40-90`、`src/oblib/lib/hash/ob_link_hashmap.h`、`src/oblib/lib/allocator/ob_retire_station.h`）。
  - 157 个成员用 `this` 初始化。
  - 侵入式容器：`ObDLinkBase` 有 739 个子类，包括全部 573 个表达式算子。
- **其他**：`src/oblib/lib/alloc/memory_dump.cpp:45` 用 `siglongjmp` 跳出 SIGSEGV 处理函数，Rust 做不到；SQL 解析器靠 setjmp/longjmp 处理错误（`sql_parser_base.c:106`）。

**按子系统看**：

| 子系统 | 能否保持结构 | 决定性文件 |
|---|---|---|
| 错误、序列化、日志、容器 | 能 | `ob_macro_utils.h:654-655` |
| oblib 分配器、无锁结构、信号 | 只能靠大量 unsafe | `page_arena.h`、`ob_iallocator.h`、`ob_link.h`、`ob_link_hashmap.h`、`ob_retire_station.h`、`ob_qsync.h`、`memory_dump.cpp` |
| SQL 解析器（bison） | 先通过 FFI 保留 C 版解析器，或者重写 | `sql_parser_mysql_mode.y`（18,723 行）、`sql_parser_base.c:106` |
| 解析 / 改写 / 优化（约 40.7 万行） | 需要重新设计 | `ob_raw_expr.h`、`ob_dml_stmt.h:278`、`ob_logical_operator.h:1674/1863` |
| SQL 执行引擎和代码生成（约 37 万行） | 能，前提是算子和表达式改用 id，少量 unsafe | `ob_operator.h:319-326`、`ob_expr.h:678-681`、`ob_hash_join_basic.h:114-145` |
| Memtable/MVCC 和事务 | 只能靠大量 unsafe | `ob_mvcc_row.h:91-99`、`ob_tx_callback_list.h:230`、`ob_trans_ctx.h:85-236` |
| 存储的其余部分 | 句柄和引用计数需要重新设计 | `ob_meta_obj_struct.h:159-233` |
| palf（日志服务） | 能，核心部分少量 unsafe | `fixed_sliding_window.h` |
| observer / rootserver / share | 能，前提是替换全局单例（`GCTX` 884 处，`GCONF` 672 处） | — |
| PL | 能 | `ob_pl_interpreter.h` |
| 网络和协议 | 已经是 Rust | `rust/sql-nio` |

**如果硬按原结构翻译**：57,226 个函数体中有 45.1%（占函数体行数的 56.4%）含裸指针解引用，或者含只能用 unsafe 写的操作（placement new、`reinterpret_cast`、裸 alloc、memcpy、`ATOMIC_*`、汇编和 SIMD、setjmp）。结果基本就是"用 unsafe Rust 写的 C++"。

**所有权重新设计之后**：仍含 unsafe 专属操作的函数约占 11.8%（行数占 16.1%），集中在 oblib 底层（19.6%）、memtable（22.9%）和解析器（30.2%）。假设重新设计后的移植约有 10–15% 是 unsafe。这些都是正则统计：`->` 也会命中重载的 `operator->`，头文件里的内联方法没有扫。

> **判断 1：重新设计所有权和数据结构层，保留模块架构。** 决定性文件：
> - `src/sql/resolver/expr/ob_raw_expr.h`
> - `src/query/api/query/engine/ob_operator.h`
> - `src/query/api/query/engine/expr/ob_expr.h`
> - `src/storage/memtable/mvcc/ob_mvcc_row.h` + `ob_tx_callback_list.h`
> - `src/oblib/lib/queue/ob_link.h` + `src/oblib/lib/hash/ob_link_hashmap.h` + `src/oblib/lib/allocator/ob_retire_station.h`
> - `src/oblib/lib/allocator/page_arena.h` + `src/oblib/lib/alloc/ob_iallocator.h`

按 kit 的规定，选择重新设计会改变后面三件事：
1. 规则手册变成设计文档。
2. 第 2 步的双译者对照失效，换成对设计文档的对抗审查，加上"一次性的廉价全量试跑"。
3. 工作单元变成模块或子系统。

第 6 步的行为对齐不受影响。

### 判断 2：验证成本是多少？

**基线**：实测数据都来自 CI；本机没有构建，原因见第 7 节。

| 场景 | 耗时 | 来源 |
|---|---|---|
| CI 冷构建（自建 32 槽 pod，`make -j32`，ccache 命中 0/366） | `make` 542–609 秒；含依赖初始化的编译步骤 836–1,121 秒 | 8–9 月的 5 次冷构建，如 run 33350991972 |
| CI 热构建（ccache 命中 64–88%） | `make` 92–181 秒 | 如 run 37760693836 |
| 近一周 42 次编译步骤 | 143–605 秒，中位数 422 秒 | 2026-10-03 至 10-10 |
| GitHub 托管 4 vCPU 机器，Debug 构建，`make -j4` | 2,272–3,961 秒，中位数 3,301 秒 | `compile.yml` 7 月的 22 次运行，这个工作流现已禁用 |
| 你的 M4 笔记本冷构建 | 约 30 分钟（假设一个 M4 核约等于一个 CI 槽） | 由约 18,000 槽·秒推算 |

- **构建系统**：CI 和开发都用 CMake（`.github/script/seekdb/compile.sh` → `build.sh release`），并且强制开 unity build（`CMakeLists.txt:118`）。216 个 unity 组覆盖 2,789 个文件，再加上单独编译的源文件；CI 日志显示一次全量构建共 366 次编译器调用。Bazel 8.2.1 被称为"权威的模块化构建图"，但 CI 不调用它。
- **C++ 没有独立的类型检查**：没有 `-fsyntax-only` 或 clang-tidy，带 `-Werror` 的完整构建就是检查。CodeQL 的 C++ 周任务近 50 次运行没有一次成功。
- **链接方式**：链接时用了 `-Wl,--start-group … --end-group`（`src/observer/CMakeLists.txt:90`），说明 sql、storage、share 这几个静态库之间存在**符号级的循环引用**。
- **Rust 的唯一实测数据点**：sql-nio（8,448 行）冷 release 编译 12.7–13.9 秒（thin LTO、`codegen-units = 1`，见 `rust/Cargo.toml`），clippy 检查 0.5–5.7 秒。

**Rust 移植后的估计**：

| | C++ 今天（实测） | Rust 移植（估计） |
|---|---|---|
| 冷检查 | 等于完整构建：542–609 秒（32 槽） | 约 1–10 分钟 |
| 增量检查 | 重编一个 unity 文件（平均 50 秒以内）加约 5 秒链接 | 叶子 crate 几秒到 1 分钟；底层 crate 的接口一变，就接近冷检查 |
| 冷 release 构建 | 542–609 秒（32 槽） | 约 10–60 分钟；release 没有增量编译 |

**扩展假设**：Rust 检查或构建的时间 ≈ 移植后的行数 ÷ sql-nio 上实测的单核编译速度 ÷ 能并行编译的 crate 数，且不会低于最大的那个 crate 单独编译的时间。移植后约 160–240 万行（C/C++ 的 0.6–0.9 倍，因为头文件里的声明会合并），最大的 sql crate 约 50–80 万行。

这意味着：
- **第 4 步要保留**：Rust 检查比今天的 C++ 检查便宜，但远没有 tsc、go vet 那么便宜，所以第 4 步不能并进第 3 步，要用 `build_daemon.sh` 作唯一的构建者。`cargo check -p` 只适合放进叶子 crate 的单元循环里。
- **crate 可能被迫变大**：链接层面的循环可能迫使模块合并成更少、更大的 crate，那样最慢的 crate 编译时间会更长。
- **内存风险（假设）**：在 16 GB 内存的笔记本上，单独编译一个 50–80 万行的 crate 可能就要几 GB 内存；release 构建（`codegen-units = 1`、`debug = true`、thin LTO）可能超过 16 GB。需要把并发降到 4–6，开发构建调高 `codegen-units`，并改用 `debug = "line-tables-only"`。

> **判断 2：今天 C++ 的验证成本就是完整构建，CI 冷构建约 9–10 分钟，笔记本约 30 分钟。Rust 移植后冷 `cargo check` 估计 1–10 分钟，冷 release 10–60 分钟，下限由最大的 sql crate 决定。这不够便宜，所以第 4 步保留，用构建守护进程。** 决定性文件：`CMakeLists.txt:118`（强制 unity build）、`src/observer/CMakeLists.txt:90`（`--start-group` 链接）、`cmake/Rust.cmake`、`rust/Cargo.toml`（`codegen-units = 1`）。

### 判断 3：测试能不能带走？

**测试资产普查**：

| 资产 | 规模 | 通过什么接口测试 | 能否带走 |
|---|---|---|---|
| mysqltest（`tools/deploy/mysql_test`） | 283 个用例（`t/` 下 93 个，30 个套件共 190 个）；CI 跑其中 272 个（`tools/deploy/mysqltest_config.yaml`） | 走 MySQL 协议的 SQL | 部分能，见下 |
| obtest（`tools/obtest`） | 500 个用例 | 封闭的 Java 测试框架，在多节点 OB 集群上跑 | 不能：500 个里 499 个依赖内部实现；还需要 python2，jar 版本对不上，34 个 include 文件不在仓库里 |
| C++ 单元测试（`unittest/`） | 现在只剩 2 个 `.gitignore` | — | 已经没了：首次推送（`2c524b542`）时有 1,439 个文件、3,485 个 TEST，2026 年陆续删除，最后一批在 `45cbbe9e1`（2026-08-21）里删掉，这个提交共删了 706 个文件，标题却是一个不相关的 SQL 修复。`docs/developer-guide/en/unittest.md` 还写着"CI 跑单元测试"，已经不属实 |
| `deps/oblib/unittest`、`tools/ob_error/test` | 共 3 个 gtest 文件 | C++ 头文件 | 随 C++ 一起消失 |
| rust/sql-nio | 3 个 `#[test]` | Rust 内部 | 只有保留这个 crate 时才有用；`cargo test` 不在当前 CI 里 |
| pyseekdb（外部仓库 oceanbase/pyseekdb） | 49 个集成测试文件，295 个测试 | Python SDK（嵌入式、服务器、OceanBase 三种模式） | 能 |

**CI 跑的 272 个 mysqltest 用例怎么分**：脚本是 `migration/feasibility/scripts/tests/classify.py`。一个用例等于 `.test` 文件加上它用 `--source` 引入的所有文件，去掉注释后再做匹配。

| 类别 | 数量 | 例子 |
|---|---|---|
| 干净：只用公开 SQL | **89** | `t/join_basic.test`、`test_suite/type_date/t/timestamp2.test`、`test_suite/delete/t/delete_range.test` |
| 公开但脆弱（依赖时序、确切的报错文本等） | 32 | — |
| 通过 SQL 依赖内部实现 | **151**（56%） | `t/create_using_type.test:21` 关联查询了 `oceanbase.__all_table`；`test_suite/fork_table/t/fork_table_chain.test:24,284` 用了 `ob_global_debug_sync` 和 `__all_ddl_task_status`；obtest 的 `fork_table/fork_table_restart_recovery.test` 用了 `ObForkTableTask` 内部的 DEBUG_SYNC 点和 `obs0.stop`/`nstart` |

依赖内部实现的方式（CI 内的用例数）：

| 方式 | 用例数 |
|---|---|
| 内部表（`__all_*`、`GV$/V$`、`DBA_OB_*`） | 91 |
| 内部 `ob_*` 会话变量 | 49 |
| EXPLAIN 计划文本 | 39–40 |
| `SHOW CREATE` 输出里的 OB 存储选项 | 37 |
| ALTER SYSTEM | 37 |
| OB 专有错误码 | 28 |
| 强制冻结/合并 | 20 |
| 隐藏索引表/列 | 14 |
| 下划线函数 | 14 |
| debug_sync / 错误注入 | 10 |
| 隐藏参数 | 9 |

另外还有 132 个用例要求报错文本完全一致。

几个要点：
- **主打功能的测试几乎全依赖内部实现**：geometry 39/39、fork_table 16/16、vector_index 17/21（另外 4 个是脆弱类）、fts_index 12/15。
- **每个用例跑之前都要执行的 `tools/deploy/init.sql` 本身就依赖内部实现**：里面有 12 个 `alter system set_tp` 错误注入点和 5 个隐藏参数。
- **有一批容易放出来的**：151 个里有 30 个只是被共用的 include 文件拖累。其中 19 个 geometry 用例都是因为 `import_default_srs_data_mysql.inc` 查询了 `oceanbase.__all_spatial_reference_systems`，改几个 include 就能放出来。
- **CI 重试会掩盖不稳定**：失败的用例最多重试 3 次（`.github/script/seekdb/mysqltest_for_seekdb.py:19`，`MAX_CASE_RETRIES = 3`）。即便如此，近 50 次 CI 运行里仍有 17 次失败。
- **完全没有测试覆盖的公开接口**：
  - 二进制预处理语句、TLS、协议压缩：runner 没有传 `--ps-protocol`、`--ssl`、`--compress`。
  - `FORK DATABASE`（唯一的用例被排除在 CI 外）、`DIFF TABLE`、`MERGE TABLE`、`DBMS_HYBRID_SEARCH`。
  - 命令行参数和配置文件、崩溃恢复、真实并发（只有 6 个用例）。
  - 嵌入式模式：已经在 `0a373ba54`（#1039，2026-07-16）从 master 移除，它的 C 接口只在 `feat/embedded-mode` 分支上。

**裁判要检查的对齐场景**（作为 `00b` 的种子）：

1. **向量检索**（HNSW、IVF、稀疏向量）：用固定种子生成数据；暴力检索结果完全一致，近似检索的 recall@k 和旧引擎对齐；检查 DML 之后的可见性和带过滤的 top-k。
2. **全文检索**：覆盖每种分词器，以及 `MATCH … AGAINST` 的自然语言模式和布尔模式；相关度排序在容差内一致。
3. **混合检索**：`DBMS_HYBRID_SEARCH.SEARCH`/`GET_SQL` 的 RRF 融合，id 和分数在误差范围内一致。
4. **`FORK TABLE` / `FORK DATABASE`**：快照隔离、带向量和全文索引的 fork、`DIFF` 输出、每种 `MERGE` 策略、错误码。
5. **ACID**：双会话的隔离异常矩阵、`FOR UPDATE` 超时、死锁报错。
6. **崩溃恢复**：提交后 SIGKILL（CI 启动脚本已经支持）再重启，检查已提交和未提交的数据；在 DDL 和 fork 过程中杀进程。
7. **MySQL 兼容**：89 个干净用例，加上和真实 MySQL 8.0 对比类型、排序规则、时区、标准错误码和报错文本。
8. **协议**：二进制预处理语句、TLS、压缩、认证、大量并发连接。
9. **嵌入式和服务器模式**：pyseekdb 的 295 个集成测试，改成跑本仓库构建出来的二进制。
10. **命令行和配置**：`--variable`，`--parameter` 重启后是否保留，数据和 redo 目录的布局，`seekdb.cnf`，`--role STANDBY`；只用公开参数。
11. **性能容差带**：sysbench、TPC-C、固定 recall 下的向量 QPS、全文检索基准、冷启动时间和内存占用。

> **判断 3：现有测试不能直接当裁判。** CI 的 272 个 mysqltest 只有 89 个是干净的公开 SQL，151 个通过 SQL 依赖内部实现（每个用例都要跑的 `init.sql` 也是）；obtest 在公开仓库里跑不起来；C++ 单元测试已经删除。**第 1 步之前必须先建裁判（`prompts/00b-judge-setup.md`）。** 决定性文件：`tools/deploy/mysqltest_config.yaml`、`tools/deploy/init.sql`、`.github/script/seekdb/mysqltest_for_seekdb.py`、`tools/obtest/`。

## 3. 六步在 seekdb 上的具体安排

规模基数（`migration/feasibility/results/units/`）：
- **需要翻译**：2,298,401 行。按「头文件 + 同名 `.ipp` + `.cpp`」划成 **3,753 个单元**，中位数 271 行，p90 1,319 行。最大的单元是 `src/rootserver/ob_ddl_service` 这一组，共 25,785 行。超过 2,000 行的单元有 212 个，占了 39% 的行数，应该按类或函数组再拆小。
- **按模块**：

  | 模块 | 单元数 | 行数 |
  |---|---|---|
  | sql | 1,288 | 943,882 |
  | storage | 801 | 484,209 |
  | oblib | 517 | 262,558 |
  | share | 415 | 236,822 |

- **不翻译的部分**：约 50 万行，占 src 的约 18%：
  - 内嵌数据 34.7 万行，其中 `src/storage/fts/dict/ob_ik_dic.cpp` 一个文件就有 276,043 行；
  - 已签入的生成代码 87,832 行；
  - 语法文件 24,585 行；
  - 第三方源码（zstd 1.3.8、xxhash）26,398 行；
  - 没有被构建的文件 10,816 行。
- **生成器**：`gen_errno.pl`、`gen_ob_sys_variables.py`、`generate_inner_table_schema.py`、bitpacking 编解码生成器、protoc、bison/flex，这些应该改成直接输出 Rust。

### 前置：建裁判（`prompts/00b-judge-setup.md`）

- **为什么必须先做**：见判断 3。没有裁判，就没有判定"迁完了"的标准。
- **在 seekdb 上具体要做的**：
  1. 把 `init.sql` 拆成公开部分和引擎专有部分。
  2. 改写或隔离 151 个依赖内部实现的用例，先做只是被 include 拖累的那 30 个。
  3. 写一个差分 runner，把同样的 SQL 发给新旧两个二进制，比较归一化后的结果。可以复用 `.github/script/seekdb/sdb.py` 的启动方式，它只依赖 `--base-dir`、`--port`、`--parameter`、`--nodaemon` 这几个参数和 MySQL 协议。
  4. 把 pyseekdb 的 295 个集成测试接到本仓库构建出来的二进制上。
  5. 补上上面列的 11 个对齐场景。
  6. 先在原始代码上跑到全部通过；再在故意改坏的原始代码上逐个确认裁判能抓到。每改坏一处都要重新构建一次旧二进制。
- **填好的占位符**：`[target language]` = Rust，`[reviewer model]` = `claude-opus-5-5`。
- **前提**：需要一台能构建 seekdb 的 Linux 机器，或者在这台 Mac 上把工具链装好（Homebrew、cmake、Rust 等，见 `docs/developer-guide/en/toolchain.md`）。

### 第 1 步：建立地图和规则

**依赖图（`prompts/01-dependency-map.md`）**

- **kit 自带的脚本不够用**：`depmap_c.py` 直接用在 seekdb 上只能找到 4,089 条边，实际有 25,017 条，因为它不会去 `src/`、`src/oblib/` 等 include 根目录里找文件。调查时写的改版 `migration/feasibility/scripts/units/depmap_seekdb.py` 按 CMake 的 include 根目录解析，覆盖了 99.5% 的引号 include，可以作为起点，但必须经过两名审查者的正式核对。
- **已知结构**：
  - 文件级有 30 个环，共涉及 75 个文件，最大的环有 8 个文件。
  - 顶层模块只有一个环 {sql, storage, rootserver, pl}，由 4 处 include 形成：`src/sql/engine/px/ob_px_sub_coord.cpp:30-31`、`src/storage/ddl/ob_tablet_slice_writer.cpp:27`、`src/storage/ddl/ob_ddl_insert_dag.cpp:22`。去掉这 4 处后模块图无环，和仓库自己的 Bazel 模块规则一致（`bazel/architecture/module_policy.bzl`）。
  - 子目录这一级几乎全部连成一团：sql 的 19 个子目录里有 18 个互相依赖，storage 的 33 个里有 30 个。
- **include 图看不到的依赖**：有 45 处 weak symbol "向上调用"，即底层模块给出默认实现、上层模块在链接时覆盖；还有 `--start-group` 暗示的符号级循环。所以 crate 的划分**必须按符号级依赖来验证**，不能只看 include。
- **填好的占位符**：
  - `[your dependency mechanism]` = 按 CMake include 根目录解析的 `#include "…"`，加上符号级引用（链接组、`OB_WEAK_SYMBOL`）。
  - `[crate / package / module]` = 每个顶层模块 `src/<module>` 一个 crate。
  - `[reviewer model]` = `claude-sonnet-5-5`，因为这里的核对是机械的：逐行给出 file:line 证据。
- **收尾**：用 `scripts/make_manifest.py` 生成 `migration/manifest.tsv`，每个头文件组一行，共 3,753 行。`--sub` 规则取决于 RULEBOOK §4 的命名规则，由你决定。

**设计文档**：重新设计模式下，它替代 `templates/RULEBOOK.md` 里的规则手册。需要你拍板的决定：
- 表达式、算子、计划树改用 arena + 索引，还是 `Rc<RefCell>`。
- 分配器模型：用 `'arena` 生命周期，还是用 id。
- 无锁结构用 crossbeam-epoch 重写，还是逐行翻译成 unsafe。
- MVCC 版本链和事务回调链表怎么表示。
- 全局单例（`GCTX`、`GCONF`、`THIS_WORKER`）怎么替换。
- 45 处 weak symbol 改成启动时显式注册回调。
- SQL 解析器先通过 FFI 保留，还是重写。
- 第三方库的去留：
  - vsag 继续通过 C 接口调用；
  - Boost.Geometry 是纯模板库，没法直接 FFI，是风险最高的一个；
  - s2geometry、ICU 继续通过 FFI 调用；
  - OpenSSL 换成 RustCrypto 或 rustls；
  - gRPC/protobuf 换成 tonic/prost。
- unsafe 逃生口（RULEBOOK §3）的使用规则。

**缺口清单（`prompts/02-gap-inventory.md`）**

- `[name your gap]` = 所有权与生命周期，具体包括 arena 分配、裸指针图、侵入式容器、手写引用计数、placement new 和手动析构；另外还有无锁结构的内存回收、线程局部变量和全局单例、weak symbol 向上调用，以及 union、位域、`reinterpret_cast` 这类类型双关。
- `[reviewer model]` = `claude-opus-5-5`。
- 规模约 2.7 万处：

  | 来源 | 处数 |
  |---|---|
  | `T *&` 输出参数 | 9,457 |
  | 裸指针成员 | 6,120 |
  | `reinterpret_cast` | 3,692 |
  | `ATOMIC_*` | 2,997 |
  | placement new | 2,074 |
  | 引用成员 | 1,278 |
  | 手动析构 | 1,265 |
  | union | 239 |
  | `this` 初始化 | 157 |
  | weak symbol | 45 |

### 第 2 步：压力测试规则（`prompts/03-stress-test.md`）

- **双译者对照在重新设计模式下失效**。按 README，换成两件事：对设计文档做对抗审查；在一个子系统上做一次性的廉价全量试跑，审查产出、修规则，然后扔掉。
- **试点**：用生产流水线跑 3 个最难的单元，按标准挑选。候选：
  - `src/sql/resolver/expr/ob_raw_expr.h` + `.cpp`：会被原地改写的 DAG。
  - `src/storage/memtable/mvcc/ob_mvcc_row.h` + `.cpp`：无锁版本链。
  - `src/oblib/lib/hash/ob_link_hashmap.h`：在指针上打标记，加 QClock 回收。
- **填好的占位符**：`[3]` = 3，`[target formatter]` = `rustfmt`，`[implementer model]` = `claude-sonnet-5-5`，`[reviewer model]` = `claude-opus-5-5`。
- **开始前需要你做的事**：把 `templates/settings.json` 复制到仓库的 `.claude/settings.json`，改成 seekdb 版。这一步只能由你来做。
  - 去掉 `npm test`、`npx tsc` 两条。
  - 保留对 `cargo build/check/test/run`、`make`、`cmake` 和会改动状态的 git 命令的禁用。
  - 增加禁用：`cargo clippy`、`./build.sh`、`bash build.sh`、`bazel`、`./tools/deploy/obd.sh`，以及 mysqltest 运行器 `.github/script/seekdb/mysqltest_for_seekdb.py`。

### 第 3 步：全量翻译（`prompts/04-translation-kickoff.md`）

- **队列**：`migration/manifest.tsv` 里还没有输出文件的行。工作单元是头文件组（3,753 个），批次门按子目录划分（147 个），crate 按顶层模块划分。
- **填好的占位符**：
  - `[100]` = 100，共约 38 个批次。
  - `[TODO(port)]` = `TODO(port)` / `PERF(port)` / `BUG(port)`，每处 unsafe 还要加 `SAFETY:` 注释。
  - `[implementer model]` = `claude-sonnet-5-5`，`[reviewer model]` = `claude-opus-5-5`。
- 这一步不跑编译器。

### 第 4 步：编译（`prompts/05-survey-build.md`）

- **不并入第 3 步**，原因见判断 2。
- **填好的占位符**：
  - `[build command]` = 在 Rust 工作区上跑 `cargo check --workspace --message-format=short`。
  - `[module]` = crate。
  - `[fixer model]` = `claude-sonnet-5-5`，`[reviewer model]` = `claude-opus-5-5`。
- **构建者**：用 `scripts/build_daemon.sh` 作唯一的构建者。
- **错误数量参考**：Bun 约 100 万行的移植，仅包级循环就带来约 1.6 万个编译错误（见 kit README）。seekdb 假设在 10⁴–10⁵ 量级。

### 第 5 步：跑起来

- **hello world**：`seekdb --nodaemon --base-dir <dir> --port <port>` 能启动并完成自举（内部表、系统租户、日志服务），然后通过 MySQL 协议回答 `SELECT 1`。
- **冒烟测试**：跑 89 个干净的 mysqltest 用例，`MAX_CASE_RETRIES` 设成 0。

### 第 6 步：行为对齐

- **裁判**：前置步骤建好的差分 runner、改写后的 mysqltest、pyseekdb 集成测试，以及上面的 11 个对齐场景。
- **完成条件**（两个数都要写进报告）：
  1. 裁判里的所有场景全部通过；
  2. 原测试在原代码上重跑，没有继承失败。重跑时必须关掉那 3 次重试。
- **收尾**：用 `prompts/06-post-parity.md` 清理 `BUG(port)`、`TODO(port)`、`PERF(port)` 标记。占位符 `[target tree]` = Rust 工作区目录（按 RULEBOOK §4 定），`[reviewer model]` = `claude-opus-5-5`。

## 4. 成本与工期

### 4.1 Token：只给量级

| 步骤 | 怎么算 | 量级 |
|---|---|---|
| 第 3 步：翻译 | 3,753 个单元 × 每个单元 5 个 agent（实现者、2 个审查者、仲裁、修复者；kit Run 3 实测是 7 个文件用了 35 个 agent）× 每个 agent 1–3×10⁵ token（假设：kit Run 3 约 5.8 万；seekdb 的单元平均约 612 行，上下文也更重） | 约 2×10⁹ – 6×10⁹ |
| 重新设计的一次性试跑 | 再加第 3 步的 0.5–1 倍（假设做 1–2 次廉价全量试跑） | 约 1×10⁹ – 6×10⁹ |
| 第 4 步：编译修复 | 10⁴–10⁵ 个错误（假设）÷ 每个修复者处理 20 个 × 3 个 agent × 10⁵ | 约 1.5×10⁸ – 1.5×10⁹ |
| 第 1–2 步 | 约 2.7 万处缺口 ÷ 每批 20 处 × 3 个 agent × 10⁵，再加设计文档和试点 | 约 4×10⁸ – 10⁹ |
| 前置：建裁判 | 约 180 个用例改写 × 3 个 agent × 10⁵，再加变异验证 | 约 10⁷ – 10⁸ |
| 第 6 步：行为对齐 | 10³–10⁴ 个失败（假设）× 3 个 agent × 10⁵ | 约 3×10⁸ – 3×10⁹ |
| 合计（默认 effort） | | 约 3×10⁹ – 1.5×10¹⁰ |
| **合计（全部 max effort，你的决定）** | 上一行 × 1.5–3（假设：max effort 下思考 token 更多、工具调用更多） | **约 5×10⁹ – 5×10¹⁰，即几十亿到几百亿 token** |

换算成钱：**约 1 万到 20 万美元**。所用假设：
- 实现者和修复者用 Sonnet 5.5，规则手册作者和审查者用 Opus 5.5；
- 缓存命中率 70–90%；
- max effort 下思考 token 计入输出，输出占比升到 10–20%，混合单价约每百万 token 2–4 美元；
- 如果缓存命中率低，再乘以 2–3。

即使如此，真正的瓶颈仍是日历时间和你的专注时间，不是 token 费用。

### 4.2 每一步的日历时间和你的专注时间

| 步骤 | 日历时间 | 你的专注时间 |
|---|---|---|
| 前置：建裁判（含搭建构建环境） | 1–3 周 | 15–30 小时 |
| 第 1 步：依赖图 | 2–4 天 | 3–6 小时 |
| 第 1 步：设计文档 | 2–4 周 | 30–60 小时（政策决定由你做） |
| 第 1 步：缺口清单 | 3–7 天 | 5–10 小时（回答 UNKNOWN 行） |
| 第 2 步：设计审查、试跑、试点（2 轮） | 1–2 周 | 8–16 小时 |
| 第 3 步：全量翻译（约 38 个批次门） | 2–6 周（受速率上限限制） | 10–20 小时 |
| 第 4 步：编译错误清零（假设 20–60 轮） | 1–3 周 | 5–15 小时 |
| 第 5 步：自举和冒烟测试 | 1–3 周 | 10–20 小时 |
| 第 6 步：行为对齐到完成条件 | 1–3 个月 | 40–100 小时 |
| 合计（默认 effort） | 约 4–8 个月 | 约 130–280 小时 |
| **合计（全部 max effort）** | **约 5–13 个月** | **约 130–280 小时** |

全部用 max effort 后，每个 agent 的回合更长、token 更多。第 3、4、6 步受 token 速率上限约束，默认 effort 下这三步合计约 1.7–4.9 个月，按 1.5–2 倍放大（假设）后，总日历时间约 5–13 个月。你的专注时间基本不受 effort 影响。

这些都是估算，依据是本报告统计的单元数，以及 kit 三次试跑的实测数据。那三次试跑最大的一次也只产出了 2,513 行 Rust，seekdb 比它们大三个数量级。

## 5. 模型计划（已由你在 2026-10-10 决定）

你的两项决定：
1. 设计文档 / 规则手册的起草和修订，从原先推荐的 Fable 5.1 改为 **Opus 5.5**。
2. **所有 agent 一律用 max effort**。在 Claude Code 里就是每次调用 Agent 都设 `effort: "max"`；直接调 API 时设 `output_config.effort = "max"`。

价格是每百万 token 的输入/输出价：

| 阶段 | 模型 | effort | 理由 |
|---|---|---|---|
| 设计文档 / 规则手册的起草和每次修订 | `claude-opus-5-5`（$4/$20） | max | 一次性工作，但任何错误都会复制进 3,753 个单元；重新设计模式下，它是影响面最大的产物 |
| 建裁判：改写者和对抗审查者 | `claude-opus-5-5` | max | 裁判错了，迁移的完成条件就失效了 |
| 依赖图审查者 | `claude-sonnet-5-5`（$2/$10） | max | 核对是机械的，只需要逐行给出 file:line 证据 |
| 缺口清单的提议者、审查者和联合审计 | `claude-opus-5-5` | max | 在 arena 分配的指针图里追踪所有权，是最难的一类缺口 |
| 设计文档的对抗审查、差异检查 | `claude-opus-5-5` | max | 要能识别出设计里的漏洞 |
| 翻译实现者 | `claude-sonnet-5-5` | max | 工作量最大，后面有两个审查者和编译器兜底。Haiku 5.5 对所有权重新设计偏弱（假设） |
| 翻译审查者、仲裁 | `claude-opus-5-5` | max | 缺口复杂度高 |
| 翻译、编译、行为对齐三个阶段的修复者 | `claude-sonnet-5-5` | max | 真正的判定者是编译器和裁判 |
| 机械扫描（统计站点、核对队列） | 用脚本；必须用模型时用 `claude-haiku-5-5`（$0.10/$0.50） | max | 不需要判断力 |

签字后，01–06 各个 prompt 里的占位符按这张表填：
- `[reviewer model]` = `claude-opus-5-5`；第 1 步依赖图例外，用 `claude-sonnet-5-5`。
- `[implementer model]` = `claude-sonnet-5-5`。
- `[fixer model]` = `claude-sonnet-5-5`。
- 规则手册 / 设计文档的作者 = `claude-opus-5-5`。
- **所有 agent：effort = `max`**。kit 的 prompt 里没有 effort 占位符，这一条作为附加要求。kit 规定每次调用子代理都要显式指定模型，沿用会话默认值算偏差、要记入偏差日志；effort 也按同样的规则处理。

这些决定对成本的影响见第 4 节。改用 Opus 5.5 写规则手册对总成本影响很小，这部分不到总量的 1%；全部改用 max effort 则会让 token 和日历时间明显增加。

## 6. 结论：以后再迁，不是现在

**为什么不是现在**：
1. **没有裁判**（判断 3）。kit 明确要求，没有裁判不能开始第 1 步。主打功能的测试几乎都依赖内部实现，建裁判本身就要 1–3 周，还需要一个能构建 seekdb 的环境。
2. **这是重新设计，不是逐行翻译**（判断 1）。kit 的机械化流程最适合保持结构的迁移。这里动手之前，要先由人拍板 7 组核心数据结构的新表示方式。
3. **在 fork 上长期分叉有风险**。上游近 3 个月有 492 个非合并提交，大约每天 5 个，而且上游自己也在把组件往 Rust 迁（#1457 刚合并）。如果只在 fork 上做一次 5–13 个月的全量迁移，做完时 C++ 原版早已走远，还得不停地把上游的改动翻译进来。
4. **bug 数据只能部分支撑**（第 1 节）。Rust 能挡住的修复约 15–20%，单凭这一点撑不起一次性重写的成本。

**"以后"的意思**：下面三件事都满足了，再启动第 1 步。
1. 裁判建好，并通过 `00b` 关口：在原代码上全部通过，每一处故意改坏都能被抓到。
2. 判断 1 列出的 7 组核心数据结构，设计决定已经拍板。
3. 和上游的关系已经弄清楚（见下文）。

**真做时的建议做法**：
- **按子系统逐个迁**：每个子系统在自己的边界内完整走一遍六步，并通过 C 接口和剩下的 C++ 共存，就像 sql-nio 那样。顺序沿用现有的 Bazel 分层，先从边界清晰、依赖少的部分开始。
- **不建议**照 kit 的原样把 230 万行一次性翻译完再编译：光 sql crate 就有约 94 万行，在全部翻完之前，不会有一个能跑起来的二进制。

**能改变结论的那一个事实**（仓库本身回答不了）：上游 seekdb 团队内部那个 "Migrate seekdb to Rust" 计划（PR #1246 的评论里提到过），到底是全量迁移还是逐个组件迁；有没有排期和人手；愿不愿意和这个 fork 上的工作协调。
- **如果是全量迁移、而且愿意协调**：结论改为"现在"。立即开始 `00b`，所有工作直接面向上游合入，就不存在分叉问题。
- **如果只是零星迁几个组件**：对这个 fork 来说，结论改为"不做全量迁移"，改成一个一个组件地给上游提 PR。

**一天内怎么核实**：直接问维护者。可以在 oceanbase/seekdb 开一个 issue，或者在 PR #1246 下留言问那个内部计划；也可以联系 #1246、#1457 的作者，或者去 Discord 问（README 里有链接）。如果你本人就在 seekdb 团队，直接去看内部的事项就行。

**如果你决定签字"迁"**：下一步是 `prompts/00b-judge-setup.md`。占位符 `[target language]` = Rust，`[reviewer model]` = `claude-opus-5-5`；输入是判断 3 的普查数据和上面的 11 个对齐场景。按 kit 的规定，由你来启动它。

## 7. 读了什么、跑了什么

**读了**：
- kit：`README.md`、`CLAUDE.md`、`RUN-NOTES.md`、`prompts/00`–`06`、`templates/RULEBOOK.md`、`templates/settings.json` 和 `settings.README.md`、`scripts/depmap_c.py`、`scripts/make_manifest.py`。
- seekdb 文档：`README.md`、`AGENTS.md`，以及 `docs/developer-guide/en/` 下的 `build-and-run.md`、`toolchain.md`、`mysqltest.md`、`unittest.md`、`coding-convention.md`。
- seekdb 构建和 CI 配置：`.github/workflows/` 下的 `compile.yml`、`seekdb.yml`、`rust-checks.yml`；`rust/Cargo.toml`、`rust/sql-nio/Cargo.toml`。
- 第 2 节列出的所有决定性文件（抽读，带行号）。

**跑了**：
- 只读的 git 命令：`git ls-files`、`git log`、`git show`、`git patch-id`、`git ls-tree`，以及 `wc`、`grep`。
- `gh` 的只读 API：oceanbase/seekdb 的 PR 和 issue、近千次 CI 运行的作业和步骤耗时、部分 CI 日志。
- 5 个只读调查子代理，分别负责痛点、架构、测试、构建基线、依赖图与工作单元。它们在任务的临时目录里写分析脚本，用 `python3 -I` 运行。
- 我自己抽查核实的内容：
  - 决定性文件都存在；
  - `int ret = OB_SUCCESS;` 37,299 处，`OB_FAIL(` 100,004 处，`ATOMIC_*` 3,003 处；
  - 形成模块环的 4 处 include；
  - `45cbbe9e1` 删除了 706 个文件；
  - CI 用例清单是 272 个；
  - PR #1246、#1457、#1412 的状态；
  - 上游领先 9 个提交；
  - 第 1 节、第 2 节引用的若干行号。

**抽样和跳过的**：
- 修复分类只读了关键词命中的 diff 和 30 个随机抽样。
- 架构统计是去掉注释后的正则计数，头文件里的内联方法没有扫。
- 依赖图只做到 include 级；符号级依赖只统计了 weak symbol。
- CodeQL 默认配置分析了哪些语言，没有核实。
- 生成的 inner table 代码量没有实际运行生成器去测（估计不少于 4.5 万行）。
- `834bbee1e` 之后上游的 9 个提交没有覆盖。

**和 kit 流程的偏差**（建议将来记进 RULEBOOK §7 的偏差日志）：

| ID | 偏差 |
|---|---|
| DEV-001 | 没有在本机计时构建：这台 Mac 没有 Homebrew、cmake 和 Rust 工具链，装 Homebrew 需要 sudo。改用 CI 的实测数据作基线。 |
| DEV-002 | kit 规定这一步只写 `migration/cost-log.tsv`。因为你要求在新分支上做迁移，报告本身（本文件）和证据脚本（`migration/feasibility/`）也提交到了 `rust-migration` 分支。没有改动任何源码。 |
| DEV-003 | 5 个调查子代理用的是会话默认模型 `claude-opus-5-5`。模型计划要到这个关口才定，所以调查时还没有计划可以遵循。 |

**证据**：
- `migration/feasibility/scripts/<领域>/`：分析脚本。用法：设置 `REPO=<seekdb 的 checkout 路径>`，用 `python3 -I` 运行。
- `migration/feasibility/results/<领域>/`：精简后的结果表。
- 子代理 token 消耗合计 1,438,032，不含主会话。
