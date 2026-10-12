# 08 `storage-core-kvcache`：`storage_core` 与 `share::cache` 的 API

> - **依据**：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）0.8 回收方案、0.9 整张表与表后规则、§2.8、§3 白名单第 10–11 项、§4 存储核心路径行；`migration/infra/README.md` 的文档结构。基线 `7d907abfa`。
> - **事实来源**：只读源码。行号都在基线上核对过；`file:line` 里的路径省略 `src/` 前缀时，以下列缩写为准：`MVR`=`storage/memtable/mvcc/ob_mvcc_row`，`ENG`=`.../mvcc/ob_mvcc_engine.cpp`，`ITR`=`.../mvcc/ob_mvcc_iterator.cpp`，`MVI`=`.../mvcc/ob_multi_version_iterator.cpp`，`CB`=`.../mvcc/ob_mvcc_trans_ctx`，`MVCTX`=`.../mvcc/ob_mvcc_ctx.cpp`，`QE`=`.../mvcc/ob_query_engine`，`KB`=`.../mvcc/ob_keybtree`，`KD`=`.../mvcc/ob_keybtree_deps.h`，`TCL`=`.../mvcc/ob_tx_callback_list`，`MVH`=`.../mvcc/ob_mvcc.h`，`MT`=`storage/memtable/ob_memtable`，`RC`=`storage/memtable/ob_row_compactor.cpp`，`TXF`=`storage/tx/ob_tx_data_functor.cpp`，`FA`=`storage/allocator/ob_fifo_arena`，`MA`=`storage/allocator/ob_memstore_allocator`，`HL`=`storage/allocator/ob_handle_list`，`MF`=`storage/tx_storage/ob_memstore_freezer.cpp`，`SMM`=`storage/meta_mem/ob_storage_meta_mem_mgr.cpp`，`RS`=`oblib/lib/allocator/ob_retire_station.h`，`QS`=`oblib/lib/allocator/ob_qsync.h`，`LGB`=`logservice/palf/log_group_buffer`，`LA`=`logservice/palf/lsn_allocator`，`LSW`=`logservice/palf/log_sliding_window.cpp`，`KS`=`share/cache/ob_kv_storecache`，`ST`=`share/cache/ob_kvcache_store`，`MP`=`share/cache/ob_kvcache_map`，`SR`=`share/cache/ob_kvcache_struct`，`HV`=`share/cache/ob_kvcache_hazard_version`，`HP`=`share/cache/ob_kvcache_hazard_pointer`，`HD`=`share/cache/ob_kvcache_hazard_domain`。`.h`/`.cpp` 写在行号前，如 `MVR.cpp:840`。
> - **计数**：标"约"的计数来自对基线的 grep（去注释前），再人工剔除定义行；方法写在出现处。
> - **一句话设计**：版本链节点、行、键、keybtree 节点都只活在 memtable 的 FIFO arena 里，从不单独释放，所以 MVCC 的安全性只需要三件事——引用不长于 arena（借用或 pin）、链接只能指向同一个 arena（身份检查）、发布用 Release/Acquire；回收方案（QClock/RetireStation、hazard version、hazard pointer）照 C++ 移植，只修一处释放后使用；策略（`mvcc_write_` 的五种情况、`lock_for_read`、cleanout、ELR、快速提交……）全部留在 storage 用安全 API 翻译。

## 1. 范围

### 1.1 单元与处置

**本模块负责的单元**（处置写进 `units.tsv`；"拆分"表示一个头文件组的成员按函数级重映射分到两个落点，见 1.2）：

| # | 单元（代表文件） | 行数（h+cpp） | 处置 | Rust 落点 |
|---|---|---|---|---|
| 1 | `storage/memtable/mvcc/ob_mvcc_row.h` | 458+1226 | 拆分：`rewrite:storage_core`（布局、标志、原子访问、链接原语）＋ `translate`（规则函数） | `rust/port/storage_core/src/ob_mvcc_row.rs`；规则：`rust/port/storage/src/memtable/mvcc/ob_mvcc_row.rs` |
| 2 | `storage/memtable/mvcc/ob_row_latch.h` | 49 | `rewrite:storage_core`；原语本身是 `oblib::sync::bitlock::ObRowLatch`（RULEBOOK §2.7，模块 02 实现，02-sync.md O06），本文件只 `pub use` | `.../storage_core/src/ob_row_latch.rs` |
| 3 | `storage/memtable/ob_memtable_data.h` | 110 | `rewrite:storage_core` | `.../storage_core/src/ob_memtable_data.rs` |
| 4 | `storage/memtable/mvcc/ob_keybtree.h`（`KB.h:328` 把 `.cpp` include 进头文件） | 330+1781 | `rewrite:storage_core` | `.../storage_core/src/ob_keybtree.rs` |
| 5 | `storage/memtable/mvcc/ob_keybtree_deps.h` | 646 | `rewrite:storage_core` | `.../storage_core/src/ob_keybtree_deps.rs` |
| 6 | `storage/memtable/mvcc/ob_tx_callback_list.h` | 282+1055 | 拆分：`rewrite:storage_core`（侵入式链接、三把锁、日志游标、遍历/摘链原语）＋ `translate`（快速提交、checksum、填日志等全部策略） | core：`.../storage_core/src/ob_tx_callback_list.rs`；storage：`.../storage/src/memtable/mvcc/ob_tx_callback_list.rs` |
| 7 | `storage/memtable/mvcc/ob_mvcc.h` | 183 | `translate`；其中链接成员 `MVH:96-101,176-177` 与定义 `CB.cpp:132-161` 重映射进 #6 的 core 落点 | `.../storage/src/memtable/mvcc/ob_mvcc.rs` |
| 8 | `storage/allocator/ob_memstore_allocator.h`；另有 `MF:1659-1703` 的 `ObMemstoreAllocator::alloc` 定义 | 204+118 | `rewrite:storage_core`；`alloc` 从 `MF` 重映射进来，对 freezer/限流的直接调用改成钩子（2.3）；`MF:1621-1656` 两个 InfoLogger、`MF:1706-1737` 和 `MA.cpp:101-115` 的限流静态函数留在 storage | `.../storage_core/src/ob_memstore_allocator.rs` |
| 9 | `storage/allocator/ob_fifo_arena.h` | 234+221 | `rewrite:storage_core` | `.../storage_core/src/ob_fifo_arena.rs` |
| 10 | `storage/allocator/ob_handle_list.h` | 161+72 | `rewrite:storage_core` | `.../storage_core/src/ob_handle_list.rs` |
| 11 | `logservice/palf/log_group_buffer.h` | 127+500 | `rewrite:storage_core` | `.../storage_core/src/log_group_buffer.rs` |
| 12 | `logservice/palf/lsn_allocator.h` | 117+522 | `rewrite:storage_core` | `.../storage_core/src/lsn_allocator.rs` |
| 13 | （无 C++ 对应，RULEBOOK 0.9 的保活协议） | — | 新文件 | `.../storage_core/src/memtable_ref.rs` |
| 14 | `oblib/lib/allocator/ob_retire_station.h` | 279 | `replace:sync`：API 由本文 2.5 冻结、本模块实现（Q3） | `rust/port/oblib/src/sync/ob_retire_station.rs`（§3 白名单第 2 项路径） |
| 15 | `oblib/lib/atomic/atomic128.h` | 120 | 不归本模块：`replace:atomic`，模块 02 提供 `oblib::atomic::{AtomicU128, uint128_t, cas128, load128}`（02-sync.md A02）。列在这里是因为最大的使用者 `LA.cpp`（21 处 `CAS128/LOAD128`）在本模块，照 02 的同名函数翻译（3.7 S46）；`REACH_COUNT_PER_SEC`、`EXECUTE_COUNT_PER_SEC` 改 `Latch`（§2.7、04 号的修订） | — |
| 16 | `share/cache/ob_kvcache_hazard_version.h` | 112+362 | `rewrite:share_cache` | `rust/port/share/src/cache/ob_kvcache_hazard_version.rs` |
| 17 | `share/cache/ob_kvcache_hazard_pointer.h` | 524+164 | `rewrite:share_cache` | `.../share/src/cache/ob_kvcache_hazard_pointer.rs` |
| 18 | `share/cache/ob_kvcache_hazard_domain.h` | 427+273 | `rewrite:share_cache` | `.../share/src/cache/ob_kvcache_hazard_domain.rs` |
| 19 | `share/cache/ob_kvcache_struct.h` | 253+320 | `rewrite:share_cache` | `.../share/src/cache/ob_kvcache_struct.rs` |
| 20 | `share/cache/ob_kvcache_store.h` | 261+1315 | `rewrite:share_cache` | `.../share/src/cache/ob_kvcache_store.rs` |
| 21 | `share/cache/ob_kvcache_map.h` | 143+804 | `rewrite:share_cache` | `.../share/src/cache/ob_kvcache_map.rs` |
| 22 | `share/cache/ob_kv_storecache.h` | 649+715 | `rewrite:share_cache` | `.../share/src/cache/ob_kv_storecache.rs` |
| 23 | `share/cache/ob_kvcache_inst_map.h` | 149+431 | `rewrite:share_cache`（不需要 unsafe） | `.../share/src/cache/ob_kvcache_inst_map.rs` |
| 24 | `share/cache/ob_cache_utils.h` | 617 | `rewrite:share_cache`（只被 #20、#23 使用，`grep -rl ob_cache_utils.h src` 在 `share/cache` 之外为空） | `.../share/src/cache/ob_cache_utils.rs` |

- `share/cache` 里另外 4 个单元照常 `translate`：`ob_cache_name_define.h`、`ob_cache_type.h`、`ob_recycle_multi_kvcache.{h,ipp}`、`ob_vtable_event_recycle_buffer.h`（只被 `storage/multi_data_source/ob_mds_event_buffer.h:314` 使用）。
- §3 白名单第 11 项的确切路径：#16–#22（`ob_kvcache_inst_map.rs`、`ob_cache_utils.rs` 不在白名单内，照 `#![deny(unsafe_code)]`）。第 10 项是整个 `rust/port/storage_core/**`。
- RULEBOOK §4 说存储核心"约 10 个单元"；按上表实际是 12 个 C++ 单元（#1–#12，把"memstore 分配器"展开成 #8–#10）加 1 个新文件。

**值类型**（Q1，推荐下移到 oblib、在 1d 写出；不选推荐项时改用 2.1 末尾的原始表示）：

| C++ | 文件 | 被本模块用到的地方 |
|---|---|---|
| `share::SCN`（含 `atomic_load/atomic_store/atomic_get/atomic_set/atomic_bcas/atomic_vcas/inc_update`） | `share/scn.h`（154 行）、`share/scn.cpp`（486 行；`:18` include 的 `ob_table_range.h` 在文件里没有用到） | 节点 `trans_version_/scn_/tx_end_scn_`（`MVR.h:86-90`）、行 `max_*_version_/m*_modify_scn_`（`MVR.h:287-290`）、LSN 分配器、回调链表 |
| `transaction::ObTransID` | `share/transaction/ob_tx_id.h`（77 行）+`.cpp` | 节点 `tx_id_`（`MVR.h:85`）、行 `max_trans_id_`（`:291-292`） |
| `transaction::ObTxSEQ` | `data_plane/api/data_plane/transaction/ob_tx_seq.h`（190 行） | 节点 `seq_no_`（`MVR.h:88`） |
| `blocksstable::ObDmlFlag` 与 `get_dml_str` | `data_plane/api/data_plane/blocksstable/ob_datum_row.h:38-55` | 载荷头 `dml_flag_`（`ob_memtable_data.h:69`）、行 `first/last_dml_flag_`（`MVR.h:280-281`） |
| `palf::LSN`、`lsn_2_block/lsn_2_offset` | `share/log/palf/lsn.h`（75 行）+`.cpp`（142 行） | 组缓冲、LSN 分配器 |
| `PALF_BLOCK_SIZE`、`CLOG_FILE_TAIL_PADDING_TRIGGER`、`DEFAULT_GROUP_BUFFER_SIZE`、`PADDING_LOG_CONTENT_CHAR`、`LOG_INVALID_LSN_VAL`/`LOG_MAX_LSN_VAL`、`OB_INVALID_LOG_ID` | `share/log/palf/log_define.h:86-129` | 同上 |
| `REPLAY_TASK_QUEUE_SIZE`（= 32） | `share/ob_define.h:311` | `ObMvccRowIndex`（`MVR.h:258`） |

### 1.2 混合单元的拆分

storage_core 只依赖 oblib（RULEBOOK 0.3），而且 1d 时 storage/share/data_plane 都还没有翻译，所以 C++ 里"机制和策略写在同一个类"的单元必须按函数拆开（Q2、修订 A2）。拆分后两边的 Rust 文件都写 `//! Ported from: <同一组源文件>`，各自列出搬来的函数。

**`ob_mvcc_row`（#1）**

- 进 core：`ObMvccTransNode` 的全部字段与 `MVR.cpp:36-43` 的标志常量、`:79-152` 的标志/版本/屏障函数、`:154-202` 的 `is_lock_node`/`to_string`、`:69-77` 的 `get_dml_flag`/`get_data_size`；`ObMvccRow` 的字段、`:354-370` `reset`、`:372-429` `to_string`、`:722-741` `get_max_trans_version`/`update_max_*`、`:778-791` `update_dml_flag_`、`MVR.h:380-436` 的标志与 getter；`ObMvccRowIndex`（`MVR.h:244-259`、`MVR.cpp:324-352`）。
- 进 storage（`translate`，挂在 2.10 的扩展 trait 上）：`MVR.cpp:45-66` 节点 checksum；`:204-322` `ObMvccRowFilter`；`:431-502` `unlink_trans_node`；`:504-524` `need_compact`；`:526-545` `row_compact`；`:547-686` `insert_trans_node`；`:689-720` `elr`；`:743-776` `trans_commit`；`:793-832` `remove_callback`/`wakeup_waiter`；`:834-1103` `mvcc_write_`/`mvcc_sanity_check_`/`mvcc_undo`/`mvcc_write`；`:1120-1207` `check_row_locked`；`:1209-1224` `print_row`。
- 理由：这些函数要用 `ObStoreCtx`、事务表 cleanout（`MVR.cpp:881-883`）、`ObLockWaitMgr`（`:714-716,800-810`）、`ObRowReader`（`:309`）、`ObMemtableRowCompactor`（`:536`）、`get_replay_queue_index()`（`:560`，在 `share/ob_define.h:313`），都是 storage/share 的东西。它们改链时只用 core 的 `RowGuard` 原语（2.4），所以仍然满足 RULEBOOK §2.8"改链只能经由 `RowGuard`"。

**`ob_tx_callback_list`（#6）与 `ob_mvcc`（#7）**

- 进 core：哨兵 `head_`、链接 `prev_/next_`（`MVH:176-177`，访问函数 `:96-101`）、`append`/`remove`（`CB.cpp:132-161`）、三把字节锁与 `LockGuard`/`LockState`/`LOCK_MODE`（`TCL.h:139-165`、`TCL.cpp:980-1052`）、`log_cursor_`/`log_epoch_`（`TCL.h:231-232`、`TCL.cpp:933-943`）、`parallel_start_pos_`（`TCL.h:233`）。
- 留在 storage：`ObTxCallbackList` 结构体本身（嵌入 core 的 `TxCallbackListCore<TxCallback>`）、它的全部成员函数（`TCL.cpp:26-931`，含 `callback_` 遍历内核）、计数和 checksum 字段、`ObITransCallback` 的数据字段与虚函数（改成 `ObITransCallback` 数据结构 + `ObITransCallbackVirt` trait，§2.13）、3 种回调和 `TxCallback` enum（§2.8）。

**`ob_memstore_allocator`（#8）**：`ObMemstoreAllocator::alloc`（`MF:1659-1703`）进 core，它对 `ObMemstoreFreezer::check_memstore_full_internal` 和 `TxShareThrottleTool::alloc_resource` 的调用改成 `ObMemstoreAllocHook`（2.3），组合根绑定 storage 的实现（RULEBOOK 0.4 的第 3 种拆法）。

### 1.3 本模块依赖、但不负责的单元

- `oblib/lib/allocator/ob_qsync.h` → `oblib::sync::Quiescence`（模块 02；RULEBOOK 0.8 "ObQSync 当静默屏障"）。本模块要求它保持 `ObQSync = ObQSyncWrapper<256>` 的语义：`get_itid() % 256` 选槽，`sync()` 不 `PAUSE` 地自旋到每个槽读到 0（`QS.h:45-74,121-123`）。
- `oblib/lib/lock/ob_small_spin_lock.h` 的 `ObByteLock`（= `ObSmallSpinLock<uint8_t, 0, 500, 100>`，`:74-77,213`）→ 模块 02 的 `bitlock` 退避变体。
- `oblib/lib/lock/ob_tc_rwlock.h`（LSN 分配器的 `common::RWLock`）→ 模块 02 的 `BigReaderLatch<T>`。
- `oblib/lib/thread_local/ob_tsi_utils.h:46-55` 的 `get_itid()`、`:70-77` 的 `icpu_id()`（没有定义 `HAVE_SCHED_GETCPU`，所以是 `get_itid() & (OB_MAX_CPU_NUM - 1)`）→ 模块 03。
- `oblib/lib/queue/ob_link.h` 的 `ObLink`/`ObDLink` 与 `ol_*`/`dl_*` 无锁原语：模块 05 不对外提供（05-containers.md 的 `ob_link.h` 行："库内使用者……在各自模块内实现"）。本模块自己实现用到的部分：`ob_retire_station.rs` 里的 `#[repr(C)] pub struct ObLink { next_: *mut ObLink }`（`ob_link.h:29-38`；`HazardList`、keybtree 节点头、KV cache 块句柄的 `retire_link_` 共用，2.5）；KV store 的块句柄双向链表 `mb_list_`（`ObKVMemBlockHandle : ObDLink`，`SR.h:115`；`dl_insert_before`/`dl_del`，`ob_link.h:238-327`；调用点 `ST.cpp:1168-1205`）在 `ob_kvcache_store.rs` 内逐行移植。memstore 的句柄表不需要它（2.3：表项在锁下用普通容器保存）。
- `oblib/lib/resource/ob_resource_mgr.h` 的 `ObMemoryMgr::alloc_cache_mb/free_cache_mb`（KV 块内存）：模块 01 删除（01-mem.md 的 `ob_resource_mgr` 行）；替代见 3.8 S54。
- `SR.h:22-28` include 的其余头文件：`ob_atomic_reference.h`、`ob_dlist.h` 在 `share/cache` 里没有使用（`grep -rn 'ObAtomicReference\|ObDList' src/share/cache` 为空），本模块不需要 02 的 `ObAtomicReference`；`ObList` 只用于 `multi_get` 的结果表（`ob_kv_storecache.h:325`、`MP.h:109`），照 05 的 `ObList<T>` 翻译；`ObLfFIFOAllocator` 见 S55。

### 1.4 消费方（`translate`，按本文 API 写）

- storage：`ob_mvcc_engine`、`ob_mvcc_iterator`、`ob_multi_version_iterator`、`ob_mvcc_trans_ctx`、`ob_mvcc_ctx`、`ob_mvcc_define`、`ob_query_engine`、`ob_row_compactor`、`ob_memtable`、`ob_memtable_iterator`、`ob_memtable_read_row_util`、`ob_row_conflict_handler`、`tx/ob_tx_data_functor`、`tx_table/ob_tx_table{,_guards,_interface}`、`meta_mem/ob_storage_meta_mem_mgr`、`ddl/ob_tablet_ddl_kv`（keybtree 的第二个实例化，`ob_tablet_ddl_kv.h:83-85`）、`tx_storage/ob_memstore_freezer`、`throttle/*`、`tablelock/ob_table_lock_callback`、`lob/ob_ext_info_callback`。
- 规模（grep 统计，含声明行、已剔除定义行）：storage 里对节点成员的访问约 230 处，其中 `prev_` 33、`trans_version_` 28、`is_committed()` 23、`is_aborted()` 21；对行成员约 110 处（`latch_` 15 处加锁）；`buf_` 重解释成 `ObMemtableDataHeader` 12 处；`ObMvccRow*`/`ObMvccTransNode*` 作为结构体字段保存的 17 处（`ob_i_store.h:285`、`ob_mvcc_define.h:190-300`、`CB.h:540-541`、`ITR.h:159-160`、`MVI.h:99-101`、`QE.h:129`、`RC.h:75`、`TXF.h:63,97-98`）。
- KV cache：18 个缓存实例（`ob_storage_cache_suite.cpp:54-61`、`observer/ob_server.cpp:823-2487`、`ob_opt_stat_service.cpp:255-261`、`ob_schema_cache.cpp:371-373`）；`get` 约 21 处、`put` 11、`put_and_fetch` 9、`erase` 7、`alloc` 4、`put_kvpair` 4；直接碰内存块句柄的只有 `ObPointerSwizzleNode`（`storage/blocksstable/ob_kvcache_pointer_swizzle.{h,cpp}`）。
- logservice：`LogSlidingWindow` 是组缓冲和 LSN 分配器唯一的持有者（`log_sliding_window.h:247-248`），调用点全部在 `LSW`（2.8 逐个列出）；`LogWriteBuf`（`log_writer_utils.h:29-62`）持有组缓冲的片段。

## 2. Rust API

### 2.0 crate 与 lint

```rust
// rust/port/storage_core/Cargo.toml: [dependencies] oblib, arc-swap; [dev-dependencies] loom（AtomicU128 经 oblib::atomic 转出，不直接依赖 portable-atomic）
// rust/port/storage_core/src/lib.rs（1d 手写，不由脚本生成）
#![deny(unsafe_op_in_unsafe_fn)]
#![warn(clippy::undocumented_unsafe_blocks)]
pub mod ob_memtable_data; pub mod ob_row_latch; pub mod ob_mvcc_row; pub mod memtable_ref;
pub mod ob_handle_list; pub mod ob_fifo_arena; pub mod ob_memstore_allocator;
pub mod ob_keybtree_deps; pub mod ob_keybtree; pub mod ob_tx_callback_list;
pub mod log_group_buffer; pub mod lsn_allocator;
pub use {ob_memtable_data::*, ob_row_latch::*, ob_mvcc_row::*, memtable_ref::*, ob_memstore_allocator::*,
         ob_keybtree::*, ob_keybtree_deps::{BtreeKey, BtreeVal, ObKeyBtreeDomain, ObMvccRowVal}, ob_tx_callback_list::*,
         log_group_buffer::*, lsn_allocator::*};
```

- 整个 crate 在 §3 白名单内；仍然要求每处 `unsafe` 写 `// SAFETY:`。
- 对外（storage、logservice）的全部函数都是安全函数。带 `unsafe fn` 的只有 2.5 的 `RetireStation` 内部接口，它只被 storage_core 和 `share::cache` 里的白名单模块调用。

### 2.1 值类型（Q1）

推荐项下，下列类型由本模块在 1d 逐行移植进 oblib（名字、方法名、序列化、`TO_STRING` 不变），所有 crate 从 oblib 引用；本文其余签名按推荐项书写：

```rust
// oblib（落点由 1c 的 units.tsv 给出）
pub struct SCN { /* val_: u64，位域 ts_ns_:62, v_:2，scn.h:30-150 */ }
/// SCN 字段上的原子操作（C++ 是 SCN 自身的 atomic_* 方法，scn.h:52-57、scn.cpp:33-70,168-200）。
#[repr(transparent)] pub struct AtomicSCN(core::sync::atomic::AtomicU64);
impl AtomicSCN {
    pub const fn new(v: SCN) -> Self;
    pub fn atomic_load(&self) -> SCN;  pub fn atomic_get(&self) -> SCN;              // SeqCst
    pub fn atomic_store(&self, v: SCN); pub fn atomic_set(&self, v: SCN);            // SeqCst
    pub fn atomic_bcas(&self, old: SCN, new: SCN) -> bool; pub fn atomic_vcas(&self, old: SCN, new: SCN) -> SCN;
    pub fn inc_update(&self, v: SCN) -> SCN;                                         // 语义同 scn.cpp:168
    pub fn get_mut(&mut self) -> &mut SCN;
}
pub struct ObTransID(i64); pub struct ObTxSEQ { raw_val_: i64 }   // cast_to_int / cast_from_int（ob_tx_seq.h:94-100）
#[repr(transparent)] pub struct ObDmlFlag(pub i32);               // DF_NOT_EXIST..DF_MAX（ob_datum_row.h:38-46），§2.12 枚举规则
pub struct LSN { pub val_: u64 }                                  // lsn.h:26-60
pub const REPLAY_TASK_QUEUE_SIZE: i64 = 32;
```

不选推荐项时的原始表示（2.4、2.8 的签名逐个替换）：`SCN`→`u64`（`val_`），`ObTransID`→`i64`，`ObTxSEQ`→`i64`（`raw_val_`），`ObDmlFlag`→`i32`，`LSN`→`u64`；比较语义必须照 C++（`SCN::operator<` 认为无效值最小，`scn.cpp:330-342`；`ObTxSEQ::operator>` 只比 `get_seq()`，`ob_tx_seq.h:62-69`），由调用方转换。

### 2.2 memtable 容器与 pin（`memtable_ref.rs`）

C++ 里回调持有 `ObMemtable *memtable_`、`ObMvccRow &value_`、`ObMvccTransNode *tnode_`（`CB.h:538-544`），靠"销毁 memtable 之前先摘掉指向它的回调"（`MT.cpp:167-181` → `CB.cpp:660-677`）保活。Rust 版把这个协议收进容器：

```rust
/// memtable 对象的唯一所有者。C++：t3m 对象池里的 `ObMemtable`（SMM:617 `free_obj`）。
/// `T` 是 storage 的 `ObMemtable`；本 crate 不认识它的内容。
pub struct MemtableRef<T: Send + Sync + 'static> { inner: NonNull<MemtableInner<T>> }
impl<T: Send + Sync + 'static> MemtableRef<T> {
    /// f 拿到本容器的自身句柄，必须把它存进 T（T 的 `&self` 方法用它登记回调）。
    pub fn new_cyclic(f: impl FnOnce(MemtableSelf<T>) -> ObResult<T>) -> ObResult<MemtableRef<T>>;
    pub fn get(&self) -> &T;
    /// 尚未发布时的两段式 init（RULEBOOK 0.5）；pin 计数不为 0 时返回 None。
    pub fn get_mut(&mut self) -> Option<&mut T>;
    /// 进程内唯一、永不复用；替代 C++ 用对象地址作键的 `ObMemtableSet`（hash::ObHashSet<uint64_t>，TCL.cpp:444）。
    pub fn memtable_id(&self) -> u64;
    /// RULEBOOK 0.9：回调只能经此（或 `MemtableSelf::register_callback`）创建，同时拿到 pin。
    pub fn register_callback(&self) -> ObResult<MemtablePin<T>>;
    pub fn get_pin_count(&self) -> i64;
    /// 核心的释放入口：pin 计数为 0 时析构 T 并释放；否则记 ERROR，把自己原样还给调用方（延后）。
    pub fn release(self) -> Result<(), MemtableRef<T>>;
}
impl<T: Send + Sync + 'static> core::ops::Deref for MemtableRef<T> { type Target = T; }
/// Drop = release；被拒绝时把容器停进核心的延后表（记 ERROR），由 `retry_deferred_memtable_release` 重试。
impl<T: Send + Sync + 'static> Drop for MemtableRef<T> {}
/// 每轮 t3m GC（`ObStorageMetaMemMgr::gc_tables_in_queue`，SMM:416）末尾调用一次；返回延后表里剩余的个数。
pub fn retry_deferred_memtable_release() -> i64;

/// 回调保活（RULEBOOK §4 改名表）。不实现 Clone；Drop 时把创建它的分片减 1。
pub struct MemtablePin<T: Send + Sync + 'static> { inner: NonNull<MemtableInner<T>>, shard_: u8 }
impl<T: Send + Sync + 'static> core::ops::Deref for MemtablePin<T> { type Target = T; }
impl<T: Send + Sync + 'static> MemtablePin<T> { pub fn memtable_id(&self) -> u64; }

/// 存在 T 内部的自身句柄。C++：把 `this` 传给 `alloc_row_callback(ctx, value, memtable)`（MVCTX:309-320）。不实现 Clone。
pub struct MemtableSelf<T: Send + Sync + 'static> { inner: NonNull<MemtableInner<T>> }
impl<T: Send + Sync + 'static> MemtableSelf<T> {
    pub fn register_callback(&self) -> ObResult<MemtablePin<T>>;
    pub fn memtable_id(&self) -> u64;
}
pub const MEMTABLE_PIN_SHARD_NUM: usize = 16;
```

- `MemtableInner<T>` = `{ memtable_id_, state_: AtomicU8 (NORMAL/RELEASING/DESTROYED), pins_: [CacheAligned<AtomicI64>; 16], value_: T }`，堆上分配，地址不变。
- storage 的表句柄（RULEBOOK 0.9 "`Arc` 加 arc-swap，归零后推进 GC 队列放进 `Drop`"）间接持有 `MemtableRef<ObMemtable>`：`Arc` 的载荷是一层包装，它的 `Drop` 把 `MemtableRef` 原样移交 t3m 的 GC 队列（C++ `ObTableHandleV2::reset` → `push_table_into_gc_queue`，`storage/ob_i_table.cpp:178-191`），由 GC 照 C++ 流程（`safe_to_destroy` → 摘回调 → keybtree 批量销毁 → `release`，`SMM:416-617`）处理；**不得**让 `Arc` 归零时直接析构 `MemtableRef`（那会跳过批量销毁、在查询线程里做 purge）。`MemtableRef` 自己的 `Drop` 只是兜底。
- 读路径的 `'m` 来自对它的借用（`&ObMemtable`），回调路径的 `'m` 来自 `&MemtablePin`。

### 2.3 memtable arena（`ob_fifo_arena.rs`、`ob_handle_list.rs`、`ob_memstore_allocator.rs`）

```rust
/// C++ `ObFifoArena`（FA.h:38-229）。进程内一个，嵌在 `ObMemstoreAllocator` 里（MA.h:198）。
pub struct ObFifoArena { /* nway_, allocated_, reclaimed_, hold_, retired_: AtomicI64; cur_pages_: [AtomicPtr<Page>; 512] */ }
impl ObFifoArena {
    pub const MAX_CACHED_GROUP_COUNT: i64 = 16;            // FA.h:158
    pub const MAX_NWAY: i64 = 32;                          // Handle::MAX_NWAY，FA.h:112
    pub const MAX_CACHED_PAGE_COUNT: i64 = 16 * 32;        // FA.h:159
    pub const ALLOC_PAGE_SIZE: i64;                        // OB_MALLOC_BIG_BLOCK_SIZE + size_of::<Page>() + size_of::<Ref>()，FA.h:160
    pub fn new() -> Self;
    pub fn init(&mut self) -> ObResult;                    // FA.cpp:31-48
    pub fn reset(&self);                                   // FA.cpp:50-54
    pub fn update_nway_per_group(&self, nway: i64);        // FA.cpp:56-70
    pub fn allocated(&self) -> i64; pub fn retired(&self) -> i64; pub fn reclaimed(&self) -> i64; pub fn hold(&self) -> i64;
    pub fn get_max_cached_memstore_size(&self) -> i64;     // FA.h:190-193
    // alloc(idx, Handle&, size) / free(Handle&)（FA.cpp:92-175）只在 crate 内可见
}

/// C++ `ObMemstoreAllocator`（MA.h:62-199）。进程内一个，是 storage `ObSharedMemAllocMgr::memstore_allocator_`（smam.h:92）。
pub struct ObMemstoreAllocator { /* lock_: Latch<HandleListState>（ObSpinLock，MEMSTORE_ALLOCATOR_LOCK），arena_: ObFifoArena */ }
impl ObMemstoreAllocator {
    pub fn new() -> Self;
    pub fn init(&mut self) -> ObResult;                    // MA.cpp:42-50：arena_.init()，set_nway_per_group(1)
    pub fn start(&self) -> ObResult; pub fn stop(&self); pub fn wait(&self); pub fn destroy(&self);   // 空操作，MA.h:134-137
    pub fn set_frozen(&self, handle: &AllocHandle);        // MA.cpp:81-85
    pub fn for_each(&self, f: &mut dyn FnMut(&AllocHandle) -> ObResult, reverse: bool) -> ObResult;   // MA.h:142-151，持 lock_
    pub fn get_active_memstore_used(&self) -> i64; pub fn get_freezable_active_memstore_used(&self) -> i64;
    pub fn get_max_cached_memstore_size(&self) -> i64; pub fn hold(&self) -> i64; pub fn get_memstore_quota_used(&self) -> i64;
    pub fn get_frozen_memstore_pos(&self) -> i64; pub fn get_memstore_reclaimed_pos(&self) -> i64;
    pub fn get_memstore_allocated_pos(&self) -> i64; pub fn get_retire_clock(&self) -> i64;   // MA.h:153-172
    pub fn set_nway_per_group(&self, nway: i64);           // MA.cpp:87-91
    pub fn set_memstore_threshold(&self) -> ObResult;      // MA.cpp:93-98：持 lock_ 调钩子
}

/// storage 实现、组合根绑定（RULEBOOK 0.4 trait + 类型化槽位）。槽位为空时 alloc 返回分配失败（记 WARN）。
pub trait ObMemstoreAllocHook: Send + Sync {
    /// C++ `freezer->check_memstore_full_internal(is_out_of_mem)`（MF:1679-1684）。
    fn check_memstore_full_internal(&self, is_out_of_mem: &mut bool) -> ObResult;
    /// C++ `throttle_tool_->alloc_resource<ObMemstoreAllocator>(align_size, expire_ts, is_throttled)`，
    /// 以及 is_throttled 时 `memstore_throttled_alloc() += align_size`（MF:1694-1698）。
    fn alloc_resource(&self, align_size: i64, expire_ts: i64);
    /// C++ `set_memstore_threshold_without_lock`（MF:1706-1718）。
    fn set_memstore_threshold_without_lock(&self) -> ObResult;
}
pub static MEMSTORE_ALLOC_HOOK_SLOT: oblib::slot::Slot<dyn ObMemstoreAllocHook>;     // 06 的 Slot<T: ?Sized> 内部存 &'static T
/// C++ `server_service<ObSharedMemAllocMgr>()->memstore_allocator()`（MA.cpp:37）。
pub static MEMSTORE_ALLOCATOR_SLOT: oblib::slot::Slot<ObMemstoreAllocator>;

/// C++ `ObMemstoreAllocator::AllocHandle`（MA.h:75-126），即 `ObSingleMemstoreAllocator`（MT.h:180）。
/// 一个 memtable 一个；Clone 只增加引用计数（keybtree 持有一份，保证节点内存活得比树长）。
/// 内存在最后一份 clone 析构时整体释放（C++ `destroy_handle`，MA.cpp:62-78）。
#[derive(Clone)] pub struct AllocHandle { inner: Arc<AllocHandleInner> }
impl AllocHandle {
    pub fn new() -> AllocHandle;
    pub fn init(&self) -> ObResult;                        // MA.cpp:33-40：经 MEMSTORE_ALLOCATOR_SLOT 登记进句柄表
    pub fn destroy(&self);                                 // MA.h:92-97：之后 alloc 一律失败；内存待最后一份 clone 析构
    pub fn arena_id(&self) -> u64;                         // 身份：进程内唯一、永不复用
    pub fn set_frozen(&self);                              // MA.h:120-124
    pub fn is_frozen(&self) -> bool; pub fn is_active(&self) -> bool; pub fn get_id(&self) -> i64;   // HL.h:118-131
    pub fn get_protection_clock(&self) -> i64; pub fn get_retire_clock(&self) -> i64;               // MA.h:98-106
    pub fn get_size(&self) -> i64; pub fn get_occupied_size(&self) -> i64;                          // MA.h:107-108
    /// C++ 经 `mt_` 回指读的两个属性（MF:1666,1699），改成由 storage 写入的镜像。
    pub fn set_inner_tablet(&self, is_inner: bool); pub fn set_offlined(&self, offlined: bool);
    /// 诊断用回指（C++ `mt_`，InfoLogger 和 `ob_all_virtual_memstore_allocator_info.cpp:37-48` 读它）。
    pub fn set_owner(&self, owner: Weak<dyn core::any::Any + Send + Sync>);
    pub fn owner(&self) -> Option<Arc<dyn core::any::Any + Send + Sync>>;
    // —— 带类型的分配（替代 `local_allocator_.alloc(size)` 的全部调用点，见 2.4、2.6）——
    pub fn dup_data<'a>(&'a self, data: &ObMemtableData<'_>) -> ObResult<NodeMut<'a>>;        // ObMTKVBuilder::dup_data，MT.h:121-134
    pub fn alloc_node_batch<'a>(&'a self, alloc_data_size: i64) -> ObResult<NodeBatch<'a>>;  // MT.cpp:2509-2516
    pub fn row<'a>(&'a self, a: MvccAddr<ObMvccRow>) -> RowRef<'a>;                          // 身份不符时 ob_assert
    pub fn node<'a>(&'a self, a: MvccAddr<ObMvccTransNode>) -> NodeRef<'a>;
    pub fn try_row<'a>(&'a self, a: MvccAddr<ObMvccRow>) -> ObResult<RowRef<'a>>;           // 身份不符返回 OB_ERR_UNEXPECTED
    pub fn try_node<'a>(&'a self, a: MvccAddr<ObMvccTransNode>) -> ObResult<NodeRef<'a>>;
    /// 在 arena 里构造任意不需要 Drop 的值（keybtree 的键、DDL 树的值）。闭包对任意 'b 成立，所以结果只能借用
    /// 这次分配出的内存或 'static 数据（2.6 的安全论证依赖这一点）。
    pub fn with_arena<R>(&self, f: impl for<'b> FnOnce(&ArenaCap<'b>) -> R) -> R;
}
/// 带"品牌"生命周期的 arena 分配能力。不变型（invariant），只能由 `with_arena`/keybtree 内部创建。
pub struct ArenaCap<'b> { /* &'b AllocSource, PhantomData<fn(&'b ()) -> &'b ()> */ }
impl<'b> ArenaCap<'b> {
    pub fn alloc<T: Copy>(&self, v: T) -> ObResult<&'b T>;                       // align_of::<T>() <= 8，编译期断言
    pub fn alloc_slice_copy<T: Copy>(&self, src: &[T]) -> ObResult<&'b [T]>;
    pub fn alloc_slice_fill_with<T: Copy>(&self, n: usize, f: impl FnMut(usize) -> T) -> ObResult<&'b [T]>;
}
```

- `ObHandleList`（HL.h:26-157）只在 crate 内可见：活跃表按 clock 非增排序（`HL.h:84-95`），`hazard_` = 活跃表尾的 clock 或 `i64::MAX`（`HL.cpp:55-69`），全部修改在 `ObMemstoreAllocator::lock_` 下进行（`MA.cpp:52-85`）；表项是 `Weak<AllocHandleInner>`，在句柄最后一份 clone 析构时摘除。
- C++ 把 `ObMemstoreAllocator` 当作限流工具的类型参数（`alloc_resource<ObMemstoreAllocator>`；`DEFINE_CUSTOM_FUNC_FOR_THROTTLE(Memstore)` 生成 `resource_unit_size`/`init_throttle_config`/`adaptive_update_limit`，`MA.h:65`、`storage/throttle/ob_share_throttle_define.h:27-40`）。Rust 版由 storage 的限流模块为核心类型 `ObMemstoreAllocator` 实现它自己的 trait（trait 在 storage，孤儿规则允许），函数体照 `MA.cpp:101-115`、`MF:1721-1737` 翻译。
- `memstore_throttled_alloc()`（`MA.h:35-39` 的线程局部）归 storage，钩子的 `alloc_resource` 实现里累加。

### 2.4 MVCC（`ob_memtable_data.rs`、`ob_row_latch.rs`、`ob_mvcc_row.rs`）

```rust
// —— ob_memtable_data.rs ——
/// C++ `ObMemtableData`（ob_memtable_data.h:75-105）：传参用。
#[derive(Clone, Copy)]
pub struct ObMemtableData<'a> { pub dml_flag_: ObDmlFlag, pub buf_len_: i64, pub buf_: Option<&'a [u8]> }
impl<'a> ObMemtableData<'a> {
    pub fn new(dml_flag: ObDmlFlag, buf_len: i64, buf: Option<&'a [u8]>) -> Self; pub fn reset(&mut self);
    pub fn set(&mut self, dml_flag: ObDmlFlag, data_len: i64, buf: Option<&'a [u8]>); pub fn dup_size(&self) -> i64;   // 16 + buf_len_
}
/// C++ `reinterpret_cast<const ObMemtableDataHeader *>(node->buf_)`（12 处）。只读视图，由 `NodeRef::get_mtd()` 产出。
#[derive(Clone, Copy)]
pub struct ObMemtableDataHeader<'m> { pub dml_flag_: ObDmlFlag, pub buf_len_: i64, pub buf_: &'m [u8] }
impl<'m> ObMemtableDataHeader<'m> {
    pub const SIZE: i64 = 16;                              // sizeof(ObMemtableDataHeader)
    pub fn dup_size(&self) -> i64;
    pub fn checksum(&self, bc: &mut ObBatchChecksum) -> ObResult;   // ob_memtable_data.h:39-50（ObBatchChecksum 归 oblib；若不在 1d，挪到 2.10 的 storage 扩展）
}

// —— ob_row_latch.rs ——
/// C++ `ObRowLatch`（ob_row_latch.h:26-44）：1 字节纯 TAS 自旋，不 PAUSE、不让出、不睡眠。原语归模块 02
/// （RULEBOOK §2.7 "oblib::sync::bitlock 的纯 TAS 自旋变体"；API 见 02-sync.md 的 bitlock 一节），这里只转出。
/// 它的 `lock()`/`unlock()`/`ObRowLatchGuard` 只在 storage_core 内用；storage 的翻译代码一律经 `RowRef::lock()` 拿 `RowGuard`。
pub use oblib::sync::bitlock::ObRowLatch;

// —— ob_mvcc_row.rs ——
pub const NDT_NORMAL: u8 = 0x0; pub const NDT_COMPACT: u8 = 0x1;                   // MVR.h:34-35
/// C++ `ObMvccTransNode`（MVR.h:64-235）。size_of == 96（同 C++），载荷从偏移 96 开始（见 3.1 S6）。
#[repr(C)]
pub struct ObMvccTransNode {
    pub tx_id_: ObTransID,                  // 发布后不变
    pub trans_version_: AtomicSCN,
    pub scn_: AtomicSCN,
    seq_no_: AtomicI64,                     // ObTxSEQ 的 raw_val_；发布后仍可能被 set_seq_no（CB.h:481）
    pub write_epoch_: i64,                  // 发布后不变
    pub tx_end_scn_: AtomicSCN,
    prev_: AtomicPtr<ObMvccTransNode>,      // 更旧
    next_: AtomicPtr<ObMvccTransNode>,      // 更新
    pub modify_count_: AtomicU32,
    pub acc_checksum_: AtomicU32,
    pub version_: i64,                      // 发布后不变
    snapshot_version_barrier_: AtomicI64,
    pub type_: u8,                          // 发布后不变
    flag_: AtomicU8,
    linked_: AtomicBool,                    // 核心私有，占 C++ 的尾部填充（偏移 90–95），见 3.1 S8
}
impl ObMvccTransNode {
    pub const NORMAL_READ_BIT: i64 = 0; pub const WEAK_READ_BIT: i64 = 1 << 62;
    pub const COMPACT_READ_BIT: i64 = 2 << 62; pub const SNAPSHOT_VERSION_BARRIER_BIT: i64 = 3 << 62;   // MVR.h:231-234
    pub fn set_committed(&self); pub fn is_committed(&self) -> bool; pub fn set_elr(&self); pub fn is_elr(&self) -> bool;
    pub fn clear_elr(&self); pub fn set_aborted(&self); pub fn clear_aborted(&self); pub fn is_aborted(&self) -> bool;
    pub fn set_delayed_cleanout(&self, delayed_cleanout: bool); pub fn is_delayed_cleanout(&self) -> bool;
    pub fn set_incomplete(&self); pub fn set_complete(&self); pub fn is_incomplete(&self) -> bool;   // MVR.h:131-186
    pub fn trans_commit(&self, commit_version: SCN, tx_end_scn: SCN);     // MVR.cpp:135-141
    pub fn trans_abort(&self, tx_end_scn: SCN);                           // :143-147
    pub fn remove_callback(&self);                                        // :149-152
    pub fn set_safe_read_barrier(&self, is_weak_consistent_read: bool); pub fn is_safe_read_barrier(&self) -> bool;
    pub fn set_snapshot_version_barrier(&self, version: SCN, flag: i64);
    pub fn get_snapshot_version_barrier(&self, version: &mut i64, flag: &mut i64);   // :79-114
    pub fn fill_trans_version(&self, version: SCN) -> ObResult; pub fn fill_scn(&self, scn: SCN) -> ObResult;   // :123-133
    pub fn get_trans_id_and_seq_no(&self, tx_id: &mut ObTransID, seq_no: &mut ObTxSEQ);
    pub fn get_seq_no(&self) -> ObTxSEQ; pub fn set_seq_no(&self, seq_no: ObTxSEQ); pub fn get_tx_id(&self) -> ObTransID;
    pub fn set_tx_end_scn(&self, tx_end_scn: SCN); pub fn get_tx_end_scn(&self) -> SCN;
    pub fn get_tx_version(&self) -> SCN; pub fn get_scn(&self) -> SCN; pub fn get_write_epoch(&self) -> i64;   // MVR.h:203-217
    pub fn flag_raw(&self) -> u8;                                         // C++ RC:348 直接拷 flag_
}

/// C++ `ObMvccTransNode *`（已发布或已封口的节点）。Copy，16 字节：指针 + arena 身份。
#[derive(Clone, Copy)] pub struct NodeRef<'m> { /* NonNull, arena_id: u64, PhantomData<&'m ObMvccTransNode> */ }
impl<'m> core::ops::Deref for NodeRef<'m> { type Target = ObMvccTransNode; }
impl PartialEq for NodeRef<'_> {}                                         // 比地址，对应 C++ 指针比较
impl<'m> NodeRef<'m> {
    pub fn prev(self) -> Option<NodeRef<'m>>;   pub fn next(self) -> Option<NodeRef<'m>>;   // Acquire（RULEBOOK 0.9）
    pub fn get_mtd(self) -> ObMemtableDataHeader<'m>;
    pub fn get_dml_flag(self) -> ObDmlFlag; pub fn get_data_size(self) -> i64;   // MVR.cpp:69-77
    pub fn is_lock_node(self, is_lock: &mut bool) -> ObResult;                  // :154-167
    pub fn need_elr(self) -> bool;                                              // MVR.h:187-195
    pub fn addr(self) -> MvccAddr<ObMvccTransNode>;
}
/// 刚分配、尚未封口的节点：独占，可写全部字段（DerefMut）。C++：`dup_data`/`init_tx_node_` 之后、
/// `mvcc_write` 之前对 `node->tx_id_` 等的赋值（ENG:344-362、:379-397，RC:340-350）。
pub struct NodeMut<'m> { /* NonNull, arena_id, PhantomData<&'m mut ObMvccTransNode>, batch 回指 */ }
impl<'m> core::ops::Deref for NodeMut<'m> { type Target = ObMvccTransNode; }
impl<'m> core::ops::DerefMut for NodeMut<'m> {}
impl<'m> NodeMut<'m> {
    pub fn set_flag_raw(&mut self, flag: u8);                             // RC:348
    pub fn get_mtd(&self) -> ObMemtableDataHeader<'_>;
    pub fn into_ref(self) -> NodeRef<'m>;                                 // 封口：之后非原子字段不可再写
}
/// 预分配的节点批（`batch_mvcc_write_` 的 `new_nodes + pos`，MT.cpp:2505-2577）。
pub struct NodeBatch<'m> { /* AllocHandle 借用、缓冲、pos、上一个节点 */ }
impl<'m> NodeBatch<'m> {
    pub fn remaining(&self) -> i64;
    /// C++ `ObMvccEngine::init_tx_node_`（ENG:369-401）在 pos 处构造。
    pub fn init_tx_node(&mut self, data: &ObMemtableData<'_>) -> ObResult<NodeMut<'m>>;
    /// C++ `pos += aligned_data_size`（MT.cpp:2577）。上一个节点若已被链入，下次 `init_tx_node` 会自动前移（3.1 S8）。
    pub fn advance(&mut self);
}

/// C++ `ObMvccRow`（MVR.h:242-453）。size_of == 112（C++ 断言 <= 120，MVR.h:299）。
#[repr(C)]
pub struct ObMvccRow {
    latch_: ObRowLatch,
    flag_: AtomicU8,
    first_dml_flag_: AtomicI32, last_dml_flag_: AtomicI32,             // ObDmlFlag
    pub update_since_compact_: AtomicI32,
    pub total_trans_node_cnt_: AtomicI64,
    pub latest_compact_ts_: AtomicI64,
    pub last_compact_cnt_: AtomicI64,
    pub max_trans_version_: AtomicSCN, pub max_elr_trans_version_: AtomicSCN,
    pub max_modify_scn_: AtomicSCN, pub min_modify_scn_: AtomicSCN,
    max_trans_id_: AtomicI64, max_elr_trans_id_: AtomicI64,             // ObTransID
    list_head_: AtomicPtr<ObMvccTransNode>,
    latest_compact_node_: AtomicPtr<ObMvccTransNode>,
    index_: AtomicPtr<ObMvccRowIndex>,
}
impl ObMvccRow {
    pub const F_INIT: u8 = 0x0; pub const F_HASH_INDEX: u8 = 0x1; pub const F_BTREE_INDEX: u8 = 0x2;
    pub const F_LOWER_ROW_EXIST_AND_SCANNED: u8 = 0x4; pub const F_LOWER_ROW_DELETED_AND_SCANNED: u8 = 0x8;
    pub const NODE_SIZE_UNIT: i64 = 1024; pub const WARN_WAIT_LOCK_TIME: i64 = 1_000_000;
    pub const WARN_TIME_US: i64 = 10_000_000; pub const LOG_INTERVAL: i64 = 1_000_000; pub const INDEX_TRIGGER_COUNT: i64 = 500;
    pub fn is_btree_indexed(&self) -> bool; pub fn set_btree_indexed(&self); pub fn clear_btree_indexed(&self); pub fn set_hash_indexed(&self);
    pub fn is_lower_row_scanned(&self) -> bool; pub fn is_lower_row_exist_and_scanned(&self) -> bool;
    pub fn is_lower_row_deleted_and_scanned(&self) -> bool;
    pub fn set_lower_row_exist_and_scanned(&self); pub fn set_lower_row_deleted_and_scanned(&self);   // MVR.h:401-436
    pub fn get_first_dml_flag(&self) -> ObDmlFlag; pub fn get_last_dml_flag(&self) -> ObDmlFlag;
    pub fn get_max_trans_version(&self) -> SCN; pub fn get_max_trans_id(&self) -> i64;             // MVR.cpp:722-727, MVR.h:393
    pub fn update_max_trans_version(&self, v: SCN, tx_id: ObTransID);                             // MVR.cpp:729-734
    pub fn update_max_elr_trans_version(&self, v: SCN, tx_id: ObTransID);                         // :736-741
    pub fn get_total_trans_node_cnt(&self) -> i64; pub fn get_last_compact_cnt(&self) -> i64;
    pub fn is_empty(&self) -> bool;
}
/// C++ 未持行锁时的 `ObMvccRow *`/`ObMvccRow &`。Copy，16 字节。
#[derive(Clone, Copy)] pub struct RowRef<'m> { /* NonNull, arena_id, PhantomData<&'m ObMvccRow> */ }
impl<'m> core::ops::Deref for RowRef<'m> { type Target = ObMvccRow; }
impl PartialEq for RowRef<'_> {}
impl<'m> RowRef<'m> {
    pub fn lock(self) -> RowGuard<'m>;                                    // ObRowLatchGuard guard(row.latch_)
    pub fn get_list_head(self) -> Option<NodeRef<'m>>;                    // MVR.h:382，Acquire
    pub fn latest_compact_node(self) -> Option<NodeRef<'m>>;              // RC:81,114 的读
    pub fn addr(self) -> MvccAddr<ObMvccRow>;
}
/// 行锁 guard（RULEBOOK 改名表；C++ `ObRowLatchGuard`）。持锁期间是唯一能改链的入口。Drop 时 `latch_.unlock()`（`ATOMIC_STORE`，SeqCst，`ob_row_latch.h:42`）。
pub struct RowGuard<'m> { /* RowRef<'m> */ }
impl<'m> core::ops::Deref for RowGuard<'m> { type Target = ObMvccRow; }
impl<'m> RowGuard<'m> {
    pub fn row(&self) -> RowRef<'m>;
    pub fn get_list_head(&self) -> Option<NodeRef<'m>>; pub fn latest_compact_node(&self) -> Option<NodeRef<'m>>;
    // 链接原语：C++ 的 `ATOMIC_STORE(&(x->prev_), y)` 等。目标节点必须与本行同一 arena，否则 ob_assert。
    pub fn set_list_head(&mut self, node: Option<NodeRef<'m>>);
    pub fn set_latest_compact_node(&mut self, node: Option<NodeRef<'m>>);
    pub fn set_prev(&mut self, node: NodeRef<'m>, prev: Option<NodeRef<'m>>);
    pub fn set_next(&mut self, node: NodeRef<'m>, next: Option<NodeRef<'m>>);
    // 回放索引（C++ `index_`，MVR.cpp:560-680）
    pub fn has_index(&self) -> bool;
    pub fn alloc_index(&mut self, allocator: &'m AllocHandle) -> ObResult;          // MVR.cpp:661-670
    pub fn reset_index(&mut self);                                                  // ObMvccRowIndex::reset，:324-330
    pub fn get_index_node(&self, index: i64) -> Option<NodeRef<'m>>;               // :337-344
    pub fn set_index_node(&mut self, index: i64, node: Option<NodeRef<'m>>);       // :346-352
    pub fn update_dml_flag_(&mut self, flag: ObDmlFlag, modify_scn: SCN);          // :778-791
}
/// 跨调用、跨线程保存的节点/行地址（C++ 存在结构体里的 `ObMvccRow *`/`ObMvccTransNode *`）。
/// Copy、Send、Sync、'static；只能经某个 `AllocHandle` 的 `row/node`（回调路径上就是 `pin.local_allocator_`）换回引用。
#[derive(Clone, Copy, PartialEq, Eq)] pub struct MvccAddr<T> { /* NonNull<T>, arena_id: u64 */ }
impl<T> MvccAddr<T> { pub fn arena_id(&self) -> u64; }
// ObToString：ObMvccTransNode、ObMvccRow（含 verbose 版，MVR.cpp:169-202、372-429）、NodeRef、RowRef、MvccAddr（打印地址）
```

### 2.5 回收原语（`oblib::sync`，`ob_retire_station.rs`）

```rust
/// C++ `QClock`（RS.h:30-113）。槽数 OB_MAX_CPU_NUM * 4：x86-64 为 256，aarch64 为 512。
pub struct QClock { clock_: CacheAligned<AtomicU64>, qclock_: CacheAligned<AtomicU64>, clock_array_: Box<[AtomicU64]> }
impl QClock {
    pub const MAX_QCLOCK_SLOT_NUM: u64;
    pub fn new() -> Self;                                   // clock_=1, qclock_=0, 槽=u64::MAX（RS.h:36,43）
    pub fn enter_critical(&self) -> u64;                    // RS.h:45-58
    pub fn leave_critical(&self, slot_id: u64);             // :59-63
    pub fn wait_quiescent(&self, clock: u64) -> u64;        // :65-72
    pub fn try_quiescent(&self, clock: &mut u64) -> bool;   // :74-83
}
/// C++ `QClockGuard`（RS.h:265-273）。!Send、!Sync：临界区令牌，不得存进迭代器（RULEBOOK 0.9）。
pub struct QClockGuard<'a> { qclock_: &'a QClock, slot_id_: u64, _not_send: PhantomData<*const ()> }
impl<'a> QClockGuard<'a> { pub fn new(qclock: &'a QClock) -> Self; }
/// C++ `common::ObLink`（ob_link.h:29-38）的布局：模块 05 不提供（1.3），定义在这里，供 `HazardList`、keybtree 节点头、
/// KV cache 块句柄的 `retire_link_` 嵌入。`next_` 的最低位是 C++ 的"已删除"标记（`is_deleted`），照抄。
#[repr(C)] pub struct ObLink { pub next_: *mut ObLink }
/// C++ `HazardList`（RS.h:121-188）：单线程 FIFO，节点是嵌入对象里的 `ObLink`。
pub struct HazardList { size_: i64, head_: *mut ObLink, tail_: *mut ObLink }
impl HazardList { pub fn new() -> Self; pub fn size(&self) -> i64;
    pub unsafe fn push(&mut self, p: NonNull<ObLink>); pub fn pop(&mut self) -> Option<NonNull<ObLink>>;
    pub fn move_to(&mut self, target: &mut HazardList); }
/// C++ `RetireStation`（RS.h:194-263）。槽数 OB_MAX_CPU_NUM * 32：x86-64 为 2048，aarch64 为 4096。
/// C++ 在对象里存 `QClock &qclock_`（RS.h:262）；Rust 版每次调用传入，免得回收域（QClock 与 RetireStation 同在一个
/// static 里）自引用。调用方总是传同一个 QClock。
pub struct RetireStation { retire_limit_: i64, retire_list_: Box<[RetireList]> }
impl RetireStation {
    pub const MAX_RETIRE_SLOT_NUM: i64;
    pub fn new(retire_limit: i64) -> Self;
    /// SAFETY 契约：retire_list 里的对象已经对新读者不可达；在 reclaim_list 弹出之前不得释放或复用。
    pub unsafe fn retire(&self, qclock: &QClock, reclaim_list: &mut HazardList, retire_list: &mut HazardList);   // :241-243
    pub unsafe fn retire_with_limit(&self, qclock: &QClock, reclaim_list: &mut HazardList, retire_list: &mut HazardList,
                                    limit: i64);                                                              // :244-246（重载改名）
    pub fn purge(&self, qclock: &QClock, reclaim_list: &mut HazardList);                                      // :248-255
}
```

### 2.6 keybtree（`ob_keybtree_deps.rs`、`ob_keybtree.rs`）

C++ 是模板 `ObKeyBtree<BtreeKey, BtreeVal>`，有两个实例化：memtable 的 `<ObStoreRowkeyWrapper, ObMvccRow *>`（`QE.h:36`）和 DDL 的 `<ObDatumRowkeyWrapper, ObBlockMetaTreeValue *>`（`ob_tablet_ddl_kv.h:83-85`）。每个实例化各有一套函数内静态的 QClock、RetireStation、ObQSync（`KB.cpp:1742-1761`）。

```rust
pub const MAX_CPU_NUM: i64 = 64; pub const RETIRE_LIMIT: i64 = 1024;
pub const NODE_KEY_COUNT: i64 = 15; pub const NODE_COUNT_PER_ALLOC: i64 = 128;      // KD.h:38-50

/// C++ 模板参数 `BtreeKey`（KB.h:39）。由 storage 为每个实例化实现一个标记类型。
pub trait BtreeKey: 'static {
    /// 存进树里的键：Copy，借用 'a 的内存。memtable 是 `ObStoreRowkeyWrapper<'a>`（8 字节），DDL 是 `ObDatumRowkeyWrapper<'a>`。
    type Key<'a>: Copy;
    /// 比较时需要的上下文（DDL 的 `datum_utils_`，ob_datum_rowkey.h:150-163；memtable 为 `()`）。
    type Ctx: Send + Sync;
    fn shorten<'a: 'b, 'b>(k: Self::Key<'a>) -> Self::Key<'b>;   // 实现一律是恒等函数
    /// C++ `CompHelper::compare` → `search_key.compare(idx_key, cmp)`（KD.h:52-59）。
    fn compare(ctx: &Self::Ctx, search_key: &Self::Key<'_>, idx_key: &Self::Key<'_>, cmp: &mut i32) -> ObResult;
    /// C++ `ObMTKVBuilder::dup_key`（MT.h:81-100）：把键深拷进 arena。DDL 实例化不用，返回 OB_NOT_SUPPORTED。
    fn dup_key<'b>(cap: &ArenaCap<'b>, key: &Self::Key<'_>) -> ObResult<Self::Key<'b>>;
    /// `__builtin_prefetch(mid_key.get_ptr(), 0, 3)`（KD.h:302）的地址；可返回 null。
    fn prefetch_addr(key: &Self::Key<'_>) -> *const u8;
    /// 本实例化的回收域（C++ 函数内静态量）。storage 写一个 `static X: LazyLock<ObKeyBtreeDomain>` 并返回它。
    fn domain() -> &'static ObKeyBtreeDomain;
}
/// C++ 模板参数 `BtreeVal`。值必须 8 字节、低 3 位为 0（KB.h:50-52，KB.cpp:1563 断言）。
pub trait BtreeVal: 'static {
    type Val<'a>: Copy;
    fn shorten<'a: 'b, 'b>(v: Self::Val<'a>) -> Self::Val<'b>;
}
/// memtable 的值族：`ObMvccRow *`。行由树在 `insert_or_get_row` 里创建，所以树能给结果打上 arena 身份。
pub struct ObMvccRowVal;
impl BtreeVal for ObMvccRowVal { type Val<'a> = &'a ObMvccRow; fn shorten<'a: 'b, 'b>(v: &'a ObMvccRow) -> &'b ObMvccRow { v } }

/// 一个实例化一套：C++ `get_qclock()`/`get_retire_station()`/`get_qsync()`（KB.cpp:1742-1761）。
pub struct ObKeyBtreeDomain { /* qclock_: QClock, retire_station_: RetireStation(RETIRE_LIMIT), qsync_: Quiescence, purge_started_, purge_completed_: AtomicU64 */ }
impl ObKeyBtreeDomain { pub fn new() -> Self; }   // 不起线程、不取锁（RULEBOOK 0.5 LazyLock 规则）

/// 节点内存的来源。C++ 的 `BtreeNodeAllocator(ObIAllocator &)`（KB.h:92）。
pub enum BtreeAllocSource { Memstore(AllocHandle), Private(ObKeyBtreeArena) }
/// DDL 树自带的 arena（C++ `ObBlockMetaTree::arena_`，ob_tablet_ddl_kv.cpp:36）：线程安全的分块 bump arena，树析构时整体释放。
pub struct ObKeyBtreeArena { /* Latch<Vec<Box<[u8]>>> + 统计 */ }
impl ObKeyBtreeArena { pub fn new(block_size: i64) -> Self; pub fn total(&self) -> i64; }

/// C++ `BtreeNodeAllocator`（KB.h:81-141）：64 条按 CPU 分片的空闲表，每次向来源要 128 个节点。
pub struct BtreeNodeAllocator<K: BtreeKey, V: BtreeVal> { /* source_, alloc_memory_, free_list_array_: [CacheAligned<..>; 64] */ }
impl<K: BtreeKey, V: BtreeVal> BtreeNodeAllocator<K, V> {
    pub fn new(source: BtreeAllocSource) -> Self;
    pub fn get_allocated(&self) -> i64;        // KB.h:94（C++ 的 alloc_memory_ 从不增加，照抄，见 3.5）
}

/// C++ `ObKeyBtree`（KB.h:254-323）。Send + Sync。
pub struct ObKeyBtree<K: BtreeKey, V: BtreeVal> { /* split_info_, size_, node_allocator_, root_, ctx_, 销毁状态 */ }
impl<K: BtreeKey, V: BtreeVal> ObKeyBtree<K, V> {
    pub fn new(node_allocator: BtreeNodeAllocator<K, V>, ctx: K::Ctx) -> Self;
    pub fn init(&self) -> ObResult;                                         // KB.cpp:1483-1487
    pub fn size(&self) -> i64;
    pub fn destroy(&mut self, is_batch_destroy: bool) -> ObResult;          // KB.cpp:1531-1554
    pub fn pre_batch_destroy(&mut self) -> ObResult;                        // :1502-1507
    pub fn batch_destroy() -> ObResult;                                     // :1510-1528（静态）
    pub fn get<'t>(&'t self, key: &K::Key<'_>, value: &mut Option<V::Val<'t>>) -> ObResult;   // :1638-1650
    /// C++ `insert(key, value)`（:1557-1592）。键和值必须在闭包给的 cap 里构造（DDL：ob_tablet_ddl_kv.cpp:155-196）。
    pub fn insert<'t>(&'t self, build: impl for<'b> FnOnce(&ArenaCap<'b>) -> ObResult<(K::Key<'b>, V::Val<'b>)>) -> ObResult;
    pub fn set_key_range<'t>(&'t self, iter: &mut BtreeIterator<'t, K, V>, min_key: K::Key<'t>, start_exclude: bool,
                             max_key: K::Key<'t>, end_exclude: bool) -> ObResult;                     // :1670-1681
    pub fn set_key_range_btree_raw_iterator<'t>(&'t self, iter: &mut BtreeRawIterator<'t, K, V>, min_key: K::Key<'t>,
                             start_exclude: bool, max_key: K::Key<'t>, end_exclude: bool) -> ObResult; // :1683-1694（重载改名）
    pub fn print(&self, out: &mut dyn core::fmt::Write);                    // :1490-1500（调试）
}
/// memtable 实例化专用：结果带 arena 身份。
impl<K: BtreeKey> ObKeyBtree<K, ObMvccRowVal> {
    pub fn get_row<'t>(&'t self, key: &K::Key<'_>, value: &mut Option<RowRef<'t>>) -> ObResult;
    /// C++ `insert_or_get(key, creator, val)`（:1594-1636）加上 `ObMvccEngine::create_btree_kv_` 的创建器（ENG:431-483）：
    /// 键不存在时由核心调用 `K::dup_key` 并分配、初始化行（`set_btree_indexed`），候选键和行在 OB_EAGAIN 重试间复用。
    pub fn insert_or_get_row<'t>(&'t self, key: &K::Key<'_>, stored_key: &mut Option<K::Key<'t>>,
                                 value: &mut Option<RowRef<'t>>) -> ObResult;
}

/// C++ `BtreeIterator`（KB.h:148-213）：每批最多 225 个 KV（KVQueue），批与批之间不在临界区内。
pub struct BtreeIterator<'t, K: BtreeKey, V: BtreeVal> { /* tree, kv_queue_, start/end_key_, flags */ }
impl<'t, K: BtreeKey, V: BtreeVal> BtreeIterator<'t, K, V> {
    pub fn new() -> Self; pub fn reset(&mut self);
    pub fn get_next(&mut self, key: &mut K::Key<'t>, value: &mut V::Val<'t>) -> ObResult;    // KB.cpp:1188-1210
    pub fn is_reverse_scan(&self) -> bool; pub fn is_iter_end(&self) -> bool;
}
impl<'t, K: BtreeKey> BtreeIterator<'t, K, ObMvccRowVal> {
    pub fn get_next_row(&mut self, key: &mut K::Key<'t>, value: &mut Option<RowRef<'t>>) -> ObResult;
}
/// C++ `BtreeRawIterator`（KB.h:225-248）：整个生命期都在临界区内（"DO NOT Keep for a long time"，KB.h:223-224）。!Send。
pub struct BtreeRawIterator<'t, K: BtreeKey, V: BtreeVal> { /* ... */ }
impl<'t, K: BtreeKey, V: BtreeVal> BtreeRawIterator<'t, K, V> {
    pub fn new() -> Self; pub fn reset(&mut self);
    pub fn get_next(&mut self, key: &mut K::Key<'t>, value: &mut V::Val<'t>) -> ObResult;
    pub fn estimate_key_count(&mut self, top_level: i64, child_count: &mut i64, key_count: &mut i64) -> ObResult;  // KB.cpp:1357
    pub fn split_range(&mut self, top_level: i64, btree_node_count: i64, range_count: i64,
                       key_array: &mut dyn ObIArray<K::Key<'t>>) -> ObResult;                                  // :1387
    pub fn estimate_element_count(&mut self, physical_row_count: &mut i64, element_count: &mut i64) -> ObResult;  // :1464
    pub fn is_reverse_scan(&self) -> bool;
}
```

### 2.7 事务回调链表核心（`ob_tx_callback_list.rs`）

```rust
/// C++ `ObTxCallbackList` 的机制部分。`C` 是 storage 的 `TxCallback`（RULEBOOK §2.8 的三变体 enum）。
pub struct TxCallbackListCore<C: Send + Sync + 'static> { /* 哨兵、log_cursor_、log_epoch_、parallel_start_pos_、
    append_latch_/log_latch_/iter_synced_latch_: ObByteLock、下一个 append 序号、延后释放表 */ }
/// 一个回调节点：核心私有的链接 + 载荷。以 Arc 持有（链表一份，位置句柄各一份）。
pub struct TxCallbackNode<C> { /* prev_/next_: AtomicPtr, seq_: u64, removed_: AtomicBool */ pub payload: C }
impl<C> core::ops::Deref for TxCallbackNode<C> { type Target = C; }
/// 跨调用保存的位置（C++ `ObCallbackScope::start_/end_`，ob_redo_log_generator.h:42-43）。持有 Arc，节点被摘掉后仍安全。
#[derive(Clone)] pub struct TxCallbackPos<C> { /* None=哨兵 或 Arc<TxCallbackNode<C>> */ }
/// C++ `ObTxCallbackList::LOCK_MODE`（TCL.h:148-154），§2.12 的枚举规则。
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq)] pub struct LOCK_MODE(pub i32);
impl LOCK_MODE { pub const LOCK_ITERATE: Self = Self(1); pub const LOCK_APPEND: Self = Self(2); pub const LOCK_ALL: Self = Self(3);
                 pub const TRY_LOCK_ITERATE: Self = Self(4); pub const TRY_LOCK_APPEND: Self = Self(5); }
/// C++ `LockState`（TCL.h:139-147）。
#[derive(Clone, Copy, Default)] pub struct LockState { pub APPEND_LOCKED_: bool, pub ITERATE_LOCKED_: bool }
impl LockState { pub fn is_locked(&self) -> bool; }
/// C++ `ObTxCallbackList::LockGuard`（TCL.h:155-164、TCL.cpp:980-1052）。!Send。
pub struct TxCallbackLockGuard<'a, C: Send + Sync + 'static> { pub state_: LockState, /* list、延后释放 */ }
impl<C: Send + Sync + 'static> TxCallbackLockGuard<'_, C> { pub fn is_locked(&self) -> bool; }
/// C++ `try_lock_log()` 返回的 `ObByteLock *`（TCL.h:187-190）加调用方的解锁。!Send。
pub struct TxCallbackLogGuard<'a, C: Send + Sync + 'static> { /* list */ }
/// 持锁期间的借用位置（C++ 遍历里的 `ObITransCallback *iter`）。不 Copy。
pub struct TxCallbackCursor<'g, C> { /* 原始指针 + PhantomData<&'g TxCallbackNode<C>> */ }
impl<'g, C> TxCallbackCursor<'g, C> {
    pub fn is_head(&self) -> bool;
    pub fn next(&self) -> TxCallbackCursor<'g, C>;  pub fn prev(&self) -> TxCallbackCursor<'g, C>;   // Acquire
    pub fn get(&self) -> Option<&'g C>;             // 哨兵返回 None
    pub fn to_pos(&self) -> TxCallbackPos<C>;
}
impl<C> PartialEq for TxCallbackCursor<'_, C> {}

impl<C: Send + Sync + 'static> TxCallbackListCore<C> {
    pub fn new() -> Self;
    pub fn reset(&mut self);                                                // TCL.cpp:70-72
    pub fn new_node(payload: C) -> Arc<TxCallbackNode<C>>;
    /// `is_append_only` = C++ `callback_mgr_.is_callback_list_append_only(id_)`（TCL.cpp:955-958，CB.h:327-330）。
    pub fn lock(&self, mode: LOCK_MODE, is_append_only: bool) -> TxCallbackLockGuard<'_, C>;
    pub fn try_lock_log(&self) -> Option<TxCallbackLogGuard<'_, C>>;
    pub fn empty(&self) -> bool;                                            // TCL.h:191
    // —— 位置 ——
    pub fn head<'g>(&'g self, g: &'g TxCallbackLockGuard<'_, C>) -> TxCallbackCursor<'g, C>;   // get_guard()，TCL.h:181
    pub fn head_for_log<'g>(&'g self, g: &'g TxCallbackLogGuard<'_, C>) -> TxCallbackCursor<'g, C>;
    pub fn cursor_at<'g>(&'g self, g: &'g TxCallbackLockGuard<'_, C>, pos: &TxCallbackPos<C>) -> Option<TxCallbackCursor<'g, C>>;
    pub fn cursor_at_for_log<'g>(&'g self, g: &'g TxCallbackLogGuard<'_, C>, pos: &TxCallbackPos<C>) -> Option<TxCallbackCursor<'g, C>>;
    pub fn get_log_cursor(&self) -> TxCallbackPos<C>;                       // TCL.h:183
    pub fn log_cursor_is_head(&self) -> bool;                               // `log_cursor_ == &head_`
    pub fn get_log_epoch(&self) -> i64;                                     // TCL.cpp:933-937
    /// C++ `set_log_cursor_(cursor)`（TCL.cpp:939-943）；epoch 由调用方算（`cursor->get_epoch()` 或 i64::MAX）。
    pub fn set_log_cursor(&self, cursor: &TxCallbackCursor<'_, C>, epoch: i64);
    pub fn parallel_start_pos(&self) -> Option<TxCallbackPos<C>>;          // TCL.h:233
    pub fn set_parallel_start_pos(&self, g: &TxCallbackLockGuard<'_, C>, pos: Option<&TxCallbackCursor<'_, C>>);
    // —— 改链（需要 APPEND_LOCKED_；C++ ObITransCallback::append，CB.cpp:132-147）——
    pub fn append_after(&self, g: &TxCallbackLockGuard<'_, C>, pos: &TxCallbackCursor<'_, C>, node: Arc<TxCallbackNode<C>>) -> ObResult;
    pub fn append_chain_after(&self, g: &TxCallbackLockGuard<'_, C>, pos: &TxCallbackCursor<'_, C>,
                              chain: Vec<Arc<TxCallbackNode<C>>>) -> ObResult;
    /// C++ `ObITransCallback::remove()`（CB.cpp:149-161）。需要 ITERATE_LOCKED_，否则返回 OB_ERR_UNEXPECTED（记 ERROR）；
    /// 摘尾节点而 guard 没有 append 锁时，核心在摘链期间临时取 append 锁（对应 C++ TCL.cpp:334-339 的临时 LockGuard）。
    /// 节点内存延后到 guard 释放时归还；摘的是日志游标之后（未写日志）的节点时，再延后到下一次拿到日志锁时（3.6 S41）。
    pub fn unlink(&self, g: &TxCallbackLockGuard<'_, C>, cur: &TxCallbackCursor<'_, C>) -> ObResult;
}
```

### 2.8 palf 组缓冲与 LSN 分配器（`log_group_buffer.rs`、`lsn_allocator.rs`）

```rust
/// C++ `LogGroupBuffer`（LGB.h:30-124）。存储是按 u64 字的原子数组（Q4、修订 A6）。
pub struct LogGroupBuffer { /* start_lsn_, reuse_lsn_, readable_begin_lsn_: AtomicU64; truncate_lock_: Latch<()>;
                              reserved/available_buffer_size_: AtomicI64; data_buf_: ArcSwapOption<GroupBufferStorage>; is_inited_: AtomicBool */ }
impl LogGroupBuffer {
    pub fn new() -> Self;
    pub fn init(&mut self, start_lsn: &LSN) -> ObResult;                                    // LGB.cpp:46-82
    pub fn reset(&mut self); pub fn destroy(&self);                                         // :35-44, :84-97
    pub fn fill(&self, lsn: &LSN, data: &[u8]) -> ObResult;                                 // :195-230
    pub fn fill_padding_body(&self, lsn: &LSN, data: &[u8], log_body_size: i64) -> ObResult; // :232-284
    /// C++ `get_log_buf(lsn, total_len, LogWriteBuf &)`（:166-193）：每个片段调用一次 `push`（= `log_buf.push_back`）。
    pub fn get_log_buf(&self, lsn: &LSN, total_len: i64, push: &mut dyn FnMut(GroupBufferSlice) -> ObResult) -> ObResult;
    pub fn can_handle_new_log(&self, lsn: &LSN, total_len: i64) -> bool;                     // :126-138
    pub fn can_handle_new_log_lsn(&self, lsn: &LSN, total_len: i64, ref_reuse_lsn: &LSN) -> bool;   // :140-164（重载改名）
    pub fn check_log_buf_wrapped(&self, lsn: &LSN, log_len: i64, is_buf_wrapped: &mut bool) -> ObResult;   // :306-328
    pub fn get_available_buffer_size(&self) -> i64; pub fn get_reserved_buffer_size(&self) -> i64;
    pub fn activate(&self) -> ObResult;                                                     // :330-340
    pub fn inc_update_readable_begin_lsn(&self, new_lsn: &LSN) -> ObResult;                 // :373-383
    pub fn inc_update_reuse_lsn(&self, new_reuse_lsn: &LSN) -> ObResult;                    // :385-403
    pub fn get_reuse_lsn(&self, reuse_lsn: &mut LSN);
    pub fn truncate(&self, new_lsn: &LSN) -> ObResult;                                      // :405-434
    pub fn read_data(&self, read_begin_lsn: &LSN, in_read_size: i64, buf: &mut [u8], out_read_size: &mut i64) -> ObResult;  // :436-481
}
/// 组缓冲里的一段（C++ `LogWriteBuf::InnerStruct { const char *buf_; int64_t buf_len_; }`，log_writer_utils.h:54-57，
/// 指向组缓冲时）。'static：持有存储的 Arc，可以放进跨线程的 I/O 任务。
#[derive(Clone)] pub struct GroupBufferSlice { /* Arc<GroupBufferStorage>, pos, len */ }
impl GroupBufferSlice {
    pub fn len(&self) -> i64;
    pub fn copy_to(&self, offset: i64, dst: &mut [u8]) -> ObResult;   // 读（I/O 前拷进对齐缓冲：log_block_handler.cpp:366-376）
    pub fn copy_from(&self, offset: i64, src: &[u8]) -> ObResult;     // 写回组头（log_group_entry_header.cpp:119-161 的就地写）
}

/// C++ `LSNAllocator`（LA.h:30-114）。
pub struct LSNAllocator { /* lsn_ts_meta_: oblib::atomic::AtomicU128（即 portable_atomic 的）; lock_: BigReaderLatch<Bases{log_id_base_, scn_base_}>
                             （LatchId LOG_OFFSET_ALLOC_LOCK，LA.cpp:29）; group_entry_header_ser_size_: i64; is_inited_: AtomicBool */ }
impl LSNAllocator {
    /// `group_entry_header_ser_size` = `LogGroupEntryHeader::HEADER_SER_SIZE`（LA.cpp:340 用到；logservice 的常量，核心不能直接引用）。
    pub fn new(group_entry_header_ser_size: i64) -> Self;
    pub fn init(&mut self, log_id: i64, scn: &SCN, start_lsn: &LSN) -> ObResult;          // LA.cpp:50-74
    pub fn reset(&mut self);
    pub fn get_log_block_size(&self, block_id: u64, block_size: &mut i64) -> ObResult;     // LA.h:38-45
    pub fn get_max_log_id(&self) -> i64; pub fn get_max_scn(&self) -> SCN;                 // LA.cpp:208-236
    pub fn get_curr_end_lsn(&self, curr_end_lsn: &mut LSN) -> ObResult;                    // :238-249（不取锁）
    pub fn try_freeze_by_time(&self, last_lsn: &mut LSN, last_log_id: &mut i64) -> ObResult;   // :251-285
    pub fn try_freeze(&self, last_lsn: &mut LSN, last_log_id: &mut i64) -> ObResult;           // :287-319
    pub fn alloc_lsn_scn(&self, base_scn: &SCN, size: i64, log_id_upper_bound: i64, lsn_upper_bound: &LSN,
                         lsn: &mut LSN, log_id: &mut i64, scn: &mut SCN, is_new_log: &mut bool,
                         need_gen_padding_entry: &mut bool, padding_len: &mut i64) -> ObResult;    // :321-520
    pub fn inc_update_last_log_info(&self, lsn: &LSN, log_id: i64, scn: &SCN) -> ObResult;    // :107-171
    pub fn inc_update_scn_base(&self, scn: &SCN) -> ObResult;                                  // :173-206
    pub fn truncate(&self, lsn: &LSN, log_id: i64, scn: &SCN) -> ObResult;                     // :76-105
}
```

### 2.9 KV cache（`share::cache`）

**对消费方的 API**（`ob_kv_storecache.rs`）。值和键在块里一律是字节（RULEBOOK 0.8），消费方用自己的视图类型解码（Q5）。

```rust
/// C++ `ObIKVCacheKey`（SR.h:41-53）。存储形态是 `deep_copy` 写出的字节。
pub trait ObIKVCacheKey: Send + Sync {
    fn size(&self) -> i64;
    fn deep_copy(&self, buf: &mut [u8]) -> ObResult;                 // 写进 buf[..size]
    /// C++ `stored_key->equal(key, is_equal)`（MP.cpp:197,298）：本键（查找键）与存储形态比较。
    fn equal(&self, stored: &[u8], equal: &mut bool) -> ObResult;
    fn hash(&self, hash_value: &mut u64) -> ObResult;                // SR.h:49
}
/// C++ `ObIKVCacheValue`（SR.h:55-62）。
pub trait ObIKVCacheValue: Send + Sync {
    fn size(&self) -> i64;
    fn deep_copy(&self, buf: &mut [u8]) -> ObResult;
}
/// C++ `ObKVCache<Key, Value>`（KS.h:55-90）。方法名、返回码同 C++。
pub struct ObKVCache<K: ObIKVCacheKey, V: ObIKVCacheValue> { /* cache_id_, inited_ */ }
impl<K: ObIKVCacheKey, V: ObIKVCacheValue> ObKVCache<K, V> {
    pub fn new() -> Self;
    pub fn init(&mut self, cache_name: &[u8], mem_limit_pct: i64) -> ObResult;          // KS.h:373-389（默认 100）
    pub fn destroy(&mut self);                                                          // KS.h:392-398
    pub fn put(&self, key: &K, value: &V, overwrite: bool) -> ObResult;                // KS.h:487-502
    /// C++ `put_and_fetch(key, value, const V *&pvalue, handle, overwrite)`（KS.h:506-526）：值经 `handle.value()` 读。
    pub fn put_and_fetch(&self, key: &K, value: &V, handle: &mut ObKVCacheHandle, overwrite: bool) -> ObResult;
    /// C++ `get(key, const V *&pvalue, handle)`（KS.h:529-547）：值经 `handle.value()` 读。
    pub fn get(&self, key: &K, handle: &mut ObKVCacheHandle) -> ObResult;
    pub fn erase(&self, key: &K) -> ObResult;                                          // KS.h:550-559
    /// C++ `alloc(ksz, vsz, kvpair, handle, inst_handle)`（KS.h:562-581）：预留空间，`kvpair` 给出可写缓冲。
    pub fn alloc(&self, key_size: i64, value_size: i64, kvpair: &mut Option<ObKVCachePair>,
                 handle: &mut ObKVCacheHandle, inst_handle: &mut ObKVCacheInstHandle) -> ObResult;
    /// C++ `put_kvpair(inst_handle, kvpair, handle, overwrite)`（KS.h:332-354）。kvpair 按值交回，发布后保护转进 handle。
    pub fn put_kvpair(&self, inst_handle: &mut ObKVCacheInstHandle, kvpair: ObKVCachePair,
                      handle: &mut ObKVCacheHandle, overwrite: bool) -> ObResult;
    pub fn size(&self) -> i64; pub fn count(&self) -> i64; pub fn get_hit_cnt(&self) -> i64;
    pub fn get_miss_cnt(&self) -> i64; pub fn get_hit_rate(&self) -> f64; pub fn store_size(&self) -> i64;
    pub fn get_cache_id(&self) -> i64;
}
/// C++ `ObKVCacheHandle`（KS.h:281-302）。不 Clone（C++ 禁拷贝，HD.h:56）。Send + Sync。
pub struct ObKVCacheHandle { /* Option<KvBlockHandle> */ }
impl ObKVCacheHandle {
    pub fn new() -> Self; pub fn reset(&mut self); pub fn is_valid(&self) -> bool;     // KS.cpp:30-63
    pub fn move_from(&mut self, other: &mut ObKVCacheHandle);                          // :41-47
    pub fn assign(&mut self, other: &ObKVCacheHandle) -> ObResult;                     // :49-56（可能 OB_ALLOCATE_MEMORY_FAILED）
    pub fn key(&self) -> &[u8]; pub fn value(&self) -> &[u8];                          // 无效句柄返回 &[]
    /// 给 `ObPointerSwizzleNode` 用（C++ 存 `ObKVMemBlockHandle *` + `seq_num_`，ob_kvcache_pointer_swizzle.h:57-59）。
    pub fn get_block_ref(&self) -> Option<KvBlockRef>;
    /// C++ `handle.handle_.hazptr_holder_.protect(ok, mb_handle_, seq_num_)`（ob_kvcache_pointer_swizzle.cpp:75）。
    pub fn protect_by_ref(&mut self, r: &KvBlockRef, success: &mut bool) -> ObResult;
}
/// RULEBOOK 改名表：替代 `ObKVMemBlockHandle *`。不透明：保护一个内存块上的一对键值；只给 `&[u8]`；Drop 释放保护。
pub struct KvBlockHandle { /* HazptrHolder + 键/值在块内的偏移与长度 */ }
impl KvBlockHandle { pub fn key(&self) -> &[u8]; pub fn value(&self) -> &[u8]; pub fn try_clone(&self) -> ObResult<KvBlockHandle>; }
/// 块 id + 代号 + 值的位置（RULEBOOK 0.8 "map 节点只存块 id 和 seq_num"）。Copy、无生命周期，可以存进缓存的值里。
#[derive(Clone, Copy, PartialEq, Eq)] pub struct KvBlockRef { /* mb_index_: u32, seq_num_: i32, policy_: u8, key/value 偏移和长度 */ }
impl KvBlockRef { pub fn is_lfu(&self) -> bool; }        // swizzle 只对 LFU 块生效（ob_kvcache_pointer_swizzle.cpp:44-48）
/// C++ `ObKVCachePair`（SR.h:64-76）作为 `alloc` 的返回：独占的写入预留，自带块保护。Send。
pub struct ObKVCachePair { /* KvBlockHandle（保护）+ 可写区间 */ }
impl ObKVCachePair {
    pub fn key_buf(&mut self) -> &mut [u8]; pub fn value_buf(&mut self) -> &mut [u8];
    pub fn size(&self) -> i64;                              // C++ `size_` = align8(sizeof(pair)+k+v)，SR.cpp:111-114
}
/// C++ `ObKVCacheInstHandle`（IM.h:95-110）：Clone = 增加引用（C++ 拷贝构造）。
#[derive(Clone)] pub struct ObKVCacheInstHandle { /* ... */ }
impl ObKVCacheInstHandle { pub fn new() -> Self; pub fn reset(&mut self); pub fn is_valid(&self) -> bool;
                           pub fn get_inst(&self) -> Option<&ObKVCacheInst>; }
/// C++ `ObKVGlobalCache`（KS.h:114-278）：`X::instance()`（RULEBOOK 0.5）。
pub struct ObKVGlobalCache { /* store_, map_, insts_, hazard station/domain */ }
impl ObKVGlobalCache {
    pub fn instance() -> &'static ObKVGlobalCache;
    pub fn default_max_cache_size() -> i64;                                       // 1 TiB
    pub fn init(&self, bucket_num: i64, max_cache_size: i64, block_size: i64, cache_wash_interval: i64,
                opts: &ObKVCacheRuntimeOptions) -> ObResult;                      // KS.cpp:184-237
    pub fn stop(&self); pub fn wait(&self); pub fn destroy(&self);
    pub fn reload_config(&self, opts: &ObKVCacheRuntimeOptions) -> ObResult;     // KS.cpp:564-603
    pub fn get_suitable_bucket_num(&self, memory_limit: i64, bucket_num: &mut i64) -> ObResult;
    pub fn get_cache_inst_info(&self, inst_handles: &mut dyn ObIArray<ObKVCacheInstHandle>) -> ObResult;
    pub fn get_memblock_info(&self, infos: &mut dyn ObIArray<ObKVCacheStoreMemblockInfo>) -> ObResult;
    pub fn erase_cache(&self) -> ObResult; pub fn erase_cache_name(&self, cache_name: &[u8]) -> ObResult;   // 重载改名
    pub fn sync_wash_mbs(&self, wash_size: i64, wash_blocks: &mut Option<Box<ObCacheMemBlock>>) -> ObResult;
    pub fn get_washable_size(&self, washable_size: &mut i64) -> ObResult; pub fn print_all_cache_info(&self);
    pub fn get_cache_name(&self, cache_id: i64, cache_name: &mut &[u8]) -> ObResult;
    pub fn get_bucket_num(&self) -> i64; pub fn get_managed_used(&self) -> i64;
}
```

**内部组件**（只在 `share::cache` 内可见；签名照 C++，这里只列和回收有关的）：

```rust
// ob_kvcache_hazard_version.rs —— map 节点的 hazard version
pub(crate) struct ObKVCacheHazardNode { hazard_next_: *mut ObKVCacheHazardNode, version_: u64 }   // HV.h:25-39
pub(crate) struct ObKVCacheHazardSlot { acquired_version_: AtomicU64, delete_list_: AtomicPtr<..>,
    waiting_nodes_count_: AtomicI64, last_retire_version_: AtomicU64, is_retiring_: AtomicBool }   // HV.h:42-67
impl ObKVCacheHazardSlot { fn acquire(&self, v: u64) -> bool; fn release(&self);
    unsafe fn delete_node(&self, node: NonNull<ObKVCacheHazardNode>); fn retire(&self, version: u64); }   // retire 见 3.8 FIXED
pub(crate) struct ObKVCacheHazardStation { version_: AtomicU64, waiting_node_threshold_: i64, hazard_slots_: Box<[..]> }  // HV.h:69-93
impl ObKVCacheHazardStation { fn init(&mut self, waiting_node_threshold: i64, slot_num: i64) -> ObResult;
    unsafe fn delete_node(&self, slot_id: i64, node: NonNull<..>) -> ObResult; fn acquire(&self, slot_id: &mut i64) -> ObResult;
    fn release(&self, slot_id: i64); fn retire(&self) -> ObResult; fn get_min_version(&self) -> u64; }
pub(crate) struct ObKVCacheHazardGuard<'a> { .. }   // HV.h:95-106；!Send；get_ret()/get_slot_id()
// ob_kvcache_hazard_pointer.rs / ob_kvcache_hazard_domain.rs —— 内存块的 hazard pointer
pub(crate) struct HazardPointer { mb_handle_: AtomicPtr<ObKVMemBlockHandle>, .. }   // protect(mb, seq)/protect(mb)/release，HP.cpp:24-54
pub(crate) struct HazptrHolder { .. }   // HD.h:52-83；不 Clone；protect/release/reset/move_from/assign
pub(crate) struct HazardDomain { .. }   // HD.h:161-…；retire/reclaim(func)/wash；get_instance()
// ob_kvcache_struct.rs / ob_kvcache_store.rs —— 块句柄、块、store（QClock + RetireStation(RETIRE_LIMIT=2)，ST.h:143-144,215-224）
// ob_kvcache_map.rs —— Node { hazard 头, inst_, hash_code_, seq_num_: i32, kvpair_size_: i32, mb_index_: u32,
//                            pair_off_: u32（键长、值长在块内 pair 头里）, next_: AtomicPtr<Node>, get_cnt_: AtomicI64 }（替代 MP.h:77-103 的指针）
// 其他内部结构的来源：块句柄是定长数组 `mb_handles_`（ST.h:241），下标即 `handle_index_of`（ST.cpp:37-40）；
//   空闲池 `mb_handles_pool_`（ST.h:242，C++ 存 `ObKVMemBlockHandle *`）用 05 的 `ObFixedQueue<u32>` 存下标；
//   `mb_list_` 的 `dl_*` 在本文件内实现（1.3）；map 的 `bucket_lock_: ObBucketLock`（MP.h:134）用 02 的 `BucketLatch`；
//   inst map 的 `DRWLock lock_`（ob_kvcache_inst_map.h:136）用 02 的 `ShardedRwLatch<T>`。
```

### 2.10 storage 侧扩展 trait（第 3 步翻译；签名在此冻结）

1.2 里搬到 storage 的函数，挂在下面三个 trait 上（RULEBOOK 不允许跨 crate 写固有 impl；名字见修订 A5）。trait 定义和实现都在 `rust/port/storage/src/memtable/mvcc/ob_mvcc_row.rs`，函数体由第 3 步照 C++ 逐行翻译：

```rust
/// 不持行锁调用的 `ObMvccRow` 方法（它们自己取锁，或者只用原子量）。
pub trait ObMvccRowExt<'m> {
    fn mvcc_write(self, ctx: &mut ObStoreCtx, node: NodeRef<'m>, check_exist: bool, res: &mut ObMvccWriteResult<'m>) -> ObResult;   // MVR.cpp:1053
    fn mvcc_write_(self, ctx: &mut ObStoreCtx, node: NodeRef<'m>, res: &mut ObMvccWriteResult<'m>) -> ObResult;                     // :834
    fn mvcc_undo(self);                                                                                       // :1036
    fn check_row_locked(self, ctx: &mut ObMvccAccessCtx, lock_state: &mut ObStoreRowLockState) -> ObResult;  // :1120
    fn need_compact(self, for_read: bool, for_replay: bool) -> bool;                                          // :504
    fn wakeup_waiter(self, tablet_id: &ObTabletID, key: &ObMemtableKey<'_>) -> ObResult;                      // :820
    fn print_row(self);                                                                                       // :1209
}
impl<'m> ObMvccRowExt<'m> for RowRef<'m> { /* 第 3 步 */ }
/// 必须在持行锁时调用的 `ObMvccRow` 方法（C++ 调用方先 `ObRowLatchGuard guard(value_.latch_)`）。
pub trait ObMvccRowLockedExt<'m> {
    fn insert_trans_node(&mut self, ctx: &dyn ObIMvccCtx, node: NodeRef<'m>, allocator: &'m AllocHandle,
                         next_node: &mut Option<NodeRef<'m>>) -> ObResult;                 // :547
    fn unlink_trans_node(&mut self, node: NodeRef<'m>) -> ObResult;                        // :431
    fn row_compact(&mut self, memtable: &ObMemtable, snapshot_version: SCN, node_alloc: &'m AllocHandle) -> ObResult;   // :526
    fn elr(&mut self, tx_id: &ObTransID, elr_commit_version: SCN, tablet_id: &ObTabletID, key: Option<&ObMemtableKey<'_>>) -> ObResult;  // :689
    fn trans_commit(&mut self, commit_version: SCN, node: NodeRef<'m>) -> ObResult;        // :743
    fn remove_callback(&mut self, cb: &ObMvccRowCallback) -> ObResult;                     // :793
}
impl<'m> ObMvccRowLockedExt<'m> for RowGuard<'m> { /* 第 3 步 */ }
pub trait ObMvccTransNodeExt<'m> {
    fn checksum(self, bc: &mut ObBatchChecksum);                                           // MVR.cpp:45-49
    fn m_cal_acc_checksum(self, last_acc_checksum: u32) -> u32;                            // :51-59
    fn cal_acc_checksum(self, last_acc_checksum: u32);                                     // :61-66
}
impl<'m> ObMvccTransNodeExt<'m> for NodeRef<'m> { /* 第 3 步 */ }
```

- 签名里的 `ObStoreCtx`、`ObMvccWriteResult`、`ObStoreRowLockState` 等是 storage 的类型，生命周期参数以 `types.tsv` 为准；这里只冻结"接收者是 `RowRef`/`RowGuard`/`NodeRef`、节点参数是 `NodeRef`"这两点。
- `ObMvccRowFilter`（`MVR.cpp:204-322`）照常作为 storage 的私有类型翻译。

## 3. 语义

每条给出 C++ 证据；"FIXED(port)"表示照样复刻会是未定义行为、按 RULEBOOK 0.1.4 改成安全写法，代码里在对应位置标 `FIXED(port): <原缺陷>`；"新增"表示 C++ 没有对应、只在协议出错时才可观察到的保护。3.9 有汇总表。

### 3.1 MVCC

- **S1 标志位**：`F_WEAK_CONSISTENT_READ_BARRIER=0x1`、`F_STRONG_…=0x2`、`F_COMMITTED=0x4`、`F_ELR=0x8`、`F_ABORTED=0x10`、`F_DELAYED_CLEANOUT=0x40`、`F_INCOMPLETE_STATE=0x80`（`MVR.cpp:36-43`）；行标志 `MVR.h:260-266`。置位/清位是"读、或、CAS"循环（`ATOMIC_ADD_TAG/SUB_TAG`，`MVR.h:43-59`）→ `fetch_update`，SeqCst。`need_elr()` 用整字节相等比较（`MVR.h:192-194`），所以核心私有的 `linked_` 不能占用 `flag_` 的空位，放在填充字节里。
- **S2 提交写回顺序**：先写版本，再置 committed，再写 `tx_end_scn`（`MVR.cpp:135-141`，注释"we need set commit version before set committed"）；读者先读 committed 再读版本（`ITR:125-160`；`TXF:500-506` 注释强调 ARM 上的读序）。全部 SeqCst，不弱化（RULEBOOK 0.8）。
- **S3 链接的内存序**：写链照 C++ 的 `ATOMIC_STORE` 用 SeqCst（`MVR.cpp:604-612,968-973`、`RC:367-382`）；读者的 `iter->prev_`/`->next_` 在 C++ 里是普通读（`ITR:144,165,170,191,262,329`，storage 里共 44 处），Rust 版 `prev()`/`next()` 用 Acquire（RULEBOOK 0.9、§2.7，审查 F13）。
- **S4 ELR 改版本**：`iter->trans_version_ = elr_commit_version` 是普通写（`MVR.cpp:705`），与 `trans_version_.atomic_load()`（`ITR:160`）构成数据竞争 → `atomic_store`。FIXED(port)。
- **S5 发布后再写的字段**：`modify_count_` 在头插之后才写（`MVR.cpp:975-979`），`acc_checksum_` 在 `get_redo` 里写（`CB.cpp:1907`），`seq_no_` 在 `set` 里对已链接的节点再写一次（`CB.h:481`），`max_trans_id_` 不持锁写（`MVR.cpp:733`，经 `MT.cpp:1103`）→ 全部改原子字段，写用 Release、读用 Acquire；C++ 的写入时机原样保留。FIXED(port)（数据竞争）。
- **S6 载荷对齐**：C++ 的 `buf_` 紧跟在 `flag_` 之后、偏移 90（`MVR.h:85-99`：12 个 8/4 字节字段占 0–87，`type_` 88、`flag_` 89），`ObMemtableDataHeader` 的 `int64_t buf_len_` 因此未对齐（`ob_memtable_data.h:69-71`）。Rust 版把载荷头放在 `size_of::<ObMvccTransNode>()`（96，8 字节对齐）；分配大小公式 `sizeof(node) + dup_size()`（`MT.h:127`、`MT.cpp:2505`、`RC:327`）不变，所以 memstore 用量不变。载荷指针从分配得到的基址派生，不从 `&ObMvccTransNode` 派生（RULEBOOK 0.9、S-21）。FIXED(port)（未对齐访问）。
- **S7 压缩节点的未初始化字段**：`construct_compact_node_` 只 `alloc` 不构造（`RC:329`），从不写 `write_epoch_`、`tx_end_scn_`（`RC:340-350`），之后 `to_string`（`MVR.cpp:200`）和多版本迭代（`MVI:153`）会读到 → Rust 版 `dup_data` 一律先按构造函数默认值初始化（`write_epoch_=0`、`tx_end_scn_=max`，`MVR.h:67-81`）。FIXED(port)（读未初始化内存），并作为新的疑似 bug 报告（8.2）。
- **S8 批量预分配的槽位复用**：`batch_mvcc_write_` 只在成功时 `pos += aligned_data_size`（`MT.cpp:2577`）。`mvcc_write` 在 TSC 或主键重复时，节点已经头插、随后 `mvcc_undo` 摘掉（`MVR.cpp:1075-1098`、`:1036-1051`），失败路径不前移 `pos`，下一行在同一地址重新构造节点（`ENG:374`），而并发读者可能还拿着旧节点在沿 `prev_` 走 → 读到被改写的节点。Rust 版：节点一旦被链入过（任何 `RowGuard` 链接原语把它作为目标），它的槽位就不再复用；从未链入的槽位照 C++ 复用。FIXED(port)（释放后使用），新的疑似 bug（8.2）。
- **S9 布局**：`size_of::<ObMvccRow>() == 112`、`size_of::<ObMvccTransNode>() == 96`（编译期断言；C++ 只断言行 <= 120，`MVR.h:299`）。行布局依赖 `size_of::<ObRowLatch>() == 1`，由 02 的 T14 断言。
- **S10 行锁**：纯 TAS，`swap(true, SeqCst)` 循环，不 PAUSE、不让出（`ob_row_latch.h:37-42`，复核 C13）；解锁 `store(false, SeqCst)`（`:42`）。原语的实现和测试归 02（02-sync.md T7、LM12）；本模块保证 `RowGuard` 是 storage 代码拿行锁的唯一入口。
- **S11 身份检查（新增）**：每个 `RowRef`/`NodeRef`/`MvccAddr` 带 `arena_id`；链接原语、`set_index_node`、`AllocHandle::row/node` 要求身份一致，不一致时 `ob_assert!`（记 ERROR 和调用栈后 abort）；`try_row/try_node` 返回 `OB_ERR_UNEXPECTED`。只有翻译错误才会触发。
- **S12 `is_safe_read_barrier`** 普通读 `flag_`（`MVR.cpp:97`）→ `load(Acquire)`。FIXED(port)。
- **S13 快照屏障**：`version | flag` 存进一个 i64，高 2 位是标志（`MVR.h:231-234`、`MVR.cpp:102-114`），SeqCst。
- **S14 行元数据**：`update_max_trans_version` 先 `inc_update`，再在"新值等于入参"时写 `max_trans_id_`（`MVR.cpp:729-741`）；`update_dml_flag_` 在 `DF_LOCK` 时什么也不做（`:778-791`）；`get_max_trans_version` 取两者最大（`:722-727`）。
- **S15 回放索引**：数组长 32；`set_index_node` 越界静默忽略（`MVR.cpp:332-352`）；`reset` 只在非空时清零（`:324-330`）；索引从调用方给的分配器分配（`:661-670`，调用方传 `memtable_->get_allocator()`，`CB.cpp:1938`）。
- **S16 行锁持有约定**：C++ 里在持锁状态下调用、Rust 里改成接收 `&mut RowGuard` 的函数：`unlink_trans_node`、`insert_trans_node`、`trans_commit`（行）、`elr`、`remove_callback`（行）、`row_compact`/`ObMemtable::row_compact`/`ObMemtableRowCompactor`（`CB.cpp:1754`→`:1778-1785`、`ENG:96-99`）、`cleanout_tx_node(…, false)` 的全部调用方（`MVR.cpp:883,1150`、`RC:188`、`MVI:572`）。`ObCleanoutTxNodeOperation` 的 `need_row_latch_: bool`（`TXF.h:89-99`）改成 `latched_: Option<&mut RowGuard>`；`need_row_latch_ == true` 时它自己 `lock()`（`TXF:513-548`，见 6 例 3）。沿途的 `cleanout_tx_node(…, need_row_latch)`（`storage/tx_table/ob_tx_table_guards.h:130`、`ob_tx_table_interface.h:91`、`ob_tx_table.h:265`）同样把最后一个参数改成 `row_guard: Option<&mut RowGuard<'m>>`（`None` 对应 `true`）。

### 3.2 容器与 pin

- **S17 释放协议（新增）**：`release`/`Drop` 先把 `state_` 置 RELEASING（SeqCst），再求 16 个分片之和；和为 0 → 置 DESTROYED、析构 T、释放；否则 → 置回 NORMAL、记 ERROR（`"memtable released with live callbacks"`，含 `memtable_id` 和计数）、延后。`register_callback`：分片 `fetch_add(1, SeqCst)` 后读 `state_`：RELEASING → 撤销并自旋等待；DESTROYED → 撤销并返回 `OB_STATE_NOT_MATCH`。pin 在创建它的分片上减 1，所以并发创建与求和不会漏数（证明见 4 U7；loom L3）。
- **S18 与 C++ 的差异**：C++ 没有这个检查，"先摘回调再销毁"完全靠流程（`SMM:583-617`）。已知的流程缺口：`ObTransService::remove_callback_for_uncommited_txn` 在服务未运行时返回 `OB_SUCCESS`（`storage/tx/ob_trans_service.cpp:342-343`），而调用方期望 `OB_NOT_RUNNING`（`SMM:593`）→ C++ 会在仍有回调时销毁 memtable；Rust 版在这里延后释放并记 ERROR。这是 RULEBOOK 0.9"协议错误最多变成延迟释放"的预期效果，不标 `BUG(port)`。
- **S19 `memtable_id`** 替代按地址作键的 `ObMemtableSet`（`hash::ObHashSet<uint64_t>`，`TCL.cpp:444`、`CB.cpp:667-669`）；回调的 `get_memtable()` 比较改成比较 id。

### 3.3 memtable arena

- **S20 `ObFifoArena::alloc`**（`FA.cpp:92-150`）逐行移植：整段在 `Quiescence` 临界区内（`:98`）；`size + 72 > ALLOC_PAGE_SIZE` 走大页路径，分配专用页后立即 `frozen()` 并 `retire_page`（`:105-115`）；否则在 `cur_pages_[grp%16*32 + way]` 上 bump，第一次在某页分配时在页内放一个 24 字节的 `Ref` 头（`FA.h:132-142`，持有 `Handle::lock_`，`FA.cpp:125-128`）；换页时旧页的 `self_ref_` 记到当前句柄上（`:131-136`）；装页 CAS 失败就 `destroy_page`（`:141-145`）；不设重试上限。返回 None 时调用方报 `OB_ALLOCATE_MEMORY_FAILED`（`ENG:456,460`、`MT.cpp:2516`、`KB.cpp:1309`）。
- **S21 整块释放**：页按字节计数归还：每个 `Ref` 记本句柄在该页拿走的字节（含 `Ref` 自身），`self_ref_` 记负的总字节，`release_ref` 把它们加回 `Page::ref_`，回到 0 才 `free_page`（`FA.h:44-97`、`FA.cpp:152-157`）。`free(handle)` 先 `Quiescence::sync()` 一次再逐个 `release_ref`（`:159-175`）。所以一页的内存要等它被封口、且所有在其上分配过的 memtable 都释放后才归还（同 C++）。
- **S22 计数器**：`allocated_/hold_/reclaimed_/retired_` 的增减点照 `FA.cpp:177-218`；`total_hold_`、`last_reclaimed_`、`lastest_memstore_threshold_`、`set_memstore_threshold` 是死代码，不移植。页内存来自全局分配器（RULEBOOK 0.7 的 jemalloc 直通）；C++ 的 `ObMemAttr` 标签 `OB_MEMSTORE`（`FA.cpp:44`）删掉。
- **S23 `ObMemstoreAllocator::alloc`**（`MF:1659-1703`）：大小上取整到 8（`:1662`）；句柄还没有 id 时，持 `lock_` 二次检查：已冻结 → `OB_EAGAIN`（memtable 未下线记 ERROR，否则 WARN），否则 `set_clock(arena_.retired())` + `hlist_.set_active`（`:1664-1678`）；然后调钩子 `check_memstore_full_internal`，失败或已满 → 返回 None，每秒最多一条 WARN（`:1679-1692`）；再调钩子 `alloc_resource`（不睡眠，`:1695-1698`）；最后 `arena_.alloc(is_inner_tablet ? 0 : id, …)`（`:1699-1700`）。
- **S24 `AllocHandle::free`** 是空操作（`MA.h:117-119`），memstore 没有逐对象释放（`ENG:452` 注释）。`destroy()` 之后再分配一律失败（C++ `host_ = NULL`，`MA.h:92-97,109-111`）。内存在最后一份 clone 析构时归还（`arena_.free` + 摘出句柄表，表空则 `arena_.reset()`，`MA.cpp:62-78`）；正常流程里这一刻就是 t3m 释放 memtable 的时刻（`SMM:617` → `MT.cpp:184-215`），与 C++ 相同，前提是 keybtree 先释放了它持有的 clone（3.5 S33）。
- **S25 nway**：只有 `set_nway_per_group(1)` 一个调用（`MA.cpp:47`），way id 恒为 0；缩小 nway 的路径（`FA.cpp:63-69`）照样移植，不另加测试以外的保证。
- **S26 句柄表**：`init_handle` 头插进总表；第一次分配时 `set_active` 按 clock 非增插入活跃表并重算 hazard；`set_frozen` 从活跃表摘出并重算（`HL.cpp:23-69`）。全部在 `ObMemstoreAllocator::lock_` 下（`MA.cpp:52-85`）。`for_each` 持锁遍历，`reverse=true` 从最老的开始（`MA.h:142-151,173-186`）。
- **S27 节点内存的分配序列**与 C++ 一致（节点、行、键、行索引、btree 节点块都从同一句柄分配），但键在 Rust 里的布局由 storage 的 `ObStoreRowkey`/`ObObj` 视图决定，字节数可能不同；memstore 用量只在相关虚拟表和冻结时机上可见，不在裁判比较范围内（记录在此，不另设 A.4 项）。

### 3.4 回收原语

- **S28 QClock**（`RS.h:30-113`）逐行移植：`enter_critical` 从 `get_itid()` 号槽开始 CAS `u64::MAX → clock_`，失败就 `sched_yield` 换下一槽（`:45-58`，"槽不够"的 ERROR 日志条件与 C++ 相同、实际不会触发）；`is_quiescent` 的 `qclock_` 缓存是普通 store、可能回退（`:85-98`），照抄。槽数 `OB_MAX_CPU_NUM * 4`。
- **S29 RetireStation**（`RS.h:194-263`）：按 `get_itid() % (OB_MAX_CPU_NUM * 32)` 选槽（x86-64 2048、aarch64 4096；RULEBOOK 0.8 写的 4096 只对 aarch64 成立，修订 A3）；槽内 TAS 自旋锁 + `sched_yield`（`:200-211`）；`prepare_list_` 超过上限时 `wait_quiescent` 只等一个宽限期，再把 `retire_list_` 交给调用方回收（`:221-224`）；否则每 64 次（`size % 64 == 63`）试一次 `try_quiescent`（`:226-229`）；`purge` 对全部槽做两遍强制回收（`:248-255`）。
- **S30 不等读者的承诺的边界**：上面两处 `wait_quiescent` 是 C++ 里仅有的等待，等的是 QClock 临界区（keybtree 的读写句柄、KV store 的 `mb_list_` 遍历），不等 hazard pointer 持有者、不等 map 读者。Rust 版照样保留这两处等待（修订 A9 把 RULEBOOK 0.8 的措辞说清）。

### 3.5 keybtree

- **S31 常量与布局**：`NODE_KEY_COUNT=15`、`NODE_COUNT_PER_ALLOC=128`、`MAX_CPU_NUM=64`、`RETIRE_LIMIT=1024`（`KD.h:38-50`）；节点 = `ObLink` 8 + `host_` 8 + `level_` 2 + magic 2（0xb7ee）+ `RWLock` 4 + `MultibitSet` 8 + `kvs_[15]`（`KD.h:333-342`），memtable 实例化 272 字节，DDL 实例化（键 16 字节）392 字节；`Path::MAX_DEPTH=16`（`KD.h:352`），超深返回 `OB_ARRAY_OUT_OF_RANGE`（`KB.cpp:188-200`）；`WeightEstimate` 按 `NODE_KEY_COUNT/2` 的幂（`KD.h:161-180`）。
- **S32 写路径**：`insert`/`insert_or_get` 先进 QClock 临界区，`OB_EAGAIN` 循环里每次失败 `free_list()`（复用被写锁住的旧节点、释放新分配的节点），**先** `release_ref` 离开临界区、**再** `retire`（`KB.cpp:1557-1636`）；`retire` 在 `Quiescence` 临界区内调 `RetireStation::retire`，回收到的节点按各自的 `host_` 还给各自树的空闲表（`:1707-1729`）。写锁 uid = `(uint16_t)(get_itid() + 1)`（`KB.cpp:717`），照抄回绕。
- **S33 销毁**：非批量 `destroy(false)`：摘根、递归把可达节点直接还给空闲表、在 `Quiescence` 临界区内 `purge` 并释放回收表、出临界区后 `sync()`（`KB.cpp:1531-1554`）。批量：每棵树 `pre_batch_destroy`（只摘根和递归释放，`:1502-1507`），然后静态 `batch_destroy` 做一次 `purge` + `sync()`（`:1510-1528`，`SMM:528-536`）。Rust 版用域上的 `purge_started_/purge_completed_` 记录：`pre_batch_destroy` 记下"下一次开始的 purge 号"，树析构（或 `destroy(true)`）时若那次 purge 还没完成就自己补做一次非批量 purge（新增，正常流程不触发）；完成后才释放树持有的 `AllocHandle` clone。这样 arena 一定活得比回收站里该树的节点长。
- **S34 扫描**：`BtreeIterator` 只在 `scan_batch` 里进临界区，最多取 225 个 KV 进队列，然后 `iter_->reset()` 离开；下一批从最后一个键（`start_exclude=true`）重新从根往下找（`KB.h:143-147`、`KB.cpp:1212-1244`）。`find_path` 失败时 `scan_batch` 不 reset，槽位要到 `BtreeIterator::reset`/析构才释放（`KB.cpp:1217-1221,1163-1165`），照抄。`BtreeRawIterator` 进入一次、直到 reset 或析构（`KB.cpp:1325-1343`）。
- **S35 键与值的生命期**：树里的键和值一律是 `with_arena`/树内部的 `ArenaCap<'b>` 构造的，或是 'static 数据（HRTB 保证，4 U8）；树持有 `AllocHandle`（或私有 arena），所以从 `&'t ObKeyBtree` 读出的键值引用在 `'t` 内有效。迭代器缓存的 225 个 KV 在离开临界区后仍有效，因为它们指向 arena，不指向 btree 节点（C++ 同理）。
- **S36 `insert_or_get_row`**：键已存在 → `stored_key` 是树里那份键（C++ 创建器的 `is_exist_key` 分支：`stored_key->encode(...)`，`ENG:448-450`）；不存在 → 至多一次 `K::dup_key`、至多一次分配行并 `set_btree_indexed`，候选在 `OB_EAGAIN` 重试之间复用（`ENG:452-468`）；键被并发插入时候选行不再使用、也不归还（同 C++）。`OB_ALLOCATE_MEMORY_FAILED` 记 WARN，其他错误记 ERROR（`KB.cpp:1628-1633`）。
- **S37 切分与估算**：`update_split_info`（`KB.cpp:1732-1740`）、`split_range`/`estimate_key_count`/`estimate_element_count`（`:1357-1478`，`MAX_SAMPLE_LEAF_COUNT=500`，批大小 1→1024、64→1024，`:956-984`）逐行移植；它们决定并行扫描的切分和优化器的行数估计（`MT.cpp:1739`、`QE.cpp:434,494,617`），由金标准测试 G1 覆盖。
- **S38 空闲表**：64 条，按线程局部计数轮转（初值 `icpu_id()`，`KB.h:117-133`）；每次从来源要 128 个节点，第一个给调用方，其余按 `KB.cpp:1294-1306` 的公式分到各表；表锁是"把 tail 换成 `~0` 当锁"加 `sched_yield`（`KB.cpp:1250-1274`）。`alloc_memory_` 从不增加（只出现在 `KB.h:92,94,114`），`get_allocated()` 恒为 `size_of` 常数，进 `__all_virtual_memstore_info`（`ob_all_virtual_memstore_info.cpp:214`），照抄。
- **S56 节点锁**（`KD.h:61-104`）：C++ 用 union 让 `read_ref_: int16`（低 16 位）、`writer_id_: uint16`（高 16 位）和 `lock_: uint32` 重叠，并在三者上混用 16 位和 32 位原子操作；Rust 不允许大小不同的原子访问重叠（02-sync.md O11 同此结论），改成一个 `AtomicU32` 上的位运算，全部 SeqCst：`try_rdlock` = 读整字、高 16 位非 0 失败、否则 `CAS(lock, lock + 2)`（`:66-75`）；`try_wrlock(uid)` 的 16 位 `BCAS(&writer_id_, 0, uid)` 改成"高 16 位为 0 时 CAS 把高半字设成 `uid`，只因低半字变化而失败就重试"，等价于原来不受低半字影响的 16 位 CAS；失败后低半字最低位（spin 标记）为 1 则继续抢，否则返回 false；抢到后等低半字 `<= 1`，期间 `sched_yield`（`:77-92`）；`rdunlock` = `fetch_sub(2)`（持读锁时低半字 ≥ 2，不会借位，`:93`）；`set_spin` 的 16 位 `AAF(1)` 用 CAS 循环做低半字的 16 位回绕加法（`:65`）；`wrunlock` = `store(0)`，连 spin 标记一起清（`:94`），照抄；`is_hold_wrlock(uid)` = 高 16 位等于 `uid`。loom L5 的第三个用例覆盖。

### 3.6 事务回调链表

- **S39 锁模式**（`TCL.cpp:980-1052`）：`LOCK_ALL` = 先 iterate 再 append；`lock_iterate_` 在"列表不是只追加"时连带拿 append；`TRY_*` 用 try_lock；guard 析构先放 append 再放 iterate。三把锁都是 02 的 `bitlock::ObByteLock`：前 501 次尝试每次失败 PAUSE，第 502 次失败第一次 `usleep(100)`，此后每 501 次尝试睡一次（`ob_small_spin_lock.h:126-165`，§2.7、审查 F10；02-sync.md T11 测这个计数）。C++ 在 `LOCK_ALL` 两次加锁之间 `tg->click()`（`:989-991`），只影响耗时日志，Rust 版不传 `ObTimeGuard`。
- **S40 追加的发布顺序**：新节点先设好自己的 prev/next，再改后继的 prev，最后改前驱的 next（`CB.cpp:132-147`）→ 最后一步 Release，正向遍历者用 Acquire 读 next，看到新节点时它已完整链好。`append_callback` 在 append 锁内先调全部 `before_append_cb`、失败时对已成功的逆序调 `after_append_fail_cb`（`TCL.cpp:155-262`），这部分是 storage 的策略，用 `lock(LOCK_APPEND)` + `append_after/append_chain_after` 写。
- **S41 摘链与释放（新增的安全规则）**：C++ 摘链（`CB.cpp:149-161`）后立刻按类型释放（`TCL.cpp:358-366`），正确性靠"填日志只遍历游标之后、摘未写日志的节点时 TxCtx 持排他锁"（`TCL.cpp:545-549` 注释）。Rust 版不依赖 TxCtx：`unlink` 要求 guard 持 iterate 锁（全部 6 个会摘链的 functor 都在 iterate 锁下运行：`TCL.cpp:396,434,497,607,621,692,710,792`）；摘尾节点时若 guard 没有 append 锁，核心临时取（C++ 在 `next == end` 时临时加 append 锁，`:334-339`，条件比"尾节点"宽，多加的锁不影响可观察行为）；被摘节点的链接字段保持原样（同 C++，让正在它上面的遍历者能继续走），节点的 Arc 先挂在 guard 上、guard 释放时归还；如果被摘节点的追加序号 ≥ 当前日志游标的序号（未写日志），就再延后到下一次 `try_lock_log()` 成功时归还（那时没有填日志的遍历者）。所有游标都借用某个 guard，所以不会悬垂；跨调用的位置用 `TxCallbackPos`（持 Arc）。可观察行为与 C++ 相同，只是内存晚一点归还。
- **S42 日志游标**：`log_cursor_ == &head_` 表示没有未写日志的回调；追加时游标在哨兵上就移到新节点（`TCL.cpp:106,132-134,192,228-230`）；摘到游标节点时游标移到下一个（`:347-349`）；`submit_log_succ` 在 log 锁下前移，下一个是哨兵时再拿 append 锁（`:565-584`）。游标和它的序号、epoch 用原子量，单写者由上述锁保证。
- **S43 计数器**：`appended_`、`logged_`、`synced_` 等在 C++ 里有的是普通读写、有的用 `ATOMIC_*`（如 `synced_ += sync_cnt` 不持任何锁，`TCL.cpp:592`；`logged_` 用 `ATOMIC_AAF`，`:581`）。它们在 storage 的 `ObTxCallbackList` 里，按 RULEBOOK §2.7 改原子量（FIXED(port) 的数据竞争由清单第 8 类逐个列出），不在核心。

### 3.7 palf

- **S44 组缓冲的存储（偏离 RULEBOOK 0.9 的写法，Q4/修订 A6）**：`read_data` 先 `memcpy` 再复查 `readable_begin_lsn_`，复查失败就丢弃结果（`LGB.cpp:461-477`）——它在设计上就允许与 `fill` 并发读写同一段字节，这在 Rust 内存模型里即使写是不相交的也是数据竞争。所以存储用 `[AtomicU64]`：整字用 Relaxed load/store，首尾不满一字的部分用 `fetch_update` 拼字节；全部读写都不需要 unsafe。复查要靠两道栅栏才成立（seqlock 的标准写法）：`fill`/`fill_padding_body` 在 `inc_update_readable_begin_lsn_` 之后、写数据之前 `fence(Release)`（`LGB.cpp:223-226,263-266`）；`read_data` 在拷完数据之后、复查 `readable_begin_lsn_` 之前 `fence(Acquire)`（`:469-471`）。这样读者只要读到了新写的任何一个字，复查就一定看到推进后的 `readable_begin_lsn_` 并丢弃结果。C++ 没有这两道栅栏（`memcpy` 本身就是数据竞争；在 x86 上靠 TSO 成立，在 aarch64 上没有保证，见 8.2 B6）。可观察行为不变（复查逻辑照抄），由 loom L8 覆盖。FIXED(port)。
- **S45 组缓冲的其余语义**照 C++：位置 = `(lsn - start_lsn) % reserved`（`:117-122`），绕回时分两段拷（`:483-497`）；`fill` 的检查顺序与返回码（`OB_NOT_INIT`、`OB_INVALID_ARGUMENT`、`OB_ERR_UNEXPECTED`、`OB_EAGAIN`，`:206-221`）；先 `inc_update` 可读起点再拷（`:223-226`）；`fill_padding_body` 先用 `PADDING_LOG_CONTENT_CHAR` 填满再拷有效部分（`:266-277`）；`truncate` 与 `read_data` 互斥（`truncate_lock_`，`:420,444`）；`reuse_lsn_` 只增（CAS 循环，`:394-400`）；`activate` 依赖调用方持 palf 写锁（`:329`）。`is_inited_` 在 C++ 是普通 bool、被并发读 → `AtomicBool`。`get_log_buf` 返回的片段持有存储的 Arc，`destroy` 之后旧片段仍然有效。
- **S46 LSN 分配器**：状态是 128 位字：低 64 位从最低位起依次是 `is_need_cut_:1`、`log_id_delta_:28`、`scn_delta_:35`，高 64 位是 `lsn_val_`（`LA.h:91-101`，按 Clang 小端位域规则）；21 处 `CAS128/LOAD128`（`LA.cpp:89-500`）一律译成 02 的 `cas128(&self.lsn_ts_meta_, &mut cmp, with)`/`load128(&self.lsn_ts_meta_)`（SeqCst；失败时把 `cmp` 改成当前值，同 C++，02-sync.md A2）；`uint128_t` 与位域之间的转换由本模块的私有函数做。读锁持有者之间用 CAS 竞争；写锁持有者同时改 `log_id_base_/scn_base_` 并用 CAS 把增量清零，所以写锁下的 CAS 失败返回 `OB_ERR_UNEXPECTED`（`:131-136,360-365`）；`get_curr_end_lsn` 不取锁（`:238-249`）。重定基阈值 `(1<<28)-1000`、`(1<<35)-1000`（`LA.h:84-85`），2 MB 切分（`:86-87`），文件尾不足 4 KB 补 padding（`LA.cpp:433-471`）全部照抄，由 G2 覆盖。`lock_` 是 `common::RWLock`（即 TCRWLock，`LA.h:103`）→ `BigReaderLatch`；`log_id_base_`、`scn_base_` 放进锁内（写锁下写、读锁下读，§2.7 规则 1）。

### 3.8 KV cache

- **S47 退休条件的修复（FIXED(port)，RULEBOOK 0.8、复核 C06）**：
  - C++ `ObKVCacheHazardSlot::retire(version)`（`HV.cpp:75-104`）拿到 `is_retiring_` 后，用 CAS 把整条 `delete_list_` 摘下，然后对表上**每个**节点调 `retire()` 释放；入参 `version` 只用来更新 `last_retire_version_`（`:84-86`）。头文件注释写的是"Retire nodes in delete_list whose version is less than version"（`HV.h:54`）。
  - 正确条件：**节点 N 可以释放，当且仅当 `N.version_ < min_version`**，其中 `min_version = get_min_version()` = `min(version_, 所有槽的 acquired_version_)`（`HV.cpp:325-336`），调用方在 `release` 时（`:253-255`）或全局 `retire` 开头（`:271-274`）算出。
  - 理由：删除方先在桶写锁下把 N 从桶里摘掉，再 `N.version_ = FAA(version_)`（`HV.cpp:197`）；读者进入时把当时的 `version_` 写进自己的槽（`:221`），之后才遍历桶。能遍历到 N 的读者必然在摘链之前进入，它记下的版本 ≤ `N.version_`，所以只要它不退出，`min_version ≤ N.version_`，N 不会被释放。读者"先读 `version_` 再 CAS 槽"两步之间若有人算 `min_version`，此时看不到它的槽；但它在 CAS 之后才开始遍历，那时已经摘掉的节点它够不着，之后才摘掉的节点版本都不小于它读到的值，仍受它的槽保护。按读者实际能够到的节点算，`min_version` 随时间单调不减，所以用稍早算出的值只会更保守。
  - Rust 版：摘下整表后逐个比较，`version_ < version` 的释放并计数，其余按原顺序用 `add_nodes`（`HV.cpp:106-122`）挂回；`waiting_nodes_count_` 只减去释放的个数；`last_retire_version_` 的更新照旧（只用于 `print_current_status`）。C++ 读 `version_` 是普通读（`:221`），改原子读（FIXED(port)，数据竞争）。
  - 代价：长时间持有 hazard guard 的扫描（`clean_garbage_node` 一次最多 20 万桶，`MP.cpp:511-574`）期间被删的节点要等扫描结束才释放；C++ 修复前是立即释放（错误）。属于预期。
- **S48 hazard version 的其余语义**：1024 个槽、阈值 512（`MP.h:38-39`）；`acquire` 从 `get_itid() % slot_num` 起找空槽，转满 3 圈失败返回 `OB_ENTRY_NOT_EXIST`（`HV.cpp:209-238`）——这个错误从 `get` 漏出来像未命中、`erase` 把它变成成功，照抄；`delete_node` 要求 `hazard_next_` 为空，否则 `ob_abort`（`:198-201`）。
- **S49 hazard pointer**：`protect(mb, seq)`：先比较 seq（C++ 普通读，`SR.h:132`，改 Acquire）、Relaxed 发布、`fence(SeqCst)`、Relaxed 复读 seq（`HP.cpp:24-38`）；`protect(mb)` 同理比较 `status_ != FREE`（`:40-54`）；`reclaim` 交换出退休表，在 `bit_set_mu_` 下扫描全部 hazard pointer 建位图，释放未被保护的块、其余挂回，从不等待（`HD.h:369-422`）。Rust 版在扫描之前显式 `fence(SeqCst)`（C++ 依赖 TAS 的全屏障，`:378-379` 注释掉了 `MEM_BARRIER`），扫描用 Relaxed 原子读（C++ 普通读 `mb_handle_`）。块的 `retire()`：`FULL→FREE` CAS 后 seq 加 1 再挂退休表（`SR.cpp:307-317`）；块句柄复用时不重置 seq（`SR.cpp:264-276`）。
- **S50 map 节点只存块 id 和 seq**：`Node` 去掉 `mb_handle_`、`key_`、`value_` 三个指针，改存 `mb_index_`（`handle_index_of`，`ST.cpp:37-40`）和 pair 在块内的偏移 `pair_off_`（键长、值长在 pair 头里）；命中路径先 `protect(mb_handles_[idx], seq)` 再读键字节比较，比较不等就释放保护（`MP.cpp:286-311`）。`seq_num_` 保持 i32（`MP.h:81`），与块的 i64 seq 比较时符号扩展，照抄 C++ 的截断行为。节点比 C++ 的 88 字节小（去掉 vptr 和三个指针，改存块号和偏移），只影响 `get_managed_used`（`MP.h:66-73`）这类内部统计，属于 A.4 `mem-view`。块内的 pair 头保持 24 字节（`magic_`、`size_`、键长、值长，替代两个指针，SR.h:64-76），`get_align_size` 的公式不变（`SR.cpp:111-114`），所以 `store_size`、每块能放的键值数与 C++ 一致（G4）。
- **S51 put/erase/wash 不等读者**：put、overwrite、erase、`internal_data_move` 都只是在桶写锁 + hazard guard 下摘节点并 `delete_node`（`MP.cpp:152-259,356-413,734-791`）；wash 只退休块、交给 hazard pointer 回收（`ST.cpp:358-463`）。`clean_garbage_node` 和 `erase_all` 照 C++ 每次调用进入一次 hazard guard、扫完本批桶再退出（`MP.cpp:415-457,459-509,511-574`；批大小由调用方给，`ob_kv_storecache.cpp:529,544` 为 20 万和 10 万）。
- **S52 返回码与边角**：`put_and_fetch`/`put_kvpair` 在 `overwrite=false` 且已存在时返回 `OB_ENTRY_EXIST`，此时 handle 仍然有效、指向已有值或刚存入的孤儿副本（`KS.cpp:314-341`）；overwrite 时旧节点被删但旧块的 `kv_cnt_/get_cnt_` 不减（`MP.cpp:249-251`，对比 `erase` 的 `:389-390`），照抄（`BUG(port)` 候选，见 8.2）；`get_hit_rate` 恒返回 0.8（`KS.h:469-472`）；`set_mem_limit_pct` 只有声明，不移植。
- **S53 handle 跨线程**：C++ handle 会随异步 I/O 回调、schema guard 跨线程移动（`ob_micro_block_cache.h:165,327`、`ob_schema_getter_guard.cpp:2265-2290`），所以 `ObKVCacheHandle`/`KvBlockHandle`/`ObKVCachePair` 是 Send。hazard pointer 槽不绑定线程（`HD.cpp:144-270`：释放时归还到当前线程的缓存），不属于 RULEBOOK §3 说的"临界区令牌"；`ObKVCacheHazardGuard`、`QClockGuard` 是令牌，!Send。
- **S54 块内存**：C++ `ObKVCacheStore::alloc_mb/free_mb`（`ST.cpp:1142-1165`）经 `ObResourceMgrHandle` 调 `ObMemoryMgr::alloc_cache_mb/free_cache_mb`，jemalloc 后端下就是 `jemalloc_malloc(size)`/`jemalloc_free`（`ob_resource_mgr.cpp:110-144`）。01 删除 `ObResourceMgr`/`ObMemoryMgr`，Rust 版在 `ob_kvcache_store.rs` 内直接 `std::alloc::alloc(Layout::from_size_align(block_size, 16))`（全局分配器即 jemalloc，`align <= 16 && align <= size` 时就是 `je_malloc(size)`，01-mem.md S17），返回空指针时走 C++ 的 `OB_ALLOCATE_MEMORY_FAILED` 分支（`ST.cpp:1150-1152`）；`free_mb` 用 `dealloc`。两个函数去掉 `resource_handle` 参数，`block_size <= 0 || block_size < block_size_` 的 `OB_INVALID_ARGUMENT` 检查保留（`:1147-1149`）。块内所有切片都从这个原始指针派生（U11）。
- **S55 map 节点的分配**：C++ 从 `ObLfFIFOAllocator node_allocator_`（`MP.h:129`，`MP.cpp:64-66` 按 `OB_MALLOC_MIDDLE_BLOCK_SIZE` 分页）分配，所有 inst 共用这一个（`ob_kvcache_inst_map.cpp:203`）。RULEBOOK 0.9 首版去掉对象池，01 把这类池改成逐个 `try_box`（01-mem.md 的池行），所以 Rust 版每个节点 `try_box` 后转原始指针挂桶链，在 S47 的条件成立后 `Box::from_raw` 释放；分配失败走 C++ 的 `OB_ALLOCATE_MEMORY_FAILED` 分支（`MP.cpp:217-219`、`:748-750`）。`node_allocator_.allocated()` 的两处读数（`MP.h:66-73`、`ob_kvcache_inst_map.cpp:369-370`）改读 map 里的一个 `AtomicI64`（活节点数 × `size_of::<Node>()`）。`is_fragment(node)`（`MP.cpp:193,385,616`）恒为 false：`internal_map_replace` 不再触发，`replace_fragment_node` 的定时任务（`KVCacheRep`，`ob_kv_storecache.cpp:215,538-548`）照样扫描、不替换。标 `DIVERGE(port): mem-view`；A.4 的 `mem-view` 目前只写了视图内容，这里要它覆盖"只影响内存占用的内部整理"（修订 A10）。

### 3.9 偏差汇总

| # | 位置 | 类别 | 一句话 |
|---|---|---|---|
| S4 | `MVR.cpp:705` | FIXED(port) | ELR 普通写版本 → 原子写 |
| S5 | `MVR.cpp:975-979,733`；`CB.cpp:1907`；`CB.h:481` | FIXED(port) | 发布后写的字段改原子量 |
| S6 | `MVR.h:97-99` | FIXED(port) | 载荷头从偏移 90 挪到 96，分配大小不变 |
| S7 | `RC:329-350` | FIXED(port)，新疑似 bug | 压缩节点未初始化的两个字段按构造默认值初始化 |
| S8 | `MT.cpp:2577` + `MVR.cpp:1036-1098` | FIXED(port)，新疑似 bug | 链入过的批量槽位不再复用 |
| S11 | 新增 | 新增保护 | arena 身份检查 |
| S12 | `MVR.cpp:97` | FIXED(port) | 普通读标志改 Acquire |
| S17/S18 | 新增 | 新增保护 | 有 pin 时拒绝释放 memtable |
| S41 | `TCL.cpp:358-366` | 新增保护 | 被摘回调延后释放 |
| S44 | `LGB.cpp:223-226,461-477` | FIXED(port)，新疑似 bug（aarch64） | 组缓冲改原子字存储，读写两侧加栅栏 |
| S47 | `HV.cpp:75-104` | FIXED(port)，RULEBOOK 已列 | 退休前比较 `min_version` |
| S49 | `SR.h:132`、`HD.h:378-379` | FIXED(port) | seq 和 hazard 槽的普通读改原子读，回收前加全屏障 |
| S50 | `MP.h:77-103` | A.4 `mem-view` | map 节点存块号和偏移，节点变小，只影响内部统计 |
| S55 | `MP.h:129`、`MP.cpp:193,385,616` | A.4 `mem-view`（待修订 A10） | map 节点逐个分配，不做碎片搬迁 |

## 4. unsafe

全部在 §3 白名单内（第 10 项 `rust/port/storage_core/**`，第 11 项 1.1 表 #16–#22，第 2 项 `oblib/src/sync/ob_retire_station.rs`）。对外的函数全部安全；`unsafe fn` 只有 2.5 的 `HazardList::push`、`RetireStation::retire*` 和 2.9 内部的 `delete_node`，调用方都在白名单模块里。

| # | 位置 | 做什么 | 安全论证要点 |
|---|---|---|---|
| U1 | `NodeRef/RowRef` 的 `Deref`、`prev/next` | 原始指针 → `&'m T` | 节点和行只在 arena 释放时整体消失（`ENG:452`、`MA.h:117-119`）；`'m` 来自对 `AllocHandle`/memtable/pin 的借用，期间 arena 不会释放（2.2、2.3）；链接只能指向同一 arena（U6） |
| U2 | `get_mtd` | 从分配基址派生载荷切片 | 基址指针的来源是整块分配（`size_of::<Node>() + dup_size()`）；切片在 `[96, 96+16+buf_len)` 内；载荷在封口后不再写（只有 `NodeMut` 能写） |
| U3 | `ObFifoArena` 页与 `Ref` | 页内 bump、页头读写、按字节计数释放页 | 页内偏移由原子 FAA 分配、互不重叠（`FA.h:69-82`）；页只在所有 `Ref` 归还且封口后释放（`FA.cpp:152-157`），归还前 `Quiescence::sync()` 等待所有正在 `alloc` 的线程离开（`FA.cpp:98,166`） |
| U4 | `NodeMut` 的 `DerefMut`、`NodeBatch` | 对未发布节点的 `&mut` | 节点刚分配或刚从批里切出，地址尚未出现在任何链接、`MvccAddr`、`NodeRef` 里；`into_ref` 消耗 `NodeMut` |
| U5 | `ArenaCap::alloc*` | 在新内存上 `ptr::write` 后给出 `&'b T` | 内存是本次新分配、对齐 ≤ 8（编译期断言）；`T: Copy` 所以无需 Drop；`'b` 是生成型生命周期，结果只能逃出 `with_arena` 的闭包到 `'static` 的位置——不可能，所以只能被存进同一 arena 的结构（树） |
| U6 | 链接原语、`set_index_node`、`AllocHandle::row/node` | 比较 `arena_id` 后存指针/解引用 | arena id 进程内唯一、永不复用（全局 `AtomicU64` 发号），所以 id 相等意味着同一个仍然存活的 arena |
| U7 | `MemtableRef/Pin/Self` | 堆上容器的原始指针；`unsafe impl Send/Sync`（`T: Send + Sync`） | 容器只在 `release` 判定 pin 和为 0 后释放：创建方"先加分片再读状态"、释放方"先置状态再求和"，SeqCst 下两者至少有一方看到对方（Dekker）；pin 在自己的分片上减，求和期间新 pin 只能由已有 pin 的持有者创建，这时旧 pin 尚未减掉；`MemtableSelf` 存在 T 里，T 析构时它随之析构，不会被解引用 |
| U8 | keybtree 节点、写时复制、键值擦除 | 原始节点指针；把 `K::Key<'b>` 存成定长字节、读出时按调用方的 `'t` 复原 | 节点只经 `QClock` 临界区 + `RetireStation` 回收（照 C++）；树持有 `AllocHandle` clone，析构前保证回收站里已没有本树节点（S33）；键值只能来自 HRTB 的 `ArenaCap` 或 'static（U5），所以在树存活期间有效；`shorten` 只缩短生命周期 |
| U9 | `QClock`、`RetireStation`、`HazardList` | 侵入式链表、跨线程交出节点 | 槽内表只在槽锁下访问；`retire` 的契约由调用方（keybtree、KV store）保证"已不可达"；回收只在一个宽限期之后（`RS.h:221-229`） |
| U10 | 回调链表 | Arc 原始指针做链接；游标解引用；延后释放 | 节点内存由 Arc 管理；链表持有的那一份只在 guard 释放（或下一次拿到日志锁）时归还，所有游标借用 guard，所以不会悬垂；摘链要求 iterate 锁，追加要求 append 锁（S41） |
| U11 | KV cache：块缓冲、键值切片、map 节点、hazard 节点、块句柄链表 | 原始块内存 → `&[u8]`；Box 原始指针做桶链；节点延后释放；`mb_list_` 的 `dl_*` | 块句柄链表只在 QClock 临界区内遍历和修改，摘下的句柄经 store 的 RetireStation 等一个宽限期才复用（`ST.cpp:1168-1242`）；块只在 hazard pointer 扫描确认未被保护后释放（S49）；切片只覆盖已发布的那对键值（发布前只有 `ObKVCachePair` 的 `&mut`）；map 节点只在 S47 的版本条件成立后释放 |
| U12 | `ObKVCacheHandle`/`KvBlockHandle`/`ObKVCachePair` 的 `unsafe impl Send` | 跨线程移动保护 | 保护对象不绑定线程（S53）；这几个类型不含临界区令牌 |

不需要 unsafe 的：`LogGroupBuffer`（全部原子量）、`LSNAllocator`（`oblib::atomic::AtomicU128`）、`ObHandleList`（锁内 `Vec<Weak<_>>`）、`ObKVCacheInstMap`。

`unsafe impl Send/Sync` 的完整清单：`MemtableRef`、`MemtablePin`、`MemtableSelf`、`NodeRef`、`RowRef`、`MvccAddr`、`ObKeyBtree`、`BtreeIterator`（Send：不含临界区）、`TxCallbackListCore`、`TxCallbackPos`、`RetireStation`、`QClock`、KV cache 的 4 个句柄类型。明确不实现 Send 的：`RowGuard`、`QClockGuard`、`BtreeRawIterator`、`TxCallbackLockGuard`、`TxCallbackLogGuard`、`TxCallbackCursor`、`ObKVCacheHazardGuard`。

## 5. 测试

放在 `rust/port/storage_core/tests/`、`rust/port/share/tests/cache/`、`rust/port/oblib/tests/sync/`；loom 测试在 `cfg(loom)` 下把原子量换成 loom 的类型（各模块内部用一个 `sync` 门面导入原子类型）。

**单元测试**

- T1 MVCC：头插、中间插入、摘链、压缩节点插入的链接结果（照 `MVR.cpp:431-686,968-983`、`RC:360-382` 的 C++ 用例手算期望）；标志位的置/清/整字节比较（`need_elr`）；`NodeBatch` 槽位复用规则（未链入复用、链入过不复用）；`get_mtd` 内容；`try_row/try_node` 身份不符返回错误；`size_of` 断言。
- T2 容器：`register_callback` 后 `release` 被拒、`Drop` 进延后表、pin 全释放后 `retry_deferred_memtable_release` 释放；`get_mut` 在有 pin 时返回 None。
- T3 arena：页切换、大页路径、`Ref` 头、按字节计数释放；`destroy()` 后分配失败；hook 槽为空时分配失败；句柄表的 hazard 计算与 for_each 顺序。
- T4 keybtree：插入、点查、正反向扫描（跨批边界，225 的倍数附近）、`insert_or_get_row` 的已存在/新建/并发插入（单线程模拟 EAGAIN）、`destroy(false)`、`pre_batch_destroy` + `batch_destroy`、未调用 `batch_destroy` 时析构自动补做 purge；节点锁（S56）的单线程语义：读计数 +2/-2、写锁占高半字、`set_spin` 的 16 位回绕、`wrunlock` 连 spin 标记一起清。
- T5 回调链表：五种锁模式与 append-only 规则；追加、链式追加、按位置插入；摘头/中/尾（guard 没有 append 锁时摘尾，核心临时取 append 锁；没有 iterate 锁时摘链返回 `OB_ERR_UNEXPECTED`）；游标在摘链后的行为；`TxCallbackPos` 在节点被摘后 `cursor_at` 返回 None；延后释放的两级（guard 释放、下一次 `try_lock_log`）。
- T6 palf：`fill`/`fill_padding_body` 的全部返回码分支；绕回两段拷；`read_data` 下界与复查；`truncate`；`get_log_buf` 两段；未对齐首尾的字拼接。LSN 分配器的每个分支（新组、聚合、2 MB 切分、文件尾 padding、恰好到文件尾、越上界 `OB_EAGAIN`、重定基）。
- T7 KV cache：put/get/erase/overwrite 返回码；`alloc` + `put_kvpair`；handle 的 reset/move_from/assign；`protect_by_ref` 的 seq 过期；wash 后 handle 仍可读、释放后块被回收；`erase_all`；hazard station 槽耗尽返回 `OB_ENTRY_NOT_EXIST`；map 节点计数随 put/erase 增减、`replace_fragment_node` 不搬迁任何节点（S55）；`alloc_mb` 的 `OB_INVALID_ARGUMENT` 分支（S54）。
- T8 回收原语：`wait_quiescent` 只等一个宽限期；`try_quiescent` 的 63/64 节奏；`purge` 两遍。

**loom 测试**（每个都缩小常数：槽数 2–4、上限 1–2、节点扇出不变但只插几个键）

- L1 节点发布：写者分配、填字段、在 `RowGuard` 下头插；读者 `get_list_head`/`prev` 读字段与载荷，必须看到完整内容。
- L2 提交顺序：`trans_commit` 与读者"先 committed 后版本"，读到 committed 时版本不是 max。
- L3 pin 协议：线程 A 持 pin 并经 `MemtableSelf` 再登记一个、释放旧的；线程 B 反复 `release`；断言 T 不会在任何 pin 存活时析构。
- L4 QClock + RetireStation：读者进出临界区并解引用；写者退休、回收、释放；断言读者从不读到已释放的节点（用 loom 的 `UnsafeCell` 标记释放）。
- L5 keybtree 回收：两个写者并发插入触发写时复制退休，一个读者扫描；以及两棵树共享回收域时一棵树 `destroy` 与另一棵树的 `retire` 并发（S33 的场景）；第三个用例单测节点锁（S56）：`try_rdlock`/`rdunlock`/`try_wrlock`/`set_spin`/`wrunlock` 交错，断言写者从 `try_wrlock` 返回 true 之后读计数只剩 spin 标记，且高半字只被一个 uid 占有。
- L6 回调链表：追加者（append 锁）、填日志者（log 锁，从游标向后读）、快速提交者（iterate 锁，摘游标之前的节点）三方并发；再加一个持 `LOCK_ALL` 摘未写日志节点的线程，断言填日志者不读到已释放的节点。
- L7 LSN 分配器：两个 `alloc_lsn_scn` + 一个 `inc_update_scn_base`，断言分出的区间不重叠、log id 连续。
- L8 组缓冲：`fill` 与 `read_data` 并发，`read_data` 要么返回正确数据、要么返回 `OB_ERR_OUT_OF_LOWER_BOUND`。
- L9 **KV cache 退休条件（S47 的回归测试）**：读者 acquire 版本后拿着节点指针；删除方摘链并 `delete_node`；第三个线程触发 `release` 里的 slot retire 和全局 `retire`；断言节点在读者 release 之前不被释放。为证明测试有效，测试代码里另写一个不比较版本的 slot 变体跑同一场景，它必须失败（变体只存在于测试代码，不进产物）。
- L10 hazard pointer：`protect(mb, seq)` 与块 `retire` + `reclaim` 并发，protect 成功则块不被释放。
- L11 FIFO arena：`alloc` 与 `free(handle)` 并发（同页），页不会在分配者还在临界区时释放。
- L12 `RowGuard` 下改链：两个写者各自 `lock()` 后头插节点，一个读者不持锁沿 `get_list_head()`/`prev()` 走；断言读者看到的每个节点都已完整初始化、链不断（latch 自身的互斥由 02 的 LM12 测）。

**Miri**（单线程，跑上面全部单元测试，外加）

- M1 载荷来源：`NodeMut` 先 `DerefMut` 写头再 `get_mtd` 读载荷，Stacked Borrows 下不报错。
- M2 arena 释放后全部引用都已失效（构造 → 写 → 销毁的完整周期，Miri 检查泄漏和越界）。
- M3 keybtree 插入/扫描/销毁；回调链表摘链 + 延后释放 + 位置句柄；KV cache 的 put/get/wash/reclaim；组缓冲首尾拼字。

**以 C++ 为准的金标准测试**（golden 文件由第 5b 步的 C++ 测试程序产出：程序链接基线 `build_release` 里对应的目标文件，按下列确定性场景运行，输出 TSV 到 `rust/port/<crate>/tests/golden/`；Rust 测试读文件逐行比较）

- G1 keybtree：用整数键（实现一个与 `ObStoreRowkeyWrapper` 同形的测试键）单线程按若干固定序列插入（升序、降序、交错、带重复），输出每步之后的 `size()`，以及最后在若干 `(top_level, btree_node_count, range_count)` 下的 `split_range` 键、`estimate_key_count`、`estimate_element_count` 的结果。
- G2 LSN 分配器：从给定 `(log_id, scn, start_lsn)` 起，按固定的 `(base_scn, size)` 序列调用 `alloc_lsn_scn`，覆盖 2 MB 切分、文件尾 padding、恰好到文件尾、重定基（初始 base 设在阈值附近），输出每次的全部出参。
- G3 FIFO arena：nway=1，单线程按固定大小序列分配（含跨页、大页），穿插 `free(handle)`，输出每步之后的 `allocated/hold/retired/reclaimed` 与句柄 `get_allocated`。
- G4 KV cache：`get_align_size` 对一组键值大小；单线程把固定大小序列 put 进空 cache 后，每块的 `kv_cnt`、`store_size`。
- G5 布局：`sizeof(ObMvccRow)=112`、`sizeof(ObMvccTransNode)=96`、`sizeof(ObMemtableDataHeader)=16`、memtable 实例化的 `sizeof(BtreeNode)=272`（由同一个 C++ 程序 `static_assert`/打印，Rust 侧 `const` 断言）。
- G6 `TO_STRING` 格式：`ObMvccTransNode`、`ObMvccRow`（含 verbose）、`LogGroupBuffer`、`LSNAllocator` 的输出，指针用 `0x…` 归一化后逐字节比较（§2.11）。

## 6. 翻译者怎么用

**例 1：`lock_for_read_` 沿版本链走（`ITR:66-92,144`）**

```cpp
ObMvccTransNode *iter = value_->get_list_head();
version_iter_ = NULL;
while (OB_SUCC(ret) && NULL != iter && NULL == version_iter_) {
  if (OB_FAIL(lock_for_read_inner_(flag, iter))) { }
}
...
iter = iter->prev_;                       // lock_for_read_inner_ 里
```
```rust
// value_: Option<RowRef<'m>>, version_iter_: Option<NodeRef<'m>>（结构体带 'm，由 types.tsv 给出）
let mut iter: Option<NodeRef<'m>> = self.value_.unwrap().get_list_head();
self.version_iter_ = None;
while ret.is_ok() && iter.is_some() && self.version_iter_.is_none() {
    if ob_fail!(ret = self.lock_for_read_inner_(flag, &mut iter)) {}
}
...
*iter = iter.unwrap().prev();             // lock_for_read_inner_(&mut self, flag, iter: &mut Option<NodeRef<'m>>)
```

**例 2：`mvcc_write_` 的头插（`MVR.cpp:840,968-983`）**

```cpp
ObRowLatchGuard guard(latch_);
...
ATOMIC_STORE(&(writer_node.prev_), list_head_);
ATOMIC_STORE(&(writer_node.next_), NULL);
if (NULL != list_head_) { ATOMIC_STORE(&(list_head_->next_), &writer_node); }
ATOMIC_STORE(&(list_head_), &writer_node);
if (NULL != writer_node.prev_) { writer_node.modify_count_ = writer_node.prev_->modify_count_ + 1; }
else { writer_node.modify_count_ = 0; }
res.tx_node_ = &writer_node;
total_trans_node_cnt_++;
```
```rust
// impl<'m> ObMvccRowExt<'m> for RowRef<'m> { fn mvcc_write_(self, ctx, writer_node: NodeRef<'m>, res) -> ObResult {
let mut guard = self.lock();
...
let head = guard.get_list_head();
guard.set_prev(writer_node, head);
guard.set_next(writer_node, None);
if let Some(h) = head { guard.set_next(h, Some(writer_node)); }
guard.set_list_head(Some(writer_node));
match writer_node.prev() {
    Some(p) => writer_node.modify_count_.store(p.modify_count_.load(Acquire) + 1, Release),
    None => writer_node.modify_count_.store(0, Release),
}
res.tx_node_ = Some(writer_node);
guard.total_trans_node_cnt_.fetch_add(1, AcqRel);    // 持锁下的普通 ++
```

**例 3：cleanout 的手工加锁（`TXF:510-550`，构造点 `ITR:211`、`MVR.cpp:881-883`）**

```cpp
ObCleanoutTxNodeOperation(ObMvccRow &value, ObMvccTransNode &tnode, const bool need_row_latch);
...
if (need_row_latch_) { value_.latch_.lock(); }
if (need_cleanout()) { ... value_.unlink_trans_node(tnode_) ... value_.trans_commit(commit_version, tnode_) ... }
if (need_row_latch_) { value_.latch_.unlock(); }
```
```rust
pub struct ObCleanoutTxNodeOperation<'g, 'm> { value_: RowRef<'m>, tnode_: NodeRef<'m>, latched_: Option<&'g mut RowGuard<'m>> }
// 构造：ITR:211 → ObCleanoutTxNodeOperation::new(value, iter, None)；MVR.cpp:883 → new(guard.row(), iter, Some(&mut guard))
let mut own: Option<RowGuard<'m>> = None;
let g: &mut RowGuard<'m> = match self.latched_.as_deref_mut() {
    Some(g) => g,
    None => { own = Some(self.value_.lock()); own.as_mut().unwrap() }
};
if self.need_cleanout() { ... g.unlink_trans_node(self.tnode_) ... g.trans_commit(commit_version, self.tnode_) ... }
// own 在作用域结束时 Drop 解锁
```

**例 4：回调的字段与 `trans_commit`（`CB.h:538-544`、`CB.cpp:1743-1764`）**

```cpp
ObMvccRow &value_; ObMvccTransNode *tnode_; ObMemtable *memtable_;
ObRowLatchGuard guard(value_.latch_);
if (NULL != tnode_) { ... value_.trans_commit(ctx_.get_commit_version(), *tnode_) ...
  tnode_->trans_commit(ctx_.get_commit_version(), ctx_.get_tx_end_scn()); ... }
```
```rust
pub struct ObMvccRowCallback { base: ObITransCallback, value_: MvccAddr<ObMvccRow>, tnode_: Option<MvccAddr<ObMvccTransNode>>,
                               memtable_: Option<MemtablePin<ObMemtable>>, /* 其余照 C++ */ }
let mt: &ObMemtable = self.memtable_.as_deref().unwrap();
let mut guard = mt.local_allocator_.row(self.value_).lock();
if let Some(t) = self.tnode_ {
    let tnode = mt.local_allocator_.node(t);
    ... if ob_fail!(ret = guard.trans_commit(self.ctx_.get_commit_version(), tnode)) {} ...
    tnode.trans_commit(self.ctx_.get_commit_version(), self.ctx_.get_tx_end_scn());
}
```

**例 5：构造节点（`ENG:338-366`）**

```cpp
if (OB_FAIL(kv_builder_->dup_data(node, *engine_allocator_, arg.data_))) {
} else { node->tx_id_ = arg.tx_id_; node->trans_version_ = SCN::max_scn(); node->modify_count_ = arg.modify_count_; ...
  node->prev_ = NULL; node->next_ = NULL; if (node->scn_.is_max()) { node->set_incomplete(); } }
```
```rust
// engine_allocator_: AllocHandle（C++ ObIAllocator *）；node: &mut Option<NodeRef<'m>>
match self.engine_allocator_.dup_data(arg.data_) {
    Err(e) => ret = Err(e),
    Ok(mut n) => {
        n.tx_id_ = arg.tx_id_;
        n.trans_version_.atomic_store(SCN::max_scn());
        *n.modify_count_.get_mut() = arg.modify_count_;
        ...                                   // prev_/next_ 已由核心置空，不写
        if n.scn_.atomic_load().is_max() { n.set_incomplete(); }
        *node = Some(n.into_ref());
    }
}
```

**例 6：建键和行（`ENG:431-483` + `QE.cpp:186-208`）**

```cpp
ObQueryEngine::ObMvccRowCreator row_creator = [...](const bool is_exist_key, ObStoreRowkeyWrapper &new_or_exist_key, ObMvccRow *&new_row) {
  if (is_exist_key) { stored_key->encode(new_or_exist_key.get_rowkey()); }
  else { /* dup_key、alloc ObMvccRow、set_btree_indexed、encode，候选跨重试复用 */ } };
query_engine_->create_btree_kv(key, row_creator, value);
```
```rust
// 创建器的逻辑移进核心（S36）；键的深拷贝由 storage 的 `impl BtreeKey for MemtableBtreeKey { fn dup_key }` 提供。
// ObQueryEngine::create_btree_kv 的 Rust 签名改成 (key, stored_key, value)，体内就是对 keybtree_.insert_or_get_row 的一次调用。
let mut stored: Option<ObStoreRowkeyWrapper<'m>> = None;
if ob_fail!(ret = self.query_engine_.create_btree_kv(key, &mut stored, value)) {
} else if value.is_none() { ret = Err(OB_ERR_UNEXPECTED); }
else if ob_fail!(ret = stored_key.encode(stored.unwrap().get_rowkey())) {}
```

**例 7：批量预分配（`MT.cpp:2505-2577`）**

```cpp
if (pos + aligned_data_size > alloc_data_size) { new_nodes = (char *)local_allocator_.alloc(new_alloc_data_size); pos = 0; ... }
... mvcc_engine_.mvcc_write(ctx, *stored_kvs[i].value_, tx_node_args[i], check_exist, new_nodes + pos, mvcc_results[i]) ...
pos += aligned_data_size;                 // 只在成功时
```
```rust
// batch: Option<NodeBatch<'m>>
if batch.as_ref().map_or(true, |b| b.remaining() < aligned_data_size) {
    match self.local_allocator_.alloc_node_batch(new_alloc_data_size) { Ok(b) => batch = Some(b), Err(e) => ret = Err(e) }
}
... self.mvcc_engine_.mvcc_write_void(ctx, stored_kvs[i].value_.unwrap(), &tx_node_args[i], check_exist,
                                     batch.as_mut().unwrap(), &mut mvcc_results[i]) ...
// 带 `void *buf` 的重载声明在后（ob_mvcc_engine.h:90-95），按 §4 重载规则改名，最终以 overloads.tsv 为准；
// `buf` 参数改成 `&mut NodeBatch<'m>`，函数体里 `init_tx_node_(arg, node)` 变成 `batch.init_tx_node(arg.data_)`。
batch.as_mut().unwrap().advance();       // 只在成功时；链入过的槽位核心会自动跳过（S8）
```

**例 8：回调链表遍历内核里的摘链（`TCL.cpp:283-379`）**

```cpp
for (ObITransCallback *iter = (is_reverse ? start->get_prev() : start->get_next()); OB_SUCC(ret) && !iter_end && iter != NULL && iter != end; iter = next) {
  ... next = (is_reverse ? iter->get_prev() : iter->get_next());
  if (OB_FAIL(functor(iter))) {} else if (functor.need_remove_callback()) {
    ... if (next == end) { if (!lock_state.APPEND_LOCKED_) { LockGuard guard(*this, LOCK_MODE::LOCK_APPEND); ret = iter->del(); } ... }
    ... if (log_cursor_ == iter) { set_log_cursor_(next); } ... callback_mgr_.get_ctx().free_mvcc_row_callback(iter); } }
```
```rust
// fn callback_(&self, functor: &mut dyn ObITxCallbackFunctor, g: &TxCallbackLockGuard<'_, TxCallback>, start, end, ...)
let mut iter = if is_reverse { start.prev() } else { start.next() };
while ret.is_ok() && !iter_end && !iter.is_head_or(&end) {
    ...
    let next = if is_reverse { iter.prev() } else { iter.next() };
    let cb: &TxCallback = iter.get().unwrap();
    if ob_fail!(ret = functor.call(cb)) {
    } else if functor.need_remove_callback() {
        ...
        ret = cb.del();                                         // 载荷的 del() 只做策略部分（释放 old_row_ 等）
        if ret.is_ok() {
            ret = self.core_.unlink(g, &iter);                  // C++ next == end 时的临时 append 锁由核心在摘尾节点时自己取
        }
        if ret.is_ok() {
            if self.core_.get_log_cursor().is_at(&iter) { self.core_.set_log_cursor(&next, epoch_of(&next)); }
            ...
            callback_mgr.get_ctx().free_mvcc_row_callback(cb);  // 只做计数等策略；内存由核心延后归还
        }
    }
    iter = next;
}
```


**例 9：追加回调（`TCL.cpp:91-151`）**

```cpp
LockGuard gaurd(*this, LOCK_MODE::LOCK_APPEND);
if (OB_FAIL(callback->before_append_cb(for_replay))) {
} else { ... append_pos = get_tail() 或 parallel_start_pos_->get_prev(); ... append_pos->append(callback); ... }
```
```rust
let g = self.core_.lock(LOCK_MODE::LOCK_APPEND, self.is_append_only_());
if ob_fail!(ret = node.before_append_cb(for_replay)) {
} else {
    let head = self.core_.head(&g);
    let append_pos = if !for_replay || parallel_replay || serial_final || self.core_.parallel_start_pos().is_none() {
        head.prev()                                            // get_tail()
    } else { self.core_.cursor_at(&g, &self.core_.parallel_start_pos().unwrap()).unwrap().prev() };
    ...
    ret = self.core_.append_after(&g, &append_pos, node);       // node: Arc<TxCallbackNode<TxCallback>>
}
```

**例 10：创建行回调（`MVCTX:309-320`；调用方 `MT.cpp:2074` 传 `this`）**

```cpp
cb = new(cb_buffer) ObMvccRowCallback(ctx, value, memtable);
```
```rust
// ObMemtable 有字段 memtable_self_: MemtableSelf<ObMemtable>（由 MemtableRef::new_cyclic 传入）；
// 原来传 this 的地方传 &self.memtable_self_。
match memtable_self.register_callback() {
    Err(e) => ret = Err(e),
    Ok(pin) => cb = Some(TxCallbackListCore::new_node(TxCallback::MvccRow(
        ObMvccRowCallback::new(ctx, value.addr(), Some(pin))))),
}
```

**例 11：t3m 批量销毁（`SMM:528-536,612-618`）**

```cpp
for (...) { ((ObMemtable *)set_iter->first)->pre_batch_destroy_keybtree(); }
memtable::ObMemtableKeyBtree::batch_destroy();
...
pool_arr_[DATA_MEMTABLE]->free_obj((void *)(set_iter->first));
```
```rust
// gc_memtable_set_: 以 memtable_id 为键的表，值是 MemtableRef<ObMemtable>。
// 走到这里时 batch_remove_unused_callback_for_uncommited_txn 已成功（SMM:591-610），正常情况下 pin 全为 0。
// pin 不为 0 时 get_mut() 返回 None：C++ 在这里会继续销毁、留下悬空回调；Rust 版把该表移出本批、放回 GC 队列。
let mut still_pinned: Vec<u64> = Vec::new();
for (id, mt) in memtable_set.iter_mut() {
    match mt.get_mut() {
        Some(m) => m.pre_batch_destroy_keybtree(),
        None => {
            log_error!(OB_ERR_UNEXPECTED, "memtable still pinned before batch destroy", k!(id), kv!("pin_count", mt.get_pin_count()));
            still_pinned.push(*id);
        }
    }
}
for id in still_pinned { if let Some(mt) = memtable_set.remove(&id) { self.push_memtable_into_gc_queue_(mt); } }
let _ = ObMemtableKeyBtree::batch_destroy();                 // ObKeyBtree::<MemtableBtreeKey, ObMvccRowVal>
...
for (_, mt) in memtable_set.drain() {
    if let Err(mt) = mt.release() { /* 有 pin：核心已记 ERROR；放回 GC 队列，下一轮再试 */ self.push_memtable_into_gc_queue_(mt); }
}
```

**例 12：KV cache 读（`storage/blocksstable/ob_row_cache.cpp:233`）**

```cpp
const ObRowCacheValue *value = NULL;
if (OB_FAIL(get(key, value, handle.handle_))) { ... } else { handle.value_ = value; }
```
```rust
// ObRowValueHandle { handle_: ObKVCacheHandle }（不再另存 value_ 指针）；读值时 ObRowCacheValueView::new(handle.handle_.value())
if ob_fail!(ret = self.get(key, &mut handle.handle_)) { ... }
```

**例 13：两段式写入（`storage/blocksstable/ob_micro_block_cache.cpp:1192-1204`）**

```cpp
alloc(sizeof(ObMicroBlockCacheKey), value_size, kvpair, cache_handle, inst_handle);
kvpair->key_ = new (kvpair->key_) ObMicroBlockCacheKey(key);
ObMicroBlockCacheValue *cache_value = new (kvpair->value_) ObMicroBlockCacheValue(block_buf, block_size);
... put_kvpair(inst_handle, kvpair, cache_handle, false /* overwrite */);
```
```rust
let mut kvpair: Option<ObKVCachePair> = None;
if ob_fail!(ret = self.alloc(key_size, value_size, &mut kvpair, &mut cache_handle, &mut inst_handle)) {
} else {
    let p = kvpair.as_mut().unwrap();
    if ob_fail!(ret = key.deep_copy(p.key_buf())) {}
    else if ob_fail!(ret = ObMicroBlockCacheValue::write_header(p.value_buf(), block_size)) {}   // 值的字节布局由消费方定义
    ...
    if ret.is_ok() { ret = self.put_kvpair(&mut inst_handle, kvpair.take().unwrap(), &mut cache_handle, false); }
}
```

**例 14：palf 填缓冲与取写缓冲（`LSW:536,833`）**

```cpp
group_buffer_.fill(lsn, log_buf, buf_len);
group_buffer_.get_log_buf(begin_lsn, group_log_size, log_write_buf);
```
```rust
ret = self.group_buffer_.fill(&lsn, &log_buf[..buf_len as usize]);
ret = self.group_buffer_.get_log_buf(&begin_lsn, group_log_size, &mut |s| log_write_buf.push_back_group(s));
// LogWriteBuf 的片段类型由 logservice 定义，组缓冲来源存 GroupBufferSlice
```

**例 15：memtable 迭代器取下一行（`QE.h:95-112`）**

```cpp
ObStoreRowkeyWrapper key_wrapper(key_.get_rowkey());
if (OB_FAIL(btree_iter_.get_next(key_wrapper, value_))) { ... }
else { key_.encode(key_wrapper.get_rowkey()); BTREE_ASSERT(((uint64_t)value_ & 7ULL) == 0); ... }
```
```rust
// Iterator<'m> { btree_iter_: BtreeIterator<'m, MemtableBtreeKey, ObMvccRowVal>, key_: ObMemtableKey<'m>, value_: Option<RowRef<'m>> }
let mut key_wrapper = ObStoreRowkeyWrapper::new(self.key_.get_rowkey());
if ob_fail!(ret = self.btree_iter_.get_next_row(&mut key_wrapper, &mut self.value_)) { ... }
else { self.key_.encode(key_wrapper.get_rowkey()); }          // 对齐断言由核心在插入时保证
```

## 7. 依赖

- **白名单 crate**（RULEBOOK §1）：`arc-swap`（组缓冲的存储句柄）；`portable-atomic` 不直接依赖（`AtomicU128` 用 02 的 `oblib::atomic` 转出，02-sync.md A02）；`crossbeam-utils` 不用（缓存行对齐用 `oblib::sync::CacheAligned`，§2.7）。dev：`loom`。它不在 §1 白名单里，02-sync 已排队修订（amendments.tsv 的 02-sync "§1 依赖白名单" 行：loom 只作 dev-dependency、只在 `cfg(loom)` 下编译）；Miri 需要 nightly，01-mem 已排队修订（"0.3 工具链" 行）。本文的 L/M 测试以这两条修订通过为前提，不重复追加。
- **oblib（1d 其他模块）**：
  - 01 `mem`：FIFO arena 的页和 KV cache 的块走全局分配器（白名单路径内直接 `std::alloc::alloc`，对齐 16，RULEBOOK 0.7、01-mem.md S17）；计账不用 `MemBudget`（memstore 限额走钩子，KV cache 用自己的 `store_size_`），只在 `ObMemstoreAllocator::init`、`ObKVGlobalCache::init` 里各调一次 `MemBudget::register_probe` 给内存视图供数（01-mem.md S16；名字与 C++ 分配属性一致：memstore 页 `OB_MEMSTORE`/`DEFAULT_CTX_ID`，`FA.cpp:44-45`；KV 块 `OB_KVSTORE_CACHE_MB`，`ob_resource_mgr.cpp:119`）。
  - 02 `sync`：`Latch`、`BigReaderLatch`、`Quiescence`、`BucketLatch`、`ShardedRwLatch`、`bitlock::{ObByteLock, ObRowLatch}`、`CacheAligned`、`pause()`；本模块的 `ob_retire_station.rs` 落在 02 的目录里，由本模块实现（Q3）。
  - 03 `thread`：`get_itid()`、`icpu_id()`、`sched_yield`/`usleep` 的封装。
  - 04 `error-log-fmt`：`ObResult`、`OB_*`、`ob_log!`/`log_*!`、`ob_assert!`、`ObToString`、`databuff_printf!`、`reach_time_interval!`。
  - 05 `containers`：`ObIArray`（`split_range` 等的出参）、`ObFixedQueue<T>`（KV store 的块句柄池）。`ObLink`/`dl_*` 不从 05 来（1.3）。
  - 06：`ObBatchChecksum`（若 crc64 不在 1d，节点和载荷头的 checksum 挪到 2.10 的 storage 扩展）。
  - `oblib::slot::Slot`（两个槽位）。
  - Q1 的值类型。
- **其他 1d 模块**：无。第 4 步波次：storage_core 与 share 同在第 3 波，相互不依赖。

## 8. 开放问题

### 8.1 需要你决定的问题

- **Q1 值类型放哪里（修订 A1）**。storage_core 的节点、行、LSN 分配器的字段类型是 `SCN`、`ObTransID`、`ObTxSEQ`、`ObDmlFlag`、`LSN`（1.1 第二张表），而 RULEBOOK 0.3 规定 storage_core 只依赖 oblib，1d 时 share/data_plane 也还没有翻译。
  - **推荐**：把这 5 个值类型和 1.1 表里的常量下移到 oblib，在 1d 由本模块逐行移植（约 1,100 行 C++：`scn` 640、`ob_tx_id` 113、`ob_tx_seq` 190、`lsn` 217、`ObDmlFlag` 与 `get_dml_str` 约 20、`log_define` 里约 10 个常量），连带 `AtomicSCN`；所有 crate 从 oblib 引用。前提：它们用到的 oblib 单元（`murmurhash`、`ob_yson` 的 `TO_STRING_AND_YSON`、序列化宏）在 1d 范围内，否则一并提前。
  - 备选 A：核心只用原始整数（2.1 末尾），storage/logservice 在调用点转换；翻译量大、比较语义容易写错。
  - 备选 B：核心对值类型泛型化（一个关联类型族的 trait，storage 用标记类型实例化）；不改 RULEBOOK，但核心代码和报错都更难读。
- **Q2 混合单元的拆分与扩展 trait 的命名（修订 A2、A5）**。推荐 1.2 的函数级重映射，以及 `ObMvccRowExt`/`ObMvccRowLockedExt`/`ObMvccTransNodeExt` 这种"C++ 类名 + `Ext`"的命名规则（含义：核心类型在上层 crate 实现的方法）。备选：storage 里写同名自由函数（`mvcc_write(row, ...)`），调用点要改形状，且 `trans_commit`、`elr` 这类名字在 crate 根容易重名。
- **Q3 QClock/RetireStation 只实现一份（修订 A4）**。RULEBOOK 0.8 把 keybtree 的 QClock+RetireStation 写在 storage_core、把 KV cache 句柄的 RetireStation 写在 share::cache，而 share 与 storage_core 互不依赖，照写就是两份。推荐：一份放在 `oblib::sync`（白名单路径），由本模块实现、API 按 2.5 冻结；模块 02 只需要知道这个文件在它的目录里。02-sync.md（O16）和 01-mem.md（`ob_retire_station.h` 行）都按 RULEBOOK 写成"归 08、在 storage_core"，实现者不变，选推荐项只改落点。备选：由模块 02 实现；或照 RULEBOOK 在 storage_core 和 share::cache 各放一份（两份代码要一起改、一起测）。
- **Q4 组缓冲用原子字存储（修订 A6）**。推荐 3.7 S44 的 `[AtomicU64]`；代价是 `fill`/`read_data` 不再是 `memcpy`（`PERF(port)`：整字拷贝，首尾用 CAS）。备选：照 RULEBOOK 写 `UnsafeCell<[u8]>`，让 `read_data` 与 `fill` 互斥（给 `fill` 加锁，改变并发模型）。
- **Q5 KV cache 的值只能是字节**。RULEBOOK 0.8 定了 `&[u8]` 访问。C++ 里有几类值是带内部指针的对象，深拷进块里直接用：schema cache 的 3 个实例（`ob_schema_cache.cpp:371-373`）、优化器统计的 4 个（`ob_opt_stat_service.cpp:255-261`）、`ObStorageMetaCache`（`storage/meta_mem/ob_storage_meta_cache.h:190`）。在 Rust 里它们必须定义自描述的字节布局（相对偏移）和零拷贝视图，否则每次命中都要反序列化，schema 每条语句都会读，可能撞性能带。推荐：维持 RULEBOOK，要求这些消费方单元在 1c 的清单里标出"值的字节布局"并配视图类型；备选：允许 `T: Copy + 'static` 的定长值按类型存放（解决不了带指针的 schema）。
- **Q6 DDL 的 keybtree 实例化**。C++ 键是 16 字节的 `{rowkey_, datum_utils_}`（`ob_datum_rowkey.h:150-163`），比较只用查找键的 `datum_utils_`。推荐：`datum_utils_` 改成树的 `Ctx`（2.6），键只存 rowkey；DDL 的值和键在 `insert` 的闭包里用 cap 构造，`block_meta_` 指向的元数据要一并拷进树的私有 arena。备选：让 DDL 树自己写一个安全的有序容器（会改变 `get_sorted_meta_array` 之外的估算行为，不推荐）。

### 8.2 新发现的疑似 bug（需要你知悉；是否在 C++ 侧修、是否报上游由你决定）

- **B1（S8）** `ObMemtable::batch_mvcc_write_` 在写入失败、且节点已经头插又被 `mvcc_undo` 摘掉时，不前移 `pos`（`MT.cpp:2577`），下一行在同一地址重新构造节点（`ENG:374`），并发读者可能正拿着旧节点。Rust 版 FIXED(port)。
- **B2（S7）** 压缩节点的 `write_epoch_`、`tx_end_scn_` 从未初始化（`RC:329-350`），`to_string` 和多版本迭代会读到垃圾值。Rust 版 FIXED(port)。
- **B3（S18）** `ObTransService::remove_callback_for_uncommited_txn` 在服务未运行时返回 `OB_SUCCESS`（`ob_trans_service.cpp:342-343`），调用方据此销毁仍有回调指向的 memtable（`SMM:590-611`）。Rust 版由 pin 协议变成延后释放。
- **B4（S52）** `ObKVCacheMap::put` 覆盖旧节点时不减旧块的 `kv_cnt_`、`get_cnt_`（`MP.cpp:249-251`，对比 `erase` 的 `:389-390`），影响块的评分与淘汰。照样复刻，标 `BUG(port): put overwrite 后查旧块 kv_cnt_`。
- **B5（S47）** 即 RULEBOOK 0.8 已列出的 KV cache 退休条件；本文给出了确切条件和回归测试 L9。
- **B6（S44）** `LogGroupBuffer::read_data` 的复查在 aarch64 上没有保证：复查用的 `ATOMIC_LOAD` 编译成 load-acquire，它不阻止之前 `memcpy` 的普通读越过它，读者可能先读到旧的 `readable_begin_lsn_`、后读到已被覆盖的数据，把半新半旧的内容当成有效数据返回（`LGB.cpp:461-477`；写者一侧 `inc_update` 之后的 `memcpy` 同理，`:223-226`）。Rust 版加栅栏，FIXED(port)。

### 8.3 排队的修订（已追加到 `migration/infra/amendments.tsv`）

| # | 章节 | 问题 | 建议 |
|---|---|---|---|
| A1 | 0.3、0.9 | storage_core 只依赖 oblib，但它的数据结构要用 share/data_plane 的 5 个值类型，1d 时这些还没翻译 | 见 Q1 |
| A2 | §4 存储核心单元、0.9 | `ob_mvcc_row`、`ob_tx_callback_list`、`ob_mvcc` 混有依赖 storage 的策略函数，整单元 `rewrite:storage_core` 写不出来 | 按 1.2 拆分：机制 `rewrite:storage_core`，策略照原路径 `translate`，由 units.tsv 做函数级重映射 |
| A3 | 0.8 | "退休表按 `itid % 4096` 分槽"只对 aarch64 成立 | 改成 `itid % (OB_MAX_CPU_NUM * 32)`（x86-64 2048、aarch64 4096，`ob_define.h:1483-1487`、`RS.h:198,258`）；QClock 槽数 `OB_MAX_CPU_NUM * 4` |
| A4 | 0.8 | QClock+RetireStation 在 storage_core 和 share::cache 各写一份，违反单一归属 | 见 Q3 |
| A5 | §4 改名表 | 本文引入的新名字不在改名表里；§2.8 用到的 `TxCallback` 也不在 | 补：`RowRef`、`NodeMut`、`NodeBatch`、`MvccAddr`、`MemtableSelf`、`AllocHandle` 的带类型分配与 `ArenaCap`、`ObKeyBtreeDomain`、`ObKeyBtreeArena`、`BtreeAllocSource`、`ObMvccRowVal`、`BtreeKey`/`BtreeVal`（C++ 模板参数名）、`TxCallbackListCore`/`TxCallbackNode`/`TxCallbackPos`/`TxCallbackCursor`/`TxCallbackLockGuard`/`TxCallbackLogGuard`、`TxCallback`、`GroupBufferSlice`、`ObMemstoreAllocHook`、`KvBlockRef`、`AtomicSCN`，以及"`<类名>Ext`"规则 |
| A6 | 0.9 palf 行 | "在 `UnsafeCell<[u8]>` 上做不相交的写"：`read_data` 在设计上与 `fill` 并发读写同一段（`LGB.cpp:461-477`），即使写不相交也是数据竞争 | 存储改原子字（Q4） |
| A7 | 0.9 回调行、storage 备忘 | 第三种回调的类名是 `ObExtInfoCallback`（`storage/lob/ob_ext_info_callback.h:84`），不是 `ObExtInfoLogCallback` | 改名 |
| A8 | 0.9 keybtree 行 | 漏了 DDL 的第二个实例化（`ob_tablet_ddl_kv.h:83-85`），每个实例化各有一套静态回收域（`KB.cpp:1742-1761`）；"K/V 各一种类型"不成立 | 补一句：keybtree 泛型化，回收域按实例化各一套 |
| A9 | 0.8 KV cache | "put/erase/wash 照 C++ 不等读者"需要说清边界：不等 hazard pointer 持有者和 map 读者，但 store 的 RetireStation 会等 QClock 的一个宽限期（`ST.cpp:1209-1242`、`RS.h:221-224`） | 改写为 3.4 S30 的表述 |
| A10 | A.4 `mem-view`；0.9 对象池 | 对象池首版去掉后，KV cache map 节点的碎片搬迁（`is_fragment` → `internal_map_replace`，`MP.cpp:193,385,616,706-732`；定时任务 `KVCacheRep`）失去意义（S55）。这既不是视图内容、也不是 C++ 的错误，按 infra README 的规则找不到可以挂靠的 A.4 项 | `mem-view` 改为"内存视图的内容，以及只影响内存占用的内部整理（如 KV cache map 节点不再做碎片搬迁）"；或新增一项 `kv-defrag` |

另外，本文的 loom 测试依赖 02-sync 已排队的修订（§1 白名单加 `loom`，只作 dev-dependency），Miri 测试依赖 01-mem 已排队的修订（0.3 用固定日期的 nightly 单独跑 Miri）；本文不重复追加这两条。
