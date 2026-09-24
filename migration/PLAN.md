# seekdb from C++ to Rust: plan of record

## 1. What this plan is and where things stand

This is the plan every later step of the Rust migration reads. It turns the step-0 feasibility report and the developer's 17 decisions into one plan: what is built, in what order, with which kit prompt, and what must hold before each step starts. It replaces the feasibility report as the working document. The report stays as background and as the source of most figures here.

Status on 2026-09-24:
- **The 17 decisions are made** (Gate A: Decisions 1-9; Gate B: Decisions 10-16; Gate C: Decision 17), plus one correction to the report (the wasm race-fix exception is dropped).
- **The coverage fact is measured, but it does not stand yet.** The 272-case judge executes 64.4% of the functions in the core and the SQL tier together (coverage README). Under report §11 the number waits until 00b explains the difference below.
- **The precondition is not met.** Over two passes of the same C++ binary, a fourth case failed once (`subquery.idx_with_const_expr_21_subquery_dilang`), and it was not on the quarantine list. So the judge is not validated yet, and Step 1 cannot start. The next work is 00b's core set (section 10).
- **The C++ reference cannot be built on this Mac the way the report assumed, but the plan's default route works.** At 834bbee1e, a macOS 27 host needs an SDK 27, and this Mac has SDK 26.2; configure stops (checked). The plan's default, skipping the MacOS27.cmake include and keeping the bundled Clang 17.0.6 with SDK 26.2, was tested on 2026-09-24 and built in 317 s (section 4, "How the C++ reference is built"). The developer confirms the toolchain choice before action 5 (section 8, item 2); section 10's actions 1, 3, 4 and 14 do not wait for it.

Where things live. Paths are from the repository root, /Users/colin/seekdb-dev/migrate-to-rust/, unless given in full. `prompts/`, `templates/` and `scripts/` are the kit's (/Users/colin/repo/code-migration-kit-with-claude-code/); the seekdb tree's own `script/` directory is unrelated.

| What | Path |
|---|---|
| This plan | migration/PLAN.md |
| The decisions and the corrections to the report (authoritative over the report and over this plan) | migration/decisions.md |
| The feasibility report (background) | migration/feasibility/feasibility.md |
| The evidence behind the report | migration/feasibility/evidence-brief.md, migration/feasibility/evidence-full.md, migration/feasibility/raw/ |
| The coverage result and the precondition result | migration/judge/coverage-076eb309b/README.md, with AB.groups.txt, the per-pass seekdb_result.json files, the pass-B diff of the subquery case, scratch-tree.patch and coverage_groups.py beside it |
| Raw coverage profiles and merged .profdata (about 1 GB) | /Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/ |
| The judge's own files (from 00b on) | migration/judge/, laid out as in section 4, "Where the judge's files live" |
| The kit cost log | migration/cost-log.tsv. One row so far: step 0, 251 min, 13,049,394 tokens, 55 subagents, claude-opus-5-5. |
| The kit | /Users/colin/repo/code-migration-kit-with-claude-code: README.md, CLAUDE.md, prompts/00b-06, templates/, scripts/ |

How to read the figures:
- "Report §N" is section N of migration/feasibility/feasibility.md. "Decision N" is a row of migration/decisions.md. "Corrections" is the section of that name in migration/decisions.md. "Coverage README" is migration/judge/coverage-076eb309b/README.md.
- "Assumption" marks a figure that is neither measured nor counted. "The plan's default" or "the plan's proposal" marks a choice this plan makes that no decision covers; the developer may change it.
- Where this plan and decisions.md disagree, decisions.md wins and this plan gets corrected. Within decisions.md, the Corrections section overrides the table rows.
- This file, migration/judge/ and decisions.md are committed on migrate-to-rust (commits 8326be1ac, 040f28a1a and 98a4a176b, 2026-09-24). The repository's .gitignore ignores `*.patch`; migration/.gitignore re-includes it, so the judge's patches are tracked.

---

## 2. Scope and goals

- **What the port is for:** a Rust seekdb engine as a product. Having the Rust engine is the payoff (Decision 1 (b)).
- **Not criteria for now:** binary size, start-up time and memory use (Decision 1 (b)). Query speed is gated at 1.2x against the C++ reference, and "start-up time" covers restart after a kill too, crash recovery included, so restart time is recorded and not gated (decisions.md row 1a, 2026-09-24).
- **Form:** the basic server mode only. The embedded form is deferred: `--embedded`, the out-of-process driver in seekdb-bindings, and the in-process C ABI on origin/feat/embedded-mode (Decision 8).
- **The first Rust release:** macOS arm64 only. All work happens on this Mac, and it is the only gate machine. No Linux machine is available, so there is no Linux gate. The .result files were recorded on Linux x86_64 CI; the judge compares C++ with Rust on this Mac instead (Decision 7).
- **Long-term platforms:** wasm, Android, macOS, Linux and Windows; not iOS. The design must not rule any of them out (Decision 7). Section 3 ends with the constraints and design guidelines that follow from this.
- **Where the code lives:** the fork cao1629/seekdb, branch migrate-to-rust. The fork stops following upstream oceanbase/seekdb: no upstream design review, no replay budget (Decision 2 (c)).
- **The frozen C++ base:** upstream master 834bbee1e (2026-09-22), never replayed (Decision 3 (a)). It is 076eb309b's content plus 23 files (+342/-25): 3 correctness fixes, macOS 27 build support and mirror URLs. Open PRs #1356, #1427 and #1351 are not taken. The C++ reference is 834bbee1e's source with no code changes (the wasm branch's race-fix hunks are not added, Corrections), built with `-ffp-contract=off` and the toolchain of section 4 through migration/judge/reference-build.patch, which changes only build files.
- **Parallel branches:** all are ruled out as code sources: origin/feature/plugin, origin/feat/embedded-mode, origin/feature/webassembly-shell, origin/codex/palf-log-buffer-redesign, codex/namespace-worker-proxy-v20 and origin/feature/rust. origin/feature/rust's `compare_cpp.py` is borrowed as a pattern for 00b's expression generator, not merged (Decision 15).
- **The machine:** this Mac as it is: Apple M4 Pro, 14 cores, 24 GiB RAM, 33 GiB free disk on 2026-09-24 after the coverage build (Decision 17 (a); a later `df -h /` showed 50 GiB, section 7). macOS 27.0, with Command Line Tools holding SDKs up to 26.2 and no Xcode (checked while revising this plan).
- **Who does the work:** one developer with Claude Code, on a Claude subscription where rate limits set the pace and there is no dollar ceiling (Decision 5 (b)).

---

## 3. How Rust replaces the C++

### The approach

A parallel Rust tree and one cutover (Decision 10 (a)):
- **Redesign the ownership core by hand:** about 352K lines at the narrowest, about 600-650K at the widest (report §3).
- **Rewrite the SQL layers against the new core, keeping their control flow:** the optimizer, rewrite rules, resolvers, operators, expressions and DAS, about 0.75M lines. Their algorithms, pass order, function boundaries and file layout stay; nearly every function signature changes.
- **Translate the other leaves file by file** against the new core.
- **Keep bit for bit,** in all three tiers, the algorithms and code that produce text the judge compares.
- **Keep in C++ behind a C ABI:** the pieces listed under "The C++ that stays" (Decision 13).
- **Cut over once:** the Rust binary replaces the C++ one under a bumped data version (Decision 11).

**Switch condition** (Decision 10, kept from the report): if the Step 2a narrow run cannot pass its named list of 20-50 single-table statements without most of bootstrap (system-package PL, inner-SQL schema loading, virtual tables), switch to (b): two or three large pieces switched inside the running binary. Section 6 has the extra work that brings.

### The core, defined

"Core" means the code redesigned by hand (report §3):

| Part | Directories | Lines |
|---|---|---|
| Foundation substrate | src/oblib/lib/{alloc,allocator,container,hash,string,rc,lock,atomic,list,queue} | 57,762 |
| Runtime pieces marked for redesign | src/oblib/lib/thread, src/oblib/lib/utility, IO (src/share/io, src/oblib/lib/{file,restore}, the local device), src/share/cache, the DAG scheduler (src/storage/scheduler, src/data_plane/api/data_plane/scheduler) | 53,467 |
| Value and datum types | datum, object, rc | 17,744 |
| Statement IR | src/sql/resolver/expr plus the DML statement classes | 42,825 |
| Execution framework | query/api engine, operator factory, exec context, physical plan, `ObExpr`, frame info, pushdown filter, code generator | 43,808 |
| Storage and transaction core | tablet, meta_mem, memtable, tx, multi_data_source, tx_table, data_plane access | 137,103 |
| **Total (narrow scope)** | | **about 352K** |

The wider scope, about 600-650K lines, adds the schema object model (share/schema plus observer/schema, about 88K), the sstable formats in storage/blocksstable (87.6K), the rest of resolver/dml and all of the code generator. The core is about 550-1,050 stems (report §3). The design document settles which scope is built.

### What is rewritten against the new core, keeping its control flow

| Code | Lines (report §3) |
|---|---|
| src/sql/optimizer | 155,780 |
| src/sql/rewrite (the 34 rules and query range) | 97,268 |
| src/sql/resolver outside the statement IR | about 108,500 |
| src/sql/engine outside the execution framework (61 operators, 528 expressions) | about 343,000 |
| src/sql/das | 46,252 |
| **Total** | **about 0.75M** |

To keep this tier's control flow, the design document must fix two things (report §3):
- **IR ids that mirror pointer identity one for one.** One id per allocation; an in-place edit keeps the id; new ids appear only where `ObRawExprCopier` makes a copy today. Then `find_item` (src/sql/optimizer/ob_optimizer_util.h:265-287, about 580 call lines) and the set helpers built on it keep their exact meaning. The report's second reason, keeping upstream fixes replayable, no longer applies (Decision 3).
- **A Rust mirror of `ParseNode`** (206 files, 2,610 references), so the 89 resolvers keep their shape.

### Leaf code translated file by file

The 146 virtual tables, `ObDDLService` (381 methods), the PL interpreter, the value libraries (ObNumber, time, JSON, the 3 live collations), config, the rootserver and the rest of share and observer keep their structure against the new API (report §3).

Units for Step 3: 3,692 stems, minus about 550-1,050 core stems, minus about 100 island stems, plus about 150-400 units from splitting the 54 files of 4K lines or more, gives **about 2,700-3,400 units**: about 1,200-1,400 in the SQL tier and about 1,300-2,200 other leaves (report §3). The two tier bands do not add back to the total: the other-leaf band is the total minus the SQL-tier band at its widest (2,700 − 1,400 to 3,400 − 1,200), so together they span 2,500-3,600. Token usage (section 7) multiplies the tier bands; run counts and the calendar use the total. Step 1's manifest settles the real split. Step 3 adds 25-50 units of island shims (report §6).

### The C++ that stays, behind a C ABI

For the first parity gate (Decision 13 (a)):

| Kept in C++ | What crosses the boundary (report §3) | Why it stays |
|---|---|---|
| The parser's C core: bison/flex output, sql_parser_base.c, parse_node.c | The `ParseNode` C struct; the core calls back into Rust for memory (parse_malloc.cpp) and charset functions | The grammar has 758 rules with C actions and no bison-compatible Rust target |
| vsag | 24 functions over `void*` handles; callbacks: 2 `vsag::Allocator` subclasses, `vsag::Logger` (7 virtuals), a filter, 2 `std::streambuf` bridges | Snapshots are persisted and the format is pinned |
| S2 | About 9 adapter functions, plus `get_cellid_mbr_from_geom` and the geographic MBR test | Cell ids are persisted in spatial-index rows |
| share/geo with boost.geometry (47.8K lines) | A new bytes-in/bytes-out kernel API of about 76 entry points and about 10 callbacks | 42 geometry tests with 2,834 ERROR lines pin boost's numbers |
| ICU regex | 15 C functions from ob_expr_regexp_context.{h,cpp} (954 lines) | No Rust regex crate matches ICU's semantics |

`ObParser`, `ObPLParser` and `ObFastParser` are ported to Rust (Decision 13). `ObFastParser`'s constant counts must match the grammar's exactly; 00b's plan-cache hit counter checks it.

Every island keeps: a `try/catch` at every `extern "C"` entry (ob_geo_dispatcher.h:1314-1363 maps 18 exception types); OpenSSL, which libs2.a needs for its `BN_*` symbols; and a C++ subset of oblib (allocator, `ObString`, charset, containers, logging) under share/geo and S2. The design document sizes that subset at link level and lists it as kept C++, with its build, its allocator hook and a rule that its charset behavior matches the Rust charset code (report §3). The report also lists the malloc hook that charges island allocations (malloc_hook.cpp:143-163); at 834bbee1e that library is built only on Linux (src/oblib/lib/CMakeLists.txt:34-41), so on macOS the design document decides how island memory is charged.

Revisit the islands after parity, because they keep a C++ runtime inside the product (Decision 13).

### Data directories: no compatibility with the C++ build

The Rust build does not open data directories written by the C++ build (Decision 11 (a)):
- bump `DATA_CURRENT_VERSION` (src/oblib/common/ob_version_def.h:53, today 1.4.0.0) and provide a tested logical export and import;
- move the version check ahead of the meta.db open (src/observer/ob_server.cpp:1698-1719);
- close the missing-version-file hole (src/oblib/common/ob_data_version_mgr.cpp:58-60, 88-94);
- print a clear message at startup.

This frees the OB_UNIS, ObNumber and JSON binary bytes from being a contract, so the judge does not compare them; they get differential tests of the value libraries in the core build instead (report §5 item 8).

### When an allocation fails

Decision 12 (b), exactly: one policy on every platform. A general out-of-memory aborts the process. Typed errors stay at the named budget owners (clog, replay, the IO allocator, the KV cache store, the micro block cache, the vector module, the query memory tracker, memstore full, the temp-file write buffer pool, plus the SQL work-area spill) and for the logical -4013 errors (hash-join partition depth, the KV-cache handle pool, the IVF cache, vsag NO_ENOUGH_MEMORY). Fallible (`try_reserve`-style) collections appear only there and in buffers whose size a client controls.

On macOS and Linux this matches today's jemalloc behavior. On the later Windows, Android and wasm targets, and in a library form, a general out-of-memory ends the whole process or host (Decision 12 notes).

### Where `unsafe` may appear

`#![forbid(unsafe_code)]` everywhere except named crates: island shims, SIMD kernels, IO buffers, and thin wrappers over vetted reclamation crates. The `unsafe` count per crate is reported at every gate (Decision 14 (b)).

### Toolchain

One pinned stable toolchain (1.98.1 today, rust/rust-toolchain.toml) for everything. No nightly features in shared code; allocator-api2 instead of the nightly `allocator_api`. wasm gets its own pinned nightly when it comes back into scope. Reopen only if Step 2a measures per-crate checks above about 60 s and `-Zthreads` would bring them under (Decision 16 (a)).

### Outline of the redesigned core (input to the design document)

Carried from report §3, adjusted to the decisions:
- **Process.** For the first release, Rust owns `main`, the threads, one jemalloc as `#[global_allocator]`, and `panic=abort`. Keep those choices in the server binary's crate, so a later library build can replace them (see the guidelines below). General allocation aborts on out-of-memory; typed budget errors stay at the named budget owners (Decision 12).
- **Crates.** 20-40 crates in an acyclic graph, each at most about 100-180K lines. Seed them from the Bazel header-level graph, which is already acyclic (179 `cc_library` targets, plus the 79 oblib and 116 share targets referenced by src/sql/sql_runtime_group_deps.bzl), not from the 12 module roots. sql-nio becomes a plain Rust crate inside the Rust binary, and its two C-ABI files (row_encode.rs, response_api.rs) are dropped.
- **Foundation:**
  - one error type that carries the exact OB code, generated from ob_errno.def;
  - per-statement bump arenas borrowed as `&'q`;
  - owned bytes or `Arc<[u8]>` for anything sent to another thread or put in a cache (the fix for the bug table in report §2);
  - `Atomic*` field types in place of `ATOMIC_*` on plain fields (about 823 field names);
  - `Mutex<T>` that owns the data it guards;
  - `Arc` with a custom drop for pooled handles;
  - an explicit server context in place of the `server_service<T>` slots and `GCTX`;
  - a small set of thread-pool, queue and timer primitives in place of the many C++ variants.
- **Statement IR:** arenas of typed ids that mirror today's pointer identity one for one; stack growth on native targets in place of `SMART_CALL`.
- **Execution:** the plan is an immutable `Send + Sync` value (today 16 `const_cast`s write into the shared plan); per-run state lives in typed column batches owned by operators; dispatch goes through enums or typed tables.
- **sql/storage boundary:** storage, the lower crate, defines the column batch and a filter/aggregate trait, and SQL implements it. This is what lets sql and storage be separate crates.
- **Storage and transactions:** immutable `Arc` tablet snapshots; epoch reclamation from a vetted crate instead of QClock, the retire station and the KV cache's hazard versions; an `Arc` transaction context whose callbacks complete in sequence-number order; MDS as an enum plus a trait; new explicit little-endian on-disk formats under the bumped data version (Decision 11).
- **`unsafe`** only in the named crates (Decision 14).

### What is dropped or regenerated rather than translated

From report §3:
- the obmalloc backend (26 files, 8,490 lines);
- lib/codec (32,109 lines, no callers outside itself);
- the timezone tables (38,963 lines, never read);
- tzcode and ob_ctype_uca.cc (both dead);
- vendored zstd and xxhash, which become crates;
- protobuf/gRPC, which become prost/tonic;
- the 276,043-line IK dictionary, which becomes a data file;
- about 30.9K lines of other dead files.

Six generators carry text contracts: gen_errno.pl, gen_ob_sys_variables.py, generate_inner_table_schema.py, syspack_codegen.py, gen_str_datum_func_parts.py and gen_expr_str_cmp_func.py. Point them at Rust output instead of translating what they produce today (about 87K checked-in lines plus about 104K inner-table lines generated at build time).

### What must stay the same, to the bit

From report §3; these are what the judge compares exactly:
- **Plan text:** 568 plan tables (EST.ROWS and EST.TIME are `ceil()` of doubles; masked under Decision 6, section 4); 4,005 `output(` lines (the expression printer and implicit casts); query-block names printed as `%08X` hashes (ob_sql_hint.cpp:389); 3,598 `rowset=` lines, set by `ObCodeGenerator::detect_batch_size`, whose rule is carried over exactly.
- **Row order:** ties under ORDER BY follow the SORT operator's own algorithms (ObAdaptiveQS, radix, and `std::sort` behind about 169 `lib::ob_sort` call lines). Of 11,908 SELECTs, 10,107 have no ORDER BY: about 7,500 are single-table and follow storage or PX scan order, and about 300 depend on hash output order. Only the about 300 are masked (Decision 6), so ties under ORDER BY and the scan order of the about 7,500 must come out the same from the Rust build.
- **Number formatting:** 770,711 of the 912,659 configured result lines come from four arithmetic matrices (ObNumber; MySQL's dtoa port).
- **Errors and hooks:** 1,546 error-catalog entries with their text and 2,688 `--error` directives; DEBUG_SYNC point names (7 configured cases; FORK_TABLE_BUILD_DATA alone has 46 uses); 322 numbered tracepoints, 12 of them set by init.sql; 289 parameter names.

### Where a faithful translation still changes behavior silently

Each needs a design-document rule and inventory rows before any fan-out (report §3, adjusted):
1. **Sorting.** Rust's `sort_unstable` is a different algorithm from `std::sort`, and a Rust sort may panic when the comparator is not a total order (per the Rust 1.81 release notes, not checked). The C++ reference uses the libc++ headers of the macOS SDK it is built against (deps/3rd's devtools ship no `include/c++/v1`, checked), so the tie behavior of `std::sort` is fixed by that SDK: SDK 26.2 under the plan's default toolchain (section 4). Ties are not masked, so the Rust port reproduces the tie behavior of the algorithms the C++ calls, as that SDK's libc++ implements them. Two comparators still return false for every pair after an error (ob_sort_op_impl.cpp:440-445, ob_slice_calc.cpp:1046-1052).
2. **Hashing.** The default `HashMap` is seeded randomly per process. OB hash iteration order reaches output at ob_join_order.cpp:16662/16727, ob_range_generator.cpp:1799 and ob_key_part.cpp:289/305.
3. **Floating point.** clang fuses `a*b+c` into one FMA instruction by default on arm64, and no build file sets `-ffp-contract` (assumption from compiler defaults; it holds for the deps/3rd clang 17.0.6 of the plan's default and for the host Apple clang of the fallback alike). rustc never fuses. So the C++ reference is built with `-ffp-contract=off` for every judge run, and Rust calls `mul_add` only where the C++ calls `fma` itself. 00b checks once that the 40 plan-bearing cases give the same text with and without the flag.
4. **Integer overflow** panics in Rust debug builds. The judge build profile keeps overflow checks off, as in release.
5. **Error codes used as values.** 4,189 `OB_X == ret` lines, 2,905 `ret = OB_SUCCESS;` resets, 17,767 `OB_SUCC(ret) &&` guards, about 1,560 `tmp_ret`/`OB_TMP_FAIL` lines, and about 6,000 uses of a code as a normal outcome (`OB_ITER_END`, `OB_ENTRY_NOT_EXIST`, `OB_EAGAIN`). `?` is the wrong mapping for all of these.
6. **Deep recursion.** `SMART_CALL` (1,138 sites) switches to a new stack or returns `OB_SIZE_OVERFLOW`; a Rust stack overflow aborts the process. On macOS, grow the stack the way `SMART_CALL` does, with a stack-growing crate such as `stacker` (assumption: that it works on macOS; checked in the design work). The recursive algorithms keep their shape. The wasm depth limit is a guideline below.
7. **The parser's error exit.** The parser exits through `longjmp` from a thread-local `jmp_buf` (sql_parser_base.c:104-106). No Rust frame may sit between the parse entry and a callback.
8. **Plan caching can be lost with nothing showing.** If the Rust `ObFastParser` disagrees with the grammar about constants, src/sql/ob_sql.cpp:3327-3350 re-parses the statement and skips the plan cache. Only 00b's plan-cache hit counter catches this.

### Constraints from the decisions for the long-term platforms

These come from decisions.md. The design document follows them and may not reject them:
- **Toolchain:** no nightly features in shared code, so shared code keeps building on stable and a later wasm nightly only has to cover the wasm build (Decision 16 (a)).
- **Out-of-memory on later targets:** on Windows, Android, wasm and in a library form, a general out-of-memory ends the whole process or host, so the budget owners must be sized so that general out-of-memory stays rare there (Decision 12 notes).
- **A clean close** is needed by the library form later; it is post-parity work, not part of the first release (Decision 9 notes).

### Design guidelines the design document adopts or rejects

These are guidelines, not decisions. They keep the long-term targets (Decision 7) open at little cost now; the design document adopts or rejects each one explicitly.
- **Don't hard-wire process ownership into the engine** (Decision 8 notes, "Design guideline, not a decision"). wasm and Android will need the engine as an in-process library. Keep these out of the engine crates, in the server binary's crate:
  - `chdir` into the base dir (src/observer/main.cpp:705); today the meta.db path is built from `getcwd()` after it (src/observer/ob_server.cpp:1698-1709). Engine crates take the base dir from the server context as an absolute path.
  - installing signal handlers (src/observer/ob_signal_handle.cpp);
  - `_Exit` on the last client. At 834bbee1e that path runs only in embedded mode: `wait()` starts `wait_no_client()` only when `gctx_.is_embedded_mode()` (src/observer/ob_server.cpp:1670-1672), and the `_Exit(0)` calls at :1645 and :1654 sit inside `wait_no_client()`. The first release's server binary needs only the unconditional `_Exit(0)` at the end of `wait()` (:1678);
  - the malloc hook (built only on Linux at 834bbee1e, section 3, "The C++ that stays").
  - The plan's own addition, not in Decision 8's notes: the choice of global allocator and of `panic=abort`.
- **The wasm rules,** from report Decision 7; each costs little if followed from the first line and means rework if added later. They are guidelines because Decision 7 did not choose the report's "enforced from day one":
  - u64 for every persisted field and wire field;
  - no 128-bit atomics;
  - a recursion depth limit on wasm that returns today's `OB_SIZE_OVERFLOW`, with stack growth on native targets;
  - networking behind cargo features;
  - SIMD128 chosen at compile time;
  - `panic=abort` for the whole program.
- **Budget limits on wasm:** set the budget owners' limits from the 2 GiB heap that is never returned (report Decision 12).

---

## 4. The judge

The judge runs the pinned C++ reference and the Rust build side by side on this Mac, through the same external interfaces: SQL over the MySQL protocol (obclient, mysqltest), the command line (`sdb.py start` / `stop`), and files in the base dir. It is built with `prompts/00b-judge-setup.md` before Step 1 (report §5).

- **The reference:** 834bbee1e, built with `-ffp-contract=off` (section 3, "Where a faithful translation still changes behavior silently", item 3), on this Mac, with the toolchain below.
- **The checked-in .result files** are used only to validate the C++ reference under the full init.sql. Under the reduced init (item 2 below) the C++ reference is recorded again, and the Rust build is compared with that recording, not with the .result files.
- **Which tests are in scope:** 00b's first step (section 10, action 3) confirms the census of report §5 as a file receipt. After Decisions 7 and 8, the in-scope portable set is the 272 configured mysqltest cases (283 tracked) and tools/ob_error/test/test.sh (run under family 6). The seekdb-bindings gtests, the seekdb-async tests, shell-e2e and the wasm unittests are out of scope for now. The 7 tracked internal-bound C++ files are recorded for what they guarded and left behind.
- **The case counts are over the tracked cases, not the configured ones.** Report §5's 130 plain-SQL cases and 153 cases that lean on OB internals add up to the 283 tracked cases; its 40 plan-bearing cases come from the same count, and the evidence gives both 40 and 42 (evidence-brief.md:2098 and :1327). The lists over the 272 configured cases do not exist yet; action 3 writes them as files, and this plan's counts are restated from those files.
- **The runner's working tree:** the harness runs from migrate-to-rust, whose tools/deploy (tests, .result files, init SQL) and .github/script/seekdb/sdb.py are 834bbee1e's. The runner resolves them from its own location (mysqltest_for_seekdb.py:475-477), so before every judge run, `git -C /Users/colin/seekdb-dev/migrate-to-rust diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py` must succeed and `git status --porcelain -- tools/deploy` must print nothing. The runner itself is left out of that check, because item 1 changes it. The reduced init profile lives outside tools/deploy.

### How the C++ reference is built

**The report's route does not work at 834bbee1e on this Mac** (found while revising this plan). 834bbee1e added macOS 27 support: on a macOS 27 host, cmake/Env.cmake:202-203 includes cmake/MacOS27.cmake, which accepts only developer tools whose SDK is 27 or newer and otherwise stops with `FATAL_ERROR` ("macOS 27 requires Xcode/Command Line Tools with SDK 27 or newer"). It then compiles with the host Apple clang found by `xcrun`, or with `OB_MACOS_LLVM_ROOT`'s clang, and deps/init/oceanbase.macos27.arm64.deps no longer downloads the obdevtools LLVM. This Mac runs macOS 27.0 with SDK 26.2 at most (`xcrun --sdk macosx --show-sdk-version`) and no Xcode, so configure fails. The coverage build worked because 076eb309b has no MacOS27.cmake and compiled with deps/3rd's clang 17.0.6. A `bash build.sh release` in the migrate-to-rust worktree on 2026-09-24 confirmed the stop (its build_release/ is left in that failed-configure state; nothing builds there).

Two ways out; the developer chose (b) on 2026-09-24 (decisions.md row 3a):
- **(b) The plan's default: keep the 076eb309b toolchain.** reference-build.patch skips the MacOS27.cmake include in cmake/Env.cmake, so the build takes the macOS 15 path with deps/3rd's clang 17.0.6 and SDK 26.2, the toolchain behind the coverage measurement and the report's 314 s build. Every other build change in 834bbee1e is behind `OB_MACOS27`, behind a check for the AppleClang compiler (the `-Xclang -fopenmp` branch in src/oblib/lib/CMakeLists.txt; assumption: CMake reports deps/3rd's clang as `Clang`, not `AppleClang`), or in the deps lists, which `--make` without `--init` does not read; all of them drop out. What remains are the three source fixes and the removal of `static` from explicit specializations in mds_table_handle.ipp and compile_mapper.h. The route was tested on 2026-09-24 in /Users/colin/seekdb-dev/ref-834bbee1e: with only the MacOS27.cmake include skipped (`if(FALSE AND MACOS_MAJOR GREATER_EQUAL 27)` in cmake/Env.cmake) and `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk bash build.sh release --make`, 834bbee1e built in 317 s with Clang 17.0.6 and 0 errors, and the 193 MB binary's `seekdb -V` reports revision 834bbee1e (log and timings in /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-toolchain-test/). Not yet run through the judge, and built without `-ffp-contract=off`. SDKROOT points at the versioned path /Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk, not the MacOSX.sdk link, which a Command Line Tools update can move. The patch changes only build files, so the source stays exactly 834bbee1e's.
- **(a) The fallback: upstream's route.** Install Command Line Tools with a macOS 27 SDK and build with the host Apple clang it selects. The compiler, the SDK and its libc++ headers then change with Apple software updates, so the archive (item 13) keeps a copy of the developer directory, or the build sets `OB_MACOS_LLVM_ROOT` to an LLVM the archive holds.

Under either choice, the libc++ headers come from the SDK (deps/3rd's devtools have no `include/c++/v1`), and the runtime libc++ and libSystem come from macOS itself. So item 13 records the compiler (`clang --version` of the one used), the SDK version and path, `_LIBCPP_VERSION`, and the macOS build (`sw_vers`); the archive keeps a copy of the SDK directory; and the rebuild check runs again after any macOS or Command Line Tools update.

**Adding `-ffp-contract=off`:** `-DCMAKE_CXX_FLAGS=` on the build.sh line does not survive: cmake/Env.cmake:125 sets `CMAKE_CXX_FLAGS` to `-std=gnu++20` outright, and :350 resets `CMAKE_C_FLAGS` on Apple. The coverage block at cmake/Env.cmake:67-71 adds its flags with `add_compile_options`, which shows the route: reference-build.patch adds `add_compile_options(-ffp-contract=off)` next to it, and action 5 confirms the flag on the compile lines of one C and one C++ object.

**deps/3rd:** it is copied from the main checkout (/Users/colin/seekdb-dev/seekdb, at 35eb2c3a3) with `cp -c -R`, as the coverage build did. Its marker file names 214b0e11b970224fe30e72a114fb7014, the md5 of the macOS 15 deps list at 076eb309b and 35eb2c3a3. At 834bbee1e that list differs only in its mirror URLs (md5 d9313c47ee9c3209e642d3bbaaa22db4), and the macOS 27 list (8687d33807fec4aabe94ec15b149e784) is the macOS 15 list without `obdevtools-llvm-17.0.6-20251212.tar.gz`. `build.sh release --make` without `--init` does not run dep_create.sh, so the marker mismatch is expected and harmless.

### The harness items

The report's 14 items (report §5), adapted. Items 5 and 11 are dropped; 12 remain, and item 14 is optional. The **core set is items 1, 2, 3, 10, 12 and 13**: they must exit before Step 1. The rest finish while Step 1 runs and are signed off before Step 2a ("00b's exit", below).

| # | Item | What it means now | Core set | Status |
|---|---|---|---|---|
| 1 | Runner fixes | In the runner that ships with the frozen tree (.github/script/seekdb/mysqltest_for_seekdb.py): pass `--nodaemon` to `sdb.py start` (sdb.py:127-128 accepts it); a `--max-retries` option, 0 for every judge run and 3 by default so CI keeps its behavior, with every attempt logged (`MAX_CASE_RETRIES = 3` at line 19, loop at line 502); fixed case order and slice count, plus a mode with a fresh instance per case; a `--case-list` option that runs only the cases named in a file (today the runner selects cases only by `--slice-index`/`--slice-count`); options for the init.sql and init_user.sql files, so the reduced profile can live outside tools/deploy; save what the instance writes (logs, coverage files) before every `destroy_instance` (line 100); a differential mode that compares two builds' outputs instead of comparing with .result files. **Remove the trailing-whitespace tolerance for judge runs:** when mysqltest reports a mismatch and the .reject equals the .result apart from trailing spaces and tabs, `run_case` turns the failure into a pass (`files_equal_ignoring_trailing_whitespace`, lines 62-76; `return_code = 0` at lines 379-390). That is a third mask, which Decision 6 does not allow; the plan's default is a named switch, on for CI and off for every judge run. Keeping it on for judge runs would change Decision 6 and needs the developer's sign-off. It fired in neither coverage pass (no "trailing whitespace ignored" line in either runner.log). The coverage run's hunks in scratch-tree.patch (`MAX_CASE_RETRIES = 0`, `--nodaemon`, `save_coverage_profiles` keyed by `SEEKDB_COV_PROFRAW_DIR`) exist only in the scratch tree; this item lands them properly. | yes | **done 2026-09-24:** judge options, recording and byte-exact `compare`; two reviewers on disjoint batches; smoke-tested on the reference (judge/harness/README.md; DEV-003) |
| 2 | Entry-gate report and pinned reference | Run init.sql and init_user.sql statement by statement (today `execute_init_sql`, line 123, pipes each file whole to obclient). Define a reduced init profile applied to both builds, and record the C++ reference again under it. Its goal: an init that the Step 2a narrow path can execute, so no `set_tp` lines and no system-package PL; it is fixed at 00b's gate, and changes only by a recorded amendment followed by re-recording the C++ reference. Build the reference as in "How the C++ reference is built", and check once that the 40 plan-bearing cases give the same text with and without `-ffp-contract=off` (section 10, action 9). | yes | **done 2026-09-24:** reference built and archived (judge/reference.md); the reduced init recorded twice, byte-identical on the 128 plain-SQL cases (judge/recordings.tsv); the 40 plan-bearing cases identical with and without the flag |
| 3 | Restart script | migration/judge/harness/restart_scenarios.py (restart-scenarios.md): scenarios restart_data, restart_parameters and restart_mid_dml against one base dir, recorded in the runner's format so `compare` diffs two builds. It starts and cleans up through `sdb.py`, but kills the server itself with SIGKILL (pid checked through sdb.py's own instance check) before `sdb.py stop`, because only the C++ build turns SIGTERM into `raise(SIGKILL)`: a Rust build that shut down cleanly on SIGTERM would otherwise pass without running crash recovery (Decision 9). Reviewed by Fable 5.1 (lifecycle) and Opus 5.5 (scenarios), 18 of 21 findings applied. | yes | **done 2026-09-24:** two runs on the archived reference, all three scenarios pass their own checks, and `compare` finds the two recordings identical (judge/harness/README.md) |
| 4 | `--ps-protocol` replay | The 272 cases replayed through the arm64 mysqltest in deps/3rd/u01/obclient/bin, first C++ against C++, then C++ against Rust. | no | in progress: the `--ps-protocol` runner option, written on a copy of the runner (judge/harness/second-set/) while the mutation runs use the live file |
| 5 | Embedded-contract tests | **Dropped** (Decision 8). | — | — |
| 6 | Plan-cache hit counter per case | Compared between the two builds. | no | in progress: the `--plan-cache-stats` runner option, on the same copy |
| 7 | Expression and cast differential generator | Modeled on origin/feature/rust's `rust/embedding-response/tests/compare_cpp.py` (error codes and every float bit over 4,000 seeded cases), borrowed as a pattern (Decision 15). | no | in progress: the generator under judge/families/expressions/ |
| 8 | Golden bytes for real contracts only | Checked against the source on 2026-09-24 (judge/golden-bytes-scope.md): the MySQL wire, recorded by a script with its own fixed-bytes client, including compressed-protocol frames (zlib) and a `--compress` replay of the 272 cases; `CRC32()` (zlib's CRC-32) and `COMPRESS()` output, as known-answer vectors in family 5; the files `SELECT ... INTO OUTFILE` writes and `LOAD DATA` reads with GZIP, DEFLATE and ZSTD. CRC32C has no external use and moves to the core build's tests with table-block compression, OB_UNIS, ObNumber and JSON binary (Decision 11). | no | scoped |
| 9 | Performance baselines | sysbench in Docker at 1, 16 and 64 threads, measured as migration/judge/performance-protocol.md describes (the server natively on the Mac with fixed `cpu_count=8` and `memory_limit=8G`, only the client in Docker, interleaved rounds, medians, load recorded per round); judge wall time. Cold start and binary size are recorded but not gated (Decision 1). Restart time is recorded and not gated either (decisions.md row 1a). The wasm start-up time is dropped (Decision 7). | no | **done 2026-09-24** (judge/perf/baseline-834bbee1e.md) |
| 10 | Coverage map from two runs | Measured at 076eb309b, coverage-instrumented, under the full init.sql (coverage README). 834bbee1e differs by 23 files (+342/-25), which cannot move the figures noticeably (coverage README). The figure stands once 00b explains the subquery difference (report §11). | yes | **stands at 64.4%** (the precondition met, judge/validation-834bbee1e.md) |
| 11 | The wasm path | **Dropped** (Decision 7). | — | — |
| 12 | Validation | The quarantine list below, named before any validation run; two runs of the 272 cases on the pinned reference with retries off, in which every case outside the list passes (compared with the checked-in .result files under the full init); at least 10 injected mutations, each caught ("How the injected mutations run", below). The two coverage passes ran, but the precondition failed; they must be repeated on 834bbee1e built plain (no coverage) with `-ffp-contract=off`, retries off. | yes | **the two passes done 2026-09-24** (judge/validation-834bbee1e.md); the 14 injected mutations running since 22:30 |
| 13 | Archive of the pinned C++ reference | Archive the built binary, deps/3rd (which holds the clang 17.0.6 of the plan's default toolchain), a copy of the SDK directory it was built against, reference-build.patch, obclient and mysqltest, and the recorded reference outputs; record the compiler version, the SDK version, `_LIBCPP_VERSION` and the macOS build ("How the C++ reference is built"). Check once that a clean rebuild from the archive gives the same compiler, SDK and `_LIBCPP_VERSION` and the same output on the 40 plan-bearing cases, and check again after any macOS or Command Line Tools update. deps/3rd is copied at build time and not tracked. The flagged binary is copied into the archive directory as soon as it is built (section 10, action 5), and every judge run uses that copy, never the reference worktree's build_release/; the rest of the archive and the rebuild check come before the injected mutations (action 11). | yes | **done 2026-09-24:** archive complete, the rebuild from it reproduces compiler, SDK, libc++ and the plan-bearing output (judge/reference.md) |
| 14 | Optional ASan (then TSan) build of the 272 cases | Its purpose was to inform Decision 1, which is answered. Optional; run it only if time is free. | no | optional |

Also in the 00b phase (not kit items, report §6): time `cargo check` and measure its memory on an existing 150-200K-line Rust crate on this Mac, to pin the check rate (section 10, action 14 names the crate and the command). The report's "merge the wasm branch's race fixes into C++" is dropped (Corrections).

### The parity scenario families

The report's 18 families (report §5), with the report's numbers kept. Families 10, 16 and 18 are dropped; 15 remain.

1. **Differential run of the 272 cases.** Fixed order and slice count, retries off, byte-for-byte diff of C++ against Rust; a second mode with a fresh instance per case. Quarantined cases take part as the quarantine list's last column says.
2. **Entry gate.** init.sql and init_user.sql statement by statement; the reduced init on both builds, with the C++ reference recorded under it; the 130 plain-SQL cases (migration/judge/lists/plain-sql.txt) under that profile.
3. **Plan text.** The 40 plan-bearing configured files (migration/judge/lists/plan-bearing.txt) run exactly and are reported on their own; then again with the EST mask (below). `rowset=` stays exact.
4. **Row order.** Ties under ORDER BY and the about 10,100 SELECTs without ORDER BY are compared exactly. Only the declared list of about 300 hash-order SELECTs (migration/judge/lists/hash-order-selects.txt) may be compared as sorted sets, under the row-order mask (below). This narrows the report's family 4, which fell back to sorted sets wherever the order differed; Decision 6 allows no mask beyond the declared list.
5. **Expressions and casts.** All 527 expression names, the 320 unreached ones first, against a type matrix with NULL and edge values; both cast matrices (src/sql/engine/expr/ob_datum_cast.cpp, src/share/object/ob_obj_cast.cpp); compare value, result type, warnings and error code. The matrix includes `now()` and other temporal values stored into DATETIME columns of every scale, since a quarantined case turns on how `now()` is truncated to whole seconds (investigations/subquery-datetime-rounding.md).
6. **Value formats.** The four arithmetic matrices (770,711 lines); golden bytes for `CRC32()`, `COMPRESS()` and the compressed files of `SELECT ... INTO OUTFILE` and `LOAD DATA` (judge/golden-bytes-scope.md); the error-catalog text through tools/ob_error/test/test.sh (18 `ob_error <code>` lines compared with expect_result.result), run with each build's `ob_error` first on the PATH, once the Rust tree builds that tool from the regenerated catalog.
7. **Plan-cache hits.** Hit and miss counts per case.
8. **Binary protocol.** The 272 cases replayed with `--ps-protocol`, C++ against C++ first, then C++ against Rust. The report's seekdb-async `exec_*` cases are dropped with the embedded form (Decision 8).
9. **Restart after a kill.** Start, load data, stop (a kill; see below), start again on the same base dir, then diff a fixed SELECT set and SHOW PARAMETERS; also kill the server in the middle of DML. Seeds: tools/obtest/t/fork_table/fork_table_restart_recovery.test (obtest itself does not run here, so the scenario is rewritten for the restart script), and the behavior the bindings' `ParameterPersistence.ChangedMemoryLimitSurvivesRestart` checks (ALTER SYSTEM SET memory_limit, SIGKILL, restart, the value survived), rewritten for server mode. The bindings and seekdb-async seeds themselves spawn `--embedded` and are not run (Decision 8).
10. **Embedded process contract.** **Dropped** (Decision 8).
11. **Concurrency.** The 69 multi-connection cases and the 6 send/reap cases; 4 slices in parallel on separate ports; sysbench `oltp_point_select` and `oltp_read_write` at 1, 16 and 64 threads; compare error codes and table checksums. The report's `run_two_concurrent_clients.sh` is dropped (Decision 8): it is /Users/colin/seekdb-dev/seekdb-bindings/lib/tests/run_two_concurrent_clients.sh, which loops the bindings gtest `TwoClientsOpen.TwoConcurrentClients` (test_two_clients_threads), and the bindings gtests start `seekdb --embedded`. The plan's proposal in its place, rewritten for server mode as family 9 rewrites the bindings' restart seed: start the server and have two obclient sessions connect while it is still starting, in a loop, and compare what each session sees.
12. **Memory budgets.** Drive each budget owner into failure and compare the error the client sees: -4013 from the clog allocator, -4030 memstore full, -11049 from the query tracker, -7603 from the vector limit, -4013 from the micro block cache after its retries, -4013 from hash-join partition depth (ob_hash_join_op.cpp:837-842). Also check spilling with `ob_sql_work_area_percentage=5`.
13. **Derived data.** The geometry (42), vector_index (22) and fts_index (15) suites compared through query results; a differential corpus for the IK tokenizer.
14. **Performance.** Measured as migration/judge/performance-protocol.md describes, so that a shared Mac affects both builds alike. Gate on the median QPS and latency against the C++ baselines on this Mac at 1.2x (decisions.md row 1a), and on judge wall time. Cold start, restart time and binary size are recorded, not gated (Decision 1; row 1a).
15. **Data version gate and cutover.** The Rust build refuses a C++ data dir, and refuses a v1.0.x dir that holds etc/observer.data_version.bin; it bootstraps an empty dir; export from C++ v1.4.x and import into Rust, covering VECTOR, GEOMETRY with SRID, JSON, LOBs, generated columns, and fulltext, vector and spatial index DDL (Decision 11).
16. **wasm.** **Dropped** (Decision 7).
17. **The judge checks itself.** At least 10 injected C++ mutations, each caught. Examples: flip a comparison in ob_opt_est_cost_model.cpp, drop an error path in ob_datum_cast.cpp, change SORT's tie handling, change ObNumber rounding, rename a DEBUG_SYNC point.
18. **Linux parity.** **Dropped** (Decision 7: no Linux machine, no Linux gate).

### The masks

Exactly as Decision 6 (b) records:
- **Exact is the main mode.** Masks are a short list declared in 00b, reviewed, and never added during Step 6.
- **The two masks:**
  - EST.ROWS and EST.TIME in the 40 plan-bearing files;
  - row order for the about 300 SELECTs whose order comes from hash output. 00b writes these down as an explicit list of statements before any Rust code exists.
- **How each mask works:** a named switch, off by default, validated C++ against C++. The exact run is always reported next to the masked run.
- **At the final gate** the masked classes run exactly once more, and the remaining differences are documented for sign-off.
- **Nothing else is tolerated.** The runner's trailing-whitespace tolerance is off for judge runs (item 1).
- Keeping the EST numbers exact at the end would need the storage estimator's inputs (block sizes, rows per micro and macro block, freeze thresholds) kept identical. That is a design-document constraint, open until Step 1 (section 8).

### The quarantine list

Named before any validation run; each entry with its reason; kept as migration/judge/quarantine.tsv. It governs validation of the C++ reference against the checked-in .result files: outside it, every case must pass. In the C++-against-Rust runs, the plan's default is not to exclude a quarantined case outright: a quarantined case is compared whenever the two C++ recordings on this Mac agree on it (checked at 00b's exit and again at Step 6's final gate), and stays out only while they disagree.

| Case | Reason | Source | Status | In the C++-against-Rust runs |
|---|---|---|---|---|
| `type_date.type_create_time` | Fails on this Mac every time against the Linux-recorded .result, for reasons unrelated to the code under test | seekdb-dev notes (report §4) | seed; failed in both coverage passes | compared if the two C++ recordings agree |
| `type_date.type_modify_time` | Passes or fails by luck (timing) | seekdb-dev notes | seed; failed in both coverage passes | compared if the two C++ recordings agree |
| `vector_index.sparse_vector_index_vsag_query` | Ordering in approximate vector search | seekdb-dev notes | seed; failed in both coverage passes | compared if the two C++ recordings agree |
| `histogram.stats_farm` | Fails now and then | seekdb-dev notes | seed; passed in both coverage passes, stays listed | compared if the two C++ recordings agree |
| `subquery.idx_with_const_expr_21_subquery_dilang` | Timing, from the source (action 4): `now()` and `current_timestamp()` without digits have scale 0 and are truncated to the whole second when evaluated, on the INSERT and on the SELECT alike (investigations/subquery-datetime-rounding.md, checked by a second reader); the column's half-up rounding then has nothing to round. So `date_add(current_timestamp(), interval -1 microsecond)` is the start of the select's second minus 1 microsecond, and the nine `-1 microsecond` selects (from test line 103) exclude the rows inserted by lines 55-57 whenever the inserts and the selects start in the same wall-clock second. In pass B the inserts ran at about 16:51:32.20 and the selects at about 16:51:32.86. A faster build fails more often, because a second boundary is less likely to fall between them | coverage README; pass-B diff; investigations/subquery-datetime-rounding.md | **pending: 00b confirms the cause before listing it (section 10, actions 4 and 7)** | compared if the two C++ recordings agree; the truncation of `now()` is also covered by family 5 |

### Where the judge's files live

The plan's default layout (the kit's 00b wants the harness under version control):
- **Tracked, under migration/judge/:** harness/ (the restart script, the differential and mask tools, family scripts); lists/ (the case lists of action 3, each with the command that produced it); census/ (the portable and internal-bound lists and counts); quarantine.tsv; reference-build.patch; reference.md (the toolchain record of item 13); reduced-init/ (the reduced init.sql and init_user.sql); mutations/ (one patch per injected mutation, with the case that catches it); recordings.tsv (each recording's path, the build and init it used, and a checksum); investigations/ (copies of tests changed for diagnosis, such as the subquery check).
- **Untracked, large:** run outputs and recordings under /Users/colin/seekdb-dev/mysqltest-runs/<name>/; the archive under /Users/colin/seekdb-dev/ref-archive-834bbee1e/; the reference worktree at /Users/colin/seekdb-dev/ref-834bbee1e. migration/judge/recordings.tsv and reference.md point at them.
- **Runner changes** go into .github/script/seekdb/mysqltest_for_seekdb.py itself (item 1).

### How the injected mutations run

The plan's default procedure for item 12 and family 17:
1. Complete the archive first (item 13), so the clean binary and its checksum are on record before any mutated build. Judge runs keep using the archived binary.
2. Apply one mutation as a saved patch (migration/judge/mutations/NN-<what>.patch) in the reference worktree, in a .cpp file rather than a widely included header, so the incremental rebuild stays short.
3. Rebuild incrementally, copy the mutated binary aside (about 193 MB; deleted after its run), and run all 272 cases under the full init with retries off, compared with the .result files and the quarantine list, as in the precondition run. The mutation counts as caught if at least one case outside the quarantine list fails; the patch's note records which cases caught it.
4. Revert the patch. After the last mutation, rebuild and confirm that the worktree's only difference from 834bbee1e is reference-build.patch.

Mutations for the later families (the expression generator, the `--ps-protocol` replay, restart, memory budgets) run the same way against the family they test, at the second 00b sign-off (below).

### How the server stops

Kill-only stops until parity (Decision 9 (a)). SIGTERM becomes `raise(SIGKILL)` (src/observer/ob_signal_handle.cpp:129-134), so every restart exercises crash recovery, and recovery must be exact from the first Rust build that restarts. A clean shutdown comes after parity, as its own flagged 06 change.

### 00b's exit

**The first sign-off, before Step 1** (report §6, adapted):
- two runs of the 272 cases on 834bbee1e built with `-ffp-contract=off`, retries off, in which every case outside the quarantine list passes (compared with the checked-in .result files under the full init), each quarantined case with a stated reason;
- the reduced-init recordings: two C++ recordings of the 130 plain-SQL cases under the reduced init that are byte-identical on every case outside the quarantine list (there are no .result files to compare with there);
- every injected mutation caught (at least 10);
- the 40 plan-bearing cases compared with and without `-ffp-contract=off`, and the result recorded (section 8, item 3);
- the core-set items built (1, 2, 3, 10, 12, 13);
- the reference archived and rebuilt once from the archive;
- the coverage numbers recorded; with the precondition met, the 64.4% figure stands (report §11);
- the kit's gate: the developer sees the harness, the portable and internal-bound counts (action 3's receipt), the reviewer findings and both validation runs, and signs off that the judge is real.

**The second sign-off, before Step 2a:** the other items (4, 6, 7, 8, 9; 14 if run) and the 15 families are built by the end of Step 1. Each has its own exit: a clean C++-against-C++ run where the family can run against C++ alone (families 14 and 15 need a Rust build and are checked when one exists), and at least one caught mutation of the behavior it tests. The developer signs these off together before Step 2a starts.

---

## 5. Go-conditions

### Before Step 1

| # | Condition (report §11, adjusted) | Status on 2026-09-24 |
|---|---|---|
| 1 | **The judge is validated (00b's first sign-off, section 4).** On 834bbee1e built with `-ffp-contract=off` on this Mac, two runs with retries off in which every case outside the quarantine list passes (compared with the checked-in .result files under the full init); every injected mutation is caught; the core-set items are built; the reference is archived and rebuilt once from the archive. | **Partly met on 2026-09-24:** the two validation passes on the archived reference agree and pass every case outside the quarantine list (judge/validation-834bbee1e.md); the subquery case is explained and quarantined; the runner fixes, the reduced init and its two identical recordings, and the flag check are done. The restart script (item 3) and the archive with its rebuild check (item 13, action 11) are done too (judge/harness/README.md, judge/reference.md). Still open: the injected mutations (action 12), running since 2026-09-24 22:30. |
| 2 | **The Gate A and Gate B decisions are made** (Decisions 1-16). | **Met** on 2026-09-24. The report also required "upstream's answer" here; Decision 2 chose the fork explicitly, so that part is replaced, not met. |
| 3 | **The C++ base is frozen** at a named commit, and each parallel branch is merged, reconciled or ruled out. | **Met:** 834bbee1e (Decision 3 (a)); all branches ruled out (Decision 15); the race-fix exception dropped (Corrections). |
| 4 | **The coverage fact comes out at about 60% of functions or more.** | **Met on 2026-09-24: 64.4% stands.** The subquery difference is explained (action 7) and the precondition holds (judge/validation-834bbee1e.md). 64.4% of functions over the core and the SQL tier together (core 65.6%, SQL tier 64.5%). Caveat: line coverage is about 10 points lower (55.4% together; 58.0% core, 54.9% SQL tier), so about 45% of lines run under no configured case, mostly error paths and rare branches. Executing a function is also weaker than checking its output. Measured at 076eb309b under the full init.sql, including its 12 `set_tp` lines; the parser is not counted (coverage README). |

### Before Step 2a

- **00b's second sign-off** (section 4, "00b's exit"): the other harness items and the 15 families, each with its own exit.
- **The design document is signed off** (Step 1's exit, section 6).
- **The query-speed ratio and the reading of "start-up time"** (section 8, item 8): **confirmed 2026-09-24**, 1.2x and restart recorded, not gated (decisions.md row 1a).
- **.claude/settings.json is installed,** adapted for seekdb (section 6, Step 2a).
- **Disk:** Decision 17 (a) keeps the Mac as it is. The plan's default adds a `df -h /` check against the 20-60 GB Rust target directory before the disposable full pass (section 7, "Disk").

### Before the core build and the Step 3 fan-out (after Step 2a)

Carried from report §11, adjusted:
1. **The narrow run passes** its named list of 20-50 single-table statements, diffed against the C++ reference under the reduced init. If it cannot without most of bootstrap, switch to Decision 10 (b).
2. **`unsafe` stays inside the named crates** (Decision 14), counted per crate.
3. **A scan+filter+aggregate query stays within 1.2x of C++** (median latency at most 1.2 times, median QPS at least 1/1.2; decisions.md row 1a, confirmed 2026-09-24).
4. **The disposable full pass's survey `cargo check`** shows an acyclic crate graph and an API-error count the developer signs off as absorbable by the design.
5. **The calendar is re-estimated** from what the Step 2a pilot measures (Decision 5 (b) notes): the sustainable agent runs per day under the subscription with Opus 5.5 everywhere, and the harness-counted tokens per run. These are inputs to the new calendar (section 7), not stop triggers; the report's triggers on tokens per run and on the processed-to-harness ratio belonged to its option (a), which Decision 5 did not choose. The developer then confirms Decision 5 (b) or remakes it.
   - Processed tokens are logged only if the harness exposes them under the subscription, in migration/usage.tsv (the plan's default; cost-log.tsv keeps the kit's six columns). If they cannot be measured, the ratio stays the report's inferred 5-18, and the runs-per-day figure alone drives the estimate, since it already reflects whatever the rate limit counts.
   - **No stop rule in calendar terms** (decisions.md row 5b, 2026-09-24): the re-estimated calendar is reported and the work goes on.

Per-crate `cargo check` time decides only whether Step 4 folds into Step 3 and whether Decision 16 reopens, not whether to go on.

### What would still stop the whole-port plan

Of the report's four triggers for "don't migrate the whole", three are answered: Decision 1 names the payoff, Decision 2 chose the fork on purpose, and the coverage fact measured above 60% (it stands once the precondition is met, go-condition 4). What remains: Step 2a fails condition 2 or 3 (broad `unsafe`, or clearly slower than the confirmed ratio), and switching to Decision 10 (b) does not fix it. In that case the report's fallback applies: keep the C++, add ASan and TSan builds, keep compiler warnings on, make `ObArray::operator[]` bounds-checked, move `ATOMIC_*` fields to real atomic types, and port only pieces that have their own differential judge (report §11).

---

## 6. The steps in order

1. 00b's core set; the go-conditions for Step 1 hold (section 5).
2. Step 1: design document, map and inventory. 00b's remaining items and families finish alongside and are signed off before Step 2a.
3. Step 2a: design review, one disposable full pass, one or two narrow end-to-end runs. Its results gate the core build and the fan-out.
4. The core build (a departure from the kit, run under prompt 04's discipline, then a compile loop and a run loop).
5. Step 2b: the leaf pilot. It starts once the core API list is signed off and may overlap the last weeks of the core module bodies (report §7); the Step 3 fan-out waits for the core build's full exit.
6. Steps 3-6, then prompt 06 and the cutover.

There is no upstream replay alongside any step (Decisions 2 and 3).

### Model plan

Decision 4 (c): Opus 5.5 for every implementer and every fixer; reviewer tiers as in report §8; Haiku 4.5 only for receipts. **Since 2026-09-24 every role Decision 4 and row 4a gave Fable 5.1 runs on Opus 5.5 (decisions.md row 4c)**, so the table below has no Fable 5.1 role; where the kit or the report pairs two different reviewer models, this plan runs two Opus 5.5 reviewers in separate contexts on disjoint batches.

| Phase | Model |
|---|---|
| 00b harness code and scenario scripts | Opus 5.5 |
| 00b corpus generation, mutation injection, log triage | Opus 5.5 (report §8 had Sonnet 5; see the note below) |
| 00b reviewers checking that no assertion was weakened | two Opus 5.5 runs, in separate contexts, on disjoint batches (before 2026-09-24: one Fable 5.1 and one Opus 5.5) |
| Design document and every amendment | Opus 5.5 |
| Adversarial review of the design document | two Opus 5.5 runs, in separate contexts |
| Core API design sessions with the developer | Opus 5.5 |
| Core module bodies (disposable and final runs) | Opus 5.5, with Opus 5.5 reviewers |
| Step 2a disposable full pass | Opus 5.5, one run per unit, no reviewers (report: Sonnet 5; an implementer run under Decision 4) |
| 01 dependency-map script and skeptics | Opus 5.5 |
| 02 classifiers | Opus 5.5 for every row family (report: Sonnet 5 for the mechanical families; see the note below) |
| 02 skeptics | Opus 5.5 for every row family, two per batch in separate contexts |
| 03 translators A and B, pilot implementer, pilot fixer | Opus 5.5 |
| 03 diff inspector / pilot reviewers | Opus 5.5 / Opus 5.5 |
| 04 implementers | Opus 5.5 for every unit |
| 04 reviewers | Opus 5.5 for every unit, including the units that print pinned text (cost model, printers, SORT, ObNumber, casts, errno text) or touch lock-free code |
| 04 and 05 fixers | Opus 5.5 |
| 05 reviewers; recurring error families | Opus 5.5; Opus 5.5 drafts and writes the amendment |
| Step 5 bootstrap debugging | Opus 5.5, including the hardest sessions (decisions.md row 4c) |
| Step 6 triage (inherited, regression, environment) | Opus 5.5 (report: Sonnet 5; see the note below) |
| Step 6 fixers | Opus 5.5, including the plan-text and float clusters and judge or comparator bugs (decisions.md row 4c) |
| 06 fixers / reviewers | Opus 5.5 / Opus 5.5 |
| Receipts (counts, slicing error lists, trailer checks) | Haiku 4.5 |

**Roles Decision 4 did not settle, or settled against the report** (decisions.md row 4a, 2026-09-24: the five support roles stay on Opus 5.5 and the three fixing roles below moved to Fable 5.1; row 4c, the same day, moved them and every other Fable 5.1 role to Opus 5.5):
- **Five roles moved from Sonnet 5 to Opus 5.5.** Report §8 used Sonnet 5 for five roles that are not implementers, fixers or reviewers: 00b corpus generation, mutation injection and log triage, the 02 mechanical classifiers, and Step 6 triage. Decision 4 says "Sonnet 5 is no longer used for translating or fixing", which would leave them on Sonnet 5; Decision 5's notes say "with Opus 5.5 everywhere, per Decision 4", which would move them. This plan uses Opus 5.5.
- **Three fixing roles on Opus 5.5.** Report §8 used Fable 5.1 for three fixing roles: Step 5's hardest sessions, Step 6's plan-text and float clusters, and Step 6's judge or comparator bugs ("a divergence needs reasoning over two implementations"). Row 4a moved them to Fable 5.1; row 4c moved them back to Opus 5.5 with every other Fable 5.1 role.

Settings: every subagent call sets the model and `effort: 'max'` explicitly, for every role (decisions.md row 4b, which replaces report §8's "set it explicitly, high for reviewers"); inheriting the session default is a deviation to log. Append one row per step to cost-log.tsv in the kit's six columns (`step`, `timestamp`, `wall_clock_min`, `tokens` harness-counted, `subagents`, `model`). Where the harness exposes the usage objects (input, cache-read, cache-write and output tokens), log them per subagent in migration/usage.tsv (the plan's default).

### Named departures from the kit

Recorded in the deviation log (migration/RULEBOOK.md, section 7) when each happens:
1. **00b runs before a signed-off "migrate" verdict.** The report's verdict was "migrate later"; the go-conditions in section 5 play that role. Record now.
2. **Step 0 ran three incremental rebuilds and a second Rust build** beyond the rubric's one build (report §12). Record now.
3. **Step 2a replaces the README's repeated disposable full runs** with one cheap full pass plus one or two narrow end-to-end runs. Record at Step 2a.
4. **The core build has no kit prompt;** it runs under prompt 04's discipline with subsystem-sized units and its own exit gate. Record at the core build.
5. **Prompt 03 runs in a changed form:** translator B gets the frozen core crates as ordinary dependencies, with their public API docs only, never the design document; and the files are chosen as the top three by risk score from each of two pools (SQL tier, other leaves) instead of the top `[3]` of one ranked list. Record at Step 2b.
6. **00b is split in two sign-offs.** The kit's 00b says "Nothing in Step 1 starts until I sign off that the judge is real". Here the core set (items 1, 2, 3, 10, 12, 13) is signed off before Step 1, and the other items and the families are built during Step 1 and signed off before Step 2a, each with its own clean C++-against-C++ run and caught mutation (section 4, "00b's exit"). Nothing translated depends on the later pieces before Step 2a. Record at 00b's first sign-off.
7. **A quarantine list instead of N/N.** The kit's 00b wants the harness to "pass clean" on the original, "N/N pass". Here cases that fail on this Mac for reasons unrelated to the code under test are named with reasons before any validation run, and every other case must pass (section 4, "The quarantine list"). Record at 00b's first sign-off.

### Before Step 1: build the judge (`prompts/00b-judge-setup.md`)

- **How it runs:** the prompt text is the contract, and section 10 lists its steps as they apply here.
- **Placeholders:** `[target language]` = Rust; `[reviewer model]` = two Opus 5.5 runs, in separate contexts and on disjoint batches (decisions.md row 4c; one Fable 5.1 and one Opus 5.5 before 2026-09-24).
- **Units:** 12 harness items (section 4) and 15 scenario families. The core set (items 1, 2, 3, 10, 12, 13) finishes before Step 1; item 10 is measured and waits on the precondition.
- **Also in this phase:** the performance baselines (item 9); the `cargo check` rate (section 10, action 14).
- **Exit:** section 4, "00b's exit" (two sign-offs, departure 6).
- **Usage:** harness items 12-34 tasks (the report's 14-40 scaled to 12 items; assumption) x 3 agent runs x 0.15-0.6M = 5-61M; corpus 5,000-20,000 generated statements ÷ 50 per batch = 100-400 batches x 2 runs x 0.15-0.6M = 30-480M; family debugging and validation 15 families x 5-20 sessions x 0.15-0.6M = 11-180M. Total about 0.05-0.72B harness-counted tokens. Machine time for the core set about 4-10 hours (section 7).

### Step 1: create the map and the rules (`prompts/01`, `prompts/02`, `templates/RULEBOOK.md`)

**Order within Step 1.** At 00b's first sign-off, the design document and the map (prompt 01) start together. The inventory (prompt 02) starts once a draft design document is committed as migration/RULEBOOK.md, since prompt 02's prerequisite is "draft RULEBOOK.md committed". 00b's remaining items and families run alongside.

**The design document.** Because this is a redesign, the rulebook becomes a design document, with templates/RULEBOOK.md as its skeleton (migration/RULEBOOK.md). Written by the developer with Opus 5.5 (decisions.md row 4c). It must decide:
- the crate graph, and the core's scope (narrow about 352K or wider about 600-650K);
- the error type, and the rules for error codes used as values;
- the arena and handoff rules;
- the IR ids, mirroring today's pointer identity one for one;
- the Rust mirror of `ParseNode`;
- the column batch and the storage filter trait;
- whether the storage estimator's inputs are kept identical (Decision 6);
- the storage and PX scan order that the about 7,500 unmasked single-table SELECTs depend on;
- the server context struct, and which process-wide actions stay in the binary crate (guideline, section 3);
- the out-of-memory policy exactly as Decision 12;
- the `unsafe` policy (Decision 14);
- the island ABIs, the kept C++ oblib subset under share/geo and S2, and how island memory is charged on macOS;
- sort and hash determinism, FMA (`mul_add` only where the C++ calls `fma`), overflow, and the judge build profile;
- stack growth on macOS, and whether the wasm depth-limit guideline is adopted now;
- the data-version bump and its three fixes (Decision 11);
- the toolchain (Decision 16), and which of the wasm-rule guidelines are adopted;
- the naming rules the manifest needs.

**The map** (`prompts/01-dependency-map.md`):
- **Placeholders:** `[your dependency mechanism]` = "C/C++ `#include` directives, resolving the 118 forwarding headers, plus link-level definition edges from `nm` over the built object libraries"; `[crate / package / module]` = "cargo crate in the 20-40-crate plan"; `[reviewer model]` = Opus 5.5.
- **Script:** an adapted copy of the kit's `scripts/depmap_c.py` (/Users/colin/repo/code-migration-kit-with-claude-code/scripts/depmap_c.py) at migration/scripts/depmap_seekdb.py, the plan's default path. It keys units by class rather than by path: 53 forwarder/.cpp pairs cross groups, and the query/api and data_plane/api headers are implemented elsewhere. The kit's copy skips only .git, build, vendor, third_party, node_modules and dot-directories, and reads only .c, .h, .cc, .cpp, .hpp, .cxx and .hxx. The adapted copy also skips deps/, every build_* directory and rust/ (with its target/), and also reads .ipp (41 files under src, such as src/storage/multi_data_source/mds_table_handle.ipp) and .def (4 files under src; some are included as X-macro lists, such as src/share/ob_lib_cache_namespace.def); there are no .inc files under src (counted while revising this plan).
- **Known cycles:** 27 header cycles over 69 files; one directory-level cycle of 110 directories and 4,108 files; 4 top-level include lines from d51422b54 (report §6; counted at 076eb309b).
- **Manifest rows.** The kit's `scripts/make_manifest.py` writes one `source`/`target` row per source path by string substitution, and `scripts/queue_runner.mjs` counts a unit as done when its target file exists; it reads only the first two columns. seekdb's units are .h/.cpp stems keyed by class, 150-400 pieces split out of the 54 files of 4K lines or more, and subsystem units for the core. So the manifest keeps `source` and `target` first, where `target` is the one output file the unit writes (a split piece gets its own target module), and adds `unit_id`, `kind` (`stem`, `split` or `subsystem`) and `inputs` (every source file or line range the unit covers). queue_runner.mjs runs unchanged on it; the rows are written by an adapted copy, migration/scripts/make_manifest_seekdb.py (the plan's default path).
- **Closing action:** `python3 migration/scripts/make_manifest_seekdb.py --order migration/depmap/order.txt --out migration/manifest.tsv` with `--sub` pairs from the design document's naming section. The core gets its own manifest, migration/core-manifest.tsv, in the same format.
- **The declaration index** (a Step 1 deliverable, used by every Step 3 unit): for each unit, the declarations its one-level includes provide, written to migration/decl-index/<unit_id>.txt by migration/scripts/decl_index.py (the plan's default paths). The tool it reads declarations with (for example clang's AST dump over the build's compile commands) is chosen in Step 1; the 01 skeptics check a sample of units against their raw headers.
- **Units:** 3,919 at a 30K-token cap, or 4,137 at a 20K cap, before removing the core and island stems.

**The inventory** (`prompts/02-gap-inventory.md`):
- **Placeholders:** `[name your gap]` = "ownership and lifetimes: arena memory handed to other threads, tasks and caches; borrowed `ObString`/`ObDatum` views; hand-counted handles. Also atomics on plain fields, error codes used as values, sort and hash order, pointer identity, integer overflow and float contraction"; `[reviewer model]` = Opus 5.5 for every row family (decisions.md row 4c).
- **Sweep lists** (report §6): about 823 `ATOMIC_*` field names; 441 `inc_ref`/`dec_ref` lines and 130 Handle classes; 1,750 `const_cast` lines; 4,189 `OB_X == ret` lines; 2,905 resets; about 1,560 `tmp_ret` lines; 31 uses of `int &ret = ret_;` in 18 files, including the two unfixed comparators; about 40 budget-backed out-of-memory sites plus the logical -4013 errors, and the micro block cache's FIFO; 122 `server_service` slot types; the base classes upper modules subclass (`ObTimerTask` 72, `ObDLinkBase` 72, `ObFuncExprOperator` 313).
- **Expected size:** about 6K-40K rows (assumption).

**Exit:** two clean skeptic rounds for the map; an acyclic crate graph; the manifests and the declaration index written; an inventory row for every swept site; the design document signed off by the developer; 00b's second sign-off, with the remaining items (4, 6, 7, 8, 9; 14 if run) and the 15 families built (section 4, "00b's exit").

**Usage:** inventory 300-2,000 batches x 3 runs x 0.15-0.4M = 0.14-2.4B; map 30-100 runs x 0.15-0.6M = 0.005-0.06B; design 40-150 sessions x 0.3-1M = 0.012-0.15B; total about 0.15-2.6B harness-counted tokens (report §6 gave 0.16B at the low end; the three low ends add up to 0.15B).

### Step 2a: stress-test the design, before the core is built

The bakeoff does not apply to the core (README, "If you're redesigning"). Before this step the developer installs `templates/settings.json` as .claude/settings.json, because the disposable full pass is the first fan-out (the kit wants it before any translation fan-out, kit README Quick start step 6).

**Who builds and runs things once the denies are live.** The template denies `cargo build`, `cargo check`, `cargo test`, `cargo run`, `make` and `cmake` in every session in the repo, and the kit's CLAUDE.md forbids working around a live deny. So:
- **Adapt the template for seekdb** (templates/settings.README.md: "substitute your target's build/test commands"): also deny the seekdb build (`bash build.sh`, `./build.sh`), the mysqltest runner and `sdb.py start` in loop sessions. The exact patterns are chosen when the file is installed.
- **Builds and judge runs go through the build daemon,** an adapted copy of the kit's `scripts/build_daemon.sh` at migration/scripts/build_daemon.sh (the plan's default path), started by the developer with `--cmd`. The kit's copy hashes the whole tree except .git, target, node_modules, dist, build and migration every 30 s, which here would include deps/3rd (5.9 GB) and build_release; the adapted copy also skips deps/ and every build_* directory. Its `--cmd` may chain a build and a judge run (for example `cargo build --profile judge -p <narrow binary> && <statement diff>`), so fixers read both from migration/build-output-r<N>.txt.
- **One-off measurements** (per-crate `cargo check` time and memory, the incremental rebuild and link time) are run by the developer, or through the daemon with `--once`.

No kit prompt covers this step as a whole; it replaces the bakeoff with:
1. **Adversarial review of the design document** by two Opus 5.5 runs in separate contexts.
2. **One cheap disposable full pass.** Every one of the 2,700-3,400 Step 3 units gets one Opus 5.5 implementer run against the design document, with the core API written as stubs; no reviewers, no compiler in the loop. Then one survey `cargo check` counts cross-crate cycle errors and API errors, and the run is thrown away. It shows whether the crate graph is acyclic over the whole tree, whether the typed-ID IR survives the about 580 `find_item` call lines and the 34 rules, and which API the leaves need that the design lacks. **Run the first 100-unit batch alone and measure the sustainable runs per day before committing to the rest:** at 50 runs a day the pass alone takes 54-68 days (section 7).
3. **One or two narrow end-to-end runs** of the core path, thrown away after measuring, each with a compile loop in the form of prompt 05 through the build daemon: sql-nio, the C parser core over FFI with its tree converted to the owned Rust AST, the new IR for single-table SELECT, INSERT and CREATE TABLE, typed column batches and the storage filter trait, down to memtable-only storage. Pass criterion: a named list of 20-50 single-table statements from the plain-SQL cases, diffed against the C++ reference under the reduced init. CREATE TABLE alone reaches `ObDDLService` (src/rootserver/ob_ddl_service.cpp, 23,564 lines), the schema service and the build-time inner-table schema; whether a minimal schema path exists without most of bootstrap is Decision 10's switch condition.

This plan drops the report's fourth item, translating the three SQL-tier pilot files structure-preserving to count `unsafe` per 1,000 lines for Decision 10 (d): Decision 10 keeps only the switch to (b), and its notes say (d) lost its main advantage with Decision 2.

**What Step 2a measures:**
- the `unsafe` count outside the named crates;
- `cargo check` time and memory per crate;
- the incremental rebuild and link time in the judge build profile after a foundation-crate edit (the Step 6 referee price, assumed 2-30 min);
- the speed of a scan+filter+aggregate query against C++;
- harness-counted tokens per unit, and processed tokens where the harness exposes them;
- the sustainable agent runs per day under the subscription with Opus 5.5 (this replaces the report's "how many agents can run at once");
- the size of the Rust target directory against the free disk (Decision 17);
- whether the named statement list passes without system packages, inner-SQL schema loading or virtual tables;
- the first real count of compile errors for Step 4.

**Exit:** the conditions in section 5, "Before the core build and the Step 3 fan-out".

**Usage:** design review 2 reviewers x 5-15 rounds x 0.3-1M = 0.003-0.03B; full pass 2,700-3,400 units x 1 run x 0.15-0.6M = 0.4-2.0B; narrow runs 1-2 runs x 100-400 units x 2-4 agent runs x 0.15-0.5M = 0.03-1.6B; total about 0.44-3.7B harness-counted tokens (report §6 gave 0.43-3.6B; the three parts add up to 0.438-3.67B).

### Between Step 2a and Step 2b: build the core (a departure from the kit)

- **A queue on disk:** `migration/core-manifest.tsv`, listing subsystem units (foundation, runtime, IR, execution framework, storage boundary, storage and transaction core) and the stems inside each.
- **The developer and Opus 5.5 write the APIs by hand.** Where agents write bodies, prompt 04 runs over the core manifest with "file" swapped for "unit": Opus 5.5 implementers, two reviewers (Opus 5.5), Opus 5.5 fixers, the settings.json denies and the batch gates of Step 3.
- **Order:** (1) foundation and runtime crates: error, arena, bytes, context, logging, config, thread and timer primitives, IO; (2) the IR; (3) the execution framework and code generator; (4) the storage boundary, the tablet/memtable/transaction core, and a single-writer WAL.
- **Value libraries** (ObNumber, time, charset, JSON, casts) fan out as soon as the foundation API is frozen, each with differential tests against C++. Those tests also cover the OB_UNIS, ObNumber and JSON binary bytes moved out of the judge.
- **Compile loop, then run loop, before the exit.** Prompt 04 has no compile or run step, so after the bodies are written: a compile loop in the form of prompt 05 (survey build through the build daemon, the error list sliced by crate, Opus 5.5 fixers without compiler access) until the core crates build; then a run loop in which the daemon's `--cmd` builds the binary and runs the 130 plain-SQL cases differentially under the reduced init (`--case-list migration/judge/lists/plain-sql.txt`), and Opus 5.5 fixers work from the outputs, triaged as in Step 6.
- **Exit, signed off by the developer:** a frozen core API list; the `unsafe` count per crate, zero outside the named crates; the 130 plain-SQL cases pass under the reduced init, diffed against the C++ reference; the confirmed speed ratio holds; `cargo check` time per crate recorded.
- **Usage:** by hand, 200-800 sessions (400-1,200 hours of attention) x 0.3-1M = 0.06-0.8B; if agents write the bodies, 550-1,050 units x 4.2 agent runs x 0.2-0.6M x 1-2 runs (disposable, then final) = 0.46-5.3B harness-counted tokens (report §6).

### Step 2b: stress-test the rules for the leaves (`prompts/03-stress-test.md`, changed)

Starts once the core API list is signed off, and may overlap the last weeks of the core module bodies (report §7); the Step 3 fan-out waits for the core build's full exit. The report ran 03 twice, once per implementer tier; with Opus 5.5 as the only implementer (Decision 4), it runs once.
- **Placeholders:** `[3]` = 6, the top three by risk score from each pool; `[target language]` = Rust; `[target formatter]` = rustfmt; `[implementer model]` = Opus 5.5; `[reviewer model]` = Opus 5.5, with an Opus 5.5 diff inspector (decisions.md row 4c).
- **Candidate pools** (report §6): SQL tier: a slice of ob_transform_utils.cpp (pointer identity), an expression that reads session state and uses casts, a slice of ob_join_order.cpp (cost floats). Other leaves: a slice of ob_ddl_service.cpp (schema guards and handles), a virtual table, a PL interpreter file. The two pools exercise different design-document sections, which is why both stay.
- **How 03 runs here:** the pilot half as written (the production pipeline on the files, graded on adherence). The bakeoff half in the changed form in "Named departures", item 5; a difference caused by how each translator uses the core API is a finding about the API, not about the rules.
- **Usage:** 1-2 rounds x 6 files x about 7 agent runs x 0.15-0.6M = 0.006-0.05B harness-counted tokens.

### Step 3: translate everything (`prompts/04-translation-kickoff.md`, `scripts/queue_runner.mjs`)

- **Placeholders:** `[100]` = 100; `[TODO(port)]` = `TODO(port)`, `PERF(port)`, `BUG(port)`; `[implementer model]` = Opus 5.5 for every unit; `[reviewer model]` = Opus 5.5 for every unit (decisions.md row 4c). Fixers: Opus 5.5.
- **What each unit gets:** its stem (median 2.6K tokens, mean 6.6K); its entry in the declaration index (migration/decl-index/, a Step 1 deliverable) in place of raw headers (the one-level include context has a median of 25.6K tokens, the full closure a median of 1.43M, so raw headers cannot be shown); the design document and the frozen core API; its inventory rows.
- **The queue:** `node /Users/colin/repo/code-migration-kit-with-claude-code/scripts/queue_runner.mjs --manifest migration/manifest.tsv next --batch 100`, run from the repository root; the manifest's extra columns (Step 1, "Manifest rows") give each unit its inputs.
- **Order:** the crate graph, leaves to root.
- **Excluded:** the core, data, generated, vendored and dead files, and the islands themselves.
- **Exit:** the queue is empty, and each file's `grep -c 'TODO(port)'` equals its trailer.
- **Units and runs:** 2,700-3,400 units x 4.2 agent runs (implementer, 2 reviewers, fixer, an arbiter on about 20%) = about 11,300-14,300 agent runs; with 25-50 island-shim units (about 100-210 runs), about 11,400-14,500 in all. The report gave 11,000-14,500 for the units alone.
- **Usage:** SQL tier 1,200-1,400 units x 4.2 x 0.3-1.0M = 1.5-5.9B; other leaves 1,300-2,200 x 4.2 x 0.15-0.6M = 0.8-5.5B; island shims 25-50 x 4.2 x 0.3-1.0M = 0.03-0.21B; total about 2.4-11.6B harness-counted tokens (report §6 gave 2.3B at the low end; the three low ends add up to 2.36B). The tier bands do not add back to the unit total (section 3, "Leaf code translated file by file").
- **Wall clock:** set by the sustainable runs per day (section 7).

### Step 4: compile (`prompts/05-survey-build.md`, `scripts/build_daemon.sh`)

- **Placeholders:** `[build command]` = `cargo check --workspace --message-format=short`; `[module]` = crate; `[fixer model]` = Opus 5.5; `[reviewer model]` = Opus 5.5.
- **The daemon:** migration/scripts/build_daemon.sh (Step 2a), started by the developer with that command as `--cmd`.
- **Folding into Step 3:** only per crate, and only if Step 2a measured a crate check at 60 s or less. Even then, cargo's lock on a target directory and the disk and RAM limits argue for one build daemon.
- **Error count:** 20K-100K is an unanchored assumption; Step 2a's full pass gives the first real count.
- **Usage:** 20K-100K errors ÷ 25 per slice = 800-4,000 slices x 3 agent runs x 0.15-0.5M = 0.36-6B harness-counted tokens. Referee: 20-80 rounds x 3-15 min per clean check = 1-20 hours of daemon time (report §6).

### Step 5: run it (no kit prompt)

- **Hello world:** the Rust binary bootstraps an empty `--base-dir`; sql-nio answers `select 1` on run/sql.sock; init.sql and init_user.sql run statement by statement with zero errors.
- **Smoke:** the 272 cases' entry gate under the full init, then the 130 plain-SQL cases differentially under the full init (they already passed under the reduced init at the core build's exit). The report's third smoke stage, the bindings and seekdb-async suites, is dropped (Decision 8).
- **Usage:** 100-500 hands-on sessions x 0.3-1M = 0.03-0.5B harness-counted tokens.

### Step 6: match behavior (the 00b judge, then `prompts/06-post-parity.md`)

- **Denies for the fix loops:** before the fix loops start, the test denies are active again in .claude/settings.json (`cargo test`, the mysqltest runner, `sdb.py start`); fixers work read-only from failure evidence, and only the build daemon or the developer re-runs the judge (kit README, Quick start step 6; templates/settings.README.md).
- **Triage:** run every failure on the pinned C++ build and classify it as inherited, regression or environment.
- **The judge build profile:** `opt-level` 1-2, overflow checks off as in release, many codegen units, no LTO, incremental on. Debug builds cannot stand in: overflow panics in debug, and unoptimized Rust is too slow for the 272 cases.
- **Done-gate:** every parity scenario passes on this Mac (there is no Linux gate, Decision 7); the masked classes run exactly once more and the remaining differences are documented (Decision 6); the pinned C++ re-run shows zero inherited failures outside the quarantine list, with the quarantined cases and their reasons documented; both counts are documented and signed off by the developer.
- **After the gate:** `prompts/06-post-parity.md` with `[target tree]` = rust/ and `[reviewer model]` = Opus 5.5, fixers Opus 5.5. Kill-only stops stay until then; the clean shutdown is its own flagged 06 change (Decision 9).
- **Usage:** 300-2,000 failure clusters (assumption) x 3 agent runs x 0.2-0.8M = 0.18-4.8B harness-counted tokens; 06: 500-2,000 markers x 2 x 0.15-0.25M = 0.15-1B (assumption).
- **Referee:** 300-2,000 clusters x 1-3 targeted checks x 3-35 min = 15-3,500 machine-hours, plus 20-60 full rounds x 1.75-3.25 h = 35-195 h (report §6). Decision 17 (a) keeps one Mac, so the report's relief of adding a second machine is gone. At the top of the band, one Mac needs about 28-37 weeks for Step 6, not the report's 26 (section 7, "Calendar"; section 8, item 13).

### Cutover

1. Bump `DATA_CURRENT_VERSION` (src/oblib/common/ob_version_def.h:53).
2. Move the version check ahead of the meta.db open (src/observer/ob_server.cpp:1698-1719).
3. Close the missing-file hole (src/oblib/common/ob_data_version_mgr.cpp:58-60, 88-94).
4. Test a round trip: export from C++ v1.4.x, import into Rust (family 15).
5. Print a startup message that tells users what to do.

### If Decision 10 switches to (b) at the end of Step 2a

The in-binary switch adds seam work (report §6): bridges for the base classes other modules subclass (`ObTimerTask` 72, `ObDLinkBase` 72, `ObIReplaySubHandler` 14, about 160 subclasses); the palf pilot (32,705 lines, about 50 stems); the C++-side seam refactors, landed under the unchanged judge; an umbrella staticlib; byte agreement on clog and slog inside every mixed release. Usage (assumption): 200-400 seam units x 4.2 x 0.15-0.6M = 0.13-1.0B harness-counted tokens, 2-4 more months, 150-400 more hours of attention.

---

## 7. Effort and duration

Decision 5 (b): a subscription, where rate limits set the pace and there is no dollar ceiling. The budget becomes calendar time. The dollar tables of report §7 no longer apply and are not carried over.

### How usage is counted

- **Harness-counted tokens** are the usage measure and the counter cost-log.tsv records. They behave like new tokens (context written once, plus output). Calibration: step 0 logged 13,049,394 tokens over 55 subagents (cost-log.tsv), about 0.24M per subagent; the survey alone was 8.92M over 43 (report §7), about 0.21M.
- **Processed tokens** are every input token on every turn, cache reads included, plus output. From turns x average context, the report infers processed ≈ 5-18 x harness-counted (report §7, an inference, not a measurement). Rate limits probably follow something closer to processed tokens (assumption). Step 2a measures the ratio where the harness exposes the usage objects (migration/usage.tsv); if it cannot, the ratio stays inferred (section 5).
- **An agent run** in this plan is one implementer, reviewer, fixer or arbiter run: 0.15-0.6M harness-counted tokens (0.3-1.0M for the SQL tier), so about 0.75-11M processed (1.5-18M for the SQL tier) (report §7).

### Tokens per step

| Step | Multiplication (section 6) | Harness-counted | Processed (x5-18, inferred) |
|---|---|---|---|
| 00b | 12-34 tasks x 3 + 100-400 corpus batches x 2 + 15 families x 5-20 sessions, each x 0.15-0.6M | 0.05-0.72B | 0.25-13B |
| Step 1 | 300-2,000 inventory batches x 3 x 0.15-0.4M, plus the map (30-100 runs x 0.15-0.6M) and the design (40-150 sessions x 0.3-1M) | 0.15-2.6B | 0.75-47B |
| Step 2a | review, plus full pass 2,700-3,400 x 1 x 0.15-0.6M, plus 1-2 narrow runs x 100-400 units x 2-4 x 0.15-0.5M | 0.44-3.7B | 2.2-66B |
| Core build | 200-800 sessions x 0.3-1M (by hand), or 550-1,050 units x 4.2 x 0.2-0.6M x 1-2 (agents write bodies) | 0.06-5.3B | 0.3-95B |
| Step 2b | 1-2 rounds x 6 files x 7 runs x 0.15-0.6M | 0.006-0.05B | 0.03-0.9B |
| Step 3 | SQL tier 1,200-1,400 x 4.2 x 0.3-1.0M; other leaves 1,300-2,200 x 4.2 x 0.15-0.6M; island shims 25-50 x 4.2 x 0.3-1.0M | 2.4-11.6B | 12-209B |
| Step 4 | 800-4,000 slices x 3 x 0.15-0.5M | 0.36-6B | 1.8-108B |
| Step 5 | 100-500 sessions x 0.3-1M | 0.03-0.5B | 0.15-9B |
| Step 6 | 300-2,000 clusters x 3 x 0.2-0.8M | 0.18-4.8B | 0.9-86B |
| 06 post-parity | 500-2,000 markers x 2 x 0.15-0.25M | 0.15-1B | 0.75-18B |
| **Total** | | **about 3.8-36B** | **about 19-650B** |
| If Decision 10 switches to (b) | 200-400 seam units x 4.2 x 0.15-0.6M | +0.13-1.0B | +0.65-18B |

Four figures differ slightly from the report's (Step 1's 0.16B, Step 2a's 0.43-3.6B, Step 3's 2.3B, the total's 3.7B) because this plan adds the parts before rounding. Moving implementers and fixers from Sonnet 5 to Opus 5.5 does not change these counts (assumption: tokens per run depend on the unit, not the model). Under a subscription it shows up as fewer runs per day instead (Decision 4 notes).

### Wall clock for the fan-out steps under the subscription

The sustainable agent runs per day under the subscription, with Opus 5.5 everywhere, are unknown until the Step 2a pilot measures them (Decision 5). The three rates below are assumptions, chosen to span the likely range. For scale: the report's API assumption (20-80 runs at once, 10-30 min per run) came to 20 x 48 = 960 to 80 x 144 = 11,520 runs a day, so even 400 a day is well below its low end.

**Step 3, the multiplication:**
- Agent runs: 2,700-3,400 units x 4.2 = about 11,300-14,300, plus 25-50 island-shim units x 4.2 = about 100-210: about 11,400-14,500 (section 6, Step 3).
- At 50 runs a day: 11,400 ÷ 50 = 228 days, up to 14,500 ÷ 50 = 290 days.
- At 150 runs a day: 11,400 ÷ 150 = 76 days, up to 14,500 ÷ 150 ≈ 97 days.
- At 400 runs a day: 11,400 ÷ 400 ≈ 29 days, up to 14,500 ÷ 400 ≈ 36 days.
- Plus 27-34 batch gates (100 units a batch) x 0.5-1 day each = 14-34 days (report §6; assumption).
- **Step 3 in total:** about 242-324 days (35-46 weeks) at 50 a day; 90-131 days (13-19 weeks) at 150; 43-70 days (6-10 weeks) at 400. The report's API estimate was 2-7 weeks.

**Every fan-out step, in days of agent runs:**

| Step | Agent runs | 50 a day | 150 a day | 400 a day |
|---|---|---|---|---|
| 00b harness tasks (the core set among them) | 12-34 tasks x 3 = 36-102 | 1-2 | under 1 | under 1 |
| Step 1 inventory and map, with 00b's remaining corpus and family runs alongside | 300-2,000 batches x 3 + 30-100 = 930-6,100, plus 200-800 corpus + 75-300 family sessions = 1,205-7,200 | 24-144 | 8-48 | 3-18 |
| Step 2a | 10-30 review + 2,700-3,400 full pass + 200-3,200 narrow = 2,910-6,630 | 58-133 | 19-44 | 7-17 |
| Core build, if agents write bodies | 550-1,050 x 4.2 x 1-2 = 2,310-8,820 | 46-176 | 15-59 | 6-22 |
| Step 2b | 1-2 rounds x 6 files x 7 = 42-84 | 1-2 | under 1 | under 1 |
| Step 3 | about 11,400-14,500 | 228-290 | 76-97 | 29-36 |
| Step 4 | 800-4,000 slices x 3 = 2,400-12,000 | 48-240 | 16-80 | 6-30 |
| Step 6 | 300-2,000 clusters x 3 = 900-6,000 | 18-120 | 6-40 | 2-15 |
| 06 post-parity | 500-2,000 markers x 2 = 1,000-4,000 | 20-80 | 7-27 | 3-10 |
| **Whole port** (adding Step 5's 100-500 sessions; the core build by hand at the low end, by agents at the high end) | **about 20,000-60,000** | **400-1,200** | **133-400** | **50-150** |

What follows from this:
- When the rate limit binds, running two fan-outs at the same time saves nothing, because they draw on one budget (assumption: all models share one subscription budget). So Step 4's fixer runs no longer overlap Step 3 for free, and the report's "about half of Step 4 overlaps Step 3" holds only at the high rate. For the same reason the Step 1 row carries 00b's remaining corpus and family runs, which run alongside it.
- Two other kinds of work run alongside and are assumed to fit inside the rate (assumption): the value-library fan-out and Step 2b alongside the core build (if agents write the core's bodies, their 2,310-8,820 runs take 46-176 days at 50 a day, about 7-25 weeks, inside the core build's 13-39 weeks), and the design-document sessions (40-150) and core API sessions (200-800), now on Opus 5.5 (decisions.md row 4c), a few a day.
- Step 2a's full pass is itself 2,700-3,400 runs: 54-68 days at 50 a day. That is why section 6 measures the rate on the first 100-unit batch.

### Calendar

The report's critical path (report §7) was about 43-124 weeks, or 10-29 months, plus up to 5 months of replay pauses: 10-34 months. It no longer holds: Gate A is done, replay is gone, and the fan-out steps now depend on the runs per day. Recomputed per scenario, taking for each step on the critical path the larger of the report's band and the time its agent runs need at that rate (weeks; about 4.3 weeks a month):

| Step on the critical path | Report (API) | 50 a day | 150 a day | 400 a day |
|---|---|---|---|---|
| 00b core set | 3-6 | 3-6 | 3-6 | 3-6 |
| Design document (map, inventory and 00b's remaining runs alongside) | 6-16 | 6-21 | 6-16 | 6-16 |
| Step 2a | 3-8 | 8-19 | 3-8 | 3-8 |
| Core build (by hand; set by the developer's hours) | 13-39 | 13-39 | 13-39 | 13-39 |
| Step 3 (runs plus batch gates) | 2-7 | 35-46 | 13-19 | 6-10 |
| Step 4 (report: the half not overlapping Step 3; at 50 and 150 a day, all of it) | 1-4 | 7-34 | 2-11 | 1-4 |
| Step 5 | 3-10 | 3-10 | 3-10 | 3-10 |
| Step 6 (set by machine time; the report with a second machine, this plan with one Mac) | 9-26 | 9-37 | 9-37 | 9-37 |
| 06 and the cutover | 2-6 | 3-11 | 2-6 | 2-6 |
| **Total** | **42-122 weeks** (the report's 43-124 without Gate A) | **87-223 weeks (about 20-52 months)** | **54-152 weeks (about 13-35 months)** | **46-136 weeks (about 11-32 months)** |

**Step 6 on one Mac.** The report's 9-26 weeks (2-6 months) relied on a second machine at the top of the 50-3,700 machine-hour band (report §6). Decision 17 (a) keeps one Mac. Take 60-80% of the week's 168 hours as usable for judge work (assumption: the rest goes to the developer's own work on the same machine), and the targeted checks as running one at a time, since each needs its own incremental rebuild through the one cargo daemon (assumption). Then 3,700 machine-hours take 3,700 ÷ 134 ≈ 28 weeks up to 3,700 ÷ 101 ≈ 37 weeks, and the calendar takes the top, 37 weeks. The report's 26 weeks still holds while Step 6 needs less than about 2,600-3,500 machine-hours (26 weeks at 60-80% of the week).

The core build's band is set by the developer's hours and does not move with the rate. The design document's 6-16 weeks moves only at 50 runs a day, where the map, the inventory and 00b's remaining runs outlast it (up to 21 weeks). At 50 runs a day the rate limit, not the developer, sets the calendar.

### Active attention

From report §7, without the rows for the decisions (done) and for upstream replay (Decisions 2 and 3). All are assumptions for one developer. Three rows are carried unchanged although the decisions removed work inside them, so they are upper figures (not reduced for the dropped scope; assumption): 00b's remaining items and families (the report's figure included embedded item 5 and family 10, wasm item 11 and family 16, and Linux family 18), Step 5 (the bindings and seekdb-async smoke suites), and Step 6 (the Linux done-gate).

| Step | Active attention |
|---|---|
| 00b core set | 30-80 h |
| 00b remaining items and families (alongside the design; not reduced) | 20-70 h |
| Step 1: design document | 120-300 h |
| Step 1: map and inventory | 20-40 h |
| Step 2a | 40-150 h |
| Core build | 400-1,200 h |
| Step 2b | 10-30 h |
| Step 3 (mostly the 27-34 batch gates) | 20-60 h |
| Step 4 | 20-100 h |
| Step 5 (not reduced) | 40-250 h |
| Step 6 (not reduced) | 100-500 h |
| 06 post-parity | 20-60 h |
| Cutover | 10-30 h |
| **Total** | **about 850-2,870 h** |
| If Decision 10 switches to (b) | adds 150-400 h |

The core build is the largest and most intense block, about 31 hours a week at either end of its band; the design document runs at about 19-20 hours a week (report §7).

### Machine time

From report §7, without the replay row:

| Step | Machine time |
|---|---|
| 00b core set | About 4-10 h; the multiplication is below the table |
| Step 2a | 1-3 survey `cargo check`s x 3-15 min, plus the narrow runs' builds and statement diffs: a few hours to a day |
| Step 4 | 20-80 rounds x 3-15 min, plus cascades: 1-20 h |
| Step 5 | release builds of 15-45 min x tens of iterations: days |
| Step 6 | 50-3,700 h; on one Mac the top takes about 28-37 weeks ("Calendar") |

Measured since the report: one pass of the 272 cases on this Mac took 1,454 s and 1,464 s (about 24 min) on the coverage-instrumented binary, single slice (coverage README, timings.txt); the instrumented build took 582 s; the plain build took 314 s at 076eb309b (report §4).

**00b's core set, run by run** (section 10). A full pass of the 272 cases on the plain flagged build is taken as 15-30 min (assumption, around the measured 24 min of the instrumented binary); other runs scale with their case count. Builds are taken as 5-10 min each (assumption, from the 314 s plain build at 076eb309b) and a mutation's incremental rebuild as 2-10 min (assumption).
- Two reference builds, without and then with the flag (action 5), and one rebuild from the archive (action 11): 3 x 5-10 min = 15-30 min.
- The subquery case run 20 times, plus 20 runs of its diagnostic copy (action 7): about 10-20 min (the one-case dry run took 28 s).
- Two validation passes (action 8): 2 x 15-30 min = 30-60 min.
- The flag check, the 40 plan-bearing cases on two binaries (action 9): about 5-10 min.
- Two reduced-init recordings of the 130 plain-SQL cases (action 10): 2 x 7-15 min = 14-30 min.
- At least 10 mutations, each an incremental rebuild and a full pass (action 12): 10 x (2-10 + 15-30) min = 170-400 min, plus a clean rebuild at the end, 5-10 min.
- Total: about 250-560 min, so about 4-10 h. The report's 8-20 h counted 30-80 min per run and two coverage passes, which are done.
- Not counted: the sysbench baselines (item 9) and the later items and families, whose runs are not estimated.

### Disk

Decision 17 (a) keeps the Mac, recorded with 33 GiB free after the coverage build. A `df -h /` while this plan was revised showed 50 GiB available; the difference is not explained, so every action that builds checks `df -h /` before and after (section 10), and the check is repeated before Step 2a and during its pilot.

What 00b adds (section 10, actions 5-12):
- the reference worktree: a checkout of about 0.4 GB (the migrate-to-rust tree without build_release, deps/3rd and rust/target measured 366 MB);
- deps/3rd, 5.9 GB, copied with `cp -c`, an APFS clone whose blocks are shared with the main checkout's until one side changes, so it costs little while both exist; the archive's copy is made the same way;
- the reference build directory, rebuilt in place for the flag and for each mutation (its size not measured; the plain binary is 193 MB and the coverage binary 669 MB);
- binaries copied aside: the unflagged one until the flag check, and each mutated one until its run ends (about 193 MB each);
- the archive: the binary, a copy of the SDK directory (302 MB for MacOSX26.2.sdk), deps/3rd as a clone, obclient and mysqltest, and the recorded outputs;
- run outputs and instance directories under /Users/colin/seekdb-dev/mysqltest-runs/.

Later, the Rust target directory for 1.8-3.4M Rust lines is assumed to need 20-60 GB (report §4), next to the C++ build directories, the reference and its archive, the scratch worktree /Users/colin/seekdb-dev/cov-076eb309b, the raw coverage profiles (about 1 GB) and the judge's data directories. The top of that band does not fit in 33 GiB, and fits in 50 GiB only with little else.

---

## 8. Risks and open items

| # | Risk or open item | What is known now | Settled by |
|---|---|---|---|
| 1 | Why `subquery.idx_with_const_expr_21_subquery_dilang` failed in pass B | The source reading of action 4 (checked by a second reader) contradicts the earlier rounding guess: `now()` is truncated to the whole second, and the case fails when its inserts and its `-1 microsecond` selects start in the same second. Confirmed on the reference on 2026-09-24 (action 7): the unchanged case failed 4 of 20 runs, and the same-second rule explains all 20 diagnostic runs. The case is on the quarantine list | **Settled 2026-09-24** (investigations/subquery-datetime-rounding.md) |
| 2 | Which toolchain builds the C++ reference, and how `-ffp-contract=off` gets in | 834bbee1e's macOS 27 path needs SDK 27, and this Mac has SDK 26.2 at most and no Xcode, so the report's route stops at configure (section 4, "How the C++ reference is built"). The plan's default (b): reference-build.patch skips the MacOS27.cmake include, and the build uses deps/3rd's clang 17.0.6 with SDK 26.2; tested on 2026-09-24: builds in 317 s, 0 errors, `seekdb -V` reports 834bbee1e (section 4). The fallback (a): Command Line Tools with SDK 27 and the host Apple clang. Either way the flag goes in through `add_compile_options` in the same patch, because `-DCMAKE_CXX_FLAGS=` is overwritten (cmake/Env.cmake:125, :350) | **Settled 2026-09-24: (b)** (decisions.md row 3a) |
| 3 | Whether the 40 plan-bearing cases give the same text with and without `-ffp-contract=off` | Checked 2026-09-24 (action 9): recorded on both binaries, `compare` found 40 of 40 identical (judge/recordings.tsv) | **Settled 2026-09-24** |
| 4 | The reduced init profile | **Settled 2026-09-24:** judge/reduced-init/ creates only the admin user and the test database with its grants (no `set_tp` lines, no system-package PL); two C++ recordings of the 128 plain-SQL cases under it are byte-identical (judge/recordings.tsv). Changes only by a recorded amendment and a new C++ recording | 00b |
| 5 | The exact list of about 300 hash-order SELECTs for the row-order mask | 633 candidates listed by script over the configured cases (judge/lists/hash-order-select-candidates.txt); to be narrowed against the reference's plans after the mutation runs | 00b, before any Rust code |
| 6 | Where the judge's files live | The plan's default layout: section 4, "Where the judge's files live" | Developer, at 00b's first sign-off |
| 7 | Model roles Decision 4 does not settle, or settles against the report | Five Sonnet 5 roles: this plan moves them to Opus 5.5. Three fixing roles the report gave Fable 5.1 (Step 5's hardest sessions, Step 6's plan-text and float clusters, Step 6's judge or comparator bugs): this plan uses Opus 5.5, as Decision 4 says for every fixer, and proposes Fable 5.1 for them (section 6, "Model plan") | **Settled 2026-09-24:** five support roles on Opus 5.5, three fixing roles on Fable 5.1 (decisions.md row 4a); then every Fable 5.1 role moved to Opus 5.5 (row 4c) |
| 8 | The query-speed ratio, and whether restart time counts as "start-up time" | **Settled 2026-09-24** (decisions.md row 1a): 1.2x on median latency and QPS; restart after a kill counts as start-up time, recorded and not gated | Developer, before Step 2a |
| 9 | The `cargo check` rate and memory | **Measured 2026-09-24** on cranelift-codegen 0.135.2 (122K hand-written lines plus 181K generated): a full check of the crate at about 19,000-44,000 hand-written lines per second on one thread, maximum RSS under 0.82 GB (migration/measurements/cargo-check-rate.md), well above the report's assumed 500-5,000 | 00b phase |
| 10 | Sustainable agent runs per day under the subscription with Opus 5.5 | Unknown; the calendar spans about 11-52 months across the scenarios (section 7) | Step 2a, first 100-unit batch; then the developer confirms Decision 5 (b) or remakes it |
| 11 | Tokens per run and the processed-to-harness ratio | Assumed 0.15-0.6M (0.3-1.0M SQL tier) and 5-18x. Inputs to the calendar re-estimate, not stop triggers (section 5); the ratio is measured only if the harness exposes processed tokens under the subscription, and otherwise stays inferred | Step 2a |
| 12 | Per-crate `cargo check` time (Step 4 folding; Decision 16 reopening above about 60 s) | Assumed 20-60 s central, 15 s-5 min range | Step 2a |
| 13 | Step 6 machine time with one Mac: incremental rebuild and link in the judge build profile | Assumed 2-30 min per rebuild; Step 6 at 50-3,700 machine-hours; Decision 17 (a) removed the second-machine relief, so the calendar allows up to 37 weeks for Step 6 (section 7) | Step 2a measures the rebuild. The plan's proposal: if it lands near the top of its band, the developer decides whether to add a machine, which would remake Decision 17 |
| 14 | Disk: 33 GiB recorded by Decision 17, 50 GiB measured since (cause unknown), against a 20-60 GB target directory | See section 7, "Disk" | `df -h /` at every build in 00b; before Step 2a, and during its pilot |
| 15 | Whether the narrow path runs without most of bootstrap | Unmeasured; Decision 10's switch condition | Step 2a |
| 16 | Step 4's compile-error count | 20K-100K, unanchored | Step 2a's full pass |
| 17 | Whether the storage estimator's inputs are kept identical, so EST numbers can be exact at the end | Open (Decision 6 notes) | Design document (Step 1) |
| 18 | The storage and PX scan order of the about 7,500 unmasked single-table SELECTs | Not masked (Decision 6), so the storage redesign must keep it | Design document; checked by the narrow run and the core build's exit |
| 19 | The kept C++ oblib subset under share/geo and S2, and how island memory is charged on macOS | Not sized at link level; malloc_hook is Linux-only at 834bbee1e | Design document |
| 20 | `stacker` on macOS | Assumption that it grows the stack as `SMART_CALL` does | Design document, checked in Step 2a |
| 21 | Timing-sensitive cases on a faster or slower build | A build that runs at a different speed moves where statements fall against second boundaries: the subquery case fails more often on a faster build (its inserts and selects then share a second more often), and other timing cases may shift the other way. So timing cases may fail for reasons unrelated to correctness on either build | 00b's quarantine reasons; Step 6 triage |
| 22 | Coverage depth | About 45% of lines in the core and the SQL tier run under no configured case; executing is weaker than checking output (coverage README) | 00b's families (expression generator, `--ps-protocol` replay, restart, memory budgets); Step 6 |
| 23 | Unmasked exactness causing false failures (ties under ORDER BY, scan order, EST numbers in the exact run) | Each false failure costs about 1-3M tokens of triage (report §9) | Design document; Step 6 |
| 24 | No Linux gate | Linux, Windows, Android and wasm parity are unverified until each has its own gate | When each platform comes back into scope |
| 25 | One developer's attention | The core build runs at about 31 h a week for 3-9 months (report §7) | Developer, at the core build's start |
| 26 | Narrow run fails and forces Decision 10 (b) | +0.13-1.0B tokens, 2-4 months, 150-400 h | Step 2a |
| 27 | The runner's trailing-whitespace tolerance | It turns a mismatch that differs only in trailing spaces and tabs into a pass (section 4, item 1). The plan's default is a named switch, on for CI and off in every judge run; keeping it on for judge runs would add a mask Decision 6 does not list | Developer, at 00b's first sign-off |
| 28 | A stop rule for the calendar | **Settled 2026-09-24** (decisions.md row 5b): no stop rule; the re-estimated calendar is reported and the work goes on | Developer, before Step 2a |
| 29 | The reference toolchain drifting | The libc++ headers come from the SDK and the runtime libc++ from macOS, so a macOS or Command Line Tools update can change sort ties or other library behavior under the frozen source; under fallback (a) it also replaces the compiler | Item 13's record and SDK copy; the rebuild check repeated after any update |

---

## 9. What changed from the feasibility report

Every place the report's recommendation no longer holds, with what changed it:
- **Scope: server and embedded → server only.** Embedded-contract harness item 5 and family 10 are dropped; family 8 loses the seekdb-async `exec_*` cases; family 9 keeps the bindings' restart-persistence behavior, rewritten for server mode, and the seekdb-async seed `conn_open_shares_one_handle_per_dir` is dropped; family 11 drops `run_two_concurrent_clients.sh` (/Users/colin/seekdb-dev/seekdb-bindings/lib/tests/, which loops a bindings gtest that starts `seekdb --embedded`), with a server-mode startup race proposed in its place; Step 5 drops the bindings and seekdb-async smoke suites; the report's recommendation of the out-of-process driver is replaced by no embedding for now (Decision 8).
- **Platforms: Linux and macOS first, with a Linux gate → macOS arm64 only, no Linux gate.** Family 18 and the Linux done-gate are dropped; harness item 11 and family 16 (wasm) are dropped; item 9 loses the wasm start-up time; the wasm rules "enforced from day one" become guidelines (Decision 7).
- **Upstream: ask upstream first, then a fork with scheduled replay → a fork that stops following upstream, frozen, never replayed.** Go-condition 2's "upstream's answer" is replaced; the replay section, its token, machine-time and attention rows and its queue pauses are removed; the IR-id rule keeps its `find_item` reason but loses its replay reason (Decisions 2 and 3).
- **Base commit: 076eb309b → 834bbee1e** (Decision 3 (a)).
- **Race fixes: merge the wasm branch's two fixes into C++ → not merged; the reference is 834bbee1e's source with no code changes.** Removed from "Start now", from 00b's phase and from Decision 15 (Corrections). Only reference-build.patch, which changes build files, is added.
- **Building the reference: the coverage build's route → a toolchain choice.** At 834bbee1e a macOS 27 host needs SDK 27, which this Mac lacks; the plan's default keeps the 076eb309b toolchain through reference-build.patch, and the developer confirms it (section 4, "How the C++ reference is built"; section 8, item 2). Found while revising this plan.
- **Parallel branches: reconcile the design with feature/plugin, let the palf redesign settle → all ruled out as code sources** (Decision 15).
- **Model plan: (a) → (c).** Opus 5.5 for every implementer and fixer, so Step 2a's full pass and the Step 3 other-leaf units run on Opus 5.5; Step 2b runs once instead of once per tier; the report's trigger "reconsider (c) if Sonnet 5's adherence is weak" is moot; the three fixing roles the report gave Fable 5.1, and every other Fable 5.1 role, run on Opus 5.5 (Decision 4 (c); decisions.md rows 4a and 4c).
- **Spend: the API with per-step ceilings and dollar triggers → a subscription.** The dollar tables and the $400K trigger are dropped; the report's other stop triggers (tokens per run, the processed-to-harness ratio) become inputs to the calendar re-estimate, with no stop rule (decisions.md row 5b); tokens stay the logged measure; "how many agents at once" becomes "runs per day"; Step 3 goes from 2-7 weeks to 6-46 weeks by scenario; the calendar goes from 10-34 months to about 11-52 months by scenario (Decision 5 (b)).
- **Performance gates: QPS, latency, cold start, restart, judge wall time and binary size → only QPS and latency, at 1.2x (decisions.md row 1a) and judge wall time.** Cold start and binary size are recorded only (Decision 1 (b)). Restart time is recorded only under this plan's reading of "start-up time", which the developer confirms (section 8, item 8).
- **Row order: sorted-set fallback wherever the order differed → only for the declared list of about 300 hash-order SELECTs** (Decision 6).
- **Decision 10 (d): kept open until a Step 2a measurement → dropped,** and with it the structure-preserving `unsafe` measurement in Step 2a (Decision 10 notes).
- **Machine: free about 150 GB or add an SSD, a Linux machine before Step 6, a second build machine if needed → keep the Mac as it is** (Decision 17 (a)). Decision 17 recorded 33 GiB free, not the report's 51-52 GiB; a later `df -h /` showed 50 GiB, cause unknown. The second-machine relief for Step 6 is gone, so Step 6's upper bound grows from 26 to 37 weeks (section 7, "Calendar").
- **Coverage: expected between a third and 60% → measured 64.4%, provisional until 00b explains the subquery difference** (report §11). Once it stands, the coverage trigger for "don't migrate the whole" no longer applies, and no re-measure at 00b's exit is needed: report §11 asks for one only in the band between a third and 60%.
- **Quarantine list: four seeds → five entries,** the fifth pending 00b's confirmation (coverage README).
- **The judge's pass time on this Mac: unmeasured at HEAD → 1,454 s and 1,464 s** on the instrumented binary (coverage README).
- **The coverage procedure: report §11's untested recipe → the coverage README's tested one,** which adds the Mach-O `___llvm_profile_*` link fix in cmake/Env.cmake and the `SEEKDB_COV_PROFRAW_DIR` copy step (coverage README).
- **The island malloc hook:** the report lists it as a cost every island keeps; at 834bbee1e malloc_hook is built only on Linux (src/oblib/lib/CMakeLists.txt:34-41). Found while writing this plan.
- **Runner fixes (item 1): the report's list → plus `--case-list`, init-file options, and the trailing-whitespace tolerance off for judge runs;** the report's "turn retries off" becomes `--max-retries`, 0 for judge runs and 3 for CI (Decision 6; section 8, item 27). Found while revising this plan.
- **Departures from the kit: the report named 00b before a "migrate" verdict → also 00b's two sign-offs and the quarantine list in place of N/N** (section 6, departures 6 and 7).
- **Step 6 done-gate: zero inherited failures → zero outside the quarantine list,** with the quarantined cases documented (section 6).
- **00b's machine time: 8-20 h → about 4-10 h for the core set,** counted run by run from the measured pass time; the coverage passes are done (section 7).
- **Rounding:** Step 1's 0.16B, Step 2a's 0.43-3.6B, Step 3's 2.3B and 11,000-14,500 runs, and the total's 3.7B → 0.15B, 0.44-3.7B, 2.4B, 11,400-14,500 runs (with the island shims) and 3.8B, from adding the parts before rounding (section 7).

---

## 10. Next actions

This section is how 00b runs here. `prompts/00b-judge-setup.md` is the contract, with the placeholders of section 6. Its step 1 (categorize) is action 3; its step 2 (rewrite for portability, checked by the two reviewers) is actions 6 and 10; its step 3 (validate against known answers) is actions 8 and 12; its stop-and-show is action 15. Each action names who does it and what it waits for. No action builds in /Users/colin/seekdb-dev/migrate-to-rust/build_release/ or edits files under tools/deploy.

1. **Drafts for the developer [Claude; may start now].**
   - Copy templates/RULEBOOK.md to migration/RULEBOOK.md and fill only its section 7 (Deviation log), with departures 1 and 2 (section 6). The rest becomes the design document in Step 1.
   - Draft the worktree's CLAUDE.md (today there is only AGENTS.md). It imports the kit's manual, `@/Users/colin/repo/code-migration-kit-with-claude-code/CLAUDE.md` (kit README, Quick start step 2), and says: step 0 is done; 00b is the current step; migration/PLAN.md decides the order of work, over the kit CLAUDE.md's routing (which says to run prompts/00-feasibility.md while migration/RULEBOOK.md, migration/depmap/ and migration/manifest.tsv do not exist) and over any skill's routing.
   - Done on 2026-09-24: the status section of ~/.claude/skills/migrate-seekdb-to-rust/SKILL.md points at this plan and says the port's working documents live in migration/ (English), with a Chinese summary in the vault at ~/obsidian/tech/seekdb/migrate to rust/seekdb 整体迁移到 Rust 的方案.md.
2. **Developer actions [developer; actions 1, 3, 4 and 14 do not wait for them].**
   - Commit action 1's files on migrate-to-rust. (PLAN.md, migration/judge/ and the row-15 fix in decisions.md were committed on 2026-09-24.)
   - Confirm section 8, item 2 (the reference toolchain) before action 5, and item 7 (the model roles) before action 6, the first fan-out.
   - Confirm items 6 and 27 at 00b's first sign-off. Items 8 and 28 were confirmed on 2026-09-24 (decisions.md rows 1a and 5b).
3. **Census and case lists (00b step 1) [Claude; read-only; may start now].** Each file starts with the command that produced it, and the counts are `wc -l` of the files.
   - migration/judge/census/: the portable set (the 272 cases in `runtime_configs.psmall.test-set` of tools/deploy/mysqltest_config.yaml, which the runner reads, and tools/ob_error/test/test.sh); the internal-bound set (the 7 tracked internal-bound C++ files, each with what it guarded); the out-of-scope sets with their reason (the seekdb-bindings gtests and the seekdb-async tests, Decision 8; shell-e2e and the wasm unittests, Decision 7).
   - migration/judge/lists/plan-bearing.txt (configured cases whose .result carries plan tables) and lists/plain-sql.txt (configured cases with no plan tables, no `__all_*` reads, no ALTER SYSTEM and no sleep, the report §5 groups), both by grep over the configured .test and .result files.
   - migration/judge/lists/hash-order-selects.txt: candidates by script now (SELECTs without ORDER BY that are not single-table), confirmed against the reference's plans after action 5, before any Rust code exists.
   - Restate section 4's counts (40 or 42, 130, about 300) from these files.
4. **The subquery case, from the source [Claude; read-only; may start now].**
   - Read tools/deploy/mysql_test/test_suite/subquery/t/idx_with_const_expr_21_subquery_dilang.test (the `now()` inserts at lines 55-57; the `-1 microsecond` selects from line 103, with no sleep between) and migration/judge/coverage-076eb309b/pass-B.subquery.idx_with_const_expr_21_subquery_dilang.diff.
   - Find in the source how an insert rounds `now()`'s microseconds into a scale-0 `DATETIME`, and whether a value rounded up to the next second explains the empty result.
   - Write migration/judge/quarantine.tsv (case, reason, source, status, handling in the C++-against-Rust runs) with the four seeds, and the subquery case marked pending until action 7.
5. **Reference worktree and builds [Claude; waits on section 8, item 2].** The worktree /Users/colin/seekdb-dev/ref-834bbee1e already exists from the 2026-09-24 toolchain test, with deps/3rd cloned, the MacOS27.cmake include skipped by an uncommitted one-line edit to cmake/Env.cmake, and a finished build without the flag; under the default (b), action 5 continues from there (turn that edit into the first part of reference-build.patch and skip the first two commands below).
   ```
   df -h /
   git -C /Users/colin/seekdb-dev/migrate-to-rust worktree add --detach /Users/colin/seekdb-dev/ref-834bbee1e 834bbee1e
   cp -c -R /Users/colin/seekdb-dev/seekdb/deps/3rd /Users/colin/seekdb-dev/ref-834bbee1e/deps/3rd
   ```
   - Build with `--make` and without `--init`. dep_create.sh then does not run, so the deps/3rd marker (deps/3rd/DONE) still holds the md5 of 35eb2c3a3's macOS 15 list, and the mismatch is expected; the only package difference from the macOS 27 list is the LLVM that list drops (section 4, "How the C++ reference is built").
   - Under the default toolchain (b), write the first part of reference-build.patch (skip the MacOS27.cmake include in cmake/Env.cmake) and build without the flag. This is a new command for 834bbee1e, not the coverage README's:
     ```
     cd /Users/colin/seekdb-dev/ref-834bbee1e && SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk bash build.sh release --make
     ```
     Copy build_release/src/observer/seekdb aside to /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-noflag/seekdb (about 193 MB) for action 9.
   - Add `add_compile_options(-ffp-contract=off)` next to cmake/Env.cmake:67-71, rebuild, and confirm the flag on the compile lines of one C and one C++ object.
   - Save the patch as migration/judge/reference-build.patch. Copy the flagged binary into /Users/colin/seekdb-dev/ref-archive-834bbee1e/ and record its `shasum -a 256`; every judge run from here on uses that copy (item 13). Write migration/judge/reference.md: the compiler (`clang --version` of the one used), the SDK path and version, `_LIBCPP_VERSION`, `sw_vers`, the build times, and `df -h /` after each build.
   - Under fallback (a), the steps are the same with the host Apple clang, and the patch leaves the MacOS27.cmake include alone.
6. **Runner fixes, item 1 [Claude: two Opus 5.5 implementer runs, then the two 00b reviewers (Fable 5.1, Opus 5.5) on disjoint batches; waits on section 8, item 7].** In /Users/colin/seekdb-dev/migrate-to-rust/.github/script/seekdb/mysqltest_for_seekdb.py:
   - `--max-retries N`: 3 by default so CI keeps its behavior, 0 in every judge run, with every attempt logged (`MAX_CASE_RETRIES` at line 19, the loop at line 502);
   - `--nodaemon` in `prepare_instance` (line 157);
   - save instance outputs before every `destroy_instance` (line 100), with scratch-tree.patch's `save_coverage_profiles` as the tested model;
   - fixed case order and slice count, and a fresh-instance-per-case mode;
   - `--case-list FILE`;
   - options for the init.sql and init_user.sql files;
   - init.sql and init_user.sql statement by statement (`execute_init_sql`, line 123);
   - a differential mode that compares two builds' outputs;
   - the trailing-whitespace tolerance behind a named switch, on for CI and off in every judge run (section 8, item 27).
7. **The subquery case, on the reference [Claude; after actions 4-6; before action 8].** A diagnosis of one case, not a validation run.
   - Run the unchanged case 20 times through the runner (`--case-list` naming only it, `--max-retries 0 --no-ignore-trailing-whitespace`, the archived binary) and count the failures.
   - Run a copy of the test with `select now(6)` after the inserts (migration/judge/investigations/) 20 times with deps/3rd's mysqltest against an instance of the archived binary, and log, against pass or fail, the second in which the inserts ran and the second in which the `-1 microsecond` selects ran (a `select now(6)` right after the inserts and another right before the selects).
   - If "the inserts and the selects started in the same second" explains every failure, add the case to quarantine.tsv with that reason. If not, the case stays off the list, the precondition stays unmet, and 00b treats the failure as a judge defect to explain before action 8.
8. **Repeat the precondition, item 12 (00b step 3) [Claude; after actions 5-7].** First the check on the working tree (section 4, "The runner's working tree"): the `git diff` must exit 0 and the `git status` must print nothing. Then two passes, one after the other, of all 272 configured cases on the archived flagged binary, full init.sql, retries off, one slice:
   ```
   H=/Users/colin/seekdb-dev/migrate-to-rust
   REF=/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb
   CLI=/Users/colin/seekdb-dev/ref-834bbee1e/deps/3rd/u01/obclient/bin
   git -C $H diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py
   git -C $H status --porcelain -- tools/deploy
   for P in A B; do
     RUN=/Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-$P
     mkdir -p $RUN
     python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
       --seekdb $REF \
       --obclient $CLI/obclient --mysqltest $CLI/mysqltest \
       --base-dir $RUN/instance --work-dir $RUN --port 3881 \
       --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace > $RUN/runner.log 2>&1
   done
   ```
   For each pass, read `failed_cases` in seekdb_result.json, and for each failed case failures/<case>/mysqltest.log and mysqltest_log/<case>.reject. `grep -c 'RETRY' runner.log` and `grep -c 'trailing whitespace ignored' runner.log` must both print 0, and seekdb_result.json must show `"ignore_trailing_whitespace": false`, an empty `trailing_whitespace_ignored_cases` and an empty `retried_cases`. Every judge run in this section passes `--max-retries 0 --no-ignore-trailing-whitespace`; the runner defaults (3 retries, trailing whitespace ignored) are for CI only, and `--record-dir` refuses to run without `--max-retries 0`. Every case outside the quarantine list must pass in both passes; a case outside the list that fails in either pass is a difference 00b explains before anything else. Record the pass times (the instrumented passes took 1,454 s and 1,464 s).
9. **The flag check [Claude; after actions 3, 5 and 6].** Record lists/plan-bearing.txt (`--case-list`, `--max-retries 0 --no-ignore-trailing-whitespace`, `--record-dir`) once on the unflagged binary and once on the archived flagged one, then `compare --left <unflagged> --right <flagged> --out <json>`; record whether the plan text is identical (section 8, item 3). The compare prints the differing `seekdb_sha256` as a note, which is expected here. Then delete the unflagged binary.
10. **Reduced init and restart script, items 2 and 3 (00b step 2) [Claude: Opus 5.5 implementers, then the two 00b reviewers on disjoint batches; after action 6].**
    - Write migration/judge/reduced-init/ (init.sql and init_user.sql without the `set_tp` lines and without system-package PL; section 4, item 2). Record the archived reference twice under it on lists/plain-sql.txt (`--init-sql` and `--init-user-sql` pointing at the reduced files, `--max-retries 0 --no-ignore-trailing-whitespace`, a new `--record-dir` each time), run `compare` on the two recordings, check that they are byte-identical outside the quarantine list, and log both in migration/judge/recordings.tsv.
    - Write the restart script under migration/judge/harness/, on `sdb.py start` / `stop` against one base dir; stops are kills (Decision 9 (a)).
11. **The archive, item 13 [Claude; after actions 8-10; before action 12].** Complete /Users/colin/seekdb-dev/ref-archive-834bbee1e/: deps/3rd (`cp -c -R`), a copy of the SDK directory, reference-build.patch, obclient and mysqltest, and the recorded outputs. Then remove the reference worktree's build_release/, rebuild once from the archived deps/3rd and SDK, and check that the compiler, the SDK, `_LIBCPP_VERSION` and the output on lists/plan-bearing.txt are the same. Run `df -h /` before and after.
12. **Injected mutations, items 12 and 17 (00b step 3) [Claude, Opus 5.5 for the injection; after action 11].** At least 10, as in section 4, "How the injected mutations run".
13. **Performance baselines, item 9 [Claude with the developer; after action 11].** sysbench in Docker at 1, 16 and 64 threads against the archived reference, and the judge wall time from action 8. The speed ratio is 1.2x (decisions.md row 1a).
14. **The `cargo check` rate [Claude; may start now].** No crate of 150-200K lines of ordinary code is in the local cargo registry (checked while revising this plan: the larger ones there are generated bindings or data tables, such as windows-sys, linux-raw-sys and encoding_rs). The plan's default is cranelift-codegen 0.135.2 from the registry: about 122K lines of .rs outside tests, benches and examples, plus the code its build script generates, which sits inside the design's 100-180K-line crate cap. Its non-optional direct dependencies are in the registry; whether the whole set resolves offline is not checked.
    ```
    cp -R ~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/cranelift-codegen-0.135.2 /tmp/cranelift-check
    cd /tmp/cranelift-check && cargo +1.98.1 clean && /usr/bin/time -l cargo +1.98.1 check --offline -j14
    ```
    Record the wall time, the lines checked per second per core, and the maximum resident set size. If `--offline` cannot resolve every dependency, drop it and let cargo fetch them.
15. **00b's first sign-off [developer; after actions 3-12].** Claude shows the harness, the census counts (action 3), the reviewer findings, both validation runs (action 8) and every caught mutation (action 12), and the developer signs off that the judge is real (section 4, "00b's exit"). Claude then records departures 6 and 7 in the deviation log, appends the step's row to migration/cost-log.tsv with `step` = `00b` (later rows use `1`, `2a`, `core` and `2b`, then the kit's step numbers `3` to `6`), and marks go-conditions 1 and 4 in section 5.
16. **Then Step 1** (section 6), with 00b's remaining items (4, 6, 7, 8, 9; 14 if run) and the 15 families built alongside and signed off before Step 2a.
