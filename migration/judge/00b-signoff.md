# 00b: the first sign-off

The kit's 00b ends with "stop and show me the harness, the portable/internal-bound counts, the
reviewer findings, and both validation runs — the clean pass and every caught mutation"
(`prompts/00b-judge-setup.md`). This file is that showing, for the core set that must exit before
Step 1 (PLAN.md section 4, "00b's exit"). Under the developer's goal directive of 2026-09-24
("直到完成迁移"), Claude signs the gate off on this evidence and logs it in RULEBOOK.md section 7;
the developer can overrule it at any time.

## The harness

| Piece | Where | State |
|---|---|---|
| Runner (item 1) | .github/script/seekdb/mysqltest_for_seekdb.py; judge/harness/runner.patch; judge/harness/README.md | done: judge options, recording and byte-exact `compare`; two reviewers on disjoint batches (Fable 5.1, Opus 5.5), five weakenings found and fixed or logged (DEV-003); smoke-tested on the reference |
| Entry gate and pinned reference (item 2) | judge/reference.md, judge/reference-build.patch, judge/reduced-init/ | done: 834bbee1e with `-ffp-contract=off` (364/364 compile commands); the reduced init; the flag leaves all 40 plan-bearing cases unchanged |
| Restart script (item 3) | judge/harness/restart_scenarios.py, restart-scenarios.md | done: three kill-and-restart scenarios; SIGKILL sent by the harness; reviewed by Fable 5.1 and Opus 5.5; two runs identical |
| Coverage map (item 10) | judge/coverage-076eb309b/ | done: 64.4% of functions in the core and the SQL tier; stands |
| Validation (item 12) | judge/validation-834bbee1e.md; judge/mutations/ | done: the two passes, and all 14 mutations caught |
| Archive (item 13) | /Users/colin/seekdb-dev/ref-archive-834bbee1e/; judge/reference.md | done: rebuild from the archive reproduces compiler, SDK, libc++ and the plan-bearing output |

## Portable and internal-bound counts (00b step 1)

From judge/census/ (each file carries the command that produced it):

| File | Count |
|---|---|
| portable.txt | 273 (the 272 configured mysqltest cases, plus tools/ob_error/test/test.sh) |
| internal-bound.txt | 13 (3 gtests, 4 bazel/probes, 6 src/sql/bazel_pilot probes; none is run by any workflow) |
| out-of-scope.txt | 145 (Decision 8: 20 embedded-mode tests; Decision 7: 125 wasm and shell-e2e tests) |
| lists/plan-bearing.txt | 40 |
| lists/plain-sql.txt | 128 |
| lists/hash-order-select-candidates.txt | 633 (narrowed to the confirmed list before any Rust code) |

## Reviewer findings (00b step 2)

| Reviewed | Reviewers | Findings | Outcome |
|---|---|---|---|
| Runner batch A (retries, lifecycle, case selection) | Fable 5.1 | 3 minor, 1 weakening | fixed (empty selection now fails; stop goes through destroy's own check; failed stop marked) |
| Runner batch B (init, recording, compare, tolerance) | Opus 5.5 | 5 major, 7 minor, 5 weakenings | fixed: record mode requires `--max-retries 0`; the unasked init-failure option removed; failed cases leave a `.partial`; compare checks manifests and inputs; the tolerance is off in every judge command. Logged: init runs one obclient session per file (DEV-003) |
| Restart script, lifecycle | Fable 5.1 | 1 major, 5 minor | applied, including the harness-sent SIGKILL |
| Restart script, scenarios and determinism | Opus 5.5 | 6 major, 9 minor | 18 of 21 findings applied across both reviews |
| Mutations | Fable 5.1 | all 14 re-checked (coverage, equivalence, visibility, patch hygiene) | 14 kept, 0 fixed, 0 dropped |

## Validation runs (00b step 3)

- **The clean pass:** two passes of the 272 cases on the archived reference, full init, retries off,
  tolerance off: 268 passed and the same 4 failed in each, all on the quarantine list; nothing failed
  outside it (judge/validation-834bbee1e.md). The kit's "N/N pass" becomes "every case outside a
  quarantine list named before the runs passes" (PLAN.md departure 7).
- **The quarantine list** (judge/quarantine.tsv): 5 cases, each with a reason; the fifth
  (subquery.idx_with_const_expr_21_subquery_dilang) was explained from the source and confirmed on the
  reference (judge/investigations/subquery-datetime-rounding.md).
- **Two reduced-init recordings** of the 128 plain-SQL cases are byte-identical (judge/recordings.tsv).
- **Injected mutations:** all 14 caught, each by at least one expected case (judge/mutations/README.md, "Results"). Five are caught by exactly one case (04, 05, 10, 13, 14). One extra failure under 07 (vector_index.rebuild_vector_index, a 4012 timeout with no output difference) is not counted as a catch and is not explained.

## Performance baselines (item 9)

Measured on 2026-09-24 under judge/performance-protocol.md (judge/perf/baseline-834bbee1e.md), 5 rounds
each, median QPS: `oltp_point_select` 7,151 / 43,685 / 58,145 and `oltp_read_write` 6,686 / 40,087 /
49,857 at 1 / 16 / 64 threads. Point-select stops scaling at about 2 of the server's 8 cores, so its
limit is the client or the Docker hop; these figures are for the C++-against-Rust ratio, not the
machine's ceiling. Cold start 2-3 s, restart after a kill about 3.2 s (recorded, not gated). Item 9
belongs to the second set; it is shown here because it is done.

## The first sign-off (2026-09-25)

Every condition of PLAN.md section 4, "00b's exit", for the first sign-off holds:

| Condition | Evidence |
|---|---|
| Two runs of the 272 cases on 834bbee1e with `-ffp-contract=off`, retries off, every case outside the quarantine list passing | judge/validation-834bbee1e.md: 268 passed and the same 4 quarantined cases failed in each pass |
| Each quarantined case with a stated reason | judge/quarantine.tsv (5 cases; the subquery case explained from the source and 20 diagnostic runs) |
| Two reduced-init recordings of the plain-SQL cases, byte-identical outside the quarantine list | judge/recordings.tsv: 128 of 128 identical |
| Every injected mutation caught, at least 10 | judge/mutations/README.md: 14 of 14, each by an expected case |
| The 40 plan-bearing cases compared with and without the flag | 40 of 40 identical (PLAN.md section 8, item 3) |
| The core-set items built (1, 2, 3, 10, 12, 13) | the harness table above |
| The reference archived and rebuilt once from the archive | judge/reference.md |
| The coverage numbers recorded, 64.4% standing | judge/coverage-076eb309b/README.md |

The two items PLAN.md section 8 left for this sign-off take the plan's defaults (decisions.md row
5c): item 6, the judge's files live as section 4, "Where the judge's files live", lays them out; item
27, the trailing-whitespace tolerance is a named switch, on for CI and off in every judge run.

Signed off by Claude on 2026-09-25 under the developer's goal directive ("直到完成迁移") and
decisions.md row 5c; the departures are RULEBOOK.md DEV-006 (two sign-offs) and DEV-007 (the
quarantine list). The developer may overrule it. The second sign-off (items 4, 6, 7, 8, 9 and the
families) comes before Step 2a.
