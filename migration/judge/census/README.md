# Test census and case lists for the judge

This is 00b's step 1, "Categorize" (`prompts/00b-judge-setup.md`; PLAN.md section 10, action 3). It sorts every test asset into portable, internal-bound or out of scope, and writes the case lists the judge runs. Everything here was produced read-only on 2026-09-24 from /Users/colin/seekdb-dev/migrate-to-rust at c55acd2a5. tools/deploy is 834bbee1e's: `git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py` succeeds and `git status --porcelain -- tools/deploy` prints nothing. The scripts read the runner from 834bbee1e with `git show`, because the working tree's runner changes during action 6. Nothing was built or run.

## Counts

Each file starts with `#` lines that give the command or script behind it, followed by one entry per line. The files have no blank lines, so the count of a file is `grep -vc '^#' <file>`.

| File | Entries | What the entries are |
|---|---|---|
| census/portable.txt | 273 | the 272 configured mysqltest cases, plus tools/ob_error/test/test.sh |
| census/internal-bound.txt | 13 | 3 gtests, 4 probes in bazel/probes, 6 probes in src/sql/bazel_pilot |
| census/out-of-scope.txt | 145 | Decision 8: 12 seekdb-bindings gtests, 1 seekdb-bindings wheel smoke test, 7 seekdb-async tests. Decision 7: 68 shell-e2e tests, 44 wasm CTests, 13 wasm test scripts |
| lists/plan-bearing.txt | 40 | configured cases whose .result holds a plan table |
| lists/plain-sql.txt | 128 | configured cases with no plan table, no `__all_*` table, no ALTER SYSTEM and no sleep |
| lists/hash-order-select-candidates.txt | 633 | SELECT statements without an outer ORDER BY that read more than one table (574), or one table with GROUP BY, DISTINCT or a window function (59) |

Counts per group in out-of-scope.txt, from the worktree root: `grep -v '^#' migration/judge/census/out-of-scope.txt | cut -f1 | sort | uniq -c`.

## How to reproduce them

Run everything from the worktree root, /Users/colin/seekdb-dev/migrate-to-rust. Five of the files carry the script that produced them, in lines that start with `#|`. The script's output is every line after it, so each of these checks prints nothing when its file still matches the tree (bash or zsh):

```
F=migration/judge/census/portable.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | sh) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
F=migration/judge/lists/plan-bearing.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | sh) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
F=migration/judge/lists/plain-sql.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | python3 -B -) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
F=migration/judge/lists/hash-order-select-candidates.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | python3 -B -) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
F=migration/judge/census/out-of-scope.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | python3 -B -) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
```

- The mysqltest scripts load `discover_cases()` from 834bbee1e's .github/script/seekdb/mysqltest_for_seekdb.py with `git show`, so they name and find cases exactly as the runner does, and the runner changes of action 6 cannot move the lists. They read tools/deploy from the working tree, so run them only while `git diff --quiet 834bbee1e -- tools/deploy` succeeds.
- out-of-scope.txt reads the sibling repositories at fixed commits, which its rows record: seekdb-bindings d7de02902, seekdb-async dd708416a, and cd41da17b in /Users/colin/seekdb-dev/seekdb, the head of origin/feature/webassembly-shell on 2026-09-24. /Users/colin/seekdb-dev/shell-e2e is not under git, so its 68 rows only hold while its spec files (dated 2026-09-15 and 2026-09-16) are unchanged.
- internal-bound.txt was written by reading each file. Its header gives the three commands that find the files.

## What the files settle in PLAN.md

- **Plan-bearing: 40.** 42 tracked .result files hold a plan table, and 40 of them belong to configured cases; update.update2 and geometry.geometry_index2_mysql are tracked but not configured. PLAN.md's 40 holds. The evidence's 42 (evidence-brief.md:1327) counts tracked files.
- **Plain SQL: 128, not 130.** The report's 130 counted the 283 tracked cases and grepped only their .test files, with a wider pattern than the four groups. Over the configured cases the four groups give 128 once the files a case sources are followed (28 geometry cases source an include that reads `oceanbase.__all_spatial_reference_systems`) and the .result is scanned (information_schema and information_schema.information_schema_desc print `__all_*` names from the catalog). plain-sql.txt's header gives the 158 you get without following the sourced files and the 130 you get without scanning the .result (129 if only the `__all_*` check skips the .result, because information_schema_desc's .result also matches ALTER SYSTEM, as a privilege name in the USER_PRIVILEGES view text). PLAN.md section 4 (family 2, "00b's exit") says 130.
- **Hash-order SELECTs: 633 candidates, against PLAN.md's "about 300".** The 300 was the feasibility verifier's estimate of the statements whose order really comes from hash output. The 633 are the wider set that action 5 narrows against the reference's plans. The script counts 10,577 SELECT statements with a FROM and no outer ORDER BY (the evidence says about 10,100). It sets aside 902 of them (793 expected to fail under `--error`, 108 under `--sorted_result`, 1 with the result log off); of the other 9,675, 9,042 are single-table (the evidence says about 7,500) and 633 are candidates.
- **Internal-bound: 13, not 7.** The report counted the 3 gtests and the 4 .cpp files in bazel/probes. The 6 compile-only probes in src/sql/bazel_pilot are the same kind and were already there at 076eb309b.
- **Decision 8's "12 gtests" leaves out one embedded-mode test.** seekdb-bindings also has python/tests/seekdb_test.py, which opens embedded seekdb through pylibseekdb and which cibuildwheel runs for every wheel. out-of-scope.txt lists it under Decision 8.
- **File name.** PLAN.md sections 4 and 10 name the row-order list lists/hash-order-selects.txt. That file is the confirmed list and does not exist yet; lists/hash-order-select-candidates.txt is what it will be built from.

The other counts agree with the report: 272 configured and 283 tracked cases, 7 seekdb-async tests, 68 shell-e2e tests and 44 wasm CTests.

## Tests in none of the three census files

These are tracked or local test assets that no decision and no census file covers yet:

- **11 tracked mysqltest cases that the config leaves out.** PLAN.md section 4 limits the judge to the 272 configured cases. The config's comment says config_test.config is left out because its support files are missing; fork_table.fork_table_sstable, geometry.geometry_ddl_mysql, vector_index.create_table_with_hybrid_vector_index and view.check_privilege are commented out; the other six are simply not listed. The eleven: config_test.config, ddl.dump_ddl_mem, fork_table.fork_database_check_names, fork_table.fork_table_sstable, geometry.geometry_ddl_mysql, geometry.geometry_index2_mysql, geometry.st_bestsrid_mysql, update.update2, vector_index.create_table_with_hybrid_vector_index, view.check_privilege, window_function.sqlancer_const_prop_cast. Command: `git ls-files 'tools/deploy/mysql_test/*.test' | wc -l` gives 283.
- **tools/obtest: 509 .test files** (`git ls-files 'tools/obtest/*.test' | wc -l`). They need the mytest.jar cluster harness, so they cannot run here (report section 5). Family 9 uses fork_table_restart_recovery.test as a seed, rewritten for the restart script.
- **origin/feat/embedded-mode (d248c8ece in /Users/colin/seekdb-dev/seekdb).** Report section 5 counts 61 C++ cases, 5 x 15 language cases and 11 Java cases against the in-process C ABI. Decision 8 defers that ABI, and Decision 15 rules the branch out as a code source. They were not recounted here.
- **tools/module_check/unittest_module_check_test.py:** 12 Python tests of the module-layering checker (`git grep -c 'def test_' -- tools/module_check`). The checker reads the C++ tree but imports no C++.
- **rust/sql-nio:** 3 `#[test]` functions in src/cert.rs and src/tls.rs. They are Rust already.
- **/Users/colin/seekdb-dev/shell-e2e/result-table.test.mjs:** 8 `node:test` tests (`npm run test:helpers`) of result-table.mjs, the helper the Playwright tests use to check result tables. They test that helper, not seekdb, and are not among the 68 Playwright rows in out-of-scope.txt.
- **Test-support headers under test paths, which hold no test:** src/storage/deadlock/test/test_key.h is compiled into production (src/storage/deadlock/ob_deadlock_key_register.h:18 includes it and registers ObDeadLockTestIntKey), and src/oblib/lib/thread/ob_test_util.h, src/storage/testing/ob_storage_test_errors.h and src/storage/testing/ob_storage_test_schema.h are included by nothing outside the Bazel header inventories.

## Notes for later actions

- **Action 6:** the runner's `--case-list` must skip lines that start with `#`. plan-bearing.txt and plain-sql.txt keep their script in `#` lines, followed by one case name per line.
- **Action 10 and the Step 2a path:** plain-sql.txt screens only the four groups. Some of its cases still use other OB features:
  - system packages: ai_function.ai_model_endpoint_ddl, ai_function.ai_model_lower_case_name_mysql1, histogram.dbms_stats_delete_stats, histogram.stats_lock_and_unlock, histogram.stats_set_table_stats;
  - OB dictionary views (`oceanbase.DBA_*`): those five, and table_column_related_views and fts_index.index_view;
  - FORK TABLE: fork_table.fork_table_partition, fork_table.fork_table_privilege, fork_table.fork_table_with_constraints, fork_table.fork_table_with_lob.
- **Action 5:** many hash-order candidates return one row, because they are aggregates without GROUP BY. In those, row order can only show inside GROUP_CONCAT or in a column that is not aggregated. Two candidates contain mysqltest `$` variables (they come from `eval`), so they need the variables filled in before they can be run on their own.
