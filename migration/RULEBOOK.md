# seekdb C++ → Rust：设计文档（重新设计模式下的规则手册）

> **元规则：两个 agent 可能给出不同答案的问题，答案都写在这里。** 实现者、审查者、修复者动手之前必须通读全文。本文件在任何循环内都只读；修订排队交给人，在批次之间应用。
>
> - **状态**：草稿 v0.1（2026-10-11），**等你在设计文档关口拍板**。
>   - 标【D1】…【D14】的是需要你决定的核心政策，正文先按推荐项写；
>   - 附录 A.2 是其余的决定，按推荐执行，你可以随时推翻其中任何一项；
>   - 拍板之前，不开始缺口清单和第 2 步。
> - **基线**：上游 master `7d907abfa`。
> - **事实来源**：
>   - `migration/design/survey/` 下的 7 份调查备忘，附复现脚本：ir-graphs、memory、concurrency、storage-txn、layering、parser-generators-thirdparty、conventions；
>   - `migration/00-feasibility-report.md`、`migration/00b-judge-report.md`、`migration/01-depmap-report.md`。
>   - 本文引用的数字都出自这些文件，正文不再逐条注明。
> - **读者**：第 3 步的实现者（`claude-sonnet-5-5`）和审查者（`claude-opus-5-5`），第 4 步的修复者。第 0 节讲新架构是什么，第 1–6 节是逐条执行的规则。

## 0. 架构（取代模板里的"范围与姿态"）

### 0.1 姿态

1. **这是重新设计，不是逐行直译。**
   - 重新设计的范围：所有权和数据结构层，按本节执行。
   - 保持不变的：模块划分、算法、策略常数、错误码、用户可见的行为。
   - 依据：可行性报告判断 1。按原结构直译时，57,226 个函数体里有 45.1% 需要 unsafe。
2. **行为第一，性能第二。**
   - 验收标准只有两个：00b 裁判（287 个 mysqltest 用例加 7 个生命周期场景），以及第 6 步的对齐。
   - 已知较慢但忠实的写法，标 `PERF(port):` 后照样往前走。
3. **翻译阶段不跑编译器。** 草稿不必能编译；第 4 步按 crate 分波次编译（0.2）。
4. **BUG 规则。**
   - 原版的缺陷照样复刻，标 `BUG(port): <复现方法>`，包括 00b 报告第 6 节 D 的 15 条疑似 bug。
   - **唯一的例外**是在 Rust 里会构成数据竞争的写法，例如运行期改写共享计划（0.6）。这类写法改成在单线程下行为等价的形式，同样标 `BUG(port)`。
   - 修复只在对齐之后单独提交（`prompts/06`）。
5. **UNKNOWN 规则。** 本文和缺口清单都没有决定的情况：
   - 采用目标语言里最保守的表示；
   - 标 `TODO(port): <问题>`；
   - 然后继续往前走，不要停下批次。

### 0.2 迁移策略【D1】

**核心一次性翻译，再按 crate 分波次编译。**

1. **第 3 步**：按 `migration/manifest.tsv` 翻译全部单元（头文件组），不跑编译器。
2. **第 4 步**：按 crate 依赖顺序，分波次 `cargo check --keep-going`。上一波错误清零，才进入下一波：
   1. oblib
   2. share
   3. data_plane、logservice、query
   4. sql、storage（两者并行）
   5. rootserver
   6. standby
   7. observer
3. **第 5 步**：启动、冒烟。**第 6 步**：裁判对齐。

**不做按模块的 C ABI 共存**：

- 原因有三：
  - oblib 有 48% 的代码在头文件里，有 2,262 个模板；
  - C++ 上层对 oblib 有 4,641 条 include 边，链接层还有约 3,685 个"上层引用 oblib"的符号；
  - api 层有 797 处 `virtual`、433 处 `ObIAllocator`。
- 这些都放不到 C ABI 后面；硬要共存，就得同时维护两份逐位一致的 oblib。
- C ABI 只用于接口窄的孤岛：现有的 `config`、`sql-nio`，以及 C 解析器和第三方库的 -sys/shim crate。

**代价**：第一次能跑起来要等全部 crate 编译通过。所以第 2 步的"一次性全量试跑"格外重要：在重新设计模式下，它取代双译者对照（kit README）。

### 0.3 crate 图【D2】

所有移植 crate 放在 `rust/port/<crate>/`，作为 `rust/Cargo.toml` 工作区的成员。

| crate | 来自 | 直接依赖（移植 crate） | 说明 |
|---|---|---|---|
| `ob_macros` | 新建，proc-macro | — | `ob_printf!` 等编译期宏（§1） |
| `oblib` | `src/oblib` | `ob_macros`、`config` | 最底层。新写的基础设施也放这里：`mem`、`sync`、`thread`、`error`、`log`、`fmt`、`container`、`serialization`、`slot`（0.5–0.8） |
| `share` | `src/share` | `oblib` | |
| `data_plane` | `src/data_plane`（几乎全是 api 头文件） | `oblib`、`share` | |
| `logservice` | `src/logservice` | `oblib`、`share` | |
| `query` | `src/query` + `src/objit` | `oblib`、`share` | `ItemType` 的唯一定义；运行期表达式 `RtExpr` 也放这里（0.6） |
| `parser` | 新建：SQL/PL/全文检索的 C 解析器（build.rs 生成并编译）+ `ParseNode` 视图 | `oblib`、`query` | 0.12 |
| `sql` | `src/sql` + `src/pl` | `oblib`、`share`、`data_plane`、`query`、`parser` | pl 并入 sql（0.4） |
| `storage_core` | 新建，从 storage 抽出的 unsafe 核心 | `oblib` | 0.9 |
| `storage` | `src/storage` | `oblib`、`share`、`data_plane`、`logservice`、`query`、`storage_core` | **不依赖 sql** |
| `rootserver` | `src/rootserver` | 以上全部 | |
| `standby` | `src/standby` | `oblib`、`share`、`logservice`、`storage` | gRPC 用 tonic（0.14） |
| `observer` | `src/observer` | 以上全部 | 二进制，组合根（0.5） |
| -sys / shim | 新建 | 视库而定 | `zstd138_sys`、`icu69_sys`、`libxml2_sys`、`s2_shim`、`vsag_shim`、`bgk_shim`（Boost.Geometry）、`libaio_sys`；SQLite 用 `rusqlite` |
| 已有 | `rust/config`、`rust/sql-nio` | — | 不改动 |

**规则**：

- **依赖方向**：等于 include 级 DAG（`migration/depmap/crates/edges.tsv`），也符合 Bazel 模块策略。sql 与 storage 互不依赖（`bazel/architecture/module_policy.bzl:79-91`）。
- **sql 暂时不拆**：它约 100 万行 C++，占全部需翻译代码的约四成。
  - 第 2 步用试译的 30–50 万行代码，在 16 GB 笔记本上实测冷 `cargo check` 的时间和峰值内存；
  - 超过 10 分钟或 12 GB 时，沿 IR 边界切出 `sql_ir`/`sql_engine`。
- **链接**：所有 Rust crate 合成一个 staticlib，避免每个 crate 各带一份 std。
- **cargo 配置**：
  - 工具链保持 1.98.1，edition 2021；
  - 保留 `panic = "abort"`；
  - 参与对齐的 profile 一律 `overflow-checks = false`（§2.13）；
  - 大 crate 单独设置 `codegen-units`；
  - 开发构建用 `debug = "line-tables-only"`。

### 0.4 跨 crate 依赖：seam 表【D3】

**现状**：链接级有 741 个成环符号。
- 来源：12 条逆向边上 364 个，sql↔storage 横向 2 环上 377 个。
- 76% 的形态是"声明在 `data_plane/api` 或 `query/api`、定义在上层"。
- 78% 是"类型在下层、方法体编译在上层"，而 Rust 不能跨 crate 写 inherent impl。

**拆法按优先级选用**，每个符号只用一种：

1. **合并**：只用于 sql+pl。
   - pl 的声明已经在 `src/sql/pl`，`src/pl/*.h` 大多只是 20 行的转发头。
   - 做法：pl 并入 sql crate，同名的 `src/sql/pl/X.h` 和 `src/pl/X.cpp` 合成一个单元（§4）。
2. **下移**：方法体只用到下层的东西时，把它搬进类型所在的 crate。manifest 为此做函数级重映射。
   - 典型：值类型的构造、`reset`、`assign`，`ObReqTimeInfo`，`ObSqlEndTransCb`，`print_service_status`。
3. **trait + 类型化槽位**：
   - 下层定义 trait 和 `Slot<dyn Trait>`，上层实现，组合根绑定。
   - 自由函数保留同名的转发函数，所以调用点不用改。
   - 槽位为空时，返回与 C++ 空指针分支相同的错误码（`OB_NOT_INIT`、`OB_NOT_SUPPORTED`）。
   - 句柄型的函数（如 `tx_desc_*`）改用句柄 trait，例如 `Arc<dyn TxDesc>`。
4. **链接期 seam**：由宏从同一个 trait 生成，下层 `extern "Rust"` 声明，上层 `#[unsafe(export_name = …)]` 定义。
   - 只用于经过测量确认的热路径，标 `PERF(port)`。

**seam 表 `migration/design/seams.tsv`**：

- 由脚本根据 nm 结果和头文件生成，每个符号一行，写明：声明处和定义处的 file:line、调用方、形状、选用的拆法、Rust 落点。
- 和依赖图一样，要经过两名审查者核对。
- 在缺口清单阶段完成；翻译时照表执行，不自行决定。
- 链接级数据目前只有 macOS 构建，生成 seam 表之前要在 Linux 构建上补跑一次（DEV-007）。

**逐边方案**（工作量是 agent 工作量的估计：S ≤ 2 天，M ≈ 1 周，L ≥ 2 周）：

| 边 | 拆法 | 工作量 |
|---|---|---|
| oblib→share（4 个函数） | `ob_strerror`：下移，错误表由生成器写进 oblib。<br>`get_cpu_count`：下移，直接读 `config::cpu_count()`。<br>`allow_next_syslog`、`current_resource_owner_id`：oblib 里设 `OnceLock<fn>` 钩子，默认就是现在 weak 版本的函数体，由 share 注册 | S |
| oblib→observer（1） | trait `InnerConnFactory` + 槽位；空槽返回 `OB_NOT_SUPPORTED`，与现在 weak 版本的函数体一致 | S |
| oblib→storage（1） | oblib 设钩子 `request_finish_callback`（默认什么也不做），由 storage 注册；observer 和 sql 改调 oblib 的包装函数 | S |
| share→observer（2） | `print_service_status` 下移回 share；ex_rpc 用 trait `TaskDispatcher` + 槽位 | S |
| sql→pl（152） | 合并 | S，只需改 manifest |
| 指向 observer 的向量索引（169） | query 定义 `VectorIndexUtil`（约 70 个方法）、`VectorIndexAdaptor`、结果迭代器、IVF cache 等 trait 和槽位，实现留在 observer；只依赖 schema 和参数的辅助函数下移到 query | L |
| 指向 observer 的其余部分（27） | end_trans_cb 下移到 sql；req_time 下移到 query；inner SQL 由 query 定义 trait `InnerSqlAccess`；tablet report 由 data_plane 定义 trait `TabletReport`；ex_rpc 同上 | S–M |
| sql、storage→rootserver（5） | data_plane 定义 trait `DdlCoordinator`（5 个函数），由 rootserver 实现 | S |
| sql↔storage 横向（377） | 两个方向都反转。<br>• 句柄型（`tx_desc_*`、lob、fts 迭代器）用句柄 trait；<br>• 服务型（`tmp_file_*`、锁）用槽位，空槽返回 `OB_NOT_INIT`；<br>• 值类型的方法体下移到 data_plane 或 query；<br>• `ObExpr` 和下压过滤器族按 0.6 处理 | L |

**weak 符号**：
- 生产构建里真正被覆盖的只有 6 个，按上表处理。
- 其余约 34 处 weak 定义按普通函数翻译，包括从未有强定义的测试缝，以及只在 Linux 上编译的。

**main 之前的初始化**：11 处 `__attribute__((constructor/init_priority))`，以及 268 个目标文件里的动态初始化。Rust 没有 main 之前的初始化，有副作用的（例如注册）一律改成组合根里的显式调用。

### 0.5 进程级状态

全局对象用 `OnceLock`/`LazyLock` 静态量加类型化槽位，一对一映射 C++ 现有的单一组合根。

| C++ | Rust |
|---|---|
| `GCTX.xxx_`（919 处，只用到 16 个成员） | `share::GCTX: LazyLock<GlobalContext>`。指针成员是 `OnceLock<&'static T>`，读取返回 `Option`，保留 `OB_ISNULL` 分支；运行时会变的状态（`status_`、`start_service_time_` 等）用原子量 |
| `share::server_service<T>()`（1,222 处，102 种类型） | 类型所在 crate 里的槽位，如 `storage::LS_SERVICE.get()`；`oblib::slot::Slot<T>` 是通用槽位类型 |
| `X::get_instance()`（585 处） | `X::instance()`，背后是 `static LazyLock<X>` |
| `GCONF` | 基线已经清零，参数框架已迁到 `rust/config`；直接调用 `config` crate 的 getter |
| MTL / 多租户 | 基线已经移除，不需要对应物 |

- **组合根**：在 observer。
  - 照抄 `obs_construct_modules`（构造 64 个模块、绑定 98 个槽位）→ `obs_init_modules` → `obs_start_modules` 的顺序；
  - 出错时按 stop → wait → destroy 的顺序收尾；
  - 纯函数式的 seam 和 weak 钩子在 `main` 的第一行注册。
- **不支持解绑和进程内重启**，与当前 master 一致：嵌入式模式已经移除。
- **线程局部状态**：
  - `THIS_WORKER`（389 处）改成 `thread_local!` 里的 `WorkerCtx`，每个请求用 RAII 装入、恢复；
  - `check_status` 一类的上层逻辑，改用启动时注册的钩子；
  - `ObCurTraceId`、警告缓冲区、`CURRENT_CONTEXT` 同理。
- **`Sync` 说明**：放进静态量或槽位的类型必须是 `Sync`，在类型定义旁写一行理由。

### 0.6 SQL 编译期与运行期对象图【D6】

**编译期**：ObRawExpr、语句、TableItem、逻辑算子、Path。

- **每条语句一个 `QueryArena`**，图内一律用类型化 u32 id：`ExprId`、`StmtId`、`TableItemId`、`LogOpId`、`PathId`。
  - id 是 `NonZeroU32` 的包装，所以 `Option<Id>` 仍然是 4 字节，可以直接对应可空指针。
  - 理由：C++ 本来就是"arena 整体释放、对象身份就是语义"。`ObSharedExprResolver` 会合并结构相同的表达式，代码生成也按对象身份去重。
- **原地改写**：2,336 处 `get_param_expr` 调用里约 95% 只是读，真正写入的只有 50–120 处；改用 id 后完全不需要 unsafe。
- **临时上下文**：7 处 `CREATE_WITH_TEMP_CONTEXT`、33 处局部工厂，改成可截断的子作用域。
  - 子作用域结束时，丢弃其中新建的节点，但保留对外层节点的修改。这和 C++ 释放临时 MemoryContext 的效果完全一样。
- **语句回收**：C++ 的语句工厂有 free list，Rust 用 `slotmap` 的代际 key，旧 id 访问会报错。

**运行期计划**：

- **共享方式**：用 `Arc<PhysicalPlan>`，构建后只读，`Send + Sync`。
  - 内部用 `RtExprId`/`SpecId`，与 `ob_expr.h:1317` 的"下标+1"编码一致；
  - 统计字段改成原子量（`ob_physical_plan.cpp` 里有 52 处 `ATOMIC_`）。
- **不再做计划和表达式的字节序列化**：C++ 的序列化本来就只在进程内有效（函数指针按地址写），PX worker 默认共享同一个计划。`_px_worker_share_plan_enabled=False` 时改成深拷贝。
- **计划内存大小**：它参与入缓存判断和淘汰（`ob_plan_cache.cpp:892`）。Rust 版要定义等价的统计口径；相关视图里的数值，裁判要做归一化。

**执行期**：

- **算子树**：拥有式的 `Box<dyn Operator>` 树。每次调用传入 `&PhysicalPlan` 和 `&mut ExecRuntime`，计划本身不存成引用。
- **28 处跨算子旁路**：主要在 `engine/px`，包括 SPF 子查询迭代器和 PX op input，挪进 `ExecRuntime` 的 id 表。
  - 子查询表达式在求值时会拿到正在执行的 SPF 算子（`ob_expr_subquery_ref.cpp:312-322`）；如果算子放进 `RefCell` 表，这里必然 panic，所以算子树不全用 id。
- **帧**：保留 C++ 的字节帧布局。unsafe 只出现在帧模块里，估计 20–40 处。
- **表达式种类**：
  - 573 种表达式算子：零大小结构体，加一张以 `ItemType` 为下标的静态注册表，由 528 个 `REG_OP` 一一生成；运行期仍然只用函数指针 `EvalFn`。
  - 封闭的小集合：RawExpr 约 27 种、LogOp 33、Path 10、OpSpec 61，用 enum 加公共头结构。
- **运行期写共享计划的地方**：已确认一处，`ObExprColumnConv::eval_enumset` 会临时改写共享表达式的 `args_[0]`（`ob_expr_column_conv.cpp:742-746`），这在 C++ 里是数据竞争。
  - 改成每次执行的局部副本，标 `BUG(port)`；
  - 另有 31 处 `extra_info_` 的非 const 转换和 6 处 `const_cast<…Spec>` 待缺口清单逐处审查。
- **按地址顺序迭代的 3 处**：`ob_raw_expr_copier.cpp:336`、`ob_raw_expr_replacer.cpp:379`、`ob_transform_utils.cpp:7953`。
  - 改成按插入顺序，标 `PARITY(port)`；
  - 裁判对受影响的 EXPLAIN 输出做归一化。

具体译法见 §2.6 的 R1–R8。

### 0.7 内存模型【D7】

- **查询作用域**：
  - 请求 arena 是 `oblib::mem::Arena`，内部是 bumpalo 3.20，翻译代码只看这层外观；每个请求一个，对应 C++ 的 `CREATE_WITH_TEMP_CONTEXT`。
  - 查询作用域的类型带 `'q` 生命周期。
  - **arena 里的类型不能需要 Drop**：`try_alloc` 用编译期断言拒绝这类类型。
  - 增长期间用 `ArenaVec<'q, T>`，定型后转成 `&'q [T]`。
  - 批次临时 arena 归算子所有，在批次边界 `reset`。
- **长生命周期对象**：会话、计划缓存、schema、存储元数据等，一律改成拥有型（`Box`/`Vec`/`Arc`），走全局分配器。随之消失的有：`ObIAllocator` 参数、placement new、手动析构。
- **全局分配器**：
  - `#[global_allocator]` 是自带 jemalloc（`seekdb-jemalloc-sys`）上的一层薄封装；
  - 加一个线程局部的 `MemAttr`（label、ctx），由 `MemAttrGuard` 设置，只按 ctx 计数、供诊断；
  - **不设硬上限**。这与 C++ 默认的 jemalloc 后端一致：它完全忽略 `ObMemAttr`，基线里也从来没有设置过硬上限。
- **限额**：用显式的 `MemBudget`，只在 C++ 原来的检查点检查。
  - 单查询上限：错误 11049，原来有 9 个检查点，计入 arena 的占用和算子报告的用量；
  - 工作区落盘：35 个文件；
  - 以及 `vector_memory_limit`、memstore、kvcache 的模块预算。
- **分配失败**：`OB_ALLOCATE_MEMORY_FAILED` 映射为错误码 -4013。
  - arena 分配一律用 `try_*`；
  - 容器的 `push_back` 内部用 `try_reserve`；
  - 大小取决于用户数据、或超过 64 KB 的缓冲，必须用 `try_reserve`；
  - 小的固定大小分配允许不可失败：在 `panic = "abort"` 下 OOM 就是 abort。这一点与 C++ 不同（C++ 是本语句报 4013），作为已知偏差接受。理由是裁判不覆盖分配失败，默认配置下两者通常都会先遇到 OOM killer。
- **不依赖 allocator_api**：钉住的 1.98.1 里没有；即使 1.100 稳定了 `Allocator` trait，`Box::try_new` 等仍不稳定。
- **288 个"拥有分配器、又借用它"的长生命周期类**，以及 schema 的扁平化拷贝、会话的字符串池，改成拥有型字段。逐类的改法由缺口清单给出。
- **内存视图**（`V$OB_MEMORY` 等）：保留表结构，内容由 Rust 的统计填充；裁判只比较表结构。
- **`SMART_VAR`/`HEAP_VAR`**：一律放到堆上。

### 0.8 并发模型【D8】

- **线程**：
  - 保留普通 OS 线程和阻塞式 worker。seekdb 空闲时实测 80–90 个线程，没有协程，也没有 TG 框架。
  - **禁用 async**，唯一的例外是 standby 的 gRPC（tonic）：在 standby 内部起一个独立的 tokio runtime。
  - `oblib::thread` 提供 `Threads`、`SimplePool<T>`、`Timer`/`TimerService`、`ReentrantThread`、`AdaptivePool`。
  - 策略常数照抄：worker 的上下限公式、空闲 10 秒收缩、停滞 3 秒救援扩容、TimerWK 4–128 个、默认栈 256 KB。
- **分段栈**：`SMART_CALL`（约 1,130 处）改成 `smart_call!`，基于 `stacker`。
  - 剩余栈不足 64 KB（`_LARGE` 为 192 KB）时扩栈，每次 2 MB；
  - 每个线程累计超过 10 MB 时返回 `OB_SIZE_OVERFLOW`。
  - Rust 栈帧通常更大，第 5 步实测之前不放宽这个上限。
- **锁**：`oblib::sync` 提供 `Latch<T>`、`RwLatch<T>`、`ShardedRwLatch<T>`、`Monitor<T>`、`ReentrantLatch`、`bitlock`，底层先用 `parking_lot`。
  - **保留**：
    - 来自 204 项 latch 表的 `LatchId`；
    - 带截止时间的加锁，超时返回 `OB_TIMEOUT`。DDL 和 Root Service 的两个串行锁用语句截止时间加锁，超时报 4012，用户可见，所以不能用 `std::sync`；
    - 写锁降级；
    - READ_PREFER，用 `read_recursive` 实现。
  - **备选**：性能带不达标时，在同一个包装内换成移植的 ObLatch 自旋加 futex 语义，调用点不用改。
  - **数据放进锁里**，规则见 §2.7。
  - **加锁顺序原样保留**，不重排；debug 构建打开 `parking_lot` 的 `deadlock_detection`。
- **原子操作**：
  - 一律 `SeqCst`。2,962 处 `ATOMIC_*` 中 99% 在 C++ 里本来就是 seq_cst 或 `__sync` 全屏障；
  - 29 处显式弱序照原样写；
  - 凡是被原子访问过的字段，类型改成 `Atomic*`（§2.7）；
  - 弱化 ordering 只在对齐之后做，要有 profile 证据和 loom 测试，标 `PERF(port)`。
- **无锁结构**：
  - 队列：crossbeam-channel/queue；
  - `ObLinkHashMap`/`ObLinearHashMap`/`FixedHash2`：`DashMap<K, Arc<V>>`；
  - "换掉指针后等读者退出"的模式：`arc-swap`；
  - 自己写的结构（keybtree、KV cache 的 map）：crossbeam-epoch，加"退休预算"。待回收超过 1024 个时，在放掉 guard 后反复 flush 并让出 CPU，以保住 C++ RetireStation 的有界内存；
  - KV cache 的内存块：`Arc`；
  - C++ 的四套自研回收方案都不移植。
- **信号**：
  - `ObSignalHandle` 改用 `nix` 实现，保留：SIGPIPE 忽略，SIGTERM → `raise(SIGKILL)`（生命周期场景会测到），SIGUSR1 优雅停机，实时信号 40–64；
  - `memory_dump` 里的 `siglongjmp` 改成不依赖信号的内存探测：Linux 用 `process_vm_readv`，macOS 用 `mach_vm_read_overwrite`。
- **不翻译的死代码**：
  - `ob_bucket_qsync_lock`、`ob_delay_free_allocator`、`ob_map_queue_thread_pool`、`ob_ms_queue`（连同 `ob_co_seq_queue`）、`ob_ring_array`；
  - 没有调用者的 `install_ob_signal_handler`、`do_with_crash_restore`。
  - 这些都由缺口清单逐一确认。

### 0.9 存储与事务【D9】

| 结构 | 做法 | unsafe |
|---|---|---|
| MVCC 版本链、`ObMvccRow`、行锁 | 进 `storage_core`。<br>• 节点 `#[repr(C)]`，链接字段是 `AtomicPtr`；<br>• 只能经 `NodeRef<'m>`/`RowGuard<'m>` 访问，`'m` 绑定在持有 `Arc<Memtable>` 的 guard 上；<br>• 节点随整个 memtable 一起释放，所以版本链不需要 epoch 回收 | 在核心内 |
| keybtree | 进 `storage_core`。<br>• **按原算法移植**：它的切分点和行数估计会进入并行扫描的切分和优化器；<br>• 回收用 crossbeam-epoch，延迟闭包持有 `Arc<NodePool>`；<br>• 扫描每 225 行重新 pin 一次 | 在核心内 |
| 事务回调链表 | 进 `storage_core`。<br>• 侵入式原子链接，保留三把锁的并发模式；<br>• 回调是闭合的 enum，共三种 | 只有链接操作 |
| 回调里指向行、节点、memtable 的指针 | 保留裸指针，以及"销毁 memtable 前先摘掉所有指向它的回调"的协议（`ob_memtable.cpp:166-181`） | 在核心内 |
| memtable arena | 进 `storage_core`。<br>• 移植 `ObFifoArena`：并发分配、整块释放；<br>• 对接 memstore 的限流计量 | 在核心内 |
| palf 组缓冲、LSN 分配器 | 进 `storage_core`。<br>• 组缓冲：在 `UnsafeCell<[u8]>` 上做不相交的写；<br>• LSN 分配器：`portable_atomic::AtomicU128` | 少量 |
| tx ctx 表、tx desc、表锁对象、tx data | `Arc`，加桶数组，每桶一把 `RwLatch`；tx ctx 表仍是 1<<14 个桶 | 0 |
| tablet/table/sstable 句柄、t3m | `Arc` 加 `arc-swap`。<br>• "引用归零后推进 GC 队列"放进 `Drop`；<br>• tablet 自带的 arena 改成拥有型字段 | 0 |
| 宏块引用 | 记的是磁盘块，不是内存，所以保持 `MacroBlockId → AtomicI64` 的计数表 | 0 |
| MDS、表锁内部的链表、日志回调池 | `slab`，加索引和锁；MDS 的 6 种单元改用 enum 擦除类型 | 0 |
| palf 滑动窗口 | 安全 Rust；`SlotGuard` 的 `Drop` 就是 `revert` | 0 |

- **`storage` crate 加 `#![forbid(unsafe_code)]`**。MVCC 的规则（`mvcc_write_` 的五种情况、`lock_for_read`、cleanout、ELR）都用核心 crate 的安全 API 写。
- **对象池**（tablet、tx ctx、日志回调）首版去掉，但保留数量上限和对应的错误码，例如 `OB_TRANS_CTX_COUNT_REACH_LIMIT`。
- **排期**：
  - `storage_core` 要先于 storage 的批量翻译完成，API 冻结，并配好 loom/Miri 测试；
  - {memtable, tx, tx_table, tx_storage, tablelock, deadlock, concurrency_control}（约 10.4 万行）彼此共享裸指针，要在同一个批次窗口里翻译。

### 0.10 持久化格式【D4】

**Rust 版读写与 C++ 版相同的格式，能直接打开 C++ 版写下的数据目录。**

- **范围**：
  - 日志与元数据：clog（palf）、slog、事务表、MDS 数据源 ID；
  - block_file：超级块、宏块和微块的头、编码微块、索引块；
  - 序列化：unis 序列化，由逐字节一致的 derive 宏实现；
  - 约 25 个按内存布局直接落盘的头：`#[repr(C)]` 加位域访问函数，每个配 golden 文件；
  - 校验与哈希：`ob_crc64`（含 SSE4.2 路径），palf 日志头用的 XXH64；
  - 压缩：zstd 1.3.8（OceanBase 补丁版）、zlib 1.2.13；
  - 其他：bitpacking 布局、S2 cell id、SQLite 元数据文件、vsag 的序列化格式。
- **迁移期间格式冻结**。Rust 版如果要改格式，只能走版本号。
- **裁判增加跨二进制场景**：C++ 写、Rust 读，分正常重启和 kill -9 两种。
- **`COMPRESS()` 的输出字节保持不变。**

### 0.11 必须逐字节一致的用户可见面【D5】

1. **错误**：
   - 错误码、SQLSTATE、报错文本（按 512 字节 `vsnprintf` 的语义），以及警告缓冲区"错误码相等才用缓冲区文本"的规则（§2.3）；
   - 裁判必须关闭 `enable_rich_error_msg`。
2. **`TO_STRING` 的输出格式**：`cmp_meta()` 会把它原样返回给用户（§2.11）。
3. **没有 ORDER BY、或 ORDER BY 有并列值时的行序**：
   - `ObHashMap`/`ObHashSet` 和 SQL 引擎自带的哈希表逐行移植：桶数、取模、头插、哈希函数都不变；
   - `ob_sort` 逐行移植**基准平台**的 `std::sort`。
     - 基准平台定为 macOS（libc++），因为裁判的期望输出在那里录制；
     - 前提：在 Linux 上复跑 C++ 版裁判（DEV-005）时，没有因为排序差异而失败；
     - 如果失败，就回到本决定，改为让裁判对并列行不敏感：加 `--sorted_result` 或补全 ORDER BY。
4. **以下逐行移植，并配上以 C++ 为准的金标准测试**：
   - 字符集与排序规则、时间的解析与格式化、`ObNumber`、`ObDecimalInt`；
   - 浮点数文本（MySQL 的 dtoa）、`RAND(seed)`；
   - hash 分区的落点（`ObObj::hash_murmur`）。
5. **递归深度**：Rust 栈帧大小不同，触发 `OB_SIZE_OVERFLOW` 的嵌套深度可能变化。如果裁判里有依赖确切深度的用例，记为偏差。

### 0.12 解析器【D10】

- **保留** bison/flex 生成的三套 C 解析器：SQL、PL、全文检索。
  - `parser` crate 的 build.rs 调用 `deps/3rd` 里的 bison 2.4.1 和 flex 2.5.35，等价于 `gen_parser.sh`，再用 `cc` 编译。
  - 理由：1064 报错的 near 文本和行号、select 列名、plan cache 参数化，全部取决于 bison 2.4.1 自动机和约 1.3 万行动作代码的确切行为。保留原解析器，这部分的对齐风险就是零。
- **FFI 面很窄**：server 调用解析器 23 个函数，解析器回调 server 19 个函数。
  - 19 个回调由 Rust 以 `extern "C"` 导出；
  - `lookup_pl_symbol` 改成由 `sql::pl` 在启动时注册的函数指针；
  - 和字符集的 5 处直接耦合，打一个小补丁，改成调用访问函数。
- **`ParseNode`/`ParseResult` 的布局**：用 bindgen 离线生成并入库，加 `size_of`/`offset_of` 静态断言。
- **`ParseNode<'p>` 句柄**：`Copy`、`!Send`，生命周期就是语句的解析 arena。
  - 读：`kind()`、`child(i)`、`num_child()`、`str_bytes()`、每个标志位一个访问函数；
  - 写：只提供标量字段的 setter。plan cache 参数化会把常量节点就地改成 `T_QUESTIONMARK`。
- **规则**：
  - 回调绝不重入解析器，因为 longjmp 不能越过 Rust 栈帧；
  - 一次解析必须在同一个 OS 线程上跑完，因为 `jmp_buf` 和错误缓冲是线程局部的；
  - 解析 arena 要活过 resolve，因为 `TableItem::node_` 会持有句柄。
- **迁移完成后（可选）**：在同一视图后面换成 bison 3.8 + Rust skeleton 生成的解析器，以语法树、错误位置、参数化输出三项差分全部一致为关口。这不属于本次迁移。

### 0.13 生成器与数据表

- **改由 build.rs 生成**，沿用 `rust/config/build.rs` 的先例，读同一份输入：
  - `ob_errno.def` → `oblib::error`：错误码常量、各张表、每条带参消息一个带类型的格式化函数（§2.3）；
  - 系统变量 JSON → share；
  - proto → prost/tonic；
  - 版本号 → `cargo:rustc-env`。
- **内部表**：保留 Python DSL，只换成 Rust 发射器，输出静态的列描述数组，再加一个通用的构建函数。现在配置阶段会生成 104,889 行 C++，改成数据表后估计 2–4 万行。
- **删除 4 个只为 C++ 服务的生成器**：str_datum 和 str_cmp 两个拆分编译单元的生成器、`gen_type_name.sh`（改由枚举派生名字表）、syspack（改用 `include_str!`）。
- **共存期间**：每个生成器从同一份源头同时输出 C++ 和 Rust。
- **数据表交给机械转换，不交给翻译者**（manifest 会给它们打标）：
  - 包括 `src/storage/fts/dict/ob_ik_dic.cpp`（27.6 万行）、`ob_timezone_info.cpp`（约 4 万行）、字符集表、`ob_uctype.h`，以及 16 个 `.map`、4 个 `.def` 表格；
  - 大块数据放进单独的小 crate，用 `include_bytes!` 引入；表格由转换脚本生成。

### 0.14 第三方库【D11】

**判据**：库的输出（结果、报错文本、落盘字节）是否就是它的算法本身。是，就保留原生库，加一层薄封装；否，或者能用测试向量证明等价，就换成纯 Rust。

| 库 | 做法 |
|---|---|
| Boost.Geometry 1.74 | 保留。新写 cxx 薄 shim（`bgk_shim`），约 100 个算法入口，WKB 字节进出；C++ 异常在 shim 里映射成 `OB_ERR_BOOST_GEOMETRY_*`；编排逻辑译成 Rust |
| Boost.Spirit（SRS WKT 解析） | 手写 Rust 解析器，用全部默认 SRS 定义逐位比对 |
| ICU 69.1（只用 uregex） | 保留。直接声明带 `_69` 后缀的符号，约 20 个函数，外面包一层 RAII |
| libxml2、s2geometry、vsag、SQLite | 保留。libxml2 用离线 bindgen 生成 -sys；s2 和 vsag 用 cxx shim；SQLite 用 `rusqlite`，链接同一份库 |
| zstd 1.3.8（仓内，OceanBase 补丁版）、zlib 1.2.13 | 保留，字节兼容。zstd 原样放进 -sys crate；zlib 用钉住版本的 `libz-sys` |
| OpenSSL 1.1.1u | 换成 RustCrypto：aes、des、sm3、sm4、md4、md-5、sha1、sha2、hmac 及各种分组模式；cfb1 没有现成 crate，手写；证书解析用 `x509-parser`。用测试向量验证 |
| gRPC + protobuf C++、protobuf-c | 换成 tonic + prost，只用在 standby |
| CRoaring | 换成 `roaring` crate（只用内存中的 roaring64，不涉及序列化） |
| libcurl | 换成 `ureq` + rustls |
| RapidJSON（打过补丁） | 把 Reader 和它的"普通精度" strtod 移植到 Rust；`GetParseError_En` 的文本逐字复制；用 C++ shim 做差分测试的参照 |
| xxHash、fast_float | xxhash-rust、std，各用金标准测试证明等价 |
| `<regex>`（JSON schema 的 pattern） | `regress`，ECMAScript 语义 |
| libaio、libunwind | libaio 用 5 个函数的 FFI；libunwind 用 `backtrace` |
| tzcode、libev 头文件、libeasy 残留 | 删除：tzcode 不参与编译，libev 和 libeasy 只剩残留头文件 |

- **分配器钩子**：zstd `customMem`、zlib `zalloc`、vsag 的 allocator、JSON 解析器的 allocator，重新接到 `MemBudget` 上。
- **unsafe 估计**：手写 unsafe 约 200–350 处，集中在约 10 个 -sys、shim 和视图 crate 里。

## 1. 生态取舍【D13】

| 领域 | 决定 |
|---|---|
| 异步 | **禁用**。翻译代码里不得出现 `async fn`、tokio、async-std。唯一例外：standby 的 tonic/tokio，限定在 standby 内部 |
| 错误 | 自有 `ObError`：`#[repr(transparent)] ObError(NonZeroI32)`，4 字节；`ObResult<T = ()>`。常量由生成器产出，沿用 C++ 名字，如 `OB_ITER_END`。不用 thiserror、anyhow |
| 日志 | 自有 `oblib::log`：宏加运行时，包括 111 个模块的模块表、线程级级别表、限流、异步写、轮转、压缩，日志行格式与 C++ 一致。翻译代码禁用 `log`、`tracing`、`eprintln!` |
| 格式化 | 自有 proc-macro `ob_printf!`：编译期解析 C 格式串、检查参数类型，运行时逐字节复刻 C 的输出。`databuff_printf!`、`buf_printf!`、`append_fmt!`、`log_*_fmt!` 都建在它上面。用户可见的文本禁止用 `format!` 生成 |
| 容器 | 存储类型：`Vec`、`SmallVec`、`ArenaVec`。一律通过 `oblib::container::ObIArray<T>` trait 访问。`ObHashMap`/`ObHashSet`/`ObSortedVector`/位集在 oblib 里逐行移植。翻译代码禁用 std `HashMap`/`HashSet`、hashbrown、indexmap。`BTreeMap`/`BTreeSet` 只用来对应 C++ 的 `std::map`/`std::set` |
| 排序 | `oblib::sort::ob_sort` 逐行移植基准平台的 `std::sort`（0.11）。禁用 `sort_unstable*`；`sort_by` 只用来对应 `std::stable_sort` |
| 哈希、校验和 | murmurhash、fnv、crc64（含 SSE4.2 路径）逐行移植；xxHash 用 xxhash-rust，前提是金标准测试证明等价 |
| 字符集、时间、数值、浮点文本 | 逐行移植。不得用 chrono、time、encoding_rs、icu4x、rust_decimal、num-bigint、ryu 实现 SQL 语义 |
| 时间源、随机数 | 移植 `ObTimeUtility`、`ObClockGenerator`、`ObRandom` 和 MySQL 的 `RAND(seed)`。翻译代码不直接用 `std::time::SystemTime`，也不用 `rand` |
| 序列化 | 自有 `oblib::serialization`（`macro_rules!` 加 trait），逐字节复刻 unis 格式。serde 只允许用在 build.rs 里 |
| 锁、原子、无锁结构 | 见 0.8 |
| 栈 | `stacker`，包在 `oblib::thread::smart_call!` 里 |
| 分配器 | 见 0.7 |
| SIMD、汇编 | 用 `std::arch` 里同名的 intrinsic，配 `#[target_feature]` 和运行时检测；`asm!` 只留给 rdtsc 这类没有替代品的地方；不引入 SIMD crate |
| FFI | Rust 导出走 cbindgen 生成的 C ABI；调用 C++ 走 cxx；调用 C 库用离线 bindgen 生成并入库。保持 `panic = "abort"`，C++ 异常一律在 shim 里接住 |
| 依赖白名单 | 下表点名的 crate，版本按工具链的做法钉死。**表外的一律不准引入**，新增要走修订 |

| 用途 | 允许的 crate |
|---|---|
| 同步、并发 | parking_lot、crossbeam-epoch、crossbeam-channel、crossbeam-queue、crossbeam-utils、dashmap、arc-swap、portable-atomic |
| 栈、信号、系统调用 | stacker、nix、libc |
| 内存、存储结构 | bumpalo、smallvec、slab、slotmap |
| 宏、FFI | syn、quote、proc-macro2（只用于 `ob_macros`）；cxx、cbindgen、bindgen（离线） |
| 第三方替代品 | RustCrypto 系列（aes、des、sm3、sm4、md4、md-5、sha1、sha2、hmac、cbc、ecb、cfb-mode、cfb8、ofb、ctr、aes-gcm）、x509-parser、tonic、prost、prost-build、tonic-build、tokio（只限 standby）、roaring、ureq、rustls（已在用）、rusqlite、libsqlite3-sys、libz-sys、xxhash-rust、backtrace、regress |

## 2. 无对应构造的统一译法

每个源构造只有一种译法。实现者给已列出的构造发明第二种译法，就是违反规则；审查者看到表里没有的构造，要提交修订请求，补进这张表。

### 2.1 错误与控制流【D12】

**函数体逐行保留 C++ 的 if/else 链，翻译阶段禁用 `?`。**

- 理由：声明 `ret` 的 39,077 个函数里，约 27% 在失败后还会读写 `ret`，或者继续往下执行（重置、按错误码分派、`tmp_ret`、清理）。在这些函数里，`?` 和原语义不等价。
- 对齐通过后：约 73% 的函数失败后只是原样上抛，可以用同一套脚本打的标签，把它们机械地改成 `?`。

| C++ | Rust |
|---|---|
| 返回 `ret` 的 `int f(…, T &out)` | `fn f(…, out: &mut T) -> ObResult`。输出参数保持 `&mut`，不改成返回值，因为失败时写了一半的输出，调用方仍可能读到 |
| `int ret = OB_SUCCESS;` / `INIT_SUCC(ret)` | `let mut ret: ObResult = Ok(());` |
| `OB_FAIL(e)` / `OB_SUCC(e)` / `OB_TMP_FAIL(e)` | `ob_fail!(ret = e)` / `ob_succ!(ret = e)` / `ob_fail!(tmp_ret = e)`，展开为 `{ ret = e; ret.is_err() }`；if/else 链的结构原样保留 |
| `OB_FAIL(ret)` / `OB_SUCC(ret)` / `OB_SUCCESS != ret` | `ret.is_err()` / `ret.is_ok()` / `ret.is_err()` |
| `ret = OB_X;` / `return OB_X;` / `return ret;` | `ret = Err(OB_X);` / `return Err(OB_X);` / `return ret;` |
| `OB_X == ret` / `ret != OB_X` | `ret.is(OB_X)` / `!ret.is(OB_X)`，由 `ObResultExt` 提供 |
| `ret = OB_SUCCESS;`（吞掉错误） | `ret = Ok(());` |
| `COVER_SUCC(x)` / `FAILEDx(e)` | `cover_succ!(ret, x)` / `failedx!(ret = e)`，语义同宏定义（`ob_macro_utils.h:451,653`） |
| `CK` / `OX` / `OZ` / `OV` / `OZX1` / `OZX2` / `FALSE_IT` | 同名小写宏，**显式传入 `ret`**，如 `ck!(ret, c1, c2)`、`oz!(ret, f(a); a)`。`false_it!(stmt)` 等价于 `{ stmt; false }`。Rust 宏有卫生规则，不能像 C++ 那样隐式捕获局部变量 |
| `OB_ISNULL(p)` / `OB_NOT_NULL(p)` | 可空性由缺口清单决定：可空的用 `Option`，写成 `p.is_none()`/`p.is_some()`；清单判定不可空的，删掉检查 |
| `IS_NOT_INIT`、`is_inited_`、`int init(…)` | 保留两段式初始化：字段 `is_inited_: bool`、`fn init(&mut self, …) -> ObResult`；构造函数改成不可失败的 `new()`/`Default` |
| `OB_ASSERT` / `abort_unless` / `ob_abort` | `ob_assert!(x)`：记 ERROR 日志和调用栈后 `std::process::abort()`，release 构建也一样 |
| `OB_E(EN_X) OB_SUCCESS`、`ERRSIM_POINT_DEF` | `ob_e!(EN_X)`、`errsim_point!(…)`，保留注入点，用 `cfg(feature = "errsim")` 控制 |
| throw / try / catch（118 处） | 内部的 throw 改成 `return Err(..)`；第三方 C++ 异常在 shim 里 catch 后转成错误码；`catch (...) { 恢复; throw; }` 改成用 RAII 守卫恢复现场 |
| `int` 返回值是值而不是错误码的函数 | 由缺口清单按函数打标签，翻译时照标签处理 |

**错误恢复规则**：
- 分配失败和结构性错误在 C++ 里经常共用一个哨兵值，到 Rust 里不得共用一个构造：
  - 分配失败一律是 `OB_ALLOCATE_MEMORY_FAILED`；
  - 结构性错误按原函数的错误码和部分结果返回。
- 容错转换（warn-on-fail）**不得吞掉内存错误**，判断用 `e.is_unrecoverable()`，对应 C++ 的 `CM_IS_WARN_ON_FAIL && ret != OB_ALLOCATE_MEMORY_FAILED`。

### 2.2 日志

| C++ | Rust |
|---|---|
| `LOG_WARN`/`LOG_ERROR(msg, kv…)` | `log_warn!(ret, "msg", kv…)`，错误码显式传入（C++ 隐式读 `ret`，`ob_log_module.h:262`） |
| `LOG_INFO`/`LOG_DEBUG`/`LOG_TRACE(msg, kv…)` | `log_info!("msg", kv…)` 等；这三级在 C++ 里错误码固定为 0 |
| `LOG_WARN_RET(OB_X, …)`、`XXX_LOG_RET(level, OB_X, …)`、`XXX_LOG(LEVEL, …)` | `log_warn!(OB_X, …)`、`ob_log!(XXX, LEVEL, OB_X, …)`、`ob_log!(XXX, LEVEL, ret, …)`；错误码参数既接受 `ObResult` 也接受 `ObError` |
| `#define USING_LOG_PREFIX P` | 模块顶部写 `const LOG_PREFIX: ObLogMod = ObLogMod::P;`，`log_*!` 在调用处解析这个名字 |
| `_LOG_*`、`_XXX_LOG`（printf 式） | `log_warn_fmt!(ret, "C 格式串", args…)`，走 `ob_printf!` |
| `K(x)` / `K_(x)` / `KR(x)` / `KP(x)` / `KPC(x)` / `"k", v` | `k!(x)` / `kv!("x", self.x_)` / `kr!(x)` / `kp!(x)` / `kpc!(x)` / `kv!("k", v)`；值都走 `ObToString` |
| `REACH_TIME_INTERVAL(t)` / `REACH_COUNT_INTERVAL(n)` / `REACH_THREAD_TIME_INTERVAL(t)` | `reach_time_interval!(t)` / `reach_count_interval!(n)` 在每个调用点放一个 `static AtomicI64`；`reach_thread_time_interval!(t)` 用 `thread_local!` 里的 `Cell<i64>` |
| `LOG_DBA_*`、`NG_TRACE`、`REC_TRACE`、`FLT_*` | 同名小写宏 |

### 2.3 用户消息与格式化

- **`LOG_USER_ERROR/WARN/NOTE(OB_X, args…)`** 改成 `log_user_error!(OB_X, args…)`，它调用生成器产出的 `user_msg::OB_X(level, args…)`：
  - **参数类型由格式串决定**：`%.*s` → `(i32, &[u8])`，`%s` → `&[u8]`，`%ld` → `i64`，`%d` → `i32`，`%u` → `u32`，`%lu` → `u64`，`%f`/`%lf` → `f64`，`%c` → `i32`；
  - **输出逐字节等于 `vsnprintf(buf, 512, …)`**：截到 511 字节；`%s`/`%.*s` 遇到 NUL 即停；`%.*s` 和 `%.Ns` 按字节截断，可能截断多字节字符，所以消息缓冲用 `Vec<u8>`，不用 `String`；inf/nan 按 C 的拼写（`inf`、`nan`、`-nan`）；
  - **结果写进线程局部的警告缓冲区**：每条最多 512 字节，共 64 条，环形覆盖；
  - 给 `ret` 赋值仍然单独写一句。
- **错误包**：发送时只有警告缓冲区里记录的错误码等于返回的错误码，才用缓冲区里的文本，否则用默认文本（`obmp_packet_sender.cpp:536-570`）。这条规则原样保留。**消息不得塞进错误值里。**
- **其他宏**：`FORWARD_USER_*` 和 `LOG_USER_ERROR_WITH_LINE_COL` 改成同名小写宏。
- **格式化写缓冲**：`databuff_printf`、`BUF_PRINTF`、`snprintf` 写进定长缓冲的，改成 `databuff_printf!` 或 `buf_printf!`。返回值和 `pos` 的语义照旧，溢出时返回 `OB_SIZE_OVERFLOW`。

### 2.4 内存与所有权

| C++ | Rust |
|---|---|
| `if (OB_ISNULL(buf = alloc.alloc(sz))) { ret = OB_ALLOCATE_MEMORY_FAILED; } else { p = new (buf) T(..); }` | 查询作用域：`arena.try_alloc(T::new(..))`；拥有型：`try_box(T::new(..))`。结果交给 `ob_fail!` 链 |
| `OB_NEW(T, label, ..)` / `OB_NEWx(T, pool, ..)` | `try_box(..)`，属性用 `MemAttrGuard`；`OB_NEWx` 按 pool 的生命周期选 `arena.try_alloc` 或 `try_box` |
| `OB_DELETE`、`x->~T(); alloc.free(x);` | 交给 Drop；arena 里的对象什么也不用做 |
| `ob_write_string(alloc, src, dst)` / `deep_copy(alloc, ..)` | `arena.try_str(src)`；拥有型用 `Clone`，必要时先 `try_reserve` |
| `ObString` 成员 | 查询作用域用 `ObStr<'q>`（即 `&'q [u8]`），拥有型用 `Box<[u8]>`；归属由缺口清单决定 |
| `ObArenaAllocator` 成员或局部变量 | 请求或语句级的用 `MemCtx` 里的 `Arena`；算子批次级的用算子拥有的 `Arena`，在批次边界 `reset()`，此时不能有存活的借用 |
| `allocator.reset()` / `reuse()` | `arena.reset()` |
| `CHECK_MEM_STATUS()` | `mem_ctx.check_status()`，保留在原来的调用点 |
| `ObMallocHookAttrGuard`、`CURRENT_CONTEXT`/`WITH_CONTEXT` | `MemAttrGuard`；`MemCtx` 作为参数显式传递，或从 `WorkerCtx` 里取 |
| `SMART_VAR`/`HEAP_VAR` | 堆上的 `Box` |
| 裸指针成员 | 由缺口清单逐个分类，在以下几种里选一种：id（0.6）、`&'q T`、`Box<T>`、`Arc<T>`、`Weak<T>`、`Option<&'static T>`（槽位）。`NonNull<T>` 只允许出现在 §3 白名单模块里 |
| `T *&` 输出参数（在被调方分配） | `out: &mut Option<&'q T>` 或 `out: &mut Option<Box<T>>`，按缺口清单 |

### 2.5 容器与字符串

| C++ | Rust |
|---|---|
| `ObSEArray<T, N>` / `ObArray<T>` / `ObFixedArray<T>` | `SmallVec<[T; N]>` / `Vec<T>` / `ArenaVec<'q, T>`。一律通过 `ObIArray<T>` trait 的同名方法访问：`push_back(v) -> ObResult`（内部 `try_reserve`）、`count() -> i64`、`at(i)`、`reset()`、`assign(&other) -> ObResult` |
| 参数 `const ObIArray<T> &` / `ObIArray<T> &` | `&[T]` / `&mut dyn ObIArray<T>` |
| `arr.at(i)` / `arr[i]` / `arr.at(i, obj)` | `arr.at(i)`：越界 abort，与 `OB_ASSERT` 一致 / 同前（C++ 的 `operator[]` 越界只记日志，属于未定义行为）/ `arr.at_into(i, &mut obj) -> ObResult`，越界返回 `OB_ARRAY_OUT_OF_RANGE` |
| `ObHashMap<K, V>` / `ObHashSet<K>` | oblib 里逐行移植的同名类型：桶数、`%` 取桶、头插、可失败的哈希函数都不变；方法名 `create`、`set_refactored`、`get_refactored`、`erase_refactored`、`foreach_refactored` 不改 |
| SQL 引擎自带的哈希表（`ObExtendHashTable`、`ObHashPartInfrastructure` 等） | 逐行移植：桶数、增长策略、哈希函数都不变，因为它们决定输出的行序 |
| 以对象地址为键的表（如 `ObRawExprCopier` 的映射表） | `ObHashMap<Id, …>`。需要迭代、且顺序会进入结果的，按插入顺序存一份 `Vec<Id>`，标 `PARITY(port)` |
| `ObLinearHashMap` / `ObLinkHashMap` | `DashMap<K, Arc<V>>`（0.8） |
| `ObList<T>` / `ObSortedVector<T>` / `ObVector<T>` / 位集 | oblib 里的同名类型，底层分别是 `VecDeque`、`Vec`、`[u64]`；`lower_bound` 等语义照旧 |
| `ObDList<T>` + `ObDLinkBase` | arena 里的链表用 id 串起来；其他地方用 oblib 的侵入式链表（白名单模块，§3）。`ObExprOperator` 的这个基类直接去掉 |
| `std::map` / `std::set` / `std::vector` / `std::string` | `BTreeMap` / `BTreeSet` / `Vec` / `Vec<u8>` |
| `std::sort`、`lib::ob_sort` / `std::stable_sort` | `ob_sort(slice, cmp)` / `slice.sort_by(cmp)` |
| `ObString` 的方法（`length()`、`ptr()`、`case_compare()`、`prefix_match()` 等） | `[u8]` 上的扩展 trait `ObStringExt`，方法名不变 |
| `ObString` 当写缓冲用（`assign_buffer`、`write`） | `oblib::string::ObStringWriter<'a>`（`&'a mut [u8]` 加长度） |
| `const char *` | 内部用 `&[u8]`，不假定是 UTF-8，不带结尾的 NUL；FFI 边界用 `&CStr`；`&str` 只用于字面量 |
| `char buf[N]` + `snprintf` | `[u8; N]` + `databuff_printf!` |
| `ObSqlString` / `ObStringBuffer` | oblib 里的同名类型，追加操作可失败；`append_fmt!` 走 `ob_printf!` |
| `strcmp`/`memcmp` / `strcasecmp` | 切片比较，顺序与 memcmp 相同 / `ob_strcasecmp`，按 C locale 只折叠 ASCII |
| 和排序规则有关的比较、哈希、大小写转换 | 一律调用移植后的 `ObCharset::*`；不得用 `str::cmp`、`to_lowercase`、`eq_ignore_ascii_case` 代替 |
| `isspace` 等 ctype 函数、`strtol`/`strtod`/`atoi` | `oblib::ctype::isspace` 等，按 C locale 实现，`isspace` 包含 `\v`；`ob_strtol`/`ob_strtod` 跳过前导空白、允许只解析一部分、溢出时夹到边界值 |

### 2.6 SQL IR（R1–R8）

- **R1**：`ObRawExpr *` 改成 `ExprId`/`Option<ExprId>`，其他图同理。arena 一律显式传参（`qa: &QueryArena` 或 `&mut QueryArena`），**不存进 self**。
- **R2**：调用 `&mut QueryArena` 期间不得持有 `&RawExpr`，先把需要的 id 或字段拷出来。
- **R3**：槽位参数（`ObRawExpr *&`，共 1,459 个）一律"拷出、调用、立即 `set_param` 写回"，标 `SLOT(port)` 供审查。
- **R4**：地址为键的表改成 id 为键（§2.5）；要迭代的保留插入顺序，标 `PARITY(port)`。
- **R5**：写在节点上的访问标记（`IS_MARKED` 一类）改成 `FixedBitSet` 旁表；`rt_expr_` 改成代码生成局部的 `IdVec<ExprId, Option<RtExprId>>`。
- **R6**：`SMART_CALL` 改成 `smart_call!`（0.8）。
- **R7**：visitor 的双分派改成 `walk_expr` 里的 `match`。
- **R8**：语句回收用 `slotmap` 的代际 key；向下转型（1,350 处）改成带检查的访问函数，例如 `as_column_ref()`，类不符时返回 `OB_ERR_UNEXPECTED`，而不是 C++ 里的未定义行为。

### 2.7 并发

| C++ | Rust |
|---|---|
| `ObSpinLock`、`lib::ObMutex` | `Latch<T>`（`ObSpinLock` 名为自旋锁，其实会睡眠） |
| `SpinRWLock`、`ObLatch` | `RwLatch<T>` |
| `TCRWLock`、`DRWLock` | `ShardedRwLatch<T>`：读者只锁自己 itid 对应的分片，写者锁全部分片 |
| `ObRecursiveMutex` | `ReentrantLatch<T>`（`parking_lot::ReentrantMutex`） |
| `ObThreadCond` | `Monitor<T>` |
| `ObByteLock`、`ObSmallSpinLock`、`ObPtrSpinLock`、`ObRowLatch` | `oblib::sync::bitlock`：锁位和数据仍在同一个字里 |
| 带超时的加锁、`*WithTimeout` guard | `lock_until(deadline) -> ObResult<Guard>`，超时返回 `OB_TIMEOUT` |
| `ATOMIC_LOAD/STORE/SET/TAS/FAA/AAF/INC/DEC/BCAS/CAS/VCAS` | 对应的 `Atomic*` 方法，ordering 一律 `SeqCst` |
| `*_ACQ`/`*_REL`/`*_RLX` | 照原样用 `Acquire`/`Release`/`Relaxed` |
| `MEM_BARRIER` / `WEAK_BARRIER` / `__COMPILER_BARRIER` / `PAUSE` | `fence(SeqCst)` / `fence(AcqRel)` / `compiler_fence(SeqCst)` / `std::hint::spin_loop()` |
| `inc_update`/`dec_update`（CAS 求最大、最小） | `fetch_max`/`fetch_min`，注意返回值语义不同 |
| `volatile` 标志或计数 | `Relaxed` 原子 |
| 128 位 CAS（3 处） | `Latch` |
| `ObLightyQueue`、`ObFixedQueue`（作队列）、`ObPriorityQueue2` | `crossbeam_channel::bounded`；满时 `try_send` 返回 `Full`，映射为 `OB_SIZE_OVERFLOW` |
| `ObFixedQueue`（作空闲表） | `crossbeam_queue::ArrayQueue` |
| `ObSpLinkQueue`、`ObLinkQueue` | `crossbeam_queue::SegQueue<Box<T>>`，所有权随 `Box` 转移 |
| `ObLinkHashMap`、`ObLinearHashMap`、`FixedHash2` | `DashMap<K, Arc<V>>`。拿到 guard 后立刻克隆 `Arc` 再放手，否则会重现 C++ 文档警告过的回调死锁 |
| ObQSync 的"换指针后等读者退出" | `ArcSwap<T>` |
| `CACHE_ALIGNED` | `crossbeam_utils::CachePadded` |
| 线程池子类、`run1()` | 实现 `Runnable` trait，交给 `oblib::thread` 的对应类型 |
| `ObTimerTask&` 裸引用登记 | `Arc<dyn TimerTask>`，由定时服务持有 |
| `void* task` 的线程池任务 | `SimplePool<T>`，任务的类型是明确的 |

**数据放进锁的规则**（翻译单元是头文件组，类定义和方法在同一个单元里，足够做判断）：

1. 在本类加锁区域里写过、在锁外从不写的字段，放进锁里的 `T`。
2. 锁外也读的字段，改成 `Atomic*`：在锁内写，锁外用 `Relaxed` 读。
3. 一个锁保护的是别的对象的字段时，用 `Latch<()>` 当令牌，被保护的字段改成原子量，标 `TODO(port)`。
4. 跨函数持锁的写法（231 处 `unlock`、93 个加锁接口方法），改成返回 guard，或写成 `with_locked(|g| …)`。

**原子字段的规则**：

- 凡是被任意 `ATOMIC_*` 访问过的字段，类型都改成 `AtomicI64`/`AtomicU32`/`AtomicPtr`/`AtomicBool` 等。
- 同一字段上的普通读写：
  - 在拥有 `&mut self` 的上下文里（构造、发布前的 reset），用 `get_mut()`；
  - 其余用 `Relaxed`。
- 如果某处普通访问参与了跨线程的 store→load 握手（附近通常有 `MEM_BARRIER`），改用 `SeqCst`，并请审查者确认。

### 2.8 存储

- **只能通过核心 API 访问 MVCC 内部**：
  - `x->prev_` 改成 `x.prev()`；
  - 改链只能经由 `RowGuard`；
  - 核心之外不允许持有裸的 `*mut TxNode`。
- **句柄**：
  - C++ 拷贝句柄的地方用 `clone()`；`reset()` 改成 drop 或赋 `None`；`get_obj()` 改成 `&*h`；
  - 在作用域内只是借用的，传 `&T`，**不要多 clone**。
- **手动 `inc_ref`/`dec_ref`**（169 处）逐处改写：
  - 管内存对象的，改用 `Arc` 的 clone 和 drop；
  - 管磁盘块的（`inc_macro_ref` 等），保持计数表。
- **事务回调**：只能用 `TxCallback` 的三个 enum 变体，不新增种类。

### 2.9 解析器的消费方（205 个文件）

| C++ | Rust |
|---|---|
| `node->children_[i]` | `node.child(i)`：越界或空都返回 `None` |
| `node->num_child_` | `node.num_child()` |
| `node->type_` | `node.kind()` |
| `node->str_value_` / `str_len_` | `node.str_bytes()` |
| 写节点的字段 | 对应的 setter，只允许写标量字段 |

### 2.10 全局状态与 seam

- 映射见 0.5。
- 符号落在 seam 表里的，**按表实现**：
  - 声明所在的单元，生成 trait、槽位和转发函数；
  - 定义所在的单元，写 `impl`；
  - 组合根的绑定由脚本追加，翻译者不改组合根。
- **中心注册表一律由脚本生成**，翻译者只写自己的单元。包括：表达式注册表、算子注册表、各 crate 的模块树（`lib.rs`）、组合根绑定。

### 2.11 序列化与 TO_STRING

| C++ | Rust |
|---|---|
| `OB_UNIS_VERSION(v)` + `OB_SERIALIZE_MEMBER(C, a_, b_)` | `ob_serialize_member!(C, v; a_, b_);`，生成 `impl ObSerialize`。成员顺序按宏里列出的顺序，不按字段声明顺序 |
| `OB_SERIALIZE_MEMBER_INHERIT`/`_SIMPLE`/`_TEMP`/`_IF` | 同名小写宏，变体一一对应 |
| `OB_DEF_SERIALIZE`/`DESERIALIZE`/`SERIALIZE_SIZE(C)` 的函数体 | `impl ObUnisBody for C`；unis 头由 `ob_unis!(C, v)` 统一加上 |
| `NEED_SERIALIZE_AND_DESERIALIZE` + `DEFINE_SERIALIZE…` | 手写 `impl ObSerialize for C`，不带 unis 头，palf 的 `LogEntry` 就属于这一类 |
| `serialize(char *buf, int64_t len, int64_t &pos) const` / `deserialize(…)` / `get_serialize_size()` | `fn serialize(&self, buf: &mut [u8], pos: &mut i64) -> ObResult` / `fn deserialize(&mut self, buf: &[u8], pos: &mut i64) -> ObResult` / `fn get_serialize_size(&self) -> i64`。**就地解码**：这样旧数据里缺失的尾部成员保留构造时的默认值 |
| `OB_UNIS_ENCODE/DECODE/ADD_LEN(x)`、`LST_DO_CODE(…)` | `ob_unis_encode!(ret, buf, pos, x)` 等；解码时保留 `pos < data_len` 的判断 |
| `serialization::encode_vi64` 等 | `oblib::serialization` 里的同名函数，字节序相同（定长整数按大端写） |
| `TO_STRING_KV` / `VIRTUAL_TO_STRING_KV` / `INHERIT_TO_STRING_KV` / `DECLARE_TO_STRING` + `DEF_TO_STRING` | `impl ObToString`，写成 `to_string_kv!(Self; "a" => self.a_, …)`；继承版先打印父类。键就是 C++ 宏参数字符串化的结果 |
| 手写的 `int64_t to_string(char *, int64_t)` | 逐行翻译成 `fn to_string(&self, buf: &mut ObBuf) -> i64` |
| `ObCStringHelper::convert(x)`、`J_KV`、`databuff_print_kv` | `helper.convert(&x)`、`j_kv!`、`databuff_print_kv!` |

**`TO_STRING` 的格式**：
- 整体是 `{k:v, k:v}`；
- double 用 `%.18le`，float 用 `%.9e`，bool 输出 `True`/`False`，空指针输出 `NULL`；
- 数组输出 `[cnt:N, a, b]`。
- 所有类型都逐字节复刻。

### 2.12 值类型与位布局

| C++ | Rust |
|---|---|
| `ObObj`、`ObDatum`，以及其他落盘或依赖 `offsetof` 的结构 | `#[repr(C)]`，字段顺序和类型都不变。只含标量的联合体改成原始位（`u64`）加访问方法，如 `f64::from_bits`；含指针的联合体按缺口清单处理 |
| 位域 | `oblib::ob_bitfield!` 宏，显式写出每个字段的偏移（按 Clang/GCC 在小端上的规则，从低位起依次分配）；每个结构配一条与 C++ 对照的布局测试 |
| C++ 枚举 | `#[repr(transparent)] pub struct T(pub i32)`，加同名的关联常量；`match` 用常量模式，最后加一个 `_` 分支。理由：反序列化不校验取值，int→枚举的转换至少有 781 处 |
| `ItemType`（约 2,020 个值，两份头文件副本） | 合并成 `query::item_type::ItemType`，`#[repr(i32)]`，取值与 C++ 完全相同 |
| `ObNumber`、`ObDecimalInt`、`ObTimeConverter`、`ob_dtoa`、哈希函数、crc64 | 逐行移植，配上以 C++ 为准的金标准测试 |
| 整数类型 | `int64_t` → `i64` 等一一对应。<br>• 作字节数据用的 `char` → `u8`（C 的 `char` 在 x86 上有符号，在 aarch64 Linux 上无符号）；<br>• `long` → `i64`（Windows 上原本是 32 位，共 265 处，标 `TODO(port)`）；<br>• `size_t` → `usize`；<br>• 无符号数回绕在 C++ 里是有定义的行为，所以参与对齐的 profile 关闭溢出检查；C++ 里显式检查溢出的地方，改用 `checked_*`/`overflowing_*` |

### 2.13 类、模板与其他语言构造

| C++ | Rust |
|---|---|
| 带虚函数的类 | 一个 trait（方法一一对应），加一个数据 struct：<br>• 基类数据放在第一个字段 `base`，trait 提供 `base()`/`base_mut()`；<br>• 基类的默认实现 → trait 的默认方法；<br>• 派生类里调用 `Base::f()` → 自由函数 `base_f(self)` |
| 多重继承（120 个，多为接口混入） | 拆成多个 trait；虚继承（2 处）逐处处理 |
| 向下转型 `static_cast<D*>(b)` | 对有类型标签的类层次，先查标签再转，标签不符就 abort；能整体改成 enum 的，按 0.6 处理 |
| CRTP | 泛型加 trait，以 `Self` 作类型参数 |
| 模板 | 泛型加显式的 trait 约束：<br>• 非类型模板参数 → const 泛型；<br>• 特化 → 每个类型一个 trait impl；<br>• SFINAE、`if constexpr` → trait 分派 |
| 运算符重载 | • 比较 → `PartialEq`/`PartialOrd`，语义不同时保留 `compare()`；<br>• 深拷贝的 `operator=` → `assign(&mut self, &T) -> ObResult`，浅拷贝 → `#[derive(Clone, Copy)]`；<br>• `operator()` → 方法 `call()`，`operator[]` → `at()`；<br>• 算术 → `std::ops` |
| `DISALLOW_COPY_AND_ASSIGN` | 不 derive `Clone`/`Copy` |
| 函数重载 | 按 §4 的改名规则 |
| 默认参数 | 调用方把所有参数显式写全，默认值取自 C++ 的声明 |
| 命名空间作用域里带构造函数的全局对象 | 能用 `const` 初始化的写成 `static`；否则写 `static X: LazyLock<T>`；构造时有副作用（比如注册）的，改成组合根里的显式调用 |
| 函数内的 `static ObX x`、`thread_local`、`RLOCAL` | `static X: LazyLock<…>` 或 `OnceLock`；线程局部用 `thread_local! { static X: Cell<…> = const { … } }` |
| 定义类型或函数的宏、X-macro 表 | 同名的小写 `macro_rules!` |
| `goto`（93 处，全在 5 个从 MySQL 派生的文件里） | 带标签的块（`'l: { … break 'l; }`）或循环状态机，逐处翻译 |
| 非空的 `switch` 贯穿（约 144 处） | 重复代码，或者改写成依次递减的 `if` 级联，注释注明原来是贯穿；空 case 合并成 `A \| B =>` |
| 内联汇编（39 处） | pause/yield → `spin_loop`；rdtsc、cpuid、crc32 → `core::arch` 的 intrinsic；换栈汇编由 `stacker` 取代；其余的 `asm!` 放进 §3 的白名单模块 |
| SIMD intrinsic | `std::arch` 里的同名函数，加 `#[target_feature(enable = …)]` 和运行时检测；`ob_sse_to_neon.h` 按 `cfg` 拆成两份实现 |
| `DEFER(…)` / `ARRAY_FOREACH` / `(void)f()` | `defer!`，在 Drop 里执行 / `for` 循环加 `ret.is_ok()` 条件 / `let _ = f();` |

## 3. 逃生口

**unsafe 只允许出现在下面这些地方**。其余 crate 一律 `#![forbid(unsafe_code)]`，由 CI 用 grep 检查。

1. `oblib::mem`：全局分配器、`try_box`、arena 外观。
2. `oblib::sync::{bitlock, sharded}`；选用备选方案时，还有 `oblib::sync::latch_raw`（0.8）。
3. `oblib::container` 里的侵入式链表。
4. `oblib::signal`。
5. `query` 或 `sql` 里的帧模块（0.6），估计 20–40 处。
6. `storage_core` 整个 crate（0.9）。
7. `share::cache` 里的 KV cache map（0.8）。
8. `parser` 里的视图和 FFI 回调（0.12）。
9. 各 -sys 和 shim crate（0.14）。
10. 已有的 `config`、`sql-nio`。

**每处 unsafe 的要求**：

- 写 `// SAFETY: <为什么正确>` 注释，开启 `clippy::undocumented_unsafe_blocks`；
- 开启 `#![deny(unsafe_op_in_unsafe_fn)]`；
- `unsafe impl Send/Sync` 也只允许出现在上述地方；
- `oblib::sync` 和自写的无锁结构要配 loom 测试；单线程部分跑 Miri。

**标记**：全部可 grep，格式是承重的，不得改写。

| 标记 | 用途 |
|---|---|
| `TODO(port): <问题>` | 推迟的决定，见 UNKNOWN 规则 |
| `PERF(port): <快的写法是什么>` | 已知较慢的写法；对齐之后才做的 ordering 弱化 |
| `BUG(port): <复现方法>` | 照样复刻的原版缺陷，见 BUG 规则 |
| `SLOT(port)` | 改写 IR 槽位的地方（R3） |
| `PARITY(port): <说明>` | 为了保持行序或输出而做的特殊处理 |
| `SAFETY:` | 每处 unsafe |

**lint**：

- 照样跑 `clippy -D warnings`；
- 统一放开逐行保留错误链必然触发的几条：空分支、`needless_return`、`non_upper_case_globals`、`non_camel_case_types`、`too_many_arguments`。

## 4. 命名与输出路径【D14】

是否完成，靠输出文件是否存在来判定，所以这些规则是整个过程可以续跑的前提，不是风格偏好。

- **单元**：同一目录下同名的 `.h`/`.hpp`/`.ipp`/`.inc`/`.cpp`/`.cc`/`.c` 合成一个头文件组，一个单元对应一个 `.rs` 文件。顺序取自 `migration/depmap/units/order.txt`。
- **路径**：`src/<模块>/<相对路径>/<名字>.<扩展名>` → `rust/port/<crate>/src/<相对路径>/<名字>.rs`。
  - crate 与模块同名，以下几处例外：

    | 源路径 | 映射到 | 说明 |
    |---|---|---|
    | `src/pl/<rest>` | `rust/port/sql/src/pl/<rest>` | 与同名的 `src/sql/pl/<rest>` 合成一个单元 |
    | `src/objit/include/objit/<rest>` | `rust/port/query/src/objit/<rest>` | |
    | `src/data_plane/api/data_plane/<rest>` | `rust/port/data_plane/src/<rest>` | |
    | `src/query/api/query/<rest>` | `rust/port/query/src/<rest>` | 与同一相对路径下的实现合成一个单元 |

  - 按 seam 表下移的函数，归到目标单元（函数级重映射）。
  - 数据表单元走机械转换（0.13）。
- **manifest**：由脚本按上述规则重新生成，并合并目标路径相同的单元。现在的 `migration/manifest.tsv` 用的还是临时规则（依赖图报告第 5 节），拍板后重新生成。
- **模块树**：各 crate 的 `lib.rs` 和 `mod` 声明由脚本生成，翻译者只写自己的单元文件。
- **名字**：
  - 类型名、方法名、字段名保留 C++ 的写法，包括 `Ob` 前缀和字段末尾的下划线；
  - 宏用同名小写；
  - 常量沿用 `OB_X`；
  - 枚举的关联常量沿用 C++ 的枚举值名。
  - 理由：对齐调试时能按名字对照。
- **命名空间**：crate 就是命名空间；嵌套的命名空间只在用来消除重名时保留为模块。
- **重载**：
  - 在头文件里声明位置最靠前的那个保留原名；
  - 其余改名为"原名 + `_` + 区分参数的类型名的蛇形写法"，类型名去掉 `Ob` 前缀和 const、引用、指针修饰，例如 `print(const ObColumnRefRawExpr &)` → `print_column_ref_raw_expr`；
  - 仍然冲突时，再加参数个数后缀；
  - 超过 20 个重载的集合（如 `ObRawExprPrinter::print` 有 116 个）由缺口清单单列处理。
- **共享类型的归属**：每个共享类型只有一个归属模块，翻译出来的代码只能从这里导入，不得各自复制一份。

  | 类型 | 归属 |
  |---|---|
  | `ObError`、`ObResult`、错误码常量 | `oblib::error` |
  | `ObIArray`、`ObHashMap` 等容器 | `oblib::container` |
  | `ObStringExt`、`ObStringWriter` | `oblib::string` |
  | `Arena`、`ArenaVec`、`ObStr`、`MemAttr`、`MemBudget`、`MemCtx` | `oblib::mem` |
  | 锁类型 | `oblib::sync` |
  | `smart_call!`、线程池、定时器 | `oblib::thread` |
  | 日志宏 | `oblib::log` |
  | `ob_printf!` | `ob_macros`，运行时在 `oblib::fmt` |
  | 序列化 | `oblib::serialization` |
  | `Slot<T>` | `oblib::slot` |
  | `ItemType`、`RtExpr`、`RtExprId`、`SpecId` | `query` |
  | `ExprId` 等编译期 id、`QueryArena` | `sql::ir` |
  | `ParseNode` | `parser` |
  | `GlobalContext`/`GCTX` | `share` |
- **测试**：C++ 的单元测试已经删除，不翻译测试。逐行移植的算法要配金标准测试，放在 `rust/port/<crate>/tests/`。

## 5. 缺口清单

所有权、生命周期、可空性这类问题**不由实现者决定**，一律查表：`migration/inventory.tsv`，一个站点一行；跨 crate 的符号查 `migration/design/seams.tsv`。站点不在表里，就是清单的缺陷：提出来，按 UNKNOWN 规则处理，继续往前走。

清单至少要覆盖以下类别（`prompts/02` 先用脚本扫出全部站点）：

1. 指针的可空性（`OB_ISNULL` 34,194 处）。
2. `T *&` 输出参数（9,454 处）。
3. 裸指针成员（6,099 处）的归属：id、`&'q`、`Box`、`Arc`、`Weak` 还是槽位。
4. `ObString` 成员和其他借用成员：`'q` 还是拥有型。
5. 拥有分配器、又借用它的自引用类（288 个），以及 schema 的扁平化拷贝。
6. 返回 `int` 的函数返回的是错误码还是值；失败后仍会继续执行的函数（约 27%）的标签。
7. 每个锁保护哪些字段（§2.7 的四条规则）。
8. 被原子访问的字段，以及参与 store→load 握手的普通访问。
9. union、位域、`reinterpret_cast`（3,692 处）。
10. 运行期写共享计划的点（1 + 31 + 6）。
11. 临时作用域里有没有向外层分配。
12. 跨算子旁路（28 处）。
13. weak 符号和 `.cpp` 里自写的 extern 原型。
14. 对象池，以及构造时有副作用的静态初始化。
15. 超过 20 个重载的集合。
16. 待确认的死代码。

## 6. 逐文件义务

- **文件头**：`//! Ported from: <源文件列表>`。
- **文件尾**：每个翻译出来的文件以状态行结尾，`<N>` 是本文件里 `TODO(port)` 的数量。审查者要核对它；状态行少报，就是一条审查发现。

  ```
  // PORT STATUS: confidence=<high|medium|low> todos=<N>
  ```

- **只写自己的单元文件**。中心注册表、模块树、组合根由脚本生成（§2.10）。
- **翻译阶段不运行编译器**、不运行测试、不运行会改动状态的 git 命令。这些操作在第 2 步之前由 `.claude/settings.json` 禁止（`templates/settings.json`）。

## 7. 偏差日志

对既定流程的每一处偏离都记一行，在关口由人写入，或经人同意后写入。

| ID | 日期 | 偏差 | 认可 |
|---|---|---|---|
| DEV-001 | 2026-10-10 | 可行性阶段没有在本机计时构建，改用 CI 数据；之后已实测，冷构建 7 分 44 秒 | 你（可行性关口） |
| DEV-002 | 2026-10-10 | 可行性报告和证据脚本提交到了 `rust-migration` 分支 | 你 |
| DEV-003 | 2026-10-10 | 5 个可行性调查子代理用的是会话默认模型，因为当时模型计划还没定 | 你 |
| DEV-004 | 2026-10-10 | 00b 的两名审查者各审一半、互不重叠，每个改写用例只被审查一次 | 你（00b 关口） |
| DEV-005 | 2026-10-10 | 裁判只在 macOS 上建立和验证；Linux 上至少要再验证一次"原版全部通过"。这也是 0.11 排序决定的前提 | 你 |
| DEV-006 | 2026-10-10 | 协议变体只覆盖了 121 个核心用例 | 你 |
| DEV-007 | 2026-10-11 | 链接级依赖只来自 macOS 的 release 构建。生成 seam 表之前，要在 Linux 构建上补跑 | 你（依赖图关口） |
| DEV-008 | 2026-10-11 | 依赖图第二轮的两名审查者各有一次 grep 误读到脚本里的几行；他们声明判断没有依赖这几行 | 你 |
| DEV-009 | 2026-10-11 | 依赖图审查者按模型计划用 Sonnet 5.5。不算偏差，记在这里备查 | — |

## 附录 A：决定清单

### A.1 核心决定：需要你拍板

| # | 问题 | 推荐（正文已按此写） | 主要备选 | 依据 |
|---|---|---|---|---|
| D1 | 迁移策略 | 核心一次性翻译，按 crate 分波次编译；C ABI 只用于孤岛 | 按模块 C ABI 共存；先在 C++ 里拆环再翻译 | layering §3.3 |
| D2 | crate 划分 | 10 个按模块划分的 crate：pl 并入 sql，objit 并入 query，sql 和 storage 互不依赖；另加 `storage_core`、`parser`、-sys/shim、`ob_macros`。sql 暂不拆，第 2 步实测后再定 | query 并入 sql；sql+storage 合并；现在就拆 sql | layering §4 |
| D3 | 拆环原则 | 按 seam 表逐符号定拆法：合并 → 下移 → trait + 槽位 → 链接期 seam（只用于热路径）。seam 表由脚本生成，经两名审查者核对 | 全部用链接期 seam；合并成少数几个大 crate | layering §3.1 |
| D4 | 数据目录兼容 | 必须兼容：读写同一套落盘格式，裁判增加跨二进制场景 | 只读兼容；不兼容（只支持新建的数据目录） | storage-txn Q1、conventions Q9、parser Q8 |
| D5 | 没有 ORDER BY 或有并列值时的行序 | 保持一致：哈希表、排序、哈希函数逐行移植，排序以 macOS libc++ 为准，前提是 Linux 复验通过 | 以 Linux libstdc++ 为准；让裁判对这类行序不敏感 | conventions Q5、Q6 |
| D6 | SQL IR 的表示 | arena 加类型化 u32 id；运行期计划用 Arc 只读共享；执行期用拥有式算子树；573 种表达式用 trait 对象注册表 | bumpalo + `&'a` + Cell；`Rc<RefCell>` | ir-graphs Q1–Q5 |
| D7 | 内存模型 | 查询作用域用 bump arena（`'q`），长生命周期用拥有型；全局 jemalloc 加线程局部 MemAttr，只做诊断；限额在原检查点显式检查；不依赖 allocator_api | 全部改成全局堆的拥有型；allocator-api2 参数化容器 | memory Q1–Q6 |
| D8 | 并发模型 | OS 线程，不用 async；parking_lot 封装的 Latch 系列；原子一律 SeqCst；crossbeam-epoch 加退休预算；stacker | async/tokio；std::sync；按语义弱化 ordering | concurrency Q1–Q8 |
| D9 | 存储的 unsafe 核心 | 独立的 `storage_core`，keybtree 按原算法移植；句柄改成 Arc；对象池首版去掉 | unsafe 放在 storage 内部的模块里；keybtree 不回收退休节点 | storage-txn Q2–Q6 |
| D10 | 解析器 | 保留 C 解析器，Rust 侧用 `ParseNode<'p>` 视图；迁移后可选替换 | 用 Rust 解析器生成器或手写重写 | parser Q1、Q2 |
| D11 | 第三方库 | 逐库判断（0.14） | 全部 FFI；全部换成纯 Rust | parser Q3–Q7 |
| D12 | 错误模型和函数体 | `ObError` 是 4 字节错误码；函数体逐行保留 if/else 链，翻译阶段不用 `?`；用户消息逐字节复刻 printf | 混用 `?`；带数据的错误枚举 | conventions Q1–Q3 |
| D13 | 依赖白名单和 unsafe 政策 | §1 的白名单；unsafe 只允许出现在 §3 列出的地方 | 只用 std；不限制 | conventions Q13、concurrency Q10 |
| D14 | 命名与单元 | §4 的规则 | `.h` 和 `.cpp` 各自一个单元 | 依赖图报告第 6 节 |

### A.2 其余决定：按推荐执行，你可以随时推翻

| 主题 | 决定 | 来源 |
|---|---|---|
| IR | 临时作用域用可截断的子作用域；语句回收用 `slotmap`；帧保留字节布局；计划不再字节序列化，不共享计划时深拷贝；封闭小集合用 enum；运行期写共享计划的点改成局部副本，标 `BUG(port)`；按地址顺序迭代的 3 处改成插入顺序，裁判对 EXPLAIN 归一化 | ir-graphs Q2、Q4、Q6–Q10 |
| 内存 | arena 用 bumpalo；分配失败采用混合策略（用户决定大小或大于 64 KB 的必须可失败）；保留 ctx 级计数，只做诊断；保留 11049；自引用对象改成拥有型字段；内存视图保留表结构 | memory Q2–Q5、Q7、Q8 |
| 并发 | 数据放进锁里；性能带不达标时，在同一包装内换成移植的 ObLatch；加退休预算；KV cache 内存块用 `Arc`；不放宽 SMART_CALL 的 10 MB 上限，先实测；`memory_dump` 改成不靠信号的探测；死代码确认后不翻译 | concurrency Q3、Q6、Q7、Q8 附带问题、Q9、Q11 |
| 存储 | keybtree 用 crossbeam-epoch；回调保活沿用"销毁前先摘回调"的协议；unsafe 核心是独立 crate；对象池首版去掉 | storage-txn Q2–Q4、Q6 |
| 解析器、第三方库、生成器 | 迁移后的 bison 3.8 替换列为独立的可选阶段；ICU 保留 69.1；JSON schema 的 pattern 用 regress；RapidJSON 移植到 Rust；加密换成 RustCrypto + rustls，HTTP 用 ureq；gRPC 用 tonic + prost；生成器按 Ga 方案；用户消息每条一个带类型的格式化函数；bindgen 离线生成并入库 | parser Q1 附带问题、Q4–Q7、Q9–Q11 |
| 约定 | 格式串由 `ob_printf!` 编译期检查；自有日志门面，日志行格式与 C++ 一致；`operator[]` 越界直接 abort；参与对齐的 profile 关闭溢出检查；枚举一律译成新类型加关联常量；`ObIArray&` 参数译成 `&[T]`/`&mut dyn ObIArray<T>`；`TO_STRING` 全部逐字节复刻；edition 2021；clippy 用放开清单 | conventions Q3、Q4、Q7、Q8、Q10–Q12、Q14、Q15 |
| 分层 | 全局状态用 `OnceLock` 加槽位；槽位不解绑；query 是独立 crate；sql 与 storage 两个方向都反转；在 seam 表里拆环，基线保持不变；生产构建里被覆盖的 6 个 weak 符号改成下移或钩子 | layering Q3–Q6、Q8、Q10 |

## 附录 B：事实来源

| 文件 | 内容 |
|---|---|
| `migration/design/survey/ir-graphs.md` | SQL 编译期和运行期对象图 |
| `migration/design/survey/memory.md` | 分配器、限额、分配失败 |
| `migration/design/survey/concurrency.md` | 线程、锁、原子操作、无锁结构、信号 |
| `migration/design/survey/storage-txn.md` | memtable、MVCC、事务、句柄、palf、持久化格式 |
| `migration/design/survey/layering.md` | crate 边界、逆向依赖、全局状态、迁移策略 |
| `migration/design/survey/parser-generators-thirdparty.md` | 解析器、生成器、第三方库 |
| `migration/design/survey/conventions.md` | 错误、日志、序列化、容器、值类型、语言构造 |
| `migration/design/survey/scripts/<主题>/` | 计数脚本，用法见同目录的 `README.md` |
