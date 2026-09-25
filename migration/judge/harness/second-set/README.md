## The runner options `--ps-protocol`, `--plan-cache-stats`, `--test-dir` and `--compress`, and the two masks

PLAN.md section 4: item 4 and family 8 (`--ps-protocol`), item 6 and family 7
(`--plan-cache-stats`), the families that bring their own .test files (`--test-dir`), item 8's
replay over the compressed protocol (`--compress`), and the two masks of Decision 6 (b) for
families 3 and 4 (`compare --mask est` and `compare --mask row-order`). They were written on a copy
of the runner, `mysqltest_for_seekdb.py` in this directory, while the injected-mutation runs used
the live file. `runner-second-set.patch` here is the whole change against the live
`.github/script/seekdb/mysqltest_for_seekdb.py` and applies with `git apply` from the worktree
root. The copy cannot run from this directory: the runner finds tools/deploy, sdb.py and the mask
lists in migration/judge/lists from its own path, so every command below runs the live path once
the patch is applied. `hash_order_list.py` here turns the hash-order candidates into a draft of the
row-order mask's list ("The helper that writes the row-order list", below).

The change also adds `--seekdb-parameter` to `run` (family 7 needs it, below) and
`--require-plan-cache`, `--require-ps-protocol` and `--require-compress` to `compare`. Everything
new is off by default. With none of it, the runner builds the same sdb.py and mysqltest commands and
writes the same values into seekdb_result.json and manifest.json as before, apart from
`runner_sha256`; it only adds the new keys (checked in-process against the live runner, below).

Two sentences in the `compare` paragraph of ../README.md describe the live runner and become wrong
when the patch is applied, so they change with it:
- "the registry is empty until the EST and hash-order masks are added (Decision 6)" becomes "the
  registry holds the EST and row-order masks of Decision 6, described in second-set/README.md";
- "Exit 0 means every case is identical and there were no recording problems" becomes "Exit 0 means
  every case's verdict is identical (the exact result for a case outside the given masks' lists, the
  masked result for a listed one) and there were no recording problems".

| Option | Default | What it does |
|---|---|---|
| `run --ps-protocol` | off | Passes `--ps-protocol` to mysqltest; recorded as `ps_protocol` |
| `run --plan-cache-stats` | off | Checks on every new instance that the counter read counts only itself, then reads the server's plan cache counters before and after every case; per-case hits and misses go into seekdb_result.json and, with `--record-dir`, into `DIR/plan_cache.tsv` |
| `run --seekdb-parameter NAME=VALUE` | none | Passes `--parameter NAME=VALUE` to `sdb.py start` for every instance; repeatable; recorded as `seekdb_parameters` |
| `run --test-dir DIR` | the configured cases | Runs `DIR/<name>.test`, sorted by name, with the expected output in `DIR/r/<name>.result` |
| `run --compress` | off | Passes `--compress` (mysqltest's `-C`) to mysqltest, which asks for the compressed protocol; recorded as `compress` |
| `compare --require-plan-cache` | off | A recording made without `--plan-cache-stats` is a recording problem |
| `compare --require-ps-protocol` | off | A recording made without `--ps-protocol` is a recording problem |
| `compare --require-compress` | off | A recording made without `--compress` is a recording problem |
| `compare --mask est` | off | For the cases in lists/plan-bearing.txt, also compares with the EST.ROWS and EST.TIME(us) cells of every plan table replaced by `#` (family 3); the list must have the sha256 the runner pins |
| `compare --mask row-order` | off | For the statements in lists/hash-order-selects.txt, also compares with their result rows sorted (family 4); refused until the runner pins the confirmed list's sha256 |

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
`--require-ps-protocol`. Item 8's replay over the compressed protocol is the same run with
`--compress` in their place, compared with `--require-compress`. Without the `--require-...`
option, `compare` passes two plain recordings whose .result files match, so a family 7, family 8
or compressed-protocol comparison must name it.

Families 3 and 4 need no special run: they compare the same recordings as family 1, once exactly
and once with a mask, and `compare` prints both results (`--mask row-order` works once the confirmed
list's sha256 is pinned in the runner, below):

```
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --mask est --out $OUT/family3.json
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --mask row-order --out $OUT/family4.json
```

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

### `--compress`

The runner passes mysqltest's `--compress`, the long form of `-C`, after `--ps-protocol` and before
`--record`. The mysqltest binary has the option: its option table holds `compress` with the help text
"Use the compressed server/client protocol." (from the strings of
/Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest; mysqltest was not run). With it the
client library asks for CLIENT_COMPRESS at login, and the server offers it (golden-bytes-scope.md,
"The compressed protocol"). Only mysqltest's connections change: the init files and the plan cache
reads still go through obclient without it.

`compress` is written to seekdb_result.json and manifest.json, and `compare` refuses to compare
recordings that differ in it. A manifest written before this option has no `compress` key and counts
as uncompressed.

`compress: true` records only that the runner passed the option, not that every connection mysqltest
opened used the compressed protocol. MariaDB's mysqltest applies the option to the connections a case
opens with `connect` as well as to the first one, but this mysqltest was not run. The server's log
shows it for every login, so no packet capture is needed (source lines at 834bbee1e):
- `ObMPConnect` writes one `MySQL LOGIN` line per login, with `capability` and `c/s protocol`
  (src/observer/mysql/obmp_connect.cpp:250-252 and :298-303).
- `capability` is the capability flags the client sent, plus CLIENT_MULTI_STATEMENTS
  (`session_client_capabilities` at rust/sql-nio/src/capability.rs:54-56, used at
  rust/sql-nio/src/pump.rs:268; src/oblib/rpc/obmysql/ob_login_info.h:53). The log prints it in
  decimal.
- `c/s protocol` is `OB_MYSQL_COMPRESS_CS_TYPE` when those flags have CLIENT_COMPRESS (bit 0x20) and
  `OB_MYSQL_CS_TYPE` otherwise (src/oblib/rpc/obmysql/obsm_struct.h:66-77,
  src/oblib/rpc/obmysql/ob_mysql_packet.h:146).
- The server always offers CLIENT_COMPRESS (rust/sql-nio/src/capability.rs:21 and :39-40). A login
  keeps those of the client's flags that the server offers (`negotiate_client_capabilities` at
  :50-52, rust/sql-nio/src/login.rs:144), and a connection whose kept flags have CLIENT_COMPRESS gets
  compressed frames (rust/sql-nio/src/pump.rs:93-99 and :423-424). So a login line that says
  `OB_MYSQL_COMPRESS_CS_TYPE` is a compressed connection.

The first C++ `--compress` recording therefore runs with `--save-instance-dir`, and so does a plain
recording of the same cases. The check is over every `seekdb.log*` file the two runs saved:
- the two runs have the same number of `MySQL LOGIN` lines;
- in the `--compress` run, every line of a mysqltest session says
  `c/s protocol="OB_MYSQL_COMPRESS_CS_TYPE"` and has bit 0x20 set in `capability`;
- only the runner's own obclient sessions (the init files and the plan cache reads, which log in as
  `root`) say `OB_MYSQL_CS_TYPE`.

mysqltest logs in as `admin` (`MYSQLTEST_USER`), so the `user_name=admin` lines are the quick check.
A case that connects as another user adds lines under that user's name. The reference's saved log of
a plain run, /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-B/failures/instance/seekdb_log/, has
64 `user_name=admin` lines, all `OB_MYSQL_CS_TYPE`, with `capability` 2294260365 or 146776717
(0x88bfa28d and 0x08bfa28d, bit 0x20 clear). The runner does not run this check itself, for two
reasons: it cannot tell a C++ build from a Rust build, and nothing holds the Rust build's log to this
format. The check is needed once for the pinned mysqltest, whose sha256 `compare` requires to match.

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

### The two masks and how they are switched on

`compare` knows two masks, the entries of its registry `COMPARE_MASKS`: `est` (family 3) and
`row-order` (family 4), exactly the two that Decision 6 (b) allows; a third would mean changing the
registry in the runner, which Decision 6 does not allow. Both are off by default;
`--mask NAME` switches one on and may be given for both. Any other name stops `compare` with an
argparse error (exit 2) that names the known masks.

Each mask reads its declared list from the runner's own worktree: `est` reads
migration/judge/lists/plan-bearing.txt, `row-order` reads migration/judge/lists/hash-order-selects.txt.
`COMPARE_MASKS` also pins the sha256 of the signed-off version of each list
(`PLAN_BEARING_LIST_SHA256`, `HASH_ORDER_LIST_SHA256` in the runner), and `compare` stops with exit 2
when the file's sha256 is not the pinned one. So any edit to a list, committed or not, stops every
masked comparison until the runner's pin changes with it. A list therefore grows only through a
change to the runner, which a reviewer sees: Decision 6 declares the masks in 00b, has them reviewed,
and allows no additions during Step 6.
- `est` pins plan-bearing.txt as commit 5ed97a72b wrote it (sha256
  `8566d1f37b9b9875daa2ecfbdebd44d693823fbdeda520120313660ce7c54972`).
- `row-order` pins nothing yet (`HASH_ORDER_LIST_SHA256` is `None`), so `--mask row-order` stops
  until the confirmation step has written hash-order-selects.txt and its sha256 is pinned at the
  sign-off.

`compare` also stops with exit 2 in three other cases:
- a list is missing, empty or malformed;
- a list names a case that is not one of the configured cases (`discover_cases`), such as a
  misspelled name, which would otherwise never be masked and never be counted;
- either recording was made with `--test-dir`. The lists name configured cases, and a family's own
  test that happens to have the same name (`--test-dir` stems have no dot, and six plan-bearing
  names have none either, `explain` among them) must not be masked.

The report names each list with its sha256.

A mask applies only to the cases its list names. A case in no given list is compared exactly and
nothing else. When both masks are given, `row-order` runs before `est`, so its digests see the
recorded bytes.

### How `compare` reports a masked comparison

- Each case prints two results, exact and masked (`identical`, `different` or `missing`), then its
  name; a case outside every given list prints `-` as its masked result. After the exact diff comes
  the masked diff, headed `masked (est)` or `masked (row-order, est)`.
- Under a case, `row-order: echo N of <digest> (test line L): the rows come in a different order on
  the two sides; masked` names each listed statement whose rows differ only in their order. These are
  the differences the mask let through, the ones the final gate documents. A listed statement that
  could not be masked gets a `not masked` line with the reason for each side, and a case with a line
  naming both EST columns that the EST mask did not mask gets an `est:` line with the counts.
- The summary lines:
  - `compare finished: ...` as before, over every case and exact.
  - `masked (...)` over the masked cases only. It gives their masked result, then their exact result
    (`exact_identical`, `exact_different`, `exact_missing`); with `--mask est` alone, that exact
    result is family 3's report of the plan-bearing cases on their own.
  - `verdict: ...` counts each masked case by its masked result and every other case by its exact
    result.
  - `mask est: ...` and `mask row-order: ...` give the list, its sha256 and the counts described
    under `mask_lists` below.
- The exit status follows the verdict: 0 only if every case's verdict is `identical`, there is no
  recording problem, and the plan cache comparison, when there is one, passes. So `--mask est` turns
  a plan-bearing case whose only differences are EST numbers into a pass, while a difference in any
  case outside the list still fails.
- In the `--out` JSON every case has `masks` (the masks that apply to it), `status` and `diff`
  (exact) and `verdict`; a masked case also has `masked_status`, `masked_diff`, `mask_changed` and
  `mask_details`. Each row-order record in `mask_details` has `applied`, `problems` when it was not
  applied, `reordered` when it was, and the list's `hash_operators`. The top level adds
  `verdict_summary` and `mask_lists`, with these entries per mask:
  - the list, its sha256, and `listed_cases`;
  - `compared_cases`: the listed cases with a .result on both sides, the ones the mask ran on;
  - `not_compared_cases`, with their names in `cases_not_compared`: listed cases missing from either
    recording;
  - for `est`, per side, `est_header_lines` (lines naming both EST.ROWS and EST.TIME(us)),
    `tables_masked` and `tables_left_exact`;
  - for `row-order`, `listed_statements` (the lines of the list), `statements` (those of the
    compared cases), `not_compared_statements`, `masked`, `not_masked`, and `reordered`: the
    statements whose rows came in a different order on the two sides and are equal once sorted.

### The EST mask: what it changes in a plan table

The plan table is written by the server; mysqltest only prints each of its lines as one row of the
one-column result `Query Plan`, so the table starts at column 0 of its lines. `ObSqlPlan::format_plan_table`
(src/sql/monitor/ob_sql_plan.cpp:1490-1620) prints five columns, ID, OPERATOR, NAME, EST.ROWS and
EST.TIME(us) (:39-49; `PLAN_TABLE_COLUMN_CNT` is 5, ob_sql_plan.h:42). Each column is as wide as its
widest value or its name, whichever is wider (`PlanFormatHelper::init` at :1037 starts from the name
lengths, `get_plan_table_formatter` at :1056-1121 widens by the values), and every cell is
left-aligned and padded with spaces. A negative estimate prints as `more than 1.0e19` (:1096, :1108).
A line of `=` as long as the header row opens and closes the table, and a line of `-` of the same
length follows the header row (ob_sql_plan.h:38-40; :1509 counts the six `|`). The `+---+` boxes that
mysqltest itself pads under `--result_format 4` never hold a plan table in these files.

In the 40 cases of lists/plan-bearing.txt, all 658 plan tables start right after `Query Plan`: 568
have the five columns and 90 are EXPLAIN BASIC tables with ID, OPERATOR and NAME only. In all 568,
every EST cell holds digits and both EST columns are exactly as wide as their names (checked over the
checked-in .result files).

The mask takes a line as a plan table's header row only if its cells, with trailing spaces removed,
are exactly those five names; the line before it is `=` repeated to the header row's length; the line
after it is `-` of that length; the rows that follow start with `|`; the line after them is `=` of
that length again; and in every row the last two cells are as wide as the header's two EST cells and
hold digits or `more than 1.0e19` followed by spaces. The last two cells are cut from the right end of
the row, so a `|` inside a NAME cannot move them. A table that fails any of this is left exact and
counted (`tables_left_exact_left` or `_right`), with a line under the case. A table whose header row
does not have the five names in this form at all, `| ID |...` for one, is not taken as a table: it
stays exact, and what counts it is `est_header_lines_left` and `_right`, the lines of the listed
cases that name both EST.ROWS and EST.TIME(us), whatever their form. Every such line is the header
of a masked table, of a table left exact, or of one the mask did not take; whenever a case has lines
of the last two kinds, the `est:` line under it gives the counts. In a table that passes,
the mask rewrites each EST.ROWS and EST.TIME(us) cell to `#` padded to the width of the column's name
(8 and 12), the header row's two EST cells to the bare names, and the three frame lines to the new
header row's length:

```
explain select * from t1 where t1.c2 = 5 or exists (select 1 from t2 where t1.c1 = t2.c1);
Query Plan
===================================================
|ID|OPERATOR           |NAME|EST.ROWS|EST.TIME(us)|
---------------------------------------------------
|0 |UNION ALL          |    |#       |#           |
|1 |├─TABLE FULL SCAN  |t1  |#       |#           |
|2 |└─MERGE JOIN       |    |#       |#           |
|3 |  ├─TABLE FULL SCAN|t2  |#       |#           |
|4 |  └─TABLE FULL SCAN|t1  |#       |#           |
===================================================
```

**How column alignment is handled.** When one build prints a wider estimate, the server widens that
EST column in every row, in the header row and in the three frame lines. The mask does not carry that
over: after masking, the two EST columns always have the width of their names, and the frame lines are
as long as the recorded ID, OPERATOR and NAME cells plus 8 + 12 + 6. The ID, OPERATOR and NAME cells,
with their padding, stay byte-exact, so a different ID, operator, tree prefix or alias shows, and so
does a different width of those columns, through the frame lines too. Nothing outside the tables
changes: the `Outputs & filters` lines (`rowset=` among them), the EXPLAIN BASIC tables, and the one
`Optimization Info` block among the 40 files (view_2, whose `table_rows:1` and similar lines are
estimates too) stay exact. On the 40 checked-in .result files the mask rewrites all 568 tables and
leaves none exact, and exactly 568 lines name both EST columns.

### The row-order mask: how a statement's rows are found in a recording

What mysqltest writes for one statement, as the checked-in .result files show:
- the echo: the statement as the .test holds it, then the delimiter. Under the default result format
  each line of a statement that spans lines loses its leading spaces (window_function.farm); under
  `--result_format 3` the lines keep them (fts_index.basic_dml). Under `--disable_query_log` there is
  no echo, only what the statement prints. `--replace_regex` and `--replace_result` apply to the echo
  as well.
- if the statement returns a result set: the column names joined by tabs, then one line per row, the
  values joined by tabs (`NULL` for NULL). Under `--result_format 4` mysqltest draws a box instead: a
  `+---+` border, the names line `| a | b |`, the border again, one `|` line per row padded to the
  widest value, and the border a third time; an empty result is the three header lines and the
  closing border (all 1,303 boxed results in the checked-in files have this shape). Under
  `--disable_result_log`, nothing after the echo. A value that holds a newline is printed as it is,
  so its row takes more than one line. The mask sorts lines, not rows. With several columns, such a
  row has lines with too few tabs, and the helper refuses the statement. In a one-column plain
  result, though, a second line of a value looks like a row, and sorting could hide a changed value
  by pairing halves of different values. So the confirmation step keeps a statement only when its
  row count on the reference equals its `rows` ("The helper that writes the row-order list", below).
- if the statement fails as expected: `ERROR <sqlstate>: <message>` in place of the result.
- then `Warnings:` and one `Level<TAB>Code<TAB>Message` line per warning, and `affected rows: N`
  under `--enable_info`.
- then what the next command writes: the next echo, a `--echo` line, a line like `result_format: 4`,
  and under `--result_format 4` also blank lines and `##` comment lines of the .test. `connection`,
  `connect` and `disconnect` write nothing (connection.result). A text that holds several statements,
  which needs another delimiter, is one query: MariaDB's mysqltest prints one echo and then each
  result set in turn. No candidate is such a text.

A plain result has no end marker, and a row of a one-column result looks like any other line: after
`c`, `1`, `2`, the line `select c from t2;` could be a third row as far as the text goes. So the
list does not leave the end to be read from the recording: each listed statement carries its row
count and a digest of the line that follows its rows in the reference output, and the mask checks
both, on each side:

1. It finds the listed echo: the `occurrence`-th place where one or more lines in a row, each with its
   whitespace collapsed to single spaces and joined by one space, equal the `statement` column.
2. The next line is the result header. If it starts with `ERROR `, the statement is not masked. If it
   is a `+---+` border followed by a `|` line and the same border again, the header is those three
   lines (a boxed result); otherwise it is the one line.
3. The rows are the next `rows` lines. The line after them must have the digest `next_sha256`, or the
   file must end there if the column says `eof`.
4. Only if steps 1 to 3 hold on both sides are the rows sorted, byte by byte, on both sides. If either
   side fails, neither is sorted, the reasons are printed under the case (`row-order: echo N of
   <digest> (test line L) not masked: left ...; right ...`) and the statement stays exact. If the
   two sides' rows differed before sorting and match after it, the statement is `reordered`, and a
   line under the case names it.

So the echo, the header, the row count and everything after the rows stay exact; only the order of
the listed rows is not compared. A side with more or fewer rows fails step 3, and the statement is
compared exactly. A list made from another reference whose row count is too large fails step 3 on
both sides instead of sorting lines that are not rows. Several listed statements in one case are
handled in list order, and rows that would overlap rows already sorted are left exact. The echo is
matched by its text, so the same SELECT on the lines after an `explain` line counts as an echo too;
the helper refuses such statements (below).

The runner does not read the .test. It trusts the list's `rows` and `next_sha256` to mark where the
rows are, so what keeps a statement with several result sets (a text holding several statements)
and a statement under `--disable_result_log` off the list is only the helper, which refuses both.

### The list format: migration/judge/lists/hash-order-selects.txt

The confirmation step writes it; it does not exist yet. UTF-8, one statement per line, eight fields
separated by tabs; lines starting with `#` and blank lines are skipped.

| Field | What it holds |
|---|---|
| `case` | the runner's case name, one of the configured cases |
| `occurrence` | which echo of this statement in the case's output, counting from 1 (one SELECT can run several times in a case) |
| `statement_sha256` | the first 16 hex digits of the sha256 of `statement` |
| `rows` | how many rows the reference prints |
| `next_sha256` | the first 16 hex digits of the sha256 of the line after the rows in the reference, or `eof` |
| `test_line` | the line of the .test where the statement starts; for readers, `compare` does not use it |
| `hash_operators` | the operators of the reference's plan that give the rows their order, as EXPLAIN prints them, such as `HASH GROUP BY` or `HASH JOIN, HASH DISTINCT`; the confirmation step fills it in |
| `statement` | the echo with its whitespace collapsed to single spaces, delimiter included |

`case`, `occurrence` and `statement_sha256` are the statement's key: they find the statement in every
recording of the case. The key survives reruns as long as the lines that look like the statement's
echo come out the same, which the .test decides for the echoes themselves. A result row whose text
equals the statement (sql_audit, the plan cache views, SHOW CREATE VIEW) is counted as an echo too;
if such a row comes and goes, the occurrence moves, the listed rows are not found where the list
says, and the statement stays exact. `rows` and `next_sha256` only check that the recording has the
reference's shape there.

`compare` refuses the whole list (exit 2) in these cases:
- a line does not have eight fields;
- a number is not a whole number (`occurrence` and `test_line` must be at least 1);
- `statement` is not already collapsed, or `statement_sha256` does not match it;
- `next_sha256` is neither 16 hex digits nor `eof`;
- `hash_operators` does not contain `HASH`, or has spaces at either end;
- a case, occurrence and digest appear twice.

Decision 6 masks only SELECTs whose order comes from hash output, so each line must name the hash
operators behind it. The helper leaves the field empty, so its draft is refused as it stands.

A sample from the helper's draft over the checked-in .result files (`ec522c0d2d248d53` is the
digest of the `--echo` line `================ order ================`; select_basic runs the same
SELECT three times; executor.basic's result is boxed, so its `next_sha256` is the digest of the
closing border):

```
intersect	1	570e82a949537e52	4	9eeaf054e1e45181	41		select c1 from t1 intersect select c1 from t4;
minitest	1	bc5b434e80eb9b3e	2	ec522c0d2d248d53	115		select distinct c2, c2+1 from t1;
select_basic	3	4e94a1a7355f16ab	0	c75bc4c33c229fc7	51		select * from db1.t1,db2.t1;
outer_join_where_is_null	1	ad041b2ae0829b56	1	eof	32		select t1.name, t2.name, t2.id from t1 left join t2 on (t1.id = t2.owner) where t2.id is null;
executor.basic	1	b85c5fba01761414	4	68a95de82a6ba629	45		select * from t_h5_int t1, t_refered as t2 where t1.a = t2.aa;
```

The last of them as the confirmation step would keep it. executor.basic runs under
`--explain_protocol 2`, and its checked-in .result prints this statement's plan right before the
statement (test_suite/executor/r/mysql/basic.result:1411-1425): a HASH JOIN under the PX
COORDINATOR.

```
executor.basic	1	b85c5fba01761414	4	68a95de82a6ba629	45	HASH JOIN	select * from t_h5_int t1, t_refered as t2 where t1.a = t2.aa;
```

### The helper that writes the row-order list

```
python3 -B migration/judge/harness/second-set/hash_order_list.py \
  --runner migration/judge/harness/second-set/mysqltest_for_seekdb.py --out /tmp/hash-order-draft.txt
```

It reads lists/hash-order-select-candidates.txt and, for each candidate, the case's .test and a
reference output: the checked-in .result files by default, or with `--result-dir DIR` a recording's
`DIR/<case>.result`, such as the reduced-init C++ recording that the C++-against-Rust runs compare
with. `--runner` names the runner whose echo matching and list parser it uses; the default is the
live runner, and until the patch is applied the helper stops with a message saying so. The output is
the list format with a header of `#` lines (the candidate file's sha256 and the reference used) and a
`# unresolved<TAB>case<TAB>test line<TAB>reason` line for each candidate it cannot place. Every line
it writes has an empty `hash_operators` field; with `HASH` in that field in its place, the line goes
through the runner's list parser before it is written.

The confirmation step (EXPLAIN on the reference, in each statement's place in its case) then, for
each line:
- deletes it when the statement's order does not come from hash output (an ORDER BY inside it, for
  example topk line 110 or intersect line 96, or a plan without a hash operator);
- deletes it when `select count(*) from (<statement>) x` on the reference, in the same place, does
  not return `rows`: then the listed lines are not one line per row (a value that holds a newline,
  for one), and the mask would sort something other than rows;
- fills in `hash_operators` from the plan for the lines it keeps.

It saves the rest as lists/hash-order-selects.txt (the `#` lines can stay), and the list's sha256 goes
into `HASH_ORDER_LIST_SHA256` in the runner for the sign-off. Until then `compare` refuses the draft
twice over: the empty field fails the parser, and the row-order mask has no pinned sha256.

How it places a candidate:
- It splits the .test much as the candidate list's own script does (`--` lines, `#` lines, `if`
  and `while` lines and braces, statements split at the current delimiter outside quotes and
  comments), follows `delimiter`, and tracks what `--disable_query_log`, `--disable_result_log`,
  `--vertical_results`, `--enable_metadata`, `--disable_column_names`, `--enable_sorted_result`,
  `--result_format` and `--explain_protocol` switch. It must find the candidate at its line with its
  text.
- `occurrence` is the candidate's place among the .test's statements with the same echo that run with
  the query log on, and the reference must echo that text exactly as many times.
- A boxed result ends at its closing border. For a plain result, it works out from the .test what
  writes the next line: it passes over commands that write nothing (`let`, `connection`, `--error`,
  `--replace_column`, `--disable_warnings` and the like) and takes the next echo, `--echo` text,
  `result_format: N` line, or the end of the file. The rows end where that line appears, or at
  `Warnings:` or `affected rows:` when that line comes right after the warnings. With several
  columns, every row must have the header's number of tabs.
- `--replace_result` and `--replace_regex` rewrite the next statement's echo as well as its result,
  so the helper cannot predict that echo. It gives up when either comes before the next output (the rows would
  otherwise run on to a later line that holds the unchanged text).
- After placing the rows of a plain result, it checks them once more: no row may read, with its
  whitespace collapsed, like the first line of any statement's echo in the .test or like any
  `--echo` text. Such a row means the rows ran past the result, or a row the helper cannot tell from
  the next output; either way it gives up.
- It gives up, with the reason, on a candidate that is sent with `send`, is an `eval` with variables,
  runs without the query log or under `--disable_result_log`, `--vertical_results`,
  `--enable_metadata`, `--disable_column_names` or `--enable_sorted_result`, holds several statements,
  sits in a block or has a same-echo twin in one, is echoed a different number of times than the
  .test runs it, or returns an error in the reference; and on a plain result followed by a block, a
  statement without the query log, an `eval` or `--echo` with variables, a statement under
  `--explain_protocol`, a `--replace_result` or `--replace_regex`, a `##` comment under any
  `--result_format` but 1, a blank line under any but 1 and 3, or any other mysqltest command.

On the checked-in .result files it places 596 of the 633 candidates, in 61 cases: 403 plain results
(233 with one column, 34 of them with two or more rows) and 193 boxed ones; 99 print no row, 297 one
row and 200 two or more. The 37 others:
- 16 in subquery.idx_with_const_expr_21_subquery_dilang run under `--enable_sorted_result`, so
  mysqltest already sorts them;
- 12 are followed by a `--replace_regex` before the next statement: delete.delete_from_mysql line
  202, func_group_1 lines 174, 200, 205, 210, 215, 220 and 225, join_equivalent_transfer line 65,
  join_many_table_single_field line 20, and join_star lines 34 and 55;
- 4 (subquery.subquery lines 492 and 519, subquery.subquery_sj_firstmatch lines 79 and 90) are
  echoed twice because the same SELECT also follows an `explain` line;
- 2 in geometry.geometry_type_mysql sit in an `if (0)` block;
- 2 in vector_index.vector_index_ivfflat_dml are `eval`s with variables under `--disable_query_log`;
- vector_index.vector_index_ivfflat_dml line 313 is a `WITH ... UPDATE`, which returns no rows.

The check of the rows against the .test's output lines refuses no candidate here. A statement that
prints no row or one row loses nothing by being masked; the confirmation step may drop it anyway.

### Validating a mask, C++ against C++

Before a mask is used against a Rust build (Decision 6 (b)), compare two C++ recordings of the same
cases with it. The recordings must hold every listed case, and the mask must have run on all of
them. Exit 0 alone does not show that: two identical C++ recordings also exit 0 when the mask
recognizes nothing. So the mask holds for those recordings when `compare` exits 0 and its `mask` line
shows:
- for `est`: `not_compared_cases=0`; `tables_left_exact_left=0` and `tables_left_exact_right=0`;
  `tables_masked_left` equal to `est_header_lines_left` and `tables_masked_right` equal to
  `est_header_lines_right`. Then every line naming both EST columns in the listed cases was the
  header of a table the mask rewrote.
- for `row-order`: `not_compared_cases=0` and `not_compared_statements=0`, so every listed statement
  was looked at; and `not_masked=0`, so each was found and masked on both sides.

The exact result is printed beside it, so a case that already differs between two C++ recordings is
named before any Rust recording is compared. At the final gate the masked classes run exactly once
more: the same `compare` without `--mask`.

The first such check of the EST mask (2026-09-25, offline, with this copy in a scratch tree that holds
the pinned plan-bearing.txt) compared the two full-init C++ recordings of the 40 plan-bearing cases
from action 9: /Users/colin/seekdb-dev/mysqltest-runs/00b/a9-rec-noflag (the reference built without
`-ffp-contract=off`) against a9-rec-flag (the flagged reference). The result was exit 0 with 40
identical exactly and masked, and `mask_changed=33`. The `mask est` line showed `listed_cases=40,
compared_cases=40, not_compared_cases=0`, and on each side `est_header_lines=568,
tables_masked=568, tables_left_exact=0`. The row-order mask has no confirmed list yet. With the
helper's draft, `hash_operators` filled with a stand-in and the pin set to match, the same pair gave
exit 0: 18 of the 61 listed cases compared, 253 statements masked, `not_masked=0` and
`reordered=0`.
The reduced-init pair a10-rec1 against a10-rec2 gave exit 0 with 22 cases and 91 statements, all
masked. Neither pair holds every listed case, so neither validates the row-order mask.

### What `compare` does with them

| Manifest key | Kind | Value when the manifest has no such key |
|---|---|---|
| `seekdb_parameters` | must match | empty list |
| `ps_protocol` | must match | false |
| `compress` | must match | false |
| `plan_cache_stats` | must match | false |
| `plan_cache_read` | must match | none |
| `test_dir_sha256` | must match | none |
| `test_dir` | note; with `--mask`, a value on either side stops `compare` (exit 2) | none |

When both recordings have `plan_cache_stats`, `compare` also reads both plan_cache.tsv files and
reports each case as identical, different or missing (`plan-cache <status> <case>` lines, a
`plan cache: cases=..., identical=..., different=..., missing=..., different_timing_sensitive=...`
line, and `plan_cache` in the `--out` JSON, with each case's seconds and its `timing_sensitive`
flag). A different or missing case fails the comparison (exit 1). A recording with
`plan_cache_errors`, or with `plan_cache_stats` but no passed read check on every instance in
`plan_cache_checks`, counts as a recording problem. `--require-plan-cache`, `--require-ps-protocol`
and `--require-compress` make a recording without that option a recording problem; the `--out` JSON
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

The masks, `--compress` and the helper were checked the same way on 2026-09-25, and again after the
two reviews that day, with nothing started. Both files compile (without writing bytecode), and
`run --help`, `compare --help` and the helper's `--help` print. `compare` exits 2 for `--mask` with an
unknown name, and for `--mask row-order` while no list is pinned. 27 in-process tests loaded the copy
and the live runner from a scratch copy of the tree. The copy holds tools/deploy and
migration/judge/lists, so the pin and the configured-case check run as they would in the worktree.
sdb.py, mysqltest, obclient and git were answered by stand-ins. The tests cover:
- EST:
  - a table whose EST.ROWS and EST.TIME(us) are 9 and 14 digits wide on one side is `different`
    exactly and `identical` masked, and the comparison exits 0;
  - a case outside plan-bearing.txt with a changed EST number keeps its exact verdict and fails;
  - a changed operator, alias, alias width, `rowset=`, ID or tree depth stays different;
  - `more than 1.0e19` is masked; a malformed cell or frame is left exact; an EXPLAIN BASIC table is
    untouched;
  - a header row the mask does not take (`| ID|...`) stays exact and counts in
    `est_header_lines` only;
  - the `masked (est)` line gives the exact result of the masked cases beside their masked result;
  - on the 40 checked-in .result files, the mask rewrites 568 tables, leaves none exact, finds 568
    lines naming both EST columns, and changes only lines of those tables. It gives the same bytes
    when every EST value is redrawn at random widths (1 to 15 digits, some `more than 1.0e19`), and
    different bytes when one letter of an OPERATOR cell changes.
- Row order:
  - rows in another order are masked on both sides for results with several columns, one column, a
    box, and rows at the end of the file;
  - a reordered first echo when the second is listed, a reordered unlisted statement, a changed
    value, a row more or fewer, a changed header, a changed next line and an error in place of the
    result all stay different;
  - a list whose row count is too large masks nothing; each kind of malformed list line (among them
    an empty `hash_operators`, one without `HASH`, one with a leading space, and a ninth field) and a
    repeated key are refused; both masks work together;
  - `reordered` counts only statements whose order differs between the two sides: a recording
    against itself gives 0, though sorting rewrote unsorted rows (`mask_changed`); each reordered
    statement gets a line under its case;
  - the helper's 596 lines, with a stand-in in `hash_operators`, applied to the checked-in .result
    files with every listed block shuffled on one side: all masked `identical`, `different` exactly
    where the order changed, and `reordered` equal to the number of blocks the shuffle moved. One
    swap of two lines outside the listed rows in each case makes all of them `different`.
- The review fixes:
  - `--mask` exits 2 when either recording was made with `--test-dir`;
  - an edited plan-bearing.txt (a case added, a comment added, a case removed) exits 2, and the
    pinned file passes;
  - `--mask row-order` exits 2 without a pin and with a wrong one;
  - list names that are not configured cases exit 2: a misspelled name, a dotted name, and
    `update.update2`, which is tracked but not configured;
  - the statistics count the listed cases and statements that were not compared, and name those
    cases in the JSON;
  - the helper's draft, whose `hash_operators` is empty, exits 2.
- `--compress`:
  - a default run gives the same mysqltest and sdb.py commands and the same seekdb_result.json and
    manifest.json values as the live runner, apart from `runner_sha256`, with only new keys added;
  - `--compress` puts `--compress` just before `--record` (after `--ps-protocol` when both are
    given) and records `compress: true`, and the obclient commands never get it;
  - `compare` refuses a mix, counts a missing key as false, and `--require-compress` flags
    recordings without it; the help text says only what the option asks for.

7 more tests ran the helper:
- 4 on a made-up tree with two .test and .result pairs written for it: each way it ends a plain
  result (the next echo, a `--echo` line, `Warnings:`, the next statement under `--error`, no rows,
  the end of the file), a boxed result, a second echo of one statement, 12 of the reasons it gives
  up, and its output, with `hash_operators` filled in and the pin set, masking a shuffled copy;
- 3 on single made-up cases:
  - `--replace_result`, `--replace_regex` and `replace_result ...;` before the next statement make it
    give up, and `--replace_column` does not;
  - with the replace check switched off, the check of the rows still refuses the case with the
    rewritten echo;
  - a row equal to an `--echo` text or to the first line of a two-line statement is refused, and a
    row that differs is not;
  - a draft line has an empty `hash_operators`, which the parser refuses, and it parses once the
    field is filled in.

44 bugs put into temporary copies of the runner (30) and the helper (14) each failed at least one
test. The 28 from the first round include frame lines not checked, the recorded EST widths kept, the
exit status following only the masked cases or only the exact result, the next line not checked, the
first echo always taken, a boxed header taken as one line, one side sorted, echoes matched without
collapsing whitespace, `compress` dropped from the must-match keys, the manifest or the mysqltest
command, unknown masks accepted, and in the helper `--echo` treated as silent, warnings not ending
the rows, and blocks and count mismatches accepted. The 16 for the review fixes are:
- in the runner: masks allowed for `--test-dir` recordings, the pinned sha256 not checked, a mask
  without a pin accepted, unknown case names accepted, the exact counts of the masked cases taken
  from the masked result, header lines counted only where the mask takes them, `reordered` with its
  old meaning, `hash_operators` not required, the statements of cases not compared left uncounted,
  missing cases counted as compared, and reordered statements not printed;
- in the helper: replace commands passed over, the check of the rows switched off, `--echo` texts
  or the first lines of multi-line statements left out of that check, and the draft filling in
  `hash_operators`.

The 34 tests of families/concurrency/offline_test.py pass with the patched runner, each in a scratch
tree made a git repository so the recordings identify tools/deploy; in the first round they also
passed with the live runner.
