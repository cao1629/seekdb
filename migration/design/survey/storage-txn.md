# 调查备忘：存储引擎的内存结构、事务与持久化格式

> - **主题**：storage-txn。**基线**：`7d907abfa`；工作区的 `src/`、`rust/`、`deps/` 与基线一致（`git diff --stat 7d907abfa HEAD -- src/ rust/ deps/` 输出为空）。只读调查，没有构建。
> - **范围**：`src/storage/` 下的 memtable、tx、tx_table、tx_storage、multi_data_source、tablelock、concurrency_control、deadlock、meta_mem、tablet、ls、blocksstable、blockstore、slog、slog_ckpt、checkpoint、meta_store，以及 `src/logservice`（含 palf）。
> - 推断和估算都标了「估计」。

## 1. 结论

1. **推荐组合**：MVCC 版本链、`ObMvccRow`、keybtree、事务回调链表的链接操作、palf 的组缓冲，收进一个独立的小 crate `storage-core`。unsafe 集中在约 4K 行 C++ 对应的代码里，约占本主题 31.7 万行的 1–2%（估计）。事务上下文、提交/回滚、MDS、tablet 元数据、palf 状态机全部写成安全 Rust。
2. **为什么便宜**：版本链节点从不单独释放，随整个 memtable 一起回收（`ob_mvcc_engine.cpp:452`）。节点只存 `tx_id`，靠它反查事务，没有指回回调或事务的指针。所以版本链不需要 epoch 回收；真正需要的只有 keybtree 的写时复制节点（`ob_keybtree.h:251-253`），用 `crossbeam-epoch` 代替 QClock/RetireStation。
3. **句柄**：C++ 句柄本来就是 RAII，改成 `Arc`，整体替换的槽位用 `arc-swap`。"归零后推进 GC 队列"放进 `Drop`。宏块引用计数记的是磁盘块，保持为按 ID 的计数表。MDS、表锁、事务上下文表用 `Arc` + 锁 + `slab`，零 unsafe。
4. **持久化格式由你定**：裁判从不跨二进制，对齐不强制兼容。但 PR 模板要求升级兼容，可行性报告定的又是逐子系统迁移（新旧代码共享同一批文件），所以推荐"读写同一格式"。增量成本主要在验证：约 25 个按内存布局落盘的头结构、自定义 crc64、跨二进制重启场景。翻译量基本不变。

## 2. 事实

### 2.0 怎么数的

1. **镜像**：`python3 -I migration/feasibility/scripts/arch/strip.py <repo> migration/feasibility/results/arch/gen.ignore $M`。去掉注释和字符串，保留行号，共 6,607 个文件。
2. **分类计数**：`scan_topic.py $M`。按子目录分组，正则和 `migration/feasibility/scripts/arch/unsafe_share.py` 完全相同，另加序列化宏、QSync/epoch 两列。
3. **函数级**：`python3 -I migration/feasibility/scripts/arch/func_share2.py $M`。只统计 `.cpp/.ipp` 里的函数体。
4. **其他脚本**：`type_refs.py`（按顶层模块数整词出现次数）、`ser_funcs.py`（序列化函数体的个数、行数和宏调用数）、`templates.py`（模板密度）、`manifest_units.py`（按 `migration/manifest.tsv` 第一列的路径前缀计数）。
5. **按内存布局落盘的头**：`grep -rhoE 'reinterpret_cast\s*<\s*(const\s+)?\w*(Header|HEADER)\w*\s*\*\s*>' $M/src/storage $M/src/logservice | sort | uniq -c`。
6. 脚本在临时目录 `/tmp/survey-storage-txn-7d907/`，没有入库。

### 2.1 规模

- 本主题 944 个文件、317,028 行（含头文件），对应 509 个 manifest 单元，占全部 3,754 个的 13.6%。
- 单元数：blocksstable 125、tablet 58、tx 57、palf 51、memtable 38（其中 mvcc 17）、multi_data_source 36。

| 子系统 | 行数 | `ATOMIC_*` | 裸指针成员 | 手动 `inc/dec_ref` | 函数体数 | 含 Tier-A | 直译需 unsafe |
|---|---|---|---|---|---|---|---|
| memtable/mvcc | 14,104 | 163 | 64 | 0 | 724（memtable 合计） | 22.9% | 52.3% |
| memtable 其余 | 12,986 | 102 | 51 | 8 | ↑ | ↑ | ↑ |
| tx | 37,627 | 149 | 86 | 25 | 1,025 | 10.0% | 36.0% |
| tx_table | 9,980 | 39 | 32 | 2 | 304 | 11.8% | 43.1% |
| multi_data_source | 14,309 | 46 | 50 | 4 | 382 | 17.8% | 38.7% |
| tablelock | 16,194 | 23 | 18 | 0 | 521 | 9.6% | 34.2% |
| meta_mem | 11,766 | 30 | 45 | 17 | 401 | 15.0% | 41.4% |
| tablet | 34,761 | 16 | 59 | 45 | 998 | 8.5% | 41.4% |
| blocksstable | 87,883 | 26 | 467 | 43 | 2,369 | 16.7% | 44.8% |
| palf | 23,151 | 124 | 61 | 1 | 879 | 10.8% | 24.2% |
| **本主题合计** | **317,028** | **900** | **1,051** | **169** | **9,347** | **13.1%** | **39.6%** |

- 「含 Tier-A」指函数体里有 placement new、`reinterpret_cast`、memcpy、`ATOMIC_*` 等结构（其中 `ATOMIC_*` 改用 Rust 原子类型后是安全的）；「直译需 unsafe」指有 Tier-A 结构或者有 `->`。

### 2.2 Memtable 与 MVCC

- **节点 `ObMvccTransNode`**（`ob_mvcc_row.h:64-235`）：
  - 字段在 `:85-99`。`prev_`/`next_` 是裸指针，`prev_` 指向更旧的版本，`list_head_` 是最新版本；状态 `flag_` 用 CAS 循环改位（宏定义在 `:43-58`）。
  - 末尾的 `char buf_[0]` 是变长载荷：`ObMemtableDataHeader` 加扁平行格式（`ob_mvcc_engine.cpp:369-394`）。
- **行 `ObMvccRow`**（`:242-453`）含 1 字节忙等自旋锁 `ObRowLatch`（`ob_row_latch.h:26-46`）、`list_head_`、`latest_compact_node_`、回放索引 `index_`，并有 `STATIC_ASSERT(sizeof(ObMvccRow) <= 120)`（`:299`）。
- **写者持行锁拼接**：
  - `mvcc_write_` 先拿 `ObRowLatchGuard`（`ob_mvcc_row.cpp:840`），头插在 `:968-972`。
  - 回放时按 scn 插到链中间（`:547-686`，拼接在 `:604-612`）。
  - 回滚/中止从链中间摘除：`unlink_trans_node`（`:431-502`）、`mvcc_undo`（`:1036-1051`）。
- **读者不加锁**：
  - `lock_for_read_` 从 `get_list_head()` 起沿 `iter = iter->prev_` 走（`ob_mvcc_iterator.cpp:70,144,165,170`）。这里的 `prev_` 是普通读，按 C++ 内存模型是数据竞争，实际靠硬件行为成立。
  - 未决节点去事务表判定（`:231`），必要时以 `usleep(10)` 自旋，等待状态回填（`:256`）。
  - `ATOMIC_LOAD`/`ATOMIC_STORE` 都是 SeqCst（`ob_atomic.h:41,44`）。
- **回收粒度是整个 memtable**：
  - 节点、行、键都从 memstore 分配器取（`ob_mvcc_engine.cpp:342,452-465`）。注释写明 "Memstore allocator has no per-object free"（`:452`），`AllocHandle::free` 是空操作（`ob_memstore_allocator.h:117-119`）。
  - 摘掉的节点、行压缩后的旧节点都不释放：`ob_row_compactor.cpp` 只有 `alloc`（`:329`）。memtable 目录里的 13 处 `free` 只针对回调、旧行缓冲和出错路径上的键，没有一处释放节点或行（`grep -rnE '(->|\.)free\s*\(' $M/src/storage/memtable`）。
  - **所以版本链本身不需要 epoch/hazard 回收，读者持有 memtable 句柄就够了。**
- **行锁就是链头的未决节点**：
  - 没有独立的锁表。`mvcc_write_` 的 Case 5 把锁持有者写进 `lock_state`（`ob_mvcc_row.cpp:930-943`）。
  - 等待走 `ObLockWaitMgr`，按 `(tablet_id, rowkey)` 哈希（`ob_lock_wait_mgr.h:356-370`），等待节点就是请求本身 `rpc::ObLockWaitNode`（`:187`）。
  - 行指针会被带出 memtable：`ObStoreRowLockState::mvcc_row_` 是裸 `ObMvccRow *`（`ob_i_store.h:285`），`ObFastQueryContext::mvcc_row_` 是 `void *`（`:211`）。
- **keybtree**：
  - 实例是 `ObKeyBtree<ObStoreRowkeyWrapper, ObMvccRow *>`（`ob_query_engine.h:36`），写时复制加 epoch 回收（`ob_keybtree.h:251-253`）；K 和 V 必须都是 8 字节，值的低 3 位必须为 0（`:51-52`）。
  - 每个节点 15 个键（`ob_keybtree_deps.h:47`），用 64 位 multibitset 做索引（`:107`），读写锁是 union（`:61-104`）。
  - `QClock`、`RetireStation`、`ObQSync` 是全局静态变量（`ob_keybtree.cpp:1743-1760`）；销毁前先 `WaitQuiescent`，再清空回收站（`:1514-1549`）。
  - 节点池按 CPU 分片，只随 memtable 一起释放（`ob_keybtree.h:76-80`）；头文件里写着 `#include "ob_keybtree.cpp"`（`:328`）。
- **keybtree 的结果用户看得到**：
  - `ObMemtable::get_split_ranges` 调 `split_range`（`ob_memtable.cpp:1723-1739`），决定并行扫描怎么切分。
  - `estimate_row_count` 调 `estimate_key_count`/`estimate_element_count`（`ob_query_engine.cpp:494,617`），结果交给优化器。换成别的数据结构，切分点和行数估计都会变。
- **include 环** `ob_mvcc_define.h:23` ↔ `ob_mvcc_row.h:28`：两端在同一个 crate 里，Rust 模块可以互相引用，没有影响（`01-depmap-report.md` §3.2）。

### 2.3 事务

- **回调链表 `ObTxCallbackList`**（`ob_tx_callback_list.h:31-276`）：
  - 用哨兵 `head_` 首尾相接成环（`:230`；`empty()` 判断的是 `head_.get_next() == &head_`，`:191`）。
  - 三把字节锁 `append_latch_`、`log_latch_`、`iter_synced_latch_`（`:270-274`）。它们让写者在尾部追加的同时，填日志的线程可以遍历还没写日志的前缀（锁模式见 `ob_tx_callback_list.cpp:980-1050`）。
  - 链接字段侵入在 `ObITransCallback` 里（`ob_mvcc.h:176-177`）。
  - 回调只有三种：`ObMvccRowCallback`、`ObOBJLockCallback`（表锁）、`ObExtInfoLogCallback`（LOB）。
  - 并行写时，一个事务有多条链（`ob_mvcc_trans_ctx.h:376-377`）。
- **指针是单向的**：
  - 回调指向行、节点和 memtable：`ObMvccRow &value_; ObMvccTransNode *tnode_; ObMemtable *memtable_;`（`ob_mvcc_trans_ctx.h:540-544`）。
  - 节点里**没有**指回回调或事务的指针（字段表见 `ob_mvcc_row.h:85-99`），只存 `tx_id_`。事务状态按 `tx_id` 去事务表或事务上下文反查：读路径在 `ob_mvcc_iterator.cpp:231`，写路径的清理在 `ob_mvcc_row.cpp:880-883`。
  - 回调被移除（快速提交、检查点）时，只给节点打上 `F_DELAYED_CLEANOUT`（`ob_mvcc_row.cpp:149-152`）。
  - 所以"MVCC 节点 ↔ 回调环"不是指针环，而是"指针 DAG 加一条按 ID 的反查"。
- **回调里的 memtable 裸指针靠协议保活**：memtable 的引用归零后进入 t3m 的 GC 队列（`ob_i_table.cpp:188`）；销毁之前，先遍历所有活跃事务，摘掉指向它的回调（`ob_memtable.cpp:166-181` → `ob_mvcc_trans_ctx.cpp:660-672`）。
- **`ObTransCtx`**（`ob_trans_ctx.h:85-252`）：
  - 继承 `ObLightHashLink`，即侵入式哈希链加引用计数（`ob_light_hashmap.h:74-103`）。
  - 每个 LS 一张表：`ObLightHashMap`，1<<14 个桶，桶级自旋读写锁（`ob_trans_ctx_mgr_v4.h:81`）。`get` 加引用，`revert` 减到 0 时释放。
  - 有一把 `CtxLock lock_`（`:222`），还有回指针 `trans_service_`、`ls_tx_ctx_mgr_`、`p_mt_ctx_`、`timer_`（`:221-248`）。
- **`ObTxCtx`**（`ObTransCtx` 的实现类）：
  - 把 `ObMemtableCtx mt_ctx_` 直接嵌在对象里（`ob_tx_ctx.h:598`）。
  - 在途的日志回调挂在侵入式 `ObDList<ObTxLogCb> busy_cbs_` 上（`:633`）；每次提交日志前 `acquire_ctx_ref_()`，失败时 `release_ctx_ref_()`（`ob_tx_ctx.cpp:2840,2851`）。
  - 对象来自小对象池；数量超过上限时报 `OB_TRANS_CTX_COUNT_REACH_LIMIT`（`ob_trans_factory.cpp:89-110`）。
- **提交路径（单机，没有两阶段提交）**：
  - 调用链：`ObTxCtx::commit`（`ob_tx_ctx.cpp:581`）→ `one_phase_commit_` → `do_local_tx_end_`（`:5553`）→ `do_local_commit_tx_` 提交 `TX_COMMIT_LOG`（`:5605`）→ palf 落盘 → apply 线程回调 `on_success`（`:1514`）→ `on_local_commit_tx_`（`:5688`）→ `tx_end_`（`:1459-1508`）。
  - `tx_end_` 的注释写明了五步的顺序：先确定 end_scn 和 commit_version，再设置事务表状态，最后 `mt_ctx_.trans_end` 从前往后回填节点。
  - 回填节点在 `ObMvccRowCallback::trans_commit` 里做，持行锁（`ob_mvcc_trans_ctx.cpp:1748`）。中止走 `trans_abort`（`:1844`），回滚到保存点走 `rollback_callback`（`:1867`）。
  - 事务日志只有 8 种，外加大日志分段（`ob_tx_log.h:78-93`）。`grep -rln 'TwoPhaseCommit\|ObTxPrepareLog' src/storage/tx` 没有结果。
- **事务描述符 `ObTxDesc`**（`ob_trans_define_v4.h:176`）：
  - 同样是 `ObLightHashLink`；在 storage 以外被引用 282 次：sql 157 次、data_plane 93 次、observer 32 次。
  - 会话持有它的裸指针，并暴露 `ObTxDesc *&get_tx_desc()`（`ob_basic_session_info.h:1706,1715`），共 127 处调用。
  - data_plane 已经把它做成不透明的借用句柄：只读观察函数在 `ob_tx_desc_access.h`，`clone_tx_desc`/`release_tx_desc` 在 `ob_tx_desc_lifecycle.h`，注释写明 "descriptor state and layout stay private"。
- **multi_data_source**：
  - 那个 8 文件的 include 环来自 X-macro 注册表 `compile_utility/mds_register.h`：同一个文件在不同的宏定义下被多次包含，生成单元类型表和事务数据源枚举（`ob_multi_data_source.h:55-66` 在枚举体里 include 它）。
  - 枚举值 3/4/7/29/34/38/41/42 会写进 clog（`OB_SERIALIZE_MEMBER(ObTxBufferNode, type_, …)`，`ob_multi_data_source_tx_buffer_node.cpp:38`），必须保持不变。
  - `UserMdsNode` 同时挂在两条侵入式链表上，基类的顺序不能变（`mds_node.h:181`）；它还带回指针 `mds_ctx_` 和 `p_mds_row_`（`:177,263`）。
  - 并发靠 `MdsLock`，不是无锁结构；模板密度是每千行 35.2 个，storage 平均 2.6 个。

### 2.4 句柄与引用计数

- **数量**：storage 和 logservice 里共有 92 个 `Ob*Handle`/`Ob*Guard` 类（含锁 guard）。本主题有 169 处 `inc_ref`/`dec_ref` 一类的调用（正则计数，含声明和定义），其中 `ob_tablet.cpp` 21 处、`ob_sstable.cpp` 11 处。
- **C++ 句柄本来就是 RAII**，难点在于"引用归零以后做什么"：

| 句柄 | 归零动作 | 证据 |
|---|---|---|
| `ObTableHandleV2` | memtable 推入 t3m GC 队列；独立的 sstable 析构并由 allocator 释放；随 tablet 生命周期的 sstable 什么也不做（负计数也不报错） | `ob_i_table.cpp:165-206` |
| `ObMetaObjGuard<T>`、`ObTabletHandle` | 还给对象池或 allocator；持有超过 2 小时告警；tablet 句柄带 wash 优先级 | `ob_meta_obj_struct.h:159-196,354-381`；`ob_tablet_handle.h:36-63` |
| `ObLightHashMap` 的值（tx ctx、tx desc、表锁对象等，共 5 个子类） | 从桶里摘下后释放 | `ob_light_hashmap.h:74-103` |
| `ObTxDataGuard` | 还给 tx data 分配器 | `ob_tx_data_define.h:371-400` |
| `ObMacroBlockHandle`、`ObStorageObjectHandle`、`ObTablet::inc_macro_ref` | 不是内存：按 `MacroBlockId` 给磁盘块计数，块管理器 mark-and-sweep 回收 | `ob_macro_block_handle.cpp:54-79`；`ob_tablet.cpp:2015-2064`；`ob_block_manager.h:241` |
| palf 滑动窗口的槽位 | 计数归零且已经滑出时，重置这个槽 | `fixed_sliding_window.h:186,230-279` |

- **tablet 是自带 arena 的对象**：
  - `ObTablet` 的 `table_store_addr_`、`rowkey_read_info_`、`ddl_kvs_` 等指针都指向它自己的 `ObArenaAllocator`（`ob_tablet.h:760-789`）。
  - 更新时写时复制：旧版本挂在 `ObTabletPointer::old_version_chain_` 上（`ob_tablet_pointer.h:180`、`ob_tablet_pointer.cpp:566-590`）。
  - tablet 内存受 `META_OBJ_CTX_ID` 的上限约束（`ob_meta_memory_limit.h`）。
- **`ObTenantMetaMemMgr` 在 seekdb 里叫 `ObStorageMetaMemMgr`（t3m）**：旧名在 `src/` 里出现 0 次；`ob_tablet_meta_mem_mgr.cpp` 是一份没有参与构建的副本（`migration/depmap/excluded.tsv:116`）。t3m 用两个定时任务回收 tablet 和 table（`ob_storage_meta_mem_mgr.h:369-498`）。
- **引用范围**：
  - `ObTabletHandle` 被引用 803 次，其中 storage 以外 59 次；`ObTableHandleV2` 303 次（storage 外 25 次）；`ObITable` 931 次（storage 外 80 次）。
  - MVCC 内部类型 `ObMvccRow`、`ObMvccTransNode`、`ObITransCallback`、`ObTxCallbackList`、`ObKeyBtree` 在 storage 以外都是 0 次。所以不安全的核心可以完全藏在 storage 内部。

### 2.5 palf 以及 redo 怎样交给它

- **seekdb 的 palf 是单副本 WAL**：96 个文件、23,151 行，没有选举和配置变更（`grep -rln 'election\|paxos\|LogConfigMgr\|ack_log\|majority' src/logservice/palf` 没有结果）。
- **`FixedSlidingWindow<T>`**（`fixed_sliding_window.h:77-502`）：
  - 长度为 2^n 的槽数组，每个槽有 `ref_` 和一把 `ObByteLock`（`:38-39`）。`get` 在读锁下给槽加引用，再检查一次范围（`:168-206`）。
  - `revert` 把引用减到 0 时，在槽锁下重置这个槽并推进 `end_sn_`（`:214-279`）；`slide` 逐槽回调后用 CAS 推进 `begin_sn_`（`:290-357`）。
  - 只用了计数、CAS 和锁，没有裸指针图。
- **交接路径**：
  - `ObTxCtx::submit_log_block_out_` 把序列化好的 `ObTxLogBlock` 交给 `ObLSTxLogAdapter::submit_log`（`ob_tx_ctx.cpp:2958-2993`、`ob_tx_log_adapter.cpp:46-92`），再到 `ObLogHandler::append_`（`ob_log_handler.cpp:355-397`）。
  - palf 先**拷贝**字节（`palf_handle_.append`），然后把 `AppendCb*` 裸指针推进 apply 队列（`:397`）。
  - `AppendCb` 自带侵入式链接 `__next_`，用 container_of 还原对象（`ob_append_callback.h:28-52`）。apply 线程调用 `on_success`/`on_failure`（`ob_log_apply_service.cpp:448-477`）。
- **不安全的地方只有两处**：多个线程按 LSN 预留区间，并发写同一个环形字节缓冲（`LogGroupBuffer::fill`，`log_group_buffer.cpp:195`）；LSN 分配器做 128 位位域 CAS（`lsn_allocator.h:91-100`）。
- **crate 方向是干净的**：链接级 storage → logservice 有 46 个符号，反方向为 0（`migration/depmap/crates/union_edges.tsv:54`）。

### 2.6 持久化格式

| 家族 | 位置 | 帧和头 |
|---|---|---|
| clog（palf） | redo 目录（`ob_file_system_router.cpp:167`） | `LogBlockHeader` magic 0x4942、版本 1（`log_block_header.h:58-59`）；`LogGroupEntryHeader`；`LogEntryHeader`；`LogMetaEntryHeader` 0x4C4D（`log_meta_entry_header.h:51`）；`LogMeta` 版本 4（`log_meta.h:60`）；每块 64MB（`share/log/palf/log_define.h:85-86`） |
| clog 载荷 | 同上 | `ObTxLogBlock` 加 8 种事务日志（`ob_tx_log.h:78-93`）；行 redo 是 `ObMemtableMutator`（`MMB_MAGIC`，`ob_memtable_mutator.h:53`），装的是扁平行格式；MDS 数据源 ID（`ob_multi_data_source.h:55-66`） |
| slog | `data_dir/slog`（`ob_file_system_router.cpp:146`） | entry magic 0xAAAA（`ob_storage_log_entry.h:40`）、batch magic 0xAABB（`ob_storage_log_batch_header.h:40`）；7 种子类型：更新/删除 tablet、更新 LS、超级块等（`ob_storage_log_struct.h:26-49`） |
| block_file | `data_dir/sstable/block_file`（`ob_block_sstable_struct.cpp:32-33`） | 超级块 1018、微块头 1005（`ob_block_sstable_struct.h:50-51`）；宏块公共头 1001（`ob_macro_block_common_header.h:36`）；sstable 宏块头 1007（`ob_sstable_macro_block_header.h:92`）；检查点链式宏块 10000/10001（`ob_linked_macro_block_struct.h:36,82`）；tablet 载荷版本 4（`ob_tablet.h:753`）；编码微块（`blocksstable/encoding`，25,062 行）；索引块（21,204 行） |
| 事务表 | block_file 内的 sstable | tx data / tx ctx 行（`ob_tx_table_define.h:71-72`） |
| 向量索引 | 同上 | VSAG 库自己的格式（提交 `50306fbfd` 为了兼容恢复成旧格式） |

- **序列化原语**：
  - 定长整数按大端写（`serialization.h:155-226`）。
  - unis 框架里的整数用变长编码：`encode(int64_t)` 调 `encode_vi64`（`:1698-1700`，变长编码在 `:308-322`）。
  - 每个对象头是版本号加 5 字节长度（`:96`）；版本不符时返回 `OB_NOT_SUPPORTED`（`ob_unify_serialize.h:143-150`）。
- **按内存布局直接落盘**：
  - `ObMicroBlockHeader::serialize` 是整个结构体的拷贝，含位域、union 和一个指针字段（`ob_micro_block_header.cpp:239-266`）。
  - `ObMacroBlockCommonHeader` 用 `reinterpret_cast` 写和读（`ob_macro_block_common_header.cpp:99-106`）。
  - `ObColumnHeader` 含 union，并用 1 个字节存 `ObObjType` 的枚举值（`ob_block_sstable_struct.h:157-195`）。
  - 脚本统计到约 25 个这样的头类型；已排除只在内存里用的 `ObMemtableDataHeader` 和 `ObBlockCachedDecoderHeader`。
- **内存、日志、sstable 共用同一种行格式**：memtable 节点的载荷由 `ObRowWriter` 写（`ob_memtable.cpp:2154` 等），redo 把这些字节原样带进 clog，sstable 的扁平微块也用这种格式；它的头 `ObRowHeader` 带位域（`ob_block_sstable_struct.h:641-784`）。
- **校验和是自定义的 `ob_crc64`**：用 SSE4.2 的 crc32 指令组合而成，多项式 `0xD800000000000000`（`ob_crc64.h:27,75-77`）。有纯软件实现，可以对照着移植。
- **序列化代码量**：
  - 手写的序列化函数体：storage 424 个、7,244 行，另有宏调用 204 处；logservice 34 个、532 行，宏调用 33 处；share 198 个、3,147 行，宏调用 357 处。
  - 编码微块和索引块不论兼容与否都要移植，兼容只决定它们要不要逐位一致。
- **裁判不跨二进制**：每个用例先 destroy，再用同一个二进制 start（`migration/judge/runner/judge.py:148-151`）；生命周期场景 kill -9 之后也用同一个二进制重启（`lifecycle.py:60-79`、`suites/lifecycle/crash_committed.lifecycle`）。没有"C++ 写、Rust 读"的场景。
- **项目的政策**：PR 模板里有一节 "Upgrade Compatibility: Please make sure this is compatible with old version or you should give us upgrading solution."（`.github/pull_request_template.md`）。
- **答"要兼容"的代价**：
  - 序列化 derive 宏必须逐字节一致。
  - 约 25 个按内存布局落盘的头，要用 `#[repr(C)]` 加手写位域访问，并配 golden 文件。
  - crc64 必须逐位一致。
  - 裁判要增加"C++ 写 → Rust 读"的正常重启和 kill -9 场景。
  - 迁移期间格式冻结，Rust 版要改格式就得走版本号。
- **答"不兼容"的代价**：
  - 用户升级要先导出再导入。
  - C++ 和 Rust 组件不能共享同一批文件，也就做不了逐子系统迁移（可行性报告 §6 的结论）。
  - 省下的只有 golden 文件和跨二进制场景，翻译量不变。

## 3. 选项

几个选项不互斥，分别适用于不同的结构。第 4 节给出组合。

### 选项 A：小的 unsafe 核心 + memtable arena + 原子指针（keybtree 用 crossbeam-epoch）

**做法**
- 新建 crate `storage-core`，只依赖 std、`crossbeam-epoch`、`portable-atomic`。它放"机制"，不放"策略"：
  - 并发的 memtable arena：每个 memtable 一个，整块释放，对接内存主题的 memstore 限流计量；
  - `TxNode`/`MvccRow` 的 `#[repr(C)]` 布局和原子字段；
  - 安全访问层 `NodeRef<'m>`/`RowGuard<'m>`；
  - 行自旋锁；
  - 移植过来的 keybtree；
  - 回调链表的侵入式原子链接；
  - palf 的 `GroupBuffer`。
- MVCC 规则留在 storage crate，用核心的安全 API 写：`mvcc_write_` 的五种情况、`lock_for_read`、cleanout、ELR。storage crate 加 `#![forbid(unsafe_code)]`。
- **版本链**：不用 epoch。`'m` 生命周期绑定在 `MemtableGuard` 上，后者持有 `Arc<Memtable>`。
- **keybtree**：
  - 写时复制节点用 `crossbeam_epoch::Atomic`/`Shared`，退休节点交给 `guard.defer`。
  - 延迟闭包捕获 `Arc<NodePool>`，保证 arena 在所有延迟回收完成之后才释放。
  - 扫描每批 225 条重新 pin 一次，和 C++ 每批扫描后释放内部迭代器的做法一致（`ob_keybtree.h:143-147,160`）。
- **内存序**：`ATOMIC_LOAD`/`ATOMIC_STORE`/CAS 一律映射成 `SeqCst`。C++ 里的普通读（如 `iter->prev_`）在 Rust 里必须改成原子读，默认也用 `SeqCst`；要降级就加 `PERF(port)` 标记并经过评审。

**Rust 草图**

```rust
// storage-core/src/mvcc.rs：机制层；MVCC 规则在 storage crate
#[repr(C)]
pub struct TxNode {
    pub tx_id: i64,                // 只存 ID，状态去事务表反查（ob_mvcc_row.h:85）
    pub trans_version: AtomicU64,  // SCN
    pub scn: AtomicU64,
    pub seq_no: i64, pub write_epoch: i64,
    pub tx_end_scn: AtomicU64,
    prev: AtomicPtr<TxNode>,       // 更旧
    next: AtomicPtr<TxNode>,       // 更新
    pub modify_count: u32, pub acc_checksum: u32,
    pub version: i64, pub snapshot_version_barrier: AtomicI64,
    pub kind: u8, flag: AtomicU8,
    data: [u8; 0],                 // 扁平行，和节点一起在 arena 里分配
}
#[derive(Clone, Copy)]
pub struct NodeRef<'m>(&'m TxNode);           // 'm 来自 MemtableGuard
impl<'m> NodeRef<'m> {
    pub fn prev(self) -> Option<NodeRef<'m>> {
        // SAFETY: 节点只随 memtable 整体释放（ob_mvcc_engine.cpp:452），
        // 而 'm 不会长于持有该 memtable 的 guard。
        unsafe { self.0.prev.load(Ordering::SeqCst).as_ref() }.map(NodeRef)
    }
}
pub struct RowGuard<'m> { row: &'m MvccRow }  // 持有行自旋锁；只有它能拼接和摘除
impl<'m> RowGuard<'m> {
    pub fn push_head(&self, n: NodeRef<'m>) { /* ob_mvcc_row.cpp:968-972 */ }
    pub fn insert_by_scn(&self, n: NodeRef<'m>) -> Option<NodeRef<'m>> { /* :547-686 */ }
    pub fn unlink(&self, n: NodeRef<'m>) { /* :431-502 */ }
}

// 回调：裸指针加释放协议，与 C++ 相同
pub struct RowCallback {
    link: CbLink,                  // 侵入式 prev/next（AtomicPtr），由核心 crate 提供
    row: NonNull<MvccRow>,         // SAFETY: memtable 销毁前会摘掉所有指向它的回调（ob_memtable.cpp:166-181）
    tnode: Option<NonNull<TxNode>>,
    memtable: MemtablePtr,
    key: MemtableKey, seq_no: TxSeq, scn: Scn,
}
pub enum TxCallback { Row(RowCallback), TableLock(ObjLockCallback), ExtInfo(ExtInfoCallback) }
```

**优点**
- 算法、回收时机、内存占用、keybtree 的切分和估计都与 C++ 一致，对齐风险最低。
- unsafe 有清晰的边界：crate 边界就是审计边界，核心可以用 `loom`（并发模型检查）和 Miri 测试。
- 性能与 C++ 同构。ARM 上 `prev` 从普通读变成 `SeqCst` 读（`ldar` 指令），估计开销很小。

**缺点**
- 核心必须先手写并冻结 API，然后才能放开 memtable/tx 的批量翻译，形成排期依赖。
- keybtree 的 epoch 和节点池之间的交互，是全项目最难的一段 unsafe（2,757 行 C++）。

**unsafe 用量（估计）**
- keybtree 基本整段（约 2.8K 行 C++）、`ObMvccRow` 的链表操作（约 0.4K 行）、回调链接（约 0.2K）、arena（约 0.3K）、palf 组缓冲（约 0.1K）。
- 合计约 4K 行 C++ 对应的代码，150–300 个 `unsafe` 块，约占本主题的 1–2%。
- 对比直译基线：本主题 39.6% 的函数需要 unsafe（2.1 节）。

**对翻译和对齐的影响**
- 消费方大约 900 个文件，规则是机械的：
  - `x->prev_` 改成 `x.prev()`；
  - 改链只能通过 `RowGuard`；
  - 不允许持有裸 `*mut TxNode`。
- 核心约 10 个 manifest 单元，不进批量流水线：`ob_mvcc_row`、`ob_row_latch`、`ob_keybtree` 及 deps、`ob_tx_callback_list` 加 `ob_mvcc`、`ob_memtable_data`、memstore 分配器、`log_group_buffer`、`lsn_allocator`。

**编译**：核心 crate 小，泛型只实例化一份（K/V 各一种类型），能和其他 crate 并行编译；`crossbeam-epoch` 是很小的依赖。

### 选项 B：用 `Arc` 句柄替换手写引用计数

**做法**
- 管内存对象的引用计数，一律改成 `std::sync::Arc`：
  - `ObTableHandleV2` 改成 `TableHandle(TableRef)`。`TableRef` 是闭合的 enum，成员是 memtable、sstable、ddl kv、tx data memtable、tx ctx memtable、lock memtable（与现有类层次一致）。
  - `ObTabletHandle` 改成 `TabletHandle { tablet: Arc<Tablet>, hold_start, wash_priority }`。
  - `ObTxDataGuard` 改成 `Arc<TxData>`。
  - `ObLightHashMap` 改成同样的桶数组：每个桶一把 `RwLock`，桶里放 `Vec<Arc<V>>`；tx ctx 表仍是 1<<14 个桶、每桶一把锁（C++ 的锁数默认等于桶数，`ob_light_hashmap.h:104`）。
  - `ObTxDesc` 改成 `Arc<TxDesc>`，对外仍然是 data_plane 的不透明句柄。
- 会被整体替换的槽位用 `arc-swap`：`ObTabletPointer::obj_`、表存储。
- "归零以后做什么"放进 `Drop`：
  - `impl Drop for Memtable` 把内部的 `Box<MemtableInner>` 移交给 t3m 的 GC 队列，与 `ob_i_table.cpp:188` 一致。Box 移交后 arena 地址不变，回调里的裸指针依然有效。
  - 随 tablet 生命周期的 sstable 直接由 `Arc` 保活，不再需要 `lifetime_guaranteed_by_tablet_`。
- 磁盘宏块计数不是内存所有权，保持 `MacroBlockId → AtomicI64` 的计数表；句柄的 `Drop` 调用 `dec_ref`。
- 在途的日志回调持有 `Arc<TxCtx>`，代替 `acquire_ctx_ref_`/`release_ctx_ref_`。

**Rust 草图**

```rust
pub enum TableRef { Memtable(Arc<Memtable>), SSTable(Arc<SSTable>), DdlKv(Arc<DdlKv>),
                    TxData(Arc<TxDataMemtable>), TxCtx(Arc<TxCtxMemtable>), Lock(Arc<LockMemtable>) }
impl Drop for Memtable {   // 对应 ObTableHandleV2::reset 里的 push_table_into_gc_queue
    fn drop(&mut self) { if let Some(inner) = self.inner.take() { t3m().push_memtable_gc(inner) } }
}
pub struct TabletPointer { obj: ArcSwapOption<Tablet>, addr: Mutex<MetaDiskAddr> /* … */ }
```

**优点**
- 零 unsafe。clone 和 drop 各是一次原子加减，与 C++ 的 `inc_ref`/`dec_ref` 相同。
- 改写是机械的：拷贝句柄改成 `clone`，`reset()` 改成 drop 或赋 `None`，`get_obj()` 改成 `&*h`。

**缺点**
- 169 处手动 `inc_ref`/`dec_ref` 要逐处改写。例如 `ObTablet::inc_macro_ref` 是磁盘计数，不能改成 `Arc`。
- 对象池（tablet 池、tx ctx 池）没法复用 `Arc` 的内存。首版应去掉池，但保留数量上限和错误码；按 ctx 计量内存，需要内存主题的分配器支持。
- tablet 自带的 arena（`ob_tablet.h:760-789`）在安全 Rust 里是自引用结构，要改成 `Box`/`Arc` 拥有的字段（或者用 `self_cell`）。这会改变 tablet 内存的计量粒度。
- 翻译者容易多 clone。规则要写明：作用域内借 `&T`，只有 C++ 拷贝句柄的地方才 `clone`。

**unsafe**：0（`Arc` 和 `arc-swap` 都是安全 API）。

**对齐**：析构时机不变，都在最后一个句柄释放时；GC 线程的语义由 `Drop` 移交来保持。

### 选项 C：slab/索引节点存储

**做法**：节点放进 slab（`slab` crate，sql-nio 已经在用）或按段增长的追加式数组，链接改用 `u32` 索引。

**适合的地方**
- **MDS**：`UserMdsNode` 同时在行链和事务链上（`mds_node.h:181`）。改成 `Slab<MdsNode>` 加两组索引链接，外面一把表级 `Mutex`。6 种单元（`mds_register.h`）用 enum 擦除类型，不再生成模板组合。
- **表锁**：`ObOBJLock` 里的 `ObDList<ObTableLockOpLinkNode>`（`ob_obj_lock.h:172-194`）改成 `RwLock<VecDeque<…>>`。
- **事务内的在途日志回调**（`busy_cbs_`）和日志回调池：改成 `Vec` 加索引。

**不适合的地方**
- **MVCC 版本链**：
  - 节点是变长的（`buf_[0]`），slab 要求定长。拆成"定长头 + 字节 arena"之后，每读一行多一次间接。
  - 热点行的链很长：回放索引在 500 跳时才触发（`ob_mvcc_row.h:275`）。
  - 并发增长的段式数组，读槽位时也要 unsafe，只是藏进了 crate。
  - memtable 节点本来就不单独释放，用索引防悬垂的收益很小。
- **回调链表**：`Slab::insert` 需要 `&mut`，等于让追加和填日志互斥，丢掉了 C++ 用三把锁换来的并发（2.3 节）。

**Rust 草图（MDS）**

```rust
pub struct MdsTable { inner: Mutex<MdsTableInner> }
struct MdsTableInner { nodes: Slab<MdsNode>, rows: BTreeMap<MdsKey, RowList> }
struct MdsNode { row_prev: Option<u32>, row_next: Option<u32>, ctx_prev: Option<u32>, ctx_next: Option<u32>,
                 status: MdsNodeStatus, writer_id: i64, seq_no: TxSeq, redo_scn: Scn, end_scn: Scn,
                 trans_version: Scn, value: MdsValue /* enum，6 种单元 */ }
```

- **unsafe**：MDS 和表锁为 0。MVCC 如果用这个办法，unsafe 只是挪进了段式 arena（约 200 行），keybtree 仍然需要另一套回收。
- **性能**：MDS 和表锁的吞吐低，影响可以忽略。MVCC 的读路径多一次间接，估计热点行扫描会有可测的退化。
- **编译**：MDS 把模板换成 enum 后，编译量明显下降（现在每千行 35.2 个模板）。

### 选项 D（否决）：逐行 unsafe 直译，或者用现成的 crate 替换 keybtree

- **逐行直译**：本主题 39.6% 的函数体需要 unsafe，memtable 是 52.3%。结果就是"用 unsafe 写的 C++"，与可行性报告的判断 1 一致。
- **用 `crossbeam-skiplist` 之类替换 keybtree**：切分点和行数估计都会变（2.2 节），进而影响并行扫描的切分和优化器的选择；没有 ORDER BY 的查询，结果顺序和执行计划都可能不同。跳表的扫描也比 B 树慢。为此承担对齐风险不值得。

## 4. 推荐

| 结构 | 做法 | unsafe |
|---|---|---|
| MVCC 版本链、`MvccRow`、行锁 | A：arena + `AtomicPtr`，整个 memtable 一起回收，经 `NodeRef`/`RowGuard` 访问 | 核心内 |
| keybtree | A：按原算法移植，`crossbeam-epoch` 代替 QClock/RetireStation | 核心内 |
| 事务回调链表 | A：侵入式原子链接，保留三把锁的模式；回调是闭合 enum | 核心内（只有链接操作） |
| 回调指向行、节点、memtable | 裸指针，保留"销毁前先摘回调"的协议 | 核心内 |
| tx ctx 表、tx desc、表锁对象、tx data | B：`Arc` + 桶数组（每桶一把 `RwLock`） | 0 |
| tablet/table/sstable 句柄、t3m | B：`Arc` + `arc-swap`，`Drop` 时移交 GC 队列 | 0 |
| 宏块引用 | 按 ID 的计数表 | 0 |
| MDS、表锁内的链表、日志回调池 | C：`slab` + 索引 + 锁，enum 擦除类型 | 0 |
| palf 滑动窗口 | 安全 Rust：槽数组 + `AtomicI64` + 槽锁；`SlotGuard` 的 `Drop` 就是 `revert` | 0 |
| palf 组缓冲、LSN 分配器 | `UnsafeCell<[u8]>` 上的不相交写；`portable_atomic::AtomicU128` | 少量 |

**理由**
1. **风险集中**：unsafe 只出现在 `storage-core` 一个 crate，约 10 个单元；其余约 500 个单元按机械规则翻译。
2. **对齐**：算法、回收时机、keybtree 的估计都保持不变；崩溃恢复的日志和检查点逻辑原样移植。
3. **性能**：热路径上的原子操作和 C++ 一一对应；没有引入新的共享计数热点（不让每条回调都持有 `Arc<Memtable>`）。

**前提**
- 核心 crate 先于批量翻译完成，API 冻结，并带 loom/Miri 测试。
- 内存主题提供并发的 memtable arena（含 memstore 限流计量），以及能按 ctx 计量的全局分配。
- 约定主题的序列化 derive 宏能逐字节复现 unis 格式。不论第 5 节问题 1 怎么选，Rust 自己的持久化也要用它。
- 迁移单元按"共享指针"的簇划分。{memtable, tx, tx_table, tx_storage, tablelock, deadlock, concurrency_control} 约 10.4 万行，必须一起迁；palf 可以单独迁，因为它的接口只有字节和回调。

## 5. 需要人决定的问题

**问题 1：持久化格式——Rust 版是否必须读 C++ 版写的数据？**
- **A【推荐】兼容。**
  - 读写同一套格式（clog、slog、block_file、事务表），迁移期间冻结格式。
  - 裁判增加"C++ 写 → Rust 读"的正常重启和 kill -9 两个场景。
  - 前提：原地升级是产品要求，或者按可行性报告逐子系统迁移。
- **B 只读兼容。** Rust 能读旧数据，但写新格式（启动时转换，或新旧格式混存）。代价是两套编码长期并存。
- **C 不兼容。** Rust 版用自己的格式，升级靠导出再导入。只适用于一次性整体切换。

**问题 2：keybtree 退休节点怎样回收？**
- **A【推荐】** `crossbeam-epoch`，延迟闭包持有 `Arc<NodePool>`。内存行为与 C++ 一致。
- **B** 不回收：退休节点留在 memtable arena 里，随 memtable 一起释放。能去掉全部 epoch 代码，但 memtable 内存估计多 10–20%（窄行偏高），冻结和转储会更早发生。
  - 估计依据：每个节点约 272 字节；大约每 7 次插入发生一次分裂，留下约 2 个废节点。
- **C** 逐行移植 QClock/RetireStation。unsafe 更多，要自己维护。

**问题 3：回调怎样保证 memtable 不被释放？**
- **A【推荐】** 保留 C++ 的协议：回调持裸指针，memtable 销毁前摘掉所有指向它的回调。
- **B** 每个事务对它写过的每个 memtable 各持一个 `Arc`。可以去掉协议，但长事务会拖住已转储 memtable 的内存，memstore 用量和限流时机都会变。
- **C** 每条回调持一个 `Arc<Memtable>`。最简单，但所有写者会争抢同一个计数所在的缓存行。

**问题 4：unsafe 核心放在哪里？**
- **A【推荐】** 独立 crate `storage-core`；storage crate 加 `#![forbid(unsafe_code)]`。
- **B** 放在 storage crate 里的模块，用 `#[allow(unsafe_code)]` 白名单放行。

**问题 5：`ATOMIC_*` 和普通读怎样映射内存序？**
- **A【推荐】** 一律用 `SeqCst`；要降级必须带 `PERF(port)` 标记并经过评审。
- **B** 翻译时逐处推断 `Acquire`/`Release`。性能可能更好，但每一处都是一次并发正确性的判断，不适合批量翻译。

**问题 6：对象池（tablet、tx ctx、日志回调）怎么处理？**
- **A【推荐】** 首版去掉池，保留数量上限和错误码（如 `OB_TRANS_CTX_COUNT_REACH_LIMIT`），内存按 ctx 计量。出现性能回归再加回池，标 `PERF(port)`。
- **B** 保留池。需要自定义分配器、nightly 的 `allocator_api`，或者用 unsafe 原地复用。

## 6. 对其他主题的依赖或影响

**内存与分配器**
- memtable arena 必须是并发的、整块释放的、带 memstore 限流计量的分配器（`ob_memstore_allocator.h:62-140`、`ob_fifo_arena.h`）。`bumpalo` 不能跨线程共享，需要移植 `ObFifoArena`，或者按线程分块。
- tablet 等元数据对象受 `META_OBJ_CTX_ID` 上限约束，tx data 受分配器限流。`Arc` 走全局分配器时，要能带上 ctx 标签计量。
- `ObTablet` 自带的 arena 要改为拥有字段，这要和"`'arena` 生命周期还是 id"的决定保持一致。

**并发**
- 行锁是 1 字节自旋锁；`CtxLock`、`ObByteLock`、桶锁都需要映射。
- `ObQSync` 被 keybtree、FIFO arena、锁等待、`ObLogHandler::ls_qs_` 共同使用，可以移植成安全原语：每 CPU 一个计数，等待它们归零。
- 如果采用 `crossbeam-epoch`，oblib 的 `ObLinkHashMap` 也应统一用它。用到它的地方有死锁检测（`ob_deadlock_detector_mgr.h:253`）和自增服务（`ob_tablet_autoincrement_service.h:181`）。
- 锁等待把请求本身当作等待节点，被唤醒后重新投递（`ob_lock_wait_mgr.h:187`），依赖 worker/请求模型的设计。
- 内存序规则（问题 5）是全项目的规则。

**分层**
- `AppendCb` 是 logservice 里的 trait，由 storage 实现，方向天然正确。
- `ObTxDesc` 已经有 data_plane 的不透明 API。可以在下层 crate 定义 `trait TransactionService` 和句柄类型，由 storage 实现、启动时注册。解掉 storage → observer（33 个符号）和 storage → rootserver（4 个）的逆向边，也是同一个办法。
- storage ↔ sql 横向有 271 和 99 个符号，需要定方向。

**SQL IR**：优化器读取 memtable 的行数估计和切分结果（来自 keybtree），所以 keybtree 必须按原算法移植。除此之外没有直接耦合。

**约定**
- 序列化 derive 宏的字节格式是问题 1 的关键。
- `ob_abort()`/`abort_unless` 改成记日志后 `std::process::abort()`，与现有的 `panic = "abort"` 一致。
- 现有 Rust 代码在 49 个 `unsafe fn` 上写了 `/// # Safety` 文档，但 136 个 `unsafe {}` 块都没有块级 `// SAFETY:` 注释（`grep -rnE '\bunsafe\s*\{' rust/*/src`）。核心 crate 要先立规矩。
- ERRSIM 注入点（如 `ob_tx_ctx.cpp:1511` 的 `EN_TX_ON_SUCCESS_DELAY`）要有统一的处理方式。

**解析器**：没有直接耦合。

**编译时间**
- MDS 的模板改成 enum。
- 核心 crate 独立，可以并行编译。
- storage 作为单个 crate，需翻译的部分约 48 万行；它的 33 个子目录里有 30 个连成一个环（`01-depmap-report.md` §3.4），要拆 crate 得先拆环。本主题建议只拆出 `storage-core`。
