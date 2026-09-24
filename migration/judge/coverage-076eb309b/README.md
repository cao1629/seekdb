# Function coverage of the 272-case judge at 076eb309b

This is the "single fact" of the feasibility report (section 11): what share of the functions
in the code the port redesigns or rewrites the 272 configured mysqltest cases execute. It was
measured on 2026-09-24 on the developer's Mac (Apple M4 Pro, 14 cores, 24 GiB).

## Result

Two full passes, merged (`AB.groups.txt`):

| Code | Functions executed | Lines executed |
|---|---|---|
| Core (the six rows of feasibility section 3, union) | 12,525 / 19,089 = 65.6% | 110,426 / 190,548 = 58.0% |
| SQL tier: src/sql/{optimizer,rewrite,resolver,engine,das} | 19,829 / 30,745 = 64.5% | 285,782 / 520,171 = 54.9% |
| Core + SQL tier (union) | 29,937 / 46,501 = 64.4% | 371,880 / 671,372 = 55.4% |
| All of src/ | 64,719 / 105,432 = 61.4% | 1,026,103 / 1,688,470 = 60.8% |

The two passes agree to within 0.1 point on every row (`A.groups.txt`, `B.groups.txt`). The
combined figure is above the report's threshold of about 60% of functions.

Per-group rows (functions, merged):

| Group | Directories | Functions |
|---|---|---|
| Foundation substrate | src/oblib/lib/{alloc,allocator,container,hash,string,rc,lock,atomic,list,queue} | 63.7% |
| Runtime | src/oblib/lib/{thread,utility,file,restore}, src/share/io, src/share/cache, src/storage/scheduler, src/data_plane/api/data_plane/scheduler | 57.3% |
| Value and datum types | src/oblib/common/{object,datum}, src/share/{datum,rc} | 63.9% |
| Statement IR | src/sql/resolver/expr, src/sql/resolver/dml/*stmt* | 72.7% |
| Execution framework | src/query/api/query/engine, src/sql/engine/{ob_operator*,ob_exec_context*,ob_physical_plan*}, src/sql/engine/expr/ob_expr_frame_info*, src/sql/engine/basic/ob_pushdown_filter*, src/sql/code_generator | 66.6% |
| Storage and transaction core | src/storage/{tablet,meta_mem,memtable,tx,multi_data_source,tx_table}, src/data_plane/api/data_plane/access | 67.1% |

These directory lists are this measurement's reading of the report's core table. The report's
runtime row also names "the local device", which has no directory here; its value row says only
"datum, object, rc". `coverage_groups.py` holds the exact patterns.

## How to read it

- A function counts as executed if any of its code ran once. Line coverage is about 10 points
  lower: roughly 45% of the lines in the core and the SQL tier run under no configured case
  (mostly error paths and rare branches). Executing a line is also weaker than checking its
  output: a case can run a function whose result never reaches a compared line.
- The runs used the full tools/deploy/init.sql, including its 12 `alter system set_tp` lines,
  which switch on self-checks that change behavior.
- The SQL parser objects are built without coverage (src/sql/parser/CMakeLists.txt), so the
  parser is not counted; it is not in either group.
- Measured at 076eb309b. The frozen base 834bbee1e differs by 23 files (+342/-25 lines), which
  cannot move these figures noticeably.

## The precondition: do two runs of the same binary agree?

Not yet. Failures per pass (`pass-*.seekdb_result.json`):

| Case | Pass A | Pass B | On the seed quarantine list? |
|---|---|---|---|
| type_date.type_create_time | failed | failed | yes |
| type_date.type_modify_time | failed | failed | yes |
| vector_index.sparse_vector_index_vsag_query | failed | failed | yes |
| subquery.idx_with_const_expr_21_subquery_dilang | passed | failed | **no** |

In pass B, subquery.idx_with_const_expr_21_subquery_dilang returned no rows for nine queries
that expect three (`pass-B.subquery.idx_with_const_expr_21_subquery_dilang.diff`). The test
inserts rows with `now()` into a `DATETIME` column (whole seconds) and then selects
`a5 <= date_add(current_timestamp(), interval -1 microsecond)`. The first guess here, that the
stored value rounds up to the next second, is wrong (00b action 4,
`../investigations/subquery-datetime-rounding.md`): `now()` and `current_timestamp()` without
digits are truncated to the whole second when evaluated, so the bound is the start of the
select's second minus 1 microsecond, and the nine selects exclude the rows whenever the inserts
and the selects start in the same second (in pass B: about 16:51:32.20 and 16:51:32.86). It is
the same class as the two type_date cases, and a faster build fails more often.

Under the report's rule this case was not named before the runs, so the precondition is not met.
00b confirms the explanation on the reference (action 7), adds the case to the quarantine list
with that reason, and repeats two clean passes on the pinned C++ build.

## How it was run

- Scratch worktree `/Users/colin/seekdb-dev/cov-076eb309b`, detached at 076eb309b, with deps/3rd
  cloned from the main checkout (`cp -c -R`).
- Patches in the scratch tree only (`scratch-tree.patch`):
  - CMakeLists.txt: `WITH_COVERAGE` taken out of the compatibility-build `FATAL_ERROR` guard.
  - cmake/Env.cmake: on Apple, the `-Wl,-u` symbols get Mach-O's extra leading underscore
    (`___llvm_profile_*`); the original names are unresolvable in a Mach-O link.
  - .github/script/seekdb/mysqltest_for_seekdb.py: `MAX_CASE_RETRIES = 0`; `--nodaemon` passed
    to `sdb.py start`; before every destroy, when `SEEKDB_COV_PROFRAW_DIR` is set, the instance
    is stopped and `<base_dir>/seekdb*.profraw` is copied out.
- Build: `SDKROOT=$(xcrun --show-sdk-path) bash build.sh release -DWITH_COVERAGE=ON --make`,
  582 s; the binary is 669 MB.
- A one-case dry run (28 s) confirmed the profile lands and `llvm-profdata` / `llvm-cov` read it.
- Two passes, one after the other, all 272 configured cases as one slice on port 3881:
  1,454 s and 1,464 s (`timings.txt`).
- `llvm-profdata merge -sparse` and `llvm-cov export -format=text -summary-only` from
  deps/3rd/usr/local/oceanbase/devtools/bin (clang 17.0.6), then `coverage_groups.py`.
- Raw profiles (about 1 GB) and the merged `.profdata` files stay outside the repo, in
  `/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/`.
