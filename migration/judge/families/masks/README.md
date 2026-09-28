# Families 3 and 4: the plan-text and row-order masks

PLAN.md section 4, families 3 and 4, and "The masks" (Decision 6 (b)). Neither family records
anything of its own: both compare family 1's recordings of the 272 configured cases
(../differential/README.md), once exactly and once with a mask, and `compare` prints both results.

- **Family 3, plan text:** the 40 plan-bearing cases (../../lists/plan-bearing.txt), exactly, and again
  with `--mask est`, which replaces the EST.ROWS and EST.TIME(us) cells of every plan table by `#`.
- **Family 4, row order:** ties under ORDER BY and every SELECT without ORDER BY are compared exactly;
  only the statements of ../../lists/hash-order-selects.txt are also compared with their rows sorted,
  under `--mask row-order`.

How each mask finds what it rewrites, and how `compare` reports it, is in
../../harness/second-set/README.md ("The two masks and how they are switched on" and the sections after
it). The runner pins the sha256 of both lists (`PLAN_BEARING_LIST_SHA256`, `HASH_ORDER_LIST_SHA256`),
so a list changes only together with the runner.

| File | What it is |
|---|---|
| hash-order-decisions.tsv | One line per candidate of ../../lists/hash-order-select-candidates.txt: whether the helper placed it, its row count, `select count(*)` and `found_rows()` on the reference, the plan's operators on the path that sets the output order, and the decision with its reason |
| ../../lists/hash-order-selects.txt | The row-order mask's list: 92 statements in 20 cases, sha256 `b0b11890b9e8fcd2c46b525ef63f787d2fca8f2bdf19181bf790c3626fb68c57` |
| ../../harness/second-set/hash_order_list.py | The helper that places each candidate in the reference output and writes a draft of the list |
| ../../harness/second-set/hash_order_list_test.py | Offline tests of the helper's handling of `--replace_regex` and `--replace_result` (12 tests) |
| ../../harness/second-set/hash_order_confirm.py | The confirmation step: `prepare` writes instrumented copies of the cases, `confirm` reads their recording on the reference and keeps the draft lines whose order a hash operator decides |

## How the row-order list was made

1. **Candidates.** Action 3 read the configured .test files and wrote 633 candidates: SELECTs with a
   FROM and no ORDER BY outside parentheses that join, group, use DISTINCT, a set operation, a window
   or a nested SELECT, so that a hash operator could decide their order
   (../../lists/hash-order-select-candidates.txt, whose header gives the rules).
2. **Draft.** The helper places each candidate in the checked-in .result: the statement's echo, its
   row count and the line after its rows. It places 608 of the 633; the 25 others it cannot place
   safely (sorted by `--enable_sorted_result`, inside an `if` block, an `eval` with variables, echoed
   twice because the same SELECT follows an `explain`, and so on; the second-set README lists them).
   The helper that wrote the draft is the live stage's (sha256
   `a821f0873a12e62e94114bec1132f5d527039a5b55b3bdd6a952fa5070f1aa8f`), which places 12 more than the
   first version: a `--replace_regex` after the statement no longer makes it give up when the pattern
   cannot change the next output (second-set README, "The helper that writes the row-order list").
3. **Confirmation on the reference.** `hash_order_confirm.py prepare` writes a copy of every case
   with a candidate (65 cases), with the candidate's `EXPLAIN` and `select count(*) from
   (<candidate>)` printed between markers right before it and `select found_rows()` right after it.
   The runner recorded those copies on the archived reference under the full init
   (`--test-dir`, retries off). `confirm` checks that each case's output without the markers is the
   checked-in .result byte for byte, and keeps a draft line only when a HASH operator lies on the
   plan's path that sets the output order, no operator runs with a degree of parallelism above 1,
   the statement prints at least two rows, `found_rows()` gives that row count, and `select
   count(*)` gives it too where it runs. It writes the kept lines, with their hash operators, as the
   list, and one line per candidate into hash-order-decisions.tsv.

The commands (07-masks is /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/07-masks):

```
python3 -B migration/judge/harness/second-set/hash_order_confirm.py prepare --out 07-masks/explain-tests-t5
<runner> run --test-dir 07-masks/explain-tests-t5 --case-list 07-masks/explain-tests-t5.cases \
  --record-dir 07-masks/explain-rec-t5/rec --max-retries 0 --no-ignore-trailing-whitespace ...
python3 -B migration/judge/harness/second-set/hash_order_confirm.py confirm \
  --tests 07-masks/explain-tests-t5 --recording 07-masks/explain-rec-t5/rec \
  --draft-out 07-masks/hash-order-draft.txt --out migration/judge/lists/hash-order-selects.txt \
  --decisions migration/judge/families/masks/hash-order-decisions.tsv
```

(the list's header holds the exact lines).

## The decisions

| Decision | Candidates | What it means |
|---|---|---|
| keep | 92 | on the list: a hash operator orders two rows or more, and the counts check |
| no-hash-order | 435 | no hash operator on the path that sets the output order (a sort, a merge join or merge group by, a scan in index order, or a nested loop decides it); compared exactly |
| under-two-rows | 73 | one row or none: there is no order to mask |
| not-in-draft | 25 | the helper could not place it (step 2) |
| parallel | 6 | the plan runs with dop=2, so its order is the exchange's, not a hash table's; compared exactly |
| not-reproduced | 2 | its case's output without the markers did not reproduce the checked-in .result, so its place could not be trusted |

**Why 92 and not about 300.** PLAN.md and Decision 6 (b) said "about 300", an estimate from the
report's count of SELECTs without ORDER BY that could meet hash output, made before any plan was read.
Reading the reference's plan in each statement's place shows that most of the 633 candidates are not
ordered by a hash operator at all (435), and 73 more print fewer than two rows. The list holds only
the statements Decision 6 allows to be compared as sorted sets; every other candidate is compared
exactly, which is the stricter choice.

**Reproducible.** The confirmation was run again on a second recording of the instrumented copies
(07-masks/explain-rec-t6 of explain-tests-t6, which the current tool prepared): the helper's draft is
byte-identical (sha256 `54124208484aeb7e2e4a4bcf57b5d940e6702ba00bdb381e58d9e84e975f3f96`), and the
92 list lines and the first nine columns of every decision line are the same as from t5 (checked
2026-09-28).

## Validating the masks, C++ against C++

Family 1's two plain recordings of the 272 cases (f1-p1 and f1-p2, ../differential/README.md, "Live
check, 2026-09-28") hold every listed case of both lists. `compare --mask row-order --mask est` on
them, with the runner that pins both lists:

```
mask row-order: .../lists/hash-order-selects.txt (sha256 b0b11890...): listed_cases=20, compared_cases=20,
  not_compared_cases=0, listed_statements=92, statements=92, not_compared_statements=0, masked=92,
  not_masked=0, reordered=0
mask est: .../lists/plan-bearing.txt (sha256 8566d1f3...): listed_cases=40, compared_cases=40,
  not_compared_cases=0, est_header_lines_left=568, tables_masked_left=568, tables_left_exact_left=0,
  est_header_lines_right=568, tables_masked_right=568, tables_left_exact_right=0
masked (row-order, est): cases=50, identical=50, different=0, missing=0, exact_identical=50,
  exact_different=0, exact_missing=0, mask_changed=39
```

So both masks ran on every listed case and statement, found every table and every listed statement
in its place on both sides, and hid nothing: the 50 masked cases are identical exactly too, and no
listed statement came in another order. The comparison's exit is 1 only because of the two quarantined
type_date cases, which are outside both lists (review/compare-f1-p1-p2-masks.log and .json). This is the
validation that ../../harness/second-set/README.md ("Validating a mask, C++ against C++") asks for;
the EST mask was validated before on the 40 plan-bearing cases alone (a9-rec-noflag against a9-rec-flag).

## The mutations

The replay of the first set's mutated outputs against family 1's C++ recording
(../differential/README.md, "The mutations") shows both families catching the mutations of what they
test, with their masks on:
- **Family 3, plan text: mutation 01** (the optimizer's range-scan cost takes the smaller of its IO and
  CPU cost). 31 of the 40 plan-bearing cases print differently; under the EST mask 9 still do,
  because the mutation also changes plan shapes, not only estimates (dist_nest_loop_simple,
  executor.basic, fts_index.partitioned_simple_query, geometry.geometry_basic_mysql,
  geometry.geometry_index_mysql, intersect, subquery.optimizer_subquery_bug, subquery.spf_bug13044302,
  topk). The other 22 differ only in EST numbers: family 3's exact report of the plan-bearing cases
  names them, and the masked verdict passes them, as Decision 6 intends.
- **Family 4, row order: mutation 03** (rows tied on every ORDER BY key come out in reverse storage
  order). All 8 cases it changes still differ under the row-order mask, since ties under ORDER BY are
  compared exactly and none of them is a listed statement.
- The row-order mask hides one difference of mutation 01: vector_index.vector_index_ivfflat_dml, whose
  listed HASH DISTINCT at test line 58 comes out in another order under the changed plan. That is the
  mask working as declared; the case stays in the exact report.
