# 03 `thread-signal`：线程、栈、信号、`arch` 与 `main` 的开头

> - **依据**：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）的 0.5（进程级状态、`main` 的开头、线程局部状态、外部线程）、0.8（线程、栈、信号）、§2.7 的"线程池子类""定时任务"两组行、§3 白名单第 4–6 项；阶段规则见 `migration/infra/README.md`。基线 `7d907abfa`，工作区的 `src/` 就是基线。
> - **事实来源**：调查 `migration/design/survey/concurrency.md`、`layering.md`；审查 `migration/design/review/concurrency-performance.md`（F8、F14、F16、F17、M12、M13）、`behavioral-fidelity.md`（F3、F4）、`verification.md`（C16、C17、C27、C29）。其余结论都是现读源码得出的，给出 file:line。
> - **计数口径**：除特别注明外，在 `src/` 的 `*.h`/`*.cpp`/`*.ipp`/`*.c` 上用 `grep -rn` 数（含注释和宏定义行，所以是上限）。
> - **状态**：草稿，等两名审查者审查后冻结。需要人决定的问题在 §8.1；RULEBOOK 本身需要修订的 7 处在 §8.2，已追加到 `migration/infra/amendments.tsv`。

## 1. 范围

### 1.1 单元与处置

单元是 RULEBOOK §4 的头文件组（`migration/depmap/units/units.tsv` 的 `unit` 列）。`replace:*` 的单元由 1d 在目标路径写只含 `pub use` 的桩文件；`translate` 的单元第 3 步照常翻译，翻译时调用本文的 API；`delete` 的单元交缺口清单第 16 类确认。

| # | 单元 | 内容 | 处置 | 去向与依据 |
|---|---|---|---|---|
| 1 | `src/oblib/lib/thread/thread` | `lib::Thread`（每个 OS 线程一个对象）；weak 的 `get_max_thread_num`（从未覆盖，返回 4096） | `replace:thread` | `Thread`、`CurrentThread`（§2.4）；`thread.h:73-146`、`thread.cpp:42-501` |
| 2 | `src/oblib/lib/thread/threads` | `lib::Threads`、`ObPThread` | `replace:thread` | `Threads`、`Runnable`、`HostRef`、`IRunWrapper`（§2.4）；`threads.h:34-161`、`threads.cpp:27-323` |
| 3 | `src/oblib/lib/thread/thread_pool` | `using ThreadPool = Threads` | `replace:thread` | `pub type ThreadPool = Threads;`（`thread_pool.h:24`） |
| 4 | `src/share/ob_thread_pool` | `using ObThreadPool = lib::Threads` | `replace:thread` | 桩在 share：`pub type ObThreadPool = oblib::Threads;`（`ob_thread_pool.h:24`） |
| 5 | `src/oblib/lib/thread/ob_pthread` | `ob_pthread_create/join/tryjoin_np/get_pth` | `replace:thread` | §2.4；`ob_pthread.cpp:22-82`。`ob_pthread_get_pth` 在 `src/` 其余地方 0 处使用，不提供 |
| 6 | `src/oblib/lib/thread/ob_reentrant_thread` | `share::ObReentrantThread`、`BLOCKING_RUN_IMPLEMENT` | `replace:thread` | §2.5；`ob_reentrant_thread.h:29-93`、`.cpp:29-206`；子类 3 个（`ob_async_task_queue.h`、`ob_rs_reentrant_thread.h`、`ob_server_thread_helper.h`） |
| 7 | `src/oblib/lib/thread/ob_simple_thread_pool` | `ObSimpleThreadPoolBase<Q>`、`QueueTypeMap`、两个别名 | `replace:thread` | §2.6；`ob_simple_thread_pool.h:35-129`、`.ipp:30-293`；子类 19 个（`grep -rnE "public\s+(common::)?(ObSimpleThreadPool\|ObLinkQueueThreadPool)\b"`） |
| 8 | `src/oblib/lib/thread/ob_dynamic_thread_pool` | `ObSimpleDynamicThreadPool`、`ObSimpleThreadPoolDynamicMgr`；`ObDynamicThreadPool`（死） | `replace:thread` | §2.6；`.h:95-143`、`.cpp:351-459`。`ObDynamicThreadPool`/`ObDynamicThreadTask` 在 `src/` 其余地方 0 处使用（standby 的 4 个文件只 include 了头文件），不提供 |
| 9 | `src/oblib/lib/thread/ob_adaptive_worker_pool` | CRTP `ObAdaptiveWorkerPool<Derived>` | `replace:thread` | §2.6；`ob_adaptive_worker_pool.h:63-123` |
| 10 | `src/oblib/lib/thread/ob_link_task` | `struct LinkTask : ObLink` | `replace:thread` | 桩为空：链表队列的任务改成 `QueueTypeMap::TaskType`（§2.6），侵入式链节点随之消失 |
| 11 | `src/oblib/lib/thread/ob_thread_name` | `set_thread_name` 两个重载 | `replace:thread` | §2.3；`ob_thread_name.cpp:31-69` |
| 12 | `src/oblib/lib/thread/protected_stack_allocator` | `ProtectedStackAllocator`、`StackMgr`、`ObStackHeaderGuard` | `replace:thread` | 线程栈交给 `std::thread::Builder`（§3.3 第 2 条）；扩展栈段在 `oblib::thread::stack`（§2.11）。`StackMgr` 只插入、删除，没有读者（`g_stack_mgr` 在 `src/` 其余地方只经 `main.cpp:664` 的 `ObStackHeaderGuard` 出现），不提供；`ObStackHeaderGuard` 是空类型 |
| 13 | `src/oblib/lib/thread/ob_thread_hook` | macOS/Windows 的 `futex_hook`、`ob_pthread_cond_timedwait_us`、`ob_epoll_wait` | `replace:sync` | 前两者是锁原语，归 02（RULEBOOK §2.7 的 `FutexCond`、`Monitor`）；`ob_epoll_wait` 在 `src/` 其余地方 0 处使用 |
| 14 | `src/oblib/lib/thread/ob_queue_thread` | `ObCond`（在用）、`S2MQueueThread`/`M2SQueueThread`（死） | `replace:sync` | `ObCond` 只被 `src/share/schema/ob_ddl_trans_controller.h:74` 使用，是 mutex+cond 加粘性标志（`ob_queue_thread.cpp:29-112`），归 02（§8 Q11）；两个队列线程在 `src/` 其余地方 0 处使用 |
| 15 | `src/oblib/lib/thread/ob_balance_filter` | `ObBalanceFilter` | `delete` | 只被 `S2MQueueThread` 使用（`ob_queue_thread.h:134`） |
| 16 | `src/oblib/lib/thread/ob_priority_scheduler` | `ObPriorityScheduler` | `delete` | 同上（`ob_queue_thread.h:77`） |
| 17 | `src/oblib/lib/thread/ob_map_queue_thread_pool` | 死代码 | `delete` | RULEBOOK 0.8 已列 |
| 18 | `src/oblib/lib/thread/ob_map_queue` | 只被 #17 使用 | `delete` | `ob_map_queue_thread_pool.h:20` |
| 19 | `src/oblib/lib/thread/ob_test_util` | 测试辅助 | `delete` | 0 个 include 者 |
| 20 | `src/oblib/lib/thread/ob_async_task_queue` | `ObAsyncTask`、`ObAsyncTaskQueue` | `translate` | 普通的 `ObReentrantThread` 子类（§6 例 3） |
| 21 | `src/oblib/lib/thread/ob_dedup_queue` | `ObDedupQueue` | `translate` | 普通的 `ThreadPool` 子类 |
| 22 | `src/oblib/lib/thread/ob_thread_lease` | `ObThreadLease` | `translate` | 纯原子状态机，按 RULEBOOK §2.7 的原子规则翻译 |
| 23 | `src/oblib/lib/task/ob_timer` | `ObTimer` | `replace:thread` | §2.7；`ob_timer.h:28-78`、`.cpp:30-214` |
| 24 | `src/oblib/lib/task/ob_timer_service` | `ObTimerTask`、`TaskToken`、`ObTimerTaskThreadPool`、`ObTimerService`、weak 的 `lib_server_runtime_dispatch` | `replace:thread` | §2.7；`ob_timer_service.h:41-203`、`.cpp:23-689` |
| 25 | `src/oblib/lib/task/ob_timer_monitor` | `ObTimerMonitor`（含唯一的 seqlock） | `replace:thread` | §2.7；`ob_timer_monitor.h:28-91`、`.cpp:24-227` |
| 26 | `src/oblib/lib/thread_local/ob_tsi_utils` | itid | `replace:thread` | §2.2；`ob_tsi_utils.h:36-91`、`.cpp:25-147` |
| 27 | `src/oblib/lib/thread_local/ob_tsi_factory` | `GET_TSI0`/`GET_TSI`/`GET_TSI_MULT0`/`GET_TSI_MULT` | `replace:thread` | §2.10；`ob_tsi_factory.h:26-29` |
| 28 | `src/oblib/lib/stat/ob_di_tls` | `ObDITls<T, tag>`、`is_thread_in_exit` | `replace:thread` | §2.10；`ob_di_tls.h:33-120` |
| 29 | `src/oblib/lib/thread_local/thread_buffer` | `ThreadSpecificBuffer`、`get_tc_buffer` | `delete` | `src/` 其余地方 0 处使用 |
| 30 | `src/oblib/lib/coro/co_var` | `RLOCAL*` 宏、`ByteBuf<N>` | `replace:thread` | 宏按 RULEBOOK §2.13 的 `thread_local!` 规则译，`ByteBuf<N>` → `[u8; N]`；桩只有说明（§2.10） |
| 31 | `src/oblib/lib/core_local/ob_core_local_storage` | `ObCoreLocalStorage<T>` | `translate` | 只被 `ObCoreLocalPartitionAuditInfo`（`src/storage/tx/ob_trans_define.h:998`）使用；数组元素用 02 的 `CacheAligned<T>`，下标用本文的 `get_itid()`（`ob_core_local_storage.h:183-186`） |
| 32 | `src/oblib/lib/worker` | `lib::Worker`、`THIS_WORKER`、`this_worker()`、`DisableSchedInterGuard`、`ErrsimModuleGuard`、`LogReductionMode`、`ObRuntimeContext`、`ObExtraRpcHeader` | `replace:thread` | §2.8。`ObRuntimeContext`、`ObExtraRpcHeader` 在 `src/` 其余地方 0 处使用，不提供 |
| 33 | `src/oblib/lib/profile/ob_trace_id` | `ObCurTraceId`、`TraceId`、`SeqGenerator`、`ObTraceIdGuard` | `replace:thread` | §2.9。`get_trace_id()` 返回线程局部对象的指针，要按所有权改签名（约 159 处调用；layering 调查 §2.7 的 388 处是 `ObCurTraceId::` 的全部用法，本文重数为约 389），所以必须由 1d 定 API；04 的草稿把它记为 `translate`，见 §8 Q12 |
| 34 | `src/oblib/lib/utility/ob_smart_call` | `SMART_CALL`、`SMART_CALL_LARGE`、`CALL_WITH_NEW_STACK`、`jump_call`、`all_stack_size` | `replace:thread` | §2.11；`ob_smart_call.h:28-138`、`.cpp:28-85` |
| 35 | `src/oblib/lib/utility/ob_common_utility` | `check_stack_overflow` 两个重载、`get/set_stackattr`、`get_reserved_stack_size`、`ObFatalErrExtraInfoGuard`、`ObBasicTimeGuard`、`get_cur_ts` | `replace:thread` | §2.10、§2.11；`ob_common_utility.h:28-172`、`.cpp:32-199` |
| 36 | `src/oblib/lib/utility/ob_smart_var` | `SMART_VAR`/`HEAP_VAR` | `replace:mem` | 归 01：RULEBOOK 0.7、§2.4 一律放堆上，原来对 `check_stack_overflow(size + 32K)` 的依赖（`ob_smart_var.h:47-48`）随之消失 |
| 37 | `src/oblib/lib/signal/ob_signal_struct` | `ObSignalHandlerGuard`、`signal_handler_t`、`g_rlimit_core`、`ObSqlInfoGuard`、`SQL_INFO_GUARD` | `replace:signal` | §2.13 |
| 38 | `src/oblib/lib/signal/ob_signal_handlers` | `get_signal_handler`、`install_ob_signal_handler`（死）、`ob_signal_handler`、`coredump_cb`（死） | `replace:signal` | 只提供 `ob_signal_handler`（`memory_dump.cpp:549,574` 把它传给 `ObSignalHandlerGuard`）；RULEBOOK 0.8 已把 `install_ob_signal_handler` 列为死代码 |
| 39 | `src/oblib/lib/signal/ob_signal_utils` | `do_with_crash_restore`（死）、`crash_restore_handler`、`safe_parray` | `delete` | 只被死代码使用；RULEBOOK 0.8 已列 `do_with_crash_restore` |
| 40 | `src/oblib/lib/signal/ob_libunwind` | C 文件，`safe_backtrace` | `delete` | 只被 #39 和 `coredump_cb` 使用；RULEBOOK 0.14 libunwind 改用 `backtrace` |
| 41 | `src/observer/ob_signal_handle` | `observer::ObSignalHandle` | `translate`（observer） | 业务部分调 `ObServer`、`OB_LOGGER` 等上层，留在 observer；平台相关的全部改调 `oblib::signal`（§2.13、§6 例 13） |

### 1.2 不成单元、但由本组提供的函数级内容

| C++ | 内容 | 本组提供 |
|---|---|---|
| `src/oblib/lib/ob_define.h:1875-1971` | `get_tid_cache`、`reset_tid_cache`、`ob_syscall_gettid`、`ob_gettid`、`ob_get_tname`、`ob_get_origin_thread_name`、`ob_get_extended_thread_name`、`ob_get_tname_v2`、宏 `GETTID`/`GETTNAME`/`GETTNAME_V2`/`gettid` | `oblib::thread` 的同名函数和小写宏（§2.3）。`ob_define.h` 单元的其余内容不归本组；它的译文对这些名字只写 `pub use crate::thread::…` |
| `src/oblib/lib/alloc/memory_dump.cpp:40-143` | `DumpSignalGuard::do_with_segv_catch`：装 SIGSEGV/SIGABRT 处理函数后 `sigsetjmp`，越界读时 `siglongjmp` 回来；三处使用：`find_chunk`（`:366-398`）、按块统计（`:616-653`）、按块打印（`:766-791`） | `oblib::signal::probe_read_memory`（§2.13、§3.12 第 8 条）；该单元由其所属模块（01）决定去留和翻译 |
| `src/oblib/lib/utility/ob_backtrace.cpp:37-111` | `light_backtrace` 读 rbp/x29 后沿帧指针链回溯 | 读帧指针和有界的链上读取：`oblib::arch::{frame_pointer, walk_frame_pointers}`（§2.14）。`lbt`/`light_backtrace` 本身归 04 |
| `src/observer/main.cpp:645-889` | `main`、`inner_main` | `translate`（目标 `src/ob_main.rs`，RULEBOOK §4）；二进制入口 `rust/port/observer/src/bin/seekdb.rs` 由脚本按 §2.15 的模板生成 |
| `src/observer/ob_server.cpp:1991-1997` | 原生栈大小公式 | `translate`，写本组的 `global_thread_stack_size`（§2.4、§3.11 第 7 条） |
| `src/sql/ob_sql_utils.cpp:1964-1970` | `check_stack_overflow_c`（C 解析器回调） | 09 的 `parser` 蹦床调用本组的 `check_stack_overflow`（§6 例 11） |

`src/oblib/lib/time/*`（`ObTimeUtility`、`ObClockGenerator`、`ObSysTime`、`ob_tsc_timestamp`）不在本组范围，但本组的实现离不开它们，见 §8 Q5 和修订 R6。

### 1.3 39 处内联汇编

RULEBOOK §2.13 说"内联汇编（39 处）"。核对：在 `migration/depmap/nodes.tsv` 的文件上用 `grep -nE '__asm__|\basm\s*\(|\basm\s+volatile|__asm\b'` 去掉注释行得 41 行，其中 `ob_smart_call.cpp:65-66` 两行是 `register … __asm__("x0")` 寄存器绑定、不是汇编语句，余 39 条，与 RULEBOOK 一致。

| 文件:行 | 指令 | 平台 | Rust 去向 |
|---|---|---|---|
| `src/oblib/lib/atomic/ob_atomic.h:33,36` | `pause` / `yield`（`PAUSE()`） | x86-64 / aarch64 | 02 的 `oblib::sync::pause()`，内部调本组的 `oblib::arch::pause()`（RULEBOOK §2.7、复核 C29） |
| `src/oblib/lib/atomic/ob_atomic.h:29` | 空 asm + `memory`（`__COMPILER_BARRIER`） | 全部 | `core::sync::atomic::compiler_fence(SeqCst)`（RULEBOOK §2.7），不经 `arch` |
| `src/oblib/lib/utility/ob_tracepoint.h:49` | 同上（`TP_COMPILER_BARRIER`） | 全部 | 同上 |
| `src/oblib/lib/atomic/atomic128.h:37,50,74,84` | `cmpxchg16b`、`ldaxp`/`stlxp`（128 位 CAS/LOAD） | x86-64 / aarch64 | LSN 分配器用 `portable_atomic::AtomicU128`（RULEBOOK 0.9），`REACH_COUNT_PER_SEC` 用 `Latch`；不经 `arch` |
| `src/oblib/lib/time/ob_tsc_timestamp.h:32,38,44,70,77,83` | `rdtsc`、`rdtscp`（含 i386 的 `.byte 0x0f,0x31`） | x86 | `oblib::arch::{rdtsc, rdtscp, rdtscp_id}`（内部用 `core::arch::x86_64::{_rdtsc, __rdtscp}`） |
| `src/oblib/lib/time/ob_tsc_timestamp.h:93` | `mrs cntvct_el0` | aarch64 | `oblib::arch::{rdtsc, rdtscp}` 在 aarch64 上读 `cntvct_el0`（与 `ob_tsc_timestamp.h:90-99` 相同）；也直接提供 `cntvct()` |
| `src/oblib/lib/time/ob_tsc_timestamp.cpp:166` | `mrs cntfrq_el0` | aarch64 | `oblib::arch::cntfrq()` |
| `src/oblib/rpc/frame/ob_net_easy.cpp:215` | `mrs cntfrq_el0` | aarch64 | 同上 |
| `src/oblib/easy/util/easy_util.h:15,27` | `rdtsc` / `mrs cntvct_el0` | x86-64 / aarch64 | `oblib::arch::rdtsc()`（libeasy 残留若按 RULEBOOK 0.14 删除，这两处随之消失） |
| `src/oblib/easy/include/easy_atomic.h:87,89,146,148,187,189` | `pause` / `yield` ×3 对 | x86-64 / aarch64 | `oblib::sync::pause()`（同上，libeasy 残留删除则消失） |
| `src/oblib/lib/cpu/ob_cpu_topology.cpp:114` | `cpuid` | x86-64 | `oblib::arch::cpuid(leaf, subleaf)` |
| `src/oblib/lib/cpu/ob_cpu_topology.cpp:123` | `xgetbv` | x86-64 | `oblib::arch::xgetbv(0)` |
| `src/oblib/lib/utility/utility.h:616` | `cpuid`（SSE4.2 检测） | x86-64 | `oblib::arch::cpuid(1, 0)` |
| `src/data_plane/api/data_plane/encoding/ob_cpu_features.h:32,47` | `cpuid` leaf 7 | x86-64 | `oblib::arch::cpuid(7, 0)` |
| `src/oblib/lib/checksum/ob_crc64.cpp:218,345,352` | `crc32q`、`crc32b`（SSE4.2） | x86-64 | crc64 所在模块的 `simd` 子模块用 `_mm_crc32_u64`/`_mm_crc32_u8`（RULEBOOK §1"SIMD、汇编"、§3 第 8 项）；不经 `arch` |
| `src/oblib/lib/checksum/ob_crc64.cpp:1061` | `cpuid` | x86-64 | `oblib::arch::cpuid(1, 0)`，或 `is_x86_feature_detected!("sse4.2")` |
| `src/oblib/lib/utility/ob_backtrace.cpp:60,62` | 读 `rbp` / `x29` | x86-64 / aarch64 | `oblib::arch::frame_pointer()` |
| `src/oblib/lib/utility/ob_smart_call.cpp:32,50,67` | `jump_call`：换栈后调用（Win64、x86-64、aarch64 各一份） | 三种 | 由 `oblib::thread::stack` 用 `psm::on_stack` 取代（RULEBOOK §2.13） |

合计 2+1+1+4+6+1+1+1+2+6+1+1+1+2+3+1+2+3 = 39。

### 1.4 RULEBOOK 0.8 的简称与本文的名字

RULEBOOK 0.8 和 §2.7 用了 `Timer`、`TimerService`、`SimplePool<T>`、`ReentrantThread`、`AdaptivePool`、`TimerTask`、`Runnable` 这些简称，但它们不在 §4 的改名表里；§4 又规定"本文引入的新名字只来自改名表"，README 也要求"表外的一律保留 C++ 名"。本文按 C++ 名写，C++ 里没有对应物的才起新名（修订 R1 请人把这些新名字补进改名表）：

| RULEBOOK 的写法 | 本文 | C++ |
|---|---|---|
| `Threads` | `Threads`（另有 `pub type ThreadPool`） | `lib::Threads` / `lib::ThreadPool` / `share::ObThreadPool` |
| `Runnable` | `Runnable`（C++ 没有对应物，沿用 RULEBOOK） | `Threads` 的虚函数 `run(idx)`、`run1()` |
| `SimplePool<T>` | `ObSimpleThreadPoolBase<Q>`，别名 `ObSimpleThreadPool<T>`、`ObLinkQueueThreadPool<T>` | `ObSimpleThreadPoolBase<Q>` 及其两个别名 |
| `Timer` / `TimerService` | `ObTimer` / `ObTimerService` | 同名 |
| `Arc<dyn TimerTask>` | `Arc<dyn ObTimerTaskVirt>`，数据部分 `ObTimerTask`（RULEBOOK §2.13 的 `Virt` 规则：`ObTimerTask` 有数据成员 `timeout_check_`） | `ObTimerTask` |
| `run(self: &Arc<Self>)` | `runTimerTask(self: Arc<Self>)`（`&Arc<Self>` 不是可分派的接收者，见修订 R2） | `virtual void runTimerTask()` |
| `ReentrantThread` | `ObReentrantThread` + `ObReentrantThreadVirt` | `share::ObReentrantThread` |
| `AdaptivePool` | `ObAdaptiveWorkerPool` + `ObAdaptiveWorkerPoolVirt` | `lib::ObAdaptiveWorkerPool<Derived>` |
| `WorkerCtx`（在改名表里） | `WorkerCtx`（当前线程的句柄）+ `WorkerVirt`（覆盖部分） | `lib::Worker`、`THIS_WORKER` |

按已有规则得出、不算新名字的：`*Virt` 和 `base_<方法>`（§2.13）；`set_thread_name_1`、`update_loop_ts_0`、`check_stack_overflow_0`、`ObCurTraceId::get_trace_id_str_0`、`TraceId::set_trace_id`/`set_char`/`hash_uint64_t`（§4 的重载规则，§8 Q7）；`this_worker!`、`gettid!`、`gettname!`、`gettname_v2!`、`get_tsi!`、`get_tsi_mult!`、`smart_call!`、`smart_call_large!`、`blocking_run_implement!`、`basic_time_guard!`、`basic_time_guard_click!`、`sql_info_guard!`（宏用同名小写）；`instance()`（RULEBOOK 0.5）；`with_request`（RULEBOOK 0.5 点名；同样点名的 `with_warning_buffer` 归 04）；与 libc 或指令同名的包装（`pthread_sigmask`、`sigtimedwait`、`raise`、`pthread_kill`、`_exit`、`getpagesize`、`rdtsc`、`cpuid`、`xgetbv` 等）。

本文另外引入的新名字，全部列进修订 R1：`oblib::thread` 的 `Runnable`（沿用 RULEBOOK 的写法）、`HostRef`、`CurrentThread`、`NATIVE_STACK_FACTOR`、`ObStackMem`、`smart_call_impl`（宏展开用，`#[doc(hidden)]`）、`with_static_init_phase`、`in_static_init_phase`、`ob_di_tls!`、`ObTraceIdStr`、`ObCurTraceId::with_trace_id_mut`、`SeqGenerator::set_seq_generator`、`ob_set_tname`、`ob_set_extended_thread_name`、`pthread_getname_np_into_tname`、`Threads::set_expect_run_wrapper`、`WorkerCtx::get_worker_virt`、`WorkerCtx::get_session_any`、`ObBasicTimeGuard::tl_enable_time_guard`/`set_tl_enable_time_guard`；`oblib::signal` 的 `ObSigSet`、`PthreadHandle`、`probe_read_memory`、`terminate_current_process`；`oblib::arch` 的 `pause`、`cntvct`、`cntfrq`、`frame_pointer`、`walk_frame_pointers`。

## 2. Rust API

### 2.1 布局

| 路径 | 内容 | unsafe |
|---|---|---|
| `rust/port/oblib/src/thread/mod.rs` 及其下非 `stack`/`sys` 的文件 | itid、tid 与线程名、`Thread`/`Threads`/`Runnable`/`HostRef`/`IRunWrapper`/`ObPThread`、`ObReentrantThread`、线程池、定时器、`WorkerCtx`、trace id、`ObDITls`、两个守卫、静态初始化阶段标志 | 无（crate 级 `deny(unsafe_code)` 生效） |
| `rust/port/oblib/src/thread/stack/**` | `smart_call!`、`smart_call_large!`、`check_stack_overflow`、栈属性、扩展栈段、`call_with_new_stack` | 有：RULEBOOK §3 第 4 项 |
| `rust/port/oblib/src/thread/sys/**` | 线程相关的系统调用（gettid、改当前线程名、读主线程名、macOS QoS、pthread key 析构回调、页大小） | 有：**需要修订 R3 批准**；批准前的退路见 §4 |
| `rust/port/oblib/src/signal/**` | `oblib::signal` | 有：§3 第 5 项 |
| `rust/port/oblib/src/arch/**` | `oblib::arch` | 有：§3 第 6 项 |

- 翻译代码按 RULEBOOK §4 写 `use oblib::<名字>;`：上表的公开名字经 §1.1 各单元的桩文件 `pub use` 到 `oblib` 根；宏一律 `#[macro_export]`。
- 下文的 `ObResult`/`ObError`/`OB_*` 来自 04 的 `oblib::error`；`Latch`/`RwLatch`/`Monitor` 来自 02 的 `oblib::sync`；`ObBuf` 来自 04 的 `oblib::fmt`。

### 2.2 itid（C++ `lib/thread_local/ob_tsi_utils.{h,cpp}`）

```rust
// rust/port/oblib/src/thread/itid.rs
use core::sync::atomic::{AtomicBool, AtomicI64, AtomicU64};

/// C++ INVALID_ITID（ob_tsi_utils.h:37）
pub const INVALID_ITID: i64 = -1;
/// C++ uint64_t itid_slots[1024]（.cpp:73）：1024 × 64 = 65536 个 itid
pub(crate) static itid_slots: [AtomicU64; 1024] = [const { AtomicU64::new(0) }; 1024];
/// C++ volatile int64_t max_itid / max_used_itid（.cpp:25-26）
pub(crate) static max_itid: AtomicI64 = AtomicI64::new(INVALID_ITID);
pub(crate) static max_used_itid: AtomicI64 = AtomicI64::new(INVALID_ITID);
/// C++ static bool itid_lock（.cpp:71）：自旋锁；不得改用 oblib::sync 的锁（latch 要调 get_itid，会递归）
pub(crate) static itid_lock: AtomicBool = AtomicBool::new(false);

/// C++ get_itid()（.h:46-55）：本线程的稠密线程号；首次调用时分配，线程退出时归还
pub fn get_itid() -> i64 { todo!() }
/// C++ get_max_itid()：当前最大 itid + 1（.h:57-63）
pub fn get_max_itid() -> i64 { todo!() }
/// C++ get_max_used_itid()：历史最大 itid + 1（.h:65-68）
pub fn get_max_used_itid() -> i64 { todo!() }
/// C++ alloc_itid() / free_itid() / detect_max_itid()（.cpp:74-147）；正常只经 get_itid 使用
pub fn alloc_itid() -> i64 { todo!() }
pub fn free_itid(itid: i64) { todo!() }
pub fn detect_max_itid() -> i64 { todo!() }
/// C++ 在 HAVE_SCHED_GETCPU 未定义时的 sched_getcpu()（.h:70-72）；基线各平台都没有定义它
pub fn sched_getcpu() -> i32 { todo!() }
/// C++ icpu_id() / get_max_icpu_id() / icore_id()（.h:74-91）
pub fn icpu_id() -> i64 { todo!() }
pub fn get_max_icpu_id() -> i64 { todo!() }
pub fn icore_id() -> i32 { todo!() }
```

### 2.3 tid 与线程名（C++ `ob_define.h:1875-1971`、`lib/thread/ob_thread_name.{h,cpp}`）

```rust
// rust/port/oblib/src/thread/name.rs
// OB_THREAD_NAME_BUF_LEN = 16、OB_EXTENED_THREAD_NAME_BUF_LEN = 32（ob_define.h:521-522），常量本身随 ob_define 的译文
const TNAME_LEN: usize = OB_THREAD_NAME_BUF_LEN as usize;
const EXT_TNAME_LEN: usize = OB_EXTENED_THREAD_NAME_BUF_LEN as usize;

/// C++ ob_syscall_gettid()（ob_define.h:1911-1925）：不缓存
pub fn ob_syscall_gettid() -> i64 { todo!() }
/// C++ ob_gettid()（:1927-1934）：线程局部缓存，≤0 时重取
pub fn ob_gettid() -> i64 { todo!() }
/// C++ get_tid_cache() 返回 int64_t&；src 里只读（5 处）
pub fn get_tid_cache() -> i64 { todo!() }
/// C++ reset_tid_cache()（:1905-1908）：fork/daemon 之后调用
pub fn reset_tid_cache() { todo!() }
/// C++ GETTID() 与小写的 gettid()（:1973、:1976）
#[macro_export] macro_rules! gettid { () => { $crate::thread::ob_gettid() }; }

/// C++ ob_get_tname()（:1937-1941）返回 char* 指向 16 字节的线程局部缓冲；Rust 返回拷贝（C 字符串语义：到 NUL 或缓冲末尾为止）
pub fn ob_get_tname() -> [u8; TNAME_LEN] { todo!() }
/// C++ 写进 ob_get_tname() 缓冲的地方（main.cpp:658,661 的 snprintf）：按 snprintf("%s") 截到 15 字节加 NUL
pub fn ob_set_tname(name: &[u8]) { todo!() }
pub fn ob_get_origin_thread_name() -> [u8; TNAME_LEN] { todo!() }
pub fn ob_get_extended_thread_name() -> [u8; EXT_TNAME_LEN] { todo!() }
/// C++ 写进 ob_get_extended_thread_name() 缓冲的地方（ob_timer_service.cpp:182-185,194）
pub fn ob_set_extended_thread_name(name: &[u8]) { todo!() }
/// C++ ob_get_tname_v2()（:1963-1970）：扩展名非空用扩展名，否则用 tname
pub fn ob_get_tname_v2() -> [u8; EXT_TNAME_LEN] { todo!() }
#[macro_export] macro_rules! gettname { () => { $crate::thread::ob_get_tname() }; }
#[macro_export] macro_rules! gettname_v2 { () => { $crate::thread::ob_get_tname_v2() }; }

/// C++ set_thread_name(const char *type, uint64_t idx)（ob_thread_name.cpp:51-59），先声明，保留原名
pub fn set_thread_name(type_: &[u8], idx: u64) { todo!() }
/// C++ set_thread_name(const char *type)（:61-69）；单参版本没有可区分的参数类型，按 §4 的"参数个数后缀"得此名（§8 Q7）
pub fn set_thread_name_1(type_: &[u8]) { todo!() }
```

### 2.4 `Thread`、`Threads`、`Runnable`、`HostRef`、`IRunWrapper`、`ObPThread`

```rust
// rust/port/oblib/src/thread/threads.rs
use std::sync::{Arc, Weak};
use core::sync::atomic::{AtomicBool, AtomicI32, AtomicI64, AtomicU64};

/// C++ lib::IRunWrapper（thread.h:54-69）：没有数据成员的接口类，按 §2.13 只生成 trait、保留原名
pub trait IRunWrapper: Send + Sync + 'static {
    fn pre_run(&self) -> ObResult { Ok(()) }
    fn end_run(&self) -> ObResult { Ok(()) }
    fn id(&self) -> u64;
}

/// 新名字（修订 R1）。把宿主交给线程、线程池、可重入线程时用：模块用 Static（&'static，RULEBOOK 0.5 的 Box::leak），
/// Arc 持有的对象用 Shared。线程运行期间持有强引用；Threads 自己只存非拥有的形式（&'static 或 Weak），不成环。
pub enum HostRef<T: ?Sized + 'static> {
    Static(&'static T),
    Shared(Arc<T>),
}
impl<T: ?Sized + 'static> Clone for HostRef<T> { fn clone(&self) -> Self { todo!() } }
impl<T: ?Sized + 'static> core::ops::Deref for HostRef<T> { type Target = T; fn deref(&self) -> &T { todo!() } }

/// RULEBOOK §2.7"线程池子类、run1() → 实现 Runnable trait"。C++ 的线程池子类改成实现它、再把自己交给 Threads::start。
pub trait Runnable: Send + Sync + 'static {
    /// C++ virtual void Threads::run(int64_t idx)；只有 ObThWorker、ObPxPool、ObPartitionCreator 覆盖
    fn run(&self, idx: i64) { base_run(self, idx) }
    /// C++ virtual void run1()
    fn run1(&self);
}
/// C++ Threads::run(idx) 的基类实现（threads.cpp:240-246）；RULEBOOK §2.13 的 base_<方法> 命名
pub fn base_run<R: Runnable + ?Sized>(this: &R, idx: i64) { todo!() }

/// C++ THREAD_STACK_RESERVED_SIZE（threads.cpp:27）
pub const THREAD_STACK_RESERVED_SIZE: i64 = 16 << 10;
/// C++ global_thread_stack_size（threads.cpp:29）；ObServer 在配置之后重写它（ob_server.cpp:1994-1997）。
/// ACHUNK_PRESERVE_SIZE = 17 KiB（alloc_assist.h:121）由 01 导出。
pub static global_thread_stack_size: AtomicI64 =
    AtomicI64::new((1 << 18) - THREAD_STACK_RESERVED_SIZE - ACHUNK_PRESERVE_SIZE as i64);
/// 新名字（修订 R1）。RULEBOOK A.3 的"帧膨胀系数"：第 5 步实测之前取 1。只作用于 Threads 创建的线程（§3.11 第 7 条）。
pub const NATIVE_STACK_FACTOR: f64 = 1.0;
/// C++ weak get_max_thread_num()（thread.cpp:498-501）；从未被覆盖
pub fn get_max_thread_num() -> i32 { 4096 }

/// C++ lib::Thread（thread.h:73-138）。由 Threads 持有；翻译代码只用下面的关联函数和 CurrentThread。
pub struct Thread { /* idx_、stack_size_、Arc<共享状态{stop_: AtomicBool, tid_: AtomicI64}>、JoinHandle、create_ret_: AtomicI32 */ }
impl Thread {
    /// C++ Thread &Thread::current()（thread.cpp:42-46）
    pub fn current() -> CurrentThread { CurrentThread }
    /// C++ update_loop_ts(int64_t t)（thread.h:102-107），先声明，保留原名；只调 ObLatch::clear_lock()
    pub fn update_loop_ts(t: i64) -> i64 { todo!() }
    /// C++ update_loop_ts()（thread.h:109-113）
    pub fn update_loop_ts_0() -> i64 { todo!() }
    /// C++ static thread_local bool is_doing_ddl_ 及 set_doing_ddl（thread.h:114-116）
    pub fn set_doing_ddl(v: bool) { todo!() }
    pub fn is_doing_ddl() -> bool { todo!() }
}
/// 新名字（修订 R1）：当前线程的句柄，零大小。C++ 在非 Threads 创建的线程上得到空引用，Rust 得到 is_valid() == false 的句柄。
#[derive(Clone, Copy, Debug)]
pub struct CurrentThread;
impl CurrentThread {
    /// 代替 C++ 的 OB_NOT_NULL(&lib::Thread::current())（ob_dedup_queue.cpp:413、log_io_worker.cpp:224）
    pub fn is_valid(&self) -> bool { todo!() }
    /// C++ Thread::has_set_stop()（thread.h:140-144）：本线程自己的停止标志；无效句柄返回 false
    pub fn has_set_stop(&self) -> bool { todo!() }
    /// C++ Thread::stop()（thread.cpp:157-182）；无效句柄什么也不做
    pub fn stop(&self) { todo!() }
    /// C++ Thread::get_tid()（thread.h:100）；无效句柄返回 0
    pub fn get_tid(&self) -> i64 { todo!() }
}

/// C++ lib::Threads（threads.h:34-143）。宿主用字段 `pub base: Threads` 嵌入它（§2.13 的 base 命名）。
pub struct Threads { /* n_threads_、init_threads_、Vec<Arc<Thread>>（在 RwLatch 里，对应 lock_）、stack_size_、stop_: AtomicBool、run_wrapper_、宿主句柄 */ }
pub type ThreadPool = Threads;
impl Threads {
    /// C++ explicit Threads(int64_t n_threads = 1)：stack_size_ 取构造时的 global_thread_stack_size
    pub fn new(n_threads: i64) -> Self { todo!() }
    /// C++ static IRunWrapper *&get_expect_run_wrapper()（threads.cpp:33-37）：读、写分成两个函数
    pub fn get_expect_run_wrapper() -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn set_expect_run_wrapper(rw: Option<&'static dyn IRunWrapper>) { todo!() }
    pub fn set_default_run_wrapper(rw: Option<&'static dyn IRunWrapper>) { todo!() }
    pub fn get_default_run_wrapper() -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn set_run_wrapper(&self, rw: Option<&'static dyn IRunWrapper>) { todo!() }
    pub fn get_run_wrapper(&self) -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn get_effective_run_wrapper(&self) -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn init(&self) -> ObResult { Ok(()) }
    /// C++ virtual int start()：多了宿主参数（C++ 靠虚函数找到 run1）
    pub fn start(&self, host: HostRef<dyn Runnable>) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn do_set_thread_count(&self, n_threads: i64, async_recycle: bool) -> ObResult { todo!() }
    pub fn set_thread_count(&self, n_threads: i64) -> ObResult { todo!() }
    pub fn inc_thread_count(&self, inc: i64) -> ObResult { todo!() }
    pub fn thread_recycle(&self) -> ObResult { todo!() }
    pub fn try_thread_recycle(&self) -> ObResult { todo!() }
    /// C++ 两个重载 has_set_stop() const / bool &has_set_stop()（threads.h:99-108）合并成一个读、一个写（RULEBOOK §2.7）
    pub fn has_set_stop(&self) -> bool { todo!() }
    pub fn set_stop(&self) { todo!() }
    pub fn get_thread_count(&self) -> i64 { todo!() }
    /// C++ get_thread_idx()（threads.h:119）：读线程局部 thread_idx_，与 self 无关
    pub fn get_thread_idx(&self) -> u64 { todo!() }
    /// C++ set_thread_idx(idx)（threads.h:120）：覆盖 Runnable::run 的实现里用
    pub fn set_thread_idx(idx: i64) { todo!() }
}
/// C++ ~Threads()：stop、wait、destroy（threads.cpp:49-54）。线程持有宿主的强引用，所以只有线程都退出后才会走到这里。
impl Drop for Threads { fn drop(&mut self) { todo!() } }

/// C++ ObPThread（threads.h:145-159）：单线程，跑一个函数
pub struct ObPThread { /* base: Threads、Latch<Option<Box<dyn FnOnce() + Send>>> */ }
/// C++ int ob_pthread_create(void **ptr, void *(*start_routine)(void*), void *arg)（ob_pthread.cpp:22-44）
pub fn ob_pthread_create(ptr: &mut Option<Box<ObPThread>>, start_routine: Box<dyn FnOnce() + Send + 'static>) -> ObResult { todo!() }
/// C++ ob_pthread_join（:45-54）：wait 后释放
pub fn ob_pthread_join(ptr: Option<Box<ObPThread>>) { todo!() }
/// C++ ob_pthread_tryjoin_np（:56-66）：线程还在运行返回 OB_EAGAIN 并保留；已退出则释放
pub fn ob_pthread_tryjoin_np(ptr: &mut Option<Box<ObPThread>>) -> ObResult { todo!() }
```

### 2.5 `ObReentrantThread`

```rust
// rust/port/oblib/src/thread/reentrant.rs —— C++ share::ObReentrantThread（ob_reentrant_thread.h:29-93）
pub struct ObReentrantThread {
    /// C++ 基类 lib::ThreadPool
    pub base: Threads,
    /// C++ protected volatile bool stop_（.h:73）：逻辑停止标志，子类直接读（ob_async_task_queue.cpp:106）
    pub stop_: AtomicBool,
    /* cond_: Monitor<{created_: bool, running_cnt_: i64}>、thread_name_: Option<&'static str>、宿主句柄 */
}
/// C++ 的虚函数部分（§2.13）。子类实现它。
pub trait ObReentrantThreadVirt: Send + Sync + 'static {
    fn base(&self) -> &ObReentrantThread;
    fn run2(&self);
    fn before_blocking_run(&self) -> ObResult { Ok(()) }
    /// C++ 是纯虚函数、子类一律写 BLOCKING_RUN_IMPLEMENT()（为了 pstack 里看得出归属）；Rust 给默认体
    fn blocking_run(&self) -> ObResult { base_blocking_run(self) }
    fn after_blocking_run(&self) -> ObResult { Ok(()) }
}
/// C++ ObReentrantThread::blocking_run()（.cpp:158-189）
pub fn base_blocking_run<T: ObReentrantThreadVirt + ?Sized>(this: &T) -> ObResult { todo!() }
/// C++ #define BLOCKING_RUN_IMPLEMENT() nothing(); return ObReentrantThread::blocking_run();（.h:91）
#[macro_export]
macro_rules! blocking_run_implement {
    ($self:expr) => {{ $self.base().nothing(); return $crate::thread::base_blocking_run($self); }};
}
impl ObReentrantThread {
    pub fn new() -> Self { todo!() }
    /// C++ create(thread_cnt, thread_name = nullptr, wait_event_id = REENTRANT_THREAD_COND_WAIT)（.cpp:41-57）
    pub fn create(&self, host: HostRef<dyn ObReentrantThreadVirt>, thread_cnt: i64,
                  thread_name: Option<&'static str>, wait_event_id: i64) -> ObResult { todo!() }
    pub fn start(&self) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn logical_start(&self) -> ObResult { todo!() }
    pub fn logical_stop(&self) { todo!() }
    pub fn logical_wait(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    /// C++ 覆盖的 has_set_stop()：ATOMIC_LOAD(&stop_)（.h:48-52）
    pub fn has_set_stop(&self) -> bool { todo!() }
    pub fn destroy(&self) -> ObResult { todo!() }
    pub fn get_thread_name(&self) -> Option<&'static str> { todo!() }
    pub fn nothing(&self) {}
    /// C++ get_cond().wait(ms)：C++ 不持锁就等（未定义行为），Rust 在内部加锁后等，见 §3.5 第 5 条
    pub fn idle_wait(&self, idle_time_ms: i64) -> ObResult { todo!() }
    pub fn idle_wait_us(&self, idle_time_us: i64) -> ObResult { todo!() }
}
```

### 2.6 线程池与自适应扩缩

```rust
// rust/port/oblib/src/thread/pool.rs

/// C++ template<class T> struct QueueTypeMap { using TaskType; using QElemType; }（ob_simple_thread_pool.h:35-45），
/// 加上池子用到的队列操作。本组在同一 crate 里为 05 的 ObLightyQueue<T>、ObTLinkQueue16<T> 实现它（§7）。
pub trait QueueTypeMap: Send + Sync + 'static {
    /// C++ 的 void* / LinkTask*：任务的类型是明确的（RULEBOOK §2.7）。只挂在队列里的用 Box<X>；
    /// 同时被别处持有、会被反复投递的（如 ObLogApplyService 的成员任务）用 Arc<X> 或 id（RULEBOOK §2.7 队列项规则）
    type TaskType: Send + 'static;
    fn init(&self, task_num_limit: i64, label: &'static str) -> ObResult;
    /// 满时 OB_SIZE_OVERFLOW，任务留在 *task 里
    fn push(&self, task: &mut Option<Self::TaskType>) -> ObResult;
    /// 超时 OB_ENTRY_NOT_EXIST
    fn pop(&self, task: &mut Option<Self::TaskType>, timeout_us: i64) -> ObResult;
    fn pop_nowait(&self, task: &mut Option<Self::TaskType>) -> ObResult;
    fn size(&self) -> i64;
    fn wake_all(&self);
    fn destroy(&self);
}

/// C++ ObSimpleThreadPoolBase<T>（ob_simple_thread_pool.h:47-126，含基类 ObSimpleDynamicThreadPool 的字段）
pub struct ObSimpleThreadPoolBase<Q: QueueTypeMap> { /* is_inited_、stop_、queue_: Q、run_wrapper_、workers_、cur_worker_idx_、
                                                       min/max_thread_cnt_、name_、ObAdaptiveWorkerPool、宿主句柄 */ }
pub type ObSimpleThreadPool<T> = ObSimpleThreadPoolBase<crate::queue::ObLightyQueue<T>>;     // C++ ObSimpleThreadPool
pub type ObLinkQueueThreadPool<T> = ObSimpleThreadPoolBase<crate::queue::ObTLinkQueue16<T>>; // C++ ObLinkQueueThreadPool

/// C++ 的虚函数 handle/handle_drop（.h:112-115）
pub trait ObSimpleThreadPoolBaseVirt<Q: QueueTypeMap>: Send + Sync + 'static {
    fn base(&self) -> &ObSimpleThreadPoolBase<Q>;
    fn handle(&self, task: Q::TaskType);
    fn handle_drop(&self, task: Q::TaskType) { self.handle(task) }
}

impl<Q: QueueTypeMap> ObSimpleThreadPoolBase<Q> {
    pub const QUEUE_WAIT_TIME: i64 = 1000 * 1000;   // .h:54
    pub const SHRINK_TIMEOUT_US: i64 = 1000 * 1000; // .h:55
    pub fn new() -> Self { todo!() }
    /// C++ init(thread_num, task_num_limit, name = "unknown")（.ipp:47-79）：多了宿主参数；不创建线程
    pub fn init(&self, host: HostRef<dyn ObSimpleThreadPoolBaseVirt<Q>>, thread_num: i64,
                task_num_limit: i64, name: Option<&'static str>) -> ObResult { todo!() }
    pub fn destroy(&self) { todo!() }
    /// C++ push(TaskType *task)（.ipp:108-128）：成功时取走 *task；失败时留在原处，由调用方照 C++ 处理
    pub fn push(&self, task: &mut Option<Q::TaskType>) -> ObResult { todo!() }
    pub fn get_queue_num(&self) -> i64 { todo!() }
    pub fn notify_stop(&self) { todo!() }
    pub fn worker_count(&self) -> i64 { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn has_set_stop(&self) -> bool { todo!() }
    pub fn set_run_wrapper(&self, rw: Option<&'static dyn IRunWrapper>) { todo!() }
    pub fn get_run_wrapper(&self) -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn get_thread_count(&self) -> i64 { todo!() }
    /// C++ 返回 cur_worker_idx_：池级字段，最后一个启动的工作线程写入（.ipp:248），不是"本线程的下标"
    pub fn get_thread_idx(&self) -> u64 { todo!() }
    pub fn set_thread_count(&self, n_threads: i64) -> ObResult { todo!() }
    pub fn set_adaptive_thread(&self, min_thread_num: i64, max_thread_num: i64) -> ObResult { todo!() }
    pub fn queue_size(&self) -> i64 { todo!() }
    pub fn reap_workers(&self) { todo!() }
}

/// C++ ObSimpleThreadPoolDynamicMgr（ob_dynamic_thread_pool.h:126-143）：单例线程 "qth_mgr"，每 3 秒回收已退出的工作线程
pub struct ObSimpleThreadPoolDynamicMgr { pub base: Threads, /* 已绑定的池（非拥有引用）、RwLatch */ }
impl ObSimpleThreadPoolDynamicMgr {
    pub const CHECK_INTERVAL_US: i64 = 3 * 1000 * 1000;  // .h:128
    /// C++ get_instance()（.cpp:355-359）→ RULEBOOK 0.5 的 instance()
    pub fn instance() -> &'static ObSimpleThreadPoolDynamicMgr { todo!() }
    pub fn init(&'static self) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
}
impl Runnable for ObSimpleThreadPoolDynamicMgr { fn run1(&self) { todo!() } }

/// C++ template<typename Derived> class ObAdaptiveWorkerPool（ob_adaptive_worker_pool.h:63-123）。
/// CRTP 按 §2.13 改成"结构体 + 宿主实现的 trait"；宿主把它当作第二个基类字段 base_ob_adaptive_worker_pool。
pub struct ObAdaptiveWorkerPool { idle_cnt_: AtomicI64, total_cnt_: AtomicI64 }
/// C++ 的 CRTP 约定：do_add_worker()、queue_size()（.h:35-37）
pub trait ObAdaptiveWorkerPoolVirt {
    fn do_add_worker(&self) -> bool;
    fn queue_size(&self) -> i64;
}
impl ObAdaptiveWorkerPool {
    pub const fn new() -> Self { Self { idle_cnt_: AtomicI64::new(0), total_cnt_: AtomicI64::new(0) } }
    pub fn pop_with_idle<R>(&self, pop_fn: impl FnOnce() -> R, expand: &mut bool) -> R { todo!() }
    pub fn try_expand_one<H: ObAdaptiveWorkerPoolVirt + ?Sized>(&self, host: &H, limit: i64) -> bool { todo!() }
    pub fn try_shrink_one<H: ObAdaptiveWorkerPoolVirt + ?Sized>(&self, host: &H, floor: i64) -> bool { todo!() }
    pub fn idle_count(&self) -> i64 { todo!() }
    pub fn worker_count(&self) -> i64 { todo!() }
}
```

### 2.7 定时器

```rust
// rust/port/oblib/src/thread/timer.rs

/// C++ ObTimerTask 的数据部分（ob_timer_service.h:53-73）
pub struct ObTimerTask { timeout_check_: AtomicBool }
impl ObTimerTask {
    pub const fn new() -> Self { Self { timeout_check_: AtomicBool::new(false) } }
    pub fn enable_timeout_check(&self) { todo!() }
    pub fn disable_timeout_check(&self) { todo!() }
    pub fn timeout_check(&self) -> bool { todo!() }
}
/// C++ ObTimerTask 的虚函数部分。定时服务持有 Arc<dyn ObTimerTaskVirt>（RULEBOOK §2.7）。
pub trait ObTimerTaskVirt: Send + Sync + 'static {
    fn base(&self) -> &ObTimerTask;
    /// C++ virtual void runTimerTask()。接收者是 Arc<Self>（可分派），任务可以把 self 再交给 schedule 重排自身（修订 R2）。
    /// 方法名保留 C++ 的驼峰写法（RULEBOOK §4），lint 见修订 R5。
    #[allow(non_snake_case)]
    fn runTimerTask(self: Arc<Self>);
    /// C++ typeid(*this).name()（.h:64、.cpp:81）；只进日志
    fn task_type_name(&self) -> &'static str { core::any::type_name::<Self>() }
    /// C++ virtual to_string：默认 "task_type:%s, timeout_check:%s"（.h:59-67）
    fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() }
}

/// C++ ObTimer（ob_timer.h:28-78）。C++ 用 this 的地址在服务里作键；Rust 用构造时分配的唯一 id。
pub struct ObTimer { /* id_: u64、is_inited_、is_stopped_: AtomicBool、timer_name_: Latch<[u8; 16]>、run_wrapper_ */ }
impl ObTimer {
    /// C++ ObTimer()：只记下全局服务，不启动它（.h:32-40）
    pub fn new() -> Self { todo!() }
    /// C++ init(timer_name = nullptr, attr = ObMemAttr("timer"))（.cpp:30-53）：attr 按 RULEBOOK 0.7 删掉
    pub fn init(&self, timer_name: Option<&[u8]>) -> ObResult { todo!() }
    pub fn inited(&self) -> bool { todo!() }
    pub fn start(&self) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn set_run_wrapper_with_ret(&self, run_wrapper: Option<&'static dyn IRunWrapper>) -> ObResult { todo!() }
    pub fn get_run_wrapper(&self) -> Option<&'static dyn IRunWrapper> { todo!() }
    pub fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() }
    /// C++ schedule(ObTimerTask &task, delay, repeate = false, immediate = false)（.cpp:131-146）
    pub fn schedule(&self, task: Arc<dyn ObTimerTaskVirt>, delay: i64, repeate: bool, immediate: bool) -> ObResult { todo!() }
    pub fn schedule_repeate_task_immediately(&self, task: Arc<dyn ObTimerTaskVirt>, delay: i64) -> ObResult { todo!() }
    /// 以下几个只按身份比较任务：传 &*arc 即可（比较数据指针，不解引用）
    pub fn task_exist(&self, task: &dyn ObTimerTaskVirt) -> bool { todo!() }
    pub fn cancel(&self, task: &dyn ObTimerTaskVirt) -> ObResult { todo!() }
    pub fn cancel_task(&self, task: &dyn ObTimerTaskVirt) -> ObResult { todo!() }
    pub fn wait_task(&self, task: &dyn ObTimerTaskVirt) -> ObResult { todo!() }
    pub fn cancel_all(&self) { todo!() }
}
/// C++ ~ObTimer() → destroy()（.cpp:55-58）
impl Drop for ObTimer { fn drop(&mut self) { todo!() } }

/// C++ ObTimerService（ob_timer_service.h:121-203）：全进程一个，1 个调度线程 "TimerSvr" 加工作线程池 "TimerWK"
pub struct ObTimerService { /* base: Threads、Monitor<状态{队列、running 集、uncanceled 集}>、ObTimerTaskThreadPool、
                               is_never_started_/is_stopped_/is_destroyed_: AtomicBool、mutex_: Latch<()> */ }
impl ObTimerService {
    pub const MIN_WAIT_INTERVAL: i64 = 10 * 1000;         // .h:192
    pub const MAX_WAIT_INTERVAL: i64 = 100 * 1000;        // .h:193
    pub const MIN_WORKER_THREAD_NUM: i64 = 4;             // .h:194
    pub const MAX_WORKER_THREAD_NUM: i64 = 128;           // .h:195
    pub const TASK_NUM_LIMIT: i64 = 10000;                // .h:196
    pub const DUMP_INTERVAL: i64 = 60 * 1000 * 1000;      // .h:197
    pub const ALARM_INTERVAL: i64 = 60 * 1000 * 1000;     // .h:198
    pub const DELAY_IN_PRI_QUEUE_THREASHOLD: i64 = 1000 * 1000; // .h:202
    pub fn instance() -> &'static ObTimerService { todo!() }   // C++ get_instance()（.h:129-133）
    pub fn start(&'static self) -> ObResult { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn schedule_task(&self, timer: &ObTimer, task: Arc<dyn ObTimerTaskVirt>, delay: i64,
                         repeate: bool, immediately: bool) -> ObResult { todo!() }
    /// task 为 None 时匹配该定时器的全部任务（C++ 传 nullptr）
    pub fn cancel_task(&self, timer: &ObTimer, task: Option<&dyn ObTimerTaskVirt>) -> ObResult { todo!() }
    pub fn wait_task(&self, timer: &ObTimer, task: Option<&dyn ObTimerTaskVirt>) -> ObResult { todo!() }
    pub fn task_exist(&self, timer: &ObTimer, task: &dyn ObTimerTaskVirt) -> bool { todo!() }
    pub fn is_never_started(&self) -> bool { todo!() }
    pub fn is_stopped(&self) -> bool { todo!() }
}

/// C++ ObTimerMonitor（ob_timer_monitor.h:28-88）
pub struct ObTimerMonitor { /* inited_、timer_: ObTimer、monitor_task_: Arc<ObTimerMonitorTask>、
                               records_: [Latch<TimerRecord>; 256]、tail_: AtomicI64 */ }
impl ObTimerMonitor {
    pub const MAX_MONITOR_THREAD_NUM: i64 = 256;           // .h:56
    pub const CHECK_INTERVAL: i64 = 10 * 1000 * 1000;      // .h:57
    pub fn instance() -> &'static ObTimerMonitor { todo!() }
    pub fn init(&self) -> ObResult { todo!() }
    pub fn start(&self) -> ObResult { todo!() }
    pub fn wait(&self) { todo!() }
    pub fn stop(&self) { todo!() }
    pub fn destroy(&self) { todo!() }
    pub fn start_task(&self, thread_id: i64, start_time: i64, interval: i64, task: &Arc<dyn ObTimerTaskVirt>) { todo!() }
    pub fn end_task(&self, thread_id: i64, end_time: i64) { todo!() }
    pub fn dump(&self, print_trace: bool) { todo!() }
}
```

`TaskToken`、`ObTimerTaskThreadPool`、`ObTimerUtil::copy_buff`、`lib_server_runtime_dispatch` 是 `pub(crate)` 的实现细节，翻译代码用不到。

### 2.8 `WorkerCtx`（`THIS_WORKER`）

```rust
// rust/port/oblib/src/thread/worker.rs
use core::any::Any;

/// 改名表：WorkerCtx ↔ THIS_WORKER 等线程局部。零大小的句柄，方法都作用于调用线程自己的 worker 上下文。
#[derive(Clone, Copy, Debug, Default)]
pub struct WorkerCtx;
/// C++ #define THIS_WORKER oceanbase::lib::Worker::self()（worker.h:174）；RULEBOOK §4"宏用同名小写"
#[macro_export] macro_rules! this_worker { () => { $crate::thread::WorkerCtx }; }
/// C++ lib::this_worker()（worker.h:169-172）
pub fn this_worker() -> WorkerCtx { WorkerCtx }

/// C++ lib::Worker 里的嵌套枚举（worker.h:38-39），按 RULEBOOK §2.12 译成新类型加常量；嵌套名放进模块 worker
pub mod worker {
    #[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, Debug)]
    pub struct Status(pub i32);
    impl Status { pub const WS_NOWAIT: Status = Status(0); pub const WS_INVALID: Status = Status(1); }
    pub const WS_NOWAIT: Status = Status::WS_NOWAIT;
    pub const WS_INVALID: Status = Status::WS_INVALID;
    /// 进了落盘格式（ob_tablet_meta.cpp:533,589 按 int8 存），取值不能变
    #[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, Debug)]
    pub struct CompatMode(pub i32);
    impl CompatMode { pub const INVALID: CompatMode = CompatMode(-1); pub const MYSQL: CompatMode = CompatMode(0); }
}
/// C++ enum class LogReductionMode（worker.h:33）
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct LogReductionMode(pub i32);
impl LogReductionMode {
    pub const NONE: LogReductionMode = LogReductionMode(0);
    pub const REFINED: LogReductionMode = LogReductionMode(1);
    pub const COMPRESSED: LogReductionMode = LogReductionMode(2);
}

/// C++ lib::Worker 的虚函数部分（worker.h:44-55）。ObThWorker（observer）、ObPxWorker（sql）实现它，
/// 并在自己的线程开始时用 WorkerCtx::set_worker_to_thread_local 装进线程局部。默认体就是 C++ 基类的实现。
pub trait WorkerVirt: Any + Send + Sync + 'static {
    fn check_wait(&self) -> worker::Status { base_check_wait() }
    fn check_status(&self) -> ObResult { let _ = self.check_wait(); Ok(()) }
    fn can_retry(&self) -> bool { false }
    fn set_need_retry(&self) {}
    fn unset_need_retry(&self) {}
    fn need_retry(&self) -> bool { false }
    fn resume(&self) {}
    /// RULEBOOK §2.13"需要运行期向下转型的 trait 对象"：THIS_THWORKER_SAFE 的 dynamic_cast 经这里
    fn as_any(&self) -> &dyn Any;
}
/// C++ Worker::check_wait() 的基类实现（worker.cpp:73-81）：调 weak 的 common_yield()（从未覆盖，返回 OB_SUCCESS）
pub fn base_check_wait() -> worker::Status { todo!() }

impl WorkerCtx {
    /// C++ Worker::set_worker_to_thread_local(Worker*)（worker.h:148-151）：把本线程的数据复位到 Worker() 的初值，
    /// 并装入 w（None 表示基类 Worker）。只在线程开头调用（§3.8 第 2 条）。
    pub fn set_worker_to_thread_local(w: Option<Arc<dyn WorkerVirt>>) { todo!() }
    /// THIS_THWORKER_SAFE 等向下转型用：返回装入的对象（克隆一次 Arc）
    pub fn get_worker_virt(&self) -> Option<Arc<dyn WorkerVirt>> { todo!() }

    // ---- C++ 的虚函数：有装入的对象就派发给它，否则走基类实现 ----
    pub fn check_wait(&self) -> worker::Status { todo!() }
    pub fn check_status(&self) -> ObResult { todo!() }
    pub fn can_retry(&self) -> bool { todo!() }
    pub fn set_need_retry(&self) { todo!() }
    pub fn unset_need_retry(&self) { todo!() }
    pub fn need_retry(&self) -> bool { todo!() }
    pub fn resume(&self) { todo!() }
    /// C++ sched_wait() 恒为 true；sched_run(waittime = 0) 调 check_status() 并丢弃结果后返回 true（worker.cpp:83-93）
    pub fn sched_wait(&self) -> bool { true }
    pub fn sched_run(&self, waittime: i64) -> bool { todo!() }

    // ---- 数据成员（worker.h:78-104），全部在本线程的线程局部里 ----
    /// C++ set_req_flag(&req) … set_req_flag(NULL)（ob_th_worker.cpp:156,198）：只记"有请求"，不保存借用
    pub fn with_request<Req: ?Sized, R>(&self, cur_request: &Req, f: impl FnOnce() -> R) -> R { todo!() }
    pub fn has_req_flag(&self) -> bool { todo!() }
    pub fn set_worker_level(&self, level: i32) { todo!() }
    pub fn get_worker_level(&self) -> i32 { todo!() }
    pub fn set_curr_request_level(&self, level: i32) { todo!() }
    pub fn get_curr_request_level(&self) -> i32 { todo!() }
    pub fn is_timeout_ts_valid(&self) -> bool { todo!() }
    pub fn set_timeout_ts(&self, timeout_ts: i64) { todo!() }
    pub fn get_timeout_ts(&self) -> i64 { todo!() }
    pub fn set_ntp_offset(&self, offset: i64) { todo!() }
    pub fn get_ntp_offset(&self) -> i64 { todo!() }
    pub fn get_timeout_remain(&self) -> i64 { todo!() }
    pub fn is_timeout(&self) -> bool { todo!() }
    pub fn set_disable_wait_flag(&self, f: bool) { todo!() }
    pub fn get_disable_wait_flag(&self) -> bool { todo!() }
    /// C++ set_session(sql::ObSQLSessionInfo*)：线程局部存拥有型的 Arc（RULEBOOK 0.5）；类型由上层决定，这里擦除
    pub fn set_session(&self, session: Option<Arc<dyn Any + Send + Sync>>) { todo!() }
    /// C++ get_session()：按类型取回；没有装入或类型不符都返回 None
    pub fn get_session<T: Any + Send + Sync>(&self) -> Option<Arc<T>> { todo!() }
    pub fn get_session_any(&self) -> Option<Arc<dyn Any + Send + Sync>> { todo!() }
    /// C++ static get/set_log_reduction_mode（worker.h:267-272）
    pub fn get_log_reduction_mode() -> LogReductionMode { todo!() }
    pub fn set_log_reduction_mode(mode: LogReductionMode) { todo!() }
    // RULEBOOK 0.5 点名的 with_warning_buffer 由 04 提供（oblib::log::with_warning_buffer(&ObWarningBufferRef, f)，
    // 线程局部存 04 的拥有型句柄）。C++ 的警告缓冲区指针本来就是独立的线程局部（ob_warning_buffer.h:241-245），
    // 不在 Worker 里，所以 WorkerCtx 不转发
    #[cfg(feature = "errsim")] pub fn set_module_type(&self, t: ObErrsimModuleType) { todo!() }
    #[cfg(feature = "errsim")] pub fn get_module_type(&self) -> ObErrsimModuleType { todo!() }
}

/// C++ is_log_reduction() / get_log_reduction() / set_log_reduction()（worker.h:258-265）
pub fn is_log_reduction() -> bool { todo!() }
pub fn get_log_reduction() -> LogReductionMode { todo!() }
pub fn set_log_reduction(mode: LogReductionMode) { todo!() }

/// C++ DisableSchedInterGuard（worker.h:176-190）：守卫只保存旧值（拥有型），不持借用，RAII 合规
pub struct DisableSchedInterGuard { last_flag_: bool }
impl DisableSchedInterGuard { pub fn new() -> Self { todo!() } }
impl Drop for DisableSchedInterGuard { fn drop(&mut self) { todo!() } }
#[cfg(feature = "errsim")]
pub struct ErrsimModuleGuard { last_type_: i32 }   // worker.h:195-213；new/Drop 同上
```

不提供的成员（C++ 依据见 §3.8 第 5 条）：`get_sql_arena_allocator`、`get_allocator`、`get_cur_request`、`set_group`/`get_group`/`is_group_worker`、`is_th_worker`/`set_is_th_worker`、`worker_node_`、`ObRuntimeContext`、`get_ob_runtime_context`、`ObExtraRpcHeader`。

### 2.9 `ObCurTraceId`

```rust
// rust/port/oblib/src/thread/trace_id.rs —— C++ lib/profile/ob_trace_id.{h,cpp}

/// C++ ObCurTraceId::TraceId（.h:47-237）：32 字节，union 里的位域用 RULEBOOK §2.12 的 ob_bitfield! 映射到 uval_[0]
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
pub struct TraceId { uval_: [u64; 4] }
impl TraceId {
    pub const fn new() -> Self { Self { uval_: [0; 4] } }
    pub fn is_valid(&self) -> bool { todo!() }
    pub fn is_invalid(&self) -> bool { todo!() }
    pub fn init(&mut self, ip_port: &ObAddr) { todo!() }
    pub fn check_ipv6_valid(&self) { todo!() }
    /// C++ set(const uint64_t *uval)（.h:76-89），先声明，保留原名；空指针 → OB_ERR_UNEXPECTED
    pub fn set(&mut self, uval: Option<&[u64; 4]>) -> ObResult { todo!() }
    /// C++ set(const TraceId &)（.h:90-97）
    pub fn set_trace_id(&mut self, new_trace_id: &TraceId) { todo!() }
    /// C++ set(const char *buf)（.h:158-176）：sscanf 语义
    pub fn set_char(&mut self, buf: &[u8]) -> ObResult { todo!() }
    pub fn get(&self) -> &[u64; 4] { &self.uval_ }
    pub fn get_seq(&self) -> u64 { todo!() }
    pub fn get_addr(&self) -> ObAddr { todo!() }
    pub fn get_uval(&self, uval: &mut [u64; 4]) { todo!() }
    pub fn reset(&mut self) { todo!() }
    pub fn get_server_addr(&self, addr: &mut ObAddr) -> ObResult { todo!() }
    pub fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() }
    pub fn parse_from_buf(&mut self, buf: &[u8]) -> ObResult { todo!() }
    pub fn equals(&self, trace_id: &TraceId) -> bool { todo!() }
    pub fn mark_user_request(&mut self) { todo!() }
    pub fn is_user_request(&self) -> bool { todo!() }
    pub fn set_inner_sql_id(&mut self, inner_sql_id: i64) { todo!() }
    pub fn get_execution_id(&self) -> i64 { todo!() }
    pub fn set_sub_id(&mut self, sub_id: i32) { todo!() }
    /// C++ int64_t hash() const（.h:193-201），先声明
    pub fn hash(&self) -> i64 { todo!() }
    /// C++ int hash(uint64_t &hash_val) const（.h:203）
    pub fn hash_uint64_t(&self, hash_val: &mut u64) -> ObResult { todo!() }
    pub fn is_default(&self) -> bool { todo!() }
}
// 序列化：ob_serialize_member!(TraceId, 1; uval_[0], uval_[1], uval_[2], uval_[3])（ob_trace_id.cpp:26；宏归 06）

/// C++ ObCurTraceId::SeqGenerator（.h:34-46）
pub struct SeqGenerator;
impl SeqGenerator {
    pub const BATCH: u64 = 1 << 20;
    pub fn gen_seq() -> u64 { todo!() }
    /// C++ ObCurTraceId::SeqGenerator::seq_generator_ = …（main.cpp:667）
    pub fn set_seq_generator(v: u64) { todo!() }
}

/// 新名字（修订 R1）：C++ get_trace_id_str() 返回的线程局部缓冲改成返回拥有型的小字符串（RULEBOOK §2.4）
#[derive(Clone, Copy)]
pub struct ObTraceIdStr { buf: [u8; 64], len: usize }   // OB_MAX_TRACE_ID_BUFFER_SIZE = 64（ob_define.h:432）
impl core::ops::Deref for ObTraceIdStr { type Target = [u8]; fn deref(&self) -> &[u8] { todo!() } }

/// C++ struct ObCurTraceId（.h:32-339）：只有静态函数，作用于本线程的 trace id
pub struct ObCurTraceId;
impl ObCurTraceId {
    pub fn init(ip_port: &ObAddr) { todo!() }
    pub fn set(uval: Option<&[u64; 4]>) { todo!() }               // .h:247
    pub fn set_trace_id(new_trace_id: &TraceId) { todo!() }       // .h:255
    pub fn set_inner_sql_id(execution_id: i64) { todo!() }
    pub fn set_sub_id(sub_id: i32) { todo!() }
    pub fn reset() { todo!() }
    /// C++ get_trace_id_str(char *buf, int64_t buf_len)（.h:286-293），先声明：打印进调用方的缓冲，返回打印出的字节
    pub fn get_trace_id_str(buf: &mut [u8]) -> &[u8] { todo!() }
    /// C++ get_trace_id_str()（.h:294-298）：返回拷贝，不再返回线程局部缓冲的指针
    pub fn get_trace_id_str_0() -> ObTraceIdStr { todo!() }
    /// C++ const uint64_t *get()：返回拷贝
    pub fn get() -> [u64; 4] { todo!() }
    pub fn get_seq() -> u64 { todo!() }
    pub fn get_addr() -> ObAddr { todo!() }
    /// C++ TraceId *get_trace_id()（.h:316-320）：返回拷贝。经指针写的地方改调上面的 set*/init/reset 或 with_trace_id_mut
    pub fn get_trace_id() -> TraceId { todo!() }
    /// 新名字（修订 R1）：经 get_trace_id() 指针做复合修改的地方用它
    pub fn with_trace_id_mut<R>(f: impl FnOnce(&mut TraceId) -> R) -> R { todo!() }
    pub fn mark_user_request() { todo!() }
    pub fn is_user_request() -> bool { todo!() }
    pub fn set_char(buf: &[u8]) { todo!() }                       // .h:332
}

/// C++ ObTraceIdGuard（.h:341-362）：守卫保存旧 trace id 的拷贝（拥有型），Drop 时写回
pub struct ObTraceIdGuard { old_trace_id_: TraceId, is_old_trace_saved_: bool }
impl ObTraceIdGuard { pub fn new(trace_id: &TraceId) -> Self { todo!() } }
impl Drop for ObTraceIdGuard { fn drop(&mut self) { todo!() } }
```

### 2.10 线程局部工具

```rust
// rust/port/oblib/src/thread/tls.rs

/// C++ ObDITls<T, tag>（ob_di_tls.h:33-90）：每线程一个惰性创建的 T，线程退出时析构。
/// 每个 (T, tag) 在 T 所在的 crate 里用 ob_di_tls! 实现一次（孤儿规则允许：类型是本地的）。
pub trait ObDITls<const TAG: usize>: Sized + 'static {
    /// C++ get_instance() 返回 T*，可能为空；Rust 改成闭包，借用不出闭包（RULEBOOK 0.5）
    fn get_instance<R>(f: impl FnOnce(Option<&mut Self>) -> R) -> R;
}
/// 为 T 生成上面的实现（内部是一个 thread_local!，值是 RefCell<Option<Box<T>>>，用 T::default() 构造）
#[macro_export]
macro_rules! ob_di_tls { ($t:ty, $tag:expr) => { /* impl $crate::thread::ObDITls<{ $tag }> for $t { … } */ }; }
/// C++ GET_TSI(type) / GET_TSI0(type)（ob_tsi_factory.h:26,28）
#[macro_export]
macro_rules! get_tsi { ($t:ty, $f:expr) => { <$t as $crate::thread::ObDITls<0>>::get_instance($f) }; }
/// C++ GET_TSI_MULT(type, tag) / GET_TSI_MULT0（ob_tsi_factory.h:27,29）
#[macro_export]
macro_rules! get_tsi_mult { ($t:ty, $tag:expr, $f:expr) => { <$t as $crate::thread::ObDITls<{ $tag }>>::get_instance($f) }; }

/// C++ ObBasicTimeGuard（ob_common_utility.h:67-166）。数据放在线程局部的定长栈里（无 Drop、常量初始化，外部线程也能用），
/// 守卫对象本身只记自己在栈里的位置。
pub struct ObBasicTimeGuard { enable_: bool, depth_: u32 }
impl ObBasicTimeGuard {
    pub const MAX_CLICK_COUNT: i64 = 16;
    pub fn new(owner: &'static str, start: &'static str, end: &'static str) -> Self { todo!() }
    /// C++ static time_guard_click(mod)（.h:110-115）
    pub fn time_guard_click(mod_: &'static str) { todo!() }
    pub fn get_start_ts(&self) -> i64 { todo!() }
    pub fn get_diff(&self) -> i64 { todo!() }
    pub fn to_string(&self, buf: &mut ObBuf) -> i64 { todo!() }
    /// C++ public static __thread bool tl_enable_time_guard（.h:149）
    pub fn tl_enable_time_guard() -> bool { todo!() }
    pub fn set_tl_enable_time_guard(v: bool) { todo!() }
}
impl Drop for ObBasicTimeGuard { fn drop(&mut self) { todo!() } }
/// C++ BASIC_TIME_GUARD(name, owner) / BASIC_TIME_GUARD_CLICK(mod)（.h:168-170）
#[macro_export]
macro_rules! basic_time_guard {
    ($name:ident, $owner:literal) => {
        let $name = $crate::thread::ObBasicTimeGuard::new($owner, concat!($owner, "#start"), concat!($owner, "#end"));
    };
}
#[macro_export]
macro_rules! basic_time_guard_click { ($m:expr) => { $crate::thread::ObBasicTimeGuard::time_guard_click($m) }; }

/// C++ ObFatalErrExtraInfoGuard（ob_common_utility.h:50-60、.cpp:138-166）。全库没有构造它或它的子类的地方，
/// 线程局部恒为空（§3.10 第 3 条），所以不提供装入，只给读者留入口。唯一的虚函数是 to_string，
/// 按 04 的约定以 ObToString 为 supertrait
pub trait ObFatalErrExtraInfoGuardVirt: crate::fmt::ObToString + Sync {}
pub struct ObFatalErrExtraInfoGuard;
impl ObFatalErrExtraInfoGuard {
    /// C++ get_thd_local_val_ptr()（.cpp:155-158）：恒返回 None
    pub fn get_thd_local_val_ptr() -> Option<&'static dyn ObFatalErrExtraInfoGuardVirt> { None }
}

/// 第 2 步生成的 main 在执行 run_static_init_side_effects 期间置位；Threads::start 在 debug 构建下断言它为假（§3.13 第 3 条）
pub fn with_static_init_phase<R>(f: impl FnOnce() -> R) -> R { todo!() }
pub fn in_static_init_phase() -> bool { todo!() }
```

`RLOCAL`/`RLOCAL_INIT`/`RLOCAL_STATIC`/`RLOCAL_INLINE`/`_RLOCAL`/`RLOCAL_EXTERN`/`TLOCAL`（`co_var.h:29-34`）不提供宏：按 RULEBOOK §2.13 的 `thread_local!` 规则逐处译；`ByteBuf<N>` 译成 `[u8; N]`。

### 2.11 `oblib::thread::stack`

```rust
// rust/port/oblib/src/thread/stack/mod.rs —— RULEBOOK §3 第 4 项；C++ ob_smart_call.{h,cpp}、ob_common_utility.{h,cpp}

/// C++ ALL_STACK_LIMIT / STACK_PER_EXTEND / STACK_RESERVED_SIZE / STACK_RESERVED_SIZE_LARGE（ob_smart_call.h:28-36）
pub const ALL_STACK_LIMIT: i64 = 10 << 20;
pub const STACK_PER_EXTEND: i64 = (2 << 20) - ACHUNK_PRESERVE_SIZE as i64 * 2;   // 2,062,336
pub const STACK_RESERVED_SIZE: i64 = 64 << 10;
pub const STACK_RESERVED_SIZE_LARGE: i64 = (256 - 64) << 10;                    // 192 KiB

/// C++ get_reserved_stack_size()（ob_common_utility.cpp:35-42）：32 KiB
pub fn get_reserved_stack_size() -> i64 { 32 << 10 }
/// C++ check_stack_overflow(bool &is_overflow, reserved = get_reserved_stack_size(), int64_t *used_size = nullptr)
/// （.h:31-34、.cpp:45-83），先声明，保留原名
pub fn check_stack_overflow(is_overflow: &mut bool, reserved_stack_size: i64, used_size: Option<&mut i64>) -> ObResult { todo!() }
/// C++ inline int check_stack_overflow(void)（.h:39-44）：溢出返回 OB_SIZE_OVERFLOW。即 RULEBOOK 0.8 的 check_stack_overflow(reserved)
pub fn check_stack_overflow_0() -> ObResult { todo!() }
/// C++ get_stackattr(void *&, size_t &)（.cpp:85-130）：当前栈段的低地址和大小
pub fn get_stackattr(stackaddr: &mut usize, stacksize: &mut usize) -> ObResult { todo!() }

/// C++ __SMART_CALL_IMPL(reserved_size, func)（ob_smart_call.h:112-128）
pub fn smart_call_impl(reserved_size: i64, func: impl FnOnce() -> ObResult) -> ObResult { todo!() }
/// C++ SMART_CALL(func) / SMART_CALL_LARGE(func)（.h:130、:138）
#[macro_export]
macro_rules! smart_call {
    ($func:expr) => { $crate::thread::stack::smart_call_impl($crate::thread::stack::STACK_RESERVED_SIZE, || $func) };
}
#[macro_export]
macro_rules! smart_call_large {
    ($func:expr) => { $crate::thread::stack::smart_call_impl($crate::thread::stack::STACK_RESERVED_SIZE_LARGE, || $func) };
}

/// 新名字（修订 R1）：main 用的整块新栈（C++ main.cpp:877-886 的 mmap/munmap）
pub struct ObStackMem { /* base、size */ }
impl ObStackMem {
    /// mmap(nullptr, size, RW, PRIVATE|ANON)；失败 OB_ERR_UNEXPECTED
    pub fn mmap(size: usize) -> ObResult<ObStackMem> { todo!() }
    /// munmap；失败 OB_ERR_UNEXPECTED
    pub fn munmap(self) -> ObResult { todo!() }
}
/// C++ CALL_WITH_NEW_STACK(func, stack_addr, stack_size)（ob_smart_call.h:99-110）+ call_with_new_stack（:41-70）
pub fn call_with_new_stack<R>(stack: &mut ObStackMem, func: impl FnOnce() -> R) -> R { todo!() }
```

`alloc_stack`/`dealloc_stack`/`jump_call`/`set_stackattr`/`all_stack_size` 和每线程的扩展栈段缓存是 `pub(crate)` 的实现细节。

### 2.12 `oblib::thread::sys`（修订 R3）

```rust
// rust/port/oblib/src/thread/sys/mod.rs —— 等修订 R3 把本路径加进 §3 白名单
pub(crate) fn sys_gettid() -> i64 { todo!() }                         // syscall(SYS_gettid) / pthread_threadid_np / GetCurrentThreadId
pub(crate) fn set_current_thread_os_name(name: &[u8]) { todo!() }     // prctl(PR_SET_NAME) / pthread_setname_np / SetThreadDescription
pub(crate) fn set_current_thread_qos_user_initiated() -> i32 { todo!() } // macOS pthread_set_qos_class_self_np + setpriority；其余平台 0
pub(crate) fn itid_register_release(itid: i64) { todo!() }            // pthread_setspecific（key 的析构回调调用 free_itid）；Windows 用 FlsAlloc
/// C++ main.cpp:660 的 pthread_getname_np(pthread_self(), ob_get_tname(), 16)：读当前线程的 OS 名字进 tname，返回 C 的返回值
pub fn pthread_getname_np_into_tname() -> i32 { todo!() }
/// C++ getpagesize()（ob_server.cpp:1996）
pub fn getpagesize() -> usize { todo!() }
```

本组不需要"作用域线程局部"（装入借用值的线程局部）：`with_request` 只存标志（§3.8 第 8 条），警告缓冲区由 04 用拥有型句柄实现，`ObFatalErrExtraInfoGuard` 从未装入（§3.10 第 3 条）。

### 2.13 `oblib::signal`

```rust
// rust/port/oblib/src/signal/mod.rs —— RULEBOOK §3 第 5 项；只用 libc（RULEBOOK 0.8）
pub use libc::timespec;
/// 平台的信号号：unix 取自 libc；Windows 的 libc 没有这几个，用 ob_signal_handle.h:24-35 的值（SIGTERM 是 MSVC 的 15）
#[cfg(unix)] pub const SIGPIPE: i32 = libc::SIGPIPE;
#[cfg(unix)] pub const SIGTERM: i32 = libc::SIGTERM;
#[cfg(unix)] pub const SIGUSR1: i32 = libc::SIGUSR1;
#[cfg(unix)] pub const SIGKILL: i32 = libc::SIGKILL;
#[cfg(unix)] pub const SIG_BLOCK: i32 = libc::SIG_BLOCK;
#[cfg(windows)] pub const SIGPIPE: i32 = 13;
#[cfg(windows)] pub const SIGTERM: i32 = 15;
#[cfg(windows)] pub const SIGUSR1: i32 = 10;
#[cfg(windows)] pub const SIGKILL: i32 = 9;
#[cfg(windows)] pub const SIG_BLOCK: i32 = 0;

/// C++ sigset_t；Windows 上是 ob_signal_handle.h:36-41 的 64 位掩码
#[derive(Clone, Copy)]
pub struct ObSigSet {
    #[cfg(unix)] raw: libc::sigset_t,
    #[cfg(windows)] raw: u64,
}
/// C++ sigemptyset / sigaddset / sigismember / pthread_sigmask（返回值照 C）
pub fn sigemptyset(set: &mut ObSigSet) -> i32 { todo!() }
/// macOS 复刻 SDK 宏：信号号大于 32 时不置位、返回 0（§3.12 第 2 条）
pub fn sigaddset(set: &mut ObSigSet, signo: i32) -> i32 { todo!() }
pub fn sigismember(set: &ObSigSet, signo: i32) -> i32 { todo!() }
pub fn pthread_sigmask(how: i32, set: Option<&ObSigSet>, oldset: Option<&mut ObSigSet>) -> i32 { todo!() }
/// C++ sigtimedwait(&set, NULL, &timeout)：Linux 真的 sigtimedwait；macOS 是 ob_signal_handle.cpp:25-37 的 sigwait 模拟（忽略超时）
pub fn sigtimedwait(set: &ObSigSet, timeout: &timespec) -> i32 { todo!() }
pub fn raise(sig: i32) -> i32 { todo!() }
/// 新名字（修订 R1）：pthread_t 的可 Send/Sync 包装（macOS 上 pthread_t 是裸指针类型）
#[derive(Clone, Copy, PartialEq, Eq, Default, Debug)]
pub struct PthreadHandle(usize);
pub fn pthread_self() -> PthreadHandle { todo!() }
pub fn pthread_kill(thread: PthreadHandle, sig: i32) -> i32 { todo!() }
/// C++ _exit / _Exit（main.cpp:816,823、ob_server.cpp:1697）：不跑 atexit、不刷 stdio
pub fn _exit(status: i32) -> ! { todo!() }
/// C++ Windows 分支 TerminateProcess(GetCurrentProcess(), 1)（ob_signal_handle.cpp:130-131）
#[cfg(windows)] pub fn terminate_current_process(exit_code: u32) -> ! { todo!() }

/// 新名字（修订 R1）：不依赖信号的内存探测（RULEBOOK 0.8）。能完整读出 out.len() 字节返回 true，否则 false；从不触发 SIGSEGV
pub fn probe_read_memory(addr: usize, out: &mut [u8]) -> bool { todo!() }

/// C++ uint64_t g_rlimit_core（ob_signal_struct.cpp:29）；main.cpp:545 写、只被死代码读
pub static g_rlimit_core: core::sync::atomic::AtomicU64 = core::sync::atomic::AtomicU64::new(0);
/// C++ signal_handler_t / ob_signal_handler / ObSignalHandlerGuard（ob_signal_struct.h:36-60）：
/// C++ 里线程局部的处理函数指针只被 install_ob_signal_handler 装的分派函数读，而它从未被调用，所以守卫在 C++ 里就没有效果
#[allow(non_camel_case_types)]
pub type signal_handler_t = fn(i32);
pub fn ob_signal_handler(sig: i32) { todo!() }                // C++ 未重定向时：signal(sig, SIG_DFL); raise(sig)
pub struct ObSignalHandlerGuard;
impl ObSignalHandlerGuard { pub fn new(handler: signal_handler_t) -> Self { let _ = handler; ObSignalHandlerGuard } }
/// C++ SQL_INFO_GUARD(sql_string, sql_id)（ob_signal_struct.h:97-98）：读者 coredump_cb 是死代码（ob_signal_handlers.cpp:202），只求值参数
#[macro_export]
macro_rules! sql_info_guard { ($sql:expr, $sql_id:expr) => { let _ = (&$sql, &$sql_id); }; }
```

### 2.14 `oblib::arch`

```rust
// rust/port/oblib/src/arch/mod.rs —— RULEBOOK §3 第 6 项；asm! 只出现在这里（RULEBOOK §1）
/// PAUSE 的指令：x86-64 pause（_mm_pause），aarch64 yield（ob_atomic.h:31-36）。翻译代码用 02 的 oblib::sync::pause()
pub fn pause() { todo!() }
/// C++ rdtsc() / rdtscp()（ob_tsc_timestamp.h:67-99）：x86-64 读 TSC；aarch64 两者都读 cntvct_el0
pub fn rdtsc() -> u64 { todo!() }
pub fn rdtscp() -> u64 { todo!() }
#[cfg(target_arch = "x86_64")] pub fn rdtscp_id(cpuid: &mut u64) -> u64 { todo!() }
#[cfg(target_arch = "aarch64")] pub fn cntvct() -> u64 { todo!() }
#[cfg(target_arch = "aarch64")] pub fn cntfrq() -> u64 { todo!() }
#[cfg(target_arch = "x86_64")] pub use core::arch::x86_64::CpuidResult;
#[cfg(target_arch = "x86_64")] pub fn cpuid(leaf: u32, subleaf: u32) -> CpuidResult { todo!() }
/// 先查 cpuid(1).ecx 的 OSXSAVE 位，不支持时返回 0（不执行 xgetbv，避免 #UD）
#[cfg(target_arch = "x86_64")] pub fn xgetbv(xcr: u32) -> u64 { todo!() }
/// C++ ob_backtrace.cpp:60,62 的 mov rbp / mov x29
pub fn frame_pointer() -> usize { todo!() }
/// C++ light_backtrace(buffer, size, rbp) 的链上读取（ob_backtrace.cpp:68-111），只读 [stack_lo, stack_hi) 内的地址；
/// 04 的 light_backtrace 用 get_stackattr 取范围后调用它
pub fn walk_frame_pointers(buffer: &mut [usize], rbp: usize, stack_lo: usize, stack_hi: usize) -> i32 { todo!() }
```

### 2.15 `main` 的开头与生成物

二进制入口由脚本生成（RULEBOOK §4："observer 的二进制入口由脚本生成，写到 `rust/port/observer/src/bin/seekdb.rs`；`main.cpp` 所在单元的目标改成 `src/ob_main.rs`"），形状固定为：

```rust
// rust/port/observer/src/bin/seekdb.rs（生成）
fn main() {
    // 0.5 第 1 步：屏蔽信号，早于任何别的代码（含日志、LazyLock）。失败时不在这里报告：
    // ob_main::inner_main 照 C++ 在 main.cpp:676-681 再调一次，由那里 MPRINT（SIG_BLOCK 是幂等的）。
    let _ = observer::ObSignalHandle::change_signal_mask();
    // 第 2 步：按原版二进制的动态初始化顺序 force 非平凡单例（§3.13 第 2 条）
    oblib::thread::with_static_init_phase(|| observer::generated::run_static_init_side_effects());
    // 第 3 步：注册纯函数式的 seam 和 weak 钩子（RULEBOOK 0.4 的 OnceLock<fn> 钩子），
    // 含 09 §3.9.8 的启动断言和 PARSER_HOST_SLOT 的绑定（顺序照 09）
    observer::generated::register_hooks();
    // 第 4 步：显式启动日志写线程——入口由 04 定义（日志的写线程不得在首次打日志时惰性创建）。
    // 04 的修订 #4 建议把它移回 C++ 的位置（ObServer::init 里的 OB_LOGGER.init，ob_server.cpp:650）；获批则这里删去这一步
    // 第 5 步：其余照 C++ 的 main（main.cpp:855-889）
    std::process::exit(observer::ob_main::main(std::env::args_os().collect()));
}
```

- 第 2 步用 09 定的名字：`generated/bindings.rs` 里的 `pub fn run_static_init_side_effects()`（09 §2.6.4），函数体是按顺序排列的 `LazyLock::force(&<路径>);`（§3.13 第 2 条）。第 3 步的 `register_hooks()` 09 的文件清单里还没有，本文暂定为 `generated/hooks.rs` 里的 `pub fn register_hooks()`，见 Q15。本组只规定调用顺序和 `with_static_init_phase` 的包裹。
- 槽位绑定 `generated::bind_all(mods)` **不在**这里：由组合根在 `obs_construct_modules` 的位置调用（RULEBOOK 0.5、§2.10；09 §2.6.4）。
- `ob_main::main` 是 `main.cpp:855-889` 的译文：Linux 先 `disable_hugepage_for_self_text()`，然后 `ObStackMem::mmap(1 << 20)`、`call_with_new_stack(&mut stack, || inner_main(args))`、`stack.munmap()`。

## 3. 语义

### 3.1 itid

1. **分配**（`ob_tsi_utils.cpp:74-94`）：按 0…1023 的顺序扫描 `itid_slots`，在第一个有空位的槽上取最低的空位（`ffsl(~slot)`），CAS 置位；CAS 失败就从槽 0 重新扫描；1024 个槽全满（65,536 个线程同时在用）时 `ob_abort()`。所以总是拿"当前最小的空闲号"，号从 0 开始，最大 65535。
2. **最大值账目**（`.cpp:95-101,117-131`）：分配后在 `itid_lock` 自旋锁内 `max_itid = max(idx, max_itid)`、`max_used_itid` 只增不减；释放时清位（CAS 循环），锁内若 `itid >= max_itid` 就用 `detect_max_itid()`（从槽 1023 往下找最高的置位，`.cpp:133-147`）重算。`get_max_itid()` 返回 `max_itid + 1`，`get_max_used_itid()` 返回 `max_used_itid + 1`，初值都是 0（`.h:57-68`）。自旋锁不得换成 `oblib::sync` 的锁：latch 记写者 itid，会递归进 `get_itid()`。
3. **生命周期**：线程第一次调用 `get_itid()` 时分配，并用 pthread key 的析构回调登记归还（`.cpp:28-69,106-115`）。Rust 同样用 pthread key（`thread::sys`，修订 R3）：glibc 在线程退出时先跑 `thread_local!` 的析构、后跑 key 的析构，所以别的线程局部析构里取锁时 itid 仍然有效。归还之后线程局部里的值不清零（C++ 的 `TLOCAL tid` 也不清零），同一线程在退出过程中再调 `get_itid()` 会拿到已归还的号，可能与新线程重号——`BUG(port): 线程退出时 TLS 析构里取锁，get_itid 返回已归还的 itid`。
4. **`sched_getcpu`/`icpu_id`**：`HAVE_SCHED_GETCPU` 在基线上没有定义（`grep -rn HAVE_SCHED_GETCPU src cmake bazel` 只有 `ob_tsi_utils.h:70,81` 两处判断；`build_release/compile_commands.json` 里 0 次），而 `oceanbase::common` 里的内联 `sched_getcpu` 遮蔽了 libc 的同名函数，所以所有平台上都是 `get_itid() & (OB_MAX_CPU_NUM - 1)`；`get_max_icpu_id()` 恒为 `OB_MAX_CPU_NUM`（x86-64 64，aarch64 128，`ob_define.h:1483-1487`）。
5. **外部线程**：`get_itid()` 只读写常量初始化、无 Drop 的线程局部和 pthread key，所以 vsag/OpenMP 线程经日志或锁调到它也合乎 RULEBOOK 0.5。修订 R3 不获批时的退路见 §4。

### 3.2 tid 与线程名

1. `ob_gettid()` 把系统 tid 缓存在线程局部，缓存值 ≤0 时重取：Linux `syscall(SYS_gettid)`，macOS `pthread_threadid_np`（64 位线程 id），Windows `GetCurrentThreadId`（`ob_define.h:1911-1934`）。`reset_tid_cache()` 把缓存置 -1，fork/daemon 之后调用（`:1905-1908`）。
2. `set_thread_name(type, idx)`（`ob_thread_name.cpp:51-59`）：原名缓冲 `STRNCPY(ori_tname, type, 16)`（`type` 不短于 16 字节时没有结尾 NUL，C++ 之后按 C 串读是越界读；Rust 的数组有界，读到第 16 字节为止——`FIXED(port): ori_tname 无结尾 NUL 时的越界读`）；`tname = snprintf("%s%llu", type, idx)` 截到 15 字节加 NUL；然后设 OS 名：macOS `pthread_setname_np(name)`（只能改当前线程），Linux `prctl(PR_SET_NAME)`（内核截到 15 字节），Windows `SetThreadDescription`（宽字符，最多 15 个）（`:31-49`）。单参版本用 `"%s"`（`:61-69`）。
3. 线程创建时不设名字（`thread.cpp:73-155` 没有命名）：Linux 新线程继承创建者的 comm，macOS 新线程名字为空。Rust 不给 `std::thread::Builder` 传 `.name()`（std 只在给了名字时才设 OS 名），行为相同。
4. `ob_get_tname_v2()`：扩展名非空用扩展名，否则用 tname（`ob_define.h:1963-1970`）。定时器工作线程在跑任务前把扩展名设为 `tname + '_' + timer_name`（仅当 `tname_len + 1 + timer_name_len < 32`），跑完清空（`ob_timer_service.cpp:173-197`）。
5. 这几个线程局部都是常量初始化、无 Drop 的字节数组和整数。日志行头在任何线程上都会读它们（`ob_log.cpp:740-763`），vsag 的日志回调来自 vsag 自己的线程（`ob_vsag_adaptor.cpp:496-500` 装 OB 的 logger），所以这条是硬约束。

### 3.3 `Thread` 与 `Threads`

1. **线程数上限**（`thread.cpp:73-87`）：创建前对全局计数做 `FAA`，若旧值 `>= get_max_thread_num() - OB_RESERVED_THREAD_NUM`（4096 − 128 = 3968，`ob_define.h:1481`）返回 `OB_SIZE_OVERFLOW` 并记 ERROR "thread count reach limit"；`stack_size_ <= 0` 返回 `OB_ERR_UNEXPECTED`；创建失败返回 `OB_ERR_SYS`（`:142-145`）。失败时计数减回（`:150-153`），线程退出时在线程内减（`:396`）。
2. **栈**：Linux 上 C++ 用 `ob_malloc` 分配 `stack_size_` 加保护页，经 `pthread_attr_setstack` 交给线程（`thread.cpp:84,118`；`protected_stack_allocator.cpp:62-135`）；macOS/Android 用 `pthread_attr_setstacksize(stack_size_)`，**失败时静默退回系统默认**（`thread.cpp:101-114`）。Rust 一律用 `std::thread::Builder::stack_size(eff)`，`eff = ceil(stack_size_ × NATIVE_STACK_FACTOR)`；在 macOS 上若 `stack_size_` 不是页大小的整数倍，C++ 的 `setstacksize` 返回 EINVAL 后用系统默认（512 KiB），Rust 同样传 `512 KiB × NATIVE_STACK_FACTOR`。这种情况真会发生：`global_thread_stack_size` 的初值 228,352 不是页的倍数（`threads.cpp:29`），`ObServer::init_config` 之后才按页取整（`ob_server.cpp:1996-1997`），在那之前构造的 `Threads`（信号线程、日志写线程等）在 macOS 上拿到的是 512 KiB。1d 在两种 macOS 上实测确认系统默认值。
3. **`start()` 等子线程起来再返回**（`thread.cpp:132-137`）：自旋 `sched_yield` 直到子线程写了 `create_ret_`。子线程依次：设 QoS（macOS 上 `pthread_set_qos_class_self_np(USER_INITIATED)` 和 `setpriority(PRIO_DARWIN_THREAD, 0, 0)`，其余平台什么也不做；`thread.cpp:341-347`、`ob_platform_utils.h:352-381`）；登记当前线程、记 tid（`:348-349`）；写 `create_ret_ = OB_SUCCESS`；跑。C++ 在 `:368-391` 建 `ObPageManager` 和 `ThreadRoot` 内存上下文，Rust 没有对应物（RULEBOOK 0.7：没有 `CURRENT_CONTEXT`），删掉。
4. **跑**（`thread.cpp:186-202`）：有效的 run wrapper（`threads.h:81-85`：自己的，否则全局默认）存在时，`pre_run()` → `host.run(idx)` → `end_run()`，返回值都丢弃；`ObDisableDiagnoseGuard` 只影响诊断统计，Rust 不保留。默认的 `Runnable::run` 就是 `base_run`：设线程局部 `thread_idx_`、`WorkerCtx::set_worker_to_thread_local(None)`（等价于 C++ 在栈上放一个新的 `Worker` 并装入，`threads.cpp:240-246`）、`run1()`。
5. **停止**：`Threads::stop()` 持读锁，置 `stop_`，再对每个线程调 `Thread::stop()`（置该线程自己的停止标志）（`threads.cpp:286-297`）。`Thread::stop()` 按 glibc 私有偏移 720/724 读 tid（`thread.cpp:170-180`）只为调试转储，读的是 glibc 的私有结构，Rust 不保留。
6. **等待与销毁**：`wait()` 不持锁，逐个 join（`threads.cpp:275-284`）；C++ join 失败会 `ob_abort()`（`thread.cpp:250-254`），Rust 的 join 只在线程 panic 时失败，`panic = "abort"` 下不会发生。`destroy()` join 并释放全部线程（`threads.cpp:299-313`）；Rust 先在锁内取出线程表、再在锁外 join，不在持锁时等别的线程（C++ 的 `destroy` 本来就不取锁）。`Drop` = stop、wait、destroy（`:49-54`）。
7. **`start()`**（`threads.cpp:202-238`）：`n_threads_ = init_threads_`；线程局部的"期望 run wrapper"不为空且与本池的不同时 `ob_abort()`（它只被 `ob_pthread_create` 临时置空再恢复，`ob_pthread.cpp:28-31`，所以实际从不触发，照抄）；按下标 0…n−1 依次创建，任一失败就 stop/wait/destroy 全部并返回错误。连续 `start()` 两次会再建一批线程、旧的不 join（C++ 直接覆盖 `threads_`）——`BUG(port): Threads::start 重入时旧线程脱离管理`。
8. **`do_set_thread_count(n, async_recycle)`**（`threads.cpp:56-114`，`set_thread_count`/`inc_thread_count` 持写锁调用它）：未启动时只改 `init_threads_`；缩小时先停 [n, n_threads_)，非异步回收时逐个 wait+destroy 并把 `n_threads_` 改成 n（异步回收时 `n_threads_` 不变，留给 `thread_recycle`）；扩大时新建 [n_threads_, n)，任一失败就销毁本次新建的、保持原数量并返回错误。持写锁 join 是 C++ 的做法，照抄。
9. **`thread_recycle`/`try_thread_recycle`**（`threads.cpp:129-195`，持写锁）：凡是**线程自己的**停止标志已置（PX 池的线程空闲太久时用 `Thread::current().stop()` 标记自己，`ob_server_runtime.cpp:284`）的，wait（try 模式下用 try_wait，`OB_EAGAIN` 就保留）后销毁，再按原顺序压紧线程表。try_wait 的语义：Linux `pthread_tryjoin_np`，macOS 先 `pthread_kill(pth, 0)` 判活（`thread.cpp:259-306`）；Rust 用 `JoinHandle::is_finished()`，结束才 join，否则 `OB_EAGAIN`。
10. **停止标志**：`Threads` 的 `has_set_stop()` 有两个重载，const 版 `ATOMIC_LOAD`、非 const 版返回 `bool&`（`threads.h:99-108`），后者在 89 行调用里被读、在 `ob_simple_thread_pool.ipp:251` 被写（`grep -rn "has_set_stop()\s*=[^=]"` 只有这一处），是数据竞争。Rust 合成 `has_set_stop()` 与 `set_stop()` 两个方法（RULEBOOK §2.7），`FIXED(port): has_set_stop() 返回 bool& 的数据竞争`。
11. **`Thread::current()`**：C++ 在非 `Threads` 创建的线程上 `assert` 被 `NDEBUG` 去掉后解引用空指针（`thread.cpp:42-46`）；两处调用写成 `OB_NOT_NULL(&lib::Thread::current()) ? … : false`（`ob_dedup_queue.cpp:413`、`log_io_worker.cpp:224`），取空引用的地址本身就是未定义行为。Rust 返回句柄 `CurrentThread`，非本组创建的线程上 `is_valid()` 为假、`has_set_stop()` 为假、`stop()` 什么也不做——`FIXED(port): 对空引用取地址`。
12. `update_loop_ts` 两个重载都只调 `ObLatch::clear_lock()`（`thread.h:102-113`），Rust 转调 02 的同名函数（持锁登记表没有消费者，concurrency 调查 §2.2），返回 0。`is_doing_ddl_` 是线程局部布尔，只被 `set_doing_ddl` 写（`ob_server_runtime_controller.cpp:1094,1109`）；`ObThWorker` 存了指向它的指针、在 `is_doing_ddl()` 里读（`ob_th_worker.h:86,114`、`.cpp:216`），而 `is_doing_ddl()` 没有调用者，译者删掉这个指针成员。
13. **run wrapper 的派发**：weak 的 `lib_server_runtime_dispatch` 在全局命名空间（`ob_timer_service.cpp:23-30`），share 里的"强定义"在 `oceanbase::common`（`src/share/rc/ob_server_runtime.cpp:31-37`），是两个不同的符号，覆盖从未生效；两者的函数体都是直接调 `fn()`（layering 调查 §2.5）。Rust 直接调用。默认 wrapper 由 `ObServer` 设为 server runtime（`ob_server.cpp:1384`），它不覆盖 `pre_run`/`end_run`（`src/share/rc/ob_server_runtime.h:53-61`），所以两个钩子都是空操作。
14. **`ObPThread`**（`ob_pthread.cpp:22-66`）：`ob_pthread_create` 建一个单线程的 `Threads` 跑 `start_routine(arg)`，成功后才写出句柄（C++ 用 `ATOMIC_STORE`，调用方的字段改成 Rust 的拥有型字段即可）；`ob_pthread_join` 等待后释放；`ob_pthread_tryjoin_np` 用 try_wait，成功才释放，否则返回 `OB_EAGAIN` 并保留。

### 3.4 宿主与 `HostRef`

1. C++ 靠虚函数让线程找到子类的 `run1`；Rust 的线程要拿到宿主的 `'static` 句柄，所以 `Threads::start`、`ObReentrantThread::create`、`ObSimpleThreadPoolBase::init` 都多了宿主参数。模块是 `Box::leak` 得到的 `&'static`（RULEBOOK 0.5），传 `HostRef::Static(self)`，对应方法的接收者写 `&'static self`；`Arc` 持有的对象（如 `ObThWorker`、每个 LS 里的对象）传 `HostRef::Shared(self.clone())`，接收者写 `self: &Arc<Self>`。RULEBOOK 0.5 写的是"生命周期方法一律取 `&self`"，`&'static self` 仍是 `&self` 接收者，修订 R7 请人补一句。
2. `Threads` 内部只存非拥有的句柄（`&'static` 或 `Weak`），运行中的线程持有强引用：宿主在线程退出前不会被释放，`Drop for Threads` 也就不会在线程还在跑时执行；宿主与线程表之间没有 `Arc` 环。扩容（`set_thread_count`）时要把 `Weak` 升级；调用方手里本来就有宿主的引用，所以不会失败（实现上失败时返回 `OB_NOT_INIT`）。
3. C++ 的 `~Threads()` 会 stop+wait；Rust 里 `Arc` 宿主只要还有线程在跑就不会析构，所以宿主必须照 C++ 的代码显式 stop/wait/destroy（C++ 的调用点本来就这样写）。

### 3.5 `ObReentrantThread`

1. `create`（`.cpp:41-57`）：已创建返回 `OB_INIT_TWICE`；`thread_cnt <= 0` 返回 `OB_INVALID_ARGUMENT`；初始化条件变量；`set_thread_count(cnt)`；`created_ = true`；启动线程。`stop_` 初值为真，线程起来后在条件变量上等 `logical_start`。
2. **线程体**（`run1`，`.cpp:139-156`）：线程名非空时，线程数为 1 用 `set_thread_name_1(name)`，否则 `set_thread_name(name, idx)`；依次 `before_blocking_run`、`blocking_run`、`after_blocking_run`，前一个失败就不跑后面的。
3. **`blocking_run`**（`.cpp:158-189`）：循环：持锁时，`!created_` 就退出；`stop_` 为真时，若 `ThreadPool` 的停止标志已置就退出，否则 `cond_.wait(3000 ms)`；`stop_` 为假时记 `need_run`、`running_cnt_++`。放锁后 `run2()`，再持锁 `running_cnt_--` 并广播。
4. `logical_start`：未创建返回 `OB_NOT_INIT`；先不加锁地看 `stop_`，为真才持锁置假并广播（`.cpp:77-92`）。`logical_stop`：持锁置真并广播（`:94-105`）。`logical_wait`：持锁；`running_cnt_ < 0` 时 C++ 把 ret 设成 `OB_INNER_STAT_ERROR` 但函数是 `void`、不返回它；否则等到 `running_cnt_` 归零（`:107-121`）。`stop()` = `logical_stop` + `ThreadPool::stop`；`destroy()`：已创建时 stop，持锁 `created_ = false` 并广播，`ThreadPool::wait`，销毁条件变量（`:59-75`）。
5. `idle_wait(ms)` 直接 `get_cond().wait(ms)`（`.cpp:195-206`），C++ 的调用方（`ob_freeze_reentrant_thread.cpp:66,73`）没有持锁，`pthread_cond_timedwait` 在未加锁的互斥量上是未定义行为。Rust 在 `idle_wait` 内部加锁再等——`FIXED(port): idle_wait 未持锁就等待条件变量`。`ms == 0` 时 `ObThreadCond` 无限等待（`ob_thread_cond.cpp:87-91`），照抄。
6. 子类直接读的 `stop_` 是逻辑停止标志；子类的 `has_set_stop()` 也读它（`.h:48-52`）。`ThreadPool::has_set_stop()`（基类的物理停止标志）在 Rust 里写 `self.base.base.has_set_stop()`。

### 3.6 线程池与自适应扩缩

1. **`ObSimpleThreadPoolBase::init`**（`.ipp:47-79`）：已初始化 `OB_INIT_TWICE`；`thread_num <= 0`、`task_num_limit <= 0` 或名字为空 `OB_INVALID_ARGUMENT`；初始化队列；`max_thread_cnt_` 若仍是 −1 则设为 `thread_num`，`min_thread_cnt_` 若仍是 −1 则设为 0；登记到回收线程；失败时 `destroy()`。**不创建任何线程**。
2. **`push`**（`.ipp:108-128`）：`OB_NOT_INIT` / `OB_INVALID_ARGUMENT` / 已停止 `OB_IN_STOP_STATE`；入队失败的 `OB_SIZE_OVERFLOW` 改成 `OB_EAGAIN`；不论入队成败，只要空闲线程数为 0 就 `try_expand_one(max_thread_cnt_)`。
3. **工作线程**（`.ipp:242-293`）：名字 `name_ + idx`（idx 在创建时取 `cur_worker_idx_++`，不复用）；把池级 `cur_worker_idx_` 写成自己的 idx（多个线程并发写，`FIXED(port)`：改成 Relaxed 原子量，语义仍是"最后一个写的赢"）；循环条件 `!stop_ && !Thread::current().has_set_stop()`；用 `pop_with_idle` 包住 `pop(QUEUE_WAIT_TIME = 1 s)`；取到任务就清空闲计时、`expand` 为真时 `try_expand_one(max)`、`handle(task)`；超时时第一次记下空闲起点，之后空闲满 `SHRINK_TIMEOUT_US = 1 s` 就 `try_shrink_one(min_thread_cnt_)`，成功则退出循环；退出时把自己的 `Threads` 标为停止，等回收线程回收。池停止后，每个退出循环的工作线程都从队列里取剩下的任务逐个 `handle_drop`（`.ipp:286-292`，几个线程并发地取）。
4. **其余**：`do_add_worker`（`.ipp:130-166`）在池已停止时返回假；在锁内建线程对象、锁外启动（新线程可能立刻扩容，持锁会死锁）、启动成功后才挂进列表。`stop`：置 `stop_`、唤醒队列、停全部工作线程；`wait`：join 全部；`destroy`：清 `is_inited_`、置 `stop_`、停止并 join 再释放全部工作线程、解除登记、销毁队列（`.ipp:81-106,206-224`）。`set_adaptive_thread(min, max)`：`min < 0 || max <= 0 || min > max` 返回 `OB_INVALID_ARGUMENT`（`.ipp:168-187`）；`set_thread_count(n)`：`n <= 0` 返回 `OB_INVALID_ARGUMENT`，`max = n`，`min = min(min, max)`（`.ipp:189-204`）。
5. **回收线程**（`ob_dynamic_thread_pool.cpp:404-417`）：单例 "qth_mgr"，持读锁遍历已登记的池，`reap_workers()` 移除并销毁已停止的工作线程，然后睡 `CHECK_INTERVAL_US = 3 s`。由 `ObServer` 在 `ob_server.cpp:702` 初始化、`:1635` 停止；在那之前收缩退出的线程留在列表里不回收。
6. **`ObAdaptiveWorkerPool`**（`ob_adaptive_worker_pool.h:69-119`，C++ 用 `std::atomic` 显式内存序，Rust 照写）：`pop_with_idle` 先 `idle_cnt_.fetch_add(1, Relaxed)`、调 `pop_fn`、再 `fetch_sub(1, Relaxed)`，取出前的值为 1（自己是最后一个空闲线程）时 `expand = true`。`try_expand_one(limit)`：`cur = load(Relaxed)`，`cur < limit` 时 `compare_exchange_weak(cur, cur + 1, AcqRel, Relaxed)`，成功后**反复** `do_add_worker()` 直到成功、每次失败睡 1 ms，返回真；池已停止时 `do_add_worker` 一直返回假，这个循环不会结束——`BUG(port): 池停止后 try_expand_one 无限重试`。`try_shrink_one(floor)`：`cur > floor` 时 CAS 减一，减到 0 而队列非空就加回并返回假。
7. **策略常数**。上下限和收缩、救援的策略写在调用方，随调用方的单元翻译；本组只提供机制。需要照抄的常数：

| 池 | 常数 | C++ 依据 |
|---|---|---|
| ReqWorker（`ObServerRuntime`，observer） | 下限 `2 + max(1, (int64)(min_cpu × cpu_quota_concurrency))`，`cpu_quota_concurrency` 取配置（默认 10） | `ob_server_runtime.cpp:565-574` |
| | 上限 `max(memory_size / 20 / (stack_size + 3 MiB + 512 KiB), 150)` | `:576-580` |
| | 空闲满 `KEEP_ALIVE_TIMEOUT = 10 s` 就 `try_shrink_one(0)` 并退出；轮询间隔 100 ms | `src/observer/omt/ob_server_runtime.h:184`、`ob_th_worker.cpp:217,296-303` |
| | 收到请求且没有空闲线程时扩一个：高优先级到上限、其余到下限；工作循环的扩容信号到下限 | `ob_server_runtime.cpp:651-661`、`ob_th_worker.cpp:274-277` |
| | 救援：每 3 s 检查一次，`worker_count ≥ 下限`、队列非空、完成数与上次相同时 `try_expand_one(上限)`；回收每 1 s 一次；`timeup` 每 100 ms 一次 | `ob_server_runtime.cpp:678-701` |
| TimerWK（`ObTimerService` 的工作池，本组） | `init(MIN_WORKER_THREAD_NUM = 4, TASK_NUM_LIMIT = 10000, "TimerWK")` 后 `set_thread_count(MAX_WORKER_THREAD_NUM = 128)`；按第 1、4 条，实际上限 128、**下限 0**、初始没有线程，有任务而无空闲线程时扩一个，空闲约 1–2 秒退出（RULEBOOK 0.8 写的"4–128"不准确，修订 R4） | `ob_timer_service.cpp:233-235`、`.h:194-196`、`ob_simple_thread_pool.ipp:63-68,196-199` |
| 其余 `ObSimpleThreadPool` 子类 | 同第 3 条：等待 1 s、空闲 1 s 收缩、回收 3 s | `ob_simple_thread_pool.h:54-55`、`ob_dynamic_thread_pool.h:128` |

### 3.7 定时器

1. **身份**：C++ 用 `(ObTimer*, ObTimerTask*)` 的地址作键（`CompareForSet`，`ob_timer_service.cpp:39-62`）。Rust 用 `(ObTimer::id_, Arc 的数据指针)`：服务持有任务的 `Arc`，地址在登记期间不会被复用。键的顺序只用于查找，不影响任何可观察行为。
2. **同一定时器串行**：只有当该定时器在 running 集和 uncanceled 集里都没有令牌时，它的令牌才会被派发（`pop_task`，`:533-552`）。所以同一个 `ObTimer` 的任务从不并发，同一定时器的就绪任务按队列顺序先后执行。
3. **`schedule_task`**（`:312-346`）：`delay < 0` 返回 `OB_INVALID_ARGUMENT`；令牌的计划时间是 `now`（immediately）或 `now + delay`，周期 `repeate ? delay : 0`；追加到优先队列尾部并 `notify`。时间用 `ObSysTime::now()`（Realtime；macOS 上是开机时的墙钟加 `mach_absolute_time` 的增量，`Time.cpp:46-69`）。
4. **调度线程 "TimerSvr"**（`:566-618`）：队列为空时 `wait()`；否则循环 `pop_task(now)`：按向量顺序扫描，跳过已派发的占位令牌，记下最早计划时间 `st`；取第一个到期、且其定时器没有在跑的令牌，把它的计划时间改成 `TOKEN_DISPATCHED`（`INT64_MAX`）作为占位留在队列里。没有可派发的就 `timed_wait(clamp(st − now, 10 ms, 100 ms))`（全是占位时 `st = INT64_MAX`，即每 100 ms 醒一次）。有就记 `pushed_time_`、放进 running 集、投给 TimerWK 池。
5. **投递失败**（`:601-611`）：C++ 从 running 集删掉并 `delete_token`，但占位令牌还在优先队列里，之后 `pop_task` 会读已释放的令牌（未定义行为）。Rust 同时把占位从队列里移除（与取消的效果相同）——`FIXED(port): 投递失败后队列里留下悬垂令牌`。
6. **执行**（`ObTimerTaskThreadPool::handle`，`:102-140`）：`this_worker!().set_timeout_ts(INT64_MAX)`、`ObCurTraceId::reset()`、设扩展线程名（§3.2 第 4 条）、调 `runTimerTask`（克隆一次 `Arc`）、清扩展线程名、监控（第 12 条）、告警日志（在优先队列里延迟 > 1 s、在线程池里延迟 > 500 ms、耗时 > 10 min、耗时大于周期，`:142-171`），最后进入收尾。
7. **收尾**（`schedule_task(TaskToken*)`，`:431-486`）：令牌在 uncanceled 集里（运行期间被取消）就移除并删除；否则在 running 集里找：找不到返回 `OB_ERR_UNEXPECTED`（WARN）；找到就移除，若周期为 0 或服务已停止，从优先队列里移除第一个指向它的占位（与最后一个元素交换后删尾）并删除；否则（周期任务）把计划时间改成 `now + delay_`、清零 `last_try_pop_time_`/`pushed_time_`，令牌原地留在队列里，`notify_all`。所以周期任务是"上一次结束后再过 delay"，不是固定频率。
8. **`cancel_task(timer, task 或全部)`**（`:348-398`）：在优先队列里删除匹配的令牌（与末尾交换后删尾，交换来的元素在同一位置再判断一次）；未派发的直接删除，已派发的仍被 running 集引用；再把 running 集里匹配的令牌移到 uncanceled 集。不 notify，不等待：正在运行的任务会跑完，然后由收尾删除，不再重排。
9. **`wait_task(timer, task 或全部)`**（`:400-429`）：持 monitor 循环：只要优先队列（含占位）、running 集、uncanceled 集里还有匹配的令牌，就 `timed_wait(10 ms)`；都没有才返回 `OB_SUCCESS`。所以 `cancel_task` 之后 `wait_task` 返回时，该任务不在运行，并且除非有人在 `cancel_task` 之后又排了它，不会再运行（RULEBOOK §2.7）。照 C++ 保留的两种不返回的情形：对没有取消的周期任务调用 `wait_task` 会一直等；任务在自己的 `runTimerTask` 里对自己调用 `wait_task` 会死锁。任务在运行中重排自身（第 10 条）时，新令牌出现在队列里，`wait_task` 继续等；`ObTimer::stop`/`destroy` 先置 `is_stopped_`，任务里的 `schedule` 因而返回 `OB_CANCELED`，等待得以结束。
10. **自重排**：`runTimerTask(self: Arc<Self>)` 里可以 `timer.schedule(self.clone(), …)`（`ob_user_resource_mgr.cpp:321-330`）。新令牌要等当前令牌收尾（离开 running 集）后才可能被派发（第 2 条）。
11. **`stop`**（`:249-290`）：置 `is_stopped_`；删除全部未派发的令牌，清空优先队列；running 集的令牌全部移到 uncanceled；`notify_all`；等到三处都空（`timed_wait(10 ms)`）；再停工作池和调度线程。`start`（`:220-247`）：在 `mutex_` 和 monitor 内，只有已停止时才做：清 `is_stopped_`，`init(4, 10000, "TimerWK")` 后 `set_thread_count(128)`，启动调度线程，置 `is_never_started_ = false`；失败则保持停止。停止后再 `start` 会因工作池 `OB_INIT_TWICE` 而失败（没有 destroy），照抄。`destroy` 只做一次：stop、wait、销毁工作池和调度线程（`:299-310`）。
12. **`ObTimer`**（`ob_timer.cpp`）：`init` 在全局服务从未启动时启动它，失败时（非 Windows）`ob_abort()`（`:30-41`）；已初始化 `OB_INIT_TWICE`；名字按 `copy_buff`（非空且非空串才 `strncpy`，末字节置 0）记下（`ob_timer_service.h:44-50`）。`schedule`：未初始化 `OB_NOT_INIT`，已停止 `OB_CANCELED`（`:131-146`）。`cancel`/`cancel_task` 相同；`cancel_all` = 取消全部再 `wait()`；`wait()` = 等全部（`:83-93,153-209`）。`stop`：置停止、`cancel_all`、`wait`；`destroy`：已初始化时置停止、`cancel_all`、`wait`、清初始化标志、清 run wrapper 和名字（`:72-106`）。`start`：未初始化 `OB_NOT_INIT`，否则清停止标志。`set_run_wrapper_with_ret`：已初始化 `OB_OP_NOT_ALLOW` 并记 ERROR（`:108-120`）。`timer_service_` 为空的分支（`OB_ERR_NULL_VALUE`）只在 `destroy` 之后可能出现，而那时先命中"未初始化"，所以不可达；Rust 保留"有无服务"的原子标志以维持分支结构。
13. **`ObTimerMonitor`**：`start_task`/`end_task` 只在任务的 `timeout_check()` 为真时调用（`ob_timer_service.cpp:112-117,134-136`），而 `src/` 里只有 13 处 `disable_timeout_check()`、没有 `enable_timeout_check()`，所以实际不触发；照样移植。唯一的 seqlock（`ob_timer_monitor.cpp:181-187`）按 RULEBOOK §2.7 改成每条记录一把 `Latch`。`dump`：超时阈值 `clamp(interval × 100, 1 min, 15 min)`，超时记 ERROR；每 6 次打印一次统计并清零 `task_cnt_`（`.cpp:171-224`）；周期 `CHECK_INTERVAL = 10 s`。`ObTimerMonitorTask` 回指宿主（`.h:59-68`），宿主是单例，Rust 里直接调 `ObTimerMonitor::instance()`（RULEBOOK §2.7"宿主是模块的，持有 `&'static`"）。
14. 任务类型名 C++ 是 Itanium 修饰名（`typeid(*task).name()`），Rust 是 `type_name`，只出现在日志里。

### 3.8 `WorkerCtx`

1. **数据与初值**（`worker.cpp:49-62`）：`timeout_ts_ = INT64_MAX`、`ntp_offset_ = 0`、`worker_level_ = INT32_MAX`、`curr_request_level_ = 0`、`disable_wait_ = false`、无会话、无请求；另有线程局部的 `log_reduction_mode_ = NONE`（`worker.h:239-265`）。这些标量放在常量初始化、无 Drop 的线程局部里；会话和装入的 `WorkerVirt` 放在另一个线程局部（有 Drop），并用标量里的标志门控，没有设过就不碰它。外部线程（RULEBOOK 0.5）因此只读到初值，不会触发有 Drop 的线程局部。
2. **装入**：C++ `set_worker_to_thread_local(Worker*)` 把 `THIS_WORKER` 指向另一个对象，数据随之换成那个对象的。全库只有 4 处调用，都在线程开头：`threads.cpp:244`、`ob_th_worker.cpp:212`、`ob_server_runtime.cpp:230`、`main.cpp:795`。Rust 的 `set_worker_to_thread_local(w)` 把本线程的数据复位到初值并装入 `w`，等价。
3. **线程启动前的跨线程写**：`create_worker` 在 `ObThWorker` 的线程启动前写了它的 `worker_level_ = 0`、`group_ = nullptr`（`ob_th_worker.cpp:58-59`）。数据改在线程局部之后，`ObThWorker` 要把初值存在自己的字段里，由它的线程在装入之后自己设置（§6 例 9）。这是唯一一处。
4. **虚函数**：基类 `check_wait()` 调 weak 的 `common_yield()`（从未覆盖：layering 调查 §2.5 列的 6 个被覆盖的 weak 符号里没有它），得 `OB_SUCCESS` → `WS_NOWAIT`（`worker.cpp:39-43,73-81`）；基类 `check_status()` = `check_wait(); return OB_SUCCESS;`（`worker.h:45`，注意它调的是**虚** `check_wait`）；`can_retry`/`need_retry` 为假，`set_need_retry`/`unset_need_retry`/`resume` 什么也不做（`:47-55`）。覆盖者：`ObThWorker`（`ob_th_worker.h:54-64`）、`ObPxWorker`（`ob_px_worker.h:188-192`）。`sched_wait()` 恒为真；`sched_run()` 调 `check_status()`、丢弃结果、返回真（`worker.cpp:83-93`）。
5. **不提供**：`get_sql_arena_allocator`/`get_allocator`（13 处调用，RULEBOOK 0.5、0.7：arena 一律显式传参，调用点由清单第 11 类改写）；`get_cur_request`（唯一的调用在注释里，`ob_trans_service_v4.cpp:964-972`）；`set_group`/`get_group`/`is_group_worker`（只有写入 `nullptr` 的两处，`ob_th_worker.cpp:59`、`ob_th_worker.h:123`，没有读者）；`is_th_worker`（没有读者）；`worker_node_`（`ObServerRuntime` 的工作线程链表，由 observer 改成自己的容器）。
6. **时间**：`get_timeout_remain() = timeout_ts_ − ObTimeUtility::current_time()`（`worker.cpp:96-99`）；`is_timeout() = ObClockGenerator::getClock() >= timeout_ts_`（`:101-104`）；`is_timeout_ts_valid() = timeout_ts_ != INT64_MAX`（`worker.h:91`）。两个时钟不同，照抄。
7. **会话**：拥有型 `Arc`（RULEBOOK 0.5），类型擦除；`get_session::<T>()` 在类型不符或未设置时返回 `None`。C++ 的典型用法是保存、置空或替换、恢复（`ob_dbms_stats_executor.cpp:103-111`），Rust 照写（§6 例 8）。
8. **`with_request`**：C++ 只把请求指针当标志用（`has_req_flag()` 有 4 处读者），在 `ObThWorker::process_request` 里前后各设一次（`ob_th_worker.cpp:156,198`）。Rust 只存标志，闭包结束时恢复原值，嵌套也正确。
9. **钩子**：C++ 的覆盖因线程而异（ReqWorker 线程是 `ObThWorker`，PX 线程是 `ObPxWorker`，其余是基类），一个进程级的钩子表达不了"本线程是哪一种"。所以本文把 RULEBOOK 0.5 的"`check_status` 一类的上层逻辑改用启动时注册的钩子"落实为：上层实现 `WorkerVirt`，在自己的线程开头登记进线程局部；`WorkerCtx` 的同名方法派发给它。见 §8 Q4。派发前先把 `Arc` 克隆出来，不在 `RefCell` 借用期间调用（RULEBOOK 0.5"借用不得跨过任何函数调用"）。

### 3.9 trace id

1. **布局**（`ob_trace_id.h:216-236`，按 Clang 小端位域规则）：`uval_[0]` 的低 32 位是 `ip_`；高 32 位是 `bytes_no_ip_`，其中 bit 32–47 `port_`、48 `is_user_request_`、49 `is_ipv6_`、50–51 `reserved_`、52–55 `inner_sql_id_`、56–63 `sub_task_`；`uval_[1]` 是 `seq_`；`uval_[2]`、`uval_[3]` 是 `ipv6_[0]`、`ipv6_[1]`。用 RULEBOOK §2.12 的 `ob_bitfield!` 写，配布局测试。
2. **`init(addr)`**（`.h:55-70`）：`seq_ = gen_seq()`，清 `is_user_request_`/`reserved_`/`inner_sql_id_`/`sub_task_`，`is_ipv6_` 和 `port_`（截成 16 位）照地址设；IPv6 写 `ipv6_[0..2]`，IPv4 只写 `ip_`。**另一部分不清**：IPv4 时 `ipv6_` 保留上一个 trace id 的值，IPv6 时 `ip_` 保留旧值，照抄。
3. **序号**（`.h:38-44`）：线程局部 `thread_seq` 是 `BATCH = 1 << 20` 的倍数时（含首次的 0），从全局 `seq_generator_` `FAA(BATCH)` 取一批；返回 `++thread_seq`。`main.cpp:667` 把全局初值设为当前时间。
4. **文本**（`.h:30-31,123-138`）：IPv4 `"Y%llX-%016llX-%llx-%llx"`（`uval_[0..4]`），IPv6 `"Z%X-%016llX-%llx-%llx"`（`bytes_no_ip_`、`uval_[1..4]`），走 04 的 `ob_printf!`。`set_char`（`.h:158-176`）按首字符选格式 `sscanf`，匹配项数既不是 0 也不是 4 时返回 `OB_INVALID_ARGUMENT`；首字符都不是时什么也不改、返回成功。
5. `hash()` 依次把四个 `u64` 喂给 `murmurhash`，种子链式传递（`.h:193-201`）。序列化：unis 版本 1，四个 `u64`（`ob_trace_id.cpp:26`）。
6. **线程局部**：本线程的 trace id 是常量初始化、无 Drop 的 `Cell<TraceId>`（C++ `static thread_local TraceId`，`.h:316-320`）。`get_trace_id()` 返回指针，约 159 处调用（`grep -rhoE "ObCurTraceId::get_trace_id\(" src`，不含 `get_trace_id_str`），其中经指针写的只有 `->set(…)` 6 处、`init`/`set`/`reset` 等静态函数另有入口；Rust 返回拷贝，写的地方改调静态函数或 `with_trace_id_mut`。保存指针、之后再经它读的，改成在读的位置再调一次 `get_trace_id()`（读到的是当时的值，与经指针读相同）。
7. `ObTraceIdGuard` 保存的是旧值的拷贝，守卫不持借用，RAII 合规（`.h:341-362`）。

### 3.10 线程局部工具

1. **`ObDITls`**（`ob_di_tls.h:64-90`）：首次 `get_instance` 时堆上构造 `T`（C++ `OB_NEW`，失败返回空，下次再试）；线程退出时析构，并置线程局部的 `is_thread_in_exit`，之后本线程再取**尚未创建**的实例一律返回空。构造期间递归取同一个实例时，C++ 返回占位指针 `0x1`（解引用即未定义行为），Rust 返回 `None` 并记 ERROR——`FIXED(port): ObDITls 构造期间递归取实例得到占位指针`。实例超过 64 KiB 时用 `try_box`（RULEBOOK 0.7）。闭包里只对实例本身做操作，不调用可能再取同一实例的函数（RULEBOOK 0.5）；违反时同样返回 `None` 并记 ERROR，不 panic。
2. **`ObBasicTimeGuard`**（`ob_common_utility.h:81-166`）：构造时若线程开关打开，就对外层守卫 `click(start)` 并把自己设为当前守卫，记开始时间（`get_cur_ts()` = `OB_TSC_TIMESTAMP.fast_current_time()`）；析构时恢复外层并 `click(end)`。`click(mod)`：耗时 = 现在 − 上次；不足 16 条时追加，满了就替换耗时最小的一条（仅当本次更大）；序号递增。`to_string`：按序号排序后打印 `"owner: %s, click_count: %ld, time dist:[%s=%d, …], seq:[%d, …]"`，任一步失败返回 0（`.cpp:169-199`）。Rust 的数据放在线程局部的定长栈（8 层）里，无 Drop、常量初始化：日志路径会用它（`ob_log.h:1228`），而日志可能在 vsag 线程上打。嵌套超过 8 层时按开关关闭处理，只影响日志内容。
3. **`ObFatalErrExtraInfoGuard`**（`.cpp:138-166`）：C++ 构造时把自己登记为线程局部的当前值、析构时恢复上一个；读者（`ob_charset.cpp:2158,2181`、`ob_hang_fatal_error.cpp:46`、`ob_raw_expr.cpp:755`、`ob_basic_session_info.cpp:2155`；`ob_signal_handlers.cpp:155` 已注释掉）取当前值打印。但全库没有构造这个类或它唯一的子类 `ObSqlFatalErrExtraInfoGuard`（`ob_sql_utils.h:1045-1063`，只定义了 `to_string`，`ob_sql_utils.cpp:2971`）的地方——`grep -rn FatalErrExtraInfo src` 除定义和上述读者外没有结果——所以线程局部恒为空，读者的非空分支不可达。装入侧与 RULEBOOK 0.8 列的 `install_ob_signal_handler` 同类（没有调用者），交缺口清单第 16 类确认；Rust 不提供装入，`get_thd_local_val_ptr()` 恒返回 `None`，读者照原样译。
4. `SQL_INFO_GUARD` 只写线程局部，读者 `coredump_cb` 是死代码（`ob_signal_handlers.cpp:202`），Rust 只对参数求值。`ObSignalHandlerGuard` 改的线程局部只被从未安装的分派函数读（`ob_signal_handlers.cpp:52-87`），Rust 是空类型。两者在 C++ 里就没有可观察效果。

### 3.11 栈

1. **常数**（`ob_smart_call.h:28-36`、`ob_common_utility.cpp:35`）：累计上限 10 MiB；每段 `2 MiB − 2 × 17 KiB = 2,062,336` 字节；`SMART_CALL` 余量 64 KiB，`SMART_CALL_LARGE` 192 KiB；`check_stack_overflow` 默认余量 32 KiB。
2. **`check_stack_overflow(is_overflow, reserved, used_size)`**（`ob_common_utility.cpp:45-83`）：取当前栈段 `[stack_start, stack_start + size)`；`eof = start + size`；当前位置取一个局部变量的地址；取栈属性失败时记 ERROR、`is_overflow = true` 并返回该错误；`size < reserved` 返回 `OB_ERR_UNEXPECTED` 且 `is_overflow = true`；当前位置高于 `eof` 同样；否则 `used = eof − cur`，写出 `used_size`，`used > size − reserved` 时 `is_overflow = true` 并记 WARN "stack possible overflow"（带 `lbt()`）。返回值只表示"能否判断"，溢出靠 `is_overflow`。无参版本（`.h:39-44`）：成功且溢出时返回 `OB_SIZE_OVERFLOW`，否则原样返回。
3. **栈属性**（`.cpp:85-136`）：线程局部缓存当前段的低地址和大小；为空时取原生栈：Linux `pthread_getattr_np` + `pthread_attr_getstack`，macOS `pthread_get_stackaddr_np`（栈顶）减 `pthread_get_stacksize_np`，Windows `GetCurrentThreadStackLimits`。换栈时改成新段，回来时恢复（`ob_smart_call.h:47-52`）。所以 `check_stack_overflow` 在扩展段上按扩展段计算；C 解析器的回调 `check_stack_overflow_c`（`ob_sql_utils.cpp:1964-1970`）运行在同一个栈上，也按当前段算。
4. **`smart_call`**（`ob_smart_call.h:112-128`）：先 `check_stack_overflow(is_overflow, reserved)`，出错就原样返回错误、不调用 `func`；不溢出就直接调用；溢出时 `alloc_stack(STACK_PER_EXTEND)`：线程局部 `all_stack_size` 为 0 时先设为**当前段**的大小（第一次扩栈总发生在原生栈上，所以就是原生栈的大小；主线程是 1 MiB 的新栈），`all_stack_size + 段大小 > ALL_STACK_LIMIT` 返回 `OB_SIZE_OVERFLOW`（不调用 `func`，不记日志），段分配失败返回 `OB_ALLOCATE_MEMORY_FAILED`，否则累加，在新段上调用 `func`，回来后归还并减回（`:72-96`）。
5. **能扩几段**：原生栈 224 KiB（macOS）或 223 KiB（Linux）时 `229,376 + 4 × 2,062,336 = 8,478,720 ≤ 10 MiB`，第 5 段 `10,541,056` 超限，所以最多 4 段；复核 C27、行为审查 F3 的口径相同。
6. **扩展段**：C++ 每次用 `ob_malloc` 分配、不加保护页、不进系统调用（`protected_stack_allocator.cpp:69-72,95-135`）。Rust 按 RULEBOOK 0.8：从全局分配器申请（大于 64 KiB，用可失败的分配），每线程缓存，按栈式空闲表复用（最近归还的最先复用），线程退出时随线程局部析构释放；**不得每次扩栈都 mmap/munmap**；段同样不加保护页。用 `psm::on_stack(base, size, f)` 切栈，切换前把线程局部的段边界改成新段，回来后恢复。段按页对齐。每线程最多同时占用 4 段，所以缓存最多 4 段（§8 Q8）。
7. **原生栈大小**：C++ `global_thread_stack_size = max(256 KiB, stack_size 配置) − 16 KiB − 17 KiB`，macOS 再按页取整（`ob_server.cpp:1991-1997`；配置默认 256 KiB，所以 Linux 223 KiB、macOS 224 KiB，复核 C16）。这段在 observer 里照抄；`NATIVE_STACK_FACTOR`（RULEBOOK A.3）由 `Threads` 在建线程时乘上去，日志里打印的仍是 C++ 的值。`main` 的 1 MiB 新栈（`main.cpp:877`）不乘系数（§8 Q9）。
8. `SMART_CALL` 的宏体里声明了自己的 `int ret`（`ob_smart_call.h:114`），实参里写的 `ret` 绑定到这个内层变量。全库只有一处利用了这一点：`ob_json_base.cpp:2980` 的 `SMART_CALL(OB_FAIL(seek(…)))`，seek 的错误赋给了内层 `ret`，宏返回 0/1，外层 `ret` 不变；扩栈路径上是 `int ret = OB_FAIL(…)` 自引用初始化（未定义行为）。Rust 的闭包捕获不到内层变量，这一处按 §6 例 10 的写法翻译。
9. C++ 只在 x86-64 和 aarch64 上换栈，其余架构直接调用（`ob_smart_call.h:44-68`）；Windows 换栈时还改 TIB 的 `StackBase`/`StackLimit`（`:53-63`）。Rust 用 psm 的同一组平台，Windows 只要求能 `cargo check`（D18）。

### 3.12 信号

1. **屏蔽的集合**（`ob_signal_handle.cpp:97-117`）：SIGPIPE、SIGTERM、SIGUSR1，加 40–53、55–57、59、60、62、63、64（跳过 54、58、61，复核 C17）。`change_signal_mask()` 对这个集合 `pthread_sigmask(SIG_BLOCK)`，失败返回 `OB_ERR_UNEXPECTED` 并记 ERROR（`:83-95`）；`add_signums_to_set` 里 `sigaddset` 返回非 0 也返回 `OB_ERR_UNEXPECTED`。
2. **平台差异**（行为审查 F4、复核 C17）：Linux 的 `sigaddset` 是函数，40–64 都是合法的实时信号号（glibc 的 SIGRTMIN 是 34）；macOS 的 `<signal.h>` 把 `sigaddset` 定义成宏 `(*(set) |= __sigbits(signo), 0)`，`__sigbits` 对大于 32 的信号号得 0，即静默不置位、返回 0——基线二进制里内联出的掩码常量是 `0x20005000`（只有 SIGPIPE、SIGTERM、SIGUSR1）。而 `libc::sigaddset` 调的是函数版本，对 40 返回 −1，照 C++ 判断就会让实例起不来，所以 Rust 在 macOS 上复刻宏：`1 ≤ signo ≤ 32` 时置位，其余不置位，一律返回 0。Windows 用 `ob_signal_handle.h:36-51` 的内联实现（64 位掩码、`pthread_sigmask` 空操作、`sigtimedwait` 睡满超时后返回 −1）。
3. **时机**：C++ 在 `inner_main` 里先 `MPRINT` 再屏蔽（`main.cpp:673-681`），早于任何线程创建，之后创建的线程都继承这个掩码。Rust 按 RULEBOOK 0.5 把它提到 `main` 的第一条语句（§2.15）。Rust 运行时在进入 `main` 之前做的事不创建线程：把 SIGPIPE 设成 `SIG_IGN`，为 SIGSEGV/SIGBUS 装栈溢出报告用的处理函数并给主线程建备用信号栈。前者与 C++ 等价（SIGPIPE 本来就被屏蔽，处理线程收到后什么也不做，`ob_signal_handle.cpp:126-128`）；后者只改变崩溃时的表现（真正碰到保护页时打印一行后 abort），不在裁判范围内。
4. **处理线程**（`ob_signal_handle.cpp:56-81`）：名字 "SignalHandle"；循环到 `has_set_stop()`：Linux `sigtimedwait(&set, NULL, {1 s, 0})`，超时返回 −1，每秒复查停止标志；macOS 用 `sigwait` 模拟、**忽略超时**（`:25-37`），只有收到信号才会醒。
5. **动作**（`:120-217`）：先记 INFO "received signal"。SIGPIPE 什么也不做；**SIGTERM → `raise(SIGKILL)`**（退出状态 137，生命周期场景会测到；Windows 上 `TerminateProcess(…, 1)`）；**SIGUSR1** → `ObServer::prepare_stop()`（置标志后睡 5 s，`ob_server.cpp:1493-1498`）、`set_stop()`（停 sql-nio、置 `stop_`、状态改为 STOPPING，`:1510-1517`），主线程的 `ObServer::wait()` 每 3 s 查一次 `stop_`，看到后 `_Exit(0)`（`:1682-1697`）；40 检查日志文件；41/42 降低/提高日志级别；49 打印内存；53 设模块日志级别；59 详细内存和 SQL 统计；62 从文件生成转储任务；63 打印 memstore；43、45、46、50、51、57、60 什么也不做；其余记 WARN "Ignore unknown signal"。这些都在 observer 的译文里，用 `oblib::signal::raise`、`_exit` 等安全包装。
6. **macOS 的唤醒**：`~ObSignalHandle()` 在 macOS 上对信号线程 `pthread_kill(SIGUSR1)` 以唤醒 `sigwait`（`ob_signal_handle.h:65-75`）。在生产路径上这个析构不会执行：`ObSignalHandle` 是 `ObServer` 单例的成员，正常退出走 `_Exit(0)`，失败走 `_exit(1)`（`main.cpp:816-824`）。Rust 里 `ObServer` 是永不释放的模块（RULEBOOK 0.5），同样不执行；observer 照样把析构体译成 `Drop`，用本组的 `pthread_kill`/`SIGUSR1`。
7. **退出**：`_exit`/`_Exit` 不跑 `atexit`、不刷 stdio，Rust 的 `std::process::exit` 会跑，所以提供 `oblib::signal::_exit`。
8. **内存探测**（替代 `memory_dump.cpp:40-143` 的 `sigsetjmp`/`siglongjmp`）：`probe_read_memory(addr, out)` 只用系统调用读本进程内存：Linux `process_vm_readv(getpid(), …)`（读本进程不受 ptrace 限制），返回 `ENOSYS`/`EPERM`（某些容器的 seccomp 会禁）时退回"往管道 `write`，`EFAULT` 表示不可读"；macOS `mach_vm_read_overwrite(mach_task_self(), …)`；Windows `ReadProcessMemory(GetCurrentProcess(), …)`。读不全就返回假，绝不触发 SIGSEGV。C++ 有三处 `do_with_segv_catch`：`find_chunk` 向前扫描块头（`:366-398`），按块解析并统计（`:616-653`），按块解析并打印（`:766-791`）。Rust 里这三处读 `AChunk`/`ABlock`/`AObject` 的地方都先用探测把字节拷出来，再按 `oblib::pod` 的视图解码；原来"越界即 `has_segv`"的分支改成"探测失败即 `has_segv`"，之后的处理不变（`find_chunk` 返回空，后两处 `segv_cnt++` 后跳过这一块）。逐窗口探测比直接读慢，只在诊断命令 `DUMP MEMORY` 里用到。C++ 在扫描期间给整个进程装了处理函数（`:84-121`，SIGSEGV、SIGABRT），别的线程这时出错会 `siglongjmp` 到它自己未初始化的缓冲，Rust 不再有这个问题。RULEBOOK 0.7 取消了自带分配器的后端，这几处在 Rust 里是否保留由 01 决定；保留时用这个探测。

### 3.13 `main` 与静态初始化

1. **顺序**（RULEBOOK 0.5）：屏蔽信号 → force 单例 → 注册钩子 → 启动日志写线程 → 照 C++ 的 `main`。前三步都不得创建线程。C++ 在屏蔽信号之前做的事（Linux 的 `disable_hugepage_for_self_text`、mmap 新栈、macOS 的 malloc zone 配置、读主线程名、`MPRINT`）都不创建线程，提到屏蔽之后不改变行为。
2. **force 列表**：脚本读原版二进制的初始化段（macOS 基准二进制的 `__DATA,__mod_init_func`，Linux 的 `.init_array`），按顺序把每个 `_GLOBAL__sub_I_<编译单元>` 映射到该编译单元里需要动态初始化的命名空间作用域对象和类静态成员，再经 `symbols.tsv` 映射到 Rust 的 `LazyLock` 静态量，写成 `run_static_init_side_effects()`（09 的 `generated/bindings.rs`）里的 `LazyLock::force(&…);` 序列；`const` 初始化的静态量不列。函数内的 `static`（`get_instance()` 一类）在 C++ 里本来就是首次使用时初始化，不在初始化段里，也不列——它们在 Rust 里同样首次使用时初始化。11 处 `constructor`/`init_priority` 属性（`grep -rnE "__attribute__\s*\(\(\s*(constructor\|init_priority)"`）按 RULEBOOK 0.4 改成组合根里的显式调用；其中 `ob_tsi_utils.cpp:69` 的 `FreeItid`（pthread key）在 Rust 里由 `get_itid` 首次使用时创建，不需要列。
3. **初始化函数的约束**（RULEBOOK 0.5、审查 M12）：`LazyLock`/`OnceLock` 的初始化函数不得创建线程、不得取 `oblib::sync` 的锁、不得等待别的线程。本组的 `ObTimerService::new`、`ObTimerMonitor::new`、`ObSimpleThreadPoolDynamicMgr::new` 都满足（线程只在 `start`/`init` 时创建）。`with_static_init_phase` 期间 `Thread::start` 在 debug 构建下 `debug_assert!(!in_static_init_phase())`，把违反者在测试里暴露出来；02 可以在锁的慢路径上做同样的检查。
4. `ob_main::main` 的返回值经 `std::process::exit` 成为退出状态（与 C++ `return ret` 一样被系统截成低 8 位）；C++ 的 `return` 还会跑静态对象的析构，Rust 的静态量没有析构，只在早期失败返回的路径上有差别，不可观察。

### 3.14 外部线程（RULEBOOK 0.5）

外部线程（vsag/OpenMP 线程、共存期的 C++ 线程）经分配钩子、日志回调等进入 Rust 时，本组能被触及的状态全部是常量初始化、无 Drop 的线程局部：tid 缓存、三个线程名缓冲、trace id、`WorkerCtx` 的标量、`ObBasicTimeGuard` 的定长栈、itid（pthread key 归还）。`WorkerCtx` 的会话和 `WorkerVirt` 只有设置过才会访问；`Thread::current()` 返回无效句柄。sql-nio 的 reactor 线程和 standby 的 tokio 线程是 Rust std 线程，不算 0.5 意义上的外部线程，它们取锁时分配的 itid 照常在线程退出时归还。

### 3.15 内存序

| 字段 | C++ | Rust | 依据 |
|---|---|---|---|
| `Threads::stop_` | const 重载 `ATOMIC_LOAD`；非 const 重载经 `bool&` 普通读写；`stop()`/`start()` 普通写 | `AtomicBool`，读写一律 `SeqCst`（覆盖 `ATOMIC_LOAD` 那一处，也比停止标志所需的 `Relaxed` 强） | `threads.h:99-108`、`threads.cpp:220,289` |
| `Thread` 的停止标志 | 普通 `bool`，别的线程写 | `AtomicBool` `Relaxed`（停止标志，RULEBOOK §2.7） | `thread.h:130,140-144`、`thread.cpp:122,181` |
| 线程总数、`create_ret_` | `ATOMIC_FAA` / `ATOMIC_STORE`/`LOAD` | `SeqCst` | `thread.cpp:76,132,151,346,383,394,396` |
| `itid_slots` | `ATOMIC_LOAD`/`ATOMIC_BCAS` | `SeqCst` | `ob_tsi_utils.cpp:81-83,120-121,137` |
| `max_itid`、`max_used_itid` | 锁内普通写 / `ATOMIC_STORE`；读 `ATOMIC_LOAD` | 写 `Release`（普通写）/ `SeqCst`；读 `SeqCst` | `.cpp:97-100,127-128`、`.h:62,67` |
| `itid_lock` | `ATOMIC_BCAS` 加锁、普通写 0 解锁 | `compare_exchange(SeqCst)`、`store(false, Release)` | `.cpp:95-101,125-130` |
| `ObAdaptiveWorkerPool` 两个计数 | `std::atomic` 显式内存序 | 照写（RULEBOOK 0.8"显式弱序照原样写"） | `ob_adaptive_worker_pool.h:80-117` |
| `ObSimpleThreadPoolBase` 的 `stop_`、`is_inited_` | 普通 `bool`，工作线程不加锁读 | `AtomicBool`，读 `Acquire`、写 `Release`（门控排空等后续动作） | `.ipp:84-85,116,133,209,258,286` |
| `min/max_thread_cnt_`、`cur_worker_idx_` | 普通整数，并发读写 | `AtomicI64`/`AtomicU64` `Relaxed`（配置值、诊断值） | `.ipp:63-68,140,179-180,196-199,248` |
| `ObReentrantThread::stop_` | `volatile`；`has_set_stop` 用 `ATOMIC_LOAD`；写在锁内 | `AtomicBool` `SeqCst` | `.h:48-52,73`、`.cpp:84-86,101` |
| `ObTimer` 的 `is_inited_`/`is_stopped_`，`ObTimerService` 的 `is_never_started_`/`is_stopped_` | 普通 `bool`，不加锁读 | `AtomicBool`，`Acquire`/`Release` | `ob_timer.h:73-74`、`ob_timer_service.h:156-157,180-181` |
| `SeqGenerator::seq_generator_` | `ATOMIC_FAA`；`main` 里普通写 | `fetch_add(SeqCst)`；`store(SeqCst)` | `ob_trace_id.h:41`、`main.cpp:667` |
| `ObTimerMonitor` 记录 | seqlock | 每条一把 `Latch`（RULEBOOK §2.7） | `ob_timer_monitor.cpp:123-187` |

### 3.16 标记汇总

- `FIXED(port)`：`has_set_stop()` 的 `bool&` 数据竞争（§3.3 第 10 条）；对空引用取地址（§3.3 第 11 条）；`ori_tname` 无结尾 NUL（§3.2 第 2 条）；`idle_wait` 不持锁等待（§3.5 第 5 条）；定时器投递失败留下悬垂令牌（§3.7 第 5 条）；`ObDITls` 占位指针（§3.10 第 1 条）；`cur_worker_idx_` 的数据竞争（§3.6 第 3 条）；`SMART_CALL` 扩栈路径上的自引用初始化（§3.11 第 8 条，§6 例 10）；`light_backtrace` 在 aarch64 上越出栈范围的读（§4 的 `arch` 行）。
- `BUG(port)`：`Threads::start` 重入（§3.3 第 7 条）；`try_expand_one` 在池停止后无限重试（§3.6 第 6 条）；线程退出时取到已归还的 itid（§3.1 第 3 条）；`ob_json_base.cpp:2980` 吞掉 seek 的错误（§6 例 10）。
- 没有用到 A.4 的 `DIVERGE` 项。只影响日志文本的差别（任务类型名、`ObBasicTimeGuard` 超过 8 层）不需要标记。

## 4. unsafe

对外的 API 全部是安全函数。unsafe 只出现在下列路径里，每处写 `// SAFETY:`，并开 `deny(unsafe_op_in_unsafe_fn)`。

| 路径 | 用途 | 估计处数 | 安全论证要点 |
|---|---|---|---|
| `thread/stack/**`（§3 第 4 项） | `psm::on_stack`；Linux `pthread_getattr_np`/`pthread_attr_getstack`/`pthread_attr_destroy`、macOS `pthread_get_stackaddr_np`/`pthread_get_stacksize_np`、Windows `GetCurrentThreadStackLimits`；`mmap`/`munmap`（main 的新栈）；段的原始指针 | 约 10 | 段由线程局部缓存独占持有，在回调返回前不会被归还或释放（栈式空闲表：使用中的段不在表里）；段按页对齐，满足 psm 对齐要求；`panic = "abort"`，回调不会展开穿过换栈帧；段边界在切换前写入线程局部、返回后恢复，`check_stack_overflow` 只读这两个整数；`ObStackMem` 拥有映射，`munmap` 消耗它，不会二次释放 |
| `thread/sys/**`（修订 R3） | `syscall(SYS_gettid)`/`pthread_threadid_np`、`prctl`/`pthread_setname_np`/`pthread_getname_np`、`pthread_set_qos_class_self_np`/`setpriority`、`pthread_key_create`/`pthread_setspecific`、`sysconf`；Windows 的 `GetCurrentThreadId`/`SetThreadDescription`/`FlsAlloc`/`FlsSetValue` | 约 15（按上面每个平台各一处调用数） | 系统调用只传本线程的、长度已截断的缓冲；pthread key 的值是整数（itid+1），析构回调只调 `free_itid`，不解引用指针 |
| `signal/**`（§3 第 5 项） | `sigemptyset`/`sigaddset`/`sigismember`/`pthread_sigmask`/`sigtimedwait`/`sigwait`/`raise`/`pthread_self`/`pthread_kill`/`_exit`/`signal`；`process_vm_readv`/`pipe`/`write`/`close`、`mach_vm_read_overwrite`/`mach_task_self`、`ReadProcessMemory` | 约 20 | `ObSigSet` 总是先 `sigemptyset` 初始化；`PthreadHandle` 只从 `pthread_self()` 得来，`pthread_kill` 对已退出线程的结果由系统定义（C++ 同样）；内存探测从不解引用 `addr`，只把它交给内核，写入的是调用方的 `&mut [u8]`；macOS 的 `sigaddset` 宏复刻是纯位运算，不需要 unsafe |
| `arch/**`（§3 第 6 项） | `asm!("yield")`、`mrs cntvct_el0`、`mrs cntfrq_el0`、读 rbp/x29、`xgetbv`；`_rdtsc`/`__rdtscp`/`__cpuid_count`；帧指针链上的读取 | 约 12 | 指令都只读寄存器、无内存副作用（`options(nomem, nostack)`）；`xgetbv` 先查 OSXSAVE；帧指针链只在 `[stack_lo, stack_hi − 16)` 内读 8 字节对齐的地址，C++ 在 aarch64 上读 `rbp + 16` 之前不检查范围（`ob_backtrace.cpp:96-98`），Rust 补上检查——`FIXED(port): light_backtrace 越出栈范围的读` |

**修订 R3 不获批时的退路**：`thread/sys` 里的系统调用改放进 `thread/stack/**`（同属"线程与栈"，但偏离该条目的本意，所以先走修订）。如果这也不被接受，只能退到安全 Rust：线程名只能在建线程时经 `std::thread::Builder::name` 设置（主线程和外部线程的名字读不到），tid 退化成 `std::thread::ThreadId`（日志行头的 tid 与系统工具对不上），itid 的归还改用"线程局部守卫的 Drop"——这会让 vsag 线程经日志取锁时注册一个有 Drop 的线程局部，违反 RULEBOOK 0.5 的字面，届时要另外规定外部线程路径不得取 `oblib::sync` 的锁。

`unsafe impl Send/Sync`：`HostRef`、`ObSigSet`、`PthreadHandle`、`ObStackMem` 都靠字段自然得到或不需要。本组不写任何 `unsafe impl`。

## 5. 测试

单元测试放在各模块的 `#[cfg(test)]` 里；需要 C++ 参照的金标准测试放在 `rust/port/oblib/tests/`，golden 文件由 5b 阶段的 C++ 测试程序产出（RULEBOOK 0.2）。

**itid**
1. 单线程：首次取得最小空闲号；`get_max_itid`/`get_max_used_itid` 的 +1 语义；归还后 `max_itid` 用最高置位重算。
2. 多线程：N 个线程并发取号互不相同且都 < N（无别的线程时）；线程退出后号被复用为最小空闲号；Linux 上 pthread key 在 `thread_local!` 析构之后才归还（在一个线程局部的 Drop 里调 `get_itid()`，检查得到的是原号）。
3. loom：在缩小的位图（2 个槽）上模型检查分配、归还和 `max_itid` 重算；Miri 跑单线程部分。
4. `icpu_id() == get_itid() & (OB_MAX_CPU_NUM - 1)`；`get_max_icpu_id() == OB_MAX_CPU_NUM`。

**tid 与线程名**
5. 金标准：`set_thread_name(type, idx)` 对长度 0、14、15、16、20 的 `type` 和 idx 0、9、10、2⁶⁴−1 产出的 tname 与 ori 名缓冲逐字节对照 C++（C++ 程序直接调 `lib::set_thread_name` 后转储两个缓冲）。
6. OS 名：设名后用 `pthread_getname_np`（Linux 另读 `/proc/self/task/<tid>/comm`）读回；`ob_gettid()` 与系统调用一致，`reset_tid_cache()` 后重取。
7. 新建的 `Threads` 线程在 Linux 上继承创建者的名字、在 macOS 上为空。

**线程与线程池**
8. `Threads`：start/stop/wait/destroy；`set_thread_count` 扩、缩（含 `async_recycle`）；`thread_recycle`/`try_thread_recycle` 只回收自标停止的线程并保持顺序；`start` 返回时子线程已登记；`Runnable::run` 覆盖生效；run wrapper 的 `pre_run`/`end_run` 各调一次。
9. 线程数上限：用测试钩子把上限调小，验证 `OB_SIZE_OVERFLOW` 和计数回退。
10. 栈大小：Linux 上读回新线程的 `pthread_getattr_np` 大小 ≥ `stack_size_ × NATIVE_STACK_FACTOR`；macOS 上非页倍数的 `stack_size_` 得到 512 KiB（顺带实测系统默认值，§3.3 第 2 条）。
11. `Thread::current()`：在 std 直接创建的线程上 `is_valid()` 为假、`has_set_stop()` 为假、`stop()` 无效果。
12. `ObReentrantThread`：`logical_start`/`logical_stop`/`logical_wait` 与 `run2` 的运行计数；`destroy` 让阻塞在 3 s 等待里的线程退出；`idle_wait(0)` 等到广播。
13. `ObSimpleThreadPoolBase`：`init` 不建线程；`push` 后按需扩出一个线程；队列满返回 `OB_EAGAIN` 且任务留在调用方；停止后 `OB_IN_STOP_STATE`；停止时剩余任务走 `handle_drop`；空闲 1 s 后收缩到下限；回收线程 3 s 内回收已退出的工作线程；`set_adaptive_thread`/`set_thread_count` 的参数校验。
14. `ObAdaptiveWorkerPool`：`try_expand_one` 不超过上限、`try_shrink_one` 不在队列非空时缩到 0；loom 检查并发扩缩下 `total_cnt_` 不越界。
15. TimerWK：服务启动后没有工作线程；排一个任务后出现一个；空闲约 2 s 后回到 0。

**定时器**（时间相关的断言留出余量）
16. 一次性、周期（结束后再过 delay，用一个会睡 50 ms 的任务验证不是固定频率）、`immediately`。
17. 同一定时器的两个任务从不并发（任务内用原子计数检测重入）；不同定时器的任务可以并发。
18. 取消未运行的任务：不再运行，`task_exist` 为假；取消正在运行的任务：跑完、不再重排，`wait_task` 在它跑完后 10 ms 内返回。
19. `cancel_task` + `wait_task` 的保证：在任务运行期间从另一线程取消并等待，返回后计数器不再增长；对未取消的周期任务 `wait_task` 不返回（用超时断言）。
20. 自重排任务；`ObTimer::stop` 之后任务里的 `schedule` 返回 `OB_CANCELED`，`stop` 能返回。
21. `ObTimerService::stop`：队列里未派发的任务被丢弃，正在运行的跑完；停止后收尾不重排周期任务。
22. 投递失败路径（把工作池队列上限调成 1）：令牌被移除，`wait_task` 能返回。
23. `ObTimer` 的错误码：未初始化 `OB_NOT_INIT`、重复初始化 `OB_INIT_TWICE`、初始化后设 run wrapper `OB_OP_NOT_ALLOW`。
24. 宿主为 `Weak` 的任务在宿主释放后运行即返回。

**WorkerCtx 与 trace id**
25. 初值；`set_worker_to_thread_local` 复位；`check_status` 在无装入时成功、装入后派发；`sched_run` 吞掉错误返回真；`get_timeout_remain`/`is_timeout`。
26. 会话：设置、按类型取回、类型不符返回 `None`、恢复；`with_request` 嵌套恢复。
27. 外部线程：在 std 直接创建的线程上调用全部只读接口，用一个计数的测试分配器确认没有为线程局部分配内存（即没有触及有 Drop 的线程局部）。
28. 金标准：`TraceId` 的 `to_string`（IPv4、IPv6、各位域取极值）、`set_char`（合法、部分匹配、未知前缀）、`hash()`、序列化字节，向量由 C++ 程序产出；布局测试核对 `uval_[0]` 各位域的位置。
29. `SeqGenerator`：首次取批、批内递增、跨批再取；`ObTraceIdGuard` 恢复。

**线程局部工具**
30. `ObDITls`：惰性创建、每线程一份、线程退出后不再创建、构造期间递归取得 `None`。
31. `ObBasicTimeGuard`：16 条以内追加、满后替换最小值、嵌套 click；`to_string` 的文本对照 C++（金标准，C++ 程序用固定时间源构造守卫）。
32. `with_static_init_phase`：闭包内 `in_static_init_phase()` 为真、返回后为假；debug 构建下闭包内调 `Threads::start` 触发断言（`#[should_panic]`）。`ObFatalErrExtraInfoGuard::get_thd_local_val_ptr()` 为 `None`。

**栈**
33. `check_stack_overflow`：在已知深度的递归里核对 `used_size` 单调、余量判断与公式一致；无参版本在溢出时返回 `OB_SIZE_OVERFLOW`。
34. `smart_call!`：用固定帧大小的递归函数，原生栈 224 KiB（Linux 上 223 KiB）时恰好扩 4 段后返回 `OB_SIZE_OVERFLOW`；`all_stack_size` 回到原生栈大小；第二次跑同样的递归不再向全局分配器申请（计数的测试分配器）；`smart_call_large!` 在 192 KiB 余量处就扩段。
35. 扩展段上的 `check_stack_overflow` 按段计算；嵌套扩栈后逐层恢复段边界。
36. `call_with_new_stack`：在 1 MiB 新栈上 `get_stackattr` 返回新栈，返回后恢复。
37. 这些测试依赖 psm 的汇编，不进 Miri；另在 ASan 构建下跑一遍。

**信号与 arch**
38. `sigaddset`：macOS 上 40 不置位且返回 0；Linux 上 40–64 置位；屏蔽后的掩码在 macOS 上等于 `0x20005000`（与基线二进制一致，复核 C17）。
39. `change_signal_mask` 后新建的线程（`Threads` 和 std 直接创建的）掩码相同；Linux 冒烟测试读全部线程 `/proc/self/task/*/status` 的 `SigBlk`（RULEBOOK A.4 裁判改动第 6 项）。
40. `sigtimedwait`：Linux 上超时返回 −1；给自己发 SIGUSR1 后返回 SIGUSR1；macOS 上 `pthread_kill(SIGUSR1)` 唤醒 `sigwait`。
41. `probe_read_memory`：可读页返回真且内容正确；`mprotect(PROT_NONE)` 的页、未映射地址返回假且进程不收到信号；Linux 上模拟 `EPERM` 走管道退路。
42. `arch`：`rdtsc` 不减；aarch64 上 `cntfrq() > 0`；`cpuid(0)` 的厂商串；`walk_frame_pointers` 至少回溯到调用者且不越出栈范围；CI 脚本反汇编 `oblib::sync::pause` 确认 aarch64 上是 `yield`、x86-64 上是 `pause`（复核 C29 的做法）。
43. 生命周期场景（裁判，RULEBOOK A.4 第 5 项）：SIGTERM 后退出状态 137；SIGUSR1 后约 5–8 s 以状态 0 退出。

## 6. 翻译者怎么用

**例 1：线程池子类**（`src/storage/memtable/ob_lock_wait_mgr.h:179`、`.cpp:91-120,172-197`）

```cpp
class ObLockWaitMgr: public share::ObThreadPool { … };
share::ObThreadPool::set_run_wrapper(share::server_runtime());          // init
int ObLockWaitMgr::start() { int ret = share::ObThreadPool::start(); … }
void ObLockWaitMgr::run1() {
  lib::set_thread_name("LockWaitMgr");
  while(!has_set_stop() || !is_hash_empty()) { … ob_usleep(100000, true/*is_idle_sleep*/); }
}
```

```rust
pub struct ObLockWaitMgr { pub base: Threads, /* … */ }
impl Runnable for ObLockWaitMgr {
    fn run1(&self) {
        set_thread_name_1(b"LockWaitMgr");
        while !self.base.has_set_stop() || !self.is_hash_empty() { /* … */ ob_usleep(100000, true); }
    }
}
impl ObLockWaitMgr {
    pub fn init(&self, /* … */) -> ObResult { /* … */ self.base.set_run_wrapper(Some(share::server_runtime())); /* … */ }
    pub fn start(&'static self) -> ObResult {           // 模块：&'static self（§3.4）
        let ret = self.base.start(HostRef::Static(self));
        /* TRANS_LOG … */
        ret
    }
}
```

**例 2：`has_set_stop` 的写与 `Thread::current()`**（`src/oblib/lib/thread/ob_simple_thread_pool.ipp:251`、`src/observer/omt/ob_server_runtime.cpp:253,284`、`src/logservice/palf/log_io_worker.cpp:224`）

```cpp
this->has_set_stop() = true;
while (!Thread::current().has_set_stop()) { … Thread::current().stop(); … }
&& false == (OB_NOT_NULL(&lib::Thread::current()) ? lib::Thread::current().has_set_stop() : false)
```

```rust
self.base.set_stop();
while !Thread::current().has_set_stop() { /* … */ Thread::current().stop(); /* … */ }
&& false == (if Thread::current().is_valid() { Thread::current().has_set_stop() } else { false })
```

**例 3：`ObReentrantThread` 子类**（`src/oblib/lib/thread/ob_async_task_queue.h:95-125`、`.cpp:41-56,98-148,156-159`）

```cpp
class ObAsyncTaskQueue : public ObReentrantThread {
  virtual void run2();
  virtual int blocking_run() { BLOCKING_RUN_IMPLEMENT(); }
};
… create(thread_cnt, thread_name) …                      // init
void ObAsyncTaskQueue::run2() { … while (!stop_) { … ObCurTraceId::init(zero_addr); ret = task->process(); … } }
int ObAsyncTaskQueue::start() { return logical_start(); }
```

```rust
pub struct ObAsyncTaskQueue { pub base: ObReentrantThread, /* … */ }
impl ObReentrantThreadVirt for ObAsyncTaskQueue {
    fn base(&self) -> &ObReentrantThread { &self.base }
    fn run2(&self) { /* … */ while !self.base.has_set_stop() { /* … */ ObCurTraceId::init(&zero_addr); /* … */ } }
    fn blocking_run(&self) -> ObResult { blocking_run_implement!(self) }
}
// init(&'static self, …)：ob_fail!(ret = self.base.create(HostRef::Static(self), thread_cnt, thread_name, REENTRANT_THREAD_COND_WAIT))
pub fn start(&self) -> ObResult { self.base.logical_start() }
```

**例 4：定时任务与宿主**（`src/storage/tablelock/ob_lock_table.h:174-181`、`.cpp:38-43,230-241`）

```cpp
class CheckObjLockTask : public common::ObTimerTask {
  void runTimerTask() override;  ObLockTable &lock_table_;
};
void ObLockTable::CheckObjLockTask::runTimerTask() { if (OB_FAIL(lock_table_.check_and_clear_obj_lock(true))) {} }
check_obj_lock_timer_.cancel_task(check_obj_lock_task_);  check_obj_lock_timer_.wait_task(check_obj_lock_task_);
```

```rust
pub struct CheckObjLockTask { base: ObTimerTask, ls_: Weak<ObLS> }   // 宿主不是模块：持 Weak（RULEBOOK §2.7），落点按 types.tsv
impl ObTimerTaskVirt for CheckObjLockTask {
    fn base(&self) -> &ObTimerTask { &self.base }
    fn runTimerTask(self: Arc<Self>) {
        let mut ret: ObResult = Ok(());
        let Some(ls) = self.ls_.upgrade() else { return; };          // 宿主已释放：直接返回
        if ob_fail!(ret = ls.lock_table_.check_and_clear_obj_lock(true)) {}
    }
}
// ObLockTable::destroy
let _ = self.check_obj_lock_timer_.cancel_task(&*self.check_obj_lock_task_);
let _ = self.check_obj_lock_timer_.wait_task(&*self.check_obj_lock_task_);
```

**例 5：任务重排自身**（`src/sql/session/ob_user_resource_mgr.cpp:306-330`）

```cpp
const int64_t delay = SLEEP_USECONDS;
const bool repeat = false;
if (OB_SUCC(ret) && OB_ISNULL(conn_res_mgr_.timer_)) {
  ret = OB_ERR_UNEXPECTED;
  LOG_ERROR("connect resource cleanup timer is null", K(ret));
} else if (OB_SUCC(ret) && OB_FAIL(conn_res_mgr_.timer_->schedule(*this, delay, repeat))) {
  LOG_ERROR("schedule connect resource cleanup task failed", K(ret));
}
```

```rust
// 在 runTimerTask(self: Arc<Self>) 里。conn_res_mgr_: &'static ObConnectResourceMgr（ObServer 的成员，即模块）；
// timer_: OnceLock<&'static ObTimer>（C++ 在 init 里把 &timer 记进裸指针，:70）
let delay: i64 = SLEEP_USECONDS as i64;
let repeat = false;
let timer = self.conn_res_mgr_.timer_.get();
if ret.is_ok() && timer.is_none() {
    ret = Err(OB_ERR_UNEXPECTED);
    log_error!(ret, "connect resource cleanup timer is null", k!(ret));
} else if ret.is_ok() && ob_fail!(ret = timer.unwrap().schedule(self.clone(), delay, repeat, false)) {
    log_error!(ret, "schedule connect resource cleanup task failed", k!(ret));
}
```

**例 6：`void*` 任务的线程池**（`src/storage/tx_storage/ob_ls_freeze_thread.cpp:129-160`）

```cpp
if (OB_FAIL(common::ObSimpleThreadPool::push(task))) {}            // 失败时 task 既不入队也不归还
void ObLSFreezeThread::handle(void *task) { … static_cast<ObLSFreezeTask *>(task)->handle(); }
```

```rust
pub struct ObLSFreezeThread { pub base: ObSimpleThreadPool<Box<ObLSFreezeTask>>, /* … */ }
impl ObSimpleThreadPoolBaseVirt<ObLightyQueue<Box<ObLSFreezeTask>>> for ObLSFreezeThread {
    fn base(&self) -> &ObSimpleThreadPool<Box<ObLSFreezeTask>> { &self.base }
    fn handle(&self, task: Box<ObLSFreezeTask>) { task.handle(); }    // C++ 的 NULL 分支：队列项不可能为空，不保留
}
let mut task: Option<Box<ObLSFreezeTask>> = /* 从 task_array_ 取出 */;
if ob_fail!(ret = self.base.push(&mut task)) {}                       // 失败时 task 仍是 Some，离开作用域被释放，与 C++ 一样不回到 task_array_
```

**例 7：用语句截止时间加锁**（`src/rootserver/ob_local_ddl_serial_call.h:38-47`）

```cpp
int ret = local_ddl_serial_mutex().lock(THIS_WORKER.get_timeout_ts());
```

```rust
match local_ddl_serial_mutex().lock_until(this_worker!().get_timeout_ts()) { /* 02 的 Latch：超时 OB_TIMEOUT */ }
```

**例 8：保存、替换、恢复会话和超时**（`src/sql/optimizer/stat/ob_dbms_stats_executor.cpp:103-111`）

```cpp
sql::ObSQLSessionInfo *origin_session = THIS_WORKER.get_session();
int64_t origin_timeout = THIS_WORKER.get_timeout_ts();
THIS_WORKER.set_session(NULL);
THIS_WORKER.set_timeout_ts(MAX_UPDATE_OPT_GATHER_STAT_TIMEOUT + ObTimeUtility::current_time());
…
THIS_WORKER.set_session(origin_session);
THIS_WORKER.set_timeout_ts(origin_timeout);
```

```rust
let origin_session = this_worker!().get_session_any();
let origin_timeout = this_worker!().get_timeout_ts();
this_worker!().set_session(None);
this_worker!().set_timeout_ts(MAX_UPDATE_OPT_GATHER_STAT_TIMEOUT + ObTimeUtility::current_time());
/* … */
this_worker!().set_session(origin_session);
this_worker!().set_timeout_ts(origin_timeout);
```

**例 9：覆盖 `Worker` 的类**（`src/observer/omt/ob_th_worker.cpp:42-66,138-146,208-216,315-341`）

```cpp
worker->set_worker_level(0);                       // create_worker，线程启动前
void ObThWorker::worker(…) { Worker::set_worker_to_thread_local(static_cast<lib::Worker*>(this)); … }
void ObThWorker::run(int64_t idx) { … this->worker(tid, req_recv_timestamp, worker_level); }
int ObThWorker::check_status() { if (nullptr != session_) { session_->is_terminate(ret); } … is_timeout() … }
#define THIS_THWORKER_SAFE dynamic_cast<oceanbase::omt::ObThWorker *>(&THIS_WORKER)
```

```rust
pub struct ObThWorker { pub base: Threads, self_: Weak<ObThWorker>, init_worker_level_: AtomicI32, /* … */ } // Arc::new_cyclic 构造
impl WorkerVirt for ObThWorker {
    fn check_status(&self) -> ObResult {
        let mut ret: ObResult = Ok(());
        if let Some(session) = this_worker!().get_session::<ObSQLSessionInfo>() { session.is_terminate(&mut ret); }
        /* … this_worker!().is_timeout() … */
        ret
    }
    fn as_any(&self) -> &dyn Any { self }
    /* check_wait、can_retry、set_need_retry、unset_need_retry、need_retry、resume */
}
impl Runnable for ObThWorker {
    fn run(&self, idx: i64) { let _ = idx; /* … */ self.worker(&mut tid, &mut req_recv_timestamp, &mut worker_level); }
    fn run1(&self) {}
}
// worker() 开头
WorkerCtx::set_worker_to_thread_local(self.self_.upgrade().map(|w| w as Arc<dyn WorkerVirt>));
this_worker!().set_worker_level(self.init_worker_level_.load(Relaxed));   // create_worker 里的 set_worker_level(0) 改写到这里
// THIS_THWORKER_SAFE
let worker = this_worker!().get_worker_virt();
let worker = worker.as_ref().and_then(|w| w.as_any().downcast_ref::<ObThWorker>());
```

**例 10：`SMART_CALL`**（`src/sql/ob_sql_utils.cpp:1956-1958`、`src/sql/ob_sql.cpp:1237`、`src/oblib/common/json_type/ob_json_base.cpp:2980-2987`）

```cpp
if (OB_FAIL(SMART_CALL(add_expr(expr->get_param_expr(i))))) {}
if (OB_FAIL(SMART_CALL_LARGE(handle_physical_plan(sql, context, result, pc_ctx, get_plan_err)))) {}
if (SMART_CALL(OB_FAIL(parent_info.parent_jb_->seek(allocator, (*sub_path), …)))) {
} else { for (…) { … } }
```

```rust
if ob_fail!(ret = smart_call!(self.add_expr(expr.get_param_expr(i)))) {}
if ob_fail!(ret = smart_call_large!(self.handle_physical_plan(sql, context, result, pc_ctx, get_plan_err))) {}
let inner = smart_call!(parent_info.parent_jb_.seek(allocator, sub_path, /* … */));
if inner.is_err() {
    // BUG(port): C++ 的 OB_FAIL 写在 SMART_CALL 里，赋给的是宏内部的 ret；seek 的错误被吞掉，外层 ret 不变
} else { /* for … */ }
```

**例 11：`check_stack_overflow`**（`src/sql/resolver/dml/ob_select_resolver.cpp:1004-1012`、`src/sql/engine/ob_operator.cpp:283`、`src/sql/ob_sql_utils.cpp:1964-1970`）

```cpp
} else if (OB_FAIL(check_stack_overflow(is_stack_overflow))) {
} else if (OB_UNLIKELY(is_stack_overflow)) { ret = OB_SIZE_OVERFLOW; }
if (OB_FAIL(check_stack_overflow())) {
bool check_stack_overflow_c() { bool is_overflow = false; int ret = OB_SUCCESS;
  if (OB_FAIL(oceanbase::common::check_stack_overflow(is_overflow))) {} return is_overflow; }
```

```rust
} else if ob_fail!(ret = check_stack_overflow(&mut is_stack_overflow, get_reserved_stack_size(), None)) {
} else if is_stack_overflow { ret = Err(OB_SIZE_OVERFLOW); }
if ob_fail!(ret = check_stack_overflow_0()) {
// parser crate 的 extern "C" 蹦床（09）里：
let mut is_overflow = false;
let _ = oblib::check_stack_overflow(&mut is_overflow, oblib::get_reserved_stack_size(), None);
is_overflow
```

**例 12：trace id**（`src/observer/main.cpp:667,750`、`src/oblib/lib/thread/ob_async_task_queue.cpp:133`、`src/oblib/lib/task/ob_timer_service.cpp:120`、`src/pl/ob_pl.cpp:224-226`）

```cpp
ObCurTraceId::SeqGenerator::seq_generator_ = ObTimeUtility::current_time();
ObCurTraceId::get_trace_id()->set("Y0-0000000000000001-0-0");
ObCurTraceId::init(zero_addr);
ObCurTraceId::reset();
if (OB_SUCC(ret) && OB_NOT_NULL(ObCurTraceId::get_trace_id())) {
  trace_id_.set(*ObCurTraceId::get_trace_id());
}
```

```rust
SeqGenerator::set_seq_generator(ObTimeUtility::current_time() as u64);
ObCurTraceId::set_char(b"Y0-0000000000000001-0-0");
ObCurTraceId::init(&zero_addr);
ObCurTraceId::reset();
if ret.is_ok() {                                   // OB_NOT_NULL(get_trace_id()) 恒真（指向线程局部），去掉
    self.trace_id_.set_trace_id(&ObCurTraceId::get_trace_id());   // C++ set(const TraceId &) 重载 → set_trace_id
}
```

**例 13：信号**（`src/observer/main.cpp:676-681`、`src/observer/ob_signal_handle.cpp:56-81,97-117,129-136`）

```cpp
if (OB_FAIL(ObSignalHandle::change_signal_mask())) { MPRINT("change signal mask failed, ret=%d", ret); }
int signals[] = {SIGPIPE, SIGTERM, SIGUSR1, 40, 41, …, 64};
if (0 != (rc = sigaddset(&sig_set, signals[i]))) { ret = OB_ERR_UNEXPECTED; … }
struct timespec timeout = {1, 0};
while (!has_set_stop()) { signum = sigtimedwait(&waitset, NULL, &timeout); … }
case SIGTERM: { raise(SIGKILL); break; }
```

```rust
if ob_fail!(ret = ObSignalHandle::change_signal_mask()) { mprint!("change signal mask failed, ret=%d", /* ret 的错误码，按 04 的写法 */); }
let signals: [i32; 25] = [SIGPIPE, SIGTERM, SIGUSR1, 40, 41, /* … */ 64];
rc = oblib::signal::sigaddset(&mut sig_set, signals[i]);
if 0 != rc { ret = Err(OB_ERR_UNEXPECTED); /* … */ }
let timeout = oblib::signal::timespec { tv_sec: 1, tv_nsec: 0 };
while !self.base.has_set_stop() { signum = oblib::signal::sigtimedwait(&waitset, &timeout); /* … */ }
SIGTERM => { let _ = oblib::signal::raise(SIGKILL); }
```

**例 14：itid 与 PAUSE**（`src/oblib/lib/lock/ob_drw_lock.h:128-131`、`src/oblib/lib/queue/ob_lighty_queue.cpp:120-122`）

```cpp
return latches_[get_itid() % OB_MAX_CPU_NUM].latch_.rdlock(latch_id_);
while(… (timeout = abs_timeout - get_us()) > 0) { PAUSE(); }
```

```rust
self.latches_[(get_itid() % OB_MAX_CPU_NUM) as usize]./* … */;
while /* … */ { oblib::sync::pause(); }     // 02 提供；内部调 oblib::arch::pause()，aarch64 上发 yield
```

**例 15：`GET_TSI`**（`src/rootserver/parallel_ddl/ob_create_view_helper.cpp:479-490`）

```cpp
ObSchemaVersionGenerator *tsi_generator = GET_TSI(TSISchemaVersionGenerator);
if (OB_ISNULL(tsi_generator)) { ret = OB_ERR_UNEXPECTED; }
else if (OB_ISNULL(schema_service_impl)) { ret = OB_ERR_UNEXPECTED; }
else if (OB_UNLIKELY(OB_ISNULL(new_view_schema_))) { ret = OB_ERR_UNEXPECTED; }
else if (OB_FAIL(tsi_generator->get_current_version(last_schema_version))) {
} else if (OB_UNLIKELY(last_schema_version <= 0)) { ret = OB_ERR_UNEXPECTED; }
else if (OB_FAIL(ddl_operator.insert_ori_schema_version(get_trans_(), …, last_schema_version))) {}
```

```rust
// 闭包里只对 TSI 对象本身操作，先把结果拷出来；ddl_operator 可能再取同一个 TSI，不能在闭包里调（§3.10 第 1 条）
let mut tsi_null = false;
let mut tsi_ret: ObResult = Ok(());
get_tsi!(TSISchemaVersionGenerator, |g: Option<&mut TSISchemaVersionGenerator>| match g {
    None => tsi_null = true,
    Some(g) => tsi_ret = g.get_current_version(&mut last_schema_version),
});
if tsi_null { ret = Err(OB_ERR_UNEXPECTED); }
else if schema_service_impl.is_none() { ret = Err(OB_ERR_UNEXPECTED); }
else if self.new_view_schema_.is_none() { ret = Err(OB_ERR_UNEXPECTED); }
else if ob_fail!(ret = tsi_ret) {
} else if last_schema_version <= 0 { ret = Err(OB_ERR_UNEXPECTED); }
else if ob_fail!(ret = ddl_operator.insert_ori_schema_version(self.get_trans_(), /* … */, last_schema_version)) {}
```

`get_current_version` 在 C++ 里排在另外两个空指针检查之后执行；它只读写 TSI 对象自身，提前执行不改变可观察行为。TSI 对象的方法若有别的副作用，按原顺序拆成两次 `get_tsi!`。

## 7. 依赖

**crate**（RULEBOOK §1 白名单）：`psm`（只在 `thread/stack`，版本由 1d 选一个发布满两周的 0.1.x 并写进 `Cargo.lock`）、`libc`（`thread/stack`、`thread/sys`、`signal`；工作区已锁 0.2.190）。不用 `nix`（RULEBOOK 0.8）、`stacker`（RULEBOOK 0.8）、`crossbeam`、`dashmap`。Windows 的少量 Win32 调用（`GetCurrentThreadId`、`SetThreadDescription`、`GetCurrentThreadStackLimits`、`ReadProcessMemory`、`TerminateProcess`、`FlsAlloc`）在白名单路径里手写 `extern "system"` 声明。

**1d 的其他模块**

| 模块 | 本组用到的 |
|---|---|
| 01 `mem` | `ACHUNK_PRESERVE_SIZE`（17 KiB）；`try_box`；扩展栈段走全局分配器。`ObThWorker::check_status` 里的 `CHECK_MEM_STATUS()` 由 01 提供（01 的修订：线程局部装拥有型的 `Arc<ObMemTracker>`，见 `amendments.tsv` 的 01-mem 行），与 `WorkerCtx` 互不依赖。`memory_dump` 的去留由 01 决定（§8 Q14） |
| 02 `sync` | `Latch`、`RwLatch`（`Threads` 的 `lock_`、工作线程列表、定时器监控记录）、`Monitor`（定时服务、`ObReentrantThread` 的条件变量）、`ObLatch::clear_lock`（`update_loop_ts`）、`pause()`（02 实现、调本组的 `arch::pause`）、`lock_until`；`ObCond`（§1.1 #14）、`futex_hook`/`ob_pthread_cond_timedwait_us` 的替代（#13）。反过来，02 的 latch 在锁字里记本组的 `get_itid() + 1`（02 的修订：C++ 记的是 `GETTID()`，02 改记 itid） |
| 04 `error-log-fmt` | `ObError`/`ObResult`/`OB_*`、`ob_fail!` 等、日志宏、`ObBuf`、`ob_printf!`（trace id 文本）、`ObToString`（`ObFatalErrExtraInfoGuardVirt` 的 supertrait）、日志写线程的显式启动入口（§2.15 第 4 步）。反过来，04 的日志行头用本组的 `ob_gettid`、`ob_get_tname_v2`、`ObCurTraceId::get_trace_id_str_0`，`do_log_message` 用 `ObBasicTimeGuard`，`light_backtrace` 用 `arch::{frame_pointer, walk_frame_pointers}` 和 `stack::get_stackattr` |
| 05 `containers` | `ObLightyQueue<T>`、`ObTLinkQueue16<T>`（本组在同一 crate 里为它们实现 `QueueTypeMap`）；定时服务内部用 `Vec` |
| 06 `string-num-sort-serial` | `ob_sort`（`ObBasicTimeGuard::to_string`）、`murmurhash`（`TraceId::hash`）、`ob_serialize_member!`、`ob_bitfield!`、prelude |
| 09 `parser-sys-generators` | `check_stack_overflow_c` 的蹦床；`generated/bindings.rs` 的 `run_static_init_side_effects`、`bind_all` 和 `register_hooks`（Q15）的生成脚本；第 3 步的启动断言（09 §3.9.8） |
| 不在 1d 范围的 translate 单元 | `ObTimeUtility::current_time`、`ObClockGenerator::getClock`、`ObSysTime::now`、`OB_TSC_TIMESTAMP`（`src/oblib/lib/time/`）；`ObAddr`（`src/oblib/lib/net/ob_addr`）。见 §8 Q5、修订 R6 |

## 8. 开放问题与修订

### 8.1 需要人决定的问题

| # | 问题 | 推荐 | 备选 |
|---|---|---|---|
| Q1 | RULEBOOK 0.8/§2.7 的简称（`Timer`、`SimplePool<T>` 等）与 §4"新名字只来自改名表"、README"表外保留 C++ 名"冲突，用哪套名字 | 用 C++ 名（§1.4），C++ 里没有对应物的新名字补进改名表（修订 R1） | 用 RULEBOOK 0.8 的简称，C++ 名只出现在桩文件的 `pub use … as …` 里 |
| Q2 | `ObTimerTask` 的接收者 | `runTimerTask(self: Arc<Self>)`：可分派；服务每次运行前克隆一次 `Arc`（修订 R2） | 保留 `self: &Arc<Self>`，trait 方法加 `where Self: Sized`，再由宏为每个任务生成 `dyn` 适配层（多一层间接，翻译者要多写一行宏） |
| Q3 | 线程相关的系统调用（§2.12）放在哪里 | 白名单加 `rust/port/oblib/src/thread/sys/**`（修订 R3） | 塞进 `thread/stack/**`（偏离该条目的本意）；或与 04 的修订 #6（日志要的 libc）、Q13 合并成一个由本组持有的 `oblib::sys`，API 不变、只改路径；都不行时退到安全 Rust，代价见 §4 末段 |
| Q4 | RULEBOOK 0.5"`check_status` 一类改用启动时注册的钩子"怎么落实 | 上层实现 `WorkerVirt`，在自己的线程开头装进线程局部（§3.8 第 9 条）：覆盖本来就因线程而异，`ObThWorker` 的状态留在对象上，译文最接近 C++ | 进程级的"worker 种类"注册表加每线程种类标签：钩子在启动时注册，但 `ObThWorker` 的 `can_retry_`/`need_retry_`/`query_start_time_` 要搬进 `WorkerCtx` 的线程局部，observer 的译文改动更大 |
| Q5 | 1d 的代码要用 `ObTimeUtility`、`ObClockGenerator`、`ObSysTime`、`OB_TSC_TIMESTAMP`、`ObAddr`，它们是第 3 步才翻译的单元，1d 编译不过 | 把 `src/oblib/lib/time/{ob_time_utility, ob_clock_generator, Time, ob_cur_time, ob_tsc_timestamp}`（约 1,570 行，`wc -l`）列入 1d、归本组（`rewrite:thread`；`ObClockGenerator` 本身就是 `ThreadPool` 子类，`ob_tsc_timestamp` 是 `arch::rdtsc` 的主要用户）；`ObAddr` 列入 06（修订 R6）。04 的修订 #5 提出了同样的建议（time 门面归 03；它点名的 `ObBasicTimeGuard` 本组已列在 §1.1 #35） | 1d 期间用一个临时的 `oblib::thread::clock`（直接调 `clock_gettime`），第 3 步翻译完 time 单元后替换——会有两份实现并存一段时间 |
| Q6 | RULEBOOK 0.8"TimerWK 4–128"与 C++ 实际（下限 0）不符 | 按 C++：调用序列照抄（`init(4, 10000)` 后 `set_thread_count(128)`），效果是下限 0、上限 128（修订 R4） | 按 RULEBOOK 字面常驻 4 个——会多 4 个空闲线程，改变空闲线程数（并发调查 §2.1 的实测是 11 个） |
| Q7 | 零参数或参数是子集的重载怎么命名（`check_stack_overflow()`、`set_thread_name(type)`、`update_loop_ts()`、`get_trace_id_str()`） | 按 §4 的"仍然冲突时加参数个数后缀"：`check_stack_overflow_0`、`set_thread_name_1`、`update_loop_ts_0`、`get_trace_id_str_0`；以 1c 生成的 `overloads.tsv` 为准，若脚本给出不同的名字，本文跟着改 | 为这几个函数在改名表里给可读的名字（如 `check_stack_overflow_ret`）——需要修订 |
| Q8 | 每线程缓存几段扩展栈 | 全部缓存（最多 4 段，RULEBOOK 0.8 的"每线程缓存"），第 6 步按性能带的空闲 RSS 复查 | 每线程只留 1 段，其余归还全局分配器（深递归后的常驻内存更少，第二次深递归多几次分配） |
| Q9 | 帧膨胀系数作用到哪些栈 | 只作用于 `Threads` 建的线程（RULEBOOK A.3 说的是 worker 的公式） | 也作用于 `main` 的 1 MiB 新栈（主线程跑 `ObServer::init/start`，可能执行内部 SQL） |
| Q10 | x86-64 Linux 的 Rust release 默认省略帧指针，04 的 `light_backtrace`（帧指针回溯）会只得到一两层 | 工作区 rustflags 加 `-C force-frame-pointers=yes`，与 C++ 的 `-fno-omit-frame-pointer`（`compile_commands.json` 363 条全有）一致；只影响日志里的回溯 | 不加，`lbt()` 在 x86-64 Linux 上退化（aarch64 macOS 的 ABI 本来就保留帧指针） |
| Q11 | `ObCond`（`ob_queue_thread`）归谁 | 归 02（它是锁原语；`replace:sync`），本组只在 #14 记处置 | 本组在 `oblib::thread` 里用 `Monitor<bool>` 实现 |
| Q12 | `ob_trace_id` 的处置：04 的草稿（`04-error-log-fmt.md` §1.2）记为 `translate`、经启动时注册的钩子读取，依据是 RULEBOOK 0.5"`check_status` 一类的上层逻辑，改用启动时注册的钩子；`ObCurTraceId`、警告缓冲区同理"；本文把"同理"读作"与 `THIS_WORKER` 一样放进 `thread_local!`"（修订 R8） | `replace:thread`（本文 §2.9）：`get_trace_id()` 要按所有权改签名（约 159 处调用，`grep -rhoE "ObCurTraceId::get_trace_id\(" src`；`ObCurTraceId::` 的全部用法约 389 处），必须在第 3 步之前定下；04 与本组在同一个 crate，直接调用，不需要钩子 | 维持 `translate`：第 3 步的译者各自决定 `get_trace_id()` 的形状，其余单元的调用无法统一 |
| Q13 | `main.cpp` 等 observer 单元直接调的其他 libc 函数（`getrlimit`、`getuid`、`stat64`、`chdir`、`dlopen`/`dlsym`、`setlocale`、`mallopt`）没有白名单位置，observer 又是 `forbid(unsafe_code)` | 由人指定一个模块提供安全包装：建议与 04 的修订 #6（`dup2`、`statfs`、`localtime_r`、`strerror_r`、`setlocale`）合并成一个由本组持有的 `oblib::sys`（并入修订 R3 的 `thread/sys`）；`setlocale` 的调用位置见 04 的修订 #12。在此之前本组只提供线程、栈、信号相关的那些 | 能用 std 的改用 std（`std::env::set_current_dir` 等），其余各自写进 -sys |
| Q14 | 01 的修订（`amendments.tsv` 里 01-mem 的"0.8 信号（memory_dump 一句）"）建议删掉 RULEBOOK 0.8 的内存探测：Rust 没有 obmalloc 的 chunk，`ObMemoryDump` 只留门面。那样 `probe_read_memory` 就没有调用者 | 跟随 01：修订通过就不实现 `probe_read_memory`，§2.13、§3.12 第 8 条、§5 第 41 条随之删除；不通过则按本文实现 | 无论如何都实现（约 40 行，留给诊断用） |
| Q15 | `main` 第 3 步的生成函数：09 的文件清单（09 §2.6.4）有 `bind_all`、`run_static_init_side_effects`，没有注册 RULEBOOK 0.4 的 `OnceLock<fn>` 钩子的函数；09 §3.9.8 又要求 `main` 在第 3 步做启动断言、绑定 `PARSER_HOST_SLOT` | 09 在 `generated/bindings.rs`（或单独的 `generated/hooks.rs`）里加 `pub fn register_hooks()`，由 `gen_bindings.py` 按 1c 的 `seams.tsv` 生成，函数体末尾接 09 §3.9.8 的断言和绑定；`seekdb.rs` 只调这一个函数 | `seekdb.rs` 的模板里逐条写出第 3 步的调用（脚本生成），不设 `register_hooks` |

### 8.2 修订（已追加到 `migration/infra/amendments.tsv`）

| # | 章节 | 问题 | 建议 |
|---|---|---|---|
| R1 | §4 改名表；0.8；§2.7 | 0.8/§2.7 的 `Timer`、`TimerService`、`SimplePool<T>`、`ReentrantThread`、`AdaptivePool`、`TimerTask`、`Runnable` 不在改名表里，与"新名字只来自改名表"冲突；本组还需要若干 C++ 没有对应物的新名字 | 0.8/§2.7 的简称注明对应的 C++ 名；改名表 `oblib::thread` 一行补上 `Runnable`、`HostRef`、`CurrentThread`、`NATIVE_STACK_FACTOR`、`ObTraceIdStr`、`ObStackMem`、`with_static_init_phase`、`ObCurTraceId::with_trace_id_mut`，`oblib::signal` 补 `PthreadHandle`、`probe_read_memory`，`oblib::arch` 补 `walk_frame_pointers` |
| R2 | §2.7 定时任务 | `run(self: &Arc<Self>)` 不是可分派的接收者（Rust 只允许 `&Self`、`&mut Self`、`Box/Rc/Arc<Self>`、`Pin<…>`），带它的 trait 不能做成 `dyn` | 改成 `fn runTimerTask(self: Arc<Self>)`，方法名保留 C++ 写法 |
| R3 | §3 白名单 | 线程相关的系统调用（gettid、改当前线程名、读主线程名、macOS QoS、pthread key 析构回调、页大小）都要 unsafe，白名单只有 `thread/stack/**`（栈） | 加 `rust/port/oblib/src/thread/sys/**`，用途限于上述，约 15 处 |
| R4 | 0.8 线程 | "TimerWK 4–128 个"：C++ 是 `init(4, 10000, "TimerWK")` 后 `set_thread_count(128)`（`ob_timer_service.cpp:233-235`），4 只是上限的初值、随即被 128 覆盖，下限是 0（`ob_simple_thread_pool.ipp:63-68,196-199`） | 改为"TimerWK：下限 0、上限 128，按需创建，空闲 1 秒收缩" |
| R5 | §3 lint | §4 要求方法名保留 C++ 写法，含驼峰名（`runTimerTask` 145 处、`ObSysTime::toMicroSeconds` 等）；放开清单没有 `non_snake_case`，trait 定义和固有方法在 `clippy -D warnings` 下编译失败 | 放开清单加 `non_snake_case`（本组先在定义处局部 `allow`） |
| R6 | 0.2 的 1d 范围 | 1d 的代码要用 `ObTimeUtility`、`ObClockGenerator`、`ObSysTime`、`OB_TSC_TIMESTAMP`、`ObAddr`，它们不在 1d 范围，第 3 步才翻译，1d 编译不过 | 把 `src/oblib/lib/time/` 的 5 个单元列入 1d、归 03，`ObAddr` 归 06；或者由人指定别的模块组 |
| R7 | 0.5 模块对象 | "生命周期方法一律取 `&self`"：要把 `self` 交给线程、线程池或定时器的方法拿不到 `'static` | 补一句：这类方法对模块取 `&'static self`，对 `Arc` 持有的对象取 `self: &Arc<Self>`；调用方（组合根）本来就持有 `&'static` |
| R8 | 0.5 线程局部状态 | "`check_status` 一类的上层逻辑，改用启动时注册的钩子；`ObCurTraceId`、警告缓冲区同理"的"同理"可以读成 trace id 也经钩子读取（04 的草稿这样读），也可以读成与 `THIS_WORKER` 一样放进 `thread_local!`（同节第一条把 trace id 列为线程局部里的拥有型数据；v0.1 的原文如此，见审查 soundness S-11）；两种读法决定 `ob_trace_id` 是 1d 的 `replace` 还是第 3 步的 `translate` | 改为"`check_status` 一类改用启动时注册的钩子；`ObCurTraceId` 是 `oblib::thread` 里常量初始化、无 Drop 的线程局部，`get_trace_id()` 返回拷贝，日志直接调用；警告缓冲区见 04 的修订 #3"。见 Q12 |
