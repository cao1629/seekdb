# 05 `containers`：`oblib::container`、`oblib::hash`、`oblib::queue` 的 API

> - **依据**：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）0.6、0.7、0.8、0.9、0.11.3–4、§1（容器、并发容器两行）、§2.5、§2.7、§3 第 3 项、A.3（`operator[]`）、A.4；`migration/infra/README.md`（1d）。事实来源另有 `migration/design/survey/{conventions,concurrency,memory}.md`、`migration/design/review/concurrency-performance.md`（F2、F3、F15、M1、M11）、`behavioral-fidelity.md`（F6、F7）、`verification.md`（C07、C08、C23、C24、C26、C28）。
> - **基线**：`7d907abfa`。日期 2026-10-11。只读源码写成，没有构建项目。
> - **计数口径**：凡写"约 N 处"，都是在 `migration/depmap/nodes.tsv` 的 6,509 个节点上、去掉注释和字符串字面量内容后，用正则数出的出现次数（脚本在 `/tmp/c05-survey/count.py`，按规则不入库）；"外部 include"取自 `migration/depmap/edges.tsv`，不含同目录。正则计数都是近似值。
> - **写法约定**：代码块里 inherent 方法以 `;` 结尾表示省略函数体；trait 的默认方法体写成 `{ … }`。`ObResult`、`ObError`、`OB_*` 来自 `oblib::error`（04 号）。每个类型、方法旁的 `// C++:` 注释给出对应的 C++ 名字和位置。
> - **已核对的语言事实**：§2 的三种形态（同名方法按锁模式分两个互斥 inherent impl 块；`dyn ObIArray<T>` 带 `where T: Clone` 方法仍可做 trait 对象；新类型 `Deref<Target=[T]>` 时 `count()`/`at()`/`remove()` 解析到 trait 方法而不是切片固有方法）用 1.98.1 编译过两个独立的小样例（`/tmp/c05-survey/rs/probe{1,2}.rs`）；修订 A1 说的"`Vec` 的固有方法遮蔽 trait 方法"用第三个样例验证过（`probe3.rs`）。这些样例不属于项目，不入库。

## 1. 范围

### 1.1 处置判据

| 处置 | 判据 |
|---|---|
| `replace:container` / `replace:hash` / `replace:queue` | RULEBOOK §2.5、§2.7 点名的类型，以及与它们同族、所有权或并发语义需要重新设计、不能按通用规则逐行翻译的类型。1d 在 `rust/port/oblib/src/{container,hash,queue}/` 实现；单元的目标路径（按 §4，`src/oblib/lib/<dir>/<name>.h` → `rust/port/oblib/src/lib_m/<dir>/<name>.rs`，`lib` 撞 crate 根文件名所以加 `_m`）由 1d 写**桩文件**：文件头 `//! Disposition: <处置>`，正文只有 `pub use crate::{container,hash,queue}::…;`，文件尾是 RULEBOOK §6 格式的状态行（RULEBOOK §4 的"非翻译单元的桩文件"） |
| `translate` | 是算法或语言构造、不涉及所有权重设计的单元：第 3 步按通用规则逐行翻译，可以使用本模块的类型 |
| `delete` | 在基线上没有实例化或没有 include 的死代码，或被 `replace` 吸收后不再需要的实现细节。RULEBOOK 0.8 已点名的直接写 `delete`；其余由缺口清单第 16 类确认（证据见表） |

### 1.2 `src/oblib/lib/container/`（30 个单元）

| 单元 | 行数 | 外部 include | 主要 C++ 名字 | 处置 | 理由 / 证据 |
|---|---|---|---|---|---|
| `ob_iarray.h` | 150 | 114 | `ObIArray<T>`、`is_contain`、`get_difference`、`append`、`is_array_equal`、`has_to_string<T>` | replace:container | §2.5；`ObIArray` 是全库最主要的数组参数类型（约 18,141 处） |
| `ob_array_wrap.h` | 155 | 11 | `ObIArrayWrap<T>`、`ObArrayWrap<T>` | replace:container | `ObIArray` 的只读基类（:32-69）；`ObArrayWrap` 约 142 处 |
| `ob_array.h` | 869 | 130 | `ObSEArrayImpl`、`ObSEArray`、`ObArray`、`ObClassOp`、`ObNullAllocator`、`ObSEArrayLocalSize` | replace:container | §2.5；`ObSEArray<` 约 5,621 处，`ObArray<` 约 2,449 处 |
| `ob_se_array.h` | 21 | 181 | 转发头 | replace:container | 只 `#include "ob_array.h"` |
| `ob_raw_se_array.h` | 595 | 4 | `ObRawSEArray` | replace:container | 只给平凡类型用的 `ObSEArray` 变体（:29-37）；唯一实例 `ob_row_queue.h:99`。Rust 是 `ObSEArray` 的别名 |
| `ob_fixed_array.h` | 341 | 39 | `ObFixedArrayImpl`、`ObFixedArray` | replace:container | §2.5、0.6；约 424 处；定容语义（`OB_SIZE_OVERFLOW`）要保留 |
| `ob_array_serialization.h` | 146 | 18 | `ObSArray` 别名、`ObFixedArray`/`Ob2DArray` 的序列化 | replace:container | `ObSArray` 约 414 处（:31 就是 `ObArray`） |
| `ob_array_iterator.h` | 152 | 14 | `array::Iterator` | replace:container（吸收） | Rust 用切片迭代器 |
| `ob_se_array_iterator.h` | 191 | 5 | `array::ObSEArrayIterator` | replace:container（吸收） | 同上 |
| `ob_fixed_array_iterator.h` | 53 | 4 | `ObFixedArrayImpl` 的迭代器 | replace:container（吸收） | 同上 |
| `ob_fast_array.h` | 263 | 5 | `ObFastArray<T, N=50>` | replace:container | 带本地缓冲的数组（:34-133），不是 `ObIArray`，接口是 `ObSEArray` 的子集（§2.2）；库外约 8 处，3 个文件（`ob_result_set.h`、`ob_resolver_define.h`、`ob_expr_obj_access.h`） |
| `ob_array_helper.h` | 253 | 3 | `ObArrayHelper<T>`、`ObArrayHelpers<T>` | replace:container | `ObArrayHelper` 是写进外部缓冲的 `ObIArray`（:28-182）；`ObArrayHelpers` 在库外没有使用者（grep 0），不移植 |
| `ob_array_array.h` | 426 | 7 | `ObArrayArray<T, L=8, A=8>` | replace:container | 数组的数组，内层是 `ObSEArray`（:37-46）；约 38 处 |
| `ob_2d_array.h` | 653 | 15 | `Ob2DArray`、`ObSegmentArray` | replace:container | 分段数组（:31-124），约 50 处 |
| `ob_vector.{h,ipp}` | 298+596 | 40 | `ObVector<T>`、`ObSortedVector<T>` | replace:container | §2.5；约 69 + 49 处；`lower_bound` 语义照旧 |
| `ob_bit_set.h` | 1060 | 25 | `ObSegmentBitSet<N>`、`ObFixedBitSet<N>`、`ObBitSet<N>` | replace:container | §2.5 位集；`ObBitSet<` 约 212 处 |
| `ob_ext_ring_buffer.h` | 208 | 1 | `ObExtendibleRingBuffer<T>` | replace:container | 并发结构（无锁、共享 hazard），唯一使用者 `ob_change_stream_dispatcher.h:207`；按 0.8"并发容器一律用 oblib 封装" |
| `ob_ext_ring_buffer_impl.h` | 858 | 0 | `erb::*` | replace:container（吸收） | 只被上一行 include |
| `ob_bitmap.{h,cpp}` | 249+922 | 9 | `ObBitmap` | **translate** | 名为 bitmap，实为"每行一字节"的过滤结果向量（`data_[pos] = value`，:236 起），带 SIMD 批量算子（`filter`、`get_row_ids`、`to_bits_mask`）；不是 §2.5 的位集（位集按 32 位字存）。原始指针参数按 §2.4 改切片，SIMD 按 §2.13 进 `simd` 模块 |
| `ob_heap.h` | 328 | 12 | `ObBinaryHeapBase`、`ObBinaryHeap` | **translate** | RULEBOOK 0.11.3："`ObBinaryHeap` 按原代码移植"；纯算法，需要金标准测试 |
| `ob_loser_tree.h` | 582 | 6 | `ObRowsMerger`、`ObLoserTree` | **translate** | 归并算法，等值键的出队顺序进入合并结果；纯算法，配金标准测试 |
| `ob_tuple.h` | 467 | 10 | `ObTuple<…>`、`get_impl` 等 | **translate** | 变参模板元编程（MDS 的 `unit_tuple_.for_each`）；不是所有权问题。RULEBOOK §2.13 没有变参模板的专门译法，建议按"模板 → 泛型加 trait"为基线用到的元数用 `macro_rules!` 生成元组实现，由第 3 步决定 |
| `ob_concurrent_bitset.h` | 279 | 1 | `ObConcurrentBitset` | delete | 唯一 include 者 `ob_sql_session_mgr.h` 不使用它（grep `ConcurrentBitset` 只命中 include） |
| `ob_id_map.h` | 298 | 2 | `ObIDMap` | delete | 唯一实例化是 `ob_memtable_context.h:310` 的 typedef `MemtableIDMap`，该 typedef 无使用者；`ob_resource_pool.h:25` 只 include |
| `ob_int_flags.h` | 87 | 1 | `ObInt64Flags` | delete | `ob_join_order.h` 只 include，`ObInt64Flags` 全库 0 处使用 |
| `ob_rbtree.h` | 981 | 1 | `ObRbTree`、`ObRbNode` | delete | `ob_io_define.h` 只 include，`ObRbTree`/`ObRbNode` 全库 0 处使用 |
| `ob_mask_set2.h` | 178 | 0 | `ObMaskSet2` | delete | 无 include |
| `ob_paringheap.h` | 530 | 0 | `ObParingHeap` | delete | 无 include |
| `ob_ring_array.h` | 153 | 0 | `ObRingArray` | delete | RULEBOOK 0.8 点名的死代码 |
| `ob_ring_buffer.h` | 343 | 0 | `ObRingBuffer` | delete | 只被 `ob_ring_array.h` include |

### 1.3 `src/oblib/lib/hash/`（27 个单元）

| 单元 | 行数 | 外部 include | 主要 C++ 名字 | 处置 | 理由 / 证据 |
|---|---|---|---|---|---|
| `ob_hashmap.h` | 378 | 99 | `ObHashMap`、`HashMapTypes`、`hashmap_preproc` | replace:hash | §2.5、0.11.3 逐行移植；`ObHashMap<` 约 485 处 |
| `ob_hashset.h` | 239 | 41 | `ObHashSet`、`HashSetTypes` | replace:hash | 同上；`ObHashSet<` 约 204 处 |
| `ob_hashtable.h` | 1748 | 3 | `ObHashTable`、桶、迭代器、`ObBucketLockCond` | replace:hash | 上两者的实现；库外 0 处实例化 |
| `ob_hashutils.{h,cpp}` | 1582+33 | 18 | 6 种 defend mode、各 locker、`hash_func<K>`、`equal_to<K>`、`cal_next_prime`/`PRIME_LIST`、`HashMapPair`、`HashNullObj`、`pair_first`、`SimpleAllocer`、`BigArray`/`NormalPointer` | replace:hash | §2.5 的"6 种锁模式作为类型参数"；`hash_func` 特化表决定桶序（0.11.3） |
| `ob_serialization.h` | 246 | 0 | `hash::serialization`/`deserialization`（archive） | delete（吸收） | `ObHashMap::serialization()`/`deserialization()` 全库 0 处调用 |
| `mprotect.h` | 49 | 0 | `MProtect`、`MProtectGuard` | delete（吸收） | `#if 0`（:22），实际是空结构 |
| `ob_link_hashmap.{h,cpp}` | 617+31 | 12 | `ObLinkHashMap`、`ObAllocatingLinkHashMap`、`RefHandle`/`ZeroRefHandle`、`ObILinkHashMap` | replace:hash | §2.7 并发容器表的 `ObLinkHashMap` 行；6 个声明（时区 2、自增 2、用户资源 1、死锁检测 1），另有会话表的 2 个 `ObAllocatingLinkHashMap` typedef（`ob_sql_session_mgr.h:171,174`） |
| `ob_link_hashmap_deps.h` | 127 | 0 | `RefNode`、`LinkHashNode`、`LinkHashValue`、`AllocHandle`、`CountHandle`、`DCArrayAlloc` | replace:hash（吸收） | `Arc` 取代 uref/href |
| `ob_dchash.{h,cpp}` | 454+31 | 0 | `DCHash`、`DCArray`、`HashNode`、`KeyHashNode` | replace:hash（吸收） | 只移植排序键 `(key.hash()\|2)&~1`、`key.compare()`（:181-192），供 `for_each` 排序（0.11.4、C23） |
| `ob_darray.h` | 811 | 0 | `IArrayAlloc` 等 | delete（吸收） | 只被 `ob_dchash.h`、`ob_hash.h` include |
| `ob_linear_hash_map.{h,cpp}` | 2507+25 | 13 | `ObLinearHashMap`、`BlurredIterator`、`ShareMemMgrTag`/`UniqueMemMgrTag` | replace:hash | §2.7、0.9；约 10 个声明 |
| `ob_fixed_hash2.h` | 125 | 2 | `FixedHash2<Node>`、`SpHashNode` | replace:hash | §2.7；唯一实例 `ob_lock_wait_mgr.h:188` |
| `ob_hash.h` | 791 | 0 | `HashBase`、`HashRoot` | delete（吸收） | 只被 `ob_fixed_hash2.h` include，`FixedHash2` 不用其中任何名字（:38-120）；HazardRef 不移植（0.8） |
| `ob_placement_hashmap.h` | 175 | 5 | `ObPlacementHashMap<K, V, N>` | replace:hash | 定长开放寻址；sql 和 storage 约 31 处（与下一行合计） |
| `ob_placement_hashset.h` | 98 | 8 | `ObPlacementHashSet<K, N>` | replace:hash | 同上 |
| `ob_placement_hashutils.h` | 111 | 0 | `placement_hash_search` 等 | replace:hash（吸收） | 上两者的探测函数 |
| `ob_iteratable_hashset.h` | 190 | 1 | `ObIteratableHashSet<K, N>` | replace:hash | `ObTxIDSet`（`ob_obj_lock.h:164`）；按插入序迭代 |
| `ob_iteratable_hashmap.h` | 271 | 1 | `ObIteratableHashMap` | delete | `ob_server_schema_service.h:22` 只 include（行尾注释写着类名），0 处实例化 |
| `ob_array_index_hash_set.h` | 159 | 3 | `ObArrayIndexHashSet` | replace:hash | `ob_row_desc.h:121` 一处 |
| `ob_pointer_hashmap.h` | 960 | 13 | `ObPointerHashArray`、`ObPointerHashMap` | replace:hash | schema 缓存约 29 处；墓碑、子表扩容，迭代序按槽位 |
| `ob_array_hash_map.h` | 258 | 3 | `ObArrayHashMap` | replace:hash | 唯一实例 `ob_hash_partitioning_infrastructure_op.h:821`；带锁的定长线性探测 |
| `ob_build_in_hashmap.h` | 250 | 1 | `ObBuildInHashMap` | replace:hash | 侵入式桶链（依赖 `ob_intrusive_list.h`）；唯一实例 `ob_load_data_impl.h:305` |
| `ob_cuckoo_hashmap.h` | 1155 | 2 | `ObCuckooHashMap` | delete | 两个 include 者（`ob_index_block_builder.h:20`、`ob_lob_manager.h:21`）都不使用（grep `uckoo` 只命中 include） |
| `ob_ptr_array_map.h` | 245 | 1 | `ObPtrArrayMap`、`ObLinkArray` | delete | `ob_schema_store.h` 只 include，0 处使用 |
| `ob_multi_mod_ref_mgr.h` | 171 | 0 | `ObMultiModRefMgr`、`ObRef` | delete | 无 include、无使用 |
| `ob_refered_map.h` | 202 | 0 | `ObReferedMap` | delete | 无 include |
| `fnv_hash.h` | 67 | 1 | `fnv1_32_and_fnv1a_32_compile_time_hash`、`fnv_hash_for_logger` | replace:hash | 本目录里的哈希函数；04 号的日志模块（`ob_log_module.h:24`）在 1d 就要用；`constexpr` → `const fn`，配金标准（§5 G3） |

### 1.4 `src/oblib/lib/queue/`（7 个单元）

| 单元 | 行数 | 外部 include | 主要 C++ 名字 | 处置 | 理由 / 证据 |
|---|---|---|---|---|---|
| `ob_fixed_queue.h` | 414 | 16 | `ObFixedQueue<T>`、`ObOrderedFixedQueue<T>` | replace:queue | §2.7（内部 `ArrayQueue`） |
| `ob_lighty_queue.{h,cpp}` | 117+254 | 5 | `ObLightyCond`、`ObLightyQueue`、`LightyQueue` | replace:queue | §2.7、F15、C26；`ObLightyCond` → 02 号的 `FutexCond` |
| `ob_priority_queue.h` | 304 | 5 | `ObPriorityQueueImpl`、`ObPriorityQueue`、`ObPriorityQueue16`、`ObTLinkQueue16`、`ObPriorityQueue2` | replace:queue | §2.7、F15、C26 |
| `ob_link_queue.{h,cpp}` | 300+70 | 12 | `QLink`、`ObSpScLinkQueue`、`ObSpLinkQueue`、`ObLinkQueueImpl`、`ObLinkQueue`、`ObLinkQueue16`、`ObSimpleLinkQueue`、`faa_bounded` | replace:queue | §2.7；见修订 A4（§8 Q8；内部不能是 `SegQueue`） |
| `ob_link.h` | 330 | 17 | `ObLink`、`ol_*`、`ObDLink`、`dl_*`、`link_next` 等 | replace:queue | `ObLink`/`QLink` 作为入队对象的基类一律删除（§2.17）；`ol_*`/`dl_*`/`link_next` 无锁原语不提供给翻译代码。基线上在本目录之外直接用它们的只有 01 号的 `ob_simple_fifo_alloc.h`（`dl_*`）和 08 号的 KV cache（`ob_kvcache_store.cpp`、`ob_kvcache_hazard_pointer.h`、`ob_kvcache_struct.h`），由这两个模块各自在内部实现；`ob_lock_wait_mgr.cpp` 的 `link_next` 由 `FixedHash2` 的 `lower_bound`/`quick_next` 取代（§6 例 11） |
| `ob_ms_queue.{h,cpp}` | 78+115 | 0 | `ObMsQueue` | delete | RULEBOOK 0.8 点名 |
| `ob_co_seq_queue.h` | 138 | 0 | `ObCoSeqQueue` | delete | RULEBOOK 0.8 点名（只被 `ob_ms_queue.h` 用） |

### 1.5 `src/oblib/lib/list/`（7 个单元）

| 单元 | 行数 | 外部 include | 主要 C++ 名字 | 处置 | 理由 / 证据 |
|---|---|---|---|---|---|
| `ob_dlist.h` | 560 | 73 | `ObDList<T>`、`DLIST_FOREACH*`、`DLIST_REMOVE_ALL*`、`ObDlistDeserialzeHelper` | replace:container（`container/intrusive`） | §2.5 侵入式链表；约 155 处 `DLIST_FOREACH*` |
| `ob_dlink_node.h` | 167 | 18 | `ObDLinkBase<T>`、`ObDLinkNode<T>`、`ObDLinkDerived<T>` | replace:container（`container/intrusive`） | 同上；`ObDLinkBase<` 约 93 处 |
| `ob_list.h` | 571 | 20 | `ObList<T, Alloc>`、`list::Node`、`list::Iterator`/`ConstIterator` | replace:container | §2.5；约 56 处；迭代器稳定性见修订 A2（§8 Q8） |
| `ob_obj_store.h` | 151 | 6 | `ObObjNode<T>`、`ObObjStore<T>` | replace:container | 按插入序登记对象（DAS、逻辑计划、raw expr 的工厂），`ObObjStore<` 约 17 处，10 个文件 |
| `ob_intrusive_list.h` | 481 | 1 | `SLink`、`Link`、`DLL` 等 | delete（吸收） | 只被 `hash/ob_build_in_hashmap.h` include，后者被取代 |
| `ob_atomic_list.h` | 302 | 0 | `AtomicSLL` 等 | delete | 只被 `ob_intrusive_list.h` include；只支持 x86_64 的高位指针标签（:27-35） |
| `ob_link.h`（list/） | 206 | 0 | `ObSLink`、`ObDLink` | delete | 无 include |

### 1.6 目录之外、由本模块承接或约束的构造

| 构造 | 位置 | 本模块怎么承接 | 处置（单元归属） |
|---|---|---|---|
| `murmurhash`、`fnv_hash2`、`wyhash` | `src/oblib/lib/hash_func/{murmur_hash.{h,cpp},wyhash.h}`（78+68、113 行） | `hash_func<ObString>` 就是 `murmurhash(ptr, len, 0)`（`ob_hashutils.h:773-780`）；本模块直接调用，并在 `oblib::hash` 里再导出 `murmurhash`（09 号文档按 `oblib::hash::murmurhash` 引用） | translate，**1d 期间由 06 号提前翻译**（06 文档 §1、§8 Q8，金标准 T-HASH-1）；见 §8 Q3 |
| `do_hash`/`do_equal` | `src/oblib/lib/hash_func/ob_hash_func.h`（73 行） | 键类有 `hash()` 成员时调用它，否则对键对象的内存字节做 `murmurhash`（:28-62）；placement 系、`ObPointerHashMap`、`ObArrayHashMap` 用它。Rust 写成 trait `DoHash`（§2.9） | 建议 replace:hash（06 文档列为 translate，并注明"05 决定哈希容器怎么接"），见 §8 Q3；`xxhash.{h,c}` 按 0.14 用 `xxhash-rust` |
| `ObLink`/`QLink`/`ObSpLinkQueue::Link` 作为基类（入队对象），以及 `ObLink*` 作队列元素 | 直接继承的 15 个类，库外 11 个（`ob_request.h:44`、`ob_tx_ctx.h:103`、`ob_dtl_linked_buffer.h:221`、`ob_server_runtime.h:97`、`ob_keybtree_deps.h:183`、两个 `TableGCItem`、两个 DBMS 作业键、`ob_tmp_file_flush_ctx.h:248`、`ob_tmp_file_thread_job.h:30`）；`ObLink` 一词共出现在约 52 个文件（如 `ob_retry_queue.h:37` 的出队参数、`ob_append_callback.h:45-47` 的成员链接） | 基类删除，对象以 `Box<T>`（单一归属）或 `Arc<T>`/id（多重归属，清单第 19 类）入队 | translate（各自单元） |
| `LinkHashValue<K>` 基类、`LinkHashNode<K>`、自定义 `AllocHandle` | `ob_timezone_info.h:532-535`、`ob_autoincrement_service.h:99`、`ob_sql_session_info.h:178-179`、`ob_user_resource_mgr.h:64-65`、`ob_deadlock_detector_*`、`ob_tablet_autoincrement_service.h:51,117` | 基类删除（`Arc` 计数取代 uref/href）；自定义分配器的副作用（对象池、计数、`clean_sessions`）移到值类型的构造和 `Drop` | translate；types.tsv 不为这些基类生成 `base` 字段（修订 A6） |
| `SpHashNode` 基类 | `ob_lock_wait_node.h:105` | 节点保留自己的 `hash_` 字段，实现 trait `SpHashNode`（§2.14） | translate |
| `ObDLinkBase<T>` 基类、`ObObjNode<T>` | 约 93 处 | 按 RULEBOOK §2.5：arena 里的链表改用 id 串、只读计划里的改 `Vec`、`ObExprOperator` 的这个基类直接去掉；其余改成一个类型为 `ObDLinkBase` 的字段（字段名由 types.tsv 按 §2.13 的基类规则给出，本文例子写 `dlink_`）加 `impl ObDLinkBaseVirt`（§2.8） | translate |
| `ObHashMap<…, NoPthreadDefendMode>` + 外部 `ObBucketLock` 分段 | `ob_resource_map.h:189-194`（含 `ObTabletPointerMap`）、`ob_tablet_stat_mgr.h:404-425`、`ob_tablet_id_set.h:31,73`、`ob_ddl_tablet_context.h:162-164` | `BucketedHashMap`（§2.11）：锁和表合成一个字段 | translate（这些类按 §2.11 改写） |
| 实现了 `ObIArray` 的类 | `ob_expr_operator.h:1781`（`ObConstCArray`）、`ob_fixed_meta_obj_array.h:39` | `ObConstCArray` 只为把几个值适配成 `const ObIArray&` 实参，Rust 直接写 `&[a, b]`；`ObFixedMetaObjArray` 在自己的 crate 里实现 `ObIArrayWrap`/`ObIArray`（trait 不 sealed） | translate |
| `SCondTemp<3>` | `ob_scond.h:131-215` | 02 号已提供 `oblib::sync::SCondTemp<PRIO>`（02 文档 §2.9），本模块的 `ObPriorityQueue2` 直接用 | replace:sync（02 号） |
| `ObLightHashMap` | `src/share/ob_light_hashmap.h` | 不归本模块：tx ctx 表按 0.9 改写 | translate / rewrite |
| SQL 引擎自带的哈希表、`ObSqlBitSet` | `ob_exec_hash_struct.h`、`ob_hash_partitioning_infrastructure_op.h`、`ob_raw_expr.h:243` | 不归本模块（§2.5"逐行移植"由翻译者做），可以用本模块的 `ObSegmentBitSet`、`ObArrayHashMap` | translate |

## 2. Rust API

### 2.0 通用约定（所有类型都适用）

**模块与导出**

```
rust/port/oblib/src/container/
  mod.rs            ObIArrayWrap、ObIArray、is_contain、get_difference、append、is_array_equal
  array.rs          ObArray、ObSArray、ObSEArray、ObRawSEArray、ObFastArray
  fixed_array.rs    ObFixedArray、ObFixedArrayStorage
  array_wrap.rs     ObArrayWrap、ObArrayHelper
  array_2d.rs       Ob2DArray、ObSegmentArray、ObArrayArray
  arena_impl.rs     impl ObIArrayWrap/ObIArray for oblib::mem::ArenaVec（01 号的类型）
  vector.rs         ObVector、ObSortedVector
  list.rs           ObList、ObListIter
  bit_set.rs        ObFixedBitSet、ObBitSet、ObSegmentBitSet
  obj_store.rs      ObObjStore
  ext_ring_buffer.rs ObExtendibleRingBuffer
  intrusive/mod.rs  ObDLinkBase、ObDLinkBaseVirt、ObDListPointer、ObDList、ObDLinkNode、游标（#[allow(unsafe_code)]，§3 白名单）
rust/port/oblib/src/hash/
  hashutils.rs      HashFunc、ObCStrKey、DefendMode 系、cal_next_prime、PRIME_LIST、HashMapPair、HashNullObj
  hash_func.rs      DoHash（C++ ob_hash_func.h）；pub use 06 号翻译的 murmurhash
  fnv_hash.rs       fnv1_32_and_fnv1a_32_compile_time_hash、fnv_hash_for_logger
  hashmap.rs        ObHashMap      hashset.rs   ObHashSet      bucketed.rs  BucketedHashMap 及四种 guard
  link_hashmap.rs   ObLinkHashMap、ObAllocatingLinkHashMap、LinkHashKey
  linear_hash_map.rs ObLinearHashMap、BlurredIterator
  fixed_hash2.rs    FixedHash2、FixedHash2Key、SpHashNode
  placement.rs      ObPlacementHashMap、ObPlacementHashSet、ObIteratableHashSet、ObArrayIndexHashSet
  pointer_hashmap.rs ObPointerHashMap   array_hash_map.rs ObArrayHashMap   build_in_hashmap.rs ObBuildInHashMap
rust/port/oblib/src/queue/
  fixed_queue.rs    ObFixedQueue、ObOrderedFixedQueue
  lighty_queue.rs   ObLightyQueue、LightyQueue
  link_queue.rs     ObSpScLinkQueue、ObSpLinkQueue、ObSimpleLinkQueue、ObLinkQueueImpl、ObLinkQueue、ObLinkQueue16
  priority_queue.rs ObPriorityQueueImpl、ObPriorityQueue、ObPriorityQueue16、ObTLinkQueue16、ObPriorityQueue2
```

- 全部类型随 crate 根 `pub use` 导出（§4），引用方写 `use oblib::ObSEArray;` 或经 prelude。
- **进 `oblib::prelude` 的**（交 06 号写进 prelude）：trait `ObIArrayWrap`、`ObIArray`、`HashFunc`、`DoHash`、`LinkHashKey`、`SpHashNode`、`ObDLinkBaseVirt`；类型 `HashMapPair`、`HashNullObj`。方法调用要靠 trait 在作用域里才能解析。

**C++ 模板参数和构造参数怎么写**（一条规则）：只保留元素、键值和锁模式参数，其余（分配器、标签、块大小、`auto_free`、哈希函子、相等函子、`EXTEND_RATIO`、`MemMgrTag`、`RefHandle`、`AllocHandle`、`SHRINK_THRESHOLD`、桶数组类型）一律删掉：

| C++ | Rust |
|---|---|
| `ObSEArray<T, N, Alloc, auto_free>` / `ObArray<T, Alloc, auto_free>` / `ObSArray<T>` | `ObSEArray<T, N>` / `ObArray<T>` / `ObSArray<T>`；查询作用域、对象本身在 bump arena 里的写 `ArenaVec<'q, T>`（§2.5、01 号） |
| `ObFixedArray<T, Alloc>` | 拥有型 `ObFixedArray<T>`；查询作用域 `ObFixedArray<T, ArenaVec<'q, T>>` |
| `ObHashMap<K, V, D, hash_func<K>, equal_to<K>, SimpleAllocer<…>, NormalPointer, ObMalloc, R>` | `ObHashMap<K, V, D>`（`D` 省略即 `LatchReadWriteDefendMode`） |
| `ObHashSet<K, D, …>` | `ObHashSet<K, D>`（`D` 省略即 `ReadWriteDefendMode`，同 C++ `ob_hashset.h:43`） |
| `ObLinkHashMap<K, V, AllocHandle, RefHandle, S>` / `ObAllocatingLinkHashMap<…>` | `ObLinkHashMap<K, V>` / `ObAllocatingLinkHashMap<K, V>` |
| `ObLinearHashMap<K, V, MemMgrTag>` | `ObLinearHashMap<K, V>` |
| `ObList<T, Alloc>`、`ObVector<T, Alloc>`、`ObSortedVector<T, Alloc>`、`ObObjStore<T, Alloc, auto_free>` | `ObList<T>`、`ObVector<T>`、`ObSortedVector<T>`、`ObObjStore<T>` |
| `ObBitSet<N, Alloc, auto_free>`、`ObSegmentBitSet<N, Alloc>`、`ObFixedBitSet<N>` | `ObBitSet<N>`、`ObSegmentBitSet<N>`、`ObFixedBitSet<N>` |
| `ObFixedQueue<X>`（存 `X*`） | `ObFixedQueue<T>`，`T` 是 Rust 的元素本身（`Box<X>`、`Arc<X>` 或 id），翻译者要写出来 |
| `ObPriorityQueue<P>`、`ObPriorityQueue2<H, N, L>`、`ObSpLinkQueue`、`ObLinkQueue` | `ObPriorityQueue<T, P>`、`ObPriorityQueue2<T, H, N, L>`、`ObSpLinkQueue<T>`、`ObLinkQueue<T>` |
| 构造参数（`block_size`、`allocator`、`label`、`ObMemAttr`、`ctx_id`） | 删掉，构造一律 `new()`；`set_attr`/`set_label`/`set_block_size`/`set_block_allocator`/`set_allocator`/`set_ctx_id` 的调用删掉（0.7） |

**接收者**（RULEBOOK §2.13 的具体化）

- **非同步容器**（全部数组、`ObVector`/`ObSortedVector`、`ObList`、位集、`ObDList`、`NoPthreadDefendMode` 的 `ObHashMap`/`ObHashSet`、placement 系、`ObPointerHashMap`、`ObBuildInHashMap`、`ObSimpleLinkQueue`）：只读 `&self`，其余（含 `init`/`create`/`destroy`/`reset`）`&mut self`。它们放进共享对象时，照 §2.7 放进 `Latch<T>`/`RwLatch<T>`，或用 `BucketedHashMap`。
- **内部同步容器**（加锁模式的 `ObHashMap`/`ObHashSet`、`BucketedHashMap`、`ObLinkHashMap`、`ObLinearHashMap`、`FixedHash2`、`ObArrayHashMap`、`ObExtendibleRingBuffer`、全部队列）：全部操作 `&self`，**包括 `init`/`create`/`destroy`**，所以可以直接作为模块的字段（0.5：模块的生命周期方法取 `&self`）。实现建议：可重建的内部存储放在 `ArcSwapOption<Inner>` 里（arc-swap），`destroy` 换成 `None`，在途操作持有的旧 `Arc` 保证不会悬垂；`create` 之前和 `destroy` 之后的操作返回 C++ 的 `OB_NOT_INIT`。
- **返回内部引用的方法**（`get`、`get_mut`、迭代器）：非同步容器 `&self`/`&mut self`；内部同步容器一律 `&mut self`（独占说明没有并发写者），共享时改用拷出（`get_refactored`）或闭包（`read_atomic`、`atomic_refactored`、`foreach_refactored`）。

**参数与输出参数**（§2.4 的具体化）

- 元素、键、值按值传入（C++ `const T&` 是拷贝语义）：调用方之后还要用就先 `.clone()`。
- C++ `T &out` → `&mut T`；C++ `T *out = NULL`（可空）→ `Option<&mut T>`；C++ `T *&out` → `&mut Option<…>`。
- C++ 指针类型的元素、值（`ObRawExpr *`、`ObTableSchema *`）按 §2.4/§5.3 的通用规则译成 `Option<Id>`、`Option<Arc<T>>`、`Option<&'q T>` 等；所以"取出旧值"这类输出参数写成 `&mut T` 就够了，`T` 本身可空。
- 队列的入队参数写成 `&mut Option<T>`：成功时取走（变成 `None`），失败时原样留给调用方，对应 C++"失败时指针仍归调用方"；`None` 对应 C++ 的 `NULL`，返回 C++ 的 `OB_INVALID_ARGUMENT`。

**重载改名**（本模块自定；overloads.tsv 对 replace 单元照抄本表，修订 A6）

| C++ 重载 | Rust |
|---|---|
| `at(idx)` / `at(idx, obj)` | `at` / `at_into`（RULEBOOK §2.5）；`const`/非 `const` 的 `at(idx)` → `at` / `at_mut`（§2.13） |
| `pop_back()` / `pop_back(T&)` | `pop_back` / `pop_back_into`（与 `at_into` 同一后缀约定："把元素写进调用方给的变量"） |
| `push_back(const T&)` / `push_back(const ObIArray<T>&)` | `push_back` / `push_back_i_array`（§4 规则） |
| `prepare_allocate(cap)` / `prepare_allocate(cap, args...)` | `prepare_allocate` / `prepare_allocate_with(cap, make)` |
| `ObHashMap::create` 的 5 个重载、`ObHashSet::create` 的 6 个重载 | 去掉标签、属性、分配器实参后合为一个 `create(bucket_num)` |
| `get(key) const` / `get(key)` | `get` / `get_mut` |
| `ObList::pop_front()` / `pop_front(T&)`、`erase(iterator)` / `erase(const T&)` | `pop_front` / `pop_front_into`、`erase` / `erase_value` |
| `ObVector::remove(iterator)` / `remove(int64_t)` / `remove(iterator, iterator)` | `remove` / `remove_index` / `remove_range` |
| `ObVector::remove_if(value)` / `remove_if(value, pred)` / `remove_if(value, pred, removed)` | `remove_if` / `remove_if_pred` / `remove_if_pred_into` |
| `ObSortedVector::find(value, pos, cmp)` / `find(value, pos, cmp, eq)`、`remove_if(v, cmp, eq)` / `remove_if(v, cmp, eq, removed)`、`remove(pos)` / `remove(start, end)` | `find` / `find_eq`、`remove_if` / `remove_if_into`、`remove` / `remove_range` |
| `ObLinearHashMap::erase(key)` / `erase(key, value&)` | `erase` / `erase_into` |
| `ObArrayArray::at(i, j)` / `at(i)` / `at(i, j, obj)` / `count()` / `count(i)` / `push_back(array)` / `push_back(i, obj)` | `at` / `at_array` / `at_into` / `count` / `count_array` / `push_back` / `push_back_obj` |
| `ObPointerHashMap::set_refactored(k, v, ow, owk)` / `set_refactored(k, v, old&, ow, owk)` | `set_refactored` / `set_refactored_into` |
| `ObLinkHashMap::revert(HashNode*)` / `revert(Value*)` | 只有 `revert(Arc<V>)` |
| `ObDList::move(list)` | `r#move`（`move` 是 Rust 关键字，用原始标识符保留 C++ 名；§4 的 `_m` 后缀只用于模块名） |

### 2.1 数组的两个 trait 与自由函数

```rust
// rust/port/oblib/src/container/mod.rs
use crate::error::ObResult;

/// C++: ObIArrayWrap<T>（ob_array_wrap.h:32-69）——数组的只读基类。
/// [T] 实现它，所以 `const ObIArray<T> &` 参数译成 `&[T]` 后照样写 `arr.count()`、`arr.at(i)`。
pub trait ObIArrayWrap<T> {
    /// C++: get_data()（:62，返回 T*）。
    fn get_data(&self) -> &[T];
    fn get_data_mut(&mut self) -> &mut [T];
    /// C++: count()（:56）。
    fn count(&self) -> i64 { … }
    /// C++: empty()（:57）。
    fn empty(&self) -> bool { … }
    /// C++: at(idx) const（:40-47）：越界 ob_assert!（记 ERROR 加调用栈后 abort），release 构建也一样。
    fn at(&self, idx: i64) -> &T { … }
    /// C++: 非 const 的 at(idx)（:48-55）。
    fn at_mut(&mut self, idx: i64) -> &mut T { … }
    /// C++: at(idx, obj)（ob_array.h:310-320）：越界 OB_ARRAY_OUT_OF_RANGE，否则 obj = 元素的克隆。
    fn at_into(&self, idx: i64, obj: &mut T) -> ObResult where T: Clone { … }
}
impl<T> ObIArrayWrap<T> for [T] { /* get_data = self */ }

/// C++: ObIArray<T>（ob_iarray.h:41-91）。参数 `ObIArray<T> &` 译成 `&mut dyn ObIArray<T>`（dyn 兼容）。
/// 不 sealed：其他 crate 的类型（如 ObFixedMetaObjArray）可以实现。
pub trait ObIArray<T>: ObIArrayWrap<T> {
    /// C++: push_back(const T&)（:51）。内部 try_reserve，分配失败 OB_ALLOCATE_MEMORY_FAILED，count 不变。
    fn push_back(&mut self, obj: T) -> ObResult;
    /// C++: pop_back()（:52）：空数组什么也不做。
    fn pop_back(&mut self);
    /// C++: pop_back(T&)（:53）：空数组 OB_ENTRY_NOT_EXIST；否则把末元素移进 obj。
    fn pop_back_into(&mut self, obj: &mut T) -> ObResult;
    /// C++: remove(idx)（:54）：保序删除；越界 OB_ARRAY_OUT_OF_RANGE。
    fn remove(&mut self, idx: i64) -> ObResult;
    /// C++: reset()（:58-59，"同 clear()"）、reuse()、destroy()（:59-61）。
    fn reset(&mut self);
    fn reuse(&mut self);
    fn destroy(&mut self);
    /// C++: reserve(capacity)（:62）：参数是**总容量**，不是增量。
    fn reserve(&mut self, capacity: i64) -> ObResult;
    /// C++: assign(const ObIArray&)（:63）：先清空再逐个克隆。self 与 other 是同一块存储时什么也不做。
    fn assign(&mut self, other: &[T]) -> ObResult where T: Clone;
    /// C++: prepare_allocate(capacity)（:64）：count 补到 capacity，新元素是 T::default()；capacity ≤ count 时不变。
    fn prepare_allocate(&mut self, capacity: i64) -> ObResult where T: Default;
    /// C++: alloc_place_holder()（:65-66，默认 WARN "Not supported" 后返回 NULL）。
    fn alloc_place_holder(&mut self) -> Option<&mut T> where T: Default { … }
}

/// C++: is_contain（ob_iarray.h:93-104）。
pub fn is_contain<T: PartialEq>(arr: &[T], item: &T) -> bool;
/// C++: get_difference（:106-117）：dest 先 reset，再放入 a 中不在 b 里的元素。
pub fn get_difference<T: PartialEq + Clone>(a: &[T], b: &[T], dest: &mut dyn ObIArray<T>) -> ObResult;
/// C++: append<Dst, Src>（:119-128）：逐个 push_back，遇错即停，已放入的保留。
pub fn append<T: Clone>(dst: &mut dyn ObIArray<T>, src: &[T]) -> ObResult;
/// C++: is_array_equal（:130-145）：同一块存储直接 true（先比地址，所以 NaN 元素与自身比较也是 true）。
pub fn is_array_equal<T: PartialEq>(left: &[T], right: &[T]) -> bool;
```

### 2.2 `ObArray`、`ObSArray`、`ObSEArray`、`ObRawSEArray`、`ObFastArray`

```rust
// rust/port/oblib/src/container/array.rs
/// C++: ObArray<T>（ob_array.h:858-864，即 LOCAL_ARRAY_SIZE=0 的 ObSEArrayImpl）。内部 Vec<T>。
pub struct ObArray<T> { data_: Vec<T>, max_print_count_: i64 }
/// C++: ObSArray（ob_array_serialization.h:31）。
pub type ObSArray<T> = ObArray<T>;
/// C++: ObSEArray<T, N>（ob_array.h:836-847）。内部 SmallVec<[T; N]>（smallvec 的 const_generics 特性）。
pub struct ObSEArray<T, const N: usize> { data_: smallvec::SmallVec<[T; N]>, max_print_count_: i64 }
/// C++: ObRawSEArray<T, N>（ob_raw_se_array.h:33-226），只用于平凡类型；行为与 ObSEArray 相同。
pub type ObRawSEArray<T, const N: usize> = ObSEArray<T, N>;

// 以下对 ObArray<T>、ObSEArray<T, N> 都成立（写一次）：
impl<T, const N: usize> ObSEArray<T, N> {
    /// C++: 各构造函数（ob_array.h:287-289,566-595）。
    pub const fn new() -> Self;
    /// C++: size()（:424），同 count()。
    pub fn size(&self) -> i64;
    /// C++: get_capacity()（:420-423）。Rust 的容量增长策略与 C++ 不同（见 §3 A4），只用于日志和判断"是否已分配"。
    pub fn get_capacity(&self) -> i64;
    /// C++: set_max_print_count（:339-350）：负数 OB_INVALID_ARGUMENT。
    pub fn set_max_print_count(&mut self, max_print_count: i64) -> ObResult;
    /// C++: push_back(const ObIArray<T>&)（:625-640）：逐个 push_back，遇错即停。
    pub fn push_back_i_array(&mut self, other: &[T]) -> ObResult where T: Clone;
    /// C++: prepare_allocate(capacity, args...)（:397-401,447-471）：新元素由 make() 构造。
    pub fn prepare_allocate_with(&mut self, capacity: i64, make: &mut dyn FnMut() -> T) -> ObResult;
    /// C++: prepare_allocate_and_keep_count（:403-407）：只保证容量，count 不变。
    pub fn prepare_allocate_and_keep_count(&mut self, capacity: i64) -> ObResult;
}
impl<T, const N: usize> ObIArrayWrap<T> for ObSEArray<T, N> { … }
impl<T, const N: usize> ObIArray<T> for ObSEArray<T, N> { … }
impl<T, const N: usize> core::ops::Deref for ObSEArray<T, N> { type Target = [T]; … }   // 使 &arr 能作 &[T] 实参、能 .iter()
impl<T, const N: usize> core::ops::DerefMut for ObSEArray<T, N> { … }
impl<T, const N: usize> Default for ObSEArray<T, N> { … }
/// C++ 拷贝构造是深拷贝（:821-834）：Clone 克隆每个元素；分配失败按 A.4 oom-abort。
impl<T: Clone, const N: usize> Clone for ObSEArray<T, N> { … }
/// C++: to_string（:741-771），格式见 §3.13。
impl<T: ObToString, const N: usize> ObToString for ObSEArray<T, N> { … }
/// C++: NEED_SERIALIZE_AND_DESERIALIZE（:521-563），格式见 §3.14；trait 名以 06 号为准。
impl<T: ObSerialize, const N: usize> ObSerialize for ObSEArray<T, N> { … }
impl<'de, T: ObDeserialize<'de> + Default, const N: usize> ObDeserialize<'de> for ObSEArray<T, N> { … }

/// C++: ObFastArray<T, N=50>（ob_fast_array.h:34-133）。C++ 不是 ObIArray，Rust 也只实现 ObIArrayWrap（经 Deref 到切片）；
/// 没有 remove、assign、prepare_allocate。
pub struct ObFastArray<T, const N: usize = 50> { data_: smallvec::SmallVec<[T; N]> }
impl<T, const N: usize> ObFastArray<T, N> {
    pub const fn new() -> Self;
    /// C++: push_back（:157-174；满时 push_back_when_full 容量翻倍，:176-201）：分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn push_back(&mut self, obj: T) -> ObResult;
    /// C++: at(idx, obj)（:49-65）：越界 OB_ARRAY_OUT_OF_RANGE。
    pub fn at_into(&self, idx: i64, obj: &mut T) -> ObResult where T: Clone;
    /// C++: at(idx)（:66-83，注释 "dangerous"）：越界记 ERROR 后 abort（right_to_die_or_duty_to_live）。
    pub fn at(&self, idx: i64) -> &T;
    pub fn at_mut(&mut self, idx: i64) -> &mut T;
    /// C++: reserve(capacity)（:84-105）：capacity ≤ N 什么也不做；已经用上堆缓冲时 OB_ERR_UNEXPECTED。
    pub fn reserve(&mut self, capacity: i64) -> ObResult;
    pub fn count(&self) -> i64;
    /// C++: head()（:107）。
    pub fn head(&self) -> &[T];
    pub fn capacity(&self) -> i64;
    pub fn empty(&self) -> bool;
    pub fn pop_back(&mut self);
    /// C++: reset() = destroy()、reuse()（:109-110,203-231）：都回到本地缓冲。
    pub fn reset(&mut self);
    pub fn reuse(&mut self);
    pub fn destroy(&mut self);
}
impl<T, const N: usize> ObIArrayWrap<T> for ObFastArray<T, N> { … }
impl<T, const N: usize> core::ops::Deref for ObFastArray<T, N> { type Target = [T]; … }
impl<T: ObToString, const N: usize> ObToString for ObFastArray<T, N> { … }   // :233-260，格式见 §3.13
```

C++ 的 `operator=`（:773-793，深拷贝，错误记进 `error_`）按 RULEBOOK §2.13（"深拷贝的 `operator=` → `assign(&mut self, &T) -> ObResult`"）译成 `assign(&other)`；`error()`、`get_copy_assign_ret()`（约 8 处，5 个文件，含 2 处定义和 `ob_resource_commmon.h` 的 2 处转发）随之消失：原来先赋值再查 `get_copy_assign_ret()` 的地方，直接查 `assign` 的返回值（§6 例 3）。`begin()`/`end()` 迭代器改用切片的 `iter()`/`iter_mut()`。`get_data_size()`、`get_block_size()` 不提供。

### 2.3 `ObFixedArray`、`ObArrayWrap`、`ObArrayHelper`

```rust
// rust/port/oblib/src/container/fixed_array.rs
/// 定容数组的存储：sealed，只有两种实现。
/// Vec<T>：拥有型（计划、缓存、schema）；ArenaVec<'q, T>：查询作用域（对象本身在 bump arena 里，T 不能需要 Drop）。
pub trait ObFixedArrayStorage<T>: private::Sealed { … }
impl<T> ObFixedArrayStorage<T> for Vec<T> {}
impl<'q, T> ObFixedArrayStorage<T> for crate::mem::ArenaVec<'q, T> {}

/// C++: ObFixedArrayImpl / ObFixedArray（ob_fixed_array.h:35-337）。
/// RULEBOOK 0.6 写的"计划里的定长数组是 Box<[T]>"由这个类型承担：存储只在 init 时按容量分配一次，之后不再增长。
pub struct ObFixedArray<T, S: ObFixedArrayStorage<T> = Vec<T>> {
    data_: Option<S>,   // None 对应 C++ data_ == NULL（未 init）
    capacity_: u32,     // C++ capacity_（可在 init 前用 set_capacity 预设，:63）
}
impl<T> ObFixedArray<T> {
    /// C++: ObFixedArrayImpl(allocator, item_count)（:45-60）：item_count 用 set_capacity 设置。
    pub const fn new() -> Self;
}
impl<'q, T> ObFixedArray<T, crate::mem::ArenaVec<'q, T>> {
    /// C++: 以查询作用域分配器构造的 ObFixedArray。
    pub fn new_in(arena: &'q crate::mem::Arena) -> Self;
}
impl<T, S: ObFixedArrayStorage<T>> ObFixedArray<T, S> {
    /// C++: set_capacity（:63）。
    pub fn set_capacity(&mut self, capacity: u32);
    /// C++: init(capacity)（:178-205）：<0 或 >UINT32_MAX → OB_INVALID_ARGUMENT；已 init → OB_INIT_TWICE；
    /// 0 → 什么也不做（仍未 init）；否则按"预设容量优先"分配（:193），失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn init(&mut self, capacity: i64) -> ObResult;
    /// C++: clear()（:157）：count 清零，保留存储。
    pub fn clear(&mut self);
    pub fn prepare_allocate_with(&mut self, capacity: i64, make: &mut dyn FnMut() -> T) -> ObResult;
    pub fn prepare_allocate_and_keep_count(&mut self, capacity: i64) -> ObResult;
}
impl<T, S: ObFixedArrayStorage<T>> ObIArrayWrap<T> for ObFixedArray<T, S> { … }
/// push_back：未 init 且 capacity_>0 时先按 capacity_ init；capacity_==0 → OB_NOT_INIT；满 → OB_SIZE_OVERFLOW。
/// remove：OB_NOT_IMPLEMENT（:117）。reserve：未 init 时 init(capacity)，再超过容量 → OB_SIZE_OVERFLOW（:159-176）。
impl<T, S: ObFixedArrayStorage<T>> ObIArray<T> for ObFixedArray<T, S> { … }
impl<T, S: ObFixedArrayStorage<T>> core::ops::Deref for ObFixedArray<T, S> { type Target = [T]; … }
impl<T, S: ObFixedArrayStorage<T>> core::ops::DerefMut for ObFixedArray<T, S> { … }
/// C++ 拷贝构造（:264-270）：容量等于对方的元素数。
impl<T: Clone> Clone for ObFixedArray<T> { … }
impl<T: ObToString, S: ObFixedArrayStorage<T>> ObToString for ObFixedArray<T, S> { … }   // :281-295
impl<T: ObSerialize, S: ObFixedArrayStorage<T>> ObSerialize for ObFixedArray<T, S> { … } // ob_array_serialization.h:33-63

// rust/port/oblib/src/container/array_wrap.rs
/// C++: ObArrayWrap<T>（ob_array_wrap.h:71-150）里**拥有内存**的用法（allocate_array/release_array）。
/// 作为视图构造的 ObArrayWrap(ptr, cnt)（多数用法）不用这个类型，直接写 &[T] / &mut [T]；
/// 查询作用域里 allocate_array(arena, n) 写成 01 号的 arena 切片分配，得到 &'q mut [T]。
pub struct ObArrayWrap<T> { data_: Option<Box<[T]>> }
impl<T> ObArrayWrap<T> {
    pub const fn new() -> Self;
    /// C++: allocate_array(allocator, num)（:93-109）：num<=0 → OB_INVALID_ARGUMENT；分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn allocate_array(&mut self, num: i64) -> ObResult where T: Default;
    /// C++: release_array()（:113-122）、reset()（:87-91）。
    pub fn release_array(&mut self);
    pub fn reset(&mut self);
}
impl<T> ObIArrayWrap<T> for ObArrayWrap<T> { … }
impl<T> core::ops::Deref for ObArrayWrap<T> { type Target = [T]; … }
impl<T: ObToString> ObToString for ObArrayWrap<T> { … }   // :124-131："null" 或 "[a, b]"

/// C++: ObArrayHelper<T>（ob_array_helper.h:28-182）：在调用方给出的缓冲上做 push_back。
/// 外部缓冲是已初始化的 &'a mut [T]（C++ 里多是栈上数组，如 ob_tx_ctx.cpp:2523）；
/// 拥有型对象里的 ObArrayHelper 成员（如 ob_column_schema.h:383 的 extended_type_info_）按 types.tsv 改成 ObArray<T>。
pub struct ObArrayHelper<'a, T> { data_: Option<&'a mut [T]>, count_: i64, capacity_: i64 }
impl<'a, T> ObArrayHelper<'a, T> {
    pub const fn new() -> Self;
    /// C++: ObArrayHelper(capacity, base_address, index) 与 init(…)（:35-50）：index 是缓冲里已有的元素数。
    pub fn init(&mut self, capacity: i64, base_address: &'a mut [T], index: i64);
    /// C++: clear()（:149）。
    pub fn clear(&mut self);
}
/// 未 init 或 count 越界（check_inner_stat，:52-55）→ OB_INNER_STAT_ERROR；满或越界 → OB_ARRAY_OUT_OF_RANGE（:59-140）。
impl<'a, T> ObIArrayWrap<T> for ObArrayHelper<'a, T> { … }
impl<'a, T> ObIArray<T> for ObArrayHelper<'a, T> { … }
```

### 2.4 `Ob2DArray`、`ObArrayArray`、`ObObjStore`、`ArenaVec` 的 trait 实现

```rust
// rust/port/oblib/src/container/array_2d.rs
/// C++: Ob2DArray / ObSegmentArray（ob_2d_array.h:31-124）。按块存储，块容量 BLOCK_CAPACITY =
/// 不超过 MAX_BLOCK_SIZE / size_of::<T>() 的最大 2 的幂（:105-106）；首块初始 min(128, BLOCK_CAPACITY)（:114）。
/// 不是 ObIArray（C++ 也不是），不 Deref 成切片（存储不连续）。
pub struct Ob2DArray<T, const MAX_BLOCK_SIZE: usize = { OB_MALLOC_BIG_BLOCK_SIZE as usize }> { blocks_: Vec<Vec<T>>, count_: i64 }
pub type ObSegmentArray<T, const MAX_BLOCK_SIZE: usize = { OB_MALLOC_BIG_BLOCK_SIZE as usize }> = Ob2DArray<T, MAX_BLOCK_SIZE>;
impl<T, const M: usize> Ob2DArray<T, M> {
    pub const BLOCK_CAPACITY: usize = …;
    pub const fn new() -> Self;
    pub fn init(&mut self, new_size: i64) -> ObResult where T: Default;   // C++ init（:44）
    pub fn push_back(&mut self, obj: T) -> ObResult;
    pub fn pop_back(&mut self);
    pub fn pop_back_into(&mut self, obj: &mut T) -> ObResult;
    pub fn remove(&mut self, idx: i64) -> ObResult;
    pub fn assign(&mut self, other: &[T]) -> ObResult where T: Clone;
    pub fn at(&self, idx: i64) -> &T;        // ob_assert! 越界
    pub fn at_mut(&mut self, idx: i64) -> &mut T;
    pub fn at_into(&self, idx: i64, obj: &mut T) -> ObResult where T: Clone;
    pub fn count(&self) -> i64;
    pub fn empty(&self) -> bool;
    pub fn reuse(&mut self);
    pub fn reset(&mut self);
    pub fn destroy(&mut self);
    pub fn reserve(&mut self, capacity: i64) -> ObResult;
    pub fn set_all(&mut self, value: &T) -> ObResult where T: Clone;
    pub fn prepare_allocate(&mut self, capacity: i64) -> ObResult where T: Default;
    pub fn get_capacity(&self) -> i64;
    pub fn mem_used(&self) -> i64;
    pub fn iter(&self) -> impl Iterator<Item = &T>;
}

/// C++: ObArrayArray<T, LOCAL_ARRAY_SIZE=8, ARRAY_ARRAY_SIZE=8>（ob_array_array.h:35-120）。
pub struct ObArrayArray<T, const N: usize = 8> { arrays_: Vec<ObSEArray<T, N>>, count_: i64 }
impl<T, const N: usize> ObArrayArray<T, N> {
    pub const fn new() -> Self;
    pub fn reset(&mut self);
    pub fn reuse(&mut self);
    /// C++: reserve（:205-262）：负数 OB_INVALID_ARGUMENT。
    pub fn reserve(&mut self, capacity: i64) -> ObResult;
    /// C++: push_back(const ObIArray&)（:264-281）：新增一个内层数组并 assign。
    pub fn push_back(&mut self, array: &[T]) -> ObResult where T: Clone;
    /// C++: push_back(array_idx, obj)（:283-300）：array_idx ≥ count → OB_ARRAY_OUT_OF_RANGE。
    pub fn push_back_obj(&mut self, array_idx: i64, obj: T) -> ObResult;
    /// C++: at(array_idx, idx)、at(array_idx)、count(array_idx)（:58-108）：下标非法时记 ERROR 后 abort（right_to_die_or_duty_to_live）。
    pub fn at(&self, array_idx: i64, idx: i64) -> &T;
    pub fn at_mut(&mut self, array_idx: i64, idx: i64) -> &mut T;
    pub fn at_array(&self, array_idx: i64) -> &[T];
    pub fn at_array_mut(&mut self, array_idx: i64) -> &mut dyn ObIArray<T>;
    /// C++: at(array_idx, idx, obj)（:303-322）：OB_ARRAY_OUT_OF_RANGE。
    pub fn at_into(&self, array_idx: i64, idx: i64, obj: &mut T) -> ObResult where T: Clone;
    pub fn count(&self) -> i64;
    pub fn count_array(&self, array_idx: i64) -> i64;
    pub fn assign(&mut self, other: &ObArrayArray<T, N>) -> ObResult where T: Clone;
}

// rust/port/oblib/src/container/obj_store.rs
/// C++: ObObjStore<T>（ob_obj_store.h:45-150）：按登记顺序保存对象；Rust 内部 Vec<T>。
/// T 多是工厂建出的对象：查询作用域里是 id，拥有型是 Box/Arc。
pub struct ObObjStore<T> { objs_: Vec<T> }
impl<T> ObObjStore<T> {
    pub const fn new() -> Self;
    /// C++: store_obj（:113-130）：分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn store_obj(&mut self, obj: T) -> ObResult;
    /// C++: begin() 起的 Iterator（:49-74）。
    pub fn iter(&self) -> core::slice::Iter<'_, T>;
    pub fn iter_mut(&mut self) -> core::slice::IterMut<'_, T>;
    pub fn empty(&self) -> bool;
    pub fn get_size(&self) -> i32;
    pub fn destroy(&mut self);
    pub fn clear(&mut self);
}

// rust/port/oblib/src/container/arena_impl.rs
/// 01 号的 ArenaVec<'q, T>（01 文档 §2.2）作为查询作用域的 ObSEArray/ObArray。
/// 用它的 as_slice/as_mut_slice/try_reserve/push_back/pop_back/reuse 实现；reset/destroy 等于 reuse（arena 内存不单独释放）。
impl<'q, T> ObIArrayWrap<T> for crate::mem::ArenaVec<'q, T> { … }
impl<'q, T> ObIArray<T> for crate::mem::ArenaVec<'q, T> { … }
```

### 2.5 `ObVector`、`ObSortedVector`

迭代器（C++ 是 `T*`）一律译成位置下标 `i64`：`begin()` 是 0，`end()` 是元素数，`*it` 写成 `v.at(it)`，`++it` 写成 `it += 1`。

```rust
// rust/port/oblib/src/container/vector.rs
/// C++: ObVector<T, Allocator>（ob_vector.h:84-174，ob_vector.ipp）。
pub struct ObVector<T> { data_: Vec<T> }
impl<T> ObVector<T> {
    pub const fn new() -> Self;
    pub fn assign(&mut self, other: &ObVector<T>) -> ObResult where T: Clone;
    /// C++: size() 返回 int32_t（:105）。
    pub fn size(&self) -> i32;
    pub fn capacity(&self) -> i64;
    pub fn remain(&self) -> i64;
    pub fn begin(&self) -> i64;
    pub fn end(&self) -> i64;
    pub fn last(&self) -> i64;
    /// C++: at(index, v) → bool（:117-125）。
    pub fn at_into(&self, index: i64, v: &mut T) -> bool where T: Clone;
    /// C++: at(index) / operator[]（:127-138）：越界 C++ 只记 ERROR "invalid index" 后照样解引用；Rust ob_assert! abort（A.3）。
    pub fn at(&self, index: i64) -> &T;
    pub fn at_mut(&mut self, index: i64) -> &mut T;
    pub fn push_back(&mut self, value: T) -> ObResult;
    pub fn insert(&mut self, pos: i64, value: T) -> ObResult;
    /// C++: replace（.ipp:382-394）：pos 越界 OB_ENTRY_NOT_EXIST。
    pub fn replace(&mut self, pos: i64, value: T, replaced_value: &mut T) -> ObResult;
    /// C++: find / find_if（.ipp:216-239）：找不到返回 end()。
    pub fn find(&self, value: &T) -> i64 where T: PartialEq;
    pub fn find_if<V: ?Sized>(&self, value: &V, equal: impl FnMut(&T, &V) -> bool) -> i64;
    /// C++: remove(iterator)（.ipp:242-254）：越界 OB_ARRAY_OUT_OF_RANGE。
    pub fn remove(&mut self, pos: i64) -> ObResult;
    /// C++: remove(int64_t index)（.ipp:271-281）：越界 OB_ENTRY_NOT_EXIST。
    pub fn remove_index(&mut self, index: i64) -> ObResult;
    /// C++: remove(start, end)（.ipp:256-269）。
    pub fn remove_range(&mut self, start_pos: i64, end_pos: i64) -> ObResult;
    /// C++: remove_if(value) / remove_if(value, pred) / remove_if(value, pred, removed)（.ipp:283-336）：找不到 OB_ENTRY_NOT_EXIST。
    pub fn remove_if(&mut self, value: &T) -> ObResult where T: PartialEq;
    pub fn remove_if_pred<V: ?Sized>(&mut self, value: &V, predicate: impl FnMut(&T, &V) -> bool) -> ObResult;
    pub fn remove_if_pred_into<V: ?Sized>(&mut self, value: &V, predicate: impl FnMut(&T, &V) -> bool, removed_value: &mut T) -> ObResult;
    /// C++: reserve = expand（:155,.ipp:135-159）。
    pub fn reserve(&mut self, size: i64) -> ObResult;
    /// C++: clear() 释放内存（.ipp:117-125）；reset() 只把元素数归零（:157）；destroy()（:160）。
    pub fn clear(&mut self);
    pub fn reset(&mut self);
    pub fn destroy(&mut self);
    pub fn iter(&self) -> core::slice::Iter<'_, T>;
}
impl<T: ObToString> ObToString for ObVector<T> { … }   // .ipp:396-410

/// C++: ObSortedVector<T, Allocator>（ob_vector.h:176-290）。比较器是 C++ 的"小于"函子。
pub struct ObSortedVector<T> { vector_: ObVector<T> }
impl<T> ObSortedVector<T> {
    pub const fn new() -> Self;
    pub fn assign(&mut self, other: &ObSortedVector<T>) -> ObResult where T: Clone;
    pub fn size(&self) -> i32;
    pub fn count(&self) -> i64;
    pub fn capacity(&self) -> i64;
    pub fn remain(&self) -> i64;
    pub fn reserve(&mut self, size: i64) -> ObResult;
    pub fn remove(&mut self, pos: i64) -> ObResult;
    pub fn clear(&mut self);
    pub fn reset(&mut self);
    pub fn push_back(&mut self, value: T) -> ObResult;
    pub fn begin(&self) -> i64;
    pub fn end(&self) -> i64;
    pub fn last(&self) -> i64;
    pub fn at_into(&self, index: i64, v: &mut T) -> bool where T: Clone;
    pub fn at(&self, index: i64) -> &T;
    pub fn at_mut(&mut self, index: i64) -> &mut T;
    /// C++: insert（.ipp:413-428）：插在 lower_bound 处（等值元素之前）。
    pub fn insert(&mut self, value: T, insert_pos: &mut i64, compare: impl FnMut(&T, &T) -> bool) -> ObResult;
    /// C++: replace（.ipp:430-453）：lower_bound 处 equal 则替换，否则插入。
    pub fn replace(&mut self, value: T, replace_pos: &mut i64, compare: impl FnMut(&T, &T) -> bool,
                   equal: impl FnMut(&T, &T) -> bool, replaced_value: &mut T) -> ObResult;
    /// C++: insert_unique（.ipp:455-499）：冲突 OB_CONFLICT_VALUE，insert_pos 指向冲突元素。
    pub fn insert_unique(&mut self, value: T, insert_pos: &mut i64, compare: impl FnMut(&T, &T) -> bool,
                         unique: impl FnMut(&T, &T) -> bool) -> ObResult;
    /// C++: find(value, pos, compare)（.ipp:501-516）。
    pub fn find(&self, value: &T, pos: &mut i64, compare: impl FnMut(&T, &T) -> bool) -> ObResult;
    /// C++: find(value, pos, compare, equal)（.ipp:518-533）。
    pub fn find_eq<V: ?Sized>(&self, value: &V, pos: &mut i64, compare: impl FnMut(&T, &V) -> bool,
                              equal: impl FnMut(&T, &V) -> bool) -> ObResult;
    /// C++: lower_bound（.ipp:535-541）：std::lower_bound，调用 compare(元素, value)。
    pub fn lower_bound<V: ?Sized>(&self, value: &V, compare: impl FnMut(&T, &V) -> bool) -> i64;
    /// C++: upper_bound（.ipp:543-549）：std::upper_bound，调用 compare(value, 元素)——参数顺序与 lower_bound 相反。
    pub fn upper_bound<V: ?Sized>(&self, value: &V, compare: impl FnMut(&V, &T) -> bool) -> i64;
    pub fn remove_if<V: ?Sized>(&mut self, value: &V, compare: impl FnMut(&T, &V) -> bool, equal: impl FnMut(&T, &V) -> bool) -> ObResult;
    pub fn remove_if_into<V: ?Sized>(&mut self, value: &V, compare: impl FnMut(&T, &V) -> bool,
                                     equal: impl FnMut(&T, &V) -> bool, removed_value: &mut T) -> ObResult;
    pub fn remove_range(&mut self, start_pos: i64, end_pos: i64) -> ObResult;
    /// C++: sort（.ipp:588-593）：lib::ob_sort（06 号的 oblib::sort::ob_sort）。
    pub fn sort(&mut self, compare: impl FnMut(&T, &T) -> bool);
    pub fn iter(&self) -> core::slice::Iter<'_, T>;
}
impl<T: ObToString> ObToString for ObSortedVector<T> { … }
```

### 2.6 `ObList`

```rust
// rust/port/oblib/src/container/list.rs
/// C++: list::Iterator / ConstIterator（ob_list.h:46-221）。Copy；带代际，指向已删节点时访问会 ob_assert!（C++ 是未定义行为）。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ObListIter { key_: Option<slotmap::DefaultKey> }   // None 即 end()

/// C++: ObList<T, Allocator>（ob_list.h:229-567）。双向链表，节点在 slotmap 里（修订 A2）：
/// 插入、删除 O(1)，删除别的节点不影响已有迭代器。
pub struct ObList<T> { nodes_: slotmap::SlotMap<slotmap::DefaultKey, ListNode<T>>, head_: Option<slotmap::DefaultKey>, tail_: Option<slotmap::DefaultKey> }
impl<T> ObList<T> {
    pub fn new() -> Self;
    /// C++: push_back / push_front（:276-319）：分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn push_back(&mut self, value: T) -> ObResult;
    pub fn push_front(&mut self, value: T) -> ObResult;
    /// C++: pop_back()、pop_front()、pop_front(value&)（:321-368）：空表 OB_ENTRY_NOT_EXIST。
    pub fn pop_back(&mut self) -> ObResult;
    pub fn pop_front(&mut self) -> ObResult;
    pub fn pop_front_into(&mut self, value: &mut T) -> ObResult;
    /// C++: insert(iter, value)（:370-390）：插在 iter 之前；iter 为 end() 时等于 push_back。
    pub fn insert(&mut self, iter: ObListIter, value: T) -> ObResult;
    /// C++: erase(iter)（:392-408）：空表 OB_ENTRY_NOT_EXIST；iter 为 end() OB_INVALID_ARGUMENT。
    pub fn erase(&mut self, iter: ObListIter) -> ObResult;
    /// C++: erase(const T&)（:410-421）：删第一个相等的；找不到也返回 OB_SUCCESS。
    pub fn erase_value(&mut self, value: &T) -> ObResult where T: PartialEq;
    /// C++: get_first / get_last（:423-425）：空表时 C++ 解引用哨兵（未定义行为），Rust ob_assert!。
    pub fn get_first(&self) -> &T;
    pub fn get_first_mut(&mut self) -> &mut T;
    pub fn get_last(&self) -> &T;
    pub fn get_last_mut(&mut self) -> &mut T;
    /// C++: begin()、last()、end()（:427-447），以及迭代器的 ++/--、*、->。
    pub fn begin(&self) -> ObListIter;
    pub fn last(&self) -> ObListIter;
    pub fn end(&self) -> ObListIter;
    pub fn next(&self, iter: ObListIter) -> ObListIter;
    pub fn prev(&self, iter: ObListIter) -> ObListIter;
    pub fn get(&self, iter: ObListIter) -> &T;
    pub fn get_mut(&mut self, iter: ObListIter) -> &mut T;
    pub fn iter(&self) -> impl Iterator<Item = &T>;
    pub fn iter_mut(&mut self) -> impl Iterator<Item = &mut T>;
    /// C++: clear / destroy / reset（:448-475）。
    pub fn clear(&mut self);
    pub fn destroy(&mut self);
    pub fn reset(&mut self);
    pub fn empty(&self) -> bool;
    pub fn size(&self) -> i64;
    /// C++: assign<C>(container)（:549-561）：清空后逐个 push_back，遇错即停。
    pub fn assign<'a>(&mut self, src: impl IntoIterator<Item = &'a T>) -> ObResult where T: Clone + 'a;
}
impl<T: ObToString> ObToString for ObList<T> { … }   // :537-547
impl<T: ObSerialize> ObSerialize for ObList<T> { … }  // :477-535
```

### 2.7 位集：`ObFixedBitSet`、`ObBitSet`、`ObSegmentBitSet`

字一律是 `u32`（C++ `typedef uint32_t BitSetWord`，ob_bit_set.h:39,188；修订 A3）。

```rust
// rust/port/oblib/src/container/bit_set.rs
pub const OB_DEFAULT_BITSET_SIZE: i64 = 256;   // C++ ob_define.h:1507（= OB_MAX_TABLE_NUM_PER_STMT, :445）；06 号若已导出则复用

/// C++: ObFixedBitSet<N>（ob_bit_set.h:183-433）。MAX_BITSETWORD = (N-1)/32+1 个字，构造时一次分配
/// （N ≤ 256 时在 SmallVec 的内联空间里）。RULEBOOK R5 的 IR 标记旁表就用它。
pub struct ObFixedBitSet<const N: usize = { OB_DEFAULT_BITSET_SIZE as usize }> { bitset_word_array_: smallvec::SmallVec<[u32; 8]> }
impl<const N: usize> ObFixedBitSet<N> {
    pub fn new() -> Self;
    pub fn reset(&mut self);
    pub fn bit_count(&self) -> i64;                       // MAX_BITSETWORD * 32
    /// C++: add_member / del_member（:232-259）：index<0 或 index>=N → OB_INVALID_ARGUMENT（判的是 N，不是 bit_count）。
    pub fn add_member(&mut self, index: i64) -> ObResult;
    pub fn del_member(&mut self, index: i64) -> ObResult;
    pub fn add_members(&mut self, other: &ObFixedBitSet<N>) -> ObResult;
    /// C++: has_member（:272-286）：负数记 WARN 后返回 false；超过 bit_count 返回 false。
    pub fn has_member(&self, index: i64) -> bool;
    pub fn num_members(&self) -> i64;
    pub fn is_empty(&self) -> bool;
    /// C++: find_next / find_prev / find_first（:345-431）：没找到时 pos = -1；start_pos 非法 OB_INVALID_ARGUMENT。
    pub fn find_next(&self, start_pos: i64, pos: &mut i64) -> ObResult;
    pub fn find_prev(&self, start_pos: i64, pos: &mut i64) -> ObResult;
    pub fn find_first(&self, pos: &mut i64) -> ObResult;
    pub fn clear_all(&mut self);
}
impl<const N: usize> Clone for ObFixedBitSet<N> { … }
impl<const N: usize> PartialEq for ObFixedBitSet<N> { … }      // :326-336
impl<const N: usize> ObToString for ObFixedBitSet<N> { … }      // :338-343
impl<const N: usize> ObSerialize for ObFixedBitSet<N> { … }     // OB_UNIS_VERSION(1) + OB_SERIALIZE_MEMBER_TEMP（:186,433）

/// C++: ObBitSet<N, Alloc, auto_free>（ob_bit_set.h:435-1060）：按需增长，存储同 ObSEArray<u32, MAX_BITSETWORD>。
pub struct ObBitSet<const N: usize = { OB_DEFAULT_BITSET_SIZE as usize }> { bitset_word_array_: ObSEArray<u32, 8> }
impl<const N: usize> ObBitSet<N> {
    pub fn new() -> Self;
    pub fn assign<const M: usize>(&mut self, other: &ObBitSet<M>) -> ObResult;
    pub fn equal(&self, other: &ObBitSet<N>) -> bool;
    pub fn prepare_allocate(&mut self, bits: i64) -> ObResult;
    pub fn reserve(&mut self, bits: i64) -> ObResult;
    /// C++: add_member（:598-621）：负数 OB_INVALID_ARGUMENT；字不够时逐个 push_back(0) 补齐。
    pub fn add_member(&mut self, index: i64) -> ObResult;
    /// C++: del_member（:649-666）：超出已有字数时什么也不做。
    pub fn del_member(&mut self, index: i64) -> ObResult;
    pub fn has_member(&self, index: i64) -> bool;
    pub fn is_empty(&self) -> bool;
    pub fn is_subset(&self, other: &ObBitSet<N>) -> bool;
    pub fn is_superset(&self, other: &ObBitSet<N>) -> bool;
    pub fn overlap(&self, other: &ObBitSet<N>) -> bool;
    pub fn add_members2<const M: usize>(&mut self, other: &ObBitSet<M>) -> ObResult;
    pub fn is_subset2<const M: usize>(&self, other: &ObBitSet<M>) -> bool;
    pub fn is_superset2<const M: usize>(&self, other: &ObBitSet<M>) -> bool;
    pub fn overlap2<const M: usize>(&self, other: &ObBitSet<M>) -> bool;
    pub fn add_members(&mut self, other: &ObBitSet<N>) -> ObResult;
    pub fn del_members(&mut self, other: &ObBitSet<N>);
    pub fn do_mask(&mut self, begin_index: i64, end_index: i64) -> ObResult;
    pub fn reuse(&mut self);
    pub fn reset(&mut self);
    pub fn num_members(&self) -> i64;
    /// C++: to_array（:862-880）：arr 先 reuse。
    pub fn to_array(&self, arr: &mut dyn ObIArray<i64>) -> ObResult;
    pub fn bitset_word_count(&self) -> i64;
    pub fn bit_count(&self) -> i64;
    pub fn get_first_peculiar_bit(&self, other: &ObBitSet<N>) -> i64;
    pub fn get_bitset_word(&self, index: i64) -> u32;
    pub fn find_next(&self, start_pos: i64, pos: &mut i64) -> ObResult;
    pub fn find_prev(&self, start_pos: i64, pos: &mut i64) -> ObResult;
    pub fn find_first(&self, pos: &mut i64) -> ObResult;
    pub fn clear_all(&mut self);
    pub fn set_all(&mut self);
}
impl<const N: usize> Clone for ObBitSet<N> { … }
impl<const N: usize> PartialEq for ObBitSet<N> { … }   // :928-939：按较长者比，缺的字当 0
impl<const N: usize> ObToString for ObBitSet<N> { … }  // = ObSEArray<u32> 的 to_string：u32 没有 to_string 成员，输出 "[cnt:N]"
impl<const N: usize> ObSerialize for ObBitSet<N> { … } // = ObSEArray<u32> 的序列化（:947-967）

/// C++: ObSegmentBitSet<N, Alloc>（ob_bit_set.h:32-180）：ObBitSet 的子集接口，存储是 ObSegmentArray<u32>。
/// 唯一使用者 ob_exec_hash_struct.h:504。
pub struct ObSegmentBitSet<const N: usize = { OB_DEFAULT_BITSET_SIZE as usize }> { bitset_word_array_: ObSegmentArray<u32> }
impl<const N: usize> ObSegmentBitSet<N> {
    pub fn new() -> Self;
    pub fn get_bitset_word(&self, index: i64) -> u32;
    pub fn equal(&self, other: &ObSegmentBitSet<N>) -> bool;
    pub fn prepare_allocate(&mut self, bits: i64) -> ObResult;
    pub fn reserve(&mut self, bits: i64) -> ObResult;
    pub fn add_member(&mut self, index: i64) -> ObResult;
    pub fn has_member(&self, index: i64) -> bool;
    pub fn is_empty(&self) -> bool;
    pub fn reuse(&mut self);
    pub fn reset(&mut self);
    pub fn bitset_word_count(&self) -> i64;
    pub fn bit_count(&self) -> i64;
    pub fn clear_all(&mut self);
    pub fn set_all(&mut self);
}
```

`ObBitSet::add_atomic_member`（:623-647）全库 0 处调用，不提供。

### 2.8 侵入式链表：`ObDList`（`container/intrusive`，§3 白名单）

适用范围（RULEBOOK §2.5）：arena 里的链表用 id 串起来，只读计划里的一律 `Vec`，其余才用本节的 `ObDList`。契约：**链表拥有节点**，指针类型只能是 `Box<T>`（单一归属）或 `Arc<T>`（节点同时被别处持有 `Arc` 克隆，§2.7 多重归属规则）；只用于单一归属的链接（每个节点类型一个链接字段）。"节点被 `Pin` 且宿主 Drop 时自动摘链"的形态首版不提供，见 §8 Q2。

```rust
// rust/port/oblib/src/container/intrusive/mod.rs   （#[allow(unsafe_code)]）
/// C++: ObDLinkBase<Derived> 的 prev_/next_（ob_dlink_node.h:33-74）。在 Rust 里是节点 struct 的一个**字段**，不是基类
/// （字段名由 types.tsv 按 RULEBOOK §2.13 的基类规则给出，本文例子写 dlink_；本类型不带泛型参数）。
/// 字段全是原子量，所以含它的节点仍是 Send + Sync；只有持有 &mut ObDList 的一方会改它。
pub struct ObDLinkBase { prev_: AtomicPtr<ObDLinkBase>, next_: AtomicPtr<ObDLinkBase>, node_: AtomicPtr<()>, owner_: AtomicUsize }
impl ObDLinkBase {
    pub const fn new() -> Self;
    /// 是否挂在某个链表上。
    pub fn is_linked(&self) -> bool;
}
impl Default for ObDLinkBase { … }
/// 挂在链表上时被析构（只可能出自错误的 dlink() 实现）→ ob_assert! abort，不留悬垂指针。
impl Drop for ObDLinkBase { … }

/// C++: 继承 ObDLinkBase<T> 这件事。节点类型写 `impl ObDLinkBaseVirt for X { fn dlink(&self) -> &ObDLinkBase { &self.dlink_ } }`，全是安全代码。
pub trait ObDLinkBaseVirt { fn dlink(&self) -> &ObDLinkBase; }

/// 链表持有节点的指针类型：sealed，只实现给 Box<T> 和 Arc<T>（翻译代码不实现它，也就不需要 unsafe）。
pub unsafe trait ObDListPointer: Sized + private::Sealed { type Target: ObDLinkBaseVirt; }
unsafe impl<T: ObDLinkBaseVirt> ObDListPointer for Box<T> { type Target = T; }
unsafe impl<T: ObDLinkBaseVirt> ObDListPointer for alloc::sync::Arc<T> { type Target = T; }

/// C++: ObDList<DLinkNode>（ob_dlist.h:60-137）。表头放在 Box 里，地址稳定；表头地址兼作 owner_ 标签。
pub struct ObDList<P: ObDListPointer> { header_: core::pin::Pin<Box<ObDLinkBase>>, size_: i32, _p: core::marker::PhantomData<P> }
impl<P: ObDListPointer> ObDList<P> {
    pub fn new() -> Self;
    /// C++: move(list)（:70-81）：把全部节点挪到 list（list 先清空）。
    pub fn r#move(&mut self, list: &mut ObDList<P>) -> ObResult;
    /// C++: get_first / get_last（:88-94）；C++ 空表返回表头，Rust 返回 None。
    pub fn get_first(&self) -> Option<&P::Target>;
    pub fn get_last(&self) -> Option<&P::Target>;
    /// C++: node->get_next() / get_prev()（ob_dlink_node.h:40-44）：到表尾时 C++ 返回表头，Rust 返回 None。
    pub fn get_next(&self, node: &P::Target) -> Option<&P::Target>;
    pub fn get_prev(&self, node: &P::Target) -> Option<&P::Target>;
    /// C++: add_last / add_first（:152-189）：节点已挂在链表上 → 记 ERROR、返回 false，传入的指针被 drop
    /// （Box 不可能出现这种情况；Arc 只是少一个计数）。
    pub fn add_last(&mut self, e: P) -> bool;
    pub fn add_first(&mut self, e: P) -> bool;
    /// C++: add_before(pos, e)（:191-208）：pos 不在本表或 e 已挂 → false。
    pub fn add_before(&mut self, pos: &P::Target, e: P) -> bool;
    /// C++: increasing_add（:210-229），见 §3 D3。
    pub fn increasing_add(&mut self, e: P) -> bool where P::Target: PartialOrd;
    /// C++: move_to_first / move_to_last（:231-259）：e 不在本表 → false（C++ 会破坏两张表）。
    pub fn move_to_first(&mut self, e: &P::Target) -> bool;
    pub fn move_to_last(&mut self, e: &P::Target) -> bool;
    /// C++: remove_last / remove_first / remove（:261-285）：空表或 e 不在本表 → None。
    pub fn remove_last(&mut self) -> Option<P>;
    pub fn remove_first(&mut self) -> Option<P>;
    pub fn remove(&mut self, e: &P::Target) -> Option<P>;
    /// C++: push_range（插到表头）/ push_back_range（接到表尾）/ pop_range(num, range)（:286-343）。
    pub fn push_range(&mut self, range: &mut ObDList<P>);
    pub fn push_back_range(&mut self, range: &mut ObDList<P>);
    pub fn pop_range(&mut self, num: i32, range: &mut ObDList<P>);
    pub fn is_empty(&self) -> bool;
    pub fn get_size(&self) -> i32;
    /// C++: clear / reset（:123-130）：C++ 只重置表头；Rust 摘下并 drop 全部节点（拥有语义）。
    pub fn clear(&mut self);
    pub fn reset(&mut self);
    /// DLIST_FOREACH / DLIST_FOREACH_NORET / DLIST_FOREACH_X（:24-29）。
    pub fn iter(&self) -> ObDListIter<'_, P>;
    /// DLIST_FOREACH_BACKWARD_X（:31-34）。
    pub fn iter_rev(&self) -> ObDListIter<'_, P>;
    /// DLIST_FOREACH_REMOVESAFE* / DLIST_FOREACH_BACKWARD_REMOVESAFE_X（:36-46）：遍历中删除当前节点。
    pub fn cursor_front_mut(&mut self) -> ObDListCursorMut<'_, P>;
    pub fn cursor_back_mut(&mut self) -> ObDListCursorMut<'_, P>;
}
impl<P: ObDListPointer> Drop for ObDList<P> { … }   // 摘下并 drop 全部节点
impl<P: ObDListPointer> ObToString for ObDList<P> where P::Target: ObToString { … }   // :348-373

pub struct ObDListIter<'a, P: ObDListPointer> { … }
impl<'a, P: ObDListPointer> Iterator for ObDListIter<'a, P> { type Item = &'a P::Target; … }

pub struct ObDListCursorMut<'a, P: ObDListPointer> { … }
impl<'a, P: ObDListPointer> ObDListCursorMut<'a, P> {
    pub fn get(&self) -> Option<&P::Target>;
    pub fn move_next(&mut self);
    pub fn move_prev(&mut self);
    /// 摘下当前节点并前进到它原来的下一个（REMOVESAFE 的 save 指针）。
    pub fn remove_current(&mut self) -> Option<P>;
    pub fn move_current_to_first(&mut self) -> bool;
    pub fn move_current_to_last(&mut self) -> bool;
}

/// C++: ObDLinkNode<T>（ob_dlink_node.h:142-155）与 ObDLinkDerived<T>（:157-162）：带数据的通用节点。
pub struct ObDLinkNode<T> { pub dlink_: ObDLinkBase, data_: T }
impl<T> ObDLinkNode<T> {
    pub fn new(data: T) -> Self;
    pub fn get_data(&self) -> &T;
    pub fn get_data_mut(&mut self) -> &mut T;   // 只有拿到 &mut 节点（未挂表）时可用
}
impl<T> ObDLinkBaseVirt for ObDLinkNode<T> { … }
/// 链表拥有的 ObDLinkNode 可以在挂表时改数据：只交出 data_，链接字段碰不到。
impl<T> ObDList<Box<ObDLinkNode<T>>> {
    pub fn iter_data_mut(&mut self) -> impl Iterator<Item = &mut T>;
}
```

### 2.9 哈希基础：`HashFunc`、锁模式、`cal_next_prime`、`HashMapPair`、哈希函数

```rust
// rust/port/oblib/src/hash/hashutils.rs
/// C++: hash_func<K> 的特化表（ob_hashutils.h:748-859）：int operator()(const K&, uint64_t &res)。
/// ObHashMap、ObHashSet、BucketedHashMap、ObLinearHashMap 的键都要实现。
/// 规则：C++ 键类定义了 `int hash(uint64_t &) const` 的，同一单元写 `impl HashFunc for T`，方法体就是那个函数的翻译。
pub trait HashFunc {
    fn hash_func(&self, res: &mut u64) -> ObResult;
}
// 本模块提供的实现：
//   i8 i16 i32 i64 isize → res = (值 as i64) as u64（有符号扩展）；u8 u16 u32 u64 usize → res = 值（:837-859，_HASH_FUNC_SPEC）
//   [u8]、&[u8]、Box<[u8]>、Vec<u8>（C++ ObString、char*、const char*）→ murmurhash(bytes, 0)（:773-798）
//   (i32, u32)（C++ std::pair<int, uint32_t>）→ (first as u32).wrapping_add(second) as u64（:800-807）
//   (u64, u64)（C++ std::pair<uint64_t, uint64_t>）→ first.wrapping_add(second)（:809-816）
//   &T、Box<T>、Arc<T>（C++ 指针键 K*）→ 委托给 T（:757-771）
// ParseNode 的特化（:818-835）由 09 号在 parser crate 里实现（0.4：特化挪到类型所在的 crate）。
// 相等一律用 PartialEq（C++ equal_to<K>，:702-727）；&T、Box<T>、Arc<T> 的 PartialEq 比较的是指向的值，正好对应 C++ 指针特化 *a == *b。

/// C++ `ObHashMap<const char*, V>` 的键（唯一用户 ob_mysql_transaction.h:98）：哈希按整个串，相等只比首字节——
/// C++ 把 `const char*` 落到 equal_to<const _key*> 的通用指针特化（:720-727），比的是 *a == *b。照样复刻，标 BUG(port)。
#[derive(Clone, Copy)]
pub struct ObCStrKey<'a>(pub &'a [u8]);
impl HashFunc for ObCStrKey<'_> { … }
impl PartialEq for ObCStrKey<'_> { … }

/// C++: _defendmode 模板参数（ob_hashutils.h:573-648）。sealed。
pub trait DefendMode: private::Sealed + Send + Sync + 'static {}
/// 有内部锁的五种模式。NoPthreadDefendMode 不实现它——ObHashMap/ObHashSet 据此分成两套接收者（§2.10）。
pub trait LockedDefendMode: DefendMode {}
/// C++: LatchReadWriteDefendMode（:573-582）：每桶一把 ObLatch，latch id HASH_MAP_LOCK（:315,345）。ObHashMap 的默认。
pub struct LatchReadWriteDefendMode;
/// C++: ReadWriteDefendMode（:584-608）：Linux 上 pthread rwlock，macOS/Windows 上 pthread mutex（读也互斥）。ObHashSet 的默认（ob_hashset.h:43）。
/// Rust：RwLatch，latch id DEFAULT_MUTEX，各平台都允许并发读（不可观察）。
pub struct ReadWriteDefendMode;
/// C++: SpinReadWriteDefendMode（:609-618）：SpinRWLock = ObLatch，latch id DEFAULT_SPIN_RWLOCK（ob_spin_rwlock.h:35）。
pub struct SpinReadWriteDefendMode;
/// C++: SpinMutexDefendMode（:619-628）：写持 pthread 自旋锁，**读不加锁**。Rust 读写都加锁（DEFAULT_SPIN_LOCK），FIXED(port)；基线 0 处使用。
pub struct SpinMutexDefendMode;
/// C++: MultiWriteDefendMode（:629-638）：pthread mutex + cond（cond 只服务于已不可达的"假节点等待"）。Rust：互斥，DEFAULT_MUTEX；基线 0 处用作表的模式。
pub struct MultiWriteDefendMode;
/// C++: NoPthreadDefendMode（:639-648）：无锁，由外部同步。约 184 处，89 个文件。
pub struct NoPthreadDefendMode;
impl LockedDefendMode for LatchReadWriteDefendMode {}
impl LockedDefendMode for ReadWriteDefendMode {}
impl LockedDefendMode for SpinReadWriteDefendMode {}
impl LockedDefendMode for SpinMutexDefendMode {}
impl LockedDefendMode for MultiWriteDefendMode {}

/// C++: PRIME_LIST（ob_hashutils.cpp:21-29，28 个素数，53 … 4294967291）与 cal_next_prime（ob_hashutils.h:674-687）：
/// n>0 时取 ≥n 的最小素数，超过最大值取最大值；n≤0 原样返回。
pub const PRIME_LIST: [i64; 28] = [53, 97, 193, 389, 769, 1543, 3079, 6151, 12289, 24593, 49157, 98317, 196613, 393241,
    786433, 1572869, 3145739, 6291469, 12582917, 25165843, 50331653, 100663319, 201326611, 402653189, 805306457,
    1610612741, 3221225473, 4294967291];
pub fn cal_next_prime(n: i64) -> i64;

/// C++: HashMapPair<K, V>（:867-906）。回调参数就是它；字段名照 C++。
pub struct HashMapPair<K, V> { pub first: K, pub second: V }
/// C++: HashNullObj（:863-865），ObHashSet 的值类型。
#[derive(Clone, Copy, Default, PartialEq)]
pub struct HashNullObj;

// rust/port/oblib/src/hash/hash_func.rs
/// C++: murmurhash(const void *key, int32_t len, uint64_t seed)（lib/hash_func/murmur_hash.h）。由 06 号在 1d 期间提前翻译
/// （translate 单元，路径与签名以 06 号为准），本模块只再导出，供 hash_func 特化表和 09 号使用。
pub use crate::lib_m::hash_func::murmur_hash::murmurhash;
/// C++: do_hash(key) / do_hash(key, seed)（lib/hash_func/ob_hash_func.h:28-62）：键类有 hash() 成员时调用
/// key.hash() / key.hash(hash_val, seed)，否则对键对象的内存字节做 murmurhash(&key, sizeof(key), 0 或 seed)。
/// Rust 用 trait 表达：C++ 有 hash() 成员的键类在自己的单元里实现 DoHash（方法体是那两个成员函数的翻译）；
/// 本模块为整数类型实现"本机字节序的字节做 murmurhash"。其余没有 hash() 成员的类类型（C++ 按对象字节、含填充）
/// 由所在单元显式写出参与哈希的字节，按 RULEBOOK §5.2 第 9 类（POD 视图用 oblib::pod）处理。
pub trait DoHash {
    fn do_hash(&self) -> u64;
    fn do_hash_seed(&self, seed: u64) -> u64;
}
/// C++: do_equal(key1, key2)（:66-70）就是 ==：Rust 用 PartialEq，不另设 trait。
/// C++: fnv1_32_and_fnv1a_32_compile_time_hash / fnv_hash_for_logger（hash/fnv_hash.h:33-60）。
/// 字节按平台 char 的符号性扩展（RULEBOOK §2.12，`uint32_t(str[i])`）。
pub const fn fnv1_32_and_fnv1a_32_compile_time_hash(s: &[u8]) -> u64;
pub const fn fnv_hash_for_logger(s: &[u8]) -> u64;
```

### 2.10 `ObHashMap`、`ObHashSet`

两种接收者由锁模式决定，用两个互斥的 inherent impl 块实现（`NoPthreadDefendMode` 不实现 `LockedDefendMode`，rustc 判定不重叠；已用 1.98.1 编译验证）。

```rust
// rust/port/oblib/src/hash/hashmap.rs
/// C++: ObHashMap<K, V, D, …>（ob_hashmap.h:54-374）+ ObHashTable（ob_hashtable.h:696-1743）。
/// 桶数 = cal_next_prime(bucket_num)，桶号 = hash % 桶数，新节点插在桶链表头（逐行移植，0.11.3）。
/// 内部：NoPthread 是 Option<Box<[Chain]>>；加锁模式是可重建的 [RwLatch<Chain>] 加 AtomicI64 size_（§2.0）。
pub struct ObHashMap<K, V, D: DefendMode = LatchReadWriteDefendMode> { … }

/// 两种模式共有的方法。
impl<K: HashFunc + PartialEq, V, D: DefendMode> ObHashMap<K, V, D> {
    pub fn new() -> Self;
    /// C++: size()（ob_hashmap.h:161-164）、bucket_count()（:165-168）、empty()（:169-172）、created()（:173-176）。
    pub fn size(&self) -> i64;
    pub fn bucket_count(&self) -> i64;
    pub fn empty(&self) -> bool;
    pub fn created(&self) -> bool;
    /// C++: get_refactored(key, value)（ob_hashmap.h:226-240；ob_hashtable.h:1127-1159）：拷出值。
    /// 未 create → OB_NOT_INIT；键的 hash_func 失败 → 原样返回；不存在 → OB_HASH_NOT_EXIST。
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    /// C++: read_atomic（ob_hashmap.h:281-286；ob_hashtable.h:1632-1662）：桶读锁内调用回调；不存在 OB_HASH_NOT_EXIST。
    /// 回调不得访问同一张表（M11）。回调的返回值 C++ 忽略，Rust 回调无返回值。
    pub fn read_atomic(&self, key: &K, callback: impl FnOnce(&HashMapPair<K, V>)) -> ObResult;
    /// C++: foreach_refactored（ob_hashmap.h:291-295；ob_hashtable.h:1483-1521）：桶 0..n、链表从头到尾；
    /// 每个非空桶持读锁调用回调；回调返回错误即停并返回它。回调不得访问同一张表。
    pub fn foreach_refactored(&self, callback: impl FnMut(&HashMapPair<K, V>) -> ObResult) -> ObResult;
    /// C++: foreach_refactored_range（ob_hashtable.h:1525-1558）：[start, end) 夹到 [0, 桶数]。
    pub fn foreach_refactored_range(&self, callback: impl FnMut(&HashMapPair<K, V>) -> ObResult,
                                    start_bucket: i64, end_bucket: i64) -> ObResult;
    /// C++ 的 foreach_refactored 回调里改 entry.second 的用法（如 ob_dependency_info.cpp:939-949）：要独占，不加锁。
    pub fn foreach_refactored_mut(&mut self, callback: impl FnMut(&mut HashMapPair<K, V>) -> ObResult) -> ObResult;
    /// C++: 非 const 的 get(key)（ob_hashmap.h:252-260）。
    pub fn get_mut(&mut self, key: &K) -> Option<&mut V>;
    /// C++: 非 const 迭代器 begin()/end()（:145-160）：顺序同 foreach_refactored。
    pub fn iter_mut(&mut self) -> impl Iterator<Item = &mut HashMapPair<K, V>>;
}

/// NoPthreadDefendMode：普通数据结构，修改要 &mut self。
impl<K: HashFunc + PartialEq, V> ObHashMap<K, V, NoPthreadDefendMode> {
    /// C++: create 的全部重载（ob_hashmap.h:178-209）：bucket_num<=0 → OB_INVALID_ARGUMENT；已 create → OB_INIT_TWICE；
    /// 桶数组分配失败 → OB_ALLOCATE_MEMORY_FAILED（ob_hashtable.h:823-844）。
    pub fn create(&mut self, bucket_num: i64) -> ObResult;
    /// C++: destroy（ob_hashtable.h:846-872）：总是 OB_SUCCESS；之后可以再 create。
    pub fn destroy(&mut self) -> ObResult;
    /// C++: clear / reuse（ob_hashtable.h:874-902）：未 create → OB_NOT_INIT。
    pub fn clear(&mut self) -> ObResult;
    pub fn reuse(&mut self) -> ObResult;
    /// C++: set_refactored(key, value, flag, broadcast, overwrite_key, callback)（ob_hashmap.h:261-273；ob_hashtable.h:1207-1288）。
    /// flag==0 且已存在 → OB_HASH_EXIST；flag!=0 且已存在 → 原位置覆盖（键、值都换成新的）；否则头插。
    /// broadcast（只对已不用的 MultiWrite 条件变量有效）、overwrite_key（C++ 未使用，:1213）、callback（全库 0 处）三个参数删除。
    pub fn set_refactored(&mut self, key: K, value: V, flag: i32) -> ObResult;
    /// C++: erase_refactored(key, value = NULL)（ob_hashmap.h:303-316；ob_hashtable.h:1362-1410）：不存在 OB_HASH_NOT_EXIST；
    /// value 非空时把被删的值移进去。
    pub fn erase_refactored(&mut self, key: &K, value: Option<&mut V>) -> ObResult;
    /// C++: erase_if（ob_hashmap.h:342-356；ob_hashtable.h:1418-1477）：键不存在 OB_HASH_NOT_EXIST；
    /// pred 为 false → OB_SUCCESS 且 is_erased=false。
    pub fn erase_if(&mut self, key: &K, pred: impl FnOnce(&HashMapPair<K, V>) -> bool, is_erased: &mut bool, value: Option<&mut V>) -> ObResult;
    /// C++: atomic_refactored（ob_hashmap.h:274-279；ob_hashtable.h:1665-1695）：不存在 OB_HASH_NOT_EXIST。
    pub fn atomic_refactored(&mut self, key: &K, callback: impl FnOnce(&mut HashMapPair<K, V>)) -> ObResult;
    /// C++: set_or_update（ob_hashmap.h:323-335；ob_hashtable.h:1698-1729）：存在则回调，否则插入。
    pub fn set_or_update(&mut self, key: K, value: V, callback: impl FnOnce(&mut HashMapPair<K, V>)) -> ObResult;
    /// C++: const get(key)（ob_hashmap.h:241-250）。
    pub fn get(&self, key: &K) -> Option<&V>;
    /// C++: const 迭代器（:149-160）。
    pub fn iter(&self) -> impl Iterator<Item = &HashMapPair<K, V>>;
}

/// 加锁模式：内部同步，全部 &self；引用式访问要 &mut self。
impl<K: HashFunc + PartialEq, V, D: LockedDefendMode> ObHashMap<K, V, D> {
    pub fn create(&self, bucket_num: i64) -> ObResult;
    pub fn destroy(&self) -> ObResult;
    /// clear 逐桶持写锁（ob_hashtable.h:880-893）。
    pub fn clear(&self) -> ObResult;
    pub fn reuse(&self) -> ObResult;
    /// 桶写锁内完成查重、覆盖或头插。
    pub fn set_refactored(&self, key: K, value: V, flag: i32) -> ObResult;
    pub fn erase_refactored(&self, key: &K, value: Option<&mut V>) -> ObResult;
    pub fn erase_if(&self, key: &K, pred: impl FnOnce(&HashMapPair<K, V>) -> bool, is_erased: &mut bool, value: Option<&mut V>) -> ObResult;
    /// 桶写锁内调用回调（M11）；回调不得访问同一张表。
    pub fn atomic_refactored(&self, key: &K, callback: impl FnOnce(&mut HashMapPair<K, V>)) -> ObResult;
    pub fn set_or_update(&self, key: K, value: V, callback: impl FnOnce(&mut HashMapPair<K, V>)) -> ObResult;
    pub fn get(&mut self, key: &K) -> Option<&V>;
    pub fn iter(&mut self) -> impl Iterator<Item = &HashMapPair<K, V>>;
}
impl<K, V, D: DefendMode> Default for ObHashMap<K, V, D> { … }
// Drop：析构时 destroy（ob_hashtable.h:812-817）。
// Sync：K、V 都 Send + Sync 时，加锁模式的表是 Sync；NoPthread 的表在 K、V Sync 时也是 Sync（没有内部可变性）。

// rust/port/oblib/src/hash/hashset.rs
/// C++: ObHashSet<K, D = ReadWriteDefendMode, …>（ob_hashset.h:41-235，默认锁模式在 :43）。回调和迭代的元素是 HashMapPair<K, HashNullObj>。
pub struct ObHashSet<K, D: DefendMode = ReadWriteDefendMode> { … }
impl<K: HashFunc + PartialEq, D: DefendMode> ObHashSet<K, D> {
    pub fn new() -> Self;
    pub fn created(&self) -> bool;
    pub fn size(&self) -> i64;
    pub fn empty(&self) -> bool;
    /// C++: exist_refactored（:157-166）：存在返回 OB_HASH_EXIST（作为 Err），不存在 OB_HASH_NOT_EXIST，其他错误原样。
    pub fn exist_refactored(&self, key: &K) -> ObResult;
    pub fn foreach_refactored(&self, callback: impl FnMut(&HashMapPair<K, HashNullObj>) -> ObResult) -> ObResult;
}
impl<K: HashFunc + PartialEq> ObHashSet<K, NoPthreadDefendMode> {
    pub fn create(&mut self, bucket_num: i64) -> ObResult;
    pub fn destroy(&mut self) -> ObResult;
    pub fn clear(&mut self) -> ObResult;
    /// C++: reuse() 返回 void（:140-143）。
    pub fn reuse(&mut self);
    /// C++: set_refactored(key, flag = 1, …)（:172-182）：注意默认 flag 是 1（覆盖），与 ObHashMap 的 0 不同；翻译时照 C++ 写出实际值。
    pub fn set_refactored(&mut self, key: K, flag: i32) -> ObResult;
    /// C++: set_refactored_1(key, overwrite_key)（:184-194）：等于 flag=1。约 34 处，2 个文件。
    pub fn set_refactored_1(&mut self, key: K, overwrite_key: bool) -> ObResult;
    pub fn erase_refactored(&mut self, key: &K) -> ObResult;
    /// C++: get(key)（:144-152）：返回表里那个键。
    pub fn get(&self, key: &K) -> Option<&K>;
    pub fn iter(&self) -> impl Iterator<Item = &HashMapPair<K, HashNullObj>>;
}
impl<K: HashFunc + PartialEq, D: LockedDefendMode> ObHashSet<K, D> {
    pub fn create(&self, bucket_num: i64) -> ObResult;
    pub fn destroy(&self) -> ObResult;
    pub fn clear(&self) -> ObResult;
    pub fn reuse(&self);
    pub fn set_refactored(&self, key: K, flag: i32) -> ObResult;
    pub fn set_refactored_1(&self, key: K, overwrite_key: bool) -> ObResult;
    pub fn erase_refactored(&self, key: &K) -> ObResult;
    pub fn get(&mut self, key: &K) -> Option<&K>;
    pub fn iter(&mut self) -> impl Iterator<Item = &HashMapPair<K, HashNullObj>>;
}
impl<K: ObToString, D: DefendMode> ObToString for ObHashSet<K, D> { … }   // :202-211
```

不移植（基线无使用者）：`EXTEND_RATIO>1` 的 `extend()`（ob_hashtable.h:1290-1334；`EXTEND_RATIO` 在库外 0 处作为模板实参）、`BigArray` 桶数组（0 处）、`get_refactored` 的 `timeout_us` 与"假节点"（ob_hashtable.h:1080-1120；带第三个实参的调用 0 处）、`serialization`/`deserialization`（0 处）、`bucket_begin`/`get_bucket_iterator`（`ob_dtl_interm_result_manager.cpp:169,400` 两处，改写见 §6 例 6）、`PrintFunctor`（用 `foreach_refactored` 加打印闭包代替）、`get_local_allocer`。

### 2.11 `BucketedHashMap`：`ObHashMap<…, NoPthreadDefendMode>` + 外部 `ObBucketLock`

```rust
// rust/port/oblib/src/hash/bucketed.rs
/// RULEBOOK 0.9、§2.7、F3、M1、C28。桶数 = C++ 桶锁的桶数 = C++ 表的桶数 = cal_next_prime(bucket_num)
/// （ob_resource_map.h:219-232 校验两者相等；ob_tablet_stat_mgr.cpp:500-503）。桶号 = hash % 桶数，
/// 与 ObBucketLock::get_bucket_idx（ob_bucket_lock.h:337-341）和表的桶号（ob_hashtable.h:1139）一致。
/// 锁的粒度照 C++ ObBucketLock（02 号 §2.7 BucketLatch）：锁数 = 桶数 / 8 + 1，桶 i 由第 i / 8 把锁保护。
/// 实现：每把锁是一个 RwLatch<[桶链表]>，锁里的数据就是它保护的那至多 8 个桶（头插链表），没有 unsafe。
/// 下文"锁组"指一把锁连同它的桶。不得退化成整表一把锁。
pub struct BucketedHashMap<K, V> { … }
impl<K: HashFunc + PartialEq, V> BucketedHashMap<K, V> {
    pub fn new() -> Self;
    /// C++: bucket_lock_.init(bkt_num, latch_id) + map_.create(bkt_num)（ob_resource_map.h:209-240）。
    /// OB_INVALID_ARGUMENT（≤0）、OB_INIT_TWICE、OB_ALLOCATE_MEMORY_FAILED。
    pub fn create(&self, bucket_num: i64, latch_id: ObLatchIds) -> ObResult;
    pub fn destroy(&self);
    pub fn created(&self) -> bool;
    pub fn bucket_count(&self) -> i64;
    pub fn size(&self) -> i64;
    /// C++: hash_func_(key, hash_val)（ob_resource_map.h:194,274）。
    pub fn hash_func(&self, key: &K, hash_val: &mut u64) -> ObResult;
    /// C++: ObBucketHashRLockGuard(bucket_lock_, hash_val)（ob_bucket_lock.h:303-315）。
    pub fn rdlock(&self, hash_val: u64) -> BucketedHashMapRLockGuard<'_, K, V>;
    /// C++: ObBucketHashWLockGuard(bucket_lock_, hash_val)（:317-329）。
    pub fn wrlock(&self, hash_val: u64) -> BucketedHashMapWLockGuard<'_, K, V>;
    /// C++: ObBucketWLockAllGuard（:160-197）：按锁号从小到大全部写锁。
    pub fn wrlock_all(&self) -> BucketedHashMapWLockAllGuard<'_, K, V>;
    /// C++: ObBucketTryWLockAllGuard / ObBucketTryRLockAllGuard（:199-287）：任一把锁拿不到 → OB_EAGAIN（已拿到的放掉）。
    pub fn try_wrlock_all(&self) -> ObResult<BucketedHashMapWLockAllGuard<'_, K, V>>;
    pub fn try_rdlock_all(&self) -> ObResult<BucketedHashMapRLockAllGuard<'_, K, V>>;
}
/// 单锁组读锁：只能访问桶落在本锁组的键（与 C++ 相同：同一把锁下的别的桶也受保护）；
/// 落在别的锁组 → OB_ERR_UNEXPECTED 并记 ERROR（C++ 此时是无锁并发访问）。
impl<'a, K: HashFunc + PartialEq, V> BucketedHashMapRLockGuard<'a, K, V> {
    /// C++: map_.get_refactored(key, v)（在桶锁下，如 ob_resource_map.h:294）：OB_HASH_NOT_EXIST。
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    pub fn get(&self, key: &K) -> Option<&V>;
}
/// 单锁组写锁：同上，另有修改操作。
impl<'a, K: HashFunc + PartialEq, V> BucketedHashMapWLockGuard<'a, K, V> {
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    pub fn get(&self, key: &K) -> Option<&V>;
    pub fn get_mut(&mut self, key: &K) -> Option<&mut V>;
    pub fn set_refactored(&mut self, key: K, value: V, flag: i32) -> ObResult;
    pub fn erase_refactored(&mut self, key: &K, value: Option<&mut V>) -> ObResult;
}
/// 全表写锁：任何键都可以，另有遍历（C++ 在 ObBucketWLockAllGuard 下调用 map_.foreach_refactored，ob_resource_map.h:381-395）。
impl<'a, K: HashFunc + PartialEq, V> BucketedHashMapWLockAllGuard<'a, K, V> {
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    pub fn set_refactored(&mut self, key: K, value: V, flag: i32) -> ObResult;
    pub fn erase_refactored(&mut self, key: &K, value: Option<&mut V>) -> ObResult;
    pub fn foreach_refactored(&self, callback: impl FnMut(&HashMapPair<K, V>) -> ObResult) -> ObResult;
    pub fn foreach_refactored_mut(&mut self, callback: impl FnMut(&mut HashMapPair<K, V>) -> ObResult) -> ObResult;
}
impl<'a, K: HashFunc + PartialEq, V> BucketedHashMapRLockAllGuard<'a, K, V> {
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    pub fn foreach_refactored(&self, callback: impl FnMut(&HashMapPair<K, V>) -> ObResult) -> ObResult;
}
```

### 2.12 `ObLinkHashMap`

```rust
// rust/port/oblib/src/hash/link_hashmap.rs
/// C++ ObLinkHashMap 的 Key 要求（ob_link_hashmap.h:105-107）："must implement compare() and hash()"。
/// 规则：C++ 键类的 `uint64_t hash() const` 与 `int compare(const K&) const` 译进这个 impl。
pub trait LinkHashKey: Clone + Send + Sync + 'static {
    /// C++: Key::hash()，DCHash 用 (hash | 2) & !1 排序（ob_dchash.h:192）。
    fn link_hash(&self) -> u64;
    /// C++: Key::compare(that)，哈希相同时的次序（ob_dchash.h:181-191）。
    fn link_compare(&self, other: &Self) -> i32;
}

/// C++: ObLinkHashMap<K, V, AllocHandle, RefHandle, SHRINK_THRESHOLD>（ob_link_hashmap.h:108-588）。
/// 值是 Arc<V>（§2.7）：get 返回克隆，revert 就是 drop；V 的可变字段按 §2.7 放进锁或原子量。
/// 内部实现可用 dashmap（固定哈希器：直接用 link_hash）或自写分片表；对外语义见 §3 LH。
pub struct ObLinkHashMap<K: LinkHashKey, V: Send + Sync + 'static> { … }
/// C++: ObAllocatingLinkHashMap（:590-611）：create() 与基类相同；clean_sessions() 属于会话的分配器，见 §6 例 9。
pub type ObAllocatingLinkHashMap<K, V> = ObLinkHashMap<K, V>;

impl<K: LinkHashKey, V: Send + Sync + 'static> ObLinkHashMap<K, V> {
    /// C++: ObLinkHashMap(min_size = 1<<13, max_size = INT64_MAX)（:195-206）：只影响内部桶数组大小。
    pub fn new() -> Self;
    pub fn with_size(min_size: i64, max_size: i64) -> Self;
    /// C++: init(label/attr)（:211-223）。
    pub fn init(&self) -> ObResult;
    /// C++: reset()（:225-228）：等于 remove_if(always_true)。
    pub fn reset(&self);
    /// C++: destroy()（:230-239）、purge()（:241-250；Rust 没有退休表，空操作）。
    pub fn destroy(&self);
    pub fn purge(&self);
    /// C++: count()（DCHash 节点计数，:251）、size()（CountHandle，:257）。
    pub fn count(&self) -> i64;
    pub fn size(&self) -> i64;
    /// C++: alloc_value(value*&)（:258-265）：new 一个 V::default()。
    pub fn alloc_value(&self, value: &mut Option<Arc<V>>) -> ObResult where V: Default;
    /// C++: free_value(value)（:266-275）：drop。
    pub fn free_value(&self, value: Arc<V>);
    /// C++: create(key, value*&)（:276-287）：alloc_value + insert_and_get；失败时 value 置 None。
    pub fn create(&self, key: &K, value: &mut Option<Arc<V>>) -> ObResult where V: Default;
    /// C++: insert_and_get(key, value)（:290-325）：表里存一份克隆，调用方手里的 Arc 就是 C++ 里"必须 revert 的那次引用"。
    /// 已存在 OB_ENTRY_EXIST；分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn insert_and_get(&self, key: &K, value: &Arc<V>) -> ObResult;
    /// C++: del(key)（:327-346）：不存在 OB_ENTRY_NOT_EXIST。值在最后一个 Arc 释放时析构（C++ uref 归零时 free_value）。
    pub fn del(&self, key: &K) -> ObResult;
    /// C++: get(key, value*&)（:348-372）：不存在 OB_ENTRY_NOT_EXIST。
    pub fn get(&self, key: &K, value: &mut Option<Arc<V>>) -> ObResult;
    /// C++: operate(key, fn)（:374-386）：不加引用地调用 fn(key, value)；不存在 OB_ENTRY_NOT_EXIST。
    pub fn operate(&self, key: &K, f: impl FnOnce(&K, &Arc<V>)) -> ObResult;
    /// C++: revert(value)（:387-406）。
    pub fn revert(&self, value: Arc<V>);
    /// C++: contains_key（:407-415）：存在返回 OB_ENTRY_EXIST（作为 Err），不存在 OB_ENTRY_NOT_EXIST。
    pub fn contains_key(&self, key: &K) -> ObResult;
    /// C++: map(fn) / for_each(fn)（:417-436）：按 DCHash 序遍历快照；fn 返回 false 即停并返回 OB_EAGAIN；
    /// 回调时不持任何内部锁，可以对同一张表 del（§2.7）。
    pub fn map(&self, f: impl FnMut(&K, &Arc<V>) -> bool) -> ObResult;
    pub fn for_each(&self, f: impl FnMut(&K, &Arc<V>) -> bool) -> ObResult;
    /// C++: remove_if(fn)（:438-442,561-579）：按 DCHash 序遍历快照，fn 为 true 的 del；总是返回 OB_SUCCESS。
    pub fn remove_if(&self, f: impl FnMut(&K, &Arc<V>) -> bool) -> ObResult;
}
```

### 2.13 `ObLinearHashMap`

```rust
// rust/port/oblib/src/hash/linear_hash_map.rs
/// C++: ObLinearHashMap<K, V, MemMgrTag>（ob_linear_hash_map.h:197-575）。Rust 不做线性哈希，改为分片表：
/// 每个分片一把 RwLatch（latch id HASH_MAP_LOCK；C++ 的桶锁是桶标志字里的自旋位，没有 latch id，§3.9 LN8）加一张可增长的链式表。
/// 分片数默认 max(16, next_pow2(4 × CPU 数))，上限 1024（满足 0.9 RowHolderMapper"分片数按核数放大"）。
pub struct ObLinearHashMap<K, V> { … }
impl<K: HashFunc + PartialEq + Clone, V> ObLinearHashMap<K, V> {
    pub fn new() -> Self;
    /// C++: init(label)、init(attr)、init(m_seg_sz, s_seg_sz, dir_init_sz, attr)（:400-402,888-938）：已 init → OB_INIT_TWICE。
    pub fn init(&self) -> ObResult;
    pub fn destroy(&self) -> ObResult;
    pub fn clear(&self) -> ObResult;
    pub fn reset(&self) -> ObResult;
    pub fn is_inited(&self) -> bool;
    /// C++: insert（:988-993,1861-1907）：已存在 OB_ENTRY_EXIST；未 init OB_NOT_INIT。
    pub fn insert(&self, key: K, value: V) -> ObResult;
    /// C++: insert_or_update（:994-999,1974-2026）：存在则覆盖值。
    pub fn insert_or_update(&self, key: K, value: V) -> ObResult;
    /// C++: get(key, value)（:1000-1005,2028-2058）：OB_ENTRY_NOT_EXIST。
    pub fn get(&self, key: &K, value: &mut V) -> ObResult where V: Clone;
    /// C++: erase(key) / erase(key, value&)（:1012-1023,2094-2156）：OB_ENTRY_NOT_EXIST。
    pub fn erase(&self, key: &K) -> ObResult;
    pub fn erase_into(&self, key: &K, value: &mut V) -> ObResult;
    /// C++: count()（:1037-1041）。
    pub fn count(&self) -> u64;
    /// C++: get_bkt_cnt()（:421）：Rust 返回分片表的桶总数，只用于日志。
    pub fn get_bkt_cnt(&self) -> u64;
    /// 以下四个在**分片写锁内**原子完成（§2.7、F3）；闭包不得访问同一张表（C++ :123-130 的 CAUTION）。
    /// C++: operate（:1079-1085,2377-2414）：fn 返回 false → OB_EAGAIN；不存在 OB_ENTRY_NOT_EXIST。
    pub fn operate(&self, key: &K, f: impl FnOnce(&K, &mut V) -> bool) -> ObResult;
    /// C++: insert_or_operate（:2497-2502,1910-1972）：存在则 fn（false → OB_EAGAIN），否则插入。
    pub fn insert_or_operate(&self, key: K, value: V, f: impl FnOnce(&K, &mut V) -> bool) -> ObResult;
    /// C++: erase_if（:1071-1077,2158-2223）：fn 为 true 删除并 OB_SUCCESS；false → OB_EAGAIN；不存在 OB_ENTRY_NOT_EXIST。
    pub fn erase_if(&self, key: &K, f: impl FnOnce(&K, &mut V) -> bool) -> ObResult;
    /// C++: remove_if（:1057-1069,2436-2489）：逐分片持写锁调用 f，true 的删除；总是 OB_SUCCESS（未 init OB_NOT_INIT）。
    pub fn remove_if(&self, f: impl FnMut(&K, &mut V) -> bool) -> ObResult;
    /// C++: for_each（:1043-1055,2348-2434）：逐分片取快照（克隆键值）、放锁后回调；f 返回 false → OB_EAGAIN（§2.7）。
    /// 回调拿到的是快照里的副本，所以只给 &V：C++ 里在回调中改值的写法要改用 operate。
    pub fn for_each(&self, f: impl FnMut(&K, &V) -> bool) -> ObResult where K: Clone, V: Clone;
}

/// C++: ObLinearHashMap::BlurredIterator（:306-329,847-880）。C++ 构造时绑定表；Rust 不绑定，每次 next 传入表
/// （ob_block_manager.h:455 把迭代器和表放在同一个对象里，Rust 不能自引用）。
pub struct BlurredIterator { shard_idx_: usize, pos_: usize }
impl BlurredIterator {
    pub const fn new() -> Self;
    /// 取下一个键值，结束时 OB_ITER_END；并发修改时可能漏掉或重复（同 C++ :118-121）。
    pub fn next<K: HashFunc + PartialEq + Clone, V: Clone>(&mut self, map: &ObLinearHashMap<K, V>, key: &mut K, value: &mut V) -> ObResult;
    pub fn rewind(&mut self);
}
```

### 2.14 `FixedHash2`（行锁等待表）

```rust
// rust/port/oblib/src/hash/fixed_hash2.rs
/// C++: SpHashNode 基类（ob_fixed_hash2.h:28-36）与 Node::compare（ob_lock_wait_node.cpp:69-91）。
/// 节点类型（ObLockWaitNode 或持有它的请求）实现这个 trait。
pub trait SpHashNode: Send + 'static {
    type OrderKey: Ord + Copy + Send;
    /// C++: hash()（ob_fixed_hash2.h:34）。ObLockWaitNode 的 hash_ 由 change_hash 置为 hash | 1（ob_lock_wait_node.cpp:63-67）。
    fn sp_hash(&self) -> u64;
    /// C++: compare 里 hash 之后的键。ObLockWaitNode：(recv_ts_, abs_timeout_)。第四键 addr_ 由插入序号代替（RULEBOOK §2.7）。
    fn sp_order_key(&self) -> Self::OrderKey;
}

/// 节点在表里的句柄（C++ 用节点指针当句柄）。按 (hash, order, seq) 全序。
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub struct FixedHash2Key<O> { hash_: u64, order_: O, seq_: u64 }
impl<O: Copy> FixedHash2Key<O> {
    /// C++: node->hash()。
    pub fn hash(&self) -> u64;
}

/// C++: FixedHash2<Node>（ob_fixed_hash2.h:38-120）：全部节点按 (hash, order, seq) 排成一个序；
/// 分片号 = hash >> shift（:105，shift = clz(limit)+1），分片数 = limit = last2n(bucket_count)；每片一把 Latch 加一个有序表。
/// 表拥有节点（Box<N>）：请求在锁等待期间的"挂起"就是把所有权移进来（§2.7）。
pub struct FixedHash2<N: SpHashNode> { … }
impl<N: SpHashNode> FixedHash2<N> {
    /// C++: FixedHash2(buf, size)（:43-46）。ObLockWaitMgr 的 hash_(hash_buf_, sizeof(hash_buf_)) 写成
    /// FixedHash2::new(LOCK_BUCKET_COUNT)（ob_lock_wait_mgr.h:184,331-333；16 字节一个 SpHashNode，所以 limit = 16384）。
    pub fn new(bucket_count: i64) -> Self;
    /// C++: insert(node)（:49）：不会失败（C++ 的 -EEXIST 因 addr_ 唯一而不可能；Rust 因 seq 唯一而不可能）。
    pub fn insert(&self, node: Box<N>) -> FixedHash2Key<N::OrderKey>;
    /// C++: del(node, tmp)（:50）：取走成功返回 0，节点已不在表里返回 -ENOENT（libc 值）。
    pub fn del(&self, key: &FixedHash2Key<N::OrderKey>, node: &mut Option<Box<N>>) -> i32;
    /// C++: get(hash, node)（:82-88）：该 hash 的第一个节点。
    pub fn get_first(&self, hash: u64) -> Option<FixedHash2Key<N::OrderKey>>;
    /// C++: get_next_internal(hash) 再沿 link_next 跳过较小 hash 的写法（ob_lock_wait_mgr.cpp:363-366,383-385）：
    /// 第一个 hash ≥ 给定值的节点。
    pub fn lower_bound(&self, hash: u64) -> Option<FixedHash2Key<N::OrderKey>>;
    /// C++: quick_next(cur) / next(cur)（:57-68）：cur 之后的下一个节点（cur 为 None 时第一个）；cur 已被删除也可以继续。
    pub fn quick_next(&self, cur: Option<&FixedHash2Key<N::OrderKey>>) -> Option<FixedHash2Key<N::OrderKey>>;
    pub fn next(&self, cur: Option<&FixedHash2Key<N::OrderKey>>) -> Option<FixedHash2Key<N::OrderKey>>;
    /// C++ 经节点指针读写节点字段：在节点所在分片的锁内调用 f；节点已不在表里返回 None。
    /// f 不得改变 sp_hash()/sp_order_key() 的结果（debug 构建会断言）。
    pub fn with_node<R>(&self, key: &FixedHash2Key<N::OrderKey>, f: impl FnOnce(&mut N) -> R) -> Option<R>;
    /// C++: is_empty()（:69-81）。
    pub fn is_empty(&self) -> bool;
}
```

### 2.15 其余哈希结构（逐行移植，接口照 C++）

```rust
// rust/port/oblib/src/hash/placement.rs
/// C++: ObPlacementHashMap<K, V, N=1031>（ob_placement_hashmap.h:30-175）：N 个槽的线性探测，起点 do_hash(key) % N，
/// 无删除；满 OB_HASH_FULL（ob_placement_hashutils.h:84-108）。
pub struct ObPlacementHashMap<K, V, const N: usize = 1031> { … }
impl<K: DoHash + PartialEq, V, const N: usize> ObPlacementHashMap<K, V, N> {
    pub fn new() -> Self;
    /// C++: set_refactored(key, value, flag = 0, overwrite_key = 0)（:47）：flag=0 且存在 OB_HASH_EXIST。
    pub fn set_refactored(&mut self, key: K, value: V, flag: i32, overwrite_key: i32) -> ObResult;
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult where V: Clone;   // OB_HASH_NOT_EXIST
    pub fn get(&self, key: &K) -> Option<&V>;
    pub fn get_mut(&mut self, key: &K) -> Option<&mut V>;
    pub fn reset(&mut self);
}
/// C++: ObPlacementHashSet<K, N=1031>（ob_placement_hashset.h:28-98）。
pub struct ObPlacementHashSet<K, const N: usize = 1031> { … }
impl<K: DoHash + PartialEq, const N: usize> ObPlacementHashSet<K, N> {
    pub fn new() -> Self;
    pub fn set_refactored(&mut self, key: K) -> ObResult;      // 存在 OB_HASH_EXIST，满 OB_HASH_FULL
    pub fn exist_refactored(&self, key: &K) -> ObResult;        // OB_HASH_EXIST / OB_HASH_NOT_EXIST
    pub fn clear(&mut self);
    pub fn reset(&mut self);
}
/// C++: ObIteratableHashSet<K, N=1031>（ob_iteratable_hashset.h:79-190）：按插入序迭代。
pub struct ObIteratableHashSet<K, const N: usize = 1031> { … }
impl<K: DoHash + PartialEq + Clone, const N: usize> ObIteratableHashSet<K, N> {
    pub fn new() -> Self;
    pub fn set_refactored(&mut self, key: K) -> ObResult;
    pub fn exist_refactored(&self, key: &K) -> ObResult;
    pub fn reset(&mut self);
    pub fn clear(&mut self);
    pub fn iter(&self) -> impl Iterator<Item = &K>;   // C++ begin()/end()
    pub fn count(&self) -> i64;
}
/// C++: ObArrayIndexHashSet<ItemArray, Item, N=1031>（ob_array_index_hash_set.h:34-150）：存的是外部数组的下标。
pub struct ObArrayIndexHashSet<Item, const N: usize = 1031> { … }
impl<Item: DoHash + PartialEq, const N: usize> ObArrayIndexHashSet<Item, N> {
    pub fn new() -> Self;
    /// C++: init(array)（:55-61）：Rust 不保存数组引用，set_index/get_index 时传入。
    pub fn set_index(&mut self, array: &[Item], idx: u64) -> ObResult;            // OB_INVALID_ARGUMENT / OB_ENTRY_EXIST / OB_HASH_FULL
    pub fn get_index(&self, array: &[Item], item: &Item, idx: &mut u64) -> ObResult;  // OB_ENTRY_NOT_EXIST
    pub fn get_collision_count(&self) -> u64;
    pub fn reset(&mut self);
}

// rust/port/oblib/src/hash/pointer_hashmap.rs
/// C++: ObPointerHashMap<K, V, GetKey>（ob_pointer_hashmap.h:445-960）：值是指针，键由 GetKey 从值取出；
/// 线性探测（起点 do_hash(key) & anchor_mask_，越界再取模，:340-345），空槽 / 墓碑 / 值三态，子表按需扩容（:781-916）。
/// Rust：值 V（多为 Arc<T> 或 id），GetKey 是 trait。
pub trait GetKey<K, V> { fn get_key(value: &V) -> K; }
pub struct ObPointerHashMap<K, V, G: GetKey<K, V>> { … }
impl<K: DoHash + PartialEq, V: Clone, G: GetKey<K, V>> ObPointerHashMap<K, V, G> {
    pub fn new() -> Self;
    pub fn init(&mut self) -> ObResult;                 // OB_ALLOCATE_MEMORY_FAILED
    pub fn assign(&mut self, other: &Self) -> ObResult;
    pub fn destroy(&mut self);
    /// C++: set_refactored(key, value, overwrite = 0, overwrite_key = 0)（:585-594）与带 over_write_value 的重载（:543-583）。
    pub fn set_refactored(&mut self, key: &K, value: V, overwrite: i32, overwrite_key: i32) -> ObResult;
    pub fn set_refactored_into(&mut self, key: &K, value: V, over_write_value: &mut V, overwrite: i32, overwrite_key: i32) -> ObResult;
    pub fn get_refactored(&self, key: &K, value: &mut V) -> ObResult;   // OB_HASH_NOT_EXIST / OB_NOT_INIT
    pub fn get(&self, key: &K) -> Option<&V>;
    pub fn erase_refactored(&mut self, key: &K, erased_value: Option<&mut V>) -> ObResult;
    pub fn clear(&mut self);
    pub fn count(&self) -> i64;
    pub fn item_count(&self) -> i64;
    pub fn set_sub_map_mem_size(&mut self, sub_map_mem_size: i64);
    /// C++: begin(sub_map_id)/end(sub_map_id) 与 erase(iterator, sub_map_id)（:699-770）：按子表、按槽位顺序。
    pub fn sub_map_count(&self) -> i64;
    pub fn iter_sub_map(&self, sub_map_id: i64) -> impl Iterator<Item = &V>;
    pub fn dump_keys(&self);
}

// rust/port/oblib/src/hash/array_hash_map.rs
/// C++: ObArrayHashMap<key_t, val_t>（ob_array_hash_map.h:27-255）：定长线性探测（起点 key.hash() % capacity），
/// 一把 ObSpinLock（latch id HASH_MAP_LOCK，:31,55）→ 02 号的 Latch；无删除。内部同步，&self。
pub struct ObArrayHashMap<K, V> { … }
impl<K: DoHash + PartialEq + Clone, V: Clone> ObArrayHashMap<K, V> {
    pub fn new() -> Self;
    /// C++: init(attr/label, capacity)（:58-79）：capacity ≤ 0 → OB_INVALID_ARGUMENT；已 init → OB_INIT_TWICE；分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn init(&self, capacity: i64) -> ObResult;
    pub fn destroy(&self);
    pub fn reset(&self);
    pub fn size(&self) -> i64;
    pub fn is_inited(&self) -> bool;
    /// C++: get（:160-179）：遇空槽即 OB_ENTRY_NOT_EXIST；未 init OB_NOT_INIT。
    pub fn get(&self, key: &K, val: &mut V) -> ObResult;
    /// C++: insert（:181-203）：遇相等键 OB_ENTRY_EXIST；探完无空槽 OB_SIZE_OVERFLOW；未 init OB_NOT_INIT。
    pub fn insert(&self, key: &K, val: &V) -> ObResult;
    /// C++: update（:205-224）：遇空槽 OB_ENTRY_NOT_EXIST；探完 OB_SIZE_OVERFLOW。
    pub fn update(&self, key: &K, val: &V) -> ObResult;
    /// C++: for_each（:226-239）：按槽位顺序，每项单独持锁调用；f 返回 false → OB_ERR_UNEXPECTED 并停；未 init 时什么也不做、返回成功。
    pub fn for_each(&self, f: impl FnMut(&K, &V) -> bool) -> ObResult;
}

// rust/port/oblib/src/hash/build_in_hashmap.rs
/// C++ 模板参数 H（Hasher，如 ob_load_data_impl.h:269-276 的 ObPartDataFragHash）的要求：Key、Value、key()、hash()、equal()。
pub trait ObBuildInHashDesc {
    type Key: Copy;
    type Value;
    fn key(value: &Self::Value) -> Self::Key;
    fn hash(key: Self::Key) -> u64;
    fn equal(lhs: Self::Key, rhs: Self::Key) -> bool;
}
/// C++: ObBuildInHashMap<H, bucket_num = 16>（ob_build_in_hashmap.h:67-246）：值自带键，侵入式桶链（DLL，头插）。
/// 唯一实例 ob_load_data_impl.h:305（N = 100）。Rust：表持有值 V（翻译者按归属写 Box<X>、Arc<X> 或 id），桶号 = hash(key) % N。
pub struct ObBuildInHashMap<D: ObBuildInHashDesc, const N: usize = 16> { … }
impl<D: ObBuildInHashDesc, const N: usize> ObBuildInHashMap<D, N> {
    pub fn new() -> Self;
    /// C++: reset()（:144-147）：C++ 只清表、不碰值；Rust 丢弃表持有的值。
    pub fn reset(&mut self);
    pub fn count(&self) -> i64;
    /// C++: set_refactored(value)（:163-183）：见 §3.11 P5，Rust 总是成功。
    pub fn set_refactored(&mut self, value: D::Value) -> ObResult;
    /// C++: get_refactored(key, value*&)（:185-199）、get(key)（:201-209）：桶内从链表头起第一个 equal 的；不存在 OB_HASH_NOT_EXIST / None。
    pub fn get_refactored<'a>(&'a self, key: D::Key, value: &mut Option<&'a D::Value>) -> ObResult;
    pub fn get(&self, key: D::Key) -> Option<&D::Value>;
    pub fn get_mut(&mut self, key: D::Key) -> Option<&mut D::Value>;
    /// C++: remove(key)（:211-228）、erase_refactored(key)（:230-241，不存在 OB_HASH_NOT_EXIST）。
    pub fn remove(&mut self, key: D::Key) -> Option<D::Value>;
    pub fn erase_refactored(&mut self, key: D::Key) -> ObResult;
    /// C++: begin()/end()（:152-160），顺序见 §3.11 P5。
    pub fn iter(&self) -> impl Iterator<Item = &D::Value>;
}
```

`GetKey`、`ObBuildInHashDesc` 是 C++ 模板参数（`GetKey` 函子、`H` 描述类）对应的 trait；名字分别取自 C++ 模板参数名和类名（§8 Q1）。

### 2.16 `ObExtendibleRingBuffer`

```rust
// rust/port/oblib/src/container/ext_ring_buffer.rs
/// C++: ObExtendibleRingBuffer<T, RefT>（ob_ext_ring_buffer.h:53-127）："无限长的数组"，[begin_sn, end_sn) 有效。
/// Rust：内部同步（RwLatch<VecDeque<Option<P>>> 加 begin_sn），P 是元素（多为 Arc<T>，读出后与环共享）。
pub struct ObExtendibleRingBuffer<P> { … }
impl<P: Clone + Send + Sync> ObExtendibleRingBuffer<P> {
    pub fn new() -> Self;
    /// C++: init(begin_sn, seg_size)（:60-61）：seg_size 只影响内部分段，Rust 保留参数、不影响行为。
    pub fn init(&self, begin_sn: i64, seg_size: i64) -> ObResult;
    pub fn destroy(&self) -> ObResult;
    pub fn is_inited(&self) -> bool;
    /// C++: get(sn, ptr)（:65-73）：sn < begin → OB_ERR_OUT_OF_LOWER_BOUND；sn ≥ end → OB_ERR_OUT_OF_UPPER_BOUND。
    pub fn get(&self, sn: i64, ptr: &mut Option<P>) -> ObResult;
    /// C++: set(sn, ptr)（:75-83）：sn < begin → OB_ERROR_OUT_OF_RANGE；中间未设的位置为 None。
    pub fn set(&self, sn: i64, ptr: Option<P>) -> ObResult;
    /// C++: pop(cond, ptr, popped, use_lock)（:99-111）：空 OB_ENTRY_NOT_EXIST；cond(begin 处的元素) 为 true 才弹出。
    pub fn pop(&self, cond: impl FnOnce(Option<&P>) -> bool, ptr: &mut Option<P>, popped: &mut bool, use_lock: bool) -> ObResult;
    pub fn begin_sn(&self) -> i64;
    pub fn end_sn(&self) -> i64;
}
```

带条件的 `set(sn, ptr, cond, set)`（:85-97）全库 0 处调用，不提供。

### 2.17 队列

入队对象的 `ObLink`/`QLink` 基类删除：只在一个队列里的对象以 `Box<T>` 入队，所有权随之转移；同时挂在多处的对象（清单第 19 类）以 `Arc<T>` 或 id 入队（§2.7）。用 `ObLink*` 串起来的局部链表（如 `ob_lock_wait_mgr.cpp:508-519` 的 retire 链）改成 `Vec<Box<T>>`。

```rust
// rust/port/oblib/src/queue/fixed_queue.rs
/// C++: ObFixedQueue<T>（ob_fixed_queue.h:29-257）与 ObOrderedFixedQueue<T>（:258-410）：有界 MPMC FIFO。
/// 内部 crossbeam_queue::ArrayQueue<T>（§2.7）。两者在 Rust 里行为相同（ArrayQueue 本身按序）。
pub struct ObFixedQueue<T> { … }
pub type ObOrderedFixedQueue<T> = ObFixedQueue<T>;
impl<T: Send> ObFixedQueue<T> {
    pub const fn new() -> Self;
    /// C++: init(max_num, allocator, label/attr) 与 init(max_num, buf)（:36-42,88-136）：
    /// max_num ≤ 0 → OB_INVALID_ARGUMENT；已 init → OB_INIT_TWICE；分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn init(&self, max_num: i64) -> ObResult;
    /// C++: destroy（:138-153）：丢弃队列里剩下的元素，之后可以再 init。
    pub fn destroy(&self);
    /// C++: push(ptr)（:185-211）：未 init → OB_NOT_INIT；None → OB_INVALID_ARGUMENT；满 → OB_SIZE_OVERFLOW（元素留在 ptr 里）。
    pub fn push(&self, ptr: &mut Option<T>) -> ObResult;
    /// C++: pop(ptr)（:213-237）：未 init → OB_NOT_INIT；空 → OB_ENTRY_NOT_EXIST。
    pub fn pop(&self, ptr: &mut Option<T>) -> ObResult;
    /// C++: get_total / get_free / get_curr_total / capacity / is_inited（:48-52）。
    pub fn get_total(&self) -> i64;
    pub fn get_free(&self) -> i64;
    pub fn get_curr_total(&self) -> i64;
    pub fn capacity(&self) -> i64;
    pub fn is_inited(&self) -> bool;
}

// rust/port/oblib/src/queue/lighty_queue.rs
/// C++: ObLightyQueue（ob_lighty_queue.h:48-88，.cpp:43-198）：有界 MPMC，push 非阻塞，pop 可带超时。
/// 内部：ArrayQueue<T> 加 02 号的 FutexCond（C++ 的 ObLightyCond 数组，n_cond = min(1024, 不超过 capacity 的最大 2 的幂)，:67-71）。
pub struct ObLightyQueue<T> { … }
impl<T: Send> ObLightyQueue<T> {
    pub const fn new() -> Self;
    /// C++: init(capacity, label)（.cpp:43-64）：已 init OB_INIT_TWICE；分配失败 OB_ALLOCATE_MEMORY_FAILED。
    pub fn init(&self, capacity: u64) -> ObResult;
    pub fn destroy(&self);
    /// C++: reset() = clear()（:57-58，.cpp:84-89）：丢弃全部元素。
    pub fn reset(&self);
    pub fn clear(&self);
    pub fn size(&self) -> i64;
    pub fn capacity(&self) -> i64;
    pub fn is_inited(&self) -> bool;
    /// C++: push(p)（.cpp:91-107）：未 init OB_NOT_INIT；满 OB_SIZE_OVERFLOW（不等待）；None OB_INVALID_ARGUMENT。
    pub fn push(&self, p: &mut Option<T>) -> ObResult;
    /// C++: pop(p, timeout = 0)（.cpp:109-133）：未 init OB_NOT_INIT；timeout（相对微秒）内没有元素 → OB_ENTRY_NOT_EXIST；timeout ≤ 0 不等。
    pub fn pop(&self, p: &mut Option<T>, timeout: i64) -> ObResult;
    /// C++: wake_all（.cpp:66-73）：唤醒所有阻塞在 pop 的线程（线程池停止时用）。
    pub fn wake_all(&self);
}

/// C++: LightyQueue（ob_lighty_queue.h:90-113，.cpp:202-249）：push 也可以阻塞。内部 ArrayQueue<T> 加一个 FutexCond（双向共用）。
pub struct LightyQueue<T> { … }
impl<T: Send> LightyQueue<T> {
    pub const fn new() -> Self;
    pub fn init(&self, capacity: u64) -> ObResult;
    pub fn destroy(&self);
    pub fn size(&self) -> i64;
    pub fn curr_size(&self) -> i64;
    pub fn max_size(&self) -> i64;
    pub fn is_inited(&self) -> bool;
    /// C++: push(data, timeout = 0)（.cpp:208-228）：底层 push 成功即返回；否则 timeout ≤ 0 或已到截止时间 → **OB_TIMEOUT**
    /// （不论底层错误是满还是未 init）；否则等待后重试。成功后 signal。
    pub fn push(&self, data: &mut Option<T>, timeout: i64) -> ObResult;
    /// C++: pop(data, timeout = 0)（.cpp:230-249）：到时返回底层的错误（空 OB_ENTRY_NOT_EXIST、未 init OB_NOT_INIT）。成功后 signal。
    pub fn pop(&self, data: &mut Option<T>, timeout: i64) -> ObResult;
}
impl<T> ObToString for LightyQueue<T> { … }   // C++ :106 返回 0：什么也不写

// rust/port/oblib/src/queue/link_queue.rs
/// C++: ObSpLinkQueue（ob_link_queue.h:99-188）：多生产者，消费者经头指针 TAS 串行。内部 Latch<VecDeque<T>>（修订 A4）。
pub struct ObSpLinkQueue<T> { … }
impl<T: Send> ObSpLinkQueue<T> {
    pub const fn new() -> Self;
    /// C++: top(p)（:106-120）：空 OB_EAGAIN；不出队，返回队头的克隆（队头要被查看的队列用 Arc/id 元素）。
    pub fn top(&self, p: &mut Option<T>) -> ObResult where T: Clone;
    /// C++: pop(p)（:121-131）：空 OB_EAGAIN（p 置 None）。
    pub fn pop(&self, p: &mut Option<T>) -> ObResult;
    /// C++: push(p)（:132-144）、push_front(p)（:145-159）：None → OB_INVALID_ARGUMENT。
    pub fn push(&self, p: &mut Option<T>) -> ObResult;
    pub fn push_front(&self, p: &mut Option<T>) -> ObResult;
    pub fn is_empty(&self) -> bool;
}
/// C++: ObSpScLinkQueue（:61-97）：单生产者单消费者。
pub struct ObSpScLinkQueue<T> { … }
impl<T: Send> ObSpScLinkQueue<T> {
    pub const fn new() -> Self;
    pub fn top(&self) -> Option<T> where T: Clone;
    pub fn empty(&self) -> bool;
    pub fn push(&self, p: T);
    pub fn pop(&self) -> Option<T>;
}
/// C++: ObSimpleLinkQueue（:273-295，.cpp:28-67）：非线程安全。
pub struct ObSimpleLinkQueue<T> { … }
impl<T> ObSimpleLinkQueue<T> {
    pub const fn new() -> Self;
    pub fn push(&mut self, p: &mut Option<T>) -> ObResult;   // None → OB_INVALID_ARGUMENT
    pub fn pop(&mut self, p: &mut Option<T>) -> ObResult;    // 空 OB_EAGAIN
    pub fn is_empty(&self) -> bool;
}
/// C++: ObLinkQueueImpl<QUEUE_COUNT = 512>（:190-268）：QUEUE_COUNT 个 ObSpLinkQueue 加 push_/pop_ 两个票号，照原算法移植。
pub struct ObLinkQueueImpl<T, const QUEUE_COUNT: usize = 512> { … }
pub type ObLinkQueue<T> = ObLinkQueueImpl<T, 512>;
pub type ObLinkQueue16<T> = ObLinkQueueImpl<T, 16>;
impl<T: Send, const QUEUE_COUNT: usize> ObLinkQueueImpl<T, QUEUE_COUNT> {
    pub fn new() -> Self;
    pub fn push(&self, p: &mut Option<T>) -> ObResult;        // None → OB_INVALID_ARGUMENT
    pub fn push_front(&self, p: &mut Option<T>) -> ObResult;
    pub fn pop(&self, p: &mut Option<T>) -> ObResult;          // 空 OB_EAGAIN
    pub fn size(&self) -> i64;
    pub fn merge(&self, other: &Self) -> ObResult;
}

// rust/port/oblib/src/queue/priority_queue.rs
/// C++: ObPriorityQueueImpl<PRIOS, QueueType>（ob_priority_queue.h:28-141）。
pub struct ObPriorityQueueImpl<T, const PRIOS: usize, const QUEUE_COUNT: usize = 512> { … }
pub type ObPriorityQueue<T, const PRIOS: usize> = ObPriorityQueueImpl<T, PRIOS, 512>;
pub type ObPriorityQueue16<T, const PRIOS: usize> = ObPriorityQueueImpl<T, PRIOS, 16>;
pub type ObTLinkQueue16<T> = ObPriorityQueue16<T, 1>;
impl<T: Send, const PRIOS: usize, const QUEUE_COUNT: usize> ObPriorityQueueImpl<T, PRIOS, QUEUE_COUNT> {
    pub const PRIO_CNT: usize = PRIOS;
    pub fn new() -> Self;
    pub fn set_limit(&self, limit: i64);
    pub fn size(&self) -> i64;
    /// C++: init(limit, name)（:40-44）。
    pub fn init(&self, limit: i64, name: &[u8]) -> ObResult;
    /// C++: push(data, priority = 0)（:46-63）：先 fetch_add(size,1)，旧值 > limit → OB_SIZE_OVERFLOW；
    /// 再查参数（None 或 priority 越界 → OB_INVALID_ARGUMENT）；失败时 size 减回。
    pub fn push(&self, data: &mut Option<T>, priority: i32) -> ObResult;
    /// C++: push_front（:65-81）：不检查上限。
    pub fn push_front(&self, data: &mut Option<T>, priority: i32) -> ObResult;
    /// C++: pop(data, timeout_us = 0)（:83-106）：timeout<0 → OB_INVALID_ARGUMENT；按优先级 0..PRIOS 取；都空则等待
    /// timeout_us 后返回 OB_ENTRY_NOT_EXIST，**醒来不重试**（调用方循环）。
    pub fn pop(&self, data: &mut Option<T>, timeout_us: i64) -> ObResult;
    pub fn destroy(&self);
    pub fn clear(&self);
    pub fn wake_all(&self);
    pub fn get_queue(&self, i: usize) -> &ObLinkQueueImpl<T, QUEUE_COUNT>;
    pub fn get_prio_cnt(&self) -> i64;
}

/// C++: ObPriorityQueue2<HIGH, NORMAL = 0, LOW = 0>（ob_priority_queue.h:151-300）：SQL 请求队列 ReqQueue（ob_server_runtime.h:50）。
/// 各优先级共享一个上限计数；唤醒按 02 号的 SCondTemp<3>（三个唤醒类）。
pub struct ObPriorityQueue2<T, const HIGH: usize, const NORMAL: usize = 0, const LOW: usize = 0> { … }
impl<T: Send, const H: usize, const N: usize, const L: usize> ObPriorityQueue2<T, H, N, L> {
    pub const PRIO_CNT: usize = H + N + L;
    pub fn new() -> Self;
    pub fn set_limit(&self, limit: i64);
    pub fn get_queue_num(&self) -> i32;           // C++ 恒为 1（:161）
    pub fn size(&self) -> i64;
    pub fn queue_size(&self, prio: i32) -> i64;
    pub fn get_prio_cnt(&self) -> i64;
    /// C++: get_queue(queue_idx, prio)（:171-180）：queue_idx 只能是 0。
    pub fn get_queue(&self, queue_idx: i32, prio: i32) -> Option<&ObLinkQueue<T>>;
    /// C++: push(data, priority, fixed_wakeup_order = false)（:191-225），见 §3 Q5。
    pub fn push(&self, data: &mut Option<T>, priority: i32, fixed_wakeup_order: bool) -> ObResult;
    /// C++: wakeup(priority = PRIO_CNT - 1)（:227-229）。
    pub fn wakeup(&self, priority: i32);
    /// C++: pop(data, timeout_us, index = -1) / pop_normal / pop_high（:231-244）。
    pub fn pop(&self, data: &mut Option<T>, timeout_us: i64, index: i32) -> ObResult;
    pub fn pop_normal(&self, data: &mut Option<T>, timeout_us: i64) -> ObResult;
    pub fn pop_high(&self, data: &mut Option<T>, timeout_us: i64) -> ObResult;
}
impl<T, const H: usize, const N: usize, const L: usize> ObToString for ObPriorityQueue2<T, H, N, L> { … }   // :181-189
```

## 3. 语义

每点给出 C++ 证据。"Rust"一栏只写与 C++ 不同或需要明确的地方；没写的就是照 C++。

### 3.1 数组（A）

- **A1 `at(i)`**：越界在所有构建里 `OB_ASSERT`（ob_array_wrap.h:40-55）。Rust `ob_assert!`（04 号），记 ERROR 加调用栈后 abort。
- **A2 `operator[]`**：`ObSEArray` 越界只记 ERROR "idx out of range" 后照样解引用（ob_array.h:323-336，未定义行为）；`ObFixedArray::operator[]` 就是 `at()`（ob_fixed_array.h:133-134）。Rust 一律译成 `at(i)`，越界 abort，前提见 RULEBOOK A.3；`ObVector::at`（ob_vector.h:127-133，"invalid index"）同理，见修订 A7。不要写 `arr[i as usize]`（切片下标越界会 panic，信息不同）。
- **A3 `at(i, obj)`**：`ObSEArray` 越界 `OB_ARRAY_OUT_OF_RANGE`（ob_array.h:310-320）；`ObFixedArray` 同（ob_fixed_array.h:119-129）；`ObArrayHelper` 未 init 是 `OB_INNER_STAT_ERROR`、越界 `OB_ARRAY_OUT_OF_RANGE`（ob_array_helper.h:125-140）。`[T]` 上的 `at_into` 用 `OB_ARRAY_OUT_OF_RANGE`；`ObConstCArray` 在 C++ 里返回 `OB_INDEX_OUT_OF_RANGE`（ob_expr_operator.h:1801-1810），它的实参在 Rust 里成了切片，越界路径不可达，不再区分。
- **A4 `push_back` 的增长**：C++ 满时容量翻倍（最少 1，ob_array.h:609-615），`reserve` 再按 `block_size_` 取整（:368-372）。Rust 用 `try_reserve`，容量策略不同，只影响 `get_capacity()` 的值和内存；分配失败 `OB_ALLOCATE_MEMORY_FAILED`、count 不变（:617-622）。用户数据决定大小的数组都因此可失败（0.7）。
- **A5 `pop_back`**：空数组时什么也不做（:642-650）；`pop_back(obj)` 空数组 `OB_ENTRY_NOT_EXIST`（:652-664）。`ObFixedArray` 同（ob_fixed_array.h:98-115）。
- **A6 `remove(idx)`**：保序（POD 用 memmove，非 POD 逐个赋值，ob_array.h:136-140,195-206），越界 `OB_ARRAY_OUT_OF_RANGE`（:666-678）；`ObFixedArray::remove` 是 `OB_NOT_IMPLEMENT`（ob_fixed_array.h:117）。
- **A7 `reset`/`destroy`/`reuse`**：`reset` 就是 `destroy`：析构元素、释放堆内存、回到本地缓冲，并把 `max_print_count_` 复位为 32（ob_array.h:363,702-720）；`reuse` 只析构元素、count 置 0、保留容量（:352-362）。
- **A8 `reserve(capacity)`**：参数是总容量；不大于现有容量时什么也不做（:365-394）。Rust 的 `ObIArray::reserve` 照此实现（不是 `Vec::reserve` 的增量语义）。
- **A9 `prepare_allocate(capacity)`**：count 补到 capacity，新增元素默认构造（平凡类型清零，ob_array.h:724-739,149-152）；capacity ≤ count 时不变。Rust 用 `T::default()`：C++ 的平凡类型在 Rust 里的 `Default` 必须全零（数值 0、`Option` 为 `None`），这由各类型的 `#[derive(Default)]` 保证。
- **A10 `assign(other)`**：先 `reuse`，再 `reserve(N)`，再逐个拷贝；成功才把 count 设成 N（ob_array.h:795-819），所以失败后数组为空。自赋值什么也不做（:799）。Rust 失败只可能是分配失败。
- **A11 `ObFixedArray::push_back`**：未 init 且 `capacity_ > 0` 时先按 `capacity_` init（懒分配）；`capacity_ == 0` → `OB_NOT_INIT`；满 → `OB_SIZE_OVERFLOW`（ob_fixed_array.h:65-96）。
- **A12 `ObFixedArray::init`**：`capacity < 0` 或 `> UINT32_MAX` → `OB_INVALID_ARGUMENT`；已 init → `OB_INIT_TWICE`；`capacity == 0` 什么也不做（仍未 init）；分配大小是"预设的 `capacity_`（若非 0）否则参数"（:178-205，:193）——`set_capacity(m)` 后 `init(n)` 分配的是 m。
- **A12b `ObFixedArray` 的 `reset`/`reuse`/`destroy`**：三者相同（:136-155）：释放存储，`count_`、`capacity_`（含 `set_capacity` 的预设值）都清零，回到未 init 状态，之后 `push_back` 返回 `OB_NOT_INIT`，直到再次 `init`（§6 例 4 的循环就是先 `reset` 再 `init`）。`clear()`（:157）只把 `count_` 清零、保留存储。
- **A13 `ObFixedArray::reserve`**：未 init 时先 `init(capacity)`（同样适用 A12 的预设规则），然后 `capacity > capacity_` → `OB_SIZE_OVERFLOW`（:159-176）。
- **A14 `ObFixedArray::assign`**：未 init 且对方非空时 `init(对方元素数)`，否则 `clear()`；再逐个 `push_back`（:213-231），所以预设容量小于对方时中途 `OB_SIZE_OVERFLOW`，已放入的保留。
- **A15 `ObFixedArray` 的分配器**：C++ 没有分配器时 `init` 返回 `OB_ERR_UNEXPECTED`（:187-189），`destroy` 什么也不做（:139-155）。Rust 删除分配器参数后这两个分支不存在：`init` 照常分配，`destroy` 照常复位。这只改变"忘了给分配器"这一 C++ 缺陷路径。
- **A16 `ObArrayHelper`**：`check_inner_stat()` 要求 `data_ != NULL && 0 <= count_ <= capacity_`（ob_array_helper.h:52-55）；`push_back` 满时是 `OB_ARRAY_OUT_OF_RANGE`（:59-73），不是 `OB_SIZE_OVERFLOW`。
- **A17 自由函数**：`get_difference` 先 `dest.reset()`（ob_iarray.h:110）；`append` 遇错即停（:119-128）；`is_array_equal` 先比地址（:135）。
- **A18 `Ob2DArray`**：块容量 = 不超过 `max_block_size / sizeof(T)` 的最大 2 的幂（ob_2d_array.h:105-106）；元素地址在增长时不变（C++ 依赖这一点的指针在 Rust 里本来就要改成下标）。
- **A19 `ObArrayArray`**：引用式访问的下标非法时记 ERROR 后 abort（ob_array_array.h:58-108，`right_to_die_or_duty_to_live` 最后 `std::abort()`，ob_hang_fatal_error.cpp:44-53）；`push_back(array_idx, obj)` 只检查 `array_idx >= count_`（:289），负数在 C++ 里越界读 `array_ptrs_[array_idx]` 再写入（未定义行为）——Rust 对负数也返回 `OB_ARRAY_OUT_OF_RANGE`，标 `FIXED(port)`。
- **A20 参数约定**：`const ObIArray<T> &` → `&[T]`；`ObIArray<T> &` → `&mut dyn ObIArray<T>`（RULEBOOK §2.5、A.2）。C++ 通过 `const ObIArray&` 打印数组时走的是实际类型的 `to_string`（虚函数）；Rust 的 `&[T]` 只能按切片格式（= `ObSEArray` 格式）打印，实参是 `ObFixedArray` 时日志内容可能不同。只影响日志。

### 3.2 `ObVector` / `ObSortedVector`（V）

- **V1** 迭代器是指针，Rust 是下标；`end()` 等于元素数。`size()` 是 `int32_t`（ob_vector.h:105）。
- **V2** `insert` 在满时扩容到 `(old+1)<<1`（ob_vector.ipp:355）；容量只影响内存。元素按 `MEMCPY` 搬动（:170-214），只适用于平凡类型和指针——Rust 按值移动，无差别。
- **V3** `remove(pos)` 越界 `OB_ARRAY_OUT_OF_RANGE`（:242-254）；`remove(index)` 越界 `OB_ENTRY_NOT_EXIST`（:271-281）；`remove(start, end)` 要求 `start < 元素数` 且 `end ≤ 元素数`，否则 `OB_ARRAY_OUT_OF_RANGE`（:256-269；所以 `start == end == 元素数` 也是错误）。
- **V4** `remove_if` 三个变体只删第一个匹配的，找不到 `OB_ENTRY_NOT_EXIST`（:283-336）。
- **V5** `clear()` 释放内存，`reset()` 只把元素数归零（ob_vector.ipp:117-125；ob_vector.h:157）。
- **V6** `ObSortedVector::insert`：插在 `std::lower_bound` 的位置，即等值元素之前（:413-428）。
- **V7** `replace`：`lower_bound` 处的元素 `equal` 则替换并经 `replaced_value` 交回旧值，否则插入（:430-453）。
- **V8** `insert_unique`：取 `lower_bound` 位置的元素（位置在末尾且非空时取最后一个）和它前一个元素，任一 `unique(元素, value)` 为真 → `OB_CONFLICT_VALUE`，`insert_pos` 指向那个元素（:455-499）。
- **V9** `find(value, pos, compare)`：`lower_bound` 处 `!compare(e,v) && !compare(v,e)` 才算找到，否则 `OB_ENTRY_NOT_EXIST` 且 `pos = end()`（:501-516）；`find_eq` 用 `equal`（:518-533）。
- **V10** `lower_bound`/`upper_bound` 直接调用 06 号逐行移植的 `oblib::sort::lower_bound`/`upper_bound`（libc++ 19 的对分循环，06 文档 S-SORT-7），不用 `slice::partition_point`：合法的严格弱序下结果唯一，比较器不合法时也与 C++ 相同。
- **V11** `ObSortedVector::sort` 是 `lib::ob_sort`（:588-593），即 06 号逐行移植的 `std::sort`（0.11.3）。
- **V12** C++ 的 `NULL == value → OB_ERROR`（:419,437-450,461）：元素是指针时才成立；Rust 的 `T` 是泛型（可能本身就是 `Option`），这三处检查删除。已知调用方在插入前都先判空（如 ob_routine_mgr.cpp:233），所以不可达；清单第 1 类核对调用方时一并确认。

### 3.3 `ObList`（L）

- **L1** `push_back`/`push_front`/`insert` 分配节点失败 `OB_ALLOCATE_MEMORY_FAILED`（ob_list.h:276-319,370-390）。
- **L2** `pop_back`/`pop_front`/`pop_front(value)` 空表 `OB_ENTRY_NOT_EXIST`（:321-368）。
- **L3** `erase(iter)`：空表 `OB_ENTRY_NOT_EXIST`，`iter` 为 `end()` `OB_INVALID_ARGUMENT`（:392-408）；`erase(value)` 删第一个相等元素，**找不到也返回 `OB_SUCCESS`**（:410-421）。
- **L4** 迭代器在删除其他元素、插入后仍然有效（链表语义）；指向已删元素的迭代器在 C++ 里是未定义行为，Rust 因代际 key 而 `ob_assert!` abort（`FIXED(port)`）。
- **L5** `get_first`/`get_last` 在空表上 C++ 读哨兵节点的 `data`（:423-425）；Rust abort。
- **L6** `clear`/`destroy`/`reset` 释放全部节点（:448-475）。

### 3.4 位集（B）

- **B1** 字是 `u32`，第 `i` 位在第 `i >> 5` 个字的第 `i & 31` 位（ob_bit_set.h:216-220,240-242）。
- **B2** `ObBitSet::add_member`：负数 `OB_INVALID_ARGUMENT`；字不够时逐个 `push_back(0)` 补到所需字数（:598-621），所以 `bitset_word_count()` 随最大成员增长。`del_member` 超出已有字数时什么也不做、返回成功（:649-666）。`has_member` 负数记 WARN 返回 false（:746-760）。
- **B3** `ObFixedBitSet::add_member`/`del_member` 的上限是模板参数 `N`，不是 `bit_count()`（:236,250）；`has_member` 的上限是 `bit_count()`（:279）。
- **B4** `find_next(start, pos)`：`start < -1` → `OB_INVALID_ARGUMENT`；从 `start + 1` 找起；没找到 `pos = -1`（:345-377）。`find_prev(start, pos)`：`start < 0` → `OB_INVALID_ARGUMENT`；`start == 0` 时 `pos = -1`；从 `start - 1`（截到最高位）往下找（:385-425）。`find_first` = `find_next(-1)`。`ObBitSet` 版本同（:969-1056）。
- **B5** `ObBitSet` 的 `==`：按两者中较长的字数比，缺的字当 0（:928-939）。
- **B6** `ObBitSet` 构造时把 `(N + 1) >> PER_BITSETWORD_MOD_BITS` 当作内部 `ObSEArray` 的块大小传入（:591-596）——只影响 C++ 的内存块，不影响行为。

### 3.5 `ObDList`（D）

- **D1** `add_last`/`add_first`：节点的 prev 或 next 非空（已挂表）→ 记 ERROR "link node is not alone"、返回 false（ob_dlist.h:152-189）。Rust 以 `owner_` 标签判断（等价）。
- **D2** `add_before(pos, e)`：`pos` 未挂表或 `e` 已挂表 → false（:191-208）。Rust 另要求 `pos` 挂在本表（C++ 不查，挂在别的表上会破坏两张表的 `size_`）。
- **D3** `increasing_add(e)`：从表头起跳过所有满足 `*e <= *cur` 的节点，插在第一个 `*e > *cur` 的节点之前（:210-229）；所以表按降序排，等值时新节点排在已有等值节点之后。
- **D4** `move_to_first`/`move_to_last`：先摘再加（:231-259）。
- **D5** `remove_first`/`remove_last` 在空表上返回 NULL（经 `remove(header)`，:261-285）。
- **D6** `push_range(range)` 把 range 整段插到表头之后（即表的最前面），`push_back_range` 接到表尾，二者都清空 range（:286-316）；`pop_range(num, range)` 把前 `num` 个节点挪到 range 的最前面（:317-343）。
- **D7** `move(list)`：`list` 先清空，再接管本表全部节点（:70-81）。
- **D8** `clear`：C++ 只重置表头，节点保留陈旧的 prev/next（:123-128），之后再 `add_last` 这些节点会因 D1 失败；Rust 摘下并 drop 全部节点。
- **D9** 宏：`DLIST_FOREACH*` 从表头到表尾，`DLIST_FOREACH_BACKWARD_X` 反向，`*_REMOVESAFE*` 允许删除当前节点，`DLIST_REMOVE_ALL*` 反复 `remove_first`（:24-53）。`DLIST_FOREACH(curr, list)` 的循环条件含 `OB_SUCC(ret)`。

### 3.6 `ObHashMap` / `ObHashSet`（H）

- **H1 建表**：桶数 = `cal_next_prime(bucket_num)`（ob_hashmap.h:184,197,204,208；ob_hashutils.h:674-687；素数表 ob_hashutils.cpp:21-29）。`bucket_num <= 0` 时 `cal_next_prime` 原样返回，`create` 报 `OB_INVALID_ARGUMENT`；再次 `create` 报 `OB_INIT_TWICE`；桶数组分配失败 `OB_ALLOCATE_MEMORY_FAILED`（ob_hashtable.h:823-844）。
- **H2 未建表**：`get/set/erase/erase_if/atomic/read_atomic/set_or_update/foreach/clear` 返回 `OB_NOT_INIT`（ob_hashtable.h:877-878,1133-1135,1214-1216 等）；`destroy` 总是成功、之后可以再建（:846-872）。
- **H3 哈希失败**：键的 `hash_func` 返回非 `OB_SUCCESS` 时，在取锁前原样返回（:1136-1137,1217-1218,1370-1371 等）。
- **H4 桶号**：`hash % bucket_num`（:1139,1174,1226,1373,1439,1644,1677,1710）。
- **H5 插入**：新节点插在桶链表头（:1044-1045），`size_` 原子加一（:1046）。
- **H6 `set_refactored`**：`flag == 0` 且键已存在 → `OB_HASH_EXIST`（:1236-1237）；`flag != 0` 且已存在 → 用新的键值对整个替换（`NormalPairTag` 拷贝整个 pair，:1247），**位置不变**（:1252-1257）；不存在 → 头插（:1272-1273）。
- **H7 `get_refactored`**：桶读锁内找；不存在 `OB_HASH_NOT_EXIST`（:1127-1159）。`timeout_us` 与"假节点"只在 `timeout_us > 0` 时生效（:1087-1113），全库没有这样的调用，Rust 不移植。
- **H8 `erase_refactored`**：不存在 `OB_HASH_NOT_EXIST`；`value` 非空时先拷出再摘（:1362-1410）。
- **H9 `erase_if`**：键不存在 `OB_HASH_NOT_EXIST`；`pred(pair)` 为假时 `OB_SUCCESS`、`is_erased = false`（:1418-1477）。
- **H10 `atomic_refactored`/`read_atomic`**：分别持桶写锁、读锁调用回调，回调返回值被忽略；不存在 `OB_HASH_NOT_EXIST`（:1632-1695）。回调参数是整个 `HashMapPair`（`ObHashMap::atomic_refactored` 直接转发给表的 `preproc_`，即恒等函数，ob_hashmap.h:274-279、ob_hashtable.h:1338-1342,1739）。
- **H11 `set_or_update`**：桶写锁内，存在则回调，否则插入（:1698-1729）；返回值不反映回调。
- **H12 遍历顺序**：`foreach_refactored`、`begin()/end()` 都是桶 0..n、桶内从链表头到尾（:526-544,940-956,1493-1518）——这就是 0.11.3 要保持的行序。`foreach_refactored` 对空桶不加锁直接跳过（:1495-1506），对非空桶持读锁；回调出错即停（:1512-1514）。C++ 的回调签名是非 const 引用，个别回调在里面改值（ob_dependency_info.cpp:939-949）——Rust 用 `foreach_refactored_mut`（独占）。
- **H13 回调与锁**：`atomic_refactored`、`read_atomic`、`foreach_refactored`、`set_or_update` 的回调都在桶锁内执行，回调里不得访问同一张表（ob_hashtable.h:1336-1337 的注释、M11）。Rust 同样会死锁（`RwLatch` 不可重入），不另加检测。
- **H14 锁模式与 latch**：见 §2.9 各模式注释；加锁失败（只可能是超时，桶锁不带超时）C++ 记 WARN 后不持锁继续（ob_hashutils.h:309-367 的 locker 只记 `succ_`，表代码不检查），Rust 的锁不会失败。`ReadWriteDefendMode` 在 macOS/Windows 上读也互斥（:586-597），Rust 各平台都是读写锁，不可观察。`SpinMutexDefendMode` 读不加锁（:619-628）是数据竞争，Rust 读也加锁，`FIXED(port)`（基线 0 处使用）。
- **H15 哈希函数**：见 §2.9 的实现表；`const char*` 键只比首字符（`ObCStrKey`，`BUG(port)`）。
- **H16 `ObHashSet`**：默认锁模式 `ReadWriteDefendMode`（ob_hashset.h:43）；`set_refactored` 默认 `flag = 1`（:172）；`exist_refactored` 用 `OB_HASH_EXIST` 表示存在（:157-166）；`reuse` 返回 void（:140-143）。

### 3.7 `BucketedHashMap`（BH）

- **BH1** 锁的桶号与表的桶号相同（§2.11 注释；ob_resource_map.h:219-232，ob_bucket_lock.h:337-341，ob_hashtable.h:1139），锁数和"桶 → 锁"的映射照 `ObBucketLock`（桶数/8+1，桶 i 用第 i/8 把，02 号 §2.7）；每个桶是 C++ 那一个桶的链表，头插，所以全表锁下的 `foreach_refactored` 顺序（桶 0..n、桶内从头到尾）与 C++ `map_.foreach_refactored` 相同（ob_resource_map.h:381-395）。同一线程在持有一个锁组时再去锁同组的另一个桶，C++ 会自锁，Rust 同样会（锁是同一把）。
- **BH2** guard 内用不属于本锁组的键 → `OB_ERR_UNEXPECTED` 并记 ERROR。C++ 用别的值算锁桶的地方（`ob_ddl_tablet_context.cpp:301,310,344` 用 `slice_idx % bucket_count_` 加锁，而表有 `cal_next_prime(bucket_count_)` 个桶，`:193-194`，两个不同锁桶可能同时改同一个表桶，是数据竞争）改成按键的哈希加锁，标 `FIXED(port)`。
- **BH3** `wrlock_all` 按锁号递增加锁；`try_*lock_all` 任一把锁失败返回 `OB_EAGAIN`（ob_bucket_lock.h:199-287）。
- **BH4** 不得因为"表自带锁"而去掉桶锁、也不得改成整表一把锁（RULEBOOK 0.9、F3）。

### 3.8 `ObLinkHashMap`（LH）

- **LH1 错误码**（`err_code_map`，ob_link_hashmap.h:447-460）：`insert_and_get` 已存在 `OB_ENTRY_EXIST`、分配失败 `OB_ALLOCATE_MEMORY_FAILED`；`get`/`del`/`operate` 不存在 `OB_ENTRY_NOT_EXIST`；`get` 遇到"已删除但仍被引用"的节点也是 `OB_ENTRY_NOT_EXIST`（:357-363）；`contains_key` 存在时返回 `OB_ENTRY_EXIST`（:407-415）。
- **LH2 生命周期**：值在"已从表中删除"且"最后一次 revert"之后才释放（uref 归零时 `free_value`，:492-523）。Rust：表里的 `Arc` 加调用方手里的 `Arc`，最后一个 drop 时析构，语义相同。`RefHandle` 与 `ZeroRefHandle`（:64-91，唯一用户是会话表 `ob_sql_session_mgr.h:171-172`）只改变 uref 的初值，对用户的行为相同，Rust 不区分。
- **LH3 遍历顺序**（0.11.4、C23）：DCHash 是一条按 `(calc_hash, key.compare)` 升序排列的有序链表，`calc_hash(key) = (key.hash() | 2) & !1`（ob_dchash.h:181-192；插入经 `ol_search` 找到第一个不小于自己的节点，ob_link.h:95-105,157-169），哑节点被跳过（ob_dchash.h:320-329）。Rust 的 `for_each`/`map`/`remove_if` 先取快照，再按 `((link_hash()|2) & !1, link_compare)` 升序排序后回调。会话表的 `hash()` 是 `murmurhash(&sessid_, 4, 0)`（ob_sql_session_info.h:107-110），所以 `SHOW PROCESSLIST` 的行序与 C++ 一致。
- **LH4 遍历的停止**：`map`/`for_each` 在表为空时直接成功（:420）；回调返回 false 即停并返回 `OB_EAGAIN`（:423-427）。`remove_if` 的包装总返回 true（:566-575），所以总是遍历完、返回 `OB_SUCCESS`。
- **LH5 回调与删除**（F2、C07）：C++ 遍历不持锁，回调里可以 `del` 同一张表（ob_user_resource_mgr.cpp:284-319）。Rust 快照后放掉全部内部锁再回调，所以同样允许；快照之后被删除的键仍会被回调一次（C++ 的迭代器在并发删除时也可能如此）。
- **LH6 `count()` 与 `size()`**：都是元素数（DCHash 节点计数 ob_dchash.h:319、`CountHandle` ob_link_hashmap_deps.h:114-123）。
- **LH7 `operate`**：不加引用、在回收保护内调用 `fn(key, value)`（:374-386）；Rust 在内部锁内调用（闭包不得访问同一张表）。

### 3.9 `ObLinearHashMap`（LN）

- **LN1 错误码**：未 init `OB_NOT_INIT`；`insert` 已存在 `OB_ENTRY_EXIST`（:1876-1883）、分配失败 `OB_ALLOCATE_MEMORY_FAILED`（:1889-1891）；`get`/`erase` 不存在 `OB_ENTRY_NOT_EXIST`（:2031,2097）；`operate` 回调 false → `OB_EAGAIN`（:2390-2394）；`erase_if` 回调 false → `OB_EAGAIN`、不存在 `OB_ENTRY_NOT_EXIST`（:2162,2171-2189）；`insert_or_operate` 回调 false → `OB_EAGAIN`（:1929-1933）；`insert_or_update` 存在时覆盖值（:1990-1999）。
- **LN2 原子性**（F3、C08、0.9）：`operate`、`insert_or_operate`、`insert_or_update`、`erase_if` 在 C++ 里都在桶锁内一次完成（`load_access_bkt_` 到 `unload_access_bkt_` 之间）；Rust 在分片写锁内一次完成。`RowHolderMapper::reset_hash_holder` 的"仍由我持有才删除"（ob_lock_wait_mgr.cpp:142-156）因此保持原子。块管理器的 `inc_ref`/`dec_ref`/`sweep` 另有外部 `ObBucketLock`（→ 02 号 `BucketLatch`）保护"取、改、写回"（ob_block_manager.cpp:623-698,881-897），不得因为本表自带锁而删掉。
- **LN3 `for_each`**：C++ 逐桶持锁回调（:2348-2434）；Rust 按 RULEBOOK §2.7 逐分片取快照、放锁后回调，回调只拿 `&V`。基线的 `for_each` 回调都不改值（`ob_block_manager.h:268,284,318,340,359`、`ob_shared_macro_block_manager.h:97`、`ob_i_tmp_file_manager.h:86`、`ob_tmp_file_block_manager.h:205` 都是 `const` 引用；`mds_table_mgr.h:96,133` 的 `MdsTableBase* &` 只解引用不改指针）。
- **LN4 `remove_if`**：C++ 在桶锁内调用回调、删除返回 true 的项，总是成功（:2436-2489）；Rust 在分片写锁内做。基线没有调用者。
- **LN5 哈希**：`Hash` 函子调用键的 `hash(hash_val)` 成员（:160-167），返回值被忽略（`(void)hash_func_(...)`，:1709）；Rust 同样忽略 `hash_func` 的 `ObResult`，用写出的值（初值 0）。C++ 的 `ObLinearHashMap<ObString, …>`（`ob_lock_memtable.h:235`）经这个函子调到的是 `ObString::hash(uint64_t seed)` 这个按值重载（ob_string.h:295-302），哈希值不写回，所有键落进同一个桶；Rust 的 `HashFunc for ObString` 是 murmurhash（与 `ObHashMap` 共用），只影响性能和内部次序，不可观察。
- **LN6 `count()`**：C++ 汇总 64 个分片计数（:812-845）；Rust 原子计数。
- **LN7 `BlurredIterator`**：每次取一个键值，结束 `OB_ITER_END`；并发修改时可能漏掉或重复（:112-121,847-880）。
- **LN8 锁**：C++ 每个桶是桶标志字里的自旋锁位（:349,1295-1320）；Rust 每个分片一把 `RwLatch`（latch id `HASH_MAP_LOCK`），读操作（`get`、`for_each` 的快照）持读锁，其余持写锁。

### 3.10 `FixedHash2`（F）

- **F1 次序**：C++ 的全序是 `(hash, [哑节点], recv_ts_, abs_timeout_, addr_)`（ob_lock_wait_node.cpp:69-91）；Rust 是 `(hash, recv_ts_, abs_timeout_, 插入序号)`（RULEBOOK §2.7、C24：`addr_` 每次运行都不同，本来就不确定）。
- **F2 分片**：分片号 = `hash >> shift`，`shift = clz(limit) + 1`（ob_fixed_hash2.h:44,104-106），所以分片号随 hash 单调，逐分片遍历就是全序遍历。
- **F3 `quick_next`**：遍历全部非哑节点（:57-62）。C++ 在当前节点已被删除时从它所在桶的开头重来（:91-99），可能重复访问；Rust 从被删节点的键之后继续，不重复。`check_timeout` 那种"遍历中删除上一个节点"（ob_lock_wait_mgr.cpp:428-500）照写即可。
- **F4 唤醒**（behavioral F7、RULEBOOK §2.7）：`fetch_waiter(hash)`（ob_lock_wait_mgr.cpp:376-409）从第一个 `hash() == hash` 的节点起：遇到 `run_ts > now` 的节点就停止（不唤醒任何人）；否则取走它，取走失败（已被别人取走）就看下一个同 hash 节点。被唤醒后再次失败的请求带着原来的 `recv_ts_` 重新 `insert`（C24：`recv_ts_` 只在收包时设一次），所以在同一行的等待者里仍排在前面（只是插入序号变大）。
- **F5 回收**：C++ 节点访问靠 `CriticalGuard`/`WaitQuiescent`（ob_lock_wait_mgr.cpp:301,332,382,406,428,503）防释放后使用。Rust 里节点只能在分片锁内经 `with_node` 访问，`del` 交出 `Box` 后别的线程再也访问不到，所以不需要；`ObLockWaitMgr` 里这几处按 M3 照译成 `oblib::sync::Quiescence`（无害，保持结构）。

### 3.11 其余哈希（P）

- **P1 placement 系**：起点 `do_hash(key) % N`，线性探测，绕回；遇到空槽即不存在；探完 N 个槽 → `OB_HASH_NOT_EXIST`/`OB_HASH_FULL`（ob_placement_hashutils.h:30-108）；`N == 0` 的分支照抄。
- **P2 `ObIteratableHashSet`**：迭代按插入顺序（内部 `ObDLinkNode` 链表，ob_iteratable_hashset.h:79-150）。
- **P3 `ObPointerHashMap`**：起点 `do_hash(key) & anchor_mask_`，超出容量再 `% capacity_`（ob_pointer_hashmap.h:340-345）；空槽 `(V)0`、墓碑 `(V)-1`（:105-110）在 Rust 里是枚举；子表扩容与 `need_rebuild` 照抄（:318-330,781-916）。
- **P4 `ObArrayHashMap`**：起点 `key.hash() % capacity_`，插入遇到相等键 `OB_ENTRY_EXIST`，探完 `OB_SIZE_OVERFLOW`（ob_array_hash_map.h:181-203）；`get`、`update` 遇空槽 `OB_ENTRY_NOT_EXIST`（:160-179,205-224）；`for_each` 不查是否 init（:226-239）；`serialize`（按内存布局整块写出，:93-117）无调用者，不提供。
- **P5 `ObBuildInHashMap`**：`set_refactored(value)` 只在"这个对象自己的链接字段已挂在某条链上"时返回 `OB_HASH_EXIST`（`chain_.in(value)`，ob_build_in_hashmap.h:172、ob_intrusive_list.h:117），同键的另一个对象照样插入，头插（`DLL::push`，ob_intrusive_list.h:130-137），之后 `get` 返回后插入的那个。Rust 的值有唯一归属，"同一对象再插一次"不可能发生，所以总是成功（同键照样头插）。迭代先按"非空桶链"：桶第一次变为非空时头插进链（:177），变空时摘出（:224），所以最近变为非空的桶在前；桶内从链表头起（:107-130,152-158）。唯一用户（ob_load_data_impl.cpp:1567,1586）只插入和查找，不迭代。

### 3.12 队列（Q）

- **Q1 `ObFixedQueue`**：见 §2.17 注释（ob_fixed_queue.h:88-237）。C++ 的 `head_unsafe`（:239-257）0 处调用，不提供。`get_total` 在 C++ 里是不加原子的读（:155-159），Rust 是原子读。
- **Q2 `ObLightyQueue`**：`push` 不阻塞（.cpp:91-107）；`pop(p, timeout)` 在 timeout 内等待，超时返回 `OB_ENTRY_NOT_EXIST`（.cpp:109-133）。C++ 的实现是"票号"：`pop` 先领一个序号再等，超时后往自己的槽里放一个哑元素把序号消耗掉（:115-125），所以等待中的消费者会让 `size()`（push_ − pop_）变成负数、也让"满"的判断放宽。Rust 用 `ArrayQueue` 加条件变量：FIFO、返回码、超时都相同；`size()` 不会为负，满的判断按实际元素数。`size()` 只用于日志和线程池统计（§8 Q6）。
- **Q3 `LightyQueue`**（C26）：见 §2.17 注释；`push` 在 `timeout ≤ 0` 时立即 `OB_TIMEOUT`（.cpp:217-219）；成功的 push/pop 都 `signal`（.cpp:224-226,245-247），等待者被唤醒后重试。
- **Q4 `ObPriorityQueueImpl`**：`push` 的上限判断是"加之前的值 > limit"（:49），所以最多容纳 limit + 1 个；`push_front` 不判上限（:65-81）；`pop` 按优先级 0..PRIOS 依次 `try`（:90-94），都空则在 `SimpleCond` 上等 `timeout_us`，醒来**不再取**，返回 `OB_ENTRY_NOT_EXIST`（:95-100）。
- **Q5 `ObPriorityQueue2`**（F15、C26）：
  - 优先级分三类：`< HIGH` 为高（余量 2048）、`< HIGH+NORMAL` 为普通（余量 1024）、其余为低（余量 0）（:196-202）；
  - 上限是**所有优先级共享**的一个计数：`fetch_add(size, 1)` 的旧值 `> limit + 余量` → `OB_SIZE_OVERFLOW`，计数减回（:204-205,221-223）；之后才查 `data`/`priority`（:206-208）；
  - 入队后按类 0/1/2 调 `SCondTemp<3>::signal(1, 类, fixed_wakeup_order)`（:212-218）；SQL 请求都用 `fixed_wakeup_order = true`（ob_server_runtime.cpp:623,631,637）；
  - `pop`/`pop_normal`/`pop_high` 只从前 `PRIO_CNT`/`HIGH+NORMAL`/`HIGH` 个队列取（:231-244）；取之前按类 2/1/0 `prepare`（:268-274），都空则等待，醒来**不重试**（:275-277）；
  - `limit` 由 `server_task_queue_size` 设置（ob_server_runtime.cpp:333）；
  - 唤醒（`SCondTemp`，02 号 §3.9 F6，ob_scond.h:139-162,195-209）：`fixed_wakeup_order = false` 时先在本 CPU 的那组条件变量里从类 2 往下唤醒到入队的类，剩下的计入 `n2wakeup_`，由 `do_wakeup` 在全部 CPU 上从类 2 到类 0 唤醒；`fixed_wakeup_order = true`（SQL 请求）时不看入队的类，`do_wakeup` 只唤醒一个：从类 2 到类 0、从第 0 组起第一个有等待者的条件变量。所以普通、低优先级的入队也可能唤醒只取高优先级的 `pop_high` 等待者，后者取不到就返回 `OB_ENTRY_NOT_EXIST`（上一条的"不重试"）；
  - `ReqQueue = ObPriorityQueue2<1, QQ_MAX_PRIO - 1, RQ_MAX_PRIO - QQ_MAX_PRIO>` 即 `<1, 2, 3>`（ob_server_runtime.h:50；ob_th_worker.h:42,44），SQL 用的 `RQ_HIGH = 3`、`RQ_NORMAL = 4` 都落在"低"类（余量 0）。
- **Q6 `ObSpLinkQueue`**：FIFO；`top` 不出队（:106-120）；空时 `pop`/`top` 返回 `OB_EAGAIN`（:113,170）。C++ 的生产者无锁、消费者串行；Rust 内部一把 `Latch`（`PERF(port)`）。
- **Q7 `ObLinkQueueImpl`**：`push` 领 `push_` 票号放进 `idx(票号)` 子队列；`pop` 经 `faa_bounded` 领 `pop_` 票号，`pop_ >= push_` 时 `OB_EAGAIN`，否则在对应子队列上自旋直到元素到达（:28-48,198-241）；`push_front` 把 `pop_` 减一后插到该票号子队列的头部（:212-225）；`size() = push_ − pop_`（:242-247）。整体按票号 FIFO。Rust 照移植（自旋用 `oblib::sync::pause()`）。
- **Q8 `ObSpScLinkQueue`**：单生产者单消费者 FIFO（:61-97）；`ObSimpleLinkQueue`：非线程安全 FIFO（.cpp:28-67）。

### 3.13 `to_string` 的输出（逐字节，§2.11；元素的打印走 04 号的 `ObToString`）

| 类型 | C++ 输出 | 证据 |
|---|---|---|
| `ObIArray` 基类（`ObArrayHelper` 用它） | `[cnt:N, e0, e1]`；元素类型没有 `to_string` 成员（`has_to_string<T>` 为假：基本类型、指针）时只有 `[cnt:N]` | ob_iarray.h:68-86 |
| `ObSEArray`、`ObArray`、`ObRawSEArray`、`ObBitSet`（内部 `ObSEArray<u32>`） | 元素有 `to_string` 成员时：令 n = min(元素数, `max_print_count_`（默认 32））；依次写第 0..n-2 个、各跟 `, `，写之前若 `pos + OB_LOG_KEEP_SIZE >= buf_len - 1`（512，异步日志时 0）就写 `..., ` 并跳出循环；之后总是写第 n-1 个，再写 `]`。所以超过 n 的元素直接不打、也没有省略号。元素没有 `to_string` 成员时只写 `[cnt:N]`（N 是元素数） | ob_array.h:741-771；ob_define.h:1274-1277；ob_print_utils.h:1024,1036 |
| `ObFastArray` | 同上一行的循环，但 n = 元素数（没有 `max_print_count_`），也不看 `has_to_string`（基本类型同样逐个打印） | ob_fast_array.h:233-260 |
| `ObFixedArray` | `[e0, e1]`，全部元素，不看 `has_to_string` | ob_fixed_array.h:281-295 |
| `ObArrayWrap`、`ObFixedBitSet`（字数组） | 指针为空 `null`，否则 `[e0, e1]` | ob_array_wrap.h:124-131；ob_bit_set.h:338-343；ob_print_utils.h:946-968 |
| `ObVector`、`ObSortedVector` | `[e0, e1]` | ob_vector.ipp:396-410 |
| `ObList` | `[e0e1e2]`（元素之间没有分隔符） | ob_list.h:537-547 |
| `ObDList` | `[e0, e1]` | ob_dlist.h:348-373 |
| `ObHashSet` | 每个键后跟一个逗号：`k0,k1,`；顺序同 H12 | ob_hashset.h:202-211 |
| `ObPriorityQueue2` | `total_size=%ld ` 后接每级 `queue[0][%d]=%ld ` | ob_priority_queue.h:181-189 |
| `LightyQueue` | 空（返回 0） | ob_lighty_queue.h:106 |

`has_to_string` 由 04 号 `ObToString::has_to_string()` 提供（04 文档 §2.4.4）。`ObSEArray` 系列的格式由本模块实现（读 `max_print_count_`）；04 号的切片钩子（`[T]`/`Vec`/`SmallVec`/`Box<[T]>`）的默认格式就是 `ObSEArray` 一行、`max_print_count_` 取默认值 32——经 `&[T]` 打印的数组因此总按这个格式（§3.1 A20）。

### 3.14 序列化（逐字节，0.10；编码函数是 06 号的 `oblib::serialization`）

| 类型 | 格式 | 证据 |
|---|---|---|
| `ObSEArray`、`ObArray`、`ObBitSet` | `encode_vi64(count)` 后逐个 `serialization::encode(元素)`；解码：`reset`，`decode_vi64(count)`，`prepare_allocate(count)`，逐个就地解码。count 为负时 `prepare_allocate` 什么也不做，结果是空数组、返回成功 | ob_array.h:521-563 |
| `ObFixedArray` | `OB_UNIS_ENCODE_ARRAY(data_, count_)`；解码：`reset`，`OB_UNIS_DECODE(count)`，`count > 0` 时 `prepare_allocate(count)` 后逐个解码 | ob_array_serialization.h:33-63 |
| `Ob2DArray` | 同 `ObSEArray` | ob_array_serialization.h:65-146 |
| `ObFixedBitSet` | `OB_UNIS_VERSION(1)` 的 unis 头加成员 `bitset_word_array_`（`uint32_t[MAX_BITSETWORD]`） | ob_bit_set.h:186,433 |
| `ObList` | `encode_i64(size)`（定长 8 字节）后逐个 `item.serialize`；解码先 `clear`，失败时 `pos` 不前进 | ob_list.h:477-535 |
| `ObDList` | `serialize_dlist`/`deserialize_dlist`/`get_dlist_serialize_size` 及 `ObDlistDeserialzeHelper`（`OB_UNIS_ENCODE(count)` 后逐个节点 `serialize`）在基线上 0 处调用，不移植 | ob_dlist.h:375-555 |

### 3.15 并发与内存序汇总

- 内部同步容器只用 02 号的 `Latch`/`RwLatch`/`FutexCond`/`SCondTemp` 和原子量；`DashMap`、`crossbeam` 只出现在本模块内部（RULEBOOK 0.8），哈希器固定，不用 `RandomState`。
- C++ 里用 `ATOMIC_*` 的计数（`size_`、`push_`/`pop_`、队列 size）按 RULEBOOK §2.7 一律 `SeqCst`；本模块新增的内部计数照同一规则。
- 回调在锁内执行的接口（H13、LN2、`ObArrayHashMap::for_each`、`FixedHash2::with_node`）：回调不得再访问同一个容器，否则自死锁（与 C++ 相同）。回调在锁外执行的接口：`ObLinkHashMap` 的遍历、`ObLinearHashMap::for_each`。
- 内部同步容器的 `destroy` 与并发操作：在途操作持有旧存储的 `Arc`，不会悬垂；`destroy` 之后开始的操作返回 `OB_NOT_INIT`。C++ 在这种情况下是释放后使用。

### 3.16 与 C++ 不同之处汇总

| 项 | C++ | Rust | 依据 |
|---|---|---|---|
| `operator[]`/`ObVector::at` 越界 | 记日志后越界访问 | abort | RULEBOOK §2.5、A.3；修订 A7 |
| 数组容量增长 | 翻倍并按块取整 | `try_reserve` | 不可观察（A4） |
| `ObFixedArray` 无分配器的分支 | `OB_ERR_UNEXPECTED`、`destroy` 不复位 | 分支消失 | 0.7 删除分配器参数（A15） |
| 指向已删元素的 `ObList` 迭代器、空表 `get_first` | 未定义行为 | abort | `FIXED(port)`（L4、L5） |
| `ObDList::clear` | 只重置表头 | 释放节点 | 拥有语义（D8） |
| `ObDList` 跨表操作 | 破坏两张表 | 返回 false / None | 安全（D2、D4） |
| `SpinMutexDefendMode` 的读 | 不加锁 | 加锁 | `FIXED(port)`，基线 0 处使用（H14） |
| `ReadWriteDefendMode` 在 macOS | 读互斥 | 读共享 | 不可观察 |
| `const char*` 键 | 只比首字符 | 照样（`ObCStrKey`） | `BUG(port)`（H15） |
| `ObLinkHashMap` 遍历 | 无锁链表、可能看到并发变化 | 排序快照 | RULEBOOK §2.7、0.11.4 |
| `ObLinearHashMap::for_each` | 桶锁内回调、可改值 | 快照、只读 | RULEBOOK §2.7（LN3） |
| `ObLinearHashMap` 的扩缩 | 线性哈希逐桶分裂 | 分片表 | 迭代序不进入用户输出（§8 Q5） |
| `FixedHash2` 并列项次序 | 请求地址 | 插入序号 | RULEBOOK §2.7、C24 |
| `FixedHash2::quick_next` 在删除后 | 可能重复访问 | 不重复 | F3 |
| `ObLightyQueue` 的 `size()` | 有等待者时可为负 | 不为负 | Q2、§8 Q6 |
| `ObSpLinkQueue` 的生产者 | 无锁 | 一把 `Latch` | `PERF(port)`（Q6） |
| `BucketedHashMap` 的错位锁桶 | 数据竞争 | 按键哈希加锁 | `FIXED(port)`（BH2） |
| `ObArrayArray::push_back(负下标, …)` | 越界写 | `OB_ARRAY_OUT_OF_RANGE` | `FIXED(port)`（A19） |
| 经 `&[T]` 打印数组 | 实际类型的 `to_string` | 切片格式 | 只影响日志（A20） |

## 4. unsafe

| 位置 | 需要的 unsafe | 安全论证要点 |
|---|---|---|
| `container/intrusive/**`（`ObDList`） | ① `Box::into_raw`/`Box::from_raw`、`Arc::into_raw`/`Arc::from_raw`；② 由链接字段里存的指针得到 `&ObDLinkBase` 和 `&T`；③ `ObDListPointer` 的 `unsafe trait`/`unsafe impl`（sealed，只给 `Box`/`Arc`） | **所有权**：节点挂在表上期间，表持有它的 `Box`（独占）或一个 `Arc` 强计数，所以节点和它的链接字段一直有效；表只在摘下时用 `from_raw` 取回同一个指针。**独占修改**：链接字段只在持有 `&mut ObDList` 时写，且先校验 `owner_` 等于本表的表头地址；不属于本表的节点一律拒绝（返回 false/None），不碰它的邻居。**不交出 `&mut T`**：`Box` 节点只交出 `&T`（`ObDLinkNode` 只交出 `&mut data_`），`Arc` 节点本来就拿不到 `&mut`，所以没有人能在节点挂表时移动或交换它的链接字段。**`dlink()` 由用户实现**：返回的链接若不在节点内，`owner_` 的 CAS 保证同一个链接不会同时被两个节点使用；它所在的对象若在挂表期间被析构，`ObDLinkBase::drop` 发现仍挂表就 `ob_assert!` abort，不留悬垂指针。**表的析构**：先逐个摘下并 drop 节点，再释放表头。**Send/Sync**：链接字段全是原子量，不需要 `unsafe impl Send/Sync`。 |
| `hash/**`、`queue/**` | 预计 0 处 | 全部建在安全的 `Latch`/`RwLatch`/`FutexCond`/`ArrayQueue`/`SlotMap`/`Vec` 上。实现者若为性能要用 unsafe，只能写在这两个目录（§3 白名单），每处 `// SAFETY:`，并补 loom/Miri 测试 |
| 其余 `container/**` | 0 处 | 全部是安全代码；`container/` 除 `intrusive/` 外不在白名单里 |

对外暴露的 API 全部是安全的；翻译代码实现 `ObDLinkBaseVirt`、`HashFunc`、`LinkHashKey`、`SpHashNode`、`ObIArray` 都不需要 unsafe。

## 5. 测试

测试放在 `rust/port/oblib/tests/`（集成测试）和各源文件的 `#[cfg(test)]`（单测）。loom 测试在 `cfg(loom)` 下编译，要求 02 号的锁原语和本模块用到的原子量都经 `oblib::sync` 的 loom 兼容层（§7）。

**单测**（每个返回码、边界一条；`[ ]` 是要写的）

- [ ] U1 `ObIArray` 全部方法在 `ObArray`、`ObSEArray<_, 0/1/4>`、`ObFixedArray`、`ArenaVec`、`ObArrayHelper` 上各跑一遍同一脚本：返回码与 §3 A1–A17 一致；`dyn ObIArray<T>` 调用路径与直接调用一致。
- [ ] U2 `at` 越界 abort（`#[should_panic]` 不适用于 abort：用子进程测试，检查退出信号和 ERROR 日志）。
- [ ] U3 `ObFixedArray` 的 A11–A14、A12b：预设容量与 `init(n)` 的组合、`reserve` 的懒 init、`assign` 中途 `OB_SIZE_OVERFLOW` 后的元素数、`reset` 之后 `push_back` 返回 `OB_NOT_INIT`。
- [ ] U4 `ObVector`/`ObSortedVector` 的 V3、V4、V6–V9：重复键、空表、末尾插入、`insert_unique` 两个冲突位置。
- [ ] U5 `ObList`：L1–L6，含"保存迭代器、删除别的元素、再用迭代器"。
- [ ] U6 位集：B2–B5，`find_next`/`find_prev` 在字边界（31、32、63、64）和空集上的结果。
- [ ] U7 `ObHashMap` 每种锁模式：H1–H11 的返回码；`flag` 覆盖后位置不变；`erase_if` 的 `is_erased`。
- [ ] U8 `ObHashSet`：默认 `flag = 1`、`exist_refactored` 的返回码、`set_refactored_1`。
- [ ] U9 `BucketedHashMap`：锁数 = 桶数/8+1、桶 i 归第 i/8 把锁；guard 内用同锁组别的桶的键成功、用别的锁组的键返回 `OB_ERR_UNEXPECTED`；`try_*lock_all` 在另一线程持有某把锁时 `OB_EAGAIN`。
- [ ] U10 `ObLinkHashMap`：LH1 的每个返回码；`del` 之后仍持有的 `Arc` 可用、最后一个 drop 时析构（用带计数的值类型）；遍历中 `del` 同一张表不死锁。
- [ ] U11 `ObLinearHashMap`：LN1；`erase_if` 在回调为 false 时不删除；`BlurredIterator` 走完全表恰好 `count()` 个（无并发时）。
- [ ] U12 `FixedHash2`：插入同 hash 的多个节点后 `quick_next` 的顺序 = (recv_ts, abs_timeout, 插入序)；删除当前节点后 `quick_next` 继续；`del` 两次第二次 `-ENOENT`。
- [ ] U13 队列：每种队列的返回码（`OB_NOT_INIT`、`OB_INVALID_ARGUMENT`、`OB_SIZE_OVERFLOW`、`OB_ENTRY_NOT_EXIST`、`OB_EAGAIN`、`OB_TIMEOUT`）；`LightyQueue::push` 未 init 时返回 `OB_TIMEOUT`；`ObPriorityQueue2` 在 limit=10 时高、普通、低三类各能放入多少（10+2048+1、10+1024+1、10+1）；入队失败时元素留在 `Option` 里。
- [ ] U14 `ObDList`：D1–D9 全部；`Box` 与 `Arc` 两种指针；`r#move`、`push_range`、`pop_range` 后两张表的 `get_size`；`ObDLinkNode` 的 `iter_data_mut`。
- [ ] U15 placement 系、`ObPointerHashMap`、`ObArrayHashMap`、`ObBuildInHashMap`、`ObExtendibleRingBuffer`、`ObObjStore`、`Ob2DArray`、`ObArrayArray` 各自的返回码表。
- [ ] U16 `to_string`：§3.13 每一行一个用例（含超过 32 个元素时只打前 32 个、缓冲将满时 `..., ` 之后仍打第 n-1 个、元素无 `to_string` 成员时 `[cnt:N]`、`ObFastArray` 打印基本类型）。

**loom**（小规模状态空间：2–3 个线程，每线程 2–3 个操作）

- [ ] LM1 加锁模式 `ObHashMap`：并发 `set_refactored` 同键（恰好一个成功，其余 `OB_HASH_EXIST`）；`atomic_refactored` 与 `erase_refactored` 交错；`destroy` 与 `get_refactored` 交错（后者成功或 `OB_NOT_INIT`，不悬垂）。
- [ ] LM2 `BucketedHashMap`：两个线程在不同锁组并发写、在同一锁组（含同组的不同桶）串行；`wrlock_all` 与单锁组的锁不死锁。
- [ ] LM3 `ObLinearHashMap::erase_if` 与 `insert_or_update` 同键交错：`reset_hash_holder` 的"仍由我持有才删除"不丢失他人写入（F3 的场景）。
- [ ] LM4 `ObLinkHashMap`：`for_each` 回调中 `del` 同键、并发 `insert_and_get`；值恰好析构一次。
- [ ] LM5 `FixedHash2`：`insert`、`del`（fetch_waiter 形态）与 `quick_next` 遍历（check_timeout 形态）交错；每个节点恰好被一个线程取走。
- [ ] LM6 `ObFixedQueue`、`ObLightyQueue`：多生产者多消费者，元素不丢不重；`pop` 带超时与 `push` 交错不丢唤醒；`wake_all` 唤醒全部等待者。
- [ ] LM7 `LightyQueue`：满队列上的阻塞 `push` 被 `pop` 唤醒。
- [ ] LM8 `ObLinkQueueImpl<_, 2>`：票号算法在 push/pop/push_front 交错下保持 FIFO（按票号）。
- [ ] LM9 `ObPriorityQueue2<_, 1, 1, 1>`：共享上限在并发 push 下不超过 limit+余量+1；`fixed_wakeup_order = true` 的入队恰好唤醒一个等待者，按类 2→1→0 的次序（§3.12 Q5）；被普通优先级入队唤醒的 `pop_high` 等待者返回 `OB_ENTRY_NOT_EXIST`、元素留在队列里。

**Miri**（单线程部分）

- [ ] MI1 `ObDList` 全部操作（含 `Arc` 节点被别处持有、跨表 `remove` 被拒、表析构时仍有节点、`ObDLinkNode` 的 `iter_data_mut`），在 Miri 的 Stacked Borrows 与 Tree Borrows 下都要通过。
- [ ] MI2 U1、U4、U5、U6、U7（NoPthread）、U14、U15 的单线程脚本在 Miri 下跑一遍（检查无 UB、无泄漏）。

**金标准测试**（以 C++ 为准；golden 文件在 1d 由 C++ 驱动程序产出，§8 Q4）

- 产出方法：驱动程序源码 `rust/port/oblib/tests/golden/src/containers_golden.cpp`，`#include` 基线头文件，编译参数取 `build_release/compile_commands.json` 里任一 oblib 编译单元的参数（同一 `-D`、`-I`、`-O2`），链接 `build_release/src/oblib/liboblib.a`；生成脚本 `rust/port/oblib/tests/golden/gen.sh` 入库。驱动读一个确定性的操作脚本（固定种子的伪随机序列，脚本文件同样入库），把每步的返回码、迭代顺序、`to_string` 字节、序列化字节（十六进制）写进 `rust/port/oblib/tests/golden/05/*.txt`；Rust 测试按同一脚本执行并逐行比较。
- [ ] G1 `ObHashMap`/`ObHashSet` 迭代顺序：键类型 `int64_t`（含负数）、`uint64_t`、`ObString`、`std::pair<uint64_t,uint64_t>`；桶数 1、53、1543；插入、覆盖、删除交错后 `foreach_refactored` 的顺序。
- [ ] G2 `cal_next_prime`：0、1、53、54、4294967291、4294967292 等边界。
- [ ] G3 `fnv1_32_and_fnv1a_32_compile_time_hash`、`fnv_hash_for_logger`：全部日志模块名和随机字节串（含 0x80 以上字节）的输出；`do_hash`/`do_hash(seed)` 在整数键上的输出。`murmurhash` 本身的金标准是 06 号的 T-HASH-1。
- [ ] G4 DCHash 顺序：用 C++ 的 `ObLinkHashMap<SessionInfoKey, …>`（或同构的测试键）插入随机会话 id，`for_each` 的顺序与 Rust 排序快照一致。
- [ ] G5 `ObSortedVector`：随机插入、`insert_unique`、`replace`、`lower_bound`/`upper_bound` 的位置；含不满足严格弱序的比较器（检验 V10 的探测顺序）。
- [ ] G6 位集：随机 `add_member`/`del_member` 后 `find_next`/`find_prev`/`num_members`/`to_string`/序列化字节。
- [ ] G7 数组的 `to_string` 与序列化字节：`ObSEArray<int64_t>`、`ObSEArray<ObString>`、`ObFastArray<int64_t>`、`ObFixedArray<int64_t>`、`ObList<…>`、`ObArrayWrap`、`ObFixedBitSet`、`ObBitSet`；`to_string` 另用小缓冲（如 600 字节）触发 `...` 分支。
- [ ] G8 `ObDList::increasing_add`、`push_range`、`pop_range` 的结果顺序。
- [ ] G9 placement 系、`ObPointerHashMap`、`ObArrayHashMap` 的槽位顺序（迭代序）。

## 6. 翻译者怎么用

**例 1　`const ObIArray&` / `ObIArray&` 参数与循环**（`ob_raw_expr_util.cpp:2511-2523`）

```cpp
int ObRawExprUtils::extract_column_exprs(const ObIArray<ObRawExpr*> &exprs,
                                         ObIArray<ObRawExpr*> &column_exprs, bool need_pseudo_column) {
  int ret = OB_SUCCESS;
  for (int64_t i = 0; OB_SUCC(ret) && i < exprs.count(); ++i) {
    if (OB_ISNULL(exprs.at(i))) { ret = OB_INVALID_ARGUMENT; }
    else if (OB_FAIL(extract_column_exprs(exprs.at(i), column_exprs, need_pseudo_column))) {}
  }
  return ret;
}
```
```rust
pub fn extract_column_exprs(qa: &mut QueryArena, exprs: &[Option<ExprId>],
                            column_exprs: &mut dyn ObIArray<Option<ExprId>>, need_pseudo_column: bool) -> ObResult {
    let mut ret: ObResult = Ok(());
    let mut i: i64 = 0;
    while ret.is_ok() && i < exprs.count() {
        if exprs.at(i).is_none() { ret = Err(OB_INVALID_ARGUMENT); }
        else if ob_fail!(ret = extract_column_exprs_raw_expr(qa, *exprs.at(i), column_exprs, need_pseudo_column)) {}
        i += 1;
    }
    ret
}
```
（`qa` 参数按 R1；重载名 `extract_column_exprs_raw_expr` 以 overloads.tsv 为准。）

**例 2　局部 `ObSEArray` 与 `push_back`**（`ob_select_resolver.cpp:3935-3943`，省略了中间三行声明）

```cpp
const ObIArray<ObRawExpr *> &partition_exprs = win_expr->get_partition_exprs();
ObSEArray<ObRawExpr *, 4> exprs;
for (int64_t j = 0; OB_SUCC(ret) && j < partition_exprs.count(); ++j) {
  ret = exprs.push_back(partition_exprs.at(j));
}
```
```rust
let partition_exprs: &[Option<ExprId>] = qa.win_expr(win_expr).get_partition_exprs();   // 访问函数返回切片（访问函数的写法以 07 号为准）
let mut exprs: ObSEArray<Option<ExprId>, 4> = ObSEArray::new();
let mut j: i64 = 0;
while ret.is_ok() && j < partition_exprs.count() {
    ret = exprs.push_back(*partition_exprs.at(j));
    j += 1;
}
```
（R2：调用 `&mut QueryArena` 之前先把需要的 id 拷出；这里只读，借用在下一次可变调用前结束。）

**例 3　`at(i, obj)` 与"先赋值、再查 `get_copy_assign_ret()`"**（`ob_ddl_scheduler.cpp:2397-2399`；`ob_resource_limit_calculator.cpp:270-271`）

```cpp
for (int64_t i = 0; i < remove_task_ids.size(); i++) {      // ObArray<ObDDLTaskID> remove_task_ids;（:2389）
  ObDDLTaskID remove_task_id;
  if (OB_FAIL(remove_task_ids.at(i, remove_task_id))) {
...
if (OB_SUCC(ret)) {
  res = min_res;                       // ObMinPhyResourceResult 的隐式 operator=，内部 ObSArray::operator= 把错误记进 error_
  ret = res.get_copy_assign_ret();
}
```
```rust
let mut i: i64 = 0;
while i < remove_task_ids.size() {                         // remove_task_ids: ObArray<ObDDLTaskID>
    let mut remove_task_id = ObDDLTaskID::default();
    if ob_fail!(ret = remove_task_ids.at_into(i, &mut remove_task_id)) {
...
if ret.is_ok() {
    ret = res.assign(&min_res);        // RULEBOOK §2.13：深拷贝的 operator= → assign；ObMinPhyResourceResult 手写 assign，get_copy_assign_ret 删除
}
```

**例 4　计划里的 `ObFixedArray`：`reset` + `init` + `push_back`，再整份放进外层数组**（`ob_static_engine_cg.cpp:4365-4387`）

```cpp
if (OB_FAIL(spec.exec_param_array_.init(subquery_cnt))) {   // ObFixedArray<ObFixedArray<ObExpr *, ObIAllocator>, ObIAllocator>（ob_subplan_filter_op.h:174）
} else {
  ObFixedArray<ObExpr *, ObIAllocator> cache_vec(phy_plan_->get_allocator());
  for (int64_t child_idx = 1; OB_SUCC(ret) && child_idx < spec.get_child_cnt(); ++child_idx) {
    ...
      cache_vec.reset();
      if (OB_FAIL(cache_vec.init(subquery_expr->get_param_count()))) {
      } ...
      for (int64_t j = 0; OB_SUCC(ret) && j < subquery_expr->get_param_count(); ++j) {
        ...
        OZ(cache_vec.push_back(rt_expr));
      }
      if (OB_SUCC(ret)) {
        OZ(spec.exec_param_array_.push_back(cache_vec));      // 拷贝构造：容量 = cache_vec 的元素数
      }
```
```rust
// 计划生命周期，拥有型：exec_param_array_: ObFixedArray<ObFixedArray<Option<RtExprId>>>（元素类型以 07 号为准）
if ob_fail!(ret = spec.exec_param_array_.init(subquery_cnt)) {
} else {
    let mut cache_vec: ObFixedArray<Option<RtExprId>> = ObFixedArray::new();
    let mut child_idx: i64 = 1;
    while ret.is_ok() && child_idx < spec.get_child_cnt() {
        ...
            cache_vec.reset();                                   // A12b：回到未 init，下一句必须 init
            if ob_fail!(ret = cache_vec.init(subquery_expr.get_param_count())) {
            } ...
            let mut j: i64 = 0;
            while ret.is_ok() && j < subquery_expr.get_param_count() {
                ...
                oz!(ret, cache_vec.push_back(rt_expr));
                j += 1;
            }
            if ret.is_ok() {
                oz!(ret, spec.exec_param_array_.push_back(cache_vec.clone()));   // C++ 的拷贝 → clone()
            }
        child_idx += 1;
    }
```
（查询作用域里的同类成员写 `ObFixedArray<T, ArenaVec<'q, T>>`，构造用 `ObFixedArray::new_in(arena)`。）

**例 5　共享模块里的默认锁模式 `ObHashMap`**（`ob_opt_stat_monitor_manager.cpp:194-219`）

```cpp
SpinRLockGuard guard(lock_);
if (OB_FAIL(column_usage_map_.get_refactored(col_key, flags))) {
  if (OB_LIKELY(ret == OB_HASH_NOT_EXIST)) {
    if (OB_FAIL(column_usage_map_.set_refactored(col_key, arg.flags_))) {
      if (OB_FAIL(column_usage_map_.get_refactored(col_key, flags))) {
      } else if ((~flags) & arg.flags_) {
        UpdateValueAtomicOp atomic_op(arg.flags_);
        if (OB_FAIL(column_usage_map_.atomic_refactored(col_key, atomic_op))) {}
} } } }
```
```rust
let _guard = self.lock_.rdlock();
// column_usage_map_: ObHashMap<StatKey, i64>（默认 LatchReadWriteDefendMode，全部 &self）
if ob_fail!(ret = self.column_usage_map_.get_refactored(&col_key, &mut flags)) {
    if ret.is(OB_HASH_NOT_EXIST) {
        if ob_fail!(ret = self.column_usage_map_.set_refactored(col_key.clone(), arg.flags_, 0)) {
            if ob_fail!(ret = self.column_usage_map_.get_refactored(&col_key, &mut flags)) {
            } else if (!flags) & arg.flags_ != 0 {
                let mut atomic_op = UpdateValueAtomicOp::new(arg.flags_);
                if ob_fail!(ret = self.column_usage_map_.atomic_refactored(&col_key, |e| { let _ = atomic_op.call(e); })) {}
} } } }
```
（C++ 默认的 `flag = 0` 显式写出；`operator()` 译成 `call()`，§2.13。）

**例 6　C++ 直接摸桶的写法**（`ob_dtl_interm_result_manager.cpp:169-186`，持桶读锁取第一个键，放锁后删除）

```cpp
MemProfileMap::bucket_iterator bucket_it = mem_profile_map_.bucket_begin();
while (bucket_it != mem_profile_map_.bucket_end()) {
  while (true) {
    ObDTLMemProfileKey key;
    { /* 桶读锁 */ node_it = bucket_it->node_begin(); if (node_it == bucket_it->node_end()) break; else key = node_it->first; }
    destroy_mem_profile(key);
  }
  ++bucket_it;
}
```
```rust
let mut b: i64 = 0;
while b < self.mem_profile_map_.bucket_count() {
    loop {
        let mut key: Option<ObDTLMemProfileKey> = None;
        // foreach_refactored_range 在该桶的读锁内回调；拿到第一个键就用 OB_ITER_END 停下
        let _ = self.mem_profile_map_.foreach_refactored_range(|e| { key = Some(e.first.clone()); Err(OB_ITER_END) }, b, b + 1);
        match key { None => break, Some(k) => { self.destroy_mem_profile(&k); } }
    }
    b += 1;
}
```

**例 7　回调里改值的 `foreach_refactored`**（`ob_dependency_info.cpp:936-953`）

```cpp
auto free_func = [](common::hash::HashMapPair<ObDependencyObjKey, ObDependencyObjItem*> &entry) -> int {
  if (OB_NOT_NULL(entry.second)) { entry.second->~ObDependencyObjItem(); entry.second = nullptr; }
  return OB_SUCCESS;
};
if (!ref_obj_version_table_.created()) {} else if (OB_FAIL(ref_obj_version_table_.foreach_refactored(free_func))) {}
ref_obj_version_table_.destroy();
```
```rust
// 值是 Option<Box<ObDependencyObjItem>>：手动析构就是赋 None（§2.4 OB_DELETE → drop）
if !self.ref_obj_version_table_.created() {
} else if ob_fail!(ret = self.ref_obj_version_table_.foreach_refactored_mut(|e| { e.second = None; Ok(()) })) {}
let _ = self.ref_obj_version_table_.destroy();
```

**例 8　`ObResourceMap` 一族 → `BucketedHashMap`**（`ob_resource_map.h:269-281,283-305`）

```cpp
if (OB_FAIL(hash_func_(key, hash_val))) {
} else {
  common::ObBucketHashRLockGuard guard(bucket_lock_, hash_val);
  handle.reset();
  ret = get_without_lock(key, handle);   // map_.get_refactored(key, ptr) …
}
```
```rust
// 字段 map_: ObHashMap<…, NoPthreadDefendMode> 与 bucket_lock_: ObBucketLock 合成 map_: BucketedHashMap<Key, Arc<ValueStore>>
if ob_fail!(ret = self.map_.hash_func(key, &mut hash_val)) {
} else {
    let guard = self.map_.rdlock(hash_val);
    handle.reset();
    ret = self.get_without_lock(&guard, key, handle);   // 内部：guard.get_refactored(key, &mut ptr)，OB_HASH_NOT_EXIST → OB_ENTRY_NOT_EXIST
}
```
（`get_without_lock` 增加 guard 参数：§2.7 规则 4"跨函数持锁改成传 guard"。）

**例 9　`ObLinkHashMap`：先查后插、遍历中删除**（`ob_user_resource_mgr.cpp:119-143`、`:284-319`）

```cpp
if (OB_ISNULL(user_res = OB_NEW(ObConnectResource, attr))) { ret = OB_ALLOCATE_MEMORY_FAILED; }
else { user_res->cur_connections_ = 0; ... }
if (OB_FAIL(ret)) {
} else if (OB_FAIL(user_res_map_.insert_and_get(user_key, user_res))) {
  OB_DELETE(ObConnectResource, MEMORY_LABEL, user_res); user_res = NULL;
  if (OB_ENTRY_EXIST == ret && OB_FAIL(user_res_map_.get(user_key, user_res))) {...}
}
...
bool CleanUpConnResourceFunc::operator()(ObUserKey key, ObConnectResource *conn_res) {
  ... conn_res_map_.del(key); ...
}
if (OB_FAIL(conn_res_mgr_.user_res_map_.for_each(user_func))) {}
```
```rust
let mut user_res: Option<Arc<ObConnectResource>> = Some(Arc::new(ObConnectResource::new()));  // 小的定长分配，A.4 oom-abort
if ret.is_err() {
} else if ob_fail!(ret = self.user_res_map_.insert_and_get(&user_key, user_res.as_ref().unwrap())) {
    user_res = None;                                       // OB_DELETE → drop
    if ret.is(OB_ENTRY_EXIST) && ob_fail!(ret = self.user_res_map_.get(&user_key, &mut user_res)) {...}
}
...
// 回调里对同一张表 del：ObLinkHashMap 回调时不持内部锁（LH5）
if ob_fail!(ret = mgr.user_res_map_.for_each(|key, conn_res| user_func.call(key, conn_res))) {}
```
（`ObConnectResource` 去掉 `LinkHashValue<ObUserKey>` 基类；`ObUserKey` 实现 `LinkHashKey`，方法体是 C++ 的 `hash()`/`compare()`。`ObConnectResAlloc` 一类自定义 `AllocHandle` 删除。）

**例 10　`ObLinearHashMap::erase_if`**（`ob_lock_wait_mgr.cpp:142-156`）

```cpp
auto remove_if_op = [tx_id](const ObIntWarp &k, const ObTransID &v) { return tx_id == v; };
if (OB_FAIL(map_.erase_if(ObIntWarp(hash), remove_if_op))) { if (ret != OB_ENTRY_NOT_EXIST) {} }
```
```rust
if ob_fail!(ret = self.map_.erase_if(&ObIntWarp::new(hash), |_k, v| tx_id == *v)) { if !ret.is(OB_ENTRY_NOT_EXIST) {} }
```
（闭包在分片写锁内执行，"仍由我持有才删除"保持原子，LN2。）

**例 11　`FixedHash2`：`fetch_waiter`**（`ob_lock_wait_mgr.cpp:376-409`）

```cpp
ATOMIC_INC(&sequence_[(hash >> 1) % LOCK_BUCKET_COUNT]);
if (ATOMIC_LOAD(&total_wait_node_) > 0) {
  CriticalGuard(get_qs());
  node = hash_.get_next_internal(hash);
  while(NULL != node && node->hash() <= hash) {
    if (node->hash() == hash) {
      if (node->get_run_ts() > ObTimeUtility::current_time()) { break; }
      else { int err = 0; while(-EAGAIN == (err = hash_.del(node, ret))); if (0 != err) { ret = NULL; } else { ATOMIC_DEC(&total_wait_node_); break; } }
    }
    node = (Node*)link_next(node);
  }
}
if (NULL != ret) { WaitQuiescent(get_qs()); }
return ret;
```
```rust
self.sequence_[((hash >> 1) % LOCK_BUCKET_COUNT) as usize].fetch_add(1, SeqCst);
let mut ret: Option<Box<Node>> = None;
if self.total_wait_node_.load(SeqCst) > 0 {
    let _cg = self.get_qs().enter();                           // CriticalGuard → Quiescence（M3）
    let mut it = self.hash_.lower_bound(hash);
    while let Some(k) = it {
        if k.hash() > hash { break; }
        // 同 hash：先看节点的 run_ts（在分片锁内读字段）
        // 节点已被别的线程取走时 with_node 返回 None：按"可以唤醒"处理，下一句 del 失败后接着看下一个
        // （C++ 此时读的是已摘下节点的字段，竞态下结果本来就不定）
        let future = self.hash_.with_node(&k, |n| n.get_run_ts() > ObTimeUtility::current_time()).unwrap_or(false);
        if future { break; }
        if 0 != self.hash_.del(&k, &mut ret) { ret = None; } else { self.total_wait_node_.fetch_sub(1, SeqCst); break; }
        it = self.hash_.quick_next(Some(&k));
    }
}
if ret.is_some() { self.get_qs().sync(); }                     // WaitQuiescent
ret
```

**例 12　SQL 请求队列 `ObPriorityQueue2`**（`ob_server_runtime.cpp:601-637` 的 `recv_request`、`:583-599` 的 `get_new_request`）

```cpp
if (OB_FAIL(req_queue_.push(&req, is_high_prio ? RQ_HIGH : RQ_NORMAL, true))) {}
...
ObLink* task = nullptr;
req = nullptr;
ret = req_queue_.pop(task, timeout);
if (OB_SUCC(ret)) { if (nullptr == req && nullptr != task) { req = static_cast<rpc::ObRequest*>(task); } }
```
```rust
// req_queue_: ReqQueue = ObPriorityQueue2<Box<ObRequest>, 1, { QQ_MAX_PRIO - 1 }, { RQ_MAX_PRIO - QQ_MAX_PRIO }>
// recv_request(&self, req: Box<ObRequest>)：请求的所有权移进队列（§2.7；归属以 types.tsv 为准）
let mut r: Option<Box<ObRequest>> = Some(req);
if ob_fail!(ret = self.req_queue_.push(&mut r, if is_high_prio { RQ_HIGH } else { RQ_NORMAL }, true)) {}   // 失败时请求留在 r 里
...
// get_new_request(&self, timeout: i64, req: &mut Option<Box<ObRequest>>)
let mut task: Option<Box<ObRequest>> = None;
*req = None;
ret = self.req_queue_.pop(&mut task, timeout, -1);            // 默认参数 index = -1 写全（§2.13）
if ret.is_ok() { if req.is_none() && task.is_some() { *req = task; } }   // 不再需要 static_cast
```

**例 13　`ObSpLinkQueue`：`push_front`、`pop`**（`ob_tmp_file_thread_wrapper.cpp:1034-1056`）

```cpp
ObSpLinkQueue cur_working_list;
... cur_working_list.push_front(swap_job); ...
while (!cur_working_list.is_empty()) {
  ObSpLinkQueue::Link *link = nullptr;
  cur_working_list.pop(link);
  ObTmpFileSwapJob *swap_job = static_cast<ObTmpFileSwapJob *>(link);
  if (OB_ISNULL(swap_job)) { ret = OB_ERR_UNEXPECTED; } else if (OB_FAIL(push_working_job_front_(swap_job))) {}
}
```
```rust
// 提交换出任务的线程在 wait_swap_complete() 上等，换出线程完成后 signal_swap_complete()（ob_tmp_file_thread_job.cpp:61,85）：
// 任务有两个持有者，所以元素是 Arc<ObTmpFileSwapJob>（§2.7 多重归属）
let cur_working_list: ObSpLinkQueue<Arc<ObTmpFileSwapJob>> = ObSpLinkQueue::new();
... let _ = cur_working_list.push_front(&mut swap_job); ...   // swap_job: Option<Arc<ObTmpFileSwapJob>>
while !cur_working_list.is_empty() {
    let mut swap_job: Option<Arc<ObTmpFileSwapJob>> = None;
    let _ = cur_working_list.pop(&mut swap_job);
    if swap_job.is_none() { ret = Err(OB_ERR_UNEXPECTED); } else if ob_fail!(ret = self.push_working_job_front_(&mut swap_job)) {}
}
```
（`ObTmpFileSwapJob` 去掉 `ObSpLinkQueue::Link` 基类，`ob_tmp_file_thread_job.h:30`。）

**例 14　`ObSortedVector`：`replace` 与从 `lower_bound` 起遍历**（`ob_routine_mgr.cpp:234-238`、`:390-406`）

```cpp
} else if (OB_FAIL(routine_infos_.replace(new_routine_schema, iter, compare_routine, equal_routine, replaced_routine))) {
...
ConstRoutineIter routine_begin = routine_infos_.lower_bound(routine_id_lower, compare_with_routine_id);
for (ConstRoutineIter iter = routine_begin; OB_SUCC(ret) && iter != routine_infos_.end(); ++iter) {
  if (OB_ISNULL(routine = *iter)) { ret = OB_ERR_UNEXPECTED; } else if (OB_FAIL(routine_schemas.push_back(routine))) {}
}
```
```rust
// routine_infos_: ObSortedVector<Option<Arc<ObSimpleRoutineSchema>>>
} else if ob_fail!(ret = self.routine_infos_.replace(new_routine_schema.clone(), &mut iter,
                                                    Self::compare_routine, Self::equal_routine, &mut replaced_routine)) {
...
let routine_begin = self.routine_infos_.lower_bound(&routine_id_lower, Self::compare_with_routine_id);
let mut iter = routine_begin;
while ret.is_ok() && iter != self.routine_infos_.end() {
    let routine = self.routine_infos_.at(iter).clone();
    if routine.is_none() { ret = Err(OB_ERR_UNEXPECTED); } else if ob_fail!(ret = routine_schemas.push_back(routine)) {}
    iter += 1;
}
```

**例 15　侵入式链表：工作线程表**（`worker.h:118`；`ob_server_runtime.h:180-181,298`；`ob_server_runtime.cpp:476-480,750`）

```cpp
common::ObDLinkNode<Worker*> worker_node_;            // lib::Worker 的成员，data 指回 worker 自己
using WListNode = common::ObDLinkNode<lib::Worker*>;
using WList = common::ObDList<WListNode>;
WList workers_;
...
if (!workers_.add_last(&w->worker_node_)) { ... }    // :750
...
DLIST_FOREACH_REMOVESAFE(wnode, runtime->workers_) {  // :476，已持 workers_lock_
  const auto w = static_cast<ObThWorker*>(wnode->get_data());
  runtime->workers_.remove(wnode);
  destroy_worker(w);
}
```
```rust
// 节点嵌在 worker 里：worker_node_ 改成链接字段，"data 指回自己"不再需要。
// 工作线程自己也持有这个对象，所以表里放 Arc（§2.7 多重归属）。
pub worker_node_: ObDLinkBase,                        // lib::Worker 的字段
impl ObDLinkBaseVirt for ObThWorker { fn dlink(&self) -> &ObDLinkBase { &self.worker_node_ } }
pub type WList = ObDList<Arc<ObThWorker>>;
...
if !workers.add_last(w.clone()) { ... }               // workers: workers_ 的锁 guard（数据放进锁，§2.7）
...
let mut cur = workers.cursor_front_mut();
while ret.is_ok() && cur.get().is_some() {            // DLIST_FOREACH_REMOVESAFE 的条件含 OB_SUCC(ret)
    if let Some(w) = cur.remove_current() { destroy_worker(w); }
}
```

## 7. 依赖

**白名单 crate**（RULEBOOK §1）

| crate | 用在哪里 |
|---|---|
| `smallvec` 1.x（打开 `const_generics`、`const_new`、`union`） | `ObSEArray`、`ObFastArray`、位集的内联存储；`new()` 用 `SmallVec::new_const()`，所以能是 `const fn`（可放进 `static`） |
| `slotmap` | `ObList`（修订 A2） |
| `crossbeam-queue` | `ObFixedQueue`、`ObLightyQueue`、`LightyQueue` 内部的 `ArrayQueue`（只在 `oblib::queue` 内） |
| `arc-swap` | 内部同步容器的可重建存储（§2.0） |
| `dashmap`、`crossbeam-utils`（可选） | `ObLinkHashMap` 内部（只在 `oblib::hash` 内），哈希器固定 |
| `libc` | `ENOENT`（`FixedHash2::del` 的返回值） |
| `loom`（dev-dependency） | §5 的 loom 测试；RULEBOOK §3 要求，§1 的白名单没有列，见 §8 Q7（02 号、01 号已提修订） |

**其他 1d 模块**

- **01 `mem`**：`ArenaVec<'q, T>`（本模块为它实现 `ObIArrayWrap`/`ObIArray`；它的固有方法 `push_back`/`pop_back`/`count`/`reuse` 与 trait 同名且语义一致，不得再增加名为 `remove`、`reserve`、`at`、`assign`、`reset`、`destroy`、`prepare_allocate` 而语义不同的固有方法，否则会遮蔽 trait 方法），`Arena`（`ObFixedArray::new_in`），arena 切片分配（`ObArrayWrap` 查询作用域用法）。
- **02 `sync`**：`Latch<T>`、`RwLatch<T>`（`rdlock`/`wrlock`/`try_*`）、`ObLatchIds`（`HASH_MAP_LOCK`、`DEFAULT_SPIN_RWLOCK`、`DEFAULT_MUTEX`、`DEFAULT_SPIN_LOCK`、`DEFAULT_BUCKET_LOCK`）、`FutexCond`（`get_key`/`wait`/`signal`）、`SCondTemp<3>`（`prepare`/`wait`/`signal`）、`pause()`、`CacheAligned<T>`、`Quiescence`（例 11）、`pub(crate) current_time()`（超时计算）；loom 兼容层（`cfg(loom)` 时换成 loom 的原子量和 park）。
- **03 `thread-signal`**：`get_cpu_count()`（`ObLinearHashMap` 默认分片数）、`icpu_id()`（经 02 的 `SCondTemp`）。
- **04 `error-log-fmt`**：`ObError`/`ObResult`/全部 `OB_*`、`ObResultExt::is`、`ob_assert!`、`log_*!`（`LIB_LOG`、`HASH_WRITE_LOG` 译成 `log_warn!` 等）、`ObToString`（含 `has_to_string`、切片钩子）、`databuff_print_obj`、`ObBuf`。
- **06 `string-num-sort-serial`**：1d 期间提前翻译的 `murmurhash`（`hash_func<ObString>`、`DoHash` 的默认实现、会话键的 `link_hash`，§8 Q3）；`ObSerialize`/`ObDeserialize` 与 `encode_vi64` 等（§3.14）、`ob_sort`（`ObSortedVector::sort`）、`oblib::sort::lower_bound`/`upper_bound`（V10）、prelude（§2.0 列出的 trait 和类型）、测试用 `ObRandom`（伪随机脚本）。
- **09 `parser-sys-generators`**：`ParseNode` 的 `HashFunc`/`PartialEq`（C++ `equal_to<ParseNode>` 特化 `ob_hashutils.h:730-745`、`hash_func<ParseNode>` 特化 `:818-835`）。

## 8. 开放问题

**Q1　本文引入的、改名表（RULEBOOK §4）里没有的名字**

- 名单（不含已有规则能推出的名字：C++ 类名 `ObIArrayWrap`/`SpHashNode`/`GetKey`，§2.13 的 `Virt` 规则给出的 `ObDLinkBaseVirt`、`_mut` 规则给出的 `at_mut`/`get_mut` 等，§2.5 点名的 `at_into`，§4 重载规则给出的 `push_back_i_array`，改名表已有的 `BucketedHashMap`）：trait `HashFunc`、`DefendMode`、`LockedDefendMode`、`LinkHashKey`、`ObDListPointer`、`ObFixedArrayStorage`、`DoHash`、`ObBuildInHashDesc`；类型 `ObCStrKey`、`FixedHash2Key`、`ObListIter`、`ObDListIter`、`ObDListCursorMut`、`BucketedHashMapRLockGuard`/`WLockGuard`/`WLockAllGuard`/`RLockAllGuard`；方法 `pop_back_into`、`pop_front_into`、`prepare_allocate_with`、`foreach_refactored_mut`、`erase_into`、`erase_value`、`remove_index`、`remove_range`、`remove_if_pred(_into)`、`remove_if_into`、`find_eq`、`at_array`、`count_array`、`push_back_obj`、`set_refactored_into`、`new_in`、`with_size`、`iter`/`iter_mut`/`iter_rev`/`iter_data_mut`、`cursor_front_mut`/`cursor_back_mut` 及游标方法、`with_node`、`lower_bound`/`get_first`（`FixedHash2`）、`sp_hash`、`sp_order_key`、`link_hash`、`link_compare`、`hash_func`、`do_hash`/`do_hash_seed`。
- **推荐**：整体并入改名表（它们都有一一对应的 C++ 构造，命名规则在 §2.0 的重载表里写明）。
- **备选**：逐个改回 §4 通用重载规则的机械名字（如 `pop_back_t`、`remove_int64_t`），可读性差，翻译者更容易写错。

**Q2　侵入式链表的"Pin + 宿主 Drop 时自动摘链"形态**

- 现状：RULEBOOK §2.5 允许"链表拥有节点（Box/Arc 适配），或者节点被 Pin 且宿主 Drop 时自动摘链"。后者在安全 API 下要求节点 Drop 时能拿到链表的锁和计数，链表又可能比节点活得短，需要额外的 unsafe 协议。
- **推荐**：首版只提供 `Box`/`Arc` 两种拥有式适配器；C++ 里节点由别处拥有的链表（如 SQL 工作区的 profile 登记表：`ObSqlWorkAreaProfile` 是算子的成员，`ob_sql_memory_manager.h:84,559`，登记时 `profile_list_.add_last(&profile)`，`.cpp:136`），节点改成 `Arc`，宿主和链表各持一份（§2.7 多重归属规则），摘链在宿主的注销路径里显式做。
- **备选**：1d 再实现 `ObDList<PinnedRef<'a, T>>`：链表只借用 `Pin<&'a T>`，节点 Drop 时经节点里保存的链表指针摘链（需要 `container/intrusive` 内约 15 处额外 unsafe，并要求链表放在 `'static` 或 `Arc` 里）。

**Q3　`src/oblib/lib/hash_func` 的分工**

- 现状：README 的模块表没有写 `lib/hash_func` 归谁。06 号文档把 `murmur_hash`、`wyhash`、`ob_hash_func.h` 都列为 `translate`，其中 `murmur_hash` 由 06 在 1d 期间提前翻译（06 §1、§8 Q8），`ob_hash_func.h` 注明"05 决定哈希容器怎么接"；09 号文档则按 `oblib::hash::murmurhash`（05）引用。`do_hash` 是按"有没有 `hash()` 成员"分派的模板（`ob_hash_func.h:28-62`），照字面翻译不出来，必须是 trait。
- **推荐**：`murmur_hash`、`wyhash` 照 06 的安排（translate，06 提前翻译、06 的金标准）；`ob_hash_func.h` 改为 `replace:hash`，由本模块提供 trait `DoHash`（§2.9）；`oblib::hash` 再导出 `murmurhash`，09 的引用路径不用改；`hash/fnv_hash.h` 在本模块目录里，`replace:hash`。见修订 A6。
- **备选**：`ob_hash_func.h` 保持 `translate`，由第 3 步按通用规则把 `do_hash` 译成 trait；1d 期间本模块的 placement 系等先用一个私有 trait，第 3 步再换。

**Q4　1d 产出金标准文件时编译 C++ 驱动程序**

- 现状：RULEBOOK 把 golden 文件放在 5b 产出；README 要求 1d 的 API 文档写出"以 C++ 为准的金标准测试（golden 文件怎么从 C++ 产出）"，而 1d 的实现必须在第 2 步之前测完。
- **推荐**：按 DEV-014 的例外，在 1d 编译 §5 的 C++ 驱动（只 `#include` 基线头文件、链接现成的 `build_release/src/oblib/liboblib.a`，不改 C++ 源码），产出的 golden 文件入库。
- **备选**：1d 只写 Rust 侧测试和 golden 格式，golden 文件等 5b；在那之前，迭代顺序类的测试只能用手算的期望值。

**Q5　`ObLinearHashMap` 的分片数**

- 现状：C++ 是线性哈希、每桶一把自旋锁位，没有"分片数"这个常数；RULEBOOK 0.9 只要求 `RowHolderMapper`"分片数按核数放大"。
- **推荐**：所有实例默认 `max(16, next_pow2(4 × CPU 数))`，上限 1024；不提供按实例设置的接口（`init()` 签名与 C++ 一致）。
- **备选**：默认 64，另加 `init_with_shards(n)` 只给 `RowHolderMapper` 用（多一个新名字）。

**Q6　`ObLightyQueue` 的内部实现**

- 现状：C++ 用"票号 + 哑元素"（Q2），等待中的消费者会让 `size()` 为负、让"满"的判断放宽；这些只影响线程池统计（`ObSimpleThreadPool` 的队列长度）和高负载下 `OB_SIZE_OVERFLOW` 的触发时机，都不在裁判的观察范围内。
- **推荐**：`ArrayQueue` 加 `FutexCond`（§2.17），返回码、FIFO、超时语义相同。
- **备选**：照原算法移植票号（每个槽一个 `Latch<Slot>`，自旋等待对端），完全复刻 `size()` 的负值，代价是更难做 loom 测试。

**Q7　loom 作为测试依赖**

- 现状：RULEBOOK §3 要求 `oblib::sync`、`oblib::hash` 和回收方案配 loom 测试，但 §1 的白名单没有列 loom。`amendments.tsv` 里 02-sync（§1 依赖白名单一行）和 01-mem 已经提了同一修订，本文不重复登记，只依赖它的结论。
- **推荐**：把 `loom` 作为 dev-dependency 加进白名单（只在 `cfg(loom)` 的测试里出现），与 02 号的提议相同。
- **备选**：不用 loom，改用多线程压力测试加 TSan（覆盖面弱）。

**Q8　RULEBOOK 本身需要修订的地方**（已写进 `migration/infra/amendments.tsv`，`doc` 列为 `05-containers`，`section` 列带编号 `05-A1`…`05-A7`；这里的"修订 A1–A7"与 §3.1 的语义条目 A1–A20 无关）

- **A1** §2.5 容器表与 0.6：翻译代码一律写同名新类型 `ObArray<T>`/`ObSEArray<T, N>`/`ObFixedArray<T>`，表中的 `Vec`/`SmallVec`/`Box<[T]>` 是内部存储——直接写 `Vec` 时，固有方法优先于 trait 方法：实参是整数字面量的 `arr.reserve(16)`、`arr.remove(0)` 会不声不响地绑到 `Vec::reserve`（增量、不报错）和 `Vec::remove`（返回元素、越界 panic），实参是 `i64` 变量时则编译不过（已用 1.98.1 验证）；计划里的 `ObFixedArray` 的 `init` + `push_back` + `OB_SIZE_OVERFLOW` 也无法用 `Box<[T]>` 表达（RULEBOOK 0.6 的"计划里的定长数组是 `Box<[T]>`"一句、§2.5 的 `ObSEArray`/`ObArray`/`ObFixedArray` 一行）。
- **A2** §2.5 `ObList` 的底层不能是 `VecDeque`：C++ 迭代器在插入、删除别的元素后仍有效，并被存进别的容器之后再用（`ob_ik_token.h:94-95`、`ob_ik_arbitrator.cpp:226-241`）；改为 slotmap 串成的双向链表。
- **A3** §2.5 位集的底层是 `[u32]` 而不是 `[u64]`（`ob_bit_set.h:39,188`），否则 `to_string`、序列化、`find_prev` 的字边界都变。
- **A4** §2.7 `ObSpLinkQueue`/`ObLinkQueue` 内部不能是 `SegQueue`：C++ 在用 `top()`（`ob_log_apply_service.cpp:175`、`ob_load_data_impl.cpp:1455,1457`）和 `push_front()`（`ob_tmp_file_thread_wrapper.cpp:1043,1151`，以及 `ObPriorityQueueImpl::push_front`）。
- **A5** §2.11 的数组格式：04-error-log-fmt 的修订 #1 已经指出"`[cnt:N, a, b]` 只是 `ObIArray` 的默认实现"，并提议改成"按 04 号 §3.7 的路径表逐字节复刻"。那张表不含容器自己的 `to_string`；本条补充：容器的格式以本文 §3.13 为准（`ObList` 元素之间无分隔符、`ObHashSet` 每个键后跟逗号、`ObFastArray` 不受 32 个的上限、`ObSEArray` 截断时仍打印第 n-1 个元素）。
- **A6** §4 overloads.tsv 与 types.tsv：`replace:*` 单元的方法名取自 API 文档（§2.0 重载表），不按通用重载规则生成；`ObLink`/`QLink`/`ObSpLinkQueue::Link`、`LinkHashValue<K>`/`LinkHashNode<K>`、`SpHashNode` 这些在 Rust 里删除的基础设施基类不按 §2.13 生成 `base`/`base_<蛇形类名>` 字段（§1.6）；`ObDLinkBase<T>` 照常生成字段，类型是不带泛型的 `ObDLinkBase`。
- **A7** A.3 `operator[]` 的前提检查同时 grep `"invalid index"`（`ObVector::at`，`ob_vector.h:127-133`）。

`lib/hash_func` 的分工（Q3）、loom 进白名单（Q7，02 号已提）不是本文登记的 RULEBOOK 修订。