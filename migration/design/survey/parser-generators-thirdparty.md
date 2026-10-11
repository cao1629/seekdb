# 调查备忘：解析器、代码生成器与第三方库

> 设计文档主题调查，只读。基线 `7d907abfa`：`git diff --stat 7d907abfa HEAD -- src deps cmake CMakeLists.txt rust unittest` 为空，工作区就是基线。日期 2026-10-11。
> 证据来源：git 跟踪的源码；本机 00b 阶段 release 构建留下的忽略文件（`src/sql/parser/*.output`、`*_tab.c`、`build_release/` 下的生成文件和 `.o`）。这些文件由基线源码生成，只读取，不重新构建。
> 脚本在 `/tmp/seekdb-survey-parser-3p-k7q2/`，按规则不入库，方法写在 §2.6。估算一律标「估」。Rust crate 的外部事实单独列在 §2.6 末尾，附来源。

## 1. 结论

1. **解析器**：迁移期保留 bison/flex 生成的 C 解析器（SQL、PL、全文检索三套），通过一层窄 FFI 接入：server 调它 23 个函数，它回调 server 19 个函数。Rust 侧只看到一个 `ParseNode<'p>` 句柄视图，205 个消费文件可以按固定规则机械翻译。只有这样，1064 报错里的 near 文本和行号、select 列名、plan cache 参数化才能做到逐字节不变。迁完以后，可以在同一视图后面换成 bison 3.8 + Rust skeleton 生成的解析器，以语法树差分作为验收关口。
2. **第三方库，按"行为就是库本身"来分**：
   - 保留原生库，用 cxx 或离线 bindgen 包一层薄封装：Boost.Geometry、ICU 正则、libxml2、s2、vsag、SQLite、打过补丁的 zstd 1.3.8。
   - 能证明等价的换成纯 Rust：OpenSSL → RustCrypto，gRPC/protobuf/protobuf-c → tonic/prost，CRoaring → roaring，xxHash，fast_float。
   - RapidJSON（打过补丁，报错文本对用户可见）：把它的 Reader 移植到 Rust。
3. **生成器**：errno、系统变量、proto、版本号改由 build.rs 生成，沿用 `rust/config` 的先例。内部表保留 Python 前端，加一个 Rust 后端，输出数据表而不是代码。4 个只为 C++ 服务的生成器（两个拆分编译单元的、`gen_type_name.sh`、syspack）直接删除，改用宏、派生和 `include_str!`。
4. **代价**：手写 unsafe 估计 200–350 处，集中在约 10 个 -sys、shim 和视图 crate 里。构建机仍需要 C/C++ 工具链、bison 2.4.1/flex 2.5.35，以及 Boost、s2、vsag 的头文件和库。

## 2. 事实

### 2.1 三套语法

| | SQL | PL | 全文检索布尔模式 |
|---|---|---|---|
| 源文件 | `sql_parser_mysql_mode.y` 18,723 行 + `.l` 1,853 行 | `pl_parser_mysql_mode.y` 2,976 行 + `.l` 698 行 | `ftsparser.y` 250 行 + `ftsblex.l` 85 行 |
| 规则 / 终结符 / 非终结符 / 状态（`YYNRULES`/`YYNTOKENS`/`YYNNTS`/`YYNSTATES`） | 3,605 / 1,186 / 756 / 6,550（`yytable` 长 `YYLAST`=191,936） | 510 / 214 / 156 / 784 | 18 / 16 / 7 / 24 |
| 动作数 / 动作 C 代码行 | 2,675 / 11,674（约占规则段的 2/3） | 421 / 1,695 | 17 / 168 |
| `@N` 位置引用 / `YYERROR`+`YYABORT` | 527 / 88 | 166 / 35 | 0 / 6 |
| 优先级 | 51 条 `%left/%right/%nonassoc`，122 处 `%prec` | 15 处 `%prec` | 1 条 `%nonassoc` |
| 生成的 C | `_tab.c` 81,297 行 + `_lex.c` 75,028 行 | 8,409 + 3,902 行 | 1,916 + 2,344 行 |

**生成方式**：
- 配置阶段用 `execute_process` 调 `gen_parser.sh` 生成（`src/sql/CMakeLists.txt:19-35`，`src/pl/CMakeLists.txt:1-20`），产物不入库（`unresolved.tsv` 里 4 条引用）。
- 脚本强制要求 bison 2.4.1（`gen_parser.sh:29-35`），输出里一出现 conflict 就中止构建（:62-76）。
- flex 用 `-Cfa -B -8`，生成后还要用 sed 修补词法器（:82-96）。
- Windows 用 win_bison，参数是 `-Wno-deprecated -Wno-other`（`gen_parser_win.ps1:25`）。这两个类别 bison 3.0 才有，所以这套语法**已经在两个 bison 大版本下都能生成**（推断）。

**词法器**（`.l`）：
- `%option reentrant bison-bridge bison-locations case-insensitive`（:19-23），9 个互斥起始条件（:35-44）。
- 4 种 sql_mode 会改变出什么 token：ANSI_QUOTES、HIGH_NOT_PRECEDENCE、NO_BACKSLASH_ESCAPES、PIPES_AS_CONCAT。
- 行号在动作里手工累加（:379、:528、:1407、:1785）。列号是从 1 开始的字节偏移，由 `YY_USER_ACTION` 维护（`sql_parser_base.h:491-499`）。
- 关键字是一张 991 条的 trie 表（`non_reserved_keywords_mysql_mode.c`）。
- 语法动作不回写任何影响词法的状态（grep 无命中），所以词法器只取决于输入、sql_mode 和解析前设置的标志。**这意味着 token 流可以单独做差分测试。**

**同一个词法器有四个入口**：
- `obsql_mysql_yyparse`：完整解析；
- `obsql_mysql_fast_parse`：参数化；
- `obsql_mysql_multi_fast_parse`：多语句切分；
- `obsql_mysql_multi_values_parse`：拆多行 VALUES。

后三个是 `.y` 尾部手写的 token 循环（:18458-18723），在 `parse_sql` 里分派（`sql_parser_base.c:124-132`）。

### 2.2 错误怎么产生，怎么到达用户

**setjmp/longjmp**：
- 只在两处 setjmp：`parse_sql`（`sql_parser_base.c:104-106`，`static __thread jmp_buf`）和 `obpl_parser_parse`（`pl_parser_base.c:48`）。
- 只在两处 longjmp：`obsql_mysql_parser_fatal_error`（`sql_parser_mysql_mode.l:1840-1852`）和 PL 的对应函数（`pl_parser_mysql_mode.l:672`）。
- 触发条件只有词法器里的 `YY_FATAL_ERROR`/`YY_UNEXPECTED_ERROR`（`.l:31-32`），也就是内存不足或内部错误。语法动作走的是 `YYABORT_WITH_ERROR`/`YYERROR`（`sql_parser_base.h:79-106`）。
- 因此跳转只越过 C 栈帧：回调先返回 NULL，然后才 longjmp。

**bison 错误恢复**：只有一条 `hint_option: error`（`.y:9193-9199`），作用是悄悄丢掉格式错误的 hint。

**用户看到什么**：
- `yyerror` 记下出错 token 的 `first_column`（`.y:18438-18453`）。
- `ObParser::parse_sql` 发出 `LOG_USER_ERROR(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX), len, stmt+start_col-1, line_+1)`（`ob_parser.cpp:883-896`；prepare 路径 :1123-1133，PL 路径 `ob_pl_parser.cpp:113-152`）。
- 用户收到的是 **1064 / 42000**，文本为 "You have an error in your SQL syntax; check the manual that corresponds to your OceanBase version for the right syntax to use near '<从出错 token 到语句末尾>' at line N"（`ob_errno.def:478,481`）。
  - near 的长度按连接字符集截成合法字符串。
  - near 文本完全取决于 LALR 自动机**在哪个 token 上失败**。

**其他用户可见的错误路径**：
- `parse_define.h:26-43` 的 17 个 `OB_PARSER_ERR_*` 原样作为 OB 错误码返回。
- PL 的部分错误把语法动作里写的文本直接塞进 `OB_NOT_SUPPORTED` 的报错（`ob_pl_parser.cpp:283`）。
- 全文检索解析失败一律映射成 `OB_ERR_PARSER_SYNTAX`，不带位置（`ob_das_legacy_tr_merge_iter.cpp:1081-1089`）。

**位置也对用户可见**：select 项的列名就是 `@N` 切出来的原文（`dup_expr_string`/`dup_string`，`sql_parser_base.h:153-185`）。

### 2.3 ParseNode：解析器和其余代码之间的契约

**结构**（`src/query/api/query/parser/parse_node.h:129-195`）：
- C 结构：`ObItemType type_`、19 个标志位的位域 union、`value_` union、`str_value_/str_len_`（不保证以 0 结尾）、`raw_text_`、`children_` 指针数组、位置信息。SQL、PL 共用。
- 节点由回调 `parse_malloc` 分配在语句的内存池里（`ob_sql.cpp:829-832`），活到语句结束。

**ObItemType**：约 2,020 个枚举值（`type_name.c` 生成 2,023 个 case），有两份几乎相同的副本（`query/api/query/parser/ob_item_type.h` 2,477 行，`objit/include/objit/common/ob_item_type.h` 2,486 行，diff 31 行，基本是注释）。SQL 语法构造了 734 种不同的 `T_*` 节点。

**消费方**：
- 解析器目录以外有 **205 个文件**提到 ParseNode，其中 166 个在 `sql/resolver`。共 3,518 处 `children_[` 按下标取子节点，1,113 处 `num_child_`。子节点的位置就是隐式的 schema。
- **解析后还会就地改写**：
  - plan cache 参数化把常量节点改成 `T_QUESTIONMARK`（`ob_sql_parameterization.cpp:584-586`）；
  - resolver 在栈上直接造节点（`ob_raw_expr_resolver_impl.cpp:54-55`）。
  - 启发式计数：53 个文件共 698 行写字段（上限）；6 个文件 27 处调用节点构造或复制函数；24 个文件 39 处 `const_cast<ParseNode`。

**PL 嵌套 SQL**：PL 的语法动作会接管 bison 的 lookahead（`yychar/yylval/yylloc`，通过 `YYLookaheadToken`，`pl_parser_mysql_mode.y:162-197`），自己读 token 直到终止符，再用 SQL 解析器重新解析这段子串（:2856-2953）。表达式的解析方式是先加 "SELECT " 前缀，再取 `children_[2]->children_[0]->children_[0]`（:186-195）。

**FFI 面**（对本机 `.o` 跑 `nm`，见 §2.6 [C5]）：
- C 解析器从外部引入 **19 个**非 libc 符号：
  - `parse_malloc/realloc/free/strdup/strndup`、`parser_alloc`、`malloc_parentheses_info`、`strndup_with_prefix`；
  - `parse_str_convert_utf8`、`replace_invalid_character`、`check_real_escape`；
  - `murmurhash`、`ob_strntoll/ull`；
  - `lookup_pl_symbol`（实现在 pl，属于向上调用）；
  - `check_stack_overflow_c`、`obpl_parser_check_stack_overflow`、`ob_backtrace_c`、`parray_c`。
- server 其余部分从它引入 **23 个**：
  - 入口：`parse_init/parse_sql`、`obpl_parser_init/parse`、`fts_parse_docment`；
  - 节点操作：`new_node` 等 3 个构造函数、`append_child`、`push_back_child`、`merge_tree`、`deep_copy_parse_node(_base)`、`get_deep_copy_size`、`parsenode_equal`；
  - 其他：`get_type_name`、`mysql_sql_reserved_keyword_lookup`、`ob_parse_binary/bit_string`（各带 `_len`）、`str_tolower/toupper`。
- 和字符集只有 5 处直接耦合：解析 `ObCharsetInfo` 字段，并通过 `cset->well_formed_len/ismbchar/numchars` 函数指针调用（`sql_parser_base.h:776-806`）。

**解析器目录的构成**：语法文件和手写 C 共 27,810 行（`.y/.l` 24,585 行，`.c` 3,225 行），加 3,489 行头文件。C++ 4,880 行（`ob_fast_parser.cpp` 2,527、`ob_parser.cpp` 1,275 等），这部分是普通翻译单元。临时 manifest 里有 30 个解析器单元。

### 2.4 生成器及其产物

| 生成器 | 输入 | 当前产物 | 改成输出 Rust 需要什么 |
|---|---|---|---|
| `src/share/gen_errno.pl`（467 行） | `ob_errno.def`：1,548 条 `DEFINE_*`（858+172+326+190+2） | `share/ob_errno.h` 2,792 + `lib/ob_errno.h` 396 + `ob_errno.cpp` 15,636 行，已入库 | 解析 .def，生成错误码和静态表（名称、cause、solution、MySQL errno、SQLSTATE、两种文本），以及用户消息的格式化。格式串只用到 12 种 printf 规格：`%.*s` 357、`%s` 204、`%ld` 37、`%u` 33、`%d` 25、`%.Ns/%-.Ns` 38、`%f` 12、`%lu` 7、`%c` 4、`%lf` 1、`%%` 3。调用点是 2,821 处 `LOG_USER_ERROR` 和 128 处 `LOG_USER_WARN` |
| `system_variable/gen_ob_sys_variables.py`（1,200 行） | JSON，776 个系统变量，其中 94 个钩子写的是 C++ 函数名（如 `"ObSysVarOnCheckFuncs::check_and_convert_charset_not_null"`，`ob_system_variable_init.json:53`） | 8 个文件 29,946 行，已入库 | 一个新的发射器；钩子名按命名规则映射到 Rust 路径 |
| `inner_table/generate_inner_table_schema.py`（3,417 行）+ `ob_inner_table_schema_def.py`（Python DSL，14,510 行） | 384 张表、54 个系统索引、143 个视图（视图 SQL 启动时交给解析器） | 配置阶段生成 54 个文件 **104,889 行**（`build_release/generated/`），不入库 | 保留 Python DSL，新增 Rust 发射器；输出静态列描述数组，也就是数据而不是代码 |
| `codec/generate_scalar_bitpakcing.py` + `generate_simd_bitpakcing.py`（384+782 行） | 位宽参数 | 24,406 行，已入库。SSE2 intrinsics：`_mm_loadu_si128` 1,898 处等；ARM 走 `ob_sse_to_neon.h` | 生成 `core::arch` 代码（unsafe）或 const 泛型函数；必须保持落盘布局 |
| `gen_str_datum_func_parts.py`、`gen_expr_str_cmp_func.py` | 排序规则列表 | 14 个小文件 | **删除**：只是把模板实例化拆到多个编译单元以便并行编译；Rust 用宏或泛型 |
| protoc + grpc_cpp_plugin | `oblib/grpc/standbyservice.proto` 135 行，10 个 RPC（6 个服务端流） | 4 个文件 12,345 行，已入库；Windows 在配置阶段重新生成（`grpc/CMakeLists.txt:5-45`） | build.rs 里用 prost-build + tonic-build（protoc 或纯 Rust 的 protox） |
| protoc-c | `share/geo/ob_vector_tile.proto` 79 行 | 651 行，已入库 | prost-build |
| `sql/parser/gen_type_name.sh` | `ob_item_type.h` | `type_name.c`，构建时生成 | **删除**：用枚举派生名字表，顺便合并两份 ObItemType |
| `configure_file(ob_version.cpp.in)` | 5 个 CMake 变量（`ob_version.cpp.in:37-49`） | `ob_version.cpp` | build.rs `cargo:rustc-env`，先例是 `DEFAULT_LOG_LEVEL`（`cmake/Rust.cmake:106`） |
| `sys_package/syspack_codegen.py`（176 行） | 13 个包，26 个 .sql | `syspack_source.cpp` 1,690 行，构建时生成 | **删除**：改用 `include_str!` |
| bison/flex | 6 个语法和词法文件 | 172,896 行 C，配置阶段生成 | 见 §3.1 |
| `ob_uctype.h` | UnicodeData（生成器不在仓库里） | 已入库 | 按 depmap 关口定的"数据表机械转换" |

`reserved.py`（2,137 行）是开发用的关键字对照工具，不参与构建。

**先例**：`rust/config/build.rs:369-419` 读 YAML，同时生成 Rust、cxx 桥（`config_bridge.h`，被 251 处 C++ include）和 cbindgen 的 C 头文件。"一个源头同时生成两种语言"的做法已经在用。

### 2.5 第三方库

server 链接的库来自 `deps/init/oceanbase.el8.x86_64.deps:13-31`，各平台有 14 份 `.deps`。链接行在 `src/oblib/CMakeLists.txt:280-333`、`oblib/lib/CMakeLists.txt:43-144`、`oblib/grpc/CMakeLists.txt:53-119`。

"API 文件/行"按库的 API 正则统计，见 §2.6 [C6]。116 个文件（约占单元的 3%）直接调用第三方 API。

| 库（版本） | API 文件 / 行 | 用途 | 用户可见或落盘 | 推荐路径 | 换掉时的对齐风险 |
|---|---|---|---|---|---|
| Boost.Geometry 1.74（纯头文件） | 33 / 475，全部在 `src/share/geo`。之上还有 102 个 ST_* 表达式文件，共 13,171 行 | 主要算法调用次数：relate 63、buffer 33、covered_by 32、within 23、difference 15。地理策略：andoyer、geographic_winding、geographic_segments、area::geographic | GIS 结果。Boost 异常映射成用户错误码 `OB_ERR_BOOST_GEOMETRY_*`（`ob_geo_dispatcher.h:1313-1362`） | 保留，新写 cxx 薄 shim（§3.2） | 高 |
| Boost.Spirit/Phoenix | 1 / 59（`ob_srs_wkt_parser.cpp`） | 解析 SRS 定义的 WKT | 报错，以及解析出的 double（qi 不保证正确舍入） | 手写 Rust 解析器；用全部默认 SRS 定义逐位比对 | 中低 |
| ICU 69.1（符号带 `_69` 后缀，只用 stubdata） | 2 / 53（`ob_expr_regexp_context.cpp`） | REGEXP_*，只用 uregex | 结果；`u_errorName` 加行号和列号直接进报错（:575-617）；`regexp_time_limit/stack_limit`（:252-253） | 保留，在仓库里写约 20 个函数的绑定 | 高 |
| libxml2 2.13.6（el8）/ 2.10.4（macOS）+ liblzma | 2 / 67，用到内部 API（`parserInternals.h`、`ctxt->recovery`） | XML 函数的 SAX 解析 | 接受哪些输入、恢复模式；报错统一为 `OB_ERR_PARSER_SYNTAX`（`ob_libxml2_sax_handler.cpp:510-525`） | 保留，用 bindgen 生成 -sys | 中 |
| libcurl 8.12.1 | 6 / 118 | AI 函数、embedding、遥测；用 curl_multi | 远端 HTTP 的行为 | ureq/reqwest + rustls，或 `curl` crate | 低 |
| OpenSSL 1.1.1u | 20 / 157。它在 5,584 个文件里"可达"，是因为 libeasy 残留的 `easy_io_struct.h` include 了它；去掉这个头文件，只剩 20 个 | AES-128/192/256 的 ecb/cbc/cfb1/cfb8/cfb128/ofb/gcm、SM4（6 种模式）、SM3、DES/3DES/`DES_fcrypt`、MD4/MD5/SHA1/SHA2、HMAC、`EVP_BytesToKey`、X509/PEM | SQL 函数的输出字节；OpenSSL 的错误文本只进日志（`ob_encryption_util.cpp:206`） | RustCrypto（aes、des、sm4、sm3、md4、md-5、sha1、sha2、hmac、cbc/ecb/cfb-mode/cfb8/ofb/ctr、aes-gcm）；cfb1 没有现成 crate，要手写约 30 行（估）；crypt 用 pwhash；证书用 x509-parser（工作区已有） | 低：都是标准算法，用测试向量验证 |
| zlib 1.2.13 | 6 / 33 | 存储压缩器、COMPRESS()/UNCOMPRESS()、LOAD DATA 读 gzip、SELECT INTO 压缩输出 | COMPRESS() 的输出字节 | 固定 1.2.13 的 libz-sys（静态）；协议层已经在用 flate2（zlib-rs/miniz） | 版本不变就是 0 |
| zstd 1.3.8，仓内 47 个文件，**OceanBase 改过** | 1 / 29（封装层） | 存储和 clog 压缩器 `'zstd_1.3.8'` | 落盘；API 被改过（`zstd_compress.c:3375`、`zstd_decompress.c:728` 多了 `int *zstd_version` 参数） | 原样放进一个 -sys crate，用 cc 编译 | 0 |
| gRPC 1.46.7 + protobuf C++（连带 abseil、c-ares、re2） | 9 / 166 | 物理备库 | 线上协议 | tonic + prost | 中低 |
| protobuf-c 1.4.1 | 2 / 19（`ob_geo_mvt.cpp`） | ST_AsMVT | 输出字节 | prost：两者都按 tag 顺序编码（pb-c 描述符按 tag 排序，`ob_vector_tile.pb-c.c:259-323`；Value 只含一个 oneof） | 低 |
| s2geometry 0.10.0（+abseil） | 4 / 127 | 空间索引的 cell 覆盖，含内覆盖 `get_inner_cover_cellids` | 落盘的 cell id；内覆盖会直接决定结果 | 保留，写 cxx shim | 高（纯 Rust 的 `s2` crate 是 Go 版的部分移植，完整度未核对） |
| CRoaring 3.0.0 | 5 / 146，只用内存中的 roaring64，不序列化，挂了内存钩子 | 向量索引过滤 | 否 | roaring crate（`RoaringTreemap`） | 0 |
| vsag 1.1.0（连带 diskann、openblas、antlr4、fmt、cpuinfo、gfortran、gomp/omp） | 5 / 78；适配层 C++ 1,674 行（`ob_vsag_adaptor.cpp`、`ob_vector_util.cpp`），接口用 `std::string`、iostream | 向量索引 | 结果；序列化后的索引 | 保留适配层，改成 cxx shim | 没有替代品 |
| SQLite 3.38.1 | 2 / 51 | 本地元数据（tablet meta、校验和等） | 落盘文件 | rusqlite，链接同一份库或用 bundled | 低 |
| RapidJSON（打过补丁的 relaxed 版 1.0.0） | 7 / 47；非上游 flag（`ob_json_parse.h:28-30`） | JSON 解析 | `GetParseError_En` 文本和出错偏移进报错（`ob_json_parse.cpp:120-126`）；double 走"普通精度"路径，未开 FullPrecision | 把 Reader 和它的 strtod 移植到 Rust（约 2K 行，估） | 移植：中；换 serde_json：高 |
| fast_float 6.1.3 | 2 / 4 | 解析 double | 结果 | Rust 标准库，两者都正确舍入 | 0 |
| libaio 0.3.112（仅 Linux） | 6 / 78 | 异步 IO | 否 | 5 个函数的 FFI 或直接 syscall | 低 |
| libunwind 1.6.2（仅 x86_64） | 1 / 14 | 崩溃栈 | 否 | backtrace crate | 0 |
| jemalloc 5.3.1 | 2 / 25 | 分配器 | 否 | 已经通过 `seekdb-jemalloc-sys` 引入（`deps/external/Cargo.toml:10`） | — |
| xxHash（仓内） | 3 处，其中 palf 日志头校验和（`log_entry_header.cpp:286`） | 校验和、哈希 | 落盘 | xxhash-rust | 0，算法本身确定 |
| `<regex>`/`<regex.h>` | 8 个文件；JSON schema 的 `pattern` 对用户可见（`ob_json_schema.cpp:4015`） | 校验、内部固定模式 | 结果，且现在 libstdc++ 和 libc++ 就不一致 | schema 用 regress（ECMAScript 引擎），内部固定模式用 regex | 中 |
| tzcode（仓内） | 不参与编译：`compile_commands.json` 和 unity 文件都没有 `localtime.c` | — | — | 删除 | — |
| libev 的 `ev.h` + libeasy | 20 个头文件，0 个 .c | 残留 | — | 删除 | — |

**谁在用 OB 的分配器钩子**：zstd `customMem`、zlib `zalloc`、OpenSSL `CRYPTO_set_mem_functions`（`ob_encryption_util.cpp`）、roaring `roaring_init_memory_hook`、vsag 的 allocator、RapidJSON 的 allocator、解析器的 `parse_malloc`。ICU、libxml2、curl、s2、Boost 直接用全局 malloc，最终落到 jemalloc。

**Boost 孤岛有多大**：35 个 include Boost 的文件共 16,134 行，它们的 include 闭包是 275 个文件、90,410 行，其中 oblib 175 个文件、64,739 行（[C7]）。整个 `share/geo` 直接 include 了 31 个外部头文件，包括 `ob_obj_cast.h`、lob、JSON bin、number。所以"整个 geo 目录留在 C++"做不到，孤岛只能切得更窄。

### 2.6 已有约定、方法与外部事实

**已有的 Rust/FFI 约定**：
- release 和 cmake-debug 两个 profile 都是 `panic = "abort"`（`rust/Cargo.toml:16,23`），thin LTO，`codegen-units = 1`。
- sql-nio 走 cbindgen 生成的 C ABI，有 186 处 unsafe（可行性报告 §1.3）；rustls 只启用 ring，以避开 cmake + bindgen（`rust/sql-nio/Cargo.toml:31-36`）。
- config 用 cxx（1.0.204）加 cbindgen。
- jemalloc 由 cargo registry 上的 -sys crate 构建。
- 工具链固定为 1.98.1，target 包括 linux、windows-gnu、android；macOS 和 Windows msvc 也要构建。所以选的每个 crate 都必须在这些平台上编得过。

**方法**（命令都在仓库根目录运行；Python 都用 `python3 -I`）：
- [C1] `bison_stats.py *.output`：统计 bison 2.4.1 报告里的 Grammar、Terminals、Nonterminals、state 各段。表格里的宏值是 `grep -E '^#define (YYLAST|YYNTOKENS|YYNNTS|YYNRULES|YYNSTATES)' *_tab.c` 的结果。
- [C2] `grammar_actions.py`：在 .y 的规则段按花括号深度区分语法行和动作行（跳过字符串和注释），再数 `$$`、`$N`、`@N`、`YYERROR|YYABORT`。优先级用 `grep -c '^%left\|^%right\|^%nonassoc'` 和 `grep -c '%prec'` 数。
- [C3] 词法器和错误路径：`grep -n 'longjmp\|setjmp\|jmp_buf'`、`grep -n '^%x\|^%option'`、`grep -c 'YY_FATAL_ERROR'`，范围是 git 跟踪的 src 文件。
- [C4] `parsenode_usage.py` 和 `parsenode_writes.py`：只看提到 ParseNode 的文件，排除三个 parser 目录，数 `children_\s*\[`、`num_child_`、字段赋值（`->field\s*=` 且后面不是 `=`）、构造函数调用和 `const_cast<ParseNode`。跳过以注释开头的行。
- [C5] 对 15 个解析器 C 目标文件跑 `nm -g`，用未定义符号减去它们自己定义的符号，得到 19 个外部依赖（去掉 libc）。对其余 350 个 `.o` 的未定义符号和解析器定义的符号取交集，得到 23 个。
- [C6] `api_sites.py`：每个库一条 API 正则，例如 Boost 用 `\bbg::|\bboost::geometry::`，OpenSSL 用 `\b(EVP_\w+|SSL_\w+|…)`。范围是 git 跟踪的 src C/C++ 文件，排除 `excluded.tsv`，跳过注释行和 `#include` 行。
- [C7] `island.py`：在 `migration/depmap/edges.tsv` 上从种子文件出发求 include 的前向闭包。
- [C8] `errno_formats.py`：在 `DEFINE_*` 行的格式串里用正则 `%[-+ #0]*(\*|\d+)?(\.(\*|\d+))?(hh|h|ll|l|L|z|j|t)?[diouxXeEfgGcsp%]` 数 printf 规格。这一步解析出 1,534 条，比 grep 少 14 条，因为有些行带行尾注释。
- 生成产物的行数：`find build_release/generated -type f | xargs wc -l`，以及对入库生成文件跑 `wc -l`。

**外部事实**（2026-10 查证）：
- rust-bison-skeleton 需要 Bison ≥ 3.7.3，支持 `error` 规则和自定义词法器，lib-ruby-parser 在用。它只有 25 个 star，需要 fork 进仓库维护。
- LALRPOP 的 `#[precedence]`/`#[assoc]` 只是"分层表达式"的简写，不是 bison 的 `%prec` 冲突消解。
- grmtools/lrpar 不支持 `error` token。
- re2c ≥ 3.0 有 Rust 后端（re2rust），支持条件、大小写不敏感和最长匹配。
- prost-derive 按 tag 排序后编码；例外是 oneof，放在它最小 tag 的位置。
- rust_icu 5.x 只支持 ICU 74–77，crate 列表里没有 uregex。

来源：github.com/iliabylich/rust-bison-skeleton、docs.rs/lib-ruby-parser、lalrpop.github.io/lalrpop/tutorial/004_full_expressions.html、softdevteam.github.io/grmtools/master/book/yacccompatibility.html、mankier.com/1/re2rust、github.com/tokio-rs/prost（`prost-derive/src/lib.rs`）、github.com/google/rust_icu。

## 3. 选项

### 3.1 SQL/PL 解析器

**A. 保留 C 解析器，Rust 侧用视图**

做法：
- `seekdb-parser-sys` 在 build.rs 里调用 deps/3rd 的 bison 2.4.1 和 flex 2.5.35（等价于 `gen_parser.sh`），再用 cc 编译 17 万行生成的 C（`.y/.l` 里的动作代码就在其中），以及 3,225 行手写 `.c`。
- ParseNode 和 ParseResult 的布局用 bindgen 离线生成并入库，加 `size_of`/`offset_of` 静态断言。
- 19 个回调由 Rust 以 `extern "C"` 导出。其中 `lookup_pl_symbol` 改成启动时由 pl 注册的函数指针，和 weak symbol 的处理方式相同。
- 5 处字符集耦合改成调用访问函数，这是对 C 代码的一个小补丁，作用是让字符集模块不必保留 MySQL `CHARSET_INFO` 的 ABI。

```rust
#[derive(Clone, Copy)]   // 句柄，!Send；生命周期 = 语句的解析 arena
pub struct ParseNode<'p> { raw: NonNull<sys::ParseNode>, _p: PhantomData<(&'p ParseArena, *const ())> }
impl<'p> ParseNode<'p> {
    pub fn kind(self) -> ItemType { ItemType::from_raw(unsafe { (*self.raw.as_ptr()).type_ }) }
    pub fn child(self, i: usize) -> Option<ParseNode<'p>> { /* 越界或 NULL 都返回 None，取代 children_[i] */ }
    pub fn str_bytes(self) -> &'p [u8] { /* str_value_ + str_len_ */ }
    pub fn is_neg(self) -> bool { /* 19 个标志位，每个一个访问器 */ }
    pub fn set_kind(self, k: ItemType) { /* plan cache 参数化的就地改写；只写标量字段，不交出 &mut */ }
}
#[no_mangle] pub extern "C" fn parse_malloc(n: usize, pool: *mut c_void) -> *mut c_void { /* 失败返回 null；规则：回调绝不重入解析器 */ }
```

**B. 用 Rust 解析器生成器重写（lalrpop / grmtools / pest）**：
- LALRPOP 没有 `%prec`。51 条优先级声明和 122 处 `%prec` 要靠改写语法来表达，等于重新定义语言。
- grmtools 没有 `error` token，词法器也不能带动作代码。
- pest 是 PEG，有序选择的语义和 LALR 不同。

**C. 手写递归下降**：类似 sqlparser-rs，需要重写 3,605 条规则、734 种节点形状和所有位置语义。

**D. bison 3.8 + Rust skeleton**

做法：
- 把 rust-bison-skeleton fork 进仓库，用同一份语法，把动作逐条机械译成 Rust：`malloc_non_terminal_node(...)` 变成 `b.non_terminal(T_X, &[..])?`。
- 词法器用 re2rust 的 `-c --case-insensitive` 改写，或者手写。
- PL 需要 skeleton 把 lookahead 暴露给动作。
- 节点放进 Rust arena，对外仍是 A 的 `ParseNode<'p>` 接口。

```
expr: expr '+' expr { $$ = Value::Node(b.non_terminal(T_OP_ADD, &[$1.node(), $3.node()])?); }
```

| 维度 | A 保留 C | B 生成器重写 | C 手写 | D bison 3.8 + Rust |
|---|---|---|---|---|
| unsafe | 约 60–100 处（估），全在视图和 -sys 里 | ~0 | ~0 | ~0–10（估） |
| 性能 | 不变 | 相当 | 不确定 | 相当：同样查表驱动，re2rust 生成 DFA |
| 对 3,754 个单元的机械性 | 205 个消费文件按 5 条规则改：`children_[i]`→`child(i)`、`type_`→`kind()`、`num_child_`→`num_child()`、`str_value_/str_len_`→`str_bytes()`、写字段→setter。30 个解析器单元里，8 个含 C 的标"保留 C"；9 个 C++ 单元和 13 个头文件单元正常翻译，其中 `parse_node.h` 变成布局镜像 | 消费者同 A；语法要重构 | 全部重写 | 消费者同 A；另外要移植 3,096 个动作（约 13.4K 行 C），以及 2.5K 行词法 |
| 对齐风险 | 解析本身**无**风险，是同一份代码；风险只在消费方的普通翻译 | 高：接受的语言、出错 token、`error` 恢复、位置 | 最高 | 低到中：同一个 LALR 自动机，出错 token 不变；风险在动作和词法的移植，可以差分验证 |
| 编译 | C 由 cc 一次编完并缓存 | 中 | 中 | 生成的 Rust 叶子 crate 估计 1–2 分钟（每个动作一个函数，避免巨型 match） |
| 构建依赖 | bison 2.4.1 / flex 2.5.35，和现在一样 | 无 | 无 | bison ≥3.7.3、re2c ≥3 |

**D 的验收方法**：
- 在测试二进制里同时链接 C 版和 Rust 版。
- 对 mysqltest 全部语句、sysbench/TPC-C 和语法驱动的模糊输入逐条比较：节点类型、子节点、标志、`str_value_`、位置、错误码、`start_col_/line_`，以及参数化输出。
- C 版现成的 `parsenode_equal` 和 `get_type_name` 可以直接拿来用。

**全文检索布尔模式**（`YYNRULES`=18，24 个状态）：在 A 下和 SQL 解析器放在同一个 -sys 里；如果做 D，就手写一个约 200 行的 Rust 版（估），用模糊测试和 C 版对比。

### 3.2 第三方库策略

**T1 全部走 FFI**：
- 对齐最稳，但会留下两套 TLS（OpenSSL 和 sql-nio 的 rustls），以及 gRPC C++、abseil 这些最重的依赖。
- RapidJSON、Boost、s2 这类纯头文件或 C++ 库，每个都要写 C++ shim。

**T2 全部换纯 Rust**：

| 换成 | 偏差 |
|---|---|
| `geo` | GIS 数值结果和 `OB_ERR_BOOST_GEOMETRY_*` 错误码都会变，地理策略不同 |
| regex 或 fancy-regex | ICU 的错误名、位置、时间和栈限制都对不上 |
| serde_json | 错误文本、偏移、relaxed flag、double 精度 |
| quick-xml | 恢复模式 |
| s2 crate | 覆盖结果 |

几个对用户可见的地方都会偏离，裁判会直接抓到。

**T3 逐库判断**（推荐）。判据：库的输出（结果、报错文本、落盘字节）是否就是它的算法本身。
- 是，就保留原生库，配薄封装。C 库用离线 bindgen；C++ 库用 cxx，并在 C++ 内把异常映射成 OB 错误码，沿用 `ob_geo_dispatcher.h:1313-1362` 的写法。
- 否，或者能用测试向量证明等价，就换纯 Rust。

逐库的结论见 §2.5 最后两列。下面三个选择要单独说明。

**Boost.Geometry**：

| 方案 | 做法 | 代价与风险 |
|---|---|---|
| G1 | 保留 35 个 Boost 文件和 geo 的类型头文件（估约 25–30K 行 C++），另写一层 oblib 兼容层：ObIAllocator、ObVector、ObString、日志、errno | 兼容层要长期维护 |
| G2（推荐） | 新写一个 cxx shim，大约 100 个算法入口（估，现有约 224 个模板调用点），C++ 估计 3–8K 行。WKB 字节进出，内部复用 OB 的零拷贝 WKB 视图类型和 traits（`ob_geo_bin_traits.h` 等约 0.8K 行，复制进 shim 并去掉 OB 依赖）。编排逻辑译成 Rust | 数值内核不变，对齐风险只在编排代码 |
| G3 | 用 `geo` | 接受偏差 |

G2 的 shim 接口示例：

```rust
#[cxx::bridge(namespace = "seekdb::bgk")]
mod ffi {
    struct Srs { geographic: bool, semi_major: f64, inv_flattening: f64 }
    unsafe extern "C++" {
        include!("bgk/kernel.h");
        fn relate(a: &[u8], b: &[u8], srs: &Srs, mask: &str, out: &mut bool) -> i32; // 返回 OB errno
        fn buffer(g: &[u8], srs: &Srs, p: &BufferParams, out: &mut Vec<u8>) -> i32;
    }
}
```

**RapidJSON**：

| 方案 | 做法 | 代价与风险 |
|---|---|---|
| R1 | C++ shim，SAX 回调用函数指针表 | 每个 SAX 事件跨一次语言，没法内联 |
| R2（推荐） | 移植 `GenericReader`，包括 3 个自定义 flag 和普通精度的 strtod；`GetParseError_En` 的文本逐字复制。以 R1 作为差分测试的参照 | 移植工作量约 2K 行（估） |
| R3 | serde_json | 报错文本、偏移、精度都对不上 |

**ICU**：
- 保留 69.1，用 `extern "C"` 直接声明带 `_69` 后缀的符号，例如 `uregex_open_69`，外面包一层 RAII，约 20 个函数。
- rust_icu 不支持 69，也没有 uregex，所以不适用。
- 升级 ICU 会带来 Unicode 版本变化，`\p{}` 字符类和大小写折叠都会跟着变。

### 3.3 生成器

| 方案 | 做法 | 评价 |
|---|---|---|
| Ga（推荐） | 输入是简单数据的生成器改写成 Rust build.rs，读同一份输入：errno 的 .def、系统变量 JSON（serde_json）、proto、版本号。内部表的 Python DSL 保留，只换发射器，输出 `static COLUMNS: &[ColumnDef]` 这类数据表加一个通用构建函数（估计从 105K 行代码降到 20–40K 行数据）。删除 str-cmp 和 datum 两个拆分生成器、`gen_type_name.sh`、syspack，共 4 个 | 和 C++ 共存期间，每个生成器从同一源头同时输出 C++ 和 Rust，保证错误码、系统变量 id、内部表 id 在两种语言里一致，先例是 `rust/config/build.rs` |
| Gb | 全部保留 Perl/Python，各加一个 Rust 后端 | 改动最少，但构建会继续依赖 Perl |
| Gc | 生成一次，把 Rust 产物入库，删掉生成器 | 以后改 1,548 条错误、776 个系统变量、384 张表都要手工维护，不可取 |

**错误消息的格式化**：生成器为每条错误生成一个带类型的函数，C 的 printf 语义要逐字节复刻：
- `%.*s` 和 `%.Ns` 按**字节**截断，可能截断多字节字符；所以消息缓冲用 `Vec<u8>`，不能用 `String`。
- `%f` 对应 `{:.6}`，但 inf/nan 要按 C 的写法 "inf"/"nan"/"-nan" 单独处理。
- 2,821 处调用由编译器检查参数类型和个数。

另一种做法是运行时模拟 printf，参数不检查。

```rust
pub const OB_ERR_PARSE_SQL: ObErrno = ObErrno(-5001);          // ER_PARSE_ERROR 1064, "42000"；常量还是枚举，由约定主题决定
pub fn ob_err_parse_sql(msg: &[u8], near: &[u8], line: i32) -> UserMsg { // "%s near '%.*s' at line %d"
    UserMsg::new(OB_ERR_PARSE_SQL).s(msg).lit(b" near '").bytes(near).lit(b"' at line ").d(line)
}
```

### 3.4 互操作技术

| 方向 | 工具 | 理由 |
|---|---|---|
| Rust 调 C 库（ICU uregex 子集、libxml2、解析器结构） | bindgen 离线生成，按平台入库 | 构建机不需要 libclang；和 nio.h、config.h 入库的做法一致 |
| Rust 调 C++ 库（Boost、s2、vsag shim） | cxx | 已在用（config）；支持切片、`Vec`、`UniquePtr`，有类型检查；异常在 shim 里转成错误码 |
| Rust 导出给 C 或 C++（解析器回调、共存期接口） | cbindgen；C++ 消费方需要丰富类型时用 cxx | 和 sql-nio、config 的先例一致 |
| 成熟的 -sys crate | libz-sys、rusqlite/libsqlite3-sys | 不重复造轮子；版本要钉住 |

**FFI 规则**：
- `panic=abort` 已经保证 panic 不会穿过边界。
- longjmp 不越过 Rust 栈帧：回调不得重入解析器。
- C++ 异常一律在 shim 内捕获。
- unsafe 只允许出现在 `*-sys`、`*-shim` 和 `parser` 视图 crate 里。

## 4. 推荐

**解析器选 A**，同时把 D 定为迁移完成后的独立可选阶段，以语法树差分为关口。理由：
1. 用户可见的 1064 文本、列名、PL 的报错，全部取决于 bison 2.4.1 自动机和 13K 行动作代码的确切行为。A 的对齐风险为零，B 和 C 都做不到。
2. FFI 面很窄：23+19 个函数，加两个 C 结构。consumer 侧的改写规则只有 5 条，适合 agent 批量做。
3. A 下的视图接口和 D 完全相同，日后替换实现不必重译 205 个消费文件。

**前提**：
- 内存主题提供一个能暴露给 C 的语句级 arena。
- 字符集模块接受那个 5 处访问函数的补丁。
- pl 在启动时注册 `lookup_pl_symbol`。
- 解析必须在一个 OS 线程上一次跑完，因为 `__thread` 的 `jmp_buf` 和错误缓冲是线程局部的。

**第三方库选 T3**，具体配置：
- G2（Boost shim）；ICU 保留；libxml2、s2、vsag、SQLite 保留；仓内 zstd 1.3.8 保留；zlib 固定 1.2.13。
- RustCrypto、tonic/prost、roaring、xxhash-rust、backtrace；HTTP 用 ureq/reqwest + rustls，去掉 OpenSSL 和 libcurl。
- RapidJSON 用 R2；JSON schema 的 pattern 用 regress。

**理由**：凡是库的语义直接到达用户或落盘的，都保留原生库；其余的换成纯 Rust，可以删掉最重的 C++ 依赖（gRPC、protobuf、abseil 中属于 gRPC 的部分、c-ares、re2、OpenSSL、protobuf-c、CRoaring）。

**估计的 unsafe 分布**：解析器视图约 60–100；ICU 约 20；libxml2 约 40–60；s2 和 vsag shim 约 30–50；zstd 和 libaio 约 20；Boost 走 cxx，手写约 0–20。合计约 200–350 处（估），作为对比，sql-nio 一个 crate 就有 186 处。

**生成器选 Ga**，错误消息用带类型的格式化函数。

## 5. 需要人决定的问题

1. **解析器路线**
   - (A) 保留 C 解析器走 FFI【推荐】
   - (B) 用 lalrpop/grmtools/pest 重写
   - (C) 手写递归下降
   - (D) 迁移期就做 bison 3.8 + Rust skeleton

   附带问题：是否把 D 列为迁移后的独立阶段，关口是语法树、错误位置、参数化三项差分全部一致？（是【推荐】/否）
2. **ParseNode 在 Rust 中的形态**
   - (a) 保持 C 布局，用 `ParseNode<'p>` 句柄，只改标量字段，`!Send`【推荐】
   - (b) 解析完把整棵树拷贝成 Rust 原生结构：每条语句多一次整树拷贝，23 个 C 辅助函数（构造、深拷贝、比较等）都要在 Rust 里重写一遍
   - (c) 新的类型化 AST：重写 205 个消费文件和参数化
3. **Boost.Geometry**
   - (G1) C++ 孤岛加 oblib 兼容层
   - (G2) 新写 cxx 薄 shim，Rust 负责编排【推荐】
   - (G3) 改用 `geo`，接受 GIS 数值和错误码的差异
4. **正则**
   - REGEXP_*：(a) 保留 ICU 69.1【推荐】／(b) 升级到 ICU 74+ 并用 rust_icu／(c) fancy-regex，接受差异
   - JSON schema 的 pattern：(a) regress【推荐】／(b) fancy-regex／(c) 用 C++ shim 保留 std::regex
5. **RapidJSON**
   - (R1) C++ shim
   - (R2) 移植到 Rust，以 R1 做差分测试【推荐】
   - (R3) serde_json，接受报错文本、偏移和精度的差异
6. **加密与 HTTP**
   - (a) RustCrypto + rustls，HTTP 用 ureq/reqwest，移除 OpenSSL 和 libcurl【推荐】
   - (b) 用 openssl crate 加 curl crate：行为最接近，但要保留两套 TLS
7. **gRPC**
   - (a) tonic + prost，在 standby 模块里起一个独立的 tokio runtime【推荐】
   - (b) 保留 C++ gRPC，走 shim
   - (c) grpcio，即 gRPC C core 的绑定
8. **数据目录兼容**：新二进制必须能读 C++ 版写下的数据目录吗？
   - (a) 必须：zstd 1.3.8 补丁版、zlib 1.2.13、bitpacking 布局、S2 cell id、xxhash、SQLite、vsag 序列化全部保持字节兼容【推荐作为默认】
   - (b) 不要求：只保证 SQL 结果一致，压缩库可以用新版
   - 不论选哪个，COMPRESS() 的输出字节都要求与现在一致吗？（是【推荐】/否）
9. **生成器**
   - (Ga) 能用 build.rs 的就用 build.rs，内部表保留 Python 前端，删除 4 个只服务 C++ 的生成器【推荐】
   - (Gb) 全部保留 Perl/Python，各加 Rust 后端
   - (Gc) 生成一次后入库，删除生成器
10. **用户消息格式化**
    - (a) 每条错误一个带类型的格式化函数，编译期检查参数，字节级复刻 printf【推荐】
    - (b) 运行时模拟 printf
11. **绑定生成**
    - (a) bindgen 离线生成并入库，C++ 用 cxx，Rust 导出用 cbindgen【推荐】
    - (b) 在 build.rs 里跑 bindgen：每台构建机都要装 libclang

## 6. 对其他主题的依赖或影响

- **SQL IR 图**：
  - ParseNode 是独立的 C 布局树，生命周期等于语句内存池，和 IR 主题的"类型化 id 加 arena"方案不冲突。`TableItem::node_` 这类引用存句柄，要求解析 arena 活过 resolve（IR 备忘 §6 已经指出）。
  - ObItemType 要统一成一个 `#[repr(i32)]` 枚举，同时供解析器和表达式使用。现有两份副本，146 个文件引用它，其中 sql 占 139 个。
  - 102 个 ST_* 表达式是 Boost shim 的调用方。
- **内存与分配器**：
  - 解析 arena 要通过 `parse_malloc` 暴露给 C，arena 里的类型不能有 Drop。ParseNode 是 POD，满足这一条。
  - 第三方的分配钩子要重新接到计量分配器上：zstd `customMem`、zlib `zalloc`、vsag allocator、JSON 解析器的 allocator。OpenSSL 和 CRoaring 的钩子随着换库消失；纯 Rust 的 roaring 改走全局分配器。
- **并发**：
  - tonic 需要 tokio，限定在 standby 模块里；reqwest 如果用异步版也一样。
  - vsag 在内部起 OpenMP 线程。
  - C 解析器用了 `__thread` 状态，解析期间不能让出线程。
  - libxml2 有全局初始化和线程局部的错误状态；ICU 的 regex 对象不是线程安全的，要按表达式上下文各建一份。
- **存储**：问题 8 决定压缩库、bitpacking 布局、S2 cell id、xxhash、SQLite、vsag 序列化能不能动。palf 日志头的 XXH64 和 `'zstd_1.3.8'` 压缩器名都是落盘格式。
- **分层**：
  - 所有 -sys 和 shim crate 放在 oblib 层之下或与之同层。
  - 解析器 -sys 回调 oblib（分配、字符集）是向下依赖；回调 pl（`lookup_pl_symbol`）是向上依赖，要改成注册，和 12 条逆向依赖的拆法一致。
  - geo kernel shim 放在 share/geo 之下。
  - 解析器 -sys 要把 SQL、PL、全文检索三套 C 放进同一个 crate，因为 PL 直接调用 `parse_sql_stmt`。
- **约定**：
  - errno 生成器决定 `ObError` 的形式、名字到码的映射和带类型的用户消息，这是 `Result<T, ObError>` 约定的数据源。
  - 系统变量钩子和生成项都要遵守 RULEBOOK §4 的命名规则。
  - unsafe 白名单只包括 -sys、shim 和视图 crate，每处都要写 `SAFETY:` 注释。
- **编译时间**：
  - 生成的 C 解析器（17 万行）和 Boost shim 都是叶子 crate 的 build.rs 产物，编一次就缓存，不影响增量构建。
  - 内部表从 105K 行代码变成数据表，可以减轻 share crate 的编译负担。
  - tonic、tokio、prost 会增加约 100 个依赖 crate（估），冷编译一次。
