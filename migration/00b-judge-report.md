# 00b 裁判搭建报告

> 本文是 code-migration kit `prompts/00b-judge-setup.md` 的产出。
>
> - **基线**：上游 oceanbase/seekdb master `7d907abfa`，已冻结。
> - **原版二进制**：在本机构建，macOS arm64，`MacOSX15.sdk`，冷构建 7 分 44 秒。
> - **模型**：全部子代理都是 Opus 5.5、max effort（可行性报告第 5 节的分配）。
> - **状态**：**等你在 00b 关口签字。** 签字之前不开始第 1 步。裁判的使用说明在 `migration/judge/README.md`。

## 结论

裁判已经建好，并通过了 kit 要求的两项验证：

1. **原版全部通过**：两次完整运行都是 **294/294**，即 287 个 mysqltest 用例加 7 个生命周期场景。两次是同时跑的，每次 4 个并行 worker，结果完全一致。另外在 121 个核心用例上，原版在四种协议下也全部通过：普通、预处理语句（排除了 3 个，原因写明）、压缩、TLS。
2. **能从公开接口观察到的注入缺陷全部被抓到**：一共注入 10 个缺陷，每个单独构建，单独跑全部 294 个用例。
   - 7 个针对具体行为的缺陷（M1–M4、M5b、M6c、M6d）每个都至少被 1 个用例抓到。其中 M5b（二进制协议参数）和 M6c、M6d（参数修改与持久化）只有新加的生命周期场景抓得到，CI 原有用例都覆盖不到。
   - M5 没被抓到。原因已查明：它是等价变异，接收端的 C++ 代码抵消了这处改动，从公开接口看不出任何差别。
   - M6、M6b 让实例在启动阶段就失败，所有用例跟着失败，这说明不了裁判能抓住具体缺陷，所以又补了只影响一个参数的 M6c、M6d。
   - 详见第 5.2 节。

需要你裁定的事项见第 6 节：灰色地带规则、契约边界、4 个隔离用例，以及 15 条疑似原版 bug。

## 1. 裁判是什么

裁判只通过**公开接口**评估一个 seekdb 二进制：进程启动参数（`--base-dir`/`--port`/`--parameter`/`--nodaemon`）和 MySQL 协议。原版和移植版用同一套输入，输出与从原版录制的期望输出逐字比对。

| 组成 | 规模 | 覆盖内容 |
|---|---|---|
| mysqltest 用例 | 287 个可运行用例，另有 4 个隔离 | CI 原有用例，以及改写和新增的场景（见第 2–4 节） |
| 生命周期场景 | 7 个 | 已提交的数据经 `kill -9` 后仍在，未提交的被回滚；正常重启后对象定义不变；启动参数、`ALTER SYSTEM SET`、`SET GLOBAL` 在重启后保留；FORK/MERGE 和 HNSW 索引经崩溃后不变；二进制协议参数绑定 |
| 协议变体 | 4 种 | 同一批用例分别走普通、预处理语句、压缩、TLS 协议；TLS 会为每次运行生成自签名证书 |

运行方式：默认**每个用例一个全新实例**，结果与执行顺序无关，也与只跑其中一部分无关；`--jobs N` 可并行；失败不重试。在这台机器上，一次完整运行（4 个 worker）大约 12 分钟。

## 2. 第 1 步：分类（基于新基线）

分类工具和结果都在 `migration/judge/census/`，清单是 `out/mt_ci_*.txt`，计数见 `out/summary.txt`。

| 资产 | 用例数 | 公开接口可直接用 | 依赖内部实现 |
|---|---|---|---|
| CI 跑的 mysqltest | 273 | 干净 89 + 脆弱 32 = 121 | 152 |
| obtest | 500 | 1（测试框架在公开仓库里跑不起来） | 499 |
| C++ 单元测试 | 0（公开仓库已删除） | — | — |

## 3. 第 2 步：改写成可移植的形式

7 个改写 agent 按套件分批工作，规则写在 `migration/judge/README.md` 的“改写规则”一节，分 A 类、灰色地带、B 类。之后两名对抗审查者在各自独立的上下文里审查，两人负责的用例互不重叠，审查结论在 `migration/judge/suites/mysqltest/reviews/`。

**152 个依赖内部实现的用例最终去向**：

| 去向 | 数量 | 说明 |
|---|---|---|
| `portable` | 29 | 改写者审查后确认：分类器命中的都是 A 类或灰色地带构造（OB 错误码、`SHOW CREATE` 里的存储选项、公开的 `ob_` 变量等），原文件可直接用 |
| `rewritten` | 119 | 去掉 B 类构造，或换成观察同一事实的公开查询；每个用例在清单 `cases.tsv` 的说明列里写明去掉了什么、保留了什么、丢了哪些覆盖 |
| `quarantined` | 4 | 只能靠 debug_sync 或内部表才能制造的场景：`fork_table_ddl`、`fork_table_lock`、`fork_table_snapshot`、`fork_table_merge`（在 BUILD_DATA 阶段强制做大合并）；说明里写了它们原本守护的行为 |

另外 2 个脆弱用例（`type_date.type_create_time`、`type_modify_time`）只修了 sleep 环境问题，改完后录出的输出与原 CI 期望输出逐字节相同。

**改写中丢掉的覆盖**，大头有三类，逐条见清单说明：
- 数百段 EXPLAIN 计划文本，各批次的数量见各自的说明分片，比如 SQL 引擎批次约 300 段、fts 71 处、vector 60 处。凡是只出现在 EXPLAIN 里、原测试从没真正执行过的语句，都改成实际执行并断言结果。
- 内部表和隐藏辅助表的转储，比如全文索引的词频、向量索引的辅助表行数。
- 靠 debug_sync 强制出来的并发时序。

**对抗审查**：

| | 审查用例数 | 高 | 中 | 低 | 处理 |
|---|---|---|---|---|---|
| 审查者 A（geometry / fork / fts） | 57 | 1 | 6 | 6 | 全部处理（见下） |
| 审查者 B（顶层 / vector / SQL / misc） | 64 | 0 | 4 | 5 | 中级全部处理；低级部分留待关口 |

已修复的问题，修完都在原版上录一次、在全新实例上比对一次：
- 2 个 vector 用例的输出带着随时间变化的隐藏列编号，改用 `SHOW INDEX`。
- `fts_index.basic_dml`：从“只看命中了哪些行”补回到 305 个带相关度的探针；相关度用 BM25 独立重算，全部吻合。
- `fts_index.drop_index`：每一步之后加 MATCH，验证剩下的全文索引仍然可用。
- geometry 3 个用例补上确定的排序，遮挡范围缩小。
- `rebuild_vector_index` 恢复了 3 处公开的 rebuild 调用；`vector_index_ivfflat_basic` 恢复了 4 处 5083 报错检查。
- `fork_table_merge` 改为隔离；fork 批次的说明补全了重试写法和不再有保证的时序。

## 4. 新增覆盖

可行性报告里列出的覆盖空白，现在补上了：

| 新增 | 内容 |
|---|---|
| 18 个场景（`suites/mysqltest/scenarios/`） | ACID（读现象、写冲突、行锁）、FORK DATABASE / DIFF TABLE / MERGE TABLE 各种策略、向量（精确距离、HNSW 与 IVF 各变体、稀疏向量）、全文（5 种分词器、各种匹配模式）、混合检索（`DBMS_HYBRID_SEARCH`，包括 RRF 融合、query DSL、ES 模式）。每个都在 3 个全新实例上结果一致 |
| 7 个生命周期场景（`suites/lifecycle/`） | 见第 1 节 |
| 二进制协议探针（`runner/stmt_params.c`） | `mysqltest --ps-protocol` 从不绑定参数。这个探针绑定了 14 种类型的极值参数，并取回二进制结果 |
| TLS、压缩、预处理语句协议 | 可行性评估时，这三种协议在测试里覆盖为零 |

## 5. 第 3 步：验证

### 5.1 原版全部通过

| 运行 | mysqltest | 生命周期 | 时间（UTC） |
|---|---|---|---|
| orig-1 | 287/287 | 7/7 | 2026-10-10，4 个 worker |
| orig-2 | 287/287 | 7/7 | 与 orig-1 同时运行 |

此前在 247 个用例上的一次提前运行也全部通过，只有 3 个当时正在修改的用例例外，它们后来已重录。录制本身也是在全新实例上完成的。所以每个改写用例和新增场景至少有“一次录制 + 两次比对”，彼此一致。

**协议变体**（121 个核心用例，即干净的、脆弱的，加 2 个修过环境问题的用例）：

| 协议 | 结果 | 说明 |
|---|---|---|
| 普通 | 121/121 | — |
| 预处理语句 | 118/118 | 排除 3 个，原因见 `protocol/ps/exclude.tsv`；另有 6 个因为 mysqltest 渲染方式不同，单独录了一份期望输出，去掉格式差异后与普通协议的值完全一致 |
| 压缩 | 121/121 | — |
| TLS | 121/121 | — |

### 5.2 注入缺陷都被抓到

**做法**：
- 每个缺陷是一个补丁，放在 `migration/judge/validation/mutations/`。
- `validate.py build` 把补丁打到原版源码上，在单独的构建目录里增量构建，构建完立即撤回补丁。
- `validate.py run` 用这个二进制跑全部 294 个用例：4 个并行 worker，每个用例一个全新实例。
- 下表由 `validate.py report` 生成，M6b 一行是手工补的。“抓到它的用例数”只计在两次原版运行中都通过、在缺陷版本上失败的用例。

| 缺陷 | 注入位置 | 抓到它的用例数 | 其中生命周期场景 | 例子 |
|---|---|---|---|---|
| M1 (limit-off-by-one) | `src/sql/engine/basic/ob_limit_op.cpp` | 16 / 294 | 0 | `executor.basic`, `fork_table.fork_table_vector`, `fts_index.partitioned_simple_query`, `global_index.global_index_select`, `limit`, `scenario.hybrid.search_fusion` |
| M2 (swallow-duplicate-key) | `src/storage/ls/ob_ls_tablet_service.cpp` | 16 / 294 | 1 | `lifecycle:restart_graceful`, `bulk_insert`, `delete.delete_from_mysql`, `duplicate_key`, `fork_table.fork_table_with_index`, `minitest` |
| M3 (month-zero-padding) | `src/oblib/common/timezone/ob_time_convert.cpp` | 38 / 294 | 2 | `lifecycle:binary_protocol`, `lifecycle:restart_graceful`, `add`, `datatype.div`, `datatype.minus`, `datatype.replace` |
| M4 (l2-distance-squared) | `src/sql/engine/expr/ob_expr_vector.cpp` | 11 / 294 | 1 | `lifecycle:vector_restart`, `scenario.hybrid.es_mode_partitions`, `scenario.hybrid.query_dsl`, `scenario.hybrid.search_fusion`, `scenario.vector.exact_distance`, `scenario.vector.hnsw_topk` |
| M5 (tiny-param-sign) | `rust/sql-nio/src/stmt_execute.rs` | 0 / 294 | 0 | 等价变异，见下 |
| M5b (datetime-param-drops-microseconds) | `rust/sql-nio/src/stmt_execute.rs` | 1 / 294 | 1 | `lifecycle:binary_protocol` |
| M6 (config-not-persisted) | `rust/config/src/lib.rs` | 294 / 294 | 7 | 全部在启动阶段失败 |
| M6b (alter-system-not-persisted) | `rust/config/src/ffi.rs` | 已中止：运行过的 249 个全部在启动阶段失败 | — | — |
| M6c (one-parameter-alter-ignored) | `rust/config/src/ffi.rs` | 1 / 294 | 1 | `lifecycle:param_persistence` |
| M6d (one-parameter-lost-on-restart) | `rust/config/src/ffi.rs` | 1 / 294 | 1 | `lifecycle:param_persistence` |

**逐个说明**：
- **M1–M4** 对应 kit 举例的几类缺陷：
  - M1 是边界比较差一，`LIMIT n` 只返回 n−1 行；
  - M2 去掉一条报错路径，普通 INSERT 遇到主键冲突不再报错；
  - M3 改了输出格式，1–9 月不补零；
  - M4 改了计算结果，`l2_distance` 返回距离的平方。
- **M5 是等价变异，所以换成 M5b**：
  - M5 让 Rust 协议层在解码有符号 TINY 参数时不做符号扩展。但接收端 `src/observer/mysql/obmp_stmt_execute.cpp:1090-1092` 对有符号 TINY 只取低 8 位（`static_cast<int8_t>`），扩展与否得到的值都一样，从公开接口观察不到差别。
  - 于是在同一个文件里另选了一处观察得到的缺陷 M5b：DATETIME/TIMESTAMP 参数丢掉微秒。只有二进制协议探针（`lifecycle:binary_protocol`）抓得到它，因为 `mysqltest --ps-protocol` 从不绑定参数。
- **M6、M6b 让实例起不来**：
  - M6 让配置文件的每次写入都静默丢失：写好的临时文件被删掉，没有改名去替换原文件。实例启动时本身就要写配置文件，所以 287 个 mysqltest 用例都在等实例就绪（wait-ready）时失败，7 个生命周期场景都在 `start` 一步失败。
  - M6b 让运行时的 `ALTER SYSTEM SET` 全部静默失效。seekdb 初始化时自己会执行一条 `ALTER SYSTEM SET`，再回读核对，见 `src/rootserver/ob_local_management_service.cpp` 的 `set_config_after_bootstrap_`。核对等不到新值就会超时（-4012），初始化失败。已运行的 249 个用例全部在启动阶段失败。每次失败都要等实例退出或 600 秒超时，跑了 2 小时 49 分后中止，没有保留结果文件。
  - 这两个缺陷“被抓到”，只说明裁判能发现起不来的实例，所以又做了两个只影响一个参数（`trace_log_slow_query_watermark`）的版本：
- **M6c**：对这个参数执行 `ALTER SYSTEM SET` 之后，配置文件被恢复成旧内容。seekdb 改完参数会立刻从文件重新加载，所以语句返回成功，却没有任何效果。`lifecycle:param_persistence` 在改完立刻回读这一步就抓到了：期望 7s，实际 3s。
- **M6d**：修改在运行时正常生效，但重新加载之后，配置文件被恢复成旧内容，所以重启后修改丢失。`lifecycle:param_persistence` 改完立刻回读的一步通过；重启后回读的一步失败，读到的是启动参数 3s，而不是 7s。这说明裁判能单独抓住“只丢持久化”的缺陷。

## 6. 需要你在关口裁定的事项

**A. 灰色地带的规则**：目前一律默认保留，所以裁判偏严，移植版必须支持这些构造。

1. **空间参考系数据的装载**：所有 39 个 geometry 用例都通过 `REPLACE INTO oceanbase.__all_spatial_reference_systems` 写内部表装载数据，然后等 30 秒。没有公开的 `CREATE SPATIAL REFERENCE SYSTEM` 语句，官方的导入脚本也是这样写的。
2. **`_st_*` 函数**：出现在 14 个 geometry 用例里；其中 4 个用例只测这些函数，如果判为 B 类就要隔离。
3. **管理命令**：`ALTER SYSTEM MAJOR/MINOR FREEZE`、`FLUSH PLAN CACHE`、`FLUSH KVCACHE`，以及对公开参数的 `ALTER SYSTEM SET`（`vector_memory_limit`、`merger_check_interval`、`ob_compaction_schedule_interval`、`weak_read_version_refresh_interval`、`debug_sync_timeout` 等）。
4. **字典视图**：
   - `DBA_OB_MAJOR_COMPACTION` 用于轮询等待；
   - `DBA_OB_AI_MODELS` 的输出参与比对；
   - Oracle 风格的 `DBA_SCHEDULER_*`、`DBA_TAB_STATISTICS`、`DBA_TAB_MODIFICATIONS`、`DBA_TAB_COL_STATISTICS`、`DBA_PART_*`、`DBA_IND_*`；
   - 统计信息相关视图里有些值取决于引擎的估算算法。
5. **公开的 `ob_*` 变量**（`ob_query_timeout`、`ob_trx_timeout`、`ob_trx_lock_timeout`、`ob_enable_plan_cache`、`ob_enable_transformation`、`ob_enable_index_direct_select` 等）。另外 `minitest` 用到 `SHOW VARIABLES LIKE 'ob%'`，会把全部 `ob_*` 变量及其默认值都变成契约。
6. **PL 包**：`dbms_vector`、`DBMS_AI_SERVICE`、`DBMS_STATS`、`DBMS_HYBRID_SEARCH`。
7. **`root@sys` 登录**，以及在 `oceanbase` 库里建用户表。

**B. 契约边界**

8. **`information_schema` 用例会列出全部内部表**（`oceanbase.__all_*`），等于把内部表目录写进了契约。
9. **`expr.collation_expr` 的 `cmp_meta()`** 输出的是 C++ 内部结构。
10. **只断言“执行成功”的 EXPLAIN**（`--disable_result_log`）：`explain` 用例里有 19 处，`join_*` 里有若干处，而 `group_by_2` 已经删掉了。需要定一个统一的规则。
11. **没有 ORDER BY、依赖执行计划的行序**：`executor.basic` 里约 80 个连接、`subquery_sj_firstmatch`、`group_by_basic`。保留为 A 类，还是加 `--sorted_result`？
12. **全精度相关度**：原有的全文检索期望输出固化了引擎估算的平均文档长度，以及浮点数最后一位的计算顺序。要不要统一四舍五入？
13. **`tokenize()` 的输出顺序**：同一份代码下稳定，但顺序由实现决定。新增场景里已经把它单独放进 `fts.tokenize_raw_order`。
14. **时序不再有保证的用例**：`fork_table_chain`、`fork_table_cow`、`fork_table_with_index`、`rebuild_vector_index`（去掉 debug_sync 后，某些交错只是大概率发生），以及 `fork_table_vector` 的“遇到 4179 就重试直到成功”。是保留，还是隔离？
15. **`SHOW CREATE TABLE` 里的 OB 存储选项**（`BLOCK_SIZE`、`ORGANIZATION INDEX`、`COMPRESSION 'zstd_1.3.8'`、`TABLET_SIZE`）：现在按 A 类保留。

**C. 4 个隔离用例**：确认这样处理，或者给出其他做法（见第 3 节）。

**D. 疑似原版 bug**：都已按原版的实际行为录进期望输出，移植版要逐个复现（kit 的 BUG(port) 规则）。确认后，迁移完成时再统一处理。

| # | 现象 | 所在用例 |
|---|---|---|
| 1 | `MERGE TABLE` 报 7 行受影响，紧接着 `row_count()` 却是 0 | `scenario.fork.merge_strategies` |
| 2 | 没有合并基准时，`STRATEGY THEIRS` 会把只有当前方改过的行也撤销；删除在两个方向上都不会传播 | 同上 |
| 3 | `DIFF TABLE` 的 `__flag` 永远是 `'INSERT'` | `scenario.fork.diff_table` |
| 4 | `DIFF/MERGE` 的列定义不一致只报笼统的 4029 “Schema error”；表不存在报 1146 但不带表名；列检查先于权限检查，导致没权限时报的是 4029 而不是 1142 | `scenario.fork.merge_diff_errors` |
| 5 | `FORK DATABASE` 静默丢掉视图和跨库外键 | `scenario.fork.fork_database` |
| 6 | 可重复读下，`START TRANSACTION WITH CONSISTENT SNAPSHOT` 不在开始时取快照 | `scenario.acid.isolation_read_phenomena` |
| 7 | SERIALIZABLE 允许写偏斜 | `scenario.acid.write_conflicts` |
| 8 | `LOCK IN SHARE MODE` 实际是排他锁，第二个读者会报 1205 | `scenario.acid.row_locks` |
| 9 | BM25 的文档总数被冻结在缓存的执行计划里 | `scenario.fts.match_modes` |
| 10 | 布尔模式的检索词绕过了索引的分词器 | `scenario.fts.parser_indexes` |
| 11 | 在空表上建的 IVF_SQ8 索引不按距离排序 | `scenario.vector.ivf_topk` |
| 12 | 混合检索静默忽略大写的顶层键和未知的选项 | `scenario.hybrid.query_dsl`、`es_mode_partitions` |
| 13 | 预处理协议下读取 `int(255) zerofill` 列，客户端报截断（101），是服务端还是客户端的问题尚未定论 | `expr.func_length`（预处理协议下已排除） |
| 14 | `sparse_vector_index_vsag_query` 的近似检索结果与 CI 期望输出不同，但在本机每次运行都稳定；推测是录制 CI 期望输出的构建采用了不同的剪枝策略 | 该用例 |
| 15 | 死锁检测耗时在 2.5 到 122 秒之间随机；没有交付专门的死锁场景，已有的 `deadlock_detector.trans_deadlock_basic` 至今 4 次运行都通过 | — |

“`MERGE ... STRATEGY THEIRS` 没有合并对已有行的修改”这一条已经查明不是 bug：那一列用的是不区分大小写的排序规则，`'b'` 和 `'B'` 被视为相等，不构成冲突。

## 7. 环境问题与偏差

| ID | 内容 |
|---|---|
| ENV-1 | macOS 26 默认的 SDK 和依赖包里的 LLVM 17 不兼容，构建时要指定 `MacOSX15.sdk`。只改配置参数，不动源码 |
| ENV-2 | seekdb 守护化后 fork 出的子进程会退出，统一用 `--nodaemon`，由 `sdb.py` 负责放到后台 |
| ENV-3 | obclient 2.2.12 自带的 mysqltest 在 macOS 上 sleep 不准（`--real_sleep 1` 实测 0.42 秒），裁判自有文件里的等待一律改为 `--exec sleep N` |
| ENV-4 | CI 是所有用例共用一个实例，用例之间会互相污染（`information_schema`、`global_index_lookup_*` 就是这样失败的）。裁判改为每个用例一个全新实例 |
| DEV-004 | kit 写的是“两名审查者”。这里是两名审查者各审一半、互不重叠，所以每个改写用例只被审查过一次 |
| DEV-005 | 裁判是在 macOS 上建立和验证的，而 CI 跑在 Linux 上。Linux 上至少要再验证一次“原版全部通过”（参见第 6 节第 14 条疑似 bug） |
| DEV-006 | 协议变体只覆盖了 121 个核心用例，没有覆盖全部 287 个 |

## 8. 下一步

你签字后，进入第 1 步：
- 依赖图（`prompts/01`）；
- 设计文档，在重新设计模式下由它取代规则手册；需要你拍板的决定见可行性报告第 3 节；
- 缺口清单（`prompts/02`）。

第 1 步开始前，请先给第 6 节的 A、B、C 三组事项定下结论，否则裁判的契约边界不确定。

## 9. 成本

| 项目 | 数值 |
|---|---|
| 日历时间 | 2026-10-10 11:08Z（你签字进入 00b）到 17:30Z，共 382 分钟，包括等你装 Homebrew 的几分钟 |
| 子代理 | 10 个，都是 `claude-opus-5-5`、max effort：7 个改写者、1 个场景作者、2 个对抗审查者 |
| 子代理 token | 5,438,116，不含主会话，与第 0 步的统计口径一致 |
| 本机构建 | 原版冷构建 1 次（7 分 44 秒）；10 个缺陷版本在单独的构建目录里各增量构建 1 次 |
| 本机运行 | 完整运行 12 次：原版 2 次，缺陷版本 10 次（其中 M6b 中止）；另有录制、协议变体、确定性复跑 |

`migration/cost-log.tsv` 已追加一行：`0b	2026-10-10T17:30Z	382	5438116	10	claude-opus-5-5`。
