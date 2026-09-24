# Performance baseline of the C++ reference (00b item 9)

Measured on 2026-09-24 with judge/harness/perf_run.sh, under judge/performance-protocol.md: the
archived reference (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb) natively on the Mac with
`cpu_count=8` and `memory_limit=8G`, sysbench 1.0.20 in the Docker container `sb`, 16 tables x
100,000 rows, uniform, 15 s warm-up then 60 s measured, 5 rounds. `oltp_read_write` ran with
`--db-ps-mode=disable` (see the protocol). The Mac was shared with the developer's work during the
runs; every round's load is in results.tsv. No round was discarded: this is a one-sided baseline, and
the gate compares C++ and Rust interleaved (protocol), which evens out background load.

Outputs: /Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e/ (oltp_point_select; its
oltp_read_write rows failed with error 5930 and are superseded) and
/Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e-rw/ (oltp_read_write).

## Results (median of 5 rounds, with min-max)

| Workload | Threads | QPS | Avg latency (ms) | P95 latency (ms) | Server CPU peak |
|---|---|---|---|---|---|
| oltp_point_select | 1 | 7,151 (7,056-7,301) | 0.14 | 0.18 | ~0.3 core |
| oltp_point_select | 16 | 43,685 (42,812-44,542) | 0.37 | 0.50 | ~1.8 cores |
| oltp_point_select | 64 | 58,145 (57,906-58,394) | 1.10 | 1.44 | ~2.0 cores |
| oltp_read_write | 1 | 6,686 (6,618-8,178) | 2.99 | 3.68 | ~0.5 core |
| oltp_read_write | 16 | 40,087 (38,691-41,811) | 7.98 | 10.27 | ~2.9 cores |
| oltp_read_write | 64 | 49,857 (47,291-53,406) | 25.67 | 32.53 | ~3.2-6.1 cores |

## Where the bottleneck is

- `oltp_point_select` stops scaling from 16 to 64 threads (43.7K to 58.1K) while the server uses
  about 2 of its 8 cores: the limit is the client or the Docker network hop, not the server. These
  figures compare two builds; they are not the server's ceiling.
- `oltp_read_write` at 64 threads reaches 3-6 cores of server CPU, closer to the server's own limit;
  its rounds spread about ±6%.

## Lifecycle (recorded, not gated, Decision 1)

| What | Time |
|---|---|
| Cold start to `select 1`, empty base dir | 2,087 ms and 3,096 ms (two runs) |
| Restart after `kill -9`, same base dir, after the workload | 3,172 ms and 3,166 ms |
| Binary size | 192,895,048 bytes |
