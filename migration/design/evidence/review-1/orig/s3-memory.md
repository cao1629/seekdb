# 3. Memory: owners, handoff, handles, accounting and out-of-memory

This section turns ARCHITECTURE.md §3 into rules for core authors (ob-base, ob-runtime, the storage core, sql-exec) and for implementers who translate units against them. It follows ARCHITECTURE.md exactly. Choices only the developer can make are marked **default, for the developer to confirm** and listed in 3.10; the objections this section raised against ARCHITECTURE.md are at the end, each with its settlement (RESOLUTIONS.md s3-N).

- Code facts are at 834bbee1e (`git diff --quiet 834bbee1e -- src` succeeds). A count gives its pattern and was run from the repository root as `git grep -n -P '<pattern>' -- 'src/*' | wc -l`, and `-l` for files.
- "R03" is migration/design/research/03-memory.md. "C<n>" is claim n of the gap-oom-policy-conflict area of migration/feasibility/evidence-full.md. "The sweep" is migration/inventory/sweep/.
- Snippets carry explanatory comments; translated code does not (ARCHITECTURE §14 rule 8).
- Rules are numbered within their subsection so that reviews can cite them: M for owners, H for handoff, R for reference counts, A for budgets, T for the query tracker.

## 3.1 The types and where they live

| Rust type | Home | Replaces | Thread traits |
|---|---|---|---|
| `bumpalo::Bump`, passed as `&'q Bump` | - | `ObIAllocator&` for statement and execution data | `Send`, not `Sync` (bumpalo 3.20.3 src/lib.rs:393-396, :509) |
| `ObArenaAllocator` | ob-base `allocator` | `ObArenaAllocator` (src/oblib/lib/allocator/page_arena.h:975-1079) and the arena of a `lib::MemoryContext` | `Send`, not `Sync` |
| `Budget`, `Charge` | ob-base `allocator` | `ObBlockAllocMgr` (src/oblib/lib/allocator/ob_block_alloc_mgr.h:30-63), `MemoryUsageTracker` (src/oblib/lib/allocator/ob_malloc.h:183-215) and the counters of 3.7.2 | `Send + Sync` |
| `ObMemTracker` | ob-base `allocator` | `lib::ObMemTracker` and `ObMemTrackerGuard` (src/sql/executor/ob_memory_tracker.h:28-67) | `Send + Sync`, shared as `Arc` |
| `Id<T>`, `Arena<T>` | ob-base (ARCHITECTURE §4.1); `Arena<T>` also reports its capacity in bytes (T2) | objects identified by address | as `T` |
| `AlignedBuf` | ob-platform † | IO data buffers | `Send + Sync` |
| `ObJemalloc` | ob-platform † | the jemalloc path of `ob_malloc` (src/oblib/lib/allocator/ob_malloc.h:117-125) | `GlobalAlloc` |

`ob_base::allocator` mirrors src/oblib/lib/allocator; it adds to ARCHITECTURE §14 rule 7's home modules. `ObMemTracker` moves down from the `sql` crate, which the parser shim, resolver, rewrite and sql-exec cannot use (ARCHITECTURE §1.2, fix 1). Names follow ARCHITECTURE §14 rule 3.

```rust
// ob_base::allocator
pub struct Budget { /* name: &'static str, limit: AtomicI64, hold: AtomicI64 */ }
impl Budget {
    pub fn new(name: &'static str, limit: i64) -> Arc<Budget>;
    pub fn limit(&self) -> i64;
    pub fn set_limit(&self, limit: i64);
    pub fn hold(&self) -> i64;
    /// ObBlockAllocMgr::alloc_block: add, compare with the limit, undo when over.
    pub fn try_charge(self: &Arc<Self>, bytes: i64) -> Option<Charge>;
    /// Counts with no limit: the work area, memstore pages, a request's counter.
    pub fn charge(self: &Arc<Self>, bytes: i64) -> Charge;
}
pub struct Charge { /* budget: Arc<Budget>, bytes: AtomicI64 */ }   // not Clone
impl Charge {
    pub fn bytes(&self) -> i64;
    pub fn try_grow(&self, more: i64) -> bool;   // the same add, compare, undo
    pub fn grow(&self, more: i64);
    pub fn shrink(&self, less: i64);
    pub fn set(&self, bytes: i64);               // arenas report their size this way
}
impl Drop for Charge { /* gives bytes() back to the budget */ }

pub struct ObArenaAllocator { /* bump: Bump, counted: Option<Charge> */ }
impl ObArenaAllocator {
    pub fn new() -> Self;                              // counted by no request
    pub fn counted_by(tracker: &ObMemTracker) -> Self; // counted toward -11049 (3.6.2)
    pub fn bump(&self) -> &Bump;
    pub fn sync(&self);                                // counted.set(bump.allocated_bytes())
    pub fn reset(&mut self);                           // bump.reset(), then sync()
}

/// The only way engine code puts a value in a bump arena.
pub fn arena_alloc<'q, T>(bump: &'q Bump, value: T) -> &'q mut T {
    const { assert!(!core::mem::needs_drop::<T>()) };
    bump.alloc(value)
}
```

Budget operations are `SeqCst`, as `ObBlockAllocMgr`'s `ATOMIC_AAF` is; the work-area counter keeps the relaxed order `MemoryUsageTracker` names (src/oblib/lib/allocator/ob_malloc.h:188-206), as ARCHITECTURE §11 allows for orders the C++ names weaker.

## 3.2 Who owns each allocation

ARCHITECTURE §3.1: the owner is the server context, a session, a cached plan, a statement's compilation, a plan execution, a batch, a budget owner, or a receiver (thread, task, timer, queue, channel, cache). Today the owner is picked at run time: `ObIAllocator` sits on 6,317 lines as `ObIAllocator\s*&` (2,090 files) and 2,246 as `ObIAllocator\s*\*` (746 files), and the current context picks the rest.

**M1. One owner, named in the inventory.** Each allocating site's inventory row names its owner from the list above, in `target_translation`. A translation that gives memory another owner is a defect even when it compiles.

**M2. Compilation.** IR objects live in the typed-id arenas of ARCHITECTURE §4.1, owned by the compile context. Names, literal text and parameter values live in one per-statement `Bump`, passed as `&'q Bump`; `ParseTree<'q>` borrows its `str_value_` from it (ARCHITECTURE §4.2). No `Rc` or `Arc` in IR types.
- The statement arena is an `ObArenaAllocator` counted by the request. It replaces the memory a statement attempt takes from the request's contexts: the worker's temporary context for a first attempt (src/observer/omt/ob_th_worker.cpp:244) and a fresh one for retries and the later statements of a multi-statement query (src/observer/mysql/obmp_query.cpp:388-424, :467-475). In Rust every attempt gets its own arena, which lives until the attempt's result is sent, since parameter values are read during execution.
- Anything whose size grows with the statement (child lists, IN lists, select items) lives in the statement `Bump` (for example `bumpalo::collections::Vec<'q, ExprId>`) or in a typed arena, so that -11049 counts it (3.6.2), as today's `ObFixedArray<ObRawExpr*, ObIAllocator>` on the statement's allocator is counted (src/sql/resolver/expr/ob_raw_expr.h:3376). A `Vec` inside a node, such as the SQL front section's `ParseNode::children_`, is not counted; default 9 allows that difference, and the nodes themselves count through their arenas.

**M3. A cached plan owns all its memory.** It is `Send + Sync + 'static`, shared as `Arc<ObPhysicalPlan>`, and holds a `Charge` on the plan cache's counter (3.6.3). Code generation copies what the plan keeps out of the statement arena. Plan memory stays outside the request's count, as today, where each cache object has its own context under the plan cache's root (src/sql/plan_cache/ob_lib_cache_object_manager.cpp:47-86).

**M4. Execution.**
- Rows live in column batches owned by the operator that produced them and stay valid until its next call (ARCHITECTURE §5).
- An operator that keeps rows longer (sort, hash join, hash group by, materialization) copies them into a store whose blocks are charged to the work area (3.6.3).
- Expression temporaries go into the frame's per-execution bump arena (`tmp` in the execution section), reset between batches through `&mut` and counted by the request (T2). A borrow into it cannot outlive the reset, which does not compile while one is alive.

**M5. Scans.** A scan holds its source: an `Arc` of the tablet snapshot or cached block, or a memtable reference kept alive by that snapshot. Views borrow from the scan. This makes 9e2b6ba16 (sstable pointers outliving the iterator that pinned them) a compile error.

**M6. One thread per arena.** `Bump` is not `Sync`, so parallel work makes one arena per task and moves owned results back; the 29 `ObSafeArenaAllocator` lines (9 files) become per-task arenas. Where the C++ shares one, the Rust shares an owned object: compaction's merge progress, which allocates from the shared arena (src/storage/compaction/ob_basic_tablet_merge_ctx.cpp:386-388), owns its memory and is shared as an `Arc`; the storage meta cache's IO callback, which fills the caller's arena from an IO thread (src/storage/meta_mem/ob_storage_meta_cache.h:244; .cpp:404), returns an owned value. There is no `Mutex<Bump>` (ARCHITECTURE §3.1 rejects shared arenas); 62b9d1a81, parallel merge tasks sharing one arena, is the class this prevents.

**M7. Long-lived owners and what may go into a bump.**
- The server, sessions, caches, services and PL packages keep collections that free item by item; 38054020d (PL memory growing in a long-running SQL) is the arena form of this leak.
- Nothing with a destructor goes into a bump, since bumpalo never runs `Drop` (bumpalo README.md:28-36). Engine code places values only through `arena_alloc`, whose `const` assertion rejects a type that needs `Drop` at compile time, and `Bump::alloc_slice_copy`/`alloc_str`, whose element types are `Copy`. clippy's `disallowed-methods` bans the other `Bump::alloc*` methods and `bumpalo::boxed::Box` in engine crates.
- Owned trees whose depth a client controls (JSON, XML) get an iterative `Drop` or live in an id arena, so dropping them cannot overflow the stack (R10 item 20).
- `Box::leak`, `Vec::leak`, `String::leak` and `std::mem::forget` are banned in engine crates. They would turn owned memory into `'static` borrows and get around M8 and 3.3.

**M8. No implicit current context.** `CURRENT_CONTEXT` (120 lines / 76 files), `WITH_CONTEXT(` (29 / 18), `CREATE_WITH_TEMP_CONTEXT(` (22 / 18) and the containers that bind to the current context at their first allocation (src/oblib/lib/container/ob_array.h:484-492; src/oblib/lib/container/ob_raw_se_array.h:194-199) become an argument or a field. The destination owns the memory because it is a field of the owner. Example E3 shows this.

### Example E3: an implicit context becomes a field

C++ (src/sql/engine/ob_physical_plan.cpp:263-289). `field_columns_` is built on the plan context's arena (:40), and `WITH_CONTEXT` also sends anything that allocates through the current context to the plan:

```cpp
int ObPhysicalPlan::set_field_columns(const ColumnsFieldArray &fields)
{
  int ret = OB_SUCCESS;
  ObField field;
  WITH_CONTEXT(mem_context_) {
    int64_t N = fields.count();
    if (N > 0 && OB_FAIL(field_columns_.reserve(N))) {
    ...
      } else if (OB_FAIL(field.deep_copy(ofield, &allocator_))) {
      } else if (OB_FAIL(field_columns_.push_back(field))) {
```

Rust:

```rust
impl ObPhysicalPlan {
    pub fn set_field_columns(&mut self, fields: &[ObField]) -> ObResult {
        self.field_columns_.reserve(fields.len());          // aborts on out-of-memory (3.7.1)
        for (i, ofield) in fields.iter().enumerate() {
            if !self.contain_paramed_column_field_ && ofield.is_paramed_select_item_ {
                let Some(ctx) = &ofield.paramed_ctx_ else {
                    log_warn!(OB_INVALID_ARGUMENT, "invalid paramed ctx", K(i));
                    return Err(OB_INVALID_ARGUMENT);
                };
                if !ctx.param_idxs_.is_empty() { self.contain_paramed_column_field_ = true; }
            }
            self.field_columns_.push(ofield.clone());        // ObField owns its bytes: clone is deep_copy
        }
        Ok(())
    }
}
```

`field_columns_` is a `Vec` inside the plan, so the context switch has nothing left to do. The resolver builds `ObField` as owned values, since the plan keeps them (M1). The reserve and deep-copy errors, which could only be -4013 from an allocation, have no Rust counterpart (3.5).

## 3.3 How memory reaches other threads, tasks, timers, queues, channels and caches

Today memory is handed over either as a copy into memory the receiver owns or as a pointer with a hand-kept count, and the types do not say which. `ObSimpleThreadPool::push(TaskType *task)` and `handle(TaskType *task)` pass raw pointers, and `TaskType` is `void` for the default queue (src/oblib/lib/thread/ob_simple_thread_pool.h:37, :80, :112, :128). Four rows of the report's bug table are this class: bc31e03f4, eda242c9a, 9d24b8807 and dd899675c (report §2).

**H1. Handoff is owned data.** What goes to another thread, task, timer, queue, channel or cache is `Vec<u8>` or `Box<[u8]>` for one receiver, `Arc<[u8]>` or `Arc<T>` for several, or an owned struct. Strings a receiver keeps are copied first; 9d24b8807 fixed shallow `ObString` copies in a queued task by adding exactly these copies.

**H2. The APIs enforce it.** Every core API that runs code elsewhere or keeps a value past the call takes `Send + 'static`. The thread spawner of ARCHITECTURE §7.2 takes its body as `Box<dyn FnOnce() + Send + 'static>` (`ThreadSpawner::spawn`, s1-crates-core.md 3.7), and a channel's send takes `T: Send + 'static`. Where the API keeps its C++ name:

```rust
impl<T: Send + 'static> ObSimpleThreadPool<T> { pub fn push(&self, task: T) -> ObResult; }   // today push(void *task)
pub trait ObTimerTask: Send + Sync + 'static { fn runTimerTask(&self); }                     // holds Weak<Owner> (ARCHITECTURE §7.1)
impl<K, V> ObKVCache<K, V> where K: Send + Sync + 'static, V: Send + Sync + 'static {
    pub fn put(&self, key: K, value: V, overwrite: bool) -> ObResult;                       // today put(const Key&, const Value&, bool)
}
pub fn async_call<F, R>(f: F) -> AsyncHandle<R> where F: FnOnce() -> ObResult<R> + Send + 'static;  // src/share/ob_ex_rpc.h
```

`unsafe impl Send` is banned outside the named crates (ARCHITECTURE §8). `&'q` data cannot satisfy `'static`, so each of the four bugs above stops compiling.

**H3. Scoped threads where the caller waits.** Fork-join code whose caller waits for all workers may borrow through `std::thread::scope`. PX, DTL and DAG tasks are not fork-join and copy. DAS parallel tasks copy their input as today (src/sql/das/ob_das_parallel_handler.cpp:137-151).

**H4. What a receiver may not get.** A PX worker, a DAS parallel task or a DAG task never receives the requester's `ObMemTracker` or a counted arena. Their contexts sit under `ROOT_CONTEXT` today (src/sql/engine/px/ob_px_worker.cpp:159-160; ob_das_parallel_handler.cpp:137-138), and `ObPxWorker::check_status` has no memory check (ob_px_worker.cpp:369-384).

**H5. Cache inserts move the value.** `ObIKVCacheValue::deep_copy(char *buf, int64_t buf_len, ObIKVCacheValue *&value)` (src/share/cache/ob_kvcache_struct.h:55-61; `deep_copy\(\s*char\s*\*` is on 99 lines / 57 files) becomes a move of an owned value, whose `size()` the cache charges (3.7.2, KV cache store).

### Example E1: the PX SQC buffer (bc31e03f4)

C++ before bc31e03f4, and at 834bbee1e (src/sql/engine/px/ob_dfo_scheduler.cpp:382-394):

```cpp
// before: the buffer lives in the execution's allocator, which the async task could outlive
char *ser_buf = static_cast<char *>(exec_ctx.get_allocator().alloc(ser_len));
(void)ex_rpc::async_call([ser_buf, ser_pos]() {
  return px_init_sqc_fast_in_proc(ser_buf, ser_pos); });
// 834bbee1e: the closure shares ownership
std::shared_ptr<char[]> ser_buf(new (std::nothrow) char[ser_len]);
(void)ex_rpc::async_call([ser_buf, ser_pos, runtime_services]() {
  return launch_sqc_fast_local(ser_buf.get(), ser_pos, runtime_services); });
```

Rust:

```rust
let mut ser_buf = Vec::with_capacity(args.get_serialize_size());
args.serialize(&mut ser_buf)?;
let runtime_services = exec_ctx.get_runtime_services().clone();   // Arc'd services
ex_rpc::async_call(move || launch_sqc_fast_local(&ser_buf, runtime_services));
```

Writing the buggy form, `let ser_buf = exec_ctx.arena().bump().alloc_slice_copy(..)`, gives a `&'q [u8]`, and `async_call` rejects the closure because it is not `'static`. ARCHITECTURE §5 lets PX workers share the plan by `Arc` without this serialization; the rule holds either way.

### Example E2: a queued task with a hand-kept count (9d24b8807)

C++ (src/query/vector/ob_vector_embedding_handler.cpp:919-933, and :336-339 since 9d24b8807):

```cpp
void ObEmbeddingTask::release_if_managed()
{
  if (need_callback()) {
    int64_t v = ATOMIC_AAF(&ref_cnt_, -1);
    if (0 == v) { this->~ObEmbeddingTask(); ob_free(this); }
  }
}
// init: copies added by 9d24b8807; before it, model_url_ = model_url; shared the caller's bytes
} else if (OB_FAIL(ob_write_string(allocator_, model_url, model_url_))) {
```

Rust:

```rust
pub struct ObEmbeddingTask {
    model_url_: Box<[u8]>, model_name_: Box<[u8]>, provider_: Box<[u8]>, user_key_: Box<[u8]>,
    input_chunks_: Vec<Box<[u8]>>,
    cb_handle_: Option<Arc<dyn ObIEmbeddingCallback>>,
    state_: Mutex<EmbeddingTaskState>,
}
impl ObEmbeddingTaskHandler {
    pub fn push_task(&self, task: Arc<ObEmbeddingTask>) -> ObResult;   // T: Send + Sync + 'static
}
```

`retain_if_managed`/`release_if_managed` become `Arc::clone` and drop. The two ownership modes, a task whose memory its creator owns (constructor at :266; destroyed by hand without a free at :1356) and a self-freeing task, become one `Arc`. `model_url_: &'q [u8]`, the pre-fix shape, does not compile, because the pool needs `'static`.

## 3.4 Shared objects and their reference counts

Today: `\b(inc_ref|dec_ref)\w*\s*\(` is on 441 lines / 127 files; the sweep's refcount.tsv has 545 rows over 183 files. Rules:

**R1.** An `inc_ref`/`dec_ref` pair becomes an `Arc` clone and drop (E2); two counters on one object become two `Arc`s (E5). Transaction-context references (`acquire_ctx_ref`, `revert_tx_ctx` and the like) become `Arc<ObPartTransCtx>` (PLAN §3). No hand-written count fields remain outside the named crates.

**R2.** A last release that routes the object somewhere (a pool, a GC queue, a budget), as `ObMetaObjGuard<T>` does (src/storage/meta_mem/ob_meta_obj_struct.h:355-384), is a handle type with a private `Option<Arc<T>>` whose `Drop` calls `Arc::into_inner`, which returns the value only to the last owner (E4). Every holder goes through the handle, so no bare clone can skip the routing. A release into a budget needs no code: the object holds its `Charge`, which drops with it.

**R3.** A pool stays only when Decision 12 names its size limit: the KV-cache handle pool (src/share/cache/ob_kvcache_store.cpp:979-1032). Pools that only reuse memory go: `op_alloc`/`op_free` (`\bop_alloc\w*\(` 26 lines, `\bop_free\w*\(` 25), the meta-object pools behind `ObMetaObjGuard`, the DTL buffer free queue (src/sql/dtl/ob_dtl_channel_mem_manager.cpp:77-96, :145). jemalloc keeps per-thread caches, and Decision 1 (b) does not count memory use. The large-tablet pool is the one pool whose size a user can hit and Decision 12 does not name; see 3.7.5 and objection 2.

**R4.** A guard that hands out `const T *&` valid while it lives (`ObSchemaGetterGuard`, src/share/schema/ob_schema_getter_guard.h:248-270; the name is on 1,967 lines / 396 files) returns `&'g T` borrowed from the guard. The guard holds an `Arc` of the schema version, replacing `ObSchemaMgrItem::ref_cnt_` (src/share/schema/ob_schema_mgr_cache.h:65-95). A caller that keeps a schema object past the guard clones an `Arc`. dd899675c, a schema arena reset while a fallback schema was built from it, cannot happen when a version is immutable and freed with its last `Arc`.

**R5.** Hand-written reclamation (the KV cache's hazard versions, QClock, the retire station, hazard refs, memstore quiescence) is replaced as ARCHITECTURE §11 says: locks first, ob-epoch only where the gate needs lock-free reads. A reader keeps memory alive by holding an `Arc` or an epoch guard. The -4013 retry loops around hazard-pointer slots (src/share/cache/ob_kvcache_store.cpp:405-407, :642-644) go with that code. An `ObKVCacheHandle` (src/share/cache/ob_kv_storecache.h:281) becomes an `Arc` of its cache entry (E8).

**R6.** A count that a virtual table or log prints becomes `Arc::strong_count`. No test reads one (`git grep -n -i -E 'select .*ref_count|ref_count.*from' -- 'tools/deploy/mysql_test/*.test'` finds 0 lines; `ref_count` is only in 4 DESC results).

### Example E4: the tablet handle

C++ (src/storage/meta_mem/ob_tablet_handle.cpp:67-111, abridged):

```cpp
obj_->update_wash_score(calc_wash_score(wash_priority_));
const int64_t ref_cnt = obj_->dec_ref();
const int64_t hold_time = ObClockGenerator::getClock() - hold_start_time_;
if (OB_UNLIKELY(hold_time > HOLD_OBJ_MAX_TIME && need_hold_time_check())) { /* WARN, 2 hours */ }
if (OB_UNLIKELY(ref_cnt < 0)) { /* ERROR */ }
else if (0 == ref_cnt) {
  ...
  } else if (OB_FAIL(t3m_->push_tablet_into_gc_queue(obj_))) {
```

Rust:

```rust
pub struct ObTabletHandle {
    obj_: Option<Arc<ObTablet>>,
    hold_start_time_: i64,
    wash_priority_: WashTabletPriority,
    t3m_: Weak<ObStorageMetaMemMgr>,
}
impl Drop for ObTabletHandle {
    fn drop(&mut self) {
        let Some(obj) = self.obj_.take() else { return };
        obj.update_wash_score(self.calc_wash_score(self.wash_priority_));
        let hold_time = ObClockGenerator::get_clock() - self.hold_start_time_;
        if hold_time > HOLD_OBJ_MAX_TIME {
            log_warn!(OB_ERR_TOO_MUCH_TIME, "The meta obj reference count was held for more than two hours ", K(hold_time));
        }
        if let Some(tablet) = Arc::into_inner(obj) {
            if let Some(t3m) = self.t3m_.upgrade() { t3m.push_tablet_into_gc_queue(tablet); }
        }
    }
}
```

The negative-count error and the `ob_abort` on a missing pool have no counterpart. The temporary-tablet branch (ob_tablet_handle.cpp:95-104) drops the tablet in place. Macro-block references stay a separate owner inside `ObTablet` whose `Drop` releases them.

### Example E5: two counters on one IO result

C++ (src/share/io/ob_io_define.cpp:687-704, :745-756): `result_ref_cnt_` reaching 0 frees the result into the IO allocator; `out_ref_cnt_`, counted by user handles, reaching 0 cancels the IO and destroys the callback.

Rust:

```rust
pub struct ObIOResult { charge_: Charge /* IO budget */, io_callback_: Mutex<Option<Box<dyn ObIOCallback>>>, /* ... */ }
pub struct ObIOHandle { out_: Option<Arc<IoOutRef>> }          // Clone = inc_out_ref
struct IoOutRef { result: Arc<ObIOResult> }
impl Drop for IoOutRef {                                         // dec_out_ref reaching 0
    fn drop(&mut self) {
        self.result.cancel();
        drop(self.result.io_callback_.lock().take());
    }
}
```

Requests hold `Arc<ObIOResult>`; the last clone drops `charge_`, which is `io_allocator_.free` (ob_io_define.cpp:701). `IoOutRef` is named after the C++ field `out_ref_cnt_`.

## 3.5 How each C++ memory construct translates

One mapping per construct (RULEBOOK §2). The kit's error-recovery rule applies: each guard row carries `allocation-guard` or `precondition-guard` in its evidence notes (templates/inventory.tsv).

| C++ | Rust |
|---|---|
| `ObIAllocator &` for statement or execution data | `&'q Bump` |
| `ObIAllocator &`/`*` whose data the callee keeps (cache, plan, session, queue) | none; the callee takes an owned value |
| `ObIAllocator *allocator_` member | nothing, when the struct's data become owned collections; `&'q Bump`, with a lifetime on the struct, when it is statement- or execution-scoped |
| `ObArenaAllocator` object | `ObArenaAllocator`, `counted_by(tracker)` exactly when the C++ arena drew from the request's context tree (3.6.2) |
| `ObSafeArenaAllocator` | a per-task arena (M6) |
| `ObString` parameter | `&[u8]` |
| `ObString` member (1,041 lines / 270 files, `^\s*(common::)?ObString\s+\w+_\s*;`) | `&'q [u8]` in statement- or execution-scoped structs; `Box<[u8]>`, `Vec<u8>` or `Arc<[u8]>` elsewhere, by the struct's owner |
| `assign_ptr` (963 lines) | a borrow |
| `ob_write_string(alloc, src, dst)` (598 lines) | `bump.alloc_slice_copy(src)`, or `src.to_vec()`, `Box::from(src)`, `Arc::from(src)` |
| `deep_copy(char *buf, len, ...)` into receiver memory | move an owned value; the receiver charges its size |
| placement new (2,581 lines / 681 files) and explicit destructor calls (1,278 / 498) | a constructor and `Drop` |
| `OB_NEW*` (689), `OB_DELETE*` (138), `ob_malloc` (182), `ob_free` (268) | `Box::new`, owned collections, drop |
| `CURRENT_CONTEXT`, `WITH_CONTEXT`, `CREATE_WITH_TEMP_CONTEXT`, `CREATE_CONTEXT` (71) / `DESTROY_CONTEXT` (79) | an owner passed as an argument or held as a field (M8); a temporary context becomes a local `ObArenaAllocator` or owned collections |
| `ObMemAttr` (1,505 lines / 600 files), labels, ctx ids | dropped; a label survives only as a `Budget` name (3.6.5) |
| `ObMallocHookAttrGuard` (103 lines / 36 files), `ObMallocCallbackGuard` (7 uses) | dropped; both act only on the Linux malloc hook or the obmalloc allocators (src/oblib/lib/alloc/ob_ctx_allocator.cpp:394-418), neither in use on macOS |
| `inc_ref`/`dec_ref`, `revert_*` | `Arc` clone and drop (3.4) |
| `ObBlockAllocMgr`, a FIFO or slice allocator with a limit | a `Budget` if Decision 12 names the owner (3.7.2); plain allocation otherwise (3.7.5) |
| `MemoryUsageTracker`, `TrackedAllocator` | a `Charge` on the matching `Budget` |
| `if (OB_ISNULL(p = alloc(n))) ret = OB_ALLOCATE_MEMORY_FAILED;`, and the same with another code (`OB_SQL_RESOLVER_NO_MEMORY`, 8 lines) | removed; the allocation aborts on failure (3.7.1) |
| the same where `n` can be 0 or less | that case stays: `if n <= 0 { return Err(OB_ALLOCATE_MEMORY_FAILED) }`, marked `BUG(port):`, since `ob_malloc` and the arenas return NULL for such sizes (src/oblib/lib/allocator/ob_malloc.h:118-120; src/oblib/lib/allocator/page_arena.h:623) |
| `OB_FAIL(arr.push_back(x))`, `reserve` | the container keeps its C++ signature and its other codes (the element's fallible `assign`, `ObFixedArray`'s `OB_SIZE_OVERFLOW`, C23; ARCHITECTURE §2); its allocation aborts |
| `OB_ALLOCATE_MEMORY_FAILED` at a named owner or logical limit | unchanged (3.7.2, 3.7.3) |
| `OB_ALLOCATE_MEMORY_FAILED` injected by a tracepoint (for example src/observer/ob_uniq_task_queue.h:589-591) | unchanged; tracepoints keep their C++ behavior (ARCHITECTURE §7.1) |
| a kept library's out-of-memory code mapped to -4013 (vsag `NO_ENOUGH_MEMORY`, `std::bad_alloc` in islands) | unchanged (3.7.6) |
| comparisons with `OB_ALLOCATE_MEMORY_FAILED` (78 lines / 47 files) | kept as written in translated code, since codes are values (ARCHITECTURE §2); they go only with replaced code such as the hazard-pointer loops (R5) |

Patterns behind the table's counts: `\bassign_ptr\(`, `\bob_write_string\(`, `new\s*\(\s*[^)]*\)\s*[A-Za-z_:][\w:<>, ]*\s*[\(;{]`, `(->|\.)\s*~\w+\s*\(\s*\)`, `OB_NEW\w*\(`, `OB_DELETE\w*\(`, `\bob_malloc\(`, `\bob_free\(`, `\bCREATE_CONTEXT\(`, `\bDESTROY_CONTEXT\(`, `\bObMemAttr\b`, `ObMallocHookAttrGuard`, `lib::ObMallocCallbackGuard guard`, `=\s*(common::)?OB_SQL_RESOLVER_NO_MEMORY\s*;` and `(==|!=)\s*(common::)?OB_ALLOCATE_MEMORY_FAILED|OB_ALLOCATE_MEMORY_FAILED\s*(==|!=)`.

The 4,737 assignments (`=\s*(::)?(oceanbase::)?(common::)?OB_ALLOCATE_MEMORY_FAILED\s*;`, 1,171 files) therefore mostly disappear with their allocation. The sweep's oom-sites.tsv (183 rows) marks 30 as "logical" because no allocation appears in the 4 lines above them. Reading them, most report an allocation made further up or an allocator's `-ENOMEM` (src/oblib/lib/hash/ob_link_hashmap.h:454); the classification pass confirms each row with this table.

## 3.6 What is counted, and how

### 3.6.1 Budgets and charges

**A1.** A budget is a limit and an atomic counter. A charge taken with `try_charge` follows `ObBlockAllocMgr::alloc_block` (src/oblib/lib/allocator/ob_block_alloc_mgr.h:38-50): add, compare with the limit, undo when over, and log the refusal at most once a second.

**A2.** A `Charge` lives in the same struct as the memory it counts, so both drop together. It is taken before the memory is allocated. A refusal becomes the owner's own code.

**A3.** Engine crates implement neither `GlobalAlloc` nor `Allocator`, and engine code has no `A: Allocator` parameters (ARCHITECTURE §3.1-3.2). ARCHITECTURE §3.2 rejected a counting global allocator, allocators that refuse over a limit and per-context jemalloc arenas; none comes back as a helper.

**A4.** Budgets trip by the C++ formulas at the C++ check points, not at the same byte counts (**default, for the developer to confirm**, ARCHITECTURE default 9), except the SQL work area, which counts the C++ figures (3.6.3). A charge counts what the Rust owner holds: whole blocks where the C++ counts blocks, such as KV-cache memory blocks and temp-file pages.

**A5.** Budgets register in a list the `RuntimeContext` keeps, `Mutex<Vec<Weak<Budget>>>`, which the memory virtual tables read (3.6.5).

**A6.** An owner allocates with ordinary calls after a successful charge. The typed error comes from the budget check; a system allocation failure after it aborts like any other (3.7.1). This keeps `try_reserve` to client-sized buffers (3.7.4).

#### Example E6: a clog task allocation

C++ (src/logservice/palf/log_engine.cpp:876-878; the allocator's limit, src/logservice/ob_log_allocator.cpp:248-258):

```cpp
} else if (NULL == (flush_log_task = alloc_mgr_->alloc_log_io_flush_log_task(palf_epoch_))) {
  ret = OB_ALLOCATE_MEMORY_FAILED;
  PALF_LOG(ERROR, "alloc_log_io_flush_log_task failed", K(ret));
```

Rust:

```rust
impl ObLogAllocator {
    pub fn alloc_log_io_flush_log_task(&self, palf_epoch: i64) -> Option<Box<LogIOFlushLogTask>> {
        let charge = self.clog_.try_charge(size_of::<LogIOFlushLogTask>() as i64)?;
        self.flying_log_task_.fetch_add(1, SeqCst);
        Some(Box::new(LogIOFlushLogTask::new(palf_epoch, charge)))
    }
}
```

`set_limit` keeps its formulas on `clog_` and `replay_` (3.7.2). The call site keeps its shape: `None` sets `ret` to `OB_ALLOCATE_MEMORY_FAILED` and logs at ERROR. The C++ charges whole slab blocks and Rust charges objects; that is A4's byte difference. The task gives its charge back when it drops.

### 3.6.2 The query memory tracker (-11049)

Today the tracker is thread-local (src/sql/executor/ob_memory_tracker.cpp:25). The request worker points it at its temporary context (ob_th_worker.cpp:244-245), and a check compares that context tree's `tree_mem_hold()` with `memory_budget / 100 * query_memory_limit_percentage` (ob_memory_tracker.cpp:29-35; default 32, src/share/parameter/ob_parameter_seed.ipp:1108). A thread with no tracker, such as a PX worker, passes every check (:39). The tree holds only memory drawn from that context and its children; `ObArray` and `ObSEArray` join it only with `auto_free`, false by default (ob_array.h:839, :858).

**T1. One tracker per request, passed explicitly.** The request entry creates `Arc<ObMemTracker>` where the worker makes its temporary context. Compile, execution and island calls reach it through the context argument they already have: exec context, optimizer context, resolver params, transformer context. ARCHITECTURE §11 makes state that changes results a parameter. Inner SQL runs under its caller's tracker, as today: its guard is commented out (src/observer/ob_inner_sql_connection.cpp:681) and its result's context is a child of the caller's (src/observer/ob_inner_sql_result.cpp:81).

**T2. The request's total is its counted arenas plus its charges** (ARCHITECTURE §3.2), in the tracker's counter:
- counted arenas: the statement arena, the execution arenas, and the IR's typed arenas (`Arena<T>` capacity times `size_of::<T>()`, reported through one `Charge` the compile context holds);
- charges: the frame's result buffers, operator stores and hash tables (next to their work-area charge), and memory the islands keep for the request (3.6.4).

An arena or store counts exactly when its C++ counterpart drew from the request's context tree. The inventory row says `counted` in its evidence notes.

**T3. Arenas report at known points.** An arena's owner sets a `Charge` on the tracker's counter to `Bump::allocated_bytes()`, which is O(1) (bumpalo src/lib.rs:2223-2227), at every check point below before the check runs, at each reset, and when the arena drops; `ObArenaAllocator::sync` does this for arenas kept in one. A suspended arena's size is therefore as of its last report (**default, for the developer to confirm**).

**T4. The same check points and cadence.** The tracker keeps C++'s two `uint16_t` counters, wrapping. `try_check_tick_` is shared by every try site, so the 32- and 1,024-call cadences interleave as today.

| C++ check point | Cadence | Rust caller |
|---|---|---|
| `ObThWorker::check_status` (ob_th_worker.cpp:333), reached by `THIS_WORKER.check_status()` (52 lines / 38 files) | every call | the request status check on the caller's context |
| `ObExecContext::check_status` (src/sql/engine/ob_exec_context.cpp:679) | every call | `ObExecContext::check_status` |
| `ObRawExprFactory::try_check_status` (src/sql/resolver/expr/ob_raw_expr.h:4999, :5074-5079) | its own tick, every 1,024 creates, then a full check | `create_raw_expr` (the SQL front section's expression store) |
| `ObSelectResolver::resolve` (src/sql/resolver/dml/ob_select_resolver.cpp:1005-1006) | try, 32 | resolver params |
| `ObTransformRule` (src/sql/rewrite/ob_transform_rule.cpp:280, :287) | try, 32 | transformer context |
| `new_node` (src/sql/parser/parse_node.c:183-186) | try, 1,024, through the C callback | sql-parser-sys `try_check_mem_status` |

**T5. The parser's callback.** `try_check_mem_status(int64_t)` has no receiver (parse_node.c:178). sql-parser-sys therefore keeps a per-call slot: the Rust `parse_sql` wrapper sets it to the statement's arena and tracker for the duration of the C call and clears it with a scope guard. The callback syncs the arena, runs `try_check_status(check_try_times)` and returns the code; the C core then turns any non-zero result into -4013 through `malloc_new_node` and `YY_FATAL_ERROR` (src/sql/parser/sql_parser_base.h:503-508; sql_parser_mysql_mode.l:31, :1840-1853; parse_define.h:26), while the -11049 message stays in the warning buffer, as today. `malloc_pool` points at the same pair, and `parse_malloc` keeps the 8-byte size header (src/sql/parser/parse_malloc.cpp:90-105). See objection 1.

#### Example E7: the check

C++ (ob_memory_tracker.cpp:36-64):

```cpp
int64_t tree_mem_hold = mem_tracker_.mem_context_->tree_mem_hold();
++mem_tracker_.check_status_times_;
if (0 == mem_tracker_.cache_mem_limit_
  || (mem_tracker_.check_status_times_ % UPDATE_MEM_LIMIT_THRESHOLD == 0)) {
  update_mem_limit();
}
if (mem_tracker_.cache_mem_limit_ > 0
    && tree_mem_hold >= mem_tracker_.cache_mem_limit_) {
  ret = OB_EXCEED_QUERY_MEM_LIMIT;
  SQL_LOG(WARN, "Exceeded memory usage limit", K(ret), K(tree_mem_hold), K(mem_tracker_.cache_mem_limit_));
  LOG_USER_ERROR(OB_EXCEED_QUERY_MEM_LIMIT, mem_tracker_.cache_mem_limit_, tree_mem_hold);
}
```

Rust:

```rust
pub struct ObMemTracker {
    counter: Arc<Budget>,          // no limit: counted arenas and charges
    limit_source: Arc<AtomicI64>,  // memory_budget / 100 * query_memory_limit_percentage, or 0
    cache_mem_limit_: AtomicI64,
    check_status_times_: AtomicU16,
    try_check_tick_: AtomicU16,
}
impl ObMemTracker {
    pub fn check_status(&self) -> ObResult {
        let tree_mem_hold = self.counter.hold();
        let times = self.check_status_times_.fetch_add(1, SeqCst).wrapping_add(1);
        if self.cache_mem_limit_.load(SeqCst) == 0 || times % UPDATE_MEM_LIMIT_THRESHOLD == 0 {
            self.cache_mem_limit_.store(self.limit_source.load(SeqCst), SeqCst);
        }
        let limit = self.cache_mem_limit_.load(SeqCst);
        if limit > 0 && tree_mem_hold >= limit {
            log_warn!(OB_EXCEED_QUERY_MEM_LIMIT, "Exceeded memory usage limit", K(tree_mem_hold), K(limit));
            return Err(log_user_error!(OB_EXCEED_QUERY_MEM_LIMIT, limit, tree_mem_hold));
        }
        Ok(())
    }
    pub fn try_check_status(&self, check_try_times: i64) -> ObResult {
        let tick = self.try_check_tick_.fetch_add(1, SeqCst).wrapping_add(1);
        if i64::from(tick) % check_try_times == 0 { self.check_status() } else { Ok(()) }
    }
}
```

The config module keeps `limit_source` current, so ob-base needs no config types. The message keeps its catalog text, two spaces after `mem_hold=%ld),` included (src/share/ob_errno.def:1880).

### 3.6.3 Counters that decide behavior without an error

- **SQL work area.** The limit is `memory_budget / 100 * ob_sql_work_area_percentage`, minus the non-active work areas, and at least 1 byte (src/sql/engine/ob_sql_memory_manager.cpp:807-817; default 5, src/share/system_variable/ob_system_variable_init.json:1798-1811). Its counter replaces the WORK_AREA `MemoryUsageTracker` (ob_sql_memory_manager.cpp:36-47, :430-431). Stores charge it as blocks come and go, with the stored-row sizes the execution section fixes to the C++ figures so that dumps come at the same rows (its rules 4.4 and 6.9; ARCHITECTURE §3.2 and default 9 except the work area from A4). The spill decision reads the operator's charged bytes where the C++ reads `mem_context_->used()` (src/sql/engine/sort/ob_sort_op_impl.cpp:1024-1025, in `preprocess_dump` from :1020). Spills are never triggered by a failed allocation (C9); the processor's only -4013 is a 1-byte allocation (src/sql/engine/ob_sql_mem_mgr_processor.cpp:100-104), which becomes plain.
- **Plan cache.** The C++ already counts explicitly: each cache object adds its size once (src/sql/plan_cache/ob_plan_cache.cpp:1662-1668). The limit is `runtime_mem / 100 * mem_limit_pct` (src/sql/plan_cache/ob_plan_cache.h:293-305). A full cache refuses new plans with -5226 `OB_REACH_MEMORY_LIMIT`, and a plan at or above the high mark is not cached (ob_plan_cache.cpp:885-892). A Rust plan's size is the bytes code generation copied into it plus its arrays' capacities, counted while building. Plan-cache hits are compared (PLAN §4, family 7), so Step 2a records plan sizes next to the C++ ones.
- **Memstore, tx data, MDS and the throttle.** Memtable pages charge the memstore counter as `ObFifoArena`'s hold does. The freeze trigger (src/storage/tx_storage/ob_memstore_freezer.cpp:1050-1055) and memstore-full (3.7.2) read it. The TxShare throttle only sleeps and never fails an allocation (C10), so it keeps its formulas over these counters and is outside Decision 12.

### 3.6.4 Island memory

ARCHITECTURE §9.3-9.4 sets the rule and the islands section applies it (its 7.8). At the level of this section:
- Memory an island keeps after an entry returns comes from a receiver that is the thing charged. For geo that is the caller's per-execution arena, which the request counts, so -11049 keeps counting built geometries; GIS temporary contexts are children of the query's context today (src/share/geo/ob_geo_utils.cpp:640, :721, :1857; src/share/geo/ob_s2adapter.cpp:286). For the parser it is the statement arena. For vsag it is the vector budget, whose bridge applies `ObVectorMemContext::alloc`'s check and returns null on a refusal (3.7.2).
- Geo kernel scratch memory, which no check point can see while it lives, is not charged (the islands section's default).
- Library-internal `malloc` stays uncharged, as on today's macOS build, and each side frees only what it allocated.
- CRoaring's process-wide memory hook (src/share/roaringbitmap/ob_rb_memory_mgr.cpp:153-165) is allowed process-wide state (ARCHITECTURE §7.1). Its allocations never refuse (a general out-of-memory aborts) and each counts toward the vector budget (ARCHITECTURE §9.4, default 34).

### 3.6.5 The global allocator, labels and the memory virtual tables

- `ObJemalloc` (ob-platform) implements `GlobalAlloc` over jemalloc 5.3.1 from seekdb-jemalloc-sys =0.2.2 with its `stats` feature (deps/external/Cargo.toml:10). That crate exposes no Rust API (its src/lib.rs:4-5), so ob-platform declares the `je_` functions itself, built with `--with-jemalloc-prefix=je_` as today (deps/external/cmake/Jemalloc.cmake:40), and exports `je_malloc_conf` with the reference's value, `"background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:0"` (src/oblib/lib/allocator/ob_malloc.cpp:38-42).
- The seekdb binary crate declares `#[global_allocator]` and, on macOS, has ob-platform promote the jemalloc malloc zone before any thread starts, as `inner_main` does (src/observer/main.cpp:647-653). Island and library `malloc` then reach the same jemalloc (ARCHITECTURE §7.3).
- Labels and ctx ids are not counted per allocation today: `ob_malloc` ignores the attribute on jemalloc (ob_malloc.h:117-125), and ctx limits are enforced on neither backend (C2). In Rust a label survives only as a `Budget` name.
- The memory virtual tables are filled from the registered budgets and the request trackers (**default, for the developer to confirm**, ARCHITECTURE default 9). The SQL audit's `request_memory_used_`, which only obmalloc's callbacks fill today (src/oblib/lib/alloc/ob_ctx_allocator.cpp:394-418), comes from the request tracker's counter. No test reads these rows (R03 §1.9; `request_memory_used` is in no .test file).

## 3.7 What happens when memory runs out (Decision 12, exactly)

### 3.7.1 A general allocation failure aborts the process

- `Vec`, `Box` and `String` call `handle_alloc_error`, which prints "memory allocation of N bytes failed" to stderr and aborts; the server log gets nothing, since `std::alloc::set_alloc_error_hook` is not stable (Decision 16). bumpalo panics with "out of memory" (bumpalo src/lib.rs:2465-2467), and capacity overflow panics; `panic = "abort"` turns both into an abort (ARCHITECTURE §10).
- sql-nio already works this way (rust/sql-nio/src/session_storage.rs:28; rust/Cargo.toml:16).
- On macOS and Linux this is today's behavior: jemalloc does not return NULL in practice (C1; C25, an assumption about the OS).

### 3.7.2 The budget owners

Each owner keeps its C++ formula, parameter, check point, code, message and behavior around the error.

| Owner | Limit (formula, parameter) | Check point | Code, behavior |
|---|---|---|---|
| clog | runtime memory size / 100 × 30 (src/logservice/ob_log_allocator.cpp:248-258; ob_log_allocator.h:87; size from src/logservice/ob_log_allocator_mgr.cpp:170-179) | 5 task allocations (src/logservice/palf/log_engine.cpp:876-877, 892-893, 910-911, 933-934, 966-967) and the palf sliding-window array (src/logservice/palf/fixed_sliding_window.h:111-114), all drawn from `clog_blk_alloc_` | -4013, logged at ERROR (E6) |
| replay | min(size / 100 × 5, 512 MiB) (src/logservice/ob_log_allocator.cpp:254; ob_log_allocator.h:89-91) | replay buffer and task (src/logservice/replayservice/ob_log_replay_service.cpp:1020-1031, 1092-1093; src/logservice/replayservice/ob_replay_status.cpp:1027-1028) | `OB_EAGAIN` directly; tx replay rewrites -4013, `OB_MINOR_FREEZE_NOT_ALLOW` and `OB_SCN_OUT_OF_BOUND` to `OB_EAGAIN` (src/storage/tx/ob_tx_replay_executor.cpp:696-704); replay retries |
| IO allocator | `calc_io_memory` (src/share/io/ob_io_manager.cpp:661-693): 256 MiB up to 1 GiB of configured IO memory, 1 GiB up to 4, 2 GiB up to 8, otherwise the configured amount; applied at :702 to the FIFO of src/share/io/ob_io_struct.cpp:171-188 | request and result objects (src/share/io/ob_io_manager.cpp:723-770); data buffers at request init for writes (src/share/io/ob_io_define.cpp:866) and at prepare for reads (:1129) | objects retry every min(remaining, 1 ms) until the IO timeout, then `OB_TIMEOUT` (src/share/io/ob_io_manager.cpp:933-985); buffers -4013 at once. Buffers are ob-platform's `AlignedBuf` behind a charge |
| KV cache store | `kvcache_memory_limit`, default min(40% of memory_budget, MAX_KVCACHE_MEMORY_SIZE) (src/share/config/ob_server_config.cpp:46, 205-215), rounded down to whole memory blocks (src/share/cache/ob_kvcache_store.h:106-112) | `reserve_store_size` (src/share/cache/ob_kvcache_store.cpp:915-961) | lock-free reservation, then under `wash_out_lock_` one synchronous wash of the excess; -4013 at :922 and :955, reaching reads through `put_cache_block` (src/storage/blocksstable/ob_micro_block_cache.cpp:442-446). The unit of charge and wash stays the memory block (E8) |
| micro block cache | a constant 4 GiB FIFO (src/storage/blocksstable/ob_micro_block_cache.cpp:1003-1013), handed out by `get_allocator` (:1115-1120) | IO callback objects (:843, :1035, :1071, :1328) and block buffers (:502, :682, :698-715) | -4013 at once. The 3-retry loop in `alloc_data_buf` (:387-408, "UNUSED NOW" at :389) has no caller and is not ported: the only calls are `ObTmpPageCache`'s own override (src/storage/tmp_file/ob_tmp_file_cache.cpp:698, :739, :769) |
| vector module | `vector_memory_limit`, default 50% of effective memory (src/share/config/ob_server_config.cpp:225-231); the hold is vsag's counted bytes plus vector-index roaring bytes (src/storage/allocator/ob_vector_allocator.cpp:29-44) | `ObVectorMemContext::alloc`: every 20th allocation or one of 2 MiB or more (src/storage/allocator/ob_vector_allocator.cpp:131-155; ob_vector_allocator.h:36-37) refuses when limit ≤ 0, hold ≥ limit or size > limit − hold | the caller gets null; -7603 stays in a local `ret` (:142); vsag reports `NO_ENOUGH_MEMORY`, which maps to -4013 (src/oblib/lib/vector/ob_vsag_adaptor.cpp:61-62). Rust charges every allocation and refuses only at those checks |
| query memory tracker | 3.6.2 | 3.6.2 | -11049, "Exceed query memory limit (mem_limit=%ld, mem_hold=%ld),  please check whether the query_memory_limit_percentage configuration item is reasonable." |
| memstore full | memstore limit, default 50% of memory_budget (src/share/parameter/ob_parameter_seed.ipp:92-96), minus `REPLAY_RESERVE_MEMSTORE_BYTES` = 100 MiB for user DML (src/storage/tx_storage/ob_memstore_freezer.h:126) | `check_memstore_full_` (src/storage/tx_storage/ob_memstore_freezer.cpp:1059-1090) before DML (src/storage/tx_storage/ob_access_service.cpp:187, :655-657); inner tablets skip | -4030 "Server runtime memory limit exceeded" (src/share/ob_errno.def:100). Recomputed only when the last check found it full or 100 ms have passed (.h:127). The C++ keeps both values per thread, one pair for user DML and one for internal checks (:1098-1099, :1111-1112); Rust keeps each pair once, in the freezer (**default, for the developer to confirm**) |
| temp-file write buffer pool | memory_budget / 50 × `_temporary_file_io_area_size` (default 1, src/share/parameter/ob_parameter_seed.ipp:425), rounded up to whole pool blocks, one block when the parameter is 0, refreshed at most every 10 s (src/storage/tmp_file/ob_tmp_file_write_buffer_pool.cpp:977-999) | `alloc_page_` grows the pool by blocks up to the limit (:161-197) | -9124 "fail to allocate a tmp file page" (src/share/ob_errno.def:1740) |
| SQL work-area spill | 3.6.3 | the operator's memory processor | a spill, never an error |

#### Example E8: the KV cache store reservation

C++ (src/share/cache/ob_kvcache_store.cpp:915-961), in outline: -4013 for a bad size; a lock-free reservation; else, under `wash_out_lock_`, a second try, then the block is accounted before a synchronous wash of the excess, and given back with -4013 if the wash could not make room.

Rust:

```rust
fn reserve_store_size(&self, block_size: i64) -> ObResult<Charge> {
    let cache_limit = self.store_.limit();          // compute_fixed_cache_limit, set at each reload
    if block_size <= 0 || cache_limit <= 0 || block_size > cache_limit {
        return Err(OB_ALLOCATE_MEMORY_FAILED);
    }
    if let Some(c) = self.store_.try_charge(block_size) { return Ok(c); }
    let _guard = self.wash_out_lock_.lock();
    if let Some(c) = self.store_.try_charge(block_size) { return Ok(c); }
    let c = self.store_.charge(block_size);          // accounted before washing
    let wash_size = max(self.store_.hold() - cache_limit, 0);
    if wash_size == 0 { return Ok(c); }
    match self.sync_wash_mbs(wash_size) {
        Ok(wash_blocks) => { self.free_mbs(wash_blocks); Ok(c) }
        Err(_) if self.store_.hold() <= cache_limit => Ok(c),
        Err(tmp_ret) => {
            if tmp_ret != OB_CACHE_FREE_BLOCK_NOT_ENOUGH && tmp_ret != OB_SYNC_WASH_MB_TIMEOUT {
                log_warn!(tmp_ret, "Fail to synchronously wash KV cache before allocating memblock", K(block_size));
            }
            Err(OB_ALLOCATE_MEMORY_FAILED)           // c drops here: the reservation is returned
        }
    }
}
```

The C++ lock-free path is a compare-and-swap that never passes the limit (:963-977); `try_charge` adds and undoes, which differs only in which of two racing callers is refused. A memory block is an `Arc` holding its `Charge` and a handle-pool slot; entries hold an `Arc` of their block, and an `ObKVCacheHandle` an `Arc` of its entry. A block's charge and slot return once the wash has removed its entries and the last reader drops.

### 3.7.3 The logical limits

These are not allocations. Each stays as written.

| Limit | Where | Code |
|---|---|---|
| hash-join partition depth | `sizeof(uint64_t) * CHAR_BIT <= part_shift_` (src/sql/engine/join/ob_hash_join_op.cpp:838-843; src/sql/engine/basic/ob_hash_partitioning_infrastructure_op.h:1698-1700) | -4013 |
| KV-cache handle pool | a fixed table of `compute_mb_handle_num` handles (src/share/cache/ob_kvcache_store.h:102-105); `pop_mb_handle_with_recovery` supplies unused handles, reclaims released ones, washes once (src/share/cache/ob_kvcache_store.cpp:979-1032) | -4013 at :1030 |
| IVF cache | `ObIvfCacheMgr::check_memory_limit`: a limit flag rechecked every 10 calls (src/observer/vector_index/ob_vector_index_ivf_cache_mgr.cpp:136-165) | -4013 at :163 |
| vsag `NO_ENOUGH_MEMORY` | mapping in the kept vsag shim (src/oblib/lib/vector/ob_vsag_adaptor.cpp:61-62) | -4013 |

The handle table stays sized by its formula, whose constants come from QClock (ob_kvcache_store.h:104) and are kept as numbers when QClock goes. Its recovery steps map onto the Rust reclamation of R5. Any other non-allocation -4013 the inventory confirms follows the UNKNOWN rule: kept as written, with `TODO(port):`, and listed for the developer.

### 3.7.4 Where fallible calls are allowed

- `try_reserve`, `try_reserve_exact`, `Bump::try_alloc*` and allocator-api2's `try_*` are in clippy's `disallowed-methods` for engine crates. Each allowed use carries `#[allow(clippy::disallowed_methods, reason = "<inventory row>")]`.
- They are allowed at the owners of 3.7.2, which by A6 do not need them, and in **client-sized buffers**. A client-sized buffer is one whose length comes from bytes the client sent (a packet length, a parameter count, a login attribute block, a compressed frame) before any SQL-level size check.
- Today that is sql-nio's 14 lines in 8 files (`git grep -n -E 'try_reserve' -- rust/`), which stay.
- SQL functions keep their own size checks, which run first. REPEAT returns `OB_ERR_FUNC_RESULT_TOO_LARGE` against `max_result_size` (src/sql/engine/expr/ob_expr_repeat.cpp:124-127) before it allocates (:134-137), and the allocation after such a check is ordinary.

### 3.7.5 Limits Decision 12 does not name

Decision 12 names its owners. These finite limits exist too (C6, rechecked):

| Limit | Where |
|---|---|
| LS allocator, 1 GiB | src/storage/tx_storage/ob_ls_service.cpp:123-129 |
| `ObResourceMap` default allocator | src/storage/ob_resource_map.h:233 |
| storage meta cache IO, 4 GiB | src/storage/meta_mem/ob_storage_meta_mem_mgr.cpp:213, :224 |
| async task queue, 1 GiB | src/oblib/lib/thread/ob_async_task_queue.h:112 |
| dedup queue, 1 GiB | src/oblib/lib/thread/ob_dedup_queue.h:137 |
| DDL scheduler, 1 GiB | src/rootserver/ddl_task/ob_ddl_scheduler.cpp:856 |
| DDL task executor, 1 GiB | src/share/ob_ddl_task_executor.h:154 |
| DDL redo replayer, 10 GiB | src/storage/ddl/ob_ddl_redo_log_replayer.h:45 |
| server storage meta persister, 512 MiB | src/storage/meta_store/ob_server_storage_meta_persister.cpp:36 |
| server checkpoint writer, 128 MiB | src/storage/slog_ckpt/ob_server_checkpoint_writer.cpp:36 |
| IO manager's own allocator, device channels only | src/share/io/ob_io_manager.cpp:113, :401 |
| large-tablet buffer pool: 4% of 2,500 = 100 buffers, not allowed over, waits up to 3 s for a wash, then -4013 | src/storage/meta_mem/ob_storage_meta_mem_mgr.h:131-157, src/storage/meta_mem/ob_storage_meta_mem_mgr.cpp:177, src/storage/meta_mem/ob_storage_meta_obj_pool.h:224-253 |

**Default, for the developer to confirm:** these limits and their -4013 are dropped, and their memory becomes plain allocation. Reasons:
- Decision 12 says typed errors stay at the named owners.
- No test expects -4013 (C18).
- memory_budget is about 19 GiB on this Mac (R03 §1.8); the judged workloads stay far below these caps (assumption, not measured per queue).

The alternative is to keep each as a further owner, which widens Decision 12.

### 3.7.6 Codes near -4013 that are not out-of-memory

- **-4080** `OB_EXCEED_MEM_LIMIT` is an internal signal. The PDML batch cache asks for a flush (src/sql/engine/pdml/static/ob_pdml_op_batch_row_cache.cpp:166), and row stores with dumping off return it (src/sql/engine/basic/ob_temp_block_store.cpp:1099; src/sql/engine/basic/ob_chunk_row_store.cpp:487). It stays a value.
- **-5226** `OB_REACH_MEMORY_LIMIT` is the plan cache's (3.6.3).
- **`std::bad_alloc` inside islands** keeps the existing catch mappings, for example geo's `OB_ERR_STD_BAD_ALLOC_ERROR` (ARCHITECTURE §9.3 rule 5, its default 17). It can come only from library-internal `new`, because Rust allocation callbacks abort instead of returning null, except the vector budget's refusal.
- **`OB_PARSER_ERR_NO_MEMORY`**, -4013 in the kept C parser (src/sql/parser/parse_define.h:26), stays. `parse_malloc` returns null only for a size of 0 or less, or a null pool (src/sql/parser/parse_malloc.cpp:94-95).

### 3.7.7 Family 12 and the judge

- The -11049 message prints `mem_hold`, which no Rust build matches byte for byte. That scenario replaces the number in its own test text, declared in 00b before its second sign-off (**default, for the developer to confirm**, ARCHITECTURE default 10).
- -7603 never reaches the client, so family 12 records what the C++ returns. The micro block cache gives -4013 without retries (ARCHITECTURE §18).
- Spilling is checked with `ob_sql_work_area_percentage=5`, which join_many_table.test and join_many_table_single_field.test also set (C19).

### 3.7.8 Later platforms

On Windows, Android and wasm, and in a library form, a general out-of-memory ends the process or host (Decision 12 notes). So the owners above are where sizing keeps it rare. On wasm, their limits come from the 2 GiB heap when wasm returns (ARCHITECTURE §13).

## 3.8 Conventions from the earlier sql-nio port

From /Users/colin/obsidian/tech/seekdb/migrate to rust/abi-naming.md and notes/ffi-mechanics.md; only the memory conventions are judged here.

| Convention | Verdict | Why |
|---|---|---|
| `_view`: borrowed, no ownership, the C++ side vouches for each `(data, len)` | adopt at island ABIs; each function states how long the view lives | the C boundary form of M5 and H1 |
| `_handle`: opaque, released exactly once, `_acquire`/`_release` | adopt the handle, released exactly once from the Rust wrapper's `Drop`; the entries are named `<tag>_<thing>_create`/`_destroy` (s7-islands-unsafe.md 7.2 rule 7) | ARCHITECTURE §9.3 rule 6 |
| receiver as the first argument, never a global | adopt for memory callbacks; the parser core keeps its C signatures | M8, T1, 3.6.4. The one exception is objection 1 |
| Rust entry points trust pointer/length pairs from C++ | adopt, only inside the named island crates | ARCHITECTURE §8 |
| generation-scoped leases, C++ holding Rust buffers across calls (notes/nio-abi-contracts.md) | reject | sql-nio becomes an `rlib` (ARCHITECTURE §1.1), and the request owns its bytes |

## 3.9 What a reviewer checks

1. The owner in the inventory row matches the Rust owner (M1).
2. IR types hold no `Rc` or `Arc`. Values enter a bump only through `arena_alloc` or `Copy` slices. No `Box::leak`, `mem::forget` or `bumpalo::boxed::Box` (M2, M7).
3. Nothing `&'q` reaches a spawn, queue, timer, channel, cache insert or `async_call`. No `unsafe impl Send` outside named crates (H1-H2).
4. PX, DAS parallel and DAG tasks receive no `ObMemTracker` and no counted arena (H4).
5. No `CURRENT_CONTEXT` remains; the memory owner is an argument or a field (M8).
6. Every `OB_ALLOCATE_MEMORY_FAILED` in a translated file matches one of these: an owner row (3.7.2), a logical limit (3.7.3), a tracepoint, a kept library mapping, or a guard for a size of 0 or less marked `BUG(port):`. Anything else is a finding.
7. Each `try_*` allocation call has an `#[allow(..., reason = "<row>")]` naming an owner or client-sized row (3.7.4).
8. Each owner's formula, parameter, check point, code, message and surrounding behavior match the cited C++ lines. Parameter names are unchanged (all_virtual_sys_parameter_stat.result pins them, C19).
9. A `Charge` is taken before its memory and lives beside it (A2). Counted arenas are exactly the rows marked `counted`, and they report at the T4 check points (T2-T3).
10. Hand counts became `Arc`; pool, queue and budget releases use R2's form.
11. Island callbacks take the receiver first, and memory is freed by the side that allocated it (3.6.4).

## 3.10 Defaults for the developer, and what Step 2a measures

**Defaults, for the developer to confirm:**
1. The limits of 3.7.5, the large-tablet pool included, are dropped (ARCHITECTURE default 29).
2. Memstore-full's cached values are kept in the freezer, not per thread (3.7.2; ARCHITECTURE default 30).
3. A request's hold counts each arena as of its last report (T3; ARCHITECTURE default 31).

This section also relies on ARCHITECTURE defaults 9 and 10.

**Step 2a measures:**
- The scan+filter+aggregate query with per-execution arenas and batch views, against the 1.2x gate.
- How many lifetime parameters the narrow path needs; the aim is `'q` in the IR and planner, one batch lifetime in operators, and none in storage APIs beyond iterator borrows.
- The memstore's bytes per row against the C++'s on the narrow path. ARCHITECTURE §6.1 wants no flush earlier than the C++, and the freeze trigger reads these bytes.
- Plan sizes against the C++ `get_mem_size()` (3.6.3).
- Whether any core crate needs `unsafe` for memory beyond the named crates.
- Family 12's spill scenario: the sort's spill point, C++ against Rust.

## Objections to ARCHITECTURE.md, and how each was settled

Each objection is followed by its settlement, recorded in RESOLUTIONS.md as s3-N.

1. **§11's closed list of thread-locals leaves out a slot the parser needs.** §9.2 has Rust implement `try_check_mem_status` under its C name. Its signature has no receiver (src/sql/parser/parse_node.c:178), and `new_node` calls it with only a count (:183-186). The tracker must therefore be found through a per-call slot. §11 allows only its listed thread-locals plus the warning-buffer slot. Proposed addition: "the parser shim's per-call memory slot, set and cleared around each C parse call inside sql-parser-sys" (T5).

   **Settled:** accepted. ARCHITECTURE §11's closed list now names sql-parser-sys's per-call parse slot, and §9.2 describes it.
2. **§3.1 rule 9's test and §3.2 disagree on one real pool.** "A pool whose size users hit stays" would keep the large-tablet pool: 100 buffers, `allow_over_max_free_num` false, a 3-second wash wait, then -4013 (src/storage/meta_mem/ob_storage_meta_mem_mgr.cpp:177; src/storage/meta_mem/ob_storage_meta_obj_pool.h:224-253). §3.2 keeps typed errors only at Decision 12's owners, which do not include it. Proposed wording: "a pool whose limit Decision 12 names stays (the KV-cache handle pool)". This section follows §3.2 (3.7.5).

   **Settled:** accepted. ARCHITECTURE §3.1 rule 9 now uses that wording.
3. **§3.2 is silent on the eleven other finite limits of 3.7.5** (C6, rechecked). Proposed: add them to §17 as a default, as done here.

   **Settled:** accepted. ARCHITECTURE §3.2 drops them, and §17 lists that as default 29.
4. **§1.2 fix 1's list of types moved down leaves out `ObMemTracker`.** It lives in src/sql/executor (crate `sql` by R01's map), but the parser shim, the resolver, rewrite and sql-exec all check it (the T4 table). This section moves it to ob-base.

   **Settled:** accepted. ARCHITECTURE §1.2 fix 1 and §1.1's ob-base row list it, and the crate map moves src/sql/executor/ob_memory_tracker to ob-base's `allocator` module.
5. **Citations in §3.2.** The micro block cache's retry loop is src/storage/blocksstable/ob_micro_block_cache.cpp:396-400, inside `alloc_data_buf` (:387-408, "UNUSED NOW" at :389); the tmp-file cache calls its own override at src/storage/tmp_file/ob_tmp_file_cache.cpp:698, :739 and :769 (:654 defines it); the hash-join check is src/sql/engine/join/ob_hash_join_op.cpp:837-842. The conclusions stand.

   **Settled:** the micro block cache and tmp-file citations are fixed in ARCHITECTURE §3.2. The hash-join citation was right in ARCHITECTURE: the `if` is at :838 and the -4013 at :843 (`sed -n 838,843p`), so this section's 3.7.3 now cites :838-843.
6. **§6.1 and §3.2 together need a measurement neither names.** "No flush earlier than the C++" and "the same formulas, not the same bytes" both hold only if the Rust memtable uses no more bytes per row, or no judged run reaches the freeze trigger, memstore limit / 100 × trigger percentage (src/storage/tx_storage/ob_memstore_freezer.cpp:1052-1054). This section adds the measurement to Step 2a; the storage section reaches the same point.

   **Settled:** accepted. ARCHITECTURE §6.1 states the condition and §15 adds the measurement to Step 2a.
