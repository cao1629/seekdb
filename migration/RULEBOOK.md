# seekdb C++ → Rust：设计文档（重新设计模式下的规则手册）

> **元规则：两个 agent 可能给出不同答案的问题，答案都写在这里。** 实现者、审查者、修复者动手之前必须通读全文。本文件在任何循环内都只读；修订排队交给人，在批次之间应用。
>
> - **状态**：草稿 v0.2（2026-10-11），**等你在设计文档关口拍板**。
>   - v0.1 经 4 名对抗审查者审查（流程与一致性、内存安全与健全性、并发与性能、行为一致性），共 102 条发现、57 处缺失的规则；另有 1 名复核者对其中 30 条高影响事实做了源码核对。审查原文、复核结果和逐条处理都在 `migration/design/review/`，总表是 `dispositions.md`。
>   - 标【D1】…【D18】的是需要你决定的核心政策，正文按推荐写。D15–D18 是 v0.2 新增；A.1 另外标出 v0.2 改了推荐的项。
>   - 附录 A.2 是其余的决定，按推荐执行，你可以随时推翻；A.3 是有前提条件的决定和触发条件；A.4 是接受的偏差和裁判改动清单。
>   - 拍板之前，不开始缺口清单、基础设施阶段和第 2 步。
> - **基线**：上游 master `7d907abfa`。
> - **事实来源**：
>   - `migration/design/survey/` 下的 7 份调查备忘，附复现脚本：ir-graphs、memory、concurrency、storage-txn、layering、parser-generators-thirdparty、conventions；
>   - `migration/design/review/` 下的 4 份审查和复核结果；
>   - `migration/00-feasibility-report.md`、`migration/00b-judge-report.md`、`migration/01-depmap-report.md`。
>   - 本文引用的数字都出自这些文件，正文不再逐条注明。计数多为正则估算，以出处的说明为准。
> - **读者**：第 3 步的实现者（`claude-sonnet-5-5`）和审查者（`claude-opus-5-5`），第 4 步的修复者，以及基础设施阶段（1d）的作者。第 0 节讲新架构是什么，第 1–6 节是逐条执行的规则。

## 0. 架构（取代模板里的"范围与姿态"）

### 0.1 姿态

1. **这是重新设计，不是逐行直译。**
   - 重新设计的范围：所有权、数据结构和基础设施层，按本节执行。
   - 保持不变的：模块划分、算法、策略常数、错误码、用户可见的行为。
   - 依据：可行性报告判断 1。按原结构直译时，57,226 个函数体里有 45.1% 需要 unsafe。
2. **行为第一，性能第二。**
   - 验收标准只有两个：00b 裁判（287 个 mysqltest 用例加 7 个生命周期场景），以及第 6 步的对齐。
   - 已知较慢但忠实的写法，标 `PERF(port):` 后照样往前走。性能带的定义见 A.3。
3. **翻译阶段不跑编译器。** 草稿不必能编译；第 4 步按 crate 分波次编译（0.2）。基础设施阶段 1d 是记录在案的例外（DEV-014）。
4. **BUG 规则。**
   - 原版的缺陷照样复刻，标 `BUG(port): <复现方法>`，包括 00b 报告第 6 节 D 的 15 条疑似 bug（缺口清单把它们定位到 file:line）。
   - **例外**：照样复刻会在 Rust 里构成未定义行为的写法，即数据竞争、越界、悬垂引用、未对齐访问、读未初始化内存、非法的枚举值、违反别名规则的写。改成在单线程下可观察行为相同的安全写法，标 `FIXED(port): <原缺陷>`。`FIXED` 不进 `prompts/06` 的"现在修"清单。
   - 复刻下来的 `BUG(port)` 只在对齐之后单独修复（`prompts/06`）。
5. **UNKNOWN 规则。** 本文、缺口清单和各张生成表都没有决定的情况：
   - 采用 §5.3 里该类别的默认译法（"最保守的表示"在那里逐类定义）；
   - 标 `TODO(port): <问题>`；
   - 然后继续往前走，不要停下批次。

### 0.2 迁移策略与阶段【D1】【D16】

**核心一次性翻译，再按 crate 分波次编译。** 不做按模块的 C ABI 共存（理由见本节末尾）。

**阶段**：

| 阶段 | 内容 | 交付物 | 关口 |
|---|---|---|---|
| 1a 依赖图 | 已完成 | `migration/depmap/`、临时 manifest | 已签字 |
| 1b 设计文档 | 本文件 | `migration/RULEBOOK.md` | **本关口** |
| 1c 缺口清单与生成表【D15】 | `prompts/02` 的扩展版：脚本先扫，能定论的先定论，agent 只处理剩下的（§5） | `inventory.tsv`、`types.tsv`、`seams.tsv`、`units.tsv` 和各张名字表 | 前 20 行一个关口，最终一个关口 |
| 1d 基础设施【D16】 | 写出并测试第 3 步要用到的全部基础设施（见下） | API 文档、代码、测试 | 你签字 |
| 2 压力测试（重新设计变体） | 试点加一次性切片试跑（见下） | 规则修订；sql 是否拆分 | 你签字 |
| 3 翻译 | 只翻译处置为 `translate` 的单元，每批 100 行（§4） | `rust/port/**` | 每批 |
| 4 编译 | 按波次 `cargo check --keep-going`（见下） | 错误清零 | 每波 |
| 5 运行 | 启动、冒烟；A.4 的裁判改动落地，并用故意改坏的代码重新验证 | | 你签字 |
| 5b 金标准测试 | 由 C++ 测试程序产出 golden 文件，补齐金标准测试和布局测试 | `rust/port/<crate>/tests/` | 你签字 |
| 6 对齐 | 裁判对齐 | | 你签字 |

- 1c 和 1d 可以并行，都要在第 2 步之前签字。1d 的 API 文档是第 2、3 步提示词的只读上下文。
- **1d 的范围**：
  - `oblib` 的门面和基础设施：`mem`、`sync`、`thread`（含 `stack`）、`signal`、`arch`、`atomic`、`error`、`log`、`fmt`、`container`、`hash`、`queue`、`string`、`num`、`ctype`、`sort`、`serialization`、`slot`、`pod`、`prelude`；
  - `ob_macros`：`ob_printf!`、日志、错误链、序列化等宏；
  - `storage_core`（0.9），以及 `share::cache` 里 KV cache 的回收部分（0.8）；
  - 帧模块、datum 模块和 object 模块（0.6、§2.12）；
  - `parser` crate（0.12）；各 -sys 和 shim crate（0.14）；
  - 生成器（0.13）；
  - 1c 和第 3 步要用的脚本：`units.tsv` 与 manifest、名字表、`generated/` 下的注册表和绑定（§4）。
- **基础设施的原则**：被替换的 C++ 基础设施（容器、锁、队列、分配器门面、线程、日志、格式化等）**保留 C++ 的类型名、方法名和错误码**，只把签名按所有权改写，例如 `get` 返回 `Arc<V>`，`revert` 变成 drop；改写后的签名写进 API 文档。翻译者照 C++ 的调用写，不自己挑 crate。这些单元在 `units.tsv` 里的处置是 `replace`（§4）。
- **1d 的模型**：API 设计用 `claude-opus-5-5`；代码由 `claude-sonnet-5-5` 实现，每个模块配两名 `claude-opus-5-5` 审查者；全部 max effort（A.3）。

**第 2 步（重新设计变体）**：kit 在重新设计模式下取消双译者对照，改为"对设计文档的对抗审查，加一次性的全量试跑"。对抗审查已在 1b 完成；试跑按切片做（DEV-013）：

1. **试点**：沿用 `prompts/03`，去掉双译者对照。从 `translate` 单元里挑 3 个：一个 IR 密集的 sql 单元、一个带锁和原子量的 storage 单元、一个 oblib 的算法单元。
2. **一次性切片试跑**：在一次性 worktree 上，用正式流水线（实现者 `claude-sonnet-5-5`，两名审查者）翻译一个约 30–50 万行的切片，默认是 `src/sql/resolver` 加 `src/sql/rewrite` 加它们依赖的 query 单元。审查产出、修订规则，然后删掉。
3. **编译实测**：在切片上测冷 `cargo check` 的时间和峰值内存（16 GB 笔记本），由你或 `build_daemon.sh` 执行，作为禁令的例外（DEV-013）。
4. 第 2 步关口决定 sql 拆不拆（0.3、A.3）。

**第 4 步的波次**：上一波错误清零，才进入下一波。已在 1d 编译过的 crate 只做回归检查。

1. `ob_macros`、各 -sys/shim、`*_data`
2. `oblib`
3. `share`、`storage_core`
4. `query`、`logservice`
5. `data_plane`、`parser`
6. `sql`、`storage`（两者并行）
7. `rootserver`、`standby`
8. `observer`

**不做按模块的 C ABI 共存**：

- 原因有三：
  - oblib 有 48% 的代码在头文件里，有 2,262 个模板；
  - C++ 上层对 oblib 有 4,641 条 include 边，链接层还有约 3,685 个"上层引用 oblib"的符号；
  - api 层有 797 处 `virtual`、433 处 `ObIAllocator`。
- 这些都放不到 C ABI 后面；硬要共存，就得同时维护两份逐位一致的 oblib。
- C ABI 只用于接口窄的孤岛：现有的 `config`、`sql-nio`，以及 C 解析器和第三方库的 -sys/shim crate。

**代价**：第一次能跑起来要等全部 crate 编译通过。所以第 2 步的切片试跑和 1d 的基础设施测试格外重要。

### 0.3 crate 图【D2】

所有移植 crate 放在 `rust/port/<crate>/`，作为 `rust/Cargo.toml` 工作区的成员。

| crate | 来自 | 直接依赖（移植 crate） | 说明 |
|---|---|---|---|
| `ob_macros` | 新建，proc-macro | — | `ob_printf!` 等编译期宏（§1） |
| `*_data` | 新建，由转换脚本生成 | — | 大块数据表，如 `oblib_data`、`storage_data`（0.13） |
| `oblib` | `src/oblib` | `ob_macros`、`config`、`oblib_data` | 最底层；1d 写的基础设施都在这里（0.2）；datum、object 模块也在这里 |
| `storage_core` | 新建（1d），从 storage 和 logservice 抽出的 unsafe 核心 | `oblib` | 0.9 |
| `share` | `src/share` | `oblib` | 含 KV cache（`share::cache`，0.8） |
| `query` | `src/query` + `src/objit` | `oblib`、`share` | `ObItemType`；运行期表达式 `RtExpr`；帧模块；编译期 IR 的 id 类型 `query::ir_id`（0.6） |
| `logservice` | `src/logservice` | `oblib`、`share`、`storage_core` | palf 组缓冲和 LSN 分配器在 `storage_core` |
| `data_plane` | `src/data_plane`（几乎全是 api 头文件） | `oblib`、`share`、`query` | 依赖 query 是因为前向声明的向上类型（0.4） |
| `parser` | 新建：SQL/PL/全文检索的 C 解析器（build.rs 生成并编译）、`ParseNode` 视图、回调蹦床 | `oblib`、`query` | 0.12 |
| `sql` | `src/sql` + `src/pl` | `oblib`、`share`、`data_plane`、`query`、`parser` | pl 并入 sql（0.4）；`QueryArena` 在 `sql::ir` |
| `storage` | `src/storage` | `oblib`、`share`、`data_plane`、`logservice`、`query`、`storage_core` | **不依赖 sql** |
| `rootserver` | `src/rootserver` | 以上全部 | |
| `standby` | `src/standby` | `oblib`、`share`、`logservice`、`storage` | gRPC 用 tonic（0.14） |
| `observer` | `src/observer` | 以上全部 | 二进制，组合根（0.5） |
| -sys | 新建，离线 bindgen | 视库而定 | `zstd138_sys`、`icu69_sys`、`libxml2_sys`、`openssl_sys`、`curl_sys`、`libaio_sys`；SQLite 用 `rusqlite`；jemalloc 用 `seekdb-jemalloc-sys`（`deps/external` 已经通过 Cargo 构建） |
| shim | 新建，cxx | 视库而定 | `s2_shim`、`vsag_shim`、`bgk_shim`（Boost.Geometry、OB 的几何类型、Spirit SRS 解析器）、`regex_shim`（`std::regex`） |
| 已有 | `rust/config`、`rust/sql-nio` | — | 不改动 |

**规则**：

- **依赖方向**：等于 include 级 DAG（`migration/depmap/crates/edges.tsv`），加上两条新边：`data_plane → query`（分层备忘 D 已确认不成环）和 `logservice → storage_core`。符合 Bazel 模块策略：sql 与 storage 互不依赖（`bazel/architecture/module_policy.bzl:79-91`）。
- **sql 暂时不拆**：它约 100 万行 C++，占全部需翻译代码的约四成。第 2 步在切片上实测，冷 `cargo check` 超过 10 分钟或 12 GB 时，沿 IR 边界切出 `sql_ir`/`sql_engine`（A.3）。
- **链接**：C++ 与 Rust 混合链接期间，所有 Rust crate 合成一个 staticlib，避免每个 crate 各带一份 std；最终产物是 `observer` 二进制。
- **unsafe 的 crate 级 lint**：没有白名单模块的 crate 用 `#![forbid(unsafe_code)]`；含白名单模块的 crate（`oblib`、`share`、`query`、`parser` 等）用 `#![deny(unsafe_code)]`，只在白名单模块上写 `#[allow(unsafe_code)]`，CI 用 grep 校验（§3）。
- **cargo 配置**：
  - 工具链保持 1.98.1，edition 2021；
  - 保留 `panic = "abort"`；
  - 参与对齐的 profile 一律 `overflow-checks = false`、`debug-assertions = false`（0.15）；
  - 大 crate 单独设置 `codegen-units`；
  - 开发构建用 `debug = "line-tables-only"`。

### 0.4 跨 crate 依赖：seam 表【D3】

**现状**：链接级有 741 个成环符号；另有 273 个"前向声明的向上类型"出现在下层的成员和签名里。
- 成环符号的来源：12 条逆向边上 364 个，sql↔storage 横向 2 环上 377 个。
- 76% 的形态是"声明在 `data_plane/api` 或 `query/api`、定义在上层"。
- 78% 是"类型在下层、方法体编译在上层"，而 Rust 不能跨 crate 写 inherent impl。

**拆法按优先级选用**，每个符号只用一种：

1. **合并**：只用于 sql+pl。
   - pl 的声明已经在 `src/sql/pl`，`src/pl/*.h` 大多只是 20 行的转发头。
   - 做法：pl 并入 sql crate，同名的 `src/sql/pl/X.h` 和 `src/pl/X.cpp` 合成一个单元（§4）。
2. **下移**：方法体只用到下层的东西时，把它搬进类型所在的 crate。`units.tsv` 为此做函数级重映射，两边的义务见 §6。
   - 典型：值类型的构造、`reset`、`assign`，`ObReqTimeInfo`，`ObSqlEndTransCb`，`print_service_status`。
3. **trait + 类型化槽位**（服务型）：
   - 下层定义 trait 和 `Slot<dyn Trait>`，上层实现，组合根绑定。
   - 自由函数保留同名的转发函数，所以调用点不用改。
   - 槽位为空时，返回与 C++ 空指针分支相同的错误码（`OB_NOT_INIT`、`OB_NOT_SUPPORTED`）。
4. **句柄 trait**（句柄型，以及已知逐行调用的路径）：
   - 调用方持有 `&mut dyn Trait` 或 `Box<dyn Trait>`，代价与 C++ 的虚调用相同。例如 `tx_desc_*`、lob、fts 迭代器。
   - 已知逐行调用的符号一律用这一种，不走槽位：`query::das_scan_next_row(s)`（storage 里 18 处）、`spill_row_store_*`（9 处）。

**链接期 seam（`extern "Rust"` 加导出属性）不在第 3 步使用**：seam 表在任何测量之前就定稿，这一项实际上选不上；而且导出属性与 `forbid(unsafe_code)` 冲突。对齐之后如有实测证据，再作为修订引入，由宏在白名单模块里展开。

**impl 的落点**（Rust 的孤儿规则和 `Drop` 规则）：

- `Drop` 和外来 trait 的 impl（`ObSerialize`、`ObToString`、`PartialEq` 等）一律写在类型所在的 crate。方法体要用上层的东西时，改成下层的 impl 调用槽位钩子。在声明模块之外定义的析构（54 个）和序列化、TO_STRING 函数体（67 个）由 seam 表逐个定落点。
- 跨层 pimpl（下层的 `void *impl_`、析构在上层，如 `ObTxExecResult`、`ObTmpFile`）：译成 `impl_: Option<Box<dyn Any + Send + Sync>>`。下层的 `Drop` 会自动析构上层对象，上层用 `downcast_mut` 取回。
- 下层为上层类型写的模板特化（如 `ob_hashutils.h:730-828` 为 `_ParseNode*` 写的）：impl 挪到类型所在的 crate，按函数级重映射处理。
- 前向声明的向上类型：seam 生成脚本把"成员和签名里用到的前向声明"也列进来（分层备忘的 `fwd_used.py`），每一项选一种处理：句柄 trait、类型下移、不透明句柄、把特化挪到类型所在的 crate。

**seam 表 `migration/design/seams.tsv`**：

- 由脚本根据 nm 结果、头文件和前向声明生成，每个符号一行，写明：声明处和定义处的 file:line、调用方、形状、选用的拆法、Rust 落点。
- 在 1c 完成，由两名 `claude-opus-5-5` 审查者核对；翻译时照表执行，不自行决定。
- 链接级数据目前只有 macOS 构建，生成 seam 表之前要在 Linux 构建上补跑一次（DEV-007）。

**逐边方案**（工作量是 agent 工作量的估计：S ≤ 2 天，M ≈ 1 周，L ≥ 2 周）：

| 边 | 拆法 | 工作量 |
|---|---|---|
| oblib→share（4 个函数） | `ob_strerror`：下移，错误表由生成器写进 oblib。<br>`get_cpu_count`：下移，直接读 `config::cpu_count()`。<br>`allow_next_syslog`、`current_resource_owner_id`：oblib 里设 `OnceLock<fn>` 钩子，默认就是现在 weak 版本的函数体，由 share 注册 | S |
| oblib→observer（1） | trait `InnerConnFactory` + 槽位；空槽返回 `OB_NOT_SUPPORTED`，与现在 weak 版本的函数体一致 | S |
| oblib→storage（1） | oblib 设钩子 `request_finish_callback`（默认什么也不做），由 storage 注册；observer 和 sql 改调 oblib 的包装函数 | S |
| share→observer（2） | `print_service_status` 下移回 share；ex_rpc 用 trait `TaskDispatcher` + 槽位 | S |
| sql→pl（152） | 合并 | S，只需改 units.tsv |
| 指向 observer 的向量索引（169） | query 定义 `VectorIndexUtil`（约 70 个方法）、`VectorIndexAdaptor`、结果迭代器、IVF cache 等 trait 和槽位，实现留在 observer；只依赖 schema 和参数的辅助函数下移到 query | L |
| 指向 observer 的其余部分（27） | end_trans_cb 下移到 sql；req_time 下移到 query；inner SQL 由 query 定义 trait `InnerSqlAccess`；tablet report 由 data_plane 定义 trait `TabletReport`；ex_rpc 同上 | S–M |
| sql、storage→rootserver（5） | data_plane 定义 trait `DdlCoordinator`（5 个函数），由 rootserver 实现 | S |
| sql↔storage 横向（377） | 两个方向都反转。<br>• 句柄型（`tx_desc_*`、lob、fts 迭代器）和已知逐行调用的路径用句柄 trait；<br>• 服务型（`tmp_file_*`、锁）用槽位，空槽返回 `OB_NOT_INIT`；<br>• 值类型的方法体下移到 data_plane 或 query；<br>• `ObExpr` 和下压过滤器族按 0.6 的"下压过滤器"处理 | L |

**weak 符号**：
- 生产构建里真正被覆盖的只有 6 个，按上表处理。
- 其余约 34 处 weak 定义按普通函数翻译，包括从未有强定义的测试缝，以及只在 Linux 上编译的。

**main 之前的初始化**：11 处 `__attribute__((constructor/init_priority))`，以及 268 个目标文件里的动态初始化。Rust 没有 main 之前的初始化：
- 有副作用的（例如注册）改成组合根里的显式调用；
- 其余非平凡的单例，由 `main` 按原版二进制的动态初始化顺序显式 `LazyLock::force`。顺序由脚本从原版二进制的初始化段读出（0.5）。

### 0.5 进程级状态

| C++ | Rust |
|---|---|
| `GCTX.xxx_`（919 处，只用到 16 个成员） | `share::GCTX: LazyLock<GlobalContext>`。指针成员是 `OnceLock<&'static T>`，读取返回 `Option`，保留 `OB_ISNULL` 分支；运行时会变的状态（`status_`、`start_service_time_` 等）用原子量 |
| `share::server_service<T>()`（1,222 处，102 种类型） | 类型所在 crate 里的槽位，名字由 `slots.tsv` 给出（§4）；`oblib::slot::Slot<T>` 是通用槽位类型 |
| `X::get_instance()`（585 处） | `X::instance()`，背后是 `static LazyLock<X>` |
| `GCONF` | 基线已经清零，参数框架已迁到 `rust/config`；直接调用 `config` crate 的 getter |
| MTL / 多租户 | 基线已经移除，不需要对应物 |

**模块对象**（绑定进 GCTX 或槽位的对象、`get_instance()` 单例）：

- 进程内只创建一次，`Box::leak`（或写成 `static`）得到 `&'static T`，**永不释放**。对模块的 `ob_delete`/`OB_DELETE` 不翻译，原位置写 `// DIVERGE(port): module-leak`。
- 生命周期方法 `init/start/stop/wait/destroy` 一律取 `&self`：
  - init 时才填的字段用 `OnceLock<T>`；
  - 运行期可变的字段按 §2.7 放进锁或改原子量；`is_inited_` 是 `AtomicBool`；
  - `destroy(&self)` 只复位状态，不释放内存。
- C++ 依赖"解绑后读到 NULL"的分支（如 `ob_memtable.cpp:170-178`），改为检查模块的 `stopped: AtomicBool`。
- 两段式的 `init(&mut self)` 只用于尚未发布的对象（§2.13 的接收者规则）。

**组合根**：在 observer。
- 照抄 `obs_construct_modules`（构造 64 个模块、绑定 98 个槽位）→ `obs_init_modules` → `obs_start_modules` 的顺序；
- 槽位绑定和静态初始化的副作用由脚本生成 `generated/bindings.rs`，组合根在 C++ 对应的位置调用 `generated::bind_all()`；
- 出错时按 stop → wait → destroy 的顺序收尾。

**`main` 的开头**，按以下顺序，早于任何其他代码：
1. 屏蔽信号（0.8）；
2. 按原版的动态初始化顺序 `LazyLock::force` 全部非平凡单例（0.4）；
3. 注册纯函数式的 seam 和 weak 钩子；
4. 显式启动日志写线程；
5. 其余照 C++ 的 `main`。

`LazyLock`/`OnceLock` 的初始化函数**不得创建线程、不得取锁、不得等待别的线程**。

**不支持解绑和进程内重启**，与当前 master 一致：嵌入式模式已经移除。

**线程局部状态**：
- `THIS_WORKER`（389 处）改成 `thread_local!` 里的 `WorkerCtx`。线程局部只存拥有型或 `'static` 的数据（`Arc<Session>`、trace id、计数等）。
- 需要临时装入借用值的，一律用闭包式接口，如 `WorkerCtx::with_request(&req, || …)`、`with_warning_buffer(&mut buf, || …)`；**不得用 guard 持有借用**。
- `check_status` 一类的上层逻辑，改用启动时注册的钩子；`ObCurTraceId`、警告缓冲区同理。
- arena 和 `MemCtx` **不从线程局部取**，一律显式传参（0.7）。C++ 里绑定在 `CURRENT_CONTEXT` 上的容器，在 Rust 里用全局堆。
- 非 `Copy` 的线程局部（`thread_local`/`RLOCAL` 共 233 处的一部分）：用 `RefCell`，经 `with_borrow_mut` 访问；借用不得跨过任何函数调用。
- **外部线程**（vsag、OpenMP 的线程，共存期的 C++ 线程）可能调用 Rust（分配器钩子、回调）。这些路径不得假定有 `WorkerCtx`，没有时按默认值处理；只能访问 `const` 初始化、没有 `Drop` 的线程局部。

**`Sync` 说明**：放进静态量或槽位的类型必须是 `Sync`，在类型定义旁写一行理由。

**会话**（`ObSQLSessionInfo`，kill、PROCESSLIST 等路径会在其他线程访问它）：`Arc<ObSQLSessionInfo>`，字段按缺口清单第 22 类分组：
- 只由持有 `query_mutex_` 的工作线程访问的字段，放进一把 `Latch<…>`，由请求入口加锁、向下传 guard（§2.7 的重入规则）；
- 其他线程也读写的字段，改原子量或 `ArcSwap`；
- `ObTxDesc *&get_tx_desc()`（127 处调用）改成 `tx_desc()` 返回的 `ArcSwapOption<ObTxDesc>` 槽。

### 0.6 SQL 编译期与运行期对象图【D6】

**编译期**：ObRawExpr、语句、TableItem、逻辑算子、Path。

- **每条语句一个 `QueryArena`**（在 `sql::ir`），图内一律用类型化 u32 id：`ExprId`、`StmtId`、`TableItemId`、`LogOpId`、`PathId`。id 类型和 `IdVec` 放在 `query::ir_id`，下层要在签名里提到它们时不必依赖 sql。
  - id 是 `NonZeroU32` 的包装，所以 `Option<Id>` 仍然是 4 字节，可以直接对应可空指针。
  - 每种节点一个 `IdVec` 存储；节点可以需要 `Drop`。
  - 理由：C++ 本来就是"arena 整体释放、对象身份就是语义"。`ObSharedExprResolver` 会合并结构相同的表达式，代码生成也按对象身份去重。
- **id 在一条语句内永不复用**。子作用域截断时，把其中新建的槽位标成墓碑并析构内容；经墓碑 id 访问返回 `OB_ERR_UNEXPECTED`，并记 ERROR 日志。旧 id 因此不会静默指向新节点。
- **原地改写**：2,336 处 `get_param_expr` 调用里约 95% 只是读，真正写入的只有 50–120 处；改用 id 后完全不需要 unsafe。
- **arena 怎么传**：见 R1（§2.6），按签名判定，由脚本生成 `qa_signatures.tsv`。
- **临时上下文**：全库 21 处 `CREATE_WITH_TEMP_CONTEXT`，另有 33 处局部工厂，分三种情况（§2.4）：
  - (a) worker 和请求入口（4 个文件）→ 每个请求一个新的 `MemCtx`（0.7）；
  - (b) SQL 编译期、建了 `ObRawExprFactory` 的 → `qa.scope()`，可截断的子作用域：结束时丢弃其中新建的节点，保留对外层节点的修改；
  - (c) 其余局部临时上下文 → 块内的局部 `Arena`。
  - 子作用域的前提是：作用域内新建的节点不被外层引用。缺口清单第 11 类逐处确认，已知的可疑点是 `ob_transform_utils.cpp:3590-3625`；确有外层引用的，那些节点改在外层作用域分配（A.3）。
- **语句回收**：C++ 的语句工厂有 free list，Rust 用 `slotmap` 的代际 key，旧 id 访问会报错。
- **向下转型**：按 C++ 的 is-a 判定（§2.13）。
- **表达式算子**（`ObExprOperator` 层次，573 个子类）：
  - 每个子类译成实现 `ExprOp` trait 的零大小类型。`ObExprOperator` 层次**不用** §2.13 的"trait + 数据 struct"。
  - 基类的数据成员，以及 38 个有状态子类的成员，搬到 RawExpr 节点上的 `OpState`：一个 enum，变体名是子类名去掉 `ObExpr` 前缀；由 `ExprOp::calc_result_type(&self, st: &mut OpState, …)` 等方法读写。
  - 注册表由脚本从 528 个 `REG_OP` 和 1 个 `REG_SAME_OP` 生成，以 `ObItemType` 为下标；中间类不注册。运行期仍然只用函数指针 `EvalFn`。
- **封闭的小集合**：RawExpr 约 27 种、LogOp 33、Path 10、OpSpec 61，用 enum 加公共头结构；ExtraInfo（15–19 种）和 ExprOpCtx（25 种）同样用 enum，求值函数按 `match` 取具体类型，不做 `dyn` 向下转型。这些 enum 由脚本从各单元的变体 struct 生成（`generated/`）。

**运行期计划**：

- **共享方式**：用 `Arc<PhysicalPlan>`，构建后只读，`Send + Sync`。
  - 内部用 `RtExprId`/`SpecId`，与 `ob_expr.h:1317` 的"下标+1"编码一致；
  - 统计字段改成原子量（`ob_physical_plan.cpp` 里有 52 处 `ATOMIC_`）；
  - 计划里的定长数组（`ObFixedArray`）是 `Box<[T]>`，链表（如 `ob_cache_object.h:231` 的 `pre_calc_frames_`）是 `Vec`，不用侵入式链表。
- **不再做计划和表达式的字节序列化**：C++ 的序列化本来就只在进程内有效（函数指针按地址写），PX worker 默认共享同一个计划。`_px_worker_share_plan_enabled=False` 时改成深拷贝：`PhysicalPlan`、`FrameInfo` 不得 `derive(Clone)`，深拷贝手写在帧模块里，配 Miri 测试。
- **计划内存大小**：它参与入缓存判断和淘汰（`ob_plan_cache.cpp:892`）。Rust 版定义等价的统计口径；相关视图里的数值是接受的偏差（A.4 `plan-mem`）。

**执行期**：

- **算子树**：拥有式的 `Box<dyn …>` 树。每次调用传入 `&PhysicalPlan` 和 `&mut ObExecContext`，计划本身不存成引用。
- **`ObExecContext`** 保留 C++ 的名字（v0.1 里的 "ExecRuntime" 就是它），新增几张按 id 索引的表：
  - 28 处跨算子旁路（主要在 `engine/px`，含 SPF 子查询迭代器和 PX op input）；
  - 被别的算子或迭代器读写的算子状态（当前 group、batch 计数、rescan 参数），按 `SpecId` 索引；
  - datum 的存储表（见下）。
  - 迭代器不得持有任何指向算子的指针。子查询表达式在求值时要访问正在执行的 SPF 算子的状态（`ob_expr_subquery_ref.cpp:312-322`），一律经这张表。
- **帧与 datum**：
  - 保留 C++ 的字节帧布局。
  - 帧里的 datum 是 `ObDatum { desc: u32, r: DataRef }`，仍是 12 字节（C++ 有 `STATIC_ASSERT(sizeof(ObDatum) == 12)`）。`DataRef` 是 `{ store: u32, off: u32 }` 或 8 字节内联值，**不存绝对指针**。
  - `DataRef` 指向的内存（帧缓冲、批次 arena、行存储的块、钉住的 KV cache 块、计划的常量区）都登记在 `ObExecContext` 的存储表里，由表项拥有或钉住。注销表项和 reset 批次 arena 都要 `&mut`，所以这时不会有存活的视图。
  - `DataRef` 转字节切片只能经帧模块：`fn bytes<'f>(&'f self, d: &ObDatum) -> &'f [u8]`，得到的 `ObDatumView<'f>` 绑定在帧的借用上。
  - 一次取多个参数用 `ctx.eval_args::<N>(expr) -> ObResult<[ObDatumView<'_>; N]>`：先全部求值，再统一借出。覆盖二元算子（306 处 `args_[i]->eval`）和 `eval_param_value`（约 134 处调用）。
  - 按运行期标志复用的临时 arena（`get_reset_tmp_alloc`、`TempAllocGuard`，`ob_expr.h:170-178,260-271`）只能经帧模块 reset，要求 `&mut` 令牌。
  - 常量帧里的 datum 存相对偏移，深拷贝计划时不需要重定位指针。
  - unsafe 只出现在帧模块和 datum 模块里，处数在 1d 实测后写进 §3。
- **运行期新建、引用计划表达式的表达式**（`ObDatumCaster` 的 `cast_expr.args_[0] = &src_expr`，`ob_datum_cast.cpp:13070`）：参数用 `ExprRef`，即 `Plan(RtExprId)` 或 `Local(&'e RtExpr)`。
- **运行期写共享计划的地方**：已确认一处，`ObExprColumnConv::eval_enumset` 在没有执行上下文 id 的分支里临时改写共享表达式的 `args_[0]`（`ob_expr_column_conv.cpp:741-746`），这在 C++ 里是数据竞争。
  - 这个分支改成把替换后的参数作为显式实参传入（`eval_enumset_with_arg0(expr, ctx, arg0)`），不复制 `RtExpr`，标 `FIXED(port)`；有执行上下文 id 的分支照 C++，每次执行一份副本（`:747-776`）。禁止逐行克隆 `RtExpr`。
  - 另有 31 处 `extra_info_` 的非 const 转换和 6 处 `const_cast<…Spec>` 待缺口清单逐处审查。
- **按地址顺序迭代的 3 处**：`ob_raw_expr_copier.cpp:336`、`ob_raw_expr_replacer.cpp:379`、`ob_transform_utils.cpp:7953`（最后一处所在的函数没有调用者）。
  - 改成按插入顺序，标 `DIVERGE(port): addr-order`。只影响 EXPLAIN；裁判里的 EXPLAIN 都在 `--disable_result_log` 下，不需要归一化（A.4）。
- **下压过滤器**：query 定义 trait `PushdownFilter`，方法清单由 seam 表给出；sql 实现；storage 只持有 `Box<dyn PushdownFilter>`，或经 `RtExprId` 加 `EvalFn` 访问。data_plane 里引用它的 8 处按前向声明的方式处理（0.4）。

具体译法见 §2.6 的 R1–R9。

### 0.7 内存模型【D7】

- **查询作用域**：
  - 请求 arena 是 `oblib::mem::Arena`，内部是 bumpalo 3.20，翻译代码只看这层外观；每个请求一个，挂在 `MemCtx` 上。
  - 查询作用域的类型带 `'q` 生命周期。
  - **arena 里的类型不能需要 Drop**：`try_alloc` 用编译期断言拒绝这类类型；`ArenaVec<'q, T>` 同样要求 `!needs_drop::<T>()`。C++ 里被显式析构、或者成员需要析构的类型，不放进 bump arena，一律 `try_box` 或拥有型。
  - 增长期间用 `ArenaVec<'q, T>`，定型后转成 `&'q [T]`。
  - 批次临时 arena 归帧模块的存储表所有，在批次边界 `reset`（0.6）。
  - 翻译代码禁止 `ManuallyDrop` 和 `mem::forget`（clippy 的 `disallowed_types`/`disallowed_methods`），只有 `oblib::mem` 例外。
- **容器的存储类型按拥有者的生命周期类别定**，类别由 `types.tsv` 给出（§2.5）。
- **长生命周期对象**：会话、计划缓存、schema、存储元数据等，一律改成拥有型（`Box`/`Vec`/`Arc`），走全局分配器。随之消失的有：`ObIAllocator` 参数、placement new、手动析构。
- **自持 arena 的对象**（拥有一个分配器、又借用它：288 个长生命周期类，以及查询、语句级的同类对象），由缺口清单逐类打标签：
  - (a) arena 能上移到调用方栈帧的（只在单个请求内）：移到调用方，对象带 `'q`；
  - (b) 跨调用或跨请求存活、或必须 `Send` 的（游标、SPI 结果集、阻塞算子的行存储）：不用 bump arena，改为拥有型分块缓冲 `Vec<Box<[u8]>>` 加 `RowId { chunk: u32, off: u32 }`，算子只存 id；这类对象不得持有 `&Arena`；
  - (c) 指向自身或兄弟成员的指针（如 `p_field_columns_`、`tlog_`、`elr_handler_`、`head_(&dummy_)`；初始化列表里 19 处，placement new 进自身缓冲 31 处，`new (&member)` 134 处）一律删除，改为访问函数或调用时传参。确实需要原地链接的，只允许在 §3 白名单模块里用 `Pin<Box<_>>`。
  - "默认指向自带分配器、也可改指外部分配器"的成员（如 `ob_rowkey_info.cpp:104` 的 `allocator_(&arena_)`）：改成"外部 arena 参数"或"拥有型缓冲"二选一，由清单给出。
- **全局分配器**：
  - `#[global_allocator]` 是 `seekdb-jemalloc-sys` 上的一层直通封装：**不计数、不加块头、不分 ctx arena**；不分配、不加锁、不 panic、不访问线程局部。这与 C++ 默认的 jemalloc 后端一致：`ob_malloc` 直接调 `je_malloc`，`ObMemAttr` 只在失败日志里用到（`ob_malloc.h:118-145`）。
  - 因此 `ObMemAttr`、内存标签和 `ObMallocHookAttrGuard` 在 Rust 里没有对应物，翻译时删掉；要计入限额的，见下一条。
  - C++ 可以用环境变量 `MALLOC_BACKEND=obmalloc` 在运行时切到自带的分配器，Rust 版不支持（A.4 `malloc-backend`）。
  - 配一组在 Miri 和 ASan 下运行的分配器门面单测。
  - **不设硬上限**，与基线一致。
- **限额**：用显式的 `MemBudget`（原子计数），只在 C++ 原来的检查点检查。
  - 单查询上限：错误 11049，原来有 9 个检查点，计入 arena 的占用和算子报告的用量。C++ 的口径是整棵请求 MemoryContext 的占用（含 563 处惰性绑定到 `CURRENT_CONTEXT` 的数组），Rust 版不计全局堆上的查询期容器，所以触发点和报错文本里的数字不同（A.4 `query-mem-limit`）；
  - 工作区落盘：35 个文件；
  - 以及 `vector_memory_limit`、memstore、kvcache 的模块预算。
- **分配失败**：`OB_ALLOCATE_MEMORY_FAILED` 映射为错误码 -4013。
  - arena 分配一律用 `try_*`；
  - 容器的 `push_back` 内部用 `try_reserve`；
  - 大小取决于用户数据、或超过 64 KB 的缓冲，必须用 `try_reserve`；
  - 小的固定大小分配允许不可失败：在 `panic = "abort"` 下 OOM 就是 abort（A.4 `oom-abort`）。
  - **零字节分配**：arena 外观的原始字节接口（对应 `alloc.alloc(len)`）在 `len == 0` 时返回失败，与 `PageArena` 一致（`page_arena.h:220-231`），调用方照 C++ 报 4013。
- **线程安全的 arena**（`ObSafeArenaAllocator` 29 处、`ALLOC_THREAD_SAFE` 11 处）：`Latch<Arena>`，或每线程一个 arena；不得 `unsafe impl Sync`。
- **不依赖 allocator_api**：钉住的 1.98.1 里没有；即使 1.100 稳定了 `Allocator` trait，`Box::try_new` 等仍不稳定。
- **内存视图**（`V$OB_MEMORY` 等）：保留表结构，内容由 jemalloc 的统计（`mallctl("stats.*")`）和 `MemBudget` 填充；裁判只比较表结构。
- **`SMART_VAR`/`HEAP_VAR`**：一律放到堆上。
- **第三方库的分配钩子**（zstd `customMem`、zlib `zalloc`、vsag 的 allocator、JSON 解析器的 allocator）只接到原子计数的 `MemBudget` 加全局分配器，不接 bump arena：vsag 会从多个线程回调（0.14）。

### 0.8 并发模型【D8】

- **线程**：
  - 保留普通 OS 线程和阻塞式 worker。seekdb 空闲时实测 80–90 个线程，没有协程，也没有 TG 框架。
  - **禁用 async**，唯一的例外是 standby 的 gRPC（tonic）：在 standby 内部起一个独立的 tokio runtime。
  - `oblib::thread` 提供 `Threads`、`SimplePool<T>`、`Timer`/`TimerService`、`ReentrantThread`、`AdaptivePool`。
  - 策略常数照抄：worker 的上下限公式、空闲 10 秒收缩、停滞 3 秒救援扩容、TimerWK 4–128 个。
- **栈**（`oblib::thread::stack`）：
  - `SMART_CALL`（约 1,130 处）→ `smart_call!`，`SMART_CALL_LARGE` → `smart_call_large!`：剩余栈不足 64 KB（`_LARGE` 为 192 KB）时切到扩展栈段。
  - 扩展栈段每段 `2 MB − 34 KB`（`ob_smart_call.h:29`），从全局分配器申请，每线程缓存、按栈式空闲表复用，线程退出时释放；用 `psm::on_stack` 切栈。C++ 扩栈走 `ob_malloc`、不加保护页（`protected_stack_allocator.cpp:69-135`），不进系统调用；所以**禁止每次扩栈都 mmap/munmap**（stacker 每次都这样做，因此不用 stacker）。
  - 每线程的累计口径照 C++：`all_stack_size` 的初值是原生栈大小，累计超过 10 MB 时返回 `OB_SIZE_OVERFLOW`。
  - `check_stack_overflow`（111 处）→ `oblib::thread::check_stack_overflow(reserved)`，默认 `reserved = 32 KB`：按**当前栈段**计算剩余量（切栈时更新线程局部的段边界），不扩栈，不足就返回 `OB_SIZE_OVERFLOW`。解析器回调 `check_stack_overflow_c` 同样走这里。
  - 原生栈大小：C++ 的公式（256 KB − 16 KB − 17 KB，macOS 上按 16 KB 页取整为 224 KB，`ob_server.cpp:1991-1994`）乘一个帧膨胀系数。系数在第 5 步用三个深嵌套用例实测，测出之前取 1（A.3）。
- **锁**：`oblib::sync` **移植 ObLatch 的语义**，不直接用 parking_lot 的锁：
  - 锁字里记写者的 itid，`is_wrlocked_by_self()` 由此而来；
  - 自旋次数按 204 项 latch 表的 `LatchId` 取，包括 17 项纯自旋（`INT64_MAX` 或 2000 万次）；
  - 等待队列用 `parking_lot_core` 的 park/unpark（可移植的 futex）；
  - 带截止时间的加锁，超时返回 `OB_TIMEOUT`。DDL 和 Root Service 的两个串行锁用语句截止时间加锁，超时报 4012，用户可见；
  - 写锁降级；READ_PREFER。
  - 各种锁原语的映射见 §2.7。**加锁顺序原样保留**，不重排。
- **原子操作**：
  - `ATOMIC_*` 一律 `SeqCst`。2,962 处 `ATOMIC_*` 中 99% 在 C++ 里本来就是 seq_cst 或 `__sync` 全屏障；seekdb 用 Clang 编译，`__sync_*` 和 Rust 的 `SeqCst` 生成同一份 IR。
  - 29 处显式弱序照原样写。
  - 原子字段上的普通读写：读用 `Acquire`、写用 `Release`；拥有 `&mut` 的上下文用 `get_mut()`。只有纯计数、统计和停止标志才用 `Relaxed`，由清单第 8 类标出（§2.7）。
  - 弱化 ordering 只在对齐之后做，要有 profile 证据和 loom 测试，标 `PERF(port)`。
- **回收方案**：
  - **keybtree**：在 `storage_core` 里移植 QClock + RetireStation + ObQSync（约 500 行）。退休表按 `itid % 4096` 分槽，每槽上限 1024，超限时只等一个宽限期（`ob_retire_station.h:198-258`）；节点从 memtable 的 FIFO arena 分配，随 memtable 释放；销毁前 `WaitQuiescent`。都与 C++ 相同。
  - **KV cache**：在 `share::cache` 里移植 map 节点的 hazard version、内存块的 hazard pointer 和句柄的 RetireStation，对外只暴露不透明的 `KvBlockHandle`（`&[u8]` 访问加 `Drop`）；map 节点只存块 id 和 `seq_num`，不持有句柄。put/erase/wash 照 C++ 不等读者。
    - 移植时补上退休条件里的版本检查：C++ 的 `ObKVCacheHazardSlot::retire`（`ob_kvcache_hazard_version.cpp:75-102`）不比较 `min_version` 就释放删除表上的全部节点，头文件注释（`.h:54`）和上游 OceanBase 都要求比较。照样复刻会是释放后使用，所以标 `FIXED(port)`，并作为新的疑似 bug 报给你。
  - **ObQSync 当静默屏障用的**（锁等待表、`ObFifoArena`、keybtree 销毁）：`oblib::sync::Quiescence`，每个 itid 一个对齐的 `AtomicI64`，提供 `enter`/`leave`/`sync()`，全是安全代码。
  - **"换掉指针后等读者退出"**：`arc-swap`。
  - **HazardRef** 不移植：它唯一的最终用户 FixedHash2 由 `oblib::hash::FixedHash2` 取代。
  - 以上回收代码都配 loom 测试；单线程部分跑 Miri。
- **并发容器和队列**：一律用 1d 写的 oblib 封装，保留 C++ 的名字和语义（§2.7）。DashMap、crossbeam 只出现在这些封装内部，哈希器固定，不用 `RandomState`。
- **信号**（`oblib::signal`，直接用 libc：nix 的 `Signal` 枚举没有实时信号）：
  - `main` 的第一条语句屏蔽 SIGPIPE、SIGTERM、SIGUSR1，以及 `ob_signal_handle.cpp:105-110` 列出的实时信号（40–64 中的大部分），早于任何线程（0.5）；之后创建的线程都继承这个掩码。
  - 平台差异照 C++：
    - Linux：`sigaddset` 加原始信号号；信号线程用 `sigtimedwait`；
    - macOS：大于 32 的信号号静默跳过、不报错（SDK 的 `sigaddset` 宏就是这样，基线二进制里的掩码只有 SIGPIPE、SIGTERM、SIGUSR1 三位）；信号线程用 `sigwait`，析构时发 SIGUSR1 唤醒（`ob_signal_handle.cpp:25-37`）。
  - SIGPIPE 忽略，SIGTERM → `raise(SIGKILL)`（生命周期场景会测到），SIGUSR1 优雅停机。
  - `memory_dump` 里的 `siglongjmp` 改成不依赖信号的内存探测：Linux 用 `process_vm_readv`，macOS 用 `mach_vm_read_overwrite`。
- **不翻译的死代码**：
  - `ob_bucket_qsync_lock`、`ob_delay_free_allocator`、`ob_map_queue_thread_pool`、`ob_ms_queue`（连同 `ob_co_seq_queue`）、`ob_ring_array`；
  - 没有调用者的 `install_ob_signal_handler`、`do_with_crash_restore`。
  - 这些都由缺口清单逐一确认，处置为 `delete`（§4）。

### 0.9 存储与事务【D9】

| 结构 | 做法 | unsafe |
|---|---|---|
| MVCC 版本链、`ObMvccRow`、行锁 | 进 `storage_core`。<br>• 节点 `#[repr(C)]`，链接字段是 `AtomicPtr`，`prev()`/`next()` 用 `Acquire` 读；<br>• 只能经 `NodeRef<'m>`/`RowGuard<'m>` 访问，`'m` 来自 `MemtablePin` 或持有 `Arc<Memtable>` 的 guard；<br>• 节点随整个 memtable 一起释放，版本链不需要单独回收；<br>• 尾随载荷（`ob_mvcc_row.h:99` 的 `buf_[0]`）的指针从分配得到的原始指针派生，不从 `&Header` 派生；<br>• `ObRowLatch` 是纯 TAS 自旋（`ob_row_latch.h:37-40`） | 在核心内 |
| keybtree | 进 `storage_core`。<br>• **按原算法移植**：它的切分点和行数估计会进入并行扫描的切分和优化器；<br>• 回收照 C++（0.8）；<br>• 扫描只在每次批量填充最多 225 个 KV 时进入临界区，填完立即退出；临界区令牌不得存进迭代器、游标、结果集 | 在核心内 |
| 事务回调链表 | 进 `storage_core`。<br>• 侵入式原子链接，保留三把锁的并发模式（`ObByteLock` 的退避策略，§2.7）；<br>• 回调是闭合的 enum，共三种 | 只有链接操作 |
| 回调里指向行、节点、memtable 的指针 | **保活协议收回核心**：<br>• 回调只能经核心的 `MemtableRef::register_callback()` 创建，同时拿到一个 `MemtablePin`（按 memtable 分片计数，避免所有写者争同一条缓存行），摘除或析构时归还；<br>• 核心的 memtable 释放入口在 pin 计数不为零时拒绝释放（记 ERROR 并延后）。storage 的协议错误因此最多变成延迟释放，不会变成未定义行为；<br>• 回调路径上的 `'m` 来自 pin；<br>• 保留 C++ "销毁 memtable 前先摘掉所有指向它的回调"的流程（`ob_memtable.cpp:166-181`） | 在核心内 |
| memtable arena | 进 `storage_core`。<br>• 移植 `ObFifoArena`：并发分配、整块释放；<br>• 对接 memstore 的限流计量 | 在核心内 |
| palf 组缓冲、LSN 分配器 | 进 `storage_core`（logservice 因此依赖它）。<br>• 组缓冲：在 `UnsafeCell<[u8]>` 上做不相交的写；<br>• LSN 分配器：`portable_atomic::AtomicU128`（`lsn_allocator.cpp` 里 21 处 CAS128/LOAD128） | 少量 |
| tx ctx 表、tx desc、表锁对象、tx data | `Arc`，加桶数组，每桶一把 `RwLatch`；tx ctx 表仍是 1<<14 个桶 | 0 |
| tablet/table/sstable 句柄、t3m | `Arc` 加 `arc-swap`。<br>• "引用归零后推进 GC 队列"放进 `Drop`；<br>• tablet 自带的 arena 改成拥有型字段 | 0 |
| tablet 指针表（`ObResourceMap` 一族，`ob_resource_map.h:189-200`） | `oblib::hash::BucketedHashMap`：分区数等于 C++ 的桶锁数，每个分区一个带 `RwLatch` 的 ObHashMap；`get_tablet` 走分区读锁，保留 C++ 的分段并发，**不得退化成整表一把锁** | 0 |
| 宏块引用 | 记的是磁盘块，不是内存。`ObBlockManager` 的 `block_map_` 用 `oblib::hash::ObLinearHashMap`；`inc_ref`/`dec_ref`/`sweep_one_block` 照 C++ 在同一把桶锁（`BucketLatch`）下做"取、改、写回"，sweep 只在 `ref_cnt == 0` 时 erase（`ob_block_manager.cpp:623-698,881-897`） | 0 |
| 行持有者映射（`RowHolderMapper`） | `oblib::hash::ObLinearHashMap`，分片数按核数放大；`reset_hash_holder` 的"仍由我持有才删除"在分片写锁内完成（`ob_lock_wait_mgr.cpp:140-154`） | 0 |
| MDS、表锁内部的链表、日志回调池 | `slab`，加索引和锁；MDS 的 6 种单元改用 enum 擦除类型 | 0 |
| palf 滑动窗口 | 安全 Rust；`SlotGuard` 的 `Drop` 就是 `revert` | 0 |

- **`storage` crate 加 `#![forbid(unsafe_code)]`**。MVCC 的规则（`mvcc_write_` 的五种情况、`lock_for_read`、cleanout、ELR）都用核心 crate 的安全 API 写。
- **头加尾随数据的结构**（零长或柔性数组成员约 70 个，如 `ob_chunk_datum_store.h:165,522` 的 `StoredRow`、`Block`，`ob_ra_datum_store.h`、`ob_temp_block_store.h` 里的同类结构）：一律表示为字节缓冲上的视图，如 `StoredRowView<'a>(&'a [u8])`、`StoredRowMut<'a>(&'a mut [u8])`，头字段用 0.10 的逐字段编解码读写。只有 `storage_core` 允许原始指针形式。
- **对象池**（tablet、tx ctx、日志回调）首版去掉，但保留数量上限和对应的错误码，例如 `OB_TRANS_CTX_COUNT_REACH_LIMIT`。
- **排期**：`storage_core` 在 1d 写完、编译通过、配好 loom/Miri 测试，API 在第 3 步开始前冻结。v0.1 要求事务簇（约 10.4 万行）在同一个批次窗口里翻译，这条取消：第 3 步不编译，跨单元的名字由生成表决定。

### 0.10 持久化格式【D4】

**Rust 版读写与 C++ 版相同的格式，能直接打开 C++ 版写下的数据目录。**

- **范围**：
  - 日志与元数据：clog（palf）、slog、事务表、MDS 数据源 ID；
  - block_file：超级块、宏块和微块的头、编码微块、索引块；
  - 序列化：unis 序列化，由逐字节一致的 derive 宏实现；
  - 约 25 个按内存布局直接落盘的头；
  - 校验与哈希：`ob_crc64`（含 SSE4.2 路径），palf 日志头用的 XXH64；
  - 压缩：zstd 1.3.8（OceanBase 补丁版）、zlib 1.2.13；
  - 其他：bitpacking 布局、S2 cell id、SQLite 元数据文件、vsag 的序列化格式。
- **落盘头的读写**：一律用安全代码逐字段小端编解码（`from_le_bytes`/`to_le_bytes`），由 `ob_bitfield!` 或派生宏生成 `read_from(&[u8]) -> ObResult<Self>` 和 `write_to(&mut [u8])`；`#[repr(C)]` 只用于布局文档和 golden 测试。需要零拷贝时用 `HeaderView<'a>(&'a [u8])` 加按偏移读取的访问函数。指针型联合体成员改成"相对缓冲区的偏移"加访问函数。
- **packed 结构**（约 64 处 `__attribute__((packed))`/`#pragma pack`，不含第三方）：要么表示成上面的字节视图，要么 `#[repr(C, packed)]` 但只按值读写字段，不得取字段引用，也不得 derive 需要字段引用的 trait。
- **迁移期间格式冻结**。Rust 版如果要改格式，只能走版本号。
- **裁判增加跨二进制场景**：C++ 写、Rust 读，分正常重启和 kill -9 两种（A.4）。
- **`COMPRESS()` 的输出字节保持不变**：zlib 静态链接同一份 1.2.13 源码，启动时断言 `zlibVersion()`（0.14）。

### 0.11 必须逐字节一致的用户可见面【D5】

1. **错误**：
   - 错误码、SQLSTATE、报错文本（按 512 字节 `vsnprintf` 的语义）；
   - 错误包：`send_error_packet` 逐行翻译（§2.3），包括 PL 的 SIGNAL/RESIGNAL 分支；
   - 裁判必须关闭 `enable_rich_error_msg`。
2. **`TO_STRING` 的输出格式**：`cmp_meta()` 会把它原样返回给用户（§2.11）。
3. **没有 ORDER BY、或 ORDER BY 有并列值时的行序**：
   - `ObHashMap`/`ObHashSet` 和 SQL 引擎自带的哈希表逐行移植：桶数、取模、头插、哈希函数都不变；
   - `ob_sort` 逐行移植**基准平台**的 `std::sort`。基准是基线构建实际用的 MacOSX15.sdk（15.4）里的 libc++ 19.1.2（`_LIBCPP_VERSION 190102`）的 introsort 路径。SDK 路径列进 ENV-1 的固定项：`xcrun` 默认的新 SDK 是 libc++ 21，不能用；
   - `std::push_heap/pop_heap/make_heap/sort_heap` 同样逐行移植这个版本（相等时选左子）；翻译代码禁用 `BinaryHeap`；`ObBinaryHeap` 按原代码移植；
   - 前提：在 Linux 上复跑 C++ 版裁判（DEV-005）时，没有因为排序差异而失败。Linux 构建链接的是 libstdc++，复跑排在第 3 步之前（A.3）。如果失败，就回到本决定，改为让裁判对并列行不敏感：加 `--sorted_result` 或补全 ORDER BY。
4. **会进入用户输出的并发结构的顺序**：
   - 会话表的迭代序照 C++ 的 DCHash 序（按会话 id 的 murmurhash 排序）：`SHOW PROCESSLIST` 和 `information_schema.PROCESSLIST` 都不排序；
   - 行锁等待者的唤醒序照 FixedHash2 的排序（§2.7）。
5. **以下逐行移植，并配上以 C++ 为准的金标准测试**：
   - 字符集与排序规则、时间的解析与格式化、`ObNumber`、`ObDecimalInt`；
   - 浮点数文本（MySQL 的 dtoa）、`RAND(seed)`；
   - hash 分区的落点（`ObObj::hash_murmur`）；
   - 数值文本解析：`ob_strtod` 和 C 库的 `strtod` 是两种语义，fast_float 有自己的契约（§2.5）。
6. **浮点求值**：
   - 乘加是否融合（FMA）以 D17 为准：推荐用 `-ffp-contract=off` 重建 C++ 参照，Rust 一律不融合；
   - C 数学库、浮点转窄整型、无守卫的整数除法和取余，按 §2.12 的辅助函数译；
   - 以浮点数为键的 `std::map` 用复刻 C++ `operator<` 等价类的包装类型（§2.5）。
7. **随平台变化的 C 语义**：`char` 提升成 `int` 参与运算或比较时按平台的符号性（`core::ffi::c_char`）；`long` 用 `core::ffi::c_long`；NaN 的拼写按目标平台的 C 库（§2.3）。
8. **递归深度**：按 0.8 的栈规则移植；第 5 步按实测系数调整原生栈之后，仍有差异的才记为偏差（A.4 `stack-depth`）。

### 0.12 解析器【D10】

- **保留** bison/flex 生成的三套 C 解析器：SQL、PL、全文检索。
  - `parser` crate 的 build.rs 调用 `deps/3rd` 里的 bison 2.4.1 和 flex 2.5.35，等价于 `gen_parser.sh`，再用 `cc` 编译。
  - 理由：1064 报错的 near 文本和行号、select 列名、plan cache 参数化，全部取决于 bison 2.4.1 自动机和约 1.3 万行动作代码的确切行为。保留原解析器，这部分的对齐风险就是零。
  - 这些 C 单元在 `units.tsv` 里的处置是 `keep-c`；`parse_node.h` 变成布局镜像。
- **FFI 面很窄**：server 调用解析器 23 个函数，解析器回调 server 19 个函数。
  - 19 个回调按函数级重映射搬进 `parser` crate：`extern "C"` 蹦床把指针转成安全类型，再调用 sql 在启动时注册的 trait 方法；
  - `lookup_pl_symbol` 改成由 `sql::pl` 在启动时注册的函数指针；
  - 和字符集的 5 处直接耦合，打一个小补丁，改成调用访问函数。
- **`ParseNode`/`ParseResult` 的布局**：
  - 用 bindgen 离线生成并入库，记录生成时用的 `-D` 参数。服务端解析器有意不定义 `SQL_PARSER_COMPILATION`（`src/query/BUILD.bazel:329-331`），而 `parse_node.h` 有几处字段只在这个宏下存在；
  - build.rs 用完全相同的参数编译 C，并校验；
  - C 侧导出 `parse_node_layout()`，返回各结构的 `sizeof`/`offsetof`，启动自检时与 Rust 常量比对。
- **`ParseNode<'p>` 句柄**：`Copy`、`!Send`，生命周期就是语句的解析 arena。
  - 读：`kind()`（返回 `ObItemType`，直接包装 i32）、`child(i)`、`num_child()`、`str_bytes()`、每个标志位一个访问函数；
  - `str_bytes()`：指针为空时返回 `&[]`，长度为负时按 0 处理；
  - 写标量字段：对应的 setter。plan cache 参数化会把常量节点就地改成 `T_QUESTIONMARK`；
  - 写指针字段：setter 只接受来自同一解析 arena 的 `&'p [u8]` 或 `ParseNode<'p>`。其他来源（参数 ObObj 的缓冲、`ObNumber::format()` 的结果、字符串字面量，如 `ob_sql_utils.cpp:203,212` 的 `replace_questionmarks`）先拷进解析 arena，并同时更新 `str_len_`；改 `children_` 用 `set_child(i, node)`；
  - 新建节点：`ParseArena::new_node(kind, children)`。解析器目录外在栈上构造的节点（35 处）改成 `LocalParseNode`（拥有一个 `sys::ParseNode` 值），用 `as_node(&mut self) -> ParseNode<'_>` 取句柄；
  - 需要长期持有、跨线程使用的节点（PS 缓存的 `raw_params_`，`ob_prepare_stmt_struct.cpp:345-378`）转成拥有型的 `OwnedParseTree`（`Send + Sync`，深拷贝）；
  - 就地改写节点字符串（如 `ObCharset::casedn`）按 §2.4 的"拷贝、修改、改指"处理；
  - 39 处 `const_cast<ParseNode` 由缺口清单第 21 类逐处处理。
- **规则**：
  - 回调绝不重入解析器，因为 longjmp 不能越过 Rust 栈帧；
  - 一次解析必须在同一个 OS 线程上跑完，因为 `jmp_buf` 和错误缓冲是线程局部的；
  - 解析 arena 要活过 resolve，因为 `TableItem::node_` 会持有句柄。
- **迁移完成后（可选）**：在同一视图后面换成 bison 3.8 + Rust skeleton 生成的解析器，以语法树、错误位置、参数化输出三项差分全部一致为关口。这不属于本次迁移。

### 0.13 生成器与数据表

- **能用 build.rs 的就用 build.rs**，沿用 `rust/config/build.rs` 的先例，读同一份输入：
  - `ob_errno.def` → `oblib::error`：错误码常量、各张表、每条带参消息一个带类型的格式化函数（§2.3）；
  - 系统变量 JSON → share。94 个钩子写的是 C++ 函数名，由生成器按 §4 的路径规则、改名表和重载表映射到 Rust 路径，映射表随 build.rs 入库；
  - proto → prost/tonic：standby 的 gRPC，以及 share/geo 的 `ob_vector_tile.proto`（ST_AsMVT）；
  - 版本号 → `cargo:rustc-env`；
  - `ObItemType` → `query::item_type`（见下）；
  - bitpacking（`ob_generated_*_bp_func.*`，24,406 行，其中 SIMD 调用 7,200 处）→ build.rs 输出到 `oblib/src/simd/bitpacking/`（§3 白名单）。
- **`ObItemType`**：
  - build.rs 读头文件，生成 `#[repr(transparent)] pub struct ObItemType(pub i32)`，加同名关联常量和模块级常量（`pub const T_INT: ObItemType = …`，随 crate 根导出，引用方照 C++ 不加限定地写 `T_INT`）。
  - 头文件有两份定义，取值不同：`src/objit/include/objit/common/ob_item_type.h` 和 `src/query/api/query/parser/ob_item_type.h`（`src/sql/parser/ob_item_type.h` 只是转发）。两份用同一个 include guard，每个编译单元里先被包含的那份生效。例如 4920 在 objit 那份里是 `T_MAX`，在 query 那份里是 `T_FLUSH_PRIVILEGES`；objit 那份还多出 `T_AUTO_PARTITION`（4440）等值。
  - 生成器以解析器和 server 实际生效的那一份为准，由 1d 用预处理结果确认；另一份里多出或取值不同的枚举值，以及让它生效的编译单元，列进缺口清单逐个判定。
  - 原来的头文件单元处置为 `generate`。
- **内部表**：保留 Python DSL，只换成 Rust 发射器，输出静态的列描述数组，再加一个通用的构建函数。现在配置阶段会生成 104,889 行 C++，改成数据表后估计 2–4 万行。
- **删除 4 个只为 C++ 服务的生成器**：str_datum 和 str_cmp 两个拆分编译单元的生成器、`gen_type_name.sh`（改由枚举派生名字表）、syspack（改用 `include_str!`）。
- **共存期间**：每个生成器从同一份源头同时输出 C++ 和 Rust，保证错误码、系统变量 id、内部表 id 在两种语言里一致。
- **数据表按"数据/代码"拆开**，不交给翻译者：
  - 数据部分由转换脚本写进 `rust/port/<crate>_data/`，大块数据用 `include_bytes!`；
  - 代码部分照常作为单元翻译（处置 `translate`），目标文件里 `pub use <crate>_data::…`；
  - 范围：`src/storage/fts/dict/ob_ik_dic.cpp`（27.6 万行）、`ob_timezone_info.cpp`（约 4 万行）、字符集表（文件逐个列进 `units.tsv`）、`ob_uctype.h`，以及 16 个 `.map`、4 个 `.def` 表格。
- **生成物的位置**：脚本和 build.rs 的产物一律写到 `rust/port/<crate>/src/generated/`，不进 manifest，由单元文件 `include!` 或 `pub use`（§4）。

### 0.14 第三方库【D11】

**判据**：库的输出（结果、报错文本、落盘字节）是否就是它的算法本身。是，就保留原生库，加一层薄封装；否，或者能用测试向量证明等价，就换成纯 Rust。

| 库 | 做法 |
|---|---|
| Boost.Geometry 1.74 | 保留。`bgk_shim`（cxx）里放：<br>• OB 自己的 bin/tree 几何类型和它们的 traits 注册（逆时针、闭合，`ob_geo_bin_traits.h`、`ob_geo_tree_traits.h`），原样复制；<br>• OB 的 WKB 写出逻辑；<br>• 约 100 个算法入口，WKB 字节进出；<br>• 异常映射整段照搬 `ob_boost_geometry_exception_handle`（`ob_geo_dispatcher.h:1313-1362`，17 个分支，顺序不变）。<br>编排逻辑译成 Rust |
| Boost.Spirit（SRS WKT 解析） | 保留，放进 `bgk_shim`：用户自定义 SRS 的数值解析取决于 `qi::real_parser` 的舍入 |
| ICU 69.1（只用 uregex） | 保留。直接声明带 `_69` 后缀的符号，约 20 个函数，外面包一层 RAII |
| libxml2、s2geometry、vsag、SQLite | 保留。libxml2 用离线 bindgen 生成 -sys，版本按平台与基线一致；s2 和 vsag 用 cxx shim；SQLite 用 `rusqlite`，链接同一份库 |
| zstd 1.3.8（仓内，OceanBase 补丁版）、zlib 1.2.13 | 保留，字节兼容。zstd 原样放进 -sys crate；zlib 从同一份 1.2.13 源码静态构建，启动时断言 `zlibVersion()` |
| OpenSSL 1.1.1u | 保留（`openssl_sys`，链接 `deps/3rd` 的同一份构建）：SQL 的加解密和摘要函数（含 `DES_fcrypt`、`EVP_BytesToKey`、`RAND_bytes`、cfb1），以及 libcurl 的 TLS。MySQL 协议的 TLS 仍用 rustls（`sql-nio` 已在用） |
| libcurl 8.12.1 | 保留（`curl_sys`）：CURLcode 数值、URL 判定、代理环境变量、根证书来源都与 C++ 相同 |
| gRPC + protobuf C++ | 换成 tonic + prost，只用在 standby |
| protobuf-c 1.4.1（ST_AsMVT） | 换成 prost，放在 share；输出字节用金标准测试比对 |
| CRoaring | 换成 `roaring` crate（只用内存中的 roaring64，不涉及序列化） |
| RapidJSON（打过补丁） | 把 Reader 和它的"普通精度" strtod 移植到 Rust；`GetParseError_En` 的文本逐字复制；差分参照是按 D17 的浮点设置编译的 C++ shim |
| xxHash | `xxhash-rust`，用金标准测试证明等价 |
| fast_float | 移植 `from_chars` 的契约（§2.5） |
| `<regex>`（JSON schema 的 pattern） | 保留平台的 `std::regex`，经 `regex_shim`（cxx）调用：ECMAScript 方言、按字节匹配、主体遇 NUL 截断，都与 C++ 相同 |
| libaio、libunwind | libaio 用 5 个函数的 FFI；libunwind 用 `backtrace` |
| tzcode、libev 头文件、libeasy 残留 | 删除：tzcode 不参与编译，libev 和 libeasy 只剩残留头文件 |

- **回调规则**（libxml2 的 15 个 SAX 回调、vsag 的分配器、zstd/zlib 的分配钩子等）：
  - 分两层：-sys 或 shim 里的 `extern "C"` 蹦床只把指针转换成安全类型（`&'a [u8]`、`&'a T`），立即调用一个安全的 trait 方法；业务逻辑（如 `ob_libxml2_sax_handler.cpp` 的 1,029 行）写在 oblib 的安全代码里；
  - 回调上下文类型必须 `Sync`，蹦床里用 `fn assert_sync<T: Sync>()` 做静态断言；
  - 可能被外部线程调用的回调，不得访问非 `const` 初始化的线程局部（0.5）；
  - 分配钩子只接到原子计数的 `MemBudget` 加全局分配器，不接 bump arena。
- **版本和构建参数**：每个保留的库写明版本（`migration/design/third_party_versions.tsv`，1d 产出），能查询版本的在启动时断言；shim 和 -sys 的 C/C++ 代码用与基线相同的编译器和参数编译（clang 17.0.6、`-O2`，arm64 上 `-march=armv8-a+crc+lse`），浮点收缩按 D17。
- **unsafe 估计**：手写 unsafe 约 200–350 处，集中在约 12 个 -sys、shim 和视图 crate 里。

### 0.15 目标平台与对齐基准【D17】【D18】

- **目标平台**：与 C++ 的 CMake 一致（`CMakeLists.txt:179-196`），共 6 个：Linux x86-64、Linux aarch64、macOS x86-64、macOS arm64、Windows x64、Android arm64-v8a。
  - 每个平台的条件分支都要翻译，不能只译基准平台：`_WIN32`/`_MSC_VER` 595 行（264 个文件）、`__ANDROID__` 39 行、`__APPLE__` 151 行。
  - 推荐（D18）：6 个平台都翻译；Linux x86-64 和 macOS arm64 必须能构建、运行；其余平台在 CI 里至少 `cargo check`。裁判对齐在 macOS arm64（基准）和 Linux x86-64 上做；Windows、Android 的对齐放到第 6 步之后（A.3）。
- **对齐基准构建**：
  - C++：macOS arm64，`RelWithDebInfo -O2`，`-DNDEBUG -DOB_BUILD_SYS_VEC_IDX -D_NO_EXCEPTION`，不定义 `ENABLE_DEBUG_LOG`（取自 `build_release/compile_commands.json`），MacOSX15.sdk（15.4）；浮点收缩按 D17；
  - Rust：`release` profile，debug-assertions 和 overflow-checks 都关闭，`OB_BUILD_SYS_VEC_IDX` 对应的 feature 打开；**不用** `cmake-debug` profile（opt-level 0 可能撞上裁判单条语句 10 秒的超时）。
- **浮点乘加融合【D17】**：
  - 现状：基线没有 `-ffp-contract` 参数，Clang 默认融合同一表达式里的 `a*b+c`；但 s2 的 `_fp_contract_off.h` 里有 `#pragma STDC FP_CONTRACT OFF`，经 `ob_s2adapter.h` 进入包含 `ob_sql_utils.h`、`ob_tablet_scan.h`、`ob_geo_utils.h`、`ob_query_range_provider.h` 的编译单元，在 unity 构建下还会波及同一批次里排在后面的文件。结果是融合与否逐函数不同：基线里 422 个 OB 相关函数含 FMA 指令，`ObExprBM25::doc_token_weight` 却没有融合。x86-64 构建没有 `-march`，C++ 在那里本来就不融合。
  - 用户可见的后果：`JSON_SCHEMA_VALID('{"multipleOf":0.1}', '1.1')` 的结论翻转；17 位以上的 JSON 数值末位不同；GIS 距离、IVF 检索的临界结果不同。
  - 推荐 (b)：用 `-ffp-contract=off` 重建 macOS arm64 的 C++ 参照，重跑裁判，只重录因此改变的期望行，逐行记下原因（DEV-016）；Rust 一律不写 `mul_add`，shim 和 -sys 也用 `-ffp-contract=off` 编译。这样 arm64 与 x86-64 一致，也省掉逐处维护清单。
  - 备选 (a)：以现有基线二进制为准，脚本把含 FMA 的函数映射回源码表达式，生成"融合清单"；Rust 只在清单列出的表达式上写 `f64::mul_add`/`f32::mul_add`，标 `PARITY(port): fp-contract`；shim 逐个编译单元复刻 pragma 状态，CI 按符号比对 FMA 指令数。
- **预处理条件**：
  - `NDEBUG` → `cfg(debug_assertions)` 取反；
  - `ENABLE_DEBUG_LOG`、`ENABLE_SANITY`、`OB_BUILD_*`、`ERRSIM` → 同名的 cargo feature；
  - 平台和架构 → `cfg(target_os = …)`、`cfg(target_arch = …)`；
  - 每个分支都译。
- **性能带**：见 A.3。

## 1. 生态取舍【D13】

| 领域 | 决定 |
|---|---|
| 异步 | **禁用**。翻译代码里不得出现 `async fn`、tokio、async-std。唯一例外：standby 的 tonic/tokio，限定在 standby 内部 |
| 错误 | 自有 `ObError`：`#[repr(transparent)] ObError(NonZeroI32)`，4 字节；`ObResult<T = ()>`。常量由生成器产出，沿用 C++ 名字，如 `OB_ITER_END`。不用 thiserror、anyhow |
| 日志 | 自有 `oblib::log`：宏加运行时，包括 111 个模块的模块表、线程级级别表、限流、异步写、轮转、压缩，日志行格式与 C++ 一致。写线程由组合根显式启动（0.5）。翻译代码禁用 `log`、`tracing`、`eprintln!` |
| 格式化 | 自有 proc-macro `ob_printf!`：编译期解析 C 格式串、检查参数类型，运行时逐字节复刻目标平台 C 库的输出。`databuff_printf!`、`buf_printf!`、`append_fmt!`、`log_*_fmt!` 都建在它上面。用户可见的文本禁止用 `format!` 生成 |
| 容器 | 存储类型：`Vec`、`Box<[T]>`、`SmallVec`、`ArenaVec`，按拥有者的生命周期类别选（§2.5）。一律通过 `oblib::container::ObIArray<T>` trait 访问。`ObHashMap`/`ObHashSet`/`ObSortedVector`/位集在 oblib 里逐行移植；并发容器用 oblib 的封装（0.8）。翻译代码禁用 std `HashMap`/`HashSet`、hashbrown、indexmap、dashmap、`BinaryHeap`。`BTreeMap`/`BTreeSet` 只用来对应 C++ 的 `std::map`/`std::set` |
| 排序 | `oblib::sort::ob_sort` 逐行移植基准平台的 `std::sort`，堆算法同理（0.11）。禁用 `sort_unstable*`；`sort_by` 只用来对应 `std::stable_sort` |
| 哈希、校验和 | murmurhash、fnv、crc64（含 SSE4.2 路径）逐行移植；xxHash 用 xxhash-rust，前提是金标准测试证明等价 |
| 字符集、时间、数值、浮点文本 | 逐行移植。不得用 chrono、time、encoding_rs、icu4x、rust_decimal、num-bigint、ryu 实现 SQL 语义；`str::parse` 不得用于 SQL 语义的数值解析 |
| 时间源、随机数 | 移植 `ObTimeUtility`、`ObClockGenerator`、`ObRandom` 和 MySQL 的 `RAND(seed)`。翻译代码不直接用 `std::time::SystemTime`，也不用 `rand`。C++ 里直接调用 libc `rand()`/`srand(time(0))`、`std::shuffle`/`std::random_shuffle` 的地方（结果本来就不确定），统一译成以时间为种子的 `ObRandom`，注释写明"原本就不确定" |
| 序列化 | 自有 `oblib::serialization`（`macro_rules!` 加 trait），逐字节复刻 unis 格式。serde 只允许用在 build.rs 里 |
| 锁、原子、无锁结构、并发容器、队列 | `oblib::sync`、`oblib::atomic`、`oblib::hash`、`oblib::queue`（0.8、§2.7）。parking_lot_core、crossbeam、dashmap、portable-atomic 只允许出现在这几个模块和 `storage_core` 内部；`arc-swap` 可以直接用 |
| 栈 | `psm`，包在 `oblib::thread::stack` 里（0.8） |
| 分配器 | 见 0.7 |
| SIMD、汇编 | 用 `std::arch` 里同名的 intrinsic，配 `#[target_feature]` 和运行时检测，只能写在各 crate 的 `simd` 模块里，业务代码调用它提供的安全封装；`asm!` 只能写在 `oblib::arch`（如 rdtsc、aarch64 的 `yield`）；不引入 SIMD crate |
| FFI | Rust 导出走 cbindgen 生成的 C ABI；调用 C++ 走 cxx；调用 C 库用离线 bindgen 生成并入库。保持 `panic = "abort"`，C++ 异常一律在 shim 里接住；回调按 0.14 的两层规则 |
| 依赖白名单 | 下表点名的 crate，版本按工具链的做法钉死。**表外的一律不准引入**，新增要走修订 |

| 用途 | 允许的 crate |
|---|---|
| 同步、并发（只在 oblib 的 `sync`/`atomic`/`hash`/`queue` 和 `storage_core` 内部） | parking_lot_core、crossbeam-utils、crossbeam-queue、crossbeam-channel、dashmap、portable-atomic |
| 同步（翻译代码可直接用） | arc-swap |
| 栈、信号、系统调用 | psm、libc |
| 内存、存储结构 | bumpalo、smallvec、slab、slotmap |
| 宏、FFI、构建 | syn、quote、proc-macro2（只用于 `ob_macros`）；cxx、cxx-build、cc、cbindgen、bindgen（离线）；serde、serde_json（只作 build-dependencies） |
| 第三方替代品 | tonic、prost、prost-build、tonic-build、tokio（只限 standby）、roaring、rustls（已在用）、rusqlite、libsqlite3-sys、xxhash-rust、backtrace |
| 自有 -sys（链接 `deps/3rd` 或 `deps/external` 的同一份构建） | seekdb-jemalloc-sys、zstd138_sys、icu69_sys、libxml2_sys、openssl_sys、curl_sys、libaio_sys、zlib 的静态构建 |

## 2. 无对应构造的统一译法

每个源构造只有一种译法。实现者给已列出的构造发明第二种译法，就是违反规则；审查者看到表里没有的构造，要提交修订请求，补进这张表。

### 2.1 错误与控制流【D12】

**函数体逐行保留 C++ 的 if/else 链，翻译阶段禁用 `?`。**

- 理由：声明 `ret` 的 39,077 个函数里，约 27% 在失败后还会读写 `ret`，或者继续往下执行（重置、按错误码分派、`tmp_ret`、清理）。在这些函数里，`?` 和原语义不等价。
- 对齐通过后：约 73% 的函数失败后只是原样上抛，可以由脚本打标签，再机械地改成 `?`。这个标签不属于 1c 的范围。

| C++ | Rust |
|---|---|
| 返回 `ret` 的 `int f(…, T &out)` | `fn f(…, out: &mut T) -> ObResult`。输出参数保持 `&mut`，不改成返回值，因为失败时写了一半的输出，调用方仍可能读到 |
| `int ret = OB_SUCCESS;` / `INIT_SUCC(ret)` | `let mut ret: ObResult = Ok(());` |
| `OB_FAIL(e)` / `OB_SUCC(e)` / `OB_TMP_FAIL(e)` | `ob_fail!(ret = e)` / `ob_succ!(ret = e)` / `ob_fail!(tmp_ret = e)`，展开为 `{ ret = e; ret.is_err() }`；if/else 链的结构原样保留 |
| `OB_FAIL(ret)` / `OB_SUCC(ret)` / `OB_SUCCESS != ret` | `ret.is_err()` / `ret.is_ok()` / `ret.is_err()` |
| `ret = OB_X;` / `return OB_X;` / `return ret;` | `ret = Err(OB_X);` / `return Err(OB_X);` / `return ret;` |
| `OB_X == ret` / `ret != OB_X` | `ret.is(OB_X)` / `!ret.is(OB_X)`，由 `ObResultExt` 提供 |
| `ret = OB_SUCCESS;`（吞掉错误） | `ret = Ok(());` |
| `COVER_SUCC(x)` / `FAILEDx(e)` | `cover_succ!(ret, x)` / `failedx!(ret = e)`，语义同宏定义（`ob_macro_utils.h:451,653`） |
| `CK` / `OX` / `OZ` / `OV` / `OZX1` / `OZX2` / `FALSE_IT` | 同名小写宏，**显式传入 `ret`**，如 `ck!(ret, c1, c2)`、`oz!(ret, f(a); a)`。`false_it!(stmt)` 等价于 `{ stmt; false }`。Rust 宏有卫生规则，不能像 C++ 那样隐式捕获局部变量 |
| `OB_ISNULL(p)` / `OB_NOT_NULL(p)` | C++ 的指针一律译成 `Option`（§2.4），写成 `p.is_none()`/`p.is_some()`，检查原样保留；C++ 的引用译成 `&T`，本来就没有检查 |
| `IS_NOT_INIT`、`is_inited_`、`int init(…)` | 保留两段式初始化：字段 `is_inited_`、`fn init(…) -> ObResult`；接收者按 §2.13：未发布的对象 `&mut self`、`is_inited_: bool`；模块和共享对象 `&self`、`is_inited_: AtomicBool`（0.5）。构造函数改成不可失败的 `new()`/`Default` |
| `OB_ASSERT` / `abort_unless` / `ob_abort` | `ob_assert!(x)`：记 ERROR 日志和调用栈后 `std::process::abort()`，release 构建也一样 |
| `OB_E(EN_X) OB_SUCCESS`、`ERRSIM_POINT_DEF` | `ob_e!(EN_X)`、`errsim_point!(…)`，保留注入点，用 `cfg(feature = "errsim")` 控制 |
| throw / try / catch（118 处） | 内部的 throw 改成 `return Err(..)`；第三方 C++ 异常在 shim 里 catch 后转成错误码；`catch (...) { 恢复; throw; }` 改成用 RAII 守卫恢复现场 |
| `int` 返回值是值而不是错误码的函数 | 由缺口清单第 6 类按函数打标签，翻译时照标签处理 |

**错误恢复规则**（只约束翻译时新写出来的分支；逐行翻译 C++ 的分支服从 0.1.4，C++ 本来就共用哨兵值的写法照样保留，如 0.7 的 `alloc(0)`）：
- 分配失败和结构性错误在 C++ 里经常共用一个哨兵值，到新写的 Rust 分支里不得共用一个构造：
  - 分配失败一律是 `OB_ALLOCATE_MEMORY_FAILED`；
  - 结构性错误按原函数的错误码和部分结果返回。
- 容错转换（warn-on-fail）**不得吞掉内存错误**，判断用 `e.is_unrecoverable()`，对应 C++ 的 `CM_IS_WARN_ON_FAIL && ret != OB_ALLOCATE_MEMORY_FAILED`。

### 2.2 日志

| C++ | Rust |
|---|---|
| `LOG_WARN`/`LOG_ERROR(msg, kv…)` | `log_warn!(ret, "msg", kv…)`，错误码显式传入（C++ 隐式读 `ret`，`ob_log_module.h:262`） |
| `LOG_INFO`/`LOG_DEBUG`/`LOG_TRACE(msg, kv…)` | `log_info!("msg", kv…)` 等；这三级在 C++ 里错误码固定为 0 |
| `LOG_WARN_RET(OB_X, …)`、`XXX_LOG_RET(level, OB_X, …)`、`XXX_LOG(LEVEL, …)` | `log_warn!(OB_X, …)`、`ob_log!(XXX, LEVEL, OB_X, …)`、`ob_log!(XXX, LEVEL, ret, …)`；错误码参数既接受 `ObResult` 也接受 `ObError` |
| `#define USING_LOG_PREFIX P` | 模块顶部写 `const LOG_PREFIX: ObLogMod = ObLogMod::P;`，`log_*!` 在调用处解析这个名字 |
| 单元里没有 `USING_LOG_PREFIX`、却调用了 `LOG_*`（11 个单元，如 `ob_expr`、`ob_define`、`ob_trace`） | `const LOG_PREFIX: ObLogMod = ObLogMod::COMMON;`，标 `DIVERGE(port): log-prefix`。C++ 里头文件内联函数的前缀取决于包含它的 `.cpp` |
| `_LOG_*`、`_XXX_LOG`（printf 式） | `log_warn_fmt!(ret, "C 格式串", args…)`，走 `ob_printf!` |
| `K(x)` / `K_(x)` / `KR(x)` / `KP(x)` / `KPC(x)` / `"k", v` | `k!(x)` / `kv!("x", self.x_)` / `kr!(x)` / `kp!(x)` / `kpc!(x)` / `kv!("k", v)`；值都走 `ObToString` |
| 持锁区域内打印本对象（`KPC(this)` 共 1,217 处，如 `ob_tx_ctx.cpp:587-642` 在 `CtxLockGuard` 下打印 6 次） | 打印 guard 里的数据：`kpc!(*g)`，锁外字段另行列出。不得打印 `self`：它的 `ObToString` 会尝试取同一把锁 |
| `REACH_TIME_INTERVAL(t)` / `REACH_COUNT_INTERVAL(n)` / `REACH_THREAD_TIME_INTERVAL(t)` | `reach_time_interval!(t)` / `reach_count_interval!(n)` 在每个调用点放一个 `static AtomicI64`；`reach_thread_time_interval!(t)` 用 `thread_local!` 里的 `Cell<i64>` |
| `LOG_DBA_*`、`NG_TRACE`、`REC_TRACE`、`FLT_*` | 同名小写宏 |

**持有锁内数据的类型怎么打印**：`ObToString` 只直接打印锁外字段；锁内部分用 `try_lock()` 读，拿不到（包括本线程自己持有）时打印 `<locked>`。任何 `to_string` 都不得阻塞加锁。C++ 的 `TO_STRING_KV` 是不加锁的脏读，这只改变日志内容，日志不参与比较。

### 2.3 用户消息与格式化

- **`LOG_USER_ERROR/WARN/NOTE(OB_X, args…)`** 改成 `log_user_error!(OB_X, args…)`，它调用生成器产出的 `user_msg::OB_X(level, args…)`：
  - **参数类型由格式串决定**：`%.*s` → `(i32, &[u8])`，`%s` → `&[u8]`，`%ld` → `c_long`，`%d` → `i32`，`%u` → `u32`，`%lu` → `c_ulong`，`%lld` → `i64`，`%f`/`%lf` → `f64`，`%c` → `i32`；
  - **输出逐字节等于 `vsnprintf(buf, 512, …)`**：截到 511 字节；`%s`/`%.*s` 遇到 NUL 即停；`%.*s` 和 `%.Ns` 按字节截断，可能截断多字节字符，所以消息缓冲用 `Vec<u8>`，不用 `String`；
  - **inf/nan 按目标平台 C 库的拼写**：macOS 一律 `nan`（负 NaN 也是），glibc 对负 NaN 写 `-nan`；`ob_printf!` 用 cfg 区分，金标准测试覆盖；
  - **结果写进线程局部的警告缓冲区**：每条最多 512 字节，共 64 条，环形覆盖；
  - 给 `ret` 赋值仍然单独写一句。
- **错误包**：`send_error_packet`（`obmp_packet_sender.cpp:536-653`）逐行翻译，不按摘要实现。它的规则包括：
  - 警告缓冲区里记录的错误码等于返回的错误码时，才用缓冲区的文本，否则用默认文本；
  - `OB_ERR_SIGNAL_EXCEPTION`（PL 的 SIGNAL/RESIGNAL）不论错误码是否相等都用缓冲区的文本，文本为空时用调用方传入的 `errmsg`；errno 和 SQLSTATE 也取自警告缓冲区。
  - **消息不得塞进错误值里。**
- **其他宏**：`FORWARD_USER_*` 和 `LOG_USER_ERROR_WITH_LINE_COL` 改成同名小写宏。
- **格式化写缓冲**：`databuff_printf`、`BUF_PRINTF`、`snprintf` 写进定长缓冲的，改成 `databuff_printf!` 或 `buf_printf!`。返回值和 `pos` 的语义照旧，溢出时返回 `OB_SIZE_OVERFLOW`。

### 2.4 内存与所有权

| C++ | Rust |
|---|---|
| `if (OB_ISNULL(buf = alloc.alloc(sz))) { ret = OB_ALLOCATE_MEMORY_FAILED; } else { p = new (buf) T(..); }` | 查询作用域：`arena.try_alloc(T::new(..))`（`T` 不能需要 Drop）；拥有型：`try_box(T::new(..))`。结果交给 `ob_fail!` 链 |
| `OB_NEW(T, label, ..)` / `OB_NEWx(T, pool, ..)` | `try_box(..)`，标签删掉；`OB_NEWx` 按 pool 的层级（清单第 11 类）选 `arena.try_alloc` 或 `try_box` |
| `OB_DELETE`、`x->~T(); alloc.free(x);` | 交给 Drop；需要显式析构的类型本来就不在 bump arena 里（0.7）。对模块对象的 `ob_delete` 见 0.5 |
| `ob_write_string(alloc, src, dst)` / `deep_copy(alloc, ..)` | `arena.try_str(src)`；拥有型用 `Clone`，必要时先 `try_reserve` |
| `ObString` 成员 | 查询作用域用 `ObStr<'q>`（即 `&'q [u8]`），拥有型用 `Box<[u8]>`；按 `types.tsv` 的生命周期类别 |
| `ObArenaAllocator` 成员或局部变量（成员 382 个） | 层级由清单第 11 类给出：请求或语句级 → 调用方传入的 `&'q Arena`；算子批次级 → 帧模块存储表里的批次 arena（0.6）；跨调用存活的 → 0.7 的 (b) |
| `allocator.reset()` / `reuse()` | `arena.reset()`（需要 `&mut`，此时不能有存活的借用） |
| `CHECK_MEM_STATUS()` | `mem_ctx.check_status()`，保留在原来的调用点 |
| `ObMallocHookAttrGuard`、`ObMemAttr` 参数、内存标签 | 删掉（0.7） |
| `CURRENT_CONTEXT`/`WITH_CONTEXT` | `MemCtx` 显式传参；绑定在当前上下文上的容器改用全局堆 |
| `CREATE_WITH_TEMP_CONTEXT`（21 处） | (a) worker 和请求入口（4 个文件）→ 新建请求 `MemCtx`；(b) SQL 编译期、建了 `ObRawExprFactory` 的 → `qa.scope()`；(c) 其余 → 块内的局部 `Arena`。见 0.6 |
| `ObIAllocator &`/`*` 参数（带分配器参数的签名 7,147 个） | 查询作用域的函数：`arena: &'q Arena`；拥有型的删掉参数。按 `types.tsv` 和清单判定 |
| 借用分配器的成员（`ObIAllocator *x_` 309 个、`&x_` 200 个） | 查询作用域：`&'q Arena`；拥有型：删掉 |
| `SMART_VAR`/`HEAP_VAR` | 堆上的 `Box` |
| 普通指针参数、返回指针的方法（头文件里约 8,590 个指针参数、2,227 个返回指针的方法） | const → `Option<&T>`，非 const → `Option<&mut T>`；转移所有权的（被调方 new、调用方 delete）→ `Box<T>`，由清单第 2 类标出 |
| C++ 的引用参数 | `&T` / `&mut T` |
| 引用成员 `T &x_`（约 1,200 个，如 `ob_expr.h:278` 的 `ObExecContext &exec_ctx_`） | 查询作用域 → `&'q T`；指向模块 → `&'static T`；其余 → id 或 `Arc<T>`；按 `types.tsv` |
| 裸指针成员（6,120 个） | 由清单第 3 类逐个分类，在以下几种里选一种：id（0.6）、`&'q T`、`Box<T>`、`Arc<T>`、`Weak<T>`、`&'static T`（模块）、以上各项的 `Option`、类型擦除的 `Box<dyn Any + Send + Sync>`（跨层 pimpl，0.4）、enum，或者删除（宿主回指，见下）。`NonNull<T>` 只允许出现在 §3 白名单模块里 |
| 宿主或父对象的回指针 | 删掉字段，改成调用时传参（`fn f(&mut self, host: &Host)`），或者把被回访的状态搬到宿主之外，由双方用 id 访问（执行期见 0.6 的 `ObExecContext` 表）。定时任务见 §2.7 |
| 指向自身或兄弟成员的指针 | 0.7 的 (c) |
| `T *&` 输出参数（在被调方分配，9,454 处） | `out: &mut Option<&'q T>` 或 `out: &mut Option<Box<T>>`，按清单第 2 类 |
| 返回指向静态或线程局部轮转缓冲的指针（`ObNumber::format()` 33 处调用、`ObLogCursor::to_str()`、`ObLogger::show_probe()`） | 改成写进调用方的缓冲（`&mut ObBuf`），或返回拥有型的小字符串，输出字节不变。依赖"缓冲之后会被覆盖"的原版行为属于未定义行为，标 `FIXED(port)` |
| 就地改写借来的字节（`ObCharset::casedn/caseup` 36 处、`const_cast<char *>` 509 处中的一部分，如 `ob_alter_system_resolver.cpp:607-609`、`ob_sql_utils.cpp:3860-3863`、`ob_prepare_stmt_struct.cpp:371`） | 字节归写的一方所有的，直接用 `&mut [u8]`。借来的（解析树、别处的 ObString）：**拷贝、修改、改指**，即在同一 arena 里分配修改后的副本，更新本地视图，并把所有者（节点的 setter、ObString 成员）改指到副本。清单第 18 类逐处列出，并记下改写之前是否已有别的视图会在之后读到；有的话一并改指 |
| 由元素地址反推下标（`ob_aggregate_processor.h:1129` 的 `&info - aggr_infos_.get_data()`、`ob_expr_frame_info.cpp:143,155,405`） | 随身携带下标（`enumerate`，或在元素里存下标） |

### 2.5 容器与字符串

| C++ | Rust |
|---|---|
| `ObSEArray<T, N>` / `ObArray<T>` / `ObFixedArray<T>` | 按拥有者的生命周期类别（`types.tsv`）：<br>• 计划、缓存、会话等长生命周期 → `Vec<T>`/`Box<[T]>`，`ObSEArray` 用 `SmallVec<[T; N]>`；<br>• 查询作用域、对象本身由 bump arena 分配 → 增长期 `ArenaVec<'q, T>`，定型后 `&'q [T]`（`ObSEArray` 成员同样处理）；<br>• `QueryArena` 里的 IR 节点 → `SmallVec<[T; N]>`/`Vec<T>`。<br>一律通过 `ObIArray<T>` trait 的同名方法访问：`push_back(v) -> ObResult`（内部 `try_reserve`）、`count() -> i64`、`at(i)`、`reset()`、`assign(&other) -> ObResult` |
| 参数 `const ObIArray<T> &` / `ObIArray<T> &` | `&[T]` / `&mut dyn ObIArray<T>` |
| `arr.at(i)` / `arr[i]` / `arr.at(i, obj)` | `arr.at(i)`：越界 abort，与 `OB_ASSERT` 一致 / 同前（C++ 的 `operator[]` 越界只记日志，属于未定义行为；前提见 A.3）/ `arr.at_into(i, &mut obj) -> ObResult`，越界返回 `OB_ARRAY_OUT_OF_RANGE` |
| `ObHashMap<K, V>` / `ObHashSet<K>` | oblib 里逐行移植的同名类型：桶数、`%` 取桶、头插、可失败的哈希函数都不变。6 种锁模式（defend mode）作为类型参数移植，默认是每桶一把读写 latch；`foreach_refactored`、`atomic_refactored`、`read_atomic` 的回调照 C++ 在桶锁内执行，回调不得访问同一张表。方法名不改 |
| SQL 引擎自带的哈希表（`ObExtendHashTable`、`ObHashPartInfrastructure` 等） | 逐行移植：桶数、增长策略、哈希函数都不变，因为它们决定输出的行序 |
| 以对象地址为键的表（如 `ObRawExprCopier` 的映射表） | `ObHashMap<Id, …>`。需要迭代、且顺序会进入结果的，按插入顺序存一份 `Vec<Id>`，标 `DIVERGE(port): addr-order`（0.6） |
| `ObLinkHashMap`、`ObLinearHashMap`、`FixedHash2` | oblib 的同名封装（§2.7） |
| `ObList<T>` / `ObSortedVector<T>` / `ObVector<T>` / 位集（含 `ObFixedBitSet`） | oblib 里的同名类型，底层分别是 `VecDeque`、`Vec`、`[u64]`；`lower_bound` 等语义照旧 |
| `ObDList<T>` + `ObDLinkBase` | arena 里的链表用 id 串起来；只读计划里一律 `Vec`；其他地方用 oblib 的侵入式链表（白名单模块，§3）。侵入式链表的契约：链表拥有节点（`Box`/`Arc` 适配），或者节点被 `Pin` 且宿主 Drop 时自动摘链；只用于单一归属的对象。`ObExprOperator` 的这个基类直接去掉 |
| `std::map` / `std::set` / `std::vector` / `std::string` | `BTreeMap` / `BTreeSet` / `Vec` / `Vec<u8>` |
| 以浮点数为键的 `std::map`/`std::set`（如 `ob_expr_map.cpp:235-252,297-305`） | 键用 `oblib::num::CppFloatKey<f64>`：复刻 C++ `operator<` 的等价类（−0.0 等于 +0.0；NaN 的处理照基线实测），禁用 `total_cmp` |
| `std::sort`、`lib::ob_sort` / `std::stable_sort` / `std::push_heap` 等 | `ob_sort(slice, cmp)` / `slice.sort_by(cmp)` / `oblib::sort::{push_heap, pop_heap, make_heap, sort_heap}`（0.11） |
| `ObString` 的方法（`length()`、`ptr()`、`case_compare()`、`prefix_match()` 等） | `[u8]` 上的扩展 trait `ObStringExt`，方法名不变 |
| `ObString` 当写缓冲用（`assign_buffer`、`write`） | `oblib::string::ObStringWriter<'a>`（`&'a mut [u8]` 加长度） |
| `const char *` | 内部用 `&[u8]`，不假定是 UTF-8，不带结尾的 NUL；FFI 边界用 `&CStr`；`&str` 只用于字面量 |
| `char buf[N]` + `snprintf` | `[u8; N]` + `databuff_printf!` |
| `ObSqlString` / `ObStringBuffer` | oblib 里的同名类型，追加操作可失败；`append_fmt!` 走 `ob_printf!` |
| `strcmp`/`memcmp` / `strcasecmp` | 切片比较，顺序与 memcmp 相同 / `ob_strcasecmp`，按 C locale 只折叠 ASCII |
| 和排序规则有关的比较、哈希、大小写转换 | 一律调用移植后的 `ObCharset::*`；不得用 `str::cmp`、`to_lowercase`、`eq_ignore_ascii_case` 代替 |
| `isspace` 等 ctype 函数 | `oblib::ctype::isspace` 等，按 C locale 实现，`isspace` 包含 `\v` |
| `ob_strtol`/`ob_strtod`（MySQL 的 dtoa 一族） | oblib 里逐行移植：跳过前导空白、允许只解析一部分；`ob_strtod` 溢出时返回 `DBL_MAX` 并报错 |
| C 库的 `strtod`/`strtol`/`strtoull`/`atof`/`atoi`（如 `ob_query_parse.cpp:1984,2207`、`ob_expr_st_buffer.cpp:597`） | `oblib::num::c_strtod` 等：调用平台的 C 库（在白名单模块里拷成以 NUL 结尾的缓冲），返回值和结束位置与 C 完全相同，即上溢返回 ±HUGE_VAL，接受十六进制浮点、`inf`、`nan` |
| fast_float 的 `from_chars`（如 `ob_array_cast.cpp:705-713,989-1003`） | `oblib::num::fast_float_from_chars`：复刻它的契约（前缀解析、返回结束位置和 `errc`、上溢和下溢都置 `result_out_of_range`、接受 inf/nan），数值部分用 core 里正确舍入的解析 |

### 2.6 SQL IR（R1–R9）

- **R1**：`ObRawExpr *` 改成 `ExprId`/`Option<ExprId>`，其他图同理。arena 一律显式传参，**不存进 self**：签名（参数、返回值、所属的类）里出现编译期 IR 类型（ObRawExpr、ObDMLStmt、TableItem、ObLogicalOperator、Path 及其派生类，以及它们的工厂和上下文类型）的函数，`self` 之后的第一个参数一律是 `qa: &mut QueryArena`，不区分只读与否。这个条件只看声明就能判断，调用方和被调方必然得出同一个结论；脚本据此生成 `migration/design/qa_signatures.tsv`，随清单一起审查。原来持有工厂的上下文类型，改成和 `qa` 并列传递。
- **R2**：调用 `&mut QueryArena` 期间不得持有 `&RawExpr`，先把需要的 id 或字段拷出来。
- **R3**：槽位参数一律"拷出、调用、立即写回"：表达式槽（`ObRawExpr *&`，1,459 个）用 `set_param`；语句槽（`Ob*Stmt *&`，330 个）用按槽位命名的 setter，如 `qa.set_ref_stmt(e, v)`。标 `SLOT(port): <槽位表达式>`。
- **R4**：地址为键的表改成 id 为键（§2.5）；要迭代的保留插入顺序，标 `DIVERGE(port): addr-order`。
- **R5**：写在节点上的访问标记（`IS_MARKED` 一类）改成 oblib 的 `ObFixedBitSet` 旁表；`rt_expr_` 改成代码生成局部的 `IdVec<ExprId, Option<RtExprId>>`。
- **R6**：`SMART_CALL` 改成 `smart_call!`（0.8）。
- **R7**：visitor 的双分派改成 `walk_expr` 里的 `match`。
- **R8**：语句回收用 `slotmap` 的代际 key；子作用域截断后经墓碑 id 访问返回 `OB_ERR_UNEXPECTED`（0.6）。
- **R9**：向下转型（1,350 处）按 §2.13 的 is-a 规则，译成 `as_<变体蛇形名>()`。

### 2.7 并发

**锁和同步原语**（`oblib::sync`，1d）：

| C++ | Rust |
|---|---|
| `ObSpinLock`、`lib::ObMutex`、`ObLatchMutex` | `Latch<T>`（`ObSpinLock` 名为自旋锁，其实会睡眠） |
| `SpinRWLock`、`ObLatch` | `RwLatch<T>` |
| `DRWLock`（3 个实例） | `ShardedRwLatch<T>`：每个实例 `OB_MAX_CPU_NUM` 个对齐的分片，读者只锁自己 itid 对应的分片，写者锁全部分片 |
| `TCRWLock`（23 处成员，含每个计划缓存节点一把） | `BigReaderLatch<T>`：移植 TCRef，即一张全局的每线程槽表（槽里存锁的地址），加每个实例一个写锁字和一个读计数；实例约 40 字节，与 C++ 同一量级。写锁等读者超时返回 `OB_EAGAIN`（`ob_tc_rwlock.h:194-201`） |
| `ObQSyncLock`（6 个实例） | `QSyncLatch<T>`：每个实例 48 个对齐的读者引用槽（同 `ObQSyncWrapper<48>`），写者先自旋再让出，不设截止时间 |
| `ObQSync` 当静默屏障 | `Quiescence`（0.8） |
| `ObBucketLock` 家族（69 处，各类 guard 156 处） | `BucketLatch`：`RwLatch<()>` 数组，桶数与 C++ 相同，按 `hash % n` 取桶，多桶时排序后按序加锁（`ObMultiBucketLockGuard`），带 Try 和超时的版本照抄。不得因为容器自带锁而删掉桶锁 |
| `ObRecursiveMutex`（21 处） | 见下面的"重入"规则 |
| `ObThreadCond`、`ObMonitor`、`ObUtilMutex`、`pthread_mutex_t`+`pthread_cond_t`、`std::mutex`+`std::condition_variable` | `Monitor<T>`（锁加条件变量）；只有互斥的用 `Latch<T>` |
| `SimpleCond`、`ObLightyCond`、`ObFutex`、`LWaitCond` | `FutexCond`（parking_lot_core 的 park/unpark） |
| `ObRowLatch` | `oblib::sync::bitlock` 的纯 TAS 自旋变体：不 PAUSE、不让出、不睡眠 |
| `ObSmallSpinLock`、`ObByteLock`、`ObPtrSpinLock` | `bitlock` 的退避变体：PAUSE `MaxSpin` 次后，每 `MaxSpin` 次 `usleep(USleep)`，模板实参照抄。锁位和数据仍在同一个字里，全程原子访问；带标签的指针用 strict provenance 的 `map_addr`、`AtomicPtr::fetch_or`。不得用 park |
| 带超时的加锁、`*WithTimeout` guard | `lock_until(deadline) -> ObResult<Guard>`，超时返回 `OB_TIMEOUT` |
| `is_wrlocked_by`、`is_locked_by_self`、`self_locked` | `is_wrlocked_by_self()`；用法见下面的"重入"规则 |
| `CACHE_ALIGNED`（143 处） | `oblib::sync::CacheAligned<T>`，按架构对齐：x86 64 字节、ARM 128 字节，与 `ob_macro_utils.h:617-623` 一致（`crossbeam_utils::CachePadded` 在 x86-64 上是 128 字节，不用） |
| `PAUSE()` | `oblib::sync::pause()`：x86 上是 `_mm_pause`，aarch64 上是 `yield`。不用 `std::hint::spin_loop()`，它在 aarch64 上发 `isb sy` |
| 线程池子类、`run1()` | 实现 `Runnable` trait，交给 `oblib::thread` 的对应类型 |
| `void* task` 的线程池任务 | `SimplePool<T>`，任务的类型是明确的 |

**原子量**：

| C++ | Rust |
|---|---|
| `ATOMIC_LOAD/STORE/SET/TAS/FAA/AAF/INC/DEC/BCAS/CAS/VCAS` | 对应的 `Atomic*` 方法，ordering 一律 `SeqCst` |
| `*_ACQ`/`*_REL`/`*_RLX` | 照原样用 `Acquire`/`Release`/`Relaxed` |
| `MEM_BARRIER` / `WEAK_BARRIER` / `__COMPILER_BARRIER` | `fence(SeqCst)` / `fence(AcqRel)` / `compiler_fence(SeqCst)` |
| `inc_update`/`dec_update`（59 处） | `oblib::atomic::inc_update(&a, x)`/`dec_update`：先 `load`，只有 `x` 更大（更小）时才进入 `compare_exchange` 循环；返回值与 C++ 相同。**禁止** `fetch_max`/`fetch_min`：它们在值不变时也要独占缓存行 |
| `volatile` 标志或计数 | 原子量，ordering 按下面的普通读写规则 |
| 128 位 CAS（`lsn_allocator.cpp` 21 处、`REACH_COUNT_PER_SEC` 2 处） | LSN 分配器用 `portable_atomic::AtomicU128`（0.9）；`REACH_COUNT_PER_SEC` 用 `Latch` |

**原子字段的规则**：

- 凡是被任意 `ATOMIC_*` 访问过的字段，类型都改成原子量：
  - 标量 → `AtomicI64`/`AtomicU32`/`AtomicBool` 等；
  - 指针按角色选：发布或替换后读者无锁读的 → `ArcSwapOption<T>`；由空到非空只设一次的（BCAS 惰性初始化、双重检查）→ `OnceLock<T>` 或 `OnceLock<&'static T>`；指向 arena 或池里对象的 → `AtomicU32` 存 id；`AtomicPtr` 只允许出现在 §3 白名单模块里。
- 同一字段上的普通读写：
  - 在拥有 `&mut self` 的上下文里（构造、发布前的 reset），用 `get_mut()`；
  - 普通读用 `Acquire`，普通写用 `Release`：读出来的值可能被用来访问别的数据（指针、下标、"已初始化"标志、版本号），`Relaxed` 读不建立 happens-before；
  - 只有清单第 8 类标为纯计数、统计或停止标志的，才用 `Relaxed`。
- C++ 里没有原子操作、也没有锁保护的并发普通字段（数据竞争），如 `has_set_stop()` 返回的 `bool&`（约 90 处，`threads.h:104`）、`ObTxDataMemtable::state_`：由清单第 8 类列出，按角色选内存序（停止标志和统计 `Relaxed`；门控访问的状态机 `Acquire`/`Release`），标 `FIXED(port)`。环形缓冲改用 `ArrayQueue` 或槽级锁；`has_set_stop()` 改成 `AtomicBool` 加 `set_stop()`/`has_set_stop()`。
- seqlock（`ob_timer_monitor.cpp:181-187`）：改成 `Latch<TimerRecord>`（诊断路径，频率低）。

**数据放进锁的规则**：字段是否进锁、是否改原子，一律以清单第 7、8 类为准。清单缺行时按 UNKNOWN 处理：标量改原子量，非标量放进锁，标 `TODO(port)`。

1. 只在加锁区域里写的字段，放进锁里的 `T`。
2. 锁外也读的字段：标量改原子量（锁内写、锁外按上面的规则读）；非标量放进锁，锁外的读者也加锁，或者改成 `ArcSwap` 快照。
3. 被**别的对象的锁**保护的字段（如 `ob_tx_redo_submitter.h:190-218` 在调用方持有 TxCtx 的锁时操作其状态）：把字段移进那把锁的 `T`，或者拆出一个由那把锁拥有的状态结构。不改原子量，也不用 `UnsafeCell`。
4. 跨函数持锁（231 处 `unlock`、93 个加锁接口方法）：改成返回 guard，或写成 `with_locked(|g| …)`。返回锁引用的访问函数（如 `ob_tx_data_op.h:151`、`ob_vector_index_cache.h:130`、`ob_sql_memory_manager.h:554`）改成拥有者类型上返回 guard 的方法，方法名写进 `types.tsv`。
5. **重入**：
   - 条件重入（锁库之外 7 处：`ob_ls_meta.cpp:454,491`、`ob_tx_log_operator.h:208,420`、`ob_tx_ctx.cpp:1169`、`ob_trans_define_v4.cpp:571,933`）：外层拿到的 guard 作为参数向下传，内层不再加锁；原来的 `is_locked_by_self()` 判断保留为 `debug_assert!`。无法传 guard 的路径标 `TODO(port)`，由清单第 7 类处理。
   - `ObRecursiveMutex`：同样处理，数据放进一把非重入的 `Latch<T>`，由最外层加锁、向下传 guard。会话的 `query_mutex_`、`thread_data_mutex_` 见 0.5。
6. **带解锁钩子的复合锁**（`CtxLock`：3 把 ObLatch、5 种模式，`before_unlock`/`after_unlock`，`ob_trans_ctx_lock.cpp:161-176`）：guard 的 `Drop` 依次执行 `before_unlock(&mut inner)` → 放锁 → `after_unlock(arg)`；提交回调绝不在锁内执行；多模式锁按 C++ 的模式位组合。
7. **临界区内的日志**：见 §2.2。

**并发容器和队列**（`oblib::hash`、`oblib::queue`，1d；保留 C++ 的名字和方法）：

| C++ | Rust |
|---|---|
| `ObLinkHashMap`、`ObAllocatingLinkHashMap` | `oblib::hash::ObLinkHashMap<K, V>`：值是 `Arc<V>`，`get` 返回克隆，`revert` 就是 drop。`for_each`/`remove_if` 先取快照，按 C++ DCHash 的顺序排序（排序键照 `ob_dchash.h` 移植），释放全部内部锁之后再执行回调；回调里可以删除同一张表的元素（如 `ob_user_resource_mgr.cpp:284-319` 的连接资源清理） |
| `ObLinearHashMap` | `oblib::hash::ObLinearHashMap<K, V>`：`operate`/`insert_or_operate`/`insert_or_update`/`erase_if` 在分片写锁内原子完成，闭包不得访问同一张表；`for_each` 同上取快照 |
| `FixedHash2<ObLockWaitNode>`（锁等待表） | `oblib::hash::FixedHash2`：每个键一个有序集合，按 `(recv_ts, abs_timeout, 插入序)` 排序（C++ 的第四个比较键是请求地址，每次运行都不同，本来就不确定）；唤醒和重新插入照 `fetch_waiter`/`wait`（`ob_lock_wait_mgr.cpp:376-405`）；`check_timeout` 那种遍历中删除照 C++ 允许 |
| `ObHashMap<…, NoPthreadDefendMode>` + 外部 `ObBucketLock` 分段 | `oblib::hash::BucketedHashMap`（0.9） |
| `ObLightyQueue` | `push` 非阻塞，满时返回 `OB_SIZE_OVERFLOW` |
| `LightyQueue` | `push(data, timeout)` 阻塞等待，超时返回 `OB_TIMEOUT`（`ob_lighty_queue.cpp:208-228`） |
| `ObPriorityQueue2`（SQL 请求队列 `ReqQueue`） | 移植：每级一个队列，各级**共享**一个上限计数，高优先级多给 2048 余量、普通优先级多给 1024，满时立即返回 `OB_SIZE_OVERFLOW`（`ob_priority_queue.h:191-225`）；`pop_high`/`pop_normal` 只从前若干级取；按 C++ 的唤醒类等待 |
| `ObFixedQueue`（作队列，或作空闲表） | `oblib::queue::ObFixedQueue`（内部 `ArrayQueue`） |
| `ObSpLinkQueue`、`ObLinkQueue` | `oblib::queue` 的同名类型（内部 `SegQueue`） |

- 翻译代码不直接用 DashMap、crossbeam 的通道和队列。
- 队列项：只挂在一个容器里的对象用 `Box<T>`，所有权随之转移；同时挂在多个容器里的对象（清单第 19 类），由唯一的所有者（slab 或 `Arc`）持有，各容器只存 id 或 `Arc` 克隆。请求在锁等待期间的"挂起"，表达为把所有权移进等待表，唤醒时再移回队列。

**定时任务**：

| C++ | Rust |
|---|---|
| `ObTimerTask&` 裸引用登记 | `Arc<dyn TimerTask>`，由定时服务持有；`run(self: &Arc<Self>)`，任务可以重排自身（如 `ob_user_resource_mgr.cpp:321-330`） |
| 任务里指向宿主的引用 | 宿主是模块的，持有 `&'static`；不是模块的（如每个 ObLS 里的 `ObLockTable`），持有 `Weak`，用 `Arc::new_cyclic` 构造，每次运行时 `upgrade`，失败就返回 |
| `cancel_task`/`wait_task` | 语义原样保留：`wait_task` 返回之后，任务不在运行，也不会再运行（`ob_timer_service.cpp:400-429`）。C++ 里所有 cancel + wait 的调用点照样翻译 |

### 2.8 存储

- **只能通过核心 API 访问 MVCC 内部**：
  - `x->prev_` 改成 `x.prev()`（`Acquire`）；
  - 改链只能经由 `RowGuard`；
  - 核心之外不允许持有裸的 `*mut TxNode`。
- **句柄**：
  - C++ 拷贝句柄的地方用 `clone()`；`reset()` 改成 drop 或赋 `None`；`get_obj()` 改成 `&*h`；
  - 在作用域内只是借用的，传 `&T`，**不要多 clone**。
- **手动 `inc_ref`/`dec_ref`**（169 处）逐处改写：
  - 管内存对象的，改用 `Arc` 的 clone 和 drop；
  - 管磁盘块的（`inc_macro_ref` 等），照 0.9 在桶锁下做"取、改、写回"。
- **事务回调**：只能用 `TxCallback` 的三个 enum 变体，不新增种类；创建回调必须经 `MemtableRef::register_callback()` 拿 `MemtablePin`（0.9）。
- **行存储和其他头加尾随数据的结构**：只经字节视图访问（0.9）。

### 2.9 解析器的消费方（205 个文件）

| C++ | Rust |
|---|---|
| `node->children_[i]` | `node.child(i)`：越界或空都返回 `None` |
| `node->num_child_` | `node.num_child()` |
| `node->type_` | `node.kind()`（`ObItemType`） |
| `node->str_value_` / `str_len_` | `node.str_bytes()`：空指针得 `&[]` |
| 写标量字段 | 对应的 setter |
| 写 `str_value_`、`children_` | 只接受同一解析 arena 里的值的 setter、`set_child(i, node)`；其他来源先拷进解析 arena（0.12） |
| 栈上的 `ParseNode x;` | `LocalParseNode`（0.12） |
| 跨会话、跨线程保存的节点 | `OwnedParseTree`（0.12） |
| 就地改写节点字符串 | 拷贝、修改、改指（§2.4） |

### 2.10 全局状态与 seam

- 映射见 0.5。
- 符号落在 seam 表里的，**按表实现**：
  - 声明所在的单元，生成 trait、槽位和转发函数；
  - 定义所在的单元，写 `impl`；
  - 本单元在 `seams.tsv` 里的"Rust 落点"和"定义处"义务，见 §6。
- **中心注册表、组合根绑定、模块树一律由脚本生成**，写到 `rust/port/<crate>/src/generated/`；翻译者只写自己的单元：
  - 注册表单元照常翻译，只把注册表本身换成 `include!("generated/<名字>.rs")`；
  - 组合根单元照抄 `obs_construct_modules` 的顺序，在 C++ 对应的位置调用 `generated::bind_all()`；
  - 槽位静态量写在类型所在 crate 的 `generated/slots.rs`；
  - LogOp、Path、OpSpec、OpState、ExtraInfo、ExprOpCtx 的 enum 由脚本从各单元的变体 struct 生成。

### 2.11 序列化与 TO_STRING

| C++ | Rust |
|---|---|
| `OB_UNIS_VERSION(v)` + `OB_SERIALIZE_MEMBER(C, a_, b_)` | `ob_serialize_member!(C, v; a_, b_);`，生成 `impl ObSerialize`。成员顺序按宏里列出的顺序，不按字段声明顺序 |
| `OB_SERIALIZE_MEMBER_INHERIT`/`_SIMPLE`/`_TEMP`/`_IF` | 同名小写宏，变体一一对应 |
| `OB_DEF_SERIALIZE`/`DESERIALIZE`/`SERIALIZE_SIZE(C)` 的函数体 | `impl ObUnisBody for C`；unis 头由 `ob_unis!(C, v)` 统一加上 |
| `NEED_SERIALIZE_AND_DESERIALIZE` + `DEFINE_SERIALIZE…` | 手写 `impl ObSerialize for C`，不带 unis 头，palf 的 `LogEntry` 就属于这一类 |
| `serialize(char *buf, int64_t len, int64_t &pos) const` / `get_serialize_size()` | `fn serialize(&self, buf: &mut [u8], pos: &mut i64) -> ObResult` / `fn get_serialize_size(&self) -> i64` |
| `deserialize(const char *buf, int64_t len, int64_t &pos)` | `trait ObDeserialize<'de> { fn deserialize(&mut self, buf: &'de [u8], pos: &mut i64) -> ObResult; }`。**就地解码**：旧数据里缺失的尾部成员保留构造时的默认值。<br>• 含借用字段（零拷贝指向 `buf`）的类型：`impl<'de> ObDeserialize<'de> for T<'de>`；<br>• 拥有型：`impl<'de> ObDeserialize<'de> for T`，在内部拷贝；<br>• 需要拷进 arena 的，另给 `deserialize_in(&mut self, buf, pos, arena: &'q Arena)` |
| `OB_UNIS_ENCODE/DECODE/ADD_LEN(x)`、`LST_DO_CODE(…)` | `ob_unis_encode!(ret, buf, pos, x)` 等；解码时保留 `pos < data_len` 的判断 |
| `serialization::encode_vi64` 等 | `oblib::serialization` 里的同名函数，字节序相同（定长整数按大端写） |
| `TO_STRING_KV` / `VIRTUAL_TO_STRING_KV` / `INHERIT_TO_STRING_KV` / `DECLARE_TO_STRING` + `DEF_TO_STRING` | `impl ObToString`，写成 `to_string_kv!(Self; "a" => self.a_, …)`；继承版先打印父类。键就是 C++ 宏参数字符串化的结果。持有锁内数据的类型按 §2.2 打印 |
| 手写的 `int64_t to_string(char *, int64_t)` | 逐行翻译成 `fn to_string(&self, buf: &mut ObBuf) -> i64` |
| `ObCStringHelper::convert(x)`、`J_KV`、`databuff_print_kv` | `helper.convert(&x)`、`j_kv!`、`databuff_print_kv!` |

**`TO_STRING` 的格式**：
- 整体是 `{k:v, k:v}`；
- double 用 `%.18le`，float 用 `%.9e`，bool 输出 `True`/`False`，空指针输出 `NULL`；
- 数组输出 `[cnt:N, a, b]`。
- 所有类型都逐字节复刻。

### 2.12 值类型与位布局

| C++ | Rust |
|---|---|
| `ObDatum`（`src/oblib/common/datum/ob_datum.h`，packed，12 字节，`ptr_` 加 19 个类型化指针别名的联合体） | 存储形态 `ObDatum { desc: u32, r: DataRef }`，仍是 12 字节，`DataRef` 是 `{ store: u32, off: u32 }` 或 8 字节内联值（0.6）；`const ObDatum &` 参数和局部变量 → `ObDatumView<'a>`，按值传递；写入经拥有者（帧、行）的 API。两种形态之间的转换只在 datum 模块和帧模块里 |
| `ObObj`（`src/oblib/common/object/ob_object.h`，值联合体含 `string_`、`lob_`、`decimal_int_`、`ptr_`） | 拆成借用视图 `ObObj<'a>`（`Copy`，字符串、数字用 `&'a [u8]`/`&'a [u32]`）和拥有型 `ObObjOwned`（`Box<[u8]>`）。schema、计划、会话变量、PL 只用拥有型；`deep_copy(alloc)` 译成"视图 → 拥有型"或"视图 → arena 视图"两个函数；按 `types.tsv` 的生命周期类别选 |
| 其他依赖 `offsetof` 或落盘的结构 | 0.10 的逐字段编解码；只含标量的联合体改成原始位（`u64`）加访问方法，如 `f64::from_bits` |
| packed 结构 | 0.10 |
| 头加尾随数据的结构（柔性数组） | 字节视图（0.9） |
| 位域 | `oblib::ob_bitfield!` 宏，显式写出每个字段的偏移（按 Clang/GCC 在小端上的规则，从低位起依次分配）；每个结构配一条与 C++ 对照的布局测试（5b） |
| C++ 枚举 | `#[repr(transparent)] pub struct T(pub i32)`，加同名的关联常量和模块级常量；`match` 用常量模式，最后加一个 `_` 分支。理由：反序列化不校验取值，int→枚举的转换至少有 781 处。**禁止**把任何来自 C 或整数的值 `transmute` 成 Rust `enum` |
| `ObItemType`（约 2,020 个值） | 生成的新类型 `query::item_type::ObItemType(pub i32)`（0.13）。C++ 会产生非枚举值，例如 `ob_raw_expr_util.cpp:3833,3848,3881` 经 `static_cast<ObItemType>` 得到 42、41、39，所以不能用 `#[repr(i32)] enum` |
| `ObNumber`、`ObDecimalInt`、`ObTimeConverter`、`ob_dtoa`、哈希函数、crc64 | 逐行移植，配上以 C++ 为准的金标准测试 |
| 整数类型 | `int64_t` → `i64` 等一一对应。<br>• 作字节数据搬运的 `char` → `u8`；提升成 `int` 参与运算或比较的，先转 `core::ffi::c_char` 再扩展，即按平台的符号性：macOS 和 x86 有符号，Linux aarch64 无符号。例如 `fnv_hash2`（`murmur_hash.cpp:51-57`）对 0x80 以上的字节做符号扩展；<br>• `long`/`unsigned long` → `core::ffi::c_long`/`c_ulong`（Windows 上是 32 位）；`long long` → `i64`；<br>• `size_t` → `usize`；<br>• 无符号数回绕在 C++ 里是有定义的行为，所以参与对齐的 profile 关闭溢出检查；有符号溢出在 C++ 里是未定义行为，基线二进制按补码回绕，哈希函数这类有意依赖回绕的地方显式写 `wrapping_*`；C++ 里显式检查溢出的地方，改用 `checked_*`/`overflowing_*` |
| 浮点转窄整型（`static_cast<uint8_t/int8_t/int16_t>(double)` 等，如 `ob_array_cast.cpp:452-462`） | 越界时在 C++ 里是未定义行为，基线的结果取决于代码生成：值存进内存时是"先饱和到 32 位再截断"（300.0 → 44，-5.0 → 251），直接使用时得到 32 位的值。统一用 `oblib::num::c_fptosi_*`，实现"先饱和到 32 位再截断"，标 `FIXED(port)`；不得直接用 `as`（它按目标宽度饱和） |
| 整数除法和取余 | C++ 有守卫的照写 `/`、`%`。没有守卫、可能除以 0 或 `MIN / -1` 的，用 `oblib::num::c_div`/`c_rem`：aarch64 上照硬件语义（除以 0 得 0，余数等于被除数；`MIN / -1` 得 `MIN`，余数 0）；x86 上与 C++ 一样终止进程。Rust 的 `/`、`%` 在这两种情况下总会 panic，与 overflow-checks 无关。审查清单加一条"新出现的 `/`、`%` 有没有守卫" |
| C 数学库 | `rint`/`nearbyint` → `round_ties_even`（不得用 `round`：`ROUND(2.5e0)` 基线是 2）；`pow` → `powf`（不得用 `powi`）；`fmod` → `%`；`frexp`/`ldexp`/`modf` 等 → `libc` 同名函数。这些在各平台调用系统 libm，结果与该平台的 C++ 相同 |
| 乘加 `a*b+c` | 按 D17。推荐方案下一律写普通运算，不用 `mul_add`；如果 D17 选 (a)，只在"融合清单"列出的表达式上写 `mul_add`，标 `PARITY(port): fp-contract` |

### 2.13 类、模板与其他语言构造

| C++ | Rust |
|---|---|
| 带虚函数的类 | 一个 trait（方法一一对应），加一个数据 struct：<br>• 数据 struct 保留 C++ 名（`ObITable`），trait 叫 C++ 名加 `Virt`（`ObITableVirt`）；没有数据成员的纯接口类只生成 trait，名字保留 C++ 名；<br>• 派生类里，第一个有数据的基类字段叫 `base`，其余基类叫 `base_<蛇形类名>`；trait 提供 `base()`/`base_mut()` 对应第一个基类；<br>• 基类的默认实现 → trait 的默认方法；派生类里调用 `Base::f()` → 自由函数 `base_f(self)`；<br>• 名字写进 `types.tsv`，由脚本生成。<br>`ObExprOperator` 层次例外，见 0.6 |
| 接收者 | • C++ 的 const 方法 → `&self`；<br>• 非 const 方法：类型在 `types.tsv` 里标为共享（会出现在静态量、槽位、`Arc` 里，或跨线程访问）的，一律 `&self`，可变字段按 §2.7 放进锁或改原子量；<br>• 其余 → `&mut self`；<br>• const 与非 const 的重载对（253 个头文件里约 666 对）：const 版本保留原名，非 const 版本叫原名加 `_mut`，不看声明顺序 |
| 多重继承（120 个，多为接口混入） | 拆成多个 trait；虚继承（2 处）由清单第 20 类逐处给出译法 |
| 向下转型 `static_cast<D*>(b)`、`dynamic_cast` | **按 C++ 的 is-a 判定**：译成 `as_<变体蛇形名>()`，返回 `ObResult<&T>`/`ObResult<&mut T>`。接受目标类及其全部派生类对应的变体，接受集由脚本从类层次生成，写进 `types.tsv`。只有 C++ 里 is-a 不成立（真正的未定义行为）时，才返回 `OB_ERR_UNEXPECTED` 并记 ERROR 日志；一律不 abort，C++ 本来就用 `OB_ASSERT` 守住的除外。<br>例：IFNULL/NVL 是 `ObSysFunRawExpr`，标签是 `EXPR_SYS_FUNC`，`is_op_expr()` 只认 `EXPR_OPERATOR`；但它继承 `ObOpRawExpr`（`ob_raw_expr.h:3868`），所以 `as_op()` 必须接受它（`ob_transform_simplify_expr.cpp:1254,1315`）。<br>变体名 = 类名去掉 `Ob` 前缀和层次后缀（RawExpr/Log/Stmt/Spec），再转成蛇形 |
| 需要运行期向下转型的 trait 对象 | 必须是 `'static`（不带 `'q`，借用改成 id 或拥有型），用 `as_any()` 转换；做不到的，改成 enum，或在 trait 里为每个可转换的目标提供 `fn as_x(&mut self) -> Option<&mut X<'q>>` |
| CRTP | 泛型加 trait，以 `Self` 作类型参数 |
| 模板 | 泛型加显式的 trait 约束：<br>• 非类型模板参数 → const 泛型；<br>• 特化 → 每个类型一个 trait impl，impl 放在类型所在的 crate（0.4）；<br>• SFINAE、`if constexpr` → trait 分派 |
| 运算符重载 | • 比较 → `PartialEq`/`PartialOrd`，语义不同时保留 `compare()`；<br>• 深拷贝的 `operator=` → `assign(&mut self, &T) -> ObResult`，浅拷贝 → `#[derive(Clone, Copy)]`；<br>• `operator()` → 方法 `call()`，`operator[]` → `at()`；<br>• 算术 → `std::ops` |
| `DISALLOW_COPY_AND_ASSIGN` | 不 derive `Clone`/`Copy` |
| 函数重载 | 按 §4 的改名规则（`overloads.tsv`） |
| 默认参数 | 调用方把所有参数显式写全，默认值取自 C++ 的声明 |
| 可见性（`private`/`protected`/`friend`，friend 508 处） | 第 3 步一律 `pub`；对齐之后由脚本按 C++ 的访问级别收窄 |
| 命名空间作用域里带构造函数的全局对象 | 能用 `const` 初始化的写成 `static`；否则写 `static X: LazyLock<T>`；构造时有副作用（比如注册）的，改成组合根里的显式调用（0.4） |
| 函数内的 `static ObX x` | `static X: LazyLock<…>` 或 `OnceLock` |
| 运行期会被写的非线程局部静态量（函数内显式的约 31 处，宏生成的约 268 处，另有数十个可变的类静态计数器） | 原子量或 `Latch<T>`；不得用 `static mut` |
| `thread_local`、`RLOCAL` | `Copy` 类型：`thread_local! { static X: Cell<…> = const { … } }`；其余用 `RefCell`（0.5） |
| 定义类型或函数的宏、X-macro 表 | 同名的小写 `macro_rules!`，`#[macro_export]`（§4） |
| `goto`（93 处，全在 5 个从 MySQL 派生的文件里） | 带标签的块（`'l: { … break 'l; }`）或循环状态机，逐处翻译 |
| 非空的 `switch` 贯穿（约 144 处） | 重复代码，或者改写成依次递减的 `if` 级联，注释注明原来是贯穿；空 case 合并成 `A \| B =>` |
| 内联汇编（39 处） | pause/yield → `oblib::sync::pause()`；rdtsc、cpuid、crc32 → `core::arch` 的 intrinsic；换栈汇编由 `oblib::thread::stack` 取代；其余的 `asm!` 放进 `oblib::arch` |
| SIMD intrinsic | 搬进所在 crate 的 `simd` 模块（函数级重映射），用 `std::arch` 里的同名函数，加 `#[target_feature(enable = …)]` 和运行时检测，对外提供基于切片的安全封装；`ob_sse_to_neon.h` 按 `cfg` 拆成两份实现 |
| 预处理条件 | 0.15 |
| `DEFER(…)` / `ARRAY_FOREACH` / `(void)f()` | `defer!`，在 Drop 里执行 / `for` 循环加 `ret.is_ok()` 条件 / `let _ = f();` |

## 3. 逃生口

**unsafe 只允许出现在下面这些路径里**（按 `units.tsv` 的目标路径表达）。没有白名单模块的 crate 一律 `#![forbid(unsafe_code)]`；含白名单模块的 crate 用 `#![deny(unsafe_code)]`，只在白名单模块上写 `#[allow(unsafe_code)]`。两者都由 CI 用 grep 校验。

1. `rust/port/oblib/src/mem/**`：全局分配器、`try_box`、arena 外观。
2. `rust/port/oblib/src/sync/**`：latch 的移植、bitlock、`BigReaderLatch`、`QSyncLatch`、`Quiescence`、`FutexCond`。
3. `rust/port/oblib/src/hash/**`、`rust/port/oblib/src/queue/**`、`rust/port/oblib/src/container/intrusive/**`。
4. `rust/port/oblib/src/thread/stack/**`（psm）。
5. `rust/port/oblib/src/signal/**`。
6. `rust/port/oblib/src/arch/**`（`asm!`）。
7. `rust/port/oblib/src/num/**`（调用 C 库的数值解析）、`rust/port/oblib/src/pod/**`（POD 视图）。
8. `rust/port/*/src/simd/**`：SIMD 内核，含生成的 bitpacking。
9. datum 模块和 object 模块（`ObDatum`、`ObObj` 所在的单元），以及 query 里的帧模块。确切路径在 1d 写进 `units.tsv`（处置 `rewrite:datum`、`rewrite:frame`）。
10. `rust/port/storage_core/**`（0.9）。
11. `share::cache` 里移植的 KV cache 回收和 map（0.8），确切路径同 9。
12. `rust/port/parser/**`：视图、布局和回调蹦床（0.12）。
13. 各 -sys 和 shim crate（0.14）。
14. 已有的 `config`、`sql-nio`。

**每处 unsafe 的要求**：

- 写 `// SAFETY: <为什么正确>` 注释，开启 `clippy::undocumented_unsafe_blocks`；
- 开启 `#![deny(unsafe_op_in_unsafe_fn)]`；
- `unsafe impl Send/Sync` 也只允许出现在上述地方，而且不得用于含临界区令牌或 `Guard` 的类型；
- 禁止把来自 C 或整数的值 `transmute` 成 Rust `enum`；
- `oblib::sync`、`oblib::hash`、回收方案和 `storage_core` 要配 loom 测试；单线程部分跑 Miri；分配器门面另跑 ASan。

**标记**：全部可 grep，格式是承重的，不得改写；一行最多写一个标记。

| 标记 | 用途 |
|---|---|
| `TODO(port): <问题>` | 推迟的决定，见 UNKNOWN 规则 |
| `PERF(port): <快的写法是什么>` | 已知较慢的写法；对齐之后才做的 ordering 弱化 |
| `BUG(port): <复现方法>` | 照样复刻的原版缺陷，对齐之后单独修（0.1.4） |
| `FIXED(port): <原缺陷>` | 照样复刻会构成未定义行为、翻译时已改成安全写法的缺陷（0.1.4） |
| `PARITY(port): <说明>` | 为了让输出与 C++ 相同而做的特殊处理（如 D17 选 (a) 时的 `mul_add`） |
| `DIVERGE(port): <项>` | 接受的偏差；`<项>` 必须是 A.4 清单第一列里的名字 |
| `SLOT(port): <槽位表达式>` | 改写 IR 槽位的地方（R3） |
| `SAFETY:` | 每处 unsafe |

**lint**：

- 照样跑 `clippy -D warnings`；
- 统一放开逐行保留错误链必然触发的几条：空分支、`needless_return`、`non_upper_case_globals`、`non_camel_case_types`、`too_many_arguments`；
- 用 `disallowed_methods`/`disallowed_types` 禁止以下用法（白名单模块除外）：`ManuallyDrop`、`mem::forget`、`mem::transmute`、`Atomic*::fetch_max`/`fetch_min`、`BinaryHeap`、`slice::sort_unstable*`、std `HashMap`/`HashSet`、`DashMap`、`core::hint::spin_loop`、`f64::powi`/`f32::powi`、`SystemTime::now`。

## 4. 命名与输出路径【D14】

是否完成，靠输出文件是否存在来判定，所以这些规则是整个过程可以续跑的前提，不是风格偏好。

- **单元**：同一目录下同名的 `.h`/`.hpp`/`.ipp`/`.inc`/`.cpp`/`.cc`/`.c`/`.def`/`.map` 合成一个头文件组，一个单元对应一个 `.rs` 文件。重新设计模式下仍以头文件组为单元（DEV-010）。
- **单元表 `migration/design/units.tsv`**：唯一来源，由脚本生成，与缺口清单一起审查。每个单元一行，列出代表文件、`members`（合并单元的全部成员，含跨目录的成员）、目标路径、crate、处置、批次。
  - 处置取值：`translate`、`replace:<基础设施模块>`、`rewrite:<模块>`（如 `rewrite:storage_core`、`rewrite:frame`、`rewrite:datum`）、`keep-c`、`keep-cxx:<shim>`、`generate:<生成器>`、`data:<转换脚本>`、`delete`。
  - 要逐个定处置的有：
    - oblib 基础设施目录（container、hash、allocator、thread、lock、alloc、oblog、objectpool、queue、list、atomic、thread_local 等）下约 190 个单元。现在的临时 manifest 里，第 81 行 `ob_latch.h`、第 100 行 `page_arena.h`、第 201 行 `ob_retire_station.h`、第 208 行 `ob_link_hashmap.h` 等约 20 行都属于这一类；
    - 解析器单元 25 行；死代码 6 个；`ObItemType` 的头文件；数据表和 `.def`；
    - 存储核心的约 10 个单元：`ob_mvcc_row`、`ob_row_latch`、`ob_keybtree` 及其 deps、`ob_tx_callback_list`、`ob_mvcc`、`ob_memtable_data`、memstore 分配器、`log_group_buffer`、`lsn_allocator`；
    - OB 的几何 traits 和树类型（`keep-cxx:bgk_shim`）。
  - 合并单元只占一行。合并之后用合并过的图重算单元级的强连通分量，重写 order.txt；脚本检查目标路径唯一，有重复就报错退出。
- **翻译队列 `migration/manifest.tsv`**：由 `units.tsv` 派生，只含处置为 `translate` 的行，表头仍是 kit 要求的 `source\ttarget`。在 `seams.tsv` 签字之后重新生成一次（函数级重映射）。
- **非翻译单元的桩文件**：负责的阶段（多为 1d）在目标路径写一个只含 `pub use <替代物>` 的桩文件，让跨单元的导入仍然可以解析。文件头写 `//! Disposition: <处置>`，文件尾仍是 §6 格式的状态行。
- **批次**：翻译 manifest 中连续 100 行。kit"一个环一批"的规定不适用：第 3 步不编译，环只影响名字推断，而跨单元的名字都由生成表决定（DEV-012）。
- **路径**：`src/<模块>/<相对路径>/<名字>.<扩展名>` → `rust/port/<crate>/src/<相对路径>/<名字>.rs`。
  - crate 与模块同名，以下几处例外：

    | 源路径 | 映射到 | 说明 |
    |---|---|---|
    | `src/pl/<rest>` | `rust/port/sql/src/pl/<rest>` | 与同名的 `src/sql/pl/<rest>` 合成一个单元 |
    | `src/objit/include/objit/<rest>` | `rust/port/query/src/objit/<rest>` | |
    | `src/data_plane/api/data_plane/<rest>` | `rust/port/data_plane/src/<rest>` | |
    | `src/query/api/query/<rest>` | `rust/port/query/src/<rest>` | 与同一相对路径下的实现合成一个单元 |
    | 存储核心的约 10 个单元 | `rust/port/storage_core/src/<名字>.rs` | 处置 `rewrite:storage_core`，不进翻译队列 |

  - 模块名与 Rust 关键字或 crate 根文件名（`lib`、`main`、`mod`）冲突时，加后缀 `_m`，如 `lib_m`、`static_m`；脚本同时改写目标路径。
  - observer 的二进制入口由脚本生成，写到 `rust/port/observer/src/bin/seekdb.rs`；`main.cpp` 所在单元的目标改成 `src/ob_main.rs`。
  - 按 seam 表下移的函数，归到目标单元（函数级重映射，§6）。
- **生成物**：`rust/port/<crate>/src/generated/`，不进 manifest（0.13、§2.10）。
- **模块树和导入**：
  - 各 crate 的 `lib.rs` 和 `mod` 声明由脚本生成，对所有单元模块做 `pub use <模块>::*;`；
  - 引用方一律写 `use <crate>::<名字>;`；
  - 重名由脚本检测，冲突的名字按 `migration/design/symbols.tsv` 给出的完整路径引用；
  - 宏一律 `#[macro_export]`。同一 crate 内重名的宏（头文件里 2,863 个函数式宏中有 73 个名字定义在不止一处），第一个定义保留原名，其余加单元名前缀，名单由脚本生成；
  - 每个单元文件的第一条 `use` 固定为 `use oblib::prelude::*;`，prelude 的内容在 1d 确定；
  - 归属表里的 `oblib::mem` 等是 1d 写的门面模块。
- **名字**：
  - 类型名、方法名、字段名保留 C++ 的写法，包括 `Ob` 前缀和字段末尾的下划线；
  - 宏用同名小写；常量沿用 `OB_X`；枚举的关联常量和模块级常量沿用 C++ 的枚举值名；
  - 本文引入的新名字只来自下面的改名表；
  - 理由：对齐调试时能按名字对照。
- **命名空间**：crate 就是命名空间；嵌套的命名空间只在用来消除重名时保留为模块。
- **重载**：由脚本生成 `migration/design/overloads.tsv`（完整签名 → Rust 名）：
  - 在头文件里声明位置最靠前的那个保留原名；
  - 其余改名为"原名 + `_` + 区分参数的类型名的蛇形写法"，类型名去掉 `Ob` 前缀和 const、引用、指针修饰，例如 `print(const ObColumnRefRawExpr &)` → `print_column_ref_raw_expr`；仍然冲突时，再加参数个数后缀；
  - const 与非 const 的重载对按 §2.13；
  - 超过 20 个重载的集合（如 `ObRawExprPrinter::print` 有 116 个）照同一规则生成，清单第 15 类单列复核。
- **槽位名**：`slots.tsv`，统一为"类型名去掉 `Ob` 前缀的大写蛇形 + `_SLOT`"，静态量写在类型所在 crate 的 `generated/slots.rs`。
- **trait 名、基类字段名、变体名、`as_*` 访问函数名**：§2.13，写进 `types.tsv`。
- **改名表**（本文引入的新名字的唯一来源）：

  | Rust 名 | 对应的 C++ | 归属 |
  |---|---|---|
  | `ObError`、`ObResult` | `int ret` 错误码 | `oblib::error` |
  | `Arena`、`ArenaVec`、`ObStr<'q>`、`MemBudget`、`MemCtx`、`try_box` | 查询作用域的 `ObArenaAllocator`/`PageArena`、ObString 视图、内存限额检查、`lib::MemoryContext` | `oblib::mem` |
  | `Latch`、`RwLatch`、`ShardedRwLatch`、`BigReaderLatch`、`QSyncLatch`、`BucketLatch`、`Quiescence`、`Monitor`、`FutexCond`、`CacheAligned`、`pause`、`bitlock` | 见 §2.7 | `oblib::sync` |
  | `inc_update`、`dec_update` | 同名 | `oblib::atomic` |
  | `ObLinkHashMap`、`ObLinearHashMap`、`FixedHash2`（名字不变）、`BucketedHashMap` | 见 §2.7 | `oblib::hash` |
  | `CppFloatKey`、`c_strtod`、`fast_float_from_chars`、`c_fptosi_*`、`c_div`、`c_rem` | 见 §2.5、§2.12 | `oblib::num` |
  | `Slot` | `server_service<T>()`、GCTX 的指针成员 | `oblib::slot` |
  | `WorkerCtx`、`smart_call!`、`check_stack_overflow` | `THIS_WORKER` 等线程局部、`SMART_CALL`、同名函数 | `oblib::thread` |
  | `RtExpr`、`RtExprId`、`SpecId`、`ExprRef` | `ObExpr`、"下标+1"编码、局部表达式引用 | `query` |
  | `ExprId`、`StmtId`、`TableItemId`、`LogOpId`、`PathId`、`IdVec` | `ObRawExpr *` 等编译期指针 | `query::ir_id` |
  | `QueryArena` | 每条语句的 `ObRawExprFactory`、`ObStmtFactory` 等 | `sql::ir` |
  | `ExprOp`、`OpState` | `ObExprOperator` 的虚函数和数据 | sql 的表达式模块 |
  | `DataRef`、`ObDatumView` | datum 的 `ptr_`、`const ObDatum &` | datum 模块 |
  | `ObObjOwned` | 拥有型的 `ObObj` | object 模块 |
  | `ParseNode<'p>`、`ParseArena`、`LocalParseNode`、`OwnedParseTree` | `ParseNode *`、解析 arena、栈上节点、PS 缓存里的节点 | `parser` |
  | `NodeRef`、`RowGuard`、`MemtableRef`、`MemtablePin` | `ObMvccTransNode *`、行锁 guard、memtable 引用、回调保活 | `storage_core` |
  | `KvBlockHandle` | `ObKVMemBlockHandle *` | `share::cache` |
  | `GlobalContext`、`GCTX` | `ObGlobalContext`、`GCTX` | `share` |
  | `<类名>Virt` | 类的虚函数部分 | 类所在的 crate |
  | `ObExecContext`（名字不变） | v0.1 里的 "ExecRuntime" 就是它 | `sql` |
  | `ObItemType`（名字不变） | | `query::item_type` |

- **共享类型的归属**：每个共享类型只有一个归属模块，翻译出来的代码只能从这里导入，不得各自复制一份。归属见上表，另有：`ObIArray` 等容器 → `oblib::container`；`ObHashMap` → `oblib::hash`；`ObStringExt`、`ObStringWriter` → `oblib::string`；线程池、定时器 → `oblib::thread`；日志宏 → `oblib::log`；`ob_printf!` → `ob_macros`，运行时在 `oblib::fmt`；序列化 → `oblib::serialization`。
- **测试**：C++ 的单元测试已经删除，不翻译测试。逐行移植的算法要配金标准测试，放在 `rust/port/<crate>/tests/`；golden 文件由 5b 阶段的 C++ 测试程序产出（0.2）。

## 5. 缺口清单与生成表【D15】

所有权、生命周期、可空性这类问题**不由实现者决定**，一律查表。站点不在表里，就是清单的缺陷：提出来，按 UNKNOWN 规则处理，继续往前走。

### 5.1 表

| 表 | 粒度 | 产出 | 内容 |
|---|---|---|---|
| `migration/inventory.tsv` | 一个站点一行 | 脚本先扫，agent 判定剩余 | 站点级的译法 |
| `migration/design/types.tsv` | 一个 C++ 类一行 | 脚本加 agent | Rust 名和 trait 名；生命周期参数（脚本对成员类型做闭包计算）；生命周期类别（查询、计划、长生命周期、批次）；是否共享（决定接收者和 `Sync`）；处置；is-a 接受集；返回 guard 的方法名 |
| `migration/design/seams.tsv` | 一个跨 crate 符号或前向声明一行 | 脚本加 agent，两名审查者 | 0.4 |
| `migration/design/units.tsv` | 一个单元一行 | 脚本，审查 | §4 |
| `slots.tsv`、`overloads.tsv`、`symbols.tsv`、`qa_signatures.tsv` | | 脚本 | §4、R1 |

### 5.2 类别

"脚本"表示由脚本直接定论，不进 agent；"脚本 + agent"表示脚本先按模式打标，agent 只处理打不上标的部分。

| # | 类别 | 规模 | 谁来定 |
|---|---|---|---|
| 1 | 指针的可空性 | `OB_ISNULL` 34,194 处 | 脚本：C++ 指针一律 `Option`，保留检查（§2.1） |
| 2 | 指针参数和 `T *&` 输出参数的归属 | 9,454 处输出参数，另有普通指针参数 | 脚本 + agent：按被调方的分配来源判定 arena 还是 `Box`，所有权转移由 agent 判定 |
| 3 | 裸指针成员的归属 | 6,120 个 | agent（§2.4 的选项） |
| 4 | 引用成员、借用分配器的成员、`ObString` 成员 | 约 1,200 + 509 + 1,032 | agent，结论写进 `types.tsv` |
| 5 | 自持 arena 的类，"默认指向自带分配器"的成员 | 288 个类，另有若干 | agent（0.7 的 (a)(b)(c)） |
| 6 | 返回 `int` 的函数返回的是错误码还是值 | | 脚本 + agent |
| 7 | 每个锁保护哪些字段；返回锁引用的访问函数；重入点 | 锁声明 461 处、guard 2,109 处 | 脚本 + agent，按类判定 |
| 8 | 原子字段的角色；参与发布的普通访问；C++ 里的数据竞争字段 | 736 个字段，另有约 90 处 `has_set_stop()` 等 | 脚本 + agent |
| 9 | union、位域、`reinterpret_cast`、packed、柔性数组 | 3,692 + 约 64 + 约 70 | 脚本 + agent：字节读写用 `from_le_bytes`/`from_be_bytes`；POD 视图用 `oblib::pod`；以地址为键的改成 id；派生类槽当基类槽用的按 R3 |
| 10 | 运行期写共享计划的点 | 1 + 31 + 6 | agent |
| 11 | 临时作用域：新建节点是否被外层引用；`ObArenaAllocator` 成员和 `OB_NEWx` 的 pool 的层级 | 21 + 33 + 382 | agent |
| 12 | 跨算子旁路；被别的算子读写的状态 | 28 处 | agent |
| 13 | weak 符号和 `.cpp` 里自写的 extern 原型 | 45 | 脚本 |
| 14 | 对象池；构造时有副作用的静态初始化；动态初始化的顺序 | 11 + 268 个目标文件 | 脚本 + agent |
| 15 | 超过 20 个重载的集合 | | 脚本，agent 复核 |
| 16 | 待确认的死代码 | 6 个单元 + 2 个函数 | agent |
| 17 | 00b 的 15 条疑似 bug，以及 0.8 的 KV cache 退休条件：定位到 file:line，`classification=BUG`；第 13–15 条不要求打标 | 16 | agent |
| 18 | 就地改写借来的字节 | `casedn/caseup` 36 处、`const_cast<char *>` 509 处 | 脚本 + agent |
| 19 | 同时挂在多个容器里的对象；宿主回指；定时任务的宿主 | | agent |
| 20 | 虚继承；`void *` 成员 | 2 + 121 | agent |
| 21 | 解析树：39 处 `const_cast<ParseNode`、栈上节点、非标量写 | | agent |
| 22 | 会话字段的访问分组 | | agent |
| 23 | `ObItemType` 两份定义的差异，以及让 objit 那份生效的编译单元 | | 脚本 + agent |

### 5.3 UNKNOWN 时的默认译法

| 类别 | 默认 |
|---|---|
| 可空性 | `Option` |
| 裸指针成员 | `Option<Arc<T>>`；类型属于 QueryArena 图时用 `Option<Id>` |
| `ObString` 成员 | `Box<[u8]>` |
| 输出参数 | `&mut Option<Box<T>>` |
| 锁保护不明 | 标量改原子量，非标量放进锁 |
| 原子访问的内存序不明 | `SeqCst` |
| 返回 `int` 的函数 | 当作错误码 |
| 生命周期类别不明 | 长生命周期（拥有型） |
| 是否共享不明 | 共享（`&self`） |
| 向下转型的接受集不明 | 目标类及其全部派生类 |

每处都标 `TODO(port)`。

### 5.4 规模与成本

v0.2 的口径：第 1、13 类和名字表全部由脚本定论；"失败后仍继续执行"的函数标签推迟到对齐之后，只在把 if/else 链机械改成 `?` 时才需要。agent 要处理的约 2.2 万个条目（估计），成本见 D15（A.1）。

## 6. 逐文件义务

- **文件头**：`//! Ported from: <源文件列表>`；按 seam 表搬进来的函数，也列出它们的源文件。
- **文件尾**：每个翻译出来的文件以状态行结尾：

  ```
  // PORT STATUS: confidence=<high|medium|low> todos=<N>
  ```

  `<N>` 等于 `grep -c 'TODO(port)' <file>` 的输出（一行最多一个 `TODO(port)`，所以这就是条数）。审查者要核对它；状态行少报，就是一条审查发现。
- **seam 义务**：开工前查 `seams.tsv`：
  - 本单元是某行的"Rust 落点"时，按 file:line 把列出的函数体一并翻译，并写进 `Ported from`；
  - 本单元是某行的"定义处"、拆法为下移时，跳过这些函数，在原位置留 `// moved to <路径> (seam <id>)`。
- **只写自己的单元文件**。中心注册表、模块树、组合根绑定由脚本生成（§2.10）；桩文件由负责的阶段写（§4）。
- **翻译阶段不运行编译器**、不运行测试、不运行会改动状态的 git 命令。这些操作在第 2 步之前由 `.claude/settings.json` 禁止（`templates/settings.json`）。1d 和第 2 步的编译实测是记录在案的例外（DEV-013、DEV-014）。

## 7. 偏差日志

对既定流程的每一处偏离都记一行，在关口由人写入，或经人同意后写入。DEV-010 起是 v0.2 提出、等你在本关口确认的。

| ID | 日期 | 偏差 | 认可 |
|---|---|---|---|
| DEV-001 | 2026-10-10 | 可行性阶段没有在本机计时构建，改用 CI 数据；之后已实测，冷构建 7 分 44 秒 | 你（可行性关口） |
| DEV-002 | 2026-10-10 | 可行性报告和证据脚本提交到了 `rust-migration` 分支 | 你 |
| DEV-003 | 2026-10-10 | 5 个可行性调查子代理用的是会话默认模型，因为当时模型计划还没定 | 你 |
| DEV-004 | 2026-10-10 | 00b 的两名审查者各审一半、互不重叠，每个改写用例只被审查一次 | 你（00b 关口） |
| DEV-005 | 2026-10-10 | 裁判只在 macOS 上建立和验证；Linux 上至少要再验证一次"原版全部通过"。这也是 0.11 排序决定的前提 | 你 |
| DEV-006 | 2026-10-10 | 协议变体只覆盖了 121 个核心用例 | 你 |
| DEV-007 | 2026-10-11 | 链接级依赖只来自 macOS 的 release 构建。生成 seam 表之前，要在 Linux 构建上补跑 | 你（依赖图关口） |
| DEV-008 | 2026-10-11 | 依赖图第二轮的两名审查者各有一次 grep 误读到脚本里的几行；他们声明判断没有依赖这几行 | 你 |
| DEV-009 | 2026-10-11 | 依赖图审查者按模型计划用 Sonnet 5.5。不算偏差，记在这里备查 | — |
| DEV-010 | 2026-10-11 | 重新设计模式下仍以头文件组为单元（kit 的默认是模块或子系统）：manifest、依赖图和续跑机制都已按头文件组建好，子系统级的一致性由 `types.tsv`、`seams.tsv` 和名字表保证 | 待确认 |
| DEV-011 | 2026-10-11 | 第 4 步按 crate 分 8 个波次编译，每波由 `build_daemon.sh` 构建 | 待确认 |
| DEV-012 | 2026-10-11 | 批次是翻译 manifest 中连续 100 行，不按 kit"一个环一批"划分：第 3 步不编译，名字由生成表决定 | 待确认 |
| DEV-013 | 2026-10-11 | 第 2 步用约 30–50 万行的切片试跑代替全量试跑；切片上的编译实测是禁令的例外 | 待确认 |
| DEV-014 | 2026-10-11 | 新增 1d 基础设施阶段：在第 2 步之前写代码、编译、跑测试，对禁令是例外 | 待确认 |
| DEV-015 | 2026-10-11 | 设计文档的对抗审查用了 4 名审查者加 1 名复核者，复核者抽查了 30 条高影响事实；逐条处理见 `migration/design/review/dispositions.md`。不算偏差，记在这里备查 | — |
| DEV-016 | 2026-10-11 | 只在 D17 选 (b) 时生效：C++ 参照改用 `-ffp-contract=off` 重建，因此改变的裁判期望行逐行重录 | 待确认 |

## 附录 A：决定清单

### A.1 核心决定：需要你拍板

"v0.2 改动"一列说明这一版改了什么；"改推荐"表示推荐项本身变了。

| # | 问题 | 推荐（正文已按此写） | 主要备选 | v0.2 改动 | 依据 |
|---|---|---|---|---|---|
| D1 | 迁移策略 | 核心一次性翻译，按 crate 分 8 波编译；C ABI 只用于孤岛；第 2 步之前有 1c、1d 两个前置阶段；第 2 步用切片试跑 | 按模块 C ABI 共存；先在 C++ 里拆环再翻译 | 加 1d 和切片试跑；波次改为 8 波 | layering §3.3；审查 P-02、P-06、P-31 |
| D2 | crate 划分 | 10 个按模块划分的 crate（pl 并入 sql，objit 并入 query，sql 和 storage 互不依赖），加 `storage_core`、`parser`、`*_data`、-sys/shim、`ob_macros`；新增 `data_plane → query`、`logservice → storage_core` 两条边；sql 暂不拆，第 2 步实测后再定 | query 并入 sql；sql+storage 合并；现在就拆 sql | 加两条边、数据 crate 和 openssl/curl/regex 的 -sys/shim | layering §4；P-02、P-26 |
| D3 | 拆环原则 | 按 seam 表逐符号定拆法：合并 → 下移 → trait + 槽位（服务型）/ 句柄 trait（句柄型和逐行路径）；`Drop` 和外来 trait 的 impl 落在类型所在的 crate；前向声明的向上类型纳入 seam 表；第 3 步不用链接期 seam | 全部用链接期 seam；合并成少数几个大 crate | 删掉链接期 seam；加 impl 落点和前向声明 | layering §3.1；P-26、S-20、S-22、并发 F19 |
| D4 | 数据目录兼容 | 必须兼容：读写同一套落盘格式，落盘头用安全的逐字段编解码；裁判增加跨二进制场景 | 只读兼容；不兼容（只支持新建的数据目录） | 加编解码和 packed 规则 | storage-txn Q1、conventions Q9、parser Q8；S-10 |
| D5 | 没有 ORDER BY 或有并列值时的行序 | 保持一致：哈希表、排序、堆算法、哈希函数逐行移植，排序以 MacOSX15.sdk 的 libc++ 19.1.2 为准，前提是 Linux 复验通过；会进入用户输出的并发结构保持 C++ 的顺序 | 以 Linux libstdc++ 为准；让裁判对这类行序不敏感 | 钉住 SDK 和 libc++ 版本；加堆算法和并发结构的顺序 | conventions Q5、Q6；行为 F6–F8 |
| D6 | SQL IR 的表示 | arena 加类型化 u32 id（不复用，截断留墓碑）；运行期计划 `Arc` 只读共享；拥有式算子树，跨算子状态放在 `ObExecContext` 的表里；帧里的 datum 存 `DataRef`，不存指针；573 种表达式是零大小类型，状态放在 `OpState`；向下转型按 is-a | bumpalo + `&'a` + Cell；`Rc<RefCell>`；帧里保留绝对指针，靠帧模块的 unsafe | datum 表示、`OpState`、墓碑、is-a 转型 | ir-graphs Q1–Q5；S-01、S-09、S-25、P-13、行为 F2 |
| D7 | 内存模型 | 查询作用域用 bump arena（`'q`，不放需要 Drop 的类型），长生命周期用拥有型；自持 arena 的对象分三类处理；全局分配器直通 jemalloc、不计数；限额在原检查点显式检查；不依赖 allocator_api | 全部改成全局堆的拥有型；allocator-api2 参数化容器；按 ctx 计数（v0.1 推荐） | **改推荐**：取消按 ctx 计数；加自持 arena 的规则 | memory Q1–Q6；S-02、S-07、S-23、并发 F6 |
| D8 | 并发模型 | OS 线程，不用 async；**移植 ObLatch 的语义**（写者 itid、按 LatchId 自旋、park/unpark 等待）；`ATOMIC_*` 用 SeqCst，普通读写用 Acquire/Release；**移植 keybtree 和 KV cache 的回收方案**；并发容器和队列用保留 C++ 名字的 oblib 封装；扩展栈段用 psm 并按线程缓存 | parking_lot 封装 + crossbeam-epoch 加"退休预算"+ DashMap（v0.1 推荐）；async/tokio；std::sync | **改推荐**：锁的底层和回收方案改为移植；DashMap 退到封装内部；栈不用 stacker | concurrency Q1–Q8；并发 F1–F5、F8–F15、F17、F21 |
| D9 | 存储的 unsafe 核心 | 独立的 `storage_core`（含 palf 组缓冲和 LSN 分配器），1d 写完并冻结 API；keybtree 按原算法和原回收方案移植；回调保活协议收回核心（`MemtablePin`）；句柄用 `Arc`；对象池首版去掉 | unsafe 放在 storage 内部的模块里；keybtree 用 crossbeam-epoch（v0.1 推荐） | **改推荐**：keybtree 的回收；加回调 pin | storage-txn Q2–Q6；S-04、S-08 |
| D10 | 解析器 | 保留 C 解析器，Rust 侧用 `ParseNode<'p>` 视图（补全空指针、指针字段、栈上节点、拥有型树、布局自检）；迁移后可选替换 | 用 Rust 解析器生成器或手写重写 | 补全视图 | parser Q1、Q2；S-18、S-19、S-26 |
| D11 | 第三方库 | 逐库判断（0.14）；OpenSSL、libcurl、`std::regex`、Boost.Spirit 都保留 | 全部 FFI；全部换成纯 Rust；RustCrypto + ureq + regress + 手写 SRS 解析（v0.1 推荐） | **改推荐**：上述 4 个库改为保留 | parser Q3–Q7；行为 F5、F9–F11、M6 |
| D12 | 错误模型和函数体 | `ObError` 是 4 字节错误码；函数体逐行保留 if/else 链，翻译阶段不用 `?`；用户消息逐字节复刻 printf | 混用 `?`；带数据的错误枚举 | 无 | conventions Q1–Q3 |
| D13 | 依赖白名单和 unsafe 政策 | §1 的白名单；unsafe 只允许出现在 §3 按路径列出的地方 | 只用 std；不限制 | 白名单增删（见 §1）；白名单改按路径表达 | conventions Q13、concurrency Q10；P-19、P-20、S-15 |
| D14 | 命名与单元 | §4：头文件组为单元，`units.tsv` 加处置，manifest 只含 `translate` 行；改名表；`Virt` trait；名字表由脚本生成 | `.h`、`.cpp` 各自一个单元；以子系统为单元（kit 重新设计模式的默认：单元少、上下文大，但 manifest、依赖图和续跑机制要重做） | 加处置、改名表、导入约定 | 依赖图报告第 6 节；P-01、P-03、P-05、P-09、P-12、P-18、P-25、P-27 |
| D15 | 缺口清单的范围和成本 | (A) 脚本先定论（可空性、名字表、weak 符号等），agent 只处理约 2.2 万个条目。按依赖图报告的单价（每批 20 个、每批 3 个 agent、每次约 4×10⁵ token、约 25 分钟）：约 3,600 次 agent、约 1.5×10⁹ token，并发 8 约 8 天（估计） | (B) 全部由 agent 判定，含 3.4 万处可空性和 3.9 万个函数的失败后行为：约 4 倍；(C) 只做第 3 步健全性必需的类别（第 3、4、5、7、8 类，seam，types），约 1.5 万个条目、约 2,500 次 agent、约 1.0×10⁹ token、约 5.5 天 | 新增 | P-34 |
| D16 | 基础设施阶段 1d | 第 2 步之前写完 0.2 列出的基础设施，API 冻结；API 设计用 Opus、实现用 Sonnet、两名 Opus 审查者；估计 6–9 万行 Rust 加约 1 万行脚本，墙钟 2–4 周（估计） | 不设独立阶段，让第 3 步的实现者边翻边写（v0.1 的隐含做法：同一个门面会被多个译者各写一份） | 新增 | P-31 |
| D17 | 浮点乘加融合的参照 | (b) 用 `-ffp-contract=off` 重建 C++ 参照，重跑裁判，只重录因此改变的期望行；Rust 一律不融合，shim 也用 `-ffp-contract=off` 编译。理由：x86-64 的 C++ 本来就不融合，这样各平台一致，也不用逐处维护清单 | (a) 以现有基线二进制为准，生成"融合清单"，Rust 只在清单上的表达式写 `mul_add`，shim 逐编译单元复刻 pragma 状态 | 新增 | 行为 F1；复核 C14 |
| D18 | 目标平台 | 6 个平台都翻译；Linux x86-64 和 macOS arm64 必须能构建、运行，其余在 CI 里至少 `cargo check`；裁判对齐在 macOS arm64 和 Linux x86-64 上做，Windows、Android 的对齐放到第 6 步之后 | 只做 Linux 和 macOS（删掉 Windows/Android 分支，与 CMake 不一致）；6 个平台都在第 6 步对齐 | 新增 | P-33；`CMakeLists.txt:179-196` |

### A.2 其余决定：按推荐执行，你可以随时推翻

| 主题 | 决定 | 来源 |
|---|---|---|
| IR | 子作用域截断留墓碑，id 不复用；语句回收用 `slotmap`；帧保留字节布局，datum 存 `DataRef`；计划不再字节序列化，不共享计划时手写深拷贝；封闭小集合用 enum（含 ExtraInfo、ExprOpCtx）；`eval_enumset` 的无上下文分支显式传参，标 `FIXED(port)`；按地址顺序迭代的 3 处改成插入顺序，标 `DIVERGE`；下压过滤器用 query 定义的 trait | ir-graphs Q2、Q4、Q6–Q10；S-01、S-09、S-25；并发 F18、F22；P-30 |
| 内存 | arena 用 bumpalo；分配失败采用混合策略（用户决定大小、或大于 64 KB 的必须可失败）；全局分配器不计数，内存视图用 jemalloc 统计；保留 11049（口径偏差见 A.4）；`alloc(0)` 照 PageArena 返回失败；自持 arena 的对象按 (a)(b)(c)；线程安全的 arena 用 `Latch<Arena>`；内存视图保留表结构 | memory Q2–Q5、Q7、Q8；S-02、S-23；并发 F6；行为 F15、F18 |
| 并发 | 数据放进锁里（7 条规则）；TCRWLock 用 `BigReaderLatch`、DRWLock 用 `ShardedRwLatch`、桶锁用 `BucketLatch`；bitlock 按类型照抄等待策略；`inc_update` 先读后 CAS；PAUSE 在 aarch64 上发 `yield`；定时任务持 `Weak` 或 `&'static`，保留 cancel + wait；ObPriorityQueue2 移植；信号在 main 的第一句屏蔽；`memory_dump` 改成不靠信号的探测；死代码确认后不翻译 | concurrency Q3、Q6、Q7、Q8 附带问题、Q9、Q11；并发 F4–F17、F20–F22、M1–M14 |
| 存储 | keybtree 移植 QClock/RetireStation；回调经 `MemtablePin`；unsafe 核心是独立 crate；对象池首版去掉；宏块引用在桶锁下读-改-写；tablet 指针表用 `BucketedHashMap`；柔性数组用字节视图 | storage-txn Q2–Q4、Q6；S-04、S-08、S-21；并发 F3 |
| 解析器、第三方库、生成器 | bison 3.8 的替换列为迁移后的独立可选阶段；ICU 保留 69.1；生成器能用 build.rs 的就用 build.rs，内部表保留 Python DSL、只换发射器，删除 4 个只为 C++ 服务的生成器（解析器备忘里叫"Ga 方案"）；`ObItemType` 由生成器产出新类型；用户消息每条一个带类型的格式化函数；bindgen 离线生成并入库，记录 `-D` 参数；RapidJSON 移植；gRPC 用 tonic + prost；protobuf-c 换成 prost | parser Q1 附带问题、Q4–Q7、Q9–Q11；P-17、P-36、S-05、S-26 |
| 约定 | 格式串由 `ob_printf!` 编译期检查；自有日志门面，日志行格式与 C++ 一致；`operator[]` 越界 abort（前提见 A.3）；参与对齐的 profile 关闭溢出检查；枚举一律译成新类型加关联常量和模块级常量；`ObIArray&` 参数译成 `&[T]`/`&mut dyn ObIArray<T>`；`TO_STRING` 全部逐字节复刻；edition 2021；clippy 用放开清单加禁用清单；第 3 步可见性一律 `pub`；接收者按是否共享；`char` 和 `long` 用平台类型；NaN 的拼写按平台的 C 库 | conventions Q3、Q4、Q7、Q8、Q10–Q12、Q14、Q15；P-11；行为 F13、F14 |
| 分层 | 全局状态用 `OnceLock` 加槽位；模块 `Box::leak`、永不释放，生命周期方法取 `&self`；槽位不解绑，用 `stopped` 标志代替；query 是独立 crate；sql 与 storage 两个方向都反转；在 seam 表里拆环，基线保持不变；生产构建里被覆盖的 6 个 weak 符号改成下移或钩子；单例按原版的动态初始化顺序 force | layering Q3–Q6、Q8、Q10；S-03；并发 M12 |

### A.3 有前提条件的决定

| 决定 | 前提 | 在哪一步确认 | 前提不成立时 |
|---|---|---|---|
| 排序以 macOS libc++ 19.1.2 为准（D5） | 在 Linux 上复跑 C++ 版裁判（DEV-005），没有因排序差异而失败 | 第 3 步之前（一次裁判运行，成本很低） | 让裁判对并列行不敏感：`--sorted_result` 或补全 ORDER BY |
| sql 不拆（0.3） | 切片上冷 `cargo check` ≤ 10 分钟且峰值内存 ≤ 12 GB | 第 2 步 | 沿 IR 边界切出 `sql_ir`/`sql_engine`，路径规则和 seam 表随之更新 |
| 子作用域截断（0.6） | 清单第 11 类没有发现外层引用作用域内新建的节点 | 1c | 有外层引用的节点改在外层作用域分配；这类点很多时，改用分层 id（ir Q2-B），重新审查 0.6 |
| `operator[]` 越界 abort（§2.5） | 用原版跑一遍裁判、保留 observer.log，日志里没有 "idx out of range" | 第 3 步之前 | 命中的点改成不 abort 的写法（如空范围返回空切片），记入清单 |
| 原生栈的帧膨胀系数（0.8） | 用 `cases/join_many_table`、`sqlancer_optimizer_regressions.test:19`、`cases/subquery.optimizer_subquery_bug.test:297` 实测 Rust 与 C++ 的栈用量 | 第 5 步 | 系数不足以对齐时，在 `ObOperator::open`、`get_next_*`、`ObExpr::eval` 的递归点加 `smart_call!` |
| D17 选 (b) | 重建参照后重跑裁判，变化的期望行都能逐行解释为 FMA 差异 | 第 3 步之前 | 改用 (a) |
| Windows、Android 的对齐（D18） | macOS arm64 和 Linux x86-64 的对齐已通过 | 第 6 步之后 | — |
| 性能带 | 默认值：裁判里单条语句的耗时不超过 C++ 的 2 倍（防止撞上语句和事务超时）；sysbench `oltp_point_select`、`oltp_read_only`、`oltp_write_only` 在 1 线程和 32 线程下吞吐不低于 C++ 的 80%；冷启动不超过 1.5 倍；空闲 RSS 不超过 1.3 倍 | 单条语句耗时在第 6 步检查；其余在第 6 步之后测 | 不达标的项按 `PERF(port)` 标记排优先级 |
| 模型计划的补充 | 1d：API 设计 `claude-opus-5-5`，实现 `claude-sonnet-5-5`，审查者 `claude-opus-5-5`；`seams.tsv` 和 `types.tsv` 的审查者 `claude-opus-5-5`；全部 max effort | 本关口 | — |

### A.4 接受的偏差与裁判改动

**接受的偏差**：代码里标 `DIVERGE(port): <项>`，`<项>` 取下表第一列。

| 项 | 内容 | 裁判怎么处理 |
|---|---|---|
| `addr-order` | 按地址顺序迭代的 3 处改成插入顺序，只影响 EXPLAIN 的输出 | 裁判里的 EXPLAIN 都在 `--disable_result_log` 下，无需改动 |
| `plan-mem` | 计划内存大小的口径不同 | 相关视图的数值归一化 |
| `mem-view` | 内存视图的内容由 jemalloc 统计和 `MemBudget` 填充 | 只比较表结构（现状如此） |
| `malloc-backend` | 不支持用 `MALLOC_BACKEND=obmalloc` 切换分配器 | 裁判用默认后端 |
| `oom-abort` | 小的固定大小分配失败时进程 abort，C++ 是本语句报 4013 | 裁判没有 4013 期望 |
| `query-mem-limit` | 11049 的计量口径不同，触发点和报错文本里的数字不同 | 裁判没有 11049 期望 |
| `module-leak` | 模块对象永不释放 | 用户不可见 |
| `log-prefix` | 11 个没有 `USING_LOG_PREFIX` 的单元统一用 COMMON | 日志不参与比较 |
| `stack-depth` | 第 5 步调整之后仍存在的递归深度差异 | 按实测结果逐项决定 |

**裁判改动清单**：第 5 步的前提；改完用故意改坏的代码重新验证一遍，像 00b 那样。

1. 跨二进制场景两个：C++ 写、Rust 读，正常重启和 kill -9 各一个（0.10）；
2. 关闭 `enable_rich_error_msg`（0.11）；
3. 计划内存的数值归一化（`plan-mem`）；
4. 三个会话争用同一行的行锁场景，检查唤醒顺序和 1205 超时（0.11）；
5. 生命周期场景加"SIGUSR1 优雅停机"探针（0.8）；
6. 冒烟测试（Linux）检查所有线程的 `SigBlk`（0.8）；
7. 如果 D17 选 (b)：按重建后的参照重录因 FMA 改变的期望行（DEV-016）。

## 附录 B：事实来源

| 文件 | 内容 |
|---|---|
| `migration/design/survey/ir-graphs.md` | SQL 编译期和运行期对象图 |
| `migration/design/survey/memory.md` | 分配器、限额、分配失败 |
| `migration/design/survey/concurrency.md` | 线程、锁、原子操作、无锁结构、信号 |
| `migration/design/survey/storage-txn.md` | memtable、MVCC、事务、句柄、palf、持久化格式 |
| `migration/design/survey/layering.md` | crate 边界、逆向依赖、全局状态、迁移策略 |
| `migration/design/survey/parser-generators-thirdparty.md` | 解析器、生成器、第三方库 |
| `migration/design/survey/conventions.md` | 错误、日志、序列化、容器、值类型、语言构造 |
| `migration/design/survey/scripts/<主题>/` | 计数脚本，用法见同目录的 `README.md` |
| `migration/design/review/process-consistency.md` | 对抗审查：流程与内部一致性（P-01…P-36） |
| `migration/design/review/soundness.md` | 对抗审查：内存安全与健全性（S-01…S-26） |
| `migration/design/review/concurrency-performance.md` | 对抗审查：并发正确性与性能（F1…F22、M1…M15） |
| `migration/design/review/behavioral-fidelity.md` | 对抗审查：行为一致性（F1…F18、M1…M7） |
| `migration/design/review/verification.md` | 30 条高影响事实的源码复核（C01…C30） |
| `migration/design/review/dispositions.md` | 每条发现的处理 |
