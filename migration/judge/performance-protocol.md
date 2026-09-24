# How performance is measured on a shared Mac

PLAN.md section 4, item 9 (baselines) and family 14 (the performance gate). The Mac is shared: the
developer runs other work on it, and so do the port's own builds and agents. The gate is a ratio of
the Rust build against the C++ reference, so what matters is that both are measured the same way,
not that either reaches the machine's ceiling.

## Setup

- **The server** runs natively on the Mac: the archived C++ reference
  (/Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb) or the Rust build under test.
- **The client** is sysbench 1.0.20 in the Docker container `sb` (ubuntu:22.04, with
  `host.docker.internal` mapped to the host). Only the client runs in Linux; no Linux build of
  seekdb is involved. sysbench cannot run natively here: Homebrew's build links MariaDB
  Connector/C, which forces TLS, and seekdb has no SSL (seekdb-dev skill, "sysbench benchmark").
- **Fixed server resources:** every performance run starts the server with
  `--parameter cpu_count=8 --parameter memory_limit=8G` (plus the other parameters in the
  seekdb-dev skill's minimal start), instead of letting it size itself from the whole machine. That
  leaves 6 cores for the client, the Docker VM and the developer's work, and keeps the server's own
  thread pools the same size in every run.
- **Fixed workload:** `oltp_point_select` and `oltp_read_write`, 16 tables x 100,000 rows,
  `--rand-type=uniform`, at 1, 16 and 64 threads; a 15 s warm-up that is discarded, then a 60 s
  measured run with `--report-interval=10`.
- **`oltp_read_write` runs with `--db-ps-mode=disable`** (plain text statements). With server-side
  prepared statements, each connection prepares a set of statements for each of the 16 tables and
  seekdb refuses the prepare with error 5930 "maximum open cursors exceeded" (first baseline run,
  2026-09-24). `oltp_point_select` prepares one statement per table and stays under the limit. That
  limit and its error are server behavior the Rust build must reproduce; they are judged by the
  mysqltest cases and the `--ps-protocol` replay, not by the performance runs.
- **The script** is judge/harness/perf_run.sh: `perf_run.sh BINARY OUTDIR [PORT] [ROUNDS] [WORKLOADS]`;
  results.tsv has one row per round, workload and thread count, with a status column (a run with a
  sysbench FATAL is marked failed and never enters a median).

## Keeping the two sides comparable

- **Interleave:** C++, Rust, C++, Rust, ... at least 5 rounds per side per workload and thread count.
  Background load changes then hit both sides alike.
- **Report the median and the spread** (min-max and the interquartile range) of QPS, average latency
  and P95 latency per side; the gate compares medians. `max` latency is recorded but never gated.
- **Record the machine's load in every round:** `sysctl -n vm.loadavg` before and after, the top 10
  processes by CPU from `ps -axo pcpu,rss,comm -r | head`, and `memory_pressure` (macOS) once per round.
- **Discard a round** whose 1-minute load average exceeds 4 above the round's own load (the server
  plus the client), or whose memory pressure is not "normal", and run it again. Log every discarded
  round with its reason.
- **No port work during a performance window:** no builds, no judge runs and no agent fan-out while
  the rounds run. The developer is told when a window starts; pausing their own heavy work is
  welcome but not required.

## Knowing what was measured

- For each workload, check where the bottleneck is: if QPS stops rising from 16 to 64 threads while
  the server's CPU stays well under 8 cores (`ps -o pcpu -p <pid>` sampled during the run), the limit
  is the client or the Docker network hop, not the server. Record that finding with the numbers.
- The Docker hop adds latency and lowers absolute QPS, so these figures are good for the ratio
  between the two builds and for before/after comparisons, not as the machine's ceiling.
- Cold start, restart time and binary size are recorded alongside but not gated (Decision 1).
