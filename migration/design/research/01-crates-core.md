# Research 01: the crate graph and the core's scope

Input to the design document (migration/RULEBOOK.md), Step 1. Question: which crates the Rust tree has, the edges between them, how the C++ directory cycle is broken, how big the hand-designed core is, which directories the core must own for the Step 2a narrow path, and where the existing rust/sql-nio sits.

Everything below was measured at 834bbee1e (the worktree's src/, rust/, cmake/ and bazel/ are identical to it: `git diff --stat 834bbee1e HEAD -- src rust CMakeLists.txt cmake bazel` prints nothing). The scripts that produced the counts are in migration/design/evidence/01-crates/ (`incgraph.py`, `crate_analysis.py`, `linkgraph.py`, the crate map `crates.tsv` and its order `order.txt`); section 8 says how to rerun them.

## Summary

- The C++ include graph has one directory-level cycle of **110 directories** (sql 57, storage 42, rootserver 7, pl 4; 4,087 live files, 1.86M lines) when forwarding headers are treated as ordinary files, and **102 directories** (sql 56, storage 39, rootserver 7; 3,964 files) when they are followed to the header they forward to. The report's 110/4,108 is reproduced at directory level; the file count differs by 21 (076eb309b against 834bbee1e and a slightly different live set). The **27 header cycles over 69 files** are reproduced exactly.
- At module level only **3 include lines** join sql, storage and rootserver into a cycle. The real coupling is below and beside that: 95 two-way pairs among the sql subdirectories, a storage core whose directories all include each other, and declarations in query/api and data_plane/api whose code lives in sql, storage and observer.
- Bazel does not give crate boundaries for sql or storage: each is one runtime library (`sql_runtime`, `storage_runtime`). Only oblib (21 targets) and share (77 targets) are split finely.
- Recommendation: **35 crates** (28 redesigned or translated crates, 4 C/C++ island crates, the existing sql-nio, the Rust parser layer and the binary crate; section 5). The largest are storage-tablet (174.7K hand-written C++ lines) and sql-expr (171.8K); none is over the 180K cap. Against this map, **1,053 of 13,112 cross-crate include lines (8.0%)** and **2,929 of 40,904 cross-crate symbol references (7.2%)** go from a lower crate to a higher one. Those are the cuts; section 5 sorts them into five kinds, each with its fix.
- The narrow path needs more than the report's narrow core: the schema object model (ob-schema, 88.6K), the storage access path and the single log stream's entry points are on it. It does not need the sstable formats (87.6K). So the recommended core scope is **neither 352K nor 600-650K**: the narrow core plus the schema model and the storage entry points for Step 2a, about 500K lines, with the sstable formats added in the core build.

## 1. What the C++ does today

### 1.1 Size

Live files are the ones compiled by the reference build (compile_commands.json of /Users/colin/seekdb-dev/ref-834bbee1e/build_release, unity members included) plus everything they reach by `#include`.

| Set | Files | Lines |
|---|---|---|
| Tracked C/C++ under src/ and rust/sql-nio/include (.h .hpp .cpp .cc .c .ipp .def) | 6,645 | 2,754,830 |
| Generated at build time (build_release/generated: inner-table schema, sqlite virtual table) | 52 | 104,297 |
| Live, all kinds | 6,590 | 2,825,378 |
| of which hand-written | 6,445 | 2,284,483 |
| of which data (ob_ik_dic.cpp, ob_timezone_info.cpp, ob_ctype_utf8_tab.h) | 3 | 320,504 |
| of which generated and tracked (errno, system variables, codec, protobuf) | 22 | 86,709 |
| of which vendored (zstd 1.3.8, libeasy headers, xxhash) | 68 | 29,385 |
| Tracked but dead (reached by no compiled file) | 107 | 33,749 |

Live lines per module (hand-written in brackets where it differs):

| Module | Lines | Largest subdirectories (live lines) |
|---|---|---|
| src/sql | 962,171 [946,230] | engine 351,755 (expr 170,218; px 41,140; basic 34,043), optimizer 155,780, resolver 151,357 (dml 46,346; ddl 40,489; expr 35,318), rewrite 97,271, das 46,252, session 33,063, top level 27,632, code_generator 20,519, plan_cache 19,890 |
| src/storage | 755,629 [479,586] | fts 286,550 (the IK dictionary 276,043), blocksstable 87,627, tx 37,103, tablet 34,645, ddl 34,344, compaction 32,965, access 31,614, top level 27,324, memtable 26,926, tmp_file 20,893, tablelock 16,157, ls 15,425, multi_data_source 14,243, lob 14,210 |
| src/oblib | 362,518 [251,525] | lib 193,781 (codec 27,287; compress 26,523; charset 17,686; utility 15,377; hash 13,733; container 11,481), common 146,651 (timezone 47,164; json_type 26,565; object 15,469; xml 14,961; number 10,103) |
| src/share | 270,498 [237,411] | schema 76,465, top level 66,910, geo 47,684, object 17,006, system_variable 14,008 |
| src/observer | 130,183 | virtual_table 49,040, vector_index 24,890, top level 15,824, mysql 14,372, schema 11,356 |
| src/rootserver | 104,049 | top level 50,299 (ob_ddl_service.cpp 23,564), ddl_task 30,969 |
| src/pl, logservice, query, data_plane, standby, objit | 39,137 / 32,705 / 30,384 / 16,091 / 14,696 / 2,486 | logservice/palf 23,057; query/api/query/engine 14,150 |
| build-time generated | 104,297 | share/inner_table 102,481 |

### 1.2 The include graph

Quoted includes are resolved the way the compiler does: the including file's directory first, then the -I roots in the order of an src/sql compile command (rust/sql-nio/include, repo root, build_release/generated three times, src, src/query/api, src/data_plane/api, src/objit/include, src/oblib/easy, src/oblib, src/oblib/common, src/oblib/easy/include). 25,411 include lines resolve; 79 do not (66 distinct names, system and third-party headers). 117 headers only forward to another header (the report counted 118 at 076eb309b).

| Level | Result at 834bbee1e | Report (076eb309b) |
|---|---|---|
| Header to header | 27 cycles over 69 files; largest 8: seven files in storage/multi_data_source plus storage/tx/ob_multi_data_source.h; next 7 files around sql/pl/ob_pl.h | 27 over 69 |
| File, .cpp included | 29 cycles over 73 files | 29 over 73 |
| Directory, forwarders as ordinary files | one cycle of 110 directories (sql 57, storage 42, rootserver 7, pl 4), 4,087 files, 1,856,316 lines | 110 directories, 4,108 files |
| Directory, forwarders followed | 102 directories (sql 56, storage 39, rootserver 7), 3,964 files, 1,809,802 lines | not measured |
| Directory, query/api and data_plane/api headers moved to the directory of their same-stem .cpp (67 headers) | 128 directories (adds observer 11, query 6, data_plane 2, standby 3), 4,665 files, 1,992,332 lines | not measured |

The four pl directories drop out when forwarders are followed: rootserver reaches src/pl only through three includes of forwarders that point into src/sql/pl (rootserver/ob_ddl_operator.cpp, pl_ddl/ob_pl_ddl_operator.cpp, ddl_task/ob_ddl_redefinition_task.cpp).

Other directory cycles (forwarders followed): src/oblib/lib 38 directories (445 files, 133,156 lines); src/share with the generated inner-table headers 21 (657 files, 360,839 lines); src/oblib/common 13 (139, 138,985); src/observer 9 (495, 119,033); src/query/api/query/engine 5 (44, 12,260); src/logservice 4 (120, 32,286); src/oblib/rpc 3; src/standby 3.

At module level (forwarders followed) the graph is acyclic except for {rootserver, sql, storage}, which three include lines close:
- src/sql/engine/px/ob_px_sub_coord.cpp:30-31 include storage/ddl/ob_ddl_direct_load_utils.h and storage/ddl/ob_ddl_insert_dag.h;
- src/storage/ddl/ob_ddl_insert_dag.cpp:22 includes rootserver/ddl_task/ob_ddl_task.h.
A fourth line, src/storage/ddl/ob_tablet_slice_writer.cpp:27, includes sql/engine/ob_batch_rows.h, which only forwards to query/engine/ob_batch_rows.h. All four came from d51422b54 (report §6).

The module order is therefore not the problem. Include lines per module pair (forwarders followed) show where the weight is: sql → query 1,128, sql → data_plane 192, storage → data_plane 266, storage → query 124, observer → sql 254, observer → storage 252, pl → sql 162, rootserver → storage 75, rootserver → sql 56. storage reaches SQL code through query/api (ObExpr, the pushdown filter, the vector formats), and sql reaches storage code through data_plane/api. Inside src/sql, the subdirectory groups (depth 2, engine at depth 3) have 95 two-way pairs; the heaviest are engine/expr ↔ engine top level 165/4, rewrite → optimizer 56 with optimizer → rewrite 29, rewrite ↔ resolver 63/20, engine/expr ↔ session 72/3, engine/cmd → resolver 68, optimizer ↔ resolver 50/19.

### 1.3 The link graph

`nm -m -g` over the 366 objects of the reference build (64 MB of symbol lines, 6.6 s): an undefined symbol in one object that exactly one other object defines strongly (weak definitions, which are inline and template copies, are skipped) is a call or data edge. A unity object whose members fall into more than one crate is split by searching its member files for the definition; definitions produced by macros (for example `OB_SERIALIZE_MEMBER`) fall back to the crate holding most of the object's lines. So the link counts are approximate by a few percent; the include counts are exact.

What the link graph adds to the include graph: code declared in one place and defined in another. With the crate map of section 5, the storage crates call 125 symbols defined in the SQL crates (the pushdown filter executors, `ObExpr` evaluation, DAS iterators), the SQL crates call 224 symbols defined in pl, and the SQL, storage and rootserver crates call 297 symbols defined in observer (mostly `share::ObVectorIndexUtil`, defined under src/observer/vector_index, and `query::ObInnerSQLConnectionAccess`, defined in src/observer/ob_inner_sql_connection.cpp). None of these shows at include level, because the declarations sit in query/api, data_plane/api or share.

### 1.4 The Bazel graph

The 20 BUILD.bazel files under src declare 179 `cc_library`, 50 `seekdb_semantic_unity_cc_library` and 6 `seekdb_generated_unity_cc_library` targets (counted with a Python `ast` pass over the files). Per package:

| Package | Targets | What they are |
|---|---|---|
| share | 47 + 28 unity + 2 generated | fine-grained: error_codes, config, io, cache, schema_entities, schema_access, object_cast, datum, ... |
| oblib | 12 + 9 unity | foundation, bitmap, codec, vector, malloc, common, rpc, compression |
| query | 25 | header interfaces only (sql_expression_interface, root_command_service, ddl_schema_service, ...) |
| data_plane | 27 + 1 generated | header interfaces only (tablet_scan_interface, datum_row_interface, ...) |
| sql | 7 + 3 unity | one runtime library in three parts: sql_runtime, sql_runtime_without_pass, sql_runtime_simd |
| storage | 12 + 2 unity + 1 generated | one runtime library: storage_runtime, storage_runtime_simd, plus small interface targets |

src/sql/sql_runtime_group_deps.bzl ("Exact fine-grained dependencies for SQL Unity compile groups", line 1) maps each SQL unity group to the share targets it needs; it does not split SQL itself. So the Bazel graph is acyclic because the interfaces were pulled out into query and data_plane header targets, while the code behind them stayed in two large libraries. It can seed the crates below SQL and storage (share, oblib); it cannot seed a split of the 946K lines of SQL or the 480K hand-written lines of storage.

### 1.5 How the C++ already breaks upward calls

The C++ already uses two ways to call up the stack without an include cycle, and both have direct Rust equivalents:
- **Interfaces declared low, implemented high.** `query::ObIRootCommandService` (src/query/api/query/command/ob_root_command_service.h:36) is implemented by `ObLocalManagementService` in rootserver (src/rootserver/ob_local_management_service.cpp:646, `parallel_create_table`); SQL reaches it as `ctx.root_command_service()`. `ObITabletScan` (src/data_plane/api/data_plane/access/ob_tablet_scan.h:493) is implemented by `ObAccessService` (src/storage/tx_storage/ob_access_service.h:69). `ObSchemaService` (src/share/schema/ob_schema_service.h:509) is implemented by `ObSchemaServiceSQLImpl` (src/observer/schema/ob_schema_service_sql_impl.h:64), which reads the inner tables through `ObISQLClient`/`ObMySQLProxy` on 183 lines.
- **Process-wide slots bound at start.** `server_service<T>()` appears on 1,243 lines with 121 distinct `T`; the slot is `ObServerServiceSlot` with a plain `inline static Service *service_` (src/share/rc/ob_server_runtime.h:127-129), bound by 83 `BIND_SERVICE` and cleared by 96 `UNBIND_SERVICE` lines in src/observer/omt/ob_server_runtime_controller.cpp.

### 1.6 rust/sql-nio today

23 .rs files, 8,448 lines, in a one-member workspace (rust/Cargo.toml `members = ["sql-nio"]`). It is a `staticlib` + `rlib` (rust/sql-nio/Cargo.toml) linked into the C++ binary by cmake/Rust.cmake, with `lto = "thin"`, `codegen-units = 1`, `debug = true` and `panic = "abort"` in `[profile.release]`. Its C ABI: 33 `#[no_mangle]` functions in the cbindgen-generated include/nio.h (534 lines), included by 10 C++ files (8 under src/oblib/rpc, src/observer/mysql/obmp_packet_sender.cpp and obmp_stmt_execute.cpp; src/query/api/query/protocol/ob_mysql_rust_row.h is the tenth); 4 reverse callbacks into C++ (`ob_sql_sock_handler_on_connect/_on_readable/_on_disconnect/_on_close`, src/ffi_types.rs:53-72); 186 lines containing `unsafe`. Its dependencies are all external: mio, flate2, slab, socket2, rustls (ring), rustls-pki-types, x509-parser, libc / windows-sys, and cbindgen at build time. The two files the plan drops are row_encode.rs (735 lines) and response_api.rs (966).

## 2. Constraints that bind this topic

From migration/decisions.md (which wins over the plan) and migration/PLAN.md:

| Source | What it fixes for the crate graph |
|---|---|
| Decision 10 (a) | A parallel Rust tree and one cutover, so no crate has to link against half-ported C++ modules; the only C++ left is the islands. Switch condition: if the Step 2a narrow run cannot pass its 20-50 single-table statements without most of bootstrap (system-package PL, inner-SQL schema loading, virtual tables), switch to (b). The crate plan must make that narrow run buildable. |
| Decision 13 (a) | Islands kept in C++ behind a C ABI: the parser's C core (bison/flex output, sql_parser_base.c, parse_node.c), vsag, S2, share/geo with boost.geometry, ICU regex. `ObParser`, `ObPLParser` and `ObFastParser` are ported to Rust. |
| Decision 14 (b) | `#![forbid(unsafe_code)]` everywhere except named crates (island shims, SIMD kernels, IO buffers, thin wrappers over vetted reclamation crates), with an `unsafe` count per crate at every gate. So every island and every FFI line needs its own crate. |
| Decision 16 (a) | One pinned stable toolchain (1.98.1), no nightly in shared code: each crate is type-checked on one thread, which is why the plan caps crate size. |
| Decision 8 notes, PLAN §3 guidelines | Keep process ownership (chdir, signal handlers, `_Exit`, the global allocator, `panic=abort`) out of the engine crates, in the server binary's crate. |
| Decision 7 | macOS arm64 first; wasm, Android, Linux, Windows later, so nothing in the graph may assume one process or one platform. |
| Decision 11 (a) | New on-disk formats under a bumped data version, so the storage crates are free to change layouts. |
| Decisions 2, 3 | Fork, frozen, never replayed: no reason to keep C++ file layout for upstream merges. |
| PLAN §3 "Crates" | 20-40 crates, acyclic, each at most about 100-180K lines; seed from the Bazel graph, not the 12 module roots; sql-nio becomes a plain crate, its row_encode.rs and response_api.rs dropped. |
| PLAN §3 "sql/storage boundary" | Storage is the lower crate; it defines the column batch and a filter/aggregate trait that SQL implements. |
| PLAN §6 Step 1 exit | "an acyclic crate graph"; prompt 01's `[crate / package / module]` = "cargo crate in the 20-40-crate plan". |
| PLAN §6 core build order | (1) foundation and runtime; (2) IR; (3) execution framework and code generator; (4) storage boundary, tablet/memtable/transaction core, single-writer WAL. |

Measured on this Mac: `cargo check` of cranelift-codegen ran at 19,000-44,000 hand-written lines per second on one thread, under 0.82 GB (migration/measurements/cargo-check-rate.md), so a 180K-line crate would check in about 4-10 s, far under Decision 16's 60 s line (assumption: translated seekdb code checks at a similar rate; Step 2a measures it).

## 3. The Step 2a narrow path: what the core must own

PLAN §6, Step 2a: sql-nio, the C parser core over FFI with its tree converted to owned Rust, the new IR for single-table SELECT, INSERT and CREATE TABLE, typed column batches and the storage filter trait, down to memtable-only storage, passing 20-50 single-table statements from the plain-SQL cases under the reduced init (which creates the admin user and the test database with its grants, PLAN §8 item 4).

The C++ path those statements take:

| Step | C++ entry points (file:line) | Directories | In the report's narrow core? |
|---|---|---|---|
| Protocol | `ObMPQuery::process_single_stmt` → `get_observer_sql_engine()->stmt_query` (src/observer/mysql/obmp_query.cpp:340, :845) | rust/sql-nio, observer/mysql (14,372) | no; sql-nio is Rust already |
| Pipeline | `ObSql::stmt_query` (src/sql/ob_sql.cpp:173), `generate_stmt` :2021, `transform_stmt` :2624, `optimize_stmt` :2712, `code_generate` :2736 | sql top level (27,632) | no |
| Parse | ob_parser.cpp (1,275), ob_fast_parser.cpp (2,527) over the C core | sql/parser | no (ported layer + island) |
| Resolve | ObDMLResolver (ob_dml_resolver.cpp 12,826), ObSelectResolver (4,676), ObInsertResolver (1,175), ObCreateTableResolver (2,346), all through the schema guard | sql/resolver/{dml,ddl}; the IR in resolver/expr and the stmt classes | the IR only |
| Schema | `ObSchemaService` (src/share/schema/ob_schema_service.h:509), loaded by `ObSchemaServiceSQLImpl` over inner SQL (183 `ObISQLClient`/`ObMySQLProxy` lines); ob_multi_version_schema_service.cpp (2,839), ob_server_schema_service.cpp (3,404) | share/schema (76,465), observer/schema (11,356), generated inner tables (102,481) | **no** (wider scope) |
| Transform, optimize | ob_transformer_impl.cpp (983), ob_optimizer.cpp (1,252), ob_select_log_plan.cpp (7,795), ob_log_plan.cpp (12,805), ob_join_order.cpp (17,680) | sql/rewrite, sql/optimizer | no |
| Code generation | ob_static_engine_cg.cpp (6,563) | sql/code_generator | yes |
| Execute | ob_table_scan_op.cpp (3,859), ob_table_insert_op.cpp (396), the expressions the statements use | sql/engine | framework only |
| DAS to storage | `tsc_service.table_scan` (src/sql/das/ob_das_scan_op.cpp:1237) → `ObAccessService::table_scan` (src/storage/tx_storage/ob_access_service.cpp:335); `insert_rows` :810 | sql/das, storage/tx_storage | data_plane access only |
| Read path | ob_table_scan_iterator.cpp (733), ob_multiple_scan_merge.cpp (779) | storage/access (31,614) | **no** |
| Memtable, transaction | ob_memtable.cpp (3,079), ob_trans_service_v4.cpp (1,350) | storage/{memtable,tx,tx_table} | yes |
| Log stream | one log stream only: `ObLSID::is_valid()` is `SYS_LS_ID == id_` (src/share/ob_ls_id.h:34, :47); `ObLSService` holds one `ObLS *ls_` and `get_ls(ObLS *&)` takes no id (src/storage/tx_storage/ob_ls_service.h:70, :138) | storage/ls (15,425), storage/tx_storage (8,243) | **no** |
| Redo | `log_handler_->append` (src/storage/tx/ob_tx_log_adapter.cpp:90; interface at src/logservice/ob_log_handler.h:60) | logservice (32,705) | no (the core build adds "a single-writer WAL") |
| CREATE TABLE | `ObCreateTableExecutor::execute` (src/sql/engine/cmd/ob_table_executor.cpp:442) → `ObDDLExecutorUtil::execute_pcreate_table` (src/sql/engine/cmd/ob_ddl_executor_util.cpp:286; `root_commands.parallel_create_table` at :295) → `ObLocalManagementService::parallel_create_table` (src/rootserver/ob_local_management_service.cpp:646) → `ObCreateTableHelper` (parallel_ddl/ob_create_table_helper.cpp, 907) with `ObDDLService` (23,564) and ObDDLOperator (5,895) | sql/engine/cmd, rootserver, share/inner_table | no |
| First start | rootserver/ob_bootstrap.cpp (762) creates the system tables | rootserver | no |

What this means for the core's scope:
- **The schema object model is on every statement's path**, not only CREATE TABLE: each resolver looks names up through the schema guard, and the reduced init itself creates a user, a database and grants. So the core must own ob-schema (share/schema plus observer/schema, 88.6K hand-written) before the narrow run, together with a way to load it that does not need inner SQL on day one (for example the schema kept in memory and written through the new storage, loaded by inner SQL only later). That is exactly the "inner-SQL schema loading" of Decision 10's switch condition, so it should be designed on purpose, not discovered in Step 2a.
- **The storage entry points the SQL tier calls** (`ObAccessService`, the single `ObLS`, the scan iterators in storage/access) are on the path, but the report's narrow core lists only "data_plane access" (1,993 lines of headers). Because seekdb has one log stream, the Rust core can fold the log-stream layer into the storage core's own top object instead of porting it.
- **The sstable formats are not on the narrow path** (memtable-only). They are needed once a memstore freezes, a major merge runs, or a restart recovers from a checkpoint, so they belong to the core build, not to Step 2a (assumption: no plain-SQL case triggers a freeze under the reduced init; the core build's run loop over the 130 cases shows it).
- The resolvers, the transformer driver, the optimizer's single-table path, the table-scan and insert operators and CREATE TABLE's DDL path are SQL tier and leaves by the plan's tiers, but the narrow run needs a minimal version of each. Those are disposable in Step 2a and belong to their own crates afterwards.

So the core scope that fits the narrow path is: the report's narrow rows (about 352K) plus the schema object model (about 88K) plus the storage entry points (storage/access, storage/tx_storage, storage/ls: about 55K, much of it replaced), about 500K lines of C++ to redesign, with the sstable formats (87.6K) added during the core build. The report's wider scope also includes all of resolver/dml and the code generator; the code generator is in the narrow rows already, and the rest of resolver/dml is SQL tier that the narrow run only needs in part.

## 4. Options

| Option | How crates are drawn | Cost | Risk |
|---|---|---|---|
| A. One crate per Bazel target | 179 + 56 targets | Seeds share and oblib well (77 and 21 targets, already acyclic at header level) | sql and storage stay one target each (1.4M hand-written lines); the query/api and data_plane/api interface targets hold declarations whose code is in sql, storage and observer, which a Rust crate cannot do (a struct's inherent impl must be in its crate). Not usable above share. |
| B. One crate per module root (the 12 roots of bazel/architecture/module_policy.bzl) | oblib, share, sql, storage, ... | Few crates; the module order is almost acyclic (3 lines) | sql (946K) and storage (480K) are 5-9 times the cap; link-level cycles storage ↔ sql, sql ↔ pl, sql ↔ observer remain. The plan rejects it. |
| C. Crates by layer of the redesigned engine, sized by measurement | The core's six rows become their own crates; the SQL tier is split along its pipeline (IR, execution framework, expressions, DAS, operators, resolver, rewrite, optimizer, code generator); leaves by function; islands and the binary separate | Every upward edge in the C++ has to be cut once, by moving a header down, by a trait, or by merging; section 5 counts them (1,053 include lines, 2,929 symbols) | The cut list is from C++ includes and symbols; the Rust core's API may create new edges. Step 2a's disposable full pass checks the graph over the whole tree with one survey `cargo check`. |

Option C is the only one that meets the cap and the plan's sql/storage boundary; it uses option A's share and oblib targets to decide which files sit together inside ob-share, ob-runtime and ob-values.

## 5. Recommendation

### 5.1 The crates

35 crates, listed bottom to top; a crate may depend only on crates above it in the table. Sizes are live C++ lines mapped to the crate by the prefix map in crates.tsv (hand-written; all lines in brackets where different). Rust line counts will differ; the cap is applied to the C++ lines as the only measure available now.

| # | Crate | Main C++ sources | Lines | Tier and `unsafe` |
|---|---|---|---|---|
| 1 | ob-base | oblib/lib: alloc, allocator, container, hash, string, rc, lock, atomic, list, queue, utility, oblog, stat, wait_event, trace, profile, signal, objectpool, time, checksum, the top-level headers | 105,212 | core (foundation); forbid |
| 2 | ob-runtime | oblib/lib: thread, task, file, restore, net, future, compress; oblib/rpc; share: io, cache, config, parameter, rc, interrupt, resource_limit_calculator; storage/scheduler and the data_plane DAG scheduler | 56,760 [81,914] | core (runtime); IO buffers may need `unsafe` |
| 3 | ob-values | oblib/common: object, datum, number, wide_integer, rowkey, row, cell, timezone code, sql_mode; oblib/lib/charset; share: datum, object (casts), aggregate, lob | 88,837 [133,298] | core value types plus value libraries; forbid |
| 4 | ob-values-doc | oblib/common: json_type, xml, udt; share: json, semistruct, roaringbitmap, vector | 50,858 | leaf; forbid |
| 5 | ob-share | the rest of share; oblib/common/mysqlclient (the `ObISQLClient` interface); oblib/grpc (to prost/tonic) | 65,446 [96,219] | leaf; forbid |
| 6 | ob-schema | share/schema, share/inner_table, share/system_variable, observer/schema; the generated inner-table schema | 88,602 [205,091] | core for the narrow path (section 3); forbid |
| 7 | geo-sys | share/geo with boost.geometry and the S2 adapter | 47,033 | island (C++); unsafe allowed |
| 8 | ob-log | logservice, share/log | 34,379 | core build (the single-writer WAL); forbid |
| 9 | vsag-sys | oblib/lib/vector/ob_vsag_adaptor | 1,583 | island; unsafe allowed |
| 10 | storage-api | query/api/query/engine/vector (the column formats), data_plane/api access, blocksstable, transaction, memtable, meta | 7,863 | core: the column batch and the filter/aggregate trait; forbid |
| 11 | storage-sstable | storage: blocksstable, blockstore, tmp_file | 110,047 | wider core; SIMD decoders may need `unsafe` |
| 12 | storage-tx | storage: tx, tx_table, memtable, tablelock, deadlock, concurrency_control, allocator, throttle | 98,122 | core; reclamation wrappers may need `unsafe` |
| 13 | storage-tablet | storage: tablet, multi_data_source, meta_mem, ls, tx_storage, slog, slog_ckpt, checkpoint, meta_store, access, lob, truncate_info, the top level; storage/tx/ob_multi_data_source (the one header cycle that would otherwise span two crates) | 174,714 | core; forbid |
| 14 | storage-engine | storage: compaction, ddl | 67,309 | leaf; forbid |
| 15 | storage-search | storage: fts, retrieval, vector_index, vector_type; data_plane vector, fts, retrieval | 30,771 [306,814 with the IK dictionary, which becomes a data file] | leaf; SIMD distance kernels may need `unsafe` |
| 16 | sql-parser-sys | the parser's C core: sql_parser_base, parse_node, keyword tables, the SQL, PL and FTS grammars | 5,337 C, plus 24.6K lines of .y/.l and about 175K lines of bison/flex output | island (C); unsafe allowed |
| 17 | sql-session | sql/session, including the generated system-variable factory | 17,122 [33,063] | SQL tier, low; forbid |
| 18 | sql-ir | sql/resolver/expr, the DML statement classes, ObOptimizerUtil, ObSQLUtils, ObOptimizerTraceImpl, sql/printer, the hint classes, the PL data types (ob_pl_type, ob_pl_user_type) | 85,779 | core (statement IR); forbid |
| 19 | sql-exec | query/api/query/engine (without the column formats); sql/engine ob_operator, ob_exec_context, ob_physical_plan and the other top-level framework files; frame info; the pushdown filter; the chunk stores | 39,768 | core (execution framework); forbid |
| 20 | icu-regex-sys | sql/engine/expr/ob_expr_regexp_context | 954 | island; unsafe allowed |
| 21 | sql-expr | sql/engine/expr (the 528 expressions) and the type deduction pass (ob_raw_expr_deduce_type) | 171,787 | SQL tier; forbid |
| 22 | sql-das | sql/das, ob_table_location, query/api vector | 57,757 | SQL tier; forbid |
| 23 | sql-engine | sql/engine operators, px, dtl | 150,256 | SQL tier; forbid |
| 24 | sql-parser | ObParser, ObFastParser, ObPLParser, parse_malloc, the Rust mirror of `ParseNode` | 9,156 | ported; forbid (the FFI calls go through sql-parser-sys) |
| 25 | sql-resolver | the rest of sql/resolver | 97,612 | SQL tier; forbid |
| 26 | sql-rewrite | sql/rewrite | 97,271 | SQL tier; forbid |
| 27 | sql-optimizer | sql/optimizer | 142,620 | SQL tier; forbid |
| 28 | sql-codegen | sql/code_generator, ob_operator_factory, ob_operator_reg | 21,551 | core (the plan's execution framework row); forbid |
| 29 | sql | ob_sql, plan_cache, executor, monitor, engine/cmd, spi, hybrid_search, tablelock, privilege_check | 76,121 | leaf; forbid |
| 30 | pl | src/pl and sql/pl, without the PL data types | 43,403 | leaf; forbid |
| 31 | rootserver | src/rootserver | 104,049 | leaf; forbid |
| 32 | observer | src/observer: mysql handlers, virtual tables, vector_index, omt, inner SQL connection, server | 117,010 | leaf; forbid |
| 33 | standby | src/standby | 14,696 | leaf; forbid |
| 34 | sql-nio | rust/sql-nio without row_encode.rs and response_api.rs | 6,747 lines of Rust | existing; its remaining `unsafe` is socket code (section 5.4) |
| 35 | seekdb | observer/main.cpp, ob_signal_handle, ob_command_line_parser: `main`, signal handlers, chdir, the global allocator, `panic=abort` | 1,817 | binary; forbid |

Dropped rather than mapped (30,838 lines): src/oblib/easy (libeasy, replaced by sql-nio), src/oblib/lib/codec (no callers outside itself, report §3), rust/sql-nio/include/nio.h.

Only storage-tablet and sql-expr are near the cap. storage-tablet can shed lob and truncate_info (17.7K) to storage-engine, and sql-expr can split into casts, comparison and arithmetic below the function families, if Step 2a measures their checks as slow.

### 5.2 Edges

The order in the table is a topological order; crate_analysis.py prints each crate's direct dependencies. In outline: the ob- crates stack in table order; storage-api is under storage-sstable, which is under storage-tx (which also uses ob-log), then storage-tablet, then storage-engine and storage-search; the SQL crates stack from sql-session and sql-ir through sql-exec, sql-expr, sql-das and sql-engine to sql-resolver, sql-rewrite, sql-optimizer, sql-codegen and sql, with sql-parser over sql-parser-sys; pl, rootserver, observer and seekdb come last. sql-nio depends on no seekdb crate, and observer uses it.

Where a lower crate needs code from a higher one at run time, the lower crate defines a trait and the higher crate implements it; the seekdb binary crate builds the server context that holds the implementations and hands it down (PLAN §3: "an explicit server context in place of the `server_service<T>` slots and `GCTX`"). This is the Rust form of what the C++ already does with `ObIRootCommandService`, `ObITabletScan`, `ObSchemaService` and `ObISQLClient` (section 1.5).

### 5.3 How the directory cycle is broken

Against the map above, 1,053 include lines in 106 crate pairs and 2,929 symbol references in 135 crate pairs go upward. They fall into five kinds:

| Kind | Include lines (pairs) | Symbols (pairs) | Largest pairs | Fix |
|---|---|---|---|---|
| Inside the core | 262 (11) | 679 (9) | storage-sstable → storage-tablet 114 lines; storage-tx → storage-tablet 97 lines, 350 symbols (`ObTableHandleV2`, `ObLSService`, `ObLS`) | Redesigned by hand, so the new API decides. The direction is sstable below transaction below tablet; the transaction layer gets memtable and log handles from its own context instead of reaching up through the single log stream. |
| Core to a leaf or the SQL tier | 315 (26) | 732 (31) | storage-tablet → storage-engine 70 lines, 202 symbols (DDL memtables, compaction scheduling); sql-exec → sql-engine 89 symbols (operator spec serialization, PX bloom filter); ob-runtime → ob-share 36 lines (share/ob_define.h 10) | Mostly types in the wrong place: move them down (share/ob_define.h into ob-base; the chunk and temp-block stores into sql-exec). Where the core starts leaf work (the tablet scheduling a merge or holding a DDL memtable), a trait in the core implemented by storage-engine. |
| Inside the SQL tier | 219 (29) | 611 (27) | sql-engine → sql 83 symbols (`ObMonitorNode`, `ObSQLUtils`); sql-rewrite → sql-optimizer 24 lines, 45 symbols (`ObLogPlan`, `ObTableLocation` users); sql-expr → sql-engine 15 lines (`ObSubPlanFilterOp` iterators) | Move utility classes down (done in the map for `ObOptimizerUtil`, `ObSQLUtils`, the printer, `ObTableLocation`; still to do for `ObMonitorNode` and the parts of `ObTransformUtils` the resolver and IR call). Each pair is small: the largest is 24 lines. |
| SQL to PL | 91 (12) | 224 (11) | sql-resolver → pl 28 lines, 95 symbols; sql → pl 18 lines, 58 symbols; sql-expr → pl 15 lines | PL data types already moved to sql-ir. Calls into the PL interpreter and PL resolver (`ObPL::execute`, `ObPLResolver`, `ObPLPackageGuard`) go through a trait defined in sql-exec and implemented in pl, bound in the server context. PL calls back into SQL directly (pl sits above sql). |
| Storage to SQL | 78 (11) | 125 (19) | storage-tablet → sql-exec 24 lines (ob_pushdown_filter.h, ob_expr.h); storage-sstable → sql-exec 17 symbols (`ObWhiteFilterExecutor::filter_datum`, `ObPhysicalFilterExecutor::filter`); storage-search → sql-das 21 symbols | The plan's boundary: storage-api defines the column batch and the filter/aggregate trait; the SQL filter executors implement it. `ObVTableScanParam`'s raw `sql::` pointers (report §3) become the trait object. |
| Into observer and rootserver | 3 (3) | 297 (21) | sql-das → observer 66 symbols, rootserver → observer 40, sql-resolver → observer 38 (`ObVectorIndexUtil`, `ObInnerSQLConnectionAccess`, `ObReqTimeInfo`) | Move `ObVectorIndexUtil` from observer/vector_index to storage-search; keep inner SQL behind `ObISQLClient` (ob-share) with the connection implemented in observer. storage/ddl/ob_ddl_insert_dag.cpp:22 and ob_px_sub_coord.cpp:30-31 are fixed by the same moves. |
| Other leaves and dropped code | 85 (14) | 261 (17) | ob-share → ob-schema 47 lines, 122 symbols (share/ob_rpc_struct.h, ob_ddl_common, autoincrement using schema types) | Move the schema-using share files (DDL argument structs, autoincrement service) into ob-schema. |

The same scripts rerun on the design document's final map give the remaining list; the Step 1 exit ("an acyclic crate graph") is met when that list is empty or every entry has a named fix, and Step 2a's survey `cargo check` confirms it on Rust code.

### 5.4 Where sql-nio sits

sql-nio becomes a plain workspace crate beside ob-base, with no dependency on any seekdb crate, and observer uses it. Concretely:
- Its C ABI goes: the 33 `#[no_mangle]` functions become ordinary Rust functions, the 4 reverse callbacks (`ob_sql_sock_handler_on_*`, src/ffi_types.rs:53-72) become one trait that the observer crate implements, cbindgen and nio.h are removed, and the crate type becomes `rlib` only.
- row_encode.rs and response_api.rs are dropped as C-ABI files, as the plan says. Their encoding logic (MySQL row and response packets) is still needed by the Rust server; whether it is kept as Rust behind a Rust API or the C++ obmysql encoder is translated instead is a question for the developer (section 7), because the MySQL wire bytes are a judged contract (00b item 8).
- Its remaining `unsafe` (186 lines today, most at the FFI boundary) should fall to socket-level code; Decision 14 then needs sql-nio on the list of named crates or the socket code rewritten without `unsafe`.
- The workspace profile changes: `codegen-units = 1` cannot stay for the large crates (report §4), and `panic = "abort"` moves to the seekdb binary's profile.

### 5.5 Islands, and the prior art on the C++/Rust boundary

Each island is one `-sys` crate that builds its C or C++ with a build script (cc or cmake crate) and exposes a safe wrapper; all its `unsafe` stays inside (Decision 14). The kept C++ oblib subset under share/geo and S2 lives inside geo-sys, which is why S2 and share/geo share a crate. The seekdb binary is linked by cargo, so the report's "only one Rust staticlib can go into seekdb" problem (evidence: cmake/Rust.cmake builds only `--package sql-nio`) disappears.

The earlier piece-by-piece port's conventions (abi-naming.md, notes/ffi-mechanics.md), adopted or rejected for the islands:

| Convention | Decision | Reason |
|---|---|---|
| Split each boundary crate in two layers: safe logic, and a thin `extern "C"` adapter holding all `unsafe` | adopt | It is the shape Decision 14 needs for the island crates. |
| Named `extern "C"` symbols with a short module tag; the receiver as the first argument; no global state | adopt | Island callbacks (parser memory and charset, vsag allocator and logger, geo callbacks) need a receiver, and Decision 8's guideline rules out process-wide state in engine crates. |
| No function-pointer tables for callbacks | adopt | The same reasons as in the note: signature drift becomes a compile error. vsag's C++ `Allocator`/`Logger` subclasses call named Rust symbols. |
| Header generated by cbindgen from Rust, checked in, with a CI diff check | adopt for symbols Rust implements and C++ calls | For the island entry points the C/C++ side is the source of truth: hand-written C header, Rust declarations in one file (bindgen only for third-party headers, as the note itself says). |
| Reverse declarations kept in one file per crate | adopt | Keeps each island's ABI reviewable in one place. |
| `shim` marker for transitional scaffolding, "shim count reaches zero" as the exit | reject | A parallel tree with one cutover has no transitional seams; the islands are kept for the first parity gate on purpose (Decision 13). |
| The `nio` tag and nio.h | reject | sql-nio loses its C ABI (5.4). |
| C++ callers guarantee pointer and length validity; no checks in the adapter | adopt for islands, with the one kept rule | The callers are our own crates; still use `&[]` for length 0, since C++ passes empty `ObString` as null plus 0. |

## 6. Risks

- **The cut list is from C++.** 1,053 include lines and 2,929 symbols is what the C++ needs; the Rust core's own API can add upward needs. Step 2a's full pass and survey `cargo check` is the check; its cross-crate cycle errors are the measure.
- **Link counts are approximate** for 30 unity objects whose members span crates (section 1.3); macro-generated definitions fall back to the majority crate.
- **ob-schema's generated code.** The inner-table generator writes 102,481 lines of C++ today; its Rust output inside ob-schema could make that crate the slowest to check. If Step 2a measures it above the 60 s line, move the generated part into its own crate.
- **storage-tablet and sql-expr sit at the cap** (174.7K and 171.8K); both have a split ready (5.1).
- **The narrow run's schema path.** If the schema cannot be kept in memory for the narrow run without inner SQL, Decision 10's switch condition is likely to fire; designing that path is part of the core, not of Step 2a.

## 7. Questions for the developer

1. **Core scope.** Build the narrow core plus the schema object model and the storage entry points for Step 2a (about 500K lines), adding the sstable formats in the core build, rather than either of the report's two sizes?
2. **Standby.** src/standby (14.7K) is built by default (`option(OB_ENABLE_STANDBY ... ON)`, CMakeLists.txt:29) and needs gRPC. Keep it as a crate for the first release, or leave it out until after parity?
3. **Bison output.** Generate the parser's C at build time (every build machine, including the later Windows and Android ones, needs bison 2.4.1, src/sql/parser/gen_parser.sh) or check the generated C (about 175K lines) into sql-parser-sys?
4. **sql-nio's encoders.** Keep row_encode.rs and response_api.rs's logic as Rust behind a Rust API, or translate the C++ obmysql encoder and delete them outright?

## 8. How to reproduce

```
cd migration/design/evidence/01-crates
python3 -B incgraph.py            # file, header, directory and module graphs -> out/
python3 -B incgraph.py --raw      # forwarding headers not followed (the 110-directory cycle)
python3 -B incgraph.py --home     # api headers moved to their implementing directory
python3 -B crate_analysis.py crates.tsv order.txt   # crate sizes and upward include lines
python3 -B linkgraph.py crates.tsv order.txt        # upward symbol references (reads nm/all.txt)
```
nm/all.txt was produced by `nm -m -g` over every `.o` under /Users/colin/seekdb-dev/ref-834bbee1e/build_release/src, keeping external and undefined lines and dropping weak private ones.
