# Family 1: the differential run of the 272 cases

PLAN.md section 4, family 1: the 272 configured mysqltest cases recorded on the C++ reference and on
the Rust build in a fixed order and slice count, retries off, and compared byte for byte; a second
mode runs each case on a fresh instance. Quarantined cases take part as ../../quarantine.tsv's last
column says. Families 3 and 4 compare the same recordings with their masks (../masks/README.md).

This family adds no script: it is the runner's `run --record-dir` and `compare`. This README holds the
commands, the rule for quarantined cases, and the live check.

## Commands

Every judge run passes `--max-retries 0 --no-ignore-trailing-whitespace`, and before every run
`git -C /Users/colin/seekdb-dev/migrate-to-rust diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py`
must succeed and `/` must have at least 8 GiB free. With `$H` the worktree, `$A` the archive
(/Users/colin/seekdb-dev/ref-archive-834bbee1e) and `$RUN` a new directory:

```
python3 -u $H/.github/script/seekdb/mysqltest_for_seekdb.py run \
  --seekdb $A/seekdb --obclient $A/client/obclient --mysqltest $A/client/mysqltest \
  --base-dir $RUN/instance --work-dir $RUN/work --port <port> \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --record-dir $RUN/rec > $RUN/runner.log 2>&1
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --out $OUT/family1.json
python3 $H/.github/script/seekdb/mysqltest_for_seekdb.py compare \
  --left $RUN_A/rec --right $RUN_B/rec --mask row-order --mask est --out $OUT/family34.json
```

This is the plain mode: one instance for the whole run, under the full tools/deploy/init.sql, as the
checked-in .result files were recorded; the runner starts a new instance only after a failed case. A
recording takes about 25 minutes.

**The fresh-instance mode is family 7's recording.** Family 7 records the 272 cases with a fresh
instance per case (`--fresh-instance-per-case`), plus `--plan-cache-stats` and
`--seekdb-parameter plan_cache_evict_interval=1d` (../plan_cache/README.md, record.sh). `compare`
checks every `.result` of such a pair first, so a family 7 pair is also family 1's fresh-instance
pair, and the second mode needs no run of its own (decided 2026-09-28 under decisions.md row 5c; a
recording takes about two hours). The added options read counters between the cases and stop the
plan cache's eviction timer; a Rust build is recorded the same way for the comparison.

## Quarantined cases

A quarantined case is compared whenever the C++ recordings agree on it and stays out while they do
not. "Agree" is decided over every C++ recording of the same mode that is available, not over one
pair, because one agreeing pair can hide a case that splits the recordings (the dilang case split 2
against 2 over four recordings of family 11). For the seven cases that send `PURGE RECYCLEBIN`, a
recording that stops on the hang (4012 on the statement, `tablet is already deleted` repeating in
seekdb.log) is made again, for either build.

## Live check, 2026-09-28

Two plain recordings of the 272 cases on the archived reference (sha256 db7d9180...), with the archive's
obclient and mysqltest, the full init, `--max-retries 0 --no-ignore-trailing-whitespace`, port 3892,
by the unit that applied the second-set reviews (review/run272.sh, which makes the working-tree, disk
and port checks). Outputs: /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/review/f1-p1 and f1-p2
(runner.log, times.txt, work/, rec/, and logins.txt, the `MySQL LOGIN` lines item 8's check reads).

| Run | When (+08:00) | Result |
|---|---|---|
| f1-p1 | 2026-09-28 23:00 to 23:23 | 272 recorded, none failed, no retry, one instance |
| f1-p2 | 2026-09-29 00:09 to 00:32 | 272 recorded, none failed, no retry, one instance |
| compare f1-p1 f1-p2 | – | exit 1: 270 identical, 2 different, 0 missing, 0 recording problems. The two are quarantined: type_date.type_create_time prints `2 searched` in f1-p1 and not in f1-p2, type_date.type_modify_time the other way round (each selects the row a REPLACE one second earlier wrote, so the second boundary decides) |
| compare with `--mask row-order --mask est` | – | the same exact result; the 50 masked cases identical exactly and masked (../masks/README.md) |

So outside the quarantine list the two recordings are byte-identical, 270 of 270.

**The quarantined cases, decided over every C++ recording available** (review/quarantine_agreement.py,
qa-final.txt: the four one-instance recordings f1-p1, f1-p2, cmp-c1 and cmp-c2, whose `.result` files
the `--compress` option leaves unchanged; family 7's fresh-instance recordings rec2, rec4, rec7 and
rec8; family 11's four multi-connection recordings; family 8's four binary-protocol recordings):

| Case | One instance (4) | Fresh instances (4) | Other modes | In the C++-against-Rust runs |
|---|---|---|---|---|
| type_date.type_create_time | disagree: f1-p2 against the other three | agree | family 8: agree (4) | out of the plain mode while its recordings disagree; compared in the fresh-instance mode |
| type_date.type_modify_time | disagree: f1-p1 against the other three | agree | family 8: agree (4) | the same |
| vector_index.sparse_vector_index_vsag_query | agree | agree | family 8: agree | compared |
| histogram.stats_farm | agree | agree | families 8 and 11: agree | compared |
| subquery.idx_with_const_expr_21_subquery_dilang | agree | agree | family 11: 2 against 2 (seq-1 and par-1 print no rows for the nine selects, ser-1 and par-2 print them) | out: its same-second race (quarantine.tsv) can hit any mode, as family 11 shows |
| the seven `PURGE RECYCLEBIN` cases | agree | agree, except rec2, which hung in create_table_with_vector_index | family 11's par-2 hung in the same case; everything else agrees | compared; a recording that stops on the hang is made again |

**The fresh-instance pair** is family 7's rec7 and rec8 (../plan_cache/README.md): `compare` finds their
`.result` files identical, 272 of 272 (the type_date pair included), with 0 recording problems, and
exits 0 with family 7's list.

## The mutations

Family 1 needs no mutation of its own: any C++ change that alters what a configured case prints is its
kind, and the first set's 14 injected mutations are exactly that (../../mutations/README.md). Their
272 runs of 2026-09-24/25 compared each case with the checked-in .result; family 1 compares with a C++
recording instead. So review/mutation_replay.py takes, for every case a mutated run failed outside
../../quarantine.tsv (the list as it now stands, with the seven purge cases), the mutated output
(`mysqltest_log/<case>.reject`) and compares it with the same case in f1-p1, exactly and with the
masks. A case whose mutated run stopped without a .reject (mysqltest ended on an error it did not
expect) counts as caught too: a recording of it would hold a failed case, which `compare` reports.

| Mutation (first set) | Cases outside the quarantine list that differ from f1-p1 | of them, still different under both masks |
|---|---|---|
| 01 cost model: range scan cost | 32 | 9 |
| 02 cast: string to double error dropped | 5 (one stopped: geometry.geometry_cast_mysql) | 5 |
| 03 SORT: ties in reverse storage order | 8 | 8 |
| 04 ObNumber CEIL carry | 1 | 1 |
| 05 DEBUG_SYNC point renamed | 1 (stopped: fork_table.fork_table_lock) | 1 |
| 06 SUBSTRING_INDEX with a negative count | 2 | 2 |
| 07 `NULL <=> NULL` | 6 | 6 |
| 08 GROUP_CONCAT separator | 3 | 3 |
| 09 hash full outer join drops left rows | 2 | 2 |
| 10 DML IGNORE null not zeroed | 1 (stopped: update.update_ignore) | 1 |
| 11 RR snapshot per statement | 3 (two stopped: trx.repeatable_read_transaction, trx.serializable_transaction) | 3 |
| 12 alias ambiguity dropped | 3 (all stopped: column_alias, select_basic, groupby.group_by_basic) | 3 |
| 13 DATE_ADD month-end clamp | 1 | 1 |
| 14 FORK TABLE privilege | 1 (stopped: fork_table.fork_table_privilege) | 1 |

Every one of the 14 is caught by family 1, and by families 1, 3 and 4 together with both masks on.
The same replay against f1-p2 gives the same numbers (review/replay-f1-p1.txt, replay-f1-p2.txt and
their .json). The second set's patches change nothing any configured case prints (each is caught by
its own family instead, ../../mutations/second-set/README.md): the replay finds no case of theirs
outside the quarantine list that differs.
