# Storage behavior, the data version, bootstrap and the generators

This design section turns ARCHITECTURE.md §6 (storage behavior the judge sees), §12 (data version, bootstrap and catalog access), §15 items 1 and 6 (the Step 2a start and storage), §1.4 (the generators) and §16 (PLAN §8 item 21) into rules for the authors of the core crates involved (section 1), the implementers whose units call them, and the reviewers. It settles three bullets of PLAN §6 "Step 1": whether the storage estimator's inputs are kept identical (Decision 6), the storage and PX scan order, and the data-version bump with its three fixes (Decision 11); it also covers bootstrap, PLAN §3's "What is dropped or regenerated", the logical export and import (4.6), and, in section 10, the storage and transaction core's rules and what crash recovery must reproduce.

- Authority, highest first: decisions.md, PLAN.md, the feasibility report, ARCHITECTURE.md, then this file. Section 9 lists the objections this file raised against ARCHITECTURE.md, each with its settlement (RESOLUTIONS.md s6-N); both files follow the settlements.
- "Must" marks a rule. Code facts are at 834bbee1e, with paths from the repository root; each count names the command or the research report (R06 = research/06-storage.md, R11 = research/11-bootstrap-data.md) behind it. "Assumption" marks a claim without evidence.
- "Default, for the developer to confirm" means the same as in ARCHITECTURE.md; section 8 lists the ones this section relies on and the questions it adds.
- Rust examples use the C++ names, as ARCHITECTURE §14 rule 3 requires, and carry no comments, as rule 8 requires.

## 1. Where the code lives

| Piece | C++ source | Crate (ARCHITECTURE §1.1) | Built in |
|---|---|---|---|
| `ObIStorageEstimator`, `ObIOptimizerStorageService`, `ObPartitionEst`, `ObEstRowCountRecord` | src/data_plane/api/data_plane/ob_i_storage_estimator.h:41-60, ob_i_optimizer_storage_service.h:35-48; src/storage/access/ob_index_sstable_estimator.h:31-44; src/share/ob_est_row_count_record.h:27-36 | storage-api (`ObPartitionEst` is a core item written there; the map sends the rest of its header to storage-tablet, and the design's placement overrides the map for core units, RULEBOOK section 4) | core API |
| Memtable, `ObQueryEngine`, `ObKeyBtree` | src/storage/memtable | storage-tx | core; Step 2a |
| `ObTableEstimator`, `ObLSTabletService` estimate methods, the scan merge, `ObTabletAutoincrementService` | src/storage/access, src/storage/ls, src/storage/ob_tablet_autoincrement_service.cpp | storage-tablet | core; Step 2a for the memtable paths |
| `ObIndexBlockScanEstimator`'s index walk, the sstable writer | src/storage/access/ob_index_sstable_estimator.cpp, src/storage/blocksstable | storage-sstable (walk over its reader) | core build |
| Freeze triggers `ObMemstoreFreezer`, `ObCheckPointService` | src/storage/tx_storage | storage-tablet | core build |
| Fast freeze; daily major freeze launcher | src/storage/compaction; src/rootserver/freeze | storage-engine; rootserver | leaf |
| Data-version gate, `DATA_CURRENT_VERSION` | src/oblib/common/ob_data_version_mgr.{h,cpp}, ob_record_header.{h,cpp}, ob_version_def.h:52-53 | ob-runtime, module `data_version` (map rows move the three files there) | core |
| meta.db (SQLite) | src/share/storage/ob_sqlite_connection.{h,cpp} and ob_sqlite_connection_pool.{h,cpp} (a map row moves them), src/share/config | ob-runtime, which may use rusqlite; the table storages of src/share/storage stay in ob-share and use the pool | core build |
| Catalog access through inner SQL, as the C++ does (5.2 rule 5; ARCHITECTURE default 25); `ObDMLSqlSplicer` | src/share/ob_dml_sql_splicer.{h,cpp}, src/observer/schema/ob_schema_service_sql_impl.cpp, the `*_sql_service` writers | where the map puts them; Step 2a's in-memory schema service lives in observer and is thrown away | Step 3 leaves, first batches (the core-build exit runs DDL) |
| Bootstrap sequence | src/rootserver/ob_bootstrap.cpp, ob_runtime_ddl_service.cpp, ob_ddl_operator.cpp's `init_*`, src/observer/ob_service.cpp | rootserver, observer | Step 2a in memory (observer, thrown away); the real sequence in the first Step 3 batches (ARCHITECTURE §15) |
| Generated inner-table data, system-variable metadata | build_release/generated/share/inner_table; src/share/system_variable | ob-schema | core |
| System-variable hooks | src/sql/session/ob_system_variable_factory.cpp | sql-session | SQL tier |

The gate sits in ob-runtime because the seekdb binary crate may use only crates 1-3, 7 and 38 (ARCHITECTURE §1.1), and section 4 needs seekdb to call the gate before `fork`. Without the map rows of ARCHITECTURE §1.2 fix 1 the map would send ob_data_version_mgr, the `ObRecordHeader` it deserializes (src/oblib/common/ob_data_version_mgr.cpp:114-116) and ob_version_def to ob-values, which seekdb may not use.

## 2. Which storage inputs to the estimates stay identical (Decision 6; PLAN §8 item 17)

ARCHITECTURE §6.1 chose option (b): the row-count rules, the count definitions and the memtable B-tree rules stay identical; the block layout is free. This section writes that choice as rules.

### 2.1 What the optimizer reads from storage

| Method | What it returns | Callers |
|---|---|---|
| `ObIStorageEstimator::estimate_row_count_for_batch` | logical and physical rows of a set of ranges in one tablet, plus one `ObEstRowCountRecord` per table | src/sql/optimizer/ob_storage_estimator.cpp:131-143, which swallows any error and marks the result unreliable (:94-104) |
| `ObIStorageEstimator::estimate_block_count_and_row_count` | per tablet: data macro blocks, data micro blocks, sstable rows, memtable rows | ob_storage_estimator.cpp:148-186, for dynamic sampling (ob_dynamic_sampling.cpp:823-850) and statistics gathering (ob_basic_stats_estimator.cpp:265-299) |
| `ObIOptimizerStorageService::get_latest_tablet_row_count_delta` | inserted minus deleted rows of the latest tablet statistics | ob_opt_stat_service.cpp:351-362, which reads `OB_HASH_NOT_EXIST` as 0 |
| `ObIOptimizerStorageService::run_io_benchmark` | disk read speeds | only DBMS_STATS system statistics; no configured case gathers them (R06 §1.2) |

`ObEstRowCountRecord` reaches text only through `ObTableScanSpec::explain_index_selection_info` (src/sql/engine/table/ob_table_scan_op.cpp:642-658), which no configured .result prints (`grep -rl 'estimation info\[table_id' tools/deploy/mysql_test`: no file). The logical plan's "Optimization Info" block does print `physical_range_rows` and `logical_range_rows` (src/sql/optimizer/ob_log_table_scan.cpp:1662-1779), unmasked, in view_2.result:473-498.

### 2.2 Rules: what stays identical

1. **The memtable range count** is `ObQueryEngine::estimate_row_count` (ob_query_engine.cpp:561-637) with `sample_rows` (:267-343), rule for rule:
   - a reverse walk from the range end, then a forward walk from the start, each stopping after `MAX_SAMPLE_ROW_COUNT` = 500 rows (ob_query_engine.h:134; ob_query_engine.cpp:330);
   - the forward walk runs only when the reverse walk stopped at 500; when the reverse walk reaches the range start it returns `OB_ITER_END`, which skips the forward walk (:597-606) and is turned into success at the end (:635);
   - the five cases on the row's first and last DML flag (:306-328): both `DF_NOT_EXIST` and the newest node is the caller's transaction, +1; first `DF_INSERT` and last not `DF_DELETE`, +1; last `DF_DELETE` with first not `DF_INSERT`, −1; insert then delete, 0; anything else, 0;
   - above 500 rows, the rest of the range comes from the B-tree element walk (rule 2) and the logical count is scaled by `(element_count + 500) / 1000` in `double`, truncated to `i64` (:615-628).

   So the count is exact for any range of fewer than 500 rows. `ratio`, `delete_row_count` and `empty_delete_row_count` feed only `ratio`, which nothing reads (:613); the port drops them.
2. **The memtable B-tree keeps `ObKeyBtree`'s shape.** 15 keys per node (`NODE_KEY_COUNT`, ob_keybtree_deps.h:47). A full inner node splits at position 7. A full leaf splits at the running average of every earlier leaf split position of this tree, at least 1: `split_info_` packs a 32-bit sum and a 32-bit count, a split adds `(1 << 32) + pos` to it, and the tree starts with one split at 7 (ob_keybtree.cpp:159-178, :1483-1487, :1731-1739; ob_keybtree.h:312-318). Keys are never removed while the memtable lives, so its size counts every distinct key ever inserted (:1586, :1627). The element walk is kept too (ob_keybtree.cpp:922-994, :1033-1085): leaf batches of 1, 2, 4 … up to 1,024 leaves, stopping after 500 leaves; a batch whose last key passes the range end counts half (:1063-1068); at 500 leaves or more, walk level 1 from batches of 64 and multiply by the rounded average keys per leaf (:976-990). The node layout for a given insert order may not change; the concurrency may (ARCHITECTURE §11: locks first, ob-epoch only where needed).
3. **The sstable range count** is `ObIndexBlockScanEstimator` (ob_index_sstable_estimator.cpp:96-401) run over the Rust index tree: sum the row counts of the root index rows, subtract the index rows wholly outside each border, and at each border descend to the data micro block and count its excluded rows exactly (:193-286, :288-330, :379-401; ob_micro_block_row_scanner.cpp:1036-1074). On the right border of a major sstable, stop descending when the rows in range are at least `RANGE_ROWS_IN_AND_BORDER_RATIO_THRESHOLD` = 1,000 times the border index row's rows (ob_index_sstable_estimator.h:135; .cpp:258-274). A multi-version minor sstable's logical count is its row-count deltas and its physical count counts every stored version (.cpp:96-111). The tree's layout is free; this walk over it is not.
4. **Combining tables** is `ObTableEstimator` (ob_table_estimator.cpp:28-221): a get counts its keys; a single-rowkey range counts 1 (:106-109); tables in snapshot order, empty ones skipped; a negative logical count is repaired against the running total by `fix_invalid_logic_row` (:209-221); after the last table, a negative count becomes 1 and logical may not exceed physical (:82-89). A memtable result with logical ≤ 0 and physical > 1,024 (ob_index_sstable_estimator.h:35) is estimated again over the memtable's split into at most 3 ranges; an error keeps the first result, and a split into one range makes the result 0 (ob_table_estimator.cpp:117-148). Keep all of it.
5. **Which tables count** (ob_ls_tablet_service.cpp:4715-4784): the tables readable at the snapshot the optimizer passes, the maximum readable SCN (ob_storage_estimator.cpp:42); a minor sstable whose upper transaction version is at or below the major's data version is skipped, and so is any table with no data to read.
6. **The per-tablet counts keep their definitions** (`inner_estimate_block_count_and_row_count`, ob_ls_tablet_service.cpp:4786-4829): the sum over sstables of data macro blocks and data micro blocks, index blocks excluded (ob_sstable_meta.h:106-109), and of the rows the writer recorded; memtable rows are each data memtable's B-tree size (ob_memtable.h:308), deleted and rolled-back keys included. The values may differ where the layout differs; the definitions may not.
7. **The row delta** is `insert_row_cnt_ - delete_row_cnt_` of `ObTabletStatMgr`'s latest statistics, with the C++ buckets (src/storage/ob_tablet_stat_mgr.h:288-293), and `OB_HASH_NOT_EXIST` when there is none (ob_access_service.cpp:1212-1235).
8. **The freeze triggers, each with its parameter and default.** A Rust memtable has no size cap of its own and freezes only on these:
   - memstore use at `freeze_trigger_percentage` (20) of the memstore limit (ob_parameter_seed.ipp:357; ob_memstore_freezer.cpp:1049-1055);
   - fast freeze, checked once a memtable is 300 s old (ob_tablet_scheduler.h:81-87; .cpp:73-160);
   - `ALTER SYSTEM MINOR/MAJOR FREEZE`, leaving data in the same kind of table (mini, minor or major) as the C++;
   - the daily major freeze at `major_freeze_duty_time` 02:00;
   - the clog disk usage flush (`ObCheckClogDiskUsageTask`), checked every 2 s, when unrecyclable clog reaches 30% of the log disk (ob_checkpoint_service.cpp:35, :185-198; ob_log_service.cpp:538-557);
   - the advance-checkpoint flush, which freezes every memtable 10 minutes after start and every 10 minutes after that (`_advance_checkpoint_interval` 10m, ob_parameter_seed.ipp:1162-1165; checked every 60 s, ob_checkpoint_service.cpp:40, :56, :200-231);
   - FORK TABLE's freeze: the fork task freezes every source tablet before it forks (`ObTabletForkUtil::freeze_tablets`, src/rootserver/fork_table/ob_fork_table_task.cpp:311), and the tablet fork task freezes again and waits while a memtable starts below the fork snapshot (src/storage/ddl/ob_tablet_fork_task.cpp:1328-1340, freezing through `tablet_freeze` at :1471-1500).

   These are all the freezes at 834bbee1e: every caller of `tablet_freeze(` or `freeze_tablets(` (`grep -rn 'tablet_freeze(\|freeze_tablets(' src --include='*.cpp'`) is one of the triggers above or their machinery (src/observer/ob_service.cpp for `ALTER SYSTEM`; src/storage/compaction/ob_tablet_scheduler.cpp for fast freeze; src/storage/compaction/ob_batch_freeze_tablets_dag.cpp, which the major merge's scheduler starts, src/storage/compaction/ob_basic_schedule_tablet_func.cpp:83-96; src/storage/checkpoint/ob_data_checkpoint.cpp for the two checkpoint flushes; the fork files above; and the freezer and log stream that implement them; the grep also matches the `get_is_tablet_freeze`/`set_is_tablet_freeze` accessors, which freeze nothing).

   Whether the advance-checkpoint flush fires inside a judged run follows from its timer: a 60 s check, a 10-minute interval counted from the service's start (`prev_advance_ckpt_task_ts_` is set at init, ob_checkpoint_service.cpp:56, and after each flush, :222-223), and a full run of the 272 cases of about 23 minutes (1,409 s, migration/judge/validation-834bbee1e.md:13). The log line once cited as proof (pass A's seekdb.log, 20:58:18, "skip advance checkpoint because interval is not reached", previous timestamp 20:53:18) does not show a flush: the runner restarts instances after failed cases, so 20:53:18 may be a start. A C++-against-C++ run with `_advance_checkpoint_interval=0m` (section 8, question 1) settles whether any recorded line depends on it.
9. **The compaction state that tests poll** keeps its values: `DBA_OB_MAJOR_COMPACTION.frozen_scn` and `last_scn` become equal after a major freeze, `__all_virtual_tablet_memstore_info.is_active`, and `__all_virtual_tablet_compaction_history` rows with `type = 'MAJOR_MERGE'` (R06 §1.2 lists the include files that wait on them).
10. **Table options stay schema attributes that print the same text**, whatever storage does with them: `COMPRESSION = 'zstd_1.3.8'`, `BLOCK_SIZE = 16384`, `TABLET_SIZE = 134217728`, `PCTFREE = 0`, `ORGANIZATION INDEX` (R06 §3.4 counts about 170 lines of each). Blocks are compressed with the kept zstd 1.3.8 (default 15).
11. **One SQL-tier constant:** `sizeof(*arg)` in `calc_sys_op_opnsize` (src/sql/engine/expr/ob_expr_sys_op_opnsize.cpp:73, :79) becomes the literal 12, the C++ `ObDatum` size (R06 §1.2), never `size_of::<ObDatum>()`; it sets DBMS_STATS's average column length.

### 2.3 What is free, and what that changes

Free: every on-disk format, the macro and micro block layout, the encodings, the index tree, checksums, and the rows-per-micro-block cut (`ObMicroBlockAdaptiveSplitter`, ob_macro_block_writer.cpp:276-306; 16 target rows and 3 minimum, ob_macro_block_writer.h:131-132). The consequences, all confined to data in sstables:
- EST.TIME differs for a table with sstable data and gathered statistics or dynamic sampling, because block counts reach cost only there (ob_opt_est_cost_model.cpp:118-129; `micro_block_count_` starts at −1, ob_opt_est_cost_model.h:66);
- EST.ROWS differs for a major-sstable range of 1,000 or more times its border block's rows;
- dynamic sampling's block ratio differs for tables above 22,000 rows with sstable data (ob_dynamic_sampling.cpp:756-790);
- scan batches in block scan end at other rows (section 3.1, rule 5).

These differences are documented at the final gate (default 14). Sections 7.1 and 9 say how they are measured first and why the advance-checkpoint flush makes them reach more cases than the 9 that freeze explicitly; all 16 configured fork_table cases (migration/judge/census/portable.txt) also read sstable data, since FORK TABLE freezes their source tablets (2.2 rule 8), 3 of them among the 9.

### 2.4 The Rust interface

```rust
pub struct ObPartitionEst {
    pub logical_row_count_: i64,
    pub physical_row_count_: i64,
}

impl ObPartitionEst {
    pub fn is_invalid_memtable_result(&self) -> bool {
        self.logical_row_count_ <= 0 && self.physical_row_count_ > 1024
    }
}

#[derive(Default)]
pub struct BlockAndRowCounts {                  // new: the C++ returns four out-parameters, so plain names
    pub macro_block_count: i64,
    pub micro_block_count: i64,
    pub sstable_row_count: i64,
    pub memtable_row_count: i64,
}

pub trait ObIStorageEstimator: Send + Sync {
    fn estimate_row_count_for_batch(
        &self,
        param: &ObTableScanParam<'_>,
        batch: &ObSimpleBatch<'_>,
        timeout_us: i64,
        est_records: &mut Vec<ObEstRowCountRecord>,
    ) -> ObResult<ObPartitionEst>;

    fn estimate_block_count_and_row_count(
        &self,
        tablet_id: ObTabletID,
        timeout_us: i64,
    ) -> ObResult<BlockAndRowCounts>;
}

pub trait ObIOptimizerStorageService: Send + Sync {
    fn get_latest_tablet_row_count_delta(&self, tablet_id: ObTabletID) -> ObResult<i64>;
    fn run_io_benchmark(&self) -> ObResult<(i64, i64)>;
}
```

- The traits live in storage-api; `ObPartitionEst` moves down from storage/access into it (ARCHITECTURE §1.2, fix 1). storage-tablet implements them; `StorageContext`, the lowest context whose crate may name storage-api, holds them as `Arc<dyn …>` fields and `SqlContext` reaches them by deref (ARCHITECTURE §7.1), replacing `server_service<ObIStorageEstimator>()`.
- Counts come back only with `Ok`. Every C++ caller reads the out-parameters only on success (ob_storage_estimator.cpp:94-104, :169-182), so nothing is lost. The error codes stay the C++ ones (`OB_NOT_INIT`, `OB_INVALID_ARGUMENT`, `OB_TABLET_NOT_EXIST`), and `est_records` is filled as the C++ fills it, partly on error.
- The caller's arena argument goes (ARCHITECTURE §3.1); `timeout_us` stays an argument, as the C++ takes it from `THIS_WORKER`.

### 2.5 Worked examples

**The memtable walk.** C++, ob_query_engine.cpp:295-334, condensed:

```cpp
while (OB_SUCC(ret)) {
  if (OB_FAIL(iter->next())) {
  } else if (OB_ISNULL(value = iter->get_value())) {
    ret = OB_ERR_UNEXPECTED;
  } else {
    ++sample_row_count;
    ++physical_row_count;
    if (DF_NOT_EXIST == value->first_dml_flag_ && DF_NOT_EXIST == value->last_dml_flag_) {
      ObMvccTransNode *iter = value->get_list_head();
      if (nullptr != iter && iter->get_tx_id() == tx_id) { ++logical_row_count; }
    } else if (DF_INSERT == value->first_dml_flag_ && DF_DELETE != value->last_dml_flag_) {
      ++logical_row_count;
    } else if (DF_DELETE == value->last_dml_flag_) {
      if (DF_INSERT != value->first_dml_flag_) { --logical_row_count; ++delete_row_count; }
      else { ++empty_delete_row_count; }
    }
    if (sample_row_count >= MAX_SAMPLE_ROW_COUNT) { break; }
  }
}
```

Rust, rust/storage-tx/src/mvcc/ob_query_engine.rs (the map row `src/storage/memtable` has no `dir`, so `<dir>` is the part below it):

```rust
fn sample_rows(
    &self,
    iter: &mut BtreeRawIterator<'_>,
    start_key: &ObMemtableKey,
    start_exclude: bool,
    end_key: &ObMemtableKey,
    end_exclude: bool,
    tx_id: ObTransID,
    logical_row_count: &mut i64,
    physical_row_count: &mut i64,
) -> ObResult {
    let mut sample_row_count: i64 = 0;
    *logical_row_count = 0;
    *physical_row_count = 0;
    iter.reset();
    let mut ret = self.keybtree_.set_key_range(iter, start_key, start_exclude, end_key, end_exclude);
    while ret.is_ok() {
        let value = match iter.next() {
            Ok(value) => value,
            Err(e) => {
                ret = Err(e);
                break;
            }
        };
        sample_row_count += 1;
        *physical_row_count += 1;
        let (first, last) = (value.first_dml_flag_, value.last_dml_flag_);
        if first == ObDmlFlag::DF_NOT_EXIST && last == ObDmlFlag::DF_NOT_EXIST {
            if value.get_list_head().is_some_and(|node| node.get_tx_id() == tx_id) {
                *logical_row_count += 1;
            }
        } else if first == ObDmlFlag::DF_INSERT && last != ObDmlFlag::DF_DELETE {
            *logical_row_count += 1;
        } else if last == ObDmlFlag::DF_DELETE && first != ObDmlFlag::DF_INSERT {
            *logical_row_count -= 1;
        }
        if sample_row_count >= MAX_SAMPLE_ROW_COUNT {
            break;
        }
    }
    ret
}
```

The counts go out through `&mut` because the driver adds them even when the walk ended with `OB_ITER_END` (ob_query_engine.cpp:609-612); a code used as a value stays a value (ARCHITECTURE §2). The null check goes because the iterator yields a reference. The driver keeps the C++ order:

```rust
let mut ret = self.sample_rows(&mut iter, end_key, end_exclude, start_key, start_exclude,
                               tx_id, &mut log1, &mut phy1);
if ret.is_ok() {
    ret = self.sample_rows(&mut iter, start_key, start_exclude, end_key, end_exclude,
                           tx_id, &mut log2, &mut phy2);
}
let mut logical_row_count = log1 + log2;
let mut physical_row_count = phy1 + phy2;
if ret.is_ok() {
    ret = iter.estimate_element_count(&mut remaining_row_count, &mut element_count);
    if ret == Err(OB_ITER_END) {
        logical_row_count = (logical_row_count as f64
            * ((element_count + MAX_SAMPLE_ROW_COUNT) as f64
                / (MAX_SAMPLE_ROW_COUNT * 2) as f64)) as i64;
        physical_row_count += remaining_row_count - phy1;
        ret = Ok(());
    }
}
match ret {
    Ok(()) | Err(OB_ITER_END) => Ok(ObPartitionEst {
        logical_row_count_: logical_row_count,
        physical_row_count_: physical_row_count,
    }),
    Err(e) => Err(e),
}
```

**The leaf split point.** C++, ob_keybtree.cpp:1731-1739:

```cpp
int32_t ObKeyBtree<BtreeKey, BtreeVal>::update_split_info(int32_t split_pos)
{
  if (split_pos < 0) { split_pos = 0; }
  UNUSED(ATOMIC_FAA(&split_info_, 0x100000000ULL + split_pos));
  const int32_t ret = split_pos_sum_ / split_count_;
  return (ret < 1) ? 1 : ret;
}
```

Rust:

```rust
pub fn update_split_info(&self, mut split_pos: i32) -> i32 {
    if split_pos < 0 {
        split_pos = 0;
    }
    self.split_info_.fetch_add((1u64 << 32).wrapping_add(split_pos as u64), Ordering::SeqCst);
    let info = self.split_info_.load(Ordering::SeqCst);
    let ret = ((info as u32) / ((info >> 32) as u32)) as i32;
    if ret < 1 { 1 } else { ret }
}
```

`split_info_` is an `AtomicU64`: the C++ union puts the sum in the low half on the little-endian targets it runs on. The C++ reads the two halves without an atomic load, a data race that ARCHITECTURE §11 turns into one `SeqCst` load; with one inserting thread both give the same number.

**The per-tablet counts.** C++, ob_ls_tablet_service.cpp:4804-4828, iterates the read tables; Rust, over the tablet snapshot's read tables in the same order:

```rust
fn inner_estimate_block_count_and_row_count(&self, tables: &[ObTableHandle]) -> ObResult<BlockAndRowCounts> {
    let mut counts = BlockAndRowCounts::default();
    for table in tables {
        match table.get() {
            ObITable::Memtable(memtable) if memtable.is_data_memtable() => {
                counts.memtable_row_count += memtable.get_physical_row_cnt();
            }
            ObITable::SSTable(sstable) => {
                let meta = sstable.get_meta()?;
                counts.macro_block_count += sstable.get_data_macro_block_count();
                counts.micro_block_count += meta.get_data_micro_block_count();
                counts.sstable_row_count += meta.get_row_count();
            }
            _ => {}
        }
    }
    Ok(counts)
}
```

`?` is allowed here: the C++ loop stops at the first error and nothing runs after it (ARCHITECTURE §2).

### 2.6 What a reviewer checks

- The constants 500, 15, 7, 1,024, 3, 1,000, 64, 22,000 and 12 appear with the C++ names and values.
- `sample_rows` keeps the five cases in the C++ order, and the driver skips the forward walk after `OB_ITER_END`.
- The leaf split uses the running average; no Rust B-tree crate, skip list or other ordered map stands in for the memtable index.
- The per-tablet counts exclude index blocks, and the memtable count is the B-tree size, not the count of live rows.
- Nothing freezes a memtable outside the triggers of 2.2 rule 8, FORK TABLE's included, and no memtable has a byte cap of its own.
- The block-writing code does not claim to reproduce `ObMicroBlockAdaptiveSplitter`; if 7.1's mutation 3 moves an unmasked line, an amendment makes the cut rule kept, and the reviewer then checks it.

## 3. The order in which scans return rows (PLAN §8 item 18)

9,042 single-table SELECTs have no ORDER BY; 7,202 print two or more rows; 6,096 of those read tables without a primary key, 5,740 of them in the four arithmetic matrices (R06 §1.3, from single_table_rows2.py). None is masked, so the order below is a contract.

### 3.1 Rules for storage

1. **Within a tablet**, a forward scan returns rows in ascending rowkey order and a reverse scan in descending order. Over several ranges, rows come ordered by the range's position in the caller's list, then by rowkey, as the loser tree compares them (ob_scan_merge_loser_tree.cpp:72-93). Versions of one rowkey in memtables and sstables fuse into one row.
2. **One rowkey compare.** Every rowkey compare in storage (memtable B-tree, sstable index, merge, range borders) uses the value library's null-first compare for the column's type, collation, scale, precision and LOB header, ascending, NULL first: the `null_first_cmp_` that `ObStorageDatumUtils` takes from `ObDatumFuncs::get_basic_func` (src/storage/blocksstable/ob_storage_datum.cpp:189-225). The C++ memtable instead compares `ObObj` values through `ObRowkey::fast_compare` (src/oblib/common/rowkey/ob_rowkey.h:219-259). The two must agree; the core build's value-library differential test (7.2) compares the Rust compare with both.
3. **Tables without a primary key** get the hidden `__pk_increment`, column id 1, `uint64`, binary collation (src/oblib/lib/ob_define.h:583, :615; added at ob_create_table_resolver.cpp:109-135). Storage hands out its values through `ObITabletAutoincrementService::next_value` (src/share/autoincrement/ob_i_tablet_autoincrement_service.h:38-40):
   - per tablet, increasing, from cached intervals of 10,000 (`DEFAULT_TABLET_INCREMENT_CACHE_SIZE`, src/share/ob_tablet_autoincrement_param.h:32);
   - the next interval is fetched once a quarter of the current one is used (`PREFETCH_THRESHOLD` 4, src/storage/ob_tablet_autoincrement_service.h:86-105);
   - the interval end is persisted with the tablet as its `ObTabletAutoincSeq` (src/storage/ob_tablet_autoinc_seq_service.cpp:162-200), so values after a restart continue above it.

   The Rust tablet keeps the sequence in its multi-source-data state as the C++ does. Rows of such a table therefore come back in insert order within each tablet.
4. **UPDATE keeps the hidden pk** (`copy_heap_table_hidden_pk`, src/sql/engine/dml/ob_dml_service.cpp:1806-1832); a row that UPDATE moves to another tablet takes a new value from that tablet (:970-973).
5. **Batches end only where the C++ ends them**, for the four reasons ob_multiple_merge.cpp:605-611 lists: the capacity, which is the smaller of the caller's capacity and the operator's batch size (:595); the end of one micro block's rows in the `BATCH` state, the block scan (ob_vector_store.cpp:338-340); a switch from the `SINGLE_ROW` state to `BATCH` (ob_multiple_merge.cpp:636-644); the LIMIT count. Memtable rows always go through `SINGLE_ROW`, since only the sstable scanner overrides `can_batch_scan` (ob_store_row_iterator.h:96-99; ob_sstable_row_scanner.h:53), so a memtable-only scan ends batches only at the capacity, the LIMIT count and the end of the scan. Block scan needs a single table producing rows at that key range and a micro block wholly in range (ob_multiple_scan_merge.cpp:596-613; ob_sstable_row_scanner.ipp:109-121). This is ARCHITECTURE §6.2's "same storage events": the same kinds of events; for sstable data the micro-block event falls where the Rust cut puts it (2.3).
6. **No scan-time parallelism** that the C++ does not have.

### 3.2 Rules for the SQL tier that fix the order storage sees

These units are translated keeping their control flow; the rules are listed because the scan order depends on them.
- Non-point ranges are sorted by start key (src/sql/rewrite/ob_range_generator.cpp:1290); IN values are sorted (:1440-1442); point ranges are de-duplicated through a hash set and keep their generated order (:1268-1287); spatial cell ranges come from hash-set iteration (:1799), which shows in geometry_partition_table_mysql.result:250-252 (R06 §1.3). Every sort is the transcribed `ob_sort` (ARCHITECTURE §10).
- Tablets come in schema partition order (src/sql/optimizer/ob_table_location.cpp:3552-3579). DAS reads them in task order unless the plan must keep a sort order (src/sql/das/iter/ob_das_merge_iter.cpp:143-163).
- PX at dop 1 uses one partition granule per tablet (src/sql/engine/px/ob_granule_util.cpp:56-70), tablet by tablet with ranges ascending (ob_granule_pump.cpp:205-251, :843). Tasks are reordered only for DDL and PDML (:654-686), and then by `murmurhash` of the task index, descending (:235, :248), which is deterministic.
- Above dop 1 the order depends on timing in the C++ too; such cases follow PLAN §4's quarantine rule.

### 3.3 Worked example: the merge compare

C++, ob_scan_merge_loser_tree.cpp:72-93, after the init and null checks:

```cpp
cmp_ret = l.row_->scan_index_ - r.row_->scan_index_;
if (0 == cmp_ret) {
  if (OB_FAIL(compare_rowkey(*l.row_, *r.row_, cmp_ret))) {
  } else if (reverse_) {
    cmp_ret = -cmp_ret;
  }
}
```

Rust, rust/storage-tablet/src/ob_scan_merge_loser_tree.rs (the map row `src/storage/access` has no `dir`):

```rust
pub fn cmp(&self, l: &ObScanMergeLoserTreeItem<'_>, r: &ObScanMergeLoserTreeItem<'_>) -> ObResult<i64> {
    if l.row_.scan_index_ < 0 || r.row_.scan_index_ < 0 {
        return Err(OB_INVALID_ARGUMENT);
    }
    let mut cmp_ret = l.row_.scan_index_ - r.row_.scan_index_;
    if cmp_ret == 0 {
        cmp_ret = self.compare_rowkey(l.row_, r.row_)?;
        if self.reverse_ {
            cmp_ret = -cmp_ret;
        }
    }
    Ok(cmp_ret)
}
```

`compare_rowkey` calls the one compare of rule 2 over the first `rowkey_size_` columns. The `IS_NOT_INIT` check goes because the constructor takes the datum utilities (ARCHITECTURE §7.1).

### 3.4 What a reviewer checks

- No scan orders rows by physical position, by a sequence shared by all tablets, or by a hash.
- No rowkey compare in storage calls anything but the value library's null-first compare.
- `next_value` takes the tablet id, and an UPDATE path copies the old hidden pk unless the tablet changes.
- A batch never ends for a reason outside 3.1 rule 5, and a memtable scan never ends one early.
- The narrow run's statement list (ARCHITECTURE §15) includes tables without a primary key; three of the four matrices (add, datatype.minus, expr.mul) are among the 100 cases that need no leaves (lines 2, 59 and 72 of migration/design/evidence/plain_sql_no_vt_pl.txt), so the core build's first run catches drift in rules 2-3 at once.

## 4. How the Rust build refuses a data directory it did not write (Decision 11)

### 4.1 What the C++ does

- main.cpp:705 changes to the base dir. main.cpp:733-741 then turns the process into a daemon unless `--nodaemon` or `--initialize` is given, and only then calls `ObServer::init` (:798).
- `init_config` (ob_server.cpp:1691-1720) creates ./store/sstable, opens ./store/sstable/meta.db through `getcwd()`, and loads the configuration, and only then loads and checks the version file (:1716-1719). So a refusal comes after meta.db was written to, and in daemon mode it never reaches the terminal. The process ends with `_exit(1)` (main.cpp:821-824).
- A missing file is logged and skipped (ob_data_version_mgr.cpp:88-94), then the current version is written (:58-60, :163-209). v1.0.0 and v1.0.1 wrote etc/observer.data_version.bin instead, so their directories are treated as fresh today (evidence-full.md:3305).
- The file is a 32-byte `ObRecordHeader`, written big-endian field by field (ob_record_header.cpp:112-122; serialization.h:155-164), then the payload `"%s %lu\n"`. The header holds magic 0xBEDE, format 2 (ob_data_version_mgr.h:93-94), a data checksum `ob_crc64` of the payload, and a header checksum that XORs 16-bit pieces (ob_record_header.cpp:31-45). On arm64, `ob_crc64` is CRC-32C with initial value 0 and no final XOR (ob_crc64.cpp:357-398, :1070-1072).
- The writer writes `<file>.tmp`, calls `fsync`, renames the old file to `<file>.history`, then renames the temporary file into place (ob_data_version_mgr.cpp:286-364).

### 4.2 Rules

1. **Value.** `DATA_CURRENT_VERSION = cal_version(2, 0, 0, 0)` = 8589934592, printed "2.0.0.0" (default 24). The package version and `SERVER_CURRENT_VERSION` (ob_version_def.h:52) stay 1.4.0.0. No code compares the data version with another release's constant (`grep -rn 'DATA_VERSION_[0-9]' src` outside ob_version_def.h: 0 lines), so the bump changes stored values, never a code path.
2. **Format.** The Rust writer produces the C++ bytes, so the frozen C++ binary reads the file and refuses it by its own equality check (ob_data_version_mgr.cpp:61-65). For 2.0.0.0 the file is exactly these 51 bytes:
   ```
   bede 0020 0002 a21c 0000000000000000 00000013 00000013 00000000408ca393
   322e302e302e3020383538393933343539320a        ("2.0.0.0 8589934592\n")
   ```
   migration/design/evidence/s6/data_version_file.py rebuilds the file from a real C++ directory byte for byte (/Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e-rw/base/etc/seekdb.data_version.bin), then prints the 2.0.0.0 file. One trap: the header checksum XORs the `int16_t` magic after sign extension to 64 bits (ob_record_header.h:30-37), so the three upper pieces are 0xFFFF. The timestamp field is 0. ob-base's `ob_crc64(crc: u64, data: &[u8]) -> u64` is CRC-32C (polynomial 0x82F63B78) from the caller's initial value with no final XOR. It wraps the crc32c crate, whose `unsafe` intrinsics stay inside that third-party crate, as `!crc32c_append(!init, data)` on the low 32 bits (s8-numerics-platform.md 9.2), and known-answer vectors from the C++ pin it, this file included.
3. **Where it runs.** `ObDataVersionMgr::check_base_dir` in ob-runtime runs twice. The seekdb binary calls it after resolving the canonical base dir and setting up the log sink, and before `fork`: a refusal then reaches the terminal, and the shell gets status 1. With `--nodaemon`, which the judge's runner passes, that is today's status for a failed start (`_exit(1)`, src/observer/main.cpp:821-824). In daemon mode it is a change: the C++ parent calls `daemon(3)` (src/oblib/lib/utility/utility.cpp:1559, from `start_daemon` at main.cpp:736) before `ObServer::init` (:798), and `daemon(3)` returns status 0 to the shell, so a failed daemon-mode start returned 0; the Rust refusal returns 1. That change is intended (Decision 11 asks for a clear message at start-up) and family 15 records it. `Server::open` calls it again before opening meta.db, the clog or any storage file, and returns the refusal as an error to a library host.
4. **When the file exists,** it must pass every check of the C++ reader: header checksum, lengths, magic, format, payload checksum, trailing newline, a single entry, and the string matching the number (ob_data_version_mgr.cpp:73-161, :234-284). A file that parses to another version refuses with `OB_NOT_SUPPORTED`; a file that fails to parse refuses with `OB_INVALID_DATA`. Neither is ever treated as fresh.
5. **When the file is missing,** the directory is fresh only if none of these exists:
   - the block file `<data_dir>/sstable/block_file`, with `data_dir` from `--data-dir` or the default `store` (ob_server.cpp:1782-1810; ob_block_sstable_struct.cpp:32-33);
   - a non-empty clog directory under `--redo-dir`, or `<data_dir>/redo` by default (ob_server.cpp:410-433, :1826-1835);
   - etc/observer.data_version.bin;
   - store/sstable/meta.db, which every C++ start creates first (ob_server.cpp:1698-1716);
   - store/meta/meta.db.

   ARCHITECTURE §12 item 2 took the last two from this rule (section 9, objection 4). Anything else refuses.
6. **Bootstrap writes the file first,** before any meta.db, clog or block file, with the C++ sequence of temporary file, `fsync` and rename. A kill between the rename and the first storage file leaves a directory the next start bootstraps again (4.2 rule 5 finds nothing). The Rust build never rewrites the file.
7. **The message** is one line, the same on stderr and in the log. The log line also carries the code, `ret=-4007` (`OB_NOT_SUPPORTED`) or `ret=-4070` (`OB_INVALID_DATA`, src/share/ob_errno.def:135):
   `seekdb: cannot open <base dir>: <reason>; this build needs data version 2.0.0.0. To move data from another seekdb version, export it with the build that wrote it, then import the export into an empty directory with this build.`
   The reason is one of:
   - `it holds data version <string> (<number>)`;
   - `it holds etc/observer.data_version.bin, written by seekdb 1.0`;
   - `etc/seekdb.data_version.bin is missing but <path> exists`;
   - `etc/seekdb.data_version.bin is damaged (<which check failed>)`.
8. **meta.db** is SQLite through rusqlite at store/meta/meta.db (default 24), opened by `Server::open` after the gate with the path joined to the absolute base dir, `journal_mode=WAL` and `synchronous=NORMAL` as today (src/share/storage/ob_sqlite_connection.cpp:284, :304). Its seven tables are created from the generated `CREATE TABLE` text (section 6). A frozen C++ binary started on a Rust directory creates store/sstable/meta.db before it refuses; the Rust build never reads that file.
9. **The other uses of `DATA_CURRENT_VERSION`** (70 lines, R06 §1.4) do not carry over one for one. Values the C++ writes into catalog rows or tablet creation arguments carry 2.0.0.0: the freeze info (ob_ddl_operator.cpp:3665) and tablet creation (ob_bootstrap.cpp:335, :381). The strict equality checks inside persisted objects (ob_macro_block_meta.cpp, ob_tx_table_define.cpp:86, ob_medium_compaction_info.cpp, ob_pl_user_type.cpp:422) become each new format's own version field (R06 §4.5 rule 6).

### 4.3 Worked example: the start order

C++, ob_server.cpp:1710-1719, inside `init_config`, which runs after the fork:

```cpp
} else if (OB_FAIL(FileDirectoryUtils::create_full_path(meta_db_dir))) {
} else if (OB_FAIL(meta_db_pool_.init(abs_meta_db_path))) {
} else if (OB_FAIL(config_mgr_.init(&meta_db_pool_))) {
} else if (OB_FAIL(config_mgr_.got_version())) {
} else if (FALSE_IT(base_version = config_mgr_.get_current_version())) {
} else if (OB_FAIL(DATA_VERSION_MGR.init())) {
} else if (OB_FAIL(DATA_VERSION_MGR.load_from_file())) {
} else if (OB_FAIL(DATA_VERSION_MGR.validate_or_init_current_version())) {
}
```

Rust, the seekdb crate's `main`, then `Server::open` in observer:

```rust
let base_dir = canonical_base_dir(&opts)?;
let mut message: Vec<u8> = Vec::new();
if let Err(ret) = ObDataVersionMgr::check_base_dir(&base_dir, &opts.data_dir_, &opts.redo_dir_, &mut message) {
    let _ = std::io::stderr().write_all(&message);
    log_error!(ret, "the data version check refused the base dir", K(message));
    std::process::exit(1);
}
if !opts.nodaemon_ && !opts.initialize_ {
    start_daemon(&base_dir.join("run/seekdb.pid"))?;
}
let server = Server::open(ObServerOptions::from(&opts, base_dir))?;
```

```rust
pub fn open(options: ObServerOptions) -> ObResult<Server> {
    let mut message: Vec<u8> = Vec::new();
    let state = match ObDataVersionMgr::check_base_dir(&options.base_dir_, &options.data_dir_,
                                                        &options.redo_dir_, &mut message) {
        Ok(state) => state,
        Err(ret) => {
            log_error!(ret, "the data version check refused the base dir", K(message));
            return Err(ret);
        }
    };
    if state == DataDirState::Fresh {
        ObDataVersionMgr::write_current_version(&options.base_dir_)?;
    }
    let meta_db = ObSQLiteConnectionPool::open(&options.base_dir_.join("store/meta/meta.db"))?;
    ...
}
```

`check_base_dir` is a hand-designed core API, so it returns `ObResult<DataDirState>`; the refusal is its code (`OB_NOT_SUPPORTED` or `OB_INVALID_DATA`, rule 7) plus the message it writes into the caller's buffer, never a second error type (ARCHITECTURE §2; s2-errors.md 2.12 check 10). The options are `ObServerOptions`' `data_dir_` and `redo_dir_` fields (s1-crates-core.md 3.3); `nodaemon_` and `initialize_` are the binary's own options, which the C++ also keeps in `ObServerOptions` (src/observer/ob_server_options.h:43, :46).

`std::process::exit` is safe here because no engine thread exists yet. A later failure ends the process as main.cpp:821-824 does, through ob-platform's `_exit` (ARCHITECTURE §7.3, §8).

### 4.4 What a reviewer checks

- Nothing under store/ is created or opened before `check_base_dir` returns.
- The writer's bytes equal 4.2 rule 2's 51 bytes, and the reader accepts the C++ 1.4.0.0 file only to refuse it with the version message.
- Family 15 (PLAN §4) covers every condition of 4.2 rule 5: a C++ 1.4.0.0 directory; a v1.0.x directory holding etc/observer.data_version.bin; a C++ directory with its version file deleted; an empty directory, which bootstraps; the frozen C++ binary refusing a Rust directory (its log says "persisted data version does not match this binary"); and the export from C++ v1.4.x imported into Rust.
- `getcwd()` appears nowhere in engine crates.

### 4.5 Which value encodings stay byte for byte

Decision 11 frees what is persisted, and PLAN §3 takes the OB_UNIS, ObNumber and JSON binary bytes out of the judge ("Data directories: no compatibility with the C++ build"). Output can still show an encoding, so the rule is: an encoding stays byte for byte when a SQL function returns its bytes or its size.
1. **Kept:** ObNumber's and decimal-int's binary forms (the value libraries are translated line by line, ARCHITECTURE §10), and the JSON binary, `ObJsonBin`: `JSON_STORAGE_SIZE` returns the stored binary's length (src/sql/engine/expr/ob_expr_json_storage_size.cpp:60-80) and `JSON_STORAGE_FREE` a size taken from it, and family 5 runs every expression (PLAN §4), although no configured case calls either (`grep -rli 'json_storage_size\|json_storage_free' tools/deploy/mysql_test --include='*.test'` finds nothing). The JSON binary is translated with the rest of json_type in ob-values-doc, keeping its layout.
2. **Free:** `OB_UNIS` serialization, which RULEBOOK 2.3 maps to the core's own little-endian encoding, and every on-disk and log format (2.3).
3. **Checks:** the core build's value-library tests compare ObNumber's, decimal-int's and the JSON binary's bytes with the C++'s for recorded values; `OB_UNIS` gets round-trip tests, since its bytes are free. The value-library core sessions list any other encoding a SQL function exposes (LOB locators are the candidates, assumption) and keep it by this rule.

### 4.6 The logical export and import (Decision 11)

The refusal message of rule 7 tells a user to export with the build that wrote the data and import with this one. Decision 11 asks for that path to be tested (family 15; PLAN §6, "Cutover", step 4).
1. **Mechanism** (ARCHITECTURE §12 item 7, default 39): a wrapper script over the client package deps/3rd already ships (deps/3rd/u01/obclient/bin: `mysqldump`, `obclient`), run against running servers: export from a C++ v1.4.x server on its own directory, import into a Rust server started on an empty directory.
2. **Order and options:** every user database, with routines and views; table DDL first, as `SHOW CREATE TABLE` prints it, which both builds parse with the same grammar; then the rows, binary and spatial values as hex so that no byte passes through a character-set conversion, each table in primary-key order and a table without one in hidden-pk order, so its rows keep their scan order after the import (3.1 rule 3); then secondary, fulltext, vector and spatial indexes; then views.
3. **What it must carry** (PLAN §4, family 15): VECTOR, GEOMETRY with its SRID, JSON, LOBs, generated columns, and fulltext, vector and spatial index DDL.
4. **Checks:** 00b runs the round trip C++ to C++ first (export from the reference, import into a fresh reference directory), which also checks the assumption that this `mysqldump` handles every listed type; a type it mishandles gets a workaround in the script, never in the engine. Family 15 then runs C++ v1.4.x to Rust and compares a fixed SELECT set and `SHOW CREATE TABLE` of every table on the two servers.
5. **Owner:** the script lives in migration/scripts/ while family 15 uses it and ships beside the seekdb binary from the cutover.

## 5. What bootstrap does at each stage

### 5.1 Stages

| Piece | Step 2a (in memory, thrown away) | Core-build exit (the 100 cases, default 1) | Step 5 (full init.sql, 272 cases) |
|---|---|---|---|
| Absolute base dir; etc/, log/, run/, store/ | yes | yes | yes |
| The gate and the version file (section 4) | yes | yes | yes |
| meta.db with its seven tables | no | yes | yes |
| The log stream (palf and the three log-stream tablets), 358 system tablets, the two checkpoints as the single commit point | no | yes | yes |
| 640 system schemas from the generated data, with the hard-coded schema versions | yes | yes | yes |
| Catalog rows as the C++ writes them (core key-value rows, `__all_table`, `__all_column` and their history tables, `__all_ddl_operation`, global stat) | no | yes | yes |
| Server runtime "sys"; 776 system variables with the 8 overrides and the environment values; 6 databases; root; the max-id counters | yes | yes | yes |
| Schema writes for CREATE USER, GRANT, CREATE DATABASE, CREATE TABLE | in memory | yes | yes |
| Freeze info, SRS 0, global merge info, 4 parameters | no | yes | yes |
| Statistics preferences, the 3 maintenance-window scheduler jobs | no | no | yes |
| Virtual tables, system views | no | no | yes |
| 13 system packages, loaded asynchronously | no | no | yes |
| Bootstrap telemetry | no | no | no (default 2) |

Counts and sources are R11 §2.2: the 358 system tablets are the schemas outside the virtual and view ranges; the 13 packages come from 26 .sql files; the databases and root are created at ob_ddl_operator.cpp:3423-3656.

### 5.2 Rules

1. **One commit point.** Bootstrap is complete only when the local, then the server, checkpoint is written (ob_server.cpp:1195-1216). A start that finds storage without a committed bootstrap refuses with the C++ messages (ob_server.cpp:1140-1145, :1338-1343). Under Decision 9 every stop is a kill, so this is the only protection.
2. **The Step 2a bootstrap** lives in observer behind an in-memory schema service, which keeps the schemas it is given in memory and reads no inner table, creates the state of R11 §6, and is thrown away. It writes nothing that the core build's bootstrap reads.
3. **System schemas come from the generated data on every start** (default 25). One hand-written builder in ob-schema turns the static definitions of section 6 into `ObTableSchema` values in `construct_inner_table_schemas`' order: the core table, core-related, system, virtual, views, each followed by its indexes and LOB tables (src/share/schema/ob_schema_utils.cpp:390-436). It then assigns the hard-coded schema versions (5.3). A restart reads only user objects and `__all_global_stat` from storage.
4. **System variables** start from the 776 generated defaults, then take the 8 bootstrap overrides (R11 §2.2: `read_only`, `parallel_servers_target` from the CPU count, the 4 charset and collation variables, `lower_case_table_names`, `ob_tcp_invited_nodes = '%'`), then the environment values (src/sql/session/ob_system_variable.cpp:2488-2541). The strings keep their C++ form, built from the canonical base dir:
   - `pid_file` is `<base dir>/run/observer.pid`, although the pid file itself is run/seekdb.pid (main.cpp:670);
   - `socket` is `<base dir>/run/sql.sock`;
   - `datadir` is the configured `data_dir` with the base dir prefix removed (ob_server.cpp:1811-1818).

   `--variable` options apply only at bootstrap (ob_server.cpp:706).
5. **Catalog writes and reads go through inner SQL, as the C++ does** (ARCHITECTURE §12; default 25). The schema service's SQL implementation (src/observer/schema/ob_schema_service_sql_impl.cpp, with the 56 `ORDER BY` fetches its restart reload runs), the `*_sql_service` writers, `ObCoreTableProxy`, `ObGlobalStatProxy`, `ObDMLSqlSplicer` with `ObDMLExecHelper` (src/share/ob_dml_sql_splicer.h:89-143), and bootstrap's raw statements (src/rootserver/ob_runtime_ddl_service.cpp:134-173 among the 62 raw writes and 76 raw reads of R11 §8.1) translate file by file and send the same statement texts, parameterized the same way, in the same order, through `ObISQLClient`, the `StorageContext` cell observer implements (s1-crates-core.md 3.2).
   - Why: family 7 compares plan-cache hits and misses exactly, and its counters are server-wide and count the inner SQL a case's own DDL runs (migration/judge/harness/second-set/README.md, the paragraph beginning "The counters are server-wide"). A Rust catalog path that sent other statements, or none, would change the counts of nearly every case with DDL, and no mask may cover them (Decision 6).
   - The first draft had typed row access here (R11 §8.1 option B: `ObDMLSqlSplicer` over direct row writes, typed scans for the schema load). It stays the alternative in ARCHITECTURE default 25, and needs family 7 changed on both builds to count only a case's own sessions, a PLAN §4 change.
6. **Not ported:** `init_debug_database` (never runs; ob_local_management_service.cpp:1753-1809), `batch_create_schema`, `construct_schema` and `TableIdCompare` (no callers; R11 §2.1); standby's role code, since the Rust build starts as `OB_ENABLE_STANDBY=OFF` does (src/standby/standby_module_disabled.cpp:76-91; ARCHITECTURE §7.4).
7. **System packages** load after start in a timer task, as today, while `_enable_async_load_sys_package` is true (ob_parameter_seed.ipp:1133). They arrive in Step 5 with the PL leaves.

### 5.3 Worked example: the hard-coded schema versions

The C++ assigns versions from the largest down, walking the list backwards and placing each virtual table before its indexes (src/share/schema/ob_schema_utils.cpp:452-503). The Rust builder keeps the algorithm and the order:

```rust
pub fn generate_hard_code_schema_version(tables: &mut [ObTableSchema]) -> ObResult {
    let mut current = (HARD_CODE_SCHEMA_VERSION_BEGIN + tables.len() as i64)
        * ObSchemaVersionGenerator::SCHEMA_VERSION_INC_STEP;
    let mut tid2table: ObHashMap<u64, usize> = ObHashMap::create(tables.len())?;
    for (i, table) in tables.iter().enumerate() {
        tid2table.set_refactored(table.get_table_id(), i)?;
    }
    let mut order: Vec<u64> = Vec::with_capacity(tables.len());
    for table in tables.iter_mut().rev() {
        table.set_schema_version(OB_INVALID_VERSION);
        if table.is_index_table() && is_virtual_table(table.get_data_table_id()) {
            order.push(table.get_data_table_id());
        }
        order.push(table.get_table_id());
    }
    for table_id in order {
        let pos = *tid2table.get(&table_id).ok_or(OB_ERR_UNEXPECTED)?;
        let table = &mut tables[pos];
        if table.get_schema_version() != OB_INVALID_VERSION {
            continue;
        }
        table.set_schema_version(current);
        for column in table.columns_mut() {
            column.set_schema_version(current);
            column.set_table_id(table_id);
        }
        current -= ObSchemaVersionGenerator::SCHEMA_VERSION_INC_STEP;
    }
    if current != HARD_CODE_SCHEMA_VERSION_BEGIN * ObSchemaVersionGenerator::SCHEMA_VERSION_INC_STEP {
        return Err(OB_ERR_UNEXPECTED);
    }
    Ok(())
}
```

A virtual table reached twice keeps its first version, as the C++ `table->get_schema_version() != OB_INVALID_VERSION` test does.

### 5.4 What a reviewer checks

- **The golden catalog dump.** Once, from the archived reference after bootstrap on an empty directory, dump over SQL `__all_table`, `__all_column`, `__all_ddl_operation`, `__all_core_table`, `__all_sys_variable`, `__all_database` and `__all_user` in primary-key order, kept under migration/judge/. Leave out `gmt_create` and `gmt_modified`. Replace with a fixed token the values that differ by design or by run: data-version values (4.2 rule 9), `server_uuid`, `pid_file`, `socket`, `datadir`, `port`, `system_time_zone`, and schema versions taken from the clock. The Rust dump must equal it at the core-build exit. It is an entry-gate check (family 2), not a mask.
- **The variables:** `SELECT @@global.x, @@session.x` for all 776 names on both builds.
- **The static definitions** of section 6 against a normalized dump of the reference's 640 `ObTableSchema` objects, a unit test that covers Step 2a before any SQL runs.
- The catalog's inner SQL is the C++'s statement for statement: the same texts, parameterized the same way, in the same order and in the same transactions (5.2 rule 5), since family 7 counts their plan-cache accesses.

## 6. How the generators produce Rust

### 6.1 What each generator becomes

| Generator (lines) | Input, unchanged | Rust output | Crate |
|---|---|---|---|
| src/share/inner_table/generate_inner_table_schema.py (3,417) | ob_inner_table_schema_def.py (14,510), ob_inner_table_init_data.py (51) | the TID and TNAME constants; one `InnerTableDef` static per schema (640), with column, index, LOB and view data; the creator groups in the C++ order; the counts up to `OB_BOOTSTRAP_SCHEMA_VERSION`; `ALL_PRIVILEGES` (16 rows); the seven SQLite `CREATE TABLE` texts; column maps for the 7 SQLite virtual tables; `table_id_to_name` | ob-schema |
| src/share/system_variable/gen_ob_sys_variables.py (1,200) | ob_system_variable_init.json (776 variables) | the metadata table (id, name, default and base values, type, flags, bounds, enum names), `SysVarClassType`, the `OB_SV_*` names, the by-name lookup order, `ESSENTIAL_SYS_VARS` | ob-schema |
| the same script, second back end | the same JSON | a table naming the hand-written hook functions (35 check-and-convert, 16 to-select-object, 16 to-show-string, 15 get-meta-type, 6 on-update, 6 session-special-update, 23 value kinds; R11 §3.3) | sql-session |
| src/share/gen_errno.pl (467) | ob_errno.def, mysql_errno.h | as ARCHITECTURE §2 decides | ob-errno |
| tools/ob_error/src/gen_os_errno.pl (184) | os_errno.def (138) | the OS error table of the ob_error tool | ob-errno, for its `ob_error` binary target (6.2 rule 8) |
| src/share/inner_table/sys_package/syspack_codegen.py (176) | 26 .sql files | none: a hand-written 13-entry table with `include_str!` of the unchanged .sql files | pl |
| gen_str_datum_func_parts.py (165), gen_expr_str_cmp_func.py (325) | collation lists | none; they only split C++ template instances across object files | — |
| the IK dictionary, ob_ik_dic.cpp | three word lists | three text files in today's order, read with `include_str!`; the lazy cache stays | storage-search |
| src/sql/parser/gen_type_name.sh (18), run by gen_parser.sh:100 | the unchanged ob_item_type.h | `get_type_name`, a `match` from `ObItemType` to the `"T_..."` text, built with the script's own `sed` rule (`(T_[A-Z0-9_]+)[ =0-9]*,`), so an enumerator the rule does not match returns `"Unknown"` as today; 82 C++ lines call it, among them a user error (src/pl/ob_pl_resolver.cpp:813). Its C output, type_name.c, which the kept C core calls (src/sql/parser/parse_node.c), is copied from the reference build (ARCHITECTURE §9.2) | sql-parse-tree |

The line counts come from `wc -l` at 834bbee1e.

### 6.2 Rules

1. **Inputs stay byte-identical;** Decisions 2 and 3 freeze them anyway.
2. **The Rust back end sits beside the script, not in it.** RULEBOOK section 0 forbids editing a file under src/, so each back end is a Python module in migration/scripts/gen/ that imports the unchanged script (generate_inner_table_schema.py and gen_ob_sys_variables.py define their parsing in functions, for example `parse_args` at generate_inner_table_schema.py:146) or, where the script runs only as a program, re-reads the same input the same way; both outputs then come from the same parse of the same input. The unchanged script's C++ output must stay byte-identical to the reference build's generated/ directory (checked by `diff -r`), which also shows the script was not touched. syspack_codegen.py gets no back end: the pl crate's 13-entry table is hand-written (6.1), and a gate compares its entries with the script's list of .sql files.
3. **Output is checked in** under rust/<crate>/src/generated/, one directory per crate (RULEBOOK section 4), with a banner naming the script, the command and each input's SHA-256. `cargo build` never runs Python or Perl (Decision 7). A gate regenerates and runs `git diff --exit-code` (default 5).
4. **Plain Rust only:** `pub const` and `pub static` data and ordinary functions; no `static mut` (Decision 14), no `build.rs`, no procedural macros, nothing nightly (Decision 16).
5. **Names stay C++:** the 333 C++ constant names the definition file spells, and every generated name, keep their spelling (ARCHITECTURE §14 rule 3). The three C++ expressions in the definition file (`ObCharset::get_default_charset()` and two others, ob_inner_table_schema_def.py:287-288, :426) become enum values the builder evaluates. Outputs that are empty at 834bbee1e (the cluster-private switch, `cluster_distributed_vtables`, `restrict_access_virtual_tables`) are dropped.
6. **No generated per-variable types:** the 720 generated C++ classes (R11 §3.3) become rows of the metadata table plus hook slots. The mutable current defaults live in the server context, filled at start from the table, the environment and `--variable` (5.2 rule 4).
7. **Generated files are not translation units.** The manifest excludes them (PLAN §6, Step 3, "Excluded"), and no implementer edits them.
8. **The ob_error tool** that family 6 runs (tools/ob_error, 1,365 lines, built through CMakeLists.txt:316) becomes ob-errno's binary target `ob_error` (ARCHITECTURE §1.1). The tool includes only share/ob_errno.h and its own os_errno.h (tools/ob_error/src/ob_error.h:20-21), so it needs no engine crate; it reads the error catalog and the OS table above from ob-errno.

### 6.3 Worked example: one inner table

C++, generated at build_release/generated/share/inner_table/ob_inner_table_schema.1_50.cpp:31-140, condensed:

```cpp
int ObInnerTableSchema::all_core_table_schema(ObTableSchema &table_schema)
{
  uint64_t column_id = OB_APP_MIN_COLUMN_ID - 1;
  table_schema.set_database_id(OB_SYS_DATABASE_ID);
  table_schema.set_table_id(OB_ALL_CORE_TABLE_TID);
  table_schema.set_rowkey_column_num(3);
  table_schema.set_table_type(SYSTEM_TABLE);
  table_schema.set_charset_type(ObCharset::get_default_charset());
  ADD_COLUMN_SCHEMA_TS_T("gmt_create", ++column_id, 0, 0, 0, ObTimestampType, CS_TYPE_BINARY,
      0, -1, 6, true, false, false, gmt_create_default_null, gmt_create_default)
  ADD_COLUMN_SCHEMA("table_name", ++column_id, 1, 0, 0, ObVarcharType, CS_TYPE_INVALID,
      OB_MAX_CORE_TALBE_NAME_LENGTH, -1, -1, false, false);
  ...
}
```

Rust, rust/ob-schema/src/generated/ob_inner_table_schema_1_50.rs, written by the back end:

```rust
pub static ALL_CORE_TABLE_DEF: InnerTableDef = InnerTableDef {
    database_id: OB_SYS_DATABASE_ID,
    table_id: OB_ALL_CORE_TABLE_TID,
    table_name: OB_ALL_CORE_TABLE_TNAME,
    table_type: ObTableType::SYSTEM_TABLE,
    rowkey_column_num: 3,
    charset: CharsetExpr::DefaultCharset,
    collation: CollationExpr::DefaultOfDefaultCharset,
    compress_func_name: OB_DEFAULT_COMPRESS_FUNC_NAME,
    columns: &[
        ColumnDef { name: "gmt_create", column_id: 16, rowkey_position: 0, column_type: ObObjType::ObTimestampType,
                    collation_type: CS_TYPE_BINARY, length: 0, precision: -1, scale: 6, nullable: true,
                    on_update_current_timestamp: false, default: ColumnDefault::Now, orig_default: ColumnDefault::Null,
                    ..ColumnDef::NONE },
        ColumnDef { name: "table_name", column_id: 18, rowkey_position: 1, column_type: ObObjType::ObVarcharType,
                    collation_type: CS_TYPE_INVALID, length: OB_MAX_CORE_TALBE_NAME_LENGTH, precision: -1,
                    scale: -1, nullable: false, ..ColumnDef::NONE },
    ],
    ..InnerTableDef::NONE
};
```

The script writes the column ids it computes from `OB_APP_MIN_COLUMN_ID` = 16 (src/oblib/lib/ob_define.h:595), so the data can be read and diffed, and the builder assigns nothing on its own. `InnerTableDef`, `ColumnDef`, `CharsetExpr` and `ColumnDefault` are new types with no single C++ counterpart, so they take plain names (ARCHITECTURE §14 rule 3).

### 6.4 What a reviewer checks

- Regenerating from the unchanged inputs gives the checked-in Rust files and the reference's C++ files, byte for byte.
- No generated file is edited by hand, and no hand-written file copies generated data.
- `cargo build --offline --locked` succeeds on a machine without Python.
- The unit test of 5.4 passes for all 640 schemas.
- `ob_error <code>` prints the same text from both builds for every one of the 1,546 codes (R11 §9.4, item 3).

## 7. Checks before and during the core build

### 7.1 Three sensitivity mutations in 00b, before any Rust code (ARCHITECTURE §6.1)

They run as PLAN §4's "How the injected mutations run" describes, numbered after the last patch in migration/judge/mutations/, on the 272 cases and on the 40 plan-bearing cases with the EST mask on and off. They measure; the output is the list of lines that move.
1. **Double the block counts:** in `ObLSTabletService::inner_estimate_block_count_and_row_count` (ob_ls_tablet_service.cpp:4822-4824), add twice each data macro and micro block count. Moved EST.TIME lines are the expected size of the documented difference. A moved plan shape or row order names a place where option (a) would be needed.
2. **Stop the memtable walk at 50 rows:** in `ObQueryEngine::sample_rows` (ob_query_engine.cpp:330), compare with 50 instead of `MAX_SAMPLE_ROW_COUNT`. The mutation stays in the .cpp, as the mutations README requires. Moved lines depend on memtable estimates above 50 rows per range.
3. **Cut micro blocks at 4 rows:** in `ObMicroBlockAdaptiveSplitter::check_need_split` (ob_macro_block_writer.cpp:276-306), split once the block holds 4 rows, whatever its size. Moved lines depend on the rows-per-micro-block cut, through block counts or through the batch ends of block scan. Each one is an amendment request that makes the cut rule kept for that path. The 16 fork_table cases are read with this mutation in place like the rest, since FORK TABLE freezes their source tablets and they read sstable data (2.2 rule 8).

### 7.2 Differential tests in the core build (ARCHITECTURE §14 rule 9: vectors recorded from the reference)

- **The estimator:** an `EXPLAIN EXTENDED` corpus over tables in known states, recording the unmasked `physical_range_rows` and `logical_range_rows` lines of the Optimization Info block. The states: inserts only; deletes; an open transaction; after a minor freeze; after a major freeze; ranges of 499, 500 and 5,000 rows.
- **The rowkey compare:** the Rust compare against both C++ compares of 3.1 rule 2, for every rowkey type and collation the value library supports.
- **The memtable B-tree:** for recorded insert sequences, the leaf sizes and `estimate_element_count` results.
- **The data-version file:** 4.2 rule 2's bytes, and the C++ file refused.

## 8. Defaults and open questions

This section relies on these defaults of ARCHITECTURE §17: 1 (the core-build exit list), 2 (telemetry dropped), 5 (generated code checked in and diffed), 14 (estimator option (b), EST.TIME differences documented at the final gate), 15 (zstd 1.3.8 for table blocks), 24 (data version 2.0.0.0, meta.db at store/meta/meta.db), 25 (catalog access through inner SQL, as the C++ does) and 39 (the export and import).

Questions it adds for the developer, carried into ARCHITECTURE §17's list of questions:
1. **The advance-checkpoint flush in the init profiles.** Should the profiles the C++-against-Rust runs use set `_advance_checkpoint_interval = 0m`? Neither profile sets it today, nor disables the statistics jobs or the 02:00 major freeze (ARCHITECTURE §17, question 3). Section 9, objection 2, and 2.2 rule 8 give the reasoning. The reduced init changes only by a recorded amendment and a new C++ recording (PLAN §4, item 2); the full init is tools/deploy/init.sql, which PLAN §4 keeps identical to 834bbee1e, so the 272 cases would need a copy outside tools/deploy with its own recording. A C++-against-C++ run with and without the setting shows whether any recorded line depends on it.
2. **The memstore trigger and the clog disk usage flush** fire at other data volumes in Rust, because budgets trip by the same formulas, not the same bytes (ARCHITECTURE §3.2, default 9), and the WAL format is free. Assumption: no configured case reaches 20% of the memstore limit or 30% of the log disk. The check: keep every rotated server log of one full reference run and grep it for `freeze_source="FREEZE_TRIGGER"`, the memstore trigger (ob_memstore_freezer.cpp:631; src/storage/ls/ob_freezer_define.h:36-48), and for a `start flush` line whose `recycle_scn` is below the maximum, the clog disk usage path (ob_checkpoint_executor.cpp:282).

## 9. Objections to ARCHITECTURE.md, and how each was settled

Each objection is followed by its settlement, recorded in RESOLUTIONS.md as s6-N.

1. **Wrong line numbers in §6.1 and §6.2.** §6.1 places the 1,000x rule at src/storage/access/ob_index_sstable_estimator.cpp:96-113; those lines are `estimate_row_count` and `estimate_block_count`. The test is at :258-274 and the constant at ob_index_sstable_estimator.h:135. §6.2 cites the 10,000 cache at ob_tablet_autoincrement_param.h:32; it is at :31.

   **Settled:** the 1,000x citation is fixed in ARCHITECTURE §6.1. The second claim was wrong: `DEFAULT_TABLET_INCREMENT_CACHE_SIZE = 10000` is at :32 (`grep -n DEFAULT_TABLET_INCREMENT_CACHE_SIZE src/share/ob_tablet_autoincrement_param.h`; :31 is `DEFAULT_HANDLE_CACHE_SIZE = 10`), so ARCHITECTURE keeps :32 and 3.1 rule 3 above now cites :32.
2. **§6.1's freeze list and §16 miss two live triggers.**
   - The advance-checkpoint flush freezes every memtable 10 minutes after start and every 10 minutes after that (ob_checkpoint_service.cpp:40, :56, :200-231; `_advance_checkpoint_interval` 10m, ob_parameter_seed.ipp:1162-1165). By its timer it fires during a run (2.2 rule 8; the pass-A log line this objection first cited as a flush shows only the interval's start, REVIEW-1.md B21), since a full run of the 272 cases takes about 23 minutes (1,409 s, migration/judge/validation-834bbee1e.md:13). So every table alive at those moments moves into a mini sstable at a time that differs between runs. With §6.1's free block layout, that is where differences in block counts and in block-scan batch ends can reach the plain-SQL and plan-bearing cases, not only the 9 that freeze explicitly (bulk_insert, sfu_norow_alias, array.array_arith_op_mysql, three fork_table cases, histogram.stats_farm, two vector_calc cases; found by grepping the configured .test files for freeze statements and includes).
   - The clog disk usage flush (ob_log_service.cpp:538-557) compares WAL bytes, which Decision 11 frees. So §6.1's "no flush earlier than the C++" cannot hold for it, nor for the memstore trigger under default 9.

   This section adds both to the kept triggers (2.2 rule 8) and asks the developer about the first (section 8, question 1).

   **Settled:** accepted. ARCHITECTURE §6.1 lists both triggers and says "no flush earlier" holds for the explicit and timed ones, and for the byte-driven ones only under the assumption and the Step 2a measurement; §16 and §17 carry the question.
3. **§6.1 frees the rows-per-micro-block cut, while §6.2 keeps batches ending at the same storage events.** For sstable data in block scan, a batch ends where a micro block's rows end (ob_multiple_merge.cpp:605-611; ob_vector_store.cpp:338-340). R05 §1.8 shows the hash join's output order follows probe batches, and that order is unmasked. The two sections agree only if "same storage events" means the same kinds of events. This section reads it that way (3.1 rule 5), and 7.1's mutation 3 measures the effect. §6.2 should say so.

   **Settled:** accepted. ARCHITECTURE §6.2 now says "the same kinds of storage events" and names them.
4. **§12 item 2's missing-file rule can pass a C++ directory.** It checks the block file and the clog where `check_need_initialize` looks, which depends on `data_dir` and `redo_dir`. Those are READONLY parameters that `--data-dir` and `--redo-dir` set and meta.db keeps (ob_parameter_seed.ipp:26-29; ob_server.cpp:1782-1830), and meta.db opens only after the gate. A C++ directory whose data or redo dir lies elsewhere and whose version file was lost passes the three checks. Every C++ start creates ./store/sstable/meta.db first (ob_server.cpp:1698-1716), so 4.2 rule 5 adds store/sstable/meta.db and store/meta/meta.db to the rule.

   **Settled:** accepted. ARCHITECTURE §12 item 2 now has both conditions.
5. **§12 item 5 cannot print to the terminal from inside the engine.** The C++ becomes a daemon before `ObServer::init` (main.cpp:733-741 against :798), so in daemon mode a refusal made inside the engine reaches only the log. §7.3 gives `fork` to the seekdb binary without saying the gate must run before it. 4.2 rule 3 runs the gate in seekdb before `fork` and again in `Server::open`.

   **Settled:** accepted. ARCHITECTURE §7.3 and §12 item 5 now run the check before `fork` and again in `Server::open`.
6. **§1.1 has no crate for the ob_error tool** that family 6 runs (tools/ob_error, 1,365 lines, CMakeLists.txt:316), although §2 has the errno generator write "the ob_error tool's data". 6.2 rule 8 makes it a binary target of the seekdb crate.

   **Settled:** the gap is real; the crate differs. The tool includes only share/ob_errno.h and its own os_errno.h (tools/ob_error/src/ob_error.h:20-21), so it becomes ob-errno's binary target, as s1-crates-core.md 5.3 rule 9 and s2-errors.md 2.8 already had it, and needs no engine crate; building it from the seekdb crate would compile the whole engine for it. ARCHITECTURE §1.1 and §12 and 6.2 rule 8 above now say so.
7. **§6.1's "exact up to MAX_SAMPLE_ROW_COUNT = 500 rows from each end of a range" overstates the rule.** The result is exact only for a range of fewer than 500 rows. From 500 rows on, the physical count adds the B-tree element estimate, which halves a batch whose last key passes the range end (ob_keybtree.cpp:1063-1068). That is also why the B-tree's shape must be kept.

   **Settled:** accepted. ARCHITECTURE §6.1 now says exact for a range of fewer than 500 rows, and names the half-batch rule.

## 10. The storage and transaction core: what this document fixes, what recovery must reproduce, and what the core API sessions design

The storage and transaction core (storage/{tablet,meta_mem,memtable,tx,multi_data_source,tx_table} and data_plane/api/data_plane/access, 133,109 lines) is part of the core, and the single-writer WAL (logservice, 32,705 lines) and crash recovery (storage/{slog,slog_ckpt,checkpoint,meta_store}, 14,058 lines) join it in the core build (s1-crates-core.md 2.1). Sections 2 and 3 fix what the judge sees of it. This section fixes the rest that PLAN §3's outline and Decisions 9 and 11 decide, says what recovery must reproduce, and lists what the core API sessions design. s1-crates-core.md 2.1 rule 2 and s8-numerics-platform.md section 5 point here.

### 10.1 Rules fixed now

1. **Tablets** are immutable snapshots of a tablet's table store, shared as `Arc`: a change builds a new snapshot, and a scan holds the snapshot's `Arc`, so the tables it reads, memtables included, live until it ends (s3-memory.md M5).
2. **The transaction context** is an `Arc<ObPartTransCtx>`, replacing `acquire_ctx_ref`, `revert_tx_ctx` and their counts (s3-memory.md R1). Its callback lists keep the C++ order, by sequence number (`ObTxSEQ`), as `ObTxCallbackList` keeps them (src/storage/memtable/mvcc/ob_tx_callback_list.h:31-80), and callbacks complete in that order.
3. **Multi-source data (MDS)** is an enum with one variant per data kind (`ObTxDataSourceType`, src/storage/multi_data_source/runtime_utility/mds_factory.h:34) plus a trait for the operations the kinds share, in place of the C++ class templates of src/storage/multi_data_source (48 files).
4. **Reclamation:** locks first, arc-swap for published snapshots, and ob-epoch only where Step 2a's or a later gate's measurement shows a structure needs lock-free reads (ARCHITECTURE §11; s8-numerics-platform.md section 5).
5. **`SCN`, `ObTxSEQ` and LSN** are `Copy` types with atomic wrappers keeping the C++ member names (s8-numerics-platform.md rule 4.6).
6. **Formats are free** (Decision 11): the WAL, checkpoint, slog, sstable and tablet-meta formats are new explicit little-endian encodings (RULEBOOK section 1, "Serialization"), and xxhash-rust or crc32c checksum them.
7. **One log stream** folds into `StorageContext`'s top storage object; no code asks for a log stream by id (s1-crates-core.md 2.1 rule 2).

### 10.2 What crash recovery must reproduce

Under Decision 9 every stop is a kill, every restart runs recovery, and recovery must be exact from the first Rust build that restarts (PLAN §4, "How the server stops"). Family 9 and the restart scenarios (migration/judge/harness/restart-scenarios.md: restart_data, restart_parameters, restart_mid_dml) check it, and family 15 checks the start on an empty or foreign directory (section 4). After a kill the restarted server shows exactly what the C++ shows:
1. **Every transaction whose commit the client saw, and none other.** A transaction's redo can reach the log before its commit once its pending redo passes `_private_buffer_size` (16 KB, src/share/parameter/ob_parameter_seed.ipp:651), so the scenarios leave redo in the log with a rollback after it, and with no end at all (a second session holds the transaction open at the kill); recovery applies neither. The log write that makes a commit survive a process kill comes before the commit's reply to the client, as palf's does; which write and which sync, the core API sessions fix.
2. **Schema, indexes, users, grants and parameters** as they were: `SHOW CREATE TABLE`, the index-hinted and full-scan reads and `SHOW GRANTS` of the scenarios' select sets, and `ALTER SYSTEM` parameters, which meta.db keeps (restart_parameters).
3. **Sequences** continuing from what the C++ persisted at the same points: the hidden `__pk_increment` from the persisted interval end (3.1 rule 3; src/storage/ob_tablet_autoinc_seq_service.cpp:162-200), and AUTO_INCREMENT columns from their persisted values, so rows inserted after a restart get the C++'s values and, for a table without a primary key, the C++'s scan order.
4. **The same state after a restart with no writes between** (restart 2 of restart_data), and after writes that follow a recovery (restart 3).
5. **The bootstrap commit point** of 5.2 rule 1: a start that finds storage without a committed bootstrap refuses with the C++ messages.

### 10.3 What the core API sessions design before Step 2a's stubs

Step 2a's disposable full pass stubs the whole core API (PLAN §6, Step 2a item 2), so these are fixed before the stubs are written, each recorded as an amendment to this section with its evidence: the tablet and table-store API; memtable MVCC (row nodes, row locks, visibility, over the `ObKeyBtree` of 2.2 rule 2); the transaction context's states and the callback API; the MDS enum and trait; the WAL (the single writer, group commit, the write and sync before a commit reply); checkpoints and replay (the server slog, tablet checkpoints, clog replay and its `OB_EAGAIN` retries, s3-memory.md 3.7.2); and ob-epoch's API, if a structure needs it. Step 2a's narrow path then uses a part of them, memtables and autocommit transactions without a WAL (ARCHITECTURE §15); the WAL, checkpoints and replay are built in the core build.
