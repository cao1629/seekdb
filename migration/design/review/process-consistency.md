# 设计文档对抗审查：流程与内部一致性

> - **审查对象**：`migration/RULEBOOK.md` 草稿 v0.1（提交 `a9088af5f`），基线 `7d907abfa`。
> - **只管一类错误**：流程与内部一致性。具体是：文档内部的矛盾；常见构造没有规则，或者规则有两种读法；单个单元的实现者无法执行、又没有交给缺口清单或 seam 表的规则；与依赖图、manifest、kit 流程的冲突；前置条件的顺序；埋在正文里、没进附录 A 的决定。正确性、并发与性能、行为忠实度不在本文范围内。
> - **方式**：只读，没有构建。对照了 7 份调查备忘、`01-depmap-report.md`、`depmap/`、`manifest.tsv`、`00-feasibility-report.md`、`00b-judge-report.md`，以及 kit 的 README、CLAUDE.md、`prompts/02–05`、`templates/`、`scripts/queue_runner.mjs`、`scripts/make_manifest.py`。计数脚本在 `/tmp/rbreview-proc-7f3a2c/`，没有入库；标"启发式"的是正则估算。
> - **行号**：`RULEBOOK.md:N` 指草稿的行号；manifest 的"第 N 行"按 `grep -n` 计，表头是第 1 行。

## 1. 结论

1. **照现稿开工，第一批就会出问题。** manifest 前 300 行里有 116 个单元属于 oblib 的容器、哈希、分配器、线程、锁、日志等基础设施目录，其中相当一部分按规则要"不移植、换成 crate、另写"，比如 `ob_latch.h`、`page_arena.h`、`ob_retire_station.h`、`ob_link_hashmap.h`。后面还有保留 C 的解析器单元，以及由生成器产出的单元。规则手册没把它们移出队列，也没说实现者该写什么；而完成与否靠目标文件是否存在来判断。storage_core、解析器 C 文件、数据表、注册表、组合根，这几类单元的落点在 §0 和 §4 之间也互相矛盾。
2. **跨 3,700 个单元必须一致的名字和签名，大多只给了例子或干脆没写。** 包括槽位名、"trait + 数据 struct"的命名、const 重载的改名、类型改名表、导入路径。另有三条政策需要调用图或类型级的结论，而缺口清单是一个站点一行，装不下：`qa` 一律显式传参、`'q` 生命周期的传播、字段进锁或改原子。几处规则还给同一构造开出了互斥的译法：`init(&mut self)` 和"先绑定、后 init"、ObFixedArray、ObSEArray、表达式算子的状态、CREATE_WITH_TEMP_CONTEXT、向下转型失败时的处理。
3. **crate 图只看了 include 边和链接边，漏掉了 273 个前向声明的向上类型引用**（data_plane→query、query→sql、oblib→sql）。翻译要用到的基础设施 crate、脚本和 API，计划里没有排期，而第 2、3 步都离不开它们。
4. 共 36 条发现：**高 12、中 20、低 4**。最该先改的三条：P-01（manifest 里不按单元翻译的行）、P-31（基础设施和脚本没有排期）、P-11（接收者可变性，以及 `init(&mut self)` 与组合根顺序的冲突）。

## 2. 发现

### 2.1 与 manifest、依赖图、kit 流程的冲突

#### P-01【高】manifest 里有大量不该按单元翻译的行，规则没说怎么处理，而完成判定靠文件存在

- **规则**：
  - §4「是否完成，靠输出文件是否存在来判定」「一个单元对应一个 `.rs` 文件。顺序取自 order.txt」（RULEBOOK.md:698,700）；
  - §0.8「C++ 的四套自研回收方案都不移植」，死代码"由缺口清单逐一确认"（:267,271-274）；
  - §0.8 和 §2.7 规定：`ObLinkHashMap` 换成 `DashMap`，队列换成 crossbeam，锁换成 `oblib::sync`（底层 parking_lot）；
  - §0.7「请求 arena 是 `oblib::mem::Arena`，内部是 bumpalo」；
  - §0.12「保留 bison/flex 生成的三套 C 解析器」；
  - §0.13「`ob_errno.def` → `oblib::error`」，由 build.rs 生成；以及「数据表交给机械转换…（manifest 会给它们打标）」（:361）。
- **具体例子**（manifest 行号 → 源文件）：
  - **被替换或不移植**：81 `src/oblib/lib/lock/ob_latch.h`、100 `src/oblib/lib/allocator/page_arena.h`、139 `ob_qsync.h`、157/158/160 `ob_fixed_queue.h`/`ob_lighty_queue.h`/`ob_link_queue.h`、177 `ob_smart_call.h`、201 `ob_retire_station.h`、202 `ob_hazard_ref.h`、208 `ob_link_hashmap.h`、692 `ob_linear_hash_map.h`、279/286/290 `src/share/cache/ob_kvcache_hazard_*.h`。
  - **死代码**：784 `ob_delay_free_allocator.h`、829 `ob_ring_array.h`、854 `ob_bucket_qsync_lock.h`、865 `ob_co_seq_queue.h`、866 `ob_ms_queue.h`、881 `ob_map_queue_thread_pool.h`。
  - **保留 C 的解析器单元**：953 `src/sql/parser/parse_node.h`（单元里含 `parse_node.c`）、955 `sql_parser_base.h`（含 `.c`）、1172 `fts_base.h`（含 `.c`）、1173 `fts_parse.h`（含 `.c`）、3664 `non_reserved_keywords_mysql_mode.c`、3535 `src/pl/parser/pl_parser_base.h`（含 `.c`）。解析器单元在 manifest 里共 25 行。
  - **生成器的输入**：3609 `src/share/ob_errno.def → rust/port/share/src/ob_errno.rs`。
  - **kit 的队列**：`scripts/queue_runner.mjs:62` 是 `done: existsSync(target)`，`:87` 只派发 `!done` 的行，`:56` 要求表头恰好是 `source\ttarget`，没有可以"打标"的列。
- **问题**：
  - 被替换和不移植的基础设施单元大多在前三个批次里（第 81–290 行）。实现者拿到的指令互相矛盾：队列要求逐行翻译，规则却说这些单元"不移植""被替换""保留 C""由生成器产出"。
  - 实现者写什么，都会生成目标文件，队列就把它记成完成。什么都不写，队列会无限重派。
  - 两份备忘已经明确说过这些单元不进流水线，但没写进规则：内存备忘说"分配器、容器库本身：128 个。这部分应该重写，不按文件翻译"（memory.md:122）；解析器备忘说"8 个含 C 的标'保留 C'…`parse_node.h` 变成布局镜像"（parser-generators-thirdparty.md:242）。
- **建议改法**：§4 增加"单元处置"一条：
  > manifest 增加第三列 `disposition`，取值为 `translate`、`rewrite:<模块>`、`replace:<crate>`、`keep-c`、`generate:<生成器>`、`data:<转换脚本>`、`delete`。
  > 翻译队列只读 `translate` 行，由脚本另写 `manifest.translate.tsv`，表头仍是 `source\ttarget`。
  > 其余行写进 `migration/design/dispositions.tsv`，注明负责的阶段。这个阶段在目标路径写一个只含 `pub use <替代物>` 的桩文件，让跨单元的导入仍然可解析。桩文件的文件头写 `//! Disposition: <值>`，文件尾仍是原格式的状态行 `// PORT STATUS: confidence=high todos=0`（§3 规定标记格式不得改写）。
  > 处置名单由脚本生成，与缺口清单一起审查。要逐个定处置的有：oblib 基础设施目录（container、hash、allocator、thread、lock、alloc、oblog、objectpool、queue、list、atomic、thread_local）下约 190 个单元；解析器 25 行，按解析器备忘的"保留 C/翻译"拆分；死代码 6 个；数据表和 `.def`。

#### P-02【高】storage_core：§0.9 的落点与 §4 路径规则、§0.3 依赖、§0.2 波次、§6 禁令都对不上

- **规则**：
  - §0.9 的表规定 MVCC 行、keybtree、回调链表、memtable arena 进 `storage_core`，还有「palf 组缓冲、LSN 分配器 | 进 `storage_core`」（:285）；
  - §0.9「`storage_core` 要先于 storage 的批量翻译完成，API 冻结，并配好 loom/Miri 测试」（:295）；
  - §0.3 规定 logservice 只依赖「`oblib`、`share`」，`storage_core` 只依赖「`oblib`」；
  - §4 的路径例外表里没有 storage_core；
  - §0.2 的波次里没有 storage_core；
  - §6「翻译阶段不运行编译器、不运行测试」。
- **具体例子**：
  - manifest 第 688 行 `src/logservice/palf/log_group_buffer.h → rust/port/logservice/src/palf/log_group_buffer.rs`，第 689 行 `lsn_allocator.h → rust/port/logservice/…`；
  - 第 2623 行 `src/storage/memtable/mvcc/ob_mvcc_row.h → rust/port/storage/…`，2624 行 `ob_keybtree.h`，2661 行 `ob_tx_callback_list.h`，2656 行 `src/storage/allocator/ob_fifo_arena.h`；
  - 存储备忘写的是"核心约 10 个 manifest 单元，不进批量流水线"（storage-txn.md:277），规则手册没有照搬；
  - logservice 的单元在第 649–730 行，storage 从第 2324 行才开始。
- **问题**：
  1. 按路径规则，这些单元落到 logservice 和 storage，与 §0.9 相反。
  2. 组缓冲如果进 storage_core，logservice 就必须依赖 storage_core，crate 表里没有这条边。如果留在 logservice，§3 又不允许那里有 unsafe。
  3. "先于 storage"太晚：palf 早在 logservice 段就要用到组缓冲的 API。
  4. loom/Miri 测试要 `cargo test`，而模板的 settings.json 禁止它；第 4 步又要等全部翻译完才编译。整个流程里，找不到一个阶段能在 storage 翻译之前把 storage_core 编出来并测完。
  5. 波次表漏了 storage_core，也漏了 parser、`ob_macros` 和各 -sys crate。
- **建议改法**：
  - §4 的例外表加一行：存储备忘列出的约 10 个单元（`ob_mvcc_row`、`ob_row_latch`、`ob_keybtree` 及其 deps、`ob_tx_callback_list`、`ob_mvcc`、`ob_memtable_data`、memstore 分配器、`log_group_buffer`、`lsn_allocator`）→ `rust/port/storage_core/src/<名字>.rs`，处置为 `rewrite:storage_core`，不进翻译队列。
  - §0.3 给 logservice 加上对 `storage_core` 的依赖。
  - §0.2 的波次改成（同时满足 P-26 的 data_plane → query）：
    1. `ob_macros`、-sys/shim
    2. `oblib`
    3. `storage_core`、`share`
    4. `query`、`logservice`
    5. `data_plane`、`parser`
    6. `sql`、`storage`
    7. `rootserver`、`standby`
    8. `observer`
  - 在 P-31 的基础设施阶段手写 storage_core，并完成编译和测试。这一阶段对禁令的例外记进 §7。API 要在 manifest 第 649 行（第一个 logservice 单元）所在的批次之前冻结，而不是"先于 storage"。

#### P-03【中】合并单元：kit 的脚本做不到"合并"，合并后的单元排在哪里也没说

- **规则**：§4「manifest：由脚本按上述规则重新生成，并合并目标路径相同的单元」「顺序取自 order.txt」（:700,713）；§0.4「同名的 `src/sql/pl/X.h` 和 `src/pl/X.cpp` 合成一个单元」。
- **具体例子**：
  - 我按 §4 的规则对 `units.tsv` 做了模拟：3,754 个单元落到 3,718 个目标，其中 36 个目标是合并的（pl/sql 33 个，query/api 3 个）。
  - `rust/port/sql/src/pl/ob_pl_type.rs` 由 `src/sql/pl/ob_pl_type`（order.txt 第 641 行）和 `src/pl/ob_pl_type`（第 1329 行）合成，两者相隔 688 行；`rust/port/query/src/protocol/ob_mysql_protocol_util.rs` 的两个成员在第 788 行和第 1350 行。
  - kit 的 `make_manifest.py:51-65` 每个源文件写一行；`queue_runner.mjs:62` 只要目标文件存在，就把那一行记成完成。
- **问题**：
  - 如果新 manifest 里有两行指向同一个目标，先翻译的那行会写出文件，另一行随之被记成完成，再也不会被翻译。例如 `src/pl/ob_pl` 单元（含 `ob_pl.cpp`，共 3,392 行）就会这样被跳过。
  - 如果合成一行，规则要说清两件事：它排在第 641 行还是第 1329 行；source 列怎么写多个文件（queue_runner 的 source 只是一条路径）。
- **建议改法**：
  > 合并单元在 manifest 里只占一行。source 列写代表文件，另加 `members` 列，用逗号分隔，跨目录的成员也写上。合并后用合并过的图重算单元级的强连通分量，重写 order.txt。生成脚本要检查目标路径唯一，有重复就报错退出。

#### P-04【中】批次划分有三种说法，和 kit"一个环一批"、order.txt 都冲突

- **规则**：
  - §0.9「{memtable, tx, tx_table, tx_storage, tablelock, deadlock, concurrency_control}（约 10.4 万行）…要在同一个批次窗口里翻译」（:296）；
  - §4「顺序取自 order.txt」；文档头「修订…在批次之间应用」；
  - 可行性报告第 3 步「批次门按子目录划分（147 个）」；
  - kit `prompts/01-dependency-map.md:27`「Every component bigger than one file is a **cycle** and migrates as a single batch」；
  - kit 的 `queue_runner.mjs next --batch 100` 只按 manifest 顺序平铺取行。
- **具体例子**：
  - 这 7 个目录共 153 个单元、104,092 行，分散在 manifest 第 2326–3743 行之间，中间夹着 1,265 个别的单元，跨了 10 个百行批次。
  - 单元级的环里，sql 有 1,129 个单元、storage 有 573 个单元，各占 order.txt 的一行（01-depmap-report.md §3.2）。
- **问题**：
  - 机械队列做不到"同一窗口"。
  - kit 要求一个环作为一批来迁，对 1,129 个单元的环根本做不到。
  - 批次门定在哪里没有定义，修订什么时候应用也就没有定义。
- **建议改法**：§4 写明批次定义：
  > 批次 = 翻译 manifest 中连续 100 行，批次门就设在这里。
  > 例外：§0.9 的事务簇由生成脚本挪到 storage 段的开头，作为一个批次窗口。
  > 单元级的环不适用 kit"一环一批"的规定，因为翻译阶段不编译，环只影响名字推断。这一偏离记入 §7。

  也可以删掉"同一窗口"的要求，并说明它在不编译的第 3 步里为什么不重要。

#### P-05【中】单元粒度和第 4 步的波次偏离了 kit 在重新设计模式下的指引，既没进附录 A 的备选，也没记偏差

- **规则**：
  - §4 以头文件组为单元（:700）；A.1 D14 的备选只有「`.h` 和 `.cpp` 各自一个单元」；
  - §0.2「分波次 `cargo check --keep-going`。上一波错误清零，才进入下一波」（:42）；
  - §7 偏差日志里没有对应的条目。
  - kit README:187「The unit of work is a module or subsystem, not a file.」；`prompts/05-survey-build.md:12`「Run ONE scripted survey build … over everything, once」；
  - 可行性报告把 `[build command]` 填成了 `cargo check --workspace --message-format=short`；
  - kit `templates/RULEBOOK.md:129`「A deviation nobody logged is a deviation nobody approved」。
- **问题**：
  - kit 对重新设计模式推荐以模块或子系统为单元，这个选项连备选都没列。
  - 分波次闸门取代了 kit 每轮一次的 survey build，也和可行性报告定好的构建命令不一致。
  - 两处都没记偏差。按 kit 的标准，没记的偏差就等于没有被批准。
- **建议改法**：
  - A.1 D14 增加备选"以子系统（目录）为单元：kit 重新设计模式的默认"，写出利弊。
  - §7 预填 DEV-010（重新设计模式下仍以头文件组为单元）和 DEV-011（第 4 步按波次跑、每波由 build_daemon 构建），在设计文档关口由你签字。
  - 同步修改可行性报告里第 4 步的占位符。

#### P-06【中】第 2 步的定义前后不一，且和禁令冲突

- **规则**：
  - §0.2「第 2 步的'一次性全量试跑'格外重要：在重新设计模式下，它取代双译者对照」（:61）；
  - §0.3「第 2 步用试译的 30–50 万行代码，在 16 GB 笔记本上实测冷 `cargo check`」（:89）；
  - 可行性报告第 2 步「在一个子系统上做一次性的廉价全量试跑」，试点候选是 `ob_raw_expr.h`、`ob_mvcc_row.h`、`ob_link_hashmap.h`；
  - §6「这些操作在第 2 步之前由 `.claude/settings.json` 禁止」（:780）；
  - kit `prompts/03-stress-test.md:5` 说该提示词只适用于保持原结构的迁移；kit README:43「Step 2's pilot needs the denies live」。
- **问题**：
  - 试跑的范围有三种说法："全量"、"30–50 万行"、"一个子系统"。
  - 测 `cargo check` 要用编译器，而第 2 步时禁令已经生效。
  - 三个试点候选里有两个按现稿已经不走流水线：`ob_link_hashmap.h` 换成 DashMap、不移植；`ob_mvcc_row.h` 进 storage_core。
  - 没指定重新设计模式下第 2 步用哪个提示词。"廉价"试跑用哪个模型也没说，而模型计划约束所有步骤（kit CLAUDE.md:85）。
- **建议改法**：新增 §0.2a"第 2 步（重新设计模式）"：
  > 1. 试点：沿用 `prompts/03`，去掉双译者对照。3 个单元从处置为 `translate` 的单元里按标准挑选。
  > 2. 试跑：在一次性 worktree 上全量翻译子系统 X（写明范围，约 30–50 万行）。实现者用模型计划规定的 `claude-sonnet-5-5`，试跑结束后删除。
  > 3. 编译实测由你或 build_daemon 执行，作为禁令例外记入 §7。
  > 4. 在第 2 步关口决定 sql 要不要拆。

#### P-07【中】标记的语义互相重叠，第 6 步之后的清理没法按标记处理

- **规则**：
  - §0.1.4「唯一的例外是…数据竞争…改成在单线程下行为等价的形式，同样标 `BUG(port)`」（:30）；
  - §0.6 `eval_enumset`「改成每次执行的局部副本，标 `BUG(port)`」；
  - §3 定义「`BUG(port): <复现方法>` 照样复刻的原版缺陷」「`PARITY(port)`：为了保持行序或输出而做的特殊处理」（:686-688），而 §0.6 把按地址顺序改成按插入顺序也标 `PARITY(port)`（:204）；
  - §2.7 规则 3 对同类情形标 `TODO(port)`；
  - §0.1.4 还要求标出「00b 报告第 6 节 D 的 15 条疑似 bug」；
  - kit `prompts/06`「Each re-runs the parity referee and proves the ONLY behavior that changed is the marked defect」。
- **具体例子**：
  - `ob_expr_column_conv.cpp:742-746` 已经改掉了，却标成"照样复刻的缺陷"；
  - `ob_raw_expr_copier.cpp:336` 是接受的偏差，标的却是定义为"保持行序"的 PARITY；
  - 00b 报告 §6 D 的 15 条只写了现象和所在用例，没有 file:line。其中第 13 条（客户端截断，未定论）、第 14 条（CI 期望输出的差异）、第 15 条（死锁耗时）不是代码缺陷。
- **问题**：
  - `prompts/06` 会把每个 `BUG(port)` 当作待修的缺陷，而已经改掉的地方没有可修的。
  - PARITY 同时标"保持原样"和"接受偏差"。
  - 同一情形（C++ 的数据竞争在 Rust 里改成无竞争），有的标 BUG，有的标 TODO，有的什么都不标。
  - 实现者不知道 15 条 bug 在哪个文件，没法打标。
- **建议改法**：
  > - `BUG(port)` 只用于照样复刻的缺陷。
  > - 新增 `FIXED(port): <原缺陷>`，用于数据竞争这类例外，不进 06 的"现在修"。
  > - `PARITY(port)` 只用于保持原样的处理。
  > - 新增 `DIVERGE(port): <归一化项>`，用于接受的偏差：地址序、计划内存、递归深度、OOM 时 abort 等。另附一张裁判归一化清单。
  > - 15 条 bug 在缺口清单阶段（`prompts/02`）定位到 file:line，写进清单（classification=BUG）。第 13–15 条不要求打标。

#### P-08【低】状态行的计数口径和 kit 不一致；SLOT 标记没有说明位

- **规则**：§6「`<N>` 是本文件里 `TODO(port)` 的数量」（:773）；kit `prompts/05` 的修复者自检要求「`grep -c 'TODO(port)' <file>` must equal `todos=`」，这个命令按行计数；§3 的 `SLOT(port)` 不像其他标记那样带「: <说明>」。
- **问题**：同一行写了两个 TODO 时，审查者数出现次数，修复者按行数，两者对不上。`grep 'SLOT(port):'` 会漏掉所有 SLOT。
- **建议改法**：「`<N>` = `grep -c 'TODO(port)' <file>` 的输出；一行最多写一个 `TODO(port)`」；SLOT 改为 `SLOT(port): <槽位表达式>`。

#### P-09【中】路径规则会生成非法或冲突的模块名

- **规则**：§4 的路径规则 `src/<模块>/<相对路径>/<名字>.<扩展名>` → `rust/port/<crate>/src/<相对路径>/<名字>.rs`（:701）；§4「各 crate 的 `lib.rs` 和 `mod` 声明由脚本生成，翻译者只写自己的单元文件」（:714）。
- **具体例子**：
  - 352 个 manifest 目标落在 `rust/port/oblib/src/lib/…` 下。模块名 `lib` 与 crate 根文件 `src/lib.rs` 冲突：`mod lib;` 会找 `src/lib.rs`（也就是 crate 根自己）或 `src/lib/mod.rs`。
  - manifest 第 3504 行 `src/observer/main.cpp → rust/port/observer/src/main.rs`。observer 是二进制 crate，`main.rs` 就是它的根，脚本要在这里写 `mod` 声明，而这个文件归翻译者。
  - `src/sql/engine/pdml/static/` 下有 7 个目标，模块名 `static` 是关键字。
- **问题**：要么由脚本统一处理（`#[path]`、`r#static`、改名），要么由实现者各自处理，规则都没说。完成判定和模块树都依赖这些路径。
- **建议改法**：
  > 模块名与 Rust 关键字或 crate 根文件（`lib`、`main`、`mod`）冲突时，加后缀 `_m`，比如 `lib_m`、`static_m`。manifest 生成脚本同时改写目标路径。
  > observer 的二进制入口由脚本生成，写到 `src/bin/seekdb.rs`；`main.cpp` 所在单元的目标改成 `src/ob_main.rs`。

#### P-10【中】数据表单元：目标位置、代码和数据混在一起的单元、清单外的生成文件都没说清

- **规则**：§0.13「数据表交给机械转换，不交给翻译者（manifest 会给它们打标）」「大块数据放进单独的小 crate，用 `include_bytes!` 引入」，范围包括「`ob_timezone_info.cpp`（约 4 万行）、字符集表、`ob_uctype.h`，以及 16 个 `.map`、4 个 `.def`」（:361-363）；§4「数据表单元走机械转换」，单元的扩展名列表是 `.h/.hpp/.ipp/.inc/.cpp/.cc/.c`。
- **具体例子**：
  - 单元 `src/oblib/common/timezone/ob_timezone_info`（manifest 第 221 行）由 `.cpp`（39,989 行）和 `.h`（781 行）组成。`.cpp` 从第 39025 行起有 51 个真正的方法定义，比如 `ObTZNameKey::reset`、`ObTimeZoneInfo::assign`。
  - `src/storage/fts/dict/ob_ik_dic.cpp` 里有 2 个函数。
  - `src/oblib/lib/charset/ob_uctype.h` 被依赖图按生成文件排除了（`depmap/excluded.tsv:12`），根本不在 manifest 里。
  - "字符集表"没有列出具体文件。`ob_ctype_utf8_tab.h`（4,472 行）是纯表，`ob_ctype_uca.cc` 却是代码和表混在一起。
  - `units.tsv` 里有 4 个 `.def` 成员和 16 个 `.map` 成员，§4 的扩展名列表没有它们。
  - 生成器的产物有两处没覆盖：
    - bitpacking 生成器产出 `src/oblib/lib/codec/ob_generated_*_bp_func.*`，共 24,406 行，其中 SIMD 调用 7,200 处（解析器备忘 §2.4、约定备忘 §2.8），§0.13 没提；
    - 系统变量生成器还会写出 `src/sql/session/ob_system_variable_factory.*`（excluded.tsv），而 §0.13 只写了「系统变量 JSON → share」。
- **问题**：
  - 如果整个单元都交给机械转换，代码部分就没人翻译。
  - "单独的小 crate"不是 manifest 的目标路径，所以这一行永远不算完成，会派给翻译者，而 `ob_ik_dic` 这个单元有 27.6 万行。
  - 上面几类生成物是谁、在什么时候产出，都没有着落。
- **建议改法**：
  > 数据表单元按"数据/代码"拆开处理：数据部分由转换脚本写进 `rust/port/<crate>_data/`；代码部分照常作为单元翻译（处置为 translate），目标文件里 `pub use <crate>_data::…`。
  - 逐个列出字符集表文件；§4 的扩展名列表补上 `.def`、`.map`。
  - bitpacking 生成器单独定一个方案，比如由 build.rs 在白名单模块里输出 `core::arch` 代码。
  - 写明系统变量生成器的输出分别落在哪个 crate。

### 2.2 规则之间的矛盾与二义

#### P-11【高】`init(&mut self)` 和"先绑定、后 init"的组合根、LazyLock 单例不兼容；接收者的可变性没有规则

- **规则**：
  - §2.1「保留两段式初始化：字段 `is_inited_: bool`、`fn init(&mut self, …) -> ObResult`」（:441）；
  - §0.5「`X::get_instance()` → `X::instance()`，背后是 `static LazyLock<X>`」（:156）；
  - §0.5「照抄 `obs_construct_modules`（构造 64 个模块、绑定 98 个槽位）→ `obs_init_modules` → `obs_start_modules` 的顺序」（:161）；
  - GCTX 的指针成员是 `OnceLock<&'static T>`，槽位是 `bind(&'static T)`（layering.md:195-199）。
- **具体例子**：
  - `src/observer/omt/ob_server_runtime_controller.cpp:1181` 的 `obs_construct_modules` 先在 1248–1357 行绑定槽位，`:1361` 的 `obs_init_modules` 才调用各模块的 init，比如 `mods_log_storage_adapter_->init(mods_ls_service_, mods_memstore_freezer_)`。
  - `src/observer/ob_server.cpp:702` `ObSimpleThreadPoolDynamicMgr::get_instance().init()`，`:716` `ObSqlTaskFactory::get_instance().init()`，`:758` `ObMemoryDump::get_instance().init()`。全仓库形如 `get_instance().init/start/stop/destroy(` 的调用有 45 处。
- **问题**：
  - 绑定之后手里只剩 `&'static T`，`init(&mut self)` 调用不了；LazyLock 也只给 `&X`。
  - 不只 init：槽位上的服务（1,222 处）、单例（585 处）、GCTX（919 处）的每个非 const 方法都有同样的问题。
  - 规则手册通篇没说 C++ 的非 const 方法译成 `&mut self`，还是 `&self` 加内部可变性。两个 agent 必然各选一种，而被调方选了哪种，又决定了其他单元里的调用方能不能写对。
- **建议改法**：§2.13 加一行"接收者"：
  > - C++ 的 const 方法 → `&self`。
  > - 非 const 方法：如果类型会出现在 §0.5 的静态量、槽位或 Arc 共享位置（由类型表给出，见 P-23），一律 `&self`。可变字段按 §2.7 放进锁或改原子：`is_inited_` 改成 `AtomicBool`，`init` 内部用 Latch 串行化。
  > - 其余类型 → `&mut self`。

  另一种做法是让组合根在 `obs_init_modules` 结束前一直持有 `&'static mut`（`Box::leak`），init 之后再绑定。但这会改变 C++ 的顺序，要在附录 A 里定。

#### P-12【高】带虚函数的类译成"trait + 数据 struct"，两者同名，命名没有规定

- **规则**：§2.13「带虚函数的类 | 一个 trait（方法一一对应），加一个数据 struct：基类数据放在第一个字段 `base`，trait 提供 `base()`/`base_mut()`」（:640）；§4「类型名…保留 C++ 的写法，包括 `Ob` 前缀」。
- **具体例子**：
  - `src/storage/ob_i_table.h:79` 的 `class ObITable` 有 45 处 `virtual`，还有数据成员 `TableKey key_;`（:400）和 `int64_t ref_cnt_;`（:401）。
  - 它的派生类在另一个单元：`src/storage/blocksstable/ob_sstable.h:123` `class ObSSTable : public ObITable, public ObIStorageMetaObj`。
  - 全仓库有 3,920 个派生类、11,591 个虚函数、120 个多重继承（conventions.md §2.8）。
- **问题**：
  - Rust 的类型和 trait 共用一个命名空间，`ObITable` 不能既是 struct 又是 trait。
  - 派生类在另一个单元、由另一个实现者独立翻译，必须写出 trait 名、基类 struct 名和 `base` 字段。各人会选 `ObITableTrait`、`ObITableBase`、`ObITableData`、`IObITable`，结果系统性地对不上。
  - 多重继承时，第二个基类的字段叫什么，也没有规定。
- **建议改法**：
  > - 数据 struct 保留 C++ 名（`ObITable`），trait 名为 C++ 名加 `Virt`（`ObITableVirt`）。没有数据成员的纯接口类只生成 trait，名字保留 C++ 名。
  > - 派生类里，第一个有数据的基类字段叫 `base`，其余基类叫 `base_<蛇形类名>`；`base()`/`base_mut()` 对应第一个基类。
  > - 这些名字写进类型表（P-23），由脚本生成。

#### P-13【高】573 种表达式算子：§0.6 说"零大小结构体"，§2.13 说"trait + 数据 struct"；有状态的 38 种和基类的数据没有去处

- **规则**：§0.6「573 种表达式算子：零大小结构体，加一张以 `ItemType` 为下标的静态注册表…运行期仍然只用函数指针 `EvalFn`」（:198）；§2.13 带虚函数的类那一行；§2.5 规定 `ObSEArray` 译成 `SmallVec`；§0.7「arena 里的类型不能需要 Drop」。
- **具体例子**：
  - 基类 `src/query/api/query/engine/expr/ob_expr_operator.h:731-766` 有成员 `magic_`、`id_`、`row_dimension_`、`real_param_num_`、`result_type_`、`raw_expr_`（:764）；
  - `src/sql/engine/expr/ob_expr_like.h:229-232` 有 `is_pattern_literal_` … `like_id_`；
  - `src/sql/engine/expr/ob_expr_udf.h:77` 的 `class ObExprUDF` 有 `ObSEArray<int64_t, 8> subprogram_path_`（:212）、`ObSEArray<ObExprResType, 5> params_type_`（:214）、`params_desc_`（:215）；
  - IR 备忘记的是"只有 38 个有自有数据成员"，推荐"有状态的 38 种：放进节点上的 `OpState`/`OpExtra`"（ir-graphs.md:153,297）。这个方案没有写进规则手册。
- **问题**：`engine/expr` 下约 480 个单元，实现者可以照 0.6 写零大小类型（状态丢失），也可以照 §2.13 写带 `base` 的 struct（就不是零大小了，注册表里放什么？），还可以自己发明 OpState。结果不确定。
- **建议改法**：把备忘的方案写进 §0.6：
  > - `ExprOp` 的实现一律是零大小类型。
  > - `ObExprOperator` 基类的数据成员，以及 38 个有状态子类的成员，搬到 RawExpr 节点上的 `OpState`。`OpState` 是一个 enum，变体名为子类名去掉 `ObExpr` 前缀。
  > - 由 `ExprOp::calc_result_type(&self, st: &mut OpState, …)` 读写。
  > - §2.13 的"trait + 数据 struct"不适用于 `ObExprOperator` 层次。

#### P-14【高】容器的存储类型：ObFixedArray 一律译成 `ArenaVec<'q>`，与"计划是 Arc 拥有型"冲突；ObSEArray 译成 SmallVec，与"arena 里不能有 Drop"冲突

- **规则**：
  - §2.5「`ObSEArray<T, N>` / `ObArray<T>` / `ObFixedArray<T>` | `SmallVec<[T; N]>` / `Vec<T>` / `ArenaVec<'q, T>`」（:498）；
  - §0.6「共享方式：用 `Arc<PhysicalPlan>`，构建后只读」；
  - §0.7「长生命周期对象：会话、计划缓存…一律改成拥有型」「arena 里的类型不能需要 Drop：`try_alloc` 用编译期断言拒绝」（:214,217）；
  - §2.4 规定查询作用域里先分配后 placement new 的写法译成 `arena.try_alloc(T::new(..))`。
- **具体例子**：
  - `src/query/api/query/engine/ob_operator.h:331-338` 中，ObOpSpec 有成员 `ExprFixedArray output_; startup_filters_; filters_; calc_exprs_;`。`ExprFixedArray` 就是 `ObFixedArray<ObExpr*, ObIAllocator>`（`ob_expr.h:764`）。头文件里这样的成员共 81 个。
  - P-13 的 `ObExprUDF`：每个 raw 表达式节点会懒创建一个算子实例（`ob_raw_expr.cpp:2156-2171`，IR 备忘 §2.2），在查询作用域里 placement new，成员却是 ObSEArray。
  - `src/sql/engine/ob_exec_context.h:961-967` 的 `create_expr_op_ctx` 在 exec ctx 的 arena 上 placement new 各种 ctx 类型。
- **问题**：
  - Arc 共享的计划里放不了 `ArenaVec<'q>`，因为根本没有 `'q`。
  - arena 分配的 struct 里放 SmallVec，会被 `try_alloc` 的断言拒绝。
  - 内存备忘区分过"`auto_free` 数组（563 处）→ ArenaVec"，规则手册没有照搬。
  - QueryArena 用什么存储（typed Vec 还是 bump）也没说，所以 RawExpr 里能不能放 SmallVec 无从判断。
- **建议改法**：容器那一行按"拥有它的类型属于哪一类生命周期"分情况写：
  > - 计划、长生命周期 → `Box<[T]>`/`Vec<T>`；
  > - 查询作用域、且对象由 bump arena 分配 → 增长期用 `ArenaVec<'q, T>`，定型后用 `&'q [T]`，ObSEArray 成员也照此办理；
  > - QueryArena 里的 IR 节点（用 typed Vec 存储，允许 Drop）→ SmallVec。
  > 类型属于哪一类，由类型表（P-23）给出。§0.6 写明 QueryArena 的存储方式。

#### P-15【中】CREATE_WITH_TEMP_CONTEXT 有两种互斥的译法，21 处里有 12 处两种都不适用

- **规则**：§0.6「临时上下文：7 处 `CREATE_WITH_TEMP_CONTEXT`、33 处局部工厂，改成可截断的子作用域」（:179）；§0.7「请求 arena…每个请求一个，对应 C++ 的 `CREATE_WITH_TEMP_CONTEXT`」（:212）；§2.4 里没有这一行。
- **具体例子**：`src/` 下共 21 处调用。
  - **请求级**：`src/observer/mysql/obmp_query.cpp:475`、`obmp_stmt_execute.cpp:1764`、`src/observer/omt/ob_th_worker.cpp:244`、`src/oblib/rpc/frame/ob_req_queue_thread.cpp:80`。
  - **代价改写**：`src/sql/rewrite/ob_transform_rule.cpp:379`、`ob_transform_temp_table.cpp:2337/2523/2598`、`ob_transform_late_materialization.cpp:444`。
  - **两者都不是**：`src/share/geo/ob_geo_utils.cpp:640/721/1857`、`ob_s2adapter.cpp:286`、`ob_wkb_to_json_visitor.cpp:580`、`ob_wkb_to_json_bin_visitor.cpp:881`；`src/share/cache/ob_kvcache_store.cpp:765`、`ob_kvcache_inst_map.cpp:357`、`ob_kvcache_hazard_version.cpp:295`；`src/sql/ob_sql_utils.cpp:516`；`src/sql/engine/expr/ob_expr_in.cpp:509`；`src/observer/vector_index/ob_plugin_vector_index_scheduler.cpp:1350`。
- **问题**：同一个宏有两种译法，后一组没有任何规则可循。"7 处"只数了 `src/sql` 的 `.cpp`。
- **建议改法**：§2.4 加一行，分三种情况：
  > (a) worker 和请求入口（列出上面 4 个文件）→ 每个请求一个新的 `MemCtx`（§0.7）；
  > (b) SQL 编译期、建了 `ObRawExprFactory` 的 → `qa.scope()`（§0.6）；
  > (c) 其余局部临时上下文 → 块内的局部 `Arena`，加上带 C++ 标签的 `MemAttrGuard`，块结束时释放。

  同时更正 §0.6 的计数。

#### P-16【中】向下转型失败时怎么办，两条规则说法不同，适用范围又有交叉

- **规则**：§2.6 R8「向下转型（1,350 处）改成带检查的访问函数，例如 `as_column_ref()`，类不符时返回 `OB_ERR_UNEXPECTED`」（:527）；§2.13「对有类型标签的类层次，先查标签再转，标签不符就 abort；能整体改成 enum 的，按 0.6 处理」（:642）；§0.6 把 LogOp（33）、Path（10）、OpSpec（61）定为 enum，但没说类型不符时怎么办。
- **具体例子**：转成 `ObLog*` 子类的 static_cast 有 309 处（IR 备忘 §2.4），按 0.6 是 enum，那该照 R8 返回错误，还是照 §2.13 abort？`static_cast<ObSelectStmt*>(stmt)` 这类语句转型，R8 提到了"语句回收"，0.6 的 enum 名单里却没有语句层次。
- **问题**：同一种未定义行为，一处译成返回错误，一处译成 abort，用户看到的结果不同（报 OB_ERR_UNEXPECTED，还是进程崩溃）。访问函数的名字（`as_column_ref`）也没有命名规则。
- **建议改法**：合成一条：
  > 所有带标签的向下转型，包括 0.6 改成 enum 的层次，一律译成 `as_<变体蛇形名>()`，返回 `ObResult<&T>`；类型不符时返回 `OB_ERR_UNEXPECTED` 并记 ERROR 日志。只有 C++ 本来就用 `OB_ASSERT` 守住的地方才 abort。
  > 变体名 = 类名去掉 `Ob` 前缀和层次后缀（RawExpr/Log/Stmt/Spec），再转成蛇形。

#### P-17【中】ItemType：有三个头文件，归属模块与路径规则对不上，enum 写法又和新类型规则冲突

- **规则**：§2.12「`ItemType`（约 2,020 个值，两份头文件副本）| 合并成 `query::item_type::ItemType`，`#[repr(i32)]`」（:632）；§2.12「C++ 枚举 | `#[repr(transparent)] pub struct T(pub i32)`…理由：反序列化不校验取值」（:631）；§4 的路径规则和共享类型归属表。
- **具体例子**：
  - manifest 第 731 行 `src/objit/include/objit/common/ob_item_type.h`（2,486 行），按规则落到 `rust/port/query/src/objit/common/ob_item_type.rs`；
  - 第 944 行 `src/query/api/query/parser/ob_item_type.h`（2,477 行），落到 `rust/port/query/src/parser/ob_item_type.rs`；
  - 第 956 行 `src/sql/parser/ob_item_type.h`（18 行），落到 sql crate。
  - 三处都不是 `query::item_type`。
- **问题**：
  - 三个实现者会各写一份 ItemType：同一个 crate 里出现两份 2,000 多个值的完整定义，或者各自去猜该怎么 re-export。
  - `#[repr(i32)]` 只能用在 enum 上，而 enum 正违背新类型规则自己的理由：C 解析器会往 `type_` 写任意整数。
- **建议改法**：
  > ItemType 由 build.rs 读 objit 的头文件，生成到 `query::item_type`，按 §2.12 的新类型规则写：`#[repr(transparent)] pub struct ObItemType(pub i32)`，同时生成关联常量和模块级常量。
  > 上面三个单元的处置为 `generate`，目标文件里只写 `pub use`。

  类型名用 `ObItemType` 还是 `ItemType`，写进 P-18 的改名表。

#### P-18【中】§0 和 §2 引入的新类型名和 §4"保留 C++ 名"冲突，又没有改名表

- **规则**：§4「类型名、方法名、字段名保留 C++ 的写法，包括 `Ob` 前缀…理由：对齐调试时能按名字对照」（:716-720）。§0.6 却在用 `PhysicalPlan`、`ExecRuntime`、`RtExpr`、`RawExpr`、`Operator`、`OpSpec`；§0.5 用 `GlobalContext`、`WorkerCtx`，并把方法 `get_instance` 改名为 `instance`；§0.7 用 `Arena`、`MemCtx`。
- **具体例子**：
  - 源码里出现次数：`ObExecContext` 2,568，`ObEvalCtx` 3,320，`ObArenaAllocator` 2,017，`ObPhysicalPlan` 339，`ObGlobalContext` 111，`lib::MemoryContext` 289。
  - `ObExecContext` 是不是就是 `ExecRuntime`？`ObEvalCtx` 通篇没提。
- **问题**：几千个签名里，有人写 `ObExecContext`，有人写 `ExecRuntime`。"ExecRuntime 的 id 表"既可以理解成新类型，也可以理解成改了名的 ObExecContext。
- **建议改法**：§4 增加"改名表（唯一来源）"：列出全部 C++ 名 → Rust 名及其归属模块，表外的一律保留 C++ 名；并写明 ExecRuntime 是取代 ObExecContext，还是 ObExecContext 里新加的一个成员。

#### P-19【中】unsafe 白名单用的模块路径，在 §4 的路径规则下根本不存在；而规则本身要求的 unsafe 又落在白名单外

- **规则**：
  - §3「unsafe 只允许出现在…`oblib::mem`…`oblib::sync::{bitlock, sharded}`…`oblib::container` 里的侵入式链表…`oblib::signal`…`query` 或 `sql` 里的帧模块」「其余 crate 一律 `#![forbid(unsafe_code)]`」（:660-666）；
  - §0.9「`storage` crate 加 `#![forbid(unsafe_code)]`」；
  - §2.13 SIMD「`std::arch`…加 `#[target_feature]` 和运行时检测」，以及「rdtsc、cpuid、crc32 → `core::arch` 的 intrinsic」；
  - §0.8 memory_dump「Linux 用 `process_vm_readv`，macOS 用 `mach_vm_read_overwrite`」；
  - §0.4.4「上层 `#[unsafe(export_name = …)]` 定义」；§0.12「19 个回调由 Rust 以 `extern "C"` 导出」。
- **具体例子**：
  - 路径规则给出的模块：`page_arena.h` → `oblib::lib::allocator::page_arena`，不是 `oblib::mem`；`src/oblib/lib/lock/*` → `oblib::lib::lock::*`，不是 `oblib::sync`；`src/oblib/lib/signal/ob_signal_handlers.cpp` → `oblib::lib::signal`；`ObSignalHandle` 在 `src/observer/ob_signal_handle.cpp`。
  - `src/oblib/lib/alloc/memory_dump.cpp` → `oblib::lib::alloc::memory_dump`，FFI 调用需要 unsafe；`src/oblib/lib/checksum/ob_crc64.cpp:51-360` 的 SSE4.2 路径 → `oblib::lib::checksum`。
  - SIMD 文件：storage 有 8 个，比如 `src/storage/blocksstable/encoding/ob_dict_decoder_simd.cpp`、`encoding/neon/ob_raw_decoder_neon.cpp`；sql 有 6 个，比如 `src/sql/parser/ob_parse_simd.cpp`、`src/sql/engine/basic/ob_pushdown_filter_simd.cpp`。运行时检测之后调用 `#[target_feature]` 函数，必须写 `unsafe`。
  - `#[no_mangle]` 和 `export_name` 都会触发 `unsafe_code` lint，所以在 forbid 的 sql/storage 里写不了链接期 seam。19 个回调的 C++ 源码在 sql 的单元里（`src/sql/parser/parse_malloc.cpp`），按路径规则进 sql crate，同样编不过。
  - "`query` 或 `sql`"没定帧模块到底放在哪个 crate。
- **问题**：CI 的 grep 和审查者对照的路径不存在；规则本身要求的若干处 unsafe 无处可放，实现者只能违反其中一条。
- **建议改法**：
  > - 白名单改用 manifest 的目标路径或单元来表达，比如 `rust/port/oblib/src/lib/alloc/**`。
  > - 每个 crate 设一个 `simd` 子模块（`…/src/simd/<单元名>.rs`），运行时分派的 unsafe 调用只能写在这里。
  > - 写明帧模块的路径，比如 `rust/port/query/src/engine/expr/frame.rs`。
  > - 解析器回调按函数级重映射搬到 `parser` crate。
  > - 链接期 seam 只允许出现在使用 `deny` 加模块级 `#[allow(unsafe_code)]` 的 crate 里；或者干脆从翻译阶段删掉这一项，因为 seam 表在任何测量之前就定稿了（见 P-32）。

#### P-20【低】依赖白名单和正文自己要用的 crate 对不上

- **规则**：§1「下表点名的 crate…表外的一律不准引入」（:408）。但正文要用的 crate 不在表里：
  - R5「改成 `FixedBitSet` 旁表」（:524），这是 `fixedbitset` crate 的类型；
  - §0.7 的全局分配器建在 `seekdb-jemalloc-sys` 上；
  - §0.12「再用 `cc` 编译」，§0.14 的 zstd -sys 也要用 cc；
  - cxx shim 的 build.rs 需要 `cxx-build`；
  - §0.13 用 build.rs 读系统变量 JSON，需要 serde/serde_json。
  - 另外，§0.14 的 OpenSSL 替代清单里没有 `EVP_BytesToKey`、`DES_fcrypt`/crypt、PEM，而解析器备忘 §2.5 列了它们。
- **问题**：照 R5 写就违反 §1；写 build.rs 的人要用表外的 crate；加密工具类有几个函数没有指定的替代品。
- **建议改法**：
  - 白名单补上 `cc`、`cxx-build`、`serde`/`serde_json`（只作 build-dependencies）、`seekdb-jemalloc-sys`。
  - R5 改成"oblib 的 `ObFixedBitSet`（§2.5 的位集）"。
  - 补齐上面三个加密函数的替代：手写，配金标准测试。

#### P-21【中】UNKNOWN 规则里的"最保守表示"没有定义，kit 自带的范例在本规则下又被禁止

- **规则**：§0.1.5「采用目标语言里最保守的表示」（:33）；§2.4「`NonNull<T>` 只允许出现在 §3 白名单模块里」（:491）；kit `templates/inventory.tsv` 第 5 行把 UNKNOWN 行译成 `Option<NonNull<u8>> + TODO(port)`。
- **问题**：清单本身估计有 3–5% 的 UNKNOWN 行，约 800–1,400 行（01 报告 §8），再加上所有缺行的站点。到了这些地方，各人会挑不同的"保守"类型：Arc、Box、id 或 `&'q`。kit 默认的写法在这里又是被禁止的。
- **建议改法**：§0.1.5 加一张"各类别在 UNKNOWN 时的默认译法"：
  > - 可空性 → `Option`；
  > - 裸指针成员 → `Option<Arc<T>>`，类型属于 QueryArena 图时用 `Option<Id>`；
  > - ObString 成员 → `Box<[u8]>`；
  > - 输出参数 → `&mut Option<Box<T>>`；
  > - 锁保护不明 → 放进锁；原子访问不明 → `Atomic*`；
  > - 返回 int 的函数 → 当作错误码。
  > 每处都标 `TODO(port)`。

### 2.3 需要跨单元知识，却没有交给清单或 seam 表

#### P-22【高】"arena 一律显式传参"需要调用图，正是否决 G2 时用的理由

- **规则**：§2.6 R1「arena 一律显式传参（`qa: &QueryArena` 或 `&mut QueryArena`），**不存进 self**」（:520）；§2.4「`MemCtx` 作为参数显式传递，或从 `WorkerCtx` 里取」（:489）。
  - 分层备忘否决 G2 的理由是："所有直接或间接用到服务的函数都要改签名…改签名需要调用图。depmap 只有 include 级，没有调用图，按文件独立翻译做不到"（layering.md:278）。
  - IR 备忘的估计是："约 590 个工具函数和自由函数…必须加参数；…最多要改 2,270 个签名"（ir-graphs.md:204）。
- **具体例子**：`src/sql/rewrite/ob_transform_utils.cpp:3605-3610` 先构造带 `expr_factory_` 的 `UniqueCheckHelper`，再调用 `compute_tables_property(stmt, check_helper, …)` 和 `ObOptimizerUtil::is_exprs_unique(…)`，后者在别的单元里。每个被调方要不要 `qa`、要 `&` 还是 `&mut`，取决于它自己再往下调了什么，是一个传递闭包。今天持有工厂的上下文对象（如 ObTransformerCtx、ObOptimizerContext）按规则也不能再持有 qa。
- **问题**：调用方和被调方由不同的实现者翻译，而被调方的签名无法从它的 C++ 声明推出来。结果是 1,349 个 IR 单元之间处处对不上。规则手册对 qa 用的，正是它对 ServerCtx 否决掉的那套做法。
- **建议改法**：
  > 签名（参数、返回值、所属的类）里出现编译期 IR 类型（ObRawExpr、ObDMLStmt、TableItem、ObLogicalOperator、Path 及其派生类，以及它们的工厂和上下文类型）的函数，`self` 之后的第一个参数一律加 `qa: &mut QueryArena`，不区分只读与否。这个条件只看声明就能判断，调用方和被调方得出的结论必然一致。
  > 由脚本据此生成 `migration/design/qa_signatures.tsv`，随清单一起审查。
  > 原来持有工厂的上下文类型，改成和 qa 并列传递。
  > `MemCtx` 只保留一种取法（从 `WorkerCtx` 取），删掉"或"。

#### P-23【高】类型级的结论没有载体：'q 参数、生命周期类别、是否共享、trait 名，而清单是一个站点一行

- **规则**：§0.7「查询作用域的类型带 `'q` 生命周期」；§2.4「`ObString` 成员 | 查询作用域用 `ObStr<'q>`…归属由缺口清单决定」，裸指针成员可选 `&'q T`；§5「一个站点一行」（:749）；§0.5「放进静态量或槽位的类型必须是 `Sync`」。内存备忘的前提 4 是"最好附一张脚本生成的类到生命周期的分类表"（memory.md:323），规则手册没有采纳。
- **具体例子**：清单只要把 `ObSelectStmt` 的某个成员定为 `ObStr<'q>`，`ObSelectStmt` 就变成 `ObSelectStmt<'q>`。所有包含它的 struct，以及其他单元里所有提到它的函数，都得加上 `<'q>`。这是跨很多单元的类型传递闭包，调用方的实现者在自己的单元里算不出来。
- **问题**：泛型参数、struct 和 trait 的名字、是否 `&self`（P-11）、属于哪一类生命周期（P-14），这些都是类型级的结论。它们决定了其他单元该怎么写，却没有任何地方承载。
- **建议改法**：在缺口清单阶段（`prompts/02`）由脚本加清单产出 `migration/design/types.tsv`，每个 C++ 类一行，列出：
  > - Rust 名（struct 名、trait 名）；
  > - 生命周期参数（由脚本对成员类型做闭包计算）；
  > - 生命周期类别（查询、计划、长生命周期、批次）；
  > - 是否共享（静态量、槽位、Arc → `&self` 政策，要求 Sync）；
  > - 处置。
  > §5 加第 17 类"类型级结论"；实现者一律从这张表取泛型参数。

#### P-24【中】§2.7 说锁和原子的规则在单元内就能判断，实际要看其他单元，又和 §5 的委托重复

- **规则**：§2.7「数据放进锁的规则（翻译单元是头文件组，类定义和方法在同一个单元里，足够做判断）」（:556）「凡是被任意 `ATOMIC_*` 访问过的字段，类型都改成 `Atomic*`」（:565）；§5 第 7 类「每个锁保护哪些字段（§2.7 的四条规则）」，第 8 类「被原子访问的字段」。
- **具体例子**：
  - `src/storage/tx/ob_tx_data_op.h:151` 有 `common::SpinRWLock &get_lock() { return lock_; }`，加锁的地方都在别的单元：`src/storage/tx_table/ob_tx_table_iterator.cpp:71`、`src/storage/tx/ob_tx_ctx.cpp:1113`、`src/storage/tx/ob_tx_data_define.cpp:498`。
  - `src/query/api/query/vector/ob_vector_index_cache.h:130` 的 `RWLock &get_lock()` 被另外两个 crate 加锁：sql 的 `src/sql/das/iter/ob_das_ivf_scan_iter.cpp:589,608,1602,2526`，observer 的 `src/observer/vector_index/ob_vector_index_ivf_cache_util.cpp:57`。
  - 原子：`src/oblib/lib/stat/ob_diagnose_info.cpp:584` 的 `ATOMIC_LOAD(&source->total_timeouts_)`，访问的字段声明在 `src/oblib/lib/wait_event/ob_wait_event.h`。用启发式找到 51 处这样的跨单元原子访问，这只是下限。
- **问题**：
  - 在单元里看不到哪些字段是在别的单元持锁时写的，也看不到哪些字段在别处被原子访问。
  - 决定权有两个来源（实现者自己判断，还是查清单），以哪个为准没有说。
  - 字段一旦挪进 `Latch<Inner>`，其他单元访问它的写法也跟着变。
- **建议改法**：
  > 删掉"足够做判断"。字段是否进锁、是否改原子，一律以清单第 7、8 类为准。清单缺行时按 UNKNOWN 处理：标量字段改成 `Atomic*`，非标量字段放进锁，标 `TODO(port)`。
  > 返回锁引用的访问函数，改成在拥有者类型上提供返回 guard 的方法（如 `lock_tx_ops()`），方法名写进类型表。

#### P-25【高】跨单元必须一致的名字只给了例子：槽位名、重载名（包括 const 重载）、enum 变体名和访问函数名

- **规则**：§0.5「类型所在 crate 里的槽位，如 `storage::LS_SERVICE.get()`」（:155）；§4 的重载规则「在头文件里声明位置最靠前的那个保留原名；其余改名为'原名 + `_` + 区分参数的类型名的蛇形写法'…仍然冲突时，再加参数个数后缀」（:722-726）；R8「例如 `as_column_ref()`」。
- **具体例子**：
  - `src/share/rc/ob_server_runtime.h:120-140`：C++ 的槽位是模板，在使用处才实例化（`ObServerServiceSlot<Service>::service_`，:127-129），注释写明"Share does not know any upper-layer service type: the slot is instantiated only at the binding and consuming sites"（:122-124）。所以没有任何单元声明过这些槽位，而调用遍布 381 个文件的 1,222 处，涉及 102 种类型。
  - `src/oblib/common/datum/ob_datum.h:172-173` 的 `ObDatumPtr &ptr()` 和 `const ObDatumPtr &ptr() const`，参数表完全相同，按规则推不出名字。启发式统计：253 个头文件里有 666 对 const/非 const 重载。
  - 另一例是 `src/data_plane/api/data_plane/truncate_info/ob_truncate_info_array.h:59-60` 的 `get_array()`。
  - 此外还有跨基类与派生类、跨多个头文件的重载集。
- **问题**：
  - `LS_SERVICE` 由谁声明、叫什么，没有规定。
  - 非 const 版本写在前面时，`ptr` 会归 `&mut self` 那个，const 版本得到一个随意的名字，而绝大多数调用只需要读。
  - 其他单元的调用方必须重复这套推算，而且推出来未必一样。
- **建议改法**：由脚本生成名字表，随清单一起提交：
  > - `slots.tsv`：类型 → 静态量名，统一为 `<类型名去掉 Ob 前缀的大写蛇形>_SLOT`。静态量由脚本写进该类型所在 crate 的 `generated/slots.rs`（见 P-29），不写进单元文件。
  > - `overloads.tsv`：完整签名 → Rust 名。
  > - const 重载：const 版本保留原名（`&self`），非 const 版本叫"原名 + `_mut`"（`&mut self`），不看声明顺序。
  > - 变体名和 `as_*` 访问函数的命名规则，同 P-16。

#### P-26【高】crate 图只看了 include 边和链接边，漏掉了前向声明的向上类型引用

- **规则**：§0.3「依赖方向：等于 include 级 DAG（`crates/edges.tsv`）」（:87），data_plane 和 query 都只依赖「`oblib`、`share`」；§4 归属表「`ExprId` 等编译期 id、`QueryArena` | `sql::ir`」；R1「`ObRawExpr *` 改成 `ExprId`」；seam 表只收链接级的符号（§0.4）。
- **具体例子**：
  - `src/query/api/query/engine/expr/ob_expr_operator.h:55` 前向声明了 `class ObRawExpr;`，有成员 `ObRawExpr *raw_expr_;`（:764），`cg_expr(…, const ObRawExpr &raw_expr, …)`（:355,1233）的参数也用到它。按 R1，query crate 就得引用 `sql::ir::ExprId`。
  - `src/query/api/query/engine/expr/ob_expr.h:161` 的 `struct ObEvalCtx` 持有 `ObExecContext &exec_ctx_;`（:278），而 `ObExecContext` 定义在 `src/sql/engine/ob_exec_context.h:220`。
  - `src/data_plane/api/data_plane/access/ob_tablet_scan.h:37-41` 前向声明了 query 的 `ObPushdownFilterExecutor` 和 `ObExpr`，还用到 `ObFixedArray<ObExpr *, ObIAllocator>`。
  - oblib 里也有：`src/oblib/lib/worker.h:125` 的 `sql::ObSQLSessionInfo *session_;`；`src/oblib/common/object/ob_object.h:1251` 的 `sql::ObExecContext *exec_ctx_;`，位于 `ObObjPrintParams` 中；`src/oblib/lib/hash/ob_hashutils.h:88-90,730-828` 为 `_ParseNode*` 特化了 hash 和 equal，而 parser crate 在 oblib 之上。
  - 分层备忘 §2.6 统计这类类型共 273 个：query→sql 213 个（其中 185 个来自 `ob_operator_reg.h`），data_plane→storage 20 个，data_plane→query 11 个。
  - 分层备忘 Q3 推荐的 A 方案要求"约 28 个 sql 核心类型改用 trait 或句柄表达；`ob_operator_reg.h` 的注册表搬回 sql"。附录 A.2 把 Q3 记为已决定，正文却没有这两项。
- **问题**：
  - 这些引用在 include 图和 nm 结果里都看不到，所以 crate 表和 seam 表都漏了。
  - query、data_plane、oblib 的实现者写不出这些类型的名字。
  - 第 3 波让 data_plane 和 query 并行编译，实际上 data_plane 依赖 query。
- **建议改法**：
  > - seam 生成脚本扩展到"成员和签名里用到的前向声明"，可以用分层备忘的 `fwd_used.py`。每一项选一种处理：句柄 trait、类型下移、不透明句柄，或者把模板特化挪到类型所在的 crate（孤儿规则）。
  > - §0.3 加上 data_plane → query（分层备忘 D 已经说明不成环），波次照 P-02 的建议，让 query 先于 data_plane。
  > - ExprId/QueryArena 挪到 query，或者在 query 定义句柄，写进 A.1 D6。
  > - 把 Q3-A 的两项条件抄进 §0.4。

#### P-27【高】导入路径没有约定："crate 就是命名空间"、共享类型的归属模块、路径规则推出的模块路径，三者互相矛盾

- **规则**：
  - §4「命名空间：crate 就是命名空间；嵌套的命名空间只在用来消除重名时保留为模块」（:721）；
  - §4 的路径规则；§4 的归属表，比如「`ObIArray`、`ObHashMap` 等容器 | `oblib::container`」「序列化 | `oblib::serialization`」「日志宏 | `oblib::log`」；
  - §1 的「`oblib::sort::ob_sort`」；
  - kit `templates/RULEBOOK.md:99-102`："Import specifier convention … decide once or every implementer rediscovers it"，以及"Home module for every shared type"。
- **具体例子**：
  - 按路径规则，`src/oblib/lib/hash/ob_hashmap.h` 落在 `oblib::lib::hash::ob_hashmap`，归属表却写 `oblib::container`；
  - `src/oblib/lib/container/ob_iarray.h` → `oblib::lib::container::ob_iarray`；
  - `src/oblib/lib/utility/ob_sort.h` → `oblib::lib::utility::ob_sort`，§1 写 `oblib::sort`；
  - `src/oblib/lib/utility/serialization.h` → `oblib::lib::utility::serialization`；
  - `src/oblib/lib/oblog/ob_log_module.h` → `oblib::lib::oblog::ob_log_module`。
  - 宏的情况：`#[macro_export]` 会把宏放到 crate 根。73 个函数式宏名在不止一个头文件里定义，其中 30 个在同一个 crate 内，比如 `sint2korr` 同时定义在 `src/oblib/common/mysqlclient/ob_mysql_global.h` 和 `src/oblib/lib/charset/ob_mysql_global.h`。
- **问题**：sql 里的实现者写 `use oblib::container::ObHashMap;`、`use oblib::lib::hash::ob_hashmap::ObHashMap;`，还是按"crate 即命名空间"写 `use oblib::ObHashMap;`，三种写法都符合规则。宏怎么导出、怎么导入、重名怎么办，也都没说。
- **建议改法**：
  > - 各 crate 的 `lib.rs` 由脚本生成，对所有单元模块做 `pub use <模块>::*;`。引用方一律写 `use <crate>::<名字>;`。
  > - 重名由脚本检测；冲突的名字按 `symbols.tsv` 给出的完整路径引用。
  > - 归属表里的 `oblib::mem` 等是门面模块，由基础设施阶段编写。
  > - 宏一律 `#[macro_export]`；同一 crate 内重名的宏，第一个定义保留原名，其余加单元名前缀，名单由脚本生成。
  > - 每个单元文件的第一条 `use` 固定为 `use oblib::prelude::*;`，prelude 的内容在基础设施阶段确定。

#### P-28【中】函数级重映射（下移）对目标单元有什么义务，没写；manifest 又在 seam 表之前就重新生成

- **规则**：§0.4「下移：方法体只用到下层的东西时，把它搬进类型所在的 crate。manifest 为此做函数级重映射」（:111）；§4「按 seam 表下移的函数，归到目标单元」「拍板后重新生成」（:711,713）；§0.4「seam 表…在缺口清单阶段完成」（:125）；§6「文件头：`//! Ported from: <源文件列表>`」。
- **具体例子**：
  - 分层备忘 §3.1 B：`ob_trans_define_v4.cpp:1142-1250` 里 ObTxReadSnapshot、ObTxExecResult 的方法，要归到 data_plane 的 `ob_tx_read_snapshot.h` 单元；
  - `print_service_status` 定义在 `src/observer/omt/ob_server_runtime_controller.cpp:1124`，要归到 share 的 `src/share/io/ob_io_manager.h:98` 单元。share 在 manifest 里排得很前，observer 在第 3430 行前后。
  - 备忘还说"576 个方法符号里能下移的都应该下移"。
- **问题**：
  - 两列的 manifest 表达不了函数级重映射。
  - 目标单元的实现者不知道要从别的单元的 `.cpp` 里把函数体拉过来，源单元的实现者也不知道要跳过它们。
  - 设计文档关口重新生成的 manifest，早于 seam 表。
- **建议改法**：
  > 实现者开工前查 seams.tsv：
  > - 本单元是某行的"Rust 落点"时，按 file:line 把列出的函数体一并翻译，并写进 `Ported from`；
  > - 本单元是某行的"定义处"、拆法为下移时，跳过这些函数，在原位置留 `// moved to <路径> (seam S-xxxx)`。
  > manifest 在 seams.tsv 签字之后再生成一次。

#### P-29【中】中心注册表、组合根、main.rs 由翻译者写还是由脚本写，规则自相矛盾

- **规则**：
  - §2.10「组合根的绑定由脚本追加，翻译者不改组合根」「中心注册表一律由脚本生成…包括：表达式注册表、算子注册表、各 crate 的模块树（`lib.rs`）、组合根绑定」（:601-602）；
  - §0.5「照抄 `obs_construct_modules`…纯函数式的 seam 和 weak 钩子在 `main` 的第一行注册」；
  - §2.13「构造时有副作用（比如注册）的，改成组合根里的显式调用」；
  - §6「只写自己的单元文件」。
- **具体例子**：
  - manifest 第 1212 行 `src/sql/engine/expr/ob_expr_operator_factory.h`（单元里含 `.cpp`，有 528 个 `REG_OP`）；
  - 第 1012 行 `src/query/api/query/engine/ob_operator_reg.h`，第 1059 行 `src/sql/engine/ob_operator_reg.h`，第 1602 行 `src/sql/engine/ob_operator_factory.h`；
  - 第 2535 行 `src/storage/multi_data_source/compile_utility/mds_register.h`；
  - 第 3430 行 `src/observer/omt/ob_server_runtime_controller.h`（`obs_construct_modules` 在 :1181）；
  - 第 3504 行 `src/observer/main.cpp → rust/port/observer/src/main.rs`；
  - main 之前就执行的注册：`src/observer/composition/retrieval/ob_das_legacy_tr_merge_iter.cpp:1325-1335` 的全局对象 `g_text_retrieval_registration`，构造时调用 `install_legacy_das_text_retrieval_engine()`（分层备忘 §2.7）。
- **问题**：
  - 这些都是翻译单元，"翻译者不写注册表、不改组合根"等于没给它们规则。
  - 脚本如果先往同一个目标路径写东西，完成判定就会把整个单元记成已翻译。
  - 把静态初始化的副作用搬进组合根需要改组合根，而翻译者不许改。
  - LogOp、Path、OpSpec 的 enum（§0.6）横跨多个单元，没人负责写它们的定义。
- **建议改法**：
  > - 脚本的产物一律写到 `rust/port/<crate>/src/generated/` 下，不进 manifest，由单元文件 `include!` 或 `pub use`。
  > - 注册表单元照常翻译，只把注册表本身换成 `include!("generated/<名字>.rs")`。
  > - 组合根单元照抄 `obs_construct_modules` 的顺序；seam 绑定和静态初始化的副作用由脚本生成 `generated/bindings.rs`，组合根在 C++ 对应的位置调用 `generated::bind_all()`。
  > - LogOp、Path、OpSpec 和 OpState 的 enum 定义，由脚本从各单元的变体 struct 生成。

#### P-30【中】"下压过滤器族按 0.6 处理"是悬空引用

- **规则**：§0.4 逐边方案中 sql↔storage 那一行写着「`ObExpr` 和下压过滤器族按 0.6 处理」（:140）。全文搜"下压""pushdown""过滤器"，只有这一行，§0.6 里没有任何相关规则。
- **具体例子**：`ObPushdownFilterExecutor` 在源码里出现的次数：storage 139、sql 46、query 37、data_plane 8。分层备忘 §2.3 指出它的方法体依赖 sql 引擎内部（`sql/engine/basic/ob_pushdown_filter.cpp:28-35` include 了 sql 引擎和代码生成的头文件），不能下移，并说要"和 SQL IR 主题一起定"。
- **问题**：横向 377 个成环符号中，有一族的形状没人定。seam 表的作者和 storage 的实现者只能各自设计接口。
- **建议改法**：在 §0.6 补一节"下压过滤器"：
  > query 定义 trait `PushdownFilter`，方法清单由 seam 表给出；sql 实现；storage 只持有 `Box<dyn PushdownFilter>`，或者通过 RtExprId 加 EvalFn 访问。
  > data_plane 里那 8 处，按 P-26 的方式处理。

### 2.4 前置条件的顺序，以及埋在正文里的决定

#### P-31【高】基础设施 crate 和各种脚本是第 2、3 步的前提，计划里却没有排期、负责人、模型和关口

- **规则**：
  - §0.3 列了一批新建的 crate 和模块：`ob_macros`，oblib 的 `mem`/`sync`/`thread`/`error`/`log`/`fmt`/`container`/`serialization`/`slot`，`parser`，`storage_core`，-sys/shim（:69-82）；
  - §2 处处用到只给了示例的宏和 API：`ob_fail!`、`ck!`、`log_warn!`、`ob_printf!`、`to_string_kv!`、`ob_serialize_member!`、`smart_call!`、`try_box`、`Arena::try_alloc`……；
  - §0.4、§0.13、§2.10、§4 提到的脚本至少有 7 个：seam 表生成、manifest 再生、模块树、注册表、组合根绑定、数据表转换、名字表。
  - 文档头「拍板之前，不开始缺口清单和第 2 步」；kit CLAUDE.md:85「Running subagents on an untriggered default is a process violation」。
- **具体例子**：第一个翻译批次（manifest 第 1–100 行）里就有 `ob_latch.h`（81）、`ob_malloc.h`（97）、`page_arena.h`（100），它们的替代物必须先存在。每个单元都要用 `ob_fail!`、`log_warn!`，而它们的确切签名只有例子。
- **问题**：
  - API 没冻结，3,750 个实现者就会各自发明签名。
  - 第 2 步的试跑没法编译，也没法评判。
  - 模型计划里没有基础设施作者，也没有 seams.tsv 审查者这两类角色。
- **建议改法**：在第 2 步之前新增一个阶段"1c 基础设施"：
  > - 交付物：§4 归属表里每个名字、§2 里每个宏的 API 骨架，包括签名、文档和测试；脚本清单，写明负责人。
  > - 关口：你签字。
  > - 模型：API 设计用 opus-5-5；代码由 sonnet-5-5 实现，配两名审查者。这两项写进模型计划。
  > - 本阶段对禁令的例外，记入 §7。
  > - 第 2、3 步的提示词把这些 API 文档作为只读上下文。

#### P-32【中】几项有前提条件的决定被写成了定论，而它们依赖的结果排在后面

- **规则与例子**：
  1. **临时作用域**：§0.6「改成可截断的子作用域…这和 C++ 释放临时 MemoryContext 的效果完全一样」。IR 备忘 Q2(A) 的原话是"前提是缺口清单确认作用域内不向外层分配"，否则改用 B（分层 id）；而且它已经找到一处反例："至少一处让临时节点和语句级节点混在同一组数据结构里：`ob_transform_utils.cpp:3590-3625`"（ir-graphs.md:89）。在 `:3605-3616`，局部 `ObRawExprFactory` 造出的节点和语句级的 `exprs` 一起流进了 `res_info`。
  2. **排序基准**：§0.11.3 的前提是「在 Linux 上复跑 C++ 版裁判（DEV-005）时，没有因为排序差异而失败」，可是 `src/oblib/lib/utility/ob_sort.h` 在 manifest 第 151 行，属于第二个批次。
  3. **sql 拆分**：§0.3 规定第 2 步之后再决定拆不拆 sql，一旦拆分，crate 名、路径规则和 seam 都要变。
  4. **链接期 seam**：§0.4.4 说"只用于经过测量确认的热路径"，但 seam 表在任何测量之前就定稿了，这一项实际上永远选不上。
- **问题**：决定依赖的结果在决定之后才出来，没有触发条件，也没有回退步骤。
- **建议改法**：
  - 附录 A 把这几项列为"有条件的决定"，写明触发条件，以及由哪一步来确认。
  - DEV-005 排在第 3 步之前。它只是一次裁判运行，成本很低。
  - 写明："清单第 11 类发现有向外层分配时，改用分层 id（ir Q2-B），重新审查 §0.6。"

#### P-33【中】需要你决定的事埋在正文里，附录 A 没有列出

- **规则与例子**：
  1. **目标平台**：§2.12「`long` → `i64`（Windows 上原本是 32 位…标 `TODO(port)`）」，§2.13 用 `cfg` 拆分 `ob_sse_to_neon.h`，§0.8 只写了 Linux 和 macOS 的内存探测，§0.14 的 libaio 只有 Linux 有。全文没有目标平台清单。解析器备忘写明的 target 包括 linux、windows-gnu、android、macOS、msvc（parser-generators-thirdparty.md:171）。
  2. **接受的用户可见偏差**：§0.6 视图里计划内存数值的归一化（:189）、§0.11.5 递归深度（:330）、§0.7 OOM 直接 abort。这几项都不在附录 A；A.2 只写了"混合策略"。
  3. **链接方式**：§0.3「所有 Rust crate 合成一个 staticlib」与 observer「二进制」矛盾，见 P-36。
  4. **性能带**："性能带不达标时"（§0.8，:253；A.2）。"性能带"在 `migration/*.md` 里都没有定义。
  5. **排期**：§0.9 storage_core 的前置阶段和批次窗口（P-02、P-04）。
  6. **清单范围**：见 P-34。
  7. **模型**：seams.tsv 审查者和基础设施作者用什么模型。
- **建议改法**：增加 A.3"随正文出现、需要你确认的决定"，把以上各项逐条列入，写明推荐和备选。

#### P-34【中】清单范围是依赖图报告估算的两倍多，成本和工期没有重估，也没请你拍板

- **规则**：§5 列了 16 类，其中包括「指针的可空性（`OB_ISNULL` 34,194 处）」「返回 `int` 的函数…失败后仍会继续执行的函数（约 27%）的标签」「每个锁保护哪些字段」（:753-768）。依赖图报告 §8 的估算是"可行性阶段扫出约 27,324 处…约 4,500 次…1.8×10⁹ token…并发 8 约 9.8 天"，这个数字不含以下几项：
  - OB_ISNULL 34,194 处；
  - ObString 成员 1,032 个；
  - 声明 `ret` 的 39,077 个函数；
  - 锁：声明 461 处、guard 2,109 处；
  - 原子字段 736 个。
- **问题**：你在拍板 D 项时看不到 §5 让第 1 步的清单成本翻了一倍还多。文档头写着"拍板之前，不开始缺口清单"，可关口上恰恰缺这份估算。
- **建议改法**：按类别重新估算，并区分脚本和 agent 的分工：OB_ISNULL、int 返回值这两类先由脚本打标签，agent 只处理剩下的部分。把"清单范围"作为 D15 列入 A.1，附成本表。

#### P-35【低】金标准测试、布局测试和裁判的改动都没有排期，也不在 manifest 里

- **规则**：
  - §4「逐行移植的算法要配金标准测试，放在 `rust/port/<crate>/tests/`」（:745）；
  - §0.10「约 25 个按内存布局直接落盘的头…每个配 golden 文件」「裁判增加跨二进制场景」；
  - §2.12「位域…每个结构配一条与 C++ 对照的布局测试」；
  - 裁判的归一化分散在 §0.6、§0.7、§0.11 里；
  - kit README 第 6 步要求先调试裁判，再相信它的结论；00b 也用故意改坏的代码验证过裁判。
- **问题**：
  - 金标准值要靠编译、运行 C++ 来产出，而这在翻译循环里是禁止的；这些测试文件也不在队列里。
  - 改过的裁判需要重新验证。
- **建议改法**：
  - 列一份"裁判改动清单"：跨二进制场景两个、关闭 rich_error、EXPLAIN 归一化、计划内存归一化、内存视图归一化。把它作为第 5 步的前提，并用故意改坏的代码再验证一遍。
  - 金标准测试和布局测试放到第 4 步之后的专门阶段：由 C++ 测试程序生成 golden 文件，写明负责人和模型。

### 2.5 误引与措辞

#### P-36【低】误引、丢掉限定语、未定义的术语、计数口径

1. §0.14「gRPC + protobuf C++、protobuf-c | 换成 tonic + prost，只用在 standby」（:377）。解析器备忘 §2.5 写的是：protobuf-c 1.4.1 用在 `ob_geo_mvt.cpp` 里，实现 ST_AsMVT；proto 文件是 `src/share/geo/ob_vector_tile.proto`，生成的 `ob_vector_tile.pb-c.c/h` 见 excluded.tsv。
   改为："gRPC 只用在 standby；protobuf-c（ST_AsMVT）换成 prost，放在 share。"
2. §0.3「链接：所有 Rust crate 合成一个 staticlib」（:91）。分层备忘 §6 的原话是"**共存期间**所有 Rust crate 合并成一个 staticlib"；而规则手册的 observer 是二进制 crate。
   改为："C++ 与 Rust 混合链接期间合成一个 staticlib；最终产物是 observer 二进制。"
3. A.2「生成器按 Ga 方案」（:827）。"Ga"只在解析器备忘 §3.3 里有定义。
   改为把方案内容写出来。
4. §0.6「由 528 个 `REG_OP` 一一生成」（:198）。实际是 573 个子类对应 528 个 `REG_OP` 加 1 个 `REG_SAME_OP`，中间类不进注册表。
   改为"注册表由 528 个 `REG_OP` 和 1 个 `REG_SAME_OP` 生成；中间类不注册"。
5. §0.6「7 处 `CREATE_WITH_TEMP_CONTEXT`」（:179）。这只数了 `src/sql` 下的 `.cpp`，全库共 21 处。
   改法见 P-15。

## 3. 缺失的规则

下表列的是源码里常见、§2 却没有给出译法的构造，或者只给了"由清单决定"、却没给出可选译法的构造。计数口径写在括号里；"启发式"指正则估算。和上文发现重复的，只注明编号。

| # | 构造 | 规模 | 例子 | 现状 | 建议加的规则 |
|---|---|---|---|---|---|
| 1 | 普通指针参数（不是 `T *&`）、返回指针的方法 | 头文件里约 8,590 个指针参数、约 2,227 个返回指针的方法（启发式） | `const ObTableSchema *table_schema` 这类参数；`T *get_x()` | §2.4 只管裸指针成员和 `T *&`，§2.1 只管 OB_ISNULL 检查 | 不可空的指针参数：const 译成 `&T`，非 const 译成 `&mut T`；可空的译成 `Option<&T>`/`Option<&mut T>`，可空性查清单第 1 类；返回值同理；转移所有权的（被调方 new、调用方 delete）译成 `Box<T>`，由清单标出 |
| 2 | 引用成员 `T &x_;` | 头文件约 1,184 个（启发式）；可行性报告记 1,278 个 | `src/query/api/query/engine/expr/ob_expr.h:278` 的 `ObExecContext &exec_ctx_;` | 没有规则；§5 第 4 类只写了"其他借用成员" | 查询作用域里译成 `&'q T`；指向全局服务的译成 `&'static T`；其余改成 id 或 `Arc`。由类型表（P-23）决定 |
| 3 | `ObIAllocator &/*` 参数；借用分配器的成员 | 带分配器参数的签名 7,147 个；成员 `ObIAllocator *x_;` 309 个、`&x_;` 200 个（memory.md §2.3） | `ObSQLUtils::generate_new_name_with_escape_character(ObIAllocator&, …)` | §0.7 只说长生命周期对象的这类参数"随之消失"，查询作用域里怎么写没有说 | 查询作用域的函数：参数改成 `arena: &'q Arena`，借用分配器的成员改成 `&'q Arena`；拥有型的删掉参数和成员。怎么判定，查类型表或清单 |
| 4 | 接收者的可变性（`&self` 还是 `&mut self`） | 所有非 const 方法 | 见 P-11 | 没有规则 | 见 P-11 |
| 5 | `reinterpret_cast` | 3,692 处 | 存储编码里的 `*reinterpret_cast<int64_t*>(buf)`；`src/sql` 里 121 处 `reinterpret_cast<u?int64_t>(`（ir-graphs.md:63） | §5 第 9 类交给清单，但没有给出可选的译法；storage 又是 forbid(unsafe) | 给出可选译法：字节读写用 `from_le_bytes`/`from_be_bytes`；POD 视图用 oblib 白名单模块里的 `pod::cast`；以地址为键的改成 id；派生类槽当基类槽用的，按 R3 处理 |
| 6 | 语句槽 `Ob*Stmt *&` | 330 个（ir-graphs.md:82） | `ObSelectStmt *&get_ref_stmt()`（`ob_raw_expr.h:2850`） | R3 只写了 `ObRawExpr *&` 和 `set_param` | 同样"拷出、调用、立即写回"，写回用 `qa.set_ref_stmt(e, v)` 这类按槽位命名的 setter，标 `SLOT(port)` |
| 7 | 在栈上构造 ParseNode、写非标量字段 | 6 个文件里 27 处调用节点构造函数；53 个文件写字段，上限 698 行；24 个文件 39 处 `const_cast<ParseNode`（解析器备忘 §2.3） | `src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:54-58`：直接写 `ret_node.type_`、`int16_values_[…]` | §2.9 只允许标量字段的 setter，`ParseNode<'p>` 没有构造方法 | 解析器 crate 提供 `ParseArena::new_node(kind, children)`，栈上的节点改成在解析 arena 里分配；写 `children_` 改用 `set_child(i, node)`；`const_cast` 那 39 处交给清单逐处处理 |
| 8 | 字段和方法的可见性 | `friend class/struct` 508 处（不含被排除的生成、第三方文件） | `src/storage/tx/ob_trans_ctx.h:87` `friend class CtxLock;`；`src/sql/engine/ob_exec_context.h:190,199` | 没有 pub/private 规则；在 P-26 的 seam 里，上层要访问下层类型的字段 | 二选一写定：(a) 翻译阶段字段和方法一律 `pub`，对齐之后再收窄，用新标记 `VIS(port): <原访问级别>` 记下；(b) 按 C++ 的访问级别映射，被 friend 访问的改成 `pub(crate)`，跨 crate 访问的改成 `pub` |
| 9 | const 与非 const 重载对 | 253 个头文件里 666 对（启发式） | `ob_datum.h:172-173` | 见 P-25 | 见 P-25 |
| 10 | 单元里没有 `USING_LOG_PREFIX`，却调用了 `LOG_*` | 11 个单元 | `src/query/api/query/engine/expr/ob_expr`、`src/share/ob_define`、`src/oblib/lib/trace/ob_trace` | §2.2 只写了 `#define USING_LOG_PREFIX P` 怎么译。C++ 里，头文件内联函数的前缀取决于 include 它的那个 `.cpp` | 没有前缀的单元，统一用 `const LOG_PREFIX: ObLogMod = ObLogMod::COMMON;`，标 `DIVERGE(port)`（见 P-07） |
| 11 | 非 Copy 的线程局部变量 | `thread_local`/`RLOCAL` 共 233 处，其中一部分是类类型 | `src/observer/ob_srv_xlator.cpp:87` `static thread_local common::ObArenaAllocator sync_arena`；`src/oblib/lib/oblog/ob_log.cpp:242` `RLOCAL(ObLogIdLevelMap, id_level_map)` | §2.13 只写了 `Cell<…>` | Copy 类型用 `Cell`；其余用 `RefCell`，经 `with_borrow_mut` 访问；不允许借用跨过函数调用 |
| 12 | 不带作用域的枚举值，在 C++ 里不加限定直接使用 | ItemType 约 2,020 个值，等等 | `T_INT`、`T_QUESTIONMARK` | §2.12 只生成关联常量，而关联常量不能 `use` 进作用域 | 同时生成模块级常量 `pub const T_INT: ObItemType = ObItemType::T_INT;`，随 P-27 的 crate 根 `pub use` 一起导出；引用方照 C++ 不加限定地写 `T_INT` |
| 13 | 宏的导出、导入和重名 | 头文件里 2,863 个函数式宏，其中 73 个名字定义在不止一个头文件里 | `sint2korr` 等 | 只说了"同名小写" | 见 P-27 |
| 14 | `ObArenaAllocator` 成员属于哪一级 | 382 个成员（memory.md §2.3） | 算子、会话、计划里的 arena 成员 | §2.4 分了请求级、语句级、批次级，但没说由谁判定 | §5 加一类"arena 成员的层级"，由清单给出 |
| 15 | `OB_NEWx(T, pool, …)` 里 pool 的生命周期 | `OB_NEW*` 共 657 处的一部分 | — | §2.4「按 pool 的生命周期选」，由谁判定没说 | 并进第 14 类，由清单给出 |
| 16 | 虚继承 | 2 处 | conventions.md §2.8 | 「逐处处理」，但没有交给清单 | §5 加一类，或者在本表直接写出这 2 处的译法 |
| 17 | 返回锁引用的访问函数 | 至少 3 个头文件 | `ob_tx_data_op.h:151`、`ob_vector_index_cache.h:130`、`src/sql/engine/ob_sql_memory_manager.h:554` | 见 P-24 | 见 P-24 |
| 18 | 下层 crate 里为上层类型写的模板特化（孤儿规则） | 若干 | `src/oblib/lib/hash/ob_hashutils.h:730-828` 为 `_ParseNode*` 写的特化 | §2.13「特化 → 每个类型一个 trait impl」，没说 impl 放在哪个 crate | impl 一律放在类型所在的 crate，由脚本按函数级重映射挪过去 |
| 19 | OpenSSL 的几处替代缺口 | `EVP_BytesToKey`、`DES_fcrypt`/crypt、PEM | 解析器备忘 §2.5 | §0.14 的替代清单里没有 | 见 P-20 |
| 20 | bitpacking 生成器的产物 | 24,406 行，其中 SIMD 调用 7,200 处 | `src/oblib/lib/codec/ob_generated_*_bp_func.*` | §0.13 没有提到 | 见 P-10 |
| 21 | 系统变量钩子的函数名 | 94 个钩子写的是 C++ 函数名 | `ob_system_variable_init.json:53` 的 `ObSysVarOnCheckFuncs::check_and_convert_charset_not_null` | 解析器备忘说"钩子名按命名规则映射到 Rust 路径"，规则手册没写 | 由生成器按 §4 的路径规则、改名表和重载表映射；映射表随 build.rs 一起入库 |
| 22 | 导入约定和 prelude | 每个单元 | kit 模板 §4 的要求 | 没有 | 见 P-27 |
