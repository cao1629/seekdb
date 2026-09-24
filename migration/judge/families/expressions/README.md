# Family 5: expressions and casts

PLAN.md section 4, family 5 and item 7. `generate.py` writes a mysqltest corpus into `cases/` that
runs every registered expression against a type matrix with NULL and edge values, both cast matrices,
and `now()` and other temporal values stored into DATETIME columns of every scale. The judge records
the corpus on the C++ reference and on the Rust build with the runner and compares the recordings
byte for byte (harness README, "compare").

The generator reads source files at 834bbee1e and, for the reach split only, the coverage profile.
It never talks to a server. The corpus has not been run yet (the injected-mutation runs occupy the
machine); see "Before the first comparison".

## Commands

```
python3 migration/judge/families/expressions/generate.py reach
python3 migration/judge/families/expressions/generate.py cases
python3 migration/judge/families/expressions/generate.py cases --check
python3 migration/judge/families/expressions/generate.py check-recording --record-dir <record-dir>
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
  found, or when a setup statement failed (the session `SET`, the creation and filling of `tm`, and
  each unit's own setup). It also lists, without failing, the multi-row statements, type-only
  statements and `CREATE TABLE ... AS SELECT` statements that failed (see "Before the first
  comparison"). It was tested only in process, on a recording written by hand in mysqltest's format.
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
  recorded, for every statement. It does not tell some types apart: a collection result is sent as
  `MYSQL_TYPE_STRING` with no flags, like CHAR (src/query/protocol/ob_mysql_protocol_util.cpp:87), a
  geometry result is always `MYSQL_TYPE_GEOMETRY` without its subtype or SRID, and ENUM and SET
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
  `CHAR CHARACTER SET latin1`, `DATETIME(6)`, `DATE`, `TIME(6)`, `YEAR`, `NUMBER`,
  `DECIMAL(65,30)`, `SIGNED`, `UNSIGNED`, `DOUBLE`, `FLOAT`, `JSON`); the 8 geometry targets over 7
  source columns and NULL; 21 other spellings and precisions (`CHAR(3)`, `DATETIME(3)`,
  `DECIMAL(10,2)`, `FIXED`, `NUMERIC`, `NCHAR`, `NATIONAL CHAR`, `FLOAT(30)`, ...) over 7 columns;
  the 11 literal kinds of domain `A` to the 14 main targets; two `CAST(... AS NUMBER)` results as
  sources to the 14 main targets (the grammar gives `NUMBER`, `DECIMAL`, `FIXED` and `NUMERIC` the
  same cast type); the `CONVERT` spellings; and `CAST(... AS type IGNORE)`. The grammar accepts
  `IGNORE` (`sys_view_cast_opt`, sql_parser_mysql_mode.y:2290-2296), but the resolver accepts it only
  in inner sessions, system views and SHOW statements and returns a syntax error for user SQL
  (ob_raw_expr_resolver_impl.cpp:5070-5077), so its two probes record that error. The target list is
  parsed from the grammar, and a target added or removed there stops the generator.
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

- Each file starts with one `SET` statement: `NAMES utf8mb4 COLLATE utf8mb4_general_ci` and, in the
  session, `time_zone = '+00:00'`, `sql_mode` = the 834bbee1e default
  (`STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_AUTO_CREATE_USER`, from the system variable default
  281018368), `timestamp = 1709214296.654321` (2024-02-29 13:44:56.654321 UTC),
  `div_precision_increment = 4`, `block_encryption_mode = 'aes-128-ecb'` and
  `group_concat_max_len = 1024`. `NAMES` may share a `SET` with variables: the grammar lists it
  among the `var_and_val` items and the resolver handles it inside a variable list
  (ob_variable_set_resolver.cpp:78).
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
  so files do not depend on each other or on their order; lock names are unique to the corpus.
- Nothing depends on timing: `SLEEP` gets 0 and 0.01, `BENCHMARK` small counts, the GTID waits a
  zero timeout (both are stubs that return NULL), and the AI functions a model name that does not
  exist, so they fail before any network call.
- Every statement is checked when the files are written: no semicolon (outside `CHAR(59)`) and no
  line break; balanced quotes; and outside quotes no comment marker (`#`, `-- `, or `/*` other than
  an optimizer hint `/*+`) and no `[]`, `ARRAY()` or `MAP()`, which the grammar rejects.

## Counts

| Section | Files | SQL statements | Of which probes |
|---|---|---|---|
| s1_unreached (291 entries, registry order) | 162 | 8,487 | 7,839 |
| s2_reached (236 entries) | 129 | 6,702 | 6,186 |
| s3_cast | 22 | 1,083 | 995 |
| s4_compare | 12 | 618 | 570 |
| s5_arith | 16 | 844 | 780 |
| s6_store | 36 | 1,667 | 1,523 |
| s7_temporal | 15 | 540 | 525 |
| total | 392 | 19,941 | 18,418 |

"Probes" leaves out each file's session `SET` statement and the creation, filling and dropping of
`tm`. Files hold about 50 probe statements; an entry larger than that is split into parts, each
repeating the entry's own setup. File names have no dot before `.test`, as `--test-dir` requires, and
sort in the order above, unreached entries first.

## Running the corpus

`--test-dir` comes from the runner change in migration/judge/harness/second-set/. The copy there
cannot run from its own directory (it finds tools/deploy and sdb.py from its own path), so the
command runs the live runner once `runner-second-set.patch` is applied to it (second-set README),
under the reduced init, retries off and the trailing-whitespace tolerance off:

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
<record-1>` on the C++ recording.

## Before the first comparison

The corpus was checked offline only (Python compile, generation twice, byte comparison, the
statement checks above). No server has parsed it. On the first two C++ recordings:

- `check-recording` must exit 0: every file recorded, and `errno 0` for the session `SET`,
  `CREATE TABLE tm`, the `INSERT INTO tm` and the setup of every unit (`ti`, `ts`, `td`, `tn`, `tsv`
  and the others). The matrix is one `INSERT`, so a value that the server rejects empties the whole
  table, and two C++ recordings would still agree; this check is what catches it (an earlier version
  lost rows 1-4 in every file to a bare `[]`);
- the two recordings must be identical, which is the check of the determinism rules above;
- the failed statements that `check-recording` lists are reviewed. A multi-row statement over `tm`
  that fails although some of its rows should not hides those rows: its rows go into `ERROR_ROWS`
  (or `CAST_ERROR_ROWS`). A failed type-only statement in s5 is split per pair. A failed
  `CREATE TABLE tctNN ... AS SELECT` leaves its expression without a column type;
- no statement may stop or crash the reference. Direct calls of internal and rarely used
  functions are the likeliest place; such a statement is removed from the specification table and
  the removal is noted here.

The argument meanings of the OceanBase-specific functions (arrays, maps, vectors, private `_st_`
spatial functions, AI functions) were taken from their type-deduction code, not from documentation;
where a guess is wrong the probes still record the error each build returns.

The second sign-off also needs a caught mutation for this family (PLAN.md section 4, "00b's exit"),
for example a changed cast rounding in ob_datum_cast.cpp.
