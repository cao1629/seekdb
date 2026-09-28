# 00b, the second set live: C++ pairs, mutations and the review fixes

PLAN.md section 4, "00b's exit": for the second sign-off every item and family that can run against
C++ alone needs a clean C++-against-C++ run (two recordings of the pinned reference that `compare`
finds identical outside quarantine.tsv) and one injected C++ mutation of the behavior it tests that
the family catches. Two reviews of the live stage (review A: units 01-06; review B: units 07-11 and the
runner's mask code) found that families 1, 3/4, 7 and 12 and item 8's `--compress` replay had neither,
and weaker points elsewhere. This file is the state after the unit that applied those reviews, from
2026-09-28 21:19 to 2026-09-29 02:40 +08:00: per item and family, the two recordings, the verdict,
what is not comparable and why, the mutation, and what became of each review finding.

Every run used the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256
db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91) with the archive's obclient and
mysqltest, `--max-retries 0 --no-ignore-trailing-whitespace` wherever the runner ran, no
`--save-instance-dir`, ports 3891-3895, at most two instances at a time, and before each run the checks
that `git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py` succeeds, tools/deploy has
no local changes, `/` has at least 8 GiB free (it never went below 10 GiB at a run's start, and was
8.1 GiB after the last recording) and the port is free. No recording ran between 22:00 and 23:00 +08:00, and none of family 7 started between
19:00 and 23:00. Outputs are under /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/, one
directory per run; the queue scripts (slotA.sh, slotB.sh, slotA2.sh) and their logs are there too.

## Per item and family

"Identical" is `compare`'s verdict outside the quarantine list. The mutation column names the patch
(mutations/second-set/ unless marked "first set", mutations/) and what the family's own check did.

| Item / family | The two C++ recordings | Verdict | Not comparable, and why | Mutation | Caught | Review outcome |
|---|---|---|---|---|---|---|
| 1, the 272 cases, plain mode | f1-p1, f1-p2 (2026-09-28 23:00 and 2026-09-29 00:09, 23 min each) | 270 of 272 identical, 0 missing, 0 recording problems; the 2 that differ are quarantined | type_date.type_create_time and type_modify_time (the plain recordings split on them), subquery.idx_with_const_expr_21_subquery_dilang (family 11's recordings split 2 against 2); decided over every C++ recording (families/differential/README.md) | first set's 14, replayed against f1-p1 | yes: all 14 differ outside the quarantine list (01 in 32 cases, 04, 05, 10, 13 and 14 in 1 each); the same against f1-p2 | B2 applied |
| 1, fresh-instance mode | family 7's f7-rec7 and f7-rec8 | 272 of 272 identical, the type_date pair included; 0 recording problems | as above | as above | as above | B2: family 7's recordings serve (decision below) |
| 2, entry gate (reduced init, the 128 plain-SQL cases) | a10-rec1, a10-rec2 (first sign-off) | 128 of 128 identical | – | first set | yes (first sign-off) | not in either review |
| 3, plan text (40 plan-bearing cases), exact and `--mask est` | f1-p1, f1-p2 | exact 40 of 40 identical; masked 40 of 40, `tables_masked=568` and `tables_left_exact=0` on each side, `not_compared_cases=0` | – | first set 01 (cost model) | yes: 31 plan-bearing cases differ exactly, 9 still differ under the EST mask (plan shapes) | B1 applied |
| 4, row order, exact and `--mask row-order` | f1-p1, f1-p2 | 20 listed cases, 92 of 92 statements masked, `not_masked=0`, `reordered=0`; identical exactly and masked | the 92 listed statements are compared as sorted sets (Decision 6) | first set 03 (SORT ties) | yes: 8 cases differ, all 8 still under the mask | B1 applied |
| 5 / item 7, expressions and casts | expr-r18, expr-r19, expr-r20 (2026-09-28 21:35-21:37, plan cache on) | 393 of 393 identical in all three pairs; `check-recording` 0 problems | four probe shapes left out of the corpus because the reference reads memory it never wrote (families/expressions/README.md, "Not comparable") | 02 (MAKE_SET drops its last string), rebuilt and run on the new corpus | yes: 2 files, the same 41 statements and 104 rows | A5 applied |
| 6, the error catalog (`ob_error`) | run1, run2 (2026-09-25); the new sweep oberr-sweep-1, oberr-sweep-2 | identical to expect_result.result; the two sweeps of all 1,544 codes byte-identical | – | 03 (rowid message) | yes: test.sh line 82, and the sweep at code 5870 | A9: the sweep added |
| 7 / item 6, plan-cache counts | f7-rec7 (2026-09-28 23:00 to 2026-09-29 01:01), f7-rec8 (00:32 to 02:32), with rec1-rec6 of unit 05 | exit 0 with the pinned list for rec7 against rec8 and rec4 against rec8, rec8 held out from the list: 272 `.result` identical, the misses of the 70 compared cases identical | hits of every case (timers add about 5 a second); misses of every case but the 70 that three or more recordings reached with the reads under 0.1 s apart and agreed on (families/plan_cache/not-comparable.list) | 05 (plan key includes the session id), the recording of 2026-09-28 compared again | yes, by `compare` itself: 70 of 70 compared cases differ against rec4, rec7 and rec8 | A1, A2, A3, A4 applied; the list was rebuilt once, after the held-out rec7 failed its first form by one case |
| 8 / item 4, the binary protocol | ps-r7, ps-r8 (2026-09-28 23:46 and 2026-09-29 01:24; cases.txt of 268) | 266 identical and the 2 known failures failed alike; exit 0 | 4 cases print run-to-run values that the text run masks (families/ps_protocol/cases.txt); the 7 purge cases are back in and recorded without a hang | 01 (binary TIMESTAMP without the time zone), run again on the new cases.txt | yes: against ps-r7, 12 cases differ (TIMESTAMP values), 254 identical, the known failures accepted | A7 applied |
| 9 / item 3, restart after a kill | rs-3, rs-4 (2026-09-29 02:18, the script with `stack_size`) | 3 of 3 identical; every check holds | – | 04 (replay skips user deletes), run again against the new pair | yes: restart_data fails its three checks | A10 applied |
| item 8, golden bytes on the wire | run11, run12 (2026-09-27) | 10 of 10 identical | the masked fields of families/wire/README.md | 06 (OK drops NO_BACKSLASH_ESCAPES) | yes (2026-09-28) | not changed |
| item 8, the `--compress` replay | cmp-c1, cmp-c2 (2026-09-28 23:23 and 2026-09-29 01:01) | 272 of 272 identical, `--require-compress`; the login check passed (every mysqltest login compressed) | – | none of its own: the compressed framing is rust/sql-nio, kept as it is; item 8's is 06 | – | A6 applied |
| 11, concurrency | par-1, seq-1, ser-1, a10-rec1/2, sb-2, sb-3, st-1, st-2 (2026-09-28, unit 11) | identical in every pair (119, 128, 7, 3 cases) | dilang (its race), create_table_with_vector_index (the purge hang; now quarantined) | 11 (the session sweep cancels a live commit) | yes | B6 applied |
| 12, memory budgets | mem-10 to mem-15 (final script; 2026-09-28 21:58 and 2026-09-29 01:49-01:52) | exact: identical whenever mem_hold agrees (5 pairs); with `--mask mem-hold` identical in every pair, the two pairs whose mem_hold differs included | only the number after `mem_hold=` in the -11049 line (11020166 or 11085702 over 15 runs) | 12 (memstore reserve halved), new | yes: memstore_fill fails its distance check (47 chunks, not 13) | B3, B4 applied |
| 13, the IK tokenizer | ik-r07, ik-r08 (2026-09-28 21:30, with the relevance column) | 22 of 22 identical; `check-recording` 0 problems | – | 08 (underscore not a letter connector), run again | yes: 11 files; 16 searches change ids and 36 more only relevance | B7 applied |
| 14, performance | baselines of the first sign-off | – | – | needs a Rust build | – | – |
| 15, data version gate | – | – | – | needs a Rust build | – | – |
| 17, the judge checks itself | – | – | – | first set 14 + second set 9 (01-06, 08, 11, 12; 03 also through the sweep) | every one caught by its family | – |

## The review findings

| Finding | Severity | Outcome |
|---|---|---|
| A1: family 7 has no clean C++ pair | blocker | Applied. Two complete recordings outside 19:00-23:00 with no failed case (f7-rec7, f7-rec8); the purge cases decided (quarantine, below); the list in the runner's format checked in and pinned; the README's missing sections written. rec7 against rec8 and rec4 against rec8 exit 0 with the pinned list, rec8 held out from it. The per-plan counts the review names as the fallback are described in families/plan_cache/README.md as the way to widen the family; not built in this round |
| A2: the list is circular | blocker | Applied. build_list.py rebuilds the list from every recording (partial ones too) by a declared rule: hits never compared; misses compared only for cases that at least three recordings reached with the reads under 0.1 s apart and that agree; counts from instances alive in 22:00-23:01 dropped (all of rec3, 23 cases of rec2). The first rule's list (two recordings enough, 72 cases) failed on the held-out rec7 in one case (information_schema.information_schema_desc, misses 17 against 18, no logged cause); the list was rebuilt with rec7 and three recordings required (70 cases), pinned at 01:25 before rec8 finished, and checked on rec8. The "0 for rec4 vs rec2" claim is gone from mutations/second-set/README.md |
| A3: the catch rests on plan_check.py | major | Applied. The rule is the pinned list, so `compare` decides: the mutated recording against rec4, rec7 and rec8 exits 1 with the 70 compared cases different; the C++ pairs rec7/rec8 and rec4/rec8 exit 0 with the same list |
| A4: the not-comparable list is not pinned | major | Applied. `PLAN_CACHE_NOT_COMPARABLE_SHA256` pins families/plan_cache/not-comparable.list; any other list exits 2 (two new offline tests). Ownership recorded in harness/README.md: unit 05 wrote the option; this unit, the only agent of its round, owned the runner for the pins and the mask. A second agent's review of these runner changes is left to the sign-off |
| A5: expressions with the plan cache off | major | Applied. Each file flushes the plan cache and keeps it on; split_rows.py unchanged (split-rows.tsv byte-identical), stability.py turns the cache off for its own repeats (0 unstable of 16,669); three recordings identical; 370 statements now report the reused plan's column definitions and 12 a different value or message (families/expressions/README.md); MAKE_SET caught again |
| A6: the `--compress` replay never recorded | major | Applied. Owner: item 8 (families/wire/README.md). Two recordings identical with `--require-compress`; families/wire/login_watch.py keeps the `MySQL LOGIN` lines without `--save-instance-dir`, and the check passed on cmp-c2 against f1-p2. The first check also showed that the server drops INFO log lines under load, so login counts are reported, not required to match |
| A7: four ps cases excluded without evidence | major | Applied, for all seven purge cases (the two seen hanging over the binary protocol too, since the quarantine rule now covers them): cases.txt has 268 cases; ps-r7 and ps-r8 recorded them without a hang and compare identical |
| A8: the PURGE RECYCLEBIN hang is not in quarantine | major | Applied under decisions.md row 5c: the seven configured cases that send `PURGE RECYCLEBIN` are in quarantine.tsv with the six hangs as evidence and one rule for every family (compared while two C++ recordings agree; a recording that stops on the hang is made again); PLAN.md's table has the row; families 1, 7, 8 and 11 and the mutation README follow it |
| A9: ob_error prints 15 of 1,544 entries | minor | Applied under row 5c: families/ob_error/sweep_ob_error.sh sweeps every code; two sweeps identical; mutation 03 caught by it |
| A10: the restart document is stale; only dynamic parameters | minor | Applied: restart-scenarios.md records the live runs of 2026-09-24 and the caught mutation; restart_parameters also sets `stack_size`, one of the six static parameters; the first runs with it showed that a static parameter's new value appears in SHOW PARAMETERS only after the restart, and the checks now expect that; rs-3 and rs-4 identical; mutation 04 caught again |
| B1: the row-order mask cannot run | blocker | Applied: `HASH_ORDER_LIST_SHA256` pins b0b11890...; families/masks/README.md (how the list was made, the decisions, why 92, t5 and t6 give the same list); the stale passages of second-set/README.md fixed; the real `compare --mask row-order --mask est` on f1-p1/f1-p2 gives the mask lines above; the first set's replay is the families' catch. The list and the tools are left uncommitted, as this round does not commit |
| B2: family 1 has no C++ run | blocker | Applied: the plain pair; the fresh-instance pair is family 7's (below); quarantined cases decided over every recording (dilang stays out, the type_date pair is out on the plain recordings, the rest compared); families/differential/README.md; the purge cases to quarantine (A8); the first set's replay as the mutation |
| B3: family 12 never run | blocker | Applied: the first run found the reference's `__all_virtual_memstore_info` never ends its rows (a read without `limit 1` fails with 4019); fixed in the script; every value but mem_hold's number is now recorded exactly (identical in all 14 runs that reached them); port 3894, no `--save-instance-dir`; mutation 12 caught |
| B4: no mem-hold mask | major | Applied: the raw -11049 line is recorded; `compare --mask mem-hold` in the runner (decisions.md row 6a), off by default, for memory_scenarios.py recordings only, the digits after `mem_hold=` only; validated on mem-8/mem-9 and mem-14/mem-15 (exact 1, masked 0); 13 offline tests; the three READMEs and PLAN.md updated |
| B5: the helper's replace handling changed silently | minor | Applied by documenting and testing, not reverting: the confirmed list names the changed helper's sha256 (a821f087...), so reverting would make the list unreproducible; second-set/README.md records the change and the 608 placements, and hash_order_list_test.py (12 tests, including patterns that match the next echo) covers it; bugs put into copies fail them |
| B6: family 11's account of the hang | minor | Applied: the cause is the case's own dropped tables (family 7's rec2 hung on a fresh instance), the failure count corrected, one rule for the case in "Recording a build", and ser-1/seq-1 for p3/s1 |
| B7: IK relevance never compared | minor | Applied: the s8 searches print `MATCH ... AGAINST` as `score`; two recordings agree on all 225 scores, so the column is compared; mutation 08 now changes 36 more searches through their relevance |

No finding was rejected. Two requests were not done as asked: B1's "commit" (this round does not
commit) and A4's "review it again", which needs an agent other than the one that made the change.

## Decisions taken under decisions.md row 5c

- **The purge cases are quarantined** (A8), with the rule above.
- **Family 1's fresh-instance pair is family 7's pair.** Family 7 records the 272 cases with a fresh
  instance per case and `compare` checks their `.result` first; the extra options
  (`--plan-cache-stats`, `plan_cache_evict_interval=1d`) read counters between cases and stop the
  eviction timer, and a Rust build is recorded the same way. A separate pair would take four more
  hours of machine time.
- **Family 7 compares misses of 70 cases and hits of none**, by the rule above, and the list is
  rebuilt only from new recordings and checked on one it did not see. Per-plan counts are the next
  step if the family should compare more.
- **ob_error's sweep** joins family 6; **stack_size** joins restart_parameters; **every memory value
  but mem_hold's number is recorded exactly**; **the hash-order helper's change is kept** (B5).

## What changed (uncommitted)

- Runner (.github/script/seekdb/mysqltest_for_seekdb.py, sha256
  2abd81aa4a6693764d78f6f4b26bfb0b3b790ef94470757aeaba9e7c8e306aab): the two pins and the mem-hold
  mask; CI defaults unchanged, `compare` as strict as before for everything else.
- Lists: lists/mem-hold-lines.txt (new, pinned); lists/hash-order-selects.txt (now pinned);
  quarantine.tsv (+7); families/plan_cache/not-comparable.list (new, pinned) and not-comparable.tsv.
- Family files: expressions (generate.py, stability.py, cases/, README); ik (generate.py, cases/,
  README); memory (memory_scenarios.py, README, mask_offline_test.py); plan_cache (build_list.py,
  offline_test.py, README); ps_protocol (cases.txt, README); wire (login_watch.py, README); ob_error
  (sweep_ob_error.sh, README); concurrency (README); masks (README, new); differential (README, new);
  harness (restart_scenarios.py, restart-scenarios.md, README, second-set/README.md,
  second-set/hash_order_list_test.py).
- Mutations: 12-memory-memstore-reserve-halved.patch and .md (new); the notes of 01, 02, 04, 05 and 08
  and README.md updated. The reference worktree was rebuilt for 01, 02, 04, 08 and 12 and for
  mutation 03's `ob_error`; after each, `git diff` equals reference-build.patch and `git status` shows
  only ` M cmake/Env.cmake` (a relink changes the worktree binary's sha256; judge runs use the archive).
- PLAN.md: the purge row in the quarantine table and the note that the mem-hold mask is built.

## Open for the sign-off

- A second agent's review of the runner changes (both pins, the mem-hold mask) and of family 7's
  rule and list.
- Family 7's margin: over eight recordings one short case moved by one miss once
  (information_schema_desc in rec7), so a single one-miss difference is recorded again on both builds
  before it counts (families/plan_cache/README.md). Counting per plan instead of per server would make
  the family wider and firmer; it needs a runner option and new recordings.
- families/plan_cache/logwatch.py stops reading a rotated log as soon as the new one appears, as
  login_watch.py did at first; family 7's logs can miss lines written after a rename. The counts are
  unaffected; differences.py's causes can be incomplete.
- Families 14 and 15 wait for a Rust build.
