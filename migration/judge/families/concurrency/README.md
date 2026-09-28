# Family 11: concurrency

PLAN.md section 4, family 11: the multi-connection and send/reap mysqltest cases, the cases run as 4
slices at the same time on separate ports, sysbench `oltp_point_select` and `oltp_read_write` at 1, 16
and 64 threads, and, in place of the dropped `run_two_concurrent_clients.sh`, two obclient sessions that
connect while the server is still starting. For every `oltp_point_select` and `oltp_read_write` run the
recording holds sysbench's report (query counts, ignored errors, reconnects) and its ignored errors by
code, compared exactly. The per-table checksums are compared after prepare, after each
`oltp_point_select` run and after the one-thread `oltp_read_write` run; after the 16- and 64-thread
`oltp_read_write` runs, whose table contents depend on how the threads interleave, the values that hold
under any interleaving are compared instead.

Every script here writes a recording in the runner's format (`DIR/manifest.json` and one
`DIR/<case>.result`, or `.partial` when the case failed), so two builds are compared the same way as
the mysqltest cases, byte for byte:

```
python3 .github/script/seekdb/mysqltest_for_seekdb.py compare --left <recording A> --right <recording B> --out <report.json>
```

| File | What it is |
|---|---|
| ../../lists/multi-connection.txt, ../../lists/send-reap.txt | The case lists (item 1 below), each starting with the script that produced it |
| parallel_slices.py | Runs the runner in record mode as 4 slices at once and merges the 4 recordings into one |
| sysbench_parity.py | sysbench 1.0.20 from the Docker container `sb` against one build running natively; records what two runs of the same build reproduce |
| startup_connect.py | Two obclient sessions keep trying a fixed query while the server starts, and again after each kill |
| recording_common.py | Shared: the check that a port is free (all three scripts); for the last two, start and kill through sdb.py, obclient steps, the recording and its manifest |
| offline_test.py | The offline tests: every client, server, sdb.py and docker call is replaced by a stand-in |

## Ports, and why each script checks its port first

The commands below use ports 3891-3894 (parallel_slices.py), 3895 (startup_connect.py) and 3896
(sysbench_parity.py); 3881 and 3882 are the judge's own. This family's range is 3891-3896. The memory and
wire families' live checks use port 3891 as well (families/memory/README.md:358,
families/wire/README.md:257), so until each family has its own range, none of their live checks may run
while this family's does. Run this family's three scripts one at a time too: the four slices start four
servers, and sysbench loads the machine. All commands run from the worktree root,
/Users/colin/seekdb-dev/migrate-to-rust, and, like every judge run, only while
`git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py` succeeds and
`git status --porcelain -- tools/deploy` prints nothing.

A server already listening on the port would be taken for the new one. `sdb.py wait-ready` reports ready
as soon as `select 1` succeeds on the port while its own seekdb pid is alive (sdb.py:180-240), and the
C++ seekdb writes its pid file (src/observer/main.cpp:742-743) seconds before it opens its SQL port at
the end of `ObServer::start` (src/observer/ob_server.cpp:1291). A slice's init SQL and cases, or
sysbench, would then run against the other server and change its state, while the new seekdb dies on the
bind. So before a server starts, each script opens a TCP connection to 127.0.0.1:<port>. Only a refusal
counts as free; a port that accepts the connection, or a connection that fails any other way (a timeout,
a reset), stops the run:

- parallel_slices.py checks all its ports before any runner starts (exit 1, nothing created) and, with
  `--serial`, each slice's port again right before that slice starts. A slice whose port is taken by
  then is not started, nor are the later ones; parallel.json says why, and the merge is refused.
- sysbench_parity.py checks right before `sdb.py start`. A port that answers is a run error in the
  manifest, and no case runs.
- startup_connect.py checks before every round (in a restart round, after the kill), besides its own
  check that both sessions' first attempts fail.

What the check cannot see: the runner starts a new instance on the same port after a failed case, and
before every case with `--fresh-instance-per-case` (mysqltest_for_seekdb.py:1048-1084), and nothing
outside the runner can check the port at those moments. Keeping the families' port ranges apart covers
that.

## Which cases open more than one connection

| List | Cases | What puts a case on it |
|---|---|---|
| lists/multi-connection.txt | 119 of the 272 configured cases | mysqltest runs a `connect` command for it, in the .test or in a file the .test sources. The default connection stays open, so each `connect` adds one. No configured case opens a client any other way (no `exec` or `system` that runs one) |
| lists/send-reap.txt | 6 | a `send`, `send_eval` or `reap` command: a statement is sent without waiting and its result is read later, so two sessions run statements at the same time. All 6 are also on multi-connection.txt |

**119, not 69.** The plan's 69 and 6 come from the survey's scan ("'^(--)?connect(' gives 69 and
'^(--)?(send|reap)' gives 6", feasibility/raw/report-workflow-result.json), which reads only the .test
files. That scan gives 69 and 6 over the 272 configured cases and 74 and 7 over the 283 tracked ones,
so the plan's figures count configured cases, not tracked ones as PLAN.md section 4 says of its case
counts. Following the files a case sources adds 50 configured cases whose extra connection is opened in
an include: 38 geometry cases through test_suite/geometry/t/import_default_srs_data_mysql.inc, 8 through
include/index_quick_major.inc, 2 through include/wait_daily_merge.inc and wait_minor_merge.inc, and 2
trx cases through test_suite/trx/include/serializable_or_rr_trans_basic.inc and
serializable_trans_init.inc. Over the tracked cases the same rule gives 127 and 7. Sourced files are
read whatever the `if` or `while` around the `source` line says; the one sourced file that does not
exist, include/show_rpl_debug_info.inc, is named only on wait_condition.inc's failure path. Each list's
header has the details.

17 of the 119 are in lists/plain-sql.txt, the only list recorded under the reduced init so far; none of
the 6 send/reap cases is. Two of the 119 are on the quarantine list (histogram.stats_farm and
subquery.idx_with_const_expr_21_subquery_dilang), so they are compared only while two C++ recordings
agree on them.

Each list carries its script in `#|` lines, and its output is every line after the script. These
checks print nothing while the lists still match the tree (bash or zsh):

```
F=migration/judge/lists/multi-connection.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | python3 -B -) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
F=migration/judge/lists/send-reap.txt
diff <(sed -n 's/^#| \{0,1\}//p' "$F" | python3 -B -) <(sed -n '/^#|/,$p' "$F" | grep -v '^#|')
```

Both lists are in configured order, one case per line, for the runner's `--case-list` (it skips `#`
lines), either as one slice or through parallel_slices.py.

## Running the cases as four slices at once (parallel_slices.py)

```
python3 migration/judge/families/concurrency/parallel_slices.py run \
  --seekdb <seekdb> --obclient <obclient> --mysqltest <mysqltest> \
  --out-dir <new or empty dir> --init full|reduced \
  [--case-list FILE] [--ports 3891,3892,3893,3894] [--fresh-instance-per-case] [--serial] [--save-instance-dir DIR]

python3 migration/judge/families/concurrency/parallel_slices.py merge --out <new or empty dir> <slice record dir>...
```

`run` first checks that every port refuses a TCP connection (see the ports section), then starts the
runner four times at once, slice `i` with
`--slice-index i --slice-count 4 --port <port i> --max-retries 0 --no-ignore-trailing-whitespace`, its own
`--base-dir`, `--work-dir` and `--record-dir` under `<out-dir>/slice_<i>/`, and the init files `--init`
names: `full` is tools/deploy/init.sql and init_user.sql, `reduced` is migration/judge/reduced-init/. The
number of ports is the slice count. `--case-list` and `--fresh-instance-per-case` are passed on;
`--serial` runs the same slices one after another, checking each slice's port again before it starts.
Each runner's output goes to `<out-dir>/slice_<i>/runner.log`. If a save directory is given (or
`$SEEKDB_COV_PROFRAW_DIR` is set), slice `i` gets `<dir>/slice_<i>` and the variable is removed from the
runners' environment, because four runners writing numbered folders into one directory would collide.
When the runners have exited, the four recordings are merged into `<out-dir>/merged/`, and
`<out-dir>/parallel.json` records each slice's command, port, exit code, time and, for a slice that was
not started, why; none of that is part of the recording. Exit 0 means every slice exited 0 and the
merged recording reports success. `merge` does the same merge on existing slice recordings (exit 0
success, 1 merged with problems, 2 refused).

### How the merge keeps compare's checks honest

`compare` checks each recording's manifest (`finished`, `max_retries`, `retried_cases`, `error`,
`failed_cases`, `tools_deploy_status`, `tools_deploy_tree`; mysqltest_for_seekdb.py:1265-1298), requires
the `RECORDING_INPUT_KEYS` (:39-47) to be equal on both sides and the case lists to be equal in order
(:1527), and reports the `RECORDING_NOTE_KEYS` (:48) as notes. The merge keeps each of these meaning what
it means for a one-slice recording:

- **Values that can differ per slice are carried as the worst of the four.** `finished` is true only if
  all four finished; `error` joins every slice's error (and says which slice did not finish);
  `failed_cases` and `retried_cases` are the union; `max_retries` is the common value, or the largest
  when they differ; `success` holds only if all four succeeded. A problem in any slice fails the
  same check in the merged recording.
- **Values that must be the same are required to be the same.** The input keys, `seekdb_sha256`,
  `runner_sha256`, the binary and init paths, `case_list`, `tools_deploy_status`,
  `fresh_instance_per_case` and `slice_count` must be identical in all four manifests, or the merge
  refuses. The merged manifest carries each input and note key the slices have (the runner's
  `RECORDING_INPUT_KEYS` and `RECORDING_NOTE_KEYS`, read from the runner itself), so compare reads the
  slices' values and not its defaults. So a merged recording always stands for one build, one set of
  inputs, one runner and one tools/deploy, and compare's input check between the merged recording and
  another means what it means for two one-slice recordings.
- **`repo_head` is kept per slice.** compare only reports it as a note, and other work commits to this
  branch every few minutes, so the slices of a `--serial` run (about 10 minutes for the 119 cases) can
  start at different commits. The merge does not require it to match: each slice's `repo_head` is in
  `merged_slices`, and the merged `repo_head` is the common one, or null when they differ. Whatever a
  commit could change in a recording still has to match through its own key: the runner
  (`runner_sha256`), sdb.py (`sdb_sha256`), tools/deploy (`tools_deploy_tree`, `tools_deploy_status`), the
  init files (their sha256), and the case list (the check below).
- **The case list is the one a one-slice run records.** The runner gives slice `i` the cases
  `cases[i::4]` of its selection (mysqltest_for_seekdb.py:1026). The merge puts slice `i`'s cases back at
  positions `i, i+4, ...`, checks that each slice holds exactly as many cases as that needs, and checks
  the result against the runner's own selection, computed again with its `discover_cases` and
  `load_case_list` (:615) for the slices' case list. Any difference refuses the merge.
- **Every file is the slice's own.** The merge refuses a slice directory with a file its manifest does
  not account for, or with a `.result` or `.partial` that disagrees with the case's outcome
  (`recorded`, `partial`), and copies each file byte for byte, comparing it after the copy. `outcomes`
  is the union, so compare still finds each missing case's exit code.
- **What it adds is marked.** `slice_index` is null, `slice_count` is 4, `merged_slices` names each
  slice's record directory, the sha256 of its manifest and its `repo_head`, and `merger_sha256` is this
  script's. compare reads none of these, so the merged recording and a one-slice recording are
  comparable.

### When a merged recording may differ from a one-slice recording

- **Which earlier cases shared the server.** Without `--fresh-instance-per-case` the cases of a slice
  run one after another on one instance, so a case sees what earlier cases of its slice left behind,
  and the four slices group the cases differently from one slice. Only ser-1 against seq-1 in the live
  check below (the serial merged recording against the one-slice recording, both on this Mac with
  retries off) shows whether that grouping changes the output. CI's four slices do not show it: CI calls the
  runner with its defaults (.github/script/seekdb/mysqltest_slice.sh:24-33, run without arguments at
  .github/workflows/seekdb.yml:305), which at 834bbee1e retry a failed case up to 3 times, each on a
  fresh instance (mysqltest_for_seekdb.py:502-510 at 834bbee1e), and ignore trailing whitespace
  (:380-389), so a case that depends on its slice neighbours fails once and passes on the retry; CI also
  runs in a Linux container (.github/workflows/seekdb.yml:245-251).
  `--serial` runs the same four slices one after another: a merged parallel recording against a merged
  serial one differs only by the load of four servers at once; a merged serial recording against a
  one-slice one differs only by the grouping. `--fresh-instance-per-case` removes the grouping question
  at the cost of a server start per case; compare treats it as an input, so both sides need the same
  setting.
- **Four servers of default size.** The runner starts each instance without `--parameter`, so each
  sizes its caches and buffers from `memory_budget`'s default: 80% of physical memory, at most physical
  memory less 1G (src/share/config/ob_server_config.cpp:190-203, :234-249), which is about 19.2G of this
  Mac's 24G for each of the four. The live check must confirm they fit (look for
  OB_RESOURCE_UNIT_VALUE_INVALID or -4013 in the slices' logs). This cannot be changed from here without
  changing the runner. The four do not clash on the RPC port: all keep the default `rpc_port` 2882, but
  no RPC listener opens, because `enable_rpc_service` defaults to False
  (src/share/parameter/ob_parameter_seed.ipp:46), the gRPC listener starts only when it is on
  (src/standby/standby_module.cpp:504-511), and `rpc_port` otherwise only sets the server's own address
  (src/observer/ob_server.cpp:1861).
- **Timing-sensitive cases** can behave differently under the load of four servers; that is what the
  parallel run is for, and a C++-against-C++ comparison finds them.

## sysbench against one build (sysbench_parity.py)

```
python3 migration/judge/families/concurrency/sysbench_parity.py \
  --seekdb <seekdb> --obclient <obclient> --base-dir <new or empty dir> --record-dir <new or empty dir> --port 3896 \
  [--container sb] [--threads 1,16,64] [--point-select-events 200000] [--read-write-events 30000] \
  [--rand-seed 1] [--tables 16] [--table-size 100000] [--mysql-host host.docker.internal] [--save-instance-dir DIR]
```

The server runs natively through `sdb.py start --nodaemon` with perf_run.sh's parameters
(perf_run.sh:34: `memory_limit=8G`, `cpu_count=8`, `system_memory=1G`, `datafile_size=2G`,
`datafile_maxsize=4G`, `log_disk_size=2G`; sdb.py:129-130 passes `--parameter`), and no init file is run
(perf_run.sh runs none either; the manifest's init keys are null). Two of these parameters do nothing at
834bbee1e. `memory_limit` is a "deprecated compatibility parameter" that seekdb accepts and stores but
ignores for memory sizing and memory control (src/share/parameter/ob_parameter_seed.ipp:81-85), and
`system_memory` is not a parameter at all, which seekdb skips without an error
(src/share/config/ob_common_config.cpp:193-196). Memory is sized by `memory_budget`
(ob_parameter_seed.ipp:75-80, read at ob_server_config.cpp:237), whose default gives this server, like
perf_run.sh's, about 19.2G of the Mac's 24G. That does not make two runs on this Mac differ, but
performance-protocol.md presents memory as fixed at 8G. The list is kept identical to perf_run.sh's so
that these runs use the baseline's server; when perf_run.sh and performance-protocol.md move to
`memory_budget=8G` without `system_memory` (the orchestrator's decision), `SERVER_PARAMETERS` in
sysbench_parity.py changes with them, and the C++ reference is recorded again.

sysbench runs in the container with perf_run.sh's options (perf_run.sh:46: 16 tables of 100,000 rows,
`--rand-type=uniform`, user root, database sbtest) and `--db-ps-mode=disable` for `oltp_read_write` runs
(perf_run.sh:51; performance-protocol.md explains the error 5930 it avoids). It differs from perf_run.sh
where the measurement would make the result depend on timing:

| perf_run.sh | Here | Why |
|---|---|---|
| `prepare` with `--threads=8` | `--threads=1` | With more than one thread, each prepare thread seeds its generator from libc `random()` at its own start (sb_lua.c:1517 in `cmd_worker_thread`, sb_rand.c:178-185), so which table gets which data depends on which thread starts first. With one thread, prepare runs in the main thread (sb_lua.c:1536-1550), whose generator was seeded at start-up from libc's unseeded, fixed sequence (sb_rand.c:154-158). `--rand-seed` does not reach prepare: sysbench calls `srandom(seed)` only in `print_run_mode` (sysbench.c:657-669), which only `run` calls (sysbench.c:1063), while prepare is a Lua command (sysbench.c:1487-1490) |
| `--time=15` warm-up, then `--time=60` | no warm-up; `--events=N --time=0` | A time limit makes the amount of work depend on speed. With `--events` every run finishes exactly N events: the event counter is taken atomically (sysbench.c:696-703) and each finished event is counted once (sysbench.c:756; the report's transactions are these events, db_driver.c:1042-1043). `--time` defaults to 10 (sysbench.c:99), so it is set to 0 |
| time-seeded | a fixed seed per run: `--rand-seed=1` for the first run, each later run 1 more (1 to 6 by default) | With a fixed seed and one thread, the single worker's generator is fixed (sysbench.c:805), so the statement sequence is fixed. The seeds must differ between runs: each worker seeds itself from libc `random()` after `srandom(seed)` (sb_rand.c:178-185), so with one seed for every run the first worker of each run draws the same stream as the first worker of the run before, on the same tables, and its `UPDATE ... SET c=?` writes the value that row already holds, which the server reports as 0 rows changed (the live check, below) |
| `--report-interval=10` | none | The report thread seeds its own generator from the same `random()` (sysbench.c:938), racing the worker for its values |
| the stock script, LuaJIT compiler on | a wrapper that turns the compiler off, then runs the stock script | With the compiler on, the one-thread prepare creates sbtest2 twice and stops (below) |
| default ignore list | `--mysql-ignore-errors=1213,1020,1205` (the default, drv_mysql.c:74-75, spelled out) and `--verbosity=5` | sysbench writes one `DEBUG: Ignoring error <code>` line per ignored error only at debug verbosity (drv_mysql.c:740); that gives the counts by code. Any other error is fatal (drv_mysql.c:766-774) |

Before anything runs, the script checks that the container runs `sysbench 1.0.20` (the version this
analysis read, from the 1.0.20 tag), writes the two Lua wrappers below into the container, and takes
the sha256 of /usr/bin/sysbench, the three Lua scripts, the two wrappers and every library `ldd`
lists; a wrapper whose sha256 is not that of the text the script wrote stops the run before the server
starts. The digest of that list is in every case's header, so a changed client changes every
recording.

**Every sysbench command runs with the LuaJIT compiler off.** The container's sysbench uses the
system LuaJIT, 2.1.0-beta3 on arm64 (Ubuntu's `libluajit-5.1-2` 2.1.0~beta3+dfsg-6ubuntu0.1). With its
compiler on, the one-thread prepare loses its place in the loop over the tables: after sbtest12 or
sbtest13 it creates sbtest2 again and stops with `FATAL: ... error 1050 (Table 'sbtest2' already
exists)`, three times out of three (the live check, below). perf_run.sh never showed it, because its
prepare runs 8 threads and each thread's loop covers only 2 tables. So each command runs
`/tmp/seekdb-judge-sysbench/<workload>.lua` in place of the stock script, a wrapper of three lines:
`jit.off()`, an `assert` that `jit.status()` reports the compiler off, and `dofile` of
/usr/share/sysbench/<workload>.lua. Each Lua state sysbench creates (the main one and one per worker
thread) loads the wrapper, so the stock script runs interpreted everywhere: the wrapper changes how
the Lua code runs, not what it does. With the compiler off the prepare creates all 16 tables in
order. The recorded command lines name the wrapper, the case header says
what it does, and the manifest keeps both wrappers' text under `sysbench.lua_wrappers`.

### What each case records

Cases, in this order on one server: `prepare`, `oltp_point_select_<t>` for each thread count, then
`oltp_read_write_<t>` (thread counts ascending, so the one-thread `oltp_read_write` run starts from the
data prepare left). N is the run's `--events`.

| Case | Recorded exactly | Checks (yes/no) |
|---|---|---|
| prepare | `create database sbtest`, the sysbench command, the per-table values below, the per-table checksum | sysbench exited 0 with no FATAL line; it created the 16 tables and their `k_<n>` indexes; the values below; the hinted statement's plan for sbtest1 names k_1 (the plan text itself is not recorded, see below); the index agrees with the table |
| oltp_point_select_1, _16, _64 | the command; sysbench's report line (transactions, read/write/other/total queries, ignored errors, reconnects) and its ignored errors by code; the per-table checksum | exit 0, no FATAL; exactly N events and N transactions; the query counts are N times one event's statements, with no ignored error and no reconnect; the checksums are the prepare case's (point selects change nothing); the index agrees with the table |
| oltp_read_write_1 | the command; the report line and the ignored errors by code; the per-table values; the per-table checksum | as above, with 14 reads, 4 writes and 2 others per event (oltp_read_write.lua:46-63; the baseline logs under /Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e*/ show the same split, and 1 read per `oltp_point_select` event); the values below |
| oltp_read_write_16, _64 | the command; the report line and the ignored errors by code; the per-table values | the same checks as oltp_read_write_1 (exit 0, no FATAL; exactly N events and N transactions; 14N reads, 4N writes and 2N others with no ignored error and no reconnect; the values below; the index agrees with the table), without the checksum |

- **The per-table checksum:** `select count(*), sum(crc32(concat_ws('#', id, k, c, pad))),
  bit_xor(crc32(concat_ws('#', id, k, c, pad))) from sbtest<n>`. seekdb has no `CHECKSUM TABLE`: in its
  MySQL grammar `CHECKSUM` is only a table option that is parsed and dropped
  (src/sql/parser/sql_parser_mysql_mode.y:6480-6487). The sum and the xor do not depend on the order
  the rows are read in; the sum also counts a duplicated row, which the xor would cancel; the `'#'`
  separator cannot occur in the data (ids and k are integers, c and pad are digits and dashes), so no
  two different rows join to the same string. `CRC32()` itself is checked in family 5.
- **The per-table values that hold under any interleaving:** `count(*)`, `count(distinct id)`,
  `min(id)`, `max(id)`, the number of c and pad values that do not have the shape of sysbench's
  templates (`c not regexp '^[0-9]{11}(-[0-9]{11}){9} *$'`, pad with 5 groups), and `min(k) >= 1`. They
  hold because every write keeps them: each event deletes one id and inserts the same id again
  (oltp_common.lua:477-488), so the ids stay 1 to 100,000 (prepare's auto-increment ids; prepare checks
  this premise); every c and pad value comes from the two templates (oltp_common.lua:133-140, :466,
  :484-485); k starts in 1..table_size (:222), and is only ever increased (:261) or set to a new id
  (:478, :483).
- **The index agreeing with the table:** per table, `count(*)` and `sum(crc32(concat_ws('#', id, k)))`
  over `k > 0`, read once with `/*+ index(sbtest<n> k_<n>) */` and once with `/*+ full(sbtest<n>) */`,
  must be equal. Only yes or no is recorded. It holds under any interleaving and tests concurrent index
  maintenance directly.
- **The plan of the hinted statement:** only whether it names k_1 is recorded (a yes/no check whose line
  gives the reason), not the plan text. The text holds EST.ROWS and EST.TIME, which the optimizer derives
  partly from the storage layer's row estimates (src/sql/optimizer/ob_access_path_estimation.cpp:304-336),
  and the storage layer estimates rows held in a memtable and rows in an SSTable in different ways
  (src/storage/access/ob_table_estimator.cpp:110-117). When a background freeze moves the rows sysbench
  just loaded from the memtable into an SSTable depends on timing, so two runs of the same binary are
  not known to print the same estimates, and masking them here would be a new mask, which Decision 6
  does not allow. The index the hint forces does not depend on the estimates.

### What the 16- and 64-thread oltp_read_write runs compare, and what they leave out

**Compared exactly: the report line and the ignored errors by code**, with the one-thread run's check:
14N reads, 4N writes and 2N others, no ignored error and no reconnect. These numbers stay fixed as long
as no event is run again and every write changes a row. A write changes its row unless it stores the
value the row already holds, which can only come from a random stream an earlier run used on the same
tables; the per-run seeds (the table above) rule that out. sysbench runs a failed event again from its
start (the internal sysbench.lua `thread_run`, lines 28-47) and counts every attempt's statements, and it
counts a statement that changes no row as other, not write (drv_mysql.c:917-924). Both can happen only
when transactions meet on a row lock, which depends on timing, and neither happened on the C++
reference: the 10 baseline `oltp_read_write` runs at 16 and 64 threads
(/Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e-rw/run-r{1..5}-oltp_read_write-{16,64}.log,
116,090 to 160,265 transactions each, 1,356,618 in all) reported 0 ignored errors, 0 reconnects and
exactly 14, 4 and 2 queries per transaction, and a parity run has 30,000 events per thread count.
Recording them catches two kinds of broken build that the invariants cannot see: one that answers a
row-lock conflict with 1213 or 1205, which sysbench ignores and retries without a word (seekdb maps
OB_DEAD_LOCK to 1213 and OB_ERR_EXCLUSIVE_LOCK_CONFLICT to 1205, src/share/ob_errno.def:153 and :1378),
and one whose UPDATE or DELETE after a lock wait changes no row (a lost `k=k+1` still leaves k >= 1). If
two C++ runs ever disagree here, the live check reports it; the comparison is not dropped in advance.

**Left out: the table contents.** Which thread runs which event with which random values depends on
timing, and so do the final k, c and pad values. The rule in their place is the per-table values that
hold under any interleaving and the index agreeing with the table (above). If an ignorable error ever
happens, the case fails its query-count check, and the per-table values can fail too, for a reason both
builds share: sysbench restarts the event without a rollback (oltp_common.lua:494-503 only re-prepares
statements after a lost connection), and the next `BEGIN` commits whatever part of the event seekdb
kept.

For every case the manifest keeps sysbench's parsed report, the ignored errors by code, the FATAL lines
and codes and the path of the full sysbench log at `outcomes.<case>.details.sysbench`. The counts by code
of two recordings can be printed side by side with:

```
python3 -c 'import json,sys; [print(d, c, json.load(open(d+"/manifest.json"))["outcomes"][c]["details"]["sysbench"]["ignored_errors_by_code"]) for d in sys.argv[1:] for c in ("oltp_read_write_16","oltp_read_write_64")]' <recording A> <recording B>
```

## Two sessions while the server starts (startup_connect.py)

```
python3 migration/judge/families/concurrency/startup_connect.py \
  --seekdb <seekdb> --obclient <obclient> --base-dir <new or empty dir> --record-dir <new or empty dir> --port 3895 \
  [--restarts 2] [--save-instance-dir DIR]
```

Cases: `first_start` on the new base directory, then `restart_1` ... `restart_<n>` on the same one. In
each, two sessions start a loop before the server does: each attempt is a new
`obclient -h 127.0.0.1 -P <port> -uroot -A -c --table` connection running
`select schema_name from information_schema.schemata order by schema_name;`, 0.05 s apart, until one
succeeds (a 600 s limit). Before the sessions start, the port must refuse a TCP connection (see the ports
section), and the server is started only after both sessions have failed once, so the first phase,
nothing listening, is always seen; if a first attempt succeeds, something else answers on the port and
the run stops. For each session the recording holds the distinct error codes seen before success, each
as `<code> (<SQLSTATE>)` and sorted by code (not how many times each came, and not their order, which is
timing), and the query's output after success; then a check that both sessions got the same output.
`first_start` then creates the database `startup_probe`, and each restart checks that the first answers
already list it. A restart is a SIGKILL to the pid sdb.py's own check finds, then `sdb.py stop` (which
removes the stale pid file), as in the restart script; the run ends with the same kill (Decision 9) and
`sdb.py destroy`. Attempt counts and times go only to `outcomes.<case>.details`.

What a session should see on the C++ reference: seekdb starts its SQL listener as one of the last steps
of `ObServer::start`, after bootstrap, the configuration reload, "server runtime is ready" and "server
metadata is ready" (src/observer/ob_server.cpp:1291), and sets `SS_SERVING` right after (:1313); a local
root login is not refused while the status is still starting (src/observer/mysql/obmp_connect.cpp:624).
So a session should see only the client's code for nothing listening and then the full answer, in
every round. The archived obclient gives 2002 for that, not 2003: `ERROR 2002 (HY000): Can't connect
to OceanBase server on '127.0.0.1' (36)`, and the live check recorded `2002 (HY000)` for both sessions
in every round (below). A build
that opens its port earlier and answers with an error until it is ready shows that error code, and a
build that answers before recovery has finished shows a different result; both show up in compare.
Whether a short phase is always hit depends on the 0.05 s pace, which is why the codes of two C++ runs
must be compared before this family gates anything.

## Comparing two builds

For each script: record the C++ reference twice and compare the two recordings (they must be
identical); then record the Rust build the same way and compare it with a C++ recording. For
parallel_slices.py the merged recording can also be compared with a one-slice recording of the same
case list, init and `--fresh-instance-per-case` setting. Every recording carries the sha256 of the
script that made it (the first lines of each case for sysbench_parity.py and startup_connect.py, with
recording_common.py's; `runner_sha256` and `merger_sha256` in the manifest), so an edited script means
recording the C++ reference again.

## What was checked offline (2026-09-25)

No server, client, sdb.py, mysqltest or sysbench was started, and nothing connected to a server.

- `python3 -m py_compile` of all five files, and `--help` of the three scripts and of
  `parallel_slices.py run` and `merge`.
- The two lists: their `#|` scripts reproduce them (the checks above); the runner's own `load_case_list`
  accepts both (119 and 6 cases; slices of 30/30/30/29 and 2/2/1/1).
- `python3 -B migration/judge/families/concurrency/offline_test.py`: 34 tests, all passing (38 since the
  live check added four, below). In the tests that drive the scripts, every subprocess call other than
  `git`, and every TCP connection, fails the test unless it is replaced by a stand-in. The port-check
  tests connect only to sockets they opened themselves on 127.0.0.1 ports the system chose (one runs
  the README's one-line check in a python subprocess), and the pid test uses a sleeping python process
  as the stand-in server.
  - Merge: recordings built with the runner's real case selection (send-reap.txt, multi-connection.txt
    and all 272 cases), merged in shuffled order, are accepted by the runner's real `compare` against a
    one-slice recording (all identical, no problems, no notes); a changed file shows as `different`.
    Slices at two different `repo_head`s merge, with a null `repo_head`, each slice's in
    `merged_slices`, and only a note from compare. The merge refuses a different seekdb, init,
    `fresh_instance_per_case`, runner or tools/deploy tree in one slice, a duplicate slice index, a wrong
    slice count, a missing slice, a slice holding the wrong cases, a stray file, a file that disagrees
    with its outcome, and a used output directory. A slice error, a failed case, an unfinished slice and
    a retried case are carried into the merged manifest, and compare refuses each with the problem named.
  - `run`, with the runner replaced by a stand-in that writes a recording: four runner commands with
    ports 3891-3894, `--slice-index` 0-3, `--slice-count 4`, `--max-retries 0`,
    `--no-ignore-trailing-whitespace`, their own base, work and record directories, the chosen init
    files, the case list and per-slice save directories, and no `$SEEKDB_COV_PROFRAW_DIR`; the four ports
    are checked before any runner starts, and all four start before any finishes; `--serial` runs them
    one after another and checks each port again first; the merged recording is accepted by compare; a
    failed slice fails the run; a port that answers stops the run before any runner starts, with nothing
    created; a port taken during a `--serial` run leaves that slice and the later ones unstarted, with
    the reason in parallel.json and the merge refused; a used output directory and repeated ports are
    refused.
  - startup_connect.py, with obclient and the server replaced by a stand-in whose port answers 2003
    until some time after the start: two runs whose start-up takes 0.3 s and 0.8 s give identical
    recordings (3 cases); the recording holds `2003 (HY000)` for both sessions, both outputs, the probe
    database and its check after each restart, and no counts or times; the port is checked once per
    round, before the start and, in a restart round, after the kill; an extra startup error code shows
    as a difference; a
    port that accepts a TCP connection is a run error before any session attempt, and a listener that
    appears right after that check is still caught by the sessions' first attempts; a server that never
    answers, one that exits while starting, and one that loses the probe database each fail their case
    with a `.partial`, and the later rounds are not run.
  - sysbench_parity.py, with docker and obclient replaced by stand-ins: two runs whose multi-thread
    `oltp_read_write` runs leave different table contents give identical recordings (7 cases), with the
    report line and `ignored errors by code: none` recorded for every run, 14/4/2 queries per event
    checked at 16 and 64 threads too, and no checksum after those two runs; the recorded sysbench
    command lines leave out host and port, the docker commands carry them; prepare runs with
    `--threads=1`; every run has `--time=0` and no `--report-interval`; `--db-ps-mode=disable` only for
    `oltp_read_write` runs; the server starts with perf_run.sh's parameters, after its port was checked.
    Ignored errors at 16 threads (two 1213 and one 1205) fail that case, with the report and the codes in
    its `.partial`, and compare refuses the recording; a write that changes no row at 64 threads (4N-1
    writes, 2N+1 others) fails that case; the query-count check also fails when only the `DEBUG` lines
    show an ignored error. A port that answers is a run error before the server starts. A FATAL line
    (its error code in the `.partial`), a lost row, a point-select run that changes a table, an index
    that disagrees in one table and a plan that does not use k_1 each fail their case, and the cases
    after it are not run; another sysbench version is refused before anything starts. The parser reads
    the report format of the baseline logs and the FATAL format of error 5930 that the first baseline
    run hit.
  - The port check itself, on sockets the test opened: a listening port is reported as answering, a
    closed port counts as free, and a connection that times out counts as not free. The one-line check
    that the live-check commands run before s1 is read from this README and run with its port pointed
    at such sockets: exit 0 on the closed port, exit 1 with the message on the listening one.
  - The pid check and the kill, against a stand-in process: sdb.py's own check accepts it only with the
    matching `--base-dir`, the kill sends one SIGKILL to that pid and nothing else, and afterwards the
    pid is gone and the check reports the server as exited.
- The tests catch each change made after review: undoing the report check at 16 and 64 threads, the
  `DEBUG`-line condition, the `repo_head` exception, any of the four port checks, or the refusal test
  inside the port check makes at least one test fail.
- Read, not run: sysbench 1.0.20's C and internal Lua sources (the 1.0.20 tag, cloned to /tmp) and the
  container's Lua scripts with `docker exec sb cat`; one `docker exec sb sysbench --version` was run
  while reading (it prints `sysbench 1.0.20` and exits; nothing was connected to). The baseline logs
  were read again for the counts above.

## Recording a build

The same commands record the reference again or record another build, such as the Rust one: set
`BUILD` to its binary and `OUT` to a new directory. Nothing else may run a server on these ports, and
the Docker container `sb` must be running.

```
cd /Users/colin/seekdb-dev/migrate-to-rust
git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py && test -z "$(git status --porcelain -- tools/deploy)" || echo "tools/deploy is not 834bbee1e's"
BUILD=/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb
CLIENT=/Users/colin/seekdb-dev/ref-archive-834bbee1e/client
REC=/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/11-conc
OUT=/Users/colin/seekdb-dev/mysqltest-runs/<new directory>
F=migration/judge/families/concurrency
RUNNER=.github/script/seekdb/mysqltest_for_seekdb.py
mkdir -p $OUT

# 1. four slices at once, reduced init, the 128 plain-SQL cases
python3 $F/parallel_slices.py run --seekdb $BUILD --obclient $CLIENT/obclient --mysqltest $CLIENT/mysqltest \
  --out-dir $OUT/p1 --init reduced --case-list migration/judge/lists/plain-sql.txt > $OUT/p1.log 2>&1
python3 $RUNNER compare --left /Users/colin/seekdb-dev/ref-archive-834bbee1e/recordings/a10-rec1 --right $OUT/p1/merged --out $OUT/p1.json

# 2. the 119 multi-connection cases, full init: four slices at once, and as one slice
python3 $F/parallel_slices.py run --seekdb $BUILD --obclient $CLIENT/obclient --mysqltest $CLIENT/mysqltest \
  --out-dir $OUT/p2 --init full --case-list migration/judge/lists/multi-connection.txt > $OUT/p2.log 2>&1
python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import recording_common; recording_common.require_free_ports([3891])' $F && \
python3 $RUNNER run --seekdb $BUILD --obclient $CLIENT/obclient --mysqltest $CLIENT/mysqltest \
  --base-dir $OUT/s1/base --work-dir $OUT/s1/work --port 3891 --slice-index 0 --slice-count 1 \
  --max-retries 0 --no-ignore-trailing-whitespace --record-dir $OUT/s1/rec \
  --case-list migration/judge/lists/multi-connection.txt > $OUT/s1.log 2>&1
python3 $RUNNER compare --left $REC/seq-1/rec --right $OUT/p2/merged --out $OUT/p2.json
python3 $RUNNER compare --left $REC/seq-1/rec --right $OUT/s1/rec --out $OUT/s1.json

# 3. sysbench
python3 $F/sysbench_parity.py --seekdb $BUILD --obclient $CLIENT/obclient \
  --base-dir $OUT/sb/base --record-dir $OUT/sb/rec --port 3896 > $OUT/sb.log 2>&1
python3 $RUNNER compare --left $REC/sb-2/rec --right $OUT/sb/rec --out $OUT/sb.json

# 4. two sessions while the server starts
python3 $F/startup_connect.py --seekdb $BUILD --obclient $CLIENT/obclient \
  --base-dir $OUT/st/base --record-dir $OUT/st/rec --port 3895 > $OUT/st.log 2>&1
python3 $RUNNER compare --left $REC/st-1/rec --right $OUT/st/rec --out $OUT/st.json
```

The one-slice run calls the runner directly, which has no port check of its own, so the line before it
runs the same check on 3891 and the run starts only if it passes. For a build other than the
reference, each `compare` must find every case identical, with only the note that `seekdb_sha256`
differs, except for the two quarantined cases (../../quarantine.tsv) that recordings of the reference
do not reproduce ("Not comparable", in the live check below): a difference in
subquery.idx_with_const_expr_21_subquery_dilang is not counted, and a recording in which
vector_index.create_table_with_vector_index stops on `PURGE RECYCLEBIN` with 4012 is made again, for
either build, as the quarantine list says.

## Live check, 2026-09-28

Every run used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256
db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91) with the archive's obclient and
mysqltest. The outputs are in /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/11-conc/: one
directory per run, the commands in run.sh (it checks, before every run, that tools/deploy and sdb.py
are 834bbee1e's, that tools/deploy has no local change, and that at least 8 GiB are free on /), the
comparisons in compare-*.log and .json (compare.sh). parallel_slices.py passes
`--max-retries 0 --no-ignore-trailing-whitespace` to every slice, and so does the one-slice run. Ports:
3891-3894 for the slices, 3897 for the one-slice run, 3896 for sysbench, 3895 for the start-up
sessions, 3898 and 3899 for the diagnostics below; no other agent ran a server during the check. The
runner was the live file (sha256 29e19d689317750c6d55e2b61e8c100ce93a4cfc5ddc86c6ec76a67d5ae188b6);
this unit did not change it.

```
cd /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/11-conc
./run.sh par-reduced-1   # 4 slices at once, reduced init, lists/plain-sql.txt
./run.sh par-1           # 4 slices at once, full init, lists/multi-connection.txt
./run.sh seq-1           # the same 119 cases as one slice, port 3897
./run.sh ser-1           # the same 4 slices one after another (--serial)
./run.sh par-2           # 4 slices at once again
./run.sh sb-2; ./run.sh sb-3   # sysbench_parity.py, port 3896
./run.sh st-1; ./run.sh st-2   # startup_connect.py, port 3895
./compare.sh sb-2-vs-sb-3 sb-2/rec sb-3/rec      # and the other pairs below
```

### Result

| Run | What | Time | Result |
|---|---|---|---|
| par-reduced-1 | 4 slices at once, reduced init, the 128 plain-SQL cases | 58 s | 128 recorded; against the archived one-slice recordings a10-rec1 and a10-rec2: 128 identical each, exit 0 |
| par-1 | 4 slices at once, full init, the 119 multi-connection cases | 445 s | 119 recorded, none failed |
| seq-1 | the 119 cases as one slice | 18.5 min | 119 recorded, none failed; against par-1: 119 identical, exit 0, no notes |
| ser-1 | the 4 slices one after another | 20.7 min | 119 recorded, none failed; against seq-1 and against par-1: 118 identical, 1 different (subquery.idx_with_const_expr_21_subquery_dilang, quarantined; below), exit 1 |
| par-2 | 4 slices at once, second run | 21.3 min | 118 recorded, 1 failed: vector_index.create_table_with_vector_index, whose last statement ran 1,000 s and failed with 4012 (below); against ser-1: 118 identical, 1 missing; against seq-1 and par-1: 117 identical, 1 different (the quarantined subquery case), 1 missing; exit 1 each (the failed case) |
| sb-2, sb-3 | sysbench_parity.py, 1, 16 and 64 threads | 3 min each | 7 cases each, all checks held; sb-2 against sb-3: 7 identical, exit 0 |
| st-1, st-2 | startup_connect.py, a first start and two restarts after a kill | 8 s each | 3 cases each, all checks held; st-1 against st-2: 3 identical, exit 0 |

- **Four default-sized servers fit.** Each slice of par-reduced-1, par-1 and par-2 started one server
  and kept it for all its cases (one `sdb.py start` per slice runner.log), except par-2's slice 2,
  where the runner started a new one after the failed case, as it does after every failed case; the
  four servers sized from `memory_budget`'s default ran side by side on this Mac.
- **117 of the 119 cases are identical in all four recordings** (par-1, par-2, ser-1, seq-1),
  histogram.stats_farm (quarantined) among them, so neither the grouping of cases into slices nor the
  load of four servers changed their output. The two others are not comparable ("Not comparable",
  below): subquery.idx_with_const_expr_21_subquery_dilang (quarantined) splits two against two, and
  vector_index.create_table_with_vector_index failed once.
- **What each start-up session saw:** `2002 (HY000)` and then the full answer, in all three rounds of
  both runs; the second and third rounds listed `startup_probe`. Each session made 26 to 37 attempts
  in 1.6 to 2.4 s per round (in `outcomes.<case>.details`, not in the recording).
- **sysbench's counts are exact on the reference:** every `oltp_read_write` run of sb-2 and sb-3, at 1,
  16 and 64 threads, reported 420,000 reads, 120,000 writes, 60,000 others, no ignored error and no
  reconnect, and every `oltp_point_select` run 200,000 reads.

### What the runs found and what changed

1. **The one-thread prepare failed with the LuaJIT compiler on.** The first diagnostic run (the
   coverage build, diag-cov-sb-failed-jit) stopped in prepare with `FATAL: mysql_drv_query() returned
   error 1050 (Table 'sbtest2' already exists)` after creating sbtest1 to sbtest13; the server's log
   shows the 14th CREATE TABLE naming sbtest2. On the reference (diag-prepare-1, diag_prepare.py) the
   stock script failed the same way twice, after sbtest12, and a wrapper that turns the compiler off
   created all 16 tables in order. sysbench_parity.py now runs every command through such a wrapper
   ("sysbench against one build", above), writes it into the container, records its text in the
   manifest and includes it in the client's digest, and refuses a container whose wrapper does not
   hold that text. An offline test checks this (the wrapper's exact text, every command running it,
   the digest changing with it, a missing or different wrapper refused before the server starts).
2. **One seed for every run made writes change no row.** sb-1, the first recording with the wrapper,
   failed `oltp_read_write_16`: 118,220 writes and 61,780 others instead of 120,000 and 60,000, with
   no ignored error. The cause is the seed: each worker seeds itself from libc `random()` after
   `srandom(seed)`, so with `--rand-seed=1` for every run the worker of the 16-thread run that started
   first drew the same stream as the one worker of the one-thread run before it, sent the same `UPDATE
   sbtest<n> SET c=? WHERE id=?` with the value the row already held, and the server reported 0 rows
   changed, which sysbench counts as other. A 16-thread run straight after prepare (diag-rw-1,
   diag_rw.py) had exact counts, since no earlier run had written with the same stream. diag-seq-1
   (diag_seq.py, the family's sequence on a scratch instance) showed it growing with each run that
   shares a seed with earlier ones: 1,795 such writes in the 16-thread run, 7,117 in the 64-thread
   run, then 28,045 and 28,143 in a second round of both. diag-seq-2 gave each run its own seed: every
   `oltp_read_write` run at 1, 16, 64, 16 and 64 threads reported exactly 120,000 writes, and a last
   16-thread run that reused an earlier seed reported 94,617 writes. sysbench_parity.py now gives each
   run its own seed (1 to 6 by default; the manifest keeps them as `sysbench.rand_seeds`, and the
   recorded command lines show them). The query-count check stays as it was; an offline test checks
   the seeds and that a reused seed fails the 16-thread case.
3. **The merged recording dropped the runner's newer recording keys.** parallel_slices.py was written
   before the runner gained `seekdb_parameters`, `ps_protocol`, `compress`, `plan_cache_stats`,
   `plan_cache_read`, `test_dir` and `test_dir_sha256`. The merge already required them to be equal in
   all slices, but left them out of the merged manifest, so `compare` read their defaults: a merge of
   `--ps-protocol` slices would have compared as a text-protocol recording. The merged manifest now
   carries every key of the runner's `RECORDING_INPUT_KEYS` and `RECORDING_NOTE_KEYS` that the slices
   have; par-reduced-1 and par-1 were merged again with it (par-reduced-1-remerged, par-1-remerged):
   every `.result` file is byte-identical and only the manifest gained those keys, and the comparisons
   above use the re-merged recordings (the old merges compare the same way). An offline test checks
   that a merge of `--ps-protocol` slices keeps the key and fails against a text-protocol recording.
4. **parallel.json timed the slices in index order.** `run` waited for slice 0, then slice 1, and so on,
   so a slice that ended while an earlier one was still running was given the earlier one's time
   (par-1's slices 1 to 3 all show 444.59 s). It now polls all four and times each at its own exit;
   par-2 is the first run with it. An offline test has the slices end in reverse order.
5. **obclient's code for nothing listening is 2002, not 2003** ("Two sessions while the server
   starts", above). Nothing else changed in startup_connect.py.

### What the multi-thread sysbench runs exercise that the 272 cases do not

A diagnostic run of the family's sysbench sequence against the coverage-instrumented 076eb309b build
(diag-cov-sb, diag_cov_sysbench.py, the same commands, wrappers and seeds as sysbench_parity.py) copied
the build's profile after each step; cov_diff.py lists the lines a step ran that the 272 cases'
profile (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata) never ran. The
build's counters are not updated atomically, so a multi-thread run loses some increments, and a line
whose count is worked out from other counters can show runs that did not happen: obmp_query.cpp's
branch for a session without CLIENT_MULTI_STATEMENTS (lines 225-240) shows 10.1k runs, although every
sysbench login in the server's log carried that flag. The findings below rest on counts of function
and branch entries.

- **Sessions sharing a prepared statement.** In the 16- and 64-thread `oltp_point_select` runs every
  prepare found the statement the one-thread run had prepared: the prepare's cache-hit branch in
  `ObSql::handle_ps_prepare` (src/sql/ob_sql.cpp:1405-1437) and `fill_result_set` ran about 1,270
  times; the 272 cases ran it 0 times (all 30 of their prepares, over both passes, missed). The
  reference's log of diag-seq-1 has 16 `add stmt item` lines in all, at the start of the one-thread
  run.
- **The session sweep meeting a commit in flight.** Every 5 seconds the session manager visits each
  session, and for one whose explicit COMMIT is still in flight it checks the commit's deadlines
  (`data_plane::cancel_timed_out_tx_commit`, src/storage/tx/ob_tx_api.cpp:1797-1805): 1 time in the
  one-thread `oltp_read_write` run, 12 in the 16-thread run and 47 in the 64-thread run; 0 times in
  the 272 cases, whose sweeps found a session in a transaction 46 times but never one committing. The
  mutation below uses this.
- **Row-lock conflicts are rare.** `ObMvccRow::mvcc_write` reported a write-write conflict
  (src/storage/memtable/mvcc/ob_mvcc_row.cpp:1072) 0 times in the 16-thread run and 4 times in the
  64-thread run, and the lock wait manager parked one request (ob_lock_wait_mgr.cpp:223), because
  sysbench spreads its rows uniformly over 16 tables of 100,000 rows. A change to the row-lock
  conflict path would show in this family only by chance; the multi-connection cases that wait on a
  lock on purpose (sfu, trx.*, deadlock_detector.trans_deadlock_basic) are what cover it, in family 1
  as well as here. Readers meeting a row whose transaction is committing are frequent (the wait in
  `LockForReadFunctor::inner_lock_for_read`, src/storage/tx/ob_tx_data_functor.cpp:340, 149 times in
  the 16-thread run and 762 in the 64-thread run) but the 272 cases reach that wait too (4.65k times).

### Not comparable

- **subquery.idx_with_const_expr_21_subquery_dilang** (one of the 119 multi-connection cases,
  quarantined, judge/quarantine.tsv). ser-1 returned the three rows GG1 to GG3 for each of the nine
  selects bounded by `date_add(current_timestamp(), interval -1 microsecond)` and the like (as the
  checked-in .result does), while seq-1 and par-1 returned none. That is the cause the quarantine entry
  states and investigations/subquery-datetime-rounding.md explains: `now()` and `current_timestamp()`
  are truncated to the whole second, so the selects exclude the rows whenever the inserts and the
  selects start in the same wall-clock second. It depends on the clock, not on how the cases are
  grouped or on the load (par-2 and ser-1 returned the rows, par-1 and seq-1 did not), and the
  quarantine rule applies: the case is compared only while two C++ recordings agree on it, and here
  they do not.
- **vector_index.create_table_with_vector_index** (quarantined since 2026-09-28, with the six other
  configured cases that send `PURGE RECYCLEBIN`; ../../quarantine.tsv). In par-2 its last statement,
  `PURGE RECYCLEBIN` (test line 232), ran for 1,000 s and failed with
  `mysqltest: At line 232: query 'PURGE RECYCLEBIN' failed: 4012: Timeout`; the case took 8 to 9 s in
  par-1, seq-1 and ser-1 and passed in both validation passes of the first sign-off. While the purge
  ran, the server's worker was spinning: a `sample` of the process
  (par-2-hang-evidence/seekdb-sample.txt) shows it in `ObPurgeRecycleBinExecutor::execute` →
  `ObDDLService::purge_table` → `ObDDLService::lock_table` → `ObTableLockService::lock` →
  `batch_pre_check_lock_` → `execute_lock_set_in_batches_`, and the log grew by several megabytes a
  second (it rotated every minute or so) with the same two lines again and again: "tablet is already
  deleted (OB_TABLET_NOT_EXIST, tablet 200480)" from `check_exist`
  (src/storage/tablelock/ob_table_lock_local_executor.cpp:64-68) and "execute table lock task"
  (ob_table_lock_service.cpp:1333) (par-2-hang-evidence/seekdb-log-excerpt.txt). At 13:11:55 the
  purge's transaction reached its timeout (`tx abort ... OB_TRANS_TIMEOUT`, then `lock_table(ret=-6210)`
  and `purge expire recycle object of runtime finished(... ret=-6210)` in the log the runner saved,
  par-2/slice_2/work/failures/instance/seekdb_log/). `need_retry_partial_task_`
  (ob_table_lock_service.cpp:1739-1747) retries a lock task that returns OB_TABLET_NOT_EXIST, which is
  meant for a tablet still being created, and `check_exist` returns the same code for a tablet the GC
  has already deleted, so the lock is retried until the transaction times out. The tablet in par-2
  belonged to `t14_spatial`, which fork_table.fork_table_error had dropped earlier in the same slice,
  but a leftover object from an earlier case is not needed: family 7's rec2
  (../plan_cache/README.md), which runs every case on a fresh instance, hung in this same case on one
  of the case's own dropped tables. So it is the case's own drops that race the tablet GC, and whether
  the GC deleted the tablet before the purge locks it is timing. The reference hung in this case in
  three recordings so far (par-2 here, family 7's rec2 over text, family 8's rec4 over the binary
  protocol, ../ps_protocol/cases.txt), and the other purge cases hung in three more
  (../../quarantine.tsv). It is a liveness failure of the reference itself; the case is compared only
  while two C++ recordings agree on it, and a recording that stops on this hang is made again.

Every other case of the four multi-connection recordings, and every case of the sysbench and start-up
pieces, compared identical in every pair.

## The mutation for the second sign-off

migration/judge/mutations/second-set/11-conc-session-sweep-cancels-live-commit.patch, with its note
beside it: the commit-deadline test in `data_plane::cancel_timed_out_tx_commit`
(src/storage/tx/ob_tx_api.cpp:1801) is inverted, so the 5-second session sweep takes the callback of
any commit in flight that is still within its deadline and answers the client with 4012 (statement
timeout). Only concurrent sessions give the sweep a commit in flight: the 272 configured cases never
reach the line (0 times in both coverage passes), while the family's `oltp_read_write` runs reach it
dozens of times, and the first COMMIT answered with 4012 makes sysbench stop with a FATAL line, which
fails the case and the comparison with sb-2. Caught on 2026-09-28 (../../mutations/second-set/README.md).
