# Standalone iPhone Test Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `./run.iphone.test.sh` as the only command needed to execute, resume, and report the complete eight-stage physical-iPhone validation workflow.

**Architecture:** A thin shell launcher delegates to a Python state-machine. The state-machine owns atomic checkpoints, cross-date run selection, per-case failure files, phase isolation, sanitized command execution, and deterministic reports. Phase adapters expose the existing inventory/registry/C++/Rust gates and the new mysqltest/vector/lifecycle/final-matrix gates through one versioned command contract.

**Tech Stack:** POSIX shell, Python 3 `unittest`, JSON/JSONL, C++17, Objective-C++/UIKit, Xcode `devicectl`, existing iOS device registry.

---

### Task 1: Implement run selection and atomic checkpoint storage

**Files:**
- Create: `unittest/ios_build/iphone_test_state.py`
- Create: `unittest/ios_build/test_iphone_test_state.py`

- [ ] **Step 1: Write failing state tests**

Cover default same-day continuation, cross-date `--resume` selecting the newest incomplete `started_at`, no-candidate resume failure, `--restart` backup behavior, atomic save, interrupted `running` recovery, source/config incompatibility, passed-case skipping, and failed-case retry.

- [ ] **Step 2: Verify RED**

Run `python3 unittest/ios_build/test_iphone_test_state.py -v`; expect import failure for `iphone_test_state`.

- [ ] **Step 3: Implement immutable run identity and atomic persistence**

Use timezone-aware ISO timestamps, UUID run IDs, schema version 1, temporary-file write plus flush/fsync/rename, and directory fsync. Keep `started_at` immutable and update `last_resumed_at`. Reject path traversal and incompatible source/schema/config fingerprints.

- [ ] **Step 4: Verify GREEN and commit**

Run focused and complete iOS Python tests, then commit `test(ios): add resumable iphone test state`.

### Task 2: Implement failure files, phase engine, and deterministic reports

**Files:**
- Create: `unittest/ios_build/iphone_test_runner.py`
- Create: `unittest/ios_build/test_iphone_test_runner.py`
- Modify: `unittest/ios_build/iphone_test_state.py`

- [ ] **Step 1: Write failing orchestration tests**

Require eight ordered phases, explicit case transitions, one collision-safe failure JSON per failed case directly under `iphone_test/YYYY-MM-DD`, isolated-case continuation, infrastructure stop, missing-adapter failure, bounded diagnostics, nonzero aggregate exit, and deterministic `summary.json`/`summary.md`.

- [ ] **Step 2: Verify RED**

Run `python3 unittest/ios_build/test_iphone_test_runner.py -v`; expect missing runner symbols.

- [ ] **Step 3: Implement the phase/case protocol**

Define `PhaseAdapter`, `CaseResult`, failure categories, retry safety, and sanitized subprocess results. Mark a case running before dispatch and persist every terminal transition. Preserve old failure files after a retry passes.

- [ ] **Step 4: Implement reports and redaction**

Reject known device/signing metadata keys and UUID-like values except the runner-owned run ID. Reports group device-native, host-driven-device, host-only, pass, fail, blocked, excluded, and incomplete counts.

- [ ] **Step 5: Verify GREEN and commit**

Run focused/full tests and commit `test(ios): add standalone phase orchestration`.

### Task 3: Add the shell command and secure local configuration

**Files:**
- Create: `run.iphone.test.sh`
- Create: `unittest/ios_build/run_all_iphone_tests.py`
- Create: `unittest/ios_build/test_run_all_iphone_tests.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing CLI tests**

Cover shell failure propagation, mutually exclusive lifecycle flags, default output root, argument forwarding, environment-only device/signing values, multiple-device refusal, physical-device-only selection, and dry-run output without secret serialization.

- [ ] **Step 2: Verify RED**

Run the focused CLI test and confirm the missing launcher/entrypoint failure.

- [ ] **Step 3: Implement the thin shell and Python CLI**

The shell uses `set -eu`, resolves its own repository root, and `exec`s Python. The CLI accepts `--resume`, `--restart`, output root, non-secret suite controls, and process-local device/bundle/signing inputs. It prints the selected original start-date directory before external actions.

- [ ] **Step 4: Ignore generated run output and verify**

Ignore `/iphone_test/` while keeping all source and docs tracked. Run shell syntax, CLI tests, full Python tests, and commit `feat(ios): add one-command iphone test runner`.

### Task 4: Register stages 1–4 as standalone adapters

**Files:**
- Create: `unittest/ios_build/iphone_test_phases.py`
- Create: `unittest/ios_build/test_iphone_test_phases.py`
- Modify: `unittest/ios_build/run_all_iphone_tests.py`

- [ ] **Step 1: Write failing adapter-contract tests**

Require inventory, registry smoke, C++ suite, and Rust test/production isolation adapters. Each adapter must expose stable case IDs, execution class, command, timeout, evidence validator, and SQL/restart follow-up requirement.

- [ ] **Step 2: Verify RED and implement adapters**

Reuse `generate_test_inventory.py`, `run_device_suite.py`, build marker checks, and existing device case IDs. Do not parse raw device metadata into run output.

- [ ] **Step 3: Run stages 1–4 through only the standalone command**

Interrupt once after a completed case, invoke `--resume`, and prove the original start-date directory and passed-case skip behavior. Commit `test(ios): automate completed iphone phases`.

### Task 5: Add the mysqltest standalone adapter

**Files:**
- Create: `unittest/ios_build/mysqltest_parser.py`
- Create: `unittest/ios_build/mysqltest_device_cases.cpp`
- Create: `unittest/ios_build/run_mysqltest_phase.py`
- Create: `unittest/ios_build/test_mysqltest_parser.py`
- Modify: `unittest/ios_build/iphone_test_phases.py`
- Modify: `unittest/ios_build/ios-test-classification.json`

- [ ] **Step 1: Write failing corpus and parser tests**

Require all 283 active files and 272 CI-selected cases to be classified. Recursively resolve `--source`, preserve expected errors and provenance, and reject rather than drop unsupported connection/process/shell/topology/result-rewrite directives.

- [ ] **Step 2: Implement lossless device translation and host separation**

Generate registry data for every losslessly translatable single-connection case. Record protocol/multi-process cases as host-only or exact not-applicable; run the supported host mysqltest gate separately.

- [ ] **Step 3: Run every translated case through the standalone runner**

One failed source case produces one failure file. Resume retries only failed/pending cases. Re-run SQL/restart and commit `test(ios): automate mysqltest phase`.

### Task 6: Add vector correctness and persistence adapter

**Files:**
- Create: `unittest/ios_build/vector_probe.h`
- Create: `unittest/ios_build/vector_probe.cpp`
- Create: `unittest/ios_build/run_vector_phase.py`
- Create: `unittest/ios_build/test_vector_probe.py`
- Modify: `unittest/ios_build/iphone_test_phases.py`

- [ ] **Step 1: Write failing vector coverage tests**

Require deterministic readback, exact/ANN neighbors, filters, update/delete, commit/rollback, repeated queries, resource samples, and same-directory restart persistence.

- [ ] **Step 2: Implement device cases through internal SQL proxy**

Use a fixed small corpus, exact expected neighbors or documented recall thresholds, bounded repetitions, and no unauthenticated listener.

- [ ] **Step 3: Execute via the standalone command and commit**

Require crash/Jetsam delta zero and SQL/restart follow-up. Commit `test(ios): automate vector phase`.

### Task 7: Add lifecycle, manual lock, and memory-pressure adapter

**Files:**
- Create: `unittest/ios_build/lifecycle_probe.h`
- Create: `unittest/ios_build/lifecycle_probe.mm`
- Create: `unittest/ios_build/memory_probe.cpp`
- Create: `unittest/ios_build/run_device_lifecycle.py`
- Create: `unittest/ios_build/test_device_lifecycle.py`
- Modify: `unittest/ios_build/iphone_test_phases.py`

- [ ] **Step 1: Write failing lifecycle/checkpoint tests**

Require scene transitions, protected-data events, memory warning, footprint sampling, bounded pressure, clean stop/restart, terminal prompt timeout, and resume after interruption during manual lock.

- [ ] **Step 2: Implement automatic and manual controls**

Automate supported activation/background/memory-warning controls. Pause in the invoking terminal for physical lock/unlock and validate only current-run device events. Non-interactive mode records blocked, never pass.

- [ ] **Step 3: Execute via the standalone command and commit**

Run the bounded sequence, SQL/restart follow-up, and commit `test(ios): automate lifecycle and memory phase`.

### Task 8: Add final matrix reporting and end-to-end resume validation

**Files:**
- Create: `docs/ios-layered-test-results.md`
- Modify: `unittest/ios_build/iphone_test_phases.py`
- Modify: `unittest/ios_build/run_all_iphone_tests.py`
- Modify: `docs/developer-guide/zh/ios-build.md`
- Modify: `docs/developer-guide/zh/ios-change-log.md`
- Modify: `docs/iphone-test-runner-design.md`

- [ ] **Step 1: Run a controlled interrupted end-to-end test**

Start with `./run.iphone.test.sh`, interrupt after a persisted phase, resume on a simulated later date with `--resume`, and prove the original directory and completed-case skips.

- [ ] **Step 2: Run the complete physical-device matrix**

Execute all eight phases only through the standalone command. Preserve one file per failure, repair repository defects with TDD, and resume until no required failed/pending case remains.

- [ ] **Step 3: Publish exact bounded results**

Generate tracked English results and update the required Chinese iOS docs with exact counts, exclusions, known limits, clean-stop/crash outcomes, and the accumulated-container shutdown boundary.

- [ ] **Step 4: Final review and commit**

Run Shell, Python, Rust, inventory, device evidence, neutrality, credential scan, and `git diff --check`; request final seekdb code review and commit `docs(ios): publish standalone iphone validation`.

