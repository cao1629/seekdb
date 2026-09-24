# Resolutions

This file records how every objection the eight design sections raised against ARCHITECTURE.md was settled, and how the conflicts between sections were settled. It is part of the Step 1 design document (migration/RULEBOOK.md section 0). Written 2026-09-25 by Opus 5.5, which holds the design document role (decisions.md row 4c).

- Every objection is settled in one of three ways: **accepted** (ARCHITECTURE.md changed to the section's proposal), **changed** (ARCHITECTURE.md or the section changed to a third answer), or **rejected** (the objection was wrong; the section changed). Each section file keeps its objections, each followed by a "Settled" line that points here.
- A settlement is a design decision signed off with the design document. Where it creates a choice only the developer can make, it is a default in ARCHITECTURE.md §17 (defaults 29-37 are new) or a question under that table.
- The design files before these changes are kept in migration/design/evidence/rulebook/backup/ (`diff` against it shows every edit: ARCHITECTURE.md 215 changed lines, s1 133, s2 28, s3 38, s4 60, s5 40, s6 38, s7 46, s8 37). The research reports in migration/design/research/ are unchanged; where they differ from the settled files, the settled files win.
- The first review's 64 findings changed the design after these settlements; migration/design/REVIEW-1.md records each with its outcome, and where it and this file differ, REVIEW-1.md is the later word (for example on catalog access, the init profiles and default 10).
- The crate graph is acyclic: `cd migration/design/evidence/rulebook && python3 -B check_crate_graph.py /Users/colin/seekdb-dev/migrate-to-rust/migration/design/ARCHITECTURE.md crates/allowed-design.tsv` printed `acyclic: 39 crates, 419 allowed edges, every edge points to an earlier crate; topological order found for all 39 crates; rows equal crates/allowed-design.tsv` on 2026-09-25, and a copy of the table with one upward edge added (ob-base using ob-runtime) made it fail with the cycle named.

## s1-crates-core.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s1-1 | parse_malloc's C-named callbacks sit in sql-parser, which forbids `unsafe`, but `#[unsafe(no_mangle)]` needs a named crate | Accepted. Every function the parser's C core calls by its C name lives in sql-parser-sys; the crate map moves src/sql/parser's parse_malloc, ob_memory_tracker_wrapper, ob_parser_charset_utils, parser_utility and parse_node_hash there | ARCHITECTURE §1.1 rows 22 and 29, §9.2; s1 1.3 |
| s1-2 | sql-nio's row names no crate, yet its pipe code moves to ob-platform | Accepted. sql-nio's row is "3 only"; it calls ob-platform directly instead of receiving a function | ARCHITECTURE §1.1 row 37 and sql-nio paragraph; s1 1.2, 1.5 rule 4 |
| s1-3 | The rows come only from direct include lines; 442 link references reach lower crates off the rows | Accepted. The rows now hold every downward include or link edge the design map shows (rerun: `check_allowed.py` leaves 7 include lines and 10 symbol references off the rows, all of the three kinds that disappear by design: seekdb through observer, `parse_malloc` calls that become `ParseTree` methods, kept C including a frozen header). Those and every upward edge get ledger rows (`fix` = one of the five, `row`, or `gone`) | ARCHITECTURE §1.1, §1.2; s1 1.2, 1.4 |
| s1-4 | The nio.h edges are invisible to the scripts | Accepted. ARCHITECTURE §1.1 says where the nio.h glue goes (observer), that `SQL_REQ_OP` becomes the trait `ObSqlRequestOperator` in ob-runtime, and that the packed-row encoder is reached through a trait in sql-exec | ARCHITECTURE §1.1 |
| s1-5 | "Become context fields" hides that 443 of 1,195 lookups cannot reach a context holding the service | Accepted, no fifth context. §7.1 states the rule: such a lookup goes through an `Arc`/`Weak` the owning object received at construction, or a parameter, never a `static`. A storage-tx context would add a deref level for one crate's lookups of its own services; the core API sessions add one only if the storage-tx API shows the need (assumption: the objects making those lookups are built where an `Arc` of the service is at hand) | ARCHITECTURE §7.1 |
| s1-6 | "Before any thread starts" would make replay run with no engine threads, unchecked | Changed. `RuntimeContext`'s own threads (timer service, log writer, IO) start when it is built, before replay, as the C++ starts them before replay (ob_server.cpp:682, :687, :1125; replay at :1155), and read no cell; every other thread starts after the binding step. The runtime controller's two timers (ob_server.cpp:1150) come later than in the C++, which no case observes (assumption) | ARCHITECTURE §7.1; s1 3.3 |
| s1-7 | `Timer`, `TimerTask`, `TimerService`, `ServerOptions` break the rule that a one-to-one core type keeps its C++ name | Accepted. `ObTimer`, `ObTimerTask` (method `runTimerTask`), `ObTimerService`, `ObServerOptions` | ARCHITECTURE §7.1, §7.2, §14 rule 3; s1 3.3, 3.5, 3.7, 5.4, 6 |
| s1-8 | "A memtable-only run never reads an sstable" is stated as a fact | Accepted. Marked an assumption, with the advance-checkpoint flush, statistics jobs and 02:00 freeze named; Step 2a keeps narrow runs under 10 minutes | ARCHITECTURE §1.3, §15 |
| s1-9 | §1.2's counts belong to R01's map | Accepted. On the design map: 1,013 upward include lines in 110 pairs, 2,662 upward symbol references in 134 pairs, with the command | ARCHITECTURE §1.2; s1 1.4, 7 |
| s1-10 | ob-platform's named uses miss the malloc-zone promotion and the post-`fork` jemalloc background-thread switch | Accepted, with s7-3 | ARCHITECTURE §7.3, §8 |
| s1-11 | §14 rule 1 is ambiguous; no rule for nested types or overloads | Accepted. §14 rule 1 now reads the matched prefix row (and the `dir` column, keyword directories, `main_`); rule 3 adds `Outer_Inner` and overload numbering in header order (`f`, `f_2`, ...; `new`, `new_2`, ...) assigned by the declaration index | ARCHITECTURE §14 |

## s2-errors.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s2-1 | 24 predicates at ob_define.h:71-265, not 22 at :71-255 | Accepted, with the `awk` command | ARCHITECTURE §2 |
| s2-2 | Eleven lookup functions, not five | Accepted: the eleven of src/share/ob_errno.cpp:15530-15633 | ARCHITECTURE §2 |
| s2-3 | 5 of the 18 non-zero values in parse_define.h are not catalog codes | Accepted. The check pins `OB_PARSER_SUCCESS`, 13 matches and the 5 outsiders by value | ARCHITECTURE §2 |
| s2-4 | "Client number 0 for an unknown code" holds only in (-65535, 0) | Accepted. §2 lists 0 in (-65535, 0), `-code` at or below -65535, the value itself above 0, each cut to 16 bits | ARCHITECTURE §2 |
| s2-5 | The async end-of-transaction callback does send a message recorded under another code | Accepted. §2's "Why" names the synchronous packet and the async callback apart; keeping the callers as written keeps both | ARCHITECTURE §2 |
| s2-6 | Comparators also feed a top-N check and `std::lower_bound`, not only sorts | Accepted. Fallible comparators are called by the port's sort, heap and binary-search functions, or directly by code that tests the result at once | ARCHITECTURE §2 |
| s2-7 | Three rows of R02 §5.2's table contradict §2's `?` rule | Accepted. §2 points to s2 2.3's table, names the `map_err` renames, `if let Err(e)` resets and hand-expanded check macros as not used; `CK`/`OX`/`OZ`/`OV` stay `macro_rules!`. s4's example follows (C-9) | ARCHITECTURE §2; s4 3.1, 3.5 |

## s3-memory.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s3-1 | §11's closed thread-local list has no slot for the parser's receiver-less `try_check_mem_status` | Accepted (same as s7-6). sql-parser-sys's per-call parse slot joins the list, set and cleared by a scope guard around each C parse call | ARCHITECTURE §9.2, §11 |
| s3-2 | §3.1 rule 9 ("a pool whose size users hit stays") conflicts with §3.2 on the large-tablet pool | Accepted. "A pool stays only when Decision 12 names its limit: the KV-cache handle pool" | ARCHITECTURE §3.1 rule 9 |
| s3-3 | §3.2 is silent on the eleven other finite limits | Accepted. They and the large-tablet pool's -4013 are dropped; new default 29 | ARCHITECTURE §3.2, §17 |
| s3-4 | §1.2 fix 1 does not move `ObMemTracker` down | Accepted. It moves to ob-base's `allocator` module; crate-map row added | ARCHITECTURE §1.1, §1.2, §3.2; s1 1.3 |
| s3-5 | Citations: micro-block retry loop, tmp-file calls, hash-join check | Accepted for the micro block cache (:396-400 inside `alloc_data_buf` :387-408; calls at ob_tmp_file_cache.cpp:698, :739, :769, override at :654). Rejected for the hash join: ARCHITECTURE's :838-843 is right (`if` at :838, -4013 at :843); s3's :837-842 is off by one and now fixed | ARCHITECTURE §3.2; s3 3.7.3 |
| s3-6 | "No flush earlier than the C++" plus default 9 needs a bytes-per-row measurement | Accepted. §6.1 states the condition; Step 2a measures the memtable's bytes per row | ARCHITECTURE §6.1, §15 |

## s4-sql-front.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s4-1 | `str_value_: &'q [u8]` cannot tell NULL from an empty string | Accepted. `str_value_` and `raw_text_` are `Option<&'q [u8]>`; NULL tests go through `str_value_is_null`/`raw_text_is_null` | ARCHITECTURE §4.2; s4 1.1-1.3, 5 |
| s4-2 | A 32-bit arena counter wraps while PL cache objects keep their arenas | Accepted. The counter skips numbers a live arena holds; the per-arena generation is replaced by the number check (several arenas: every build; one arena: debug assertions) | ARCHITECTURE §4.1 |
| s4-3 | Raw expressions that outlive a statement are not covered | Accepted. `RawExpr`'s byte fields and `ObObj` hold bytes as `Cow<'q, [u8]>`, so long-lived owners use `'q = 'static` | ARCHITECTURE §3.1; s4 2.3 |
| s4-4 | 27 variants, not 29 | Accepted | ARCHITECTURE §4.1 rule 7 |
| s4-5 | Rule 5 misses `ObStmtFactory`'s first-in, first-out reuse of freed select statements | Accepted. Both kinds of reuse keep order and id | ARCHITECTURE §4.1 rule 5 |

## s5-execution.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s5-1 | murmur_hash belongs in ob-base, which `ObHashMap` needs, not ob-values | Accepted. The crate map already sent src/oblib/lib/hash_func to ob-base; §10's text now says ob-base's `hash` module | ARCHITECTURE §10; s5 7.1 |
| s5-2 | `loc: u64` makes a 16-byte datum or needs `packed` | Accepted. `loc: [u8; 8]`, 12 bytes, alignment 4, no `packed`, as the C++ asserts (ob_pushdown_filter_simd.cpp:46) | ARCHITECTURE §5; s5 2.1 |
| s5-3 | Default 9 lets the SQL work area dump at other rows, which changes unmasked ties and hash group order | Changed default 9: budgets trip by the same formulas, not bytes, except the SQL work area, which counts the C++ figures (stored-row headers, 12 bytes per cell, hash buckets) so dumps come at the same rows. Step 2a checks the dump rows. The developer confirms default 9 with this exception; if not, family 12's spill test must avoid ties and hash-ordered output (s5 section 10) | ARCHITECTURE §3.2, §15, §17 default 9; s3 A4, 3.6.3 |
| s5-4 | §5's filter kinds miss the JSON-path white filter and the truncate-partition filter | Accepted | ARCHITECTURE §5 |
| s5-5 | `next_batch` conflicts with keeping C++ names | Changed to the C++ names: the provided wrapper is `get_next_batch`, bodies are `inner_get_next_batch` | ARCHITECTURE §5; s5 part 4; s2 2.2 rule 2 |
| s5-6 | The aggregate protocol's `emit` needs the output columns | Accepted | ARCHITECTURE §5; s5 5.10 |
| s5-7 | `ObBitVector`'s file is mapped to ob-values-doc | Accepted. Crate-map row moves src/share/vector/ob_bit_vector to ob-values | ARCHITECTURE §1.1, §1.2, §5; s1 1.3 |
| s5-8 | §10's seed list is short | Accepted. 16777213, 99194853094755497 and 0 with their users | ARCHITECTURE §10 |
| s5-9 | "Owned by" should read "written by" | Accepted; the frame owns the slots, the producer writes them and owns its skip vector | ARCHITECTURE §3.1 rule 3 |

## s6-storage.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s6-1 | Wrong line numbers for the 1,000x rule and the 10,000 cache | Accepted for the 1,000x rule (ob_index_sstable_estimator.cpp:258-274, .h:135). Rejected for the cache: `DEFAULT_TABLET_INCREMENT_CACHE_SIZE = 10000` is at ob_tablet_autoincrement_param.h:32 (`grep -n`; :31 is `DEFAULT_HANDLE_CACHE_SIZE`), so ARCHITECTURE's :32 stays and s6 3.1 rule 3 now cites :32 | ARCHITECTURE §6.1; s6 3.1 |
| s6-2 | §6.1 and §16 miss the advance-checkpoint flush and the clog-disk-usage flush | Accepted. Both are kept triggers; "no flush earlier" holds for explicit and timed flushes, and for the byte-driven ones only under the assumption and the Step 2a measurement. Whether the init profiles set `_advance_checkpoint_interval = 0m` is a question for the developer | ARCHITECTURE §6.1, §16, §17 questions |
| s6-3 | "Same storage events" must mean the same kinds of events | Accepted. §6.2 names the kinds; the micro-block end falls where the Rust cut puts it; 00b's mutation measures it | ARCHITECTURE §6.2 |
| s6-4 | The missing-file rule can pass a C++ directory whose data or redo dir lies elsewhere | Accepted. No store/sstable/meta.db and no store/meta/meta.db are conditions too | ARCHITECTURE §12 item 2; s6 4.2 rule 5 |
| s6-5 | A refusal inside the engine never reaches the terminal, since the C++ daemonizes first | Accepted. seekdb runs the check before `fork` and `Server::open` runs it again; exit status 1 | ARCHITECTURE §7.3, §12 item 5 |
| s6-6 | §1.1 has no crate for the ob_error tool | Changed crate: ob-errno's binary target, not the seekdb crate. The tool includes only share/ob_errno.h and its own os_errno.h (tools/ob_error/src/ob_error.h:20-21), so it needs no engine crate; s1 and s2 already placed it there | ARCHITECTURE §1.1, §1.4, §12; s6 6.1, 6.2 rule 8 |
| s6-7 | "Exact up to 500 rows" should read "exact below 500 rows" | Accepted, with the half-batch rule named | ARCHITECTURE §6.1 |

## s7-islands-unsafe.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s7-1 | Where the kept oblib subset lives is not stated; one shared copy breaks the graph | Accepted. rust/kept-oblib/ holds the replacement files; each island compiles its own copy with its tag and links partially (`ld -r` with an export list) | ARCHITECTURE §1.1, §9.4 |
| s7-2 | CRoaring is missing; its hook has no receiver; sql-das needs vsag-sys | Accepted. vsag-sys binds CRoaring and installs its hook once per process; the hook charges the vector budget through process-wide state set at install and never refuses (new default 34 for counting every CRoaring allocation). Crate map moves share/roaringbitmap and oblib/lib/vector to vsag-sys; sql-das's and storage-tx's rows gain 13 | ARCHITECTURE §1.1, §8, §9.4, §13, §17; s3 3.6.4 |
| s7-3 | ob-platform's `unsafe` list is incomplete | Accepted, with s1-10. §8 names every libc call of s7 7.10's closed table and `je_malloc_conf` | ARCHITECTURE §8 |
| s7-4 | sql-engine needs ob-clib-sys for zlib (OUTFILE) | Accepted | ARCHITECTURE §1.1 row 28 |
| s7-5 | §9.3 rule 3 lacks `float` and typed views, which vsag needs | Accepted | ARCHITECTURE §9.3 rule 3 |
| s7-6 | §11's thread-local list needs the parser's slot | Accepted (as s3-1) | ARCHITECTURE §11 |
| s7-7 | "-11049 keeps counting GIS memory" cannot hold for kernel scratch | Accepted. -11049 counts built geometries and results; kernel scratch is not charged (new default 35) | ARCHITECTURE §9.4, §17 |
| s7-8 | `ob_parse_binary_simd` is x86_64-only | Accepted; the arm64 release defines none | ARCHITECTURE §9.2 |
| s7-9 | sql-nio has a third libc call (pump.rs:496-502), and more to remove | Accepted | ARCHITECTURE §1.1; s1 1.5 rule 4 |
| s7-10 | §13's flag list is incomplete | Accepted (with s8-3) | ARCHITECTURE §13 |
| s7-11 | The `*_simd.cpp` files have no NEON code | Accepted. The ob-simd row names the vector distance kernels as the NEON code | ARCHITECTURE §1.1 row 4 |
| s7-12 | §2's generated island C header has no user | Accepted. No island header; a gate checks every value in the frozen src/share/ob_errno.h and src/oblib/lib/ob_errno.h against ob-errno | ARCHITECTURE §2; s2 2.8, 2.11 |

## s8-numerics-platform.md

| ID | Objection | Settlement | Changed |
|---|---|---|---|
| s8-1 | "The 29 weaker orderings" misses 75 `std::memory_order_*` lines | Accepted. They become `SeqCst`; they join the 29 only if Step 2a's sysbench shows a cost | ARCHITECTURE §11; s8 4.2 |
| s8-2 | The ban list lacks `algebraic_*` | Accepted | ARCHITECTURE §10 |
| s8-3 | §13's flag list lacks `-DNDEBUG`, `-fmax-type-align=8` and more | Accepted (with s7-10). Shared flags listed; the rest from a checked-in flags file derived from compile_commands.json | ARCHITECTURE §13 |
| s8-4 | `OB_ASSERT` wording invites `debug_assert!(x)`, which skips `x` | Accepted. `ob_assert!(x)` evaluates `x`, then asserts the value | ARCHITECTURE §10 |
| s8-5 | The loop denies miss `cargo +<toolchain>`, `cargo rustc` and `rustc` | Accepted | ARCHITECTURE §13 |
| s8-6 | The C-library list omits CRoaring | Accepted (with s7-2) | ARCHITECTURE §13 |
| s8-7 | §1.4 and §13 disagree on xxhash | Settled for the crate (xxhash-rust, known-answer vectors); its uses are free formats and an uncalled datum family | ARCHITECTURE §1.4, §13; s8 9.2 |
| s8-8 | The 64 MiB cap cannot include the thread's own stack in safe Rust | Accepted. The cap counts extension bytes only; the firing depth is no contract | ARCHITECTURE §11 |

## Conflicts between sections

| ID | Conflict | Settlement | Changed |
|---|---|---|---|
| C-1 | The thread spawner: s1's object-safe trait with a stack size against s3's generic `spawn` | s1's trait: `ThreadSpawner::spawn(&self, name, stack_size, Box<dyn FnOnce() + Send + 'static>)`, so a library host can pass its own | ARCHITECTURE §7.2; s3 H2 |
| C-2 | The timer task's method: s1's `runTimerTask` against s3's `run_timer_task` | The C++ name, `runTimerTask`, on the trait `ObTimerTask` (s1-7) | s3 H2 |
| C-3 | `Server::open`'s result: s1's `ObResult<Server>` against s6's `ObResult<Arc<ServerContext>>` | `ObResult<Server>`; `Server` holds the `ServerContext` and offers `request_stop` and `wait_stop_request` | ARCHITECTURE §7.1; s6 4.3 |
| C-4 | The storage-api traits (estimator, optimizer storage service): s6 puts them in `SqlContext`, s1's rule puts services in the lowest context whose crate may name them | `StorageContext` fields, reached from `SqlContext` by deref | ARCHITECTURE §7.1; s6 2.4 |
| C-5 | The ob_error tool: ob-errno's binary (s1, s2) against the seekdb crate's (s6) | ob-errno (s6-6) | ARCHITECTURE §1.1; s6 6.2 |
| C-6 | The no-argument `check_stack_overflow`: s8's `check_stack_overflow_ret` against s1's overload numbering | The numbering rule: `check_stack_overflow_2` (second in ob_common_utility.h:31-44); one rule for core and translated code, so the declaration index needs no exception | ARCHITECTURE §11, §14; s8 section 6 |
| C-7 | Island codes: s7's `ObError::new` against s2's `ObError::from_code` | `ObError::from_code`, which turns 0 into `Ok(())` | s7 7.3 |
| C-8 | s2's example called `next_batch` | `get_next_batch` (s5-5) | s2 2.2 rule 2 |
| C-9 | s4 expanded `CK` and `OZ` by hand; s2 keeps them as `macro_rules!` | The macros (s2-7); s4's rule 3.1 and its `resolve_limit_clause` example use `ck!` and `oz!` | s4 3.1, 3.5 |
| C-10 | Macros: s1 invokes exported macros by path; every other section imports and calls them bare | Imported with `use` from the declaration index and invoked by bare name | ARCHITECTURE §14 rule 6; s1 5.5 rule 3 |
| C-11 | Networking features: s1's one `network` feature, on by default, against s8's `mysql` and `http` turned on by seekdb | s8's: `mysql` and `http`, off in observer, on in seekdb | ARCHITECTURE §1.1; s1 1.1 rule 5 |
| C-12 | `ob_crc64`: s6's hand-written table-driven CRC-32C against s8's crc32c crate | The crate, as `!crc32c_append(!init, data)` on the low 32 bits; s6's definition (polynomial 0x82F63B78, caller's initial value, no final XOR) is what the known-answer vectors pin | s6 4.2 rule 2 |
| C-13 | The per-execution arena: s5's `tmp: Bump` against s3's rule that execution arenas are counted by the request (T2) and never reach a PX worker counted (H4) | `tmp: ObArenaAllocator`, counted on a request thread, uncounted in a PX worker | s5 part 3 |
| C-14 | The workspace lint table: s7 has the workspace deny `unsafe_op_in_unsafe_fn`, s8's table lacked it | s7's; the line is added to s8 7.2 | s8 7.2 |
| C-15 | s2's code-pattern gate relied on `non_snake_case`, which s8's workspace table allows | The gate fails on `unused_variables` or `unreachable_patterns` warnings naming a catalog identifier | s2 2.11 rule 6 |
| C-16 | CRoaring's allocations: s3 called them "plain", s7 counts them toward the vector budget | Both: they never refuse, and each is counted (default 34) | s3 3.6.4 |
| C-17 | The ob-simd, sql-parse-tree, sql-ir and SQL-tier rows missed downward edges the rerun found once parse_malloc, `ObVectorIndexUtil` and oblib/lib/vector moved | Rows widened as in s1-3 (for example ob-simd 1, 2; sql-parse-tree 7, 8; sql-ir 22; sql-optimizer, sql-codegen, sql-engine and sql-rewrite 20) | ARCHITECTURE §1.1 |
| C-18 | sql-nio's Windows named-pipe creation (rust/sql-nio/src/transport.rs:176-191, two `unsafe` blocks) moves to ob-platform, but ob-platform's closed list of `unsafe` uses lacked it | Added to the list as a Windows item | ARCHITECTURE §8; s7 7.10 |
| C-19 | Markers: ARCHITECTURE §14 rule 8 asked every marker to carry the C++ file:line; s7's `SAFETY` lines name the rule they rely on | `TODO(port)`, `PERF(port)` and `BUG(port)` carry the C++ file:line; `SAFETY` names its rule | ARCHITECTURE §14 rule 8 |
| C-20 | Test stubs: s7 7.12 rejects the sql-nio convention of `#[cfg(test)]` stubs; s4 5 adopts it for sql-parser-sys | Both hold: no test stubs an island's C or C++ side; sql-parser-sys's tests link the real C core and give the Rust callbacks (`lookup_pl_symbol`, `try_check_mem_status`, the stack checks) test versions | s7 7.12 |
| C-21 | Island handle entry names: s3 3.8 adopted the sql-nio `_acquire`/`_release` pair; s7 names them `_create`/`_destroy` | s7's names; s3's row adopts the handle and its single release, not the names | s3 3.8 |

## What the settlements leave for the developer

- ARCHITECTURE.md §17 defaults 29-37 (new) and the narrowed default 9.
- The two questions under §17: `_advance_checkpoint_interval = 0m` in both init profiles, and the assumption that no configured case reaches the memstore-percentage or clog-disk-usage flush.
- Files Step 1 still writes before its exit, which the settled design names: migration/crates.tsv (from crates-design.tsv), migration/crate-edges.tsv (the ledger), migration/core-manifest.tsv, migration/manifest.tsv, migration/not-translated.tsv, migration/process-statics.tsv, migration/decl-index/, migration/scripts/check_crate_graph.py (from the scratch copy), and migration/inventory.tsv (prompt 02).
