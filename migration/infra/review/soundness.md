# 对抗审查：1d 基础设施 API 的健全性（soundness）

> - **对象**：`migration/infra/api/01-mem.md` … `09-parser-sys-generators.md`（2026-10-11 版）、`migration/infra/README.md`、`migration/infra/amendments.tsv`（77 行），对照 `migration/RULEBOOK.md` v0.2 的 §3 和 0.5–0.9、0.12、0.14。只查一类问题：安全 API 能不能被安全代码用出未定义行为，或者按文档的签名写不出健全的实现。行为是否与 C++ 一致、性能、命名、流程不在本文范围。
> - **基线**：`7d907abfa`。只读，没有构建项目。引用 API 文档写作 `01-mem.md:行号`，同一份文档里第二次引用只写 `:行号`；C++ 写源码路径加行号。
> - **探针**：`/Users/meizhi/.claude/jobs/3985849d/tmp/review-sound/main/`，按规则不入库。`probe_holes.rs` 用 std 写的替身照抄文档里的签名，U-01、U-02、U-03、U-04、U-06、U-07 的误用在钉住的 rustc 1.98.1 下全部编译通过；`probe_fixes.rs` 换成建议的签名后，同样的误用分别报 E0499（U-03）、E0597（U-07）、E0597（U-06）、E0502（U-01），不误用的写法照常编译。

## 1. 结论

1. 设计层审查（`migration/design/review/soundness.md`）指出的问题，大多在 v0.2 和这九份文档里落实成了健全的形状：
   - 帧和 datum 不存地址，按值编解码，解析全程带边界检查，07 只剩 3 处 unsafe；
   - `ParseNode<'p>` 不变且 `!Send`；`MemtablePin` 把回调保活收进了核心；KV cache 只交出绑在句柄借用上的切片；
   - 线程局部一律闭包式装入；`ObDList` 只接 `Box`/`Arc` 并用 owner 标签校验。
2. 剩下的问题集中在两种模式：
   - (a) 借用的生命周期没有绑到真正决定内存存活的所有者上：
     - `Latch<Arena>` 的分配绑在 `&Latch` 上，却能经 `lock()` 拿到 `&mut Arena` 去 reset；
     - `LocalParseNode::as_node`、`z_stream::set_input` 每次借用都铸出新的生命周期，存进去的指针活得比借用长；
     - `NodeBatch` 复用槽位，`Copy` 的 `NodeRef`/`MvccAddr` 不随之失效。
   - (b) 白名单里的不安全代码依赖翻译代码（storage、observer 都是 `forbid(unsafe_code)`）"自觉"地正确使用安全接口：
     - `MemtableSelf` 必须留在 `T` 里；
     - 回调链表的 guard、游标必须属于同一张表，`pub state_` 不被改写；
     - `BtreeKey::domain()` 每次返回同一个域；
     - `walk_frame_pointers` 的地址范围由调用方给对；
     - `dup2`、`setlocale`、`xmlCleanupParser` 只在特定时机调用。
3. 共 16 条：高 1、中 9、低 6；另列 10 条缺失的规则。冻结前最需要改的是：
   - U-01：`Latch<Arena>` 与 RULEBOOK 0.5 的 `destroy(&self)` 规则叠加后，会成为常见写法；
   - U-02、U-03、U-04：storage_core 的三个对外安全接口；
   - U-06、U-07、U-08：parser 和 -sys 的借用生命周期。

## 2. 发现

### U-01【高】`Latch<Arena>` 的分配活到 `&Latch` 结束，却能经 `lock()`/`with_locked` 拿到 `&mut Arena` 去 reset

- **位置**：
  - `01-mem.md:266-278`（`impl crate::sync::Latch<Arena>`）、`:823-826`（S8）、`:1020`（§4 U5）、`:740,745`（规定共享宿主用它）；
  - `02-sync.md:204,213,235-236`。
- **API**：

  ```rust
  impl Latch<Arena> {                       // 01 §2.2，同一 crate 里的固有方法
      pub fn try_alloc<T>(&self, val: T) -> ObResult<&mut T>;
      pub fn try_str(&self, src: &[u8]) -> ObResult<ObStr<'_>>;
      pub fn alloc(&self, len: i64) -> ObResult<&mut [u8]>;
  }
  impl<T> Latch<T> {                        // 02 §2.2，通用方法
      pub fn lock(&self) -> ObLatchMutexGuard<'_, T>;              // guard 实现 DerefMut<Target = T>
      pub fn with_locked<R>(&self, f: impl FnOnce(&mut T) -> R) -> R;
  }
  ```

- **失败场景**（安全代码，探针编译通过）：

  ```rust
  let x: &mut u64 = la.try_alloc(5u64)?;   // 生命周期是 &la 的借用
  la.lock().reset();                        // 只要 &la：Arena::reset 释放全部块
  *x = 7;                                   // 写已释放的内存
  ```

  如果持有 `try_alloc` 结果的是另一个线程，`la.with_locked(|a| a.reuse())` 同样导致释放后使用，还多一层数据竞争。
- **为什么不健全**：
  - U5 的论证是"`reset`/`reuse` 要 `&mut`，即 `get_mut`，`'a` 期间被借用检查器挡住"。
  - 但 `Latch<T>` 是通用类型，`lock()`、`with_locked` 只要 `&self` 就交出 `&mut Arena`，`Arena::reset(&mut self)` 需要的 `&mut` 由 guard 提供。
  - 借用检查器看不出这个 `&mut Arena` 与 `try_alloc` 返回的 `&'a mut T` 有任何关系。
- **为什么是常见写法**：
  - 01 把"长生命周期、跨线程共享"的 MemoryContext 和 `ObSafeArenaAllocator` 一律译成 `Latch<Arena>`（`01-mem.md:740,745`）。
  - 这些宿主按 RULEBOOK 0.5 和 §2.13 的接收者规则只有 `&self`，而 C++ 恰好在它们的 `destroy()` 和循环里清 arena：
    - `src/storage/compaction/ob_compaction_memory_context.cpp:94-98` 的 `destroy()` 里有 `safe_arena_.clear()`，而从它分配的对象在 `src/storage/compaction/ob_basic_tablet_merge_ctx.cpp:386-388` 交了出去；
    - `src/storage/blocksstable/ob_sstable.cpp:1131-1141` 在循环里调 `safe_allocator.reuse()`。
  - 手里只有 `&self` 的译者会自然写出 `self.safe_arena_.lock().reset()`，编译通过。
- **建议改法**：
  - 新增专用类型 `ObSafeArena`（名字取 C++ 的 `ObSafeArena`）。它内部持有 `Latch<Arena>`，但不导出：
    - 只提供 `try_alloc`/`try_str`/`alloc`/`used`/`total`（`&self`）和 `reset`/`reuse`/`reset_remain_one_page`（`&mut self`）；
    - 不提供 `lock()`、`with_locked`、`Deref`。
  - 探针 bad_4 显示，同样的误用在这个形状下报 E0502。
  - 01 §2.12 补一句：共享宿主的 `destroy(&self)` 不得清 arena（RULEBOOK 0.5 本来就要求 destroy 只复位状态、不释放内存），内存随宿主 Drop 释放。
  - U5 的论证改为"`ObSafeArena` 不暴露内部的 `&mut Arena`"。

### U-02【中】`MemtableSelf` 是可移动、`'static`、`Send` 的裸指针句柄，"必须留在 T 里"只是约定

- **位置**：`08-storage-core-kvcache.md:144-175`、`:975`（§4 U7）、`:984`（Send/Sync 清单）、`:1245-1246`（例 10）。
- **API**：
  - `MemtableRef::new_cyclic(f: impl FnOnce(MemtableSelf<T>) -> ObResult<T>)`、`MemtableRef::get_mut(&mut self) -> Option<&mut T>`；
  - `pub struct MemtableSelf<T> { inner: NonNull<MemtableInner<T>> }`，带 `unsafe impl Send/Sync`；
  - `MemtableSelf::register_callback(&self) -> ObResult<MemtablePin<T>>`。
- **失败场景**（探针编译通过）：

  ```rust
  let mut m = MemtableRef::new_cyclic(|s| Ok(ObMemtable { memtable_self_: Some(s), /* … */ }))?;
  let escaped = m.get_mut().unwrap().memtable_self_.take();   // 把句柄移出 T
  let _ = m.release();                                         // pin 和为 0：析构 T，释放容器
  let _ = escaped.unwrap().register_callback();                // 解引用已释放的 MemtableInner
  ```

  同样的效果还有两种写法：用 `mem::swap` 交换两个 memtable 的 `MemtableSelf`；在 `new_cyclic` 的闭包里把句柄塞进外部变量，再让闭包返回 `Err`。
- **为什么不健全**：
  - U7 的最后一句"`MemtableSelf` 存在 T 里，T 析构时它随之析构，不会被解引用"依赖调用方。
  - `new_cyclic` 只在注释里写"必须把它存进 T"，而 `get_mut` 又给出了 `&mut T`。
  - storage 是 `forbid(unsafe_code)`，上面这段安全代码就能制造释放后使用，正是设计层 S-04 要消除的那一类。
- **建议改法**：
  - `MemtableInner<T>` 放进 `Arc`，`T` 放在内部的 `Option` 里，由 `release` 析构；
  - `MemtableSelf` 持有 `Weak<MemtableInner<T>>`（或 `Arc` 加状态位），`register_callback` 先 `upgrade` 再查 `state_`，`DESTROYED` 时返回 `OB_STATE_NOT_MATCH`（S17 已有这个返回码）；
  - 这样句柄被移到哪里都不会悬垂，`MemtableSelf` 也不再需要 `unsafe impl Send/Sync`。

### U-03【中】`NodeBatch` 复用"从未链入"的槽位，`NodeMut`、`NodeRef`、`MvccAddr` 却都不随槽位失效

- **位置**：
  - `08-storage-core-kvcache.md:356-372`（`NodeMut`、`NodeBatch::init_tx_node`）、`:868`（S8）、`:970,972`（§4 U2、U4）、`:1174-1182`（例 7）；
  - C++：`src/storage/memtable/ob_memtable.cpp:2532`（节点构造在 `new_nodes + pos`）、`:2539-2545`（`need_find_all_duplicate_key()` 时把主键冲突改成成功、继续下一行）、`:2577`（只在成功时 `pos += aligned_data_size`）；`src/storage/memtable/mvcc/ob_mvcc_define.h:191-193`（`ObMvccWriteResult::tx_node_` 保存节点指针）。
- **API**：
  - `NodeBatch<'m>::init_tx_node(&mut self, data) -> ObResult<NodeMut<'m>>`，其中 `'m` 是 `AllocHandle` 的借用，与 `&mut self` 无关；
  - `NodeMut::into_ref(self) -> NodeRef<'m>`，`NodeRef` 是 `Copy`；
  - `NodeRef::get_mtd(self) -> ObMemtableDataHeader<'m>`，里面是 `&'m [u8]`；
  - `NodeRef::addr(self) -> MvccAddr<…>`，`'static`。
- **失败场景**：
  1. 两个同时存活的 `&mut`（探针编译通过）：

     ```rust
     let mut n1 = batch.init_tx_node(&d1)?;   // 槽位 p，尚未链入
     let mut n2 = batch.init_tx_node(&d2)?;   // S8：从未链入 → 复用 p
     n1.write_epoch_ = 1; n2.write_epoch_ = 2;
     ```

  2. 封口但没有链入的节点：
     - `mvcc_write` 在链入之前失败（锁冲突、TSC），`mvcc_results[i].tx_node_` 仍是 `Some(NodeRef<'m>)`；
     - 插入去重路径照 C++ 继续下一行，`init_tx_node` 在同一地址经 `NodeMut` 重写节点头和载荷；
     - 此前取出的 `get_mtd()` 切片如果还活着，就是在共享引用之下写内存；即使时间上不重叠，旧的 `NodeRef`/`MvccAddr` 也静默地指向了另一个节点。
- **为什么不健全**：
  - U4 说节点"地址尚未出现在任何链接、`MvccAddr`、`NodeRef` 里"，U2 说"载荷在封口后不再写（只有 `NodeMut` 能写）"。
  - S8 的复用条件只看"是否链入过"，不看"是否发出过 `NodeMut`/`NodeRef`"，所以这两条论证都不成立。
- **建议改法**：
  - 签名改成 `init_tx_node(&mut self, …) -> ObResult<NodeMut<'_>>`，让 `NodeMut` 借住批。探针 bad_1 显示两个同时存活的 `NodeMut` 会报 E0499。
  - `into_ref` 时把槽位记为已消费，之后一律不复用；只有"`NodeMut` 没有封口就被 drop"的槽位才复用。
  - C++ 复用槽位只是省内存，不进入可观察行为：memstore 用量是内部统计，08 的 S27 已说明可以与 C++ 不同。
  - U2、U4 改写成"封口即永久消费"。

### U-04【中】回调链表核心：`pub state_` 可伪造，guard、游标、位置、节点都不校验属于哪张表

- **位置**：
  - `08-storage-core-kvcache.md:598-601`（`LockState`、`TxCallbackLockGuard { pub state_ }`）、`:606`（游标是原始指针）、`:618`（`new_node` 返回 `Arc`）、`:624-642`（`head`/`cursor_at`/`set_log_cursor`/`append_after`/`unlink`）、`:917`（S41）、`:1209`（例 8）；
  - C++：`src/storage/memtable/mvcc/ob_tx_callback_list.h:155-161`（`LockGuard::state_` 是公有字段）、`ob_tx_callback_list.cpp:1044-1052`（`~LockGuard` 按 `state_` 解锁）。
- **失败场景**：
  1. 伪造锁状态（探针编译通过）：

     ```rust
     let mut g = core.lock(LOCK_MODE::LOCK_ITERATE, false);
     g.state_.APPEND_LOCKED_ = true;   // 只拿了 iterate 锁
     ```

     之后 `append_after(&g, …)` 通过"需要 APPEND_LOCKED_"的检查，在没有 append 锁的情况下改链。guard 析构时照 C++ 按 `state_` 去解 append 锁，解掉的是另一个线程持有的锁。于是两个线程同时追加，链被改坏。
  2. 跨表使用，签名里只有生命周期、没有表的身份：

     ```rust
     list_a.append_after(&guard_of_b, &cursor_of_b, node);
     list_a.unlink(&g_a, &cursor_into_b);
     list_a.set_log_cursor(&cursor_of_b, epoch);
     ```

     A 的 guard 摘下 B 的节点后，节点的 `Arc` 挂在 A 的 guard 上，随 A 的 guard 释放而归还；B 上正在遍历的线程手里的 `TxCallbackCursor` 是原始指针，于是悬垂。
  3. 同一个节点追加两次：`new_node` 把 `Arc` 交给调用方，调用方克隆一份，再 `append_after` 到另一张表（或同一张表的另一个位置），第二次写 `prev_`/`next_` 时把两条链缠在一起。
  4. `set_log_cursor(&self, …)` 不要求 guard。例 8 是在 iterate guard 里调用的，但签名并不要求，所以 S42 的"单写者由上述锁保证"不成立。
- **为什么不健全**：S41 的结论"所有游标都借用某个 guard，所以不会悬垂"隐含了两个前提：guard、游标、节点属于同一张表；guard 真的持着它声称的锁。这两点都没有类型或运行期检查保证，而 storage 里的这些调用都是安全代码。
- **建议改法**：
  - `state_` 改为私有，只给 `is_locked()`、`is_append_locked()`；核心的检查读 guard 内部记录的实际加锁结果。
  - 每张表一个进程内唯一的 `list_id`，guard、游标、`TxCallbackPos`、节点都带它。`append_after`/`unlink`/`cursor_at`/`set_log_cursor` 先比对，不符就记 ERROR 并返回 `OB_ERR_UNEXPECTED`，做法与 S11 的 arena 身份检查相同。
  - `TxCallbackNode` 加 `linked: AtomicBool`，`append_*` 用 CAS 把它从 0 置 1，已经链入的节点一律拒绝。
  - `set_log_cursor` 增加一个 `&TxCallbackLockGuard` 或 `&TxCallbackLogGuard` 参数。

### U-05【中】keybtree 的回收和键值擦除，依赖 storage 实现的安全 trait 行为正确

- **位置**：
  - `08-storage-core-kvcache.md:491-513`（`BtreeKey`、`BtreeVal`、`ObMvccRowVal`）、`:504`（`fn domain() -> &'static ObKeyBtreeDomain`）、`:906`（S34）、`:976`（§4 U8）、`:984`（Send/Sync 清单）；
  - C++：`src/storage/memtable/mvcc/ob_keybtree.h:50-52`（键和值都必须 8 字节、值的低 3 位为 0）、`ob_keybtree.cpp:1563`（对应断言）、`:1742-1761`（每个实例化各有一套函数内静态的 QClock、RetireStation、ObQSync）。
- **失败场景**：
  1. `domain()` 不稳定：storage 的实现在两次调用之间返回不同的域，比如每次 `Box::leak` 一个新的，或者按条件返回两个 static 之一。读者在域 D1 的 QClock 里进临界区，写者把替换下来的节点交给 D2 的 RetireStation；D2 只等 D2 的 QClock 静默，于是节点在读者仍在 D1 临界区里时被回收复用，读者读到被改写的节点。
  2. `compare` 不一致（违反全序，或者依赖可变状态）：树的不安全实现如果按"已排序"的假设省掉了下标、分裂、路径栈上的越界检查，就会越界。文档没有把"比较器任意返回时实现仍须内存安全"写成实现义务；标准库对 `Ord` 有同样的要求。
  3. 值的表示：
     - C++ 只允许 8 字节、低 3 位为 0 的指针值；Rust 的 `BtreeVal::Val<'a>` 只要求 `Copy`；
     - U8 又说键值以"定长字节"存放、读出时复原；
     - 如果实现把 `Val` 当 `u64` 读写，或在低位放标记，那么 `(u8, u32)` 这类带填充的类型、`bool` 这类有无效位型的类型，复原时就会读到未初始化的字节或产生无效值。
  4. Send：
     - S34 照 C++ 保留了"`find_path` 失败时迭代器不 reset，QClock 槽位要到 reset 或析构才释放"；
     - 而 §4 把 `BtreeIterator` 列为 `unsafe impl Send`，理由是"不含临界区"。这与 RULEBOOK §3"`unsafe impl Send/Sync` 不得用于含临界区令牌的类型"冲突；
     - `ObKeyBtree`、`BtreeIterator` 的 `unsafe impl` 也没有写出对 `K::Key<'t>`、`V::Val<'t>` 的 `Send`/`Sync` 约束。
- **为什么不健全**：这几个 trait 由 storage（`forbid(unsafe_code)`）实现，是普通的安全 trait。不安全代码的正确性不能依赖安全 trait 实现的行为，这是 Rust 对 unsafe 的基本约定：`BinaryHeap`、`sort` 遇到不一致的 `Ord` 也必须保持内存安全。
- **建议改法**：
  - 回收域由核心拥有：`ObKeyBtree::new` 时调用一次 `K::domain()`，把指针存进树；之后树、迭代器、批量销毁都用这个指针（`batch_destroy` 改成以域为参数），不再每次调用 trait。或者改成核心内按 `TypeId` 建域的静态表。
  - U8 补一条实现义务："比较结果任意（包括返回 `Err`）时，树的实现仍然内存安全；越界只能返回错误或 abort"，并配一个随机比较器的 Miri 测试。
  - `BtreeVal` 改成 `unsafe trait`（实现者保证 8 字节、无填充、低 3 位为 0），或者封闭成两种由核心实现的值族：`ObMvccRowVal` 和 DDL 的值族。
  - S34 改为 `find_path` 失败也立即离开临界区（只影响回收时机）；`BtreeIterator` 的 `unsafe impl Send` 加 `where K::Key<'t>: Send, V::Val<'t>: Send`，`ObKeyBtree` 同理加 `Send + Sync` 约束。

### U-06【中】`LocalParseNode::as_node(&mut self)` 每次铸出新的生命周期，存进去的指针活得比借用长

- **位置**：`09-parser-sys-generators.md:512-520`、`:1729`（§3.1.7）、`:1860`（§4 P1 的第 (2)、(5) 点）。
- **API**：
  - `pub struct LocalParseNode { raw: sys::ParseNode }`、`pub fn as_node(&mut self) -> ParseNode<'_>`；
  - `ParseNode<'p>::set_str_value(self, s: &'p [u8])`，以及 `set_raw_text`、`set_child`、`set_children`；
  - `str_bytes(self) -> &'p [u8]`。
- **失败场景**（探针编译通过）：

  ```rust
  let mut local = LocalParseNode::new();
  { let buf = vec![b'x'; 8]; local.as_node().set_str_value(&buf); }   // 'l 只覆盖这一条语句
  let s = local.as_node().str_bytes();                                  // 新的 'l2：读已释放的 buf
  ```

- **为什么不健全**：
  - `'p` 不变，只保证同一个 `'p` 的句柄之间不能混用。
  - `LocalParseNode` 是拥有型的值，可以反复 `as_node()`；每次借用都是新的生命周期，而 C 结构里保存的指针跨越了借用。
  - P1 第 (5) 点"写进去的指针都活过 `'p`"只对那一次借用的 `'p` 成立，对后来的借用不成立。
  - C++ 的 35 处栈上 `ParseNode` 正是"先填字段、过一会儿再读"的用法，如 `ob_raw_expr_resolver_impl.cpp` 的 `ret_node`。
  - arena 节点没有这个问题：它的句柄只能从 `&'p ParseArena` 或同一棵树派生，`'p` 结束后拿不到新的句柄。
- **建议改法**：句柄只能和存储的（重新）初始化一起产生，并借住存储：

  ```rust
  pub fn local_parse_node<'p>(slot: &'p mut MaybeUninit<sys::ParseNode>) -> ParseNode<'p>;   // 全零初始化后返回
  ```

  调用方写 `let mut slot = MaybeUninit::uninit(); let n = local_parse_node(&mut slot);`。`n` 是 `Copy` 的，整段代码用同一个 `'p`；探针 bad_3 显示，把短命的数据存进去会报 E0597。另一种做法是保留 `LocalParseNode`，但让它带上数据的生命周期（`LocalParseNode<'d>`），`as_node` 返回的句柄只接受 `&'d` 的数据。

### U-07【中】`z_stream::set_input<'a>(&'a mut self, input: &'a [u8])` 没有把输入的生命周期绑在流上

- **位置**：`09-parser-sys-generators.md:914-934`（§2.3.2）。
- **失败场景**（探针编译通过）：

  ```rust
  { let v = make_input(); strm.set_input(&v); }    // 'a 只到这条语句为止
  deflate(&mut strm, &mut out, Z_NO_FLUSH);         // zlib 读 next_in：已释放的 v
  ```

- **为什么不健全**：`'a` 既不出现在返回值里，也不出现在 `z_stream` 的类型里，编译器把它推成这一次调用的区间，`&'a mut self` 和 `&'a [u8]` 在调用结束后同时失效。注释里的"在下一次 set_input 或 drop 之前有效"是调用方的义务，而 zlib 在 `deflate`/`inflate` 里会读 `next_in`。
- **建议改法**（二选一）：
  - (a) 输入随每次调用传入：`deflate(strm, input: &[u8], output: &mut [u8], flush) -> (i32, consumed, produced)`，返回前把 `next_in` 置空；
  - (b) 流带上输入的生命周期：`z_stream<'a>` 加 `set_input(&mut self, input: &'a [u8])`。探针 bad_2 显示 (b) 下同样的误用报 E0597。
  - (a) 更贴近 C++ 每次压缩前设 `next_in` 的用法，推荐 (a)。

### U-08【中】`AioRequest<B: AsMut<[u8]> + Send + 'static>` 接受内联数组，内核会按移动前的地址写

- **位置**：`09-parser-sys-generators.md:1150-1162`（§2.3.7）、`:1809`（§3.9.7）、`:1868`（§4 P9）。
- **API**：`io_prep_pread<B: AsMut<[u8]> + Send + 'static>(fd, buf: B, offset, id) -> AioRequest<B>`、`io_submit(ctx, reqs: Vec<AioRequest<B>>)`。
- **失败场景**：
  - `let req = io_prep_pread(fd, [0u8; 4096], 0, 1);`，`[u8; 4096]` 满足约束。
  - `iocb` 里的缓冲地址在 prep 时（或 submit 时）取自请求内部的数组。请求随后被移进 `Vec`，再移进上下文的存储，存储扩容时还会再移动一次。
  - 内核按旧地址 DMA 写入，写的是已经失效、甚至已被复用的内存。
- **为什么不健全**：§3.9.7 的"请求的 iocb 和缓冲在提交到完成之间归上下文所有"只保证缓冲不被释放，不保证缓冲地址不变。`AsMut<[u8]>` 不承诺地址稳定：内联数组随宿主移动，用户自己实现的 `AsMut` 甚至可以每次返回不同的切片。
- **建议改法**：
  - 缓冲类型改用封闭的或 `unsafe` 的稳定地址 trait，如 `unsafe trait StableIoBuf`，只给 `Box<[u8]>`、`Vec<u8>`（提交后不再改容量）和 01 的 `AlignedBuf` 实现；
  - 地址在 submit 时取，且只取一次；
  - `iocb` 本身也放进 `Box`：内核在 `io_getevents` 里回写 `iocb` 指针，所以 `iocb` 同样必须地址固定。

### U-09【中】扩展栈段和 main 的新栈都没有保护页：安全代码的栈溢出会变成静默的堆损坏

- **位置**：
  - `03-thread-signal.md:1129`（§3.11 第 6 条："段同样不加保护页"）、`:857`（`smart_call_impl`）、`:869-877`（`ObStackMem::mmap`、`call_with_new_stack`）、`:1186`（§4 的 stack 行）；
  - C++：`src/oblib/lib/thread/protected_stack_allocator.cpp:69-72`（`smart_call_alloc` 传 `guard_page = false`；只有线程栈经 `alloc` 加保护页，见 `:122-131` 的 `VirtualProtect`/`mprotect`）、`src/observer/main.cpp:877-886`（1 MiB 的 `mmap`，没有保护页）。
- **失败场景**：
  - 经 `smart_call!` 进入 2,062,336 字节的扩展段后，两个检查点之间的任何安全代码都可能用尽这一段，越过段的低端，写进全局分配器排在它前面的块。例如：
    - 没有 `smart_call!` 的递归，包括编译器生成的递归 Drop（深层嵌套的拥有型树）；
    - 大的局部数组（`char buf[N]` 译成 `[u8; N]`）；
    - 第三方 Rust 库内部的递归。
  - `main` 在 1 MiB 的 `mmap` 区上运行 `inner_main`，溢出同样会写坏相邻的映射。
  - `smart_call_impl(reserved_size, f)` 和 `call_with_new_stack(&mut ObStackMem::mmap(size)?, f)` 还接受任意小的 `reserved_size` 和 `size`。
- **为什么不健全**：
  - Rust 对栈溢出的安全保证依赖保护页加栈探测（`probe-stack = inline-asm`）：溢出时必然先碰到不可访问的页，进程终止。没有保护页，安全代码就能写坏别的对象。
  - C++ 有同样的风险，但 C++ 没有"安全代码不会破坏内存"的承诺。
  - Rust 的帧通常比 C++ 大，`NATIVE_STACK_FACTOR` 还没有实测（A.3），所以风险只会更大。
- **建议改法**：
  - 段按页对齐分配后，对最低一页 `mprotect(PROT_NONE)` 一次。段本来就按线程缓存、按栈式空闲表复用（§3.11 第 6 条），所以 `mprotect` 只发生在新建段和线程退出归还时，不违反 RULEBOOK 0.8"禁止每次扩栈都 mmap/munmap"的本意；RULEBOOK 那一句要相应放宽，见第 3 节第 7 条。
  - `ObStackMem::mmap` 多映射一页作保护页。
  - `call_with_new_stack` 和 `smart_call_impl` 断言大小不小于一个固定下限（如 64 KiB）。
  - 保护页上的 SIGSEGV 不会被 Rust 运行时识别为栈溢出，但结果是进程终止而不是内存损坏，满足安全要求。

### U-10【中】接收原始地址或原始句柄的安全函数

- **位置**：
  - `03-thread-signal.md:977`（`arch::walk_frame_pointers`）、`:930-934`（`PthreadHandle`、`pthread_kill`）、`:1188-1189`（§4 的 signal、arch 两行）；
  - `04-error-log-fmt.md:1177`（`log::sys::dup2`）、`:1770`（§4）。
- **API 与失败场景**：
  - `walk_frame_pointers`：

    ```rust
    pub fn walk_frame_pointers(buffer: &mut [usize], rbp: usize, stack_lo: usize, stack_hi: usize) -> i32;
    ```

    安全代码可以传任意 `rbp`、`stack_lo`、`stack_hi`，如 `walk_frame_pointers(&mut b, 0x1000, 0, usize::MAX)`，函数就去读这些地址。§4 的论证"只在 `[stack_lo, stack_hi − 16)` 内读"把正确性交给了调用方给的范围。
  - `pthread_kill`：
    - `pub struct PthreadHandle(usize)` 派生了 `Default`、`Copy`，而 `pub fn pthread_kill(thread: PthreadHandle, sig: i32) -> i32` 是安全函数；
    - `PthreadHandle::default()` 是 0，glibc 的 `pthread_kill` 会解引用它；
    - 线程被 join 之后再用它的 `pthread_t`，POSIX 规定为未定义行为，glibc 的线程描述符此时可能已被复用或解除映射；
    - 所以 §4 的"对已退出线程的结果由系统定义"不成立。
  - `dup2`：`pub fn dup2(src_fd: i32, dst_fd: i32) -> i32` 让安全代码可以把任意 fd 换到任意 fd 号上，破坏别处 `File`/`OwnedFd` 对 fd 的独占，违反 Rust 的 I/O 安全约定。§4 的论证"`dst` 只能是 1、2 或同一个 `ObPLogFileStruct` 持有的 fd"是调用方的义务。
- **建议改法**：
  - `walk_frame_pointers` 不再收范围，内部用 `get_stackattr` 取当前段；或者改成 `unsafe fn`，只给 04 的 `light_backtrace` 调用。
  - `PthreadHandle` 去掉 `Default`；`pthread_kill` 改收一个借用活线程的句柄（如 `&JoinHandle`，或 `Threads` 里的线程对象，借用期间线程不会被 join），或者只在 `oblib::signal` 内部保留这个操作。
  - `dup2` 改为 `ObPLogFileStruct` 的私有方法，参数用它自己持有的 `File`，不对外导出原始 fd 版本。

### U-11【低】安全性依赖"调用时机"的安全函数

- **位置**：
  - `04-error-log-fmt.md:1775`（`setlocale_like_main`，"只在 main 起任何线程之前调用一次"）；
  - `06-string-num-sort-serial.md:979`（U1：`c_strto*` 依赖 locale 此后不再变化）；
  - `01-mem.md:546,1023`（`configure_darwin_malloc_zone`，"只在 main 的开头、单线程时调用"）；
  - `09-parser-sys-generators.md:1013`（`xmlCleanupParser`）、`:1112`（`curl_global_cleanup`）、`:789,1811`（`check_parse_node_layout` 是全部 `ParseNode`/`ParseResult` 安全访问的前提，却只由 observer 的 `main` 调用）；
  - `03-thread-signal.md:1619`（Q13：observer 还需要 `setlocale` 等 libc 包装）。
- **失败场景**：
  - `setlocale` 与其他线程里的 `strtod`/`strftime`/`localtime_r` 并发，是 C 库层面的数据竞争；
  - `xmlCleanupParser`、`curl_global_cleanup` 在别的线程仍在用库时释放全局状态；
  - `configure_darwin_malloc_zone` 在别的线程分配时注销、重新注册默认 zone；
  - 单元测试、基准程序或其他二进制直接调用解析器，从不运行布局自检，布局不符时，安全的字段访问会读错位置。
  - 这些都是 `pub` 的安全函数，前提只写在注释和 SAFETY 里。
- **建议改法**：
  - 这几个函数改成 `unsafe fn`（它们都在白名单模块里）。
  - observer 的生成 `main` 只调用一个安全入口 `oblib::sys::process_init_before_threads()`：用 `Once` 保证只执行一次，断言 `oblib::thread` 还没有建过线程（`Threads::start` 置一个全局标志），再依次调用它们。
  - 布局自检挪到构建期：build.rs 用 bindgen 得到的常量生成一段 `_Static_assert(sizeof(ParseNode) == 112, …)` 之类的 C 源，与解析器用同一组 `-D` 编译，布局不符就构建失败；启动自检可以保留，作第二道检查。

### U-12【低】令牌型原语的裸加解锁、临界区进出是安全的 `pub` 方法

- **位置**：
  - `02-sync.md:410-413`（`Quiescence::acquire_ref`/`release_ref`）、`:483`（`BucketLatch::unlock`）、`:697-701`（`ObByteLock<()>::raw_lock`/`raw_unlock`）、`:721-728`（`ObRowLatch::lock`/`unlock`）、`:1575`（Q6）；
  - `08-storage-core-kvcache.md:298`（`pub use oblib::sync::bitlock::ObRowLatch`）、`:451-452`（`QClock::enter_critical`/`leave_critical`）；
  - `amendments.tsv` 第 8 行（修订 A7）。
- **失败场景**：
  - 任何拿得到实例的安全代码，都能对别的线程持有的锁调用 `unlock()`，对别的线程的槽位调用 `leave_critical(slot)`、`release_ref(idx)`。
  - storage_core 和 KV cache 的不安全代码依赖这些原语：`RowGuard` 的独占靠 `ObRowLatch`；keybtree 和块句柄的回收靠 QClock 的槽位，而 `leave_critical` 无条件清槽（`src/oblib/lib/allocator/ob_retire_station.h:59-63`）。
  - 目前这些实例都是核心的私有字段，所以还不是现成的漏洞。但 Q6 和修订 A7 只打算用 clippy 的 `disallowed_methods` 来限制，lint 不是健全性边界：只要哪天有一个实例经访问函数暴露出去，安全代码就能让写者在读者仍在临界区时回收节点。
- **建议改法**：
  - 裸方法改成 `pub(crate)` 或 `unsafe fn`；对 crate 外只提供 guard（`QClockGuard`、`ObRowLatchGuard` 等）；storage_core 需要跨函数持锁的地方（`try_lock_log`）用自己的 guard 类型包住。
  - RULEBOOK §3 补一句：被不安全代码依赖的锁或临界区，它们的解锁、退出操作不得是安全的公开函数。

### U-13【低】`RowGuard::alloc_index` 不检查分配器与行属于同一个 arena

- **位置**：
  - `08-storage-core-kvcache.md:430`；S11（`:871`）列出了链接原语、`set_index_node`、`AllocHandle::row/node` 的身份检查，唯独没有 `alloc_index`；
  - C++：`insert_trans_node` 用调用方传入的分配器分配 `index_`（`src/storage/memtable/mvcc/ob_mvcc_row.cpp:661-670`），调用方 `ob_mvcc_trans_ctx.cpp:1938` 传的是 `memtable_->get_allocator()`；Rust 版里这一步落在 storage 的 `ObMvccRowLockedExt::insert_trans_node`（`08-storage-core-kvcache.md:835-836`）调用核心的 `alloc_index`。
- **失败场景**：
  - `guard.alloc_index(&other_memtable.local_allocator_)`（比如把冻结的和活跃的两个 memtable 混用）把索引分配进另一个 memtable 的 arena，行的 `index_` 指了过去。
  - 那个 memtable 先释放之后，下一次 `get_index_node` 读的就是已释放的内存。
  - `'m` 只证明分配器活过这一次借用，证明不了它活得和行一样久。
- **建议改法**：
  - `alloc_index` 同样比对 `allocator.arena_id()` 和行的 `arena_id`，不一致就 `ob_assert!`；
  - 或者不再收分配器参数，由核心从行所在的 arena 分配（`RowRef` 带 arena 身份，可以找到对应的 `AllocHandle`）。

### U-14【低】跨线程句柄的 `Send`，没有约束它借入或持有的 trait 对象

- **位置**：
  - `09-parser-sys-generators.md:1113-1124`（`CURL<'b>`、`curl_easy_setopt_write(h, sink: &'b mut dyn curl_write_callback)`）、`:1872`（§4 P13：`CURL` 是 `unsafe impl Send`）；
  - `:1286-1305`（vsag：`extern "Rust"` 里的 `vsag_allocate(ctx: usize, …)` 等是安全函数；`pub trait Allocator: Sync`；`VectorIndexPtr { index, allocator: Option<Arc<dyn Allocator>> }`）。
- **失败场景**：
  - `CURL<'b>` 借着一个 `&'b mut dyn curl_write_callback`，后者的具体类型可以是 `!Send` 的，比如持有 `Rc`，或者与本线程共享 `RefCell` 状态。句柄经 `unsafe impl Send` 送到另一线程去 `perform`，回调就在另一线程上通过 `&mut` 修改这份 `!Send` 的状态。
  - vsag 的分配器上下文以 `usize` 令牌交给 C++，保存在索引里，但文档没有规定令牌指向哪里：
    - 如果指向 `VectorIndexPtr` 里 `allocator` 字段的地址，`VectorIndexPtr` 一移动（它是普通的可移动结构），令牌就悬垂；
    - `Allocator` 只要求 `Sync`、不要求 `Send`，而 `Arc<dyn Allocator>` 会随索引跨线程移动，在别的线程上被最后一次 drop；
    - `vsag_allocate` 等声明成安全函数，函数体却要把整数当指针解引用。
- **建议改法**：
  - `sink: &'b mut (dyn curl_write_callback + Send)`；
  - `trait Allocator: Send + Sync`；令牌固定为 `Box<Arc<dyn Allocator>>` 的堆地址，随索引一起拥有、晚于索引释放；
  - `extern "Rust"` 里收令牌的函数一律声明为 `unsafe fn`（`istream_buf_callback` 已经这样写了）。

### U-15【低】FFI 边界上声明为安全、却要求调用方满足前提的函数

- **位置**：
  - `09-parser-sys-generators.md:1340,1345-1348`（regex_shim：`std_regex_search_cstr(re, subject: &[u8])` 的"主体是 C 字符串"、`posix_regcomp(pattern: &[u8], …)` 的"pattern 以 NUL 结尾"、`posix_regexec(re, subject: &[u8], …)`）；
  - `:754-778`（`#[no_mangle] pub extern "C" fn parse_malloc(nbyte, malloc_pool: *mut c_void)` 等 20 个蹦床）；
  - `01-mem.md:581,1022`（`malloc_hook.rs` 里 `#[no_mangle] extern "C"` 定义的 libc 符号）。
- **失败场景**：
  - cxx 的 `unsafe extern "C++"` 块里声明的函数，在 Rust 一侧是安全函数。Rust 代码把一个不含 NUL 的切片传给 `posix_regcomp`，C++ 一侧如果直接把 `pattern.data()` 交给 `regcomp`，就会越过切片末尾读。
  - 蹦床和 malloc 钩子写成了安全的 `extern "C" fn`，同一 crate 里的安全代码可以直接调用 `parse_malloc(8, 0x1 as *mut _)` 或 `free(p)`，函数体把参数当指针解引用或释放。
- **建议改法**：
  - shim 的 C++ 一侧一律先构造 `std::string(ptr, len)`（自带 NUL，遇到内嵌 NUL 照 C++ 的行为截断），不依赖调用方；
  - 蹦床和钩子写成 `#[unsafe(no_mangle)] pub unsafe extern "C" fn`。C 的调用不受影响；Rust 2024 本来也要求写 `unsafe(no_mangle)`。

### U-16【低】`ParseResult::no_param_sql(&self) -> &'p [u8]` 借出的缓冲，会被下一次解析就地重写

- **位置**：`09-parser-sys-generators.md:587-590`。
- **失败场景**：
  - `set_no_param_sql_buf(buf)` 之后 `parse_sql(&mut r, sql1)`，取 `let s = r.no_param_sql();`，`s` 的生命周期是 `'p`，比 `&r` 活得长；
  - 再对同一个 `ParseResult` 调用 `parse_sql(&mut r, sql2)`：C 往同一个缓冲写新的参数化文本，而 `s` 仍是活的共享引用。
- **建议改法**：
  - 返回值绑在 `&self` 上：`fn no_param_sql(&self) -> &[u8]`；
  - 或者解析后由 `take_no_param_sql(&mut self) -> &'p [u8]` 把缓冲移交出来，此后 C 不再持有它。
  - C++ 每次解析都新建 `ParseResult`（`src/sql/parser/ob_parser.cpp:931,1089`），所以这个改法不影响译文。

## 3. 缺失的规则

API 文档和 RULEBOOK §3 都还没有规定、但上面的问题要求写成通用规则的约束：

1. **保存借用的类型要把被借数据的生命周期写进类型**（U-06、U-07）：`fn f<'a>(&'a mut self, x: &'a T)` 这样的签名证明不了 `x` 活过以后的使用。凡是把指针存进 `self`、或交给 C 保存的方法，`'a` 必须是类型参数，或者由闭包限定作用域（ICU 的 `with_uregex_setText` 就是正确的写法）。建议写进 RULEBOOK §3 的"每处 unsafe 的要求"。
2. **不安全代码不得依赖安全 trait 实现的行为**（U-05、U-08）：用户实现的 `domain()`、`compare()`、`hash()`、`AsMut`、`dlink()` 只能影响结果，不能影响内存安全。确需依赖的，写成 `unsafe trait`，或在构造时取一次并存下来。05 的 `ObDList` 对 `dlink()` 已经是这样处理的（先校验 `owner_` 再写）。
3. **同一容器的多个句柄要校验身份**（U-04、U-13）：凡是 API 同时收两个"必须属于同一对象"的参数（guard 与表、游标与表、分配器与行），一律带身份比对或品牌生命周期。08 的 S11（arena 身份检查）应推广成通用规则。
4. **不安全代码依赖的状态不得是 `pub` 字段，锁和临界区的裸解锁、退出不得是安全的公开函数**（U-04 的 `state_`、U-12）：lint 代替不了可见性。
5. **发出过 `Copy` 句柄的槽位不得静默复用**（U-03）：凡是会发出 `Copy` 句柄或地址的存储（`NodeRef`、`MvccAddr`、`DataRef`、`KvBlockRef`），复用槽位之前必须保证这些句柄已经失效（靠代号检查）或不可能存在（靠借用）。
6. **进程级 C 状态的安全包装**（U-10、U-11）：`setlocale`、`dup2`、`pthread_kill`、`xmlCleanupParser`、`curl_global_*`、malloc zone 一律是 `unsafe fn`，再配一个生成的 `process_init_before_threads()` 作为唯一的安全入口。observer 需要的 libc 包装由谁持有（03 的 Q13、04 的修订 #6），也要一并定下来。
7. **栈段必须有保护页**（U-09）：RULEBOOK 0.8 的"不加保护页、不进系统调用"改为"每个缓存段在创建时 `mprotect` 一次保护页"，并规定 `smart_call` 和 `call_with_new_stack` 的最小段大小。
8. **cxx 与 `extern "C"` 的边界**（U-14、U-15）：
   - cxx 的 `unsafe extern "C++"` 函数在 Rust 侧是安全的，所有前提（NUL 结尾、长度、生命周期）必须在 C++ 一侧检查；
   - `extern "Rust"` 里收令牌的回调一律是 `unsafe fn`；
   - 导出给 C 的 Rust 函数一律写成 `unsafe extern "C"`。
9. **`unsafe impl Send/Sync` 的约束**（U-05、U-14）：
   - 泛型容器和 FFI 句柄的 `unsafe impl`，必须对全部载荷（关联类型、trait 对象、借入的回调）写出 `where` 约束；
   - "有时含"临界区令牌的类型也不得 `Send`，如 S34 的 `BtreeIterator`。
10. **交给内核或 C 异步使用的缓冲，必须是封闭的稳定地址类型**（U-08）：不得只用 `AsMut<[u8]>`、`Deref<Target = [u8]>` 这类不承诺地址的约束。

## 4. 已核对、未发现健全性问题的部分

下面这些是逐份查过、按文档写法健全的设计，列出来供冻结时参考，免得改动时把它们改坏：

- **01**：
  - `Arena` 的 bump 分配，以及 `reset`/`reuse(&mut self)`；
  - `realloc` 一律新分配加拷贝（S5），旧切片经隐式重借用恢复可用后，也不会与新分配重叠；
  - `ArenaVec` 自动 `!Send`；`needs_drop` 断言只防泄漏，不承担健全性；
  - `MemCtx` 是 `Send + !Sync`；`ObMemTrackerGuard::with` 是闭包式；
  - `ForeignBlock::from_raw` 是 `unsafe fn`，载荷不形成 `&[u8]`；`ObMalloc` 的对齐和 `sdallocx` 规则（S17）。
- **02**：
  - 带数据的锁，`Send/Sync` 的条件与 std 的 `Mutex`/`RwLock` 一致；guard 都是 `!Send`；
  - `ShardedRwLatch`、`QSyncLatch`、`BigReaderLatch` 的读 guard 记住了槽号；
  - 等待队列的栈节点，"存 0 之后不再访问"（§3.3 R8、§4 U4）；
  - `HookedWriteGuard` 的 `Arg` 不带生命周期，借不住 inner；`ObPtrSpinLock` 用保持来源的原子操作；
  - 忘记 guard 只会造成永久持锁或写者饥饿，不会产生未定义行为。
- **03**：
  - `HostRef`（`&'static` 或 `Arc`）保证线程不会活过宿主；
  - `WorkerCtx`、trace id、`ObTraceIdGuard`、`DisableSchedInterGuard` 都只存拥有型数据；
  - `ObDITls` 用 try 借用，重入时返回 `None`；外部线程只碰 const 初始化、无 Drop 的线程局部；
  - itid 在线程退出时可能重号（§3.1 第 3 条的 BUG(port)），但 QClock 槽用 BCAS 抢、RetireStation 槽有锁、KV hazard 槽用 CAS、TCRef 用 CAS，都容忍重号，不构成未定义行为。
- **04**：
  - printf 引擎是纯 Rust，不经 C 的可变参数；
  - `ObCStringHelper` 的结果来自 arena 的 `&self` 分配；
  - 警告缓冲区是 `Arc<Latch<…>>`，线程局部只存句柄；限流器指针是 `&'static`。
- **05**：
  - 数组、哈希、队列全是安全代码；内部同步的表返回内部引用时要 `&mut self`；
  - `NoPthreadDefendMode` 的表只在 K、V 都 `Sync` 时才 `Sync`；
  - `ObDList` 只接 `Box`/`Arc`，链接字段全是原子量，写之前校验 `owner_`；用户实现的 `dlink()` 即使返回别的链接，也只会被拒绝。
- **06**：
  - `oblib::pod` 和 `derive(ObPod)` 按字段做小端编解码，不按布局重解释；
  - `Slot<T>` 是 `OnceLock<&'static T>`；`ObStringBuffer<'q>` 只能把缓冲移交出去；
  - `ObDeserialize<'de>` 的 `&mut self` 让 `'de` 不变。
- **07**：
  - `ObDatum` 是 packed 的值类型，帧和行按值读写；`DataRef` 的解析带边界检查，伪造的 `DataRef` 只会导致 abort；
  - 视图绑在存储表的借用上，注销和 reset 都要 `&mut`；`TempAllocGuard` 自带 arena；
  - `eval_args`、`batch_eval_map` 的视图都借住 `ObEvalCtx`；`IdVec`/`QueryArena` 按墓碑 id 访问时返回错误；
  - `ObBitVector` 的 DST 转换符合 `repr(transparent)` 的要求。
  - 附注（不是未定义行为）：`DataRef` 的代号只有 12 位（`07-sql-ir-frame-datum.md:161`），同一槽位注销 4096 次后，过期的引用会解析到新表项，读到别的数据，而不是 §3.1 和 8.11 所说的 abort。
- **08**：
  - `MemtablePin` 的 Dekker 式释放协议（S17）；`NodeRef`/`RowRef` 的 `'m` 来自借用；`ArenaCap<'b>` 不变，并配 HRTB；
  - palf 组缓冲改成原子字存储后，栅栏位置正确（S44：写者推进可读起点后 `fence(Release)`，读者复查前 `fence(Acquire)`）；
  - KV cache 只交出绑在句柄上的切片；hazard pointer 不绑线程，所以句柄可以 `Send`（S53）；
  - `ObFifoArena::reset(&self)` 只封口缓存页，页按引用计数释放（`src/storage/allocator/ob_fifo_arena.cpp:50-54,72-90`）。
- **09**：
  - `ParseNode<'p>` 不变且 `!Send`；arena 节点的句柄只能从 `&'p ParseArena` 或同一棵树派生；读访问按值、只经原始指针逐字段访问；
  - `with_pl_ns`、ICU 的 `with_uregex_setText`、libxml2 的 SAX 上下文都由闭包或单次调用限定；
  - `xmlParserCtxtIn<'i>`、`BIOMem<'a>`、`CURL<'b>` 把不拷贝的输入绑在类型上；zstd 每次调用都传入缓冲；
  - longjmp 只发生在 C 帧之间，蹦床都是叶子调用，重入计数拒绝在回调里再解析。
  - 附注（不是未定义行为）：`OwnedParseTree::new_with` 的 `impl FnOnce(ParseNode<'_>, &ParseArena)` 里两个生命周期互相独立，闭包里没法把 arena 分配的字节挂到节点上（编译不过），应写成 `for<'p> FnOnce(ParseNode<'p>, &'p ParseArena)`。
