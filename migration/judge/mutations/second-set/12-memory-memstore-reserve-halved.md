# 12-memory: the memstore's replay reserve is halved for user writes

Family 12, memory budgets (PLAN.md section 4, family 12). Patch:
12-memory-memstore-reserve-halved.patch, one .cpp file, one line changed. Written on 2026-09-28 by the
unit that applied the second-set reviews (the memory family had no mutation). `git -C
/Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes against the worktree with only
reference-build.patch applied.

## What it changes

src/storage/tx_storage/ob_memstore_freezer.cpp, `ObMemstoreFreezer::check_memstore_full_`, line 1075
at 834bbee1e:

```
-      const int64_t reserved_memstore = from_user ? REPLAY_RESERVE_MEMSTORE_BYTES : 0;
+      const int64_t reserved_memstore = from_user ? REPLAY_RESERVE_MEMSTORE_BYTES / 2 : 0;
```

`REPLAY_RESERVE_MEMSTORE_BYTES` is 100 MB (ob_memstore_freezer.h:126). The memstore counts as full
for a user write when its quota used exceeds `memstore_limit - reserve` (:1083), and
`ObAccessService::check_write_allowed_` then refuses the write with -4030
(src/storage/tx_storage/ob_access_service.cpp:655-657). With the patch, user writes keep going until
the memstore is 50 MB from its limit instead of 100 MB, so the memory kept for log replay shrinks by
half. Replay itself (`from_user` false) is unchanged, and so is every write that stays below the new
line. A translation that takes the reserve as a plain number, or reads the constant in the wrong
unit, makes this kind of change.

## Why family 12 catches it (the exact output that changes)

memstore_fill reads the memstore used (22,877,976 bytes on the reference in every run), sets
`memstore_memory_limit` to that value rounded up to MB plus 100 MB plus 16 MB (138M), and writes
chunks of 1,000 rows of 1,000 bytes until one is refused. On the reference 13 chunks are accepted
and the quota read right after the refusal is 41,596,320 bytes, 1.7 MB above the line
(families/memory/README.md, "Live check, 2026-09-28"). With the reserve at 50 MB the line moves 50 MB
up, so about 37 more chunks are accepted before the refusal. In the recording:
- `-- accepted chunks: 13` becomes a larger number;
- the `memstore_used=... memstore_limit=144703488` line read after the refusal shows a quota about
  50 MB larger;
- the check `memstore_used is above memstore_limit minus the 100 MB reserve, by at most 8 MB` reads
  `no`, so memstore_fill ends as a `.partial` and the script exits 1.

memstore_below_reserve does not change: with a 64 MB limit the new line is 14 MB, and the inner
tables alone hold about 24 MB (24,957,792 bytes in every run), so every user write is still refused.

## Why the 272 configured cases do not catch it

- The line runs in the 272 cases: in the coverage profile of both passes
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb), line 1075 and the
  comparison at 1083 run 91.2k times each. ob_memstore_freezer.cpp and ob_access_service.cpp are
  identical at 076eb309b and 834bbee1e.
- The refusal never happens there: `ret = OB_SERVER_RUNTIME_OUT_OF_MEM` at ob_access_service.cpp:657
  has 0 hits, because the judge's instances run with the default memory budget, whose memstore limit
  (50% of it) is gigabytes above anything the cases write. Halving the reserve only moves the line
  closer to the limit, so a user write that was never refused is still never refused.
- No configured case sets `memstore_memory_limit`, `memstore_limit_percentage` or the throttling
  triggers (`grep -rl` over tools/deploy/mysql_test finds only the parameter listing in
  inner_table/r/mysql/all_virtual_sys_parameter_stat.result).

## How it runs

Apply the patch in /Users/colin/seekdb-dev/ref-834bbee1e, `make seekdb` in build_release with the
archive's SDK, copy the binary aside, revert and check that the worktree equals reference-build.patch
(review/mutations/build.sh), then run the family with the mutated binary and compare it with a C++
recording made by the same script:

```
H=/Users/colin/seekdb-dev/migrate-to-rust
python3 -u $H/migration/judge/families/memory/memory_scenarios.py \
  --seekdb $MUTATED --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --base-dir $RUN/base --record-dir $RUN/rec --port <port> \
  --init-sql $H/migration/judge/reduced-init/init.sql \
  --init-user-sql $H/migration/judge/reduced-init/init_user.sql
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left <C++ recording>/rec --right $RUN/rec --mask mem-hold
```

Caught when memstore_fill is a `.partial` whose distance check reads `no`, and `compare` exits 1 with
memstore_fill missing on the right (a recording problem) or different.

## Caught by (filled after the run)

Caught by family 12 on 2026-09-29 (review/mutations/build.sh built it: 11 s, seekdb sha256
c9c7f17424295660dd7fc830a79526a4e5e37e84d16d4a18a8b0a069d0bb15d7, the worktree back to
reference-build.patch afterwards; the binary was deleted after its run). memory_scenarios.py on the
mutated build (review/mut12-mem, port 3891, 78 s) exits 1 with memstore_fill as a `.partial`: 47
chunks accepted instead of 13, and after the refusal `memstore_used=93591720` instead of 41,596,320
with the same `memstore_limit=144703488`, 51.3 MB above the old line (1.3 MB above the new one) instead of 1.7 MB, so the
distance check reads `no`. Against mem-10, `compare --mask mem-hold` exits 1: memstore_fill missing on
the right (a recording problem), work_area_spill, hash_join_depth, memstore_below_reserve and
vector_limit identical, and query_memory_limit identical once masked (its mem_hold was 11085702, one
of the reference's two values). memstore_below_reserve did not change, as predicted.

The 272 configured cases were not run on this build: the refusal line (ob_access_service.cpp:657) has
0 hits in them, so the mutation cannot change what they print (above).
