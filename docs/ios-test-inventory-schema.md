# iOS Test Inventory Schema

## Purpose

This schema records how each seekdb test maps to the layered iOS validation
strategy. It prevents host-only results from being reported as physical-device
evidence and makes exclusions explicit and reviewable.

The generated inventory is stored as UTF-8 JSON Lines under
`build_ios_arm64/generated/ios-test-inventory.jsonl`. Each line is one test or
one independently runnable suite. The tracked
`unittest/ios_build/ios-test-classification.json` manifest supplies explicit
per-corpus classification templates, a reviewed decision for every active
mysqltest and orphan GTest ID, a materialized list of all 500 legacy obtest
IDs, and narrowly scoped per-ID overrides. Exact-set validation rejects new,
missing, or stale reviewed IDs. Discovery
uses `git ls-files -z`, so untracked local files cannot silently change the
reported corpus. Strict generation also rejects relevant staged or unstaged
tracked changes and untracked active mysqltest files, binding `source_commit`
to the actual HEAD inputs. Generated inventory and local device logs remain ignored, but
important outcomes must be summarized in the tracked iOS build and change-log
documents.

## Required Fields

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable, product-neutral identifier. |
| `source_path` | string | Repository-relative test or suite path. |
| `corpus` | string | Discovery corpus used to select the tracked classification. |
| `case_name` | string | Framework-level test or case name. |
| `ci_selected` | boolean | Whether the current psmall mysqltest runner selects this case. |
| `owning_module` | string | seekdb module responsible for the behavior. |
| `framework` | string | Test framework or runner. |
| `execution_class` | enum | `device-native`, `host-driven-device`, or `host-only`. |
| `device_equivalent` | string or null | Stable ID of the device test providing equivalent coverage. |
| `requirements` | string array | Runtime, fixture, entitlement, memory, or tooling requirements. |
| `timeout_seconds` | integer | Positive per-test or per-suite timeout. |
| `memory_class` | enum | `small`, `medium`, `large`, or `unbounded`. |
| `applicability` | enum | `required`, `blocked`, or `excluded`. |
| `exclusion_reason` | string or null | Concrete reason when applicability is `excluded`. |
| `blocked_reason` | string or null | Concrete missing capability when applicability is `blocked`. |
| `latest_result` | enum | `pass`, `fail`, `blocked`, or `not-run`. |
| `evidence_path` | string or null | Repository-relative path to current evidence or summary. |
| `classification_source` | string | Manifest classification or exact override that classified the row. |
| `source_commit` | string | Git commit used for the generated source inventory. |
| `corpus_digest` | string | SHA-256 of canonical classified rows before revision metadata. |

## Classification Rules

- `device-native` means the test executable and asserted behavior run on the
  physical iPhone.
- `host-driven-device` means a Mac runner installs, launches, controls, or
  collects evidence from code executing on the physical iPhone.
- `host-only` means the asserted behavior runs on macOS. Cross-compilation alone
  does not change this classification.
- `blocked` means a required runner, target, or platform capability is absent;
  it is not a passing or excluded result and requires `blocked_reason`.
- `device_equivalent` may be set only when the replacement asserts the same
  externally observable behavior. Similar code paths are not sufficient.
- Every excluded row must have a non-empty `exclusion_reason`; exclusions must
  never be silently omitted from coverage totals.
- Device-equivalent IDs must resolve within the same inventory and may not form
  direct or indirect cycles.
- Required string fields and requirement entries must be nonempty; booleans,
  timeouts, enums, nullable fields, relative evidence paths, Git object IDs,
  and SHA-256 digests are validated by type and format.
- `excluded` rows carry only `exclusion_reason`, `blocked` rows carry only
  `blocked_reason`, and `required` rows carry neither.

## Discovered Corpora

- Active mysqltest cases include top-level `tools/deploy/mysql_test/t/*.test`
  and suite `test_suite/*/t/*.test` files. CI selection imports and calls
  `.github/script/seekdb/mysqltest_for_seekdb.py::discover_cases`, preserving
  the current psmall YAML semantics.
- Legacy obtest inventory is exactly `tools/obtest/t/**/*.test`; helper,
  include, result, backup, and client `.test` files are not runnable cases in
  that corpus.
- Rust tests are tracked `#[test]` functions. Orphan C++ tests are tracked
  `TEST`/`TEST_F` registrations even when no normal C++ target exists.
- The normal C++ target count is derived by scanning tracked non-iOS
  `unittest/**/CMakeLists.txt` target definitions; it is not accepted as a
  literal manifest assertion.
- iOS probes are the tracked `*_probe.c`, `*_probe.cpp`, and `sql_probe.cpp`
  sources. Host Python tests are tracked `unittest/ios_build/test_*.py` files.

## Commands

The official focused validation command is:

```bash
python3 unittest/ios_build/test_inventory.py -v
```

Strict inventory generation requires all relevant inputs to match HEAD:

```bash
python3 unittest/ios_build/generate_test_inventory.py
```

`--allow-dirty` exists only for pre-commit development and does not provide
HEAD-bound evidence.

## Evidence Extension

Device evidence should additionally record a unique run ID, test name, source
commit, application build identity, hook mode, start and end timestamps,
duration, non-unique device model and OS version, Xcode and SDK versions,
deployment target, dependency versions, process exit status, assertion result,
and evidence checksums. Failure-injection evidence must include the injection
point, primary engine error, separate cleanup error, decoded cleanup actions,
working-directory restoration, and the crash or jetsam report delta.

Never record a signing identity, Apple team identifier, account, device unique
identifier, private key, provisioning secret, or other credential.

## Example

```json
{"id":"startup.cleanup.during_init","source_path":"unittest/ios_build/run_device_cleanup_test.py","corpus":"device-runner","case_name":"during_init","ci_selected":false,"owning_module":"observer/ios","framework":"python+devicectl","execution_class":"host-driven-device","device_equivalent":null,"requirements":["physical iPhone","test-hook build","writable app container"],"timeout_seconds":180,"memory_class":"large","applicability":"required","exclusion_reason":null,"blocked_reason":null,"latest_result":"not-run","evidence_path":null,"classification_source":"override:startup.cleanup.during_init","source_commit":"<git-commit>","corpus_digest":"<sha256>"}
```
