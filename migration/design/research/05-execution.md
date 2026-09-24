# Research 05: execution, column batches, storage filters, and sort and hash order

Step 1 research for three items the design document must decide (PLAN.md section 6): "the column batch and the storage filter trait", "the storage and PX scan order that the about 7,500 unmasked single-table SELECTs depend on", and "sort and hash determinism".

Source facts are at the frozen base 834bbee1e; paths are from the repository root; "assumption" marks what was not checked; the appendix lists the commands behind the counts.

## Summary

- Query execution runs only on batches of 12-byte `ObDatum` values in untyped per-query frames. Vectorization 2.0 (`ObIVector` and its formats) left query execution in cbcea2b91; its headers survive only for the DDL and direct-load write path, so the Rust column batch does not need them.
- Storage and SQL call each other in a loop: storage's micro-block scanner calls SQL's filter tree, which calls storage's decoders, which call SQL's expression evaluation for black filters. Storage also writes scan results straight into SQL's frames.
- The compared row order depends on more than the sort and hash code: on which sort algorithm the optimizer picks (a tracepoint in the full init.sql; an estimated cardinality under the reduced init), on where batches end (the hash join interleaves its output by probe batch), and on the storage estimator (plan choice). Masking EST numbers hides none of this.
- In memory, hash group-by, hash distinct, the hash set operators and the probe side of hash joins emit rows in an order independent of hash values. The build-side output of left outer, full outer and left anti hash joins, the hash-based partition sort, dumps to disk and OB hash-map iteration in the optimizer depend on them.
- Recommendation: a safe column batch of fixed-size datum handles in typed per-expression slots of a per-run frame; a storage-owned filter tree whose black leaves call SQL through a trait passed on every scan call, plus the aggregate protocol the C++ already has (src/share/aggregate); and transcribed libc++ 20.1 `std::sort` and heap functions, OB's own sorts, hash functions and hash tables, identical batch sizes and batch ends, and identical storage estimator inputs. Five questions for the developer close the report.

## 1. What the C++ does today

### 1.1 Datums, expressions and frames

- `ObDatum` is a packed 12-byte value: an 8-byte pointer union (src/oblib/common/datum/ob_datum.h:106-131) and a 4-byte descriptor with a 29-bit length, a 2-bit flag (NONE, OUTROW, EXT, HAS_LOB_HEADER) and a null bit (:134-165). `static_assert(12 == sizeof(ObDatum))` guards the SIMD filter code (src/sql/engine/basic/ob_pushdown_filter_simd.cpp:46). A datum never owns its bytes: its pointer goes into the expression's reserved buffer, another expression's result, a storage micro block, or a row store.
- `ObExpr` (src/query/api/query/engine/expr/ob_expr.h:342) holds raw function pointers `eval_func_` and `eval_batch_func_` (:617-662), pointer arrays for arguments and parents, and offsets into a frame: `frame_idx_`, `datum_off_`, `eval_info_off_`, `dyn_buf_header_offset_`, `res_buf_off_`, `res_buf_len_`, `eval_flags_off_`, `pvt_skip_off_` (:683-699), plus `batch_idx_mask_` (:711). Every access is `reinterpret_cast<ObDatum *>(ctx.frames_[frame_idx_] + datum_off_)[idx]` (:364-386).
- `ObEvalCtx` (:161-292) holds `char **frames_`, the batch index and size, a temporary allocator and a result allocator. It names storage's `ObVectorStore` as a friend (:168).
- There are four kinds of frame: const, param, dynamic and datum (src/sql/engine/expr/ob_expr_frame_info.h:136-144). Const frames belong to the shared plan; datum frames belong to one run.
- `ObExpr::eval` (ob_expr.h:1039-1070) checks the evaluated flag in `ObEvalInfo` (:130-159), points the datum at the reserved buffer, calls `eval_func_`, and sets the datum to NULL on error; `eval_batch` is at :1072-1094. Most expressions have no batch function: src/sql and src/query have 579 `eval_func_ =` lines and 93 `eval_batch_func_ =` lines, and the default batch function calls `eval_func_` once per row that is neither skipped nor evaluated (src/sql/engine/expr/ob_expr.cpp:711-753). Hand-written batch loops step 16 rows at a time by reading the 64-bit bitmap words (`ObBitVector = ObBitVectorImpl<uint64_t>`, src/share/vector/ob_bit_vector.h:195) as `uint16_t` (src/sql/engine/expr/ob_batch_eval_util.h:40-90). A batch loop stops at the first failing row, so which error a statement reports depends on row order and on batch boundaries.
- Lines under src/sql that reach into these internals: `locate_expr_datum` 332, `locate_batch_datums` 146, `locate_datum_for_write` 106, `get_evaluated_flags` 97; 35 of the 156 operator files use one of them or `frames_` (66 with the evidence's wider pattern, evidence-full.md:188). About 528 expression types are registered (529 `REG_OP(` lines in ob_expr_operator_factory.cpp).

### 1.2 What is left of vectorization 2.0 (`ObIVector`)

- cbcea2b91 (2026-07-30, "remove rich vectorization engine 2.0", 1,969 files, -165,592 lines) removed the column formats from query execution. 8f96ded56 (2026-08-12) removed the row-at-a-time mode (-244,621 lines).
- The format headers remain: `ObIVector` (src/query/api/query/engine/vector/ob_i_vector.h:372) and `VectorFormat` with the uniform, fixed-length, discrete and continuous formats (type_traits.h:27-39), 1,797 lines together with the forwarding copies under src/sql/engine/vector. The 185 `ObIVector` references belong to the DDL and direct-load write path (23 files in src/storage/ddl; `ObBatchDatumRows` in src/storage/blocksstable), the temp column store behind PX sstable insert (src/sql/engine/basic/ob_temp_column_store.cpp, reached only from ob_px_sstable_insert_op.cpp), and cast helpers in ob_datum_cast.cpp that take `ObIVector` and that no other file calls. The rich-format branch of the pushdown aggregate program is `#if 0` (src/sql/engine/aggregate/ob_pushdown_aggregate_program.cpp:764-797, :823-1156).
- So every query operator and expression works on `ObDatum` batches.

### 1.3 Batch sizes and where batches end

- `_rowsets_max_rows` is 256 and `_rowsets_target_maxsize` 524,288 (src/share/parameter/ob_parameter_seed.ipp:290-295); `_lob_rowsets_max_rows` is 65,535 (:327).
- `ObCodeGenerator::detect_batch_size` (src/sql/code_generator/ob_code_generator.cpp:85-170) returns 0 (no batches) when a child statement assigns user variables, and 1 when the root statement assigns them or the plan cannot be vectorized but has a registered vectorized operator. Otherwise `ObStaticEngineExprCG::detect_batch_size` (src/sql/code_generator/ob_static_engine_expr_cg.cpp:95-166) takes the configured maximum, lowers it to each expression's own maximum, and rounds down to a power of two; plans with a non-deterministic UDF, a UDF next to a user variable, or `T_FUN_SYS_WRAPPER_INNER` get 1 (:1406-1457); and the size is capped at 16 when the largest estimated table-scan cardinality is below 16 (`SMALL_SCAN_CARDINALITY`, ob_static_engine_expr_cg.h:75, :144-145; the cardinality is `op->get_card()`, src/sql/code_generator/ob_static_engine_cg.cpp:480-483). A `ROWSETS_MAX_ROWS` hint overrides it; tracepoint 2206 (`EN_ENABLE_RANDOM_BATCH_SIZE`) makes it random.
- The result is printed as `rowset=` in plan text (the explain path calls the same function, src/sql/optimizer/ob_explain_log_plan.cpp:56): 3,035 `rowset=16` and 563 `rowset=256` lines in the tracked .result files, which PLAN.md section 4 keeps exact. Because of the small-scan rule, whether a plan prints 16 or 256 depends on the optimizer's cardinality estimate.
- The batch size is not only about speed: the UDF and user-variable rule keeps side effects row by row (comment at ob_static_engine_expr_cg.cpp:1429-1448).
- Where a scan batch ends: in the BATCH path the vector store fills rows from one micro block and ends the batch there ("todo: support data cross microblocks in vectorized", then `set_end()`, src/storage/access/ob_vector_store.cpp:306-352). The SINGLE_ROW path (memtable rows, delete versions) fills row by row, and a switch between the two states ends the batch (`ScanState`, src/storage/access/ob_multiple_merge.h:43-47; the `OB_PUSHDOWN_STATUS_CHANGED` return code, 13 lines under src/storage). LIMIT passes its remaining count down as `max_row_cnt` (src/query/api/query/engine/ob_operator.h:416).

### 1.4 Operators

- 61 operators are registered in src/query/api/query/engine/ob_operator_reg.h; 45 are `VECTORIZED_OP`. The other 16 are VALUES, INSERT, DELETE, UPDATE, REPLACE, INSERT ON DUP, the four PX multi-part DML operators, TABLE ROW STORE, EXPR VALUES, VALUES TABLE ACCESS, TOPK, FUNCTION TABLE and JSON TABLE. They run through `get_next_batch_with_onlyone_row` (ob_operator.h:525-560), a batch of one row.
- `ObBatchRows` (src/query/api/query/engine/ob_batch_rows.h:30-73) is a skip bitmap, a size, an end flag and an all-rows-active flag; the values themselves sit in the operator's output expressions' frame datums. src/sql defines 45 `inner_get_next_batch` and 62 `inner_get_next_row` methods.

### 1.5 Storage pushdown filters and the boundary between storage and SQL

- Filter node and executor kinds (src/query/api/query/engine/basic/ob_pushdown_filter.h:99-131): black, white, AND, OR, dynamic (the join runtime filter and the pushdown top-N filter, :148-153), sample, semi-structured and external. The white operators are `=`, `<=`, `<`, `>=`, `>`, `<>`, BETWEEN, IN, IS NULL and IS NOT NULL (:404-417). A predicate is white when its first argument is a column (or a JSON path storage can read) and every other argument is a constant that compares without a cast (`is_white_mode`, src/sql/engine/basic/ob_pushdown_filter.cpp:522-578). Everything else is black.
- The call chain goes back and forth. Storage's micro-block scanner calls SQL's `ObPushdownFilterExecutor::execute` (ob_pushdown_filter.cpp:1370-1449), which walks AND and OR children with bitmaps and calls `data_plane::filter_micro_block` (:1526), defined in storage (src/storage/blocksstable/ob_micro_block_row_scanner.cpp:2122-2131). That calls the reader or decoder (`filter_pushdown_filter` and `filter_black_filter_batch`, src/storage/blocksstable/encoding/ob_imicro_block_decoder.h:46-65; the per-column `pushdown_operator`, ob_icolumn_decoder.h:150-175, implemented by the dictionary, RLE, const, raw, integer-base-diff and other decoders). White filters run on encoded data there. Black filters get their column values decoded into the SQL expressions' frames and then run `ObBlackFilterExecutor::filter_batch`, which is `ObExpr::eval_batch`.
- Skip index: every executor gives an `ObBoolMask` (always true, always false, or uncertain) from per-block minimum, maximum and null counts (`execute_skipping_filter`, ob_pushdown_filter.cpp:1451-1485; src/storage/access/ob_sstable_index_filter.h). Black filters take part when they are monotonic (`judge_greater_or_less`, ob_pushdown_filter.h:941-944).
- Runtime reordering: `ObWhereOptimizer` (src/storage/access/ob_where_optimizer.cpp:24-190) records each child's cost, measured with `rdtsc`, and its filtered row count (ob_pushdown_filter.cpp:1505-1550), and re-sorts the children of an AND or OR node from these figures, first after one batch and then every 32 batches (`REORDER_FILTER_INTERVAL`, :24, :170). The bitmaps do not depend on child order, but which rows reach which black filter does (an AND child skips rows already false). So after a reorder, which black filter raises a warning or error first depends on timing, in the C++ itself.
- Output: `ObVectorStore` copies datums shallowly into the output expressions' batch datums, pointing into micro-block memory (ob_vector_store.cpp:95-120, :306-352). Storage also evaluates SQL expressions itself, for generated columns and row filters (`fill_virtual_columns` and `filter_row_outside`, src/storage/access/ob_multiple_merge.cpp:1254-1292).
- Aggregate pushdown already has a protocol between storage and SQL, added in 8f96ded56: src/share/aggregate/ob_pushdown_aggregate_protocol.h (217 lines) declares `ObIAggregateInputSegment`, which storage implements for a row batch, a decoded batch, an encoded micro block or an index summary, and `ObIPushdownAggregatePlan`/`ObIPushdownAggregateProgram`, which SQL implements and storage drives through consume, seal and emit. Users: src/storage/access/ob_pushdown_aggregate_input.h, ob_aggregated_store.cpp; src/sql/engine/aggregate/ob_pushdown_aggregate_program.cpp.
- Size of the boundary: src/storage has 1,070 `sql::` lines in 99 files, 289 references to filter executor classes in 41 files, and 369 lines using `ObEvalCtx`, `ObExpr`, frames, batch datums or `ObBitVector` in 54 files.

### 1.6 PX in the single-node server

- `parallel_degree_policy` defaults to MANUAL (src/share/system_variable/ob_system_variable_init.json:2873-2890), so PX runs for PARALLEL hints, tables with a parallel degree, DDL and PDML. In the tracked .result files, 512 plan lines show `dop=1` and one shows `dop=3`; 14 files print a PX COORDINATOR. Most PARALLEL hints in the tests are on DDL and PDML; the parallel SELECTs without ORDER BY are a handful, mostly returning one row.
- Granules are handed out in task order. They are shuffled, by murmurhash of the task index and then `lib::ob_sort`, only for DDL and PDML (src/sql/engine/px/ob_granule_pump.cpp:205-250, :654-690). With DOP 1 a PX scan returns partitions and block ranges in order. With DOP above 1, which worker takes which granule depends on timing, so row order and ties across workers are not reproducible, even C++ against C++.
- The merge-sort receive merges the workers' sorted streams with `std::push_heap` and `std::pop_heap` over channel indexes (src/sql/engine/px/exchange/ob_row_heap.h:248-270), so ties across channels follow libc++'s heap.
- Even in one process the SQC arguments are encoded and decoded (src/sql/engine/px/ob_px_local_sqc_launcher.cpp:431), with function pointers serialized as raw addresses (src/query/api/query/engine/ob_serializable_function.h:52-80).
- The full init.sql sets `_max_px_workers_per_cpu = 10` (tools/deploy/init.sql:36) and tracepoint 561, a random PX shuffle without statistics (:29; read at src/sql/optimizer/ob_log_plan.cpp:7399). The reduced init sets neither.

### 1.7 Sort: the algorithms and when each runs

| Path in `ObSortOpImpl` | Where | What decides the order of ties |
|---|---|---|
| Encoded key, `ObAdaptiveQS` (three-way quicksort plus byte radix on the encoded key) | src/sql/engine/sort/ob_sort_op_impl.cpp:31-360, used at :1853-1861 | Input order and the swap sequence; ties are rows with equal encoded bytes |
| `lib::ob_sort` (= `std::sort`) with the row comparator | src/oblib/lib/utility/ob_sort.h:28-41; ob_sort_op_impl.cpp:1862, :1865 | libc++'s introsort and input order |
| Partition sort, `part_cnt_ > 0` (window functions, merge group-by with a hash sort key) | :1361-1466 | Partitions in order of the `T_FUN_SYS_HASH` value (`hash >> shift`, then sorted inside a bucket); rows inside a partition by AQS or `std::sort` |
| Top-N heap and partition top-N | :605-700, :1176-1260, :1468-1528 | OB's `ObBinaryHeap` |
| In-memory merge of sorted runs (`local_merge_sort_`) | :1871-1905 | `ObBinaryHeap` over the run heads |
| External merge after a dump to disk | :1917-1990; `build_ems_heap` at :1658 | Chunk boundaries (set by the memory bound and the row store's byte counts) and the heap |
| Prefix sort, unique sort | :2955; classes at ob_sort_op_impl.h:888, :972 | `lib::ob_sort` per prefix group |

- Which of the first two paths runs is an optimizer choice. `ObLogSort::get_op_exprs` (src/sql/optimizer/ob_log_sort.cpp:91) calls `check_can_encode_sortkey` (src/sql/optimizer/ob_optimizer_util.cpp:6938-6995). It needs `_enable_newsort` (default True, ob_parameter_seed.ipp:857) or the hint; then encodable types only: integers, floats, NUMBER, the date and time types, CHAR and decimal-int, with the binary, utf8mb4_bin, utf8mb4_general_ci and some GBK and GB18030 collations, but not VARCHAR (src/data_plane/api/data_plane/ob_order_perserving_encoder.h:118-133); then a width and cardinality rule on the child's estimated cardinality, with no encoding below 1,000 rows (:6976-6985). Tracepoint 1200, `EN_ENABLE_NEWSORT_FORCE`, skips that rule (:6988-6992). The full init.sql sets it to fire on every call (init.sql:24 with frequency 1; src/oblib/lib/utility/ob_tracepoint.h:260-289); the reduced init does not. So the same `ORDER BY int_column` sorts with AQS under the full init, and with `std::sort` under the reduced init whenever fewer than 1,000 rows are estimated.
- The C++ reference's libc++ headers come from SDK 26.2, `_LIBCPP_VERSION` 200100 (migration/judge/reference.md). Its `std::sort` is an introsort derived from pdqsort: sorting networks for 2 to 5 elements, insertion sort below 24 elements, median of three or Tukey's ninther above 128, `__partition_with_equals_on_right`, `__partition_with_equals_on_left` when the pivot equals the element just before the range, a partial insertion sort after a balanced partition (limit 8), and heap sort when the depth limit `2*log2(n)` runs out (/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk/usr/include/c++/v1/__algorithm/sort.h:334-346, :717-826, :883-889). `lib::ob_sort` never passes `std::less`, so the header template is always instantiated rather than the library's prebuilt versions (sort.h:870-940), and the branchless bitset partition is never chosen (sort.h:53-56). `std::pop_heap` sifts down Floyd's way and then up (pop_heap.h:47-56).
- There are 169 `lib::ob_sort(` call lines: sql 73, storage 39, observer 28, share 12, oblib 9, rootserver 8. Storage sorts scan ranges and multi-get rowkeys with it before scanning (src/storage/access/ob_table_scan_range.cpp:177-179, :213-215), so equal keys from different groups keep libc++'s tie order there too. Three more `std::sort` lines sit outside ob_sort.h; four `std::stable_sort` lines sort JSON object keys (src/oblib/common/json_type/ob_json_tree.cpp:743) and XML nodes; heap functions appear in four files, among them the PX row heap and the hash group-by popular-value heap (src/sql/engine/aggregate/ob_hash_groupby_op.cpp:2616-2623).
- After a comparator error, the quick-sort comparator switches to address order "to maintain strict weak ordering" (ob_sort_op_impl.cpp:399-408), and the statement fails with the error (:1867-1869). So no order after an error is ever compared. PLAN.md section 3 item 1 cites :440-445 as a comparator that returns false after an error; at 834bbee1e those lines belong to the top-N row check used at :2420, not to a `std::sort` comparator. One detail a translation must keep: equal encoded prefixes are ordered by `(l_cell.len_ - r_cell.len_) < 0` on 29-bit bitfields, which C++ promotes to `int` (:414).

### 1.8 Hash operators: what decides their output order

| Operator | Output order with all rows in memory | Depends on hash values? |
|---|---|---|
| HASH GROUP BY | Groups in first-seen order: `curr_group_id_` walks group ids 0..n, assigned on insert (src/sql/engine/aggregate/ob_hash_groupby_op.cpp:478-562, :1433-1550, :2226-2240) | Only after a dump, when rows go to partition `(hash >> part_shift) & (part_cnt - 1)` (:1002, :1961, :2233) and partitions are aggregated one after another; also in the bypass and skew handling |
| HASH DISTINCT; HASH UNION, INTERSECT, EXCEPT DISTINCT | Rows from the insertion-ordered store (`get_next_hash_table_row`, src/sql/engine/basic/ob_hash_partitioning_infrastructure_op.h:2610-2655) | Only after a dump |
| HASH JOIN, rows emitted while probing (inner; right outer, semi, anti) | Probe order; inside one probe batch, the k-th pass over the batch emits every probe row's k-th match (src/sql/engine/join/ob_hash_join_op.cpp:4319-4476, :4946-4964); a probe row's matches come newest build row first (head insertion, ob_hash_join_op.h:459-481) | No, but the probe batch boundaries show in the output |
| HASH JOIN, build rows emitted at the end (unmatched rows of left outer and full outer joins, left anti) | Bucket order: `cur_bkid_` walks the open-addressing table (`fill_left_join_result_batch`, :5185-5232) | Yes: `murmur_hash_v2` with seed 16777213 (ob_hash_join_op.h:1123), `HASH_VAL_MASK`, the bucket count from `calc_bucket_number` over the actual build row count (:1338-1348, :1351-1450), linear probing (ob_hash_join_op.h:410-481), and a counter-based hash for NULL keys (:4029, :4077) |
| Partition sort | `T_FUN_SYS_HASH` value order (1.7) | Yes |
| OB hash maps iterated in the optimizer and rewriter | Bucket order of `ObHashTable`: `hash % bucket_num`, bucket counts from a 28-entry prime list, chains with head insertion (src/oblib/lib/hash/ob_hashtable.h:1044, :1139; ob_hashutils.h:677-689) | Yes. Sites whose order reaches output: full-text tokens (src/sql/optimizer/ob_join_order.cpp:16662, :16727; `ObString` keys hashed with murmurhash, seed 0, ob_hashutils.h:773-779), spatial cell ids turned into ranges (src/sql/rewrite/ob_range_generator.cpp:1799), IN-list values (src/sql/rewrite/ob_key_part.cpp:289, :305) |

OB hash maps keyed by pointers hash the pointed-to value (`hash_func<_key *>`, ob_hashutils.h:756-763), so no iteration order depends on addresses, and src/sql uses no `std::unordered_map`. src/sql iterates OB hash maps in at least 26 `foreach_refactored` calls and 12 typed iterator loops. Plan text in the tracked .result files, a sample only, shows HASH JOIN 118 times, HASH GROUP BY 76, HASH FULL OUTER JOIN 28, HASH OUTER JOIN 6, HASH SEMI and ANTI JOIN 4, the HASH RIGHT joins 20, HASH DISTINCT 10.

### 1.9 Where the order reaches compared output

| Where | Size | Notes |
|---|---|---|
| Ties under ORDER BY | Not counted. In the two coverage passes the row comparator ran about 1.3M calls with all keys equal (migration/judge/mutations/README.md, mutation 03) | Tie order is the sort algorithm plus the order rows arrive in, so scan order and hash output order reach ordered results too |
| SELECTs without ORDER BY | 00b's census of the configured cases (migration/judge/lists/hash-order-select-candidates.txt:209-225): 22,771 SELECT or WITH statements, of which 1,787 have ORDER BY, 10,407 no FROM, 793 run under `--error` and 108 under `--sorted_result`; 9,042 are single-table and 633 are hash-order candidates | The report's figures over the tracked cases are 10,107 of 11,908, about 7,500 single-table, about 300 hash-order |
| The 9,042 single-table SELECTs | 202 read system, virtual or dual tables; 8,840 read user tables, 1,618 of them with WHERE and 28 with LIMIT | Order is the key order of the chosen access path, and the access path is a cost-based choice |
| Other places | LIMIT without ORDER BY; GROUP_CONCAT without ORDER BY; window functions over ties (ROW_NUMBER); INSERT ... SELECT into an auto-increment column; which row's error or warning a batch loop reports first | The row-order mask covers none of these |

## 2. Constraints that bind this topic

| Source | What it says | What it means here |
|---|---|---|
| Decision 6 (b); PLAN §4, "The masks" | Exact comparison; masks only for EST.ROWS and EST.TIME in 40 files and for the row order of about 300 hash-order SELECTs | Ties, scan order, `rowset=` and every other effect in 1.9 must be exact |
| Decision 10 (a); PLAN §3 | The SQL tier is rewritten keeping its control flow; algorithms that produce compared text stay bit for bit | The 528 expression bodies and 61 operators keep their loops, so the batch API must serve them |
| PLAN §3, outline, "Execution" and "sql/storage boundary" | Immutable `Send + Sync` plan; per-run state in typed column batches; storage defines the column batch and a filter/aggregate trait, SQL implements it | Sets which side defines the types |
| PLAN §3, silent changes 1 and 2 | Tie behavior as the SDK 26.2 libc++ implements it; OB hash iteration reaches output | Transcribe, do not substitute |
| Decision 14 (b) | `unsafe` only in named crates: island shims, SIMD kernels, IO buffers, reclamation wrappers | A datum cannot be a raw pointer in the SQL or storage crates |
| Decision 1a | Median latency and QPS within 1.2x of the C++ on the Step 2a scan+filter+aggregate query and on sysbench | White filters on encoded data, batched black filters and aggregate pushdown have to stay |
| Decision 12 (b) | Typed out-of-memory errors only at named budget owners, including the SQL work-area spill and the hash-join partition depth -4013 | Row stores and hash tables of spilling operators allocate fallibly; nothing else here does |
| Decision 16 (a) | Stable 1.98.1, no nightly | No `std::simd`, no `allocator_api` (allocator-api2 instead) |
| Decision 7 and the wasm guidelines | Keep wasm, Android and Windows possible; SIMD128 chosen at compile time | No run-time CPU dispatch that changes results (today AQS picks its byte compare by `is_avx512_supported`, ob_sort_op_impl.cpp:278-283) |
| Decision 11 (a) | New on-disk formats are allowed | Section 4.4: rows per block feed estimates and batch ends |
| Decision 3; judge/reference.md | Frozen C++ reference built against SDK 26.2 headers, `_LIBCPP_VERSION` 200100 | The libc++ version to transcribe |
| PLAN §8 items 17, 18, 21-23 | Estimator inputs open; scan order must be kept; timing cases; coverage depth; false failures from unmasked exactness | Sections 4.3 and 4.4 answer 17 and 18 from this topic's side |

## 3. Options

### 3.1 The column batch

| Option | What it is | Costs and risks |
|---|---|---|
| A. Frames as byte buffers with offsets, as today | A transliteration of `frames_` and `reinterpret_cast` | Needs `unsafe` in every SQL crate, against Decision 14. Rejected |
| B. Typed slot per expression; a datum is a handle | A per-run frame with one slot per expression. A datum is a fixed-size value that holds values of up to 8 bytes inline and otherwise a buffer id and an offset. Buffers are reference-counted handles registered for the batch | Safe. One indexed load per variable-length read. Keeps the expression loops. A read after the producer's next batch gets wrong bytes or a panic, never undefined behavior |
| C. Like B, but copy variable-length bytes into buffers the frame owns at the storage boundary | Simpler lifetimes | Copies every scanned string, where the C++ copies nothing ("shallow copy", ob_vector_store.cpp:306); a risk to the 1.2x gate on string-heavy scans |
| D. Column formats: `ObIVector` again, or Apache Arrow (arrow-rs) | Typed columns per format | Rewrites the 528 expression bodies instead of keeping their loops and brings back what cbcea2b91 removed; Arrow has no place for `ObDatum`'s OUTROW and LOB-header flags. Rejected for the port |
| E. Datums as raw pointers inside one named `unsafe` crate | Closest to the C++ | Needs the developer to add that crate to Decision 14's list |

### 3.2 The storage filter and aggregate boundary

| Option | What it is | Costs and risks |
|---|---|---|
| F1. Storage owns the filter tree; SQL implements the black leaves | White filters are storage data. Black leaves, row filters and generated columns call SQL through a trait passed into each scan call. Aggregate pushdown uses the existing protocol as traits | One dynamic call per batch per black filter, as in the C++ loop; no SQL types in storage |
| F2. Move expression evaluation below storage | Storage depends on `ObExpr` | Pulls 528 expressions, the session and the value libraries under storage and undoes the crate split. Rejected |
| F3. Per-row callbacks | A closure per row | Slower than today, and since batch loops stop at the first failing row, row callbacks change which error is reported. Rejected |
| F4. Storage generic over the filter type | Monomorphized scans | Compile time and code size for no gain, because dispatch is per batch. Rejected |

### 3.3 Sort and hash order

| Option | Costs and risks |
|---|---|
| S1. Transcribe libc++ 20.1's `std::sort` and heap functions, and translate OB's own sorts | About 600 lines of libc++ to transcribe once; exact |
| S2. Rust's `sort_unstable` or `sort_by`, with ties normalized by the judge | A third mask, which Decision 6 does not allow. Rejected |
| S3. Stable sorts in both builds | The reference is frozen (Decision 3). Rejected |
| H1. Transcribe OB's hash functions and seeds, and translate its hash tables | Exact everywhere; small, since the tables live in the SQL tier that keeps its control flow anyway |
| H2. hashbrown with a fixed seed, relying on the mask | Changes bucket-order output outside the mask (ORDER BY ties above a left outer join, LIMIT, auto-increment values). Rejected |

## 4. Recommendation

### 4.1 The column batch: option B

In the datum crate, below both storage and SQL (crate names are report 01's to settle):

```rust
pub struct Datum { loc: u64, desc: u32 }   // desc: len 29 bits | flag 2 bits | null 1 bit, as ObDatumDesc
// loc: the value itself for types of at most 8 bytes; otherwise a BufId (u32) and an offset (u32)
pub struct DatumVec { datums: Vec<Datum> } // max_batch_size entries, at most 65,535
pub struct Bitmap { words: Vec<u64> }      // skip and evaluated flags; 16-row chunks by shifts
pub struct BufTable { bufs: Vec<BufRef> }  // Arc'd block buffers, per-batch arena chunks, plan constants
```

In the SQL engine crates:

```rust
pub struct EvalFrame { slots: Vec<ExprSlot>, bufs: Vec<BufTable> /* one per batch producer */, batch_idx: u32, batch_size: u32 }
pub struct ExprSlot { datums: DatumVec, eval_flags: Bitmap, pvt_skip: Bitmap, info: EvalInfo, res: ResBuf }
// every operator:
fn next_batch(&mut self, frame: &mut EvalFrame, max_rows: usize) -> ObResult<BatchRows>;
```

Rules:
- The plan (expressions, operator specs, const data, and the chosen eval functions as plain `fn` pointers) is immutable and shared as an `Arc` by PX workers; each worker builds its own `EvalFrame`. This removes the in-process serialization of 1.6.
- A slot keeps today's meaning: a producer's datums stay valid until its next batch, and consumers that keep rows longer copy them into row stores that own their bytes, as the C++ does. A producer resets only its own `BufTable` when it starts a batch.
- Storage fills `DatumVec`s the caller owns and registers its block handles in a `BufTable` the caller owns, so a scan batch needs no copy and storage sees no SQL type.
- The `Datum` API (`get_int`, `set_string`, `set_null`, and so on) is the only way to read or write a value; C++ code that compares `ptr_` with a frame address becomes an API call.
- The `ObIVector` formats are not part of the query batch; the DDL write path chooses its own format.
- Step 2a measures B on the scan+filter+aggregate query. If the handle lookups cost the 1.2x gate, E is the fallback (question 1).

### 4.2 The storage filter trait: option F1

```rust
pub enum FilterNode { And(Vec<FilterNode>), Or(Vec<FilterNode>), White(WhiteFilter), Black(BlackLeaf), Dynamic(DynamicLeaf), Sample(SampleFilter) }
pub struct WhiteFilter { col: u32, op: WhiteOp, params: Vec<OwnedDatum>, cmp: DatumCmpFn, in_set: Option<DatumSet> }
pub struct BlackLeaf { id: BlackFilterId, cols: Vec<u32>, mono: Monotonicity }
pub struct DynamicLeaf { id: DynamicFilterId, cols: Vec<u32>, state: Arc<DynamicFilterState> } // runtime join filter, top-N filter

pub trait ScanHost { // SQL implements it; storage receives it on every scan call
    fn eval_black(&mut self, id: BlackFilterId, cols: &[&DatumVec], bufs: &BufTable, rows: Range<u32>, out: &mut Bitmap) -> ObResult<()>;
    fn judge_black(&mut self, id: BlackFilterId, min: &Datum, max: &Datum, bufs: &BufTable) -> ObResult<BoolMask>;
    fn eval_row_filters(&mut self, row: &RowView) -> ObResult<bool>;
    fn fill_generated_columns(&mut self, row: &mut RowView) -> ObResult<()>;
}
// Aggregate pushdown: AggregateInputSegment (storage implements) and PushdownAggregatePlan and
// PushdownAggregateProgram (SQL implements), one for one with src/share/aggregate/ob_pushdown_aggregate_protocol.h.
```

- Storage owns the walk (AND and OR bitmaps, the skip index, reordering), white filters on encoded data, the BATCH and SINGLE_ROW state machine, and where batches end. `OB_PUSHDOWN_STATUS_CHANGED` becomes a state change inside storage, not an error, as the prior-art read-path note (/Users/colin/obsidian/tech/seekdb/storage/读写路径.md) also proposes; the facts taken from it were re-checked at 834bbee1e.
- SQL builds the tree when the scan opens, from its plan: white filters with their constants evaluated and their comparison functions taken from the datum crate's tables; black leaves as ids into its own list of filter expressions. Because the host is passed on each call, no filter object keeps a reference into the frame.
- Dynamic filters are shared state (an `Arc` with atomics or a mutex), because the producer is another operator or, in PX, another thread.
- Reordering keeps the C++ rule and its timing source. Results do not depend on it; a case whose warnings or errors do would already differ C++ against C++ in 00b.

### 4.3 Rules that keep sort, hash, batch and scan order exact

1. **One `ob_sort`.** The foundation crate holds a line-by-line transcription of `std::sort` and its helpers from the SDK 26.2 libc++ (`__algorithm/sort.h`, and `make_heap.h`, `sort_heap.h`, `sift_down.h`, `pop_heap.h` and `push_heap.h` for the heap-sort fallback and the heap functions), using the plain partition, since `lib::ob_sort` never selects the branchless one. Every `lib::ob_sort`, `std::sort` and heap-function call site uses it. The transcription keeps libc++'s license notice (Apache 2.0 with LLVM exceptions).
2. **OB's own algorithms are translated, not replaced:** `ObAdaptiveQS`, `ObBinaryHeap`, partition sort, prefix sort, unique sort, the in-memory and external merges, the bytes of `ObOrderPerservingEncoder`, and only the scalar byte compare in AQS.
3. **Comparators return what the C++ returns** for every pair, collation and NULL order included. After an error a Rust sort may stop early, because the statement fails and no order after an error is compared.
4. **The choice of algorithm is translated exactly:** `check_can_encode_sortkey` with its thresholds and tracepoint 1200; the top-N, prefix and partition-sort selection; the dump decision. The row store reports the same byte counts to the work-area manager as `ObChunkDatumStore`, so dumps happen at the same rows (family 12 forces them with `ob_sql_work_area_percentage=5`).
5. **Banned in the SQL, optimizer and storage crates**, by clippy's `disallowed-methods` and `disallowed-types`: `sort_unstable*`, `select_nth_unstable*`, `BinaryHeap`, `std::collections::HashMap` and `HashSet` (use the `ObHashMap` port of rule 7, or `BTreeMap` where the C++ has no hash map). `sort_by` (stable) only where the C++ uses `std::stable_sort`.
6. **OB's hash functions and seeds belong to the value libraries:** murmurhash64A, the per-type `murmur_hash_v2` functions with their collation-aware string hashing, and the seeds (16777213 for hash join, 99194853094755497 for hash group-by, 0 for `hash_func<ObString>`).
7. **Hash tables keep their layout:** the hash join table (bucket count formula, mask, linear probing, head insertion, the walk in bucket order, the NULL-key counter); group ids in first-seen order; partition index bits and counts for dumps; the store order of `ObHashPartInfrastructure`; and a port of `ObHashMap` and `ObHashSet` (prime bucket list, `%`, head insertion, growth ratio) used wherever the C++ uses them.
8. **Batch sizes:** `detect_batch_size` is translated exactly, and the batch size used at run time is the one printed as `rowset=`.
9. **Batch ends:** a scan ends a batch at the same storage events (the end of a micro block in the BATCH path, a state switch, the maximum batch size); LIMIT passes `max_row_cnt` the same way; the 16 operators that are not vectorized keep one-row batches. The hash join, which emits one match per probe row per pass over a probe batch (1.8), and the batch loops that stop at the first failing row (1.1) make these ends visible.
10. **Scan order:** the rows of one range in rowkey order, forward or reverse; ranges and multi-get keys sorted with rule 1's `ob_sort` and the same comparator; memtables and sstables merged by rowkey (a simple merger for up to 3 tables, a loser tree above that, src/storage/access/ob_simple_rows_merger.h:30); partitions in the tablet order that DAS and the granule iterator use; tables without a primary key in hidden auto-increment order; index lookups in index order; PX with DOP 1 in granule order. This is the answer to PLAN §8 item 18 from this topic's side.
11. **PX:** DOP 1 is exact through rules 9 and 10 and the transcribed heap in the merge-sort receive. With DOP above 1 order is not reproducible in either build, and such cases follow PLAN §4's quarantine rule (compared only while two C++ recordings agree).
12. **00b's hash-order list** (PLAN §8 item 5) records the mechanism behind each confirmed statement: first-seen order, probe order within a batch, bucket order, dump, or partition sort. Only the last three depend on hash values, which tells Step 6 where to look when a masked statement differs.

### 4.4 What this asks of other design topics

- **Estimator inputs (PLAN §8 item 17).** Storage estimates drive choices the judge sees even with EST numbers masked: the access path (and so the key order of the 8,840 user-table scans), the join and aggregation algorithms, the sort algorithm under the reduced init (1,000 rows), and `rowset=` (16 rows). This report recommends keeping the inputs identical. For memtables that is cheap below 1,000 rows per range, where the estimate is an exact count (`MAX_SAMPLE_ROW_COUNT` is 500 from each end, src/storage/memtable/mvcc/ob_query_engine.h:134, ob_query_engine.cpp:561-633); above that it depends on the key B-tree's `estimate_element_count`. For sstables it depends on the rows per micro and macro block. Dynamic sampling (`optimizer_dynamic_sampling` default 1, ob_system_variable_init.json:2920-2927) samples blocks, so its sampling unit matters too.
- **Rows per micro block.** The same counts set scan batch ends (rule 9). A new on-disk format (Decision 11) can change the byte layout and still keep the rule that decides how many rows go into one micro block.
- **Virtual tables** (202 of the single-table statements) return rows in the order their fill code iterates, a rule for the leaf translation.
- **A side note for 00b.** Mutation 03 patches the `std::sort` comparator and expects alias3 (`order by a2`, an INT column) to catch it, but under the full init.sql tracepoint 1200 sends INT keys to AQS, which never calls that comparator (ob_optimizer_util.cpp:6988-6992). Keys that cannot be encoded, such as VARCHAR, can still catch it; the mutation run will show which case does.

## 5. Questions for the developer

1. **The datum and Decision 14.** Accept the safe handle-based datum (recommended; Step 2a measures its cost against the 1.2x gate), or name a datum crate that may hold raw pointers, beyond the four kinds of crate Decision 14 lists?
2. **Storage estimator inputs (item 17).** Keep the memtable and sstable estimator inputs identical (recommended, because plan choices and `rowset=` depend on them), accepting that this constrains the storage redesign's block sizes, rows per block and key B-tree?
3. **Rows per micro block.** Keep the rule that decides how many rows go into a micro block, so that scan batch ends and hash-join output order still match after a major freeze (recommended together with question 2), or accept differences in those statements, which no mask covers?
4. **Exact hash order everywhere.** This report treats the mask for about 300 statements as a fallback and recommends exact hash order everywhere, because hash order also reaches ORDER BY ties, LIMIT, GROUP_CONCAT and auto-increment values. Confirm, or limit the exact rule to what the mask cannot cover?
5. **The libc++ transcription.** May a Rust transcription of libc++ code (Apache 2.0 with LLVM exceptions) go into the fork, with its notice kept? (Assumption: compatible with the fork's Apache 2.0 license; not checked by a lawyer.)

## Appendix: commands behind the counts

From the worktree root; `F` stands for `--include='*.h' --include='*.cpp' --include='*.ipp'`.

- Frozen base: `git diff --stat 834bbee1e -- src` prints nothing. Removal commits: `git show --stat --format='%H %ad %s' cbcea2b91 | tail -1`, the same for 8f96ded56.
- Eval functions: `grep -rn 'eval_func_ = ' $F src/sql src/query | wc -l` (579), the same for `eval_batch_func_ = ` (93). Internals: the same grep over src/sql for `locate_expr_datum`, `locate_batch_datums`, `locate_datum_for_write`, `get_evaluated_flags`; operator files: `grep -lE 'frames_|locate_batch_datums|locate_expr_datum|locate_datum_for_write|get_evaluated_flags|get_eval_info|get_pvt_skip|reinterpret_cast<ObDatum'` over `find src/sql/engine -name '*_op.cpp' -o -name '*_op.h'` (35 of 156).
- `ObIVector`: `grep -rn 'ObIVector\b' $F src | wc -l` (185), grouped by directory with `sed 's|/[^/]*$||' | sort | uniq -c`.
- Operators: a Python pass over the `REGISTER_OPERATOR(...)` calls in ob_operator_reg.h with `#define` blocks removed (61, 45 with `VECTORIZED_OP`); `grep -rn 'int Ob[A-Za-z]*Op::inner_get_next_batch' --include='*.cpp' src/sql | wc -l` (45), the same for `inner_get_next_row` (62).
- Plan text: `grep -rho 'rowset=[0-9]*' tools/deploy/mysql_test --include='*.result' | sort | uniq -c`; the same with `'dop=[0-9]*'`, with `'PX COORDINATOR'` (`-l`, 14 files), and with `-E '(HASH|MERGE|NESTED-LOOP)[A-Z -]*(JOIN|GROUP BY|DISTINCT|UNION DISTINCT|INTERSECT DISTINCT|EXCEPT DISTINCT)'`.
- Boundary: `grep -rn 'sql::' $F src/storage` (1,070 lines, 99 files with `-l`); the filter executor class names (289 in 41); `-E 'ObEvalCtx|ObExpr\b|locate_batch_datums|locate_expr_datum|locate_datum_for_write|eval_batch\(|frames_|ObBitVector'` (369 in 54).
- Sorts and heaps: `grep -rn 'lib::ob_sort(' $F src | awk -F/ '{print $2}' | sort | uniq -c`; `grep -rn 'std::sort(\|std::stable_sort(\|std::push_heap\|std::pop_heap\|std::make_heap' $F src`.
- Hash maps in src/sql: `foreach_refactored` (26), `-E '(ObHashMap|ObHashSet)<[^;]*>::(const_)?iterator'` per directory (12), `std::unordered_map\|std::unordered_set` (0).
- Single-table split: the script embedded in migration/judge/lists/hash-order-select-candidates.txt with its last loop changed to classify single-table statements by FROM target, WHERE and LIMIT (scratch copy: migration/design/evidence/05-exec-single.py): 9,042 = 202 + 8,840; 1,618 with WHERE; 28 with LIMIT.
- libc++: `_LIBCPP_VERSION 200100` at MacOSX26.2.sdk/usr/include/c++/v1/__config:31.
