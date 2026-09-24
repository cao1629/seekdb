# iOS Layered Test Strategy Design

## Status

Proposed for implementation. This document defines how seekdb validation is divided between a physical iPhone, a macOS build host, and host-driven device tests. It does not treat a host-only result as device evidence.

## Goals

- Exercise every testable iOS runtime behavior on a physical iPhone.
- Preserve host-side tests when the tested behavior is inherently a compiler, linter, build-system, or packaging concern.
- Make each result attributable to one of three execution classes: device-native, host-driven device, or host-only.
- Add deterministic evidence for startup failure cleanup, SQL behavior, vector search, persistence, shutdown, application lifecycle, and memory pressure.
- Fix reproducible seekdb or iOS-port defects and add focused regression coverage before rerunning the affected layer.

## Non-Goals

- Running Clippy, Python, CMake, Xcode, or code-signing logic on iOS.
- Claiming that every Linux- or process-oriented C++ test is portable to an App sandbox.
- Weakening iOS security, code signing, sandboxing, or memory protections to make a test pass.
- Exposing a production network listener solely for testing.
- Treating compilation or successful installation as runtime validation.

## Execution Classes

### Device-native

Code is cross-compiled for `arm64-apple-ios`, linked into a signed test application or XCTest bundle, and executed on a physical iPhone. Results must include the device model, OS version, application build identity, test name, duration, exit status, and structured assertions.

This class covers portable C++ tests, Rust runtime tests exposed through a test-only C ABI, SQL and vector cases executed through the in-process SQL proxy, lifecycle probes, and bounded memory-pressure workloads.

### Host-driven device

A macOS process controls or observes an application running on the iPhone. The behavior under test must occur on the device; the host only supplies input, lifecycle events, log collection, or assertions over device-produced evidence.

This class covers installation, launch, termination, foreground/background transitions, lock and unlock transitions where automation is supported, crash-log collection, and optional MySQL protocol tests if a secure test-only transport is implemented.

### Host-only

The tested behavior belongs to the development toolchain rather than the iOS runtime. These tests run on macOS and remain explicitly labeled host-only.

This class includes Clippy, Python build-script tests, CMake configuration tests, archive selection, Xcode link-command extraction, code-signing preparation, and C++ tests that require unsupported process, shell, dynamic-loading, or Linux-specific facilities.

## Test Inventory and Classification

An inventory generator will enumerate the repository's C++ unit tests and SQL regression suites without assuming that the current checkout exposes a single all-tests target. Each item will record:

- source path and owning module;
- required libraries, generated data, and external services;
- operating-system and process assumptions;
- estimated runtime and memory requirement;
- selected execution class;
- exclusion reason when it cannot run on iOS;
- the device-equivalent test, if one is required.

Classification must be reviewable data rather than an undocumented build-system decision. Unsupported tests remain visible in the final report.

## Device Test Application

The existing iOS probe will be extended with a test-only registry. Each registered case has a stable name, a function returning a seekdb error code, a timeout, and a result serializer. The registry runs tests sequentially by default so memory usage and global engine state remain attributable to one case.

The application writes append-only JSON Lines records under its sandbox Documents directory. A run starts with metadata, emits one record per assertion or test case, and ends with an explicit completion record. An interrupted run is therefore distinguishable from a successful empty run.

Production libraries must not acquire test-only behavior. Fault injection, Rust test exports, and additional lifecycle controls are compiled only into the test target.

## Startup Failure Cleanup

The first implementation item is the existing startup-failure cleanup defect. The desired invariant is:

1. `ObServer::init()` owns cleanup for its own initialization failure.
2. If `ObServer::start()` fails after successful initialization, the iOS wrapper requests stop, waits for started modules to quiesce, and destroys the singleton exactly once.
3. `curl_global_cleanup()` runs whenever `curl_global_init()` succeeded.
4. The original working directory is restored whenever it was captured, regardless of engine success or failure.
5. The primary engine error is preserved; cleanup failures are recorded separately and only become the return value when no earlier error exists.
6. The public state becomes `FAILED` on the failure path, and the single-run process contract remains unchanged.

A test-only fault point immediately after initialization and during startup will provide deterministic red/green coverage. The regression test must verify final state, working-directory restoration, cleanup markers, and absence of a newly generated crash report.

## C++ Unit Tests

Portable C++ tests will be compiled into one or more static test libraries and invoked by the device registry. GoogleTest process entry points will not be assumed; adapters may call existing test functions or register compatible cases in an XCTest-hosted executable.

Tests requiring `fork`, subprocess execution, writable repository paths, unrestricted sockets, Linux `/proc`, loadable plugins, or unavailable third-party binaries remain host-only. Where such a test protects portable business logic, a focused device-native equivalent will be added instead of emulating an unsupported operating-system facility.

The initial implementation targets the smallest representative groups: allocator and value semantics, SQL expression behavior, transaction and persistence behavior, and iOS lifecycle code. Expansion proceeds module by module after resource measurements remain within the device budget.

## SQL and mysqltest Coverage

The existing device-native SQL suite remains the baseline. SQL regression cases will be classified into:

- statements that can be translated losslessly into the structured in-process runner;
- protocol or client-behavior cases that require a host mysqltest client;
- server-management or multi-process cases that are not applicable to the in-process iOS product shape.

The preferred path is device-native execution through the internal SQL proxy. If protocol coverage is required, a test-only authenticated listener or an Xcode-supported forwarding mechanism must be designed separately and disabled in production builds. The test plan must not expose an unauthenticated network service on the phone.

Every migrated regression case retains its source suite and case name for traceability. Expected errors are asserted by exact seekdb error code when stable.

## Rust Validation

Rust unit-test logic that exercises protocol parsing, TLS name handling, compression, and response encoding will be exposed through a test-only C ABI and linked into the signed device application. Panics must be contained at the FFI boundary and converted into a failed structured result.

Clippy and rustdoc remain host-only because they are compiler tools, not runtime workloads. The host gate continues to run Rust unit tests and `cargo clippy --all-targets -- -D warnings`; device-native Rust coverage is an additional runtime gate, not a replacement.

## Vector Search Validation

The device suite will create isolated vector tables and cover:

- deterministic insert and readback;
- index creation and readiness;
- exact and approximate search on a small known corpus;
- filter predicates combined with vector search;
- update and delete visibility;
- transaction commit and rollback;
- persistence across a clean stop and relaunch;
- bounded repeated queries for leak and stability observation.

Assertions use deterministic expected neighbor sets or explicitly documented recall thresholds. Dataset sizes increase only after recording peak resident memory and execution time for the previous level.

## Application Lifecycle Validation

Host-driven tests will exercise launch, foreground-to-background transition, background-to-foreground transition, clean stop, relaunch with the same data directory, and user-requested termination. Lock/unlock testing will use supported Xcode device controls when available; otherwise it is a documented manual test with timestamps and device-produced state evidence.

The engine must not perform unsupported background work indefinitely. Each transition records engine state, SQL responsiveness when permitted, persistence state, and whether a new crash or jetsam report appeared.

## Memory Pressure Validation

Memory testing uses bounded workloads rather than attempting to disable iOS limits. Each level records the application footprint, engine logical budget, workload size, latency, final state, and any memory warning, jetsam, or crash evidence.

The sequence is:

1. idle engine baseline;
2. SQL and transaction workload;
3. vector index construction;
4. repeated vector queries;
5. controlled allocation pressure below the configured logical budget;
6. clean stop and persistence verification.

A termination is never reported as a passed stress test. The failure is classified using device logs before any code or budget change is proposed.

## Failure Handling and Repair Loop

For each failure:

1. preserve the exact app build, input, JSONL, console log, and crash or jetsam report;
2. reproduce with the smallest relevant layer;
3. identify whether the cause is seekdb, the iOS port, the test harness, the build host, or an operating-system limitation;
4. add a focused failing regression test when the defect is in code under repository control;
5. implement the smallest scoped fix;
6. rerun the focused test, the affected layer, the baseline SQL suite, and clean-stop persistence;
7. record unsupported external limitations without presenting them as fixed.

## Evidence and Reporting

Ignored raw evidence is stored under `build_ios_arm64/device-evidence/`. Important commands, dependency versions, test counts, outcomes, and limitations are summarized in the tracked iOS build and change-log documentation as required by the repository guidance.

The final report contains separate tables for:

- device-native passed, failed, and skipped tests;
- host-driven device passed, failed, and skipped tests;
- host-only passed, failed, and skipped tests;
- unsupported tests and exact reasons;
- defects fixed with regression-test references;
- unresolved product, platform, and evidence gaps.

## Implementation Phases

1. Fix startup failure cleanup and add deterministic fault-injection coverage.
2. Build the test inventory and classification manifest.
3. Add the device test registry and portable C++ adapter.
4. Add device-native Rust runtime tests while retaining host Clippy gates.
5. Migrate applicable SQL regression cases and evaluate protocol-test transport separately.
6. Add vector correctness and persistence coverage.
7. Add lifecycle, lock-state, and memory-pressure orchestration.
8. Run the complete layered matrix, repair reproducible repository defects, and publish the bounded results.

Each phase must leave the existing 36-step SQL suite and clean-stop persistence gate passing before the next phase begins.

## Acceptance Criteria

- Every discovered test is classified with an execution location or explicit exclusion reason.
- All device-compatible C++ and Rust runtime tests execute from a signed application on the physical iPhone.
- Applicable SQL regression behavior executes against the engine running on the iPhone.
- Vector correctness, persistence, repeated lifecycle, and bounded memory-pressure tests produce structured device evidence.
- Host-only Clippy, Python, CMake, and packaging tests remain green and are labeled accurately.
- Startup failure restores process-owned state and has deterministic regression coverage.
- No result is described as an iPhone pass unless the tested behavior executed on the physical device.
- The final report lists every remaining gap and does not infer success from compilation, installation, or missing evidence.
