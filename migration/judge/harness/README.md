# The judge harness

PLAN.md section 4. The judge runs the pinned C++ reference and the Rust build through the same
external interfaces. This directory holds the harness pieces that are not the runner itself.

## The runner

The runner is the one CI uses, extended in place: `.github/script/seekdb/mysqltest_for_seekdb.py`
(PLAN.md section 4, item 1; section 10, action 6). `runner.patch` here is the whole change against
834bbee1e; `runner-batch-a.patch` and `runner-batch-b.patch` are the two implementer runs before the
reviewers' fixes. Two adversarial reviewers checked disjoint batches (batch A: Fable 5.1; batch B:
Opus 5.5); the Opus 5.5 reviewer found five weakenings (record mode inheriting retries, an unasked
option that turned init failures into a pass, init not run statement by statement, failed cases
leaving nothing recorded, and the PLAN's judge commands leaving the trailing-whitespace tolerance on),
and the fixer resolved them or logged them (RULEBOOK.md DEV-003; PLAN.md section 10 commands).

Every judge run passes `--max-retries 0 --no-ignore-trailing-whitespace`. The defaults (3 retries,
trailing whitespace ignored) exist only so CI behaves as before.

| Option | Default | What it does |
|---|---|---|
| `--max-retries N` | 3 | Re-runs a failed case on a fresh instance up to N times; every attempt is logged and listed in `retried_cases` |
| `--case-list FILE` | all configured cases | Runs only the named configured cases, in configured order; unknown names, duplicates or an empty list fail the run |
| `--fresh-instance-per-case` | off | A new instance before every case |
| `--save-instance-dir DIR` | `$SEEKDB_COV_PROFRAW_DIR` | Before every destroy: stop the instance the way `destroy` does, copy `log/` and `seekdb*.profraw` to `DIR/<counter>-<reason>` |
| `--init-sql FILE`, `--init-user-sql FILE` | tools/deploy/init.sql, init_user.sql | The init files; any init failure fails the run |
| `--record-dir DIR` | off | Records each case's actual output to `DIR/<case>.result` (a failed case's log to `DIR/<case>.partial`) with `DIR/manifest.json`; requires `--max-retries 0` |
| `--ignore-trailing-whitespace` / `--no-ignore-trailing-whitespace` | on | The CI tolerance for trailing spaces and tabs; off for every judge run |

`compare --left DIR --right DIR [--out FILE] [--mask NAME]...` diffs two recordings byte for byte,
refuses recordings that are unfinished, retried, failed or made from different mysqltest, obclient,
init files, sdb.py or tools/deploy, and reports differing server binaries as a note. Masks are named
switches, off by default; the registry is empty until the EST and hash-order masks are added
(Decision 6). Exit 0 means every case is identical and there were no recording problems.

Every run writes `WORK_DIR/entry-gate.json`: each init statement with its line and a status
(succeeded, failed, not_run, unknown). Each init file goes to one obclient session, and statuses are
inferred from obclient's `ERROR ... at line N` lines (RULEBOOK.md DEV-003).

## Smoke test on the reference (2026-09-24)

On the archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb), port 3881, all with
`--max-retries 0 --no-ignore-trailing-whitespace`; outputs in
/Users/colin/seekdb-dev/mysqltest-runs/smoke-runner-834bbee1e/:

| Run | What | Result |
|---|---|---|
| s1 | join_basic, explain, empty_table; full init | success in 21 s; no retries; `ignore_trailing_whitespace` false; no case passed by the tolerance |
| s2 | join_basic, empty_table; reduced init; `--record-dir rec-s2` | success in 3 s; both cases recorded (10 and 76 lines); manifest `finished: true` |
| s3 | the same, `--record-dir rec-s3` | success in 2 s |
| compare | rec-s2 against rec-s3 | exit 0: 2 identical, 0 different, 0 missing, 0 recording problems |
| s4 | a copy of tools/deploy/init.sql with a failing `select` inserted at line 5 | fails as intended: entry-gate.json marks line 5 failed with `ERROR 1146 (42S02) at line 5`, the 4 statements before it succeeded, the 29 after it not_run, and all 6 statements of init_user.sql not_run; `init_failed_statements` 1 |

This is the one real check against obclient that DEV-003 asks for.

## Restart scenarios on the reference (2026-09-24)

`restart_scenarios.py` (restart-scenarios.md) ran twice on the archived reference with the reduced
init, port 3882: 26 s and 25 s, all three scenarios (restart_data, restart_parameters,
restart_mid_dml) passed their own checks, and `compare` found the two recordings identical (3 of 3, no
recording problems). Outputs: /Users/colin/seekdb-dev/mysqltest-runs/00b/restart-smoke/. The first real
run confirmed what the stubbed tests could not: obclient keeps an open session's transaction until the
kill, the client stops on a lost-connection error, and SHOW PARAMETERS prints the name and value
columns the script reads. The recordings carry the script's sha256, so any later edit to the script
means recording the C++ reference again.
