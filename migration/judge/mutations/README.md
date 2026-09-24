# Injected mutations (PLAN.md section 4, item 12 and family 17)

The judge must catch deliberately broken C++. Each patch here is one small change to one .cpp file
of the pinned reference (834bbee1e). A mutation counts as caught when at least one configured case
outside `../quarantine.tsv` fails in a run of all 272 configured cases under the full init.sql with
retries off. The procedure is at the end of this file.

## How the mutations were chosen

- One .cpp file each, never a header or .ipp; no cmake change. Every patch applies to
  /Users/colin/seekdb-dev/ref-834bbee1e with `git apply --check` (checked 2026-09-24).
- Every mutated line runs under the 272 cases. The counts come from `llvm-cov show` on the merged
  profile of the two coverage passes (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata,
  binary /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb), so each count is
  the total over two full runs, bootstrap included. The profile was taken at 076eb309b; all 14 files
  are byte-identical at 076eb309b and 834bbee1e (`git diff --quiet 076eb309b 834bbee1e -- <file>`
  exits 0 for each), so the line numbers and counts hold for 834bbee1e.
- Each change alters SQL output or an error code, and each is narrow enough that bootstrap and
  init.sql should still run: most mutated lines run only for one statement shape that init.sql does
  not use (the per-row reasons are in the table). None of them is expected to crash the server.
- The expected catching cases were found by reading the .test and .result files; none of them is
  on the quarantine list.

## The mutations

Line counts are "line: count" from `llvm-cov show` (k = thousand, M = million), summed over both passes.

| NN | Subsystem | File : function | The change | Coverage evidence | Expected to catch it (and why) | Caught by (filled after the run) |
|---|---|---|---|---|---|---|
| 01 | Optimizer cost model | src/sql/optimizer/ob_opt_est_cost_model.cpp : `ObOptEstCostModel::cost_range_scan` | `if (io_cost > cpu_cost)` becomes `if (io_cost < cpu_cost)`: a range scan now costs the smaller of its IO and CPU cost instead of the larger | 1392: 171k; both branches run (1393: 40.0k IO, 1397: 131k CPU) | With the default cost parameters a one-block scan costs 1.67 us of IO (`get_micro_block_rnd_cost`), while its CPU cost includes 2.1 us per range (`get_range_cost`), so most scans switch from the CPU figure to the smaller IO figure. Surest: global_index.global_index_lookup_1, _2, _3 and _5, whose IN-list scans (5 ranges, so about 10.5 us of range cost against 1.67 us of IO) print `DISTRIBUTED TABLE RANGE SCAN ... 5 236`; that EST.TIME drops by about 9 us. Also any other EXPLAIN whose scan EST.TIME crosses an integer: executor.basic (152 EST.TIME lines), dist_nest_loop_simple (38), executor.full_join, subquery.subquery, subquery.optimizer_subquery_bug, fts_index.simple_query. Bootstrap: inner SQL may get other plans, but plans do not change results | |
| 02 | Cast error path | src/sql/engine/expr/ob_datum_cast.cpp : `common_string_double` | The body of the `check_convert_str_err` failure branch is replaced by `ret = OB_SUCCESS;`: a string with trailing garbage converts to DOUBLE with no warning and no error | 789: 47.8k; error branch 790-798: 12.2k, including the `LOG_USER_WARN(OB_ERR_DOUBLE_TRUNCATED...)` at 797: 12.2k. This line and ob_obj_cast.cpp:5732 (8 hits) are the only places that raise this warning | The `Warning 1292 Truncated incorrect DOUBLE value` lines disappear: expr.expr_floor (16 lines, e.g. `'now'`, `'haha1'`), expr.expr_ceil (4), array.array_ddl_mysql (4), geometry.geometry_function_mysql (3), geometry.st_point_mysql (3), geometry.st_x_mysql (1), vector_index.vector_calc_gbk_mysql (2), vector_index.vector_calc_utf8_mysql (2). Bootstrap: init.sql converts no bad strings | |
| 03 | SORT tie handling | src/sql/engine/sort/ob_sort_op_impl.cpp : `ObSortOpImpl::Compare::operator()(const StoredRow *l, const StoredRow *r)` | Three added lines after the key loop: when every sort key compares equal, `less = r < l`, so rows tied on all ORDER BY keys come out in reverse storage order instead of input order. The comparator is still a strict weak ordering, so libc++'s sort check (the comment at lines 402-403) is not triggered | Function: 10.1M calls; 14.4M key comparisons, of which 3.56M return <0 (425) and 5.21M >0 (427), so about 1.3M calls end with all keys equal and reach the new code | alias3: `select c1 as a1,c2 as a2 from t1 order by a2` (t1 has no index on c2, so a SORT runs) expects `2 0 / 4 0 / 1 1 / 3 1 ...` and gets `4 0 / 2 0 / 3 1 / 1 1 ...`; also minitest (`select * from t1 order by v1`, three rows tied on `a`), and other ORDER BY ties (range, window_function.farm, group_concat ... order by in groupby.group_by_basic). Small sorts only: encoded sort keys (the other branch) need at least 1,000 estimated rows (ob_optimizer_util.cpp:6978). Bootstrap: only the order of fully tied rows changes | |
| 04 | ObNumber rounding | src/oblib/common/number/ob_number_v2.cpp : `ObNumber::ceil` | `if (POSITIVE == d_.sign_)` before the round-up carry becomes `if (NEGATIVE == d_.sign_)`: CEIL of a positive NUMBER with a fractional part rounds toward zero instead of up | Carry block 962-966: 4 (twice per pass); function 1.96k | expr.expr_ceil: `ceil(c16)` on `decimal(20,10) unsigned` (an ObUNumber column, value 16.1, two rows) expects 17 and gets 16. The file is in src/oblib/common/number/ in this tree, not src/oblib/lib/number/ as PLAN.md writes. See the note below on why half-up rounding is not the target | |
| 05 | DEBUG_SYNC point name | src/rootserver/fork_table/ob_fork_table_task.cpp : `ObForkTableTask::process` | The WAIT_FROZE_END stage calls `DEBUG_SYNC(FORK_TABLE_BUILD_DATA)` instead of `DEBUG_SYNC(FORK_TABLE_WAIT_FREEZE_END)`. The names live in ob_debug_sync_point.h, so the rename is done at the call site | 184: 200 (once per fork task); for comparison, the BUILD_DATA point at 198: 10.8k | fork_table.fork_table_lock: it arms `FORK_TABLE_BUILD_DATA wait_for signal_base`, so the task now parks at status 19 instead of 48; wait_fork_table.inc waits for status >= BUILD_DATA for about 60 s (600 polls of 0.1 s) while debug_sync_timeout is 120 s, so the include prints `# Warning: wait_fork_table.inc timed out` and `die`s. Probably also fork_table.fork_table_cow (same wait, 60 s timeout, a race) and fork_table.fork_table_ddl (the FORK_TABLE_WAIT_FREEZE_END scenario near test line 612 no longer pauses). Costs 60-120 s in fork_table_lock plus up to about 17 min in fork_table_chain, cow and ddl, which run before it (see the review below) | |
| 06 | String function | src/sql/engine/expr/ob_expr_operator.cpp : `ObExprKMPSearchCtx::substring_index_search` | For a negative count the result starts at the found delimiter instead of after it (offset `pos` instead of `pos + pattern_.length()`, length `text.length() - pos`); the result stays inside the input string | Negative-count branch 5457: 42; delimiter found 5466-5468: 22 | substring_index: `SUBSTRING_INDEX('abcdabcdabc','abc',-3)` gives `abcdabcdabc` instead of `dabcdabc`; `substring_index('aaaaaaaaa1','aa',-1)` gives `aa1` instead of `1`; `SUBSTRING_INDEX(1.414, 1, '-1')` gives `14` instead of `4` | |
| 07 | Comparison operator | src/sql/engine/expr/ob_expr_null_safe_equal.cpp : `ObExprNullSafeEqual::ns_equal` | When both sides are NULL, `equal = true` becomes `equal = false`: `NULL <=> NULL` returns 0 | 120: 60 (of 226 compared pairs) | expr.expr_nseq (`select null<=>null;` expects 1), expr.func_equal (`NULL<=>NULL` column expects 1), safe_null_test, and the `<=>` queries over NULL rows in join_null, select_basic and subquery.subquery. Constant `<=>` is folded through the same eval function | |
| 08 | Aggregate | src/sql/engine/aggregate/ob_aggregate_processor.cpp : `ObAggregateProcessor::collect_aggr_result` (T_FUN_GROUP_CONCAT) | The default GROUP_CONCAT separator `','` becomes `';'` | 3973: 322 (default separator chosen); separator appended 548 times (3997-4004) | groupby.group_by_basic (`abc,abc,a2b3,a1b3,a3b2` becomes `abc;abc;a2b3;a1b3;a3b2`, many statements), topk, view, expr.collation_expr, subquery.subquery, window_function.farm, array.array_arith_op_mysql. Bootstrap: only the text of GROUP_CONCAT results changes | |
| 09 | Join | src/sql/engine/join/ob_hash_join_op.cpp : `ObHashJoinOp::fill_left_join_result_batch` | `need_left_join()` in the unmatched-build-row test becomes `LEFT_OUTER_JOIN == MY_SPEC.join_type_`: a HASH FULL OUTER JOIN drops the unmatched rows of its build (left) side; LEFT OUTER JOIN is unchanged | 5198: 11.9k tuples tested; 5199: 10.2k emitted | executor.full_join: the four `/*+use_hash(t1 t2)*/ ... full join` selects (plans show HASH FULL OUTER JOIN) lose rows such as `1 NULL` to `8 NULL` from t_h3_01_20. `need_left_join()` is true for LEFT and FULL OUTER JOIN (ob_join_op.h:81-85), so only FULL changes; `LEFT_OUTER_JOIN` is already used unqualified in this file (line 426). Bootstrap: inner SQL uses no full outer join | |
| 10 | DML | src/sql/engine/dml/ob_dml_service.cpp : `ObDMLService::check_column_null` | `if (is_ignore \|\| (!is_single_value && !is_strict_mode(sql_mode)))` loses `is_ignore \|\|`: UPDATE/INSERT IGNORE under a strict sql_mode no longer turns a NULL for a NOT NULL column into the zero value with warning 1048; it fails with ERROR 1048 | 115: 24 NOT NULL violations; 116: 18 took the IGNORE/non-strict branch; zero-value warning 155: 8 | update.update_ignore: `update ignore t1 set a=null where a='aa';` and `UPDATE ignore Z0CASE SET T1=NULL WHERE T2='11';` run before the test's `set sql_mode = ''` (test line 231), expect `Warning 1048` and now fail without `--error`, which aborts the case. UPDATE always passes `is_single_value = false` (ob_dml_service.cpp:800), and the default sql_mode is strict: `281018368` (ob_system_variable_init.cpp:341) has bit 22, `SMO_STRICT_ALL_TABLES`, which `is_strict_mode` tests (ob_sql_mode_utils.h:32-35) | |
| 11 | Transaction read path | src/storage/tx/ob_tx_api.cpp : `ObTransService::get_read_snapshot` | `!tx.snapshot_version_.is_valid()` becomes `true`: REPEATABLE READ and SERIALIZABLE take a new read snapshot for every statement, like READ COMMITTED | RR/SE branch 683: 234; snapshot taken 685: 152, so it was reused 82 times, and the reuse is what the mutation removes. The RC branch (1.37M) is untouched | trx.repeatable_read_transaction and trx.serializable_transaction (both source trx/include/serializable_or_rr_trans_basic.inc): at lines 104-107 conn2 expects `--error 6235` on `update t1 set c='a444'` after conn1 committed `a333`, and the update now succeeds; lines 119-128 expect conn1 to keep its old snapshot. Bootstrap and init run at READ COMMITTED | |
| 12 | Resolver | src/sql/resolver/dml/ob_select_resolver.cpp : `ObSelectResolver::resolve_alias_column_ref` | `ret = OB_NON_UNIQ_ERROR;` is removed: when an unqualified name matches two different select-item aliases, the first one is used silently | 3319: 18 | column_alias (`select c1, c2 as c1 from tt1 order by c1;` under `--error 1052`) and select_basic (its `--error 1052` statements that match two aliases, such as `select c1, c2 as c1 from t1 group by c1;` and `select c1, c2 + 1 as c1 from t1 order by c1;`) now succeed, which fails the case. The 18 hits are 9 per pass: 1 in column_alias and the rest in select_basic | |
| 13 | System function (date) | src/oblib/common/timezone/ob_time_convert.cpp : `ObTimeConverter::merge_date_interval` | When adding months or years lands past the end of a month, the day is clamped to `days - 1` instead of `days` | 1882: 88; clamp 1883: 8 | type_date.expr_date_add_sub: `date_add('2012-2-29', interval 1 year)` gives 2013-02-27 instead of 2013-02-28; `date_sub(..., interval 1 year)` gives 2011-02-27; the `adddate`/`subdate` forms change the same way | |
| 14 | Privilege | src/sql/privilege_check/ob_privilege_check.cpp : `get_fork_table_stmt_need_privs` | FORK TABLE asks for DROP instead of CREATE on the destination table | 1187: 248 (every fork statement) | fork_table.fork_table_privilege: `test_user2` has SELECT, INSERT on the source and CREATE on `db_fork_priv.*` but no DROP, so `fork table t_priv to t_priv_fork;` (test line 73, no `--error`) fails with a DROP-denied error. The other fork cases run as root, who has DROP. init.sql's `grant all` does not go through this function | |

Subsystems: optimizer cost model, cast, SORT, ObNumber, DEBUG_SYNC (fork-table DDL task), string
function, comparison operator, aggregate, join, DML, transaction, resolver, date function, privilege.

## Notes for the reviewer

- **ObNumber half-up rounding cannot be caught by the 272 cases**, so 04 changes ObNumber's CEIL
  instead of `round_scale_v3_`. The evidence: signed DECIMAL columns are resolved to decimal-int
  (ob_resolver_utils.cpp:4328-4331), and so are casts to `number(p,s)` (double to ObNumber casts: 4
  hits). The ObNumber path to a scale, `number_range_check_v2` (ob_datum_cast.cpp:10794, 40.8k),
  never changed a value (10796: 0). ObNumber division in SQL runs only through `DIV`
  (ob_expr_int_div.cpp:339, 6.10k), which truncates to an integer. `ROUND()` reaches ObNumber
  only 56 times (ob_expr_func_round.cpp:237). The exact halves found in `ROUND()` arguments are
  literals (`round(0.5)`, `round(1000.5)` in update_delete_limit_unique_key), which resolve to
  decimal-int, or sit in the quarantined subquery.idx_with_const_expr_21_subquery_dilang
  (`round(9.0/2.0)`). A flip of the half-up test at ob_number_v2.cpp:1617
  (`residue >= tmp_round_pows / 2`) would probably survive the judge. Families 5-7 (the expression
  and cast generator, the value-format matrices) must cover it.
- **05 depends on timing, but in a safe direction**: the park at status 19 lasts 120 s and the test
  gives up after about 60 s. If a later run changes `debug_sync_timeout` in fork_table_lock.test,
  check this again.
- **05 can spread to later fork cases.** The runner keeps one instance for all 272 cases, so the
  parked DDL task and the consumed sync point can make later fork_table cases fail too. That still
  counts as caught; fork_table_lock is the case to name.
- **01 and 03 also change behavior for inner SQL**: plan choice (01) and the order of fully tied
  rows (03). Result rows should not move. If a run shows failures outside the named cases, these
  two are the first suspects, and a slower plan under 01 would show up in the run's total time.
- **02 also drops strict-mode errors**: a bad string inserted into a DOUBLE column now succeeds, so
  any `--error` on such an insert is another catcher. Most of the 12.2k hits are string-to-double
  comparisons in WHERE clauses whose warnings are never printed.
- **The compiler runs with `-Wall -Wextra -Werror`** (src/oblib/CMakeLists.txt:114-136, applied to
  every target through `oblib_base_without_pass`), with `-Wno-unused-parameter` and
  `-Wno-constant-logical-operand` among the exceptions. Patches 10 (`is_ignore` becomes unused) and
  11 (`|| true`) rely on those two. Every identifier a patch adds already exists in the headers its
  file includes (`FORK_TABLE_BUILD_DATA`, `LEFT_OUTER_JOIN`, `NEGATIVE`, `OB_PRIV_DROP`).
- Each patch was made in a scratch git repository holding pristine copies of the 14 files
  (`git show 834bbee1e:<path>`), with `git diff --src-prefix=a/ --dst-prefix=b/`.

## Adversarial review (2026-09-24)

A second reader checked every patch against the reference worktree, the coverage profile and the
named cases. Verdict: 14 kept, 0 fixed, 0 dropped. What the review adds to the table above:

- **Mechanics.** All 14 pass `git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check`; each has
  one `diff --git` header and one .cpp file (`--numstat` added/removed: 01 1/1, 02 1/9, 03 3/0,
  04 1/1, 05 1/1, 06 2/2, 07 1/1, 08 1/1, 09 1/1, 10 2/2, 11 1/1, 12 0/1, 13 1/1, 14 1/1). All 14
  files are identical at 076eb309b and 834bbee1e (re-checked). The identifiers the patches add are
  already used in their files (`LEFT_OUTER_JOIN` at ob_hash_join_op.cpp:426, `OB_PRIV_DROP` at
  ob_privilege_check.cpp:412, `NEGATIVE` at ob_number_v2.cpp:949, `FORK_TABLE_BUILD_DATA` at
  ob_fork_table_task.cpp:198).
- **Line counts.** Every count in the table was re-read from AB.profdata with `llvm-cov show
  -name-regex` on the mangled names (llvm-cov matches mangled names, so `ObNumber::ceil` finds
  nothing and `ObNumber4ceil` does). All matched.
- **Startup and init.** The dry run's profile
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/dry/all.profdata: bootstrap, init.sql,
  init_user.sql and the case a_trade_notify) has 0 hits on the mutated lines of 02, 04, 05, 06, 07,
  08, 09, 10, 11, 12, 13 and 14, so none of them can fail bootstrap or init. 01's line 1392 runs
  573 times there (102 IO, 471 CPU), so inner-SQL plan costs do change under 01; 03's comparator
  runs 12 times. Both change plan choice or the order of fully tied rows only.
- **09 has no bypass.** ob_hash_join_op.cpp is the only hash join in the tree (no vectorized
  variant), and the row-mode path (`find_next_unmatched_tuple` from `fill_left_operate`, line
  5240) has 0 hits in both passes: every hash join in the 272 cases goes through
  `fill_left_join_result_batch`.
- **12's other raise sites are silent.** The other `OB_NON_UNIQ_ERROR` origins in
  ob_select_resolver.cpp (1370, 3293, 3397) have 0 hits; 3319 is the only one that fires (18). The
  re-raise at 3517-3522 (4 hits) is a different scenario (an ambiguous table column in GROUP BY
  scope).
- **10.** The three IGNORE statements that expect `Warning 1048` are at update_ignore.result lines
  204-206, 413-415 and 434-436; bootstrap has 0 NOT NULL violations (dry profile, line 115: 0).
- **05's cost is larger than "about 60 s".** In configured order, fork_table_chain (2
  `FORK_TABLE_BUILD_DATA wait_for ... execute 10000` arms, `debug_sync_timeout` 60 s),
  fork_table_cow (6 arms, 60 s) and fork_table_ddl (9 arms, 60 s) run before fork_table_lock. Under
  the mutation each armed fork parks first at WAIT_FROZE_END for up to 60 s while
  wait_fork_table.inc polls for about 60 s (600 polls of 0.1 s plus query time). That is a race:
  each of those cases either dies (caught early) or passes about 60 s slower per arm. fork_table_lock
  is the sure catcher (120 s park against the 60 s poll); fork_table_merge (1 arm, 60 s) follows
  it. Added wall time up to about 20 min; the runner's CASE_TIMEOUT is 3600 s, so no case is
  killed. fork_table_ddl section 12 (the FORK_TABLE_WAIT_FREEZE_END arm) is an unlikely catcher:
  its `--error 4012,1210` ALTER most likely still fails because the unpaused fork is still waiting
  for the freeze. A debug-sync wait that times out only logs "wait for event timeout" and
  continues (ob_debug_sync.cpp:603).
- **01.** The coverage supports "EST.TIME changes wherever io_cost differs from cpu_cost" (both
  branches run: 40.0k and 131k); the microsecond figures in the table are the designer's
  arithmetic and were not re-derived.
- **03.** `lib::ob_sort` is `std::sort` (ob_sort.h:40); small sorts take the plain-key branch
  (10.1M calls against 1.85k encoded, threshold `card < 1000` in ob_optimizer_util.cpp). The added
  tie-break is a total order on row addresses, so tied rows come out in reverse insertion order
  whatever the algorithm does; alias3 and minitest both print ties in insertion order today.

## How to run them (PLAN.md section 4, "How the injected mutations run")

1. **Archive first (item 13).** The clean flagged binary is already archived at
   /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb with its `shasum -a 256` in ../reference.md.
   Ordinary judge runs keep using that copy, never build_release/.
2. **Check the working trees.** `git -C /Users/colin/seekdb-dev/migrate-to-rust diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py`
   must exit 0 and `git -C /Users/colin/seekdb-dev/migrate-to-rust status --porcelain -- tools/deploy`
   must print nothing. In the reference worktree, `git -C /Users/colin/seekdb-dev/ref-834bbee1e status --porcelain`
   must show only ` M cmake/Env.cmake` (reference-build.patch).
3. **For each NN, one at a time:**
   ```
   M=/Users/colin/seekdb-dev/migrate-to-rust/migration/judge/mutations
   W=/Users/colin/seekdb-dev/ref-834bbee1e
   P=$M/NN-<name>.patch
   git -C $W apply --check $P && git -C $W apply $P
   cd $W && SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk bash build.sh release --make
   RUN=/Users/colin/seekdb-dev/mysqltest-runs/mutation-NN
   mkdir -p $RUN && cp $W/build_release/src/observer/seekdb $RUN/seekdb
   H=/Users/colin/seekdb-dev/migrate-to-rust
   CLI=$W/deps/3rd/u01/obclient/bin
   python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
     --seekdb $RUN/seekdb \
     --obclient $CLI/obclient --mysqltest $CLI/mysqltest \
     --base-dir $RUN/instance --work-dir $RUN --port 3881 \
     --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace > $RUN/runner.log 2>&1
   git -C $W apply -R $P
   ```
   The build is incremental (build_release/ exists after action 11). The run covers all 272
   configured cases under the full tools/deploy/init.sql, compared with the .result files.
4. **Judge the run.** Read `failed_cases` in $RUN/seekdb_result.json and drop the cases listed in
   ../quarantine.tsv. The mutation is caught if any case is left. Check that `retried_cases` is
   empty and `ignore_trailing_whitespace` is false. Write the cases that caught it into the "Caught
   by" column above. If the instance failed to boot or run init.sql (entry-gate.json), write
   that down: the mutation then tells us little. Then delete $RUN/seekdb (about 193 MB).
5. **After the last mutation:** rebuild once more
   (`SDKROOT=... bash build.sh release --make`) and confirm that
   `git -C /Users/colin/seekdb-dev/ref-834bbee1e status --porcelain` shows only ` M cmake/Env.cmake`
   and that `git -C /Users/colin/seekdb-dev/ref-834bbee1e diff` equals ../reference-build.patch.

The gate (PLAN.md section 4, "00b's exit") needs at least 10 mutations caught.
