# 调查备忘：线程、同步与无锁结构

> - **主题**：concurrency（线程模型、锁、无锁结构与内存回收、原子与屏障、信号）。**基线**：`7d907abfa`；`git diff --stat 7d907abfa HEAD -- src/ rust/ deps/` 为空。只读调查，没有构建。
> - **计数口径**：翻译范围 = `migration/depmap/nodes.tsv` 的 6,529 个文件；先生成去掉注释和字符串字面量、保留行号的镜像，再在镜像上跑正则。脚本在 `/tmp/conc-survey-mz7q/scripts/`（按规则不入库），复现方法见 §2.0。
> - 推断和估算都标了「估计」；正则计数都是近似值。

## 1. 结论

1. **线程模型不变，只换实现**：seekdb 全是普通 OS 线程（没有协程，也没有 OceanBase 的 TG 框架），空闲时实测 80–90 个线程；行锁等待已经是"请求挂起、事后重投"，不占线程。Rust 版保留阻塞式 worker 和所有线程池、定时器的策略常数，用 `std::thread` 写一个小的 `oblib::thread`；SMART_CALL 的分段栈用 `stacker` 复刻（含 10 MB 上限 → `OB_SIZE_OVERFLOW`）。不引入 async。
2. **锁：数据放进锁里，接口统一**：锁约 460 处声明（其中 ObLatch 家族约 360）、约 2,100 处 guard，映射为 `oblib::sync::{Latch<T>, RwLatch<T>, ShardedRwLatch<T>, Monitor<T>}`。底层先用 `parking_lot`，保留 LatchId、带截止时间的加锁（超时返回 `OB_TIMEOUT`）、写锁降级、READ_PREFER（→`read_recursive`）。性能带不达标时，在同一个包装里换成移植的 ObLatch 自旋 + futex 语义，调用点不用改。
3. **原子操作一律 `SeqCst`**：2,962 处 `ATOMIC_*` 中 99% 在 C++ 里本来就是 seq_cst 或 `__sync` 全屏障。凡是被原子访问的字段都改成 `Atomic*`，同一字段上的普通读写用 `Relaxed`。弱化 ordering 推迟到行为对齐之后，凭 profile 和 loom 测试逐处做。
4. **无锁结构能换 crate 就换，必须自写的统一用 crossbeam-epoch**：队列换 crossbeam-channel/queue；ObLinkHashMap、ObLinearHashMap 换 `DashMap<K, Arc<V>>`；"换掉指针后等读者退出"的模式换 `arc-swap`。memtable keybtree、KV cache 用 crossbeam-epoch，加"退休预算"保住 C++ RetireStation 的有界内存。四套自研回收方案都不移植。
5. **unsafe 收进不超过 8 个审计模块**（bitlock、分片锁、信号、keybtree、KV cache 等），其余 crate 一律 `#![forbid(unsafe_code)]`。`memory_dump.cpp:45` 的 `siglongjmp` 改成不依赖信号的内存探测。

## 2. 事实

### 2.0 怎么数的

| 脚本（`python3 -I <脚本> /tmp/conc-survey-mz7q/stripped`） | 内容 |
|---|---|
| `mirror.py <repo> <out>` | 按 `nodes.tsv` 生成去注释、去字符串的镜像（6,529 个文件） |
| `count_atomics.py` | `\b(ATOMIC_[A-Z0-9_]+)\s*\(`、`__sync_*(`、`__atomic_*(`、`std::atomic<`、`memory_order_*`、屏障宏、`volatile`；跳过宏定义文件 `ob_atomic.h` |
| `atomic_targets.py`、`atomic_roles.py` | 取每处 `ATOMIC_*` 的第一个实参：分 `&member_`、`&obj->m_`、数组元素、指针值；再按字段名启发式分角色 |
| `inventory.py` | 约 250 个并发相关标识符的整词引用数和文件数 |
| `lock_sites.py` | 锁声明（锁类型后跟变量名）、RAII guard（`\w*(Lock\|Latch\|Mutex\|Cond\|QSync\|Critical)\w*Guard\w* 名字(`）、手工 `.lock()/.unlock()/.wait()/.signal()…` |
| `includers.py <repo>` | 用 `migration/depmap/edges.tsv` 统计并发头文件被多少个外部单元 include |

- **口径差异**：可行性报告的 2,997 处（抽查 3,003）含注释和被排除的文件；本文的 2,962 处不含。
- **线程实测**：
  - 启动：`python3 migration/judge/runner/sdb.py start --binary build_release/src/observer/seekdb --base-dir /tmp/conc-survey-mz7q/run/base --port 39123 --nodaemon`；
  - 计数：`wait-ready` 之后跑 `ps -M -p <pid>` 和 `sample <pid> 1`，然后 `sdb.py destroy`；
  - 环境：macOS 26，10 核，默认参数，无负载。

### 2.1 线程模型

**基本事实**

- **没有协程**：`ucontext` 只在 `ob_signal_handlers.cpp:165` 里用来读崩溃现场。`lib/coro/co_var.h:32-37` 的 `RLOCAL*` 宏全部展开成 `thread_local`。libeasy 只剩 18 个头文件（2,179 行），`easy_uthread.h` 没有实现。
- **没有 TG 框架**：`TG_CREATE`/`TG_START`/`TG_SCHEDULE` 等 27 个宏引用数都是 0；`ObTenant`/`ObMultiTenant` 也是 0（单租户）。
- **线程类型**：`share::ObThreadPool` = `lib::ThreadPool` = `lib::Threads`（`share/ob_thread_pool.h:25`、`threads.h:34-161`）。
- **线程启动协议**（`thread.cpp`）：
  - Linux 上用自带的带保护页的栈（:84、:118）；
  - 设置线程局部的 `current_thread_` 和 `tid_`；
  - 建线程级内存上下文：`ObPageManager::set_thread_local_instance`（:372）和 `ROOT_CONTEXT->CREATE_CONTEXT`（:377）；
  - 调 `IRunWrapper::pre_run/end_run`；
  - `stop()` 按 glibc 的私有偏移 720/724 读 tid（:164）。
- **停止标志**：`stop_` 是普通 `bool`，`bool &has_set_stop()` 直接返回它的引用（`threads.h:104`），91 处调用。

**规模**

| 项目 | 数量 | 证据 |
|---|---|---|
| 直接继承线程池基类的类 | 52：Threads 系 36、`ObSimpleThreadPool` 12、`ObReentrantThread` 3、`ObAsyncTaskQueue` 1 | `grep -rnoE "public\s+(lib::\|common::\|share::)?(ThreadPool\|Threads\|ObThreadPool\|ObSimpleThreadPool\|…)"` |
| `run1()` 线程体 | 63 | `void\s+(\w+::)?run1\s*\(\s*\)` |
| `handle(void *task)` | 29 | `ObSimpleThreadPool` 的任务是 `void*`，所有权靠约定（`ob_simple_thread_pool.h:48-128`） |
| `ObTimer` 成员 | 58 | 另有 105 个 `runTimerTask()` 实现、76 处 `schedule(` |
| `THIS_WORKER` | 390 处，138 个文件 | `static __thread Worker *self_`（`worker.h:115`）、`#define THIS_WORKER`（:174） |
| 线程局部变量 | `thread_local` 67、`__thread` 37、`RLOCAL*` 119、`GET_TSI*` 33 | `inventory.py` |
| `get_itid()` 稠密线程号 | 67 | 1024×64 位位图分配，线程退出时归还（`ob_tsi_utils.h:36-55`）；用来索引各种每线程槽数组 |
| `SMART_CALL*` | 约 1,130 | 剩余栈不足 64 KB（`_LARGE` 为 192 KB）时，切到 2 MB 堆栈，用 `jump_call` 汇编跳转；每线程栈总量超过 10 MB 返回 `OB_SIZE_OVERFLOW`（`ob_smart_call.h:28-30、:83、:112`） |
| `check_stack_overflow` | 110 | resolver 里溢出时 24 处返回 `OB_SIZE_OVERFLOW` |
| `SMART_VAR`/`HEAP_VAR` | 687 | 大局部变量按剩余栈放栈上或堆上 |
| 默认线程栈 | 256 KB | `rust/config/parameters.yaml:1354-1357`，范围 [256K, 20M] |

- **`THIS_WORKER` 的用途**（按方法统计）：
  - 超时：`set/get_timeout_ts`、`get_timeout_remain`、`is_timeout`，约 223 处；
  - 其他：`check_status` 52、session 34、重试标志约 25、SQL arena 13、调度 13。
  - `alloc_worker()` 是 weak 符号，默认给每个线程一个线程局部的 `Worker`（`worker.cpp:33`）。
- **定时器**：所有 `ObTimer` 共享一个全局 `ObTimerService`（`ob_timer_service.h:129`）：1 个调度线程，加 4–128 个 `TimerWK` 工作线程（:194-195）。任务以 `ObTimerTask&` 裸引用登记，生命周期靠 `cancel_task` + `wait_task` 的约定保证。
- **SQL worker**（`ObServerRuntime`，自适应）：
  - 下限 `2 + max(1, min_cpu × cpu_quota_concurrency)`，`cpu_quota_concurrency` 默认 10；
  - 上限 `max(内存/20/(stack_size+3.5M), 150)`（`ob_server_runtime.cpp:570-580`）；
  - 空闲 10 秒收缩；请求完成数停滞 3 秒时救援扩容（:653-698），这是修复 `d5dfc58f2`（线程池饿死）留下的机制。
- **行锁等待不占线程**：冲突的请求挂进 `ObLockWaitMgr`，worker 线程放手；唤醒或超时后 `repost` 回队列重试（`ob_lock_wait_mgr.h:179-289`）。

**空闲实测：80–90 个线程，随自适应池变化，全部是 pthread**

| 线程名 | 数量 | 线程名 | 数量 |
|---|---|---|---|
| DdlBuild | 16 | TxLoopWorker | 5 |
| TimerWK | 11 | sql-nio-*（Rust） | 5 |
| DDLTaskExecutor | 8 | ReqWorker（SQL，按需扩） | 4 |
| DAG | 6 | IO_SYNC_CH | 4 |

另外 31 个各 1 个，包括：LockWaitMgr、DeadLockLocal、DetectorTimer、TimerSvr、ClockGenerator、LogLoop、LogIOCB、OB_SLOG、OB_PLOG、SignalHandle、MemoryDump、DagScheduler、IOWorker、IO_GETEVENT、ApplySrv、CSFetcher、CSDispatcher 等。名单在 `/tmp/conc-survey-mz7q/sample_idle_threads.txt`。

### 2.2 锁

**两个原语，其余都是包装**

| 原语 | 文件 | 语义 |
|---|---|---|
| `ObLatch`（读写） | `ob_latch.h:159-253`、`ob_latch.cpp:480-706` | 32 位锁字：bit31 WAIT，bit30 WRITE，低 30 位是写者 TID 或读者计数（上限 2²⁴）。先自旋 `max_spin_cnt_` 次 `PAUSE`，再 `sched_yield` `max_yield_cnt_` 次，最后挂进全局等待队列（按锁地址哈希的 3,079 个桶，`ob_latch.h:150`），每个等待者在自己的字上 `futex_wait`。支持绝对超时（`OB_TIMEOUT`）、`wr2rdlock` 降级、按 TID 校验的 `unlock(puid)`；不带 puid 时任何线程都能解锁。 |
| `ObLatchMutex` | `ob_latch.cpp:62-190` | 同样是"自旋 → 让出 → `ObFutex` 等待"，解锁时有 WAIT 位就唤醒一个 |

- **Latch 表**（`ob_latch_define.h`）共 204 项：
  - 策略：FIFO 180 项，READ_PREFER 24 项（读者忽略 WAIT 位插队，并排在等待队列最前）；
  - 自旋次数：2000 的 182 项，`INT64_MAX` 的 13 项和 2000 万的 4 项（实际上是纯自旋锁），1 的 2 项（分配器）；
  - 让出次数全部为 0；
  - 统计已经剥离，`ObLatchWaitEventGuard` 只记 `current_wait`；`current_locks[16]` 持锁登记表没有任何消费者。
- **包装**：
  - `SpinRWLock` = `ObLatch`（`ob_spin_rwlock.h`）；
  - `ObSpinLock` 和 `lib::ObMutex` = `ObLatchMutex`（`ob_spin_lock.h`、`ob_mutex.h`），注意 `ObSpinLock` 名为自旋锁，其实会睡眠；
  - `ObRecursiveMutex` = 一个 `ObLatch` 加重入计数，靠锁字里的写者 TID（`is_wrlocked_by()`）判断是否重入（`ob_recursive_mutex.h:27-80`）；
  - `ObBucketLock` = `ObLatch` 数组；
  - `DRWLock` = 按 `get_itid() % OB_MAX_CPU_NUM` 分片的 `ObLatch` 数组（`ob_drw_lock.h:130`）；
  - `TCRWLock`（`ob_tc_rwlock.h:143-210`）= `ObLatch` + `TCRef` 每线程读引用 + 用 futex 实现的 `LWaitCond`，是"大读者锁"。
- **其他锁**：
  - `ObThreadCond`：pthread mutex + cond 的监视器，需要显式 `init`（`ob_thread_cond.h/.cpp`）；
  - 嵌在数据字里的位自旋锁：`ObByteLock`/`ObSmallSpinLock`/`ObPtrSpinLock`（`ob_small_spin_lock.h`），以及 `ObRowLatch`（纯 TAS 自旋，没有退避，`ob_row_latch.h:26-44`，每个 `ObMvccRow` 一个）；
  - 直接用 pthread 或 `std::mutex` 的约 20 处声明（`pthread_mutex_t` 7、`pthread_cond_t` 5、`std::mutex` 5，其他 2），`ObMonitor`/`ObUtilMutex` 用在定时器服务里。
- **futex 直接调用点**：6 个原语（latch 等待队列、`ObFutex`、`LWaitCond`、`SimpleCond`、`ObLightyCond`、`ob_ex_rpc`）。各平台实现：
  - Linux：`syscall(SYS_futex)`（`ob_futex.h:39`）；
  - macOS：私有接口 `__ulock_wait`（`ob_futex.cpp:77`）；
  - Windows：`WaitOnAddress`（`ob_futex.h:92`）。
  - `futex_hook` 是 weak 符号（`ob_futex.cpp:102`）。

**使用规模**（`lock_sites.py`、`inventory.py`）

| 项目 | 数量 |
|---|---|
| 锁声明（启发式） | 461。`ObSpinLock` 101、`ObMutex` 88、`SpinRWLock` 68、`ObThreadCond` 39、`RWLock` 别名 30、`ObLatch` 29、`TCRWLock` 23、`ObBucketLock` 15、`ObByteLock` 12…… 其中 ObLatch 家族约 360 |
| RAII guard | 2,109。`ObSpinLockGuard` 317、`ObMutexGuard` 250、`SpinWLockGuard` 168、`LockGuard` 148、`RLockGuard` 139、`ObThreadCondGuard` 134、`SpinRLockGuard` 119…… 按模块：storage 1,105、logservice 174、share 169、sql 165、oblib 153 |
| 手工调用（含非锁接收者） | 1,010：`wait` 277、`unlock` 231、`lock` 129、`signal` 77、`wrlock` 55…… |
| 自带 `lock()/unlock()` 的类 | 37 个文件里 93 个方法，多数配 `ObLockGuard<T>` 之类的模板使用 |
| 显式超时加锁 | 约 21 处，在带参数的 41 处 `lock(…)` 里人工剔除了行锁、表锁接口：分配器、tx_ctx（5 s 或 100 ms）、tablet GC、apply/replay、计划缓存节点（超时变成 `OB_PC_LOCK_CONFLICT`，按缓存未命中处理，`ob_i_lib_cache_node.cpp:128-145`）、两个用语句截止时间的进程级互斥锁。另有约 50 个 `*WithTimeout`/`*RetryInterval` guard |
| `ob_usleep`（就是 `::usleep`，`utility.h:210-247`） | 224；`sched_yield` 19，`PAUSE` 86 |

- **锁保护的数据没有标注**。典型例子是 `ObPxTargetMonitor`：
  - 注释说锁保护"PX worker 配额计数"（`ob_px_target_monitor.h:89-90`），但 `get_parallel_session_count()` 不加锁就读（`.cpp:67-69`）；
  - 释放锁之后才在另一个条件变量上等待（:95），有丢失唤醒的窗口，靠最长 1 秒的超时兜底。
- **锁顺序没有机制保障**：没有锁层级，也没有运行时检查，只有零星注释，如 `ob_trans_ctx_mgr_v4.cpp:1007`（"ObLSTxCtxMgr -> ObTxCtx"）和 `ob_ddl_service.cpp:22030`。
  - 一年内的并发修复（`migration/feasibility/results/pain/verdicts.tsv`）：死锁 7 个，如 `834bbee1e` 提交回调的 latch 顺序环；先检查后操作 3 个；饿死 1 个；数据竞争 4 个。只有数据竞争是 Rust 编译期能挡住的。

**用户可见的锁行为**

| 行为 | 机制 | 证据 |
|---|---|---|
| 行锁等待超时 → 1205 "Lock wait timeout exceeded" | `OB_ERR_EXCLUSIVE_LOCK_CONFLICT`（-6003）和 `_NOWAIT`（-6006）；超时取 `ob_trx_lock_timeout` 和 `ob_query_timeout` 的较小者，`ob_trx_lock_timeout` 为 0 表示不等 | `ob_errno.def:1378-1381`、`ob_mvcc_acc_ctx.h:330-339`、`ob_access_service.h:325-328` |
| 死锁 → 1213 | LCL 检测器：`DetectorTimer` 时间轮，精度 10 ms；牺牲者经 `kill_tx(session, OB_DEAD_LOCK)` 回滚 | `ob_errno.def:153`、`ob_deadlock_parameters.h:34-36`、`ob_sql_trans_control.cpp:342` |
| 并发 DDL、Root Service 调用串行化，等锁超时 → 4012 | 两个进程级 `ObMutex`，都用 `THIS_WORKER.get_timeout_ts()` 作截止时间加锁 | `src/rootserver/ob_local_ddl_serial_call.h:28-47`、`src/query/api/query/command/ob_root_service_serialization.h:35-55` |
| 递归过深 → `OB_SIZE_OVERFLOW` | SMART_CALL 10 MB 上限，`check_stack_overflow` | 见 §2.1 |

这里只有第三行直接依赖 latch 本身的语义（带截止时间加锁）；计划缓存的超时只影响命中率，不影响结果。行锁、死锁、栈深三项和锁原语怎么选无关，但它们的策略常数和算法必须原样保留。

### 2.3 无锁结构与内存回收

**并发基础设施合计约 2.64 万行，约占翻译量的 1.1%**：oblib 的 `lock/queue/thread/task/signal/atomic/thread_local/core_local` 共 96 个文件、17,512 行；并发哈希表 5,519 行（`ob_link_hashmap*`、`ob_dchash`、`ob_linear_hash_map`、`ob_hash`、`ob_darray`、`ob_fixed_hash2`；`ob_cuckoo_hashmap` 没有同步，不计入）；回收方案 1,511 行；KV cache 的 hazard 部分 1,862 行。行数取自 `nodes.tsv`，用 `awk` 按路径求和。

| 结构 | 机制 | 行数 | 外部 include 单元 | 主要用户 |
|---|---|---|---|---|
| `ob_link.h` | Harris 链表，`next_` 最低位作删除标记（:37-91）；另有无锁双向链表 `dl_insert/dl_del`（:251-330） | 330 | 19 | 哈希表、队列、slice/fifo 分配器、KV cache 的 LRU |
| `ObLinkHashMap` | `DCHash` + 值引用计数（`uref`/`href`）；`get` 加引用、`revert` 减引用，`del` 后节点交给 RetireStation（`ob_link_hashmap.h:290-406`） | 648+127+485 | 12 | 时区、两个自增服务、死锁检测器、用户资源，共 5 处 |
| `ObLinearHashMap` | 桶标志位自旋锁 + 目录翻倍 | 2,532 | 13 | 块管理、锁等待、MDS、临时文件、表锁 memtable |
| `ObSpLinkQueue` 等 | 侵入式 MPSC 队列：push 用 XCHG，pop 时把 head 置 NULL 当锁（`ob_link_queue.h:61-188`） | 370 | 13 | DTL 通道、apply 服务、临时文件、重试队列 |
| `ObLightyQueue`/`ObFixedQueue` | 有界 MPMC 环形队列 + 每个序号一个 futex 条件变量（`ob_lighty_queue.cpp`）；对象池的空闲表 | 371、414 | 5、17 | 线程池、对象池 |
| `ObPriorityQueue2` | 多级链式队列 + futex | 304 | 5 | SQL 请求队列（`ob_server_runtime.h`） |
| memtable `ObKeyBtree` | 无锁读、写时复制节点，QClock + RetireStation + ObQSync | 2,757 | 3 | memtable 索引 |
| KV cache | map 节点用 hazard version，内存块用 hazard pointer，句柄退休用 QClock | 8,356，其中 hazard 部分 1,862 | — | 块缓存、行缓存 |

**四套自研回收方案**

1. **QClock + RetireStation**（`ob_retire_station.h:30-263`）：
   - 机制：全局单调时钟。读者进入临界区时，把当前时钟写进按 `get_itid()` 选中的槽（`OB_MAX_CPU_NUM×4` 个），冲突就探测下一个；写者把节点挂进按线程划分的退休表。
   - 背压：某个槽的待回收表超过上限（1024）时，退休线程在 `wait_quiescent` 里一直 `PAUSE` 自旋（:217-230），也就是阻塞写者来换取有界内存。
   - 长临界区：keybtree 的 `ScanHandle` 在整个扫描期间都占着临界区（`ob_keybtree_deps.h:41、:412-457`）。
   - 依赖这个上界的地方：KV cache 句柄池的大小就是按它算的（`ob_kvcache_store.h:104`）。
2. **ObQSync**（`ob_qsync.h`）：每槽一个读者计数，`sync()` 自旋等所有槽归零（:65-74）。有 8 个用户，典型用法是"换指针 → 等读者退出 → 释放旧对象"。
3. **HazardRef**（`ob_hazard_ref.h`）：按版本号判断安全点。只被 `ob_hash.h`/`ob_darray.h` 使用，最终用户是 `ob_fixed_hash2.h`（锁等待节点表）。
4. **KV cache 的 hazard pointer 和 hazard version**（`share/cache/ob_kvcache_hazard_*`）：可行性报告提到的 `54123f13e`（#871）就出在这里。这个 bug 是退休条件里的租户捷径（`tenant_id == 0` 恒成立）绕过了版本保护，造成释放后使用。它是回收方案自身的逻辑错误，Rust 挡不住。

**没有外部 include 的死代码**，翻译前应确认后删除：`ob_bucket_qsync_lock` 467 行、`ob_delay_free_allocator` 431 行、`ob_map_queue_thread_pool` 352 行、`ob_ms_queue` 193 行（加只被它用的 `ob_co_seq_queue`）、`ob_ring_array` 153 行。

### 2.4 原子操作与屏障

**宏的语义**（`ob_atomic.h`）

| 宏 | 实现 | 次数 |
|---|---|---|
| `ATOMIC_LOAD` / `ATOMIC_STORE` / `ATOMIC_SET`、`ATOMIC_TAS`（交换）/ `ATOMIC_LOAD64` | `__atomic_*` + `__ATOMIC_SEQ_CST`（:41-53、:63-66） | 1,097 / 498 / 112+42 / 5 |
| `ATOMIC_AAF`/`FAA`/`INC`/`DEC`/`SAF`/`FAS`/`BCAS`/`CAS`/`VCAS`/`ANDF` | `__sync_*`，GCC 文档定义为全屏障（:55-74、:123-125） | 353/184/173/85/82/2/188/57/30/3 |
| `ATOMIC_ADD_TAG`/`SUB_TAG` | 在 `ob_mvcc_row.h:43`、`ob_i_tablet_memtable.h:29` 本地定义的 LOAD + BCAS 循环 | 22 |
| `*_ACQ`/`*_REL`/`*_RLX` | 显式弱序 | 29（1.0%） |
| `*_AR`、`ATOMIC_CMP_AND_EXCHANGE` | acq_rel | 0 |
| `MEM_BARRIER`（`__sync_synchronize`）/ `WEAK_BARRIER` | 全屏障 / x86 上只是编译器屏障、ARM 上是全屏障（:29-36） | 14+5 / 22 |
| `PAUSE` | x86 `pause`，ARM `yield` | 86 |

- **分布**：2,962 处，459 个文件。storage 1,088、oblib 719、sql 415、share 310、logservice 176。
- **按操作**：load 1,117、store 512、算术 RMW 879、CAS 300、交换 154。
- **实参形态**：
  - `&member_`（本对象成员）2,128，别的对象的成员 511；
  - 指针值 117，多是 `ob_link.h` 这类辅助函数里的 `addr`；
  - 数组元素 81，全局变量或局部变量 65。
  - 涉及 736 个不同的字段名。
- **混用估计（启发式）**：在 1,123 个（字段, 文件）对里，有 392 对（约 35%）在同一文件中除原子访问外还有超过 2 处普通引用，例如 `ob_latch.cpp` 里的 `lock = lock_;` 就是 volatile 普通读。
- **角色估计（启发式）**：计数器和统计约 46%，标志和状态约 15%，引用计数约 9%，指针和链接约 7%，无法归类约 24%。
- **藏在别处的原子操作**：
  - `REACH_*` 系列宏共 316 处：`REACH_TIME_INTERVAL`（241）和 `REACH_COUNT_INTERVAL`（22）在每个调用点放一个 `static volatile int64_t`，再做 `ATOMIC_CAS`/`ATOMIC_AAF`（`ob_macro_utils.h:791-803、:880-888`）；`REACH_THREAD_TIME_INTERVAL`（46）对线程局部变量做 CAS（:819-827）；
  - 值类型上的 `atomic_load/store/get/set/vcas` 方法约 120 处，`inc_update/dec_update`（CAS 求最大、最小）61 处；
  - `__sync_*` 直接调用 36 处；`std::atomic` 只有 32 处声明、77 处 `memory_order`，集中在新代码里，如 `ob_adaptive_worker_pool.h` 用的是 acq_rel/relaxed；
  - 128 位 CAS 只在 `REACH_COUNT_PER_SEC` 里用到（3 处）；
  - 非汇编的 `volatile` 约 214 处，多是"穷人版原子"的标志和计数。
- **seqlock**：真正的只有 1 处，在 `ob_timer_monitor.cpp:181-187`，读者不加锁复制整个结构。`utility.h:468` 的 `SeqLockGuard` 名字叫 seqlock，实际只是写者自旋锁。
- **伪共享对齐**：`CACHE_ALIGNED` 140 处，对齐大小 x86 为 64、ARM 为 128（`ob_macro_utils.h:617-623`）。

### 2.5 信号

- **实际生效的只有两处**：
  1. **`ObSignalHandle` 线程**：`main.cpp:678` 先在主线程屏蔽信号，之后创建的线程都继承这个掩码；然后由该线程 `sigtimedwait`（macOS 上用 `sigwait` 模拟），见 `ob_signal_handle.cpp:56-80、:105-140`。处理的信号：
     - SIGPIPE：忽略；
     - **SIGTERM：`raise(SIGKILL)`**，用户可见，生命周期场景会测到；
     - SIGUSR1：优雅停机；
     - 实时信号 40–64：日志文件检查、调日志级别、内存转储等。
  2. **`memory_dump.cpp`**：`find_chunk` 从任意指针往回扫描内存找块头。扫描前临时给整个进程装上 SIGSEGV/SIGABRT 处理函数，段错误时 `siglongjmp` 回来（:43-79、:366-398）。只由诊断用的 `DUMP MEMORY` 命令触发（`ob_dump_task_generator.cpp:54-115`），对外协议看不到。
- **死代码**：
  - `install_ob_signal_handler()`（`ob_signal_handlers.cpp:69-87`）在整个 `src/` 里没有调用者，`g_redirect_handler` 从来没被置为 true，所以崩溃打印函数 `coredump_cb`（:141-232）不会执行，崩溃走默认动作，直接 core；
  - `do_with_crash_restore` 也没有调用者。
- **解析器的 longjmp** 归解析器主题：`sql_parser_base.c:106` 和 `pl_parser_base.c:48` 调 `setjmp`，词法文件里 `longjmp`。

### 2.6 已有 Rust 的约定

- **sql-nio**：
  - 锁和线程：`std::sync::{Mutex, Condvar, RwLock, LazyLock}`，68 处 `lock()` 里 67 处是 `.lock().unwrap()`；线程用 `thread::Builder::new().name("sql-nio-N")`，`catch_unwind` 失败就 abort（`reactor.rs:1113-1121`）；
  - 原子：`Ordering` 显式给出，Acquire 78、Release 41、AcqRel 8，没有 Relaxed；
  - FFI 句柄：`unsafe impl Send/Sync for Handler`（`ffi_types.rs:50-51`）。
- **工作区**：
  - `Cargo.lock` 里没有 parking_lot 和 crossbeam；
  - release 和 cmake-debug 两个 profile 都是 `panic = "abort"`（`rust/Cargo.toml`），所以 `std` 锁中毒在生产中不可能出现；
  - 工具链钉在 1.98.1。

## 3. 选项

### 3.1 线程模型

| 选项 | 做法 | 评价 |
|---|---|---|
| **T1 OS 线程（推荐）** | `oblib::thread` 提供 `Threads`、`SimplePool<T>`、`Timer`/`TimerService`、`ReentrantThread`、`AdaptivePool`，名字、栈大小、自适应常数都照抄；线程入口统一设置线程局部状态：itid、内存属性（见 memory 主题）、worker 上下文 | 全部是安全代码。翻译机械：52 个子类各对应一个固定类型，`run1` 变成 trait 方法。对齐风险低，编译成本可忽略 |
| T2 async（tokio） | 后台服务或 SQL 执行改成 future | 阻塞调用要改造：latch、futex 条件变量、`usleep` 轮询 224 处、SMART_CALL；而且要和 sql-nio 的 mio reactor、C++ 共存期的线程交互。等于重新设计执行模型，对齐风险高，否决 |

草图：
```rust
pub trait Runnable: Send + Sync + 'static { fn run1(&self, t: &ThreadCtx); } // t.idx(), t.stop_requested()
pub struct Threads { name: &'static str, n: AtomicUsize, stop: Arc<AtomicBool>, joins: Mutex<Vec<JoinHandle<()>>> }
// start(): Builder::new().name(..).stack_size(..).spawn(move || thread_main(r.clone(), ctx))
// SMART_CALL!(f(x)) => smart_call(RESERVED, || f(x)): 剩余 < RESERVED 时 stacker::grow(STACK_PER_EXTEND, ..)，
//                     线程累计扩栈 > 10 MB 时返回 Err(ObError::SizeOverflow)
```

- **定时任务**：`ObTimerTask` 换成 `Arc<dyn TimerTask>`，由服务持有，裸引用加"取消再等待"的约定随之消失。
- **`THIS_WORKER`**：换成 `thread_local!` 的 `WorkerCtx`。每个请求用 RAII 装入、恢复；`check_status` 这类上层逻辑通过启动时注册的钩子回调，这也是 weak 符号 `alloc_worker`/`common_yield` 的去处。

### 3.2 锁

| 选项 | 做法 | unsafe | 对齐与性能 | 翻译 |
|---|---|---|---|---|
| L1 `std::sync` | 照 sql-nio | 0 | **不能带超时加锁**，满足不了两个串行锁、约 21 处超时加锁和约 50 个超时 guard；不能降级；`RwLock` 公平性随平台而定 | `.lock().unwrap()` 噪声大 |
| **L2 parking_lot 包装（推荐先行）** | `Latch<T>`/`RwLatch<T>` 是 `parking_lot::{Mutex, RwLock}` 的新类型，构造时传 `LatchId`。`lock_until(deadline)` 映射为 `OB_TIMEOUT`；`downgrade`；READ_PREFER 用 `read_recursive`；`Monitor<T>` = `Mutex` + `Condvar` | 0 | 有超时、可降级、无中毒（0.12.5 文档）；公平性是"任务公平 + 最终公平"，与 FIFO 策略接近。**自旋比 ObLatch 短得多**：ObLatch 是 2000 次 `PAUSE`，有 17 个 latch 是纯自旋，parking_lot 只自旋几轮就让出、再 park；热锁的吞吐要靠性能带检验 | 一张对照表就能机械翻译 |
| L3 移植 ObLatch 语义 | 在同一个包装里，用 `AtomicU32` 锁字加 `parking_lot_core::park/unpark` 实现原来的 3079 桶等待队列，按 latch 表保留自旋次数、FIFO/READ_PREFER 和锁字里的写者 TID | 约 15–25 处，单文件（估计） | 原样保留语义；手写锁本身有出错风险，需要 loom 验证 | 调用点和 L2 完全相同 |
| L4 锁当令牌 + `UnsafeCell` | 照搬 C++：锁和数据分开，字段用 `UnsafeCell` | 2,109 个 guard 区域里的所有字段访问 | 对齐最直接 | 等于 unsafe 写的 C++，否决 |

- **映射表**：
  - `ObSpinLock`/`ObMutex` → `Latch<T>`；
  - `SpinRWLock`/`ObLatch` → `RwLatch<T>`；
  - `TCRWLock`/`DRWLock` → `ShardedRwLatch<T>`：读者只锁自己 itid 对应的分片，写者锁全部分片，内部 unsafe；`crossbeam_utils::sync::ShardedLock` 没有超时，不合用；
  - `ObRecursiveMutex` → `parking_lot::ReentrantMutex`；
  - `ObThreadCond` → `Monitor<T>`；
  - `ObByteLock`/`ObSmallSpinLock`/`ObPtrSpinLock`/`ObRowLatch` → `oblib::sync::bitlock`，锁位和数据仍在同一个字里，内部 unsafe。
- **数据怎么放进锁**（这一步不完全机械，翻译单元是头文件组，类定义和方法在同一单元，正好够做判断）：
  1. 在本类 guard 区里写过、在锁外从不写的字段，放进 `T`；
  2. 锁外也读的字段，变成 `Atomic*`：锁内写入，锁外 `Relaxed` 读；
  3. 一个锁保护的是别的对象的字段时，用 `Latch<()>` 当令牌，被保护的字段改原子，并标 `TODO(port)`；
  4. 跨函数持锁的（`unlock` 231 处、93 个加锁接口方法）改成返回 guard，或者写成 `with_locked(|g| …)`。

草图：
```rust
pub struct RwLatch<T> { id: LatchId, inner: parking_lot::RwLock<T> }
impl<T> RwLatch<T> {
    pub fn write_until(&self, deadline: Instant) -> ObResult<RwLockWriteGuard<'_, T>> {
        self.inner.try_write_until(deadline).ok_or(ObError::Timeout)          // C++ wrlock(abs_timeout) → OB_TIMEOUT
    }
    pub fn read(&self) -> RwLockReadGuard<'_, T> {
        if LATCHES[self.id].read_prefer { self.inner.read_recursive() } else { self.inner.read() }
    }
}
struct PxTargetMonitor { quota: RwLatch<Quota /* used, sessions */>, servers_target: AtomicI64 }
```

### 3.3 原子操作的 ordering 政策

| 选项 | 做法 | 评价 |
|---|---|---|
| **O1 一律 SeqCst（推荐）** | 宏里隐含 seq_cst 的（99%）都用 `SeqCst`；29 处显式弱序照写；`MEM_BARRIER` → `fence(SeqCst)`，`WEAK_BARRIER` → `fence(AcqRel)`（x86 原语义就是不保证 store→load），`__COMPILER_BARRIER` → `compiler_fence`，`PAUSE` → `hint::spin_loop`；`inc_update` → `fetch_max`（注意返回值语义不同）；`REACH_TIME_INTERVAL`/`REACH_COUNT_INTERVAL` → 每个调用点一个 `static AtomicI64` 的宏，`REACH_THREAD_TIME_INTERVAL` → `thread_local!` 里的 `Cell<i64>` | 能逐字翻译，对齐风险最低。x86 上和 C++ 生成的指令基本一样；ARM 上 RMW 少了 `__sync` 尾部的 `dmb`，Dekker 式握手只要两边都是 SeqCst 原子就仍然成立 |
| O2 翻译时按语义弱化 | 由 agent 按"计数器 Relaxed、发布 Release/Acquire、引用计数 AcqRel"逐处判断 | 2,962 处里靠推理，错了只会在 ARM 上随机出问题，裁判很难抓到。否决 |
| O3 = O1 + 对齐后的 `PERF(port)` 弱化 | 只动 profile 证明是热点的地方，每处配 loom 测试 | 推荐作为 O1 的后续 |

**字段规则**：
- 凡被任意 `ATOMIC_*` 访问过的字段，类型改成 `AtomicI64`/`AtomicU32`/`AtomicPtr`/`AtomicBool`；
- 同一字段的普通读写：在拥有 `&mut self` 的上下文里（构造、发布前的 reset）用 `get_mut()`，其余用 `Relaxed`；
- 如果某处普通访问参与了跨线程的 store→load 握手（附近通常有 `MEM_BARRIER`），改用 `SeqCst`，并请审查者确认；
- `volatile` 标志一律改成 `Relaxed` 原子；
- `ATOMIC_LOAD64`/`STORE64` 作用在结构上的，用 `AtomicU64` 加 `to_bits`，或者 `crossbeam_utils::atomic::AtomicCell`；
- 128 位 CAS 只有 3 处，改成 `Mutex`。

草图：
```rust
struct CountHandle { count: AtomicI64 }                  // C++: int64_t count_;
fn size(&self) -> i64 { self.count.load(SeqCst) }        // ATOMIC_LOAD(&count_)
fn add(&self, x: i64) -> i64 { self.count.fetch_add(x, SeqCst) } // ATOMIC_FAA(&count_, x)
fn dump(&self) -> i64 { self.count.load(Relaxed) }       // C++ 里的普通读 count_
```

### 3.4 无锁结构与内存回收

**通用结构直接换 crate**（前提，和下面选哪种回收方案无关）：

| C++ | Rust |
|---|---|
| `ObLightyQueue`/`LightyQueue`/`ObFixedQueue`（作队列用）/`ObPriorityQueue2` | `crossbeam_channel::bounded`（`recv_timeout`，满时 `try_send` 返回 `Full` → `OB_SIZE_OVERFLOW`）；多优先级按优先级逐个 `try_recv`，再阻塞 `select` |
| `ObFixedQueue`（作空闲表用） | `crossbeam_queue::ArrayQueue` |
| `ObSpLinkQueue`/`ObLinkQueue`/`ObSpScLinkQueue` | `crossbeam_queue::SegQueue<Box<T>>` 或 `crossbeam_channel::unbounded`，不再侵入式，所有权随 `Box` 转移 |
| `ObLinkHashMap`/`ObLinearHashMap`/`FixedHash2`（锁等待表，一个哈希值挂多个等待者，值类型改成队列） | `dashmap::DashMap<K, Arc<V>>`：`get` + `revert` 变成克隆 `Arc` 再 drop，`del` 后最后一个 `Arc` 释放值，正好就是 `uref` 归零。规则：拿到 guard 立刻克隆 `Arc` 再放手，否则会重现 C++ 文档警告的回调死锁（`docs/developer-guide/en/container.md:584`） |
| ObQSync 的"换指针后等读者" | `arc_swap::ArcSwap<T>` |
| `CACHE_ALIGNED` | `crossbeam_utils::CachePadded` |

**必须自写的结构**（keybtree；KV cache；MVCC 链按 storage 主题的说法随整个 memtable 一起释放，不需要回收方案），选一种回收方案：

| 选项 | 做法 | unsafe | 性能与语义 | 翻译与对齐 |
|---|---|---|---|---|
| **R1 crossbeam-epoch（推荐）** | `epoch::pin()` 对应 `enter_critical`；`Atomic<T>` 加 `Shared<'g,T>`，删除标记用 `with_tag`；`defer_destroy` 对应 retire。另加"退休预算"：按结构维护一个静态的待回收计数，超过 1024 时，在放掉 guard 之后反复 `pin().flush()` 并让出 CPU，模拟 `wait_quiescent` | 解引用 `Shared` 和 `defer_destroy` 是 unsafe，估计 keybtree 40–80 处、KV cache 40–80 处 | `pin` 的开销和 QClock 的一次 CAS 相当（估计）；`Shared<'g>` 的生命周期能在编译期挡住"出了临界区还拿着指针"；成熟（0.9.21）。原生语义不阻塞写者，内存随停滞的读者增长，退休预算用来补上 | 用户代码改动是机械的；背压语义靠退休预算近似；与 storage 主题的推荐一致 |
| R2 seize（0.5.1，Hyaline） | 每个结构一个 `Collector`，`retire` 是 unsafe | 同上 | 回收延迟可预测，内存效率接近 hazard pointer；papaya 0.2.5 用的就是它；但 API 在最近几个小版本之间有不兼容的改动，用户比 R1 少 | 同上 |
| R3 移植 QClock + RetireStation + QSync | 约 500 行，基本是原子操作，释放节点要 unsafe | 用户拿到的是裸指针，所有用户都要 unsafe，估计几百处 | 有界背压、itid 槽位与 C++ 完全一致 | 1:1 翻译，对齐最稳，但安全收益最小 |

- **KV cache 的内存块**：读者会长时间持有，epoch 不合适，因为 pin 太久会让垃圾堆积。可选：
  - `Arc<MemBlock>` 引用计数：零 unsafe，代价是每次 get/release 两次原子操作；
  - `haphazard` 0.1.8（Folly hazptr 的 Rust 移植）：文档里还有 TODO；
  - 移植现有的 hazard pointer。
  推荐 `Arc`，热点再说。KV cache 原来的三套方案并成"map 节点用 epoch + 内存块用 `Arc`"，句柄池也就不再依赖 RetireStation 的上界。

草图：
```rust
let guard = &epoch::pin();                              // QClock::enter_critical
let n = node.child[i].load(Acquire, guard);             // Shared<'_, Node>
if let Ok(old) = parent.slot.compare_exchange(cur, new_node, AcqRel, Acquire, guard) {
    unsafe { guard.defer_destroy(cur) };                // RetireStation::retire（SAFETY: cur 已经摘下）
    KEYBTREE_BUDGET.retired(1);                         // 放掉 guard 后，超过 1024 就 flush 并让出
}
```

### 3.5 unsafe 收敛和信号

- **允许出现 unsafe 的模块清单**，由 CI 用 grep 检查，其余 crate 加 `#![forbid(unsafe_code)]`：
  1. `oblib::sync::bitlock`（约 10 处）；
  2. `oblib::sync::sharded`（约 10 处）；
  3. 选 L3 时的 `oblib::sync::latch_raw`（约 20 处）；
  4. `oblib::signal`（`sigaction` 约 5 处）；
  5. `storage::memtable::keybtree`；
  6. `share::cache::map`；
  7. storage 主题的 `storage-core`；
  8. C++ 共存期的 FFI 边界。
  - 合计估计 150–300 处 unsafe 块。
- **每处 unsafe 的要求**：写 `SAFETY:` 注释；开 `clippy::undocumented_unsafe_blocks`；`unsafe impl Send/Sync` 只允许出现在清单内的模块。
- **验证**：`oblib::sync` 和自写的无锁结构配 loom 测试；单线程用例跑 Miri；可选再加一个 TSan 构建。
- **信号**：
  - `ObSignalHandle` 换成 `nix` 的 `SigSet::thread_block` + `SigSet::wait`，零 unsafe；保留 SIGTERM → SIGKILL、SIGUSR1、实时信号 40–64 的语义。
  - `memory_dump` 的 `siglongjmp` 有三种改法：
    - **S1（推荐）**：不靠信号探测内存。Linux 用 `process_vm_readv` 读本进程，或往管道 `write` 遇到 EFAULT 不会触发 SIGSEGV；macOS 用 `mach_vm_read_overwrite`。也可以改由分配器维护块登记表，直接按指针查。
    - S2：留一个很小的 C 垫片继续 `sigsetjmp`，要求中间没有任何 Rust 栈帧。
    - S3：删除这个诊断功能。
  - 崩溃处理函数在基线上本来就没有安装，Rust 版保持默认的 core 行为即可。
- **编译成本**（估计）：parking_lot、crossbeam-*、dashmap、arc-swap、stacker 都是小型叶子 crate；stacker 依赖的 psm 要用 `cc` 编译汇编，`cc` 已经在 `Cargo.lock` 里。包装类型的泛型单态化只生成很小的内联函数，原子类型没有额外成本。各选项之间的差别，相对 sql、storage 大 crate 的编译时间都可以忽略。

## 4. 推荐

1. **线程**：T1。
   - 线程池、定时器、`THIS_WORKER` 各提供一个安全的 Rust 类型；
   - 策略常数照搬：worker 上下限公式、10 秒收缩、3 秒救援、TimerWK 4–128 个、栈默认 256 KB、10 MB 扩栈上限；
   - SMART_CALL 用 `stacker`，`SMART_VAR`/`HEAP_VAR` 一律放堆上。
   - 前提：memory 主题提供线程级 `MemAttr` 的安装接口。
2. **锁**：L2 先行，L3 作为备份，两者调用点相同，只换包装内部；数据放进锁里，按 §3.2 的四条规则判断。
   - 前提：同意新增 `parking_lot` 依赖；第 6 步的性能带用例覆盖热锁，包括 ROW_LOCK、TRANS_CTX_LOCK、桶锁、ServerRuntime 锁。
3. **原子操作**：O1，对齐后再做 O3。
   - 前提：审查清单里加一条"普通访问是否参与 store→load 握手"。
4. **回收**：通用结构换 crate；自写结构用 R1 加退休预算；KV cache 内存块用 `Arc`；四套自研方案和 §2.3 列的死代码都不翻译。
   - 前提：storage 主题确认 keybtree 用 crossbeam-epoch（它的草案已经这么推荐）。
5. **锁顺序**：翻译时原样保留加锁顺序，不重排；debug 构建打开 `parking_lot` 的 `deadlock_detection` 特性，或者用 LatchId 恢复持锁登记表，做运行时检测。类型级的锁层级留到迁移之后再考虑。
6. **理由汇总**：
   - 这个组合里不安全的部分最少，unsafe 估计 150–300 处，集中在 8 个以内的模块；
   - 翻译主要靠对照表；
   - 用户可见的三类锁行为（超时、死锁、栈深）都能逐项保留；
   - 新增依赖都是叶子 crate，编译成本可以忽略；
   - 回收方案的选择和 storage 主题、memory 主题都不冲突。

## 5. 需要人决定的问题

1. **线程模型**：(A) 保留 OS 线程加阻塞 worker **【推荐】**；(B) 改成 async/tokio。
2. **锁的底层实现**：
   - (A) `std::sync`，和 sql-nio 一致，但没有超时；
   - (B) parking_lot 包装，保留 LatchId、超时、降级 **【推荐】**；
   - (C) 一开始就移植 ObLatch 的自旋 + futex 语义；
   - (D) B 先行，性能带不达标时在同一包装内换成 C。
   推荐 B，并同意 D 作为兜底。
3. **锁和数据的关系**：(A) 数据放进锁里，按 §3.2 规则拆字段 **【推荐】**；(B) 锁当令牌，字段用 `UnsafeCell`，照搬 C++。
4. **原子 ordering 的默认值**：(A) 一律 SeqCst，对齐后按 profile 弱化 **【推荐】**；(B) 翻译时按语义弱化；(C) 一律 AcqRel/Acquire/Release。
5. **自写无锁结构的回收方案**：(A) crossbeam-epoch **【推荐】**；(B) seize；(C) 移植 QClock + RetireStation + QSync。
6. **回收的背压语义**：(A) 加退休预算，近似保留 C++ "超过 1024 就阻塞写者"的有界内存 **【推荐】**；(B) 接受 crossbeam 的不阻塞、内存随停滞读者增长。
7. **KV cache 内存块的保护**：(A) `Arc` 引用计数 **【推荐】**；(B) `haphazard` hazard pointer；(C) 移植现有的 hazard pointer。
8. **SMART_CALL 分段栈**：(A) `stacker`，保留 10 MB 上限和 `OB_SIZE_OVERFLOW` **【推荐】**；(B) 加大线程栈，去掉分段栈，溢出深度会变；(C) 移植 `jump_call` 汇编。
   - 附带问题：Rust 栈帧通常比 C++ 大，同样的嵌套深度可能更早触到 10 MB。是否允许按实测的帧大小比例放宽上限？推荐不放宽，先测。
9. **`memory_dump` 的 `siglongjmp`**：(A) 不靠信号的探测或块登记表 **【推荐】**；(B) 保留一个 C 垫片；(C) 删除这个诊断功能。
10. **新增依赖**：(A) 同意 `parking_lot`、`crossbeam-channel`、`crossbeam-queue`、`crossbeam-epoch`、`crossbeam-utils`、`dashmap`、`arc-swap`、`stacker`、`nix`，版本按 rust-toolchain 的做法钉死 **【推荐】**；(B) 只用 std，全部自己写。
11. **死代码**（§2.3 列的 5 组，以及没有调用者的崩溃处理函数和 `do_with_crash_restore`）：(A) 确认后不翻译 **【推荐】**；(B) 照样翻译。

## 6. 对其他主题的依赖或影响

- **SQL IR 图**：
  - 计划缓存里的计划会被多个会话共享，必须是 `Sync`：建好之后只读，统计字段用原子。`a14cd837c` 就是共享计划被并发改写；
  - PX worker 共用执行上下文的数据竞争（`251569f69`、`62b9d1a81`）靠 arena 不是 `Sync` 在编译期挡住，前提是 IR 主题不给 arena 类型 `unsafe impl Sync`；
  - DTL 通道从 `ObSpLinkQueue` 换成 crossbeam-channel。
- **内存和分配器**：
  - 线程入口要安装线程级 `MemAttr`，对应原来的 `ObPageManager` 加 `CREATE_CONTEXT`；
  - 回收出来的节点经全局分配器释放，`defer_destroy` 走 `Box` 的 drop；
  - `ObConcurrentFIFOAllocator`、`ObSliceAlloc`、`ObVSliceAlloc`、`ObSmallAllocator`、对象池（`op_alloc` 等）里的无锁空闲表和 `ObDLink` 双向链表，由 memory 主题决定去留；
  - `SMART_VAR`/`HEAP_VAR` 一律放堆上，和 memory 主题的"全局 jemalloc"一致。
- **存储**：
  - keybtree 的回收方案以本主题为准（R1）；
  - `ObRowLatch` 和 MVCC 标志位走 `bitlock` 加原子；
  - `ObLockWaitMgr` 的"挂起请求、事后重投"，以及 LCL 死锁检测的时间轮和优先级，按原样翻译；
  - `TCRWLock` 有 68 处引用在 storage，依赖 `ShardedRwLatch`。
- **分层**（12 条逆向依赖和 weak 符号）：
  - `lib::Worker` 是定义在 oblib、由 observer 覆盖的虚类，改成启动时注册的钩子；
  - 本主题涉及的 weak 符号有 `alloc_worker`、`common_yield`（`worker.cpp:33-42`）、`futex_hook`、`get_max_thread_num`（`thread.cpp:498`）、`print_all_thread`；
  - `oblib::sync` 和 `oblib::thread` 必须放在最底层的 crate。
- **解析器**：如果通过 FFI 保留 C 解析器，`setjmp` 到 `longjmp` 之间不能有 Rust 栈帧，也就是语法动作不能回调 Rust，并且要确认解析器是可重入的。
- **约定**：
  - 错误映射：超时是 `OB_TIMEOUT`（-4012），栈深是 `OB_SIZE_OVERFLOW`；
  - 命名：`oblib::sync::{Latch, RwLatch, ShardedRwLatch, Monitor, BitLatch}`；
  - `TODO(port)`/`PERF(port)` 的用法：弱化 ordering 一律记为 `PERF(port)`；
  - `SAFETY:` 注释、unsafe 白名单、loom/Miri 的要求写进设计文档的约定章节。
- **C++ 与 Rust 共存期**：
  - 被 C++ 线程调用的 Rust 代码，线程局部状态（itid、worker 上下文）必须在首次使用时惰性初始化；
  - C++ latch 和 Rust 锁不能互相保护对方的数据，锁顺序检测也跨不过语言边界；
  - sql-nio 继续用 `std::sync`，不必改动。
