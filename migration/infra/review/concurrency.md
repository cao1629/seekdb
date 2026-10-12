# 对抗审查：1d 基础设施 API 文档的并发正确性与性能断崖

> - **审查对象**：`migration/infra/api/01-mem.md` … `09-parser-sys-generators.md`，重点是 01、02、03、05、07、08；`migration/infra/amendments.tsv`（77 行）。对照已签字的 `migration/RULEBOOK.md` v0.2（0.8、0.9、§2.7），以及设计审查 `migration/design/review/concurrency-performance.md`。
> - **只查一类错误**：
>   - 数据竞争；
>   - 死锁：回调或析构里的自锁、新包装引入的锁序、传 guard 的写法；
>   - 活锁与饥饿、丢失唤醒；
>   - 内存序不足：发布、seqlock、回收时钟、ARM 与 x86 的差别；
>   - 无界内存增长；
>   - 相对 C++ 的热路径断崖。
>
>   跨模块一致性见 `review/consistency.md`，行为对齐见 `review/fidelity-*.md`，本文不重复；fidelity-03 的 B1（栈余量与保护页）不再列。
> - **方法**：每条都对照基线 `7d907abfa` 的 C++（`src/` 下的 file:line）。
>   - 外部事实取自 crate 源码：parking_lot_core 0.9.11（`thread_parker/mod.rs`、`thread_parker/unix.rs`、`parking_lot.rs`、`word_lock.rs`）、loom 0.7.2（`README.md`）、arc-swap 1.7.1（`debt/fast.rs`）、dashmap 6.1.0（`lib.rs`）。
>   - 在本机（Apple M4，10 核，macOS）跑了 4 个独立探针，放在 `/Users/meizhi/.claude/jobs/3985849d/tmp/review-conc/probes/`，没有入库：
>     - `handoff.c`：ulock 与 pthread 条件变量的唤醒往返；
>     - `sb_litmus2.c`：store-buffering 试金石；
>     - `acq.c`：aarch64 上 acquire 与 seq_cst 的代码生成；
>     - `colocate.c`：锁字与数据同在一行与否的吞吐。
> - **约束**：没有构建项目，没有 git 写操作。标"估计"的数字都没有实测。
> - **引用约定**：`02:1166` 指 `migration/infra/api/02-sync.md` 第 1166 行，其余文档同理；C++ 路径省略 `src/` 前缀。

## 1. 结论

1. **核对过、不构成问题的部分**：
   - **扫描规模与 C++ 相同**：itid 的 1024×64 位图扫描（`lib/thread_local/ob_tsi_utils.cpp:73-147`）、TCRWLock 写者扫 1024×8 个槽（`lib/lock/ob_tc_ref.h:30-110`）、QClock 一行 8 个槽（`lib/allocator/ob_retire_station.h:110-112`），都与 C++ 同构。
   - **`Latch<T>` 让锁字和数据同在一行，不是退化**：探针 `colocate.c` 在 M4 上测得同行反而更快。探针照 `ObLatchMutex` 的自旋方式加锁，临界区是 6 个计数各加一，每次临界区的耗时：

     | 线程数 | 同行 | 分行 |
     |---|---|---|
     | 2 | 8.6–19.0 ns | 28.8–44.5 ns |
     | 4 | 20.5–20.7 ns | 104.5–112.7 ns |
     | 8 | 103 ns | 271–274 ns |

   - **`SCondTemp` 的 `fixed_wakeup_order` 与 C++ 一致**：8 次 trylock 都失败时丢掉这次唤醒，与 C++ 相同（`lib/lock/ob_scond.h:139-162`），02 照抄并标了 `BUG(port)`。被丢唤醒的请求，由工作线程处理完上一个请求后取走，最迟等 100 ms 的轮询（`observer/omt/ob_th_worker.cpp:217,268`）。
   - **`ObLinkHashMap::for_each` 的"快照加排序"代价可忽略**：会话表大约每秒遍历一次，每次 O(N log N)。
   - **组缓冲的 seqlock 栅栏位置正确**（08 S44）：写方推进 `readable_begin_lsn_` 之后、写数据之前 `fence(Release)`；读方拷完数据之后、复查之前 `fence(Acquire)`。
   - **keybtree 的回收在 C++20 模型下成立**：子指针的读写都是 SeqCst（`storage/memtable/mvcc/ob_keybtree_deps.h:231-237`），QClock/RetireStation 因此成立。`MemtablePin` 的 S17 协议在 SeqCst 下成立，前提是新 pin 只能由已有 pin 的持有者创建。
   - **malloc 路径上没有共享 RMW**：全局分配器是直通；`MemBudget` 的父链只挂在请求自己的上下文树上（01 S11、S13）。
   - **代价可忽略的几处**：
     - `WorkerCtx` 派发时的 `RefCell` 借用和 `Arc` 克隆，`check_status` 每 1024 行才调一次；
     - `ObSpLinkQueue` 改成 `Latch<VecDeque>`，`ObLinkQueue` 按票号分散到 512 个子队列，锁基本无竞争；
     - 会话表 `get` 多两次分片锁 RMW，C++ 也要加一次 uref。
2. **1 个高严重度问题**：K-01。锁内 `Weak::upgrade` 得到的 `Arc` 成了最后一个引用，析构又回到同一把锁，线程自锁。
   - 08 的 memstore 句柄表正是这样设计的：`for_each` 持 `lock_` 遍历 `Weak` 表，句柄析构时要取 `lock_` 摘表。
   - `lock_` 一旦被永久占住，下一次冻结、新 memtable 的首次分配都会卡住，写入停摆。
3. **5 个中严重度问题**：
   - **K-02 宿主在自己的工作线程上析构**：`Drop for Threads` 会 join 自己，进程 abort；`ObTimer` 的析构会等自己，TimerWK 永久挂起。宿主靠 Drop 停线程时，线程持有的强引用让 Drop 永远不执行，线程泄漏。
   - **K-03 KV cache 退休条件的修复（08 S47）没有配节流**：min_version 被长扫描钉住时，积压 ≥512 的槽每次 release 都要扫 1024 个槽，再遍历整条删除表。在每 200 ms 一次的 wash 窗口和 FLUSH KVCACHE 期间，KV cache 访问慢两个数量级（估计），删除表无界增长。
   - **K-04 `ObLightyQueue` 改成 `ArrayQueue` 加单个 `FutexCond`**：C++ 按票号给每个等待者一个条件变量，一次 push 只唤醒一个；照 02 的映射写就是全唤醒。所有 `ObSimpleThreadPool` 都受影响，例如数据盘的同步 IO 通道（默认 6×CPU 个线程）和 TimerWK（最多 128 个）。
   - **K-05 Dekker 式握手只靠 SeqCst 访问**：loom 0.7 把 SeqCst 访问当作 AcqRel，02、05、08 列出的 9 个 loom 用例必然误报。其中两处规格本身在模型上就不成立：futex 垫片 validate 的内存序，08 S47 的读者遍历。
   - **K-06 帧模块逐个 datum 解析存储表、每个 `TempAllocGuard` 新建 arena**：表达式逐行求值时，每行每参数多一组查表（估计 5–15 ns），每个守卫多一次 malloc/free。这组 API 在第 3 步会被 `sql/engine/expr` 的 440 个源文件照写，以后再加快路径就要改全部调用点。
4. **7 个低严重度问题**：
   - K-07 `ObLinearHashMap` 的全局计数，取代了 C++ 的 64 个分片计数；
   - K-08 `MemtablePin` 每写一行多两次共享 RMW；
   - K-09 futex 垫片在 macOS 上走 pthread 条件变量（实测每次唤醒往返多 5–15%）；
   - K-10 扩展栈段的缓存只增不减；
   - K-11 `FixedHash2` 的 16384 个分片锁每 100 ms 全扫一遍；
   - K-12 `BtreeIterator` 失败后带着 QClock 槽，却被标成 `Send`；
   - K-13 命中路径上的 `Arc` 计数。
5. **8 条缺失的规则**（第 3 节）。最值得先补的两条：
   - **M-01 锁的慢路径自锁检测**：锁字里本来就记着写者的 itid，检测不增加快路径开销；能把"传 guard 漏改"这类悄悄挂死变成带调用栈的 abort。
   - **M-02 临界区内不得发生最后一次 drop**：K-01 背后的通用规则。
6. **冻结前建议先做**：
   - 08 改写句柄表的摘除，补 M-02、M-03；
   - S47 加节流；
   - 05 写明 `ObLightyQueue` 的唤醒个数；
   - 02 与 08 写明握手点的内存序，loom 用例改用 fence 版本；
   - 07 现在就定下批量视图和临时 arena 复用的 API 形状。

## 2. 发现

| 编号 | 严重度 | 文档 | 主题 |
|---|---|---|---|
| K-01 | 高 | 08、05、02 | 锁内 upgrade 出的 `Arc` 成为最后一个引用，析构回到同一把锁自锁；memstore 句柄表会让写入停摆 |
| K-02 | 中 | 03、RULEBOOK §2.7 | 宿主在自己的工作线程上析构：`Threads` 自 join（abort）、`ObTimer` 自等（挂起），以及靠 Drop 停线程的写法失效 |
| K-03 | 中 | 08 | KV cache 退休条件修复后没有节流，长 guard 期间每次 release 都要 O(1024+N) |
| K-04 | 中 | 05、02、03 | `ObLightyQueue` 用单个 `FutexCond`，一次 push 唤醒全部空闲消费者 |
| K-05 | 中 | 02、05、08 | Dekker 式握手只靠 SeqCst 访问：loom 验证不了，futex validate 与 S47 的内存序有缺口 |
| K-06 | 中 | 07 | 逐个 datum 解析存储表、每个 `TempAllocGuard` 新建 arena，表达式逐行路径变慢 |
| K-07 | 低 | 05 | `ObLinearHashMap`：全局计数取代 64 个分片计数，读也要改分片锁字 |
| K-08 | 低 | 08 | `MemtablePin` 按回调计数，每写一行多两次共享 RMW |
| K-09 | 低 | 02 | futex 垫片在 macOS 上是 pthread 条件变量，C++ 用 `__ulock_*` |
| K-10 | 低 | 03 | 扩展栈段按线程缓存，常驻内存只增不减 |
| K-11 | 低 | 05 | `FixedHash2` 的 16384 个分片锁每 100 ms 全扫一遍 |
| K-12 | 低 | 08 | `BtreeIterator` 标成 `Send`、"不含临界区"，失败后却带着 QClock 槽 |
| K-13 | 低 | 05、07 | 命中路径上的 `Arc` 计数：`load_full`、每次执行克隆计划常量区 |

---

### K-01（高）锁内 upgrade 出的 `Arc` 成为最后一个引用，析构回到同一把锁自锁

- **文档**：
  - 08 §2.3：
    - `ObMemstoreAllocator::for_each(&self, f: &mut dyn FnMut(&AllocHandle) -> ObResult, reverse)`，注明"持 lock_"（08:209）；
    - `AllocHandle { inner: Arc<AllocHandleInner> }`，内存在最后一份 clone 析构时整体释放（08:232-235）；
    - `ObHandleList` 的表项是 `Weak<AllocHandleInner>`，"在句柄最后一份 clone 析构时摘除"，"全部修改在 `ObMemstoreAllocator::lock_` 下进行"（08:270）；
    - S24："内存在最后一份 clone 析构时归还（`arena_.free` + 摘出句柄表…）"（08:890）；
    - `owner()` 经 `Weak` 回指 memtable，"InfoLogger 和 `ob_all_virtual_memstore_allocator_info.cpp:37-48` 读它"（08:248-249）。
  - 05 的锁内回调：
    - `atomic_refactored`/`read_atomic`/`foreach_refactored`/`set_or_update` 在桶锁内执行（05:1717）；
    - `ObLinearHashMap` 的 `operate`/`insert_or_operate`/`erase_if`/`remove_if` 在分片写锁内执行（05:1742）；
    - `FixedHash2::with_node`、`ObArrayHashMap::for_each` 同样在锁内（05:1817）。
  - 05 只规定"回调不得访问同一张表"，没有规定回调里不得让某个值的最后一个引用落地。02 的 `with_locked` 同理。
- **C++ 现场**：
  - **句柄表**：
    - `ObMemstoreAllocator::destroy_handle` 先 `arena_.free`，再持 `lock_` 摘表（`storage/allocator/ob_memstore_allocator.cpp:61-77`）；`for_each` 持 `lock_` 遍历链表节点（`ob_memstore_allocator.h:142-151`）。
    - `for_each` 的调用者：冻结时的 `log_frozen_memstore_info`/`log_active_memstore_info`（`ob_memstore_allocator.h:173-185`，经 `tx_storage/ob_memstore_freezer.cpp:1421-1437`、`ob_memstore_freezer_common.cpp:258`），以及虚拟表（`observer/virtual_table/ob_all_virtual_memstore_allocator_info.cpp:96`）。
    - C++ 的表存裸指针，句柄归 memtable 所有：`destroy_handle` 只在释放 memtable 的线程上执行，碰到 `for_each` 持锁就等它做完。`for_each` 的线程从不拥有句柄，所以从不在锁内析构句柄。
  - **MDS 是同一形状**：
    - `~MdsTableImpl` 调 `unregister_from_mds_table_mgr`，后者对 `mds_table_map_` 做 `erase_if`（`storage/multi_data_source/mds_table_impl.ipp:72-80`、`mds_table_mgr.cpp:94-115`）；
    - 刷盘时 `map.operate(tablet_id, FlushOp)` 在桶锁内操作表（`mds_table_order_flusher.ipp:79,154`）；
    - 表里同样是裸指针。
- **按文档的实现**：
  - `for_each` 要把 `Weak` 升级成 `Arc`，才能交出 `&AllocHandle`；`owner()` 同样要升级 memtable 的 `Weak`。这些临时 `Arc` 在本轮回调结束、放锁之前 drop。
  - MDS 的表在 Rust 里最自然的译法是存 `Weak<MdsTableBase>`（"表不拥有"），`FlushOp` 在 `operate` 里升级。
- **为什么错**（交错）：
  1. 冻结线程 T1 进入 `for_each`，取 `lock_`，把句柄 H 的 `Weak` 升级为临时 `Arc`。此时强计数为 2：memtable 一份，T1 一份。
  2. t3m 的 GC 线程 T2 释放这个 memtable（`SMM:617`），memtable 那份 clone 被 drop，强计数降到 1。
  3. T1 的本轮回调结束，drop 临时 `Arc`，强计数归零，`AllocHandleInner::drop` 在 T1 上执行，要取 `lock_` 摘表。
     - `lock_` 是 `ObSpinLock`（latch id `MEMSTORE_ALLOCATOR_LOCK`，`ob_memstore_allocator.h:67,130`），译成不可重入的 `Latch`（02 §3.2）。T1 先自旋 2000 次（`lib/stat/ob_latch_define.h:184`），再在锁字上永久等待。
     - `lock_` 从此没人释放。
  4. 之后所有要取 `lock_` 的路径都卡住：
     - 新 memtable 的 `init_handle`（`MA.cpp:52-59`）；
     - 冻结的 `set_frozen`（`:81-85`）；
     - 新句柄的第一次分配（08 S23）；
     - `set_memstore_threshold`。
     下一次冻结时，活跃 memtable 已经冻结，新 memtable 拿不到第一块内存，**这张表的写入全部挂住**；冻结线程和虚拟表查询也一起挂住。
  - **第二条路是 `owner()` 回指**：InfoLogger 升级出 memtable 的 `Arc`，若它成了最后一个，memtable 就在 `lock_` 里析构；析构又 drop 它的 `AllocHandle`，回到第 3 步。
  - **MDS**：`FlushOp` 升级出的 `Arc<MdsTableImpl>` 若在分片写锁内成了最后一个，`Drop` → `unregister` → 对同一张表、同一个分片 `erase_if` → 自锁。
- **后果**：概率不高，后果是写入停摆。TPC-C、sysbench 写入这类频繁冻结的负载，跑得越久越容易撞上。08 的 loom 用例（L3、L11）都不覆盖"析构回到同一把锁"。
- **建议改法**：
  - **08**：`AllocHandleInner` 的析构不得同步取 `lock_`。两种做法任选：
    - 析构只置 dead 标志，由下一个持 `lock_` 的人顺手摘除；
    - `for_each` 先把升级出的 `Arc` 收进一个 `SmallVec`，放锁之后再 drop。

    `owner()` 升级出的引用同样带出锁再 drop。
  - **补规则 M-02**（第 3 节）：
    - 临界区内经 `upgrade`/`clone` 得到的 `Arc`，一律带出临界区再 drop；
    - 锁内回调的签名改成"返回要延后释放的值"，或者由容器提供 `DeferDrop` 收集器；
    - 像 MDS 这样"析构时回表注销"的对象，表里存 `Arc` 或 id，不存 `Weak`。
  - **配 M-01 的自锁检测**：违反时立即带调用栈 abort，不让它悄悄挂死。

### K-02（中）宿主在自己的工作线程上析构

- **文档**：
  - RULEBOOK §2.7 的定时任务表：不是模块的宿主（如每个 ObLS 里的 `ObLockTable`），由任务持有 `Weak`，每次运行时 `upgrade`。
  - 03 §2.4：
    - `HostRef::Shared(Arc<T>)`，"线程运行期间持有强引用"（03:219-224）；
    - `impl Drop for Threads`（03:311），注释"线程持有宿主的强引用，所以只有线程都退出后才会走到这里"；§3.4 第 2 条同样这样说（03:1044）。
  - 03 §2.7：`impl Drop for ObTimer`（03:517），即 C++ 的 `~ObTimer() → destroy()`。
  - 03 §3.7 第 9 条明说"任务在自己的 `runTimerTask` 里对自己调用 `wait_task` 会死锁"，照 C++ 保留（03:1086）。
- **C++ 现场**：
  - `~ObTimer()` → `destroy()` → `cancel_all()` 加 `wait()`；`wait()` 一直等到本定时器的令牌全部离开队列、运行集和未取消集（`lib/task/ob_timer.cpp:55-58,95-106`；`ob_timer_service.cpp:400-429`）。
  - `ObLockTable::destroy` 先 `cancel_task` 再 `wait_task`（`storage/tablelock/ob_lock_table.cpp:230-241`）；`~ObLockTable() {}`（`ob_lock_table.h:85`），成员 `ObTimer check_obj_lock_timer_` 析构时再 `destroy` 一次。
  - `Threads::~Threads()` 依次 stop、wait、destroy（`lib/thread/threads.cpp:49-54`）；wait 里 join 失败就 `ob_abort`（`thread.cpp:250-254`）。
  - 在 C++ 里，这些析构都在拥有者自己的线程上执行。任务和工作线程拿的是裸引用，从不参与析构。
- **为什么错**（三种交错）：
  1. **定时器等自己**：
     - 以 `ObLockTable` 为例：`CheckObjLockTask` 正在 TimerWK 线程上运行，手里有刚升级的 `Arc<ObLockTable>`；拆除路径没有先 `destroy`，就放掉了自己的最后一份强引用。
     - 任务返回时 drop 的就是最后一份，`ObLockTable` 在 TimerWK 线程上析构：`Drop for ObTimer` → `destroy` → `wait`。
     - 它要等的令牌就是自己这次运行。运行集要等 `runTimerTask` 返回后才清理（03 §3.7 第 7 条），于是每 10 ms 轮询一次，永不返回。
     - 结果：这个 TimerWK 线程永久挂起，宿主的内存也永不释放。
     - 正常路径上 C++ 会在 `ObLS::destroy` 里先显式 `lock_table_.destroy()`（`storage/ls/ob_ls.cpp:381`），照译时不会走到这里。会走到这里的，是拆除中途出错提前返回的路径，以及 C++ 只靠成员 `ObTimer` 的析构（`~ObTimer()` → `destroy()`）来停定时器的类。
  2. **线程 join 自己**：
     - 用 `HostRef::Shared` 交出的宿主，最后一份强引用若由池里的线程在退出闭包时放掉，`Drop for Threads` 就在这个线程上执行，`wait()` 去 join 自己。
     - `pthread_join` 返回 `EDEADLK`，std 的 unix 实现断言返回值为 0，于是 panic；在 `panic = "abort"` 下就是进程 abort。
     - 注释说的"只有线程都退出后才会走到这里"不成立：放掉最后一份引用的线程，此刻还没退出。
  3. **Drop 里停线程的代码永远不执行**：
     - C++ 惯用"析构里 stop + wait"，`~Threads()` 本身就是这样写的。
     - Rust 的线程一直持有宿主的强引用，所以宿主的 `Drop` 永远不执行，线程也就永远不停。
     - 03:1045 只要求"宿主必须照 C++ 的代码显式 stop/wait/destroy"，漏写了也发现不了。
- **后果**：
  - 情形 1 挂起一个 TimerWK 线程（上限 128），宿主泄漏；
  - 情形 2 进程 abort；
  - 情形 3 线程泄漏：按对象建的池反复创建时，线程总数会涨到 3968 的上限（03 §3.3 第 1 条）。

  三种情形都只在拆除与运行并发时出现，概率不高，但没有任何测试覆盖。
- **建议改法**：
  - `Drop for Threads`、`ObSimpleThreadPoolBase` 的 destroy、`ObTimer::destroy`/`wait`/`wait_task` 先判断当前线程是不是本池的线程、是不是正在执行本定时器的令牌。是就跳过自己：不 join 自己，等待时排除自己正在执行的令牌，并记 ERROR。代价是一次线程局部比较。
  - **补规则 M-03**：
    - 拆除者在放掉最后一份强引用之前，必须显式 `cancel+wait`（定时器）或 `stop+wait`（线程）；
    - 任务和线程闭包升级出的 `Arc`，不得成为宿主的最后一份引用；
    - CI 加一个测试钩子：任务运行期间 drop 宿主的全部外部引用，断言不挂起、不 abort。
  - 线程闭包改持 `Weak`，每轮循环 `upgrade` 一次，失败就退出。这样 C++ 那种"靠析构停线程"的写法才能照译。

### K-03（中）KV cache 退休条件修复后没有节流

- **文档**：
  - 08 S47（08:929-934）的修法：
    - 摘下整条删除表后逐个比较，`version_ < min_version` 的才释放；
    - 其余"按原顺序用 `add_nodes` 挂回"，`waiting_nodes_count_` 只减去释放的个数。
  - S47 对代价的估计只有一句："长时间持有 hazard guard 的扫描期间被删的节点要等扫描结束才释放……属于预期"。
  - S48：1024 个槽，阈值 512（08:935）。
- **C++ 现场**：
  - **每次 release 都可能触发回收**：KV cache 的 get、put、erase 都在 hazard guard 里（`share/cache/ob_kvcache_map.cpp:182-259,289-310,377-402`）。guard 析构时调 `release(slot)`：若本槽的 `waiting_nodes_count_ >= 512`，就先 `get_min_version()` 读遍 1024 个槽（`ob_kvcache_hazard_version.cpp:325-336`），再 `slot.retire(min_version)`（`:240-261`）。
  - **C++ 的 retire 不看版本**：整条删除表一起释放（`:75-104`），所以触发一次之后计数归零，要再积 512 个才会再触发。
  - **长 guard 有两种**：
    - wash：默认每 200 ms 一次（`_cache_wash_interval`，`rust/config/parameters.yaml:1306-1312`）。每次在一个 guard 里扫 `bucket_num/50` 个桶，最多 20 万（`ob_kv_storecache.h:203-207`、`ob_kv_storecache.cpp:221,524-529`、`ob_kvcache_map.cpp:511-560`）。
    - `erase_all`：在一个 guard 里扫完全部桶（`:415-457`）。`ALTER SYSTEM FLUSH KVCACHE` 走这条路，裁判的 `fts_index.cn_word` 用到了它（`cn_word.test:46`）。
  - **谁往自己的槽里攒节点**：
    - 每次 put 遍历桶时，把指向已洗掉块的节点删进自己的槽（`ob_kvcache_map.cpp:188-191`）；overwrite、erase 也是这样。
    - wash 刚洗掉一批块，紧接着的 put 删得最多，而这正是 `clean_garbage_node` 钉住 min_version 的时候。
- **为什么错**：
  - 修复之后，只要 min_version 被钉住，删除表里的节点就一个也释放不了，计数一直 ≥ 512。
  - 于是这个槽**每次** release 都要读 1024 个槽，再把整条删除表摘下、比较、挂回，代价 O(1024 + N)，N 还在涨。
  - C++ 是每积 512 个才扫一次，Rust 变成每次 guard 析构都扫一次。
- **后果**（估计）：
  - **单次 release 的代价**：1024 个槽大多被别的核写过，每读一个约 30–100 ns；再加上 N 个散落在堆上的节点。每次 release 约 50–500 µs，而一次正常的 KV get 是 0.2–1 µs。
  - **wash 窗口**：扫 20 万个桶约 5–20 ms（估计）。窗口里，已越过 512 的线程上的微块缓存、行缓存、索引块缓存访问都慢两个数量级，点查和扫描的 p99 跟着 wash 周期抖动。
  - **FLUSH KVCACHE**：持续时间更长，删除表在此期间无界增长。
- **建议改法**（不改 S47 的正确条件，只加节流）：
  - 每个槽记一个 `next_retire_at`。一次 retire 之后若还剩 r 个，就设 `next_retire_at = r + 阈值`，计数到这个值才再触发；也可以再加一个条件：自上次扫描以来，station 的 `version_` 前进过。
  - 删除表大体按版本降序排列（同一个槽的 `delete_node` 按版本递增、头插）。从头找到第一个 `version_ < min_version` 的节点，把它之后的整段一起释放，前面的不动，不必逐个摘下再挂回。
  - L9 再加一个用例：钉住 min_version 的同时，让另一个线程不停地 put，断言每次 release 扫描槽的次数有界。

### K-04（中）`ObLightyQueue` 改成单个 `FutexCond`：一次 push 唤醒全部等待者

- **文档**：
  - 05 §2.17：内部是"`ArrayQueue<T>` 加 02 号的 `FutexCond`（C++ 的 `ObLightyCond` 数组 …）"（05:1494）。
  - Q2："Rust 用 `ArrayQueue` 加条件变量：FIFO、返回码、超时都相同"（05:1769）。
  - §8 Q6 推荐这个方案，认为差别"只影响线程池统计和 `OB_SIZE_OVERFLOW` 的触发时机"（05:2352-2355）。
  - 02 的对照把 `ObLightyCond::signal()` 写成 `signal(i32::MAX as u32)`，即唤醒全部（02:619）。
  - 05 没有规定 push 唤醒几个，也没有规定消费者"先取 key、再试取、再等待"的次序。
- **C++ 现场**：
  - **按票号唤醒**：`pop` 先领票号 `seq = FAA(pop_)`，在 `cond_[seq % n_cond]` 上等；`push` 把元素放进 `seq` 号槽，只 signal 这一个条件变量（`lib/queue/ob_lighty_queue.cpp:91-133,135-143,157-165`）。`n_cond = min(1024, 不超过容量的最大 2 的幂)`，`get_cond(seq) = cond_[seq % n_cond_]`（`ob_lighty_queue.h:67-76`）。所以每个等待者有自己的条件变量，一次 push 只唤醒领了这个票号的消费者。
  - **用户**：`ObSimpleThreadPool = ObSimpleThreadPoolBase<ObLightyQueue>`（`lib/thread/ob_simple_thread_pool.h:35,47,128`），工作线程 `pop(task, 1 s)`（`.ipp:261-262`）。其中：
    - 数据盘的同步 IO 通道：`ObSyncIOChannel::submit` 每个请求 push 一次（`share/io/ob_io_struct.cpp:1116-1141`）。`sync_io_thread_count` 默认是 0（`parameters.yaml:1097-1103`），这时线程数取 6×CPU（`ob_io_struct.cpp:1224-1236`），10 核的 M4 上是 60 个；
    - TimerWK，最多 128 个（`lib/task/ob_timer_service.cpp:233-235`）；
    - 另有 `ObAsyncTaskQueue`（`lib/thread/ob_async_task_queue.h:121`）、palf 的 `LogSharedQueueThread` 等。
- **为什么错**：所有空闲消费者都在同一个 `FutexCond` 上等。push 若照 02 的映射写成 `signal(i32::MAX)`，K 个空闲线程全部醒来，只有一个拿到元素，其余 K−1 个各做一次上下文切换再睡回去。
- **后果**（估计）：
  - 每次多余的唤醒约花 2–5 µs CPU（探针 `handoff.c` 测得一次往返约 2 µs）。
  - 数据盘同步 IO 通道在一次突发之后会留下几十个空闲线程，空闲线程要 1–2 s 才收缩（03 §3.6 第 3 条）。这段时间里，每个同步 IO 请求白烧约 0.1–0.3 ms CPU；TimerWK 同理。
  - IO 密集、冷读的性能带会受影响。
- **建议改法**（两种方案选一种，写进 05 §2.17 和 Q6）：
  - 照 C++ 移植票号算法（即 Q6 的备选），每个票号槽对应一个条件变量；
  - 保留 `ArrayQueue`：
    - push 之后只 `signal(1)`，`wake_all` 只在 stop 时用；
    - 消费者固定写成：`key = get_key()`，`try_pop()` 成功就返回，否则 `wait(key, 剩余时间)`；
    - 被唤醒却没取到元素的消费者（元素被插队的消费者拿走了）重新等待，不会丢元素。
  - LM6 加一条断言：一次 push 只唤醒一个等待者。

### K-05（中）Dekker 式握手只靠 SeqCst 访问：loom 验证不了，有两处规格在模型上不成立

- **文档**：
  - **把正确性押在 SeqCst 访问上的协议**：
    - 02 U1 把 `BigReaderLatch` 的读者登记与写者扫槽称为"两边都是 SeqCst，Dekker 式握手"（02:1164），`QSyncLatch`、`Quiescence` 同理；
    - 08 U7 的 `MemtablePin`："SeqCst 下两者至少有一方看到对方（Dekker）"（08:975）；
    - 08 S47 的证明（08:931-932）。
  - **内存序没写的地方**：02 U3 的 futex 垫片只说"validate 回调只做一次原子读和比较"（02:1166）。
  - **loom 用例**：02 的 LM5、LM6、LM7、LM10、LM11（02:1214-1220），08 的 L3、L4、L9（08:1005-1011），05 的 LM6（05:1884）。
- **外部事实**：
  - loom 0.7.2 的 `README.md:70-74`："SeqCst accesses … are regarded as AcqRel … causing Loom to generate false alarms"；`fence(SeqCst)` 则是支持的。`:75-79` 还说明 loom 不探索 load buffering，所以它的结论本来就不完备。
  - 探针 `acq.c`：`-mcpu=apple-m1` 下，Acquire 读编译成 `ldapr`（RCpc），SeqCst 读是 `ldar`，SeqCst 的 FAA 是 `ldaddal`。Rust 用的是同一个 LLVM 后端。
- **为什么错**：
  1. **loom 必然误报**：
     - 上面这些协议都是"我写 A、再读 B；你写 B、再读 A"，正确性靠的是 SeqCst 访问的全序。loom 按 AcqRel 建模，就会探索到两边都没看见对方的执行，于是报互斥被破坏、释放后使用或丢失唤醒。
     - 实现者只有三条路：关掉这些用例；加锁"修好"它们，造成性能断崖；或者改弱语义去迎合测试。
     - RULEBOOK 要求这些结构配 loom 测试（RULEBOOK:376、:949），这 9 个用例按现在的写法过不了。
  2. **futex 垫片的 validate 没有规定内存序**：
     - `SimpleCond` 的唤醒协议，是 `v_` 与 `n_waiters_` 之间的 store-buffering 握手：等待者先 `FAA(n_waiters_)` 再比较 `v_`，唤醒者先 `FAA(v_)` 再读 `n_waiters_`（`lib/lock/ob_scond.h:37-61`）。
     - C++ 的比较在内核里做，`futex_wait` 入队之前有全屏障。parking_lot_core 的 `park` 在 validate 之前只有桶锁的 Acquire CAS（`parking_lot.rs:591-605`、`word_lock.rs:92`）。
     - validate 若照 parking_lot 的惯例写成 Relaxed 或 Acquire（aarch64 上是 `ldapr`），C++20 模型和 ARM 架构都允许两边都没看见对方：等待者睡下，唤醒者以为没人在等。这就是丢唤醒，请求队列上最多要等 100 ms 的轮询才恢复（`observer/omt/ob_th_worker.cpp:217,268`）。
     - 探针 `sb_litmus2.c` 在 M4 上跑了 600 万次，没有观察到这种结果；作为校准的 relaxed 版本，每 200 万次出现 1–39 次。所以这是模型上的缺口，硬件上罕见。
  3. **08 S47 的证明默认读者"之后才遍历"**：
     - 读者先读 `version_`，再 CAS 自己的槽，然后遍历桶链；释放者先读 `version_`，再扫各槽；删除者在桶写锁内摘链，然后 `FAA(version_)`（`ob_kvcache_hazard_version.cpp:61,197,221`）。
     - 若释放者扫到读者的槽时它还是空的，而读者读 `version_` 又早于删除者的 FAA，那么读者 CAS 之后对桶链的 Acquire 读与删除者的摘链之间没有 happens-before：读者仍可能读到旧指针，走到已经释放的节点。
     - C++ 这里用的是普通读写（`ob_kvcache_map.cpp:289-310`），靠的是硬件。08 自己的 L9 会碰到这个窗口，但在 loom 里它与第 1 条的误报分不开。
- **建议改法**（补规则 M-04）：
  - 每个 Dekker 握手点显式写 `fence(SeqCst)`：写方在写完自己的标志之后、读对方之前；读方同理。
    - x86 上，带 `lock` 前缀的 RMW 后面再加 `mfence`，多 20–30 个周期。
    - 握手点紧跟在 RMW 之后时，可以用 `cfg(loom)` 门控的 `oblib::sync::dekker_fence()`：生产构建为空（x86 和 ARM 上 RMW 本身已是全屏障），loom 构建为 `fence(SeqCst)`。
  - 02 U3 写明：validate 的读用 SeqCst（aarch64 上，`ldaddal` 之后的 `ldar` 按 RCsc 排序，不会越过前面的写）；`n_waiters_` 的增减用 SeqCst RMW。
  - 08 S47 写明内存序，二选一：
    - 桶链指针的写（含摘链）用 SeqCst，命中路径的读也用 SeqCst（x86 上与 Acquire 同价）；
    - 或者在 `acquire` 的 CAS 之后、`get_min_version` 的扫描之前各加一个 `fence(SeqCst)`，L9 用 fence 版本建模。
  - 第 5 节的测试清单注明：靠 SeqCst 访问的协议，在 loom 下用 fence 版本建模，另配真实线程的压力测试。
  - 修订 AM-05 把 loom 加进白名单时，一并写明 loom 不建模 SeqCst 访问、也不探索 load buffering，loom 通过不等于协议正确。

### K-06（中）帧模块逐个 datum 解析存储表、每个 `TempAllocGuard` 新建 arena

- **文档**：
  - **存储表**：
    - 07 §2.3：`StoreTable { slots: Vec<Slot { gen, entry: Option<StoreEntry> }>, … }`（07:535），`bytes(r, len)` 负责解析 DataRef（07:552）；
    - §3.1 规定每次解析都要查槽、比代号、匹配表项种类、检查 `off + len` 是否越界（07:1326-1333）。
  - **逐元素访问**：`ObEvalCtx::datum_at(v, i)`、`view_at(v, i)` 每个元素单独解析（07:879-880）；`batch_eval_map` 逐行借出 N 个参数的视图（07:856-860）。
  - **文档自己的取舍**：§4 和 8.10 承认"批量循环里按值解码、每次带边界检查，比 C++ 慢"，先标 `PERF(port)`，"对齐之后有 profile 证据再加批量视图"（07:1424,1621-1624）。
  - **临时 arena**：`TempAllocGuard { alloc_: Arena }`，"每个守卫自带一个新的 Arena，drop 时释放"（07:820-823；§3.9 第 2 条，07:1389；8.5 与修订 AM-28）。
- **C++ 现场**：
  - datum 本身就是指针，`datums[i].ptr_` 直接解引用。
  - `TempAllocGuard` 共用求值上下文的临时 arena。release 构建里，只在用量超过 `OB_MALLOC_MIDDLE_BLOCK_SIZE` 时才 `reset_remain_one_page`（`query/api/query/engine/expr/ob_expr.h:170-178,260-271`），所以一个守卫几乎不花钱。
  - 守卫多半出现在逐行求值里：`sql/engine/expr` 下有 185 个文件用到它（全仓 374 行）。例如 `ob_expr_like.cpp:1057` 在批量循环里每行建一个守卫。
- **为什么是断崖**：
  - **查表**：每读一个 datum 的载荷，先要读帧里 12 字节的记录（一次存储表解析），再解析载荷的 DataRef（又一次）。每次解析是 3–6 次相互依赖的读，加 4–5 个分支，而 C++ 只读一次。估计每行每参数多 5–15 ns；C++ 向量化的算术和比较约每行 1–2 ns，所以算子内核会慢数倍。
  - **临时 arena**：每个守卫的第一次分配都要从 jemalloc 要一块，drop 时还回去；即使命中 tcache，估计每行也多 20–50 ns。
  - **"以后再加快路径"的代价**：`view_at`、`datum_at`、`bytes` 的形状在第 3 步会被 `sql/engine/expr` 的 440 个源文件照写（`ls src/sql/engine/expr/*.cpp`），以后换成批量视图就要改全部调用点。
- **后果**：分析型、表达式密集的查询（大范围扫描加过滤、对文本列做 `LIKE`、类型转换），以及性能带里带表达式的扫描用例。
- **建议改法**（现在就定下 API 形状，内部仍然可以全部是安全代码）：
  - 增加 `ObEvalCtx::batch(v: ObDatumVector, size) -> DatumBatch<'_>`：
    - 构造时把记录所在的存储表项解析一次，得到 `&[u8]`；逐行读记录时只做一次切片边界检查；
    - 解析载荷时带一项缓存：同一批里的 DataRef 绝大多数指向同一个 res buf、DRB 或 arena 块，store id 相同就复用上次解析出的切片；
    - `batch_eval_map` 和 `view_at` 内部都走它。
  - `TempAllocGuard::new` 从线程局部的空闲表取一个已 reset 的 `Arena`，没有就新建；drop 时 `reset_remain_one_page` 后放回，与 C++ 的复用节奏相同。它仍然不借用 ctx，8.5 推荐的理由不受影响。

### K-07（低）`ObLinearHashMap`：全局计数取代 C++ 的分片计数，读也要改分片锁字

- **文档**：
  - 05 LN6："C++ 汇总 64 个分片计数；Rust 原子计数"（05:1746）；
  - LN8：读操作持分片读锁（05:1748）；
  - 分片数取 `max(16, next_pow2(4 × CPU 数))`（05:1209-1210）。
- **C++ 现场**：
  - 计数：64 个 `CACHE_ALIGNED` 的计数器按线程号取模（`lib/hash/ob_linear_hash_map.h:284-300,800-845`），每次 insert/erase 只写本线程那一个（`add_cnt_`，`:1903,1967,2021,2152,2219,2234-2238`）。
  - 读：只锁目标桶的标志位（`:1290-1320`），不同的桶互不相干。
- **为什么是退化**：
  - `RowHolderMapper` 每写一行插入一次，提交时再删除一次（`storage/memtable/ob_memtable.cpp:2088-2092`、`ob_lock_wait_mgr.cpp:128-154`）。Rust 版每次都对同一个全局计数做 RMW，所有写线程争同一条缓存行，估计每次 50–200 ns。
  - 读要对分片锁字做两次 RMW，同一分片的读者互相抢这条缓存行；C++ 的桶锁则按桶分散。
- **建议**：
  - 计数照 C++ 用 64 个 `CacheAligned<AtomicI64>`，按 `get_itid() % 64` 选，`count()` 求和；
  - 每个分片的锁放在独占的缓存行上。

### K-08（低）`MemtablePin` 按回调计数：每写一行多两次共享 RMW

- **文档**：08 §2.2：`register_callback()` 对 16 个分片之一 `fetch_add`；pin 析构时，在原来那个分片上 `fetch_sub`（08:154-176；S17，08:880）。
- **C++ 现场**：回调存裸指针，没有引用计数（`CB.h:538-544`）。一个事务碰过哪些 memtable，已经由 `ObMemtableSet` 记着（`TCL.cpp:444`）。
- **代价**（估计）：
  - 每写一行，注册时做一次 SeqCst RMW；提交或回滚时析构回调又做一次，而且常常在别的线程上，要把缓存行抢回来。
  - 只有 16 个分片，写线程多于 16 个时还会共用分片。
  - 每行多 50–200 ns，占 TPC-C、sysbench 写入每行开销的百分之几。
- **建议**：pin 按"回调链表 × memtable"持有，与 `ObMemtableSet` 同粒度，每个事务对每个 memtable 只计一次；回调借用链表持有的 pin。S17 的协议本身不变。

### K-09（低）futex 垫片在 macOS 上是 pthread 条件变量

- **外部事实**：
  - parking_lot_core 0.9.11 在 Linux 上用 futex，在其他 unix（含 macOS）上用 `thread_parker/unix.rs`，即每个线程一对 pthread mutex 和 cond（`thread_parker/mod.rs:53-58`、`unix.rs:28,118,172-178`）。
  - 每次 park/unpark 还要取一次全局哈希表桶的 WordLock（`parking_lot.rs:358-366`）。
  - C++ 在 macOS 上直接用 `__ulock_wait`/`__ulock_wake`（`lib/lock/ob_futex.cpp:24-84`）。
- **实测**（`handoff.c`，M4，2 万次往返）：ulock 每次往返 1.80–2.02 µs，pthread 条件变量 2.04–2.15 µs，多 5–15%。
- **影响**：
  - 每一次有竞争的 latch 交接、请求队列的唤醒（`SCondTemp`）、线程池的唤醒都多一点；
  - 裁判的基准平台正是 macOS arm64。
- **建议**：
  - futex 垫片按平台直接调用 C++ 用的那个系统调用：Linux 用 `futex`，macOS 用 `__ulock_*`，Windows 用 `WaitOnAddress`；
  - parking_lot_core 只作其余平台的后备；
  - 需要的 unsafe 在 `oblib::sync` 白名单内，约 6 处。

### K-10（低）扩展栈段按线程缓存，常驻内存只增不减

- **文档**：03 §3.11 第 6 条：扩展栈段每线程缓存，线程退出时才释放（03:1129）；Q8 推荐全部缓存，每线程最多 4 段（03:1614）。
- **C++ 现场**：
  - 每次扩栈都 `ob_malloc`，返回时 `ob_free`（`lib/allocator/protected_stack_allocator.cpp:69-72,95-135`）；
  - jemalloc 配的是 `dirty_decay_ms:1000`（01:934），约 1 秒后把脏页还给系统。
- **后果**（估计）：
  - ReqWorker 只要有负载就长期存活（空闲 10 s 才退出），每个用过扩展栈的线程都一直占着它碰过的页。
  - 一次硬解析约碰 0.1–0.5 MB，150 个线程就是 15–75 MB；跑过深嵌套的用例之后，最坏是 150 × 4 × 2 MB ≈ 1.2 GB。
  - 会影响性能带里的内存一项。
- **建议**：
  - 每线程只缓存 1 段，多出来的段一归还就释放；
  - ReqWorker 空闲超过 1 s 时，这 1 段也释放；
  - fidelity-03 B1 要给缓存段加保护页，两件事一起做。

### K-11（低）`FixedHash2` 的 16384 个分片锁每 100 ms 全扫一遍

- **文档**：05 §2.14：分片数 = `limit`，每个分片一把 `Latch` 加一个有序表；`quick_next` 逐分片往下走（05 §2.14、§3.10 F2）。
- **C++ 现场**：`check_timeout` 每 100 ms 跑一次（`storage/memtable/ob_lock_wait_mgr.cpp:172-195`），沿一条链表走过 16384 个哑节点（`ob_lock_wait_mgr.h:184,333`），全程只读。
- **代价**（估计）：
  - Rust 每轮要加锁、解锁 16384 次，即 3.2 万次 RMW，碰到 1–2 MB 的锁所在的缓存行（`Latch` 按 64/128 字节对齐）。
  - 每轮约 1–3 ms，约占一个核的 1–3%；还会与正在进入锁等待的行锁请求抢分片锁。
- **建议**：
  - 维护一张非空分片位图（256 个 `AtomicU64`），`quick_next` 只看非空分片；
  - 或者把分片锁换成不对齐的 `RwLatch` 或 bitlock。

### K-12（低）`BtreeIterator` 标成 `Send`、"不含临界区"，失败后却带着 QClock 槽

- **文档**：
  - 08 S34："find_path 失败时 scan_batch 不 reset，槽位要到 BtreeIterator::reset/析构才释放"，照抄（08:906）；
  - §4 又把 `BtreeIterator` 列为"Send：不含临界区"（08:984）。
- **C++ 现场**：
  - `scan_batch` 在 `set_key_range` 失败时直接返回，不调 `iter_->reset()`（`storage/memtable/mvcc/ob_keybtree.cpp:1212-1244`）；
  - QClock 槽是在 `acquire_ref` 时取得的（`:231-236`）。
- **后果**：
  - 这样的迭代器若被搬到别的线程，或者长期留在游标、结果集里，它占着的 QClock 槽会让所有退休超限的写者在 `wait_quiescent` 里一直 PAUSE 自旋（`lib/allocator/ob_retire_station.h:65-72,217-224`），memtable 写入停住。
  - C++ 也有这个缺口，但 C++ 的迭代器出错后很快就析构；Rust 把它标成 `Send`，长期保存看起来就是合法的。
- **建议**：`scan_batch` 失败时同样调 `iter_->reset()`，标 `FIXED(port)`。`is_iter_end_` 此时已经置位，迭代器不会再被使用，所以行为不可观察。改了之后，"`Send`、不含临界区"的说法才成立。

### K-13（低）命中路径上的 `Arc` 计数

- **内部同步容器的 `ArcSwapOption`**：
  - 05 §2.0 建议把可重建的内部存储放在 `ArcSwapOption<Inner>` 里，"在途操作持有的旧 `Arc`"（05:185）。
  - 实现者若用 `load_full()`，每次 get/set 都对 `Inner` 的计数做两次共享 RMW；用 `load()` 拿 guard，则没有共享写。
  - arc-swap 每个线程只有 8 个 debt 槽（`debt/fast.rs:23`），同一线程同时持有超过 8 个 guard 时也会退回到计数。
  - 应当规定：一律用 `load()`，guard 不得跨回调或长期持有。
- **每次执行克隆计划常量区**：
  - 07 的 `register_plan_stores(Arc<[Box<[u8]>]>)`（07:541）每次执行都克隆一次计划常量区的 `Arc`。
  - C++ 只对计划本身计数，Rust 多出一条共享缓存行上的 RMW。sysbench 点查这类所有线程执行同一计划的负载会争这条缓存行，估计每条语句多 0.1–0.2 µs。
  - 存储表可以直接借用计划（`&'plan`），或者让计划常量区只在计划缓存的引用计数保护下借出。

## 3. 缺失的规则

| # | 构造 | 计数与例子 | 后果 | 建议补的规则 |
|---|---|---|---|---|
| M-01 | 锁的自锁检测 | 锁字里本来就记着写者的 `itid+1`（02 G2，02:979）。会改成"传 guard"的地方：`ObRecursiveMutex` 21 处、条件重入 7 处（02:1107-1108）。此外还有警告缓冲区（04:1699，只有 `debug_assert!`）、`with_locked` 回调，以及 K-01 的析构 | 漏改的路径在 release 构建里悄悄挂死，现场只看得到一个永远在等锁的线程 | `Latch`/`RwLatch` 的写锁、`BigReaderLatch` 的写锁，在 CAS 失败后的慢路径上，比较锁字里的写者与自己的 `itid+1`；相等就记 ERROR、打调用栈、`ob_abort`，与 `OB_ASSERT` 同一态度。快路径不多一条指令。读锁之后再读（FIFO 下有写者排队）无法检测，照 C++ 在文档里写明 |
| M-02 | 临界区内的最后一次 drop | 见 K-01；05 的锁内回调（05:1717,1742,1817）；`erase_if`/`remove_if`/覆盖写在锁内丢弃旧值；02 的 `with_locked` | 宿主的析构若再取同一把锁就自锁；取别的锁就引入 C++ 没有的锁序 | 锁内拿到的 `Arc`（upgrade 或 clone 得来）一律带出锁再 drop。容器的删除和覆盖把旧值交回调用方，不在锁内析构：`erase_if` 返回 `Option<V>`，`insert_or_update`、`set_refactored(flag=1)` 交回旧值。DashMap 的 `retain` 不用于析构有副作用的值。"析构时回表注销"的对象，表里不存 `Weak` |
| M-03 | 宿主与工作线程的拆除次序 | 见 K-02；RULEBOOK §2.7 的 `Weak` 宿主、03 的 `HostRef::Shared` | 自 join 导致 abort；自等导致挂起；线程泄漏 | 拆除者先 `cancel+wait` 或 `stop+wait`，再放最后一份强引用；`Drop for Threads`、`ObTimer::wait`/`wait_task` 识别"本线程就是本池的线程"或"本线程正在跑本定时器的令牌"，跳过自己；线程闭包持 `Weak` |
| M-04 | Dekker 握手与 loom | 见 K-05：02 的 U1、U3，08 的 U7、S47；9 个 loom 用例 | loom 误报；validate 的内存序有缺口 | 握手点写 `fence(SeqCst)`，或者用 `cfg(loom)` 门控的 `dekker_fence()`；每个"一写一读、跨两个地址"的协议，在文档里写明内存序，并说明 loom 怎么建模 |
| M-05 | C++ 里普通 `++` 的统计计数 | KV cache 命中路径上的 `recent_get_cnt_`、`get_cnt_`（`share/cache/ob_kvcache_map.cpp:304-305`）；C++ 刻意分片的计数，如 `ObLinearHashMap` 的 64 个计数器、`ObPCNonAtomicCounter`（`ob_kvcache_struct.h:190`） | 按 RULEBOOK §2.7 改成原子量之后，最自然的写法是 `fetch_add`，多一次带 lock 前缀的 RMW；分片计数被合成一个（K-07） | 纯统计、允许丢计数的 `++`，写成 `x.store(x.load(Relaxed) + 1, Relaxed)`，代价和语义都与 C++ 相同；C++ 分片的计数保持分片、按缓存行对齐 |
| M-06 | 每个 `FutexCond` 使用者的唤醒个数与等待次序 | `ObLightyQueue`（K-04）、`LightyQueue`、`SCondTemp`、`ObCond`、ob_ex_rpc | 全唤醒导致惊群；"先检查条件、再取 key"会丢唤醒 | 每个使用者写明用 `signal(1)` 还是全唤醒；等待一律按"先 `get_key()`、再检查条件、再 `wait(key)`"的次序 |
| M-07 | 每线程缓存的上限与回收 | 扩展栈段（K-10）；hazard pointer 槽的线程缓存（08 S53，`HD.cpp:144-270`）；`ObDITls` | 常驻内存随线程数和历史峰值增长 | 每种线程局部缓存写明上限和空闲时释放的时机，并配一个"缓存总量"的诊断读数 |
| M-08 | 只存 `Weak` 的登记表 | `MemBudget::register`（01:498）、`ObHandleList`（08:270） | 登记表若只在遍历时剪枝，按请求或按对象登记的条目会无界堆积（每个 `Weak` 都留着 `Arc` 的那块分配）；析构时若去取登记表的锁，就会碰上 K-01 | 登记只用于进程内个数有界的对象；插入时顺手剪掉已失效的 `Weak`；析构不取登记表的锁，改为打标记、惰性摘除 |
