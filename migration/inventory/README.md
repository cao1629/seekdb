# 缺口清单（阶段 1c）：磁盘上的约定

> 依据：kit `prompts/02-gap-inventory.md`，按设计文档 `migration/RULEBOOK.md` §5 扩展（D15：脚本先定论，agent 只处理剩下的）。基线 `7d907abfa`。
> 本文件定义目录、文件格式、键和状态。扫描脚本、提议者、怀疑型审查者、合并脚本都照这里读写；队列状态只看磁盘，不看对话。

## 1. 目录

| 路径 | 内容 | 谁写 |
|---|---|---|
| `migration/scripts/inventory/` | 扫描脚本和生成表的脚本，附 `README.md` 说明用法 | 1c 的脚本 agent |
| `migration/inventory/sweep/<类别>.tsv` | **完成的定义**：该类别要判定的全部站点，一行一个 | 扫描脚本 |
| `migration/inventory/sweep/counts.tsv` | 每个类别的站点数，由脚本写，是机械回执 | 扫描脚本 |
| `migration/inventory.<类别>.tsv` | 分片：判定结果，列与 kit 模板相同 | 提议者（每个 worker 只写自己的分片） |
| `migration/inventory/review/<批次>.<审查者>.tsv` | 怀疑型审查者的逐行裁定 | 审查者 |
| `migration/inventory.tsv` | 合并结果：按键去重、确定性排序 | 合并脚本（全部站点都有行之后） |
| `migration/design/units.tsv`、`types.tsv`、`seams.tsv`、`slots.tsv`、`overloads.tsv`、`symbols.tsv`、`qa_signatures.tsv` | 设计文档 §5.1 的生成表 | 脚本，部分列由 agent 补 |
| `build_release/inventory-ast/` | AST 抽取的中间结果，不入库，可由脚本重建 | `ast_extract.py` |

## 2. 格式

**扫描清单** `sweep/<类别>.tsv`，制表符分隔，首行是表头：

```
file	symbol	source_construct	line	pretag
```

- `file`：仓库相对路径。`line`：1 起的行号。
- `symbol`：站点的稳定名字，按类别约定（见第 4 节），例如 `ObTxCtx::lock_`、`ObSQLUtils::foo(…)#out:3`。
- `source_construct`：C++ 构造的简短描述，例如 `raw pointer member`、`T *& out-param`。
- `pretag`：脚本给出的提示（可空），例如 `callee-allocates:arena`；它不是结论。
- **键** = `(file, symbol, source_construct)`，在一个类别内唯一；扫描脚本必须检查唯一性，重复就报错退出。

**分片** `inventory.<类别>.tsv`：kit 模板的 7 列，表头原样：

```
file	symbol	source_construct	classification	target_translation	evidence	status
```

- 键同上，必须与扫描清单里的一行对应。
- `classification`：本类别的分类码（第 4 节）；判不了时写 `UNKNOWN`。
- `target_translation`：设计文档规定的 Rust 写法，写到实现者能照抄的程度；UNKNOWN 行写最保守的表示加 `TODO(port): <问题>`（RULEBOOK §5.3）。
- `evidence`：数据流证据，写 file:line，至少覆盖"在哪里创建、在哪里修改、从哪里逃逸"；"共用哨兵值"的守卫行加标签 `allocation-guard` 或 `precondition-guard`（kit 模板的约定）。
- `status`：`proposed` → `confirmed` / `revised` / `unknown`。两名审查者都确认才是 `confirmed`；被有证据地驳回则修改一次、重新提交（`revised`），仍有争议就写成 `unknown`。

**审查裁定** `review/<批次>.<审查者>.tsv`：

```
file	symbol	source_construct	verdict	cited_line	note
```

- `verdict`：`confirm` / `refute` / `evidence-defect`（结论对、证据有误）。
- `cited_line`：支撑裁定的 file:line，必填。

## 3. 规则

- 扫描只靠脚本和 grep，不靠判断；同一份输入跑两次，输出逐字节相同。
- 计数以 `wc -l` 和 `counts.tsv` 为准，不写叙述性的总数。
- 并行的 worker 不争同一个文件：每个 worker 只写自己的分片或自己的审查文件。
- 合并时按键去重、确定性排序，报告合并后的 `wc -l`。
- 进度不变式：worker 运行期间，分片行数必须增长；三分钟没有增长就视为失败，从磁盘恢复已写的行，并报告停滞。
- 设计文档是只读的。审查中发现规则错了，写进 `migration/inventory/amendments.tsv` 排队给人，不改 RULEBOOK。

## 4. 类别

编号与设计文档 §5.2 相同。"脚本"类别不进 agent，由脚本直接生成结论（或由规则整体覆盖，不出行）。

| # | 文件名中的类别 | 内容 | 定论方式 |
|---|---|---|---|
| 1 | —（不出行） | 指针的可空性 | 规则整体覆盖（§2.1：C++ 指针一律 `Option`，保留检查）；`counts.tsv` 只记 `OB_ISNULL` 的数量备查 |
| 2 | `c02-ptr-params` | `T *&` 输出参数；转移所有权的指针参数和返回值 | 脚本预标，agent 判余下的 |
| 3 | `c03-ptr-members` | 裸指针成员 | agent |
| 4 | `c04-borrow-members` | 引用成员、借用分配器的成员、`ObString` 成员 | agent |
| 5 | `c05-self-arena` | 自持 arena 的类；"默认指向自带分配器"的成员 | agent |
| 6 | `c06-int-returns` | 返回 `int` 的函数：错误码还是值 | 脚本预标，agent 判余下的 |
| 7 | `c07-locks` | 每个带锁成员的类：锁保护哪些字段；返回锁引用的访问函数；重入点 | agent（按类） |
| 8 | `c08-atomics` | 被 `ATOMIC_*` 访问的字段的角色；发布用的普通访问；C++ 里的数据竞争字段 | 脚本预标，agent 判余下的 |
| 9 | `c09-layout` | union、位域、`reinterpret_cast`、packed、柔性数组 | 脚本预标，agent 判余下的 |
| 10 | `c10-plan-writes` | 运行期写共享计划的点 | agent |
| 11 | `c11-scopes` | 临时作用域；`ObArenaAllocator` 成员和 `OB_NEWx` pool 的层级 | agent |
| 12 | `c12-side-channels` | 跨算子旁路；被别的算子读写的状态 | agent |
| 13 | `c13-weak` | weak 符号和 `.cpp` 里自写的 extern 原型 | 脚本 |
| 14 | `c14-init` | 对象池；构造时有副作用的静态初始化；动态初始化顺序 | 脚本预标，agent 判余下的 |
| 15 | `c15-overloads` | 超过 20 个重载的集合 | 脚本生成 `overloads.tsv`，agent 复核 |
| 16 | `c16-dead` | 待确认的死代码 | agent |
| 17 | `c17-bugs` | 00b 的 15 条疑似 bug 和 KV cache 的退休条件 | agent |
| 18 | `c18-inplace` | 就地改写借来的字节 | 脚本预标，agent 判余下的 |
| 19 | `c19-multi-owner` | 同时挂在多个容器里的对象；宿主回指；定时任务的宿主 | agent |
| 20 | `c20-vinherit-voidptr` | 虚继承；`void *` 成员 | agent |
| 21 | `c21-parse-tree` | 解析树：`const_cast<ParseNode`、栈上节点、非标量写 | agent |
| 22 | `c22-session` | 会话字段的访问分组 | agent |
| 23 | `c23-itemtype` | `ObItemType` 两份定义的差异和让 objit 那份生效的编译单元 | 脚本预标，agent 判余下的 |

各类别的 `symbol` 约定和分类码，由对应的扫描脚本写在 `migration/scripts/inventory/README.md` 里。
