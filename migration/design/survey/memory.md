# 调查备忘：内存与分配器模型

> 设计文档主题调查，只读。基线 `7d907abfa`：`git diff --stat 7d907abfa..HEAD -- src deps rust` 为空，工作区里的 `src/`、`rust/` 就是基线。日期 2026-10-11。
> 计数都跑在去掉注释和字符串的镜像上：镜像由 `python3 -I migration/feasibility/scripts/arch/strip.py "$PWD" migration/feasibility/results/arch/gen.ignore <mirror>` 生成，生成文件已排除。脚本在 `/tmp/memsurvey-9f3a2c/scripts/`，按规则没有入库，核心正则见附录。正则计数都是近似值；标「估」的是推断。

## 1. 结论

1. **推荐组合**：查询和批次作用域的数据放进 bump arena，借用标 `'q` 生命周期。arena 里的类型必须不需要 Drop；图结构的边用 typed id，具体由 IR 主题定。会话、计划缓存、schema、存储元数据等长生命周期对象一律改成拥有型（`Box`/`Vec`/`Arc`，走全局分配器），`ObIAllocator` 参数和手动析构随之消失。
2. **全局分配器**：用自带 jemalloc 做一层薄封装，加一个线程局部的 `MemAttr`，只做 ctx 级的诊断计数，不做限额。限额交给显式的 `MemBudget`，在 C++ 原来的检查点执行：11049 查询上限、工作区落盘、向量上限等。
3. **不采用 allocator_api / allocator-api2 容器参数化作为通用规则**。钉住的工具链 1.98.1 没有 allocator_api；1.100（计划 2026-11-12 发布）只稳定 `Allocator` trait 和 `Vec`/`Box` 的构造。
4. **分配失败**：`OB_ALLOCATE_MEMORY_FAILED` 映射为 `ObError::AllocateMemoryFailed`（-4013）。arena 分配一律用 `try_*`；大小取决于用户数据的缓冲一律 `try_reserve`；小的固定大小分配允许 OOM 时直接 abort。
5. **理由**：CI 和发布构建默认用的 jemalloc 后端已经忽略 label/ctx，而且两个后端都没有任何硬上限生效；裁判不覆盖分配失败；这个组合的翻译规则最机械，自写的 unsafe 只集中在一个 crate 里。

## 2. 事实

### 2.1 接口、实现和两个后端

- **接口** `ObIAllocator`（`src/oblib/lib/alloc/ob_iallocator.h:66-122`）：
  - 虚函数 `alloc(size)`、`alloc(size, attr)`、`free(ptr)`（:74-90）。`free` 不带尺寸；经 `ObIAllocator &` 调用时，每次分配都是一次虚调用。
  - `total()`/`used()`/`reset()`/`reuse()` 默认都是空实现（:105-114）。
- **实现**：62 个类，其中直接继承 53 个（oblib 32、storage 17、share 5、query 3、sql 3、logservice 2）。可行性报告在旧基线上数的是 60/51。
  - 有 12 个的 `free()` 是空操作，包括 `ObArenaAllocator`、`ObSafeArenaAllocator`、`ObDataBuffer`、`ObExprStrResAlloc`、`ObInplaceAllocator`、`ObReserveArenaAllocator`，以及 memstore 的 `AllocHandle`（`ob_memstore_allocator.h:75-117`）。
- **`ObArenaAllocator`**（`src/oblib/lib/allocator/page_arena.h:975-1079`）：
  - 页式 bump 分配。默认页大小 8 KB（`OB_MALLOC_NORMAL_BLOCK_SIZE`，`ob_define.h:1305`），16 字节对齐（`page_arena.h:661`）。
  - `free` 什么也不做（:1020）。`reset()` 释放全部页（:1029）；`reuse()` 保留页，从头重用（:1036、:945-949）。
  - 检查点接口 `set_tracer`/`revert_tracer`（:1040-1047）在仓库里没有外部调用者。
  - 页来自 `ModulePageAllocator`：没绑定分配器时直接走 `ob_malloc(attr)`（:126-131）。
- **`ObMemAttr`**（`alloc_struct.h:128-151`）：由 `label_`（不超过 15 个字符，编译期断言，:84）、`ctx_id_`、`sub_ctx_id_`、`prio_` 组成。
  - **没有 tenant id，seekdb 是单租户**：`MTL(` 0 处，`server_service<` 1,218 处；内部表定义里写着 "single-tenant GV/V collapse"（`src/share/inner_table/ob_inner_table_schema_def.py:7442`）。
  - ctx 只剩 8 个（`src/oblib/lib/utility/ob_mod_define.h:18-26`），另有 11 个旧名字是 `DEFAULT_CTX_ID` 的别名（:480-490）。
- **两个后端**（`ob_malloc.h:38-44`），由环境变量 `MALLOC_BACKEND` 选择（`ob_malloc.cpp:123`）：
  - **jemalloc**：CMake 在 Linux/macOS 上做非 ASAN 构建时的默认值（`ob_malloc.cpp:144-160`；jemalloc 只在这些平台编译，`deps/external/CMakeLists.txt:17`）。CI 和发布用的正是这条路径（`./build.sh release`，`.github/workflows/buildbase/action.yml:31`）。`ob_malloc` 直接调用 `jemalloc_malloc(nbyte)`，**完全忽略 `ObMemAttr`**（`ob_malloc.h:118-124`）：不按 label/ctx 计账，也没有上限。
  - **obmalloc**：Windows、Android、ASAN 构建的默认值，也可以显式指定。Bazel 构建只在 sanity 配置下才链接 jemalloc（`src/observer/BUILD.bazel:525-530`），所以 Bazel 的普通构建默认也是 obmalloc。这个后端按 ctx 分派给 `ObCtxAllocator`，按 label 计账（`ob_malloc_allocator.cpp:61-90`）。
  - `ObMalloc`（`ob_malloc.h:380-415`）只是把 `ob_malloc`/`ob_free` 包成 `ObIAllocator`，固定一个 attr。`OB_NEW(T, label, …)` 走 `ob_malloc` 再 placement new（:628-637）；`OB_NEWx(T, pool, …)` 走传入的分配器（:650-661）。
  - 演进方向：提交 `38bd9c622`（2026-08-12）"Refine jemalloc integration and module memory limits" 把限额收拢到了模块级。按后端分支的代码只有 17 处，分布在 6 个文件里。
- **`malloc` 被整体替换**（`src/oblib/lib/alloc/malloc_hook.cpp:154-`）：
  - jemalloc 后端下直接转发。
  - obmalloc 后端下，按线程局部属性 `ObMallocHookAttrGuard::get_tl_mem_attr()`（`alloc_struct.h:560-573`，默认 `"glibc_malloc"`/`GLIBC`）调用 `ob_malloc`；**失败时每 10 ms 重试一次，永不返回 NULL**（`malloc_hook.cpp:75-85`）。
  - `ObMallocHookAttrGuard` 共 105 处，分布在 36 个文件里（vector_index 占 46 处）。也就是说，选项 (d) 的「线程局部当前属性」C++ 已经在用。
  - 现有的 Rust crate 没有 `#[global_allocator]`，走的是 libc `malloc`，所以同样经过这个钩子。
- **`MemoryContext`**（`src/oblib/lib/rc/context.h`）：
  - 是一棵上下文树。每个节点有一个可单独释放的分配器、一个 arena 和一个加锁的 arena（:397-399）。
  - `CURRENT_CONTEXT` 指向线程局部的上下文栈 `Flow`（:725-753）。
  - jemalloc 后端下，hold = `MemoryContextMalloc` 的存活字节 + arena 的页（:366-391、:480-487）；`tree_mem_hold()` 递归求和（:600-612）。
  - 另有 `MemoryUsageTracker`（`ob_malloc.h:183-220`），目前只有 `WORK_AREA` 注册了解析函数（`src/sql/engine/ob_sql_memory_manager.cpp:431`）。
- **容器怎么绑定分配器**：
  - `ObSEArray`/`ObArray` 默认用 `ModulePageAllocator`（`ob_array.h:268`、:858），也就是全局的 `ob_malloc`。
  - 带 `auto_free=true` 的，第一次扩容时才**惰性绑定当前线程的 `CURRENT_CONTEXT`**（`ob_array.h:484-497`）。
  - `ObFixedArray<T, ObIAllocator>`、`ObList<T, ObIAllocator>` 在初始化时显式传入分配器。

### 2.2 限额，以及碰到限额时用户看到什么

- **全局和 ctx 级的硬上限在基线里从来没有设置过**：
  - `ObMemoryMgr` 的 `hard_limit_` 和 `hard_limit_bytes_[]` 初值都是 `INT64_MAX`（`ob_resource_mgr.cpp:30-40`）。检查在 `update_hold`（:206）和 `update_ctx_hold`（:234）里，但全仓库找不到 `set_allocator_hard_limit` 或 `set_ctx_hard_limit` 的调用者（grep 只命中定义）。
  - `_ctx_memory_limit`（`rust/config/parameters.yaml:525`）经 `ob_server_duty_task.cpp:56-87` 只写到软上限 `limit_bytes_`（`ob_resource_mgr.cpp:157-166`）。软上限只用于打印和 `set_idle` 的参数校验（`ob_ctx_allocator.cpp:284-293`），**不参与分配判断**。
  - `AChunkMgr` 的 `hard_limit_` 同样是 `INT64_MAX`（`achunk_mgr.cpp:77`）。
- **相关参数**（全部已经在 Rust 的 `rust/config/parameters.yaml` 里）：
  - `memory_budget`（:117）：各模块上限都按它推算。
  - `memory_limit`（:126）：**已废弃**，原文是 "accepted and persisted, but is ignored"。
  - `kvcache_memory_limit`、`memstore_memory_limit`、`vector_memory_limit`（:135-158）。
  - `enable_sql_operator_dump`、`_chunk_row_store_mem_limit`、`_sort_area_size`、`_hash_area_size`（:346-375）。
  - `query_memory_limit_percentage`（:2083）、`range_optimizer_max_mem_size`（:1771）。
  - 系统变量 `ob_sql_work_area_percentage`（`ob_system_variable_init.cpp:1731`）。

| 机制 | 用户看到的结果 | 证据 |
|---|---|---|
| 分配返回 NULL | 本条语句报 **ERROR 4013 (HY001) "No memory or server runtime memory limit reached"**，会话保留 | `src/oblib/lib/ob_errno.h:43`；`src/share/ob_errno.cpp:173-181`；`mysql_errno=-1` 时客户端收到的是 `-ret`（:15614-15621） |
| 单条 SQL 超过 `query_memory_limit_percentage` | **ERROR 11049**，消息里带 `mem_limit`/`mem_hold` 字节数 | `src/sql/executor/ob_memory_tracker.cpp:30-56`；`ob_errno.cpp:13799-13807` |
| 解析器分配失败 | `OB_PARSER_ERR_NO_MEMORY` 也是 -4013，经 longjmp 返回 | `parse_define.h:26`；`sql_parser_base.c:106-118` |
| 向量模块超过 `vector_memory_limit` | 分配返回 NULL（局部错误码 `OB_ERR_VSAG_MEM_LIMIT_EXCEEDED`） | `src/storage/allocator/ob_vector_allocator.cpp:131-156` |
| 工作区超过 `_sort_area_size` 等 | 落盘，不报错。落盘点取决于算子自己报告的用量 | `ob_sort_op_impl.h:716` |
| 查询范围抽取超过 `range_optimizer_max_mem_size` | 退化成全范围扫描：计划变，结果不变 | `ob_expr_range_converter.cpp:111-113`；`ob_range_graph_generator.cpp:66-71` |
| memstore 到达阈值 | 写入限流并触发冻结。写满时返回什么，本次没有追到 | `parameters.yaml:622-644` |

- **11049 的检查点**：一共 9 处，不是每次分配都查。包括 `ob_th_worker.cpp:245`（守卫）和 :333、`ob_exec_context.cpp:679`、`ob_transform_rule.cpp:287`、`ob_select_resolver.cpp:1006`、`ob_raw_expr.h:5077`，以及解析器包装 `ob_memory_tracker_wrapper.cpp:22-27`。
- **内存视图**：
  - `V$OB_MEMORY`（`ob_inner_table_schema_def.py:7446-7466`）按 ctx/label 汇总 `__all_virtual_memory_info`（:3315）。
  - 其他：`__all_virtual_ctx_memory_info`（:4966）、`__all_virtual_memory_context_stat`（:3688）、`__all_virtual_malloc_sample_info`（:5725）、`V$SQL_WORKAREA*`（:7833/:7871/:7910）、`V$OB_VECTOR_MEMORY`（:13813）。
  - `__all_virtual_memory_info` 只遍历 `ObCtxAllocator` 的 label（`ob_all_virtual_memory_info.cpp:143-148`）。默认 jemalloc 后端下，`ob_malloc`、`OB_NEW`、MemoryContext 和 arena 的页都绕过 `ObCtxAllocator`，能统计到的只剩少数直接用 ctx 分配器的地方，比如 `src/oblib/rpc/ob_sql_mem_pool.cpp:46`。
- **裁判（`migration/judge`）**：
  - `grep -rl 'HY001\|No memory or server\|Exceed query memory\|ERROR 4013\|ERROR 11049\|--error 4013\|--error 11049' migration/judge` 结果为空，**没有任何用例期待内存类错误**。
  - 18 个用例执行 `alter system set vector_memory_limit = 0`；2 个用例修改 `ob_sql_work_area_percentage`，用来压出落盘路径（`join_many_table.test:13`）。
  - 裁判报告把「`information_schema` 列出全部内部表」（`00b-judge-report.md:173`）和「没有 ORDER BY 时的行序」（:176）列为待定。

### 2.3 使用形态

| 形态 | 处数 | 文件数 | 来源 |
|---|---|---|---|
| 出现 `ObIAllocator` | 9,194 | 2,489 | census |
| 参数里带 `ObIAllocator &`/`*` 的签名（声明和定义分别计） | 7,147 | — | sigs |
| 其中同时带 `T *&` 输出参数（在被调方分配、经输出参数返回） | 1,084 | — | sigs |
| 借用分配器的成员 `ObIAllocator *x_;` / `&x_;` | 309 / 200 | 229 / 158 | census |
| `ObArenaAllocator` 声明（其中成员 382 个，其余多为局部变量） | 1,177 | 597 | census |
| placement new | 2,072 | 720 | unsafe_share |
| ├ 前 8 行内有 alloc（先分配再构造） | 1,564 | — | proximity |
| ├ `new (this)` / `new (&member)`（原地重建） | 41 / 134 | — | proximity |
| └ 其余（容器、对象池内部） | 333 | — | proximity |
| 手动析构 `->~T()` | 1,264 | 493 | unsafe_share |
| └ 3 行内紧跟 free/reset/destroy | 707 | — | proximity |
| `OB_NEW*` / `OB_DELETE*` / `ob_delete` | 657 / 133 / 44 | 221 / 50 / 29 | census |
| `ObString` 成员（share/schema 176、sql/resolver 147、`ob_rpc_struct.h` 98） | 1,032 | 271 | census |
| `ob_write_string` / `deep_copy*` 调用 | 593 / 1,996 | 179 / 567 | census |
| 裸指针成员 / `T *&` | 6,099 / 9,454 | 1,471 / 2,126 | unsafe_share |
| `ObSEArray`（5,060 处用默认 `ModulePageAllocator`）/ `ObArray`（默认 2,399） | 5,621 / 2,450 | 825 / 574 | containers |
| `ObFixedArray`（全部显式 `ObIAllocator`）/ `ObList`（52 处 `ObIAllocator`） | 424 / 67 | 151 / 39 | containers |
| `ModulePageAllocator, true>`（`auto_free`，惰性绑定 `CURRENT_CONTEXT`） | 563 | 83 | census |
| `ObIArray<T> &` 参数：只读 / 可写（输出数组，元素由调用方的分配器承载） | 6,514 / 8,398 | — | census |
| 对分配器调用 reset/reuse/clear | 603 | 278 | census |
| 读 `used()`/`hold()`/`total()`，用于判断或打印 | 196 | 77 | census |
| `CURRENT_CONTEXT` / `CREATE_WITH_TEMP_CONTEXT` / `MemoryContext` 成员 | 114 / 24 / 76 | 74 / 18 / 62 | census |
| `ret = OB_ALLOCATE_MEMORY_FAILED` | 3,983 | 1,157 | census |
| `OB_ISNULL(x = …alloc…(…))` | 2,203 | 704 | census |
| 按 `OB_ALLOCATE_MEMORY_FAILED` 分支（`==`/`!=`） | 78 | 47 | census |
| 拥有分配器成员的类 / 其中还持有裸指针、`ObString` 或 arena 容器成员（自引用形态，是上限值） / 只借用分配器的类 | 462 / 288 / 430 | — | selfref |

- **按模块看 `ObIAllocator` 出现次数**：sql 5,061、storage 1,526、oblib 799、share 719、query 296、observer 295、rootserver 217、data_plane 144、pl 107。
- **按 manifest 目录粗分 3,754 个单元**：
  - 以查询/批次作用域为主：解析、解析器、改写、优化、代码生成 376 个；执行引擎、DAS、PL 1,016 个。
  - 长生命周期核心（计划缓存、会话、schema）：100 个。
  - storage：801 个，几种生命周期混在一起。
  - 其余，以拥有型为主：1,314 个。
  - 分配器、容器库本身：128 个。这部分应该重写，不按文件翻译。

### 2.4 分配器的生命周期

| 生命周期 | C++ 做法 | 证据 |
|---|---|---|
| 进程 / 单租户 | `ObMallocAllocator` 单例；各模块分配器挂在 `server_service<>` 上 | `ob_malloc_allocator.cpp:151-155` |
| 会话 | 多个字符串池，系统变量用两个缓冲区交替以便压缩；PL 游标有自己的 context | `ob_basic_session_info.h:1725-1744`；`ob_sql_session_info.h:321` |
| 请求 / 语句 | 工作线程每个请求 `CREATE_WITH_TEMP_CONTEXT`；结果集、`ObExecContext`、解析树、stmt、raw expr、逻辑计划都从 `CURRENT_CONTEXT->get_arena_allocator()` 分配；重试和多语句再套一层临时 context | `ob_th_worker.cpp:239-245`；`obmp_query.cpp:469-475`、:804-805；`ob_exec_context.h:795` |
| 缓存的计划 | 每个计划自带 `MemoryContext` 和引用计数；`get_mem_size()` 取分配器的 `total()`，计划淘汰据此计算 | `ob_i_lib_cache_object.h:60`、:102-104 |
| 算子 | 20 个 sql/engine 文件里的算子自带 `MemoryContext`（排序、哈希连接、哈希分组等），用量决定是否落盘 | `ob_sort_op_impl.h:825`；`ob_hash_join_op.h:278` |
| 行 / 批次 | 求值临时 arena 在未被占用时 reset，超过 64 KB 时只保留一页；结果 arena 从不 reset；串结果放在 frame 预留区里 | `ob_expr.h:260-271`、:279、:287、:431-441；`ob_exec_context.h:884-885` |
| memtable | FIFO arena 的句柄，`free` 是空操作，按 retire clock 整体回收 | `ob_memstore_allocator.h:75-117` |
| 单个 schema 对象 | 「扁平化」深拷贝：对象、它自己的 `ObDataBuffer` 分配器和全部字符串放在同一块缓冲区里 | `ob_schema_utils.h:285-300` |
| RPC 包 | `ObString` 反序列化默认零拷贝，直接指向包缓冲区 | `ob_string.cpp:48-49` |

- **典型代码**：`ObRawExprFactory::create_raw_expr_inner`（`ob_raw_expr.h:5034-5070`）依次做了这些事：
  - 先 `allocator_.alloc`，失败时设 `OB_ALLOCATE_MEMORY_FAILED`（:5041）；
  - placement new（:5053）；初始化失败时手动析构（:5060、:5064）；
  - 把对象登记到 `expr_store_`，供析构时统一处理；
  - 析构只在非工作线程执行（:5019-5025）。原因是表达式里的 `auto_free` 数组在工作线程上随 context 一起整体释放。

### 2.5 分配失败怎么处理

- **几乎全部检查**：3,202 个分配调用里，3,023 个（94%）在 5 行内做了空指针检查。抽查了 3 个「未检查」的，都是误报（检查跨了多行，或者根本不是分配），所以真实比例更高。
- **统一往上传**：`OB_ALLOCATE_MEMORY_FAILED` 有 3,983 处赋值。只有 78 处按它分支，其中 storage 48 处，多是退避重试或日志限频。SQL 层有一条要守住的语义：容错转换（warn-on-fail）**不能吞掉内存错误**。例如 `ob_expr_day_of_func.cpp:426`、`ob_expr_date_format.cpp:98`；`ob_pl_resolver.h:533-540` 的 `is_unrecoverable_error`；`ob_table_columns.cpp:1342-1350` 规定内存错误不改写。
- **也有故意忽略失败的**：`ob_physical_plan.cpp:978-986` 分配失败时只是退回到「不淘汰」逻辑。
- **能不能从公开面观察到**：能，表现为本语句报 4013 或 11049。但哪条语句、什么时候失败，取决于时机和计账，本身就不确定，裁判也没有覆盖。
  - 默认配置（jemalloc 后端、没有硬上限、Linux overcommit）下，实际能走到 4013 的估计只有三种情况：模块预算（比如向量）、超大尺寸、errsim 注入。
  - obmalloc 后端下，经 libc `malloc` 的分配会无限重试，而不是失败。
- **用户数据决定大小的分配先过显式上限**：比如 `repeat()` 先查 `max_result_size`，超了报 `OB_ERR_FUNC_RESULT_TOO_LARGE`，然后才分配（`ob_expr_repeat.cpp:128-139`）。但在上限以内，几百 MB 的分配仍然可能失败。

### 2.6 Rust 现状与工具链

- **工具链**：`rust/rust-toolchain.toml` 钉在稳定版 **1.98.1**。`rust/Cargo.toml` 的 release 和 cmake-debug 两个 profile 都设了 `panic = "abort"`，而 Rust 的分配失败默认就是 abort（`handle_alloc_error`）。
- **sql-nio 的先例**：全局分配器加 14 处 `try_reserve*`（如 `pump.rs:490`、`compress.rs:25`、`response_api.rs:21`）。失败时映射成 `io::ErrorKind::OutOfMemory`（`pump.rs:491`），或者把连接标成错误（`response_api.rs:883-889`），而不是 abort。另有 1 处 `alloc_zeroed` 加 `handle_alloc_error`（`session_storage.rs:27-28`）。
- **allocator_api 的现状**（外部事实，2026-10 查证）：
  - rust-lang/rust#156882 "stabilise `Allocator`" 进了 **1.100.0**，目前是 beta，计划 2026-11-12 发布。稳定的部分：`Allocator` trait、`Box::new_in`、`Vec::new_in`/`with_capacity_in`（1.100.0-beta.4 文档标注 "since 1.100.0"）。
  - 仍不稳定（`allocator_ext`）：`Box::try_new`/`try_new_in`、`Vec::try_with_capacity(_in)`、其他集合的分配器参数。
  - `try_reserve`/`try_reserve_exact` 从 1.57 起就是稳定的。
- **可用的 crate**：
  - bumpalo 3.20：`try_alloc*`、`allocation_limit`、`allocated_bytes`，`Vec` 有 `try_reserve`；**不运行 Drop**；底层内存只能来自全局分配器。
  - bump-scope 2.4：底层分配器可以泛型替换；有 scope 和检查点；不开 `panic-on-alloc` 时只提供 `try_` 方法；`BumpBox` 会运行 Drop。
  - allocator-api2：0.2、0.3、0.4 三个版本互不兼容，并存。
  - `rust/Cargo.lock` 里已经有 hashbrown 0.17.1（经 indexmap 间接引入）和 slab 0.4.12。

## 3. 选项

### (a) bump/region arena 加生命周期（查询作用域），其余走全局分配器

- **做法**：
  - 每个请求（对应 `CREATE_WITH_TEMP_CONTEXT`）建一个 `Arena`。查询作用域的类型带 `'q`：`ObString` 成员变成 `ObStr<'q>`（`&'q [u8]`），`ObFixedArray<T, ObIAllocator>` 变成 `&'q [T]` 或 `ArenaVec<'q, T>`。
  - 批次临时 arena 归算子所有，在批次边界 `reset(&mut self)`。
  - 长生命周期对象（计划缓存、schema、会话）改成拥有型。
- **优点**：
  - 和 C++ 语义一致（`free` 本来就是空操作，整体释放），性能和 PageArena 相当。
  - 借用由编译期检查，arena reset 之后的悬垂引用编译不过；翻译出来的代码里没有 unsafe。
- **缺点**：
  - 所有含引用的类型都要带 `'q`。
  - 288 个「拥有 arena 又借用它」的类写不出来，要拆成「arena 在外层、借用者在内层」，或者改用偏移量/id。
  - `reset()` 需要 `&mut`，与存活的借用冲突，所以跨批次的数据必须放进拥有型缓冲。
  - `Bump` 是 `!Sync`。
- **unsafe**：翻译代码里为 0。arena crate 内部有 unsafe（第三方）；如果解析器保留 C 版，FFI 的 `parse_malloc` 桥接需要 1 处。
- **对翻译和对齐的影响**：规则机械，就是「指针变 `&'q`，`ObString` 变 `&'q [u8]`」。结果不受影响。11049、落盘和查询范围退化的阈值会随结构体大小漂移：结果不变，但接近阈值的语句 `EXPLAIN` 可能不同。

### (b) allocator_api / allocator-api2，容器按分配器参数化

- **做法**：容器写成 `Vec<T, &'q Bump>`、`hashbrown::HashMap<K, V, S, &'q Bump>`、`Box<T, &'q Bump>`；如果要模拟 `ObIAllocator &`，就用 `&'q dyn Allocator`（1.100 里 `Allocator` 是 dyn-compatible 的）。
- **优点**：最贴近 C++「容器带分配器」的写法；`try_reserve` 能直接映射失败。
- **缺点**：
  - 1.98.1 不可用。1.100 也只有 `Vec`/`Box`，`String`、`HashMap`、`Rc`、`Arc` 仍要靠 allocator-api2，而它有三个不兼容的版本。
  - 仍然需要 `'q`，所以 (b) 的复杂度是在 (a) 之上叠加的。
  - 泛型 `A` 会随签名传播（7,147 个分配器参数、8,398 个可写的 `ObIArray&`）；如果做成泛型就会单态化膨胀，影响编译时间。
  - 用 `dyn` 时每次分配多一次间接调用（与 C++ 的虚调用相当）。容器也会变大：`Vec<T>` 24 字节，`Vec<T, &Bump>` 32 字节，`Vec<T, &dyn Allocator>` 40 字节。
  - serde 等生态不接受自定义分配器的容器。
- **unsafe**：翻译代码里为 0；每个计账分配器需要一个 `unsafe impl Allocator`。
- **对翻译和对齐的影响**：签名改写最多；对齐风险与 (a) 相同。

### (c) id/句柄式存储

- **做法**：同类对象放在 `Vec<T>` 或 typed arena 里，引用写成 `ExprId(u32)`/`OpId(u32)`；需要防悬垂时用 slotmap 的代际 id。
- **优点**：能表达环、反向边和原地改写（`ob_raw_expr.h:2000` 的 `ObRawExpr *&`）；不需要生命周期；`reset` 是安全的；局部性好；方便序列化（计划可以直接缓存）。
- **缺点**：访问语法全部要改（`expr->get_param_expr(i)` 变成 `ctx.expr(id).param(i)`），翻译的机械程度中等偏低；用错容器的 id 是逻辑 bug；不适合存字符串和字节缓冲。
- **unsafe**：0。边界检查的开销估计 1–2%（估）。
- **对翻译和对齐的影响**：适合 SQL IR 图（raw expr、stmt、逻辑和物理算子），是否采用由 IR 主题决定。本主题只要求：id 所指的存储放在请求或计划的作用域里。

### (d) 全局分配器，加线程局部的「当前内存属性」做计账

- **做法**：`#[global_allocator]` 包一层自带的 jemalloc（`deps/external` 已经通过 Cargo 构建 `seekdb-jemalloc-sys`）。线程局部 `Cell<MemAttr>` 由 `MemAttrGuard` 设置，对应 `ObMallocHookAttrGuard` 和 `CURRENT_CONTEXT`。按 ctx 计数；free 时如何归到正确的 ctx 有三种办法：
  - 每个 ctx 一个 jemalloc arena（`mallocx(MALLOCX_ARENA)` 加 `sdallocx`）。这样做与 tcache 有交互，需要性能验证。
  - 每块分配加 16 字节头部。
  - 只做抽样。
- **优点**：
  - 拥有型数据不再需要分配器参数，对编译时间没有影响。
  - 保留 `V$OB_MEMORY` 一类诊断。
  - C++ 已有同样的模式（105 处 guard、114 处 `CURRENT_CONTEXT`）。
- **缺点**：
  - 归属的是「谁分配的」，不是「谁拥有」：在别的线程上扩容的 Vec 会记到那个线程的属性上。
  - 做不到精确的单查询上限。
  - 不可失败的 API 碰到 OOM 时直接 abort。
- **unsafe**：一个 `unsafe impl GlobalAlloc`，约 100–200 行（估）。
- **对翻译和对齐的影响**：
  - 机械：拥有型数据的 `ObIAllocator` 参数和 `ObMemAttr` 实参直接删掉；`OB_NEW(T, attr)` 改成 `try_box(T)`，属性换成 scope guard。
  - 诊断视图里的数值会变，裁判不比较这些值。

| 维度 | (a) arena + `'q` | (b) 分配器参数化 | (c) id/句柄 | (d) 全局分配器 + 线程局部属性 |
|---|---|---|---|---|
| 安全 | 编译期检查借用；arena 内类型禁止需要 Drop | 同 (a) | 越界会 panic；悬垂 id 靠代际 id 检查 | 全局堆本身安全 |
| 自写 unsafe | 0（解析器 FFI 桥接 1 处） | 每个分配器 1 个 | 0 | 1 个模块 |
| 性能 | 与 PageArena 相当 | 同 (a)；用 dyn 时多一次间接调用 | 多出边界检查 | 每个对象要单独 free；TLS 读约 1 ns（估） |
| 机械程度 | 高；288 个自引用类要改写 | 中 | 中偏低 | 高 |
| 编译时间 | 基本没有影响 | 泛型 `A` 会单态化 | 没有影响 | 没有影响 |
| 主要耦合 | IR 图、并发（`!Sync`）、解析器 FFI | 工具链、第三方 crate 生态 | IR 图 | 第三方 C 库的 malloc、线程池 |

## 4. 推荐

**组合**：查询和批次作用域用 (a)，其中的图用 (c)，长生命周期用拥有型；全局分配器用 (d)，只做诊断；限额交给显式的 `MemBudget`。(b) 不作为通用规则；以后只允许在 `ob_mem` 内部或个别热点（例如把算子哈希表放进 bump）使用，届时再定。

**理由**：

1. **C++ 本来就是这样分工的**。query 和 sql 的大部分内存是请求 arena，整体释放；`ObSEArray`/`ObArray` 绝大多数用的是全局堆（5,060 + 2,399 处默认分配器）；真正绑定 arena 的容器是 424 个 `ObFixedArray`、52 个 `ObList`，加上 563 个 `auto_free` 数组。
2. **默认后端下 label/ctx 计账已经关掉了，硬上限也从未设置过**，所以不需要一个能拦截每次分配的全局计账分配器。现在仍然生效的限额都是模块级的（向量、memstore、kvcache、工作区、单查询上限），而且都在少数几处显式检查。
3. **(b) 的收益**（容器带分配器）在 Rust 里可以用「arena 内只放冻结后的切片，增长期间用 `ArenaVec`」得到，不必让 8,398 个输出数组参数都带上泛型。

**草图 1：请求 arena 和查询作用域的类型**（crate `ob_mem`，位于最底层的 oblib crate）

```rust
pub struct Arena { bump: bumpalo::Bump, attr: MemAttr }       // 取代 ObArenaAllocator / get_arena_allocator()
pub type ObStr<'q> = &'q [u8];                                 // ObString：MySQL 字符串按字节处理，不用 &str
pub struct ArenaVec<'q, T>(ManuallyDrop<bumpalo::collections::Vec<'q, T>>); // 不实现 Drop，和 C++ arena 一样
impl Arena {
    pub fn try_alloc<T>(&self, v: T) -> ObResult<&mut T> {
        const { assert!(!std::mem::needs_drop::<T>()) };      // arena 不运行 Drop：编译期拒绝需要 Drop 的类型
        self.bump.try_alloc(v).map_err(|_| ObError::AllocateMemoryFailed)
    }
    pub fn try_slice<T: Copy>(&self, s: &[T]) -> ObResult<&[T]> {  // 冻结 ObFixedArray / ObSEArray
        self.bump.try_alloc_slice_copy(s).map(|v| &*v).map_err(|_| ObError::AllocateMemoryFailed)
    }
    pub fn try_str(&self, s: &[u8]) -> ObResult<ObStr<'_>> { self.try_slice(s) } // ob_write_string
    pub fn hold(&self) -> usize { self.bump.allocated_bytes() } // total()
    pub fn reset(&mut self) { self.bump.reset() }               // reuse()：编译器保证此时没有存活的借用
}
```

**草图 2：MemoryContext 和单查询上限**

```rust
pub struct MemCtx { pub arena: Arena, budget: Arc<MemBudget> }   // 取代 lib::MemoryContext
pub struct MemBudget { parent: Option<Arc<MemBudget>>, hold: AtomicI64 } // 取代 MemoryUsageTracker
// MemBudget::charge(&self, delta)：沿 parent 链累加，不会失败；MemBudget::tree_hold(&self) -> i64
impl MemCtx {
    /// 原 CHECK_MEM_STATUS()：只在原来的 9 个检查点调用，不在每次分配时检查
    pub fn check_status(&self) -> ObResult<()> {
        let (hold, limit) = (self.budget.tree_hold(), query_mem_limit()); // budget 的 % 由 config 给出
        if limit > 0 && hold >= limit { Err(ObError::ExceedQueryMemLimit { limit, hold }) } else { Ok(()) }
    }
}
```

`tree_hold` = 各 arena 的 `hold()` + 算子显式报告的量。算子在 C++ 调用 `sql_mem_processor_` 的地方（35 个文件）同时 `charge`。

**草图 3：全局分配器和失败映射**

```rust
#[derive(Clone, Copy)] pub struct MemAttr { pub label: &'static str, pub ctx: CtxId } // 没有 tenant
thread_local! { static CUR_ATTR: Cell<MemAttr> = const { Cell::new(MemAttr::GLIBC) }; } // const 初始化，不分配
pub struct MemAttrGuard(MemAttr);                     // RAII，对应 ObMallocHookAttrGuard
pub struct ObAlloc;                                   // #[global_allocator]，按 ctx 计数，不做限额
impl From<TryReserveError> for ObError { fn from(_: TryReserveError) -> Self { ObError::AllocateMemoryFailed } }
pub trait TryPush<T> { fn try_push(&mut self, v: T) -> ObResult<()>; } // try_reserve(1)? + push
pub fn try_box<T>(v: T) -> ObResult<Box<T>> {        // 稳定版（含 1.100）没有 Box::try_new
    let layout = Layout::new::<T>();
    if layout.size() == 0 { return Ok(Box::new(v)); }
    // SAFETY: layout 非零；指针非空时写入 v，再交给 Box，Box 用同一 layout 释放
    let p = unsafe { std::alloc::alloc(layout) }.cast::<T>();
    if p.is_null() { return Err(ObError::AllocateMemoryFailed); }
    unsafe { p.write(v); Ok(Box::from_raw(p)) }
}
```

**C++ → Rust 映射**（交给设计文档细化）：

| C++ | Rust |
|---|---|
| `if (OB_ISNULL(buf = alloc.alloc(sz))) { ret = OB_ALLOCATE_MEMORY_FAILED; } else { p = new (buf) T(..); }` | `let p = arena.try_alloc(T::new(..))?;`（查询作用域）或 `try_box(T::new(..))?`（拥有型） |
| `OB_NEW(T, label, ..)` / `OB_NEWx(T, pool, ..)` | `try_box(..)?`；`OB_NEWx` 按 pool 的生命周期选 `arena.try_alloc` 或 `try_box` |
| `OB_DELETE` / `x->~T(); alloc.free(x);` | Drop；arena 里的对象什么也不用做 |
| `ob_write_string(alloc, src, dst)` / `deep_copy(alloc, ..)` | `arena.try_str(src)?`；拥有型用 `Clone`，必要时先 `try_reserve` |
| `ObString` 成员 | 查询作用域用 `ObStr<'q>`，拥有型用 `Box<[u8]>` |
| `ObSEArray<T, N>` / `ObArray<T>`（默认分配器） | `SmallVec<[T; N]>` / `Vec<T>`，用 `try_push` |
| `ObFixedArray<T, ObIAllocator>`、`auto_free` 数组 | 增长期用 `ArenaVec<'q, T>`，定型后转成 `&'q [T]` |
| 可写的 `ObIArray<T> &out` | `&mut Vec<T>` |
| `allocator.reset()` / `reuse()` | `arena.reset()`，此时不能有存活的借用 |
| `CHECK_MEM_STATUS()` | `mem_ctx.check_status()?`，保留在原调用点 |
| `CM_IS_WARN_ON_FAIL && ret != OB_ALLOCATE_MEMORY_FAILED` | `!e.is_unrecoverable()` |

**前提**：

1. IR 主题对查询图选 arena 加 typed id（或 `&'q` 加 `Cell`），不选 `Rc<RefCell>`。
2. 裁判继续不比较内存视图的数值，也不覆盖 OOM 的结果。
3. 接受「小的固定大小分配遇到 OOM 时进程 abort」这一行为差异：C++ 是本语句报 4013；Linux overcommit 下两者通常都会先被 OOM killer 处理掉。
4. 288 个自引用类和 schema 的扁平拷贝，设计文档要按类给出改写方式，最好附一张脚本生成的类到生命周期的分类表。

## 5. 需要人决定的问题

1. **查询作用域数据怎么存？**
   - A. bump arena 加 `'q`，图用 id **（推荐）**
   - B. 全部改成全局堆的拥有型，不用 arena：最简单；代价是硬解析路径上每个对象多一次 malloc/free，命中计划缓存的语句影响小（估，需要实测）
   - C. allocator-api2 参数化容器
2. **arena 用哪个实现？**（翻译代码只看 `ob_mem::Arena` 这层外观）
   - A. bumpalo 3.20：最成熟；靠 `allocated_bytes` 加线程局部属性计账 **（推荐）**
   - B. bump-scope 2.4：底层分配器可替换、可以只开 `try_`；但较新
   - C. 自研，照着 PageArena 写，约 500 行（估），unsafe 要自己审
3. **分配失败采用什么策略？**
   - A. 全部可失败：用 clippy `disallowed_methods` 禁掉 `push`/`Box::new` 等；翻译成本最高
   - B. 混合：arena、用户决定大小或超过阈值（比如 64 KB）的缓冲必须用 `try_*`，其余不可失败 **（推荐）**
   - C. 全部不可失败，OOM 即 abort
4. **label/ctx 计账（`ObMemAttr`）保留到什么程度？**
   - A. 全部丢掉，`V$OB_MEMORY` 返回空
   - B. 线程局部 `MemAttr` 加 ctx 级计数，只做诊断，label 实参保留 **（推荐）**
   - C. 像 obmalloc 那样完整做到 label 级，每块分配带头部
5. **单查询内存上限（11049）怎么处理？**
   - A. 保留：arena 的 hold 加算子显式报告的量，在原来的 9 个检查点检查 **（推荐）**
   - B. 只算 arena
   - C. 参数照收，但不生效（像 `memory_limit` 那样）
6. **工具链怎么处理？**
   - A. 设计不依赖 allocator_api，工具链按团队自己的节奏升级 **（推荐）**
   - B. 设计要求 ≥1.100，arena 容器用 std 的 `Vec<T, A>`
   - C. 用 nightly
7. **长生命周期的自引用对象怎么改？**（288 个类，加上 schema 的扁平拷贝和会话字符串池）
   - A. 改成拥有型字段（`Box`/`Vec`/`Arc`），删掉对象内部的分配器 **（推荐）**
   - B. 用 ouroboros/self_cell 这类自引用 crate
   - C. 每个对象一个 arena，内部引用改成偏移量
8. **内存视图怎么处理？**
   - A. 保留表结构，内容由 Rust 侧的统计填充；裁判只比较表结构 **（推荐）**
   - B. 删掉这些视图：会破坏「`information_schema` 列出全部内部表」这一契约（`00b-judge-report.md:173`）

## 6. 对其他主题的依赖或影响

- **SQL IR 图**：arena 只负责存储；边用 id 还是 `&'q`，由 IR 主题决定。如果 IR 选 `Rc<RefCell>`，查询 arena 就只剩字符串和临时数据。解析阶段原地改写的 `ObRawExpr *&`（`ob_raw_expr.h:2000`）在 (a) 下只能用 id 或 `Cell` 表达。不管 IR 怎么选，arena 里的节点都不能需要 Drop。
- **并发**：
  - `Bump` 是 `!Sync`。少数加锁 arena（`ObSafeArenaAllocator` 约 10 个文件，`ALLOC_THREAD_SAFE` 11 处）改成 `Mutex<Arena>` 或每线程一个 arena。
  - PX worker 共享计划：用 `Arc<拥有型计划>`，每个 worker 有自己的请求 arena。
  - 线程局部属性要随任务传到线程池，就像 C++ 每个请求重建 context 那样。
  - QClock、retire station 这类无锁回收不归本主题。
- **存储**：memstore 的 FIFO arena 加 retire clock、kvcache 的内存块、元数据对象池、tmp file 缓冲池、compaction 的 context，都是带预算的模块分配器。本主题提供 `MemBudget` 和共享 arena 原语，具体表示由存储主题设计。
- **分层**：`ob_mem` 放在最底层 crate。工作区 tracker 的注册（`ob_sql_memory_manager.cpp:431`）和各模块预算改成启动时显式注册，这和 12 条逆向链接边的拆法一致。
- **解析器**：如果保留 C 版，`parse_malloc`/`parse_free`（`parse_malloc.cpp:31`、:39 把 `malloc_pool` 当作 `ObIAllocator`）改成 `extern "C"` 回调，从请求 arena 分配，失败返回 NULL，由解析器 longjmp 出 -4013。
- **第三方库**：vsag 通过自定义分配器接口拿内存（`ObVsagMemContext : vsag::Allocator`，`ob_vector_allocator.h:71`），改成 C 回调接到 Rust 的 `MemBudget`。vsag、ICU、s2 的 libc malloc 要不要继续由 jemalloc 接管（现在由 `malloc_hook.cpp` 负责），需要和第三方库主题一起定。
- **约定**：`ObError` 需要单独的 `AllocateMemoryFailed`、`ExceedQueryMemLimit` 变体，并提供 `is_unrecoverable()`；可失败的方法统一用 `try_` 前缀；保留 `panic = "abort"`；每处 unsafe 写 `SAFETY:` 注释；`SMART_VAR`（506 处）改成按大小 `Box`。

## 附录：复现

| 脚本（`/tmp/memsurvey-9f3a2c/scripts/`） | 做什么、核心规则 |
|---|---|
| `migration/feasibility/scripts/arch/unsafe_share.py <mirror>`（仓库内） | placement new `\bnew\s*\((?!\s*std::nothrow)`、手动析构 `(->\|\.)~\w+\s*\(\s*\)`、裸指针成员、`T *&` |
| `alloc_classes.py <mirror> ObIAllocator` | 沿用 `classes.py` 的类正则，求 `ObIAllocator` 的传递子类；类体里 `free(void*)` 只含 `UNUSED(..)` 的记为空操作 |
| `mem_census.py <mirror>` | 例：`\bObIAllocator\s*&\s*\w+\s*[,)]`、`(?m)^\s+(const\s+)?(common::)?ObString\s+\w+_\s*;`、`\bret\s*=\s*(common::)?OB_ALLOCATE_MEMORY_FAILED\b`、`ModulePageAllocator\s*,\s*true\s*>` |
| `proximity.py <mirror>` | 分配调用后 5 行内有没有空指针检查；placement new 前 8 行内有没有 alloc；手动析构后 3 行内有没有 free |
| `containers.py <mirror>` | 解析容器的模板实参，按分配器实参分类 |
| `selfref.py <mirror>` | 类体顶层有分配器成员，且同时有裸指针、`ObString` 或 `ObFixedArray`/`ObList` 成员 |
| `factory_sigs.py <mirror>` | 跨行参数表 `\b(\w+)\s*\(([^(){};]*)\)\s*…[{;:]` 中同时含分配器参数和 `T *&` |
| `unit_classes.py migration/manifest.tsv` | 按目录前缀粗分单元的生命周期 |
