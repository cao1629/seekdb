# 调查备忘：SQL 引擎的编译期与运行期对象图

> 设计文档主题调查（只读）。基线 `7d907abfa`（已核对：`git diff --stat 7d907abfa HEAD -- src rust` 为空）。
> 计数命令见各处和附录。凡是 `/tmp/irsurvey-7d907-mz/*.py` 的数字来自临时脚本（未入库，启发式写在附录里）；估算都标了「估算」。

## 1. 结论

1. **推荐：按生命周期分 arena，图内一律用类型化 u32 id。** 编译期（ObRawExpr、语句、TableItem、逻辑算子、Path）每条语句一个 `QueryArena`，临时上下文用可截断的子作用域。运行期计划是 `Arc` 共享的只读 `PhysicalPlan`，内部用 `RtExprId`/`SpecId`。执行期算子是拥有式树，每次调用时借入 `&PhysicalPlan`。
2. **理由：C++ 本来就是“arena 整体释放、对象身份就是语义”。** 运行期表达式本来就存在一个数组里，序列化时就写成“下标+1”（`ob_expr.h:1317`）。改用 id 后，身份和共享原样保留，原地改写不需要任何 unsafe。2,336 处 `get_param_expr` 调用约 95% 只读，写入只有 50–120 处。
3. **bump+`&'a`+`Cell` 不选：** `Cell` 让生命周期不变（invariant），临时 arena 里的节点就不能指向外层。7 处临时上下文和 33 处局部工厂会因此失去提前释放。**`Rc<RefCell>` 也不选：** 在 `panic=abort` 下，借用冲突会直接变成进程崩溃。
4. **573 种表达式用 trait object 注册表**（其中 535 种没有自有字段），运行期仍用函数指针。unsafe 只留在帧访问模块里（估算 20–40 处）。
5. **前提：** 缺口清单核实三件事——临时作用域内不向外层分配；运行期写共享计划的点（已确认 `ob_expr_column_conv.cpp:742-746`）；28 处跨算子旁路。另外，arena 的分配要走内存主题提供的计量分配器。

## 2. 事实

### 2.1 范围

| 目录 | 文件 | 行数 | manifest 单元 |
|---|---|---|---|
| `src/sql/resolver` | 336 | 151,372 | 187 |
| `src/sql/rewrite` | 95 | 97,272 | 48 |
| `src/sql/optimizer` | 205 | 155,804 | 106 |
| `src/sql/code_generator` | 15 | 20,549 | 8 |
| `src/sql/engine`（其中 `engine/expr`） | 1,379（921） | 351,841（170,105） | 726（480） |
| `src/sql/das`、`plan_cache`、`printer` | 171 | 77,237 | 89 |
| `src/query`（API 头文件）、`src/pl`、`src/sql/pl` | 218 | 90,637 | 185 |

- 合计 **1,349 / 3,754 个单元（36%）** 位于操作这些图的目录里。
- 类型出现次数（`surface.py`）：`Ob*RawExpr` 20,557 次，分布在 1,196 个文件；语句类型 6,101 次；`ObLog*` 3,663 次；`ObExpr` 5,254 次（其中 storage 75 次）。
- 参数里带这些类型的函数定义（`sig_counts.py`）：raw expr 3,953 个，语句 1,583 个，运行期 `ObExpr` 1,931 个。

### 2.2 编译期表达式 DAG：ObRawExpr

- **类层次**：
  - 29 个子类，其中 `ObTerminalRawExpr`/`ObNonTerminalRawExpr` 是抽象中间类（`class_hier.py`）。
  - `ExprClass` 有 19 个值，含 INVALID（`ob_raw_expr.h:1770-1791`）。
  - 继承链有“是一个”的用法：`ObSysFunRawExpr : ObOpRawExpr`，`ObUDFRawExpr : ObSysFunRawExpr`。
- **共享是语义，不是偶然**：
  - `ObSharedExprResolver`（`ob_shared_expr_resolver.h:63`）把结构相同的表达式合并成一个节点。
  - 代码生成按对象身份去重：`IS_MARKED` 标记直接写在节点上（`ob_raw_expr_util.h:171-175`），每个 raw 对象只生成一个 `ObExpr`。
  - 所以“同一指针”就意味着运行期“只算一次”（evaluated 标志）。
- **原地改写**：
  - 接口是 `virtual ObRawExpr *&get_param_expr(int64_t)`（`ob_raw_expr.h:2000`）。
  - 基线上出现 2,375 次，其中声明/定义 39 次，**调用 2,336 次**（可行性报告记 2,362，是旧基线上的数）。
  - 写入：绑定到 `ObRawExpr *&` 16 次，直接赋值 34 次（如 `ob_transform_pre_process.cpp:1643`）。另外至多 69 次把槽位传给带 `*&` 形参的函数（`ref_slot_funcs.py`，是上界）。**写入合计 50–120 处，约 95% 是只读。**
  - 更普遍的写法是“槽位参数”：非 const 的 `Ob*RawExpr *&` 共 **1,459 个**，涉及 558 个函数名；主要在 resolver（787）、rewrite（398）、optimizer（174）。
  - 槽位不一定在子节点数组里：`ObAggFunRawExpr` 的第 i 个参数返回的是 `order_items_` 里的槽（`ob_raw_expr.h:3827`）。
- **节点指向图外的指针**：
  - `inner_alloc_`、`expr_factory_`（`:2225-2226`）。
  - `rt_expr_`（`:2234`）：CG 时 raw→rt 的映射。
  - `ObNonTerminalRawExpr::op_`（`:3293`）：每个节点懒创建一个 `ObExprOperator`（`ob_raw_expr.cpp:2156-2171`）。
- **语句与表达式互指**：
  - `ObQueryRefRawExpr::ref_stmt_`（`:2892`，另有 `ObSelectStmt *&get_ref_stmt()` 在 `:2850`）。
  - `TableItem::ref_query_`（`ob_dml_stmt.h:278`）。
  - 相关子查询：`ObExecParamRawExpr::outer_expr_`（`:2786`）跨语句指向外层表达式。
  - `TableItem::node_` 指向解析树（`ob_dml_stmt.h:290`）。
- **工厂**：
  - `ObRawExprFactory::create_raw_expr_inner`（`ob_raw_expr.h:5034-5070`）：在 allocator 上 placement new，再登记进 `expr_store_`。
  - `destory()` 逐个显式调用析构（`:5092-5107`），因为成员里的 `ObSEArray` 用的是 `ModulePageAllocator`，属于堆内存。
  - 语句工厂有 free list：被释放的 `ObSelectStmt` 槽会被复用（`ob_stmt.cpp:207-253`）。
- **向下转型**：转成 `Ob*RawExpr` 子类的 cast 有 1,350 处，其中 dynamic_cast 只有 12 处，其余都是“先查 expr class 再 static_cast”。最多的是 ColumnRef 292、Const 179、QueryRef 77、Op 77。
- **指针身份当键**：
  - `src/sql` 里 `reinterpret_cast<u?int64_t>(` 有 121 处。
  - 例子：`ObRawExprCopier` 的两张表以地址为键（`ob_raw_expr_copier.h:176-177`）；`expr_hash_func` 直接返回指针（`ob_stmt.h:60-66`）。
  - 编译期按地址哈希顺序迭代、再把键还原成对象的地方有 3 处：`ob_raw_expr_copier.cpp:336`、`ob_raw_expr_replacer.cpp:379`、`ob_transform_utils.cpp:7953`。最后一处决定 `shared_exprs` 的顺序。
- **拷贝与遍历**：
  - 拷贝类：`ObIRawExprCopier`/`ObRawExprCopier`/`ObPLExprCopier`/`ObIRawExprReplacer`（`ob_raw_expr_copier.h`）、`ObRawExprReplacer`（`ob_raw_expr_replacer.h:36`）、`ObStmtExprCopier`/`ObStmtExprReplacer`。
  - 调用次数：`deep_copy(` 508，`ObRawExprCopier` 145，`copy_on_replace(` 57，`replace_expr(` 122，`deep_copy_stmt(` 20。
  - 拷贝时用地址映射表保持共享（同一节点只拷一次）。
  - Visitor：`ObRawExprVisitor` 有 16 个 visit 重载、13 个子类。

### 2.3 语句图与改写阶段

- **语句图也是 DAG**：
  - 子语句来自 `TableItem::ref_query_`、子查询表达式（`ob_dml_stmt.cpp:3078-3100`），以及 `ObSelectStmt::set_query_`（`ob_select_stmt.h:573`）。
  - temp table 改写后，多个 TableItem 共享同一条语句（`ob_transform_temp_table.cpp:1165/1172`）。
  - `JoinedTable::left_table_/right_table_` 指向的 TableItem 同时也在 `table_items_` 里（`ob_dml_stmt.h:464-465`）。
- **按槽位遍历整条语句**：
  - `ObDMLStmt::iterate_stmt_expr` 逐个交出语句里每个表达式槽（`ob_dml_stmt.cpp:649-757`）。
  - visitor 用 `reinterpret_cast<ObRawExpr *&>(expr)` 把派生类槽当基类槽改写，改完再检查 class 有没有变（`ob_stmt_expr_visitor.h:113-131`）。
  - `ObRawExprPointer` 保存的是 `ObRawExpr **`，一次改多处引用（`ob_raw_expr.h:5137-5150`）。
- **34 条改写规则**（直接继承 `ObTransformRule`）都在同一份语句图上原地修改语句和表达式。语句级的槽位 `Ob*Stmt *&` 有 330 个。
- **代价改写（CBQT）的流程是“拷贝→试优化→丢弃”**：
  - `evaluate_cost`（`ob_transform_rule.cpp:346-411`）先深拷贝语句、在外层工厂上跑启发式规则。
  - 然后 `CREATE_WITH_TEMP_CONTEXT` 建一个临时 `ObRawExprFactory`（`:379-380`）完整跑一遍优化器，结束后整块释放，再 `free_stmt`（`:407`）。
  - 接受改写时只是交换指针 `stmt = trans_stmt`（`:324/331`）。
- **临时作用域**：
  - `src/sql` 里有 7 处 `CREATE_WITH_TEMP_CONTEXT`，sql+pl 里有 33 处局部 `ObRawExprFactory x(alloc)`。
  - 至少一处让临时节点和语句级节点混在同一组数据结构里：`ob_transform_utils.cpp:3590-3625`。

### 2.4 优化器：逻辑计划与 Path

- **`ObLogicalOperator`**：
  - 33 个子类。
  - 成员：`child_`（`ob_logical_operator.h:1674`）、`my_plan_`（`:1742`）、`parent_`（`:1863`）、`void *traverse_ctx_`（`:1871`）。
  - `set_child` 会顺带改子节点的 `parent_`（`ob_logical_operator.cpp:395-408`）。候选计划共享子树时，`parent_` 以最后一次写入为准，只有最终计划上的才可信。
  - `allocate_*_as_top` 会在 `top` 之上插入新节点（`ob_log_plan.cpp:5983-6010`）。
  - 转成 `ObLog*` 子类的 static_cast 有 309 处。
- **Path 也是 DAG**：
  - `JoinPath::left_path_/right_path_`（`ob_join_order.h:1061-1062`）。
  - `Path::parent_` 指向所属 `ObJoinOrder`（`:518`）。
  - `Path::log_op_` 缓存已经生成的算子（`:526`），所以共享的 Path 会生成共享的算子。
  - Path 类层次共 10 个类。
- **分配**：`ObLogOperatorFactory`（`ob_log_operator_factory.h:267-277`）在优化器 allocator 上分配，这个 allocator 就是语句 mem pool。

### 2.5 代码生成：编译期图 → 运行期计划

- **raw→rt 映射**：`construct_exprs` 先 `prepare_allocate(n)`，再 `set_rt_expr(&rt_exprs.at(i))`（`ob_static_engine_expr_cg.cpp:195-206`）。rt 表达式的下标就是它在展开后 raw 数组里的位置。
- **边**：`args_` 由 raw 的子节点生成（`:332`）；`parents_` 反向边在之后单独计算（`:358-410`）。
- **算子**：`generate_spec` 有 63 个重载。spec 的 id 直接用逻辑算子的编号（`ob_physical_plan.cpp:814-821`）。
- **相位边界干净**：
  - 物理计划不保存任何 ObRawExpr：`ob_physical_plan.h` 只在 `:400` 的注释里提到它。
  - plan cache 和 PS cache 的头文件里没有 `ObRawExpr *`/`ObDMLStmt *`。
  - 编译期 IR 只活到语句结束，只有物理计划会留下来。

### 2.6 运行期计划、帧、共享与每次执行

- **`ObExpr`**：
  - 成员：`args_/arg_cnt_/parents_/parent_cnt_`（`ob_expr.h:678-681`）；`eval_func_` 与序列化用的成员放在 union 里（`:657`）；`void **inner_functions_`（`:673`）；`extra_info_`（`:712`）；以及帧偏移。
  - `eval` 是 const 函数，结果写进帧，不写回表达式（`:1039-1070`）。
  - 全部 ObExpr 存在同一个 `ObArray<ObExpr> &rt_exprs_` 里（`ob_expr_frame_info.h:134`）。
- **`ObOpSpec` 写明“immutable in execution”**（`ob_operator.h:226`）：
  - 树指针在 `:319-326`。
  - `ObOperator` 写明“mutable in execution”（`:361`），持有 `const ObOpSpec &spec_` 和 `ObExecContext &ctx_`（`:571-572`），父子指针在 `:578-579`。
- **每次执行的状态本来就按 id 索引**：
  - 算子和输入：`op_kit_store_`，按 spec id 索引（`ob_exec_context.h:166-185,881`）。
  - 表达式运行期上下文：`expr_op_ctx_store_[expr_ctx_id_]`（`:805`）。
  - 帧：`char **frames_`（`:874`）。
- **帧**：
  - 常量帧直接把计划内存的指针放进每次执行的帧表，所有执行共享（`ob_expr_frame_info.cpp:255-257`）；其余帧每次执行重新分配（`:264-275`）。
  - 帧访问已经封装好：`frames_[` 只直接出现 31 次（其中 15 次在 `ob_expr.h`），访问器调用有 660 次。
- **共享**：
  - plan cache 对象有手写的原子引用计数，还有自己的 MemoryContext（`ob_i_lib_cache_object.h:48,102-104`）。
  - 计划大小 `get_mem_size()=allocator_.total()`（`:60`）参与入缓存判断（`ob_plan_cache.cpp:892`）和淘汰。
  - PX worker 默认共享同一个计划：`_px_worker_share_plan_enabled` 默认 True（`rust/config/parameters.yaml:2149-2154`，`ob_dfo.cpp:578`）。
  - 计划里的统计用原子操作修改：`ob_physical_plan.cpp` 有 52 处 `ATOMIC_`。
- **C++ 运行期写共享计划的点**：
  - `ObExprColumnConv::eval_enumset` 在 `expr_ctx_id` 无效时临时改写共享表达式的 `args_[0]`，用完再改回（`ob_expr_column_conv.cpp:742-746`）。并发执行时这是数据竞争。
  - 待审：`src/sql/engine` 里对 `expr.extra_info_` 的非 const `static_cast` 31 处；`const_cast<...Spec>` 6 处（如 `ob_groupby_op.cpp:61`）。
- **跨算子旁路**：
  - 在 exec ctx 和 operator 之外调用 `get_operator_kit(` 28 处，其中 `engine/px` 目录 24 处。
  - 子查询表达式在 eval 时通过 op id 拿到正在执行的 SPF 算子的迭代器（`ob_expr_subquery_ref.cpp:312-322`）。
  - `ObSubQueryIterator` 持有 `ObOperator &op_` 和 `const ObSubPlanFilterOp *parent_`（`ob_subplan_filter_op.h:126,138`）。
- **其他运行期事实**：
  - `ObDatumCaster` 每次执行临时造一个 ObExpr，指向计划里的表达式（`ob_datum_cast.cpp:13070`）。
  - storage 的下推也用运行期表达式：29 个文件里出现 181 次 `ObExpr/ObEvalCtx`。
  - 执行状态会跨多次协议调用存活：`ObResultSet::open/get_next_row`（`ob_result_set.cpp:167/342`）、`ObSPICursor`（`ob_spi.h:102`）。

### 2.7 573 个表达式算子（ObExprOperator 层次）

- **规模与形状**：
  - 共 573 个子类（`ob_expr_operator.h:304`；`class_hier.py`）。深度分布：1 层 44，2 层 418，3 层 100，4 层 11。主干中间类是 `ObFuncExprOperator`（下面共 406 个）。
  - 只有 **38 个有自有数据成员**（`exprop_shape.py`）。
  - 覆写次数：`cg_expr` 513；`calc_result_type0/1/2/3/N` 分别 34/142/102/24/178；`need_rt_ctx` 56；`get_cast_mode` 36；`assign` 24；旧引擎的 `calc_result*` 36；`DECLARE_SET_LOCAL_SESSION_VARS` 85。
- **注册**：
  - 静态表 `OP_ALLOC[T_MAX_OP]` 以 ItemType 为下标（`ob_expr_operator_factory.cpp:429`），由 528 个 `REG_OP` 和 1 个 `REG_SAME_OP` 填充（`:511` 起）。
  - 按函数名查找用 `NAME_TYPES` 线性扫描。
  - `ObItemType` 共 2,021 个枚举值，算子区间 `T_MIN_OP=100..T_MAX_OP=3000` 内有 742 个（`src/objit/include/objit/common/ob_item_type.h:116,919`，`src/query/api` 下还有一份副本）。
- **两阶段**：
  - 编译期：通过节点上懒创建的 op 实例做类型推导和 `cg_expr`。
  - 运行期：只剩函数指针。`EvalFunc`/`EvalBatchFunc`（`ob_expr.h:617-620`）；`eval_func_ =` 赋值 579 处，`eval_batch_func_ =` 93 处；约 609 个行级、137 个批量 eval 函数定义（`eval_fns.py`）。
  - 按类型索引的函数指针表有 104 处数组声明（含 extern），如 `NULLSAFE_TYPE_CMP_FUNCS[ObMaxType][ObMaxType][2]`（`ob_datum_funcs.cpp:55`）。
  - `inner_functions_` 是 `void**`，出现 78 次、在 18 个文件里。`ObIExprExtraInfo` 有 15–19 个子类，`ObExprOperatorCtx` 有 25 个子类。

### 2.8 序列化：只在进程内使用

- ObExpr 指针写成 `u32` 的“下标+1”，0 表示 NULL，经由一个线程局部数组换算（`ob_expr.h:575-579,1316-1356`）。
- 函数指针直接写成**地址**（`ob_serializable_function.h:54/66/83`），所以序列化结果只在同一进程内有效。seekdb 没有远程计划执行，也没有远程 DAS，只有进程内的 SQC 启动器（`ob_px_local_sqc_launcher.cpp:428-493`）。
- PX 有两条路：
  - 共享计划（默认）：只序列化帧信息和 op input。
  - 不共享：每个 worker 反序列化出一份完整的计划副本（`ob_dfo.cpp:568-704`）。
- `ObExprFrameInfo::assign` 复制计划时，用指针减法把 `args_`/`parents_` 换算成下标再换回去（`ob_expr_frame_info.cpp:139-161`）。

### 2.9 生命周期汇总

| 对象 | 分配者 | 活多久 |
|---|---|---|
| ParseNode | `ObParser(result.get_mem_pool())`（`ob_sql.cpp:829-832`） | 语句（ObResultSet） |
| ObStmt、ObRawExpr、TableItem | `ObExecContext` 懒创建的工厂，建在 exec ctx allocator 上（`ob_exec_context.cpp:642-664`） | 语句；CBQT 试算的语句副本仍在外层（语句槽可回收），优化器新建的表达式进临时上下文 |
| ObLogPlan、ObLogicalOperator、Path | 优化器 allocator，即语句 mem pool（`ob_sql.cpp:2335-2349`） | 语句；CBQT 试算时是临时上下文 |
| ObPhysicalPlan、ObOpSpec、ObExpr 数组、帧布局、常量帧 | plan cache 对象自己的 MemoryContext | 引用计数归零时释放；跨会话、跨 PX worker 共享 |
| ObExecContext、ObOperator、ObOpInput、帧、表达式运行期上下文 | exec ctx allocator | 一次执行，游标则更长 |

### 2.10 其他会影响对齐的事实

- **栈**：`SMART_CALL` 在 sql+pl 里用了 1,072 次。栈余量低于 64 KiB 时切换到堆上的扩展栈；扩展栈总量超过 `ALL_STACK_LIMIT` 时返回 `OB_SIZE_OVERFLOW`（`ob_smart_call.h:30-36,82,112-128`）。
- **内存**：工厂每创建 1024 个表达式检查一次 `CHECK_MEM_STATUS`（`ob_raw_expr.h:5074-5078`），超限会报错，用户可见。
- **已有 Rust 的先例**：
  - `sql-nio` 已经用 `slab::Slab` 的下标代替指针（`rust/sql-nio/src/reactor.rs:118-146`）。
  - 工作区约定 `panic = "abort"`，release 用 `codegen-units = 1` 加 thin LTO（`rust/Cargo.toml:11-23`）。

## 3. 选项

### 选项 A：所有图都用类型化索引（`ExprId(u32)` 存进 `Vec`），连执行期算子也用

- **做法**：
  - 每张图一个 `IdVec<Id, T>`，id 是 `NonZeroU32` 包装，所以 `Option<Id>` 仍是 4 字节，可以直接对应可空指针。
  - 执行期算子也放进 `IdVec<OpId, RefCell<Box<dyn Operator>>>`，算子之间按 id 互访。
- **优点**：
  - 身份就是 id，共享和“只算一次”的语义原样保留。
  - 地址为键的表直接改成 id 为键；深拷贝就是 `FxHashMap<ExprId, ExprId>`。
  - 运行期 id 和 C++ 的序列化格式（下标+1）一致。
- **缺点**：
  - 函数都得能拿到 arena：约 590 个工具函数和自由函数（`*Util(s)`/`*Helper` 的静态函数 325 个，自由/类内联 266 个）必须加参数；如果统一规定“arena 一律显式传参”，最多要改 2,270 个签名（估算）。
  - 执行期的 `RefCell`：SPF 正在执行（已被借出）时，子查询表达式又要借同一个 SPF，必然 panic。
- **unsafe**：图结构里没有；帧访问另算。

### 选项 B：编译期用 bump arena 加 `&'a` 引用和 `Cell/RefCell`，运行期仍用索引

- **做法**：
  - 编译期节点形如 `bumpalo::Bump` 上的 `&'a RawExpr<'a>`。
  - 子节点槽是 `&'a [Cell<&'a RawExpr<'a>>]`，正好对应 C++ 的 `ObRawExpr *&`。
  - 会增长的数组用 `RefCell<bumpalo::collections::Vec<'a, _>>`。
  - 运行期计划如果也用引用，就成了自引用结构，需要 `self_cell`，所以运行期仍用 A。
- **优点**：读路径的写法最像 C++，不用给工具函数加 arena 参数（生命周期大多可以省略）。
- **缺点**：
  - **`Cell` 使 `RawExpr<'a>` 对 `'a` 不变**：临时 arena 里的节点不能指向外层节点。7 处临时上下文和 33 处局部工厂只能并进语句 arena，CBQT 每次试算的内存一直留到语句结束。这会改变内存峰值和“内存超限”报错，属于对齐风险。
  - bumpalo 不跑 Drop：节点里的堆字段必须全部改成 bump 集合。
  - 要给语句、TableItem、逻辑算子、Path、各种上下文和 visitor 都加 `'a` 参数，代理在第 4 步要修大量生命周期错误。
  - `RefCell` 冲突就是 abort。
- **unsafe**：0（bumpalo 内部有）。

### 选项 C：`Rc<RefCell<…>>` 加 `Weak` 反向边，运行期用 `Arc`

- **优点**：写法简单；还能保留“按指针地址哈希”，包括 C++ 那种随地址变化的顺序。
- **缺点**：
  - 遍历中途拿着 `Ref` 守卫又去调用会修改同一节点的函数，这在 C++ 里是常态，到这里就 panic，在 `panic=abort` 下等于崩溃。
  - 每个节点都有引用计数和单独分配。
  - 深表达式递归 drop 可能爆栈，而 C++ 是整块释放。
  - 运行期 `Arc` 的原子计数会进入热路径。
- **unsafe**：0。**对齐风险最高。**

### 选项 D（推荐）：按图选择的混合方案

- **编译期**：A 的 `QueryArena` 和 `OptArena`。临时上下文是可截断的子作用域。
- **运行期计划**：A 的索引，放在 `Arc` 里只读共享，不再做字节序列化。
- **执行期**：拥有式 `Box<dyn Operator>` 树。每次调用传入 `&PhysicalPlan`，计划不存成引用。旁路（SPF 迭代器、PX op input）挪进 `ExecRuntime` 的 id 表。
- **帧**：保留 C++ 的字节布局，unsafe 只在帧模块里。
- **种类**：封闭小集合用 enum（RawExpr 约 27 种、LogOp 33、Path 10、OpSpec 由 61 个 `REGISTER_OPERATOR` 生成）；开放大集合用 trait object（573 种 ExprOp、ExtraInfo、ExprOpCtx、Operator）。

```rust
// 编译期：一条语句一个 QueryArena（≈ ObRawExprFactory + ObStmtFactory 的对象仓库）
seekdb_ir::define_id!(ExprId); seekdb_ir::define_id!(StmtId); seekdb_ir::define_id!(TableItemId);
pub struct RawExpr { pub hdr: RawExprHdr, pub kind: RawExprKind }
pub enum RawExprKind {                       // 29 个 C++ 类 → 约 27 个变体，大载荷装箱
    Const(ConstData), ExecParam { outer: Option<ExprId> }, ColumnRef(Box<ColumnRefData>),
    QueryRef(Box<QueryRefData>),             // ref_stmt: Option<StmtId>, exec_params: SmallVec<[ExprId; 4]>
    Op(OpData), SysFunc(Box<SysFunData>),    // SysFunData { op: OpData, .. }：组合代替继承，as_op() 统一取
    Agg(Box<AggData>), WinFun(Box<WinFunData>), AliasRef { ref_expr: ExprId }, /* … */
}
pub struct OpData { pub params: SmallVec<[ExprId; 4]>, pub op: Option<Box<OpState>> } // OpState = op_ 的实例状态
impl QueryArena {
    pub fn param(&self, e: ExprId, i: usize) -> Option<ExprId>;           // get_param_expr(i) 读
    pub fn set_param(&mut self, e: ExprId, i: usize, v: Option<ExprId>); // get_param_expr(i) = v
    pub fn scope(&mut self) -> ArenaScope<'_>;                            // 临时上下文：drop 时截断
}
// 改写（ob_transform_simplify_expr.cpp:2325 的 ObRawExpr *&child = e->get_param_expr(i)）
let mut child = qa.param(e, i).ok_or(OB_ERR_UNEXPECTED)?;
smart_call(|| self.recursive_push_not(qa, &mut child, map, happened))?;   // stacker::maybe_grow 包装
qa.set_param(e, i, Some(child));                                            // 槽位立即写回
```

```rust
// 运行期：plan cache 里的共享对象，Send + Sync，CG 之后只读
define_id!(RtExprId); define_id!(SpecId);    // u32，0 = NULL，与 ob_expr.h:1317 的“下标+1”相同
pub type EvalFn = fn(&RtExpr, &mut EvalCtx<'_>, &mut Datum) -> ObResult<()>;
pub struct RtExpr { pub item_type: ItemType, pub datum_meta: DatumMeta, pub eval: Option<EvalFn>,
    pub eval_batch: Option<EvalBatchFn>, pub inner_fns: Box<[InnerFn]>, // 带类型的函数指针 enum，代替 void**
    pub args: IdRange, pub parents: IdRange, pub frame: FrameSlot, pub expr_ctx_id: Option<u32>,
    pub extra: u64, pub extra_info: Option<Box<dyn ExprExtraInfo>> /* : Send + Sync */ }
pub struct PhysicalPlan { pub exprs: Box<[RtExpr]>, pub edges: Box<[RtExprId]>, pub frames: FrameInfo,
    pub specs: IdVec<SpecId, OpSpec>, pub root: SpecId, pub stat: PlanStat /* 全是原子量 */ }
pub trait Operator: Send {                   // 执行期：ObResultSet/游标持有 Arc<PhysicalPlan> 和算子树
    fn open(&mut self, plan: &PhysicalPlan, rt: &mut ExecRuntime) -> ObResult<()>;
    fn get_next_batch(&mut self, plan: &PhysicalPlan, rt: &mut ExecRuntime, max: usize) -> ObResult<()>;
    fn rescan(&mut self, plan: &PhysicalPlan, rt: &mut ExecRuntime) -> ObResult<()>;
}
// 573 种：零大小结构体 + 静态注册表（由 529 行 REG_OP 生成），按 ItemType 下标取
pub trait ExprOp: Sync {
    fn calc_result_type(&self, st: &mut OpState, ty: &mut ExprResType, args: &mut [ExprResType], tc: &mut TypeCtx) -> ObResult<()>;
    fn cg_expr(&self, cg: &mut ExprCgCtx, raw: RawExprView<'_>, rt: &mut RtExpr) -> ObResult<()>;
    fn need_rt_ctx(&self, _st: &OpState) -> bool { false }
}
```

- **unsafe 用量（估算）**：
  - 编译期为 0。
  - 运行期 20–40 个 unsafe 块，全部在帧模块：对应 `ob_expr.h` 里约 15 个访问器，加上其余 16 处直接访问。常量帧只给只读访问器。
  - 可选：参数访问用 `get_unchecked`（1 处，`PERF(port)`，前提是计划构造时已校验所有 id）。
  - Datum 指针归内存主题。

### 573 种表达式：enum 还是 trait object

| | E1：573 变体 enum | E2：trait object 注册表（推荐） | E3：手写函数指针表结构体 |
|---|---|---|---|
| 逐文件翻译 | 每个文件都要改中心 enum 和 ~10 个 match | 文件内自洽，只在注册表加一行（和 `REG_OP` 一一对应） | 同 E2，但样板多 |
| 有状态的 38 种 | 变体自带字段 | 放进节点上的 `OpState`/`OpExtra` | 同 E2 |
| 编译时间 | 10 个 573 臂的巨型函数；增量编译时一改全改 | 各实现独立编译 | 同 E2 |
| 性能 | 不影响运行期（运行期本来就是函数指针） | 同左 | 同左 |

### 综合比较

| 维度 | A | B | C | D |
|---|---|---|---|---|
| 内存安全 | 图结构 0 unsafe；执行期有 RefCell panic | 0 unsafe；有 RefCell abort | 0 unsafe；新增崩溃模式最多 | 0 unsafe；帧模块约 20–40 处 |
| 性能（估算，未测） | ≈ C++：多一次下标加边界检查 | 编译期最快 | 最差：计数、分散分配 | ≈ C++ |
| 机械可译 | 规则清楚；590–2,270 个签名加 arena 参数 | 读路径像 C++；写路径要判断借用和生命周期 | 守卫的生命周期难以机械判断 | 同 A，另有 3 类非机械点（见 4） |
| 对齐风险 | 低；执行期 RefCell 是例外 | 中：临时作用域失效 → 内存行为变化 | 高 | 低 |
| 编译时间 | 低 | 中：修生命周期错误的轮次多 | 低–中 | 低 |
| 耦合 | arena 走计量分配器 | Bump 不跑 Drop；与内存主题冲突 | Arc 牵连并发主题 | 同 A |

## 4. 推荐

**推荐选项 D 加 E2。理由：**

1. **C++ 已经是“arena 加身份”的模型。** 编译期对象从不单独释放；共享是语义（`ObSharedExprResolver`，CG 按身份去重）。运行期表达式本来就是一个数组，序列化时就用下标（`ob_expr.h:1317-1355`），每次执行的状态也已经按 id 索引（op kit、expr ctx、帧）。改用 id 是把隐含的结构写出来，而不是推倒重来。
2. **原地改写用 id 表达成本最低。** 写入只有 50–120 处，槽位参数 1,459 个，都可以机械改成“拷出、修改、立即写回”。按地址的表（121 处）改成按 id；1,350 处向下转型改成带检查的访问器（`as_column_ref()?`），把 C++ 里未定义行为的情况变成 `OB_ERR_UNEXPECTED`。
3. **只有 id 能保留临时作用域的提前释放。** 子作用域截断的语义和 C++ 释放临时 MemoryContext 完全一样：新分配的丢弃，对外层节点的修改保留。B 做不到。
4. **运行期计划必须 `Send + Sync`**（plan cache 跨会话共享，PX 默认共享计划）。用 id 就没有自引用问题。共享计划在 Rust 里可以直接写成 `&RtExpr`，C++ 里运行期写共享计划的点会变成编译错误，必须译成每次执行的局部副本。
5. **执行期不全用 id。** 算子树本质上就是一棵拥有关系树，只有 28 处旁路要重新设计。用 `RefCell` 全索引在 SPF 上一定会冲突。

**给翻译代理的规则**（写进设计文档）：

- R1：`ObRawExpr *` → `ExprId`/`Option<ExprId>`；arena 一律显式传参（`qa: &QueryArena` 或 `&mut QueryArena`），不存进 self。
- R2：不要在调用 `&mut QueryArena` 期间持有 `&RawExpr`，先把需要的 id 或字段拷出来。
- R3：槽位改成拷出、调用、立即 `set_param`，标 `SLOT(port)` 供审查。
- R4：地址键 → `FxHashMap<Id,_>`；需要迭代的改成 `IndexMap`（插入顺序），并标 `PARITY(port)`。
- R5：`IS_MARKED` 一类“写在节点上的访问标记” → `FixedBitSet` 旁表；`rt_expr_` → CG 本地的 `IdVec<ExprId, Option<RtExprId>>`。
- R6：`SMART_CALL` → `smart_call()`，内部用 `stacker::maybe_grow`，保持 64 KiB 余量、总量上限和 `OB_SIZE_OVERFLOW`。
- R7：visitor 的双分派 → `walk_expr` 里的 `match`。
- R8：语句回收用 `slotmap` 的代际 key，旧 id 访问会报错。

**非机械点**（设计文档里要逐个给出方案）：

1. SPF/子查询迭代器和 PX 的 28 处旁路：迭代器拥有子算子，存进 `ExecRuntime`。
2. 运行期写共享计划的点：已确认 1 处，另有 31+6 处待审，统一改成每次执行的局部副本，标 `BUG(port)`。
3. 7 处临时上下文和 33 处局部工厂的作用域划分，以及帧模块的 API。

**前提**：

- 缺口清单逐处核实临时作用域内没有向外层工厂分配；如果有，就改用分层 id（问题 2 选 B）。
- 内存主题提供带计量的分配器（Vec 的 allocator 或全局分配器加上下文标签），保留 `CHECK_MEM_STATUS` 和计划大小统计。
- 并发主题接受 `Arc<PhysicalPlan>` 加原子统计。

## 5. 需要人决定的问题

1. **编译期图**（ObRawExpr、语句、TableItem、逻辑算子、Path）怎么表示？
   - A. 每条语句一个 arena，加类型化 u32 id **【推荐】**
   - B. bumpalo + `&'a` + Cell/RefCell
   - C. `Rc<RefCell>`
2. **临时作用域**（7 处 `CREATE_WITH_TEMP_CONTEXT`、33 处局部工厂）怎么处理？
   - A. 子作用域，结束时截断 **【推荐；前提是缺口清单确认作用域内不向外层分配】**
   - B. 分层 id，id 高 2 位标层，允许混合分配
   - C. 不提前释放，全部进语句 arena，接受 CBQT 内存增长
3. **573 种表达式**？
   - A. trait object 注册表，零大小结构体 + 静态表 **【推荐】**
   - B. 573 变体的 enum
   - C. 手写函数指针表
4. **计划共享与 PX**？
   - A. `Arc<PhysicalPlan>`，删掉计划和表达式的字节序列化；`_px_worker_share_plan_enabled=False` 时改为深拷贝（`Clone`） **【推荐】**
   - B. 保留字节序列化，函数指针改成注册表序号
   - C. 保留字节序列化，照抄按地址编码
5. **执行期算子**？
   - A. 拥有式 `Box<dyn Operator>` 树，每次调用传 `&PhysicalPlan`，旁路挪进 `ExecRuntime` 的 id 表 **【推荐】**
   - B. 全部放进 `IdVec<OpId, RefCell<…>>`，按 id 互访
   - C. 直译裸指针加 unsafe
6. **帧内存**（与内存主题一起定）？
   - A. 保留 C++ 字节帧布局，unsafe 集中在帧模块 **【推荐】**
   - B. 改成强类型列（每个表达式一个 `Vec<Datum>`），重做 CG 的帧布局
7. **语句回收**（`ObStmtFactory` 的 free list）？
   - A. `slotmap` 代际 key **【推荐】**
   - B. 只追加，不回收
8. **C++ 运行期写共享计划的地方**（如 `ob_expr_column_conv.cpp:742-746`）？
   - A. 译成每次执行的局部副本，标 `BUG(port)` **【推荐】**
   - B. 用 `UnsafeCell` 原样保留
9. **封闭小集合**（RawExpr 约 27 种、LogOp 33、Path 10、OpSpec 61）？
   - A. enum + 公共头结构 **【推荐】**
   - B. trait object + 向下转型
10. **按地址顺序迭代的 3 处**（`ob_transform_utils.cpp:7953` 等），C++ 的顺序随地址而变：
    - A. Rust 按插入顺序，裁判对相关 EXPLAIN 输出做归一化 **【推荐】**
    - B. 想办法模拟 C++ 的哈希顺序

## 6. 对其他主题的依赖或影响

- **内存/分配器**：
  - arena 的 Vec 要走 OB 的计量分配器，保留每 1024 次分配一次的 `CHECK_MEM_STATUS`。
  - 计划大小（`allocator_.total()`）影响入缓存判断和淘汰（`ob_plan_cache.cpp:892,1316`），也出现在 plan cache 视图里，Rust 的口径会不同，需要定义等价统计，裁判也要归一化。
  - Datum 指针（指向帧、存储行、参数）的生命周期和帧布局由内存主题定。
- **并发**：
  - `PhysicalPlan: Send + Sync`，统计改成原子量（52 处 `ATOMIC_`）。
  - 常量帧跨执行只读共享；PX worker 克隆 `Arc`。
  - 共享计划不可变以后，C++ 里潜在的数据竞争（column_conv）会在编译期暴露出来。
- **存储**：storage 的下推依赖 `RtExpr`/`EvalCtx`（29 个文件）。这些类型要放在 storage 能依赖的 crate 里：按 include 分层是 query 或 sql，都位于 storage 之下。eval 函数留在 sql，通过函数指针注入，不产生反向依赖。
- **分层**：`ObExpr` 声明在 query（`src/query/api/...`），实现在 sql（`src/sql/engine/expr/ob_expr.cpp`）。Rust 里要把它的方法搬进定义 `RtExpr` 的 crate。sql/pl/query 之外，`ObRawExpr` 只被 rootserver 的 4 个、observer 的 5 个文件引用，它们都在 sql 之上，没有问题。data_plane 里只有一个没用到的前置声明（`ob_table_param.h:29`），oblib 只在注释里提到。
- **解析器**：
  - `ItemType`（2,021 个值，有两份头文件副本）要生成为 `#[repr(i32)]`。
  - `TableItem::node_` 指向 ParseNode。如果解析器保留 C 版、走 FFI，这里就是一个裸指针边界，要求解析结果活过 resolve。
- **约定/编译**：
  - 错误类型 `Result<T, ObError>`。在 `panic=abort` 下，凡是会 panic 的结构（RefCell、越界）都等于崩溃。
  - 新增 `SLOT/PARITY(port)` 标记。
  - release 的 `codegen-units = 1` 对约 100 万行的 sql crate 很不利，需要编译时间主题评估。
- **PL**：PL resolver 用 raw expr（16 个文件里 453 次），通过 proxy 工厂建在上层工厂上。PL 单元保存运行期 `const ObExpr *`（`ob_sql_expression.h:139`），也适用 `Arc` 加 id 的计划表示。
- **裁判**：
  - 身份和共享语义不变，结果集不受影响。
  - 可能变化的只有内部顺序：3 处地址顺序迭代，可能影响 EXPLAIN 里生成视图的列顺序。
  - 内存相关的视图数值和报错时机取决于内存主题。

## 附录：复现命令（在 worktree 根目录执行）

```sh
grep -rno 'get_param_expr(' src | wc -l                                   # 2375
grep -rnE 'get_param_expr\s*\(\s*(const\s+)?int64_t' src | wc -l          # 39（声明/定义）
grep -rnE '\*\s*&\s*\w+\s*=\s*[^;]*get_param_expr\(' src | wc -l          # 16（绑定槽位）
grep -rnE 'get_param_expr\([^()]*\)\s*=[^=]' src | wc -l                  # 34（直接赋值）
grep -rnoP '(?<!const )\bOb\w*RawExpr\s*\*\s*&' src | wc -l               # 1459（槽位）
grep -rno 'reinterpret_cast<u\?int64_t>(' src/sql | wc -l                 # 121
grep -rhoE '(static|dynamic|reinterpret)_cast<\s*(const\s+)?Ob\w*RawExpr\s*\*?\s*&?\s*>' src --include='*.h' --include='*.cpp' --include='*.ipp' | wc -l  # 1350
grep -cE '^\s*REG_OP\(' src/sql/engine/expr/ob_expr_operator_factory.cpp  # 528
grep -cE '^REGISTER_OPERATOR\(' src/query/api/query/engine/ob_operator_reg.h  # 61
grep -c 'int generate_spec(' src/sql/code_generator/ob_static_engine_cg.h # 63
grep -rn 'CREATE_WITH_TEMP_CONTEXT' src/sql --include='*.cpp' | wc -l     # 7
grep -rnE 'ObRawExprFactory\s+\w+\s*\(' src/sql src/pl --include='*.cpp' | wc -l  # 33
grep -rno 'frames_\[' src --include='*.h' --include='*.cpp' --include='*.ipp' | wc -l  # 31
grep -rn 'get_operator_kit(' src --include='*.cpp' --include='*.h' | grep -v 'src/sql/engine/ob_exec_context\|src/sql/engine/ob_operator.cpp' | wc -l  # 28
grep -rno 'SMART_CALL\(_LARGE\)\?(' src/sql src/pl --include='*.cpp' --include='*.h' | wc -l  # 1072
grep -rnE 'static_cast<\s*(Ob|[A-Z])\w*\s*\*\s*>\s*\(\s*expr\.extra_info_\s*\)' src/sql/engine --include='*.cpp' | wc -l  # 31
grep -rnE 'const_cast<\s*(const\s+)?Ob\w*Spec\s*[*&]' src/sql/engine src/sql/das src/storage --include='*.cpp' --include='*.h' | wc -l  # 6
grep -c 'ATOMIC_' src/sql/engine/ob_physical_plan.cpp                      # 52
```

临时脚本的启发式（`/tmp/irsurvey-7d907-mz/`）：

- `class_hier.py`：解析 `class X : public Y {` 类头，计算传递子类数。
- `exprop_shape.py`：用花括号匹配取出类体，统计覆写和以 `_;` 结尾的成员。
- `param_expr_sites.py`、`ref_slot_funcs.py`：按行用正则给 `get_param_expr` 分类；槽位传参只按函数名匹配，所以是上界。
- `surface.py`、`sig_counts.py`：按类型 token 统计出现次数，以及参数表里带这些类型的函数定义。
- `eval_fns.py`：匹配 `EvalFunc` 和 `EvalBatchFunc` 两种签名的函数定义。
- `dir_sizes.py`、`manifest_units.py`：统计各目录的行数和 manifest 单元数。
