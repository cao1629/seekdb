# The restart scenarios

PLAN.md section 4, item 3 ("Restart script") and family 9 ("Restart after a kill").
`restart_scenarios.py` here runs three scenarios against one seekdb binary and writes a recording
in the runner's recording format, so the C++ reference and the Rust build are compared the same
way as the mysqltest cases: byte for byte, with the runner's `compare` subcommand.

## Command line

```
python3 migration/judge/harness/restart_scenarios.py \
  --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
  --obclient /Users/colin/seekdb-dev/migrate-to-rust/deps/3rd/u01/obclient/bin/obclient \
  --base-dir /Users/colin/seekdb-dev/mysqltest-runs/<name>/base \
  --record-dir /Users/colin/seekdb-dev/mysqltest-runs/<name>/rec \
  --port <a free port> \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql

python3 .github/script/seekdb/mysqltest_for_seekdb.py compare --left <rec A> --right <rec B>
```

| Option | Default | What it does |
|---|---|---|
| `--seekdb FILE` | required | The server binary |
| `--obclient FILE` | required | The client for every statement, the readiness check and init; it also fills the manifest's `mysqltest` fields |
| `--base-dir DIR` | required | New or empty. Reused across the restarts of one scenario; `sdb.py destroy` removes it after each scenario |
| `--record-dir DIR` | required | New or empty; gets `manifest.json` and one `<scenario>.result` (or `.partial`) per scenario |
| `--port N` | required | The server's SQL port; no default, because judge runs share this machine |
| `--init-sql FILE`, `--init-user-sql FILE` | tools/deploy/init.sql, init_user.sql | The init files, as in the runner |
| `--scenario NAME` | all | Repeatable. Scenarios always run in the order restart_data, restart_parameters, restart_mid_dml, whatever order the options give, so two recordings of the same selection have the same case list |
| `--save-instance-dir DIR` | `$SEEKDB_COV_PROFRAW_DIR` | New or empty. Before every destroy, stop the instance and copy `log/` (and `seekdb*.profraw`) to `DIR/<counter>-<scenario>`, exactly as the runner does. The script prints the directory in use and whether it came from the option or the variable, and a refusal names the variable |

Exit code 0 means every requested scenario ran and all its checks held; 1 means anything else.
Argument errors exit 2.

## How a scenario runs

1. **Start:** `sdb.py start --binary --base-dir --port --nodaemon`, `sdb.py wait-ready` (timeout 600 s),
   then init. Init is the runner's own `execute_init_sql`: init.sql in database `oceanbase`, then
   init_user.sql in database `test`, each piped whole to one obclient session. The per-statement
   statuses go to `entry-gate.json` in the work directory, and `init_failed_statements` in the
   manifest counts the failures.
2. **The scenario's SQL**, with kills and restarts on the same base dir, then the check that the
   server is still running (through sdb.py's pid check), so a server that answers the last
   statements and then dies does not pass.
3. **Cleanup:** save `log/` if asked, then `sdb.py destroy`.

**Init runs once per scenario, after the first start, never after a restart.** A restart has to
bring back the user, the database and the data by itself; running `create ... if not exists` again
would hide a loss. restart_data checks the user after every restart by logging in as `admin` with
its password; every statement names database `test` (`-Dtest`). What the full tools/deploy/init.sql
sets only in memory (the `set_tp` tracepoints, session variables) does not survive a kill, so judge
runs pass the reduced init, as in the command line above.

**How the server stops: the script sends SIGKILL itself.** Before each kill it takes the pid through
sdb.py's own check (`inspect_instance_process`: `run/seekdb.pid` names a live process whose
executable is the instance's binary and whose arguments hold this `--base-dir`). It sends SIGKILL to
that pid only, waits up to 20 s for the process to exit (sdb.py's `wait_process_exit`), and then runs
`sdb.py stop`, which finds the process gone, removes the stale `run/seekdb.pid` and exits 0 (the
next `wait-ready` would otherwise read the dead pid and fail). If the pid check fails, the
recording says `seekdb was not running before the kill`; if the process is already gone when
SIGKILL is sent, or does not exit, it says `stop (kill) failed`. Both fail the scenario, so a server
that crashed on its own does not pass as a kill.

Why not `sdb.py stop` alone: it sends SIGTERM, and only the C++ reference turns SIGTERM into
`raise(SIGKILL)` (src/observer/ob_signal_handle.cpp:129-134). A build that shuts down cleanly on
SIGTERM within sdb.py's 20 s would pass every scenario without running crash recovery, and nothing
could tell afterwards: the server is not the script's child, so its exit status is not visible, and
a clean exit removes the pid file itself (src/observer/main.cpp:828). With the kill sent by the
script, every restart runs crash recovery whatever the binary does with SIGTERM (Decision 9 (a)).
For the reference the only change is that its log no longer has the "received signal" line,
because the handler that writes it before `raise` is no longer called. This departs from the
letter of PLAN.md item 3 ("built on `sdb.py start` / `stop`"):
sdb.py still starts, checks and cleans up the instance, but the kill is the script's. How a build
handles SIGTERM is therefore not tested here.

**A restart** is: the kill as above, `sdb.py stop`, `sdb.py start`, `wait-ready`, then a probe query
on the scenario's table, retried every second for up to 600 s, each attempt limited to 30 s. The
probe's attempts are not recorded; it only decides when "ready" is written. It is there because
`wait-ready` checks `select 1`, which does not show that the table is readable again. The obtest
seed waits in a similar way with `check_until_timeout`, but it waits for an expected count and
first checks `__all_server.start_service_time` to see that the server restarted; here the kill is
confirmed directly, and the probe waits only for the query to succeed, so a table whose rows come
back late shows up in the recorded queries after it. If the probe never succeeds, the `.partial`
gets the last attempt's exit code and the last lines of its stderr. How long the restart took is not
recorded (restart time is recorded elsewhere and not gated; PLAN.md section 4, item 9).

**Failures:** a failed start, `wait-ready` or init on a scenario's first start is a run error, as in
the runner: the run stops and the manifest's `error` names it. Everything after that, including a
restart that does not come back, fails only that scenario: its `.partial` shows where it stopped, it
is listed in `failed_cases`, and the next scenario runs. A failed `destroy` is a run error.

## The recording

**manifest.json** has the runner's keys (`write_record_manifest`, mysqltest_for_seekdb.py:921-959;
the keys added at the end, :1110-1119), so `compare` accepts it: `load_recording` needs `cases`
(:1251-1262), `recording_problems` checks `finished`, `max_retries`, `retried_cases`, `error`,
`failed_cases`, `tools_deploy_status` and `tools_deploy_tree` (:1265-1298), the
`RECORDING_INPUT_KEYS` (:39-47) must be equal on both sides and the `RECORDING_NOTE_KEYS` (:48)
are reported as notes, the case lists must be equal (:1527), and `outcomes` gives the exit code of
a missing case (:1321-1325):

| Keys | Value here |
|---|---|
| `finished` | false when the run starts (the file is created with `open("x")`, so a used record dir is refused), true at the end |
| `seekdb`, `seekdb_sha256` | the binary |
| `mysqltest`, `mysqltest_sha256`, `obclient`, `obclient_sha256` | all four name the obclient used |
| `init_sql`, `init_sql_sha256`, `init_user_sql`, `init_user_sql_sha256` | the init files |
| `sdb_sha256` | .github/script/seekdb/sdb.py |
| `runner_sha256` | restart_scenarios.py itself; `mysqltest_runner_sha256` is the runner it borrows init and the manifest helpers from |
| `repo_head`, `tools_deploy_tree`, `tools_deploy_status` | `git rev-parse HEAD`, `HEAD:tools/deploy`, `git status --porcelain -- tools/deploy` |
| `cases` | the scenario names, in the fixed order |
| `slice_index`, `slice_count`, `case_list` | 0, 1, null |
| `max_retries`, `retried_cases` | 0 and `{}`: nothing is ever retried |
| `fresh_instance_per_case` | true: every scenario starts on a destroyed base dir |
| `success`, `failed_cases`, `error`, `init_failed_statements` | as in the runner |
| `outcomes` | per scenario: `exit_code` (0 or 1; null if not run), `recorded`, `partial`, `problems` |
| `work_dir`, `recorder`, `recorded` | the work directory, this script's path, and what the files mean |

**`<scenario>.result`** exists when the scenario ran and every check held; otherwise the same content
is in `<scenario>.partial`. `compare` refuses recordings with failed cases or an error, so a
failing scenario always shows up. The file holds, in order:

- `-- <scenario>`, then `-- recorder sha256 <sha256 of restart_scenarios.py>`, then
  `-- start, ready, init`. For the mysqltest cases the test content is tools/deploy, which `compare`
  checks; here the test content is this script, and `compare` treats `runner_sha256` only as a
  note. The sha256 line makes every scenario differ when two recordings were made with different
  versions of the script, so a changed script means recording the C++ reference again;
- every statement as sent, followed by obclient's stdout exactly as printed. Each step (one
  statement, or one transaction's statements) is one obclient session:
  `obclient -h 127.0.0.1 -P <port> -uroot -A -c -Dtest --table`, statements on stdin. `--table`
  prints the bordered table with column names; DDL and DML print nothing in batch mode; `-c` keeps
  the index hints. The one statement run as `admin` is preceded by a line saying so;
- lifecycle markers, such as `-- restart 1: stop (kill), start, ready`, or on failure
  `-- restart 1: stop (kill), start failed`;
- checks, as `-- check: <what>: yes` or `: no`. A `no` fails the scenario;
- when a statement fails: obclient's stderr (`ERROR ... at line N: ...`) and
  `-- obclient exit code N`. The scenario stops there. Other diagnostics (the last probe attempt,
  a background client's stderr) are written only on a failure path, so they only ever reach a
  `.partial`.

Nothing that changes between two runs of the same binary is written to a `.result`: no times, pids,
ports, paths, probe attempts, error line numbers, or counts that depend on when the kill landed.

**The work directory** is a new temporary directory, printed at the start and named in the
manifest's `work_dir`. It keeps `entry-gate.json` and restart_mid_dml's client script,
acknowledgement file, client stderr, and the open session's stdout and stderr, for diagnosis. It is
not part of the recording.

## Why some transactions are large

A transaction's redo reaches the log before its commit only once its pending redo is larger than
`_private_buffer_size`, 16 KB by default (src/share/parameter/ob_parameter_seed.ipp:651): after each
write, `ObMvccWriteGuard::~ObMvccWriteGuard` (src/storage/memtable/mvcc/ob_mvcc_ctx.cpp:350-374)
calls `ObTxCtx::submit_redo_after_write` (src/storage/tx/ob_tx_ctx.cpp:1218-1223), which submits
only when `pending_log_size_too_large` (src/storage/memtable/ob_memtable_context.cpp:839-852) says
so. For a table with a local index the main-table write skips this and the index write does it
(src/sql/engine/dml/ob_dml_service.h:456, :481). A single-row transaction therefore writes nothing
to the log before its commit, and a kill between its statements tests nothing about uncommitted
data. The scenarios add two transactions of 1600 rows each (a cross join of a 40-row helper table
`t_bulk` with itself, each row with a 100-character string), far above 16 KB, so their redo is in the
log while they are not committed:

- restart_data rolls one back before restart 1 (the log then holds its redo and its rollback);
- restart_mid_dml keeps one open in a second session until the kill (the log holds its redo and no
  end).

Recovery must apply neither. The log is written in order, and both are followed by committed work
that waits for its own log entries, so the redo that was submitted is on disk at the kill.

## restart_data

A table `t_data` with a primary key on `id` and a secondary index `idx_name` on `name`; columns
INT, VARCHAR, DECIMAL(12,3), DATE, DATETIME(6), DOUBLE and VARCHAR. All written values are
literals, never `now()`.

1. Create `t_data` and `t_bulk` (40 rows).
2. Insert 12 rows in three multi-row INSERTs: NULLs in every nullable column, a row that is NULL
   everywhere except the key, an empty string, a quote (`O'Brien`), a backslash, trailing spaces,
   negative and large decimals, a leap day, 1970-01-01 and 2038-01-19, microsecond datetimes, and
   doubles that sum exactly.
3. A transaction that inserts 1600 rows (`id` 1000 to 2599, name `bulk`) into `t_data` from `t_bulk`
   and is rolled back.
4. Update by key, by a key list and through the index; change an indexed value; delete by key and
   through the index; delete a key and insert it again with new values; a committed
   multi-statement transaction; a small rolled-back transaction that inserted a row and deleted
   another. Each step runs in its own session; single statements commit through autocommit.
5. Select set 0. A select set is 9 statements, each with a full ORDER BY, a single-row result or a
   fixed text: `show create table t_data` (so a lost index or a changed column type shows, which
   the index hint alone would hide, because an index hint on a missing index is ignored); all rows
   by key; the named rows through the index (`/*+ index(t_data idx_name) */`) and through a full
   scan (`/*+ full(t_data) */`); counts and sums; a NULL predicate; a DECIMAL range; a LIKE; a key
   list that includes the updated, reinserted and rolled-back keys. Then `show grants;` run as
   `admin` with password `admin`, the login the runner's mysqltest cases use.
6. Restart 1, select set 1; restart 2 with no writes in between, select set 2.
7. Writes after recovery: an insert, an update, a change of an indexed value, a delete, and a
   committed transaction with an insert and a delete through the index. Select set 3.
8. Restart 3, select set 4.

Checks: select set 1 and select set 2 are each byte-identical to select set 0, and select set 4 to
select set 3 (statements and output). Set 0 is taken after the rollback, so rolled-back rows that
come back in recovery make set 1 differ. Set 3 against set 4 catches a recovery that leaves the log
in a state where commits made after it are lost at the next kill. Probe after a restart:
`select count(*) from t_data;`.

## restart_parameters

The two parameters, both cluster-level, dynamic, visible in SHOW PARAMETERS (no leading
underscore; SHOW PARAMETERS hides those while they are at their default), and with effects that
reach only logging:

| Parameter | Seed | Default | Set to | What it changes |
|---|---|---|---|---|
| `max_syslog_file_count` | src/share/parameter/ob_parameter_seed.ipp:172 | 2 | 4 | How many rotated log files are kept (log retention): ob_reload_config.cpp:35, ob_server.cpp:1926. Its checker (src/share/config/ob_config_helper.cpp:267-280) accepts 0 or any value at least `syslog_file_uncompressed_count` (default 0) |
| `trace_log_slow_query_watermark` | src/share/parameter/ob_parameter_seed.ipp:109 | 1s | '5s' | The slow-query threshold for trace logging and plan statistics: obmp_base.cpp:105, obmp_packet_sender.cpp:767, ob_physical_plan.cpp:388, ob_trans_ctx.cpp:159 |

How they persist: `ALTER SYSTEM SET` reaches `ObAdminSetConfig::update_sys_config_`
(src/rootserver/ob_system_admin_util.cpp:122), which writes the value to the config store in the
meta database (`ObConfigManager::save_config`, src/share/config/ob_config_manager.cpp:128) and
reloads it synchronously (`got_version`, :114). On start the server reads the store again before
anything else (src/observer/ob_server.cpp:1715). So the value is visible right after the statement
and must come back after a kill.

Steps: SHOW PARAMETERS LIKE for each (the defaults); ALTER SYSTEM SET both; SHOW again; restart 1;
SHOW again. SHOW PARAMETERS always returns svr_type, name, data_type, value, info, section, scope,
source, edit_level, default_value and isdefault (src/sql/resolver/cmd/ob_show_resolver.cpp:2544-2548)
and cannot select columns, so the script cuts the name and value columns out of obclient's table at
the column borders and records only those, with a line saying so.

Checks, for each parameter: SHOW returns exactly one row each time; the value after the SET differs
from the default; the value after restart 1 equals the value after the SET. The checks compare
what SHOW printed, not a hard-coded spelling, and the printed values themselves are compared
between the two builds.

## restart_mid_dml

A table `t_mid (seq, payload, amount, noted)` with no primary key on `seq`, so a duplicated row
would be visible. Row `seq` always holds `payload = 'row-<seq, 6 digits>'`,
`amount = seq * 1.25` and `noted = 2026-02-<seq mod 28 + 1>`. Also `t_bulk` (40 rows) and
`t_open (n, note)` with a primary key.

1. **The open session.** A background obclient session (`-N -s --unbuffered --disable-reconnect`)
   gets `begin; insert into t_open ... from t_bulk a, t_bulk b; select 'open';` on a pipe that the
   script keeps open, so the session stays connected with its 1600-row transaction open. The script
   waits for `open` on the session's stdout (the insert has returned); if it does not come within
   120 s, the scenario fails with the session's stderr in the `.partial`.
2. **The client.** A second background session with the same options (no `--force`) runs 5000
   numbered transactions from a script file: `begin; insert; commit; select <seq>;`. The selected
   number is printed only after the commit returned, and `--unbuffered` flushes it at once, so every
   line in the acknowledgement file is a committed transaction. Without `--disable-reconnect`, or
   with `--force`, the client could carry on after the kill and leave a gap.
3. **The kill.** When the acknowledgement file holds 200 lines, the script takes the pid, notes
   whether both sessions are still running, and sends SIGKILL (as above). After `sdb.py stop` it
   kills the open session and waits for the client to exit, so nothing writes during recovery (if
   the client has not exited within 120 s, it is killed and the scenario fails).
4. Restart; probe `select count(*) from t_mid;`.
5. Read every row of `t_mid` back with one query, and count the rows of `t_open`. Neither output is
   recorded (the first depends on when the kill landed); the script computes the checks from them.

The task's example was 200 of 1000; 5000 leaves room for the time between seeing the 200th
acknowledgement and the kill (one `ps` inside sdb.py's pid check), during which a fast client
commits more.

Checks (all yes or no; neither the acknowledgement count A, the row count M, the client's exit code
nor its error text is recorded):

- the open session was still running when the server was killed;
- the client was still running when the server was killed (noted right before SIGKILL);
- the client exited with a non-zero status;
- the client stopped on no error other than one lost connection: its stderr has at most one `ERROR`
  line, and that line is 2006 (server gone away) or 2013 (lost connection during query), whichever
  the kill caused. A client that stopped on an SQL error before the kill (for example `ERROR 1062`)
  gets `no`. No `ERROR` line at all is accepted, because a client that writes into a socket the
  server has already reset can die of SIGPIPE without printing; the non-zero status still holds
  then. On `no`, the last lines of the client's stderr go into the `.partial`;
- the acknowledgements are 1..A in order, with A at least 200 (a last line without its newline
  counts as `no`);
- the client stopped before its last transaction (A below 5000);
- every read-back row has four columns and a numeric `seq` (on `no` the scenario stops, so no later
  check is written without being evaluated);
- every acknowledged transaction's row is present;
- no row is duplicated;
- the rows form a gap-free prefix 1..M of the numbering;
- at most one row beyond the acknowledged ones is present, so M is A or A + 1: one client commits in
  order, and only the transaction in flight at the kill may have committed without its
  acknowledgement reaching the client. This also catches acknowledgements lost in the harness,
  which would make "every acknowledged row is present" cover fewer rows than it claims;
- the other columns of every present row are the ones written for its `seq`;
- no row of the transaction left open at the kill is present (`count(*)` of `t_open` is 0).

## What is not covered yet

- **Data already written to sstables.** The data here is small and nothing is frozen, so recovery
  replays the log into memtables only; recovery from a checkpoint plus the log after it is not
  exercised. That fits the Rust narrow path, which keeps data in memtables only (reduced-init
  README). A later variant runs `alter system minor freeze`, waits for the freeze to finish, writes
  more, and then kills, once the Rust build has sstables.
- **How a build handles SIGTERM**, since the script kills with SIGKILL, and the clean shutdown that
  comes after parity (Decision 9).

## What has been checked

- `python3 -m py_compile` and `--help`.
- In-process tests with `subprocess.run` and `subprocess.Popen` replaced by a fake server and fake
  clients, and the script's pid lookup and kill replaced by fakes that assert the kill goes to the
  validated pid (kept outside the repo). Two clean runs are accepted by the runner's `compare`
  (exit 0, 3 identical, 0 recording problems); each result starts with the recorder sha256; every
  restart sends one SIGKILL and then runs `sdb.py stop`; probe attempts use the 30 s limit. Each of
  these gives `no` or a failed step, a `.partial`, `failed_cases`, exit 1, and a refused `compare`:
  a SELECT set that changes after restart 1; one that changes only after restart 3; a changed
  `show create table`; a missing acknowledged row; a client that finished before the kill; a client
  that stopped on `ERROR 1062`; 40 more rows than acknowledgements; a last acknowledgement without
  its newline; rows of the open transaction after the restart; an open session whose insert failed;
  a parameter lost in the restart; a server already gone at the kill; a server dead at the end of
  the scenario; a probe that never succeeds (its last error is in the `.partial`). A failed restart
  fails only its scenario; a failed first start is a run error that stops the run;
  `--save-instance-dir` gets `log/` before each destroy; a used save directory from the environment
  is refused with a message naming the variable.
- The real pid lookup and kill against stand-in processes: the lookup accepts a process that
  matches the marker's binary and `--base-dir` and refuses one that does not; the kill sends SIGKILL
  and waits for the exit, and reports a pid that is already gone; after the kill, the real
  `sdb.py stop` exits 0 and removes the stale pid file.
- **Not yet run against a server** (judge runs were in progress on this machine). The first real
  run must confirm: that obclient accepts `--unbuffered` and `--disable-reconnect` (both appear in
  its help text) and runs the open session's statements as they arrive on the pipe; that the
  client's error at the kill is 2006 or 2013; the column names SHOW PARAMETERS prints; that
  `show create table` and `show grants` print the same before and after a restart; that the probe
  waits long enough; and that two runs on the C++ reference give byte-identical recordings. Then a
  caught mutation (00b's second sign-off) shows the scenarios can fail.
