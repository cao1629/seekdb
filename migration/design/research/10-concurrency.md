# Research 10: concurrency and the low level

Input to the design document (migration/RULEBOOK.md), Step 1. Question: how the C++ handles atomics, locks, lock-free structures, thread-local state and the stack today; which rules the Rust tree adopts for atomics, locks, thread-locals and stack growth; whether `stacker` grows the stack on macOS the way `SMART_CALL` does (PLAN.md section 8, item 20); and whether the wasm depth-limit guideline is adopted now (PLAN.md section 6, Step 1).

Everything was measured at 834bbee1e (`git diff --quiet 834bbee1e -- src` succeeds in the worktree). Scripts, raw output and the unpacked stacker 0.1.25 and psm 0.1.32 sources are in migration/design/evidence/conc/: `atomic_census.py` (comments stripped, `#define` lines skipped), `mixed_access.py`, `lock_census.py` and `nesting.py`. Counts marked "lines" come from `git grep` and include comments.

## Summary

- **Atomics.** 2,967 `ATOMIC_*` calls in 462 files, all sequentially consistent or full-barrier except 29. They touch 778 distinct names but about 1,055 distinct (file, field) pairs, and 128 more lines call `.atomic_*()` members of `SCN` and `ObTxSEQ`. Most of these fields are also accessed without the macros somewhere. **Rule:** `Atomic*` field types with `SeqCst` by default. Reference counts, lock words, list links and reclamation clocks go away in the redesign and are not retyped.
- **Locks.** Every OB lock is built on `ObLatch`/`ObLatchMutex`: a futex word holding the writer's OS thread id, spin counts from 206 latch ids, and a global wait queue. Latch statistics are no longer visible to users. There are 479 lock declarations and 2,151 guard objects. **Rule:** parking_lot behind one core module, `Mutex<T>`/`RwLock<T>` owning their data, timed locks keeping each site's error code, no hand-written spin locks.
- **Lock-free code.** About 17K lines and four hand-written reclamation schemes (QClock with RetireStation, ObQSync, HazardRef, the KV cache's hazard versions and pointers). **Rule:** crossbeam channels and queues, arc-swap for published snapshots, crossbeam-epoch only inside Decision 14's named crates, tested with loom.
- **Thread-locals.** 229 declaration lines in 113 files; every `RLOCAL` macro is a plain `thread_local`, and no coroutines remain. **Rule:** state that changes a statement's result is a parameter; `thread_local!` only for a closed list (diagnostics, caches, stack bookkeeping, research 02's warning-buffer slot), const-initialized, without `Drop`.
- **Stack.** 1,137 `SMART_CALL` lines (rewrite 388, resolver 303, optimizer 229, pl 79). Worker threads get 224 KiB on macOS, and the stack switch ran during the 272 judge cases. By source reading, stacker 0.1.25 does on macOS what `SMART_CALL` does: same pthread bounds, switch to a new stack. Its stacks have guard pages, but it has no total cap. The local rustc 1.98.1 links it on this platform. **Rule:** one core wrapper at every `SMART_CALL` site that keeps the total cap and today's `OB_SIZE_OVERFLOW`; on wasm it counts depth instead of growing. The wasm guideline is adopted now, at no cost on native targets.
- **The C++ that stays also checks the stack.** share/geo stays C++ (Decision 13) with 5 `SMART_CALL` sites and 1 stack check, and its cached stack bounds would not describe a stack that Rust grew. The two parser C cores call back into C++ for stack checks (parse_node.c:657, :696; pl_parser_mysql_mode.y:90). After the port, Rust supplies those callbacks and shares the current stack segment with geo.

## 1. What the C++ does today

### 1.1 Atomics: macros on plain fields

The macros are in src/oblib/lib/atomic/ob_atomic.h. Fields stay plain `int64_t`, `bool`, pointers or enums, and each access chooses whether to be atomic.

| Macro family | Definition (ob_atomic.h) | Calls |
|---|---|---|
| `ATOMIC_LOAD`, `ATOMIC_STORE` | `__atomic_load_n`/`__atomic_store_n`, `__ATOMIC_SEQ_CST` (:41, :44) | 1,100 / 498 |
| `ATOMIC_AAF`, `FAA`, `SAF`, `FAS`, `INC`, `DEC` | `__sync_*_and_fetch` / `__sync_fetch_and_*`, full barrier (:55-62, :124-125) | 885 |
| `ATOMIC_BCAS`, `CAS`, `VCAS` | `__sync_bool/val_compare_and_swap` (:67-72, :123) | 275 |
| `ATOMIC_TAS`, `SET` | `__atomic_exchange_n`, `SEQ_CST` (:63-66) | 154 |
| `ATOMIC_ANDF`, `ATOMIC_LOAD64` | `__sync_and_and_fetch`; a 64-bit load through an `int64_t*` cast (:47-53) | 3 + 5 |
| `_ACQ`, `_REL`, `_RLX` variants | the named order (:42-46) | 29 |
| `ATOMIC_ADD_TAG`, `SUB_TAG` | CAS loops that set or clear bits of the MVCC row's flag byte (src/storage/memtable/mvcc/ob_mvcc_row.h:43-58) | 18 |

The first argument is `&name_` on the object's own member in 2,127 calls, a member reached through `.` or `->` in 519, a pointer passed in by a helper in 154, an array element in 81, a local, global or static in 61, and a call result in 25. There are 778 distinct target names, 711 ending in `_`; PLAN's "about 823" (evidence-full.md:1887, method not given) is the same order. The retype works on fields, not names: there are **1,055 distinct (file stem, name) pairs**. Split by name pattern, the calls go to counters, sizes and statistics (1,162), ids, versions, times and SCNs (358), flags and states (281), reference counts (217), list or queue links (168), lock words (140) and others (616). This split is a regex over names, not a reading.

Four more facts shape the rules:
- **Value types with atomic members.** `SCN` has `atomic_load/store/set/get/bcas/vcas` (src/share/scn.h:55-60), and `ObTxSEQ` has `atomic_load` (src/data_plane/api/data_plane/transaction/ob_tx_seq.h:118). Any field of these types can be shared, and 128 lines call `.atomic_*()` on such fields, for example `rec_log_ts_`, `end_scn_` and `commit_version_`. `inc_update`/`dec_update` (ob_atomic.h:97-121) are CAS loops for max and min.
- **The same field is also accessed without the macros.** In 724 of 966 member-style pairs, the field's own files mention it outside an `ATOMIC_*` call on 2,615 lines (`mixed_access.py`, a heuristic that skips declarations, initializer lists and log arguments). A checked example: `ObServerRuntime::stopped_` is written with `ATOMIC_STORE` (src/observer/omt/ob_server_runtime.h:193-194; .cpp:511) and read plainly in `has_stopped()` (.h:197) and at .cpp:457 and :526. In C++ those plain reads are data races.
- **128-bit atomics** have two users. palf's `LSNAllocator` does a 16-byte CAS on `lsn_ts_meta_` (src/logservice/palf/lsn_allocator.h:93; 21 `LOAD128`/`CAS128` lines in lsn_allocator.cpp). `REACH_COUNT_PER_SEC` (src/oblib/lib/utility/ob_macro_utils.h:842-866) is used twice, in ob_tx_ctx.cpp:3054 and :5275.
- **Other low-level forms:** `volatile` 228 lines, `std::atomic` 26 lines, direct `__sync_*` builtins 37 lines, `MEM_BARRIER`/`WEAK_BARRIER` 16/22 lines, `PAUSE()` spin loops 81 lines, `CACHE_ALIGNED` 143 lines, `get_itid()` per-thread slot indexes 66 lines. `ObAtomicReference` (src/oblib/lib/atomic/ob_atomic_reference.h) has no users.

### 1.2 Locks and latches

**One base.** `ObLatchMutex` (src/oblib/lib/lock/ob_latch.h:42-71) and `ObLatch` (:159-253) each wrap a 32-bit word. It holds the writer's id, `GETTID()`, which on macOS is `pthread_threadid_np` (src/oblib/lib/ob_define.h:1916-1920), together with a write bit (1<<30), a wait bit (1<<31) and a reader count below 1<<24 (:248-250). Locking spins `OB_LATCHES[latch_id].max_spin_cnt_` times, yields, and then waits in a global wait queue of 3,079 hashed buckets (:150; ob_latch.cpp:91-135, :212-290). The futex is Linux `SYS_futex`, and on macOS Darwin's private `__ulock_wait`/`__ulock_wake` (ob_futex.cpp:33-77). Every lock call passes a latch id from the 206 `LATCH_DEF` entries of src/oblib/lib/stat/ob_latch_define.h, which set spin counts and label the wait statistics. The views that showed those statistics are removed: `__all_virtual_latch`, `__all_virtual_system_event` and `GV$LATCH` (src/share/inner_table/ob_inner_table_schema_def.py:3058, :3121, :7440).

**The wrappers** are named "spin" but spin and then sleep: `ObSpinLock` and `lib::ObMutex` hold an `ObLatchMutex` (ob_spin_lock.h:48, ob_mutex.h:42), and `SpinRWLock` holds an `ObLatch` (ob_spin_rwlock.h:59). The real spin locks are `ObRowLatch`, the per-row memtable latch, which is `while(!try_lock());` with no pause (src/storage/memtable/mvcc/ob_row_latch.h:37-43), and `ObSmallSpinLock`/`ObByteLock`, which spin with `PAUSE` and then `usleep` (ob_small_spin_lock.h:126-162).

| Kind | Declarations (files) | Guard objects |
|---|---|---|
| `ObSpinLock` | 101 (77) | `ObSpinLockGuard` 317 |
| `lib::ObMutex` | 88 (71) | `ObMutexGuard` 252 |
| `SpinRWLock` | 67 (53) | `SpinWLockGuard` 175, `SpinRLockGuard` 120 |
| `TCRWLock`, directly, plus fields declared as `RWLock`: mostly `common::RWLock`, a typedef of `TCRWLock` (ob_tc_rwlock.h:359); 6 classes typedef `SpinRWLock` as `RWLock` | 23 + 30 | `TCWLockGuard` 75, `TCRLockGuard` 52, and more through typedefs |
| `ObThreadCond` (pthread mutex and cond, ob_thread_cond.h:104-105) | 39 (34) | `ObThreadCondGuard` 134 |
| `ObLatch` | 31 (23) | `ObLatchWGuard` 29, `ObLatchRGuard` 27 |
| `ObBucketLock` (latches picked by key hash) | 15 | `ObBucketHashWLockGuard` 74, `...RLockGuard` 21 |
| `ObByteLock` 12, `ObQSync` 8, `ObQSyncLock` 5, `ObRecursiveMutex` 6, pthread and std types 19, others | 85 | |
| **Total** | **479 in 314 files** | **2,151 in 386 files** |

The lock features that Rust's std `Mutex` lacks:
- **Timeouts** on every lock call (`abs_timeout_us`, ob_latch.h:47-50, :177-183): about 27 calls pass one, and 49 lines use timeout guard types. Each site maps `OB_TIMEOUT` to its own code; for example `ObTabletDDLKvMgr::wrlock` turns it into `OB_EAGAIN` (src/storage/ddl/ob_tablet_ddl_kv_mgr.cpp:174-184).
- **Try-locks:** 15 `try_lock`, 27 `try_rdlock` and 27 `try_wrlock` call lines.
- **Unlock by a named owner.** `unlock(&uid)` exists (ob_latch.cpp:605-615). Its two callers at 834bbee1e unlock on the thread that locked: ob_server_runtime.h:326-340 and ob_tablet_ddl_kv_mgr.cpp:187-191, :319-340. The Obsidian storage note on DDL, LOB and temp files (line 201, written at 073e9b2f1) describes a cross-thread unlock in the DDL clog callback; that no longer holds.
- **Downgrade:** `wr2rdlock` (ob_latch.cpp:586) has no callers.
- **Read-biased locks.** `TCRWLock` counts readers in per-thread slots (`TCRef`, ob_tc_rwlock.h:143-205). It guards the plan cache node (src/sql/plan_cache/ob_i_lib_cache_node.h:214) and the schema manager cache (src/share/schema/ob_schema_mgr_cache.h:148), which are probably read on every query (assumption from their role, not traced).

Also: whichever of two parties comes second frees a clog callback object, decided by `ATOMIC_BCAS` on `the_other_release_this_` (src/storage/ddl/ob_ddl_clog.cpp:34-40; src/storage/ob_sync_tablet_seq_clog.cpp:91, :137); and `CtxLock` holds three `ObLatch` (src/storage/tx/ob_trans_ctx_lock.h:91-93) with `before_unlock`/`after_unlock` hooks (:82-83) so that callbacks run after release.

### 1.3 Lock-free structures and how they free memory

| Structure | Lines | Users |
|---|---|---|
| QClock and RetireStation (src/oblib/lib/allocator/ob_retire_station.h:30-117, :191-261) | 479 with ob_qsync.h | `ObLinkHashMap` (ob_link_hashmap.h:47-119), the KV cache store (ob_kvcache_store.h:215-222), `ObKeyBtree` (ob_keybtree.cpp:1743-1752) |
| `ObQSync`: reader counts in slots; `sync()` waits for zero | (above) | 8 users, among them ob_log_handler.h:367, ob_dchash.cpp:24-26, ob_tc_ref.h:128-129, ob_keybtree.cpp:1757-1759, ob_lock_wait_mgr.h:300-302 |
| `HazardRef` (ob_hazard_ref.h) | 397 | ob_hash.h:709/782, ob_darray.h:748/803 |
| KV cache: hazard versions (`ObKVCacheHazardStation`, used by ob_kvcache_map.h) and hazard pointers (`HazardDomain`, `SharedHazptr`) | 2,809 with the map | src/share/cache; the use-after-free fixed in 54123f13e was here (feasibility section 2) |
| Queues: `ObLinkQueue`/`ObSpLinkQueue`, `ObLightyQueue`, `ObFixedQueue`, `ObMsQueue`, `ObCoSeqQueue`; `ObAtomicList` | 2,120 + 302 | 39, 21, 30, 14 and 6 lines of use; `ObAtomicList` 11 |
| Maps: `ObLinkHashMap`, `ObDCHash`, `ObLightHashMap`, ob_hash.h/ob_darray.h | 775 + 485 + 542 + 1,602 | 22, 2, 19 lines |
| Allocators: `ObSliceAlloc`, `ObVSliceAlloc`, `ObLfFIFOAllocator`, `ObConcurrentFIFOAllocator` | 1,184 | 24, 28, 12, 37 lines |
| `TCRef`, `TCRWLock`, `ObQSyncLock`, `ObBucketQSyncLock` | 1,171 | see 1.2 |
| `ObKeyBtree`, the memtable index | 2,757 | memtable |
| palf `LSNAllocator` (128-bit CAS), `FixedSlidingWindow` | 639 + 502 | palf |

Together with the latch and futex code (1,463 lines), that is about 17.2K lines (`wc -l` over the files named).

### 1.4 Thread-local state

All six `RLOCAL` macros expand to `thread_local` (src/oblib/lib/coro/co_var.h:33-38), as does `TLOCAL` (ob_macro_utils.h:926). There are 229 declaration lines in 113 files (59 `thread_local`, 37 `__thread`, 124 `RLOCAL*`, 9 `TLOCAL`) and 41 uses of the `ObDITls` per-thread singletons (`GET_TSI`). By what they hold:

| What the thread-local holds | Examples |
|---|---|
| State that changes a statement's result, reached without a parameter | `Worker::self_` behind `THIS_WORKER` (src/oblib/lib/worker.h:115; 396 lines in 142 files); the memory-context stack `Flow::g_flow` behind `CURRENT_CONTEXT` (src/oblib/lib/rc/context.h:732, :753; 120 lines); the warning buffer (src/oblib/lib/oblog/ob_warning_buffer.cpp:24), fed by 2,809 `LOG_USER_ERROR` and 127 `LOG_USER_WARN` calls; `ObTimeoutCtx` (ob_timeout_ctx.cpp:62; 100 lines); the query memory tracker (src/sql/executor/ob_memory_tracker.h:66); the interrupt checker (src/share/interrupt/ob_global_interrupt_call.h:298-305); `g_copy_context` (src/sql/das/ob_das_task.h:96); `g_expr_ser_array` (src/query/api/query/engine/expr/ob_expr.h:577); `ObDataCheckpoint::freeze_source_` (src/storage/checkpoint/ob_data_checkpoint.h:225-226); `Thread::is_doing_ddl_` (src/oblib/lib/thread/thread.h:116); the memory label of `ObMallocHookAttrGuard` (src/oblib/lib/alloc/alloc_struct.h:560-573; 103 lines) |
| Diagnostics | the trace id `ObCurTraceId` (src/oblib/lib/profile/ob_trace_id.h:318; 375 lines); the SQL text for crash logs (src/oblib/lib/signal/ob_signal_struct.h:90); logger state (ob_log.h:852-860); held-latch arrays (ob_latch.h:218-220); the optimizer tracer (src/sql/ob_optimizer_trace_impl.h:67) |
| Caches and buffers that do not change results | format buffers, random generators, thread names (ob_define.h:1939-1958), the hazard-pointer cache (ob_kvcache_hazard_domain.cpp:60), the per-site statics of `TC_REACH_TIME_INTERVAL` (42 uses) and `REACH_THREAD_TIME_INTERVAL` (47) |
| Worker identity; stack bookkeeping | `ObDagWorker::self_` (ob_dag_scheduler.h:816), `Thread::current_thread_` (thread.h:120), `ThreadPool::thread_idx_` (threads.h:133); `g_stackaddr`, `g_stacksize` (ob_common_utility.cpp:32-33), `all_stack_size` (ob_smart_call.cpp:85) |
| The parser's C core (stays C, Decision 13) | `error_msg` and the `jmp_buf` (src/sql/parser/sql_parser_base.c:36, :104-106) |

`REACH_TIME_INTERVAL` (244 uses) is a process-wide static per call site, updated by CAS (ob_macro_utils.h:799-811), not a thread-local.

### 1.5 Coroutines

None remain. Besides the `RLOCAL` macros: `USE_CO_LATCH` is `false` with no readers (ob_latch.cpp:24); `ObPxCoroWorker::run` only copies its arguments and `exit` returns `OB_NOT_INIT` (src/sql/engine/px/ob_px_worker.cpp:52-66); "CoStack" is only a memory label (protected_stack_allocator.cpp:102); src/ has no `makecontext`, `swapcontext` or boost.context. Requests run on blocking worker threads, and sql-nio uses mio with plain threads (rust/sql-nio/Cargo.toml).

### 1.6 The stack: thread stacks, SMART_CALL and the checks

**Thread stacks.** The parameter `stack_size` defaults to 256K, in the range [256K, 20M] (src/share/parameter/ob_parameter_seed.ipp:730-731). At startup the server sets `global_thread_stack_size = max(256K, stack_size) - 16K - 17K` and, on Apple, rounds it up to the page size (src/observer/ob_server.cpp:1966-1974; threads.cpp:27-29; alloc_assist.h:121). The request workers are `lib::Threads` (`ObThWorker : lib::Worker, lib::Threads`, src/observer/omt/ob_th_worker.h:46-47). So on this Mac, with 16 KiB pages, a worker gets 229,376 bytes (224 KiB). Linux allocates the stack itself with a guard page; macOS and Android only call `pthread_attr_setstacksize` (src/oblib/lib/thread/thread.cpp:80-118).

**`SMART_CALL(f)`** (src/oblib/lib/utility/ob_smart_call.h:112-138):
1. It asks `check_stack_overflow(reserve)`. The reserve is 64 KiB, or 192 KiB for `SMART_CALL_LARGE` (:30, :36).
2. If enough stack is left, it calls `f` directly.
3. Otherwise it allocates a 2 MiB extension minus 34 KiB (:29) with `ob_malloc`, labelled "CoStack". The extension has **no guard page** (`smart_call_alloc` passes `guard_page = false`, src/oblib/lib/thread/protected_stack_allocator.cpp:69-72).
4. It records the new bounds in the thread-local `g_stackaddr`/`g_stacksize` and moves SP with hand-written assembly for x86_64, Win64 and aarch64 (ob_smart_call.cpp:28-83).
5. The per-thread total, which starts at the thread's own stack size, may not exceed 10 MiB (`ALL_STACK_LIMIT`, :28). An extension that would pass it fails with `OB_SIZE_OVERFLOW` (:82-83), which is -4019 "Size overflow" (src/share/ob_errno.def:89). A failed allocation returns `OB_ALLOCATE_MEMORY_FAILED` (:84-85).
6. On other architectures it is a plain call (:66-68).

On this Mac that allows four extensions, about 8.1 MiB of usable stack in all. `OB_STACK_OVERFLOW` (-4385, ob_errno.def:351) has no users.

**The stack bounds come from pthread.** `get_stackattr` caches them per thread, on macOS from `pthread_get_stackaddr_np` and `pthread_get_stacksize_np` (ob_common_utility.cpp:85-130, :111-119). `check_stack_overflow` reports an overflow when less than the reserve is left, 32 KiB by default (:35, :45-83), and returns `OB_ERR_UNEXPECTED` ("stack incorrect params") when the stack pointer lies outside the cached bounds (:66-69).

| Where the stack is checked | Count | What happens when the stack is short |
|---|---|---|
| `SMART_CALL` / `SMART_CALL_LARGE` | 1,131 + 6 lines in 153 files: sql/rewrite 388, sql/resolver 303, sql/optimizer 229, pl 79, oblib/common 36 (JSON, XML), sql/engine 20, sql top level 20, share/geo 5, others 57. `SMART_CALL_LARGE` wraps plan generation (src/sql/ob_sql.cpp:1236, :1867, :1998), PL package compilation (src/pl/ob_pl_package_manager.cpp:930, :1041) and schema batch fetch (src/share/schema/ob_schema_cache.cpp:984) | switch stacks, up to the 10 MiB cap |
| `check_stack_overflow(...)` | 106 lines in 53 files (resolver 31, rewrite 28, optimizer 17, engine 10) | `OB_SIZE_OVERFLOW` at most sites (e.g. ob_raw_expr_resolver_impl.cpp:365-367). Some skip the work silently: `ObRawExpr::get_name` returns success with no text (ob_raw_expr.cpp:324-326) |
| Operators: `check_stack_once` on the first `get_next_row`/`get_next_batch` (src/sql/engine/ob_operator.cpp:517-526, :885, :1021) | every operator | `OB_SIZE_OVERFLOW`, with no switch |
| Expressions: the code generator marks every 16th level of an expression tree (`STACK_OVERFLOW_CHECK_DEPTH = 16`, src/sql/code_generator/ob_static_engine_expr_cg.h:74; .cpp:1214-1262), and `eval` checks there (src/query/api/query/engine/expr/ob_expr.h:1055) | per plan | `OB_SIZE_OVERFLOW`, with no switch |
| `SMART_VAR` / `HEAP_VAR` families | 728 lines in 216 files; 213 are `ObMySQLProxy::MySQLResult` | `SMART_VAR` puts a large local on the heap when the stack lacks room for it or more than 256 KiB is in use (ob_smart_var.h:29-56). `HEAP_VAR` always puts it on the heap |

Explicit semantic limits are separate and are kept exactly: `OB_MAX_SUBQUERY_LAYER_NUM = 64` (src/oblib/lib/ob_define.h:304, checked at ob_select_resolver.cpp:3598 and two other sites), `JSON_DOCUMENT_MAX_DEPTH = 100` (src/oblib/common/json_type/ob_json_parse.h:43), `OB_XML_PARSER_MAX_DEPTH = 1000` (src/oblib/common/xml/ob_xml_parser.h:42), and the system variables `cte_max_recursion_depth` and `max_sp_recursion_depth` return their own error codes.

The parsers' C cores, which stay C (Decision 13), check the stack through callbacks into C++:
- `parsenode_hash` and `parsenode_equal` call `check_stack_overflow_c()` (src/sql/parser/parse_node.c:657, :696) and set `OB_PARSER_ERR_SIZE_OVERFLOW`. The callback is defined in src/sql/ob_sql_utils.cpp:1963-1971. Its callers are the ParseNode-keyed hash maps (src/oblib/lib/hash/ob_hashutils.h:734-832) and ob_inlist_resolver.cpp:670.
- The PL grammar calls `obpl_parser_check_stack_overflow()` (src/pl/parser/pl_parser_mysql_mode.y:90), which is defined in src/pl/parser/ob_pl_parser.cpp:28-31.
- The SQL grammar sets no `YYMAXDEPTH`, and its `check_parser_size_overflow` macro (sql_parser_base.h:614) is not used in the grammar.

### 1.7 How deep the judge's statements go

- Over the 40,676 statements in the 283 tracked .test files (`nesting.py`), the deepest parenthesis nesting is 20 (tools/deploy/mysql_test/t/sqlancer_optimizer_regressions.test:19), and the most `(select` in one statement is 28 (test_suite/subquery/t/subquery.test:304).
- The only `--error 4019` cases are array size limits (test_suite/array/t/array_arith_op_mysql.test:212-219), not stack exhaustion.
- Yet the stack switch **does run** under the 272 cases. In the merged coverage of both passes, `jump_call` is executed: ob_smart_call.cpp has 1/1 functions and 19/19 lines covered, and ob_smart_call.h 5/5 functions and 37/39 lines (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.summary.json). That was measured at 076eb309b on the coverage-instrumented build. These files, ob_sql.cpp and ob_server.cpp are identical at 834bbee1e.
- A likely reason, not measured: `SMART_CALL_LARGE` switches once more than 32 KiB of a 224 KiB stack is in use, so plan generation on a plan-cache miss may often run on an extension. Execution counts were not extracted: that means loading the 669 MB instrumented binary into llvm-cov while judge runs occupy the machine.

### 1.8 Does stacker grow the stack on macOS as SMART_CALL does? (PLAN section 8, item 20)

By source reading of stacker 0.1.25 and psm 0.1.32 from crates.io (both updated 2026-08-02), yes:
- `stacker::remaining_stack()` is the stack pointer minus a thread-local limit (src/lib.rs). On macOS that limit starts as `pthread_get_stackaddr_np - pthread_get_stacksize_np` (src/backends/macos.rs), the same source the C++ reads (ob_common_utility.cpp:111-119).
- `stacker::grow(size, f)` maps a new stack with `mmap`, with a `PROT_NONE` guard page on each side (src/mmap_stack_restore_guard.rs). It sets the thread-local limit to the new stack, runs `f` there through `psm::on_stack`, and restores the old limit afterwards (src/lib.rs, `_grow`). This is the same flow as `call_with_new_stack` plus `set_stackattr`.
- psm switches stacks on aarch64 outside Windows with src/arch/aarch_aapcs64.s, which has Darwin symbol naming (:6), and marks the target "switchable" (build.rs).
- rustc itself uses this path here: `nm` on the 1.98.1 aarch64-apple-darwin `librustc_driver` lists `stacker::_grow`, `stacker::remaining_stack` and `_rust_psm_on_stack`.

| | `SMART_CALL` | stacker |
|---|---|---|
| Stack bounds | pthread bounds, cached per thread | pthread bounds, cached per thread |
| New stack | `ob_malloc`, about 2 MiB, no guard page | `mmap`, requested size, guard pages on both sides |
| Total cap | 10 MiB per thread, then `OB_SIZE_OVERFLOW` | none; a failed `mmap` panics (`assert_ne!`), which aborts under `panic=abort` |
| Cost of a switch | a jemalloc allocation and free | `mmap` + `mprotect` + `munmap` (assumption: slower; not measured) |
| Memory accounting | counted under the "CoStack" label | not seen by the allocator |
| Unwinding | none | `catch_unwind` around the callback; unused under `panic=abort` |

So stacker supplies the switch; the cap and the `OB_SIZE_OVERFLOW` return come from a thin wrapper that needs no `unsafe`. Section 4.5 lists what Step 2a still runs.

### 1.9 The stack on wasm

- **At 834bbee1e** `SMART_CALL` is a plain call on wasm (ob_smart_call.h:66-68).
- **The wasm branch** (origin/feature/webassembly-shell, cd41da17b; read as evidence only, Decision 15) returns `OB_SIZE_OVERFLOW` instead of switching. Its comment reads "A native stack pointer swap cannot extend the Wasm execution stack". It measures the Emscripten data stack with `emscripten_stack_get_end/base/current` (its ob_smart_call.h and ob_common_utility.cpp).
- **stacker on wasm32** allocates the new stack from the global allocator with no guard pages (src/alloc_stack_restore_guard.rs). `remaining_stack()` returns `None` there (src/backends/mod.rs falls through to fallback.rs), so `maybe_grow` always grows. psm on wasm32 only moves the linear-memory stack pointer, so the engine's own call stack still overflows (inferred from the wasm branch's comment and psm's `wasm32.o`, not run).

## 2. Constraints that bind this topic

- **Decision 14 (b).** `#![forbid(unsafe_code)]` outside the named crates (island shims, SIMD kernels, IO buffers, thin wrappers over vetted reclamation crates). Elsewhere this forbids `unsafe impl Send/Sync`, `static mut`, reading through an `AtomicPtr`, `AtomicI64::from_ptr` (the fallback in evidence-full.md:1995) and crossbeam-epoch's `Shared::deref` (`unsafe`, crossbeam-epoch 0.9.21 src/atomic.rs:1412). stacker's `grow`/`remaining_stack` and parking_lot are safe APIs.
- **Decision 16 (a).** Stable 1.98.1 only in shared code: no `AtomicU128` (unstable), no `#[thread_local]` attribute, no `-Zsanitizer=thread`, no Miri. loom runs on stable.
- **Decision 12 (b).** A general out-of-memory aborts. A failed stack extension is not one of the named budget owners, so an abort there is consistent. The C++ returns `OB_ALLOCATE_MEMORY_FAILED` (ob_smart_call.h:84-85).
- **Decision 7.** macOS arm64 first, ruling out none of wasm, Android, Linux and Windows. The relevant guidelines (PLAN section 3) are no 128-bit atomics and a wasm depth limit with growth on native targets. Thread-local destructors on wasm needed a private std patch (feasibility section 10, Decision 16).
- **Decision 8 guideline.** The engine must later run as an in-process library, on threads it did not create. Engine code therefore cannot rely on per-thread setup done by its own thread pool, and thread creation and stack sizes belong in the server binary's crate.
- **Decision 9 (a).** Kill-only stops until parity: no clean thread shutdown or thread-local teardown is needed.
- **Decision 1 and row 1a.** Memory use is not a criterion, so bigger stacks are acceptable. Query speed is gated at 1.2x on sysbench at 1, 16 and 64 threads, so read-hot locks and stack-switch cost matter.
- **Decision 6 (b).** Exact comparison, but no judge case expects a stack-exhaustion error (section 1.7), so the depth at which `OB_SIZE_OVERFLOW` fires is not compared. Scan order must not start to depend on thread timing (PLAN section 8, item 18).
- **Decision 13 (a).** share/geo stays C++ with its `SMART_CALL` sites, and the parser's C core keeps its thread-local `jmp_buf` (PLAN section 3, item 7).
- **PLAN section 3, core outline.** `Atomic*` field types; a `Mutex<T>` that owns its data; `Arc` with custom drop for pooled handles; epoch reclamation from a vetted crate instead of QClock, the retire station and the KV cache's hazard versions; a small set of thread-pool, queue and timer primitives; stack growth on native targets; recursive algorithms keep their shape (item 6).

## 3. Options

### 3.1 Atomics

| Option | Cost | Risk |
|---|---|---|
| A1. Retype every field touched by `ATOMIC_*` or `.atomic_*()` to `Atomic*`, `SeqCst` everywhere, weaker only where the C++ names a weaker order | About 1,055 fields plus the SCN/ObTxSEQ fields; the compiler lists every plain access | Low. On arm64 a `SeqCst` load, store and read-modify-write compile to the `LDAR`/`STLR`/`LDADDAL` family that clang emits for these macros, so no speed loss (assumption; codegen not inspected) |
| A2. Choose Acquire, Release or Relaxed per field while translating | Reasoning at every field, by the implementer and by the reviewer | Ordering bugs that family 11 is unlikely to catch; small gain on arm64 |
| A3. Keep plain fields and use `AtomicI64::from_ptr` | Least editing | Needs `unsafe` everywhere; ruled out by Decision 14 |
| A4. Remove whole categories by redesign: reference counts become `Arc`, lock words become parking_lot, links become crossbeam, clocks become crossbeam-epoch | Part of the core redesign already | Only as good as the replacement crates |

### 3.2 Locks

- **B1. std `Mutex`/`RwLock`/`Condvar`** (sql-nio uses them, rust/sql-nio/src/lib.rs:25): no dependency, but no timed lock, poisoning in the API, and unspecified `RwLock` fairness.
- **B2. parking_lot 0.12.5 / lock_api 0.4.14:** `try_lock_until` (lock_api src/mutex.rs:389, src/rwlock.rs:793), `downgrade` (rwlock.rs:1718), fair unlock, a 1-byte `Mutex` (`state: AtomicU8`), no poisoning, optional `deadlock_detection`. Guards are not `Send` unless `send_guard` is on.
- **B3. Port `ObLatch`:** keeps latch ids and spin counts, which nothing shows any more, at the price of `unsafe` and a futex layer.

### 3.3 Lock-free structures

- **C1. Safe-API crates:** crossbeam-channel and crossbeam-queue for queues, arc-swap for snapshots published by pointer swap, `crossbeam_utils::sync::ShardedLock` (8 shards, src/sync/sharded_lock.rs:18) for read-hot locks, crossbeam-skiplist as a safe ordered map, and crossbeam-epoch inside a named crate where lock-free reads need reclamation. Cost: a replacement per structure; the 17K lines are not translated.
- **C2. Port each structure** into named `unsafe` crates: 17K lines of `unsafe`, and the 54123f13e class of bug stays.
- **C3. Locks first** (`Mutex<HashMap>`, channels), lock-free only where the 1.2x gate needs it: cheapest, but read-hot paths may contend at 64 threads.

### 3.4 Thread-local state

- **T1.** Pass everything explicitly. Cost: signature changes, which the SQL tier has anyway (PLAN section 3). Research 02 rejected this for the warning buffer: every path to 2,799 `LOG_USER_ERROR` calls would change for no gain in behavior (02-errors.md, section 4.2, M3).
- **T2.** Keep the thread-locals as they are. Cost: none at first. Risk: hidden inputs break when work moves between threads (PX workers, DAG tasks, IO callbacks), and under the library form (Decision 8 guideline).
- **T3.** Explicit for state that changes results, with a closed list of allowed thread-locals.

### 3.5 Stack growth and thread stack sizes

- **D1.** One wrapper at the 1,137 `SMART_CALL` sites and the 106 check sites, on stacker, with the C++ constants: 64/192 KiB reserves, 2 MiB extensions, a 10 MiB cap, 224 KiB threads. Faithful, but it switches often, and a Rust frame chain between two checks may use more than the C++ reserve.
- **D2.** D1 with bigger thread stacks and reserves set by measurement. Fewer switches, and room for debug builds, whose frames are larger (assumption: Rust at opt-level 0 uses several times the stack of clang -O2). Memory is not a criterion (Decision 1), and untouched stack pages cost only address space (assumption).
- **D3.** Rewrite the recursions as loops over explicit work stacks. Rejected: PLAN section 3, item 6 keeps the recursive algorithms' shape.
- **D4.** Depth counters only. Portable, but they do not bound bytes on native targets. It is what the wasm guideline asks for on wasm.

## 4. Recommendation

A4 plus A1 for atomics, B2 for locks, C1 with C3 as the default for lock-free code, T3 for thread-locals, and D2 plus D4 for the stack, with the wasm guideline adopted now. Each rule carries its reason.

### 4.1 Rules

**Atomics**
1. A field that one thread writes while another reads it is an `Atomic*` type, or sits inside a lock. *Reason: the compiler then finds every access, which the C++ cannot (section 1.1: 724 of 966 pairs are also accessed plainly).*
2. Every atomic operation uses `Ordering::SeqCst`. A weaker order is allowed only where the C++ names one (the 29 `_ACQ`/`_REL`/`_RLX` calls, mapped one to one), or with a `// ordering:` comment that names the paired operation or says "statistic only". A CI grep rejects any other ordering. *Reason: same semantics as the macros, at no cost on arm64 (assumption, as in A1), and nothing for reviewers to prove.*
3. A plain read or write of such a field in C++ becomes a `SeqCst` load or store, except in constructors and resets on an object not yet shared, which use `AtomicX::new` or `get_mut()`. *Reason: the borrow checker proves the exclusive case, and every other plain access in C++ was a data race.*
4. Reference counts, lock words, list links and reclamation clocks are not retyped. They are replaced by `Arc` (with a custom drop for pooled handles), the lock crate, crossbeam and crossbeam-epoch (rules 8-12). By name, reference counts (217 calls), links (168) and lock words (140) are about 18% of the calls.
5. A value type used atomically gets a plain `Copy` type plus an atomic wrapper with the same operations. This covers `SCN`, `ObTxSEQ` and LSN. `inc_update`/`dec_update` become `fetch_max`/`fetch_min`, and the translation returns `max(old, x)`, because `inc_update` returns the new value (ob_atomic.h:97-108). `ATOMIC_ADD_TAG`/`SUB_TAG` become `fetch_or`/`fetch_and`.
6. There are no 128-bit atomics. `LSNAllocator` keeps its 16-byte state under a `Mutex` unless the performance gate shows the need for a 64-bit packing. `REACH_COUNT_PER_SEC` becomes a `Mutex` or two separate atomics. *Reason: `AtomicU128` is not on stable (Decision 16), and the wasm guideline forbids it; the change costs two sites.*
7. There are no `AtomicPtr`s outside the named crates. A pointer published to readers becomes an `ArcSwap`/`ArcSwapOption`, or an index into an arena. `volatile` has no Rust counterpart: where it signals across threads it becomes an atomic. Barriers (`MEM_BARRIER`, `WEAK_BARRIER`) and `PAUSE` spin loops appear only inside the named crates. `CACHE_ALIGNED` becomes `crossbeam_utils::CachePadded`.

**Locks**

8. One lock family, parking_lot, re-exported from a `sync` module in ob-base (research 01's crate for src/oblib/lib):
   - `Mutex<T>` and `RwLock<T>` own the data they guard. `Mutex<()>` is allowed only with a comment naming what it serializes.
   - Bucket locks become a keyed lock array in the same module.
   - `TCRWLock` sites become `RwLock` by default, and `ShardedLock` at sites the performance gate shows to be read-hot. The plan cache node and the schema manager cache are the first candidates.
   - Pure spin locks are not allowed: `ObRowLatch` becomes a 1-byte parking_lot `Mutex`, which spins briefly and then parks.
   - Timed locks use `try_lock_until` and keep the site's error code (`OB_TIMEOUT`, or `OB_EAGAIN` where the C++ remaps it).
   - Latch ids and spin counts are dropped.
   - *Reason: timeouts and a 1-byte mutex are needed, poisoning is useless under `panic=abort`, and latch statistics are not visible.*
9. Guards never cross threads (no `send_guard`). A handoff between two parties is an `Arc` held by both, so the last drop releases it (`the_other_release_this_`), or state plus a `Condvar`.
10. Callbacks, IO and calls into another crate run after the guard is dropped. The locked section returns the work to do, as `CtxLock`'s `before_unlock`/`after_unlock` does. Where the C++ nests two locks, the Rust code keeps the order and states it at the declaration. Debug builds turn on parking_lot's `deadlock_detection` with a checker thread; the judge and performance builds do not.
11. Condition variables are a `Condvar` paired with the `Mutex<T>` that holds the waited-for state. Producer and consumer queues are crossbeam channels: `ObLightyQueue` becomes `bounded` with `recv_timeout`, `ObFixedQueue` becomes `ArrayQueue`, and `ObLinkQueue`/`ObSpLinkQueue` become `SegQueue` or a channel.

**Lock-free code**

12. Hand-written reclamation is replaced, not translated. Published snapshots (schema versions, tablet table stores, config) use arc-swap. Structures that need lock-free reads with reclamation (the memtable index and the KV cache map) use crossbeam-epoch inside one named crate with a safe API, or crossbeam-skiplist, whose API is already safe. That choice belongs to the storage report. Every other map starts as a lock around a map, sharded if measured hot. Concurrent maps take the fixed hasher that PLAN section 3, item 2 requires.
13. Every named crate that holds lock-free code has loom tests for each structure and is reviewed by the lock-free reviewer (Opus 5.5 under row 4c).

**Thread-locals**

14. State that changes a statement's result, its errors or its limits is passed as a parameter: the worker (`THIS_WORKER`), the memory context, timeouts, the memory tracker, the interrupt checker, the DAS and expression serialization contexts, the freeze source, `is_doing_ddl_` and the `ObMallocHookAttrGuard` memory label. The one exception is the warning-buffer slot that research 02 keeps (02-errors.md, section 5.1). *Reason: work moves between threads (PX, DAG tasks), and the library form will call in from host threads (Decision 8 guideline).*
15. `thread_local!` is allowed only for a closed list: diagnostics (trace id, SQL text for crash logs, logger state), per-thread caches and buffers that do not change results, the stack wrapper's bookkeeping, and the warning-buffer slot.
    - Diagnostics and the warning-buffer slot are set only by a scope guard at a named entry point (request, PX task, DAG task) and copied explicitly at each handoff.
    - Each is const-initialized (`thread_local! { static X: Cell<..> = const { .. } }`) with a type that has no `Drop`, or it states why it needs one. *Reason: no lazy-init check, and no dependence on the thread-local destructors that needed a std patch on wasm.*
    - Engine code never assumes its own thread pool initialized a thread-local.
16. Rate-limited logging keeps per-site state: a per-site `static AtomicI64` for `REACH_TIME_INTERVAL`, and `thread_local!` for the `TC_`/`THREAD_` forms. There is no async runtime in the engine; threads block as today (RULEBOOK section 1, "Async model").

**Stack**

17. Every `SMART_CALL` site calls one function in ob-base, for example `stack::call(Reserve::Normal, || ...) -> Result<T>`, and every `check_stack_overflow`, `check_stack_once` and expression-check site calls `stack::check(reserve)`. Each site keeps its C++ reaction: an error, or the silent skip of ob_raw_expr.cpp:324-326. The expression checks stay at the code generator's every-16-levels marks. The wrapper works like this:
    - if `stacker::remaining_stack()` is at least the reserve, it calls the closure;
    - else, if the thread's total plus one extension would pass the cap, it returns `OB_SIZE_OVERFLOW`;
    - else it grows with `stacker::grow`.
    - *Reason: it keeps the C++ behavior at the cap, the recursions keep their shape, and stacker's guard pages turn a missed check into a fault instead of heap damage.*
18. The constants are Rust's own, set by the Step 2a probe (section 4.5), not copied from the C++. Starting values:
    - a reserve of 256 KiB at `SMART_CALL` sites, 1 MiB at `SMART_CALL_LARGE` sites and 128 KiB for checks;
    - 8 MiB extensions and a 64 MiB total cap;
    - engine threads created by the server binary with an 8 MiB stack.
    - The parameter `stack_size` keeps its name, default and range (it is in the parameter table the judge compares) and becomes a lower bound (question 1).
    - The depth at which `OB_SIZE_OVERFLOW` fires is not a behavior contract. The design document records this as a deviation.
19. On wasm the wrapper does not grow. It counts nesting per thread in a const thread-local and returns `OB_SIZE_OVERFLOW` above a limit that is set when wasm returns. The branch is compiled for wasm only and untested until then. *Reason: the guideline's shape for one `cfg` branch, kept local because every site goes through rule 17.*
20. Deep recursive data is not dropped, cloned or printed recursively. Parse trees, expression and plan trees, and JSON and XML trees live in arenas of typed ids (PLAN section 3) or have an iterative `Drop`. *Reason: a left-deep `1+1+...+1` tree dropped recursively would overflow a Rust stack, while the C++ frees its arenas in one step.*
21. Large locals: a type over 16 KiB is created directly on the heap (`Box::new_uninit`, `Vec`) or in an arena. This replaces the 728 `SMART_VAR`/`HEAP_VAR` lines. The lints `clippy::large_stack_frames` and `large_stack_arrays` (both in 1.98.1's clippy) are set to warn in the core crates.
22. The islands see the same stack segment as Rust.
    - The kept C++ oblib subset under share/geo (PLAN section 8, item 19) keeps `SMART_CALL` for its 5 sites and its check at ob_geo_topology_calculate.cpp:163. Its `get_stackattr`/`set_stackattr` read and write the Rust wrapper's per-thread segment record through two C ABI functions of the island shim. Otherwise, geo code running on a stack that Rust grew sees a stack pointer outside its cached bounds and fails with `OB_ERR_UNEXPECTED` (ob_common_utility.cpp:66-69).
    - The parsers' C cores call back `check_stack_overflow_c` and `obpl_parser_check_stack_overflow` (section 1.6). Rust supplies both from `stack::check`, next to the memory and charset callbacks. They only check and never grow, and they return before any `longjmp` (PLAN section 3, item 7).
    - vsag, S2 and ICU are third-party and were not audited for recursion.

### 4.2 Canonical mappings for RULEBOOK section 2

| C++ construct | Rust (rule) |
|---|---|
| `ATOMIC_LOAD/STORE/SET/TAS` on a field | `AtomicX::load/store/swap(SeqCst)` (1-3) |
| `ATOMIC_AAF/FAA/SAF/FAS/INC/DEC` | `fetch_add/fetch_sub(SeqCst)`, adding the delta for the `_AF` forms |
| `ATOMIC_BCAS/VCAS/CAS` | `compare_exchange(SeqCst, SeqCst)`, as `.is_ok()` or the old value |
| `inc_update/dec_update`, `ADD_TAG/SUB_TAG` | `fetch_max/fetch_min`, `fetch_or/fetch_and` (5) |
| `LOAD128/CAS128` | `Mutex<T>` (6) |
| `ObSpinLock`, `lib::ObMutex`, `ObLatchMutex`, `ObRowLatch`, `ObByteLock` | `sync::Mutex<T>` (8) |
| `SpinRWLock`, `ObLatch`, `TCRWLock`/`common::RWLock` | `sync::RwLock<T>`; `ShardedLock` where measured hot (8) |
| `ObBucketLock` and its guards | the keyed lock array in `sync` (8) |
| `ObThreadCond`, `ObCond` | `Condvar` plus `Mutex<State>` (11) |
| `ObLinkQueue`, `ObLightyQueue`, `ObFixedQueue` | `SegQueue`, a `bounded` channel, `ArrayQueue` (11) |
| QClock, RetireStation, `ObQSync`, `HazardRef`, KV cache hazards | arc-swap, or crossbeam-epoch in a named crate (12) |
| `RLOCAL*`, `thread_local`, `__thread`, `GET_TSI` | a parameter, or `thread_local!` from the closed list (14-15) |
| `SMART_CALL`, `SMART_CALL_LARGE` | `stack::call(reserve, closure)` (17) |
| `check_stack_overflow`, `check_stack_once` | `stack::check(reserve)` (17) |
| `SMART_VAR`, `HEAP_VAR` | a heap or arena allocation (21) |

### 4.3 Crates for RULEBOOK section 1

Versions as on crates.io on 2026-09-24:

| Crate | Version | Where it is used |
|---|---|---|
| parking_lot | 0.12.5 | locks, everywhere, through `sync` |
| crossbeam-utils | 0.8.23 | `CachePadded`, `ShardedLock` |
| crossbeam-channel | 0.5.17 | queues between threads |
| crossbeam-queue | 0.3.14 | `ArrayQueue`, `SegQueue` |
| arc-swap | 1.9.2 | published snapshots |
| crossbeam-epoch | 0.9.21 | only in the named reclamation crate |
| stacker | 0.1.25 | only in ob-base's `stack` module |
| loom | 0.7.2 | dev-dependency of the named crates |

crossbeam-skiplist (0.1.3, last updated 2024-01-08) is an option for the storage report. Banned: `AtomicPtr` and `static mut` outside the named crates, hand-written spin loops, async runtimes in the engine, and `thread_local!` outside the closed list.

### 4.4 What the inventory sweeps

Prompt 02's sweep list names "about 823 `ATOMIC_*` field names". Key those rows by (class, field) instead: the 1,055 pairs plus the fields found through `.atomic_*()`, each with its category (rule 4 replaces it, rule 1 retypes it) and whether plain accesses exist. Also sweep:
- the 479 lock declarations, each with the fields its guarded sections touch (the manual part of `Mutex<T>`);
- the 229 thread-local lines, each marked rule 14 or rule 15;
- the 1,137 `SMART_CALL` and 106 check sites, each with its reaction when the stack is short;
- the users of the structures in section 1.3.

### 4.5 What Step 2a checks

- **stacker on this Mac (PLAN section 8, item 20).** A test binary spawns a thread with a 224 KiB stack, recurses through `stack::call` with a known frame size, crosses several extensions, and gets `OB_SIZE_OVERFLOW` at the cap, not a signal. It also records the time per switch.
- **The stack probe.** For the deepest recursions (a left-deep `+` chain, nested `CASE`, nested subqueries, nested views), the Rust build must succeed at the largest depth where the C++ reference succeeds. Record bytes per level in the debug and judge profiles, and set rule 18's reserves to at least twice the largest stack use seen between two consecutive wrapper calls.
- **The locks on the sysbench point-select path** at 64 threads, with `RwLock` against `ShardedLock` at the plan cache node and the schema manager cache.
- **One geo call on a stack that Rust grew**, to check rule 22.

## 5. Questions only the developer can answer

1. `stack_size` keeps its name, default and range, but under rule 18 it no longer sets the worker stack. It becomes a lower bound under an 8 MiB default. Is that acceptable, or must it keep setting the stack exactly (then the default 256K means frequent switches and reserves tuned for small stacks)? Default: lower bound.
2. May a checking-only CI job use a pinned nightly to run Miri and ThreadSanitizer over the named `unsafe` crates? Decision 16 bans nightly features in shared code, not in test tooling. Default: no; loom only.
3. Are the crates in section 4.3 accepted as dependencies, or does every dependency need its own sign-off? Default: accepted with the design document.
