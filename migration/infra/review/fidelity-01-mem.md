# 行为一致性审查：`01-mem.md`

> 来源：行为一致性审查者为 `migration/infra/api/01-mem.md` 派出的子审查（`claude-opus-5-5`，max effort），2026-10-11 完成。父审查者在用量上限处中断，这份结果由编排者原样存档。探针在作业临时目录 `review-fid/01/`，没有入库。

审查 `migration/infra/api/01-mem.md` 的行为一致性：用户或裁判能否看到按这份 API 写出的代码与 C++ 不同。通读了全文，读源码核对了 43 条引用，做了 1 个编译器探针。

## 发现

先说结论：11049 的算法与 C++ 逐行一致，已核对，包括 u16 回绕、每 512 次刷新上限、先除后乘、`>=` 比较、报错文本里的两个空格，以及 9 个检查点。另外几项也一致：`alloc(0)` 报 4013；jemalloc 下 ctx hold 恒为 0，`ob_ra_datum_store` 的 80% 落盘判断因此不变；`ObBlockAllocMgr`；向量限额检查的条件；`je_malloc_conf`；`MALLOC_BACKEND` 的报错文本。问题集中在计账口径和两处宏语义上。

**F-01【中】ArenaVec 和 05 的带计账容器漏了 `ObSEArrayImpl::reserve` 按 block_size 取整，S3/S7 说的"统计数与 C++ 相同"不成立，T3 还把不同的序列写成了测试**
- **文档**：
  - §2.2 :249："扩到 max(2×cap, cap+additional)，最少 1，与 ObArray 的增长相同（ob_array.h:610-614）"；
  - S7 :819，S3 :782；
  - T3 :1034："增长序列 1、2、4、8…"；
  - Q3(b) :1388。
- **C++**：
  - `reserve` 把字节数向上取整到 `block_size_` 的整数倍，capacity 取 `new_size/sizeof(T)`（ob_array.h:365-393，取整在 :369-372）。
  - `block_size_` 缺省 `OB_MALLOC_NORMAL_BLOCK_SIZE`=7936（:287），并取 `max(sizeof(T), bs)`（:594）。
  - push_back 的翻倍（:610-614）发生在取整后的 capacity 上。所以 `ObArray<int64_t>` 第一次 push 就从块分配器拿 7936 字节（capacity 992）。
- **按文档的 API**：
  - 统计记 8、16、32… 字节。
  - 反方向也有：C++ 里用缺省 `ModulePageAllocator` 的 ObSEArray 走全局堆，不进任何 arena（page_arena.h:116-131）；按 RULEBOOK §2.5 改成 ArenaVec 后，它会计入 arena。
  - 文档自身也不一致：§2.2 的 ArenaVec 注释说 auto_free 数组由 ArenaVec 承载，§2.12 :752 却说走全局堆。C++ 实际绑定的是 `CURRENT_CONTEXT->get_allocator()`，即 arena（ob_array.h:84-91、:484-497）。
- **会改变的决策**：
  - query range 退化：KeyPart 的数组就在 range arena 上（ob_key_part.cpp:376-377、:514），而 `ObExprRangeConverter` 用 `allocator_.used()-mem_used_` 与 `range_optimizer_max_mem_size` 比较（ob_expr_range_converter.cpp:111）。
  - 排序落盘：`quick_sort_array_` 经 `ModulePageAllocator(get_malloc_allocator())` 分配（ob_sort_op_impl.cpp:762-763），按 usable_size 计入 `mem_context_->used()`，再进 `update_used_mem_size`（:1027）。
  - 落盘与否会改变 ORDER BY 并列行的次序（内存 introsort 对比外排归并）和哈希算子的输出序，这违背 D5。
- **在哪里看得到**：只在接近内存边界的大查询上，用户看得到；裁判看不到（见 F-04）。
- **改法**：
  1. ArenaVec 和带计账容器都照抄 reserve 的取整：构造时可传 block_size，缺省 7936；统计按取整后的字节记。
  2. `ModulePageAllocator(WORK_AREA)`+`set_allocator(malloc)` 这类（ob_sort_op_impl.cpp:562、:582、:761）要同时记两处：
     - 工作区预算记请求大小（TrackedAllocator，ob_malloc.h:262-268）；
     - 上下文的 malloc 计数记 `usable_size(size+32)`，Header 是 `alignas(max_align_t)` 的 32 字节。
  3. 清单第 11 类按 C++ 的块分配器归类（全局堆 / 指定的 arena / CURRENT_CONTEXT 的 arena），不只按生命周期类别。
  4. T3 改成金标准：C++ 在 ObArenaAllocator 上对 `ObArray`、`ObSEArray<T,4>` 逐次 push，打印 used/total。
- **amendments.tsv:13**：只提了"容器不计入"，没提取整，也没提反向多计。

**F-02【低】`ObMallocHookAttrGuard` 并不是"jemalloc 下不影响任何分配"：VIB* 标签决定 CRoaring 的内存是否计入 vector_memory_limit**
- **文档**：§2.12 :743 写"删掉整行……属性只在 obmalloc 的 hook 里读"（alloc_struct.h:560-573）；E13 :1292。
- **C++**：
  - `roaring_malloc/roaring_free` 读 `get_tl_mem_attr()`，标签以 "VIB" 开头时，把 CRoaring 的分配记进 `vec_idx_used_`（ob_rb_memory_mgr.cpp:39-50、:77-89）。
  - 这个钩子在 server 初始化时无条件安装（ob_server.cpp:2341）。
  - 适配器里有约 20 处 `ObMallocHookAttrGuard(ObMemAttr("VIBitmap…"))`（ob_plugin_vector_index_adaptor.cpp:187…3003）。
  - jemalloc 下 `ObVectorAllocator::hold()` = `all_used_mem_ + vec_idx_used_`（ob_vector_allocator.cpp:29-45），`ObVectorMemContext::alloc` 用它卡限额（:135-146）。
- **按文档的 API**：删掉 guard，再加上 CRoaring 换成 roaring crate，`vec_idx_used_` 恒为 0，限额会更晚触发；V$OB_VECTOR_MEMORY 的数值也不同。Q3 第 3 项提到了 `vec_idx_used_`，但只归因于换库，A.4 里也没有这一项。
- **在哪里看得到**：用户调小 vector_memory_limit、或者大索引逼近限额时，C++ 分配失败（日志记 -7603、返回空，ob_vector_allocator.cpp:139-143），Rust 成功。客户端最终收到的错误码没有核实。裁判的 18 个用例都设为 0，即 50% 内存，碰不到。
- **改法**：改正这条理由。二选一：
  - 保留一个"向量位图作用域"的线程局部标志，位图包装在作用域里把内存增量记进向量预算；
  - 或者在 A.4 的 mem-accounting 里点名这一项。

**F-03【低】`WITH_CONTEXT`/`CREATE_WITH_TEMP_CONTEXT` 的译法有两处与 C++ 不同**
- **文档**：§2.12 :749-750；E5 :1147-1154；E8 :1198-1203。
- **C++**：`CONTEXT_P` 展开成 `for (_S _s{…}; OB_SUCC(ret) && _s.i_-- > 0; _s.i_--) if (OB_SUCC(_s.get_ret()))`（context.h:64-66）。由此：
  1. 进入时调用方的 ret 已经失败，body 就不执行；
  2. WITH 遇到空 ctx 时，只把宏内部的 `_s.ret_` 设成 OB_INVALID_ARGUMENT（:806-820），调用方的 ret 不变，body 被跳过，函数返回原来的 ret。
- **按文档的 API**：
  - WITH_CONTEXT 遇到空 ctx 写 `ret = OB_INVALID_ARGUMENT`（-4002）；
  - CREATE_WITH_TEMP_CONTEXT 译成无条件执行的块。
- **例子**：
  - ob_sql_utils.cpp:510-516：`init_datum_param_store` 失败后紧接着进入这个宏。C++ 跳过 body、返回原错误；按文档会继续执行 body，ret 可能被 body 里的 `ob_fail!` 覆盖。
  - ob_physical_plan.cpp:268、:298，ob_inner_sql_result.cpp:189、:215：上下文为空时 C++ 返回成功，Rust 返回 -4002。
- **在哪里看得到**：只在错误路径上，客户端收到的错误码不同。
- **改法**：两个宏都译成 `if ret.is_ok() { … }`；空 ctx 时跳过 body、不改 ret。

**F-04【低】Q3 和 amendments.tsv:13 里的裁判依据读反了**
- `join_many_table.test:13` 把工作区设成 100，缺省是 5（ob_system_variable_init.json:1798-1801），所以它是放大工作区，不是"调低强制落盘"。被比对的查询带 `--sorted_result`（:41），:86 再恢复成 5。`join_many_table_single_field` 也是先设 100 再恢复 5，表只有 2 行。
- 裁判里没有任何用例调低工作区、设置 `range_optimizer_max_mem_size`、把 vector_memory_limit 设成非零，也没有 11049 的期望。
- 所以 mem-accounting 用户可见、裁判不可见，提议的裁判改动（核对行序、加 `--sorted_result`）没有必要。

**F-05【低】SMART_VAR 的两条说法不对**
- S21 :977 说"数组形式 C++ 不清零"，不对：C++ 用 `new (ptr) T{args}` 和 `new (buf) T{args}`（ob_smart_var.h:175、:178），参数为空时是值初始化。用基线 clang 17 做的探针显示，堆路径和栈路径都是全零。两边都清零，没有行为差异，只是事实写错。
- S21 :978 让 errsim 只在 `cfg(feature="errsim")` 下生效，而且只对 `heap_var!` 和大于 128 KB 的对象生效。C++ 的 `ERRSIM_POINT_DEF` 在 release 构建里也生效（04 的修订 #2，tsv:33），并且覆盖所有走堆的 SMART_VAR，包括因栈余量不足而走堆的（ob_smart_var.h:40-66）。结果是 `ALTER SYSTEM SET_TP` 注入的效果不同。

**F-06【低】`try_str`/`try_bytes` 表达不了 `ob_write_string` 产出的 NULL**
- **C++**：源为 NULL、长度不大于 0、或者分配失败时，都写 `dst.assign(NULL,0)`（ob_string.h:811-815）。
- **按文档的 API**：返回 `Ok(&[])`（§2.2 :204-207、S1 :774、§2.12 :733）。在 06-A3 提议的 `Option<ObStr>` 位置上，自然的写法 `Some(arena.try_str(..)?)` 会把 C++ 的 NULL 变成 Some(空串)。
- **C++ 区分两者的点**：ob_core_table_proxy.cpp:855-866，NULL 拼成 SQL 的 NULL，空串拼成 `''`。
- **改法**：加 `try_str_opt`，空源或 NULL 源返回 None，失败时 dst 也置 None；与 06-A3 一起定。

**F-07【低】MemCtx 的口径与"与 C++ 相同"不符**
- **C++**：
  - `create_context` 用父上下文的 freeable 分配器分配子上下文的结构体（context.h:528），所以父的 hold、tree_mem_hold 都包含它。
  - `MemoryContext::metadata_size()`（context.cpp:65-68）被计划缓存的计账直接加上（ob_plan_cache.cpp:1665、:1673）。
- **按文档的 API**：子 MemCtx 由拥有者持有，不计入父；也没有 `metadata_size` 的对应物，译者只能自己编一个值。
- **在哪里看得到**：11049（已在 A.4 query-mem-limit 名下）；计划缓存的淘汰点（A.4 plan-mem 只写了"视图数值归一化"）。
- **改法**：S11 改成承认这处差异；给出常量 `METADATA_SIZE`，取基线的 sizeof，由金标准测出；plan-mem 的条文补上"淘汰时机可能不同"。

**F-08【低】11049 只保护首条语句，显式传参时要钉住这条路径**
- **C++**：非重试、且是多语句里第 1 条的语句，直接在 worker 的上下文里执行（obmp_query.cpp:399-415）。重试和后续语句走 `process_with_tmp_context` 新建的根上下文（:417、:461-489），这个上下文不在 tracker 的树里。
- **按文档的 API**：E5 只示范了重试路径，Q2 的 `mem_ctx_sources.tsv` 也没有规定首条语句的 MemCtx 来源。译者一律传新的 MemCtx，11049 就永远不触发；一律传 worker 的，重试也会触发。
- **改法**：在 S12 里写明两条路径，把 obmp_query.cpp:410 这一路钉到 worker 的 MemCtx，并加一个单测。

**F-09【低】`usize` 形参和 `try_bytes_zeroed` 的用途说明会改变 0 字节和负数的语义**
- **C++**：
  - `ob_malloc` 和 arena 的 `alloc`：len 不大于 0 时返回 NULL，调用方报 4013；
  - `reserve(n)`：n 不大于当前容量（包括负数）时是成功的空操作（ob_array.h:368）。
- **按文档的 API**：`try_bytes_zeroed(len: usize)` 被列为"ob_malloc + MEMSET"的译法（§2.3 :295），len=0 时成功；`try_vec`、`ArenaVec::try_reserve` 收 usize，译者把 i64 `as usize` 后，负数变成巨大的请求，报 4013。
- **改法**：原始字节接口收 i64，照 C++ 判断不大于 0；reserve 一类收 i64 的绝对容量；删掉"ob_malloc + MEMSET"这个用途说明。

## 引用抽查

共 43 条：CONFIRMED 35，PARTIAL 3，WRONG 5。

| 文档行 | 断言 | 源码位置 | 结论 | 说明 |
|---|---|---|---|---|
| :862 | update_mem_limit 先除后乘，pct=0 → 0 | ob_memory_tracker.cpp:30-36 | CONFIRMED | |
| :863-867 | 先取 tree_mem_hold、u16 计数、%512 刷新、`>=` | :37-56 | CONFIRMED | |
| :869 | 先自增再取模，只在装了 tracker 时计数 | :57-65 | CONFIRMED | |
| :861 | guard 构造和析构都清零 | ob_memory_tracker.h:52-60 | CONFIRMED | 析构是清零，不是恢复；只有一处 guard，等价 |
| :868 | 11049 文本（两个空格）、HY000 | ob_errno.def:1880 | CONFIRMED | |
| :862 | query_memory_limit_percentage 缺省 32 | parameters.yaml:2083-2089 | CONFIRMED | |
| :873-881 | 9 个检查点 | ob_th_worker.cpp:245、:333；ob_exec_context.cpp:679；ob_transform_rule.cpp:287；ob_select_resolver.cpp:1006；ob_raw_expr.h:5074-5079、:4999；parse_node.c:183-187 | CONFIRMED | n 只有 32 和 1024 |
| :879-880、:81 | `ob_memory_tracker_wrapper.cpp:20-28` | executor 下的同名文件只有 22 行，没有函数体 | PARTIAL | 函数实际在 `src/sql/parser/ob_memory_tracker_wrapper.cpp:20-28` |
| :871 | 内部 SQL 的 guard 被注释掉 | ob_inner_sql_connection.cpp:682 | CONFIRMED | |
| :377、:843-845 | jemalloc 下 hold/used 的组成 | context.h:366-395 | CONFIRMED | |
| :848-850 | 带锁节点的 tree_mem_hold 恒为 0 | context.h:600-613 | CONFIRMED | |
| :851 | 临时上下文 parent=nullptr；根带 ADD_CHILD_THREAD_SAFE | context.h:843；context.cpp:35 | CONFIRMED | |
| :750 | WITH 空 ctx 时设 ret=-4002 并跳过 body | context.h:64-66、:806-820 | WRONG | 见 F-03 |
| :771 | sz>0 的判断在 ensure_cur_page 之前 | page_arena.h:618-626 | CONFIRMED | |
| :772 | `_alloc` 先 ensure_cur_page | :559-565 | CONFIRMED | sz<-32 时无符号回绕走 alloc_big，不建页（无关紧要） |
| :787 | 页头 32 字节，page_limit=page_size-32 | :198-206、:415 | CONFIRMED | |
| :808 | realloc 原地扩展用 `<`、不更新 used_ | :671-691 | CONFIRMED | |
| :813 | reuse 不更新 tracker | :1036 | CONFIRMED | |
| :179 | ObArenaAllocator 缺省页 7936 | :978-981 | CONFIRMED | `PageArena<>` 缺省是 7904（:256、:501），§2.12 缺这一行 |
| :249、:819 | 增长"与 ObArray 相同" | ob_array.h:365-393、:287、:594 | WRONG | 见 F-01 |
| :882、:752 | 563 处 auto_free 数组 | ob_array.h:484-497；grep 结果 559 处 | PARTIAL | 绑定的是 arena，§2.2 与 §2.12 互相矛盾 |
| :298、:832 | ob_malloc 的 nbyte≤0 → NULL+WARN | ob_malloc.h:118-125 | CONFIRMED | |
| :307 | TrackedAllocator 记请求大小 | ob_malloc.h:256-271 | CONFIRMED | 另有 32 字节头 |
| :393-397 | MemoryContextMalloc 按 usable_size 计 | ob_malloc.h:418-508；ob_malloc.cpp:216-233 | CONFIRMED | |
| :934 | je_malloc_conf 逐字相同 | ob_malloc.cpp:39-42 | CONFIRMED | |
| :943-944 | MALLOC_BACKEND 的报错文本、exit 127；macOS 的 MPRINT | malloc_hook.cpp:121-139；main.cpp:647-654 | CONFIRMED | C++ 在 constructor(101) 里检查，早于 main |
| :888-890 | ObBlockAllocMgr 的计账、WARN 文本、严格大于 | ob_block_alloc_mgr.h:38-59 | CONFIRMED | |
| :830 | 超过 128 KB 直接上堆 | ob_smart_var.h:35、:192 | CONFIRMED | |
| :977 | 数组形式 C++ 不清零 | ob_smart_var.h:175、:178 | WRONG | 探针显示全零 |
| :882 | smart_alloc 走 CURRENT_CONTEXT 的 malloc | ob_smart_var.cpp:30-36 | CONFIRMED | |
| :909-914 | 向量检查；jemalloc 下 hold=used | ob_vector_allocator.cpp:29-45、:131-156 | CONFIRMED | |
| :915 | 0 表示 50% 有效内存；裁判 18 个用例 | ob_server_config.cpp:141-148；parameters.yaml:152-158 | CONFIRMED | 18 个 .test |
| :641 | get_memory_by_percentage | alloc_func.h:37-53 | CONFIRMED | |
| :651-653 | jemalloc 下 ctx hold 恒 0；80% 判断 | ob_resource_mgr.cpp:30-40；ob_ra_datum_store.cpp:1238-1252 | CONFIRMED | update_hold 只有 obmalloc 调用 |
| :898 | max_wa_memory_size 公式（下限 1） | ob_sql_memory_manager.cpp:792、:808-818 | CONFIRMED | |
| :899 | 工作区 tracker 只收 WORK_AREA 的页 | ob_sql_memory_manager.cpp:37-47、:431-432；page_arena.h:116-181；ob_fifo_allocator.cpp:75-76 | CONFIRMED | |
| :785 | query range 退化 | ob_expr_range_converter.cpp:111；ob_range_graph_generator.cpp:66-71 | CONFIRMED | |
| :743 | 属性只在 obmalloc 的 hook 里读 | ob_rb_memory_mgr.cpp:39-50、:77-89；ob_server.cpp:2341 | WRONG | 见 F-02 |
| :1385 | join_many_table.test:13 调低工作区强制落盘 | 该文件 :13、:41、:86 | WRONG | 见 F-04 |
| :984 | `__all_virtual_memory_info` 在 C++ 下只有 1 行 | ob_ctx_allocator.cpp:68-129 | CONFIRMED | 按 hold 降序；要求 mem_dump 已初始化 |
| :961 | zlib 的 u32 乘法回绕 | ob_compress_util.h:94-102 | CONFIRMED | |
| :852 | 页大小在 obmp_query.cpp:472 | 实际在 :473 | PARTIAL | 行号差 1 |
| :774 | ob_write_string 空源不分配 | ob_string.h:811-812 | CONFIRMED | 但 dst 被置为 NULL，见 F-06 |

## 无金标准的"一致"说法

- S7/T3 ArenaVec 的增长：T3 把与 C++ 不同的序列写成了测试（F-01）。
- S11/T7 MemCtx 的 hold、used、tree_mem_hold：T7 只有 Rust 自洽的期望值，测不出 F-07。应该让 5b 的 C++ 程序建一棵上下文树，打印这三个数。
- S14 工作区预算"按页计原始页大小，与 C++ 相同"：没有测试。应该让 C++ 程序在 WORK_AREA 上下文里分配，读回 workarea tracker。
- S12 只测了检查节奏，"首条语句受限、重试不受限"没有测试（F-08）。
- S18 macOS 的 MPRINT 文本、S19 的 libc 覆盖：T12 只测了 Linux 的 3 个点。
- S3 有 T2 金标准，但它只覆盖直接调用的操作序列，测不出容器带来的差异。

## A.4 之外的偏差

1. **拟议的 `mem-accounting`**（Q3/S24，tsv:13）：
   - 需要这一项。用户可见：落盘点会改变并列行次序和哈希输出序，与 D5 冲突；还有 query range 的退化点、向量限额、各个池的流控。裁判不可见（F-04）。
   - 建议先按 F-01 把容器计账做对，A.4 只保留结构体大小、池按对象计费、VIB/CRoaring 三项，并在 D5 旁注明"接近内存边界时不保证并列行次序"。
2. **WITH_CONTEXT 空 ctx 返回 -4002，以及 body 无条件执行**（F-03）：这是规则写错，要改正，不应列入 A.4。
3. **SMART_VAR 的 errsim 按 cfg 编译**（F-05）：与 04 的修订 #2 冲突，没有登记。
4. **删掉 ObMallocHookAttrGuard 后向量计账变化**（F-02）：应并入 mem-accounting。
5. **`ObVsagSearchAlloc` 的 Deallocate 真正释放**（S20 :963）：只影响内存，用"结果不变"一句带过；按"与 C++ 不同只能是 A.4 或 FIXED"的规则，应当点名。
6. **不起 MemoryDump 线程**（S23）：线程数少一个，勉强算在 mem-view 名下，tsv:16 已排。
7. **Linux 上 init_malloc_hook 挪到 main 的第 1 步**：C++ 在 constructor(101) 里执行，早于任何静态初始化；差异不可见，记一句即可。
