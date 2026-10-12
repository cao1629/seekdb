# 缺口清单前 20 行：修订轮（提议者，第 1 轮，也是唯一一轮）

> 依据：两名怀疑型审查者的裁定 `first20.k1.tsv`/`first20.k1.md` 和 `first20.k2.tsv`/`first20.k2.md`；协调者的修订指令。基线 `7d907abfa`。
> 按 kit 规则只修订一次：被驳回或判为证据缺陷的 13 行已在分片里原位改写，键不变，状态改为 `revised`；两名审查者都确认的 7 行内容不动，状态改为 `confirmed`。仍有争议的行，下一步写成 `unknown`。
> 行号按 `first20.tsv` 的顺序（与 K2 相同；提议说明的"总表"另有编号）。

## 0. 总览

| # | 站点 | 原分类 → 新分类 | 状态 | 起因 |
|---|---|---|---|---|
| 1 | c02 `create_expr_op_ctx(…, void *&)` | `OUT_BORROW` → `OUT_BORROW`（译法改） | revised | K1 驳回；K2 附注 |
| 2 | c02 `create_rowkey_check_hashset` | `OUT_BOX` | confirmed | — |
| 3 | c03 `cur_exec_ctx_` | `REMOVE_HOSTPTR`（证据改） | revised | K1 证据缺陷；K2 附注 |
| 4 | c03 `my_session_` | `OPTION_ARC` | confirmed | — |
| 5 | c04 `ObEvalCtx::exec_ctx_` | `REF_Q`（证据、译法改） | revised | K1 证据缺陷 |
| 6 | c04 `column_name_` | `OBSTR_Q`（证据、译法改） | revised | K2 证据缺陷 |
| 7 | c05 `ObResultSet` | `NOT_SELF_ARENA` | confirmed | — |
| 8 | c05 `ObSPIResultSet` | `OWNED_CHUNKS`（译法改） | revised | K1、K2 驳回 |
| 9 | c07 `ObTransCtx::lock_` | `UNKNOWN`（默认划分改） | revised | K2 驳回；K1 附注 |
| 10 | c07 `ObLSMeta::rw_lock_` | `DATA_IN_LOCK` → `GUARDS_FIELDS` | revised | K1、K2 驳回 |
| 11 | c08 `callback_lists_` | `PTR_ONCE_LOCK` → `ONCELOCK` | revised | K1、K2 驳回 |
| 12 | c08 `ObService::stopped_` | `ATOMIC_RELAXED` → `RELAXED_STOP_FLAG` | revised | K1、K2 驳回 |
| 13 | c10 `eval_enumset` | `PLAN_WRITE_EXPLICIT_ARG` | confirmed | — |
| 14 | c11 `check_exprs_unique_on_table_items` | `SCOPE_STMT_QA`（证据改） | revised | K1、K2 证据缺陷 |
| 15 | c11 `process_with_tmp_context` | `SCOPE_REQUEST` | confirmed | — |
| 16 | c12 `get_subquery_iter` | `EXEC_CTX_KIT_LOOKUP` | confirmed | — |
| 17 | c18 `casedn` | `COPY_MODIFY_REPOINT`（译法改） | revised | K1 驳回；K2 附注 |
| 18 | c18 `add_fixed_raw_param` | `COPY_MODIFY_REPOINT`（译法改） | revised | K1 驳回 |
| 19 | c19 `ObLockWaitNode` | `SINGLE_OWNER_BOX_MOVE` → `UNKNOWN` | revised | K1、K2 驳回 |
| 20 | c21 `replace_questionmarks` #2 | `COPY_INTO_PARSE_ARENA` | confirmed | — |

现在是 `UNKNOWN` 的行：第 9 行（字段划分）和第 19 行（请求的所有权）。

**先认错**：提议说明称脚本 README 没有 S3b 一节、c07 和 c08 没有分类码，这与现状不符。S3b 节从 `migration/scripts/inventory/README.md:1172` 开始；c07 的码在 `:1274-1281`，c08 的码在 `:1309-1318`。本轮第 9-12 行全部改用这两张码表；我当时自拟的 `DATA_IN_LOCK`、`PTR_ONCE_LOCK`、`ATOMIC_RELAXED`、`HOOKED_COMPOSITE` 作废。c19 的码表确实缺"按 `Box` 在单一容器间移动"的码，这一条仍然成立（第 19 行）。

## 1. 逐行修订

### 第 1 行　c02 `ObExecContext::create_expr_op_ctx(…, void *&)#out:2`（`OUT_BORROW`，revised）

- **改了什么**：
  - 不再并进 api/07 §2.10.2 那个返回 `ObResult<&mut …>` 的合并版本，改为独立函数，名字取 overloads.tsv 的 `create_expr_op_ctx_int64_t_void`（`overloads.tsv:6647`）。签名是 `fn create_expr_op_ctx_int64_t_void<'a>(&'a mut self, op_id: u64, op_ctx_init: ObExprOperatorCtx, op_ctx: &mut Option<&'a mut ObExprOperatorCtx>) -> ObResult`。
  - 唯一的强制改动：C++ 发出 `op_ctx_size` 字节的裸内存，由调用方 placement new，这在安全 Rust 里没有对应写法，所以改由调用方传入已构造好的枚举变体。
  - 函数体照 `ob_exec_context.cpp:601-618` 的判断顺序；分配器的选择在 `:604`，原来误写成 `:603`。
  - 调用方补到 6 处，补上 `ob_expr_json_func_helper.cpp:793,812,831`。
  - 所有者补上第二个：`ObTempExprCtxReplaceGuard`（`ob_exec_context.h:1040-1062`）换进来的 `ObTempExprCtx` 的表，由 `~ObTempExprCtx`（`ob_expr_frame_info.cpp:560-574`）析构。
- **为什么**：RULEBOOK §2.1 规定输出参数保持 `&mut`、不改成返回值（`RULEBOOK.md:605`）；§2.4 规定 `T *&` 译成 `out: &mut Option<…>`（`:683`）；§4 的重载名在 `overloads.tsv:6647`。按协调者 (b)，照已签字的 RULEBOOK 写。
- **后续**：api/07 §2.10.2 的写法要么照此改，要么修订 §2.1/§4，作为修订 A 排队（见 §2）。

### 第 3 行　c03 `ObSQLSessionInfo::cur_exec_ctx_`（`REMOVE_HOSTPTR`，revised）

- **改了什么**：
  - 写入点补全为 26 个 `LinkExecCtxGuard` 构造点（`grep` 逐个列出）。K2 说有 27 个，我按构造语句只数到 26 个，差别不影响结论。
  - 被指对象补上 DDL、打印、解析、加锁辅助函数里的栈上 `ObExecContext`，以及 `ObDesExecContext` 自己。
  - 读点补上编译期的类型降级（`ob_raw_expr_type_demotion.cpp:184,360`）。
  - 译法补一句：guard 原来经这个字段找父上下文（`ob_sql_utils.cpp:3384-3390`），删字段后，26 个构造点都要显式传父上下文（顶层和栈上会话的辅助函数传 `None`）；`TODO(port)` 的来源表也要覆盖这 26 处。
- **为什么**：K1 列出漏掉的 10 个构造点和类型降级读点；K2 指出父上下文发现依赖这个字段。
- **分类保持**：K2 说这是"活动子对象登记"，不是字面上的宿主回指。c03 的 `REMOVE_HOSTPTR` 写的是"删字段，调用时传参或把状态挪出去"，与这里的处理一致，所以保留。把这种情形在 RULEBOOK §2.4 单列，作为修订 J 排队。

### 第 5 行　c04 `ObEvalCtx::exec_ctx_`（`REF_Q`，revised）

- **改了什么**：逃逸里补上 `ObTempExprCtx`。它继承 `ObEvalCtx`（`ob_expr_frame_info.h:184-194`），被缓存在执行上下文自己的 `temp_expr_ctx_map_` 里（`ob_exec_context.h:906`；缓存逻辑在 `ob_exec_context.cpp:525-553`，以 `ObTempExpr` 的地址为键；析构在 `:373-382`），形成"执行上下文 → `ObTempExprCtx` → `exec_ctx_` → 执行上下文"的自引用。
- **译法**：
  - 这个缓存只存每个临时表达式的数据：帧和帧数、`expr_op_ctx_store_`、`expr_op_size_`；
  - 键从地址改成 id（R4/§2.5）；
  - 去掉 `ObEvalCtx` 基类；
  - `ObTempExpr::eval` 先装入缓存的帧和表，再用 `new_scope` 构造一次性的 `ObEvalCtx`。
- **为什么**：K1。"不得跨调用保存"本身是对的；我漏了这个反例。api/07 没有给这个缓存的形态，作为修订 E 排队。

### 第 6 行　c04 `ObColumnRefRawExpr::column_name_`（`OBSTR_Q`，revised）

- **改了什么**：
  - 证据补上两个主要写入者，都经非 const 的 getter 写：`init_column_expr`（`ob_raw_expr_util.cpp:4811-4812`，来源是 schema 的列名，每个解析期建的列引用都走这里）和 `ob_select_resolver.cpp:4324`（别名先在 `:4320` 拷进语句 arena）。
  - 译法删掉"no write-through caller found"，写成这两处经 `get_column_name_mut()` 赋值。
  - 另注明：要等 `QueryArena` 和 IR 节点带上 `'q` 才写得出来（修订 F）。
- **为什么**：K2。我原来的 grep 模式没有匹配到 `.get_column_name().assign_ptr(`，所以误判没有写穿调用者。两处来源都在语句作用域内，结论不变。

### 第 8 行　c05 `ObSPIResultSet`（`OWNED_CHUNKS`，revised）

- **改了什么**：
  1. 不再保留拥有型的 `MemCtx`。`mem_context_` 和 `mem_context_destroy_guard_` 一并去掉：`MemCtx` 里含 bump `Arena`，而 0.7(b) 要求"不用 bump arena"（`RULEBOOK.md:319`）。没有代码读这个子上下文的 hold，按 api/01 §2.12 关于 MemoryContext 成员那一行属于"其余拥有型"。它在 C++ 里给父内存树计的账，属于 A.4 `query-mem-limit` 已接受的差异。
  2. `result_set_` 改成 `Option<Box<ObResultSet>>`，`ObResultSet` 取不带 `'q` 的拥有型。依据是 §5.3"生命周期类别不明 → 长生命周期（拥有型）"（`RULEBOOK.md:1105`），types.tsv 里它的 lifetime_class 是空的。
  3. 原来交给 `inner_open`（`ob_spi.cpp:2751,2766,2878`）和 `store_params_value_to_str`（`:4310`）的游标 arena 去掉，游标要保留的东西都改成拥有型；`WITH_CONTEXT` 块（`:140,:4464,:4527`）用调用方显式传入的 `MemCtx` 做临时分配（依赖 `01-mem@L12`）。
  4. 只要求 `Send`，删去 `+Sync`。GV$OPEN_CURSOR 持会话的 thread-data 锁读它（证据改引 `ob_virtual_open_cursor_table.cpp:139`），按 api/02 §2.2，`Latch<T>` 只要 `T: Send` 就是 `Sync`。
  5. 补上与它同构的 `ObInnerSQLResult`（`ob_inner_sql_result.cpp:91-92`）和 `PLPrepareResult`（`ob_spi.cpp:105-109`）。
- **为什么**：K1 指出原译法正好保留了 0.7(b) 要去掉的东西（拥有的 bump arena，以及借它的 `ObResultSet<'q>`）；K1、K2 都指出 `Sync` 与 `MemCtx: !Sync` 矛盾。
- **对 K1 的一处保留**：K1 建议 TODO 只留"分块缓冲类型"一项。但 §5.3 规定，用默认值的地方"每处都标 `TODO(port)`"（`RULEBOOK.md:1109`），所以"`ObResultSet` 的生命周期类别"这一项仍以 TODO 保留。问题改写成：请人确认拥有型默认（这会把 c04 的 `ObResultSet::mem_pool_` 定为 `REMOVE_ALLOC`），或者拆成两个类型。

### 第 9 行　c07 `transaction::ObTransCtx::lock_`（`UNKNOWN`，revised）

- **改了什么**：结构部分不变（字段划分定下以后，按 README 的码是 `HOOKED`）。按 §5.3 的默认划分加了三条排除：
  1. init 在发布前写完的字段，保持普通字段；
  2. 自带同步的成员不进任何 CtxLock latch，由它们自己的 c07 行决定：
     - `mt_ctx_`：`ObMemtableCtx` 有 `rwlock_`/`lock_`（`ob_memtable_context.h:557-558`）；`start_access` 只持 ACCESS 锁就调用它（`ob_tx_ctx.cpp:5199`）；放锁后还经 `arg.p_mt_ctx_` 调用它（`ob_trans_ctx.cpp:66`）；
     - `trace_log_`：recorder 自带 latch `TRANS_TRACE_RECORDER_LOCK`（`ob_trans_define.h:489-494`）；
     - `ctx_tx_data_`：自带 `SpinRWLock`（`ob_ctx_tx_data.h:103,123`）。
  3. `after_unlock` 在 CTX latch 放掉之后才运行（`ob_trans_ctx_lock.cpp:166`），却要读 `trans_need_wait_wrap_`（`ob_trans_ctx.cpp:73,89-90,103-104`），而这个字段在锁内写（`ob_tx_ctx.cpp:2111`）。所以 `before_unlock` 要把它的两个值拷进钩子的 Arg，`after_unlock` 只读 Arg、只在 init 写的字段、自带同步的成员和原子量。`opid_` 经 `REC_TRANS_TRACE_EXT2`（`ob_trans_define_v4.h:640-644`）在放锁后被读，它是标量，按默认是原子量。
- **K1 的三个附注一并改掉**：
  - `after_unlock` 的范围是 `:62-115`；
  - 写明分类码 `HOOKED`；
  - init 持着 ALL guard（`ob_tx_ctx.cpp:64`），放锁时会跑钩子（`opid_` 加 1、ELR 交接），Rust 的 init 要保留这些副作用。
- **TODO 的问题扩展为两部分**：字段按哪种 latch 划分；以及上面第 2 条排除名单是否完整。
- **为什么**：K2 的反例成立：按原来的默认，`trans_need_wait_wrap_` 会进 `TransCtxLocked`，`after_unlock` 就读不到；`mt_ctx_` 进锁的话，所有 DML 路径都得取 CTX 锁。

### 第 10 行　c07 `storage::ObLSMeta::rw_lock_`（`GUARDS_FIELDS`，revised）

- **改了什么**：
  - 分类改为 `GUARDS_FIELDS`，并逐字段给码：
    - `IN_LOCK`：`clog_checkpoint_scn_`、`clog_base_lsn_`、`replayable_point_`、`saved_info_`、`all_id_meta_`；
    - `ATOMIC_OUTSIDE`：`restore_status_`（`AtomicU8`）、`tablet_change_checkpoint_scn_`（`AtomicU64`）、`ls_persistent_state_`（`AtomicI64`）。
  - 四个无锁读者改成 `Acquire` 读，标 `FIXED(port)`，不再新加读锁；`is_valid()` 因此在持锁时也能调用，原来的内部辅助函数不需要了。
  - 写明锁中锁的顺序：`all_id_meta_` 自带 `ObSpinLock`（`ob_id_service.h:196`），顺序是 `rw_lock_` → `ObAllIDMeta::lock_`。
  - 证据里 tmp 拷贝的行号改正为 `:146,:179,:210,:253,:302,:334,:354,:394`。
- **为什么**：K1、K2 都指出 `DATA_IN_LOCK` 不是 c07 的码（`README.md:1274-1281`）。K1 进一步指出，规则 2（`RULEBOOK.md:779`）对锁外读的**标量**规定改原子量，而这三个字段都只占一个字长：
  - `enum Status : uint8_t`（`ob_restore_status.h:29,64`）；
  - `SCN::val_`（`scn.h:144-145`）；
  - `int64_t state_`（`ob_ls_state.h:266`）。
  - K2 给的另一种写法 `ARCSWAP_SNAPSHOT`（读者加锁）是给非标量用的，按协调者 (b) 不采用。

### 第 11 行　c08 `memtable::ObTransCallbackMgr::callback_lists_`（`ONCELOCK`，revised）

- **改了什么**：
  - 分类码改为 `ONCELOCK`（`README.md:1313`）。
  - 读点按"是否持 `rwlock_` 读锁"分成两组：
    - 持读锁的读与持写锁的发布互斥，不标记：`:632/:635`（读锁在 `:616`）、`:940`（`:938`）、`fill_from_all_list`（`:976` 加锁，`:977,:984` 读）、`:1415/:1419`（`:1413`）、`:1431`、`:1443`、`:2126`、`:2143`；
    - 不持锁的读才标 `FIXED(port)`，并补上漏掉的 `:405,:408,:496,:499` 和 `h:323`；
    - 共用的辅助函数 `get_callback_list_`、`get_logging_list_count`、`get_callback_list_count` 两种调用路径都有，标记放在辅助函数里。
  - 证据删掉扫描 pretag 里并不存在的 `publish`。
- **为什么**：K1 指出码不对、漏了读点、pretag 写错；K2 指出原来"全部标 FIXED"夸大了竞争。

### 第 12 行　c08 `observer::ObService::stopped_`（`RELAXED_STOP_FLAG`，revised）

- **改了什么**：只改分类码为 `RELAXED_STOP_FLAG`（`README.md:1311`），并写明不标 `FIXED(port)`：这里只有同一线程上的一个写者和一个读者，不存在竞争。
- **为什么**：K1、K2。

### 第 14 行　c11 `check_exprs_unique_on_table_items` 的局部工厂（`SCOPE_STMT_QA`，revised）

- **改了什么**：证据删掉"外层输入只读"，换成正确的论证。
  - 外层节点确实会被改写：`create_equal_expr` 经 `const_cast` 把外层的 `child_expr`、`set_expr` 设成新节点的参数（`ob_raw_expr_util.cpp:4574`），随后的 `formalize`（`:4575`）用新节点所在的局部工厂（`ob_raw_expr.cpp:376-379`）后序重做信息提取和类型推导，再执行 `pull_relation_id`（`:4576`）。
  - 但没有外层节点链到新节点上，理由有三：
    - 外层子节点随语句早已 formalize 过，重新推导是幂等的，不会新加隐式 cast；否则 C++ 在 `:3631` 释放局部 arena 之后就会访问已释放的内存；
    - set-op 表达式的类型推导是空操作（`ob_raw_expr_deduce_type.cpp:2063-2068`）；
    - 局部工厂的 `query_ctx_` 是 NULL（`ob_raw_expr.h:5007`），类型降级因此自行关闭（`ob_raw_expr_type_demotion.cpp:182-189`）。
  - `qa.scope` 保留对外层节点的修改，与 C++ 一致。
  - 新增一条差异说明：`qa.scope` 共享语句的 query ctx（api/07 §2.8.1），降级在 Rust 里不会被关闭。本处不会触发：降级要求一边是列引用、另一边是静态常量（`ob_raw_expr_type_demotion.cpp:316-345,357`），而这里的等值式有一边总是 `ObSetOpRawExpr`，它的信息只有 `IS_SET_OP`（`ob_raw_expr_info_extractor.cpp:487-494`），不会是常量。"不带 query ctx 的作用域"作为修订 G 排队。
- **为什么**：K1、K2。分类和译法不变。

### 第 17 行　c18 `ObSetConfigResolver::resolve#ObCharset::casedn(name)`（`COPY_MODIFY_REPOINT`，revised）

- **改了什么**：
  - 调用名改为 `ObCharset::casedn_string`：`overloads.tsv:1288` 把 `casedn(const ObCollationType, ObString &)`（`ob_charset.h:663`）命名为 `casedn_string`；叫 `casedn` 的是 5 个参数的 `char *` 版本（`:508`）。
  - 证据补上 `TABLET_SIZE`、`LOG_DISK_SIZE` 两个产生式：它们经 `make_name_node`（`sql_parser_mysql_mode.y:15774-15777,15787-15790`）也就是 `parse_strdup`（`sql_parser_base.h:247-255`）建节点，同样是解析 arena 里的副本。
- **为什么**：K1 指出名字用错；两名审查者都补了另外两个产生式。
- **对 K1 的反驳**：K1 认为参数取 `&mut ObStringWriter<'_>` 只是我的假设。我保留这个写法，依据是 RULEBOOK §2.5"ObString 当写缓冲用 → ObStringWriter"一行：`casedn_string` 正是把 `ObString` 当写缓冲用，就地改写字节并 `set_length`（`ob_charset.cpp:1842-1850`），api/06 §2.1.2 的 `ObStringWriter` 提供了对应的 `ptr_mut`/`set_length`。同意把"就地改写的 `ObString &` 参数"在 §2.5 写成明文，作为修订 H 排队。

### 第 18 行　c18 `ObPsStmtInfo::add_fixed_raw_param#const_cast<char*>(…)`（`COPY_MODIFY_REPOINT`，revised）

- **改了什么**：译法按源的两种来历分支写。
  - **(a) 源是请求的解析节点**：`assign_fixed_raw_params`（`:397`）← `ob_ps_cache.cpp:477`，参数是快速解析得到的 `raw_params_`（`ob_sql.cpp:975`）。照原来的 `OwnedParseTree::new_with` 加修正闭包。
  - **(b) 源是另一个 PS 信息的 `raw_params_`**：`deep_copy_fixed_raw_params`（`:417`）← `ObPsStmtInfo::deep_copy`（`:461`）。源在 Rust 里已是 `OwnedParseTree`，拿不到 `ParseNode` 句柄，所以用 `try_clone()` 并拷贝 `flag_`（`:360`）。NEG_PARAM 修正跳过：在已修正的树上它是空操作——首字节是 `'-'`、下一个字节不是空白，扫描停在 `tmp_pos = 1`，在 `'-'` 上写 `'-'`，前移 0。
  - 写明 `ObPCParam` 需要两种形态或一个枚举，由它的 c03 行（`ObPCParam::node_`，`ob_plan_cache_util.h:250`）决定，这一点同时决定本函数的参数类型。
- **为什么**：K1 指出原译法对一半调用者不成立。K1 给了两种写法（`try_clone`，或先 `copy_into` 临时 arena 再 `new_with`），我选 `try_clone`：结果字节相同，少一次深拷贝。

### 第 19 行　c19 `ObLockWaitNode`（`SINGLE_OWNER_BOX_MOVE` → `UNKNOWN`，revised）

- **为什么改成 UNKNOWN**：按协调者 (c)，在现行 RULEBOOK 和 API 下写不出正确答案，原因有三：
  - RULEBOOK §2.7"队列项"（`RULEBOOK.md:803`）假定请求是独立拥有的对象，可以作为 `Box` 移进等待表；
  - C++ 的 MySQL 请求却是 `ObSqlSockSession` 的按值成员（`ob_sql_sock_session.h:64`），每个包原地重建（`ob_sql_sock_handler.cpp:82-85`），`pkt_` 在会话的内存池里（`:135`），`handle_ctx_` 回指会话，提交时内存池被 reuse（`ob_sql_sock_session.cpp:99-105`）；
  - api/05 §2.14 要求 `Box<N>`，且 `N: SpHashNode + Send + 'static`；c19 的码表也没有"按 `Box` 移动"的码。
- **§5.3 默认**：裸指针成员取 `Option<Arc<T>>`；按 §2.7，同时挂在多处的对象由一个 `Arc` 所有者持有、各容器只存克隆；锁保护不明时非标量进锁。具体是：
  - 会话的请求槽是 `Option<Arc<rpc::ObRequest>>`，工作队列项、等待表项、retire 的 `Vec` 和 repost 的实参各持一份克隆；
  - `CONTAINER_OF` 和 `void*` 消失；
  - 节点的可变字段进 `ObRequest` 内的 `Latch<ObLockWaitNode>`；
  - 等待表是 `FixedHash2<LockWaitEntry>`，表项持有 `Arc` 克隆和插入时拷下的排序键；
  - 线程局部的 `Node*` 改成 `WorkerCtx` 里拥有型的 `Option<Arc<rpc::ObRequest>>`（0.5 允许线程局部持有拥有型数据）。
- **补上的路径**（K2）：
  - `transform_row_lock_to_tx_lock`（`ob_lock_wait_mgr.cpp:669-694`）在重新插入之后还执行 `try_lock_times_--`（`:689`），C++ 里这与唤醒者竞争；在 Arc 加 Latch 的默认下成为加锁写，标 `FIXED(port)`；
  - `wait()` 插入之后的写（`:312,:323`）同理。
  - 注释行号改为 `:245-246`。
- **TODO 的问题**：Rust 里 `rpc::ObRequest` 归谁所有？会话的请求槽用 `Option<Box<ObRequest>>`（`build_sql_req` 借出、`finish_sql_request` 收回）还是用 `Arc`？`pkt_`、`handle_ctx_` 归谁所有？定下来以后，补 c19 的 `Box` 移动码，再把默认替换成 RULEBOOK §2.7 的 `Box` 移动路径。

## 2. 需要排队的修订（不改 RULEBOOK，交合并者）

修订行的字母编号供本文和分片引用；`amendments.tsv` 里已有的写成 `<doc>@L<行号>`。

| 编号 | 内容 | 涉及行 |
|---|---|---|
| A | api/07 §2.10.2 把 `create_expr_op_ctx` 两个重载合一、返回表槽借用，与 RULEBOOK §2.1（`:605`）、§4 和 `overloads.tsv:6647` 冲突：要么 api/07 照分片改，要么修订 §2.1/§4（允许注册表式的创建函数返回表槽借用），再给 overloads.tsv 加一列"并入" | 1 |
| B | c19 码表补"按 `Box` 在单一容器之间移动"的码；同时决定 `ObSqlSockSession::sql_req_` 的 Rust 形态（借出、收回的请求槽），以及 `ObRequest::pkt_`、`handle_ctx_` 的所有权 | 19 |
| C | §5.3"锁保护不明"的默认不适用于自带同步的成员；§2.7 规则 6 和 api/02 §2.11 补一句："`after_unlock` 只读 Arg 和锁外字段，放锁后要用的锁内数据由 `before_unlock` 拷进 Arg"；c07 字段码加一个"自带同步、不进本锁"的码（K2 建议叫 `OWN_SYNC`） | 9 |
| D | 0.7(b) 的拥有型分块缓冲和 `RowId` 在 1d 里没有类型；`ObResultSet` 的生命周期类别：一套拥有型，还是拆成两个类型 | 8 |
| E | api/07：`ObTempExprCtx` 缓存的 Rust 形态（只缓存帧和算子上下文表，`ObEvalCtx` 每次重新构造） | 5 |
| F | api/07 §2.8.1：`QueryArena` 和 IR 节点带 `'q`（IR 节点的 `ObString` 成员是 `ObStr<'q>`） | 6 |
| G | api/07 §2.8.3：给 `qa.scope` 加一个不带 query ctx 的变体，对应 33 处局部工厂的 `query_ctx_ == NULL` | 14 |
| H | RULEBOOK §2.5：明文规定"就地改写的 `ObString &` 参数 → `&mut ObStringWriter<'_>`" | 17 |
| I | `ObPCParam` 要有请求解析节点和 `OwnedParseTree` 两种形态；api/09 §2.2.5 `new_with` 的闭包写成 `for<'p> FnOnce(ParseNode<'p>, &'p ParseArena)` | 18 |
| J | RULEBOOK §2.4 单列"长生命周期对象登记活动子对象"（删字段、显式传参）；补一张生成表，写明 145 个 `get_cur_exec_ctx()` 读点和 26 个 `LinkExecCtxGuard` 构造点各自的执行上下文或父上下文来源（与 `01-mem@L12` 同类） | 3 |
| K | RULEBOOK §2.7 规则 5 的重入名单补 `ob_tx_ctx.cpp:5855-5857`；`:1169` 属于 §2.2 打印规则 | 9 |
| L | （来自确认行，行不改）`eval_enumset_with_arg0` 怎么实现：enumset 转换函数自己读 `args_[0]`（`ob_datum_cast.cpp:121-128`），`RtExpr` 又不 `Clone`；有 id 的分支里，存在执行上下文表里的副本，求值时还要能取出、放回 | 13 |
| M | （来自确认行）api/09 的 `ParseNode` 缺"`children_` 是否为空"的读取；`ObNumber::format()` 的两种改法要定一种 | 20 |
| N | （来自确认行）`ObExecContext` 是否算共享类型（types.tsv 标为共享，api/07 却用 `&mut`），这决定 `my_session_` 用 `Option<Arc<…>>` 还是 `ArcSwapOption`；`set_my_session` 的调用点是 23 或 24 个，原行写的 26 是 grep 的行数 | 4 |
| O | （来自确认行）`ObExecContext` 旁路表的 API | 16 |
| P | （来自确认行）"`ctx_` 字段必须是拥有型"比原行"借用检查器会强制清理"更准确（K2） | 15 |

## 3. 确认行

第 2、4、7、13、15、16、20 行内容不动，状态改为 `confirmed`。两名审查者对这些行附带的说明（上表 L-P，以及 K1 对第 7 行"ObInnerSQLResult、PLPrepareResult 同构"的附注）都转成修订或写进第 8 行。按协调者的指令，第 13 行虽然有修订 L 指出的实现问题，仍保持 `confirmed`，不改写。
