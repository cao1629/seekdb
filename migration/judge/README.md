# 迁移裁判（judge）

code-migration kit `prompts/00b-judge-setup.md` 的产物。裁判通过**公开接口**评估原始 C++ 版和 Rust 移植版，两边用同一套用例、同样的输入，机械地比对输出。公开接口指进程参数（`--base-dir`、`--port`、`--parameter`）和 MySQL 协议。第 6 步就靠它判定"迁完了"，整个迁移期间要一直保持可运行。

- **基线**：上游 oceanbase/seekdb master `7d907abfa`（2026-10-10 冻结）。
- **期望输出**：从原始版二进制录制，即 `.result` 文件。原始代码就是规格。

## 组成

| 路径 | 内容 |
|---|---|
| `runner/judge.py` | mysqltest 运行器。默认**每个用例一个全新实例**：起实例、只执行 `init_public.sql`、跑用例、失败不重试，结果写进 `judge_result.json`。主要参数：<br>• `--protocol plain/ps/compress/tls` 切换普通、预处理语句、压缩、TLS 协议；<br>• `--record` 从原始版录制期望输出；<br>• `--shared-instance` 退回 CI 那种所有用例共用一个实例的方式；<br>• `--case-timeout` 设单个用例的超时；<br>• `try` 子命令在全新实例上试跑任意一个 `.test`，供编写新用例时用 |
| `runner/lifecycle.py` | 进程生命周期场景运行器：启动、正常停止、`kill -9`、重启、常驻会话（用来持有未提交事务）、`exec:` 调用探针程序。脚本在 `suites/lifecycle/*.lifecycle`，期望记录在同目录的 `.result` |
| `runner/stmt_params.c` | 二进制协议探针，用 deps 自带的 libobclnt 编写：通过 `COM_STMT_PREPARE/EXECUTE` 绑定各类型参数，覆盖各整数类型的极值、DECIMAL、DOUBLE、DATETIME、DATE、负的 TIME、多字节字符串、二进制和 NULL，并取回二进制结果。`mysqltest --ps-protocol` 不绑定任何参数，覆盖不到这条路径 |
| `runner/sdb.py` | 实例启停脚本，复制自 `.github/script/seekdb/sdb.py` 并固定在裁判里。有两处增补：启动前把 `wallet/` 证书放好；`kill -9` 后清掉残留的 pid 文件 |
| `suites/mysqltest/cases.tsv` | 用例清单，唯一写入者是主会话。状态含义：<br>• `clean`：干净，原样使用；<br>• `portable`：分类器标为依赖内部实现，但改写者审查后确认命中的都是 A 类或灰色地带构造，原样使用；<br>• `fragile`：公开但脆弱，原样使用；<br>• `rewritten`：改写版；<br>• `scenario`：新增场景；<br>• `quarantined`：隔离，不跑，并记录它原本守护的行为 |
| `suites/mysqltest/init_public.sql` | 每次起实例后只执行这一份公开初始化：建 admin 用户、建 `test` 库、授权 |
| `suites/mysqltest/cases/` | 改写后的用例（`<用例名>.test`），以及从原始版录制的期望输出（`<用例名>.result`） |
| `suites/mysqltest/include/<批次>/` | 改写用例用到的改写版 include，按批次分开存放，避免并行改写时冲突 |
| `suites/mysqltest/protocol/<协议>/` | 某协议下原始版输出与普通协议的期望输出不同、但只是渲染差异时，在这里另存一份 `<用例名>.result`。原始版在该协议下本身就跑不通的用例，写进 `exclude.tsv` 并注明原因 |
| `suites/mysqltest/scenarios/` | 为覆盖空白新写的场景：混合检索、FORK/DIFF/MERGE、隔离级别、向量、全文 |
| `suites/mysqltest/rewrites/`、`reviews/` | 改写队列和说明分片；两名对抗审查者的结论 |
| `census/` | 普查和清单工具：<br>• `ci_enabled.py`、`classify.py`、`make_manifest.py`、`out/`：普查及其结果；<br>• `update_manifest.py`：合并分片、修改单行、刷新说明；<br>• `reclassify_unchanged.py`：没有实质改动的副本改为 `portable`；<br>• `missing_results.py`：列出还缺期望输出的用例；<br>• `server_sleep.py`：sleep 环境修复，见下 |
| `validation/` | 第 3 步验证：`mutations/*.patch` 是 10 个注入缺陷，`validate.py` 负责构建缺陷版本、运行、汇总和出报告表格，`results/<标签>/` 存放每个二进制的结果 |

**环境修复**：这些问题都出在这台 macOS 构建机上，修复本身不改变任何断言。
- **构建**：macOS 26 默认的 26.5 SDK 和依赖包里的 LLVM 17 不兼容，配置时加 `-DCMAKE_OSX_SYSROOT=…/MacOSX15.sdk -DCMAKE_OSX_DEPLOYMENT_TARGET=15.0`。
- **启动**：seekdb 自己做守护化时，fork 出的子进程会退出，所以统一加 `--nodaemon`，由 `sdb.py` 负责放到后台。
- **sleep**：obclient 2.2.12 自带的 mysqltest 睡眠时间偏短而且不稳定，`--real_sleep 1` 实测只有 0.42 秒。裁判自有文件里的等待一律改成 `--exec sleep N`。不用服务端 `sleep()`：一是单条语句有 10 秒超时，二是连接上有还没 `reap` 的 `send` 时不能再发语句。
- **TLS**：sql-nio 读取的证书是 `wallet/{ca,server-cert,server-key}.pem`（见 `src/oblib/lib/ob_define.h`）。

不施加的引擎专有初始化：`tools/deploy/init.sql` 和 `init_user.sql` 里的 `ob_query_timeout`、`recyclebin`、隐藏参数（`_nlj_batching_enabled`、`_enable_adaptive_compaction`、`_enable_var_assign_use_das`、`_enable_spf_batch_rescan`、`_max_px_workers_per_cpu`）、合并调度参数、12 个 `set_tp` 错误注入点、对内部虚拟表的 `ANALYZE`。

## 运行

```bash
# 原始版二进制在 build_release/src/observer/seekdb
python3 migration/judge/runner/judge.py run --seekdb build_release/src/observer/seekdb --work-dir /tmp/judge-orig
python3 migration/judge/runner/judge.py run --seekdb <port> --work-dir /tmp/judge-port --protocol ps
python3 migration/judge/runner/lifecycle.py run --seekdb <binary> --work-dir /tmp/judge-life
python3 migration/judge/runner/judge.py list
```

`run` 默认跑 `clean`、`portable`、`fragile`、`rewritten`、`scenario` 五种状态。`--record` 只能配合原始版二进制和一份明确的 `--cases` 列表使用。

## 改写规则（待你在 00b 关口确认）

判断标准：一个断言如果能通过公开接口观察到，并且值是确定的、面向用户的，就必须保留；移植版必须给出完全一样的结果。只有当断言的值本身就是实现内部状态，或者依赖调试注入钩子时，才可以去掉。

**A 类：保留，移植版必须一致**
- 用户 SQL 的结果（DDL/DML/查询），包括原测试依赖的行序。
- 错误码（包括 OB 专有错误码）和报错文本：它们都会通过协议返回给客户端。
- `SHOW` 系列、`DESC`、标准 `information_schema` 表的输出，包括 `SHOW CREATE TABLE` 里的 OB 存储选项。
- 公开的系统变量和参数：名字不以下划线开头，且出现在 `src/share/system_variable/ob_system_variable_init.json` 或 `rust/config/parameters.yaml` 里。

**灰色地带：默认保留（裁判从严），每处都要记在说明里，由你在关口决定是否放宽**
- 文档化的管理命令：`ALTER SYSTEM MAJOR FREEZE`、`MINOR FREEZE`、对公开参数的 `ALTER SYSTEM SET`。
- `DBA_OB_*`、`CDB_OB_*` 字典视图。
- `ob_` 开头但属于公开系统变量的会话变量，如 `ob_query_timeout`。

**B 类：去掉或改写成公开等价物**
- `oceanbase.__all_*`、`__all_virtual_*`、`__tenant_virtual_*` 这类原始内部表，以及 `GV$`/`V$` 视图。
  - 如果查询观察的是一个对用户可见的事实（比如表是否存在、列定义），改用标准 `information_schema` 或 `SHOW` 查询同一个事实；
  - 如果观察的是内部状态（tablet/ls id、内存统计、合并进度），直接删掉。
- 隐藏参数（`_` 开头）、`set_tp`、errsim、`debug_sync`、`ob_global_debug_sync`。
  - 如果它们是用来强制并发交错的，尽量改成公开的多会话编排：`connect`、`send`、`reap`，加显式事务；
  - 做不到就隔离（`quarantined`），并写清它原本守护的行为。
- `EXPLAIN` 和计划文本。删掉 EXPLAIN；如果被 EXPLAIN 的语句在测试里没有真正执行过，改成执行该语句、断言结果，保住结果覆盖。测试的目的本来就是检查计划选择（比如是否走索引）时，把"计划断言已去掉，不可移植"记在说明里。
- 直接访问隐藏的辅助对象（`__idx_*`、`__doc_id_*` 等内部表或列）。
- 不在公开变量列表里的 `ob_*` 内部会话变量。如果某个变量是打开功能的开关，去掉后用例就测不到原功能，那就隔离，并说明原因。

**改写原则**
- 改动尽量小：只动 B 类构造和直接依赖它的语句，其余保持原样。不要"顺手改进"，不要重排语句，不要放宽任何 A 类断言。
- 不能出现弱化：不能放宽容差、不能删掉某个字段、不能把精确值检查换成"非空"检查，也不能删掉 A 类语句。
- 每个用例在说明分片里写一行，包括：
  - 决定（`rewritten` 或 `quarantined`）；
  - 去掉了什么（B 类构造）；
  - 保留了什么（A 类断言）；
  - 灰色地带保留了什么；
  - 丢掉了什么断言、为什么、丢了哪些覆盖。
- 原始文件一律不改。改写版放在 `cases/`；需要改的 include 复制到 `include/<批次>/` 再改，从 `tools/deploy` 用相对路径 `../../migration/judge/suites/mysqltest/include/<批次>/...` 引用。

## 验证协议（00b 第 3 步）

1. 用原始版二进制跑全部可运行用例，必须全部通过；任何失败都算裁判的 bug。用新实例再跑一遍，确认结果确定、可复现。
2. 对原始代码手工注入几处缺陷，比如翻转一个比较、去掉一条报错路径、改一个输出格式，分别重新构建，确认每个缺陷都至少被一个用例抓到。
3. 报告两次运行的结果：原始版 N/N 通过；每个注入的缺陷各被哪些用例抓到。

## 状态

- [x] 第 1 步普查（新基线）：CI 跑 273 个 mysqltest 用例，其中干净 89、脆弱 32、依赖内部实现 152；obtest 500 个中 499 个依赖内部实现。清单见 `census/out/`。
- [x] 第 2 步改写：152 个依赖内部实现的用例中，29 个 `portable`、119 个改写、4 个隔离。两名对抗审查者各审一半，结论在 `suites/mysqltest/reviews/`；中级及以上的问题都已处理。
- [x] 补充覆盖：18 个场景（`suites/mysqltest/scenarios/`）、7 个生命周期场景（`suites/lifecycle/`）、二进制协议探针，以及预处理、压缩、TLS 三种协议变体。
- [x] 第 3 步验证（原版）：两次完整运行都是 294/294。协议变体在 121 个核心用例上全部通过，预处理协议排除 3 个，原因见 `protocol/ps/exclude.tsv`。
- [x] 第 3 步验证（注入缺陷）：10 个缺陷中，7 个针对具体行为的（M1–M4、M5b、M6c、M6d）都被抓到；M5 是等价变异；M6、M6b 让实例起不来。逐项说明见 `migration/00b-judge-report.md` 第 5.2 节，原始结果在 `validation/results/`。
- [ ] 00b 关口：等你签字，以及对报告第 6 节各项事项的裁定。
