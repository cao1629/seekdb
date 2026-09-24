# Research 07: the server context and process-wide state

Input to the design document (PLAN.md section 6, Step 1: "the server context struct, and which process-wide actions stay in the binary crate"). Source: C++ at 834bbee1e (`git diff --stat 834bbee1e HEAD -- src` is empty in this worktree). Crate names follow the working crate graph in research/01-crates-core.md, which is not decided yet. Section 7 lists how every count was made.

## Summary

- Engine code reaches process-wide state at about 4,000 sites: 1,224 `share::server_service<T>()` lookups, 891 `GCTX` uses, 682 `GCONF`, 623 `::get_instance()`, 401 `THIS_WORKER`, 112 `SERVER_MODULE_SCOPE` guards. Together they sit in 913 files. Every slot lookup already goes to a type declared in the caller's own module or a lower one; upward calls go through 30 interfaces declared low and implemented high.
- The server never stops cleanly: `SIGTERM` becomes `raise(SIGKILL)`, and `ObServer::wait()` ends in `_Exit(0)`, so the ordered teardown in `ObServer::stop()` and `destroy()` never runs.
- Recommendation: no engine singletons in Rust. One context struct per layer (runtime, storage, SQL, server), built by `Server::open` in the `observer` crate in the C++ init order. Services get their dependencies through constructors. Upward needs are traits declared in the lower crate and filled once at startup. The request deadline stays in a thread-local. A short, named list of process-global exceptions is allowed. One small set of thread, queue and timer primitives, with the C++ timer behavior kept exactly. Process ownership lives in the `seekdb` binary crate: `main`, signals, allocator, panic strategy, daemonizing, the pid file, exit and `chdir`.
- Open for the developer: whether physical standby is in scope, `unsafe` for a few OS calls, daemon mode, one engine per process or several, and what `SIGUSR1` does.

## 1. What the C++ does today

### 1.1 How engine code reaches process-wide state

Occurrences per module; files in brackets. "Other" is oblib, share, logservice, data_plane, query, pl and standby.

| Access path | Total | storage | sql | rootserver | observer | other |
|---|---|---|---|---|---|---|
| `server_service<T>()` | 1,224 (382) | 678 | 92 | 133 | 284 | 37 |
| `GCTX` | 891 (196) | 71 | 206 | 463 | 135 | 16 |
| `GCONF` | 682 (275) | 188 | 242 | 62 | 118 | 72 |
| `GMEMCONF` | 23 (13) | 6 | 0 | 0 | 14 | 3 |
| `::get_instance()` | 623 (281) | 174 | 70 | 48 | 137 | 194 |
| `THIS_WORKER` | 401 (142) | 58 | 157 | 41 | 86 | 59 |
| `SERVER_MODULE_SCOPE` | 112 (71) | 16 | 22 | 11 | 50 | 13 |

865 files use at least one of these paths other than `THIS_WORKER`, or a macro singleton (section 1.3); 48 more use only `THIS_WORKER`. The 913 files are 855 of the 3,819 stems under src.

### 1.2 The service slots and GCTX

- **Slots.** `ObServerServiceSlot<Service>` is an `inline static Service *service_` with plain, non-atomic `bind_server_service`, `server_service` and `unbind_server_service` (src/share/rc/ob_server_runtime.h:126-148).
- **Binding.** `ObServer::obs_construct_modules` builds 64 modules and then binds 96 slots: 83 through `BIND_SERVICE` and 13 by direct calls (src/observer/omt/ob_server_runtime_controller.cpp:1182-1358). src/standby/standby_module.cpp:209-210 binds 2 more. Of the 98 bound types, 30 are interfaces (`ObI*`/`I*`); 17 of those are declared under data_plane/api and query/api. 96 `UNBIND_SERVICE` lines clear the slots (:1709 on).
- **Lookups.** The lookups name 94 distinct classes (122 spellings with namespace variants). Every lookup names a type declared in the caller's own module or a lower one; none goes up (`slotmatrix.py`). The biggest are storage→storage 612, rootserver→rootserver 118, observer→storage 101, observer→observer 74 and observer→sql 65.
- **Readiness.** Callers defend against empty slots: 149 lines null-check a lookup, and 112 `SERVER_MODULE_SCOPE` guards test `g_server_modules_ready` (ob_server_runtime.h:114, :230-232). That flag is set at src/observer/omt/ob_server_runtime.cpp:448 and cleared at :542. A second flag, `g_modules_ready` (src/share/rc/ob_server_runtime_support.h:29), has no users outside its header and is never set.
- **GCTX.** `ObGlobalContext` is a function-local static, zeroed with `MEMSET` in its constructor (src/share/ob_server_struct.h:66-113; ob_server_struct.cpp:30-34). `ObServer::init_global_context` points its fields at `ObServer` members (src/observer/ob_server.cpp:2349-2373). Uses by field: `sql_proxy_` 308, `schema_service_` 294, `self_addr` 175, `meta_db_pool_` 26, `status_` 10, and every other field 9 or fewer. So GCTX is mostly the inner SQL client, the schema service and the server's own address.
- **Runtime state.** `g_server_runtime` points at `ObServerRuntimeState`: role, write-enabled, recovery mode, cpu and memory sizes (ob_server_runtime.h:51-119). It also serves as the `IRunWrapper` for threads and timers, but that does nothing: both definitions of `lib_server_runtime_dispatch` ignore the wrapper and call the function (src/share/rc/ob_server_runtime.cpp:34-39; src/oblib/lib/task/ob_timer_service.cpp:24-30).
- **The ObServer singleton.** `OBSERVER` and `ObServer::get_instance()` appear about 40 times, all inside src/observer.

### 1.3 Other singletons, startup tables and per-thread state

- **Engine singletons behind `get_instance()`.** 83 distinct names. The largest are `ObMultiVersionSchemaService` 67, `ObMallocAllocator` 55, `ObOptStatManager` 32, `ObKVGlobalCache` 29, `ObServerConfig` 26 (besides `GCONF`), `ObAutoincrementService` 21 and `ObIOManager` 20.
- **Macro singletons.** 27 macros wrap `get_instance()`. Besides `GCTX` and `GCONF`: `OB_STORAGE_OBJECT_MGR` 126, `LOCAL_DEVICE_INSTANCE` 43, `OB_SERVER_BLOCK_MGR` 36, `OTTZ_MGR` 26, `OB_STORE_CACHE` 24, `SERVER_STORAGE_META_SERVICE` 23, `OB_FILE_SYSTEM_ROUTER` 21, `OB_TS_MGR` 19 and `OB_IO_MANAGER` 19.
- **Test hooks.** `DEBUG_SYNC` runs through the global `GDS` (src/share/ob_debug_sync.h:224-226) at 173 sites in 74 files. Tracepoints are declared at 99 `ERRSIM_POINT_DEF` sites and checked at 230 `EVENT_CALL`/`OB_E` sites. The judge depends on both: DEBUG_SYNC point names, and 12 tracepoints set by init.sql (PLAN section 3).
- **Tables filled once at startup and never changed.** Expression, extra-info and cache registrations, UUID, number and decimal constants, and charsets (src/sql/ob_sql_init.h:46-71).
- **Per-thread state.**
  - `THIS_WORKER` is a `__thread Worker*` (src/oblib/lib/worker.h:115, :174). Its uses are mostly the request deadline: `set_timeout_ts` 73, `get_timeout_ts` 60, `get_timeout_remain` 50, `is_timeout_ts_valid` 24, `is_timeout` 16. Then `check_status` 52, `set_session` 27, `need_retry` 21.
  - Timer runs reset the deadline to `INT64_MAX` (ob_timer_service.cpp:119).
  - 121 files declare thread-locals: 100 `thread_local`, 37 `__thread`, 103 `RLOCAL*`.
  - Each `lib::Thread` creates a "ThreadRoot" memory context (src/oblib/lib/thread/thread.cpp:377-379). The arena design replaces it; this report does not decide it.

### 1.4 Timers

- **The service.** One process-wide `ObTimerService`: a static local (ob_timer_service.h:129-133) that `ObServer::init` starts (ob_server.cpp:683). One dispatcher thread, "TimerSvr", feeds a worker pool of 4 to 128 threads ("TimerWK") with a limit of 10,000 tasks (ob_timer_service.h:192-196). Every `ObTimer` binds to it in its constructor (src/oblib/lib/task/ob_timer.h:39).
- **Numbers.** 59 `ObTimer` objects in 43 files; 23 of them are named `timer_`.
- **Behavior the port must keep:**
  - **One task at a time per `ObTimer`.** `pop_task` skips a due task while another task of the same timer is running (ob_timer_service.cpp:534-548). Different timers run in parallel.
  - **Repeats count from the end of the last run.** The next run is scheduled at completion time plus the delay (:477). `immediate` runs the first time at once.
  - **Cancel and wait.** `cancel_task` drops queued runs and lets a running one finish without rescheduling it (:348-397). `wait_task` polls every 10 ms until nothing is queued or running (:400-429). `ObTimer::stop` is cancel-all plus wait (ob_timer.cpp:72-81). Scheduling on a stopped timer returns `OB_CANCELED` (:138).
  - **What a run sets up.** Each run resets `THIS_WORKER`'s deadline and the trace id. For the length of the run it names the worker thread `<name>_<timer name>` (:102-140, :173-189).
  - **Dispatch timing.** The dispatcher sleeps 10-100 ms between checks (ob_timer_service.h:192-193). Among tasks due together, the order is effectively arbitrary, because removal swaps the last queue entry into the gap (ob_timer_service.cpp:364-365).
- **Two time wheels.** `ObTimeWheel` (src/share/ob_time_wheel.h:124) serves the transaction timer at 100 ms precision (src/storage/tx/ob_trans_timer.h:143) and the deadlock detector at 10 ms (src/storage/deadlock/ob_deadlock_parameters.h:34).

### 1.5 Threads and thread pools

| Primitive | Defined | Subclasses or users |
|---|---|---|
| `lib::Threads` (also spelled `lib::ThreadPool`, `share::ObThreadPool`) | src/oblib/lib/thread/threads.h:34; thread_pool.h:25; src/share/ob_thread_pool.h:25 | 35 subclasses (16 + 12 + 7 by spelling) |
| `ObSimpleThreadPool` (a queue plus workers calling `handle`) | ob_simple_thread_pool.h:48, :128 | 12 subclasses; the `ObSimpleThreadPoolDynamicMgr` thread resizes them (ob_dynamic_thread_pool.h:126) |
| `ObReentrantThread`, `ObAsyncTaskQueue` | ob_reentrant_thread.h:30; ob_async_task_queue.h:95 | 3 subclasses; users in 3 files |
| `ObDedupQueue`, `ObUniqTaskQueue` | ob_dedup_queue.h:134; src/observer/ob_uniq_task_queue.h:98 | 2 users; 2 instantiations |
| `ObAdaptiveWorkerPool` | ob_adaptive_worker_pool.h:64 | the request workers (`ObServerRuntime`) and `ObSimpleThreadPool` |
| `ObDagScheduler`, `ObDDLDagThreadPool` | ob_dag_scheduler.h:1134; src/storage/ddl/ob_ddl_dag_thread_pool.h:29 | core redesign (PLAN section 3) |
| `ObPxPool`, `ObPxPools` | src/observer/omt/ob_server_runtime.h:51, :107 | PX workers |
| `ObOccamThreadPool`, `ObMapQueueThreadPool`, `ObDynamicThreadPool` | | no users outside their own files: drop |

- **Request workers.** Thread per request with blocking waits. The normal size is `2 + max(1, min_cpu × cpu_quota_concurrency)`. When completions stall, a rescue cap of `max(memory / 20 / (stack_size + 3.5 MiB), 150)` applies (src/observer/omt/ob_server_runtime.cpp:569-580).
- **What every `lib::Thread` does at start** (thread.cpp:336-398):
  - raises its macOS QoS to USER_INITIATED and clears the background priority (:341, :345), because threads of a daemon inherit a low QoS;
  - uses the stack size from the `stack_size` parameter, default 256K (ob_server.cpp:1966-1974; ob_parameter_seed.ipp:730);
  - creates its root memory context.
- **The rest.** sql-nio runs its own named reactor threads, which abort on panic (rust/sql-nio/src/reactor.rs:1113-1120).
- **Measured.** How many threads a live server has is unmeasured, and this task may not start one. A saved instance log of a reference run (/Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-A/failures/instance/seekdb_log/, 61 s of log) shows 157 distinct thread ids writing log lines. 8 of them are timer workers, running 42 different timers. All 8 show up as `TxLoopWorker_<timer>`, because `ObTxLoopWorker::runTimerTask` renames the shared worker thread (src/storage/tx/ob_tx_loop_worker.cpp:97).

### 1.6 Signals

- **Setup.** `main` blocks the handled signals before any thread starts (src/observer/main.cpp:678). `ObServer::start` then starts `ObSignalHandle`, a thread in a `sigtimedwait` loop (ob_server.cpp:1110; ob_signal_handle.cpp:56-81).
- **The handled set.** SIGPIPE, SIGTERM, SIGUSR1 and 22 numbers from 40 to 64 (ob_signal_handle.cpp:105-108).
- **SIGTERM** calls `raise(SIGKILL)` (:129-134).
- **SIGUSR1** calls `prepare_stop()`, which sleeps 5 s, then `set_stop()` (:137-140; ob_server.cpp:1473-1497).
- **40-64** reopen or change logging (40-42, 53), print memory (49, 59, 63) or dump a trace (62); the rest do nothing.
- **On macOS, only 3 of the 25 exist.** In SDK 26.2, `sigaddset` is a macro whose `__sigbits` returns 0 above 32 (MacOSX26.2.sdk/usr/include/signal.h:116-122). The same file replaces `sigtimedwait` with `sigwait`, which ignores the timeout (ob_signal_handle.cpp:19-37).
- **No crash handler is installed.** `install_ob_signal_handler()` (src/oblib/lib/signal/ob_signal_handlers.cpp:69) has no callers, so SIGSEGV and SIGABRT take the default action. Apart from an unused `main` macro in the leftover libeasy headers (src/oblib/easy/thread/easy_uthread.h:90), the only `sigaction` users catch faults for a moment during memory dumps (src/oblib/lib/alloc/memory_dump.cpp:94-123).
- **The judge's use.** `sdb.py stop` sends SIGTERM and SIGKILLs after a timeout (.github/script/seekdb/sdb.py:351-380). The restart script sends SIGKILL itself (migration/judge/harness/restart_scenarios.py:246). Nothing sends SIGUSR1.

### 1.7 Start, bootstrap and stop

`main` (src/observer/main.cpp):

1. Runs `inner_main` on a new 1 MiB mmap'd stack (:877-882).
2. Configures the macOS malloc zone (:649) and seeds the trace id (:667).
3. Blocks signals (:678) and sets the locale (:693-698).
4. `chdir(base_dir)` (:705); checks the uid of etc/ (:720); creates run/, log/ and etc/ and a soft link (:722-728).
5. Sets OpenSSL's malloc (:729).
6. Daemonizes, or with `--nodaemon` only writes and `lockf`s run/seekdb.pid (:733-746; src/oblib/lib/utility/utility.cpp:1590-1646).
7. Starts curl (:751) and the logger on log/seekdb.log (:755-760).
8. Installs a `Worker` on the main thread (:795).
9. `ObServer::init` (:798), `ObServer::start` (:809), then sd_notify READY (:811).
10. `--initialize` exits here (:816). Otherwise `wait()` (:819).

`ObServer::init` has 63 checked steps (ob_server.cpp:624-871). The ones that matter here:
- registers `this` as the DDL slice store (:630);
- `init_config` opens meta.db through `getcwd` and checks the data version after it (:1698-1719);
- `check_need_initialize` (:410-436): an empty data dir with empty redo means bootstrap; a mix of the two is an error;
- starts the pool resize thread and the timer service (:681-683), then the async logger (:688);
- fills the static SQL tables (:698-711) and GCTX (:720);
- initializes, without starting, the IO manager, the KV cache, schema, network, `ObService` and the standby module, the local management service, SQL, storage, the clock thread (:794) and a dozen more singletons.

`ObServer::start` has 26 steps (ob_server.cpp:1096-1330):
1. Starts the signal thread (:1110), IO and the storage object manager.
2. Starts the runtime controller's timers (:1150).
3. `SERVER_STORAGE_META_SERVICE.start()` (:1155) replays the server slog. On a restart, the replayer calls `create_runtime` through the `storage::ObIServerRuntime` interface (src/storage/meta_store/ob_server_storage_meta_replayer.cpp:82). That builds the module graph: 64 constructs, 96 binds, 67 init steps and 32 starts (ob_server_runtime.cpp:419-452; ob_server_runtime_controller.cpp:1182-1547).
4. For an empty dir, `initialize_server_runtime` creates the bootstrap runtime instead (ob_server.cpp:1331-1371).
5. The standby module runs `bootstrap_primary` (:1196). That calls `ObService::bootstrap` (src/observer/ob_service.cpp:669-702), which runs `ObBootstrap::execute_bootstrap` (src/rootserver/ob_bootstrap.cpp:171-239):
   - core partitions, then the in-bootstrap flag and the global stat;
   - the inner-table schemas (74 system tables, 134 virtual tables and 139 views from ob_inner_table_schema_def.py) are built and published;
   - their partitions are created by a background task while the schemas are loaded through `ObDDLService`;
   - system data is written and the schema refreshed.
6. `wait_for_server_runtime` waits for replay and the local timestamp (:1274), then schema and timezone readiness (:1285).
7. Only then does the listener start (`net_frame_.start()`, :1291), followed by the standby gRPC listener (:1297).
8. `status_` becomes serving (:1313).

References to modules initialized later:
- the log storage adapter gets `mods_ls_service_` and `mods_memstore_freezer_` before either is initialized (ob_server_runtime_controller.cpp:1372 against :1380 and :1431);
- `sql_engine_.init` gets the PL engine before `pl_engine_.init` (:1446 against :1472);
- `session_mgr_.bind_lifecycle_services` runs last (:1503).

Stop:
- `wait()` polls `stop_` every 3 s and then calls `_Exit(0)` unconditionally (ob_server.cpp:1663-1680).
- `stop_` is set only by SIGUSR1 or by a failed init or start (:859, :1311).
- No caller reaches `ObServer::stop()` (:1499). `destroy()` (:872) comes after `wait()` in main.cpp:825, so it never runs either.

### 1.8 The base classes other modules subclass

- **`ObTimerTask`** (ob_timer_service.h:53-73): a single pure virtual, `runTimerTask()`, plus a timeout-check flag. 72 subclasses: storage 33, observer 11, rootserver 8, sql 7, share 6, oblib 3, logservice 2, standby 2. 5 of them sit in files the reference build does not compile:
  - three in src/storage/meta_mem/ob_tablet_meta_mem_mgr.h, an uncompiled copy of ob_storage_meta_mem_mgr.h;
  - src/observer/report/ob_tablet_meta_checker.h;
  - src/observer/vector_index/ob_vector_index_async_task_scheduler.h.
  
  So 67 are live on macOS.
- **`ObDLinkBase<T>`** (src/oblib/lib/list/ob_dlink_node.h): intrusive `prev_`/`next_` pointers with unlink and insert, used with `ObDList<T>`. 72 grep lines, one commented out (src/sql/engine/ob_physical_plan.h:55), so 71 subclasses: sql 24, storage 16, oblib 10, data_plane 6, share 6, observer 3, rootserver 3, query 2, logservice 1. They include the DAG tasks and dags, `ObIORequest`, `ObDDLTask`, plan cache values and `ObExprOperator` itself (src/query/api/query/engine/expr/ob_expr_operator.h:304).
- **`ObFuncExprOperator`** (ob_expr_operator.h:1063-1072): a constructor-only layer over `ObExprOperator`. 313 subclasses (sql 310, query 3), all in compiled files. They are registered once at startup (ob_sql_init.h:53). Expressions reach process state through the evaluation context: 222 `get_my_session()` in 102 expression files, `GCTX` 26 times in 11 files, `server_service` 8 times in 7.

### 1.9 Dependence on the working directory

- **`getcwd`.** 5 engine sites read it: ob_server.cpp:1704 and :1788, src/sql/session/ob_system_variable.cpp:2497 and :2524, src/storage/ob_file_system_router.cpp:123.
- **Relative paths.** 21 source lines hold paths relative to the base dir, such as `etc/seekdb.data_version.bin` (src/oblib/common/ob_data_version_mgr.h:91) and `run/sql.sock` (src/oblib/rpc/obmysql/ob_sql_nio_server.cpp:158).
- **Values shown to users.** The `pid_file` and `socket` system variables print `getcwd()` plus "run/observer.pid" and "run/sql.sock" (ob_system_variable.cpp:2496-2533). The real pid file is run/seekdb.pid. No configured test prints either (`git grep` over tools/deploy/mysql_test).

## 2. Constraints that bind this topic

| Source | What it requires here |
|---|---|
| Decision 7 | The design rules out none of wasm, Android, macOS, Linux or Windows. Report Decision 7 notes wasm needed a patched std for thread-local destructors. |
| Decision 8, notes | A guideline, not a decision: don't hard-wire process ownership into the engine (chdir, signal handlers, `_Exit` on the last client, the malloc hook). The embedded form is deferred, so its `seekdb.clients` flock and `wait_no_client` are not ported. |
| Decision 9 | Kill-only stops until parity; SIGTERM becomes `raise(SIGKILL)`. The clean shutdown comes after parity, as its own flagged change. The library form will need a clean close. |
| Decision 10 | A parallel tree and one cutover, so there are no C++/Rust seams inside the engine. The Step 2a narrow run must start without most of bootstrap. |
| Decision 11 | The data-version check moves ahead of the meta.db open (ob_server.cpp:1698-1719). |
| Decision 12 | The named budget owners return typed errors, so each is a reachable service with its own budget. |
| Decision 13 | vsag, S2, geo, ICU and the parser's C core stay C++. Their callbacks into Rust need receivers. |
| Decision 14 | `#![forbid(unsafe_code)]` outside named crates: no raw-pointer slots. |
| Decision 16 | Stable toolchain only. |
| Decision 6; row 1a | Exact output; query speed gated at 1.2x on this Mac; restart time recorded, not gated. |
| PLAN section 3 | "An explicit server context in place of the `server_service<T>` slots and `GCTX`"; "a small set of thread-pool, queue and timer primitives"; main, the threads, the global allocator and `panic=abort` kept in the server binary's crate. The wasm guidelines include networking behind cargo features and `panic=abort`. |
| PLAN section 8 | Item 17: freeze thresholds and background timing feed EST numbers. Item 19: island memory charging on macOS. Item 20: `stacker` and thread stack sizes. Item 21: timing-sensitive cases. |

## 3. Options

### 3.1 How engine code reaches services

| Option | What it is | Cost | Risk |
|---|---|---|---|
| A. Statics | `static X: OnceLock<Arc<T>>` per service, or one static server object | Lowest: each call site becomes a static load | One engine per process for good; no close and reopen (`OnceLock` cannot be cleared); hidden init order; the 149 null checks become `expect` or `Option`; crate tests need global setup. Works against Decision 8's guideline. |
| B. Registry keyed by type | `HashMap<TypeId, Arc<dyn Any>>` passed around or global | Low: mirrors `server_service<T>()` | Lookups can fail at run time; nothing checks at build time which services exist; hashing on hot paths; keeps the defensive checks. |
| C. Explicit context structs | Typed `Arc` fields, one struct per layer, passed down; services get dependencies through constructors | High: every site changes, and functions that reach state gain a `ctx` parameter | Construction cycles need an explicit binding step; the `ctx` parameter spreads to callers. |
| D. Generic trait bounds | `fn f<C: HasSchema + HasPlanCache>(ctx: &C)` | High, plus monomorphization in every crate | Noisy signatures that agents would write differently; slower checks. |

The cost of C is smaller than it looks. The SQL tier changes nearly every signature anyway (PLAN section 3), and about 78% of stems (2,964 of 3,819) touch no process state.

### 3.2 Per-thread request state

Making the deadline explicit would change about 220 deadline sites and add a parameter to storage calls that do not take one today. A thread-local keeps the C++ set-and-restore shape. The known hazard is work moved to another thread, which C++ already handles by copying the deadline. Session, allocator and request pointers are better explicit: the SQL tier holds them in its execution context already.

### 3.3 Timers and thread pools

Translating each C++ primitive keeps 10 designs, three of them dead. Merging them into a few Rust primitives is what PLAN section 3 asks for. The risk is changing behavior the judge can see through timing: background freezes and merges, and transaction timeouts. The timer rules in section 1.4 are therefore kept as a written contract.

## 4. Recommendation

### 4.1 The context structs

Option C, with a short, named list of process-global exceptions. One struct per layer. Each is defined in the lowest crate that can name all its fields, and each holds an `Arc` of the layer below. The table is a first cut. Where research 01's crate map puts a service above the struct's crate (the meta.db pool in ob-share, compaction in storage-engine), the struct holds a trait object for it, or the service moves; the crate-graph decision settles which.

| Struct (crate, per research 01) | Holds | Replaces |
|---|---|---|
| `RuntimeContext` (ob-runtime) | absolute `base_dir`, `data_dir` and `redo_dir`; `self_addr`; `Arc<ServerConfig>` with atomic parameters and the config manager; the meta.db pool; server status and role as atomics; the named memory budgets; `TimerService` and the shared timer; the thread spawner; the IO manager and local device; the KV cache; the bandwidth throttle | `GCONF`, `GMEMCONF`, `GCTX.self_addr`/`status_`/`meta_db_pool_`/`config_mgr_`/`bandwidth_throttle_`, `ObServerRuntimeState`, `ObTimerService`, `ObIOManager`, `LOCAL_DEVICE_INSTANCE`, `ObKVGlobalCache`, `ObMallocAllocator` budgets |
| `StorageContext` (storage-tablet) | the runtime context; the storage, transaction and log modules (about 40 of the 64); the storage singletons; for upward needs, `OnceLock<Weak<dyn …>>` for the inner SQL client and the other interfaces implemented above | `server_service` lookups from storage; `OB_STORAGE_OBJECT_MGR`, `OB_SERVER_BLOCK_MGR`, `OB_STORE_CACHE`, `SERVER_STORAGE_META_SERVICE`, `OB_FILE_SYSTEM_ROUTER`, `OB_TS_MGR`; `GCTX.sql_proxy_` as used by storage |
| `SqlContext` (sql-exec) | the storage context; the schema service; the session manager; timezone; autoincrement; trait objects for the services in crates above it: plan cache, PL, PX pools, DTL, opt stats, the local command (DDL) service, vector index | `GCTX.schema_service_`, `ObMultiVersionSchemaService` and `ObOptStatManager` singletons, `OTTZ_MGR`, sql-side slot lookups |
| `ServerContext` (observer) | the SQL context; the rootserver and DDL services, `ObService`, the request-worker runtime, the AI, vector-index and change-stream services, the network handle (behind a cargo feature), the stop request | `ObServer` members, `OBSERVER`, rootserver's `GCTX` use (463) |

Allowed process-wide state, and nothing else:
1. Tables filled once and never changed (`static` or `LazyLock`): the expression registry, the error catalog, charsets, system-variable metadata.
2. The logging facade and its level; the binary crate installs the sink.
3. The `DEBUG_SYNC` and tracepoint registries, keyed by the exact C++ names (173 + 329 sites). A context parameter at every hook site would buy nothing.
4. Hooks that third-party libraries hold per process, installed once with `std::sync::Once`: vsag's logger and block-size limit (src/oblib/lib/vector/ob_vsag_adaptor.cpp:496-505), and CRoaring's memory hook if it stays (src/share/roaringbitmap/ob_rb_memory_mgr.cpp:153-165).
5. Thread-locals of `Copy` types only, so wasm needs no thread-local destructors: the request deadline and retry flag, the trace id, the thread name.

### 4.2 How the context is built and passed

- **`Server::open` builds the contexts, in the observer crate.** It takes `ServerOptions` (parsed argv) and builds the contexts bottom-up in the C++ `obs_construct_modules`/`obs_init_modules` order. The `seekdb` binary only calls it, so a later library build can call it too. Research 01 puts this in the binary crate; this report moves it one crate up for that reason.
- **Construction replaces two-phase init.** C++ `init(args)` parameters become constructor parameters. `is_inited_` (5,327 uses in 1,072 files) and its `OB_NOT_INIT` paths disappear.
- **Cycles get one binding step.** Two of the late references in section 1.7 are real cycles. The log service holds an `ObILogStorage` adapter that points at the LS service and the memstore freezer (src/storage/tx_storage/ob_log_storage_adapter.h:31-57), while `ObLS` looks up the log service (src/storage/ls/ob_ls.cpp:79). SQL calls PL (`sql_engine_.init` takes the PL engine, ob_server_runtime_controller.cpp:1446), and PL calls SQL through SPI (src/pl/ob_pl_interpreter.cpp:21). They, and each upward interface, become `OnceLock<Weak<…>>` fields, filled in a single step right after the upper service is built. The step ends by checking that every cell is filled, before any thread starts. `Weak` keeps the post-parity clean close possible. The session manager's late binding needs only a different construction order.
- **The module graph comes after replay.** The replayer returns the runtime meta, and `Server::open` builds the graph with it. This removes storage's call up through `ObIServerRuntime::create_runtime`.
- **Services hold `Arc`s** of the services they use, as fields.
- **Short-lived objects and free functions** that reach process state take `ctx: &<Layer>Context` as their first parameter after `self`.
- **The SQL tier** reaches it through the execution context: `ExecCtx<'q>` holds `&'q SqlContext`, and expressions get it from the evaluation context.
- **Tasks and threads** capture `Weak` of their owner. Nothing that must survive a crash may depend on a `Drop` running (Decision 9).
- **Test constructors.** Every context has a test constructor with fakes, so a crate's tests can build one without a server.

### 4.3 Mapping rows for the design document

| C++ | Rust |
|---|---|
| `server_service<T>()`, T concrete | the `Arc<T>` field of the context of T's crate, through `self` or `ctx`; never `Option` |
| `server_service<I>()`, I one of the 30 interfaces | `Arc<dyn I>` (or the `OnceLock<Weak<dyn I>>` above) in the context of the crate that declares I |
| `OB_ISNULL(server_service<T>())` (149), `SERVER_MODULE_SCOPE` (112) | deleted, unless the site can run before the module graph exists; one inventory row per site says which |
| `GCTX.x`, `GCONF.p`, `GMEMCONF` | the context field named in the table above; `ctx.config().p()` |
| `X::get_instance()`, macro singletons | a context field; unchanging tables become `static` |
| `THIS_WORKER` deadline calls | the thread-local deadline with a scoped setter; copied explicitly when work moves threads |
| other `THIS_WORKER` calls | explicit fields of the request or execution context |
| `is_inited_` + `init()` | a constructor returning `Result<Self, ObError>` |
| `ObTimerTask` subclass (67 live) | `impl TimerTask`, holding `Weak<Owner>`, scheduled as `Arc<Self>` |
| `ObTimer` member (59) | one Rust `Timer` per C++ `ObTimer`, same name; never merged, never split |
| `lib::Threads` subclass (35), `ObReentrantThread` (3) | a set of worker threads from the runtime spawner |
| `ObSimpleThreadPool` (12), `ObAsyncTaskQueue`, `ObDedupQueue`/`ObUniqTaskQueue` | a task pool; dedup by key is an option |
| `ObDLinkBase<T>` + `ObDList<T>` (71) | by default a `VecDeque<T>` or `Vec<T>` owned by the list holder; when C++ unlinks from the middle by pointer, a slab with typed index links; one inventory row per class |
| `ObFuncExprOperator` subclass (313) | an entry in the static expression table (the enum or typed table of PLAN section 3), with context from the evaluation context |
| `getcwd()`, `"run/…"`-style relative paths | `ctx.base_dir().join(…)` |

### 4.4 The thread, queue and timer primitives

1. **One thread spawner** in the runtime context. Every engine thread starts there, with its C++ name, the configured stack size and, on macOS, the USER_INITIATED QoS of thread.cpp:341-345. Without the QoS, background threads of a detached server can run late, which the 1.2x gate would see (assumption: the C++ comment's reason also holds for Rust threads). A later wasm build replaces the spawner, not the crates.
2. **A set of worker threads** for the `lib::Threads` family, resizable because the DAG, IO and PX pools resize.
3. **A task pool**: a bounded queue and N workers. It returns an error when full, as the C++ limits do.
4. **`TimerService`, `Timer` and `TimerTask`**, per engine, with section 1.4's behavior as the contract: 4-128 workers; one task at a time per timer; next run at end plus delay; cancel lets a running run finish; wait polls; a stopped timer returns `OB_CANCELED`; each run resets the deadline and trace id. The intervals are copied from the C++ tasks unchanged, since freezes and merges change EST inputs (PLAN item 17). Identity for cancel is `Arc::ptr_eq`.
5. **A time wheel** for the transaction timer (100 ms) and the deadlock detector (10 ms).
6. **The request-worker pool**, keeping `ObAdaptiveWorkerPool`'s normal size and rescue cap.

No async runtime; this goes in the rulebook's "Async model" row. sql-nio already runs mio on plain threads, and C++ blocks in workers. The DAG scheduler and PX pools belong to their core and execution designs. sql-nio's two process globals, the `CONNS` registry (rust/sql-nio/src/registry.rs:23) and `NEXT_REQUEST_TICKET` (lib.rs:98), move into the reactor instance.

### 4.5 What stays in the binary crate

| Action | Decision |
|---|---|
| `chdir` (main.cpp:705) | Adopt the guideline. The binary may still `chdir` for operators. The engine takes `std::fs::canonicalize(base_dir)`, which equals `getcwd()` after the `chdir` (both return the path with links resolved), so the `pid_file` and `socket` values and the meta.db path stay byte-identical. |
| Signal mask and signal thread | Adopt. Block SIGTERM and SIGUSR1 in `main` before any thread starts. SIGTERM kills the process with SIGKILL (Decision 9). SIGUSR1 asks the engine to stop (question 5). 40-64 are dropped on macOS, where they are no-ops. Rust's std already ignores SIGPIPE at startup (from Rust documentation; not checked here). |
| `_Exit` on the last client (ob_server.cpp:1635-1672) | Not ported: embedded only (Decision 8). |
| `_Exit(0)` at the end of `wait()` (:1678) | Binary crate. `std::process::exit` runs `atexit` handlers and the islands' C++ static destructors while other threads still run. C++ avoids that with `_Exit`, and on macOS its command-line exits use `_exit` (ob_command_line_parser.cpp:96-105, written for LLVM, which is gone). Whether the islands' destructors are safe at exit is unchecked (assumption). Matching `_Exit` needs `libc::_exit` (question 2). |
| Malloc hook | Nothing to do on macOS: not built at 834bbee1e (src/oblib/lib/CMakeLists.txt:34-41; absent from the reference compile commands). |
| `#[global_allocator]`, the darwin malloc zone (main.cpp:649) | Adopt: binary crate. The zone decision belongs to island memory charging (PLAN item 19). |
| `panic=abort` | Adopt, as a workspace profile. rust/Cargo.toml:9-23 already sets it on the named profiles; the judge profile of PLAN Step 6 must set it too. Engine crates must not rely on unwinding or on abort. |
| Daemonizing, run/seekdb.pid with `lockf`, argv `--base-dir=` | Binary crate. `sdb.py` finds the process by the pid file and argv (sdb.py:121-131, 304-340). A `fork` must come before any thread (question 3). |
| setlocale (main.cpp:693-698), curl init, OpenSSL malloc, sd_notify, the log sink, the Windows service entry | Binary crate. Each is kept only if something still needs it: an island for the locale, a C HTTP library for curl. |
| The 1 MiB startup stack (main.cpp:877-882), `set_default_run_wrapper` (ob_server.cpp:1364) | Drop. |
| The timer service, the clock thread, the pool resize thread, the DDL slice store | Engine, per context: not process ownership. |

### 4.6 The prior-art boundary conventions

From abi-naming.md and ffi-mechanics.md, for the island shims (Decision 13):

| Convention | Decision and reason |
|---|---|
| The receiver is the first parameter of a callback, never a global | Adopt. It is the same rule as explicit context. vsag's `Allocator` objects carry a pointer to the vector budget owner, which its index handle keeps alive. |
| No receiver for callbacks into process singletons | Reject for the engine, which has no singletons now. Allowed only for callbacks into the named process-wide list, such as vsag's global logger routed to the log facade. |
| Long-lived receivers handed over at start, per-call ones passed per call | Adopt for island shims. |
| Named `extern "C"` functions instead of function-pointer tables, declared in one file | Adopt inside each island crate. |
| Two layers: safe logic taking closures, `unsafe` only in the `extern "C"` layer | Adopt. It matches Decision 14's named crates. |
| `shim` names for code that exists only during a partial port | Reject: one cutover (Decision 10) leaves no transitional shims. |
| `#[cfg(test)]` stubs for callback symbols | Adopt the aim, so crates test without the server. It is also why section 4.2 gives each context a test constructor. |

### 4.7 What Step 1 and Step 2a must produce

1. **The design document** fixes the four structs, the allowed process-wide list, the binding step and the mapping rows. The core build's first crate order already lists "context" (PLAN section 6).
2. **The inventory** gets one row per slot type (98), per `GCTX` field, per `SERVER_MODULE_SCOPE` site (can it run before the graph exists?), per `ObTimerTask` and `ObTimer` (interval, which timer), per `ObDLinkBase` subclass (list holder, which mapping) and per thread-pool subclass (primitive, size).
3. **A call-graph closure over the compile commands** records which functions take `ctx`, and puts it in the declaration index. Otherwise a caller's translator cannot know whether a callee translated in another unit gained the parameter. Virtual calls count every override.
4. **Step 2a** exercises it: the narrow binary builds a runtime and storage context with fakes for DDL and PL, and the disposable full pass counts missing-`ctx` errors.

## 5. Risks

- **A cell left empty.** It would stop the server at first use. The check at the end of the binding step catches it at startup.
- **An imprecise call graph** (function pointers, virtual calls) turns into mechanical compile-loop errors in Step 4.
- **Deadline copies.** Missing copies when work moves threads (PX, DAG) change timeout behavior; the rows for the 73 `set_timeout_ts` sites cover them.
- **Timer and QoS differences** move background work in time, which moves EST numbers (masked) and timing-sensitive cases (PLAN item 21).
- **`Arc` cycles** would block the later clean close; `Weak` for the cycles, the upward references and the tasks prevents them.

## 6. Questions for the developer

1. **Physical standby.** src/standby is 15,078 lines and built by default (`OB_ENABLE_STANDBY` ON, CMakeLists.txt:29). It takes 5 of the 26 start steps and opens a gRPC listener. No test under tools/deploy/mysql_test mentions standby, switchover or failover, and neither the plan, decisions.md nor the feasibility report mentions it. Is it in the port's scope?
2. **`unsafe` for a few OS calls.** macOS QoS, `_exit`, `fork`, `lockf`: name a platform crate in Decision 14's list? Or use only safe wrapper crates (nix, signal-hook) and accept `std::process::exit`, which runs island destructors, and no QoS unless a crate provides it?
3. **Daemon mode.** Keep the binary's `fork` (main.cpp:733-741)? `sdb.py` already detaches with `start_new_session`, and the judge passes `--nodaemon`. Keep `--initialize`, which its own comment marks for removal (ob_command_line_parser.cpp:188)?
4. **One engine per process, or several?** Should a later library build open two databases at once? This report assumes several may be needed, and keeps the process-global list short on that basis.
5. **SIGUSR1** today means a 5 s sleep, then `_Exit(0)` within 3 s, with no teardown. Keep it until the clean shutdown, or drop it? Keep the log-level signals once a platform has them?

## 7. How the counts were made

Scripts and outputs are under migration/design/evidence/.
- Section 1.1: `reach.py`, regex counts over `git ls-files src`. The files union: `git grep -l -P` with the same patterns plus the macro singletons.
- Slots:
  - `git grep -n 'server_service<'` gives 1,243 lines; `git grep -h -o 'server_service<[^>]*>' | sort -u` gives 122 spellings.
  - Bound and looked-up class names: the bind block of ob_server_runtime_controller.cpp:1250-1358 plus standby_module.cpp:209-210 (`bound98.txt`), and `lookup_types.txt`.
  - Direction: `slotmatrix.py`, which places each class by the header that declares it.
- Singletons: `git grep -h -o '[A-Za-z_:]*::get_instance()'`; macro list from `git grep -n '#define … get_instance()'`.
- Subclasses: `bases.py`, a comment-stripped base-clause scan (`timertasks.txt`, `sub_ObDLinkBase.txt`, `sub_ObFuncExprOperator.txt`). Compiled or not: unity bundles in /Users/colin/seekdb-dev/ref-834bbee1e/build_release/compile_commands.json resolved to 2,960 sources (`compiled_sources.txt`).
- Other counts: `ObTimer` objects by `git grep -P 'ObTimer\s+\w+(_|\[…\])\s*;'`; thread names by `git grep -o 'set_thread_name("…")'`; observed threads from the saved seekdb.log files (`threads_seen2.txt`); inner tables by `grep` over src/share/inner_table/ob_inner_table_schema_def.py.
