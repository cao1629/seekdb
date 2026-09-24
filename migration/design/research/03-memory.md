# Research 03: memory (allocators, arenas, handoff, out-of-memory)

**Question.** How the C++ owns, hands over, counts and runs out of memory, and what the Rust design does instead. This feeds three items of PLAN.md section 6, "Step 1": "the arena and handoff rules", "the out-of-memory policy exactly as Decision 12", and the memory part of "how island memory is charged on macOS".

**Base and method.** The source is /Users/colin/seekdb-dev/migrate-to-rust/src, identical to 834bbee1e (`git diff --quiet 834bbee1e -- src` succeeds). "N lines / M files" means `git grep -n -P '<pattern>' -- 'src/*' | wc -l` (and `-l` for files) from the repository root, with the pattern given beside the count. class_bases.py (in migration/design/evidence/03-memory/) finds subclasses from class heads; it misses unusual heads and has a few false positives, so its numbers are approximate. "Evidence Cn" is claim n of the gap-oom-policy-conflict area of migration/feasibility/evidence-full.md. Crate facts come from the sources in ~/.cargo/registry. Nothing was built or run.

---

## 1. What the C++ does today

### 1.1 One allocator interface, several kinds of allocator

`ObIAllocator` (src/oblib/lib/alloc/ob_iallocator.h:66-122) has `alloc(size)`, `alloc(size, attr)` and `free(ptr)`; `realloc` returns nullptr unless overridden, `total()`/`used()` return 0, and `reset()`/`reuse()` do nothing. It is in 2,498 of the 6,644 code files (`git grep -l -w ObIAllocator` over .h/.cpp/.ipp/.c/.hpp/.cc/.def), on 6,317 lines as `ObIAllocator\s*&` and 2,246 as `ObIAllocator\s*\*`. class_bases.py finds 62 classes derived from it.

| Kind | Main types | Frees one object? | Limit |
|---|---|---|---|
| Arena | `ObArenaAllocator` (page_arena.h:975-1079; 2,015 lines / 774 files); `ObSafeArenaAllocator` (page_arena.h:1081), a spin lock around an arena (29 lines / 9 files) | No: `free` is empty ("use clear() instead"); memory returns on `clear`/`reset`/`reuse` or the destructor | None of its own; pages can come from any `ObIAllocator`, budgeted ones included (`ModulePageAllocator(ObIAllocator &)`, page_arena.h:101-106) |
| Heap-style | `ob_malloc`/`ob_free`/`ob_realloc` (ob_malloc.h:99-176), `ObMalloc` (:380), `MemoryContextMalloc` (:418), `TrackedAllocator` (:222-359, a header per allocation) | Yes, through the same allocator | None |
| FIFO and slice | `ObFIFOAllocator`, `ObConcurrentFIFOAllocator`, `ObLfFIFOAllocator`, `ObVSliceAlloc`, `ObSliceAlloc`, `ObSmallAllocator` | Yes | `ObBlockAllocMgr::alloc_block` (ob_block_alloc_mgr.h:38-50): add to `hold_`, compare with `limit_`, undo and return NULL when over |
| Module allocators | `ObLogAllocator` (ob_log_allocator.h:82), `ObIOAllocator` (ob_io_struct.h:122), `ObVectorAllocator` (ob_vector_allocator.h:48), the memstore's `ObFifoArena` (ob_fifo_arena.cpp:92-175), tx-data and MDS allocators | Yes | Section 1.8 |
| Object pools | `op_alloc`/`op_free` (29 / 35 lines); the meta-object pools behind `ObMetaObjGuard` (ob_meta_obj_struct.h:159) | Back to the pool | Pool size |

Objects are built by placement new on 2,581 lines / 681 files (`new\s*\(\s*[^)]*\)\s*[A-Za-z_:][\w:<>, ]*\s*[\(;{]`; the report's 2,741 used another pattern) and destroyed by explicit destructor calls on 1,278 / 498 (`(->|\.)\s*~\w+\s*\(\s*\)`). `OB_NEW*` is on 662 lines, `OB_DELETE*` 138, `ob_malloc(` 182, `ob_free(` 268.

### 1.2 Labels and ctx ids; no tenant ids

`ObMemAttr` is a label, a ctx id, a sub-ctx id and a priority (alloc_struct.h:128-149); labels are at most 15 characters (static assert, alloc_struct.h:82-86). There is no tenant id: a tenant argument to `ObMemAttr(`, `MTL_ID()`, `OB_SYS_TENANT_ID` and `OB_SERVER_TENANT_ID` all have 0 hits in src, and "tenant" does not occur in lib/alloc, lib/allocator or lib/rc.

- **Ctx ids:** 8 real ones (ob_mod_define.h:17-26: DEFAULT_CTX_ID, PLAN_CACHE_CTX_ID, WORK_AREA, GLIBC, CO_STACK, KVSTORE_CACHE_ID, META_OBJ_CTX_ID, VECTOR_CTX_ID) and 11 aliases of DEFAULT_CTX_ID (:480-490); `ObCtxIds::` on 461 lines.
- **Labels:** 335 `LABEL_ITEM_DEF` entries, used through `ObModIds::` on 776 lines, plus about 602 distinct string-literal labels on 842 lines (`(ObMemAttr|ObArenaAllocator|ObMalloc|set_label|set_mem_attr|ObLabel)\s*\(\s*"[^"]{1,40}"`). `ObMemAttr` is on 1,505 lines / 600 files.

The default build uses the bundled jemalloc (chosen when no backend is named, ob_malloc.cpp:143-151), and there `ob_malloc` ignores the attribute (ob_malloc.h:117-125). So labels and ctx ids are not counted per allocation, and ctx limits are enforced on neither backend (evidence C2). `__all_virtual_memory_info` reads per-label numbers from the ctx allocators (ob_all_virtual_memory_info.cpp:140-152), which the jemalloc path bypasses; what it shows on this Mac was not checked (assumption: few or no rows).

### 1.3 Memory contexts and the current context

`lib::MemoryContext` (src/oblib/lib/rc/context.h, 873 lines) is a tree; each node owns an arena, a locked view of it and a heap-style allocator (context.h:304-460). On jemalloc `hold()` is heap bytes plus arena pages (context.h:366-391); `tree_mem_hold()` adds the children (:600-613). `CURRENT_CONTEXT` is a thread-local current node (:46, :741-744). Uses: `CREATE_CONTEXT(` 71 lines, `DESTROY_CONTEXT(` 79, `WITH_CONTEXT(` 30, `CREATE_WITH_TEMP_CONTEXT(` 23, `CURRENT_CONTEXT` 120, `ROOT_CONTEXT` 16.

- **The current context decides where memory goes, and no signature shows it.** `ObArray` with `auto_free` binds to `CURRENT_CONTEXT` at its first allocation (ob_array.h:484-492; also ob_raw_se_array.h:197). `ObPhysicalPlan::set_field_columns` switches into the plan's context so its arrays land there (ob_physical_plan.cpp:266-268, 297-298). A temp context is a child of the current one (context.h:833-845).
- **Each request has a context tree.** The worker makes a temp context per request and registers it with the query memory tracker (ob_th_worker.cpp:240-245); each query attempt gets another "to avoid memory dynamic leaks caused by query retry or too many multi-query items" (obmp_query.cpp:467-474).
- **Each plan cache object owns a context** (PLAN_CACHE_CTX_ID, a label per namespace), made in `ObLCObjectManager::alloc` (ob_lib_cache_object_manager.cpp:47-86) and destroyed after the object's destructor runs inside it (:163-172).

### 1.4 Who frees what

| Memory held by | Freed by | Failures in the last year (report §2; each commit present, `git show -s`) |
|---|---|---|
| An arena or a context's arena | Reset, clear or destroy of the arena, all at once; destructors of objects placed there are called by hand, if at all | dd899675c: schema arena reset while an async fallback schema was built from it. 62b9d1a81: parallel tasks shared one arena that is not thread-safe. 38054020d: PL memory kept growing in a long-running SQL |
| A heap-style or FIFO allocator | An explicit `free` through the same allocator | 328253969: worker double free |
| A hand-counted object | The last `dec_ref`: it frees the object, returns it to a pool, or the object destroys itself | e66776e2d/6da784ac1: plan-cache object read after release, dangling PS-cache item. 9d24b8807/9bb15300f: task freed while still queued |
| A cache | The cache, once hazard versions or QClock show no reader is left | 54123f13e: retire bypassed hazard-version protection |
| A queue or a closure | The receiver, by convention | bc31e03f4/b4ad89151: PX closure captured a buffer the sender freed. eda242c9a: async DDL task used request-arena memory |

### 1.5 Borrowed views

- **`ObString`** is `buffer_size_`, `data_length_` and `ptr_` (ob_string.h:794-796) with no owner. `assign_ptr` makes a view ("`buffer_size_ = 0` … means I do not hold the buf, just a ptr", ob_string.h:203-211); `ob_write_string` copies into an allocator (:806). `\bObString\b` is on 16,945 lines / 1,749 files; `^\s*(common::)?ObString\s+\w+_\s*;`, a view stored as a member, on 1,041 lines / 270 files; `assign_ptr(` 946 lines; `ob_write_string(` 598.
- **`ObDatum`** is a union of pointers plus a packed length/null/flag word (ob_datum.h:106-131, 134-163, 169); 5,313 lines / 1,099 files. Datums sit in per-execution frames at offsets set by the code generator (ob_expr.h:367-416) and point into frames, storage rows or cached blocks.
- **`ObObj`** 6,401 lines; `ob_write_obj(` 96. `deep_copy(` is on 1,019 lines / 413 files; 90 lines / 55 files use the `deep_copy(char *buf, int64_t …)` form, which copies into a buffer the receiver owns.

A view stored in a member outlives its arena whenever the struct does, and nothing in the type says so.

### 1.6 Memory handed to other threads, tasks and caches

- **Caches:** a KV-cache value copies itself into cache memory through `ObIKVCacheValue::deep_copy(char *buf, int64_t buf_len, ObIKVCacheValue *&value)` (ob_kvcache_struct.h:55-61; 26 overriding lines / 24 files); readers hold an `ObKVCacheHandle` (ob_kv_storecache.h:281).
- **Pools and task frameworks:** `ObSimpleThreadPool::push(TaskType *task)` / `handle(TaskType *task)` (ob_simple_thread_pool.h:80, 112) pass a raw pointer, ownership by convention. class_bases.py counts 12 `ObSimpleThreadPool` subclasses, 33 `ObITask`, 16 `ObIDag`, 63 `ObTimerTask`, 6 `ObAsyncTask`.
- **Parallel execution:** src/sql/engine/px is 121 files / 41,158 lines and src/sql/dtl 42 / 9,995 (`git ls-files <dir> | grep -E '\.(h|cpp|ipp)$' | xargs cat | wc -l`). DAS parallel tasks copy their input into a fresh context under `ROOT_CONTEXT` (ob_das_parallel_handler.cpp:137-143). `ALLOC_THREAD_SAFE` contexts, which several threads allocate from, appear on 9 lines / 8 files outside context.h.

So memory is handed over either as a copy into receiver-owned memory or as a pointer with a hand-kept count, and the types do not say which.

### 1.7 Hand-counted references and handles

Recount at 834bbee1e with the patterns of evidence-full.md:1933:

| What | Pattern | Lines / files |
|---|---|---|
| inc/dec calls | `\b(inc_ref\|dec_ref)\w*\s*\(` | 441 / 127 |
| Handle classes | `class \w+Handle\b` | 130 / 97: 62 definitions of 57 distinct names, 68 forward declarations |
| Handle members and locals | `\bOb\w*Handle\s+\w+;` | 593 / 230 |
| Guard classes | `class \w+Guard\b` | 304 / 198 |
| Atomic count updates | `ATOMIC_\w+\(\s*&\s*(ref_cnt_\|ref_count_\|ref_)` | 134 / 56 |
| Transaction-context references | `\b(acquire_ctx_ref\|release_ctx_ref\|revert_trans_ctx\|get_tx_ctx\|revert_tx_ctx)\w*\s*\(` | 190 / 37 |

The 441 lines sit mostly in sql/plan_cache (54), storage/tablet (45), storage/blocksstable (43), oblib/lib (36) and share/io (32). Most-used handle types: `ObSchemaGetterGuard` 1,967 lines / 396 files, `ObTabletHandle` 795 / 183, `ObTableHandleV2` 295 / 78. `ObLSHandle` no longer exists (0 hits), so the report's "1,122 lines in 186 files" for three types is 1,090 / 224 for two.

| Kind | Example | On the last release |
|---|---|---|
| Return to a pool or allocator | `ObMetaObjGuard<T>`, behind `ObTabletHandle` (ob_tablet_handle.h:37) | `obj_pool_->free_obj(obj_)`, or `reset()`, `~T()`, `allocator_->free` (ob_meta_obj_struct.h:355-384); a copy logs an error if the count is below 2 (:324), a hold over 2 hours a warning (:184-186, :364) |
| Destroy itself | `ObEmbeddingTask` | `this->~ObEmbeddingTask(); ob_free(this)` (ob_vector_embedding_handler.cpp:921-930) |
| Two counters | `ObIOResult` | `result_ref_cnt_` and `out_ref_cnt_` (ob_io_define.h:431-432) |
| Versioned slot | `ObSchemaMgrItem` with `ref_cnt_` (ob_schema_mgr_cache.h:65-95) | The guard hands out `const T*&`, valid while it lives (ob_schema_getter_guard.h:248-270) |
| Hand-written reclamation | KV-cache hazard versions (ob_kvcache_hazard_version.cpp, 362 lines); QClock (35 lines / 7 files); retire station (ob_retire_station.h, 279 lines); hazard refs (ob_hazard_ref.h, 317 lines); memstore pages wait for quiescence (ob_fifo_arena.cpp:67, :166) | Freed once no reader can still see it |

Some of the 57 names count nothing: `ObSignalHandle` (ob_signal_handle.h:61), `TriggerHandle` (ob_trigger_handler.h:30) and `ForeignKeyHandle` (ob_table_modify_op.h:38) wrap services or helpers, so the inventory classifies each name.

### 1.8 Where memory can really run out, and what the client sees

4,737 lines assign `OB_ALLOCATE_MEMORY_FAILED` (`=\s*(::)?(oceanbase::)?(common::)?OB_ALLOCATE_MEMORY_FAILED\s*;`) and 78 compare against it in either order. On the default build nearly all guard a jemalloc NULL that does not come back in practice (evidence C1, C14; C25 is an assumption about the OS). Of the 6,470 `OB_FAIL(… push_back(…))` lines, some fail for other reasons: `ObFixedArray` capacity, deep-copy errors (evidence C23). About 50 sites sit behind a real budget or a logical limit (evidence C14). The ones Decision 12 names, rechecked at 834bbee1e:

| Owner | Limit | Where it fails | Result |
|---|---|---|---|
| clog | `total/100*CLOG_MEM_LIMIT_PERCENT` (ob_log_allocator.cpp:248-258) | 5 task allocations in palf/log_engine.cpp (876-877 to 966-967) | -4013 |
| replay | `min(total/100*REPLAY_MEM_LIMIT_PERCENT, REPLAY_MEM_LIMIT_THRESHOLD)` (same lines) | Replay task allocation | -4013 turned into OB_EAGAIN and retried (ob_tx_replay_executor.cpp:696-703) |
| IO allocator | `memory_limit` of its FIFO (ob_io_struct.cpp:176-182) | Write buffer at request init (ob_io_define.cpp:866), read buffer at prepare (:1129) | -4013; request and result objects retry until the IO timeout (ob_io_manager.cpp:731, 767, 933-985) |
| KV cache store | `kvcache_memory_limit`, default 40% of memory_budget (ob_server_config.cpp:46, 205-215) | `reserve_store_size`: CAS reservation, then one synchronous wash under `wash_out_lock_` (ob_kvcache_store.cpp:915-961) | -4013 (:922, :955), reaching reads through `put_cache_block` (ob_micro_block_cache.cpp:442-446) |
| KV-cache handle pool (logical) | The preallocated `mb_handles_pool_` | `pop_mb_handle_with_recovery`, after supply, purge, reclaim and one wash (ob_kvcache_store.cpp:979-1032) | -4013 (:1030) |
| Micro block cache | A 4 GiB `ObConcurrentFIFOAllocator` (ob_micro_block_cache.cpp:1006-1009; ob_micro_block_cache.h:509), handed out as a plain `ObIAllocator*` (ob_micro_block_cache.cpp:1113-1118) | IO callback buffer, 3 retries sleeping 100 ms × i (ob_micro_block_cache.cpp:396-402; .h:292-293) | -4013 |
| Vector module | `vector_memory_limit`, default 50% of effective memory (ob_server_config.cpp:48, 225-231) | `ObVectorMemContext::alloc` (ob_vector_allocator.cpp:131-155) | The caller gets NULL; the -7603 set at :142 stays in a local `ret` |
| vsag (logical) | — | `NO_ENOUGH_MEMORY` mapping (ob_vsag_adaptor.cpp:61-62) | -4013 |
| IVF cache (logical) | The vector limit | ob_vector_index_ivf_cache_mgr.cpp:140-164 | -4013 (:163) |
| Query memory tracker | `memory_budget/100*query_memory_limit_percentage` (ob_memory_tracker.cpp:29-35; default 32, ob_parameter_seed.ipp:1108) against the request tree's `tree_mem_hold()` | `CHECK_MEM_STATUS`/`TRY_CHECK_MEM_STATUS` at ob_th_worker.cpp:333, ob_exec_context.cpp:679, ob_select_resolver.cpp:1006, ob_raw_expr.h:5077, ob_transform_rule.cpp:287, parse_node.c:186 | -11049 |
| Memstore full | `memstore_memory_limit`, default 50% of memory_budget (ob_parameter_seed.ipp:92-94), minus a replay reserve | `check_memstore_full_` (ob_memstore_freezer.cpp:1059-1119) before DML (ob_access_service.cpp:655-657); inner tablets skip it | -4030 |
| Temp-file write buffer pool | `get_memory_limit()` | ob_tmp_file_write_buffer_pool.cpp:176-196 | -9124 |
| SQL work areas | `memory_budget/100*ob_sql_work_area_percentage` (ob_sql_memory_manager.cpp:807-816; default 5, ob_system_variable_init.json:1798-1811) | The operator's memory processor | A spill, not an error; the only -4013 is a 1-byte dummy allocation (ob_sql_mem_mgr_processor.cpp:100-104) |
| Hash-join partition depth (logical) | 64 bits of partition shift | ob_hash_join_op.cpp:838-843; ob_hash_partitioning_infrastructure_op.h:1699-1700 | -4013 ("remind user to increase memory") |

-4080 is an internal signal, not a user error: the PDML flush (ob_pdml_op_batch_row_cache.cpp:166, 317) and row stores with dumping off (ob_temp_block_store.cpp:1099, ob_chunk_row_store.cpp:487). memory_budget defaults to 80% of effective memory, at most that memory minus 1 GiB and at least 1 GiB (ob_server_config.cpp:45, 190-203; alloc_func.h:34): about 19 GiB on this 24 GiB Mac, so no budget comes near its limit in the 272 cases (assumption from the formula; the cases were not checked one by one).

What the client sees (src/share/ob_errno.def): -4013 "No memory or server runtime memory limit reached", SQLSTATE HY001 (:83); -4030 "Server runtime memory limit exceeded" (:100); -9124 "fail to allocate a tmp file page" (:1740); -7603 "Vector index memory usage exceeds user defined limit '%d'M." (:1680); -11049 "Exceed query memory limit (mem_limit=%ld, mem_hold=%ld), please check whether the query_memory_limit_percentage configuration item is reasonable." (:1880), filled with the limit and the bytes held (ob_memory_tracker.cpp:51). No .test or .result file expects any of them (evidence C18), and allocation-failure injection (EN_4) exists only on obmalloc and in `ObPLAllocator1` (evidence C21).

### 1.9 What is counted on this Mac today

Counters that exist on the jemalloc build and change behavior: the memory-context hold, read by the -11049 check; the WORK_AREA tracker, the only registered resolver (ob_sql_memory_manager.cpp:430-431), used by `ModulePageAllocator` (page_arena.h:116-140) and `ObFIFOAllocator` (ob_fifo_allocator.cpp:76), whose bytes decide spilling (the sort reads its context's bytes at ob_sort_op_impl.cpp:1024-1028); each budget's `hold_`; the KV cache's store size; the memstore quota; the vector hold; the IO allocator.

Not counted: per-label and per-ctx totals (1.2), and what third-party code takes from malloc. macOS builds no malloc hook (src/oblib/lib/CMakeLists.txt:34-41; src/observer/CMakeLists.txt:17-22); the jemalloc zone is made the default zone instead (main.cpp:645-653).

No mysqltest case reads rows from a memory virtual table: grepping tools/deploy/mysql_test/*.test for a `from` naming any memory table of src/observer/virtual_table (memory info, ctx memory, malloc sample, memory context, memstore, schema memory, SQL work area, storage meta, vector memory, KV cache, DTL) or its gv$/v$ view finds 0 lines. Only names and columns appear, in information_schema.result, show_sys_tables_in_sys.result and the desc_sys_views_* results.

---

## 2. Constraints from the decisions and the plan

- **Decision 12 (b), exact text:** "One policy on every platform: a general out-of-memory aborts the process; typed errors stay at the named budget owners (clog, replay, the IO allocator, the KV cache store, the micro block cache, the vector module, the query memory tracker, memstore full, the temp-file write buffer pool, plus the SQL work-area spill) and for the logical -4013 errors (hash-join partition depth, the KV-cache handle pool, the IVF cache, vsag NO_ENOUGH_MEMORY); fallible (try_reserve-style) collections only there and in buffers whose size a client controls." Its notes: "On the later Windows/Android/wasm targets and in the library form (Decision 7), a general OOM ends the whole process or host, so their budget owners must be sized so that general OOM stays rare."
- **Decision 14 (b):** "#![forbid(unsafe_code)] everywhere except named crates (island shims, SIMD kernels, IO buffers, thin wrappers over vetted reclamation crates)".
- **Decision 16 (a):** stable only, "allocator-api2 rather than the nightly allocator_api".
- **Decision 6 (b):** exact comparison; the only masks are EST.ROWS/EST.TIME in the 40 plan-bearing files and row order for the ~300 hash-order SELECTs, so ties under ORDER BY are compared exactly.
- **Decision 1 (b) and row 1a:** memory use is not a criterion; query speed is gated at 1.2x.
- **Decision 7:** nothing may rule out wasm, Android or Windows. PLAN section 3 adds guidelines the design document adopts or rejects: the global allocator and `panic=abort` stay in the server binary crate, the malloc hook stays out of the engine (Decision 8's notes), and on wasm budget limits come from the 2 GiB heap.
- **Decision 9 (a):** kill-only stops until parity, so the first release needs no orderly teardown.
- **Decision 11 (a):** no C++ data directory is read, so no in-memory layout must match C++ for persistence.
- **Decision 13 (a):** the parser's C core, vsag, S2, share/geo with boost.geometry and ICU regex stay C++; how their memory is charged on macOS is the design document's (PLAN section 8, item 19).
- **PLAN section 3, the core outline:** "per-statement bump arenas borrowed as `&'q`; owned bytes or `Arc<[u8]>` for anything sent to another thread or put in a cache (the fix for the bug table in report §2); … `Arc` with a custom drop for pooled handles"; "arenas of typed ids that mirror today's pointer identity one for one"; "the plan is an immutable `Send + Sync` value"; "per-run state lives in typed column batches owned by operators"; "immutable `Arc` tablet snapshots; epoch reclamation from a vetted crate instead of QClock, the retire station and the KV cache's hazard versions; an `Arc` transaction context".
- **PLAN section 4, family 12:** drive each budget owner into failure and compare the error the client sees (-4013 clog, -4030 memstore, -11049 query tracker, -7603 vector limit, -4013 micro block cache after its retries, -4013 hash-join depth), and check spilling with `ob_sql_work_area_percentage=5`.
- **PLAN section 8:** item 17 (whether the storage estimator's inputs, freeze thresholds among them, stay identical), item 19 (island memory on macOS), items 22 and 23 (coverage depth; false failures from unmasked exactness).

---

## 3. Options for the Rust design

### 3.1 Short-lived memory: lifetimes, indices, reference counting, owned values

| Option | Shape | What the compiler then rejects | Cost | Risk |
|---|---|---|---|---|
| (a) Lifetimes | A bump arena per statement, execution or batch; data borrowed as `&'q [u8]`, `&'q str`, `allocator_api2::vec::Vec<T, &'q Bump>` (bumpalo implements `Allocator` for `&Bump`, bumpalo-3.20.3/src/lib.rs:2514, through its optional allocator-api2 dependency, Cargo.toml:67-70) | A view kept after its arena is reset (`reset(&mut self)`, lib.rs:1073) or dropped; arena data given to anything that needs `'static`; one arena shared by two threads at once (`Bump` has `Cell` fields, lib.rs:393-396, so it is `Send`, :509, but not `Sync`) | A lifetime parameter on every type that holds arena data; allocation stays a pointer bump | Nothing in a bump is dropped (bumpalo README.md:28-36), so a `String`, `Vec` or `Arc` stored there leaks; a struct cannot own its arena and data borrowed from it in safe Rust |
| (b) Indices | Typed ids into `Vec`-backed arenas owned by a context | Nothing needs lifetimes; ids are `Copy`; the owner can be `Send + 'static` | Every access goes through the owner; mutating while reading needs ids copied out first | A stale id, or one from another arena, is a logic error found at run time, if at all |
| (c) Reference counting | `Rc`/`Arc` per node | Lifetime bugs | A count update per clone and drop (atomic for `Arc`) | Cycles leak, and the IR has cycles through `ref_stmt_` and `outer_expr_` (report §3); identity becomes `Arc::ptr_eq` |
| (d) Owned values | `Vec<u8>`, `String`, `Box<T>` | Lifetime bugs | A heap allocation per value and a copy per handoff | Per-row allocations work against the 1.2x speed gate |

### 3.2 Handing memory to threads, tasks and caches

| Option | Shape | Cost | Risk |
|---|---|---|---|
| (a) Owned data only | Every API that runs code elsewhere or keeps data past the call (pool push, DAG and timer tasks, queues, cache insert, PX channels) takes `T: Send + 'static` | A copy per handoff; the main handoffs copy already (KV-cache `deep_copy`, the DAS copy context, the SQC buffer b4ad89151 made owned) | None found; rows 1-3 of the report's bug table (the PX buffer, the request-arena schema, the strings in a queued task) stop compiling |
| (b) Scoped borrowing | `std::thread::scope`, when the caller waits for its workers | No copy | Fork-join code only; PX and DTL are not fork-join |
| (c) Shared arenas | `Mutex<Bump>`, like `ObSafeArenaAllocator` | A lock per allocation | The data is still borrowed, so it cannot cross `'static` anyway; the shape of 62b9d1a81 |

### 3.3 Shared objects and handles

| Option | Fits | Risk |
|---|---|---|
| (a) `Arc<T>` | Tablet snapshots, plan cache objects, schema versions, the transaction context, self-destroying tasks | Atomic counts only |
| (b) `Arc<T>` with a custom drop that returns the object to a pool or releases a budget charge | `ObMetaObjGuard` objects, KV-cache blocks, IO buffers | The drop runs on whichever thread drops last and must not take a lock readers hold |
| (c) Slot index plus generation | Fixed pools whose size is a limit users hit (the KV-cache handle pool) | A stale id is caught by the generation check, at run time |
| (d) Epoch reclamation from a vetted crate (crossbeam-epoch 0.9.21 is in the local registry) | Lock-free maps, the memtable's index | Only inside a named `unsafe` crate (Decision 14); memory is held until readers leave, as with QClock |

### 3.4 Counting memory

| Option | What it counts | Cost | Risk |
|---|---|---|---|
| (a) A counting global allocator with a thread-local current context, like `ObMallocHookAttrGuard` (alloc_struct.h:560-572) | Everything, third-party crates included | A thread-local read and an atomic add per allocation and free | A free on another thread charges the wrong context unless each allocation carries a header, as `TrackedAllocator` does (ob_malloc.h:222-359); needs `unsafe impl GlobalAlloc` |
| (b) Explicit charges at the owners: a budget holds a limit and a counter; a charge taken before memory is reserved is returned by `Drop`; a memory context sums its arenas' `allocated_bytes()` and its charges | What changes behavior today (1.9) | Charges placed by hand at dozens of owners | Uncharged bytes (small `Vec`s in a query) make -11049 trip later than in C++ |
| (c) Per-context jemalloc arenas and statistics | Exact bytes per context | Needs a flag per allocation, which `GlobalAlloc` does not carry | jemalloc only, so nothing on wasm (Decision 7) |

### 3.5 Making allocation failure follow Decision 12

- **(a) General allocation:** Rust's default. `handle_alloc_error` prints a message and aborts; with `panic = "abort"` a capacity overflow aborts too. sql-nio already works this way (rust/sql-nio/src/session_storage.rs:28; rust/Cargo.toml:14-16).
- **(b) Allocators that refuse over a limit:** allocator-api2 collections with one allocator type per budget. `Allocator` is an `unsafe trait` (allocator-api2-0.2.21/src/stable/alloc/mod.rs:101), so each such allocator needs an `unsafe impl` outside Decision 14's named crates.
- **(c) Charge first, then allocate:** the owner takes a charge and returns its typed error when over the limit, then allocates with `try_reserve` (stable on `Vec`; allocator-api2 has `Vec::try_reserve`, vec/mod.rs:921, and `Box::try_new_in`, boxed.rs:390) and maps a failure to -4013; the charge returns when the memory is freed. This is `ObBlockAllocMgr::alloc_block`'s rule, with no `unsafe`.

---

## 4. Recommendation

### 4.1 The arena and handoff rules

1. **Every allocation has one owner, written in its inventory row:** the process (the server context), a session, a cached plan, one statement's compilation, one execution of a plan, one batch, a budget owner, or another thread, task or cache (a handoff). *Why:* the C++ picks the owner at run time through the current context (1.3); naming it lets the borrow checker see it.
2. **Statement compilation** (parse-tree conversion, resolver, rewrite, optimizer): IR nodes in typed-id arenas owned by the statement's compile context (PLAN); names, literal text and parameter values in one per-statement bump arena borrowed as `&'q`. One lifetime per statement; no `Rc` or `Arc` in the IR; all of it goes when compilation ends. *Why:* ids keep pointer identity for `find_item` (PLAN) and survive the IR's cycles; `&'q` bytes avoid a heap allocation per name; today's IR is not freed piece by piece either.
3. **The cached plan owns all its memory:** plan-local typed-id `Vec`s and owned bytes, `Send + Sync + 'static`, shared as `Arc` (PLAN). Code generation copies what the plan keeps out of the statement arena. *Why:* each cached object has its own context today (ob_lib_cache_object_manager.cpp:47-86), and the plan outlives the statement.
4. **Execution:** an operator owns its state, freed at close. Row data lives in column batches owned by the operator that produced them (PLAN); consumers get views that borrow from the batch until the producer's next call. An operator that keeps rows longer (sort, hash join, hash group by, materialization) copies them into its own store, charged to the work area. Expression temporaries go into a per-execution bump arena reset between batches through `&mut`, which proves no view into it remains. *Why:* today's datums point into frames and storage rows (ob_expr.h:367-416) with nothing tying them to their source.
5. **Storage reads:** a scan holds its source (an `Arc` of the tablet snapshot or cached block, or a memtable reference) while it hands out views into it, and the views borrow from that holder. *Why:* 9e2b6ba16 (sstable pointers outlived the iterator) becomes a compile error.
6. **Handoff:** data passed to another thread, task, timer, queue, channel or cache is owned: `Vec<u8>` or `Box<[u8]>` for one receiver, `Arc<[u8]>` or `Arc<T>` for several. The core's spawn, queue, timer, channel and cache-insert functions require `T: Send + 'static`; code whose caller waits for its workers may borrow through scoped threads. *Why:* the bug table (1.4); the main handoffs already copy (3.2).
7. **One thread per arena.** A bump arena is not `Sync`; parallel work makes one arena per task and moves owned results back. `ObSafeArenaAllocator` (29 lines / 9 files) becomes a per-task arena, or a `Mutex<Bump>` where sharing cannot be avoided, with the reason at the site. *Why:* 62b9d1a81.
8. **Long-lived owners** (server, sessions, caches, services, PL packages) keep owned collections that free item by item, and hold an arena only if they reset it at a stated point. *Why:* 38054020d, and the per-attempt context of obmp_query.cpp:467-474, which exists to stop that growth.
9. **Nothing with a destructor goes into a bump arena:** only bytes, numbers, ids and references to data in the same arena, or bumpalo's own `Box`, which drops. *Why:* bumpalo never drops what it holds (README.md:28-36), and the C++ calls destructors by hand on 1,278 lines.
10. **No implicit current context.** `CURRENT_CONTEXT`, `WITH_CONTEXT` and `ObArray`'s binding at first allocation become an explicit argument or a field. *Why:* they choose the owner through a thread-local (ob_array.h:484-492).
11. **Hand-counted objects:** an `inc_ref`/`dec_ref` pair becomes an `Arc` clone and drop; a last release that returns the object to a pool or a budget becomes an `Arc` with a custom drop (PLAN); hazard versions, QClock and the retire station become epoch reclamation from one vetted crate, only inside a named `unsafe` crate and only where a lock-free structure stays (a concurrency question); the IO result's two counters become one `Arc` shared by request and caller, plus a completion signal. Pools that exist only to reuse memory go away (Decision 1 (b); jemalloc keeps per-thread caches). A pool stays where its size is a limit users can hit (the KV-cache handle pool, ob_kvcache_store.cpp:1030) or where Step 2a shows speed needs it.

### 4.2 One translation per construct

| C++ | Rust |
|---|---|
| `ObIAllocator &` for statement or batch data | `&'q Bump` (or the batch's arena) |
| `ObIAllocator &` whose data the callee keeps (cache, plan, session, queue) | None: the callee takes an owned value |
| `ObArenaAllocator` member of a short-lived object | An owned `Bump` in that object, reset through `&mut` |
| `ObString` parameter | `&[u8]` |
| `ObString` member | `&'q [u8]` in statement-scoped structs; `Box<[u8]>`, `Vec<u8>` or `Arc<[u8]>` elsewhere |
| `ob_write_string(alloc, src, dst)` | `bump.alloc_slice_copy(src)`, or `src.to_vec()` / `Arc::from(src)` |
| `deep_copy(char *buf, len, …)` into cache or queue memory | Move an owned value; the owner charges its size |
| `ObDatum` read by an operator | A view into the producer's column batch |
| Placement new plus an explicit destructor call | A constructor and `Drop` |
| `inc_ref`/`dec_ref`, `revert_*` | `Arc` clone; the drop of a guard |
| A handle whose last release returns to a pool or budget | `Arc` with a custom drop |
| Hazard version, QClock, retire station | An epoch guard, inside a named `unsafe` crate |
| `CURRENT_CONTEXT`, `WITH_CONTEXT` | An explicit memory-context argument |
| `ret = OB_ALLOCATE_MEMORY_FAILED` after a general allocation | Removed; the allocation aborts |
| The same at a budget owner or logical limit (1.8) | The owner's typed error, unchanged |

Since each piece of data has one of a few fixed owners, no API needs a generic `A: Allocator` parameter, which also spares one compiled copy per allocator type (per-crate `cargo check` time is Decision 16's reopening condition).

### 4.3 Counting and budgets

1. **Count with explicit charges at the owners** (3.4 (b)). A budget holds a limit and an atomic counter and follows `ObBlockAllocMgr::alloc_block`: add, compare, undo on failure. A charge is a value whose drop returns the bytes. Engine crates implement neither `GlobalAlloc` nor `Allocator`, so no new named `unsafe` crate is needed.
2. **Keep the counters that change behavior (1.9), at the same points:** the request's memory-context total for -11049 (arena `allocated_bytes()` plus charges, kept as a running sum; today's check runs every 1,024 tries, ob_memory_tracker.h:50-51), the work-area bytes that decide spilling, and the budget holds.
3. **Same limits, same parameters, same formulas:** ob_server_config.cpp:190-231, ob_memory_tracker.cpp:29-35, ob_log_allocator.cpp:248-258, the 4 GiB micro block cache limit (ob_micro_block_cache.cpp:1006), ob_sql_memory_manager.cpp:807-816. Parameter names stay, since all_virtual_sys_parameter_stat.result pins them (evidence, public-surface tests).
4. **Bytes per row will differ from C++**: Rust layouts differ and small `Vec`s are not charged, so budgets trip at other row counts, -11049 fires later, and spilling may start elsewhere. No case is near a limit (1.8) and none expects a memory error (evidence C18). Two places can still show it: family 12 (questions 1-3), and a sort that spills in one build only, which can reorder ties (assumption: merging sorted runs orders equal keys differently from one in-memory sort; the dump decision is at ob_sort_op_impl.cpp:1024-1028). So work-area charges count the bytes of the stored rows, not allocator overhead.
5. **Labels and ctx ids** stay as names on the owners that report into the memory virtual tables, with no per-allocation label and no tenant id. The global allocator's statistics may fill server-wide rows; engine crates do not depend on jemalloc (wasm, Decision 7).
6. **The global allocator** is jemalloc as `#[global_allocator]` in the server binary crate (PLAN), the same jemalloc 5.3.1 as the reference (seekdb-jemalloc-sys =0.2.2, deps/external/Cargo.toml:10) with the same `je_malloc_conf` (ob_malloc.cpp:38-42), so the 1.2x comparison does not measure an allocator change. Its wrapper needs one `unsafe impl` (question 4).

### 4.4 Keeping the user-visible memory errors

1. **Decision 12 as written.** General allocation aborts. The owners and logical limits of 1.8 keep their codes, texts and SQLSTATEs from the generated error catalog, raised at the same check points. `try_reserve`-style calls appear only there and in client-sized buffers; a reviewer rejects them anywhere else.
2. **Each owner keeps its behavior around the error** (1.8): replay's OB_EAGAIN, IO retries until timeout, the micro block cache's 3 sleeps, the KV cache's one wash under a lock, the handle pool's recovery steps, and memstore-full rechecked only after the refresh interval while the last check found room (ob_memstore_freezer.cpp:1070-1072). -7603 stays inside `ObVectorMemContext::alloc` (ob_vector_allocator.cpp:142); surfacing it would be a fix, which the kit's BUG rule leaves until after parity.
3. **The 4,737 assignments go away with their host code,** except the about 50 owner and logical sites (evidence C14). Each of the 78 comparisons gets an inventory row: many are retry loops on -4013 (for example ob_kvcache_store.cpp:407, :644), and only those at owners stay.
4. **Client-sized buffers** follow sql-nio: 14 `try_reserve` lines in 8 files, for socket and TLS input, compression output, response batches, statement parameters and login attributes (evidence C17, recounted). SQL functions keep their own size checks, which return their own errors first: REPEAT returns OB_ERR_FUNC_RESULT_TOO_LARGE against `max_allowed_packet` (ob_expr_repeat.cpp:124-127) before it allocates (:136-138); the allocation after such a check is an ordinary one.
5. **Later platforms:** on wasm, budget limits come from the 2 GiB heap (PLAN guideline); since a general out-of-memory ends the process there and on Windows and Android (Decision 12 notes), the owners above are where the sizing that keeps it rare happens.

### 4.5 Island memory on macOS

1. **vsag:** the two `vsag::Allocator` subclasses, `ObVsagMemContext` (ob_vector_allocator.h:71) and `ObVsagSearchAlloc` (ob_vector_index_adaptor.h:237), stay as C++ classes in the island shim; their `Allocate`, `Deallocate` and `Reallocate` call Rust entry points that charge the vector budget and allocate from the Rust heap. `NO_ENOUGH_MEMORY` still maps to -4013.
2. **The parser's C core:** `parse_malloc` and its helpers get a pool pointer (parse_malloc.cpp:27-39, 90). In Rust it leads to the statement's parse arena, charged to the request's memory context; the parser also checks the query tracker at parse_node.c:186 through `try_check_mem_status`, a weak default at :177-178 that src/sql/parser/ob_memory_tracker_wrapper.cpp:22-27 overrides, so the Rust side provides that callback too. The callbacks are leaf functions that return before the parser can `longjmp` (PLAN section 3, item 7); the tree becomes the owned Rust AST and the arena is dropped.
3. **share/geo and S2:** their kept oblib subset uses memory contexts, for example `CREATE_WITH_TEMP_CONTEXT(… "GISModule" …)` at ob_geo_utils.cpp:640, 721, 1857 and ob_s2adapter.cpp:286; these are children of the query's context today (context.h:833-845), so GIS memory counts toward -11049. The subset's page source calls a Rust entry point that takes the caller's memory context as its first argument and charges it.
4. **What third-party code takes with malloc or new** (boost, S2, ICU, vsag's internals) stays uncharged, as on today's macOS build (1.9). Keeping the zone promotion of main.cpp:645-653 is a binary-crate choice (PLAN guideline).
5. **Memory is freed by the side that allocated it.** Rust memory lent to C++ comes back through a Rust release function; C++ objects held by Rust sit in a Rust type whose drop calls the island's release function. *Why:* the Rust heap and the islands' malloc may be different heaps; this rule makes that irrelevant.

### 4.6 Prior-art conventions, adopted or rejected

| Convention | Where | Verdict | Why |
|---|---|---|---|
| `_view`: borrowed, no ownership | abi-naming.md:65 | Adopt for island ABIs, each function stating how long the view lives | The C-boundary form of rules 4 and 5 |
| `_handle`: opaque, released exactly once, `_acquire`/`_release` pairs | abi-naming.md:66 | Adopt; the Rust wrapper's drop calls `_release`, so ownership enforces "exactly once" | 4.5 item 5 |
| Receiver as the first parameter, never a global | abi-naming.md:32; ffi-mechanics.md:23 | Adopt for memory callbacks: the memory context is the receiver | Rule 10; 4.5 item 3 |
| Rust entry points trust pointer/length pairs from C++ | abi-naming.md:65; ffi-mechanics.md:129-137 | Adopt inside island shims only | Those are the crates where Decision 14 allows `unsafe` |
| Generation-scoped leases: C++ holds Rust buffers across calls | nio-abi-contracts.md:23 | Reject for the core | Needed only while C++ requests wrapped Rust sql-nio; in the Rust binary sql-nio is a plain crate (PLAN) and the request owns its bytes |

ffi-mechanics.md has no allocation rule of its own; its only memory-related text is the unchecked-pointer section above.

### 4.7 What Step 2a should measure

- The scan+filter+aggregate query's speed with per-execution arenas and batch views, against the 1.2x gate.
- How many lifetime parameters the narrow path needs (aim: `'q` in the IR and planner, one batch lifetime in the operator interface, none in storage APIs beyond iterator borrows).
- The sort's spill point on family 12's spill scenario, C++ against Rust (4.3 item 4).
- Whether any core crate needs `unsafe` for memory beyond the named crates.

---

## 5. Questions only the developer can answer

1. **-11049 and exact comparison.** The message prints `mem_hold` (ob_errno.def:1880, filled at ob_memory_tracker.cpp:51). The Rust build will not hold the same bytes, so family 12's -11049 scenario can never match byte for byte. (a) Leave it unmasked and sign off the known difference at the final gate; (b) a mask for the `mem_hold` number of this one message, declared in 00b before its second sign-off, which changes Decision 6; (c) compare only the error code there, also a mask. Recommendation: (b), because a check that always fails teaches everyone to skip its result.
2. **Budget thresholds in bytes.** Must budgets trip at the same byte counts as C++ (memstore bytes per row for -4030 and for freezes, work-area bytes for spilling, the query tracker's hold)? That would mean copying C++ memory layouts into the Rust memstore and row stores, against the redesign. Recommendation: the same formulas and check points, not the same bytes, with family 12's scenarios exceeding each limit by a wide margin. This touches PLAN section 8, item 17, since freeze thresholds are an estimator input.
3. **-7603.** Family 12 expects "-7603 from the vector limit", but the code keeps -7603 inside `ObVectorMemContext::alloc`: callers get a null pointer, and on the vsag path the client gets -4013 (ob_vsag_adaptor.cpp:61-62). Confirm that family 12 records whatever the C++ reference returns, and that the Rust build keeps -7603 internal until after parity.
4. **Decision 14 and the global allocator.** A jemalloc `#[global_allocator]` over seekdb-jemalloc-sys needs one `unsafe impl GlobalAlloc`. Add that module of the server binary crate to the named crates, or use an external wrapper such as tikv-jemallocator (not in the local registry; assumption: it builds its own jemalloc rather than seekdb-jemalloc-sys, so the allocator would differ from the reference's)?
5. **Memory virtual tables.** No test reads their rows. Fill them from the Rust counters (numbers differ from C++; per-label rows exist only for owners), or keep them as close as possible to today's jemalloc behavior (assumption: few rows)? Recommendation: fill them from the counters.
