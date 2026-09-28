# 05-plan: the plan cache key includes the session id

Family 7, plan-cache counts (PLAN.md section 4, item 6 and family 7), unit 05-plan. Patch:
05-plan-key-includes-session-id.patch, one .cpp file, three lines added.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-25, and again on
2026-09-28 against the worktree with only reference-build.patch applied). Not built.

## What it changes

src/sql/plan_cache/ob_plan_cache.cpp, `ObPlanCache::construct_plan_cache_key(ObSQLSessionInfo &,
ObLibCacheNameSpace, ObPlanCacheKey &)`, lines 1947-1961 at 834bbee1e. This function fills the key
that every SQL plan lookup and every plan the cache adds is filed under: the database id, the
namespace, the system variables and configs that change plans, and the date flag. It is called for
the lookup (`construct_fast_parser_result`, ob_plan_cache.cpp:579) and, through
`construct_plan_cache_key(ObPlanCacheCtx &, ...)` (:1932-1945), when a plan is added (:893, :1750)
and for prepared-statement lookups (:1885). The patch adds, after line 1959:

```
  if (!session.is_inner()) {
    pc_key.sessid_ = session.get_sid();
  }
```

`ObPlanCacheKey` has a `sessid_` field (`uint32_t`, like `get_sid()`) that 834bbee1e never sets for a
lookup: the constructor and `reset` put 0 in it (src/sql/plan_cache/ob_plan_cache_struct.h:58-75),
and no code assigns it (the only assignments of a `sessid_` in src/sql/plan_cache are the plan cache
value's own, for plans that use temporary tables, ob_plan_cache_value.cpp:266-270). The key's `hash`
and `is_equal` include it (ob_plan_cache_struct.h:115-137). With the patch, a statement run by a user
session is filed under that session's id, so a plan compiled by one session is never found by
another: each client session compiles its own copy the first time it runs a statement, and finds
that copy afterwards. Temporary tables are still matched inside the plan cache value, as before
(ob_pcv_set.cpp:130-132, ob_plan_cache_value.cpp:1510-1522). Inner sessions keep sharing: `is_inner()`
is true for inner SQL and for SQL run from PL (src/sql/session/ob_sql_session_info.h:621-627), so the
server's own statements (schema loads, DDL work, background tasks) are filed as before.

What a statement returns does not change. A session that misses compiles the same statement under
the same schema, database and plan-affecting variables (all still in the key), so it gets the plan
the shared copy would have been. The only difference is how often a user session compiles: the
first lookup of a statement in each session, instead of the first lookup in the server. This is the
kind of change a translation makes when it fills a key field from the session because the field is
there, without seeing that the field is left 0 on purpose.

## Why family 7 catches it (the exact output that changes)

A hit adds 1 to both `hit_count` and `access_count`: `inc_hit_and_access_cnt()` when
`pc_get_plan` finds a plan (src/sql/ob_sql.cpp:2999). A lookup that finds nothing counts nothing
itself; the statement then goes the long path, and `parser_and_check` adds 1 to `access_count` for
every DML statement and SHOW VARIABLES (ob_sql.cpp:3255-3259), which are also the only statements
the cache holds. So each lookup that the patch turns from a hit into a miss moves 1 from `hits` to
`misses` in plan_cache.tsv, and hits plus misses stay the same.

Every case has such a lookup: the version query of mysqltest's login. The client library linked into
mysqltest sends `select @@version_comment, @@version limit 1` on each connection
(../../harness/second-set/README.md, "what one read counts"). At 834bbee1e that query is a hit
in every case: init_user.sql's obclient session, which connects with `-Dtest`, compiles it in database
`test` before the case, and mysqltest's connection, also in `test`, finds that plan. The family's
README shows it ("What a case's counts hold"): in probe-idle, the version query's plan in
V$OB_PLAN_CACHE_PLAN_STAT has database id 500001 (`test`), its first load at the time init_user.sql
ran, and one hit from the case's login. With the patch, mysqltest's session has its own key, so its
version query misses.

So in a recording made with the mutated binary, every case's line in plan_cache.tsv has `misses`
higher and `hits` lower by the same number k, with k at least 1:
- k is exactly 1 for a case that opens one connection and stays in database `test`: the only plan in
  `test` before the case is the version query's (init_user.sql switches to `oceanbase` with its first
  statement), and within one session the patch changes nothing. `empty_input` records 6 hits and 2
  misses instead of 7 and 1.
- k is larger for the 119 cases that open more connections (../../lists/multi-connection.txt): each
  connection's version query misses, and so does every statement a connection runs after another
  connection compiled it.

The live check of the family (../../families/plan_cache/README.md, "Live check, 2026-09-25") shows
which part of that is safe to read. Background inner SQL adds hits at random to any case, even one
of 0.02 s, so hits differ between two C++ recordings in most cases. Misses are steady wherever the
two reads are close together: the 69 cases whose reads are under 0.1 s apart have the same misses in
all four C++ recordings (rec1 to rec4; rec1 and rec3 were stopped part way and reached some of them),
and `empty_input` has 7 hits and 1 miss in all four. Background work cannot take a miss away, so a
case whose misses rise by k while its hits fall by about k is the patch's doing, and a rise in the
misses of those 69 cases is a difference no C++ recording has shown.

## Why the 272 configured cases do not catch it

- The code runs in every case: in the coverage profile of the 272 cases
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb, both passes),
  `llvm-cov show -name-regex='construct_plan_cache_key'` gives 1.87M executions of every line of
  `construct_plan_cache_key(ObSQLSessionInfo &, ...)`, and `ObSql::pc_get_plan` 1.83M lookups, of
  which 1.61M were hits (`inc_hit_and_access_cnt`, ob_sql.cpp:2999). ob_plan_cache.cpp and ob_sql.cpp
  are identical at 076eb309b and 834bbee1e (`git diff --quiet 076eb309b 834bbee1e -- <file>` exits 0),
  so the profile's line numbers hold.
- No result changes (above). The configured cases that print plan cache figures run the statements
  they count in one session, where the patch changes nothing:
  geometry.geometry_bugfix_mysql prints `executions` and `hit_count` from
  `oceanbase.V$OB_PLAN_CACHE_PLAN_STAT` for statements it ran itself on its one connection
  `conn_admin` after `alter system flush plan cache` (test lines 13 and 172-212), and
  vector_index.vector_index_bugfix1 prints the `hit_count` of three selects it ran on its default
  connection (test lines 416-428, result `2`). The key's `sessid_` shows in no view: the `SESSID`
  column of the plan stat views is the plan's own `stat_.sessid_` (src/observer/virtual_table/ob_gv_sql.cpp:796-803),
  which is set only for plans on temporary tables (src/sql/engine/ob_physical_plan.cpp:993-994). No
  configured .test or .inc file reads SHOW TRACE, `sqlstat`, the SQL audit (removed from seekdb:
  ob_inner_table_schema_def.py:3305) or a `sessid` column (`grep -ril` over tools/deploy/mysql_test,
  2026-09-28).
- What the patch does change outside the counters is the number of compilations: a case with several
  connections compiles more, and the plan cache holds more plans. Neither shows in a .result file.
  A family 1 run of the mutated build is expected to pass.

## How the mutation stage runs it

Apply, rebuild and copy the binary aside as in ../README.md, "How to run them", then record the
family with the mutated binary exactly as the C++ recordings were made
(../../families/plan_cache/README.md, "Commands"; record.sh with the mutated binary and a port of its
own), and compare it with rec4, the C++ recording that finished with no failed case:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
F=$H/migration/judge/families/plan_cache
C=/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/05-plan
RUN=<a new directory>
$F/record.sh $RUN $RUN_BINARY <port>
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $C/rec4/rec --right $RUN/rec --require-plan-cache --out $RUN/compare.json
python3 $F/differences.py --left rec4=$C/rec4 --right mutated=$RUN \
  --extra rec2=$C/rec2 --extra rec1=$C/rec1 --extra rec3=$C/rec3 \
  --out-json $RUN/differences.json --out-tsv $RUN/differences.tsv
```

(`$RUN_BINARY` is the mutated binary copied aside, kept outside `$RUN`, since record.sh refuses a run
directory that exists.) Caught when both hold:
- `empty_input` records 2 misses (1 in every C++ recording) and 6 hits (7 if a background hit falls
  into its 0.02 s);
- every one of the 69 cases whose reads were under 0.1 s apart in the C++ recordings (differences.py's
  JSON: `misses_agree` true and every recording's `seconds` under 0.1) records more misses than in
  rec4, by the k above, and none records fewer.

`compare` then reports those cases `plan-cache different`; it exits 1, as it does for two C++
recordings, since hits differ by chance in most cases. The .result files are expected to be
identical outside the quarantine list and the case that can hang on `PURGE RECYCLEBIN` (the family's
README, "Cases whose counts cannot be compared"); `compare` notes that the seekdb binaries differ.
Revert the patch afterwards.

## Caught by (filled after the run)

Caught by family 7 on 2026-09-28 (README.md in this directory). record.sh on the mutated build (272
cases, a fresh instance per case, 7,246 s), compared with rec4: all 272 .result files identical;
`empty_input` recorded 6 hits and 2 misses (7 and 1 in every C++ recording); each of the 69 cases
whose reads were under 0.1 s apart gained misses, by 1 in 61 cases, 2 in 4, 3 in 3 and 8 in 1, and
none lost any. With the family's not-comparable.tsv turned into a `--plan-cache-not-comparable` list
(each listed case with its `differs in` value), `compare` reports 217 cases whose counts differ; the
same list gives 0 for the C++ recordings rec2 against rec4.

The 272 configured cases on the mutated build: only quarantined cases failed (the first such run
was cut short by the Mac's sleep and repeated). Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/mutations/05-plan-key-includes-session-id/.

After the review of the second set (2026-09-28/29): the verdict no longer needs plan_check.py. The
family's list is now the pinned families/plan_cache/not-comparable.list (misses of 70 cases compared,
hits of none; families/plan_cache/README.md, "Which counts family 7 compares"), and `compare
--require-plan-cache --plan-cache-not-comparable` of this recording (family-plan in the run directory,
not rebuilt) exits 1 against rec4, against rec7 and against rec8, each time with all 70 compared
cases `different` and all 272 `.result` files identical. The same comparison of the C++ recordings rec7
and rec8, the second of which no list had seen, exits 0. The earlier sentence that the converted list
gave 0 for rec2 against rec4 is withdrawn: that list was built from those two recordings.
