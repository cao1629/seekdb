# 对抗审查：行为一致性（RULEBOOK.md 草稿 v0.1）

> 审查范围只有一类问题：按规则翻译出来的代码，会不会改变通过 MySQL 协议看得到的行为，进而让 00b 裁判或第 6 步对齐失败。
>
> 方法：
> - 通读 `migration/RULEBOOK.md`、7 份调查备忘、`00b-judge-report.md`、`judge/README.md`，逐条对照 `src/`（基线 `7d907abfa`）和 `migration/judge/suites/` 下 287 个用例的 `.test`/`.result`。
> - 对 00b 阶段留下的原版二进制 `build_release/src/observer/seekdb` 做了反汇编扫描（`otool`），并读了 `build_release/compile_commands.json` 里的真实编译参数。
> - 在 `/tmp` 用基线编译器（`deps/3rd/.../clang++` 17.0.6，`-O2 -march=armv8-a+crc+lse`，`MacOSX15.sdk`）编译了几段从源码原样摘出的小函数，做数值对照。
> - 没有构建项目，没有改动仓库里除本文件以外的任何东西。

## 1. 结论

1. 规则手册把"逐行移植算法"当成行为一致的充分条件，但有两处会在裁判上直接出错：**向下转型的规则自相矛盾，而且没有按 C++ 的 is-a 定义"类相符"**，含 IFNULL/NVL 的查询会崩溃或报 4016（F2）；**基线里浮点乘加是否融合（FMA）是按编译单元混合的**，Rust 永不融合，文档对此一字未提（F1）。
2. 第二类问题是平台和构建配置：基线是 macOS arm64、`-DNDEBUG`、clang 17 和 SDK 自带的 libc++ 19.1.2。文档没有规定预处理条件怎么译、对齐用哪套构建，也没有钉住 libc++ 版本；栈深规则只覆盖了 SMART_CALL，没覆盖 110 处 `check_stack_overflow`；macOS 上信号的真实语义和文档写的不一样（F3、F4、F8、M1）。
3. 换库和换容器的几处接受偏差，都有能从协议上看到的后果：`std::regex`→regress、libcurl→ureq、OpenSSL 缺 crypt(3)/EVP_BytesToKey、fast_float→std、DashMap 改变 SHOW PROCESSLIST 行序和行锁唤醒顺序。它们大多不在裁判里，但用户看得到（F6、F7、F9–F12）。
4. 已核对、没有发现问题的有：OOM 改 abort（裁判里没有 4013/11049 期望）、按地址顺序迭代的 3 处（只影响 EXPLAIN；其中 `ob_transform_utils.cpp:7953` 所在的 `extract_shared_expr` 没有调用者；裁判里的 EXPLAIN 都在 `--disable_result_log` 下，只检查能否执行）、死代码清单（确认没有调用者）、警告缓冲区"64 条环形覆盖、每条 512 字节"、SIGTERM→SIGKILL、ObHashMap 迭代序。

## 2. 发现

### F1【高】浮点乘加融合：基线是"按编译单元混合"的，Rust 永不融合，文档没有任何规则

- **规则**：
  - §0.11.4"以下逐行移植，并配上以 C++ 为准的金标准测试"；
  - §2.13 运算符重载"算术 → `std::ops`"；
  - §0.14"Boost.Geometry…新写 cxx 薄 shim…WKB 字节进出"，以及 RapidJSON"用 C++ shim 做差分测试的参照"。
  - 全文没有出现 FMA 或 fp-contract。
- **事实**：
  - 基线用 clang 17.0.6 编译，参数里没有 `-ffp-contract`。`clang++ -###` 显示 cc1 默认带 `-ffp-contract=on`，也就是同一个表达式里的 `a*b+c` 会融合成 `fmadd`。
  - 但 s2 的头文件 `s2/_fp_contract_off.h:52` 写了 `#pragma STDC FP_CONTRACT OFF`，它经由下面 4 个头文件包含的 `share/geo/ob_s2adapter.h`（→`s2/s2cell.h:23`）进入编译单元，并对该单元剩余的全部代码生效。在 unity 构建下，就是同组里排在后面的所有文件：
    - `src/sql/ob_sql_utils.h:26`
    - `src/data_plane/api/data_plane/access/ob_tablet_scan.h:26`
    - `src/share/geo/ob_geo_utils.h:24`
    - `src/sql/rewrite/ob_query_range_provider.h:23`
  - 结果是融合与否逐函数不同。对原版二进制的扫描结果：含标量 `fmadd/fmsub/fnmadd/fnmsub` 的 OceanBase 相关函数共 422 个，其中 393 个是 OB 类型上的 Boost.Geometry 实例，29 个是 OB 自己的代码或用 OB 类型实例化的头文件库（RapidJSON 占 6 个）。
  - 反例：`ObExprBM25::doc_token_weight`（`ob_expr_bm25.cpp:146-150`）在基线里**没有**融合，反汇编是 `fmul/fadd/fmul/fadd/fdiv`；可是用同一编译器单独编译同一个函数，会生成两条 `fmadd`。所以"全部融合"和"全部不融合"都复现不了基线。
- **具体例子**：
  - **JSON schema 的 multipleOf**：
    - `ObJsonSchemaUtils::check_multiple_of`（`src/oblib/common/json_type/ob_json_schema.cpp:3374-3376`，`res = abs_json - mod_ans * abs_multi; if (res > 0.0) 不合法`）。它在基线里被内联进 `ObJsonSchemaValidator::check_multiple_of`，反汇编是 `fdiv; frintm; fmsub; fcmp`。
    - 按 §2.13 会译成 `a - q * b`，两次舍入。
    - 实测：`JSON_SCHEMA_VALID('{"multipleOf":0.1}', '1.1')`，基线判不合法（0），不融合的写法判合法（1）。`0.30000000000000004` 对 0.1、`0.6000000000000001` 对 0.2 也是这样。
  - **RapidJSON 解析长数字**：
    - `GenericReader::ParseNumber`（`deps/3rd/.../rapidjson/reader.h:1719` 和 `:1773`，`d = d * 10 + digit`）在基线的 6 个实例里都是融合的。
    - 用仓库自带的头文件分别以融合、不融合编译后实测：360 个 17–19 位有效数字的输入里，32 个解析结果不同。例如 `1.98765432109876543` 解析成 1.9876543210987658（基线）和 1.9876543210987654（Rust）；`1987654321098765431234` 解析成 1.9876543210987654e+21 和 …651e+21。
    - §0.14 把"C++ shim"当差分参照，但这个参照怎么编译（clang 默认融合；GCC 或 x86 没有 FMA 指令时不融合）会直接改变差分结论。
  - **GIS**：`ObGeoFuncDistanceImpl::EvalWkbBi<ObWkbGeomPoint,ObWkbGeomPoint>::eval`（2 条）、点到线段的 `projected_point<…>`（4–6 条），以及 `side_calculator`、`segments_tupled` 等，在基线里都是融合的。它们都会进入 bgk_shim。如果 shim 的编译单元经 `ob_geo_utils.h` 带进了 s2 的 pragma，或者用 GCC/`-ffp-contract=off` 编译，结果的末位就会变；反过来，基线里没融合的实例在 shim 里被融合，也会变。
  - **向量**：`ObVectorL2Distance<float>::l2_square_flt_func`、`ObVectorNormalize::L2_normalize_vector`、`ObVectorKmeansClusterHelper::get_nearest_probe_centers` 在基线里是融合的。aarch64 上 `l2_square_flt_func` 走的是标量的 `normal::l2_square_flt`（`src/storage/vector_type/ob_vector_l2_distance.cpp:70-84`）；kmeans（`ob_vector_kmeans_ctx.cpp:322`）、选探针中心（`ob_vector_common_util.cpp:87`）和 IVF 扫描时的距离计算（`src/sql/das/iter/ob_das_ivf_scan_iter.cpp:1078`）都依赖它。
- **用户能看到的差异**：
  - JSON 校验的结论翻转；
  - 17 位以上的 JSON 数值末位不同；
  - GIS 距离和叠加结果的末位不同；
  - IVF 近似检索在临界处的候选集不同。
- **裁判或用户在哪里看到**：
  - geometry 的 39 个用例。例如 `suites/mysqltest/cases/geometry.st_distance_mysql.result` 里有 90 个 12 位以上小数的值，点到线、点到多边形的距离走 `projected_point`。
  - IVF 用例：`cases/vector_index.vector_index_ivfflat_*`、`scenarios/vector.ivf_topk`。
  - JSON schema 和长数字 JSON 不在裁判里，但用户看得到。
  - 另外，DEV-005 要在 Linux x86-64 上复验，而那里的 C++ 本身就不融合（`ob_sql` 没有 `-mfma`），数值差异会和排序差异混在一起，D5 的前提按现在的写法无法判定。
- **建议改法**：新增 §2.14"浮点求值"：
  1. 以基线二进制为准，生成"融合清单"：扫描原版 seekdb 里的 `fmadd/fmsub/fnmadd/fnmsub`，映射到符号和源码表达式；也可以用 `-g -emit-llvm` 找 `llvm.fmuladd` 的 `!dbg` 行号。
  2. Rust 只在清单列出的表达式上写 `f64::mul_add`/`f32::mul_add`，并标 `PARITY(port): fp-contract`；其余一律用普通运算。清单进缺口清单。
  3. C++ 的 shim 和 -sys（bgk_shim、RapidJSON 差分参照、s2/vsag 包装）每个编译单元显式写 `#pragma STDC FP_CONTRACT ON/OFF`，复刻基线状态；CI 按符号比对 fmadd 计数，必须与基线一致。
  4. 备选方案：先用 `-ffp-contract=off` 重建基线，再重录 00b 的期望输出，彻底消除"按单元混合"。三选一写进附录 A。

### F2【高】向下转型：R8 与 §2.13 互相矛盾，而且都没有按 C++ 的 is-a 定义"类相符"

- **规则**：
  - R8"向下转型（1,350 处）改成带检查的访问函数，例如 `as_column_ref()`，类不符时返回 `OB_ERR_UNEXPECTED`"；
  - §2.13"向下转型 `static_cast<D*>(b)`：对有类型标签的类层次，先查标签再转，标签不符就 abort"。
- **具体例子**：
  - `src/sql/rewrite/ob_transform_simplify_expr.cpp:1254` 和 `:1315` 对 IFNULL/NVL 做 `static_cast<ObOpRawExpr *>(expr)`。
    - IFNULL 是 `ObSysFunRawExpr`。它继承 `ObOpRawExpr`（`ob_raw_expr.h:3868`），但构造时设置的标签是 `EXPR_SYS_FUNC`（:3874），而 `is_op_expr()` 只认 `EXPR_OPERATOR`（:1848）。
    - `remove_dummy_nvl`（:705 调用，:1142 起）对每条语句的 select、having、order by 和条件都会走到这里。
  - 同类的还有：
    - `src/sql/optimizer/ob_table_location.cpp:2613`：在 `is_const_or_param_expr()` 之后把可能是 `ObExecParamRawExpr`（`ob_raw_expr.h:2730`，继承 `ObConstRawExpr`）的表达式转成 `ObConstRawExpr*`；
    - `ObInsert/Update/DeleteStmt : ObDelUpdStmt`；
    - `ObLogInsert/Update/Delete : ObLogDelUpd`。
- **按规则的译法**：把"标签"理解成 `ExprClass`，`as_op()` 只接受 `RawExprKind::Op`，SysFunc 落进 `_` 分支。按 §2.13 进程 abort，按 R8 返回 `OB_ERR_UNEXPECTED`。
- **用户能看到的差异**：任何含 IFNULL/NVL 的查询，要么实例崩溃，要么报 4016（Internal error）。C++ 里这条转换是合法的，结果正常。
- **裁判或用户在哪里看到**：
  - `tools/deploy/mysql_test/test_suite/expr/t/expr_locate.test:195`（`select ifnull(locate('a','a'),2.345)`）；
  - `tools/deploy/mysql_test/t/sqlancer_optimizer_regressions.test:19`；
  - `yonyou_test/t/yonyou.test`；
  - 系统视图的定义里也有 NVL/IFNULL（例如 `tools/deploy/mysql_test/r/mysql/information_schema.result:914` 打印出的视图定义），凡是查询这类视图，都会走到同一处转换；
  - `scenarios/hybrid.search_fusion.result` 里由 DSL 生成的 SQL 也带 ifnull。
  - 按 §2.13 abort 时，用例之后的所有语句都会失败。
- **建议改法**：合并成一条规则："带检查的向下转型 = C++ 的 is-a。访问函数接受目标类及其全部派生类对应的变体，接受集由脚本从类层次生成、写进缺口清单；只有 C++ 里 is-a 不成立（真正的未定义行为）时才返回 `OB_ERR_UNEXPECTED`，一律不 abort。"同时删掉 §2.13 的"标签不符就 abort"，并在 0.6 写明 `RawExprKind` 要提供 `as_op()`、`as_const()`、`as_sys_func()` 这类按 is-a 取值的访问函数。

### F3【中】栈深：只规定了 SMART_CALL，110 处 `check_stack_overflow` 和原生栈大小都没有规则

- **规则**：
  - §0.8"`SMART_CALL`…改成 `smart_call!`，基于 `stacker`。剩余栈不足 64 KB（`_LARGE` 为 192 KB）时扩栈，每次 2 MB；每个线程累计超过 10 MB 时返回 `OB_SIZE_OVERFLOW`…第 5 步实测之前不放宽这个上限"；
  - §0.11.5"如果裁判里有依赖确切深度的用例，记为偏差"。
- **具体例子**：
  - 无参的 `check_stack_overflow()` 只检查**当前栈段**剩余是否少于 32 KB（`src/oblib/lib/utility/ob_common_utility.cpp:35`、:45-82），不扩栈，直接返回 `OB_SIZE_OVERFLOW`（`ob_common_utility.h:39-44`）。调用点包括：
    - `ObOpSpec::create_operator_recursive`（`src/sql/engine/ob_operator.cpp:283`）；
    - `ObOperator::open`（:589；子算子在 :619 原生递归 `children_[i]->open()`）；
    - get_next 的 `check_stack_once`（:517-526）；
    - `ObExpr::eval`（`ob_expr.cpp:640`、:694）；
    - 解析器回调 `check_stack_overflow_c`（`ob_sql_utils.cpp:1964` → `parse_node.c:657`、:696，报 `OB_PARSER_ERR_SIZE_OVERFLOW`）；
    - PL 解析器（`ob_pl_parser.cpp:28`）。
  - 这些都跑在 worker 的原生栈上，栈大小是 256 KB 减 16 KB 再减 17 KB（`ob_server.cpp:1990-1993`）。
  - 计量口径也和文档写的不一样：`ob_smart_call.h:72-90` 里 `all_stack_size` 的初值是原生栈大小，扩栈单位是 `2MB - 2*17KB`（:29）。按文档"每次 2 MB、累计 10 MB"去实现，可扩的段数会从 4 变成 5。
- **按规则的译法**：
  - `check_stack_overflow` 没有统一译法，翻译者可能去读 pthread 的栈属性。这样一来，在 stacker 的扩展段上算出的剩余量是错的；而 C++ 的 `get_stackattr` 会随扩栈切换到新栈段（`call_with_new_stack` 里的 `set_stackattr`，`ob_smart_call.h:51-52`）。
  - 原生栈大小照搬 256 KB，而 Rust 栈帧更大。
- **用户能看到的差异**：同一条 SQL 在 Rust 中更早报 4019"Size overflow"，或者解析阶段报 `OB_PARSER_ERR_SIZE_OVERFLOW`；扩栈段数不同时，也可能出现相反方向的差异。
- **裁判或用户在哪里看到**：
  - 裁判里没有期望深度报错的用例：4019 只出现在 `cases/array.array_arith_op_mysql` 的数组上限处。所以任何因深度出现的 4019 都是失败。
  - 风险最大的是：`cases/join_many_table.test`（35 层 LEFT JOIN 链，open 原生递归 35 层以上）、`sqlancer_optimizer_regressions.test:19`（单行 20 层括号嵌套）、`cases/subquery.optimizer_subquery_bug.test:297`（12 个 SELECT）。
- **建议改法**：
  1. 在 §0.8 加上：`check_stack_overflow(reserved)` 统一译成"`stacker::remaining_stack()` 小于 reserved"，按当前栈段计算，默认 reserved 为 32 KB。
  2. 把 `all_stack_size` 的口径（初值等于原生栈大小）和扩栈单位 `2MB-34KB` 写成常量。
  3. 第 5 步用上面 3 个用例测 Rust 的栈帧膨胀系数：原生栈按系数放大，或者在 `ObOperator::open`、`get_next_*`、`ObExpr::eval` 的递归点加 `smart_call!`，二选一写进附录 A。

### F4【中】macOS 上信号的真实语义与 §0.8 不符，照字面翻译会让实例起不来

- **规则**：§0.8"`ObSignalHandle` 改用 `nix` 实现，保留：SIGPIPE 忽略，SIGTERM → raise(SIGKILL)…SIGUSR1 优雅停机，实时信号 40–64"。
- **具体例子**：
  - `src/observer/ob_signal_handle.cpp:105-117` 把 40–64 号加进屏蔽集，并检查 `sigaddset` 的返回值。
  - 在基线平台 macOS 上，`sigaddset` 是 SDK 里的宏，`__sigbits(signo)` 对大于 32 的信号号返回 0（`MacOSX15.sdk/usr/include/signal.h:117-122`），也就是静默忽略并返回 0。
  - `sigtimedwait` 在 macOS 上用无超时的 `sigwait` 模拟（`ob_signal_handle.cpp:25-37`），析构时发 SIGUSR1 把它唤醒（`ob_signal_handle.h:65-70`）。
- **按规则的译法**：
  - nix 的 `Signal` 枚举没有实时信号，在 Linux 上也表示不了 40–64；`libc::sigaddset` 这个函数在 macOS 上对大于 32 的信号号返回 -1。
  - 照 C++ "返回非 0 就报 `OB_ERR_UNEXPECTED`"翻译，`change_signal_mask` 会失败。随后 `main.cpp:678-689` 里的 `opts` 为空，启动失败。
- **用户能看到的差异**：macOS 上实例起不来。
- **裁判或用户在哪里看到**：294 个用例全部在 wait-ready 或 start 一步失败，形态和 00b 的变异 M6 一样。
- **建议改法**：把 §0.8 的这一条改成按平台写：
  - Linux 用 `libc::sigaddset` 加原始信号号；
  - macOS 跳过大于 32 的信号号且不报错，复刻 SDK 宏的语义；
  - `sigtimedwait` 在 macOS 上沿用"`sigwait` + 析构时发 SIGUSR1 唤醒"。
  - 同时在生命周期场景里加一个"SIGUSR1 优雅停机"的探针。

### F5【中】bgk_shim 只规定了"WKB 进出"，没规定几何模型的 traits 和完整的异常映射

- **规则**：§0.14"Boost.Geometry 1.74 | 保留。新写 cxx 薄 shim（`bgk_shim`）…WKB 字节进出；C++ 异常在 shim 里映射成 `OB_ERR_BOOST_GEOMETRY_*`；编排逻辑译成 Rust"。
- **具体例子**：
  - OB 把 WKB 视图和树类型都注册成**逆时针、闭合**：`src/share/geo/ob_geo_bin_traits.h:126-134`、:286-294，`ob_geo_tree_traits.h:98-104`、:264-270。Boost 默认的 `bg::model::polygon` 是顺时针。buffer、difference、union 的结果都写在 OB 的树类型里（如 `ob_geo_func_buffer.cpp`）。
  - 异常映射 `ob_boost_geometry_exception_handle`（`ob_geo_dispatcher.h:1313-1362`）也不只有 `BOOST_GEOMETRY_*`：
    - `std::bad_alloc` → `OB_ERR_STD_BAD_ALLOC_ERROR`；`std::domain_error`、`length_error` 等各有对应的码；
    - `boost::numeric::positive_overflow`/`negative_overflow` → `OB_OPERATE_OVERFLOW`；
    - `catch(...)` → `OB_ERR_GIS_UNKNOWN_EXCEPTION`；
    - catch 子句的顺序有意义。
- **按规则的译法**：新写的 shim 用 Boost 默认的模型类型承接结果，只映射 bg 的异常。
- **用户能看到的差异**：ST_Buffer、ST_Union、ST_Difference 等输出的环方向和顶点顺序反过来；`bg::correct` 的结果也不同；异常场景下的错误码不同。
- **裁判或用户在哪里看到**：geometry 的 39 个用例，例如 `cases/geometry.st_buffer_mysql`、`geometry_bugfix_mysql`，它们都以全精度坐标比对 WKT。
- **建议改法**：在 §0.14 写明：
  - shim 内部继续用 OB 的 bin/tree 几何类型和它们的 traits 注册，复制过去不改；
  - 输出的 WKB 由同一套 OB 写出逻辑生成；
  - 异常映射整段照搬上面那个函数（17 个分支，顺序不变）；
  - 每个算法入口的融合状态按 F1 处理。

### F6【中】ObLinkHashMap 一族改成 DashMap，SHOW PROCESSLIST 的行序会变成每次启动都不同

- **规则**：§0.8"`ObLinkHashMap`/`ObLinearHashMap`/`FixedHash2`：`DashMap<K, Arc<V>>`"，§2.7 同。
- **具体例子**：
  - 会话表是 `ObAllocatingLinkHashMap`（`src/sql/session/ob_sql_session_mgr.h:171-174`、:208）。
  - `SHOW PROCESSLIST` 按 `for_each_session` 的迭代顺序输出，不排序（`src/observer/virtual_table/ob_show_processlist.cpp:60`）。
  - C++ 底层的 DCHash 是按哈希值排序的有序链表，顺序完全由会话 id 决定。
- **按规则的译法**：DashMap 默认用 `RandomState`（SipHash，每个进程随机取种子），按分片迭代。
- **用户能看到的差异**：`SHOW PROCESSLIST`、`information_schema.PROCESSLIST` 不带 ORDER BY 时，行序每个进程都不同，并且和 C++ 不同。
- **裁判或用户在哪里看到**：裁判目前只在 information_schema 用例里比对表结构（`information_schema_desc`），没有比对行；但这是用户可见的，用户脚本和其他测试套件里不加排序的 `SHOW PROCESSLIST` 很常见。
- **建议改法**：§2.7 加一条：凡是迭代结果会进入用户输出的（先由缺口清单列出，至少包括会话表），不用 DashMap，改为移植 DCHash 的有序链表语义，或者在迭代处按 C++ 的哈希序排序；其余 DashMap 一律用固定哈希器，不用 `RandomState`。

### F7【中】FixedHash2 改成"值是队列"的 DashMap，会改变行锁的唤醒顺序

- **规则**：同 §0.8。concurrency 备忘写明"锁等待表，一个哈希值挂多个等待者，值类型改成队列"。
- **具体例子**：
  - `ObLockWaitMgr` 用的是 `FixedHash2<ObLockWaitNode>`（`src/storage/memtable/ob_lock_wait_mgr.h:186-188`），一个按 `compare()` 排序的链表：先比哈希，再比 `recv_ts_`（请求到达时间），再比 `abs_timeout_`（`src/oblib/rpc/ob_lock_wait_node.cpp:69-84`）。
  - `fetch_waiter` 唤醒同一行上排在最前、已经可以运行的等待者（`ob_lock_wait_mgr.cpp:376-405`）。
  - 被唤醒后重试仍然失败的请求，带着原来的 `recv_ts_` 重新插入，所以仍排在前面。
- **按规则的译法**：按入队先后排（FIFO），重新入队就排到队尾。
- **用户能看到的差异**：三个以上会话等同一行时，谁先拿到锁、谁先因 1205 超时都会不同，最终写入的值也可能不同。
- **裁判或用户在哪里看到**：现有的 `scenarios/acid.row_locks`、`acid.write_conflicts` 只有两个会话，覆盖不到；用户可见。
- **建议改法**：§2.7 写明锁等待表每个键是一个按 `(recv_ts, abs_timeout)` 排序的有序集合（`BTreeMap` 或有序 `Vec`），唤醒和重新插入的语义照抄 `fetch_waiter`/`wait`。同时在 ACID 场景里加一个三会话争用同一行的用例。

### F8【中】排序与堆：没钉住 libc++ 的版本，std 的堆算法没有规则

- **规则**：§0.11.3"`ob_sort` 逐行移植**基准平台**的 `std::sort`…基准平台定为 macOS（libc++）"；§1"排序：…禁用 `sort_unstable*`；`sort_by` 只用来对应 `std::stable_sort`"。
- **具体例子**：
  - 基线用的是 `MacOSX15.sdk` 里的 libc++ 头文件（`_LIBCPP_VERSION 190102`；`clang++ -###` 显示 `-internal-isystem …/MacOSX15.sdk/usr/include/c++/v1`），不是 deps 里 clang 17 自带的版本。libc++ 历次版本改过 introsort 的分区和插入排序细节。`ob_sort.h:27-41` 就是 `std::sort`。
  - 堆算法没有规则，`BinaryHeap` 也不在禁用表里。基线里用到 `std::push_heap/pop_heap/make_heap` 的地方：
    - PX 归并排序接收端的 `ObRowHeap`（`src/sql/engine/px/exchange/ob_row_heap.h:248-250`、:267-269）；
    - 哈希分组的热点值 top-K（`ob_hash_groupby_op.cpp:2617-2626`）；
    - 计划缓存淘汰（`ob_plan_cache.cpp:227`、:243、:1312）。
  - libc++ 19 的 `__floyd_sift_down` 在左右子节点相等时选左子（`__algorithm/sift_down.h:98`），Rust 的 `BinaryHeap` 相等时选右子。
- **按规则的译法**：照搬"某个 libc++"的 introsort；堆用 `BinaryHeap` 或者自己写一个。
- **用户能看到的差异**：ORDER BY 有并列值时的行序；并行 ORDER BY 里，不同通道并列行的先后；哪些值被当成热点，从而影响 PX 的数据分发。
- **裁判或用户在哪里看到**：D5 覆盖的全部并列行序。PX 加 ORDER BY 的用例（如 `cases/vector_index.vector_index_bugfix1.test:305`）有并列的可能，本次没有逐一核实并列行的内容是否相同。
- **建议改法**：
  1. §0.11.3 写死基准为"MacOSX15.sdk 的 libc++ 19.1.2，classic introsort 路径"，并把 SDK 版本列进 ENV-1 的固定项。
  2. 新增一条：`std::push_heap/pop_heap/make_heap/sort_heap` 同样逐行移植这个版本；翻译代码禁用 `BinaryHeap`；`ObBinaryHeap` 按原代码移植。

### F9【中】`std::regex` 换成 regress 违背 D11 自己的判据

- **规则**：§0.14"`<regex>`（JSON schema 的 pattern）| `regress`，ECMAScript 语义"。D11 的判据是"库的输出就是它的算法本身，就保留原生库"。
- **具体例子**：
  - `src/oblib/common/json_type/ob_json_schema.cpp:4015`：`std::regex pattern(start, len)`，抛出 `regex_error` 就判定模式非法；
  - :4037-4039：`std::regex_search(t_start, pattern)`，主体按 C 字符串传入，遇到第一个 NUL 就截断，按 char 粒度匹配。
  - 基线是 libc++ 的 std::regex，ECMAScript 方言：没有后行断言和命名组，`.` 匹配一个字节。
- **按规则的译法**：regress 按 ES2018+ 的语法和 Unicode 码点匹配。
- **用户能看到的差异**：
  - `JSON_SCHEMA_VALID('{"type":"string","pattern":"^.{2}$"}', '"中文"')`：基线返回 0（6 个字节），regress 返回 1（2 个码点）；
  - `(?<=a)b` 在基线里是非法模式，regress 接受；
  - 含 `\u0000` 的字符串，基线截断后再匹配。
- **裁判或用户在哪里看到**：裁判里 json_schema 出现 0 次；用户可见。
- **建议改法**：二选一写进附录 A：保留 libc++ 的 std::regex，用 cxx shim 调用；或者手写一个复刻 libc++ ECMAScript 方言和字节语义的 matcher，并用基线做差分。不能直接用 regress。

### F10【中】libcurl 换成 ureq：AI 函数的错误码文本、URL 判定、超时、代理和根证书都没有规定

- **规则**：§0.14"libcurl | 换成 `ureq` + rustls"。
- **具体例子**：
  - `src/sql/engine/expr/ob_expr_ai/ob_ai_func_client.cpp:107-128`：
    - `CURLE_URL_MALFORMAT` → `OB_INVALID_ARGUMENT`，文本 "error url format"；
    - 其余 → `OB_CURL_ERROR`，文本 `"curl error, curlerror code is %d"`，用的是 CURLcode 的数值，如 6、7、28、35、60。
  - 超时：`CURLOPT_TIMEOUT` 按秒算，`CURLOPT_CONNECTTIMEOUT_MS` 为 10000（:410-412）；`src/query/vector/ob_vector_embedding_handler.cpp:815` 按 `CURLE_OPERATION_TIMEDOUT` 单独分支。
  - libcurl 默认读 `http_proxy`/`https_proxy` 环境变量，CA 用 OpenSSL 的系统证书路径。
- **按规则的译法**：ureq 的错误类型没有 CURLcode；URL 由另一套解析器判定；rustls 用 webpki 或 native 根证书。
- **用户能看到的差异**：AI 函数和 embedding 失败时的错误码、报错文本里的数字都不同；哪些 URL 算格式错误不同；有代理环境变量或企业 CA 时，能不能连通也不同。
- **裁判或用户在哪里看到**：裁判没有网络用例；用户可见。
- **建议改法**：在 §0.14 加一张"ureq 错误 → CURLcode"映射表，按 libcurl 8.12.1 的数值；规定 URL 合法性判定要复刻 curl；规定代理读取哪些环境变量、根证书来源；再写成测试向量，例如用不可解析的主机、拒绝连接、超时、错误证书逐一比对。

### F11【中】OpenSSL 换成 RustCrypto：白名单里没有 crypt(3)、EVP_BytesToKey 和 CSPRNG

- **规则**：§0.14"OpenSSL 1.1.1u | 换成 RustCrypto…用测试向量验证"；§1"表外的一律不准引入"。
- **具体例子**：
  - DES_ENCRYPT/DES_DECRYPT 用 `EVP_BytesToKey(EVP_des_ede3_cbc(), EVP_md5(), …)`：`src/sql/engine/expr/ob_expr_encrypt.cpp:207`、:257、:406、:498；
  - ENCRYPT() 用 `DES_fcrypt`，也就是传统 crypt(3)，盐值会扰动 E 盒：:627、:712；
  - RANDOM_BYTES 用 `RAND_bytes`：`ob_expr_random_bytes.cpp:66`、:95。
- **按规则的译法**：`des` crate 不提供盐值扰动的扩展置换，白名单里也没有 KDF 和随机源，翻译者只能手写 DES-crypt 和 KDF，或者退回到 `ObRandom`。
- **用户能看到的差异**：ENCRYPT()、DES_ENCRYPT() 的输出字节可能不同；RANDOM_BYTES 变成可预测。
- **裁判或用户在哪里看到**：裁判没有覆盖；用户可见。
- **建议改法**：在白名单里点名 crypt(3) 的实现（或者允许 `pwhash::unix_crypt`）、EVP_BytesToKey 的手写版（基于 md-5）和一个 CSPRNG（如 `getrandom`），并用基线二进制生成测试向量。

### F12【低】数值文本解析：fast_float 换成 std、strtod"溢出时夹到边界值"都与 C 语义不符

- **规则**：§0.14"xxHash、fast_float | xxhash-rust、std，各用金标准测试证明等价"；§2.5"`ob_strtol`/`ob_strtod` 跳过前导空白、允许只解析一部分、溢出时夹到边界值"。
- **具体例子**：
  - fast_float：
    - `src/share/object/ob_array_cast.cpp:705-713` 解析维度和键，区分 `invalid_argument` → `OB_INVALID_ARGUMENT`、`result_out_of_range` → `OB_DATA_OUT_OF_RANGE`、`ptr != end` → 非法；
    - :989-1003 解析向量元素，用 `parse_ret.ptr` 做前缀解析，任何 `ec` 都按非法处理。fast_float 6.x 对上溢和下溢都置 `result_out_of_range`（`deps/.../fast_float/parse_number.h:257-260`）。
  - libc 的 `strtod`：
    - `src/sql/hybrid_search/ob_query_parse.cpp:1984`（`field^weight`）接受前缀、`0x1p1`、`inf`、`nan`，NaN 能通过 `weight < 0` 的检查；
    - :2207 要求整串消费，接受十六进制浮点；
    - `ob_expr_st_buffer.cpp:597` 用 `atof`。
- **按规则的译法**：`str::parse::<f64>` 对 "1e400"、"1e-400" 返回 `Ok(inf)`、`Ok(0.0)`，不支持前缀解析，不认十六进制；按 §2.5，strtod 上溢会被"夹"成 `DBL_MAX`，而 C 返回 ±HUGE_VAL。
- **用户能看到的差异**：`CAST('[1e400]' AS VECTOR(1))` 在基线报 Invalid argument，Rust 接受；混合检索的 `"title^0x10"` 在基线权重是 16，在 Rust 是 0 或报错。
- **裁判或用户在哪里看到**：裁判没有覆盖（向量字面量里没有越界值）；用户可见。
- **建议改法**：§2.5 拆成两条：`ob_strtod`（MySQL 的 dtoa，溢出返回 DBL_MAX 并报错）和 `c_strtod`（C 语义：上溢 ±inf，接受 hex、inf、nan，返回结束位置）；fast_float 移植它的 `from_chars` 语义，即前缀解析加 `errc`，不能直接用 `str::parse`。

### F13【低】`char → u8` 忽略了基准平台 macOS arm64 的 char 是有符号的

- **规则**：§2.12"作字节数据用的 `char` → `u8`（C 的 `char` 在 x86 上有符号，在 aarch64 Linux 上无符号）"。
- **具体例子**：
  - `fnv_hash2`（`src/oblib/lib/hash_func/murmur_hash.cpp:51-57`，`const char *data; hash = (hash ^ data[i]) * p;`）对 0x80 以上的字节做符号扩展。
  - 它用来计算查询块名的源哈希（`ob_sql_hint.cpp:89`、`ob_transform_rule.cpp:81`、:95），生成的名字是 `SEL$%08X`（`ob_sql_hint.cpp:382-389`）。
- **按规则的译法**：`data[i] as i32`，其中 `data: &[u8]`，变成零扩展。
- **用户能看到的差异**：用户的 QB_NAME 含非 ASCII 字符时，EXPLAIN 和 outline 里生成的查询块名不同；照基线 outline 写的 hint 在 Rust 上匹配不到，计划随之改变。
- **裁判或用户在哪里看到**：裁判没有覆盖；用户可见。
- **建议改法**：§2.12 改成"按基准平台（macOS arm64，char 有符号）的语义翻译。char 只当数据搬运时用 u8；凡是提升成 int 参与运算或比较的，先转 `i8` 再扩展"，并列出 `fnv_hash2` 作为例子。

### F14【低】§2.3 规定拼出 `-nan`，基准平台的 libc 从不这样拼

- **规则**：§2.3"inf/nan 按 C 的拼写（`inf`、`nan`、`-nan`）"。
- **具体例子**：用基线工具链实测，`snprintf("%f|%e|%g|%.18le", -NAN)` 输出 `nan|nan|nan|nan`。`-nan` 是 glibc 的拼写。会受影响的有 12 条带 `%f` 的用户消息，以及 `TO_STRING` 里的 `%.18le`。
- **用户能看到的差异**：消息里出现 `-nan`，而基线是 `nan`。
- **裁判或用户在哪里看到**：用户可见的报错文本。
- **建议改法**：`ob_printf!` 按目标平台的 libc 拼写（macOS 一律 `nan`，Linux 按 glibc），用 cfg 区分，金标准测试覆盖。

### F15【低】§2.1 的"错误恢复规则"会悄悄修掉 `alloc(0) → NULL → 4013`，与 BUG 规则冲突

- **规则**：§2.1"分配失败和结构性错误…不得共用一个构造：分配失败一律是 `OB_ALLOCATE_MEMORY_FAILED`；结构性错误按原函数的错误码"；§2.4 把 arena 分配映射成 `arena.try_alloc`/`try_str`。
- **具体例子**：
  - `PageArena` 对 0 字节分配返回 NULL（`src/oblib/lib/allocator/page_arena.h:223-226`、:559-588）。惯用写法 `if (OB_ISNULL(buf = alloc.alloc(len))) ret = OB_ALLOCATE_MEMORY_FAILED;` 在 len 为 0 时报 4013。
  - 代码里大量先判空串，正是这个原因：`ob_datum_cast.cpp:1453`、`ob_expr_column_conv.cpp:905`、`ob_expr_type_to_str.cpp:101`、`ob_pl_expr_subquery.cpp:61`。
  - bumpalo 对 0 字节返回成功。
- **用户能看到的差异**：任何没加守卫的零长度分配点，C++ 报 4013，Rust 成功。
- **裁判或用户在哪里看到**：本次没有找到裁判路径上没加守卫的点；属于规则冲突。
- **建议改法**：arena 外观的原始字节分配接口复刻 `alloc(0)` 返回失败；§2.1 注明"不共用哨兵值"只用于新写的代码，逐行翻译时服从 §0.1.4。

### F16【低】§2.3 对错误包规则的摘要漏了 PL SIGNAL 和 `errmsg` 两条分支

- **规则**：§2.3"只有警告缓冲区里记录的错误码等于返回的错误码，才用缓冲区里的文本，否则用默认文本（`obmp_packet_sender.cpp:536-570`）。这条规则原样保留"。
- **具体例子**：
  - `src/observer/mysql/obmp_packet_sender.cpp:551-566`：遇到 `OB_ERR_SIGNAL_EXCEPTION`（PL 的 SIGNAL/RESIGNAL）时，不论错误码是否相等都用缓冲区里的文本；文本为空时先用调用方传入的 `errmsg`。
  - :644-653：SIGNAL 时 errno 和 SQLSTATE 也取自警告缓冲区。
- **用户能看到的差异**：如果照摘要实现，`SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT=…` 返回的错误码、SQLSTATE 和文本都会变成默认值。
- **裁判或用户在哪里看到**：裁判没有 SIGNAL 用例；用户可见。
- **建议改法**：把这三条分支写进 §2.3，或者删掉摘要，只写"`send_error_packet` 逐行翻译"。

### F17【低】`operator[]` 越界改为 abort，这个偏差没有用基线日志核实

- **规则**：§2.5"`arr[i]`：同前（C++ 的 `operator[]` 越界只记日志，属于未定义行为）"，即 abort；附录 A.2"`operator[]` 越界直接 abort"。
- **具体例子**：
  - 基线的 `operator[]` 越界时只打一条 `LIB_LOG ERROR "idx out of range"`，然后照常返回（`src/oblib/lib/container/ob_array.h:323-334`）。
  - 00b 的两次原版运行没有保留 observer.log（`judge/validation/results/orig-*` 下只有 json），无法证明裁判路径不会触发。
  - 典型写法是 `ob_bucket_lock.cpp:365` 的 `ob_sort(&latch_array_[0], &latch_array_[0] + count)`，数组为空时就是越界。这里调用方保证了非空（`ob_ls_tablet_service.cpp:296-297`），但同样的写法出现在别处、又遇到空数组时，C++ 记一条日志继续执行，Rust 进程直接退出。
- **用户能看到的差异**：语句正常结束变成实例崩溃，后续所有连接断开。
- **建议改法**：接受这个偏差之前，先用原版跑一遍裁判，grep 日志里的 "idx out of range"。命中的点改成不 abort 的写法，例如空范围返回空切片，并记入缺口清单。

### F18【低】11049 的计量口径和 C++ 不同，报错文本里的字节数也会不同

- **规则**：§0.7"单查询上限：错误 11049，原来有 9 个检查点，计入 arena 的占用和算子报告的用量"。
- **具体例子**：C++ 比较的是请求 MemoryContext 整棵树的 hold（`src/sql/executor/ob_memory_tracker.cpp:30-56`），其中包括 563 处 `ModulePageAllocator, true>` 惰性绑定到 `CURRENT_CONTEXT` 的数组（`ob_array.h:484-497`）。Rust 版漏计了放在全局堆上的查询期 `Vec`/`SmallVec`，又把 bumpalo 按倍数增长时块尾的空闲算了进去。
- **用户能看到的差异**：同一条查询在两边触发 11049 的临界点不同；消息里的 `mem_limit`、`mem_hold` 数字不同。
- **裁判或用户在哪里看到**：裁判没有 11049 期望；用户可见。
- **建议改法**：规定查询期容器也计入 `MemBudget`（由 `MemAttrGuard` 归属），或者明确接受为偏差、列进附录 A，并说明报错文本里的数字不比对。

## 3. 缺失的规则

### M1【中】预处理条件怎么译、对齐用哪套构建配置，文档都没有规定

- **现状**：
  - 基线的预处理配置取自 `build_release/compile_commands.json`：`-DNDEBUG -DOB_BUILD_SYS_VEC_IDX -D_NO_EXCEPTION`，没有 `ENABLE_DEBUG_LOG`，`RelWithDebInfo -O2`，目标是 macOS arm64。
  - 代码里有 215 处 `#ifdef/ifndef NDEBUG|ENABLE_DEBUG_LOG|ENABLE_SANITY`，149 处 `__APPLE__`，以及按 `__aarch64__` 分出来的实现。例如：
    - `ob_operator.cpp:732-734` 只在 debug 构建里 `OB_ASSERT`，:925-930 只在 debug 构建里做 decint 长度检查；
    - `ob_vector_l2_distance.cpp:37-41`：aarch64 走 NEON，float 分 4 个 lane 累加；
    - `ob_signal_handle.cpp:25-37`（见 F4）；
    - `ob_px_sub_coord.cpp:386-391`：macOS 与 Linux 的 shuffle 不同；
    - `ob_ls.cpp:735`、:839 的 `OB_BUILD_SYS_VEC_IDX`。
  - Rust 侧，CMake 的 Debug 构建链接的是 `cmake-debug` profile，它继承 dev，即 opt-level 0 并打开 debug-assertions（`rust/Cargo.toml`）。文档只规定了"参与对齐的 profile 关闭溢出检查"。
- **用户能看到的差异**：
  - 无条件翻译 debug 块，release 版会多出 abort 或报错；
  - 只译 Linux 分支，macOS 上向量距离的数值会变（`test_suite/vector_index/r/mysql/vector_similarity.result` 有 94 个全精度值，`cases/vector_index.vector_calc_utf8_mysql.result` 有 19 个）；
  - 用 opt-level 0 跑裁判，可能撞上单条语句 10 秒的超时（`judge/README.md` 的"环境修复"一节写明有这个超时）。
- **建议补充的规则**：
  - `NDEBUG` → `#[cfg(debug_assertions)]`，`ENABLE_DEBUG_LOG`/`ENABLE_SANITY`/`OB_BUILD_*` → 同名 cargo feature，平台和架构 → `cfg(target_os/target_arch)`；每个分支都要译，不能只译生产平台。
  - 对齐基准写成"macOS arm64、release、debug-assertions 关闭、`OB_BUILD_SYS_VEC_IDX` 打开"。
  - 给 `PERF(port)` 设一条性能下限：裁判里单条语句的耗时不超过 C++ 的 N 倍，否则视为对齐失败，因为语句超时、事务超时、后台定时任务都是用户可见的。

### M2【低】浮点转窄整型的语义

- **现状**：§2.12 只讲了整数之间的对应关系。基线 arm64 上 `static_cast<uint8_t/int8_t/int16_t>(double)` 越界时，取的是"32 位饱和转换后的低位"：实测 300.0 → 44，-5.0 → 251（u8），1e10 → -1（i8）。Rust 的 `as` 按目标宽度饱和：分别是 255、0、127。
- **例子**：`src/share/object/ob_array_cast.cpp:452-462` 的 `ADD_SIGNED/UNSIGNED_VECTOR_OBJ`（`string_cast_vector` 写整型元素时使用）。本次只确认到内部的 UTINYINT 向量元素路径；用户能定义的 `VECTOR(n)` 是 float。
- **建议补充的规则**：`static_cast<窄整型>(浮点)` 统一译成 `oblib::num::c_fptosi_i32(x) as i8`/`as u8` 这样的辅助函数，按基准平台的指令语义实现。

### M3【低】以浮点数为键的 `std::map`

- **现状**：§2.5 规定 `std::map → BTreeMap`，但 f64 没有 `Ord`。MAP 函数用 `std::map<double/float, uint32_t>` 去重和排序键（`src/sql/engine/expr/ob_expr_map.cpp:235-252`、:297-305）：C++ 的 `<` 认为 −0.0 和 +0.0 等价，NaN 与任何值都不可比较；翻译者自然会用 `total_cmp`，那样 −0.0 和 +0.0 就成了两个键。
- **用户能看到的差异**：`MAP(CAST('-0' AS DOUBLE),'a', 0e0,'b')` 的元素个数和取值不同。
- **建议补充的规则**：浮点键统一用一个复刻 C++ `operator<` 等价类的包装类型（−0.0 等于 +0.0，NaN 的处理照基线实测），禁止用 `total_cmp`。

### M4【低】整数除法和取余会 panic，与 overflow-checks 无关

- **现状**：§0.3 和 §2.12 只关闭了溢出检查，但 Rust 的 `/`、`%` 在除数为 0 或 `MIN / -1` 时始终 panic，而 `panic = "abort"` 下就是进程退出。arm64 上的 C++ 遇到同样情况不会陷入异常：`sdiv` 除以 0 得 0，再用 `msub` 求余，余数等于被除数；`MIN / -1` 得 MIN，余数为 0。基线已知的危险点都显式加了守卫（`ob_expr_mod.cpp:110-113`、`ob_expr_int_div.cpp:104-109`），本次没有找到裁判路径上没加守卫的点。
- **建议补充的规则**：翻译时，没有守卫的整数除法和取余一律用 `oblib::num::c_div/c_rem`，按上面的 arm64 结果实现，不得 panic；审查清单加一条"新出现的 `/`、`%` 有没有守卫"。

### M5【低】C 数学库的映射

- **现状**：文档没有给出 `rint/round/pow/fmod/frexp` 等函数的译法。`ObExprUtil::round_double` 用的是 `std::pow(10.0, …)` 和 `rint`（`src/sql/engine/expr/ob_expr_util.cpp:392-405`），`trunc_double` 同理（:432-450）。
  - `rint` 译成 `f64::round` 会把"四舍六入五成双"变成"远离零"：`ROUND(2.5e0)` 基线是 2，`f64::round` 是 3；
  - `pow` 译成 `powi`，会变成连乘，大指数时结果和 libm 不同。
- **建议补充的规则**：给出一张一对一的映射表：`rint`/`nearbyint` → `round_ties_even`，`pow` → `powf`（禁用 `powi`），`fmod` → `%`，`frexp`/`ldexp`/`modf` → `libc` 同名函数；并说明 Rust 的这些方法在 macOS 上调用的是系统 libm。

### M6【低】保留下来的库，版本和编译方式没有钉住

- **现状**：
  - "zlib 用钉住版本的 `libz-sys`"并不能保证用的是 zlib 1.2.13：libz-sys 内置的 zlib 源码随它自己的版本变化，在 macOS 上默认动态链接系统 zlib（1.2.12）。§0.10 承诺"`COMPRESS()` 的输出字节保持不变"。
  - libxml2 在基准平台 macOS 上是 2.10.4，el8 上是 2.13.6（parser 备忘 §2.5），两个版本接受哪些输入、恢复模式怎么工作都不同，文档没有写用哪个。
  - shim 和 -sys 由 build.rs 里的 `cc`/`cxx` 编译，编译器和参数也没有规定，与 F1 相关。
  - Boost.Spirit 的 SRS 解析器改为手写，只"用全部默认 SRS 定义逐位比对"；用户自定义的 SRS 用的仍是另一套数值解析算法（qi 的 `double_` 不保证正确舍入）。
- **建议补充的规则**：
  - 每个保留下来的库写明版本，并在启动时断言，例如 `zlibVersion()=="1.2.13"`；libz-sys 必须开 `static` 特性。
  - shim 的编译器和参数与基线一致（clang 17.0.6、`-O2 -march=armv8-a+crc+lse`，FP contraction 按 F1）。
  - SRS 的数值解析复刻 `qi::real_parser` 的算法。

### M7【低】libc 随机数和 shuffle

- **现状**：§1 只禁止了 `rand` crate，并要求移植 `ObRandom`；可 C++ 里还直接用 `srand(time(0))`/`rand()`（`src/sql/das/iter/ob_das_spiv_merge_iter.cpp:511-512`、:538-539，稀疏向量 top-k 的 quickselect 选主元），以及 `std::shuffle(..., mt19937(random_device))`/`std::random_shuffle`（`ob_px_sub_coord.cpp:386-391`）。这些结果本来就是随机的，对齐不受影响，但没有指定译法，翻译者会各自发明。
- **建议补充的规则**：统一译成 `ObRandom`，以时间作种子；注释写明"原本就不确定"，以免审查者误判成对齐问题。
