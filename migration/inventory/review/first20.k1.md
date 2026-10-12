# 缺口清单前 20 行：怀疑型审查 K1

> 基线 `7d907abfa`。审查对象：`migration/inventory/first20.tsv` 的 20 个键，在 12 个分片里的 `proposed` 行，以及 `first20.proposal.md`。
> 依据：已签字的 `migration/RULEBOOK.md` v0.2、`migration/scripts/inventory/README.md` 的分类码、`migration/infra/api/01…09`。排队修订（`migration/infra/amendments.tsv`）只当作"可以依赖"，不当作已生效。
> 逐行裁定在 `first20.k1.tsv`。本文只写三类内容：驳回和证据缺陷的追踪与改法、确认行的附注，以及审查中发现的 RULEBOOK/API 问题。

## 0. 统计与一个贯穿性问题

| 裁定 | 行数 | 行 |
|---|---|---|
| confirm | 9 | c02 `create_rowkey_check_hashset`、c03 `my_session_`、c04 `column_name_`、c05 `ObResultSet`、c07 `ObTransCtx::lock_`、c10 `eval_enumset`、c11 `process_with_tmp_context`、c12 `get_subquery_iter`、c21 `replace_questionmarks` |
| evidence-defect | 3 | c03 `cur_exec_ctx_`、c04 `ObEvalCtx::exec_ctx_`、c11 `check_exprs_unique_on_table_items` |
| refute | 8 | c02 `create_expr_op_ctx`、c05 `ObSPIResultSet`、c07 `ObLSMeta::rw_lock_`、c08 `callback_lists_`、c08 `stopped_`、c18 `casedn`、c18 `add_fixed_raw_param`、c19 `ObLockWaitNode` |

**贯穿性问题：分类码。** 提议说明写"脚本 README 只有 S2、S3a 两节，没有 S3b 一节，所以 c07、c08 没有分类码"，这不符合现状：

- `migration/scripts/inventory/README.md` 从第 1174 行起就是 S3b 一节。c07 的码在第 1274 行，c08 的码在第 1309-1316 行。该文件最后修改于 20:15，早于分片的 20:44。
- 所以 c07、c08 的行必须改用 README 的码。
- c19 的 `SINGLE_OWNER_BOX_MOVE` 也不在 c19 的码表里（第 900-904 行）。
- `migration/inventory/README.md:40` 规定，分类列只能填本类别的码或 `UNKNOWN`。

8 条驳回里有 3 条（两条 c08、c07 `rw_lock_` 的一部分）只是码不对，实质结论可以保留。

## 1. 驳回

### 1.1 c02 `ObExecContext::create_expr_op_ctx(…, void *&)#out:2`

**追踪**

- 被调方 `ob_exec_context.cpp:601-618`：
  - `:604` 选分配器。提议写成 `:603`，而 `:603` 是 `int ret = OB_SUCCESS;`；
  - `:605-609` 是前置条件，`:610-612` 是分配失败，`:614-615` 登记进 `expr_op_ctx_store_[op_id]`。
- 登记后的所有者有两个：
  - 执行上下文自己的表，由 `reset_expr_op`（`:502-515`）析构；
  - 被 `ObTempExprCtxReplaceGuard`（`ob_exec_context.h:1040-1062`）换进来的 `ObTempExprCtx` 的表，由 `~ObTempExprCtx`（`ob_expr_frame_info.cpp:560-574`）析构。
  - 提议只写了前一个。
- void* 重载的直接调用者有 6 处，另有模板 `ob_exec_context.h:962-967`：
  - 提议列了 `ob_geo_expr_utils.cpp:999` 和 `ob_vector_index_util.cpp:5217,5235`；
  - 漏了 `ob_expr_json_func_helper.cpp:793,812,831`。这 3 处构造的 `ObJsonParamCacheCtx`、`ObJsonPathCacheCtx`、`ObJsonSchemaCacheCtx`（`ob_expr_json_func_helper.h:96,178,190`）都是 `ObExprOperatorCtx` 子类，同样要改成传入枚举变体。
  - 这些对象构造时都拿 `&exec_ctx->get_allocator()`：它们存在执行上下文的表里，又借执行上下文的 arena，由各自的 c04 行处理。

**为什么驳回**

分类 `OUT_BORROW` 是对的。但译法把输出参数改成返回值，还把两个重载并成一个，依据只有 api/07 §2.10.2。这与已签字的 RULEBOOK 冲突：

- §2.1（`RULEBOOK.md:605`）："输出参数保持 `&mut`，不改成返回值"；
- §2.4（`:683`）：`T *&` → `out: &mut Option<…>`。c02 的 `OUT_BORROW` 码给的也是 `out: &mut Option<&'a T>`；
- §4 的重载规则：`overloads.tsv:6647` 给这个重载的 Rust 名是 `create_expr_op_ctx_int64_t_void`。

`amendments.tsv` 里没有允许这样做的排队修订。07 的 6 条修订是 A1-A6，不包含它。提议说明自己在问题 10 里也指出了冲突，但分片仍照 api/07 写。

**改法**（二选一，写进行里）

- **照 RULEBOOK**：保留独立函数 `create_expr_op_ctx_int64_t_void(&mut self, op_id: u64, init: ObExprOperatorCtx, op_ctx: &mut Option<&mut ObExprOperatorCtx>) -> ObResult`。
  - 只有一处必需的改动：原来的 `op_ctx_size` 换成构造好的变体，因为 C++"先给裸内存、再 placement new"在安全 Rust 里没有对应物；
  - 错误码的判断顺序照 `:605-616`；
  - 6 个调用点全部列出。
- **或者**先把"注册表式的创建函数可以返回表槽借用、两个重载合一"排成 RULEBOOK §2.1/§4 的修订，行里写 `depends-on:<那条修订>`。

### 1.2 c05 `ObSPIResultSet`

**追踪**

与提议一致的部分不重复，只补关键一条：

- `init` 在 `CURRENT_CONTEXT` 下建子上下文（`ob_spi.cpp:125`），把 `ObResultSet` placement new 进 `buf_`，分配器用的是 `mem_context_` 的 arena（`:128-130`）；
- 重试时先清空这个 arena，再重建 `ObResultSet`（`ob_spi.h:217-231`）。

**为什么驳回**

分类 `OWNED_CHUNKS` 是对的，0.7(b) 点名了"SPI 结果集"。但 0.7(b) 的内容是"不用 bump arena，改为拥有型分块缓冲……这类对象不得持有 `&Arena`"（`RULEBOOK.md:319`）；scripts README 的码同样写着"no `&Arena`"（`README.md:873`）。译法却有两处正是 0.7(b) 要去掉的写法：

- 把 `mem_context_` 译成拥有型 `Option<MemCtx>`，而 `MemCtx` 里含一个 bump `Arena`（api/01 §2.4）；
- 把 `result_set_` 写成 `Option<Box<ObResultSet<..>>>`，生命周期留空，而它在 C++ 里借的正是这个 arena（`ob_spi.cpp:128-129`、`ob_spi.h:228-229`）。

提议把"内存来源参数"列为交给人选的三项之一，但这一项已被 0.7(b) 排除。剩下的问题，§5.3 已经给出 UNKNOWN 时的默认："生命周期类别不明 → 长生命周期（拥有型）"（`RULEBOOK.md:1105`）。

另外，提议要求这个类型 `Send + Sync`，可 `MemCtx` 是 `!Sync`（api/01 §2.4），两者不能同时成立。GV$OPEN_CURSOR 是在会话的 thread-data 锁下读它的（`ob_virtual_open_cursor_table.cpp:270-276`），有 `Send` 就够。

**改法**

- 保持 `OWNED_CHUNKS`。
- `result_set_: Option<Box<ObResultSet>>`，`ObResultSet` 取拥有型（不带 `'q`）。依据 §5.3：types.tsv 里 `ObResultSet` 的 lifetime_class 是空的。这一点也决定了 `ObResultSet::mem_pool_` 的 c04 行。
- 不保留 bump arena。如果要保留 C++ 子上下文在内存树上计账的作用，按 api/01 §2.12"长生命周期 `MemoryContext` 成员"一行，改用 `Arc<MemBudget>`。
- `TODO(port)` 只留一项：1d 没有 0.7(b) 的分块缓冲类型。
- 删掉 `+Sync`。

### 1.3 c07 `storage::ObLSMeta::rw_lock_`

**追踪**

8 个字段只在同时持 `update_lock_` 写锁和 `rw_lock_` 写锁时写，提议列的行全部核对无误。

另有三个字段被别的线程在**不持任何锁**的情况下读：

| 字段 | 无锁读点 | 跨线程调用方 |
|---|---|---|
| `restore_status_` | `is_valid()` `ob_ls_meta.cpp:194-197`、`check_ls_need_online` `:433-444` | — |
| `tablet_change_checkpoint_scn_` | `:163-166` | `ob_tablet_gc_service.cpp:156,187`、`ob_tablet_replay_executor.cpp:205` |
| `ls_persistent_state_` | `:88-91`（经 `ob_ls.cpp:251-253`） | `ob_ls_service.cpp:340` |

这三个字段的类型都是一个字长的标量包装：

- `ObRestoreStatus` 只有一个 `Status status_`（`ob_restore_status.h:64`）；
- `SCN` 是一个 u64（`scn.h:144-147`）；
- `ObLSPersistentState` 只有一个 `int64_t state_`（`ob_ls_state.h:266`）。

**为什么驳回**

1. `DATA_IN_LOCK` 不是 c07 的码。c07 的码见 `README.md:1274`：行级是 `GUARDS_FIELDS`，译法里要逐字段给 `IN_LOCK`、`ATOMIC_OUTSIDE`、`ARCSWAP_SNAPSHOT` 等码。
2. 与规则 2 不符。规则 2（`RULEBOOK.md:779`）是"锁外也读的字段：标量改原子量（锁内写、锁外按上面的规则读）"。提议却让这几个无锁读者新加读锁，标 `FIXED(port)`；"锁外的读者也加锁"是规则 2 留给非标量的写法，不适用于标量。
   - 按规则 2 改成原子量之后，`get_restore_status` 等函数在持锁时调用 `is_valid()` 的重入问题也随之消失，提议为此设计的内部辅助函数就不需要了。

**改法**

- 行级：`GUARDS_FIELDS`。
- `restore_status_`、`tablet_change_checkpoint_scn_`、`ls_persistent_state_`：`ATOMIC_OUTSIDE`（锁内写，锁外按 §2.7 用 `Acquire` 读）。
- 其余 5 个字段：`IN_LOCK`。另注明 `all_id_meta_` 自带一把 `ObSpinLock`（`ob_id_service.h:196`）。
- 如果坚持把这三个包装类型算作非标量，就写 `ARCSWAP_SNAPSHOT（读者加锁）`，并给出理由。

以下部分都可以保留：加锁顺序、`assign`、重入点 `:454,:491` 从未在 `rw_lock_` 上触发、MDS 截断锁另立一行。

### 1.4 c08 `memtable::ObTransCallbackMgr::callback_lists_`

**追踪**无异议：

- 构造时为 NULL（`h:206`）；
- 在 `rwlock_` 下双重检查，分配后 `ATOMIC_STORE` 发布（`ob_mvcc_trans_ctx.cpp:318-338`）；
- 只在独占路径上释放：`:219-221` ← `ob_memtable_context.cpp:145` ← `:61-64` 和 `ob_tx_ctx.cpp:160,239`。

**为什么驳回**：`PTR_ONCE_LOCK` 不是 c08 的码。对"由空到非空只设一次"，`README.md:1313` 给的码是 `ONCELOCK`。

**证据小错**

- 扫描的 pretag 是 `macros=LOAD:3,STORE:1; sites=4; …; plain-reads=19; plain-writes=1; addr-taken=1`，里面没有提议引用的 `publish`。
- 普通读的列表漏了 `:405`、`:408`、`:496`、`:499` 和 `h:323`。

**改法**：分类改为 `ONCELOCK`，译法不变，补全读点列表。

### 1.5 c08 `observer::ObService::stopped_`

**为什么驳回**：`ATOMIC_RELAXED` 不是 c08 的码。

这是模块里的停止标志：只在 `stop()` 里写（`ob_service.cpp:243`），只被同一函数里的日志读（`:253`）。对应的码是 `RELAXED_STOP_FLAG`（`README.md:1311`）。不要用 `FIXED_RACE`：这里不存在竞争，也就不该标 `FIXED(port)`。

译法（`AtomicBool` 加 `Relaxed`）不变。

### 1.6 c18 `ObSetConfigResolver::resolve#ObCharset::casedn(name)`

**追踪**

`name` 是 `children_[0]` 字符串的局部视图（`ob_alter_system_resolver.cpp:607-608`），字节来自解析 arena 里的副本：

- `NAME_OB`：词法分析器用 `parse_strdup` 拷贝（`sql_parser_mysql_mode.l:1758`；非 UTF-8 连接走 `:1754`）。
- 另外两个产生式提议漏了：`TABLET_SIZE`、`LOG_DISK_SIZE` 用 `make_name_node`（`sql_parser_mysql_mode.y:15777,15790`），内部同样是 `parse_strdup`（`sql_parser_base.h:247-255`），不改变结论。
- 改写之后马上拷进 `item.name_`（`:610`），后面再没有读者。

所以 `COPY_MODIFY_REPOINT` 是对的；RULEBOOK §2.4 也点名了 `:607-609`。

**为什么驳回**：译法里 `ObCharset::casedn(CS_TYPE_UTF8MB4_GENERAL_CI, &mut w)` 用错了名字。

- 按 §4，`overloads.tsv:1288` 把 `casedn(const ObCollationType, ObString &)` 命名为 `casedn_string`；叫 `casedn` 的是 5 个参数的 `char *` 版本（`ob_charset.h:508`）。照抄会调到错误的重载。
- `casedn_string` 的 Rust 参数取 `&mut ObStringWriter<'_>`，也只是提议自己的假设。字符集单元的处置是 `translate`，RULEBOOK §2.5 和 api/06 都没有规定"就地改写的 `ObString &` 参数"该怎么译。

**改法**：改为调用 `ObCharset::casedn_string(CS_TYPE_UTF8MB4_GENERAL_CI, &mut w)`，并在 evidence 里写明依赖那条签名规则（见 §4 第 9 条）。其余不变。

### 1.7 c18 `ObPsStmtInfo::add_fixed_raw_param#const_cast<char*>(…)`

**追踪**：`add_fixed_raw_param` 有两个调用者，源的来历不同。

- `assign_fixed_raw_params`（`ob_prepare_stmt_struct.cpp:397`，上游是 `ob_ps_cache.cpp:477`）：源是当前请求解析树里的节点。
- `deep_copy_fixed_raw_params`（`:417`，上游是 `ObPsStmtInfo::deep_copy` 的 `:461`）：源是**另一个 PS 信息**的 `raw_params_`。
  - 这些节点已经做过 NEG_PARAM 修正，而 `flag_` 又被原样拷贝（`:360`），所以修正会再跑一次；
  - 在已经修正过的节点上，这次修正是空操作：首字节已是 `'-'`，后面紧跟非空白字符。

**为什么驳回**

- 译法 `OwnedParseTree::new_with(node.node_, |root, arena| …)` 要求源是 `ParseNode<'_>`。
- 在第二条路径上，源在 Rust 里是 `OwnedParseTree`（RULEBOOK 0.12、api/09 §2.2.5）。它不提供 `ParseNode` 句柄，只有 `copy_into` 和 `try_clone`。
- 所以这个译法对一半调用者不成立。提议把 `ObPCParam::node_` 的类型推给 c03 行，但这个函数的同一个参数要同时接两种来源。

**改法**

- 分类不变。
- 译法按来源分两支写明：
  - 源是解析树节点：照现在的 `new_with`；
  - 源是 `OwnedParseTree`：`try_clone()`，再拷贝 `flag_`。修正是幂等的，结果与 C++ 相同；也可以先 `copy_into` 一个临时 `ParseArena`，再 `new_with`。
- 在依赖里写明：`ObPCParam` 需要两种形态，或者一个 enum。

### 1.8 c19 `ObLockWaitNode`

**追踪**（补上提议没有覆盖的"所有者"一层）

- `ObLockWaitNode` 按值嵌在 `rpc::ObRequest` 里（`ob_request.h:124`）。
- MySQL 请求本身又是 `ObSqlSockSession` 的按值成员（`ob_sql_sock_session.h:64`），每个请求都在原地 placement new（`ob_sql_sock_handler.cpp:82-83`）。`ob_sql_sock_handler.h:46-48` 注明："同一时刻只有一个在途请求，`sql_req_` 被复用"。
- 请求结束时，`finish_sql_request`（`ob_sql_request_operator.cpp:94-108`）先 `prepare_request_commit`，再 `commit_request`。后者复位请求的 trace id，并 reuse 会话的包内存池（`ob_sql_sock_session.cpp:99-105`）；请求里的包就在这个池里。
- 请求忙的时候，连接推迟关闭（`rust/sql-nio/src/reactor.rs:713-728`）。
- 队列、工作线程、`hash_`、retire 链在任一时刻只有一个持有它的**裸指针**，而存储始终归会话所有。

**为什么驳回**

1. `SINGLE_OWNER_BOX_MOVE` 不在 c19 的码表里（`README.md:900-904`），违反 `inventory/README.md:40`。
2. 译法的所有权链从"worker queue -> worker (Box<ObRequest>)"开始，但 C++ 里根本不存在一个单独拥有的请求对象。
   - RULEBOOK §2.7（`:803`）确实规定，请求"挂起"就是把所有权移进等待表。
   - 要照这条做，会话必须在投递时把请求借出（`Option<Box<rpc::ObRequest>>`），在 `finish_sql_request`/`commit_request` 时收回；请求里指向会话内存池的包也要另行处理。
   - 这是本行成立的前提，行里没有写。

**改法**

- 分类码先写 `UNKNOWN`，等 c19 码表补上"所有权在容器间移动"的码（见 §4 第 6 条）。
- `TODO(port)` 写明上面两项会话侧的前提。
- 其余译法保留，作为 RULEBOOK §2.7 下的目标形态：`impl SpHashNode for rpc::ObRequest`、retire 链改成 `Vec<Box<_>>`、去掉 `CONTAINER_OF`。

## 2. 证据缺陷

### 2.1 c03 `ObSQLSessionInfo::cur_exec_ctx_`

**结论成立**：删除字段、改为调用时传参，带 TODO。理由有三：

- 会话是 `Arc`；
- 被指的执行上下文是栈上或按值的对象（如 `ob_result_set.h:496-497`），用 `&'q` 或 `Arc`/`Weak` 都不可行；
- 跨线程的文件里没有读点。

**证据问题**

- **写入点不全。** `LinkExecCtxGuard` 共有 26 个构造点，提议列了 16 个，漏了 10 个：`ob_ddl_resolver.cpp:3609,3680,3893`、`ob_schema_printer.cpp:351,790,1685`、`ob_lock_executor.cpp:444`、`ob_index_builder_util.cpp:592`、`ob_fts_index_builder_util.cpp:2517`、`rootserver/truncate_info/ob_truncate_info_service.cpp:214`。这些点都要按新签名传入父上下文；它们多是栈上的临时会话，父上下文为空。
- **被指对象不全。** 不只是 `ObResultSet` 内嵌的上下文，还包括上面这些点上的栈上 `ObExecContext`，以及经 `ExecCtxSessionRegister` 登记的 `ObDesExecContext`（`ob_des_exec_context.cpp:113-114`）。
- **读点漏了编译期的类型降级。** `ob_raw_expr_type_demotion.cpp:184,360` 通过会话取当前执行上下文，据此决定是否降级。

### 2.2 c04 `ObEvalCtx::exec_ctx_`

**结论成立**：`REF_Q`，即 api/07 §2.4.5 每次调用临时构造的 `&'x mut dyn ObExecContextVirt`。

**证据问题**：漏了一个跨调用的逃逸，而且它与"never stored"直接矛盾。

- `ObTempExprCtx` 继承 `ObEvalCtx`（`ob_expr_frame_info.h:184`）。
- 它连同其中的 `exec_ctx_`，被缓存在执行上下文自己的 `temp_expr_ctx_map_` 里跨调用复用：缓存逻辑在 `ob_exec_context.cpp:525-554`，析构在 `:373-382`，使用处在 `ob_expr_frame_info.cpp:578-596`。
- 在 Rust 里，这是执行上下文的自引用。

行里应把它列为逃逸，并写明：缓存要改成只存帧和算子上下文表这类数据，每次求值再构造 `ObEvalCtx`（见 §4 第 4 条）。

### 2.3 c11 `check_exprs_unique_on_table_items` 的局部工厂

**结论成立**：`SCOPE_STMT_QA`。

**证据问题**：证据说"outer inputs are read-only … reads only"，这不对。

- `get_equal_set_conditions` 调用 `create_equal_expr`（`ob_raw_expr_util.cpp:4563-4579`），新节点的两个参数就是外层节点 `child_expr`、`set_expr`。
- 随后的 `formalize`（`:4575`）和 `pull_relation_id`（`:4576`）都会遍历并改写外层节点：
  - 后序遍历见 `ob_raw_expr.cpp:494-512`；
  - 类型推导器用的是新节点自己的工厂，也就是这个局部工厂（`:376-379`）。
- 遍历到外层的运算节点时，`add_implicit_cast`（`ob_raw_expr_deduce_type.cpp:2763-2829`）**可能**用局部工厂往外层节点里插入 cast；类型降级的 `replace_param_expr`（`ob_raw_expr_type_demotion.cpp:401`）同理。

"没有外层节点引用新节点"之所以成立，真正的理由是：外层节点早已推导过一次，重新推导是幂等的。反过来说，如果真的插入了，C++ 在 `:3633` 局部 arena 释放之后就会访问已释放的内存。证据应写这条理由，而不是"只读"；同时注明 `qa.scope` 会保留对外层节点的修改（类型、标志、relation id），与 C++ 相同。

另见 §4 第 2 条：C++ 局部工厂的 `query_ctx_` 是 NULL（`ob_raw_expr.h:5007`），类型降级因此被关闭（`ob_raw_expr_type_demotion.cpp:182-189`），而 `qa.scope` 共享语句的 query ctx，没有复现这一点。在本函数里，新建的比较是"子查询的选择项 = 集合运算表达式"，不是"列 = 常量"，所以没有可观察的差异；但这是 API 层面的问题。

## 3. 确认行的附注

- **c02 `create_rowkey_check_hashset`**：全部引用核对无误。
- **c03 `my_session_`**：
  - `set_my_session` 的调用点是 23 个，不是 26 个。26 是 grep 的行数，多算了声明、定义和 `ObSqlMemoryManager::reset_my_session`。
  - 如果 types.tsv 维持 `ObExecContext` 为共享类型（按 §2.13 方法取 `&self`），这个字段要改成 `ArcSwapOption<ObSQLSessionInfo>`（见 §4 第 11 条）。
- **c04 `column_name_`**：要等 `QueryArena` 和 IR 节点带上 `'q` 才写得出来（§4 第 10 条）。
- **c05 `ObResultSet`**：`ObInnerSQLResult`（`ob_inner_sql_result.cpp:91-92`）和 `PLPrepareResult`（`ob_spi.cpp:105-109`）也把它放在自己的 `mem_context_` 上，与 SPI 同构，所以"`ObResultSet<'q>`"不成立（见 1.2）。
- **c07 `ObTransCtx::lock_`**：
  - README 的码是 `HOOKED`，不是 `HOOKED_COMPOSITE`；
  - `after_unlock` 一直到 `ob_trans_ctx.cpp:115` 才结束；
  - `ObTxCtx::init` 本身就持着 CTX guard（`ob_tx_ctx.cpp:64`），guard 放锁时会跑钩子（`opid_++` 等）。译成 `init(&mut self)` 时要保留这些副作用。
- **c10 `eval_enumset`**：`eval_enumset_with_arg0` 的实现问题见 §4 第 3 条。
- **c21 `replace_questionmarks`**：
  - `ParseNode::child(i)` 分不清"`children_` 为空"和"某个元素为空"：前者在 `:221-222` 报 `OB_ERR_UNEXPECTED`，后者跳过。所以这个检查用现有 API 写不出来；不过函数是死代码，没有可观察的差异（§4 第 5 条）。
  - `format_0` 的签名问题见 §4 第 8 条。

## 4. RULEBOOK 与 API 问题（建议排队修订，我不应用）

1. **api/07 §2.10.2 与 RULEBOOK §2.1/§4 冲突。** api/07 把 `create_expr_op_ctx` 的两个重载合一，并返回 `ObResult<&mut …>`；而 §2.1 规定输出参数不改成返回值，overloads.tsv 也给第二个重载起了单独的名字。要么修订 §2.1（允许注册表式创建函数返回表槽借用、合并重载），要么改 api/07。

2. **`qa.scope` 表达不了"没有 query ctx 的局部工厂"。**
   - C++ 里 33 处局部 `ObRawExprFactory`，以及 5 处建了工厂的临时上下文，`query_ctx_` 都是 NULL（`ob_raw_expr.h:5007`），`try_check_tick_` 计数器也各自独立。
   - 在这些工厂下 `formalize` 时，类型降级被关闭（`ob_raw_expr_type_demotion.cpp:182-189`），也不会往 `query_ctx_->all_expr_constraints_` 加约束（`:510`）。
   - api/07 §2.8.1 只有一个 `query_ctx_`，`scope` 与外层共享它。
   - 建议：给 `scope` 加一个屏蔽 query ctx 的变体（作用域内 `get_query_ctx()` 视为空），由清单第 11 类逐处选用。否则作用域内新建的约束表达式会被外层的 query ctx 引用，作用域截断后就成了墓碑。

3. **`eval_enumset_with_arg0` 的实现路径。**
   - enumset 转换函数自己读 `expr.args_[0]`（`EVAL_ARG`，`ob_datum_cast.cpp:121-128`），而 `EvalEnumSetFn` 的签名里没有放替换参数的位置。所以 `eval_enumset_with_arg0` 只能在帧模块里造一个 `args_[0]` 已替换的局部 `RtExpr`（`ExprRef::Local`）。
   - 有 id 的分支里，`setup_eval_expr` 的 `expr_ = expr`（`ob_expr_column_conv.cpp:772`）同样要复制 `RtExpr`。
   - 但 api/07 规定 `RtExpr` 不 derive `Clone`，它的 `extra_info_` 又是 `Box`。
   - 建议：帧模块提供"浅拷贝、可替换参数、不带 extra_info"的构造函数，或者给 `EvalEnumSetFn` 加一个 `arg0: ExprRef` 参数。

4. **`ObTempExprCtx` 与"ObEvalCtx 不得跨调用保存"冲突。** 执行上下文以临时表达式的地址为键，缓存 `ObTempExprCtx`（它继承 `ObEvalCtx`），见 `ob_exec_context.cpp:525-554`。api/07 需要给出这个缓存的 Rust 形态：只缓存帧和算子上下文表，`ObEvalCtx` 每次重新构造。

5. **api/09 的 `ParseNode` 读不到"`children_` 指针是否为空"。** `child(i)` 把空数组和空元素都折成 `None`，而 C++ 里两者行为不同（如 `ob_sql_utils.cpp:221-222`）。建议加 `has_children_array()`。

6. **c19 码表缺"单一归属、所有权在容器间移动"的码。** RULEBOOK §2.7（`:803`）规定队列项用 `Box<T>` 转移所有权，请求挂起就是把所有权移进等待表；但 scripts README 的 c19 码（`:900-904`）没有对应项。同时要决定 `ObSqlSockSession::sql_req_` 的 Rust 形态：会话如何借出、收回 `Box<ObRequest>`，包内存归谁。

7. **分类码要同步（流程问题，不是 RULEBOOK 修订）。** 提议说明称 README 没有 S3b 一节，不属实。c07、c08 的码以 `README.md:1274` 和 `:1309-1316` 为准：
   - `DATA_IN_LOCK` → `GUARDS_FIELDS` 加逐字段码；
   - `PTR_ONCE_LOCK` → `ONCELOCK`；
   - `ATOMIC_RELAXED` → `RELAXED_STOP_FLAG` 或 `RELAXED_COUNTER`；
   - `HOOKED_COMPOSITE` → `HOOKED`。

8. **`ObNumber::format()` 的两种改法要定一种**（附议提议问题 9）。§2.4 允许"写进调用方缓冲"和"返回拥有型小字符串"两种，必须选定一种；overloads.tsv 给它的名字是 `format_0`。

9. **就地改写的 `ObString &` 参数没有译法。** 如 `ObCharset::casedn_string`、`caseup_string`。RULEBOOK §2.5 里"`ObString` 当写缓冲用"只覆盖了 `assign_buffer` 和 `write`。建议补一行：`→ &mut ObStringWriter<'_>`。

10. **`QueryArena` 不带 `'q`，而 IR 节点的 `ObString` 成员是 `ObStr<'q>`**（附议问题 7）。

11. **`ObExecContext` 是否共享，需要人来定**（附议问题 6）。
    - types.tsv 因为 `errcode_` 有 `ATOMIC_*` 访问，把它标成共享；但这两处原子访问都发生在工作线程上（`ob_result_set.cpp:694-704`、`ob_sql_trans_control.cpp:895`），api/07 则用 `&mut`。
    - 这一点决定 `my_session_` 用 `Option<Arc<…>>` 还是 `ArcSwapOption<…>`，也决定 `ObEvalCtx` 的借用形态。

12. **`ObResultSet` 的生命周期类别。** types.tsv 里这一格是空的。它既作为请求栈上的 `SMART_VAR` 使用，又被 SPI、内部 SQL 结果、PL prepare 结果放在各自的 `mem_context_` 上。按 0.7(b) 和 §5.3，应定为拥有型，或者拆成两种类型。

13. **RULEBOOK §2.7 规则 5 的重入名单**（附议问题 11）：缺 `ob_tx_ctx.cpp:5855-5857`；名单里的 `ob_tx_ctx.cpp:1169` 属于 §2.2 的打印规则，不是重入。

14. **执行上下文的环境指针需要一张来源表**（附议问题 5）。删除 `cur_exec_ctx_` 之后，145 个读点和 26 个 `LinkExecCtxGuard` 构造点各自从哪里拿（父）执行上下文，需要一张生成表，做法类似 `01-mem@L12`。
