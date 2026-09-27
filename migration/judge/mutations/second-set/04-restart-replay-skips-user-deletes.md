# 04-restart-replay-skips-user-deletes

Family 9 (restart after a kill), unit 04-restart. Patch: `04-restart-replay-skips-user-deletes.patch`
(two added lines in one .cpp file).

## What it changes

src/storage/memtable/ob_memtable.cpp, `ObMemtable::replay_row` (lines 894-959), the function that
writes one row of a replayed redo log into a memtable. The patch adds one branch after the
`DF_NOT_EXIST` check:

```
  } else if (blocksstable::ObDmlFlag::DF_DELETE == dml_flag && key_.tablet_id_.is_user_tablet()) {
    ret = OB_NO_NEED_UPDATE;
```

When the replayed row is a DELETE and the memtable belongs to a user tablet (tablet id 200000 or
above: `ObTabletID::is_user_tablet`, src/oblib/common/ob_tablet_id.h:68; `OB_MAX_INNER_TABLE_ID`,
src/oblib/lib/ob_define.h:929), the row is not written. Inserts and updates replay as before, and so
does every row of an inner table (schema, users, privileges), so the server still comes back up.

After a kill and a restart, every committed DELETE on a user table or on one of its indexes is
therefore lost:
- a deleted row comes back (storage writes a plain delete with `DF_DELETE`,
  src/storage/ls/ob_ls_tablet_service.cpp:2506);
- an update that changes an indexed column is written to the index as a DELETE of the old entry plus
  an INSERT of the new one (ob_ls_tablet_service.cpp:3820-3825 and 3897-3905), so after the restart
  the index holds both entries.

`OB_NO_NEED_UPDATE` is the code the replay already uses for a row that the tablet does not need
because its sstables already hold it. `ObTxReplayExecutor::replay_one_row_in_memtable_`
(src/storage/tx/ob_tx_replay_executor.cpp:581-588) turns it into success after calling
`ObTxCtx::check_no_need_replay_checksum`, which switches off the replay checksum of that
transaction: the transaction's list of checksum points starts as `[min_scn]`
(src/storage/tx/ob_trans_define.cpp:285-286), no point lies after the log, so
`force_no_need_replay_checksum_` clears `need_checksum_` (src/storage/tx/ob_tx_ctx.cpp:3552-3610),
and `replay_commit` then passes checksum 0, which skips the check (ob_tx_ctx.cpp:3920-3921;
src/storage/memtable/ob_memtable_context.cpp:555-556).

Why this return code and not a plain skip that returns success: the replayed rows of the
transaction would then not match the checksum in its commit log, `ObMemtableCtx::trans_replay_end`
would return `OB_CHECKSUM_ERROR` (ob_memtable_context.cpp:564-572; its `OB_SAFE_ABORT()` does nothing
without `ENABLE_DEBUG_LOG`, src/oblib/lib/utility/ob_macro_utils.h:766-770, which no build file
defines), and the replay service would count that as a fatal error
(src/logservice/replayservice/ob_replay_status.cpp:1231-1240, ob_log_replay_service.cpp:854-860) and
stop replaying the log stream. The restart would then hang or fail instead of showing the lost rows:
still a failed scenario, but after a 600 s wait and with nothing that points at the cause.

## Why family 9 catches it (the exact output that changes)

Only restart_data writes DELETEs to user tables. Before restart 1 it commits:
- `delete from t_data where id = 10;` and `delete from t_data where name = 'juliet';` (row 12), each
  deleting the table row and its `idx_name` entry;
- `delete from t_data where id = 4;`, which deletes the table row and the index entry
  `('delta', 4)`, followed by the insert of row 4 as `delta-again`;
- `update t_data set name = 'golf-renamed', note = 'renamed' where id = 9;`, which deletes the index
  entry `('golf', 9)`.

Select set 0 is lines 29-139 of the clean recording
/Users/colin/seekdb-dev/mysqltest-runs/00b/restart-smoke/rec-1/restart_data.result. Expected in
select set 1 (read from the source, not run):
- `select * from t_data order by id;` gains two rows, with the values they had when they were
  deleted:
  ```
  |   10 | hotel        |  42.000 | 2012-12-12 | 2012-12-12 12:12:12.121212 |  4.25 | deleted by id              |
  |   12 | juliet       |   5.500 | 2005-05-05 | 2005-05-05 05:05:05.050505 |     8 | deleted by name            |
  ```
- `select /*+ index(t_data idx_name) */ id, name ...` reads the index only (it needs no other
  column), so it gains `4 delta`, `9 golf`, `10 hotel` and `12 juliet`;
  `select /*+ full(t_data) */ id, name ...` gains `10 hotel` and `12 juliet`.
- The count line
  `|       11 |          10 |             9 |           8 |     391.146 | 1970-01-01 | 2038-01-19 03:14:07.999999 |     18.875 |`
  becomes 13, 12, 11, 10, 438.646, the same two dates, 31.125.
- `select id, amount from t_data where amount between 0 and 100 ...` gains `12 5.500` and
  `10 42.000`.
- `select id, name, note from t_data where name like 'd%' ...` may change too: if its plan reads
  `idx_name` and then the table, row 4 is found through both `delta` and `delta-again`. It shows
  twice, or, if storage answers the two identical lookups with one row, the lookup's row-count check
  (src/sql/das/iter/ob_das_local_lookup_iter.cpp:286-291) fails the statement with error 4377 and
  the scenario stops there. Either way restart_data fails.
- `show create table t_data;`, the `note is null` query, the key-list query and `show grants;` do not
  change.

Select set 2 is the same as set 1, because restart 2 replays the same log. The writes after restart
2 delete rows 11 and 5 and rename `bravo`; restart 3 loses those deletes too, so select set 4 has
rows 5 and 11 back and the index entries `2 bravo`, `5 echo` and `11 india`, which select set 3 does
not have.

So the recording ends with

```
-- check: select set 1 is byte-identical to select set 0: no
-- check: select set 2 is byte-identical to select set 0: no
-- check: select set 4 is byte-identical to select set 3: no
-- check: seekdb is still running at the end of the scenario: yes
```

in `restart_data.partial` instead of `restart_data.result`; the manifest lists `restart_data` in
`failed_cases`, the script exits 1, and `compare` against a clean recording reports a recording
problem. restart_parameters (ALTER SYSTEM values go to the SQLite store in store/sstable/meta.db,
not through the redo log) and restart_mid_dml (inserts only) write no DELETE to a user table, so
both should pass and match the clean recordings.

## Evidence that the mutated code runs in the family

From the source; the family's runs are not coverage builds.
- On restart the log stream replays its log from its last checkpoint. Transaction logs go to
  `ObLSTxService::replay` (src/storage/ls/ob_ls_tx_service.cpp:406-423), then
  `ObTxReplayExecutor::execute` (ob_tx_replay_executor.cpp:35), `do_replay_` (:73), `replay_redo_`
  (:306), `replay_redo_in_memtable_` (:435), `replay_one_row_in_memtable_` (:535) and `replay_row_`
  (:631), which calls `ObMemtable::replay_row` at :666. That is the only caller of
  `ObMemtable::replay_row` in src/ (the other `replay_row` belongs to the lock memtable).
- The DELETEs are in the log: a commit returns to the client only after its log is written, and a
  small transaction's redo is written together with its commit (../../harness/restart-scenarios.md,
  "Why some transactions are large").
- They are replayed, not skipped as already flushed: a row is skipped as not needed only when its
  tablet's sstables already cover the log, which needs a freeze, and nothing freezes before any of
  the three kills. The fast freeze leaves a memtable alone until it is 300 s old
  (`FAST_FREEZE_INTERVAL_US`, src/storage/compaction/ob_tablet_scheduler.h:81; the check at
  ob_tablet_scheduler.cpp:100), a whole recording took 25-26 s
  (/Users/colin/seekdb-dev/mysqltest-runs/00b/restart-smoke/timings.txt), and the scenario writes
  well under a megabyte, far below a memory-based freeze. restart-scenarios.md ("What is not covered
  yet") says the same: recovery replays the log into memtables only.

## Why the 272 configured cases most likely do not catch it

- The runner never starts a server on a base dir that already holds data: `prepare_instance`
  destroys the base dir before every start (.github/script/seekdb/mysqltest_for_seekdb.py:521-523),
  for the first instance, a retry and the restart after a failed case alike. Every instance
  bootstraps from an empty directory and has no log to replay.
- AB.profdata (both coverage passes of the 272 cases, bootstrap included): the 48 functions on the
  replay path have 0 hits on all 908 counted lines. They include all of `ObTxReplayExecutor`,
  `ObLSTxService::replay`, `ObMemtable::replay_row` and `mvcc_replay_`,
  `ObIMvccCtx::register_row_replay_cb`, `ObTxCtx::replay_commit`, `check_no_need_replay_checksum`
  and `force_no_need_replay_checksum`, and `ObMemtableCtx::trans_replay_end`; `ObMemtable::replay_row`
  is 0 on every line from 897 to 959. For comparison, the write path `ObMemtable::mvcc_write_` runs
  1.09M times. Output: /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/04-restart/design/coverage/replay-path.txt
  (`llvm-cov show -name-regex` with deps/3rd's clang 17.0.6 tools, on
  /Users/colin/seekdb-dev/cov-076eb309b/build_release/src/observer/seekdb). The files on the path
  (ob_memtable.cpp, ob_tx_replay_executor.cpp, ob_tx_ctx.cpp, ob_mvcc_ctx.cpp, ob_ls_tx_service.cpp,
  ob_memtable_context.cpp, ob_mvcc_engine.cpp, ob_log_replay_service.cpp) are identical at 076eb309b
  and 834bbee1e.

So a seekdb built with this patch runs the 272 cases exactly as the clean one does: the changed line
never runs there.

## Why not a parameter mutation

The task's other example, the replay of a persisted ALTER SYSTEM value, has no code in this tree
that runs only at restart for the parameters restart_parameters uses. ALTER SYSTEM SET writes the
value to `__all_sys_parameter` in the SQLite store and reloads the whole store into memory at once
(src/rootserver/ob_system_admin_util.cpp:129-137); startup reloads it through the same
`ObConfigManager::got_version`, `update_local` and `ObServerConfig::read_config`
(src/observer/ob_server.cpp:1715 and 1746 while `enable_static_effect_` is false, and again at
:1268 with it true, as for ALTER SYSTEM). A change on that path either shows in SHOW PARAMETERS
right after the SET, without a restart, or, if it touches only the two reads made while the flag
is false, is undone for dynamic parameters by the read at :1268. The one branch that runs only at
restart is the one for static (reboot-effective) parameters (src/share/config/ob_server_config.cpp:99:
such a value is read only while the flag is false), and restart_parameters sets two dynamic
parameters, so a mutation there would not be caught. That is a gap in the family, not in this
mutation.

## Mechanics

- One `diff --git` header, one .cpp file, `--numstat` 2 added and 0 removed. Made in a scratch git
  repository holding the pristine file (`git show 834bbee1e:src/storage/memtable/ob_memtable.cpp`),
  with `git diff --src-prefix=a/ --dst-prefix=b/`
  (/Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/04-restart/design/scratch/).
- Everything the new lines use is already used in the same file: `key_.tablet_id_` (:641, :842,
  :887), `OB_NO_NEED_UPDATE` (:1663), `blocksstable::ObDmlFlag::DF_DELETE` (:3046);
  `is_user_tablet()` is a member of `ObTabletID` (ob_tablet_id.h:68). The branch declares no
  variable, so `-Wall -Wextra -Werror` has nothing new to report.
- Checked 2026-09-25: `git -C /Users/colin/seekdb-dev/ref-834bbee1e apply --check` passes, and the
  worktree still shows only ` M cmake/Env.cmake`. Not built, as the task requires; the mutation stage
  builds and runs it.

## How to run it

```
H=/Users/colin/seekdb-dev/migrate-to-rust
W=/Users/colin/seekdb-dev/ref-834bbee1e
A=/Users/colin/seekdb-dev/ref-archive-834bbee1e
C=/Users/colin/seekdb-dev/mysqltest-runs/00b/restart-smoke/rec-1
P=$H/migration/judge/mutations/second-set/04-restart-replay-skips-user-deletes.patch
RUN=<a new directory>
git -C $H diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py
git -C $W apply --check $P && git -C $W apply $P
(cd $W && SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk bash build.sh release --make)
mkdir -p $RUN && cp $W/build_release/src/observer/seekdb $RUN/seekdb
git -C $W apply -R $P
git -C $W diff | cmp - $H/migration/judge/reference-build.patch
python3 -u $H/migration/judge/harness/restart_scenarios.py \
  --seekdb $RUN/seekdb --obclient $A/client/obclient \
  --base-dir $RUN/base --record-dir $RUN/rec --port <port> \
  --init-sql $H/migration/judge/reduced-init/init.sql \
  --init-user-sql $H/migration/judge/reduced-init/init_user.sql > $RUN/run.log 2>&1
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $C --right $RUN/rec --out $RUN/compare.json
diff $C/restart_data.result $RUN/rec/restart_data.partial
```

Caught when the script exits 1 with only `restart_data` in `failed_cases`, and the diff against the
clean recording shows the rows above and the three `no` checks (or the 4377 error described above).
The clean recording rec-1 was made with the same script (the `-- recorder sha256 3dd27279...` line),
the same obclient and the same init files, so only the binary differs, and restart_parameters and
restart_mid_dml should compare identical. A run takes about 25 s, like the clean runs. If restart 1
does not come back instead (`-- restart 1: stop (kill), start, ready failed`, after up to 600 s), the
scenario still fails, but the prediction above about how replay goes on was wrong; record that.
