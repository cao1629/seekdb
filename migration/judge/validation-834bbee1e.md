# Validation of the judge on the pinned reference (00b action 8)

PLAN.md section 4, item 12; section 10, action 8. Two passes of the 272 configured cases on the
archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256
db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91: 834bbee1e with
reference-build.patch, `-ffp-contract=off`), full tools/deploy/init.sql, one slice, port 3881,
`--max-retries 0 --no-ignore-trailing-whitespace`, one after the other on 2026-09-24. The working-tree
check passed before the runs (`git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py`
and an empty `git status --porcelain -- tools/deploy`).

| Pass | Wall time | Passed | Failed | Failed outside quarantine.tsv | Retries | Cases passed by the trailing-whitespace tolerance |
|---|---|---|---|---|---|---|
| A | 1,409 s | 268 | 4 | 0 | 0 | 0 (tolerance off) |
| B | 1,417 s | 268 | 4 | 0 | 0 | 0 (tolerance off) |

The four failures are the same in both passes and all are on the quarantine list:
`histogram.stats_farm`, `type_date.type_create_time`, `type_date.type_modify_time`,
`vector_index.sparse_vector_index_vsag_query`. `subquery.idx_with_const_expr_21_subquery_dilang`
passed in both passes. `grep -c RETRY` and `grep -c 'trailing whitespace ignored'` print 0 for both
runner logs.

Outputs: /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-A/ and ref-834bbee1e-B/
(seekdb_result.json, runner.log, entry-gate.json, failures/, mysqltest_log/).

What this settles:
- **The precondition holds**: two runs of the same C++ binary agree on every case outside the
  quarantine list, and every case outside it passes.
- **The coverage figure stands** (report section 11): the difference that held it back is explained
  (investigations/subquery-datetime-rounding.md), so 64.4% of the functions in the core and the SQL
  tier (coverage-076eb309b/README.md) is the go-condition's number.
- Still open for 00b's first sign-off: the archive and its rebuild check (action 11) and the injected
  mutations (action 12).
