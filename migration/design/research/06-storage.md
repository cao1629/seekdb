# Research 06: storage, as it affects the design

Step 1 research for the design document. The question: which storage behavior the judge can see, and therefore what the Rust storage must keep identical and what it may change. Four parts: the storage inputs behind EST.ROWS and EST.TIME, the scan order behind the unordered single-table SELECTs, the data-version bump with its three fixes (Decision 11), and the memtable/sstable/tablet structure.

Source: C++ at 834bbee1e (`git diff --quiet 834bbee1e -- src` succeeds in /Users/colin/seekdb-dev/migrate-to-rust). Paths are from the repository root. Scratch scripts and their outputs are in migration/design/evidence/: `single_table_rows2.py` (a copy of the census script embedded in migration/judge/lists/hash-order-select-candidates.txt, extended to find each statement's echo in its .result and count the rows printed after it) with its output `single_table_rows2.tsv`, and `est_rows.tsv` (the EST.ROWS column of every table-access row in the 40 plan-bearing .result files).

## Summary

- Storage reaches plan text through two small interfaces with four methods. For the data the tests use, what storage returns are exact row counts under fixed rules, not estimates that depend on block layout: in the 40 plan-bearing files, all 768 table-access rows that carry EST.ROWS show 100 or less. Block counts reach EST.TIME only through gathered statistics or dynamic sampling, and only when the data sits in sstables.
- PLAN §8 item 17 (keep the estimator inputs identical?): recommended answer is option (b) of section 3.1 (section 4.3). Keep the row-count rules, the definitions of the per-tablet block and row counts, the memtable B-tree's shape rules and the freeze policy. Do not promise identical block layout, compression or encoding. Measure the effect with two C++ sensitivity mutations in 00b.
- The EST mask as Decision 6 declares it does not cover all estimator output. view_2.result:473-498 prints `table_rows`, `physical_range_rows`, `logical_range_rows`, `index_back_rows`, `output_rows` and `estimation method` for two tables. Those lines are compared exactly whatever the design does.
- PLAN §8 item 18 (scan order): the order comes from four rules, section 1.3. The most important is that tables without a primary key are ordered by the hidden `__pk_increment`, a per-tablet sequence in insert order. By the census, about 7,200 single-table SELECTs without ORDER BY print two or more rows. About 6,100 of them read a table without a primary key, and 5,740 of those are the four arithmetic matrices (132 rows each).
- Decision 11: the recommended concrete rules are in section 4.5. They match research 11 (bootstrap and data) on where the gate sits and on the missing-file rule.
- Corrections found: the micro block cache's "3 retries" are dead code at 834bbee1e; under the reduced init the 15-minute statistics job is live; the daily major freeze at 02:00 is live under both inits; the vault notes' `g_mp` no longer exists.

## 1. What the C++ does today

### 1.1 Structure: tablet, memtable, sstable

The vault notes (/Users/colin/obsidian/tech/seekdb/storage/) were written against 073e9b2f1. Since then src/storage changed in 1,013 files, +33,110/−56,788 lines (`git diff --stat 073e9b2f1 834bbee1e -- src/storage`). One structural claim in them no longer holds: `share::g_mp` has 0 hits at 834bbee1e. Services are reached through `share::server_service<T>()` instead (1,243 lines; `grep -rn 'server_service<' src | wc -l`). Every fact below was re-checked at 834bbee1e.

| Piece | At 834bbee1e | Evidence |
|---|---|---|
| Tablet | `ObTablet` is a copy-on-write version object. A change builds a new one and swaps it into `ObTabletPointerMap` with `compare_and_swap_tablet` | ob_storage_meta_mem_mgr.h:272-278; ob_tablet_pointer_map.h:44 |
| Tablet memory | Deserialized in place into a pool buffer of 3,824 B or 65,480 B, with sub-objects copied behind the object. Cold tablets are washed, and the candidate is chosen by `std::type_info` | ob_storage_meta_mem_mgr.h:128-129; ob_tablet.cpp:1664-1747; ob_storage_meta_mem_mgr.cpp:2061, 2193 |
| Table store | Per tablet: arrays of major, minor, ddl, meta-major and mds sstables, of memtables, and of ddl kvs | ob_tablet_table_store.h:329-335 |
| Memtable | Up to `MAX_MEMSTORE_CNT` = 16 per tablet. `ObMemtable` → `ObQueryEngine` → `ObKeyBtree` (15 keys per node) mapping rowkey → `ObMvccRow`. Each row has a newest-first chain of `ObMvccTransNode` (`list_head_`) and first/last DML flags | ob_define.h:1522; ob_keybtree_deps.h:47; ob_mvcc_row.h:280-293 |
| Table types | Memtables 0-3. MAJOR 10, MINOR 11, MINI 12, META_MAJOR 13, DDL 14-16, MDS 17-18, MICRO_MINI 19, INC_MAJOR 20-22 | ob_i_table.h:85-112 |
| Sstable | Macro blocks of `OB_DEFAULT_MACRO_BLOCK_SIZE` = 2 MB. Micro blocks are cut at the table's `block_size` (default 16 KB, minimum 4 KB), with an index tree over them. The sstable meta keeps `row_count_`, `data_macro_block_count_` and `data_micro_block_count_` | ob_define.h:1474-1475; ob_data_store_desc.cpp:496; ob_data_store_desc.h:262; ob_sstable_meta.h:153-161 |
| Row format | Mini and minor sstables use `FLAT_ROW_STORE`. Majors follow ROW_FORMAT, and DYNAMIC (the default) means `ENCODING_ROW_STORE` | ob_data_store_desc.cpp:441-462; ob_store_format.cpp:37-41 |
| Compression | The table default is `zstd_1.3.8`, which is the vendored zstd 1.3.8 | ob_parameter_seed.ipp:143; src/oblib/lib/compress/zstd_1_3_8/zstd_src/zstd.h:72-74 |
| Reading a tablet | `ObMultipleScanMerge` merges every table of the tablet with a loser tree ordered by (range index, rowkey). Versions of one rowkey fuse into one row | ob_scan_merge_loser_tree.cpp:72-93; ob_multiple_merge.h:137 |
| Metadata store | store/sstable/meta.db, SQLite (`share::ObSQLiteConnectionPool`) | ob_server.h:450; ob_server.cpp:1698-1714 |

### 1.2 How storage reaches EST.ROWS and EST.TIME

**The interfaces.** The optimizer reaches storage through two interfaces with four methods:
- `data_plane::ObIStorageEstimator` (src/data_plane/api/data_plane/ob_i_storage_estimator.h:39-60) has two methods. `estimate_row_count_for_batch` gives the logical and physical row count of a set of ranges. `estimate_block_count_and_row_count` gives, per tablet, the macro block count, the micro block count, the sstable row count and the memtable row count. `ObAccessService` implements both (ob_access_service.cpp:1146-1210) and forwards to `ObLSTabletService` (ob_ls_tablet_service.cpp:4715-4860).
- `data_plane::ObIOptimizerStorageService` (ob_i_optimizer_storage_service.h:35-48) has two methods: `get_latest_tablet_row_count_delta` and `run_io_benchmark`.

**Which method is used.** Each base table gets a set of methods (`EST_DEFAULT`, `EST_STAT`, `EST_STORAGE`, `EST_DS_BASIC`, `EST_DS_FULL`; ob_opt_est_cost_model.h:40-48). `get_valid_est_methods` narrows them: no storage for inner paths, virtual tables or locked statistics (ob_access_path_estimation.cpp:174-244). `choose_best_est_method` then picks from one of three priority lists (:331-336). Dynamic sampling (DS) is on at level 1 by default (`optimizer_dynamic_sampling`, ob_system_variable_init.json:2920-2923).

**The inputs storage provides:**

| Input | How storage computes it | Evidence |
|---|---|---|
| Row count of a range in a memtable | Walks the B-tree from each end of the range, up to `MAX_SAMPLE_ROW_COUNT` = 500 rows. Logical rows are counted by five rules on the row's first and last DML flags; physical rows are all B-tree entries. Above 500 rows it scales by a B-tree element-count estimate (`MAX_SAMPLE_LEAF_COUNT` = 500; level weights from `NODE_KEY_COUNT` = 15). **Exact below 500 rows per range** | ob_query_engine.h:134; ob_query_engine.cpp:267-340, 561-633; ob_keybtree.cpp:953-994; ob_keybtree_deps.h:161-179 |
| Row count of a range in an sstable | Sums the row counts of the index rows, subtracts the rows outside the range, and at each border descends to the data micro block and counts its rows. It skips that descent on the right border of a major sstable when the rows in range are at least 1,000 times the border block's rows. **Exact for small tables** | ob_index_sstable_estimator.cpp:96-113, 129-286; ob_index_sstable_estimator.h:135; ob_micro_block_row_scanner.cpp:1036-1074 |
| Combining tables | Sums per table, repairs negative logical counts, then applies: at least 1, logical ≤ physical. A single-rowkey range counts 1; a get counts its keys. A memtable result with logical ≤ 0 and physical > 1,024 is split into 3 sub-ranges and estimated again | ob_table_estimator.cpp:28-95, 209-220; ob_index_sstable_estimator.h:35 |
| Which tables count | Tables read at the latest readable snapshot. Minor sstables at or below the major version are skipped | ob_ls_tablet_service.cpp:4715-4784 |
| Per-tablet counts | Sum over sstables of data macro count, data micro count and row count. The memtable row count is the B-tree size | ob_ls_tablet_service.cpp:4786-4860; ob_memtable.h:308 |
| Row delta since the last statistics | From `ObTabletStatMgr`; used to decide whether cached statistics are stale | ob_access_service.cpp:1212-1235; ob_opt_stat_service.cpp:351-368 |
| IO benchmark | Only for DBMS_STATS system statistics. No configured case gathers them (`grep -rliE 'gather_system_stats\|set_system_stats'` over tools/deploy/mysql_test: 0 files), so the defaults 2500/1024/512 apply | ob_optimizer.cpp:1181-1222; ob_opt_cost_model_parameter.h:20-22 |

**Block sizes and rows per block.** These are the inputs Decision 6's notes name.

| Item | Value | Evidence | Where it reaches the plan |
|---|---|---|---|
| Macro block | 2 MB | ob_define.h:1474; ob_server.cpp:2021 | Only through block counts |
| Micro block target | The table's `block_size`, default 16,384 | ob_define.h:1475; ob_table_schema.cpp:2539; ob_ddl_resolver.cpp:45 | `micro_block_size_` in the table meta (ob_join_order.cpp:13148). Its only use in a formula is the PX task count for NLJ subplan filters (ob_optimizer_util.cpp:8011-8012) |
| Micro block minimum and maximum | 4 KB and 256 KB | ob_data_store_desc.h:262; ob_imicro_block_writer.h:269 | No |
| Rows per micro block | The writer cuts a micro block at `block_size`. But the adaptive splitter, always on, keeps appending while its estimate of the zstd-compressed size (from past ratios) stays under `block_size`. Target 16 rows, minimum 3 | ob_macro_block_writer.cpp:276-306, 644-645; ob_macro_block_writer.h:131-132 | Through sstable micro block counts |
| Rows per macro block | Filled up to 2 MB × (100 − pctfree)%, minus the headers | ob_data_store_desc.cpp:75-88 | Through counts |
| The optimizer's own defaults | `DEFAULT_MICRO_BLOCK_SIZE` 16 KB and `DEFAULT_MACRO_BLOCK_SIZE` 2 MB. No reference outside the header (`grep -rnw`) | src/query/api/query/optimizer/ob_opt_default_stat.h:36-40 | No |

**When block counts reach EST.TIME.** `range_scan_io_cost` and `range_get_io_cost` use `get_micro_block_numbers()`, which returns 0 when `micro_block_count_ ≤ 0` (ob_opt_est_cost_model.cpp:118-129, 1436-1530). `micro_block_count_` starts at −1 (ob_opt_est_cost_model.h:66). It is set only from gathered statistics (ob_join_order.cpp:13578) or from DS (ob_access_path_estimation.cpp:1944, 2037-2041). Without statistics, the index's count is −1 too (ob_join_order.cpp:13200-13201, 13236-13239). DS reads the whole table when sstable plus memtable rows are at most `MAGIC_MAX_AUTO_SAMPLE_SIZE` = 22,000 (ob_dynamic_sampling.cpp:756-757; ob_stat_define.h:72), and it takes its micro block number from storage's sstable counts (ob_dynamic_sampling.cpp:823-850). So a table whose data is all in memtables contributes 0 micro blocks. Block layout reaches EST.TIME only for a table that has data in sstables and also has statistics or was sampled.

One more input comes from the SQL tier. DBMS_STATS's average column length is `sizeof(ObDatum)` plus the data length (ob_expr_sys_op_opnsize.cpp:73, 79), and ObDatum is 12 bytes (evidence-full.md:47). The Rust code must keep the literal 12.

**Freeze thresholds.** These decide when data leaves the memtable.

| Parameter or constant | Default | Evidence | Set by init.sql |
|---|---|---|---|
| `freeze_trigger_percentage` | 20; the trigger is the memstore limit / 100 × 20 | ob_parameter_seed.ipp:357; ob_memstore_freezer.cpp:1049-1055, 1148-1155 | — |
| `memstore_memory_limit` | 0M, meaning derived from the memory budget | ob_parameter_seed.ipp:92; ob_server_config.cpp:218-222 | — |
| `writing_throttling_trigger_percentage` | 60 | ob_parameter_seed.ipp:360 | — |
| `_ob_enable_fast_freeze` | True. Checked only once a memtable is 300 s old. It fires on a hot row, on updates plus deletes ≥ 250,000 (adaptive), or on empty rows ≥ 1,000 that are also ≥ 50% of the memtable | ob_parameter_seed.ipp:679; ob_tablet_scheduler.h:81-87; ob_tablet_scheduler.cpp:73-160 | — |
| `minor_compact_trigger` | 2 | ob_parameter_seed.ipp:641 | — |
| `major_freeze_duty_time`, `enable_major_freeze` | 02:00, True | ob_parameter_seed.ipp:535, 459 | — |
| `ob_compaction_schedule_interval` | 120 s | ob_parameter_seed.ipp:672 | 10 s (init.sql:18) |
| `merger_check_interval` | 10 min | ob_parameter_seed.ipp:538 | 10 s (init.sql:19) |
| `_enable_adaptive_compaction` | True | ob_parameter_seed.ipp:617 | False (init.sql:20) |

**What the corpus exercises.**
- The 40 plan-bearing files hold 568 plan tables with EST columns, in 33 files (the plan-bearing.txt header; recounted with `grep -a -c '|ID|OPERATOR.*EST\.ROWS'`: 568).
- 768 table-access rows carry an EST.ROWS value. The largest is 100, and 669 are 10 or less (est_rows.tsv).
- There are 351 `PX COORDINATOR` lines. `dop=` is 1 on 503 lines and 3 on one line (a `count(*)`, geometry_partition_table_mysql.result:271).
- Among the 40, only array.array_arith_op_mysql freezes. Four gather statistics: fts_index.simple_query, geometry.geometry_bugfix_mysql, subquery.subquery_sj_firstmatch and vector_index.sparse_vector_index_vsag_query (grep for `dbms_stats|analyze table` over the .test files).
- Across all 272 cases, 9 freeze: 8 with `ALTER SYSTEM ... FREEZE` in the .test, and sfu_norow_alias through include/majorfreeze.inc. The report's "8" missed that include. 10 more cases source index_quick_major.inc, which only sets `merger_check_interval`.
- The same 40 cases recorded on two different builds gave identical text (judge/recordings.tsv, a9-rec-noflag against a9-rec-flag). So the C++ estimates are stable from run to run here.
- **Estimator output outside the mask:** view_2.result:473-498 prints an "Optimization Info" block for two tables. It shows `table_rows:1`, `physical_range_rows:1`, `logical_range_rows:1`, `index_back_rows:0`, `output_rows:1` and `estimation method:[DYNAMIC SAMPLING FULL]`. view_2 is a configured, plan-bearing case. The mask covers only EST.ROWS and EST.TIME (Decision 6; PLAN §4 "The masks").
- Outside the 40, a proxy for large tables: only 2 whole-table `count(*)` results in the configured .result files exceed 500 rows (vector_index_ivfflat_post_create: 628 and 629).

**Storage state that tests read through SQL.** These are behavioral contracts. If they break, a wait loop times out and echoes a failure line.
- `DBA_OB_MAJOR_COMPACTION.frozen_scn` and `.last_scn` must become equal after a major freeze (include/wait_daily_merge.inc:15-16; test_suite/fork_table/include/wait_daily_merge.inc:14-15).
- `__all_virtual_tablet_memstore_info` must show zero rows with `is_active='NO'` once minor merges finish (include/wait_minor_merge.inc:15).
- `__all_virtual_tablet_compaction_history` must show `type = 'MAJOR_MERGE'` for every tablet of the table (test_suite/fork_table/t/fork_table_merge.test:80-86).

### 1.3 Scan order

1. **Within a tablet**, a forward scan returns rows in rowkey order. The loser tree compares the range index first and then the rowkey, reversed for reverse scans (ob_scan_merge_loser_tree.cpp:72-93). The rowkey comparison uses the column's collation and type compare functions, so these must equal the value library's.
2. **Range order** is set by the SQL tier, and storage scans ranges in the order it is given. Ranges that are not point ranges are sorted by start key (ob_range_generator.cpp:1289-1290). IN values are sorted (:1440-1442). Point ranges are de-duplicated through a hash set but keep their generated order (:1268-1287). Under PX, ranges are sorted again (ob_granule_pump.cpp:843). Spatial cell ranges come from hash-set iteration (ob_range_generator.cpp:1799), and that order shows in plan text: geometry_partition_table_mysql.result:250-252.
3. **Tables without a primary key** get the hidden rowkey `__pk_increment` (ob_create_table_resolver.cpp:109-129; ob_define.h:583, 615; `default_table_organization` = INDEX, ob_parameter_seed.ipp:1119). Its values come from a per-tablet sequence through `T_TABLET_AUTOINC_NEXTVAL` (src/sql/engine/expr/ob_expr_tablet_autoinc_nextval.cpp). The sequence is cached 10,000 values at a time (ob_tablet_autoincrement_param.h:32) and persisted as the tablet's `ObTabletAutoincSeq` MDS unit (ob_tablet_autoincrement_state.h:31). So rows of such tables come back in insert order. UPDATE keeps the hidden key (ob_dml_service.cpp:762, 1806-1829) unless the row moves to another partition, which takes a new value from the new tablet (:970-973).
4. **Partitioned tables.** The tablet list is in schema partition order (ob_table_location.cpp:3552-3579). GI builds its tasks tablet by tablet, with ranges ascending (ob_granule_pump.cpp:205-251). At dop 1 it always uses partition granules (ob_granule_util.cpp:56-70). Tasks are shuffled only for DDL and PDML (ob_granule_pump.cpp:654-686). DAS returns multi-tablet results in task order unless it must keep the sort order (ob_das_merge_iter.cpp:146-159). `parallel_degree_policy` defaults to MANUAL (ob_system_variable_init.json:2873-2881), and no configured case sets it.

What the census finds (single_table_rows2.py over the 272 configured cases, same statement rules as hash-order-select-candidates.txt):

| SELECTs without ORDER BY that read one table | Count |
|---|---|
| Total | 9,042 |
| Echo not found in the .result (mostly `eval` or query log off) | 419 |
| Print 0 rows / 1 row | 408 / 1,013 |
| **Print 2 or more rows** | **7,202** |
| … on a table without a primary key | 6,096, of which 5,740 are in add, datatype.div, datatype.minus and expr.mul (132 rows each) |
| … on a table with a primary key | 794 |
| … on a partitioned table, with / without a primary key | 89 / 12 |
| … on a view / a `LIKE` copy / not created in the case itself | 42 / 2 / 167 |

The row count is the number of lines between the statement's echo and the next statement's first line, minus the header line. It overcounts when output from statements with the query log off follows: about 57 aggregate-only SELECTs are counted as multi-row (`awk` over the .tsv). The table kind comes from the case's own `CREATE TABLE` text (`primary key` present or not, `partition by` present or not). This replaces the report's "about 7,500" for design purposes; that figure was counted over the 283 tracked cases by a different rule.

### 1.4 The data-version gate today

Decision 11, exact text: "(a) No: bump DATA_CURRENT_VERSION and provide a tested logical export and import." Notes: "Plus: move the version check ahead of the meta.db open (ob_server.cpp:1698-1719), close the missing-version-file hole (ob_data_version_mgr.cpp:58-60, 88-94), print a clear message at startup."

- `DATA_CURRENT_VERSION` is `cal_version(1,4,0,0)` (ob_version_def.h:53), the same as `SERVER_CURRENT_VERSION` (:52). 70 lines use it (`grep -rn DATA_CURRENT_VERSION src | wc -l`). Several objects persist it and check it for strict equality (ob_macro_block_meta.cpp:204, 223, 471; ob_tx_table_define.cpp:86; ob_pl_user_type.cpp:422; the `data_format_version` of the rootserver DDL tasks).
- The file is etc/seekdb.data_version.bin: an `ObRecordHeader` with magic 0xBEDE and format 2, then the line `"<version string> <number>\n"` (ob_data_version_mgr.h:65-71, 91-94; ob_data_version_mgr.cpp:114-139).
- Startup order: `init_config` (called at ob_server.cpp:634) creates ./store/sstable, opens meta.db from `getcwd()` and loads the configuration (:1698-1716), and only then loads and checks the version (:1717-1719). `check_need_initialize`, which looks at the block file and the clog directory, runs later (:410-433, called at :667).
- The hole: ENOENT is logged and skipped (ob_data_version_mgr.cpp:88-94). The empty version is then stamped with the current one (:58-60, 163-180). v1.0.0 and v1.0.1 wrote etc/observer.data_version.bin instead (evidence-full.md:3305), so today their directories get stamped 1.4.0.0 and their storage is parsed.
- The message today is a log line only, "persisted data version does not match this binary", with `OB_NOT_SUPPORTED` (:61-65).
- No configured .result contains "1.4.0" (`grep -rn '1\.4\.0' tools/deploy/mysql_test`: 0 lines). So the bump changes no compared text; research 11 finds the same.

## 2. Constraints that bind this topic

- **Decision 6 (b):** exact as the main mode. The masks are EST.ROWS and EST.TIME in the 40 plan-bearing files and row order for about 300 hash-order SELECTs, "never added during Step 6". Its notes: "Keeping EST numbers exact at the end would need the storage estimator's inputs kept identical (a design-document constraint)." PLAN §4, family 4: the SELECTs without ORDER BY are compared exactly outside the declared list. PLAN §8, item 17 (EST inputs) and item 18 (scan order) are settled by the design document. Item 23 prices each false failure at 1-3M tokens of triage.
- **Decision 11 (a)** with the three fixes above. PLAN §3 "Data directories" and family 15: refuse a C++ data dir, refuse a v1.0.x dir holding etc/observer.data_version.bin, bootstrap an empty dir, and export from C++ v1.4.x then import into Rust. The freedom covers files between releases. It does not cover SQL-visible table options (section 3.4).
- **Decision 12 (b):** typed errors stay at the storage budget owners: memstore full, the micro block cache, the KV cache store, the IO allocator, the temp-file write buffer pool, clog and replay. Family 12 drives them.
- **Decision 14 (b):** `unsafe` only in IO buffers, SIMD kernels, wrappers over vetted reclamation crates and island shims.
- **Decision 9 (a):** stops are kills, so every restart runs crash recovery, from the first Rust build that restarts (family 9).
- **Decision 7 and the Decision 8 guideline:** engine crates take the base dir as an absolute path from the server context, with no `chdir`. Today the meta.db path is built from `getcwd()` (ob_server.cpp:1700-1708).
- **Decision 1 (b):** memory use is not a criterion, so the tablet pool buffers and wash exist for a goal the port does not have.
- **Decision 13:** vsag stays C++. Its snapshots cross the boundary through `ObOStreamBuf`/`ObIStreamBuf` (src/query/api/query/vector/ob_vector_index_serialize.h:51, 83).
- **Decision 10 (a), PLAN §6 Step 2a:** the narrow run goes "down to memtable-only storage", and its named statements are diffed exactly. So the memtable row-count rules and the scan order are needed from the narrow run on.
- **PLAN §3 outline:** immutable `Arc` tablet snapshots; epoch reclamation from a vetted crate instead of QClock, the retire station and hazard versions; MDS as an enum plus a trait; new explicit little-endian formats; storage owns the column batch and the filter trait.

## 3. Options

### 3.1 The estimator inputs (PLAN §8 item 17)

| Option | What is kept | Cost | Risk |
|---|---|---|---|
| (a) Keep every input identical, block counts included | The writer's cut rule and the adaptive splitter; flat row and column-encoding sizes byte for byte in length; zstd 1.3.8 output sizes; the 2 MB macro size and header sizes; the index tree; the memtable B-tree; freeze timing | blocksstable (87,627 lines, 25,062 of them in encoding; evidence-full.md:765) becomes an algorithm-for-algorithm translation instead of a redesign. zstd 1.3.8 stays, either as C or as a crate built on that exact version. Decision 11's freedom shrinks to byte order and headers | High. Any size drift anywhere changes micro block counts. Assumption: a newer zstd gives different compressed sizes. The gain is limited to tables that are in sstables *and* have statistics or DS |
| **(b) Keep the row-count rules and the count definitions; free the block layout** | The memtable rules (exact below 500 rows per range, the five DML-flag cases, the scaling above 500); the sstable exact-count rule and the 1,000× border rule; combination and clamps; the per-tablet counts as defined (data micro/macro blocks of sstables, B-tree size of memtables); the tablet row delta; the freeze policy and its parameters | Small. It is the code behind the four interface methods and the two tree walks, and the memtable index keeps the C++ node rules (3.2) | EST.TIME can differ where block counts differ. Plan choices can flip where block counts decide between paths, and a flipped plan changes unmasked plan shape and row order. Section 4.4 measures the exposure before any Rust code |
| (c) Keep nothing and lean on the mask | Nothing | None | Rejected. view_2's Optimization Info lines are unmasked. Plan choices flip on small tables, which changes unmasked plan shape in the 40 files and row order everywhere. The narrow run's plans stop matching |

### 3.2 The memtable index under (b)

| Option | Result |
|---|---|
| A B-tree with the C++ rules: 15 keys per node; leaf splits at the position `update_split_info` returns, inner nodes at the half (ob_keybtree.cpp:160-178, 1485); the same level-weight walk for element counts | Identical estimates above 500 rows as well, given the same insert order. Cost: the rules in about 2,757 lines (ob_keybtree.cpp, .h, _deps.h), written anew on epoch reclamation |
| Any ordered index (a skip list or a B-tree crate) that counts exactly below 500 rows | Identical below 500 rows. Different above: no EST.ROWS value in the 40 files is above 100 today, but plan choices over larger tables elsewhere could flip |

### 3.3 Scan order (PLAN §8 item 18)

| Option | Cost | Risk |
|---|---|---|
| **(a) Keep the C++ rules of section 1.3**: rowkey order with the value library's comparators; the per-tablet hidden-key sequence in insert order, kept across UPDATE; ranges in the order given; tablets in the order given; partition granules at dop 1 | Low. It is how an LSM tree reads anyway, and the comparators are shared with the SQL tier | Hidden-key generation and collation compare are the two places a redesign could drift silently |
| (b) Order tables without a primary key some other way (physical position, one sequence across all tablets, or no order) | — | Rejected. It breaks about 6,100 compared SELECTs, and the four matrices alone print 5,740 × 132 = 757,680 row lines |

### 3.4 Formats, and the table options that are pinned text

`SHOW CREATE TABLE` prints storage options, and those lines are compared exactly. Counted over the .result files of the 272 configured cases (a Python loop over the runner's `discover_cases`):
- `COMPRESSION = 'zstd_1.3.8'`: 171 lines in 33 files.
- `BLOCK_SIZE = 16384`: 172 lines in 33 files.
- `TABLET_SIZE = 134217728`: 172 lines in 32 files.
- `PCTFREE = 0`: 169 lines in 31 files.
- `ORGANIZATION INDEX`: 161 lines in 29 files. `ORGANIZATION HEAP` appears only in create_table_with_hybrid_vector_index, which the config leaves out.

The choice is (a) keep these as schema attributes with the same defaults and printing, independent of what storage does with them, or (b) make them drive storage exactly, in which case 'zstd_1.3.8' must mean zstd 1.3.8 bytes. Decision 11 permits (a).

### 3.5 The version gate (Decision 11)

The decision fixes what to do. What is left: the new value; whether the file keeps its format (magic 0xBEDE, format 2); where meta.db lives; and the exact missing-file rule. If the file keeps its format, the frozen C++ binary reads a Rust file and refuses it through its own equality check (ob_data_version_mgr.cpp:61-65). But it opens and writes meta.db first (ob_server.cpp:1698-1716). So a C++ binary pointed at a Rust directory writes its parameter table into store/sstable/meta.db before it refuses, unless the Rust build keeps its metadata somewhere else.

## 4. Recommendation

### 4.1 Keep identical (judge-visible)

1. **Rowkey order within a tablet**, forward and reverse, with the loser-tree order of (range index, rowkey) and one fused row per rowkey (ob_scan_merge_loser_tree.cpp:72-93). Storage must use the value library's compare functions for every key type and collation. It must not use its own byte order unless that order is proved equal to them.
2. **The hidden `__pk_increment`:** per tablet, increasing in insert order, cached in blocks, persisted with the tablet, kept across UPDATE, and renewed only on a partition move (section 1.3, rule 3). The cache size of 10,000 and the jump after a restart are not visible in tests (hidden column). Keep them anyway; they cost nothing.
3. **Range order and tablet order as given by the SQL tier**, and one partition granule per tablet at dop 1 (section 1.3, rules 2 and 4). No scan-time parallelism that C++ does not have.
4. **The row-count rules** of section 1.2: the memtable's 500-row walk with its five DML-flag cases (ob_query_engine.cpp:296-340); the sstable's border counting with the 1,000× rule; the combination, the clamps, and the 3-way split retry. These drive EST.ROWS, the view_2 Optimization Info lines, the DS sample ratio and plan choice.
5. **The per-tablet counts as defined:** sstable data micro and macro blocks and rows from the sstable meta, and memtable rows as distinct keys ever written, deleted and aborted rows included (ob_memtable.h:308). The values may differ where layout differs; the definitions may not.
6. **The memtable B-tree rules** of section 3.2, first row: 15 keys per node, the same split points, the same element-count walk. Then estimates match above 500 rows too.
7. **The freeze policy:** every parameter name and default in the freeze table, the trigger formula, the fast-freeze conditions and the 300 s age, and the 02:00 daily freeze. The Rust memtable must never flush earlier than C++ would, for example because of a fixed arena cap. Manual `ALTER SYSTEM MAJOR/MINOR FREEZE` must leave data in the same kind of table (mini, minor or major) as C++ does.
8. **The storage state visible through SQL:** `DBA_OB_MAJOR_COMPACTION` (`frozen_scn`, `last_scn`), `__all_virtual_tablet_memstore_info` (`is_active`), and `__all_virtual_tablet_compaction_history` (`tablet_id`, `type='MAJOR_MERGE'`). The fork_table suite's failure path also prints `__all_virtual_server_compaction_event_history` and `__all_virtual_compaction_diagnose_info` (test_suite/fork_table/include/wait_daily_merge.inc:25-29).
9. **Table options as schema attributes** with the same defaults and the same `SHOW CREATE TABLE` text (section 3.4, option (a)).
10. **Typed errors at the storage budget owners** (Decision 12), with the corrected behavior of the micro block cache (section 5.1).

### 4.2 May change

- Every on-disk format: macro and micro block layout, the flat row and column encodings, compression library and version, checksums, index block format, slog, clog, super block, tablet persistence (Decision 11). Block counts will then differ for tables in sstables; that difference is what the EST mask is for.
- Block sizes as storage uses them, provided the schema attributes still print `BLOCK_SIZE = 16384` and the other defaults of section 3.4. Staying near 2 MB and 16 KB keeps EST.TIME close; it is not required.
- The memory design: no tablet pool buffers and no wash (Decision 1), `Arc` snapshots and epoch reclamation, other caches, IO engine and allocators, within Decisions 12 and 14.
- Block granule split points at dop above 1: the output order there is already timing-dependent in C++.
- meta.db: SQLite through rusqlite behind a trait, opened after the gate, as research 11 recommends. Or it may move; see question 3.

### 4.3 Estimator inputs: answer to PLAN §8 item 17

Answer (b) of section 3.1 plus the first row of section 3.2. The EST numbers should then be the same as C++ with two exceptions:
- EST.TIME for a table that has data in sstables and also has statistics or was sampled (the block counts differ);
- EST.ROWS for a range in a major sstable that holds at least 1,000 times the rows of its border micro block (the rows per block differ).

The sensitivity run below measures how often the first exception occurs. The second needs tables far larger than the 100-row maximum in the 40 files. The final gate's exact rerun of the masked class (Decision 6) documents whatever differences remain. Keeping them exact too would need option (a), whose cost section 3.1 gives. Recommend (a) only if the sensitivity run below shows that many EST lines or plan shapes depend on block counts.

### 4.4 Checks, in order

1. **00b, before any Rust code, two C++ sensitivity mutations**, run the way PLAN §4 "How the injected mutations run" describes, on the 40 plan-bearing cases and the 272:
   - (i) double the micro and macro block counts returned by `ObLSTabletService::inner_estimate_block_count_and_row_count` (ob_ls_tablet_service.cpp:4786-4829);
   - (ii) make the memtable walk stop at 50 rows instead of 500 (ob_query_engine.h:134).
   The diffs list every EST line, plan shape and row order that depends on block counts, and on the memtable walk beyond 50 rows. Mutation (i)'s diff is the expected size of the documented EST difference under (b). If (i) changes plan shapes or unmasked lines, those cases name exactly where (a) would be needed.
2. **00b: a small estimator corpus of `EXPLAIN EXTENDED` statements.** The Optimization Info block prints `physical_range_rows` and `logical_range_rows` unmasked. So statements over tables in known states compare the estimator directly between builds: inserts; deletes; one transaction left open; after a minor freeze; after a major freeze; above and below 500 rows.
3. **Step 2a narrow run:** single-table statements over tables with and without a primary key, and one partitioned table at dop 1. All are memtable-only, so rules 1-4 and 6 of section 4.1 are exercised from the first run.
4. **Core build:** the plain-SQL cases (128 in judge/lists/plain-sql.txt), then the 40 plan-bearing cases with the mask off. Every EST difference not explained by (i) is a bug.

### 4.5 The version gate: concrete rules

This agrees with research 11 (11-bootstrap-data.md, "Decision 11" and §8.3).
1. The first thing the engine does with a base dir: read etc/seekdb.data_version.bin before opening meta.db, logs or storage.
2. The missing-file rule: when the file is missing, accept the directory as fresh only if there is no block file and no clog (the `check_need_initialize` test, ob_server.cpp:410-433) **and** no etc/observer.data_version.bin. The last condition covers family 15's v1.0.x case explicitly, instead of through the block file.
3. Write the file first during bootstrap, to a temporary name that is then renamed.
4. Keep the file format (magic 0xBEDE, format 2), so the frozen C++ binary refuses a Rust directory with its own clearer message. Write a value no C++ build wrote; see question 2.
5. On refusal, print one message to stderr and to the log, then exit non-zero. The message names the found and the expected version and the way out: export with the C++ build, then import.
6. Inside the new formats, version fields are per format. The gate stays the single check between releases. The 70 C++ references to `DATA_CURRENT_VERSION` do not carry over one for one.

### 4.6 The one storage crossing into C++, and the earlier ABI conventions

The only place storage data crosses into a C++ island is vsag's snapshot, which is streamed through `ObOStreamBuf`/`ObIStreamBuf` (src/query/api/query/vector/ob_vector_index_serialize.h:51, 83). It is pinned to vsag's old serial format because `ObIStreamBuf` cannot seek (evidence-full.md:3338). Of the conventions in /Users/colin/obsidian/tech/seekdb/migrate to rust/abi-naming.md and notes/ffi-mechanics.md, three bear on it:
- **Adopt:** the receiver goes as the first parameter, never a global. The stream is one per snapshot, and ffi-mechanics.md measured what a global does under two threads.
- **Adopt:** `_handle` means an owned token released exactly once. That fits the vsag index object.
- **Adopt, with a limit:** "the Rust entry does not check the `(data, len)` that C++ passes" can hold for vsag's own buffers in the read and write callbacks. But the snapshot's stored length is checked by the Rust storage code before any byte is served. A truncated or corrupt file then ends as an error code in storage, not inside the shim crate, which is one of Decision 14's `unsafe` crates.

## 5. Corrections and risks found

### 5.1 Corrections

- **The micro block cache has no retries at 834bbee1e.** The report (§10, Decision 12), PLAN family 12 ("-4013 from the micro block cache after its retries") and research 03 (03-memory.md:108, 248) cite a loop of 3 retries with 100 ms sleeps (ob_micro_block_cache.cpp:396-402). That function, `ObIMicroBlockIOCallback::alloc_data_buf`, is marked `//UNUSED NOW` (:389). The only calls of any `alloc_data_buf` in src are the tmp-file cache's own (`grep -rn alloc_data_buf src`, excluding definitions: only ob_tmp_file_cache.cpp:698, 739, 769). The cache's allocations return -4013 at once from its 4 GiB FIFO allocator (ob_micro_block_cache.cpp:502-503, 843-844, 1006-1009, 1035-1036, 1071-1072). Family 12 and the design document should expect -4013 without sleeps. A reviewer should confirm this before research 03's "3 sleeps" rule is adopted.
- **The EST mask misses the Optimization Info block** (view_2.result:473-498). Section 4.1 keeps these lines exact, so no mask change is needed. But the mask declaration in 00b should say that it does not cover them.
- **9 configured cases freeze, not 8** (sfu_norow_alias via include/majorfreeze.inc).
- **The vault notes' `g_mp`** does not exist at 834bbee1e; see section 1.1.

### 5.2 Risks

| Risk | Evidence | Handling |
|---|---|---|
| Under the reduced init, the statistics jobs are live. The async job runs every 15 minutes and the daily windows at 22:00 (weekdays) and 06:00 (weekends). Only tracepoint 368, which the reduced init drops, stops them for non-user sessions. Gathered statistics change plan choice and so the order of unordered rows | ob_dbms_stats_maintenance_window.cpp:176-210; ob_dbms_stats.cpp:2720-2731, 4760-4768; judge/reduced-init/README.md | 00b: a recorded amendment that disables both jobs in the reduced init for both builds, or proof that no plain-SQL table is gathered within a run |
| The daily major freeze at 02:00 runs under both inits. A judge run that crosses 02:00 freezes every tablet at a different moment in each build | ob_parameter_seed.ipp:533-537; the launcher skips when the parameter is disabled (src/rootserver/freeze/ob_daily_major_freeze_launcher.cpp:152-156) | 00b: set `major_freeze_duty_time='disable'` in both init profiles, or schedule runs away from 02:00 |
| A plan flip where block counts matter, under (b) | Section 3.1 | Measure with mutation (i) |
| The hidden-key sequence or the collation compare drifts silently | Section 1.3 | The narrow run's statements include tables without a primary key. Three of the four matrices (add, datatype.minus, expr.mul) are in judge/lists/plain-sql.txt (lines 76, 146, 161) and datatype.div is plan-bearing, so the core build's first plain-SQL run catches any drift at once |
| Estimates above 500 rows, if the memtable index does not keep the C++ rules | Section 3.2 | Keep the rules (section 4.1, item 6) |

## 6. Questions for the developer

1. **EST at the final gate.** Accept documented EST.TIME differences for tables that sit in sstables and have statistics or DS, with (b) of section 3.1? Or require exact EST numbers, which needs (a): size-identical block writing and zstd 1.3.8 kept in the product?
2. **The new `DATA_CURRENT_VERSION` value**, and whether the package version (1.4.0.0) moves with it. Research 11 asks the same (its Q4); one answer serves both.
3. **Where the Rust metadata store lives.** Keeping store/sstable/meta.db means a frozen C++ binary started on a Rust directory writes into it before refusing. Moving it avoids that. Research 11's Q5 (SQLite or not) is the same decision.
4. **What the table-option text means.** Should `COMPRESSION = 'zstd_1.3.8'` stay a printed default even if the Rust build compresses with another zstd version (section 3.4, (a))? Or must the name stay true, which ties the product to zstd 1.3.8?
