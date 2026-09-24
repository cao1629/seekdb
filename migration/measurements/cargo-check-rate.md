# cargo check rate on this Mac

PLAN.md section 10, action 14; section 8, item 9. Measured on 2026-09-24 on the developer's Mac
(Apple M4 Pro, 14 cores, 24 GiB, macOS 27.0), Rust 1.98.1, with nothing else heavy running.

## What was checked

cranelift-codegen 0.135.2, copied from the local cargo registry into a scratch directory:
- 122,314 lines of hand-written `.rs` outside tests/, benches/ and examples/;
- 181,451 more lines that its build script generates (ISLE lowering rules) into `target/.../out/`
  and that are checked as part of the crate;
- about 25 dependency crates, all resolved offline.

## Results

| Run | Wall time | CPU (user) | Max RSS |
|---|---|---|---|
| First `cargo check -j14`: all dependencies, the build scripts, and the crate | 9.65 s | 16.27 s | 818 MB |
| `touch src/lib.rs`, incremental on (the dev-profile default) | 0.76 s | 0.64 s | 476 MB |
| `touch src/lib.rs`, `CARGO_INCREMENTAL=0` | 6.53 s | 5.14 s | 796 MB |
| One real edit to src/lib.rs (a new function), incremental on | 1.64 s | 1.37 s | 618 MB |
| A second real edit, `CARGO_INCREMENTAL=0` | 2.77 s | 2.64 s | 792 MB |

The touch-only incremental run is not a real re-check: rustc reuses its cached results when the
content is unchanged. The two `CARGO_INCREMENTAL=0` runs are full re-checks of the crate; why the
first took 6.5 s and the second 2.8 s is not explained, so the range is kept.

## What it means for the plan

- A full check of this crate ran at about 19,000-44,000 hand-written lines per second on one
  thread (122,314 lines in 2.8-6.5 s; 47,000-110,000 lines per second counting the generated
  code). The report's assumption was about 3,000 lines per second per core, with a range of
  500-5,000 (report section 4). This crate checks 6-15 times faster than the report's central
  figure.
- Memory for one crate of this size stays under 0.82 GB, so 24 GiB holds many parallel checks.
- A 100-180K-line crate would, at this rate, check in seconds, far under the 60 s threshold that
  decides whether Step 4 folds into Step 3 and whether Decision 16 reopens (PLAN.md section 8,
  item 12). This is one crate of one style: cranelift's generated code is simple match arms, and
  seekdb's translated code may lean more on generics and trait bounds, which check slower. Step 2a
  measures the real crates.

## Commands

```
cp -R ~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/cranelift-codegen-0.135.2 <scratch>
cd <scratch> && cargo +1.98.1 fetch
/usr/bin/time -l cargo +1.98.1 check --offline -j14
touch src/lib.rs && /usr/bin/time -l cargo +1.98.1 check --offline -j14
touch src/lib.rs && /usr/bin/time -l env CARGO_INCREMENTAL=0 cargo +1.98.1 check --offline -j14
printf '\npub fn judge_probe_edit() -> u32 { 41 + 1 }\n' >> src/lib.rs && /usr/bin/time -l cargo +1.98.1 check --offline -j14
printf '\npub fn judge_probe_edit2() -> u32 { 42 }\n' >> src/lib.rs && /usr/bin/time -l env CARGO_INCREMENTAL=0 cargo +1.98.1 check --offline -j14
```
