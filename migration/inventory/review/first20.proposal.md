# 缺口清单前 20 行：提议者说明（阶段 1c，kit prompt 02）

> 基线 `7d907abfa`。站点来自 `migration/inventory/first20.tsv`，结论写在 12 个分片 `migration/inventory.<类别>.tsv` 里。
> 本文是分片的可读版：每行列出数据流（在哪里创建、在哪里修改、从哪里逃逸、谁释放）；分片的 `evidence` 列是它的压缩版。
> 规则依据是已签字的 `migration/RULEBOOK.md` v0.2；Rust 设施取自 1d 的 API 文档 `migration/infra/api/01…09`，文中写作 api/01 等。
> **当前状态**：已经过一轮审查和一轮修订（见 `first20.k1.*`、`first20.k2.*`、`first20.revision.md`）。两名审查者都确认的 7 行状态为 `confirmed`，其余 13 行已修订一次、状态为 `revised`。本文按修订后的分片写。

## 约定

- **分类码**：一律取自 `migration/scripts/inventory/README.md` 各类别的码表：
  - c07 在 `:1274-1281`；
  - c08 在 `:1309-1318`；
  - 其余类别在 S2、S3a 两节。
  - 提议初稿曾称"README 没有 S3b 一节"，这与事实不符。初稿自拟的 c07、c08 码已全部换掉。
  - 唯一缺的码是 c19 的"按 `Box` 在单一容器之间移动"，所以第 19 行写 `UNKNOWN`（见 `first20.revision.md` §2 修订 B）。
- **修订编号**：`migration/infra/amendments.tsv` 没有编号列，用 `<doc>@L<行号>` 指一条排队修订，行号按该文件的物理行，表头是第 1 行。新发现的修订问题按 `first20.revision.md` §2 的字母编号引用。
- **守卫标签**：C++ 用同一个哨兵（NULL）表示不同失败的地方，evidence 里给守卫行标 `allocation-guard` 或 `precondition-guard`。

## 总表（按 `first20.tsv` 的顺序）

| # | 类别 | 站点 | 分类 | 状态 |
|---|---|---|---|---|
| 1 | c02 | `ObExecContext::create_expr_op_ctx(…, void *&)` 的输出参数 | `OUT_BORROW` | revised |
| 2 | c02 | `ObDMLService::create_rowkey_check_hashset` 的输出参数 | `OUT_BOX` | confirmed |
| 3 | c03 | `ObSQLSessionInfo::cur_exec_ctx_` | `REMOVE_HOSTPTR`（1 个 TODO） | revised |
| 4 | c03 | `ObExecContext::my_session_` | `OPTION_ARC` | confirmed |
| 5 | c04 | `ObEvalCtx::exec_ctx_` | `REF_Q` | revised |
| 6 | c04 | `ObColumnRefRawExpr::column_name_` | `OBSTR_Q` | revised |
| 7 | c05 | `ObResultSet` | `NOT_SELF_ARENA` | confirmed |
| 8 | c05 | `ObSPIResultSet` | `OWNED_CHUNKS`（2 个 TODO） | revised |
| 9 | c07 | `transaction::ObTransCtx::lock_` | `UNKNOWN`（结构已定，字段划分未定） | revised |
| 10 | c07 | `storage::ObLSMeta::rw_lock_` | `GUARDS_FIELDS` | revised |
| 11 | c08 | `memtable::ObTransCallbackMgr::callback_lists_` | `ONCELOCK` | revised |
| 12 | c08 | `observer::ObService::stopped_` | `RELAXED_STOP_FLAG` | revised |
| 13 | c10 | `ObExprColumnConv::eval_enumset` 改写 `args_[0]` | `PLAN_WRITE_EXPLICIT_ARG` | confirmed |
| 14 | c11 | `check_exprs_unique_on_table_items` 的局部工厂 | `SCOPE_STMT_QA` | revised |
| 15 | c11 | `ObMPQuery::process_with_tmp_context` | `SCOPE_REQUEST` | confirmed |
| 16 | c12 | `ObExprSubQueryRef::get_subquery_iter` | `EXEC_CTX_KIT_LOOKUP`（1 个 TODO） | confirmed |
| 17 | c18 | `ObSetConfigResolver::resolve` 的 `casedn` | `COPY_MODIFY_REPOINT` | revised |
| 18 | c18 | `ObPsStmtInfo::add_fixed_raw_param` 的 `const_cast<char*>` | `COPY_MODIFY_REPOINT` | revised |
| 19 | c19 | `ObLockWaitNode` | `UNKNOWN`（所有权模型未定） | revised |
| 20 | c21 | `ObSQLUtils::replace_questionmarks` 写 `str_value_`（#2） | `COPY_INTO_PARSE_ARENA` | confirmed |

## 逐行数据流

### 1. `create_expr_op_ctx(…, void *&)` 的输出参数（c02，`OUT_BORROW`，revised）

- **被调方创建**：从 `pl_expr_allocator_` 或执行上下文的 arena 分配（`ob_exec_context.cpp:604,610`，allocation-guard），先检查前置条件（`:605-609`，precondition-guard）。
- **登记**：以所有者身份登记进当前装着的表槽（`:614`）。当前的表可能是执行上下文自己的，由 `reset_expr_op` `:502-515` 析构；也可能是 `ObTempExprCtxReplaceGuard`（`ob_exec_context.h:1040-1062`）换进来的 `ObTempExprCtx` 的表，由 `~ObTempExprCtx`（`ob_expr_frame_info.cpp:560-574`）析构。
- **调用方**：拿到裸内存后 placement new 一个子类：模板 `ob_exec_context.h:962-967`，以及 6 个 `void*` 调用点 `ob_geo_expr_utils.cpp:999`、`ob_vector_index_util.cpp:5217,5235`、`ob_expr_json_func_helper.cpp:793,812,831`。调用方从不释放。
- **结论**：照 RULEBOOK §2.1、§2.4、§4 写成独立函数 `create_expr_op_ctx_int64_t_void(&'a mut self, op_id, op_ctx_init: ObExprOperatorCtx, op_ctx: &mut Option<&'a mut ObExprOperatorCtx>) -> ObResult`。由调用方传入构造好的枚举变体，是唯一的强制改动。api/07 §2.10.2 的合并写法冲突，作为修订 A 排队。

### 2. `create_rowkey_check_hashset` 的输出参数（c02，`OUT_BOX`，confirmed）

- **被调方创建**：从 `root_ctx` 的 arena 分配（`ob_dml_service.cpp:369,372`），placement new（`:379`），再 `create`（`:380-390`）。守卫：`:374-375` 是 allocation-guard；`:370` 和 `:376-377` 是 precondition-guard。
- **调用方拥有**：update 路径由 `~ObUpdRtDef` 销毁；FK 检查器由 `reset` 销毁；delete 路径放进根执行上下文的 DAS 删除链表，由 `~ObDASCtx` 销毁；`del_rtdef` 和嵌套 FK 级联语句只持别名。
- **结论**：`&mut Option<Box<SeRowkeyDistCtx>>`。`ObHashSet` 要 Drop，不能进 bump arena。

### 3. `ObSQLSessionInfo::cur_exec_ctx_`（c03，`REMOVE_HOSTPTR`，revised）

- **写**：`LinkExecCtxGuard` 写它，共 26 个构造点。guard 经这个字段找父上下文（`ob_sql_utils.cpp:3384-3390`），把它指向本语句的执行上下文（`:3396`），结束时恢复（`:3422`）。另外两处写入：`ExecCtxSessionRegister` 只被 `ObDesExecContext` 用（`ob_des_exec_context.cpp:113-114`）；`reset` 置空（`ob_sql_session_info.cpp:271`）。
- **指向谁**：`ObResultSet` 按值内嵌的执行上下文、各辅助函数栈上的 `ObExecContext`，以及 `ObDesExecContext`。
- **读**：145 行 `get_cur_exec_ctx()`，分布在 73 个文件，全部在会话的工作线程上、请求期间。其中包括编译期的类型降级（`ob_raw_expr_type_demotion.cpp:184,360`）。
- **结论**：长生命周期的 `Arc` 会话不能持有指向栈上或按值对象的指针，所以删字段、调用时传参。26 个 guard 构造点改为显式传父上下文。
- **TODO**：读点和构造点各自从哪里拿执行上下文，需要一张来源表（修订 J）。

### 4. `ObExecContext::my_session_`（c03，`OPTION_ARC`，confirmed）

- **初值与写入**：构造时为 NULL；由 `set_my_session` 写，请求路径传请求会话，辅助函数传栈上的临时会话。
- **所有权**：只在 `ObDesExecContext` 里拥有，在 `cleanup_session` 释放。
- **逃逸**：序列化；写进 `vt_ctx`；回登记到会话。读点带 NULL 检查。
- **结论**：`Option<Arc<ObSQLSessionInfo>>`，栈上的临时会话改用 `Arc::new` 构造。是否改用 `ArcSwapOption` 取决于 `ObExecContext` 是否算共享类型（修订 N）。

### 5. `ObEvalCtx::exec_ctx_`（c04，`REF_Q`，revised）

- **绑定**：构造时绑定（`ob_expr.cpp:47-59`），拷贝构造照搬（`:61-72`），从不改指。
- **跨调用的持有者**：
  - 每个算子（`ob_operator.h:573`）、`ObPL`、`ObDhRangeDistWf` 按值持有；DAS 持引用；
  - 执行上下文自己缓存的 `ObTempExprCtx`：它继承 `ObEvalCtx`（`ob_expr_frame_info.h:184-194`），放在 `temp_expr_ctx_map_` 里（`ob_exec_context.cpp:525-553`），构成自引用。
- **结论**：帧模块的 `&'x mut dyn ObExecContextVirt`，每次调用构造。上面这些持有者各自改写；`ObTempExprCtx` 的缓存只存帧和算子上下文表，键改为 id（修订 E）。

### 6. `ObColumnRefRawExpr::column_name_`（c04，`OBSTR_Q`，revised）

- **写**：
  - 主要写入者 `init_column_expr` 经非 const 的 getter 写入 schema 列名（`ob_raw_expr_util.cpp:4811-4812`）；`ob_select_resolver.cpp:4324` 写入拷进语句 arena 的别名；
  - 其余是浅赋值（`set_column_attr`/`set_column_name`）；
  - `inner_deep_copy` 拷进工厂的 arena（`:1920`）。
- **结论**：`ObStr<'q>`；上面两个写入者改用 `get_column_name_mut()`。这要等 `QueryArena` 和 IR 节点带上 `'q`（修订 F）。

### 7. `ObResultSet`（c05，`NOT_SELF_ARENA`，confirmed）

- **数据流**：`inner_mem_pool_` 只构造、从不使用（`ob_result_set.h:379,470`）。所有成员都从调用方传入的 `mem_pool_` 分配（`:471` 等）。
- **结论**：删掉这个死成员。同一类型还被 `ObSPIResultSet`、`ObInnerSQLResult`、`PLPrepareResult` 跨调用持有，生命周期类别见第 8 行。

### 8. `ObSPIResultSet`（c05，`OWNED_CHUNKS`，revised）

- **创建**：`init` 在 `CURRENT_CONTEXT` 下建子上下文（`ob_spi.cpp:114-134`），把 `ObResultSet` placement new 进 `buf_`，分配器用这个子上下文的 arena（`:127-129`）。
- **游标执行也用这个 arena**：`inner_open` 的 arena 参数（`:2751,2766,2878`）、`WITH_CONTEXT` 块（`:140,:4464,:4527`）。
- **跨调用**：流式游标跨 `spi_cursor_open`/`fetch`/`close` 存活（`ob_pl_type.cpp:1578-1603`）。
- **跨线程**：GV$OPEN_CURSOR 在别的线程、持会话的 thread-data 锁时读它（`ob_virtual_open_cursor_table.cpp:139,270-276`）。
- **结论**：RULEBOOK 0.7(b) 要求不用 bump arena、不持有 `&Arena`。具体是：
  - `allocator_` 去掉，`StmtSavedValue` 改 `Option<Box<_>>`，字节副本改 `Box<[u8]>`；
  - `result_set_` 改 `Option<Box<ObResultSet>>`，按 §5.3 取拥有型 `ObResultSet`；
  - `mem_context_` 去掉，它对父内存树的计账属于 A.4 `query-mem-limit`；
  - 只要求 `Send`。
- **两个 TODO**：1d 没有分块缓冲类型；`ObResultSet` 的生命周期类别要人确认（修订 D）。

### 9. `transaction::ObTransCtx::lock_`（c07，`UNKNOWN`，revised）

- **结构**（字段划分定下后分类为 `HOOKED`）：
  - `CtxLock` 由一把带数据的 `ctx_lock_` 和两把令牌锁组成；
  - `ctx_` 删掉，改为 `impl UnlockHooks for ObTransCtx`；
  - `lock_start_ts_` 随 guard 带出（`FIXED(port)`）；
  - `get_ctx_guard` 返回 guard；
  - 读锁路径不跑钩子（`FIXED(port)`）；
  - 三处重入点改为传 guard。
- **默认划分**：照 §5.3，但有三条排除：
  1. 只在 init 写的字段是普通字段；
  2. 自带同步的成员（`mt_ctx_`、`trace_log_`、`ctx_tx_data_`）不进 CtxLock，由各自的 c07 行决定；
  3. `after_unlock` 放锁后要读的 `trans_need_wait_wrap_`，由 `before_unlock` 拷进 Arg（`ob_trans_ctx.cpp:73,89-90,103-104` ← `ob_tx_ctx.cpp:2111`）。
- **init 的副作用**：init 持着 ALL guard（`ob_tx_ctx.cpp:64`），放锁时会跑钩子，Rust 版要保留这些副作用。
- **问题**：要求三把 latch 的字段划分（ACCESS-only、FLUSH_R-only、CTX-only 三类区域都有），并确认排除名单（修订 C、K）。

### 10. `storage::ObLSMeta::rw_lock_`（c07，`GUARDS_FIELDS`，revised）

- **写**：8 个字段只在 `update_lock_` 写锁和 `rw_lock_` 写锁下修改。
- **锁外读**：其中三个字段被别的线程无锁读，而且都是一个字长的标量：`restore_status_`（`uint8_t` 枚举）、`tablet_change_checkpoint_scn_`（`u64`）、`ls_persistent_state_`（`int64_t`）。
- **结论**：
  - 这三个按规则 2 是 `ATOMIC_OUTSIDE`（`AtomicU8`、`AtomicU64`、`AtomicI64`）：锁内用 `Release` 写，锁外用 `Acquire` 读，无锁读者标 `FIXED(port)`；
  - 其余五个是 `IN_LOCK`；`all_id_meta_` 自带 `ObSpinLock`，加锁顺序是 `rw_lock_` → 它；
  - 只持 `update_lock_` 时读 `IN_LOCK` 字段，改为取读锁；
  - 重入 guard 对 `rw_lock_` 从不触发，改为普通加锁并加 `debug_assert!`。

### 11. `memtable::ObTransCallbackMgr::callback_lists_`（c08，`ONCELOCK`，revised）

- **发布**：在 `rwlock_` 写锁下双重检查，分配后 `ATOMIC_STORE` 发布（`ob_mvcc_trans_ctx.cpp:318-338`）。
- **释放**：只在独占路径上释放。
- **读**：持 `rwlock_` 读锁的读与发布互斥，不需要标记；只有不持锁的读标 `FIXED(port)`。
- **结论**：`OnceLock<Box<[ObTxCallbackList]>>`。

### 12. `observer::ObService::stopped_`（c08，`RELAXED_STOP_FLAG`，revised）

- **读写**：`stop()` 里写（`ob_service.cpp:243`），只被同一函数里的日志读（`:253`）。
- **结论**：`AtomicBool` 加 `Relaxed`，不标 `FIXED(port)`。

### 13. `ObExprColumnConv::eval_enumset` 改写 `args_[0]`（c10，`PLAN_WRITE_EXPLICIT_ARG`，confirmed）

- **C++ 做了什么**：临时改写共享计划的 `args_[0]` 再恢复（`ob_expr_column_conv.cpp:742-746`），这是数据竞争。
- **结论**：RULEBOOK 0.6 已定，改用 `eval_enumset_with_arg0`，标 `FIXED(port)`。它怎么实现见修订 L，行不改。

### 14. `check_exprs_unique_on_table_items` 的局部工厂（c11，`SCOPE_STMT_QA`，revised）

- **新节点**：新建的等值式和拷贝出的表达式只流向局部结构，离开函数的只有 `is_unique`。
- **外层节点会被改写**：新等值式的 `formalize` 会改写外层节点的类型、信息标志和 relation id（`ob_raw_expr_util.cpp:4574-4576`），`qa.scope` 保留这些修改，与 C++ 一致。
- **没有外层节点链到新节点上**，理由有三：
  - 重新推导是幂等的；
  - set-op 表达式的类型推导是空操作；
  - 局部工厂的 `query_ctx_` 为 NULL，类型降级因此关闭。
- **差异**：`qa.scope` 共享语句的 query ctx，降级不会被关闭。本处不会触发，因为等值式有一边总是 set-op 表达式（修订 G）。

### 15. `ObMPQuery::process_with_tmp_context`（c11，`SCOPE_REQUEST`，confirmed）

- **作用域**：只有重试和多语句查询的后续语句走这个临时上下文（`obmp_query.cpp:475`）。
- **结论**：RULEBOOK 0.6 的情况 (a)，块内 `MemCtx::new`，`do_process` 显式接收 `&MemCtx`。K2 的附注：`ctx_` 里这类字段必须是拥有型（修订 P）。

### 16. `ObExprSubQueryRef::get_subquery_iter`（c12，`EXEC_CTX_KIT_LOOKUP`，confirmed）

- **C++ 路径**：在表达式求值时按 id 查算子 kit，找到 SPF 算子再取它的子查询迭代器（`ob_expr_subquery_ref.cpp:312-322`）。
- **结论**：经 `ObExecContext` 按 `SpecId` 索引的表。这张表的 API 还没有，留 TODO（修订 O）。

### 17. `ObSetConfigResolver::resolve` 的 `casedn`（c18，`COPY_MODIFY_REPOINT`，revised）

- **被写的字节**：三个产生式（`NAME_OB`、`TABLET_SIZE`、`LOG_DISK_SIZE`）的节点字符串都是解析 arena 里的副本。
- **写与读**：就地改写（`:609`）后马上拷进 `item.name_`（`:610`），之后没有读者。
- **结论**：拷进同一解析 arena，用 `ObCharset::casedn_string`（`overloads.tsv:1288`）配 `&mut ObStringWriter` 改小写，再改指节点。参数形式的依据是 RULEBOOK §2.5"ObString 当写缓冲用"那一行（修订 H）。

### 18. `ObPsStmtInfo::add_fixed_raw_param` 的 `const_cast<char*>`（c18，`COPY_MODIFY_REPOINT`，revised）

- **被写的字节**：PS 信息自己刚深拷出来的副本。
- **两种来源**：请求解析树（`assign_fixed_raw_params` ← `ob_ps_cache.cpp:477`），或另一个 PS 信息的 `raw_params_`（`deep_copy_fixed_raw_params` ← `ObPsStmtInfo::deep_copy` `:461`）。
- **结论**：
  - 源是请求解析树：在 `OwnedParseTree::new_with` 里拷贝、修改、改指；
  - 源是另一个 PS 信息：用 `try_clone()`，修正是幂等的，可以跳过；
  - `ObPCParam` 需要两种形态（修订 I）。

### 19. `ObLockWaitNode`（c19，`UNKNOWN`，revised）

- **请求本身的存储**：节点所在的 MySQL 请求是 `ObSqlSockSession` 的按值成员，每个包原地重建，包在会话的内存池里（`ob_sql_sock_session.h:64`、`ob_sql_sock_handler.cpp:82-85,135`）。所以 RULEBOOK §2.7 所说的 `Box` 移动没有来源。
- **节点路径**：
  - 插入：`wait` `:305`；
  - 取出：`fetch_waiter` `:392`、`check_timeout` `:512`；
  - 重新投递：`run1` → `repost`（`:661`）→ `CONTAINER_OF`；
  - `transform_row_lock_to_tx_lock` 在重新插入后还会写节点（`:689`）。
- **§5.3 默认**：`Arc<rpc::ObRequest>` 由会话和各容器共享，节点的可变字段进 `Latch`。
- **问题**：请求和它的包、`handle_ctx_` 归谁所有（修订 B）。

### 20. `ObSQLUtils::replace_questionmarks` 写 `str_value_`（#2，c21，`COPY_INTO_PARSE_ARENA`，confirmed）

- **C++ 做了什么**：把节点改指到 `ObNumber::format()` 的线程局部轮转缓冲，且不更新 `str_len_`。
- **结论**：先写进调用方缓冲，拷进解析 arena 后用 `set_str_value` 改指，标 `FIXED(port)`。该函数没有调用者。修订 M 记了两个 API 缺口。

## 交给人的问题

| 行 | 问题 |
|---|---|
| 9 | CtxLock 的字段划分（三把 latch 都带数据，还是只有 `ctx_lock_` 带数据），以及自带同步的成员名单是否完整 |
| 19 | `rpc::ObRequest` 归谁所有：会话的请求槽借出、收回 `Box`，还是用 `Arc`；`pkt_` 和 `handle_ctx_` 归谁所有 |
| 8 | 确认 `ObResultSet` 一律拥有型，还是拆成两个类型；1d 补分块缓冲类型 |
| 3 | 读点和 guard 构造点的执行上下文来源表 |
| 16 | `ObExecContext` 旁路表的 API |

## RULEBOOK 与 API 的问题

修订清单见 `first20.revision.md` §2（A–P），这里不重复。
