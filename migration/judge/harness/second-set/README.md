## The runner options `--ps-protocol`, `--plan-cache-stats` and `--test-dir`

PLAN.md section 4: item 4 and family 8 (`--ps-protocol`), item 6 and family 7
(`--plan-cache-stats`), and the families that bring their own .test files (`--test-dir`). They
were written on a copy of the runner, `mysqltest_for_seekdb.py` in this directory, while the
injected-mutation runs used the live file. `runner-second-set.patch` here is the whole change
against the live `.github/script/seekdb/mysqltest_for_seekdb.py` and applies with `git apply` from
the worktree root. The copy cannot run from this directory: the runner finds tools/deploy and
sdb.py from its own path, so every command below runs the live path once the patch is applied.

The change also adds `--seekdb-parameter` to `run` (family 7 needs it, below) and
`--require-plan-cache` and `--require-ps-protocol` to `compare`. Everything new is off by default.
With none of it, the runner builds the same sdb.py and mysqltest commands and writes the same values
into seekdb_result.json and manifest.json as before, apart from `runner_sha256`; it only adds the new
keys (checked in-process against the live runner, below).

| Option | Default | What it does |
|---|---|---|
| `run --ps-protocol` | off | Passes `--ps-protocol` to mysqltest; recorded as `ps_protocol` |
| `run --plan-cache-stats` | off | Checks on every new instance that the counter read counts only itself, then reads the server's plan cache counters before and after every case; per-case hits and misses go into seekdb_result.json and, with `--record-dir`, into `DIR/plan_cache.tsv` |
| `run --seekdb-parameter NAME=VALUE` | none | Passes `--parameter NAME=VALUE` to `sdb.py start` for every instance; repeatable; recorded as `seekdb_parameters` |
| `run --test-dir DIR` | the configured cases | Runs `DIR/<name>.test`, sorted by name, with the expected output in `DIR/r/<name>.result` |
| `compare --require-plan-cache` | off | A recording made without `--plan-cache-stats` is a recording problem |
| `compare --require-ps-protocol` | off | A recording made without `--ps-protocol` is a recording problem |

Every judge run still passes `--max-retries 0 --no-ignore-trailing-whitespace`. With `$H`, `$REF` and
`$CLI` as in PLAN.md section 10, one C++ recording for family 7 is:

```
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $REF --obclient $CLI/obclient --mysqltest $CLI/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN --port 3881 \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --plan-cache-stats --fresh-instance-per-case \
  --seekdb-parameter plan_cache_evict_interval=1d --record-dir $RUN/rec
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --require-plan-cache --out $OUT/family7.json
```

Family 8 is the same run with `--ps-protocol` in place of the three plan cache options
(`--plan-cache-stats --fresh-instance-per-case --seekdb-parameter ...`), compared with
`--require-ps-protocol`. Without the `--require-...` option, `compare` passes two plain
recordings whose .result files match, so a family 7 or family 8 comparison must name it.

### `--ps-protocol`

mysqltest then sends each statement that its prepared-statement list accepts through
COM_STMT_PREPARE and COM_STMT_EXECUTE. The list is a regular expression inside the mysqltest
binary (deps/3rd/u01/obclient/bin/mysqltest, 2.2.12 on MariaDB 10.4.18): a statement goes that way
when it starts with SELECT, INSERT, UPDATE, DELETE, REPLACE, SHOW, CREATE or DROP TABLE, COMMIT,
ROLLBACK, GRANT or another keyword on the list, followed by white space. Every other statement still
goes as text (COM_QUERY), for example `SET @v = 1`, `BEGIN`, `CALL`, `EXPLAIN`, `DESC` and `USE`.

`ps_protocol` is written to seekdb_result.json and manifest.json. `compare` refuses to compare a
`--ps-protocol` recording with a text-protocol one. A manifest written before this option has no
`ps_protocol` key and counts as text protocol. The checked-in .result files were made over the text
protocol, so a `--ps-protocol` run checked against them can fail where the binary protocol prints
differently; the judge compares recordings, C++ against C++ first.

### `--plan-cache-stats`: which counters

- seekdb has no GV$OB_PLAN_CACHE_STAT or V$OB_PLAN_CACHE_STAT; the inner table list says to use
  `oceanbase.__all_virtual_plan_cache_stat` instead (src/share/inner_table/ob_inner_table_schema_def.py:6518
  and :7476). That table is defined at ob_inner_table_schema_def.py:2957-2977 (table id 11003), with
  `access_count` and `hit_count` at :2970-2971.
- Its iterator returns one row, from the one plan cache of the server
  (`server_service<ObPlanCache>()`, src/observer/virtual_table/ob_all_plan_cache_stat.cpp:131-147),
  and fills the two columns from `ObPlanCacheStat::access_count_` and `hit_count_` (:84-91). So the
  counters cover every session, and the runner fails the read unless it gets exactly one row.
- The counters only go up: they are incremented atomically (src/sql/plan_cache/ob_plan_cache.h:347-352)
  and set only by the constructor (src/sql/plan_cache/ob_plan_cache_struct.h:502-514). Flushing the plan
  cache does not reset them.
- A hit adds 1 to both: `inc_hit_and_access_cnt()` when `pc_get_plan` finds a plan
  (src/sql/ob_sql.cpp:2999). A statement parsed on the long path adds 1 to `access_count` only:
  `inc_access_cnt()` in `parser_and_check` for every DML statement and SHOW VARIABLES
  (ob_sql.cpp:3255-3259; `IS_DML_STMT` is SELECT, INSERT, UPDATE, DELETE, MERGE and multi-table
  INSERT, src/objit/include/objit/common/ob_item_type.h:2389-2390). PL cache lookups use the same two
  counters (src/pl/pl_cache/ob_pl_cache_mgr.cpp:118-122).
- So for a case, `hits` is the change in `hit_count` and `misses` is the change in `access_count`
  minus `hits`.

Each case's hits and misses go into seekdb_result.json under `plan_cache`, with the raw counters of
both reads, the time each read started and finished, and `seconds`, the time between the two reads.
With `--record-dir` they also go into `DIR/plan_cache.tsv`: a header line naming `case`, `hits` and
`misses`, then one line per case, the fields separated by tabs; the manifest keeps the seconds as
`plan_cache_seconds`. A read that fails leaves the case out of plan_cache.tsv, names it in
`plan_cache_errors` (seekdb_result.json and manifest.json) and fails the run.

### `--plan-cache-stats`: what one read counts

Before and after every case the runner runs, as root:

```
obclient -h HOST -P PORT -uroot -A -B -N --proxy-mode \
  --init-command='SET ob_enable_plan_cache = 0' \
  -e 'SELECT access_count, hit_count FROM oceanbase.__all_virtual_plan_cache_stat'
```

- **Why `--proxy-mode`.** The client library linked into obclient 2.2.12 sends
  `select @@version_comment, @@version limit 1` right after login, before the init command: in
  `_mthd_my_real_connect` the call to `_get_ob_server_version` (0x100026234) comes before the
  init-command loop (0x100026258-0x1000262c4), and `_get_ob_server_version` sends the query through
  `mysql_real_query` (0x100026480) unless the connection's server-version field (offset 0x528) holds
  0xfe (the check at 0x10002643c). That query would run with the plan cache on, and would be a hit
  or a miss depending on whether its plan was still cached. `--proxy-mode` is a flag without an
  argument (its entry in obclient's option table is at file offset 0x5ced98; help text "Proxy mode
  is not support getObServerVersion."); when it is set, `do_connect` stores 0xfe in that field before
  connecting (0x10000988c-0x100009898), so the query is not sent. The flag's only other uses are the
  welcome banner, which `-e` never prints (0x100004a88), and the `status` command (0x1000115fc). All
  of this is from disassembling /Users/colin/seekdb-dev/ref-834bbee1e/deps/3rd/u01/obclient/bin/obclient
  (the same file as the archive's client/obclient) with deps/3rd's llvm-objdump; obclient was not run.
- **The init command.** It turns the plan cache off for the reading session only
  (`ob_enable_plan_cache` is a GLOBAL and SESSION variable,
  src/share/system_variable/ob_system_variable_init.json:1541-1553). The SET itself counts nothing:
  it is looked up while the cache is still on, but a lookup counts only when it finds a plan
  (ob_sql.cpp:2999), and only DML and SHOW VARIABLES are counted on the long path or added to the
  cache (ob_sql.cpp:3255-3264; `add_plan_to_pc` starts false at :3553), so a SET is never found.
- **The SELECT.** Each statement reads the setting when it starts (ob_sql.cpp:1949). With the plan
  cache off, the SELECT skips the lookup (ob_sql.cpp:1985), is never added to the cache
  (ob_sql.cpp:3525), and adds 1 to `access_count` in `parser_and_check` (ob_sql.cpp:3259), which runs
  before the SELECT reads the counters. So a read adds exactly 1 access and 0 hits, it is counted in
  its own result, and it leaves nothing in the cache for later statements.
- **What that means for a case.** The before-read's access falls before the case. The after-read's
  access falls inside it: every case's `misses` include exactly 1 from the after-read, in both builds.
  The runner does not subtract it.
- **The runner checks this on every new instance.** Right after the instance starts and its init
  files ran (the first instance, a fresh instance per case, a restart after a failed case, a retry),
  it takes two reads back to back and requires 0 hits and 1 access between them. Inner SQL that runs
  between the two reads spoils an attempt, so it tries up to 3 times, a second apart. If no attempt
  passes, or a read fails, the run stops with an error. Each check and its attempts go into
  `plan_cache_checks` in seekdb_result.json and manifest.json (`[ PC CHECK ]` lines in the log). A
  build whose counters count the read differently fails here, which is itself a family 7 difference.
- The manifest records the read's whole argument list except host and port as `plan_cache_read`,
  and `compare` refuses two recordings whose reads differ.

mysqltest's own connections still send the version query: its client library has the same
`_get_ob_server_version` (0x1000255b0 in deps/3rd/u01/obclient/bin/mysqltest, called at 0x1000253bc)
and no proxy option. Each connection a case opens therefore adds 1 access, plus 1 hit when the plan
for that connection's database is still cached. That belongs to the case and is compared like the
rest.

### `--plan-cache-stats`: what else the counters include

The counters are server-wide, and inner SQL goes through the same plan cache: it runs through
`ObSql::stmt_query` (src/observer/ob_inner_sql_connection.cpp:72) with the plan cache on. So a case's
counts also include inner SQL that ran between its two reads:

- The job scheduler checks for jobs about every 20 seconds (`CHECK_NEW_INTERVAL`,
  src/observer/dbms_scheduler/ob_dbms_sched_job_master.h:131; the loop at
  ob_dbms_sched_job_master.cpp:133 and :155) with `select * from __all_tenant_scheduler_job ...`
  (ob_dbms_sched_table_operator.cpp:486-493). The reference's log shows it at 21:20:49, 21:21:09 and
  21:21:32 (`check new jobs` in /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-B/failures/instance/seekdb_log/).
  A case whose reads are 20 seconds or more apart almost always spans one; a shorter case spans one
  with a chance of about its length over 20 seconds.
- Jobs that earlier cases created run their own inner SQL when due. The same log lists
  `<table id>_refresh` jobs every 600 seconds and `<table id>_rebuild` jobs every 86,400 seconds, which
  vector index DDL creates (src/observer/vector_index/ob_vector_index_sched_job_utils.cpp:89 and :133).
  `--fresh-instance-per-case` removes these.
- A case's own DDL runs inner SQL too. That is counted in the case, the same way every time, as long
  as it finishes before the after-read.

Whether a plan is still cached also depends on the clock. The plan cache's eviction task runs every
`plan_cache_evict_interval` (5 seconds by default, src/share/parameter/ob_parameter_seed.ipp:372),
scheduled once when the plan cache starts (src/sql/plan_cache/ob_plan_cache.cpp:351). Besides
evicting by memory use, each round drops plans that were not used for more than 30 seconds
(`IDLE_EVICT_THRESHOLD_US`, ob_plan_cache.h:483; the test at ob_plan_cache.cpp:282;
`cache_evict_by_idle` at :1355-1398, called from `run_plan_cache_task` at :2249-2263). A round looks
at no more than 5,000 buckets and 1,000 plans (ob_plan_cache.h:481-482), so a plan goes some time
after its 30 idle seconds. The reference's saved log shows 14 such rounds in the 73 seconds it covers,
dropping 7 to 383 plans each (`idle eviction collected plans`), while the plan cache held at most
202 MB of its 412 MB limit, so memory eviction played no part there. The hit or miss of any statement
whose plan was last used 30 or more seconds earlier therefore depends on timing: a statement a case
runs again more than 30 seconds after its last use (trx_timeout, for one, spends 33 seconds in
`--real_sleep`), the version query of a connection opened after a quiet spell, and inner SQL whose
plan was last used during start-up or init and which a case's DDL runs again. A fresh instance per
case does not remove this, since start-up and init start the clock, and both differ between builds.

`--seekdb-parameter plan_cache_evict_interval=1d` removes it. The runner passes it to `sdb.py
start`, which passes it on to seekdb as `--parameter` (.github/script/seekdb/sdb.py:129-130). seekdb
puts `--parameter` values into its configuration in `init_config`
(src/observer/ob_command_line_parser.cpp:346-347, src/observer/ob_server.cpp:1722-1735), the first
step of `ObServer::init` (ob_server.cpp:634), and creates the plan cache later
(src/observer/omt/ob_server_runtime_controller.cpp:1420), so the timer is scheduled with the new
interval and the first eviction round would come a day after start. This is not verified: no server
was started for this change. The first plan-cache run must confirm that seekdb.log has no `schedule
next cache evict task` line (the task logs one every round, ob_plan_cache.cpp:2242-2246) and no
`idle eviction collected plans` line. With the timer that slow, nothing is evicted by memory either,
and the plan cache stops taking new plans at its limit (`OB_REACH_MEMORY_LIMIT` in `add_plan`,
ob_plan_cache.cpp:885-886), which does depend on memory use; so use it with
`--fresh-instance-per-case`, as in the command above. It also means a case that changes
`ob_plan_cache_percentage` or the eviction percentages sees no effect (the timer is what reads them,
`update_memory_conf` at ob_plan_cache.cpp:1612), and the PS cache, whose eviction timer uses the
same parameter (src/sql/plan_cache/ob_ps_cache.cpp:125), keeps its entries too. Both are the same on
both builds.

The scheduler checks remain. `compare` prints the seconds between the reads on both sides for every
case whose counts differ, and adds `timing-sensitive: 20 s or longer` when either side is 20 seconds
or more; the summary line counts those as `different_timing_sensitive`. It is a label, not a mask:
the case still counts as different. In /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-A/runner.log,
16 of the 272 cases ran 20 seconds or more: fork_table.fork_table_cow (119.2 s),
fork_table.fork_table_vector (88.5), fork_table.fork_table_ddl (70.3), vector_index.rebuild_vector_index
(62.8), fork_table.fork_table_build_data (58.0), vector_index.all_virtual_vector_index_info (56.3),
vector_index.vector_index_partitioned (43.3), fork_table.fork_table_chain (38.0),
fork_table.fork_table_with_index (35.6), trx_timeout (33.0),
`geometry.__all_tenant_spatial_reference_systems` (29.6), trx.serializable_transaction (24.7),
trx.repeatable_read_transaction (24.4), sfu_norow_alias (23.7), vector_index.vector_calc_gbk_mysql (21.5)
and vector_index.vector_index_offline_ddl (20.6). A build 1.2 times slower moves more cases past
20 seconds. Two C++ recordings come first: a case whose counts differ between them is named, with the
reason, before any C++-against-Rust comparison of this family, and the read times in
seekdb_result.json can be matched with the `check new jobs` lines in seekdb.log.

### `--seekdb-parameter NAME=VALUE`

Each value is passed as `--parameter NAME=VALUE` to `sdb.py start`, after `--nodaemon`, for every
instance the run starts. NAME is letters, digits and underscores; VALUE may not be empty or hold a
comma, because seekdb joins the values with commas; a NAME given twice is refused. The sorted list
is `seekdb_parameters` in seekdb_result.json and manifest.json, and `compare` requires it to match; a
manifest written before this option counts as an empty list.

### `--test-dir DIR`

- The cases are the `*.test` files directly in DIR, named by their file stem and run in sorted
  order. `--case-list` names stems and filters as before; `--slice-index` and `--slice-count` slice the
  sorted list. A stem with a dot is refused: the runner's file names for staged recordings and
  .reject files assume none, as in every configured case's file name. A `.test` file anywhere below
  the top level of DIR is refused too, so that no test is skipped without a word; helpers that tests
  source should be `.inc` files.
- The expected output is `DIR/r/<name>.result`. A run checked against expected output needs one for
  every selected case, and refuses a `DIR/r/*.result` whose `.test` is not in DIR; either stops the
  run before the server starts. A `--record-dir` run needs no expected output and does not look at
  the .result files in DIR/r.
- mysqltest still runs in tools/deploy, so `--source mysql_test/include/...` resolves as it does for
  the configured cases.
- Recording and `compare` work as for the configured cases. The manifest adds `test_dir` (a note in
  `compare` when the paths differ) and `test_dir_sha256`, which `compare` requires to match: a sha256
  over the path and content of every regular file under DIR, selected or not, except the `*.result`
  files directly in DIR/r. It covers data files, `.sql` and script helpers and hidden files (a
  `.DS_Store` that Finder writes changes it too). Files a test reads from outside DIR and
  tools/deploy are not covered.
- A test deleted from DIR drops out of a `--record-dir` run with no error, and both recordings made
  after the deletion agree on it. A family that keeps its case list in a file and passes
  `--case-list` gets an error for it instead.
- `merge` knows only the configured cases, so it does not merge slices of a `--test-dir` run.

### What `compare` does with them

| Manifest key | Kind | Value when the manifest has no such key |
|---|---|---|
| `seekdb_parameters` | must match | empty list |
| `ps_protocol` | must match | false |
| `plan_cache_stats` | must match | false |
| `plan_cache_read` | must match | none |
| `test_dir_sha256` | must match | none |
| `test_dir` | note | none |

When both recordings have `plan_cache_stats`, `compare` also reads both plan_cache.tsv files and
reports each case as identical, different or missing (`plan-cache <status> <case>` lines, a
`plan cache: cases=..., identical=..., different=..., missing=..., different_timing_sensitive=...`
line, and `plan_cache` in the `--out` JSON, with each case's seconds and its `timing_sensitive`
flag). A different or missing case fails the comparison (exit 1). A recording with
`plan_cache_errors`, or with `plan_cache_stats` but no passed read check on every instance in
`plan_cache_checks`, counts as a recording problem. `--require-plan-cache` and
`--require-ps-protocol` make a recording without that option a recording problem; the `--out` JSON
lists them under `required`.

### How the change was checked

No server, obclient, mysqltest or sdb.py was started. `python3 -m py_compile` and `run --help` and
`compare --help` pass. The obclient and mysqltest facts above come from disassembly only.

34 in-process tests loaded the copy and the live runner, each from a scratch copy of the tree, with
every sdb.py, mysqltest and obclient call answered by a stand-in that keeps plan cache counters (it
counts the version query when `--proxy-mode` is missing) and with git answered by a stub. They
cover: a default run giving the same sdb.py and mysqltest commands and the same seekdb_result.json
and manifest values as the live runner, and a live-runner recording comparing cleanly with a copy's;
`--ps-protocol` on the mysqltest command, which still runs in tools/deploy; the read command; the
per-case deltas, plan_cache.tsv, `plan_cache_seconds` and the order of reads and cases; the read
check passing, retrying a second later after inner SQL between the reads, giving up after three
attempts, failing on a read error, catching a read that sends the version query, and running on each
fresh, restarted and retried instance; a failed after-read failing the run and the comparison;
`--seekdb-parameter` reaching every `sdb.py start`, bad values and repeated names refused, and a
mismatch refused by `compare`; `compare` with identical and different counts, the timing label, a
missing case, missing or failed read checks, the old read format, and both `--require-...` options;
`--test-dir` order, case list, expected output, nested tests, results without a test, missing
results, dotted and unknown names, what `test_dir_sha256` covers, and different inputs refused; and
`load_case_list` called with two arguments, as families/concurrency/parallel_slices.py does, giving
the live runner's cases and error text. 19 bugs put into temporary copies (among them the read
without `--proxy-mode`, a read check that accepts anything or retries without a pause, the old
digest, each `--require-...` option ignored, nested tests accepted, the parameters not passed, the
protocol and parameter keys dropped from the must-match list, and a plan cache difference not
failing `compare`) each failed at least one test. The 34 tests of
families/concurrency/offline_test.py pass with the patched runner; with the earlier copy of this
change, 9 of them stopped with a TypeError from `load_case_list`, which parallel_slices.py calls
with two arguments.