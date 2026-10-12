# 行为一致性审查：`03-thread-signal.md`

> 来源：行为一致性审查者为 `migration/infra/api/03-thread-signal.md` 派出的子审查（`claude-opus-5-5`，max effort），2026-10-11 完成。父审查者在用量上限处中断，这份结果由编排者原样存档。探针在作业临时目录 `review-fid/03/`，没有入库。

任务：只审 `migration/infra/api/03-thread-signal.md`，看按它写出的代码会不会在 MySQL 客户端或 00b 裁判（含 7 个生命周期场景）能看到的地方与 C++ 不同；抽查了 31 条引用。

结论：文档里的 C++ 事实大体准确。31 条引用里 26 条 CONFIRMED，3 条 PARTIAL，2 条 WRONG。发现共 5 条，其中中 1 条、低 4 条，都不在 `amendments.tsv` 里。R4（TimerWK 下限 0）和 R2（`runTimerTask(self: Arc<Self>)`）经核对是对的。

## 发现

**B1【中】栈的字节常数不随帧膨胀系数缩放，扩展段又没有保护页**
- **文档位置**：§2.4（行 240-245，`NATIVE_STACK_FACTOR` 只乘原生栈）；§2.11（行 840-847，常数写死）；§3.11 第 6、7 条（行 1129-1130，"段同样不加保护页"）；§8 Q9。
- **C++ 的事实**：
  - 各余量是按 C++ 的栈帧大小标定的：`check_stack_overflow` 默认 32 KiB（`ob_common_utility.cpp:35`），`SMART_CALL` 64 KiB、`SMART_CALL_LARGE` 192 KiB，每段 2 MiB−34 KiB，累计上限 10 MiB（`ob_smart_call.h:28-36`）。`:31-35` 的注释明说 (256−64) KiB 是"那种布局上大致可用的跨度"。
  - 扩展段不加保护页：`smart_call_alloc` 传 `guard_page=false`（`protected_stack_allocator.cpp:69-72,95-135`）。
- **按文档写出来的行为**：RULEBOOK A.3 本身就假定 Rust 栈帧比 C++ 大 k 倍（k>1），但只放大原生栈。
  - 两个检查点之间的实际用量可能超过 32/64 KiB 余量。在原生栈上会撞保护页崩溃；在 psm 扩展段上会越界写到相邻的堆块，静默损坏。这比 C++ 更容易发生，因为余量是给 C++ 栈帧定的。
  - 靠扩展段能到达的最大深度约为 4×(2 MiB−34 KiB)/帧大小，缩到 C++ 的约 1/k。深嵌套 SQL 会提前报 4019，解析器回调会报 `OB_PARSER_ERR_SIZE_OVERFLOW`。A.3 的退路"在递归点加 `smart_call!`"抬不高 10 MiB 的累计上限。
- **在哪里看得到**：崩溃会让用例余下的语句全部失败，风险最大的是 A.3 点名的 3 个用例（`cases/join_many_table`、`sqlancer_optimizer_regressions.test:19`、`subquery.optimizer_subquery_bug.test:297`）。用户在深嵌套查询上会看到 4019。
- **建议改法**：同一个系数 F 同时乘到全部字节常数上，即 32K/64K/192K 余量、每段大小、累计上限和原生栈；这样段数仍是 4，可达深度与 C++ 一致，日志里打印 C++ 的值。缓存的扩展段在第一次分配时 `mprotect` 一页保护页（之后复用，不违反"禁止每次扩栈都 mmap/munmap"）。第 5 步用"栈涂色"量两个检查点之间的最高水位。需要修订 RULEBOOK 0.8 和 A.3。

**B2【低】"SIGTERM → 退出状态 137，生命周期场景会测到"不成立，第 43 条测试在现有裁判上做不出来**
- **文档位置**：行 1140、行 1256（第 43 条测试）；RULEBOOK 0.8 有同样的说法。
- **裁判的事实**：
  - `lifecycle.py:91-92` 的 `stop` 调 `sdb.py stop`。`terminate_pid`（`sdb.py:370-403`）先发 SIGTERM，等 `STOP_TIMEOUT=20` 秒（`:21`），没退出就发 SIGKILL。
  - 退出状态拿不到：实例不是 `sdb stop` 的子进程，`process_exists` 只判断进程在不在。
- **后果**：Rust 版即使忽略 SIGTERM 或者做优雅停机，也会在 20 秒后被 SIGKILL 杀掉，裁判照样通过。A.4 裁判改动第 5 项（SIGUSR1 探针）的"以状态 0 退出"同样观察不到。用户看得到差别：`kill <pid>` 是立即死亡、优雅停机还是没反应。
- **建议改法**：生命周期场景里直接发 SIGTERM，断言约 2 秒内 pid 消失（远小于 20 秒）。要看退出状态，就让实例在一个会 `waitpid` 并把状态写进文件的小包装程序下启动，记进 A.4 的裁判改动清单。同时删掉或改正 RULEBOOK 0.8 的"会测到"。

**B3【低】trace id 的文本解析（`set_char`/`parse_from_buf`）是用户可见面，规格和测试都漏了 sscanf 的边界**
- **文档位置**：§2.9（行 701-702、710）、§3.9 第 4 条（行 1110）、第 28 条测试（行 1235）。
- **C++ 的事实**：`ob_trace_id.h:158-176` 用格式 `"Y%llX-%016llX-%llx-%llx"`（`:30-31`）做 `sscanf`。用户能碰到的入口有：
  - `DECODE_TRACE_ID(str)`（`ob_expr_decode_trace_id.h:41-51`），传的是 `trace_id_str.ptr()`，没有 NUL 结尾；
  - `ALTER SYSTEM CANCEL TASK`（`ob_alter_system_executor.cpp:418-440`），先解析，再 `to_string`，再与输入做 `case_compare`；
  - DDL 任务把 trace id 以字符串存进内部表，重启后用 `parse_from_buf(trace_id_str.ptr())` 读回（`ob_ddl_task.cpp:3143`），这属于 0.10 的数据兼容面；
  - `ob_vector_index_async_task_util.cpp:722` 同理。
- **基线工具链实测**（clang 17 + MacOSX15.sdk）：
  - `"Y"` 返回 −1（EOF），C++ 因此报 `OB_INVALID_ARGUMENT`；`"Yz"` 返回 0，算成功，trace id 全零，`DECODE_TRACE_ID` 输出 `0.0.0.0:0`；
  - `"Y-1"` 返回 1，第一个值变成 `FFFFFFFFFFFFFFFF`；
  - `"Y 0x1A-…"` 返回 4：前导空白、`0x` 前缀、`+` 号都接受；
  - 第二段宽度上限 16：`"Y1A-00000000000000001234-0-0"` 返回 2；
  - 上溢饱和到 `ULLONG_MAX`；尾部垃圾被忽略。
- **按文档写出来的行为**：文档只写了"匹配项数既不是 0 也不是 4 时报错"。手写的 `&[u8]` 解析器很可能把 EOF 当成 0，或者拒绝空白、符号、`0x`，或者不遵守宽度 16；那样 `DECODE_TRACE_ID` 和 `CANCEL TASK` 的结果就会不同。另外，C++ 会越过 ObString 末尾一直读到 NUL（读到相邻列的十六进制字节时，最后一段的值会被改掉），这是未定义行为，Rust 读到切片末尾为止，文档没有标注。
- **建议改法**：规格写成"本格式的 vsscanf 语义：EOF 返回 −1；跳过空白；可选符号和 `0x`；第二段宽度 16；按 strtoull 饱和；`%X` 截成 u32；遇到 NUL 或切片末尾即停"，并标 `FIXED(port): 越过 ObString 末尾的读`。由 C++ 产出金标准向量，覆盖上面这组输入，外加"IPv6 之后再 `init` IPv4 时遗留的 `ipv6_` 字段"。

**B4【低】ObDITls"构造期间递归取得占位指针 0x1"与源码不符**
- **文档位置**：§3.10 第 1 条（行 1117）、§3.16。
- **C++ 的事实**：`ob_di_tls.h:101-113` 中，占位值让 `is_valid()` 为假，所以递归调用会再次进入 `!is_valid()` 分支，嵌套 `OB_NEW` 出第二个 T 并返回它。外层构造完成后覆盖 `instance_`，内层那个泄漏；若每次构造都递归，就无限递归。0x1 不会被返回，这里也没有未定义行为。
- **后果**：Rust 的"返回 None 并记 ERROR"是没有登记的行为改动，不符合 `FIXED(port)` 的条件。目前没有已知的、构造函数会递归取自身的 T，所以用户不可见。
- **建议改法**：改正描述，然后二选一：照 C++ 复刻（嵌套调用新建并返回一个实例，外层覆盖），或者标 `DIVERGE` 并进 A.4。

**B5【低】Rust std 在 `main` 之前做的事，会造成 A.4 之外的偏差**
- **文档位置**：§3.12 第 3 条（行 1138），原文说"前者与 C++ 等价，后者不在裁判范围内"。
- **实测**（rustc 1.98.1，macOS arm64）：
  - 普通 `fn main`：SIGPIPE 是 `SIG_IGN`；SIGSEGV、SIGBUS 装了 std 的处理函数；主线程和每个 spawn 出的线程都有 128 KiB 的 `sigaltstack`。
  - `#![no_main]` 加 C 的 `main`：三个信号都是默认处置，没有 altstack，与 C++ 一样。
- **C++ 的事实**：SIGPIPE 只是被屏蔽、处置仍是默认，由信号线程记 INFO "received signal"（`ob_signal_handle.cpp:124`）；`install_ob_signal_handler` 是死代码，所以真的栈溢出时是 SIGSEGV，退出状态 139，并生成 core。
- **按文档写出来的行为**：撞到保护页时打印 "has overflowed its stack" 后 abort，退出状态 134；SIGPIPE 的那行 INFO 日志消失。只在崩溃时用户可见，裁判看不到。
- **建议改法**：生成的 `seekdb.rs` 用 `#![no_main]` 加 `#[no_mangle] extern "C" fn main(argc, argv)`。argv 在 macOS 上可以用 `_NSGetArgv` 取，在 Linux glibc 上 std 也能取到，这一点 1d 实测确认。如果不采用，就在 A.4 新增一项 `crash-mode`。

## 引用抽查

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| 1026 | 线程数上限 4096−128，超限返回 `OB_SIZE_OVERFLOW`；创建失败返回 `OB_ERR_SYS` | thread.cpp:73-80,142-145；ob_define.h:1481；thread.cpp:498-501 | CONFIRMED | |
| 1027 | macOS 上 `setstacksize` 失败就静默退回 512 KiB；228,352 不是页的倍数 | thread.cpp:101-114；threads.cpp:29；threads.h:42 | CONFIRMED | 实测：EINVAL，默认 524,288 |
| 1028 | 子线程依次设 QoS、登记、写 `create_ret_` | thread.cpp:96,341-349,383 | PARTIAL | macOS 在登记之前（:346）就写了 `create_ret_`；`:96` 还在 attr 上设了 QoS，文档没提；都不可见 |
| 1059 | `push` 把 `OB_SIZE_OVERFLOW` 改成 `OB_EAGAIN`，空闲线程为 0 就扩容 | ob_simple_thread_pool.ipp:108-128；ob_lighty_queue.cpp:103 | CONFIRMED | |
| 1060 | 工作线程空闲 1 s 收缩，停止后 `handle_drop` | .ipp:242-293；.h:54-55 | CONFIRMED | 实际约 2 s 才退出 |
| 1062 | qth_mgr 每 3 s 回收一次，在 ob_server.cpp:702 初始化、:1635 停止 | ob_dynamic_thread_pool.cpp:404-417；.h:128 | CONFIRMED | `bind` 不要求 mgr 已初始化 |
| 1063 | `try_expand_one` 失败时睡 1 ms 重试；`try_shrink_one` 缩到 0 而队列非空时加回 | ob_adaptive_worker_pool.h:69-111 | CONFIRMED | |
| 1068-1072 | worker 的上下限公式、10 s 保活、3 s 救援扩容 | ob_server_runtime.cpp:570-580,651-661,678-701；ob_server_runtime.h:184 | CONFIRMED | |
| 1073 | TimerWK 先 `init(4,10000)` 再 `set_thread_count(128)`，下限 0 | ob_timer_service.cpp:233-235；.ipp:63-68,196-199 | CONFIRMED | 修订 R4 正确 |
| 1080 | `schedule_task`：delay<0 报 `OB_INVALID_ARGUMENT`；`now` 或 `now+delay`；周期 `repeate?delay:0` | ob_timer_service.cpp:312-346 | CONFIRMED | 不去重，没有 `OB_ENTRY_EXIST` |
| 1081 | 等待时间夹在 [10,100] ms；全是占位时每 100 ms 醒一次 | :566-618；.h:192-193 | CONFIRMED | |
| 1083 | 告警阈值：优先队列 1 s、线程池 500 ms、耗时 10 min | :142-171；.h:117-118,202 | CONFIRMED | 只进日志 |
| 1084 | 周期任务是"上次结束后再过 delay" | :431-486（:477） | CONFIRMED | |
| 1085-1086 | cancel 时与末尾交换后删尾、不 notify；wait 每 10 ms 轮询 | :348-429 | CONFIRMED | |
| 1089 | `ObTimer::schedule`：未初始化 `OB_NOT_INIT`，已停止 `OB_CANCELED`；初始化后设 run wrapper 报 `OB_OP_NOT_ALLOW` | ob_timer.cpp:30-53,108-146 | CONFIRMED | |
| 1090 | 监控超时阈值 clamp(interval×100, 1 min, 15 min)，每 6 次打印一次 | ob_timer_monitor.cpp:171-224 | CONFIRMED | 只进日志 |
| 1098 | 基类 `check_status` = `check_wait()` 后返回成功 | worker.h:45；worker.cpp:73-93 | CONFIRMED | `common_yield` 和 `alloc_worker` 都没被覆盖 |
| 1100 | `is_timeout = getClock() >= timeout_ts_` | worker.cpp:96-104 | CONFIRMED | |
| 1096 | `set_worker_to_thread_local` 全库 4 处 | threads.cpp:244；ob_th_worker.cpp:212；ob_server_runtime.cpp:230（ObPxWorker）；main.cpp:795 | CONFIRMED | |
| 1110 | trace id 格式 V4/V6 | ob_trace_id.h:30-31,123-138；ob_expr_last_trace_id.cpp:67 | CONFIRMED | `LAST_TRACE_ID()` 和 PROCESSLIST 可见 |
| 1110 | `set_char` 的语义 | ob_trace_id.h:158-176 | PARTIAL | 见 B3 |
| 1109 | `gen_seq` 的分批规则 | ob_trace_id.h:38-44；main.cpp:667 | CONFIRMED | |
| 1117 | ObDITls 递归时返回 0x1 | ob_di_tls.h:101-113 | WRONG | 见 B4 |
| 1124-1127 | 栈常数、`check_stack_overflow`、`alloc_stack` 的口径（不记日志） | ob_smart_call.h:28-36,72-128；ob_common_utility.cpp:35-83 | CONFIRMED | |
| 1128 | macOS 原生栈 229,376，最多扩 4 段 | 实测 `pthread_get_stacksize_np` | PARTIAL | 实际报告 241,664；4 段的结论不变，第 34 条测试应改用报告值 |
| 1130 | 原生栈公式与 macOS 按页取整 | ob_server.cpp:1990-1998（在 `init_pre_setting` 里）；parameters.yaml:1354（默认 256K） | CONFIRMED | |
| 1136-1137 | 屏蔽集合；macOS 的 `sigaddset` 宏 | ob_signal_handle.cpp:97-117；MacOSX15.sdk signal.h:117-122 | CONFIRMED | 实测：宏返回 0、不置位；函数返回 −1 |
| 1139-1140 | macOS 用 `sigwait` 模拟、忽略超时；各信号的动作；SIGUSR1 睡 5 s 后每 3 s 检查再 `_Exit(0)` | ob_signal_handle.cpp:25-37,56-81,120-217；ob_server.cpp:1493-1517,1682-1697 | CONFIRMED | |
| 1140 | "生命周期场景会测到" 137 | sdb.py:370-403；lifecycle.py:91-92 | WRONG | 见 B2 |
| 1018-1019 | `ob_gettid` 缓存；`set_thread_name` 的截断 | ob_define.h:1905-1934；ob_thread_name.cpp:51-69 | CONFIRMED | 线程名只出现在日志和内部表 `__all_virtual_mds_event_history` |
| 1051-1052 | `blocking_run` 等 3000 ms；`logical_*`、`destroy` | ob_reentrant_thread.cpp:41-121,158-189 | CONFIRMED | |
| 1010 | itid 分配：CAS 失败从槽 0 重扫，全满 `ob_abort` | ob_tsi_utils.cpp:74-101 | CONFIRMED | |
| 1038 | weak 的 dispatch 没被覆盖，两份函数体都是直接调 `fn()` | ob_timer_service.cpp:23-30；share/rc/ob_server_runtime.cpp:31-37；ob_server.cpp:1384 | CONFIRMED | |

## 无金标准的"一致"说法

1. **行 1140、第 43 条测试**：SIGTERM 后退出状态 137、SIGUSR1 后退出状态 0。现有裁判观察不到（B2）。
2. **行 1110**：`set_char`"sscanf 语义"。第 28 条金标准测试只列了合法、部分匹配、未知前缀三类，缺 EOF、空白/符号/`0x`、宽度 16、饱和、遇 NUL 截断（B3）。
3. **行 1138**："SIGPIPE 设成 SIG_IGN 与 C++ 等价"。没有测试，实测处置不同（B5）。
4. **第 31 条测试**：ObBasicTimeGuard 金标准"C++ 程序用固定时间源"做不到。`get_cur_ts()` 是 `ob_common_utility.h:62` 里的 inline 函数，直接读 TSC，没有注入点；只影响日志，可以降成单元测试。
5. **第 34 条测试**："原生栈 224 KiB 时恰好扩 4 段"。macOS 上应使用 `pthread_get_stacksize_np` 报告的 241,664；深度也没有与 C++ 的差分（B1）。
6. **行 1027**："在那之前构造的 Threads 在 macOS 上拿到 512 KiB"。Rust 要相同，每个持有 Threads 的对象都必须在相对 `init_pre_setting`（ob_server.cpp:1939 起）相同的时刻构造。没有测试核对；可以把各池构造时取到的栈大小与基线日志里的 "successfully set stack size"（thread.cpp:111-113，只有页倍数的才记）比对。

## A.4 之外的偏差

1. ObDITls 递归时改成返回 None：既不是 `FIXED`，也不在 A.4（B4）。
2. Rust 运行时的信号处置：崩溃方式变成 SIGABRT/134（C++ 是 SIGSEGV/139 加 core）；SIGPIPE 的 INFO 日志消失；每个线程多一个 128 KiB 的 altstack（B5）。建议用 `#![no_main]` 消除，而不是新增 A.4 项。
3. 只影响日志的差别：任务类型名（`type_name` 对 Itanium 修饰名）、ObBasicTimeGuard 最多 8 层、去掉 `Thread::stop` 里按 glibc 私有偏移读 tid。文档说"不需要标记"，但按 infra README 的规则必须有出处，取决于 04 的修订 #8（`log-content`）是否获批，文档应注明这个依赖。
4. QoS 只在线程内设置（C++ 在 attr 上也设，thread.cpp:96）；macOS 上 `start()` 返回的时序变了。只影响调度，不需要 A.4。
5. 线程栈和扩展段不再经 `ob_malloc` 的 "Coro"/"CoStack" 标签，A.4 `mem-view` 已覆盖；Linux 上 `Builder` 把 228,352 向上取整到 229,376、TLS 的位置也不同，由 A.4 `stack-depth` 在第 5 步之后覆盖。
6. 文档没有提议新增 A.4 项。我的判断：B5 采用 `no_main`、B4 照 C++ 复刻之后，不需要新增；否则只需一项 `crash-mode`，裁判看不到。
