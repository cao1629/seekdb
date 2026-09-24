# Execution: the column batch, the storage filter trait, and sort and hash order

This section of the design document turns ARCHITECTURE.md §5 (the column batch and the storage filter trait) and the sorting, hashing and batch bullets of §10 into rules for implementers and core authors. It builds on research reports R05 (migration/design/research/05-execution.md) and R09 (09-numerics.md) and follows ARCHITECTURE.md; section 10 lists the objections it raised against ARCHITECTURE.md, each with its settlement (RESOLUTIONS.md s5-N). The number, float, overflow and profile rules of §10 and the scan order of §6.2 belong to other sections; this one refers to them.

- Code facts are at the frozen base 834bbee1e (`git diff --stat 834bbee1e -- src` prints nothing); paths are from the repository root. Every added fact carries file:line or its command; "assumption" marks what was not checked.
- "Must" rules are numbered by part (rule 2.3). Each part ends with what a reviewer checks.
- All crates named here are `#![forbid(unsafe_code)]` except ob-simd (ARCHITECTURE §8).

## 1. Where each type lives and what it is called

| Rust item | Crate | Replaces |
|---|---|---|
| `ObDatum`, `DatumVec`, `ObDatumVector`, `ObBitVector`, `BufTable`, `BufId`, `SharedBytes` | ob-values | `ObDatum` and `ObDatumVector` (src/oblib/common/datum/ob_datum.h:106-415, :417), `ObBitVector` (src/share/vector/ob_bit_vector.h:195), the byte memory of `ObEvalCtx::frames_` |
| datum compare and hash function tables | ob-values | src/share/datum |
| `ObExpr` (the plan's form), `EvalFrame`, `ExprSlot`, `ObEvalInfo`, `ObBatchRows`, the `ObOperator` trait, `ObChunkDatumStore`, `ObTempRowStore` | sql-exec (`ObExpr`'s file, src/sql/engine/expr/ob_expr.cpp, would fall to sql-expr by its directory; a map row sends it here, since `EvalFrame` names `ObExpr` and Rust allows `impl ObExpr` only in its own crate) | src/query/api/query/engine/expr/ob_expr.h:130-715, src/query/api/query/engine/ob_operator.h, the chunk stores |
| `ObPushdownFilterNode`, `ObPushdownFilterExecutor`, `ObWhiteFilterParam`, `ObBitmap`, `ObSqlDatumInfo`, `ScanHost`, `ScanColumns`, `ObVTableScanParam`, the aggregate protocol traits | storage-api | src/query/api/query/engine/basic/ob_pushdown_filter.h, src/data_plane/api/data_plane/access/ob_tablet_scan.h, src/share/aggregate/ob_pushdown_aggregate_protocol.h |
| `ob_sort`, `make_heap`, `push_heap`, `pop_heap`, `sort_heap`, `ObBinaryHeap`, `ObHashMap`, `ObHashSet`, `murmurhash64a`, `murmurhash2`, `fnv_hash2` | ob-base, `sort` and `hash` modules (ARCHITECTURE §14 rule 7) | src/oblib/lib/utility/ob_sort.h, the SDK's libc++ headers, src/oblib/lib/container/ob_heap.h, src/oblib/lib/hash, src/oblib/lib/hash_func/murmur_hash.{h,cpp} |
| `ObSortOpImpl`, `ObAdaptiveQS`, the hash join and hash group-by tables, `ObHashPartInfrastructure` | sql-engine | src/sql/engine/{sort,join,aggregate,basic} |
| white-filter kernels on encoded data | the C++ files stay in storage-sstable, where the map sends src/storage/blocksstable; they are core-build inputs whose encodings are free (ARCHITECTURE §6.1), so a NEON kernel exists only for an encoding the Rust sstable keeps, and then as a free function over slices in ob-simd with its scalar version, registered by storage-sstable | src/storage/blocksstable/encoding/neon (568 lines, `wc -l`): kernels over the C++ dict and raw encodings, registered into the decoders' tables (src/storage/blocksstable/encoding/ob_dict_decoder.cpp:166-189; ob_raw_decoder.cpp:218-240) and taking `sql::PushdownFilterInfo` and `ObBitVector` |

**Rule 1.1.** Names follow ARCHITECTURE §14 rule 3: a Rust type that replaces one C++ type keeps its C++ name; `EvalFrame`, `ExprSlot`, `DatumVec`, `BufTable`, `BufId`, `SharedBytes`, `ScanHost`, `ScanColumns` and the filter ids (`BlackFilterId`, `WhiteFilterId`, `DynamicFilterId`) have no single counterpart and get plain names.

## 2. The datum and the column batch (ob-values)

```rust
#[derive(Clone, Copy)]
#[repr(C)]
pub struct ObDatum { loc: [u8; 8], desc: u32 }          // 12 bytes, alignment 4

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct BufId(u32);

pub struct DatumVec { datums: Vec<ObDatum> }            // max_batch_size datums, or 1
pub struct ObDatumVector<'a> { datums: &'a [ObDatum], mask: usize }  // at(i) = datums[i & mask]
pub struct ObBitVector { words: Vec<u64> }

pub struct SharedBytes { bytes: Box<[u8]>, owner: Option<Box<dyn Any + Send + Sync>> }
pub enum Buf { Owned(Vec<u8>), Shared(Arc<SharedBytes>), Free }
pub struct BufTable { entries: Vec<Buf>, free: Vec<u32> }

impl BufTable {
    pub fn register(&mut self, b: Buf) -> BufId;
    pub fn release(&mut self, id: BufId);
    pub fn bytes<'a>(&'a self, d: &'a ObDatum) -> &'a [u8];         // inline bytes, or the entry's range
    pub fn split_mut(&mut self, id: BufId) -> (&mut Vec<u8>, BufView<'_>); // one entry writable, the rest readable
    pub fn copy_into(&mut self, dst: BufId, off: u32, src: &ObDatum) -> ObResult<()>; // copy_within when src is in dst
}
```

**Rule 2.1.** `ObDatum` is 12 bytes with 4-byte alignment and no `packed` attribute; a `const` assertion checks the size. `desc` holds the C++ `pack_` bit for bit: `len_` in bits 0-28, `flag_` (NONE, OUTROW, EXT, HAS_LOB_HEADER) in bits 29-30, `null_` in bit 31 (ob_datum.h:134-165; clang's bitfield order on arm64, confirmed by a differential test). `loc` is `[u8; 8]`, the 8 value bytes ARCHITECTURE §5 names, read and written as little-endian.

**Rule 2.2.** A datum is inline when it is not null, its flag is NONE and its length is 8 or less: `loc[..len]` holds the value and the rest is zero. Every other non-null datum is a reference: `loc` holds a `BufId` and an offset as two little-endian `u32`. One constructor, `ObDatum::from_bytes(id, off, bytes)`, chooses between the two, so no code builds a reference to 8 bytes or fewer. The rule needs no type: an INT, a DATE, a 4-byte decimal-int, a 3-byte VARCHAR and the ObNumber zero (a 4-byte descriptor) are inline; a 40-byte ObNumber, a 16-byte decimal-int, a long string and every datum with a flag (EXT, OUTROW, HAS_LOB_HEADER) are references.

**Rule 2.3.** The datum API is the only access (ARCHITECTURE §5). Getters and setters keep the C++ names (`get_int`, `get_uint64`, `get_double`, `set_int`, `set_null`, `set_string`, `is_null`). Getters for values of 8 bytes or fewer read `loc` (`get_int` is `i64::from_le_bytes(loc)`) and panic on a reference datum, where the C++ would read through the pointer. Longer values (strings, numbers, decimal-ints, LOBs) are read through the table: `bufs.bytes(&d)`. `binary_equal` compares `desc`, then the bytes (ob_datum.h:185-198).

**Rule 2.4.** A batch-result expression has `max_batch_size` datums and index mask `u64::MAX`; a non-batch expression has one datum and mask 0 (`batch_idx_mask_`, ob_expr.h:711). New datums are zeroed (length 0, not null, flag NONE), the state `clear_datum_descriptors` gives slots a batch did not write (src/sql/engine/expr/ob_expr.cpp:583-600).

**Rule 2.5.** `ObBitVector` is `u64` words with bit i in word i/64 at bit i%64, the C++ layout on a little-endian target (ob_bit_vector.h:232-246). Where the C++ reads the words as `uint16_t` or `uint8_t` (`reinterpret_data<uint16_t>`, src/sql/engine/expr/ob_batch_eval_util.h:61-62), Rust uses word helpers (`u16_at`, `set_u16_at`, `to_bits_mask`), never a byte view. Storage's `ObBitmap` keeps one byte per row (src/oblib/lib/container/ob_bitmap.h:170).

**Rule 2.6.** A `BufTable` belongs to one `EvalFrame` (part 3); a scan that storage runs for itself (compaction, DDL) owns its own table. Entry 0 holds the plan's constants and entry 1 the execution's parameters, both registered when the frame is built. Every other entry has one producer: an expression slot (its reserved buffer and dynamic buffers, kept for the run), a scan (the blocks of its current batch), or an operator reading a row store or a PX channel (the blocks of its current batch).

**Rule 2.7.** A datum is valid until its producer's next batch (ARCHITECTURE §5). A producer releases the entries it registered for a batch when it starts the next one, and before it resets or reuses a store whose blocks they hold. Release builds reuse released ids; debug and `checked` builds never do, so a stale datum panics there instead of reading another buffer. No build reads freed memory.

**Rule 2.8.** Shared bytes are `Arc<SharedBytes>`; `owner` holds what must live exactly as long as the bytes, such as a cache handle or a budget charge (ARCHITECTURE §3.1 rule 9, §3.2), and drops with the last `Arc`. Registering a storage block is an `Arc` clone, so a scan copies no strings, as today (src/storage/access/ob_vector_store.cpp:306-352).

**Rule 2.9.** The `ObIVector` formats (src/query/api/query/engine/vector/ob_i_vector.h:372) are not part of the query batch (ARCHITECTURE §5): since cbcea2b91 only the DDL and direct-load write path and PX sstable insert use them (R05 §1.2), and they move to storage-api with that path.

**A reviewer checks:** no `unsafe`, `transmute` or byte view of an `ObBitVector`; every reference datum comes from `from_bytes`; every producer that registers entries releases them at its next batch and before a store reset; code that keeps a value past its producer's next batch copies the bytes into a store it owns.

## 3. How expressions read their arguments and write their results (sql-exec)

```rust
pub type EvalFunc = fn(expr: &ObExpr, frame: &mut EvalFrame<'_>) -> ObResult<()>;
pub type EvalBatchFunc =
    fn(expr: &ObExpr, frame: &mut EvalFrame<'_>, skip: &ObBitVector, size: usize) -> ObResult<()>;

pub struct ObExpr {                          // in the plan: immutable, Send + Sync
    pub type_: ObExprOperatorType,
    pub datum_meta_: ObDatumMeta,
    pub obj_meta_: ObObjMeta,
    pub obj_datum_map_: ObObjDatumMapType,
    pub flag_: u64,                          // the union's bits: batch_result_(), is_static_const_(), need_stack_check_(), ...
    pub eval_func_: Option<EvalFunc>,
    pub eval_batch_func_: Option<EvalBatchFunc>,
    pub inner_functions_: Box<[InnerFunc]>,  // void **inner_functions_, one enum variant per pointer type
    pub args_: Box<[ExprIdx]>,               // arg_cnt_ is args_.len()
    pub parents_: Box<[ExprIdx]>,
    pub slot: SlotIdx,                       // no C++ counterpart: replaces frame_idx_, datum_off_, eval_info_off_, res_buf_off_, ...
    pub res_buf_len_: u32,
    pub batch_idx_mask_: u64,
    pub expr_ctx_id_: u32,
    pub extra_: u64,
    pub extra_info_: Option<Box<dyn ObIExprExtraInfo>>,
}

pub struct EvalFrame<'r> {                   // one per run of a plan, one per PX worker
    pub exec_ctx: &'r mut ObExecContext,
    exprs: &'r [ObExpr],
    slots: Vec<ExprSlot>,
    bufs: BufTable,
    expr_ctx: Vec<Option<Box<dyn ObExprOperatorCtx>>>,
    tmp: ObArenaAllocator,                   // tmp_alloc_; counted by the request's tracker on a request thread, uncounted in a PX worker (s3-memory.md T2, H4)
    batch: BatchInfo,                        // batch_idx_, batch_size_, max_batch_size_
}

pub struct ExprSlot {
    datums: DatumVec,
    eval_flags: ObBitVector,
    pvt_skip: ObBitVector,
    info: ObEvalInfo,                        // evaluated_, projected_, notnull_, point_to_frame_, cnt_
    res_buf: BufId,                          // res_buf_len x max_batch_size bytes
    dyn_bufs: Vec<Option<BufId>>,            // ObDynReserveBuf, one per datum index
}
```

Fields that stand for C++ members keep the members' names, trailing `_` included (src/query/api/query/engine/expr/ob_expr.h:630-715), so translated expression code reads `expr.datum_meta_` as the C++ reads `expr.datum_meta_`; `slot`, which no C++ member matches, takes a plain name (RULEBOOK section 4, "Identifiers").

**Rule 3.1.** `EvalFrame` replaces `ObEvalCtx`, the frames it points at and the per-run parts of `ObExecContext` that expressions use. The plan, its `ObExpr`s and its constants are shared as `Arc` by PX workers, each building its own frame, so the SQC arguments are no longer encoded and decoded inside one process (src/sql/engine/px/ob_px_local_sqc_launcher.cpp:431) and function pointers are no longer serialized as addresses (src/query/api/query/engine/ob_serializable_function.h:52-80). State the C++ writes into the shared plan, or into a worker's decoded copy of it, moves into the frame or the operator.

**Rule 3.2.** The evaluation state machine is translated statement for statement: `ObExpr::eval` and `eval_batch` (ob_expr.h:1039-1094), `eval_one_datum_of_batch`, `do_eval_batch` and `expr_default_eval_batch_func` (ob_expr.cpp:612-761). In particular a failed datum or batch is set to NULL; the first evaluation in a batch resets the evaluated flags and clears the descriptors; the default batch function stops at the first failing row and still sets that row's evaluated flag; `eval_batch` of a non-batch expression over an all-skipped batch drops the error (ob_expr.h:1078-1086); `notnull_` follows the same assignments. Which row's error a statement reports depends on these steps and on where batches end (part 4).

**Rule 3.3.** A result goes where the C++ puts it, with the C++ sizes: the reserved buffer when its length is at most `res_buf_len`, otherwise the dynamic buffer of its datum index, grown to `next_pow2(size)` (`alloc_str_res_mem`, ob_expr.cpp:286-315). Where the C++ returns no memory, above `u32::MAX` or for a type without dynamic buffers, Rust returns the error the C++ caller then sets, `OB_ALLOCATE_MEMORY_FAILED` (ob_expr.h:1115-1116); a failed allocation itself aborts (ARCHITECTURE §3.2). A dynamic buffer keeps its old bytes at the same offsets when it grows and is not shrunk before the run ends; the C++ keeps the old block alive for the same reason, a source still pointing into it (ob_expr.cpp:304-305). `ObExprStrResAlloc` (52 lines) becomes a bump over that memory. A result of 8 bytes or fewer touches no buffer (rule 2.2).

**Rule 3.4.** Temporaries go into `tmp`, which is reset between batches (ARCHITECTURE §3.1 rule 3). No result goes there, as the C++ comment on `tmp_alloc_` says (ob_expr.h:279-281).

**Rule 3.5.** Arguments are read and results written through splits of the frame, never through two live borrows of one thing: `frame.split(expr)` lends the expression's slot mutably and the other slots and the buffer table immutably, for reading argument datums and writing result datums; `BufTable::split_mut(id)` lends one entry mutably and the rest immutably, for writing result bytes while reading source bytes. A source inside the entry being written is read through that same `&mut` with `copy_within`, which is what `MEMMOVE` does. The C++ marks such overlap at src/sql/engine/expr/ob_datum_cast.cpp:487 and ob_expr.h:1108, and each of the 17 `MEMMOVE` lines under src/sql/engine/expr (`grep -rn MEMMOVE src/sql/engine/expr`) gets an inventory row.

**Rule 3.6.** Argument values are copied out as `ObDatum`s (`eval_param_value` returns `[ObDatum; N]`); no `&ObDatum` or `&[u8]` borrowed from the frame lives across a call that takes `&mut EvalFrame`.

**Rule 3.7.** `ObEvalInfo::point_to_frame_` keeps its set and clear sites (22 lines, `grep -rn point_to_frame_ src`); its only reader is the x86 AVX-512 branch of `ObBlackFilterExecutor::eval_exprs_batch` (src/sql/engine/basic/ob_pushdown_filter.cpp:2285-2286), which never runs on arm64.

**Rule 3.8.** `ObEvalCtx::BatchInfoScopeGuard` (198 lines, `grep -rn BatchInfoScopeGuard src/sql src/query src/storage`) becomes a guard over the frame's batch index and sizes that restores them when dropped. Storage's direct `set_batch_idx` calls (21 lines under src/storage), which it makes as a friend class because it does not use the guard (ob_expr.h:168, :244-250), become `ScanHost::set_batch_idx`.

### Example: the arithmetic batch loop

`ObDoArithBatchEval` steps 16 rows at a time over the skip and evaluated-flag words and stops at the first failing row. BIGINT addition adds first and then tests the sign bits (ob_expr_add.cpp:547-570).

```cpp
// src/sql/engine/expr/ob_batch_eval_util.h:59-85 (abridged)
for (int64_t i = 0; i < size && OB_SUCC(ret);) {
  const int64_t bit_vec_off = i / step_size;                       // step_size = 16
  const uint16_t skip_v = skip.reinterpret_data<uint16_t>()[bit_vec_off];
  uint16_t &eval_v = eval_flags.reinterpret_data<uint16_t>()[bit_vec_off];
  if (i + step_size < size && (0 == (skip_v | eval_v))) {
    for (int64_t j = 0; OB_SUCC(ret) && j < step_size; i++, j++) {
      batch_info_guard.set_batch_idx(i);
      ret = ArithOp::datum_op(*res.at(i), *left.at(i), *right.at(i), args...);
      desc.pack_ |= res.at(i)->pack_;
    }
    if (OB_SUCC(ret)) { eval_v = 0xFFFF; }
  } else if (i + step_size < size && (0xFFFF == (skip_v | eval_v))) {
    i += step_size;
  } else { /* row by row: eval_flags.bit_or_assign(i, OB_SUCCESS == ret) */ }
}
// ob_expr_add.cpp:549-568: raw_op is `res = l + r;` raw_check is is_int_int_out_of_range(l, r, res)
```

```rust
let s = frame.split(expr);                       // s.res: &mut ExprSlot; s.args: read-only view
let (left, right) = (s.args.datum_vector(expr.args_[0]), s.args.datum_vector(expr.args_[1]));
let mut i = 0;
while i < size && ret.is_ok() {
    let off = i / STEP;                          // STEP = 16
    let skip_v = skip.u16_at(off);
    let eval_v = s.res.eval_flags.u16_at(off);
    if i + STEP < size && (skip_v | eval_v) == 0 {
        let mut j = 0;
        while ret.is_ok() && j < STEP {
            s.batch.set_batch_idx(i);
            ret = Op::datum_op(&mut s.res.datums[i], left.at(i), right.at(i));
            desc |= s.res.datums[i].desc();
            i += 1;
            j += 1;
        }
        if ret.is_ok() { s.res.eval_flags.set_u16_at(off, 0xFFFF); }
    } else if i + STEP < size && (skip_v | eval_v) == 0xFFFF {
        i += STEP;
    } else { /* row by row, as the C++ */ }
}

// ObIntIntBatchAddRaw, for two non-null operands:
let (l, r) = (l.get_int(), r.get_int());
let v = l.wrapping_add(r);                       // the C++ adds first ...
out.set_int(v);                                  // inline: no buffer
if ObExprAdd::is_int_int_out_of_range(l, r, v) { // ... then tests the sign bits
    // databuff_printf "'(%ld + %ld)'" and log_user_error!(OB_OPERATE_OVERFLOW, "BIGINT", ...), as the C++
}
```

What stays: the 16-row steps, the flag words, the stop at the first failing row, the NULL tracking through `desc`. What changes: pointer arithmetic becomes slot indexing, the write through `res.ptr_` becomes `set_int`, the signed add becomes `wrapping_add` (ARCHITECTURE §10).

### Example: a copy whose source may be the destination

```cpp
// src/query/api/query/engine/expr/ob_expr.h:1105-1126 (abridged)
// shadow copy datum first, because %datum may overlay with %dst
common::ObDatum src = datum;
ObDatum &dst = this->locate_datum_for_write(ctx);
dst.pack_ = src.pack_;
if (!src.null_) {
  if (OB_UNLIKELY(src.len_ > res_buf_len_)) {
    if (OB_ISNULL(dst.ptr_ = get_str_res_mem(ctx, src.len_))) { ret = OB_ALLOCATE_MEMORY_FAILED; }
    else { MEMMOVE(const_cast<char *>(dst.ptr_), src.ptr_, src.len_); }
  } else {
    MEMMOVE(const_cast<char *>(dst.ptr_), src.ptr_, src.len_);
  }
}
```

```rust
pub fn deep_copy_datum(&self, frame: &mut EvalFrame<'_>, src: ObDatum) -> ObResult<()> { // src: a copy, as in C++
    let idx = frame.datum_idx(self);
    if src.is_null() || src.is_inline() {
        frame.slot_mut(self).datums[idx] = src;             // the value travels inside the datum
        return Ok(());
    }
    let (dst, off) = frame.str_res_mem(self, idx, src.len())?; // reserved or dynamic buffer; Err(OB_ALLOCATE_MEMORY_FAILED) where C++ gets NULL
    frame.bufs_mut().copy_into(dst, off, &src)?;            // copy_within when src lies in dst
    frame.slot_mut(self).datums[idx] = ObDatum::reference(src.desc(), dst, off);  // pack_ from src
    Ok(())
}
```

**A reviewer checks:** each eval function keeps the C++ statements in the C++ order, with `?` only where the C++ is a plain `OB_FAIL` chain (ARCHITECTURE §2); results go to the reserved or dynamic buffer with the C++ sizes and nowhere else; every `MEMMOVE` or overlap site uses `copy_within` or copies the source first and has its inventory row; no frame borrow outlives a `&mut EvalFrame` call; error paths set NULL exactly where the C++ does.

## 4. Operators, batch sizes and where batches end

```rust
pub struct ObBatchRows {                     // src/query/api/query/engine/ob_operator.h, one per operator
    pub size_: i64,
    pub end_: bool,
    pub all_rows_active_: bool,
    pub skip_: ObBitVector,                  // the operator's own skip vector
}

pub trait ObOperator: Send {
    fn inner_open(&mut self, frame: &mut EvalFrame<'_>) -> ObResult<()>;
    fn inner_get_next_batch(&mut self, frame: &mut EvalFrame<'_>, max_row_cnt: i64) -> ObResult<()>;
    fn inner_rescan(&mut self, frame: &mut EvalFrame<'_>) -> ObResult<()>;
    fn inner_close(&mut self, frame: &mut EvalFrame<'_>) -> ObResult<()>;
    /// ObOperator::get_next_batch (src/sql/engine/ob_operator.cpp:1016-1157), written once; fills brs_.
    fn get_next_batch(&mut self, frame: &mut EvalFrame<'_>, max_row_cnt: i64) -> ObResult<()>;
    /// The batch the last get_next_batch produced, what `const ObBatchRows *&` hands out today:
    /// the `brs_` among the base fields (ob_operator.h:579-605) that base() reaches (RULEBOOK 2.3).
    fn brs(&self) -> &ObBatchRows;
}
```

**Rule 4.1.** `get_next_batch` is the entry ARCHITECTURE §5 names, under the C++ wrapper's name. It is a provided method of the trait, written once, and keeps the C++ wrapper's steps in order: the stack check, the startup filter, `min(max_row_cnt, max_batch_size)`, stashed rows first, the retry while a batch is all skipped, `try_check_status_by_rows`, `filter_rows`, projection of the output expressions, the stash when an operator produced more than `max_row_cnt` rows (ob_operator.cpp:970-1014; ob_operator.h:734-746), and `batch_reach_end_`, which returns a last non-empty batch with `end` false and ends on the next call. Operator bodies are `inner_get_next_batch` under the C++ name.

**Rule 4.2.** The 16 registered operators that are not vectorized (R05 §1.4) keep one-row batches through `get_next_batch_with_onlyone_row` (ob_operator.h:525-552).

**Rule 4.3.** The batch belongs to the operator that produced it: `get_next_batch` returns nothing borrowed and fills the operator's own `brs_`, as the C++ wrapper does before it hands out `const ObBatchRows *&` (ob_operator.h:416). A caller reads a child's batch through `child.brs()` at each use, copying `size_`, `end_` and `all_rows_active_` into locals where the C++ reads them repeatedly, and never keeps a borrow of it across a call that takes `&mut self`, `base_mut()` or the frame. Operators and their helpers keep a pointer to a batch across their own calls on 11 members (`grep -rn 'const ObBatchRows \*[a-z_]*_;' src/sql/engine --include='*.h'`: hash join's `right_brs_`, ob_hash_join_op.h:1231; nested-loop join's `left_brs_`, ob_nested_loop_join_op.h:204; subplan filter, the merge set operators, hash set, hash group-by's bypass, the sort and the aggregate processor). Such a member names whose `brs_` it reads, not the batch: a small enum with one value per child it can point at and one for a batch the operator owns, read through one accessor that matches it. Hash join's substitution, which points `right_brs_` at its own `child_brs_` and then writes its size and end flag through a `const_cast` (src/sql/engine/join/ob_hash_join_op.cpp:3365-3368), sets the enum to the operator's own batch and writes `self.child_brs_` directly. A caller that needs rows after the child's next call copies them into a row store charged to the work area (ARCHITECTURE §3.1 rule 3).

**Rule 4.4.** A row store keeps a stored row's cells as offsets from the row's start, the form `ObChunkDatumStore` gives them before writing a block to disk (`unswizzling`, src/sql/engine/basic/ob_chunk_datum_store.cpp:132-145); reading rows back registers the block in the frame (rule 2.6). A stored row keeps the C++ sizes: the row header, 12 bytes per cell, then the payload (src/sql/engine/basic/ob_chunk_datum_store.h:104-144), so the memory a store reports is the C++ figure (rule 6.9).

**Rule 4.5.** `detect_batch_size` is translated exactly (src/sql/code_generator/ob_code_generator.cpp:85-170; ob_static_engine_expr_cg.cpp:95-166): 0 when a child statement assigns user variables; when the root statement assigns them or the plan cannot be vectorized, 1 if the plan has a registered vectorized operator and 0 if not; 1 or 8 when an expression asks for it, such as a non-deterministic UDF (`get_expr_execute_size`, ob_static_engine_expr_cg.cpp:1406-1457; ob_static_engine_expr_cg.h:413-417); otherwise `_rowsets_max_rows` (256, src/share/parameter/ob_parameter_seed.ipp:293) lowered to each expression's maximum and, if lowered, rounded down to a power of two (a size computed from `_rowsets_target_maxsize` when `_rowsets_max_rows` is 0), capped at 16 when the largest estimated scan cardinality is below 16 (`SMALL_SCAN_CARDINALITY`, ob_static_engine_expr_cg.h:75, :145); then the `ROWSETS_MAX_ROWS` hint, then tracepoint 2206's random size. The size the run uses is the size `rowset=` prints (src/sql/optimizer/ob_explain_log_plan.cpp:56).

**Rule 4.6.** Tracepoints that move batch ends or pick algorithms keep their numbers and effects (ARCHITECTURE §7.1 keeps the registry): 311 `EN_DAS_SIMULATE_GROUP_SIZE`, the DAS group size of nested-loop and subplan-filter rescans (src/sql/engine/join/ob_nested_loop_join_op.cpp:69; set by tools/deploy/init.sql:26); 1200 and 2501 (part 6); 2206 (src/oblib/lib/utility/ob_tracepoint_def.h:375).

**Rule 4.7.** A batch ends where the C++ ends it: in storage's BATCH path at the end of a micro block (`set_end()`, ob_vector_store.cpp:339-340), at a switch between the SINGLE_ROW and BATCH states (`ScanState`, src/storage/access/ob_multiple_merge.h:43-48), at the `max_row_cnt` LIMIT passes down (src/sql/engine/basic/ob_limit_op.cpp:223-231), and at the wrapper's stash (rule 4.1). How many rows a micro block holds is free (ARCHITECTURE §6.1), so micro-block ends can move after a freeze; 00b's micro-block mutation measures what that exposes.

**Rule 4.8.** Batch ends reach output: the hash join emits each probe row's next match in turn across a probe batch (src/sql/engine/join/ob_hash_join_op.cpp:4946-4965), and every batch loop stops at its first failing row (rule 3.2). A change that moves a batch end is a behavior change.

**Rule 4.9.** A C++ `sizeof` inside a formula that decides a batch size, a table size, a dump or a bypass is the C++ number, held in a named constant and checked by the differential tests, never `size_of` of the Rust type. `compute_max_batch_size` and `get_expr_datum_fixed_header_size` use `sizeof(ObDatum)` (12), `sizeof(ObEvalInfo)` and `MAX_FRAME_SIZE` (src/sql/code_generator/ob_static_engine_expr_cg.cpp:1745-1755; ob_static_engine_expr_cg.h:398-400); the hash join's bucket count uses `sizeof(HTBucket)`, 16 bytes (src/sql/engine/join/ob_hash_join_op.h:333-345; ob_hash_join_op.cpp:1344); the hash group-by bypass compares memory sizes with cache sizes (src/sql/engine/aggregate/ob_adaptive_bypass_ctrl.cpp:40-49).

**A reviewer checks:** operators implement `inner_get_next_batch` and never rewrite the wrapper; a child's batch is read through `brs()` at each use and no borrow of it outlives a call, and each kept `const ObBatchRows *` member is the enum of rule 4.3; `detect_batch_size` matches the C++ line for line and the run uses the printed size; no decision formula uses `size_of`; nothing moves a batch end.

## 5. What storage owns in a scan and what it asks SQL for (storage-api)

| | Storage | SQL |
|---|---|---|
| Filter tree | the nodes (kept in the plan), the executors, the AND/OR walk over byte maps, the skip index, reordering, where batches end | builds the nodes at code generation (`ObPushdownFilterConstructor`) |
| White filters (and the JSON-path and prepared dynamic variants) | run them, on encoded data where the format allows | evaluates their constants when storage asks |
| Black filters, row filters, generated columns | decodes their input columns into the caller's slots, then asks the host | evaluates the expressions |
| Sample and truncate-partition filters | all of it | nothing |
| Aggregate pushdown | drives consume, seal and emit | implements the plan and the program |
| Output | fills the caller's datums and registers blocks | owns the frame |

```rust
pub enum ObPushdownFilterNode {                         // PushdownFilterType, ob_pushdown_filter.h:99-114
    And(Vec<ObPushdownFilterNode>, bool /* is_runtime_filter_root_node_ */),
    Or(Vec<ObPushdownFilterNode>),
    White(ObPushdownWhiteFilterNode),                   // column, WHITE_OP_EQ..WHITE_OP_NN, WhiteFilterId, JSON path
    Black(ObPushdownBlackFilterNode),                   // BlackFilterId, column ids, PushdownFilterMonotonicity
    Dynamic(ObPushdownDynamicFilterNode),               // DynamicFilterId, JOIN_RUNTIME_FILTER or PD_TOPN_FILTER
    Sample(ObSampleFilterNode),
}

pub trait ScanHost {
    fn batch_size(&self) -> usize;
    fn set_batch_idx(&mut self, idx: usize);                              // ObEvalCtx::set_batch_idx
    fn output_columns(&mut self) -> ScanColumns<'_>;                       // ObVectorStore::datum_infos_
    fn filter_columns(&mut self, id: BlackFilterId) -> ScanColumns<'_>;    // get_datums_from_column
    fn filter_batch(&mut self, id: BlackFilterId, skip: &mut ObBitVector, size: usize) -> ObResult<()>;
    fn filter_row(&mut self, id: BlackFilterId, row: &[ObDatum]) -> ObResult<bool>;      // datums in the frame's table (rule 5.11)
    fn judge_greater_or_less(&mut self, id: BlackFilterId, bound: &ObDatum, is_greater: bool) -> ObResult<bool>;
    fn eval_white_params(&mut self, id: WhiteFilterId, out: &mut ObWhiteFilterParam) -> ObResult<bool>;
    fn prepare_dynamic_filter(&mut self, id: DynamicFilterId, out: &mut ObWhiteFilterParam)
        -> ObResult<Option<DynamicFilterAction>>;
    fn update_dynamic_filter(&mut self, id: DynamicFilterId, version: i64, out: &mut ObWhiteFilterParam)
        -> ObResult<bool>;
    fn filter_row_outside(&mut self) -> ObResult<bool>;                   // ObOperator::filter_row_outside
    fn fill_virtual_columns(&mut self, nop_pos: &[u32]) -> ObResult<()>;
    fn aggregate_columns(&mut self) -> ScanColumns<'_>;
}

pub struct ObSqlDatumInfo<'a> {                         // ob_pushdown_filter.h:64-83, without the ObExpr
    pub datums: &'a mut DatumVec,
    pub bufs: &'a mut BufTable,
    pub res_buf: BufId,
    pub res_buf_len: u32,
    pub map: ObObjDatumMapType,
}
impl ScanColumns<'_> {
    pub fn col(&mut self, i: usize) -> ObSqlDatumInfo<'_>;
    pub fn register(&mut self, block: Arc<SharedBytes>) -> BufId;          // rules 2.6-2.8
    pub fn release(&mut self, id: BufId);
}
```

**Rule 5.1.** Storage owns the walk: `ObPushdownFilterExecutor::execute`, `execute_skipping_filter` and `do_filter` (src/sql/engine/basic/ob_pushdown_filter.cpp:1370-1550) move into storage-api with their byte-map arithmetic and their early exits (AND stops when all rows are false, OR when all are true). Storage takes the host on every scan call; no filter object keeps a borrow of the frame between calls. storage-api and the storage crates use no SQL crate.

**Rule 5.2.** Storage calls SQL at the granularity the C++ does: a batch where the C++ runs `ObBlackFilterExecutor::filter_batch` (src/storage/blocksstable/encoding/ob_micro_block_decoder.cpp:1673-1708 into ob_pushdown_filter.cpp:2308-2343); one decoded row where it runs `filter` on a row (ob_micro_block_decoder.cpp:1476-1550); one block bound where the skip index calls `judge_greater_or_less` (ob_pushdown_filter.cpp:2182-2221); one row for `filter_row_outside` and `fill_virtual_columns` (src/storage/access/ob_multiple_merge.cpp:1254-1300). ARCHITECTURE §5 rejects per-row callbacks in place of batches, not these per-row calls the C++ already makes.

**Rule 5.3.** Before a black filter call, storage decodes the filter's columns into the slots `filter_columns` lends, as the C++ decodes into the column expressions' batch datums (`get_datums_from_column`, ob_pushdown_filter.cpp:2223-2234). Result byte maps, skip bits and reorder statistics are storage objects.

**Rule 5.4.** White filters: storage asks for the constants where the C++ evaluates them, at executor init (`init_evaluated_datums`, `init_compare_eval_datums` and `init_in_eval_datums`, ob_pushdown_filter.cpp:1842-1980), and keeps owned copies. That covers the IN set and the case the C++ comment describes, where a constant such as `1/0` does not evaluate and the filter is not pushed down. Compare functions come from ob-values' tables for the column and constant types; IN sets hash with the C++ functions (part 7). A white filter on encoded data returns the byte map that comparing each decoded datum with the same compare function gives; a test checks this for every encoding, and each NEON kernel against its scalar version.

**Rule 5.5.** Dynamic filters get their data through `prepare_dynamic_filter` and `update_dynamic_filter` where `check_runtime_filter` asks for it (ob_pushdown_filter.cpp:2415-2450), with the three `DynamicFilterAction`s and `dynamic_disable()` (ob_pushdown_filter.h:141-146, :1240-1347); once prepared they run as white filters. The data comes from the range, IN and top-N messages (`prepare_storage_white_filter_data`, src/sql/engine/px/p2p_datahub/ob_runtime_filter_msg.cpp:998, :1422; ob_pushdown_topn_filter_msg.cpp:308).

**Rule 5.6.** Reordering keeps `ObWhereOptimizer`'s rule: first after one batch, then every 32 (`REORDER_FILTER_INTERVAL`, src/storage/access/ob_where_optimizer.cpp:24, :160-177). Its cost counter reads a safe monotonic clock from ob-base (`std::time::Instant` on native targets) instead of `cntvct_el0` (src/oblib/lib/time/ob_tsc_timestamp.h:89-99): inline assembly is `unsafe`, no named crate carries a cycle counter, and the order reordering picks depends on timing in the C++ too (R05 §1.5).

**Rule 5.7.** Sample filters (src/storage/access/ob_sample_filter.h) and the truncate-partition filter are storage executors that never call the host. Today storage injects the truncate filter as an "external" executor inside a stand-in SQL runtime (`create_external_pushdown_filter_runtime`, src/storage/truncate_info/ob_truncate_partition_filter.cpp:279; ob_pushdown_filter.cpp:77-104); that stand-in goes.

**Rule 5.8.** Output: storage writes each output column through `output_columns`: values of 8 bytes or fewer inline, other fixed-length values (numbers, decimal-ints) copied into the slot's reserved buffer as the C++ requires (ob_vector_store.cpp:111-113 fails with `OB_ERR_SYS` otherwise), variable-length values as references into the block it registered. The `ObVTableScanParam` fields that carry SQL objects (`output_exprs_`, `op_`, `pd_storage_filters_`, ob_tablet_scan.h:442-448) become the node tree, the ids and the host.

**Rule 5.9.** Storage-internal codes stay values inside storage (ARCHITECTURE §2): `OB_PUSHDOWN_STATUS_CHANGED` (-4045) keeps driving the SINGLE_ROW/BATCH switch at its 13 lines under src/storage (`grep -rn OB_PUSHDOWN_STATUS_CHANGED src/storage`) and never reaches the host.

**Rule 5.10.** Aggregate pushdown keeps the protocol's names, methods and borrowing rule ("valid only until the next non-const call on that segment", ob_pushdown_aggregate_protocol.h) as traits; views hand out datums that the caller resolves through a `&BufTable`, `destroy` becomes `Drop`, and `emit` takes the output columns (ARCHITECTURE §5):

```rust
pub trait ObIAggregateInputSegment {
    fn selection(&self) -> &ObAggregateSelectionView;
    fn can_read_values(&self, slot: ObAggregateInputSlot) -> ObResult<bool>;
    fn try_reduce(&mut self, slot: ObAggregateInputSlot, requested: u32, out: &mut ObAggregateReduction)
        -> ObResult<()>;
    fn read_values(&mut self, slot: ObAggregateInputSlot) -> ObResult<ObAggregateValueBatchView<'_>>;
    fn try_dictionary(&mut self, slot: ObAggregateInputSlot) -> ObResult<ObAggregateDictionaryView<'_>>;
}
pub trait ObIPushdownAggregatePlan: Send + Sync {
    fn create_program(&self) -> ObResult<Box<dyn ObIPushdownAggregateProgram>>;
}
pub trait ObIPushdownAggregateProgram {
    fn state(&self) -> ObPushdownAggregateProgramState;
    fn reset_scan(&mut self) -> ObResult<()>;
    fn can_consume(&mut self, seg: &mut dyn ObIAggregateInputSegment) -> ObResult<bool>;
    fn consume(&mut self, seg: &mut dyn ObIAggregateInputSegment) -> ObResult<()>;
    fn seal(&mut self) -> ObResult<()>;
    fn emit(&mut self, max_rows: i64, out: &mut ScanColumns<'_>) -> ObResult<ObAggregateEmitResult>;
}
```

**Rule 5.11.** Every datum storage hands to SQL refers to bytes the frame's `BufTable` can read. The C++ copies a storage datum into a column expression's slot without copying its bytes (`expr_datum.from_storage_datum(datum, ..)`, src/sql/engine/basic/ob_pushdown_filter.cpp:2203-2204, and the per-row filter at :2177-2180), so the Rust does not copy either: before a call that passes datums (`filter_row`, `judge_greater_or_less`, `fill_virtual_columns`, the output of rule 5.8), storage registers the buffer the bytes sit in through the host (`ScanColumns::register`), once per buffer per batch, and the datums carry the frame table's `BufId`s. A cached block is registered as its `Arc` (rule 2.8); a row storage decodes into its own buffer is registered as that buffer's `Arc` for the call; memtable row bytes are registered by the `Arc` of the memtable buffer that holds them, once per buffer per batch, which keeps them alive while the frame refers to them, as the scan's hold on the memtable does today (s3-memory.md M5). How the Rust memtable groups row bytes into such buffers is the storage core's to fix (s6-storage.md section 10); Step 2a's memtable-only path follows this rule from its first run. Inline datums of 8 bytes or fewer need nothing registered (rule 2.2).

### Example: a black filter over one batch

```cpp
// src/sql/engine/basic/ob_pushdown_filter.cpp:2308-2343 (abridged): storage calls this
if (nullptr != parent && parent->need_check_row_filter()) {
  ret = parent->get_result()->to_bits_mask(start, end, parent->is_logic_and_node(),
                                           reinterpret_cast<uint8_t *>(skip_bit_->data_));
} else {
  skip_bit_->init(bsize);
}
if (OB_FAIL(eval_exprs_batch(*skip_bit_, bsize))) {
} else if (FALSE_IT(skip_bit_->bit_not(bsize))) {
} else if (OB_FAIL(result_bitmap.from_bits_mask(start, end, reinterpret_cast<uint8_t *>(skip_bit_->data_)))) {}

// :2268-2306 eval_exprs_batch (abridged; the x86 AVX-512 branch left out)
FOREACH_CNT_X(e, filter_.filter_exprs_, OB_SUCC(ret) && !skip.is_all_true(bsize)) {
  if (OB_FAIL((*e)->eval_batch(eval_ctx, skip, bsize))) {
  } else if (!(*e)->is_batch_result()) {
    if (is_row_filtered((*e)->locate_expr_datum(eval_ctx))) { skip.set_all(bsize); }
  } else {
    const ObDatum *datums = (*e)->locate_batch_datums(eval_ctx);
    for (int64_t i = 0; i < bsize; ++i) {
      if (!skip.at(i) && is_row_filtered(datums[i])) { skip.set(i); }
    }
  }
}
```

```rust
// storage-api: the black-filter step of the walk
let skip = &mut exec.skip_bit;                                  // storage-owned
match parent {
    Some(p) if p.need_check_row_filter() => p.result().to_bits_mask(start, end, p.is_logic_and_node(), skip)?,
    _ => skip.init(bsize),
}
host.filter_batch(exec.id, skip, bsize)?;                       // SQL: eval_exprs_batch
skip.bit_not(bsize);
result_bitmap.from_bits_mask(start, end, skip)?;

// sql-das: the host's side, eval_exprs_batch under the node's id
fn filter_batch(&mut self, id: BlackFilterId, skip: &mut ObBitVector, bsize: usize) -> ObResult<()> {
    let node = &self.spec.black_filters[id.index()];
    self.clear_evaluated_infos(node);
    for &c in node.column_exprs.iter() { self.frame.eval_info_mut(c).projected = true; }
    let mut ret = Ok(());
    for &e in node.filter_exprs.iter() {
        if !(ret.is_ok() && !skip.is_all_true(bsize)) { break; }
        ret = self.frame.eval_batch(e, skip, bsize);
        if ret.is_err() {
        } else if !self.frame.expr(e).is_batch_result() {
            if is_row_filtered(self.frame.datum(e, 0)) { skip.set_all(bsize); }
        } else {
            let datums = self.frame.datum_vector(e);
            for i in 0..bsize {
                if !skip.at(i) && is_row_filtered(datums.at(i)) { skip.set(i); }
            }
        }
    }
    self.clear_evaluated_infos(node);
    ret
}
```

The two halves meet once per batch, as the C++ calls do; the filter's column slots were filled through `filter_columns` before the call (rule 5.3).

### Example: filling the output of one micro block

```cpp
// src/storage/access/ob_vector_store.cpp:306-352 (abridged): shallow copy
} else if (OB_FAIL(get_row_ids(scanner.get_reader(), begin_index, end_index, row_capacity, true, res))) {
} else if (0 == row_capacity) {
} else if (OB_FAIL(scanner.get_rows_for_old_format(cols_projector_, col_params_, row_ids_, row_capacity,
                   0, cell_data_ptrs_, exprs_, datum_infos_, &default_datums_, pad_char))) {
}
if (OB_SUCC(ret)) {
  count_ = row_capacity;
  eval_ctx_.set_batch_idx(count_);
  set_end();                              // todo: support data cross microblocks in vectorized
```

```rust
let row_capacity = self.get_row_ids(scanner.reader(), begin_index, end_index, true, res)?;
if row_capacity > 0 {
    let mut out = host.output_columns();
    let block = out.register(scanner.block_bytes());  // Arc clone, no copy; released at the next batch (rule 2.7)
    scanner.get_rows_for_old_format(&self.cols_projector, &self.col_params, &self.row_ids[..row_capacity],
                                    block, &mut out, &self.default_datums, pad_char)?;
}
self.count = row_capacity;
host.set_batch_idx(self.count);
self.set_end();                           // the batch ends with the micro block (rule 4.7)
```

**A reviewer checks:** storage crates import no SQL crate; each host call sits where the C++ calls into SQL, at the same granularity; no executor keeps a frame borrow between calls; every non-inline datum storage passes to the host refers to a buffer registered in the frame's table before the call (rule 5.11); the encoded-data filters pass the scalar comparison test; a fixed-length column longer than 8 bytes goes to the reserved buffer.

## 6. Sorting: same algorithms, same ties

```rust
// ob-base, sort module
pub fn ob_sort<T, F>(v: &mut [T], less: F) -> ObResult<()> where F: FnMut(&T, &T) -> ObResult<bool>;
pub fn make_heap<T, F>(v: &mut [T], less: F) -> ObResult<()> where F: FnMut(&T, &T) -> ObResult<bool>;
// push_heap, pop_heap and sort_heap take the same arguments
```

**Rule 6.1.** `ob_sort` transcribes, statement for statement, the SDK 26.2 libc++ `std::sort` path for a user comparator (`_LIBCPP_VERSION` 200100; MacOSX26.2.sdk/usr/include/c++/v1/__algorithm/sort.h:717-826, :881-889): sorting networks up to 5 elements, insertion sort below 24, median of three or the ninther above 128, both partition functions, the incomplete insertion sort after a balanced partition, heap sort at depth `2*log2(n)`. It leaves out the branchless partition, which `lib::ob_sort` never selects because its comparator is never `std::less` (sort.h:52-56; src/oblib/lib/utility/ob_sort.h:27-56). Moves become swaps and rotations that give the same permutation. Indexing is checked, so where the unguarded insertion sort would read before the array (the reference's hardening mode is none, R09 §2.6) Rust panics; the C++ behavior there is undefined, so no compared output depends on it. The heap functions transcribe make_heap.h, push_heap.h, pop_heap.h (Floyd's sift-down, then sift-up, pop_heap.h:33-58) and sort_heap.h. The files keep libc++'s license notice (ARCHITECTURE default 19).

**Rule 6.2.** Every sort that can reach output uses it: the 169 `lib::ob_sort(` lines (`grep -rn 'lib::ob_sort(' src`), the 3 direct `std::sort` lines, the 10 heap-function lines, and the 2 `qsort` lines, whose ties are indistinguishable (empty charset planes, src/oblib/lib/charset/ob_ctype_simple.cc:1085-1131; log file names). `ob_sort` keeps tracepoint 2501's irreflexivity check (`EN_CHECK_SORT_CMP`, ob_sort.h:30-39), run when the comparator carries no state (`size_of::<F>() == 0`, the C++ `std::is_empty`).

**Rule 6.3.** The 4 `std::stable_sort` lines become `sort_by`. Each comparator is a strict weak order, so every stable sort gives the same order: JSON object keys compare by bytes, or by length and then bytes (`ObJsonKeyCompare`, src/oblib/common/json_type/ob_json_tree.h:869-885, used at ob_json_tree.cpp:743), JSON aggregation keys by length and then bytes (src/oblib/common/xml/ob_binary_aggregate.cpp:24-37), XML keys by `ObString::compare` (src/oblib/common/xml/ob_tree_base.cpp:34-46), which orders by bytes and then length (src/oblib/lib/string/ob_string.h:394-405). This settles the XML check R09 §4.5 left open.

**Rule 6.4.** A comparator that keeps an error in `ret_` (31 lines in 18 files, `grep -rn 'int &ret = ret_;' src`) returns `ObResult<bool>`, and `ob_sort` stops at the first error (ARCHITECTURE §2). The C++ sorts on in address order and returns `comp_.ret_` afterwards (src/sql/engine/sort/ob_sort_op_impl.cpp:395-406, :1867-1869), so no order after an error is ever seen. Infallible comparators return `Ok`.

**Rule 6.5.** OB's own sorts are translated, not replaced: `ObAdaptiveQS` with its radix steps and only its scalar byte compare (the AVX-512 choice, ob_sort_op_impl.cpp:278-283, never runs on arm64); `ObBinaryHeap` with its root compare cache, which changes which child is picked (src/oblib/lib/container/ob_heap.h:223-264); the partition sort, prefix sort, unique sort, top-N heaps, the in-memory merge of sorted runs and the external merge.

**Rule 6.6.** The choice among them is translated: `check_can_encode_sortkey` (src/sql/optimizer/ob_optimizer_util.cpp:6938-6995), with `_enable_newsort`, the hint, the encodable types, the width and row thresholds (no encoding above 256 bytes average key width or below 1,000 estimated rows, nor below 100,000 rows for keys under 64 bytes or 1,500,000 for keys between 64 and 128), and tracepoint 1200, which the full init.sql fires on every call (tools/deploy/init.sql:24); then the top-N, partition and encoded paths (ob_sort_op_impl.cpp:1848-1869).

**Rule 6.7.** The encoded-key comparison keeps its tie rule: equal prefixes order by `(l_cell.len_ - r_cell.len_) < 0`, an `int` subtraction of two 29-bit lengths, so `l.len() < r.len()` (ob_sort_op_impl.cpp:414).

**Rule 6.8.** The transcribed heap functions serve the PX merge-sort receive over channel indexes (src/sql/engine/px/exchange/ob_row_heap.h:226-280), the hash group-by popular-value heap (src/sql/engine/aggregate/ob_hash_groupby_op.cpp:2616-2623), the plan cache and the KV cache.

**Rule 6.9.** Dumps happen at the same rows. The sort dumps when `get_data_size() > get_mem_bound()` or `mem_context_->used() >= get_max_bound()` (ob_sort_op_impl.h:713-717), and after a dump ties follow chunk boundaries and the merge heap (R05 §1.7). Row stores, row arrays and hash tables of spilling operators therefore charge the work area by the C++ formulas (rule 4.4), and each spilling operator's inventory row records the formula behind every figure it reports (ARCHITECTURE §3.2: the work area counts the C++ figures).

**Rule 6.10.** clippy's `disallowed-methods` and `disallowed-types` ban `sort_unstable*`, `select_nth_unstable*` and `BinaryHeap` in engine crates (ARCHITECTURE §10); `sort_by` appears only at the 4 stable-sort sites and each `#[allow]` cites its inventory row.

### Example: the SORT comparator

```cpp
// src/sql/engine/sort/ob_sort_op_impl.cpp:395-432 (abridged)
bool ObSortOpImpl::Compare::operator()(const StoredRow *l, const StoredRow *r) {
  bool less = false;
  int &ret = ret_;
  if (OB_UNLIKELY(OB_SUCCESS != ret)) {
    less = l < r;              // keep a strict weak order after an error
  } else if (!is_inited() || OB_ISNULL(l) || OB_ISNULL(r)) { ret = ...;
  } else if (OB_FAIL(fast_check_status())) { less = l < r;          // every 8192nd call
  } else if (enable_encode_sortkey_) {
    int cmp = MEMCMP(l_cell.ptr_, r_cell.ptr_, min(l_cell.len_, r_cell.len_));
    less = cmp != 0 ? (cmp < 0) : (l_cell.len_ - r_cell.len_) < 0;
  } else {
    for (int64_t i = cmp_start_; 0 == cmp && i < cmp_end_ && OB_SUCC(ret); i++) {
      if (OB_FAIL(sort_cmp_funs_->at(i).cmp_func_(lcells[idx], rcells[idx], cmp, access_ctx_))) {
      } else if (cmp < 0) { less = sort_collation.is_ascending_;
      } else if (cmp > 0) { less = !sort_collation.is_ascending_; }
    }
  }
  return less;
}
// :1865-1869
lib::ob_sort(&rows_->at(begin), &rows_->at(0) + rows_->count(), CopyableComparer(comp_));
if (OB_SUCCESS != comp_.ret_) { ret = comp_.ret_; }
```

```rust
impl Compare {
    pub fn less(&mut self, l: &StoredRowRef, r: &StoredRowRef, store: &ObChunkDatumStore) -> ObResult<bool> {
        if !self.is_inited() { return Err(OB_NOT_INIT); }
        self.fast_check_status()?;                       // every 8192nd call, as the C++ counts
        if self.enable_encode_sortkey_ {
            let (lb, rb) = (store.cell_bytes(*l, 0), store.cell_bytes(*r, 0));
            let n = lb.len().min(rb.len());
            return Ok(match lb[..n].cmp(&rb[..n]) {      // unsigned bytes, as MEMCMP
                Ordering::Equal => lb.len() < rb.len(),  // (l_cell.len_ - r_cell.len_) < 0
                o => o == Ordering::Less,
            });
        }
        let (mut cmp, mut less) = (0, false);
        let mut i = self.cmp_start_;
        while cmp == 0 && i < self.cmp_end_ {
            let c = &self.sort_collations_[i];
            cmp = (self.sort_cmp_funs_[i].cmp_func_)(store.cell(*l, c.field_idx_), store.cell(*r, c.field_idx_),
                                                     &self.access_ctx_)?;
            if cmp < 0 { less = c.is_ascending_; } else if cmp > 0 { less = !c.is_ascending_; }
            i += 1;
        }
        Ok(less)
    }
}
// the call site
let ret = ob_sort(&mut self.rows[begin..], |l, r| comp.less(l, r, &self.datum_store));
```

The address-order branches go: the Rust sort stops where the C++ would switch to address order, and the statement fails with the same code.

**A reviewer checks:** no banned sort or heap (clippy) and every sort site names its inventory row; comparators return what the C++ returns for every pair, NULL order and collation included (ARCHITECTURE §10's float comparison rules); the algorithm choice matches the C++ line for line; the differential permutation test passes.

## 7. Hashing: same values, same table order

**Rule 7.1.** `murmurhash64a`, `murmurhash2` and `fnv_hash2` are translated line by line into ob-base's `hash` module (src/oblib/lib/hash_func/murmur_hash.h:19-51; murmur_hash.cpp:11-66), with little-endian loads through `from_le_bytes`, `wrapping_*` wherever the C++ relies on wraparound, and a C `char` read as a number as `i8` (ARCHITECTURE §10). `murmurhash` and `appname_hash` stay names for `murmurhash64a`. The home is ob-base (ARCHITECTURE §10).

**Rule 7.2.** Seeds keep their values and their users:

| Seed | Users |
|---|---|
| 16777213 | hash join keys and the NULL-key counter (src/sql/engine/join/ob_hash_join_op.h:1123; ob_hash_join_op.cpp:4010, :4029); recursive CTE search (src/sql/engine/recursive_cte/ob_search_method_op.h:88); `ObExprHash` (src/sql/engine/expr/ob_expr_hash.h:44) |
| 99194853094755497 | hash group-by (ob_hash_groupby_op.cpp:1191, :1775); window functions (src/sql/engine/window_function/ob_window_function_op.cpp:3060); `ObHashCols` (src/sql/engine/aggregate/ob_exec_hash_struct.cpp:29); `DEFAULT_PART_HASH_VALUE` of the hash partitioning code (src/sql/engine/basic/ob_hash_partitioning_basic.h:29) |
| 0 | `ObString` keys of OB hash maps (src/oblib/lib/hash/ob_hashutils.h:773-779); KEY partitioning and NDV (R09 §2.5) |

**Rule 7.3.** The per-type datum hashes keep their families, `default_hash_`, `murmur_hash_` and `murmur_hash_v2_` with their batch forms (src/share/datum/ob_datum_funcs_impl.h:880-895), and the collation `hash_sort` functions. `xx_hash_` and `wy_hash_` have no callers (`grep -rnE 'xx_hash_\b|wy_hash_\b' src` finds only the registration), so they are not translated.

**Rule 7.4.** Float hashing normalizes as the C++ does: -0.0 to 0.0 and every NaN to the C `NAN` (src/oblib/common/object/ob_obj_funcs.h:410-414), which is `__builtin_nanf("0x7fc00000")` (MacOSX26.2.sdk/usr/include/math.h:66): bits `0x7fc00000` as a float, `0x7ff8000000000000` as a double (a differential test confirms them). Rust writes those bits with `from_bits`, never `f64::NAN`, whose bit pattern Rust does not promise. A DOUBLE with a declared scale hashes its `ob_fcvt` text (ob_datum_funcs_impl.h:1240-1256).

**Rule 7.5.** `ObHashMap` and `ObHashSet` are ported with their layout: the bucket count from `cal_next_prime` (ob_hashutils.h:677-689; src/oblib/lib/hash/ob_hashmap.h:184), the bucket `hash % bucket_num` (ob_hashtable.h:1139), head insertion into a bucket's chain (:1044-1045), iteration bucket by bucket from the head, and `extend`, which re-inserts the old buckets in order with head insertion (:1290-1330). Iteration order then equals the C++'s wherever the key hashes a value, including the sites where it reaches output (src/sql/optimizer/ob_join_order.cpp:16662, :16727; src/sql/rewrite/ob_range_generator.cpp:1799; ob_key_part.cpp:289, :305). Maps keyed by an address follow ARCHITECTURE §4.1 rule 3.

**Rule 7.6.** The hash join table keeps: `hash & HASH_VAL_MASK`; the bucket count from `calc_bucket_number`, `next_pow2` of twice the build row count, doubled again when twice as many 16-byte buckets still fit in `INIT_L2_CACHE_SIZE` (ob_hash_join_op.cpp:1338-1348; rule 4.9); one bucket per hash value, found by linear probing, with its rows chained newest first (`set`, ob_hash_join_op.h:459-481); `get`'s wrap rule (:410-435); the walk in bucket order that emits the unmatched build rows of outer and anti joins; the NULL-key counter, reset in `part_rescan` (ob_hash_join_op.cpp:534-617).

**Rule 7.7.** Hash group-by keeps first-seen group ids and `ObExtendHashTable` (`INITIAL_SIZE` 128, `SIZE_BUCKET_SCALE` 2, linear probing; src/sql/engine/aggregate/ob_exec_hash_struct.h:42-47, :141-147). After a dump, rows go to partition `(hash >> part_shift) & (part_cnt - 1)` (ob_hash_groupby_op.cpp:1002, :1961, :2233) and the partitions are aggregated in order. Hash distinct and the hash set operators keep `ObHashPartInfrastructure`'s insertion-ordered store (R05 §1.8).

**Rule 7.8.** A map that is only looked up may use hashbrown with a fixed hasher (the R12 row ARCHITECTURE §13 accepts); `std::collections::HashMap`, `HashSet` and `RandomState` are banned in engine crates (ARCHITECTURE §10). A C++ `std::map` or `std::set` becomes a `BTreeMap` or `BTreeSet` whose `Ord` equals the C++ comparator.

### Example: fnv_hash2, which names query blocks

`fnv_hash2` feeds the `%08X` query-block names in plan text (src/sql/resolver/dml/ob_sql_hint.cpp:89, :389).

```cpp
// src/oblib/lib/hash_func/murmur_hash.cpp:51-66
uint32_t fnv_hash2(const void *key, int32_t len, uint32_t seed) {
  const int p = 16777619;
  int32_t hash = (int32_t)2166136261L;
  const char *data = static_cast<const char *>(key);
  for (int32_t i = 0; i < len; i++) {
    hash = (hash ^ data[i]) * p;
  }
  hash += hash << 13;
  hash ^= hash >> 7;
  hash += hash << 3;
  hash ^= hash >> 17;
  hash += hash << 5;
  hash ^= seed;
  return (uint32_t)hash;
}
```

```rust
pub fn fnv_hash2(key: &[u8], seed: u32) -> u32 {
    const P: i32 = 16777619;
    let mut hash: i32 = 2166136261u32 as i32;
    for &b in key {
        hash = (hash ^ i32::from(b as i8)).wrapping_mul(P);    // char is signed on macOS arm64
    }
    hash = hash.wrapping_add(hash << 13);
    hash ^= hash >> 7;                                         // arithmetic shift, as on int32_t
    hash = hash.wrapping_add(hash << 3);
    hash ^= hash >> 17;
    hash = hash.wrapping_add(hash << 5);
    hash ^= seed as i32;                                       // the C++ converts both to uint32_t
    hash as u32
}
```

### Example: the hash join key hash, where a step runs after an error

```cpp
// src/sql/engine/join/ob_hash_join_op.cpp:4002-4035 (abridged)
hash_value = HASH_SEED;
for (int64_t idx = 0; OB_SUCC(ret) && idx < join_keys.count(); ++idx) {
  if (OB_FAIL(join_keys.at(idx)->eval(eval_ctx_, datum))) {
  } else {
    need_null_random |= (datum->is_null() && !MY_SPEC.is_ns_equal_cond_.at(idx));
    if (OB_FAIL(hash_funcs.at(idx).hash_func_(*datum, hash_value, hash_value, datum_access_ctx_))) {}
  }
}
need_null_random &= (MY_SPEC.join_type_ != LEFT_ANTI_JOIN && MY_SPEC.join_type_ != RIGHT_ANTI_JOIN);
if (need_null_random) {
  if (skip_null) { skipped = true; }
  else {
    hash_value = common::murmurhash64A(&null_random_hash_value_, sizeof(int64_t), HASH_SEED);
    null_random_hash_value_++;
  }
}
hash_value = hash_value & ObHashJoinStoredJoinRow::HASH_VAL_MASK;
```

```rust
let mut ret: ObResult = Ok(());
let mut hash_value = HASH_SEED as u64;
let mut idx = 0;
while ret.is_ok() && idx < join_keys.len() {
    match frame.eval(join_keys[idx]) {
        Err(e) => ret = Err(e),
        Ok(datum) => {
            need_null_random |= datum.is_null() && !self.spec().is_ns_equal_cond_[idx];
            match (hash_funcs[idx].hash_func_)(&datum, frame.bufs(), hash_value, &self.datum_access_ctx_) {
                Ok(h) => hash_value = h,
                Err(e) => ret = Err(e),
            }
        }
    }
    idx += 1;
}
need_null_random &= self.spec().join_type_ != LEFT_ANTI_JOIN && self.spec().join_type_ != RIGHT_ANTI_JOIN;
if need_null_random {
    if skip_null { *skipped = true; } else {
        hash_value = murmurhash64a(&self.null_random_hash_value_.to_le_bytes(), HASH_SEED as u64);
        self.null_random_hash_value_ += 1;
    }
}
*out = hash_value & ObHashJoinStoredJoinRow::HASH_VAL_MASK;
ret
```

The counter still moves after a failed key, as in the C++, so the function keeps a local `ret` instead of `?` (ARCHITECTURE §2).

**A reviewer checks:** hash functions and seeds match the table and pass the differential tests; no std `HashMap`, `HashSet` or `RandomState`, and every iterated map is the `ObHashMap` port; NaN is written with explicit bits; `char` is `i8`; `wrapping_*` appears exactly where the C++ wraps.

## 8. Inventory rows and tests this section needs

Inventory rows (prompt 02):
- each of the 169 `lib::ob_sort` lines, 3 `std::sort`, 10 heap-function, 4 `std::stable_sort` and 2 `qsort` lines: the sort used and whether the comparator can fail; the 31 `ret_` comparators;
- every OB hash container iteration (57 iterator lines and 53 `foreach_refactored` calls, plus `auto` loops, R09 §6): a value key or an address key;
- the 17 `MEMMOVE` lines in src/sql/engine/expr and every result write whose source may be the destination's own buffer;
- the operator files that reach into frame internals (35 by R05's pattern, 66 by the evidence's, evidence-full.md:188): which frame call each use becomes;
- storage's uses of SQL objects (1,070 `sql::` lines in 99 files, R05 §1.5): which host call or storage-api type replaces each;
- each spilling operator: the C++ formula behind every work-area figure it reports;
- each `sizeof` of rule 4.9;
- the tracepoints of rule 4.6.

Differential tests in the core build, against the C++ compiled into a test-only binary with the reference's flags (ARCHITECTURE §10):
- `ob_sort` and the heap functions against `std::sort`, `std::push_heap` and `std::pop_heap` compiled with the SDK 26.2 headers, on random arrays with many ties, comparing whole permutations and comparison counts; `ObBinaryHeap` and `ObAdaptiveQS` on the same inputs;
- every hash function and per-type datum hash, NaN and -0.0 included; `ObHashMap` iteration order after inserts, erases and `extend`;
- `ObDatum`'s descriptor bits against the C++ `pack_`;
- each white filter on each encoding against the scalar comparison, and each NEON kernel against its scalar version;
- the rows at which the sort and hash group-by dump under a small work area.

Step 2a also times the handle datum on the scan+filter+aggregate query (ARCHITECTURE default 13) and runs one string-heavy filter, since every string read goes through the buffer table.

## 9. Defaults this section relies on

From ARCHITECTURE §17: 13 (the safe handle datum, with a raw-pointer datum crate as the fallback), 19 (the libc++ transcription with its notice), 20 (exact hash order everywhere, the mask as a safety net), 21 (test-only C++ for the differential tests), and 9 (budgets), which ARCHITECTURE now narrows for the work area (objection 3).

## 10. Objections to ARCHITECTURE.md, and how each was settled

Each objection is followed by its settlement, recorded in RESOLUTIONS.md as s5-N.

1. **murmur_hash's crate.** §10 translates "murmur_hash ... line by line into ob-values". But `ObHashMap`, which §14 rule 7 homes in ob-base with "the hash maps and functions", hashes `ObString` keys with `murmurhash(ptr, len, 0)` (src/oblib/lib/hash/ob_hashutils.h:773-779), and ob-base (crate 2) may not use ob-values (crate 8, §1.1). The R12 row §13 accepts also puts murmurhash64A and fnv_hash2 in ob-base. This section puts murmur_hash.{h,cpp} in ob-base's `hash` module; §10 should say ob-base.

   **Settled:** accepted. ARCHITECTURE §10 now translates murmur_hash into ob-base's `hash` module; the crate map already sent src/oblib/lib/hash_func to ob-base.
2. **`loc: u64`.** Read literally, a `u64` next to the `u32` gives a 16-byte datum, or needs `#[repr(packed)]`, under which Rust refuses a reference to the field (error E0793), so a short value cannot be lent in place. The C++ datum is 12 bytes (asserted at src/sql/engine/basic/ob_pushdown_filter_simd.cpp:46), and the stored-row sizes of rule 4.4 depend on it. This section keeps the same 8 bytes as `[u8; 8]`.

   **Settled:** accepted. ARCHITECTURE §5 now says `loc: [u8; 8]`, 12 bytes, alignment 4, no `packed`.
3. **Work-area byte counts.** Default 9 (§3.2) lets budgets trip "by the same formulas at the same check points, not at the same byte counts". For the SQL work area that changes output: the sort dumps on `mem_context_->used()` (ob_sort_op_impl.h:713-717), after a dump ties follow chunk boundaries, and hash group-by output follows the partitions `(hash >> part_shift) & (part_cnt - 1)` (ob_hash_groupby_op.cpp:1002, :1961, :2233). A dump at another row gives other ties and another group order, and family 12 checks spilling (PLAN §4, family 12). This section requires work-area charges computed by the C++ formulas (rules 4.4 and 6.9). If the developer keeps default 9 as written for the work area, family 12's spill scenario must use unique ORDER BY keys and no hash-ordered output. (Assumption: no configured case dumps today; the only tests that set `ob_sql_work_area_percentage` set 100 and then restore its default 5, tools/deploy/mysql_test/t/join_many_table.test:13, :86; join_many_table_single_field.test:12, :100; src/share/system_variable/ob_system_variable_init.json:1801.)

   **Settled:** accepted. ARCHITECTURE §3.2 and default 9 now except the SQL work area: it counts the C++ figures, so dumps come at the same rows; the developer confirms default 9 with that exception, and Step 2a checks the dump rows (ARCHITECTURE §15).
4. **The filter kinds.** §5 lists AND, OR, white, black, dynamic and sample. The C++ also has the semi-structured white filter (`SEMISTRUCT_FILTER`, a white filter on a JSON path) and the executor-only "external" filter through which storage injects truncated-partition filtering (ob_pushdown_filter.cpp:44-104; src/storage/truncate_info/ob_truncate_partition_filter.cpp:279). This section keeps the first as a white variant and the second as a storage executor (rule 5.7).

   **Settled:** accepted. ARCHITECTURE §5 now lists both.
5. **`next_batch` against the C++ names.** §5 names the operator method `next_batch`, while §14 rule 3 keeps C++ identifiers, and the C++ has `get_next_batch` (the wrapper, ob_operator.h:416) and `inner_get_next_batch` (each operator's body, :420). This section uses `next_batch` for the wrapper and `inner_get_next_batch` for the bodies; §5 or §14 should say which name wins, or the 156 operator files (R05 §1.1) will split.

   **Settled:** the C++ names win (§14 rule 3). ARCHITECTURE §5 and part 4 above name the wrapper `get_next_batch`, a provided method of the `ObOperator` trait, and the bodies `inner_get_next_batch`.
6. **The aggregate protocol changes by one parameter.** A C++ program keeps an `ObEvalCtx` and writes its results through it (src/sql/engine/aggregate/ob_pushdown_aggregate_program.cpp:359-362, :800-804); storage drives `emit` (src/storage/access/ob_aggregated_store.cpp:461-462), and a Rust program cannot keep a borrow of the frame. So `emit` takes the output columns (rule 5.10); §5's "keeps the C++ protocol as traits" should allow that.

   **Settled:** accepted. ARCHITECTURE §5 now says so.
7. **`ObBitVector`'s file is mapped to another crate.** §5 puts `ObBitVector` in ob-values, but R01's prefix map sends src/share/vector, which defines it (src/share/vector/ob_bit_vector.h:195), to ob-values-doc (migration/design/evidence/01-crates/crates.tsv), a crate ob-values may not use. §1.1's list of map changes needs a file entry moving ob_bit_vector.h into ob-values.

   **Settled:** accepted. The design crate map moves src/share/vector/ob_bit_vector to ob-values, and ARCHITECTURE §1.1's ob-values row and §1.2 fix 1 list it.
8. **§10's seed list is short.** It names 16777213 for hash join and 0 for KEY partitioning and NDV; hash group-by, window functions and the hash partitioning code use 99194853094755497, and recursive CTE search and `ObExprHash` also use 16777213 (rule 7.2).

   **Settled:** accepted. ARCHITECTURE §10 now lists the seeds and their users as rule 7.2 does.
9. **Who owns a batch.** §3.1 rule 3 says rows "live in column batches owned by the producing operator", while §5 puts the datums in the frame's per-expression slots. This section follows §5: the frame owns the slots, the producing operator is their only writer, and the operator owns only its skip vector (rule 4.3). §3.1 rule 3 should say "written by" rather than "owned by".

   **Settled:** accepted. ARCHITECTURE §3.1 rule 3 now says "written by" and names the frame as the owner of the slots.
