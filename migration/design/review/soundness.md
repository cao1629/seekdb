# 对抗审查：内存安全与健全性（soundness）

> - **对象**：`migration/RULEBOOK.md` 草稿 v0.1。只查一类问题：规则照写会不会产出不健全的 Rust（未定义行为、悬垂引用、别名违规、unsafe 漏出 §3 白名单、Send/Sync 错误、写不出来或必然被借用检查器拒绝的生命周期），以及 Rust 的语言规则让哪些规则无法照写。并发与性能、行为忠实度、流程一致性不在本文范围。
> - **基线**：`7d907abfa`。只读，没有构建。引用 RULEBOOK 时写作 `RULEBOOK.md:行号`。
> - **计数**：正则和小脚本都是近似值，标「启发式」的是上限或估计。脚本在 `/tmp/soundness-review-k3x9/scripts/`（`atomic_ptr.py`、`cross_dtor.py`、`itemtype_gaps.py`、`itemtype_diff.py`、`mut_statics.py`），按规则不入库。

## 1. 结论

1. 值类型 `ObDatum`/`ObObj` 照 §2.12 保留裸指针联合体后，"unsafe 只在帧模块 20–40 处"不成立：要么 unsafe 扩散到约 400 个表达式文件和 storage，要么安全访问函数本身不健全；借用式帧 API 又会被借用检查器拒绝。
2. `'q` 模型只覆盖"arena 在外层栈帧"的情况；自持 arena 又借用它、跨调用或跨请求存活的对象（SPI 结果集、PL 游标、排序/哈希算子、`ObResultSet`/`ObTxCtx` 的自指针）没有可写的译法。
3. 组合根的 `&'static` 槽位、单例和 GCTX 与 §2.1 的 `init(&mut self)`、C++ 的"先绑定后 init、destroy 时释放再解绑"互相冲突；storage_core 的回调访问是依赖 storage 安全代码维持协议的不健全 API；`ItemType` 作为 `#[repr(i32)]` 枚举装不下 C++ 实际产生的非枚举值。
4. 共 26 条：高 5、中 16、低 5；另列 13 类缺失的规则。

## 2. 发现

### S-01【高】`ObDatum`/`ObObj` 的指针联合体：unsafe 扩散，或安全 API 不健全

- **规则**：
  - §0.6「帧：保留 C++ 的字节帧布局。unsafe 只出现在帧模块里，估计 20–40 处」（`RULEBOOK.md:196`）；
  - §2.12「`ObObj`、`ObDatum`……`#[repr(C)]`，字段顺序和类型都不变……含指针的联合体按缺口清单处理」（`:629`）；
  - §2.4「算子批次级的用算子拥有的 `Arena`，在批次边界 `reset()`，此时不能有存活的借用」（`:486`）；
  - §0.6「`Arc<PhysicalPlan>`……`Send + Sync`」（`:185`）、§0.5「放进静态量或槽位的类型必须是 `Sync`」（`:169`）、§3「`unsafe impl Send/Sync` 也只允许出现在上述地方」（`:677`）。
- **具体例子**：
  - `src/oblib/common/datum/ob_datum.h:106-131`：`ObDatumPtr` 是 16 种 `const T*` 的联合体；`:229` `get_int() { return *int_; }`；`:294` `set_int` 经 `no_cv(int_)` 往指针所指处写；`:351` `set_string(ptr, len)` 存任意指针；`:581` `from_storage_datum` 直接抄进存储行的指针。datum 可以指向帧、计划常量内存、存储行、结果 arena。
  - 同一文件 `:131,165` 两个基类都是 `__attribute__((packed))`，`:1027` `STATIC_ASSERT(sizeof(ObDatum) == 12)`：12 字节、对齐为 1，批量数组里指针字段经常不对齐；`src/sql/engine/basic/ob_pushdown_filter_simd.cpp:43,61-66` 按 12 字节步长用 SIMD 读 datum 数组。
  - 规模：ObDatum 在 sql 出现 4,050 次、storage 986 次；`sql/engine/expr` 下 392 个 `.cpp` 用它；`get_string()` 1,149 处，`set_string(` 496 处。
  - `src/query/api/query/engine/expr/ob_expr.h:1039-1068`：`ObExpr::eval` 把指向帧的 `ObDatum*` 交给调用方，`ctx` 同时继续可用。`args_[i]->eval(ctx, d)` 273 处；`eval_param_value(ctx, a, b, …)` 一次拿多个 datum 52 处，如 `src/sql/engine/expr/ob_expr_date_add.cpp:142`。
  - `ob_expr.h:260-271`：求值临时 arena 在"没有 `TempAllocGuard` 占用"时 reset（`:170-178`），靠运行期标志，而不是借用。
  - `ObObjValue`（`src/oblib/common/object/ob_object.h:1257-1283`）含 `const char*`、`uint32_t*`、`ObLobCommon*`、`ObDecimalInt*`、`void*`。schema 里的默认值就是 ObObj（`src/share/schema/ob_column_schema.h:379-380`）；全仓 85 个 `ObObj x_;` 成员。
- **按规则的译法**：`#[repr(C)] struct ObDatum { ptr: *const u8 /*联合体*/, pack: u32 }`，方法 `get_int(&self) -> i64`、`get_string(&self) -> &[u8]`、`set_int(&mut self, v: i64)`。
- **为什么错**：
  1. 方法体都要解引用裸指针。写成 `unsafe fn`，unsafe 就进入约 400 个表达式文件和 storage，而 §0.9 已给 storage 加 `forbid(unsafe_code)`。写成内部 unsafe 的安全函数，安全代码就能用任意指针构造 datum，或在 `arena.reset()`（安全方法）之后继续 `get_string()`，从安全代码触发释放后使用。§2.4 的"reset 时不能有存活的借用"只对编译器看得见的借用成立，借用一旦变成 datum 里的裸指针就不再受检查。
  2. 改成借用式帧 API（`fn eval<'a>(&self, ctx: &'a mut EvalCtx) -> &'a Datum`）后，拿着左操作数再求右操作数需要第二个 `&mut ctx`，273 + 52 处调用点都会被借用检查器拒绝。
  3. 保留 12 字节布局就得 `#[repr(C, packed)]`：Rust 禁止对 packed 字段取引用（E0793），任何 `&self.ptr`、`&mut self.pack` 都编不过；不 packed 则变成 16 字节，违背"保留 C++ 字节帧布局"。
  4. 裸指针字段让 ObObj、ObDatum 自动 `!Send + !Sync`。含它们的 schema 对象、计划里的 extra info（如 `src/sql/engine/expr/ob_expr_calc_partition_id.h:45-48,175`，范围分区边界 `RangePartition` 存的就是 ObDatum）、会话系统变量都随之不能 `Sync`，§0.5 和 §0.6 的要求做不到；而 oblib 的 datum/object 模块不在 §3 白名单里，不能写 `unsafe impl Sync`。
- **建议改法**：在 §0.6/§2.12 增加「值表示」小节：
  - 帧里的 datum 不存绝对指针，只存 `(len/flag, DataRef)`，`DataRef` 是 `{ store: u32, off: u32 }` 或 8 字节内联值；变成字节切片只能经帧模块的 `fn bytes<'f>(&'f self, d: &Datum) -> &'f [u8]`，生命周期绑定在帧借用上；批次 arena 只能由帧模块在持有 `&mut Frames` 时 reset。
  - 帧模块提供一次取多个参数的接口，如 `ctx.eval_args::<N>(expr) -> ObResult<[DatumView<'_>; N]>`（先全部求值，再统一借出），覆盖二元算子和 `eval_param_value`。
  - `ObObj` 拆成借用视图 `ObObj<'a>`（字符串、数字用 `&'a [u8]`/`&'a [u32]`）和拥有型 `ObObjOwned`（`Box<[u8]>`）；schema、计划、会话变量只用拥有型；`deep_copy(alloc)` 译成"视图 → 拥有型"或"视图 → arena 视图"两个函数。
  - 如果坚持保留 C++ 的打包布局，就把 `oblib::common::{datum, object}` 列进 §3 白名单，构造函数一律 `unsafe fn`，读取函数一律返回带生命周期的视图，并把"20–40 处"改成实测数。

### S-02【高】自持 arena 又借用它、且跨调用存活的查询/语句级对象，没有可写的译法

- **规则**：
  - §0.7「查询作用域的类型带 `'q` 生命周期」「批次临时 arena 归算子所有，在批次边界 `reset`」（`RULEBOOK.md:213-216`）；
  - §0.7「288 个"拥有分配器、又借用它"的长生命周期类……改成拥有型字段」（`:232`），而 §5 第 5 项把同样的 288 个叫"自引用类"，没有"长生命周期"限定（`:757`）；
  - §2.4 裸指针成员"在 id、`&'q T`、`Box<T>`、`Arc<T>`、`Weak<T>`、`Option<&'static T>` 里选一种"（`:491`）。
- **具体例子**：
  - `src/sql/ob_spi.h:228-229`：`ObSPIResultSet` 在自己的 `buf_`（`:282`）里 placement new 一个 `ObResultSet`，用的是自己的 `mem_context_`（`:275`）的 arena；`:225` 还会 reset 这块 arena。
  - `src/sql/pl/ob_pl_type.h:761-803,841-850`：`ObPLCursorInfo` 自持 `entity_`（MemoryContext），用 `void *spi_cursor_` 指向其中的对象，挂在会话的 `ObHashMap<int64_t, ObPLCursorInfo*>`（`src/sql/session/ob_sql_session_info.h:323`）里跨请求存活。
  - `src/sql/ob_result_set.h:482-483`：`p_field_columns_(&field_columns_)`；`:487`、`:496` 在自身缓冲区里 placement new `ExternalRetrieveInfo` 和 `ObExecContext`。
  - `src/sql/engine/sort/ob_sort_op_impl.h:825-855`：排序算子自持 `mem_context_`，`quick_sort_array_`、`stored_rows_`、`imms_heap_` 都指向自己 arena 里的行，跨所有 `get_next_batch` 调用存活。sql/engine 下自持 `lib::MemoryContext` 的算子头文件 22 个，`StoredRow*` 一类指针 790 处。
  - `src/storage/tx/ob_tx_ctx.cpp:113,118,142`：`mt_ctx_.set_trans_ctx(this)`、`tlog_ = &trace_log_`、`elr_handler_.set_memtable_ctx(&mt_ctx_)`，成员指向自身或兄弟成员；ObTxCtx 按 §0.9 放进 `Arc`。
  - 启发式：构造函数初始化列表里"成员(&另一成员)" 19 处、11 个文件（如 `src/oblib/lib/queue/ob_link_queue.h:103` `head_(&dummy_), tail_(&dummy_)`）；placement new 进自身 `*buf*_` 成员 31 处；内存备忘统计的 `new (&member)` 134 处。
- **按规则的译法**：`struct SpiResultSet<'q> { mem: MemCtx, result_set: ResultSet<'q> }`；`struct SortOp<'q> { arena: Arena, rows: ArenaVec<'q, &'q StoredRow> }`。
- **为什么错**：
  - `'q` 必须来自结构体之外的所有者。同一个结构体既拥有 `Arena` 又持有指向它的 `&'q`，是安全 Rust 写不出的自引用结构；而且 Rust 的值会移动，C++"原地构造、永不移动"的前提不成立。
  - §0.7 只给"长生命周期类"开了"改拥有型"的口子，查询级的那部分没有规则。
  - 游标对象还必须是 `Send`：会话的后续请求由任意 worker 线程处理。`bumpalo::Bump` 是 `!Sync`，所以持有 `&Arena` 或 `ArenaVec<'q, _>`（内部是 `&'q Bump`）的结构都是 `!Send`，放不进会话。
  - 翻译者剩下的路只有裸指针（sql、storage 禁止 unsafe）或把生命周期 `transmute` 成 `'static`（不健全）。
- **建议改法**：§0.7 增加"自持 arena 的对象"规则，由缺口清单逐类打标签：
  - (a) arena 能上移到调用方栈帧的（只在单个请求内）：移到调用方，对象带 `'q`。
  - (b) 跨调用或跨请求存活、或必须 `Send` 的（游标、SPI 结果集、阻塞算子的行存储）：不用 bump arena 和 `&'q`，改为拥有型分块缓冲 `Vec<Box<[u8]>>` 加 `RowId { chunk: u32, off: u32 }`，算子只存 id。
  - (c) 指向自身或兄弟成员的指针（`p_field_columns_`、`tlog_`、`elr_handler_`、`head_(&dummy_)`）一律删除，改为访问函数或调用时传参；确实需要原地链接的只允许在 §3 白名单模块里，用 `Pin<Box<_>>` 构造。
  - 第 232 行去掉"长生命周期"限定，并写明 (b) 类对象不得持有 `&Arena`。

### S-03【高】`&'static` 槽位、单例和 GCTX，与两段式 `init(&mut self)`、C++ 的 destroy 顺序冲突

- **规则**：
  - §0.5「指针成员是 `OnceLock<&'static T>`」（`RULEBOOK.md:154`）；「`X::instance()`，背后是 `static LazyLock<X>`」（`:156`）；
  - §0.5「照抄 `obs_construct_modules`（构造 64 个模块、绑定 98 个槽位）→ `obs_init_modules` → `obs_start_modules` 的顺序；出错时按 stop → wait → destroy 的顺序收尾」（`:161-162`）；「不支持解绑」（`:164`）；
  - §2.1「保留两段式初始化……`fn init(&mut self, …) -> ObResult`」（`:441`）。
- **具体例子**：
  - `src/observer/omt/ob_server_runtime_controller.cpp:1251-1275`：construct 阶段绑定槽位，其中 `:1251` 绑定的是正在初始化的 ObServer 自己（`bind_server_service<share::ObIMemstoreRuntime>(this)`），`:1273` 绑定 `ObLSService`。`:1379` 才调用 `ObLSService::server_module_init` → `ls_service->init()`（`src/storage/tx_storage/ob_ls_service.cpp:114-117`）；`:1389-1392` 的 init 还要读另一个已绑定的槽位。
  - `src/observer/ob_server.cpp:2377,2381` 先把 `&schema_service_`、`&sql_proxy_` 发布进 GCTX，之后 `:2009` 才 `sql_proxy_.init()`、`:2201-2209` 才 `schema_service_.init(...)`。
  - `src/observer/omt/ob_server_module_lifecycle.h:97-104`：destroy 是 `m->destroy(); ob_delete(m);`。`ob_server_runtime_controller.cpp:1706` 的注释是 "Keep the slots alive while module destructors run"，`:1709` 起才解绑。`src/storage/memtable/ob_memtable.cpp:170-178` 明确依赖"服务销毁后槽位返回空"来避免访问已销毁的 trans service。
  - 单例上的可变调用 59 处，如 `ob_server.cpp:758` `ObMemoryDump::get_instance().init()`、`:843` `ObOptStatManager::get_instance().init(&sql_proxy_)`。
- **按规则的译法**：`LS_SERVICE.bind(Box::leak(Box::new(LsService::new())))`，之后再 `ls_service.init(...)` 拿 `&mut`；`ObMemoryDump::instance().init()`（`instance()` 返回 `&'static`）；destroy 时 `drop(Box::from_raw(...))`。
- **为什么错**：
  - `&'static T` 一旦存进 OnceLock，任何 `&mut T` 都与它别名：借用检查器拒绝，用裸指针绕过就是别名违规 UB。`LazyLock` 只给 `&T`，`init(&mut self)` 根本调用不了。
  - C++ 在 destroy 时释放模块，靠解绑让槽位返回 NULL。Rust 的槽位不能解绑，照译释放就留下悬垂的 `&'static`，而 C++ 恰好有代码（`ob_memtable.cpp:170-178`）在 destroy 期间经槽位访问模块。
- **建议改法**：§0.5 写死以下几条：
  - 绑定进槽位或 GCTX 的对象、`get_instance()` 单例，一律 `Box::leak` 一次（或写成 `static`），进程内永不释放；对模块的 `ob_delete(m)` 不翻译，标 `PARITY(port)`。
  - 这些类型的 `init/start/stop/wait/destroy` 一律取 `&self`：init 才填的字段用 `OnceLock<T>`，运行期可变的字段按 §2.7 进锁或用原子量；`destroy(&self)` 只复位状态，不释放内存。§2.1 的 `init(&mut self)` 只适用于尚未发布的对象。
  - "槽位为空"的语义改用显式的 `stopped: AtomicBool` 表达；依赖解绑返回 NULL 的分支（如 `ob_memtable.cpp:170-178`）改为检查 `stopped`。

### S-04【高】storage_core 的回调访问是不健全的安全 API；回调路径上的 `'m` 不绑定任何句柄

- **规则**：
  - §0.9「只能经 `NodeRef<'m>`/`RowGuard<'m>` 访问，`'m` 绑定在持有 `Arc<Memtable>` 的 guard 上」（`RULEBOOK.md:280`）；
  - §0.9「回调里指向行、节点、memtable 的指针 | 保留裸指针，以及"销毁 memtable 前先摘掉所有指向它的回调"的协议（`ob_memtable.cpp:166-181`）| 在核心内」（`:283`）；
  - §0.9「`storage` crate 加 `#![forbid(unsafe_code)]`。MVCC 的规则……都用核心 crate 的安全 API 写」（`:292`）。
- **具体例子**：
  - `src/storage/memtable/mvcc/ob_mvcc_trans_ctx.h:425,538-544`：`ObMvccRowCallback` 持有 `ObIMvccCtx &ctx_`、`ObMvccRow &value_`、`ObMvccTransNode *tnode_`、`ObMemtable *memtable_`。
  - `ob_mvcc_trans_ctx.cpp:1747-1760` `trans_commit()` 持行锁回填节点；`:1737-1740` `get_dml_flag` 经 `node->buf_` 读载荷。这个 2,187 行的文件里有 59 处 `value_.`/`tnode_->`/`memtable_->` 解引用。
  - 协议本身在 `src/storage/memtable/ob_memtable.cpp:167-181`（经 `server_service<ObTransService>()`）→ `ob_mvcc_trans_ctx.cpp:658-672`。storage 备忘列出的核心单元（`ob_mvcc_row`、`ob_row_latch`、keybtree、`ob_tx_callback_list` + `ob_mvcc`、`ob_memtable_data`、memstore 分配器、`log_group_buffer`、`lsn_allocator`）不含 `ob_mvcc_trans_ctx` 和 `ob_memtable`，§0.9 第 296 行也把 memtable/tx 放进 storage 的批量翻译。
- **按规则的译法**：核心提供 `impl RowCallback { pub fn row(&self) -> RowGuard<'_>; pub fn node(&self) -> Option<NodeRef<'_>> }`，内部 unsafe 解引用 `NonNull`；storage 的 `trans_commit`、`remove_callback_for_uncommited_txn` 用安全代码调用它们。
- **为什么错**：
  - 这里的 `'_` 来自回调的 `&self`，不来自任何 `Arc<Memtable>` guard。这个 API 是否健全，完全取决于 storage 里那段安全代码能否在 memtable 释放前摘干净回调；storage 的翻译漏摘一条，安全代码就能触发释放后使用，违反"安全 API 不能被安全代码用出 UB"的基本要求。
  - 反过来把这些函数做成 `unsafe fn`，storage 又因 forbid 无法调用。§0.9 的"`'m` 绑定在 guard 上"只对读路径成立。
- **建议改法**：把协议收回核心。
  - 每个 memtable 在核心内维护 `live_callbacks: AtomicI64`；`RowCallback` 只能经核心的 `MemtableRef::register_callback()` 创建（+1），摘除或析构时 −1。
  - 核心的 memtable 释放入口在计数不为零时拒绝释放（记 ERROR 并延后），这样 storage 的协议错误最多变成延迟释放，不会变成 UB。
  - 或者每条回调持有一个按 memtable 分片计数的 `MemtablePin`（避免所有写者争抢 `Arc` 的同一缓存行），由核心保证 pin 存活期间不释放。
  - §0.9 写明：回调路径上的 `'m` 来自 pin，不来自 guard。

### S-05【高】`ItemType` 写成 `#[repr(i32)]` 枚举，装不下 C++ 实际产生的非枚举值

- **规则**：§2.12「`ItemType`（约 2,020 个值，两份头文件副本）| 合并成 `query::item_type::ItemType`，`#[repr(i32)]`，取值与 C++ 完全相同」（`RULEBOOK.md:632`）。同一张表的上一行恰恰规定：C++ 枚举译成 `#[repr(transparent)] pub struct T(pub i32)`，"理由：反序列化不校验取值，int→枚举的转换至少有 781 处"（`:631`）。
- **具体例子**：
  - `src/sql/resolver/dml/ob_default_value_utils.cpp:235,302,657,706`、`src/sql/ob_spi.cpp:661,691`、`src/pl/ob_pl_resolver.cpp:5925` 都是 `create_raw_expr(static_cast<ObItemType>(obj.get_type()), …)`，把 ObObjType 直接当 ItemType 用。ObObjType 的 34、35、39、41、42（`ObEnumInnerType`、`ObSetInnerType`、`ObDecimalIntType`、`ObMySQLDateType`、`ObMySQLDateTimeType`，`src/oblib/common/object/ob_obj_type.h:79-87`）在 ItemType 里都是空洞。`_enable_decimal_int_type` 和 `_enable_mysql_compatible_dates` 默认都是 True（`rust/config/parameters.yaml:540-542,576-578`），所以带默认值的 DECIMAL、DATE、DATETIME 列就会产生 39、41、42。全仓 `static_cast<ObItemType>` 28 处。
  - ItemType 在 [-1, 4920] 里有 2,904 个空洞。两份副本也不只差注释：`src/query/api/query/parser/ob_item_type.h:2277` 有 `T_FLUSH_PRIVILEGES = 4920`（由 `src/sql/parser/sql_parser_mysql_mode.y:13724` 产生，`src/sql/resolver/ob_resolver.cpp:505` 消费），objit 副本没有；objit 副本 `:1951,2156,2157` 有 `T_AUTO_PARTITION`、`T_LS_ATTR_LIST`、`T_ALTER_LS`，query 副本没有。
- **按规则的译法**：`ItemType::from_raw(v: i32) -> ItemType { unsafe { transmute(v) } }`（parser crate 在白名单里，可以这样写），或 `ItemType::try_from(v).unwrap()`。
- **为什么错**：Rust 枚举持有非变体的值是立即 UB；用 `try_from` 则 C++ 里合法的 39、41、42 会变成错误或 abort，偏离行为。合并时只取一份副本，另一份产生的值同样是非法变体。
- **建议改法**：ItemType 与其他枚举一视同仁，`#[repr(transparent)] pub struct ItemType(pub i32)`，关联常量取两份副本的并集（生成器校验同名同值），`match` 一律带 `_` 分支；`ParseNode::kind()` 直接包装 i32。§3 的 unsafe 要求里加一条：禁止把任何来自 C 或整数的值 `transmute` 成 Rust `enum`。

### S-06【中】被 `ATOMIC_*` 访问过的指针成员一律改 `AtomicPtr`，解引用需要 unsafe

- **规则**：§2.7「凡是被任意 `ATOMIC_*` 访问过的字段，类型都改成 `AtomicI64`/`AtomicU32`/`AtomicPtr`/`AtomicBool` 等」（`RULEBOOK.md:565`）；§2.4「`NonNull<T>` 只允许出现在 §3 白名单模块里」（`:491`）；§3「其余 crate 一律 `#![forbid(unsafe_code)]`」（`:660`）。
- **具体例子**：对指针成员的 `ATOMIC_*` 共 162 处，约 52 处在任何白名单候选之外（启发式），例如：
  - `src/share/schema/ob_server_schema_service.cpp:74,2259,2512`：`schema_mgr_for_cache_` 的换指针和无锁读；
  - `src/logservice/ob_log_allocator_mgr.cpp:114-125`：`ATOMIC_BCAS(&allocator_)` 惰性初始化；
  - `src/share/io/ob_io_define.cpp:1424-1491`（`result_`）、`src/sql/engine/px/ob_px_bloom_filter.cpp:251,271`（`bits_array_`）、`src/storage/ls/ob_ls_switch_checker.cpp:33,49`（`ls_`）、`src/observer/ob_server.cpp:2688-2737`（`obs_`）。
- **按规则的译法**：`schema_mgr_for_cache: AtomicPtr<SchemaMgr>`，读取处 `unsafe { &*self.schema_mgr_for_cache.load(SeqCst) }`。
- **为什么错**：`AtomicPtr::load` 得到的是裸指针，只能在 unsafe 里解引用，而 share、logservice、sql、storage 都禁止 unsafe。裸指针也不携带"谁保证对象还活着"的信息，正是 C++ 里换指针后读者悬垂的老问题。
- **建议改法**：§2.7 对指针类字段按角色选型，缺口清单第 8 项同时标注角色：
  - 发布或替换后读者无锁读：`ArcSwapOption<T>`；
  - 由空到非空只设一次（BCAS 惰性初始化）：`OnceLock<T>` 或 `OnceLock<&'static T>`；
  - 指向 arena 或池里的对象：`AtomicU32` 存 id；
  - 只有 §3 白名单模块可以用 `AtomicPtr`。

### S-07【中】arena 与 Drop：显式析构的 arena 对象、`ArenaVec` 的 `ManuallyDrop`、`ObFixedArray → ArenaVec<'q>` 落进共享计划

- **规则**：
  - §0.7「arena 里的类型不能需要 Drop：`try_alloc` 用编译期断言拒绝这类类型」「增长期间用 `ArenaVec<'q, T>`」（`RULEBOOK.md:214-215`）；
  - §2.4「查询作用域：`arena.try_alloc(T::new(..))`」（`:481`）、「`OB_DELETE`、`x->~T(); alloc.free(x);` | 交给 Drop；arena 里的对象什么也不用做」（`:483`）；
  - §2.5「`ObFixedArray<T>` → `ArenaVec<'q, T>`」（`:498`）。
- **具体例子**：
  - `src/sql/engine/ob_exec_context.cpp:107,323,330,343,347,358,379,410,414,509`：在 exec ctx 的 arena 对象上逐个显式调用析构，包括 op input、phy plan ctx、PL ctx、package guard、虚表迭代器工厂、expr factory、stmt factory、表达式运行期上下文。`src/sql/das/ob_das_factory.cpp:222,234`：`das_op->~ObIDASTaskOp()`、`ctdef->~ObDASBaseCtDef()`。`src/sql/resolver/expr/ob_raw_expr.h:5092-5107`：工厂逐个 `~ObRawExpr()`。sql 里显式析构 362 处，全仓 1,264 处；这些对象持有 ObSEArray 的堆块、tablet 句柄、PL 包状态等。
  - 内存备忘的草图把 `ArenaVec<'q, T>` 定义为 `ManuallyDrop<bumpalo::collections::Vec<'q, T>>`（`migration/design/survey/memory.md:251`），本文既没有给出定义，也没有要求元素不需要 Drop。
  - `src/sql/engine/ob_physical_plan.h:453-454`：`table_locations_` 是 `ObFixedArray<ObTableLocation>`，而 `ObTableLocation` 自带 `ObArenaAllocator inner_allocator_` 和 ObSEArray（`src/sql/optimizer/ob_table_location.h:1130-1138`）。`src/sql/engine/aggregate/ob_groupby_op.h:58` 的 `aggr_infos_` 是 `ObFixedArray<ObAggrInfo>`，ObAggrInfo 里又套 ObFixedArray。sql/engine 和 query 执行头文件里共 168 处 ObFixedArray，大多在计划 spec 里。
- **按规则的译法**：`arena.try_alloc(DasScanOp::new(..))`，编译失败后用 `ManuallyDrop` 包一层绕过断言，析构处什么也不做；`table_locations: ArenaVec<'q, ObTableLocation>` 放进 `Arc<PhysicalPlan>`。
- **为什么错**：
  - C++ 显式释放的资源（tablet 句柄的引用计数、PL 包锁、SmallVec 的堆块）在 Rust 里不再释放；`ManuallyDrop` 绕过断言后是确定的泄漏，`ArenaVec` 里元素的 Drop 也永远不会运行。
  - 计划由 `Arc` 共享，活得比任何请求 arena 都久；放在计划里的 `ArenaVec<'q>` 只能是 `'static`，或者让计划自持 arena（自引用，见 S-02）。
  - IR 节点（ObRawExpr 有 ObSEArray 成员）照"查询作用域 → arena"放进 bump arena 也过不了断言；§0.6 没写明 `QueryArena` 用的是 `IdVec`（全局堆、正常 Drop）还是 bump arena。
- **建议改法**：
  - §2.4 把"arena 里的对象什么也不用做"改为：C++ 对其显式析构、或其成员需要析构的类型，不得放进 bump arena，一律 `try_box` 或拥有型，由所有者正常 Drop。翻译代码禁止 `ManuallyDrop` 和 `mem::forget`（clippy `disallowed_types`/`disallowed_methods`），只有 `oblib::mem` 例外。
  - `ArenaVec<'q, T>` 要求 `T: Copy`，或者加同样的 `!needs_drop` 编译期断言。
  - §2.5 的 ObFixedArray 按所有者区分：计划、缓存、会话等长生命周期对象里的译成 `Box<[T]>`/`Vec<T>`；只有请求级对象里的才是 `ArenaVec<'q, T>`。
  - §0.6 写明 `QueryArena` 用 `IdVec` 存节点，节点可以需要 Drop，子作用域截断时正常 Drop。

### S-08【中】keybtree 的 crossbeam-epoch：`Arc<NodePool>` 保不住节点所在的 memtable arena

- **规则**：§0.9「回收用 crossbeam-epoch，延迟闭包持有 `Arc<NodePool>`」（`RULEBOOK.md:281`）。
- **具体例子**：
  - `src/storage/memtable/mvcc/ob_query_engine.h:142-143`：`btree_allocator_(memstore_allocator_)`、`keybtree_(btree_allocator_)`；`src/storage/memtable/ob_memtable.cpp:100`：`query_engine_(local_allocator_)`。也就是说，节点来自 memtable 自己的分配器。
  - `src/storage/memtable/mvcc/ob_keybtree.h:137`：节点池只持有 `common::ObIAllocator &allocator_` 引用。
  - C++ 销毁时同步清空回收站再 `WaitQuiescent`（`src/storage/memtable/mvcc/ob_keybtree.cpp:1517,1525,1541,1549`），之后 memtable 才释放 arena。
- **按规则的译法**：`struct NodePool<'m> { alloc: &'m MemtableArena, free: [...] }`；删除时 `guard.defer_unchecked(move || pool.free(node))`。`defer` 要求闭包 `Send + 'static`，带 `'m` 的池过不了，只能用 unchecked。
- **为什么错**：crossbeam-epoch 不保证延迟闭包在 memtable 释放前执行，也没有"等这个结构的所有延迟函数跑完"的原语。闭包持有的 `Arc<NodePool>` 只保住池这个结构体，保不住它借用的 memtable arena；memtable 被 t3m 释放后，闭包把节点推回空闲表就是往已释放的内存里写。
- **建议改法**：§0.9 二选一写死：
  - (a) NodePool 自己拥有节点内存（独立分块，单独计入 memstore 用量），`Arc<NodePool>` 才真正保活；
  - (b) 保留"节点借用 memtable arena"，则释放 memtable 前必须调用核心的 `keybtree.quiesce()`：循环 `pin().flush()`，直到这棵树的待回收计数（就是"退休预算"那个计数）归零，等价于 C++ 的 `WaitQuiescent`。
  - 同时禁止 `defer_unchecked` 捕获带生命周期的引用。

### S-09【中】计划深拷贝：常量帧里存着指向自身和计划内存的指针

- **规则**：§0.6「`_px_worker_share_plan_enabled=False` 时改成深拷贝」（`RULEBOOK.md:188`）、「帧：保留 C++ 的字节帧布局」（`:196`）。
- **具体例子**：`src/sql/engine/expr/ob_expr_frame_info.cpp:47-83`，`ObExprFrameInfo::assign` 拷贝常量帧后逐个修 datum 指针：指向帧内的按偏移重定位，指向帧外计划内存的单独深拷贝；`:143,155` 再用指针减法把 `args_`/`parents_` 换算成下标。
- **按规则的译法**：`#[derive(Clone)] struct FrameInfo { const_frames: Vec<Box<[u8]>>, .. }`，然后 `plan.clone()`。
- **为什么错**：派生的 `Clone` 按字节拷贝，副本里的 datum 仍指向原计划的缓冲区。原计划被淘汰（计划缓存只靠 Arc 计数）而 PX worker 还在用副本时，就是释放后使用。字节帧里存绝对地址，本身就让计划成了"经裸指针自引用"的结构。
- **建议改法**：常量帧里的 datum 存相对偏移（或 S-01 的 `DataRef`），不存绝对地址；`PhysicalPlan`、`FrameInfo` 禁止 `derive(Clone)`，深拷贝必须手写、放在帧模块里并配 Miri 测试；如果保留绝对地址，就必须移植 `assign` 的重定位逻辑。

### S-10【中】落盘头"`#[repr(C)]` 加位域访问函数"缺少读写规则，在 storage 里没有合法实现

- **规则**：§0.10「约 25 个按内存布局直接落盘的头：`#[repr(C)]` 加位域访问函数，每个配 golden 文件」（`RULEBOOK.md:306`）；§2.12「含指针的联合体按缺口清单处理」（`:629`）；§0.9 storage `forbid(unsafe_code)`（`:292`）。
- **具体例子**：
  - `src/storage/blocksstable/ob_micro_block_header.h:28-78`：头里有 3 组位域和 `union { int64_t *column_checksums_; int64_t min_merged_trans_version_; }`。
  - `ob_micro_block_header.cpp:240-241`：写盘是 `reinterpret_cast` 后整结构赋值，连指针字段一起写；`:280,287`：读盘是把缓冲区 `reinterpret_cast` 成头再整体拷贝；`:291-292`：让 `column_checksums_` 指进缓冲区。
  - `src/storage/blocksstable/ob_macro_block_common_header.cpp:99-106`：就地 reinterpret 后写字段。
  - storage 和 logservice 里 `reinterpret_cast<…Header*>` 120 处，`reinterpret_cast` 共 837 处。
- **按规则的译法**：`let h: &MicroBlockHeader = unsafe { &*(buf.as_ptr().add(pos) as *const MicroBlockHeader) }`；写盘用 `buf[..64].copy_from_slice(bytes_of(self))`。
- **为什么错**：
  1. storage 禁止 unsafe，这类代码无处安放；
  2. 即使放进白名单，缓冲区偏移也不保证 8 字节对齐，创建未对齐的引用就是 UB（debug 构建会插入对齐检查，触发即 abort）；
  3. 有填充的结构体整体当字节读，是读未初始化内存；
  4. 盘上字节里有指针字段、bool、枚举，直接解释成 Rust 类型可能是非法值；`column_checksums_` 指进缓冲区还会产生未对齐的读。
- **建议改法**：§0.10 写明，落盘头一律用"逐字段小端编解码"的安全代码（`from_le_bytes`/`to_le_bytes`，由 `ob_bitfield!` 或派生宏生成 `read_from(&[u8]) -> ObResult<Self>` 和 `write_to(&mut [u8])`），`#[repr(C)]` 只用于布局文档和 golden 测试。需要零拷贝时用 `HeaderView<'a>(&'a [u8])` 加按偏移读取的访问函数。指针型联合体成员在 Rust 里改成"相对缓冲区的偏移"加访问函数。

### S-11【中】线程局部或长生命周期对象里装借来的指针：RAII 装入不健全，"从 WorkerCtx 里取"写不出来

- **规则**：§0.5「`THIS_WORKER`……改成 `thread_local!` 里的 `WorkerCtx`，每个请求用 RAII 装入、恢复」「`ObCurTraceId`、警告缓冲区、`CURRENT_CONTEXT` 同理」（`RULEBOOK.md:166-168`）；§2.4「`MemCtx` 作为参数显式传递，或从 `WorkerCtx` 里取」（`:489`）。
- **具体例子**：
  - `src/oblib/lib/worker.h:103-104,124-125,148-150`：线程局部的 Worker 持有 `ObIAllocator *allocator_` 和 `sql::ObSQLSessionInfo *session_`；`src/observer/omt/ob_th_worker.cpp:253` 每个请求把 `allocator_` 指向当前 context 的 arena。
  - `src/oblib/lib/oblog/ob_warning_buffer.h:241-259`：线程局部存的是指针；`src/sql/rewrite/ob_transform_utils.cpp:10087-10096` 把它临时指向栈上的 `probe_warning_buf`，用完再恢复。
  - 会话（长生命周期、跨线程）的 `cur_exec_ctx_`（`src/sql/session/ob_sql_session_info.h:1022`）由 `LinkExecCtxGuard`（`src/sql/ob_sql_utils.cpp:3396,3422`）指向当前请求的 ExecContext，读者 147 处。
  - 构造时就从线程局部取 arena 存进成员：`src/data_plane/api/data_plane/access/ob_table_scan_param.h:99` `allocator_(&CURRENT_CONTEXT->get_arena_allocator())`；`src/observer/ob_srv_xlator.cpp:335` 用 `THIS_WORKER.get_sql_arena_allocator().alloc(...)` 造请求处理器。这类调用 41 + 15 处。
- **按规则的译法**：`let _g = WorkerCtxGuard::enter(WorkerCtx { session: &session, arena: &mem_ctx.arena });`（存进 `thread_local!` 前把生命周期擦成 `'static`）；`TableScanParam { allocator: WORKER.with(|w| w.arena()) }`。
- **为什么错**：`thread_local!` 的 `with` 只能在闭包内借出，借用逃不出去，存不进结构体字段；要存进线程局部或会话，就必须把 `'a` 擦成 `'static`。而 RAII guard 可以被 `mem::forget`（安全函数），也会被错误路径上的提前 `return` 跳过恢复，之后线程局部或会话里就是悬垂指针，安全代码再读即 UB。这正是 scoped-tls 只提供闭包式 `set` 的原因。
- **建议改法**：§0.5、§2.4 改为：线程局部和会话只存拥有型或 `'static` 的数据（`Arc<Session>`、`MemAttr`、计数）；需要临时装入借用值的，一律用闭包式接口，如 `WorkerCtx::with_request(&ctx, || …)`、`with_warning_buffer(&mut buf, || …)`，禁止 guard 持有借用。删去"从 WorkerCtx 里取"：凡是"取到 arena 后存起来或返回出去"的调用点改为显式参数（缺口清单新增一类）。会话里的 `cur_exec_ctx_` 改为请求 id 加由请求方持有的状态，或 `Weak<ExecState>`。

### S-12【中】同一对象同时挂在多个容器里，与 `SegQueue<Box<T>>`、`DashMap<K, Arc<V>>`、slab、侵入式链表的规则冲突

- **规则**：§2.7「`ObSpLinkQueue`、`ObLinkQueue` | `crossbeam_queue::SegQueue<Box<T>>`，所有权随 `Box` 转移」（`RULEBOOK.md:548`）、「`FixedHash2` | `DashMap<K, Arc<V>>`」（`:549`）；§0.9「日志回调池 | `slab`，加索引和锁」（`:289`）；§2.5「`ObDList<T>` + `ObDLinkBase` | ……其他地方用 oblib 的侵入式链表（白名单模块，§3）」（`:506`）。
- **具体例子**：
  - `src/oblib/rpc/ob_request.h:44,124`：`ObRequest` 自身是 `ObLink`（在请求队列里），又内嵌 `ObLockWaitNode lock_wait_node_`；后者是 `SpHashNode`（`src/oblib/lib/hash/ob_fixed_hash2.h:28`），挂进锁等待表 `FixedHash2<Node>`（`src/storage/memtable/ob_lock_wait_mgr.h:187-188,331`）。
  - `src/storage/tx/ob_trans_submit_log_cb.h:83-84`：`ObTxLogCb` 既是 `AppendCb`（侵入式 `__next_`，`src/logservice/ob_append_callback.h:28-45`），又是 `ObDLinkBase`（在事务上下文的 `busy_cbs_` 里）；`src/logservice/ob_log_handler.cpp:396` 把它推进 apply 队列 `ObSpLinkQueue queue_`（`src/logservice/applyservice/ob_log_apply_service.h:144`），同时它仍在 `busy_cbs_` 里。
  - ObDList 在 sql/plan_cache、storage/scheduler 等处的节点同时被别的表引用，如 `src/sql/plan_cache/ob_plan_cache_value.h:361` 的 `plan_sets_`。
- **按规则的译法**：请求队列 `SegQueue<Box<Request>>`，锁等待表 `DashMap<Key, Arc<LockWaitNode>>`；apply 队列 `SegQueue<Box<dyn AppendCb>>`，日志回调同时在 `Slab<TxLogCb>` 里。
- **为什么错**：一个值不能同时被 `Box` 独占、又被 `Arc` 或 slab 持有，照规则写不出来；翻译者只能用裸指针加 `Box::from_raw`，而在 slab 或 `Arc` 的内存上 `from_raw` 是双重释放。侵入式链表的安全 API 如果接受 `&mut Node` 而不拥有节点，用户在安全代码里先 drop 宿主对象，就留下悬垂链接。
- **建议改法**：§2.7 增加"多重归属对象"规则：挂在两个及以上容器里的对象，由唯一的所有者（slab 或 `Arc`）持有，其余容器只存 id 或 `Arc` 克隆；队列项一律是 id 或 `Arc<T>`，不是 `Box`；请求在锁等待期间的"挂起"表达为把所有权移进等待表（`DashMap<K, VecDeque<Box<Request>>>`），唤醒时再移回队列。§2.5 写明 oblib 侵入式链表的安全契约：链表拥有节点（`Box`/`Arc` 适配），或节点被 `Pin` 且宿主 Drop 时自动摘链；只有单一归属的场景才允许使用。

### S-13【中】带生命周期的 trait 对象无法向下转型

- **规则**：§2.13「向下转型 `static_cast<D*>(b)` | 对有类型标签的类层次，先查标签再转，标签不符就 abort」（`RULEBOOK.md:642`）；§0.7 查询作用域的类型带 `'q`（`:213`）。
- **具体例子**：`src/sql/engine/expr/ob_expr_subquery_ref.cpp:315-318` 按 op id 取到算子后 `static_cast<ObSubPlanFilterOp *>(kit->op_)`。sql/engine 和 sql/das 里转成具体 Op、Iter、CtDef、RtDef 的 `static_cast` 共 493 处，最多的是 `ObDASScanCtDef`、`ObDASScanRtDef`、`ObDASScanIter`；这些 RtDef 和迭代器在 C++ 里分配在 exec ctx 的 arena 上。
- **按规则的译法**：`kit.op.as_any().downcast_mut::<SubPlanFilterOp<'q>>()`。
- **为什么错**：`Any` 要求 `'static`，`dyn Operator + 'q` 转不成 `dyn Any`；"先查标签再转"在 Rust 里只剩指针转换，需要 unsafe，而 sql 禁止 unsafe。
- **建议改法**：§2.13 写明：需要运行期向下转型的类层次，其 trait 对象必须是 `'static`（不带 `'q`，借用改成 id 或拥有型），用 `as_any()` 转换；做不到 `'static` 的，改成 enum，或在 trait 里为每个可转换的目标提供 `fn as_x(&mut self) -> Option<&mut X<'q>>`。

### S-14【中】指向宿主或父对象的回指针没有可用的译法，包括指向正在执行的算子

- **规则**：§2.4 裸指针成员六选一（`RULEBOOK.md:491`）；§0.6「28 处跨算子旁路……挪进 `ExecRuntime` 的 id 表」（`:194`）；§2.7「`ObTimerTask&` 裸引用登记 | `Arc<dyn TimerTask>`，由定时服务持有」（`:553`）。
- **具体例子**：
  - `src/storage/tx/ob_tx_ctx.h:598`：ObTxCtx 内嵌 `ObMemtableCtx mt_ctx_`；`ob_tx_ctx.cpp:113` `mt_ctx_.set_trans_ctx(this)` 写进 `ObMemtableCtx::ctx_`（`src/storage/memtable/ob_memtable_context.h:568`）。后者再内嵌 `ObTransCallbackMgr trans_mgr_`（`:589`），其 `ObIMvccCtx &host_`（`src/storage/memtable/mvcc/ob_mvcc_trans_ctx.h:371`）回指宿主；回调的 `ctx_`（`:538`）也回指。
  - `src/storage/meta_mem/ob_storage_meta_mem_mgr.h:378-389,494`：t3m 内嵌 `TableGCTask table_gc_task_`，任务里的 `ObStorageMetaMemMgr *t3m_` 回指宿主。
  - `src/sql/engine/subquery/ob_subplan_filter_op.h:138`：`ObSubQueryIterator::parent_` 指向拥有它的 SPF 算子；`ob_subplan_filter_op.cpp:74-80,182-191,306,359` 在子查询表达式求值时（也就是 SPF 自己的 `get_next_row` 调用栈内）读 SPF 的执行状态。
  - 启发式：名为 `host_`/`owner_`/`container_`/`holder_`/`father_` 的回指成员 24 个，放宽到 `parent_`/`mgr_`/`ctx_` 等是 115 个。
- **按规则的译法**：`ctx: Weak<TxCtx>`、`host: &'q MvccCtx`；`TableGcTask { t3m: Weak<T3m> }`；迭代器存进 `ExecRuntime`，`parent: &SpfOp`。
- **为什么错**：
  - 内嵌成员回指宿主，`&'q` 和 `&'static` 都是自引用。`Weak` 要求宿主在 `Arc` 里，而且只能在 `Arc::new_cyclic` 中拿到，可回指是在 `init()` 里才设的（与两段式初始化冲突）；`trans_mgr_` 这样的内嵌成员更没有自己的 `Arc`。
  - SPF 的情况更糟：SPF 正以 `&mut self` 执行，迭代器经任何指针去读它，都与这个 `&mut` 别名（UB）。id 表也解决不了，因为被访问的状态仍在 SPF 里。
- **建议改法**：§2.4 增加第七种选择"宿主回指"：删除字段，改为调用时传参（`fn f(&mut self, host: &Host)`），或者把被回访的状态搬到宿主之外，由双方用 id 访问。SPF 一类：被旁路读写的算子状态（当前 group、batch 计数、rescan 参数）一律搬进 `ExecRuntime` 里按 `SpecId` 索引的状态表，算子和迭代器都只经 `&mut ExecRuntime` 访问，禁止迭代器持有任何指向算子的指针。定时任务要访问的模块按 S-03 是 `&'static`；宿主不是模块的，用 `Weak` 并在 `Arc::new_cyclic` 中构造。

### S-15【中】SIMD 和内联汇编规则落在禁止 unsafe 的 crate 里

- **规则**：§1「SIMD、汇编 | 用 `std::arch` 里同名的 intrinsic，配 `#[target_feature]` 和运行时检测」（`RULEBOOK.md:406`）；§2.13「SIMD intrinsic | `std::arch` 里的同名函数……」（`:655`）、「其余的 `asm!` 放进 §3 的白名单模块」（`:654`）；§3 的白名单（`:660-671`）里没有任何 SIMD 模块。
- **具体例子**：`src/sql/engine/basic/ob_pushdown_filter_simd.cpp:61-66` 用 `_mm512_loadu_si512` 按字节读 ObDatum 数组；`src/storage/blocksstable/encoding/ob_raw_decoder_simd.cpp`、`ob_dict_decoder_simd.cpp`、`encoding/neon/*`、`src/storage/vector_type/ob_vector_add.h`、`src/data_plane/api/data_plane/vector/ob_vector_l2_distance.h` 等。带指针参数的 load/store intrinsic 共 3,991 处，包括 oblib codec 里生成的 bitpacking 代码。
- **按规则的译法**：在 storage、sql、data_plane 里直接写 `_mm512_loadu_si512(ptr)` 和 `if is_x86_feature_detected!("avx512f") { f_avx512(..) }`。
- **为什么错**：带裸指针的 load/store 是 `unsafe fn`；从没有相应 target feature 的上下文调用 `#[target_feature]` 函数也需要 unsafe。这些 crate 都 `forbid(unsafe_code)`，规则无法照写。生成 bitpacking SIMD 代码的生成器（`generate_simd_bitpakcing.py`）在 §0.13 里也没有出现。
- **建议改法**：§3 白名单增加 `oblib::simd`（以及 bitpacking 生成代码所在的模块），由它提供基于切片的安全封装（如 `load_u8x64(&[u8; 64])`）和运行时分派；业务 crate 只调用安全封装。`asm!` 同样只能出现在 `oblib::arch`。§0.13 补上 bitpacking 生成器的去向。

### S-16【中】零拷贝反序列化与 §2.11 的签名不相容

- **规则**：§2.11「`fn deserialize(&mut self, buf: &[u8], pos: &mut i64) -> ObResult`……就地解码」（`RULEBOOK.md:612`）；§2.4「`ObString` 成员 | 查询作用域用 `ObStr<'q>`（即 `&'q [u8]`）」（`:485`）。
- **具体例子**：`src/oblib/lib/string/ob_string.cpp:48-49`，默认情况（`buffer_size_ == 0`）下反序列化时 `ptr_` 直接指向输入缓冲区。`src/storage/tx/ob_multi_data_source_tx_buffer_node.h:101` 的 `ObString data_` 经 `OB_SERIALIZE_MEMBER(ObTxBufferNode, type_, data_, …)`（`.cpp:38`）在回放时就指向日志缓冲区。全仓 ObString 成员 1,032 个，可序列化的类 993 个。
- **按规则的译法**：`impl ObSerialize for TxBufferNode<'q> { fn deserialize(&mut self, buf: &[u8], pos: &mut i64) -> ObResult { self.data = decode_vstr(buf, pos)?; … } }`。
- **为什么错**：`buf` 的匿名生命周期与 `'q` 无关，把 `buf` 的切片存进 `self` 必然被借用检查器拒绝；签名里也没有 arena 参数，没法改成拷贝。
- **建议改法**：§2.11 改为 `trait ObDeserialize<'de> { fn deserialize(&mut self, buf: &'de [u8], pos: &mut i64) -> ObResult; }`（同 serde 的 `'de`）。含借用字段的类型实现 `ObDeserialize<'de> for T<'de>`；拥有型实现 `for<'de> ObDeserialize<'de>`，在内部拷贝；需要拷进 arena 的，另给 `deserialize_in(&mut self, buf, pos, arena: &'q Arena)`。

### S-17【中】FFI 回调：`void*` 擦掉了 Send/Sync，外部线程会回调进来，回调体放在哪里没有规定

- **规则**：§0.14「分配器钩子：zstd `customMem`、zlib `zalloc`、vsag 的 allocator、JSON 解析器的 allocator，重新接到 `MemBudget` 上」（`RULEBOOK.md:386`）；§1「FFI | ……保持 `panic = "abort"`，C++ 异常一律在 shim 里接住」（`:407`）；§3 只允许 -sys、shim 和 parser 视图写 unsafe（`:668-669`）。
- **具体例子**：
  - `src/storage/allocator/ob_vector_allocator.h:71-118`：`ObVsagMemContext : vsag::Allocator`。`ob_vector_allocator.cpp:111-121` 建 context 时显式 `.set_parallel(8)` 和 `ALLOC_THREAD_SAFE`，`:54-81` 用 `ATOMIC_AAF/SAF` 计数——vsag 会从多个线程回调分配器。
  - `src/oblib/lib/compress/zstd_1_3_8/ob_zstd_compressor_1_3_8.cpp:38`：`{ob_zstd_malloc, ob_zstd_free, &allocator_}`。
  - `src/oblib/common/xml/ob_libxml2_sax_handler.cpp:27-63`：15 个 SAX 回调；`:136` 把 `context->_private` 转成 `ObLibXml2SaxParser*`。整个文件 1,029 行业务逻辑，在 oblib 里。
- **按规则的译法**：`extern "C" fn vsag_alloc(ctx: *mut c_void, n: usize) -> *mut c_void { let mc = unsafe { &*(ctx as *const MemCtx) }; … mc.arena.try_alloc_layout(..) … }`；SAX 回调体整个写进 `libxml2_sys`，或者写在 oblib 里。
- **为什么错**：经 `void*` 交给 C 再回到 Rust 的上下文，编译器不再检查 Send/Sync；如果接到 `MemCtx` 或 bumpalo `Arena`（`!Sync`）上，vsag 的并发回调就是数据竞争。外部线程（vsag、OpenMP）没经过 `oblib::thread` 的线程入口，`WorkerCtx`、`MemAttr` 这些线程局部没有初始化。SAX 回调必须解引用 C 指针，放在 oblib 违反 §3，整个搬进 -sys 又把业务逻辑塞进了 FFI crate。
- **建议改法**：§0.14 增加回调规则：每个回调分两层，-sys/shim 里的 `extern "C"` 蹦床只把指针转换成安全类型（`&'a [u8]`、`&'a T`），然后立即调用一个安全的 trait 方法；回调上下文类型必须 `T: Sync`（蹦床里用 `fn assert_sync<T: Sync>()` 做静态断言）；可能被外部线程调用的回调不得访问非 const 初始化的 thread_local；分配钩子只能接到 `MemBudget`（原子计数）加全局分配器，禁止接到 bump arena。

### S-18【中】就地修改借来的字节（ObString、解析树字符串），与 `&[u8]` 的不可变性冲突

- **规则**：§2.5「`const char *` | 内部用 `&[u8]`」（`RULEBOOK.md:511`）；§2.4「`ObString` 成员 | 查询作用域用 `ObStr<'q>`（即 `&'q [u8]`）」（`:485`）；§0.12「读：……`str_bytes()`……写：只提供标量字段的 setter」（`:343-344`）。
- **具体例子**：
  - `src/sql/resolver/cmd/ob_alter_system_resolver.cpp:607-609`：用解析节点的 `str_value_` 构造 ObString 后，`ObCharset::casedn(…, name)` 就地转小写；
  - `src/sql/ob_sql_utils.cpp:3860-3863`：经 `const ObString &name` 的切分视图调用 `str_tolower(...)`，就地改写调用方的字节；
  - `src/sql/plan_cache/ob_prepare_stmt_struct.cpp:371`：往节点字符串里写 `'-'`。
  - `ObCharset::casedn/caseup(` 共 36 处，`const_cast<char *>` 共 509 处。
- **按规则的译法**：`let name: ObStr<'q> = node.str_bytes(); ObCharset::casedn(cs, name /* &[u8] */);`——写不出来，翻译者只能 `as *mut u8` 之后再写。
- **为什么错**：`&'p [u8]` 在它的生命周期内保证所指字节不变；`str_bytes()` 可以多次调用，结果还会被存进 `ObStr` 字段。此后任何经指针的就地写，都是在写共享引用背后的内存，属于 UB。改成"先拷贝再改"又改变了别名可见的语义：C++ 的这些写本来就是为了让后续读者看到小写或负号。
- **建议改法**：§2.5 增加"就地改写借用字节"的规则：缺口清单标出所有就地写点；会被就地写的缓冲区在 Rust 里由所有者借出 `&mut [u8]`，写完才以 `&[u8]` 共享，不能先交出 `&[u8]`。解析树字符串需要就地改写的，由 parser crate 提供要求独占令牌的接口（如 `fn str_bytes_mut(self, arena: &mut ParseArena) -> &mut [u8]`），`str_bytes()` 的结果不得跨过任何 `&mut ParseArena` 调用。

### S-19【中】`ParseNode<'p>` 视图的其他缺口：空指针、非标量写、跨线程长期持有、栈上节点

- **规则**：§0.12「`ParseNode<'p>` 句柄：`Copy`、`!Send`，生命周期就是语句的解析 arena……写：只提供标量字段的 setter」（`RULEBOOK.md:342-344`）；§2.9「`node->str_value_` / `str_len_` | `node.str_bytes()`」「写节点的字段 | 对应的 setter，只允许写标量字段」（`:592-593`）。
- **具体例子**：
  - 空指针：`src/sql/parser/parse_node.c:52` 以及词法器里多处把 `str_value_` 置为 NULL，`str_len_` 为 0。
  - 非标量写：`src/sql/ob_sql_utils.cpp:203,212`（`replace_questionmarks`）把 `str_value_` 指向参数 ObObj 的缓冲区，或指向 `ObNumber::format()` 返回的线程局部轮转缓冲（`src/oblib/common/number/ob_number_v2.cpp:2288-2305`，64 次调用后就被覆盖），而且 NUMBER 分支不更新 `str_len_`；`src/sql/plan_cache/ob_prepare_stmt_struct.cpp:372-373` 改 `str_value_` 和 `str_len_`；`src/pl/ob_pl_resolver.cpp:2031-2032` 指向字符串字面量；`src/sql/plan_cache/ob_sql_parameterization.cpp:747`、`ob_pl_resolver.cpp:9580-9582` 改 `children_[i]`。
  - 长期持有：`ObPsStmtInfo::add_fixed_raw_param`（`ob_prepare_stmt_struct.cpp:345-378`）把节点深拷贝进 PS 缓存的分配器，挂在 `raw_params_`（`src/sql/plan_cache/ob_prepare_stmt_struct.h:264`）上，跨会话、跨线程使用。
  - 栈上节点：解析器目录外有 35 处 `ParseNode x;`，如 `src/sql/engine/expr/ob_datum_cast.cpp:8897`、`src/sql/engine/expr/ob_expr_demote_cast.cpp:71`、`src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp` 里的 `ret_node`。
- **按规则的译法**：`fn str_bytes(self) -> &'p [u8] { unsafe { slice::from_raw_parts(n.str_value_, n.str_len_ as usize) } }`；`fn set_str(self, ptr, len)`；PS 缓存里存 `ParseNode<'static>`。
- **为什么错**：
  - `slice::from_raw_parts` 要求指针非空，长度为 0 也不例外，空指针直接是 UB。
  - 字符串被改指到参数缓冲或线程局部轮转缓冲后，`&'p [u8]` 声称的生命周期是假的；`str_len_` 不同步时还会越界读。
  - PS 缓存里的节点既不在语句 arena 里、又要跨线程，`!Send` 的 `ParseNode<'p>` 表达不了。
  - 栈上节点没有任何构造接口。
- **建议改法**：§0.12 补充：
  - `str_bytes()` 遇到空指针返回 `&[]`，长度为负按 0 处理；
  - 指针字段的 setter 只接受来自同一解析 arena 的 `&'p [u8]` 或 `ParseNode<'p>`，其他来源先拷进 arena（如 `replace_questionmarks` 的两处）；`children_` 的结构改写提供 `set_child(i, ParseNode<'p>)`；
  - PS 或计划缓存里需要长期持有的节点，转成拥有型的 `OwnedParseTree`（`Send + Sync`，按值存储）；
  - 栈上节点用 `LocalParseNode`（拥有一个 `sys::ParseNode` 值），提供 `as_node(&mut self) -> ParseNode<'_>`。

### S-20【中】跨 crate 的析构与 pimpl：`Drop` 只能写在类型所在的 crate

- **规则**：§0.4 的四种拆法：合并、下移、trait 加类型化槽位、链接期 seam（`RULEBOOK.md:106-119`）；§2.4 裸指针成员六选一，没有类型擦除的选项（`:491`）。
- **具体例子**：`src/data_plane/api/data_plane/transaction/ob_tx_exec_result.h:62` 和 `src/data_plane/api/data_plane/tmp_file/ob_tmp_file.h:76` 都是 `void *impl_`，析构函数却定义在 storage：`src/storage/tx/ob_trans_define_v4.cpp:1229-1232`（经 `ObTxExecResultAccess::destroy` 把 `impl_` `static_cast` 成 storage 的 `ObTxExecResultImpl` 再 `OB_DELETE`，`:49-84`），以及 `src/storage/tmp_file/ob_tmp_file_manager.cpp:411-414`。启发式：在声明模块之外定义的析构 54 个，序列化或 TO_STRING 的函数体 67 个；全仓 `void *` 成员 121 个。
- **按规则的译法**：seam 表给 `~ObTxExecResult` 选"trait + 槽位"或"下移"；`impl_` 译成 `*mut c_void`。
- **为什么错**：Rust 的 `Drop` 只能实现在类型所在的 crate；外来 trait（`ObSerialize`、`ObToString`、`PartialEq`）也不能在第三个 crate 里给外来类型实现（孤儿规则）。"trait + 槽位"不适用于 Drop，"下移"又因为方法体依赖 storage 的内部类型而做不到；照 §2.4 只能留下 `*mut c_void` 和上层的 unsafe 转换。
- **建议改法**：§0.4 增加"trait impl 与析构的落点"：析构和外来 trait 的 impl 一律落在类型所在的 crate；跨层 pimpl 译成 `impl_: Option<Box<dyn Any + Send + Sync>>`，由下层的 Drop 自动析构上层对象，上层用 `downcast_mut` 取回；方法体依赖上层的 trait impl，改成下层的 impl 调用槽位钩子。§2.4 把"类型擦除 `Box<dyn Any + Send + Sync>` 或 enum"加入裸指针成员的可选项，`void *` 成员由缺口清单逐个分类。

### S-21【中】柔性数组成员（`char buf_[0]`）没有译法

- **规则**：§2.12 只讲 `#[repr(C)]` 结构、联合体和位域（`RULEBOOK.md:629-630`）；§3 白名单不含 sql 的行存储。
- **具体例子**：非静态的零长或柔性数组声明 69 个（oblib 36、storage 15、sql 14、share 3、query 1），如 `src/storage/memtable/mvcc/ob_mvcc_row.h:99` 的 `char buf_[0]`（`ob_mvcc_trans_ctx.cpp:1739` 把它 reinterpret 成 `ObMemtableDataHeader`），`src/sql/engine/basic/ob_chunk_datum_store.h:165,522` 中 `StoredRow`、`Block` 的 `payload_[0]`，`ob_ra_datum_store.h:135,202,247`，`ob_temp_block_store.h:166,225`。
- **按规则的译法**：`#[repr(C)] struct StoredRow { cnt: u32, row_size: u32, payload: [u8; 0] }`，经 `&StoredRow` 加指针运算读载荷。
- **为什么错**：从 `&StoredRow` 派生的指针越过 `size_of::<StoredRow>()` 访问载荷，在 Stacked Borrows 下是 UB，Miri 默认就会报；sql 的行存储不在白名单里，连指针运算都写不了。
- **建议改法**：§2.12 增加：头加尾随数据的结构一律表示为字节缓冲上的视图，如 `StoredRowView<'a>(&'a [u8])`、`StoredRowMut<'a>(&'a mut [u8])`，头字段用 S-10 的逐字段编解码读写；只有 §3 白名单（storage_core 的 TxNode）允许原始指针形式，而且载荷指针必须从分配得到的原始指针派生，而不是从 `&Header` 派生。

### S-22【低】链接期 seam 的导出属性会被 `forbid(unsafe_code)` 拒绝

- **规则**：§0.4「链接期 seam：……上层 `#[unsafe(export_name = …)]` 定义」（`RULEBOOK.md:118`）；§3「其余 crate 一律 `#![forbid(unsafe_code)]`」（`:660`）。
- **具体例子**：用于 seam 表里经测量确认的热路径符号，如 layering 备忘 §3.1 提到的 `query::das_scan_next_row`（storage 里 18 处逐行调用）。
- **为什么错**：`unsafe_code` lint 覆盖 `no_mangle`、`export_name`、`link_section`，而定义侧所在的 sql、storage、observer 都是 forbid，规则自相矛盾；声明侧调用 `extern "Rust"` 函数同样需要 unsafe（除非写成 `unsafe extern` 块里的 `safe fn`）。签名一致只靠宏保证，链接器不检查类型。
- **建议改法**：§3 白名单增加"seam 宏展开出的导出和声明"（宏展开处用 `#[allow(unsafe_code)]`，CI 用 grep 校验只出现在宏展开里），声明侧规定用 `unsafe extern "Rust" { safe fn … }`；或者删掉第 4 种拆法，全部用槽位。

### S-23【低】全局分配器的实现约束没写

- **规则**：§0.7「`#[global_allocator]` 是自带 jemalloc 上的一层薄封装；加一个线程局部的 `MemAttr`……只按 ctx 计数」（`RULEBOOK.md:219-220`）。
- **具体例子**：C++ 侧 `ObMallocHookAttrGuard` 105 处，线程局部属性在 `src/oblib/lib/alloc/alloc_struct.h:560-573`；内存备忘给出 ctx 归账的三种做法：每个 ctx 一个 jemalloc arena、每块加 16 字节头、抽样（`migration/design/survey/memory.md:210-213`）。
- **为什么错**：`GlobalAlloc` 里 unwind 是 UB；重入分配、加锁、访问懒初始化或带 Drop 的 thread_local（线程退出后访问会 panic）都会出事。采用"每块加头"时，对齐大于 16 的 `Layout` 和 `realloc` 必须单独处理，否则就是错位或越界。
- **建议改法**：§0.7 补充：分配器里只允许访问 `const` 初始化、没有 Drop 的 thread_local；不得分配、加锁、panic；ctx 归账在 alloc 时按"当前属性"计数，dealloc 时按块头或 jemalloc arena 归还；块头方案要处理 `align > 16` 和 `realloc`；配一组在 Miri 和 ASan 下运行的分配器单测。

### S-24【低】BUG 规则只豁免数据竞争，没有豁免其他 UB

- **规则**：§0.1「原版的缺陷照样复刻……唯一的例外是在 Rust 里会构成数据竞争的写法」（`RULEBOOK.md:29-30`）。
- **具体例子**：S-19 的 `replace_questionmarks` 把节点字符串指向会被覆盖的线程局部缓冲且不更新长度（`src/sql/ob_sql_utils.cpp:212`）；S-10 的未对齐 reinterpret；S-05 的非枚举 ItemType；`src/sql/parser/parse_malloc.cpp:127-128` 中 `parse_realloc(NULL, …)` 分配的块不带长度头，一旦再对它 realloc 就会读块前 8 字节（`:133`）；现有调用方都先用 `parse_malloc`，这条路径是否可达要由缺口清单确认。
- **为什么错**：这些写法在 C++ 里"碰巧能跑"，在 Rust 里照样复刻就是 UB，规则却要求复刻。
- **建议改法**：把例外扩成"在 Rust 里会构成未定义行为的写法"（数据竞争、越界、悬垂、未对齐、读未初始化内存、非法枚举值），改成保持可观察行为的安全写法，同样标 `BUG(port)`。

### S-25【低】子作用域截断后，旧的 `ExprId` 会静默指向新节点

- **规则**：§0.6「临时上下文……改成可截断的子作用域。子作用域结束时，丢弃其中新建的节点，但保留对外层节点的修改。这和 C++ 释放临时 MemoryContext 的效果完全一样」（`RULEBOOK.md:179-180`）；§5 第 11 项只查"临时作用域里有没有向外层分配"（`:764`）。
- **具体例子**：ir-graphs 备忘 §2.3 指出，`src/sql/rewrite/ob_transform_utils.cpp:3590-3625` 让临时节点和语句级节点混在同一组数据结构里。
- **为什么错**：截断后，外层节点里留下的 id 不会报错；之后新分配的节点复用同一个下标，外层的引用就静默地指向了无关节点。C++ 里同样的情形是悬垂指针（往往会崩溃暴露），Rust 版却变成无声的错误结果。`ExprId` 不是代际 id，"旧 id 访问会报错"只对 slotmap 管理的语句成立。
- **建议改法**：子作用域内分配的 id 带作用域代数（高位存 generation；或 debug 构建在截断时记录水位线、访问时检查），截断后再访问返回 `OB_ERR_UNEXPECTED`；缺口清单第 11 项同时检查"外层节点是否引用了作用域内新建的节点"。

### S-26【低】ParseNode 的布局随宏变化，静态断言只是自证

- **规则**：§0.12「`ParseNode`/`ParseResult` 的布局：用 bindgen 离线生成并入库，加 `size_of`/`offset_of` 静态断言」（`RULEBOOK.md:341`）。
- **具体例子**：`src/query/api/query/parser/parse_node.h:191-194`（`token_off_`、`token_len_`）、`:260-266`、`:360-366`（`ParseResult` 尾部字段）都在 `#ifdef SQL_PARSER_COMPILATION` 下；`src/query/BUILD.bazel:329-331` 说明 seekdb 的服务端解析器有意不定义这个宏。
- **为什么错**：Rust 侧的 `size_of`/`offset_of` 断言比较的是 bindgen 的输出和 Rust 自己，两者同源。build.rs 用 `cc` 编 C 时，如果宏设置与生成 bindgen 时不同（比如照抄旧 CMake 的代理解析器配置），C 侧的结构就多出字段或少掉字段，Rust 读写 `ParseResult` 会越界。
- **建议改法**：离线生成 bindgen 时记录 `-D` 参数，build.rs 用完全相同的参数编 C 并校验；让 C 侧导出 `parse_node_layout()` 返回 `sizeof`/`offsetof`，在 build.rs 的探针或启动自检里与 Rust 常量比对。

## 3. 缺失的规则

源码里存在、本文没有给出译法（或给出的两条规则互相冲突）的构造：

1. **指向自身成员或自身内联缓冲的指针**：初始化列表里 19 处、11 个文件（如 `src/oblib/common/timezone/ob_timezone_info.h:694`、`src/oblib/lib/queue/ob_link_queue.h:65,103,277`、`src/storage/blocksstable/ob_datum_rowkey.h:125`）；placement new 进自身 `*buf*_` 31 处；`new (&member)` 134 处；`src/sql/ob_result_set.h:482-496`、`src/storage/tx/ob_tx_ctx.cpp:118,142`。§2.4 的六种选择都不适用，见 S-02 (c)。
2. **"默认指向自带分配器、也可改指外部分配器"的成员**：`src/oblib/common/rowkey/ob_rowkey_info.cpp:104` `allocator_(&arena_)`、`src/storage/ob_resource_map.h:200`、`src/storage/blocksstable/ob_datum_rowkey.h:125`、`src/sql/optimizer/ob_table_location.h:1130-1131`。一个字段有时借自身、有时借外部，`&'q` 写不出；需规定改成"外部 arena 参数"和"拥有型缓冲"二选一。
3. **返回指向静态或线程局部轮转缓冲的指针**：`ObNumber::format()`（`src/oblib/common/number/ob_number_v2.cpp:2288-2305`，调用 33 处）、`ObLogCursor::to_str()`（`src/oblib/common/log/ob_log_cursor.cpp:77-83`，进程级 `static char buf[512]`）、`ObLogger::show_probe()`（`src/oblib/lib/oblog/ob_log.cpp:488-490`）。译成 `&'static [u8]` 不健全；需规定改为写进调用方的缓冲或返回拥有型。
4. **运行期会被写的非线程局部静态量**：启发式 34 处，如 `src/oblib/lib/oblog/ob_log.cpp:1420-1421`、`src/rootserver/freeze/ob_major_merge_progress_checker.cpp:352` 的 `static int64_t enter_cnt`。§2.13 只说用 `LazyLock`/`OnceLock`，可变的只能写成 `static mut`（需要 unsafe）；需规定改成原子量或 `Latch<T>`。
5. **packed 结构**：`__attribute__((packed))`/`#pragma pack` 68 处（storage/blocksstable 14 个文件、sql/engine 7 个、oblib/common 5 个，含 `ObDatum`）。`#[repr(C, packed)]` 的字段不能取引用（E0793），§2.12 没提 packed；需规定 packed 结构只按值读写字段、不得 derive 需要引用字段的 trait，或改为字节视图（同 S-10）。
6. **会话对象的跨线程共享**：`src/sql/ob_sql_trans_control.cpp:332-350` 的 `kill_tx` 一族在其他线程访问会话；`ObTxDesc *&get_tx_desc()` 127 处调用。会话的 Send/Sync 模型、哪些字段进锁，§2.7 的类内规则覆盖不到；在共享会话上 `get_tx_desc() -> &mut Option<Arc<dyn TxDesc>>` 写不出来。
7. **运行期新建、指向计划表达式的 RtExpr**：`ObDatumCaster`（`src/sql/engine/expr/ob_datum_cast.cpp:13070` `cast_expr.args_[0] = const_cast<ObExpr*>(&src_expr)`），以及 column_conv 改成"局部副本"的修复（`ob_expr_column_conv.cpp:743-746`）。`RtExprId` 只能指计划数组里的表达式；需要"计划 id 或局部表达式"的引用类型，否则局部副本改不了 `args[0]`。
8. **由元素地址反推下标**：`src/sql/engine/aggregate/ob_aggregate_processor.h:1129` `&info - aggr_infos_.get_data()`、`src/sql/engine/expr/ob_expr_frame_info.cpp:143,155,405`。在引用上做指针减法需要 unsafe；需规定随身携带下标。
9. **被别的对象的锁保护的非标量字段**：§2.7 规则 3 说"被保护的字段改成原子量"（`RULEBOOK.md:560`），如 `src/storage/tx/ob_tx_redo_submitter.h:190-218` 在调用方持有 TxCtx 的锁时操作其状态。Vec、哈希表改不成原子量，只剩被禁用的 `UnsafeCell`；需规定把字段移进那把锁的 `T`，或拆出由锁拥有的状态结构。
10. **嵌在整数里的锁位与带标签的指针**：`ObSmallSpinLock::AsLock`（`src/oblib/lib/lock/ob_small_spin_lock.h:95`）、`ObPtrSpinLock`（`:218-262`，用户 `src/oblib/lib/container/ob_ext_ring_buffer_impl.h:80`）。字段必须全程原子访问；给指针打标签要用 strict provenance 的 `map_addr`、`AtomicPtr::fetch_or`，否则 Miri 报错。
11. **按运行期标志复用的 arena**：`ObEvalCtx::get_reset_tmp_alloc()`（`src/query/api/query/engine/expr/ob_expr.h:260-271`，`TempAllocGuard` 在 `:170-178`）。借用检查器看不到运行期标志；需规定 reset 只能经帧模块、并用 `&mut` 令牌表达（与 S-01 一起改）。
12. **外部线程调用 Rust**：vsag 内部线程调用分配器（S-17）；concurrency 备忘 §6 也指出共存期 C++ 线程会调用 Rust。线程局部的惰性初始化、没有 `WorkerCtx` 时的行为都没有规定。
13. **只读共享计划里的侵入式链表**：`src/sql/plan_cache/ob_cache_object.h:231` 的 `ObDList<ObPreCalcExprFrameInfo> pre_calc_frames_` 位于 `Arc<PhysicalPlan>` 里。照 §2.5 会译成侵入式链表（`!Sync`，需要白名单里的 `unsafe impl Sync`）；需规定只读计划里一律用 `Vec`/`Box<[T]>`。
