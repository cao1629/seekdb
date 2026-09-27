# 01-ps: binary TIMESTAMP cells lose the session time zone

Family 8, the `--ps-protocol` replay (PLAN.md section 4, item 4). Patch:
01-ps-binary-timestamp-without-time-zone.patch, one .cpp file, 1 line added and 2 removed.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-25). Not built.

## What it changes

src/query/protocol/ob_mysql_protocol_util.cpp, `ObSMUtils::build_cell_value`, `case ObDateTimeTC`,
the `BINARY == type` branch (lines 358-368 at 834bbee1e). Before the change, a TIMESTAMP value is
turned into its date and time parts in the session's time zone, as the text branch below it does:

```
ObTimeConverter::datetime_to_ob_time(obj.get_datetime(),
    obj.is_timestamp() ? dtc_params.tz_info_ : NULL, ob_time)
```

After it, the binary branch passes `NULL`, so `add_timezone_offset` adds nothing
(src/oblib/common/timezone/ob_time_convert.cpp:4425-4436) and the parts are the stored UTC time.
A binary TIMESTAMP cell then carries UTC instead of local time. DATETIME values (ObDateTimeType)
passed `NULL` already and do not change; DATE and DATETIME columns go through
`ObMySQLDateTC` and `ObMySQLDateTimeTC` (`_enable_mysql_compatible_dates` is true by default,
src/share/parameter/ob_parameter_seed.ipp:319) and do not change either. The text branch
(lines 369-376) is untouched, so the text protocol prints exactly what it printed before. This is a
plausible porting mistake: the time zone is applied in one of the two encoders and forgotten in the
other.

## Why family 8 catches it

- Under `--ps-protocol`, mysqltest prepares every SELECT and reads its rows over the binary
  protocol. The live probe of 2026-09-25 (families/ps_protocol/README.md, "Live check, 2026-09-25")
  counted 2 prepares for 2 SELECTs in `oceanbase.__all_virtual_ps_stat.access_count`.
- The server encodes those rows as binary: `ObQueryDriver::response_query_result` sets
  `protocol_type` to BINARY when `is_ps_protocol` (src/observer/mysql/ob_query_driver.cpp:141);
  each cell goes through `ObSMRow::build_cell_value` (src/observer/mysql/obsm_row.cpp:40-52) into
  `ObSMUtils::build_cell_value(obj, BINARY, ...)`, and a packed plan's cells go through
  `ObExprOutputPack::build_row_values` into the same function with `encode_type` BINARY
  (src/sql/engine/expr/ob_expr_output_pack.cpp:334-391). The `dtc_params` in both paths come from
  `ObBasicSessionInfo::create_dtc_params`, so `tz_info_` is the session time zone.
- The session time zone is `+08:00`: the default of `time_zone`
  (src/share/system_variable/ob_system_variable_init.json, id 16), and neither tools/deploy/init.sql
  nor init_user.sql sets it. So every TIMESTAMP value a SELECT prints through the binary protocol
  moves back 8 hours; a zero TIMESTAMP stays zero.
- The output that changes, from the C++ `--ps-protocol` recordings of the live check
  (/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/01-ps/rec1 to rec6), whose TIMESTAMP values
  are the same in every recording and the same as the checked-in text results:
  - type_date.timestamp2, `select * from test_table;` (test lines 15 and 17): `school_date` is
    `timestamp(6)` and prints `1993-09-01 11:00:00.123400`; under the mutation
    `1993-09-01 03:00:00.123400`.
  - type_date.datetime_java, `select str_val, ts_val from dt order by ts_val, str_val;` and the
    first SELECT of the file: `ts_val timestamp(6)` values move back 8 hours.
  - type_date.daylight_saving_time (`t timestamp(6)`), two_order_by (`pk3`, `c3`), driver5114_bug
    (`t`), datatype.replace (`c4`), type_date.test_select_usec_to_time (`b`), update_range and
    delete.delete_range (`c` of t3): each has a `select *` of a table with a TIMESTAMP column whose
    values the .result prints. (a_trade_quick creates TIMESTAMP columns but prints none of their
    values.)
- `compare --require-ps-protocol` of the mutated build's `--ps-protocol` recording against a C++
  recording made with the archived binary then reports those cases `different`.

## Why the 272 configured cases do not catch it

They run over the text protocol only, and the mutated line is in the binary branch. In the coverage
profile of the 272 cases (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata,
binary /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb; the file is identical
at 076eb309b and 834bbee1e, `git diff --quiet 076eb309b 834bbee1e -- src/query/protocol/ob_mysql_protocol_util.cpp`
exits 0), `llvm-cov show -name-regex='ObSMUtils16build_cell_value'` gives, summed over both passes:
line 358 (`case ObDateTimeTC`) 592, line 359 (`if (BINARY == type)`) 592, line 361 (the binary
`datetime_to_ob_time`) 0, line 371 (the text `datetime_to_str`) 592. Every binary branch of the
function has 0 hits: the integer kinds (241), FLOAT (273), DOUBLE (298), DATETIME (360), DATE (381),
TIME (400), YEAR (424), BIT (531), MySQL DATE (606) and MySQL DATETIME (625), while the text
branches beside them ran 243k, 1.23k, 258k, 592, 40, 14, 84, 566 and 4.27k times (`ObDateTC` did not
run at all: DATE values are `ObMySQLDateTC`). A family 1 run of the mutated build, compared with
the .result files or with a text recording, is expected to pass.

## How the mutation stage runs it

As in ../README.md, "How to run them", with the family 8 run in place of the normal run: apply the
patch in /Users/colin/seekdb-dev/ref-834bbee1e, rebuild, copy the binary aside, and record the
family's cases with it the way families/ps_protocol/README.md records the reference
(`--ps-protocol`, `--case-list` cases.txt, `--record-dir`, retries 0), then

```
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/01-ps/rec5/rec \
  --right <the mutated recording> \
  --require-ps-protocol --known-failures $H/migration/judge/families/ps_protocol/known-failures.txt
```

`compare` notes the different `seekdb_sha256` and fails with the TIMESTAMP cases above as
`different`. Revert the patch afterwards.

## Caught by (filled after the run)

Not run yet.
