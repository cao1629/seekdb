# 11-conc: the session sweep cancels a commit that is still in time

Family 11, concurrency (PLAN.md section 4, family 11; families/concurrency/), unit 11-conc.
Patch: 11-conc-session-sweep-cancels-live-commit.patch, one .cpp file, one line changed.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-28, against the worktree
with only reference-build.patch's cmake/Env.cmake change). Not built.

## What it changes

src/storage/tx/ob_tx_api.cpp, `data_plane::cancel_timed_out_tx_commit`, line 1801 at 834bbee1e:

```
-    } else if (desc->is_tx_commit_timeout()) {
+    } else if (!desc->is_tx_commit_timeout()) {
```

Every 5 seconds the session manager's timer task visits every session
(`ObSQLSessionMgr::runTimerTask` and `CheckSessionFunctor`, src/sql/session/ob_sql_session_mgr.cpp:367
and :496-554; `SCHEDULE_PERIOD`, ob_sql_session_mgr.h:49; scheduled at src/observer/ob_server.cpp:2297).
It checks a session only when it can take the session's query lock without waiting
(`try_lock_query`, ob_sql_session_mgr.cpp:509), so never while a statement of that session is being
processed. For a session in a transaction it calls `ObBasicSessionInfo::is_trx_commit_timeout`
(src/sql/session/ob_basic_session_info.cpp:4375-4389), which calls this function. The function looks
only at a transaction whose commit is in flight (`is_committing()`, state `IN_TERMINATE`,
src/storage/tx/ob_trans_define_v4.h:425-427): the client's COMMIT has been handed to the transaction
layer, and the answer to the client waits for the commit's callback. At 834bbee1e the function
takes that callback away from the transaction (`cancel_commit_cb`, ob_trans_define_v4.cpp:926-946)
only when the transaction has timed out, or when the commit has passed its own deadline
(`is_tx_commit_timeout()`: `commit_expire_ts_ > 0` and the clock past it, ob_trans_define_v4.h:446);
the sweep then answers the client with that timeout (ob_sql_session_mgr.cpp:547-550).

With the patch the second test is the wrong way round. When the sweep finds a commit in flight that
is still within its deadline, which is the normal case, it takes the callback away, and the client
gets OB_TRANS_STMT_TIMEOUT, MySQL error 4012 "Statement timeout occurred, please set the variable
ob_query_timeout to a larger value an then restart the statement" (src/share/ob_errno.def:1393). The
transaction layer is not told to stop: `cancel_commit_cb` only detaches the callback, so what changes
is the answer the client gets. A commit that really passed its deadline is now left alone, which a
sequential test does not see either. This is the kind of mistake a translation makes when it writes
the deadline test as "time left" instead of "time passed", or swaps the two sides of the comparison.

## Why family 11 catches it (the exact output that changes)

The sweep only finds a commit in flight when some other session is committing at the moment it runs,
and sysbench's multi-thread `oltp_read_write` runs give it that: every event ends with an explicit
COMMIT (oltp_common.lua `commit()`, sent as text because the family runs these with
`--db-ps-mode=disable`), and 16 or 64 sessions commit all the time. The coverage diagnostic below
counted how often the sweep reached the committing branch in the family's own sequence: once during
the one-thread run, 12 times during the 16-thread run and 47 times during the 64-thread run. The
prepare and the `oltp_point_select` runs never reach it (autocommit statements are not in a
transaction between statements).

With the patch, the first of those finds answers that session's COMMIT with 4012. sysbench ignores
only 1213, 1020 and 1205 (`--mysql-ignore-errors`), so it prints a FATAL line of the form
`<call> returned error 4012 (Statement timeout occurred, ...) for query 'COMMIT'`
(drivers/mysql/drv_mysql.c:766 in sysbench 1.0.20) and exits with a non-zero status.
sysbench_parity.py then writes, for that case (normally `oltp_read_write_16`; `oltp_read_write_1` if
the sweep happens to meet its single session in a commit), a `.partial` holding

```
-- check: sysbench exited with status 0 and printed no FATAL line: no
-- sysbench exit code 1; error codes in its FATAL lines: 4012
```

followed by the tail of sysbench's output, and records the later cases as not run. The recorded
cases before it are unchanged. `compare` against a reference recording exits 1: the right recording
has a failed case and did not finish its later cases, and the case differs.

The reference never answers a COMMIT with 4012 here: two recordings of the reference (sb-2 and sb-3
in the family's live check, 2026-09-28) ran all six sysbench runs with exact query counts, no
ignored error and no FATAL line, and `compare` found them identical, 7 of 7.

## Coverage: where the changed line runs

The changed line is inside `if (OB_NOT_NULL(desc) && desc->is_committing())` (line 1797).

- **The 272 configured cases never reach it.** In the coverage profile of the 272 cases
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb), `llvm-cov show` of
  src/storage/tx/ob_tx_api.cpp gives the function 46 calls over both passes (the sweep found a session
  in a transaction 46 times) and lines 1798-1805 0: in two full passes the sweep never met a commit in
  flight. The three files involved are the same at 076eb309b and 834bbee1e
  (`git diff --quiet 076eb309b 834bbee1e -- src/storage/tx/ob_tx_api.cpp src/sql/session/ob_sql_session_mgr.cpp src/sql/session/ob_basic_session_info.cpp`
  exits 0). A sequential test has at most one client committing at a time, a commit is short next to
  the 5-second period, and a session whose statement is still being processed is skipped.
- **The family reaches it dozens of times.** A diagnostic run of the family's sysbench sequence
  against the same coverage-instrumented build (not a recording: the same commands as
  sysbench_parity.py, with the wrappers and seeds of the fixed script, on a scratch instance; outputs in
  /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/11-conc/diag-cov-sb/, driver
  diag_cov_sysbench.py beside it) copied the build's profile after each step; the copies, merged, are in
  diag-cov-sb/profdata/. The cumulative counts of lines 1794 (calls) and 1798 (the committing branch):

  | After | Calls | Committing |
  |---|---|---|
  | prepare, `oltp_point_select` at 1, 16 and 64 threads | 0 | 0 |
  | `oltp_read_write`, 1 thread | 11 | 1 |
  | `oltp_read_write`, 16 threads | 87 | 13 |
  | `oltp_read_write`, 64 threads | 244 | 60 |

  Line 1802 (a real commit timeout) stayed 0 throughout. The instrumented build ran each step at about
  the reference's speed (the 16-thread run took 26.7 s against 18.2 s on the reference), and the sweep
  period does not depend on the build, so the reference build meets committing sessions at a similar
  rate: with about 3 sweeps during the 16-thread run and about 2 committing sessions per sweep, the
  chance that the 16- and 64-thread runs both pass without one is negligible.

## How the mutation stage runs it

As in ../README.md: apply the patch in /Users/colin/seekdb-dev/ref-834bbee1e, rebuild, copy the binary
aside as `$MUT`, then record the family's sysbench piece with it exactly as the reference was recorded
(no other seekdb running on the port; the Docker container `sb` running, `docker start sb` if not):

```
cd /Users/colin/seekdb-dev/migrate-to-rust
git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py
python3 migration/judge/families/concurrency/sysbench_parity.py \
  --seekdb $MUT --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --base-dir $OUT/base --record-dir $OUT/rec --port $PORT
python3 .github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/11-conc/sb-2/rec --right $OUT/rec
```

- sysbench_parity.py and recording_common.py must be the recorded versions (sha256
  d4332c649b6cabe4ad55412c0bfe94f2f421629039941fc8356fad4c30894baa and
  999e5c248b8f118d8d5191e4ec233da13a4c060fb04a921810fcd6d5d0d5dd2b): every case's recording starts
  with both digests, so any other version makes every case differ.
- The run takes about 3 minutes on the reference and stops at the first failed case. The script exits
  1; `compare` notes the different `seekdb_sha256` and fails with the failed case named. Revert the
  patch afterwards.

## Caught by (filled after the run)

Caught by family 11 on 2026-09-28 (README.md in this directory). sysbench_parity.py on the mutated
build exits 1 after 133 s. prepare and the three oltp_point_select runs are recorded as in the
reference. In oltp_read_write_1 (one thread) sysbench printed `FATAL: mysql_drv_query() returned
error 4012 (Statement timeout occurred, ...) for query 'COMMIT'`, so the recording is
`oltp_read_write_1.partial` with the two lines predicted above, and oltp_read_write_16 and _64 were
not run. `compare` against 11-conc/sb-2 exits 1: 4 identical, 3 missing and a recording problem. The
catch came in the one-thread run, the less likely of the two places named above.

The 272 configured cases on the mutated build: only quarantined cases failed. Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/mutations/11-conc-session-sweep-cancels-live-commit/.
