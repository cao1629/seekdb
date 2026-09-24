# seekdb from C++ to Rust: feasibility report (Step 0)

## 1. The six steps

1. **Create the map and the rules:** a dependency map orders the work, a gap inventory lists every place Rust demands what C++ left implicit, and a rulebook settles each translation question once.
2. **Stress-test the rules:** a two-translator bakeoff and a pilot on a few hard files; only rule changes survive.
3. **Translate everything:** an implementer, two adversarial reviewers and a fixer per unit, fanned out over a queue on disk, with no compiler in the loop.
4. **Compile:** one survey build turns the error list into a queue sliced by module, and fixers work without compiler access.
5. **Run it:** hello world, then smoke tests.
6. **Match behavior:** work down the judge's failures against the old code, then clear the deferred `BUG(port)` / `TODO(port)` / `PERF(port)` markers.

Before Step 1 comes judge setup (`prompts/00b-judge-setup.md`), which the kit requires whenever the existing tests cannot judge the old and new code on equal terms. That is the case here (Call 3).

---

## About this report

**What this covers:** moving all of seekdb from C++ to Rust, both the server form and the embedded form.
**Code surveyed:** worktree `/Users/colin/seekdb-dev/migrate-to-rust`, branch `migrate-to-rust` on the fork `cao1629/seekdb`, HEAD `076eb309b`. Upstream is `oceanbase/seekdb` (the `origin` remote).
**How it was surveyed:** read-only. No edits, no test runs, and one timing build.
**Date:** 2026-09-24.
**Who would do the work:** one developer with Claude Code on one Mac (Apple M4 Pro, 14 cores, 24 GiB RAM, 52 GiB free disk measured with `df`).
**Rubric:** `code-migration-kit-with-claude-code/prompts/00-feasibility.md`.

Labels used throughout:
- "Assumption" marks a figure that is not a measurement or a count from this repo.
- Case-study numbers from the kit README are not used as estimates.

Contents:
1. The six steps
2. The case for leaving C++
3. Call 1: keep the structure or redesign?
4. Call 2: what verification costs
5. Call 3: do the tests survive, and is there a judge?
6. The six steps for seekdb
7. Cost and duration
8. Model plan
9. Verdict
10. Where the analysts disagree
11. Decisions for the developer
12. What was read and what was run

---

## 2. The case for leaving C++

There is a real case for leaving, but it is moderate, and nearly all of it sits in one place. Memory owned by an arena, or held as a borrowed view, gets handed to work that runs later or on another thread.

### Memory bugs in this code over the last year

| Commit | What went wrong | Where | What Rust would do |
|---|---|---|---|
| bc31e03f4 / b4ad89151 (2026-06-22/23) | PX SQC serialize buffer, captured by an async closure, used after free. Open issue #920 is on the same `dispatch_sqcs` path. | src/sql/engine/px/ob_dfo_scheduler.cpp | Reject it: the `Send + 'static` bound on spawned work |
| eda242c9a (2026-06-22) | Request-arena `index_schema_` used by an async DDL task. The same commit made a plain `done_` flag atomic. | src/share/ob_rpc_struct.h | Reject it: lifetime bound; `Atomic*` field type |
| 9d24b8807, 9bb15300f (2025-12-04) | Shallow `ObString` copies went into an embedding task, and the task was freed while still queued. The fix is a hand-written refcount ending in `this->~ObEmbeddingTask(); ob_free(this)`. | src/query/vector/ob_vector_embedding_handler.cpp:919-929 | `Arc<Task>`, with owned bytes |
| dd899675c (2026-08-23) | Schema arena reset while a fallback schema was being built from it | src/share/schema/ob_multi_version_schema_service.cpp | Borrow tied to the arena |
| 9e2b6ba16 (2026-01-22) | Sstable pointers outlived the iterator that pinned them | src/storage/ddl/ob_tablet_fork_task.cpp | Reject it: borrow outlives owner |
| 62b9d1a81 (2025-11-04) | Parallel merge tasks shared one arena that is not thread-safe | src/storage/ddl/ob_ddl_independent_dag.cpp | Reject it: `Send`/`Sync` |
| 40fcfa901 (2026-05-18), then 328253969 (2026-05-26) | Worker used after free plus an unlocked insert into an intrusive list, then a double free | src/oblib/lib/thread/ob_simple_thread_pool.ipp:162 today | `Mutex<List>`; owned `JoinHandle` |
| e66776e2d / 6da784ac1 (2026-08-19/20), found by Sanity | Plan-cache object read after unlock; PS-cache callback followed a dangling `ps_item_`; `databuff_printf` given the wrong length | src/sql/plan_cache/ob_plan_cache.cpp, ob_ps_cache_callback.h, src/observer/virtual_table/ob_table_columns.cpp:603 | Lock guard owns the data; bounds-checked slices |
| 5aa611c00, b1587c030 (2026-04) | A comparator in an error state broke strict weak ordering, so `std::sort` read out of bounds. 31 comparators in 18 files keep an error code in a member. | src/sql/engine/sort/ob_sort_op_impl.cpp:395-420 | Rust sorts stay memory-safe, though they may panic |
| a44b2526f, 1e4f589b8, fe79a0dd6, 65997991e | Wrong `static_cast`; uninitialized frame memory read as pointers; negative modulo index; an MDS deep copy returned success with a null copy, so replay hit SIGSEGV | das, expr, change_stream, MDS | Enums, `MaybeUninit`, bounds checks, `Result<Box<_>>` |

### Defensive code, and checks that don't protect

- **Defensive checks.** There are 35,376 `OB_ISNULL` and 27,379 `ret = OB_ERR_UNEXPECTED`. There are also 4,392 `OB_NOT_INIT` plus 943 `OB_INIT_TWICE`, guarding 1,664 two-phase `init()` methods.
- **Array bounds.** `ObArray::operator[]` logs an out-of-range index and then reads anyway (src/oblib/lib/container/ob_array.h:323-335).
- **Asserts and warnings.** `OB_ASSERT` is compiled out, because the Bazel build defines `NDEBUG` (bazel/seekdb_build_config.bzl:86,135). All C/C++ is built with `-Wno-everything` and `-fno-strict-aliasing` (same file, lines 32-37 and 66-69).
- **No race checking:**
  - Shared fields are plain integers changed through 3,051 `ATOMIC_*` macro uses, against 26 uses of `std::atomic`.
  - The tracked build has no ASan, TSan or UBSan configuration.
  - The only memory checker, Sanity, needs internal devtools (CMakeLists.txt:232-239).
- **The public tests caught none of the crashes above.** Sanity or internal suites found them, and the fixes added no mysqltest case.

### What weighs against leaving

- **Few memory fixes.** About 17-20 fix commits in a year are memory-safety bugs that safe Rust would rule out; the verifier puts the upper bound at 17-18. That is about 4% of fix commits.
- **The crash tracker was a burst, not a stream.** 65 of about 90 crash issues were filed in March-April 2026, and 51 of those name Windows, macOS or Android. After that the monthly counts were 7, 5, 2 and 0 (June to September).
- **The bugs cluster right after the team's own large refactors:**
  - The adaptive worker pool (91f80b549) was followed by 40fcfa901 and 328253969.
  - The RPC framework removal (ee560bb45, 1,142 files) was followed by the deadlock fix cb5a24ce5, then bc31e03f4 and eda242c9a.
  - A rewrite is the largest refactor there is.
- **Much of the bug stream is not a language problem:**
  - In the last 30 days, 9-11 of the ordinary src/sql commits were SQLancer-found logic fixes in src/sql/optimizer and src/sql/rewrite.
  - Rust does not remove deadlocks, hangs (47 issue titles), stack overflows, or logical leaks into long-lived arenas (38054020d).
  - Nor does it remove bugs inside hand-written reclamation. 54123f13e, a KV-cache hazard-version use-after-free, sits in code that would still be `unsafe`.
- **Rust brings costs this repo does not pay today:**
  - An explicit out-of-memory policy.
  - Compile time for generic code. e2b7c7a8f shows the same cost in C++: 455 s cut to 53 s by hand.
  - Crash and memory-accounting tooling that is still needed.
- **FFI gives back safety.** rust/sql-nio has 186 `unsafe` in 8,448 lines, and 123 of them are in its two C-ABI files (row_encode.rs 70, response_api.rs 53).

### Conclusion

The payoff exists only if three things are redesigned: how arena memory is handed to other threads, the borrowed views, and the hand-counted handles. A line-for-line port keeps all of these:
- `ObIAllocator&` in 2,498 files;
- raw `ObString` views (about 17.7K mentions);
- 5,938 raw-pointer fields;
- 2,741 placement-new lines.

With them it keeps the same bug classes, now behind `unsafe`. The case is strong enough to justify building a judge and a pilot now. On its own it does not justify the full cost in section 7.

---

## 3. Call 1: keep the structure or redesign?

The answer is a split:
- Redesign the ownership core by hand.
- Translate everything else file by file against the new core.
- Keep bit-for-bit the algorithms and code that produce text the judge compares.
- Keep five pieces as C++ behind a C ABI.

### What forces a redesign

Every core research area and every verifier reached this independently.

- **Arena ownership:**
  - `ObIAllocator` appears in 2,498 of about 6,644 code files (src/oblib/lib/alloc/ob_iallocator.h).
  - There are 2,741 placement-new lines and 1,274 explicit destructor calls.
  - `ObString`, `ObDatum` and `ObObj` are views with no lifetime (ob_string.h:203-211, ob_datum.h:106-131).
  - `ObArray` binds to the thread-local `CURRENT_CONTEXT` arena on its first allocation (lib/rc/context.h:46-67; ob_array.h:484-492).
- **Global state:**
  - `server_service<T>()` appears on 1,222 lines over 122 types. The slots are bound and unbound at runtime as raw pointers (src/share/rc/ob_server_runtime.h:121-160).
  - `GCTX` has 892 uses and `get_instance()` about 620-710.
- **The statement IR:**
  - `ObRawExpr` and `ObDMLStmt` are edited in place through 488-740 `ObRawExpr*&` parameters.
  - Identity is by pointer: `find_item` "compares pointers to decide whether two expressions are the same" (src/sql/optimizer/ob_optimizer_util.h:265-287, about 580 call lines).
  - Statements and expressions form cycles through `ref_stmt_` and `outer_expr_`.
  - resolver/dml and resolver/expr must move together, about 81K lines.
- **Execution frames:**
  - Frames are untyped per-query memory at offsets fixed by the code generator, with `ObDatum` pointers back into the frames (ob_expr.h:367,436).
  - Raw function pointers are serialized as addresses.
  - Storage writes into those frames and runs engine filters: 1,069 `sql::` lines in 98 storage files, and `ObExpr::eval` is `OB_INLINE` at ob_expr.h:1039.
  - `ObVTableScanParam` carries `sql::ExprFixedArray*`, `ObPushdownOperator*` and `ObPushdownFilterExecutor*` into storage (src/data_plane/api/data_plane/access/ob_tablet_scan.h:442-448).
  - 66 of 156 operator files touch frame internals.
- **Tablets and handles:**
  - Tablets deserialize into their own pool buffers and point into themselves (src/storage/tablet/ob_tablet.cpp:1663-1815).
  - Which tablet to evict ("wash") is chosen by `type_info` (ob_storage_meta_mem_mgr.cpp:2137-2200).
  - Hand-rolled handles appear on 1,122 lines in 186 files.
- **Transaction core:**
  - `ObTxCtx` holds `ObMemtableCtx` by value (ob_tx_ctx.h:598) and keeps an intrusive list of callbacks that hold raw back-pointers (ob_tx_ctx.h:633).
  - The apply service reaches those callbacks through `CONTAINER_OF` (src/logservice/ob_append_callback.cpp:25).
  - Submit order is enforced with `ob_abort` (ob_tx_ctx.cpp:1586-1636).
  - MDS (the multi-data-source framework) is X-macros included at 11 sites.
- **Schema objects:**
  - They are owned by arenas and hold raw pointer arrays and `error_ret_` (ob_schema_struct.h:1506-1560).
  - Guards return `const T*&` while collecting cache handles (ob_schema_getter_guard.h:252-268).
  - Versions live in ref-counted slots on rotating arenas (ob_schema_mgr_cache.h:65-95).
- **Link-level cycles** that Cargo crates cannot have: sql↔storage (271/100 symbols), pl↔sql (382/156), observer↔sql (427/131).

A literal port of this compiles only as `unsafe` Rust throughout.

### What must stay the same, to the bit

The judge compares exact text, so these must not change:
- **Plan text and cost numbers:**
  - 568 plan tables pin EST.ROWS and EST.TIME, which are `ceil()` of doubles (src/sql/optimizer/ob_logical_operator.cpp:1189-1192; ob_opt_est_cost_model.cpp).
  - 4,005 `output(` lines pin the expression printer and implicit casts (369 contain `cast(`).
  - Query-block names are printed as `%08X` hashes (src/sql/resolver/dml/ob_sql_hint.cpp:389).
  - There are 3,598 `rowset=` lines.
  - EST.ROWS also depends on storage estimation (ob_access_path_estimation.cpp:165; src/storage/access/ob_table_estimator.cpp) and on freeze state.
- **Row order:**
  - For ties under ORDER BY, the SORT operator's own algorithms decide the order (ObAdaptiveQS, radix, and `std::sort` in ob_sort_op_impl.cpp:77-260). There are about 169 `lib::ob_sort` call lines wrapping `std::sort` (src/oblib/lib/utility/ob_sort.h:28-41).
  - 10,107 of 11,908 SELECTs have no ORDER BY. About 7,500 of those are single-table and follow storage or PX scan order. About 300 depend on hash output order.
- **Number formatting.** 770,711 of the 912,659 configured result lines come from four arithmetic matrices (ObNumber in ob_number_v2.cpp; MySQL's dtoa port in ob_dtoa.cc).
- **Errors and hooks:**
  - The error catalog: 1,546 entries in src/share/ob_errno.def with their message text, and 2,688 `--error` directives.
  - DEBUG_SYNC point names: 7 configured cases use them, and FORK_TABLE_BUILD_DATA alone has 46 uses.
  - 322 numbered tracepoints, of which init.sql sets 12.
  - 289 parameter names, pinned by all_virtual_sys_parameter_stat.result.

Six generators carry these contracts: gen_errno.pl, gen_ob_sys_variables.py, generate_inner_table_schema.py, syspack_codegen.py, gen_str_datum_func_parts.py and gen_expr_str_cmp_func.py. Point them at Rust output. Do not translate what they currently produce, which is about 87K checked-in lines plus about 104K inner-table lines generated at build time.

### Where a faithful translation still changes behavior silently

Each of these needs a rulebook entry and inventory rows before any fan-out.

1. **Sorting.** Rust's `sort_unstable` is a different algorithm from `std::sort`. Per the Rust 1.81 release notes (not checked here), a Rust sort may panic when the comparator is not a total order.
2. **Hashing.**
   - The default `HashMap` is seeded randomly per process.
   - OB hash iteration order reaches output at ob_join_order.cpp:16662/16727, ob_range_generator.cpp:1799 and ob_key_part.cpp:289/305.
3. **Floating point** (assumption from compiler defaults; not measured).
   - clang fuses `a*b+c` into one FMA instruction by default wherever the target has it, which on arm64 is everywhere.
   - rustc never does this.
   - So the same source can differ in the last bit and cross a `ceil()` boundary.
   - Only the ob_sql_simd and ob_storage_simd targets set `-mfma` on x86 (src/sql/CMakeLists.txt:13-14).
4. **Integer overflow.** It panics in Rust debug builds.
5. **Error codes used as values.**
   - There are 4,189 `OB_X == ret` lines, 2,905 `ret = OB_SUCCESS;` resets, 17,767 `OB_SUCC(ret) &&` guards and about 1,560 `tmp_ret`/`OB_TMP_FAIL` lines.
   - About 6,000 uses treat a code as a normal outcome (`OB_ITER_END`, `OB_ENTRY_NOT_EXIST`, `OB_EAGAIN`).
   - `?` is the wrong mapping for all of these.
6. **Deep recursion.** `SMART_CALL` (1,138 sites) switches to a new stack, or returns `OB_SIZE_OVERFLOW`. A Rust stack overflow aborts the process.
7. **The parser's error exit.** The parser exits through `longjmp` from a thread-local `jmp_buf` (src/sql/parser/sql_parser_base.c:104-106). No Rust frame may sit between the parse entry and a callback.
8. **Plan caching can be lost with nothing showing.** If a new parser disagrees with the fast parser about constants, src/sql/ob_sql.cpp:3327-3350 re-parses the statement and skips the plan cache. No text comparison catches this.

### The core, defined

"Core" in this report means the code redesigned by hand, measured by the end-state analyst:

| Part | Directories | Lines |
|---|---|---|
| Foundation substrate | src/oblib/lib/{alloc,allocator,container,hash,string,rc,lock,atomic,list,queue} | 57,762 |
| Value and datum types | datum, object, rc | 17,744 |
| Statement IR | src/sql/resolver/expr plus the DML statement classes | 42,825 |
| Execution framework | query/api engine, operator factory, exec context, physical plan, `ObExpr`, frame info, pushdown filter, code generator | 43,808 |
| Storage and transaction core | tablet, meta_mem, memtable, tx, multi_data_source, tx_table, data_plane access | 137,103 |
| **Total (narrow scope)** | | **about 299K** |

A wider scope comes to about 550-600K lines (the risk analyst's estimate; not re-measured). It adds:
- the schema object model (share/schema plus observer/schema, about 88K);
- the sstable formats in storage/blocksstable (87.6K);
- the rest of resolver/dml;
- all of the code generator.

### What the redesigned core looks like (outline for the design document)

- **Process.** Rust owns `main`, the threads, one jemalloc as `#[global_allocator]`, and `panic=abort`. General allocation aborts on out-of-memory, and typed budget errors stay at the named budget owners (Decision 6).
- **Crates.**
  - 20-40 crates in an acyclic graph, each at most about 100-180K lines.
  - Seed them from the Bazel header-level graph, which is already acyclic: 179 `cc_library` targets, plus the 79 oblib and 116 share targets referenced by src/sql/sql_runtime_group_deps.bzl.
  - Do not seed them from the 12 module roots.
- **Foundation:**
  - One error type that carries the exact OB code, generated from ob_errno.def.
  - Per-statement bump arenas borrowed as `&'q`.
  - Owned bytes or `Arc<[u8]>` for anything sent to another thread or put in a cache. This is the fix for the bug table in section 2.
  - `Atomic*` field types in place of `ATOMIC_*` on plain fields (about 823 field names).
  - `Mutex<T>` that owns the data it guards.
  - `Arc` with a custom drop for pooled handles.
  - An explicit server context in place of the `server_service<T>` slots and `GCTX`.
- **Statement IR.**
  - Arenas of typed ids, so "same object" becomes "same id". This keeps what `find_item` means.
  - Rewrites produce new ids.
  - Explicit work stacks instead of `SMART_CALL`.
- **Execution.**
  - The plan is an immutable `Send + Sync` value. Today it is already a shared cache object (ob_physical_plan.h:60), but 16 `const_cast`s write into it.
  - Per-run state lives in typed column batches owned by operators.
  - Dispatch goes through enums or typed tables.
- **sql/storage boundary.** Storage, the lower crate, defines the column batch and a filter/aggregate trait, and SQL implements it. This change is what lets sql and storage be separate crates instead of one 1.83M-line crate.
- **Storage and transactions:**
  - Immutable `Arc` tablet snapshots.
  - Epoch reclamation from a vetted crate, instead of QClock and the retire station.
  - An `Arc` transaction context whose callbacks complete in sequence-number order.
  - MDS as an enum plus a trait.
  - New explicit little-endian on-disk formats under a bumped data version (Decision 3).
- **`unsafe`** only in named crates (Decision 8).

**What keeps its structure against the new API:**
- the control flow of the 61 operators;
- the 528 expression bodies;
- the 89 resolvers;
- the 34 rewrite rules and the cost formulas;
- the 146 virtual tables;
- `ObDDLService` (381 methods);
- the PL interpreter (1,196 lines);
- the value libraries (ObNumber, time, JSON, and the 3 live collations);
- config.

The error convention becomes `Result` and `?`, with the exceptions listed above.

**What is dropped or regenerated rather than translated:**
- the obmalloc backend (26 files, 8,490 lines);
- lib/codec (32,109 lines, with no callers outside itself);
- the timezone tables (38,963 lines, never read);
- tzcode and ob_ctype_uca.cc (both dead);
- vendored zstd and xxhash, which become crates;
- protobuf/gRPC, which become prost/tonic;
- the 276,043-line IK dictionary, which becomes a data file;
- about 30.9K lines of other dead files.

### The C++ that stays, behind a C ABI

| Kept in C++ | What crosses the boundary | Why it stays |
|---|---|---|
| vsag (vector index library) | 24 functions over `void*` handles. Callbacks: 2 `vsag::Allocator` subclasses, `vsag::Logger` (7 virtuals), a filter, and 2 `std::streambuf` bridges. | Snapshots are persisted and the format is pinned (50306fbfd) |
| S2 | About 9 adapter functions, plus `get_cellid_mbr_from_geom` and the geographic MBR test | Cell ids are persisted in spatial-index rows |
| share/geo with boost.geometry (47.8K lines) | A new bytes-in/bytes-out kernel API of about 76 entry points and about 10 callbacks. It covers casts, range and S2 covering, MBR, SRS and MVT, not only the ST_ functions. 136 files outside share/geo reference it. The ST_ kernels have to be carved out of 14.9K lines of `ObExpr`-based code, which has 199 user-error sites. | 42 geometry tests with 2,834 ERROR lines pin boost's numbers. In the wasm engine, 3.49 of the 3.79 MB of boost code is template instances over seekdb's own types. |
| ICU regex | 15 C functions from one 954-line file | No Rust regex crate matches ICU's semantics |
| The bison SQL parser, at first | The `ParseNode` C struct (206 files, 2,610 references), plus the C++ layer `ObParser` (28 users), `ObFastParser` (2.5K lines) and `ObPLParser` (9 users) | The fast parser's constant counts must match exactly (see item 8 above) |

**Costs every island keeps:**
- a `try/catch` at every `extern "C"` entry (ob_geo_dispatcher.h:1314-1363 maps 18 exception types);
- the malloc hook, so that island allocations are still charged (malloc_hook.cpp:143-163);
- OpenSSL, which libs2.a needs for its `BN_*` symbols, as do curl and gRPC;
- on wasm, Emscripten's JavaScript exception model, and about 28.7 MB of ICU data, which is a third of the 88 MB engine.

### What this means for the kit

This is a redesign, so the README's "If you're redesigning" section applies:
- The rulebook becomes a design document.
- The bakeoff is invalid for the core. Use adversarial review of the design plus disposable runs instead.
- The core's unit of work is a subsystem.
- For the leaf code, the bakeoff becomes valid again once the core API is frozen.

**Committed line:** Redesign the ownership core by hand: about 300K lines at the narrowest, up to about 600K counting the schema model and sstable formats. Translate the remaining 2,600-3,100 stems (2,800-3,500 units once the 54 oversize files are split) file by file against that core, keeping their algorithms and every function that produces text the judge compares. Keep vsag, S2, share/geo with boost.geometry, ICU regex and the bison parser in C++.
- Redesign forced by: src/oblib/lib/alloc/ob_iallocator.h, src/oblib/lib/rc/context.h, src/oblib/lib/string/ob_string.h, src/oblib/common/datum/ob_datum.h, src/share/rc/ob_server_runtime.h, src/query/api/query/engine/expr/ob_expr.h, src/sql/resolver/expr/ob_raw_expr.h, src/sql/resolver/dml/ob_dml_stmt.h, src/data_plane/api/data_plane/access/ob_tablet_scan.h, src/storage/tablet/ob_tablet.cpp, src/storage/tx/ob_tx_ctx.h, src/share/schema/ob_schema_getter_guard.h.
- Bit-exact preservation forced by: src/sql/optimizer/ob_opt_est_cost_model.cpp, src/sql/optimizer/ob_logical_operator.cpp, src/sql/engine/sort/ob_sort_op_impl.cpp, src/oblib/lib/utility/ob_sort.h, src/sql/resolver/dml/ob_sql_hint.cpp, src/sql/printer/ob_raw_expr_printer.cpp, src/oblib/common/number/ob_number_v2.cpp, src/oblib/lib/charset/ob_dtoa.cc, src/share/ob_errno.def.
- Kept in C++ because of: src/share/geo/ob_geo_bin_traits.h, src/share/geo/ob_geo_dispatcher.h, src/oblib/lib/vector/ob_vsag_adaptor.cpp, src/share/geo/ob_s2adapter.cpp, src/sql/engine/expr/ob_expr_regexp_context.cpp, src/sql/parser/sql_parser_mysql_mode.y.

---

## 4. Call 2: what verification costs

### Baseline measured on this Mac

Conditions: 2026-09-24, fresh worktree, RelWithDebInfo, unity build, no ccache, nothing else running.

| What | Time |
|---|---|
| Clean `build.sh release --make` (324 C++ units, mostly unity groups, plus 40 C units) | 314 s: about 8 s configure and 306 s make |
| No-op rebuild | 1 s |
| One edited .cpp outside a unity group | 9 s |
| One edited file inside a unity group | 37 s: the 46,590-line group recompiles in 32 s, then the 193 MB binary relinks in about 1.2 s |
| `cargo check` of the Rust workspace | 7 s |
| `cargo build --release -p sql-nio` | 17 s |

There is no separate C++ typecheck step, because the build is the typecheck. The measured binary is macOS arm64, while CI tests Linux x86_64.

**CI** (32 cores, ccache):
- The compile step median is 223 s; a true cold cache takes 556 s of make plus 258 s of dependency init.
- The four mysqltest slices have medians of 574, 374, 447 and 370 s. That is about 29 min of serial time for the 272 configured cases, or about 6.5 s per case.
- A successful run takes about 19 min of wall time.

**The judge on this Mac has not been measured at HEAD.** The seekdb-dev notes claim 273 of 280 cases passed at a48096008 in about 27 min, with 3 known macOS failures. But the run records they cite (/Users/colin/seekdb-dev/mysqltest-runs) are gone. The worktree's runner also does not pass `--nodaemon` in `prepare_instance`, even though sdb.py:127-128 already accepts the flag; the main checkout carries that one-line fix uncommitted.

### Scaling assumption for Rust

All of these are assumptions, uncertain by about 5x.

- **Check rate.**
  - `cargo check` runs at about 3,000 lines of own code per second per core, with a range of about 500-7,000.
  - Public data spreads 5-10x:
    - TiKV's clippy took about 95 s for about 0.73M lines on 3 warm vCPUs;
    - GreptimeDB took 425-886 s with warm dependencies;
    - Databend's release build took 30m37s even with 99% cache hits.
- **One thread per crate.** On stable rustc 1.98.1 (rust/rust-toolchain.toml), each crate is type-checked on one thread; `-Zthreads` is nightly-only.
- **Rust lines.** Expect about 0.8-1.5x the translated C++, so about 1.8-3.4M Rust lines. Headers are 26% of src lines and have no Rust counterpart. Bun's Zig-to-Rust port reported 1.46x; that is external data, not an estimate for this repo.
- **Crate budget.**
  - At most about 100-180K lines per crate (honest range 30-250K), which means 20-40 crates.
  - src/sql (983,706 lines) and src/storage (760,249, or 484,206 without the dictionary) must each split into several crates. That requires breaking the sql/storage link cycle first.
- **Release profile.** rust/Cargo.toml `[profile.release]` sets `codegen-units=1`, `lto=thin` and `debug=true`. That setting cannot stay for large crates.
- **Machine limits (not measured).**
  - rustc's memory on a 100K+ line crate is unknown; 24 GiB may allow only 2-4 large checks at once.
  - A Rust target directory for about 2.5M lines may need 20-60 GB, against 52 GiB free.

### Estimate

| Referee | Price (assumption) | Where it sits in the loop |
|---|---|---|
| Check of one crate | 15-60 s central (range 20 s-5 min) | Run by the build daemon when a crate's batch lands |
| Edit to a foundation crate, which re-checks everything downstream | 2-4 min central (15-30 min pessimistic) | Every Step 4 round that touches shared types |
| Clean check of the whole workspace | 3-15 min | Once per Step 4 round |
| Release build (with `codegen-units` above 1) | 15-45 min, against 5.2 min for C++ | Once per Step 5 iteration |
| One pass of the 272 cases | About 27-30 min serial on this Mac (from the notes; unverified). About 10 min if 4 slices fit in RAM side by side (assumption). | Gates and nightly runs |
| The new scenario families from 00b | About 1-1.5 h more | Gates |
| Re-running one case | About 6.5 s plus an incremental build | Every Step 6 fix |

What this means for the loop:
- **The behavior referee, not the compiler, sets the pace of Step 6.** Fixes are checked by re-running only the failing cases, and full rounds run only at gates and nightly.
- **Step 4 folds into Step 3 only per crate, and only if a pilot measures a crate check at about 60 s or less.** Even then, cargo's lock on a target directory and the disk and RAM limits argue for one build daemon. By default, Step 4 stays a batched survey build run by `scripts/build_daemon.sh`.

**Committed line:** Verification is cheap per unit and expensive per behavior.
- A compile check fits inside the unit loop only if seekdb becomes 20-40 acyclic crates of at most about 100-180K lines, and the release profile drops `codegen-units=1`.
- Each behavior check costs a judge pass of about 30 minutes on this Mac, so behavior sets the loop.
- Forced by: src/sql (983,706 lines), src/storage (760,249), rust/Cargo.toml `[profile.release]`, src/sql/sql_runtime_group_deps.bzl, .github/script/seekdb/mysqltest_for_seekdb.py and tools/deploy/mysqltest_config.yaml.

---

## 5. Call 3: do the tests survive, and is there a judge?

### Tests that reach seekdb through a public surface (these survive)

| Suite | Cases | Where it runs | What it reaches |
|---|---|---|---|
| tools/deploy/mysql_test | 283 .test / 305 .result (933,203 lines). 272 are configured in tools/deploy/mysqltest_config.yaml, with 912,659 result lines. | CI, on OceanBase-internal Linux runners only | SQL over the MySQL protocol. 130 cases are plain SQL. 153 also lean on OB internals, in overlapping groups: 40 carry plan tables, 40 read `__all_*` tables, 47 use ALTER SYSTEM, 70 use sleep. |
| tools/obtest | 509 .test | Not runnable here | Needs the 92.8 MB mytest.jar cluster harness. 54 cases use tenant DDL that the grammar now rejects, so at most about 90 are single-instance. |
| tools/ob_error/test/test.sh | 1 | Local | CLI output comparison |
| seekdb-bindings lib/tests | 12 gtests | Linux CI only | Spawns `seekdb --embedded --nodaemon` and uses `mysql_real_query` over run/sql.sock. 4 cases build 104-107-character socket paths, which exceed macOS's 104-byte `sun_path`, so they will likely fail on this Mac. |
| seekdb-async tests | 7 tokio tests | Ran on this Mac against an old downloaded runtime | The same process contract, from Rust |
| shell-e2e | 68 Playwright tests | This Mac, run by hand | The wasm browser shell. All run on `memory://` storage, so none touches OPFS. |
| webassembly-shell unittest/wasm | 4 engine-driving Node scripts | Branch only | The JS Database API. tools/wasm/mysqltest-bridge.mjs can drive the 272 cases against the wasm engine but is not wired into anything. |
| origin/feat/embedded-mode (unmerged) | 61 C++ cases, 5 x 15 language cases, 11 Java | Branch CI | A 93-function in-process C ABI |

Three examples:
1. **tools/deploy/mysql_test/t/join_basic.test.** 69 lines of plain SQL. It survives as is.
2. **tools/deploy/mysql_test/test_suite/executor/t/basic.test.** Uses `--explain_protocol 2` and pins 152 plan tables. It survives only if the plan text, EST numbers included, stays identical or is masked by a rule set in advance.
3. **seekdb-bindings `ParameterPersistence.ChangedMemoryLimitSurvivesRestart`.** It runs ALTER SYSTEM SET memory_limit, sends SIGKILL, respawns the server and checks that the value survived. It is the only native check that parameters survive a restart.

### Tests that import internals (these die with C++)

Three examples:
1. **deps/oblib/unittest/lib/alloc/test_jemalloc_hook.cpp** (in no build).
2. **deps/oblib/unittest/lib/allocator/test_malloc_backend.cpp** (in no build).
3. **tools/ob_error/test/test_ob_error.cpp.**

These three files total 313 lines. Other tests that import internals:
- tools/module_check (about 4,260 lines of Python) and 4 bazel/probes .cpp files, which enforce the C++ layering.
- 53 unittest/wasm .cpp tests on the wasm branch, registered as 44 CTests. They link internals; for example, test_wasm_geometry links ob_s2adapter.cpp.
- The 678 unittest files deleted in 45cbbe9e1, a commit whose subject is an unrelated decimal fix. 330 .cpp/.h files of them survive on origin/release/1.4.0.
- The 3 Rust `#[test]`s in sql-nio are already Rust, but no active workflow runs them.

The public-surface count wins by a wide margin: 283 in-repo cases plus about 91 native and JS cases elsewhere, against 3 tracked internal files. But the public set is thin for 2.3M lines.

### Is it enough to judge the migration? No.

1. **An entry gate blocks any partial system:**
   - Every slice first runs tools/deploy/init.sql. It creates a PL procedure with a CONTINUE HANDLER, GET DIAGNOSTICS and PREPARE/EXECUTE, runs 12 `alter system set_tp` lines, sets hidden `_` parameters and turns recyclebin on.
   - Then init_user.sql runs ANALYZE TABLE on `__ALL_VIRTUAL_CORE_ALL_TABLE`.
   - One failed statement aborts the whole slice.
   - The set_tp lines also switch on self-checks that are active in release builds and change behavior: a forced new sort path, an NLJ group size of 4, plan regeneration from the outline, and rewrite convergence checks.
2. **The runner hides problems:**
   - A failing case gets up to 4 attempts, each on a fresh instance (`MAX_CASE_RETRIES = 3` at line 19; the loop runs `range(MAX_CASE_RETRIES + 1)` at line 502).
   - Each slice's cases share one instance in round-robin order, so state leaks between cases.
   - Trailing whitespace is ignored (lines 52-74).
   - The base dir is destroyed before every start (lines 157-194).
3. **Reach is thin.** These figures come from text matching, so they are lower bounds:

| Unit family | Registered | Reached |
|---|---|---|
| Physical operators | 61 | 36 shown in EXPLAIN, 4 inferred, 8 plausible, 13 with no evidence |
| Expression names | 527 | 207 |
| Resolver classes | 89 | 44 (plus 2 only at startup; 5 unreachable from the grammar) |
| Rewrite rules | 34 | 9 with a visible effect (lower bound) |
| System variables / parameters | 776 / 289 | 37 / about 9-12 |
| PL, XML, hybrid-search DSL | — | 14 tests define routines; 0; 0 |

4. **Whole classes are never exercised:**
   - restart and replay: 0 cases;
   - the binary prepared-statement protocol: nothing anywhere sends `COM_STMT_*`;
   - out-of-memory: 0 cases expect -4013;
   - performance: nothing automated, just one sysbench figure in prose (about 43.7k QPS at d65d43b, run through Docker);
   - concurrency: the external assets use at most 2 clients. 69 configured cases open extra connections and 6 use send/reap.
   - OPFS: only a browser page run by hand.
5. **A silent failure:** lost plan caching (see Call 1, item 8).
6. **Platform:** the .result files were recorded on Linux x86 CI, and this Mac has never produced a C++ baseline run that can still be checked.

### Build the judge first

This is a pre-Step-1 task: run `prompts/00b-judge-setup.md` between this gate and Step 1. It must produce the following, on this Mac and against a pinned C++ commit.

1. **Runner fixes:**
   - Commit the `--nodaemon` call.
   - Add a differential mode that compares C++ output with Rust output instead of with the checked-in .result files.
   - Turn retries off, and log them instead of hiding them.
   - Fix the case order and slice count, plus a second mode that starts a fresh instance per case.
2. **An entry-gate report** that runs init.sql and init_user.sql statement by statement. Also a reduced init profile that is applied to both builds. The C++ reference must be recorded again under that same reduced init. It cannot be compared with the checked-in .result files, because init.sql's tracepoints change behavior.
3. **A restart script** built on `sdb.py start` / `stop` against the same base dir.
4. **A `--ps-protocol` replay** of the 272 cases through the arm64 mysqltest in deps/3rd/u01/obclient/bin, diffed C++ against Rust.
5. **Embedded-contract tests.** Fix the bindings' socket paths and build lib/tests on macOS, then add `exec_*` prepared-statement cases to seekdb-async.
6. **A plan-cache hit counter per case**, compared between the two builds.
7. **An expression and cast differential generator.** Model it on origin/feature/rust's `rust/embedding-response/tests/compare_cpp.py`, which compares error codes and every float bit over 4,000 seeded cases.
8. **Golden bytes** for OB_UNIS, ObNumber, JSON binary and CRC32C, compared through a test-only FFI harness that never ships.
9. **Performance baselines:**
   - sysbench in Docker at 1, 16 and 64 threads;
   - cold start and restart time, read from seekdb.log;
   - judge wall time and binary size;
   - wasm start-up time, from shell-e2e/diagnostics/performance-2026-09-21/browser-integration.mjs.
10. **A coverage map from one run.** Remove the guard at CMakeLists.txt:147 in a scratch tree; main.cpp:19 already writes the profraw file.
11. **The wasm path, if wasm is in scope.** Wire mysqltest-bridge.mjs and database-browser.html in headless Chrome into one command.
12. **Validation.** Two identical clean runs on C++ with retries off, and at least 10 injected mutations, each of which the judge catches. Examples: flip a comparison in ob_opt_est_cost_model.cpp, drop an error path in ob_datum_cast.cpp, change SORT's tie handling, change ObNumber rounding, rename a DEBUG_SYNC point.

### Parity scenarios to seed 00b

1. **Differential run of the 272 cases.** Fixed order and slice count, retries off, byte-for-byte diff of C++ against Rust. A second mode uses a fresh instance per case.
2. **Entry gate.**
   - Run init.sql and init_user.sql statement by statement.
   - Run the reduced-init profile on both builds, with the C++ reference recorded under the same profile.
   - Run the 130 plain-SQL cases under that profile.
3. **Plan text.**
   - Run the 40 plan-bearing configured files exactly.
   - Separately, run them in a mode that masks EST.ROWS, EST.TIME and `rowset=`, and report that mode on its own.
4. **Row order.** Cover the ~10,100 unordered SELECTs and the ties under ORDER BY. Record whether the row order matches. Where it does not, compare as sorted sets and log the case.
5. **Expressions and casts.**
   - Test all 527 expression names, the 320 unreached ones first, against a type matrix with NULL and edge values.
   - Test both cast matrices (src/sql/engine/expr/ob_datum_cast.cpp, src/share/object/ob_obj_cast.cpp).
   - Compare the value, result type, warnings and error code.
6. **Value formats.**
   - The four arithmetic matrices (770,711 lines).
   - Golden bytes for OB_UNIS, ObNumber, JSON binary, CRC32C and a zstd round trip.
7. **Plan-cache hits.** Compare hit and miss counts per case.
8. **Binary protocol.**
   - Replay the 272 cases with `--ps-protocol`. First check that C++ against C++ is deterministic, then diff C++ against Rust.
   - Add seekdb-async `exec_*` cases.
9. **Restart after a kill.**
   - Start, load data, stop (SIGTERM becomes SIGKILL; ob_signal_handle.cpp:129-134), start again on the same base dir, then diff a fixed SELECT set and SHOW PARAMETERS.
   - Also kill the server in the middle of DML.
   - Seeds: the bindings' restart case, seekdb-async `conn_open_shares_one_handle_per_dir`, and tools/obtest/t/fork_table/fork_table_restart_recovery.test.
10. **Embedded process contract.**
    - Run the 12 bindings gtests and the 7 async tests with `SEEKDB_BIN` pointed at each build.
    - They check the argv flags, run/sql.sock, the run/seekdb.clients flock, exit when the last client leaves, `store/sstable` as the first-start marker (seekdb.c:586-605), the root@sys login, and the SHOW PARAMETERS column names.
11. **Concurrency.**
    - The 69 multi-connection cases and the 6 send/reap cases.
    - `run_two_concurrent_clients.sh` in a loop.
    - 4 slices in parallel on separate ports.
    - sysbench `oltp_point_select` and `oltp_read_write` at 1, 16 and 64 threads.
    - Compare error codes and table checksums.
12. **Memory budgets.** Drive each budget owner into failure and compare the error the client sees: -4013 from the clog allocator, -4030 memstore full, -11049 from the query tracker, -7603 from the vector limit, and -4013 from hash-join partition depth (ob_hash_join_op.cpp:837-842). Also check spilling with `ob_sql_work_area_percentage=5`.
13. **Derived data.** The geometry (42), vector_index (22) and fts_index (15) suites, compared through query results. The IK tokenizer gets a differential corpus.
14. **Performance.** Gate on declared ratios against the C++ baselines on this Mac: QPS and latency, cold start, restart, judge wall time and binary size.
15. **Data version gate and cutover.**
    - The Rust build must refuse a C++ data dir, and must refuse a v1.0.x dir that holds etc/observer.data_version.bin.
    - It must bootstrap an empty dir.
    - Export from C++ v1.4.x and import into Rust, covering VECTOR, GEOMETRY with SRID, JSON, LOBs, generated columns, and fulltext, vector and spatial index DDL.
16. **wasm, if in scope.** The 272 cases through mysqltest-bridge.mjs; the Node scripts; the OPFS reopen, interrupted-move and locked-meta.db cases in headless Chrome.
17. **The judge checks itself.** At least 10 injected C++ mutations, each of which the judge catches.

**Committed line:** The 272 mysqltest cases survive a language change but cannot judge this migration on their own, so building the judge (`prompts/00b-judge-setup.md`) comes before Step 1. Forced by:
- tools/deploy/init.sql (the PL procedure, 12 set_tp lines and hidden parameters);
- .github/script/seekdb/mysqltest_for_seekdb.py:19,157-194,490-518 (retries, one shared instance per slice, the base dir destroyed before every start);
- tools/deploy/mysqltest_config.yaml (272 cases: 0 restarts, 0 COM_STMT, 0 OOM);
- src/observer/ob_signal_handle.cpp:129-134 (every stop is a kill);
- seekdb-bindings/lib/src/seekdb.c (`mysql_real_query` only).

---

## 6. The six steps for seekdb

The recommended path is a parallel Rust tree, with a hand-designed core that a disposable run proves first. The rest is fanned out, and there is one cutover. Only seams that are already C-like cross into the C++ binary: sql-nio, the parser and the islands. Decision 2 holds the alternative, and the condition under which to switch to it.

### Before Step 1: build the judge (`prompts/00b-judge-setup.md`)

- **Placeholders:**
  - `[target language]` = Rust.
  - `[reviewer model]` = one Claude Fable 5.1 and one Claude Opus 5.5, in separate contexts.
- **Units:** about 12 harness tasks (listed in Call 3) and 17 scenario families.
- **Also in this phase** (not kit steps):
  - the coverage run and the performance baselines;
  - time `cargo check` and measure its memory on an existing 150-200K-line Rust crate on this Mac, to pin the check rate;
  - free about 150 GB of disk;
  - merge the wasm branch's race fixes into C++ (ob_atomic_list.h; ob_ringbuf_log_writer.cpp in 0be96de70).
- **Exit:**
  - N/N passing twice on the pinned C++ with retries off;
  - every injected mutation caught;
  - every family built;
  - flaky cases quarantined, each with a stated reason.
- **Cost:**
  - Harness: 12-40 tasks x 3 agent runs x 0.15-0.6M tokens = 5-72M.
  - Corpus: 5,000-20,000 generated statements ÷ 50 per batch = 100-400 batches x 2 agent runs x 0.15-0.6M = 30-480M.
  - Total: roughly 0.04-0.55B harness-counted tokens (section 7 defines the counters).
  - Machine time: a few hours of judge and coverage runs.

### Step 1: create the map and the rules (`prompts/01`, `prompts/02`, `templates/RULEBOOK.md`)

**The design document.** Because this is a redesign, the rulebook becomes a design document, using templates/RULEBOOK.md as its skeleton. It must decide:
- the crate graph;
- the error type;
- the arena and handoff rules;
- the IR ids;
- the column batch;
- the storage filter trait;
- the context struct;
- the OOM policy;
- the `unsafe` policy;
- the island ABIs;
- sort and hash determinism, FMA and overflow;
- rules for error codes used as values;
- the wasm rules (Decision 4);
- the naming rules the manifest needs.

**The map** (`prompts/01-dependency-map.md`):
- **Placeholders:**
  - `[your dependency mechanism]` = "C/C++ `#include` directives, resolving the 118 forwarding headers, plus link-level definition edges from `nm` over the built object libraries".
  - `[crate / package / module]` = "cargo crate in the 20-40-crate plan".
  - `[reviewer model]` = Opus 5.5.
- **Script.** Adapt `scripts/depmap_c.py`, and key units by class rather than by path: 53 forwarder/.cpp pairs cross groups, and the query/api and data_plane/api headers are implemented elsewhere.
- **Known cycles:**
  - 27 header cycles over 69 files;
  - one directory-level cycle of 110 directories and 4,108 files;
  - 4 top-level include lines from d51422b54.
- **Closing action:** `python3 scripts/make_manifest.py --order migration/depmap/order.txt --out migration/manifest.tsv` with `--sub` pairs taken from the design document's naming section.
- **Units:** 3,919 at a 30K-token cap, or 4,137 at a 20K cap, before removing the core and island stems.

**The inventory** (`prompts/02-gap-inventory.md`):
- **Placeholder:** `[name your gap]` = "ownership and lifetimes: arena memory handed to other threads, tasks and caches; borrowed `ObString`/`ObDatum` views; hand-counted handles. Also atomics on plain fields, error codes used as values, sort and hash order, pointer identity, integer overflow and float contraction".
- **Sweep lists:**
  - about 823 `ATOMIC_*` field names;
  - 441 `inc_ref`/`dec_ref` lines and 130 Handle classes;
  - 1,733 `const_cast` lines;
  - 4,189 `OB_X == ret` lines;
  - 2,905 resets;
  - about 1,560 `tmp_ret` lines;
  - 31 comparators that keep an error state;
  - about 40 budget-backed OOM sites plus the logical -4013 errors;
  - 122 `server_service` slot types;
  - the base classes that upper modules subclass (`ObTimerTask` 72, `ObDLinkBase` 72, `ObFuncExprOperator` 313).
- **Expected size:** about 6K-40K rows, depending on granularity (assumption).

**Exit:**
- two clean skeptic rounds for the map;
- an acyclic crate graph;
- an inventory row for every swept site;
- the design document signed off.

**Cost:**
- Inventory: 6K-40K sites ÷ 20 per batch = 300-2,000 batches x 3 runs x 0.15-0.4M = 0.14-2.4B harness-counted tokens.
- Map and design: another 0.05-0.3B.

### Step 2: stress-test the rules (`prompts/03-stress-test.md` for the leaves only)

**For the core, the bakeoff does not apply.** Two things replace it:
- adversarial review of the design document, by two reviewers in separate contexts;
- one or two disposable runs of a narrow end-to-end path, thrown away after measuring.

The narrow path runs from sql-nio, through the C parser over FFI with its tree converted to an owned Rust AST, then the new IR for simple SELECT, INSERT and CREATE TABLE, typed column batches and the storage filter trait, down to memtable-only storage.

**What the disposable run measures:**
- the `unsafe` count outside the island and FFI crates;
- the `cargo check` time per crate;
- the speed of a scan+filter+aggregate query against C++;
- tokens per unit;
- whether the reduced-init plain-SQL cases pass without system packages, inner-SQL schema loading or virtual tables. That last point is an assumption, and it is the switch condition in Decision 2.

**For the leaves** (once the core API is frozen), run 03 as written:
- **Placeholders:**
  - `[3]` = 3;
  - `[target formatter]` = rustfmt;
  - `[implementer model]` = Claude Sonnet 5;
  - `[reviewer model]` = Opus 5.5, with a Fable 5.1 diff inspector.
- **Candidates, scored by risk:**
  - a slice of ob_transform_utils.cpp (pointer identity);
  - an expression that reads session state and uses casts;
  - a slice of ob_join_order.cpp (cost floats).
- **Before this step:** the developer installs `templates/settings.json`.

**Cost:** 1-2 runs x 300-600 units x 2-4 agent runs x 0.15-0.5M = 0.09-2.4B harness-counted tokens.

### Between Step 2 and Step 3: build the core (not a kit step)

**Order:**
1. Foundation crates: error, arena, bytes, context, logging, config.
2. The IR.
3. The execution framework and code generator.
4. The storage boundary, the tablet/memtable/transaction core, and a single-writer WAL.

Fan out the value libraries (ObNumber, time, charset, JSON, casts) as soon as the foundation API is frozen, each with differential tests against C++. The gate is the plain-SQL subset under the reduced init, which grows as the core grows.

**Cost:**
- Designed and written by hand: 300-800 sessions x 0.3-1M = 0.09-0.8B harness-counted tokens.
- If agents write the core's bodies instead: 500-1,000 core units x 4.2 runs x 0.2-0.6M x 1-2 runs = 0.4-5B.

### Step 3: translate everything (`prompts/04-translation-kickoff.md`, `scripts/queue_runner.mjs`)

- **Placeholders:**
  - `[100]` = 100;
  - `[TODO(port)]` = `TODO(port)`, `PERF(port)`, `BUG(port)`;
  - `[implementer model]` = Sonnet 5, or Opus 5.5 for the groups named in section 8;
  - `[reviewer model]` = Opus 5.5.
- **What each unit gets:**
  - its stem (median 2.6K tokens, mean 6.6K);
  - a generated declaration index in place of raw headers. The one-level include context has a median of 25.6K tokens, and the full closure a median of 1.43M across all stems, so raw headers cannot be shown;
  - the design document;
  - its inventory rows.
- **Order:** the crate graph, leaves to root.
- **Excluded:** data, generated, vendored and dead files, and the islands.
- **Exit:** the queue is empty, and each file's `grep -c 'TODO(port)'` equals its trailer.
- **Cost:** 2,800-3,500 units x 4.2 agent runs (implementer, 2 reviewers, fixer, and an arbiter on about 20%) x 0.15-0.6M = 1.8-8.8B harness-counted tokens.
- **Wall clock:** limited by rate limits. About 12,000-15,000 agent runs x 10-30 min each, over 20-80 concurrent runs, comes to 2-8 weeks.

### Step 4: compile (`prompts/05-survey-build.md`, `scripts/build_daemon.sh`)

- **Placeholders:**
  - `[build command]` = `cargo check --workspace --message-format=short`;
  - `[module]` = crate;
  - `[fixer model]` = Sonnet 5;
  - `[reviewer model]` = Opus 5.5.
- **Folding into Step 3:** only per crate, and only if the pilot measured 60 s or less (see Call 2).
- **Error count:** unknown. The multiplication below uses 20K-100K as an unanchored assumption.
- **Cost:** 20K-100K errors ÷ 25 per slice = 800-4,000 slices x 3 agent runs x 0.15-0.5M = 0.36-6B harness-counted tokens.
- **Referee price:** 20-80 rounds x 3-15 min per clean check (plus cascades) = 1-20 hours of daemon time.

### Step 5: run it (no kit prompt)

- **Hello world:**
  - The Rust binary bootstraps an empty `--base-dir`.
  - sql-nio answers `select 1` on run/sql.sock.
  - init.sql and init_user.sql run statement by statement with zero errors.
- **Smoke:**
  - The 130 plain-SQL cases, run differentially: first under the reduced init, with a C++ reference recorded under the same init, then under the full init.
  - Then the bindings and seekdb-async suites.
- **Cost:** 100-500 hands-on sessions x 0.3-1M = 0.03-0.5B harness-counted tokens.

### Step 6: match behavior (the 00b judge, then `prompts/06-post-parity.md`)

- **Triage.** Run every failure on the pinned C++ build and classify it as inherited, regression or environment.
- **Done-gate:**
  - Every parity scenario passes.
  - The pinned C++ re-run shows zero inherited failures.
  - Both counts are documented and signed off by the developer, as the README requires for redesigns.
- **After the gate:** `prompts/06-post-parity.md`, with `[target tree]` = rust/ and `[reviewer model]` = Opus 5.5. Kill-only shutdown stays until then (Decision 10).
- **Cost:** 300-2,000 failure clusters (assumption) x 3 agent runs x 0.2-0.8M = 0.18-4.8B harness-counted tokens.
- **Referee price:**
  - 300-2,000 clusters x 1-3 targeted checks x 3-12 min (an incremental build plus the failing cases) = 15-1,200 machine-hours.
  - Plus 20-60 full rounds x 1.5-2.5 h = 30-150 h.

### Cutover

1. Bump `DATA_CURRENT_VERSION` (src/oblib/common/ob_version_def.h:53).
2. Move the version check ahead of the meta.db open (src/observer/ob_server.cpp:1698-1719).
3. Close the missing-file hole (src/oblib/common/ob_data_version_mgr.cpp:58-60, 88-94).
4. Test a round trip: export from C++ v1.4.x, import into Rust.
5. Print a startup message that tells users what to do. Today the bindings only return `SEEKDB_INTERNAL_ERROR`.

---

## 7. Cost and duration

### Token estimate

**Two counters are used:**
- **Harness-counted tokens** are the counter that produced this survey's 8.92M tokens over 43 subagents: about 0.21M per read-heavy agent that made about 116 tool calls. It almost certainly leaves out cache re-reads.
  - `migration/cost-log.tsv` records this counter.
  - Every per-step ceiling (Decision 14) is set in it.
- **Processed tokens** are every input token on every turn, cache reads included, plus output. This is the counter the API bill depends on.
  - Assumption: processed tokens are about 3-7 times harness-counted tokens for these agent loops.

**Per-agent-run assumption for translation.** An agent starts with a context of about 70-130K tokens:
- the design document, 30-60K;
- the stem, mean 6.6K;
- the declaration index, about 25-35K;
- its inventory rows.

It then runs 10-30 turns. That gives 0.15-0.6M harness-counted tokens, or 0.5-3M processed.

**The review topology is 4.2 agent runs per unit:** implementer, 2 reviewers, fixer, and an arbiter on about 20% of units.

| Step | Multiplication | Harness-counted | Processed (assumption: about x3-7) |
|---|---|---|---|
| 00b judge | tasks and corpus batches (section 6) | 0.04-0.55B | 0.2-3B |
| Step 1 | 300-2,000 batches x 3 x 0.15-0.4M, plus map and design | 0.2-2.7B | 1-14B |
| Step 2 | 1-2 runs x 300-600 units x 2-4 x 0.15-0.5M | 0.09-2.4B | 0.3-7B |
| Core build | 300-800 sessions x 0.3-1M (hand); or 0.4-5B if agents write the bodies | 0.09-5B | 0.6-25B |
| Step 3 | 2,800-3,500 units x 4.2 x 0.15-0.6M | 1.8-8.8B | 6-44B |
| Step 4 | 800-4,000 slices x 3 x 0.15-0.5M | 0.36-6B | 1-24B |
| Step 5 | 100-500 sessions x 0.3-1M | 0.03-0.5B | 0.2-4B |
| Step 6 | 300-2,000 clusters x 3 x 0.2-0.8M | 0.18-4.8B | 0.9-18B |
| 06 post-parity | 500-2,000 markers x 2 x 0.15-0.25M (assumption) | 0.15-1B | 0.5-5B |
| **Total** | | **about 3-30B (order 10^10)** | **about 10-140B (order 10^10 to 10^11)** |

**What drives the total:**
- Most of the multiplier is the review topology (4.2 runs per unit) and the context re-read on every turn.
- The band is wide because two inputs are unmeasured: tokens per agent run in translation, and the Step 4 error count. The Step 2 pilot measures the first.

### Dollars at list price

Prices are per million tokens:

| Model | Input / output | Cache read | 5-minute cache write |
|---|---|---|---|
| Claude Sonnet 5 | $2 / $10 | $0.20 (the generic 0.1x rate) | $2.50 (the generic 1.25x rate) |
| Claude Opus 5.5 | $4 / $20 | $0.20 (published) | $5 (derived; confirm at launch) |
| Claude Fable 5.1 | $10 / $50 | $0.25 (published) | $12.50 |

The Batch API's 50% discount does not apply to interactive agent loops.

**Blended cost per million processed tokens** (assumption: 85-92% of input is cache reads, about 3% is output; mix of Sonnet 5 implementers and fixers with Opus 5.5 reviewers):
- about $0.7 on Sonnet 5, about $1.2 on Opus 5.5 and about $2.9 on Fable 5.1;
- about $1-2 for the mix.

**Band:** roughly $10K-$300K at API list prices, most likely in the tens of thousands of dollars. Without caching, multiply by about 4-6. If the developer works on a subscription instead of the API, rate limits replace dollars as the limit.

### Machine time (the referee price)

| Step | Multiplication | Machine time |
|---|---|---|
| 00b | 2 validation passes + mutation runs + coverage run, x about 30-60 min | a few hours to a day |
| Step 4 | 20-80 rounds x 3-15 min clean check, plus cascades | 1-20 h |
| Step 5 | release/dev builds 10-45 min x tens of iterations | days |
| Step 6 | 300-2,000 clusters x 1-3 checks x 3-12 min, plus 20-60 rounds x 1.5-2.5 h | 45-1,350 h |

In Step 3 the Mac is not the limit, because no compiler runs in the loop. In Steps 4-6 it is:
- one cargo daemon;
- 24 GiB of RAM for 2-4 large checks or 2-4 judge instances;
- 52 GiB of free disk, against an assumed 20-60 GB Rust target directory plus the 6.9 GB C++ build and the judge's data directories.

### Wall clock and active attention per step

All of these are assumptions for one developer. One week = 10,080 minutes.

| Step | Wall clock | Active attention |
|---|---|---|
| Decisions (section 11) | 1-2 wk (10K-20K min) | 8-20 h (480-1,200 min) |
| 00b judge and baselines | 3-8 wk (30K-81K min) | 30-100 h (1,800-6,000 min) |
| Step 1: design document | 6-16 wk (60K-161K min) | 120-300 h (7,200-18,000 min) |
| Step 1: map and inventory (overlaps the design) | 3-6 wk (30K-60K min) | 20-40 h (1,200-2,400 min) |
| Step 2: design review, disposable core run, leaf bakeoff | 3-8 wk (30K-81K min) | 40-150 h (2,400-9,000 min) |
| Core build | 2-9 months (87K-393K min) | 200-1,200 h (12K-72K min) |
| Step 3 | 2-8 wk (20K-81K min) | 20-60 h (1,200-3,600 min), mostly about 30-40 batch gates |
| Step 4 | 2-8 wk (20K-81K min) | 20-100 h (1,200-6,000 min) |
| Step 5 | 3-10 wk (30K-101K min) | 40-250 h (2,400-15,000 min) |
| Step 6 | 2-6 months (87K-262K min) | 100-500 h (6K-30K min) |
| 06 post-parity | 2-6 wk (20K-60K min) | 20-60 h (1,200-3,600 min) |
| Cutover | 1-2 wk (10K-20K min) | 10-30 h (600-1,800 min) |

**Totals.** Steps overlap, so the calendar comes to roughly 12-24 months, with about 600-2,800 hours of active attention.
- The core build is what swings the total.
- The attention peaks in Steps 5-6, when one person debugs a system of about 2M lines.
- On top of this, upstream changes by about 8K ordinary lines a month (32 commits, +3,895/-3,944 in 30 days). Those changes have to be replayed or frozen out (Decision 11).

---

## 8. Model plan

This is the developer's decision at this gate. Once approved, these tiers become the `[model]` parameters of prompts 01-06.

| Phase | Model | Reason |
|---|---|---|
| 00b harness code and scenario scripts | Claude Opus 5.5 | The judge is the exit condition, and the volume is small |
| 00b corpus generation, mutation injection, log triage | Claude Sonnet 5 | Mechanical work, and the judge's own validation catches mistakes |
| 00b reviewers checking that no assertion was weakened | One Claude Fable 5.1 and one Opus 5.5, in separate contexts | A weak judge misleads every later step; review volume is small |
| Design document and every amendment | Fable 5.1 | One-time work; each error replicates into about 3,000-4,000 units |
| Adversarial review of the design document | Fable 5.1 and Opus 5.5, in separate contexts | The blast radius is the whole port |
| Core API design sessions with the developer | Fable 5.1 | The safety payoff is won or lost here; cache reads at $0.25/M keep long sessions affordable |
| Core module bodies (disposable and final runs) | Opus 5.5, with Fable 5.1 reviewers | Few units, each with a wide blast radius |
| 01 dependency-map script and skeptics | Opus 5.5 | The misses are subtle: forwarders, includes hidden by unity builds, declarations and code in different groups |
| 02 classifiers | Opus 5.5 for ownership rows; Sonnet 5 for mechanical families (`ATOMIC_*` fields, handle types, error-convention rows) | Ownership rows need tracing of how values flow |
| 02 skeptics | Fable 5.1 for ownership, lifetime and concurrency rows; Opus 5.5 for the rest | Those rows decide whether the redesign is correct |
| 03 translators A and B, and the pilot implementer | Sonnet 5 | They must be the production tier, or the bakeoff measures the wrong thing |
| 03 diff inspector / reviewers | Fable 5.1 / Opus 5.5 | One inspector reads everything; the volume is small |
| 04 implementers | Sonnet 5 for the bulk. Opus 5.5 for sql-optimizer, sql-rewrite, sql-resolver-expr/dml, share-schema, storage-tx-lock, storage-tablet-meta-ls, storage-access-memtable, sql-px-dtl, the two cast matrices and pl. | In those groups, mistakes in pointer identity, float cost and ownership change behavior with no error |
| 04 reviewers | Opus 5.5 at effort high. Fable 5.1 for units that print pinned text (cost model, printers, SORT, ObNumber, casts, errno text) or that touch lock-free code. | The gap is ownership, and the catches come from reviewers |
| 04 and 05 fixers | Sonnet 5 | The compiler is the real referee |
| 05 reviewers; recurring error families | Opus 5.5. Opus 5.5 drafts the amendment and Fable 5.1 writes it. | Amendments are rulebook work |
| Step 5 bootstrap debugging | Opus 5.5; Fable 5.1 for the hardest sessions | Few sessions, but each mistake is expensive |
| Step 6 triage (inherited, regression, environment) | Sonnet 5 | Mechanical: re-run on C++ and classify |
| Step 6 fixers | Opus 5.5; Fable 5.1 for plan-text and float clusters, and for judge or comparator bugs | A divergence needs reasoning over two implementations |
| 06 post-parity | Sonnet 5 fixers, Opus 5.5 reviewers | Each fix is small and proved by a parity re-run |
| Receipts (counts, slicing error lists, trailer checks) | Claude Haiku 4.5 | Needs no judgment; its 200K context is too small for translation units |
| Not used | Claude Opus 5 | Same tier as Opus 5.5 but more expensive ($5/$25 against $4/$20) |

Settings:
- Opus 5.5 defaults to effort medium, so set it explicitly: high for reviewers.
- Log every subagent's model in `migration/cost-log.tsv`.

---

## 9. Verdict

**Migrate later.**

### Start now, because it pays off even if the port never happens

- 00b: the differential judge on this Mac;
- the coverage run and the performance baselines;
- measuring the `cargo check` rate;
- the decisions in section 11;
- merging the wasm branch's race fixes into C++.

This work adds restart, PS-protocol, concurrency and performance checks that the C++ project does not have today.

### Start Step 1 only when all of these hold

1. The judge passes N/N twice on a pinned C++ commit on this Mac, with retries off. It catches every injected mutation. It has the restart, `--ps-protocol`, embedded-contract, plan-cache and expression-differential families. Its coverage map and performance baselines are recorded.
2. Decisions 1-8 are made.
3. The C++ base is frozen at a named commit, and each parallel branch has been merged, reconciled or ruled out (Decision 12).
4. The disposable core run (Step 2) meets all of these:
   - It passes the plain-SQL cases under the reduced init.
   - It keeps `unsafe` inside the island and FFI crates.
   - It checks each crate in about 60 s or less.
   - It stays within the speed ratio the developer sets against C++ on a scan+filter+aggregate query (proposal: 1.2x).
   - Its tokens per unit fall inside the bands above.
5. At least about 150 GB of disk is free.

### When the answer becomes "don't migrate the whole"

The answer becomes "don't migrate the whole of seekdb" in either of two cases:
- condition 4 fails, because the core cannot be made safe without broad `unsafe`, or it is clearly slower;
- the single fact below comes out low.

In that case:
- keep the C++;
- add ASan and TSan builds, turn warnings back on, and make `ObArray::operator[]` bounds-checked;
- move the `ATOMIC_*` fields to real atomic types;
- port to Rust only pieces that have their own differential judge.

### The single fact that would change the verdict

The fact is how much of the code to be redesigned the 272-case judge actually executes, measured as function coverage from two runs on this Mac that give identical results.

The directories counted are:
- src/sql/{engine,optimizer,rewrite,resolver/expr,resolver/dml,code_generator}
- src/storage/{access,memtable,tx,tablet,meta_mem,multi_data_source,tx_table,blocksstable}

This repo cannot tell us the number, for three reasons: the local run records are gone, the runner needs a one-line patch, and coverage builds are switched off.

The thresholds below are assumptions:
- **About 60% of functions or more, and both runs identical:** "later" shrinks to the judge-and-pilot phase, about 1-3 months, and Step 1 can start as soon as conditions 1-5 hold.
- **Under about a third, or the two runs differ:** don't migrate the whole of seekdb. Most translated code would ship unjudged, or building the judge would cost as much as the port.
- **In between:** stay at "later" while 00b widens the judge.

### How to check it in under a day

Work in a scratch worktree off 076eb309b. Never use `build_release/` in migrate-to-rust.

1. Prepare the tree:
   - Add `--nodaemon` to the `sdb.py start` call in `prepare_instance` in .github/script/seekdb/mysqltest_for_seekdb.py.
   - Set `MAX_CASE_RETRIES = 0`.
   - Remove the guard at CMakeLists.txt:147.
   - Build with `-DWITH_COVERAGE`, or with `-fprofile-instr-generate -fcoverage-mapping`. The plain build takes 314 s, so budget 10-20 min for the instrumented one.
2. Run the 272 cases twice: `python3 .github/script/seekdb/mysqltest_for_seekdb.py run --slice-index i --slice-count 4` for i = 0..3, using the arm64 mysqltest and obclient in deps/3rd/u01/obclient/bin. Each pass takes about 30-60 min when instrumented. src/observer/main.cpp:19 writes `<base_dir>/seekdb.profraw`.
3. Diff the per-case outputs and pass/fail sets between the two passes.
4. Run `llvm-profdata merge`, then `llvm-cov report --show-functions`, then map the covered functions to the directories above.
5. Fallback: the parser may still crash under coverage (cmake/Utils.cmake:139). If the instrumented build fails, do two plain runs for stability now and defer the coverage number.

Total: about 3-5 hours of machine time and under 2 hours of attention.

---

## 10. Where the analysts disagree

Three analysts wrote positions. One focused on the end state and the safety payoff, one on risk and verifiability, and one on cost, throughput and attention. They agree on most things:
- redesigning the ownership core;
- keeping the islands;
- data compatibility through a version bump and export;
- aborting on out-of-memory for the jemalloc builds;
- out-of-process embedding;
- Linux and macOS first;
- building the judge first;
- the verdict "migrate later".

These are the real disagreements.

**1. How Rust enters the product.**
- **End-state and cost analysts: build a parallel Rust tree and switch once.**
  - Only seams that are already C-like cross FFI (sql-nio, the parser, the islands), so no code is written against C++ memory layouts except the island shims.
  - The core has no narrow seams:
    - upper modules subclass base-layer classes (`ObTimerTask` 72, `ObDLinkBase` 72, `ObFuncExprOperator` 313);
    - header-only templates appear in 290 api signatures;
    - only one Rust staticlib can be linked;
    - mixed releases would need byte agreement on clog and slog, whose checksums hash raw struct memory (log_entry_header.cpp:83).
  - Seams also gather `unsafe`: sql-nio has 123 of its 186 in two FFI files.
- **Risk analyst: switch two or three large pieces inside the running binary, through seams first landed and verified in C++ under the unchanged judge.**
  - The pieces: a pilot on logservice/palf (32,705 lines, clean at link level), then the storage side under C++ SQL, then the SQL side.
  - A parallel tree gives no judge signal until init.sql and bootstrap work. That means parser, resolver, optimizer, engine, DAS, storage, tx, palf, schema and PL, with CreatePackage running at startup.
  - A first boot failure could not be traced to any one piece.
- **Unresolved:**
  - The cost analyst agrees the first end-to-end signal arrives only after about 90% of the code is translated.
  - The end-state analyst answers with the disposable narrow-path run and a growing plain-SQL subset.
  - Whether a redesigned core can pass plain-SQL cases under a reduced init without most of bootstrap is unmeasured. Decision 2 turns that into the switch condition.

**2. Who writes the core, and how large it is.**
- **End-state analyst:** about 300K measured lines, redesigned by hand (the developer with the largest model): 3-9 months, 400-1,200 hours.
- **Risk analyst:** the core is about 550-600K lines once schema and the sstable formats are counted, which is too much to write by hand. The interfaces and the ownership model are designed by hand, and agents write the bodies in disposable runs.
- **Cost analyst:** hand-build the substrate crates first, with a parity-tested value library as the second usable result.

This difference in scope and authorship drives the attention totals: 740-2,230 h, 480-1,270 h and 400-1,100 h respectively.

**3. How exact the final gate must be.**
- **End-state analyst:** masks during the migration, then byte-exact as the final gate, including EST numbers, `rowset=` and the order of unordered rows.
- **Risk analyst:** exact text as the main mode throughout, plus a short list of masks declared in advance and never added during Step 6. A judge masked as needed cannot fail.
- **Cost analyst:** exact by default, with the cost mask and row sorting as opt-in switches validated C++ against C++. Keep the cost model and SORT structure-preserved so the switches are rarely needed; each false failure costs about 1-3M tokens of triage.

The open point is whether the final gate may keep masks that were declared in advance.

**4. Whether Step 4 can fold into Step 3.**
- **End-state and risk analysts:** yes, per crate, if the pilot measures 60 s or less per crate.
- **Cost analyst:** no, even with fast crates:
  - cargo locks its target directory, so parallel agents cannot each run cargo;
  - about 4,000 units would contend for 24 GiB of RAM and 52 GiB of disk;
  - edits to foundation crates cascade.
  - Instead, one daemon checks each crate as its batch lands.

**5. What the deciding fact is, and what a good result means.**

| Analyst | Deciding fact | Good result | Bad result |
|---|---|---|---|
| End-state | Whether the judge runs stably on this Mac | "Migrate now" | — |
| Risk | Coverage and determinism | At least about 60% and identical: "now" after 00b | Under 35% or non-deterministic: "don't migrate the whole" |
| Cost | Coverage | At least about 60%: still "later", with a bounded judge phase | Under 30%: "don't migrate the whole" |

This report measures both in one check, and treats a good result as shrinking "later" rather than turning it into "now".

**6. Token and dollar bands.** Most of the spread comes from what was counted, not from opposing views:

| Analyst | Band | Per-agent assumption |
|---|---|---|
| Risk | 2.5-19B, $10K-75K | 100-400K, no cache re-reads |
| End-state | 6-45B, $5K-70K | 0.3-1.0M, cache reads priced at 0.1x |
| Cost | 15-140B processed, $10K-200K | 0.6-6M, counting every re-read |

Section 7 reports both counters explicitly.

**7. Calendar.** 12-24 months (end-state), 1-2.5 years (risk), 6-18 months (cost). The difference follows from point 2.

**8. Model tiers, on three narrow points.**
- **Judge reviewers:** Fable 5.1 (risk) against Opus 5.5 (the other two).
- **Core implementers:** Fable 5.1 (end-state, cost) against Opus 5.5 with Fable reviewers (risk).
- **Dependency-map skeptics:** Sonnet 5 (risk) against Opus 5.5 (the other two).

Section 8 picks one line for each.

---

## 11. Decisions for the developer

Ordered from most fundamental. Each decision can be discussed on its own.

### Decision 1. Is the Rust seekdb meant for upstream oceanbase/seekdb, or for your fork?

- **Context:**
  - The worktree's remotes are `origin` = oceanbase/seekdb and `fork` = cao1629/seekdb.
  - Of 716 non-merge commits on origin/master since 2026-06-01, 2 are yours. The largest contributors are obdev 190, footka 170, ep-12221 86, wangyunlai 85 (plus 17 as wangyunlai-seekdb) and hnwyllmm 76.
  - Upstream changes by about 8K ordinary lines a month. Its SQL fixes land in src/sql/optimizer and src/sql/rewrite, which is exactly the code the port redesigns.
  - release/1.4.0 still receives C++ fixes.
- **Options:**
  - (a) propose the port upstream first, and do it with the maintainers' agreement;
  - (b) a fork with a pinned base and scheduled replay of upstream fixes;
  - (c) a fork that stops taking upstream changes.
- **Recommendation:** ask upstream before Step 1. If the answer is no, take (b), and budget the replay as ongoing work. Don't drift into (c) without saying so.
- **Why:** this decides whether the Rust tree ever has more than one maintainer, who reviews the design, and how much replay costs. Replaying fixes into a redesigned optimizer cannot be done mechanically.

### Decision 2. How does Rust replace the C++?

- **Options:**
  - (a) A parallel Rust tree with a hand-designed core, the rest fanned out, and one cutover.
  - (b) Two or three large pieces switched inside the running binary, through seams first landed in C++ under the unchanged judge:
    - a pilot on logservice/palf (32,705 lines);
    - then the storage side (storage, tx, MDS, data_plane, logservice) running under C++ SQL;
    - then the SQL side (sql, pl, query, rootserver, observer, share/schema).
  - (c) Module-by-module replacement behind many small FFI seams (an earlier plan, already discarded).
  - (d) A literal structure-preserving translation first, with a safe refactor later.
- **Recommendation:** (a), gated on the Step 2 disposable run.
  - Switch to (b) if that run cannot pass the plain-SQL cases under a reduced init without most of bootstrap (system-package PL, inner-SQL schema loading, virtual tables).
  - Optionally, prototype the storage-owned filter interface in C++ first, to measure its speed under the existing judge.
  - Reject (c) and (d).
- **Why:**
  - (a) never writes Rust against C++ memory layouts, except in the island shims.
  - (b) keeps the judge running on a working binary after every switch. But it needs:
    - vtable bridges for base classes that other modules subclass (`ObTimerTask` 72, `ObDLinkBase` 72, `ObIReplaySubHandler` 14);
    - an umbrella staticlib, because only one Rust staticlib can be linked;
    - byte agreement on clog and slog inside every mixed release, because their header checksums hash raw struct memory (log_entry_header.cpp:83);
    - two foundations living side by side.
  - (c) freezes C-compatible layouts at every seam, and sql-nio already carries 186 `unsafe`.
  - (d) makes the core `unsafe` throughout and means two passes over about 600K lines.
  - The unknown that separates (a) from (b) is exactly what the disposable run measures.

### Decision 3. Must the Rust build open data directories written by the C++ build?

- **Options:**
  - (a) No: bump `DATA_CURRENT_VERSION` and provide a tested logical export and import.
  - (b) Read v1.4.0 directories.
  - (c) Ship a one-time offline converter.
  - (d) Full byte compatibility.
- **Recommendation:** (a), plus four things:
  - move the version check ahead of the meta.db open (ob_server.cpp:1698-1719);
  - close the missing-file hole (ob_data_version_mgr.cpp:58-60, 88-94);
  - test a round trip from C++ v1.4.x into Rust for every column type and index kind;
  - print a clear message at startup.
- **Why:**
  - The C++ already refuses any other data version (ob_data_version_mgr.cpp:61).
  - Strict version equality is also built into persisted objects (ob_medium_compaction_info.cpp:330,438; ob_macro_block_meta.cpp:204).
  - Header checksums hash raw struct memory, so byte compatibility would need `#[repr(C)]` mirror structs and would rule out the storage redesign.
  - v1.0.x directories (etc/observer.data_version.bin) already slip past the gate as "fresh installs".
  - The export has to run on binaries that have already shipped, and the installers restart the new binary at once (tools/macpkg/launchd/profile/postinstall).
  - Public tests cover none of this: 0 OUTFILE tests, 1 LOAD DATA test, 0 mysqldump tests.
  - Under Decision 2(b), mixed releases would still need byte agreement within each release.

### Decision 4. Which platforms must the first Rust release support?

- **Options:**
  - (a) Linux x86_64 and macOS arm64 only.
  - (b) Everything today: those two plus Windows x64, Android arm64 and wasm32.
  - (c) Linux and macOS first, with the wasm rules enforced in code from day one. wasm comes next, then Android, with Windows last.
- **Recommendation:** (c). The wasm rules to enforce from day one:
  - u64 for every persisted field and wire field;
  - no 128-bit atomics;
  - bounded recursion in place of stack switching;
  - networking behind cargo features;
  - SIMD128 chosen at compile time;
  - `panic=abort` for the whole program.
- **Why:**
  - Only Linux has CI. Packaging is broken for every format (45cbbe9e1 deleted the templates it references), and the Windows packaging path in build.ps1 is dead.
  - wasm is a shipped product: seekdb-shell pins engine-v1.4.0.0-cd41da17b, an 88 MB engine. It needs:
    - nightly-2026-09-07 with `-Zbuild-std` and a private patch to std for thread-local destructors;
    - no stack switching, which affects the 1,138 `SMART_CALL` sites;
    - 2 GiB of memory that is never returned;
    - the islands built on Emscripten's JavaScript exception model.
  - Adding these rules later means rework.
  - Windows and Android default to obmalloc with no malloc hook, which ties this decision to Decision 6.

### Decision 5. Which embedding model is the product?

- **Options:**
  - (a) The out-of-process driver in seekdb-bindings: 24 functions. It spawns `seekdb --embedded --nodaemon` and speaks MySQL over run/sql.sock.
  - (b) The in-process C ABI on origin/feat/embedded-mode: 93 functions over `ObInnerSQLConnection`, with 40 internal includes, 197 commits ahead of master.
  - (c) Both.
- **Recommendation:** (a) for the port.
  - Don't merge (b) into C++ before the port unless the product needs it now.
  - If it merges anyway, add its 61 C++ cases and language suites as a judge family, and plan to provide that ABI again in Rust.
- **Why:**
  - With (a), Rust owns the process: signals, the global allocator, `panic=abort` and exit. The contract is small, and 12 plus 7 cases already test it.
  - With (b), Rust runs inside a host, so an abort kills the host and allocator and signal ownership conflict. Its test `main()` already calls `_exit()` to avoid crashes in static destructors.

### Decision 6. What happens when an allocation fails?

- **Options:**
  - (a) Fallible allocation everywhere, carrying over about 4,700 NULL checks and about 6,470 `push_back` checks.
  - (b) Abort on general out-of-memory, with typed errors kept at the named budget owners and for the logical -4013 errors.
  - (c) (b) on the Linux and macOS jemalloc builds, with a separate stated policy for Windows and Android.
- **Recommendation:** (c).
- **Why:**
  - The default jemalloc path never returns memory-limit errors (ob_malloc.h:118-125). Even obmalloc enforces no byte limit, because `hard_limit_bytes_` is never set.
  - About 40 of 4,737 `OB_ALLOCATE_MEMORY_FAILED` sites sit behind real budgets.
  - The error-injection point EN_4 exists only on obmalloc, and no public test expects -4013, -4030, -11049 or -7603.
  - sql-nio already aborts on out-of-memory.
  - The named budget owners:
    - clog;
    - replay (turned into `OB_EAGAIN`);
    - the IO allocator (request and result objects retry until timeout; data buffers fail with -4013);
    - the KV cache store;
    - the vector module;
    - the query memory tracker;
    - the memstore-full check;
    - the temp-file write buffer pool;
    - plus the SQL work-area spill and the sleep-only throttle.
  - The logical -4013 errors that must stay typed: hash-join partition depth (ob_hash_join_op.cpp:837-842), the KV-cache handle pool, the IVF cache, and vsag's NO_ENOUGH_MEMORY.
  - Windows has no overcommit, so there an abort would turn a query error into a process exit.

### Decision 7. Which pieces stay C++ for good?

- **Options:**
  - (a) vsag, S2, share/geo with boost.geometry, ICU regex, and the bison parser, with the parser revisited after parity.
  - (b) The same without the parser.
  - (c) Also rewrite GIS on georust/proj and regex in Rust.
  - (d) Keep only vsag and S2.
- **Recommendation:** (a) for the first parity gate.
- **Why:**
  - 42 geometry tests with 2,834 ERROR lines pin boost's numbers and the error codes it derives from exceptions.
  - S2 cell ids and vsag snapshots are persisted.
  - No Rust regex crate matches ICU's semantics.
  - The parser's constant counts must match the fast parser exactly, or plan caching is lost silently.
  - Island sizes: GIS is about 76 entry points plus about 10 callbacks, vsag 24 functions, S2 about 9, ICU 15.
  - What each island costs: `unsafe` code, a `try/catch` at every entry, the malloc hook, OpenSSL, and Emscripten on wasm.

### Decision 8. Where may `unsafe` code appear?

- **Options:**
  - (a) Anywhere, with review.
  - (b) `#![forbid(unsafe_code)]` everywhere except named crates (island shims, SIMD kernels, IO buffers, thin wrappers over vetted reclamation crates), with an `unsafe` count per crate reported at every gate.
- **Recommendation:** (b).
- **Why:**
  - Without a hard rule, translation drifts back to raw pointers and the safety payoff disappears.
  - sql-nio shows where `unsafe` gathers: 123 of its 186 are in two FFI files.
  - 54123f13e shows that bugs inside hand-written reclamation stay put in `unsafe` code, so use vetted crates.

### Decision 9. How exact must the judge's comparison be?

- **Options:**
  - (a) Byte-exact throughout, including EST.ROWS/EST.TIME, `rowset=` and the order of unordered rows.
  - (b) Exact as the main mode, plus a short list of masks declared in 00b, reviewed, and never added during Step 6.
  - (c) Masked during the migration, byte-exact as the final gate.
  - (d) Normalize as needed.
- **Recommendation:** (b).
  - The masks are named switches, off by default, validated C++ against C++, and every use is logged.
  - At the final gate, run the masked classes exactly once more, and document any remaining differences for sign-off.
- **Why:**
  - 568 plan tables pin `ceil()` of doubles that also depend on storage estimation and freeze state.
  - About 10,100 SELECTs have no ORDER BY.
  - libc++ and libstdc++ already sort ties differently.
  - A judge masked as needed cannot fail. A judge that is strict everywhere floods triage with false failures.

### Decision 10. How does the server stop during the port?

- **Options:**
  - (a) Keep kill-only stops until parity. SIGTERM becomes `raise(SIGKILL)` (ob_signal_handle.cpp:129-134), and the last client leaving ends in `_Exit(0)` (ob_server.cpp:1651-1678).
  - (b) Add a clean shutdown in Rust from the start.
- **Recommendation:** (a). Change it after parity, as its own flagged 06 change.
- **Why:**
  - No test asset depends on SIGTERM acting as a kill; a server that shut down cleanly would pass them all.
  - But every restart asset exercises crash recovery today, and a clean shutdown would move the restart tests onto a different recovery path in the middle of verification.
  - Keeping kill-only means recovery must be exact from the first Rust build that restarts.

### Decision 11. How does the port keep up with the moving C++ tree?

- **Options:**
  - (a) Freeze at a named commit and never replay.
  - (b) Freeze, then replay upstream correctness fixes and their new mysqltest cases in scheduled windows, monthly or at batch gates, syncing by tree diff.
  - (c) Keep syncing continuously.
- **Recommendation:** (b). Pin the judge corpus to the same commit, and never replay in the middle of a batch.
- **Why:**
  - In the last 30 days: 32 ordinary commits (+3,895/-3,944 lines), plus about 68K lines of real cuts.
  - Deletions ride in commits with unrelated messages (45cbbe9e1, 706253423), so sync by diffing trees, not by reading commit messages.
  - mysql_test went from 600 cases to 283 in six months, and 24 commits re-recorded plan rows in a year.
  - The OceanBase mainline fix stream has been near zero since May 2026, so leaving it costs little.
  - The in-flight PRs are narrow, and are no reason to wait: #1356 keeps the `ob_malloc` facades, and #1427 touches 559 of about 99,910 `OB_FAIL` sites.

### Decision 12. What happens to the parallel branches before the design freezes?

- **The branches:**
  - origin/feature/plugin: +105,879 code lines, 34.6K of them Rust (rust/plugin-runtime, rust/extension-sdk);
  - origin/feat/embedded-mode: 197 commits ahead;
  - origin/feature/webassembly-shell: 366 files, and it conflicts with HEAD in 9 sql-nio files;
  - codex/palf-log-buffer-redesign;
  - codex/namespace-worker-proxy-v20: 223 commits ahead;
  - feature/rust (#1412).
- **Options:**
  - (a) Merge or rule out each one before the design document freezes.
  - (b) Port them after parity.
- **Recommendation:**
  - Reconcile the design document with feature/plugin's FFI and crate layout before Step 1.
  - Merge the wasm branch's race and portability fixes into C++ now.
  - Decide embedded-mode through Decision 5.
  - Let the palf buffer redesign settle before palf is touched.
  - Leave the rest until after parity.
- **Why:**
  - The plugin runtime already fixes Rust conventions that the port must match or replace.
  - The wasm fixes repair real races that porting exposed: `ObAtomicList`'s plain next links, and torn publishes in the log ring.

### Decision 13. Which model tier runs each phase?

- **Options:**
  - (a) The plan in section 8.
  - (b) A cheaper plan, with Sonnet 5 reviewers throughout.
  - (c) A stronger plan, with Opus 5.5 implementers throughout.
- **Recommendation:** (a).
- **Why:**
  - The tiers follow blast radius: Fable 5.1 for the design and the core, Opus 5.5 for review of ownership, Sonnet 5 for the bulk of implementers and fixers, and Haiku 4.5 only for receipts.
  - Prompts 01-06 take these as explicit `[model]` parameters.

### Decision 14. How is the spend paid for and capped?

- **Options:**
  - (a) The API, with prompt caching and a ceiling per gate.
  - (b) A subscription, where rate limits set the pace.
- **Recommendation:** (a) for the fan-out.
  - Set each step's ceiling at the top of its band, in harness-counted tokens, the counter logged in `migration/cost-log.tsv`.
  - Stop and decide again if the pilot's measured cost per agent run is more than twice the assumed 0.15-0.6M.
  - Accept the dollar band of about $10K-$300K only after the pilot.
- **Why:**
  - The bands rest on a read-heavy calibration, not on translation.
  - A ceiling set in processed tokens would never trigger against the harness counter.

### Decision 15. Does the machine need to grow?

- **Options:**
  - (a) Keep the Mac as it is: 52 GiB free disk, 24 GiB RAM.
  - (b) Free about 150 GB, or add an external SSD, before Step 4; optionally rent a Linux box for nightly judge runs.
- **Recommendation:** (b).
- **Why:**
  - A Rust target directory for about 2.5M lines is assumed to need 20-60 GB, next to the 6.9 GB C++ build and the judge's data directories.
  - RAM limits the machine to 2-4 large checks or judge instances at a time.
  - The .result files were recorded on Linux CI.

---

## 12. What was read and what was run

### What was read

- **The kit:**
  - README.md, CLAUDE.md, and prompts 00-feasibility, 00b-judge-setup and 01-06, all in full.
  - The README was used only as the rubric. None of its case-study numbers is used as an estimate here.
- **The evidence:**
  - /Users/colin/.claude/jobs/39d4f781/tmp/report/evidence-brief.md, in full (2,745 lines).
  - evidence-full.md was not re-read line by line; the three analysts read it.
- **21 read-only research areas, each fact-checked by a separate verifier.**
  - Main areas: sql-engine, sql-optimizer, sql-frontend, storage-core, tx-log, search-vector-gis, oblib, server-share-rest, idioms, tests-judge, build-platforms, deps, pain, coupling, runtime.
  - Gap areas: data-compat-conflict, judge-reach-map, referee-price, unit-ledger-and-tokens, moving-target, oom-policy-conflict.
  - Results: 518 claims, of which 350 confirmed, 162 corrected, 5 unverifiable and 1 refuted. The corrected text replaces the original.
- **2 late gap areas:**
  - external-judge-assets: 24 claims, 21 confirmed and 3 corrected;
  - cpp-islands-and-wasm32: 25 claims, 15 confirmed, 9 corrected and 1 unverifiable.
- **Three analyst positions** (end state, risk, cost). Each read the whole evidence base, and each ran its own read-only checks:
  - `unsafe` counts in rust/;
  - nio.h declarations;
  - the release profile;
  - storage files that name SQL types;
  - Bazel targets per package;
  - line counts of the proposed core;
  - `ob_sort` call lines;
  - `-mfma` flags;
  - sysctl and df;
  - the size of rust/target.

### What was run

- **One timing build** on this Mac, the one the rubric allows. Its results are in Call 2.
- **The survey itself:** 43 subagents, 8.92M harness-counted tokens, 4,994 tool calls, about 117 minutes of wall clock.
- **Checks made while writing** this report, all read-only in the worktree:
  - `git remote -v`;
  - author counts on origin/master since 2026-06-01: 716 non-merge commits, 2 by the developer;
  - the rust/Cargo.toml release profile and rust-toolchain.toml (1.98.1);
  - `MAX_CASE_RETRIES` at line 19 and the retry loop at line 502, giving up to 4 attempts;
  - sdb.py:127-128 (accepts `--nodaemon`);
  - ob_signal_handle.cpp (SIGTERM becomes `raise(SIGKILL)`);
  - the coverage guard at CMakeLists.txt:147;
  - 169 `lib::ob_sort` call lines;
  - 186 `unsafe` in rust/;
  - 272 configured cases;
  - df (52 GiB free);
  - list and cache prices from the claude-api skill.
- **Not done:**
  - no tests, no coverage run, no Rust builds;
  - `build_release/` in this worktree was not read;
  - obtest was not run;
  - the sibling repositories were inspected only by the external-judge-assets researcher.

### Facts reconciled where the positions differed

- **Retries:** up to 4 attempts per case, not 3.
- **Unordered SELECTs:** 10,107, of which about 7,500 are single-table and follow scan order, and about 300 depend on hash order. The cost analyst's "7,500" is the single-table subset.
- **Upstream authors:** the recount above replaces the risk analyst's figures.
- **Units:** 3,919 at a 30K-token cap and 4,137 at a 20K cap. The all-stem include-closure median is 1.43M; the 2.4M figure came from a biased sample.
- **Rewrite-rule reach:** 9 of 34 is a lower bound.
- **wasm:**
  - 44 CTests are registered on the branch;
  - no OPFS test is automated.
- **IO allocator:**
  - request and result objects retry until timeout;
  - data buffers fail with -4013.
- **Stale paths:** ob_tenant.cpp, ob_sort_compare_vec_op.ipp and the ob_expr.cpp:1056 workaround are gone. This report cites ob_simple_thread_pool.ipp:162 and ob_sort_op_impl.cpp:395-420 instead.

### Cost-log line not appended

This step's single permitted write to `migration/cost-log.tsv` was not made, because this report is the only file the writer may create. The orchestrator should append:

```
0	2026-09-24T06:37Z	117	8920000	43	unknown
```
