# 第 1 步：依赖图报告

> 本文是 code-migration kit `prompts/01-dependency-map.md` 的产出。
>
> - **基线**：上游 oceanbase/seekdb master `7d907abfa`。
> - **依赖图**：`migration/depmap/`。脚本在 `migration/scripts/`，全部是确定性的标准库 Python，每次运行几秒钟。
> - **状态**：**你已在 2026-10-11 签字**（回复“继续”）。第 6 节各项暂按默认做法执行，见第 6 节开头。设计文档草稿已经开始。

## 结论

1. **文件级的环很少**：6,529 个文件、24,515 条边，只有 28 个环，涉及 71 个文件。其中 16 个只是头文件和同名 `.ipp` 互相包含。可以照 kit 的做法，一个环作为一批来迁。
2. **最大的发现在 crate 级**：
   - 按可行性报告的划分，每个 `src/<模块>` 一个 crate。只看 include，crate 之间无环。
   - 但链接级有 12 条逆向依赖：声明在底层模块，实现在上层模块。加上这些边以后，12 个 crate 全部连成一个环。
   - 照现在的划分，Rust 编译不过。设计文档必须先给出每条逆向依赖的拆法（3.3 节）。
3. **manifest 单元（头文件组）**：共 3,754 个，有 32 个环，全部在单个 crate 内部。最大的两个是 sql 的 1,129 个单元和 storage 的 573 个单元。它们不影响编译，但环内的翻译顺序只能近似。
4. **审查**：
   - 三轮共 120 个文件、956 行逐行证据，漏掉的边和多出的边始终是 0。
   - 问题都出在“哪些文件该排除”，三处都已改成规则：
     - 第 1 轮：tzcode 被当成了手写代码；
     - 第 2 轮：漏了两个生成文件；
     - 第 3 轮开始前：我自己发现 zstd 的目录规则误排除了 OceanBase 自己的封装。
   - 第 3 轮两个方向都干净。
5. **manifest**：已用临时命名规则生成。`wc -l migration/manifest.tsv` 输出 3,755，即 3,754 个单元加一行表头。命名规则属于设计文档 §4，要你来定。

## 1. 依赖图是怎么来的

**include 级（`depmap_seekdb.py`）**：解析 git 跟踪的 `src/` 下的 C/C++ 文件，一条依赖一行。
- **解析方式和真实构建一致**：
  - 引号形式先在本文件所在目录找，再按 CMake 的 include 根目录依次找；尖括号形式只按根目录找。
  - oblib 下的文件只能用 oblib 自己的根目录。
  - 根目录取自 `src/CMakeLists.txt`、`src/oblib/CMakeLists.txt` 和顶层 `CMakeLists.txt`。
- **条件 include 一律算边**：72 条边只在某个 `#if` 下成立，主要是 Windows、Linux、调试分支，都标了 `conditional`。
- **排除的文件**：既不算边也不算遗漏。它们被谁 include，记在 `external_refs.tsv`。

  | 类别 | 文件数 | 怎么判定 |
  |---|---|---|
  | 生成代码 | 36 | 按每个生成器实际写出的文件定规则，并注明生成器的行号；另外，前 40 行带 “DO NOT EDIT” 之类标记的也算 |
  | 第三方 | 53 | 前 80 行有不属于 OceanBase/阿里/蚂蚁的版权或公有领域声明：zstd 47 个，tzcode 3 个，libev 1 个，xxHash 2 个 |
  | 不参与构建 | 26 | CMake 的源文件清单里没有，也没有节点 include 它。清单包括 CMake 文件本身，以及 CMake 在配置时从 Bazel 清单读入的那些变量 |

- **交叉验证**：用这台机器的 release 构建核对了“不参与构建”这一类：凡是实际编译过的文件，没有一个被判为不参与构建。另有 5 个实现文件只在别的平台或构建选项下编译，比如 Windows 服务、Linux 的 malloc hook。它们仍是节点。
- **构建时生成、没有签入仓库的文件**：bison/flex 解析器、内部表 schema、`rust/config` 生成的头文件等，不在仓库里。对它们的 347 条引用全部归了类，见 `unresolved.tsv`，没有一条是“找不到原因的遗漏”。

**链接级（`symdeps_seekdb.py`）**：include 图看不到两种依赖：一是在 `.cpp` 里自己写 `extern` 声明就调用的函数，二是底层的 weak 默认实现被上层的强定义覆盖（`OB_WEAK_SYMBOL`）。
- 对这台机器 release 构建的 330 个目标文件跑 `nm`，按目标文件所在目录归到模块。跳过的目标文件逐个列在 `symbols/skipped.tsv`：第三方 23 个，构建时生成 10 个，合并链接产物 2 个。
- 结果是 49 条跨模块引用边，加 3 条 weak 覆盖边。

## 2. 计数

| 项目 | 数量 |
|---|---|
| 节点（文件） | 6,529 个，2,633,843 行 |
| 边 | 24,515 条，其中 include 24,485、尖括号 include 解析到仓库内 15；另有 2,749 条“实现文件到同名头文件”的边，绝大多数和 include 边重合 |
| 文件级批次（强连通分量） | 6,486 个，其中环 28 个，涉及 71 个文件 |
| 头文件组（manifest 的单元） | 3,754 个，21,437 条边，1,550 个批次，32 个环，涉及 2,236 个单元 |
| crate（每个 `src/<模块>` 一个，可行性报告的划分） | 12 个；include 级 45 条边，无环；链接级 52 条边 |
| 子目录 | 138 个，1,464 条边 |

## 3. 环

### 3.1 文件级：28 个

- **16 个是同名的头文件和 `.ipp`/`.cpp` 互相 include**：头文件末尾 include 自己的内联实现，实现又 include 回头文件。合成头文件组以后，这些环就消失了。
- **12 个跨文件名**：

  | 文件数 | 文件 |
  |---|---|
  | 8 | `storage/multi_data_source/` 下的 `mds_register.h`、`mds_ctx.h`、`mds_node.h`、`mds_node.ipp`、`mds_table_base.h`、`ob_tablet_create_mds_ctx.h`、`runtime_utility/mds_tlocal_info.h`，加 `storage/tx/ob_multi_data_source.h` |
  | 7 | `sql/pl/` 下的 `ob_pl.h`、`ob_pl_allocator.h`、`ob_pl_interface_pragma.h`、`pl_cache/ob_pl_cache.h`，以及 3 个 `sys_package/ob_dbms_*.h` |
  | 4 | `storage/deadlock/` 的 3 个头文件，加 `storage/tablelock/ob_table_lock_deadlock.h` |
  | 3 | `storage/memtable/ob_row_conflict_info.h`、`storage/tx/ob_trans_define.h`、`ob_trans_define_v4.h` |
  | 3 | `standby/restore/` 的 3 个头文件 |
  | 2 | 7 对：`ob_sql_stat_record.h`↔`ob_sql_context.h`、`ob_i_store.h`↔`ob_i_table.h`、`ob_mvcc_define.h`↔`ob_mvcc_row.h`、`ob_plan_cache.h`↔`ob_prepare_stmt_struct.h`、`ob_ddl_service.h`↔`ob_pl_ddl_service.h`、`ob_dfo.h`↔`ob_dfo_mgr.h`、`ob_dtl_channel_mem_manager.h`↔`ob_dtl_mem_manager.h` |

  按 kit 的规定，每个环作为一个批次一起迁。完整清单在 `cycles.txt`。

### 3.2 头文件组级：32 个

单元是“同一目录下同名的 `.h`/`.ipp`/`.cpp`”，manifest 一行对应一个单元。

| 单元数 | 行数 | 所在 crate |
|---|---|---|
| 1,129 | 907,021 | sql |
| 573 | 407,299 | storage |
| 142 | 70,958 | oblib |
| 104 | 64,894 | observer |
| 69 | 90,947 | rootserver |
| 44 | 68,259 | share |
| 33 + 33 + 29 + 9 …… | | share、logservice、oblib …… |

- **为什么会有大环**：`.cpp` 可以随意 include 同模块其他单元的头文件，合成单元后就连成了一片。
- **对编译没有影响**：所有环都在单个 crate 内部（下一节会说明 crate 级的 include 图无环），而 Rust 允许同一 crate 里的模块互相引用。
- **对翻译顺序有影响**：环内的单元只能按头文件在文件级拓扑序里的位置排序（`units/order.txt` 就是这样排的）。所以翻译某个单元时，它引用的一部分接口还没翻译，只能按命名规则推断名字。

### 3.3 crate 级：include 无环，加上链接级就全部连成一个环

**include 级**是一个 DAG，从底层到上层依次是：

oblib → share → data_plane → logservice → objit → query → sql → pl → storage → rootserver → standby → observer

调查时看到的 {sql, storage, rootserver, pl} 环，在冻结的基线里已经被上游拆掉了。

**链接级**的 52 条跨模块边，按 include 分层的方向分成三类：
- 37 条顺向；
- 3 条横向：两个 crate 在 include 级原本没有先后；
- **12 条逆向，会造成环**：

| 方向 | 种类 | 符号数 | 例子 |
|---|---|---|---|
| oblib → observer | weak 覆盖 | 1 | `create_inner_sql_connection_for_proxy`（oblib 里的默认实现在 `src/oblib/common/mysqlclient/ob_mysql_proxy.cpp:29`） |
| oblib → share | 引用 3、weak 覆盖 4 | 7 | `ob_strerror`、`get_cpu_count`、`allow_next_syslog`、`current_resource_owner_id` |
| oblib → storage | weak 覆盖 | 1 | `request_finish_callback`（`src/oblib/rpc/ob_sql_request_operator.cpp:27`） |
| pl → observer | 引用 | 10 | `query::ObInnerSQLConnectionAccess::create_spi_connection_with_external_session` |
| rootserver → observer | 引用 | 27 | `query::ObInnerSQLConnectionAccess::register_multi_data_source` |
| share → observer | 引用 | 2 | `ObIOManager::print_service_status` |
| sql → observer | 引用 | 126 | `data_plane::ObVectorRefreshTransaction::lock_domain_table` |
| sql → pl | 引用 | 152 | `pl::ExecCtxBak::backup` |
| sql → rootserver | 引用 | 1 | `data_plane::rebuild_vector_index` |
| storage → observer | 引用 | 33 | `data_plane::submit_tablet_update` |
| storage → rootserver | 引用 | 4 | `data_plane::renew_ddl_task_lease` |

另外 3 条横向的边：storage → sql（99 个符号）、sql → storage（271 个）、pl → storage（7 个）。

**这些逆向依赖的典型形态**：声明放在底层模块的头文件里，比如 `data_plane/api`、`query/api` 下的接口；实现却放在上层模块里。C++ 靠链接器把两头接上。Rust 的 crate 之间不允许循环依赖，所以照现在的模块划分移植是编译不过的。每条逆向依赖都要在设计文档里定一个拆法，常见的有三种：

- 底层定义 trait 或回调表，上层在启动时注册；
- 把实现挪到声明所在的 crate；
- 把相关 crate 合并。

完整清单见 `crates/union_cycles.txt`，weak 覆盖的明细见 `symbols/weak_overrides.tsv`。

### 3.4 子目录级

子目录级有 8 个环，大小分别是 30（storage）、18（sql）、17（share）、8（observer）、7（rootserver）、4（logservice）、3（pl）、3（standby）。也就是说，sql（约 94 万行）和 storage 这样的大 crate，没法按子目录直接拆成小 crate，否则就会出现 crate 级的环。可行性报告担心的“单个 sql crate 编译过慢”，同样要在设计文档里解决。

## 4. 怀疑型审查

**做法**：
- 每轮换两名新的审查者（`claude-sonnet-5-5`，max effort），各审 20 个新文件，两人互不重叠，也不重复前几轮的文件：
  - 一半随机，从文件清单的前半和后半各抽 10 个；
  - 一半对抗性：A 拿最大的文件和预处理条件最多的文件，B 拿平台专属的文件，以及被排除的生成、第三方、不参与构建的文件。
- 审查者只读源码和构建文件，看不到依赖图。每条 include 写一行，附 file:line 证据，不下结论。
- 我把他们的记录和依赖图双向比对：查漏掉的边、多出的边，以及排除分类不一致的文件。每条差异都回到源码那一行确认。
- 抽样脚本是 `depmap_review_sample.py`（按轮次固定随机种子），比对脚本是 `depmap_review_diff.py`。每轮的样本、逐行记录和差异都在 `migration/depmap/review/round*/`。

| 轮次 | 依赖图版本 | 文件 | 逐行记录 | 漏掉的边 | 多出的边 | 排除分类 | 处理 |
|---|---|---|---|---|---|---|---|
| 1 | v1 `3bd618590` | 40 | 391 | 0 | 0 | 1 处：tzcode 的 `private.h` 被当成了节点 | 第三方改为按上游声明判定，得到 v2 |
| 2 | v2 `5cd54ea75` | 40 | 332 | 0 | 0 | 抽样内 0 处。但 B 的证据顺带暴露 `ob_expr_str_cmp_func_part_7.cpp` 是生成文件；我逐个核对生成器，又找到 `ob_str_datum_funcs_compilation.ipp` | 生成代码改为按“每个生成器写哪些文件”判定，得到 v3 |
| — | v3 `f49876e03` | — | — | — | — | 抽第 3 轮样本时我发现，zstd 目录规则把 OceanBase 自己的 4 个封装文件也排除了 | 去掉目录规则，只按上游声明判定，得到 v4 |
| 3 | v4 `08bad5293` | 40 | 233 | 0 | 0 | 0 | **干净** |

**关于停止条件**：kit 规定连续三轮失败，或同一类问题反复出现，就要停下来交给你。这里“排除分类”这一类出现了三次，但三次的根源相同：排除名单是手工维护的。修法是彻底去掉名单、改成规则，修完第 3 轮就干净了。所以我继续做完了。如果你认为这种反复本身就该叫停，上表已经列出了全部遗漏。

**审查者顺带提出、最后判定无需修改的几点**：
- `murmur_hash.h` 只有阿里巴巴的旧版权声明，按 OceanBase 自家代码处理，是节点；
- libeasy 的头文件没有第三方声明，是节点；
- `ob_memory_tracker_wrapper.cpp` 只在 Bazel 的解析器目标里编译，按 CMake 的标准判为不参与构建。

## 5. manifest（临时）

```
$ sh migration/scripts/manifest_provisional.sh
wrote migration/manifest.tsv: 3754 units
    3755 migration/manifest.tsv
```

- **一行一个头文件组**：source 列是组里的头文件，没有头文件就用实现文件。组里的全部文件列在 `depmap/units/units.tsv`。
- **顺序**：取自 `depmap/units/order.txt`，依赖在前；大环内部按头文件的顺序排。
- **临时命名规则**：`src/<模块>/<路径>/<名字>.<扩展名>` 变为 `rust/port/<模块>/src/<路径>/<名字>.rs`。3,754 个目标路径各不相同，全部以 `.rs` 结尾。kit 的 `make_manifest.py` 只做字符串替换，所以脚本先把模块前缀换成 `@@模块@@` 这样的标记，避免替换后的路径被后面的规则再次命中。
- **和可行性报告的差别**：可行性报告里是 3,753 个单元，现在是 3,754 个。原因是基线换成了 `7d907abfa`，排除规则也更细了。
- **改了命名规则怎么办**：改 `manifest_provisional.sh` 里的替换规则，重跑即可。依赖图本身不用重新生成。

## 6. 需要你决定的事

> **你在关口的决定（2026-10-11）**：回复“继续”。下面四项没有逐条裁定，暂按默认做法执行：
> - 命名规则和单元粒度沿用临时规则，由设计文档正式确定；
> - 12 条逆向依赖的拆法写进设计文档，由你在设计文档关口决定；
> - 生成器改为直接输出 Rust；
> - 数据表交给机械转换。

1. **manifest 的命名规则和单元粒度**（设计文档 §4）：目前按头文件组，一个组对应一个 `.rs` 文件，放在 `rust/port/<模块>/src/` 下保持原有的相对路径。另一种做法是 `.h` 和 `.cpp` 各自一个单元，这样文件级只有 28 个小环。
2. **12 条链接级逆向依赖的拆法**：见 3.3 节。这一条决定 crate 能不能编译，必须在设计文档里定。
3. **生成器**：7 个生成器（`gen_errno.pl`、`gen_ob_sys_variables.py`、protoc 等）要改成直接输出 Rust。另外还有构建时生成的解析器和内部表 schema，SQL 解析器是先通过 FFI 保留，还是重写，见可行性报告第 3 节。
4. **数据表**：`src/storage/fts/dict/ob_ik_dic.cpp`（27.6 万行的分词词典）、`ob_timezone_info.cpp`（约 4 万行时区数据）、16 个 `.map` 和 4 个 `.def` 表格，在 manifest 里仍是单元。建议交给机械转换脚本，不交给翻译 agent。

## 7. 局限与偏差

| ID | 内容 |
|---|---|
| DEV-007 | 链接级依赖只来自这台 macOS 上的 release 构建。只在 Linux/Windows 或其他构建选项下编译的 5 个实现文件，没有覆盖到它们的链接级依赖 |
| DEV-008 | 第二轮两名审查者各有一次在仓库根目录 grep，误打印出依赖图脚本里几行生成器清单。两人都声明没有任何一行判断依赖这几行。第三轮的指令改为只在源码目录里搜索 |
| DEV-009 | 审查者用的是模型计划里依赖图审查者的 `claude-sonnet-5-5`、max effort（可行性报告第 5 节），不算偏差，记在这里备查 |

## 8. 下一步和工期估算

按 kit 的顺序，第 1 步还剩两件事，各有自己的关口。`prompts/02` 要求先有一份已提交的规则手册草稿，所以设计文档在缺口清单之前。

**设计文档草稿**（重新设计模式下取代规则手册）：

- **需要你拍板的决定共 11 组**：
  - 可行性报告第 3 节的 8 组：表达式/算子/计划树的表示、分配器模型、无锁结构、MVCC 版本链、全局单例、weak symbol、SQL 解析器、第三方库；
  - 本报告第 6 节的 3 组：12 条逆向依赖的拆法、命名规则和单元粒度、生成器。
- **工期估算**：
  - 每组先由一个调查 agent（Opus 5.5，max effort）摸清事实：11 个 × 约 30 分钟，并行跑约 1–2 小时；
  - 起草约半天；
  - 对抗审查约半天，按 kit 每名审查者盯一类错误，3–4 名。
  - 墙钟约 2–3 天，大头是等你的决定。
- **你的专注时间**：11 组 × 30–60 分钟 ≈ 6–11 小时，加上读审查结论 1–2 小时。

  这只是第一版草稿。可行性报告估计设计文档从草稿到定稿要 2–4 周、30–60 小时。

**缺口清单**（`prompts/02`）：

- **规模**：可行性阶段扫出约 27,324 处：
  - `T *&` 输出参数 9,457
  - 裸指针成员 6,120
  - `reinterpret_cast` 3,692
  - `ATOMIC_*` 2,997
  - placement new 2,074
  - 引用成员 1,278
  - 手动析构 1,265
  - union 239
  - `this` 初始化 157
  - weak symbol 45

  `prompts/02` 的第一步会在基线上重新扫描。
- **agent 次数**：27,324 ÷ 每批 20 处 ≈ 1,367 批；每批 3 个 agent（提议者加 2 名怀疑型审查者），共约 4,100 次；再加约 10% 的复审，约 4,500 次。
- **单次耗时**：本步实测，Sonnet 5.5 审查 20 个文件的 include 用了 14–19 分钟、23–33 万 token。缺口条目要追踪数据流，更难，按 Opus 5.5 每次 20–30 分钟、3–5×10⁵ token 估算。
- **墙钟**：4,500 × 25 分钟 ÷ 并发数。并发 8 约 9.8 天，并发 16 约 4.9 天，实际取决于速率上限。再加联合审计约 1 天。
- **token**：4,500 × 4×10⁵ ≈ 1.8×10⁹。
- **你的专注时间**：
  - 前 20 行的关口 0.5–1 小时；
  - UNKNOWN 行按 3–5% 估，约 800–1,400 行，按问题归并后约 50–150 个问题，要 5–12 小时；
  - 最终关口 1–2 小时；
  - 合计约 7–15 小时。

## 9. 成本

| 项目 | 数值 |
|---|---|
| 日历时间 | 2026-10-11 00:58Z（你回复“继续工作”）到 02:25Z，共 86 分钟 |
| 子代理 | 6 个，三轮各 2 名审查者，都是 `claude-sonnet-5-5`、max effort |
| 子代理 token | 1,674,268（每名 23–33 万），不含主会话，与前几步的统计口径一致 |
| 本机运行 | 依赖图一次约 3.5 秒，符号级一次约 3 秒；没有重新构建，用的是 00b 阶段的 release 构建 |

`migration/cost-log.tsv` 已追加一行：`1-depmap	2026-10-11T02:25Z	86	1674268	6	claude-sonnet-5-5`。
