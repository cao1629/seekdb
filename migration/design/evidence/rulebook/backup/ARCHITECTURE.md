# seekdb in Rust: architecture decisions

This file holds the architecture decisions of the Step 1 design document (migration/PLAN.md section 6, "Step 1"). The port is a redesign, so migration/RULEBOOK.md becomes the design document; its sections 1-4 and 6 take their rules from this file, and its deviation log stays as it is.

- Authority, highest first: migration/decisions.md ("Decision N", "row Na"), migration/PLAN.md ("PLAN §N"), the feasibility report ("report §N"), and the research reports in migration/design/research/ ("R01" to "R12"). Each report gives the command behind each of its counts; a count here without a command comes from the report cited with it.
- Code facts are at the frozen base 834bbee1e; paths are from the repository root.
- **Default, for the developer to confirm** marks a choice only the developer can make; section 17 lists them. Everything else is decided here and signed off with this document.
- Section 18 lists facts in PLAN.md and the report that the research corrected.

## 1. The crate graph and the core's scope

### 1.1 The crates

One cargo workspace in rust/, one crate per rust/<crate>/. A crate depends only on the crates its row names, all earlier in the table, so the graph is acyclic by construction; crates 7-39 except sql-nio may also use crates 1 and 2. † marks a named `unsafe` crate (section 8). Lines are hand-written C++ lines under R01's prefix map (/Users/colin/.claude/jobs/39d4f781/tmp/design/01-crates/crates.tsv, sizes in out/crates-v4.txt beside it), after the changes below; the last column is R01's downward include edges plus the named crates each crate uses.

| # | Crate | Main C++ sources | Lines | Tier | May also use |
|---|---|---|---|---|---|
| 1 | ob-errno | generated from share/ob_errno.def and mysql_errno.h | generated | core | - |
| 2 | ob-base | oblib/lib: alloc, container, hash, string, lock, atomic, list, queue, utility, oblog | 105,212 | core | 1 |
| 3 | ob-platform † | new: jemalloc `GlobalAlloc` impl, `_exit`, thread QoS, `fork`, `lockf`, aligned IO buffers | new | core | 1 |
| 4 | ob-simd † | the `*_simd.cpp` files; data_plane vector distance kernels | about 3,300 | core | 1 |
| 5 | ob-epoch † | new: a safe API over crossbeam-epoch | new | core | 1 |
| 6 | ob-clib-sys † | bindings to zlib 1.2.13, ICU 69, zstd 1.3.8 (vendored), libxml2 | bindings | leaf | 1 |
| 7 | ob-runtime | oblib/lib: thread, task, file, net, compress; oblib/rpc; share: io, cache, config, parameter; DAG schedulers | 56,760 | core | 3, 5, 6 |
| 8 | ob-values | oblib/common: object, datum, number, wide_integer, rowkey; oblib/lib/charset; share: datum, object, lob | 88,837 | core, value libraries | 4, 7 |
| 9 | ob-values-doc | json_type, xml, udt; share: json, semistruct, roaringbitmap, vector | 50,858 | leaf | 6-8 |
| 10 | ob-share | the rest of share; `ObISQLClient` | 65,446 | leaf | 7-9 |
| 11 | ob-schema | share: schema, inner_table, system_variable; observer/schema | 88,602 | core | 7, 8, 10 |
| 12 | geo-sys † | share/geo with boost.geometry and S2, kept C++ | 47,033 | island | 7-10 |
| 13 | vsag-sys † | ob_vsag_adaptor and the vsag bridge classes, kept C++ | 1,583 | island | 7 |
| 14 | ob-log | logservice, share/log | 34,379 | core build | 7, 8, 10 |
| 15 | storage-api | data_plane/api headers; scan interface, filter tree, `ScanHost`, aggregate traits | 7,863 | core | 7-12 |
| 16 | storage-sstable | storage: blocksstable, blockstore, tmp_file | 110,047 | core build | 4, 6-11, 15 |
| 17 | storage-tx | storage: tx, tx_table, memtable, tablelock, deadlock, throttle | 98,122 | core | 5, 7-11, 14-16 |
| 18 | storage-tablet | storage: tablet, multi_data_source, meta_mem, ls, tx_storage, slog, checkpoint, access, lob | 174,714 | core | 7-11, 14-17 |
| 19 | storage-engine | storage: compaction, ddl | 67,309 | leaf | 7-11, 14-18 |
| 20 | storage-search | storage: fts, retrieval, vector_index, vector_type; `ObVectorIndexUtil` | 30,771 | leaf | 4, 7-13, 15-19 |
| 21 | sql-parse-tree | query/api/query/parser: `ObItemType`, `ParseNode` as `ParseTree` | 2,962 | core | - |
| 22 | sql-parser-sys † | the three grammars' generated C, sql_parser_base.c, parse_node.c, pl_parser_base.c, keyword files | 5,337 C, about 175K generated | island | 7, 8, 21 |
| 23 | sql-session | sql/session, generated system variables | 17,122 | SQL tier | 7, 8, 10, 11, 15 |
| 24 | sql-ir | resolver/expr, DML statement classes, `ObOptimizerUtil`, `ObSQLUtils`, printer, hints | 85,779 | core | 7-12, 15, 21, 23 |
| 25 | sql-exec | query/api/query engine; engine framework; frame info; pushdown filter | 39,768 | core | 7-11, 15, 18, 21, 23, 24 |
| 26 | sql-expr | sql/engine/expr, ob_expr_regexp_context included; type deduction | 172,741 | SQL tier | 6-12, 15, 18, 20, 21, 23-25 |
| 27 | sql-das | sql/das, `ObTableLocation`, query/vector | 57,757 | SQL tier | 7-12, 15, 20, 21, 23-26 |
| 28 | sql-engine | operators, px, dtl | 150,256 | SQL tier | 7-12, 15, 18, 19, 21, 23-27 |
| 29 | sql-parser | `ObParser`, `ObFastParser`, `ObPLParser`, parse_malloc | 6,194 | SQL tier | 7, 8, 10, 20-23 |
| 30 | sql-resolver | the rest of sql/resolver | 97,612 | SQL tier | 6-8, 10, 11, 15, 20, 21, 23-29 |
| 31 | sql-rewrite | sql/rewrite | 97,271 | SQL tier | 8, 10-12, 21, 24-27, 29, 30 |
| 32 | sql-optimizer | sql/optimizer | 142,620 | SQL tier | 7, 8, 10, 11, 15, 21, 23-31 |
| 33 | sql-codegen | code_generator, operator factory and registration | 21,551 | core | 8, 15, 21, 24-29, 32 |
| 34 | sql | ob_sql, plan_cache, executor, monitor, engine/cmd, spi | 76,121 | leaf | 6-11, 15, 18, 20-33 |
| 35 | pl | src/pl, sql/pl | 43,403 | leaf | 7-11, 15, 21, 23-34 |
| 36 | rootserver | src/rootserver | 104,049 | leaf | 7-11, 14, 16-20, 23-25, 27, 28, 30, 32, 34, 35 |
| 37 | sql-nio | rust/sql-nio without its C ABI | 6,747 Rust | existing | none, not even 1-2 |
| 38 | observer | src/observer; `Server::open` | 117,010 | leaf | 7-37 |
| 39 | seekdb | main.cpp, ob_signal_handle, ob_command_line_parser | 1,817 | binary | 3, 7, 38 |
| - | standby | src/standby | 14,696 | deferred (7.4) | - |

Changes to R01's map: ob-errno, sql-parse-tree and crates 3-6 are new, and sql-parse-tree moves the shared parse types below sql-ir, removing most of the 37 upward include lines into sql-parser (out/crates-v4.txt). icu-regex-sys is gone: ob_expr_regexp_context includes the expression framework (src/sql/engine/expr/ob_expr_regexp_context.h:32), and ICU is reached through its 15 C functions (R08 §2.1). `Server::open` moves to observer so a library build can call it (R07 §4.2). sql-nio becomes an `rlib`: its 33 `nio_*` functions become Rust functions and its 4 reverse callbacks (rust/sql-nio/src/ffi_types.rs:53-72) one trait that observer implements (R01 §5.4); the encoders of response.rs stay its API (default, for the developer to confirm); its two unix `libc` calls (reactor.rs:777, tls.rs:225) become safe socket calls, and its Windows named-pipe code (transport.rs:176-191) moves to ob-platform.

No crate passes the 180K cap; storage-tablet and sql-expr have splits ready (R01 §5.1). At the measured 19,000-44,000 lines per second on one thread (migration/measurements/cargo-check-rate.md), a 180K-line crate checks in about 4-10 s, well under Decision 16's 60 s (assumption: translated code checks at a similar rate).

### 1.2 How a lower crate reaches a higher one

Against this map the C++ has 1,053 upward include lines and 2,929 upward symbol references (R01 §5.3). Each gets one of five fixes:
1. Move a misplaced type down: share/ob_define.h into ob-base, the chunk stores into sql-exec, `ObVectorIndexUtil` into storage-search, the schema-using DDL argument structs into ob-schema, `ObMonitorNode` and the IR's parts of `ObTransformUtils` into sql-ir.
2. A trait in the lower crate, implemented higher and handed down through the server context, as the C++ already does with `ObIRootCommandService` (src/query/api/query/command/ob_root_command_service.h:36) and `ObITabletScan` (src/data_plane/api/data_plane/access/ob_tablet_scan.h:493).
3. The storage/SQL boundary of section 5, for the 125 symbols storage calls in SQL.
4. A trait in sql-exec for SQL's calls into PL (224 symbols), implemented in pl.
5. Inside the core the new API decides: sstable below transactions below tablet.

The Step 1 exit's "acyclic crate graph" holds when R01's scripts, rerun on this map, leave no upward edge without a fix; Step 2a's survey `cargo check` confirms it on Rust code. Rejected: a crate per Bazel target (sql and storage are one runtime library each, R01 §1.4); a crate per module root (sql and storage are 5-9 times the cap, and the storage↔sql, sql↔pl and sql↔observer link cycles stay).

### 1.3 The core's scope

The hand-designed core is the report's narrow rows (about 352K lines) plus the schema object model (88,602) and the storage entry points the SQL tier calls (storage/access, tx_storage and ls, about 55K): about 500K lines, built first and exercised by Step 2a. The sstable formats (87.6K), the single-writer WAL and crash recovery join in the core build. Why: every statement resolves names through the schema guard and the reduced init creates a user, a database and grants; the path runs through `ObAccessService::table_scan` (src/storage/tx_storage/ob_access_service.cpp:335) and the single `ObLS` (src/storage/tx_storage/ob_ls_service.h:70, :138); a memtable-only run never reads an sstable (R01 §3). seekdb has one log stream (src/share/ob_ls_id.h:34, :47), so that layer folds into the storage core's top object. Rejected: the narrow 352K (Step 2a would need stand-ins for what the core must design) and the wide 600-650K up front (off the narrow path).

### 1.4 Dropped or regenerated

As PLAN §3 lists, except: zstd 1.3.8 and zlib 1.2.13 stay as C (their bytes are judged, section 13); gRPC and protobuf leave with standby; there are seven generators (tools/ob_error/src/gen_os_errno.pl feeds the ob_error tool of family 6), and gen_str_datum_func_parts.py and gen_expr_str_cmp_func.py produce nothing in Rust (R11 §9.1). Each kept generator reads its input unchanged and gains a Rust back end; the output is checked in and a gate regenerates and diffs it (default, for the developer to confirm). The 640 inner-table builder functions become static data and one builder.

## 2. Errors

**Decision.**
- **One error type,** in ob-errno: `pub struct ObError(i32)` (`Copy`, `Eq`, `Hash`, never 0) and `pub type ObResult<T = ()> = Result<T, ObError>`. Each catalog code is a `pub const` under its C++ name, usable as a `match` pattern; `OB_SUCCESS` is `Ok(())`. Any non-zero `i32` is accepted, because codes outside the catalog occur: init.sql sets a tracepoint with `error_code = 4` (tools/deploy/init.sql:26), PL `SIGNAL` carries user numbers (src/pl/ob_pl_interpreter.cpp:133). The 22 predicates of src/share/ob_define.h:71-255 become methods. The error carries no message.
- **Codes used as values stay values:** `Err(OB_ITER_END)` wherever the C++ returns and tests `OB_ITER_END`. A core API replacing a C++ API keeps that API's codes (hash-map lookups, row and batch iterators, `databuff_printf`'s `OB_SIZE_OVERFLOW`); `Option`-returning APIs come after parity (default, for the developer to confirm).
- **One form per construct** (R02 §5.2's table): `?` only where the C++ function is a plain `OB_FAIL` chain with nothing running after an error, otherwise a local `ret` kept in the C++ statement order; `OB_SUCC(ret) &&` guards become `ret.is_ok() &&`; `OB_X == ret` tests become `match` arms; `COVER_SUCC` becomes `ret = ret.and(tmp)`; comparators holding `int &ret = ret_;` return `ObResult<bool>` and are called only by the port's sorts (section 10), which stop at the first error; `operator=` recording `error_ret_` becomes a fallible `assign`. Log macros take the error as an argument, instead of reading a local named `ret` (src/oblib/lib/oblog/ob_log_module.h:261-262), and print `ret=<code>` as today (default, for the developer to confirm).
- **Messages** stay in a Rust `ObWarningBuffer` with the C++ limits (a 512-byte message, a 64-item ring; src/oblib/lib/oblog/ob_warning_buffer.h:51, :141), one per session as `Arc<Mutex<_>>`, reached through a per-thread slot that the request entry sets as `ObMPBase::setup_wb` does (src/observer/mysql/obmp_base.h:122-128) (default, for the developer to confirm). `log_user_error!(OB_X, ...)` records the message and evaluates to `OB_X`. One function builds the error packet as src/observer/mysql/obmp_packet_sender.cpp:527-700 does and hands it to sql-nio's `encode_error_payload`; SHOW WARNINGS keeps the rule of src/sql/session/ob_sql_session_info.cpp:660-672.
- **One generator replaces gen_errno.pl,** reading ob_errno.def and mysql_errno.h unchanged. It writes the constants, the table, the five lookups with today's fallbacks, the ob_error tool's data, a C header for the islands, typed argument lists for the 468 formatted messages, and a check of the 19 codes copied into src/sql/parser/parse_define.h:26-44. It keeps each oddity: the duplicate name (src/share/ob_errno.def:1868, :1872), the malformed lines :575 and :702, the OB name used as a MySQL number (:792), client number 0 for an unknown code. Messages are printed with C semantics (byte precision, the 511-byte cut), never with `format!`.

**Why.** 5,718 lines compare `ret` with a specific code; 347 codes are compared, the top 10 covering only 64.6% of comparisons (R02 §2.4); and about 45% of core and SQL-tier lines run under no case (coverage README), so a one-for-one form that reviewers check against the source beats reshaping each site. SHOW WARNINGS keeps a message recorded under another code while the packet drops it (R02 §2.3), so the message cannot travel inside the error.

**Rejected.** A 1,545-variant enum (it still needs `Other(i32)`); errors carrying messages; an error enum per crate (storage codes are tested in SQL); `anyhow` (loses the code); passing the warning buffer explicitly (changes every function on the way to 2,799 `LOG_USER_ERROR` calls in 393 files, with no change in behavior). The core build checks every lookup against C++ for all codes and every format against `vsnprintf`.

## 3. Memory

### 3.1 Every allocation has one owner

The owner is the server context, a session, a cached plan, a statement's compilation, a plan execution, a batch, a budget owner, or a receiver (thread, task, timer, queue, channel, cache); each inventory row names it. Rules (R03 §4.1):
1. Compilation: IR objects in typed-id arenas (section 4) owned by the compile context; names, literals and parameter values in one per-statement bump arena (bumpalo) borrowed as `&'q`; no `Rc` or `Arc` in the IR.
2. A cached plan owns all its memory, is `Send + Sync + 'static` and is shared as `Arc`; code generation copies out of the statement arena.
3. Execution: rows live in column batches owned by the producing operator and are borrowed until its next call; an operator that keeps rows copies them into a store charged to the work area; expression temporaries go into a per-execution bump arena reset between batches through `&mut`.
4. A scan holds its source (an `Arc` of the tablet snapshot or cached block, or a memtable reference), and its views borrow from it.
5. Handoff is owned data (`Vec<u8>` or `Box<[u8]>`, `Arc` for several receivers); the spawn, queue, timer, channel and cache-insert APIs require `T: Send + 'static`; scoped threads where the caller waits.
6. One thread per arena: the 29 `ObSafeArenaAllocator` lines become per-task arenas.
7. Long-lived owners keep collections that free item by item; nothing with a destructor goes into a bump arena.
8. No implicit current context: `CURRENT_CONTEXT`, `WITH_CONTEXT` and `ObArray`'s binding at its first allocation (src/oblib/lib/container/ob_array.h:484-492) become an argument or a field.
9. `inc_ref`/`dec_ref` becomes `Arc`; a release into a pool or budget becomes an `Arc` with a custom drop. Pools that only reuse memory go; a pool whose size users hit stays (src/share/cache/ob_kvcache_store.cpp:1030).

`ObIAllocator&` for statement data becomes `&'q Bump`, and an owned value where the callee keeps the data; `ObString` becomes `&[u8]`, `&'q [u8]` or owned bytes; placement new and explicit destructor calls become constructors and `Drop`; engine code has no generic `A: Allocator` parameters.

**Why.** The report's bug table (report §2) is this class, and rules 4-6 make each case a compile error: bc31e03f4 (a PX buffer in a closure), eda242c9a (request-arena memory in an async task), 9d24b8807 (shallow strings in a queued task), dd899675c (an arena reset under a reader), 9e2b6ba16 (pointers outliving their iterator), 62b9d1a81 (one arena shared by parallel tasks). **Rejected:** `Rc`/`Arc` IR nodes (the IR has cycles through `ref_stmt_` and `outer_expr_`); owned values everywhere (an allocation per row against the 1.2x gate); shared arenas; allocator-generic code (a compiled copy per allocator).

### 3.2 Out-of-memory: Decision 12, exactly

- A general allocation failure aborts the process. The 4,737 assignments of `OB_ALLOCATE_MEMORY_FAILED` get no Rust counterpart outside the owners below (R03 §1.8).
- Typed errors stay only at Decision 12's owners and logical limits, each with its C++ formula, parameter, check point, code, message and behavior: clog and replay (replay turns -4013 into `OB_EAGAIN`, src/storage/tx/ob_tx_replay_executor.cpp:696-703); the IO allocator (objects retry until the IO timeout); the KV cache store (one wash under a lock, src/share/cache/ob_kvcache_store.cpp:915-961) and its handle pool; the micro block cache (-4013 at once: its 3-retry loop, src/storage/blocksstable/ob_micro_block_cache.cpp:387-400, has no caller, since the only `alloc_data_buf` calls are the tmp-file cache's own, src/storage/tmp_file/ob_tmp_file_cache.cpp:654); the vector module (-7603 stays inside src/storage/allocator/ob_vector_allocator.cpp:142; vsag's `NO_ENOUGH_MEMORY` is -4013) and the IVF cache; the query memory tracker (-11049); memstore full (-4030); the temp-file write buffer pool (-9124); the work-area spill; hash-join partition depth (-4013, src/sql/engine/join/ob_hash_join_op.cpp:838-843).
- Counting is by explicit charges: a budget is a limit and an atomic counter with `ObBlockAllocMgr::alloc_block`'s add, compare, undo (src/oblib/lib/allocator/ob_block_alloc_mgr.h:38-50); a charge returns its bytes when dropped; a request's total is its arenas' bytes plus its charges. Engine crates implement neither `GlobalAlloc` nor `Allocator`. Budgets trip by the same formulas at the same check points, not at the same byte counts (default, for the developer to confirm).
- `try_reserve`-style calls appear only at those owners and in client-sized buffers, as sql-nio does today; SQL functions keep their own size checks first.
- The global allocator is the reference's jemalloc 5.3.1 (seekdb-jemalloc-sys =0.2.2, deps/external/Cargo.toml:10) with its `je_malloc_conf` (src/oblib/lib/allocator/ob_malloc.cpp:38-42), so the 1.2x comparison measures no allocator change.
- Memory virtual tables are filled from the Rust counters (default, for the developer to confirm); no test reads their rows (R03 §1.9).
- Family 12: the -11049 message prints `mem_hold` (src/share/ob_errno.def:1880), which no Rust build matches byte for byte, so that scenario replaces the number in its own test text, as frozen tests already use `--replace_numeric_round`, declared in 00b before its second sign-off; -7603 stays internal, and the family records what the C++ returns (default, for the developer to confirm).
- On later platforms a general out-of-memory ends the host, so these owners are where sizing keeps it rare; wasm limits come from its 2 GiB heap when wasm returns.

**Rejected** (R03 §3.4-3.5): a counting global allocator (an `unsafe impl` and a header per allocation); allocators that refuse over a limit (`Allocator` is an `unsafe trait`); per-context jemalloc arenas (jemalloc only, so no wasm).

## 4. IR ids and the ParseNode mirror

### 4.1 One id scheme

Every object the C++ identifies by address gets a typed id `Id<T>` (ob-base) of 64 bits: an arena number in the high 32, from one process-wide counter, so ids are unique among live arenas as addresses are, and an index in the low 32 into a `Vec`-backed `Arena<T>` owned by its context. Debug builds keep a generation per arena so a stale id aborts. It covers parse nodes (`NodeId`), expressions (`ExprId`), statements, table items, optimizer objects (operators, plans, paths, join orders), query-range nodes and the tables of a cached plan. Rules (R04 §4.3):
1. One id per C++ allocation, made where the C++ makes the object (`create_raw_expr`, `ObRawExprCopier`'s copies included; the statement, table-item, operator and plan factories); a proxy factory uses its target's arena. An in-place edit keeps the id.
2. Equality is id equality; `find_item` (src/sql/optimizer/ob_optimizer_util.h:264-286) and the set helpers keep their bodies.
3. Address-keyed maps become id-keyed maps with a fixed hasher, iterated in insertion order (the C++ order changes from run to run); debug builds can reverse it to expose a hidden dependence.
4. `ObRawExpr*&` on a local or field becomes `&mut ExprId`; a slot inside an expression is read, computed and written back; `USELESS_POINTER` becomes `Option`.
5. The `JoinPath` recycle list becomes a stack of ids popped from the back (src/sql/optimizer/ob_join_order.cpp:5435-5448, :11037-11051); `evaluate_cost`'s temporary factory is its own arena, dropped with it (src/sql/rewrite/ob_transform_rule.cpp:379-398).
6. The C++'s own ids (`stmt_id_`, `table_id_`, column ids, operator numbering) stay separate fields; arena ids are never printed or sorted on.
7. Back-pointers become context (`rt_expr_` a code-generator table indexed by `ExprId`); cycles (`ref_stmt_`, `outer_expr_`) are id fields; marks stay on the object; `RawExpr` is the common fields plus an enum over the 29 subclasses.

**Why.** Identity reaches output: the output pass counts references per address (src/sql/optimizer/ob_logical_operator.cpp:246-322, :1836-1866), which decides the 4,005 pinned `output(...)` lines, and the code generator makes one `ObExpr` per distinct raw expression (src/sql/code_generator/ob_static_engine_expr_cg.cpp:190). The C++ mixes factories, and PL packs addresses into `int64_t` (src/pl/ob_pl_build.cpp:1062), which 64-bit ids fit (64 bits for every IR type: default, for the developer to confirm). **Rejected:** arena references with `RefCell` (a lifetime in every signature, borrow failures at run time); `Rc<RefCell<_>>` (cycles leak); copy-on-write expressions (they change which nodes are the same object); 32-bit ids (they collide across the factories the C++ mixes).

### 4.2 The ParseNode mirror

The C tree is converted once, when `parse_sql` returns, into `ParseTree<'q>` (sql-parse-tree): one node per C node in an arena (a map from C address keeps a node with two parents one node), built with an explicit stack. Fields keep `ParseNode`'s C names (src/query/api/query/parser/parse_node.h:129-195): `flag_` a `u32` with an accessor per bit field, `value_` an `i64` with lane helpers, `str_value_` a `&'q [u8]` borrowed from the statement arena where `parse_malloc` put it, `children_` as `Option<NodeId>` (an index at or past `num_child_` aborts). Parameterization and resolvers edit through `&mut ParseTree`; the Rust `ObFastParser` builds in the same type; `parsenode_hash` and `parsenode_equal` keep the C field order (src/sql/parser/parse_node.c:650-720).

**Why.** Resolvers read children by position (2,672 `children_[` lines in 77 files) and parameterization edits the tree in place (src/sql/plan_cache/ob_sql_parameterization.cpp:583-585), so the 93 registered statement resolvers keep their shape, and `unsafe` stays in one converter. **Rejected:** a typed syntax tree (resolvers lose their shape); handles over the C tree (`unsafe` on each access), kept as the fallback if Step 2a's parse time misses 1.2x (default, for the developer to confirm); a Rust parser now (Decision 13).

## 5. The column batch and the storage filter trait

**Decision** (R05 §4.1-4.2):
- ob-values: `ObDatum` is a fixed-size value, `loc: u64` (the value up to 8 bytes, otherwise a buffer id and an offset) and `desc: u32` (length, flag and null bit as in src/oblib/common/datum/ob_datum.h:134-165); `DatumVec`, `ObBitVector`, and a `BufTable` of the buffers a batch refers to (`Arc`'d blocks, arena chunks, plan constants). The datum API is the only access.
- sql-exec: each run has an `EvalFrame` with one slot per expression. The plan, with its evaluation functions as plain `fn` values, is immutable and shared by PX workers, which build their own frames. Operators implement `next_batch(&mut self, frame, max_rows) -> ObResult<ObBatchRows>`; datums stay valid until the producer's next batch.
- storage-api: storage owns the filter tree (AND, OR, white, black, dynamic, sample), white filters on encoded data, the skip index, `ObWhereOptimizer`'s reordering and where batches end. Black filters, row filters and generated columns call SQL through a `ScanHost` trait passed into every scan call. Aggregate pushdown keeps the C++ protocol (src/share/aggregate/ob_pushdown_aggregate_protocol.h) as traits. Storage fills caller-owned `DatumVec`s and registers block handles in a caller-owned `BufTable`: no copy at the scan, as today (src/storage/access/ob_vector_store.cpp:306-352), and no SQL type in storage.
- The `ObIVector` formats stay out of the query batch; since cbcea2b91 only the DDL write path uses them.

**Why.** Frames are untyped memory at generated offsets (src/query/api/query/engine/expr/ob_expr.h:364-386), which Decision 14 keeps out of the SQL and storage crates. Storage and SQL call each other in a loop today (src/sql/engine/basic/ob_pushdown_filter.cpp:1370-1449 into src/storage/blocksstable/ob_micro_block_row_scanner.cpp:2122-2131); one trait call per batch splits the crates at the C++'s own cost. **Rejected:** offset frames (`unsafe` everywhere); copying at the scan (the C++ copies nothing); `ObIVector` or Arrow formats (rewrites 528 expression bodies); storage depending on `ObExpr`; per-row callbacks (they change which failing row is reported). The fallback, if Step 2a measures the handle datum outside 1.2x, is a raw-pointer datum crate added to the named list (default, for the developer to confirm).

## 6. Storage behavior the judge sees

### 6.1 The estimator's inputs (PLAN §8 item 17)

**Decision:** keep the row-count rules and the count definitions; free the block layout (R06 §4.3). Kept identical:
- the memtable walk, exact up to `MAX_SAMPLE_ROW_COUNT` = 500 rows from each end of a range (src/storage/memtable/mvcc/ob_query_engine.h:134), with its five DML-flag cases;
- the memtable B-tree's shape: 15 keys per node, the same split points and element-count walk (src/storage/memtable/mvcc/ob_keybtree.cpp:160-178, :953-994);
- the sstable border counting with its 1,000x rule (src/storage/access/ob_index_sstable_estimator.cpp:96-113), the combination and clamps (src/storage/access/ob_table_estimator.cpp:28-95), and the per-tablet counts as defined (src/storage/ls/ob_ls_tablet_service.cpp:4786-4860);
- the freeze policy with every parameter and default, the fast-freeze conditions and the 02:00 daily freeze; no flush earlier than the C++;
- the compaction state tests poll (`DBA_OB_MAJOR_COMPACTION`, `__all_virtual_tablet_memstore_info`, `__all_virtual_tablet_compaction_history`);
- the table options as schema attributes printing the same text, such as `COMPRESSION = 'zstd_1.3.8'` (171 lines) and `BLOCK_SIZE = 16384` (R06 §3.4); blocks are compressed by the kept zstd 1.3.8 (default, for the developer to confirm).

Free: formats, block layout, encodings, and the rows-per-micro-block cut. EST numbers then match except EST.TIME for sstable tables with statistics or sampling and EST.ROWS for a range of 1,000 times its border block's rows; those differences are documented at the final gate (default, for the developer to confirm).

**Why.** All 768 EST.ROWS values in the 40 plan-bearing files are 100 or less, and block counts reach EST.TIME only through statistics or sampling of sstable data (src/sql/optimizer/ob_opt_est_cost_model.cpp:118-129). Plan choice, the sort algorithm under the reduced init (no key encoding below 1,000 estimated rows, src/sql/optimizer/ob_optimizer_util.cpp:6976-6985) and `rowset=` follow the estimates even with EST masked, and view_2.result:473-498 prints unmasked estimator output. **Rejected:** identical block counts (blocksstable's 87,627 lines would become a translation bound to zstd output sizes); keeping nothing (plans flip). **Measured first,** as 00b injected mutations: double the block counts (src/storage/ls/ob_ls_tablet_service.cpp:4786-4829), stop the memtable walk at 50 rows, cut micro blocks at another row count. An unmasked line that moves names a place needing the exact rule.

### 6.2 Scan order (PLAN §8 item 18)

**Decision:** rows of a tablet in rowkey order through the loser tree's (range index, rowkey) order (src/storage/access/ob_scan_merge_loser_tree.cpp:72-93), with the value library's compare functions; a table without a primary key in hidden `__pk_increment` order, per tablet and in insert order, cached 10,000 at a time (src/share/ob_tablet_autoincrement_param.h:32), kept across UPDATE, renewed only on a partition move (src/sql/engine/dml/ob_dml_service.cpp:970-973); ranges and keys in the order the SQL tier gives, sorted with the transcribed `ob_sort`; tablets in partition order; one granule per tablet at dop 1 (src/sql/engine/px/ob_granule_util.cpp:56-70); batches ending at the same storage events. Dop above 1 is timing-dependent in the C++ too and follows PLAN §4's quarantine rule. **Why:** 9,042 single-table SELECTs have no ORDER BY, 7,202 print two or more rows, and 6,096 of those read tables without a primary key (R06 §1.3); none is masked. **Rejected:** ordering such tables any other way (physical position, one sequence for all tablets), which breaks those 6,096 (R06 §3.3).

## 7. The server context and the binary crate

### 7.1 Context structs

**Decision** (R07 §4): no engine singletons. One context per layer, each holding an `Arc` of the one below: `RuntimeContext` (ob-runtime: absolute directories, config, budgets, timers, the thread spawner, IO, the KV cache), `StorageContext` (storage-tablet), `SqlContext` (sql-exec), `ServerContext` (observer). `Server::open(ServerOptions)` in observer builds them bottom-up in the C++ module order (src/observer/omt/ob_server_runtime_controller.cpp:1182-1547), after storage replay. Constructors take dependencies, replacing `init()` and `is_inited_`. Upward traits and the two real cycles (the log service and the LS service; SQL and PL) are `OnceLock<Weak<dyn Trait>>` fields, filled in one binding step that checks they are all set before any thread starts. Functions that reach process state take `ctx` after `self`. `server_service<T>()` (1,224 uses), `GCTX` (891), `GCONF` (682) and `get_instance()` (623) become context fields; their 149 null checks and 112 `SERVER_MODULE_SCOPE` guards go, unless the site can run before the module graph exists; `getcwd()` paths become `ctx.base_dir().join(..)`; each `ObTimer` stays one Rust `Timer` of the same name, each `ObTimerTask` an `impl TimerTask` holding `Weak<Owner>`.

Allowed process-wide state, and nothing else: tables filled once (expression registry, error catalog, charsets, system-variable metadata); the logging facade; the `DEBUG_SYNC` and tracepoint registries under their C++ names; hooks third-party libraries hold per process (vsag's logger, CRoaring's memory hook); counters that only hand out unique numbers (arena numbers); the thread-locals of section 11.

**Why.** Today's slots are raw pointers bound at run time (src/share/rc/ob_server_runtime.h:126-148), every lookup already goes to the caller's module or a lower one (R07 §1.2), and Decision 8's guideline wants the engine usable as a library. **Rejected:** `static` services (one engine per process, no reopening); a registry keyed by type (failures at run time); trait bounds on every function.

### 7.2 Threads and timers

One thread spawner starts every engine thread with its C++ name, the configured stack (section 11) and, on macOS, the USER_INITIATED QoS of src/oblib/lib/thread/thread.cpp:341-345; seekdb builds it and passes it in, so a library host can pass its own. The C++ timer behavior is a contract (src/oblib/lib/task/ob_timer_service.cpp:477, :534-548): one task at a time per timer, the next run at completion plus the delay, cancel lets a running task finish, a stopped timer returns `OB_CANCELED`, intervals copied unchanged. Also: a resizable worker set, a bounded task pool, the request-worker pool with `ObAdaptiveWorkerPool`'s sizes, two time wheels. No async runtime.

### 7.3 What seekdb owns (Decision 8's guideline, adopted)

`main`; arguments; the signal mask, set before any thread, and the signal thread (SIGTERM becomes `raise(SIGKILL)`, src/observer/ob_signal_handle.cpp:129-134; SIGUSR1 keeps its stop request (default, for the developer to confirm); numbers 40-64 do not exist on macOS); `fork` daemon mode with the `lockf`'d run/seekdb.pid, and `--initialize` (default, for the developer to confirm); an optional `chdir` (the engine uses `canonicalize(base_dir)`, equal to `getcwd()` after it, so the `pid_file` and `socket` values print the same); `#[global_allocator]` and the malloc-zone promotion (src/observer/main.cpp:645-653); the log sink; the final `_Exit(0)` of `wait()` (src/observer/ob_server.cpp:1678). Not ported: `_Exit` on the last client (embedded only) and the malloc hook (Linux-only, src/oblib/lib/CMakeLists.txt:34-41).

### 7.4 Physical standby

Deferred until after parity (default, for the developer to confirm): the Rust build starts as `OB_ENABLE_STANDBY=OFF` does, whose module still bootstraps (src/standby/standby_module_disabled.cpp:76-91). Standby is 14,696 lines, built by default (CMakeLists.txt:29) and untested, and it would bring tokio, tonic and prost. The bootstrap telemetry report is dropped too (default, for the developer to confirm).

## 8. Where `unsafe` may appear

**Decision** (Decision 14 (b); R12 §7.1): every crate not named here carries `#![forbid(unsafe_code)]` in its generated lib.rs and inherits `unsafe_code = "forbid"` from the workspace lints; a gate fails if the crates without it differ from this list. Named: the island shims geo-sys, vsag-sys, sql-parser-sys and ob-clib-sys's ICU part; the SIMD kernels, ob-simd; the IO buffers, in ob-platform; the reclamation wrapper, ob-epoch. Two more uses fall outside Decision 14's kinds and go in the same crates (default, for the developer to confirm): ob-platform's jemalloc `GlobalAlloc` impl, `_exit`, QoS, `fork` and `lockf`, and ob-clib-sys's zlib, zstd and libxml2 bindings. Named crates deny `unsafe_op_in_unsafe_fn` and clippy's `undocumented_unsafe_blocks`, keep C symbols in one `ffi` module, and put `// SAFETY:` above each operation. A script reports every crate's `unsafe` count at every gate. **Rejected:** `unsafe` inside large engine crates (R01's suggestion for storage-sstable, storage-tx and storage-search goes to ob-simd and ob-epoch); tikv-jemallocator (it builds its own jemalloc, not the reference's; assumption from its build).

## 9. The islands

### 9.1 What stays

Two C++ islands, geo-sys and vsag-sys; the parser's C core with its three grammars, SQL, PL and fulltext (default, for the developer to confirm); ICU through its C API (R08 §4.1). relaxed-rapidjson is ported, its `StrtodNormalPrecision` and error table checked against the C++ reader (default, for the developer to confirm). The IK tokenizer is translated. Rejected: an ICU shim over ob_expr_regexp_context (it would pull `ObExpr` headers into the island); today's C++ geo API with the GIS expressions inside the island (14,674 lines of SQL-tier code).

### 9.2 The parser island

The generated C the reference was built from is checked in with md5 sums, so no bison 2.4.1 or flex is needed (default, for the developer to confirm). Bindings for `ParseNode`, `ParseResult` and `ObItemType` are generated once by bindgen from the existing headers and checked in. Rust implements the callbacks under their C names: the `parse_malloc` family (the statement arena, with the 8-byte size header), `try_check_mem_status`, the two stack checks, `lookup_pl_symbol` (through a trait from the caller), `ob_strntoll`/`ob_strntoull`, `ob_parse_binary_simd` and the charset helpers. Each returns normally and never re-enters the parser, so no Rust frame sits between `setjmp` (src/sql/parser/sql_parser_base.c:104-106) and the only `longjmp`, which follows a failed allocation; `nm` confirms the Rust `try_check_mem_status` replaced the weak one. Charset has one implementation, in Rust: the island fills the C `ObCharsetInfo` the lexer reads (src/sql/parser/sql_parser_base.h:776-806) from the Rust charset crate, other handler slots abort, and C++ charset code serves only as a Step 2a stopgap (default, for the developer to confirm).

### 9.3 ABI rules for geo and vsag (R08 §4.7)

1. One header per island (`obgeo.h`, `obvsag.h`), generated by cbindgen from the Rust declarations, checked in and diffed at gates; the C++ shim includes it, so drift fails the C++ compile. Rejected: R01's hand-written headers, two copies with no compiler between them.
2. C++-implemented entries `<tag>_<verb>`, Rust callbacks `<tag>_rs_<verb>`, structs `<Tag><Name>`; tags `obgeo` and `obvsag` (`vsag_` belongs to vsag's own C API).
3. Only fixed-width integers, `double`, `bool`, `(const uint8_t*, int64_t)` views, `#[repr(C)]` plain structs and opaque handles cross.
4. Every entry returns an OB code from the generated header; messages are formatted in Rust.
5. Every C++ entry is `noexcept` with a catch-all; the existing catch lists keep their mappings, `std::bad_alloc` included (src/share/geo/ob_geo_dispatcher.h:1314-1366) (default, for the developer to confirm).
6. Each side frees what it allocated; island objects sit behind handles destroyed once, from the Rust `Drop`.
7. Receivers come first, never globals; callbacks may run on vsag's threads, so they use only the `Send + Sync` receiver.
8. No re-entry, and no `longjmp` over a Rust frame.
9. Islands build with the reference compiler, SDK and flags (section 13) and link the same archives.

The geo API is bytes in and bytes out, about 76 entry points and 10 callbacks; MVT and GeoJSON-to-JSON-binary move to Rust (R08 §4.2).

### 9.4 The kept oblib subset and island memory (PLAN §8 item 19)

The islands compile against the unchanged headers they reach (196 files and 77,558 lines for geo; 118 and 38,448 for vsag) and link a few replacement .cpp files defining the 124 and 38 out-of-line symbols they need: logging forwards to Rust, allocation calls a Rust function, `SMART_CALL` reads the stack bounds Rust records (section 11), charset and dtoa forward to Rust, so their behavior matches by construction; small leaves are copied (R08 §4.3). Rejected: linking the real oblib files, which pulls 363 of the 634 reference objects. Island memory is charged only through allocation callbacks whose receiver is the thing charged: the caller's arena and query context for geo and the parser (-11049 keeps counting GIS memory), the vector budget for vsag and CRoaring. Library-internal malloc stays uncharged, as on today's macOS build, which has no malloc hook and whose jemalloc ignores labels (R08 §2.4); no malloc interposition.

## 10. Determinism: numbers, sorting, hashing, overflow and profiles

**Decision** (R09 §5; R05 §4.3):
- **Numbers:** ob_dtoa.cc, ObNumber (both live families, `_v2_` and `_v3`, each caller keeping its own), the decimal-int library (the default for DECIMAL, src/share/parameter/ob_parameter_seed.ipp:302) and murmur_hash are translated line by line into ob-values. No output text comes from Rust's float formatting; doubles are parsed only by the translated `ob_strtod`; callers pass the C++ widths.
- **No FMA:** no `mul_add`, since the C++ calls none on arm64. Float code keeps the C++ order, types and lane order; libm maps one to one (`rint` to `round_ties_even`); float comparisons are ported literally (NaN last, -0.0 equal to 0.0, DOUBLE(M,D)'s tolerance, src/share/datum/ob_datum_cmp_func_def.h:139-171).
- **Overflow:** `wrapping_*` where the C++ relies on wraparound (hash mixing, the add-then-test operators of src/sql/engine/expr/ob_expr_add.cpp:229-238, `fnv_hash2`); every C++ check kept with its code and text; every guard before `/` and `%` kept, since Rust panics on zero and on `MIN / -1` in all profiles; `char` read as a number is `i8`, `long` is `i64` everywhere.
- **Sorting:** ob-base holds a transcription of `std::sort` and the heap functions from the SDK 26.2 libc++ (`_LIBCPP_VERSION` 200100, without the branchless partition), with its license notice (default, for the developer to confirm), used at every `lib::ob_sort`, `std::sort` and heap site; `std::stable_sort` becomes `sort_by`; `ObAdaptiveQS`, `ObBinaryHeap` and OB's other sorts are translated, with the rule that picks among them (`check_can_encode_sortkey`, tracepoint 1200).
- **Hashing:** bit-exact murmurhash64A, murmurhash2, `fnv_hash2`, the datum and collation hashes, and their seeds (16777213 for hash join; 0 for KEY partitioning and NDV); `ObHashMap` and `ObHashSet` ported with their bucket arithmetic (src/oblib/lib/hash/ob_hashtable.h:1139; ob_hashutils.h:677-689); the hash-join table's layout; first-seen group order. Exact hash order is the rule everywhere, with the mask as a safety net (default, for the developer to confirm).
- **Batches:** `detect_batch_size` (src/sql/code_generator/ob_code_generator.cpp:85-170) is translated exactly and sets both `rowset=` and the run.
- **Banned** in engine crates through clippy: `sort_unstable*`, `select_nth_unstable*`, `BinaryHeap`, std `HashMap`/`HashSet`/`RandomState`, `mul_add`; each `#[allow]` cites an inventory row.
- **Profiles:** `release` (the product and family 14): opt-level 3, thin LTO, overflow checks and debug assertions off, `panic = "abort"`. `judge` (every parity run): the same semantics at opt-level 2, no LTO, 256 codegen units, incremental; Step 2a may move the opt-level (default, for the developer to confirm). `checked`: `judge` with overflow checks and debug assertions on, run weekly from Step 5, diagnostic only (default, for the developer to confirm). `OB_ASSERT(x)` evaluates `x` under `debug_assert!`, as the reference's `-DNDEBUG` does.
- **Every platform follows the macOS arm64 reference's numbers** (default, for the developer to confirm).

**Why.** 770,711 of 912,659 configured result lines come from four matrices that pin three formatters and 365 overflow messages (R09 §2.1). Ties, scan order, `rowset=`, KEY-partition placement, NDV values and the `%08X` query-block names (src/sql/resolver/dml/ob_sql_hint.cpp:389) are unmasked, and a std sort may panic on DOUBLE(M,D)'s non-transitive comparator. **Rejected:** Rust's sorts, hashers or float formatting with normalization (a third mask, which Decision 6 does not allow); overflow checks in the judge profile (the C++ wraps on purpose); tie-breakers added to comparators. Differential tests against the C++ sources, compiled into a test-only binary, cover dtoa, strtod, ObNumber, decimal-int, the hashes and the sort (test-only C++: default, for the developer to confirm).

## 11. Concurrency and the stack

**Decision** (R10 §4):
- **Atomics:** a field shared between threads is an `Atomic*` or sits in a lock; every operation is `SeqCst` except the 29 the C++ names weaker. Reference counts, lock words, list links and reclamation clocks are replaced, not retyped. `SCN`, `ObTxSEQ` and LSN become `Copy` types with atomic wrappers. No 128-bit atomics (palf's `LSNAllocator` keeps its 16 bytes under a `Mutex`); no `AtomicPtr` outside named crates.
- **Locks:** parking_lot through a `sync` module in ob-base; `Mutex<T>` and `RwLock<T>` own their data; timed locks keep each site's error code; no pure spin locks; latch ids go; guards never cross threads; callbacks run after the guard drops.
- **Lock-free code:** hand-written reclamation (QClock, the retire station, hazard versions) is replaced; structures start behind locks, and ob-epoch serves only where the gate needs lock-free reads, tested with loom; no nightly Miri or ThreadSanitizer job (default, for the developer to confirm).
- **Thread-locals:** state that changes results is a parameter. `thread_local!` holds only a closed list of const-initialized `Copy` values (request deadline, trace id, thread name, diagnostics, caches that change no result, the current stack bounds), plus the warning-buffer slot of section 2, set and cleared by a scope guard.
- **Stack:** every `SMART_CALL` site (1,137 lines) calls `smart_call!` in ob-base, which runs the closure when `stacker::remaining_stack()` covers the reserve, returns `OB_SIZE_OVERFLOW` when one more extension would pass the total cap (as src/oblib/lib/utility/ob_smart_call.h:82-83 does), and otherwise calls `stacker::grow`. Starting values, fixed by the Step 2a probe: 256 KiB and 1 MiB reserves, 8 MiB extensions, a 64 MiB cap, 8 MiB threads; `stack_size` keeps its name and range and becomes a lower bound (default, for the developer to confirm). The depth where `OB_SIZE_OVERFLOW` fires is not a contract; no case expects it.
- **The wasm depth limit is adopted now:** on wasm the same wrapper counts nesting instead of growing.
- **Islands share the stack bounds:** geo's `get_stackattr`/`set_stackattr` read and write the bounds Rust records through two geo-sys functions, and the parser cores' checks call the same check.

**Why.** 724 of 966 member fields touched by `ATOMIC_*` are also accessed plainly (R10 §1.1): data races in C++, compile errors in Rust. Workers get 224 KiB on macOS (src/observer/ob_server.cpp:1966-1974), and the stack switch ran under the 272 cases (R10 §1.7). PLAN §8 item 20: by reading stacker 0.1.25, `remaining_stack` starts from `pthread_get_stackaddr_np` minus `pthread_get_stacksize_np`, the bounds the C++ reads (src/oblib/lib/utility/ob_common_utility.cpp:111-119), and `grow` maps a guarded stack; stacker has no total cap, which the wrapper adds, and Step 2a runs the test. **Rejected:** weaker orderings chosen per field; porting `ObLatch`; translating 17K lines of lock-free code (the 54123f13e bug class would stay); keeping all thread-locals; rewriting recursions as loops.

## 12. Data version, bootstrap and catalog access

**Decision** (Decision 11; R06 §4.5 and R11 §9 agree):
1. The engine reads etc/seekdb.data_version.bin before opening meta.db, logs or storage (today meta.db opens first, src/observer/ob_server.cpp:1698-1719).
2. A missing file means a fresh install only with no block file, no clog (`check_need_initialize`, ob_server.cpp:410-433) and no etc/observer.data_version.bin, which closes src/oblib/common/ob_data_version_mgr.cpp:58-60 and :88-94.
3. Bootstrap writes the file first (a temporary name, then a rename) in today's format (magic 0xBEDE, format 2), so a frozen C++ binary refuses a Rust directory by its own check.
4. `DATA_CURRENT_VERSION` (src/oblib/common/ob_version_def.h:53) becomes 2.0.0.0; the package version stays 1.4.0.0 until a release is named (default, for the developer to confirm). No configured .result shows either.
5. A refusal prints one message to stderr and the log, naming the found and expected versions and the way out (export, then import), and exits non-zero.
6. meta.db stays SQLite (rusqlite), opened after the gate, at store/meta/meta.db, a path the C++ build never opens (default, for the developer to confirm).

**Catalog access:** the schema service and bootstrap read and write the inner tables through typed row access, not inner SQL. ob-schema defines the trait, observer implements it over storage, and Step 2a uses an in-memory version. `ObDMLSqlSplicer` keeps its API over direct row writes in the caller's transaction; schema loads are typed scans, sorted as the 56 `ORDER BY` fetches of src/observer/schema/ob_schema_service_sql_impl.cpp; other leaf code keeps inner SQL through `ObISQLClient`. System schemas come from the generated data on every start, and catalog rows are still written as the C++ writes them (default, for the developer to confirm: about 9-10K lines become rewrites). **Why:** each catalog row is an inner-SQL `INSERT` today (src/rootserver/ob_bootstrap.cpp:241-270) and each restart reloads through inner SQL, which is the "inner-SQL schema loading" of Decision 10's switch condition. **Rejected:** inner SQL as today; a catalog stored apart from storage (changes what SQL sees). Checks: a golden catalog dump from the reference after bootstrap, all 776 system variables, `ob_error` for every code (R11 §9.4).

## 13. Toolchain, dependencies and the wasm guidelines

**Toolchain** (Decision 16; R12 §3): 1.98.1 with clippy and rustfmt; edition 2024 and `resolver = "3"`; no `#![feature]`, `-Z` flag or `RUSTC_BOOTSTRAP` (a gate greps); Cargo.lock committed and builds `--locked --offline`; `=` pins where output bytes are judged: flate2 =1.1.10 and miniz_oxide =0.9.1, which produce the reference's compressed-protocol frames (rust/sql-nio/src/compress.rs:93), and seekdb-jemalloc-sys =0.2.2. C and C++ built by cargo use deps/3rd's clang 17.0.6, SDK 26.2 and the reference flags (`-O2 -ffp-contract=off -fno-strict-aliasing -march=armv8-a+crc+lse`), set once in .cargo/config.toml; prebuilt archives come from deps/3rd unchanged. The loop denies add `cargo clippy`, `cargo doc`, `cargo bench` and `cargo fix`, which compile. Rejected: any nightly (Decision 16).

**Dependencies:** none without a rulebook row naming the crate, its pin and the reason; R12 §4's rows, with R10 §4.3's versions, are accepted with this document (default, for the developer to confirm). Banned: async runtimes, serde or bincode for anything persisted or sent, parser generators and the regex crate for SQL. The C libraries in the product are zlib 1.2.13 (`COMPRESS()` and OUTFILE GZIP and DEFLATE bytes), zstd 1.3.8 (OUTFILE ZSTD), SQLite, libxml2, ICU 69, S2's libcrypto, ring and jemalloc (default, for the developer to confirm).

**All six wasm guidelines are adopted now** (R12 §8): u64 for persisted and wire fields; no 128-bit atomics; the depth limit on wasm (section 11); networking behind cargo features; SIMD chosen at compile time, with a scalar version of each kernel; `panic = "abort"` everywhere. Budget sizing from the 2 GiB heap waits for wasm.

## 14. Naming rules for the manifest

From R12 §9:
1. A translated stem goes to `rust/<crate>/src/<dir>/<stem>.rs`: `<dir>` is its defining file's directory below the crate's prefix, `<stem>` the C++ stem unchanged; a class declared in query/api or data_plane/api goes where its .cpp is.
2. A file of 4,000 lines or more splits at class or function boundaries into `<stem>_p01.rs`, `<stem>_p02.rs`, in source order; `<stem>.rs` holds the types; members of a split type are `pub(crate)`.
3. Identifiers stay as in C++ (default, for the developer to confirm); macros become lowercase `macro_rules!`; a Rust keyword becomes a raw identifier (`r#type`), and `self`, `super` and `crate` take a trailing `_`. A core type that replaces one C++ type keeps its name (`ObDatum`, `ObHashMap`, `ObWarningBuffer`); one with no single counterpart gets a plain name (`ParseTree`, `EvalFrame`, `ScanHost`). Rejected: Rust-style names, which need a rename table every unit applies alike, and dropping `ob_` already collides twice (R12 §1.4).
4. Manifest columns: `source`, `target`, `unit_id` (the map's key; `.pNN` for split pieces; `core/<crate>/<module>` for core units), `kind` (`stem`, `split` or `subsystem`), `inputs` (files, with line ranges for pieces); migration/core-manifest.tsv uses the same.
5. A script writes every lib.rs and mod.rs from the manifest, so an existing target means done.
6. Imports use full paths from migration/decl-index/<unit_id>.txt; no glob imports.
7. Home modules in ob-base: logging, `log_user_error!`, `smart_call!`, `sync`, the hash maps and functions, the sorts, tracepoints and `DEBUG_SYNC`, the scope guard, `Id<T>` and `Arena<T>`.
8. The only comments in translated code are `// TODO(port): `, `// PERF(port): `, `// BUG(port): ` and `// SAFETY: `, each on its own line with the C++ file:line, and the last-line trailer `// PORT STATUS: confidence=<high|medium|low> todos=<N>`. The frozen core API's public items carry rustdoc, since Step 2b's translator B gets "public API docs only" (PLAN §6, departure 5) (default, for the developer to confirm).
9. Step 3 units write no tests; core crates keep differential tests with vectors recorded from the reference.

## 15. The Step 2a narrow path the core must support first

The path of PLAN §6, through these crates, in this order:
1. **Start:** seekdb calls `Server::open` with an absolute base dir; upper services are fakes. Bootstrap runs in memory: the server runtime "sys", the 776 system variables with their 8 overrides and environment values, the six databases, root, the max-id counters at the C++ starting values, the 640 system schemas from generated data. No catalog rows, system tablets, meta.db, inner SQL, system packages or virtual tables (R11 §9.3).
2. **Protocol:** sql-nio's Rust API with minimal connect and query handlers; logins as root and admin.
3. **Parse:** sql-parser-sys, the conversion to `ParseTree`, the Rust `ObFastParser`.
4. **Resolve, rewrite, optimize:** throwaway minimal resolvers for single-table SELECT, INSERT, CREATE TABLE, CREATE USER, CREATE DATABASE and GRANT over the in-memory catalog; the transformer driver; the optimizer's single-table path over the section 6.1 estimator. The IR is final.
5. **Generate and execute:** sql-codegen with `detect_batch_size`; frames and the operators the list uses (table scan with pushdown filters, insert, values, sort, group by, limit); the text formatters for integers, decimal-int, ObNumber, doubles and strings.
6. **Storage:** storage-api's traits, the access path with the one log stream folded into storage-tablet, the memtable with the C++ B-tree rules, autocommit transactions without a WAL, the per-tablet `__pk_increment`.

The 20-50 statements come from the 100 plain-SQL cases that need no SHOW or DESC, views, information_schema, PL or `dbms_*` (R11 §6; /Users/colin/.claude/jobs/39d4f781/tmp/design/plain_sql_no_vt_pl.txt); they cover tables with and without a primary key, one partitioned table at dop 1, DECIMAL, DOUBLE, a VARCHAR with a non-binary collation, ORDER BY with ties, LIMIT and `--error`. Step 2a also runs the stacker test and stack probe, checks `nm` for the strong `try_check_mem_status`, times full parses and the scan+filter+aggregate query (with the C++ plan cache off if the Rust build has none yet), and makes one geo call on a stack Rust grew. The path needs no system-package PL, inner-SQL schema loading or virtual tables by design (section 12); if it still fails its list, Decision 10 (b) applies.

**A gap in the core-build exit.** PLAN §6 asks the core build to pass "the 130 plain-SQL cases" (128 configured, migration/judge/lists/plain-sql.txt). 28 of them need virtual tables, views, PL, system packages or information_schema, and all of them run SQL-tier and DDL code that is not core (R01 §3; R11 §6). Default, for the developer to confirm: the exit list is the 100 cases; the SQL-tier and DDL units they reach are translated after the core API sign-off, through prompt 04, as the first Step 3 batches; the other 28 move to Step 5.

## 16. PLAN §8 items 21-23

Items 17-20 are settled in 6.1, 6.2, 9.4 and 11. Item 21 (timing-sensitive cases): the timer contract (7.2) and thread QoS (11) keep background work where the C++ has it; 00b should also disable, in both init profiles, the statistics jobs and the 02:00 major freeze, which are live under the reduced init (R06 §5.2). Item 22 (coverage depth): the differential tests of sections 2, 10 and 12 and the `checked` profile. Item 23 (false failures from exact comparison): sections 4.1, 6 and 10.

## 17. Defaults for the developer to confirm

| # | Default | Section |
|---|---|---|
| 1 | Core-build exit: the 100 plain-SQL cases, their SQL-tier and DDL units translated first; the other 28 in Step 5 | 15 |
| 2 | Physical standby deferred, so no async runtime anywhere; bootstrap telemetry dropped | 7.4 |
| 3 | The extra `unsafe` uses in ob-platform and ob-clib-sys are named | 8 |
| 4 | The C libraries of section 13 stay in the product | 13 |
| 5 | Generated code is checked in and diffed at gates | 1.4, 9.2 |
| 6 | Codes stay values until parity; `Option` APIs after | 2 |
| 7 | The warning buffer is reached through a per-thread slot | 2 |
| 8 | Server logs keep the `ret=<code>` text | 2 |
| 9 | Budgets trip by the same formulas, not bytes; memory virtual tables from Rust counters | 3.2 |
| 10 | Family 12's -11049 check replaces `mem_hold` in its own test text, as frozen tests use `--replace_numeric_round`, declared in 00b; -7603 stays internal | 3.2 |
| 11 | 64-bit ids for every IR type | 4.1 |
| 12 | The owned `ParseTree`; handles only if parse time fails 1.2x | 4.2 |
| 13 | The safe handle datum; a raw-pointer datum crate only if Step 2a fails 1.2x | 5 |
| 14 | Estimator option (b), rows per micro block free, EST.TIME differences documented at the final gate | 6.1 |
| 15 | Table blocks compressed with the kept zstd 1.3.8 | 6.1 |
| 16 | `fork` daemon mode, `--initialize` and SIGUSR1's stop kept | 7.3 |
| 17 | One C island for the three grammars; relaxed-rapidjson ported; island `std::bad_alloc` mappings kept | 9 |
| 18 | Parser charset callbacks in Rust; C++ charset only as a Step 2a stopgap | 9.2 |
| 19 | The libc++ sort and heap transcription, with its notice, in the fork | 10 |
| 20 | Exact hash order everywhere; the mask as a safety net | 10 |
| 21 | `judge` differs from `release` in build settings; `checked` is diagnostic only; test-only C++ allowed | 10 |
| 22 | The macOS arm64 numeric behavior on every platform | 10 |
| 23 | `stack_size` becomes a lower bound under 8 MiB stacks; loom only, no nightly Miri or TSan job | 11 |
| 24 | Data version 2.0.0.0, package version unchanged; meta.db at store/meta/meta.db, SQLite | 12 |
| 25 | Typed catalog access for the schema service | 12 |
| 26 | The dependency rows of section 13 accepted with this document | 13 |
| 27 | C++ names; the markers and trailer are the only comments; rustdoc on the frozen core API | 14 |
| 28 | sql-nio keeps its Rust encoders; only the `nio_*` entry points go | 1.1 |

## 18. Corrections to PLAN.md and the report

- The micro block cache has no retries at 834bbee1e, so family 12 expects -4013 without sleeps; -7603 never reaches the client (3.2).
- PLAN §3 item 1's ob_sort_op_impl.cpp:440-445 is the top-N check, not a sort comparator; neither "unfixed" comparator feeds a sort, and the quick-sort comparator already falls back to address order after an error (src/sql/engine/sort/ob_sort_op_impl.cpp:401-408; R02 §2.6, R05 §1.7).
- The compressed protocol's frames come from miniz_oxide, not zlib (R12 §1.1).
- Seven generators, whose checked-in output is 49,345 lines, not "about 87K" (R11 §3.5).
- The grammar has 2,675 rules with C actions, not 758; 93 statement resolvers are registered, not 89 (R04 §2).
- The atomics retype covers 1,055 (file, field) pairs, not about 823 names (R10 §1.1).
- The plain-SQL list has 128 configured cases, 100 needing no leaves (R11 §6); unordered single-table SELECTs number 9,042 (R06 §1.3).
- The EST mask does not cover view_2.result:473-498; 9 configured cases freeze, not 8 (R06 §5.1).
- Mutation 03 cannot be caught by an INT ORDER BY under the full init, where tracepoint 1200 sends INT keys to `ObAdaptiveQS` (src/sql/optimizer/ob_optimizer_util.cpp:6988-6992; R05 §4.4).
