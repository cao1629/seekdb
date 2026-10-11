# 对抗审查：RULEBOOK.md 的并发正确性与性能

> - **审查对象**：`migration/RULEBOOK.md` 草稿 v0.1（§0 架构、§1–6 规则、附录 A）。
> - **只查一类错误**：数据竞争、死锁、活锁、丢失唤醒、无界内存增长、内存序不足，以及会被性能带抓到的热路径断崖。其他类别（可靠性、行为对齐、流程一致性）不在本文范围内。
> - **方法**：每条规则都对照基线 `7d907abfa` 的 C++ 现场（`src/` 下的 file:line）逐条核对。外部事实取自 crate 源码：crossbeam-epoch 0.9.18 的 `internal.rs`、stacker 0.1.21 的 `lib.rs` 和 `mmap_stack_restore_guard.rs`、parking_lot_core 0.9 的 `spinwait.rs`、dashmap 6.1 的文档，以及 Rust `core::hint::spin_loop` 的源码。
> - **约束**：没有构建，也没有实测；凡是估算都标了「估计」。临时扫描脚本放在 `/tmp/conc-review-r7k2/`，没有入库。

## 1. 结论

1. **没有问题的部分**：`ATOMIC_*` 一律映射成 `SeqCst`，这条规则本身是对的。seekdb 用 Clang 编译（`cmake/Env.cmake:22`，GCC 不受支持，见 `:375`），LLVM 把 `__sync_*` 降成 seq_cst 原子操作，与 Rust 的 `SeqCst` 生成同一份 IR。因此备忘里"ARM 上少了 `__sync` 尾部的 `dmb`"这个前提并不成立。另外三项也核对过，不构成问题：573 种表达式在运行期走函数指针；`smart_call!` 每次调用的栈检查（C++ 同样是一次外部函数调用）；`push_back` 内部的 `try_reserve`。
2. **6 个高严重度问题**，都出在"换实现"的地方：
   - 退休预算会活锁或长时间停顿；
   - DashMap 规则只覆盖 `get`：遍历时删除会自锁，`operate`/`erase_if` 的原子读-改-写被拆开，并且 `ObBucketLock` 没有映射；
   - 数据放进锁之后，在临界区内打印 `KPC(this)` 会自锁；
   - parking_lot 包装丢掉了写者 TID；
   - 全局分配器在每次分配时更新共享计数。
3. **10 个中严重度问题**：
   - 热路径的性能断崖：`fetch_max` 变成无条件 RMW；stacker 每次扩栈都 mmap/munmap；TCRWLock 换成每实例 8–16 KB 的分片锁；bitlock 没有规定等待策略；KV cache 块改用 `Arc`；keybtree 与 KV cache 共用 epoch collector。
   - 正确性与语义：发布式读一律 `Relaxed`；Timer 的取消-等待语义没有规定；优先级队列的映射丢掉了共享上限和余量；信号掩码相对线程创建的顺序没有规定。
4. **6 个低严重度问题**，另有 15 处缺失的规则（第 3 节）。建议在对 D8 拍板之前做两件事：
   - 在 §2.7 补上五条规则：遍历、读-改-写、桶锁、锁持有者、锁内数据的打印；
   - 把退休预算改为按线程计数，并给每个结构配一个独立的 collector。

## 2. 发现

| 编号 | 严重度 | 主题 |
|---|---|---|
| F1 | 高 | 退休预算：全局计数加"反复 flush 直到回落"，会活锁或长时间停顿 |
| F2 | 高 | DashMap 规则只管 `get`：遍历中删除同一张表会自锁 |
| F3 | 高 | "克隆 Arc 后立刻放手"拆开了原子读-改-写；`ObBucketLock` 没有映射 |
| F4 | 高 | 数据放进锁之后，TO_STRING 在临界区内被调用时自锁 |
| F5 | 高 | parking_lot 包装丢失写者 TID，依赖它的条件重入要么死锁、要么失去互斥 |
| F6 | 高 | 全局分配器"按 ctx 计数"，每次 malloc/free 都写共享原子量 |
| F7 | 中 | `inc_update` 映射为 `fetch_max`，只读检查变成无条件 RMW |
| F8 | 中 | `smart_call!` 每次扩栈都 mmap+mprotect+munmap，计划缓存每次未命中都会扩栈 |
| F9 | 中 | TCRWLock 换成 ShardedRwLatch，每个实例 8–16 KB |
| F10 | 中 | bitlock 没有规定等待策略，行锁和 ObSmallSpinLock 的策略恰好相反 |
| F11 | 中 | KV cache 块改用 `Arc`：每次命中多两次共享 RMW；节点若持有 `Arc` 会拖住被淘汰的块 |
| F12 | 中 | keybtree 与 KV cache 共用 crossbeam 全局 collector |
| F13 | 中 | 发布式读一律 `Relaxed`，丢掉 happens-before |
| F14 | 中 | Timer 改成 `Arc<dyn TimerTask>` 后，取消-等待语义和弱引用都没有规定 |
| F15 | 中 | ObPriorityQueue2/LightyQueue 映射成 bounded channel，丢掉共享上限、分级余量和阻塞入队 |
| F16 | 中 | 没有规定"先屏蔽信号，再创建任何线程" |
| F17 | 低 | PAUSE 映射为 `spin_loop()`，在 aarch64 上是 `isb sy`，不是 `yield` |
| F18 | 低 | `eval_enumset` 的"每次执行的局部副本"会落成逐行克隆 |
| F19 | 低 | 已知逐行调用的 seam 默认走槽位 |
| F20 | 低 | 128 位 CAS：§2.7 与 §0.9 冲突，计数也不对 |
| F21 | 低 | L2→L3 的切换只看总体性能带；纯自旋 latch 会变成睡眠锁 |
| F22 | 低 | ExtraInfo/OpCtx 每行做一次 `dyn` 向下转型 |

---

### F1（高）退休预算：全局计数加"反复 flush 直到回落"，会活锁或长时间停顿

- **规则**：
  - §0.8：「自己写的结构（keybtree、KV cache 的 map）：crossbeam-epoch，加"退休预算"。待回收超过 1024 个时，在放掉 guard 后反复 flush 并让出 CPU，以保住 C++ RetireStation 的有界内存」；
  - §0.9：「扫描每 225 行重新 pin 一次」；
  - A.2：「加退休预算」。
- **C++ 现场**：
  - **keybtree 用 RetireStation**：
    - 退休表按 `get_itid() % (OB_MAX_CPU_NUM*32)` 分槽（`ob_retire_station.h:198,258`），每槽上限 `RETIRE_LIMIT=1024`（`ob_keybtree_deps.h:41`）。
    - 某槽超限时，`wait_quiescent` 只等**一个宽限期**，即等到"在该 clock 之前进入临界区的读者"离开（`ob_retire_station.h:65-72,217-230`），然后把本槽的退休表交给调用方释放。
    - `retire` 发生在 `release_ref()` 之后，也就是本线程不在临界区内时（`ob_keybtree.cpp:1583-1584,1719-1729`）。
  - **KV cache map 不用 RetireStation**：用的是 `ObKVCacheHazardStation`，1024 个槽，每槽阈值 512（`ob_kvcache_map.h:38-39`）。`release()` 时只做一次非阻塞的本地回收，从不等待读者（`ob_kvcache_hazard_version.cpp:240-261`）。
  - **扫描**：keybtree 迭代器只在把最多 225 个 KV 批量填进 `KVQueue` 时处于临界区，填完立即释放；注释写明缓存的结果"can keep … for a long time"（`ob_keybtree.h:143-147,158-160`）。
- **按规则的译法**：照备忘的草图，每个结构一个 `static` 待回收计数，`defer_destroy` 时加 1，延迟闭包执行时减 1。超过 1024 时：

  ```
  loop { drop(guard); epoch::pin().flush(); yield_now(); if cnt <= 1024 { break } }
  ```

- **为什么错**：
  1. **活锁或长时间停顿。** crossbeam-epoch 0.9.18 先把延迟对象放进**线程本地 bag**（`MAX_OBJECTS = 64`），只有在 bag 满、本线程调用 `flush()`、或线程退出时才推进全局队列。
     - `flush()` 只推送**本线程**的 bag；
     - `collect()` 每次最多处理 8 个 bag（`COLLECT_STEPS = 8`）；
     - 只要有任何一个 pin 着的参与者落后，`try_advance` 就不推进 epoch。

     其他线程 bag 里那 ≤63 个对象计入了预算，却不可能被正在等待的线程回收。压测时 SQL worker 会扩到数十、上百个；这些线程空闲后，要等自适应池收缩（空闲 10 秒）退出时才交出各自的 bag，常驻线程则永远不交。只要滞留的总数超过 1024，正在 retire 的写线程就一直自旋，memtable 写入随之停顿 10 秒乃至无限期。C++ 的等待只依赖读者离开一个很短的临界区，必定在一个宽限期内结束。
  2. **上界收紧约三个数量级。** 全局 1024，对比 C++ 每槽 1024 乘以 2048/4096 个槽。写线程会频繁进入等待，性能带能测出来。
  3. **给 KV cache map 加了阻塞。** 把阻塞式预算套到 KV cache map 上，引入了 C++ 根本没有的阻塞：C++ 的 put/erase/wash 从不等读者。
  4. **嵌套 pin 时自己等自己。** "放掉 guard 后"在嵌套 pin 时并不成立：crossbeam 的 guard 可以重入，外层帧只要还持有同一个 collector 的 guard，本线程就停在旧 epoch 上。
  5. **"每 225 行重新 pin 一次"有歧义。**
     - 这句话容易被读成"pin 着，把 225 行逐行交给调用方"。那样 guard 会跨过 `get_next_row`，被存进迭代器。
     - `Guard` 是 `!Send` 的，而游标跨协议调用时会换线程，此时只能靠 `unsafe impl Send` 绕过（storage_core 允许 unsafe），结果就是 UB。
- **建议改法**（替换 §0.8 的那一句）：
  - 退休预算**按线程计数**，与 C++ 每槽计数一致：
    - 每个线程一个对齐的 `AtomicUsize`；延迟闭包捕获它，执行回收时减 1；
    - 本线程自上次 flush 以来 defer 满 32 个，就 `guard.flush()` 推进全局队列；
    - 本线程待回收数超过 1024 时，先断言 `!local_handle.is_pinned()`，再 flush 一次，然后最多执行 N 次 `pin().flush()`+`yield`，等的是**本线程**的计数回落。本线程的对象此时都已进入全局队列，只要各读者离开各自很短的临界区就能被回收，与 C++ 的"一个宽限期"同构。达到 N 次仍未回落就记日志并返回，不无限自旋。
  - KV cache map **不加阻塞预算**，照 C++ 每槽 512 做非阻塞回收。
  - guard 只能存在于单次函数调用之内：
    - 禁止存进迭代器、游标、结果集；
    - 禁止对含 `Guard` 的类型写 `unsafe impl Send`；
    - keybtree 扫描"每次批量填充最多 225 个 KV 时 pin，填完立即 unpin"。
  - **或者**：keybtree 直接采用备忘里的 R3，移植 QClock+RetireStation（约 500 行）。

### F2（高）DashMap 规则只管 `get`：遍历中删除同一张表会自锁

- **规则**：
  - §2.7：「ObLinkHashMap、ObLinearHashMap、FixedHash2 | `DashMap<K, Arc<V>>`。拿到 guard 后立刻克隆 Arc 再放手，否则会重现 C++ 文档警告过的回调死锁」；
  - §2.5 同。
  - 两处都没有说遍历怎么译。
- **C++ 现场**：
  1. **锁等待超时检查**：
     - `ObLockWaitMgr::check_timeout`（`ob_lock_wait_mgr.cpp:411-506`）在 QSync 临界区内用 `hash_.quick_next(iter)` 遍历 FixedHash2（`:429`）；
     - 循环中调用 `retire_node(tail, node2del)` 删除上一个节点（`:430-433`、`:499`，进而调用 `:508-519` 的 `hash_.del`）。
     - 以下情况都会走到这里：等待超时、会话被杀、死锁牺牲者、等待超过 5 秒被强制唤醒。
  2. **用户连接资源清理**：
     - `ConnResourceCleanUpTask::runTimerTask` 对 `user_res_map_` 调 `for_each`（`ob_user_resource_mgr.cpp:318-319`）；
     - 回调 `CleanUpConnResourceFunc` 对**同一张表**执行 `del(key)`（`:284-303`）。
  3. **`ObLinkHashMap::remove_if`** 本身就是"遍历中删除"（`ob_link_hashmap.h:438-442`）。

  这在 C++ 里是合法的：ObLinkHashMap/FixedHash2 的遍历不加锁（`ob_link_hashmap.h:417-430`）。
- **按规则的译法**：`for r in map.iter() { … map.remove(&k) … }`。
- **为什么错**：
  - DashMap 的 `iter()` 持有当前分片的读锁；`remove`/`retain`/`entry` 的文档写明 "May deadlock if called when holding any sort of reference into the map"。
  - 删除的是当前 key 或上一个 key，几乎总落在同一个分片上，于是线程自锁。
  - 之后，该分片上的写者排队等待，读者也被挡住（parking_lot 的 RwLock 在有写者等待时会阻塞新读者）。
  - `check_timeout` 一旦挂死，行锁等待就永远不会因超时被唤醒：返回 1205 的场景（裁判 ACID 的 `FOR UPDATE` 超时）会直接挂住。连接资源清理挂死时，落在同一分片的用户再登录也会卡住。
- **建议改法**（§2.7 新增）：
  - 遍历：先把 `(K, Arc<V>)` 快照到 `Vec`，释放全部分片锁，再执行回调。
  - 回调里需要删除的，先记下 key，在快照遍历结束后再删。
  - `remove_if(fn)` 映射为 `DashMap::retain`，且 `fn` 不得访问同一张表。
  - FixedHash2 的"按 hash 有序遍历加删除"按上述方式改写，标 `PARITY(port)`。

### F3（高）"克隆 Arc 后立刻放手"拆开了原子读-改-写；`ObBucketLock` 没有映射

- **规则**：
  - §2.7 同上（DashMap，拿到后立刻克隆再放手）；
  - §2.7 数据放进锁的规则 3：「一个锁保护的是别的对象的字段时，用 `Latch<()>` 当令牌，被保护的字段改成原子量」；
  - §2.7 的映射表里没有 `ObBucketLock`。
- **C++ 现场**：
  1. **宏块引用计数**：`ObBlockManager::inc_ref`/`dec_ref`/`sweep_one_block`（`ob_block_manager.cpp:623-661`、`:663-698`、`:881-897`）。
     - 三者都在 `ObBucketHashWLockGuard(bucket_lock_, macro_id.hash())` 下操作 `block_map_`（`ObLinearHashMap<MacroBlockId, BlockInfo>`，`ob_block_manager.h:304,501-502`）：先 `get`，再 `ref_cnt_++`/`--`，最后 `insert_or_update`。
     - sweep 在同一把桶锁下判断：`ref_cnt==0` 才 `erase` 并 `free_block`。
  2. **行持有者映射**：`RowHolderMapper::reset_hash_holder`（`ob_lock_wait_mgr.cpp:140-154`）调用 `map_.erase_if(hash, [tx_id](k,v){ return tx_id == v; })`，是"仍由我持有才删除"的比较-删除，结果喂给 LCL 死锁检测。
     - 与它对应的 `set_hash_holder` 在**每写一行**时插入一次（`ob_memtable.cpp:2088-2092`，`ob_lock_wait_mgr.cpp:128-140`）。
     - `_lcl_op_interval` 默认 5s，也就是默认开启（`parameters.yaml:948-954`）。
  3. **MDS 表管理**：`ObMdsTableMgr::unregister_from_mds_table_mgr` 用 `erase_if`（`mds_table_mgr.cpp:94-115`）；`mds_table_order_flusher.ipp:79,154` 用 `operate`。
  4. **tablet 指针表**：`ObResourceMap`（tablet 指针表的基类，`ob_resource_map.h:189-193`）是 `ObHashMap<…, NoPthreadDefendMode>`（没有内部锁）加 `ObBucketLock` 按 hash 分段。`get` 走 `ObBucketHashRLockGuard`（`:276`）；`ObTabletPointerMap` 有 20 余处桶锁 guard（`ob_tablet_pointer_map.cpp:80-651`），每次 `get_tablet` 都会经过。
- **按规则的译法**：
  - 按 DashMap 规则写成 `let v = map.get(&k).map(|r| r.clone());`，放手后修改，再 `insert`。
  - 桶锁没有映射，译者有两种做法：要么认为"DashMap 已经线程安全"而丢掉桶锁，要么按规则 3 改成令牌加原子字段。
- **为什么错**：
  - **读-改-写失去原子性。** 拆成"读快照、写回"之后：
    - 并发的 `inc_ref` 与 sweep 之间出现先检查后操作的窗口：sweep 可能在 `inc_ref` 写回之前就 free 掉一个即将被引用的宏块（数据损坏），也可能丢失计数（泄漏）。
    - `reset_hash_holder` 拆开后，可能删掉别的事务刚设置的 holder。死锁检测因此丢边，本应报 1213 的死锁一直挂到锁等待超时（裁判 ACID 的死锁场景）。
  - **`ObResourceMap` 在安全 Rust 里写不出来。** 它的结构是"一张无锁 HashMap，多把桶锁，并发写不同的桶"，这要求同时持有多个 `&mut map`。最自然的退路是整张表一把 `RwLatch`，于是每条语句的 `get_tablet` 都对同一个锁字做两次 RMW，C++ 的分段并发就没了（性能带：sysbench 点查）。
  - **分片争用和扩容停顿。** DashMap 的分片数固定，每个分片内的 hashbrown 扩容要在写锁下整片 rehash；C++ 的线性哈希是逐桶分裂。`RowHolderMapper` 每写一行插入一次，大事务和批量写入时会出现分片争用和毫秒级的扩容停顿（估计）。
- **建议改法**（§2.7 补两行）：
  - ObLinearHashMap 的原子接口必须在分片写锁内完成，闭包内不得访问同一张表：

    | C++ | Rust |
    |---|---|
    | `operate` | `get_mut` |
    | `insert_or_operate` / `insert_or_update` | `entry().and_modify().or_insert()` |
    | `erase_if` | `DashMap::remove_if` |

    只有 ObLinkHashMap 的 `get`/`revert` 才用"克隆 Arc 后放手"。外层的 `ObBucketLock` 原样保留为令牌，不得因为 DashMap 自带锁而删除。高频写的表（`RowHolderMapper`）用 `with_shard_amount` 按核数放大分片，或者逐行移植线性哈希。
  - `ObBucketLock` 映射为 `oblib::sync::BucketLatch`，即 `RwLatch<()>` 数组，与 C++ 保持一致：桶数相同、按 `hash % n` 取桶、多桶时排序后按序加锁（`ObMultiBucketLockGuard`）、带超时的 Try 版本。被它分段保护的容器如果是 `NoPthreadDefendMode` 的 ObHashMap，改成同样桶数、每桶自带 `RwLatch` 的内部同步版本，保留 C++ 的分段并发，禁止退化成整表一把锁。

### F4（高）数据放进锁之后，TO_STRING 在临界区内被调用时自锁

- **规则**：
  - §2.7 数据放进锁的规则 1：「在本类加锁区域里写过、在锁外从不写的字段，放进锁里的 T」；
  - §2.11：「TO_STRING_KV … impl ObToString … 所有类型都逐字节复刻」。
- **C++ 现场**：
  - `ObTxCtx::commit`（`ob_tx_ctx.cpp:581-642`）在持有 `CtxLockGuard guard(lock_)`（`:587`）期间，多次调用 `TRANS_LOG(WARN, …, KPC(this))`（`:593,596,613,616,619,642`）。
  - `TO_STRING_KV` 直接读成员，不加锁。
  - 规模：全仓 `KPC(this)` 共 1,217 处；66 个文件同时含有 `KPC(this)` 和锁 guard；143 个头文件既有锁成员又有 `TO_STRING_KV`（启发式计数）。
- **按规则的译法**：字段进了 `Latch<Inner>` 之后，`impl ObToString for X` 只能先 `self.lock.lock()` 再读。在 `:593` 这类位置，本线程已经持有这把锁，而 parking_lot 的互斥锁不可重入，结果就是永久自锁。
- **为什么错**：C++ 的 to_string 是不加锁的脏读，无论调用方是否持锁都不会阻塞（对日志来说足够）。Rust 版则在最常见的路径上变成死锁，也就是"在临界区内报 WARN"。错误路径恰好是裁判的错误用例会走到的。
- **建议改法**（§2.11 和 §2.7 新增）：
  - 打印锁内数据时：`ObToString for X` 只打印锁外字段；锁内部分用 `try_lock()` 读，拿不到时（包括本线程自己持有）打印占位 `<locked>`。任何 to_string 都不得阻塞加锁。
  - 临界区内的日志改为打印 guard（`kpc!(g)`，配 `impl ObToString for LatchGuard<'_, Inner>`）。
  - 审查清单加一条：`log_*!` 的实参如果可能取锁，必须是非阻塞的。

### F5（高）parking_lot 包装丢失写者 TID，依赖它的条件重入要么死锁、要么失去互斥

- **规则**：
  - §0.8：「底层先用 parking_lot… 保留：LatchId；带截止时间的加锁…；写锁降级；READ_PREFER」。保留清单里没有 ObLatch 锁字中的写者 TID。
  - §2.7：「ObSpinLock、lib::ObMutex → `Latch<T>`」「SpinRWLock、ObLatch → `RwLatch<T>`」。
- **C++ 现场**（锁库之外有 7 处调用，另有 1 处定义）：
  - **LS 元数据的条件重入**：`ObLSMeta::ObReentrantWLockGuard`/`ObReentrantRLockGuard`（`ob_ls_meta.cpp:445-500`）在 `lock_.is_wrlocked_by()` 为真时跳过加锁，在本文件里被用了 37 次。
  - **事务日志提交**：`ObTxLogOperator`（`ob_tx_log_operator.h:208,420`）写成 `if (!tx_ctx_->lock_.is_locked_by_self()) get_ctx_guard(…CTX)`。
  - **提交回调的重入**：`ObTxDesc::cancel_commit_cb`（`ob_trans_define_v4.cpp:927-946`，注释 "cancel may called from `commit_cb_` it self"）和 `print_trace`（`:568-580`）都用 `self_locked()` 决定是否加锁。
  - **打印**：`ObTxCtx::to_string`（`ob_tx_ctx.cpp:1164-1172`）只在 `lock_.is_locked_by_self()` 为真时才打印受保护的部分。
  - **实现**：`CtxLock::is_locked_by_self`（`ob_trans_ctx_lock.h:85`）、`ObSpinLock::self_locked`（`ob_spin_lock.h:88-90`）、`ObLatch::is_wrlocked_by`。
- **按规则的译法**：parking_lot 的 `Mutex`/`RwLock` 不记录持有者，译者只能二选一：
  - 无条件加锁：同一线程重入时自锁，提交回调路径和 LS meta 的嵌套访问都会挂死；
  - 无条件不加锁：从未持锁的路径失去互斥，而且拿不到 `T`，编译都过不了，只能在第 4 步临时补救。
- **为什么错**：这些都是事务提交、日志提交和 LS 元数据的核心路径。无论选哪一边，都改变了 C++ 的互斥语义。
- **建议改法**：
  - §0.8 的保留清单加上"写者身份"：`Latch`/`RwLatch` 包装在拿到写锁后写入 `owner: AtomicU32 = itid+1`，释放前清零。这与 ObLatch 锁字低 30 位的语义一致，代价是一次 Relaxed store。包装提供 `is_wrlocked_by_self()`。
  - 上述 7 处译成条件加锁：`let g = (!l.is_wrlocked_by_self()).then(|| l.write());`。访问数据时，用调用方传进来的 guard 或者这个 `Option` 里的 guard。
  - `ObTxCtx::to_string` 的受保护部分按 F4 处理，只在拿到 guard 的地方打印。
  - L3 备选也必须保留这项能力。

### F6（高）全局分配器"按 ctx 计数"，每次 malloc/free 都写共享原子量

- **规则**：§0.7：「`#[global_allocator]` 是自带 jemalloc 上的一层薄封装；加一个线程局部的 MemAttr（label、ctx）… 只按 ctx 计数、供诊断」。
- **C++ 现场**：默认的 jemalloc 后端完全不计数：
  - `ob_malloc` 直接调用 `jemalloc_malloc(nbyte)`，`ob_free` 直接调用 `jemalloc_free`（`ob_malloc.h:118-124`、`:145`）；
  - `ObMemAttr` 被完全忽略（memory 备忘 §2.1）。
- **按规则的译法**：规则没有规定计数放在哪里，也没有规定 free 时怎么找回 ctx。最直接的写法是：
  - `static CTX_HOLD: [AtomicI64; 8]`，alloc 时 `fetch_add`；
  - free 时靠每块 16 字节的头部，或"每个 ctx 一个 jemalloc arena"，取回 ctx 后 `fetch_sub`（这几种办法备忘 §3(d) 都列了）。
- **为什么错**：
  - **共享缓存行争用。** 8 个 i64 计数正好挤在一条缓存行里。进程内所有线程（包括 sql-nio）的每一次 Rust 分配和释放，都要对这条线做一次 `lock xadd`。jemalloc 的 tcache 快路径原本没有任何共享写。估计：无争用的快路径约 10–20 ns，跨核争用的原子 RMW 约 50–200 ns，分配器热路径会被放大数倍。
  - **16 字节头部的代价。** 小对象会被推到上一档 size class（例如 48→64 字节），还会破坏对齐超过 16 字节的 `Layout`，内存占用上升（性能带包含内存占用）。
  - **每个 ctx 一个 arena 的代价。** 这与 tcache 相互影响，备忘自己也写了"需要性能验证"；要做到归属正确，只能绕过 tcache。
- **建议改法**（替换 §0.7 的那一句）：
  - 计数只用每线程计数器（`thread_local!` 里的 `Cell<[i64; 8]>`），读视图时再汇总。
  - 释放时按释放线程当前的 ctx 扣减（只做诊断；总量正确，ctx 之间的归属允许有偏差）。
  - GlobalAlloc 路径上禁止任何共享原子写，禁止加块头，也不设每 ctx 一个 arena。
  - 另一种做法是干脆不计数：视图内容在查询时由 jemalloc 的 `mallctl("stats.*")` 填充。

### F7（中）`inc_update` 映射为 `fetch_max`，只读检查变成无条件 RMW

- **规则**：§2.7：「inc_update/dec_update（CAS 求最大、最小） | fetch_max/fetch_min，注意返回值语义不同」。
- **C++ 现场**：
  - `inc_update`（`ob_atomic.h:97-121`）先 `ATOMIC_LOAD`，只有 `x` 大于当前值时才做 `ATOMIC_VCAS`；`SCN::inc_update` 同理（`scn.cpp:168-192`）。全仓 59 处调用。
  - **每条语句一次**：`ObTransService::update_max_read_ts_` 在取快照之后调用（`ob_trans_service_v4.cpp:672,770,1180-1183`），更新的是全局 `max_read_ts_`（`ob_tx_version_mgr.h:40-57`）。
  - **每写一行两次**：`ObMemtable::set_max_data_schema_version`/`set_max_column_cnt`（`ob_memtable.cpp:953-954,2105-2106,2259-2260,2385-2386`，实现在 `:1928-1945`）。
- **按规则的译法**：`max_read_ts.fetch_max(ts, SeqCst)`。
- **为什么错**：
  - LLVM 对 `atomicrmw max` 的降法：在 x86-64 上展开成每次都执行 `lock cmpxchg` 的循环，在 aarch64 上是 `ldsmaxal`（或 LL/SC 循环）。值不变也要写缓存行。
  - C++ 在"值没有增大"这一常见情形下只读，缓存行保持共享态。
  - Rust 版则每条语句都要独占全局 `max_read_ts_` 所在的缓存行，每次行写入都要独占同一个 memtable 的两条计数缓存行，多核下缓存行来回迁移（sysbench 只读和写入、TPC-C）。
- **建议改法**：§2.7 改为——"`inc_update`/`dec_update` 译为 `oblib::atomic::inc_update(&a, x)`：先 `load`，只有 `x` 更大（更小）时才进入 `compare_exchange` 循环；返回值与 C++ 相同（更新后的最值）。禁止直接使用 `fetch_max`/`fetch_min`。"

### F8（中）`smart_call!` 每次扩栈都 mmap+mprotect+munmap，计划缓存每次未命中都会扩栈

- **规则**：§0.8：「SMART_CALL（约 1,130 处）改成 smart_call!，基于 stacker。剩余栈不足 64 KB（_LARGE 为 192 KB）时扩栈，每次 2 MB」，配合默认线程栈 256 KB。
- **C++ 现场**：
  - **扩栈不进系统调用**：`__SMART_CALL_IMPL`（`ob_smart_call.h:112-128`）扩栈时走 `g_stack_allocer.smart_call_alloc`，最终是 `ob_malloc(2MB)`，且 `guard_page=false`，不调用 mprotect（`protected_stack_allocator.cpp:69-72,95-135`）。jemalloc 在稳态下复用大块 extent，不进系统调用。
  - **每次计划缓存未命中都扩栈**：
    - `SMART_CALL_LARGE(handle_physical_plan(…))`（`ob_sql.cpp:1237,1868,1999`）在每次计划缓存未命中时执行，不走计划缓存的语句（DDL、begin/commit 等）也会执行。
    - 在 256 KB 的栈上，"剩余不足 192 KB"几乎总是成立；`ob_smart_call.h:32-36` 的注释说的正是这个意图。
- **按规则的译法**：`stacker::maybe_grow(64K 或 192K, 2M, f)`。
- **为什么错**：
  - stacker 0.1.21 每次 `grow` 都新建一个 `StackRestoreGuard`：先 `mmap(PROT_NONE)` 再 `mprotect(RW)`，返回时 `munmap`，没有任何缓存。
  - 在多线程进程里，munmap 会触发 TLB shootdown 的核间中断（IPI），打断所有正在运行本进程线程的核。
  - 结果是每次硬解析、每条 DDL 都多出 3 个系统调用和一次全核 IPI。受影响的有冷启动（引导期大量内部 SQL 都是硬解析，性能带包含冷启动时间），以及裁判里 DDL 密集的用例。
  - Rust 的栈帧更大，普通 `SMART_CALL` 跨过 64 KB 阈值的次数也会增加。
- **建议改法**：§0.8 改为——
  - smart_call! 使用每线程缓存的扩展栈段：段从全局分配器申请，线程内用栈式空闲表复用，线程退出时释放；
  - 切栈用 `psm::on_stack`，并把 psm 加进依赖白名单，把 `oblib::thread::stack` 加进 §3 的 unsafe 白名单；
  - 10 MB 累计上限照旧；
  - 禁止每次扩栈都 mmap/munmap。

### F9（中）TCRWLock 换成 ShardedRwLatch，每个实例 8–16 KB

- **规则**：
  - §2.7：「TCRWLock、DRWLock | `ShardedRwLatch<T>`：读者只锁自己 itid 对应的分片，写者锁全部分片」；
  - §2.7：「CACHE_ALIGNED | `crossbeam_utils::CachePadded`」。
- **C++ 现场**：
  - **TCRWLock 每个实例很小**：每个实例只有一个 ObLatch、几个 int，以及一个指向**全局** `TCRef` 的引用（`ob_tc_rwlock.h:143-144`）。读者把锁的地址写进本线程在全局表里的槽（`ob_tc_ref.h:27-30,71-85`），每个实例约 32–40 字节。
  - **DRWLock 才是每实例分片**：每个实例有 `OB_MAX_CPU_NUM` 个对齐的 latch（`ob_drw_lock.h:115-119,130`），全仓只有 3 个实例。
  - **TCRWLock 共 23 处成员声明**，其中按对象数放大的有：
    - `ObILibCacheNode::rwlock_`（`ob_i_lib_cache_node.h:214`），每个计划缓存节点一把，命中路径要加读锁；
    - `ObITmpFile::meta_lock_`（`ob_i_tmp_file.h:345`），每个临时文件一把；
    - `ObVectorIndexAdaptor` 两把（`ob_vector_index_adaptor.h:473-474`）。
- **按规则的译法**：`[CachePadded<RwLock<()>>; OB_MAX_CPU_NUM]` 加 `UnsafeCell<T>`。
- **为什么错**：
  - **内存放大 256–512 倍。** `CachePadded` 在 x86-64 和 aarch64 上都是 128 字节；`OB_MAX_CPU_NUM` 在 x86 上是 64，在 aarch64 上是 128（`ob_define.h:1483-1487`）。于是每个实例 8 KB 或 16 KB，是 C++ 的 256–512 倍。1 万条缓存语句就要 80–160 MB 的锁分片，这既影响内存性能带，也改变计划缓存的内存统计口径。
  - **写锁变重。** 写锁要依次拿 64 或 128 把锁。
  - **附带的语义差异。** TCRWLock 写锁在等待读者超时后返回的是 `OB_EAGAIN`（`ob_tc_rwlock.h:194-201`），而不是 `OB_TIMEOUT`。
- **建议改法**：§2.7 拆成两行——
  - DRWLock → ShardedRwLatch：每个实例分片，与 C++ 同构。
  - TCRWLock → `oblib::sync::BigReaderLatch`：移植 TCRef，即一张全局的每线程槽表（槽里存锁的地址），加上每个实例一个写锁字和一个 `read_ref`，实例体积与 C++ 同一量级；写锁等读者超时返回 `OB_EAGAIN`。
  - `CACHE_ALIGNED` 译为按架构对齐的 `oblib::sync::CacheAligned<T>`（x86 64 B、ARM 128 B，与 `ob_macro_utils.h:617-623` 一致）。

### F10（中）bitlock 没有规定等待策略，行锁和 ObSmallSpinLock 的策略恰好相反

- **规则**：§2.7：「ObByteLock、ObSmallSpinLock、ObPtrSpinLock、ObRowLatch | `oblib::sync::bitlock`：锁位和数据仍在同一个字里」。
- **C++ 现场**：
  - **ObRowLatch 是纯自旋**：`lock()` 是纯 TAS 自旋，不 PAUSE、不让出、不睡眠（`ob_row_latch.h:38-40`）。每个 `ObMvccRow` 一把；`mvcc_write_` 和提交时回填版本都在它的保护下进行（`ob_mvcc_row.cpp:840`、`ob_mvcc_trans_ctx.cpp:1748-1754`）。
  - **ObSmallSpinLock 会睡**：`ObSmallSpinLock<…, MaxSpin=500, USleep=100>` 先 PAUSE 500 次，之后每 500 次 `usleep(100)`（`ob_small_spin_lock.h:74-165`）。它用于 `ObByteLock`，包括回调链表的三把锁和 palf 滑动窗口的槽锁。
- **按规则的译法**：一个统一的 bitlock，等待策略由实现者决定，常见做法是用 parking_lot_core 的 park，或者照抄 ObSmallSpinLock。
- **为什么错**：
  - 如果行锁采用 ObSmallSpinLock 的策略或者 park，热点行（TPC-C 的 warehouse、district 行）每次冲突超过 500 次就要睡 ≥100 µs（加上 Linux 的 timer slack 实际更长），吞吐会断崖下跌。
  - 反过来，如果 ObByteLock 改成纯自旋，C++ 的睡眠退避就变成了空转。
  - 等待策略决定用户可见的吞吐，不是实现细节。
- **建议改法**：§2.7 改为——"bitlock 带策略参数，逐类型照抄 C++：ObRowLatch 为纯 TAS 自旋；ObSmallSpinLock、ObByteLock、ObPtrSpinLock 为 PAUSE MaxSpin 次后每 MaxSpin 次 `usleep(USleep)`，模板实参照抄。不得使用 park。"

### F11（中）KV cache 块改用 `Arc`：每次命中多两次共享 RMW；节点若持有 `Arc` 会拖住被淘汰的块

- **规则**：§0.8：「KV cache 的内存块：Arc」。
- **C++ 现场**：
  - **命中路径**（`ob_kvcache_map.cpp:261-310`）：
    - 用 `HazptrHolder::protect` 保护块：对本线程的 hazard 指针做一次 relaxed store，加一次 `MEM_BARRIER`，再 relaxed 重读一次（`ob_kvcache_hazard_pointer.cpp:24-37`）；
    - 释放也只是一次本地的 relaxed store；
    - 唯一的共享 RMW 是 `ATOMIC_AAF(&mb_handle_->get_cnt_, 1)`（`:302`）。
  - **wash 时**：
    - 块靠 `seq_num_` 失效，map 里的旧节点之后由 `clean_garbage_node` 慢慢清理；
    - 块本身在 RetireStation 的一个宽限期之后就回收（`ob_kvcache_store.h:143-144,212-224`）。
- **按规则的译法**：
  - 块是 `Arc<MemBlock>`：命中时 `Arc::clone`，句柄 drop 时减计数；
  - 规则没有说 map 节点里存什么，最自然的写法是节点也存 `Arc<MemBlock>`。
- **为什么错**：
  - **共享 RMW 从 1 次变成 3 次。** 热块（索引根块、热点微块）被所有线程反复命中，每次命中多一次 `fetch_add`，用完之后再多一次 `fetch_sub`。后者往往要把已经被别的核拿走的缓存行再抢回来。点查的每条语句要命中好几个块，性能带能测出来。
  - **淘汰的块释放不了，内存无界增长。** 如果节点持有 `Arc`，被 wash 的块要等它的所有节点都被清掉才能释放，KV cache 的实际占用会长时间超过 `kvcache_memory_limit`，超出多少取决于清理速度。
  - **将来改起来代价大。** 备忘写的是"热点再说"，但规则把 `Arc` 直接暴露成了句柄类型，到时候要改所有调用点。
- **建议改法**：§0.8 改为——
  - KV cache 块句柄是不透明类型 `KvBlockHandle`，只暴露 `&[u8]` 访问和 Drop；内部首版用 `Arc`，以后可以在不改调用点的前提下换成移植的 hazard pointer；
  - map 节点只存块 id 和 `seq_num`（与 C++ 相同），命中时先校验 seq 再取句柄，节点不得持有 `Arc`。

### F12（中）keybtree 与 KV cache 共用 crossbeam 全局 collector

- **规则**：§0.8 和 §0.9 只写了「crossbeam-epoch」，没有规定用哪个 collector。
- **C++ 现场**：
  - **回收域彼此独立**：
    - keybtree 有自己的静态 QClock、RetireStation 和 QSync（`ob_keybtree.cpp:1743-1760`）；
    - KV map 有自己的 `global_hazard_station_`（`ob_kvcache_map.cpp:63`）；
    - KV store 也有自己的 QClock 和 RetireStation（`ob_kvcache_store.h:212-224`）。
  - **KV map 的长临界区**：
    - wash 在一个 hazard guard 内一次扫描最多 20 万个或 10 万个桶（`ob_kvcache_map.cpp:511-570,576-636`，上限见 `ob_kv_storecache.h:205,207`，调用点 `ob_kv_storecache.cpp:529,544`）；
    - `erase_all` 在一个 guard 内扫完全部桶（`ob_kvcache_map.cpp:415-457`）。
- **按规则的译法**：两处都直接调用 `crossbeam_epoch::pin()`，用的是默认的全局 collector。
- **为什么错**：
  - **KV cache 卡住 keybtree 的回收。** 全局 epoch 只有在所有 pin 着的参与者都到达当前 epoch 时才能前进。wash 线程 pin 住扫描 20 万个桶的这段时间里，keybtree 退休的节点一个都回收不了；叠加 F1 的预算，memtable 写者会周期性地自旋，写入的 p99 延迟随之抖动。
  - **回收工作搬到了读路径上。** crossbeam 的读者每 pin 128 次就执行一次 `collect`（`PINNINGS_BETWEEN_COLLECT = 128`，一次最多 8 个 bag × 64 个析构），点查这样的读路径要承担回收工作；C++ 的读者从不做回收。
- **建议改法**（§0.8 新增）：
  - 每个自己写的无锁结构配一个独立的 `crossbeam_epoch::Collector`（keybtree 一个，KV cache map 一个），线程本地的 `LocalHandle` 惰性注册；
  - KV cache 的 wash 和 `erase_all` 每扫完一批（例如 1024 个桶）就放掉 guard，再重新 pin。

### F13（中）发布式读一律 `Relaxed`，丢掉 happens-before

- **规则**：
  - §2.7 数据放进锁的规则 2：「锁外也读的字段，改成 Atomic*：在锁内写，锁外用 Relaxed 读」；
  - 原子字段的规则：「同一字段上的普通读写…其余用 Relaxed」；
  - §2.8：「x->prev_ 改成 x.prev()」，没有规定内存序。
- **C++ 现场**：
  1. **MVCC 版本链**：
     - 写者先填好节点（`tx_id_`、版本号、`buf_` 里的行数据），再用 `ATOMIC_STORE(&(next->prev_), &node)` 或 `ATOMIC_STORE(&(list_head_), &node)` 发布（`ob_mvcc_row.cpp:604-612,968-972`）；
     - 读者的 `iter = iter->prev_` 是普通读（`ob_mvcc_iterator.cpp:144,165,170,191,262,329`）。
  2. **回调管理器的延迟初始化**：
     - `ObTransCallbackMgr::extend_callback_lists_` 先 placement-new 一组 `ObTxCallbackList`，再执行 `ATOMIC_STORE(&callback_lists_, tmp)`（`ob_mvcc_trans_ctx.cpp:317-338`）；其余地方普通读 `callback_lists_`（`:408,499,564,2070`）；
     - `alloc_mvcc_row_callback` 是双重检查：在锁内普通写 `cb_allocators_ = …`（`:286`），在锁外普通读之后直接解引用（`:271-299`）。
- **按规则的译法**：`prev.load(Relaxed)`、`callback_lists.load(Relaxed)`，然后访问被指向的对象。
- **为什么错**：
  - Relaxed 读与发布方的 SeqCst 写（或普通写）之间不建立 happens-before。读到了新指针、却看到未初始化的节点内容，这在 Rust 内存模型下就是数据竞争，即 UB。硬件上靠地址依赖碰巧能成立，但规则要求 storage_core 跑 Miri 和 loom，这类代码会被判为数据竞争，届时要返工。
  - storage 备忘的草图里，`prev()` 用的是 SeqCst（storage-txn §3 选项 A），与 §2.7 的通用规则互相冲突。
  - 改用 Acquire 的代价可以忽略：在 x86 上就是普通的 mov，在 ARM 上是 `ldar`/`ldapr`。
- **建议改法**（§2.7 新增）：
  - 读出来之后要用来访问别的数据的值（指针、下标、"已初始化"标志、版本号），一律用 Acquire 读；发布端用 Release（或者保持 SeqCst）。
  - 只有纯计数、统计和停止标志才用 Relaxed。
  - 双重检查的初始化一律改用 `OnceLock` 或 `ArcSwapOption`。
  - MVCC 的 `prev()`/`next()` 明确规定为 Acquire。

### F14（中）Timer 改成 `Arc<dyn TimerTask>` 后，取消-等待语义和弱引用都没有规定

- **规则**：§2.7：「`ObTimerTask&` 裸引用登记 | `Arc<dyn TimerTask>`，由定时服务持有」。concurrency 备忘 §3.1 还写着"裸引用加'取消再等待'的约定随之消失"。
- **C++ 现场**：
  - **ObLockTable 的销毁顺序**：`ObLockTable::destroy()`（`ob_lock_table.cpp:230-241`）先 `cancel_task`，再 `wait_task`，然后清空 `parent_`、`lock_mt_mgr_`、`lock_memtable_handle_`。任务 `CheckObjLockTask` 持有 `ObLockTable &lock_table_`（`ob_lock_table.h:174-181`），而 ObLockTable 嵌在每个 ObLS 里。
  - **`wait_task` 的语义**：`ObTimerService::wait_task` 一直等到任务既不在队列里、也不在运行集合里、也不在未取消集合里（`ob_timer_service.cpp:400-429`）。
  - **任务自己重排自己**：`ConnResourceCleanUpTask::runTimerTask` 会重新 schedule 自身（`ob_user_resource_mgr.cpp:321-330`）。
- **按规则的译法**：为满足 `'static`，任务结构里放 `Arc<ObLockTable>`；`destroy()` 里按"约定已消失"不再 wait。
- **为什么错**：
  1. **引用环导致泄漏。** owner 经由 timer 持有 task 的 `Arc`，task 又持有 owner 的 `Arc`，形成环。LS 销毁之后，整块内存都不会释放。
  2. **销毁与任务并发。** 去掉 wait 之后，destroy 与正在运行的任务并发执行，任务会读到清理了一半的对象。这在 Rust 里不算 UB，但会走到 `OB_ERR_UNEXPECTED` 或断言路径，停机顺序 stop→wait→destroy 也失去了保证。
- **建议改法**：§2.7 改为——
  - 任务持有 owner 的 `Weak`，每次运行时 `upgrade`，失败就直接返回；
  - `cancel_task`/`wait_task` 的语义原样保留：`wait_task` 返回之后，任务不在运行，也不会再运行；C++ 里所有 cancel+wait 的调用点照样翻译；
  - `TimerTask::run` 以 `self: &Arc<Self>` 为接收者，以便任务重排自身。

### F15（中）ObPriorityQueue2/LightyQueue 映射成 bounded channel，丢掉共享上限、分级余量和阻塞入队

- **规则**：§2.7：「ObLightyQueue、ObFixedQueue（作队列）、ObPriorityQueue2 | `crossbeam_channel::bounded`；满时 `try_send` 返回 `Full`，映射为 `OB_SIZE_OVERFLOW`」。
- **C++ 现场**：
  - **SQL 请求队列**：`ReqQueue = ObPriorityQueue2<1,2,3>`（`ob_server_runtime.h:50`）：
    - 上限 `server_task_queue_size`（`ob_server_runtime.cpp:333`）是 6 个优先级**共享**的一个计数；
    - push 时高优先级多给 2048 的余量，普通优先级多给 1024（`ob_priority_queue.h:191-225`）；
    - `pop_high`/`pop_normal` 只从前若干级取（`:236-244`）；
    - `SCondTemp<3>` 按优先级唤醒不同的等待类。
  - **阻塞入队**：`LightyQueue::push(data, timeout)` 满时**阻塞等待**，超时返回 `OB_TIMEOUT`（`ob_lighty_queue.cpp:208-228`）。只有 `ObLightyQueue::push` 是非阻塞的，满时返回 `OB_SIZE_OVERFLOW`（`:91-107`）。
- **按规则的译法**：每个优先级一个固定容量的 `bounded` 通道，`try_send` 满时返回 `OB_SIZE_OVERFLOW`；出队用 `select!`。
- **为什么错**：
  - **表达不了共享上限。** 每级固定容量的通道无法表达"共享计数加分级余量"：要么低优先级洪峰时高优先级请求（内部 SQL、DDL）被拒绝，要么上限被放大。
  - **失去严格优先级。** `select!` 在多个就绪通道之间随机选择。
  - **背压变成报错或忙等。** 阻塞式的 push 改成 `try_send` 之后，原本的背压就变成了报错或者忙等。
- **建议改法**：§2.7 拆行——
  - ObPriorityQueue2 → `oblib::queue::PriorityQueue2`：每级一个 `SegQueue`，加一个共享的 `AtomicI64 size`，加分级余量，再按 C++ 的唤醒类实现等待（用 `Monitor` 或 park）；`pop_high`/`pop_normal` 照抄。
  - `LightyQueue::push(timeout)` → `send_timeout`，超时返回 `OB_TIMEOUT`。
  - 只有非阻塞的 `ObLightyQueue::push` 才映射为 `try_send`，满时返回 `OB_SIZE_OVERFLOW`。

### F16（中）没有规定"先屏蔽信号，再创建任何线程"

- **规则**：
  - §0.8：「ObSignalHandle 改用 nix 实现，保留：SIGPIPE 忽略，SIGTERM → raise(SIGKILL)… SIGUSR1 优雅停机，实时信号 40–64」；
  - §0.5：「纯函数式的 seam 和 weak 钩子在 main 的第一行注册」。
- **C++ 现场**：
  - `main.cpp:678` 带注释 "change signal mask first"：在创建任何线程之前，先 `pthread_sigmask(SIG_BLOCK, {SIGPIPE, SIGTERM, SIGUSR1, 40–64})`（`ob_signal_handle.cpp:83-121`），之后创建的线程全部继承这个掩码；
  - 信号由 SignalHandle 线程用 `sigtimedwait` 统一处理（`:56-80`），其中 SIGTERM 执行 `raise(SIGKILL)`（`:133`）。
  - 注意：C++ 在屏蔽信号之前已经调用了 `MPRINT`（`main.cpp:673`）。
- **按规则的译法**：按 main.cpp 的顺序翻译。但是 §1 的日志运行时包含"异步写"：如果它在第一次打印时惰性启动写线程，或者某个 `LazyLock` 单例在构造时起线程，这些线程就会在屏蔽信号之前诞生。
- **为什么错**：进程级的信号会投递给任何一个没有屏蔽它的线程，只要有一个线程漏掉了掩码：
  - SIGTERM 按默认动作终止进程：退出码是 143，而不是 `raise(SIGKILL)` 的 137，生命周期场景会测到；
  - SIGUSR1 变成直接终止，而不是优雅停机；
  - `kill -40`（检查日志文件）会把进程杀掉。
- **建议改法**（§0.5 和 §0.8 新增）：
  - `main` 的第一条语句就是 `SigSet::thread_block`，屏蔽上述信号，早于钩子注册、日志初始化和任何 `LazyLock` 访问；
  - `LazyLock`/`OnceLock` 的初始化函数不得创建线程；日志写线程由组合根显式启动；
  - 第 5 步的冒烟测试加一项（Linux）：逐个检查 `/proc/self/task/*/status` 里的 `SigBlk`，确认所有线程都带着掩码。

### F17（低）PAUSE 映射为 `spin_loop()`，在 aarch64 上是 `isb sy`，不是 `yield`

- **规则**：§2.7：「PAUSE | `std::hint::spin_loop()`」；§2.13：「pause/yield → spin_loop」。
- **C++ 现场**：
  - `PAUSE()` 在 aarch64 上是 `yield`，在 x86 上是 `pause`（`ob_atomic.h:31-36`）。
  - 按次数校准的自旋共 86 处，例如：latch 表里 182 项是 2000 次（L3 备选要照抄）、ObSmallSpinLock 是 500 次、`wait_quiescent`（`ob_retire_station.h:67-69`）、ObLightyQueue 的出队（`ob_lighty_queue.cpp:118-124`）。
- **为什么错**：
  - Rust 的 `core::hint::spin_loop` 在 aarch64 上发出 `__isb(SY)`（见 `core/src/hint.rs`）。每次迭代的代价比 `yield` 高一个数量级以上；`yield` 在多数核心上近似 NOP（估计）。
  - 于是在 ARM 上，"自旋 N 次再睡"的总时长会整体变长。
  - 裁判的基准平台恰好是 macOS arm64（§0.11）。
- **建议改法**："PAUSE → `oblib::sync::pause()`：x86 上是 `_mm_pause`，aarch64 上是 `asm!("yield")`（放在 oblib::sync 白名单模块里），与 C++ 发出的指令一致。"

### F18（低）`eval_enumset` 的"每次执行的局部副本"会落成逐行克隆

- **规则**：§0.6：「`ObExprColumnConv::eval_enumset` 会临时改写共享表达式的 `args_[0]`… 改成每次执行的局部副本，标 BUG(port)」。
- **C++ 现场**：
  - 有 `expr_ctx_id` 时，C++ 本来就在执行上下文里建一份副本，每次执行只建一次（`ob_expr_column_conv.cpp:747-776`）；
  - 只有 `INVALID_EXP_CTX_ID` 这个分支会逐行临时改写共享表达式（`:741-746`）。
- **按规则的译法**：无效分支里没有每次执行的槽位可以存放副本，最自然的写法是逐行执行 `let mut e = expr.clone();`。
- **为什么错**：`RtExpr` 含有 `inner_fns: Box<[InnerFn]>` 和 `extra_info: Option<Box<dyn …>>`（ir-graphs §3 的草图），它的 `args` 又是指向共享边数组的 `IdRange`。替换 `args[0]` 必须新建边存储，结果是每行若干次堆分配，落在向 ENUM/SET 列插入的热路径上。
- **建议改法**："无效分支把替换后的参数作为显式实参传入，如 `eval_enumset_with_arg0(expr, ctx, args[4])`，不复制 `RtExpr`；有 ctx 的分支沿用 C++ 每次执行一份副本的做法。禁止逐行克隆 `RtExpr`。"

### F19（低）已知逐行调用的 seam 默认走槽位

- **规则**：
  - §0.4：「trait + 类型化槽位… 自由函数保留同名的转发函数」；
  - §0.4：「链接期 seam：只用于经过测量确认的热路径」。
- **C++ 现场**：
  - `query::das_scan_next_row(s)` 声明在 query（`ob_das_iter_access.h:35-37`），定义在 sql（`ob_das_scan_iter.cpp:204-214`）；storage 的检索代码逐行调用它，共 18 处，例如 `ob_text_retrieval_token_iter.cpp:207,246`、`ob_spiv_dim_iter.cpp:129,140,258`；
  - `spill_row_store_*` 有 9 处（layering 备忘 §3）。
- **为什么错**：
  - 每次调用都要做一次 `Slot::get()`（Acquire 读加 None 判断），再做一次 vtable 调用，而且无法内联；layering 备忘估计每次多 1–2 ns。
  - 这些都已经知道是逐行调用的路径，用不着等第 6 步再测量。
- **建议改法**："生成 seam 表时，把已知逐行调用的符号直接定为句柄 trait（调用方持有 `&mut dyn DasScanIter`，与 C++ 的虚调用同价）或链接期 seam，不经过槽位。"

### F20（低）128 位 CAS：§2.7 与 §0.9 冲突，计数也不对

- **规则**：§2.7：「128 位 CAS（3 处）| Latch」；§0.9：「LSN 分配器：`portable_atomic::AtomicU128`」。
- **C++ 现场**：
  - `CAS128`/`LOAD128` 在 `lsn_allocator.cpp` 里出现 21 次（`:89,94,122,131,143,161,187,196,215` 等），实现在 `atomic128.h:34-112`；
  - `REACH_COUNT_PER_SEC`（`ob_macro_utils.h:834`）只有 2 处调用（`ob_tx_ctx.cpp:3055,5276`）。
  - 因此实际一共 23 处，而不是 3 处。
- **为什么错**：翻译 `lsn_allocator` 的人如果套用 §2.7，每次 `alloc_lsn_scn` 都要拿一把 Latch。这是每次日志追加都会走的组提交路径，结果是串行化加上 park。
- **建议改法**：§2.7 改为"128 位 CAS：LSN 分配器用 `AtomicU128`（§0.9）；`REACH_COUNT_PER_SEC` 用 Latch"，计数改为 23。

### F21（低）L2→L3 的切换只看总体性能带；纯自旋 latch 会变成睡眠锁

- **规则**：§0.8：「备选：性能带不达标时，在同一个包装内换成移植的 ObLatch 自旋加 futex 语义」。
- **C++ 现场**：
  - latch 表里有 17 项本来就是纯自旋锁：13 项自旋次数为 `INT64_MAX`，4 项为 2000 万（`ob_latch_define.h:36,39,63,66,75,104-111,204,215-217`）。例如 `MEMTABLE_CALLBACK_LIST_MGR_LOCK`，对应 `ObTransCallbackMgr::rwlock_`（`ob_mvcc_trans_ctx.h:207`）。
  - 备忘原本有一个前提：性能带要覆盖 ROW_LOCK、TRANS_CTX_LOCK、桶锁、ServerRuntime 锁。规则里没有保留这个前提。
- **为什么错**：parking_lot 的 SpinWait 先做 3 轮 `cpu_relax(2/4/8)`，然后做 7 次 `thread_yield`（sched_yield 系统调用），之后就 park。C++ 设计成"永不睡眠"的锁因此变成了睡眠锁；而总体性能带未必能定位到是哪一把锁的问题。
- **建议改法**：
  - §0.8 加一条："自旋次数为 INT64_MAX 或 2000 万的 latch 从首版起就用 L3 的纯自旋模式。"
  - 包装按 LatchId 记录争用次数（只在 debug 构建中），供判断哪把锁需要切换到 L3。

### F22（低）ExtraInfo/OpCtx 每行做一次 `dyn` 向下转型

- **规则**：§0.6 用 enum 表示的封闭小集合里只列了 RawExpr、LogOp、Path、OpSpec；ir-graphs 备忘把 ExtraInfo 和 ExprOpCtx 归为 trait object。
- **C++ 现场**：
  - `static_cast<ObEnumSetInfo *>(expr.extra_info_)` 在每行求值时执行（`ob_expr_column_conv.cpp:738`）；同类转换共 31 处；
  - `get_expr_op_ctx()` 之后也跟着一次 `static_cast`（`:749-750`）。
  - 涉及的子类数：ExtraInfo 15–19 个，ExprOpCtx 25 个。
- **为什么错**：`Box<dyn ExprExtraInfo>` 要靠 `as_any().downcast_ref::<T>()` 转型，每行多两次 vtable 调用和一次 TypeId 比较；而 C++ 的 `static_cast` 不花任何代价。
- **建议改法**："ExtraInfo 和 ExprOpCtx 都是封闭集合，用 enum 表示；求值函数按 `match` 取出具体类型，或者在 CG 时把类型化的引用存进 `RtExpr` 的专用字段。"

## 3. 缺失的规则

| # | 构造 | 计数与例子 | 后果 | 建议补的规则 |
|---|---|---|---|---|
| M1 | `ObBucketLock` 家族 | `ObBucketLock` 69 处、18 个文件；各类 guard 共 156 处（`ObBucketHashWLockGuard` 80 处、`ObBucketHashRLockGuard` 26 处、`ObMultiBucketLockGuard` 9 处…）。用户：ObResourceMap（tablet 指针表）、ObBlockManager、ObKVCacheMap（`ob_kvcache_map.h:134`）、ObLSTabletService（26 处） | 见 F3：原子性被拆开，或退化成整表一把锁 | 见 F3 的 `BucketLatch` |
| M2 | `ObQSyncLock`（大读者锁） | 30 处、10 个文件，6 个实例。热读者有两个：`ObIOUsage::accumulate`，每个 IO 一次（`ob_io_struct.cpp:373-376`）；`ObLogApplyService::get_apply_status`，每个 apply 任务一次（`ob_log_apply_service.cpp:1159-1172`）。其余在 ObLogReplayService、ObDeviceManager、ObLockMemtableMgr | 没有映射。译者若退成一把 `RwLatch`，每次 IO 都要对全局锁字做两次 RMW | 映射为大读者锁：每线程一个对齐的引用槽，48 个（同 C++ `ObQSyncWrapper<48>`，体积相同）；写者先自旋再让出，不设截止时间，照抄 `ob_qsync_lock.cpp` |
| M3 | `ObQSync` 当作静默屏障（不是换指针） | ObLockWaitMgr（`ob_lock_wait_mgr.cpp:301/332,360,382/406,428/503`，注释"it should be promised that no other threads is visting the request"）、ObFifoArena（`ob_fifo_arena.h:196`）、keybtree 的销毁（`ob_keybtree.cpp:1516-1549`） | §0.8 只给了 ArcSwap，表达不了"删除之后等所有访问者离开，再把请求交还给 worker 重投" | `oblib::sync::Quiescence`：每个 itid 一个对齐的 `AtomicI64`，提供 `enter`/`leave`/`sync()`，全部是安全代码；锁等待节点以 `Box` 独占，在 `sync()` 之后重投，不用 `Arc` |
| M4 | 锁持有者查询 | 锁库之外 7 处调用，另有 1 处定义 | 见 F5 | 见 F5 |
| M5 | 带解锁钩子的复合锁 | CtxLock 含 3 把 ObLatch（`ob_trans_ctx_lock.h:91-93`）和 5 种模式（`:102`）。`unlock_ctx()` 先 `before_unlock` 收集回调，再放锁，最后 `after_unlock` 执行提交回调和 `revert_tx_ctx_without_lock`（`ob_trans_ctx_lock.cpp:161-176`、`ob_trans_ctx.cpp:46-90`） | §2.7 规则 4 只说"返回 guard"，没有规定 Drop 的顺序。如果回调在锁内执行，提交回调里取会话锁或 tx desc 锁，就会形成新的锁顺序环 | guard 的 Drop 依次执行：`before_unlock(&mut inner)` → 放锁 → `after_unlock(arg)`；回调绝不在锁内执行；多模式锁按 C++ 的模式位组合 |
| M6 | 无原子操作、无锁保护的并发普通字段（C++ 的数据竞争） | `has_set_stop()` 约 90 处（返回 `bool&`，`threads.h:104`）；`ObTxDataMemtable::state_`（`ob_tx_data_memtable.h:337,370`）；`ObTabletStatMgr::report_queue_` 的环形槽（`ob_tablet_stat_mgr.cpp:580-592`）；向量索引的 `stop_`/`stopped_`（`ob_vector_index_async_task_util.h:217-221,248-253`） | 规则只覆盖三种字段："被 `ATOMIC_*` 访问过的"、`volatile` 的、锁内的。其余字段只能在第 4 步按编译错误临时决定内存序 | 由缺口清单列出，按角色选内存序：停止标志和统计用 Relaxed；门控访问的状态机用 Acquire/Release；环形缓冲改用 `ArrayQueue` 或槽级锁 |
| M7 | seqlock | `ob_timer_monitor.cpp:181-187`：读者不加锁，整体拷贝 `records_[i]`，前后各一次 `MEM_BARRIER` 校验 seq | 没有规则；安全 Rust 无法不加锁地拷贝非原子结构 | 这一处改成 `Latch<TimerRecord>`（诊断路径，频率低），或由 oblib::sync 提供以原子字为载荷的 seqlock |
| M8 | 其余锁原语 | `ObLatchMutex` 18 处、`SimpleCond` 27 处、`ObLightyCond` 8 处、`ObFutex` 8 处、`LWaitCond`、`pthread_mutex_t` 27 处 / `pthread_cond_t` 13 处、`std::mutex` 19 处 / `std::condition_variable` 1 处、`ObMonitor` 35 处 / `ObUtilMutex` 18 处 | §2.7 的映射表里没有这些行 | ObLatchMutex → `Latch`；pthread 或 std 的 mutex+cond → `Monitor<T>`；基于 futex 的条件变量 → `oblib::sync::FutexCond`（用 parking_lot_core 的 park/unpark，放在白名单模块） |
| M9 | `ReentrantLatch<T>` 里的数据 | `ObRecursiveMutex` 21 处、6 个文件：会话的 `query_mutex_`/`thread_data_mutex_`（`ob_basic_session_info.h:1686-1687`）、`ob_major_freeze_service.h:113,119`、`ob_global_factory.h:384` | parking_lot 的 `ReentrantMutex` 只给出 `&T`，可变数据只能放进 `RefCell`；重入时外层的 `borrow_mut` 还没释放，就会 panic，在 panic=abort 下即进程退出 | ReentrantLatch 只当令牌用，数据改用 `Cell` 或原子量；或者内层再套一把非重入的 `Latch<T>`，只在最内层的短作用域里借用 |
| M10 | 线程安全的 arena | `ObSafeArenaAllocator` 29 处、9 个文件；`ALLOC_THREAD_SAFE` 11 处 | `Arena`（bumpalo）是 `!Sync` 的 | `Latch<Arena>`（与 C++ 一把自旋锁的粒度相同），或者每线程一个 arena；禁止 `unsafe impl Sync` |
| M11 | ObHashMap 的锁模式（defend mode）与持桶锁执行的回调 | `atomic_refactored` 23 处、`read_atomic` 14 处、`foreach_refactored` 53 处；默认的 `LatchReadWriteDefendMode` 每桶一把读写 latch，回调在桶锁内执行（`docs/developer-guide/en/container.md:584-600`） | §2.5 只要求"逐行移植"，没说锁模式怎么落地；回调访问同一张表会死锁 | 把 6 种锁模式作为类型参数移植，每桶一把 `RwLatch`；回调在桶锁内执行，不得访问同一张表（与 C++ 文档一致） |
| M12 | `LazyLock` 单例的首次初始化 | `get_instance()` 686 处；268 个目标文件里有动态初始化（§0.4） | C++ 在 main 之前单线程完成初始化；Rust 的首次访问可能发生在任意线程、任意锁内，初始化函数若取锁或等待别的线程就会死锁 | 组合根按 C++ 的静态初始化顺序对全部非平凡单例执行 `LazyLock::force`；初始化函数不得取锁，也不得起线程 |
| M13 | 信号掩码与线程创建的先后 | `main.cpp:673-681` | 见 F16 | 见 F16 |
| M14 | `CACHE_ALIGNED` | 143 处 | `CachePadded` 在 x86-64 上是 128 B，C++ 是 64 B（`ob_macro_utils.h:617-623`）；对齐数组（QClock 的槽、DRWLock、`ObQSyncWrapper<48>`）的内存翻倍 | `oblib::sync::CacheAligned<T>`，按架构对齐 |
| M15 | 规则之间的冲突 | ① 128 位 CAS：§2.7 与 §0.9 冲突（F20）。② MVCC `prev` 的内存序：§2.7 的通用规则是 Relaxed，storage 备忘的草图是 SeqCst（F13）。③ DashMap 规则与 ObLinearHashMap 的 `operate`/`erase_if` 冲突（F3） | 不同的译者会给出不同的答案，违反元规则 | 按 F3、F13、F20 的改法统一 |
