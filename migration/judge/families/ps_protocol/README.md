# Family 8: the binary protocol (the `--ps-protocol` replay)

PLAN.md section 4, item 4 and family 8: the 272 configured cases replayed through mysqltest with
`--ps-protocol`, recorded twice on the C++ reference and compared, then compared C++ against Rust.
The replay is the runner's `run --ps-protocol` (../../harness/second-set/README.md,
"`--ps-protocol`"). This directory holds what the family adds to it.

| File | What it is |
|---|---|
| cases.txt | The family's case list: the configured cases whose binary-protocol output can be compared. The cases left out are named in it with their reasons ("Cases that cannot be compared", below) |
| known-failures.txt | The cases of cases.txt that stop under `--ps-protocol` in the same way in every recording, for `compare --known-failures` |
| offline_test.py | Offline tests of `compare --known-failures` and of the `failure_lines` the runner records: `python3 -B migration/judge/families/ps_protocol/offline_test.py`, which loads the live runner; `--runner PATH` loads another copy, and `--baseline PATH` (the runner before the change, from `git show deade1391:.github/script/seekdb/mysqltest_for_seekdb.py`) adds the check that the default output did not change |
| runner-known-failures.patch | The runner change this family made on 2026-09-25, against commit deade1391 |

## Commands

Every judge run passes `--max-retries 0 --no-ignore-trailing-whitespace`, and before every run
`git -C /Users/colin/seekdb-dev/migrate-to-rust diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py`
must succeed. One recording, with `$RUN` a new directory:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
REF=/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb
CLI=/Users/colin/seekdb-dev/ref-archive-834bbee1e/client
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $REF --obclient $CLI/obclient --mysqltest $CLI/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN/work --port 3891 \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --ps-protocol --case-list $H/migration/judge/families/ps_protocol/cases.txt \
  --record-dir $RUN/rec
```

A recording of cases.txt takes about 21 minutes on this Mac ("Live check, 2026-09-25"). Two
recordings are compared with

```
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --require-ps-protocol \
  --known-failures $H/migration/judge/families/ps_protocol/known-failures.txt \
  --out $OUT/family8.json
```

The recordings of the live check use the full tools/deploy/init.sql, like the checked-in .result
files. A C++-against-Rust comparison records both builds with the same case list and the same init
files (`--init-sql` and `--init-user-sql` when the reduced init is used), which `compare` checks.

## What the replay sends through the binary protocol

mysqltest (the archived /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest, obclient
2.2.12's mysqltest on MariaDB 10.4.18) sends a statement through COM_STMT_PREPARE and
COM_STMT_EXECUTE when the statement is not sent with `send` and its text matches the prepared-statement
filter compiled into the binary (`strings -a` prints its regular expression):
ALTER SEQUENCE, TABLE or USER; ANALYZE; ASSIGN; CHANGE; CHECKSUM; COMMIT; COMPOUND; CREATE DATABASE,
INDEX, ROLE, SEQUENCE, TABLE, USER or VIEW; DELETE; DO; DROP DATABASE, INDEX, ROLE, SEQUENCE, TABLE,
USER or VIEW; FLUSH; GRANT; HANDLER ... READ; INSERT; INSTALL; KILL; OPTIMIZE; PRELOAD; RENAME TABLE or
USER; REPAIR; REPLACE; RESET; REVOKE; ROLLBACK; SELECT; SET OPTION; SHOW; SHUTDOWN; SLAVE; TRUNCATE;
UNINSTALL; UPDATE; each followed by white space. Everything else still goes as COM_QUERY: SET, BEGIN,
USE, CALL, EXPLAIN, DESC, ALTER SYSTEM, PURGE, SQL-level PREPARE and EXECUTE, LOAD DATA, FORK TABLE,
and every statement sent with `send` (six cases use it: parallel_insert, sfu,
deadlock_detector.trans_deadlock_basic, fork_table.fork_table_snapshot, fork_table.fork_table_with_index,
vector_index.rebuild_vector_index). No configured case switches the option off
(`--disable_ps_protocol`).

- **The statement text goes as it is.** mysqltest binds no parameters, so every COM_STMT_EXECUTE of
  the replay carries none (smoke-probe2 reads `param_count` 0 back from the server; a `?` in a
  statement stops the case, as vector_index.vector_similarity shows below), and the server's parameter
  decoding (`ObMPStmtExecute::request_standard_params`, src/observer/mysql/obmp_stmt_execute.cpp:967-1186,
  and `nio_parse_mysql_execute_params` in rust/sql-nio/src/stmt_execute.rs) only ever sees a count of 0.
  Parameters, COM_STMT_SEND_LONG_DATA, cursors with COM_STMT_FETCH and COM_STMT_RESET are item 8's
  `wire_binary_types` scenario (../wire/README.md).
- **The server prepares a parameterized statement.** For a DML statement that is not SHOW, the
  prepare replaces its literals with `?` (`ObSqlParameterization::parameterize_syntax_tree`,
  src/sql/ob_sql.cpp:894-915) and keeps that text as the statement's text; at execution the server
  fills the literals in itself. The text protocol never does this at prepare.
- **The rows come back in the binary row format.** `ObQueryDriver::response_query_result` switches to
  BINARY for a prepared statement (src/observer/mysql/ob_query_driver.cpp:141), and every cell goes
  through `ObSMUtils::build_cell_value` with BINARY
  (src/query/protocol/ob_mysql_protocol_util.cpp:197-650): integers as 1, 2, 4 or 8 bytes, FLOAT and
  DOUBLE as their bits, DATE, DATETIME, TIMESTAMP and TIME as their parts, YEAR as a number, BIT as its
  value and length; DECIMAL, strings, JSON and the rest as length-encoded bytes, as over text. The
  packed plans' cells take the same function through `ObExprOutputPack::build_row_values`
  (src/sql/engine/expr/ob_expr_output_pack.cpp:334-391). In the coverage profile of the 272 text-
  protocol cases every binary branch of `build_cell_value` has 0 hits (the mutation note, below, gives
  the counts); the replay is what runs them.
- **The client prints the values.** mysqltest binds every result column as a string, so the client
  library turns each binary value into text itself (its own formatting of DOUBLE, FLOAT, DATETIME,
  TIME and zero-filled integers), and those strings are what a recording holds. A server change in a
  binary cell therefore shows as the client prints it.
- **Prepared statements stay in the server's PS cache.** mysqltest prepares each statement on its
  connection's one statement handle, which closes the statement before it, and the server keeps the
  closed statements until the PS cache evicts them: at 09:57 in rec1 the cache held 28,611 statements,
  103 MB of its 185 MB high mark (`ps cache evict` in seekdb.log).

## How the binary protocol changes what a case prints

The checked-in .result files were recorded over the text protocol. Under `--ps-protocol` some cases
print differently. Most of it is the client: mysqltest's prepared-statement path and the client
library, which are the same for both builds. Some of it is the server, and the Rust build must
reproduce it. None of it is a failure of the judge; the judge compares `--ps-protocol` recordings with
each other.

From the client:
- **`--result_format 3` and `4`, the formats that draw a box** (32 of the 272 cases switch to one of
  them). The prepared-statement path does not draw the box. It prints the column names separated by
  tabs, then each row's values run together with no separator, except that a NULL is printed with a
  tab in front of it (`16obschema_c_0_S<TAB>NULL<TAB>NULL0` for the box row
  `| 16 | obschema_c_0_S | NULL | NULL | 0 |`). The SHOW results such a case prints without a box
  over text lose their tabs the same way. So a value that moves between two neighbouring columns does
  not show under `--ps-protocol` in these cases; the text-protocol families see it.
- **`--replace_regex` and `--replace_column` in the box formats.** The prepared-statement path does
  not apply them there either (smoke-probe2, below, for format 4); in the default format they apply,
  and so does `--replace_result`. The values a box-format case masks over text therefore come out as
  they are: creation times in information_schema, generated column names that hold a creation time in
  microseconds in fts_index.basic_dml and fts_index.index_view (`__doc_id_1790301004178434`), a table
  id in fts_index.online_ddl_on_table_with_fts_index (`__idx_503351_f101`), BLOCK_SIZE and PCTFREE in
  generated_column's SHOW CREATE TABLE. The ones that change from run to run make the case impossible
  to compare ("Cases that cannot be compared").
- **Long values and padding in the box formats.** The text protocol's box cuts a long value to the
  box's width (fts_index.simple_query, fts_index.partitioned_simple_query); the prepared-statement
  path prints it whole. A BINARY(70) value prints its padding in both (fts_index.basic_dml_sequel).
- **`--enable_metadata`.** `max_length` is the largest length the column's type can print, which the
  client library computes for prepared statements (11 for an INT), where the text protocol reports the
  longest value (generated_column: `3 11 11` in place of `3 11 1`).
- **`--sorted_result`.** The warning lines are sorted together with the rows
  (groupby.group_by_basic: `Warning 1260 Row 3 was cut by GROUP_CONCAT()` and `Warnings:` land
  between the rows `NULL` and `d1,d4,d`).
- **Two statements stop their case** ("Cases that stop under the binary protocol", below): a
  zero-filled `int(255)` value that no longer fits the client's buffer (expr.func_length), and a
  `select cosine_similarity(?,?)` whose `?` are parameter markers over the binary protocol, which
  mysqltest never binds (vector_index.vector_similarity).

From the server:
- **Names built from the statement text show `?` for literals.** A select item without an alias is
  named after its text, and a prepared DML statement's text is the parameterized one
  (src/sql/ob_sql.cpp:894-915): view_2 prints the column `(select c2 from v limit ?)` for
  `(select c2 from v limit 1)`, subquery.subquery `(select c1 from t1 where c1=?)`.
- **The plan cache shows the parameterized text.** geometry.geometry_bugfix_mysql reads
  `V$OB_PLAN_CACHE_PLAN_STAT.query_sql`, which holds `values (?,?,st_srid(point(?,?),?))` where the
  text run shows the literals.

## Cases that stop under the binary protocol

known-failures.txt lists them; `compare --known-failures` compares each as a failure (the harness
README, "The known-failures option"). Both stopped in every recording of the live check that reached
them (rec4 was stopped before vector_index.vector_similarity), at the same line, with the same message
and a byte-identical `.partial` (72 lines of expr.func_length's output, 707 of
vector_index.vector_similarity's), and the comparison of rec5 with rec6 accepted both.

- **expr.func_length** stops at test line 54, `select * from tx;` on `create table tx(s int(255)
  zerofill)`. Over the text protocol the server sends the value zero-filled to 255 characters, and the
  .result prints it so. Over the binary protocol the server sends the INT as 4 bytes, with ZEROFILL and
  the display width 255 in the column definition. mysqltest fetches every column as a string into a
  buffer sized from the column's `max_length` (MariaDB's mysqltest does so in `append_stmt_result`),
  which the client library sets to 11 for an INT column of a prepared statement (the `--enable_metadata`
  lines of generated_column show it); the client library zero-fills the value to 255 characters, the
  fetch returns 101 (MYSQL_DATA_TRUNCATED), and mysqltest stops: `mysqltest: At line 54: mysql_fetch
  didn't end with MYSQL_NO_DATA from statement: error: 101`. The output up to that statement is
  compared as the case's `.partial`; the 12 statements after it (the same three SELECTs on an
  `int(121) zerofill` table among them) do not run under `--ps-protocol`. A build that sends the value
  some other way no longer stops there, and `compare` reports the case as recorded on one side only.
- **vector_index.vector_similarity** stops at test line 461, `select cosine_similarity(?,?);` under
  `--error 1064`. Over the text protocol the `?` are a syntax error, which the test expects. Over the
  binary protocol they are parameter markers: the server prepares the statement with two parameters,
  mysqltest executes it without binding any, the client library refuses with 2031, and mysqltest stops
  because it expected 1064: `mysqltest: At line 461: query 'select cosine_similarity(?,?)' failed
  with wrong errno 2031: 'No data supplied for parameters in prepared statement', instead of 1064...`.
  The rest of the file does not run under `--ps-protocol`: the `inner_product_similarity` and
  `l2_similarity` checks (each with its own `(?,?)` line) and a procedure that inserts 50 vectors
  before a parallel `cosine_similarity` select.

## Cases that cannot be compared

cases.txt leaves these 4 out, each with its reason (11 until 2026-09-28; the seven purge cases are back,
below). Leaving a case out of family 8 does not take it out of the other families: over the text
protocol family 1 compares it as before. Each prints, over the binary protocol, something that changes
from run to run on the same binary. Two C++ recordings of the 272 cases (rec1 and rec2) differ in these
four and in the purge hang, and nowhere else outside the cases that stop (known-failures.txt).

- **information_schema.** Its two `select * from information_schema.tables ...` statements (test lines
  39 and 64) run in format 4 under `--replace_column 8 NULL 9 NULL 10 NULL 15 NULL 16 NULL`. Over the
  binary protocol the replacement is not applied, and `CREATE_TIME` and `UPDATE_TIME` print when the
  instance created each system table: rec1 and rec2 differ in 758 lines, and in nothing else once
  those times are blanked out.
- **fts_index.basic_dml and fts_index.index_view.** They print the hidden columns of a fulltext
  index under a `--replace_regex` that turns the numbers in their names into `*`: basic_dml's eight
  `SELECT column_name, is_hidden FROM oceanbase.__all_column WHERE table_id = ...` from test line 1574
  on, in format 4; index_view's `SELECT * FROM information_schema.STATISTICS ...` (test line 55) in
  format 3. Over the binary protocol the names print as they are, and a name holds the time its
  column was created in microseconds: `__doc_id_1790300988615502` is 2026-09-25 09:49:48 +08:00 in
  rec1. So every recording differs from every other in those lines (20 in basic_dml, 7 in index_view
  between rec1 and rec2, and nothing else once the times are blanked out).
- **fts_index.online_ddl_on_table_with_fts_index.** Its format-3 `SELECT table_name FROM
  oceanbase.__all_table WHERE data_table_id = ...` (test lines 46-48, an `eval` without the query log)
  runs under `--replace_regex /_[0-9]+/_*/`. Over the binary protocol the index table names print with
  the data table's id, and the id is not the same in two runs of the same cases: `__idx_503351_f101`
  in rec1, `__idx_503344_f101` in rec2.
- **The seven vector_index cases that run `PURGE RECYCLEBIN` were left out until 2026-09-28.**
  vector_index.all_virtual_vector_index_info, create_table_with_vector_index, drop_vector_index,
  rebuild_vector_index, vector_index_partitioned, vector_index_post_create and vector_index_rebuild
  each drop tables with vector indexes and then send `PURGE RECYCLEBIN` (as text; PURGE is not on
  mysqltest's list). In rec1 the first purge of all_virtual_vector_index_info (test line 38) ran until
  the DDL timeout, 1,000 s (`_ob_ddl_timeout`, src/share/parameter/ob_parameter_seed.ipp:748), and
  failed with `4012: Timeout` (09:55:04 to 10:11:53; the case took 1,064 s); in rec2 and normal1 the
  case passed in 61 s and 56 s. In rec4 the purge of create_table_with_vector_index hung the same way
  (rec4/purge-hang-evidence.txt), and the run was stopped there. In both hangs the table lock service
  retried the lock of one tablet of the purged tables whose status was DELETED (`tablet is already
  deleted`, OB_TABLET_NOT_EXIST, tablets 200390 and 200358;
  src/storage/tablelock/ob_table_lock_local_executor.cpp:64-68), with no pause between tries
  (`need_retry_partial_task_` at src/storage/tablelock/ob_table_lock_service.cpp:1739-1747), writing
  about 8 MB of seekdb.log a second. It is a race in the reference that either protocol can hit, not
  a protocol difference: over text it has since hung create_table_with_vector_index in family 7's rec2
  and family 11's par-2, and rebuild_vector_index in first-set mutation 07's run and in the first 272
  run of second-set mutation 01. The first cases.txt left out the two cases seen hanging here and,
  by analogy, the five others. A review of the second set pointed out that four of those five had
  never been seen hanging, and that three families handled the race three ways. Since 2026-09-28 the
  seven are on ../../quarantine.tsv, with the evidence, under one rule for every family: a quarantined
  case is compared while two C++ recordings agree on it, and a recording that stops on this hang (4012
  on `PURGE RECYCLEBIN`, `tablet is already deleted` repeating in seekdb.log) is made again. So
  cases.txt holds all seven again (268 cases), and the live check below records them.

## Comparing a Rust build

Record the Rust build with the command above (same case list, same client, same init) and compare it
with a C++ recording with `--require-ps-protocol --known-failures known-failures.txt`; ps-r7 and
ps-r8 ("Live check, 2026-09-28", below) are such C++ recordings of the current cases.txt (rec5 and rec6
are of the 261-case list before it), usable as long as cases.txt, tools/deploy, the clients and the
init files stay as they were. Every case of cases.txt must then be identical, or have failed alike if it is
on known-failures.txt. What the recordings show about the binary protocol:
- the values of every column type as the client prints them from binary cells, so a changed binary
  encoding (a width, a sign, a fraction, a time zone, a byte order) shows in any case that selects
  that type;
- the statement names and plan cache texts that come from the parameterized prepare text;
- the case that stops on a zero-filled INT, whose stop depends on the column definition the server
  sends.
What they do not show: in the box formats (`--result_format 3` and `4`), where one value of a row
ends and the next begins; and what "What the replay sends through the binary protocol" lists as not
covered. In the box formats they also compare values the text runs mask (table ids, block sizes), so a
Rust build that numbers tables differently from the reference shows there first.

## Live check, 2026-09-25

All runs used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256
db7d9180…), its client/obclient and client/mysqltest, port 3891, one instance at a time, the full
init, `--max-retries 0 --no-ignore-trailing-whitespace --ps-protocol` and no `--save-instance-dir`,
and the working-tree check and a check for at least 8 GiB free on / passed before each run (run.sh in
the output directory passes those options and makes both checks). Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/01-ps/<run>/ (runner.log, times.txt, work/,
rec/).

| Run | What | Time | Result |
|---|---|---|---|
| smoke-probe | probe-tests/psprobe.test with `--test-dir` | 18 s | Between two text-protocol reads of `oceanbase.__all_virtual_ps_stat.access_count` around two SELECTs, 2 prepares: `--ps-protocol` reaches COM_STMT_PREPARE. Fourteen column types (INT, TIMESTAMP(6), DATETIME(6), DOUBLE, TIME(6), DATE, FLOAT, YEAR, BIT, DECIMAL, BIGINT UNSIGNED, TINYINT, TIMESTAMP, DATETIME) print the same over both protocols in the default format |
| rec1 | the 272 cases; runner at deade1391 (sha256 9f417e4e…) | 39 min 51 s | 269 recorded; 3 failed: expr.func_length (test line 54), vector_index.vector_similarity (test line 461), vector_index.all_virtual_vector_index_info (test line 38, 4012 after 1,064 s) |
| smoke-probe2 | probe-tests-2/psreplace.test | 18 s | `--replace_regex`, `--replace_column` and `--replace_result` apply in the default format; `--replace_regex` and `--replace_column` do not apply in format 4; `__all_virtual_ps_item_info` shows `param_count` 0 for `select c1 from ps_param where c1 = 7`; CHAR(10) values come back unpadded over both protocols |
| rec2 | the 272 cases; runner with this family's change (sha256 0b1e708d…) | 23 min 24 s | 270 recorded; 2 failed, the same two as in rec1 with the same `mysqltest:` lines; vector_index.all_virtual_vector_index_info passed in 61 s |
| compare rec1 rec2 | `compare --require-ps-protocol --known-failures known-failures.txt` (compare-rec1-rec2.json and .log) | – | Exit 1. 265 identical. 4 different: information_schema, fts_index.basic_dml, fts_index.index_view, fts_index.online_ddl_on_table_with_fts_index. 3 missing: expr.func_length and vector_index.vector_similarity failed alike (identical `.partial` files, exit 1 on both sides), not accepted as known failures only because rec1 was recorded before `failure_lines` existed; vector_index.all_virtual_vector_index_info failed in rec1 only. The five quarantined cases are identical. These are the five cases the first cases.txt left out and the two of known-failures.txt, and nothing else |
| rec3 | the first cases.txt: 267 cases, before the other six purge cases were left out (sha256 aaaedf95…) | 22 min 31 s | 265 recorded; 2 failed, the two of known-failures.txt, with the same `mysqltest:` lines and `.partial` files as in rec2. Against rec2 on the 267 cases, every recording is the same except the quarantined type_date.type_create_time (below) |
| rec4 | the first cases.txt | stopped after 19 min | Of the 244 cases it finished, expr.func_length stopped as in rec3 and the other 243 recorded the same bytes as in rec3, except the quarantined type_date.type_create_time. Its `PURGE RECYCLEBIN` in vector_index.create_table_with_vector_index then hung (rec4/purge-hang-evidence.txt: seekdb.log repeating `tablet is already deleted` for tablet 200358, tens of thousands of lines a minute); the run was stopped there with SIGINT to the runner, whose cleanup destroyed the instance, and the other six purge cases were left out of cases.txt |
| rec5 | cases.txt: 261 cases (sha256 091a9c06…) | 20 min 36 s | 259 recorded; 2 failed, the two of known-failures.txt, with the same `mysqltest:` lines and `.partial` files as in rec2 and rec3. Against rec3 on the 259 cases both recorded, the same bytes except the quarantined type_date.type_create_time (below) |
| rec6 | cases.txt | 20 min 27 s | 259 recorded; 2 failed, the same two, with the same lines and `.partial` files |
| compare rec5 rec6 | `compare --require-ps-protocol --known-failures known-failures.txt` (compare-rec5-rec6.json and .log) | – | Exit 0: 259 identical, 0 different, 0 recording problems; the 2 known failures accepted (listed=2, accepted=2, not_accepted=0); the five quarantined cases are among the identical ones |
| normal1 | the 272 cases with no case list and no `--record-dir`: mysqltest checks each case against its .result, as a normal run does (the runner starts a new instance after each failed case, as in CI) | 34 min 45 s | 232 passed, 40 failed (below). The seven purge cases all passed |

The five quarantined cases stay in cases.txt, as PLAN.md section 4 asks ("compared if the two C++
recordings agree"). type_date.type_modify_time, histogram.stats_farm and
subquery.idx_with_const_expr_21_subquery_dilang recorded the same bytes in all six recordings, and
vector_index.sparse_vector_index_vsag_query in the five that reached it (rec4 was stopped before it).
type_date.type_create_time's last select, `select pk,b from t1 where b='$value_now'` after two
REPLACEs one second apart, printed the row `2 searched` in rec1, rec2, rec4, rec5 and rec6 and not in
rec3, which printed only `1 searched`, as the Linux-recorded .result does. Over the text protocol all
18 runs of the 272 cases on this Mac (the two validation passes, the two coverage passes and the 14
mutation runs) printed that row, which is why the case fails here and is quarantined. rec5 and rec6,
the two recordings of cases.txt, agree on all five, so family 8 compares all five; a later pair of C++
recordings that disagrees on one of them takes that case out of the verdict again.

### Which cases print differently over the binary protocol

The normal run (normal1, above) compared every case with its checked-in .result, as a normal run
does. 232 cases passed and 40 failed: the 40 that rec1 had already shown differing from their .result
files, no more and no fewer. 38 of the 40 left a .reject; 34 of these hold the same bytes as the
case's recording (rec5, or rec2 for a case cases.txt leaves out), and the other four are the four
cases whose unmasked values change from run to run. The causes, from the diffs of the .reject files
and of rec1 against the .result files (the two agree):

| Cause | Where it comes from | Cases |
|---|---|---|
| The box formats only | mysqltest | select_basic, delete.delete, executor.basic, executor.full_join, expr.expr_instr, fts_index.create_fts_index_afterward, fts_index.create_table_with_fts_index, fts_index.drop_index, fts_index.load_data, fts_index.tokenize_function, global_index.global_index_lookup_1, global_index.global_index_lookup_2, global_index.global_index_lookup_3, global_index.global_index_lookup_5, global_index.global_index_lookup_6, global_index.global_index_select, histogram.stats_farm, histogram.stats_lock_and_unlock, histogram.stats_prefs_manager, histogram.stats_set_table_stats, subquery.optimizer_subquery_bug (21) |
| The box formats, plus long values printed whole, padded values, or values the text run masks | mysqltest | generated_column (also `--enable_metadata`), information_schema, fts_index.basic_dml, fts_index.basic_dml_sequel, fts_index.index_view, fts_index.online_ddl_on_table_with_fts_index, fts_index.parser_properties, fts_index.partitioned_simple_query, fts_index.simple_query, histogram.dbms_stats_delete_stats (10) |
| `--sorted_result` sorts the warnings with the rows | mysqltest | groupby.group_by_basic (1) |
| Names and plan-cache texts from the parameterized prepare text | the server | view_2, subquery.subquery, geometry.geometry_bugfix_mysql (3) |
| The case stops | mysqltest and the client library, on what the server sends | expr.func_length, vector_index.vector_similarity (2) |
| Quarantined; differ over the text protocol too, for their listed reasons | the case | type_date.type_create_time, type_date.type_modify_time, vector_index.sparse_vector_index_vsag_query (3) |

The 232 cases that passed print exactly what their .result holds, and the 225 of them in cases.txt
recorded exactly their .result in rec5. They include the seven purge cases, all of which passed in
normal1 (vector_index.all_virtual_vector_index_info stopped in rec1 on the `PURGE RECYCLEBIN` race,
not in rec2 or normal1).

What the first real run changed, and why:

1. **The runner records a failed case's mysqltest message and `compare` gained `--known-failures`.**
   rec1 showed two cases that stop under `--ps-protocol` the same way every time (expr.func_length,
   vector_index.vector_similarity; "Cases that stop under the binary protocol"). `compare` counts a
   failed case in either recording as a recording problem, so without a change no family 8
   comparison could ever pass, and the only way out would have been to drop the two cases, losing
   what they print before they stop and the chance to see a build that no longer stops. The option
   compares such a case as a failure instead: same exit code, identical `.partial`, same
   `mysqltest:` message lines. The message lines had to be recorded for that (`failure_lines` in
   `outcomes`); nothing else in a recording changed. Every default stays as it was ("The
   known-failures option" in ../../harness/README.md; runner-known-failures.patch; offline_test.py).
   rec1 was made before the change and has no `failure_lines`, so it serves as evidence only, and
   the clean comparison is between two recordings made after it.
2. **The family runs from a case list, cases.txt.** The cases it leaves out print, over the binary
   protocol, values that the text run hides with `--replace_column` or `--replace_regex` and that
   change from run to run, or can hang the reference ("Cases that cannot be compared"). No output of
   a compared case is hidden or masked. The list was written from rec1 and rec2 with the first four
   and vector_index.all_virtual_vector_index_info left out (267 cases, used by rec3 and rec4); when
   rec4 hung in another purge case, the other six purge cases were left out too (261 cases, rec5 and
   rec6). Since 2026-09-28 the seven are back (268 cases, "Live check, 2026-09-28").
3. **known-failures.txt** names the two cases of change 1.
4. **The harness README's `compare` paragraph** now describes the live runner: the second-set patch
   (commit deade1391) had made two of its sentences wrong (second-set/README.md said they would
   change with it), and it now names `--known-failures`.

## Live check, 2026-09-28

After the review of the second set (the seven purge cases back in, "Cases that cannot be compared"),
by the unit that applied the reviews: the same reference, clients, full init and options as above,
port 3892 or 3891, cases.txt of 268 cases (sha256 9c190fa9...). Outputs under
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/.

| Run | When (+08:00) | Result |
|---|---|---|
| ps-r7 | 2026-09-28 23:46 to 2026-09-29 00:09 | 268 run, 266 recorded; the two cases of known-failures.txt stopped as before; no purge hang |
| ps-r8 | 2026-09-29 01:24 to 01:47 | the same |
| compare ps-r7 ps-r8 | – | exit 0: 266 identical, 0 different, 2 known failures accepted (listed=2, accepted=2), 0 recording problems |
| compare rec5 ps-r7 | – | on the 259 cases both hold, identical; the 7 purge cases are only in ps-r7 |
| mut01-ps | 2026-09-29 01:52 to 02:15 | mutation 01 rebuilt and recorded on this cases.txt: against ps-r7, 12 cases `different`, 254 identical, the 2 known failures accepted: caught |

All five quarantined cases and the seven purge cases are identical in ps-r7 and ps-r8, so family 8
compares them.

## The mutation for the second sign-off

../../mutations/second-set/01-ps-binary-timestamp-without-time-zone.patch, with its note beside it:
in `ObSMUtils::build_cell_value` (src/query/protocol/ob_mysql_protocol_util.cpp) the binary branch
of `ObDateTimeTC` stops applying the session time zone, so every TIMESTAMP value a prepared SELECT
returns moves back 8 hours, while the text branch, and so every text-protocol case, stays as it is.
In the coverage profile of the 272 text-protocol cases the mutated line has 0 hits; under
`--ps-protocol` type_date.timestamp2, type_date.datetime_java, type_date.daylight_saving_time,
two_order_by, driver5114_bug, datatype.replace, type_date.test_select_usec_to_time, update_range
and delete.delete_range print TIMESTAMP values through it. Caught on 2026-09-28 against rec5 and
again on 2026-09-29 against ps-r7 on the 268-case list: 12 cases differ (the note beside the patch).
