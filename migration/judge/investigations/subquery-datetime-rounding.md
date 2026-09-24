# Why subquery.idx_with_const_expr_21_subquery_dilang failed in one pass

PLAN.md section 10, action 4: explain from the source why
tools/deploy/mysql_test/test_suite/subquery/t/idx_with_const_expr_21_subquery_dilang.test failed in
pass B of the coverage run and passed in pass A. Read-only work: nothing was built or run.

## Verdict

**The hypothesis in PLAN.md section 4 is contradicted by the source.** An INSERT does not round
`now()` up to the next second. `now()` with no argument has scale 0 and is truncated to the whole
second when it is evaluated, so the value that reaches the `DATETIME` column already has zero
microseconds, and the rounding in the column conversion has nothing to round.

The case is still timing-dependent, but for a different reason. The select's `now()` and
`current_timestamp()` are truncated the same way, so `date_add(now(), interval -1 microsecond)` is
the start of the select's second minus 1 µs. A row inserted in that same second is later than that
bound. **The nine `-1 microsecond` selects return no rows exactly when the three `now()` inserts
(test lines 55-57) and the selects (lines 103-121) start in the same wall-clock second**, and return
the three rows when a second boundary falls between them. In pass B the inserts ran at about
16:51:32.20 and the selects at about 16:51:32.86 (server log, below), and the stored value decodes to
16:51:32.000000. The inserts ran about 0.2 s into the second, where rounding half up would not
have moved them either, and the case still failed.

Two consequences:
- A faster build fails **more** often, not "as often": the gap between the inserts and the selects
  (about 0.65 s in pass B, mostly three `CREATE INDEX`) is what gives a second boundary a chance to
  fall between them.
- Action 7's probe should record the second of the inserts and the second of the selects, not
  where in the second the inserts land (see "What this means for action 7").

## Scope of the source reading

Every file cited below is read at 834bbee1e (the worktree's frozen base). None of them is among the
23 files that differ between 076eb309b (the build that failed) and 834bbee1e (`git diff --stat
076eb309b 834bbee1e`; its only changes under src/sql are ob_table_replace_op.{cpp,h} and
ob_transform_const_propagate.cpp). The test, its .result and tools/deploy/mysql_test/include are
identical at both commits and in the worktree.

## The test and the failure

- `a5 datetime` has no fractional-second digits (test line 30).
- Lines 55-57 insert GG3, GG2 and GG1 with `now()` as `a5`.
- Between them and line 103 the test runs 7 inserts into t2, three `CREATE INDEX` (lines 69-71) and
  7 selects. It sleeps nowhere: tools/deploy/mysql_test/include/check_all_idx_ok.inc (line 73) is
  entirely commented out.
- Lines 103-121 run nine selects of the form `a5 >= <now or current_timestamp> - 60 minute and a5 <=
  <now or current_timestamp> - 1 microsecond`, through `date_add(..., interval -N ...)` or
  `date_sub(..., interval N ...)`.
- The .result expects GG1, GG2 and GG3 from each of the nine (.result lines 139-183).
  migration/judge/coverage-076eb309b/pass-B.subquery.idx_with_const_expr_21_subquery_dilang.diff
  shows all nine empty and nothing else different. The runner timed the case at 0.704 s in pass A
  and 0.811 s in pass B (line 450 of pass A's runner.log, line 502 of pass B's, both under
  /Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/).

## How the parser and resolver type `now()`

1. `NOW()`, `CURRENT_TIMESTAMP`, `CURRENT_TIMESTAMP()`, `LOCALTIME` and `LOCALTIMESTAMP` all become
   `T_FUN_SYS_CUR_TIMESTAMP`. Without a digit in the parentheses the node has no child
   [added by the check: more exactly, the node has one child slot and it is NULL;
   `process_timestamp_node` requires `num_child_ == 1` and reads a NULL child as scale 0]
   (src/sql/parser/sql_parser_mysql_mode.y:3395-3421 `cur_timestamp_func`, :3423-3427
   `now_synonyms_func`, :3494-3498 `opt_time_func_fsp_i`, where `()` and nothing both give
   `$$[1] = 0`). So `now()` and `current_timestamp()` in the selects are the same function.
2. The resolver gives a node without a child scale 0: `process_timestamp_node`
   (src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:3826-3834) and `c_expr->set_scale(scale)` at
   :3844, reached from the `T_FUN_SYS_CUR_TIMESTAMP` case at :781-789.
3. `ObExprCurTimestamp::calc_result_type0` makes the result `ObMySQLDateTimeType` when MySQL-compatible
   dates are on and keeps a scale of 0, since `MIN_SCALE_FOR_TEMPORAL` is 0
   (src/sql/engine/expr/ob_expr_cur_time.cpp:204-217; src/oblib/lib/ob_define.h:1543). Type
   deduction copies the resolver's scale into the result type for this function
   (src/sql/resolver/expr/ob_raw_expr_deduce_type.cpp:608-620). MySQL-compatible dates are on by
   default (`_enable_mysql_compatible_dates` defaults to "True",
   src/share/parameter/ob_parameter_seed.ipp:319), and the pass-B log confirms it (below).

## How `now()` is evaluated

4. The time comes from the statement's plan context, set once per execution. A plan built for this
   execution sets it with `ObTimeUtility::current_time()` (src/sql/ob_sql.cpp:2372-2376 and
   :3919-3924); a plan-cache hit sets it with `ObClockGenerator::getClock()`
   (src/sql/plan_cache/ob_plan_set.cpp:138-140). `ObPhysicalPlanCtx::set_cur_time` uses the
   session's `timestamp` variable instead when it is set, which this test does not do
   (src/sql/engine/ob_physical_plan_ctx.cpp:160-170).
5. `ObExprCurTimestamp::eval_cur_timestamp` converts that time to the session time zone and then
   **truncates** it to the expression's scale: `timestamp_to_mdatetime` then
   `trunc_mdatetime(expr.datum_meta_.scale_, mdt_value)` for `ObMySQLDateTimeType`
   (src/sql/engine/expr/ob_expr_cur_time.cpp:233-241), or `trunc_datetime` for `ObDateTimeType`
   (:243-247).
6. `trunc_mdatetime` removes the digits below the scale, `value.microseconds_ -=
   (value.microseconds_ % power_of_10[6 - scale])`, which at scale 0 sets the microseconds to 0
   (src/oblib/common/timezone/ob_time_convert.cpp:1768-1773; `trunc_datetime` does the same on the
   plain datetime at :1760-1766). There is no rounding anywhere in this function.

So `now()` in the INSERT evaluates to the whole second in which the INSERT started, rounded down.

## How the INSERT stores the value into `a5`

7. `a5 datetime` without digits has scale 0 (src/sql/parser/sql_parser_mysql_mode.y:5411-5414 and
   :5908-5911, where no digits gives 0), and the column type becomes `ObMySQLDateTimeType` when
   MySQL-compatible dates are on (src/sql/resolver/ob_resolver_utils.cpp:4350-4362).
8. The INSERT converts each value to its column with `ObExprColumnConv::column_convert`, which carries
   `CM_COLUMN_CONVERT` in its cast mode (src/sql/engine/expr/ob_expr_column_conv.cpp:266-268 when the
   type is deduced, :351-352 when the expression is generated) and calls
   `column_convert_datum_accuracy_check` (:361-380, called at :452).
9. That calls `datum_accuracy_check` (src/sql/engine/expr/ob_datum_cast.cpp:11314)
   [added by the check: through the seven-argument overload at :11273-11291, which builds the
   accuracy from the column conversion's own `datum_meta_.scale_`, the column's scale 0, and passes
   it on to :11314], which sends
   `ObMySQLDateTimeTC` to `mdatetime_scale_check` (:11386-11388). There, for a column conversion, the
   fractional part is truncated only if `TIME_TRUNCATE_FRACTIONAL` is in the cast mode; otherwise it
   is rounded with `round_mdatetime` (:10660-10697, the choice at :10672 and :10682-10687).
   `datetime_scale_check` does the same for `ObDateTimeType` (:10579-10614).
10. `TIME_TRUNCATE_FRACTIONAL` is off here. The cast mode gets `CM_TIME_TRUNCATE_FRACTIONAL` only from
    the sql_mode bit (src/sql/ob_sql_utils.cpp:1171-1173 [added by the check: and the same test in
    the other two `get_default_cast_mode` overloads, :1204 and :1256];
    src/oblib/common/sql_mode/ob_sql_mode.h:58
    defines it as bit 33), and the default sql_mode, 281018368
    (src/share/system_variable/ob_system_variable_init.cpp:341), does not have bit 33 set
    [checked: 281018368 has only bits 22, 23 and 28 set]. So the
    column conversion rounds, half up (`round_mdatetime`,
    src/oblib/common/timezone/ob_time_convert.cpp:1674-1690).
11. But the value it receives already has zero microseconds (step 6), so the rounding changes
    nothing. The stored `a5` is the INSERT's whole second, rounded down.

The rounding that PLAN.md section 4 relies on exists and is the default for fractional values
inserted into a scale-0 column (for example the literal `'2026-09-24 16:51:32.7'`). It never sees a
fraction from `now()`: only `now(N)` with N > 0, or `sysdate(N)`, would hand it one.

## How the `-1 microsecond` selects compare

12. `date_add(now(), interval -1 microsecond)` and `date_sub(..., interval 1 microsecond)` have type
    `ObMySQLDateTimeType` (src/sql/engine/expr/ob_expr_date_add.cpp:66-68) and scale 6 because the
    unit is `MICROSECOND` (:88-93, :103-104, :116). `ObExprDateAdjust::calc_date_adjust` computes it
    with `ObTimeConverter::date_adjust` (:126, the call at :201). The value is the select's whole
    second minus 1 µs, that is `hh:mm:(ss-1).999999`.
13. The comparison with `a5` needs no cast. Both sides are `ObMySQLDateTimeType`, so
    `ObSQLUtils::is_same_type_for_compare` is true (src/sql/ob_sql_utils.cpp:2568-2582) and
    `ObRangeGenerator::try_cast_value` leaves the range bound as it is
    (src/sql/rewrite/ob_range_generator.cpp:1115-1128). If index i5 is used, the range keeps the
    microsecond bound. `ObMySQLDateTime` compares its packed 64-bit value
    (src/oblib/common/timezone/ob_time_def.h:59-63), whose fields run from the microseconds in the
    low bits up to the year and month (:70-80), so it orders by time.
14. So a GG row is returned exactly when its stored second is at most the select's second minus
    1 µs, that is when **the INSERT's second is earlier than the select's second**. When both fall
    in the same second, every one of the nine selects excludes all three rows.

## What pass B's server log shows

From /Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/pass-B/failures/instance/seekdb_log/seekdb.log
(read-only; the times are the server's wall clock):

| When | What | Evidence |
|---|---|---|
| 16:51:32.203 | first statement on t1 after it is created: the insert at test line 40 | trace `...CDB0`: schema lookup of t1 and `check_read_snapshot_for_normal` on its tablet |
| 16:51:32.208 | first statement on t2: the insert at test line 60 | trace `...CDC2`, the same on t2 |
| 16:51:32.210-32.424 | `create index i2 on t1(a2)` | trace `...CDC9`, `ddl_stmt_str` at log line 44438 |
| 16:51:32.424-32.637 | `create index i3 on t1(a3)` | trace `...CDCA`, log line 56243 |
| 16:51:32.637-32.853 | `create index i5 on t1(a5)` | trace `...CDCB`, log line 57827 |
| 16:51:32.855-32.884 | the 16 selects | traces `...CDCC` to `...CDDB`; `...CDDC` closes the connection at 32.884 |

From the first statement on t1 (`CDB0`) to just before the first `CREATE INDEX` (`CDC9`) there are
exactly 25 trace ids, matching the 18 inserts into t1 (`CDB0` to `CDC1`) and the 7 into t2 (`CDC2`
to `CDC8`). After the last `CREATE INDEX` (`CDCB`) and before the connection closes (`CDDC`) there
are exactly 16, matching the 16 selects (`CDCC` to `CDDB`).

[added by the check: the two counts are arithmetic on the id numbers. They hold only if every
statement takes one id and nothing else draws from the counter in between; the ids of the GG
inserts (`CDBF` to `CDC1`) and of eight of the nine selects (`CDD4` to `CDDB`) have no log lines at
all. `CDC8` does have 145 lines (32.219-32.831), but all of them are on the log-apply thread
ApplySrv0, replaying the creation of tablet 207379, which `CDC9` (`create index i2`) created at
log line 44418; the apply thread still carried an old trace id, so those lines do not time a
statement. The brackets below do not depend on the counts. mysqltest sends the statements of one
connection one at a time and waits for each result, and four statements are identified by the
tablet they first touched, not by their id: `CDB0` touches t1's tablet 207377 (the insert at line
40), `CDC2` touches t2's tablet 207378 (created under `CDAF`; the insert at line 60), `CDD0` is the
first touch of i3's tablet 207380 (the select at line 91; `CDCA`, `create index i3`, names that
tablet), and `CDD3` is the first touch of i5's tablet 207381 (the select at line 103, which
therefore read index i5; the i5 build at log line 58178 names the same tablet). So the GG inserts
ran between 32.203 and 32.208, and the nine `-1 microsecond` selects ran from 32.864194 (`CDD3`,
the first of them) until before the connection closed at 32.884475.]

- The GG inserts (`CDBF` to `CDC1`) come after the first t1 insert and before the first t2 insert,
  so they ran between about 32.20 and 32.208. The nine `-1 microsecond` selects (`CDD3` to `CDDB`)
  ran between 32.864 (the first log line of `CDD3`) and 32.884. Both are in the second 16:51:32.
- The i5 build logged GG3's index key (log line 58178, trace `...CDCB`): `a5` is
  `hex: 000000E00CF1BA19`, the 64-bit value 0x19BAF10CE0000000. Decoded with the `ObMySQLDateTime`
  layout (src/oblib/common/timezone/ob_time_def.h:70-80) it is 2026-09-24 16:51:32 with 0
  microseconds. This also shows the column is `ObMySQLDateTimeType`: as a plain `ObDateTimeType`
  the same number would not be a valid date.
- The inserts ran about 0.2 s into the second, so rounding half up would also have stored 16:51:32.
  The hypothesis needs an insert in the second half of a second; pass B failed without one.
- [added by the check] The log shows the select side directly. The stored rows are 16:51:32.000000
  and all nine selects, run at 32.86-32.88 by the server's clock, excluded them, so their upper bound
  was below 32.000000. With the untruncated time the bound would have been about 32.86, and the
  rows would have been returned. So the selects' `now()` and `current_timestamp()` were the whole
  second, as steps 5 and 6 say. For the INSERT side the log cannot tell truncation from rounding
  half up, because the inserts ran about 0.2 s into the second; there the source reading (steps
  4-11) is the only evidence. It is the same function at the same scale 0 as on the select side,
  and the only step that belongs to the INSERT is the column conversion of step 9, which receives
  zero microseconds.

Pass A passed, which by step 14 means a second boundary fell between its inserts and its selects.
Its saved server log starts at 16:27:20 and does not contain this case (it has no `create index i5
on t1(a5)`), so pass A's timestamps are not available.

## Other causes ruled out

- **The `-60 minute` lower bound.** It is the select's whole second minus 3600 s, and the GG rows
  are less than a second older than the select, so it holds in both passes. It excludes nothing.
- **Plan cache.** The current time is set per execution and never kept in a cached plan (step 4).
  A cache hit reads `ObClockGenerator::getClock()`, a value a background thread refreshes about
  every 10 ms (src/oblib/lib/time/ob_clock_generator.cpp:91, :118-122;
  src/oblib/lib/time/ob_clock_generator.h:90-100). It can therefore be about 10 ms behind the real
  clock. In this test GG2 and GG1 can reuse GG3's plan (the same text once the literals are
  parameterized), and the nine selects are nine different texts on a newly created table. A 10 ms offset can move which
  second a statement falls in only within 10 ms of a boundary. It cannot empty nine results the way
  pass B shows.
- **Time zone.** The INSERT and the selects run in the same session, and both convert with the
  session's time zone (src/sql/engine/expr/ob_expr_cur_time.cpp:234). The stored value and the
  bound shift together, and a time-zone offset is a whole number of minutes, so it does not move
  second boundaries.
- **Index i5 missing rows.** The i5 build wrote GG3's key (log line 58178), and the same selects
  return the rows in pass A. The same-second rule explains the empty results without any storage
  fault.

## How often the case should fail

This is an estimate, not a measurement. It assumes the INSERT's position within its second is
uniformly spread. With a gap of `d` seconds (d < 1) between the GG inserts and the first
`-1 microsecond` select, all nine selects come back empty with probability about `1 - d`. The
result is mixed only when a boundary falls inside the about 20 ms of the nine selects or the few ms of
the three inserts. In pass B, `d` was about 0.65 s, almost all of it the three `CREATE INDEX` at
about 0.21 s each, so about 1 run in 3 should fail on that build. On a build whose `CREATE INDEX`
is faster, `d` is smaller and the case fails more often. It never becomes deterministic while
`d` < 1 s.

The runner in 834bbee1e retries a failed case up to 3 times (`MAX_CASE_RETRIES = 3`,
.github/script/seekdb/mysqltest_for_seekdb.py:19, loop at :502), which would hide a failure rate of
this size in CI. Why the checked-in .result shows the rows is not checked here. It is what any run
produces when a second boundary falls between the inserts and the selects. [added by the check, an
inference about where the .result was recorded, not checked: wherever the three `CREATE INDEX`
together take a second or more, `d` is at least 1 s, a boundary always falls in between, and the
case always passes.]

## What this means for action 7 and PLAN.md

- **Action 7's probe.** The deciding fact is whether the inserts and the selects share a second,
  not where in the second the inserts land. In the investigation copy, add `select a1, a5 from t1
  where a1 like 'GG%'` after line 57 (the stored second) and `select now(6)` just before line 103
  (the select's second). The prediction: all nine selects are empty exactly when the second of
  `now(6)` equals the stored `a5`. Across 20 runs of the unchanged case on the archived reference,
  expect a failure rate of at least the instrumented build's, about 1 in 3 or more.
  [added by the check: also add `select now(6)` just before line 55. The stored `a5` against the
  fraction of that time then tests the INSERT side directly, which pass B's log cannot: truncation
  stores the second of that time, and rounding half up would store the next second whenever the
  fraction is 0.5 or more. About half the 20 runs should land in the second half of a second.]
- **PLAN.md wording (not edited here).** Section 4's quarantine row gives the rounding reason and
  says "a faster build may fail as often as the instrumented one". Family 5 (section 4) says "the
  rounding of fractional seconds is the likely cause of a quarantined case". The coverage README
  (migration/judge/coverage-076eb309b/README.md) says the same. All three should say that `now()` is
  truncated to the whole second on both sides and the case fails when the inserts and the selects
  share a second.
  [added by the check: two more places in PLAN.md give the rounding cause. Section 8, row 1 says
  "Likely the whole-second `DATETIME` rounding of `now()`". Section 10, action 7 asks to "log where
  in the second the inserts land against pass or fail" and decides with "If the rounding explains
  every failure, add the case to quarantine.tsv with that reason". That condition is the gate for
  listing the case, so it should test the rule found here: the case is listed if, in every run,
  the nine selects are empty exactly when the stored `a5` and the `now(6)` before line 103 fall in
  the same second.]
- migration/judge/quarantine.tsv lists the case with the source-backed reason and the status
  "pending: confirm on the reference (action 7)", as PLAN.md requires before listing it.
  [added by the check: whatever reads quarantine.tsv in the validation runs (item 12) has to treat
  a row whose status starts with "pending" as not listed, until action 7 changes that status.]

## Adversarial check (2026-09-24)

A second reader checked this file against the source at 834bbee1e and against pass B's server log.
Read-only: nothing was built or run. Additions are marked "[added by the check: ...]" and
"[checked: ...]" above.

- **The verdict holds.** Every cited file:line says what the text says. The path an INSERT of
  `now()` into `a5 datetime` takes: the parser gives `T_FUN_SYS_CUR_TIMESTAMP` with a NULL child;
  the resolver sets scale 0 and type deduction copies it into the result type;
  `ObExprCurTimestamp::cg_expr` sets `eval_cur_timestamp`, which truncates to scale 0 with
  `trunc_mdatetime`; the value reaches `column_convert` with zero microseconds, and
  `mdatetime_scale_check` rounds it half up at scale 0, which changes nothing. So the INSERT
  truncates; the rounding step exists but has nothing to round.
- None of the cited files is among the 23 that differ between 076eb309b and 834bbee1e, and the
  test, its .result and tools/deploy/mysql_test/include are the same at both commits and in the
  worktree.
- Nothing else in the path changes the value: both range-cast functions at 834bbee1e,
  `ObRangeGenerator::try_cast_value` (src/sql/rewrite/ob_range_generator.cpp:1115-1128) and
  `ObKeyPart::try_cast_value` (src/sql/rewrite/ob_key_part.cpp:882-889), leave a bound of the
  column's own type uncast, and the empty results show the bound was not rounded to scale 0 (it
  would then have been 32.000000 and the rows would have matched); the `now()` that
  `ObRawExprUtils::build_nvl_expr` builds with the column's scale is used only for a NOT NULL
  `TIMESTAMP` column (src/sql/resolver/dml/ob_dml_resolver.cpp:5850), and `a5` is `DATETIME`; and
  the plan-cache parameterization keeps `now()`
  and its digits as written (`is_tree_not_param`, src/sql/plan_cache/ob_sql_parameterization.cpp:315-316).
- The log facts were checked: GG3's i5 key at log line 58178 decodes to 2026-09-24 16:51:32.000000;
  the times of `CDB0`, `CDC2`, `CDC9` to `CDCB`, `CDD3` and the connection close are as stated; the
  pass-B diff has one hunk, the nine empty results; pass A's saved logs cover 16:27:20-16:28:34 and
  do not contain the case.
- What the check added: the log alone shows the select side is truncated, while the INSERT side
  rests on the source (section "What pass B's server log shows"); the trace-id counts are inference
  and the time brackets rest on tablet ids instead; the probe also records `now(6)` before the
  inserts; two more places in PLAN.md to reword, including action 7's decision rule.
- quarantine.tsv matches PLAN.md section 4: five tab-separated columns in the order of action 4,
  the four seed rows as in PLAN.md (with "section" for "§"), and the subquery row pending as action
  4 asks. Unlike the seed rows, its status does not record the coverage outcome (passed in pass A,
  failed in pass B); it is left as written.
