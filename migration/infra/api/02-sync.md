# 02 `sync`：`oblib::sync` 与 `oblib::atomic`

> - **依据**：`migration/RULEBOOK.md` v0.2 的 0.8（锁、原子操作、静默屏障）、§2.2（临界区内的日志、持锁类型的打印）、§2.7（锁和同步原语表、原子量表、原子字段规则、数据放进锁的规则 1–7）、§3 白名单第 2 项；`migration/infra/README.md` §1。基线 `7d907abfa`。
> - **状态**：API 草稿，等两名 `claude-opus-5-5` 审查者审查后冻结；冻结后由 `claude-sonnet-5-5` 实现。
> - **证据与计数**：正文的 file:line 都在基线上读过。计数用 `grep -rn --include='*.h' --include='*.cpp' --include='*.ipp' <正则> src` 得到，去掉定义文件本身；写"约"的是正则估算，口径就近注明。
> - **读者**：1d 实现者读全文；第 2、3 步的翻译者读第 2 节的签名、第 6 节的对照表，以及第 3 节标了 **【译】** 的条目。

## 0. 一页概要

- **移植 ObLatch 的语义**（0.8）：32 位锁字（bit31 WAIT、bit30 WRITE、低 30 位是写者 id 或读者数）；每次加锁按 `ObLatchIds` 查 204 项 latch 表取自旋次数和策略（FIFO/READ_PREFER）；ObLatch 的 3079 桶全局等待队列照原结构移植，每个等待者在自己的字上 park，`parking_lot_core` 只充当可移植的 futex。
- **数据放进锁**：`Latch<T>`、`RwLatch<T>`、`ShardedRwLatch<T>`、`BigReaderLatch<T>`、`QSyncLatch<T>`、`Monitor<T>`、`bitlock::ObByteLock<T>` 持有被保护的 `T`，只能经 guard 访问；guard 都是 `!Send`。
- **令牌型原语照 C++ 接口**：`BucketLatch`（及 C++ 同名的 8 种 guard）、`Quiescence`、`FutexCond`、`bitlock::{ObRowLatch, ObSmallSpinLock, ObPtrSpinLock}`，不带数据。
- **统一的加锁接口**：`lock()`/`rdlock()`/`wrlock()` 返回 guard，等价于 C++ 截止时间 `INT64_MAX`；`*_until(abs_timeout_us)` 返回 `ObResult<guard>`，超时 `OB_TIMEOUT`（`BigReaderLatch` 写锁等读者超时是 `OB_EAGAIN`）；`try*` 返回 `ObResult<guard>`，拿不到是 `OB_EAGAIN`。
- **写者身份**：锁字低 30 位记 `itid + 1`（0.8），`is_wrlocked_by_self()` 由此而来。C++ 记的是 OS tid，差别见 §3.16 和修订 A1。
- **带解锁钩子的复合锁**：`UnlockHooks` + `HookedWriteGuard`，Drop 依次 `before_unlock(&mut inner)` → 放锁 → `after_unlock(arg)`（§2.7 规则 6）。
- **`oblib::atomic`**：`inc_update`/`dec_update`（先读后 CAS，返回 C++ 的 `int64_t` 语义）、`AtomicU128` 与 `cas128`/`load128`、`ObAtomicReference`。`ATOMIC_*` 直接写 std 的 `Atomic*` 方法，对照表在 §6.3。
- **unsafe** 只在 `rust/port/oblib/src/sync/**`（§3 白名单第 2 项），约 30 处；`oblib::atomic` 不含 unsafe。

## 1. 范围

处置取值见 README §1。单元的目标路径按 RULEBOOK §4：`src/oblib/lib/<rest>` → `rust/port/oblib/src/lib_m/<rest>.rs`（`lib` 与 crate 根文件名冲突，加 `_m`）。处置为 `replace:sync`/`replace:atomic`/`generate:*` 的单元由 1d 在目标路径写只含 `pub use` 的桩文件（§4 "非翻译单元的桩文件"），每个桩文件 `pub use` 本表"Rust 去处"一列里的名字。

### 1.1 `src/oblib/lib/lock/`（21 个单元，全部）

| # | 单元（代表文件） | C++ 内容 | 处置 | Rust 去处与说明 |
|---|---|---|---|---|
| L01 | `ob_latch.{h,cpp}` | `ObLatchMutex`、`ObLatch`、`ObLatchWaitQueue`、`ObWaitProc`、`ObLatchWaitMode`、`ObLatchMutexGuard`、`ObLatchRGuard`、`ObLatchWGuard`、`ObLatchWaitEventGuard`、`ObLDLockType`、`USE_CO_LATCH`、线程局部 `current_locks`/`current_wait`/`max_lock_slot_idx` | `replace:sync` | `Latch<T>`/`ObLatchMutexGuard`、`RwLatch<T>`/`ObLatchRGuard`/`ObLatchWGuard`；锁字和等待队列在 crate 内的 `sync::raw`。不移植：`ObLatchWaitEventGuard`（只写线程局部 `current_wait`，`ob_latch.cpp:29-45`）、`current_locks`/`max_lock_slot_idx`（只写不读；`thread.h:105,111` 的 `clear_lock()` 随之删去）、`USE_CO_LATCH`（`ob_latch.cpp:24` 只定义）、`ObLDLockType`（无引用）、`record_stat_`/`enable_record_stat`（只决定是否构造上面那个 guard，`ob_latch.cpp:114-137`） |
| L02 | `ob_spin_lock.h` | `ObSpinLock`、`ObNullLock`、`ObSpinLockGuard` | `replace:sync` | `Latch<T>`（C++ 默认 latch id `DEFAULT_SPIN_LOCK`，`:38`）；`ObSpinLockGuard` 是 `ObLatchMutexGuard` 的别名。`ObNullLock` 不移植：以它为模板实参的实例（`ob_pool.h:35,106`、`ob_pooled_allocator.h:27`、`ob_encoding_allocator.h:63`）译成不加锁、单所有者（`&mut self`）的版本；以 `ObSpinLock` 为实参的 `ObLockedPool`（`ob_pool.h:102`）译成 `Latch<ObPool<…>>` |
| L03 | `ob_mutex.h` | `lib::ObMutex`、`ObMutexGuard`、`ObMutexGuardWithTimeout`、`ObMutex0` 与 `mutex_init/destroy/acquire/release/try_acquire` | `replace:sync` | `Latch<T>`（默认 `DEFAULT_MUTEX`，`:31`）；`ObMutex0` 一族没有使用者（grep 为 0），不移植 |
| L04 | `ob_spin_rwlock.h` | `SpinRWLock`、`SpinRLockGuard`、`SpinRLockManualGuard`、`SpinWLockGuard` | `replace:sync` | `RwLatch<T>`（默认 `DEFAULT_SPIN_RWLOCK`，`:35`）；`SpinRLockGuard`/`SpinWLockGuard` 是别名；`SpinRLockManualGuard`（2 处）译成 `Option<ObLatchRGuard>` |
| L05 | `ob_rwlock.{h,cpp}` | `obsys::ObRWLock`（pthread rwlock 加 `LockMode`）、`ObRLock`、`ObWLock`、`ObRLockGuard`、`ObWLockGuard`、`ObLockGuardBase` | `replace:sync` | `RwLatch<T>`，latch id 用 `DEFAULT_SPIN_RWLOCK`。使用者 3 个文件（`ob_empty_shell_task.h:54-59`、`ob_tablet_gc_service.h:117-125`、`ob_log_allocator_mgr.cpp:69,113`），全是默认的 `NO_PRIORITY`。RULEBOOK §2.7 没有这一行，见修订 A8 |
| L06 | `ob_lock.h` | `obutil::ObLockT`、`ObTryLockT` | `replace:sync` | 并入 `Monitor` 的 guard（`ObMonitor<Mutex>::Lock`/`TryLock` 的实体） |
| L07 | `ob_lock_guard.h` | `lib::ObLockGuard<T>`、`ObLockGuardWithTimeout<T>` | `replace:sync` | 由各锁的 `lock()`/`lock_until()` 返回的 guard 取代。`ObSpinLockGuardWithTimeout`（`ob_trans_define_v4.h:626`）只有 typedef，没有使用者 |
| L08 | `ob_recursive_mutex.h` | `ObRecursiveMutex`、`ObRecursiveMutexGuard` | `replace:sync` | 没有对应类型：按 §2.7 规则 5，数据放进 `Latch<T>`（latch id 照 C++ 的构造实参），最外层加锁、向下传 guard（§3.12）。约 21 处（审查 M9 的口径）；含 guard typedef 的引用行分布在 8 个文件（grep `ObRecursiveMutex`） |
| L09 | `ob_drw_lock.h` | `DRWLock`、`RDLockGuard`、`WRLockGuard`、`WRLockGuardRetryTimeout` | `replace:sync` | `ShardedRwLatch<T>`、`DRWLockRDLockGuard`、`DRWLockWRLockGuard`。`WRLockGuardRetryTimeout` 没有使用者，不移植（它把同一个 `timeout` 既当绝对截止时间传给 `wrlock`，又当相对时长传给 `ob_usleep`，`:87-112`）。实例 3 个：`ob_kvcache_inst_map.h:136`、`ob_io_calibration.h:113`、`ob_io_manager.h:196` |
| L10 | `ob_tc_ref.h` | `TCRef`、`get_global_tcref()` | `replace:sync` | `BigReaderLatch` 内部的全局槽表（crate 内）。`born`/`end`/无参 `sync()`（`:45-58`）没有使用者，不移植；也没有自定义 `TCRef` 的实例（grep 为 0） |
| L11 | `ob_tc_rwlock.h` | `LWaitCond`、`TCRWLock`（含 6 种嵌套 guard）、`TCWLockGuard`、`TCRLockGuard`、`typedef TCRWLock RWLock` | `replace:sync` | `BigReaderLatch<T>`、`TCRLockGuard`、`TCWLockGuard`；`LWaitCond` 是内部类型。C++ 里写作 `common::RWLock` 的就是 `TCRWLock`（`:359`） |
| L12 | `ob_qsync_lock.{h,cpp}` | `ObQSyncLock`、`ObQSyncLockWriteGuard`、`ObQSyncLockReadGuard` | `replace:sync` | `QSyncLatch<T>` 加同名 guard。实例 6 个 |
| L13 | `ob_bucket_lock.{h,cpp}` | `ObBucketLock` 和 8 种 guard | `replace:sync` | `BucketLatch` 加 C++ 同名 guard（令牌型，不带数据） |
| L14 | `ob_bucket_qsync_lock.{h,cpp}` | `ObBucketQSyncLock` | `delete` | RULEBOOK 0.8 列出的死代码，由清单第 16 类确认 |
| L15 | `ob_thread_cond.{h,cpp}` | `ObThreadCond`、`ObThreadCondGuard` | `replace:sync` | `Monitor<T>`、`ObThreadCondGuard` |
| L16 | `ob_monitor.h` | `obutil::ObMonitor<T>` | `replace:sync` | `Monitor<T>`（用 `Monitor::new_inited` 构造），延迟通知照抄。使用者 2 个：`ob_timer_service.h:184`、`ob_trans_result.h:51` |
| L17 | `mutex.{h,cpp}` | `obutil::ObUtilMutex`（`typedef Mutex`） | `replace:sync` | 只作 `ObMonitor` 的模板实参，并入 `Monitor` |
| L18 | `cond.{h,cpp}` | `obutil::Cond` | `replace:sync` | 并入 `Monitor` 的条件变量 |
| L19 | `ob_futex.{h,cpp}` | 三个平台的 `futex`/`futex_wait`/`futex_wake`、`ObFutex`、weak `futex_hook` | `replace:sync` | `FutexCond` 的 `val`/`futex_wait`/`futex_wake`，以及 crate 内的 futex 垫片；`futex_hook`（weak 默认实现 `ob_futex.cpp:100-110`，强定义在 03 号的 `ob_thread_hook.cpp:172,218`）不由本模块移植 |
| L20 | `ob_scond.h` | `SimpleCond`、`SCondReadyFlag`、`SCondSimpleCounter`、`SCondCounter`、`SCondSimpleIdGen`、`SCondTemp<PRIO>`、`SCond` | `replace:sync` | `SimpleCond` → `FutexCond`；`SCondTemp<PRIO>`/`SCond` 保留名字（建在 `FutexCond` 上，唯一实例化是 `ob_priority_queue.h:292` 的 `SCondTemp<3>`）；其余三个辅助类型 crate 内 |
| L21 | `ob_small_spin_lock.h` | `ObSmallSpinLock<IntType, LockBit, MaxSpin, USleep>`、`ObSmallSpinLockGuard`、`ObByteLock`、`ObByteLockGuard`、`ObPtrSpinLock<T>`、`ObPtrSpinLockGuard` | `replace:sync` | `sync::bitlock` 的同名类型；`AsLock`（`:95`）没有使用者，不移植 |

### 1.2 `src/oblib/lib/atomic/`（3 个单元，全部）

| # | 单元 | C++ 内容 | 处置 | Rust 去处与说明 |
|---|---|---|---|---|
| A01 | `ob_atomic.h` | `ATOMIC_*` 宏、`PAUSE`、`MEM_BARRIER`/`WEAK_BARRIER`/`__COMPILER_BARRIER`、`inc_update`/`dec_update`、`remove_volatile` | `replace:atomic` | `oblib::atomic::{inc_update, dec_update}`；`PAUSE` → `oblib::sync::pause`；宏按 §6.3 直接写 std 方法，不提供宏 |
| A02 | `atomic128.h` | `types::uint128_t`、`cas128`/`load128`、`CAS128`/`LOAD128`（x86 `cmpxchg16b`，aarch64 `ldaxp`/`stlxp`） | `replace:atomic` | `AtomicU128`（portable-atomic）、`cas128`、`load128`、`uint128_t`。使用者：`lsn_allocator.cpp` 21 处（08 号的 `storage_core`），`ob_macro_utils.h` 的 `REACH_COUNT_PER_SEC`（RULEBOOK §2.7 改用 `Latch`） |
| A03 | `ob_atomic_reference.{h,cpp}` | `union AtomicInt64`、`ObAtomicReference` | `replace:atomic` | `ObAtomicReference`，`AtomicU64` 上的位运算，不用 union。使用者：`share/cache/ob_kvcache_struct.{h,cpp}`（08 号） |

### 1.3 其他目录里定义锁原语、或自成锁协议的单元

| # | 单元 | C++ 内容 | 处置 | 说明 |
|---|---|---|---|---|
| O01 | `src/oblib/lib/stat/ob_latch_define.{h,cpp}` | `LATCH_DEF` X-macro 表（204 行）、`ObLatchIds`、`ObLatchPolicy`、`ObLatchDesc`、`OB_LATCHES`、`WAIT_EVENTS_TOTAL` | `generate:latch_define` | oblib 的 build.rs 读同一份头文件，生成 `ObLatchIds` 关联常量、`OB_LATCHES` 和 `ob_latch_define!`（§2.1）。`ObLatchDesc::wait_event_idx`（`:289`）与 `WAIT_EVENTS_TOTAL`（`:295`）依赖翻译单元 `ob_wait_event` 的 `ObWaitEventIds`，按函数级重映射挪到 `ob_wait_event` 单元（开放问题 Q8） |
| O02 | `src/oblib/lib/allocator/ob_qsync.h` | `ObQSyncImpl`、`ObQSyncWrapper<N>`、`ObQSync`、`QSyncCriticalGuard`、宏 `CriticalGuard`/`WaitQuiescent`、`ObDynamicQSync`、`DynamicQSyncCriticalGuard` | `replace:sync` | `Quiescence<N>`（默认 256）、`QSyncCriticalGuard`；宏的译法见 §6.2；`ObDynamicQSync` 一族没有使用者，不移植 |
| O03 | `src/oblib/lib/queue/ob_lighty_queue.{h,cpp}` | 其中的 `ObLightyCond`（`h:32-46`、`cpp:26-41`） | 单元归 05 号（`oblib::queue`） | `ObLightyCond` 由本模块的 `FutexCond` 取代 |
| O04 | `src/oblib/lib/thread/ob_queue_thread.{h,cpp}` | `ObCond`（`h:42-57`）、`S2MCond`、`S2MQueueThread` | `replace:sync`（建议） | `ObCond` 同名由本模块提供；`S2MQueueThread` 没有实例（复核 C12 已指出其中 DRWLock 从未实例化），交清单第 16 类确认后不翻译。需与 03 号对齐（Q9） |
| O05 | `src/oblib/lib/utility/utility.h:468-506` | `SeqLockGuard`、`OnceGuard` | 单元 `translate`，不归本模块 | 两者只被同文件的 `CountReporter` 使用，`CountReporter` 没有使用者（grep 为 0），建议一起按死代码处理（清单第 16 类）；若保留，`SeqLockGuard` 译成 `Latch<T>` |
| O06 | `src/storage/memtable/mvcc/ob_row_latch.h` | `ObRowLatch`、`ObRowLatchGuard` | `rewrite:storage_core`（RULEBOOK §4 已定，08 号） | 原语是 `sync::bitlock::ObRowLatch`，storage_core 的单元文件 `pub use` 它 |
| O07 | `src/storage/tx/ob_trans_ctx_lock.{h,cpp}` | `CtxLock`（3 把 ObLatch、5 种模式）、`CtxLockArg`、`CtxLockGuard` | `translate` | 用 `RwLatch` 和 `UnlockHooks`/`HookedWriteGuard` 翻译（§3.11、§6 例 12） |
| O08 | `src/storage/ls/ob_ls_lock.{h,cpp}` | `ObLSLock`（4 把 ObLatch，hold/change 位掩码）、`ObLSLockGuard`、`ObLSTryLockGuard` | `translate` | `[RwLatch<…>; 4]`，guard 存 4 个 `Option` guard；按位从低到高加锁，某一位失败后继续加后面的位（`ob_ls_lock.cpp:39-71`） |
| O09 | `src/storage/ls/ob_ls_meta.cpp:445-500` | `ObReentrantWLockGuard`、`ObReentrantRLockGuard` | `translate` | 条件重入，§2.7 规则 5（§3.12） |
| O10 | `src/storage/ob_i_memtable_mgr.h:42-160` | `MemtableMgrLock`（`void *` 加 `LockType` 擦除 `ObQSyncLock`/`SpinRWLock`） | `translate` | 持引用的 enum（`&QSyncLatch<…>` 或 `&RwLatch<…>`），guard 也是 enum |
| O11 | `src/storage/memtable/mvcc/ob_keybtree_deps.h:61-105` | `RWLock`（`read_ref_`/`writer_id_` 与 `lock_` 的 union） | `rewrite:storage_core`（08 号） | 不用本模块的锁；union 改成单个 `AtomicU32` 上的位运算 |
| O12 | `src/oblib/lib/hash/ob_hashutils.h:98-470` | `SpinLocker`、`MutexLocker`、`ReadLocker`、`WriteLocker`、`LatchReadLocker` 等 defend mode 的锁封装 | 05 号（`oblib::hash`） | 建在 `RwLatch`/`Latch` 上 |
| O13 | `src/share/ob_ex_rpc.h:67-68`、`src/observer/ob_ex_rpc.cpp:60,107` | 在 `std::atomic<int>` 上直接调 `futex_wait`/`futex_wake` | `translate` | `FutexCond::val`/`futex_wait`/`futex_wake`（§6 例 10 附注） |
| O14 | `src/oblib/lib/task/ob_timer_monitor.cpp:181-187` | seqlock | `translate` | `Latch<TimerRecord>`（RULEBOOK §2.7） |
| O15 | `src/oblib/lib/thread_local/ob_tsi_utils.{h,cpp}` | `get_itid`、`alloc_itid` | 03 号 | 本模块依赖它（§7） |
| O16 | `src/oblib/lib/allocator/ob_hazard_ref.*`、`ob_retire_station.h` | HazardRef、QClock、RetireStation | 不归本模块 | 0.8：HazardRef 不移植；QClock/RetireStation 在 storage_core（08 号） |

### 1.4 规模与用户可见面

- **使用规模**（调查备忘 §2.2 的口径）：锁声明约 461 处、RAII guard 约 2,109 处、手工 `unlock` 231 处、自带 `lock()/unlock()` 的方法 93 个。其中 `ObBucketLock` 的 guard 116 处（`ObBucketHashWLockGuard` 75、`ObBucketHashRLockGuard` 21、`ObBucketWLockGuard` 9、`ObBucketWLockAllGuard` 7、`ObBucketTryRLockAllGuard` 2、`ObBucketRLockGuard` 1、`ObMultiBucketLockGuard` 1）；guard 上调用 `get_ret()` 的 72 处；`*WithTimeout`/`*Retry*` guard 60 处；`inc_update`/`dec_update` 59 处。
- **用户可见的 4012**：两个进程级串行锁，都是默认 latch id 的 `lib::ObMutex`，用 `THIS_WORKER.get_timeout_ts()` 作截止时间加锁，超时返回 `OB_TIMEOUT`（-4012）：
  - `src/rootserver/ob_local_ddl_serial_call.h:29-47`（`local_ddl_serial_call`，40 处调用）；
  - `src/query/api/query/command/ob_root_service_serialization.h:34-55`（`serialize_root_service_call`，57 处调用）。
  - `Worker::timeout_ts_` 初值是 `INT64_MAX`（`worker.cpp:57`），未设置截止时间的线程永不超时。
- **latch 名字**：`ob_wait_event.cpp:37-42` 用 latch 表生成 `"latch: <name> wait"` 等待事件，进入等待事件视图；生成器保证名字逐字节相同（§2.1）。
- **统计**：锁操作不产生任何用户可见的统计（§3.1 G10）。

## 2. Rust API

### 2.0 通用约定

- **模块**：`rust/port/oblib/src/sync/**`（`oblib::sync`，含子模块 `bitlock`、`latch_define`）和 `rust/port/oblib/src/atomic/**`（`oblib::atomic`）。建议 06 号把 §2 里的公开名字和 `std::sync::atomic::{Atomic*, Ordering, fence, compiler_fence}` 放进 `oblib::prelude`。
- **加锁接口的形状**（所有带数据的锁一致）：

  | 形状 | 返回 | 对应 C++ |
  |---|---|---|
  | `lock()` / `rdlock()` / `wrlock()` | guard | 截止时间 `INT64_MAX` 的加锁、RAII guard 的构造 |
  | `lock_until(abs)` / `rdlock_until(abs)` / `wrlock_until(abs)` | `ObResult<guard>` | 带绝对截止时间（微秒，`ObTimeUtility::current_time()` 的时基）的加锁、`*WithTimeout` guard |
  | `trylock()` / `try_rdlock()` / `try_wrlock()` | `ObResult<guard>`，失败 `OB_EAGAIN` | C++ 返回 `int` 或 `bool` 的 try 系列 |
  | `with_locked(f)` / `with_rdlocked(f)` / `with_wrlocked(f)` | `f` 的返回值 | §2.7 规则 4 |

- **guard**：类型名沿用 C++ 的 guard 名（修订 A6）；都标 `#[must_use]`，都是 `!Send`、`!Sync`（`PhantomData<*const ()>`），只能由加锁的线程释放；有 `get_ret()` 的 C++ guard，Rust guard 也有 `get_ret() -> ObResult`，拿到 guard 时恒为 `Ok(())`（例外：未 init 的 `Monitor`，§3.8 M1）。
- **latch id 显式传入**：Rust 没有默认实参，构造时写全 C++ 的默认值（§6.1 列出）。
- **构造**：除泛型的 `ObSmallSpinLock` 和 `SCondTemp` 外，锁类型的 `new` 都是 `const fn`，可以直接写 `static`，不需要 `LazyLock`（`cfg(loom)` 下除外，§5.2）。
- **错误码**：沿用 `oblib::error` 的 `OB_*` 常量（04 号）。

### 2.1 latch 表：`oblib::sync::latch_define`

```rust
// rust/port/oblib/src/sync/latch_define.rs
//! Ported from: src/oblib/lib/stat/ob_latch_define.h, src/oblib/lib/stat/ob_latch_define.cpp
//! oblib 的 build.rs 读 src/oblib/lib/stat/ob_latch_define.h，生成 rust/port/oblib/src/generated/ 下的
//! latch_define_ids.rs（ObLatchIds 的 204 个关联常量）、latch_define_table.rs（OB_LATCHES 的字面量）
//! 和 latch_define_macro.rs（ob_latch_define! 的表体），本文件 include! 它们。

/// C++ `ObLatchIds::ObLatchIdEnum`。值是行在表中的下标（0..=203），不是 LATCH_DEF 的第二列。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct ObLatchIds(pub u32);

impl ObLatchIds {
    pub const LATCH_WAIT_QUEUE_LOCK: ObLatchIds = ObLatchIds(0);
    pub const DEFAULT_SPIN_LOCK: ObLatchIds = ObLatchIds(1);
    pub const DEFAULT_SPIN_RWLOCK: ObLatchIds = ObLatchIds(2);
    pub const DEFAULT_MUTEX: ObLatchIds = ObLatchIds(3);
    // ……生成器按 ob_latch_define.h 的顺序输出全部 204 个关联常量……
    pub const LATCH_END: ObLatchIds = ObLatchIds(203);
}
/// C++ 里写作 `ObLatchIds::ObLatchIdEnum` 的参数类型。
pub type ObLatchIdEnum = ObLatchIds;

/// C++ `ObLatchPolicy::ObLatchPolicyEnum`。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ObLatchPolicy(pub i32);
impl ObLatchPolicy {
    pub const LATCH_READ_PREFER: ObLatchPolicy = ObLatchPolicy(0);
    pub const LATCH_FIFO: ObLatchPolicy = ObLatchPolicy(1);
}

/// C++ `ObLatchDesc`。`latch_name_` 是字面量，C++ 是 `char[64]`。
#[derive(Clone, Copy, Debug)]
pub struct ObLatchDesc {
    pub latch_id_: i64,              // LATCH_DEF 第二列（如 ROW_LOCK 是 24），只用于显示
    pub latch_name_: &'static [u8],
    pub policy_: ObLatchPolicy,
    pub max_spin_cnt_: u64,          // INT64_MAX 原样存 9223372036854775807
    pub max_yield_cnt_: u64,
}
impl ObLatchDesc {
    pub const MAX_LATCH_NAME_LENGTH: i64 = 64;
    /// C++ `wait_event_id`（`ob_latch_define.h:290`）。
    pub const fn wait_event_id(latch_id: i64) -> i64 { todo!() }
}
/// C++ `#define LatchWaitEventBegin 100000`（`ob_latch_define.h:279`）。
#[allow(non_upper_case_globals)]
pub const LatchWaitEventBegin: i64 = 100000;

/// C++ `OB_LATCHES[]`，长度 LATCH_END + 1 = 204（`ob_latch_define.cpp:27-36` 的 static_assert）。
pub static OB_LATCHES: [ObLatchDesc; 204] = include!("../generated/latch_define_table.rs");

/// C++ 的 X-macro：`ob_latch_define!(cb)` 展开成
/// `cb! { (LATCH_WAIT_QUEUE_LOCK, 0, b"latch wait queue lock", LATCH_FIFO, 2000, 0, true), … }`，
/// 每行 7 列与 LATCH_DEF 的参数一一对应（def, id, name, policy, max_spin_cnt, max_yield_cnt, enable）。
/// 供 ob_wait_event 的翻译者生成 "latch: <name> wait" 一段。
#[macro_export]
macro_rules! ob_latch_define {
    ($callback:ident) => { /* 生成 */ };
}
```

### 2.2 `Latch<T>`：`ObSpinLock`、`lib::ObMutex`、`ObLatchMutex`

```rust
// rust/port/oblib/src/sync/latch.rs
use core::cell::UnsafeCell;
use core::marker::PhantomData;
use core::ops::{Deref, DerefMut};
use core::sync::atomic::AtomicU32;
use crate::error::ObResult;
use crate::sync::{raw, ObLatchIds};

/// 互斥 latch，锁字语义是 ObLatchMutex 的移植（§3.2）。
/// 整体按缓存行对齐：C++ 的 ObLatchMutex 内含 CACHE_ALIGNED 的 ObFutex（ob_futex.h:130）。
#[cfg_attr(target_arch = "x86_64", repr(C, align(64)))]
#[cfg_attr(target_arch = "aarch64", repr(C, align(128)))]
pub struct Latch<T> {
    lock_: raw::ObLatchMutex,
    latch_id_: AtomicU32,
    data_: UnsafeCell<T>,
}
// SAFETY: 与 std::sync::Mutex<T> 相同：T 只经持锁的 guard 访问。
unsafe impl<T: Send> Send for Latch<T> {}
unsafe impl<T: Send> Sync for Latch<T> {}

impl<T> Latch<T> {
    /// C++ `ObSpinLock(latch_id)`、`lib::ObMutex(latch_id)`、`ObLatchMutex()`。
    pub const fn new(latch_id: ObLatchIds, value: T) -> Self { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    /// 拥有 `&mut` 时不加锁直接访问（构造、发布前）。
    pub fn get_mut(&mut self) -> &mut T { todo!() }

    /// C++ `lock()`（截止时间 INT64_MAX）、`ObSpinLockGuard`/`ObMutexGuard`/`ObLatchMutexGuard` 的构造。
    pub fn lock(&self) -> ObLatchMutexGuard<'_, T> { todo!() }
    /// C++ `ObMutex::lock(abs_timeout_us)`、`ObLatchMutex::lock(latch_id, abs_timeout_us)`、
    /// `ObMutexGuardWithTimeout`。`ObSpinLock::lock(timeout_us)` 是相对时长，见 §6.2。
    pub fn lock_until(&self, abs_timeout_us: i64) -> ObResult<ObLatchMutexGuard<'_, T>> { todo!() }
    /// C++ `ObSpinLock::trylock()`、`ObMutex::trylock()`。失败 OB_EAGAIN。
    pub fn trylock(&self) -> ObResult<ObLatchMutexGuard<'_, T>> { todo!() }
    /// C++ `ObLatchMutex::try_lock(latch_id)`；与 `trylock` 相同。
    pub fn try_lock(&self) -> ObResult<ObLatchMutexGuard<'_, T>> { todo!() }
    /// §2.7 规则 4。
    pub fn with_locked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }

    /// C++ `ObLatchMutex::is_locked()`：锁字非 0（只有 WAIT 位也算）。
    pub fn is_locked(&self) -> bool { todo!() }
    /// C++ `get_wid()`：锁字低 30 位（这里是 itid + 1）。
    pub fn get_wid(&self) -> u32 { todo!() }
    /// C++ `ObSpinLock::self_locked()`（ob_spin_lock.h:88-91）。
    pub fn is_wrlocked_by_self(&self) -> bool { todo!() }
    /// C++ `set_latch_id`（ObMutex、SpinRWLock、TCRWLock 有）。
    pub fn set_latch_id(&self, latch_id: ObLatchIds) { todo!() }
    pub fn get_latch_id(&self) -> ObLatchIds { todo!() }
}

#[must_use]
pub struct ObLatchMutexGuard<'a, T> {
    latch_: &'a Latch<T>,
    _not_send: PhantomData<*const ()>,
}
impl<'a, T> ObLatchMutexGuard<'a, T> {
    /// C++ guard 的 `get_ret()`；拿到 guard 即为 Ok(())。
    pub fn get_ret(&self) -> ObResult { todo!() }
}
impl<T> Deref for ObLatchMutexGuard<'_, T> { type Target = T; fn deref(&self) -> &T { todo!() } }
impl<T> DerefMut for ObLatchMutexGuard<'_, T> { fn deref_mut(&mut self) -> &mut T { todo!() } }
impl<T> Drop for ObLatchMutexGuard<'_, T> { fn drop(&mut self) { todo!() } }

pub type ObSpinLockGuard<'a, T = ()> = ObLatchMutexGuard<'a, T>;
pub type ObMutexGuard<'a, T = ()> = ObLatchMutexGuard<'a, T>;
```

### 2.3 `RwLatch<T>`：`SpinRWLock`、`ObLatch`、`obsys::ObRWLock`

```rust
// rust/port/oblib/src/sync/rw_latch.rs
/// 读写 latch，锁字与等待队列是 ObLatch 的移植（§3.3）。不对齐（C++ ObLatch 8 字节，ob_latch.h:251-252）。
pub struct RwLatch<T> {
    lock_: raw::ObLatch,
    latch_id_: AtomicU32,
    data_: UnsafeCell<T>,
}
// SAFETY: 与 std::sync::RwLock<T> 相同。
unsafe impl<T: Send> Send for RwLatch<T> {}
unsafe impl<T: Send + Sync> Sync for RwLatch<T> {}

impl<T> RwLatch<T> {
    pub const fn new(latch_id: ObLatchIds, value: T) -> Self { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    pub fn get_mut(&mut self) -> &mut T { todo!() }

    pub fn rdlock(&self) -> ObLatchRGuard<'_, T> { todo!() }
    pub fn rdlock_until(&self, abs_timeout_us: i64) -> ObResult<ObLatchRGuard<'_, T>> { todo!() }
    /// 失败 OB_EAGAIN；读者数到 2^24 时 OB_SIZE_OVERFLOW（§3.3 R1）。
    pub fn try_rdlock(&self) -> ObResult<ObLatchRGuard<'_, T>> { todo!() }
    pub fn wrlock(&self) -> ObLatchWGuard<'_, T> { todo!() }
    pub fn wrlock_until(&self, abs_timeout_us: i64) -> ObResult<ObLatchWGuard<'_, T>> { todo!() }
    pub fn try_wrlock(&self) -> ObResult<ObLatchWGuard<'_, T>> { todo!() }
    pub fn with_rdlocked<R>(&self, f: impl FnOnce(&T) -> R) -> R { todo!() }
    pub fn with_wrlocked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }

    pub fn is_locked(&self) -> bool { todo!() }      // ob_latch.h:376-379
    pub fn is_rdlocked(&self) -> bool { todo!() }    // :381-385
    pub fn is_wrlocked(&self) -> bool { todo!() }    // :387-391
    /// C++ `is_wrlocked_by()`（puid 为空）、`CtxLock::is_locked_by_self()`。
    pub fn is_wrlocked_by_self(&self) -> bool { todo!() }
    pub fn get_wid(&self) -> u32 { todo!() }         // :400-404
    pub fn get_rdcnt(&self) -> u32 { todo!() }       // :406-410
    /// C++ `ObLatch::val()`：锁字原值，供日志。
    pub fn val(&self) -> u32 { todo!() }
    pub fn set_latch_id(&self, latch_id: ObLatchIds) { todo!() }
    pub fn get_latch_id(&self) -> ObLatchIds { todo!() }
}

#[must_use]
pub struct ObLatchRGuard<'a, T> { latch_: &'a RwLatch<T>, _not_send: PhantomData<*const ()> }
#[must_use]
pub struct ObLatchWGuard<'a, T> { latch_: Option<&'a RwLatch<T>>, _not_send: PhantomData<*const ()> }

impl<'a, T> ObLatchRGuard<'a, T> { pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a, T> ObLatchWGuard<'a, T> {
    pub fn get_ret(&self) -> ObResult { todo!() }
    /// C++ `wr2rdlock()`：写锁降级为读锁，并只唤醒排在队首的读者（§3.3 R8）。
    pub fn wr2rdlock(self) -> ObLatchRGuard<'a, T> { todo!() }
}
impl<T> Deref for ObLatchRGuard<'_, T> { type Target = T; fn deref(&self) -> &T { todo!() } }
impl<T> Deref for ObLatchWGuard<'_, T> { type Target = T; fn deref(&self) -> &T { todo!() } }
impl<T> DerefMut for ObLatchWGuard<'_, T> { fn deref_mut(&mut self) -> &mut T { todo!() } }
impl<T> Drop for ObLatchRGuard<'_, T> { fn drop(&mut self) { todo!() } }
impl<T> Drop for ObLatchWGuard<'_, T> { fn drop(&mut self) { todo!() } }

pub type SpinRLockGuard<'a, T = ()> = ObLatchRGuard<'a, T>;
pub type SpinWLockGuard<'a, T = ()> = ObLatchWGuard<'a, T>;
```

### 2.4 `ShardedRwLatch<T>`：`DRWLock`

```rust
// rust/port/oblib/src/sync/sharded.rs
/// SYNC_MAX_CPU_NUM 个缓存行对齐的 ObLatch 分片（x86 64、ARM 128，与 OB_MAX_CPU_NUM 相同，ob_define.h:1483-1487）。
pub struct ShardedRwLatch<T> {
    latches_: [CacheAligned<raw::ObLatch>; SYNC_MAX_CPU_NUM],
    latch_id_: AtomicU32,
    data_: UnsafeCell<T>,
}
unsafe impl<T: Send> Send for ShardedRwLatch<T> {}
unsafe impl<T: Send + Sync> Sync for ShardedRwLatch<T> {}

impl<T> ShardedRwLatch<T> {
    /// C++ 默认 latch id 是 DEFAULT_DRW_LOCK（ob_drw_lock.h:31，自旋 200000）。
    pub const fn new(latch_id: ObLatchIds, value: T) -> Self { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    pub fn get_mut(&mut self) -> &mut T { todo!() }
    pub fn rdlock(&self) -> DRWLockRDLockGuard<'_, T> { todo!() }
    pub fn try_rdlock(&self) -> ObResult<DRWLockRDLockGuard<'_, T>> { todo!() }
    pub fn wrlock(&self) -> DRWLockWRLockGuard<'_, T> { todo!() }
    /// C++ `wrlock(int64_t timeout)`：参数名叫 timeout，实际是绝对截止时间（直接传给 ObLatch::wrlock）。
    pub fn wrlock_until(&self, abs_timeout_us: i64) -> ObResult<DRWLockWRLockGuard<'_, T>> { todo!() }
    pub fn try_wrlock(&self) -> ObResult<DRWLockWRLockGuard<'_, T>> { todo!() }
    pub fn with_rdlocked<R>(&self, f: impl FnOnce(&T) -> R) -> R { todo!() }
    pub fn with_wrlocked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }
}
/// C++ `DRWLock::RDLockGuard`；记住加锁时的分片下标。
#[must_use] pub struct DRWLockRDLockGuard<'a, T> { lock_: &'a ShardedRwLatch<T>, shard_: usize, _not_send: PhantomData<*const ()> }
/// C++ `DRWLock::WRLockGuard`。
#[must_use] pub struct DRWLockWRLockGuard<'a, T> { lock_: &'a ShardedRwLatch<T>, _not_send: PhantomData<*const ()> }
impl<T> DRWLockRDLockGuard<'_, T> { pub fn get_ret(&self) -> ObResult { todo!() } }
impl<T> DRWLockWRLockGuard<'_, T> { pub fn get_ret(&self) -> ObResult { todo!() } }
// Deref（读、写）、DerefMut（写）、Drop 同 §2.3。
```

### 2.5 `BigReaderLatch<T>`：`TCRWLock`

```rust
// rust/port/oblib/src/sync/big_reader.rs
/// 大读者锁：读者把本锁 read_ref_ 的地址登记进全局每线程槽表（TCRef），写者扫表（§3.5）。
/// 实例大小与 C++ 同一量级、不随 CPU 数放大；lcond_ 内的 futex 字按缓存行对齐，与 C++ 的 ObFutex 相同。
pub struct BigReaderLatch<T> {
    latch_: raw::ObLatch,
    latch_id_: AtomicU32,
    lcond_: raw::LWaitCond,
    write_id_: AtomicI32,
    read_ref_: AtomicI32,
    data_: UnsafeCell<T>,
}
unsafe impl<T: Send> Send for BigReaderLatch<T> {}
unsafe impl<T: Send + Sync> Sync for BigReaderLatch<T> {}

impl<T> BigReaderLatch<T> {
    /// C++ 默认 latch id 是 DEFAULT_SPIN_RWLOCK（ob_tc_rwlock.h:143）。
    pub const fn new(latch_id: ObLatchIds, value: T) -> Self { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    pub fn get_mut(&mut self) -> &mut T { todo!() }
    /// C++ `rdlock()`、`RLockGuard`、`TCRLockGuard`。
    pub fn rdlock(&self) -> TCRLockGuard<'_, T> { todo!() }
    /// C++ `rdlock(abs_timeout_us)`、`RLockGuardWithTimeout`。超时 OB_TIMEOUT。
    pub fn rdlock_until(&self, abs_timeout_us: i64) -> ObResult<TCRLockGuard<'_, T>> { todo!() }
    /// C++ `try_rdlock()`（返回 bool）。失败 OB_EAGAIN。
    pub fn try_rdlock(&self) -> ObResult<TCRLockGuard<'_, T>> { todo!() }
    /// C++ `wrlock()`、`WLockGuard`、`TCWLockGuard`。
    pub fn wrlock(&self) -> TCWLockGuard<'_, T> { todo!() }
    /// C++ `wrlock(abs_timeout_us)`、`WLockGuardWithTimeout`。拿内层 latch 超时 OB_TIMEOUT，等读者超时 OB_EAGAIN。
    pub fn wrlock_until(&self, abs_timeout_us: i64) -> ObResult<TCWLockGuard<'_, T>> { todo!() }
    /// C++ `try_wrlock()`（返回 bool）。失败 OB_EAGAIN。
    pub fn try_wrlock(&self) -> ObResult<TCWLockGuard<'_, T>> { todo!() }
    /// C++ `wrlock_with_retry(abs)`、`WLockGuardWithRetry`。
    pub fn wrlock_with_retry(&self, abs_timeout_us: i64) -> TCWLockGuard<'_, T> { todo!() }
    /// C++ `WLockGuardWithRetryInterval(lock, try_thresold_us, retry_interval_us)`（ob_tc_rwlock.h:119-140）。
    pub fn wrlock_with_retry_interval(&self, try_thresold_us: i64, retry_interval_us: u32) -> TCWLockGuard<'_, T> { todo!() }
    pub fn with_rdlocked<R>(&self, f: impl FnOnce(&T) -> R) -> R { todo!() }
    pub fn with_wrlocked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }
    pub fn set_latch_id(&self, latch_id: ObLatchIds) { todo!() }
}
/// C++ `TCRLockGuard`（带槽号的读 guard，ob_tc_rwlock.h:332-357）。
#[must_use] pub struct TCRLockGuard<'a, T> { lock_: &'a BigReaderLatch<T>, slot_id_: i64, _not_send: PhantomData<*const ()> }
/// C++ `TCWLockGuard`。
#[must_use] pub struct TCWLockGuard<'a, T> { lock_: &'a BigReaderLatch<T>, _not_send: PhantomData<*const ()> }
impl<T> TCRLockGuard<'_, T> { pub fn get_ret(&self) -> ObResult { todo!() } }
impl<T> TCWLockGuard<'_, T> { pub fn get_ret(&self) -> ObResult { todo!() } }
// Deref（读、写）、DerefMut（写）、Drop 同 §2.3。
```

### 2.6 `Quiescence` 与 `QSyncLatch<T>`：`ObQSync`、`ObQSyncLock`

```rust
// rust/port/oblib/src/sync/qsync.rs
use core::sync::atomic::AtomicI64;

/// C++ ObQSync 的槽数（ObQSyncWrapper<256>，ob_qsync.h:121）。
pub const QSYNC_REF_NUM: usize = 256;

/// C++ `ObQSyncWrapper<N>`/`ObQSync`：N 个缓存行对齐的读者计数，按 itid % N 选槽。全是安全代码。
pub struct Quiescence<const N: usize = QSYNC_REF_NUM> {
    ref_array_: [CacheAligned<AtomicI64>; N],
}
impl<const N: usize> Quiescence<N> {
    pub const fn new() -> Self { todo!() }
    /// C++ 宏 `CriticalGuard(qs)`（ob_qsync.h:133）。
    pub fn enter(&self) -> QSyncCriticalGuard<'_, N> { todo!() }
    /// C++ `acquire_ref()`：返回槽号。给 storage_core 等需要跨作用域持有的场合。
    pub fn acquire_ref(&self) -> i64 { todo!() }
    /// C++ `release_ref(int64_t idx)`。
    pub fn release_ref(&self, idx: i64) { todo!() }
    /// C++ 宏 `WaitQuiescent(qs)` 即 `sync()`。
    pub fn sync(&self) { todo!() }
    pub fn try_sync(&self) -> bool { todo!() }
}
/// C++ `QSyncCriticalGuard`；Drop 时 release_ref(ref_)。
#[must_use]
pub struct QSyncCriticalGuard<'a, const N: usize = QSYNC_REF_NUM> {
    qsync_: &'a Quiescence<N>,
    ref_: i64,
    _not_send: PhantomData<*const ()>,
}
impl<const N: usize> Drop for QSyncCriticalGuard<'_, N> { fn drop(&mut self) { todo!() } }

/// C++ `ObQSyncLock`：写标志加 48 槽的 Quiescence（ob_qsync_lock.h:42-46）。
pub struct QSyncLatch<T> {
    write_flag_: CacheAligned<AtomicI64>,
    qsync_: Quiescence<48>,
    data_: UnsafeCell<T>,
}
unsafe impl<T: Send> Send for QSyncLatch<T> {}
unsafe impl<T: Send + Sync> Sync for QSyncLatch<T> {}
impl<T> QSyncLatch<T> {
    pub const fn new(value: T) -> Self { todo!() }
    /// C++ `init(mem_attr)` 恒成功；ObMemAttr 参数删去（RULEBOOK 0.7）。
    pub fn init(&self) -> ObResult { todo!() }
    /// C++ `is_inited()` 恒为 true（ob_qsync_lock.h:33）。
    pub fn is_inited(&self) -> bool { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    pub fn get_mut(&mut self) -> &mut T { todo!() }
    pub fn rdlock(&self) -> ObQSyncLockReadGuard<'_, T> { todo!() }
    pub fn try_rdlock(&self) -> ObResult<ObQSyncLockReadGuard<'_, T>> { todo!() }
    pub fn wrlock(&self) -> ObQSyncLockWriteGuard<'_, T> { todo!() }
    pub fn try_wrlock(&self) -> ObResult<ObQSyncLockWriteGuard<'_, T>> { todo!() }
    pub fn with_rdlocked<R>(&self, f: impl FnOnce(&T) -> R) -> R { todo!() }
    pub fn with_wrlocked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }
}
#[must_use] pub struct ObQSyncLockReadGuard<'a, T> { lock_: &'a QSyncLatch<T>, idx_: i64, _not_send: PhantomData<*const ()> }
#[must_use] pub struct ObQSyncLockWriteGuard<'a, T> { lock_: &'a QSyncLatch<T>, _not_send: PhantomData<*const ()> }
// Deref（读、写）、DerefMut（写）、Drop 同 §2.3。
```

### 2.7 `BucketLatch`：`ObBucketLock` 家族

```rust
// rust/port/oblib/src/sync/bucket.rs
use std::sync::OnceLock;
use core::sync::atomic::{AtomicBool, AtomicU64};

/// 令牌型：不带数据，被保护的容器自身线程安全（ObLinearHashMap、BucketedHashMap 等）。
/// 锁数 = bucket_cnt / 8 + 1，桶 i 用第 i / 8 把 latch（§3.7）。
pub struct BucketLatch {
    bucket_cnt_: AtomicU64,
    latch_cnt_: AtomicU64,
    latches_: OnceLock<Box<[raw::ObLatch]>>,
    latch_id_: AtomicU32,
    is_inited_: AtomicBool,
}
impl BucketLatch {
    pub const fn new() -> Self { todo!() }
    /// C++ `init(bucket_cnt, latch_id = DEFAULT_BUCKET_LOCK, label/attr)`；label/attr 删去。
    pub fn init(&self, bucket_cnt: u64, latch_id: ObLatchIds) -> ObResult { todo!() }
    /// 只复位状态，不释放数组（RULEBOOK 0.5）；数组随 Drop 释放。
    pub fn destroy(&self) { todo!() }
    // 以下 C++ 公开方法照搬（显式加解锁；令牌型，无内存安全问题）。
    pub fn try_rdlock(&self, bucket_idx: u64) -> ObResult { todo!() }
    pub fn try_wrlock(&self, bucket_idx: u64) -> ObResult { todo!() }
    pub fn rdlock(&self, bucket_idx: u64, abs_timeout_us: i64) -> ObResult { todo!() }
    pub fn wrlock(&self, bucket_idx: u64, abs_timeout_us: i64) -> ObResult { todo!() }
    pub fn unlock(&self, bucket_idx: u64) -> ObResult { todo!() }
    pub fn try_wrlock_all(&self) -> ObResult { todo!() }
    pub fn try_rdlock_all(&self) -> ObResult { todo!() }
    pub fn wrlock_all(&self) -> ObResult { todo!() }
    pub fn unlock_all(&self) -> ObResult { todo!() }
}

// C++ 同名 guard；构造即加锁，失败时记日志、get_ret() 返回错误码、析构不解锁——与 C++ 相同。
#[must_use] pub struct ObBucketRLockGuard<'a>       { lock_: &'a BucketLatch, index_: u64, ret_: ObResult, lock_start_ts_: i64 }
#[must_use] pub struct ObBucketWLockGuard<'a>       { lock_: &'a BucketLatch, index_: u64, ret_: ObResult, lock_start_ts_: i64 }
#[must_use] pub struct ObBucketHashRLockGuard<'a>   { guard_: ObBucketRLockGuard<'a> }
#[must_use] pub struct ObBucketHashWLockGuard<'a>   { guard_: ObBucketWLockGuard<'a> }
#[must_use] pub struct ObBucketWLockAllGuard<'a>    { lock_: &'a BucketLatch, ret_: ObResult, lock_start_ts_: i64 }
#[must_use] pub struct ObBucketTryWLockAllGuard<'a> { lock_: &'a BucketLatch, ret_: ObResult, lock_start_ts_: i64 }
#[must_use] pub struct ObBucketTryRLockAllGuard<'a> { lock_: &'a BucketLatch, ret_: ObResult, lock_start_ts_: i64 }
#[must_use] pub struct ObMultiBucketLockGuard<'a>   { lock_: &'a BucketLatch, is_write_lock_: bool, latch_array_: Vec<u64> }

impl<'a> ObBucketRLockGuard<'a>       { pub fn new(lock: &'a BucketLatch, bucket_index: u64) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketWLockGuard<'a>       { pub fn new(lock: &'a BucketLatch, bucket_index: u64) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketHashRLockGuard<'a>   { pub fn new(lock: &'a BucketLatch, hash_value: u64) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketHashWLockGuard<'a>   { pub fn new(lock: &'a BucketLatch, hash_value: u64) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketWLockAllGuard<'a>    { pub fn new(lock: &'a BucketLatch) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketTryWLockAllGuard<'a> { pub fn new(lock: &'a BucketLatch) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObBucketTryRLockAllGuard<'a> { pub fn new(lock: &'a BucketLatch) -> Self { todo!() } pub fn get_ret(&self) -> ObResult { todo!() } }
impl<'a> ObMultiBucketLockGuard<'a> {
    pub fn new(lock: &'a BucketLatch, is_write_lock: bool) -> Self { todo!() }
    /// C++ 参数是 `ObIArray<uint64_t> &`，只读，按所有权改成切片。
    pub fn lock_multi_buckets(&mut self, hash_array: &[u64]) -> ObResult { todo!() }
}
// 每种 guard 都有 Drop（§3.7 K5、K6）。
```

### 2.8 `Monitor<T>` 与 `ObCond`

```rust
// rust/port/oblib/src/sync/monitor.rs
use std::sync::{Condvar, Mutex, MutexGuard};
use core::sync::atomic::{AtomicBool, AtomicI32, AtomicI64};

/// 锁加条件变量：ObThreadCond、ObMonitor<Mutex>、pthread_mutex_t+pthread_cond_t、
/// std::mutex+std::condition_variable、ObUtilMutex+Cond（RULEBOOK §2.7）。
pub struct Monitor<T> {
    mutex_: Mutex<T>,
    cond_: Condvar,
    event_no_: AtomicI32,
    is_inited_: AtomicBool,
}
impl<T> Monitor<T> {
    /// ObThreadCond 语义：构造后未 init，init 之前的操作返回 OB_NOT_INIT（§3.8 M1）。
    pub const fn new(value: T) -> Self { todo!() }
    /// ObMonitor、pthread/std 的 mutex+cond：构造即可用（C++ 这些类型没有 init）。
    pub const fn new_inited(value: T) -> Self { todo!() }
    /// C++ `ObThreadCond::init(event_no)`；第二次 OB_INIT_TWICE。
    pub fn init(&self, event_no: i32) -> ObResult { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn is_inited(&self) -> bool { todo!() }
    pub fn into_inner(self) -> T { todo!() }
    pub fn get_mut(&mut self) -> &mut T { todo!() }
    /// C++ `ObThreadCondGuard`、`ObMonitor<Mutex>::Lock` 的构造。
    pub fn lock(&self) -> ObThreadCondGuard<'_, T> { todo!() }
    /// C++ `ObMonitor::trylock()`、`ObMonitor<Mutex>::TryLock`。失败 OB_EAGAIN。
    pub fn trylock(&self) -> ObResult<ObThreadCondGuard<'_, T>> { todo!() }
    /// C++ `ObThreadCond::signal()`/`broadcast()`：可以不持锁调用；未 init 返回 OB_NOT_INIT。
    pub fn signal(&self) -> ObResult { todo!() }
    pub fn broadcast(&self) -> ObResult { todo!() }
}

#[must_use]
pub struct ObThreadCondGuard<'a, T> {
    mon_: &'a Monitor<T>,
    guard_: Option<MutexGuard<'a, T>>,
    nnotify_: i32,              // ObMonitor 的延迟通知计数：0 无，-1 广播，n>0 通知 n 次
    ret_: ObResult,
}
impl<'a, T> ObThreadCondGuard<'a, T> {
    /// 未 init 的 Monitor 返回 OB_NOT_INIT（仍然持锁，§3.8 M1）。
    pub fn get_ret(&self) -> ObResult { todo!() }
    /// C++ `ObThreadCond::wait(time_ms)`：0 表示不限时；超时 OB_TIMEOUT。
    pub fn wait(&mut self, time_ms: u64) -> ObResult { todo!() }
    /// C++ `ObThreadCond::wait_us(time_us)`。
    pub fn wait_us(&mut self, time_us: u64) -> ObResult { todo!() }
    pub fn signal(&self) -> ObResult { todo!() }
    pub fn broadcast(&self) -> ObResult { todo!() }
    /// C++ `ObMonitor::notify()`/`notify_all()`：延迟到解锁或 wait 之前执行。
    pub fn notify(&mut self) { todo!() }
    pub fn notify_all(&mut self) { todo!() }
    /// C++ `ObMonitor::timed_wait(ObSysTime(timeout_us))`：超时也返回 true（§3.8 M5）。
    pub fn timed_wait(&mut self, timeout_us: i64) -> bool { todo!() }
}
impl<T> Deref for ObThreadCondGuard<'_, T> { type Target = T; fn deref(&self) -> &T { todo!() } }
impl<T> DerefMut for ObThreadCondGuard<'_, T> { fn deref_mut(&mut self) -> &mut T { todo!() } }
/// 先执行延迟通知，再解锁。
impl<T> Drop for ObThreadCondGuard<'_, T> { fn drop(&mut self) { todo!() } }

/// C++ `ObCond`（ob_queue_thread.h:42-57），按 64 字节对齐（C++ 写死 aligned(64)）。
#[repr(C, align(64))]
pub struct ObCond {
    spin_wait_num_: i64,
    bcond_: AtomicBool,
    last_waked_time_: AtomicI64,
    mon_: Monitor<()>,
}
impl ObCond {
    pub const SPIN_WAIT_NUM: i64 = 0;
    pub const BUSY_INTERVAL: i64 = 1000;
    pub const fn new(spin_wait_num: i64) -> Self { todo!() }
    pub fn signal(&self) { todo!() }
    pub fn timedwait(&self, time_us: i64) -> ObResult { todo!() }
    pub fn wait(&self) -> ObResult { todo!() }
}
```

### 2.9 `FutexCond` 与 `SCondTemp`

```rust
// rust/port/oblib/src/sync/futex_cond.rs
/// SimpleCond、ObLightyCond、ObFutex 合成一个类型（RULEBOOK §2.7）；futex 字按缓存行对齐，同 C++ 的 ObFutex。
#[cfg_attr(target_arch = "x86_64", repr(C, align(64)))]
#[cfg_attr(target_arch = "aarch64", repr(C, align(128)))]
pub struct FutexCond {
    futex_: raw::FutexWord,      // { v_: AtomicU32, sys_waiters_: AtomicI32 }
    n_waiters_: AtomicU32,
    event_no_: AtomicI32,
}
impl FutexCond {
    /// C++ `SimpleCond()`、`ObLightyCond()`、`ObFutex()`。
    pub const fn new() -> Self { todo!() }
    /// C++ `SimpleCond(int32_t event_no)`。event_no 不影响行为（ob_scond.h:38-50 两个分支相同）。
    pub const fn new_with_event(event_no: i32) -> Self { todo!() }
    pub fn init(&self, event_no: i32) { todo!() }
    /// C++ `SimpleCond::get_key()`。
    pub fn get_key(&self) -> u32 { todo!() }
    /// C++ `ObLightyCond::get_seq()`，与 get_key 相同。
    pub fn get_seq(&self) -> u32 { todo!() }
    /// C++ `SimpleCond::wait(key, timeout)`、`ObLightyCond::wait(cmp, timeout)`；timeout 是相对微秒。
    pub fn wait(&self, key: u32, timeout_us: i64) -> ObResult { todo!() }
    /// C++ `SimpleCond::signal(limit)`；`ObLightyCond::signal()` 写成 `signal(i32::MAX as u32)`。返回唤醒数。
    pub fn signal(&self, limit: u32) -> u32 { todo!() }
    /// C++ `ObFutex::val()`/`uval()`：给自建协议的使用者（ob_ex_rpc）。
    pub fn val(&self) -> &AtomicU32 { todo!() }
    /// C++ `ObFutex::wait(v, timeout)`、裸 `futex_wait`；timeout 相对微秒，i64::MAX 视为不限时。
    pub fn futex_wait(&self, v: u32, timeout_us: i64) -> ObResult { todo!() }
    /// C++ `ObFutex::wake(n)`、裸 `futex_wake`。
    pub fn futex_wake(&self, n: i64) -> i32 { todo!() }
}

/// C++ `SCondTemp<PRIO>`（ob_scond.h:131-215），COND_COUNT = SYNC_MAX_CPU_NUM。
pub struct SCondTemp<const PRIO: usize> {
    lock_: raw::SCondReadyFlag,                       // CacheAligned<AtomicI32>
    conds_: [[FutexCond; PRIO]; SYNC_MAX_CPU_NUM],
    n2wakeup_: raw::SCondCounter,                     // [CacheAligned<AtomicI64>; SYNC_MAX_CPU_NUM]
}
impl<const PRIO: usize> SCondTemp<PRIO> {
    pub const LOOP_LIMIT: i64 = 8;
    pub fn new() -> Self { todo!() }
    pub fn signal(&self, x: u32, prio: i32, fixed_wakeup_order: bool) { todo!() }
    pub fn prepare(&self, prio: i32, index: i32) { todo!() }
    pub fn wait(&self, timeout_us: i64) -> ObResult { todo!() }
}
pub type SCond = SCondTemp<1>;
```

### 2.10 `bitlock`

```rust
// rust/port/oblib/src/sync/bitlock.rs（即 oblib::sync::bitlock）
    use core::cell::UnsafeCell;
    use core::marker::PhantomData;
    use core::sync::atomic::{AtomicBool, AtomicPtr, AtomicU8};
    use crate::error::ObResult;

    mod sealed { pub trait Sealed {} }
    /// ObSmallSpinLock 可用的整数宽度：u8、u16、u32、u64（密封）。
    pub trait SmallLockInt: sealed::Sealed + Copy + Eq { type Atomic: Send + Sync; }

    /// C++ `ObSmallSpinLock<IntType, LockBit, MaxSpin, USleep>`：锁位与数据在同一个字里，全程原子访问。
    /// 令牌型，方法照 C++（显式 lock/unlock）。
    pub struct ObSmallSpinLock<I: SmallLockInt, const LOCK_BIT: u32 = 0, const MAX_SPIN: i64 = 500, const USLEEP: u32 = 100> {
        lock_: I::Atomic,
    }
    impl<I: SmallLockInt, const LOCK_BIT: u32, const MAX_SPIN: i64, const USLEEP: u32> ObSmallSpinLock<I, LOCK_BIT, MAX_SPIN, USLEEP> {
        pub fn new() -> Self { todo!() }
        pub fn init(&self) { todo!() }
        pub fn try_lock(&self) -> bool { todo!() }
        /// C++ `lock(event_no)`；event_no 不影响行为（ob_small_spin_lock.h:134-164 两个分支相同），删去。
        pub fn lock(&self) { todo!() }
        pub fn unlock(&self) { todo!() }
        pub fn is_locked(&self) -> bool { todo!() }
        pub fn get_data(&self) -> I { todo!() }
        pub fn set_data(&self, val: I) { todo!() }
    }
    /// C++ `ObSmallSpinLockGuard<LockType>`。
    #[must_use] pub struct ObSmallSpinLockGuard<'a, I: SmallLockInt, const LOCK_BIT: u32 = 0, const MAX_SPIN: i64 = 500, const USLEEP: u32 = 100> {
        lock_: &'a ObSmallSpinLock<I, LOCK_BIT, MAX_SPIN, USLEEP>,
    }
    impl<'a, I: SmallLockInt, const LOCK_BIT: u32, const MAX_SPIN: i64, const USLEEP: u32> ObSmallSpinLockGuard<'a, I, LOCK_BIT, MAX_SPIN, USLEEP> {
        pub fn new(lock: &'a ObSmallSpinLock<I, LOCK_BIT, MAX_SPIN, USLEEP>) -> Self { todo!() }
    }

    /// C++ `ObByteLock` = `ObSmallSpinLock<uint8_t, 0>`（:213），可带被保护的数据。
    pub struct ObByteLock<T = ()> { lock_: AtomicU8, data_: UnsafeCell<T> }
    unsafe impl<T: Send> Send for ObByteLock<T> {}
    unsafe impl<T: Send> Sync for ObByteLock<T> {}
    impl<T> ObByteLock<T> {
        pub const fn new(value: T) -> Self { todo!() }
        /// C++ `ObByteLockGuard guard(lock, event_no)`；event_no 删去。
        pub fn lock(&self) -> ObByteLockGuard<'_, T> { todo!() }
        pub fn try_lock(&self) -> ObResult<ObByteLockGuard<'_, T>> { todo!() }
        pub fn is_locked(&self) -> bool { todo!() }
        pub fn into_inner(self) -> T { todo!() }
        pub fn get_mut(&mut self) -> &mut T { todo!() }
        pub fn with_locked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R { todo!() }
    }
    /// 不带数据时的显式加解锁，给 storage_core 的回调链表（跨函数持有，如 ob_tx_callback_list.h:187 的 try_lock_log）。
    impl ObByteLock<()> {
        pub fn raw_lock(&self) { todo!() }
        pub fn raw_try_lock(&self) -> bool { todo!() }
        pub fn raw_unlock(&self) { todo!() }
    }
    #[must_use] pub struct ObByteLockGuard<'a, T = ()> { lock_: &'a ObByteLock<T>, _not_send: PhantomData<*const ()> }
    // Deref、DerefMut、Drop。

    /// C++ `ObPtrSpinLock<T>`：指针最低位作锁位；strict provenance（map_addr、AtomicPtr::fetch_and）。
    pub struct ObPtrSpinLock<T> { lock_: AtomicPtr<T> }
    impl<T> ObPtrSpinLock<T> {
        pub const fn new() -> Self { todo!() }
        pub fn init(&self) { todo!() }
        pub fn try_lock(&self) -> bool { todo!() }
        pub fn lock(&self) { todo!() }
        pub fn unlock(&self) { todo!() }
        /// 返回去掉锁位的指针；解引用由调用方在白名单模块里负责。
        pub fn get_ptr(&self) -> *mut T { todo!() }
        pub fn set_ptr(&self, ptr: *const T) { todo!() }
    }
    #[must_use] pub struct ObPtrSpinLockGuard<'a, T> { lock_: &'a ObPtrSpinLock<T> }
    impl<'a, T> ObPtrSpinLockGuard<'a, T> { pub fn new(lock: &'a ObPtrSpinLock<T>) -> Self { todo!() } }

    /// C++ `ObRowLatch`（ob_row_latch.h:26-44）：纯 TAS 自旋，1 字节。令牌型，给 storage_core。
    pub struct ObRowLatch { locked_: AtomicBool }
    impl ObRowLatch {
        pub const fn new() -> Self { todo!() }
        pub fn is_locked(&self) -> bool { todo!() }
        pub fn try_lock(&self) -> bool { todo!() }
        pub fn lock(&self) { todo!() }
        pub fn unlock(&self) { todo!() }
    }
    /// C++ `ObRowLatch::Guard`（typedef ObRowLatchGuard）。
    #[must_use] pub struct ObRowLatchGuard<'a> { host_: &'a ObRowLatch }
    impl<'a> ObRowLatchGuard<'a> { pub fn new(host: &'a ObRowLatch) -> Self { todo!() } }
    // ObSmallSpinLockGuard、ObPtrSpinLockGuard、ObRowLatchGuard 的 Drop 调 unlock()。
```

### 2.11 带解锁钩子的复合锁：`UnlockHooks`、`HookedWriteGuard`

```rust
// rust/port/oblib/src/sync/hooked.rs
/// 由宿主实现（如 ObTransCtx）。钩子不得 panic；after_unlock 运行时不持有这把 latch。
pub trait UnlockHooks {
    /// 被这把 latch 保护的数据。
    type Inner;
    /// C++ 的 CtxLockArg：before_unlock 收集、after_unlock 消费。
    type Arg;
    fn hooked_latch(&self) -> &RwLatch<Self::Inner>;
    /// 持锁时执行，可以改 inner。
    fn before_unlock(&self, inner: &mut Self::Inner) -> Self::Arg;
    /// 放锁之后执行（提交回调、revert 等）。
    fn after_unlock(&self, arg: Self::Arg);
}

/// 写 guard 的 Drop 依次执行 before_unlock(&mut inner) → 放锁 → after_unlock(arg)（RULEBOOK §2.7 规则 6）。
#[must_use]
pub struct HookedWriteGuard<'a, H: UnlockHooks> {
    host_: &'a H,
    guard_: Option<ObLatchWGuard<'a, H::Inner>>,
}
impl<'a, H: UnlockHooks> HookedWriteGuard<'a, H> {
    pub fn wrlock(host: &'a H) -> Self { todo!() }
    pub fn wrlock_until(host: &'a H, abs_timeout_us: i64) -> ObResult<Self> { todo!() }
    pub fn try_wrlock(host: &'a H) -> ObResult<Self> { todo!() }
    /// 接管一把已经拿到的写 guard（C++ "先 lock 再用 need_lock=false 的 guard 接管"）。
    pub fn from_guard(host: &'a H, guard: ObLatchWGuard<'a, H::Inner>) -> Self { todo!() }
}
impl<H: UnlockHooks> Deref for HookedWriteGuard<'_, H> { type Target = H::Inner; fn deref(&self) -> &H::Inner { todo!() } }
impl<H: UnlockHooks> DerefMut for HookedWriteGuard<'_, H> { fn deref_mut(&mut self) -> &mut H::Inner { todo!() } }
impl<H: UnlockHooks> Drop for HookedWriteGuard<'_, H> { fn drop(&mut self) { todo!() } }
```

### 2.12 `CacheAligned`、`pause`

```rust
// rust/port/oblib/src/sync/aligned.rs
#[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
compile_error!("arch unsupported"); // 同 ob_atomic.h:38 的 #error

/// C++ `CACHE_ALIGN_SIZE`（ob_macro_utils.h:617-621）。
#[cfg(target_arch = "x86_64")]  pub const CACHE_ALIGN_SIZE: usize = 64;
#[cfg(target_arch = "aarch64")] pub const CACHE_ALIGN_SIZE: usize = 128;

/// C++ `CACHE_ALIGNED`（ob_macro_utils.h:623）。不用 crossbeam_utils::CachePadded（x86-64 上是 128 字节）。
#[cfg_attr(target_arch = "x86_64", repr(align(64)))]
#[cfg_attr(target_arch = "aarch64", repr(align(128)))]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq, Hash)]
pub struct CacheAligned<T> { value: T }
impl<T> CacheAligned<T> {
    pub const fn new(value: T) -> Self { todo!() }
    pub fn into_inner(self) -> T { todo!() }
}
impl<T> Deref for CacheAligned<T> { type Target = T; fn deref(&self) -> &T { todo!() } }
impl<T> DerefMut for CacheAligned<T> { fn deref_mut(&mut self) -> &mut T { todo!() } }

/// C++ `PAUSE()`（ob_atomic.h:33,36）：x86-64 `pause`，aarch64 `yield`。
/// 指令由 03 号的 oblib::arch 发出（asm! 只能在 arch，RULEBOOK §1）；不用 core::hint::spin_loop（aarch64 上是 isb sy）。
#[inline(always)]
pub fn pause() { todo!() }

/// 与 C++ OB_MAX_CPU_NUM 同值（ob_define.h:1483-1487）；crate 内使用，避免与 ob_define 的翻译重名。
pub(crate) const SYNC_MAX_CPU_NUM: usize = if cfg!(target_arch = "x86_64") { 64 } else { 128 };
```

### 2.13 `ObToString`：持锁类型的打印（§2.2）

```rust
// rust/port/oblib/src/sync/print.rs
use crate::fmt::{ObBuf, ObToString}; // 04 号

// 锁内数据用 try 系列读，拿不到（含本线程自己持有）就输出 "<locked>"；绝不阻塞。
impl<T: ObToString> ObToString for Latch<T>          { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for RwLatch<T>        { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for ShardedRwLatch<T> { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for BigReaderLatch<T> { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for QSyncLatch<T>     { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for Monitor<T>        { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
impl<T: ObToString> ObToString for bitlock::ObByteLock<T> { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
/// C++ TO_STRING_KV(K_(bucket_cnt), K_(latch_cnt), K_(latch_id))（ob_bucket_lock.h:52）。
impl ObToString for BucketLatch { fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() } }
```

C++ 打印 `ObLatch` 成员（`ObLatch::to_string` 输出 `lock_:<u32>`，`ob_latch.cpp:715-720`）的地方，译成 `kv!("lock_", self.lock_.val())`。

### 2.14 `oblib::atomic`

```rust
// rust/port/oblib/src/atomic/mod.rs
//! Ported from: src/oblib/lib/atomic/ob_atomic.h, src/oblib/lib/atomic/atomic128.h,
//!              src/oblib/lib/atomic/ob_atomic_reference.h, src/oblib/lib/atomic/ob_atomic_reference.cpp
use core::sync::atomic::{AtomicI32, AtomicI64, AtomicIsize, AtomicU32, AtomicU64, AtomicUsize};
use crate::error::ObResult;

mod sealed { pub trait Sealed {} }
/// inc_update/dec_update 接受的原子整数（AtomicI32/U32/I64/U64/Isize/Usize；cfg(loom) 下另加 loom 的同名类型）。
pub trait ObAtomicInt: sealed::Sealed + Sync {
    type Value: Copy + PartialOrd;
    #[doc(hidden)] fn ob_load(&self) -> Self::Value;                                   // SeqCst
    #[doc(hidden)] fn ob_vcas(&self, cmpv: Self::Value, newv: Self::Value) -> Self::Value; // 强 CAS，SeqCst，返回旧值
    #[doc(hidden)] fn ob_to_i64(v: Self::Value) -> i64;                                // C 的整数转换（u64 截断）
}

/// C++ `inc_update(T *v, T x)`（ob_atomic.h:97-108）：只有 x 更大时才 CAS；返回更新后的值（int64_t）。
pub fn inc_update<A: ObAtomicInt>(v: &A, x: A::Value) -> i64 { todo!() }
/// C++ `dec_update(T *v, T x)`（ob_atomic.h:110-121）。
pub fn dec_update<A: ObAtomicInt>(v: &A, x: A::Value) -> i64 { todo!() }

/// 128 位原子量（LSN 分配器用，RULEBOOK 0.9）。
pub use portable_atomic::AtomicU128;

/// C++ `types::uint128_t`（atomic128.h:24-30）：lo 在低地址。
#[allow(non_camel_case_types)]
#[repr(C, align(16))]
#[derive(Clone, Copy, Default, PartialEq, Eq, Hash, Debug)]
pub struct uint128_t { pub lo: u64, pub hi: u64 }
impl uint128_t {
    /// (hi << 64) | lo
    pub const fn to_u128(self) -> u128 { todo!() }
    pub const fn from_u128(v: u128) -> Self { todo!() }
}
/// C++ `CAS128(src, cmp, with)`：成功 true；失败 false 并把 cmp 改成当前值。SeqCst。
pub fn cas128(src: &AtomicU128, cmp: &mut uint128_t, with: uint128_t) -> bool { todo!() }
/// C++ `LOAD128(dest, src)`。SeqCst。
pub fn load128(src: &AtomicU128) -> uint128_t { todo!() }

/// C++ `ObAtomicReference`：低 32 位是引用计数 ref，高 32 位是序号 seq（ob_atomic_reference.h:24-37）。
pub struct ObAtomicReference { atomic_num_: AtomicU64 }
impl ObAtomicReference {
    pub const fn new() -> Self { todo!() }
    /// 只清 ref，seq 保留。
    pub fn reset(&self) { todo!() }
    pub fn inc_ref_cnt(&self) -> ObResult { todo!() }
    pub fn check_seq_num_and_inc_ref_cnt(&self, seq_num: u32) -> ObResult { todo!() }
    pub fn check_and_inc_ref_cnt(&self) -> ObResult { todo!() }
    pub fn dec_ref_cnt_and_inc_seq_num(&self, ref_cnt: &mut u32) -> ObResult { todo!() }
    pub fn get_seq_num(&self) -> u32 { todo!() }
    pub fn get_ref_cnt(&self) -> u32 { todo!() }
}
impl Default for ObAtomicReference { fn default() -> Self { todo!() } }
```

### 2.15 crate 内部（`pub(crate)`，供 1d 实现者和 oblib 内的 05 号使用）

```rust
// rust/port/oblib/src/sync/raw.rs —— 锁字与等待队列，名字沿用 C++。
pub(crate) const WRITE_MASK: u32 = 1 << 30;           // ob_latch.h:66,248
pub(crate) const WAIT_MASK: u32 = 1 << 31;            // ob_latch.h:67,249
pub(crate) const MAX_READ_LOCK_CNT: u32 = 1 << 24;    // ob_latch.h:250
pub(crate) const MAX_SPIN_CNT_AFTER_WAIT: u64 = 1;    // ob_latch.h:65
pub(crate) const LATCH_MAP_BUCKET_CNT: usize = 3079;  // ob_latch.h:150

/// C++ ObFutex 的数据部分。C++ 的 ObFutex 自带 CACHE_ALIGNED；Rust 把对齐放在外层：
/// Latch<T>、FutexCond 整体对齐，ObLatchBucket 用 CacheAligned 包，LWaitCond 里显式 CacheAligned。
pub(crate) struct FutexWord { pub(crate) v_: AtomicU32, pub(crate) sys_waiters_: AtomicI32 }

/// C++ ObLatchMutex 的锁字（8 字节，不单独对齐）。
pub(crate) struct ObLatchMutex { lock_: FutexWord }
impl ObLatchMutex {
    pub(crate) const fn new() -> Self { todo!() }
    pub(crate) fn lock(&self, latch_id: ObLatchIds, abs_timeout_us: i64) -> ObResult { todo!() }
    pub(crate) fn try_lock(&self, latch_id: ObLatchIds) -> ObResult { todo!() }
    pub(crate) fn unlock(&self) -> ObResult { todo!() }
    pub(crate) fn is_locked(&self) -> bool { todo!() }
    pub(crate) fn get_wid(&self) -> u32 { todo!() }
}
/// C++ ObLatch 的锁字（4 字节）。
pub(crate) struct ObLatch { lock_: AtomicU32 }
impl ObLatch {
    pub(crate) const fn new() -> Self { todo!() }
    pub(crate) fn try_rdlock(&self, latch_id: ObLatchIds) -> ObResult { todo!() }
    pub(crate) fn try_wrlock(&self, latch_id: ObLatchIds) -> ObResult { todo!() }
    pub(crate) fn rdlock(&self, latch_id: ObLatchIds, abs_timeout_us: i64) -> ObResult { todo!() }
    pub(crate) fn wrlock(&self, latch_id: ObLatchIds, abs_timeout_us: i64) -> ObResult { todo!() }
    pub(crate) fn wr2rdlock(&self) -> ObResult { todo!() }
    pub(crate) fn unlock(&self) -> ObResult { todo!() }
    pub(crate) fn val(&self) -> u32 { todo!() }
}
/// C++ ObWaitProc（ob_latch.h:75-99）：等待者栈上的节点，用裸指针挂进桶链表（§4 U4）。
pub(crate) struct ObWaitProc {
    addr_: usize,                       // 所等 latch 的锁字地址，只比较
    mode_: i32,                         // ObLatchWaitMode：READ_WAIT = 1，WRITE_WAIT = 2
    wait_: AtomicI32,                   // 1 = 在队列里等，0 = 已被唤醒
    prev_: Cell<*mut ObWaitProc>,
    next_: Cell<*mut ObWaitProc>,
    _pin: PhantomPinned,
}
/// C++ ObLatchWaitQueue::ObLatchBucket（ob_latch.h:116-123）。
pub(crate) struct ObLatchBucket {
    lock_: ObLatchMutex,                // latch id LATCH_WAIT_QUEUE_LOCK
    wait_list_: UnsafeCell<WaitList>,   // 侵入式双向链表的头尾指针；只在持有 lock_ 时访问
}
pub(crate) struct WaitList { head_: *mut ObWaitProc, tail_: *mut ObWaitProc }
/// C++ ObLatchWaitQueue。
pub(crate) struct ObLatchWaitQueue { wait_map_: [CacheAligned<ObLatchBucket>; LATCH_MAP_BUCKET_CNT] }
impl ObLatchWaitQueue {
    pub(crate) const fn new() -> Self { todo!() }
    /// §3.3 R4–R6。
    pub(crate) fn wait(&self, latch: &ObLatch, latch_id: ObLatchIds, wid: u32, mode: i32, abs_timeout_us: i64) -> ObResult { todo!() }
    /// §3.3 R8。
    pub(crate) fn wake_up(&self, latch: &ObLatch, only_rd_wait: bool) -> ObResult { todo!() }
}
/// 静态单例，const 初始化，不走 LazyLock（RULEBOOK 0.5 的初始化限制）。
pub(crate) static WAIT_QUEUE: ObLatchWaitQueue = ObLatchWaitQueue::new();

/// C++ LWaitCond（ob_tc_rwlock.h:29-51，BigReaderLatch 内部）。
pub(crate) struct LWaitCond { futex_: CacheAligned<FutexWord> }
/// C++ TCRef（ob_tc_ref.h:27-135）：1024 × 8 个槽，槽里存 read_ref_ 的地址（usize），只比较不解引用。
pub(crate) struct TCRef { ref_: [AtomicUsize; 1024 * 8] }
impl TCRef {
    pub(crate) const fn new() -> Self { todo!() }
    pub(crate) fn inc_ref(&self, p: &AtomicI32, slot_id: &mut i64) -> i32 { todo!() }
    pub(crate) fn dec_ref(&self, p: &AtomicI32, slot_id: i64) -> i32 { todo!() }
    pub(crate) fn sync(&self, p: &AtomicI32) -> i32 { todo!() }
}
pub(crate) static GLOBAL_TCREF: TCRef = TCRef::new();

/// C++ SCondReadyFlag、SCondCounter（ob_scond.h:68-121），SCondTemp 内部。
pub(crate) struct SCondReadyFlag { lock_: CacheAligned<AtomicI32> }
pub(crate) struct SCondCounter { count_: [CacheAligned<AtomicI64>; SYNC_MAX_CPU_NUM] }

// rust/port/oblib/src/sync/futex.rs —— 可移植的 futex，底层 parking_lot_core（cfg(loom) 下换成模型，§5.2）。
pub(crate) enum FutexWaitResult { Woken, Mismatch, TimedOut }
/// timeout_us 是相对微秒；按 §3.9 F5 的平台规则换算；None 表示不限时。
pub(crate) fn futex_wait(word: &AtomicU32, expected: u32, timeout_us: Option<i64>) -> FutexWaitResult { todo!() }
/// 返回值按 §3.9 F5 的平台规则。
pub(crate) fn futex_wake(word: &AtomicU32, n: i32) -> i32 { todo!() }

// rust/port/oblib/src/sync/clock.rs —— 截止时间的时基：墙上时钟微秒，与 ObTimeUtility::current_time() 同一时基。
pub(crate) fn current_time() -> i64 { todo!() }

// rust/port/oblib/src/sync/itid.rs —— 03 号的 get_itid()；cfg(loom) 下换成 loom 线程局部。
pub(crate) fn get_itid() -> i64 { todo!() }
```

## 3. 语义

每条给 C++ 证据。**【译】**标出翻译者需要知道的点。

### 3.1 通用

- **G1 锁字**：ObLatchMutex 和 ObLatch 都是 32 位字：bit31 `WAIT_MASK`、bit30 `WRITE_MASK`、低 30 位在写锁时是写者 id，读锁时是读者数（`ob_latch.h:66-67,248-251`）。
- **G2 写者 id**：Rust 写 `itid + 1`（03 号的 `get_itid()`，`ob_tsi_utils.h:47-56`；最大 65536，远小于 2^30），0 表示无写者。`is_wrlocked_by_self()` 等于 WRITE 位为 1 且低 30 位等于本线程的 `itid + 1`（C++ `is_wrlocked_by`，`ob_latch.h:393-398`）。C++ 记的是 `GETTID()`（OS tid，`ob_latch.cpp:91,523,569`，`ob_define.h:1911-1932`），差别只在日志里的锁字数值，见 §3.16 和修订 A1。
- **G3 latch id**：每个锁对象记一个 latch id，构造时给定，可用 `set_latch_id` 改。C++ ObLatch/ObLatchMutex 在每次调用时传 latch id；在基线上按（文件，对象）统计直接调 ObLatch 的 28 个对象，每个只用一个 latch id（Python 扫描 `ObLatch[RW]Guard` 与 `.rdlock/.wrlock/.try_*lock(ObLatchIds::…)`），所以按对象记录不改变行为。
- **G4 latch 表**：每次加锁按 `OB_LATCHES[id]` 取 `max_spin_cnt_`、`max_yield_cnt_`、`policy_`（`ob_latch.cpp:102,107,456,553,660,672,675`）。表共 204 行：FIFO 180、READ_PREFER 24；自旋 2000 的 183 行（含写作 `2000L` 的 `HASH_MAP_LOCK` 和 `LATCH_END`）、`INT64_MAX` 13 行、2000 万 4 行、1 两行（`ALLOC_OBJECT_LOCK`、`ALLOC_BLOCK_LOCK`）、200 一行（`ROW_LOCK`）、200000 一行（`DEFAULT_DRW_LOCK`）；让出次数全是 0（Python 解析 `ob_latch_define.h`）。
  - `INT64_MAX` 的 13 项：自旋计数器是 `uint64_t`，永远到不了上限，所以永不睡眠（`ob_latch.cpp:644,660`、`ob_latch.h:352-353`）。
  - 2000 万的 4 项（`ELECTION_LOCK`、`CLOG_TASK_LOCK`、`SERVER_OBJECT_POOL_ARENA_LOCK`、`PARTITION_AUDIT_SPIN_LOCK`）：自旋满 2000 万次后照常进等待队列。RULEBOOK 0.8 把它们与前 13 项合称"17 项纯自旋"，实现照表执行即可。
  - `max_yield_cnt_` 照移植（每次 `sched_yield` 后重新自旋），虽然现在全是 0。
- **G5 PAUSE**：自旋循环里每次失败后 `pause()`（x86 `pause`、aarch64 `yield`，`ob_atomic.h:33,36`）。例外照 C++：`ObRowLatch::lock` 和 `Quiescence::sync` 不 PAUSE（`ob_row_latch.h:38-41`、`ob_qsync.h:65-74`）。
- **G6 截止时间**：`abs_timeout_us` 是墙上时钟微秒，与 `ObTimeUtility::current_time()` 同一时基（Linux `gettimeofday`，`ob_time_utility.cpp:161-170`）。`abs_timeout_us <= 0` 返回 `OB_INVALID_ARGUMENT` 并记 WARN（`ob_latch.cpp:93-98,649-656`）。截止时间已过并不立即失败：照样走完自旋阶段，拿不到才返回 `OB_TIMEOUT`（`ob_latch.cpp:100-142`、`:658-704`）。【译】
- **G7 内存序**：移植代码逐句照 C++：`ATOMIC_*` → `SeqCst`；`volatile` 普通读 → `Acquire`；普通写 → `Release`；`MEM_BARRIER` → `fence(SeqCst)`。加锁成功的 CAS（SeqCst）对 guard 内的访问提供 acquire，放锁的 RMW 提供 release。
- **G8 guard 与线程**：guard `!Send`，由加锁线程释放。C++ 不带 puid 的 `ObLatch::unlock()` 允许任意线程解锁；基线里带 puid 的 3 处（`ob_server_runtime.h:313-340`、`ob_tablet_ddl_kv_mgr.cpp:174-193`）传的都是本线程的 `GETTID()`，改成 guard 后语义不变。跨线程解锁的用法由清单第 7 类逐处找出，不提供 API。【译】
- **G9 日志**：WARN/ERROR 照 C++ 的位置和文本，前缀 COMMON（C++ `COMMON_LOG`）；超时路径的 DEBUG 日志照抄。等待队列的桶锁内只在不可能发生的错误分支打日志（`ob_latch.cpp:299-302,361-367`），照抄。
- **G10 统计**：锁操作不产生任何统计。`ObLatchWaitEventGuard` 只把线程局部 `current_wait` 设成锁字地址（`ob_latch.cpp:29-45`），`current_wait`/`current_locks` 在 lock 目录外没有读者（grep：只有 `thread.h:105,111` 调 `clear_lock()`）；`ObLatchStat` 数组（`ob_diagnose_info.h:37-104`）不被锁代码写入。所以 `enable_record_stat` 不翻译。

### 3.2 `Latch<T>`（ObLatchMutex）

- **L1 自旋**：`low_try_lock(max_spin_cnt, WRITE_MASK | wid)`：每轮锁字为 0 时 CAS(0 → WRITE|wid)，成功即返回；失败 `pause()`（`ob_latch.h:350-363`、`ob_latch.cpp:100-110`）。
- **L2 等待**（`ob_latch.cpp:144-176`）：循环：剩余时间 ≤ 0 → `OB_TIMEOUT`；读锁字 `lock`；若 WAIT 已置位，或 CAS(`lock|WRITE` → `lock|WAIT`) 返回的旧值非 0，就在锁字上 futex 等待（期望值 `lock|WAIT`，超时为剩余时间）；等待返回且没超时，就试一次 CAS(0 → `WAIT|WRITE|wid`)，成功即拿到锁（等过的线程拿锁时总带上 WAIT 位）。
- **L3 解锁**：`swap(0)`；旧值有 WAIT 位就唤醒 1 个（`ob_latch.cpp:178-190`）。
- **L4 trylock**：一次 CAS(0 → WRITE|wid)；只有 WAIT 位、实际无人持有时也失败（`ob_latch.cpp:62-81`）。
- **L5 查询**：`is_locked()` 是锁字非 0，只有 WAIT 位也算（`ob_latch.h:365-368`）；`get_wid()` 返回低 30 位，不看 WRITE 位（`:370-374`）。
- **L6 相对与绝对**【译】：`ObSpinLock::lock(timeout_us)` 是相对时长，内部换成 `current_time() + timeout_us`（`ob_spin_lock.h:68-71`）；`ObMutex::lock(abs_timeout_us)`、`ObLatchMutex::lock(id, abs)` 是绝对时间（`ob_mutex.h:36`、`ob_latch.h:47-50`）。
- **L7 guard**：C++ `ObLockGuard` 构造失败时记 ERROR、析构不解锁（`ob_lock_guard.h:50-67`）。Rust `lock()` 不会失败，`get_ret()` 恒为 `Ok(())`。latch id 非法（`>= LATCH_END`）只会来自错误的 `ObLatchIds(raw)`：可失败的接口照 C++ 返回 `OB_INVALID_ARGUMENT`；不可失败的接口记 ERROR 后 `ob_abort`（`FIXED(port)`：C++ 的 guard 会在没拿到锁的情况下执行临界区）。
- **L8 两个串行锁**（§1.4）：`lock_until(THIS_WORKER.get_timeout_ts())`，latch id `DEFAULT_MUTEX`（自旋 2000）；截止时间已过时先自旋 2000 次，拿不到返回 `OB_TIMEOUT`，由调用方原样上抛，用户看到 4012。
- **L9 对齐**：`Latch<T>` 整体按 `CACHE_ALIGN_SIZE` 对齐，锁字不与相邻对象共享缓存行（C++ 的 ObFutex 是 `CACHE_ALIGNED`，`ob_futex.h:130`，使 ObSpinLock 在 x86 上占 192 字节、ARM 上 384 字节）。Rust 的 `Latch<()>` 是 64/128 字节，同一量级（Q1）。

### 3.3 `RwLatch<T>`（ObLatch 与全局等待队列）

- **R1 try_rdlock**（`ob_latch.cpp:480-518`）：循环直到有确定结果：WRITE 位 → `OB_EAGAIN`；读者数 ≥ 2^24 → `OB_SIZE_OVERFLOW` 并记 ERROR；FIFO 策略且 WAIT 位 → `OB_EAGAIN`；CAS(+1) 成功 → 成功；CAS 失败 → `pause()` 重试。
- **R2 try_wrlock**：一次 CAS(0 → WRITE|wid)（`:520-537`）。
- **R3 自旋阶段**（`:634-706`、`ob_latch.h:412-453`）：每轮读锁字，调加锁函数：写者要求锁字为 0；FIFO 的读者要求无 WRITE 且无 WAIT；READ_PREFER 的读者忽略 WAIT（`ob_latch.cpp:553`）。读者数溢出（`OB_SIZE_OVERFLOW`）立即带错退出。失败 `pause()`。
- **R4 入队**（`:422-478`）：桶 = 锁字地址 % 3079（`:229,330`）；在桶锁（ObLatchMutex，latch id `LATCH_WAIT_QUEUE_LOCK`，`ob_latch.h:128-134`）内循环：试加锁，成功即返回；冲突时 CAS 置 WAIT 位，成功后入队；**READ_PREFER 策略的读等待者插到桶链表头，其余插到尾**（`:456-471`）；`wait_ = 1`。
- **R5 等待循环**（`:243-293`）：剩余时间 ≤ 0 → `OB_TIMEOUT`；在 `proc.wait_` 上等待（期望值 1）；醒来没超时，就用"忽略 WAIT"的加锁函数反复尝试，直到看到冲突（写者此时接受锁字恰为 WAIT_MASK）；仍未拿到就重新走 R4（已在链表里的不重复入队，`:458,465`）。
- **R6 退出清理**（`:295-321`）：返回前若 `wait_ == 1`，加桶锁再查一次；仍为 1 就把自己摘下，桶里若已没有同一把锁的等待者就清 WAIT 位。
- **R7 解锁**（`:605-632`）：写锁 → `fetch_and(WAIT_MASK)`；读锁 → `fetch_sub(1)` 后的新值；结果恰为 `WAIT_MASK`（无持有者、有等待者）时调 wake_up。解未加锁的 latch 返回 `OB_ERR_UNEXPECTED`（guard 下不会发生）。
- **R8 wake_up**（`:327-420`）：在桶锁内按链表顺序只看同一把锁的等待者：队首是写者就唤醒这一个；否则唤醒从队首开始的连续读者，遇到写者停下；`wr2rdlock` 的唤醒（only_rd_wait）只唤醒连续读者，队首是写者就什么也不做。之后若链表里已没有这把锁的等待者，清 WAIT 位。对每个被唤醒者：`fence(SeqCst)`，`wait_ = 0`，再按地址唤醒；**存 0 之后不再访问该节点**（`:404-407` 的注释）。实际唤醒数为 0、不是降级、且还有等待者时重复整轮（`:415`），保证至少唤醒一个正在睡的线程。
- **R9 写锁降级**（`:586-603`）：要求本线程持写锁，否则 `OB_ERR_UNEXPECTED`（guard 保证成立）；CAS 循环把锁字改成 `(lock & WAIT_MASK) + 1`；再按 R8 只唤醒读者。基线里 lock 目录外没有调用者（grep 为 0），照样提供。
- **R10 READ_PREFER 的效果**：读者在自旋阶段越过等待中的写者（R3），排队时也排在所有写者之前（R4），唤醒时一次全部放行（R8）。
- **R11 查询**：`is_locked` 是锁字非 0；`is_rdlocked` 是无 WRITE 且去掉 WAIT 后大于 0；`get_rdcnt` 在写锁时返回 0（`ob_latch.h:376-410`）。
- **R12 布局**：`RwLatch` 不对齐；锁字 4 字节，加 latch id 4 字节。

### 3.4 `ShardedRwLatch<T>`（DRWLock）

- **D1** 分片数 `SYNC_MAX_CPU_NUM`，每片一个缓存行对齐的 ObLatch（`ob_drw_lock.h:115-119`）：x86 64 × 64 = 4 KB，ARM 128 × 128 = 16 KB，与 C++ 相同。
- **D2 读**：分片 = `get_itid() % 分片数`，在该片上按 §3.3 加读锁（`:128-131`）；guard 记住分片下标，解锁用记下的下标（C++ 重新计算，同一线程结果相同，`:133-136`）。
- **D3 写**：按 0 → N−1 依次加写锁，截止时间相同；某片失败就倒序释放已拿到的片并返回错误（`:138-156`）；解锁按 N−1 → 0（`:158-169`）；`try_wrlock` 同理（`:176-194`）。
- **D4** 读、写 guard 失败时记 WARN（`:39-86`），在 Rust 里只可能出现在 `*_until`/`try_*` 的错误返回上。

### 3.5 `BigReaderLatch<T>`（TCRWLock + TCRef）

- **B1 槽表**：全局 1024 × 8 个槽（`ob_tc_ref.h:30-39`），线程的 8 个槽从 `8 × (itid % 1024)` 开始（`:102-115`）。槽里存锁的 `read_ref_` 地址；Rust 存成 `usize`，只比较、不解引用。
- **B2 读者快路径**（`ob_tc_rwlock.h:259-272`）：`write_id_ == 0` 时，在自己的 8 个槽里 CAS 一个空槽写入地址（都满就 `read_ref_ += 1`）；再查 `write_id_ == 0`，成立即成功；否则撤销登记、`lcond_.signal()`、失败。
- **B3 读者慢路径**（`:274-286`）：快路径失败就对内层 latch 加读锁（等写者放手）再立即解锁，然后重试；内层 latch 超时返回其错误码（`OB_TIMEOUT`）。
- **B4 读解锁**（`:288-294`）：槽里仍是本锁地址就 CAS 清空，否则（被写者扫走）`read_ref_ -= 1`；然后 `lcond_.signal()`。
- **B5 写锁**（`:183-208`）：内层 latch 加写锁（失败直接返回）；`write_id_ = itid + 1`；扫全部 8192 个槽，把等于本锁地址的 CAS 清空并计数，`read_ref_ += 计数`；当 `read_ref_ != 0` 且剩余时间 `ttl >= 0` 时 `lcond_.wait(min(ttl, 10 ms))`；`ttl < 0` → `write_id_ = 0`、解内层 latch、返回 **`OB_EAGAIN`**；否则 `write_id_ = (itid + 1) | WRITE_MASK`（`WRITE_MASK = 1 << 31`，`:56`）。
- **B6 try_wrlock**（`:231-249`）：内层 try_wrlock；登记 write_id_；扫槽；`read_ref_ != 0` 就撤销并失败。不等读者。
- **B7 写解锁**：`write_id_ = 0`，解内层 latch（`:224-229`）。
- **B8 LWaitCond**（`:29-51`）：`signal()`：ready 为 0 且 CAS(0 → 1) 成功才唤醒全部；`wait(t)`：ready 为 0 就在 futex 上等（期望 0，超时 t），否则把 ready 置 0。
- **B9 重试**：`wrlock_with_retry(abs)`：循环 `wrlock(abs)`，失败记 WARN 并睡 1 ms（`:209-222`）；`wrlock_with_retry_interval(t, i)`：循环 `wrlock(now + t)`，失败睡 i 微秒，不记日志（`:119-140`）。【译】这两个 guard 永不失败。
- **B10** `RLockGuardWithTimeout`/`WLockGuardWithTimeout` 失败时记 WARN 并通过出参返回错误码（`:63-108`）；Rust 由调用方在 `Err` 分支照写这条日志（§6 例 4）。
- **B11 大小**：C++ 的 TCRWLock 在 x86 上 192 字节、ARM 上 384 字节（`LWaitCond` 里的 ObFutex 是 `CACHE_ALIGNED`）；RULEBOOK §2.7 写的"约 40 字节"偏小（修订 A4）。Rust 版本同样把 `lcond_` 的 futex 字放在独占的缓存行上，其余字段紧凑排列。
- **B12** macOS 上 `lcond_.wait(0)` 是不限时等待（§3.9 F5），只在 `ttl` 恰为 0 时出现，照平台复刻。

### 3.6 `Quiescence` 与 `QSyncLatch<T>`

- **Q1 Quiescence**（`ob_qsync.h:32-109`）：N 个缓存行对齐的 i64。`acquire_ref()`：槽 = `itid % N`，`fetch_add(1)`，结果 ≤ 0 记 ERROR（`:45-53`）；`release_ref(idx)`：`fetch_sub(1)`，结果 < 0 记 ERROR（`:58-64`）；`sync()`：依次对每个槽忙等到 0，**不 PAUSE**（`:65-74`）；`try_sync()`：任一槽非 0 即 false（`:75-87`）。默认 N = 256（`ObQSync`，`:121`）。RULEBOOK 0.8 写的"每个 itid 一个槽"不准确，实际是 N 个槽按 itid 取模（修订 A3）。
- **Q2 语义**：`sync()` 逐槽忙等，不是快照：它检查某个槽之前进入该槽的读者，在它返回时都已离开；它检查过某槽之后才进入的读者不影响它。所以 `sync()` 开始前就已进入的读者在它返回时都已离开，即"一个宽限期"。
- **Q3 QSyncLatch**（`ob_qsync_lock.cpp`）：
  - `try_rdlock`：写标志非 0 → `OB_EAGAIN`；否则 acquire_ref，再查写标志，非 0 就 release 并 `OB_EAGAIN`（`:78-93`）；
  - `rdlock`：循环 `try_rdlock`，失败 `sched_yield`（`:34-45`）；
  - `wrlock`：CAS 写标志 0 → 1 失败就 `sched_yield` 重来；成功后最多 16 次 `try_sync`，全失败就把写标志清 0、`sched_yield`、从头再来；不设截止时间（`:52-71`）；
  - `try_wrlock`：同上，但 16 次失败就清标志并 `OB_EAGAIN`（`:95-111`）；
  - 解锁：读 release_ref，写清标志（`:47-50,73-76`）；`init`/`destroy` 什么也不做，`is_inited` 恒真（`:23-32`、`.h:33`）。
  - 读 guard 记住槽号（C++ 重新计算 `itid % 48`，同一线程结果相同）。

### 3.7 `BucketLatch`

- **K1 init**（`ob_bucket_lock.cpp:50-82`）：已 init → `OB_INIT_TWICE`；`bucket_cnt == 0` 或 latch id `>= LATCH_END` → `OB_INVALID_ARGUMENT`；**锁数 = `bucket_cnt / 8 + 1`**（`:66`）；分配失败 → `OB_ALLOCATE_MEMORY_FAILED`（Rust 用 `try_reserve`）。
- **K2 映射**：桶 = `hash % bucket_cnt`（`ob_bucket_lock.h:337-344`）；**latch = 桶 / 8**（`:331-334`）。8 个相邻的桶共用一把 latch：同一线程先后写锁同一组里的两个桶会自锁，C++ 相同。RULEBOOK §2.7 只写了"按 hash % n 取桶"（修订 A2）。【译】
- **K3 单桶操作**（`.cpp:99-173`）：未 init → `OB_NOT_INIT`；桶号越界 → `OB_INVALID_ARGUMENT`（`wrlock` 记 ERROR，其余记 WARN）；`try_*` 失败返回 `OB_EAGAIN` 不记日志；`rdlock`/`wrlock` 带截止时间。
- **K4 全部加锁**（`:217-310`）：按 latch 下标升序加锁，失败就倒序释放已拿到的；`unlock_all` 倒序释放。
- **K5 guard**（`.h:78-288`）：构造时加锁，失败记 WARN、存错误码、析构不解锁；成功则记开始时间，析构解锁后若持有超过 5 秒记 INFO（`ObClockGenerator::getClock()`，Rust 用本模块时钟，只影响日志）。`ObBucketTry*AllGuard` 失败为 `OB_EAGAIN` 时每 1000 次记一次 WARN（`REACH_COUNT_INTERVAL(1000)`）。【译】C++ 多数调用点不查 `get_ret()`（如 `ob_block_manager.cpp:634`），照样不查——令牌型、被保护的容器本身线程安全，未加锁执行不构成数据竞争。
- **K6 ObMultiBucketLockGuard**（`.cpp:319-390`）：第二次调用 `lock_multi_buckets` → `OB_INIT_TWICE`；**空数组 → `OB_INVALID_ARGUMENT`**（C++ 用 `ObHashSet::create(0)` 去重，`cal_next_prime(0) == 0` 被拒，`ob_hashtable.h:826-828`、`ob_hashutils.h:677-687`）；把 hash 换成 latch 下标、去重、升序排序、依次加锁；去重和数组的分配失败返回 `OB_ALLOCATE_MEMORY_FAILED`（Rust 用 `try_reserve`）；失败就释放已拿到的并清空数组；析构**按升序**释放（`:326-334`）。
- **K7 destroy**：只复位 `is_inited_`；数组保留到 Drop（RULEBOOK 0.5）。destroy 后再 init：桶数相同就复用数组；不同则返回 `OB_INIT_TWICE` 并记 WARN（Q4）。

### 3.8 `Monitor<T>` 与 `ObCond`

- **M1 ObThreadCond**（`ob_thread_cond.h:45-100`、`.cpp:27-77`）：`init` 两次 → `OB_INIT_TWICE`（pthread 初始化失败的 `OB_ERR_SYS` 在 Rust 不会出现）；未 init（或 destroy 之后）时 lock/unlock/wait/signal/broadcast 返回 `OB_NOT_INIT` 并记 WARN。Rust 的 `lock()` 在未 init 时仍然加锁，guard 的 `get_ret()` 返回 `OB_NOT_INIT`；`wait`/`signal`/`broadcast` 返回 `OB_NOT_INIT` 且不等待、不唤醒（`FIXED(port)`：C++ 的 guard 会不持锁执行临界区）。
- **M2 等待**：`wait(time_ms)` 即 `wait_us(time_ms * 1000)`；0 表示不限时；超时 `OB_TIMEOUT`；伪唤醒返回成功，调用方自己循环（`.cpp:79-147`）。【译】C++ 的默认实参 `wait()` 写成 `wait(0)`。
- **M3** `signal`/`broadcast` 不要求持锁。
- **M4 ObMonitor 的延迟通知**（`ob_monitor.h:73-170`）：`lock`/`trylock` 时计数清 0；`notify()` 使计数加 1（已是 −1 则不变），`notify_all()` 置 −1；在解锁之前和每次 wait 之前执行：n > 0 发 n 次 signal，−1 发一次 broadcast；wait 返回后计数清 0。
- **M5 ObMonitor 的返回值**：`timed_wait` 超时也返回 true（`Cond::timed_wait_impl` 只在非 ETIMEDOUT 的错误时置 false，`cond.h:102-125`）；负的超时记 ERROR 并返回 false（`:105-107`）；`wait()` 只在出错时返回 false。【译】`ObTransCond::wait`（`ob_trans_result.cpp:46-71`）依赖这一点：超时后靠重新计算 `left_time_us <= 0` 结束循环。
- **M6** `ObTransCond::usleep`（`ob_trans_result.cpp:86-94`）在未加锁的 monitor 上 `timed_wait`，POSIX 未定义；Rust 的 wait 只能在 guard 上调用，译成先加锁再等（`FIXED(port)`）。
- **M7 ObCond**（`ob_queue_thread.cpp:30-110`）：`signal`：CAS `bcond_` false → true 成功才加锁、signal、解锁；`wait`/`timedwait`：距上次被唤醒不到 1 ms（`BUSY_INTERVAL`）时先自旋 `spin_wait_num_` 次（默认 0）CAS true → false；否则加锁，循环直到 CAS true → false 成功，`timedwait` 超时返回 `OB_TIMEOUT`；成功后记录 `last_waked_time_`。`wait()` 不会超时。
- **M8 时钟**：Rust 的 Condvar 按单调时钟计时；C++ 的 ObThreadCond 在 Linux 上用 `gettimeofday` 算绝对时间（`.cpp:120-140`），ObCond 与 obutil::Cond 用相对或单调时间。只有系统时间跳变时行为不同（Q2）。

### 3.9 `FutexCond`、`SCondTemp` 与 futex 垫片

- **F1 SimpleCond::wait(key, timeout)**：只有 `timeout > 0` 且当前序号等于 key 时才等待；等待期间 `n_waiters_` 加 1；只有超时返回 `OB_TIMEOUT`，其余（被唤醒、值已变、中断）都是成功（`ob_scond.h:37-52`、`ob_futex.cpp:119-133`）。
- **F2 signal(limit)**：序号回绕加 1；`n_waiters_ > 0` 才唤醒最多 limit 个，返回唤醒数；limit ≥ `INT32_MAX` 按 `INT32_MAX`（`ob_scond.h:54-61`、`ob_futex.cpp:135-145`）。
- **F3 ObLightyCond**：`signal()` 序号加 1，有等待者就全部唤醒；`wait(cmp, timeout)` 只在 `timeout > 0` 时等（`ob_lighty_queue.cpp:26-41`）。与 F1、F2 合并后行为相同。
- **F4 ObFutex**：`wait(v, timeout)` 在字值等于 v 时等待，超时返回 `OB_TIMEOUT`；`wake(n)` 唤醒最多 n 个。
- **F5 平台规则**（futex 垫片照 C++ 三个平台的实现换算，`parking_lot_core::park` 的截止时间 = `Instant::now()` 加换算后的时长）：
  - Linux：相对微秒；≤ 0 时字值相等就立即超时（`ob_futex.h:49-62`）。
  - macOS：取 `timeout_us` 的低 32 位作微秒，**结果为 0 表示不限时**（`ob_futex.cpp:63-85`，`__ulock_wait` 的约定）。
  - Windows：`timeout_us / 1000` 截成 32 位毫秒，0 表示立即超时，`0xFFFFFFFF` 表示不限时（`ob_futex.h:84-100`）。
  - 唤醒返回值：Linux、macOS 返回实际唤醒数；Windows 上 n == 1 时恒返回 1、否则返回 n（`ob_futex.h:68-82`，§3.3 R8 的重复唤醒依赖它）。macOS 唤醒全部时 C++ 的返回值没有读者，Rust 返回实际数。
- **F6 SCondTemp**（`ob_scond.h:131-215`）：每 CPU 一组 PRIO 个 FutexCond；`prepare` 把（槽号 | 优先级 << 16）<< 32 | key 存进线程局部（C++ `RLOCAL`，按模板实例各一份；基线只实例化 `SCondTemp<3>`，Rust 用一个线程局部即可）；`signal(x, prio, fixed)`：先从本 CPU 的组里按优先级从高到低唤醒，剩余的加进 `n2wakeup_` 计数，再最多 8 次 trylock `SCondReadyFlag`，拿到就 `do_wakeup`。`if (loop_cnt > LOOP_LIMIT)`（`:162`）永不成立，8 次都拿不到时剩余的唤醒留在计数里等下次——照样复刻，标 `BUG(port)`。CPU 号用 03 号的 `icpu_id()`。

### 3.10 `bitlock`

- **T1 try_lock**：读字，锁位为 0 时 CAS(v → v | mask)（`ob_small_spin_lock.h:114-124`）。
- **T2 lock**（`:126-167`）：第一阶段 `cnt` 从 0 到 MaxSpin（共 MaxSpin+1 次）尝试，失败 `pause()`；第二阶段循环尝试，失败时若 `MaxSpin <= cnt`（随后 cnt 加 1）就把 cnt 置 0 并 `usleep(USleep)`，然后 `pause()`。RULEBOOK §2.7 的"PAUSE MaxSpin 次后每 MaxSpin 次 usleep"是概括，以代码为准。Windows 上 C++ 是 `Sleep(USleep / 1000)`（`:24-27`），100 µs 变成 `Sleep(0)`，Rust 在 Windows 上用 `thread::yield_now()`。
- **T3 unlock**：清锁位（C++ 读后 exchange；持有者独占时与 `fetch_and(!mask)` 等价）（`:169-175`）。
- **T4 get_data/set_data**：C++ 是普通读写（`:184-194`），与他人的 CAS 构成数据竞争；Rust 用原子读和保持锁位的 CAS 循环（`FIXED(port)`）。
- **T5 ObByteLock** = `<u8, 0, 500, 100>`（`:213`）；调用点传的 event_no 删去。
- **T6 ObPtrSpinLock**：指针第 0 位作锁位；`get_ptr` 去掉锁位；`set_ptr` 保留锁位（`:219-264`）；全部用 `AtomicPtr` 的保持来源的操作。
- **T7 ObRowLatch**：`try_lock` 是 `!swap(true)`；`lock` 纯循环，不 PAUSE、不让出、不睡眠；`unlock` 是 `store(false)`（`ob_row_latch.h:36-42`）。

### 3.11 带解锁钩子的复合锁（CtxLock）

- **H1 Drop 顺序**：`before_unlock(&mut inner)` → 释放写 latch → `after_unlock(arg)`（`ob_trans_ctx_lock.cpp:161-176`；C++ 在放锁后、after_unlock 前，持锁超过 1 秒记 WARN，由宿主在 `after_unlock` 开头照写，`lock_start_ts_` 随 Arg 带出）。
- **H2 CtxLockGuard 的模式**（`ob_trans_ctx_lock.h:102-129`、`.cpp:219-243`）：`CTX = 1`、`ACCESS = 2`、`REDO_FLUSH_X = 4`、`REDO_FLUSH_R = 8`、`ALL = 7`；加锁顺序 ACCESS → REDO_FLUSH_X → REDO_FLUSH_R → CTX；释放顺序 ACCESS → FLUSH → CTX（CTX 带钩子），所以提交回调运行时三把 latch 都已释放；从请求到释放超过 50 ms 记 WARN。【译】Rust 的 `CtxLockGuard` 在 Drop 里按这个顺序显式释放。
- **H3 CtxLock::lock(timeout_us)**（`.cpp:90-110`）：`timeout_us >= 0` 时截止时间 `now + timeout_us`，否则 `INT64_MAX`；依次写锁 access、flush、ctx，失败就倒序释放已拿到的；前后维护 `waiting_lock_cnt_`。`try_lock`（`:112-129`）同理。
- **H4 读路径**：`try_rdlock_ctx()` + `unlock_ctx()`（`ob_tx_ctx.cpp:5975-5978`）在读锁下执行 `before_unlock`，后者会写 `opid_` 等字段（`ob_trans_ctx.cpp:46-60`），多个读者并发即数据竞争。Rust 的读路径用普通 `ObLatchRGuard`，不执行钩子（`FIXED(port)`，Q3、修订 A9）。
- **H5** `need_lock = false` 的 guard（"先 `lock(5 s)` 再接管"，`ob_tx_ctx.cpp:361-363`）译成 `lock()` 直接返回 guard；条件重入见 §3.12。

### 3.12 重入

- **E1 条件重入**（锁库外 7 处：`ob_ls_meta.cpp:454,491`、`ob_tx_log_operator.h:208,420`、`ob_tx_ctx.cpp:1169`、`ob_trans_define_v4.cpp:571,933`）：外层拿到的 guard（或其 `&mut T`）作为参数向下传，内层不再加锁；C++ 的判断保留为 `debug_assert!(lock.is_wrlocked_by_self())`；无法传 guard 的路径标 `TODO(port)`，交清单第 7 类（RULEBOOK §2.7 规则 5）。只读数据的被调方收 `&T`，需要提前放锁、降级或等待条件变量的被调方收 guard 本身。
- **E2 ObRecursiveMutex**（`ob_recursive_mutex.h:52-90`：已写锁且属于本线程时只加计数，否则 wrlock；计数归 0 才解锁）：不移植，数据放进 `Latch<T>`，latch id 用 C++ 的构造实参，由最外层加锁。
- **E3 代价**：`is_wrlocked_by_self()` 是一次原子读加一次线程局部读。

### 3.13 数据放进锁的规则 1–7 如何落到本 API

| 规则（§2.7） | API |
|---|---|
| 1 只在加锁区域写的字段进 `T` | 带数据的锁的 `T`；guard 解引用访问 |
| 2 锁外也读的标量改原子、非标量进锁或 `ArcSwap` | 原子字段放在锁外；`arc-swap` 可直接用（RULEBOOK §1） |
| 3 被别的对象的锁保护的字段，移进那把锁的 `T` | 被调方收 `&mut T`（来自调用方的 guard） |
| 4 跨函数持锁 | guard 可以作返回值（生命周期绑在 `&self` 上）；或 `with_locked` 系列 |
| 5 重入 | §3.12 |
| 6 带解锁钩子的复合锁 | `UnlockHooks` + `HookedWriteGuard`（§3.11） |
| 7 临界区内的日志 | §3.14 |

### 3.14 `ObToString` 与临界区内的日志

- 带数据的锁实现 `ObToString`：try 系列拿到就打印 `T`，拿不到（含本线程自己持有）就输出 `<locked>`；绝不阻塞（RULEBOOK §2.2）。`Monitor` 用 `Mutex::try_lock`，`BigReaderLatch`/`QSyncLatch` 用读锁的 try 版本。
- 临界区内打印本对象：打印 guard 里的数据 `kpc!(*guard)`，锁外字段另列（§2.2）。打印 `self` 不会死锁，但锁内部分只会显示 `<locked>`。
- C++ `ObTxCtx::to_string` 只在 `is_locked_by_self()` 时打印受保护部分（`ob_tx_ctx.cpp:1164-1172`），Rust 按上面的规则打印，只影响日志内容。

### 3.15 `oblib::atomic`

- **A1 inc_update/dec_update**（`ob_atomic.h:97-121`）：`nv = load`；当 `nv < x`（dec 为 `nv > x`）时 CAS(nv → x)，成功 `nv = x`，失败 `nv = 当前值`，继续判断；返回 `nv` 转成 `i64`（C 的整数转换，u64 超过 `i64::MAX` 的部分按补码截断）。值不变时只读，不写缓存行（审查 F7）。用强 CAS（`compare_exchange`），不用 `fetch_max`/`fetch_min`（RULEBOOK §3 禁用）。`SCN::inc_update`（`scn.cpp:168-192`）用的是 SCN 自己的 `operator<`（无效值最小，`scn.cpp:330-358`），由 SCN 的翻译者照原函数逐行写，不用本函数。
- **A2 cas128/load128**（`atomic128.h:33-112`）：成功返回 true；失败返回 false 并把 `cmp` 改成当前值（x86 `cmpxchg16b` 写回 rdx:rax，aarch64 `expected = old`）。Rust 一律 SeqCst（不弱于 C++）。
- **A3 ObAtomicReference**（`ob_atomic_reference.cpp`）：每个方法循环读、算新值、CAS，失败 `pause()` 重试；aarch64 上用 `fetch_add(0)` 读（`:39-43` 等），x86 用 load，照抄。
  - `inc_ref_cnt`：ref 加 1 溢出到 0 → `OB_INTEGER_PRECISION_OVERFLOW` 并记 WARN；
  - `check_seq_num_and_inc_ref_cnt(seq)`：溢出同上；seq 不等或 ref 为 0 → `OB_EAGAIN`（不记日志）；
  - `check_and_inc_ref_cnt`：溢出同上；ref 为 0 → `OB_EAGAIN`；
  - `dec_ref_cnt_and_inc_seq_num(&mut ref_cnt)`：ref 为 0 → `OB_ERR_UNEXPECTED` 并记 WARN；ref 减到 0 时 seq 加 1；成功后输出新 ref；
  - `reset`：只清 ref，C++ 是对半个字的普通写（`:31-35`），Rust 用 `fetch_and` 清低 32 位（`FIXED(port)`）。
- **A4** `ATOMIC_*` 的逐个对照见 §6.3。

### 3.16 与 C++ 不同的地方

| 项 | 内容 | 依据 |
|---|---|---|
| 写者 id | 锁字记 `itid + 1`，C++ 记 OS tid；只影响日志里的锁字数值，并消除了 macOS 上 tid ≥ 2^30 时 C++ 返回 `OB_INVALID_ARGUMENT` 的隐患 | RULEBOOK 0.8；修订 A1 |
| 解锁线程 | guard 只能由加锁线程释放；去掉 puid 参数 | RULEBOOK §2.7 规则 4；§3.1 G8 |
| 不可能的错误 | 不可失败的接口遇到非法 latch id、读者数溢出时 `ob_abort` | `FIXED(port)`，§3.2 L7 |
| 未 init 的 Monitor | 仍然加锁，`get_ret()` 报 `OB_NOT_INIT` | `FIXED(port)`，§3.8 M1 |
| `ObTransCond::usleep` | 先加锁再等 | `FIXED(port)`，§3.8 M6 |
| bitlock 的数据位、`ObAtomicReference::reset` | 原子操作 | `FIXED(port)`，§3.10 T4、§3.15 A3 |
| CtxLock 读锁解锁 | 不执行钩子 | `FIXED(port)`，§3.11 H4 |
| `SCondTemp::signal` 的死分支 | 照样复刻 | `BUG(port)`，§3.9 F6 |
| 等待事件、持锁登记 | 不移植（无读者） | §3.1 G10 |
| BucketLatch guard 的 5 秒日志 | 用本模块时钟代替 `ObClockGenerator` | 只影响日志 |
| Monitor 的计时 | 单调时钟 | Q2 |
| `BucketLatch::destroy` | 不释放数组 | RULEBOOK 0.5；Q4 |

## 4. unsafe

全部在 `rust/port/oblib/src/sync/**`（§3 白名单第 2 项）；模块头 `#![allow(unsafe_code)]`、`#![deny(unsafe_op_in_unsafe_fn)]`，每处写 `// SAFETY:`。`oblib::atomic` 不含 unsafe（不在白名单内）。对外 API 全部安全。估计约 30 处，实现后实测写进 RULEBOOK §3。

| # | 位置 | 内容 | 安全论证要点 |
|---|---|---|---|
| U1 | 各带数据的锁的 guard | 从 `UnsafeCell<T>` 取 `&T`/`&mut T` | 写 guard 存在时锁字的 WRITE 位为 1、无读者，独占；读 guard 存在时 WRITE 位为 0 且读者数 ≥ 1，只给 `&T`。guard 生命周期绑在 `&'a Lock` 上，`!Send`，由加锁线程释放。`ShardedRwLatch`：写者持有全部分片、读者至少持一片，互斥。`BigReaderLatch`：写者设 `write_id_` 后清点并等 `read_ref_` 归零；读者在登记后复查 `write_id_`（两边都是 SeqCst，Dekker 式握手）。`QSyncLatch`：写标志与槽计数同理。`ObByteLock`：锁位 |
| U2 | 带数据的锁类型 | `unsafe impl Send/Sync` | 条件与 std 的 `Mutex`/`RwLock` 相同（`T: Send`，读写锁另需 `T: Sync`）；guard 不加任何 unsafe impl（RULEBOOK §3） |
| U3 | futex 垫片 | 调用 `parking_lot_core::park`/`unpark_one`/`unpark_filter`/`unpark_all`（`unsafe fn`） | key 是字的地址，parking_lot_core 只把它当整数用、不解引用；validate 回调只做一次原子读和比较，before_sleep、timed_out 回调为空；回调都不 panic、不再调 park 系列 |
| U4 | 等待队列 | 桶链表里的 `ObWaitProc` 是等待线程栈上的节点，用裸指针链接 | (1) 节点在 `ObLatchWaitQueue::wait` 返回前一直在栈上不动（`PhantomPinned`）；(2) 链表只在持有该桶 ObLatchMutex 时读写；(3) `wait` 返回前节点一定已摘下：要么唤醒者在桶锁内摘下后才置 `wait_ = 0`，要么等待者自己在桶锁内看到 `wait_ == 1` 后摘下（`ob_latch.cpp:295-321`）；(4) 唤醒者在 `wait_.store(0)` 之后不再访问节点，之后的 unpark 只用事先算好的地址整数（`:404-411`）；陈旧地址最多造成一次伪唤醒，等待循环本来就处理 |
| U5 | 等待队列的桶 | `UnsafeCell<WaitList>` 与 `unsafe impl Sync for ObLatchBucket` | 只在持有桶锁时访问 |
| U6 | 无 | `Monitor`（std `Mutex`/`Condvar`）、`Quiescence`、`BucketLatch`、`FutexCond` 的计数部分、TCRef 槽表（存地址整数）、`ObPtrSpinLock`（`AtomicPtr` 的保持来源的操作）、`ObRowLatch`、`ObSmallSpinLock` | 都是安全代码 |
| U7 | `pause()` | 无 unsafe；`asm!` 在 03 号的 `oblib::arch` | — |
| U8 | 仅测试 | `cfg(loom)` 下 loom 的 `UnsafeCell` 指针解引用 | 只在 loom 测试构建中出现 |

不使用 `ManuallyDrop`、`mem::forget`：`wr2rdlock` 和 `HookedWriteGuard` 用 `Option` 字段 `take()` 实现转移。

## 5. 测试

位置：单测在各源文件的 `#[cfg(test)]`；loom 测试在 `rust/port/oblib/tests/loom_sync.rs`（`RUSTFLAGS="--cfg loom"`）；金标准测试在 `rust/port/oblib/tests/golden_sync.rs`，golden 文件放 `rust/port/oblib/tests/golden/sync/`。

### 5.1 单元测试（真实线程）

- [ ] T1 latch 表：生成的 `OB_LATCHES` 有 204 行；FIFO 180、READ_PREFER 24；自旋值分布同 §3.1 G4；`ObLatchIds::ROW_LOCK.0 == 11` 而 `OB_LATCHES[11].latch_id_ == 24`；`LATCH_END.0 == 203`。
- [ ] T2 错误码：`lock_until(0)`、`lock_until(-1)` → `OB_INVALID_ARGUMENT`；他人持锁时 `lock_until(过去的时间)` → `OB_TIMEOUT`，且返回前自旋了表中的次数（测试构建里给 `pause()` 计数）；`trylock` 在只有 WAIT 位时失败。
- [ ] T3 `RwLatch`：读读并发；读写、写写互斥；FIFO latch 在有写者等待时 `try_rdlock` 返回 `OB_EAGAIN`，READ_PREFER latch 成功；`wr2rdlock` 后其他读者能进、写者不能；`is_wrlocked_by_self` 只在持写锁的线程上为真。
- [ ] T4 READ_PREFER 排队：构造"写者 W1 等待、读者 R1 R2 随后等待"，持锁者放锁后 R1、R2 先于 W1 获得锁；FIFO latch 下 W1 先。
- [ ] T5 `ShardedRwLatch`：写锁获取全部分片；`wrlock_until` 中途超时后已拿到的分片全部释放（检查每片锁字为 0）。
- [ ] T6 `BigReaderLatch`：写锁等读者超时返回 `OB_EAGAIN` 且 `write_id_` 复位、内层 latch 已释放；拿内层 latch 超时返回 `OB_TIMEOUT`；读者槽满 8 个后走 `read_ref_` 计数，仍能正确释放。
- [ ] T7 `QSyncLatch`：写者在读者长时间持有时不断让出、不超时；`try_wrlock` 16 次 try_sync 失败后写标志复位。
- [ ] T8 `BucketLatch`：`latch_cnt == n / 8 + 1`；桶 0 与桶 7 共用 latch（写锁桶 0 后 `try_wrlock(7)` 失败）；`lock_multi_buckets(&[])` → `OB_INVALID_ARGUMENT`；调用两次 → `OB_INIT_TWICE`；未 init → `OB_NOT_INIT`；越界 → `OB_INVALID_ARGUMENT`。
- [ ] T9 `Monitor`：未 init 时 `wait` 立即返回 `OB_NOT_INIT`；`wait_us(1000)` 超时 `OB_TIMEOUT`；`timed_wait` 超时返回 true，负值返回 false；`notify` 在解锁时才生效（另一线程在解锁前不会醒）。
- [ ] T10 `FutexCond`：`wait(key, 0)` 立即成功；序号已变时立即返回；`signal` 在没有等待者时不进系统调用（测试构建计数）。
- [ ] T11 bitlock：`ObByteLock` 第一次 usleep 发生在第 MaxSpin+2 次尝试（测试构建计数）；`ObRowLatch` 不调用 `pause()`；`ObPtrSpinLock` 加锁后 `get_ptr` 不含锁位、`set_ptr` 保留锁位。
- [ ] T12 `inc_update`/`dec_update`：返回值；`x` 不大于当前值时不执行 CAS（用计数包装类型实现 `ObAtomicInt` 断言）；`AtomicU64` 存 `u64::MAX` 时返回 −1。
- [ ] T13 `ObAtomicReference`：§3.15 A3 的每个错误码。
- [ ] T14 布局：`size_of`/`align_of`：`CacheAligned<u8>` 为 `CACHE_ALIGN_SIZE`；`Latch<()>` 按 `CACHE_ALIGN_SIZE` 对齐；`raw::ObLatch` 4 字节；`ObRowLatch`、`ObByteLock<()>` 1 字节；`ObCond` 64 字节对齐。
- [ ] T15 `HookedWriteGuard`：before_unlock 时 `is_wrlocked_by_self()` 为真；after_unlock 时为假，且另一线程此刻能拿到写锁。
- [ ] T16 压力：每种锁 8 线程 × 10^6 次加解锁，临界区内用非原子计数检查互斥（release 构建，CI 每日跑）。

### 5.2 loom 测试

loom 下的替换（实现者在 `sync` 内部用 `cfg(loom)` 垫片，公开 API 不变）：

- 原子量、`UnsafeCell` → loom 的同名类型；`const fn new` 在 `cfg(loom)` 下改为普通 `fn`；静态单例用 `loom::lazy_static!`。
- futex 垫片 → 模型：一把 `loom::sync::Mutex` 保护的"地址 → 等待者"表加 `loom::sync::Condvar`；wait 在锁内比对字值，wake 返回实际摘下的等待者数。
- 时钟 → 测试控制的伪时钟；带超时的等待在伪时钟已过截止时间时立即返回 `TimedOut`。
- itid → `loom::thread_local!`；`pause()`、`sched_yield`、`usleep` → `loom::thread::yield_now()`。
- 规模缩小：自旋次数上限 1–2（`INT64_MAX` 的 latch 保持"永不睡眠"但每轮让出）；等待队列 2 个桶（强制不同 latch 共桶）；`SYNC_MAX_CPU_NUM` 2；TCRef 2 × 2 槽；`Quiescence` 默认 2 槽、`QSyncLatch` 2 槽。

清单（每项 2–3 个线程）：

- [ ] LM1 `Latch`：互斥计数；持锁者放锁时有等待者必被唤醒（无丢失唤醒）；超时的等待者留下 WAIT 位后，下一次解锁的伪唤醒不破坏不变量。
- [ ] LM2 `RwLatch`：读写互斥；最后一个读者放锁唤醒写者；写者放锁唤醒队首连续读者；`wr2rdlock` 只唤醒读者；两把 latch 共用一个桶时互不丢唤醒；"被唤醒时还没睡下"的等待者触发 R8 的重复唤醒；超时清理在无其他等待者时清掉 WAIT 位。
- [ ] LM3 `RwLatch` 的 FIFO 与 READ_PREFER 各一组。
- [ ] LM4 `ShardedRwLatch`：2 分片下写者与两个不同分片的读者互斥；`try_wrlock` 中途失败的回滚。
- [ ] LM5 `BigReaderLatch`：读者快路径与写者扫槽的握手（读者要么被扫到、要么看到 `write_id_` 后退出）；被扫走槽的读者解锁时扣减 `read_ref_`；写者超时路径；`LWaitCond` 无丢失唤醒。
- [ ] LM6 `QSyncLatch`：读写互斥；写者 16 次失败后释放写标志、读者能继续。
- [ ] LM7 `Quiescence`：线程 A `enter` 后读一个 loom `UnsafeCell`，线程 B 换掉它再 `sync()` 后写旧值；loom 不得报数据竞争。
- [ ] LM8 `BucketLatch`：两个线程以不同顺序给出重叠的 hash 集合调用 `lock_multi_buckets`，无死锁。
- [ ] LM9 `Monitor`：条件循环加 wait 与 signal/broadcast 无丢失唤醒；延迟通知在解锁时送达。
- [ ] LM10 `FutexCond`：`get_key → 检查条件 → wait(key)` 对 `设置条件 → signal` 无丢失唤醒；`n_waiters_ == 0` 时跳过唤醒的路径安全。
- [ ] LM11 `SCondTemp<2>`、`ObCond`：同 LM10。
- [ ] LM12 bitlock：`ObRowLatch`、`ObByteLock`、`ObPtrSpinLock` 互斥；`set_data` 不丢锁位。
- [ ] LM13 `HookedWriteGuard`：after_unlock 去拿另一把锁 L2，第三个线程持 L2 并等本 latch，不死锁。
- [ ] LM14 `inc_update`/`dec_update`：并发更新后结果是最大（最小）值，各线程的返回值不小于自己的 x（inc）。
- [ ] LM15 `ObAtomicReference`：并发 inc/dec 后 ref 与 seq 一致；ref 归零时 seq 恰好加 1。

### 5.3 Miri（单线程部分；`-Zmiri-tree-borrows`）

- [ ] MI1 各带数据的锁：加锁、解引用、降级、Drop 顺序，检查没有别名违规。
- [ ] MI2 等待队列链表：用栈上节点做入队头、入队尾、摘除、按序唤醒的纯函数测试。
- [ ] MI3 `ObPtrSpinLock`：`-Zmiri-strict-provenance` 下加解锁、`get_ptr`/`set_ptr` 后解引用。
- [ ] MI4 `HookedWriteGuard`、`ObThreadCondGuard` 的 `Option` 转移路径。
- [ ] MI5 `BucketLatch` 的 init、destroy、再 init。
- [ ] MI6 `uint128_t` 与 `u128` 的互转，`cas128` 失败时回写 cmp。

### 5.4 以 C++ 为准的金标准测试

1d 先用从 C++ 源码推出的期望值写断言（每条断言注明 file:line）；5b 用 C++ 程序产出 golden 文件复核，不一致就是 Rust 实现或本文的缺陷。

- **产出方式**：5b 写一个链接基线 oblib 的 C++ 小程序 `migration/infra/golden/sync_golden.cpp`（单线程脚本），输出 TSV `case\tstep\top\targs\tret\tvalue`；Rust 测试按同一脚本执行并逐行比较。
- [ ] G1 latch 表：C++ 输出 `OB_LATCHES[i]` 的下标、`latch_id_`、名字、策略、自旋、让出，与生成表逐字节比较。
- [ ] G2 单线程脚本的返回码：每种锁的 try/lock/unlock/降级/超时序列（如 `try_wrlock` 两次得 0、−4023；无持有者时 `wr2rdlock` 得 −4016；`ObThreadCond` 未 init 时 `wait` 得 −4006；`ObBucketLock` init(0) 得 −4002、init 两次得 −4005）。
- [ ] G3 `inc_update`/`dec_update` 对 i32、i64、u64（含大于 `INT64_MAX` 的值）的返回值。
- [ ] G4 `ObAtomicReference` 的操作序列与 ref/seq。
- [ ] G5 `ObMonitor::timed_wait` 超时返回 true；`ObCond::timedwait` 超时 −4012。
- [ ] G6 `ObBucketLock` 的 `latch_cnt` 与一组 hash 的桶号、latch 号。

### 5.5 静态检查

- [ ] S1 CI 用 grep 校验：`unsafe` 只出现在 `oblib/src/sync/**`，每处有 `// SAFETY:`。
- [ ] S2 防止把 guard 绑定到 `_`（`let _ = x.lock();` 会立即释放）：CI 用 grep 查 `let _ = .*\.(lock|rdlock|wrlock|enter|trylock|try_rdlock|try_wrlock)\(` 与 `let _ = Ob\w*Guard::new(`，命中即失败；不用 `clippy::let_underscore_must_use`，它会误伤 §2.13 的 `(void)f()` → `let _ = f();`（修订 A7）。

## 6. 翻译者怎么用

### 6.1 类型对照

| C++ | Rust 类型 | 构造（默认 latch id 取自 C++ 声明） |
|---|---|---|
| `ObSpinLock` | `Latch<T>` | `Latch::new(ObLatchIds::DEFAULT_SPIN_LOCK, …)`（`ob_spin_lock.h:38`） |
| `lib::ObMutex` | `Latch<T>` | `DEFAULT_MUTEX`（`ob_mutex.h:31`） |
| `ObLatchMutex` | `Latch<T>` | 调用点传的 latch id |
| `std::mutex`、单独的 `pthread_mutex_t` | `Latch<T>` | `DEFAULT_MUTEX`（C++ 没有 latch id） |
| `SpinRWLock` | `RwLatch<T>` | `DEFAULT_SPIN_RWLOCK`（`ob_spin_rwlock.h:35`） |
| `ObLatch` | `RwLatch<T>` | 调用点传的 latch id（§3.1 G3） |
| `obsys::ObRWLock` | `RwLatch<T>` | `DEFAULT_SPIN_RWLOCK` |
| `DRWLock` | `ShardedRwLatch<T>` | `DEFAULT_DRW_LOCK`（`ob_drw_lock.h:31`） |
| `TCRWLock`、`common::RWLock` | `BigReaderLatch<T>` | `DEFAULT_SPIN_RWLOCK`（`ob_tc_rwlock.h:143`） |
| `ObQSyncLock` | `QSyncLatch<T>` | `QSyncLatch::new(…)` |
| `ObQSync`、`ObQSyncWrapper<N>` | `Quiescence`、`Quiescence<N>` | `Quiescence::new()`（函数内 `static ObQSync` 译成 `static`） |
| `ObBucketLock` | `BucketLatch` | `BucketLatch::new()` + `init(n, ObLatchIds::DEFAULT_BUCKET_LOCK)` |
| `ObRecursiveMutex` | `Latch<T>` | C++ 构造实参（§3.12） |
| `ObThreadCond` | `Monitor<T>` | `Monitor::new(…)` + `init(event_no)` |
| `ObMonitor<Mutex>`、pthread/std 的 mutex+cond | `Monitor<T>` | `Monitor::new_inited(…)` |
| `ObCond` | `ObCond` | `ObCond::new(ObCond::SPIN_WAIT_NUM)` |
| `SimpleCond`、`ObLightyCond`、`ObFutex` | `FutexCond` | `FutexCond::new()` / `new_with_event(e)` |
| `ObByteLock` | `bitlock::ObByteLock<T>` | `ObByteLock::new(…)` |
| `ObPtrSpinLock<T>`、`ObRowLatch` | `bitlock::` 同名 | `new()` |
| `CACHE_ALIGNED` 成员 | `CacheAligned<T>` | `CacheAligned::new(…)` |

### 6.2 写法对照（摘要）

| C++ | Rust |
|---|---|
| `XxxGuard guard(lock_);` | `let mut guard = self.lock_.lock();`（或 `rdlock()`/`wrlock()`）；**绑定到具名变量，不得写 `let _ =`** |
| `if (OB_FAIL(guard.get_ret()))` | `if ob_fail!(ret = guard.get_ret())`（恒为假，照写） |
| `if (OB_FAIL(lock_.lock())) {…} else {…; lock_.unlock();}` | `match self.lock_.lock_until(INT64_MAX) { Err(e) => …, Ok(guard) => … }` |
| `ObSpinLock::lock(timeout_us)` | `lock_until(ObTimeUtility::current_time() + timeout_us)` |
| `lock_.trylock()` / `try_rdlock()` 返回 bool 的 | `self.lock_.trylock()` 返回 `ObResult<guard>`；`if let Ok(g) = …` |
| `int locked = try_wrlock(); … if (locked) unlock();` | `let locked = self.lock_.try_wrlock().ok();` … `drop(locked);` |
| 手工 `lock()` 与 `unlock()` 在两个函数里 | 返回 guard，或 `with_locked`（规则 4） |
| `SpinRLockManualGuard g; if (c) g.lock(l);` | `let mut g = None; if c { g = Some(l.rdlock()); }` |
| `ObByteLockGuard g(l, event_no);`、`ObSmallSpinLockGuard<ObByteLock> g(l);` | `let g = self.l.lock();`（event_no 删去） |
| `ObRowLatchGuard g(row.latch_);`（storage_core 内） | `let g = ObRowLatchGuard::new(&row.latch_);` |
| `CriticalGuard(qs);` / `WaitQuiescent(qs);` | `let _critical_guard = qs.enter();` / `qs.sync();` |
| `cond_.wait();`（ObThreadCond，在 guard 内） | `guard.wait(0)` |
| `monitor_.timed_wait(ObSysTime(us))` | `guard.timed_wait(us)` |
| `monitor_.notify_all();`（ObMonitor，在 guard 内） | `guard.notify_all();` |
| `lock_.enable_record_stat(false);` | 删去（§3.1 G10） |
| `lock_.is_wrlocked_by()` 决定是否加锁 | 传 guard，`debug_assert!(… .is_wrlocked_by_self())`（§3.12） |
| 临界区内 `KPC(this)` | `kpc!(*guard)`，锁外字段另列 |

### 6.3 `ATOMIC_*` 与屏障对照（RULEBOOK §2.7 原子量表的展开）

| C++（`ob_atomic.h`） | Rust（字段类型改为 `Atomic*`） |
|---|---|
| `ATOMIC_LOAD(&x)` / `_ACQ` / `_RLX` | `x.load(SeqCst)` / `Acquire` / `Relaxed` |
| `ATOMIC_STORE(&x, v)` / `_REL` / `_RLX` | `x.store(v, SeqCst)` / `Release` / `Relaxed` |
| `ATOMIC_SET(&x, v)`、`ATOMIC_TAS(&x, v)`（都是交换，返回旧值，`:89-90`） | `x.swap(v, SeqCst)` |
| `ATOMIC_FAA(&x, v)` / `ATOMIC_FAS(&x, v)` | `x.fetch_add(v, SeqCst)` / `x.fetch_sub(v, SeqCst)` |
| `ATOMIC_AAF(&x, v)` / `ATOMIC_SAF(&x, v)` | `x.fetch_add(v, SeqCst).wrapping_add(v)` / `x.fetch_sub(v, SeqCst).wrapping_sub(v)` |
| `ATOMIC_INC(&x)` / `ATOMIC_DEC(&x)` | `x.fetch_add(1, SeqCst);` / `x.fetch_sub(1, SeqCst);` |
| `ATOMIC_BCAS(&x, c, n)` | `x.compare_exchange(c, n, SeqCst, SeqCst).is_ok()` |
| `ATOMIC_VCAS(&x, c, n)`、`ATOMIC_CAS`（返回旧值，`:123`） | `x.compare_exchange(c, n, SeqCst, SeqCst).unwrap_or_else(\|v\| v)` |
| `ATOMIC_ANDF(&x, v)`（返回新值） | `x.fetch_and(v, SeqCst) & v` |
| `*_AR`、`ATOMIC_CMP_AND_EXCHANGE`（基线 0 处） | `AcqRel`/`Acquire` 的同名操作 |
| `ATOMIC_LOAD64`/`ATOMIC_STORE64` 作用在 8 字节结构上（如 `ob_ema_v2.h:41` 的 double） | 字段改 `AtomicU64`，读写经 `to_bits`/`from_bits`；作用在 int64 上的就是普通 `load`/`store` |
| `__sync_*`（36 处） | 同上表，`SeqCst` |
| `std::atomic` 加显式 `memory_order` | 同名 ordering（`consume` 写 `Acquire`） |
| `volatile` 标志或计数 | 原子量；普通读 `Acquire`、普通写 `Release`，清单第 8 类标为纯计数、统计、停止标志的用 `Relaxed` |
| `MEM_BARRIER()` / `WEAK_BARRIER()` / `__COMPILER_BARRIER()` | `fence(SeqCst)` / `fence(AcqRel)` / `compiler_fence(SeqCst)` |
| `PAUSE()` | `oblib::sync::pause()` |
| `inc_update(&x, v)` / `dec_update(&x, v)` | `oblib::atomic::inc_update(&x, v)` / `dec_update(&x, v)` |
| `CAS128(&a, cmp, with)` / `LOAD128(dest, &a)` | `cas128(&a, &mut cmp, with)` / `dest = load128(&a)` |

### 6.4 实例（取自基线）

**例 1　ObSpinLockGuard，数据进锁**（`ob_trans_define_v4.h:286,382-383`，构造 `ob_trans_define_v4.cpp:403`）

```cpp
mutable ObSpinLock lock_;                       // lock_(common::ObLatchIds::TX_DESC_LOCK)
void reset_conflict_txs()
{ ObSpinLockGuard guard(lock_); conflict_txs_.reset(); }
```
```rust
// 结构体名示意，分组由清单第 7 类给出、写进 types.tsv
pub lock_: Latch<ObTxDescLocked>,               // Latch::new(ObLatchIds::TX_DESC_LOCK, ObTxDescLocked::default())
pub fn reset_conflict_txs(&self) {
    let mut guard = self.lock_.lock();
    guard.conflict_txs_.reset();
}
```

**例 2　截止时间加锁，用户可见的 4012**（`ob_local_ddl_serial_call.h:29-47`）

```rust
pub fn local_ddl_serial_mutex() -> &'static Latch<()> {
    static MUTEX: Latch<()> = Latch::new(ObLatchIds::DEFAULT_MUTEX, ());
    &MUTEX
}
pub fn local_ddl_serial_call<F: FnOnce() -> ObResult>(f: F) -> ObResult {
    let ret: ObResult;
    let abs_timeout_us = this_worker_timeout_ts();        // THIS_WORKER.get_timeout_ts()，名字由 03 号定
    match local_ddl_serial_mutex().lock_until(abs_timeout_us) {
        Err(e) => { ret = Err(e); }                        // OB_TIMEOUT 原样上抛
        Ok(_guard) => { ret = f(); }                       // _guard 在分支末尾释放，对应 struct Unlock
    }
    ret
}
```

**例 3　try_wrlock 加标志位**（`ob_id_service.cpp:44-65`）

```rust
pub fn submit_log_with_lock_(&self, last_id: i64, limited_id: i64) -> ObResult {
    let mut ret: ObResult = Ok(());
    let mut locked = self.rwlock_.try_wrlock().ok();      // int locked = rwlock_.try_wrlock()
    if locked.is_none() {
        ret = Err(OB_EAGAIN);
    } else if ob_fail!(ret = self.submit_log_(locked.as_deref_mut().unwrap(), last_id, limited_id)) {
        // …日志照抄…
    } else {
        // …
    }
    drop(locked);                                          // if (locked) rwlock_.unlock();
    ret
}
```

**例 4　TCRWLock 带超时的读 guard**（`ob_obj_lock.cpp:296-303`）

```rust
match self.rwlock_.rdlock_until(abs_timeout_us) {          // RDLockGuardWithTimeout guard(rwlock_, abs_timeout_us, ret)
    Err(e) => {
        ret = Err(e);
        log_warn!(ret, "RLockGuardWithTimeout failed", k!(abs_timeout_us)); // guard 内的日志，ob_tc_rwlock.h:67-69
        if ret.is(OB_TIMEOUT) { ret = Err(OB_EAGAIN); }
    }
    Ok(guard) => { timeguard.click("rlock"); /* …用 guard… */ }
}
```

**例 5　永不失败的重试写锁**（`ob_trans_ctx_mgr_v4.cpp:192`）

```rust
let mut guard = self.rwlock_.wrlock_with_retry_interval(TRY_THRESOLD_US, RETRY_INTERVAL_US);
```

**例 6　DRWLock 读后写的双重检查**（`ob_kvcache_inst_map.cpp:182-196`）

```rust
{
    let rd_guard = self.lock_.rdlock();                    // DRWLock::RDLockGuard rd_guard(lock_)
    if ob_succ!(ret = rd_guard.inst_map_.get_refactored(inst_key, &mut inst)) { self.add_inst_ref(inst); }
}
if ret.is(OB_HASH_NOT_EXIST) {
    let mut wr_guard = self.lock_.wrlock();                // DRWLock::WRLockGuard wr_guard(lock_)
    // …二次检查与插入照抄…
}
```

**例 7　桶锁下的读改写**（`ob_block_manager.cpp:634-651`）

```rust
let lock_guard = ObBucketHashWLockGuard::new(&self.bucket_lock_, macro_id.hash()); // C++ 不查 get_ret，照抄
if ob_fail!(ret = self.block_map_.get(macro_id, &mut block_info)) {
    // …
}
// …ref_cnt_ 加 1，insert_or_update…；lock_guard 在作用域末尾释放
```

**例 8　多桶加锁**（`ob_ls_tablet_service.cpp:311-312`）

```rust
let mut lock_guard = ObMultiBucketLockGuard::new(&self.bucket_lock_, true /*is_write_lock*/);
if ob_fail!(ret = lock_guard.lock_multi_buckets(&all_tablet_id_hash_array)) {
} else {
    // …
}
```

**例 9　ObThreadCond 的等待循环**（`ob_reentrant_thread.cpp:108-121`）

```rust
let mut guard = self.cond_.lock();                         // ObThreadCondGuard guard(cond_)
if guard.running_cnt_ < 0 {
    ret = Err(OB_INNER_STAT_ERROR);
} else {
    while guard.running_cnt_ > 0 {
        let _ = guard.wait(0);                             // cond_.wait()，默认 time_ms = 0
    }
}
```

**例 10　SimpleCond**（`ob_storage_log_writer.cpp:705,750-768`）

```rust
pub fn notify(&self) { let _ = self.wakeup_cond_.signal(u32::MAX); }   // signal(UINT32_MAX)
// run()
while !self.has_set_stop() {
    let key = self.wakeup_cond_.get_key();
    let mut log_writer = None;
    {
        let mut guard = self.writer_mutex_.lock();         // std::lock_guard<std::mutex>；writer_mutex_: Latch<…>(DEFAULT_MUTEX)
        // …挑选 log_writer，flush_log_once…
    }
    if log_writer.is_none() && !self.has_set_stop() {
        let _ = self.wakeup_cond_.wait(key, FLUSH_THREAD_IDLE_INTERVAL_US);
    }
}
```
附注：`ob_ex_rpc.h:67-68` 在 `std::atomic<int> done_` 上直接用 futex，译成 `done_: FutexCond`：`mark_done` 写 `self.done_.val().store(1, Release); self.done_.futex_wake(1);`，`wait` 写 `while self.done_.val().load(Acquire) == 0 { let _ = self.done_.futex_wait(0, i64::MAX); }`。

**例 11　ObQSync 当静默屏障**（`ob_lock_wait_mgr.cpp:299-333`、`ob_lock_wait_mgr.h:300-303`）

```rust
fn get_qs() -> &'static Quiescence { static QS: Quiescence = Quiescence::new(); &QS }
let mut tmp_node = None;
{
    let _critical_guard = Self::get_qs().enter();          // CriticalGuard(get_qs())
    // …插入、二次检查、异常分支照抄…
}
if tmp_node.is_some() {
    Self::get_qs().sync();                                 // WaitQuiescent(get_qs())
}
```

**例 12　CtxLock：钩子、模式位、接管**（`ob_trans_ctx_lock.{h,cpp}`、`ob_trans_ctx.cpp:46-90`、`ob_tx_ctx.cpp:361-363,587-596`）

```rust
impl UnlockHooks for ObTransCtx {
    type Inner = TransCtxLocked;                           // 示意：ctx latch 保护的字段（清单第 7 类）
    type Arg = (CtxLockArg, i64 /*lock_start_ts*/);
    fn hooked_latch(&self) -> &RwLatch<TransCtxLocked> { &self.lock_.ctx_lock_ }
    fn before_unlock(&self, inner: &mut TransCtxLocked) -> Self::Arg { /* ob_trans_ctx.cpp:46-60 逐行 */ todo!() }
    fn after_unlock(&self, arg: Self::Arg) { /* 先照 ob_trans_ctx_lock.cpp:166-172 记 1 秒告警，再 :62-90 逐行 */ }
}
pub struct CtxLockGuard<'a> {                              // Drop 里按 ACCESS → FLUSH → CTX 显式释放
    access_: Option<ObLatchWGuard<'a, ()>>,
    flush_redo_: Option<FlushRedoGuard<'a>>,               // 示意：读或写两种 guard 的 enum
    ctx_: Option<HookedWriteGuard<'a, ObTransCtx>>,
    request_ts_: i64,
    hold_ts_: i64,
}
// ob_tx_ctx.cpp:361-363：if (OB_SUCC(lock_.lock(5000000))) { CtxLockGuard guard(lock_, false); … }
match self.lock_.lock(5_000_000 /*5 seconds*/) {           // CtxLock::lock 直接返回 ALL 模式的 guard
    Ok(guard) => { /* … */ }
    Err(e) => { ret = Err(e); }
}
// ob_tx_ctx.cpp:587-596：CtxLockGuard guard(lock_); … TRANS_LOG(WARN, …, KPC(this))
let mut guard = CtxLockGuard::new(&self.lock_, CtxLockGuard::ALL);
let ctx = guard.ctx_mut();                                 // 示意：取 ctx latch 内的数据（&mut TransCtxLocked）
// …
log_warn!(ret, "invalid argument", k!(expire_ts), kpc!(*ctx)); // 打印 guard 里的数据，不打印 self
```

**例 13　条件重入改为传 guard**（`ob_tx_log_operator.h:207-213`）

```rust
// C++：CtxLockGuard guard; if (!tx_ctx_->lock_.is_locked_by_self()) { tx_ctx_->get_ctx_guard(guard, CTX); }
// Rust：持锁的调用链把 ctx 的锁内数据传下来，本函数不再加锁
fn init_log_block_(&mut self, ctx: &mut TransCtxLocked) -> ObResult {
    debug_assert!(self.tx_ctx_.lock_.is_locked_by_self());
    // …
    Ok(())
}
// 无法从调用方拿到 guard 的路径：TODO(port): 条件重入，清单第 7 类处理
```

**例 14　inc_update**（`ob_memtable.cpp:1928-1945`）

```rust
pub fn set_max_data_schema_version(&self, schema_version: i64) {
    if i64::MAX == schema_version {
        log_error!(OB_INVALID_ARGUMENT, "invalid schema version", k!(schema_version), kpc!(self));
    } else {
        let _ = inc_update(&self.max_data_schema_version_, schema_version);
    }
}
pub fn get_max_data_schema_version(&self) -> i64 { self.max_data_schema_version_.load(SeqCst) }
```

**例 15　ObByteLock 保护一个计数**（`ob_freezer.cpp:1510-1525`、`ob_freezer.h:361-362`）

```rust
// byte_lock_: ObByteLock<i64>，原 pend_replay_cnt_ 进锁
pub fn pend_ls_replay(&self) -> ObResult {
    let mut ret: ObResult = Ok(());
    let mut pend_replay_cnt_ = self.byte_lock_.lock();     // ObByteLockGuard guard(byte_lock_, LS_REPLAY_CTRL_LOCK)
    if !self.is_inited_.load(Acquire) {
        ret = Err(OB_NOT_INIT);
        log_warn!(ret, "[Freezer] not inited", kp!(self));
    } else if *pend_replay_cnt_ < 0 {
        log_error!(ret, "[Freezer] invalid pend_replay_cnt", kp!(self), k!(*pend_replay_cnt_));
    } else if false_it!(*pend_replay_cnt_ += 1) {
    } else if 1 == *pend_replay_cnt_ {
        // …失败时 *pend_replay_cnt_ -= 1…
    }
    ret
}
```

## 7. 依赖

- **白名单 crate**（RULEBOOK §1）：
  - `parking_lot_core`：只在 `oblib::sync` 的 futex 垫片里；
  - `portable-atomic`：只在 `oblib::atomic`（`AtomicU128`）；
  - `loom`：只作 dev-dependency、只在 `cfg(loom)` 下。白名单里还没有，见修订 A5；
  - 不用 `crossbeam-utils`（`CachePadded` 大小不符，RULEBOOK §2.7）、不用 `arc-swap`。
- **其他 1d 模块**：
  - 03 号 `thread-signal`：`get_itid()`（外部线程上也必须能惰性分配）、`icpu_id()`、`oblib::arch` 里发出 `pause`/`yield` 指令的函数、例 2 用到的 `WorkerCtx`；
  - 04 号 `error-log-fmt`：`ObError`/`ObResult`/`ObResultExt` 与 `OB_*` 常量，`ObToString`/`ObBuf`，`log_warn!`/`log_error!`/`log_info!`/`log_debug!`，`lbt()`；sync 内部日志前缀用 `ObLogMod::COMMON`，`cfg(loom)` 下日志宏为空；
  - 06 号：prelude 的导出（§2.0）；
  - 09 号：oblib build.rs 的生成框架（本模块的 `gen_latch_define` 步骤挂在上面，实现和测试归本文）。
- **使用者**：05 号（hash、queue、container）、08 号（storage_core、KV cache）、所有翻译单元。
- **时钟**：本模块自带 `pub(crate) fn current_time()`（墙上时钟微秒），03、05 号可直接复用，不再各写一份（Q5）。

## 8. 开放问题

| # | 问题 | 推荐 | 备选 |
|---|---|---|---|
| Q1 | `Latch<T>`、`FutexCond`、`BigReaderLatch` 的 futex 字是否照 C++ 按缓存行对齐（C++ 是因为 ObFutex 带 `CACHE_ALIGNED`） | 照 C++ 对齐：保持锁字不与相邻对象伪共享，内存与 C++ 同一量级（`Latch<()>` 64/128 字节，C++ 的 ObSpinLock 192/384 字节） | 不对齐：省内存，但热锁可能出现 C++ 没有的伪共享，由性能带兜底 |
| Q2 | `Monitor` 的计时用单调时钟（std `Condvar`）；C++ 的 ObThreadCond 在 Linux 上用墙上时钟算绝对截止时间，系统时间跳变时行为不同 | 接受单调时钟，记为偏差（建议进 A.4） | 在 futex 垫片上自写条件变量，按墙上时钟换算 |
| Q3 | CtxLock 的读锁解锁（`try_rdlock_ctx` + `unlock_ctx`）在 C++ 里会执行 `before_unlock`/`after_unlock`，是数据竞争 | 读路径不执行钩子，标 `FIXED(port)`（修订 A9）；理由：`has_pending_callback_` 在同一个写临界区内置位并被消费，读者看到的总是 false，差别只有 `opid_` 少加 1 | 钩子增加只读变体，`opid_` 改原子量后照样加 1 |
| Q4 | `BucketLatch` destroy 之后以不同的桶数再 init | 返回 `OB_INIT_TWICE` 并记 WARN（基线里没有这种调用；`init(&self)` 用 `OnceLock`，无法换数组） | 另给 `reinit(&mut self)`，拥有独占访问时重新分配 |
| Q5 | 1d 各模块的墙上时钟由谁提供 | `oblib::sync` 的 `pub(crate) current_time()`，03、05 号复用；翻译出的 `ObTimeUtility::current_time()` 保留自己的平台实现 | 由 03 号提供，本模块依赖它 |
| Q6 | `oblib::sync` 里不经 guard 的显式加解锁（`BucketLatch` 的单桶与全部加锁、`ObByteLock<()>::raw_*`、`ObRowLatch`/`ObSmallSpinLock`/`ObPtrSpinLock` 的 lock/unlock、`Quiescence::acquire_ref`/`release_ref`）是否用 `disallowed_methods` 限制在白名单路径 | 限制（修订 A7）；翻译代码用 guard | 不限制，靠审查 |
| Q7 | `Quiescence::sync()` 照 C++ 忙等不 PAUSE（`ob_qsync.h:65-74`） | 照抄 | 加 `pause()`，记 `PERF(port)` |
| Q8 | `ObLatchDesc::wait_event_idx` 与 `WAIT_EVENTS_TOTAL` 依赖 `ObWaitEventIds`，不能留在基础设施里 | 按函数级重映射挪到 `ob_wait_event` 单元，由 1c 写进 `units.tsv`/`seams.tsv` | 本模块复制 `WAIT_EVENT_DEF_END` 常量，由测试与翻译结果比对 |
| Q9 | `ob_queue_thread` 单元归属：`ObCond` 是锁原语，`S2MQueueThread` 是线程池且没有实例 | 单元处置 `replace:sync`，`ObCond` 由本模块提供，`S2MQueueThread` 经清单第 16 类确认后不翻译；与 03 号对齐 | 单元归 03 号，`ObCond` 由 03 号用 `Monitor<bool>` 实现 |
| Q10 | guard 与辅助名字不在 RULEBOOK §4 的改名表里 | 采用本文的命名（guard 用 C++ 的 guard 名，DRWLock 的嵌套 guard 拼成 `DRWLockRDLockGuard`/`DRWLockWRLockGuard`；新增 `UnlockHooks`、`HookedWriteGuard`、`ObAtomicInt`、`Monitor::new_inited`、`FutexCond::new_with_event`、`*_until`、`with_rdlocked`/`with_wrlocked`、`wrlock_with_retry_interval`），写进改名表（修订 A6） | 全部改用新造的 Rust 风格名字（`LatchGuard` 等） |

已排队的 RULEBOOK 修订（`migration/infra/amendments.tsv`，doc 列为 `02-sync`）：A1 写者 id、A2 桶锁的锁数与映射、A3 Quiescence 的槽、A4 TCRWLock 的大小、A5 loom 进白名单、A6 改名表补 guard 名、A7 显式加解锁的限制与 `let _ =` guard 的 CI 检查、A8 §2.7 映射表缺行、A9 CtxLock 读锁解锁路径。
