# 01 `mem`：`oblib::mem` 的 API

> - **依据**：`migration/RULEBOOK.md` v0.2 的 0.7、§2.4、§2.5、§3 第 1 项、A.2"内存"、A.4 的 `oom-abort`/`query-mem-limit`/`malloc-backend`/`mem-view`；`migration/infra/README.md`。基线 `7d907abfa`。
> - **事实来源**：`migration/design/survey/memory.md`；审查 S-02、S-07、S-11、S-17、S-23，并发 F6、M10，行为 F15、F18，复核 C11、C25；其余 file:line 都是本文作者在基线源码上逐条核对的。
> - **读者**：1d 的实现者（本文是规格），第 2、3 步的翻译者和审查者（本文是只读上下文）。
> - **一句话**：查询作用域用 `Arena`（bumpalo 外观，统计口径照 PageArena）；请求和语句级的 `lib::MemoryContext` 换成显式传参的 `MemCtx`；限额换成原子计数的 `MemBudget`，只在 C++ 原来的检查点检查；全局分配器是 jemalloc 直通的 `ObMalloc`。第 8 节有 9 个开放问题，其中 8 个是 RULEBOOK 的缺口，已排进 `migration/infra/amendments.tsv`（doc 列为 `01-mem`）。

## 1. 范围

**处置**（RULEBOOK §4）：
- `replace:mem`：由本模块取代。1d 在单元的目标路径写桩文件，只含 `pub use crate::mem::…`（§4 桩文件规则；`src/oblib/lib/…` 的目标路径按 §4 的 `_m` 规则是 `rust/port/oblib/src/lib_m/…`）。不进翻译队列。
- `delete`：不翻译，不留桩。仍被别处引用的名字，去处写在同一行。
- "不属本模块"：给出归属的模块组。

**计数口径**：外部使用数 = `grep -rlw <主要类名> src | grep -v <单元自身>` 的文件数；宏和调用是 `grep -rn '<名字>(' src --include='*.h' --include='*.cpp' | grep -v '#define'` 的行数。都是近似值。

### 1.1 `src/oblib/lib/allocator/`

| 单元（行数） | 主要类、宏 | 处置 | 去处与理由 |
|---|---|---|---|
| `page_arena.h`（1131） | `PageArena`/`CharArena`/`ByteArena`/`ModuleArena`、`ObArenaAllocator`、`ObSafeArenaAllocator`、`DefaultPageAllocator`、`ModulePageAllocator` | replace:mem | `Arena`（§2.2）。`ObSafeArenaAllocator` → `Latch<Arena>`（0.7）。`DefaultPageAllocator`/`ModulePageAllocator` 只决定"页从哪来、带什么标签"（:60-185），Rust 的页一律来自全局分配器，所以没有对应物；`ModulePageAllocator` 经 resolver 记到 WORK_AREA 的那部分（:116-181）改由 `Arena::set_page_tracker` 和 `ContextParam::set_memory_tracker` 承担（§3 S6、S14） |
| `ob_malloc.h/.cpp`（709+295） | `ob_malloc`/`ob_free`/`ob_realloc`、`ObMallocBackend` 一族、`MemoryUsageTracker`、`TrackedAllocator`、`ObMalloc`、`MemoryContextMalloc`、`ObMemBuf`、`ObMemBufAllocatorWrapper`、`ObRawBufAllocatorWrapper`、`ob_delete`、`OB_NEW*`/`OB_DELETE*`、`je_malloc_conf`、`configure_darwin_malloc_zone`、`restore_malloc_backend_after_fork`、`ob_malloc_align`、`ob_zalloc` | replace:mem | §2.3、§2.6、§2.7、§2.12。`ObMalloc` 成为全局分配器类型；`MemoryUsageTracker` → `MemBudget`；`TrackedAllocator` 的单次分配 → `TrackedBuf`；`MemoryContextMalloc` 保留名字。`ObMemBuf`、`ObMemBufAllocatorWrapper` 外部 0 个文件、`ob_zalloc`/`ob_zfree` 没有调用者（:287-295，签名也不符合 zlib 的 `alloc_func`），都不提供。`ObRawBufAllocatorWrapper`（3 个文件）的调用方改为直接用切片 |
| `ob_jemalloc.h`（132） | `je_*` 声明、`jemalloc_malloc` 等包装 | replace:mem | `oblib::mem::jemalloc`（§2.7）。`ENABLE_SANITY` 分支见 Q6 |
| `ob_allocator.h`（96） | `ObWrapperAllocator`、`ObWrapperAllocatorWithAttr` | replace:mem | 无对应物：它只是转发给借来的分配器（:26-92）。按 §2.4，查询作用域里成为 `&'q Arena`，拥有型里删掉（外部 49 个文件） |
| `ob_allocator_v2.h/.cpp`（279+105） | `ObAllocator`、`ObParallelAllocator` | delete | 只在 obmalloc 后端下给 MemoryContext 用（`context.h:449-478`）。jemalloc 下 MemoryContext 走 `init_malloc_alloc`（:480-488）；obmalloc 不移植（A.4 `malloc-backend`） |
| `ob_page_manager.h/.cpp`（165+28） | `ObPageManager` | delete | obmalloc 的线程页缓存。`ob_th_worker.cpp:228-235`、`thread.cpp:368-370` 的 `set_ctx` 调用随之删掉 |
| `ob_tc_malloc.h/.cpp`（43+85） | `get_virtual_memory_used`、`get_global_ctx_info`、`init_global_memory_pool`、`global_default_allocator` | replace:mem | `get_virtual_memory_used` 逐行移植（:60-82）；`get_global_ctx_info` → `ObCtxInfo`（§2.11）；构造函数 `init_global_memory_pool` 和 `global_default_allocator` 删掉（只服务 obmalloc） |
| `ob_block_alloc_mgr.h/.cpp`（66+26） | `ObBlockAllocMgr`、`default_blk_alloc` | replace:mem | `MemBudget` 的 `try_alloc`/`alloc_block`/`can_alloc_block`（§2.6），方法名照抄（:30-61）。外部 12 个文件 |
| `ob_slice_alloc.h/.cpp`（541+90） | `ObSliceAlloc`、`ObBlockSlicer`、`ObStockCtrl` | replace:mem | 定长对象池。按 0.7 长生命周期对象走全局分配器：对象用 `try_box`，池的上限由 `MemBudget` 承担（§3 S13）。外部 7 个文件 |
| `ob_vslice_alloc.h`（294） | `ObVSliceAlloc` | replace:mem | 同上（外部 11 个文件，含 `ObCompressorPool`、`ObRbMemMgr`） |
| `ob_small_allocator.h`（71） | `ObSmallAllocator` | replace:mem | 同上（外部 6 个文件）；`limit_num * obj_size` 的上限（:38-40）→ `MemBudget::set_limit` |
| `ob_fifo_allocator.h/.cpp`（176+512） | `ObFIFOAllocator` | replace:mem | 同上（外部 36 个文件）。它的页也经 `TrackedAllocator` 按 ctx 计账（`ob_fifo_allocator.cpp:75-76`），属性是 WORK_AREA 时改用 `TrackedBuf` 记到 WORK_AREA 预算 |
| `ob_concurrent_fifo_allocator.h/.cpp`（62+122）、`ob_lf_fifo_allocator.h`（75） | `ObConcurrentFIFOAllocator`、`ObLfFIFOAllocator` | replace:mem | 同上（外部 16、6 个文件）；`total_limit` → `MemBudget::set_limit` |
| `ob_simple_fifo_alloc.h`（238） | `ObSimpleFifoAlloc` | delete | 外部 0 个文件 |
| `ob_fixed_size_block_allocator.h`（464） | `ObFixedSizeBlockAllocator`、`ObFixedSizeBlockMemoryContext` | delete | 只被 `storage/blocksstable/ob_data_buffer.h:22` 包含，没有使用 |
| `ob_delay_free_allocator.h/.cpp`（184+247） | `ObDelayFreeAllocator` | delete | RULEBOOK 0.8 列出的死代码；外部 0 个文件 |
| `ob_hazard_ref.h/.cpp`（317+80） | `HazardRef` 一族 | delete | 0.8：HazardRef 不移植。仅有的使用者 `ob_hash.h:709,782`、`ob_darray.h:748,803` 是 FixedHash2 的内部，由 05 取代 |
| `ob_heavy_ref.h`（113） | `ObHeavyRef` | delete | 只被 `ob_ext_ring_buffer_impl.h:33` 包含，没有使用 |
| `ob_external_ref.h`（91） | `ObExternalRef` | 不属本模块 | `ObLinearHashMap`（`ob_linear_hash_map.h:148-696`）和 `ob_ext_ring_buffer_impl.h:150-320` 的内部机制，随 05 的封装处理 |
| `ob_qsync.h`（200） | `ObQSync`、`ObDynamicQSync`、`CriticalGuard`、`WaitQuiescent` | 不属本模块 | 02 的 `Quiescence`、`QSyncLatch`（0.8、§2.7） |
| `ob_retire_station.h`（279） | `QClock`、`RetireStation`、`HazardList` | 不属本模块 | 0.8：keybtree 在 `storage_core`、KV cache 在 `share::cache` 移植（08） |
| `ob_safe_arena.h`（94） | `ObSafeArena` | replace:mem | `Latch<Arena>`（外部 2 个文件：`ob_dtl.h:149`、`ob_px_sub_coord.h:146`） |
| `ob_memory_sanity.h`（91）、`ob_jemalloc_sanity.cpp`（596）、`ob_sanity_libc_wrap.cpp`（223） | Memory Sanity 模式、jemalloc extent hooks | delete | 只在 `ENABLE_SANITY` 构建里生效（`ob_memory_sanity.h:31-70`，基线的 363 条编译命令都没有这个宏，复核 C11）；`src/` 里没有外部使用者。见 Q6 |

### 1.2 `src/oblib/lib/alloc/`

| 单元（行数） | 主要类、宏 | 处置 | 去处与理由 |
|---|---|---|---|
| `ob_iallocator.h`（127） | `ObIAllocator`、`ObAllocAlign`、`global_default_allocator` | replace:mem | 不提供 trait。§2.4：查询作用域的 `ObIAllocator &`/`*` 参数和借用成员 → `&'q Arena`，拥有型删掉。`alloc_align`/`free_align`（:27-65、:91-104）→ `AlignedBuf` |
| `alloc_struct.h/.cpp`（669+158） | `ObLabel`、`ObMemAttr`、`ObMallocHookAttrGuard`、`ObLightBacktraceGuard`、`ObUnmanagedMemoryStat`、`Label`、`AChunk`/`ABlock`/`AObject` | replace:mem | `ObMemAttr`、`ObLabel`、`ObMallocHookAttrGuard` 删掉（0.7）；`ObLightBacktraceGuard` 删掉（只服务 obmalloc 的泄漏回溯）；`get_unmanaged_memory_size()` 返回 0（§3 S19）；chunk 结构随 obmalloc 删除 |
| `alloc_func.h/.cpp`（70+152） | `get/set_memory_budget`、`get_memory_by_percentage`、`get_allocator_memory_hold`、`set_ctx_limit`、`set_req_chunkmgr_parallel`、`errsim_alloc`、`ob_set/get_reserved_memory` | replace:mem | §2.11。`get_memory_by_percentage` 逐行移植（:37-53），其余见 S22 |
| `alloc_interface.h`（133）、`alloc_failed_reason.h/.cpp`（79+179）、`abit_set.h/.cpp`（242+132）、`block_set.h/.cpp`（124+472）、`object_set.h/.cpp`（178+703）、`object_mgr.h/.cpp`（136+346）、`ob_ctx_define.h/.cpp`（65+29）、`ob_context_allocator.h/.cpp`（518+438） | obmalloc 的 chunk、block、object 管理和 ctx 属性 | delete | obmalloc 后端内部；`lib/alloc` 之外的使用者只有 `lib/resource/`、`ob_page_manager.h`、`ob_allocator_v2.*`，都一并删除 |
| `ob_ctx_allocator.h/.cpp`（489+436） | `ObCtxAllocator`、`ObCtxAllocatorState` | delete | 引用它的 12 个文件都在 `lib/alloc`、`lib/allocator` 里（obmalloc 内部）。外部经 `ObMallocAllocator::get_ctx_allocator(i)` 拿到它的 8 处只取统计或设软上限（`ob_server_duty_task.cpp:77`、两个内存视图、`ob_ra_datum_store.cpp:1244`、`ob_ra_row_store.cpp:934`、`ob_storage_meta_mem_mgr.cpp:505`、`ob_server_runtime_controller.cpp:443`、`ob_sql_mem_pool.cpp:46`），改用 §2.11 的自由函数：`->set_limit(l)` → `set_ctx_limit(i, l)`，`->get_hold()`/`get_used()`/`get_limit()` → `get_ctx_hold(i)`/`get_ctx_used(i)`/`get_ctx_limit(i)`，`->iter_label` → `iter_label` |
| `ob_malloc_allocator.h/.cpp`（136+345） | `ObMallocAllocator`、`ObCtxAllocatorGuard` | replace:mem | 统计方法 → §2.11 的同名自由函数（`get_total_hold` → `get_allocator_memory_hold`，`get_total_limit`、`set_ctx_idle`、`print_memory_usage` 同名）；把 `ObMallocAllocator::get_instance()` 当作页分配器传给各种池的地方（外部 43 个文件，如 `ob_tmp_file_write_buffer_pool.cpp:65`）随池一起删掉参数 |
| `ob_malloc_callback.h/.cpp`（63+67） | `ObMallocCallback`、`ObMallocCallbackGuard` | delete | 只在 obmalloc 的 alloc/free 里调用（`ob_ctx_allocator.cpp:394-400,413-419`），jemalloc 下从不触发；`obmp_query.cpp:357-359` 等处安装回调的代码随之删掉，可观察行为不变 |
| `ob_malloc_sample_struct.h/.cpp`（175+33） | `ObMallocSampleMap`、`ObMallocSampleLimiter` | replace:mem | 类型保留给视图用，内容恒为空（S22）；`set_interval` 是空操作 |
| `malloc_hook.h/.cpp`（27+344）、`malloc_hook_extended.cpp`（330） | libc 的 `malloc`/`free`/…/`operator new` 覆盖 | replace:mem | §2.8：Rust 主程序里在 Linux 上照样覆盖 libc 符号，转发到 `je_*`（Q5） |
| `memory_dump.h/.cpp`（236+805） | `ObMemoryDump`、`ObMemoryDumpTask`、`LabelItem` | replace:mem | 门面（S23）：生命周期方法和查询接口保留，不扫描内存，不起线程；`siglongjmp` 探测不再需要（Q7） |

### 1.3 `src/oblib/lib/rc/`、`resource/`、`objectpool/`、`utility/` 的相关单元

| 单元（行数） | 主要类、宏 | 处置 | 去处与理由 |
|---|---|---|---|
| `rc/context.h/.cpp`（873+71） | `MemoryContext`、`__MemoryContext__`、`ContextParam`、`Flow`、`CURRENT_CONTEXT`、`ROOT_CONTEXT`、`CREATE_CONTEXT`、`DESTROY_CONTEXT`、`WITH_CONTEXT`、`CREATE_WITH_TEMP_CONTEXT` | replace:mem | `MemCtx`、`ContextParam`（§2.4）；宏的译法见 §2.12 |
| `rc/ob_rc.h/.cpp`（31+30） | `current_resource_owner_id()` | 不属本模块 | 0.4 列出的 oblib→share 钩子 |
| `resource/ob_resource_mgr.h/.cpp`（145+549）、`resource/achunk_mgr.h/.cpp`（390+489） | `ObResourceMgr`、`ObMemoryMgr`、`AChunkMgr` | delete | obmalloc 的硬上限和 chunk 缓存。硬上限从未设置（`ob_resource_mgr.cpp:30-40`，全仓找不到 `set_allocator_hard_limit` 的调用者，内存备忘 §2.2） |
| `resource/ob_cache_washer.h`（62） | `ObICacheWasher` | delete | 只被 `ObMemoryMgr` 在 obmalloc 分配 chunk 时调用（`ob_resource_mgr.cpp:62-67`）。KV cache 自己调用的 `sync_wash_mbs`（`ob_kvcache_store.cpp:938`）留作 08 的方法；`ObResourceMgr::set_cache_washer`（`ob_server.cpp:2249-2250`）随之删掉 |
| `objectpool/ob_concurrency_objpool.h`（167） | `op_alloc`/`op_free`/`op_reclaim_*` | replace:mem | `try_box`/drop（约 33 个文件） |
| `objectpool/ob_pooled_allocator.h`（95）、`ob_pool.h/.ipp`（156+164） | `ObPooledAllocator`、`ObPool`、`ObSmallBlockAllocator` | replace:mem | 全局分配器；作为 `ObHashMap` 的节点分配器出现的（如 `ob_log_plan.h:1840-1842`）由 05 的哈希表移植吸收 |
| `objectpool/ob_server_object_pool.h/.cpp`（319+19） | `ObServerObjectPool` | delete | `borrow_server_object`/`return_server_object` 没有调用者，`server_obj_pool_create` 从未实例化（`ob_server_runtime_controller.cpp:121-140`）；0.9：对象池首版去掉 |
| `objectpool/ob_cached_allocator.h`、`ob_resource_pool.h`、`ob_global_factory.h`、`ob_tc_factory.h`（121+357+547+422） | — | delete | 外部 0 个文件 |
| `utility/ob_smart_var.h/.cpp`（263+44） | `SMART_VAR`、`SMART_VAR_INDEPENDENT`、`HEAP_VAR`、`SMART_VARS_2/3/4`、`HEAP_VARS_2/3/4`、`smart_alloc` | replace:mem | `smart_var!` 等同名小写宏（§2.10） |
| `utility/ob_mod_define.h`（548） | `ObCtxIds`、`ObCtxInfo`、`ObModIds` 标签表、`ObLabelItem` | replace:mem | ctx id 保留成整数常量（它会被序列化，如 `ob_chunk_datum_store.cpp:1936-1942`）；`ObCtxInfo` 的名字表给视图用；`ObModIds` 的标签随 `ObMemAttr` 删除 |
| `utility/alloc_assist.h`（125） | `MEMCPY` 等、`ACHUNK_PRESERVE_SIZE` | 不属本模块 | 字符串和内存工具宏归 06；常量 `ACHUNK_PRESERVE_SIZE` 由 06 随 `ob_define` 一起给出 |

### 1.4 oblib 之外、由本模块取代的单元

| 单元 | 处置 | 去处 |
|---|---|---|
| `src/sql/executor/ob_memory_tracker.h/.cpp` | replace:mem | `ObMemTracker`、`ObMemTrackerGuard`（§2.5）；桩文件在 sql crate：`pub use oblib::mem::{ObMemTracker, ObMemTrackerGuard};` |
| `src/sql/executor/ob_memory_tracker_wrapper.h/.cpp`、`src/sql/parser/ob_memory_tracker_wrapper.h/.cpp` | replace:mem | `check_mem_status`、`try_check_mem_status`（§2.5）。`parse_node.c:177-178` 的 weak 缺省实现由 09 的回调蹦床取代 |

### 1.5 不由本模块取代、但要用本模块 API 的单元（照常翻译）

| 单元 | 用到什么 |
|---|---|
| `src/observer/omt/ob_th_worker.cpp`（:239-261、:325-342） | 请求 `MemCtx`、`ObMemTrackerGuard::with`、`check_mem_status` |
| `src/observer/mysql/obmp_query.cpp`（:469-482）、`obmp_stmt_execute.cpp`（:1755-1770）、`src/oblib/rpc/frame/ob_req_queue_thread.cpp`（:77-91） | 请求 `MemCtx`（`CREATE_WITH_TEMP_CONTEXT` 情况 (a)） |
| 35 个工作区文件（`grep -rln 'sql_mem_processor_\|ObSqlMemMgrProcessor' src`，见 S14） | 子 `MemCtx`、`ContextParam::set_memory_tracker`、`MemCtx::used()/hold()`、`MemoryContextMalloc::alloc` |
| `src/sql/engine/ob_sql_memory_manager.*`、`ob_sql_mem_mgr_processor.*` | `MemBudget`（取代 `MemoryUsageTracker workarea_managed_tracker_`） |
| `src/storage/allocator/ob_vector_allocator.*` | `MemBudget`、`ForeignBlock` |
| `src/storage/allocator/ob_shared_memory_allocator_mgr.h`、`src/storage/tx_table/ob_tx_data_table.cpp`、`ob_tx_data_memtable.cpp` | `MemBudget`、`Arena::set_memory_tracker` |
| `src/share/config/ob_server_config.cpp`（:43-199） | `get_memory_by_percentage`、`set_memory_budget`、`DEFAULT_MEMORY_BUDGET` |
| `src/oblib/lib/compress/ob_compress_util.h`（:76-110）、`src/share/roaringbitmap/ob_rb_memory_mgr.*` | `ForeignBlock`（第三方库回调的安全侧；蹦床在 09） |
| `src/share/cache/*`（08）、`src/storage/allocator/ob_fifo_arena.*`、memstore 分配器（08 的 `storage_core`） | `MemBudget::register_probe`（只给视图供数，S16） |
| `src/observer/main.cpp`（:647-654、:729、:738、:764-806） | 后端、macOS zone、fork 之后的恢复（§2.7） |
| `src/observer/virtual_table/` 的内存视图（S22） | §2.11 的统计接口 |

## 2. Rust API

### 2.0 布局与 cargo 特性

```text
rust/port/oblib/src/mem/          ← §3 白名单第 1 项，模块根写 #![allow(unsafe_code)]
  mod.rs          对外 re-export
  arena.rs        Arena、ObStr、ArenaVec、PageArena 口径的统计、Latch<Arena> 的方法
  heap.rs         try_box、try_box_with、try_vec、try_reserve、try_bytes、try_bytes_zeroed、ob_malloc、TrackedBuf、AlignedBuf、usable_size
  ctx.rs          ContextParam、MemCtx、MemoryContextMalloc
  tracker.rs      ObMemTracker、ObMemTrackerGuard、check_mem_status、try_check_mem_status（11049）
  budget.rs       MemBudget、视图登记
  jemalloc.rs     ObMalloc（#[global_allocator]）、je_* 声明、mallctl、统计、malloc_conf、后端、macOS zone、fork 之后
  malloc_hook.rs  libc 符号覆盖（Linux）
  foreign.rs      ForeignBlock
  smart_var.rs    smart_var! 等宏（#[macro_export]）
  stat.rs         alloc_func 的函数、ObCtxIds、ObCtxInfo、ObLabelItem、视图数据、ObMemoryDump 门面
```

| oblib 的 cargo 特性 | 缺省 | 作用 | 何时打开 |
|---|---|---|---|
| `global-allocator` | 开 | 注册 `#[global_allocator] static OB_MALLOC: ObMalloc` | 一直开；共存期的 staticlib 里 Rust 的分配因此也进 C++ 链接的那一份 jemalloc |
| `link-jemalloc` | 关 | 依赖 `seekdb-jemalloc-sys` 以链接 `libjemalloc_pic.a` | `cargo test` 和 Rust 主程序打开；共存期的 staticlib 关闭，避免与 CMake 已链接的 `seekdb_jemalloc` 重复（Q4） |
| `define-malloc-conf` | 关 | 定义 `je_malloc_conf` 符号 | 只在 Rust 主程序打开；共存期由 `ob_malloc.cpp:39-42` 定义 |
| `malloc-hook` | 关 | Linux 上覆盖 libc 的分配符号（§2.8） | 只在 Rust 主程序打开；共存期由 C++ 的 `malloc_hook` 静态库负责 |

在 Windows、Android 和 `cfg(miri)` 下，`ObMalloc` 退回 `std::alloc::System`：基线在这些平台没有构建 jemalloc（`deps/external/CMakeLists.txt:17-19`），C++ 在那里用 obmalloc，Rust 不支持 obmalloc（A.4 `malloc-backend`）；Miri 不能调用外部函数。

### 2.1 常量与错误

```rust
// 错误类型、错误码和日志宏都来自 04：ObError、ObResult<T = ()>、OB_ALLOCATE_MEMORY_FAILED（-4013）、
// OB_EXCEED_QUERY_MEM_LIMIT（-11049）、OB_INVALID_ARGUMENT、OB_NOT_INIT、OB_ERR_UNEXPECTED、log_warn!、log_user_error!。
// OB_MALLOC_NORMAL_BLOCK_SIZE（8K-256）、OB_MALLOC_MIDDLE_BLOCK_SIZE（64K-256）、OB_MALLOC_BIG_BLOCK_SIZE（2M-17K）、
// OB_MALLOC_REQ_NORMAL_BLOCK_SIZE（240K）、OB_DIRECT_IO_ALIGN（4096）来自 ob_define 单元（ob_define.h:407,1305-1308）。

/// std 的 try_reserve 失败 → OB_ALLOCATE_MEMORY_FAILED。翻译代码写 `ob_fail!(ret = try_reserve(&mut v, n))`，不直接用这个转换。
impl From<std::collections::TryReserveError> for ObError { fn from(_: std::collections::TryReserveError) -> ObError { todo!() } }

/// C++: lib::ObCtxIds（ob_mod_define.h:18-26 的 X-macro，:472-491 的结构和别名）。整数常量，不是 enum：它会被序列化。
pub struct ObCtxIds;
impl ObCtxIds {
    pub const DEFAULT_CTX_ID: u64 = 0;
    pub const PLAN_CACHE_CTX_ID: u64 = 1;
    pub const WORK_AREA: u64 = 2;
    pub const GLIBC: u64 = 3;
    pub const CO_STACK: u64 = 4;
    pub const KVSTORE_CACHE_ID: u64 = 5;
    pub const META_OBJ_CTX_ID: u64 = 6;
    pub const VECTOR_CTX_ID: u64 = 7;
    pub const MAX_CTX_ID: u64 = 8;
    // 别名，全部等于 DEFAULT_CTX_ID（ob_mod_define.h:480-490）
    pub const SCHEMA_SERVICE: u64 = 0;   pub const MEMSTORE_CTX_ID: u64 = 0;  pub const EXECUTE_CTX_ID: u64 = 0;
    pub const TRANS_CTX_MGR_ID: u64 = 0; pub const TX_CALLBACK_CTX_ID: u64 = 0; pub const LOB_CTX_ID: u64 = 0;
    pub const TX_DATA_TABLE: u64 = 0;    pub const MDS_DATA_ID: u64 = 0;      pub const MDS_CTX_ID: u64 = 0;
    pub const MERGE_RESERVE_CTX_ID: u64 = 0; pub const MERGE_NORMAL_CTX_ID: u64 = 0;
}

/// C++: common::ObCtxInfo（ob_mod_define.h:493-520）。
pub struct ObCtxInfo { /* 名字表 */ }
impl ObCtxInfo {
    /// 越界返回 "INVALID_CTX_NAME"。
    pub fn get_ctx_name(&self, ctx_id: u64) -> &'static str { todo!() }
    pub fn is_valid_ctx_name(&self, ctx_name: &[u8], ctx_id: &mut u64) -> bool { todo!() }
}
/// C++: get_global_ctx_info()（ob_tc_malloc.cpp:49-53）。
pub fn get_global_ctx_info() -> &'static ObCtxInfo { todo!() }
```

### 2.2 `Arena`、`ObStr`、`ArenaVec`、`Latch<Arena>`

```rust
/// C++: ObString 视图（查询作用域）。字节，不假定 UTF-8，不带结尾 NUL（§2.5）。
pub type ObStr<'q> = &'q [u8];

/// C++: ObArenaAllocator / PageArena<CharT, PageAllocatorT> / CharArena / ModuleArena（page_arena.h:192-1079）。
/// 内存来自 bumpalo::Bump；used()/total()/pages() 按 PageArena 的算法记账（S3）。Send，!Sync。
pub struct Arena { /* bump: bumpalo::Bump, stat: Cell<PageArenaStat>, trackers */ }

impl Arena {
    /// C++: ObArenaAllocator()，页大小 OB_MALLOC_NORMAL_BLOCK_SIZE（page_arena.h:978-981）。不预先分配。
    pub fn new() -> Arena { todo!() }
    /// C++: ObArenaAllocator(label, page_size, ctx_id) / ObArenaAllocator(attr, page_size)：标签和 ctx 删掉，只留页大小。
    pub fn with_page_size(page_size: i64) -> Arena { todo!() }

    /// 查询作用域的对象：C++ `alloc(sizeof(T))` + placement new。T 不能需要 Drop（编译期拒绝，0.7）。
    #[allow(clippy::mut_from_ref)]
    pub fn try_alloc<T>(&self, val: T) -> ObResult<&mut T> {
        const { assert!(!core::mem::needs_drop::<T>(), "Arena 里的类型不能需要 Drop：改用 try_box 或拥有型") };
        todo!()
    }
    /// 先分配、再在原位构造（大对象避免栈上的临时值）。
    #[allow(clippy::mut_from_ref)]
    pub fn try_alloc_with<T, F: FnOnce() -> T>(&self, f: F) -> ObResult<&mut T> {
        const { assert!(!core::mem::needs_drop::<T>()) };
        todo!()
    }
    /// 定型的数组：C++ `ObFixedArray` 一次性拷进 arena。空切片不分配。
    #[allow(clippy::mut_from_ref)]
    pub fn try_slice_copy<T: Copy>(&self, src: &[T]) -> ObResult<&mut [T]> { todo!() }
    #[allow(clippy::mut_from_ref)]
    pub fn try_slice_fill_default<T: Default>(&self, len: usize) -> ObResult<&mut [T]> {
        const { assert!(!core::mem::needs_drop::<T>()) };
        todo!()
    }
    /// C++: ob_write_string(alloc, src, dst)（ob_string.h:805-825）。src 为空时返回 Ok(&[])，不分配。
    pub fn try_str(&self, src: &[u8]) -> ObResult<ObStr<'_>> { todo!() }
    /// C++: ob_write_string(alloc, src, dst, true)。多分配 1 字节写 NUL，返回的切片仍是 src.len() 长（NUL 不在切片里）。
    pub fn try_str_c(&self, src: &[u8]) -> ObResult<ObStr<'_>> { todo!() }

    /// C++: ObArenaAllocator::alloc(sz)（page_arena.h:993-999 → alloc_aligned(sz, 16)）。len <= 0 → Err(OB_ALLOCATE_MEMORY_FAILED)。
    /// 内容置零（Rust 不能交出未初始化的 &mut [u8]）。
    #[allow(clippy::mut_from_ref)]
    pub fn alloc(&self, len: i64) -> ObResult<&mut [u8]> { todo!() }
    /// C++: ObArenaAllocator::alloc_aligned(sz, align)（:1005-1011）。align 是 2 的幂。
    #[allow(clippy::mut_from_ref)]
    pub fn alloc_aligned(&self, len: i64, align: i64) -> ObResult<&mut [u8]> { todo!() }
    /// C++: PageArena::alloc(sz)（:591-597 → _alloc，不对齐），给直接用 PageArena/CharArena/ModuleArena 的单元（18+1+4 个文件）。
    #[allow(clippy::mut_from_ref)]
    pub fn alloc_unaligned(&self, len: i64) -> ObResult<&mut [u8]> { todo!() }
    /// C++: ObArenaAllocator::realloc(ptr, oldsz, newsz)（:1012-1018 → PageArena::realloc :671-691）。
    /// 旧切片被移入；成功时返回长 new_len 的新切片，前 min(old, new) 字节是旧内容。
    pub fn realloc<'a>(&'a self, old: &'a mut [u8], new_len: i64) -> ObResult<&'a mut [u8]> { todo!() }

    /// C++: used() / total() / pages()（:964-966），PageArena 口径（S3）。
    pub fn used(&self) -> i64 { todo!() }
    pub fn total(&self) -> i64 { todo!() }
    pub fn pages(&self) -> i64 { todo!() }

    /// C++: clear() / reset() / PageArena::free()（:1021-1029、:721-740）：释放全部页。要 &mut，此时不能有存活的借用。
    pub fn reset(&mut self) { todo!() }
    /// C++: reuse()（:1036 → :945-949）：释放大页，保留普通页，从头重用。
    pub fn reuse(&mut self) { todo!() }
    /// C++: reset_remain_one_page()（:1030-1035 → :743-778）。
    pub fn reset_remain_one_page(&mut self) { todo!() }

    /// C++: ObArenaAllocator::set_memory_tracker（:1049-1056）：按 total() 的增量记账；reuse() 不调整（:1036，S6）。
    pub fn set_memory_tracker(&mut self, tracker: Option<Arc<MemBudget>>) { todo!() }
    /// C++: ModulePageAllocator 经 resolver 走 TrackedAllocator 的按页计账（:116-181）：每分配、释放一页都计原始页大小，含 reuse() 释放的大页。
    pub fn set_page_tracker(&mut self, tracker: Option<Arc<MemBudget>>) { todo!() }
}
impl Default for Arena { fn default() -> Arena { Arena::new() } }

/// 增长期的查询作用域数组（0.7、§2.5）。T 不能需要 Drop。!Send（内部持有 &'q Bump）。
/// C++: 查询作用域里的 ObFixedArray<T, ObIAllocator> 和 auto_free 数组的存储；ObIArray<T> 的 impl 由 05 提供。
pub struct ArenaVec<'q, T> { /* buf: bumpalo::collections::Vec<'q, T>, arena: &'q Arena */ _p: core::marker::PhantomData<&'q T> }

impl<'q, T> ArenaVec<'q, T> {
    pub fn new_in(arena: &'q Arena) -> ArenaVec<'q, T> { const { assert!(!core::mem::needs_drop::<T>()) }; todo!() }
    pub fn try_with_capacity_in(capacity: usize, arena: &'q Arena) -> ObResult<ArenaVec<'q, T>> { todo!() }
    /// 不够时扩到 max(2 × capacity, capacity + additional)，最少 1（与 ObArray 的增长相同，ob_array.h:610-614）。
    pub fn try_reserve(&mut self, additional: usize) -> ObResult { todo!() }
    pub fn try_reserve_exact(&mut self, additional: usize) -> ObResult { todo!() }
    /// C++: ObIArray::push_back；内部 try_reserve(1)。
    pub fn push_back(&mut self, val: T) -> ObResult { todo!() }
    pub fn pop_back(&mut self) -> Option<T> { todo!() }
    pub fn count(&self) -> i64 { todo!() }
    pub fn capacity(&self) -> i64 { todo!() }
    pub fn as_slice(&self) -> &[T] { todo!() }
    pub fn as_mut_slice(&mut self) -> &mut [T] { todo!() }
    /// C++: reuse()：清空，保留容量。
    pub fn reuse(&mut self) { todo!() }
    /// 定型：ArenaVec → &'q [T]（0.7）。
    pub fn into_slice(self) -> &'q [T] { todo!() }
    pub fn into_slice_mut(self) -> &'q mut [T] { todo!() }
}

/// 线程安全的 arena（0.7、M10）：C++ ObSafeArenaAllocator（page_arena.h:1081-1126）、ObSafeArena（ob_safe_arena.h）、
/// MemoryContext::get_safe_arena_allocator()。Latch 是 02 的类型；下面是写在 oblib::mem 里的固有方法（同一 crate）。
/// 构造：Latch::new(ObLatchIds::OB_AREAN_ALLOCATOR_LOCK, Arena::new())（锁 id 照 page_arena.h:1086）。
/// reset/reuse 经 Latch::get_mut()，要 &mut，此时没有存活的借用。
impl crate::sync::Latch<Arena> {
    #[allow(clippy::mut_from_ref)]
    pub fn try_alloc<T>(&self, val: T) -> ObResult<&mut T> { const { assert!(!core::mem::needs_drop::<T>()) }; todo!() }
    pub fn try_str(&self, src: &[u8]) -> ObResult<ObStr<'_>> { todo!() }
    #[allow(clippy::mut_from_ref)]
    pub fn alloc(&self, len: i64) -> ObResult<&mut [u8]> { todo!() }
    pub fn used(&self) -> i64 { todo!() }
    pub fn total(&self) -> i64 { todo!() }
}
```

### 2.3 拥有型分配

```rust
/// C++: OB_NEW(T, label, ..)、OB_NEWx 指向拥有型 pool 的情况、op_alloc、SMART_VAR/HEAP_VAR 的堆上分支。
/// 失败 → Err(OB_ALLOCATE_MEMORY_FAILED)。零大小类型不分配。
pub fn try_box<T>(val: T) -> ObResult<Box<T>> { todo!() }
/// 先分配，再把 f() 的结果写进去（编译器内联 f 后可原位构造，大对象不经过栈）。
pub fn try_box_with<T, F: FnOnce() -> T>(f: F) -> ObResult<Box<T>> { todo!() }
/// Vec::try_with_capacity 的替代（在 1.98.1 不稳定）。
pub fn try_vec<T>(capacity: usize) -> ObResult<Vec<T>> { todo!() }
/// 容器增长、用户决定大小或超过 64 KB 的缓冲一律走它（0.7）。
pub fn try_reserve<T>(v: &mut Vec<T>, additional: usize) -> ObResult { todo!() }
/// 拥有型的 ob_write_string：src 为空 → 空 Box，不分配。
pub fn try_bytes(src: &[u8]) -> ObResult<Box<[u8]>> { todo!() }
/// 置零的缓冲：C++ 的 `char buf[N]`（SMART_VAR）、ob_malloc + MEMSET、calloc。
pub fn try_bytes_zeroed(len: usize) -> ObResult<Box<[u8]>> { todo!() }
/// C++: ob_malloc(nbyte, attr)（ob_malloc.h:99-127，jemalloc 分支 :118-125）。attr 删掉。
/// nbyte <= 0 → Err(OB_ALLOCATE_MEMORY_FAILED) 并打 WARN，与 C++ 相同；内容置零。ob_free(p) → drop。
pub fn ob_malloc(nbyte: i64) -> ObResult<Box<[u8]>> { todo!() }
/// C++: ob_malloc_usable_size（ob_malloc.cpp:216-233）的口径：jemalloc 给 size 字节分配的实际大小（je_nallocx）。
pub fn usable_size(size: usize) -> usize { todo!() }

/// C++: TrackedAllocator 的一次分配（ob_malloc.h:222-359）和 MemoryContextMalloc 的一次分配（:418-508）：
/// 拥有的字节缓冲，创建时把 charged 字节记到 tracker，Drop 时扣回。Send + Sync。
pub struct TrackedBuf { /* buf: Box<[u8]>, tracker: Option<Arc<MemBudget>>, charged: i64 */ }
impl TrackedBuf {
    /// TrackedAllocator::alloc 的口径：记请求的字节数（:262-268）。len <= 0 → Err(OB_ALLOCATE_MEMORY_FAILED)。内容置零。
    pub fn try_new(len: i64, tracker: Option<&Arc<MemBudget>>) -> ObResult<TrackedBuf> { todo!() }
    pub fn charged(&self) -> i64 { todo!() }
}
impl core::ops::Deref for TrackedBuf { type Target = [u8]; fn deref(&self) -> &[u8] { todo!() } }
impl core::ops::DerefMut for TrackedBuf { fn deref_mut(&mut self) -> &mut [u8] { todo!() } }
impl Drop for TrackedBuf { fn drop(&mut self) { todo!() } }

/// C++: ob_malloc_align(align, nbyte) / ob_free_align / ObIAllocator::alloc_align（ob_iallocator.h:27-65）、
/// 直接 IO 的对齐缓冲（如 ob_storage_log_reader.cpp:65 的 OB_DIRECT_IO_ALIGN）。Send + Sync。
pub struct AlignedBuf { /* ptr: NonNull<u8>, len: usize, align: usize */ }
impl AlignedBuf {
    /// 实际对齐 = max(align, 16)（C++ 的 real_align = align_up2(align, 16)，ob_iallocator.h:42）。len 可以是 0。内容置零。
    pub fn try_new_zeroed(len: usize, align: usize) -> ObResult<AlignedBuf> { todo!() }
    pub fn len(&self) -> usize { todo!() }
    pub fn align(&self) -> usize { todo!() }
}
impl core::ops::Deref for AlignedBuf { type Target = [u8]; fn deref(&self) -> &[u8] { todo!() } }
impl core::ops::DerefMut for AlignedBuf { fn deref_mut(&mut self) -> &mut [u8] { todo!() } }
impl Drop for AlignedBuf { fn drop(&mut self) { todo!() } }
```

### 2.4 `MemCtx`、`ContextParam`、`MemoryContextMalloc`

```rust
/// C++: lib::ContextPropertyEnum（context.h:100-108）。
pub const DEFAULT_PROPERTIES: i64 = 0;
pub const ADD_CHILD_THREAD_SAFE: i64 = 1 << 1;
pub const ALLOC_THREAD_SAFE: i64 = 1 << 2;
pub const USE_TL_PAGE_OPTIONAL: i64 = 1 << 3;
pub const RETURN_MALLOC_DEFAULT: i64 = 1 << 4;

/// C++: lib::ContextParam（context.h:201-254）。set_mem_attr / set_label / set_ablock_size / set_parallel 删掉（S11）。
#[derive(Clone)]
pub struct ContextParam { /* properties_: i64, page_size_: i64, memory_tracker_: Option<Arc<MemBudget>> */ }
impl ContextParam {
    /// properties 0，页大小 OB_MALLOC_NORMAL_BLOCK_SIZE（context.h:205-211）。
    pub fn new() -> ContextParam { todo!() }
    pub fn set_properties(&mut self, properties: i64) -> &mut ContextParam { todo!() }
    pub fn set_page_size(&mut self, page_size: i64) -> &mut ContextParam { todo!() }
    /// 取代 set_mem_attr(…, ObCtxIds::WORK_AREA)：arena 的页按页记到这个预算（S14）。
    pub fn set_memory_tracker(&mut self, tracker: Arc<MemBudget>) -> &mut ContextParam { todo!() }
}
impl Default for ContextParam { fn default() -> ContextParam { ContextParam::new() } }

/// 请求和语句级的 lib::MemoryContext（0.7）：显式传参，不放进线程局部（0.5）。Send，!Sync。
/// 拥有一个 Arena（arena_alloc_）和一个 MemoryContextMalloc（freeable_malloc_alloc_），
/// 以及承载 tree_mem_hold() 的预算（挂在父 MemCtx 的预算下）。Drop 就是 DESTROY_CONTEXT。
pub struct MemCtx { /* arena: Arena, malloc: MemoryContextMalloc, tree: Arc<MemBudget>, properties: i64 */ }

impl MemCtx {
    /// 根上下文：CREATE_WITH_TEMP_CONTEXT 的情况 (a)、(c)，以及 ROOT_CONTEXT->CREATE_CONTEXT。
    /// 不挂到任何树上（C++ 的临时上下文传 parent = nullptr，context.h:843；根上下文带 ADD_CHILD_THREAD_SAFE，context.cpp:35）。
    pub fn new(param: &ContextParam) -> MemCtx { todo!() }
    /// C++: parent->CREATE_CONTEXT(context, param)（context.h:58-63、:518-550）。子上下文挂在 self 的预算树下，
    /// 除非 param 带 ADD_CHILD_THREAD_SAFE（S11）。在 jemalloc 下不会失败；保留 ObResult 和输出参数，便于逐行翻译。
    pub fn create_context(&self, context: &mut Option<MemCtx>, param: &ContextParam) -> ObResult { todo!() }

    /// C++: get_arena_allocator()（context.h:398）。
    pub fn get_arena_allocator(&self) -> &Arena { todo!() }
    /// C++: get_malloc_allocator()（context.h:397）。
    pub fn get_malloc_allocator(&self) -> &MemoryContextMalloc { todo!() }
    /// C++: get_allocator()（context.h:400-404）。请求和语句级上下文都不带 RETURN_MALLOC_DEFAULT，返回 arena；带它的上下文不映射到 MemCtx（S11）。
    pub fn get_allocator(&self) -> &Arena { todo!() }

    /// C++: reuse() / reuse_arena()（context.h:354-361）、reset_remain_one_page()（:362-365）。
    pub fn reuse(&mut self) { todo!() }
    pub fn reuse_arena(&mut self) { todo!() }
    pub fn reset_remain_one_page(&mut self) { todo!() }

    /// C++ 在 jemalloc 后端下的口径（context.h:366-395）：hold = malloc_hold + arena_hold，used = malloc_used + arena_used。
    pub fn hold(&self) -> i64 { todo!() }
    pub fn used(&self) -> i64 { todo!() }
    pub fn malloc_hold(&self) -> i64 { todo!() }
    pub fn malloc_used(&self) -> i64 { todo!() }
    pub fn arena_hold(&self) -> i64 { todo!() }
    pub fn arena_used(&self) -> i64 { todo!() }
    /// C++: tree_mem_hold()（context.h:600-613、context.cpp:56-63）。
    pub fn tree_mem_hold(&self) -> i64 { todo!() }

    /// C++: CHECK_MEM_STATUS() / TRY_CHECK_MEM_STATUS(n) 在手里有 MemCtx 的调用点的写法（RULEBOOK §2.4）。
    /// 语义与 check_mem_status() 完全相同：检查本线程当前请求的 ObMemTracker，与 self 是哪个 MemCtx 无关（S12、Q1）。
    pub fn check_status(&self) -> ObResult { todo!() }
    pub fn try_check_status(&self, check_try_times: i64) -> ObResult { todo!() }
}

/// C++: common::MemoryContextMalloc（ob_malloc.h:418-508）：活着的分配按 jemalloc 实际大小计数。
pub struct MemoryContextMalloc { /* live_bytes_: Arc<MemBudget>（父是所属 MemCtx 的树预算） */ }
impl MemoryContextMalloc {
    /// C++: get_malloc_allocator().alloc(size)。size <= 0 → Err(OB_ALLOCATE_MEMORY_FAILED)（与 ob_malloc.h:119 相同）。
    /// 记 usable_size(size)，Drop 时扣回。内容置零。
    pub fn alloc(&self, size: i64) -> ObResult<TrackedBuf> { todo!() }
    /// C++: ObArenaAllocator allocator(ctx->get_malloc_allocator())：页按 jemalloc 实际大小记到本上下文（如 ob_table_location.cpp:1832）。
    pub fn new_arena(&self, page_size: i64) -> Arena { todo!() }
    /// C++: total() / used()（ob_malloc.h:470-471），两者相等。
    pub fn total(&self) -> i64 { todo!() }
    pub fn used(&self) -> i64 { todo!() }
    /// 容器的缓冲在 C++ 里来自这个分配器、在 Rust 里是 Vec 时，由容器报告容量变化（delta 用 usable_size 计算，S11）。
    pub fn adjust(&self, delta: i64) { todo!() }
    /// 给 05 的带计账容器用。
    pub fn tracker(&self) -> &Arc<MemBudget> { todo!() }
}
```

### 2.5 单查询上限 11049

```rust
/// C++: lib::ObMemTracker（ob_memory_tracker.h:28-45）。C++ 里它是线程局部的，这里放进 Arc，由请求入口经闭包装进线程局部（S12）。
pub struct ObMemTracker { /* cache_mem_limit_: AtomicI64, check_status_times_: AtomicU16, try_check_tick_: AtomicU16,
                            mem_context_: Arc<MemBudget>（受保护的 MemCtx 的树预算） */ }
impl ObMemTracker {
    /// C++: ObMemTrackerGuard::update_mem_limit()（ob_memory_tracker.cpp:30-36）。
    pub fn update_mem_limit(&self) { todo!() }
    /// C++: check_status()（:37-56）。
    pub fn check_status(&self) -> ObResult { todo!() }
    /// C++: try_check_status(n)（:57-65）。
    pub fn try_check_status(&self, check_try_times: i64) -> ObResult { todo!() }
}

/// C++: lib::ObMemTrackerGuard（ob_memory_tracker.h:47-67）。guard 改成闭包式（0.5：线程局部装入一律用闭包）。
pub struct ObMemTrackerGuard;
impl ObMemTrackerGuard {
    pub const DEFAULT_CHECK_STATUS_TRY_TIMES: i64 = 1024;
    pub const UPDATE_MEM_LIMIT_THRESHOLD: i64 = 512;
    /// C++: MEM_TRACKER_GUARD(ctx)（ob_memory_tracker.h:72-73），作用到所在块结束。
    /// 新建 ObMemTracker（计数清零，与 guard 的构造相同），装进本线程，f 返回后恢复原值。
    pub fn with<R>(mem_ctx: &MemCtx, f: impl FnOnce() -> R) -> R { todo!() }
    /// C++: ObMemTrackerGuard::check_status() / try_check_status(n)。本线程没有装入 tracker 时返回 Ok(())。
    pub fn check_status() -> ObResult { todo!() }
    pub fn try_check_status(check_try_times: i64) -> ObResult { todo!() }
}

/// C++: CHECK_MEM_STATUS() 与 C 包装 check_mem_status()（ob_memory_tracker_wrapper.cpp:20-23）。
pub fn check_mem_status() -> ObResult { todo!() }
/// C++: TRY_CHECK_MEM_STATUS(n) 与 try_check_mem_status(n)（:25-28）；解析器的 new_node 每 1024 个节点调一次（parse_node.c:183-187）。
pub fn try_check_mem_status(check_try_times: i64) -> ObResult { todo!() }
```

### 2.6 `MemBudget`

```rust
/// 原子计数的内存预算（0.7）。取代：
/// - common::MemoryUsageTracker（ob_malloc.h:183-215；实例：工作区 workarea_managed_tracker_、tx data 的两个 tracker）；
/// - common::ObBlockAllocMgr（ob_block_alloc_mgr.h:30-61；各池的总量上限）；
/// - 模块里手写的 int64 计数加 ATOMIC_*（如向量的 all_used_mem_，ob_vector_allocator.h:67）；
/// - MemoryContext 树的 tree_mem_hold（由 MemCtx 内部使用）。
/// 方法名保留各自 C++ 类的名字。所有读改写都是 SeqCst（S13）。Send + Sync。
pub struct MemBudget { /* name_: &'static str, ctx_id_: u64, hold_: AtomicI64, limit_: AtomicI64, parent_: Option<Arc<MemBudget>> */ }

impl MemBudget {
    /// 模块级的静态预算：`static WORK_AREA_X: MemBudget = MemBudget::new_static("SqlWorkArea", ObCtxIds::WORK_AREA);`
    /// name 不超过 15 字节（与 ObLabel 的限制相同，alloc_struct.h:84），用作视图的 MOD_NAME。limit 初值 i64::MAX。
    pub const fn new_static(name: &'static str, ctx_id: u64) -> MemBudget { todo!() }
    /// 动态预算；parent 只给 MemCtx 树用：本预算的每次增减同样加到所有祖先上。
    pub fn new(name: &'static str, ctx_id: u64, parent: Option<Arc<MemBudget>>) -> Arc<MemBudget> { todo!() }

    // —— MemoryUsageTracker 同名方法（ob_malloc.h:189-211）——
    /// size > 0 才计入。
    pub fn alloc(&self, size: i64) { todo!() }
    /// size > 0 才扣减。
    pub fn free(&self, size: i64) { todo!() }
    /// delta != 0 才调整。
    pub fn adjust(&self, delta: i64) { todo!() }
    pub fn used(&self) -> i64 { todo!() }
    pub fn reset(&self) { todo!() }

    // —— ObBlockAllocMgr 同名方法（ob_block_alloc_mgr.h:35-58）——
    pub fn set_limit(&self, limit: i64) { todo!() }
    pub fn limit(&self) -> i64 { todo!() }
    /// = used()。
    pub fn hold(&self) -> i64 { todo!() }
    /// alloc_block 的计账：先加 size，超过 limit 就减回并返回 false（:38-50，S13）。只用于无父预算的预算。
    pub fn try_alloc(&self, size: i64) -> bool { todo!() }
    /// C++: alloc_block(size, attr)：try_alloc 成功后分配 size 字节；限额或分配失败返回 None（C++ 返回 NULL）。
    /// 返回的 TrackedBuf 在 Drop 时扣回，即 free_block。
    pub fn alloc_block(self: &Arc<Self>, size: i64) -> Option<TrackedBuf> { todo!() }
    /// C++: can_alloc_block(size)：`(limit - hold) > size`（:57-59）。
    pub fn can_alloc_block(&self, size: i64) -> bool { todo!() }

    // —— 逐行移植手写计数用的原语，与 ATOMIC_* 一一对应（SeqCst）——
    pub fn load(&self) -> i64 { todo!() }
    pub fn store(&self, val: i64) { todo!() }
    pub fn aaf(&self, delta: i64) -> i64 { todo!() }
    pub fn faa(&self, delta: i64) -> i64 { todo!() }
    pub fn saf(&self, delta: i64) -> i64 { todo!() }
    pub fn bcas(&self, old: i64, new: i64) -> bool { todo!() }

    // —— 视图 ——
    pub fn name(&self) -> &'static str { todo!() }
    pub fn ctx_id(&self) -> u64 { todo!() }
    /// 登记到 __all_virtual_memory_info（S22）。只保存 Weak，预算释放后自动消失。
    pub fn register(self: &Arc<Self>) { todo!() }
    pub fn register_static(&'static self) { todo!() }
    /// 自己维护计数的模块（KV cache 的 store_size_、memstore 的 FIFO arena）只登记一个读数函数：probe 返回 (hold, used)。
    pub fn register_probe(name: &'static str, ctx_id: u64, probe: fn() -> (i64, i64)) { todo!() }
}
```

### 2.7 全局分配器与 jemalloc

```rust
/// C++: common::ObMalloc（ob_malloc.h:380-415，ob_malloc/ob_free 的直通封装；jemalloc 下忽略 attr，:118-125）。
/// 这里是 #[global_allocator] 的类型：不计数、不加块头、不分 ctx arena；不分配、不加锁、不 panic、不访问线程局部（0.7）。
pub struct ObMalloc;
unsafe impl core::alloc::GlobalAlloc for ObMalloc {
    unsafe fn alloc(&self, layout: core::alloc::Layout) -> *mut u8 { todo!() }
    unsafe fn alloc_zeroed(&self, layout: core::alloc::Layout) -> *mut u8 { todo!() }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: core::alloc::Layout) { todo!() }
    unsafe fn realloc(&self, ptr: *mut u8, layout: core::alloc::Layout, new_size: usize) -> *mut u8 { todo!() }
}
#[cfg(feature = "global-allocator")]
#[global_allocator]
static OB_MALLOC: ObMalloc = ObMalloc;

/// C++: ObMallocBackend（ob_malloc.h:38-44）。§2.12 的枚举规则：新类型加关联常量。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ObMallocBackend(pub i8);
impl ObMallocBackend {
    pub const OB_MALLOC_BACKEND_UNINITIALIZED: ObMallocBackend = ObMallocBackend(-1);
    pub const OB_MALLOC_BACKEND_OBMALLOC: ObMallocBackend = ObMallocBackend(0);
    pub const OB_MALLOC_BACKEND_JEMALLOC: ObMallocBackend = ObMallocBackend(1);
    pub const OB_MALLOC_BACKEND_UNKNOWN: ObMallocBackend = ObMallocBackend(2);
}
/// C++: ob_malloc.cpp:121-168、ob_malloc.h:56-84。
pub fn ob_malloc_backend_env_name() -> &'static str { todo!() }                 // "MALLOC_BACKEND"
pub fn ob_malloc_backend_name(backend: ObMallocBackend) -> &'static str { todo!() } // "obmalloc" / "jemalloc" / "unknown"
pub fn parse_ob_malloc_backend(name: Option<&[u8]>) -> ObMallocBackend { todo!() }
/// 永不返回 OBMALLOC：环境变量要 obmalloc 时记一次 WARN、返回 JEMALLOC（A.4 malloc-backend）；无效值照实返回 UNKNOWN（S18）。
pub fn get_ob_malloc_backend() -> ObMallocBackend { todo!() }
pub fn is_jemalloc_backend() -> bool { todo!() }
pub fn is_ob_malloc_backend() -> bool { todo!() }  // 恒为 false
/// C++: init_malloc_hook()（malloc_hook.cpp:121-154）：MALLOC_BACKEND 无效时往 stderr 写同样的报错并 _exit(127)。
/// Rust 主程序在 main 的第 1 步（0.5，屏蔽信号之前）调用；只在 Linux 上退出，与 C++ 相同（S18）。
pub fn init_malloc_hook() { todo!() }
/// C++: restore_malloc_backend_after_fork()（ob_malloc.cpp:170-175）：重新打开 jemalloc 的 background_thread。
pub fn restore_malloc_backend_after_fork() -> bool { todo!() }
/// C++: configure_darwin_malloc_zone(backend)（ob_malloc.cpp:63-117、:177-194）：把 "jemalloc_zone" 提到第一位。
#[cfg(target_os = "macos")]
pub fn configure_darwin_malloc_zone(backend: ObMallocBackend) -> bool { todo!() }
/// C++: get_virtual_memory_used(int64_t *resident_size)（ob_tc_malloc.cpp:60-82），逐行移植：Linux 读 /proc/self/statm，
/// macOS 打不开该文件而返回 0，Windows 用 GetProcessMemoryInfo。
pub fn get_virtual_memory_used(resident_size: Option<&mut i64>) -> i64 { todo!() }

/// jemalloc 的全局统计（mallctl "stats.*"，读之前先推进 "epoch"）。供内存视图用（A.4 mem-view）。
#[derive(Clone, Copy, Default, Debug)]
pub struct JemallocStats { pub allocated: i64, pub active: i64, pub resident: i64, pub mapped: i64, pub retained: i64, pub metadata: i64 }
/// mallctl 返回非零 → Err(OB_ERR_UNEXPECTED)。没有 jemalloc 的平台返回全 0。
pub fn get_jemalloc_stats() -> ObResult<JemallocStats> { todo!() }
```

`jemalloc.rs` 内部声明 `seekdb-jemalloc-sys` 构建出的 `je_` 前缀符号（`deps/external/cmake/Jemalloc.cmake:40` 的 `--with-jemalloc-prefix=je_`；该 crate 不提供任何 Rust FFI，`src/lib.rs` 只有一句 `#![no_std]`）：

```rust
extern "C" {
    fn je_malloc(size: usize) -> *mut c_void;
    fn je_calloc(n: usize, size: usize) -> *mut c_void;
    fn je_realloc(ptr: *mut c_void, size: usize) -> *mut c_void;
    fn je_free(ptr: *mut c_void);
    fn je_posix_memalign(memptr: *mut *mut c_void, align: usize, size: usize) -> c_int;
    fn je_aligned_alloc(align: usize, size: usize) -> *mut c_void;
    fn je_mallocx(size: usize, flags: c_int) -> *mut c_void;
    fn je_rallocx(ptr: *mut c_void, size: usize, flags: c_int) -> *mut c_void;
    fn je_sdallocx(ptr: *mut c_void, size: usize, flags: c_int);
    fn je_nallocx(size: usize, flags: c_int) -> usize;
    fn je_malloc_usable_size(ptr: *const c_void) -> usize;
    fn je_mallctl(name: *const c_char, oldp: *mut c_void, oldlenp: *mut usize, newp: *mut c_void, newlen: usize) -> c_int;
}
// MALLOCX_LG_ALIGN(la) = la；MALLOCX_ZERO = 0x40（jemalloc 5.3.1 的公开头文件）。
// Linux 上另有 je_memalign、je_valloc（malloc_hook 用）；macOS 没有 je_memalign（C++ 在那里用 je_posix_memalign，ob_jemalloc.h:91-96）。
```

### 2.8 libc 符号覆盖（Linux，特性 `malloc-hook`）

`malloc_hook.rs` 用 `#[no_mangle] extern "C"` 定义下列符号，实现照抄 C++ 的 jemalloc 分支：`malloc`/`free`/`realloc`/`memalign`/`malloc_usable_size` 直接转发到对应的 `je_*`（`malloc_hook.cpp:158-342`），其余照 `malloc_hook_extended.cpp:69-212` 改写成这几个函数的组合（如 `calloc` = `malloc` + 清零，`valloc`/`pvalloc`/`aligned_alloc`/`posix_memalign` 走 `memalign`）。清单：`malloc`、`free`、`calloc`、`realloc`、`memalign`、`posix_memalign`、`aligned_alloc`、`valloc`、`pvalloc`、`malloc_usable_size`、`cfree`、`strdup`、`strndup`、`free_sized`、`free_aligned_sized`，以及别名 `__libc_malloc`、`__libc_free`、`__libc_realloc`、`__libc_memalign`、`__libc_calloc`、`__libc_valloc`、`__libc_pvalloc`、`__libc_free_sized`、`__libc_free_aligned_sized`、`__posix_memalign`。不覆盖 `operator new/delete`（C++ shim 里的 `operator new` 经 libstdc++ 调 `malloc`，已被覆盖）、`mmap`/`munmap`（S19）。

### 2.9 第三方库分配回调的安全侧：`ForeignBlock`

```rust
/// 交给 C/C++ 库的一块堆内存（0.7、0.14 的回调规则：只接全局分配器加 MemBudget，不接 bump arena）。
/// 布局：16 字节头（u64 总长 = 16 + payload，u64 保留为 0）后接载荷；载荷 16 字节对齐、不初始化。
/// 头的大小和含义与 ObVsagMemContext 的 MEM_PTR_HEAD_SIZE 相同（ob_vector_allocator.cpp:54-81）。Send。
/// -sys 或 shim 里的 extern "C" 蹦床（09）只做 into_raw/from_raw；计账、限额检查写在调用方的安全代码里。
pub struct ForeignBlock { /* ptr: NonNull<u8>（指向载荷） */ }
impl ForeignBlock {
    pub const HEAD_SIZE: usize = 16;
    /// size == 0 → None（vsag 的 Allocate(0) 返回 nullptr）；分配失败 → None。
    pub fn try_new(size: u64) -> Option<ForeignBlock> { todo!() }
    /// 头里记的总长（vsag 用它计 all_vsag_use_mem_）。
    pub fn total_size(&self) -> i64 { todo!() }
    pub fn payload_size(&self) -> u64 { todo!() }
    /// C++: ObVsagMemContext::Reallocate（ob_vector_allocator.cpp:83-109）的拷贝部分：载荷够大就原样返回 Ok(self)；
    /// 否则新分配、按旧载荷长度拷贝、释放旧块；新分配失败返回 Err(self)（旧块仍有效）。
    pub fn try_grow(self, new_size: u64) -> Result<ForeignBlock, ForeignBlock> { todo!() }
    /// 交出所有权（安全）：之后只能由 from_raw 收回。
    pub fn into_raw(self) -> *mut core::ffi::c_void { todo!() }
    /// # Safety
    /// ptr 必须来自 into_raw，且尚未被收回。只在 §3 白名单第 13 项（-sys、shim）的蹦床里调用。
    pub unsafe fn from_raw(ptr: *mut core::ffi::c_void) -> ForeignBlock { todo!() }
}
impl Drop for ForeignBlock { fn drop(&mut self) { todo!() } }
```

### 2.10 `SMART_VAR` 一族

```rust
/// C++: SMART_VAR(T, v, args...) { body }（ob_smart_var.h:201-203）。RULEBOOK §2.4：一律放到堆上。
/// smart_var!(ret, v: T = init_expr, { body });
///   - ret 已是 Err：不分配，跳过 body；
///   - try_box_with(|| init_expr) 失败：ret = Err(OB_ALLOCATE_MEMORY_FAILED)，跳过 body；
///   - 否则 body 里 v: &mut T；块结束时 drop。
/// C++ 的数组形式 SMART_VAR(char[N], buf) 写成 smart_var!(ret, buf: [u8; N] = [0u8; N], { … })，sizeof(buf) → buf.len()。
#[macro_export]
macro_rules! smart_var { ($ret:ident, $v:ident : $t:ty = $init:expr, $body:block) => { /* … */ }; }
/// C++: SMART_VAR_INDEPENDENT（:205-207）：用内部的局部 ret，外层 ret 不受影响。
#[macro_export]
macro_rules! smart_var_independent { ($v:ident : $t:ty = $init:expr, $body:block) => { /* … */ }; }
/// C++: HEAP_VAR（:209-211）：与 smart_var! 相同。
#[macro_export]
macro_rules! heap_var { ($ret:ident, $v:ident : $t:ty = $init:expr, $body:block) => { /* … */ }; }
/// C++: SMART_VARS_2/3/4、HEAP_VARS_2/3/4（:213-258）：按顺序分配，前一个失败后面的不再分配，全部成功才执行 body。
#[macro_export]
macro_rules! smart_vars_2 { ($ret:ident, ($v1:ident : $t1:ty = $i1:expr), ($v2:ident : $t2:ty = $i2:expr), $body:block) => { /* … */ }; }
// smart_vars_3!、smart_vars_4!、heap_vars_2!、heap_vars_3!、heap_vars_4! 同形。
```

### 2.11 统计、视图与 `ObMemoryDump`

```rust
/// C++: lib::DEFAULT_MEMORY_BUDGET、set_memory_budget、get_memory_budget（alloc_func.h:34-36、alloc_func.cpp:34-45：
/// AtomicI64，store 用 Release、load 用 Acquire，照抄 C++ 的显式内存序）。
pub const DEFAULT_MEMORY_BUDGET: i64 = 1 << 30;
pub fn set_memory_budget(bytes: i64) { todo!() }
pub fn get_memory_budget() -> i64 { todo!() }
/// C++: get_memory_by_percentage（alloc_func.h:37-53），逐行移植，含溢出保护。
pub fn get_memory_by_percentage(memory: i64, percentage: i64) -> i64 { todo!() }

/// C++: common::ObLabelItem（ob_mod_define.h:427-…）。
#[derive(Clone, Copy, Default, Debug)]
pub struct ObLabelItem { pub hold_: i64, pub used_: i64, pub count_: i64, pub alloc_count_: i64, pub free_count_: i64 }

/// 取代 ObCtxAllocator::iter_label（ob_ctx_allocator.cpp:68-133）：对每个登记的预算、读数函数和 jemalloc 总量回调一次（S22）。
pub fn iter_label(f: &mut dyn FnMut(u64 /*ctx_id*/, &'static str /*label*/, &ObLabelItem) -> ObResult) -> ObResult { todo!() }
/// 取代 ObCtxAllocator::get_hold/get_used（经 ObMallocAllocator::get_ctx_allocator）和 ObMallocAllocator::get_ctx_hold。
/// 恒为 0：C++ jemalloc 后端下 ObMemoryMgr 的 ctx hold 只由 obmalloc 的 alloc_chunk 增加，永远是 0（视图调查第 1 节）。
/// 保持 0 是为了让读它做决策的代码与 C++ 一致：ob_ra_datum_store.cpp:1238-1252 用 `limit / 100 * 80 <= hold` 决定是否落盘。
pub fn get_ctx_hold(ctx_id: u64) -> i64 { todo!() }
pub fn get_ctx_used(ctx_id: u64) -> i64 { todo!() }
/// 软上限表的读数，初值 i64::MAX（ob_resource_mgr.cpp:35-39）。
pub fn get_ctx_limit(ctx_id: u64) -> i64 { todo!() }
/// C++: set_ctx_limit（alloc_func.cpp:105-121）和 ObCtxAllocator::set_limit：只记录软上限，供视图显示和上面的比较，不参与分配（与 C++ 一致）。
/// ctx_id 越界 → Err(OB_INVALID_ARGUMENT)。
pub fn set_ctx_limit(ctx_id: u64, limit: i64) -> ObResult { todo!() }
/// C++: get_allocator_memory_hold() / get_allocator_memory_hold(ctx_id) / get_allocator_cache_hold()（alloc_func.cpp:47-75）。
/// 恒为 0，理由同 get_ctx_hold（ObMemoryMgr 的 sum_hold_、cache_hold_ 在 jemalloc 下不增长）。
/// 带 ctx_id 的重载按 overloads.tsv 的规则改名（脚本生成，形如 get_allocator_memory_hold_uint64_t）。
pub fn get_allocator_memory_hold() -> i64 { todo!() }
pub fn get_allocator_memory_hold_uint64_t(ctx_id: u64) -> i64 { todo!() }
pub fn get_allocator_cache_hold() -> i64 { todo!() }
/// C++: ObMallocAllocator::get_total_limit()（ob_malloc_allocator.cpp:198-205）：ObMemoryMgr::limit_，初值 INT64_MAX，
/// 全仓没有 set_allocator_limit 的调用者，所以恒为 i64::MAX。ob_server_runtime_controller.cpp:989 用它算 memory_size_，照样复刻。
pub fn get_total_limit() -> i64 { todo!() }
/// C++: ObMallocAllocator::set_ctx_idle(ctx_id, size, reserve)：obmalloc 的空闲 chunk 预留，空操作，返回 Ok(())。
pub fn set_ctx_idle(ctx_id: u64, size: i64, reserve: bool) -> ObResult { todo!() }
/// C++: ObMallocAllocator::print_memory_usage() / print_ctx_memory_usage()：把 get_jemalloc_stats() 和登记的预算打到日志（日志不比较）。
pub fn print_memory_usage() { todo!() }
/// C++: ob_set_reserved_memory / ob_get_reserved_memory（alloc_func.cpp:87-103）：只记录数值。
pub fn ob_set_reserved_memory(bytes: i64) { todo!() }
pub fn ob_get_reserved_memory() -> i64 { todo!() }
/// C++: set_req_chunkmgr_parallel（alloc_func.cpp:134-149）：obmalloc 的参数，空操作，返回 Ok(())。
pub fn set_req_chunkmgr_parallel(ctx_id: u64, parallel: i32) -> ObResult { todo!() }
/// C++: get_unmanaged_memory_size（alloc_struct.h:661）：不再拦截 mmap，恒为 0（A.4 mem-view）。
pub fn get_unmanaged_memory_size() -> i64 { todo!() }
/// C++: enable_memleak_light_backtrace / is_memleak_light_backtrace_enabled（alloc_struct.h:663-664）：记录开关，不再有作用。
pub fn enable_memleak_light_backtrace(enable: bool) { todo!() }
pub fn is_memleak_light_backtrace_enabled() -> bool { todo!() }

/// C++: lib::ObMallocSampleMap 等（ob_malloc_sample_struct.h）：类型保留给 __all_virtual_malloc_sample_info，内容恒为空。
pub struct ObMallocSampleKey { pub ctx_id_: i64, pub label_: [u8; 16], pub bt_: [usize; 16] }
pub struct ObMallocSampleValue { pub alloc_count_: i64, pub alloc_bytes_: i64 }
pub type ObMallocSampleMap = crate::hash::ObHashMap<ObMallocSampleKey, ObMallocSampleValue>;  // 05 的类型
pub struct ObMallocSampleLimiter;
impl ObMallocSampleLimiter { pub fn set_interval(max_ratio: i32, min_ratio: i32) { todo!() } }  // 空操作

/// C++: DumpType、ObMemoryDumpTask（memory_dump.h:46-95）；指针字段改成 u64 地址值（只用来打印）。
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct DumpType(pub i32);
impl DumpType { pub const DUMP_CONTEXT: DumpType = DumpType(0); pub const DUMP_CHUNK: DumpType = DumpType(1); pub const STAT_LABEL: DumpType = DumpType(2); }
#[derive(Clone, Copy, Debug)]
pub struct ObMemoryDumpTask { pub type_: DumpType, pub dump_all_: bool, pub p_context_: u64, pub slot_idx_: i32, pub dump_ctx_: bool, pub ctx_id_: i64, pub p_chunk_: u64 }

/// C++: common::ObMemoryDump（memory_dump.h:136-235）。模块对象（0.5）：&self 的生命周期方法，永不释放。
pub struct ObMemoryDump { /* is_inited_: AtomicBool */ }
impl ObMemoryDump {
    pub fn instance() -> &'static ObMemoryDump { todo!() }
    /// 已初始化 → Err(OB_INIT_TWICE)（memory_dump.cpp:177-178）；不建上下文、不起线程（S23）。
    pub fn init(&self) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn is_inited(&self) -> bool { todo!() }
    /// 未初始化 → Err(OB_NOT_INIT)；否则 Ok(())，不做事（:246-257）。
    pub fn generate_mod_stat_task(&self) -> ObResult { todo!() }
    /// 未初始化 → Err(OB_NOT_INIT)；否则记一条 INFO "memory dump not supported" 后返回 Ok(())（:259-271）。
    pub fn request_dump(&self, task: &ObMemoryDumpTask) -> ObResult { todo!() }
    /// 清空 map 后返回 Ok(())（C++ 在 jemalloc 下得到的也是空表）。
    pub fn load_malloc_sample_map(&self, map: &mut ObMallocSampleMap) -> ObResult { todo!() }
}
```

### 2.12 C++ 构造 → Rust（总表）

本表细化 RULEBOOK §2.4 中与本模块有关的行，不新增第二种译法。"查询作用域""拥有型"按 `types.tsv` 的生命周期类别判定（§5.1）。

| C++ | Rust |
|---|---|
| `if (OB_ISNULL(buf = alloc.alloc(sizeof(T)))) { ret = OB_ALLOCATE_MEMORY_FAILED; } else { p = new (buf) T(..); }` | 查询作用域且 `T` 不需要 Drop：`arena.try_alloc(T::new(..))`；否则 `try_box(T::new(..))`。分配失败时 C++ 设的是别的错误码（如 `OB_ERR_UNEXPECTED`），照抄 C++（0.1.4） |
| `OB_NEW(T, label, args..)`（85 行） | `try_box(T::new(args..))`，标签删掉；结果为空的分支照 C++ 的错误码 |
| `OB_NEWx(T, pool, args..)`（526 行） | pool 的层级由清单第 11 类给出：请求或语句级 arena → `arena.try_alloc`（T 不需要 Drop）；其余 → `try_box` |
| `OB_NEW_ARRAY(T, pool, count)`（25 行） | `count <= 0` 时 C++ 返回 NULL（ob_malloc.h:666）照抄；否则查询作用域 `arena.try_slice_fill_default(count)`，拥有型 `try_vec` + 填充 |
| `OB_NEW_ALIGN32`/`OB_DELETE_ALIGN32` | 0 处使用，不提供 |
| `OB_DELETE(T, label, p)`（82 行）、`OB_DELETEx(T, pool, p)`（43 行）、`ob_delete(p)`（43 行）、`p->~T(); alloc.free(p);` | 交给 Drop：`p = None` 或离开作用域。需要显式析构的类型本来就不在 bump arena 里（0.7）。对模块对象的 `ob_delete` 不翻译（0.5 `module-leak`） |
| `op_alloc(T)` / `op_free(p)`（约 33 个文件） | `try_box(T::default())` / drop |
| `ob_malloc(n, attr)` / `ob_free(p)`（166 / 255 行） | `ob_malloc(n)` / drop；用户决定大小的缓冲按 0.7 必须可失败，`ob_malloc` 本身就是可失败的 |
| `ob_realloc(p, n, attr)`（2 处：`ob_charset.cpp:2486`、`ob_pl_allocator.cpp:109`） | 缓冲改成 `Vec<u8>`，增长用 `try_reserve` |
| `ob_malloc_align(align, n, attr)` / `ob_free_align(p)` / `alloc.alloc_align(n, align)` | `AlignedBuf::try_new_zeroed(n, align)` / drop |
| `ob_write_string(alloc, src, dst)`（约 590 处） | 查询作用域：`dst = arena.try_str(src)`；拥有型：`dst = try_bytes(src)`。C++ 失败时把 `dst` 置空（ob_string.h:812-816），Err 分支照写 `dst = &[]` / `Box::default()` |
| `ob_write_string(alloc, src, dst, true)` | `arena.try_str_c(src)`；交给 C 库时由 09 的蹦床拼 `&CStr`（§2.5） |
| `ob_write_string(alloc, src, dst, len, padding, c_style)`、`ob_dup_cstring`、`ob_sub_str`、`ob_strip_space`、`ob_simple_low_to_up`（约 19 处） | 06 的同名辅助函数，内部用 `Arena::alloc` |
| `deep_copy(alloc, ..)` 一族（约 1,100 行） | 拥有型：`Clone` 或手写 `assign`，必要时先 `try_reserve`；查询作用域：类型自己的 `deep_copy_in(&self, arena) -> ObResult<T<'q>>`，字符串经 `arena.try_str`；`ObObj` 按 §2.12 的两个函数。拷进预分配平铺缓冲的形式（`deep_copy(src, buf, size, pos)`，schema 的扁平拷贝）改成拥有型的 `Clone`（0.7） |
| `ObArenaAllocator` 局部变量 | 块内 `let arena = Arena::new();`（构造时传了页大小的用 `with_page_size`）；`ObArenaAllocator a(ctx->get_malloc_allocator())` → `mem_ctx.get_malloc_allocator().new_arena(OB_MALLOC_NORMAL_BLOCK_SIZE)` |
| `ObArenaAllocator` 成员（382 个） | 层级由清单第 11 类给出：请求或语句级 → 调用方传入的 `&'q Arena`；算子批次级 → 帧模块的批次 arena（0.6）；跨调用存活 → 0.7 (b) 的拥有型分块缓冲（§2.4） |
| `allocator.reset()`/`clear()` / `reuse()` / `reset_remain_one_page()` | `arena.reset()` / `arena.reuse()` / `arena.reset_remain_one_page()`，都要 `&mut` |
| `ObSafeArenaAllocator`（8 个文件）、`ObSafeArena`（2 个）、`ctx->get_safe_arena_allocator()` | `Latch<Arena>`，由拥有者持有；C++ 包着别人的 arena（`ObSafeArenaAllocator s(tmp)`）时改成拥有那个 arena |
| `ObIAllocator &`/`*` 参数（签名 7,147 个）、借用分配器的成员（`*` 309 个、`&` 200 个） | 查询作用域：`arena: &'q Arena`；拥有型：删掉（§2.4） |
| `ObMemAttr`、`ObLabel`、`lib::ObMemAttr(..)` 实参、`ObModIds::OB_*` 标签、`set_label`、`set_attr`、`set_ctx_id` | 删掉（0.7）。唯一例外：ctx 是 `ObCtxIds::WORK_AREA` 且 C++ 会经 `resolve_memory_usage_tracker` 计账（MemoryContext 的 arena 页、`ObArenaAllocator(label, page_size, WORK_AREA)`、`ObFIFOAllocator`）→ 改成挂工作区预算（S14）。会被序列化的 ctx_id 字段（如 `ObChunkDatumStore::ctx_id_`）保留为 `u64` |
| `ObMallocHookAttrGuard g(attr);`（约 98 行、34 个文件） | 删掉整行：jemalloc 下它不影响任何分配（alloc_struct.h:560-573 的属性只在 obmalloc 的 hook 里读） |
| `ObMallocCallbackGuard`、`ObLightBacktraceGuard` | 删掉整行 |
| `lib::MemoryContext mem_context_` 成员 | 请求和语句级 → `Option<MemCtx>`；长生命周期、跨线程共享的（带 `ALLOC_THREAD_SAFE` 的 8 处、计划缓存、向量）→ 不用 MemCtx：要 arena 的用 `Latch<Arena>`，要 hold 的用 `Arc<MemBudget>`，其余拥有型（0.7） |
| `x->CREATE_CONTEXT(ctx, param)`（70 处，其中 `CURRENT_CONTEXT->` 52 处） | `mem_ctx.create_context(&mut ctx, &param)`；`mem_ctx` 是显式传入的当前 MemCtx（Q2） |
| `ROOT_CONTEXT->CREATE_CONTEXT(ctx, param)`（18 处中的多数） | `ctx = Some(MemCtx::new(&param))` |
| `DESTROY_CONTEXT(ctx)`（78 处） | `ctx = None` |
| `CREATE_WITH_TEMP_CONTEXT(param) { body }`（21 处） | (a) worker 和请求入口（`ob_th_worker.cpp:244`、`obmp_query.cpp:475`、`obmp_stmt_execute.cpp:1764`、`ob_req_queue_thread.cpp:80`）→ `let mem_ctx = MemCtx::new(&param);`，body 里显式传 `&mem_ctx`；(b) SQL 编译期、建了 `ObRawExprFactory` 的（`ob_transform_rule.cpp:379`、`ob_transform_temp_table.cpp:2337/2523/2598`、`ob_transform_late_materialization.cpp:444`）→ `qa.scope()`（07）；(c) 其余 → 块内局部 `Arena`，块内代码需要 MemCtx（建子上下文、读 hold，如 `ob_geo_utils.cpp:723` 的 `ObGeoEvalCtx(CURRENT_CONTEXT, srs)`）时用块内局部 `MemCtx::new(&param)` |
| `WITH_CONTEXT(ctx) { body }`（28 处） | body 里把 `CURRENT_CONTEXT` 换成 `ctx`。`ctx` 为空时 C++ 令 `ret = OB_INVALID_ARGUMENT` 并跳过 body（context.h:810-811），照写 |
| `CURRENT_CONTEXT->get_arena_allocator()`（31 处）/ `get_malloc_allocator()`（10 处）/ 其他 | 显式传入的 `mem_ctx` 上的同名方法（Q2）；非请求线程上没有 MemCtx 的，在任务入口建局部 `MemCtx::new` 后向下传 |
| 绑定在 `CURRENT_CONTEXT` 上的容器（`ModulePageAllocator, true>` 563 处、`ob_array.h:484-497`） | 全局堆（05） |
| `CHECK_MEM_STATUS()` / `TRY_CHECK_MEM_STATUS(n)` | 手里有 MemCtx：`mem_ctx.check_status()` / `mem_ctx.try_check_status(n)`（RULEBOOK §2.4）；没有：`check_mem_status()` / `try_check_mem_status(n)`。两者语义相同（S12、Q1） |
| `MEM_TRACKER_GUARD(CURRENT_CONTEXT);`（1 处，`ob_th_worker.cpp:245`） | `ObMemTrackerGuard::with(&mem_ctx, || { …块的其余部分… })` |
| `common::MemoryUsageTracker x_;` | `Arc<MemBudget>`（在槽位或模块对象里的用 `static` 加 `register_static`） |
| `set_memory_usage_tracker_resolver(ctx, f)` / `resolve_memory_usage_tracker(ctx)` | 不提供：唯一的注册方（`ob_sql_memory_manager.cpp:431-432`）改成在建算子上下文时显式挂工作区预算（S14） |
| `arena.set_memory_tracker(&tracker)` | `arena.set_memory_tracker(Some(tracker.clone()))` |
| `ObBlockAllocMgr x_;`、`ObBlockAllocMgr(limit)` | `Arc<MemBudget>`，`set_limit(limit)`；`alloc_block`/`free_block` → `alloc_block` / drop |
| `ObSliceAlloc`、`ObVSliceAlloc`、`ObSmallAllocator`、`ObFIFOAllocator`、`ObLfFIFOAllocator`、`ObConcurrentFIFOAllocator`、`ObPooledAllocator`、`ObPool` 成员 | 删掉池；对象改 `try_box`，字节改 `TrackedBuf`/`Box<[u8]>`。池带上限的（`set_total_limit`、`limit_num`、`ObBlockAllocMgr`）保留一个 `Arc<MemBudget>`，在 C++ 调池的 `alloc` 的位置先 `try_alloc(size)`、在 `free` 的位置 `free(size)`（S13） |
| `SMART_VAR(T, v, args..) { body }`（484 行）、`SMART_VAR_INDEPENDENT`、`HEAP_VAR`（`HEAP_VAR*` 253 行）、`SMART_VARS_*`（42 行） | `smart_var!` / `smart_var_independent!` / `heap_var!` / `smart_vars_2!` 等（§2.10） |
| `ObMallocAllocator::get_instance()->get_ctx_allocator(i)->set_limit(l)` | `set_ctx_limit(i, l)` |
| `ObMallocAllocator::get_instance()` 作为页分配器实参 | 删掉实参（随池删除） |
| `lib::get_memory_budget()` 等 `alloc_func.h` 的函数 | 同名函数（§2.11） |
| zstd `ob_zstd_malloc`/`ob_zstd_free`、zlib `ob_zlib_alloc`/`ob_zlib_free`、vsag `Allocate`/`Deallocate`/`Reallocate` | 安全侧用 `ForeignBlock`；蹦床在 09（S20） |

## 3. 语义

每条给出 C++ 的证据。与 C++ 不同的地方只有点名的 A.4 偏差，以及 S24 列出的几处需要修订的偏差（Q3）。

**S1 arena 分配失败与零字节**
- `Arena::alloc`/`alloc_aligned`（C++ `ObArenaAllocator::alloc` → `_alloc_aligned`）：`len <= 0` 返回 `Err(OB_ALLOCATE_MEMORY_FAILED)`，并且不分配页：`sz > 0` 的判断在 `ensure_cur_page()` 之前（page_arena.h:618-626）。
- `Arena::alloc_unaligned`（C++ `PageArena::alloc` → `_alloc`）：`len <= 0` 同样失败，但空 arena 会先建出第一页，因为 `_alloc` 先 `ensure_cur_page()` 再判 `sz > 0`（:562-565，复核 C25）。统计按此记一页。
- 调用方照 C++ 报 4013（0.7），这就是 RULEBOOK 要保留的 `alloc(0)` 行为（行为 F15）。
- `try_str`/`try_str_c`：`src` 为空返回 `Ok(&[])`，不分配，与 `ob_write_string` 一致（ob_string.h:811-812）。所以只有原始字节接口会因 0 字节失败。
- `try_alloc::<T>` 对零大小类型不分配、不记账（C++ 没有零大小的类型）。

**S2 对齐**
- `alloc`、`try_str`、`try_alloc::<T>`（`align_of::<T>() <= 16` 时）按 16 字节对齐：`ObArenaAllocator::alloc(sz)` 调的是 `alloc_aligned(sz)`，缺省 16（page_arena.h:996、:618）。
- `align_of::<T>() > 16` 时按 T 的对齐。

**S3 PageArena 口径的统计**
- 真实内存来自 bumpalo（块大小按倍数增长）。`used()`/`total()`/`pages()` 不读 bumpalo，而是对请求序列按 PageArena 的算法记账，所以同样的 (操作, 大小, 对齐) 序列得到与 C++ 相同的数。
- 理由：这三个数参与行为决策：
  - 算子落盘：`ob_sort_op_impl.cpp:1027` 用 `mem_context_->used()`，`ob_hash_join_op.h:973` 用 `used()`，`ob_hash_groupby_op.h:359-368` 用 `hold()`；
  - 查询范围退化：`ob_expr_range_converter.cpp:111-113` 用 `allocator_.used() - mem_used_` 与 `range_optimizer_max_mem_size` 比较，超了就退回全范围（`ob_range_graph_generator.cpp:62-71`）；
  - 表达式临时 arena 的回收（`ob_expr.h:260-271`，07）；11049（S12）。
- 状态：`page_size`（含 32 字节页头的原始页大小，缺省 `OB_MALLOC_NORMAL_BLOCK_SIZE` = 7936）、`page_limit`（建第一页时 = `page_size - 32`，:415）、普通页数、当前页下标（无页为 −1）、当前页已用字节、大页数、大页字节、`used`。`total = 普通页数 × page_size + 大页字节`，`pages = 普通页数 + 大页数`（:344-363）。页头 32 字节：`magic_`、`next_page_`、`alloc_end_`、`page_end_` 各 8 字节（:198-206）。
- 对齐分配（`alloc`、`alloc_aligned`、`try_*`、`try_str`、ArenaVec 的增长；C++ `_alloc_aligned`，:618-659）：
  1. `sz <= 0` 或 `sz > i64::MAX - (align - 1)` → 失败，状态不变；
  2. `max_adj = sz + align - 1`；没有当前页就建一页（`ensure_cur_page`，:405-420）；
  3. `pad = (align - 当前页已用 % align) % align`；`sz + pad` 放得下就留在当前页，`used += sz + pad`（:271-290）；
  4. 否则若 `max_adj <= page_limit`：换到下一页（已有就重用，没有就新建并接在末尾，:372-390），在新页起点分配，`used += sz`；
  5. 否则建大页：原始大小 `max_adj + 32`，`used += sz`（:642-652、:432-442）。
  - 对齐超过 16 时，C++ 的填充取决于 jemalloc 返回的页地址；这里假定页的载荷起点按 `align` 对齐，只对 `align <= 16` 精确。
- 不对齐分配（`alloc_unaligned`，C++ `_alloc`，:559-590）：
  - `sz + 32 <= page_size` 时先 `ensure_cur_page`，`sz > 0` 才分配：放得下留在当前页；否则换页（同上）；`used += sz`；
  - `sz + 32 > page_size` 时直接建大页，原始大小 `sz + 32`，`used += sz`。
- 失败的真实分配不改变统计；统计只在 bumpalo 成功之后更新。

**S4 reset、reuse、reset_remain_one_page**
- `reset()`：页数、大页、`used` 全部归零，当前页置空（C++ `free()`，:721-740）。真实内存：换一个新的 `Bump`，旧块全部还给全局分配器（C++ 也是全部释放）。
- `reuse()`：丢掉大页；有普通页就回到第 0 页、已用 0；`used = 0`（:945-949、:928-943、:444-465）。真实内存：`Bump::reset()`（保留最后一块）。
- `reset_remain_one_page()`：只留第一张普通页（没有普通页则清空），`used = 0`（:743-778）。真实内存：`Bump::reset()`。
- 这三个操作都要 `&mut self`，编译器保证此时没有指向 arena 的借用。C++ 在这里留下的悬垂指针在 Rust 里编译不过。

**S5 realloc**
- 当前页为空（从未分配，或 `reset` 之后）→ 失败（C++ 的 `cur_page_ == NULL` 分支返回 NULL，:674）。
- `old` 是当前页上最近一次分配、`old.len()` 等于它的长度、且 `起点 + new_len < page_limit`（严格小于，:680）时，C++ 原地扩展。统计上只把当前页已用改为 `起点 + new_len`，`used` 不变：C++ 在这条路径上不更新 `used_`（:681），照样复刻。
- 否则按不对齐分配 `new_len`，`used += new_len`，拷贝 `min(old, new)` 字节（:684-687）。
- 真实内存一律新分配加拷贝。

**S6 arena 的三种计账方式**（C++ 有三种互不相同的机制，照样区分）
- `set_memory_tracker`：C++ `ObArenaAllocator::tracker_`（:1049-1079）。`alloc`/`alloc_aligned`/`realloc`/`reset`/`reset_remain_one_page`/Drop 之后按 `total()` 的增量调整（`adjust`）；`reuse()` 不调整（:1036 没有 `update_tracker`）。这是原版的缺陷：reuse 释放的大页在 tracker 里一直算到下次 reset。照样复刻，在实现里注明 `BUG(port): ObArenaAllocator::reuse 不更新 tracker`。使用者只有 tx data 的两个 arena（`ob_tx_data_table.cpp:83`、`ob_tx_data_memtable.cpp:115`）。
- `set_page_tracker`：C++ `ModulePageAllocator` 经 `resolve_memory_usage_tracker` 走 `TrackedAllocator`（:116-181、ob_malloc.h:256-298）。每建一页计原始页大小（`alloc(size)`），每释放一页扣回，包括 `reuse()` 释放的大页。
- `MemoryContextMalloc::new_arena`：页来自上下文的 malloc 分配器，每页按 `usable_size(原始页大小)` 记到该上下文的 malloc 计数（ob_malloc.h:488-503），释放时扣回。

**S7 ArenaVec**
- 元素不能需要 Drop（编译期断言，审查 S-07）。
- 增长：`push_back` 容量不足时扩到 2 × capacity，最少 1，与 `ObArray` 相同（ob_array.h:610-614）。每次增长在统计上记一次新的对齐分配（C++ 在 arena 里增长时新分配、旧缓冲作废）。
- `into_slice` 之后不能再增长；`&'q [T]` 跟随 arena 的借用。
- `ObFixedArray` 的"容量固定、超出返回 `OB_SIZE_OVERFLOW`"（ob_fixed_array.h:65-78）由 05 在 ArenaVec 之上实现。

**S8 `Latch<Arena>`**
- 每次分配持锁，锁的粒度与 C++ 的 `ObSpinLock` 相同（page_arena.h:1090-1099）。
- `used()`/`total()` 也取锁。C++ 不取锁直接读（:1115-1122），那是数据竞争；这里是替换后的基础设施，不需要标记。
- 返回的引用活到 `&self` 的借用结束；`reset`/`reuse` 经 `get_mut()`，要 `&mut`（§4 U5）。

**S9 拥有型分配**
- `try_box`：零大小类型直接 `Box::new`；否则用全局分配器分配 `Layout::new::<T>()`，空指针 → `Err(OB_ALLOCATE_MEMORY_FAILED)`。
- `try_box_with`：先分配，再写入 `f()` 的结果；`f` 在分配之后才调用。SMART_VAR 的大对象（C++ 阈值：超过 128 KB 直接上堆，ob_smart_var.h:35、:192）一律用它，免得先在 224 KB 的原生栈上构造（0.8）。
- `try_bytes`：`src` 为空返回空 `Box`，不分配。
- `ob_malloc`：`nbyte <= 0` → `Err(OB_ALLOCATE_MEMORY_FAILED)`，并打 WARN "allocate memory fail"（C++ 在这种情况下指针保持为空，同样打 WARN，ob_malloc.h:118-125）。
- `usable_size(n) = je_nallocx(n, 0)`，等于 C++ jemalloc 后端下的 `ob_malloc_usable_size`（ob_malloc.cpp:216-233）：同一份 jemalloc，同样的大小档。
- `AlignedBuf`：实际对齐 `max(align, 16)`；`len == 0` 也成功（C++ 的 `alloc_align` 总会分配 `real_align + size + 8` 字节，ob_iallocator.h:39-52）。

**S10 分配失败策略**（0.7、§2.1）
- arena 分配一律 `try_*`；容器的 `push_back` 内部用 `try_reserve`；大小取决于用户数据或超过 64 KB 的缓冲用 `try_reserve`/`try_vec`/`try_bytes*`/`ob_malloc`。
- 小的固定大小分配允许不可失败（`Box::new`、`vec![]`、`Arc::new`）：在 `panic = "abort"` 下 OOM 就是 abort（A.4 `oom-abort`）。
- 每个调用点照 C++ 给出的错误码：例如 `ob_load_data_file_reader.cpp:72-74` 在 `OB_NEW` 返回空时设的是 `OB_ERR_UNEXPECTED`，不是 4013（0.1.4）。
- 容错转换不得吞掉内存错误，判断用 `e.is_unrecoverable()`（§2.1，04）。

**S11 MemCtx 的口径**（C++ jemalloc 分支）
- `arena_hold = arena.total()`，`arena_used = arena.used()`（context.h:378-385）。
- `malloc_hold = malloc_used` = 经 `get_malloc_allocator()` 分配、尚未释放的内存按 jemalloc 实际大小之和（`MemoryContextMalloc`，ob_malloc.h:461-503；context.h:366-377 在 jemalloc 下取 `freeable_alloc_->total()`/`used()`，二者相等）。
- `hold = malloc_hold + arena_hold`，`used = malloc_used + arena_used`（:386-395）。
- MemCtx 的 arena 的页不算进 malloc_*：C++ 的 arena 建在普通 `ObMalloc` 上，不是 `MemoryContextMalloc`（context.h:484 和 :437-438）。
- `tree_mem_hold()`：
  - 自己带 `ADD_CHILD_THREAD_SAFE` 时恒为 0（C++ 的 `if (!tree_node_.with_lock_)`，context.h:603）；
  - 否则 = 自己的 hold + 所有子上下文的 `tree_mem_hold()`（:600-613）。
  - 实现：每个 MemCtx 有一个树预算，arena 的页变化（按页，同 `set_page_tracker`）和 malloc 的增减都记进去，并沿父链加到祖先；带 `ADD_CHILD_THREAD_SAFE` 的子上下文不挂父链，所以它和它的整棵子树对祖先贡献 0，与 C++ 相同。
- `MemCtx::new` 建出的上下文是根，不挂到任何树上：C++ 的临时上下文传 `parent = nullptr`（context.h:843，`_S<CREATE>`），根上下文的子节点也因根带 `ADD_CHILD_THREAD_SAFE` 而贡献 0（context.cpp:35）。所以嵌套的请求上下文（`obmp_query.cpp:475` 套在 `ob_th_worker.cpp:244` 里面）不计入外层的树，与 C++ 相同。
- 页大小：`ContextParam` 缺省 `OB_MALLOC_NORMAL_BLOCK_SIZE`（context.h:208），传给 arena（:437-438）。请求入口用的值照抄：`OB_MALLOC_REQ_NORMAL_BLOCK_SIZE`（ob_th_worker.cpp:241、obmp_query.cpp:472），`OB_MALLOC_BIG_BLOCK_SIZE`，mini 模式下用 `MIDDLE`（obmp_stmt_execute.cpp:1761-1762）。
- 属性：
  - `ALLOC_THREAD_SAFE`、`USE_TL_PAGE_OPTIONAL` 在 jemalloc 下不影响 arena（`init_malloc_alloc` 忽略它们，context.h:480-488），MemCtx 只记录，不改变行为；
  - `RETURN_MALLOC_DEFAULT` 只出现在长生命周期上下文上（`thread.cpp:378`、`ob_vector_allocator.cpp:121,191`、`mds_service.cpp:416`、`ob_ps_cache.cpp:111`），这些不映射到 MemCtx（§2.12）。
  - `thread.cpp:368-389` 给每个线程建的 "ThreadRoot" 上下文不翻译：线程入口不建 MemCtx（CURRENT_CONTEXT 的去处见 §2.12）。
- C++ 的 `ADD_CHILD_THREAD_SAFE` 还意味着子节点链表加自旋锁（context.h:139-189）。MemCtx 是 `!Sync`，子上下文由拥有者持有，不需要这把锁。
- MemCtx 的 Drop 就是 `DESTROY_CONTEXT`：arena 释放，树预算扣回自己的全部数额。C++ 销毁父上下文时会递归销毁子上下文（context.h:573-599）；Rust 的子上下文由各自的拥有者持有，父先于子释放不会悬垂，子上下文的内存晚一些释放。

**S12 单查询上限 11049**（`ob_memory_tracker.cpp:30-65`）
- tracker 的状态：`cache_mem_limit_`（i64）、`check_status_times_`（u16，回绕）、`try_check_tick_`（u16，回绕）、受保护上下文的树预算。`ObMemTrackerGuard::with` 新建时全部清零（C++ guard 的构造和析构各清零一次，ob_memory_tracker.h:52-60）。
- `update_mem_limit()`：`pct = config::query_memory_limit_percentage()`（缺省 32，`rust/config/parameters.yaml:2083-2089`）；`cache_mem_limit_ = pct > 0 ? get_memory_budget() / 100 * pct : 0`。先除后乘，照抄。
- `check_status()`：
  1. 本线程没有装入 tracker → `Ok(())`；
  2. `hold = 树预算.used()`；`check_status_times_ += 1`（u16 回绕）；
  3. `cache_mem_limit_ == 0` 或 `check_status_times_ % 512 == 0` → `update_mem_limit()`；
  4. `cache_mem_limit_ > 0 && hold >= cache_mem_limit_` → WARN "Exceeded memory usage limit"，`log_user_error!(OB_EXCEED_QUERY_MEM_LIMIT, cache_mem_limit_, hold)`，返回 `Err(OB_EXCEED_QUERY_MEM_LIMIT)`。
  - 报错文本由 `ob_errno.def:1880` 生成："Exceed query memory limit (mem_limit=%ld, mem_hold=%ld),  please check whether the query_memory_limit_percentage configuration item is reasonable."（中间两个空格照抄），SQLSTATE HY000，错误码 11049。
- `try_check_status(n)`：`try_check_tick_ += 1`（u16 回绕）；`tick % n == 0` 时调 `check_status()`，否则 `Ok(())`。u16 回绕不影响节奏：65536 能被 1024 和 32 整除。
- 计数用 `Relaxed` 原子量：C++ 是线程局部的普通整数，这里只有装入它的那个线程会读写。
- 只有装入 tracker 的线程会触发 11049。C++ 只有 `ob_th_worker.cpp:245` 装 guard；内部 SQL 的 guard 被注释掉了（`ob_inner_sql_connection.cpp:682`）；其他线程的 `CHECK_MEM_STATUS()` 恒为成功。Rust 相同。
- 9 个检查点：
  1. `ob_th_worker.cpp:245`：装入 guard；
  2. `ob_th_worker.cpp:333`：`ObThWorker::check_status()`，即 worker 的 check_status 钩子（0.5），经 `THIS_WORKER.check_status()` 在各处调用；
  3. `ob_exec_context.cpp:679`：`ObExecContext::check_status()`；
  4. `ob_transform_rule.cpp:287`：`TRY_CHECK_MEM_STATUS(32)`；
  5. `ob_select_resolver.cpp:1006`：`TRY_CHECK_MEM_STATUS(32)`；
  6. `ob_raw_expr.h:5074-5079`：`ObRawExprFactory` 每建 1024 个表达式（工厂自己的计数，:4999）调一次 `CHECK_MEM_STATUS()`，属 07；
  7. `ob_memory_tracker_wrapper.cpp:20-23`：`check_mem_status()`，目前没有 C 调用者；
  8. `ob_memory_tracker_wrapper.cpp:25-28`：`try_check_mem_status(n)`；
  9. `parse_node.c:183-187`：解析器的 `new_node` 每 1024 个节点调 8。失败时打印 "Exceeded memory usage limit" 并返回空节点，由解析器按内存不足处理（09）。
- 计数口径（A.4 `query-mem-limit`，已接受）：树预算计入 MemCtx 的 arena 页、经 `get_malloc_allocator()` 的分配、子上下文；不计全局堆上的查询期容器（RULEBOOK 0.7）。C++ 还计入了 563 处惰性绑定到当前上下文的数组（ob_array.h:484-497），以及 SMART_VAR 的堆分配（`smart_alloc` 走 `CURRENT_CONTEXT` 的 malloc 分配器，ob_smart_var.cpp:30-36）。

**S13 MemBudget**
- `alloc`/`free`/`adjust` 的条件照 MemoryUsageTracker：`size > 0` 才动、`delta != 0` 才动（ob_malloc.h:189-208）。
- 所有读改写和读都用 `SeqCst`，与 `ATOMIC_*` 一致（0.8）。C++ 的 MemoryUsageTracker 用 relaxed（:192-211）；这里更强，单线程下可观察行为相同。弱化只在对齐之后、有证据时做（`PERF(port)`）。
- 有父预算时，增减按从近到远的顺序逐个加到祖先上。各层之间不是一个原子操作，所以读祖先可能短暂看到部分增量。C++ 的 `tree_mem_hold` 是不加锁的遍历求和，本来也不是快照。`bcas`、`store`、`reset`、`try_alloc` 只用于无父预算（debug 断言）。
- `try_alloc(size)` 照抄 `ObBlockAllocMgr::alloc_block` 的计账（ob_block_alloc_mgr.h:38-50）：`after = aaf(size)`；`after > limit` 时 `aaf(-size)` 并返回 false，每秒最多打一条 WARN "block alloc over limit, limit=%ld alloc_size=%ld"（`reach_time_interval!(1_000_000)`，04）。
- `alloc_block` 在 `try_alloc` 成功后分配，分配失败时把 size 减回（:49-50）。
- `can_alloc_block(size) = (limit - hold) > size`（:57-59），两次独立的 load，不是快照，与 C++ 一样。
- **池的上限**：C++ 的池按块向 `ObBlockAllocMgr` 计费（块大小是池的页大小）。Rust 删掉池，在原来调用池 `alloc(sz)` 的位置计 `sz`、在 `free` 的位置扣回。计费粒度从块变成对象，同样的上限会晚一些触发（Q3）。

**S14 工作区（35 个文件）**
- 落盘决策的代码（`ObSqlMemMgrProcessor`、`ObSqlMemoryManager`）照常翻译，不归本模块。本模块要保证它们读到的数与 C++ 同口径：
  - 算子上下文的 `used()`/`hold()`（S3、S11）；
  - 行存储的块经 `get_malloc_allocator().alloc(size)` 按实际大小计入（`ObChunkDatumStore::alloc_blk_mem`，ob_chunk_datum_store.cpp:558-580 用的是算子上下文的 malloc 分配器，如 ob_sort_op_impl.cpp:745-764）。
- `ObSqlMemoryManager::workarea_managed_tracker_`（ob_sql_memory_manager.h:684-686、:786）→ `Arc<MemBudget>`，名字 "SqlWorkArea"、ctx `WORK_AREA`，登记到视图。
- 它在 `get_max_work_area_size` 里决定自动内存管理的总量：`work_area_max_size = get_memory_budget() / 100 * pctg`，`max_wa_memory_size = MAX(work_area_max_size - MAX(workarea_managed_used - MAX(active_profile_used, 0), 0), 1)`（ob_sql_memory_manager.cpp:808-818）。`workarea_size_policy` 缺省 AUTO（`parameters.yaml:762-768`），所以缺省配置下这个数参与每个算子的落盘边界。
- C++ 往这个 tracker 里记的，只有经 `ModulePageAllocator`/`ObFIFOAllocator` 且 ctx 为 WORK_AREA 的页（resolver 注册在 ob_sql_memory_manager.cpp:37-47、:431-432；`ModulePageAllocator` 在 page_arena.h:116-181；`ObFIFOAllocator` 在 ob_fifo_allocator.cpp:75-76）。Rust 的对应：
  - 算子上下文的 arena 页：C++ 在 `set_mem_attr(…, ObCtxIds::WORK_AREA)` 的上下文里（如 ob_sort_op_impl.cpp:740-742）→ `ContextParam::set_memory_tracker(工作区预算)`，按页计原始页大小，与 C++ 相同；
  - `ObArenaAllocator(label, page_size, ObCtxIds::WORK_AREA)`（如 ob_expand_vec_op.h:82）→ `Arena::with_page_size(ps)` 加 `set_page_tracker(Some(工作区预算))`；
  - `ObFIFOAllocator` 带 WORK_AREA 属性 → 它的分配改为 `TrackedBuf::try_new(size, Some(&工作区预算))`；
  - `ModulePageAllocator("…", ObCtxIds::WORK_AREA)` 承载的容器（如 ob_sort_op_impl.cpp:562、:582 的 `page_allocator_`）→ 需要 05 提供带 tracker 的容器，否则不计入（Q3）。
- `ObCtxIds::WORK_AREA` 的出现：83 行、31 个文件（`grep -rn 'ObCtxIds::WORK_AREA\|WORK_AREA)' src`，不含 ob_mod_define.h）。清单第 11 类逐处标出属于上面哪一种（Q2）。

**S15 向量（`vector_memory_limit`）**——代码照常翻译，本模块给出它要用的原语：
- `ObVectorAllocator::all_used_mem_`（ob_vector_allocator.h:67）→ `Arc<MemBudget>`（"VectorAll"，ctx `VECTOR_CTX_ID`）。
- `ObVectorMemContext::alloc(size)`（ob_vector_allocator.cpp:131-156），逐行翻译成：
  1. 当 `check_cnt_ >= 20` 或 `size >= 2 MB` 时检查：`limit = GMEMCONF.get_vector_memory_limit()`、`hold = vector_allocator.hold()`；
  2. `limit <= 0 || hold >= limit || size > limit - hold` → `check_cnt_ = 20`，WARN，`OB_ERR_VSAG_MEM_LIMIT_EXCEEDED`（-7603）只记在日志里，函数返回空（C++ 返回 NULL）；
  3. 否则 `check_cnt_ = 0`；
  4. 成功分配后 `check_cnt_ += 1`。
  - `check_cnt_` 是 `AtomicU32`（`ATOMIC_*`，§2.7 一律 SeqCst）。
- `hold()` 在 jemalloc 下 = `used()` = `all_used_mem_ + rb_mem_used`（:29-45）。
- `vector_memory_limit = 0` 不是"不限"，而是 50% 的有效内存（`ob_server_config.cpp:141-148`、`parameters.yaml:152-158`）；裁判有 18 个用例这样设置。
- vsag 的 `Allocate(size)`（:54-70）：`size == 0` → 空；否则 `ForeignBlock::try_new(size)`（总长 `16 + size`）→ 经上面的检查 → `all_used_mem_.aaf(total_size)` → `into_raw`。`Deallocate`：`from_raw` → `saf(total_size)` → drop。`Reallocate`（:83-109）用 `try_grow`。
- C++ 直接经 `ObVectorAllocator::alloc` 的分配不记入 `all_used_mem_`（只有 Vsag/Ivf 上下文记），照样复刻。

**S16 memstore、KV cache、tx data**
- memstore 的 FIFO arena 和限流计量在 `storage_core`（0.9，08）。KV cache 的 `store_size_` 预留在 `share::cache`（08）：CAS 循环的条件是 `cache_size >= 0 && allocation_size > 0 && cache_limit > 0 && cache_size <= cache_limit && allocation_size <= cache_limit - cache_size`（ob_kvcache_store.h:119-126）；超限时串行同步清洗，仍不够就返回 4013（ob_kvcache_store.cpp:915-977）。
- 这两处按 §2.7 用各自的原子字段逐行移植，**不改用 MemBudget**，只调用 `MemBudget::register_probe` 给视图供数。
- tx data 的两个 `MemoryUsageTracker`（ob_shared_memory_allocator_mgr.h:74-85、:97-98）→ `Arc<MemBudget>`，arena 端用 `set_memory_tracker`（S6）。

**S17 全局分配器 `ObMalloc`**
- `alloc`：`align <= 16 && align <= size` → `je_malloc(size)`；否则 `je_mallocx(size, MALLOCX_LG_ALIGN(log2(align)))`。
- `alloc_zeroed`：同样的条件下 `je_calloc(1, size)`，否则加 `MALLOCX_ZERO`。
- `dealloc`：`je_sdallocx(ptr, size, flags)`，flags 与分配时相同。
- `realloc`：同样的条件下 `je_realloc`，否则 `je_rallocx`。
- 不打日志、不计数、不加锁、不 panic、不访问线程局部（0.7、审查 S-23）。C++ 默认后端同样是直通：`ob_malloc` 直接调 `je_malloc`（ob_malloc.h:118-125，复核 C11）。
- 共存期 Rust 和 C++ 用同一份 jemalloc（C++ 由 CMake 链接，Rust 由链接器解析到同一组 `je_*` 符号），跨语言释放也安全。
- 不设硬上限，与基线一致（`ob_resource_mgr.cpp:30-40` 的上限从未设置，内存备忘 §2.2）。

**S18 jemalloc 的配置与平台**
- `je_malloc_conf = "background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:0"`，与 ob_malloc.cpp:39-42 逐字相同；只在 Rust 主程序里定义。
- `restore_malloc_backend_after_fork()`：jemalloc 后端时写 `mallctl("background_thread", true)`，返回是否成功（ob_malloc.cpp:170-175、ob_jemalloc.h:116-127）。`main` 在 daemon 化之后调用（main.cpp:738）。
- macOS：
  - `main` 开头调用 `configure_darwin_malloc_zone`（main.cpp:647-654）：找名为 "jemalloc_zone" 的 zone 并提到第一位，失败时返回 `OB_ERR_UNEXPECTED`；
  - 链接参数 `-Wl,-u,_je_zone_register`（src/observer/CMakeLists.txt:112-116）由 Rust 主程序的 build.rs 同样给出。
- 后端环境变量：
  - 空或 "jemalloc" → JEMALLOC；
  - "obmalloc" → 记一次 WARN，仍用 JEMALLOC（A.4 `malloc-backend`）；
  - 其他值 → UNKNOWN，`get_ob_malloc_backend()` 照实返回 UNKNOWN：
    - Linux：`init_malloc_hook()` 往 stderr 写 "seekdb: invalid malloc backend '<值>'; MALLOC_BACKEND supports only obmalloc and jemalloc\n" 并 `_exit(127)`（malloc_hook.cpp:121-139）；
    - macOS：`configure_darwin_malloc_zone(UNKNOWN)` 返回 false，`main` 打印 "Failed to configure macOS malloc zone for backend 'unknown'." 并返回 `OB_ERR_UNEXPECTED`（main.cpp:647-654、ob_malloc.cpp:177-194）；
    - Windows、Android：`observer.init` 之后记 WARN "invalid malloc backend"（main.cpp:799-802）。
- Windows、Android 没有 jemalloc（`deps/external/CMakeLists.txt:17-19`），`ObMalloc` 退回系统分配器，统计全为 0（A.4 `malloc-backend`）。

**S19 libc 符号覆盖**（Rust 主程序、Linux）
- 与 C++ 的 jemalloc 分支逐个相同：
  - `malloc` → `je_malloc` 等；`realloc(p, 0)`（`p` 非空）先 free 再返回空，`realloc(NULL, n)` 等于 `malloc(n)`（malloc_hook.cpp:221-226）；`free` 保留 errno（:194、:213）；
  - `calloc`：乘法溢出就 `abort()`，否则 `malloc` 后清零（malloc_hook_extended.cpp:69-90）；
  - `aligned_alloc`（glibc 2.38 起检查对齐是 2 的幂，否则 `EINVAL`）、`posix_memalign`（`EINVAL`/`ENOMEM`）、`valloc`、`pvalloc`（加页掩码溢出就 `abort()`）的参数检查照抄（:146-205）。
- 不再弱别名覆盖 `mmap`/`munmap`（:306-321），所以 `get_unmanaged_memory_size()` 恒为 0（A.4 `mem-view`）。
- 效果是 libxml2、s2、curl、OpenSSL、SQLite、vsag 等用默认分配器的库（第三方库调查 C 节）照旧落在 jemalloc 上。

**S20 第三方库回调**（安全侧在本模块，蹦床在 09）
- zstd：`ob_zstd_malloc(opaque, size)`（ob_compress_util.h:76-84）`opaque` 为空时返回空，否则分配。
  - Rust：`ForeignBlock::try_new(size)`，失败返回空（C++ 的池或 arena 失败时同样返回空）。
  - 原版的 `ZSTD_calloc` 不检查空指针就清零（zstd_common.c:61-70），照样保留。
  - C++ 里两种 opaque：服务端的 `ObCompressorPool` 用共享的 VSlice 池（ob_compressor_pool.cpp:23-30）；导出和导入用语句 arena（ob_select_into_op.cpp:1006、ob_load_data_impl.cpp:2142）。Rust 都改成全局分配器（0.14：不接 bump arena）。`ob_zstd_free` → drop。
- zlib：`ob_zlib_alloc(opaque, items, size)` 的大小是 `unsigned` 乘法，溢出回绕（ob_compress_util.h:94-102），照写 `(items as u32).wrapping_mul(size as u32)`。zlib 自己的缺省分配器（`COMPRESS()` 等）走 libc，见 S19。
- vsag：见 S15。
  - `ObVsagSearchAlloc`（ob_vector_index_adaptor.h:237-257）在 C++ 里用 arena、`Deallocate` 不释放；Rust 改成 `ForeignBlock`，`Deallocate` 真正释放（0.14 禁止接 bump arena；内存释放得更早，结果不变）。
  - vsag 可能在自己的线程上回调，所以回调上下文是 `Sync`（0.14），只碰原子计数。
- CRoaring：RULEBOOK 0.14 换成 `roaring` crate，Rust 侧不再安装 `roaring_init_memory_hook`（ob_rb_memory_mgr.cpp:153-165）。vsag 内部的 CRoaring 改走 libc（S19）。`vec_idx_used_` 的口径见 Q3。
- OpenSSL：`CRYPTO_set_mem_functions`（ob_encryption_util.cpp:281-317）只是给 libc 分配加线程局部标签。Rust 不安装，`ObEncryptionUtil::init_ssl_malloc()` 直接返回 `Ok(())`，jemalloc 下行为相同。
- RapidJSON：Reader 移植到 Rust（0.14），没有回调。
- 解析器 `parse_malloc`/`parse_realloc`/`parse_free`：属于 09 的 `ParseArena`，可以建在 `Arena::alloc`（置零，C++ 也置零，parse_malloc.cpp:27-41）上。解析是单线程的（0.12），不受"不接 bump arena"的限制。
- 字符集加载器的 `charset_malloc`（ob_charset.cpp:2465-2496）失败时每 10 ms 重试、永不放弃：06 照写成 `try_bytes_zeroed` 的重试循环。

**S21 SMART_VAR**
- 依赖外层 `ret` 的形式：
  - 外层 `ret` 已失败时不分配、不执行 body（ob_smart_var.h:173）；
  - 分配失败时 `ret = OB_ALLOCATE_MEMORY_FAILED`，跳过 body（:95-98、:176）；
  - `INDEPENDENT` 形式用独立的 ret（:172、:205-207）。
- C++ 里小对象放在栈上，Rust 一律上堆（RULEBOOK §2.4）。
- 数组形式要求初始化：C++ 不清零，Rust 用 `[0; N]`。
- `ERRSIM_SMART_VAR` 注入（ob_smart_var.cpp:28-36）：C++ 只在走堆的那一支生效。`cfg(feature = "errsim")` 下，`heap_var!` 和 `size_of::<T>() > 128 KB` 的 `smart_var!` 读 `errsim_point!(ERRSIM_SMART_VAR)`（04），非零时按分配失败处理。

**S22 内存视图**（A.4 `mem-view`：保留表结构，内容由 jemalloc 统计和 MemBudget 填充；裁判只通过 `information_schema` 用例检查表名，`migration/judge/suites/mysqltest/cases.tsv:168`）

| 表、视图 | 定义（`ob_inner_table_schema_def.py`） | C++ jemalloc 下的内容 | Rust 的内容 |
|---|---|---|---|
| `__all_virtual_memory_info`（11037） | :3313-3333；ctx_id、label、ctx_name、mod_type、mod_id、mod_name、hold、used、count | 1 行：(5, KvstorCacheMb, 0, 0, 0)（ob_ctx_allocator.cpp:91-103） | `iter_label`：每个登记的预算或读数函数 1 行（ctx_id、label = 名字、ctx_name = `ObCtxInfo`、mod_type 'user'、mod_id 0、mod_name = label、hold、used、count 0），另加 1 行 (DEFAULT_CTX_ID, "JemallocTotal", hold = stats.active, used = stats.allocated) |
| `V$OB_MEMORY`（21017） | :7444-7466，上表的聚合 SQL | 1 行 | 随上表 |
| `__all_virtual_ctx_memory_info`（12211） | :4964-4980；ctx_id、ctx_name、hold、used、limit | 8 行，hold = used = 0，limit 缺省 `INT64_MAX`，`_ctx_memory_limit` 可改（ob_server_duty_task.cpp:56-84） | 与 C++ 相同：`get_ctx_hold`/`get_ctx_used` 恒为 0，limit = `get_ctx_limit`（预算的数值只出现在上一行的标签表里） |
| `__all_virtual_memory_context_stat`（11094） | :3686-3706 | 桩，0 行 | 0 行 |
| `__all_virtual_malloc_sample_info`（12363） | :5723-5739 | 0 行 | 0 行 |
| `__all_virtual_vector_mem_info`（12550）、`V$OB_VECTOR_MEMORY`（21662） | :6380-6397、:13811-13827 | `raw_malloc_size` 为 0（来自采样表），其余来自向量分配器 | 同左 |
| 工作区四表和三视图（12158-12161、21084/21086/21088） | :4724-4816、:7831-7927 | 来自 `ObSqlMemoryManager` | 同左（翻译代码负责；`workarea_hold_size`/`total_mem_used` 读工作区预算） |
| memstore、KV cache 等模块表 | 第 5–7 节（视图调查） | 来自各模块自己的计数 | 同左（08 等负责） |

- `__all_virtual_memstore_info` 在 C++ 里会重复输出同一行，直到超时（ob_all_virtual_memstore_usage.cpp:48 的 `start_to_read_` 从未置位）。这是翻译单元的事，记在这里提醒 08。

**S23 ObMemoryDump**
- C++ 的 `init` 建一个上下文、预分配缓冲、起一个 "MemoryDump" 线程，每 10 秒扫描 obmalloc 的 chunk 做标签统计（memory_dump.cpp:174-218、:330-360）；`DUMP_CONTEXT` 读用户在命令文件里给的任意地址（ob_dump_task_generator.cpp:86-100）。jemalloc 下扫描什么也找不到（视图调查第 1 节）。
- Rust 只保留门面：生命周期方法和返回码；不建上下文、不起线程、不读任意地址。
- 少一个空转的后台线程；生命周期场景不检查线程名（Q7）。

**S24 偏差汇总**
- 已接受（A.4）：
  - `oom-abort`：小的固定大小分配 OOM 时 abort；
  - `query-mem-limit`：11049 的口径（S12）；
  - `malloc-backend`：不支持 obmalloc（S18），Windows、Android 用系统分配器；
  - `mem-view`：视图内容（S22），`get_unmanaged_memory_size` 恒为 0（S19），`ObMemoryDump` 不扫描（S23）。
- 需要修订 A.4 才能接受的（Q3）：
  - 计账粒度的变化影响内部阈值：池上限按对象计费（S13）；C++ 经 `ModulePageAllocator` 记到工作区或上下文的容器缓冲，若 05 不提供带 tracker 的容器就不计入（S14）；`vec_idx_used_` 不再经 CRoaring 的钩子计数（S20）；
  - 结构体大小不同带来的统计差异（`sizeof(ObRangeNode)` 等在 Rust 里不同），影响落盘时机和查询范围退化。

## 4. unsafe

全部在 `rust/port/oblib/src/mem/**`（§3 白名单第 1 项）。对外的 API 都是安全的，唯一例外是 `ForeignBlock::from_raw`，它只给 -sys 和 shim 的蹦床用（白名单第 13 项）。

| # | 位置 | 为什么需要 | 安全论证要点 |
|---|---|---|---|
| U1 | `unsafe impl GlobalAlloc for ObMalloc` 和 `je_*` 调用 | 全局分配器的契约 | layout 的 size 非零由调用方保证（GlobalAlloc 的前置条件）；对齐标志由 layout 推出，`dealloc`/`realloc` 用与分配时相同的规则算标志，`sdallocx` 收到的是请求大小（在合法区间内）；不分配、不加锁、不 panic、不碰线程局部，没有重入（S-23） |
| U2 | `try_box`、`try_box_with`、`AlignedBuf` | `Box::try_new` 不稳定；对齐缓冲要自定 layout | 分配 `Layout::new::<T>()`（或 `(len, max(align,16))`）后检查空指针，再 `ptr.write`/`write_bytes(0)`，然后 `Box::from_raw` 或保存 `NonNull`；`AlignedBuf` 的 Drop 用同一个 layout 释放；`len == 0` 时用对齐的悬垂指针、不分配 |
| U3 | `je_mallctl`、`je_nallocx`、`je_malloc_usable_size` | FFI | 名字是以 NUL 结尾的常量；`oldlenp` 等于值类型的大小，类型不符时 jemalloc 返回 EINVAL；`nallocx` 是纯计算 |
| U4 | `#[no_mangle] static je_malloc_conf` | 符号必须由主程序定义 | 指向 `'static` 的 C 字符串常量，只读；包装类型 `unsafe impl Sync`（白名单内，不含临界区令牌） |
| U5 | `Latch<Arena>` 的方法返回 `&'a mut T`，`'a` 是 `&self` 的借用 | 锁内分配、锁外使用 | bump 分配单调，块不移动、不释放，直到 `reset`/`reuse`（要 `&mut`，即 `get_mut`）或 Drop；而 `'a` 期间二者都被借用检查器挡住；分配本身在锁内完成，`Bump` 的内部可变状态不会被并发修改；不同分配返回的区间互不重叠，所以多个 `&mut` 不别名 |
| U6 | `ForeignBlock`（`alloc`/`dealloc`、头部读写、`into_raw`/`from_raw`、`try_grow` 的拷贝） | 交给 C 的原始内存 | 块总长写在头里，`from_raw` 回退 16 字节读头，按同一 layout 释放；`try_grow` 用 `copy_nonoverlapping` 拷原始字节，不在 Rust 侧把可能未初始化的载荷形成 `&[u8]`；`from_raw` 的前置条件写在文档里，只在蹦床里调用 |
| U7 | `malloc_hook.rs` 的 `#[no_mangle] extern "C"` 函数 | 覆盖 libc 符号 | 参数原样转发给 `je_*`；不 panic（`extern "C"` 下 panic 会 abort）；`free` 保存并恢复 errno；不分配、不加锁 |
| U8 | macOS 的 `malloc_get_all_zones`、`malloc_zone_register`/`unregister` | zone 提升 | 照 C++ 的顺序调用（ob_malloc.cpp:63-117）；只在 `main` 的开头、单线程时调用 |
| U9 | Windows 的 `GetProcessMemoryInfo` 声明（`get_virtual_memory_used`） | 白名单里没有 windows-sys | `extern "system"` 声明，结构体按 MSDN 的布局写，`cb` 字段设成结构体大小 |

不需要 unsafe 的：`Arena` 的分配（bumpalo 的安全 API）、`ArenaVec`（bumpalo 的 `Vec`）、`MemCtx`、`MemBudget`、`TrackedBuf`（`Vec<u8>` + `try_reserve`）、`ObMemTracker`。模块里没有 `unsafe impl Send/Sync`，除了 U4 的包装类型和 `AlignedBuf`、`ForeignBlock`：后两者拥有独占的字节，跨线程移动和共享读都没有别名问题。

## 5. 测试

| # | 种类 | 内容 |
|---|---|---|
| T1 | 单测 | `Arena`：`alloc(0)`、`alloc(-1)`、`alloc_unaligned(0)`（空 arena 记一页）、`try_str(b"")` 不分配、16 字节对齐、`try_alloc` 对需要 Drop 的类型编译失败（trybuild 的 compile-fail 用例）、`reset`/`reuse`/`reset_remain_one_page` 之后的统计、`realloc` 两条路径 |
| T2 | 金标准 | PageArena 统计：5b 阶段的 C++ 测试程序对脚本化的操作序列（alloc/alloc_aligned/unaligned/realloc/reuse/reset/reset_remain_one_page，大小覆盖 0、1、15、16、7904、7905、页大小附近、1 MB）打印每一步的 used/total/pages，Rust 读同一脚本比对；页大小覆盖 7936、65280、245760、2079744 |
| T3 | 单测 | `ArenaVec`：增长序列 1、2、4、8…，`into_slice`；`needs_drop` 的编译失败用例 |
| T4 | Miri | `Latch<Arena>` 的生命周期延长（单线程多次分配后同时持有多个 `&mut`）；`ForeignBlock` 的 into_raw/from_raw/try_grow；`TrackedBuf`、`AlignedBuf` 的 Drop；`try_box` 的零大小类型。`cfg(miri)` 下 `ObMalloc` 用 System |
| T5 | loom | `MemBudget`：并发 alloc/free 后归零；`try_alloc` 在边界上不超额（两个线程各 `try_alloc(limit/2+1)`，最多一个成功）；父链传播后祖先等于子之和；`Latch<Arena>` 并发分配互不重叠 |
| T6 | ASan | `ObMalloc` 的 alloc/realloc/dealloc（各种 size、align = 1…4096）、`alloc_zeroed` 置零；`AlignedBuf`；在 Linux 上用 `link-jemalloc` 跑（Q8） |
| T7 | 单测 | `MemCtx`：hold/used 的组成；`malloc` 按 `usable_size` 计（1000 → 1024）；子上下文计入父的 `tree_mem_hold`；带 `ADD_CHILD_THREAD_SAFE` 的子不计入、自己的 `tree_mem_hold` 为 0；`MemCtx::new` 不挂树；Drop 后父的树预算回到原值；工作区预算按页变化，含 `reuse` 释放的大页 |
| T8 | 单测 | 11049：不装 tracker 时恒为 Ok；u16 回绕后节奏仍为每 1024、每 32 次；第 1 次和每 512 次刷新上限；`pct = 0` 时不报错；报错的 `log_user_error` 文本与金标准逐字节相同（金标准由 C++ 的 `LOG_USER_ERROR(OB_EXCEED_QUERY_MEM_LIMIT, …)` 产出） |
| T9 | 金标准 | `get_memory_by_percentage` 的边界（0、负数、`INT64_MAX/99` 附近、溢出）；`usable_size` 与 C++ `malloc_usable_size(malloc(n))` 对 1…4 MB 的若干点逐一相等 |
| T10 | 单测 | `smart_var!`：外层 ret 已失败时 body 不执行；注入分配失败时 ret = 4013；`independent` 不改外层 ret；`smart_vars_2!` 第一个失败时第二个不分配；在 256 KB 栈的线程里 `smart_var!(ret, b: [u8; 2 << 20] = [0; 2 << 20], {…})` 不溢出（release） |
| T11 | 集成 | 链接测试（Linux、macOS）：Rust staticlib 加 C++ 小程序，C++ `je_free` 释放 Rust `Box` 的指针、Rust 释放 C++ `je_malloc` 的指针；`mallctl("opt.background_thread")`、`opt.dirty_decay_ms`、`opt.muzzy_decay_ms` 读回 `je_malloc_conf` 的值；fork 之后 `restore_malloc_backend_after_fork()` 返回 true |
| T12 | 集成 | Linux 主程序的 `malloc-hook`：`malloc_usable_size(malloc(1000)) == 1024`；`realloc(p, 0)` 返回空；`MALLOC_BACKEND=bogus` 时退出码 127，stderr 文本与 C++ 相同；`MALLOC_BACKEND=obmalloc` 时正常启动并有一条 WARN |
| T13 | 单测 | `MemBudget::try_alloc` 的 WARN 限频；`can_alloc_block` 的严格大于；`alloc_block` 分配失败时回退计数 |
| T14 | 单测 | 视图数据：登记两个预算和一个读数函数后 `iter_label` 的行；预算释放后自动消失；`get_ctx_limit` 缺省 `INT64_MAX`，`set_ctx_limit` 后读回 |
| T15 | 单测 | `ForeignBlock`：`try_new(0)` 为 None；`total_size = 16 + size`；`try_grow` 缩小时原样返回、扩大时拷贝；zlib 的 u32 回绕（`items = 65536, size = 65536` → 0 字节请求） |

## 6. 翻译者怎么用

**E1 查询作用域的 placement new → `arena.try_alloc`**（`src/sql/engine/expr/ob_expr_json_func_helper.cpp:1307-1330`）

```cpp
if (OB_ISNULL(buf = allocator->alloc(sizeof(ObJsonString)))) {
  ret = OB_ALLOCATE_MEMORY_FAILED;
} else {
  json_node = (ObJsonString*)new(buf)ObJsonString(j_str.ptr(), j_str.length());
}
```
```rust
// ObJsonString<'q> 不需要 Drop（只借用字符串），按 types.tsv 属查询作用域
match arena.try_alloc(ObJsonString::new(j_str)) {
    Err(e) => { ret = Err(e); }   // 即 OB_ALLOCATE_MEMORY_FAILED
    Ok(n) => { json_node = Some(n); }
}
```

**E2 `OB_NEW`/`OB_DELETE`，照抄 C++ 的错误码**（`src/sql/engine/cmd/ob_load_data_file_reader.cpp:72-77`）

```cpp
ObRandomFileReader *tmp_reader = OB_NEW(ObRandomFileReader, MEMORY_ATTR, allocator);
if (OB_ISNULL(tmp_reader)) {
  ret = OB_ERR_UNEXPECTED;
} else if (OB_FAIL(tmp_reader->open(param.filename_))) {
  OB_DELETE(ObRandomFileReader, MEMORY_ATTR, tmp_reader);
}
```
```rust
let mut tmp_reader: Option<Box<ObRandomFileReader>> = try_box(ObRandomFileReader::new()).ok();
match tmp_reader.as_mut() {
    None => { ret = Err(OB_ERR_UNEXPECTED); }          // C++ 在这里设的不是 4013，照抄（0.1.4）
    Some(r) => {
        if ob_fail!(ret = r.open(param.filename_)) {
            tmp_reader = None;                          // OB_DELETE
        } else {
            *file_reader = tmp_reader;                  // 输出参数 &mut Option<Box<dyn …>>（§2.4，清单第 2 类）
        }
    }
}
```
ctor 的 `allocator` 实参按 §2.4 处理：拥有型删掉，查询作用域换成 `&'q Arena`。

**E3 `SMART_VAR` 的字符数组加 `ob_write_string`**（`src/sql/ob_sql_utils.cpp:1633-1638`）

```cpp
SMART_VAR(char[OB_MAX_SQL_LENGTH], buf) {
  MEMSET(buf, 0, sizeof(buf));
  if (OB_FAIL(inner_print(buf, sizeof(buf), res_len))) {
  } else if (OB_FAIL(ob_write_string(allocator, ObString(res_len, buf), result))) {
  }
}
```
```rust
smart_var!(ret, buf: [u8; OB_MAX_SQL_LENGTH as usize] = [0u8; OB_MAX_SQL_LENGTH as usize], {
    let buf_len = buf.len() as i64;                       // sizeof(buf)
    if ob_fail!(ret = self.inner_print(&mut buf[..], buf_len, &mut res_len)) {
    } else {
        match arena.try_str(&buf[..res_len as usize]) {   // allocator 在这里是查询作用域的 &'q Arena
            Ok(s) => { *result = s; }
            Err(e) => { *result = &[]; ret = Err(e); }
        }
    }
});
```

**E4 局部 arena 的原始字节分配**（`src/sql/ob_sql_utils.cpp:1650-1656`）

```cpp
ObArenaAllocator alloc;
const int64_t length = OB_MAX_SQL_LENGTH * i;
char *buf = NULL;
if (OB_ISNULL(buf = static_cast<char*>(alloc.alloc(length)))) {
  ret = OB_ALLOCATE_MEMORY_FAILED;
} else if (FALSE_IT(MEMSET(buf, 0, length))) {
```
```rust
let alloc = Arena::new();
let length = OB_MAX_SQL_LENGTH * i;
match alloc.alloc(length) {                       // 已置零；length <= 0 时同样失败
    Err(_) => { ret = Err(OB_ALLOCATE_MEMORY_FAILED); }
    Ok(buf) => { /* MEMSET 一行保留为空操作，或删去 */ }
}
```

**E5 请求入口的临时上下文，情况 (a)**（`src/observer/mysql/obmp_query.cpp:469-482`）

```cpp
lib::ContextParam param;
param.set_mem_attr(ObModIds::OB_SQL_EXECUTOR, ObCtxIds::DEFAULT_CTX_ID)
  .set_properties(lib::USE_TL_PAGE_OPTIONAL)
  .set_page_size(OB_MALLOC_REQ_NORMAL_BLOCK_SIZE)
  .set_ablock_size(lib::INTACT_MIDDLE_AOBJECT_SIZE);
CREATE_WITH_TEMP_CONTEXT(param) {
  ret = do_process(session, has_more_result, force_sync_resp, async_resp_used, need_disconnect);
  ctx_.clear();
}
```
```rust
let mut param = ContextParam::new();
param.set_properties(USE_TL_PAGE_OPTIONAL).set_page_size(OB_MALLOC_REQ_NORMAL_BLOCK_SIZE);  // mem_attr、ablock_size 删掉
{
    let mem_ctx = MemCtx::new(&param);
    ret = self.do_process(&mem_ctx, session, has_more_result, force_sync_resp, async_resp_used, need_disconnect);
    self.ctx_.clear();
}
```

**E6 worker：请求上下文、`MEM_TRACKER_GUARD` 和 check_status 钩子**（`src/observer/omt/ob_th_worker.cpp:239-261`、`:325-342`）

```cpp
CREATE_WITH_TEMP_CONTEXT(param) {
  MEM_TRACKER_GUARD(CURRENT_CONTEXT);
  ...
  process_request(*req);
}
int ObThWorker::check_status() {
  ...
  if (OB_UNLIKELY((OB_SUCCESS != (ret = CHECK_MEM_STATUS())))) {
```
```rust
let mem_ctx = MemCtx::new(&param);
ObMemTrackerGuard::with(&mem_ctx, || {
    // AllocatorGuard（:248-261）把请求 arena 塞进 THIS_WORKER，按审查 S-11 删除，改为向下传 &mem_ctx
    self.process_request(&mem_ctx, req);
});
fn check_status(&self) -> ObResult {   // 0.5：worker 的 check_status 钩子，手里没有 MemCtx
    ...
    if ob_fail!(ret = check_mem_status()) {
```

**E7 有 MemCtx 的检查点**（`src/sql/engine/ob_exec_context.cpp:679`、`src/sql/resolver/dml/ob_select_resolver.cpp:1006`）

```cpp
} else if (OB_UNLIKELY((OB_SUCCESS != (ret = CHECK_MEM_STATUS())))) {
if (OB_UNLIKELY((OB_SUCCESS != (ret = TRY_CHECK_MEM_STATUS(check_try_times))))) {
```
```rust
} else if ob_fail!(ret = self.mem_ctx.check_status()) {          // ObExecContext 持有 &'q MemCtx（07）
if ob_fail!(ret = try_check_mem_status(check_try_times)) {       // resolver 手里没有 MemCtx 时的写法
```

**E8 情况 (c)，块内要用 MemCtx**（`src/share/geo/ob_geo_utils.cpp:721-723`）

```cpp
CREATE_WITH_TEMP_CONTEXT(lib::ContextParam().set_mem_attr("GISModule", ObCtxIds::DEFAULT_CTX_ID)) {
  ObGeoBufferStrategy buf_strat;
  ObGeoEvalCtx gis_context(CURRENT_CONTEXT, srs);
```
```rust
{
    let tmp_ctx = MemCtx::new(&ContextParam::new());   // 标签删掉
    let mut buf_strat = ObGeoBufferStrategy::default();
    let mut gis_context = ObGeoEvalCtx::new(&tmp_ctx, srs);   // ObGeoEvalCtx<'q> 持有 &'q MemCtx 和它的 arena
```

**E9 `ObSafeArenaAllocator`**（`src/storage/blocksstable/ob_sstable.cpp:1131-1141`）

```cpp
common::ObArenaAllocator tmp_allocator(common::ObMemAttr("CacheSST"));
ObSafeArenaAllocator safe_allocator(tmp_allocator);
...
do {
  safe_allocator.reuse();
  ret = get_meta(meta_handle, &safe_allocator);
} while (ignore_ret(ret));
```
```rust
let mut safe_allocator = Latch::new(ObLatchIds::OB_AREAN_ALLOCATOR_LOCK, Arena::new());  // 拥有原来被包装的 arena
...
let meta_handle = loop {
    safe_allocator.get_mut().reuse();                 // 要 &mut：上一轮的句柄必须已经释放
    let mut handle = ObSSTableMetaHandle::default();  // 每轮新建；它借用 safe_allocator
    ret = self.get_meta(&mut handle, Some(&safe_allocator));
    if !ignore_ret(&ret) { break handle; }
};
```
C++ 在 `reuse()` 时上一轮的 `meta_handle` 可能还指着 arena 里的内存；Rust 用每轮一个句柄表达同样的循环，跨过 `reuse` 持有句柄会编译失败（S4）。

**E10 `MemoryUsageTracker` → `MemBudget`**（`src/sql/engine/ob_sql_memory_manager.h:684-686`、`:786`，`.cpp:808-815`）

```cpp
common::MemoryUsageTracker workarea_managed_tracker_;
int64_t get_workarea_managed_used() const { return workarea_managed_tracker_.used(); }
const int64_t work_area_max_size = memory_budget / 100 * pctg;
const int64_t workarea_managed_used = get_workarea_managed_used();
```
```rust
workarea_managed_tracker_: Arc<MemBudget>,   // init 里：MemBudget::new("SqlWorkArea", ObCtxIds::WORK_AREA, None)，再 register()
fn get_workarea_managed_used(&self) -> i64 { self.workarea_managed_tracker_.used() }
let work_area_max_size = memory_budget / 100 * pctg;
let workarea_managed_used = self.get_workarea_managed_used();
```

**E11 算子上下文：WORK_AREA、`used()`**（`src/sql/engine/sort/ob_sort_op_impl.cpp:739-746`、`:1027`）

```cpp
lib::ContextParam param;
param.set_mem_attr(ObModIds::OB_SQL_SORT_ROW, ObCtxIds::WORK_AREA)
  .set_properties(lib::USE_TL_PAGE_OPTIONAL);
if (NULL == mem_context_ && OB_FAIL(CURRENT_CONTEXT->CREATE_CONTEXT(mem_context_, param))) {
} else if (OB_FAIL(datum_store_.init(INT64_MAX, ObCtxIds::WORK_AREA, ObModIds::OB_SQL_SORT_ROW, ...
...
} else if (OB_FAIL(sql_mem_processor_.update_used_mem_size(mem_context_->used()))) {
```
```rust
let mut param = ContextParam::new();
param.set_properties(USE_TL_PAGE_OPTIONAL)
     .set_memory_tracker(sql_mem_mgr.get_workarea_managed_tracker().clone());   // WORK_AREA 不删，改成挂预算（S14）
if self.mem_context_.is_none() && ob_fail!(ret = exec_ctx.get_mem_ctx().create_context(&mut self.mem_context_, &param)) {
} else if ob_fail!(ret = self.datum_store_.init(i64::MAX, ObCtxIds::WORK_AREA /* 序列化字段，保留 */, ...)) {   // 标签删掉
...
} else if ob_fail!(ret = self.sql_mem_processor_.update_used_mem_size(self.mem_context_.as_ref().unwrap().used())) {
```
行存储的块改由 `mem_ctx.get_malloc_allocator().alloc(size)` 分配（`TrackedBuf`），所以 `used()` 包含它们，与 C++ 同口径。

**E12 向量预算与 vsag 回调**（`src/storage/allocator/ob_vector_allocator.cpp:54-70`、`:131-156`）

```cpp
void *ObVsagMemContext::Allocate(uint64_t size) {
  if (size != 0) {
    int64_t actual_size = MEM_PTR_HEAD_SIZE + size;
    void *ptr = ObVectorMemContext::alloc(actual_size);
    if (OB_NOT_NULL(ptr)) { ATOMIC_AAF(all_vsag_use_mem_, actual_size); *(int64_t*)ptr = actual_size; ret_ptr = (char*)ptr + MEM_PTR_HEAD_SIZE; }
```
```rust
fn allocate(&self, size: u64) -> Option<ForeignBlock> {        // vsag_shim 的蹦床调用它，再 into_raw
    let mut ret_ptr: Option<ForeignBlock> = None;
    if size != 0 {
        // 内部：S15 的限额检查（检查用的大小是 16 + size，即 C++ 的 actual_size）+ ForeignBlock::try_new(size)
        if let Some(block) = self.vector_mem_ctx.alloc(size) {
            self.all_vsag_use_mem_.aaf(block.total_size());    // 16 + size，与 actual_size 相同
            ret_ptr = Some(block);
        }
    }
    ret_ptr
}
fn deallocate(&self, block: ForeignBlock) {                    // 蹦床：unsafe { ForeignBlock::from_raw(p) }
    self.all_vsag_use_mem_.saf(block.total_size());
}                                                              // block 在这里 drop，即 C++ 的 free
```
翻译阶段禁用 `?`（§2.1），所以这里写成 `if let`。

**E13 删掉内存标签和 hook guard**（`src/observer/vector_index/ob_plugin_vector_index_adaptor.cpp:766`、`src/storage/allocator/ob_vector_allocator.cpp:117-121`）

```cpp
lib::ObMallocHookAttrGuard malloc_guard(lib::ObMemAttr("VIndexVsagADP"));
ObMemAttr attr("VIndexVsagADP", ObCtxIds::VECTOR_CTX_ID);
param.set_mem_attr(attr).set_page_size(OB_MALLOC_MIDDLE_BLOCK_SIZE).set_parallel(8)
     .set_properties(lib::ALLOC_THREAD_SAFE | lib::RETURN_MALLOC_DEFAULT);
```
```rust
// 第一行整行删掉。
// 向量上下文是长生命周期、跨线程共享的（ALLOC_THREAD_SAFE | RETURN_MALLOC_DEFAULT），不映射到 MemCtx：
// 改成持有 Arc<MemBudget>（hold 的口径）加 ForeignBlock（分配），见 §2.12 与 S15。
```

**E14 带上限的池**（`src/oblib/lib/allocator/ob_block_alloc_mgr.h:38-55`）

```cpp
void* alloc_block(int64_t size, ObMemAttr &attr) {
  int64_t used_after_alloc = ATOMIC_AAF(&hold_, size);
  if (used_after_alloc > limit_) { ATOMIC_AAF(&hold_, -size); ... }
  else if (NULL == (ret = (void*)ob_malloc(size, attr))) { ATOMIC_AAF(&hold_, -size); }
```
```rust
// 池的使用方（如 ObTxDataAllocator 的 block_alloc_，ob_tx_data_allocator.h:65）：
block_alloc_: Arc<MemBudget>,                       // MemBudget::new("TX_DATA_SLICE", ObCtxIds::DEFAULT_CTX_ID, None)
let buf = self.block_alloc_.alloc_block(size);      // None ⇔ C++ 的 NULL；Drop 即 free_block
```

**E15 zstd 的分配回调**（`src/oblib/lib/compress/ob_compress_util.h:76-92`）

```cpp
static void *ob_zstd_malloc(void *opaque, size_t size) {
  void *buf = NULL;
  if (NULL != opaque) { ObIAllocator *allocator = reinterpret_cast<ObIAllocator*>(opaque); buf = allocator->alloc(size); }
  return buf;
}
```
```rust
// zstd138_sys 里的蹦床（09）只做指针转换；安全侧：
pub fn ob_zstd_malloc(ctx: Option<&ZstdAllocCtx>, size: usize) -> Option<ForeignBlock> {
    match ctx { None => None, Some(_) => ForeignBlock::try_new(size as u64) }   // opaque 为空 → 空，照 C++
}
pub fn ob_zstd_free(ctx: Option<&ZstdAllocCtx>, block: Option<ForeignBlock>) {
    if ctx.is_some() { drop(block); }   // C++ opaque 为空时不释放：蹦床此时不调 from_raw，块随之泄漏，照样复刻
}
```

## 7. 依赖

- **白名单 crate**（RULEBOOK §1）：
  - `bumpalo`，开 `collections` 特性。RULEBOOK 写的是 3.20；3.20.0 已被撤回（yanked），按工具链的做法钉 `=3.20.3`（docs.rs 显示的 3.20.x 最新版，2026-05-22）。
  - `seekdb-jemalloc-sys =0.2.2`（jemalloc 5.3.1，`stats` 特性；`deps/external/Cargo.toml:10`），只在 `link-jemalloc` 特性下依赖。
  - `libc`：类型、`_exit`、`sysconf`，以及 Linux 的 `/proc/self/statm`。
  - 测试：`loom`、`trybuild`。这两个不在 §1 的表里，但 §3 要求 loom 测试、本文要求 compile-fail 用例，作为 dev-dependencies 处理，见 Q8。
- **其他 1d 模块**：
  - 02：`Latch`、`ObLatchIds::OB_AREAN_ALLOCATOR_LOCK`；
  - 03：`WorkerCtx` 的 check_status 钩子调用 `check_mem_status()`；线程入口不再建 ThreadRoot 上下文；
  - 04：`ObError`、`ObResult`、`OB_*`、`log_warn!`、`log_user_error!`、`reach_time_interval!`、`errsim_point!`；
  - 05：`ObIArray` 的 `ArenaVec` 实现；`ObHashMap`（`ObMallocSampleMap`）；带 tracker 的容器（Q3）；
  - 06：`ob_define` 的常量，`ObStringExt`（作用在 `ObStr` 上），`ob_write_string` 的少见变体，prelude 导出本模块的 `Arena`、`ArenaVec`、`ObStr`、`MemCtx`、`MemBudget`、`try_box` 和 §2.10 的宏；
  - 07：`ObExecContext` 持有 `&'q MemCtx`；帧模块的批次 arena 用 `Arena`；`ObRawExprFactory` 的 `try_check_status`；
  - 08：KV cache 和 memstore 的读数函数；
  - 09：zstd、zlib、vsag 的蹦床（`ForeignBlock`），解析器的 `check_mem_status`/`try_check_mem_status` 回调和 `ParseArena`。
- **既有 crate**：`config`（`query_memory_limit_percentage()`）。

## 8. 开放问题

**Q1 `CHECK_MEM_STATUS` 有两种写法，tracker 放在线程局部**（已排修订）
- 问题：
  - RULEBOOK §2.4 把 `CHECK_MEM_STATUS()` 译成 `mem_ctx.check_status()`，0.5/0.7 规定 MemCtx 不从线程局部取。
  - 但 C++ 的语义是"检查本线程装入的 tracker"（`ob_memory_tracker.cpp:26` 的 `thread_local`），与调用点手里是哪个上下文无关。
  - 9 个检查点里有 3 个手里根本没有上下文：worker 的 check_status 钩子（`ob_th_worker.cpp:333`），以及两个解析器 C 回调（`parse_node.c:183-187`）。
  - 而且嵌套的请求上下文（`obmp_query.cpp:475`）本身不受 tracker 保护，若按"检查自己这个 MemCtx"实现，触发点会变。
- 本文的做法：线程局部只放拥有型的 `Arc<ObMemTracker>`（0.5 允许拥有型数据），由请求入口经 `ObMemTrackerGuard::with` 闭包装入。`MemCtx::check_status()` 和自由函数 `check_mem_status()` 读同一个 tracker，语义完全相同。MemCtx 和 arena 本身从不进线程局部。
- 推荐：修订 §2.4 那一行，统一译成 `check_mem_status()`/`try_check_mem_status(n)`（C++ 包装函数的名字）。
- 备选：保留两种写法，按"手里有没有 MemCtx"分。这样第 3 步要多一个判断，审查者也要多查一处。

**Q2 `CURRENT_CONTEXT` 显式传参需要签名表**（已排修订）
- 问题：
  - `CURRENT_CONTEXT` 共 119 处：`->CREATE_CONTEXT` 52、`->get_arena_allocator()` 31、`->get_malloc_allocator()` 10 等。
  - 改成显式传 MemCtx，要给不带分配器参数的函数加参数。这是跨单元的签名变化，与 R1 的 `qa` 是同一类问题（审查 P-22），但现在没有对应的表。
  - 同样，`ObCtxIds::WORK_AREA` 的 83 处要逐处判定是"挂工作区预算"还是"只是序列化字段"。
- 推荐：清单第 11 类加两个子项，由脚本生成 `mem_ctx_sources.tsv`：
  - 每处 `CURRENT_CONTEXT` 的 MemCtx 来自哪个参数或成员（`ObExecContext`、`ObSqlCtx`、`ObResultSet` 等，07 定），没有来源的函数加 `mem_ctx: &MemCtx` 参数，规则只看声明；
  - 每处 WORK_AREA 的类别（S14）。
- 备选：在 `WorkerCtx` 里经闭包装入"当前 MemCtx"。这与 S-11 的结论相反，不推荐。

**Q3 计账口径带来的内部阈值差异，A.4 还没有对应项**（已排修订）
- 内容：
  1. 池的上限从按块改为按对象计费（S13）；
  2. C++ 经 `ModulePageAllocator` 记到工作区或上下文的容器缓冲（如 `ob_sort_op_impl.cpp:562,764`），Rust 用 `Vec` 后不计入；
  3. `vec_idx_used_` 不再经 CRoaring 钩子计数（S20）；
  4. Rust 的结构体大小与 C++ 不同，同样的对象数在 arena 里占的字节不同。
- 后果：落盘时机、查询范围退化、tx data 和日志池的流控、向量限额的触发点可能与 C++ 不同。结果集不变，但经过落盘的哈希算子在没有 ORDER BY 时的行序可能变；裁判有两个用例靠调低 `ob_sql_work_area_percentage` 强制落盘（`join_many_table.test:13`）。
- 推荐：
  - (a) A.4 新增 `mem-accounting` 项，裁判对这两个用例检查行序是否稳定，必要时加 `--sorted_result`；
  - (b) 同时请 05 给 `ObArray`/`ObSEArray` 的存储提供可选的 tracker（按 `usable_size` 计），把第 2 项的差异收回来。
- 备选：Arena 和容器整体按 C++ 的结构体大小记账。做不到：Rust 的布局不同。

**Q4 共存期 jemalloc 的链接**
- 问题：
  - C++ 已由 CMake 链接 `libjemalloc_pic.a`（`deps/external/cmake/Jemalloc.cmake:52-58`）；
  - `seekdb-jemalloc-sys` 的 build.rs 无条件发出 `rustc-link-lib=static=jemalloc_pic`，staticlib 里再带一份会造成重复符号或两份实例；
  - `je_malloc_conf` 也只能定义一次。
- 推荐：§2.0 的特性划分：staticlib 关闭 `link-jemalloc` 和 `define-malloc-conf`，只声明 `extern` 符号，由最终链接解析到 C++ 那一份；`cargo test` 和 Rust 主程序打开。
- 备选：CMake 改为链接 Rust 带出的那一份。这要改 C++ 构建，不推荐。

**Q5 Rust 主程序的 libc 分配和 macOS zone**（已排修订）
- 问题：RULEBOOK 0.7 只规定了 `#[global_allocator]`。但 C++ 在 Linux 上还覆盖了 libc 的分配符号（malloc_hook，Linux 专用，`src/oblib/lib/CMakeLists.txt:34-41`），在 macOS 上提升 jemalloc 的 zone。不照做的话，Rust 主程序里 libxml2、s2、curl、OpenSSL、vsag 的分配会回到 glibc 或系统 zone，内存行为（RSS、碎片）与基线不同。
- 推荐：照做（§2.8、S18、S19），只在 Rust 主程序里打开。
- 备选：不覆盖，记作 `PERF(port)`，接受内存行为的差异。

**Q6 `ENABLE_SANITY` 的 Memory Sanity 模式**（已排修订）
- 问题：0.15 要求 `ENABLE_SANITY` 映射成同名的 cargo 特性、每个分支都译。但 Memory Sanity 是 C++ 自带的调试分配器（jemalloc extent hooks、红区、毒化：`ob_jemalloc_sanity.cpp:231-391`、`ob_memory_sanity.h`），只存在于被替换的单元里，基线构建不用它。
- 推荐：不移植；`enable_sanity` 特性对本模块是空的；内存错误靠 §5 的 Miri 和 ASan 发现。
- 备选：移植 extent hooks（约 600 行 unsafe）。

**Q7 `memory_dump` 的探测**（已排修订）
- 问题：0.8 要求把 `memory_dump` 里的 `siglongjmp` 改成 `process_vm_readv`/`mach_vm_read_overwrite` 的内存探测。那段代码只用于扫描 obmalloc 的 chunk 和用户给出的任意地址；Rust 没有 obmalloc 的 chunk，读任意地址在安全 Rust 里也无意义。
- 推荐：删掉这一条，`ObMemoryDump` 只留门面（S23），也不起 "MemoryDump" 线程。
- 备选：保留线程和探测，扫描 Rust 侧的 MemCtx 登记表。

**Q8 测试工具链**（已排修订；loom 一项已由 02-sync 排过）
- 问题：
  - Miri 和 ASan 都只能在 nightly 上跑，而工具链钉在 1.98.1；
  - loom 和 trybuild 不在 §1 的白名单里。
- 推荐：这两类测试在 CI 里用固定日期的 nightly 单独跑，不参与构建；loom、trybuild 作为只在测试里出现的 dev-dependencies。这与 §1 的"表外不准引入"有出入，需要你确认。
- 备选：去掉 compile-fail 用例，`needs_drop` 断言只靠审查；ASan 改用 Linux 上的 Valgrind（不需要 nightly，但慢）。

**Q9 新名字**（已排修订）
- 问题：§4 的改名表只列了 `Arena`、`ArenaVec`、`ObStr`、`MemBudget`、`MemCtx`、`try_box`。本文还需要：
  - 类型 `TrackedBuf`、`AlignedBuf`、`ForeignBlock`、`JemallocStats`；
  - 函数 `try_box_with`、`try_vec`、`try_reserve`、`try_bytes`、`try_bytes_zeroed`、`usable_size`、`get_jemalloc_stats`。
  - 其余名字都沿用 C++：`ObMalloc`、`MemoryContextMalloc`、`ContextParam`、`ObMemTracker`、`ObMemTrackerGuard`、`check_mem_status`、`ObMemoryDump`、`ObLabelItem`、`ObCtxIds`、`ObCtxInfo`、`get_total_limit` 等；`get_allocator_memory_hold` 的重载按 `overloads.tsv` 改名。
- 推荐：把上面这些加进改名表，归属 `oblib::mem`。
- 备选：用现有名字的方法代替自由函数，如 `Arena`/`MemBudget` 上的关联函数，名字数量相同，只是位置不同。
