# 缺口清单前 20 行：K2 第 2 轮裁定（修订后的 13 行）

> 基线 `7d907abfa`。只复审 `first20.revision.md` 列出的 13 行（第 1、3、5、6、8、9、10、11、12、14、17、18、19 行，按 `first20.tsv` 顺序编号），按倒序从源码重新追踪，逐条核对新增的引用。没有读 K1 的文件。逐行裁定在 `first20.k2.r2.tsv`。

## 结果

| 裁定 | 行数 | 行 |
|---|---|---|
| confirm | 13 | 1、3、5、6、8、9、10、11、12、14、17、18、19 |
| refute | 0 | — |
| evidence-defect | 0 | — |

我第 1 轮驳回或判为证据缺陷的 8 行（第 6、8、9、10、11、12、14、19 行），本轮都已按我给出的问题改正。逐条核对的要点：

- **第 8 行**：拥有型 `MemCtx` 及其 bump arena 已经去掉。api/01 §2.12 的 MemoryContext 成员行规定，跨调用、跨线程的这类成员不再用 MemCtx，改成拥有型；`grep` 也确认没有代码读这个子上下文的 hold。现在只要求 `Send`，理由是 GV$OPEN_CURSOR 持 thread-data 锁读它（`ob_virtual_open_cursor_table.cpp:139`）。
- **第 9 行**：默认划分加了三条排除：init 时写定的字段、自带同步的成员、`after_unlock` 在放锁之后读的字段（经钩子的 Arg 传递）。核对下来已经自洽：`after_unlock` 只读 Arg、init 时写定的字段、自带同步的成员和原子量。
- **第 10 行**：改用 c07 码表的 `GUARDS_FIELDS`，8 个字段都有码。锁外被读的三个字段确实都只占一个字长：`uint8_t` 枚举（`ob_restore_status.h:29,64`）、`SCN` 的 `uint64_t val_`（`scn.h:144-145`）、`int64_t state_`（`ob_ls_state.h:266`），所以按规则 2 用 `ATOMIC_OUTSIDE` 是对的。
- **第 11 行**：每个读点"持 `rwlock_` 读锁"与"不持锁"的分组，我按函数逐个核对过加锁位置，全部一致。
- **第 19 行**：改成 `UNKNOWN`，TODO 问的正是我第 1 轮提出的问题：每个连接的 `ObRequest` 归谁所有。新增的引用也都核对无误：`ob_sql_sock_handler.h:46-48` 写明同一时刻只有一个请求，`ob_sql_sock_session.cpp:103` 在提交时 reuse 内存池。

## 附注（不影响裁定）

- **我第 1 轮的计数有误**：`LinkExecCtxGuard` 的构造点是 26 个，不是 27 个。我多数了 `ob_result_set.cpp:341` 的一行注释。提议者的 26 个逐个与源码吻合。
- **第 3 行**：译法写"顶层和栈上会话的辅助函数传 `None`"。但 `ObSql::execute_pl_sql`（`ob_sql.cpp:1268`）、`ObResultSet::open/get_next_row/do_close`（`ob_result_set.cpp:171,344,754`）等构造点也会被 PL 里的嵌套 SQL 走到，来源表要给它们传调用方的执行上下文，不能一概传 `None`，否则嵌套层级和自动提交的处理会变。
- **第 8 行与已确认的第 7 行**：第 7 行括号里预期 `ObResultSet<'q>`（经 `mem_pool_` 借请求 arena）；第 8 行现在按 §5.3 取拥有型默认，并说明这会把 c04 的 `ObResultSet::mem_pool_` 定为 `REMOVE_ALLOC`。两处要由那条 c04 行（以及第 8 行的 TODO）统一口径。
- **第 19 行**：在 `Arc` 所有者的默认下，`ObRequest` 自己的其他可变字段也要有内部可变性。例如工作线程在 `ob_th_worker.cpp:169` 改 `retry_times_`，而会话的请求槽同时持有一份克隆，`Arc::get_mut` 拿不到。定 TODO 时应一并考虑。
