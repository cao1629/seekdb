# 06-wire: OK packets lose NO_BACKSLASH_ESCAPES

Item 8, golden bytes on the MySQL wire (PLAN.md section 4, item 8; families/wire/), unit 06-wire.
Patch: 06-wire-ok-drops-no-backslash-escapes.patch, one .cpp file, 1 line changed.
`git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes (2026-09-25, and again on
2026-09-27 with only reference-build.patch's cmake/Env.cmake change in that worktree). Not built.

## What it changes

src/observer/mysql/obmp_packet_sender.cpp, `ObMPPacketSender::send_ok_packet`, line 811 at 834bbee1e.
Before the change, the status word of every OK packet gets bit 0x0200 (NO_BACKSLASH_ESCAPES) from the
session's `sql_mode`:

```
flags.status_flags_.OB_SERVER_STATUS_NO_BACKSLASH_ESCAPES = is_no_backslash_escapes;
```

where `is_no_backslash_escapes` is `SMO_NO_BACKSLASH_ESCAPES & sql_mode` (the macro
`IS_NO_BACKSLASH_ESCAPES`, src/oblib/common/sql_mode/ob_sql_mode.h:128-131; the bit is `1ULL << 20`,
:45). After the change the bit is always 0. The other seven flags set beside it (IN_TRANS,
AUTOCOMMIT, MORE_RESULTS_EXISTS, CURSOR_EXISTS, LAST_ROW_SENT, PS_OUT_PARAMS, RESERVED on connect)
are untouched, and so is the rest of the packet. This is the only line in src/ that sets the flag
(`grep -rn OB_SERVER_STATUS_NO_BACKSLASH_ESCAPES src`); the EOF builders (ob_query_driver.cpp:111,
ob_sync_plan_driver.cpp:130, ob_sync_cmd_driver.cpp:80, obmp_stmt_execute.cpp:388,
obmp_stmt_fetch.cpp:340) never set it, and rust/sql-nio only writes the status word it is given
(response.rs:64-71). The session's `sql_mode` itself does not change, so the server still treats
backslashes as the mode says; only what the client is told changes. A plausible porting mistake: the
status word is rebuilt from the session flag by flag, and one of eight flags is left at its default.

## Why the wire family catches it (the exact output that changes)

wire_ok_err_eof runs three statements under `SET sql_mode = 'NO_BACKSLASH_ESCAPES'` over the text
protocol with CLIENT_PROTOCOL_41, so each OK packet carries the status word, and wire_scenarios.py
records every packet byte for byte. In the reference recordings
/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/06-wire/run09/rec and run10/rec (identical;
recorder sha256 26adc2354ad2d021c3e1790d015e53f82ce8b931cb37f220ef848d5b6d44795b), wire_ok_err_eof.result:

| Request | Line | Recorded now | Under the mutation |
|---|---|---|---|
| 36, `set sql_mode = 'NO_BACKSLASH_ESCAPES'` | 274-275 | `S 1 45 0000002242000000...`, `status 0x4222 AUTOCOMMIT\|NO_INDEX_USED\|NO_BACKSLASH_ESCAPES\|SESSION_STATE_CHANGED` | `S 1 45 0000002240000000...`, `status 0x4022 AUTOCOMMIT\|NO_INDEX_USED\|SESSION_STATE_CHANGED` |
| 38, `insert into t_ok values (40, 'a\b', 40)` | 290-291 | `S 1 7 00010022020000`, `status 0x0222 AUTOCOMMIT\|NO_INDEX_USED\|NO_BACKSLASH_ESCAPES` | `S 1 7 00010022000000`, `status 0x0022 AUTOCOMMIT\|NO_INDEX_USED` |
| 39, `do 1` | 294-295 | `S 1 7 00010022020000`, same summary | `S 1 7 00010022000000`, same as above |

These are the only three packets with bit 0x0200 in the ten recordings (a scan of every `status`
field of run09). Request 37's `select 'a\b'` runs under the mode too, but its two EOF packets carry
`0x0002` already, as the EOF builders above never set the flag, so they do not change. The session
state inside request 36's OK (`sql_mode` = `NO_BACKSLASH_ESCAPES`) comes from the session tracker,
not from the status word, and stays. No check in the scenario reads the flag, so the mutated run
still passes its own checks; `compare` against the reference recording reports wire_ok_err_eof
`different` and the other nine scenarios identical, and exits 1.

That the code runs in the family is shown by the recording itself: the three OKs carry 0x0200 only
because line 811 copied it from the session (it is the only writer of the flag, above), and the OK
of `set sql_mode = default` right after them (request 40) carries 0x4022 again.

## Why the 272 configured cases do not catch it

- **The line runs, but always writes 0.** In the coverage profile of the 272 cases
  (/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata, binary
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb; the file is identical at
  076eb309b and 834bbee1e, `git diff --quiet 076eb309b 834bbee1e -- src/observer/mysql/obmp_packet_sender.cpp`
  exits 0), `llvm-cov show -name-regex='ObMPPacketSender14send_ok_packet'` gives 89.3k executions of
  line 811, summed over both passes. In every one of them `is_no_backslash_escapes` is false, so the
  mutated line writes the value the original writes:
  - no configured case, include file or init file sets the mode:
    `grep -rli backslash tools/deploy/mysql_test tools/deploy/init.sql tools/deploy/init_user.sql`
    finds only mysql_test/psmalltest.py, a list of test names, and its three names that contain
    `no_backslash` are neither files in this tree nor entries of mysqltest_config.yaml; no case sets
    `sql_mode` to a number, and the only variable a case assigns to it is its own saved value
    (`set @tmp_sql_mode = @@sql_mode` ... `set @@sql_mode = @tmp_sql_mode`);
  - no combined mode includes the bit (`COMBINE_SMO_*`, ob_sql_mode.h:95-110), nor does the default
    (`SMO_DEFAULT`, :112), and the server sets the bit on a session only to restore a value the user
    had set (ob_dynamic_sampling.cpp:1018-1020);
  - the same profile shows the bit clear at three other places that read a session's mode and test
    it for the bit (checked 2026-09-27 with `llvm-cov show` on each file; the three files are
    identical at 076eb309b and 834bbee1e): in the LIKE resolver the test at src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:3007
    ran 266 times and its body (:3008-3009) 0 times; in dynamic sampling the test at
    src/sql/optimizer/ob_dynamic_sampling.cpp:1018 ran 1.39k times and :1019 0 times; in LIKE's
    evaluation, src/sql/engine/expr/ob_expr_like.cpp:817 (`if (!is_no_backslash_escapes)`) ran 2 times
    and its body 2 times.
  So every OK packet of the 272 cases has the same bytes with and without the mutation, over the
  text protocol and under `--ps-protocol` alike.
- **mysqltest would not show it anyway.** Its output has no place for the status word:
  `--enable_info` prints the affected rows and the info string of an OK packet, and
  `--enable_metadata` (only in generated_column) prints column definitions.

A family 1 run of the mutated build, compared with the .result files or with a C++ recording, is
expected to pass.

## How the mutation stage runs it

As in ../README.md, "How to run them": apply the patch in /Users/colin/seekdb-dev/ref-834bbee1e,
rebuild, copy the binary aside as `$MUT`, then record the family with it exactly as the reference was
recorded (one instance at a time, a free judge port, a new record directory):

```
cd /Users/colin/seekdb-dev/migrate-to-rust
git diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py
python3 migration/judge/families/wire/wire_scenarios.py \
  --seekdb $MUT \
  --obclient /Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient \
  --base-dir $OUT/base --record-dir $OUT/rec --port $PORT \
  --init-sql migration/judge/reduced-init/init.sql \
  --init-user-sql migration/judge/reduced-init/init_user.sql \
  --gzip /usr/bin/gzip --zstd /opt/homebrew/bin/zstd
python3 .github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/06-wire/run09/rec \
  --right $OUT/rec
```

- wire_scenarios.py and wire_client.py must be the recorded versions (sha256 26adc235... and
  0cd2dd92e867c216dd1a1ef6258f05a811287f1d84c4eea28b9e7c7ed49c432a): every recording starts with
  both digests, so any other version makes all ten scenarios differ.
- `--file-dir` stays at its default, /tmp/seekdb-judge-wire-files, which must not exist when the run
  starts: its path is part of the recorded SQL of the two file scenarios.
- The run takes about 70 s (a fresh instance per scenario, each waiting about 4 s for the server's
  system packages). The script exits 0; `compare` notes the different `seekdb_sha256` and fails with
  wire_ok_err_eof `different`, the diff showing the three packets above. Revert the patch afterwards.

## Caught by (filled after the run)

Not run yet.
