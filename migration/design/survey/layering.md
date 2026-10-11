# 调查备忘：crate 边界、跨模块依赖反转、全局状态与迁移策略

> - **用途**：设计文档"分层与全局状态"一节的事实底稿和选项评估。政策由你定，这里只给推荐。
> - **基线**：`7d907abfa`。自基线以来 `src/`、`rust/`、`bazel/` 没有改动（`git diff --stat 7d907abfa HEAD -- src rust bazel` 输出为空）。
> - **方式**：只读，未构建。链接级数据来自已有的 `build_release/`（macOS release），对其中 330 个目标文件跑 `nm -m -g`，口径与 `migration/scripts/symdeps_seekdb.py` 相同，各边计数与 `migration/depmap/symbols/module_edges.tsv` 逐条一致。
> - **证据**：脚本在 `/tmp/layering-survey-7d907/`，都是标准库 Python，在仓库根目录用 `python3 -I <脚本> .` 运行，清单见 §2.10。凡是估算都标"估计"；凡是启发式计数都写明了口径。

## 1. 结论

1. **crate 划分**：按 `src/<module>` 划分，`pl` 并入 `sql`，共 10 个 crate，外加已有的 `config`、`sql-nio`。`sql` 与 `storage` 平级、互不依赖，这与 Bazel 模块策略一致。
2. **成环符号必须逐项定拆法**：成环的符号共 741 个（12 条逆向边 364 个，加上 sql↔storage 横向 2 环 377 个）。其中 76% 经过 `data_plane/api`、`query/api`：这两层隔离了 include，但没有隔离链接。还有 78% 是"类型声明在下层、方法体编译在上层"，而 Rust 不能跨 crate 写 inherent impl。拆法按"合并 → 下移 → trait + 启动注册 → 链接期 seam（只作为性能逃生口）"的顺序，在一张自动生成的 seam 表里逐项写定。
3. **全局状态**：用 `OnceLock`/`LazyLock` 静态量加类型化槽位，不用显式上下文。涉及 GCTX 919 处、`server_service<T>` 1,222 处（102 种类型）、`get_instance()` 585 处。C++ 已经有单一组合根（98 处 `bind_server_service`），一对一映射最机械，对齐风险也最低。GCONF 在基线上已经清零（迁到了 `rust/config`），MTL 也已移除。
4. **迁移策略**：核心模块只能一次性翻译，再按 crate 自底向上设编译闸门。按模块用 C ABI 共存不可行：oblib 有 48% 的代码在头文件里、2,262 个模板；api 层有 797 处 `virtual`、433 处 `ObIAllocator`。共存只适合 sql-nio、config 这类接口窄的孤岛。
5. **前提**：sql crate（约 100 万行 C++）先不拆，第 2 步在笔记本上实测编译耗时和内存再定。query 层持有约 28 个 sql 核心类型的不透明指针，这个问题要和 SQL IR 主题一起定。

## 2. 事实

### 2.1 12 条逆向边逐条拆解

`migration/depmap/crates/union_cycles.txt` 里的 12 行 against 对应 11 个模块对，其中 oblib→share 有 ref、weak 两行。每个符号的声明处和定义处由 `edge_symbols.py` + `classify_edges.py` 算出（nm 给出定义它的目标文件，再在其 unity 成员里定位源文件；声明处靠"类定义或函数声明所在头文件"的启发式）。"调用文件"是引用方模块里出现该名字的源文件数。

| 边（符号数） | C++ 机制 | 声明在 | 定义在 | 调用文件 |
|---|---|---|---|---|
| oblib→share（7 = 4 个函数：weak 覆盖 4，另有 3 处是 oblib 其他目标文件引用同名符号） | `OB_WEAK_SYMBOL`（`src/oblib/lib/utility/ob_macro_utils.h:20`）、`__attribute__((weak))` | `ob_strerror`：weak 体在 `src/oblib/lib/oblog/ob_log.cpp:73`；`allow_next_syslog`：`ob_log.cpp:68`；`get_cpu_count`：`lib/cpu/ob_cpu_topology.cpp:27`；`current_resource_owner_id`：`lib/rc/ob_rc.h:27`，weak 体在 `ob_rc.cpp:25` | `share/ob_errno.cpp`（`gen_errno.pl` 生成）、`share/ob_task_define.cpp:29`、`share/config/ob_server_config.cpp:57`（读 `config::cpu_count()`）、`share/rc/ob_context.cpp:26` | 15 |
| oblib→observer（1） | weak | `common/mysqlclient/ob_mysql_proxy.h:33`，weak 体在 `ob_mysql_proxy.cpp:29`，返回 `OB_NOT_SUPPORTED` | `src/observer/ob_inner_sql_connection.cpp:1460` | 1（`ob_mysql_proxy.cpp:227`） |
| oblib→storage（1） | weak，加上 `.cpp` 里自写的原型 | 没有头文件。weak 空函数体在 `oblib/rpc/ob_sql_request_operator.cpp:27`；原型自写在 `observer/mysql/obmp_base.cpp:28`、`obmp_disconnect.cpp:26`、`sql/das/ob_das_parallel_handler.cpp:30` | `src/storage/memtable/ob_lock_wait_mgr.cpp:24` | oblib 1、observer 2、sql 1 |
| share→observer（2） | share 类的方法写在 observer；api 声明在下、定义在上 | `share/io/ob_io_manager.h:98`、`share/ob_ex_rpc.h:41,167` | `observer/omt/ob_server_runtime_controller.cpp:1124`、`observer/ob_ex_rpc.cpp:101,120` | ex_rpc 共 19 处调用 |
| sql→pl（152，40 个类） | PL 的声明在 `src/sql/pl/`（34 个文件，10,478 行），实现在 `src/pl/`（68 个文件，39,141 行）；反向 pl→sql 有 361 个符号 | `sql/pl/ob_pl_resolver.h`、`ob_pl_type.h`、`ob_pl.h` 等 20 个头文件 | `src/pl/*.cpp`，18 个文件 | 104 |
| sql→observer（126） | 向量索引 115 个；`ObSqlEndTransCb` 5 个；`ObReqTimeInfo` 2 个；inner SQL 2 个；ex_rpc 2 个 | `query/api/query/vector/*.h`、`sql/ob_mysql_end_trans_cb.h`、`query/api/query/runtime/ob_request_time_guard.h` | `observer/vector_index/*.cpp`（32 个文件，25,010 行）、`observer/mysql/ob_mysql_end_trans_cb.cpp:28`、`observer/ob_req_time_service.cpp` | 54 |
| storage→observer（33） | 向量索引 25 个；tablet report 4 个；inner SQL 2 个；ex_rpc 2 个 | `data_plane/api/data_plane/report/ob_tablet_report.h:61-75` 等 | `observer/ob_tablet_runtime_meta_updater.cpp:753` 等 | 18 |
| rootserver→observer（27） | 向量索引 24 个（`ObVectorIndexUtil` 静态方法）；inner SQL 1 个；ex_rpc 2 个 | `query/api/query/vector/ob_vector_index_util.h` | `observer/vector_index/ob_vector_index_util.cpp` 等 | 14 |
| pl→observer（10） | 向量索引 5 个；req_time 3 个；inner SQL 1 个；ex_rpc 1 个 | 同上 | 同上 | 3 |
| sql→rootserver（1） | api 声明在下、定义在上 | `data_plane/api/data_plane/ddl/ob_ddl_coordinator.h:50` | `rootserver/ob_local_management_service.cpp:2956` | 1（`sql/engine/cmd/ob_vector_index_refresh.cpp:592`） |
| storage→rootserver（4） | 同上 | `ob_ddl_coordinator.h:36-46` | 同一文件，从 :2939 起 | 5 |

指向 observer 的边合计 199 个符号，其中**向量索引占 169 个（85%）**，其余是 inner SQL 7、ex_rpc 8、req_time 5、end_trans_cb 5、tablet report 4、`print_service_status` 1（`vector_share.py`）。
- 向量索引的实现 include 了 storage（43 处）、sql（15 处）、rootserver（2 处）的头文件（`dir_includes.py`），比如 `ob_vector_index_util.cpp:25-37`。所以它本身就是上层子系统，不能直接下移。
- `ObVectorIndexUtil` 的头文件里有 148 行 `static` 声明、没有模板。下层用到了其中 70 个方法，共 170 处调用（sql 114、rootserver 34、storage 13、pl 9；`seam_calls.py`）。

### 2.2 只拆 12 条还不够：横向边自成 2 环

`acyclic_check.py` 在 include 边加链接边的 crate 图上，分几种情形算强连通分量：

| 情形 | 剩下的环 |
|---|---|
| 原样 | 12 个 crate 全在一个环里 |
| 只去掉 12 条 against | **{sql, storage}** |
| 再去掉 storage→sql（保留 sql→storage） | 无 |
| 再去掉 sql→storage 和 pl→storage（保留 storage→sql） | 无 |
| pl 并入 sql，并去掉 sql↔storage 两个方向 | 无 |

三条横向边是 sql→storage 271、storage→sql 99、pl→storage 7。前两条正反都在，本身就构成一个环，depmap 报告 §3.3 只说它们"在 include 层没有先后"。按声明位置看：
- sql→storage 的 271 个里，261 个声明在 `data_plane/api`，另有 8 个同时出现在 storage 自己的头文件里；
- storage→sql 的 99 个**全部**声明在 `query/api`。

Bazel 策略（`bazel/architecture/module_policy.bzl:79-91`）规定 sql 和 storage 都不能依赖对方。

### 2.3 成环符号的形状（决定 Rust 怎么拆）

`api_totals.py`、`shape.py`、`distinct.py` 的统计：

| | against 364 | across 377 |
|---|---|---|
| 声明在 api 头文件 | 189 | 376 |
| 声明在 `src/sql/pl` | 152 | 0 |
| 其他（weak、ex_rpc、end_trans_cb、ObIOManager） | 23 | 1 |
| 类的方法（含构造、析构） | 332 | 244 |
| 自由函数 | 32 | 133 |

- 去掉 sql↔pl 之后，需要逐项定拆法的是 **87 个类、152 个自由函数**。
- **自由函数有两种形状**：
  - 句柄型：`bool tx_desc_is_in_tx(const ObTxDesc *desc) { return OB_NOT_NULL(desc) && desc->is_in_tx(); }`（`src/storage/tx/ob_tx_api.cpp:1673`）。`data_plane::tx_desc_*` 在 sql 里有 69 处调用。
  - 服务型：`tmp_file_open` 取 manager，manager 为空就返回 `OB_NOT_INIT`（`src/storage/tmp_file/ob_tmp_file_manager.cpp:466`）。
- **值类型的方法体经常不依赖上层**：`ObTxReadSnapshot`、`ObTxExecResult` 的构造、`reset`、`assign` 只读写自己的字段（`ob_trans_define_v4.cpp:1142-1250`）。`ObReqTimeInfo` 只引用同一个 api 头文件里的类（`ob_req_time_service.cpp`）。`ObSqlEndTransCb` 的实现只 include sql 的头文件（`observer/mysql/ob_mysql_end_trans_cb.cpp:19-20`）。
- **依赖上层内部的方法体**：`sql/engine/basic/ob_pushdown_filter.cpp:28-35` include 了 sql 引擎和代码生成的头文件，向量索引见 2.1。

### 2.4 两个 api 层：意图和隔离程度

- **意图**：
  - Bazel 8 模块化（`4cb6fb268`，2026-08-07，改动 5,169 个文件）建立了"consumer → producer"的允许表（`module_policy.bzl:1-6, 28-110`），由 `module_dependency_aspect.bzl` 在分析期检查，**检查对象只是 BUILD 依赖**。
  - api 头文件自称"Coarse native seam … can be split by capability in the later interface-narrowing stage"（`src/query/BUILD.bazel:29-31`、`src/data_plane/BUILD.bazel:50-53`）；`ob_inner_sql_connection_access.h:48` 写的是"Transitional adapter"。
  - 也就是说，接口在下层 target，实现由最终链接（`liboceanbase`）提供。
- **规模**：224 个头文件，query/api 和 data_plane/api 各 112 个。两个模块几乎全是头文件：data_plane 99%，query 91%（`header_share.py`）。
- **由别的模块实现的有多少**：
  - 按同名实现文件算，68 个（任务说明里是 73 个，口径不同）；
  - 按符号算，**132 个**头文件里的类或函数，其非内联符号由别的模块定义：storage 62、sql 55、observer 15、rootserver 2、pl 2、share 1（`api_impl.py`，启发式，类名重名时可能多算）；
  - 另有 88 个是纯头文件（内联、模板、纯接口、类型）。
- **隔离了什么**：include 级的 crate 图是 DAG（`crates/cycles.txt`：0 个环）；102 种服务槽位类型都没有被它所在模块以下的模块使用（`service_slots.py`）。
- **没隔离什么**：741 个成环符号中有 565 个（76%）就是"api 声明、上层定义"。
- **接口很 C++**：224 个 api 头文件里有 `virtual` 797 处、`template<` 250 处、`ObIAllocator` 433 处、`ObIArray<` 276 处、`ObString` 393 处（`api_types.py`）。
- **已有的纯接口反转**：比如 `ObIDmlService`、`ObITransactionService`。下层定义虚接口，上层实现，再由组合根绑定（见 2.7）。这类调用走虚函数，不产生链接边，可以直接对应成 Rust 的 trait。

### 2.5 weak 符号和 `.cpp` 里的 extern 原型

- **数量**：`OB_WEAK_SYMBOL` 在 22 个文件里出现 36 次（不含 `#define` 和注释）；另有 5 处直接写 `__attribute__((weak…))`。可行性报告在旧基线上记的是 45 处。
- **生产构建里真正被覆盖的只有 6 个**，全部跨模块，就是 2.1 表里的 6 个。按 `weak_all.py` 逐个核对，约 40 处 weak 定义的情况是：
  - 跨模块被覆盖：6 个；
  - 模块内被覆盖：1 个（`futex_hook`）；
  - 本平台没有编译：7 个，即 4 个 `ObLogger::need_to_print*` 和 3 个 Linux malloc hook 别名；
  - 本次构建里从来没有强定义：26 个，比如 storage 下 tx 和 mds 相关的 11 个，它们是单元测试删除后留下的测试缝。
- **反例**：`lib_server_runtime_dispatch` 的 weak 版本在全局命名空间（`src/oblib/lib/task/ob_timer_service.cpp:23-24`，注释写着"strong definition in share"），而 share 里的"强定义"在 `oceanbase::common` 命名空间（`src/share/rc/ob_server_runtime.cpp:31-34`）。两者是不同的符号，覆盖从未生效；只是函数体相同，所以没有行为差异。这说明 weak 覆盖错了也没人发现。
- **`.cpp` 里自写 extern 原型**：一共 81 行，几乎都在模块内部（geo、datum 拆分编译，生成的系统包）。跨模块的逆向用法只有 `request_finish_callback` 一个。

### 2.6 跨模块的不透明前向声明

Rust 没有跨 crate 的前向声明。在下层头文件里前向声明、并且确实用到、完整定义却只在上层的类型共有 **273 个**（`fwd_used.py`，启发式）：
- query→sql 213 个，其中 185 个来自 X-macro 算子注册表 `query/api/query/engine/ob_operator_reg.h`（81 处 `REGISTER_OPERATOR`）；
- 其余约 28 个是 sql 的核心上下文类型：`ObExecContext`、`ObSQLSessionInfo`、`ObRawExpr`、`ObPhysicalPlanCtx`、`ObSubQueryIterator` 等，用在 `ob_expr_operator.h`、`ob_sql_expression.h`、`ob_pushdown_filter.h`、`ob_session_access.h` 里；
- data_plane→storage 20 个，data_plane→query 11 个（`ObExpr`、`ObIVector` 等）；
- oblib 也持有上层指针，比如 `lib::Worker` 的 `sql::ObSQLSessionInfo *session_` 成员（`src/oblib/lib/worker.h`）。

### 2.7 全局状态、组合根和已有的注册模式

计数用 `count_globals.py`：在 git 跟踪的 `src` 下的 C/C++ 文件里去掉注释后按词计数，不含 `#define` 行。

| 项目 | 基线处数（文件数） | 分布和说明 |
|---|---|---|
| `GCTX`（`src/share/ob_server_struct.h:65-111`） | 919（209）；带注释的原始计数 929，旧基线 `834bbee1e` 上是 894 | rootserver 463、sql 205、observer 143、storage 90、pl 16。只用到 16 个成员：`sql_proxy_` 307、`schema_service_` 294，其余是状态位和时间戳。指针成员在 `ObServer::init_global_context`（`src/observer/ob_server.cpp:2373-2398`）里一次性赋值；`status_`、`start_service_time_` 等在运行时改写（`ob_server.cpp:1119,1333`） |
| `GCONF` | **0**（旧基线 684） | `4dc99ec33`（#1457）把参数框架迁到了 Rust。C++ 现在调用 cxx 生成的 `config::xxx()`，共 644 处、247 个文件、259 个 getter（`count_config.py`） |
| MTL（`MTL(`、`MTL_*`、`ObTenantBase`） | **0** | 多租户已经移除。`ObServerRuntimeState` 的注释是"Process-wide runtime state for the one local database… no registry, routing key"（`src/share/rc/ob_server_runtime.h:51-53`） |
| `share::server_service<T>()` | 1,222（381） | 102 种类型，按声明模块：storage 43、data_plane/api 13、share 12、observer 11、sql 10…，最多的是 `storage::ObLSService` 163 处。槽位是 `ObServerServiceSlot<T>::service_` 静态指针（`ob_server_runtime.h:121-148`） |
| `::get_instance()` | 585（265） | 函数内静态单例，跨模块使用受 include 分层约束 |
| `THIS_WORKER` | 389（138） | `static __thread Worker *self_`，`Worker` 是虚基类，上层子类化（`src/oblib/lib/worker.h:33-174`） |
| 其他线程局部量 | `ObCurTraceId::` 388、`CURRENT_CONTEXT` 114、`WITH_CONTEXT` 29、`RLOCAL` 102、`thread_local`/`__thread` 152 | 属于并发和内存主题 |
| `OBSERVER` | 23 | 只在 observer 里用（`src/observer/ob_server.h:769`） |

**组合根**：
- `ObServer::init`（`ob_server.cpp:637` 起）依次初始化：日志 650 → 配置 653 → 时区 714 → sql 工厂 720 → GCTX 740 → sql proxy 744 → IO 753 → schema 773 → 网络 778 → sql 794 → storage 817 → `init_server_runtime` 833。
- `ObServerRuntime::create_modules`（`src/observer/omt/ob_server_runtime.cpp:420-452`）依次调用：
  - `obs_construct_modules`（`ob_server_runtime_controller.cpp:1181`）：构造 64 个模块，**在 1248-1357 行绑定 98 个槽位**；
  - `obs_init_modules`（:1361）；
  - `obs_start_modules`（:1510）。
- 出错时依次执行 stop、wait、destroy，并解绑槽位。standby 另外绑定了 2 个槽位（`src/standby/standby_module.cpp:209-210`）。

**已有的依赖反转写法**（都可以直接复用到 Rust）：
- 类型化槽位：`bind_server_service<query::ObILocalCommandService>(&ob_service_)`，即下层接口、上层实现。
- 显式接口：比如 `share::ObIMemstoreRuntime`（`ob_server_runtime.h:42-49`，注释"Narrow process-memory seam … implementation remains owned by Observer"）、`data_plane::ObLogServiceHandler`（`data_plane/api/data_plane/ob_log_service_handler.h`）。
- 注册函数：`sql::register_ddl_slice_store(this)`（`ob_server.cpp:643`，槽位在 `sql/engine/px/ob_ddl_slice_store.h:38-52`）。
- 函数指针工厂：`install_das_text_retrieval_engine_factory`（`sql/das/iter/sparse_retrieval/ob_das_text_retrieval_engine.h:78`）。它由 observer 里一个全局静态对象在 main 之前安装（`observer/composition/retrieval/ob_das_legacy_tr_merge_iter.cpp:1325-1335`）。另外还有 11 处 `constructor`/`init_priority` 属性。Rust 没有 main 之前的初始化，这些都要改成显式调用。

### 2.8 crate 大小、子目录环与编译时间

用 `sizes.py` 统计，数据来自 depmap 的 `units.tsv`，按 C++ 行数，含头文件：

| crate | 单元数 | 行数 | 备注 |
|---|---|---|---|
| sql | 1,300 | 965,298 | 并入 pl（36 个单元，39,141 行）后约 100 万行 |
| storage | 801 | 757,536 | 含 27.6 万行的分词词典 `ob_ik_dic.cpp`，需翻译的约 48 万行 |
| oblib | 515 | 305,544 | 头文件占 48%，有 2,262 个模板声明，被其他模块 include 4,641 次 |
| share | 406 | 234,208 | 头文件占 34%，有 1,046 个模板 |
| observer / rootserver | 266 / 106 | 130,967 / 104,293 | |
| logservice / query / data_plane / standby / objit | — | 32,704 / 30,639 / 16,155 / 14,872 / 2,486 | objit 只有 1 个头文件 |

- **关键路径**：sql 和 storage 平级时，`cargo check` 的关键路径是 oblib → share → data_plane → query → sql → rootserver → observer，约 182 万行 C++。sql（含 pl）一个就占全部需翻译代码的约四成。
- **sql 能不能按子目录拆**：子目录级是一个 18 节点的环（`crates/submodule_cycles.txt`）。按贪心排序后仍有 517 条 include 边指向反方向，分布在 57 对子目录之间，最大的几条是 das→engine 61、optimizer→engine 49、resolver→engine 41。
  - 试拆成"前端"（resolver、rewrite、optimizer、code_generator 等，54 万行）和"执行"（engine、das、dtl）两部分：前端用到了 167 个执行侧头文件，这些头文件在 sql 内部的 include 闭包有 **496 个文件、128,521 行**，还会顺带拖进 181 个前端文件（`split_probe.py`）。
  - inherent impl 必须跟类型在同一个 crate，所以这些类型的 `.cpp` 方法体也得一起搬。因此**不能机械地拆**。
  - storage 的子目录也是一个 30 节点的环，贪心排序后有 467 条反向边。
- **Rust 侧的已知事实**：`rust/Cargo.toml` 的 release 配置是 `lto="thin"`、`codegen-units=1`、`debug=true`、`panic="abort"`；工具链固定为 1.98.1（`rust/rust-toolchain.toml`）。
  - 稳定版 rustc 的前端（类型检查、借用检查）对单个 crate 基本是串行的。据我所知并行前端 `-Z threads` 仍需要 nightly，需要在 1.98.1 上核实。
  - 代码生成按 `codegen-units` 并行，而 `codegen-units=1` 会把 sql 的代码生成串行化。
- **估计**：sql 移植后约 60–90 万行 Rust（可行性报告的 0.6–0.9 倍假设）；冷 `cargo check` 3–10 分钟，峰值内存数 GB（假设单线程前端每秒 2–5 千行，**未实测**）。

### 2.9 已有 Rust 的约定

- **sql-nio**：通过 cbindgen 导出 33 个 `nio_*` 函数。它回调 C++ 的方式是在 Rust 里写 `extern "C"` 声明（`rust/sql-nio/src/ffi_types.rs:53`），由 C++ 定义（`src/oblib/rpc/obmysql/ob_sql_nio_server.cpp:39`），**本身就是链接期绑定**。整个 crate 有 186 处 `unsafe`。
- **config**：用 cxx bridge（由 `build.rs` 生成）加 cbindgen 的 C ABI 和 C 回调（`ConfigCheckCallback`），有 17 处 `unsafe`。全局状态都写成 `static …: OnceLock<Mutex/RwLock<…>>`（`rust/config/src/config.rs:21-26`）。
- **链接方式**：两个 staticlib 分别链接（`cmake/Rust.cmake:205-214`），每个都自带一份 std。CMake 只在 macOS、Windows 以外的平台链接时加 `--start-group`（`src/observer/CMakeLists.txt:90-100`）。打包构建开 thin LTO（`build.sh:428-438`），C++ 的跨模块调用在打包产物里可以被内联。

### 2.10 脚本清单（`/tmp/layering-survey-7d907/`）

| 脚本 | 作用 |
|---|---|
| `check_owner.py` | 核对目标文件所在模块和源文件所在模块一致：2,954 个源文件里，只有 48 个生成文件不在 `src` 下 |
| `edge_symbols.py` | 每条边的全部符号，以及引用它和定义它的目标文件 |
| `classify_edges.py`、`group_edges.py`、`shape.py`、`api_totals.py`、`distinct.py`、`vector_share.py`、`callsites.py`、`seam_calls.py` | 声明处、定义处、方法/自由函数、调用点 |
| `def_includes.py`、`dir_includes.py` | 定义文件和子系统 include 了哪些模块 |
| `acyclic_check.py` | 各种拆法之后的强连通分量 |
| `api_impl.py`、`api_basename.py`、`api_types.py` | api 层由谁实现、接口里的 C++ 词汇 |
| `weak_all.py` | 每个 weak 定义有没有强定义、在哪个模块 |
| `fwd_decls.py`、`fwd_used.py`、`fwd_query_sql.py` | 向上的前向声明 |
| `count_globals.py`、`count_config.py`、`service_slots.py` | 全局状态 |
| `sizes.py`、`split_probe.py`、`header_share.py` | 大小、子目录环、拆 sql 的代价 |

## 3. 选项

### 3.1 拆环：4 个做法（实际是按 seam 组合使用）

**A. 依赖反转：下层定义 trait，上层实现，启动时注册**

做法是给 C++ 的 `bind_server_service` 找一个 Rust 对应物：槽位放在类型或接口所在的 crate，组合根负责绑定。自由函数保留同名的转发函数，这样调用点不用改。

```rust
// share：通用槽位，对应 ObServerServiceSlot<T>
pub struct Slot<T: ?Sized + Sync + 'static>(OnceLock<&'static T>);
impl<T: ?Sized + Sync + 'static> Slot<T> {
    pub const fn new() -> Self { Self(OnceLock::new()) }
    pub fn bind(&self, s: &'static T) { assert!(self.0.set(s).is_ok(), "bound twice") }
    #[inline] pub fn get(&self) -> Option<&'static T> { self.0.get().copied() }
}
// data_plane：服务型 seam（对应 tmp_file_open）
pub trait TmpFileService: Sync { fn open(&self, dir: i64, label: &str) -> ObResult<i64>; /* … */ }
pub static TMP_FILE: Slot<dyn TmpFileService> = Slot::new();
pub fn tmp_file_open(dir: i64, label: &str) -> ObResult<i64> {
    TMP_FILE.get().ok_or(OB_NOT_INIT)?.open(dir, label)      // 空槽 = C++ 的 manager 为空分支
}
// data_plane：句柄型 seam（对应 tx_desc_*(const ObTxDesc*)）；sql 持有 Arc<dyn TxDesc>
pub trait TxDesc: Send + Sync + Any { fn is_in_tx(&self) -> bool; /* …约 35 个… */ }
pub fn tx_desc_is_in_tx(d: Option<&dyn TxDesc>) -> bool { d.is_some_and(|d| d.is_in_tx()) }
// observer 组合根（对应 obs_construct_modules）
data_plane::TMP_FILE.bind(&mods.tmp_file_manager);  // storage::TmpFileManager: TmpFileService
```

- **优点**：全是安全 Rust；和现有的 98 处绑定一一对应；sql 和 storage 可以并行编译；可以替换成 mock。
- **缺点**：
  - 每个 seam 都要定 trait 的形状。
  - 没注册就调用会得到运行时错误，而 C++ 在链接时就绑定好了。
  - 每次调用多一次原子读和一次间接调用。逐行调用的路径（`query::das_scan_next_row(s)` 在 storage 有 18 处，`spill_row_store_*` 9 处）估计每次多 1–2 ns，C++ 打包构建却能 LTO 内联。
  - 进入槽位的类型必须是 `Sync + 'static`。
- **对翻译**：由 seam 表驱动。声明所在的单元生成 trait、槽位和转发函数，定义所在的单元生成 `impl`，组合根追加一行绑定。
- **对对齐**：空槽时返回与 C++ 空指针分支相同的错误码。纯函数式的 seam 在 main 的最开头注册，服务型 seam 按 `obs_construct_modules` 的原顺序绑定。

**A′（A 的变体）. 链接期绑定**

下层用 `unsafe extern "Rust" { safe fn … }` 声明，上层用 `#[unsafe(export_name = "…")]` 定义，两边由同一个 trait 经宏生成，所以签名不会对不上。思路同 crates.io 的 `crate_interface`（ArceOS 用它解决同样的分层问题；版本需要核实，也可以自己写一个约百行的宏）。
- **优点**：
  - 和 C++ 的"声明在下、定义在上、靠链接器接上"完全同构；
  - 零开销，可以 LTO 内联；
  - 没有初始化顺序问题；
  - 和 sql-nio 的 `extern "C"` 回调是同一种形状，所以孤岛共存和最终形态用的是同一套机制。
- **缺点**：
  - `unsafe` 藏在宏里；
  - 缺实现只在链接时报错，`cargo check` 发现不了；
  - 不能用泛型，也不能 mock；
  - 方法体放在别的 crate 时，类型的字段必须是 `pub`。
- **适合**：经测量确认的热路径 seam，或者孤岛共存期间。

**B. 实现下移到声明所在的 crate**

适用于方法体只用到下层东西的情况，见 2.3 的值类型、`ObReqTimeInfo`、`ObSqlEndTransCb`、`print_service_status`。`ob_strerror` 的错误表由生成器直接写进 oblib。`get_cpu_count` 由 oblib 直接读 `config` crate，`config` 是叶子 crate，只依赖 cxx。
- **Rust 的硬约束**：inherent impl 必须和类型在同一个 crate，所以 576 个方法符号里能下移的都应该下移。
- **对翻译**：manifest 要做**函数级重映射**。比如 `ob_trans_define_v4.cpp` 里属于 `ObTxReadSnapshot` 的函数，要归到 `data_plane/.../ob_tx_read_snapshot.h` 对应的单元。
- **对对齐**：代码原样不变，没有风险。
- **不适用**：方法体依赖上层内部的情况（`ob_pushdown_filter.cpp`、向量索引）。

**C. 合并 crate**
- **适用于 sql+pl**：两者互相递归，类型（`ObPLDataType`、`ObObjAccessIdx`……）互相按值使用，反转不了。团队也已经在走这条路：`src/pl` 的 34 个头文件大多只剩 20 行的转发头，注释写着"Compatibility path during the SQL/PL ownership migration"（`src/pl/ob_pl.h:17-20`），33 对同名文件分在两个目录。代价是 sql crate 多 4%。
- **不适用于 sql+storage**：合起来约 150 万行需翻译的 C++，只有一个 crate，笔记本上的编译和内存风险最高；全部合并约 230 万行，更不行。

**D. 门面 crate**

把所有 seam trait 和槽位集中到一个 crate，比如把 data_plane 和 query 合成 `api`，约 4.7 万行。
- **优点**：seam 集中，便于审查；顺带解决 data_plane 前向声明了 11 个 query 类型的问题。
- **缺点**：任何一个 seam 改动都要重编所有上层，这一点和今天改 api 头文件一样。它也不解决"方法体在上层"的问题，本质上只是 A 的 trait 放在哪里。也可以不合并，只加一条 data_plane → query 依赖：query 不依赖 data_plane，加了也不成环。

### 3.2 全局状态：2 个做法

**G1. `OnceLock`/`LazyLock` 静态量加类型化槽位（复刻 C++）**

```rust
pub struct GlobalContext {                       // share；对应 ObGlobalContext
    pub schema_service: OnceLock<&'static MultiVersionSchemaService>,
    pub sql_proxy: OnceLock<&'static MySqlProxy>, /* … 指针成员 … */
    pub status: AtomicU8, pub start_service_time: AtomicI64, pub in_bootstrap: AtomicBool, /* … */
}
pub static GCTX: LazyLock<GlobalContext> = LazyLock::new(GlobalContext::default);
// GCTX.sql_proxy_            → share::GCTX.sql_proxy.get()   （Option，保留 OB_ISNULL 分支）
// server_service<ObLSService>() → storage::LS_SERVICE.get()   （槽位跟着类型走）
// X::get_instance()           → X::instance()（static LazyLock<X>）
```

- **规则**：都是纯文本级的，最机械。和 config 的现有写法一致。
- **代价**：
  - 约 102 种槽位类型和若干单例都必须是 `Sync`，这与并发主题耦合；
  - `OnceLock` 不能解绑，而 C++ 在 destroy 时会解绑。

**G2. 显式上下文 `&ServerCtx` 逐层传递**
- **问题**：所有直接或间接用到服务的函数都要改签名。光 GCTX 和 `server_service` 就有 2,141 处使用，分布在至多 590 个文件里，而改签名需要调用图。depmap 只有 include 级，没有调用图，按文件独立翻译做不到。C ABI 共存期间，C++ 调用方也拿不到这个上下文。
- **好处**：在可测性上更好，对齐上没有区别。
- **适用范围**：请求级的上下文（`ObExecContext`、session）在 C++ 里本来就是显式传递的，照旧即可。

### 3.3 迁移策略：3 种做法

| | S1：kit 默认，核心一次性翻译 | S2：按子系统 C ABI 共存（可行性报告 §6） | S3：先在 C++ 里拆环，再走 S1 |
|---|---|---|---|
| 需要什么 | 翻译前把 12+1 条边的拆法写进 seam 表；crate DAG 和 `Cargo.toml` 先定；编译按 crate 自底向上分波次：oblib → share → data_plane/logservice/query → sql∥storage → rootserver → standby → observer（`cargo check --keep-going`） | 每个迁过去的 crate，它跨越的所有边界都要有 C ABI 或 cxx | 在 fork 上提交 C++ 重构，由裁判验证；CI 加上链接级检查（`symdeps_seekdb.py`） |
| 与 crate 环的关系 | 设计期解决 | 环如果经过 C++ 一侧，靠 extern 链接期绑定也能容忍，这正是 C++ 的做法 | 重构后 C++ 的链接图就无环，Rust 可以一一映射 |
| 可行性 | 可行；第一次能跑要等约 230 万行都编译通过 | **核心不可行**。<br>• 自底向上：C++ 上层对 oblib 有 4,641 条 include 边，oblib 有 48% 的代码在头文件里，模板和内联代码会编进每个 C++ 翻译单元，放不到 C ABI 后面；链接级还有约 3,685 个"上层 → oblib"引用。结果是共存期间要维护两份 oblib，并保持逐位一致（哈希、序列化、错误码）。<br>• 自顶向下：observer 调用下层约 2,770 个符号。<br>• 对照：sql-nio 的边界只有 33 个函数加 4 个回调，就用了 186 处 unsafe，还带来 2 次行为回归 | 可行；工作量估计 4–7 个 agent 周，每次修改要做 7 分 44 秒的 C++ 构建 |
| 对齐信号 | 晚，到第 5、6 步 | 每步都有，但只限孤岛 | 重构本身能早验证，但它本来就几乎不改行为，验证的价值不大 |
| 适用 | 核心 | 接口窄的孤岛：已完成的 config、sql-nio；可选的纯函数组件（压缩、向量距离、分词器）可以当约定试点 | 想尽早验证 seam 设计时 |

## 4. 推荐

1. **crate 图**：
   - crate 列表：`oblib`、`share`、`data_plane`、`logservice`、`query`（objit 并入或单列）、`sql`（含 pl）、`storage`、`rootserver`、`standby`、`observer`（bin，即组合根），加上已有的 `config`、`sql-nio`。可选：从 observer 中拆出 `vector_index` crate，放在 rootserver 之上、observer 之下。
   - 依赖关系等于 include 级 DAG 并符合 Bazel 策略；sql 与 storage 互不依赖。
2. **拆环规则，逐项写进 seam 表**：
   - 由脚本从 nm 结果和头文件生成 `migration/design/seams.tsv`。每个符号一行：声明处和定义处的 file:line、调用方、形状、规则、Rust 落点。像 depmap 一样经过两名审查者核对，翻译 agent 按表执行。
   - 规则的优先顺序：C 合并（只用于 sql+pl）→ B 下移（方法体只依赖下层）→ A trait 加槽位或句柄 trait，在组合根绑定（服务、句柄、类层次）→ A′ 链接期 seam（只用于测量确认的热路径）。
3. **全局状态用 G1**：槽位跟着类型或接口放在同一个 crate；GCTX 结构体里，指针成员用 `OnceLock`，运行时会变的成员用原子量；config 直接调 `config` crate；线程局部量交给并发主题。组合根照抄 `obs_construct_modules`、`obs_init_modules`、`obs_start_modules` 的顺序；纯函数式的 seam 和 weak 钩子在 main 的第一行注册。
4. **策略选 S1**（核心一次性翻译加分波次编译闸门）。C ABI 共存只用在接口窄的孤岛上；不规划按模块共存。S3 可选（见 §5 Q8）。
5. **sql 先不拆**：第 2 步实测后再定（见 §5 Q9）。cargo 配置要调整：大 crate 单独设置 `codegen-units`，开发构建用 `debug = "line-tables-only"`，词典和时区这类数据表改用 `include_bytes!` 放进单独的小 crate，不写成 Rust 源码。

**逐边拆法**（工作量单位是 agent 工作量的估计：S ≤ 2 天，M ≈ 1 周，L ≥ 2 周）：

| 边 | 推荐拆法 | 落点 | 工作量 |
|---|---|---|---|
| oblib→share（4 个函数） | `ob_strerror`：B，错误表由生成器写进 oblib。`get_cpu_count`：B，直接读 `config::cpu_count()`。`allow_next_syslog`、`current_resource_owner_id`：在 oblib 设 `OnceLock<fn>` 钩子，默认行为就是现在 weak 版本的函数体，由 share 注册 | oblib | S |
| oblib→observer（1） | A：oblib 定义 `trait InnerConnFactory` 和槽位，由 observer 绑定；空槽返回 `OB_NOT_SUPPORTED`，与现在 weak 版本的函数体一致 | oblib | S |
| oblib→storage（1），含 sql 那一次调用 | oblib 设钩子 `request_finish_callback`（默认什么也不做），由 storage 注册；observer 和 sql 改调 oblib 的包装函数 | oblib | S |
| share→observer（2） | `print_service_status`：B，回到 share。ex_rpc：A，share 定义 `trait TaskDispatcher`（`Box<dyn FnOnce()->i32 + Send>`）和槽位 | share | S |
| sql→pl（152） | C：`src/pl` 并入 sql crate，作为 `sql::pl` 模块；`src/sql/pl/X.h` 与 `src/pl/X.cpp` 合成一个单元 | sql | S，只改 manifest |
| →observer 的向量索引（169） | A：query 定义 `VectorIndexUtil`（约 70 个方法）、`VectorIndexAdaptor`、结果迭代器、IVF cache 等 trait 和槽位，实现放在 `vector_index` 或 observer；其中只依赖 schema 和参数的辅助函数用 B 下移到 query | query | L |
| sql、storage、rootserver、pl→observer 的其余部分（27） | end_trans_cb：B，到 sql。req_time：B，到 query。inner SQL：A，query 定义 `InnerSqlAccess`，约 20 处调用。tablet report：A，data_plane 定义 `TabletReport`。ex_rpc 的做法同上 | 各自 | S–M |
| sql/storage→rootserver（5） | A：data_plane 定义 `trait DdlCoordinator`（5 个函数），由 rootserver 实现、组合根绑定 | data_plane | S |
| sql↔storage 横向（377） | 双向反转。句柄型函数（`tx_desc_*`、lob/json lob、fts 迭代器）用 A 的句柄 trait；服务型（`tmp_file_*`、锁）用 A 的槽位，空槽返回 `OB_NOT_INIT`；值类型方法用 B 下移到 data_plane 或 query；`ObExpr`、下压过滤器族和 SQL IR 主题一起定 | data_plane、query | L |

**前提**：
- seam 表要完整，所以链接级数据要在 Linux 构建上补跑一次（DEV-007：本次只有 macOS）；
- 向量索引和下压过滤器的 trait 形状要由对应主题确认；
- 第 2 步要测出最大 crate 的编译代价。

## 5. 需要人决定的问题（★ = 推荐）

1. **拆环总原则**：
   - A ★ 规则化混合：合并 → 下移 → trait 加启动注册 → 链接期 seam 只作为逃生口。
   - B 全部用链接期 seam（A′）。结构与 C++ 一一对应，最机械，但 `unsafe` 宏会贯穿 741 个符号，字段也要 `pub`。
   - C 合并成少数几个大 crate（sql+storage 约 150 万行）。
2. **pl 怎么处理**：
   - A ★ 并入 sql crate。
   - B 保留独立 crate，sql→pl 的 152 个符号全部反转（其中有按值使用的类型，几乎做不到）。
3. **query 的定位**：
   - A ★ 作为独立 crate，位于 sql 和 storage 之下。约 28 个 sql 核心类型改用 trait 或句柄表达；`ob_operator_reg.h` 的注册表搬回 sql。
   - B 把 query 并入 sql。这样 storage 就要依赖 sql，sql→storage 的 271 个符号全部反转，而且 sql 和 storage 只能串行编译，关键路径再加约 48 万行。
4. **sql↔storage 的方向**：
   - A ★ 双向反转，两者互不依赖。
   - B 允许 sql 直接依赖 storage，只反转 storage→sql 的 99 个符号。这违反 Bazel 策略，编译也会串行。
5. **全局状态**：
   - A ★ G1，静态量加类型化槽位。
   - B G2，显式上下文。
6. **槽位能否解绑、进程能否在内部重启**：
   - A ★ 不支持。与当前 master 一致：嵌入式模式已在 `0a373ba54` 移除；关停靠显式的 stop、wait、destroy。
   - B 支持。槽位改用 `ArcSwapOption` 或 `RwLock<Option<…>>`，每次读取多一点开销，为将来的嵌入式模式留余地。
7. **迁移策略**：
   - A ★ S1：核心一次性翻译，按 crate 分波次编译；C ABI 只用于孤岛。
   - B S2：按模块共存。
8. **拆环在哪里做**：
   - A ★ 在设计文档的 seam 表里定，翻译时按表实现，基线 `7d907abfa` 保持不变。
   - B 先在 C++ 上做（即 S3）：在 fork 本地提交，裁判验证通过后再翻译。
9. **sql crate 什么时候拆**：
   - A ★ 第 2 步用试译或合成的 30–50 万行 crate，在 16 GB 笔记本上实测冷 `cargo check` 和峰值内存。如果超过 10 分钟或 12 GB，就沿 IR 重设计切出 `sql-ir`/`sql-engine`。
   - B 现在就拆，需要先抽出约 13 万行头文件闭包，以及对应的方法体。
10. **weak 符号**：
    - A ★ 生产构建里被覆盖的 6 个，改为下移或启动时注册的钩子；其余的按普通函数翻译，包括本次构建里从未被覆盖的 26 个，以及本平台没有编译、需要在 Linux 上复核的 7 个。
    - B 全部约 40 处都改成钩子。

## 6. 对其他主题的依赖和影响

- **SQL IR 图**：
  - `ObExpr`、`ObOperator` 声明在 query，实现在 sql；storage 在下压时调用它们。
  - IR 用 arena 加索引还是指针，决定了 query 能不能不引用 sql 类型就承载表达式运行时。约 28 个上下文类型是交界面。
  - `ob_operator_reg.h` 的注册表应改成 sql 内部由宏生成的表。
- **内存与分配器**：
  - api 签名里有 433 处 `ObIAllocator`，所以 seam trait 要能接收 `&dyn Allocator` 或带生命周期的 arena 引用。分配器 trait 必须放在 oblib，并且是对象安全的。
  - 带 arena 生命周期的类型做不成 `dyn Any`，影响句柄 trait 的向下转型。
- **并发**：
  - 进入槽位或 `LazyLock` 的类型都要说明自己为什么是 `Sync`（102 种槽位类型加单例）。
  - `THIS_WORKER`（389 处，虚基类加 `__thread`）、`ObCurTraceId`（388 处）要改成 `thread_local!` 方案。
  - ex_rpc 的派发要求闭包是 `Send`。
- **存储**：
  - data_plane 的句柄型 seam（`ObTxDesc*` 等）改成 `Arc<dyn TxDesc>` 之后，storage 拿回句柄时要向下转型。tx desc 的生命周期和所有权由存储主题定。
- **解析器**：
  - bison 生成的 C 解析器建议做成 sql 之下的 `-sys` crate。`_ParseNode` 在 oblib 和 share 里有前向声明，这是核心内部唯一天然的 C 边界。
- **约定**：
  - crate 和路径规则：`rust/port/<module>`，pl 映射到 sql。
  - seam 命名：每个 api 头文件对应一个 trait。
  - manifest 要支持函数级重映射。
  - 生成器的输出目标：errno 表写进 oblib，系统变量和内部表写进 share。
  - 数据表用 `include_bytes!`。
  - cargo 配置：大 crate 单独设 `codegen-units`，调整 debuginfo。
  - 共存期间所有 Rust crate 合并成一个 staticlib 链接，避免像现在 `libsql_nio.a`、`libconfig.a` 那样各带一份 std。
