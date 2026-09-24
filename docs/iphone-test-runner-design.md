# Standalone iPhone Test Runner Design

## Status

Approved for implementation. This document defines a standalone command that
executes the complete eight-stage iPhone validation workflow without requiring
an active Codex session. The runner resumes safely after interruption, records
one file per failed case, and never treats a missing test adapter, compilation,
installation, or host-only result as a physical-device pass.

## Goals

- Provide one repository command: `./run.iphone.test.sh`.
- Execute all eight stages of the layered iPhone validation workflow.
- Preserve progress after process termination, Mac restart, device disconnect,
  or a later-day continuation.
- Store each failed case as one independently readable file.
- Keep device and signing identifiers out of checkpoints and reports.
- Produce a machine-readable and human-readable final result.

## Non-Goals

- Hiding unsupported or unimplemented test adapters behind a passing status.
- Running compiler, linter, CMake, Python, or signing assertions on the iPhone.
- Weakening iOS signing, sandbox, background-execution, or memory protections.
- Automatically simulating a physical lock/unlock operation that Apple tooling
  does not support.

## User Interface

The primary command is:

```bash
./run.iphone.test.sh [--resume | --restart] [runner options]
```

The shell file is intentionally thin. It locates the repository Python runtime
and invokes `unittest/ios_build/run_all_iphone_tests.py`. All state-machine,
checkpoint, validation, and reporting behavior lives in the Python module and
is covered by unit tests.

### Default invocation

Without either lifecycle option, the runner uses
`iphone_test/YYYY-MM-DD/`, where the date is the local start date. If that
directory already contains an incomplete checkpoint, it continues that run.
Otherwise it creates a new run for the current date.

### Cross-date resume

`--resume` scans `iphone_test/*/checkpoint.json`, validates each candidate, and
selects the incomplete run with the latest `started_at` value. It continues
writing to the original start-date directory even when resumed on a later day.
The checkpoint retains `started_at` and updates `last_resumed_at` on every
resume. Passed cases are skipped; failed and pending cases are attempted again.

If no incomplete checkpoint exists, `--resume` exits nonzero and does not
silently create a new run. The selected run directory is printed before any
device or build action begins.

### Restart

`--restart` creates a new run for the current date. If a run already occupies
that date directory, the runner first preserves its checkpoint and reports
under timestamp-suffixed backup file names inside the same date directory. It
then creates a fresh checkpoint without deleting prior failure files or raw
evidence.

The three lifecycle modes are mutually exclusive where applicable. Invalid
combinations fail before invoking build or device tools.

## Output Layout

The user-visible run directory has exactly two directory levels:

```text
iphone_test/
  YYYY-MM-DD/
    checkpoint.json
    summary.json
    summary.md
    failure-<phase>-<case-slug>-<hash>.json
    evidence-<phase>-<case-slug>-<hash>.jsonl
```

Failed cases are stored directly inside the start-date directory. There is no
third-level `failures/` directory. Each failure file represents exactly one
stable case ID and contains:

- schema version, source commit, run ID, phase, and case ID;
- attempt count and start/end timestamps;
- failure category and bounded diagnostic text;
- process exit status where applicable;
- paths to allowlisted, sanitized evidence;
- whether retry is safe and whether later cases continued.

Case IDs are converted to a bounded filesystem-safe slug and a short hash is
added to prevent collisions. Failure files never contain a device identifier,
Apple team identifier, account, certificate identity, provisioning content,
private key, or raw `devicectl` response.

## Checkpoint Model

`checkpoint.json` is the authoritative progress record. It contains:

- schema version and runner version;
- immutable run ID, original `started_at`, and source commit;
- `last_resumed_at` and the non-unique device model/OS when available;
- sanitized configuration fingerprints rather than secret values;
- eight ordered phase records;
- every case's `pending`, `running`, `passed`, `failed`, `excluded`, or
  `blocked` state;
- attempt count, bounded diagnostic, evidence path, and completion timestamps;
- final aggregate status.

Checkpoint updates use write-to-temporary-file, file flush, `fsync`, and atomic
rename. A case is marked `running` before its command starts and reaches a
terminal state only after result and evidence validation. A checkpoint found
with a `running` case after interruption changes that case back to `pending`
and increments its interruption count before retry.

Resume refuses to combine evidence from a different source commit, runner
schema, bundle configuration, or device-test build identity. The operator must
use `--restart` after such an incompatible change.

## Execution Model

The runner executes these phases in order:

1. inventory generation and classification validation;
2. device registry and evidence smoke validation;
3. current-revision C++ device equivalents and exact exclusions;
4. Rust device runtime cases plus production-symbol isolation;
5. active mysqltest classification and every losslessly translatable device
   case, with the supported host mysqltest gate reported separately;
6. vector correctness, approximate/exact search, filters, mutation,
   transactions, repeated queries, and clean-restart persistence;
7. foreground/background, protected-data lock recovery, memory warning,
   bounded memory pressure, clean stop, and restart;
8. complete host/device matrix verification and final report generation.

Every device phase validates source build identity, selected case coverage,
terminal JSONL, lifecycle cleanup, and crash/Jetsam delta. The ordinary
36-step SQL and same-directory restart gate runs after every phase that changes
or exercises native runtime behavior.

The phase command registry is explicit and versioned. If a required phase
adapter is absent, the phase records an infrastructure failure and the command
exits nonzero. It cannot mark the phase passed by omission.

## Failure and Continuation Rules

A case failure creates its failure file immediately. The runner continues with
later cases only when the registry declares them isolated and the App/device is
still in a verified clean state. It stops the current phase after:

- build, signing, installation, or device-connectivity failure;
- stale build identity or malformed evidence;
- failed cleanup, crash, Jetsam, or an App that cannot reach terminal state;
- checkpoint corruption or incompatible resume metadata;
- a missing required adapter.

The final process exits nonzero when any required case is failed, blocked, or
incomplete. Excluded cases do not fail the run only when their exact reviewed
reason is present in the tracked inventory.

On a later invocation, passed cases remain skipped. Failed cases retry by
default. A newly passing attempt retains the earlier failure file for audit and
updates the checkpoint with the successful attempt.

## Manual Lock and Unlock Gate

Apple's available `devicectl` interface cannot perform a real physical-device
lock/unlock transition. During phase 7, the standalone runner prints a terminal
instruction and waits with a bounded timeout for the user to lock and unlock
the phone. It accepts only current-run device-produced protected-data and scene
events. This interaction occurs in the shell running the script and does not
require a Codex window.

Non-interactive mode may mark this gate blocked with the exact platform reason,
but it may not report lock recovery as passed.

## Credentials and Device Selection

Device, bundle, team, and signing inputs are supplied through command-line
options or environment variables. Secret or unique values remain process-local
and are never serialized. The runner may reuse a locally installed valid
profile and keychain identity after validation, but reports only that signing
and installation succeeded or failed.

When more than one eligible physical device exists, the runner requires an
explicit device selection. It never falls back to a simulator.

## Reports

`summary.json` contains exact counts grouped by execution class,
applicability, phase, and result. `summary.md` provides the same bounded result
for humans, including:

- source/build identity;
- device-native, host-driven-device, and host-only counts;
- passed, failed, blocked, excluded, and incomplete counts;
- links to per-case failure files;
- defects or environmental limitations encountered;
- crash/Jetsam and clean-stop outcomes;
- the unresolved accumulated-container shutdown boundary when applicable.

The report never promotes cross-compilation, installation, missing evidence,
or a host-only pass to an iPhone pass.

## Verification

Implementation requires TDD coverage for:

- default, cross-date `--resume`, and `--restart` selection;
- atomic checkpoint recovery from an interrupted `running` case;
- source/config incompatibility rejection;
- passed-case skipping and failed-case retry;
- one sanitized file per failed case and collision-safe names;
- safe/unsafe continuation decisions;
- missing-adapter failure;
- redaction of device/signing metadata;
- aggregate exit status and deterministic JSON/Markdown reports;
- shell argument forwarding and failure propagation.

The standalone runner itself is a host-only orchestration test. Only assertions
produced by the signed App on the physical iPhone count as device-native.
