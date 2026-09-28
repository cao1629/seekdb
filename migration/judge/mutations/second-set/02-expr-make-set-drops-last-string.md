# 02-expr: MAKE_SET never returns its last string

Family 5, the expression and cast corpus (PLAN.md section 4, family 5 and item 7). Patch:
02-expr-make-set-drops-last-string.patch, one .cpp file, one line changed.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-25). Not built.

## What it changes

src/sql/engine/expr/ob_expr_make_set.cpp, `ObExprMakeSet::calc_make_set_expr`, line 98 at 834bbee1e.
`MAKE_SET(bits, str1, ..., strN)` keeps only the low N bits of `bits` before it walks them:

```
input_bits &= ((ulonglong) 1 << (expr.arg_cnt_ - 1)) - 1;
```

`arg_cnt_` counts `bits` as well, so N is `arg_cnt_ - 1`. The patch writes `arg_cnt_ - 2`: the mask is
one bit short, the bit of the last string is always cleared, and MAKE_SET never returns its last
string. With two strings, `MAKE_SET(3, 'a', 'b')` gives `a` instead of `a,b`; with one string the mask
becomes 0 and the result is always the empty string. The LONGTEXT branch, `calc_text` (line 139),
keeps the right mask. This is the kind of slip a translation of the mask arithmetic makes: the number
of strings is one less than the number of arguments.

## Why family 5 catches it

- The corpus calls MAKE_SET in 55 statements: 50 in cases/s1_unreached_0053.test and 5 in
  cases/s1_unreached_0054.test (expressions.tsv, row 172: `make_set(N, S, S*)`): the first argument
  over the 20 core matrix columns and the literals of domain `N`, the second and third over domain `S`
  and three columns, and calls with one, two and four strings.
- They all run the patched line. `ObExprMakeSet::cg_expr` installs only `eval_func_ =
  calc_make_set_expr` (no batch or vector function), so vectorized execution calls it row by row, and
  folding a constant call at plan time calls it too. `calc_result_typeN` chooses LONGTEXT only when a
  string argument is a LOB other than TINYTEXT; every string argument of the corpus's calls is a
  VARCHAR literal or a `c_double`, `c_varchar` or `c_datetime6` column, so the result is VARCHAR (the
  recorded metadata shows type 253) and `calc_make_set_expr` takes its non-text branch, the one with
  line 98.
- The output that changes, in the C++ recording
  /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/02-expr/r15/rec/ (identical to r16 and r17):
  104 result rows in 41 statements, all calls with two strings. For example:
  - s1_unreached_0053.result:479, `SELECT make_set(2, 'a', 'Hello, World!') AS v;` prints
    `Hello, World!`; mutated, the empty string. The same for the other five literals of domain `S`
    in the last position that are not NULL or empty, and for `make_set(2, NULL, 'abc')` and the
    other seven literals in the middle position, which print `abc`.
  - s1_unreached_0053.result:15, `SELECT id, make_set(c_int, 'a', 'abc') AS v FROM tm ORDER BY id;`:
    rows 4, 6 and 8 print `a,abc`, `abc` and `a,abc`; mutated, `a`, the empty string and `a`. The
    sweeps of the first argument over 16 of the 20 columns change the same way wherever bit 1 is set
    (not `c_varbinary`, `c_json` and `c_geom`, whose rows all print the empty string, nor `c_vec`,
    which fails with 5083).
  - s1_unreached_0053.result:425, `SELECT id, make_set(2, c_double, 'abc') AS v FROM tm ORDER BY id;`:
    all 8 rows print `abc`; mutated, the empty string.
  - s1_unreached_0054.result:15, `SELECT id, make_set(2, 'a', c_varchar) AS v FROM tm ORDER BY id;`:
    rows 3 to 8 print the column value; mutated, the empty string.
  - Unchanged: s1_unreached_0054.result:55, `make_set(2, 'a', 'abc', 'abc', 'abc')`, whose bit 1
    stays inside the shorter mask; `make_set(2, 'a')`, which already prints the empty string; rows
    whose first argument is NULL or has bit 1 clear; and every metadata line, which
    `calc_result_typeN` decides.
- So `compare` of a corpus recording made with the mutated binary against the C++ recording reports
  s1_unreached_0053 and s1_unreached_0054 `different` and the other 391 files identical.

## Why the 272 configured cases do not catch it

- In the coverage profile of the 272 cases
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb), `llvm-cov report
  --show-functions` on src/sql/engine/expr/ob_expr_make_set.cpp gives 0 hits on every line of
  `calc_result_typeN` (35 lines), `calc_make_set_expr` (43 lines, line 98 among them), `calc_text`
  (23), `cg_expr` (7) and `set_local_session_vars` (6); only the constructor and destructor ran (9
  times each).
  `ObExprOperatorFactory::alloc<ObExprMakeSet>` ran 0 times (expressions.tsv, `reach_basis`), so no
  statement of the 272 cases resolved a MAKE_SET call.
- The only mention of make_set under tools/deploy is tools/deploy/mysql_test/psmalltest.py:1888-1889,
  which names `expr.expr_make_set_bug` and `expr.expr_make_set`; neither .test file exists in this
  tree and neither is a configured case.
- The file is identical at 076eb309b and 834bbee1e
  (`git diff --quiet 076eb309b 834bbee1e -- src/sql/engine/expr/ob_expr_make_set.cpp` exits 0), so the
  profile's line numbers hold. A family 1 run of the mutated build is expected to pass.

## How the mutation stage runs it

Apply, rebuild and copy the binary aside as in ../README.md, "How to run them", then record the
corpus with the mutated binary the way families/expressions/README.md records the reference, and
compare it with the C++ recording:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
A=/Users/colin/seekdb-dev/ref-archive-834bbee1e
RUN=<a new directory>
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $RUN/seekdb --obclient $A/client/obclient --mysqltest $A/client/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN/work --port <port> \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --init-sql $H/migration/judge/reduced-init/init.sql \
  --init-user-sql $H/migration/judge/reduced-init/init_user.sql \
  --test-dir $H/migration/judge/families/expressions/cases --record-dir $RUN/rec
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/02-expr/r15/rec --right $RUN/rec
```

Caught when `compare` exits 1 with s1_unreached_0053 and s1_unreached_0054 `different` (and a note
that the seekdb binaries differ). `compare` refuses the pair if cases/ changed after r15
(`test_dir_sha256` 1516b7f988e0b11b0015813f762c0156d2981b9f04a9f6692089ab456b339c80); then record the
archived reference again first. Revert the patch afterwards.

## Caught by (filled after the run)

Caught by family 5 on 2026-09-28 (README.md in this directory). The corpus recorded on the mutated
build, compared with 02-expr/r15, exits 1 with s1_unreached_0053 and s1_unreached_0054 `different` and
the other 391 files identical: 104 result rows in 41 statements lose their last string, as predicted
above. The metadata lines of those 41 statements change too, but only in mysqltest's `Max length`
column, which reports the longest value returned; type and length stay. `check-recording` finds 0
problems.

The 272 configured cases on the mutated build: only quarantined cases failed. Outputs:
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/mutations/02-expr-make-set-drops-last-string/.

Run again on 2026-09-29, after the corpus turned the plan cache back on (families/expressions/README.md,
"Live check, 2026-09-28: the plan cache back on"): the patch was built again in the reference
worktree (review/mutations/build.sh; seekdb sha256 e7ee685f...; the worktree back to
reference-build.patch afterwards), and the corpus recorded on it (review/mut02-expr, 41 s, no failed
file). Against the new C++ recording expr-r18, `compare` exits 1: s1_unreached_0053 and
s1_unreached_0054 `different`, the other 391 files identical. The same 41 MAKE_SET statements differ
as before, 104 value rows and their `Max length`; `check-recording` finds 0 problems.
