# Family 7: plan-cache hit and miss counts

PLAN.md section 4, item 6 and family 7: each configured case's plan-cache hits and misses, recorded
twice on the C++ reference and compared, then compared C++ against Rust. The counts come from the
runner's `run --plan-cache-stats` (../../harness/second-set/README.md, "`--plan-cache-stats`: which
counters", "what one read counts" and "what else the counters include"): the server's
`access_count` and `hit_count`, read as root before and after every case, on a fresh instance per
case, with `plan_cache_evict_interval=1d`. This directory holds what the family adds to it: a copy
of the server log of every case, the tools that find out why two recordings differ, and the list that
says which counts `compare` leaves out. What the family compares, in short: every case's `.result`
exactly, and the misses of the 70 cases that three or more recordings reached with the two reads
under 0.1 s apart and agreed on; hits of no case ("Which counts family 7 compares").

| File | What it is |
|---|---|
| record.sh | One recording: the runner command below, the working-tree, disk and port checks before it, and logwatch.py beside it |
| logwatch.py | Copies the instance's seekdb.log into `RUN/seekdb-log.gz` while the run goes on, across the fresh instances and log rotation. The runner destroys each instance, log included, after its case, and `--save-instance-dir` is not used for judge runs. It stops reading a rotated file as soon as the new one appears, so a line the server writes to the old file after the rename is lost (../wire/login_watch.py keeps reading a rotated file for 10 s); the counts do not depend on it, only differences.py's causes |
| differences.py | For two recordings: every case whose counts differ, whether hits, misses or both differ, the seconds between its two reads on each side, the background work that the server log shows between them, and the cases that failed; `--extra` adds more recordings, stopped ones too (their counts come from runner.log), and lists a case that the first two agree on but an extra one does not; the JSON also says, by the time between the reads, in how many cases hits and misses agree in every recording |
| plan_dump.py | Diagnostic runs: `make-tests` wraps configured cases so that every cached plan's executions and hits are written out right before and right after the case; `analyze` compares two runs of the wrappers plan by plan |
| build_list.py | Writes not-comparable.list and not-comparable.tsv from C++ recordings by the family's rule ("Which counts family 7 compares") |
| not-comparable.list | The list `compare --plan-cache-not-comparable` reads, pinned in the runner (`PLAN_CACHE_NOT_COMPARABLE_SHA256`, sha256 `586bdb8e207c5b264b8fb7b657dab0508d3e6a0444dba2a8c84f847c8d57696e`): all 272 configured cases, 70 as `hits` (their misses are compared) and 202 as `both`, each with its reason |
| not-comparable.tsv | The evidence behind the list: each case's hits, misses and seconds between the reads in every recording build_list.py read, and its reason |
| offline_test.py | Offline tests of `compare --plan-cache-not-comparable` and of the pin (17 tests) |
| runner-not-comparable.patch | The runner change that added `--plan-cache-not-comparable` (2026-09-28, before the pin) |

## Commands

Every judge run passes `--max-retries 0 --no-ignore-trailing-whitespace`, and before every run
`git -C /Users/colin/seekdb-dev/migrate-to-rust diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py`
must succeed. record.sh makes that check, checks that `/` has at least 8 GiB free and that the port
is not in use, and refuses a run directory that exists. One recording, with `$RUN` a new directory:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
F=$H/migration/judge/families/plan_cache
$F/record.sh $RUN /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb 3891
```

It runs, from the worktree root, with the archived client/obclient and client/mysqltest:

```
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $SEEKDB --obclient $CLI/obclient --mysqltest $CLI/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN/work --port 3891 \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --plan-cache-stats --fresh-instance-per-case \
  --seekdb-parameter plan_cache_evict_interval=1d --record-dir $RUN/rec
```

and writes `$RUN/runner.log`, `$RUN/times.txt`, `$RUN/runner.sha256`, `$RUN/work/`, `$RUN/rec/`
and `$RUN/seekdb-log.gz`. Two recordings are compared with

```
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --require-plan-cache \
  --plan-cache-not-comparable $F/not-comparable.list --out $OUT/family7.json
```

That comparison is the family's verdict: exit 0 means every `.result` is identical, the misses of
every case the list names as `hits` are identical, and there is no recording problem. To see why two
recordings differ, differences.py reads their logs:

```
python3 $F/differences.py --left A=$RUN_A --right B=$RUN_B [--extra C=$RUN_C ...] \
  --out-json $OUT/differences.json --out-tsv $OUT/differences.tsv
```

differences.py reads each recording's seekdb-log.gz only up to the last case it needs; with rec2,
whose log holds the hang described below, the run took about 14 minutes. The list is written by

```
python3 $F/build_list.py --bound 0.1 --recording rec1=$C/rec1 ... --recording rec7=<review>/f7-rec7 \
  --out-list $F/not-comparable.list --out-tsv $F/not-comparable.tsv
```

(its header holds the exact command); a new list needs a new pin in the runner. A diagnostic run of plan_dump.py's wrappers is a record.sh
run with `--test-dir` and the dump directory in the environment:

```
python3 $F/plan_dump.py make-tests --out $WRAPPERS <case>...
PC_DUMP_DIR=$RUN/dumps $F/record.sh $RUN /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb 3891 \
  --test-dir $WRAPPERS
python3 $F/plan_dump.py analyze --left $RUN_A --right $RUN_B --out-json $OUT/plans.json
```

## What a case's counts hold

A case's `hits` and `misses` are the change in the server-wide counters between the runner's two
reads (the harness README: a hit adds 1 to both counters, a statement parsed on the long path adds
1 to `access_count`, so `misses` is the change in `access_count` minus `hits`). With a fresh instance
per case they hold:

- **the case's own statements**: every lookup that finds a plan is a hit, every DML statement and
  SHOW VARIABLES parsed on the long path is a miss;
- **one miss from the runner's after-read** (the harness README, "what one read counts");
- **the version query of every connection mysqltest opens**, `select @@version_comment, @@version
  limit 1`, a hit: init_user.sql's obclient session, which connects with `-Dtest`, compiled it in
  database `test` before the case. In the idle probe below, its plan in
  `V$OB_PLAN_CACHE_PLAN_STAT` has `db_id` 500001 (`test`), was first loaded while init_user.sql ran,
  and gains one hit from the case's login;
- **the inner SQL of the logins** (mysqltest's and the after-read's) that goes through the plan
  cache. `empty_input`, whose only statement is a SET (never cached and not DML, so not counted),
  records 7 hits and 1 miss in every recording: the version query, 6 more hits, and the
  after-read's miss;
- **every other statement the server runs through the plan cache between the two reads**. Inner
  SQL uses the plan cache like user SQL (it runs through `ObSql::stmt_query` with the plan cache on,
  src/observer/ob_inner_sql_connection.cpp:72), so it is counted too, from every thread.

The last item is what makes two recordings of the same binary differ.

## Why two recordings of the reference differ

**The server's own timers.** An idle instance keeps running inner SQL. The idle probe of the live
check (probe-idle, below) slept 40 seconds in a case of its own and then listed the plans used in
that time: `probe_idle_a` recorded 202 hits and 25 misses in 39.16 s, `probe_idle_b` 203 and 25 in
39.29 s, about 5 hits a second with no user statement running. The statements it listed, with the
time between runs from their first load, last use and execution count:

| About every | Statement (database `oceanbase`) | Logged as |
|---|---|---|
| 1.7 s | `SELECT row_id, column_name, column_value FROM __all_core_table WHERE table_name = '__all_global_stat' ...` | not logged |
| 2 s | `SELECT * FROM __all_freeze_info ORDER BY frozen_scn DESC LIMIT 1` | MemstoreFreezer, ob_memstore_freezer.cpp:117 |
| 2.6 s | `SELECT MAX(schema_version) as version, host_ip() as myip, rpc_port() as myport FROM __all_ddl_operation` | not logged |
| 3 s | `SELECT * FROM __all_freeze_info WHERE frozen_scn >= 1 ...`, `SELECT * FROM __all_acquired_snapshot`, `SELECT column_value FROM __all_core_table WHERE ... 'snapshot_gc_scn'` | FreInfoReload, ob_freeze_info_manager.cpp:177 |
| 5 s | `SELECT value from oceanbase.__all_sys_stat where name = 'current_timezone_version'` | not logged |
| 5 s | `SELECT column_value FROM __all_core_table WHERE ... 'change_stream_min_dep_lsn'` | not logged |
| 10 s | `UPDATE __all_core_table SET column_value = ... 'change_stream_min_dep_lsn' ...` | CSFetcher, ob_change_stream_fetcher.cpp:452 |
| 10 s | two statements on `__all_detect_lock_info_v2` and `__all_dbms_lock_allocated` | OBJLockGC, ob_table_access_helper.h:333 |
| 10 s | four statements on `__all_vector_index_task` and its history | VecIdxSched, ob_vector_index_async_task.cpp:238 |
| 15 s | `SELECT trigger_id, is_deleted FROM __all_trigger_history ...` | not logged |
| 20 s | the job scheduler's `select * from __all_scheduler_job ...` | DBMSSched, ob_dbms_sched_job_master.cpp:372 |

Each of these is a hit once its plan is cached, and its first run after the instance starts is a
miss. The timers start with the instance, and the case's first read comes about 17 seconds later,
after the start, init.sql and init_user.sql. How many timer runs fall between the two reads therefore
depends on how long the case takes and on where the reads fall against the timers, both of which move
by some milliseconds to seconds from run to run. A case of 0.05 seconds catches a timer run in a
sizable share of recordings; a case of several seconds catches several, and a different number each
time; a case running at about 20 seconds after the start may see the first run of the 20-second
statements, which adds misses too.

**Schema refresh after DDL.** After a DDL statement commits, the `DDLTransCtr` thread takes the
highest finished DDL version (`pending_refresh_version_`) and asks for an asynchronous schema refresh
(src/share/schema/ob_ddl_trans_controller.cpp:250-270); the `SerScheQueue` thread batches the queued
requests and refreshes when the local schema is older than the request
(src/observer/ob_server_schema_updater.cpp:225-258). A refresh reads the changed schema with inner
SQL. How many DDL statements a round covers depends on thread timing, so a case with DDL runs a
different number of refresh rounds, and so of inner SELECTs, from one run to the next. The log shows
one `schedule async refresh schema task` line (ob_server_schema_updater.cpp:289) and one `try to
async refresh schema` line (:258) per round.

**DDL waits.** A DDL statement that runs as a DDL task waits for the task with a loop that reads
`__all_ddl_error_message` every 100 ms (`retry_interval`, src/sql/engine/cmd/ob_ddl_executor_util.cpp:61-119;
the query at src/share/ob_ddl_error_message_table_operator.cpp:242). Every poll is a hit, and how many
polls a wait takes depends on how long the task runs. The log brackets each wait with `start wait ddl
finsih` (ob_ddl_executor_util.cpp:75) and `finish wait ddl` (:129).

**The case's own waits.** `sleep`, `real_sleep` and polling loops in a .test make the time between
the reads longer, so more timer runs fall into it. 38 of the 39 configured geometry cases source
mysql_test/test_suite/geometry/t/import_default_srs_data_mysql.inc, which on a fresh instance loads
the 5,160 spatial reference systems and then sleeps 30 seconds.

**The statistics maintenance window at 22:00, a wall-clock effect.** Every new instance creates the
scheduler's seven window jobs, MONDAY_WINDOW to SUNDAY_WINDOW, when its tenant is created
(src/rootserver/ob_ddl_operator.cpp:3543). `get_window_job_info`
(src/sql/optimizer/stat/ob_dbms_stats_maintenance_window.cpp:213-257) starts each job at 22:00 of its
weekday in the tenant's time zone (`time_zone`, +08:00 by default; the comment's 6:00 for weekends is
not what the code does, which uses `DEFAULT_WORKING_DAY_START_HOHR`, 22, for every day,
ob_dbms_stats_maintenance_window.h:32), and for today's
job it keeps today's 22:00 while the hour is still 22 (`current_hour > default_start_hour` is false
then). So an instance created between 22:00 and 22:59 +08:00 finds today's window job already due at
the scheduler's first check, about 20.5 seconds after it starts, and an instance alive at 22:00 finds
it due then. The job runs `DBMS_STATS.GATHER_DATABASE_STATS_JOB_PROC`, which compiles the dbms_stats
package, spec and body (two `add pl package to plan cache success` lines, ob_pl_package_manager.cpp:1416),
and gathers statistics with statements that run for the first time. A case's first read comes about
17 seconds after its instance starts, so every case longer than about 3.5 seconds whose instance
started in that hour catches the job: 11 more misses and 7 more hits in most of them (13 misses when
the scheduler's own first statements fall into the reads too). rec3 ran from 22:15 to 22:58 and shows
it in 18 of the 129 cases it shares with rec1 (13:01 to 14:15), always with the two package compiles
between the reads. record.sh therefore refuses to start between 19:00 and 23:00 +08:00, so that a
recording of two to three hours never has an instance alive in that hour; outside it the window jobs
are days away and play no part. (The job ASYNC_GATHER_STATS_JOB_PROC is due 15 minutes after the
tenant is created, ob_dbms_stats_maintenance_window.cpp:179, later than any case runs except the
hang described below.)

None of this depends on the case's statements. It is how often the server's own background work
runs while the case runs, and, for the window job, what time it is.

## Which counts family 7 compares

A review of the second set found that the first not-comparable list (not-comparable.tsv of
2026-09-28 03:42, built from rec2 against rec4 with rec1 and rec3 as extras) could not be trusted: it
took `differs in` from one pair only, it was built from the pair it was then checked on, it failed on
recordings it had not seen (rec6 against rec4 gave 12 cases outside what it allowed), and 20 of its
cases were there only because of the 22:00 statistics job. The review also showed what is stable:
hits differ even in the shortest cases, and the misses of the shortest cases agree everywhere. The
family's check is now declared as a rule, built into not-comparable.list by build_list.py and pinned
in the runner:

- **Hits are compared for no case.** The server's own timers add about 5 hits a second with no
  statement running (probe-idle, the timer table above), so any case can catch one: hits differ in 29
  of the 72 cases whose reads are under 0.1 s apart, across the recordings below.
- **Misses are compared for the cases that at least three recordings reached, every one with the two
  reads under 0.1 s apart, and on which all of them agree.** A miss is a statement parsed on the long
  path, which inner SQL adds mostly in its first runs after the instance starts. Over rec1, rec2,
  rec4, rec5 and rec6 (every count from an instance alive between 22:00 and 23:01 left out, so none of
  rec3 and 23 cases of rec2), by the longest time between a case's reads in any of them:

  | Longest read gap | Cases | Misses agree | Hits agree |
  |---|---|---|---|
  | under 0.1 s | 72 | 72 | 43 |
  | 0.1 to 0.2 s | 31 | 30 | 6 |
  | 0.2 to 0.5 s | 32 | 31 | 5 |
  | 0.5 to 1 s | 13 | 12 | 1 |
  | 1 to 5 s | 28 | 14 | 5 |
  | 5 to 20 s | 21 | 3 | 1 |
  | 20 s or more | 52 | 43 | 9 |

  (23 cases were reached by one recording only and count in no row.) Misses differ from 0.1 s on
  (func_group_1: 106 or 105), so the bound is 0.1 s.
- **Every other case is listed as `both`:** nothing about its counts is compared, whatever the cause
  (the timers, DDL refresh rounds, DDL waits, sleeps in the test). Its `.result` is compared exactly,
  as for every case.

**How the list was checked, and changed once.** The first list took two recordings as enough and was
built from rec1-rec6: 72 cases as `hits`. It was checked on rec7, a recording it had not seen (made
after it, 2026-09-28 23:00 to 2026-09-29 01:01): rec4 against rec7 exited 1 on one case,
information_schema.information_schema_desc, whose misses were 17 in rec2 and rec4 (the only two
recordings that had reached it) and 18 in rec7, with its reads 0.056 and 0.065 s apart and no
difference in the logged timers (differences.py): some inner SQL the log does not show was parsed for
the first time between rec7's reads. So two recordings are too few. The list was rebuilt from rec1-rec7
with three recordings required, and pinned at 01:25 on 2026-09-29, while rec8 was still recording:
70 cases as `hits` (information_schema_desc and func_group_6, whose reads were 0.108 s apart in rec7,
dropped out; the first list is kept as review/f7-list-v1.list). rec8 (00:32 to 02:32), which no list
had seen, then checked it:

| Comparison with the pinned list | Exit | `.result` | Plan cache |
|---|---|---|---|
| rec7 against rec8 (rec8 held out) | 0 | 272 identical | 97 identical, 0 different, 175 not compared |
| rec4 against rec8 (rec8 held out) | 0 | 272 identical | 93 identical, 0 different, 179 not compared |
| rec4 against rec7 | 0 | 272 identical | 84 identical, 0 different, 188 not compared |
| rec7 against the mutated recording | 1 | 272 identical | 0 identical, 70 different, 202 not compared |
| rec8 against the mutated recording | 1 | 272 identical | 0 identical, 70 different, 202 not compared |

The first list on rec8 would have passed rec4 against rec8 and failed rec7 against rec8 on the same
one case. So family 7 has a clean C++ pair on a list that the pair did not help build, and the
mutation below changes every count the list compares. The margin is thin: over eight recordings one
short case moved by one miss once, and a new recording can do that again in another case. A
difference in one listed case, by one miss, is therefore first recorded again on both builds before
it counts as a finding; the mutation below changes all 70.

So family 7 compares, per recording pair, 272 `.result` files and the misses of 70 cases. That is
narrow, but it is what the reference reproduces, and it catches the kind of bug the family is for.
The list's header records the command and the recordings it read; build_list.py reads the counts from
each recording's plan_cache.tsv or, for a stopped one, runner.log, and drops the counts of any case
whose instance could be alive between 22:00 and 23:01 (`skip_window_hour` of differences.py).

What would compare more: counting per plan instead of per server. The diagnostic runs diag1 and diag2
(below) dumped every cached plan's executions and hits before and after each of 21 cases, twice. The
plans outside database `oceanbase` and without the `DBMS_STATS` hint, which hold the case's own
statements and each login's version query, had the same executions and hits in both runs in 20 of the
21 cases (executor.basic differs only in statistics statements the server runs in the case's
database, whose text holds the remaining query timeout). Reading those plans instead of the
server-wide counters would compare the hits and misses of the case's own statements in most cases.
It needs a runner option and new recordings, and it changes what the family measures (the server's
own inner SQL would no longer count), so it is left as the way to widen the family later.

## The PURGE RECYCLEBIN race

In rec2, vector_index.create_table_with_vector_index hung in its last statement, `PURGE RECYCLEBIN`
(test line 232), from 21:47:11 until the DDL timeout of 1,000 s (`_ob_ddl_timeout`,
src/share/parameter/ob_parameter_seed.ipp:748), and failed with `4012: Timeout` after 1,009.9 s.
seekdb.log shows the statement's session (trace YB427F000001-00065C772FB6BF2E) trying the table lock
of tablet 200141, already deleted, 72,309,760 times from 21:47:11.556 to 22:03:53.619: `tablet is
already deleted` (OB_TABLET_NOT_EXIST, src/storage/tablelock/ob_table_lock_local_executor.cpp:64-68),
each followed by `execute table lock task` (src/storage/tablelock/ob_table_lock_service.cpp:1333).
Every case runs on its own instance here, so the deleted tablet was one of the case's own tables:
no leftover object of an earlier case is needed for the hang. The evidence is in
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/05-plan/analysis/rec2-vector-failure/purge-hang-evidence.txt.
The race has hung this and the other purge cases in other families too; since 2026-09-28 the seven
configured cases that send `PURGE RECYCLEBIN` are on ../../quarantine.tsv, with one rule for every
family: such a case is compared while two C++ recordings agree on it, and a recording that stops on
this hang is made again. A recording with a failed case is a recording problem to `compare`, so no
comparison with rec2 passes.

## Comparing a Rust build

Record the Rust build with record.sh exactly as the C++ recordings were made (same options, clients
and init files, outside 19:00-23:00 +08:00) and compare it with a C++ recording that finished with no
failed case (rec4, rec7 or rec8, below):

```
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $C/rec4/rec --right $RUN_RUST/rec --require-plan-cache \
  --plan-cache-not-comparable $F/not-comparable.list --out $OUT/family7.json
```

with `C=/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/05-plan` (rec7 and rec8 are under
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/f7-rec7 and f7-rec8). Exit 0 is a pass:
the `.result` files and the misses of the 70 listed cases are identical. A `plan-cache different` line
names a case whose misses changed; differences.py on the two recordings shows what ran between the
reads.

## Live check, 2026-09-25 to 2026-09-28

All runs used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256
db7d9180…), its client/obclient and client/mysqltest, the full init, `--max-retries 0
--no-ignore-trailing-whitespace --plan-cache-stats --fresh-instance-per-case --seekdb-parameter
plan_cache_evict_interval=1d` and no `--save-instance-dir`; record.sh made the working-tree, disk and
port checks before each (the probes used run.sh in the output directory, which makes the same
checks). Unit 05-plan made the runs up to rec6 on port 3891, over four days because two of its
sessions were cut short; outputs under /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/05-plan/
(runner.log, times.txt, runner.sha256, work/, rec/, seekdb-log.gz), analyses under analysis/.

| Run | When (+08:00) | What | Result |
|---|---|---|---|
| probe-1d | 2026-09-25 12:49, 75 s | a SELECT whose plan is left idle for 40 s (`--real_sleep 40`) | `show parameters` prints `plan_cache_evict_interval` `1d`; the idle plan was still found after the sleep (`executions` 2 then 3, `hit_count` 1 then 2) |
| probe-idle | 2026-09-25 12:52, 2 min 47 s | the same CREATE and DROP twice, an empty case, and two cases that sleep 40 s and list the plans used meanwhile | 46 hits and 33 misses for both DDL cases, 7 and 1 for the empty case, 202/25 and 203/25 for the idle cases (the timer table above) |
| rec1 | 2026-09-25 13:01 to 14:15 | the 272 cases | stopped by a usage limit after 176 cases; its counts, read from runner.log, serve as an extra recording |
| rec2 | 2026-09-27 19:57 to 22:15 | the 272 cases | finished in 2 h 17 min, exit 1: vector_index.create_table_with_vector_index failed (the purge race, above); 23 cases ran with an instance alive between 22:00 and 23:01 |
| rec3 | 2026-09-27 22:15 to 22:58 | the 272 cases | stopped after 129 cases, all in the window hour, so none of its counts is used |
| rec4 | 2026-09-28 00:59 to 02:59 | the 272 cases | finished in 2 h 0 min, exit 0: 272 recorded, none failed |
| diag1, diag2 | 2026-09-28 03:00 to 03:15, 7 min 42 s each | plan_dump.py's wrappers of 21 cases and a login probe, `--test-dir` | all passed; analysis/diag12.txt and .json ("What would compare more", above) |
| rec5 | 2026-09-28 03:24 to 03:43 | the 272 cases | stopped after 62 cases |
| rec6 | 2026-09-28 08:41 to 09:51 | the 272 cases | stopped after 171 cases; the review used its counts to show that the first list failed on a recording it had not seen |
| compare rec2 rec4 | – | `compare --require-plan-cache`, no list (analysis/r2r4/compare.log) | exit 1: `.result` 271 identical, 1 missing (rec2's failed case), 1 recording problem; plan cache 93 identical, 179 different |
| rec7 | 2026-09-28 23:00 to 2026-09-29 01:01 | the 272 cases, by the unit that applied the reviews (review/f7-rec7, port 3891; another instance recorded other families on 3892 meanwhile) | finished in 2 h 0 min, exit 0: 272 recorded, none failed, no purge hang |
| rec8 | 2026-09-29 00:32 to 02:32 | the same (review/f7-rec8, port 3892) | finished in 2 h 0 min, exit 0: 272 recorded, none failed, no purge hang |
| compares with the pinned list | – | "How the list was checked, and changed once", above | rec7 against rec8 and rec4 against rec8, rec8 held out: exit 0 |

What the runs confirmed:
- **The eviction timer is off.** probe-1d's plan survived 40 idle seconds, and the saved logs (the
  probes, rec5 and rec6 read in full on 2026-09-28) have no `schedule next cache evict task` or `idle
  eviction collected plans` line, or a `plan cache memory used reach limit` warning.
- **The per-instance self-check passes.** Every instance of rec2 and rec4 (272 each) passed the check
  of two reads back to back (0 hits, 1 access) on its first attempt, and runner.log shows every check
  of the stopped recordings passing.
- **empty_input**, which runs no cached statement, records 7 hits and 1 miss in all six recordings.

What the live check changed in this directory (unit 05-plan): differences.py lists failed cases, says
whether hits, misses or both differ, counts package compiles and marks the window hour; record.sh
refuses to start between 19:00 and 23:00 +08:00; the runner gained `--plan-cache-not-comparable`
(../../harness/README.md). The first not-comparable.tsv and the draft sections of this README from
that unit are replaced by the rule and sections above; the old table is kept at
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/f7-old-not-comparable.tsv.

## The mutation for the second sign-off

../../mutations/second-set/05-plan-key-includes-session-id.patch, with its note beside it: in
`ObPlanCache::construct_plan_cache_key` (src/sql/plan_cache/ob_plan_cache.cpp) the key of every
statement from a user session gets that session's id, so a plan compiled by one session is never
found by another. Results do not change; in every case the version query of mysqltest's login turns
from a hit into a miss. Its recording of 2026-09-28 (272 cases, none failed), compared with the pinned
list against rec4, rec7 and rec8, exits 1 each time with all 70 compared cases `different` and every
`.result` identical: caught by the family's own `compare`, with no separate script.
