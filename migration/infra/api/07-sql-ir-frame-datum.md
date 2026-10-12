# 07 `sql-ir-frame-datum`：datum、object、帧模块、`query::ir_id`、`QueryArena`、`RtExpr`、`ExprOp`/`OpState`

> - **依据**：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）0.6、§2.6 R1–R9、§2.12 的 ObDatum/ObObj 两行、§2.13 的向下转型、§3 白名单第 9 项、§4 改名表的 `query`/`query::ir_id`/`sql::ir`/表达式模块/datum 模块/object 模块各行；`migration/infra/README.md` 的文档结构。基线 `7d907abfa`。
> - **事实来源**：`migration/design/survey/ir-graphs.md`、`memory.md`；审查 S-01、S-02、S-07、S-09、S-13、S-14、S-25、缺失规则 7/11（`soundness.md`），P-13、P-14、P-22、P-26、P-29、P-30（`process-consistency.md`），F18、F22（`concurrency-performance.md`），复核 C01、C02、C22（`verification.md`）。其余事实是本文作者只读源码得到的，给了 file:line；计数给了命令。
> - **状态**：草案，待两名审查者审查后冻结。与 RULEBOOK 不一致、需要人决定的地方都在第 8 节，修订已排进 `migration/infra/amendments.tsv`。
> - **一页摘要**：
>   1. **datum**：存储形态 `ObDatum` 仍是 12 字节、字节顺序同 C++（`r: DataRef` 在 0..8，`desc` 在 8..12）；`DataRef = { store: u32, off: u32 }`，**存储形态里不放内联值**（12 字节没有判别位，见 8.1/修订 A1）。8 字节以内的值内联在两种非存储形态里：按值传递的读视图 `ObDatumView<'a>` 和本地可写的 `ObDatumBuf<N>`（求值函数的结果参数就是它）。
>   2. **存储表 `StoreTable`**（帧模块）：DataRef 指向的全部内存（计划常量区、帧、参数帧、结果 arena、批次 arena、行存储块、钉住的 KV cache 块）都是表项；解析一律带边界检查，**整个 datum/object/帧模块只需约 3 处 unsafe**（`ObBitVector` 的 DST 构造）。
>   3. **求值**：`EvalFn = fn(&RtExpr, &mut ObEvalCtx, &mut ObDatumBuf) -> ObResult`。求值要 `&mut ObEvalCtx`（所以求值时没有存活视图），读用 `&self` 视图，写结果先写本地 `ObDatumBuf`，由帧模块在返回后落盘到帧（和 C++ 的"写到 res buf、再置 evaluated"等价）。字符串结果的"读参数、写结果缓冲"用 `get_str_res_mem_with` 闭包，帧模块内部处理重叠（C++ 用 MEMMOVE 的那些情况）。
>   4. **`ObEvalCtx` 与 `ObExecContext`**：存储表在 `ObExecContext` 里，构造 `ObEvalCtx` 时取出、析构时放回（闭包式）；sql 代码经 `ctx.exec_ctx::<ObExecContext>()` 向下转型拿到执行上下文（要求它 `'static`，见 8.2）。
>   5. **IR**：`query::ir_id` 五种 id（`NonZeroU32`）和分块的 `IdVec`；`sql::ir::QueryArena` 用 `IdVec` 存节点，截断和 `free_stmt` 都留墓碑、id 不复用。R1 对**节点类自身的方法**写不出来（`&self` 来自 qa，又要 `&mut qa`），本文给出可机械判定的两类译法并排了修订 A3。
>   6. **表达式算子**：每个 C++ 类（含中间类）一个零大小类型；`ExprOp` trait 在 sql 的表达式模块；`OpState` 的变体和 `impl ExprOp` 的转发全部由脚本生成；注册表保持 C++ 的 528+1 条注册顺序。ExtraInfo 的 enum 放 query（变体 struct 经 seam 表下移），ExprOpCtx 的 enum 放 sql，query/storage 只经 `ObExprOperatorCtxVirt` 调它的 6 个虚函数。

## 1. 范围

### 1.1 子模块与落点

| 子模块 | Rust 落点（crate::模块） | 文件 | §3 白名单 | 写的人 |
|---|---|---|---|---|
| datum | `oblib::common::datum::ob_datum` | `rust/port/oblib/src/common/datum/ob_datum.rs` | 第 9 项（实际 0 处 unsafe，不加 `#[allow(unsafe_code)]`） | 1d |
| object | `oblib::common::object::ob_object` | `rust/port/oblib/src/common/object/ob_object.rs` | 第 9 项（0 处） | 1d |
| 帧模块 | `query::engine::expr::frame`（子模块 `store`、`eval`、`frame_info`、`bitvec`） | `rust/port/query/src/engine/expr/frame/**` | 第 9 项（约 3 处，§4） | 1d |
| 向量格式 | `query::engine::vector::*` | `rust/port/query/src/engine/vector/*.rs`（单元原路径） | 不需要 | 1d |
| IR id | `query::ir_id` | `rust/port/query/src/ir_id.rs`（新文件，不是单元） | 不需要 | 1d |
| QueryArena | `sql::ir` | `rust/port/sql/src/ir/mod.rs`（新文件） | 不需要 | 1d |
| `ExprOp`、`OpState` 契约、注册表生成器 | `sql::engine::expr::expr_op`；生成物 `sql::generated::{expr_ops, op_state, expr_op_impls, expr_op_registry, expr_op_ctx, ir_enums, qa_slots}`、`query::generated::extra_info` | `rust/port/sql/src/engine/expr/expr_op.rs`；`rust/port/{sql,query}/src/generated/*.rs` | 不需要 | 1d（trait 和脚本）；生成物由脚本写 |

- crate 根由脚本生成的 `pub use` 导出上述公共名字，引用方写 `use oblib::ObDatum;`、`use query::{RtExpr, ObEvalCtx};`、`use sql::ir::QueryArena;`（RULEBOOK §4）。
- 白名单路径写进 `units.tsv` 时以上表为准：`rust/port/query/src/engine/expr/frame/**`（处置 `rewrite:frame`）、`rust/port/oblib/src/common/{datum,object}/**`（处置 `rewrite:datum`；object 模块沿用 §3 第 9 项给的处置名，不另起 `rewrite:object`）。

### 1.2 单元与处置

| 单元（代表文件） | C++ 规模 | 处置 | 说明（file:line） |
|---|---|---|---|
| `src/oblib/common/datum/ob_datum.h`（+`.cpp`） | 1032+204 行 | `rewrite:datum` | `ObDatumPtr`（`ob_datum.h:106-131`，20 个指针别名的 packed 联合体）、`ObDatumDesc`（`:134-165`）、`ObDatum`（`:169-415`，`STATIC_ASSERT` 在 `:1027`）、`ObDatumVector`（`:417-426`）、`ObDatumPayload<TC>`（`:428-500`）、`obj2datum/datum2obj/datum2datum`（`:541-735`）、`from_obj/to_obj/from_storage_datum/checksum`（`:737-1025`）；`.cpp` 有 `get_obj_datum_map_type`（`:26`）、`get_reserved_size`（`:86-124`）、`DEF_TO_STRING`（`:126-166`）、序列化（`:168-200`） |
| `src/oblib/common/object/ob_object.h`（+`.cpp`） | 3923+2374 行 | `rewrite:datum`（object 模块） | `ObObjMeta`（`ob_object.h:112`，布局和方法不变）、`ObObjPrintParams`（`:1191-1253`）、`ObObjValue`（`:1257-1283`）、`ObObj`（`:1290-1999`，deep copy `:1942-1944`、哈希 `:1962-1971`、成员 `:1990-1998`）、`ObObjParam`（`:3618`） |
| `src/oblib/common/object/ob_obj_funcs.h` | 2664 | `translate` | 每个类型的 print/hash/serialize 模板，照 2.2 的 `ObObj<'a>` API 逐行翻译；`hash_murmur` 等进金标准测试 |
| `ob_obj_compare.{h,cpp}`、`ob_obj_type.{h,cpp}` | 3958、2550 | `translate` | 只用 getter，不碰指针成员 |
| `src/query/api/query/engine/expr/ob_expr.h` | 1460 | `rewrite:frame` | `ObDatumMeta`（`:65-127`）、`ObEvalInfo`（`:130-158`）、`ObEvalCtx`（`:161-289`）、`ObDynReserveBuf`（`:294-317`）、`ObExpr`（`:342-714`）、`ObExprExtraInfoAccess`（`:716-725`）、`ObExprStrResAlloc`（`:730-748`）、`ObDatumObj/ObDatumObjParam/DatumParamStore`（`:750-897`）、`DATUM2STR/EXPR2STR/ROWEXPR2STR`（`:899-963`）、内联实现（`:965-1126`）、`ObExpr*` 的下标+1 序列化（`:1315-1360`）。单元目标 `rust/port/query/src/engine/expr/ob_expr.rs` 是桩：`pub use crate::engine::expr::frame::*;` |
| `src/sql/engine/expr/ob_expr.cpp` | 905 | `rewrite:frame` | 求值内核（`:47-104`、`:286-397`、`:569-761`）搬进帧模块；依赖 sql 的 `eval_question_mark_func`/`eval_assign_question_mark_func`（`:763-840`）和 `ObDatumObjParam::{from_objparam,to_objparam,construct_*}`（`:399-510`）按函数级重映射归 sql（落点由 `seams.tsv` 定，建议 `src/sql/engine/ob_physical_plan_ctx` 单元）。单元目标是桩 |
| `src/sql/engine/expr/ob_expr_frame_info.{h,cpp}` | 237+637 | `rewrite:frame` | `ObFrameInfo`（`.h:33`）、`ObExprFrameInfo`（`.h:65`，`assign` 在 `.cpp:35-193`，`alloc_frame` 在 `:231-279`）进帧模块；`pre_alloc_exec_memory`（`:195-213`）、`ObPreCalcExprFrameInfo::eval*`（`:418-`）、`ObTempExpr::{eval,row_to_frame,deep_copy}`、`ObTempExprCtx`（`.h:184`）依赖 `ObExecContext`/`ObPhysicalPlanCtx`，按函数级重映射归 sql 的自由函数（seam 定落点）。单元目标是桩 |
| `src/query/api/query/engine/vector/*.h`（12 个文件） | 1462 | `rewrite:frame` | `ObIVector`（`ob_i_vector.h:372-417`）、读写宏（`:36`、`:214`）、`VectorFormat`（`type_traits.h:27-36`）、四种格式的 base/format。seekdb 的表达式帧已不用这些格式（`ob_pushdown_aggregate_program.cpp:764-797` 整段 `#if 0`），用户是 storage 的 DDL 批（`src/storage/ddl/ob_ddl_*_vector.*`）、`ob_datum_reshape.cpp`、`ob_micro_block_checksum_helper.cpp` 和 sql 的 `ob_temp_column_store.*`。实现直接写在单元路径 |
| `src/share/vector/ob_bit_vector.h` | 852 | `translate`（形状由本文规定） | `ObBitVectorImpl<WordType>` 是无头柔性数组（`:45-196`），`to_bit_vector(void*)`（`:198-206`）。Rust：`#[repr(transparent)]` 包 `[u8]` 的 DST（2.3.4）；从字节切片构造的 3 个函数在帧模块（unsafe） |
| `src/share/vector/ob_eval_bound.h` | 64 | `translate` | `EvalBound`（`:28-63`），纯值类型 |
| `src/query/api/query/engine/expr/ob_i_expr_extra_info.h` | 约 40 | `generate:ir_enums` | 基类只有 `type_` 和 4 个纯虚（序列化 3 个、`deep_copy`）；Rust 是生成的 enum `ObIExprExtraInfo`（2.10） |
| `src/query/api/query/engine/expr/ob_expr_extra_info_factory.h` + `src/sql/engine/expr/ob_expr_extra_info_factory.cpp` | — | `generate:ir_enums` | `REG_EXTRA_INFO`（`.cpp:45-48`）35 条注册（`:78-112`），19 个子类 |
| `src/query/api/query/engine/expr/ob_expr_operator.h` | 2247 | `translate` | 基类数据 `ObExprOperator`（`:304-768`）照 2.9 写成数据 struct；中间类（`ObFuncExprOperator` `:1063` 等 10 个）的零大小类型由脚本生成，方法体按函数级重映射写在 sql；`ObExprOperatorCtx`（`:166-196`）变成生成的 enum（2.10） |
| `src/sql/engine/expr/ob_expr_operator.cpp` | — | `translate` | 基类默认实现译成 `base_<方法>` 自由函数（§2.13），供 `ExprOp` 的默认方法调用 |
| `src/sql/engine/expr/ob_expr_operator_factory.{h,cpp}` | — | `translate` | `register_expr_operators`（`.cpp:511-`）的函数体换成 `include!("generated/expr_op_registry.rs")`（§2.10） |
| 573 个算子类所在单元（`src/sql/engine/expr/ob_expr_*.{h,cpp}` 约 480 个单元，另有 query 头文件里的 `ObExprBM25`、`ObExprInOrNotIn`、`ObExprArrayMapCommon`） | — | `translate` | 每个类写固有方法（2.9.2）；数据成员写成 `<类名>State`；`ObExprBM25` 等声明在 query 头文件的类，其方法体在 sql，按函数级重映射 |
| `src/sql/resolver/expr/ob_raw_expr.h`（+`.cpp`） | 约 5200 行 | `translate`，其中 `ObRawExprFactory`（`:4996-5121`）类级替换为 `sql::ir::QueryArena` | 节点方法按 2.8.4 的两类译法 |
| `src/sql/resolver/ob_stmt.{h,cpp}` | — | `translate`，`ObStmtFactory`（`ob_stmt.cpp:207-253` 的 free list）类级替换为 `QueryArena` | |
| `src/sql/optimizer/ob_log_operator_factory.{h,cpp}` | — | `translate`，`ObLogOperatorFactory`（`.h:267-277`）类级替换为 `QueryArena`；`LOG_OP_DEF` 表（37 项）照常翻译 | |
| `src/sql/engine/ob_exec_context.{h,cpp}` | 1105+ | `translate` | `frames_`/`frame_cnt_`（`.h:874-875`）、`eval_res_allocator_`/`eval_tmp_allocator_`（`:884-885`）、`tmp_alloc_used_`（`:926`）合成一个字段 `store_table_: Option<StoreTable>`；`get_expr_factory()`/`get_stmt_factory()`（`.cpp:642-664`，235 处调用）删除，调用点改用传入的 `qa`；实现 `ObExecContextVirt`（2.4.1） |
| `src/query/api/query/engine/basic/ob_pushdown_filter.h` + `src/sql/engine/basic/ob_pushdown_filter.cpp` | 1593+3017 | `translate` | trait `PushdownFilter` 的方法清单由 `seams.tsv` 给出，本文 2.11 给候选 |
| `src/sql/code_generator/ob_static_engine_expr_cg.{h,cpp}` | — | `translate` | 帧布局（`.cpp:958-1005`）照抄，常量帧（`:1063-1105`）改用 2.5 的构建 API |

### 1.3 规模（受影响的调用点）

命令在 worktree 根目录执行，按"含该模式的行"计，是上限估计；`get_int(`、`set_int(` 等同名方法也算进了 ObObj 的调用。

| 模式 | 行数 | 分布 | 命令 |
|---|---|---|---|
| `args_[i]->eval(` | 306 | sql 298、query 8 | `rg 'args_\[[^]]+\]->eval\(' src` |
| `eval_param_value(` | 136 | sql 134 | `rg 'eval_param_value\(' src` |
| `eval_batch(` | 143 | sql 136、storage 5 | `rg '(->\|\.)eval_batch\(' src` |
| `locate_*`（expr_datum/datum_for_write/batch_datums/datums_for_update/expr_datumvector/param_datum） | 207/139/157/37/156/98 | storage 共 64 | 逐个 `rg` |
| `clear_evaluated_flag(` / `get_eval_info(` / `get_evaluated_flags(` | 358/115/114 | | |
| `BatchInfoScopeGuard` / `set_batch_idx(` | 199/222 | storage 13/21 | |
| `TempAllocGuard` / `ObExprStrResAlloc` / `get_str_res_mem(` | 370/50/91 | | `rg '<模式>' src -g '*.{h,cpp,ipp}' -g '!ob_expr.h'` |
| `ctx.exec_ctx_.xxx(` | 599 | 其中 `get_my_session` 242、`get_physical_plan_ctx` 96、`get_allocator` 70 | `rg -o 'exec_ctx_\.(get_\w+\|\w+)\(' src/sql src/storage src/pl src/query` |
| datum 的 `.ptr_`/`->ptr_` | 722（写 173） | storage 367、sql 210 | 见附录 A 的脚本 |
| `pack_` / datum 的 `len_` | 183 / 约 875 | | 同上（`len_` 的模式按变量名启发式） |
| `set_string(` / `get_string(` | 496 / 1253 | | |
| `from_obj(` / `to_obj(` / `from_storage_datum(` | 65 / 84 / 32 | | |
| `ObDatum` 局部变量声明 | 107 | sql 73、storage 23 | `^\s*(common::)?ObDatum\s+\w+\s*(;\|\(\|\[)` |
| `TempAllocGuard` 之后 40 行内又求值的 | 66 / 370 | | 附录 A |
| `ObBitVector`/`to_bit_vector(` | 608+112+57+27 行 | sql/storage/query/share | |
| `static_cast<…*>(expr.extra_info_)` | 34 | sql/engine | |
| `ObIVector` 及四种格式 | 243 行、58 个文件 | storage 39 个文件 | |
| `get_expr_factory()`/`get_stmt_factory()` | 235 | | |

### 1.4 不在本组范围

- `ObStorageDatum`（`src/data_plane/api/data_plane/blocksstable/ob_storage_datum.h:38-73`，12 字节 datum + 4 字节保留 + 40 字节本地缓冲，`ptr_ = buf_` 自指）由 data_plane 的译者实现：用本文的 `ObDatumBuf<40>`（Local 形态对应 `is_local_buf()`）或同样的两态自己写，不另立规则。
- 行存储（`ObChunkDatumStore::StoredRow`，`ob_chunk_datum_store.h:104-166`）按 RULEBOOK 0.9 的字节视图翻译；本文只规定它的 datum 怎么存 DataRef（2.3.3、3.3）。
- `ObSqlExpression`、旧引擎的 `calc_result*`/`call`/`eval` 路径照常翻译，本文只给 `ExprOp` 上的签名。
- `ob_serializable_function.h`（函数指针按地址序列化）：计划不再序列化（0.6），`RtExpr` 不带 `ser_*` 成员；剩下的两处用户（`ob_datum_funcs`、`ob_pushdown_topn_filter_msg.h`）由清单处理。

## 2. Rust API

代码块里的函数体一律写成 `{ todo!() }`（与 01 的文档相同），签名就是冻结的接口。每项后面的注释给对应的 C++ 名字和位置。

### 2.1 datum 模块（`oblib::common::datum::ob_datum`）

#### 2.1.1 枚举与常量

```rust
use crate::prelude::*;

pub const OB_DATUM_SIZE: usize = 12;                                    // ob_datum.h:72

/// C++ enum ObObjDatumMapType : uint8_t（ob_datum.h:75-87），§2.12 的枚举新类型。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct ObObjDatumMapType(pub u8);
impl ObObjDatumMapType {
    pub const OBJ_DATUM_NULL: Self = Self(0);
    pub const OBJ_DATUM_STRING: Self = Self(1);
    pub const OBJ_DATUM_NUMBER: Self = Self(2);
    pub const OBJ_DATUM_8BYTE_DATA: Self = Self(3);
    pub const OBJ_DATUM_4BYTE_DATA: Self = Self(4);
    pub const OBJ_DATUM_1BYTE_DATA: Self = Self(5);
    pub const OBJ_DATUM_4BYTE_LEN_DATA: Self = Self(6);
    pub const OBJ_DATUM_2BYTE_LEN_DATA: Self = Self(7);
    pub const OBJ_DATUM_FULL: Self = Self(8);
    pub const OBJ_DATUM_DECIMALINT: Self = Self(9);
    pub const OBJ_DATUM_MAPPING_MAX: Self = Self(10);
}
pub const OBJ_DATUM_NULL: ObObjDatumMapType = ObObjDatumMapType::OBJ_DATUM_NULL; // 其余 10 个同样写模块级常量

/// C++ enum DatumReserveSize（ob_datum.h:91-103）。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct DatumReserveSize(pub u32);
impl DatumReserveSize {
    pub const OBJ_DATUM_NULL_RES_SIZE: Self = Self(0);
    pub const OBJ_DATUM_1BYTE_DATA_RES_SIZE: Self = Self(1);
    pub const OBJ_DATUM_4BYTE_DATA_RES_SIZE: Self = Self(4);
    pub const OBJ_DATUM_8BYTE_DATA_RES_SIZE: Self = Self(8);
    pub const OBJ_DATUM_2BYTE_LEN_DATA_RES_SIZE: Self = Self(10);
    pub const OBJ_DATUM_4BYTE_LEN_DATA_RES_SIZE: Self = Self(12);
    pub const OBJ_DATUM_FULL_DATA_RES_SIZE: Self = Self(16);
    pub const OBJ_DATUM_NUMBER_RES_SIZE: Self = Self(40);
    pub const OBJ_DATUM_DECIMALINT_MAX_RES_SIZE: Self = Self(64);
    pub const OBJ_DATUM_STRING_RES_SIZE: Self = Self(128);
    pub const OBJ_DATUM_MAX_RES_SIZE: Self = Self(128);
}
pub const OBJ_DATUM_NUMBER_RES_SIZE: DatumReserveSize = DatumReserveSize::OBJ_DATUM_NUMBER_RES_SIZE; // 其余同

/// C++ ObDatumDesc::FlagType（ob_datum.h:135-140）。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct FlagType(pub u32);
impl FlagType {
    pub const NONE: Self = Self(0);
    pub const OUTROW: Self = Self(1);
    pub const EXT: Self = Self(2);
    pub const HAS_LOB_HEADER: Self = Self(3);
}
```

#### 2.1.2 `DataRef`：取代 `ObDatumPtr`

```rust
/// 取代 C++ ObDatumPtr（ob_datum.h:106-131）的 ptr_ 和 19 个类型化别名。8 字节，按值传递。
/// 编码（3.1）：
///   store == 0            → NULL（C++ ptr_ == NULL；只允许 len_ == 0 或 null datum）
///   store == u32::MAX     → SELF：off 相对"包含它的缓冲"（行存储的行/块、序列化缓冲），
///                           即 C++ 的 unswizzled 形态 ptr_ = (char *)pos（ob_chunk_datum_store.h:90）
///   其余                  → 存储表的表项：低 20 位是槽号（≥1），高 12 位是代号
/// off 是表项内的字节偏移（一个表项 ≤ 4 GiB）。
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct DataRef {
    pub store: u32,
    pub off: u32,
}
impl DataRef {
    pub const NULL: DataRef = DataRef { store: 0, off: 0 };
    pub const SELF_STORE: u32 = u32::MAX;
    pub const fn new(store: u32, off: u32) -> DataRef { todo!() }
    /// C++ unswizzling：ptr_ = reinterpret_cast<const char *>(pos)
    pub const fn self_rel(off: u32) -> DataRef { todo!() }
    pub const fn is_null(self) -> bool { todo!() }
    pub const fn is_self_rel(self) -> bool { todo!() }
    /// C++ ptr_ + n；off 溢出返回 OB_ERR_UNEXPECTED
    pub fn add(self, n: u32) -> ObResult<DataRef> { todo!() }
    /// 打印用（C++ 打印 %p 的地方）和 ObDynReserveBuf::mem_ 的落盘编码：store 在低 32 位
    pub const fn to_bits(self) -> u64 { todo!() }
    pub const fn from_bits(v: u64) -> DataRef { todo!() }
}
```

#### 2.1.3 `ObDatumDesc` 与存储形态 `ObDatum`

```rust
/// C++ ObDatumDesc（ob_datum.h:134-165）：len_:29 | flag_:2 | null_:1，从低位起分配（§2.12 ob_bitfield!）。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, Default)]
pub struct ObDatumDesc { pack_: u32 }
impl ObDatumDesc {
    pub const fn pack_(self) -> u32 { todo!() }          // C++ 直接读写 pack_ 的 183 处
    pub fn set_pack_(&mut self, v: u32) { todo!() }
    pub const fn len_(self) -> u32 { todo!() }           // bits 0..29
    pub fn set_len_(&mut self, v: u32) { todo!() }       // 按位域截断成 29 位，同 C++ 赋值
    pub const fn flag_(self) -> FlagType { todo!() }     // bits 29..31
    pub fn set_flag_(&mut self, v: FlagType) { todo!() }
    pub const fn null_(self) -> u32 { todo!() }          // bit 31
    pub fn set_null_(&mut self, v: u32) { todo!() }
    pub fn set_none(&mut self) { todo!() }               // :152 null_=0, flag_=NONE；len_ 不动
    pub fn set_null(&mut self) { todo!() }               // :153 len_=0, null_=1, flag_=NONE
    pub const fn is_null(self) -> bool { todo!() }
    pub fn set_ext(&mut self) { todo!() }                // :156 len_=16, null_=0, flag_=EXT
    pub const fn is_ext(self) -> bool { todo!() }
    pub fn set_outrow(&mut self) { todo!() }             // :158
    pub const fn is_outrow(self) -> bool { todo!() }
    pub fn set_flag(&mut self, f: FlagType) { todo!() }  // :161
    pub fn set_has_lob_header(&mut self) { todo!() }
    pub const fn has_lob_header(self) -> bool { todo!() }
    pub fn set_flag_none(&mut self) { todo!() }
}

/// 存储形态（帧、批次 datum 数组、行存储的 cells、计划常量帧、DRB 头里的 datum 记录）。
/// 字节布局与 C++ 相同：r（C++ ptr_）在 0..8，desc（C++ pack_）在 8..12；对齐 1。
/// 只存 DataRef，不存地址，也不存内联值（8.1、修订 A1）。按值复制（Copy），不借用任何东西。
#[repr(C, packed)]
#[derive(Clone, Copy, Default)]
pub struct ObDatum { r: DataRef, desc: ObDatumDesc }
const _: () = assert!(core::mem::size_of::<ObDatum>() == 12);          // STATIC_ASSERT（ob_datum.h:1027）

impl ObDatum {
    pub const fn new(r: DataRef, len: u32, null: bool) -> ObDatum { todo!() } // ObDatum(ptr, len, null)（:178-182）
    pub fn reset(&mut self) { todo!() }                                  // :184
    pub const fn r(self) -> DataRef { todo!() }                          // 读 ptr_
    pub fn set_r(&mut self, r: DataRef) { todo!() }                      // 写 ptr_（173 处里的帧外写入，如行存储）
    pub const fn desc(self) -> ObDatumDesc { todo!() }
    pub fn set_desc(&mut self, d: ObDatumDesc) { todo!() }
    // 以下逐个转发到 desc：pack_/set_pack_/len_/set_len_/flag_/null_/is_null/set_null/set_none/
    // set_ext/is_ext/set_outrow/is_outrow/set_flag/set_has_lob_header/has_lob_header/set_flag_none
    pub const fn is_null(self) -> bool { todo!() }
    pub const fn len_(self) -> u32 { todo!() }
    pub fn set_null(&mut self) { todo!() }
    /// C++ set_string(ptr, len)/set_string(ObString)（:350-351）在存储形态上的浅写入：pack_ = len
    pub fn set_string(&mut self, r: DataRef, len: u32) { todo!() }
    pub fn set_datum(&mut self, other: ObDatum) { todo!() }              // :363
    pub const fn get_deep_copy_size(self) -> i64 { todo!() }             // :364
    /// 帧和行存储里按值读写 12 字节记录
    pub fn to_bytes(self) -> [u8; 12] { todo!() }
    pub fn from_bytes(b: [u8; 12]) -> ObDatum { todo!() }
    pub fn read_at(buf: &[u8], off: usize) -> ObResult<ObDatum> { todo!() }       // 越界 OB_ERR_UNEXPECTED
    pub fn write_at(self, buf: &mut [u8], off: usize) -> ObResult { todo!() }
    pub fn get_obj_datum_map_type(type_: ObObjType) -> ObObjDatumMapType { todo!() }          // .cpp:26
    pub fn get_reserved_size(type_: ObObjDatumMapType, prec: i16 /* = PRECISION_UNKNOWN_YET */) -> u32 { todo!() } // .cpp:86-124
}

/// C++ ObDatumVector（ob_datum.h:417-426）：批次 datum 数组的句柄。datums_ 指向第 0 个 ObDatum 记录。
#[derive(Clone, Copy, Default)]
pub struct ObDatumVector { pub datums_: DataRef, pub mask_: u64 }
impl ObDatumVector {
    pub fn at(&self, i: i64) -> DataRef { todo!() }    // datums_ + 12 * (mask_ & i)：返回记录的位置（ObDatum*）
    pub fn set_batch(&mut self, is: bool) { todo!() }
    pub fn is_batch(&self) -> bool { todo!() }
}
```

#### 2.1.4 读视图 `ObDatumView<'a>`：取代 `const ObDatum &`

```rust
/// RULEBOOK §2.12：const ObDatum & 参数和局部变量一律是 ObDatumView<'a>，按值传递。
/// data 恰好是 len_ 个字节（null 时为空）；r 是来源的 DataRef，视图不来自存储表时为 DataRef::NULL。
/// 视图不可写；写入经拥有者（帧：ObEvalCtx；本地：ObDatumBuf；行：行存储的视图）。
#[derive(Clone, Copy)]
pub struct ObDatumView<'a> { desc: ObDatumDesc, r: DataRef, data: &'a [u8] }

impl<'a> ObDatumView<'a> {
    /// 只由帧模块、datum 模块和行存储视图调用（解析 DataRef 之后）；data.len() 必须等于 desc.len_()
    pub fn from_parts(desc: ObDatumDesc, r: DataRef, data: &'a [u8]) -> ObResult<ObDatumView<'a>> { todo!() }
    /// 局部临时值：非 null，len_ = bytes.len()，r = NULL（C++ `d.ptr_ = (char*)&v; d.pack_ = 8;`）
    pub fn from_bytes(bytes: &'a [u8]) -> ObDatumView<'a> { todo!() }
    pub fn null() -> ObDatumView<'static> { todo!() }
    // —— 描述 ——
    pub fn desc(self) -> ObDatumDesc { todo!() }
    pub fn r(self) -> DataRef { todo!() }
    pub fn is_null(self) -> bool { todo!() }
    pub fn len_(self) -> u32 { todo!() }
    pub fn pack_(self) -> u32 { todo!() }
    pub fn is_ext(self) -> bool { todo!() }
    pub fn is_outrow(self) -> bool { todo!() }
    pub fn has_lob_header(self) -> bool { todo!() }
    pub fn get_int_bytes(self) -> i32 { todo!() }                         // :285
    // —— 字节与浅切片（C++ 的 ptr_ 指针运算）——
    pub fn bytes(self) -> &'a [u8] { todo!() }                            // ptr_[0..len_)
    pub fn sub(self, start: u32, len: u32) -> ObResult<ObDatumView<'a>> { todo!() } // ptr_ + start；越界 OB_ERR_UNEXPECTED
    pub fn sub_of(self, part: &[u8]) -> Option<ObDatumView<'a>> { todo!() }         // part 必须落在 data 里（按地址判定）
    // —— 定长读取（ob_datum.h:217-285）。len_ 小于类型宽度时只读 len_ 个字节、其余补 0（3.2，FIXED(port)）——
    pub fn get_bool(self) -> bool { todo!() }
    pub fn is_false(self) -> bool { todo!() }
    pub fn is_true(self) -> bool { todo!() }
    pub fn get_int8(self) -> i8 { todo!() }
    pub fn get_tinyint(self) -> i8 { todo!() }
    pub fn get_smallint(self) -> i16 { todo!() }
    pub fn get_mediumint(self) -> i32 { todo!() }
    pub fn get_int32(self) -> i32 { todo!() }
    pub fn get_int(self) -> i64 { todo!() }
    pub fn get_uint8(self) -> u8 { todo!() }
    pub fn get_utinyint(self) -> u8 { todo!() }
    pub fn get_usmallint(self) -> u16 { todo!() }
    pub fn get_umediumint(self) -> u32 { todo!() }
    pub fn get_uint32(self) -> u32 { todo!() }
    pub fn get_uint64(self) -> u64 { todo!() }
    pub fn get_uint(self) -> u64 { todo!() }
    pub fn get_float(self) -> f32 { todo!() }
    pub fn get_double(self) -> f64 { todo!() }
    pub fn get_ufloat(self) -> f32 { todo!() }
    pub fn get_udouble(self) -> f64 { todo!() }
    pub fn get_unknown(self) -> i64 { todo!() }
    pub fn get_bit(self) -> u64 { todo!() }
    pub fn get_enum(self) -> u64 { todo!() }
    pub fn get_set(self) -> u64 { todo!() }
    pub fn get_enumset(self) -> u64 { todo!() }
    pub fn get_datetime(self) -> i64 { todo!() }
    pub fn get_mysql_datetime(self) -> ObMySQLDateTime { todo!() }
    pub fn get_timestamp(self) -> i64 { todo!() }
    pub fn get_date(self) -> i32 { todo!() }
    pub fn get_mysql_date(self) -> ObMySQLDate { todo!() }
    pub fn get_time(self) -> i64 { todo!() }
    pub fn get_year(self) -> u8 { todo!() }
    pub fn get_otimestamp_tiny(self) -> ObOTimestampData { todo!() }       // :258-264
    // —— 变长和结构化读取 ——
    /// C++ 返回 const ObCompactNumber &（:247）；Rust 按值拷出（desc + 最多 9 个 digit），不要求对齐
    pub fn get_number(self) -> number::ObCompactNumber { todo!() }
    pub fn get_number_desc(self) -> number::ObNumberDesc { todo!() }
    pub fn get_number_digits(self) -> [u32; 9] { todo!() }                // 有效个数 = desc.len_
    pub fn get_string(self) -> &'a [u8] { todo!() }                        // ObString(len_, ptr_)（:265）
    pub fn get_json(self) -> &'a [u8] { todo!() }
    pub fn get_enumset_inner(self, inner: &mut ObEnumSetInnerValue<'a>) -> ObResult { todo!() } // :267-271
    pub fn get_mem_lob(self, loc: &mut ObLobLocatorV2<'a>) { todo!() }     // :272-275
    pub fn get_lob_data(self) -> &'a [u8] { todo!() }                      // const ObLobCommon &：字节视图（0.9）
    pub fn get_decimal_int(self) -> &'a [u8] { todo!() }                   // const ObDecimalInt *
    pub fn get_decimal_int32(self) -> i32 { todo!() }
    pub fn get_decimal_int64(self) -> i64 { todo!() }
    pub fn get_decimal_int128(self) -> i128 { todo!() }
    pub fn get_decimal_int256(self) -> int256_t { todo!() }                // 06 的宽整数
    pub fn get_decimal_int512(self) -> int512_t { todo!() }
    // —— OBJ_DATUM_FULL（ext）——
    pub fn get_ext(self) -> i64 { todo!() }                                // extend_obj_->get_ext()（:241）
    pub fn is_min(self) -> bool { todo!() }
    pub fn is_max(self) -> bool { todo!() }
    pub fn is_nop(self) -> bool { todo!() }
    pub fn is_null_or_nop(self) -> bool { todo!() }
    // —— 其他 ——
    pub fn binary_equal(r: ObDatumView<'_>, l: ObDatumView<'_>) -> bool { todo!() }   // :185-198
    pub fn checksum(self, current: i64) -> i64 { todo!() }                  // :796-803
    pub fn to_obj(self, obj: &mut ObObj<'a>, meta: &ObObjMeta, map_type: ObObjDatumMapType) -> ObResult { todo!() } // :890-923
    pub fn to_obj_2(self, obj: &mut ObObj<'a>, meta: &ObObjMeta) -> ObResult { todo!() }                           // :930-1025
    pub fn deep_copy_in<'q>(self, arena: &'q Arena) -> ObResult<ObDatumView<'q>> { todo!() } // deep_copy(src, alloc)（:381-398）的"视图 → arena 视图"
}
impl ObToString for ObDatumView<'_> { /* DEF_TO_STRING(ObDatum)（.cpp:126-166）；%p 处打印 r.to_bits() */ }
impl<'de> ObDeserialize<'de> for ObDatumView<'de> { /* OB_DEF_DESERIALIZE(ObDatum)（.cpp:181-192）：零拷贝指向 buf */ }
impl ObSerialize for ObDatumView<'_> { /* OB_DEF_SERIALIZE/SERIALIZE_SIZE（.cpp:168-200） */ }
```

C++ `ObDatumPayload<TC>::get(d)`（`ob_datum.h:428-500`，库外只有 `src/share/datum/ob_datum_cmp_func_def.h` 的 2 处模板用到）按 TC 展开成上面对应的 getter；模板上下文由译者照 §2.13 的模板规则写成 trait。

#### 2.1.5 本地可写 datum `ObDatumBuf<N>`：取代"局部 ObDatum + 局部缓冲"，也是求值函数的结果参数

```rust
/// 可写的拥有型 datum：值 ≤ N 字节时内联在本地缓冲（RULEBOOK 0.6 说的"内联值"放在这里），
/// 也可以浅指向存储表（Ref），或在堆上持有大于 N 的拷贝（Heap）。不借用任何东西。
/// N 默认 128 = OBJ_DATUM_MAX_RES_SIZE：覆盖所有定长类型（最大 decimal int 64 字节）和短字符串。
pub struct ObDatumBuf<const N: usize = 128> { /* desc: ObDatumDesc, payload: Local([u8; N]) | Ref(DataRef) | Heap(Vec<u8>) */ }

impl<const N: usize> ObDatumBuf<N> {
    pub const fn new() -> ObDatumBuf<N> { todo!() }       // C++ ObDatum()：pack_ = 0（len 0、非 null）、ptr NULL
    pub fn reset(&mut self) { todo!() }
    // —— desc：同 ObDatum ——
    pub fn is_null(&self) -> bool { todo!() }
    pub fn set_null(&mut self) { todo!() }
    pub fn set_none(&mut self) { todo!() }
    pub fn len_(&self) -> u32 { todo!() }
    pub fn pack_(&self) -> u32 { todo!() }
    pub fn set_pack(&mut self, len: i64) { todo!() }          // :535-539
    pub fn set_ext(&mut self) { todo!() }
    pub fn set_outrow(&mut self) { todo!() }
    pub fn set_flag(&mut self, f: FlagType) { todo!() }
    pub fn set_has_lob_header(&mut self) { todo!() }
    pub fn set_flag_none(&mut self) { todo!() }
    // —— 定长写入：C++ 写进 ptr_ 所指的预留区并设 pack_（:294-334、:359-362）；这里写进本地缓冲 ——
    pub fn set_int(&mut self, v: i64) { todo!() }
    pub fn set_int32(&mut self, v: i32) { todo!() }
    pub fn set_uint(&mut self, v: u64) { todo!() }
    pub fn set_uint32(&mut self, v: u32) { todo!() }
    pub fn set_bit(&mut self, v: u64) { todo!() }
    pub fn set_bool(&mut self, v: bool) { todo!() }
    pub fn set_true(&mut self) { todo!() }
    pub fn set_false(&mut self) { todo!() }
    pub fn set_float(&mut self, v: f32) { todo!() }
    pub fn set_double(&mut self, v: f64) { todo!() }
    pub fn set_enum(&mut self, v: u64) { todo!() }
    pub fn set_set(&mut self, v: u64) { todo!() }
    pub fn set_datetime(&mut self, v: i64) { todo!() }
    pub fn set_timestamp(&mut self, v: i64) { todo!() }
    pub fn set_otimestamp_tiny(&mut self, v: &ObOTimestampData) { todo!() }
    pub fn set_time(&mut self, v: i64) { todo!() }
    pub fn set_otimestamp(&mut self, v: &ObOTimestampData) { todo!() }                   // :321-322
    pub fn set_otimestamp_int64_t_uint16_t(&mut self, time_us: i64, time_desc: u16) { todo!() } // :323-327，不设 pack_（3.2）
    pub fn set_date(&mut self, v: i32) { todo!() }
    pub fn set_year(&mut self, v: i8) { todo!() }
    pub fn set_mysql_date(&mut self, v: ObMySQLDate) { todo!() }
    pub fn set_mysql_datetime(&mut self, v: ObMySQLDateTime) { todo!() }
    pub fn set_number(&mut self, num: &number::ObNumber) { todo!() }                      // :502-508
    pub fn set_number_compact_number(&mut self, cnum: &number::ObCompactNumber) { todo!() }  // :510-514
    pub fn set_decimal_int(&mut self, bytes: &[u8]) -> ObResult { todo!() }              // :522-526，> N 时 OB_ERR_UNEXPECTED
    pub fn set_decimal_int_t<T: DecimalIntRaw>(&mut self, v: &T) { todo!() }             // template set_decimal_int(const T&)（:344-349）；DecimalIntRaw 是 06 给 int32/64/128/256/512 的字节转换 trait
    // —— 浅写入：指向存储表（C++ 存别处的 ptr_）——
    pub fn set_string(&mut self, r: DataRef, len: u32) { todo!() }                        // :350-351 两个重载合一
    pub fn set_string_view(&mut self, v: ObDatumView<'_>) -> ObResult { todo!() }         // v.r 非 NULL 时浅指，否则按 set_string_copy
    pub fn set_enumset_inner(&mut self, r: DataRef, len: u32) { todo!() }
    pub fn set_lob_data(&mut self, r: DataRef, len: i64) { todo!() }                      // :354-358
    pub fn set_number_shallow(&mut self, r: DataRef, len: u32) { todo!() }                // :516-520
    pub fn set_decimal_int_shallow(&mut self, r: DataRef, len: i32) { todo!() }           // :528-532
    pub fn set_datum(&mut self, d: ObDatum) { todo!() }                                   // :363（浅）
    // —— 拷贝写入 ——
    pub fn set_string_copy(&mut self, bytes: &[u8]) -> ObResult { todo!() }   // ≤ N 内联；否则 Heap（try_reserve，失败 4013）
    pub fn deep_copy_view(&mut self, src: ObDatumView<'_>) -> ObResult { todo!() }
    pub fn from_obj(&mut self, obj: &ObObj<'_>, map_type: ObObjDatumMapType) -> ObResult { todo!() } // :737-761
    pub fn from_obj_1(&mut self, obj: &ObObj<'_>) -> ObResult { todo!() }                          // :805-888
    pub fn from_storage_datum(&mut self, src: ObDatumView<'_>, map_type: ObObjDatumMapType, need_copy: bool) -> ObResult { todo!() } // :763-794
    // —— 读 ——
    pub fn view_local(&self) -> Option<ObDatumView<'_>> { todo!() }   // Local/Heap/null；Ref 形态返回 None（经存储表 view_buf 读）
    pub fn as_ref_datum(&self) -> Option<ObDatum> { todo!() }         // Ref 形态的存储形态
}
```

`from_obj` 的字符串类（`OBJ_DATUM_STRING`）在 C++ 里浅指 ObObj 的内存（`ob_datum.h:559-567`）；ObObj 的字节不在存储表里，所以这里拷贝（≤ N 内联，否则 Heap）。结果字节不变，只多一次拷贝（3.4）。

#### 2.1.6 C++ 重载到 Rust 名的映射（写进 `overloads.tsv` 的覆盖列）

| C++ | Rust |
|---|---|
| `ObDatum::to_obj(ObObj&, const ObObjMeta&, ObObjDatumMapType)`（:213）/ `to_obj(ObObj&, const ObObjMeta&)`（:214） | `to_obj` / `to_obj_2` |
| `from_obj(const ObObj&, ObObjDatumMapType)`（:206）/ `from_obj(const ObObj&)`（:209） | `from_obj` / `from_obj_1` |
| `set_number(const ObNumber&)`（:337）/ `set_number(const ObCompactNumber&)`（:339） | `set_number` / `set_number_compact_number` |
| `set_otimestamp(const ObOTimestampData&)`（:321）/ `set_otimestamp(int64_t, uint16_t)`（:323） | `set_otimestamp` / `set_otimestamp_int64_t_uint16_t` |
| `set_string(const ObString&)`（:350）/ `set_string(const char*, uint32_t)`（:351） | 一律 `set_string(DataRef, u32)`；来源是视图或视图的子切片时 `set_string_view`；来源是局部或临时内存时 `set_string_copy` |
| `set_decimal_int(const ObDecimalInt*, int32_t)`（:342）/ `template set_decimal_int(const T&)`（:345） | `set_decimal_int` / `set_decimal_int_t` |
| `deep_copy(src, buf, max_size, pos)`（:365）/ `deep_copy(src, alloc)`（:381） | 帧内：`ObEvalCtx::deep_copy_datum`；本地：`ObDatumBuf::deep_copy_view`；arena：`ObDatumView::deep_copy_in`；行存储：行存储视图的写函数 |

### 2.2 object 模块（`oblib::common::object::ob_object`）

```rust
/// ObObjMeta（ob_object.h:112）：4 字节，字段和全部方法照抄，不在本文重复。

/// RULEBOOK §2.12 的借用视图。C++ 的 16 字节布局（meta_ 4 + val_len_ 联合 4 + v_ 8，:1990-1998）
/// 拆成：u_（val_len_/interval_fractional_/nmb_desc_/time_ctx_ 的原始位）、v_（标量的原始位，
/// 含 ext_/ptr_ 存的句柄值）、p_（指针成员 string_/nmb_digits_/lob_/lob_locator_v2_/decimal_int_ 的字节）。
/// 数字的 digit 也用字节（u32 小端序列），不用 &[u32]（修订 A6）。
#[derive(Clone, Copy)]
pub struct ObObj<'a> { pub meta_: ObObjMeta, u_: u32, v_: u64, p_: &'a [u8] }

/// 拥有型（schema 默认值、计划、会话变量、PL 包变量）。Clone 是深拷贝，不 derive Copy。
#[derive(Clone)]
pub struct ObObjOwned { pub meta_: ObObjMeta, u_: u32, v_: u64, p_: Box<[u8]> }

impl<'a> ObObj<'a> {
    pub const MIN_OBJECT_VALUE: i64 = /* UINT64_MAX - 2 */ -3;    // :1294-1295
    pub const MAX_OBJECT_VALUE: i64 = -2;
    pub fn new() -> ObObj<'static> { todo!() }                    // ObObj()：null
    // —— 联合体的原始位 ——
    pub fn val_len_(&self) -> i32 { todo!() }
    pub fn set_val_len_(&mut self, v: i32) { todo!() }
    pub fn nmb_desc_(&self) -> number::ObNumberDesc { todo!() }
    pub fn time_ctx_(&self) -> UnionTZCtx { todo!() }               // ObOTimestampData::UnionTZCtx（嵌套类，Rust 名以 types.tsv 为准）
    pub fn v_bits(&self) -> u64 { todo!() }                      // ObObjValue 的标量成员（int64_/double_ 等按位）
    // —— 规则：C++ 的每个 get_xxx/set_xxx/is_xxx 同名保留 ——
    //    标量 getter 返回值；指针型 getter 返回 &'a [u8] 或 'a 视图：
    pub fn get_int(&self) -> i64 { todo!() }
    pub fn get_string(&self) -> &'a [u8] { todo!() }             // ObString(val_len_, v_.string_)
    pub fn get_number(&self) -> number::ObCompactNumber { todo!() } // 按值拷出
    pub fn get_decimal_int(&self) -> &'a [u8] { todo!() }
    pub fn get_ext(&self) -> i64 { todo!() }                      // ext_：PL 复合值等的句柄（由 PL 模块定义含义）
    //    标量 setter 取值；指针型 setter 取 &'a [u8]（浅，与 C++ 相同）：
    pub fn set_int(&mut self, v: i64) { todo!() }
    pub fn set_varchar(&mut self, s: &'a [u8]) { todo!() }
    pub fn set_string(&mut self, type_: ObObjType, s: &'a [u8]) { todo!() }
    pub fn set_number(&mut self, desc: number::ObNumberDesc, digits: &'a [u8]) { todo!() }
    pub fn set_decimal_int(&mut self, bytes: &'a [u8]) { todo!() }
    // —— 深拷贝（:1942-1944）——
    pub fn need_deep_copy(&self) -> bool { todo!() }
    pub fn get_deep_copy_size(&self) -> i64 { todo!() }
    /// C++ deep_copy(src, buf, size, pos)（this 是目标）：buf 是游标式切片，拷贝后前移，调用方的 pos = size - buf.len()；
    /// self 改指 buf 里的副本。缓冲不够返回 OB_BUF_NOT_ENOUGH
    pub fn deep_copy(&mut self, src: &ObObj<'_>, buf: &mut &'a mut [u8]) -> ObResult { todo!() }
    // —— 哈希（:1962-1971），逐字节等于 C++（3.10）——
    pub fn hash(&self, res: &mut u64) -> ObResult { todo!() }
    pub fn hash_uint64_t(&self, res: &mut u64, seed: u64) -> ObResult { todo!() }       // hash(res, seed)
    pub fn hash_murmur(&self, res: &mut u64, seed: u64 /* = 0 */) -> ObResult { todo!() }
    pub fn varchar_hash(&self, cs_type: ObCollationType, seed: u64) -> u64 { todo!() }
    pub fn varchar_murmur_hash(&self, cs_type: ObCollationType, seed: u64) -> u64 { todo!() }
    pub fn varchar_wy_hash(&self, cs_type: ObCollationType, seed: u64) -> u64 { todo!() }
    pub fn varchar_xx_hash(&self, cs_type: ObCollationType, seed: u64) -> u64 { todo!() }
    pub fn checksum(&self, bc: &mut ObBatchChecksum) { todo!() }
    /// OBJ_DATUM_FULL 的 16 字节镜像（ob_datum.h:697-714）：meta_、u_、v_ 按小端；p_ 非空时 OB_ERR_UNEXPECTED
    pub fn to_full_bytes(&self) -> ObResult<[u8; 16]> { todo!() }
    pub fn from_full_bytes(b: &[u8; 16]) -> ObObj<'static> { todo!() }
}
impl ObObjOwned {
    pub fn view(&self) -> ObObj<'_> { todo!() }
    /// "视图 → 拥有型"（RULEBOOK §2.12）
    pub fn deep_copy(&mut self, src: &ObObj<'_>) -> ObResult { todo!() }
    // getter 与 ObObj 同名，转发到 view()；setter 取 &[u8] 并拷贝
}
/// "视图 → arena 视图"：C++ ob_write_obj(alloc, src, dst)
pub fn ob_write_obj<'q>(arena: &'q Arena, src: &ObObj<'_>, dst: &mut ObObj<'q>) -> ObResult { todo!() }

/// ObObjParam（:3618）：非虚继承，用 base 字段加 Deref 转发 ObObj 的方法。
pub struct ObObjParam<'a> {
    pub base: ObObj<'a>,
    pub accuracy_: ObAccuracy, pub res_flags_: u32, pub flag_: ParamFlag,
    pub raw_text_pos_: i32, pub raw_text_len_: i32, pub param_meta_: ObObjMeta,
}
impl<'a> core::ops::Deref for ObObjParam<'a> { type Target = ObObj<'a>; fn deref(&self) -> &ObObj<'a> { todo!() } }
impl<'a> core::ops::DerefMut for ObObjParam<'a> { fn deref_mut(&mut self) -> &mut ObObj<'a> { todo!() } }

impl<'de> ObDeserialize<'de> for ObObj<'de> { /* 字符串类零拷贝指向 buf（ob_obj_funcs.h 的 decode） */ }
impl<'de> ObDeserialize<'de> for ObObjOwned { /* 内部拷贝 */ }
impl ObSerialize for ObObj<'_> { /* 与 C++ 逐字节相同 */ }
impl ObToString for ObObj<'_> { /* cmp_meta() 会把它返回给用户（RULEBOOK 0.11.2），逐字节复刻 */ }
```

- `ObObjPrintParams::exec_ctx_`（`ob_object.h:1251`，oblib 里的 `sql::ObExecContext *`）是前向声明的向上类型（P-26 的原例），处理方式由 `seams.tsv` 定；本文不定。
- `ObObjValue` 的 `ptr_`/`ext_`（`:1271,1281`）在 C++ 里存 PL 复合值、SQL 数组等对象的地址。Rust 里 `v_` 只存 PL 模块定义的句柄或 id，object 模块从不解引用它。

### 2.3 帧模块：存储表 `StoreTable`（`query::engine::expr::frame::store`）

#### 2.3.1 表项与解析

```rust
/// RULEBOOK 0.6 的"存储表"。DataRef 能指向的内存都是它的表项：
///   计划常量区（常量帧 + 常量字节块，Arc 共享，只读）、帧（动态帧、datum 帧、参数帧，拥有）、
///   结果 arena 与批次 arena 的块（拥有）、行存储的块（拥有）、钉住的 KV cache 块（KvBlockHandle，只读）、
///   其他共享只读字节（Arc<[u8]>）。
/// ObExecContext 持有一个（2.4.1）；storage 等不在执行上下文里的路径可以自建一个。Send，不是 Sync。
pub struct StoreTable { /* slots: Vec<Slot { gen: u16, entry: Option<StoreEntry> }>, free: Vec<u32>,
                          plan_cnt: u32, frame_stores: Vec<u32>, arenas: Vec<ChunkArena> */ }

impl StoreTable {
    pub fn new() -> StoreTable { todo!() }               // 结果 arena（id 0，执行期间不 reset）预先建好
    /// 计划常量区：只能在空表上调一次；第 i 个缓冲的 store id 固定为 i+1、代 0，执行期间不注销。
    pub fn register_plan_stores(&mut self, stores: Arc<[Box<[u8]>]>) -> ObResult { todo!() }
    pub fn register_owned(&mut self, buf: Box<[u8]>) -> ObResult<u32> { todo!() }
    pub fn register_pinned(&mut self, h: KvBlockHandle) -> ObResult<u32> { todo!() }
    pub fn register_shared(&mut self, buf: Arc<[u8]>) -> ObResult<u32> { todo!() }
    /// 注销：拥有的缓冲交还调用方（行存储落盘、复用块时要用）；钉住的句柄 drop 即解钉。代号加 1。
    pub fn unregister(&mut self, store: u32) -> ObResult<Option<Box<[u8]>>> { todo!() }
    pub fn is_live(&self, store: u32) -> bool { todo!() }
    pub fn store_bytes(&self, store: u32) -> ObResult<&[u8]> { todo!() }
    pub fn store_bytes_mut(&mut self, store: u32) -> ObResult<&mut [u8]> { todo!() } // 只读表项 OB_ERR_UNEXPECTED
    /// RULEBOOK 0.6 的 bytes：len == 0 时不检查 r，返回空切片（C++ 不解引用）；
    /// 其余不合法（NULL 且 len>0、SELF、未知或过期的 store、越界）记 ERROR 加调用栈后 ob_abort()（3.1）
    pub fn bytes(&self, r: DataRef, len: u32) -> &[u8] { todo!() }
    pub fn try_bytes(&self, r: DataRef, len: u32) -> ObResult<&[u8]> { todo!() }
    pub fn view(&self, d: ObDatum) -> ObDatumView<'_> { todo!() }
    pub fn view_buf<'s, const N: usize>(&'s self, b: &'s ObDatumBuf<N>) -> ObDatumView<'s> { todo!() }
    /// ObDatum* 的解引用与赋值：rec 指向一条 12 字节记录
    pub fn datum_at(&self, rec: DataRef) -> ObResult<ObDatum> { todo!() }
    pub fn set_datum_at(&mut self, rec: DataRef, d: ObDatum) -> ObResult { todo!() }
    /// MEMMOVE：源和目标可以重叠（同一表项内用 copy_within）
    pub fn copy_bytes(&mut self, dst: DataRef, src: DataRef, len: u32) -> ObResult { todo!() }
    /// MEMCPY：源在表外（栈上、临时 arena、ObObj 的字节）
    pub fn write_bytes(&mut self, dst: DataRef, src: &[u8]) -> ObResult { todo!() }
    pub fn fill_bytes(&mut self, dst: DataRef, len: u32, byte: u8) -> ObResult { todo!() }   // MEMSET
}
```

#### 2.3.2 arena：结果 arena 与批次 arena

```rust
impl StoreTable {
    /// 结果 arena 的 id，C++ eval_res_allocator_（ob_exec_context.h:884），执行期间不 reset
    pub const EXPR_RES_ARENA: u32 = 0;
    /// 新建批次 arena（RULEBOOK 0.7"批次临时 arena 归帧模块的存储表所有"）。块按 8 KB 起、翻倍增长，每块一个表项
    pub fn create_arena(&mut self) -> ObResult<u32> { todo!() }
    /// len == 0 返回 OB_ALLOCATE_MEMORY_FAILED（同 PageArena，page_arena.h:220-231；RULEBOOK 0.7）
    pub fn arena_alloc(&mut self, arena: u32, len: u32) -> ObResult<DataRef> { todo!() }
    /// 分配并立即写入（ob_write_string 到批次 arena 的写法）
    pub fn arena_copy(&mut self, arena: u32, src: &[u8]) -> ObResult<DataRef> { todo!() }
    /// &mut self：此刻没有存活的视图；被 reset 的块全部注销，指向它们的 DataRef 之后解析会失败（3.1）
    pub fn reset_arena(&mut self, arena: u32) -> ObResult { todo!() }
    pub fn arena_used(&self, arena: u32) -> i64 { todo!() }
    pub fn arena_total(&self, arena: u32) -> i64 { todo!() }
}
```

#### 2.3.3 帧

```rust
impl StoreTable {
    pub fn frame_cnt(&self) -> u64 { todo!() }
    /// frame_idx（RtExpr::frame_idx_）→ store id
    pub fn frame_store(&self, frame_idx: u32) -> ObResult<u32> { todo!() }
    pub fn set_frame_store(&mut self, frame_idx: u32, store: u32) -> ObResult { todo!() }
    /// 在现有帧之后追加一个私有帧（C++ ObDatumCaster 用 frame_cnt 作私有帧号，ob_datum_cast.cpp:12847-12864）
    pub fn alloc_extra_frame(&mut self, size: u32) -> ObResult<u32> { todo!() }
}
```

行存储的 datum（`StoredRow::cells()`）在行内存 `DataRef::self_rel(off)`（off 相对行所在的块）；行存储把块注册为表项后，`to_expr` 把它改写成 `DataRef { store: 块的 id, off: 块内偏移 }` 再写进帧（`ob_chunk_datum_store.h:136-137`）。所以 C++ 的 swizzling/unswizzling（`:147-149`）在 Rust 里只剩"换 store 号"，不再整行改写指针。

#### 2.3.4 `ObBitVector`（share 的类型，构造在帧模块）

```rust
// share::vector::ob_bit_vector（translate，形状由本文规定）
/// C++ ObBitVectorImpl<WordType>（ob_bit_vector.h:45-196）是"把任意内存解释成位向量"的无头结构。
/// Rust：包 [u8] 的 DST，按字读写时用 from_ne_bytes 拼字，所以不要求对齐（帧里的位向量偏移不保证 8 对齐，
/// ob_static_engine_expr_cg.cpp:975-982）。小端平台上与 C++ 逐位同布局。
#[repr(transparent)]
pub struct ObBitVectorImpl<W: BitWord> { _w: core::marker::PhantomData<W>, data_: [u8] }
pub type ObBitVector = ObBitVectorImpl<u64>;
pub type ObTinyBitVector = ObBitVectorImpl<u8>;
// at/set/unset/reset/is_all_true/accumulate_bit_cnt/foreach/bit_op_zero/reinterpret_data 等方法照抄，全是安全代码；
// 原子版本（ATOMIC_BCAS，:253-257）只用在 8 对齐的拥有型缓冲上，由清单逐处确认。

// query::engine::expr::frame::bitvec（白名单，unsafe 只在这里，§4）
pub fn to_bit_vector(mem: &[u8]) -> &ObBitVector { todo!() }               // C++ to_bit_vector(const void*)（:203-206）
pub fn to_bit_vector_mut(mem: &mut [u8]) -> &mut ObBitVector { todo!() }   // C++ to_bit_vector(void*)（:198-201）
pub fn new_bit_vector(bits: i64) -> ObResult<Box<ObBitVector>> { todo!() } // 算子自己分配的 skip 位图（memory_size(bits) 字节，清零）
```

### 2.4 帧模块：`RtExpr`、`ObEvalCtx`、求值入口与写入

#### 2.4.1 与 `ObExecContext` 的连接

```rust
/// ObEvalCtx::exec_ctx_（ob_expr.h:278）引用的 sql::ObExecContext 是前向声明的向上类型（P-26）。
/// query 定义句柄 trait，sql 的 ObExecContext 实现；trait 名按 §4 的 <类名>Virt 规则，seams.tsv 可改名。
/// 实现者必须是 'static（8.2）：sql 代码经 as_any_mut() 向下转型拿回 ObExecContext。
pub trait ObExecContextVirt: core::any::Any + Send {
    /// 构造 ObEvalCtx 时取出存储表，析构时放回；已被取出时返回 None
    fn take_store_table(&mut self) -> Option<StoreTable>;
    fn restore_store_table(&mut self, st: StoreTable);
    fn as_any_mut(&mut self) -> &mut dyn core::any::Any;
    /// 下压过滤器（query）调用 ExprOpCtx 的 6 个虚函数（2.10.2）
    fn get_expr_op_ctx_virt(&mut self, expr_ctx_id: u32) -> Option<&mut dyn ObExprOperatorCtxVirt>;
    /// ObEvalCtx::get_datum_access_ctx（ob_expr.cpp:99-103）
    fn get_datum_access_ctx(&mut self) -> ObResult<Option<&ObDatumAccessContext>>;
}
```

#### 2.4.2 id 与表达式引用

```rust
/// RULEBOOK 0.6/§4：运行期表达式 id，值 = 下标 + 1（ob_expr.h:1316-1360 的序列化编码），0 留给 NULL。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RtExprId(core::num::NonZeroU32);
impl RtExprId {
    pub const fn from_index(i: u32) -> RtExprId { todo!() }     // i + 1；i == u32::MAX 时 ob_abort
    pub const fn index(self) -> u32 { todo!() }
    pub const fn from_raw(v: u32) -> Option<RtExprId> { todo!() } // 0 → None
    pub const fn raw(self) -> u32 { todo!() }
}
/// 算子 spec 的 id，同样是下标 + 1；spec 下标就是逻辑算子编号（ob_physical_plan.cpp:814-821）。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SpecId(core::num::NonZeroU32);
impl SpecId { /* 同 RtExprId 的四个函数 */ }

/// RULEBOOK 0.6：运行期新建、引用计划表达式的表达式（ob_datum_cast.cpp:13070）的参数。
#[derive(Clone, Copy)]
pub enum ExprRef<'e> { Plan(RtExprId), Local(&'e RtExpr<'e>) }
```

#### 2.4.3 `RtExpr`（C++ `ObExpr`）

```rust
pub type EvalFn = fn(&RtExpr<'_>, &mut ObEvalCtx<'_>, &mut ObDatumBuf) -> ObResult;              // EvalFunc（:617-619）
pub type EvalBatchFn = fn(&RtExpr<'_>, &mut ObEvalCtx<'_>, &ObBitVector, i64) -> ObResult;       // EvalBatchFunc（:620-623）
pub type EvalEnumSetFn = fn(&RtExpr<'_>, &[Box<[u8]>], u64, &mut ObEvalCtx<'_>, &mut ObDatumBuf) -> ObResult; // EvalEnumSetFunc（:624-628）

/// C++ void **inner_functions_（:672-676，78 处、18 个文件）改成带类型的函数指针。
/// 变体由 1d 从这 18 个文件逐个列全；下面是已确认的。
#[derive(Clone, Copy)]
pub enum InnerFn {
    Null,                                   // C++ 里检查 NULL 的槽（ob_expr_operator.cpp:3461）
    Eval(EvalFn),                           // cast_eval_arg（ob_datum_cast.cpp:12755）
    DatumCmp(ObDatumCmpFuncType),           // between/least/nullif/in（ob_expr_between.cpp:125-126 等）
    ExprCmp(DatumCmpFunc),                  // ob_expr_operator.cpp:5245-5252
    NullSafeRowCmp(NullSafeRowCmpFunc),
    RowCmp(RowCmpFunc),                     // ob_expr.h:49-53
}

/// C++ ObExpr（ob_expr.h:342-714）。plan 里的是 RtExpr<'static>；ObDatumCaster 在栈上建的局部表达式带 'e。
/// 不 derive(Clone)（RULEBOOK 0.6）；计划深拷贝见 2.5。
pub struct RtExpr<'e> {
    pub type_: ObItemType,
    pub datum_meta_: ObDatumMeta,
    pub obj_meta_: ObObjMeta,
    pub max_length_: i32,
    pub obj_datum_map_: ObObjDatumMapType,
    pub flag_: u64,                                       // 位访问函数 batch_result_()/set_batch_result_() 等 7 个（:644-653）
    pub eval_func_: Option<EvalFn>,
    pub eval_batch_func_: Option<EvalBatchFn>,
    pub inner_functions_: Box<[InnerFn]>,                 // inner_func_cnt_ = len()
    pub args_: Box<[ExprRef<'e>]>,                        // arg_cnt_ = len()
    pub parents_: Box<[RtExprId]>,                        // parent_cnt_ = len()
    pub frame_idx_: u32,
    pub datum_off_: u32,
    pub eval_info_off_: u32,
    pub dyn_buf_header_offset_: u32,
    pub res_buf_off_: u32,
    pub res_buf_len_: u32,
    pub eval_flags_off_: u32,
    pub pvt_skip_off_: u32,
    pub expr_ctx_id_: u32,
    pub extra_: u64,                                      // 位访问 div_calc_scale_/is_error_div_by_zero_/may_not_need_raw_check_（:701-709）
    pub basic_funcs_: Option<&'static ObDatumBasicFuncs>,
    pub batch_idx_mask_: u64,
    pub extra_info_: Option<Box<ObIExprExtraInfo>>,       // 2.10.1
    pub local_session_var_id_: i64,
}
impl<'e> RtExpr<'e> {
    pub const INVALID_EXP_CTX_ID: u32 = u32::MAX;         // :348
    pub fn new() -> RtExpr<'e> { todo!() }                // ObExpr()（ob_expr.cpp:252-284）
    pub fn reset(&mut self) { todo!() }
    pub fn init_runtime_functions(&mut self) -> ObResult { todo!() }
    pub fn is_batch_result(&self) -> bool { todo!() }
    pub fn is_const_expr(&self) -> bool { todo!() }
    pub fn is_vector_sort_expr(&self) -> bool { todo!() }
    pub fn is_semantic_distance_expr(&self) -> bool { todo!() }
    pub fn is_semantic_vector_distance_expr(&self) -> bool { todo!() }
    pub fn is_variable_res_buf(&self) -> bool { todo!() }
    pub fn arg(&self, i: usize) -> ExprRef<'e> { todo!() }                       // args_[i]
    pub fn get_datum_idx(&self, ctx: &ObEvalCtx<'_>) -> i64 { todo!() }
    pub fn contain_expr(&self, ctx: &ObEvalCtx<'_>, expr: ExprRef<'_>, is_contain: &mut bool) -> ObResult { todo!() } // 按 id/地址判同一
    pub fn inner_function(&self, i: usize) -> InnerFn { todo!() }                // 越界返回 InnerFn::Null
    // 生成的取类型函数：inner_function_as_eval(i) -> ObResult<EvalFn> 等，不匹配返回 OB_ERR_UNEXPECTED
}

/// C++ ObExprExtraInfoAccess<T>（:716-725）：把 extra_ 当作小 struct。T 用 ob_bitfield! 生成 from_bits/to_bits。
pub struct ObExprExtraInfoAccess<T>(core::marker::PhantomData<T>);
impl<T: ObBitfieldU64> ObExprExtraInfoAccess<T> {
    pub fn get_info(e: &RtExpr<'_>) -> T { todo!() }
    pub fn set_info(e: &mut RtExpr<'_>, v: T) { todo!() }
}
```

#### 2.4.4 帧里的小结构

```rust
/// C++ ObDatumMeta（ob_expr.h:65-127），值类型，照抄。precision_ 与 length_semantics_ 是同宽联合，给两个访问函数。
#[derive(Clone, Copy)]
pub struct ObDatumMeta { pub type_: ObObjType, pub cs_type_: ObCollationType, pub scale_: i8, pub precision_: ObPrecision }

/// C++ ObEvalInfo（:130-158），4 字节，帧里按值读写（不借出 &mut）。
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct ObEvalInfo { flag_: u16, pub cnt_: u16 }
impl ObEvalInfo {
    pub fn evaluated_(self) -> bool { todo!() }       pub fn set_evaluated_(&mut self, v: bool) { todo!() }
    pub fn projected_(self) -> bool { todo!() }       pub fn set_projected_(&mut self, v: bool) { todo!() }
    pub fn notnull_(self) -> bool { todo!() }         pub fn set_notnull_(&mut self, v: bool) { todo!() }
    pub fn point_to_frame_(self) -> bool { todo!() }  pub fn set_point_to_frame_(&mut self, v: bool) { todo!() }
    pub fn clear_evaluated_flag(&mut self) { todo!() } // :132-138
}

/// C++ ObDynReserveBuf（:294-317），16 字节；mem_ 由地址改成 DataRef（指向结果 arena），尺寸不变。
#[derive(Clone, Copy, Default)]
pub struct ObDynReserveBuf { pub magic_: u32, pub len_: u32, pub mem_: DataRef }
impl ObDynReserveBuf {
    pub const MAGIC_NUM: u32 = 0xD928e5bf;
    pub fn supported(type_: ObObjType) -> bool { todo!() }   // :297-309
}

/// C++ ObDatumObj/ObDatumObjParam/DatumParamStore（:750-897）。datum_ 的载荷在存储表的参数 arena 里。
pub struct ObDatumObj { pub meta_: ObDatumMeta, pub datum_: ObDatum }
pub struct ObDatumObjParam { pub base: ObDatumObj, accuracy_: ObAccuracy, res_flags_: u32, flag_: ParamFlag }
// 方法同名照抄；set_sql_array_datum/get_sql_datum_array（:800-829）把对象地址塞进 datum，改成存 ObPhysicalPlanCtx
// 里 SQL 数组表的 id（i64），由调用方的单元按 id 取回。
pub type DatumParamStore = Vec<ObDatumObjParam>;
```

#### 2.4.5 `ObEvalCtx`

```rust
/// C++ ObEvalCtx（ob_expr.h:161-289）。按调用构造，借用存储表、执行上下文和计划的表达式数组；
/// 不得存进跨调用的结构（算子只存 max_batch_size_ 之类的整数）。Send 与否无关：它只活在一次调用里。
pub struct ObEvalCtx<'x> {
    st: &'x mut StoreTable,
    pub exec_ctx_: &'x mut dyn ObExecContextVirt,
    exprs: &'x [RtExpr<'static>],
    batch_idx_: i64,
    batch_size_: i64,
    pub max_batch_size_: i64,
    expr_res_arena_: u32,                    // C++ expr_res_alloc_，默认 StoreTable::EXPR_RES_ARENA
}

impl<'x> ObEvalCtx<'x> {
    // —— 构造（C++ ObEvalCtx(ObExecContext&, ObIAllocator* = NULL)，ob_expr.cpp:47-59）——
    /// 取出 exec 的存储表，构造上下文跑 f，再放回（出错也放回）。表已被取出时返回 OB_ERR_UNEXPECTED。
    /// res_arena：C++ 传入 ObArenaAllocator 时改用调用方先 create_arena() 得到的 id；None 用结果 arena
    pub fn new_scope<R>(exec: &mut dyn ObExecContextVirt, exprs: &[RtExpr<'static>], res_arena: Option<u32>,
                        f: impl FnOnce(&mut ObEvalCtx<'_>) -> ObResult<R>) -> ObResult<R> { todo!() }
    /// C++ ObEvalCtx(ObEvalCtx&)（ob_expr.cpp:61-72）：同一张表，批次信息各自独立
    pub fn child(&mut self) -> ObEvalCtx<'_> { todo!() }
    /// 求值中途要调用算子（子查询迭代、PX）：把存储表暂时还给 exec，跑完再取回
    pub fn with_exec_ctx<R>(&mut self, f: impl FnOnce(&mut dyn ObExecContextVirt) -> ObResult<R>) -> ObResult<R> { todo!() }
    /// C++ ctx.exec_ctx_ 的 sql 侧用法：向下转型；T 不对时 ERROR 日志 + ob_abort（结构性不变量）
    pub fn exec_ctx<T: ObExecContextVirt>(&mut self) -> &mut T { todo!() }
    pub fn store_table(&self) -> &StoreTable { todo!() }
    pub fn store_table_mut(&mut self) -> &mut StoreTable { todo!() }
    pub fn expr<'a>(&'a self, r: ExprRef<'a>) -> &'a RtExpr<'a> { todo!() }     // Plan(id) 越界 ob_abort（计划构造时已校验）
    // —— 批次信息（:221-258）——
    pub fn get_batch_idx(&self) -> i64 { todo!() }
    pub fn get_batch_size(&self) -> i64 { todo!() }
    pub fn is_vectorized(&self) -> bool { todo!() }
    pub fn reuse(&mut self, batch_size: i64) { todo!() }
    pub fn set_max_batch_size(&mut self, v: i64) { todo!() }
    pub fn set_batch_idx(&mut self, v: i64) { todo!() }      // C++ 是 private，storage 直接调（:244-251）
    pub fn set_batch_size(&mut self, v: i64) { todo!() }
    pub fn get_datum_access_ctx(&mut self) -> ObResult<Option<&ObDatumAccessContext>> { todo!() }
}

/// C++ ObEvalCtx::BatchInfoScopeGuard（:185-216）：闭包式，返回后恢复 batch_idx_/batch_size_/max_batch_size_。
pub struct BatchInfoScopeGuard;
impl BatchInfoScopeGuard {
    pub fn scope<R>(ctx: &mut ObEvalCtx<'_>, f: impl FnOnce(&mut ObEvalCtx<'_>) -> ObResult<R>) -> ObResult<R> { todo!() }
}

/// C++ ObEvalCtx::TempAllocGuard（:170-184）。每个守卫自带一个新的 Arena，drop 时释放（8.5、修订 A4）；
/// 不借用 ctx，所以守卫活着时照样可以求值、可以把 ctx 和 arena 一起传给辅助函数。
pub struct TempAllocGuard { alloc_: Arena }
impl TempAllocGuard {
    pub fn new(ctx: &ObEvalCtx<'_>) -> TempAllocGuard { todo!() }
    pub fn get_allocator(&self) -> &Arena { todo!() }
}
```

#### 2.4.6 求值入口

```rust
impl<'x> ObEvalCtx<'x> {
    /// C++ ObExpr::eval(ctx, datum*&)（:1039-1070）：返回帧里那条 datum 记录的副本。
    pub fn eval(&mut self, e: ExprRef<'_>) -> ObResult<ObDatum> { todo!() }
    /// C++ args_[i]->eval(ctx, d)（306 处）
    pub fn eval_arg(&mut self, e: &RtExpr<'_>, i: usize) -> ObResult<ObDatum> { todo!() }
    /// C++ eval_param_value(ctx)：按序求值全部 arg_cnt_ 个参数，遇错即停（:1007-1022）
    pub fn eval_param_value(&mut self, e: &RtExpr<'_>) -> ObResult { todo!() }
    /// RULEBOOK 0.6：先全部求值，再统一借出前 N 个参数的视图。覆盖 eval_param_value(ctx, a, b, …)（约 134 处）
    /// 和二元算子的 args_[0]/args_[1]->eval。N > arg_cnt_ 时 OB_ERR_UNEXPECTED。
    /// 返回的视图把 self 锁成可变借用；还要在视图存活期间调 &self 方法时，用下面的两步写法。
    pub fn eval_args<const N: usize>(&mut self, e: &RtExpr<'_>) -> ObResult<[ObDatumView<'_>; N]> { todo!() }
    /// 两步写法的第二步：读参数的当前值（不求值）
    pub fn arg_views<const N: usize>(&self, e: &RtExpr<'_>) -> [ObDatumView<'_>; N] { todo!() }
    /// C++ eval_batch（:1072-1093）与 eval_batch_param_value（:1024-1037）
    pub fn eval_batch(&mut self, e: ExprRef<'_>, skip: &ObBitVector, size: i64) -> ObResult { todo!() }
    pub fn eval_batch_param_value(&mut self, e: &RtExpr<'_>, skip: &ObBitVector, size: i64) -> ObResult { todo!() }
    /// C++ ObExpr::eval_enumset（ob_expr.cpp:318-386）
    pub fn eval_enumset(&mut self, e: ExprRef<'_>, str_values: &[Box<[u8]>], cast_mode: u64) -> ObResult<ObDatum> { todo!() }
    /// RULEBOOK 0.6：ObExprColumnConv::eval_enumset 无执行上下文 id 的分支（ob_expr_column_conv.cpp:741-746）
    /// 把 args_[0] 换成 arg0 求值，不改共享计划。FIXED(port)。
    pub fn eval_enumset_with_arg0(&mut self, e: &RtExpr<'_>, arg0: ExprRef<'_>, str_values: &[Box<[u8]>], cast_mode: u64) -> ObResult<ObDatum> { todo!() }
    /// 常见批量写法的封装：对未被 skip、未求值的每一行，借出 N 个参数在该行的视图，调 f 写本地结果，
    /// 再落盘到第 i 行、置 evaluated 位、维护 notnull_（ob_batch_eval_util.h:60-90 的语义）
    pub fn batch_eval_map<const N: usize>(&mut self, e: &RtExpr<'_>, skip: &ObBitVector, size: i64,
        f: impl FnMut(i64, [ObDatumView<'_>; N], &mut ObDatumBuf) -> ObResult) -> ObResult { todo!() }
}
/// C++ expr_default_eval_batch_func（ob_expr.cpp:711-761）：CG 给没有批量函数的表达式填的默认值
pub fn expr_default_eval_batch_func(e: &RtExpr<'_>, ctx: &mut ObEvalCtx<'_>, skip: &ObBitVector, size: i64) -> ObResult { todo!() }
pub fn expr_default_eval_batch_func_eval_bound(e: &RtExpr<'_>, ctx: &mut ObEvalCtx<'_>, skip: &ObBitVector, bound: &EvalBound) -> ObResult { todo!() }
```

#### 2.4.7 读与定位

```rust
impl<'x> ObEvalCtx<'x> {
    pub fn view(&self, d: ObDatum) -> ObDatumView<'_> { todo!() }
    /// RULEBOOK 0.6 的签名
    pub fn bytes(&self, d: &ObDatum) -> &[u8] { todo!() }
    pub fn view_buf<'s>(&'s self, b: &'s ObDatumBuf) -> ObDatumView<'s> { todo!() }
    // C++ locate_*（:364-427）：只读的定位返回记录副本或句柄
    pub fn locate_expr_datum(&self, e: ExprRef<'_>) -> ObDatum { todo!() }
    pub fn locate_expr_datum_at(&self, e: ExprRef<'_>, batch_idx: i64) -> ObDatum { todo!() }  // 重载 (ctx, batch_idx)
    pub fn locate_param_datum(&self, e: &RtExpr<'_>, i: usize) -> ObDatum { todo!() }
    pub fn locate_expr_datumvector(&self, e: ExprRef<'_>) -> ObDatumVector { todo!() }
    pub fn locate_param_datumvector(&self, e: &RtExpr<'_>, i: usize) -> ObDatumVector { todo!() }
    pub fn locate_batch_datums(&self, e: ExprRef<'_>) -> ObDatumVector { todo!() }             // mask_ 全 1
    pub fn datum_at(&self, v: ObDatumVector, i: i64) -> ObDatum { todo!() }
    pub fn view_at(&self, v: ObDatumVector, i: i64) -> ObDatumView<'_> { todo!() }
    pub fn get_rev_buf(&self, e: ExprRef<'_>) -> DataRef { todo!() }                           // :410-413
    // 标志位
    pub fn get_eval_info(&self, e: ExprRef<'_>) -> ObEvalInfo { todo!() }
    pub fn set_eval_info(&mut self, e: ExprRef<'_>, info: ObEvalInfo) { todo!() }
    pub fn clear_evaluated_flag(&mut self, e: ExprRef<'_>) { todo!() }        // :551-558
    pub fn set_evaluated_flag(&mut self, e: ExprRef<'_>) { todo!() }          // :560-566
    pub fn set_evaluated_projected(&mut self, e: ExprRef<'_>) { todo!() }     // :568-572
    pub fn get_evaluated_flags(&self, e: ExprRef<'_>) -> &ObBitVector { todo!() }
    pub fn get_evaluated_flags_mut(&mut self, e: ExprRef<'_>) -> &mut ObBitVector { todo!() }
    pub fn get_pvt_skip(&self, e: ExprRef<'_>) -> &ObBitVector { todo!() }
    pub fn get_pvt_skip_mut(&mut self, e: ExprRef<'_>) -> &mut ObBitVector { todo!() }
}
```

#### 2.4.8 写入

```rust
impl<'x> ObEvalCtx<'x> {
    /// 把本地结果落盘到 e 的第 idx 个 datum（非批量 idx 取 0）。规则（3.6）：
    ///   Local → 写进 res buf 的第 idx 格（超过 res_buf_len_ 时走 DRB，同 get_str_res_mem）；
    ///   Ref → 浅写 DataRef；Heap → 拷进 DRB；null → set_null。
    /// 用于求值函数之外的写帧（storage 写扫描结果、算子投影），对应 C++ locate_datum_for_write(ctx).set_xxx(...)。
    pub fn write_result(&mut self, e: ExprRef<'_>, idx: i64, res: &ObDatumBuf) -> ObResult { todo!() }
    /// C++ *datum = other / datums[i] = other（浅复制存储形态）
    pub fn set_expr_datum(&mut self, e: ExprRef<'_>, idx: i64, d: ObDatum) -> ObResult { todo!() }
    pub fn reset_ptr_in_datum(&mut self, e: ExprRef<'_>, idx: i64) -> ObResult { todo!() }      // :997-1005
    pub fn locate_datums_for_update(&mut self, e: ExprRef<'_>, size: i64) -> ObResult<ObDatumVector> { todo!() } // :979-995
    /// C++ deep_copy_datum（:1105-1126）：src 可以就在 e 自己的 res buf 里（MEMMOVE 语义）
    pub fn deep_copy_datum(&mut self, e: ExprRef<'_>, src: ObDatum) -> ObResult { todo!() }
    pub fn deep_copy_self_datum(&mut self, e: ExprRef<'_>) -> ObResult { todo!() }
    /// C++ `buf = get_str_res_mem(ctx, size); …读参数、写 buf…; res.set_string(buf, len)`（91 处）。
    /// srcs 按值给出要读的 datum；帧模块借出它们的字节和 e 当前行的结果缓冲（res buf 或 DRB，规则同 C++，3.7），
    /// 源与目标重叠时先把重叠的源拷到临时缓冲（C++ 在这些地方用 MEMMOVE，ob_datum_cast.cpp:463、487）。
    /// f 返回实际写入的长度（≤ size）；成功后 res 变成 Ref(结果缓冲, 长度)。
    pub fn get_str_res_mem_with(&mut self, e: &RtExpr<'_>, res: &mut ObDatumBuf, size: i64, srcs: &[ObDatum],
        f: impl FnOnce(&[&[u8]], &mut [u8]) -> ObResult<usize>) -> ObResult { todo!() }
    /// C++ cur_str_resvered_buf（:444-468）的只读查询：当前预留缓冲的位置和长度
    pub fn cur_str_resvered_buf(&self, e: &RtExpr<'_>, datum_idx: i64) -> ObResult<(DataRef, i64)> { todo!() }
}
```

`ObExprStrResAlloc`（`ob_expr.h:730-748`，50 处）不移植：C++ 把它当 `ObIAllocator` 交给库函数分配结果；Rust 的库函数按 RULEBOOK §2.4 取 `&Arena`，所以改成"用 `TempAllocGuard` 的 arena 算出结果，再 `res.set_string_copy(...)`"，结果字节相同，多一次拷贝（`PERF(port)`）。

### 2.5 帧模块：帧布局、计划常量区、深拷贝（`frame_info`）

```rust
/// C++ ObFrameInfo（ob_expr_frame_info.h:33-63），值类型
#[derive(Clone, Copy, Default)]
pub struct ObFrameInfo { pub expr_cnt_: u64, pub frame_idx_: u32, pub frame_size_: u64, pub zero_init_pos_: u32, pub zero_init_size_: u32 }

/// C++ ObExprFrameInfo（.h:65-150）。不 derive(Clone)（RULEBOOK 0.6）。
pub struct ObExprFrameInfo {
    pub need_ctx_cnt_: i64,
    pub rt_exprs_: Box<[RtExpr<'static>]>,
    /// 常量帧在前、常量字节块在后；第 i 个的 store id 固定为 i+1（2.3.1）。PX worker 共享同一个 Arc。
    pub const_frame_ptrs_: Arc<[Box<[u8]>]>,
    pub const_frame_: Box<[ObFrameInfo]>,
    pub param_frame_: Box<[ObFrameInfo]>,
    pub dynamic_frame_: Box<[ObFrameInfo]>,
    pub datum_frame_: Box<[ObFrameInfo]>,
}
impl ObExprFrameInfo {
    /// C++ EXPR_CNT_PER_FRAME = MAX_FRAME_SIZE / (12 + 4)（.h:66-67；MAX_FRAME_SIZE 在 ob_define.h:1314）
    pub const EXPR_CNT_PER_FRAME: i64 = MAX_FRAME_SIZE / (OB_DATUM_SIZE as i64 + 4);
    /// 计划深拷贝（C++ assign，.cpp:35-193）：字节帧原样复制，DataRef 不用重定位；extra_info_ 逐个 deep_copy；
    /// 参数和父节点本来就是 id，不做指针换算。`_px_worker_share_plan_enabled=False` 时用。
    pub fn deep_copy(&self) -> ObResult<ObExprFrameInfo> { todo!() }
    /// C++ alloc_frame（.cpp:231-279）：往 st 里登记 [常量帧（已在计划区）| 参数帧 | 动态帧 | datum 帧]，
    /// 建立 frame_idx → store 的映射；动态帧里每个 datum set_null；新帧整块清零（3.8）
    pub fn alloc_frame(&self, st: &mut StoreTable, param_frame_stores: &[u32], frame_cnt: &mut u64) -> ObResult { todo!() }
    pub fn get_expr_idx_in_frame(&self, expr: RtExprId, expr_idx: &mut i64) -> ObResult { todo!() } // .cpp:284-301
}
/// C++ ObPreCalcExprFrameInfo（.h:163-182）：链表基类去掉，计划里用 Vec 存（RULEBOOK 缺失规则 13）
pub struct ObPreCalcExprFrameInfo { pub base: ObExprFrameInfo, pub pre_calc_rt_exprs_: Box<[RtExprId]> }
impl ObPreCalcExprFrameInfo { pub fn deep_copy(&self) -> ObResult<ObPreCalcExprFrameInfo> { todo!() } } // assign（.cpp:396-416）

/// CG 建常量帧和参数帧用的构建 API（C++ ObStaticEngineExprCG::alloc_const_frame，ob_static_engine_expr_cg.cpp:1063-1105）
pub struct ObPlanConstStoreBuilder { /* frames: Vec<Box<[u8]>>, blobs: Vec<Box<[u8]>> */ }
impl ObPlanConstStoreBuilder {
    pub fn new(const_frames: &[ObFrameInfo]) -> ObResult<ObPlanConstStoreBuilder> { todo!() } // 每帧按 frame_size_ 清零分配
    pub fn frame_store(&self, i: u32) -> u32 { todo!() }                    // i+1
    pub fn add_blob(&mut self, bytes: &[u8]) -> ObResult<DataRef> { todo!() } // C++ ob_write_obj 进计划 allocator 的字符串
    pub fn frame_mut(&mut self, i: u32) -> &mut [u8] { todo!() }
    pub fn finish(self) -> Arc<[Box<[u8]>]> { todo!() }
}
```

- 帧布局的常数给 CG 用：`OB_DATUM_SIZE = 12`、`size_of::<ObEvalInfo>() == 4`、`ObDynReserveBuf` 16 字节、`ObBitVector::memory_size(n)`，都与 C++ 相同，所以 CG 的偏移计算（`ob_static_engine_expr_cg.cpp:958-1005`）照抄后得到同样的帧布局。
- `pre_alloc_exec_memory`（`.cpp:195-213`）、`ObPreCalcExprFrameInfo::eval/eval_expect_err`、`ObTempExpr::eval`、`ObTempExprCtxReplaceGuard`（`ob_exec_context.h:1040-1069`）依赖 sql 类型，归 sql 的自由函数；它们用 `StoreTable::new()` 建一张临时表，经 `ObExecContextVirt::{take,restore}_store_table` 交换进出（闭包式），等价于 C++ 暂时替换 `frames_` 和 `expr_op_ctx_store_`。

### 2.6 向量格式（`query::engine::vector`）

```rust
/// C++ VectorFormat（type_traits.h:27-36）
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq)]
pub struct VectorFormat(pub u8);   // VEC_INVALID=0, VEC_FIXED, VEC_DISCRETE, VEC_CONTINUOUS, VEC_UNIFORM, VEC_UNIFORM_CONST, VEC_MAX_FORMAT

/// C++ ObIVector（ob_i_vector.h:372-417）。四种格式都是借用拥有者数组的视图（'a）：
/// 数组由拥有者（DDL 批、临时列存、帧）分配，格式只引用；载荷指针（discrete 的 ptrs_、uniform 的 datum）存 DataRef，
/// 所以读写载荷的方法都多一个存储表参数。DEF_VEC_READ/WRITE_INTERFACES（:36、:214）的 get_int/set_int 等是默认方法。
pub trait ObIVector {
    fn get_format(&self) -> VectorFormat;
    fn get_payload<'s>(&'s self, st: &'s StoreTable, idx: i64) -> &'s [u8];                      // (payload, length)
    fn get_payload_bool<'s>(&'s self, st: &'s StoreTable, idx: i64, is_null: &mut bool) -> &'s [u8]; // 重载 2
    fn get_length(&self, idx: i64) -> ObLength;
    fn set_length(&mut self, idx: i64, length: ObLength);
    fn set_payload(&mut self, st: &mut StoreTable, idx: i64, payload: &[u8]) -> ObResult;          // 深拷贝
    fn set_payload_shallow(&mut self, idx: i64, payload: DataRef, length: ObLength);
    fn has_null(&self) -> bool;
    fn set_has_null(&mut self);
    fn reset_has_null(&mut self);
    fn is_null(&self, idx: i64) -> bool;
    fn set_null(&mut self, idx: i64);
    fn unset_null(&mut self, idx: i64);
    fn set_null_bound(&mut self, bound: &EvalBound) { todo!() }                                  // set_null(const EvalBound&)
    fn to_string(&self, buf: &mut ObBuf) -> i64 { 0 }
    fn get_int(&self, st: &StoreTable, idx: i64) -> i64 { todo!() }      // 其余 get_xxx/set_xxx 同样
    fn set_int(&mut self, st: &mut StoreTable, idx: i64, v: i64) -> ObResult { todo!() }
}
pub struct ObFixedLengthBase<'a> { nulls_: &'a mut ObBitVector, flag_: u16, len_: ObLength, data_: &'a mut [u8] }   // ob_fixed_length_base.h
pub struct ObDiscreteBase<'a> { nulls_: &'a mut ObBitVector, flag_: u16, lens_: &'a mut [ObLength], ptrs_: &'a mut [DataRef] } // ob_discrete_base.h
pub struct ObContinuousBase<'a> { nulls_: &'a mut ObBitVector, flag_: u16, offsets_: &'a mut [u32], data_: &'a mut [u8] } // ob_continuous_base.h
/// uniform 格式包的是帧里的批次 datum（ob_uniform_format.h），eval_info_ 是帧里那条 ObEvalInfo 的位置，经存储表按值改写
pub struct ObUniformBase<'a> { datums_: ObDatumVector, eval_info_: DataRef, _p: core::marker::PhantomData<&'a ()> }      // ob_uniform_base.h
// ObFixedLengthFormat<'a, T>、ObDiscreteFormat<'a>、ObContinuousFormat<'a>、ObUniformFormat<'a, const IS_CONST: bool>
// 以 base 字段包住对应的 base（§2.13）；方法名、语义照抄（ob_uniform_format.h 等）。
pub type IVectorPtrs<'v> = [&'v mut dyn ObIVector];
```

### 2.7 `query::ir_id`

```rust
/// RULEBOOK 0.6、§4：编译期 IR 的 id。NonZeroU32，值 = 下标 + 1；Option<Id> 仍是 4 字节，对应可空指针。
macro_rules! define_ir_id { ($($n:ident),*) => { $(
    #[repr(transparent)]
    #[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
    pub struct $n(core::num::NonZeroU32);
)* } }
define_ir_id!(ExprId, StmtId, TableItemId, LogOpId, PathId);

/// 五种 id 的公共接口（IdVec 的下标）。实现只有上面五个。
pub trait IrId: Copy + Eq + Ord + core::hash::Hash + 'static {
    fn from_index(i: u32) -> Self;          // i + 1
    fn index(self) -> u32;
    fn raw(self) -> u32;                    // 打印、做哈希键
}
// 每个 id 还实现 ObToString（打印 raw）和 oblib::hash 的哈希 trait（05），按值哈希 raw，供 ObHashMap<Id, _>（R4）

/// 以 id 为下标的表。分块存储（每块 512 槽）：截断时整块释放内存，但下一个 id 仍从旧的末尾继续，id 不复用。
pub struct IdVec<I: IrId, T> { /* chunks: Vec<Option<Box<[Option<T>]>>>, len: u32, live: u32 */ _p: core::marker::PhantomData<(I, T)> }
impl<I: IrId, T> IdVec<I, T> {
    pub const fn new() -> IdVec<I, T> { todo!() }
    /// try_reserve；len 到 u32::MAX - 1 时 OB_SIZE_OVERFLOW
    pub fn push(&mut self, v: T) -> ObResult<I> { todo!() }
    /// 越界或墓碑返回 None
    pub fn get(&self, id: I) -> Option<&T> { todo!() }
    pub fn get_mut(&mut self, id: I) -> Option<&mut T> { todo!() }
    /// 下一个要发的 id 的下标（= 已发的 id 个数，含墓碑）
    pub fn len(&self) -> u32 { todo!() }
    pub fn live_count(&self) -> u32 { todo!() }
    /// 把下标 ≥ from 的槽全部变成墓碑并 drop 内容；len 不变
    pub fn truncate(&mut self, from: u32) { todo!() }
    /// 单个墓碑（free_stmt）
    pub fn remove(&mut self, id: I) -> Option<T> { todo!() }
    /// 稠密旁表（R5 的 IdVec<ExprId, Option<RtExprId>>、R5 的标记表）：补到 len 个，每个 f()
    pub fn resize_with(&mut self, len: u32, f: impl FnMut() -> T) -> ObResult { todo!() }
    /// 只遍历活的，按 id 升序（即插入顺序，R4 的"按插入顺序"就用它）
    pub fn iter(&self) -> impl Iterator<Item = (I, &T)> { core::iter::empty() }
}
```

### 2.8 `sql::ir::QueryArena`

#### 2.8.1 存储与访问

```rust
/// RULEBOOK 0.6：每条语句一个；取代 ObRawExprFactory（ob_raw_expr.h:4996-5121）、ObStmtFactory（ob_stmt.cpp:194-253）、
/// ObLogOperatorFactory（ob_log_operator_factory.h:267-277）和 Path 的 OB_NEWx。节点存在 IdVec 里，可以需要 Drop。
/// 由编译入口持有（硬解析路径的栈帧或 ObResultSet），一律显式传参（R1），不放进 ObExecContext 或任何 self。
pub struct QueryArena {
    exprs: IdVec<ExprId, ObRawExpr>,
    stmts: IdVec<StmtId, ObStmt>,
    table_items: IdVec<TableItemId, TableItem>,
    log_ops: IdVec<LogOpId, ObLogicalOperator>,
    paths: IdVec<PathId, Path>,
    scopes: Vec<[u32; 5]>,          // 子作用域开始时五个 IdVec 的 len
    try_check_tick_: u64,           // ObRawExprFactory::try_check_tick_（:4999、:5073-5078）
    query_ctx_: Option<Box<ObQueryCtx>>,
}
// ObRawExpr、ObStmt、TableItem、ObLogicalOperator、Path 是"公共头 + 变体 enum"的节点类型（RULEBOOK 0.6，2.12），
// 由第 3 步翻译、脚本生成 enum；1d 用测试桩节点类型编译和测试 QueryArena。

impl QueryArena {
    pub fn new() -> QueryArena { todo!() }
    // —— 访问：越界或墓碑返回 OB_ERR_UNEXPECTED 并记 ERROR 日志（RULEBOOK 0.6、R8）——
    pub fn expr(&self, e: ExprId) -> ObResult<&ObRawExpr> { todo!() }
    pub fn expr_mut(&mut self, e: ExprId) -> ObResult<&mut ObRawExpr> { todo!() }
    pub fn stmt(&self, s: StmtId) -> ObResult<&ObStmt> { todo!() }
    pub fn stmt_mut(&mut self, s: StmtId) -> ObResult<&mut ObStmt> { todo!() }
    pub fn table_item(&self, t: TableItemId) -> ObResult<&TableItem> { todo!() }
    pub fn table_item_mut(&mut self, t: TableItemId) -> ObResult<&mut TableItem> { todo!() }
    pub fn log_op(&self, o: LogOpId) -> ObResult<&ObLogicalOperator> { todo!() }
    pub fn log_op_mut(&mut self, o: LogOpId) -> ObResult<&mut ObLogicalOperator> { todo!() }
    pub fn path(&self, p: PathId) -> ObResult<&Path> { todo!() }
    pub fn path_mut(&mut self, p: PathId) -> ObResult<&mut Path> { todo!() }
    /// 一次借出两个不同的表达式（R2 的常见需要），同一个 id 返回 OB_ERR_UNEXPECTED
    pub fn expr_pair_mut(&mut self, a: ExprId, b: ExprId) -> ObResult<(&mut ObRawExpr, &mut ObRawExpr)> { todo!() }
}
```

#### 2.8.2 创建与回收

```rust
/// 每种节点变体实现的构造 trait，由生成器为每个具体类实现（ObConstRawExpr、ObOpRawExpr、ObSelectStmt、AccessPath…）
pub trait IrNodeCtor { type Node; fn new_node(expr_type: ObItemType) -> Self::Node; }

impl QueryArena {
    /// C++ factory.create_raw_expr<T>(type, T*&)（:5082-5090）：先 try_check_status（每 1024 次调一次
    /// oblib::mem::check_mem_status()，3.12），再建节点。out 照 §2.1 保持输出参数。
    pub fn create_raw_expr<T: IrNodeCtor<Node = ObRawExpr>>(&mut self, expr_type: ObItemType, out: &mut Option<ExprId>) -> ObResult { todo!() }
    /// C++ create_raw_expr(ExprClass, ObItemType, ObRawExpr*&)（:5027-5029）
    pub fn create_raw_expr_by_class(&mut self, expr_class: ExprClass, expr_type: ObItemType, out: &mut Option<ExprId>) -> ObResult { todo!() }
    /// C++ 代理工厂（ObRawExprFactory(ObRawExprFactory&)，:5010-5017）的创建分支（:5043-5051）：
    /// 先查栈（溢出返回 OB_SIZE_OVERFLOW），节点的 is_called_in_sql 取 is_called_sql
    pub fn create_raw_expr_proxy<T: IrNodeCtor<Node = ObRawExpr>>(&mut self, is_called_sql: bool, expr_type: ObItemType, out: &mut Option<ExprId>) -> ObResult { todo!() }
    /// C++ ObStmtFactory::create_stmt<T>(T*&)
    pub fn create_stmt<T: IrNodeCtor<Node = ObStmt>>(&mut self, out: &mut Option<StmtId>) -> ObResult { todo!() }
    /// C++ ObStmtFactory::free_stmt（ob_stmt.cpp:207-226）：drop 节点、留墓碑，id 不复用（修订 A2）
    pub fn free_stmt(&mut self, s: StmtId) -> ObResult { todo!() }
    pub fn get_query_ctx(&mut self) -> ObResult<&mut ObQueryCtx> { todo!() }      // ObStmtFactory::get_query_ctx（ob_stmt.cpp:194-205）
    pub fn create_table_item(&mut self, item: TableItem) -> ObResult<TableItemId> { todo!() }
    pub fn create_log_op<T: IrNodeCtor<Node = ObLogicalOperator>>(&mut self, out: &mut Option<LogOpId>) -> ObResult { todo!() }
    pub fn create_path(&mut self, path: Path) -> ObResult<PathId> { todo!() }
}
```

#### 2.8.3 子作用域

```rust
impl QueryArena {
    /// RULEBOOK 0.6 的 qa.scope()：0.6 情况 (b)（SQL 编译期、建了 ObRawExprFactory 的临时上下文，
    /// 如 ob_transform_rule.cpp:379）和 33 处局部 ObRawExprFactory。
    /// f 返回后（不论成败）把作用域里新建的全部节点变成墓碑并 drop；对外层节点的修改保留。可以嵌套。
    pub fn scope<R>(&mut self, f: impl FnOnce(&mut QueryArena) -> ObResult<R>) -> ObResult<R> { todo!() }
}
```

#### 2.8.4 R1：签名约定与 `qa_signatures.tsv`

RULEBOOK R1 写的是"签名里出现编译期 IR 类型的函数，`self` 之后的第一个参数一律是 `qa: &mut QueryArena`"。对 IR 节点类**自己的方法**，这条写不出来：`self` 是从 `qa` 借出的节点，同时又要 `&mut qa`（借用检查器拒绝）。本文把 R1 细化为下面四种形态，全部只看声明加一条简单的函数体检查就能判定（修订 A3）：

| 形态 | 判定 | Rust 签名 | 调用写法 |
|---|---|---|---|
| `free` | 自由函数或静态成员函数，签名里有 IR 类型 | `fn f(qa: &mut QueryArena, 其余参数)` | `f(qa, …)` |
| `method` | 非 IR 节点类的成员函数（rewrite 规则、resolver、`ObLogPlan`、`ObJoinOrder` 等上下文类），签名或所属类里有 IR 类型 | `fn f(&mut self, qa: &mut QueryArena, …)`（const 方法 `&self`） | `x.f(qa, …)` |
| `node_accessor` | IR 节点类的成员函数，函数体只读写本对象的字段（含 id 字段本身，但不解引用它们去访问别的节点）；脚本按函数体判定，判不了的归 `node_assoc` | 留在节点 struct 上：`fn f(&self, …)` / `fn f(&mut self, …)`，不带 qa | `qa.expr(e)?.f(…)`；向下转型后 `qa.expr(e)?.as_op()?.f(…)` |
| `node_assoc` | IR 节点类的其余成员函数（会经指针访问别的节点，如 `same_as`、`replace_expr`、`extract_info`） | 关联函数 `fn f(qa: &mut QueryArena, this: ExprId, …)`（`StmtId` 等同理）；虚函数由生成的基类分派函数按变体 match 转发 | `ObRawExpr::f(qa, e, …)`、`ObColumnRefRawExpr::f(qa, e, …)` |

- **IR 类型**（判定集合）：`ObRawExpr`、`ObStmt`（不只 `ObDMLStmt`：90 个语句类都由 `ObStmtFactory` 建，`StmtId` 指任何语句，修订 A3）、`TableItem`、`ObLogicalOperator`、`Path` 及各自的派生类；`ObRawExprFactory`、`ObStmtFactory`、`ObLogOperatorFactory`；以及"上下文类型"：去掉工厂成员之后，成员里仍有上述类型的类（脚本对 `types.tsv` 的成员类型做闭包，如 `ObExprTypeCtx` 因 `raw_expr_`（`ob_sql_expression.h:239`）入选，`ObExecContext` 因为只持有工厂（`ob_exec_context.cpp:642-664`）而不入选）。
- 原来持有工厂的上下文类型去掉工厂成员，`qa` 与它并列传递；`exec_ctx.get_expr_factory()`/`get_stmt_factory()`（235 处）改成用传入的 `qa`。
- `qa: &mut QueryArena` 一律可变，不区分只读（R1）；需要同时读两个节点时先拷出 id 或字段（R2），或用 `expr_pair_mut`。
- **`migration/design/qa_signatures.tsv` 的列**：`sig_id`、`qualified_name`、`decl`（file:line）、`def`（file:line）、`owner_class`、`ir_types`（命中的 IR 类型，逗号分隔）、`form`（上表四种之一）、`this_id`（`node_assoc` 时的 id 类型）、`reason`（`node_accessor` 判定所依据的函数体行号）。脚本生成，随清单一起审查；译者照表写，调用方和被调方读同一行。

#### 2.8.5 R3：槽位写回的 setter

- C++ 返回 `T *&` 的访问函数（T 是 IR 类型）一律生成两个函数：读 `get_<名>(…) -> Option<Id>`（`node_accessor`，留在节点上），写 `qa.set_<名>(this, …, v)`；名字是 C++ 名去掉 `get_`。唯一例外照 R3：`get_param_expr(i)`（`ob_raw_expr.h:2000`）的写函数叫 `set_param`。
- IR 类型的字段被当作槽位传给 `T *&` 形参的（如 `TableItem::ref_query_`，`ob_dml_stmt.h:278`），按字段名生成 `qa.set_<字段名去掉尾下划线>(this, v)`，如 `qa.set_ref_query(t, v)`。
- 槽位在非 IR 容器里的（`exprs.at(i)` 当 `ObRawExpr *&` 传），拷出到局部、调用、写回 `exprs[i]`，不需要 setter。
- setter 由脚本生成在 `sql/src/generated/qa_slots.rs`（`impl QueryArena` 块）；写回处标 `SLOT(port): <槽位表达式>`。

```rust
impl QueryArena {
    pub fn set_param(&mut self, e: ExprId, i: i64, v: Option<ExprId>) -> ObResult { todo!() }   // get_param_expr(i) 的槽
    pub fn set_ref_stmt(&mut self, e: ExprId, v: Option<StmtId>) -> ObResult { todo!() }       // ObQueryRefRawExpr::get_ref_stmt()（:2850）
    pub fn set_ref_query(&mut self, t: TableItemId, v: Option<StmtId>) -> ObResult { todo!() }  // TableItem::ref_query_
    // 其余由生成器按上面的规则补全
}
```

### 2.9 表达式算子：`ExprOp`、`OpState`、注册表

#### 2.9.1 `ExprOp` trait（`sql::engine::expr::expr_op`，1d 写）

签名规则：C++ 虚函数的参数按通用规则翻译后，`self` 之后先放 `qa`（R1：签名里有 `ObRawExpr`，或有上下文类型 `ObExprTypeCtx`），再放 `st`（`OpState`；C++ 的 const 方法给 `&OpState`，`calc_result_type*` 按 RULEBOOK 给 `&mut OpState`），然后是 C++ 参数原序。默认方法体就是 C++ 基类的实现，写成 `base_<方法>` 自由函数（§2.13），由 `ob_expr_operator.cpp` 的译者翻译。方法名保留 C++ 写法（`calc_result_typeN` 用 `#[allow(non_snake_case)]`）；重载名按 §4 规则手算，以 `overloads.tsv` 为准。

```rust
#[allow(non_snake_case)]
pub trait ExprOp: Sync + 'static {
    /// C++ 构造函数 ObExprXxx(ObIAllocator&)：生成基类数据和本类数据的初值（注册表、工厂都调它）
    fn new_state(&self) -> OpState;
    fn get_type(&self, st: &OpState) -> ObItemType { st.base().type_ }                                      // :369（1 处覆写）
    fn reset(&self, st: &mut OpState) { base_reset(st) }                                                     // :370
    fn assign(&self, st: &mut OpState, other: &OpState) -> ObResult { base_assign(st, other) }               // :351（24 处）
    fn cg_expr(&self, qa: &mut QueryArena, st: &OpState, op_cg_ctx: &mut ObExprCGCtx, raw_expr: ExprId, rt_expr: &mut RtExpr<'static>) -> ObResult { base_cg_expr(qa, st, op_cg_ctx, raw_expr, rt_expr) } // :354-356（约 500 处）
    fn need_rt_ctx(&self, st: &OpState) -> bool { false }                                                    // :359（约 56 处）
    fn calc_result_type0(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, type_ctx: &mut ObExprTypeCtx) -> ObResult { todo!() } // :388
    fn calc_result_type1(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, type1: &mut ObExprResType, type_ctx: &mut ObExprTypeCtx) -> ObResult { todo!() }
    fn calc_result_type2(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, type1: &mut ObExprResType, type2: &mut ObExprResType, type_ctx: &mut ObExprTypeCtx) -> ObResult { todo!() }
    fn calc_result_type3(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, type1: &mut ObExprResType, type2: &mut ObExprResType, type3: &mut ObExprResType, type_ctx: &mut ObExprTypeCtx) -> ObResult { todo!() }
    /// C++ (ObExprResType &type, ObExprResType *types_array, int64_t param_num, …)：数组和个数合成切片
    fn calc_result_typeN(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, types_array: &mut [ObExprResType], type_ctx: &mut ObExprTypeCtx) -> ObResult { todo!() }
    // 按实参值推导类型的 5 个重载（:412-434），比上面多一个 arg_arrs；名字按 §4 重载规则：
    fn calc_result_type0_i_array(&self, qa: &mut QueryArena, st: &mut OpState, type_: &mut ObExprResType, type_ctx: &mut ObExprTypeCtx, arg_arrs: &mut dyn ObIArray<Option<ObObj<'_>>>) -> ObResult { todo!() }
    // calc_result_type1_i_array … calc_result_typeN_i_array 同理
    // 旧引擎（:437-473）：calc_result0/1/2/3/N、call、eval，ObObj 的生命周期取 ObExprCtx 的 arena
    fn calc_result0<'q>(&self, st: &OpState, result: &mut ObObj<'q>, expr_ctx: &mut ObExprCtx<'q>) -> ObResult { todo!() }
    // calc_result1/2/3/N、call、eval 同理
    fn need_charset_convert(&self, st: &OpState) -> bool { todo!() }                                        // :485
    fn get_cast_mode(&self, st: &OpState) -> ObCastMode { todo!() }                                        // :665（约 36 处）
    fn is_valid_for_generated_column(&self, qa: &mut QueryArena, st: &OpState, expr: ExprId, exprs: &[ExprId], is_valid: &mut bool) -> ObResult { todo!() } // :666
    /// DECLARE_SET_LOCAL_SESSION_VARS（:294-297，约 85 处）
    fn set_local_session_vars(&self, qa: &mut QueryArena, st: &OpState, raw_expr: ExprId, session: Option<&ObBasicSessionInfo>, local_vars: &mut ObLocalSessionVar) -> ObResult { todo!() }
    fn to_string(&self, st: &OpState, buf: &mut ObBuf) -> i64 { todo!() }                                  // :486-499
}
```

调用方怎么拿到 `op` 和 `st`（C++ `raw_expr->get_op()`，`ob_raw_expr.cpp:2156-2171` 懒建）：

```rust
impl QueryArena {
    /// 懒建 OpState（T_OP_ROW 不建，同 C++）；把它从节点里暂时取出，连同 qa 交给 f，返回后放回（出错也放回）。
    /// 节点上的状态字段是 Option<Box<OpState>>，对应 ObNonTerminalRawExpr::op_（ob_raw_expr.h:3293）。
    pub fn with_op<R>(&mut self, e: ExprId, f: impl FnOnce(&mut QueryArena, &'static dyn ExprOp, &mut OpState) -> ObResult<R>) -> ObResult<R> { todo!() }
}
```

#### 2.9.2 `OpState` 的生成契约

- 生成器读 `types.tsv` 和各单元头文件，输出 `sql/src/generated/op_state.rs`：
  - `pub enum OpState`：每个注册的叶子类一个变体，变体名 = 类名去掉 `ObExpr` 前缀（RULEBOOK 0.6），载荷是该类的状态 struct。
  - 状态 struct：基类数据保留 C++ 名 `ObExprOperator`（`ob_expr_operator.h:730-767` 的 16 个成员，`raw_expr_` 改成 `Option<ExprId>`，`input_types_` 是 `Box<[ObFuncInputType]>`，`name_` 是 `&'static [u8]`）；有自有数据成员的类（叶子约 38 个，中间类 4 个：`ObRelationalExprOperator` `:1088`、`ObSubQueryRelationalExpr` `:1444`、`ObArithExprOperator` `:1595`、`ObMinMaxExprOperator` `:1952`）各有一个 `<类名>State { pub base: <父类的状态 struct>, <自有成员> }`（命名见修订 A5）；没有自有成员的类直接用最近祖先的状态 struct。容器成员（如 `ObExprUDF` 的 `ObSEArray`，`ob_expr_udf.h:212-215`）按 RULEBOOK §2.5 的"IR 节点"类别译成 `SmallVec`/`Vec`（节点在 `IdVec` 里，可以需要 Drop，P-14）。
  - 访问函数：`st.base()`/`st.base_mut()` → `&ObExprOperator`；`st.as_<变体蛇形名>()`/`_mut()` → `ObResult<&<类名>State>`；中间类的 `st.as_arith_expr_operator()` 等接受全部派生变体（is-a，同 2.12）。
  - `st.op() -> &'static dyn ExprOp`：按变体返回对应的零大小类型。
- 零大小类型：生成器为层次里的**每个**类（含中间类、声明在 query 头文件里的类）在 `sql/src/generated/expr_ops.rs` 生成 `#[derive(Clone, Copy, Default)] pub struct ObExprXxx;`。译者在方法体所在的单元写固有方法 `impl ObExprXxx { pub fn cg_expr(&self, qa, st, …) -> ObResult {…} }`，名字和 C++ 相同；静态成员函数也写成关联函数，调用照 C++ 写 `ObExprConcat::calc(…)`。
- `impl ExprOp for ObExprXxx`：全部由生成器写在 `sql/src/generated/expr_op_impls.rs`。每个 trait 方法转发到最近的定义：本类覆写了就调本类的固有方法；否则调最近一个覆写了它的祖先（`ObArithExprOperator.calc_result_type2(…)`）；都没有就用 trait 默认（基类）。C++ 里显式调祖先的 `ObArithExprOperator::calc_result_type2(…)` 照写成调祖先零大小类型的固有方法。
- 一个类同时作为零大小类型（C++ 名）和状态 struct（`<类名>State`）出现，不冲突。

#### 2.9.3 注册表的生成契约（`sql/src/generated/expr_op_registry.rs`）

- 输入：`src/sql/engine/expr/ob_expr_operator_factory.cpp` 的 `register_expr_operators`（`:511-`）里 528 行 `REG_OP(Class)` 和 1 行 `REG_SAME_OP(T_FUN_SYS_SHA, T_FUN_SYS_SHA, N_SHA1, i)`（`:827`），**按出现顺序**；`#if defined(ENABLE_DEBUG_LOG) || !defined(NDEBUG)` 包着的 `REG_OP(ObExprErrno)`（`:840-843`）生成为 `#[cfg(any(feature = "enable_debug_log", debug_assertions))]`。计数命令：`grep -cE '^\s*REG_OP\(' …`（528），复核 C22 已确认。
- 输出：

```rust
/// C++ OP_ALLOC[T_MAX_OP]（:429）：以 ObItemType 值为下标
pub static OP_ALLOC: LazyLock<Box<[Option<&'static dyn ExprOp>]>> = LazyLock::new(|| todo!());
/// C++ NAME_TYPES[EXPR_OP_NUM]（:465）：注册顺序；name_/type_/is_internal_ 取自 op.new_state() 的基类数据（同 C++ 构造一个实例读出）
pub static NAME_TYPES: LazyLock<Box<[NameType]>> = LazyLock::new(|| todo!());
/// 组合根在 C++ 调用 register_expr_operators 的位置调用：force 上面两个 LazyLock
pub fn register_expr_operators() { todo!() }
```

- `ObExprOperatorFactory` 照常翻译，签名改为：`alloc(&mut self, type_: ObItemType, out: &mut Option<(&'static dyn ExprOp, OpState)>) -> ObResult`（C++ `:1063-1080`：类型越界 `OB_INVALID_ARGUMENT`，未注册 `OB_ERR_UNEXPECTED`；`next_expr_id_` 非空时给 `base.id_` 赋值并加一）；`get_type_by_name` 照 `:468-487` 线性扫描 `NAME_TYPES`、按 C locale 忽略大小写比较、先到先得。

### 2.10 ExtraInfo 与 ExprOpCtx

#### 2.10.1 `ObIExprExtraInfo`（生成在 `query/src/generated/extra_info.rs`）

```rust
/// C++ ObIExprExtraInfo（ob_i_expr_extra_info.h）的 19 个子类合成封闭 enum（RULEBOOK 0.6、F22）；
/// enum 沿用 C++ 基类名。RtExpr::extra_info_ 按值持有它，所以 enum 和变体 struct 都要在 query（8.3）。
pub enum ObIExprExtraInfo {
    CalcPartitionBaseInfo(CalcPartitionBaseInfo),       // ob_expr_calc_partition_id.h:101
    EnumSetInfo(ObEnumSetInfo),                          // ob_expr_type_to_str.h:77
    ExprCollectionConstructExtraInfo(ObExprCollectionConstructExtraInfo),   // 嵌套类 ObExprCollectionConstruct::ExtraInfo（:59）
    ExprObjAccessExtraInfo(ObExprObjAccessExtraInfo),    // ob_expr_obj_access.h:62
    ExprSubQueryRefExtraInfo(ObExprSubQueryRefExtraInfo),// ob_expr_subquery_ref.h:50
    // … 共 19 个。命名（ExtraInfo、ExprOpCtx 共用）：类型名 = C++ 类名；嵌套类的内层名以 Ob 开头时用内层名
    // （ObExprInOrNotIn::ObExprInCtx → ObExprInCtx），否则用外层名 + 内层名（ObExprSubQueryRef::ExtraInfo →
    // ObExprSubQueryRefExtraInfo）；变体名 = 类型名去掉 Ob 前缀。以 types.tsv 为准。
}
impl ObIExprExtraInfo {
    pub fn type_(&self) -> ObItemType { todo!() }
    pub fn deep_copy(&self) -> ObResult<ObIExprExtraInfo> { todo!() }   // 各变体的 deep_copy（计划深拷贝用）
    // 生成的 as_<变体蛇形名>()/as_<…>_mut() -> ObResult<&T>：不匹配返回 OB_ERR_UNEXPECTED 并记 ERROR
}
/// C++ ObExprExtraInfoFactory（ob_expr_extra_info_factory.cpp:45-112）：35 个 ObItemType → 变体构造函数的表
pub fn is_registered(type_: ObItemType) -> bool { todo!() }
pub fn alloc_extra_info(type_: ObItemType, out: &mut Option<Box<ObIExprExtraInfo>>) -> ObResult { todo!() }
```

- 子类的 `serialize/deserialize/get_serialize_size` 不再需要（计划不序列化，0.6）：译者跳过，原位置留 `// not ported: plan serialization removed (RULEBOOK 0.6)`。
- C++ `static_cast<ObEnumSetInfo *>(expr.extra_info_)`（34 处，如 `ob_expr_column_conv.cpp:737`）→ `e.extra_info_.as_deref().ok_or(OB_ERR_UNEXPECTED)?.as_enum_set_info()?`。

#### 2.10.2 `ObExprOperatorCtx`（生成在 `sql/src/generated/expr_op_ctx.rs`）

```rust
/// C++ ObExprOperatorCtx（ob_expr_operator.h:166-196）的 25 个子类（query 4 个、sql 21 个）合成封闭 enum。
/// 存在 ObExecContext::expr_op_ctx_store_: Box<[Option<Box<ObExprOperatorCtx>>]>，以 RtExpr::expr_ctx_id_ 为下标。
pub enum ObExprOperatorCtx {
    ExprInCtx(ObExprInCtx),                 // query/engine/expr/ob_expr_in.h:184
    ExprLikeContext(ObExprLikeContext),     // ob_expr_like.h:94
    ExprColumnConvCtx(ObExprColumnConvCtx), // ob_expr_column_conv.h:107
    // … 共 25 个
}
/// query 侧（下压过滤器，ob_pushdown_filter.h:1339 的 runtime_filter_ctx_）只经这 6 个虚函数访问（:173-194）
pub trait ObExprOperatorCtxVirt {
    fn to_string(&self, buf: &mut ObBuf) -> i64;
    fn dynamic_disable(&mut self) -> bool;
    fn collect_monitor_info(&mut self, filtered_rows_count: i64, check_rows_count: i64, total_rows_count: i64);
    fn collect_sample_info(&mut self, filter_count: i64, total_count: i64);
    fn need_reset_in_rescan(&mut self) -> bool;
    fn is_data_version_updated(&mut self, old_version: i64) -> bool;
}
impl ObExprOperatorCtxVirt for ObExprOperatorCtx { /* 按变体 match */ }
```

- `ObExecContext::create_expr_op_ctx(op_id, ctx)`（`ob_exec_context.cpp:600-618`）：`op_id >= expr_op_size_` 或表未初始化返回 `OB_INVALID_ARGUMENT`，已存在返回 `OB_INIT_TWICE`；Rust 签名 `create_expr_op_ctx(&mut self, op_id: u64, ctx: ObExprOperatorCtx) -> ObResult<&mut ObExprOperatorCtx>`，`get_expr_op_ctx(&mut self, op_id: u64) -> Option<&mut ObExprOperatorCtx>`；C++ 显式析构（`:506-509`）交给 Drop。
- 下压过滤器原来持有 `ObExprOperatorCtx *`（`ob_pushdown_filter.h:1339`），改成存 `expr_ctx_id_`，用时经 `ObExecContextVirt::get_expr_op_ctx_virt(id)` 取。

### 2.11 下压过滤器的边界

RULEBOOK 0.6：query 定义 trait `PushdownFilter`，方法清单由 `seams.tsv` 给出，sql 实现，storage 只持有 `Box<dyn PushdownFilter>` 或经 `RtExprId` 加 `EvalFn` 访问。类层次在 `src/query/api/query/engine/basic/ob_pushdown_filter.h`：`ObPushdownFilterExecutor`（`:666`）← `ObPhysicalFilterExecutor`（`:876`）← `ObBlackFilterExecutor`（`:905`）/ `ObWhiteFilterExecutor`（`:1121`）←（`ObSemiStructWhiteFilterExecutor` `:1191`、`ObDynamicFilterExecutor` `:1240`）；`ObAndFilterExecutor`（`:1205`）、`ObOrFilterExecutor`（`:1222`）；节点类 `ObPushdownFilterNode`（`:299`）等。方法体在 `src/sql/engine/basic/ob_pushdown_filter.cpp`（3017 行）。

storage 调用的方法（候选清单，按"含 filter/executor 的行里出现的方法名"启发式计数，附录 A 的脚本；括号是次数和一例）：

| 方法 | storage 调用 | 例 |
|---|---|---|
| `execute` / `filter` / `filter_datum` | 18 / 17 / 3 | `ob_storage_util.cpp:292`、`ob_micro_block_reader.cpp:740` |
| `init_evaluated_datums` / `prepare_skip_filter` / `can_skip_filter` | — / — / 12 | `ob_micro_block_reader.cpp:684` |
| `is_filter_black_node` / `is_filter_white_node` / `is_filter_dynamic_node` / `is_logic_op_node` / `is_filter_node` | 3 / — / 4 / 4 / — | `ob_dict_decoder.cpp:671`、`ob_sstable_index_filter.cpp:131` |
| `get_filter_node` / `get_type` / `get_op_type` | 5 / 5 / 35 | `ob_micro_block_row_scanner.cpp:502`、`ob_integer_base_diff_decoder.cpp:222` |
| `get_col_offsets` / `get_col_count` / `get_col_params` / `get_default_datums` | 7 / 7 / 7 / 4 | `ob_micro_block_row_scanner.cpp:471-504` |
| `get_datums` / `null_param_contained` / `exist_in_set` / `get_filter_val_meta` / `judge_greater_or_less` | 44 / 17 / 7 / 3 / 2 | `ob_integer_base_diff_decoder.cpp:339`、`ob_micro_block_decoder.cpp:1577` |
| `is_padding_mode` | 40 | `ob_micro_block_row_scanner.cpp:505` |
| `get_filter_bool_mask` / `set_filter_uncertain` / `is_filter_constant` / `is_filter_always_false` | 7 / 3 / 4 / 5 | `ob_index_block_row_struct.h:344-366` |
| `get_result` / `get_childs` / `get_child_count` / `get_op` / `get_allocator` | 5 / 3 / 3 / 3 / 4 | `ob_micro_block_row_scanner.cpp:707`、`ob_sample_filter.cpp:639` |
| `get_datums_from_column` / `get_filter_realtime_statistics` / `clear` | 2 / 5 / 3 | `ob_micro_block_row_scanner.cpp:488`、`ob_where_optimizer.cpp:135` |

`get_datums()` 返回的白名单参数是 `ObDatum` 数组：经 trait 给出时按 2.1 的形态，返回 `&[ObDatum]` 加对应的 `&StoreTable`，或直接返回视图数组，由 seam 表定。

### 2.12 向下转型访问函数

- RULEBOOK §2.13、R9：`static_cast<D*>(b)`/`dynamic_cast` → `as_<变体蛇形名>()` / `as_<…>_mut()`，返回 `ObResult<&D>`/`ObResult<&mut D>`。接受集 = 目标类及其全部派生类对应的变体，由脚本从类层次生成进 `types.tsv`（`is_a_set` 列），访问函数生成在 `sql/src/generated/ir_enums.rs`（OpSpec 的在 `query` 或 `sql`，随 enum 所在 crate）。只有 is-a 不成立才返回 `OB_ERR_UNEXPECTED` 并记 ERROR。
- 变体名 = 类名去掉 `Ob` 前缀和层次后缀（`RawExpr`/`Stmt`/`Spec`；LogOp 去掉的是前缀 `Log`），再转蛇形；Path 层次不在 RULEBOOK 的后缀表里，保留全名。
- 组合式表示：派生类的数据 struct 以 `base` 字段内嵌父类（§2.13），`as_<祖先>()` 对派生变体返回其中内嵌的那一层，如 `ObUDFRawExpr` 变体的 `as_op()` 返回 `&udf.base.base`。

| 层次 | 例子（接受集） | 依据 |
|---|---|---|
| RawExpr（30 个类：基类加 29 个子类，其中 `ObTerminalRawExpr`/`ObNonTerminalRawExpr` 是抽象中间类，`ObWinFunRawExpr` 另继承 `ObWindow`；`ExprClass` 19 个值，`ob_raw_expr.h:1770-1791`） | `as_op()`：`ObOpRawExpr`（`:3298`）及派生的 `ObSysFunRawExpr`（`:3868`）、`ObCollectionConstructRawExpr`（`:3934`）、`ObObjectConstructRawExpr`（`:3984`）、`ObUDFRawExpr`（`:4087`）、`ObPLIntegerCheckerRawExpr`（`:4309`）、`ObPLGetCursorAttrRawExpr`（`:4346`）、`ObPLSQLCodeSQLErrmRawExpr`（`:4371`）、`ObPLSQLVariableRawExpr`（`:4396`）、`ObCallParamRawExpr`（`:4422`）、`ObPLAssocIndexRawExpr`（`:4448`）、`ObObjAccessRawExpr`（`:4494`）。注意 `is_op_expr()` 只认 `EXPR_OPERATOR`（`:1848`），IFNULL/NVL 是 `EXPR_SYS_FUNC` 却必须被 `as_op()` 接受（`ob_transform_simplify_expr.cpp:1254`，复核 C15）。`as_const()`：`ObConstRawExpr`（`:2550`）、`ObUserVarIdentRawExpr`（`:2693`）、`ObExecParamRawExpr`（`:2730`）。`as_terminal()`/`as_non_terminal()`：抽象中间类，接受各自的全部派生 | `class_hier` 由脚本算 |
| Stmt（90 个类） | `as_dml()`：`ObDMLStmt`（`dml/ob_dml_stmt.h:561`）及 `ObSelectStmt`、`ObDelUpdStmt`（`:327`）、`ObInsertStmt`、`ObUpdateStmt`、`ObDeleteStmt`…；`as_del_upd()`：`ObDelUpdStmt` 及其派生 | |
| LogOp（`LOG_OP_DEF` 37 项，`ob_log_operator_factory.h:18-`） | `as_del_upd()`：`ObLogDelUpd` 及 `ObLogDelete`、`ObLogInsert`、`ObLogUpdate`… | |
| Path（10 个类，`ob_join_order.h:331-1237`） | `as_access_path()`：`AccessPath`（`:559`）及 `IndexMergePath`（`:785`） | |
| OpSpec（61 个 `REGISTER_OPERATOR`，`ob_operator_reg.h`） | `as_px_receive()`：`ObPxReceiveSpec` 及 `ObPxFifoReceiveSpec`、`ObPxMSReceiveSpec`、`ObPxCoordSpec`、`ObPxFifoCoordSpec`、`ObPxOrderedCoordSpec`、`ObPxMSCoordSpec`；`as_join()`：`ObJoinSpec` 及 `ObBasicNestedLoopJoinSpec`、`ObNestedLoopJoinSpec`、`ObHashJoinSpec`、`ObMergeJoinSpec`；`as_table_modify()`：`ObTableModifySpec` 及 Insert/Update/Delete/Replace/Lock/InsertUp、`ObPxMultiPart*Spec`；`as_group_by()`：`ObGroupBySpec` 及 Scalar/Hash/Merge | |
| ExtraInfo、ExprOpCtx、OpState | 同一规则，见 2.9、2.10 | |

## 3. 语义

### 3.1 DataRef 与存储表

1. **编码**：`store == 0` 是 NULL，`u32::MAX` 是 SELF，其余低 20 位槽号、高 12 位代号；槽号 0 和 `0xFFFFF` 保留。注销时代号加 1，槽回到空闲表；过期的 DataRef（代号不符）解析失败。计划常量区占槽 1..=P、代 0，不注销。
2. **解析**：`bytes(r, 0)` 不检查 r，返回空切片（C++ 只有 `len_ > 0` 才解引用，如 `ob_datum.cpp:136-137`、`:172-175`）。`len > 0` 时 NULL、SELF（没有基址）、未知或过期的 store、`off + len` 越界，都是 C++ 的非法内存访问：记 ERROR 日志和调用栈，然后 `ob_abort()`，与 `DEF_TO_STRING` 里的 `OB_ASSERT(NULL != ptr_)`（`ob_datum.cpp:137`）同一态度。需要判断的调用方用 `try_bytes`（`OB_ERR_UNEXPECTED`）。
3. **登记规则**：任何会被存储形态 `ObDatum` 指向的内存，都必须是表项。因此：参数存储里的字符串在建 datum 参数时拷进参数 arena（C++ `from_objparam` 浅指 ObObj，`ob_physical_plan_ctx.cpp:107-131`）；行存储的块由存储表拥有（算子只存 store id，RULEBOOK 0.7 (b)）；storage 扫描把微块的 `KvBlockHandle` 或 IO 缓冲登记进来，推进到下一批之前注销（C++ 的 datum 也只在下一次 `get_next_rows` 之前有效）。
4. **注销与 reset 都要 `&mut StoreTable`**（RULEBOOK 0.6）：这时不可能有存活的视图。

### 3.2 datum 的值语义（与 C++ 一致的点）

1. 布局：12 字节，`r` 在 0..8、`desc` 在 8..12（`ob_datum.h:169`，`ObDatumPtr` 是第一个基类）；`len_` 占低 29 位、`flag_` 2 位、`null_` 最高位（`:141-149`，Clang 小端位域从低位分配）。`OB_MAX_LONGTEXT_LENGTH = 2^29 - 1`（`ob_define.h:1420`）正是 `len_` 的上限。
2. `set_xxx` 一律整体覆盖 `pack_`（如 `set_int` 的 `pack_ = sizeof(int64_t)`，`:294`），所以同时清掉 `null_` 和 `flag_`。`set_null()` 置 `len_=0, null_=1, flag_=NONE`（`:153`）；`set_none()` 只清 null 和 flag（`:152`）；`set_ext()` 置 `len_=16`（`:156`）。`set_otimestamp(time_us, time_desc)` 不设 `pack_`（`:323-327`）；全仓没有调用者（`rg 'set_otimestamp\(' src` 只命中 `ob_datum.h` 里的两处定义），照抄，不另标。
3. `binary_equal`（`:185-198`）：null 性不同为假；都 null 为真；否则 `pack_` 相等且前 `len_` 字节相等。
4. `checksum`（`:796-803`）：先对 4 字节 `pack_`（本机字节序）做 `ob_crc64_sse42`，`len_ > 0` 再对载荷做。
5. 定长读取在 C++ 里不看 `len_`（`*int_`）。Rust 视图只借 `len_` 个字节：`len_` 不小于类型宽度时与 C++ 相同；更短时只读 `len_` 个字节、其余补 0。C++ 在这种情况下读的是越界或未初始化的字节，属于 RULEBOOK 0.1.4 列出的未定义行为，标 `FIXED(port): 定长读取越过 len_`（只写一处，在 datum 模块里）。
6. `from_obj/to_obj`（`:737-1025`）：映射表逐项照抄，包括 `to_obj` 遇 null 时保留 collation（`:933-939`）、decimal int 的 `scale < 0` 返回 `OB_ERR_UNEXPECTED`（`:900-903`、`:943-946`）、`from_obj` 遇未知类型返回 `OB_ERR_UNEXPECTED`（`:879-883`）、`from_storage_datum` 遇 ext 返回 `OB_INVALID_ARGUMENT`（`:766-768`）。
7. `get_reserved_size`（`ob_datum.cpp:86-124`）：表驱动，decimal int 且 `prec > 0` 时按宽度取 4/8/16/32/64；`type >= OBJ_DATUM_MAPPING_MAX` 记 ERROR、返回 0。
8. 序列化（`ob_datum.cpp:168-200`）：先 `OB_UNIS_ENCODE(pack_)`，再写 `len_` 个原始字节；反序列化零拷贝指向输入缓冲。
9. `DEF_TO_STRING`（`ob_datum.cpp:126-166`）格式照抄；`ptr: %p` 处打印 `r.to_bits()` 的十六进制。地址本来每次运行都不同，日志也不参与比较，不需要偏差项。

### 3.3 行存储与 SELF

- C++ 把行内 datum 的 `ptr_` 改成相对偏移再落盘（`deep_copy_unswizzling`，`ob_chunk_datum_store.h:80-95`；`StoredRow::unswizzling/swizzling`，`:147-149`）。Rust 的行内 datum 一直是 `DataRef::self_rel(off)`，行存储视图读它时用块的 store id 补全；落盘、读回的字节格式与 C++ 的 unswizzled 形态对应（偏移在原 `ptr_` 的位置），临时文件不跨进程，不要求逐字节相同。

### 3.4 本地结果与拷贝

- 所有"存储形态浅指非表项内存"的 C++ 写法（`from_obj` 的字符串、`eval_question_mark_func` 的 `from_obj`（`ob_expr.cpp:776`）、`eval_assign_question_mark_func` 的 `expr_datum = datum_param.datum_`（`:835`）、`ObExprStrResAlloc` 交给库函数的结果），在 Rust 里都多一次拷贝，进入 `ObDatumBuf` 或结果缓冲。结果字节相同；唯一可能的差别是 C++ 依赖"别名后源被改写、datum 跟着变"，这种依赖在 C++ 里本身是缺陷，清单没有发现实例，遇到时由译者标 `TODO(port)`。

### 3.5 求值（与 `ob_expr.h`、`ob_expr.cpp` 逐点一致）

1. `eval`（`ob_expr.h:1039-1070`）：批量结果走"批内求一行"；非批量时只有 `eval_func_` 非空且 `evaluated_` 为假才求值：`need_stack_check_` 时先 `check_stack_overflow`（0.8），把 datum 的 r 指回 res buf，调用；成功置 `evaluated_`，失败 `set_null()` 并返回错误。`eval_func_` 为空（常量、列引用）直接返回帧里的 datum。
2. 批内求一行（`ob_expr.cpp:612-659`）：`projected_` 或无函数时不求；`evaluated_` 为假时先清 eval flags、把 batch_size 个 datum 的 r 指回 res buf、把 `pack_` 清零（`clear_datum_descriptors`，`:594-600`）、置 `evaluated_`/`cnt_`/`point_to_frame_`/`notnull_`；该行 flag 未置时求值，成功置 flag，失败置 null；结果为 null 时清 `notnull_`。
3. `eval_batch`（`ob_expr.h:1072-1093`）：非批量表达式求一次，若整批都被 skip 则吞掉错误；`projected_` 或无批量函数时不做；`size > 0` 时 `do_eval_batch`（`ob_expr.cpp:661-709`）：判断是否还有"未 skip 且未求值"的行（`bit_op_zero`，`:677-679`），首次求值时重置 flags/指针/`pack_` 并置 info，失败时整批 datum 置 null。
4. `expr_default_eval_batch_func`（`ob_expr.cpp:711-753`）：逐行调 `eval_func_`，每行置 flag（不论成败），记录是否出现 null；会话开启诊断时把错误改记为警告并把该行置 null（`:734-745`）。
5. `eval_param_value`：按下标顺序求值全部 `arg_cnt_` 个参数，遇错即停（`:1007-1022`）；`eval_args::<N>` 同样求值全部参数，只是借出前 N 个。
6. `EvalFn` 的结果参数是本地 `ObDatumBuf`：帧模块在函数返回后落盘。C++ 在函数体内直接写帧，函数返回前读回自己的帧 datum 的写法（经 `locate_expr_datum` 等）译成读 `expr_datum`（6.2 节规则）。落盘规则同 3.6。
7. 计划共享时运行期不写计划：`args_` 不可变；`eval_enumset_with_arg0` 取代 C++ 临时改写 `args_[0]`（`ob_expr_column_conv.cpp:741-746`），标 `FIXED(port): 并发执行时改写共享计划的 args_[0]（数据竞争）`；有 `expr_ctx_id` 的分支照 C++（`:747-776`），禁止逐行克隆 RtExpr（F18）。

### 3.6 结果落盘与 DRB

1. Local：长度 ≤ `res_buf_len_` 时写进 res buf 的第 `idx` 格（`frame + res_buf_off_ + res_buf_len_ * (batch_idx_mask_ & idx)`，`ob_expr.h:436-442`），否则同 3.7 的 DRB；r 指向写入处。
2. Ref：浅写 r 和 desc。Heap：拷进 DRB。null：`set_null`。
3. 非批量表达式的 idx 被 `batch_idx_mask_` 屏蔽为 0（`:524-527`）。

### 3.7 字符串结果缓冲（`get_str_res_mem`，`ob_expr.h:431-442`；`alloc_str_res_mem`，`ob_expr.cpp:286-315`）

1. `size ≤ res_buf_len_`：用 res buf 的第 idx 格。
2. 否则看第 idx 个 DRB 头（`frame + dyn_buf_header_offset_ + 16 * idx`）：`len_ ≥ size` 复用 `mem_`；不够时从结果 arena 分配 `next_pow2(size)`，首次置 `magic_`；**旧内存不释放**（`:304-305` 的注释：旧内存可能仍被引用）。`next_pow2(size)` 超过 `UINT32_MAX` 时 C++ 只在局部置 `OB_INVALID_ARGUMENT`、对外返回 NULL（`:299-300`），调用方据 NULL 报 4013；这是 C++ 本来共用的哨兵值，按 RULEBOOK §2.1 的错误恢复规则照样保留：`get_str_res_mem_with` 返回 `OB_ALLOCATE_MEMORY_FAILED`。
3. 类型不支持 DRB（`ObDynReserveBuf::supported`，`:297-309`）时 C++ 记 ERROR 并返回 NULL；Rust 同样记 ERROR，返回 `OB_ALLOCATE_MEMORY_FAILED`（同上）。
4. 重叠：C++ 在 `common_copy_string(_zf)` 里用 MEMMOVE（`ob_datum_cast.cpp:463`、`:487` 的注释"out_ptr may overlap with src"），`deep_copy_datum` 先浅拷一份源（`ob_expr.h:1108-1109`）。Rust：`get_str_res_mem_with` 发现源与目标区间重叠时先把源拷到临时缓冲；`deep_copy_datum` 用 `copy_bytes`（`copy_within`）。结果与 C++ 相同。

### 3.8 帧

1. 帧数组的顺序是 [常量帧 | 参数帧 | 动态帧 | datum 帧]（`ob_expr_frame_info.cpp:231-262`）；常量帧所有执行共享；动态帧建好后每个 datum `set_null`（`:265-274`）。
2. C++ 只清零 `[zero_init_pos_, +zero_init_size_)`（`:215-226`），其余字节未初始化；Rust 新帧整块清零。读未初始化字节在 C++ 里是未定义行为（"legacy batch paths can touch slots"，`ob_expr.cpp:583-590`），标 `FIXED(port): 帧未清零部分被读`。
3. 参数帧（`ob_physical_plan_ctx.cpp:305-396`）：按 `MAX_FRAME_SIZE / 16` 分帧，原始参数之后的容量按 2 的幂对齐末帧，扩容时拷贝末帧旧内容；`extend_param_frame` 把新参数的 datum 写进帧并清 `evaluated_`。帧换了内存时用 `set_frame_store` 更新映射。
4. 常量帧（`ob_static_engine_expr_cg.cpp:1063-1105`）：先 `ob_write_obj` 深拷贝常量，再 `from_obj`；`len_ == 0` 时 r 置 NULL（C++ `ptr_ = NULL`）；LOB 走 `ob_adjust_in_memory_lob_datum`。Rust 用 `ObPlanConstStoreBuilder`：字符串写进常量字节块，帧内 datum 指向它（常量帧外），定长值写进帧内 res buf。
5. 计划深拷贝（C++ `assign`，`ob_expr_frame_info.cpp:35-193`）：C++ 要把帧内指针按偏移重定位、帧外指针单独深拷贝、再用指针减法换算参数下标；Rust 的帧里只有 DataRef、参数本来是 id，所以按字节复制即可，只有 `extra_info_` 要逐个 `deep_copy`。

### 3.9 临时 arena 与批次信息

1. C++：`TempAllocGuard` 共享 exec ctx 的 `eval_tmp_allocator_`，没有守卫活着时下一次 `get_reset_tmp_alloc()` 才 reset（debug 构建总是 reset，release 构建只在 `used() > OB_MALLOC_MIDDLE_BLOCK_SIZE` 时 `reset_remain_one_page`，`ob_expr.h:260-272`）。
2. Rust：每个守卫自带 Arena，守卫 drop 即释放。分配出的内存只能在守卫活着时使用（借用检查保证），所以 C++ 里"守卫结束后继续读临时内存"的写法（把临时内存设成结果）编译不过，译者必须拷进结果缓冲；这类 C++ 写法依赖临时 arena 恰好没被 reset，属于缺陷。内存占用的差别只影响 11049 的计量（A.4 `query-mem-limit`）。
3. `BatchInfoScopeGuard::scope` 返回时恢复三个批次字段（`:197-202`），出错也恢复。

### 3.10 ObObj

1. `ObObj<'a>` 的每个 getter/setter 的结果与 C++ 相同；`ObObjMeta` 的 4 字节布局和方法不变。
2. `hash`、`hash_murmur`、`varchar_*_hash`（`ob_object.h:1962-1971`、`:3286-3315`；每个类型的实现在 `ob_obj_funcs.h`）逐字节等于 C++：hash 分区的落点取决于 `hash_murmur`（RULEBOOK 0.11.5），配金标准测试。
3. 序列化与 C++ 逐字节相同；字符串类反序列化零拷贝指向输入（`ObObj<'de>`）。
4. `deep_copy`（`:1944`）：缓冲不够返回 `OB_BUF_NOT_ENOUGH`（同 `ObDatum::deep_copy`，`ob_datum.h:370-371`）；extend 类型只复制句柄值。

### 3.11 IR

1. id：五种 id 都是 `NonZeroU32`，值 = 下标 + 1；同一条语句内永不复用（RULEBOOK 0.6）。
2. 截断：`scope` 结束时把新建节点变墓碑并 drop；外层节点里留下的指向它们的 id，再访问时返回 `OB_ERR_UNEXPECTED` 并记 ERROR（RULEBOOK 0.6、R8；S-25 的问题因此不出现）。
3. `free_stmt`（C++ 只对 `ObSelectStmt`，`ob_stmt.cpp:207-226`：从 store 摘下、放进 free list、析构；`create_stmt` 先复用 free list，`:228-253`）：Rust drop 并留墓碑，下一个 `create_stmt` 取新 id。C++ 复用的只是内存，复用后旧指针静默指向新语句；Rust 的旧 id 访问报错（修订 A2）。
4. `create_raw_expr`（`ob_raw_expr.h:5034-5090`）：先 `try_check_status`（自增计数，逢 1024 的倍数调 `check_mem_status()`，超限 11049），再分配、初始化位集（`get_init_err`）、登记；失败时不留节点。代理分支（`:5043-5051`）先查栈，再在外层工厂建，并设 `is_called_in_sql`。Rust 只有一个 qa、一个计数器，C++ 里代理工厂和外层工厂各自计数，所以检查时机可能不同（A.4 `query-mem-limit`）。
5. `get_op`（`ob_raw_expr.cpp:2156-2171`）：懒建；`T_OP_ROW` 不建；建失败只记 WARN 并返回 NULL（调用方据此报错）。`with_op` 对 `T_OP_ROW` 返回 `OB_ERR_UNEXPECTED`，C++ 调用方遇到 NULL 本来就报错。
6. 按地址迭代的 3 处改成按插入顺序（`IdVec::iter` 或旁边存的 `Vec<Id>`），标 `DIVERGE(port): addr-order`（RULEBOOK 0.6、A.4）。

### 3.12 注册表与 ExtraInfo

1. `NAME_TYPES` 的顺序 = REG_OP 的书写顺序；`REG_SAME_OP` 先检查原类型已注册（`ob_expr_operator_factory.cpp:449-463`）；`get_type_by_name` 跳过 `T_MIN_OP..T_MAX_OP` 之外的项、比较长度和忽略大小写的内容、先到先得（`:468-487`）。
2. ExtraInfo：35 个类型注册（`ob_expr_extra_info_factory.cpp:78-112`），`is_registered(type)` 的结果与 C++ 相同；计划深拷贝时只对注册的类型复制 `extra_info_`（`ob_expr_frame_info.cpp:89-96`）。

## 4. unsafe

| 位置 | 处数 | 用途 | 安全论证要点 |
|---|---|---|---|
| `frame::bitvec::to_bit_vector` | 1 | `&[u8]` → `&ObBitVector` | `ObBitVectorImpl<W>` 是 `#[repr(transparent)]`，唯一非零大小字段是 `[u8]`，`PhantomData` 不占空间，所以两者的指针元数据（长度）和布局相同；生命周期继承输入切片。配一条 `const` 断言比对 `size_of_val`。 |
| `frame::bitvec::to_bit_vector_mut` | 1 | `&mut [u8]` → `&mut ObBitVector` | 同上；独占性来自输入的 `&mut`。 |
| `frame::bitvec::new_bit_vector` | 1 | `Box<[u8]>` → `Box<ObBitVector>` | 同上；`Box::from_raw` 用的指针来自同一布局的 `Box::into_raw`，释放时布局相同（u8，对齐 1）。 |
| datum 模块、object 模块、存储表、帧、`QueryArena`、`IdVec`、向量格式 | 0 | — | 全部按值编解码（`from_ne_bytes`/`to_ne_bytes`）和带边界检查的切片；分割借用用 `split_at_mut`、`get_disjoint_mut`（1.86 起稳定，1.98.1 可用）。 |

- 估计共 **3 处**（RULEBOOK 0.6 说"处数在 1d 实测后写进 §3"；ir-graphs 备忘原估 20–40 处，差别来自"帧里不放地址、按值读写记录"）。如果 06 的 `oblib::pod` 提供透明包装的转换，这 3 处可以挪到那里，帧模块变成 0 处。
- 对外 API 全部安全：任何 DataRef（包括伪造的）都只会解析成边界内的切片或触发 3.1 的 abort，不会产生未定义行为；视图的生命周期绑定在存储表的借用上，注销和 reset 需要 `&mut`。
- 性能上的取舍：批量循环里按值解码 12 字节记录、每次带边界检查，比 C++ 的指针循环慢；先按 `PERF(port)` 记下，对齐之后有 profile 证据再在帧模块里加 unsafe 的批量视图（白名单内）。
- `unsafe impl Send/Sync`：无。`StoreTable` 自动 `Send`（`KvBlockHandle` 要求是 `Send`，08 的契约）；`RtExpr<'static>` 自动 `Send + Sync`（`EvalFn` 是 fn 指针，`ObIExprExtraInfo` 的变体都是拥有型数据）；`ObEvalCtx` 不需要跨线程。

## 5. 测试

所有单测在 `rust/port/{oblib,query,sql}/tests/` 或模块内 `#[cfg(test)]`；金标准文件由 5b 阶段的 C++ 测试程序产出，放在 `rust/port/<crate>/tests/golden/`，生成程序与 golden 一起入库。

**单测**

1. `ObDatumDesc`：位布局（`len_`/`flag_`/`null_` 的位置、29 位截断）、`set_null/set_none/set_ext/set_outrow` 的结果，与 C++ 的 `pack_` 值逐个比对（golden：C++ 程序打印各操作后的 `pack_`）。
2. `ObDatum`：`size_of == 12`、`align_of == 1`；`to_bytes/from_bytes` 往返；`r` 在字节 0..8。
3. `ObDatumView`：每个 getter 在合法长度上的值；`len_` 短于宽度时的补 0；`sub`/`sub_of` 的边界；`binary_equal` 的四种情况。
4. `ObDatumBuf`：每个 setter 之后的 `pack_` 与 C++ 相同（golden）；内联、Ref、Heap 三态的转换；`set_string_view` 遇到非表项视图时改为拷贝。
5. `from_obj/to_obj/from_storage_datum`：每个 `ObObjType` 一组样例，结果字节与 C++ 相同（golden：C++ 把 datum 的 pack_ 和载荷十六进制打印出来）。
6. `checksum`、`get_reserved_size`、`get_obj_datum_map_type`：全类型表比对 golden。
7. `StoreTable`：登记、注销、代号过期、槽复用、计划区固定 id、`bytes(r, 0)` 不检查、越界 abort（用 `#[should_panic]` 在测试 profile 下把 abort 换成 panic，或用 `try_bytes` 测错误码）、`copy_bytes` 重叠、arena 的 `len == 0` 失败与 reset。
8. 帧求值（假的 `EvalFn`/`EvalBatchFn`）：3.5 的每一条——evaluated 标志、失败置 null、批内首次求值的重置、`projected_` 跳过、整批 skip 时吞错、默认批量函数逐行置位、诊断分支；`eval_args` 的求值顺序和遇错即停。
9. 结果落盘与 DRB：3.6、3.7 的每一条，包括 `next_pow2`、旧内存不释放（旧 DataRef 仍能读）、`UINT32_MAX` 边界、不支持的类型、源与目标重叠时结果正确。
10. `ObExprFrameInfo::deep_copy`：拷贝后在两张存储表上分别执行，结果相同；原计划 drop 后副本仍可执行。
11. `IdVec`：push/get、截断后的墓碑、截断后 id 继续增长、整块释放（用 drop 计数验证节点被 drop）、`remove`、`resize_with`、`iter` 只给活节点且按序。
12. `QueryArena`：嵌套 `scope`、作用域内对外层节点的修改保留、作用域内节点被外层引用时访问报 `OB_ERR_UNEXPECTED`、`free_stmt` 之后旧 id 报错、`try_check_status` 每 1024 次调一次检查（注入一个计数钩子）、`with_op` 出错时状态放回。
13. 注册表：`NAME_TYPES` 的顺序、名字、类型、`is_internal_` 与 C++ 的 dump 逐项相同（golden：C++ 在 `register_expr_operators` 之后打印数组）；`get_type_by_name` 对全部名字和若干大小写变体的结果相同；`ObExprErrno` 只在 debug 构建出现。
14. `OpState` 生成契约：每个叶子类恰好一个变体；`as_*` 的接受集与 `types.tsv` 一致；`st.op()` 回到正确的零大小类型。
15. 向下转型：从 `types.tsv` 生成的接受集测试（每个层次、每个 `as_*` 对全部变体逐一断言），包括 IFNULL/NVL 被 `as_op()` 接受。
16. 向量格式：四种格式的 get/set 往返、`set_null`/`has_null` 的标志位、uniform 格式写 `notnull_`。

**Miri**：datum、object、`StoreTable`、`bitvec` 的 3 处 unsafe、`get_str_res_mem_with` 的分割借用、`ObExprFrameInfo::deep_copy`、`IdVec` 的截断。RULEBOOK 0.6 要求深拷贝配 Miri。

**loom**：不需要。这些类型都不跨线程共享（`ObExecContext` 是 Send 不是 Sync；计划 `Arc` 共享但只读）。

**金标准测试（以 C++ 为准）**：`ObObj::hash`/`hash_murmur`/`varchar_hash`/`varchar_murmur_hash`/`varchar_wy_hash`/`varchar_xx_hash`（全部类型 × 主要 collation × 若干 seed）；`ObObj` 与 `ObDatum` 的序列化字节；`ObObj` 的 TO_STRING（`cmp_meta()` 可见）；上面 1、4、5、6、13 的 golden。

**布局测试（5b）**：`ObDatum`、`ObEvalInfo`、`ObDynReserveBuf` 的尺寸和字段偏移；CG 对一组样例计划算出的帧偏移与 C++ 相同（C++ 程序打印每个 `ObExpr` 的 `*_off_`）。

## 6. 翻译者怎么用

1. **取参数、写定长结果**（`ob_expr_add.cpp:569-572` 经 `def_arith_eval_func`，`ob_expr_operator.h:1647-1658`）

   ```cpp
   ObDatum *l = NULL; ObDatum *r = NULL; bool finish = false;
   if (OB_FAIL(get_arith_operand(expr, ctx, l, r, expr_datum, finish))) {
   } else if (!finish) { ret = Functor()(expr_datum, *l, *r, args...); }
   ```
   ```rust
   fn add_int_int(expr: &RtExpr<'_>, ctx: &mut ObEvalCtx<'_>, expr_datum: &mut ObDatumBuf) -> ObResult {
       let mut ret: ObResult = Ok(());
       let [l, r] = match ctx.eval_args::<2>(expr) { Ok(v) => v, Err(e) => return Err(e) };
       if l.is_null() || r.is_null() { expr_datum.set_null(); }
       else { ret = ObIntIntBatchAddRaw::datum_op(expr_datum, l, r); }   // 里面 expr_datum.set_int(l.get_int() + r.get_int())
       ret
   }
   ```

2. **一次取多个参数**（`ob_expr_date_add.cpp:142`）：`expr.eval_param_value(ctx, date, interval, unit)` → `let [date, interval, unit] = ctx.eval_args::<3>(expr)?;`（翻译阶段写成 if/else 链，见 §2.1；这里为简洁写了 `?`）。之后还要调 `ctx` 的 `&self` 方法（如 `ctx.exec_ctx`）时改用两步：`ob_fail!(ret = ctx.eval_param_value(expr))` 后 `let [date, interval, unit] = ctx.arg_views::<3>(expr);`——注意 `exec_ctx` 要 `&mut self`，所以先拷出会话（`Arc` 克隆）再取视图。

3. **字符串结果缓冲**（`ob_expr_concat.cpp:233-246`）

   ```cpp
   char *buf = expr.get_str_res_mem(ctx, res_len);
   for (int64_t i = 0; i < expr.arg_cnt_; i++) {
     ObDatum &v = expr.locate_param_datum(ctx, i);
     if (!v.is_null()) { MEMCPY(buf + off, v.ptr_, v.len_); off += v.len_; } }
   expr_datum.set_string(buf, res_len);
   ```
   ```rust
   let srcs: SmallVec<[ObDatum; 8]> = (0..expr.args_.len()).map(|i| ctx.locate_param_datum(expr, i)).collect();
   ret = ctx.get_str_res_mem_with(expr, expr_datum, res_len, &srcs, |src, out| {
       let mut off = 0usize;
       for (s, d) in src.iter().zip(srcs.iter()) { if !d.is_null() { out[off..off + s.len()].copy_from_slice(s); off += s.len(); } }
       Ok(off)
   });
   ```

4. **浅复制参数**（`ob_expr_concat.cpp:230-232`）：`expr_datum.set_datum(*first_not_null);` → `expr_datum.set_datum(first_not_null);`（`first_not_null: ObDatum`，按值保存）。

5. **TempAllocGuard 里继续求值**（`ob_expr_repeat.cpp:253-262`）

   ```cpp
   ObEvalCtx::TempAllocGuard tmp_alloc_g(ctx);
   common::ObIAllocator &tmp_allocator = tmp_alloc_g.get_allocator();
   if (OB_FAIL(expr.args_[0]->eval(ctx, text)) || OB_FAIL(expr.args_[1]->eval(ctx, count))) {
   } else if (OB_FAIL(ObTextStringHelper::read_real_string_data(ctx.exec_ctx_, tmp_allocator, *text, …, text_str))) {
   ```
   ```rust
   let tmp_alloc_g = TempAllocGuard::new(ctx);
   let tmp_allocator = tmp_alloc_g.get_allocator();          // &Arena，不借用 ctx
   let mut text = ObDatum::default(); let mut count = ObDatum::default();
   if ob_fail!(ret = ctx.eval_arg(expr, 0).map(|d| text = d)) || ob_fail!(ret = ctx.eval_arg(expr, 1).map(|d| count = d)) {
   } else if ob_fail!(ret = ObTextStringHelper::read_real_string_data(ctx, tmp_allocator, text, …, &mut text_str)) {
   ```
   `ObExprStrResAlloc expr_res_alloc(expr, ctx)`（`:263`）→ 把 `tmp_allocator` 交给 `repeat(...)`，结果 `output` 再 `expr_datum.set_string_copy(output)`。

6. **批量**（`ob_batch_eval_util.h:60-90` 的逐行求值）：`ArithOp::datum_op(*res.at(i), *left.at(i), *right.at(i))` 加 flag 维护 → `ctx.batch_eval_map::<2>(expr, skip, size, |_i, [l, r], out| ArithOp::datum_op(out, l, r))`。16 行一组的快速路径（`:63-73`）是同一结果的优化，不必照搬；照搬时用 `skip.reinterpret_data_u16(k)` 读字。

7. **storage 写扫描结果进表达式**（`locate_datum_for_write` 在 storage 29 处）：`expr->locate_datum_for_write(eval_ctx).set_string(ptr, len)`，ptr 指向微块 → 先 `let s = ctx.store_table_mut().register_pinned(handle)?;`（每个微块一次），再 `let mut b = ObDatumBuf::new(); b.set_string(DataRef::new(s, off), len); ctx.write_result(ExprRef::Plan(id), idx, &b)?;`；移到下一个微块前 `unregister(s)`。

8. **ObObj 与 datum 互转**（`ob_expr.cpp:776`）：`expr_datum.from_obj(v, expr.obj_datum_map_)` → `expr_datum.from_obj(&v, expr.obj_datum_map_)`（字符串会拷贝，见 3.4）。反方向 `datum.to_obj(obj, meta)` → `ctx.view(d).to_obj_2(&mut obj, &meta)`，`obj: ObObj<'_>` 借用帧。

9. **ExtraInfo 与 ExprOpCtx**（`ob_expr_column_conv.cpp:737-750`）

   ```cpp
   const ObEnumSetInfo *enumset_info = static_cast<ObEnumSetInfo *>(expr.extra_info_);
   ...
   column_conv_ctx = static_cast<ObExprColumnConvCtx *>(ctx.exec_ctx_.get_expr_op_ctx(expr_ctx_id))
   ```
   ```rust
   let enumset_info = expr.extra_info_.as_deref().ok_or(OB_ERR_UNEXPECTED)?.as_enum_set_info()?;
   …
   let column_conv_ctx = ctx.exec_ctx::<ObExecContext>().get_expr_op_ctx(expr_ctx_id).map(|c| c.as_expr_column_conv_ctx_mut());
   ```
   `INVALID_EXP_CTX_ID` 分支（`:741-746`）改成 `ctx.eval_enumset_with_arg0(expr, expr.arg(4), &enumset_info.str_values_, cast_mode)`，标 `FIXED(port)`。

10. **IR 槽位**（`ob_transform_simplify_expr.cpp:2325`，RULEBOOK R3）

    ```cpp
    ObRawExpr *&child_expr = expr->get_param_expr(i);
    ... SMART_CALL(recursive_push_not(child_expr, push_expr_map, trans_happened)) ...
    ```
    ```rust
    let mut child_expr = qa.expr(expr)?.get_param_expr(i);                 // node_accessor：Option<ExprId>
    if child_expr.is_none() { ret = Err(OB_ERR_UNEXPECTED); }
    else if ob_fail!(ret = smart_call!(self.recursive_push_not(qa, &mut child_expr, push_expr_map, trans_happened))) {}
    if ret.is_ok() { ret = qa.set_param(expr, i, child_expr); }             // SLOT(port): expr->get_param_expr(i)
    ```

11. **向下转型**（`ob_transform_simplify_expr.cpp:1254`）：`ObOpRawExpr *op_expr = static_cast<ObOpRawExpr *>(expr);`（expr 是 IFNULL 的 `ObSysFunRawExpr`）→ `let op_expr = qa.expr(expr)?.as_op()?;`，接受 SysFun 变体。

12. **临时上下文**（`ob_transform_rule.cpp:379`，RULEBOOK 0.6 情况 (b)）：`CREATE_WITH_TEMP_CONTEXT(param) { ObRawExprFactory tmp_expr_factory(…); … }` → `ret = qa.scope(|qa| { … });`，块里原来用 `tmp_expr_factory` 的地方一律用这个 `qa`。局部 `ObRawExprFactory expr_factory(alloc)`（如 `ob_transform_utils.cpp:3605`）同理。

13. **算子的类型推导和 CG**（RULEBOOK 0.6）：C++ `raw_expr->get_op()->calc_result_type2(type, t1, t2, type_ctx)` → `qa.with_op(e, |qa, op, st| op.calc_result_type2(qa, st, &mut type_, &mut t1, &mut t2, &mut type_ctx))`。译者给类 `ObExprXxx` 写的是固有方法 `impl ObExprXxx { pub fn calc_result_type2(&self, qa: &mut QueryArena, st: &mut OpState, …) -> ObResult {…} }`，读写本类成员用 `st.as_xxx_mut()?`，基类成员用 `st.base_mut()`。

14. **子查询表达式访问 SPF 算子**（`ob_expr_subquery_ref.cpp:312-322`）：按 RULEBOOK 0.6 经 `ObExecContext` 的按 `SpecId` 索引的表取迭代器状态；要驱动子算子时 `ctx.with_exec_ctx(|exec| …)`，闭包里拿到的是完整的执行上下文。

## 7. 依赖

**白名单 crate（RULEBOOK §1）**：`smallvec`（IR 节点、生成的状态 struct）；bumpalo 只经 `oblib::mem::Arena` 间接使用；不需要 `slotmap`（修订 A2）、不需要同步类 crate。

**其他 1d 模块**

| 模块 | 用到的东西 |
|---|---|
| 01 `mem` | `Arena`（`new`、`alloc`、`try_str`、`used`/`total`、`reset`），`try_box`、`try_bytes_zeroed`、`try_reserve`，`check_mem_status()`（QueryArena 每 1024 次创建调一次），`MemBudget`（不直接用） |
| 03 `thread-signal` | `check_stack_overflow`（`need_stack_check_`、代理创建分支），`smart_call!` |
| 04 `error-log-fmt` | `ObError`/`ObResult`、`OB_ERR_UNEXPECTED` 等常量、`ob_fail!` 等宏、`log_error!`/`log_warn!`、`ob_abort()`、`ObToString`、`ObBuf`、`ob_printf!` |
| 05 `containers` | `ObIArray`、`ObHashMap`（id 为键）、`ObFixedBitSet`（R5 的标记旁表）、哈希 trait（id 的实现） |
| 06 `string-num-sort-serial` | `ObStringExt`、`number::{ObNumber, ObCompactNumber, ObNumberDesc}`、`int256_t`/`int512_t`、`ob_crc64_sse42`、murmur/wy/xx 哈希、`ObSerialize`/`ObDeserialize<'de>`/`ob_unis_*!`、`ob_bitfield!`、（可选）`oblib::pod` 的透明包装转换 |
| 08 `storage-core-kvcache` | `KvBlockHandle`（`Send`，`&[u8]` 访问，drop 解钉） |
| 09 `parser-sys-generators` | `query::item_type::ObItemType` 及常量（`T_MIN_OP`、`T_MAX_OP`、`T_OP_ROW` 等）；生成器框架（本文的 OpState/注册表/ExtraInfo/ExprOpCtx/as_* 生成器挂在同一套 `generated/` 机制下） |
| 1c 的表 | `types.tsv`（is-a 接受集、状态 struct 名、生命周期类别）、`seams.tsv`（`ObExecContextVirt`、`PushdownFilter`、ExtraInfo 变体下移、ob_expr.cpp/ob_expr_frame_info.cpp 的函数级重映射）、`overloads.tsv`（2.1.6 的覆盖）、`qa_signatures.tsv`（2.8.4） |

## 8. 开放问题

修订 A1–A6 依次对应 `migration/infra/amendments.tsv` 里 `doc = 07-sql-ir-frame-datum` 的六行（A1 内联值、A2 语句回收、A3 R1、A4 临时 arena、A5 改名表、A6 ObObj 的数字）。8.2、8.3、8.9–8.11 是 RULEBOOK 没有决定的问题，不是 RULEBOOK 的错，没有排修订。

**8.1 存储形态里的"8 字节内联值"（修订 A1）**
- 问题：RULEBOOK 0.6/§2.12 说 `DataRef` 是 `{store, off}` 或 8 字节内联值。12 字节里 `desc` 的 32 位全有语义（`len_` 29 位正好是 `OB_MAX_LONGTEXT_LENGTH` 的上限，`ob_define.h:1420`），`DataRef` 的 64 位在内联时全是值，没有地方放判别位；按类型静态判别又不行：校验和、`binary_equal`、行存储拷贝、序列化都是不看类型的通用代码。另外 C++ 的 `set_int` 是"写进 ptr_ 所指的 res buf"，浅拷贝过的 datum 会看到新值，内联会改变这种别名语义。
- 推荐：存储形态只有 `{store, off}`；内联值放在 `ObDatumView`（借用）和 `ObDatumBuf`（本地缓冲）里。
- 备选：(b) 把 `len_` 减到 28 位腾出判别位——256 MB 到 512 MB 的值会出错，行为偏差；(c) 用 `flag_ == EXT && len_ ≤ 8` 编码内联——`pack_` 的读者都要掩码，容易出错。

**8.2 `ObEvalCtx` 怎么拿到 `ObExecContext`**
- 问题：eval 函数要访问会话、计划上下文等（`ctx.exec_ctx_.xxx(` 599 处），`ObEvalCtx` 在 query、`ObExecContext` 在 sql。本文用句柄 trait `ObExecContextVirt` 加 `as_any_mut()` 向下转型，要求 `ObExecContext` 不带生命周期参数（`'static`），这是对 sql 侧 `types.tsv` 和清单第 3、4 类的约束（如 `sql_ctx_` 不能是 `&'q ObSqlCtx`）。
- 推荐：`ObExecContext` 定为 `'static`（字段用拥有型、`Arc` 或 id；请求 arena 按 0.7 显式传参，本来就不存进它）。
- 备选：(b) sql 另建一个 `'static` 的核心子结构做向下转型的目标，`ObExecContext` 包着它和带生命周期的部分；(c) 不向下转型，`ObExecContextVirt` 列出 eval 函数用到的全部方法（约 40 种，由 seam 表给），代价是会话等类型也要有 query 级的句柄 trait。

**8.3 ExtraInfo enum 放在哪个 crate**
- 问题：`RtExpr::extra_info_` 按值持有 enum（RULEBOOK 0.6 和 F22 要求 enum 加 match、不做 dyn 向下转型），所以 enum 和 19 个变体 struct 要在 query；可它们大多声明在 sql（如 `ObExprUDFInfo` 用到 sql 的 `ObUDFParamDesc`，`ob_expr_udf.h:41-73`）。
- 推荐：seam 表把 19 个变体 struct 及其字段类型下移到 query（只是数据和 `deep_copy`）。
- 备选：(b) enum 留在 sql，`RtExpr` 持有 `Box<dyn ObIExprExtraInfoVirt>`，sql 用时向下转型一次到 enum（每次访问一次虚调用加一次 TypeId 比较，F22 担心的就是这个）；(c) `RtExpr` 只存下标，信息放在 sql 的计划里，eval 函数经 `exec_ctx` 取（每次访问一次向下转型）。

**8.4 语句回收（修订 A2）**
- 问题：RULEBOOK R8/0.6 说语句回收用 `slotmap` 的代际 key，又说 `StmtId` 是 `NonZeroU32`；slotmap 的 key 是 64 位，两条不能同时成立。
- 推荐：`StmtId` 不复用，`free_stmt` 留墓碑（与子作用域截断同一机制），不用 slotmap；旧 id 访问同样报 `OB_ERR_UNEXPECTED`。被释放语句的内存随 drop 归还，墓碑只占一个槽位。
- 备选：在 `u32` 里自己编代号（如 24 位槽号 + 8 位代号），代号回绕后检测不到。

**8.5 临时 arena（修订 A4）**
- 问题：RULEBOOK 0.6 要求临时 arena"只能经帧模块 reset，要求 &mut 令牌"。但 C++ 有 66 处在守卫活着时继续求值（附录 A），还经常把 ctx 和临时分配器一起传给辅助函数（`ob_expr_array_compact.cpp:78-90`）；如果临时 arena 在 `ObEvalCtx` 里，守卫就借住了 ctx，这些写法都编译不过。
- 推荐：每个 `TempAllocGuard` 自带一个新 `Arena`，drop 即释放；不需要 reset，也不借用 ctx。代价是每个守卫第一次分配要向全局分配器要一块（`PERF(port)`）。
- 备选：(b) 共享 arena 加内部可变性和守卫计数，计数为 0 时才 reset（与 C++ 的 reset 时机完全相同），需要 `UnsafeCell` 和约 5 处 unsafe，还要让守卫借用 exec ctx 之外的东西（存储表要拆分借用）；(c) 只在构造 `ObEvalCtx` 时 reset（每次算子调用一次），守卫借用共享 arena——守卫活着时同样不能求值。

**8.6 R1 对节点方法写不出来（修订 A3）**
- 问题：R1 要求"self 之后的第一个参数一律是 qa"，对 IR 节点类自己的方法，`self` 从 qa 借出、又要 `&mut qa`；而且 R1 的类型表只列了 `ObDMLStmt`，可 `StmtId` 指的是全部 90 个语句类。
- 推荐：2.8.4 的四种形态；IR 类型集合把 `ObDMLStmt` 改成 `ObStmt`。
- 备选：节点方法一律 `node_assoc`（不做函数体检查，纯声明判定；代价是连 `get_expr_type()` 这类访问函数也要写成 `ObRawExpr::get_expr_type(qa, e)` 并返回 `ObResult`）。

**8.7 新名字（修订 A5）**：本文引入的、改名表里没有的公共名字：`StoreTable`、`ObDatumBuf`、`IrId`、`IrNodeCtor`、`InnerFn`、`ObPlanConstStoreBuilder`、状态 struct 的命名模式 `<类名>State`、ExtraInfo 嵌套类的 `<外层类名>ExtraInfo`。`ObExecContextVirt`、`ObExprOperatorCtxVirt` 符合 `<类名>Virt` 规则，`ObIExprExtraInfo`、`ObExprOperatorCtx`、`BatchInfoScopeGuard`、`TempAllocGuard`、`ObExprExtraInfoAccess` 沿用 C++ 名，不需要改名表。
- 推荐：照上面加进 §4 的改名表。

**8.8 ObObj 的数字（修订 A6）**
- 问题：§2.12 说 `ObObj<'a>` 的数字用 `&'a [u32]`；可 `to_obj` 得到的数字 digit 来自 datum 的载荷，载荷不保证 4 字节对齐（行存储、DRB、参数帧），从字节切片借出 `&[u32]` 需要对齐。
- 推荐：数字也用 `&'a [u8]`（u32 小端序列），`get_number()` 按值拷出 `ObCompactNumber`。
- 备选：保留 `&[u32]`，不对齐时 `to_obj` 失败（行为偏差）或拷贝（视图无处存放副本）。

**8.9 `ObObjParam` 的拥有型**
- 问题：改名表只有 `ObObjOwned`。`ObObjParam` 绝大多数在查询作用域（参数存储、PL 调用）里，用 `ObObjParam<'a>` 就够；如果清单发现有长期持有的 `ObObjParam`，需要一个拥有型。
- 推荐：暂不加；清单遇到时按 `ObObjOwned` 加旁边的元数据字段处理，`TODO(port)`。
- 备选：现在就加 `ObObjParamOwned` 进改名表。

**8.10 批量求值的写法**
- 问题：C++ 的批量函数用指针在帧里循环；Rust 按值解码、每行一次落盘，会慢。
- 推荐：先用 `batch_eval_map` 和逐行 API，标 `PERF(port)`；对齐后按 profile 在帧模块里加批量视图（白名单内的 unsafe）。
- 备选：现在就提供 `&[ObDatum]`/`&mut [ObDatum]` 的批量视图（需要 1–2 处 unsafe 和更复杂的分割借用）。

**8.11 过期 DataRef 的处理**
- 问题：3.1 选择 abort。C++ 读已释放的 arena 页时可能读到旧值而继续运行，Rust 会让整个进程退出。
- 推荐：abort（与 C++ 的 `OB_ASSERT` 同一态度；不让错误数据流进结果）。
- 备选：记 ERROR、返回空切片，读到的定长值为 0。

附录 A：计数脚本（只读，在 worktree 根目录执行；`python3 -I -c '…'` 形式在本文作者的会话里运行过，结果见 1.3）

```python
# 1.3 的 datum 字段与访问函数计数：按目录汇总"含模式的行"
import os, re
pats = {"ptr_": r"(\.|->)ptr_\b", "ptr_ =": r"(\.|->)ptr_\s*=[^=]", "pack_": r"(\.|->)pack_\b",
        "set_string(": r"(\.|->)set_string\(", "from_obj(": r"(\.|->)from_obj\(", "to_obj(": r"(\.|->)to_obj\(",
        "locate_datum_for_write(": r"locate_datum_for_write\(", "args_[i]->eval(": r"args_\[[^]]+\]->eval\("}
# 遍历 src/**/*.{h,cpp,ipp}，逐行 re.search，按 src/<顶层目录> 累加
# TempAllocGuard 之后 40 行内（到下一行顶格的 } 为止）出现 ->eval( / .eval( / eval_param_value( / eval_batch( 的守卫数：66 / 370
# 下压过滤器：取 ob_pushdown_filter.h 里出现过的全部小写方法名，统计 src/storage 中含 filter|executor 的行里 ->m( 或 .m( 的次数
```
