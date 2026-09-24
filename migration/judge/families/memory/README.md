# Family 12: memory budgets

PLAN.md section 4, family 12. `memory_scenarios.py` here drives each memory budget owner that a
client can reach into failure on a fresh seekdb instance, records what the client sees, and writes
the recording in the runner's format, so the C++ reference and the Rust build are compared byte for
byte with the runner's `compare` subcommand. It follows `migration/judge/harness/restart_scenarios.py`
(restart-scenarios.md): the same command line, the same lifecycle through sdb.py, the same manifest,
`.result` and `.partial` files.

## What the family checks

| PLAN.md budget owner | Scenario | What the client sees |
|---|---|---|
| -4013 from the clog allocator | none | cannot be reached from outside (below) |
| -4030 memstore full | `memstore_below_reserve`, `memstore_fill` | `ERROR 4030 (HY000): Server runtime memory limit exceeded` on INSERT, UPDATE and DELETE; memstore_fill also checks that writes start being refused where the 100 MB reserve begins |
| -11049 from the query tracker | `query_memory_limit` | `ERROR 11049 (HY000): Exceed query memory limit (mem_limit=10737418, mem_hold=...)` |
| -7603 from the vector limit | `vector_limit` | -7603 cannot reach a client (below); the vector limit itself is reached, and from the source the client should see `ERROR 4013 (HY001)` (to be confirmed live) |
| -4013 from the micro block cache after its retries | none | cannot be reached from outside (below) |
| -4013 from hash-join partition depth | `hash_join_depth` | `ERROR 4013 (HY001): No memory or server runtime memory limit reached`, and `V$SQL_WORKAREA` then shows that the join's work area ran multi-pass |
| spilling with `ob_sql_work_area_percentage=5` | `work_area_spill` | the same result as without the limit; the spill shows in `V$SQL_WORKAREA` |

Every scenario then shows that the server still answers (a recorded query after the failure, and the
pid check at the end), and where the source makes it safe, that the budget works again once it is
restored.

## Command line

```
python3 migration/judge/families/memory/memory_scenarios.py \
  --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
  --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --base-dir /Users/colin/seekdb-dev/mysqltest-runs/<name>/base \
  --record-dir /Users/colin/seekdb-dev/mysqltest-runs/<name>/rec \
  --port 3891 \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql

python3 .github/script/seekdb/mysqltest_for_seekdb.py compare --left <rec A> --right <rec B>
```

The options are restart_scenarios.py's: `--seekdb`, `--obclient`, `--base-dir` (new or empty),
`--record-dir` (new or empty), `--port` (required; use 3891-3899, 3881/3882 are the judge's),
`--init-sql`, `--init-user-sql`, `--scenario NAME` (repeatable; scenarios always run in the fixed
order below) and `--save-instance-dir DIR` (defaults to `$SEEKDB_COV_PROFRAW_DIR`; copies `log/`
before every destroy). Exit 0 means every requested scenario ran and all its checks held; 1 means
anything else, including a stop by signal; argument errors exit 2.

To compare two builds, record each with the same obclient, init files and repository state, then run
`compare`. It refuses a recording that is unfinished, has failed scenarios or an error, or was made
with a different obclient, init file, sdb.py or tools/deploy; a different seekdb binary is reported
as a note. Exit 0 means every scenario is identical.

## How a scenario runs

1. **Start** a new instance: `sdb.py start --binary --base-dir --port --nodaemon --parameter
   memory_budget=1G --parameter cpu_count=4`, `sdb.py wait-ready`, then init (the runner's own
   `execute_init_sql`), exactly as restart_scenarios.py does. The parameters are the first recorded
   line after the header.
2. **Common setup**, recorded: `set global ob_query_timeout = 900000000` (the default is 10 s,
   src/share/system_variable/ob_system_variable_init.json:1481-1485, and a slow step must end in the
   budget error, not in a timeout), a check that a new session gets it, and a 1,000-row number table
   `t_seq`.
3. **The scenario's SQL.** Each step is one obclient session (`obclient -h 127.0.0.1 -P <port> -uroot
   -A -c -Dtest --table`, statements on stdin); a statement that must fail is sent alone.
4. **Stop by kill:** the script takes the pid through sdb.py's own instance check and sends SIGKILL
   (restart_scenarios.py's `kill_process`, Decision 9), then saves `log/` if asked and runs `sdb.py
   destroy`. The next scenario starts on a destroyed base dir. If the kill fails (the pid was gone
   when SIGKILL was sent, SIGKILL could not be sent, or the process did not exit within 20 s), the
   destroy still runs, and the failure is a run error: the scenario is written as `.partial` and the
   later scenarios are not run.

**Stopping the script.** SIGTERM and SIGINT are handled alike. The first one, if it arrives while a
scenario is starting its instance, running its SQL or waiting, interrupts that step; the scenario
then kills and destroys its instance as above and is written as `.partial` with the problem
`<scenario>: stopped by signal N`. If it arrives at any other time (while an instance is killed,
saved or destroyed, while a recording or the manifest is written, or between scenarios), it is held
until that step is done, and the scenario keeps its result. Either way no further scenario starts:
the rest are marked "not run after an earlier error", the manifest is finished with the error
`stopped by signal N` (prefixed with the scenario's name when one was interrupted), and the exit
code is 1. Later signals are ignored. This matters because sdb.py starts seekdb in its own session
(`start_new_session=True`, .github/script/seekdb/sdb.py:134-143), so a signal sent to the script
does not reach seekdb: without the handler, a stopped script would leave a 1G-budget seekdb running
on the port. SIGKILL cannot be handled; after one, run `sdb.py destroy --base-dir <base dir>`.

`memory_limit` is not used: at 834bbee1e it is a deprecated parameter that is accepted and ignored
(src/share/parameter/ob_parameter_seed.ipp:81-85), and `system_memory` no longer exists (an unknown
`--parameter` name is ignored silently, src/share/config/ob_common_config.cpp:193-196). The budget
that sizes the server is `memory_budget` (ob_parameter_seed.ipp:75-80, minimum 1G): with 1G the
memstore limit is 512 MB (50%, src/share/config/ob_server_config.cpp:50-56 and :218-223), the KV
cache 40%, the SQL work areas `ob_sql_work_area_percentage` of it, and the query limit
`query_memory_limit_percentage` of it. Scenario-specific parameters are set with `ALTER SYSTEM SET` or
`SET GLOBAL` inside the scenario, so they are part of the recording. `ALTER SYSTEM SET` saves the value
and reloads the configuration before it returns (src/rootserver/ob_system_admin_util.cpp:122-142,
src/observer/ob_server_reload_config.cpp:63 and :156).

**Order.** work_area_spill, query_memory_limit, hash_join_depth, memstore_below_reserve,
memstore_fill, vector_limit. Every scenario runs on its own instance and nothing carries over, so a
failure cannot leak through server state; a failed start, init, kill or destroy is a run error that
stops the run, as in the runner. Within that, the order runs the scenario with no error first (if it
fails, the sizes or the work-area timer are not what the family assumes), keeps the per-query errors
before the ones that change server-wide limits, puts the scenario that holds the most memory
(memstore_fill) late, and puts last the one whose failure path runs through a third-party library
(vsag), since a crash there is the likeliest.

## The recording

**manifest.json** has restart_scenarios.py's keys (the runner's keys, so `compare` accepts it), with
`recorder` naming this script, `runner_sha256` its sha256, `cases` the scenario names, `max_retries`
0, `fresh_instance_per_case` true, plus `server_parameters`. `outcomes` gives per scenario the exit
code, the problems, and two diagnostics that are never compared: `elapsed_seconds` and
`peak_rss_kb` (the server's resident set size, sampled with `ps` every 2 s).

**`<scenario>.result`** (or `.partial` when a check failed) holds, in order: `-- <scenario>`, `--
recorder sha256 <sha256 of memory_scenarios.py>`, the start line and `-- start, ready, init`; then
every statement as sent followed by obclient's `--table` output. For a statement that must fail, the
error as the client sees it: `ERROR <code> (<SQLSTATE>): <message>`, without obclient's `at line N`,
then `-- check: the statement failed with error <code>: yes`. Waits are recorded as `-- waited N
seconds: <why>`. A failure the script did not expect records obclient's stderr and exit code and
stops the scenario, and the next scenario runs.

**obclient's stderr for a statement that must fail** has to be exactly one line, the ERROR line.
Anything else fails the scenario and puts the last 20 lines of stderr into the `.partial`: a second
line of a multi-line message, the time-and-trace line that `enable_rich_error_msg` appends
(src/observer/mysql/obmp_packet_sender.cpp:594-639; off by default,
src/share/parameter/ob_parameter_seed.ipp:122-125), or a client warning. So a build whose message
matches on its first line but carries more lines cannot record like the reference.

**What is not recorded, and the rule used instead.** Everything recorded is what two runs of the same
binary reproduce exactly. Named exceptions, each also named in the script:

| Value | Where | Why it is left out | What is compared instead |
|---|---|---|---|
| `mem_hold` in the -11049 message | query_memory_limit | the request's memory hold at the check that failed; it follows allocator block sizes and thread-local page caches (src/oblib/lib/rc/context.h:386-391, :600-612), so it may differ between two runs of one binary, and a port cannot be expected to reproduce allocator internals | recorded as `mem_hold=<not recorded>`; check that it is at least `mem_limit` (the condition at ob_memory_tracker.cpp:46-47). The rest of the message, including `mem_limit=10737418`, is exact |
| `memstore_used` before the limit is set | memstore_below_reserve, memstore_fill | the memstore the inner tables hold after bootstrap and init varies | none in memstore_below_reserve (printed for the live check); in memstore_fill the limit is computed from it |
| the `memstore_memory_limit` computed from it | memstore_fill | follows the value above | the statement is recorded as a comment naming how n is computed |
| how many chunks were accepted before the refusal | memstore_fill | depends on the memstore used above, on 2 MB page steps and on writes by inner tables | checks: at least 6 were accepted, and one was refused within 128 |
| `memstore_used` and `memstore_limit` read right after the refusal | memstore_fill | follow the values above | checks: `memstore_limit` is the n MB set, and `memstore_used` is above `memstore_limit` minus 100 MB by at most 8 MB |
| the 200,000 lines of the sort query | work_area_spill | not variable, only long | the line count and sha256 of the exact output; still an exact comparison |

The values left out are printed on the run's stdout as `not recorded: <scenario> <name>=<value>`, so
the live check can see whether and how much they vary. No mask is added (Decision 6).

## work_area_spill

`t_spill (id, k, pad)`: 200,000 rows, `pad` a distinct 108-character string (an 8-digit key from a
permutation of the row number, then 100 characters), about 26 MB.

1. `set global ob_sql_work_area_percentage = 5` (the default, src/share/system_variable/
   ob_system_variable_init.json:1798-1808), wait 10 s.
2. Three queries, each followed by the `V$SQL_WORKAREA` probe:
   - sort: `select id from t_spill order by pad, id` (recorded as line count and sha256);
   - hash group by: 200,000 groups under `use_hash_aggregation`, reduced to one row of counts, sums
     and `sum(crc32(g))`;
   - hash join: a self-join on `pad` under `leading(a b) use_hash(b)`, reduced to one row.
3. `set global ob_sql_work_area_percentage = 100`, wait 10 s, the same three queries and probes.
4. `select count(*) from t_spill`.

The probe, recorded: `select operation_type, sum(total_executions), sum(optimal_executions),
sum(onepass_executions + multipasses_executions), max(max_tempseg_size) > 0 from
oceanbase.V$SQL_WORKAREA where sql_id in (select sql_id from oceanbase.V$OB_PLAN_CACHE_PLAN_STAT where
query_sql like '<query suffix>' or statement like '<query suffix>') group by operation_type,
operation_id order by operation_id`. The statistic that shows the spill is `V$SQL_WORKAREA`'s
ONEPASS_EXECUTIONS and MULTIPASSES_EXECUTIONS (with MAX_TEMPSEG_SIZE): an execution counts as optimal,
one-pass or multi-pass by its number of passes when the operator closes
(src/sql/engine/ob_sql_memory_manager.cpp:585-620, collected at :715-785; the view at
src/share/inner_table/ob_inner_table_schema_def.py:7831-7865 over
`__all_virtual_sql_workarea_history_stat`, :4724-4751). The suffix match cannot match the probe
itself, whose text does not end with it.

Why these sizes: the work-area total is `memory_budget / 100 * ob_sql_work_area_percentage`
(ob_sql_memory_manager.cpp:807-808) and one operator's bound at most an eighth of it (:276), so with
1G one operator gets at most 6,710,886 bytes at 5% and 134,217,725 at 100%. The bound is recomputed
by a 3 s timer (src/observer/ob_server_duty_task.h:43-50, ob_server_duty_task.cpp:87-103), hence the
10 s waits. Each operator here needs about 26-40 MB.

Checks, per query: after the 5% run, the probe lists the query's work-area operators each with one
execution, and at least one has a spilled (one-pass or multi-pass) execution; after the 100% run, the
output is byte-identical to the 5% run, and every operator's new execution was optimal (executions
+1, optimal +1, spilled unchanged).

## query_memory_limit

`t_qm (id, pad)`: 200,000 rows with distinct 158-character `pad`, about 34 MB.

`set global ob_sql_work_area_percentage = 100`, wait 10 s; `alter system set
query_memory_limit_percentage = 1`; then `select count(*), sum(c) from (select /*+
use_hash_aggregation */ pad, count(*) as c from t_qm group by pad) x` must fail with 11049 (the
outer `sum(c)` keeps the grouping from being rewritten into a distinct count). Then `select
count(*) from t_qm` (the server answers), `alter system set query_memory_limit_percentage = 32`
(the default, ob_parameter_seed.ipp:1108-1110), and the same query succeeds with 200000 groups.

Source: the limit is `memory_budget / 100 * query_memory_limit_percentage`
(src/sql/executor/ob_memory_tracker.cpp:29-35), 10,737,418 bytes here; it is compared with the
request's memory-context tree (:36-55; -11049 at :48, the message at :51 and
src/share/ob_errno.def:1880). The context and the tracker are per request
(src/observer/omt/ob_th_worker.cpp:238-245); the check runs in `ObExecContext::check_status`
(src/sql/engine/ob_exec_context.cpp:679), which operators reach every few calls
(src/query/api/query/engine/ob_operator.h:716-721). The hash group by's memory context is a child of
the request's (src/sql/engine/aggregate/ob_hash_groupby_op.cpp:403-414), so its hash table counts.
The work area goes to 100% because at 5% the group by would spill at 6.7 MB and stay below the
10.7 MB limit. Checks: the error is 11049; the message names `mem_limit=10737418`; the
unrecorded `mem_hold` is at least that.

## hash_join_depth

`t_hj_build (id, k, pad)`: 150,000 rows, every `k` = 1, `pad` 150 characters. `t_hj_probe`: (1, k=1)
and (2, k=2).

At `ob_sql_work_area_percentage = 5` (wait 10 s):

1. `select /*+ leading(a b) use_hash(b) */ count(*), sum(length(a.pad)) from t_hj_build a full outer
   join t_hj_probe b on a.k = b.k` must fail with 4013.
2. The probe for that query, recorded exactly: `select operation_type, total_executions,
   optimal_executions, onepass_executions, multipasses_executions, last_execution, max_tempseg_size >
   0 as dumped from oceanbase.V$SQL_WORKAREA where sql_id in (select sql_id from
   oceanbase.V$OB_PLAN_CACHE_PLAN_STAT where query_sql like '<pattern>' or statement like
   '<pattern>') order by operation_id`, with the pattern `%leading(a b) use_hash(b)%from t_hj_build a
   full outer join t_hj_probe b on a.k = b.k`, which matches neither the probe nor the join in step 4.
   Checks: one work area with one execution that wrote to temp files, and that execution was
   multi-pass (MULTIPASSES_EXECUTIONS 1, LAST_EXECUTION `MULTI-PASS`).
3. `select count(*) from t_hj_build` (150000).
4. The same join with the small table as the build side (`leading(b a) use_hash(a)`), which succeeds
   with 150001 and 22500000.

Both joins read `a.pad`, so every build row carries its pad: about 210 bytes a row (row header, hash
value and next pointer, `k` and the 150-character `pad`), about 30 MB in all, several times the
6.7 MB bound. The probe row with k = 2 has no match, so the join in step 4 returns 150001 rows, and
the sum of the pad lengths is 150,000 x 150. If the hints were ignored and the optimizer built on
the small table, the failing query would instead succeed quickly, and the check on its error would
say so.

Source: the check at src/sql/engine/join/ob_hash_join_op.cpp:838-843 returns -4013 when the batch it
fetches has a `part_shift_` of 64 bits or more. `part_shift_` starts at `MAX_PART_LEVEL << 3` = 32
(:168, :517; ob_hash_join_op.h:1106), and each partitioning adds `min(ctz(part_count), 8)` bits to
the batches it writes (:1697), with at most 128 partitions per level (:200, :3226), so at most 7
bits. The paths with a level cap stop at `MAX_PART_LEVEL` (:1509, :1536-1539): 32 + 4 x 7 = 60,
below 64, so `_enable_hash_join_processor` cannot reach the check. The path without a cap: below the
top level, when a dumped build partition does not fit in memory and recursing is not chosen (skew,
:1504-1506, or level 4), the join falls back to nested loop, but refuses it when the join type needs
a bitset of matched right rows (right outer, full outer, right semi, right anti;
ob_hash_join_op.h:967-971) and recurses again with no level limit (ob_hash_join_op.cpp:1512-1520).
With every build row on one key, every level puts all rows into one partition again, which never
fits the bound, so the recursion goes on until the shift reaches 64.

How deep that is: below the top level, the partition count is `calc_partition_count_by_cache_aware`
(:1243-1260) of the rows on disk, all 150,000 (:1378): `next_pow2(150000 / (l2_cache_size / 48))`,
48 bytes being a fixed price per row (:199). On macOS the L2 size is the 512 KB fallback, because
`get_level2_cache_size` reads a Linux sysfs path (src/oblib/lib/utility/utility.cpp:2263-2289,
:2317-2328; `INIT_L2_CACHE_SIZE`, src/sql/engine/aggregate/ob_adaptive_bypass_ctrl.h:28). So each
level makes next_pow2(150000 / 10922) = next_pow2(13) = 16 partitions, 4 bits. The top level uses
the optimizer's row estimate instead (:1360-1369): 16 partitions for an estimate between 98,298 and
185,673 rows, 8 below that. With 16 at the top, the shift is 36 after it and grows by 4 per level,
so the batch fetched for the 8th level has shift 64 and fails (the 9th level with 8 at the top).
Each level writes the 30 MB build side once and the next level reads it back: about 240-270 MB
written in all, at most about 60 MB on disk at a time.

What the probe shows: when the join dumps a partition, its profile's pass count becomes the level
plus one (ob_hash_join_op.cpp:1903). The profile is collected into `V$SQL_WORKAREA` when the
operator closes (`inner_close`, :872-875; ob_sql_memory_manager.cpp:715-779). That happens on the
error path too (`ObOperator::close` calls `inner_close` whatever happened,
src/sql/engine/ob_operator.cpp:800-830) and before the error packet is sent
(src/observer/mysql/ob_sync_plan_driver.cpp:109 closes, :206 sends), so the probe sees the failed
execution. 0 passes counts as optimal, 1 as one-pass, more as multi-pass
(ob_sql_memory_manager.cpp:598-606). MULTI-PASS therefore shows that the join dumped again below the
top level before the error, and a 4013 raised before that (for example an allocation failure while
building the first hash table) records differently. The number of passes itself (8 or 9 here) is not
visible: LAST_EXECUTION holds only `OPTIMAL`, `ONE PASS` or `MULTI-PASS`
(src/sql/engine/ob_sql_memory_manager.h:255-257, printed by
src/observer/virtual_table/ob_all_virtual_sql_workarea_history_stat.cpp:172-185), and the plan
monitor's statistics for the hash join describe its hash table, not the depth
(ob_hash_join_op.cpp:2640-2651). So the recording does not pin how many levels ran; only the planned
mutation ties the error to :843.

## memstore_below_reserve

`t_ms (id, pad)` with one row written first. `alter system set writing_throttling_trigger_percentage
= 100` (no write throttling); the script reads `memstore_used` (printed, not recorded); `alter system
set memstore_memory_limit = '64M'`; then an INSERT, an UPDATE and a DELETE, each after a 1 s wait,
must each fail with 4030; `select id, pad from t_ms` still shows the first row; `alter system set
memstore_memory_limit = '0M'` (back to 50% of the budget), wait, an INSERT succeeds and the table
shows both rows.

Source: -4030 comes from `ObAccessService::check_write_allowed_`
(src/storage/tx_storage/ob_access_service.cpp:655-657, via :181-192), called by every DML entry point
(:909, :950, :991, :1034, :1080, :1123), for tablets that are not inner tablets. For a user request
the memstore counts as full when its quota used exceeds `memstore_limit - 100 MB`
(src/storage/tx_storage/ob_memstore_freezer.cpp:1075, :1083; the reserve at
ob_memstore_freezer.h:126). With a 64 MB limit that is always true, so every user write is refused
from the first one, whatever the inner tables hold. The answer "not full" is kept per worker thread
for 100 ms (ob_memstore_freezer.cpp:1070-1073; ob_memstore_freezer.h:127), hence the 1 s waits.
`memstore_memory_limit` is at ob_parameter_seed.ipp:92-96, applied through
`ObMemstoreFreezer::reload_config` (ob_memstore_freezer.cpp:1222-1242).

This scenario shows the refusal on each kind of DML and does not depend on any value read from the
server. It pins the reserve only when the inner tables hold less than 64 MB: a server with no reserve
would refuse too if they already held more. The printed `memstore_used` shows which case the
reference is in; memstore_fill pins the reserve in either case.

## memstore_fill

`t_fill (id, pad varchar(1000))`. `writing_throttling_trigger_percentage = 100`, then
`freeze_trigger_percentage = 99` (in this order: the freeze trigger must stay below the throttle
trigger, src/share/config/ob_config_helper.cpp:69-84, checked at
src/rootserver/ob_local_management_service.cpp:2524-2527).

1. Read `memstore_used` from `oceanbase.__all_virtual_memstore_info` (not recorded) and set
   `memstore_memory_limit` to it, rounded up to MB, plus 100 MB plus 16 MB, so that 16-17 MB of
   writes separate the memstore from the refusal.
2. Chunks of 1,000 rows of 1,000 bytes, each after a 0.2 s pause, until one is refused (at most 128).
   The refusal must be 4030.
3. Right after the refusal, read `memstore_used` and `memstore_limit` again (not recorded).
4. Checks: at least 6 chunks were accepted; `memstore_limit` is the n MB set in step 1; and
   `memstore_used` is above `memstore_limit - 100 MB`, by at most 8 MB.
5. After 1 s, an UPDATE must also fail with 4030 (no memstore freeze starts, since its trigger is 99%
   of the limit); `select count(*) > 0` shows the server answers; `memstore_memory_limit = '0M'`,
   wait, and an INSERT succeeds.

Source: the virtual table reports the same quantity the full check compares, read fresh
(src/observer/virtual_table/ob_all_virtual_memstore_usage.cpp:57-83 calls `get_memstore_condition`,
whose `force_refresh` defaults to true, ob_memstore_freezer.h:192-197; `memstore_used` is
`memstore_quota_used_` and `memstore_limit` is the configured limit, ob_memstore_freezer.cpp:950-955).
That quota is the memstore arena's hold (ob_memstore_freezer.cpp:988-1008,
src/storage/allocator/ob_memstore_allocator.h:164-165), which grows a page of just under 2 MB at a
time (`ALLOC_PAGE_SIZE`, src/storage/allocator/ob_fifo_arena.h:160; `OB_MALLOC_BIG_BLOCK_SIZE`,
src/oblib/lib/ob_define.h:1307; one page in use per group of memtables, ob_memstore_allocator.cpp:47).
The limit is set in MB of 2^20 bytes (src/oblib/lib/utility/utility.cpp:2419-2420) and used as given
(ob_server_config.cpp:50-56).

Why the checks hold on the reference and what they pin:
- Eight new pages are 15.9 MB, less than the 16-17 MB margin, so the refusal comes once a ninth page
  is taken. At about 1.3-1.4 MB of memstore per chunk (1,000 bytes of data plus the row and index
  overhead per row), that is about 12-13 chunks, fewer if inner tables take pages meanwhile; at least
  6 leaves room for that and fails a server that counts about three times the memory per row.
- The refused chunk's check saw the quota above `memstore_limit - 100 MB`, and the quota only
  shrinks when a frozen memtable is released; the freeze trigger here, 99% of the limit, is far above
  the quota. So right after the refusal the quota is above that line, by less than one page plus
  whatever inner tables took meanwhile: about 1-2 MB on the reference; the check allows 8 MB.
- The 0.2 s pause before each chunk is longer than the 100 ms a worker thread keeps a "not full"
  answer, so every chunk's first write is checked again, and at most one chunk is written past the
  line.
- A server whose reserve is R instead of 100 MB refuses when the quota passes `memstore_limit - R`,
  so the distance read in step 3 is about 100 - R MB: the check fails for a reserve below about
  92 MB or above about 102 MB (and a reserve above 116 MB also refuses the first chunk). The count
  check is too loose to pin how much memstore a row takes; it catches a server that counts three
  times as much or more.

## vector_limit

`t_vec (id, v vector(16))` with `vector index vidx(v) with (distance=l2, type=hnsw, lib=vsag)`; 10 rows
inserted at the default limit (this creates the in-memory incremental index); `alter system set
vector_memory_limit = '1K'`, wait 1 s; then an INSERT of 20,000 rows must fail with 4013;
`select count(*) from t_vec` still shows 10. The vectors are built in SQL (`concat('[', n % 7, ',',
... , ']')`), so the statement stays short.

Source: the vector limit is `vector_memory_limit` (ob_parameter_seed.ipp:97-102; 0 means 50% of
physical memory, ob_server_config.cpp:225-232), read at every checked allocation
(src/storage/allocator/ob_vector_allocator.cpp:137); an allocation is checked after every 20
successful ones or when it is 2 MB or larger (ob_vector_allocator.h:36-37, .cpp:135-146), and over
the limit the allocator returns a null pointer (:142-155). The usage it compares includes every vsag
allocation (ob_vector_allocator.cpp:62;
src/observer/vector_index/ob_plugin_vector_index_service.cpp:836). DML on a table with an HNSW index
adds the vectors to the incremental vsag index inside the statement
(src/storage/ls/ob_ls_tablet_service.cpp:3255, src/observer/vector_index/
ob_plugin_vector_index_adaptor.cpp:1515-1532), and that index allocates through the checked context
(ob_plugin_vector_index_adaptor.cpp:783-797). seekdb adds the rows with `index_->Add`
(src/oblib/lib/vector/ob_vsag_adaptor.cpp:245), and `type=hnsw` creates vsag's HNSW index
(ob_vsag_adaptor.cpp:526-531, :816-823). In the vsag that seekdb links
(deps/3rd/usr/local/oceanbase/deps/devel/ lib/vsag_lib/libvsag.dylib, from
devdeps-vsag-1.1.0-20260107), checked by disassembly: `vsag::SafeAllocator::Allocate` throws
`std::bad_alloc` when the wrapped allocator returns null, and `vsag::HNSW::Add` has a handler that
logs "not enough memory: " and returns error type 11, `NO_ENOUGH_MEMORY`
(deps/3rd/usr/local/oceanbase/deps/devel/include/vsag/errors.h:23-45). ob_vsag_adaptor.cpp:61-63
maps that to -4013.

**-7603 cannot reach a client.** `OB_ERR_VSAG_MEM_LIMIT_EXCEEDED` is assigned in one place,
ob_vector_allocator.cpp:142, to a local `ret` inside `ObVectorMemContext::alloc`, which returns only
the pointer; the one other mention compares a background task's error code
(src/observer/vector_index/ob_plugin_vector_index_scheduler.cpp:733). So this scenario records the
4013 the client does see when the vector limit is hit.

## Budget owners that cannot be reached from outside

**-4013 from the clog allocator.** `ObLogAllocator::set_limit` caps the clog block allocator at
`total_limit / 100 * 30` (src/logservice/ob_log_allocator.cpp:248-259; CLOG_MEM_LIMIT_PERCENT at
ob_log_allocator.h:87). `total_limit` is the runtime memory size
(src/logservice/ob_log_allocator_mgr.cpp:170-180), which is `memory_budget`
(src/share/resource/ob_server_resource.cpp:749), at least 1G (ob_config_helper.cpp:452-460), so the
cap is at least 322,122,540 bytes. What draws from it is fixed in size and bounded in number: one
2,048-entry sliding-window array per palf instance (src/logservice/palf/fixed_sliding_window.h:111-114,
src/share/log/palf/log_define.h:117) and the task slices for log submission, flush, meta flush,
prefix truncation and throttling purge (ob_log_allocator.cpp:40-45), whose number in flight the
sliding window and the IO queue bound (src/logservice/palf/palf_env_impl.cpp:977-978). palf instances
belong to log streams, which SQL does not create. So the five -4013 sites
(src/logservice/palf/log_engine.cpp:876-878, 892-894, 910-912, 933-935, 966-968) stay megabytes away
from a limit of at least 307 MiB, whatever the client sends and whatever `memory_budget` is. One
route does reach them and is rejected: the hidden parameter `_ctx_memory_limit`, settable with `ALTER
SYSTEM SET` (ob_parameter_seed.ipp:296-298), caps one memory context (applied by
src/observer/ob_server_duty_task.cpp:54-80), and the clog blocks come from the default context
(`ObMemAttr`'s `ctx_id` defaults to 0, src/oblib/lib/alloc/alloc_struct.h:134-137). But capping the
default context also caps the memstore pages (src/storage/allocator/ob_fifo_arena.cpp:45) and most
other server allocations, so whichever of them fails first returns the error: that tests the context
limiter, not the clog allocator's own 30% cap.

**-4013 from the micro block cache after its retries.** The sleep-and-retry loop is in
`ObIMicroBlockIOCallback::alloc_data_buf` (src/storage/blocksstable/ob_micro_block_cache.cpp:387-408,
retries at :396-402, constants at ob_micro_block_cache.h:292-293), whose body is marked "UNUSED NOW"
(:389). Nothing calls it: the IO layer declares `alloc_data_buf` (src/share/io/ob_io_define.h:207) but
never calls it, and the only calls of any `alloc_data_buf` are inside the temp-file cache on its own
class (src/storage/tmp_file/ob_tmp_file_cache.cpp:698, :739, :769). The 4 GB FIFO it would allocate
from is a constant (ob_micro_block_cache.cpp:1006-1009) that no parameter changes, and it serves
buffers that live for one IO. The -4013 a read can get near there comes from the KV cache store
(`kvcache->alloc` in `put_cache_block`, ob_micro_block_cache.cpp:1249), a budget owner PLAN.md family
12 does not list; it is not scripted here.

None of the errors the family expects is retried by the SQL retry controller
(src/sql/ob_query_retry_ctrl.cpp:816-905 registers none of -4013, -4030, -11049).

## Time and memory

One instance at a time, one port, `memory_budget=1G` and `cpu_count=4`.

| Scenario | Data | Estimated time | Estimated memory beyond an idle server |
|---|---|---|---|
| work_area_spill | 26 MB table | 60-90 s (two 10 s waits, six query runs) | memstore about 60 MB; work areas up to 6.7 MB each at 5%, up to about 40 MB each at 100%; temp files up to about 60 MB |
| query_memory_limit | 34 MB table | 30-45 s | about 60 MB for the restored group by |
| hash_join_depth | 150,000 rows, 30 MB of build rows | 40-70 s (8-9 levels, about 240-270 MB written to temp files) | work area 6.7 MB; temp files at most about 60 MB at a time |
| memstore_below_reserve | 2 rows | 15-25 s | none |
| memstore_fill | 1 MB chunks, about 12-13 of them, 0.2 s apart | 25-45 s | memstore grows by about 18 MB |
| vector_limit | 10 rows kept | 15-25 s | small |

Whole family: about 3-5 minutes; the times above include about 5 s of start and init each.
`memory_budget=1G` caps the budget-sized pools (KV cache 410 MB, memstore 512 MB, work areas); the
idle server's own footprint has not been measured at this budget, and the estimate for the peak
resident set size is under 2.5 GiB, which fits on the 24 GiB Mac next to one judge instance. Disk:
under 2 GB in the base dir at any time, removed after each scenario. The manifest's
`outcomes.<scenario>.elapsed_seconds` and `peak_rss_kb` replace these estimates after the first live
run.

## What has been checked

Offline only (judge runs occupy the machine; no seekdb, obclient, mysqltest, sdb.py or sysbench was
started):

- `python3 -m py_compile` and `--help`.
- An in-process harness kept outside the repo (/private/tmp/memory-family-offline/: fake_env.py,
  test_memory_scenarios.py) that replaces `subprocess.run` with a fake obclient, a fake sdb.py and a
  fake `ps`, the pid lookup and kill with fakes that assert the kill goes to the instance's pid, and
  models the memstore as a quota that each chunk raises and that is refused above the limit minus a
  configurable reserve. 56 checks pass:
  - `parse_client_error` accepts one ERROR line and refuses a second line, a warning line before it,
    and a line without its newline;
  - two clean runs are accepted by the runner's `compare` (6 identical, 0 recording problems) although
    `mem_hold` and every memstore value differ between them; every scenario starts once with the two
    parameters and ends with one kill and one destroy; the manifest has the keys `compare` reads; the
    outcomes carry the elapsed time and peak RSS; `mem_hold` is replaced and its reason recorded;
    obclient's `at line N` is dropped; the computed memstore limit and the memstore reads are not
    recorded; the sort output is a digest; the hash join's `V$SQL_WORKAREA` row is recorded between
    the error and the second join; the not-recorded values are printed with their scenario's name;
    every chunk runs after a 0.2 s pause;
  - each of these gives a `.partial`, `failed_cases`, exit 1 and a refused `compare`: a different code
    from the hash join, the memstore check or the vector limit; a failed join whose work area ran one
    pass only; a hash-join error with a second stderr line (which lands in the `.partial`); a server
    with no reserve whose inner tables hold 30 MB (memstore_below_reserve); in memstore_fill, a
    reserve of 0, 50, 90 or 120 MB, three or five times the memstore per chunk, a `memstore_limit`
    read back 1 MB off, and no chunk ever refused; a query limit that is not enforced; spilled and
    unspilled outputs that differ; a run at 100% that spills; a failing setup statement (its stderr
    and exit code land in the `.partial`); the global query timeout not reaching new sessions; the
    server dying on the refused insert;
  - a failed first start is a run error and the later scenarios are marked not run; a failed kill is
    a run error, the scenario is `.partial`, the destroy still runs and the later scenarios are marked
    not run; SIGTERM during a statement kills and destroys that instance, finishes the manifest with
    `hash_join_depth: stopped by signal 15` and marks the later scenarios not run; SIGTERM during the
    first destroy, or while the first recording is written, lets that step finish, keeps that
    scenario's `.result` and stops the run; SIGTERM right after the manifest is created starts no
    instance and marks every scenario not run; SIGINT is handled the same way; a later run in the same
    process starts clean; `--scenario` keeps the fixed order; a used record directory is refused.
- A real signal (test_signal_subprocess.py): the script runs in a child process against the fakes and
  blocks for 60 s inside the failing hash join; SIGTERM from the parent ends it in under 5 s (6 ms
  measured) with exit 1, the three instances started were each killed and destroyed, the manifest is
  finished with the stop, and the later scenarios are marked not run.
- The real pid lookup and kill against stand-in processes (`/bin/sh` loops with a matching or a
  different `--base-dir`; test_real_kill.py): the lookup accepts the matching one and refuses the
  other, the kill sends SIGKILL and sees the exit, and a second kill does nothing.
- The vsag behavior above, by disassembling the linked libvsag.dylib with `otool`.
- Every other source fact above was read at 834bbee1e in this worktree.

## Live check on the reference (after the judge run ends)

```
cd /Users/colin/seekdb-dev/migrate-to-rust
RUN=/Users/colin/seekdb-dev/mysqltest-runs/00b/memory-family
mkdir -p $RUN
for n in 1 2; do
  python3 -u migration/judge/families/memory/memory_scenarios.py \
    --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
    --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
    --base-dir $RUN/base-$n --record-dir $RUN/rec-$n --port 3891 \
    --init-sql migration/judge/reduced-init/init.sql \
    --init-user-sql migration/judge/reduced-init/init_user.sql \
    --save-instance-dir $RUN/logs-$n > $RUN/run-$n.log 2>&1
  echo "run-$n rc=$?"
done
python3 .github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN/rec-1 --right $RUN/rec-2 --out $RUN/compare.json > $RUN/compare.log 2>&1
echo "compare rc=$?"
grep -h "not recorded:" $RUN/run-1.log $RUN/run-2.log
```

What the first live run must confirm, in the order the scenarios run:

- the server starts and inits with `memory_budget=1G` and `cpu_count=4`, and `set global
  ob_query_timeout` reaches new sessions;
- work_area_spill: all three operators spill at 5% and are optimal at 100%, the outputs match, and
  the `V$OB_PLAN_CACHE_PLAN_STAT` suffix lookup finds each query's `sql_id`;
- query_memory_limit: the group by fails with 11049 and `mem_limit=10737418`; whether `mem_hold`
  differs between the two runs (`not recorded:` lines);
- hash_join_depth: the error is 4013 and not a timeout or another code, and how long it takes; the
  probe finds the failed query in the plan cache and shows one `PHY_HASH_JOIN` execution, multi-pass,
  dumped;
- memstore_below_reserve: 4030 on INSERT, UPDATE and DELETE; writes resume after `'0M'`; the printed
  `memstore_used` against 64 MB (below 64 MB means this scenario alone would also catch a missing
  reserve);
- memstore_fill: the refusal after at least 6 chunks (the printed `accepted_chunks`, expected about
  12-13), `memstore_limit` read back as set, and the printed
  `used_after_refusal_above_limit_minus_reserve` between 0 and 8 MB (expected 1-2 MB); the later
  UPDATE is refused; writes resume after `'0M'`;
- vector_limit: which error the client sees (4013 expected) and that the server survives;
- both recordings identical under `compare`, and the elapsed time and peak RSS per scenario in the
  manifests.

Then, for 00b's second sign-off, one caught mutation of this family's behavior, run as PLAN.md's
"How the injected mutations run" describes. Candidates, each caught by a recorded error or a check:
return `OB_ALLOCATE_MEMORY_FAILED` instead of `OB_SERVER_RUNTIME_OUT_OF_MEM` at
ob_access_service.cpp:657; halve the reserve at ob_memstore_freezer.cpp:1075
(`REPLAY_RESERVE_MEMSTORE_BYTES / 2`; memstore_fill's distance check); compute the query limit with
`/ 50 *` at ob_memory_tracker.cpp:33; return `OB_ERR_UNEXPECTED` at ob_hash_join_op.cpp:843; use
`/ 10 * pctg` at ob_sql_memory_manager.cpp:808 (no spill at 5%).
