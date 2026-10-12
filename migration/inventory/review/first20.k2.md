# 缺口清单前 20 行：怀疑型审查者 K2 的裁定说明（阶段 1c，kit prompt 02）

> 基线 `7d907abfa`。逐行裁定在 `first20.k2.tsv`；本文写驳回行和证据缺陷行的数据流追踪与改正建议，以及审查中遇到的 RULEBOOK/API 问题。这些问题只建议排队，K2 没有改动任何分片、RULEBOOK 或修订表。
> 做法：按 `first20.tsv` 倒序（第 20 行先）逐行从源码重新追踪创建、修改、逃逸、所有权和线程；没有读 K1 的文件。
> 本文的"第 N 行"按 `first20.tsv` 的顺序编号，与提议者说明里"总表"的编号不同。

## 计数

| 裁定 | 行数 | 行（`first20.tsv` 顺序） |
|---|---|---|
| confirm | 12 | 1、2、3、4、5、7、13、15、16、17、18、20 |
| refute | 6 | 8、9、10、11、12、19 |
| evidence-defect | 2 | 6、14 |

驳回的 6 行里，第 10、11、12 行的数据流和译法都对，只是分类码不在类别的码表里；第 8、9、19 行的译法本身有错。

## 1. 驳回

### 第 19 行：c19 `ObLockWaitNode`（`src/oblib/rpc/ob_lock_wait_node.h:105`）

**追踪**

- 节点按值嵌在 `rpc::ObRequest` 里（`ob_request.h:124`），这一点提议者说得对。
- 但 MySQL 请求本身不是独立分配的对象：
  - 它是连接对象 `ObSqlSockSession` 的按值成员 `rpc::ObRequest sql_req_`（`ob_sql_sock_session.h:64`）；
  - 每收到一个包，`build_sql_req` 都在原地 placement new 重建它（`ob_sql_sock_handler.cpp:82-83`）；
  - packet 视图分配在会话的内存池里（`:135`，`build_mysql_raw_packet_view(sess->pool_, …)`），请求还经 `handle_ctx_` 回指会话（`:84`）；
  - 会话对象的内存是 Rust nio reactor 按连接分配的 `udata`（`ob_sql_nio_server.cpp:137` 把 `sizeof(ObSqlSockSession)` 交给 `nio_start`，`ob_sql_sock_handler.cpp:58` placement new）。
- `repost` 只投递 OB_MYSQL 请求（`ob_srv_deliver.cpp:143-153`），它用 `CONTAINER_OF` 找回的就是这个嵌在会话里的 `sql_req_`（`:128`）。
- 所以"任一时刻只在一个容器里：队列、工作线程、`hash_`、retire 链"漏掉了始终持有存储的 `ObSqlSockSession`。译法里 `worker queue -> worker (Box<ObRequest>)` 的 `Box` 没有来源；api/05 的 `SpHashNode: Send + 'static`（`05-containers.md:1264`）还要求 `ObRequest` 不借用会话内存池（`pkt_`）和会话本身（`handle_ctx_`）。
- 路径还漏了一条：`transform_row_lock_to_tx_lock`（`ob_lock_wait_mgr.cpp:669-694`）。
  - 它用 `fetch_waiter` 取出节点（`:680`），改 hash（`:683`），再经 `wait()` 插回（`:685`）；
  - 插回成功后还执行 `node->try_lock_times_--`（`:689`）。这时节点已在表里，可能已被别的线程取走并重新投递，C++ 里是数据竞争；Rust 里节点已交给表，这一句写不出来。
- `wait()` 自己在插入之后也继续写节点：`:312` `set_standalone_task(true)`、`:317` del、`:323` `try_lock_times_--`。Rust 里这些都要用 `insert` 返回的 key 调 `with_node`；提议者只写了 `next()`（`:358-374`）用 `with_node`。
- 分类码 `SINGLE_OWNER_BOX_MOVE` 不在 c19 A 类码表里（scripts README `README.md:900-903`）。

**改正建议**

- 分类：在 c19 码表补上"Box 在单一容器之间移动"之前写 `UNKNOWN`，译法仍按 RULEBOOK §2.7"队列项"的 Box 移动写，并标 `TODO(port)`；或者先排修订新增这个码（第 4 节问题 2）。
- 译法补三点：
  1. `ObSqlSockSession::sql_req_` 改成拥有型槽位（如 `Option<Box<rpc::ObRequest>>`）：`build_sql_req` 取出或新建，请求结束后归还或丢弃。这是本行的前提，连同 `ObRequest::pkt_`、`handle_ctx_` 改成拥有型或 `Arc`，一起写成 `depends-on`。
  2. 路径加上 `transform_row_lock_to_tx_lock`：`try_lock_times_--` 挪到插回之前，或并进 `wait()`，标 `FIXED(port)`。
  3. `wait()` 插入之后的写一律经 `hash_.with_node(&key, ..)`。
- 证据小误：`comment ob_lock_wait_mgr.cpp:249-250` 应为 `:245-246`。

### 第 12 行：c08 `observer::ObService::stopped_`（`src/observer/ob_service.h:171`）

- 追踪与提议者相同：`volatile bool`（`h:171`），构造时置 false（`ob_service.cpp:156`），`stop()` 里置 true（`:243`），唯一的读者是同一函数里的日志（`:253`）；`ObService` 是模块（types.tsv：shared yes）。译法 `AtomicBool` 加 Relaxed 是对的。
- 驳回只因为分类码：
  - c08 的码表在 scripts README `README.md:1309-1318`，其中没有 `ATOMIC_RELAXED`；
  - 停止标志的码是 `RELAXED_STOP_FLAG`（`:1311`）；
  - `FIXED_RACE` 不适用：没有并发读者，C++ 里不存在竞争。
- 改正：classification 改为 `RELAXED_STOP_FLAG`，其余不变。

### 第 11 行：c08 `memtable::ObTransCallbackMgr::callback_lists_`（`src/storage/memtable/mvcc/ob_mvcc_trans_ctx.h:377`）

- 追踪：
  - 构造时为 NULL（`h:206`）；
  - `extend_callback_lists_` 在 `rwlock_` 写锁下双重检查、分配、逐个 placement new，再 `ATOMIC_STORE` 发布（`ob_mvcc_trans_ctx.cpp:318-338`）；
  - `reset` 释放并置空（`:219-221`），只经 `ObMemtableCtx::reset`（`ob_memtable_context.cpp:145`）到达，调用方是析构、`ObTxCtx::destroy`（工厂 release 时，`ob_trans_factory.cpp:113-121`）和未发布对象上的 `default_init_`，都是独占路径。
  - 所以 `OnceLock<Box<[ObTxCallbackList]>>` 加 `take()` 的译法对。
- 驳回的理由：`PTR_ONCE_LOCK` 不是 c08 的码，README 里对应的码是 `ONCELOCK`（`README.md:1313`）。
- 证据还说过头了一处：它说 19 处普通读都不加锁，全部标 `FIXED(port)`。实际上不少读在 `rwlock_` 的读锁里，与持写锁的发布互斥，不构成竞争：
  - `remove_callbacks_for_fast_commit`：`RDLockGuard` 在 `:616`，读在 `:632`；
  - `:938` 加锁、`:940` 读；`calc_checksum_all`：`:1413` 加锁、`:1415` 读；
  - `:1429`、`:1439`、`:2124`、`:2141` 加读锁之后的 `CALLBACK_LISTS_FOREACH`。
  - `FIXED(port)` 只该标在不持锁的读上，如 `:405/:408`、`:496/:499`、`:563-564`、`:2068-2070`、`:2097`、`h:323`。
- 改正：classification 改为 `ONCELOCK`；证据把持 `rwlock_` 读锁的读与不持锁的读分开列，`FIXED(port)` 只标后者。`get()` 的 Acquire 对两类读都适用，译法不变。

### 第 10 行：c07 `storage::ObLSMeta::rw_lock_`（`src/storage/ls/ob_ls_meta.h:123`）

- 追踪核对无误：
  - 8 个序列化字段都在 `update_lock_` 写锁和 `rw_lock_` 写锁下修改。其中 `all_id_meta_` 经 `update_all_id_meta`/`update_id_meta` 修改（`:309-312`、`:401-403`），扫描脚本的 writes-under-lock 漏了它，提议者补上了；
  - `init` 作用于未发布的对象；
  - 拷贝构造只在持 `update_lock_` 的写者里用；
  - 不加锁的读者在 `:88-91`、`:163-166`、`:194-197`、`:433-444`，有跨线程的调用者；
  - `ObReentrant*Guard` 的重入分支（`:454`、`:491`）对 `rw_lock_` 从不触发：`rw_lock_` 的区域里只有赋值和日志，类外也没人直接用 `rw_lock_`。
- 驳回只因为分类码：
  - `DATA_IN_LOCK` 不是 c07 的码；
  - c07 码表（`README.md:1274-1281`）规定逐字段列码的行用 `GUARDS_FIELDS`，并且"target_translation lists every protected field with a per-field code"；本行没有逐字段的码。
- 改正：
  - classification 改为 `GUARDS_FIELDS`；
  - 字段码：`clog_checkpoint_scn_`、`clog_base_lsn_`、`replayable_point_`、`all_id_meta_`、`saved_info_` 为 `IN_LOCK`；
  - `ls_persistent_state_`、`restore_status_`、`tablet_change_checkpoint_scn_` 在锁外被读，按规则 2 写 `ARCSWAP_SNAPSHOT`，注明"readers lock"，也就是提议者的读锁方案。如果把 `SCN` 和两个枚举包装类当作标量，就应写 `ATOMIC_OUTSIDE`，这是规则 2 的判断，请两名审查者统一；
  - `all_id_meta_` 自带一把 `ObSpinLock`（`ob_id_service.cpp:600-607`），放进 `T` 之后是锁中锁，译法要写明加锁顺序 `rw_lock_` → `ObAllIDMeta::lock_`。

### 第 9 行：c07 `transaction::ObTransCtx::lock_`（`src/storage/tx/ob_trans_ctx.h:222`）

- 追踪：CtxLock 的结构、模式与加解锁顺序、跨函数持锁、读锁路径、重入点，提议者的引用我逐条核对，都对。
- 问题出在 UNKNOWN 的默认划分："init 之后还会写的非标量字段一律放进 `ctx_lock_` 的 `TransCtxLocked`"，同时 `after_unlock` 照搬 `:62-111`。
  - `after_unlock` 在 `ctx_lock_.unlock()`（`ob_trans_ctx_lock.cpp:166`）之后运行；api/02 的 `UnlockHooks::after_unlock(&self, arg)` 只拿得到宿主和 Arg。
  - 它读 `trans_need_wait_wrap_`（`ob_trans_ctx.cpp:73`、`:89-90`、`:103-104`，经 `ob_trans_ctx.h:170-173`）。这是一个两字段的值结构（`ob_trans_define.h:528-549`），在锁内经 `get_gts_` 写入（`ob_tx_ctx.cpp:2111`）。按默认划分它进了 `TransCtxLocked`，`after_unlock` 就读不到了。
  - 它经 `arg.p_mt_ctx_` 调 `elr_trans_preparing()`（`:66`）；`p_mt_ctx_` 指向本对象的 `mt_ctx_`（`ob_tx_ctx.cpp:143` `elr_handler_.set_memtable_ctx(&mt_ctx_)`）。
  - `mt_ctx_`（`ob_tx_ctx.h:598`）是自带锁的成员：`get_memtable_ctx()` 把它交给 DML 路径（`ob_tx_ctx.h:143`），`start_access` 只持 ACCESS 锁时就调用它（`ob_tx_ctx.cpp:5171`、`:5199`）。它的保护并不是"不明"，§5.3 的默认不该套到它身上；放进 `ctx_lock_` 的 `T`，所有 DML 路径都得取 CTX 锁。`trace_log_`（自带锁的 recorder，`ob_trans_define.h:489-494`）同理。
- 改正：保留 `UNKNOWN` 和 `TODO(port)`，但：
  1. 默认划分排除自带同步的成员（`mt_ctx_`、`trace_log_` 等），它们的锁由各自的 c07 行决定；
  2. `after_unlock` 放锁之后要读的锁内数据，由 `before_unlock` 拷进 Arg（`trans_need_wait_wrap_` 的两个值），`after_unlock` 只读 Arg 和锁外字段；`opid_`（`REC_TRANS_TRACE_EXT2` 在 `after_unlock` 里读它）按默认是原子量，没有问题；
  3. 交给人的问题加上这两类字段。
  - 字段划分定下来以后，本行按 README 的 c07 码表应改为 `HOOKED`（CtxLock）。

### 第 8 行：c05 `ObSPIResultSet`（`src/sql/ob_spi.h:161`）

- 追踪核对：
  - `init` 在 `CURRENT_CONTEXT` 下建子 `mem_context_`（`ob_spi.cpp:125`），再把 `ObResultSet` placement new 进 `buf_`（`:127-129`）；
  - `allocator_` 存 `StmtSavedValue`（`:228-239`）和 SQL 副本（`:2732-2733`）；
  - 流式游标跨 `spi_cursor_open`/`fetch`/`close` 存活（`ob_pl_type.cpp:1578-1603`）；
  - GV$OPEN_CURSOR 在别的线程读 `sql_id_`。
  - 分类 `OWNED_CHUNKS` 有 RULEBOOK 0.7(b) 点名，两个 TODO 也对。
- 驳回的理由：译法写"the type must be Send (+Sync for the GV$OPEN_CURSOR read)"。
  1. 译法里的 `mem_context_` 是拥有型 `MemCtx`，api/01 明确 `MemCtx` 是 `Send，!Sync`（`01-mem.md:352`；`Arena` 同样是 `!Sync`，`:175`），所以含它的类型不可能是 `Sync`；
  2. 也不需要 `Sync`：GV$OPEN_CURSOR 在持会话 thread-data 锁时遍历游标表（`ob_virtual_open_cursor_table.cpp:139`），这把锁翻成 `Latch` 之后，`Latch<T>: Sync` 只要求 `T: Send`（`02-sync.md:194`）。
- 改正：保留 `OWNED_CHUNKS` 和两个 TODO，要求改成"Send"。还要注明，TODO(2) 解决之前连 `Send` 也做不到：借用 `mem_context_` 里 Arena 的 `ObResultSet<'q>` 持有 `&Arena`，而 `Arena` 是 `!Sync`。
- 证据小误：thread-data 锁应引 `ob_virtual_open_cursor_table.cpp:139`；`ob_sql_session_info.cpp:257-261` 是会话 reset 里取的同一把锁。

## 2. 证据缺陷

### 第 14 行：c11 `ObTransformUtils::check_exprs_unique_on_table_items` 的局部 `ObRawExprFactory`（`ob_transform_utils.cpp:3606`）

- 结论 `SCOPE_STMT_QA` 成立：作用域里新建的节点（`get_equal_set_conditions` 建的等值式、`ObRawExprCopier` 拷出的表达式）只流向局部的 `res_info`、等价集和 fd 项，离开函数的只有 `is_unique`。
- 但证据的前提"外层输入只读（const stmt，各 compute_*_property reads only）"不成立：
  - `get_equal_set_conditions` 调 `create_equal_expr`（`ob_transform_utils.cpp:3882`），后者把外层的 `child_expr`、`set_expr` 用 `const_cast` 设成参数（`ob_raw_expr_util.cpp:4574`），再 formalize 新的等值式（`:4575`）；
  - formalize 里的 `extract_info` 和 `deduce_type` 都是后序遍历（`ob_raw_expr_info_extractor.cpp:27-29`、`ob_raw_expr_deduce_type.cpp:33-36`），会改写外层子节点的信息和类型；`deduce` 用的工厂是新节点所在的工厂，也就是这个局部工厂（`ob_raw_expr.cpp:376-377`）；
  - C++ 里 `const ObDMLStmt *` 并不让其中的 `ObRawExpr *` 变成只读。
- 真正要证明的是"没有外层节点链接到作用域内的新节点"：
  - 外层子节点在语句里早已 formalize 过，重新推导不需要再加隐式 cast；
  - set-op 表达式的 deduce 是空操作（`ob_raw_expr_deduce_type.cpp:2063-2068`）；
  - 所以新建的 cast 只会挂在新等值式的下面。外层节点本身被改写，在 `qa.scope()` 的语义下保留，与 C++ 一致。
- 还应记一条差异：
  - 局部工厂的 `query_ctx_` 为 NULL（`ob_raw_expr.h:5007`），类型降级在 query ctx 为空时自行关闭（`ob_raw_expr_type_demotion.cpp:187-188`）；
  - `qa.scope()` 沿用外层 QueryArena 的 `query_ctx_`（api/07 §2.8.1），作用域内的推导会看到外层的 query ctx；
  - 本处不会触发：降级要求比较的一边是列引用、另一边是常量，而这里的等值式总有一边是 set-op 表达式。但证据要写明。
- 改正：证据把"只读"换成上面的论证，并补上 query ctx 一条。分类和译法不变。

### 第 6 行：c04 `ObColumnRefRawExpr::column_name_`（`src/sql/resolver/expr/ob_raw_expr.h:3126`）

- 结论 `OBSTR_Q` 成立：字节都在语句作用域内，来源是 schema guard 钉住的列名和语句 arena 里的别名。
- 证据漏了主要的写入者：
  - `ObRawExprUtils::init_column_expr` 经非 const 的 `get_column_name()` 原地 `assign_ptr`（`ob_raw_expr_util.cpp:4811-4812`，来源是 column schema 的列名），resolver 建列引用都要走它；
  - 还有 `ob_select_resolver.cpp:4324`（来源是拷进语句 arena 的别名）。
  - 译法因此写错了一句："the non-const overload … (no write-through caller found)"。
- 改正：证据补上这两处写入；译法删去"no write-through caller found"，写明这两处经 `get_column_name_mut()` 赋值。分类不变。

## 3. 确认行的附注（不影响裁定）

- **第 1 行**：本行按 api/07 §2.10.2 把 `void *` 重载并进带类型的版本、返回 `ObResult<&mut …>`。这与 RULEBOOK §2.1（输出参数保持 `&mut`）和 overloads.tsv 的名字 `create_expr_op_ctx_int64_t_void` 都不一致；提议者已作为问题 10 提出，但 evidence 里没有写 `depends-on`。另外，分配器的选择在 `:604`，不是 `:603`。
- **第 3 行**：这个字段是长生命周期的会话登记"当前活动的子对象"，不是字面意义上的宿主回指。删字段、显式传参是唯一能实现的写法，我同意。读点来源表还要覆盖 `LinkExecCtxGuard` 在 `ob_sql_utils.cpp:3388` 经这个字段找父执行上下文的做法：27 处 guard 都依赖它。
- **第 4 行**：`set_my_session` 的调用行是 24 处，不是 26 处。
- **第 13 行**：本行顺带说"有执行上下文 id 的分支照 C++，每次执行一份 RtExpr 副本"。副本存在 exec ctx 的 ExprOpCtx 表里；求值时既要 `&mut ObEvalCtx`（它可变借用着 exec ctx），又要从 exec ctx 里借出这份副本，按现在的 api/07 写不出来（第 4 节问题 7）。这不影响本行的站点。
- **第 15 行**："ctx_ 里借用 mem_ctx 的东西由借用检查器强制在块内清掉"不准确：长生命周期的成员根本放不进块内局部 `MemCtx` 的借用，应写成"这些 ctx_ 字段必须是拥有型"。
- **第 17 行**：被改写的节点有三种来源：`NAME_OB`（词法器的 `parse_strdup`，`sql_parser_mysql_mode.l:1758`），以及 `TABLET_SIZE`、`LOG_DISK_SIZE` 两个分支里的 `make_name_node`（`sql_parser_mysql_mode.y:15774`、`15787`；`sql_parser_base.h:247-255`）。三者都是解析 arena 里的副本，证据只写了第一种。
- **第 20 行**："保留 `OB_ISNULL(children_)` → `OB_ERR_UNEXPECTED`（`:221-222`）"用 api/09 写不出来：`child(i)` 对 `children_` 为空和元素为空都返回 `None`（`09-parser-sys-generators.md:428`）。函数没有调用者，这一差别不可观察，但它是 API 的缺口（第 4 节问题 6）。
- **第 2、5、7、16、18 行**：引用逐条核对无误。

## 4. RULEBOOK 与 API 的问题（建议排队修订；K2 不改动任何文件）

1. **c07、c08 的分类码**：scripts README 的 S3b 节已经有 c07（`README.md:1274-1281`）和 c08（`:1309-1318`）的码表。README 最后修改于 20:15，分片写于 20:44。提议者说明里"README 没有 S3b 一节"的前提与现状不符，所以第 10–12 行要改用 README 的码，c07、c08 部分的"新提出的码"不必补进 README。这不是 RULEBOOK 的问题，记在这里供合并者参考。
2. **c19 缺"Box 在单一容器之间移动"的码**：RULEBOOK §2.7"队列项"明确要求只挂在一个容器里的对象以 `Box<T>` 转移所有权，锁等待期间的挂起就是把所有权移进等待表；c19 A 类码表（`README.md:900-903`）只有 `SINGLE_OWNER_INTRUSIVE`（侵入式链表）等，没有对应的码。建议补 `SINGLE_OWNER_BOX_MOVE`，与提议者问题 1 的 c19 部分合并。
3. **§5.3"锁保护不明"的默认与 §2.7 规则 6**：默认"非标量放进锁"不应套到两类字段上：
   - 自带同步的成员，如 `ObTxCtx::mt_ctx_`、`trace_log_`，它们的保护并非不明；
   - 解锁钩子 `after_unlock` 在放锁之后读的字段。
   - 建议：§2.7 规则 6 和 api/02 §2.11 补一句"`after_unlock` 只能读 Arg 和锁外字段，放锁后要用的锁内数据由 `before_unlock` 拷进 Arg"；§5.3 注明默认只适用于没有自己的锁的字段；c07 码表加一个字段码（如 `OWN_SYNC`：成员自带锁，不进本锁）。
4. **RULEBOOK §2.7"队列项"、api/05 §2.14 与 MySQL 请求的存储**：
   - RULEBOOK 的"请求挂起 = 所有权移进等待表"假定请求是独立分配的对象；
   - C++ 的 MySQL 请求却是 `ObSqlSockSession` 的按值成员（`ob_sql_sock_session.h:64`），每个包原地重建（`ob_sql_sock_handler.cpp:82-83`），packet 视图在会话内存池里（`:135`），会话内存由 Rust nio 按连接分配；
   - `SpHashNode: Send + 'static`（`05-containers.md:1264`）又要求 `ObRequest` 不借用会话。
   - 建议 RULEBOOK 或清单先定会话一侧的请求槽（如 `Option<Box<rpc::ObRequest>>`，`build_sql_req` 取出，请求结束时归还）以及 `ObRequest::pkt_`、`handle_ctx_` 的拥有方式，再定第 19 行。
5. **0.7(b) 与跨调用保有一次完整执行的对象**：
   - `ObSPIResultSet` 和 `ObInnerSQLResult`（`ob_inner_sql_result.cpp:89-93`，同样在自带的 `mem_context_` 上 placement new `ObResultSet`）都要在跨调用期间保有 `ObResultSet`、`ObExecContext` 和算子；
   - 0.7(b) 的"分块缓冲 + RowId"装不下这些；`MemCtx`、`Arena` 又是 `!Sync`（`01-mem.md:175`、`:352`），借用它们的 `ObResultSet<'q>` 连 `Send` 都不是。
   - 这类对象的表示需要人来定。它与提议者的 TODO(2) 和问题 3 是同一件事，这里补上 `ObInnerSQLResult` 这一处。
6. **api/09 的 `ParseNode` 缺"children_ 是否为空"的读取**：`child(i)` 把 `children_` 为空和元素为空都变成 `None`（`09-parser-sys-generators.md:428`），而 C++ 有区分二者、返回 `OB_ERR_UNEXPECTED` 的分支（如 `ob_sql_utils.cpp:221-222`）。建议加一个 `children_is_null()` 之类的访问函数，否则这类分支不能逐行保留。
7. **api/07 §2.10.2：ExprOpCtx 里存 RtExpr 副本时如何求值**：
   - `ObExprColumnConvCtx` 把表达式副本存进 exec ctx 的表（`ob_expr_column_conv.cpp:764-779`），RULEBOOK 0.6 要求这个分支照 C++；
   - Rust 里求值要 `&mut ObEvalCtx`（它可变借用着 exec ctx），同时又要从 exec ctx 的表里借出副本，二者冲突。
   - 建议给 `ObExecContext` 加取出、放回 ExprOpCtx 的接口（如 `take_expr_op_ctx`/`restore_expr_op_ctx`），或者允许副本放在 `Arc` 里。
8. **`qa.scope()` 与 `query_ctx_`**：
   - C++ 的 33 处局部 `ObRawExprFactory` 的 `query_ctx_` 为 NULL（`ob_raw_expr.h:5007`），类型降级因此关闭（`ob_raw_expr_type_demotion.cpp:187-188`），也不会往外层 query ctx 写约束；
   - api/07 §2.8.3 的 `qa.scope()` 沿用外层 QueryArena，它带着 `query_ctx_`（§2.8.1）。
   - 建议 §2.8.3 规定作用域内 `get_query_ctx()` 的语义，例如提供一种"不带 query ctx"的作用域来对应局部工厂；或者由清单第 11 类逐处确认不受影响。
9. **RULEBOOK §2.4"宿主或父对象的回指针"的范围**：`ObSQLSessionInfo::cur_exec_ctx_` 是长生命周期共享对象登记"当前活动的子对象"，而 RULEBOOK 只允许删除宿主回指。建议 §2.4 把这种"活动子对象登记"单列：删字段、显式传参，配一张读点来源表（与提议者问题 5 合并）。
10. **overloads.tsv 与被合并的重载**：`void *&` 一类"返回原始内存、由调用方 placement new"的重载在 Rust 里没有一一对应的写法（第 1 行），overloads.tsv 给的机械名用不上。建议 overloads.tsv 加一列"并入"，指向 API 文档里合并后的名字（与提议者问题 10 合并）。
