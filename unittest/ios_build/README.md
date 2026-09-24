# Generic iOS SQL probe

The iOS probe validates reusable seekdb SQL behavior without depending on an
application schema. It owns three tables in the `ios_probe` database:

- `lifecycle` persists a run counter across processes and is never cleared by
  the probe.
- `feature_matrix` contains generic fixtures for binary keys, unique values,
  unsigned versions, JSON, BLOBs, string arrays, optional JSON arrays, and
  paired source/translated text.
- `feature_event` supports transaction, rollback, and optimistic-update cases.

Each run clears only `feature_matrix` and `feature_event`. The tables have no
foreign keys and must not contain application or user data.

`seekdb_ios_probe_sql` accepts an absolute report path, or `nullptr` for the
link-only executable. With a report path, every completed step is appended as
one JSONL record and flushed immediately. A complete invocation ends with:

```json
{"complete":true,"result":0}
```

Consumers must require that final record and a zero result; partial JSONL is
crash evidence, not a successful run. The UIKit wrapper writes the report to
`Documents/sql-probe-results.jsonl` and separately records the lifecycle
counter in `Documents/probe-status.json`.

Device registry callbacks have enforced per-case deadlines. A watchdog cannot
safely cancel arbitrary C++, so a deadline writes and flushes terminal result
124 evidence before ending the App process with status 124. The host retries
only incomplete JSONL prefixes; a complete invalid run fails immediately.
Diagnostic strings always remain valid UTF-8 JSON: valid sequences are kept and
each malformed input byte is represented as U+FFFD.

The host Python tests validate build-script behavior and product neutrality.
They do not execute the embedded database. Compilation, signing, installation,
SQL execution, persistence across reuse of the same data directory, clean
shutdown, and repeated foreground/background cycles require iOS device
verification. Simulator or link-only success does not replace that boundary.

The `rust` device suite is available only in a test-hook build. Its three real
cert/TLS cases call the same `Result` functions as the host Rust tests. Two
control cases then capture an intentional Rust panic and verify that the next
case continues in the same process. Test builds link the unwind-enabled Rust
archive instead of the production archive; the two archives must never be
linked together. Production release and CMake debug archives retain
`panic=abort` and do not export `nio_device_test_*` symbols.
