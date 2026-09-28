# Family 5: expressions and casts

PLAN.md section 4, family 5 and item 7. `generate.py` writes a mysqltest corpus into `cases/` that
runs every registered expression against a type matrix with NULL and edge values, both cast matrices,
and `now()` and other temporal values stored into DATETIME columns of every scale. The judge records
the corpus on the C++ reference and on the Rust build with the runner and compares the recordings
byte for byte (harness README, "compare").

The generator reads source files at 834bbee1e, split-rows.tsv and, for the reach split only, the
coverage profile. It never talks to a server; split_rows.py, which writes split-rows.tsv, and
stability.py do. The corpus was first run on 2026-09-25; what the runs found and what changed is in
"Live check, 2026-09-25".

## Commands

```
python3 migration/judge/families/expressions/generate.py reach
python3 migration/judge/families/expressions/generate.py cases
python3 migration/judge/families/expressions/generate.py cases --check
python3 migration/judge/families/expressions/generate.py check-recording --record-dir <record-dir>
python3 migration/judge/families/expressions/split_rows.py --port <port> --work-dir <new-dir>
python3 migration/judge/families/expressions/stability.py --port <port> --work-dir <new-dir>
```

- `reach` reads the registry and the coverage profile and writes `expressions.tsv`. It runs
  `llvm-cov export` and `llvm-cxxfilt` from the coverage build (about 10 s). The defaults are the
  coverage artifacts of 076eb309b: `--profdata`
  /Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, `--coverage-binary`
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb, `--coverage-root`
  /Users/colin/seekdb-dev/cov-076eb309b, `--llvm-bin` its deps/3rd devtools.
- `cases` reads the registry from the source and the measured columns (`reached`, `reach_basis`,
  `eval_functions`, `eval_functions_run`) from `expressions.tsv`, writes `cases/*.test` and rewrites the
  other TSV columns. It needs no coverage files, so the corpus can be regenerated anywhere.
- `cases --check` writes nothing and exits 1 if `cases/` or `expressions.tsv` differ from a fresh
  generation. Two generations with different `PYTHONHASHSEED` values were diffed and are
  byte-identical, and two `reach` runs give the same TSV.
- `check-recording` reads one recording of the corpus (the runner's `--record-dir`: one
  `<case>.result` per file) and finds, for every generated statement, its echo and the `errno` line
  after it. It exits 1 when a file has no recording, when a statement or its `errno` line is not
  found, when a setup statement failed (the session `SET`, the creation and filling of `tm`, and
  each unit's own setup), when a statement got a client error (2000-2999: the connection to the
  server was lost, which mysqltest records like any other error before it goes on), when the check
  that the spatial reference systems are loaded does not print 1, or when a known-answer column of
  s8_known_0001 is not 1. It also lists, without failing, the multi-row statements, type-only
  statements and `CREATE TABLE ... AS SELECT` statements that failed.
- `split_rows.py` runs the corpus without splits against a running reference instance under the
  reduced init, with every sweep over `tm` (and over `tm a, tm b`) that fails followed by one
  statement per row (or row pair), and writes split-rows.tsv: the sweeps that fail although a row
  other than the all-NULL one succeeds, with their failing rows. `cases` splits those sweeps (see
  "What each statement records"), and stops if split-rows.tsv names a statement the corpus no longer
  has; after a change to the probes, run `split_rows.py` again before `cases`. About 40 s.
- `stability.py` runs the corpus against a running instance with every SELECT probe that reads no
  session state repeated (`--repeats`, 3 by default), each repeat after a different unrelated
  statement, and exits 1 if a probe's output changes between repeats: a probe that reads memory it
  never wrote. Two recordings from two processes can agree on such a value. It turns the plan cache
  off for its own session, so that every repeat compiles again as the first run did (a repeat that
  found the cached plan would print fewer compile-time warnings). About 1 minute.
- `reach` and `cases` stop with an error when a registered expression has no entry in the
  specification table (`build_specs()`), when the TSV no longer matches the registry, or when the CAST
  targets in the grammar change, so a changed source cannot silently drop coverage.

## The expression list

Source: `ObExprOperatorFactory::register_expr_operators()` in
src/sql/engine/expr/ob_expr_operator_factory.cpp. Every `REG_OP(Class)` and `REG_SAME_OP` line is
read in order. For each class the constructor chain is followed down to `ObExprOperator`'s
constructor, with default arguments taken from the class declarations, to get the item type, the
name (the `N_` macros of src/oblib/lib/ob_name_def.h, including constructors that overwrite `type_`
and `name_` in their body, such as `ObExprMid`), `param_num` and the `INTERNAL_IN_MYSQL_MODE` flag.

- 527 entries: 528 `REG_OP` lines (`ObExprMid` and `ObExprBitAnd` are registered twice, so 526
  classes) plus `REG_SAME_OP` for `sha1`. `ObExprErrno` is registered only when `NDEBUG` is not
  defined, so the release reference has 526 of them; its probes record the unknown-function error.
- Names are not unique (`+` is both `T_OP_ADD` and `T_OP_AGG_ADD`; `^` is `T_OP_XOR` and
  `T_OP_BIT_XOR`), so an entry is the triple name, item type, class.
- 60 entries are internal in MySQL mode: calling them by name fails with "FUNCTION ... does not
  exist" (`check_internal_function`). Each gets that name call plus, where one is known, SQL that
  makes the optimizer or the DML path create it (see below).
- How SQL reaches each entry is in the specification table in `generate.py`, from the grammar
  (src/sql/parser/sql_parser_mysql_mode.y) and `get_function_alias_name`: operators, special forms
  (`CAST`, `CONVERT ... USING`, `EXTRACT`, `DATE_ADD(... INTERVAL ...)`, `TIMESTAMPADD`,
  `POSITION(... IN ...)`, `TRIM(LEADING ...)`, `GET_FORMAT`, `WEIGHT_STRING(... AS ...)`,
  `CHAR(... USING ...)`, `JSON_VALUE`, `JSON_QUERY`, `->`, `->>`, `MEMBER OF`, `COLLATE`,
  `NOW`/`CURRENT_TIMESTAMP`/`LOCALTIME`, `CURDATE`/`CURRENT_DATE`, `ANY`/`ALL` subqueries, `EXISTS`,
  lambdas for the array functions) and the aliases (`bin`, `oct`, `lcase`, `ucase`, `power`,
  `inet_ntoa`, `octet_length`, `character_length`, `area`, `centroid`, `ws`).
- `expressions.tsv` has one row per entry: `order` (registry order), `name`, `item_type`, `class`,
  `param_num`, `internal`, `registered` (release or debug build only), `reached`, `reach_basis`,
  `eval_functions`, `eval_functions_run`, `probes` (statements generated for it), `sql` (the form
  used; letters are the value domains below), `constructor` (file:line) and `files` (case files that
  hold its probes).

## Reached and unreached

The split comes from the coverage profile, not from text matching: 236 entries reached, 291
unreached (the text-based judge-reach map in feasibility/evidence-full.md found 207 of 527).

- Source: AB.profdata, the merged profile of the two coverage passes of the 272 cases at 076eb309b
  (full init.sql). `llvm-cov export -format=lcov` over src/sql/engine/expr and
  src/query/api/query/engine/expr lists 12,673 functions with their call counts, including the ones
  that never ran.
- An entry is reached only when all of these hold:
  - the factory created its class: the template instance `ObExprOperatorFactory::alloc<Class>` ran
    at least once;
  - when the class defines its own `calc_result_type*` or `cg_expr`, at least one of them ran;
  - at least one of its evaluation functions ran.
- Its evaluation functions are what its code generator installs: the `cg_expr` of the class, or of
  the nearest base class that has one, is read for assignments to `eval_func_`, `eval_batch_func_`
  and `eval_vector_func_`, for calls that receive one of them as an output argument, and for the
  helpers it calls on the same class chain (up to three levels). A function assigned to one of the
  three members is matched by its full signature, so an overload with other parameters does not count
  (`ObExprVectorSimilarity::calc_similarity` has a three-argument version, which its `cg_expr`
  installs and which never ran, and a four-argument one that the L2, cosine and inner-product
  subclasses call). A reference to the dispatch table `OB_DATUM_CAST_MYSQL_IMPLICIT` expands to the
  functions in the table. When a shared base installs a different function per subclass
  (`ObExprTimeBase` for `hour`, `minute`, ...), only the subclass's own function counts.
- For 11 entries no installed function could be read (for example `ObExprAdd`, which fills function
  tables); for those every member function of the class and its expression-specific bases counts,
  except constructors, destructors, type deduction and other compile-time members. 4 have no
  functions at all (`to_type`, `sys_view_bigint_param`, `agg_param_list`, `get_path`) and 1 is not
  compiled into release builds (`errno`); these count as unreached. `reach_basis` names the rule, the
  function that installs the evaluation functions and, when the class check fails, why.
- The class check is what keeps shared code from marking an entry reached. It moved 6 entries that
  an earlier version marked reached to unreached: `+` (`T_OP_AGG_ADD`), `strcmp`, `mid`, `rtrim` and
  `vector_similarity` were never created (their `alloc<Class>` count is 0; they share functions with
  `ObExprAdd`, the comparison helper `ObExprCmpFuncsHelper::get_eval_expr_cmp_func`, `ObExprSubstr`,
  `ObExprLtrim` and the three similarity subclasses of `ObExprVectorSimilarity`), and `ceiling` was
  created 4 times (by the `--error 1210` statements at geometry/t/geometry_compare_mysql.test:222-224)
  but its own type deduction never ran.
- Limits: a function reached through a helper that picks it at code generation (a call that
  receives `eval_func_` as an output argument, such as the comparison helper) counts as run when the
  helper ran; the class check above keeps that from counting for a class that was never created or
  never compiled. `sha1` shares class and functions with `sha`. The profile is from 076eb309b, which differs from 834bbee1e in 23
  files, none of them an expression file.

## What each statement records

Every file starts with `--disable_abort_on_error`, `--enable_metadata` and `--enable_warnings`, and
every statement is followed by `--echo errno $mysql_errno`:

- **Value:** the result rows.
- **Result type:** `--enable_metadata` prints the column definition the server sends for each result
  column: MySQL type code, length, maximum length, nullability, flags (unsigned, binary, not null),
  decimals and character set number. This is the result type of the very statement whose value is
  recorded, for every statement: the session runs with the plan cache off ("Determinism"), so no
  statement reuses the column definitions of an earlier one. It does not tell some types apart: a
  collection result is sent as `MYSQL_TYPE_STRING` with no flags, like CHAR
  (src/query/protocol/ob_mysql_protocol_util.cpp:87), a geometry result is always
  `MYSQL_TYPE_GEOMETRY` without its subtype or SRID, and ENUM and SET
  results show only `ENUM_FLAG` or `SET_FLAG`, not their values. So the s6 files also turn 162
  expressions into columns: one `CREATE TABLE tctNN AS SELECT <expr> AS e FROM tm WHERE 1 = 0` per
  expression, so one expression that fails cannot hide the others, and one query per group of up to
  16 tables reads `COLUMN_TYPE` (with the element type of an array, the value list of an ENUM or SET
  and the geometry subtype), nullability, default, character set, collation and `SRS_ID` from
  `information_schema.COLUMNS`. 66 of the 162 are arrays, maps, vectors, geometries with and without
  an SRID, and ENUM and SET results; the other 96 cover the casts, arithmetic, string, temporal,
  numeric, control-flow, JSON and spatial functions. `CREATE TABLE ... AS SELECT` is not used for
  every probe: it would add at least one statement per probe, which would put the corpus past 35,000
  statements, and it adds a column conversion (its own casts, warnings and errors) between the
  expression and the recorded type.
- **Warnings:** mysqltest runs `SHOW WARNINGS` after every statement whose warning count is not zero
  and prints the rows (level, code, message) under `Warnings:`. The server keeps at most 64 warnings
  per statement and overwrites the oldest (src/oblib/lib/oblog/ob_warning_buffer.h:141, 175-176,
  207). Neither `SHOW COUNT(*) WARNINGS` nor `@@warning_count` can show more: the first counts the
  rows of the warning virtual table, which holds only the readable ones
  (src/observer/virtual_table/ob_virtual_warning.cpp:86), and nothing ever updates the second (it is
  defined in src/share/system_variable/ob_system_variable_init.cpp:1250 and read nowhere else). So
  the statements that could pass 64 warnings are split instead: the s4 comparisons that convert
  strings to numbers or temporal values put one operator in each statement (see "The cast
  matrices"), so the 64 row pairs of a cross join give at most one warning each.
- **Error code:** with `--disable_abort_on_error` a failing statement is recorded as
  `ERROR <sqlstate>: <message>` and the run goes on. That line has no error number, so the `errno`
  line after each statement prints `$mysql_errno` (0 on success). Per-statement `--error` lists taken
  from the C++ run were not used: they need a second generation pass after the first run, and a
  Rust build with a different code would abort the rest of the file instead of recording it.
- **A statement over several rows that fails records only the error.** A failed result prints
  neither metadata nor rows, so one failing row hides the values, types and warnings of the others.
  For the entries whose evaluation functions raise an error on particular values, the rows that can
  raise it are probed one per statement and the other rows together
  (`WHERE id NOT IN (...)`, then `WHERE id = n` for each): `+`, `-`, `*`, `/` and `DIV` (overflow of
  the BIGINT, BIGINT UNSIGNED, DOUBLE and DECIMAL results at the minimum, maximum and zero rows; YEAR
  and BIT arithmetic is unsigned, ARITH_RESULT_TYPE in ob_expr_arithmetic_result_type.map), unary
  minus and `ABS` (`INT64_MIN` and unsigned values above 2^63, ob_expr_neg.cpp:88-104,
  ob_expr_abs.cpp:170), `EXP` (results that overflow, ob_expr_exp.cpp:53-54), `POW` (a negative base
  with the exponent 0.5, and exponents that overflow, ob_expr_pow.cpp:64-65), `COT` (zero, including
  strings that convert to 0, ob_expr_cot.cpp:57-58) and `FROM_UNIXTIME` (values whose microseconds
  do not fit in 64 bits, ob_expr_from_unix_time.cpp:311-315). The rows come from the matrix values
  (`ERROR_ROWS` in `generate.py`); a column whose non-NULL rows all fail keeps one statement. The same
  split applies to `CAST(... AS JSON)` over the six text columns in s3, per row that is not valid
  JSON (`CAST_ERROR_ROWS`, ob_datum_cast.cpp:3293-3307), so the rows that are valid JSON show their
  values. 73 sweeps are split this way, into 158 single-row statements.
- **The sweeps split from the reference's own run:** split-rows.tsv lists the sweeps that fail on the
  reference although rows other than the all-NULL one succeed: 221 over `tm` and 35 over
  `tm a, tm b` (found by `split_rows.py` on 2026-09-25; they hid 1,145 values). `cases` replaces each
  by a statement over the rows that succeed (`WHERE id NOT IN (...)`, or
  `(a.id, b.id) NOT IN ((...), ...)`) and one statement per failing row; for a join with more than 8
  failing pairs, one statement per `a.id` that has failing pairs. A sweep that fails on every row
  but the all-NULL one fails for its types, not its values, and is left whole.
- **Rows left out:** a few rows make the reference read memory it never wrote, so their values
  change from run to run or are values the reference never computed. The generator leaves them out
  of their sweeps (`unstable_rows` in a specification, `CAST_UNSTABLE_ROWS`), with the evidence in
  "Live check, 2026-09-25", "Not comparable".
- **SQL NULL and the string 'NULL':** mysqltest prints both as `NULL`. `JSON_TYPE` (JSON null),
  `QUOTE` (a NULL argument), `DUMP` and `UPPER` (the JSON null of the matrix as text) can return the
  string, so their probes carry a second column, `(<expr>) IS NULL AS n`.

## The type matrix

Each file that needs it creates table `tm` with 40 typed columns and 8 rows in one `INSERT`, and
drops it at the end. Rows: 1 all NULL; 2 zero or empty; 3 minimum; 4 maximum; 5 a typical value; 6 a
negative or other typical value; 7 a rounding edge (0.5, xx:59:59.5); 8 a special value (multibyte
text, the smallest normal double, and so on).

| Columns | Types |
|---|---|
| integers | TINYINT, TINYINT UNSIGNED, SMALLINT, MEDIUMINT, INT, INT UNSIGNED, BIGINT, BIGINT UNSIGNED |
| approximate | FLOAT, DOUBLE |
| exact | DECIMAL(9,2), DECIMAL(18,6), DECIMAL(38,10), DECIMAL(65,30) (the 32-, 64-, 128- and 256-bit decimal paths) |
| strings | CHAR(16), VARCHAR(64), BINARY(4), VARBINARY(64), TINYTEXT, TEXT, MEDIUMTEXT, LONGTEXT, TINYBLOB, BLOB, MEDIUMBLOB, LONGBLOB |
| temporal | DATE, TIME, TIME(6), DATETIME, DATETIME(6), TIMESTAMP(6), YEAR |
| other | BIT(64), ENUM, SET, JSON, GEOMETRY, VECTOR(3), ARRAY(INT) |

Each file that creates `tm` first loads the spatial reference systems, as the geometry suite does
(tools/deploy/mysql_test/test_suite/geometry/t/st_aswkb_mysql.test:13-19): with the query and result
logs off, it sources tools/deploy/mysql_test/test_suite/geometry/t/default_srs_data_mysql.sql when
`oceanbase.__all_spatial_reference_systems` does not already hold 5160 rows (the file's 5159 rows
and SRID 0 from bootstrap), then records `SELECT COUNT(*) = 5160 AS srs_loaded ...`. Without them
every SRID other than 0 fails with 3548 "Spatial reference system is empty" (after bootstrap the
table holds only SRID 0, `ObDDLOperator::init_srs`, and `ObSrsService::fetch_all_srs` loads nothing
from fewer than 5152 rows, src/observer/omt/ob_srs_service.cpp:196-202). The first file of a run
loads them in under half a second; later files find them loaded and print the same.

The empty array of row 2 is the string `'[]'`: the grammar has no empty array literal (`'['
expr_list ']'` at sql_parser_mysql_mode.y:3172 needs one expression, as do `ARRAY(...)` and
`MAP(...)`), and the array suite inserts empty arrays the same way (array/t/array_ddl_mysql.test:533).
Where an empty array is an argument, the corpus uses `ARRAY_REMOVE([1], 1)`, which the array suite
shows returns `[]` (array/r/mysql/array_ddl_mysql.result:686-703). The statement checks reject `[]`,
`ARRAY()` and `MAP()` outside quotes.

Probes of a function applied to the matrix use 20 of the columns (`CORE_COLUMNS`: one or two per
type class) as the first argument, one statement per column, `ORDER BY id`. JSON, spatial, vector
and array functions use 7 general columns plus their own types. The cast and store sections use all
40.

Literal values come from named domains (the letters in the TSV's `sql` column): `A` any type (NULL,
0, -2.5e0, 18446744073709551615, '', ' 12.5x', multibyte text, X'00FF', a DATE, a TIMESTAMP with
microseconds, a JSON value), `N` numbers, `I` positions and scales, `CNT` small counts, `S` strings,
`T` temporal values (including '0000-00-00', '2024-02-30', 20240229134556.5 and TIME'838:59:59'), `J`
JSON documents, `JP` JSON paths, `G` geometries, `WKT`/`WKB`, `SRID`, `V` vectors, `AR` arrays,
`MAP` maps, `UNIT` interval units, `FMT` date formats, `RX` regular expressions, `LK` LIKE patterns,
`CS` character sets, `COLL` collations, `TZ` time zones, `KEY` encryption keys, and a few narrower
ones defined next to them in `generate.py`.

## The probes for one entry

- A function or operator: the first argument over the matrix columns; the first argument over its
  literal domain; every other argument over its domain with the others at a typical value, plus
  three columns (DOUBLE, VARCHAR, DATETIME(6)); every allowed number of arguments; and one call with
  an argument too many. A trailing `*` in the TSV means a variable argument list, probed with 0, 1
  and 3 extra arguments.
- Where the first argument is not the one to sweep, the specification names the argument that is:
  the array argument of `ARRAY_FILTER`, `ARRAY_FIRST`, `ARRAY_MAP` and `ARRAY_SORTBY` (the first is a
  lambda) goes over the array columns (`c_arr`, `c_vec` and the 7 general ones), and the array of
  `MEMBER OF` goes over the JSON columns, `c_json` included.
- `CAST` itself is probed with one value per target (`CAST(1 AS type)`); its source by target matrix
  is s3. `sha1` has the same class and evaluation function as `sha`, so it gets only a name call, an
  argument too many and one column.
- Functions whose output is shown through `HEX()` or `ST_AsText()` in the main probes (the
  encryption functions, `COMPRESS`, `UUID_TO_BIN`, the WKB writers and the geometry constructors,
  transforms and set operations) are also probed bare: mysqltest records raw bytes exactly, and the
  bare result keeps its own type (binary flag, character set 63) and, for geometries, the 4-byte
  SRID in front of the WKB, which `ST_AsText` drops.
- Special forms and context-dependent functions have hand-written statements: `VALUES()` in
  `INSERT ... ON DUPLICATE KEY UPDATE`, `DEFAULT()`, `AUTO_INCREMENT` with `LAST_INSERT_ID()`,
  `ROW_COUNT()` and `FOUND_ROWS()` after DML and SELECT, user and system variables, scalar and
  `ANY`/`ALL`/`EXISTS` subqueries, user-level locks, stored functions and procedures, triggers,
  fulltext `MATCH ... AGAINST`, partitioned-table DML, parallel DML and a join filter, and the
  `information_schema` views that call the column and table printers.
- Internal entries that the optimizer creates are reached through comparisons on an indexed copy of
  the matrix (`ti`): `demote_cast`, `range_placement`, `inner_double_to_int`,
  `inner_decimal_to_year`, `align_date4cmp`, `inner_row_cmp_value`, `inner_is_true`,
  `inner_decode_like`.
- `spiv_dim` and `spiv_value` are the generated columns of a sparse vector index
  (src/observer/schema/ob_schema_service_sql_impl.cpp:6391-6394). A table with a `SPARSEVECTOR`
  column is filled, a `CREATE VECTOR INDEX ... WITH (type=sindi, distance=inner_product)` builds the
  index over the existing rows (the index build scan, src/sql/engine/table/ob_table_scan_op.cpp:3371),
  and an INSERT, an UPDATE and a DELETE maintain it; the queries are exact, not `APPROX`.
- `sys_privilege_check` and `sql_mode_convert` read their arguments as a fixed type without
  converting them, so they are probed only with arguments of a type they can read (integers for the
  routine type of `sys_privilege_check`; values whose datum holds 8 bytes for `sql_mode_convert`).
  "Live check, 2026-09-25", items 1 and 7, has the evidence.
- Not generated:
  - `to_outfile_row` beyond its name call: nothing in 834bbee1e creates `T_OP_TO_OUTFILE_ROW`
    (outside its own file it appears only in the registration, ob_expr_operator_factory.cpp:773, and
    in a printer branch, ob_raw_expr.cpp:2705). `SELECT ... INTO OUTFILE` is written by
    `ObSelectIntoOp`'s file writers without it, and its files belong to family 6 and item 8
    (judge/golden-bytes-scope.md).
  - `vec_chunk` and `embedded_vec` beyond their name calls: they need an embedding model, which
    means a network service. The other vector index internals (`vec_*`) and `doc_id` get only calls
    without arguments, because an argument is read as a tablet id and draws a value from that
    tablet's auto-increment service.
  - `sleep`, `benchmark`, the GTID waits and the random-data generators only with small literal
    arguments.

## The cast matrices

- **s3_cast, explicit casts (ob_datum_cast.cpp):** every one of the 40 matrix columns cast to the 14
  main targets, one per target in the grammar's `cast_data_type` rule (`BINARY`, `CHAR`,
  `CHAR CHARACTER SET binary`, `DATETIME(6)`, `DATE`, `TIME(6)`, `YEAR`, `NUMBER`,
  `DECIMAL(65,30)`, `SIGNED`, `UNSIGNED`, `DOUBLE`, `FLOAT`, `JSON`); the 8 geometry targets over 7
  source columns and NULL; 21 other spellings and precisions (`CHAR(3)`, `CHAR(4) CHARSET utf8mb4`,
  `DATETIME(3)`, `DECIMAL(10,2)`, `FIXED`, `NUMERIC`, `NCHAR`, `NATIONAL CHAR`, `FLOAT(30)`, ...) over 7
  columns;
  the 11 literal kinds of domain `A` to the 14 main targets; two `CAST(... AS NUMBER)` results as
  sources to the 14 main targets (the grammar gives `NUMBER`, `DECIMAL`, `FIXED` and `NUMERIC` the
  same cast type); the `CONVERT` spellings; and `CAST(... AS type IGNORE)`. The grammar accepts
  `IGNORE` (`sys_view_cast_opt`, sql_parser_mysql_mode.y:2290-2296), but the resolver accepts it only
  in inner sessions, system views and SHOW statements and returns a syntax error for user SQL
  (ob_raw_expr_resolver_impl.cpp:5070-5077), so its two probes record that error. The target list is
  parsed from the grammar, and a target added or removed there stops the generator. seekdb has only
  the `utf8mb4` and `binary` character sets (`SHOW CHARACTER SET` on the reference), so the character
  set targets use `binary` and `utf8mb4`, and `latin1` and `gbk` keep one literal probe each, which
  records 1115 "Unknown character set".
- **Type classes the corpus does not reach as sources:** with the defaults of 834bbee1e
  (`_enable_mysql_compatible_dates` and `_enable_decimal_int_type` both true), DATE and DATETIME
  columns and literals are `ObMySQLDateType` and `ObMySQLDateTimeType` and DECIMAL values are
  decimal-int types, so `ObDateType`, `ObDateTimeType` and `ObNumberType` appear only where a
  function returns them or under those cluster parameters set to false, which the corpus does not
  change. The Oracle-only classes (OTimestamp, Raw, Interval, RowID, Lob, UDT) have no MySQL
  syntax.
- **s4_compare, implicit casts in comparisons:** every pair of 25 column types (325 pairs, counting
  each type with itself) under `=`, `<` and `<=>` over the 8 x 8 cross join of the matrix: the 20
  core columns plus DECIMAL(18,6) and DECIMAL(38,10) (so all four decimal widths are compared with
  each other, `EVAL_DECINT_CMP_FUNCS` in ob_expr_cmp_func.cpp), CHAR, SET and ARRAY(INT). 171 pairs
  put the three operators in one statement. 154 pairs get one statement per operator: those with
  GEOMETRY, VECTOR, ARRAY or JSON, because `<` fails at type deduction for geometry and collection
  operands whatever the other one is (ob_expr_operator.cpp:1928-1958) and would hide `=` and `<=>`;
  and those between CHAR, VARCHAR, VARBINARY or TEXT and a numeric or temporal column, whose
  conversions warn once per row pair. `<` for a geometry or collection column is kept only against
  `c_int` and against the other geometry and collection columns, since the check that fails depends
  only on those types. The remaining 15 column types are not compared: comparison functions are
  chosen by type class (`EVAL_TC_CMP_FUNCS`), by collation for strings and text, and by width for
  decimals, so the small and unsigned integers, BINARY, the other text and BLOB types, TIME and
  DATETIME use the same functions as a compared column of their class.
- **s5_arith, implicit casts in arithmetic:** `+` over all 325 pairs of the same 25 columns; `-`, `*`,
  `/` over 13 numeric, string, temporal and array columns (all four decimal widths among them); `DIV`
  and `%` over 7. The rows are chosen per operator so that overflow does not end most statements
  early. For the result types alone, 126 statements with `WHERE 1 = 0` put every pair of 21 column
  types in one statement per left type and operator. They are not split per pair: no pair of these
  types is `ObMaxType` in the result-type tables of `+`, `-`, `*`, `/`, `DIV` and `%`
  (ob_expr_arithmetic_result_type.map, ob_expr_div_result_type.map,
  ob_expr_int_div_result_type.map, ob_expr_mod_result_type.map), so type deduction cannot fail for one
  item, and `WHERE 1 = 0` evaluates no row.
- **s6_store, assignment casts and the object cast path (ob_obj_cast.cpp):**
  - strict `INSERT` of 10 literal kinds into each of the 40 column types, and `INSERT IGNORE ...
    SELECT` from 6 source columns into the 20 core column types (the column conversion in
    `ObExprColumnConv`);
  - column defaults, which go through `ObObjCaster::to_type` (ob_ddl_resolver.cpp:2784, 3514, 3938,
    4154): for each of 9 literal kinds (0, -2.5, 2.5e0, 18446744073709551615, '12.5abc', multibyte
    text, X'00FF', a TIMESTAMP and a TIME literal) a table with the 40 column types gets one
    `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT` per column, then `SHOW CREATE TABLE` prints every
    default as converted. The grammar allows only literals there (`signed_literal`, with a sign only
    before integer and decimal numbers), so the kinds differ from the INSERT ones: no `-2.5e0` and no
    `CAST(... AS JSON)`;
  - range predicates on 14 indexed column types against 10 constant kinds with `=` and `>`. The range
    extractor converts the constant with `ObObjCaster::to_type` (ob_range_generator.cpp:1177) only
    when the cast between the column type and the comparison type is monotonic in both directions
    (ob_query_range_define.cpp:1897-1903); the other pairs fall back to a filter;
  - partition pruning with constants of other types; system variable assignment from other types;
  - the `CREATE TABLE ... AS SELECT` statements above.

## now() and temporal values (s7_temporal)

Table `tn` has DATETIME(0) to DATETIME(6), TIMESTAMP(0, 3, 6), TIME(0, 3, 6) and DATE columns.

- `NOW()`, `NOW(1)`, `NOW(3)`, `NOW(6)`, `CURRENT_TIMESTAMP`, `LOCALTIMESTAMP(4)`, `UTC_TIMESTAMP(6)`,
  `CURTIME(6)`, `CURDATE()`, `DATE_ADD(NOW(), INTERVAL -1 MICROSECOND)` and two more, each stored
  into DATETIME(0..6), under four session timestamps (.654321, .5, .499999, and
  2023-12-31 23:59:59.999999, where rounding crosses into the next year) and two sql_modes (the
  default, and with `TIME_TRUNCATE_FRACTIONAL`), followed by the comparisons of the quarantined
  subquery case (`d0 <= DATE_ADD(NOW(), INTERVAL -1 MICROSECOND)`, `d0 = NOW()`, ...). These values
  are dates in 2023 and 2024 and fit every scale, so each goes into all seven columns in one INSERT.
- Temporal literals with 6 and 7 fractional digits, decimal and double datetimes, `TIME` into
  DATETIME (which takes the current date), `FROM_UNIXTIME` with 7 digits and a value that rounds
  past 9999-12-31, into DATETIME(0..6) under both sql_modes and into the TIMESTAMP, TIME and DATE
  columns. These can fail, so each is stored with one `UPDATE` per column after an `INSERT` of the
  row's key: a value that fails at one scale cannot hide the others.
- `NOW(6)` under the time zones +08:00 and -05:30.
- On the real clock (`SET @@session.timestamp = DEFAULT`) only stable properties are printed:
  microseconds of `NOW(0)` are 0, `NOW(3)` is a multiple of 1,000 microseconds, `NOW(6)` is within a
  second of `NOW()`, the stored DATETIME(0) value is within a second of the DATETIME(6) one,
  `CURDATE() = DATE(NOW())`, and similar.

## Determinism

- Each file starts with `ALTER SYSTEM FLUSH PLAN CACHE` and then one `SET` statement: `NAMES utf8mb4
  COLLATE utf8mb4_general_ci` and, in the session, `time_zone = '+00:00'`, `sql_mode` = the
  834bbee1e default (`STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_AUTO_CREATE_USER`, from the system variable
  default 281018368), `timestamp = 1709214296.654321` (2024-02-29 13:44:56.654321 UTC),
  `div_precision_increment = 4`, `block_encryption_mode = 'aes-128-ecb'` and
  `group_concat_max_len = 1024`. `NAMES` may share a `SET` with variables: the grammar lists it among
  the `var_and_val` items and the resolver handles it inside a variable list
  (ob_variable_set_resolver.cpp:78).
- The plan cache stays on, as it is for every client by default, so each literal probe goes the path
  a client's statement takes: the literals are parameterized before the statement is resolved
  (`parameterize_syntax_tree`, src/sql/ob_sql.cpp:3327, reached only when the plan lookup ran,
  :1985 and :3258-3263), and a statement that differs from an earlier one of its file only in its
  literals reuses the earlier plan with its column definitions (`quote('Hello, World!')` after
  `quote('')` reports length 8, not 112). Which plan a statement finds must not depend on timing, so
  the flush at the start of each file empties the cache (the flush is synchronous: in the probe
  /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/probe-flush/rec/pcflush.result,
  `quote('Hello, World!')` right after a flush reports its own length, 112, and `quote('')` after it
  reuses that plan), and a file runs in well under the 30 seconds after which the
  eviction timer drops an idle plan (`IDLE_EVICT_THRESHOLD_US`, src/sql/plan_cache/ob_plan_cache.h:483),
  with far fewer plans than would fill the cache. So every statement's plan comes from its own file,
  in the file's order. Until 2026-09-28 each file turned the plan cache off instead (below, "Live
  check, 2026-09-28"). Plan cache hits themselves are family 7's.
- The fixed session timestamp makes `NOW()` and the functions built on the statement time
  deterministic: `ObPhysicalPlanCtx::set_cur_time` uses the session timestamp when it is set.
- Functions that read the clock or a random source are printed only as stable properties:
  `SYSDATE()`, `UUID()`, `UUID_SHORT()`, `RAND()` without a seed, `RANDOM()`, `RANDOM_BYTES()`,
  `CONNECTION_ID()`, `LAST_TRACE_ID()`, `LAST_EXECUTION_ID()`, `CURRENT_SCN()`, `OB_TRANSACTION_ID()`,
  `HOST_IP()`, `RPC_PORT()`, `MYSQL_PORT()`, `IS_USED_LOCK()`. Seeded `RAND(n)`, `RANDOM(n)` and the
  generators with a constant seed print values, since the same seed gives the same numbers.
- Every statement that can return more than one row has `ORDER BY` (the matrix id, or both ids of a
  join, or the value); the others return one row (aggregates, lookups by key).
- Each file is one mysqltest session, creates every table it uses and changes no global setting,
  so files do not depend on each other or on their order; lock names are unique to the corpus. The
  one shared thing is the spatial reference system table: a file loads it when it is not loaded,
  with its output off, so a file prints the same whether it loaded the table or an earlier file did.
- Nothing depends on timing: `SLEEP` gets 0 and 0.01, `BENCHMARK` small counts, the GTID waits a
  zero timeout (both are stubs that return NULL), and the AI functions a model name that does not
  exist, so they fail before any network call.
- Every statement is checked when the files are written: no semicolon (outside `CHAR(59)`) and no
  line break; balanced quotes; and outside quotes no comment marker (`#`, `-- `, or `/*` other than
  an optimizer hint `/*+`) and no `[]`, `ARRAY()` or `MAP()`, which the grammar rejects.

## Counts

| Section | Files | SQL statements | Of which probes |
|---|---|---|---|
| s1_unreached (291 entries, registry order) | 162 | 9,410 | 8,438 |
| s2_reached (236 entries) | 129 | 7,156 | 6,382 |
| s3_cast | 22 | 1,175 | 1,043 |
| s4_compare | 12 | 657 | 585 |
| s5_arith | 16 | 992 | 896 |
| s6_store | 36 | 1,739 | 1,523 |
| s7_temporal | 15 | 555 | 525 |
| s8_known (families/wire/known-answers.sql) | 1 | 56 | 56 |
| total | 393 | 21,740 | 19,448 |

"Probes" leaves out each file's plan cache flush and session `SET` statement, the check that the spatial reference
systems are loaded, and the creation, filling and dropping of `tm`. Files hold about 50 probe
statements before the splits of split-rows.tsv; an entry larger than that is split into parts, each
repeating the entry's own setup. File names have no dot before `.test`, as `--test-dir` requires, and
sort in the order above, unreached entries first.

s8_known_0001.test is a byte copy of families/wire/known-answers.sql (the CRC32 and COMPRESS known
answers of family 6 and item 8), which that file's README hands to this family; `cases` writes it,
`cases --check` fails when the copy is out of date, and `check-recording` requires every
known-answer column in its recording to be 1.

## Running the corpus

`--test-dir` is one of the second-set options of the live runner (harness/second-set/README.md).
Under the reduced init, retries off and the trailing-whitespace tolerance off:

```
python3 .github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb \
  --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --mysqltest /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest \
  --base-dir <base-dir> --work-dir <work-dir> --port <port> \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql \
  --test-dir migration/judge/families/expressions/cases --record-dir <record-dir>
```

Then `compare --left <record-1> --right <record-2>`, and `generate.py check-recording --record-dir
<record-1>` on the C++ recording. One run takes about 30 s. The runner does not fail a file when the
server dies in it: mysqltest records the lost connection (2013, then 2006) for the rest of the file
and exits 0, and the next file waits for the dead server until its mysqltest is killed.
`check-recording` fails on those client errors.

## Live check, 2026-09-25

Every run below used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb,
sha256 db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91) with the archive's obclient
and mysqltest, port 3892, the reduced init, `--max-retries 0 --no-ignore-trailing-whitespace`,
`--test-dir cases` and `--record-dir`; the check that tools/deploy and sdb.py are 834bbee1e's passed
before each. The outputs are in /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/02-expr/: one
directory per run (`rNN`), the command in run.sh, the comparisons in compare-*.log. This unit did
not change the runner. r01 to r03 ran the committed runner (sha256 9f417e4e13dc65e3...); from r04
on, the runs used the live file as its owner for this round had changed it at 09:54
(0b1e708d1ec88ffa..., which adds `compare`'s known-failure list and leaves the recording path as it
was), so r15 to r17 ran the same runner. The
row-by-row runs of `split_rows.py` and `stability.py` and the one-statement checks used a separate
instance on the same port (scratch.sh), never at the same time as a run. analysis/ there holds their
logs and the two small reproductions: pc.test (the plan cache and the column length) and stb2.test
(`_st_buffer` with a NULL distance, repeated).

### Result

- **Two recordings compare identical.** r15, r16 and r17 record the corpus as it now stands
  (`test_dir_sha256` 1516b7f988e0b11b0015813f762c0156d2981b9f04a9f6692089ab456b339c80): `compare`
  exits 0 for each of the three pairs, 393 of 393 identical, no recording problems.
  `check-recording` exits 0 on each: 0 problems. Earlier triples were identical too, each at the
  corpus of its day: r04-r06, r09-r11 and r12-r14.
- **Counts (r15).** 21,348 statements: 21,292 generated statements, each followed by its `errno` line,
  and 56 in s8_known_0001. 5,079 of the generated statements recorded an error (89 different error
  codes) and 16,213 succeeded. 1,378 statements recorded warnings, 11,705 warning rows in all; the
  largest count for one statement is 56, below the server's limit of 64.
- **Known answers.** s8_known_0001 (families/wire/known-answers.sql): no error, and all 40
  known-answer values are 1: 17 `is_known_answer` (8 CRC32, 9 COMPRESS), 4 `is_known_input`, 4
  `is_known_answer` for the long inputs' COMPRESS md5, 4 `header_matches`, 4 `round_trip`, 2
  `known_bytes_inflate`, `empty_is_0`, 2 `null_is_null`, `empty_stays_empty` and `is_empty`. The
  960,000-byte input ran, and the NULL, warning and header-mismatch lines are recorded (and compare
  identical like the rest).
- **Stable within a session.** `stability.py --repeats 4` on the final corpus: 16,669 probes, none
  unstable. With the four unstable probes found below put back into one file, it reports all four
  and exits 1.
- **Failed statements.** `check-recording` lists 2,005 failed statements on r15. After the split
  (below) none of them is a sweep that hides a row other than the all-NULL one; they fail for their
  types or on every value.

### What the runs found and what changed

1. **r01, the corpus as first generated: the reference crashed.** SIGSEGV in
   `ObExprSysPrivilegeCheck::eval_sys_privilege_check` + 684 (crash report
   ~/Library/Logs/DiagnosticReports/seekdb-2026-09-25-093156.ips) at
   `SELECT sys_privilege_check('table_acc', 1, 'test', '') AS v` in s2_reached_0069; it recurs on a
   fresh instance. `calc_result_typeN` gives the arguments their types with `set_type`, not
   `set_calc_type` (src/sql/engine/expr/ob_expr_sys_privilege_check.cpp:53-65), so nothing converts
   them and each datum is read as the declared type; the fourth, the routine type, is read with
   `get_int()` (:132), and the empty string's datum has no data pointer (the faulting instruction is
   `ldr x5, [x8]`). The specification also had the arguments out of order: they are level, database,
   object and routine type (the information_schema views call `sys_privilege_check('table_acc',
   D.DATABASE_NAME, T.TABLE_NAME)`). Now: `PRIVLEVEL` (the three levels, upper case, bad values),
   `S`, `S`, `ROUTINETYPE` (integers and NULL only), the last three optional. A string routine type
   crashes or reads past its bytes and a decimal is 4 bytes read as 8, so neither is probed. All 35
   probes ran one by one on a separate instance without a crash.
   The crash also showed two things about the harness: mysqltest does not fail a file when the
   server dies in it (s2_reached_0069 "passed", recording 2013 and then 2006 for its remaining
   statements), and the next file waited for the dead server with mysqltest at full CPU until that
   mysqltest was killed (after 91 s). Two C++ recordings would agree on such a crash, so
   `check-recording` now fails on any client error 2000-2999.
2. **r01: 76 statements did not parse.** Most were meant: an argument too many where the grammar
   fixes the form (`year(t, 1)`, `weight_string('abc', 1)`, `hash(1)`, ...), `char()`, `NOW(-1)`, and
   `statement_digest` of text that is not SQL. Three were generator mistakes:
   - `VECTOR_DISTANCE` and `VECTOR_SIMILARITY` with `euclidean_squared` and `hamming`: the grammar's
     metrics are `COSINE`, `DOT`, `EUCLIDEAN` and `MANHATTAN`
     (src/sql/parser/sql_parser_mysql_mode.y:3244-3302). Removed from domain `METRIC`.
   - `x <=> ANY | ALL (subquery)`: the grammar has no subquery flag after `COMP_NSEQ` (:1205). The
     resolver makes `T_OP_SQ_NSEQ` when a `<=>` operand is a subquery of more than one column
     (src/sql/resolver/expr/ob_raw_expr_info_extractor.cpp:311-330), so `subquery_null_safe_equal`
     is now probed with row subqueries, `(c_int, c_varchar) <=> (SELECT c_int, c_varchar ...)`.
   - `COLLATE binary`: `BINARY` is a keyword and a collation name is `NAME_OB` or `STRING_VALUE`
     (:5995-6007). Now `COLLATE 'binary'`, which records 1253.
3. **r01: probes that could not reach their functions.**
   - seekdb has only the `utf8mb4` and `binary` character sets and three collations (`SHOW
     CHARACTER SET`, `SHOW COLLATION`). `CAST(... AS CHAR CHARACTER SET latin1)` failed with 1115
     for all 40 columns, 11 literals and 2 derived sources, and `CHAR(4) CHARSET gbk` for 7 more. The
     main target is now `CHAR CHARACTER SET binary`, the variant `CHAR(4) CHARSET utf8mb4`, the
     `CONVERT ... USING` and `CREATE TABLE ... AS SELECT` probes use `binary`, and `latin1` and `gbk`
     keep one literal probe each.
   - 427 statements with SRID 4326 or 3857 failed with 3548 "Spatial reference system is empty",
     because the reduced init, like the full one, loads no spatial reference systems. Every file
     with the matrix now loads them ("The type matrix"). 3548 remains only for SRIDs that do not
     exist (30 statements: SRID 1, and the SRIDs read from the first bytes of strings taken as
     geometries).
   - With them loaded, SRID 4326 reads latitude first, so the points written `POINT(116.4 39.9)`
     failed with 3617 (latitude out of range) before the function under test ran. They are now
     `POINT(39.9 116.4)` and `POINT(31.2 121.5)`; one probe keeps the error. `ST_Transform` to 3857
     fails with 3742 (not supported), so the `CREATE TABLE ... AS SELECT` for its type uses 4269, and
     4269 and 4490 (3744, no TOWGS84 clause) were added as probes.
   - `ARRAY_SORTBY` takes one lambda argument per array (30 of its 31 probes failed with 1582): one
     array with the one-argument lambdas now, and extras with two arrays and two-argument lambdas.
     `AI_PROMPT`'s arguments must be strings (29 of 30 failed with 5083): typical values `'a'` and
     `'b'` now. `POLYGON`'s typical ring had two points (3037 for all but one): a closed four-point
     ring and a hole now.
4. **r02 against r03: 7 files differed.**
   - Six in the length of the column definition, in statements with a literal argument (`quote`,
     `concat_ws`, `nullif`, `any_value`, `word_segment`, `weight_string`). With the plan cache on,
     `SELECT quote('Hello, World!')` reuses the plan built for `SELECT quote('')` two statements
     earlier and reports length 8 instead of 112, if that plan is still cached; eviction runs on a
     timer while the corpus compiles about 20,000 plans. On a separate instance the pair gave 8 and
     8 with the plan cache on, 8 and 112 with it off. The session `SET` now turns the plan cache off
     ("Determinism"); the length is still recorded, now the statement's own.
   - One in a value: `time_to_sec(c_json)` row 7 printed 6171066624917 in r02, 7056807275233 in r03
     and 180891863066 in r01. Not comparable, see below; the row is left out.
5. **r04, r05, r06: identical.**
6. **The review of the failed statements (split_rows.py).** Without splits, 2,209 of the corpus's
   8,349 sweeps fail (run split-rows-03). Run row by row (pair by pair for `tm a, tm b`), 1,953 fail
   on every row but the all-NULL one, and 256 hide rows that succeed, 1,145 values in all: from
   `year(c_double)` (rows 3 and 4 fail) to `a.c_int + b.c_ubigint` (4 of its 25 pairs overflow).
   split-rows.tsv lists the 256;
   `cases` splits them (+990 statements). The first version counted the pairs a join's
   `a.id IN (...) AND b.id IN (...)` leaves out as succeeding (they return no row), which split 6
   joins whose pairs all fail; `split_rows.py` now probes only the pairs the condition selects.
7. **r07 against r08: s2_reached_0109 differed.** `_st_buffer(ST_GeomFromText('POINT(1 2)'),
   c_double, 'abc')` over rows 1, 2 and 8 failed with 1210 in r07 and succeeded in r08; repeated in
   one session it fails 1 time in 6. Not comparable, see below; row 1 is left out of the three
   distance sweeps. A first form of `stability.py` then found `sql_mode_convert(c_varchar)` and
   `sql_mode_convert(c_date)` changing between repeats in one session. Not comparable, see below;
   `sql_mode_convert` is now probed only with values whose datum holds 8 bytes (integers, a double,
   `DECIMAL(18,6)`, `TIME`, `DATETIME`, `TIMESTAMP`, `BIT`, `ENUM`) and NULL.
8. **r09, r10, r11: identical. r12, r13, r14: identical** (after the `ST_Transform` probes of item 3).
   **r15, r16, r17: identical** (after the correction of the split in item 6).

### Not comparable

The reference reads memory it never wrote in these places, so its output there is not a value to
match. The corpus leaves them out; a C++-against-Rust comparison must not count them if they come
back.

| What | Where in the source | Evidence | In the corpus |
|---|---|---|---|
| JSON string that is not a time, cast to TIME | `CAST_FUNC_NAME(json, time)` declares `int64_t out_val;` without a value (src/sql/engine/expr/ob_datum_cast.cpp:7969); for a JSON string `ObIJsonBase::to_time` sets nothing when `str_to_time` fails (src/oblib/common/json_type/ob_json_base.cpp:5402-5412); the failure becomes a truncation warning under `CAST_FAIL`, and `SET_RES_OBJ` stores the value for that warning (ob_datum_cast.cpp:194-212) | `time_to_sec(c_json)` row 7 (`"str"`): 180891863066, 6171066624917, 7056807275233 in r01, r02, r03; `CAST(c_json AS TIME(6))` row 7 prints 01:11:34.963288 and `time(c_json)` 838:59:59 in every run, values nothing computed | row 7 left out of `CAST(c_json AS TIME(6))`, `CAST(c_json AS TIME)`, `CAST(c_json AS TIME(2))`, `time(c_json)`, `time_to_sec(c_json)` |
| `_st_buffer` with a NULL distance from a column | `ObExprPrivSTBuffer::eval_priv_st_buffer` tests `geo_datum->is_null() \|\| geo_datum->is_null()` and never the distance (src/sql/engine/expr/ob_expr_st_buffer.cpp:707), then reads the NULL datum with `get_double()` (:737) | the statement over rows 1, 2 and 8 failed with 1210 in r07 and not in r08, and 1 time in 6 in one session; row 1 alone: 1 failure in 6 | row 1 left out of the sweeps of the distance over `c_double`, `c_varchar` and `c_datetime6` (a NULL literal is caught by the type check) |
| `sql_mode_convert` of a value whose datum is not 8 bytes | `ObExprSqlModeConvert::calc_result_type1` sets no calculation type (src/sql/engine/expr/ob_expr_sql_mode_convert.cpp:38-50) and the evaluation reads the argument with `get_uint64()` (:65) | `sql_mode_convert(c_varchar)` row 3, and `sql_mode_convert(c_date)` rows 3 and 7, changed between repeats in one session (error 1235 or a list of modes) | only integers, a double, `DECIMAL(18,6)`, `TIME`, `DATETIME`, `TIMESTAMP`, `BIT`, `ENUM` and NULL |
| `sys_privilege_check` with a routine type that is not an integer | item 1 above | the crash | only integers and NULL |

`CAST_FUNC_NAME(json, datetime)`, `(json, date)` and `(json, bit)` leave their value unset on the
same path (ob_datum_cast.cpp:7842, 7907 and 8095). The corpus's JSON-to-DATE and DATETIME probes
print NULL on row 7 in every run (with the default MySQL-compatible dates they go through the mdate
and mdatetime casts), and no probe casts JSON to BIT.

### Live check, 2026-09-28: the plan cache back on

A review of the second set found that turning the plan cache off also turned off literal
parameterization (ob_sql.cpp:1985, :3258-3263 and :3327), so the family no longer checked the path a
client's statements take by default, or the reuse of a cached plan's column definitions. The corpus
now keeps the plan cache on and flushes it at the start of every file ("Determinism"); `generate.py
cases` adds the flush to 392 files (s8_known_0001, a copy of known-answers.sql, has no session
statements), 21,740 statements in all, `test_dir_sha256`
`84db8209ee4355085d2fa71d56d7e8c0fa1ceb4275dcd1cc246a97ec1374b11b`. Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/ (expr-r18 to expr-r20, expr-split-rows,
expr-stability, expr-stability-2, the compare-expr-* files), the same reference, clients, port 3892
and options as before.

- **split_rows.py with the plan cache on** (port 3893, a scratch instance): 8,349 sweeps, 2,209
  failed, 256 split, and the split-rows.tsv it wrote is byte-identical to the checked-in one.
- **stability.py** first reported 9 probes as unstable (expr-stability.log): `left('abc', 1.5)` and
  eight `WHERE c_double = '12.5x'`-style comparisons printed their truncation warnings on the first run
  and fewer on the repeats. That is the plan cache, not memory read without being written: the
  warnings come from compiling, and a repeat finds the cached plan. The tool looks for probes that read
  memory they never wrote, so it now turns the plan cache off for its own run (a hidden `SET` at the
  top of each repeated file), so each repeat compiles again as the first run did; the corpus itself is
  unchanged by this. With that, 16,669 probes, 3 repeats each, 0 unstable (expr-stability-2).
- **Three recordings, r18, r19 and r20, compare identical**: `compare` exits 0 for each of the three
  pairs, 393 of 393 identical, no recording problems; `check-recording` finds 0 problems in each
  (2,005 failed statements to review, as before).
- **What the parameterized path changes, against r15** (plan cache off; statement by statement,
  leaving out the flush and the `SET`): 370 statements report other column definitions (a length
  taken from the plan an earlier statement of the file compiled, as in the `quote` pair above), 8
  print fewer warnings (the ones raised while compiling, for a statement that found an earlier
  statement's plan), and 12 print another value or error message (one of them with errno 0 where r15
  had 1210), all of them what a client sees by default:
  - the out-of-range message shows the parameterized expression: `c_bigint - 2` fails with
    `BIGINT value is out of range in '(-9223372036854775808 + -2)'`, where r15 printed
    `'(-9223372036854775808 - 2)'` (s2_reached_0023, s2_reached_0024; 7 statements);
  - `TABLE(GENERATOR('3'))` returns 3 rows, where r15 failed with 1210 "The argument should be a
    constant integer" (s2_reached_0114), and `GENERATOR(NULL)` fails with another message;
  - a `TIME'23:59:59.5'` literal stored into DATETIME columns of every scale gets the date
    1970-01-01, where r15 had the session date 2024-02-29 (s7_temporal_0006, _0009 and _0013).
  A translation that resolved literals as constants, or that did not reuse a cached plan's column
  definitions, now shows up as a difference.

### The mutation for the second sign-off

migration/judge/mutations/second-set/02-expr-make-set-drops-last-string.patch, with its note beside
it: `MAKE_SET` never returns its last string (an off-by-one in its bit mask,
src/sql/engine/expr/ob_expr_make_set.cpp:98). MAKE_SET is one of the unreached entries: the 272
configured cases never run the file, and 104 result rows of 41 statements in s1_unreached_0053 and
s1_unreached_0054 change. Caught on 2026-09-28 by the corpus of that day, and again on 2026-09-29 by
the corpus with the plan cache on: against expr-r18, those two files differ in the same 41 statements
and 104 rows, the other 391 are identical (the note gives both runs).

The argument meanings of the OceanBase-specific functions (arrays, maps, vectors, private `_st_`
spatial functions, AI functions) were taken from their type-deduction code, not from documentation;
where a guess is wrong the probes record the error each build returns. Items 1 and 3 above corrected
the ones the first run showed wrong.
