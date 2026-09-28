# Injected mutations, second set

PLAN.md section 4, "00b's exit": for the second sign-off, each family that can run against C++ alone
needs at least one injected C++ mutation of the behavior it tests, caught by that family. The family
units of the live stage wrote the eight patches here, each with a note beside it: what the patch
changes, why the family catches it, and why the 272 configured cases most likely miss it. There is no
patch yet for the masks (unit 07), the memory budgets (unit 09) and family 1 (unit 10): those units
ended without a result on 2026-09-28. This file records how the eight were built and run on
2026-09-28 and what caught each; "After the reviews, 2026-09-28" (at the end) records what the unit
that applied the second-set reviews added: patch 12 for the memory budgets, families 1, 3 and 4
caught through the replay of the first set's mutated outputs, and the runs of 01, 02, 04, 05 and 08
again against their families' new C++ recordings.

- **Caught by the family:** the family's own check, run on the mutated build and compared with the
  family's C++ recording, fails in the way the note predicts.
- **Caught by the 272:** a run of all 272 configured cases on the mutated build, under the full
  tools/deploy/init.sql with `--max-retries 0 --no-ignore-trailing-whitespace`, compared with the
  checked-in .result files as in the first set (../README.md, "How to run them"), fails a case outside
  ../../quarantine.tsv because of the mutation. Every note says the 272 miss its mutation; these runs
  check that.

## Results

All eight are caught by their family, and none by the 272 configured cases.

| Patch | Family | Caught by the family? | Caught by the 272? | Evidence | Run time |
|---|---|---|---|---|---|
| 01-ps-binary-timestamp-without-time-zone | 8, the `--ps-protocol` replay (item 4) | yes | no | `compare --require-ps-protocol --known-failures` against 01-ps/rec5 exits 1: 12 cases `different`, 247 identical, the 2 known failures failed alike. Every changed line is a TIMESTAMP value printed in UTC instead of the session time zone. In the first 272 run, the one failure outside the quarantine list is a `PURGE RECYCLEBIN` timeout with no output difference (not counted); a second 272 run failed only quarantined cases | build 8 s; family 1,220 s; 272: 2,422 s, then 1,393 s |
| 02-expr-make-set-drops-last-string | 5, expressions and casts (item 7) | yes | no | `compare` against 02-expr/r15 exits 1: s1_unreached_0053 and s1_unreached_0054 `different` (104 rows in 41 MAKE_SET statements lose their last string), 391 files identical | build 19 s; family 28 s; 272: 1,372 s |
| 03-ob_error-rowid-message-user-format | 6, the error catalog through `ob_error` | yes | no | run_ob_error_test.sh exits 1 with `status=different`; the only changed line is line 82, `Message: rowid type mismatch` becoming `Message: rowid type mismatch, expect %.*s, got %.*s`. An `ob_error` rebuilt after the revert matches expect_result.result | build 17 s (seekdb) and 2 s (`ob_error`); family under 1 s; 272: 1,414 s |
| 04-restart-replay-skips-user-deletes | 9, restart after a kill (item 3) | yes | no | restart_scenarios.py exits 1: restart_data ends as a `.partial` with its three `no` checks, since rows deleted before each kill come back after the restart; restart_parameters and restart_mid_dml identical; `compare` against restart-smoke/rec-1 exits 1 | build 16 s; family 28 s; 272: 1,412 s |
| 05-plan-key-includes-session-id | 7, plan-cache counts (item 6) | yes | no | every .result identical (272 of 272); `empty_input` goes from 7 hits and 1 miss to 6 and 2; each of the 69 cases whose reads are under 0.1 s apart gains misses and none loses any. Since the reviews the family's own `compare` decides it: with the pinned not-comparable.list it reports every case whose misses it compares as different ("After the reviews", below) | build 23 s; family 7,246 s, plus 172 s for differences.py; 272: 1,375 s |
| 06-wire-ok-drops-no-backslash-escapes | item 8, golden bytes on the MySQL wire | yes | no | wire_scenarios.py exits 0 (its own checks pass); `compare` against 06-wire/run09 exits 1 with wire_ok_err_eof `different`: the OK packets of requests 36, 38 and 39 lose status bit 0x0200 (0x4222 becomes 0x4022, 0x0222 becomes 0x0022); the other 9 scenarios identical | build 21 s; family 70 s; 272: 1,394 s |
| 08-ik-underscore-not-letter-connector | 13, the IK tokenizer corpus | yes | no | `compare` against 08-ik/r04 exits 1 with the 11 files the note named `different` and the other 11 identical; `generate.py diff-tokens`: 38 token lists differ (no token keeps an underscore), 17 more statements differ (16 boolean-mode MATCH queries, 1 MEMBER OF), 1,198 identical | build 16 s; family 6 s; 272: 1,416 s |
| 11-conc-session-sweep-cancels-live-commit | 11, concurrency (the sysbench piece) | yes | no | sysbench_parity.py exits 1: in oltp_read_write_1 a `COMMIT` gets error 4012 and sysbench stops with a FATAL line, so oltp_read_write_16 and _64 do not run; `compare` against 11-conc/sb-2 exits 1 (4 identical, 3 missing, 1 recording problem) | build 16 s; family 133 s; 272: 1,389 s |

The 272 runs of the clean reference took 1,409-1,417 s (../README.md, "Results"); 01's first run took
longer only because of the timeout described under "Runs that are not counted".

## What each family run showed

- **01, family 8.** The mutated build recorded the final cases.txt (261 cases) with `--ps-protocol` in
  1,220 s. expr.func_length and vector_index.vector_similarity stopped as in every C++ recording and
  were accepted as known failures. Against 01-ps/rec5, 12 cases are `different`: the nine the note
  lists (type_date.timestamp2, type_date.datetime_java, type_date.daylight_saving_time, two_order_by,
  driver5114_bug, datatype.replace, type_date.test_select_usec_to_time, update_range,
  delete.delete_range) and executor.basic, fts_index.basic_dml_sequel and groupby.group_by_basic.
  shift_check.py (in the run directory) matches every changed line to TIMESTAMP values moved by exactly
  8 hours: 251 lines, 261 values 8 hours earlier, 16 values 8 hours later, and no other change. The 16
  later ones are in type_date.datetime_java's second SELECT, which runs under `time_zone='-8:00'`: the
  binary cell now carries UTC whatever the session's time zone. type_date.timestamp2, for example,
  prints `1993-09-01 03:00:00.123400` instead of `1993-09-01 11:00:00.123400`.
- **02, family 5.** The corpus (393 files, the `test_dir_sha256` of r15) ran in 28 s with no failed
  file. Against 02-expr/r15, s1_unreached_0053 (90 rows in 38 statements) and s1_unreached_0054 (14 rows
  in 3 statements) are `different` and 391 files identical. Every changed row is a MAKE_SET call with
  two strings whose second string is missing: `make_set(2, 'a', 'Hello, World!')` prints the empty
  string, and in `make_set(c_int, 'a', 'abc')` rows 4, 6 and 8 print `a`, the empty string and `a`
  instead of `a,abc`, `abc` and `a,abc`. `check-recording` finds 0 problems, and the 40 known answers
  still hold.
- **03, family 6.** build_ob_error.sh compiled `ob_error` from the patched tree (its build.txt: tree
  status ` M src/share/ob_errno.cpp`, ob_errno.cpp sha256 2959f653...). run_ob_error_test.sh exits 1
  with `status=different`, and diff.txt holds one hunk, at line 82: `Message: rowid type mismatch`
  becomes `Message: rowid type mismatch, expect %.*s, got %.*s`. `cmp` against
  03-oberr/run1/test.result differs at line 82. As a control, `ob_error` built the same way from the
  reverted tree (ob_errno.cpp sha256 fc635746..., the note's clean value) gives `status=identical`.
- **04, family 9.** restart_scenarios.py with the reduced init ran in 28 s and exits 1 with
  `failed_cases` [restart_data]. Against restart-smoke/rec-1, after restart 1 (and again after restart
  2): `select * from t_data` gains rows 10 (`hotel`) and 12 (`juliet`); the index-only query gains
  `4 delta`, `9 golf`, `10 hotel` and `12 juliet`, the full-scan query `10 hotel` and `12 juliet`; the
  count line goes from 11, 10, 9, 8, 391.146, ..., 18.875 to 13, 12, 11, 10, 438.646, ..., 31.125; the
  amount query gains `12 5.500` and `10 42.000`; the `name like 'd%'` query prints `4 delta-again`
  twice. After restart 3, rows 5 and 11 are back and the index holds `2 bravo`, `5 echo` and `11 india`
  again. The three checks read `no`, and `seekdb is still running at the end` reads `yes`.
  restart_parameters and restart_mid_dml compare identical; `compare` exits 1 because restart_data is
  missing on the right, a recording problem.
- **05, family 7.** record.sh (the 272 cases, a fresh instance per case, `--plan-cache-stats`) ran from
  13:34 to 15:35 with no failed case, outside the 19:00-23:00 hours record.sh refuses. Against rec4, all
  272 .result files are identical and plan_cache.tsv differs in 271 cases. plan_check.py (in the run
  directory) tests the note's criteria: `empty_input` has 6 hits and 2 misses (rec4: 7 and 1); the 69
  cases whose longest read gap was under 0.1 s in rec1-rec4, and whose misses agree in all of them, all
  have more misses than in rec4, by 1 in 61 cases, 2 in 4, 3 in 3 and 8 in 1; none has fewer or the
  same. Over all cases misses rose in 261, stayed in 6 and fell in 5. The 11 that did not rise are slow
  cases, with reads 0.5 to 43 s apart; in 10 of them the C++ recordings (rec1 to rec4; rec1 and rec3
  stopped part way) already disagree on misses, and the 11th, histogram.stats_lock_and_unlock, was
  reached only by rec2 and rec4. The family's README then named no list file in the format
  `compare --plan-cache-not-comparable` reads, so not-comparable.tsv was turned into one
  (family-plan-not-comparable.list in the run directory), and the verdict came from plan_check.py.
  The review of the second set showed that list to be built from the pair it was checked on; the
  family's list and verdict were redone after it ("After the reviews", below).
- **06, the wire family.** wire_scenarios.py (the recorded versions of it and wire_client.py) with the
  reduced init ran in 70 s and exits 0: no check of the scenarios reads the flag. Against
  06-wire/run09, wire_ok_err_eof is `different` and the other nine scenarios identical. The diff is
  exactly the three packets the note names: request 36 (`set sql_mode = 'NO_BACKSLASH_ESCAPES'`)
  `S 1 45 0000002242...` becomes `S 1 45 0000002240...` (status 0x4222 to 0x4022), and requests 38 (the
  insert) and 39 (`do 1`) `S 1 7 00010022020000` becomes `S 1 7 00010022000000` (0x0222 to 0x0022).
- **08, family 13.** The corpus (22 files, the `test_dir_sha256` of r04) ran in 6 s with no failed file;
  `check-recording` finds 0 problems. Against 08-ik/r04, the 11 files the note named are `different`
  and the other 11 identical. `generate.py diff-tokens`: 1,198 statements identical, 38 token lists
  differ, 17 other statements differ, none missing. Every token that held an underscore is split or
  cut (`snake_case` becomes `snake` and `case`, `admin_01` becomes `admin` and `01`, `a_` becomes `a`),
  and `doc_len` follows the token count. The 17 are the MEMBER OF query, which loses id 4, and 16
  boolean-mode MATCH queries: the 14 whose term holds an underscore lose their row (for example
  `MATCH(cs) AGAINST('snake_case' IN BOOLEAN MODE)` no longer returns id 10), and the two on the
  smart-mode column `cs` whose term is `admin` or `snake`, a word that index now holds on its own, gain
  one (ids 9 and 10).
- **11, family 11.** sysbench_parity.py (the recorded versions, sha256 d4332c64... and 999e5c24...) ran
  in 133 s and exits 1. prepare and the three oltp_point_select runs are recorded as in the reference.
  In oltp_read_write_1 (one thread, seed 4) sysbench printed `FATAL: mysql_drv_query() returned error
  4012 (Statement timeout occurred, ...) for query 'COMMIT'` and exited 1; the recording is
  `oltp_read_write_1.partial` with `-- check: sysbench exited with status 0 and printed no FATAL line:
  no` and `-- sysbench exit code 1; error codes in its FATAL lines: 4012`, and oltp_read_write_16 and
  _64 were not run. Against 11-conc/sb-2: 4 identical, 3 missing and a recording problem, so `compare`
  exits 1.

## How they ran

1. **Builds, 13:24:59 to 13:27:41.** build.sh in the run directory, as the first set's jobs/a12.sh did.
   For each patch in turn: `git apply --check` and `git apply` in /Users/colin/seekdb-dev/ref-834bbee1e;
   `SDKROOT=/Users/colin/seekdb-dev/ref-archive-834bbee1e/MacOSX26.2.sdk make -j14 seekdb` in
   build_release/ (8 to 23 s: each build compiled the unity object that holds the patched file, each
   build after the first also the one that holds the file the previous revert restored, and linked);
   copy the binary aside with its sha256; `git apply -R`; check that `git diff` equals
   ../../reference-build.patch again. Each binary's applied.diff has the same hunks as its patch. For
   03, build_ob_error.sh also built `ob_error` from the patched tree (2 s), and once more from the
   reverted tree after the last patch, as the control. Then the clean rebuild (below).
2. **Family checks, 13:30 to 15:58.** runs.sh ran each family's command from its note, with the
   archived obclient and mysqltest (/Users/colin/seekdb-dev/ref-archive-834bbee1e/client) and
   `--max-retries 0 --no-ignore-trailing-whitespace` wherever the runner runs, on ports 3882, 3891,
   3892 and 3896, one instance at a time, and compared the result with the family's C++ recording named
   in the table.
3. **The 272 runs, 15:58 to 20:51.** runs.sh, then runs2.sh after the interruption below, ran the
   command of ../README.md ("How to run them") with the mutated binary and the archived clients, on
   port 3881, with the full init, `--max-retries 0 --no-ignore-trailing-whitespace` and no
   `--save-instance-dir`. judge272.py reads `failed_cases` from seekdb_result.json, removes the
   quarantined cases, and reports `retried_cases`, `ignore_trailing_whitespace` and the failed init
   statements: in every run no case was retried, the tolerance was off and no init statement failed.
   runs2.sh also saves the Mac's sleep and wake events during each run (`pmset -g log`, in
   sleep-events.txt); there were none. Once the Mac was on mains power again, runs2.sh ran the 272 a
   second time on the 01 build (01-ps-binary-timestamp-without-time-zone/all272-2/), for the reason
   under "Runs that are not counted".
4. **Before every run** the scripts checked that `git -C /Users/colin/seekdb-dev/migrate-to-rust diff
   --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py` succeeds, that tools/deploy has no
   local changes, that / has at least 8 GiB free (11 to 16 GiB throughout) and that the port is free.
   The runner (sha256 29e19d68...) was not edited.

Outputs are in /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/mutations/<patch name>/ (build.log,
applied.diff, seekdb.sha256, family-*/, and all272/ or all272-2/), with build.sh, runs.sh, runs2.sh,
judge272.py, shift_check.py, changed_rows.py, plan_check.py and timings.txt beside them.

## Runs that are not counted

- **05's first 272 run** (05-plan-key-includes-session-id/all272/). The Mac went into clamshell sleep
  on battery at 18:02:40 and woke fully only at 18:45:29 (`pmset -g log`); the server log has no lines
  from 18:03 to 18:11 and from 18:13 to 18:28. The run had finished 171 cases and was in the 172nd,
  geometry.geometry_partition_table_mysql (about 3 s in every other run). At 18:48 the runner and
  mysqltest were no longer running, while the server they had started still was; it was stopped at
  18:53 and its instance directory removed. The run was repeated from the start as all272-2, whose
  sleep-events.txt is empty. Evidence: all272/interrupted.txt.
- **vector_index.rebuild_vector_index in 01's first 272 run** is the only failure outside the
  quarantine list in any 272 run. The case's last statement, `PURGE RECYCLEBIN` (test line 359),
  failed with 4012 after 1,071 s; all 334 lines of output before it equal the .result. While it ran,
  the server log showed the table-lock pre-check retrying `OB_TABLET_NOT_EXIST` on a tablet the GC had
  already deleted (78,263 `tablet is already deleted` lines in 2 MB of log;
  all272/purge-hang-evidence.txt). That is the hang in the reference itself that
  ../../families/ps_protocol/README.md and ../../families/concurrency/README.md describe (C++
  recordings 01-ps rec1 and rec4, 11-conc par-2), and that the first set met in this same case under
  its mutation 07. The patch changes only the binary-protocol encoder, which the 272 cases never use (0
  hits in AB.profdata, as the note shows), so the failure is not counted as a catch. A second 272 run
  of the same build (all272-2) failed only three quarantined cases, and rebuild_vector_index passed in
  64 s. Since 2026-09-28 the seven cases that send `PURGE RECYCLEBIN` are on ../../quarantine.tsv, so
  this failure is left out by the rule itself: counting it no longer takes a judgment about the
  mutation.

## Where the runs differ from the notes

- **01:** three more cases differ than the note lists (executor.basic, 142 lines;
  fts_index.basic_dml_sequel, 3; groupby.group_by_basic, 2), all TIMESTAMP values; and in
  type_date.datetime_java 16 values move 8 hours later, not earlier, because that SELECT runs under
  `time_zone='-8:00'`.
- **02:** the note says every metadata line stays; 41 of them change, all in mysqltest's `Max length`
  column, the length of the longest value returned. Type and length stay.
- **04:** of the two outcomes the note gives for the `name like 'd%'` query, the first happened: row 4
  prints twice, with no error 4377.
- **05:** misses did not rise in 11 slow cases (6 the same, 5 fewer); in 10 of them the C++
  recordings already disagree with each other on misses. The note's criteria are about the fast cases,
  and they hold.
- **08:** 16 boolean-mode MATCH queries differ, not 14: besides the 14 with an underscore in the term,
  two queries on the smart-mode column gain a row.
- **11:** the 4012 came in the one-thread run, oltp_read_write_1, which the note gives as the less
  likely place.

## The reference worktree afterwards

After the last patch, the clean rebuild (13:27, 14 s) compiled the unity object of the last reverted
file (ob_tx_api.cpp's) and linked. `git -C /Users/colin/seekdb-dev/ref-834bbee1e diff` then equals
../../reference-build.patch, `git status --porcelain` shows only ` M cmake/Env.cmake`, and
build_release/src/observer/seekdb has sha256
4ed9644afdc40e154023a4f96b9cee9e005d26b7276326d735535bad5f55bd63.
Checked again at 20:27, after the last planned 272 run: `make -j14 seekdb` compiled and linked
nothing and left the sha256 unchanged. At 20:51, after the second 01 run: `git diff` still equals
../../reference-build.patch, `git status --porcelain` still shows only ` M cmake/Env.cmake`, and all
eight patches still pass `git apply --check`.

## The mutated binaries

Each was deleted after its runs; its sha256 stays in seekdb.sha256 beside its run outputs.

| Patch | seekdb sha256 |
|---|---|
| 01 | b593a53060d4c124b0667d0a6156da96bf74a81b0fb8bb71d044484ee8040de4 |
| 02 | 7af21e47f6e7289c0c7a53553eed7114e0e6091cac7d2866f57c722bc1991466 |
| 03 | 3c9e84fd0e877110361b6316f3b51fa3e63304eefd59d31f57920a4337ae288f; its `ob_error`: 5dc150e3f2df4c2162663b7565dd27447c836b2fd14717f110b9af83258680a5 (in ob_error.sha256) |
| 04 | cd701247af25bda76712f53b761a0d3cc7b19ee7378892ae2a92f11f57e63b2d |
| 05 | 96b52183dd9781a64d910eaeaca06246ccfb39d0ea0bf18e893b89e4d2b84c46 |
| 06 | 36cfdf829743c073cdf9e30de283786d58ba661664e0df5b4c347279bf464266 |
| 08 | 95fda99e5673bbb655e401064046682479c762aa6833b34a9ee5164c6c9e3c71 |
| 11 | 773f5edd3ce38db5b1c4e2b1e85957d8ed35c0320993f693a5a05a44c2665bf5 |

## After the reviews, 2026-09-28/29

The unit that applied the two reviews of the second set added one patch and ran five of the eight
again, because their families changed (outputs under
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/). Builds: review/mutations/build.sh (01,
02, 08, 12) and review/mutations2/build.sh (04), as build.sh above: `git apply --check` and `git
apply` in /Users/colin/seekdb-dev/ref-834bbee1e, `make -j12 seekdb` with the archive's SDK, copy the
binary aside, `git apply -R`, check that `git diff` equals ../../reference-build.patch; then one clean
rebuild. Every binary was deleted after its run. Afterwards `git diff` equals reference-build.patch
and `git status --porcelain` shows only ` M cmake/Env.cmake`. A relink gives the worktree binary a new
sha256 each time (4ed9644a... before, f3363191... after the first clean rebuild); judge runs use the
archived binary, never this one.

| Patch | Family | Caught by the family? | Evidence |
|---|---|---|---|
| 01-ps-binary-timestamp-without-time-zone (again) | 8, cases.txt of 268 | yes | against ps-r7: 12 `different`, 254 identical (the seven purge cases among them), 2 known failures accepted |
| 02-expr-make-set-drops-last-string (again) | 5, the corpus with the plan cache on | yes | against expr-r18: s1_unreached_0053 and s1_unreached_0054 `different` (41 MAKE_SET statements, 104 rows), 391 identical |
| 04-restart-replay-skips-user-deletes (again) | 9, with `stack_size` in restart_parameters | yes | restart_data a `.partial` with its three `no` checks; against rs-3, restart_data missing, the other two identical |
| 05-plan-key-includes-session-id (compared again, not rebuilt) | 7, with the pinned not-comparable.list | yes | `compare` of the recording of 2026-09-28 against rec4 and against rec7 exits 1 with every compared case `different` (70 of 70), and against rec8 too; rec7 against rec8, the second held out from the list, exits 0 |
| 08-ik-underscore-not-letter-connector (again) | 13, with the relevance column | yes | against ik-r07: the same 11 files `different`; 16 searches change ids and 36 more only their relevance |
| 12-memory-memstore-reserve-halved (new) | 12, memory budgets | yes | memstore_fill a `.partial`: 47 chunks accepted instead of 13, 51.3 MB above the line instead of 1.7 MB; `compare --mask mem-hold` against mem-10 exits 1 |

Families 1, 3 and 4 have no patch of their own: the first set's 14 mutated outputs, compared with
family 1's C++ recording f1-p1, catch all 14 (../../families/differential/README.md, "The mutations";
../../families/masks/README.md). Family 6's `ob_error` of patch 03 was built again from the patched
tree for the new catalog sweep, which it fails at code 5870 (../../families/ob_error/README.md). The
272 configured cases were not run on the 12 build: the line it changes decides a refusal the 272 never
reach (0 hits at ob_access_service.cpp:657 in AB.profdata).
