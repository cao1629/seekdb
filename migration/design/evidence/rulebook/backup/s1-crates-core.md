# Design section 1: the crates, the core, the server context and the manifest

This section turns ARCHITECTURE.md §1, §7 and §14, and the parts of §8 and §15 they touch, into rules that core authors and implementers follow and reviewers check. It follows ARCHITECTURE.md; where this section finds ARCHITECTURE.md wrong, it still follows it and gives the objection in section 8.

- Code facts are at the frozen base 834bbee1e; paths are from the repository root.
- "Must" marks a rule a reviewer rejects work for breaking.
- Counts made for this section come from scripts in /Users/colin/.claude/jobs/39d4f781/tmp/design/s1/ (section 7). Other counts cite the research report (R01, R07, R12) that made them.
- Comments inside the code examples explain the example. Translated code carries none (5.5).

## 1. The crate graph

### 1.1 The workspace

1. rust/Cargo.toml is the only workspace. Its `members` are the 39 crates of 1.2, in table order. standby is not a member while it is deferred (ARCHITECTURE §7.4).
2. Crate `<name>` lives in `rust/<name>/`. Its package name is `<name>`, and its lib name is `<name>` with `-` replaced by `_` (`ob-base` is `ob_base` in paths).
3. seekdb is the only server binary. ob-errno also builds the `ob_error` tool that family 6 runs, from tools/ob_error/src/ob_error.cpp (5.3). Every other crate is an `rlib`, sql-nio included: its `staticlib` type and its cbindgen build dependency go (rust/sql-nio/Cargo.toml).
4. A crate's `[dependencies]`, `[dev-dependencies]` and `[build-dependencies]` must name only workspace crates on its row in 1.2. A new downward edge on a row is a design amendment. An upward edge is never added.
5. observer depends on sql-nio as `optional = true`, under observer's feature `network`, which is on by default. No other crate depends on sql-nio (ARCHITECTURE §13, "networking behind cargo features").
6. A crate stays under 180K C++ lines. storage-tablet and sql-expr split as R01 §5.1 describes if Step 2a measures their `cargo check` above 60 s.

### 1.2 The crate table

This table is ARCHITECTURE §1.1's, reduced to what the rules need. A crate may use only the crates named in its row. Crates 7-39, except sql-nio, may also use crates 1 and 2. The named `unsafe` crates of ARCHITECTURE §8 are 3, 4, 5, 6, 12, 13 and 22. Every other crate carries `#![forbid(unsafe_code)]`.

| # | Crate | Tier | May also use |
|---|---|---|---|
| 1 | ob-errno | core | - |
| 2 | ob-base | core | 1 |
| 3 | ob-platform | core | 1 |
| 4 | ob-simd | core | 1 |
| 5 | ob-epoch | core | 1 |
| 6 | ob-clib-sys | leaf | 1 |
| 7 | ob-runtime | core | 3, 5, 6 |
| 8 | ob-values | core, value libraries | 4, 7 |
| 9 | ob-values-doc | leaf | 6-8 |
| 10 | ob-share | leaf | 7-9 |
| 11 | ob-schema | core | 7, 8, 10 |
| 12 | geo-sys | island | 7-10 |
| 13 | vsag-sys | island | 7 |
| 14 | ob-log | core build | 7, 8, 10 |
| 15 | storage-api | core | 7-12 |
| 16 | storage-sstable | core build | 4, 6-11, 15 |
| 17 | storage-tx | core | 5, 7-11, 14-16 |
| 18 | storage-tablet | core | 7-11, 14-17 |
| 19 | storage-engine | leaf | 7-11, 14-18 |
| 20 | storage-search | leaf | 4, 7-13, 15-19 |
| 21 | sql-parse-tree | core | - |
| 22 | sql-parser-sys | island | 7, 8, 21 |
| 23 | sql-session | SQL tier | 7, 8, 10, 11, 15 |
| 24 | sql-ir | core | 7-12, 15, 21, 23 |
| 25 | sql-exec | core | 7-11, 15, 18, 21, 23, 24 |
| 26 | sql-expr | SQL tier | 6-12, 15, 18, 20, 21, 23-25 |
| 27 | sql-das | SQL tier | 7-12, 15, 20, 21, 23-26 |
| 28 | sql-engine | SQL tier | 7-12, 15, 18, 19, 21, 23-27 |
| 29 | sql-parser | SQL tier | 7, 8, 10, 20-23 |
| 30 | sql-resolver | SQL tier | 6-8, 10, 11, 15, 20, 21, 23-29 |
| 31 | sql-rewrite | SQL tier | 8, 10-12, 21, 24-27, 29, 30 |
| 32 | sql-optimizer | SQL tier | 7, 8, 10, 11, 15, 21, 23-31 |
| 33 | sql-codegen | core | 8, 15, 21, 24-29, 32 |
| 34 | sql | leaf | 6-11, 15, 18, 20-33 |
| 35 | pl | leaf | 7-11, 15, 21, 23-34 |
| 36 | rootserver | leaf | 7-11, 14, 16-20, 23-25, 27, 28, 30, 32, 34, 35 |
| 37 | sql-nio | existing | none, not even 1-2 |
| 38 | observer | leaf | 7-37 |
| 39 | seekdb | binary | 3, 7, 38 |

### 1.3 The crate map as data

Every tool that places C++ code in a crate must read one file: the prefix map. That covers the manifest script, the declaration index and the edge scripts. At sign-off the map is copied to migration/crates.tsv. Until then it is /Users/colin/.claude/jobs/39d4f781/tmp/design/s1/crates-arch.tsv (189 rows), plus the moves in crates-fix1.tsv.

- The columns are `prefix`, `crate` and an optional `dir` (5.1).
- The longest matching prefix wins. A prefix that names no directory matches the file names that start with it, so `src/sql/engine/ob_` sends src/sql/engine/ob_operator.cpp to sql-exec.
- The map starts from R01's crates.tsv, with ARCHITECTURE §1.1's changes:
  - src/query/api/query/parser goes to sql-parse-tree;
  - ob_expr_regexp_context goes back to sql-expr;
  - share/ob_errno.\* and mysql_errno.h go to ob-errno;
  - the six `*_simd.cpp` files and the five data_plane vector distance headers go to ob-simd;
  - the vendored zstd source goes to ob-clib-sys.
- The fix-1 moves of 1.4 are the extra rows of crates-fix1.tsv.

The rerun on this map (`crate_analysis.py`) reproduces ARCHITECTURE's sizes, apart from the files the table moved without subtracting them from their old crate. ob-simd has 3,037 hand-written lines, not "about 3,300". storage-search has 28,538 lines, not 30,771, and storage-sstable, sql-exec, sql-engine, sql-parser and ob-share each lose under 1,100 lines.

### 1.4 Edges that break the table, and their fixes

1. An include line or a symbol reference from crate A to crate B is allowed only when B is on A's row. Every other edge gets a row in the edge ledger, migration/crate-edges.tsv, before Step 1's exit. That covers edges to a higher crate and edges to a lower crate the row does not name. The ledger's columns are `from`, `to`, `what` (a header or a symbol group), `count`, `fix` and `how`.
2. `fix` is one of ARCHITECTURE §1.2's five, or `row`, a design amendment that adds a lower crate to A's row. A `row` fix can never make a cycle, because every crate on a row comes earlier in the table.
3. On this map (section 7), the edges that break the table are, excluding standby and dropped code:

   | | Upward | Downward, not on the row |
   |---|---|---|
   | Include lines | 1,037 in 111 crate pairs | 32 in 10 pairs |
   | Symbol references | 2,935 in 132 pairs | 442 in 42 pairs |
   | Include lines, after the fix-1 moves below | 1,024 in 112 pairs | 42 in 14 pairs |
   | Symbol references, after the fix-1 moves below | 2,712 in 134 pairs | 461 in 46 pairs |

   The fix-1 moves applied are these:
   - share/ob_define.h into ob-base;
   - src/observer/vector_index/ob_vector_index_util.\* and its query/api header into storage-search;
   - ob_monitor_node.\* into sql-ir;
   - share/ob_rpc_struct.\*, ob_ddl_common.\* and ob_autoincrement_service.\* into ob-schema;
   - the chunk, temp-block, RA and column stores into sql-exec.

   Unlisted downward edges get ledger rows like upward ones. Objection 3 gives two, with the `row` fixes proposed for them.
4. **Edges the scripts cannot see.** The scripts map rust/sql-nio/include to dropped code, so the 10 files that include nio.h show no crate edge. They get ledger rows (1.5).
5. **Trait placement for fixes 2-4.**
   - The trait is declared in the lowest crate whose code calls it.
   - It keeps the C++ interface's name where one exists.
   - It is implemented in the crate that holds the C++ implementation.
   - Callers reach it through an `OnceLock<Weak<dyn Trait>>` cell in a context (3.2) or in the service that calls it.

Worked example, fix 2. `ObIRootCommandService` (src/query/api/query/command/ob_root_command_service.h:36) is implemented by `ObLocalManagementService` (src/rootserver/ob_local_management_service.h:79, `parallel_create_table` at the .cpp:646). SQL reaches it through the exec context (src/sql/engine/ob_exec_context.cpp:51-56) and calls it at src/sql/engine/cmd/ob_ddl_executor_util.cpp:295.

```cpp
class ObIRootCommandService {
public:
  virtual int parallel_create_table(const obcall::ObCreateTableArg &arg, obcall::ObCreateTableRes &res) = 0;
};
query::ObIRootCommandService &ObExecContext::root_command_service() const;
```

```rust
// sql-exec: the trait keeps the C++ name and methods
pub trait ObIRootCommandService: Send + Sync {
    fn parallel_create_table(&self, arg: &ObCreateTableArg, res: &mut ObCreateTableRes) -> ObResult;
}
pub struct SqlContext {
    storage: Arc<StorageContext>,
    root_command_service: OnceLock<Weak<dyn ObIRootCommandService>>,
}
impl SqlContext {
    pub fn root_command_service(&self) -> Arc<dyn ObIRootCommandService> {
        self.root_command_service.get().and_then(Weak::upgrade).expect("bound by Server::open")
    }
}
// rootserver
impl ObIRootCommandService for ObLocalManagementService {
    fn parallel_create_table(&self, arg: &ObCreateTableArg, res: &mut ObCreateTableRes) -> ObResult { .. }
}
```

`ObCreateTableArg` comes from share/ob_rpc_struct.h, which the fix-1 moves put in ob-schema, so sql-exec (25) may name it.

### 1.5 sql-nio loses its C ABI

1. The 33 `#[no_mangle]` functions become ordinary `pub fn`s. cbindgen, cbindgen.toml and include/nio.h go, and the crate moves to edition 2024.
2. The four reverse callbacks (rust/sql-nio/src/ffi_types.rs:53-72) become one trait. It takes the C++ class name, `ObSqlSockHandler`, and has the methods `on_connect`, `on_readable`, `on_disconnect` and `on_close`. observer implements it. The connection is passed as a Rust handle, not as `*mut c_void`.
3. `CONNS` (registry.rs:23) maps C++ session pointers to connections, so it goes with the pointers. `NEXT_REQUEST_TICKET` (lib.rs:98) may stay a static, under 3.6 item 5.
4. The unix `libc` calls (reactor.rs:777, tls.rs:225) become socket2 and std calls. The Windows named-pipe creation (transport.rs:176-191) moves to ob-platform. sql-nio's row names no crate, so observer passes sql-nio a function that creates pipe instances, which observer gets from ob-runtime (objection 2). sql-nio then has no `unsafe` and gets `#![forbid(unsafe_code)]`.
5. The encoders of response.rs stay sql-nio's API (ARCHITECTURE default 28). row_encode.rs and response_api.rs go as C-ABI files. The packed-row encoder behind `nio_encode_mysql_packed_row_blob` stays a Rust function in sql-nio, because the SQL tier calls it (src/sql/engine/expr/ob_expr_output_pack.cpp:431, :439). SQL-tier callers reach it through a trait in sql-exec that observer implements (fix 2; objection 4).
6. The C++ that calls nio.h is placed like this:
   - The glue moves to observer: src/oblib/rpc/ob_sql_request_operator.cpp and, in src/oblib/rpc/obmysql, ob_sql_nio_server.\*, ob_sql_sock_handler.\*, ob_sql_sock_session.\*, ob_login_info.h and ob_mysql_packet_storage.h.
   - The global `SQL_REQ_OP` (src/oblib/rpc/ob_sql_request_operator.h:80-81) becomes the trait `ObSqlRequestOperator` in ob-runtime. The session code uses it (src/sql/session/ob_basic_session_info.h:34), so it is a RuntimeContext cell.

## 2. The core's scope

### 2.1 What the core replaces

ARCHITECTURE §1.3 decides the scope. About 500K lines are built first and exercised by Step 2a. The sstable formats, the WAL and crash recovery join during the core build. Lines are live hand-written C++ at 834bbee1e (`core_scope.py`, using coverage_groups.py's patterns for the report's six rows):

| Row | Prefixes under src/ | Lines | Running total |
|---|---|---|---|
| Foundation substrate | oblib/lib/{alloc,allocator,container,hash,string,rc,lock,atomic,list,queue} | 54,032 | 54,032 |
| Runtime | oblib/lib/{thread,utility,file,restore}, share/{io,cache}, storage/scheduler, data_plane/api/data_plane/scheduler | 49,738 | 103,770 |
| Value and datum types | oblib/common/{object,datum}, share/{datum,rc} | 21,015 | 124,785 |
| Statement IR | sql/resolver/expr, sql/resolver/dml/\*stmt\* | 45,353 | 170,138 |
| Execution framework | query/api/query/engine, sql/engine/ob_{operator,exec_context,physical_plan}\*, sql/engine/expr/ob_expr_frame_info\*, sql/engine/basic/ob_pushdown_filter\*, sql/code_generator | 46,861 | 216,999 |
| Storage and transaction core | storage/{tablet,meta_mem,memtable,tx,multi_data_source,tx_table}, data_plane/api/data_plane/access | 133,109 | 350,108 |
| Schema object model | share/schema, observer/schema | 87,821 | 437,929 |
| Storage entry points | storage/{access,tx_storage,ls} | 55,282 | 493,211 |
| Core build: sstable formats | storage/blocksstable | 87,627 | 580,838 |
| Core build: WAL | logservice | 32,705 | 613,543 |
| Core build: recovery | storage/{slog,slog_ckpt,checkpoint,meta_store} | 14,058 | 627,601 |

910 in-build map units touch these prefixes, inside the report's 550-1,050 core stems.

1. A map unit with a file under these prefixes is a core input. It must appear in migration/core-manifest.tsv `inputs` and never in manifest.tsv.
2. seekdb has one log stream (src/share/ob_ls_id.h:34, :47). `ObLSService` holds one `ObLS *ls_` (src/storage/tx_storage/ob_ls_service.h:138), and `get_ls(ObLS *&)` takes no id (:70). The core does not port the `get_ls` call: code that makes it reaches the single log stream's parts from `StorageContext` instead (ARCHITECTURE §1.3). The storage section designs those parts.

### 2.2 Core crates also hold translated units

- A core crate may hold translated units. storage-tablet holds storage/lob (14,210 lines, R01 §1.1). ob-values holds ObNumber, time, charset and the casts, which fan out after the foundation API freezes (PLAN §6).
- Such a unit is a Step 3 row in manifest.tsv, translated against the frozen core API.
- Core authors may move more units into the core manifest. The foundation's logging is one example: ob-base's home modules need it (5.5). The completeness check of 5.3 keeps each unit in exactly one place.

### 2.3 The crates Step 2a builds

ARCHITECTURE §15's narrow path needs crates 1, 2, 3, 7, 8, 10, 11, 12, 15, 17, 18, 21-35 in minimal form (pl only as fakes), plus 37, 38 and 39.
- geo-sys is needed for §15's one geo call on a grown stack.
- Crates 4, 5, 6, 9, 13, 14, 16, 19, 20 and 36 are not needed. §15's DDL goes to an in-memory catalog, not to rootserver, and the first structures sit behind locks (ARCHITECTURE §11).

## 3. The server context

### 3.1 The four contexts

| Context | Crate | Holds `Arc` of | Replaces |
|---|---|---|---|
| `RuntimeContext` | ob-runtime | - | GCONF, GMEMCONF, most GCTX fields, `ObTimerService`, `ObIOManager`, `ObKVGlobalCache`, the `ObMallocAllocator` budgets |
| `StorageContext` | storage-tablet | `RuntimeContext` | storage slots and singletons, `GCTX.schema_service_`, `GCTX.sql_proxy_` |
| `SqlContext` | sql-exec | `StorageContext` | SQL-side slots and singletons |
| `ServerContext` | observer | `SqlContext` | `ObServer`'s members, rootserver's services, the stop request |

```rust
pub struct StorageContext { runtime: Arc<RuntimeContext>, /* services */ }
impl Deref for StorageContext {
    type Target = RuntimeContext;
    fn deref(&self) -> &RuntimeContext { &self.runtime }
}
```

1. There are no other context types.
2. Fields are private. Each has an accessor method of the field's name that returns `&T`, or `&Arc<T>` where callers keep a clone.
3. Each context derefs to the one below, so `ctx.config()` works on all four. Accessor names must be unique across the four contexts.
4. Contexts are built only by `Server::open`, and by `for_tests` constructors with fakes, compiled under each crate's `testing` feature.

### 3.2 Where each service lives

A service is an object `Server::open` builds once per engine.

1. A service is a field of the lowest context whose crate is, or may use, the service's crate.
   - Services defined in crates 19-20 and 26-36 are therefore `ServerContext` fields, because no lower context may name those crates.
   - Of 1,223 `server_service<T>()` lookups, the looked-up service lands in `StorageContext` 753 times, `ServerContext` 358, `SqlContext` 69 and `RuntimeContext` 43 (`slot_ctx_final.py`).
2. A trait whose implementation sits above the context that needs it is a cell, `OnceLock<Weak<dyn Trait>>`, filled by the binding step. The two real cycles are cells too. One is the log service and the LS service (src/storage/tx_storage/ob_log_storage_adapter.h:31, :56-57). The other is SQL and PL: `ObSql::init` takes `pl::ObPL &` at src/sql/ob_sql.h:111, and the PL engine is initialized after SQL (src/observer/omt/ob_server_runtime_controller.cpp:1446 against :1472).

| C++ (uses, from R07 or `gctx_fields.py`) | Context field |
|---|---|
| `GCTX.self_addr()` (175), `status_` (10), `start_service_time_`, `in_bootstrap_`, `sys_package_ready_`, `server_role_` | `RuntimeContext`, as plain values and atomics |
| GCONF (682), `GCTX.config_`, `config_mgr_`, GMEMCONF (23) | `RuntimeContext::config()`, an `ObServerConfig` |
| `GCTX.meta_db_pool_` (24) | `RuntimeContext`, after share/storage's SQLite pool moves to ob-runtime (a fix-1 ledger row: src/share/config/ob_config_storage.h, in ob-runtime, includes the pool's header) |
| `ObIOManager`, `LOCAL_DEVICE_INSTANCE`, `ObKVGlobalCache`, the timer service, `bandwidth_throttle_` | `RuntimeContext` |
| `SQL_REQ_OP` | `RuntimeContext` cell (1.5) |
| `GCTX.schema_service_` (294), `ObMultiVersionSchemaService::get_instance()` (67) | `StorageContext` (ob-schema is crate 11) |
| `GCTX.sql_proxy_` (308), `ddl_sql_proxy_` | `StorageContext` cell for `ObISQLClient` (ob-share), implemented in observer |
| `ObLSService` (177 lookups; its one log stream, 2.1 rule 2), `ObTransService`, `OB_STORAGE_OBJECT_MGR` (126), `OB_SERVER_BLOCK_MGR`, `OB_STORE_CACHE`, `SERVER_STORAGE_META_SERVICE`, `OB_TS_MGR` | `StorageContext` |
| `ObSQLSessionMgr` (47 lookups) | `SqlContext` |
| `ObLocalManagementService` (110), `ObPlanCache`, `ObOptStatManager`, the compaction and DDL services | `ServerContext`, with cells below where lower crates call them |

### 3.3 How `Server::open` builds and binds them

```rust
pub struct ServerOptions {
    pub base_dir: PathBuf,
    pub data_dir: PathBuf,
    pub redo_dir: PathBuf,
    pub port: i32,
    pub use_ipv6: bool,
    pub parameters: Vec<(Vec<u8>, Vec<u8>)>,
    pub variables: Vec<(Vec<u8>, Vec<u8>)>,
    pub spawner: Arc<dyn ThreadSpawner>,
}
impl Server {
    pub fn open(options: ServerOptions) -> ObResult<Server> { .. }
    pub fn request_stop(&self) { .. }
    pub fn wait_stop_request(&self) { .. }
}
```

The fields are `ObServerOptions`'s engine fields (src/observer/ob_server_options.h), plus the spawner. The binary keeps `log_level_`, `nodaemon_`, `initialize_` (used only in main.cpp:733 and :793) and the Windows service flags. `role_` accepts PRIMARY only, as src/standby/standby_module_disabled.cpp:81-83 does.

`Server::open` runs these steps in order. A step that fails returns its code.

1. Canonicalize `base_dir`, run the data-version gate, then open meta.db (ARCHITECTURE §12).
2. Build `RuntimeContext`: config, budgets, IO, the KV cache, the timer service (the C++ order of ob_server.cpp:624-871).
3. Replay the server slog on the calling thread. The replay returns the runtime meta instead of calling `ObIServerRuntime::create_runtime` (src/storage/meta_store/ob_server_storage_meta_replayer.cpp:82). An empty directory takes the bootstrap path (ob_server.cpp:1331-1371).
4. Build the services in `obs_init_modules`' order (ob_server_runtime_controller.cpp:1362-1509), each through a constructor that takes its dependencies. Then build `StorageContext`, `SqlContext` and `ServerContext` from them.
5. The binding step: fill every cell, then call each context's `check_bound()`, which fails if any cell is empty.
6. Start: `RuntimeContext`'s threads, then each service's `start` in `obs_start_modules`' order (:1511-1547), then the rest of `ObServer::start` (ob_server.cpp:1185-1313). The listener starts last (:1291), and the status becomes serving (:1313).

Rules:

1. No constructor starts a thread or schedules a timer task. Threads start only in step 6, after the check, as ARCHITECTURE §7.1 requires. So replay must run without runtime threads (assumption, checked in the core build; objection 6).
2. `init()` and `is_inited_` become the constructor; `is_inited_` has 5,290 uses in 1,073 files (`git grep -o -P '\bis_inited_\b' -- 'src/*.h' 'src/*.cpp'`). Their `OB_NOT_INIT` paths go, for services. A value or container whose "not initialized" state callers can observe keeps it (the errors and constructs sections).
3. Every cell is filled once. A second `set` is a bug (`expect`).
4. `Weak<dyn Trait>` comes from `Arc::downgrade(&x) as Weak<dyn Trait>`. A service that must hand `Weak<Self>` to its own tasks is built with `Arc::new_cyclic`.

Worked example: `ObSql::init` (src/sql/ob_sql.h:106-120) takes 15 arguments. Five of them are `ObServer` itself as five interfaces, and one is `pl_engine_` before its own init (ob_server_runtime_controller.cpp:1446-1462).

```rust
impl ObSql {
    pub fn new(ctx: Arc<SqlContext>, plan_cache: Arc<ObPlanCache>, ps_cache: Arc<ObPsCache>) -> ObResult<Arc<ObSql>> { .. }
}
// in Server::open, the binding step
sql_ctx.bind_root_command_service(Arc::downgrade(&local_management_service) as Weak<dyn ObIRootCommandService>);
server_ctx.check_bound()?;
```

The other thirteen arguments come from `SqlContext`: `addr` from `self_addr()`, the rest as fields or as cells filled in step 5, one per interface.

### 3.4 How code reaches process state

1. **Services** hold `Arc`s of the services and lower contexts they use, given at construction.
   - A service must never hold an `Arc` of its own context or a higher one, because that makes an `Arc` cycle.
   - An object a service owns, such as a task or a sub-object, holds `Weak` of its owner.
2. **Other functions** that reach process state take `ctx` after `self`. A free or associated function takes it first.
   - Its type is the lowest context that holds everything the function reaches, directly or through its callees (virtual calls count every override).
   - The call-graph closure of R07 §4.7 computes this, and the declaration index records it. Deref coercion lets a caller pass a higher context.
3. **The SQL tier** passes the context inside the execution context: `ObExecContext` holds `&'q SqlContext`. A function that takes the exec context or the eval context takes no separate `ctx`.
4. **Lookups no context can serve.** Some callers' crates can name no context that holds the service. That is 443 of the 1,195 lookups outside standby (37%): storage-tx 158, rootserver 121, storage-engine 72, storage-sstable 24, storage-tablet 17, sql-engine 15, sql-das 13 and 23 others (`slot_ctx_final.py`, counting a higher context the caller may name as reaching the lower ones). The largest cases:
   - storage-tx looking up its own services, because `StorageContext` sits above it in storage-tablet;
   - rootserver looking up `ObLocalManagementService`.

   For these, rule 1 applies: the owning object holds the `Arc` or `Weak`. Otherwise the function takes the service as a parameter after `ctx`, per the lookup's inventory row. The site must never use a `static` (objection 5).
5. **Null checks.** The 149 null checks and the 112 `SERVER_MODULE_SCOPE` guards (src/share/rc/ob_server_runtime.h:230-232) go, unless the site can run before the module graph exists. The site's inventory row decides.
6. **Weak upgrades.** A `Weak::upgrade` cannot fail while the engine runs, because nothing is torn down before parity (Decision 9). It uses `expect`, except in timer tasks and background loops, which return when their owner is gone.

Worked example of rule 3: src/sql/das/ob_das_retry_ctrl.cpp:48-50.

```cpp
} else if (OB_ISNULL(GCTX.schema_service_)) {
  ret = OB_ERR_UNEXPECTED;
} else if (OB_FAIL(GCTX.schema_service_->get_runtime_schema_guard(schema_guard))) {
```

```rust
let ctx = das_ref.get_exec_ctx().sql_ctx();
// the OB_ISNULL branch is deleted: DAS retries run only while serving
ctx.schema_service().get_runtime_schema_guard(&mut schema_guard)
```

Worked example of rules 4 and 6: src/rootserver/ddl_task/ob_modify_autoinc_task.cpp:283-287.
- The C++ looks up `ObLocalManagementService` through its slot, then checks `is_inited_` and null (`OB_ERR_SYS`).
- In Rust, the object that creates the task passes `Weak<ObLocalManagementService>` to its constructor. The field keeps the C++ local's name with a trailing `_`, and the body starts `let local_management_service = self.local_management_service_.upgrade().expect("owner alive");`. Both checks go.

### 3.5 C++ forms and their Rust replacements

| C++ | Rust |
|---|---|
| `server_service<T>()`, `GCTX.x`, `X::get_instance()`, macro singletons | the context field of 3.2, reached as 3.4 says; a table that never changes becomes a `static` (3.6) |
| `server_service<I>()`, I an interface | the `Arc<dyn I>` field or the cell |
| `GCONF.p`, `GMEMCONF` | `ctx.config().p()`, keeping the parameter's name |
| `getcwd()` then a relative path the engine opens (ob_server.cpp:1704, :1788; src/storage/ob_file_system_router.cpp:123) | `ctx.base_dir().join(..)` |
| `getcwd()` in a printed value (the `pid_file` and `socket` system variables, src/sql/session/ob_system_variable.cpp:2494-2533) | the C++ `"%s/%s"` over `base_dir`'s bytes, which differs from `join` only for base dir `/` |
| `ObTimer` member (59), `ObTimerTask` subclass (67 live) | `Timer`, `impl TimerTask` holding `Weak<Owner>` (3.7) |
| `THIS_WORKER` deadline calls; other `THIS_WORKER` calls | the thread-local deadline of ARCHITECTURE §11; explicit fields of the request or exec context |
| `lib::Threads::set_default_run_wrapper` (ob_server.cpp:1364) | nothing: both definitions of the dispatch ignore the wrapper (R07 §1.2) |

### 3.6 Process-wide state

Only these may be process-wide, as `static`, `LazyLock` or `thread_local!` (ARCHITECTURE §7.1):

1. **Tables that are the same for every engine in the process:** the expression registry, the error catalog, charsets, the decimal and number constants of `init_sql_expr_static_var` (src/sql/ob_sql_init.h:60-71), and the generated system-variable metadata.
2. **The logging facade and its level.** seekdb installs the sink.
3. **The `DEBUG_SYNC` and tracepoint registries**, under their C++ names.
4. **Hooks a third-party library holds once per process**, such as vsag's logger and CRoaring's memory hook.
5. **Counters that only hand out unique numbers**, such as arena numbers and sql-nio's request ticket.
6. **The thread-locals of ARCHITECTURE §11.**

A value computed from one engine's options, config or base dir is a context field, even where the C++ writes it into a static table. Example: `ObPreProcessSysVars::init_sys_var` (called at ob_server.cpp:706) writes the data dir, the `pid_file` and `socket` paths, the port and the `--variable` values into the static `ObSysVariables` defaults (`set_value`, src/share/system_variable/ob_system_variable_init.h:86-89). In Rust those defaults are a `SqlContext` field, filled at start from the static table, the config and the options (R11 §9.1).

A gate lists every `static` and `thread_local!` in engine crates. It fails on any item missing from migration/process-statics.tsv, which gives each item's crate, its name and its list item above.

### 3.7 Threads and timers

```rust
pub trait ThreadSpawner: Send + Sync + 'static {
    fn spawn(&self, name: &str, stack_size: usize, body: Box<dyn FnOnce() + Send + 'static>) -> ObResult<JoinHandle<()>>;
}
pub trait TimerTask: Send + Sync + 'static {
    fn runTimerTask(&self);
    fn timeout_check(&self) -> bool { false }
}
impl Timer {
    pub fn new(service: &Arc<TimerService>, name: &str) -> Timer { .. }
    pub fn schedule(&self, task: Arc<dyn TimerTask>, delay: i64, repeate: bool, immediate: bool) -> ObResult { .. }
    pub fn cancel(&self, task: &Arc<dyn TimerTask>) { .. }
    pub fn cancel_all(&self) { .. }
    pub fn wait_task(&self, task: &Arc<dyn TimerTask>) { .. }
    pub fn stop(&self) { .. }
}
```

1. Every engine thread starts through the `ThreadSpawner` in `RuntimeContext`. That includes the resizable worker set, the bounded task pool, the request workers (sized as `ObAdaptiveWorkerPool`) and the two time wheels.
   - ob-runtime's native spawner sets the C++ thread name and the stack size.
   - On macOS it also sets the USER_INITIATED QoS of src/oblib/lib/thread/thread.cpp:341-345, through ob-platform.
   - seekdb builds that spawner and passes it in `ServerOptions`, so a library host can pass its own. `std::thread::spawn` is banned elsewhere by clippy's `disallowed-methods` (R12 §3).
2. `TimerService` is a `RuntimeContext` field, not a process static (the C++ static is at ob_timer_service.h:129-133). It keeps 4-128 workers and the 10,000-task limit (:192-196).
3. The contract is ARCHITECTURE §7.2's:
   - one task at a time per timer (ob_timer_service.cpp:534-548);
   - the next run is completion time plus the delay (:477);
   - cancel lets a running task finish;
   - a stopped timer returns `OB_CANCELED` (ob_timer.cpp:138);
   - intervals are copied unchanged.

   Task identity is `Arc::ptr_eq`. Each C++ `ObTimer` object is one `Timer` with the same name; timers are never merged or split.
4. No async runtime.

Worked example: `ObPlanCacheEliminationTask` (src/sql/plan_cache/ob_plan_cache.h:197; `runTimerTask` at the .cpp:2226-2247).
- In the C++, the task holds a raw `plan_cache_` set at :349, is scheduled at :351 and reads GCONF.

```rust
pub struct ObPlanCacheEliminationTask {
    pub(crate) plan_cache_: Weak<ObPlanCache>,
    pub(crate) run_task_counter_: AtomicI64,
}
impl TimerTask for ObPlanCacheEliminationTask {
    fn runTimerTask(&self) {
        let Some(plan_cache) = self.plan_cache_.upgrade() else { return };
        let run_task_counter = self.run_task_counter_.fetch_add(1, Ordering::SeqCst) + 1;
        let auto_flush_pc_interval = plan_cache.ctx().config()._ob_plan_cache_auto_flush_interval() / (1000 * 1000);
        ..
    }
}
```

`ObPlanCache` is built with `Arc::new_cyclic`, so it can give the task `Weak<ObPlanCache>`. It holds `Arc<SqlContext>`, a lower context than the `ServerContext` that holds it. The counter is atomic, because `runTimerTask` takes `&self` (ARCHITECTURE §11).

## 4. The binary crate

### 4.1 What seekdb does, in order

seekdb's `main` mirrors `inner_main` (src/observer/main.cpp:645-829). No engine crate does any of these steps.

| Step | C++ | seekdb |
|---|---|---|
| Allocator | main.cpp:649, malloc-zone promotion (ob_malloc.cpp:178-190) | `#[global_allocator]` over ob-platform's jemalloc impl; ob-platform promotes the zone |
| Signal mask | :678, before any thread | nix `pthread_sigmask` blocks SIGTERM, SIGUSR1 and SIGPIPE (40-64 do not exist on macOS) |
| Arguments | `parse_args` (ob_command_line_parser.cpp) | the same options; no `--embedded` (Decision 8) |
| Base dir | `chdir` at :705 | optional `chdir`; the engine uses `canonicalize(base_dir)`, which equals `getcwd()` after the `chdir` |
| Directories, checks | :720-728: uid of etc/, run/ log/ etc/, the run/seekdb link | the same; the engine also creates what it writes |
| Log sink | :755-760, log/seekdb.log, after the daemon step | seekdb installs ob-base's file sink with the parsed level, before `fork`, so the next row can log |
| Data-version check | none today (ARCHITECTURE §12 item 5) | seekdb calls ob-runtime's `ObDataVersionMgr::check_base_dir` before `fork`, so a refusal reaches the terminal and the shell gets status 1; `Server::open` checks again for library hosts |
| Daemon, pid file | :733-746; `start_daemon` (src/oblib/lib/utility/utility.cpp:1590-1646) | `fork` and `lockf` of run/seekdb.pid through ob-platform, before any thread; after `fork`, jemalloc's background threads are turned on again (ob_malloc.cpp:170-175) |
| Engine | `init`, `start` at :798, :809 | `Server::open(options)` |
| `--initialize` | `_exit(OB_SUCC(ret) ? 0 : 1)` at :816 | the same code after `open` returns |
| Signal thread | ob_signal_handle.cpp:56-81, started at ob_server.cpp:1110 | SIGTERM calls `raise(SIGKILL)` (:129-134); SIGUSR1 calls `request_stop`, which keeps `prepare_stop`'s 5 s sleep and `set_stop` (ob_server.cpp:1473-1497) |
| Wait, exit | `wait()` polls `stop_` every 3 s, then `_Exit(0)` (ob_server.cpp:1663-1680); `_exit(1)` on failure (main.cpp:823) | `wait_stop_request`, then ob-platform's `_exit(0)`; `_exit(1)` on failure |

Rules:
1. `std::process::exit`, `std::process::abort`, `std::env::set_current_dir`, `fork` and signal functions are banned outside seekdb and ob-platform, through clippy's `disallowed-methods`.
2. Engine code ends the process only by panicking, and `panic = "abort"` turns that into an abort.
3. seekdb reaches the engine only through observer, and ob-platform and ob-runtime for the process actions above.
4. seekdb's C++ also includes share, sql and storage headers. Those uses (version text, memory printing for signals 49, 59, 63) go through observer or are dropped with those signals.

### 4.2 What is not ported

| C++ | Reason |
|---|---|
| The 1 MiB mmap'd stack (main.cpp:877-882) | stacks come from the spawner and §11's rules |
| `setlocale` (:693-698) | Rust leaves the process in the "C" locale. That matches :696's `LC_CTYPE`. `LC_TIME` and `LC_NUMERIC` would only matter for grouping and locale time names, and no C or C++ left in the product calls `strftime`, `strptime`, `nl_langinfo` or `localeconv` (the locale search of section 7 finds them only in engine files, which become Rust) |
| `curl_global_init` (:751), `init_ssl_malloc` (:729), `sd_notify` (:811) | no curl (ureq, R12 §4); OpenSSL stays only inside S2; systemd is Linux-only |
| `_Exit` on the last client (ob_server.cpp:1635-1672); the malloc hook (src/oblib/lib/CMakeLists.txt:34-41) | embedded only; Linux-only |
| `observer.destroy()` and `unlink(PID_FILE_NAME)` (main.cpp:825, :828) | never reached today, because `wait()` ends in `_Exit(0)` |

### 4.3 Physical standby

Physical standby is deferred (ARCHITECTURE default 2). The Rust build starts as `OB_ENABLE_STANDBY=OFF` does (CMakeLists.txt:29).
- Its `prepare_service_start` still bootstraps a primary (src/standby/standby_module_disabled.cpp:76-91).
- The bootstrap telemetry report it then sends (:89) is dropped too.
- src/standby's units go to migration/not-translated.tsv with the reason `deferred`.

## 5. Naming and output paths

### 5.1 Target paths

1. A translated unit writes `rust/<crate>/src/<dir>/<stem>.rs` (ARCHITECTURE §14 rule 1). The parts come from the unit's `source` file:
   - `<crate>` is the source's longest-prefix match in the crate map.
   - `<dir>` is the source's directory below the matched prefix's directory. For a prefix that names no directory, the prefix's parent directory is used.
   - If the matched row has a `dir` value, `<dir>` starts with it.
   - `<stem>` is the source's stem, unchanged.
2. On ARCHITECTURE's map this gives 3,466 targets for the in-build units, with no two alike (`paths.py`). The fix-1 move of share/ob_define.h creates one collision: `ob-base/ob_define` also comes from src/oblib/lib/ob_define.h. A moved row whose target collides must carry a `dir` value, so the moved header goes to rust/ob-base/src/share/ob_define.rs.
3. A class declared in query/api or data_plane/api goes where its unit's .cpp is. The map keys every unit that has a .cpp by that .cpp's path.
4. A directory named after a Rust keyword stays on disk and is declared with `r#`: src/sql/engine/pdml/static holds 7 units (`mod r#static;`).
5. A stem named `lib`, `main` or `mod` takes a trailing `_`. The only case is src/observer/main.cpp, which becomes rust/seekdb/src/main_.rs, because the generated main.rs is the binary's root.
6. A core unit replacing exactly one C++ stem takes that stem's path. Other core modules take the design's names. The manifest script checks that no two targets are the same, core targets included.

| Source | Target |
|---|---|
| src/sql/optimizer/ob_join_order.cpp (17,680 lines) | rust/sql-optimizer/src/ob_join_order.rs, then ob_join_order_p01.rs onward |
| src/observer/mysql/obmp_packet_sender.cpp, with src/query/api/query/protocol/ob_mysql_packet_sender.h | rust/observer/src/mysql/obmp_packet_sender.rs |
| src/storage/compaction/ob_tablet_merge_task.cpp | rust/storage-engine/src/compaction/ob_tablet_merge_task.rs |
| src/sql/engine/ob_operator.cpp (prefix `src/sql/engine/ob_`) | rust/sql-exec/src/ob_operator.rs |
| src/share/io/ob_io_manager.cpp (prefix `src/share/io`) | rust/ob-runtime/src/ob_io_manager.rs |

### 5.2 Files of 4,000 lines or more

53 live hand-written files have 4,000 lines or more, 378,637 lines in all. 16 of them (95,820 lines) are core inputs, and 37 (282,817) are Step 3 splits (`big_files.py`; R12 counted 54 by `wc -l`).

1. The head unit keeps the map's key and writes `<stem>.rs`. It holds the types and the header's inline functions.
2. The pieces `<unit_id>.p01`, `.p02` and on write `<stem>_p01.rs` and on, in source order. Each piece holds `impl` blocks and functions for one contiguous range of the .cpp, cut at a function boundary, under 4,000 lines.
3. Every member of a split type is `pub(crate)`, and so is every function a piece defines.
4. A header of 4,000 lines or more is cut at class boundaries into pieces that each hold whole types. The only leaf case is share/ob_rpc_struct.h (4,032 lines).
5. The declaration index gives each item's piece path.

### 5.3 The manifests

1. migration/manifest.tsv has the columns `source`, `target`, `unit_id`, `kind` and `inputs`, in that order. queue_runner.mjs reads only the first two.
2. Rows follow migration/depmap/order.txt, dependencies first. The rows are written by migration/scripts/make_manifest_seekdb.py, which takes `--crates migration/crates.tsv` in place of `--sub` pairs, because a target depends on the matched prefix.
3. `source` is the unit's .cpp, .cc or .c whose path without its extension equals the unit key; otherwise it is the unit's first header.
4. `kind` is `stem` (one unit, one file), `split` (a head or a piece) or `subsystem` (core units only).
5. `inputs` are the unit's files from migration/depmap/units.tsv, with `:<from>-<to>` line ranges for pieces.
6. migration/core-manifest.tsv uses the same columns. `unit_id` is `core/<crate>/<module>`. `source` is the first C++ file replaced, or `-` for new code (ob-platform). `inputs` lists the map unit ids the module replaces.
7. migration/not-translated.tsv lists every other in-build unit with one reason: `island`, `generated`, `data`, `vendored`, `dead`, `dropped` or `deferred`.
8. Completeness check: every in-build unit of units.tsv (3,469) must appear exactly once across manifest.tsv's `unit_id`s, core-manifest.tsv's `inputs` and not-translated.tsv.
9. One unit the reference build does not compile is still translated: tools/ob_error/src/ob_error (725 lines, map `in_build = no`). Family 6 needs it (PLAN §4). A crate-map row `tools/ob_error/src`, ob-errno, `bin` sends it to rust/ob-errno/src/bin/ob_error.rs. Its os_errno table comes from gen_os_errno.pl's Rust back end (ARCHITECTURE §1.4).

### 5.4 Identifiers

1. Translated items keep their C++ names (ARCHITECTURE §14 rule 3, a default the developer confirms): `ObJoinOrder`, `runTimerTask`, fields with their trailing `_`, and enumerators and constants in capitals. Macros become lowercase `macro_rules!`.
2. A Rust keyword becomes a raw identifier (`r#type`). `self`, `Self`, `super` and `crate` take a trailing `_`.
3. A core type replacing one C++ type keeps its name (`ObDatum`). New core items take plain Rust names, fields included. ARCHITECTURE §7's `Timer`, `TimerTask`, `TimerService` and `ServerOptions` are used as §7 names them (objection 7).
4. A nested type `Outer::Inner` becomes a module-level type named `Outer_Inner`, in `Outer`'s file. 135 class names have bodies in more than one header, mostly nested helpers such as `Iterator` (17 headers) and `Item` (13) (`dup_classes.py`). This rule keeps each C++ qualified name one Rust name.
5. Overloads in one scope are numbered. The first in header order keeps the name, and later ones take `_2`, `_3` and on. For example, `ObSQLUtils::get_default_cast_mode` has six (src/sql/ob_sql_utils.h:347-364). Constructors are `new`, numbered the same way. The declaration index assigns the numbers once, so callers and callees in different units agree.

### 5.5 Generated files, crate roots, imports, home modules, comments and tests

1. **Generated files.**
   - Every lib.rs, main.rs and mod.rs is written by a script from the manifests. A module is `pub mod`, and a crate root carries its lint attributes and, where it applies, `#![forbid(unsafe_code)]`.
   - A unit writes no `mod`, `#![..]` or `extern crate` line.
   - Generator output goes to `rust/<crate>/src/generated/`, with a DO-NOT-EDIT banner. It is checked in and diffed at gates (ARCHITECTURE §1.4). The catalog goes to ob-errno; the system variables and inner tables go to ob-schema.
2. **Imports** are full paths from `migration/decl-index/<unit_id>.txt`. Glob imports are not allowed. Two imported items with the same name are written by full path at the second use; `use .. as` is not used.
3. **Home modules** are in ob-base (ARCHITECTURE §14 rule 7): logging, `log_user_error!`, `smart_call!`, `sync`, the hash maps and hash functions, the sorts, tracepoints and `DEBUG_SYNC`, the scope guard, `Id<T>` and `Arena<T>`.
   - Exported macros are invoked by path, as in `ob_base::smart_call!(..)`.
   - Units never write local copies.
4. **Comments.** The only comments in translated code are the four markers and the status trailer (ARCHITECTURE §14 rule 8). Each marker sits on its own line and carries the C++ file:line. The frozen core API carries rustdoc.
5. **Tests.** Step 3 units write no tests. Core crates keep differential tests in `rust/<crate>/tests/`, with vectors recorded from the reference.

## 6. What a reviewer checks

- **Crate edges.** Each crate's dependencies match its row, and observer's sql-nio dependency is optional. The rerun commands of section 7 leave no upward or unlisted edge without a ledger row that has a fix, the nio.h edges included.
- **Placement.** Each unit's crate is the map's match, or a ledger fix-1 row.
- **Paths.** Each target follows 5.1-5.2, and the completeness check of 5.3 passes. No target collides with a generated file.
- **Contexts.**
  - Each service sits where 3.2 puts it.
  - Each cell appears in its context's `check_bound()`.
  - No service holds an `Arc` of its own context or a higher one.
  - No constructor starts a thread.
- **Process-state access.**
  - A `ctx` parameter has the type the declaration index gives.
  - No `server_service`-style lookup survives as a static or a registry.
  - A deleted null check has an inventory row saying the site cannot run before the module graph.
- **Process-wide state.** Every `static` and `thread_local!` is in migration/process-statics.tsv.
- **Process actions.** No process action appears outside seekdb and ob-platform (4.1).
- **Timers.** Each `ObTimer` has one `Timer`, and each task holds `Weak<Owner>` and keeps its C++ interval.
- **Names.** Names follow 5.4, overload numbers and nested names come from the declaration index, and no comment appears beyond 5.5.

## 7. How the numbers here were made

The scripts are in /Users/colin/.claude/jobs/39d4f781/tmp/design/s1/. R01's `incgraph.py`, `crate_analysis.py` and `linkgraph.py` are copied there unchanged, and read R01's out/graph.json and nm/all.txt through links.
- Sizes and upward include lines: `python3 -B crate_analysis.py crates-arch.tsv order-arch.txt`.
- Upward symbol references: `python3 -B linkgraph.py crates-arch.tsv order-arch.txt`.
- Edges checked against the rows: `python3 -B check_allowed.py crates-arch.tsv order-arch.txt allowed.tsv out/link_edges.json` (allowed.tsv is 1.2's last column). The same scripts run in fix1/ with crates-fix1.tsv.
- Which context holds each looked-up service, and which lookups can reach it: `python3 -B slot_ctx_final.py crates-arch.tsv order-arch.txt allowed.tsv`, which extends R07's `slotmatrix.py`.
- GCTX fields per crate: `python3 -B gctx_fields.py crates-arch.tsv 'GCTX\s*\.\s*[a-z_]+'`.
- Target paths: `python3 -B paths.py crates-arch.tsv migration/depmap/units.tsv` and `paths2.py`.
- Core scope: `python3 -B core_scope.py`. Files of 4,000 lines or more: `python3 -B big_files.py`. Class names defined in more than one header: `python3 -B dup_classes.py`.
- The locale search of 4.2: `git grep -n -P '\b(strftime|strptime|nl_langinfo|localeconv|setlocale)\s*\(' -- src`. It finds src/oblib/lib/time/Time.cpp:168, src/oblib/lib/utility/utility.cpp:761, src/share/config/ob_config.cpp:747, src/storage/slog/ob_storage_log_replayer.cpp:264 and main.cpp:693-698.

## 8. Objections to ARCHITECTURE.md

1. **parse_malloc sits in the wrong crate.** §1.1 puts parse_malloc in sql-parser (29), a crate that forbids `unsafe`. But §9.2 has Rust implement the parser callbacks under their C names, which needs `#[no_mangle]` exports, and those only named crates may have (§8; R12 §7.1).
   - The rerun shows sql-parser-sys → sql-parser references to `_parse_malloc`, `_parse_strdup`, `_parse_free` and `_parse_realloc` (src/sql/parser/parse_malloc.cpp).
   - Proposal: the C-named callbacks live in sql-parser-sys (22).
2. **sql-nio's row cannot reach its pipe code.** sql-nio's row names no crate, yet §1.1 moves its Windows pipe code (transport.rs:176-191) to ob-platform. 1.5 injects a function instead. Proposal: add ob-platform (3) to sql-nio's row.
3. **The rows come only from direct include lines.** Link evidence adds 442 references to lower crates that are not on the row, in 42 pairs (461 in 46 after the fix-1 moves). Two examples:
   - sql-codegen → ob-schema, 30 references, including the runtime schema guard at src/sql/code_generator/ob_static_engine_cg.cpp:6379;
   - sql-expr → ob-simd, 5 include lines of the distance kernels.

   Proposal: derive the rows from include and link edges together, and add 4 to sql-expr's row.
4. **The nio.h edges are invisible.** The scripts treat nio.h as dropped. After the C ABI goes:
   - src/oblib/rpc (ob-runtime) and query/api (sql-exec) have edges into sql-nio;
   - the output-pack expression calls row_encode.rs's encoder, which PLAN §3 drops as a C-ABI file.

   1.5 moves the glue and adds a trait. Proposal: say so in §1.1.
5. **"Become context fields" hides how lookups reach them.** §7.1 says the slots "become context fields". Where each service lives, that holds. But 443 of 1,195 lookups cannot reach a context holding the service (3.4 rule 4). The main case is storage-tx's lookups of its own services, since `StorageContext` sits in storage-tablet above it. Proposal: state rule 4 in §7.1, or give the transaction layer a context.
6. **"Before any thread starts" moves threads after replay.** The C++ starts the timer service, logger, IO and runtime-controller timers before replay (ob_server.cpp:681-688, :1125, :1150; replay at :1155). Keeping §7.1's rule literally makes replay run with no engine threads, which is unchecked (3.3 rule 1).
7. **Some §7 names break §14 rule 3.** `Timer`, `TimerTask`, `TimerService` and `ServerOptions` replace one C++ type each (`ObTimer`, `ObTimerTask`, `ObTimerService`, `ObServerOptions`), and §14 rule 3 says such a type keeps its C++ name. This section uses §7's names.
8. **An assumption is stated as a fact.** §1.3 says "a memtable-only run never reads an sstable" as a fact. R01 §3 marks it an assumption, and §16 notes the 02:00 major freeze and statistics jobs are live under the reduced init.
9. **§1.2's counts belong to R01's map, not this one.** The counts (1,053 include lines, 2,929 symbols) were made on R01's map. On this map they are 1,037 and 2,935, excluding standby and dropped code (1.4).
10. **ob-platform's named uses miss two operations.** §8's list for ob-platform omits the malloc-zone promotion (ob_malloc.cpp:178-190) and the post-`fork` jemalloc background-thread switch (:170-175). §7.3 gives the zone promotion and `fork` daemon mode to the binary, and both operations need `unsafe`.
11. **§14 rule 1 is ambiguous.** "The crate's prefix" has several readings for crates with many prefixes. 5.1 reads it as the matched prefix row. §14 also has no rule for nested types or overloads, which 5.4 adds.
