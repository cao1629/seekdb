# 关口：缺口清单的前 20 行，加基础设施 API 审查（阶段 1c、1d）

> - **状态**：等你签字（2026-10-12）。kit `prompts/02` 规定"做完前 20 行就停下来给你看，再扇出"；1d 的 API 文档也在这一关一起交给你。
> - **基线**：上游 master `7d907abfa`；设计文档 `migration/RULEBOOK.md` v0.2（2026-10-11 签字）。
> - **要你回答的**在第 0 节；其余各节是证据。

## 0. 要你回答的

1. **修订批次 01**（`migration/amendments/batch-01.md`，112 行）：
   - 7 个决定 B1–B7，都推荐 A，详见第 4 节；
   - 其余 105 行（83 行原样采纳、22 行改后采纳，0 行拒绝）按推荐应用。
   - 应用之后得到 RULEBOOK v0.3 和修订后的 9 份 API 文档。
2. **清单扇出的规模和节奏**（新问题，第 6 节）：
   - 前 20 行的实测成本，加上账户的用量上限，说明 D15 估的"约 8 天"不成立；
   - 照现在的模型计划，日历时间是数周到数月；
   - 推荐 R-A：先补脚本规则，再分层审查。
3. **新发现的疑似 C++ bug**（第 5 节）：移植时都照规则处理（`FIXED`/`BUG`）。要不要在 C++ 侧单独修、要不要报给上游，由你决定；推荐暂不处理，对齐之后再说。
4. **偏差日志** DEV-017…019（第 7 节）。

**如果你只回"继续"**，我就按以下理解执行：
- B1–B7 全部选 A；
- 批次 01 按推荐应用；
- 扇出采用 R-A；
- 疑似 bug 暂不处理；
- DEV-017…019 确认。

之后的顺序是：
1. 应用批次 01；
2. 修订 API 文档，用桩工作区跑 `cargo check` 核对各文档的签名；
3. 提交 API 冻结；
4. 补脚本规则；
5. 扇出。

## 1. 1c 扫描：完成的定义

扫描全部由脚本完成：两次运行逐字节相同，键在类别内唯一。站点清单在 `migration/inventory/sweep/`，计数取自 `counts.tsv`。

| 类别 | 站点 | 已由脚本定论 | 说明 |
|---|---|---|---|
| c02 指针参数、`T *&` | 6,212 | — | 5,892 个输出参数（AST）、320 个"返回新分配的指针" |
| c03 裸指针成员 | 6,735 | — | AST 6,471 加正则 50 加函数指针 214 |
| c04 借用成员 | 2,708 | — | 分配器 594、引用 1,057、ObString 1,057 |
| c05 自持 arena | 469 | — | 451 个类、18 个成员 |
| c06 返回 int | 52,696 | **48,659**（错误码 48,471、值 188） | 剩 4,037 行交 agent |
| c07 锁 | 588 | — | 447 个类里的 549 个锁成员，加 6 个锁基类、27 个访问函数、6 行正则 |
| c08 原子 | 1,318 | — | 3,045 处原子操作对应 919 个字段，另有 82 个 volatile |
| c09 布局 | 4,310 | — | union 300、位域 200、packed 59、柔性数组 73、reinterpret_cast 3,672 |
| c10 运行期写计划 | 65 | — | |
| c11 作用域 | 1,080 | — | |
| c12 跨算子旁路 | 56 | — | |
| c13 weak 和 extern 原型 | 174 | **174** | |
| c14 静态初始化、对象池 | 830 | — | 另有初始化顺序表 2,988 行 |
| c15 大重载集 | 16 | — | `overloads.tsv` 已由脚本生成 |
| c16 死代码 | 10 | — | |
| c17 疑似 bug | 62 | — | 另有 5 个新发现待补（第 5 节） |
| c18 就地改写 | 544 | — | |
| c19 多重归属 | 342 | — | |
| c20 虚继承、`void *` | 195 | — | |
| c21 解析树 | 193 | — | |
| c22 会话字段 | 324 | — | |
| c23 ObItemType | 29 | — | |
| seams | 2,693 | — | 与依赖图的 741 个成环符号完全对上，另多出 1 个 |

**生成表**（`migration/design/`）：
- `units.tsv`：3,719 个单元，3,435 个 translate，60 个待复核；
- `types.tsv`：13,133 个类；
- `seams.tsv`：2,693 行；
- `overloads.tsv`：9,955 行，4,054 个重载集，没有冲突；
- `slots.tsv`：109 个槽位；
- `qa_signatures.tsv`：11,444 行；
- `symbols.tsv`：90 个重名，外加 19 个宏。

**扫描中的两处修正**：
- 共享库剥离注释和字符串时，在 16 个文件里丢了行号。改成保留行号的版本后全部重跑，结果是：键全部不变；只有 c02 的 12 个预标签变得更准，其余输出逐字节相同。
- 声明数据库用 libclang 18 加基线 clang 17 的资源目录建成：341 个编译单元，0 个致命错误，291,323 条记录；它不入库，可由脚本重建。

## 2. 前 20 行

流程按 kit 执行：
- **挑选**：挑各类别里最难的站点，见 `migration/inventory/first20.tsv`。
- **提议**：一名 Opus 提议者逐个追踪数据流。
- **审查**：两名 Opus 怀疑者分别审查，互不读对方的结论。
  - 第 1 轮：7 行两人都确认，9 行至少一人驳回，4 行只有证据缺陷。
  - 9 行驳回里有 3 行只是分类码用错：提议者开工时，S3b 的分类码还没写进 README。
- **修订与复审**：提议者修订一次，两人复审，13 行全部确认。

**最终结果**：确认 18 行，未定（UNKNOWN）2 行。没有因为仍有争议而变成 UNKNOWN 的行。2 行未定是提议者在修订中认定"现行规则下没有正确答案"，两名审查者都确认这个判断。

| # | 类别 | 站点 | 分类 | 状态 | 译法要点 |
|---|---|---|---|---|---|
| 1 | c02 | `ObExecContext::create_expr_op_ctx(…void *&)` | OUT_BORROW | 确认 | 照 §2.1 保留输出参数，独立命名；api/07 的合并写法改回（AB1-106） |
| 2 | c02 | `create_rowkey_check_hashset` | OUT_BOX | 确认 | `&mut Option<Box<…>>` |
| 3 | c03 | `ObSQLSessionInfo::cur_exec_ctx_` | REMOVE_HOSTPTR | 确认 | 删字段，执行上下文在调用时传入；26 处 guard 和读者由来源表决定（AB1-095） |
| 4 | c03 | `ObExecContext::my_session_` | OPTION_ARC | 确认 | `Option<Arc<ObSQLSessionInfo>>` |
| 5 | c04 | `ObEvalCtx::exec_ctx_` | REF_Q | 确认 | 每次调用构造的 `&mut dyn ObExecContextVirt` |
| 6 | c04 | `ObColumnRefRawExpr::column_name_` | OBSTR_Q | 确认 | `ObStr<'q>`，经非 const getter 的两个写者改用 `_mut` |
| 7 | c05 | `ObResultSet` | NOT_SELF_ARENA | 确认 | 删掉没用的 `inner_mem_pool_`；生命周期随 B6 机械更新（AB1-111） |
| 8 | c05 | `ObSPIResultSet` | OWNED_CHUNKS | 确认 | 不用 bump arena，内层结果集 `Option<Box<ObResultSet>>`，只要求 `Send` |
| 9 | c07 | `ObTransCtx::lock_`（CtxLock） | **UNKNOWN** | 未定 | 结构已定（带解锁钩子的 guard）；三把 latch 怎么分数据见 B5 |
| 10 | c07 | `ObLSMeta::rw_lock_` | GUARDS_FIELDS | 确认 | 5 个字段进锁；3 个锁外读的单字标量改原子量（`FIXED(port)`） |
| 11 | c08 | `ObTransCallbackMgr::callback_lists_` | ONCELOCK | 确认 | `OnceLock<Box<[ObTxCallbackList]>>` |
| 12 | c08 | `ObService::stopped_` | RELAXED_STOP_FLAG | 确认 | `AtomicBool`，Relaxed |
| 13 | c10 | `eval_enumset` 写共享计划 | PLAN_WRITE_EXPLICIT_ARG | 确认 | 替换参数显式传入，`FIXED(port)` |
| 14 | c11 | 转换器里的局部工厂 | SCOPE_STMT_QA | 确认 | `qa.scope`；记下 C++ 局部工厂没有 query_ctx 这一差异（AB1-089） |
| 15 | c11 | `process_with_tmp_context` | SCOPE_REQUEST | 确认 | 块内新建 `MemCtx`，显式传给 `do_process` |
| 16 | c12 | 子查询取正在执行的 SPF 迭代器 | EXEC_CTX_KIT_LOOKUP | 确认 | `ObExecContext` 按 SpecId 索引的表 |
| 17 | c18 | `casedn` 改解析树字符串 | COPY_MODIFY_REPOINT | 确认 | 拷进解析 arena，经 `ObStringWriter` 改写，再改指 |
| 18 | c18 | PS 缓存里给参数节点加负号 | COPY_MODIFY_REPOINT | 确认 | `OwnedParseTree::new_with`；拷贝路径用 `try_clone()` |
| 19 | c19 | `ObLockWaitNode` | **UNKNOWN** | 未定 | 请求归谁所有见 B7 |
| 20 | c21 | `replace_questionmarks` | COPY_INTO_PARSE_ARENA | 确认 | 参数里新增解析 arena；`FIXED(port)` 修掉 C++ 没更新长度和指向轮转缓冲两处（这个函数没有调用者，另进 c16） |

- **材料**在 `migration/inventory/review/`：提议、两轮审查、修订稿。行在各类别的分片 `migration/inventory.<类别>.tsv` 里。
- **前 20 行的实测成本**：提议约 172 万 token，两名审查者两轮共约 310 万，合计约 480 万，每行约 24 万。这 20 行是挑出来最难的，见第 6 节。

## 3. 1d：基础设施 API 文档和四份审查

9 份 API 文档在 `migration/infra/api/`，共 15,599 行，按 `migration/infra/README.md` 的 8 节结构写成：

| 文档 | 行数 | 内容 |
|---|---|---|
| 01 mem | 1,427 | arena、`MemCtx`、11049 的算法逐行照抄、全局分配器直通 jemalloc |
| 02 sync | 1,581 | 移植 ObLatch 语义，以及各种锁、带钩子的 guard、`inc_update` |
| 03 thread-signal | 1,634 | 线程、定时器（cancel/wait 语义照抄）、栈、信号、`main` 的开头 |
| 04 error-log-fmt | 1,905 | 错误码生成器与 `gen_errno.pl` 逐字节一致、`ob_printf!`、日志、TO_STRING |
| 05 containers | 2,373 | ObIArray、ObHashMap 移植、并发容器封装、队列 |
| 06 string-num-sort-serial | 1,331 | 字符串、数值解析、libc++ 19.1.2 的 introsort 和堆、unis 序列化、POD 编解码 |
| 07 sql-ir-frame-datum | 1,642 | `DataRef`/`ObDatumView`、存储表、QueryArena 和墓碑、ExprOp/OpState |
| 08 storage-core-kvcache | 1,393 | MVCC、keybtree、回调 pin、FIFO arena、palf 组缓冲、KV cache（含退休条件的修正） |
| 09 parser-sys-generators | 2,235 | 解析器 crate、-sys 和 shim、生成器 |

四份对抗审查在 `migration/infra/review/`，每份只查一类错误：

| 审查 | 高 | 中 | 低 | 最重要的 |
|---|---|---|---|---|
| 跨模块一致性 | 4 | 17 | 10 | crate 成环（-sys 依赖 oblib）；1d 的签名用到第 3 步才有的类型；约 150 个单元的处置不一致；三套时钟 |
| 健全性 | 1 | 9 | 6 | `Latch<Arena>` 能在分配仍存活时 reset（用 rustc 1.98.1 的探针证实） |
| 行为一致性 | 2 | 5 | 18 | `eval_args` 把 IFNULL/COALESCE/AND/BETWEEN 的短路求值改成全部求值，会让原本能执行的查询报错（已在原版上复现）；系统变量要先滤掉 57 个 placeholder |
| 并发与性能 | 1 | 5 | 7 | 在锁内 upgrade 得到的 `Arc` 成为最后一份时，析构函数又去取同一把锁，导致自锁 |

一致性审查顺带把 API 作者排队的 77 条修订逐条核对过：59 条成立、6 条部分成立、0 条被推翻。

行为一致性审查抽查了 208 条 file:line 引用：179 条正确、17 条部分正确、12 条错误。决定用户可见字节的部分大多正确，而且都有金标准测试：错误码表、哈希表和排序的顺序、TO_STRING、序列化、datum 布局。

**B-01 的源头是设计文档本身**：v0.2 的 0.6 节规定"一次取多个参数用 `eval_args`"。批次 01 的 AB1-008 把它收窄到 C++ 无条件求值全部参数的地方，并由 1c 给 306 处调用点逐个打标签。

## 4. 修订批次 01

`migration/amendments/batch-01.tsv`（112 行）和 `batch-01.md`（可读版）。来源全部去重合并：
- infra 修订 77 条；
- 四份审查的发现和缺失规则；
- inventory 修订 10 条；
- 前 20 行的提议、审查、修订稿。

| 类别 | 行 | 其中要你决定 |
|---|---|---|
| 决定 | 7 | 7 |
| 事实更正 | 12 | |
| 规则修改 | 47 | |
| 新规则 | 9 | |
| 命名 | 3 | |
| 只改 API 文档 | 16 | |
| 流程 | 17 | |
| 裁判改动 | 1 | |

其中 83 行改 RULEBOOK，46 行改动翻译者照着写的规则。

**要你决定的 7 项**（都推荐 A）：

| # | 问题 | A（推荐） | 其他选项 |
|---|---|---|---|
| B1 | 1d 的签名用到第 3 步才翻译的类型（ObObjType、ObNumber、时钟等） | 新增处置 `pretranslate:<模块>`，冻结前出"1d 闭包表"、逐项定负责文档，时钟归 03；SCN、ObTransID、ObTxSEQ、LSN、ObDmlFlag 下移到 oblib，由 08 写（约 1,100 行） | B：1d 先用原始整数，以后回改 API |
| B2 | 内存计账的口径：它决定落盘点，落盘又决定并列行的顺序 | 能对齐的照 C++ 字节数对齐，余下进 A.4 `mem-accounting`，D5 旁加注 | B：不对齐，全部进 A.4 |
| B3 | ObString 的 NULL 与空串 | 默认把 NULL 当空串；需要区分的、会进 `%s` 或 SQL 拼接的，由清单标成 `Option` | B：`ObStr` 一律用 `Option`（约 1.7 万处） |
| B4 | 0.8 的内存探测 | 删掉，ObMemoryDump 只留门面 | B：移植按地址的内存转储 |
| B5 | CtxLock 三把 latch 怎么分数据（前 20 行第 9 行） | 逐字段审计（约 32 个字段），审计后再定结构 | B：不审计，所有数据只放进 ctx_lock_，会多出 C++ 里没有的加锁 |
| B6 | ObResultSet 的生命周期类别（第 8 行） | 一律拥有型，第 7 行随之机械更新 | B：拆成请求作用域和拥有型两个类型 |
| B7 | MySQL 请求 `ObRequest` 归谁所有（第 19 行） | 照 §2.7 的 Box 移动：会话借出、收回请求 | B：`Arc` 加锁，请求的可变字段全部改内部可变；C：请求留在会话里，容器只存会话 id |

## 5. 新发现的疑似 C++ bug

移植时都照规则处理：会构成未定义行为的标 `FIXED(port)`，其余复刻为 `BUG(port)`。

| 来源 | 内容 | 移植时 |
|---|---|---|
| 设计复核 C06、API 08 | KV cache 的 `ObKVCacheHazardSlot::retire` 不比较 `min_version` 就释放节点，上游 OceanBase 有这个比较；疑似释放后使用 | 补上比较，`FIXED(port)`；回归测试要能在去掉比较的变体上失败 |
| 1c c23 | 先包含 objit 那份 `ObItemType` 的 23 个编译单元里，有 19 个用到 `T_MAX`；它在这些单元里是 4920，在其他单元是 4921，C++ 的定义在编译单元之间不一致 | 生成器以 query 那份为准，不一致就报错 |
| API 08 §8.2 | B1 撤销链接后复用批次槽；B2 紧凑节点不初始化两个字段；B3 服务没在运行时 `remove_callback_for_uncommited_txn` 仍返回成功；B4 KV 覆盖时不减旧块的计数；B6 aarch64 上组缓冲 `read_data` 的双重检查没有保证 | 补进清单第 17 类，逐个定是 BUG 还是 FIXED |
| API 03 | 9 处 `FIXED(port)`、4 处 `BUG(port)`（线程、定时器、信号） | 见 03 §3.16 |
| 行为一致性审查 | macOS 上解析器生成脚本里的 `sed` 补丁在基线构建中其实没有生效（BSD sed 拒绝执行，脚本又忽略了错误） | 不复刻这个补丁，以基线实际产物为准 |

## 6. 扇出：规模、成本和节奏（新问题）

**规模**：
- 交给 agent 判定的条目，按现有扫描约 3.8 万个。其中类型表里候选的生命周期类别约 5,250 个，seam 2,693 个。D15 估的是 2.2 万个。
- 有几类可以先用脚本规则消化：
  - c02 的 `forwards` 2,608 个，可以沿被调用方的结论传播；
  - c09 的 `pod-view` 1,841 个；
  - c14 的动态初始化对象 724 个，基本是机械处理。
- 消化之后约 2.5 万个（估计）。

**成本**：
- 前 20 行是挑出来最难的，每行约 24 万 token（提议加两名审查者两轮）。普通条目应该便宜 3–5 倍（估计）。
- 照现在的模型计划（全部 Opus、max effort、两名审查者），2.5 万个条目约需 1.3×10⁹–6×10⁹ token。

**节奏的硬约束是账户的用量上限**：
- 2026-10-11，大约 11 个并行的 Opus max-effort agent 跑了两小时左右，就碰到会话上限（各 agent 报告的用量合计约 1,300 万 token，估计），直到下一个时段才恢复。
- 照这个速度，扇出要 100–450 个用量时段，日历时间是数周到数月。

**选项**：

| 选项 | 做法 | token（估计） | 日历时间（估计） |
|---|---|---|---|
| **R-A（推荐）** | 先补脚本规则（1–2 天）；常规类别由 Sonnet 当提议者、每批 20 个共享上下文，配一名 Opus 审查者；高风险类别（c05、c07、c08、c19、c22、seam、类型表里的共享与生命周期）照旧用 Opus 提议、两名审查者；effort 全部 max | 3×10⁸–6×10⁸ | 2–4 周 |
| R-B | 照现在的模型计划，全部 Opus、两名审查者 | 1.3×10⁹–6×10⁹ | 1–3 个月 |
| R-C | 缩小范围（D15 的选项 C）：只做影响健全性的类别，其余按 §5.3 的默认译法加 `TODO(port)` | 2×10⁸–4×10⁸ | 1–2 周，代价是翻译阶段留下更多 TODO |

R-A 改动了你在可行性关口定的模型计划（清单原定用 Opus），所以需要你确认。kit 的指引是："怀疑型审查者用最大或中档模型，视缺口的复杂度而定。"

## 7. 偏差日志（待确认，已写入 RULEBOOK §7）

- **DEV-017**：2026-10-11 约 09:40Z，账户的用量上限中断了 7 个 agent。次日由你回复"继续"后，在保留上下文的情况下恢复。两份已完成的子审查结果原样存档，见 `migration/infra/review/fidelity-01-mem.md` 和 `fidelity-03-thread-signal.md`。
- **DEV-018**：两名审查者自行派生了子 agent（行为一致性的逐文档子审查、一致性审查的核验者），这不在模型计划的拓扑里，其中一部分在上限处丢失。恢复之后，一律禁止审查者派生子 agent。
- **DEV-019**：扫描共享库沿用了可行性阶段的剥离函数，它在 16 个文件里丢行号。改成保留行号的版本后全部重跑，键不变。

## 8. 成本

| 项 | 值 |
|---|---|
| 本阶段墙钟 | 约 290 分钟的活跃时间，横跨一次约 26 小时的暂停（用量上限，之后等你回复） |
| 子 agent | 22 个具名 agent，另有审查者派生的子 agent（至少 9 个，其中只有 2 个报告了用量） |
| token（各 agent 报告值的合计） | 19,387,214 |
| 模型 | 全部 `claude-opus-5-5`，max effort |

`migration/cost-log.tsv` 已追加一行。

## 9. 签字之后的下一步和时间估计

1. **应用批次 01**：
   - 改出 RULEBOOK v0.3；
   - 9 份 API 文档按批次和审查修订；
   - 用桩工作区跑 `cargo check`，核对跨文档的签名（1d 允许编译，DEV-014）；
   - 生成"1d 闭包表"。
   - 估计：墙钟半天到一天，agent 用量约 1,500 万–2,500 万 token，需要 2–3 个用量时段。
   - 之后是 **API 冻结关口**，你大约需要 20–30 分钟。
2. **补脚本规则，然后扇出**：按第 6 节选定的选项执行。扇出中途在每个类别完成时做机械回执（`wc -l`），全部完成后做联合审计，再到清单的最终关口。
3. **1d 实现**：在 API 冻结之后开始，用 Sonnet 实现、两名 Opus 审查者，与扇出并行，受同一个用量上限约束。

**你的专注时间**：读这份报告和批次 01 的第 1 节约 20–30 分钟；7 个决定和扇出选项约 10 分钟。
