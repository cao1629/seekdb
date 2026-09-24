# iOS Test Inventory Schema

## Purpose

This schema records how each seekdb test maps to the layered iOS validation
strategy. It prevents host-only results from being reported as physical-device
evidence and makes exclusions explicit and reviewable.

The inventory should be stored as UTF-8 JSON Lines. Each line is one test or
one independently runnable suite. Generated inventory and local device logs may
remain ignored, but important outcomes must be summarized in the tracked iOS
build and change-log documents.

## Required Fields

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable, product-neutral identifier. |
| `source_path` | string | Repository-relative test or suite path. |
| `owning_module` | string | seekdb module responsible for the behavior. |
| `framework` | string | Test framework or runner. |
| `execution_class` | enum | `device-native`, `host-driven-device`, or `host-only`. |
| `device_equivalent` | string or null | Stable ID of the device test providing equivalent coverage. |
| `requirements` | string array | Runtime, fixture, entitlement, memory, or tooling requirements. |
| `timeout_seconds` | integer | Positive per-test or per-suite timeout. |
| `memory_class` | enum | `small`, `medium`, `large`, or `unbounded`. |
| `applicability` | enum | `required`, `conditional`, or `excluded`. |
| `exclusion_reason` | string or null | Concrete reason when applicability is `excluded`. |
| `latest_result` | enum | `pass`, `fail`, `blocked`, or `not-run`. |
| `evidence_path` | string or null | Repository-relative path to current evidence or summary. |

## Classification Rules

- `device-native` means the test executable and asserted behavior run on the
  physical iPhone.
- `host-driven-device` means a Mac runner installs, launches, controls, or
  collects evidence from code executing on the physical iPhone.
- `host-only` means the asserted behavior runs on macOS. Cross-compilation alone
  does not change this classification.
- `device_equivalent` may be set only when the replacement asserts the same
  externally observable behavior. Similar code paths are not sufficient.
- Every excluded row must have a non-empty `exclusion_reason`; exclusions must
  never be silently omitted from coverage totals.

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
{"id":"startup.cleanup.during_init","source_path":"unittest/ios_build/run_device_cleanup_test.py","owning_module":"observer/ios","framework":"python+devicectl","execution_class":"host-driven-device","device_equivalent":null,"requirements":["physical iPhone","test-hook build","writable app container"],"timeout_seconds":180,"memory_class":"large","applicability":"required","exclusion_reason":null,"latest_result":"not-run","evidence_path":null}
```
