# iOS Complete Layered Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every test shipped by the current seekdb checkout, execute every device-compatible runtime assertion on a physical iPhone, retain inherently host-only gates on macOS, and publish exact passed, failed, excluded, and unavailable counts without treating compilation as device evidence.

**Architecture:** A deterministic inventory generator discovers the current source revision's test corpus and joins it with a tracked classification manifest. A test-only iOS registry runs C++, Rust, SQL/mysqltest-derived, vector, lifecycle, and memory cases inside the signed App and writes append-only JSONL evidence; a Mac runner only installs, controls, and validates device-produced evidence. Production archives keep test symbols disabled, and every layer finishes by rerunning the 36-step SQL and clean-stop persistence baseline.

**Tech Stack:** C++17, Objective-C++/UIKit, Rust 1.98.1, CMake, Python 3 `unittest`, JSONL, Xcode 27 `devicectl`, internal `ObMySQLProxy`.

---

### Task 1: Generate and validate the complete test inventory

**Files:**
- Create: `unittest/ios_build/generate_test_inventory.py`
- Create: `unittest/ios_build/test_inventory.py`
- Create: `unittest/ios_build/ios-test-classification.json`
- Modify: `docs/ios-test-inventory-schema.md`

- [ ] **Step 1: Write failing discovery and validation tests**

Require deterministic discovery of 283 active mysqltest files, the 272 cases selected by `tools/deploy/mysqltest_config.yaml`, 500 legacy `tools/obtest` files, three Rust `#[test]` functions, nine orphan C++ `TEST`/`TEST_F` registrations, the iOS probes, and the host Python tests. Require unique IDs, tracked source paths, valid execution classes, explicit reasons for exclusions, and no unclassified active case.

- [ ] **Step 2: Verify RED**

Run `python3 unittest/ios_build/test_inventory.py -v` and confirm failure because the generator and classification manifest do not exist. The direct command is required because the repository's `unittest/` directory name conflicts with Python's standard-library `unittest` package when addressed as `unittest.ios_build`.

- [ ] **Step 3: Implement deterministic discovery**

Use `git ls-files -z` as the source of truth. Import the existing seekdb mysqltest discovery logic instead of duplicating YAML selection semantics. Parse Rust and orphan GTest registrations only as source inventory because the current checkout has no registered C++ test target. Emit sorted JSONL with source commit and corpus digest under `build_ios_arm64/generated/`.

- [ ] **Step 4: Add explicit classification overrides**

Every discovered test receives one of `device-native`, `host-driven-device`, or `host-only`, plus `required`, `excluded`, or `blocked`. Missing C++ unittest sources use `source absent at source revision`; Linux/Windows/compiler/build/signing cases name their exact platform dependency; device-equivalent mappings must reference an existing registry ID and may not form cycles.

- [ ] **Step 5: Verify GREEN and commit**

Run `python3 unittest/ios_build/test_inventory.py -v`, generate the inventory twice and compare bytes, run the full iOS Python suite, then commit as `test(ios): inventory layered validation corpus`.

### Task 2: Add the device registry and structured evidence protocol

**Files:**
- Create: `unittest/ios_build/device_test_registry.h`
- Create: `unittest/ios_build/device_test_registry.cpp`
- Create: `unittest/ios_build/device_evidence.h`
- Create: `unittest/ios_build/device_evidence.cpp`
- Create: `unittest/ios_build/run_device_suite.py`
- Create: `unittest/ios_build/test_device_registry.py`
- Modify: `unittest/ios_build/app/main.mm`
- Modify: `unittest/ios_build/app/CMakeLists.txt`

- [ ] **Step 1: Write failing registry/evidence contract tests**

Require stable case IDs, duplicate rejection, suite filtering, per-case timeout metadata, and the sequence `run_start`, `case_start`, assertion records, `case_end`, `run_complete`. The validator must reject stale run/build IDs, missing completion, duplicate cases, incomplete registry coverage, nonzero case results, or host-generated assertions.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest unittest.ios_build.test_device_registry -v` and confirm the missing registry and runner cause the expected failures.

- [ ] **Step 3: Implement the minimal registry and JSONL writer**

Define focused C++ types with English documentation comments. Write each record and flush before continuing. The App selects `SEEKDB_IOS_TEST_SUITE`, `SEEKDB_IOS_TEST_FILTER`, and `SEEKDB_IOS_TEST_RUN_ID`; ordinary production-mode launch retains the existing SQL behavior.

- [ ] **Step 4: Implement the host-driven runner**

Launch the already installed App, poll and copy only allowlisted evidence, validate registry/result completeness, and scan for new crash/Jetsam reports. Raw `devicectl` JSON and device/signing identifiers must never be persisted in repository evidence.

- [ ] **Step 5: Verify host contracts, build, sign, and run a smoke case on iPhone**

The smoke registry and the subsequent 36-step SQL/restart baseline must pass on the physical device before commit `test(ios): add device test registry`.

### Task 3: Cover the current C++ test corpus on device

**Files:**
- Create: `unittest/ios_build/cpp_device_tests.cpp`
- Create: `unittest/ios_build/test_cpp_device_tests.py`
- Modify: `unittest/ios_build/ios-test-classification.json`
- Modify: `unittest/ios_build/app/CMakeLists.txt`

- [ ] **Step 1: Write failing registry classification tests**

Require every one of the nine orphan GTest registrations to have either a device case/equivalent or an exact exclusion. Require the manifest to state that the normal C++ target count is zero because its build definitions and sources are absent from the current revision.

- [ ] **Step 2: Verify RED, then implement focused device equivalents**

Port only behavior that exists on iOS: allocator size/alignment and ordinary allocation/free invariants plus `ob_error` mapping checks. Exclude `fork`, Linux `<malloc.h>`, jemalloc hook replacement, and unavailable build registrations with exact reasons; do not copy test files into `tools/`.

- [ ] **Step 3: Run on device and preserve individual case records**

Every compiled case must execute inside the App. Run the SQL/restart baseline afterward, update inventory results, and commit as `test(ios): cover available cpp cases on device`.

### Task 4: Execute Rust runtime tests on device

**Files:**
- Modify: `rust/Cargo.toml`
- Modify: `rust/sql-nio/Cargo.toml`
- Create: `rust/sql-nio/src/device_tests.rs`
- Modify: `rust/sql-nio/src/lib.rs`
- Modify: `rust/sql-nio/include/nio.h`
- Modify: `cmake/Rust.cmake`
- Modify: `src/observer/CMakeLists.txt`
- Create: `unittest/ios_build/rust_device_tests.cpp`
- Create: `unittest/ios_build/test_rust_device_tests.py`
- Modify: `deps/ios-build/build_app.py`

- [ ] **Step 1: Write failing shared Rust case and ABI tests**

Refactor the three existing cert/TLS assertions into shared `Result`-returning case functions called by both host `#[test]` wrappers and the device registry. Test stable enumeration, invalid index/capacity, bounded diagnostics, intentional panic capture, and successful execution after the captured panic.

- [ ] **Step 2: Verify RED before adding the ABI**

Run `cargo test --locked -p sql-nio` and the focused Python contract; confirm failure for the missing device ABI/profile.

- [ ] **Step 3: Implement a test-only unwind profile and C ABI**

Add an `ios-device-tests` feature and an `ios-device-test` Cargo profile with `panic="unwind"`. Export fixed-width C-compatible result structures only under the feature. Catch unwind at the outer boundary; production release/cmake-debug remain `panic="abort"` and export no device-test symbols.

- [ ] **Step 4: Cross-build, link, and run the Rust registry on iPhone**

Use the test Rust archive instead of, never alongside, the production archive. Require all three real cases plus panic-containment/continuation cases to complete, then rebuild production and prove the test symbols are absent.

- [ ] **Step 5: Run host Rust gates and device SQL baseline**

Run fmt, unit tests, doc-tests, Clippy, cbindgen drift, the iPhone suite, and the 36-step/restart baseline. Commit as `test(ios): run rust cases on device`.

### Task 5: Classify and execute the active mysqltest corpus

**Files:**
- Create: `unittest/ios_build/mysqltest_parser.py`
- Create: `unittest/ios_build/mysqltest_device_cases.cpp`
- Create: `unittest/ios_build/test_mysqltest_parser.py`
- Modify: `unittest/ios_build/ios-test-classification.json`
- Modify: `unittest/ios_build/app/CMakeLists.txt`

- [ ] **Step 1: Write failing parser and corpus-coverage tests**

Require recursive `--source` resolution, SQL statement splitting, expected-error directives, stable source/case provenance, and explicit rejection of unsupported connection, process, shell, topology, server-management, and result-rewrite semantics. Require all 283 active files and all 272 CI-selected cases to have a classification.

- [ ] **Step 2: Verify RED and implement only lossless translation**

Translate single-connection deterministic SQL and exact expected engine errors into generated device case data. Never silently drop directives. Protocol/multi-connection cases become host-driven only after a secure transport exists; otherwise retain host-only/not-applicable with exact reasons.

- [ ] **Step 3: Execute every translated case on iPhone**

Start with `empty_table`, `distinct`, `aggr_bug200109`, `limit`, `expr.expr_nseq`, and `sqlancer_optimizer_regressions`, then expand until no losslessly translatable case remains unexecuted. Record each source case independently.

- [ ] **Step 4: Run the existing host mysqltest gate where supported**

Keep the 272-case runner result separate from iPhone results. Run the device SQL/restart baseline and commit as `test(ios): execute portable mysqltest cases`.

### Task 6: Add complete vector correctness and persistence coverage

**Files:**
- Create: `unittest/ios_build/vector_probe.cpp`
- Create: `unittest/ios_build/vector_probe.h`
- Create: `unittest/ios_build/test_vector_probe.py`
- Modify: `unittest/ios_build/app/CMakeLists.txt`
- Modify: `unittest/ios_build/ios-test-classification.json`

- [ ] **Step 1: Write failing vector registry contract tests**

Require deterministic insert/readback, exact and approximate neighbors, scalar filters, update/delete visibility, commit/rollback, clean-stop/relaunch persistence, and bounded repeated queries with documented neighbor or recall assertions.

- [ ] **Step 2: Verify RED and implement through `ObMySQLProxy`**

Use a small fixed corpus and isolated schema. Do not open an unauthenticated listener. Flush every case result and record query count, duration, and correctness threshold.

- [ ] **Step 3: Run two physical-device cycles**

First cycle builds and queries indexes; second cycle uses the same data directory and verifies persistence. No new crash/Jetsam may appear. Re-run the ordinary SQL baseline and commit as `test(ios): validate vector search on device`.

### Task 7: Validate foreground/background, lock recovery, and memory pressure

**Files:**
- Create: `unittest/ios_build/lifecycle_probe.h`
- Create: `unittest/ios_build/lifecycle_probe.mm`
- Create: `unittest/ios_build/memory_probe.cpp`
- Create: `unittest/ios_build/run_device_lifecycle.py`
- Create: `unittest/ios_build/test_device_lifecycle.py`
- Modify: `unittest/ios_build/app/main.mm`
- Modify: `unittest/ios_build/app/CMakeLists.txt`

- [ ] **Step 1: Write failing lifecycle/evidence tests**

Require scene active/inactive/background/foreground events, protected-data availability changes, memory-warning receipt, physical footprint samples, bounded pressure levels, SQL responsiveness when allowed, final clean stop, and same-directory relaunch.

- [ ] **Step 2: Verify RED and implement event recording**

Observe UIKit scene/application notifications and `TASK_VM_INFO`/`os_proc_available_memory`. Memory workloads must remain bounded and stop before the logical budget; Jetsam or termination is failure, never a passing endpoint.

- [ ] **Step 3: Automate supported controls and record manual lock gate explicitly**

Use supported App activation/home controls for foreground/background. Use `devicectl` memory-warning delivery. Because `devicectl` cannot lock/unlock a physical phone, pause for the user to lock and unlock once, and accept only device-recorded protected-data/lifecycle evidence with timestamps from the current run.

- [ ] **Step 4: Run full lifecycle and memory sequence on iPhone**

Execute launch, foreground/background/foreground, manual lock/unlock, idle/SQL/vector/repeated-query/controlled-pressure levels, warning delivery, clean stop, and relaunch persistence. Rerun the SQL baseline and commit as `test(ios): validate lifecycle and memory pressure`.

### Task 8: Run the complete matrix and publish bounded results

**Files:**
- Create: `docs/ios-layered-test-results.md`
- Modify: `docs/ios-layered-test-strategy-design.md`
- Modify: `docs/developer-guide/zh/ios-build.md`
- Modify: `docs/developer-guide/zh/ios-change-log.md`
- Modify: `unittest/ios_build/ios-test-classification.json`

- [ ] **Step 1: Run every host-only gate and every device suite from a clean production rebuild**

Capture separate counts for device-native, host-driven-device, host-only, excluded/not-applicable, blocked, passed, and failed. Verify inventory completeness against the source revision and App registry.

- [ ] **Step 2: Repair reproducible repository defects with TDD**

For each failure, preserve the minimal evidence, reproduce, add a failing focused test, implement the smallest fix, rerun the affected layer, and then rerun SQL clean-stop persistence. External/platform limitations remain explicit gaps.

- [ ] **Step 3: Write the tracked English results report and update required iOS records**

List commands, non-sensitive device/OS/build identity, exact counts, exclusions and reasons, defect commits, crash/Jetsam checks, and unresolved gaps. Never include signing identity, team/account, device unique identifier, private key, provisioning content, or raw device metadata.

- [ ] **Step 4: Final verification and review**

Run all Python, Shell, Rust, inventory, production-symbol, device-evidence, neutrality, and documentation checks; confirm a clean worktree. Request a final seekdb code review and commit as `docs(ios): publish complete layered validation`.
