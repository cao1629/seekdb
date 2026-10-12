# 09 `parser-sys-generators`：解析器 crate、-sys 与 shim、生成器

> 依据：`migration/infra/README.md`；`migration/RULEBOOK.md` v0.2（2026-10-11 签字）0.3、0.12、0.13、0.14、0.15（构建参数、D17 (b)）、§1 白名单、§2.9、§3 第 8、12、13 项、§4 的 `generated/` 规则。基线 `7d907abfa`（`git diff --stat 7d907abfa HEAD -- src` 为空）。
>
> 本模块负责的东西都在 C ABI 或构建期，不是被翻译的普通单元：
> - `parser` crate（`rust/port/parser`）：bison/flex 生成的三套 C 解析器、`ParseNode<'p>` 视图、回调蹦床；
> - 7 个自有 -sys crate（`zstd138_sys`、`zlib1213_sys`、`icu69_sys`、`libxml2_sys`、`openssl_sys`、`curl_sys`、`libaio_sys`），以及 `seekdb-jemalloc-sys` 的构建配置；
> - 4 个 cxx shim（`bgk_shim`、`s2_shim`、`vsag_shim`、`regex_shim`）；
> - 生成器：errno、`ObItemType`、系统变量、proto、版本号、内部表、bitpacking；4 个被删除的生成器；`*_data` 的转换脚本；`generated/` 目录契约；`migration/design/third_party_versions.tsv` 的格式。
>
> 事实来源与计数口径：基线源码；`build_release/`（macOS arm64 RelWithDebInfo 构建，本机已有）里的 `compile_commands.json`、`.o`、`generated/`、`link.txt`。符号面用 `nm -g` 对目标文件计算；头文件闭包和"哪份 `ob_item_type.h` 先生效"用 `clang -E -H`（只预处理，输出丢到 `/dev/null`，不编译、不改动仓库）。只有 macOS arm64 的构建产物，x86-64 上的差异靠读 `#if` 分支得出，已逐处注明。"约"字表示正则计数。
>
> 与其他 1d 文档的接口：errno 生成物的**形状**（常量、各张表、`user_msg::OB_X` 的签名）由 04 §2.1 规定，本文只规定生成器本身（§2.5.1）；`Arena::alloc`、`ForeignBlock`、`check_mem_status` 在 01；`check_stack_overflow` 在 03；`murmurhash` 在 05；`Slot` 在 06；`ObCollationType` 由 07（object 模块）提供。
>
> RULEBOOK 与源码不符的 9 处列在 §8.2，已追加到 `migration/infra/amendments.tsv`。

## 1. 范围

### 1.1 解析器的构建输入（不是单元）

语法和词法文件不属于 RULEBOOK §4 的单元扩展名，它们是 `parser` crate 的 build.rs 的输入，不进 `units.tsv`。

| 文件 | 行数 | 生成物 | 生成方式（C++ 现状） |
|---|---|---|---|
| `src/sql/parser/sql_parser_mysql_mode.y` | 18,723 | `sql_parser_mysql_mode_tab.{c,h}`、`.output` | `bison -v -Werror -d`，输出含 `conflict` 就失败（`src/sql/parser/gen_parser.sh:62-76,90`） |
| `src/sql/parser/sql_parser_mysql_mode.l` | 1,853 | `sql_parser_mysql_mode_lex.{c,h}` | `flex -Cfa -B -8 -o …_lex.c ….l …_tab.h`（`:91`，`_tab.h` 作为第二个输入文件），再跑 4 条 sed（`:93-96`） |
| `src/sql/parser/ftsparser.y` | 250 | `ftsparser_tab.{c,h}` | 同上（`:81`） |
| `src/sql/parser/ftsblex.l` | 85 | `ftsblex_lex.{c,h}` | `flex -Cfa -B -8`（`:82`）+ 4 条 sed（`:84-87`） |
| `src/pl/parser/pl_parser_mysql_mode.y` | 2,976 | `pl_parser_mysql_mode_tab.{c,h}` | `src/pl/parser/gen_parser.sh:38-52,61` |
| `src/pl/parser/pl_parser_mysql_mode.l` | 698 | `pl_parser_mysql_mode_lex.{c,h}` | `flex -o …_lex.c ….l …_tab.h`（`:62`；**没有** `-Cfa -B -8`，没有 sed） |
| `src/query/api/query/parser/ob_item_type.h` | 2,477 | `src/sql/parser/type_name.c` | `gen_type_name.sh "$ITEM_TYPE_HEADER" > type_name.c`（`gen_parser.sh:9,100`）。Rust 版不再生成它（§1.8） |

- 两个 `gen_parser.sh` 由 CMake 在配置阶段 `execute_process` 调用，工作目录是源码目录（`src/sql/CMakeLists.txt:17-36`、`src/pl/CMakeLists.txt:1-20`）；它把 `deps/3rd/usr/local/oceanbase/devtools/bin` 放到 `PATH` 前面、设 `BISON_PKGDATADIR`（`gen_parser.sh:11-12`），要求 `bison -V` 恰为 2.4.1（`:29-35`）；`NEED_PARSER_CACHE=ON` 时按 md5 跳过（`:105-116`）。
- 生成物写回源码目录，被 `.gitignore:140` 等行忽略；Windows 走 `gen_parser_win.ps1`（Windows 只做 `cargo check`，不在 build.rs 的范围内）。
- 本机 `deps/3rd` 的工具版本：`bison (GNU Bison) 2.4.1`、`flex 2.5.35`、`clang version 17.0.6`；bison 编译时写死的 m4 路径是 `/usr/bin/gm4`（`strings` 可见），本机 `/usr/bin/m4` 为 GNU M4 1.4.6。Linux 的包是 `obdevtools-bison-2.4.1`、`obdevtools-flex-2.5.35`（`deps/init/oceanbase.el8.x86_64.deps:35,38`）。
- 不进 Rust 的：`gen_parser*.sh`、`gen_parser_win.ps1`、`gen_type_name.sh`、`reserved.py`（开发工具，不参与构建）、`README`、各 `CMakeLists.txt`/`BUILD.bazel`。

### 1.2 解析器单元（25 个）

解析器目录（`src/sql/parser`、`src/pl/parser`、`src/sql/pl/parser`、`src/query/api/query/parser`）在临时 manifest 里有 30 行；`src/pl/X` 与 `src/sql/pl/X` 按 §4 合并掉 3 行，两行 `ob_item_type.h` 归 §1.4，剩下 25 个单元。其中 8 个含 C 源文件，处置是 `keep-c`（与解析器调查 §3.1 的"8 个含 C 的标保留 C"一致）。

| # | 单元（代表文件；其余成员） | 内容 | 处置 | Rust 落点或桩 |
|---|---|---|---|---|
| 1 | `src/query/api/query/parser/parse_node.h` | `ParseNode`、`ParseResult` 等 C 结构，23 个入口里的大部分声明（`:129-195,293-367,386-440`） | `replace:parser` | 目标路径改为 `rust/port/parser/src/parse_node.rs`（桩：`pub use crate::*`）。§4 的路径规则会把它放进 query，而 query 不能依赖 parser，这里是例外，由 1c 的脚本特判 |
| 2 | `src/sql/parser/parse_node.h`；`parse_node.c` | 转发头（`#pragma once` + include）；节点构造、深拷贝、哈希、比较（1,024 行 C） | `keep-c` | 桩 `rust/port/sql/src/parser/parse_node.rs`：`pub use parser::{ParseNode, ParseArena, …}` |
| 3 | `src/sql/parser/sql_parser_base.h`；`.c` | 词法/语法共用宏；`parse_init`、`parse_sql`、`parse_sql_stmt` | `keep-c` | 桩 |
| 4 | `src/sql/parser/fts_base.h`；`.c` | 全文检索的 AST 与构造函数 | `keep-c` | 桩：`pub use parser::{FtsParserResult, FtsNode, …}` |
| 5 | `src/sql/parser/fts_parse.h`；`.c` | `fts_parse_docment` | `keep-c` | 桩 |
| 6 | `src/sql/parser/ob_non_reserved_keywords.h`；`.c` | 关键字 trie | `keep-c` | 桩 |
| 7 | `src/sql/parser/non_reserved_keywords_mysql_mode.c` | SQL 关键字表（1,096 行） | `keep-c` | 桩 |
| 8 | `src/pl/parser/pl_non_reserved_keywords_mysql_mode.c` | PL 关键字表 | `keep-c` | 桩 |
| 9 | `src/pl/parser/pl_parser_base.h`；`.c` | `obpl_parser_init`、`obpl_parser_parse`、`check_cursor_node` | `keep-c` | 桩 |
| 10 | `src/sql/parser/parse_define.h` | 17 个 `OB_PARSER_ERR_*` 加 `OB_PARSER_SUCCESS`（`:26-43`），C 也包含它 | `replace:parser` | `parser::define`（§2.2.12） |
| 11 | `src/sql/parser/parse_malloc.h`；`parse_malloc.cpp` | 8 个分配回调、3 个字符集回调（C++）；另有 4 个没有调用者的函数（`charset_upper` :44、`strndup_with_prefix_and_postfix` :243、`cp_str_value` :268、自由函数版 `parse_strdup_with_replace_multi_byte_char` :281；`nm` 确认 C 不引用，C++ 只有同名的成员函数 `ob_fast_parser.cpp:1031`） | `replace:parser`（11 个函数按函数级重映射进 `parser::callbacks`；4 个死函数不移植，交清单第 16 类确认） | `parser::callbacks`、`ParseArena` |
| 12 | `src/sql/parser/parse_node_hash.h`；`.cpp` | `murmurhash` 的 C 包装（`parse_node_hash.cpp:21-24`） | `replace:parser` | `parser::callbacks::murmurhash` |
| 13 | `src/sql/parser/ob_parser_charset_utils.h`；`.cpp` | 枚举 `ObCharsetParserType`（C 包含但不使用）；3 个 `obcharset_is_*`（无调用者） | `delete`（交清单第 16 类确认；头文件仍作为 C 源文件参与编译） | — |
| 14 | `src/sql/parser/ob_memory_tracker_wrapper.h`；`.cpp` | `check_mem_status`、`try_check_mem_status` 的强定义 | 由 01 负责（01 文档 §1 第 81 行列为 `replace:mem`）。**事实**：这个 `.cpp` 不在 `src/sql/parser/CMakeLists.txt:2-47` 的列表里，`compile_commands.json` 0 处；`src/sql/executor/ob_memory_tracker_wrapper.cpp` 只有两行 include；`nm` 显示只有 `parse_node.c.o` 定义这两个符号，且是 weak（`parse_node.c:177-178`）。基线里解析器用的是恒返回成功的 weak 版本，见 §3.1.9 和 §8.1 Q5 | — |
| 15 | `src/sql/parser/parser_utility.h`；`.cpp` | `parser_to_hex_cstr`，全仓只有自身和 `src/sql/bazel_pilot/…probe.cpp` 引用 | `delete`（第 16 类确认） | — |
| 16 | `src/sql/parser/ob_parse_simd.cpp` | fast parser 的 SIMD 十六进制处理；另以 `extern "C"` 导出 `ob_parse_binary_simd`（`:204`），只在 x86-64 上被 `parse_node.c:551-553` 调用 | `translate`（sql 的 `simd` 模块）；`ob_parse_binary_simd` 函数级重映射到 `parser::callbacks` | — |
| 17 | `src/sql/parser/ob_parser.h`；`.cpp` | `ObParser` | `translate`（sql） | — |
| 18 | `src/sql/parser/ob_sql_parser.h`；`.cpp` | `ObSQLParser::parse` = `parse_init` + `parse_sql`（`ob_sql_parser.cpp:30-39`） | `translate` | — |
| 19 | `src/sql/parser/ob_fast_parser.h`；`.cpp` | `ObFastParser` | `translate` | — |
| 20 | `src/sql/parser/ob_parser_utils.h` | `ParseNode` 的 `databuff_print_obj`（`:47-73`）、打印包装类、`ObCharsets4Parser` | `translate`；`databuff_print_obj(ParseNode)` 与 `databuff_print_stmt_location` 函数级重映射到 parser（`ObToString` 的 impl 必须在类型所在的 crate，RULEBOOK 0.4） | — |
| 21 | `src/sql/parser/ob_item_type_str.h` | `ObItemType` → 聚合函数名等的 switch | `translate` | — |
| 22 | `src/sql/parser/ob_char_type.h` | fast parser 的字符表 | `translate` | — |
| 23 | `src/sql/pl/parser/ob_pl_parser.h`；`src/pl/parser/ob_pl_parser.{h,cpp}` | `ObPLParser` | `translate`（sql::pl）；`obpl_parser_check_stack_overflow`（`ob_pl_parser.cpp:28-34`）函数级重映射到 `parser::callbacks` | — |
| 24 | `src/sql/pl/parser/parse_stmt_node.h`；`src/pl/parser/parse_stmt_node.h` | `ObParseCtx`、`ObScannerCtx`、`ObParseErrorInfo`（`:31-91`） | `replace:parser` | `parser::ObParseCtx` |
| 25 | `src/sql/pl/parser/parse_stmt_item_type.h`；`src/pl/parser/parse_stmt_item_type.h` | PL 的 C 枚举（`SpParam`、`SpHandlerType`、`SignalCondInfoItem` 等） | `replace:parser`（枚举由 bindgen 按 §2.12 转成新类型加常量） | `parser::sys` 里的常量 |

合计：`keep-c` 8，`replace:parser` 6，`translate` 8（其中 4 个带函数级重映射），`delete` 2，归 01 的 1。

**与 1c 脚本已写出的 `migration/design/units.tsv` 的差异**：该表的 25 个解析器行（依据列 `R-parser-*`）里，16 行与上表一致（`keep-c` 8、`translate` 8）；以下 9 行不同，1c 审查时按上表改：

- `src/query/api/query/parser/parse_node.h`：现为 `keep-c`，目标 `rust/port/query/src/parser/parse_node.rs`（crate `query`）。parser 依赖 query（RULEBOOK 0.3），query 里的桩写不出 `pub use parser::…`，所以目标必须放在 parser crate（#1）；处置记 `replace:parser`，对应 RULEBOOK 0.12 的"`parse_node.h` 变成布局镜像"。query 里用到 `ParseNode` 的 3 个签名见 §8.1 Q3。
- `parse_define.h`、`src/sql/pl/parser/parse_stmt_node.h`、`parse_stmt_item_type.h`：现为 `keep-c`（依据 `R-parser-c-header`）。这三个单元只有头文件；C 照旧包含原文件，Rust 侧的东西是 parser 里手写或 bindgen 出的 API，所以记 `replace:parser`（#10、#24、#25）。两种记法下的桩内容相同（`pub use parser::…`），只差桩文件头的 `//! Disposition:`。
- `parse_malloc.h`、`parse_node_hash.h`：现为 `translate`（REVIEW）。它们的函数就是 C 调用的回调，只能由 parser 的蹦床实现（§2.2.10），不进翻译队列（#11、#12）。
- `ob_parser_charset_utils.h`、`parser_utility.h`：现为 `translate`（REVIEW）。两者都没有调用者，记 `delete`（#13、#15）。
- `ob_memory_tracker_wrapper.h`：现为 `translate`（REVIEW）。01 文档已列为 `replace:mem`，以 01 为准（#14；另见 §8.1 Q5）。

### 1.3 FFI 面

#### 1.3.1 server 调解析器：23 个函数

用 `nm -g` 对 15 个解析器 C 目标文件（`build_release/src/sql/parser/CMakeFiles/ob_sql_server_parser_objects.dir/*.c.o` 11 个、`build_release/src/pl/CMakeFiles/ob_pl.dir/parser/*.c.o` 4 个）求出它们定义的 189 个全局符号，再与其余 627 个目标文件的未定义符号取交集，得到 23 个，与解析器调查的数字一致。

| C 函数 | 声明 | 定义 | 基线调用方（目标文件） | Rust（§2.2.9） |
|---|---|---|---|---|
| `parse_init` | `parse_node.h:386` | `sql_parser_base.c:33` | `ob_sql_parser.cpp.o` | `parse_init(&mut ParseResult)` |
| `parse_sql` | `parse_node.h:388` | `sql_parser_base.c:69` | 同上 | `parse_sql(&mut ParseResult<'p>, &'p [u8])` |
| `obpl_parser_init` | `ob_pl_parser.cpp:25`（自写原型） | `pl_parser_base.c:31` | `ob_pl_parser.cpp.o` | `obpl_parser_init(&mut ObParseCtx) -> c_int` |
| `obpl_parser_parse` | `ob_pl_parser.cpp:26` | `pl_parser_base.c:42` | 同上 | `obpl_parser_parse(&mut ObParseCtx) -> ObResult` |
| `fts_parse_docment` | `fts_parse.h:35` | `fts_parse.c:23` | `ob_das_legacy_tr_merge_iter.cpp.o`、sql 的 das_1、optimizer_0 两个 unity | `fts_parse_docment(..)` |
| `new_node` | `parse_node.h:396` | `parse_node.c:180` | sql resolver_0、resolver_dml_0 | `ParseArena::new_node` |
| `new_non_terminal_node` | `:397`（可变参数） | `:374` | resolver_0、resolver_dml_0、resolver_expr_0 | `ParseArena::new_non_terminal_node` |
| `new_terminal_node` | `:398` | `:368` | 同上 | `ParseArena::new_terminal_node` |
| `append_child` | `:432` | `:941` | resolver_dml_0 | `ParseArena::append_child` |
| `push_back_child` | `:430` | `:864` | resolver_dml_0 | `ParseArena::push_back_child` |
| `merge_tree` | `sql_parser_base.h:65`、`pl_parser_base.h:111` | `parse_node.c:332` | `ob_pl_parser.cpp.o` | `ParseArena::merge_tree` |
| `deep_copy_parse_node` | `parse_node.h:407` | `parse_node.c:143` | plan_cache_0 | `ParseArena::deep_copy_parse_node` |
| `deep_copy_parse_node_base` | `:406` | `:96` | resolver_dml_0 | `ParseArena::deep_copy_parse_node_base` |
| `get_deep_copy_size` | `:405` | `:58` | plan_cache_0 | `get_deep_copy_size` |
| `parsenode_equal` | `:425` | `:693` | resolver_dml_0 | `parsenode_equal` |
| `get_type_name` | `query/…/ob_item_type.h:2388` | 生成的 `type_name.c` | 60 个目标文件 | `query::item_type::get_type_name`（§2.5.2；解析器 C 自己只声明不调用，`parse_node.c:23`） |
| `mysql_sql_reserved_keyword_lookup` | `ob_non_reserved_keywords.h:40` | `non_reserved_keywords_mysql_mode.c:1068` | sql common_0 | 同名 |
| `ob_parse_binary` / `_len` | `parse_node.h:410,411` | `parse_node.c:533,527` | `ob_fast_parser.cpp.o` | 同名 |
| `ob_parse_bit_string` / `_len` | `:415,416` | `:568,563` | 同上 | 同名 |
| `str_tolower` | `:391` | `:599` | sql common_0 | 同名 |
| `str_toupper` | `:392` | `:619` | `parse_malloc.cpp.o` | 同名 |

23 个之外，Rust 还封装 `parsenode_hash`（`parse_node.c:654`）：基线二进制里它没有调用者（`ob_hashutils.h:819-835` 的 `hash_func<_ParseNode*>` 特化没有被实例化），封装只为完整。

#### 1.3.2 解析器回调 server：19 个函数（x86-64 上 20 个）

同一批 15 个目标文件的未定义符号减去自身定义、去掉 libc，得到 19 个（解析器调查 [C5] 的口径）。这是 macOS arm64 的结果；`parse_node.c:551-553` 在 `__x86_64__` 下还调用 `ob_parse_binary_simd`，所以 x86-64 是 20 个（见 §8.2 A1）。

| C 符号 | 声明 | C++ 定义 | C 侧调用点 | Rust 落点（§2.2.10） |
|---|---|---|---|---|
| `parse_malloc` | `parse_malloc.h:30` | `parse_malloc.cpp:90-105` | 节点、字符串、扫描器缓冲（`yyalloc`，`sql_parser_mysql_mode.l:1810-1819`） | `ParseArena`（直接） |
| `parse_realloc` | `:31` | `:121-142` | `yyrealloc`（`.l:1821-1830`） | `ParseArena`（直接） |
| `parse_free` | `:32` | `:262-266`（空函数） | `yyfree` | 空函数 |
| `parse_strndup` | `:33` | `:144-157` | 词法动作 | `ParseArena` |
| `parse_strdup` | `:34` | `:159-170` | 同上 | `ParseArena` |
| `parse_str_convert_utf8` | `:36` | `:203-224` | `.l:621,691,1171,1262,1347,1541,1754`、`.y:766,10668`、`sql_parser_base.h:234`、PL `.l:354,557,630` | `ParserHost`（字符集） |
| `replace_invalid_character` | `:37` | `:172-201` | `sql_parser_mysql_mode.l:1344` | `ParserHost` |
| `strndup_with_prefix` | `:40` | `:226-241` | PL 动作（表达式加 `"SELECT "` 前缀，`pl_parser_mysql_mode.y:186-195`） | `ParseArena` |
| `check_real_escape` | `:45`；`sql_parser_base.h:72` | `:324-346` | `CHECK_REAL_ESCAPE`（`sql_parser_base.h:771-773`） | `ParserHost` |
| `parser_alloc` | `:47` | `:107-118` | 深拷贝（`parse_node.c:113,129,155,166`） | `ParseArena` |
| `malloc_parentheses_info` | `:49` | `:75-87` | 多行 VALUES 拆分 | `ParseArena` |
| `murmurhash` | `parse_node_hash.h:26` | `parse_node_hash.cpp:21-24` | `parsenode_hash`（`parse_node.c:663-682`） | `oblib::hash::murmurhash`（05，直接） |
| `ob_strntoll` | `sql_parser_base.h:69`、`pl_parser_base.h:35` | `src/oblib/lib/charset/ob_ctype_simple.cc:754` | 数字字面量 | `ParserHost` |
| `ob_strntoull` | `:70`、`:36` | `ob_ctype_simple.cc:759` | 同上 | `ParserHost` |
| `lookup_pl_symbol` | `parse_node.h:463` | `src/pl/ob_pl_stmt.cpp:3376-3397` | `sql_parser_base.h:418-441`（PL 里的 SQL 把 PL 变量换成 `:N`） | 每次解析装入的 `&dyn PlSymbolLookup` |
| `check_stack_overflow_c` | `parse_node.h:461` | `src/sql/ob_sql_utils.cpp:1964-1971` | `parsenode_hash`、`parsenode_equal`（`parse_node.c:657,696`） | `oblib::thread::check_stack_overflow`（03，直接） |
| `obpl_parser_check_stack_overflow` | `parse_node.h:401` | `src/pl/parser/ob_pl_parser.cpp:28-34` | `pl_parser_mysql_mode.y:90` | 同上（03，直接） |
| `ob_backtrace_c` | `parse_node.c:1017`（自写原型） | `src/oblib/lib/utility/ob_backtrace.cpp:192-195` | `parser_lbt`（`parse_node.c:1019-1024`，基线里没有调用者，但要能链接） | `ParserHost` |
| `parray_c` | `parse_node.c:1018` | `ob_backtrace.cpp:196-199` | 同上 | `ParserHost` |
| `ob_parse_binary_simd`（仅 x86-64） | `parse_node.c:532` | `src/sql/parser/ob_parse_simd.cpp:204` | `ob_parse_binary`（`parse_node.c:551-553`，`#if defined(__GNUC__) && defined(__x86_64__)`） | `parser::simd`（直接） |

#### 1.3.3 其他 C 侧事实

- **线程局部和跳转**：`parse_init` 把 `p->error_msg_` 指向函数内的 `static __thread char error_msg[1024]`（`sql_parser_base.c:36-37`）；`parse_sql` 用 `static __thread jmp_buf`（`:104-106`）做 setjmp；longjmp 只在 `obsql_mysql_parser_fatal_error`（`sql_parser_mysql_mode.l:1839-1852`）。PL 的 jmp_buf 在 `ObParseCtx` 里（`parse_stmt_node.h:61`），setjmp 在 `pl_parser_base.c:48`，longjmp 在 `pl_parser_mysql_mode.l:672`。全文检索词法器没有覆盖 `YY_FATAL_ERROR`，用 flex 默认的 `yy_fatal_error` → `exit(2)`（`ftsblex_lex.c:1882`）。
- **字符集**：活代码里 C 不解引用 `ObCharsetInfo`。`sql_parser_base.h:776-806` 的 5 处字段访问都在 `/* … */` 注释里（注释从 `:774` 开始到 `:796`，第二段 `:797-811`）；C 只把 `charset_info_` 原样传给 3 个回调，PL 语法把它拷进内层的 `ParseResult`（`pl_parser_mysql_mode.y:130-131,2927-2928`），全文检索把它置空（`fts_parse.c:32`）。见 §8.2 A2。
- **头文件闭包**：用 `clang -E -H` 对 15 个解析器 C 文件预处理（参数取自 `compile_commands.json`），非系统头文件共 42 个：解析器四个目录的头文件，加 `src/oblib/common/sql_mode/ob_sql_mode.h`、`src/oblib/lib/ob_date_unit_type.h`、`src/oblib/lib/utility/alloc_assist.h`、`src/oblib/lib/utility/ob_macro_utils.h`、`src/share/cache/ob_cache_type.h`、`src/share/schema/ob_{obj_,sys_,}priv_type.h`、`src/sql/executor/ob_memory_tracker_wrapper.h`、`src/sql/ob_trans_character.h`，以及 clang 自带的 `stdint.h` 等。不依赖 `build_release/generated`。
- **编译参数**（`compile_commands.json` 里 `parse_node.c`、`sql_parser_mysql_mode_tab.c`、`pl_parser_mysql_mode_tab.c` 的参数完全相同）：编译器 `deps/3rd/usr/local/oceanbase/devtools/bin/clang`；`-O2 -g -DNDEBUG -arch arm64 -isysroot …/MacOSX15.sdk -mmacosx-version-min=15.0 -fPIC -fno-strict-aliasing -fno-omit-frame-pointer -march=armv8-a+crc+lse -mtune=generic -ffunction-sections -fdata-sections -fmax-type-align=8`；`-D`：`DEFAULT_LOG_FILE_SIZE_MB=256 DEFAULT_LOG_LEVEL=OB_LOG_LEVEL_ERROR OB_BUILD_SYS_VEC_IDX OB_HAVE_BUNDLED_JEMALLOC=1 PACKAGE_NAME="OceanBase" PACKAGE_STRING="OceanBase 1.4.0.0" PACKAGE_VERSION="1.4.0.0" RELEASEID="1" _DARWIN_C_SOURCE _GLIBCXX_USE_CXX11_ABI=1 NDEBUG __STDC_LIMIT_MACROS __STDC_CONSTANT_MACROS _NO_EXCEPTION OCI_LINK_RUNTIME`；没有 `SQL_PARSER_COMPILATION`（与 `src/query/BUILD.bazel:329-331` 的注释一致），没有 `-std`（clang 17 的 C 缺省是 gnu17），没有 `-ffp-contract`。这些参数的出处见 §1.5 末尾。

### 1.4 `ObItemType`（3 个单元）

| 单元 | 事实 | 处置 |
|---|---|---|
| `src/query/api/query/parser/ob_item_type.h`（manifest 944） | include guard `OCEANBASE_SQL_OB_ITEM_TYPE_H_`（`:17-18`）；枚举从 `:27` 起，2,019 个枚举值，只有 `T_MAX` 是隐式取值（=4921）；`[-1, 4921]` 里有 2,904 个空洞；`:20` include `share/cache/ob_cache_type.h`；`:2388` 声明 `get_type_name`，`:2460-2472` 是两个打印特化 | `generate:item_type` |
| `src/objit/include/objit/common/ob_item_type.h`（manifest 731） | 同一个 guard（`:17-18`）；枚举从 `:25` 起，2,020 个值，`T_MAX`=4920 | `generate:item_type`（桩 `pub use query::item_type::*`） |
| `src/sql/parser/ob_item_type.h`（manifest 956） | 18 行，`#pragma once` + include query 那份 | `generate:item_type`（桩） |

两份的差异（按 C 枚举规则逐项求值后比较，去掉注释）：只在 objit 那份：`T_AUTO_PARTITION=4440`、`T_LS_ATTR_LIST=4757`、`T_ALTER_LS=4758`；只在 query 那份：`T_RESERVED_3791=3791`、`T_FLUSH_PRIVILEGES=4920`；同名不同值：`T_MAX`（4920 对 4921）。其余 2,017 个同名同值。

谁先生效：
- 解析器 C 和 `type_name.c` 只看到 query 那份（`parse_node.h:29,32`；`gen_parser.sh:9`；预处理闭包里没有 objit 那份）。
- 全仓只有 `src/query/api/query/engine/expr/ob_expr_operator.h:36` include objit 那份，紧接着 `:37` include query 那份；guard 相同，所以先被包含的那份生效。对 unity 编译单元 `unity_ob_sql_ob_sql_engine_expr_13`（`build_release/src/sql/CMakeFiles/ob_sql.dir/Unity/`）预处理，第一次出现的是 objit 那份，链路是 `ob_expr_random_bytes.cpp` → `ob_expr_random_bytes.h` → `sql/engine/expr/ob_expr_operator.h` → `query/api/query/engine/expr/ob_expr_operator.h:36`。也就是说，服务端确有编译单元里 `T_MAX` 是 4920、没有 `T_FLUSH_PRIVILEGES`。`T_MAX` 作为哨兵值用在 `ob_raw_expr.h:4681,4692`（`func_type_(T_MAX)`）等处，跨编译单元比较时可能不一致。这正是清单第 23 类要判定的内容；生成器的机制见 §2.5.2。

### 1.5 第三方库与 -sys crate

"基线链接"一列的行号在 `src/oblib/CMakeLists.txt`，除非另注；本机实际链接顺序见 `build_release/src/observer/CMakeFiles/seekdb.dir/link.txt`（`libxml2.a liblzma.a` 第 49-50 个记号，`libcurl.a libssl.a libcrypto.a` 第 66-68 个，`libz.a libicui18n.a libicustubdata.a libicuuc.a` 第 92-95 个，然后 `-lpthread -ldl -framework Security -framework CoreFoundation -framework SystemConfiguration -liconv`）。macOS 用的 deps 清单是 `oceanbase.macos15.arm64.deps`（`deps/init/dep_create.sh:216-226` 按系统版本选；`deps/3rd` 下的标记文件名等于它的 md5）。

| crate | 库与版本（按平台） | 基线的来源与链接 | 用到的 API（file:line） | 回调进 OB |
|---|---|---|---|---|
| `zstd138_sys` | zstd 1.3.8，**仓内源码，OceanBase 改过**；所有平台相同 | `src/oblib/lib/compress/zstd_1_3_8/zstd_src/`（22 个 `.c`、25 个 `.h`）。`zstd_1_3_8/CMakeLists.txt:1-44`：OBJECT 库，`-DZSTDERRORLIB_VISIBILITY= -DZDICTLIB_VISIBILITY= -DZSTDLIB_VISIBILITY=`、`-fvisibility=hidden`，再 `ld -r` + `llvm-objcopy --localize-hidden`，链接后只剩 `ObZstdWrapper::*` 15 个全局符号 | `ob_zstd_wrapper.cpp` 调 18 个函数共 31 处（`ZSTD_createCCtx_advanced` :56,140、`ZSTD_compressCCtx` :59、`ZSTD_isError` 8 处、`ZSTD_freeCCtx`、`ZSTD_createDCtx_advanced`、`ZSTD_decompressDCtx` :107、`ZSTD_freeDCtx`、`ZSTD_compressBegin`、`ZSTD_CCtx_reset`、`ZSTD_compressBlock`、`ZSTD_decompressBegin`、`ZSTD_decompressBlock`、`ZSTD_compressBound`、`ZSTD_insertBlock`、`ZSTD_createDStream_advanced`、`ZSTD_freeDStream`、`ZSTD_decompressStream`、`ZSTD_compressStream2`）。改过的签名：`ZSTD_compressCCtx(…, int *zstd_version)`（`zstd.h:173-177`，定义 `zstd_compress.c:3371-3384`）、`ZSTD_decompressDCtx(…, int *zstd_version)`（`zstd.h:194-197`，`zstd_decompress.c:727-735`），写回 138 | `customMem`：`ob_zstd_malloc`/`ob_zstd_free`（`ob_compress_util.h:76-92`，`ob_zstd_compressor_1_3_8.cpp:38,74`） |
| `zlib1213_sys` | zlib 1.2.13：el8 `devdeps-zlib-static-1.2.13`（`oceanbase.el8.x86_64.deps:21`），macOS 15 `devdeps-zlib-1.2.13`（`macos15.arm64.deps:24`），Android 同版本，Windows vcpkg（版本未标） | `deps/3rd/usr/local/oceanbase/deps/devel/lib/libz.a` 按全路径静态链接（`:318`；Windows `zlib.lib` `:296`）；无 `Z_PREFIX` | 14 个函数，19 处调用：`ob_zlib_compressor.cpp`（`adler32` :70,89、`compress2` :147、`uncompress` :181）、`ob_expr_compress.cpp`（`compressBound`、`compress`、`uncompress`，`__APPLE__`/`_WIN32` 两组分支 :59-218）、`ob_expr_crc32.cpp:56`（`crc32`）、`ob_load_data_file_reader.cpp`（`inflateInit2` :616 等）、`ob_select_into_basic.cpp`（`deflateInit2` :258 等）。src 里没有调用 `zlibVersion` | `zalloc`/`zfree`：`ob_zlib_alloc`/`ob_zlib_free`（`ob_compress_util.h:94-110`） |
| `icu69_sys` | ICU 69.1：el8 `devdeps-icu-69.1`（`:25`），macOS 15（`:15`），Android（`:10`），Windows vcpkg | `libicui18n.a`、`libicustubdata.a`（只有 `icudt69_dat`）、`libicuuc.a`，全路径静态（`:319-321`）；Windows 用 vcpkg 的 `icuin/icudt/icuuc.lib`（`:297-299`，完整数据） | `ob_expr_regexp_context.cpp` 15 个函数：`uregex_open` :251、`uregex_setStackLimit` :252、`uregex_setTimeLimit` :253、`uregex_close` :50,256、`uregex_setText`、`uregex_find`、`uregex_findNext`、`uregex_start`、`uregex_end`、`uregex_getText` :466、`uregex_appendReplacement` :506,525、`uregex_appendTail` :550,564、`u_strFromUTF8` :88,102、`u_strToUTF8` :159,172、`u_errorName` :598,607,612。符号带 `_69` 后缀来自 `urename.h`（如 `:1516`）与 `uvernum.h:89,128-130` | 无 |
| `libxml2_sys` | libxml2：el8/el7/el9/al8 **2.13.6**（`el8.x86_64.deps:19`），macOS 与 Android **2.10.4**（`macos15.arm64.deps:17`、`android.arm64.deps:12`），Windows vcpkg；外加 xz 5.2.2 的 liblzma | `libxml2.a` + `liblzma.a`（`src/oblib/lib/CMakeLists.txt:49-52`），zlib 和 macOS 的 `-liconv` | `ob_libxml2_sax_handler.cpp` 15 个函数（`xmlInitParser` :108、`xmlCleanupParser` :118、`xmlNewParserCtxt` :345、`xmlParserInputBufferCreateMem` :347、`xmlParserInputBufferGrow` :358、`xmlNewIOInputStream` :365、`xmlPushInput` :369、`xmlCtxtUseOptions` :419、`xmlParseDocument` :466、`xmlParseContent` :490 等），直接读写 `xmlParserCtxt` 的 `sax`、`input->{base,cur,consumed}`、`instate`、`disableSAX`、`_private`、`recovery`、`str_xml*` 字段；`ob_xml_parser.cpp:576-585` 经 `IS_LETTER` 等宏用到 `xmlCharInRange` 和 4 张 `xmlIs*Group` 数据 | 11 个 SAX 回调（`get_sax_handler`，`:27-64`），语法检查模式只有 `serror` 1 个（`:66-103`）；`structured_error` 的签名在 macOS/Android 是 `xmlError*`、其余平台 `const xmlError*`（`ob_libxml2_sax_handler.h:41-45`） |
| `openssl_sys` | OpenSSL 1.1.1u：el8 `devdeps-openssl-static-1.1.1u`（`:18`），macOS 15（`:18`），Android（`:14`），Windows `deps/3rd/openssl`；al8 是另一个构建号 | `libssl.a`、`libcrypto.a` 全路径静态（`:311-312`），macOS 加 `-framework Security`（`:327`） | libcrypto 63 个函数，8 个文件：`ob_encryption_util.cpp`（AES 的 ecb/cbc/cfb1/cfb8/cfb128/ofb/gcm :30-67，加解密 :151-279，摘要 :353-386,450-463 含 `EVP_sm3`）、`ob_expr_encrypt.cpp`（`EVP_BytesToKey` :207,257,406,498，`DES_set_key_unchecked`、`DES_ede3_cbc_encrypt` :290,421,511，`DES_fcrypt` :627,712）、`ob_expr_random_bytes.cpp:66,95`（`RAND_bytes`）、`ob_telemetry.cpp:375,496`（`HMAC`）、`ob_grpc_context.cpp:61-90` 与 `utility.cpp:2343-2382`（证书到期：BIO/PEM/X509，直接读 `ASN1_TIME` 和 `X509_INFO` 的字段）、`ob_expr_md5.cpp:88`、`ob_sql_utils.cpp:149`（`MD5`）。SM4 的分支在 `#ifdef OB_USE_BABASSL` 下，没有任何构建定义它。libssl 无直接调用者（只经 libcurl） | `CRYPTO_set_mem_functions(ob_malloc_openssl, …)`（`ob_encryption_util.cpp:281-317`，`main.cpp:729` 调用；三个钩子只设内存标签再调 libc） |
| `curl_sys` | libcurl 8.12.1：el8 `devdeps-libcurl-static-8.12.1`（`:14`），macOS 15（`:16`）；**Android 是 8.2.1**（`android.arm64.deps:11`）；Windows vcpkg | `libcurl.a` 全路径静态（`:310`），依赖同一份 `libssl.a`/`libcrypto.a`（`libcurl.pc:36-39`），macOS 三个 framework；不带 zlib、nghttp2、c-ares、zstd | 16 个函数，4 个文件：`main.cpp:751-752,827`（`curl_global_init(CURL_GLOBAL_ALL)`/`cleanup`）、`ob_telemetry.cpp:1387-1437`、`ob_ai_func_client.cpp`（easy + multi，`setopt` :401-420，错误映射 :107-128）、`ob_vector_embedding_handler.cpp`（multi，:724-859,1565-1595）；15 个 `CURLOPT_*` | 3 个 `CURLOPT_WRITEFUNCTION`：`discard`（`ob_telemetry.cpp:1383-1385`）、`ObAIFuncClient::write_callback`（`.cpp:452-467`）、`ObEmbeddingTask::WriteMemoryCallback`（`ob_vector_embedding_handler.h:288-305`） |
| `libaio_sys` | libaio 0.3.112，只有 Linux（`el8.x86_64.deps:16`）；macOS、Android、Windows 不链接 | `-laio` 动态（`:216-220,324`；Ubuntu 24.04+ 用 `libaio.so.1t64`，`:252-275`、`cmake/Env.cmake:435-470`） | 5 个函数 `io_setup`、`io_destroy`、`io_submit`、`io_cancel`、`io_getevents`，加头文件里的内联 `io_prep_pread/pwrite`：`ob_local_device.cpp:964-1191`、`ob_file.cpp:1390-1631`（`#ifdef __USE_AIO_FILE`）。非 Linux 用 `ob_file.h:41-67` 的桩和 `ob_local_device.cpp:62-209` 的用户态模拟（这两处是普通翻译单元） | 无（`iocb.data` 存的是 OB 的对象指针） |
| `seekdb-jemalloc-sys`（已有） | jemalloc 5.3.1，crates.io `seekdb-jemalloc-sys =0.2.2`，features `["stats"]`（`deps/external/Cargo.toml:10`） | CMake 经 `deps/external` 的 Cargo 工程构建：`JEMALLOC_SYS_CONFIGURE_ARGS=--with-jemalloc-prefix=je_`、`CFLAGS=-O2 -fPIC`（macOS 加 `-arch`、`-mmacosx-version-min`）、`CC` 为基线 clang（`deps/external/cmake/Jemalloc.cmake:10-42`）；macOS 加 `-Wl,-u,_je_zone_register`（`src/observer/CMakeLists.txt:112-116`） | 由 01 封装（01 文档 §2.8）；本模块只规定构建配置（§2.3.8） | — |

编译器与参数的出处（agent 读 CMake 的结果，已抽查）：`-march`/`-mtune` 在 `cmake/Env.cmake:407-433`（arm64 `-march=armv8-a+crc+lse -mtune=generic`；x86-64 **没有** `-march`，只有 `-mtune=core2`）；公共参数在 `src/oblib/CMakeLists.txt:114-136`；`-std=gnu++20` 在 `Env.cmake:125`；`PACKAGE_*` 在 `src/CMakeLists.txt:30-38`；Linux 另有 `--gcc-toolchain=<devtools>`、`-gdwarf-4`（`Env.cmake:367-372`），链接用 `ld.lld`、`-static-libgcc -static-libstdc++`（`src/observer/CMakeLists.txt:101-102`）。C++ 编译单元全部 `-std=gnu++20`，没有 `-fno-exceptions`/`-fno-rtti`（异常和 RTTI 都开着；`-D_NO_EXCEPTION` 只是个宏）。

### 1.6 C++ shim 与 `keep-cxx` 单元

| shim | 包住的库 | 移进 shim 的 C++（处置 `keep-cxx:<shim>`，除非另注） | 规模与事实 |
|---|---|---|---|
| `bgk_shim` | Boost.Geometry 1.74（`deps/3rd/…/include/boost/version.hpp:22,30`，纯头文件） | `src/share/geo/ob_geo_bin_traits.h`（405 行，manifest 324）、`ob_geo_tree_traits.h`（384 行，325）、`ob_srs_wkt_parser.{h,cpp}`（Spirit，397 行，3194）；`ob_boost_geometry_exception_handle`（`ob_geo_dispatcher.h:1314-1366`）整段复制。另外复制、但原单元仍是 `translate` 的（见 §8.1 Q2）：`ob_geo_bin.h`（770）、`ob_geo_bin_iter.{h,ipp}`、`ob_geo_tree.{h,cpp}`（1,306 + 453）、`ob_geo_wkb_visitor.{h,cpp}`（WKB 写出）、`ob_geo_to_tree_visitor.{h,cpp}`、`ob_srs_info.h` 里 SRS 参数的部分 | 39 个文件用到 Boost（38 个在 `src/share/geo`，加 `src/sql/engine/expr/ob_expr_st_buffer.cpp`），6 个直接 include Boost 头文件。`ObGeoFuncType` 22 种（`ob_geo_func_register.h:53-76`）。38 个 `ob_geo_func_*.cpp` 共 10,867 行，716 个按类型对特化的入口（`OB_GEO_CART_BINARY_FUNC_BEGIN` 245、`_GEOG_BINARY_` 288、`_CART_TREE_` 53、`_GEOG_TREE_` 38、`_UNARY_` 79、`_UNARY_TREE_` 13；宏在 `ob_geo_dispatcher.h:1434-1499`）。`bg::` 调用约 319 处：`covered_by` 70、`relate` 63、`difference` 41、`buffer` 33、`within` 23、`correct` 14、`intersects` 11、`disjoint` 10、`intersection` 8、`sym_difference` 7、`equals` 6、`union_` 5、`touches` 5 等。`ob_geo_func_transform.cpp:21` 用 `boost/geometry/srs/transformation.hpp`，`ob_geo_rstar_tree.h:22` 用 R 树 |
| `s2_shim` | s2geometry 0.10.0 + abseil 20211102.0（`el8.x86_64.deps:23-24`、`macos15.arm64.deps:12,22`） | `src/share/geo/ob_s2adapter.{h,cpp}`（176 + 368 行，manifest 353）、`ob_geo_to_s2_visitor.{h,cpp}`（351） | 链接 `libs2.a` 和 abseil 列表（`src/oblib/CMakeLists.txt:313-314`）。入口是 `ObS2Adapter` 的方法：`init(wkb, bound)`、`get_cellids`、`get_cellids_and_unrepeated_ancestors`、`get_inner_cover_cellids`、`get_mbr`、`get_ancestors`、`get_child_of_cellid`（`ob_s2adapter.h:156-162`）；s2 的 `_fp_contract_off.h` 有 `#pragma STDC FP_CONTRACT OFF` |
| `vsag_shim` | vsag 1.1.0（连带 diskann、openblas、antlr4、fmt、cpuinfo、gfortran、gomp/omp） | `src/oblib/lib/vector/ob_vsag_adaptor.{h,cpp}`（166 + 1,417，manifest 891）、`ob_vector_util.{h,cpp}`（151 + 257，892）；另把 `src/query/api/query/vector/ob_vector_index_serialize.h:30-94` 的三个 `std::streambuf` 子类 `ObStreamBuf`、`ObOStreamBuf`、`ObIStreamBuf`（用 `setp`/`setg` 管缓冲，`fserialize`/`fdeserialize` 经 `std::ostream`/`istream` 用它们，`src/observer/vector_index/ob_plugin_vector_index_serialize.cpp:193-197`）按类级重映射移进 shim，该单元其余部分照常翻译 | `obvsag::` 门面约 25 个函数（`ob_vector_util.h:57-147`：`create_index`、`build_index`、`add_index`、`knn_search`×3、`fserialize(std::ostream&)`、`fdeserialize(std::istream&)`、`cal_distance_by_id` 等）；分配器 `ObVsagMemContext : vsag::Allocator`（`src/storage/allocator/ob_vector_allocator.h:71-118`，`set_parallel(8)` 在 `.cpp:111-121`，会从多个线程回调）；链接列表按平台在 `src/oblib/lib/CMakeLists.txt:55-125` |
| `regex_shim` | 平台的 `<regex>`（macOS 是 MacOSX15.sdk 的 libc++ 19.1.2，Linux 是 devtools 里 gcc 12.3 的 libstdc++），以及 POSIX `regcomp/regexec` | 无单元移入（调用点都是普通翻译单元） | `std::regex` 的用户可见调用：`ob_json_schema.cpp:4005-4042`（`pattern` 校验与匹配，主体按 C 字符串传入）、`ob_array_cast.cpp:557-561`（`regex_replace`）、`ob_collection_type.cpp:538-540,610-612,649-652`（含 `icase`）；内部：`ob_log_compressor.cpp:231-243,320-330`、`ob_log.cpp:1232-1298`、`ob_server_log_block_mgr.cpp:388-454`；POSIX ERE：`ob_json.cpp:716,737`、`ob_log_compressor.cpp:85-88` 等；`ob_expr_ai_prompt.cpp:19` 只 include |

**与现有 `units.tsv` 的差异**（1c 审查时按本节改）：
- 现为 `translate`（REVIEW），本节定为 `keep-cxx`：`src/share/geo/ob_srs_wkt_parser.h` → `keep-cxx:bgk_shim`（RULEBOOK 0.3 的 shim 一行点名了 Spirit SRS 解析器）；`ob_s2adapter.h`、`ob_geo_to_s2_visitor.h` → `keep-cxx:s2_shim`；`src/oblib/lib/vector/ob_vsag_adaptor.h`、`ob_vector_util.h` → `keep-cxx:vsag_shim`。
- 现为 `keep-cxx:bgk_shim` 的 `ob_geo_tree.h`，以及现为 `translate`（REVIEW）的 `ob_geo_bin.h`、`ob_geo_bin_iter.h`：等 §8.1 Q2 决定。
- 现为 `translate`（REVIEW）、本节同意维持 `translate` 的：`src/query/api/query/vector/ob_vector_index_adaptor.h`（Rust 侧的调用方）；`src/storage/allocator/ob_vector_allocator.h`（`ObVsagMemContext` 在 Rust 侧实现 `vsag_shim::Allocator`，§2.4.3）；`std::regex` 的 4 个调用单元；`ob_geo_func_*`、`ob_geo_utils.h`（编排部分，经 `bgk_shim` 的入口调用 Boost）。

### 1.7 生成器

| 生成器（Rust 落点） | 输入 | C++ 现状 | 单元与处置 |
|---|---|---|---|
| errno（`rust/port/oblib/build.rs`） | `src/share/ob_errno.def`（1,914 行，1,548 条 `DEFINE_*`：858 `DEFINE_ERROR`、326 `_EXT`、172 `_DEP`、190 `_EXT_DEP`、2 `DEFINE_OTHER_MSG_FMT`）、`src/share/mysql_errno.h`（1,044 行，`ER_*` 符号） | `src/share/gen_errno.pl`（467 行）手工运行，产物入库：`share/ob_errno.h`（2,792）、`share/ob_errno.cpp`（15,636）、`oblib/lib/ob_errno.h`（396） | 04 已列（04 文档 §1.1 第 1、2 行）：`generate:errno` |
| `ObItemType`（`rust/port/query/build.rs`） | query 那份 `ob_item_type.h`；objit 那份只做比对 | 头文件本身就是源头；`gen_type_name.sh` 生成名字表 | §1.4 |
| 系统变量（`rust/port/share/build.rs` 写元数据，`rust/port/sql/build.rs` 写工厂） | `src/share/system_variable/ob_system_variable_init.json`（10,979 行，776 个变量） | `gen_ob_sys_variables.py`（1,200 行）写 8 个文件，产物入库：share 6 个（`ob_system_variable_init.{h,cpp}`、`ob_system_variable_alias.h`、`ob_sys_var_class_type.h`、`ob_sys_var_meta.{h,cpp}`），sql 2 个（`src/sql/session/ob_system_variable_factory.{h,cpp}`，5,235 + 10,705 行）（`gen_ob_sys_variables.py:1170-1200`） | 8 个文件在 `depmap/excluded.tsv` 标为 generated，1c 补进 `units.tsv`，处置 `generate:sysvar` |
| proto → prost/tonic（`rust/port/standby/build.rs`、`rust/port/share/build.rs`） | `src/oblib/grpc/standbyservice.proto`（135 行，`package standbyservice`，proto3，10 个 RPC，其中 6 个服务端流：`:6-24`）；`src/share/geo/ob_vector_tile.proto`（79 行，`package vector_tile`，proto2） | 前者用 protoc + grpc_cpp_plugin 生成 4 个文件入库（Windows 配置阶段重新生成，`src/oblib/grpc/CMakeLists.txt:5-45`）；后者用 protoc-c 生成 `ob_vector_tile.pb-c.{c,h}`（432 + 219 行），由 `ob_geo_mvt.cpp`（ST_AsMVT）使用 | 生成物 `generate:proto`；protobuf-c 不再链接（RULEBOOK 0.14） |
| 版本号（`rust/port/share/build.rs`） | `CMakeLists.txt:10`（`VERSION 1.4.0.0`）、`cmake/Env.cmake:26`（`BUILD_NUMBER 1`）、git | `configure_file(../share/ob_version.cpp.in ob_version.cpp)`（`src/observer/CMakeLists.txt:149`），变量来自 `:118-147` | `src/share/ob_version.h`（manifest 1119）照常翻译，函数体改读 `env!` |
| 内部表（Python DSL + 新的 Rust 发射器；`share`、`observer` 的 build.rs 调用） | `src/share/inner_table/ob_inner_table_schema_def.py`（14,510 行）、`ob_inner_table_init_data.py`（51 行） | `generate_inner_table_schema.py`（3,417 行）在配置阶段按 sha256 生成 54 个文件、104,889 行（`CMakeLists.txt:31-75`）：`share/inner_table/ob_inner_table_schema.<id 段>.cpp` 等，`ob_inner_table_schema{.h,_constants.h,_misc.ipp,.lob.cpp}`、`table_id_to_name`，以及 `observer/virtual_table/ob_all_virtual_sqlite_tables.{h,cpp}` | 产物不入库，不是单元；`ADD_COLUMN_SCHEMA*` 8 个宏（`src/share/schema/ob_schema_macro_define.h:25-290`）所在单元照常翻译，提供通用构建函数（§2.5.6） |
| bitpacking（`rust/port/oblib/build.rs`） | 位宽参数（生成器内写死） | `src/oblib/lib/codec/generate_scalar_bitpakcing.py`（384）、`generate_simd_bitpakcing.py`（782），产物入库：`ob_generated_scalar_bp_func.{h,cpp}`（51 + 11,594）、`ob_generated_unalign_simd_bp_func.{h,cpp}`（56 + 12,705），合计 24,406 行；SIMD 文件 7,200 行含 `_mm_`（`loadu`/`storeu` 各 1,898，`or` 1,624，`srli/slli_epi32` 各 1,328，`and` 1,120，`srli/slli_epi16` 各 296）；ARM 经 `ob_sse_to_neon.h`（85 行，`:33-79`）映射 | 4 个文件在 `excluded.tsv` 标为 generated；1c 补进 `units.tsv`，处置 `generate:bitpacking` |

### 1.8 被删除的 4 个生成器（RULEBOOK 0.13）

| 生成器 | 产物 | 去向 |
|---|---|---|
| `src/share/datum/ob_str_datum_funcs_compilation/gen_str_datum_func_parts.py`（165 行） | `ob_str_datum_funcs_compilation_{0,1,2}.cpp`、`ob_str_datum_funcs_all.cpp`、`ob_str_datum_funcs_compilation.ipp`（入库） | 只为把模板实例化拆到多个编译单元。5 个产物在 `migration/depmap/excluded.tsv` 都记为 generated，不是单元，不需要处置。有用的信息只有两样：排序规则清单（`:6-10` 的 `DEFINED_COLLS`，3 个：`CS_TYPE_BINARY`、`CS_TYPE_UTF8MB4_GENERAL_CI`、`CS_TYPE_UTF8MB4_BIN`），和每个排序规则的 3 步初始化（`.ipp:32-38` 的 `DEF_STR_FUNC_INIT`）。调用方 `src/share/datum/ob_datum_funcs.cpp:164,169`（`__init_all_str_funcs()`）。替代见 §2.5.7 |
| `src/sql/engine/expr/ob_expr_str_cmp_func_compile/gen_expr_str_cmp_func.py`（325 行） | `ob_expr_str_cmp_func_part_{0..7}.cpp`、`ob_expr_str_cmp_func_all.cpp`（入库，`:249-257`） | 同上：9 个产物记为 generated，不是单元。`ob_expr_str_cmp_func_common.ipp` 不是产物（产物只 include 它，`:210`），是手写的单元，照常 `translate`（`:24` 的 `INIT_COMPILE_STR_FUNC(C)` 即 `StrCmpFuncIniter<C>::init_array()`）。清单是 `:12` 起的 `DEFINED_COLLS`，167 个，按 8 份切开（part_0–6 各 21 个，part_7 20 个）。调用方 `src/sql/engine/expr/ob_expr_cmp_func.cpp:403,409`（`__init_all_str_expr_cmp_func()`） |
| `src/sql/parser/gen_type_name.sh`（18 行） | `type_name.c`（构建时生成，不入库） | 名字表由 `ObItemType` 生成器产出（§2.5.2）。注意它的取名规则：sed 正则 `\s*(T_[A-Z0-9_]+)[ =0-9]*,`（`:17`）匹配不到 `T_INVALID = -1`（有负号）和最后一个没有逗号的 `T_MAX`，所以 `get_type_name(-1)` 与 `get_type_name(T_MAX)` 返回 `"Unknown"` |
| `src/share/inner_table/sys_package/syspack_codegen.py`（176 行） | `syspack_source.cpp`（构建时生成，`src/pl/CMakeLists.txt:26-48`）：13 个包的清单（顺序固定）、26 个 `.sql` 的原文（`R"sys_pack_del(…)"`，内容原样写入，`:123-128`） | 消费者 `src/pl/ob_pl_package_manager.cpp:320-341,417,456`。改成手写的 Rust 表（同顺序）加 `include_str!`（文件不是合法 UTF-8 时编译即失败，届时改 `include_bytes!`），放在 `rust/port/sql/src/generated/syspack.rs`，由一次性脚本从 `syspack_codegen.py` 的清单生成 |

### 1.9 数据与代码拆分（`*_data`）

| 数据 | 事实 | 单元处置；数据的去向与编码（§2.5.8） |
|---|---|---|
| `src/storage/fts/dict/ob_ik_dic.cpp` | 276,043 行；三个函数内的 `static const char *xxx[]`：主词典（`:29` 起）、量词（`:275948`）、停用词（`:276011`），由 `ObIKDictLoader` 按顺序读 | 单元 `ob_ik_dic.h`（含 `.cpp`）`translate`（RULEBOOK 0.13：代码部分照常翻译）；三张表由脚本 `dict_blob` 写进 `storage_data`，E2（NUL 分隔的 `include_bytes!` 加条数），译文 `pub use storage_data::…` |
| `src/oblib/common/timezone/ob_timezone_info.cpp` | 39,989 行；`TIME_ZONE_NAMES[]`（`:26`，元素 `{ObString, int32×3}`）、`TIME_ZONE_TRANS[]`（`:604`，元素 `{int64, int32, int32}`），类型在 `ob_timezone_info.h:220-233` | 单元 `ob_timezone_info.h` `translate`；两张表由脚本 `tz_table` 写进 `oblib_data`，E1（宏表） |
| 字符集表 | `src/oblib/lib/charset/ob_ctype_utf8_tab.h`（4,472 行：约 30 张 `ObUnicaseInfoChar` 表、2 张 `uchar` 表，按行首的数组定义计数）、`ob_ctype_bin_tab.h`（55 行：`ctype_bin[]` :16、`bin_char_array[]` :37），1c 已列进 `units.tsv`。`ob_ctype_uca.cc`（7,129 行）不在构建里：它 include 的 `lib/charset/ob_ctype_uca_tab.h`（`:24`）不存在，`migration/depmap/excluded.tsv:11` 记为 unbuilt，不是单元。`ob_ctype_simple.cc` 只有 `d10[]`（`:36`）一张小表，随单元照常翻译 | `data:charset_table` → `oblib_data`，E1（结构体表）/E3（`uchar` 表） |
| `src/oblib/lib/charset/ob_uctype.h` | 1,481 行，由 UnicodeData-5.0.0d9 生成，生成器不在仓库里（`depmap/excluded.tsv`） | `data:uctype` → `oblib_data`：60 张 `uctype_page*`（`unsigned char[256]`）用 E3；`ob_uni_ctype[256]`（`:1223`，元素指向这些页）用 E1 |
| 16 个 `.map` | 共 16,875 行，都在 sql：`src/sql/engine/expr/ob_expr_{abs_result_type,arithmetic_result_type,compatiable_promotion,div_promotion,div_result_type,int_div_result_type,merge_result_type,mod_result_type,neg_result_type,nvl_promotion,relational_cmp_type,relational_equal_type,relational_result_type,round_result_type,sum_result_type}.map`、`src/sql/optimizer/ob_join_property.map`；元素类型是 `ObObjType`、`ObObjTypeClass`、`bool` 的一维或二维数组；`ob_expr_compatiable_promotion.map` 和 `ob_join_property.map` 的数组没有 `const` | `data:map_table` → `sql_data`，E1；非 const 的表若有写入点，由 1c 标出，否则按只读 `static` |
| 4 个 `.def` | `ob_errno.def` 是生成器输入；`src/share/ob_lib_cache_namespace.def` 是类型名的 X-macro（`src/sql/plan_cache/ob_lib_cache_register.cpp:98-116` 包含 3 次）；`src/sql/resolver/cmd/ob_sql_category.def`、`ob_sql_topic.def` 没有任何包含点（只出现在 `sql_header_inventory.bzl`） | 都不进 `*_data`：`generate:errno`；X-macro 按 §2.13 译成 `lib_cache_obj_def!`；两张 HELP 表 `delete`（第 16 类确认）。`units.tsv` 现按 RULEBOOK 字面把这两张记为 `data:def_table`，等 §8.2 A5 决定 |
| `src/sql/engine/expr/ob_expr_to_pinyin_tab.cpp`（不在 RULEBOOK 0.13 的范围里） | 1,528 行；`PinyinPair PINYIN_TABLE[PINYIN_COUNT]`（`:23`，1,502 条，没有 `const`），元素 `{uint64_t begin, uint64_t end, ObString pinyin}`（`ob_expr_to_pinyin_tab.h:27-33`），内容取自 CLDR 33 的 `zh.xml`（`.h:26`）；唯一的读者是 `ob_expr_to_pinyin.cpp:97`（`std::lower_bound`），没有写入点。`units.tsv` 现记 `translate`（依据 `R-data-candidate-REVIEW`） | 见 §8.1 Q7 |

### 1.10 相关但不属本模块

- `rusqlite`/`libsqlite3-sys`：不开 `bundled`，链接 `deps/3rd/…/lib/sqlite/libsqlite3.a`（`src/oblib/CMakeLists.txt:316`），用 `SQLITE3_LIB_DIR`/`SQLITE3_STATIC=1` 指定；写进版本表（§2.7）。
- 解析器的消费方（205 个文件）、`ObZstdWrapper`、`ObLibXml2SaxParser`、`ObExprRegexpContext`、`ObEncryptionUtil`、`ObAIFuncClient`、`ObLocalDevice`、`ob_geo_func_*.cpp` 的编排部分：普通翻译单元，按本文的 API 写。
- libunwind → `backtrace`、CRoaring → `roaring`、RapidJSON 移植、xxHash → `xxhash-rust`、fast_float：各自的模块。

## 2. Rust API

### 2.0 crate 一览

| crate | 路径 | 直接依赖 | `links` | unsafe |
|---|---|---|---|---|
| `parser` | `rust/port/parser` | `oblib`、`query` | `seekdb_ob_parser_c` | 整个 crate 在白名单（§3 第 12 项）；根上 `#![deny(unsafe_code)]`，只在 `sys`、`node`、`arena`、`result`、`pl`、`fts`、`owned`、`api`、`callbacks`、`layout`、`simd` 模块上 `#[allow(unsafe_code)]` |
| `zstd138_sys` | `rust/port/zstd138_sys` | `oblib`（`ForeignBlock`） | `seekdb_zstd138` | 白名单（第 13 项） |
| `zlib1213_sys` | `rust/port/zlib1213_sys` | `oblib` | `seekdb_z` | 同上 |
| `icu69_sys` | `rust/port/icu69_sys` | — | `seekdb_icu69` | 同上 |
| `libxml2_sys` | `rust/port/libxml2_sys` | `zlib1213_sys` | `seekdb_xml2` | 同上 |
| `openssl_sys` | `rust/port/openssl_sys` | — | `seekdb_openssl` | 同上 |
| `curl_sys` | `rust/port/curl_sys` | `openssl_sys` | `seekdb_curl` | 同上 |
| `libaio_sys` | `rust/port/libaio_sys`（只在 Linux 有内容） | — | `seekdb_aio` | 同上 |
| `bgk_shim` | `rust/port/bgk_shim` | `cxx` | — | 同上；Rust 侧只有 cxx 生成的 extern 块 |
| `s2_shim` | `rust/port/s2_shim` | `cxx` | — | 同上 |
| `vsag_shim` | `rust/port/vsag_shim` | `cxx`、`oblib` | — | 同上 |
| `regex_shim` | `rust/port/regex_shim` | `cxx` | — | 同上 |

- `links` 的值故意不用 `ssl`、`curl`、`z`，避免与 crates.io 上同名 -sys 包的 `links` 冲突（Cargo 不允许两个包声明同一个 `links`）。
- 名字：C 库的类型、函数、常量照抄 C 名；安全层把借用限定在一次调用里的辅助函数一律用 `with_<C 名>`，把若干 C 调用合成一步的用 `<C 名>_<后缀>`；本模块新引入、不来自 C/C++ 的名字只有 `ParserHost`、`PlSymbolLookup` 和 shim 桥接里的 `SrsParams`，已排队加进 §4 改名表（§8.2 A8）。
- 各 -sys 和 shim 的依赖方向：只依赖 `oblib` 或更低的 crate；`oblib` 本身不依赖它们（例外：`oblib` 的 errno build.rs 与 `bgk_shim` 的 build.rs 共用 `ob_errno.def` 的解析代码，用 `include!` 共享源文件，不是 crate 依赖）。

### 2.1 构建公共部分：`rust/port/build_support/cc_baseline.rs`

不是 crate；各 build.rs 用 `include!("../build_support/cc_baseline.rs");` 引入，避免改 crate 图。

```rust
// rust/port/build_support/cc_baseline.rs
use std::path::{Path, PathBuf};

pub enum Lang { C, Cxx }

/// 仓库根、deps 目录。以 CARGO_MANIFEST_DIR 的上三级为仓库根（rust/port/<crate>）。
pub fn repo_root() -> PathBuf { todo!() }
/// deps/3rd/usr/local/oceanbase/deps/devel（CMake 的 DEP_DIR，cmake/Env.cmake:15）
pub fn dep_dir() -> PathBuf { todo!() }
/// deps/3rd/usr/local/oceanbase/devtools/bin
pub fn devtools_bin() -> PathBuf { todo!() }

/// 必须真编译、真链接 C/C++ 的目标：aarch64-apple-darwin、x86_64-unknown-linux-gnu（RULEBOOK D18），
/// 或环境变量 SEEKDB_NATIVE_BUILD=1。其余目标（x86_64-apple-darwin、aarch64-unknown-linux-gnu、
/// x86_64-pc-windows-{msvc,gnu}、aarch64-linux-android）只做 cargo check：build.rs 跳过 C/C++ 编译，
/// 只产出 Rust 侧的生成物。
pub fn native_build_required() -> bool { todo!() }

/// 与基线 compile_commands.json 一致的编译器和参数（§3.11），外加 -ffp-contract=off（D17 (b)）。
/// 内部调用 cc::Build::no_default_flags(true)，不让 cc 按 Cargo 的 profile 改 -O 级别。
/// C++ 不让 cc 自动链 C++ 运行时（cpp_link_stdlib(None)），运行时由主程序统一链接（§3.11.4）。
pub fn cc_build(lang: Lang) -> cc::Build { todo!() }

/// 公共 -D（不含 PACKAGE_*，那组只有部分编译单元带，见 §3.11.2）
pub fn common_defines() -> Vec<(&'static str, Option<&'static str>)> { todo!() }

/// 只在内容变化时写文件：写临时文件再 rename，并发的两次构建不会写出半个文件。
pub fn write_if_changed(path: &Path, bytes: &[u8]) { todo!() }
/// 对目录下给定扩展名的文件逐个打印 cargo:rerun-if-changed。
pub fn rerun_if_changed_tree(dir: &Path, exts: &[&str]) { todo!() }
/// 生成物文件头（§2.6.2）：生成器名、输入路径及 sha256（本文件自带约 100 行的 SHA-256 实现，不引入表外 crate）。
pub fn generated_header(generator: &str, inputs: &[&Path]) -> String { todo!() }
```

### 2.2 `parser` crate

#### 2.2.1 模块结构

```rust
// rust/port/parser/src/lib.rs
#![deny(unsafe_code)]
#![deny(unsafe_op_in_unsafe_fn)]
#![warn(clippy::undocumented_unsafe_blocks)]

#[allow(unsafe_code)] pub mod sys;        // bindgen 产物按目标选择（§2.2.2）
#[allow(unsafe_code)] mod arena;          // ParseArena
#[allow(unsafe_code)] mod node;           // ParseNode、LocalParseNode
#[allow(unsafe_code)] mod owned;          // OwnedParseTree
#[allow(unsafe_code)] mod result;         // ParseResult、ObQuestionMarkCtx
#[allow(unsafe_code)] mod pl;             // ObParseCtx（PL）
#[allow(unsafe_code)] mod fts;            // FtsParserResult、FtsNode
#[allow(unsafe_code)] mod api;            // 23 个入口的安全封装（§2.2.9）
#[allow(unsafe_code)] mod callbacks;      // C 调用的 extern "C" 蹦床（§2.2.10）
#[allow(unsafe_code)] mod layout;         // parse_node_layout 自检（§2.2.11）
#[allow(unsafe_code)] mod simd;           // ob_parse_binary_simd（x86-64）
mod define;                               // OB_PARSER_ERR_*
mod host;                                 // ParserHost、PlSymbolLookup、PARSER_HOST_SLOT
mod reentry;                              // 线程局部的重入计数
mod print;                                // ObToString

pub use arena::ParseArena;
pub use node::{ParseNode, LocalParseNode};
pub use owned::OwnedParseTree;
pub use result::{ParseResult, ObQuestionMarkCtx};
pub use pl::ObParseCtx;
pub use fts::{FtsParserResult, FtsNode};
pub use api::*;
pub use define::*;
pub use host::{ParserHost, PlSymbolLookup, PARSER_HOST_SLOT};
pub use layout::check_parse_node_layout;
pub use print::databuff_print_stmt_location;
/// 原来 include parse_node.h 的单元也拿得到这些名字（parse_node.h:29,32 把 ob_item_type.h 带进来）
pub use query::item_type::{ObItemType, get_type_name};
pub use sys::{ObStmtLoc, ParseMode, SelectParserOffset, FtsError, FtsNodeType, FtsOperType};
```

#### 2.2.2 `sys`：离线 bindgen

```rust
// rust/port/parser/src/sys.rs
// 每个目标一份，文件头记录 bindgen 版本、clang 版本、target、全部 clang 参数（§3.1.3）。
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]  include!("generated/sys_aarch64_apple_darwin.rs");
#[cfg(all(target_os = "macos", target_arch = "x86_64"))]   include!("generated/sys_x86_64_apple_darwin.rs");
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]   include!("generated/sys_x86_64_unknown_linux_gnu.rs");
#[cfg(all(target_os = "linux", target_arch = "aarch64"))]  include!("generated/sys_aarch64_unknown_linux_gnu.rs");
#[cfg(all(target_os = "android", target_arch = "aarch64"))] include!("generated/sys_aarch64_linux_android.rs");
#[cfg(all(target_os = "windows", target_arch = "x86_64"))] include!("generated/sys_x86_64_pc_windows.rs");

/// bindgen 里 blocklist 了 C 的 enum ObItemType，字段类型改用 query 的新类型：
/// #[repr(transparent)] i32，任何 i32 都合法（RULEBOOK §2.12、S-05），与 C 的 int 大小枚举布局相同。
pub use query::item_type::ObItemType;

/// C 侧从不解引用的字符集指针（§1.3.3）。Rust 侧装进去的是 ObCollationType 的值（§3.1.6），不是地址。
#[repr(C)] pub struct ObCharsetInfo { _opaque: [u8; 0] }
```

生成方式（脚本 `rust/port/parser/bindgen/regen.sh <target>`，开发者手工运行，产物入库）：
- 包装头 `rust/port/parser/bindgen/wrapper.h`：include `query/parser/parse_node.h`、`sql/pl/parser/parse_stmt_node.h`、`sql/pl/parser/parse_stmt_item_type.h`、`sql/parser/fts_base.h`、`sql/parser/parse_malloc.h`、`sql/parser/ob_non_reserved_keywords.h`。
- allowlist：上面头文件里的结构、枚举，23 个入口加 `parsenode_hash`、`parse_terminate`；blocklist `ObItemType`、`ObCharsetInfo`；`--no-layout-tests` 关闭（布局测试保留为 `#[test]`）；枚举一律 `--newtype-enum`（§2.12）。
- clang 参数：`rust/port/parser/bindgen/flags.tsv` 每个目标一行（`target<TAB>clang_args`），内容取自该平台基线 `compile_commands.json` 里 `parse_node.c` 的 `-D`、`-I`、`--target`、`-isysroot`；macOS arm64 一行就是 §1.3.3 的列表。build.rs 用同一行的 `-D` 编译 C 并校验（§3.1.3）。

#### 2.2.3 `ParseArena`

```rust
use core::ffi::c_void;
use oblib::mem::Arena;
use oblib::error::ObResult;

/// C++：解析用的 ObIAllocator（ParseResult::malloc_pool_，parse_node.h:300）。
/// 就是请求的 Arena，透明包装后地址可以当 void* 交给 C；C 的回调再把它转回 &ParseArena（§2.2.10）。
#[repr(transparent)]
pub struct ParseArena(Arena);

impl ParseArena {
    pub fn from_arena(arena: &Arena) -> &ParseArena { todo!() }
    pub fn arena(&self) -> &Arena { todo!() }

    // ---- parse_malloc.cpp 里 C++ 也直接调用的函数（ob_parser.cpp:976,988,1096 等）----
    /// parse_malloc（parse_malloc.cpp:90-105）：nbyte == 0 → None；
    /// 否则分配 8 + nbyte 字节、头里写 nbyte、载荷清零，返回载荷。
    pub fn parse_malloc(&self, nbyte: usize) -> Option<&mut [u8]> { todo!() }
    /// parser_alloc（:107-118 + :27-34）：alloc_size <= 0 → None；清零，不带长度头。
    pub fn parser_alloc(&self, alloc_size: i64) -> Option<&mut [u8]> { todo!() }
    /// parse_strndup（:144-157）：拷贝并在末尾补 NUL；返回的切片不含 NUL。
    pub fn parse_strndup(&self, s: &[u8]) -> Option<&[u8]> { todo!() }
    /// RULEBOOK 0.12 的"先拷进解析 arena"：= parse_strndup，失败返回 OB_ALLOCATE_MEMORY_FAILED。
    pub fn copy_str(&self, s: &[u8]) -> ObResult<&[u8]> { todo!() }
    /// 子节点数组：C++ 的 static_cast<ParseNode**>(alloc.alloc(sizeof(ParseNode*) * n))；全部置 None。
    /// Option<ParseNode> 与 ParseNode* 布局相同（ParseNode 是 NonNull 的 repr(transparent) 包装）。
    pub fn alloc_children<'p>(&'p self, n: i32) -> Option<&'p mut [Option<ParseNode<'p>>]> { todo!() }

    // ---- parse_node.c 的构造函数（RULEBOOK 0.12 的 ParseArena::new_node）----
    /// new_node(pool, type, num)（parse_node.c:180）：value_ = INT64_MAX、pl_str_off_ = -1、
    /// num > 0 时分配 num 个子节点槽（全空）；先调 C 侧 weak 的 try_check_mem_status(1024)（§3.1.9）。失败 → None。
    pub fn new_node(&self, kind: ObItemType, num: i32) -> Option<ParseNode<'_>> { todo!() }
    /// new_terminal_node(pool, type)（:368）
    pub fn new_terminal_node(&self, kind: ObItemType) -> Option<ParseNode<'_>> { todo!() }
    /// new_non_terminal_node(pool, tag, num, ...)（:374，可变参数）：子节点按顺序写入。
    pub fn new_non_terminal_node<'p>(&'p self, kind: ObItemType, children: &[Option<ParseNode<'p>>]) -> Option<ParseNode<'p>> { todo!() }
    /// append_child / push_back_child（:941、:864）：error_code 是 C 的 int 出参，语义照 C。
    pub fn append_child<'p>(&'p self, error_code: &mut i32, left: Option<ParseNode<'p>>, right: Option<ParseNode<'p>>) -> Option<ParseNode<'p>> { todo!() }
    pub fn push_back_child<'p>(&'p self, error_code: &mut i32, left: Option<ParseNode<'p>>, node: Option<ParseNode<'p>>) -> Option<ParseNode<'p>> { todo!() }
    /// merge_tree（:332）
    pub fn merge_tree<'p>(&'p self, fatal_error: &mut i32, kind: ObItemType, source_tree: Option<ParseNode<'p>>) -> Option<ParseNode<'p>> { todo!() }
    /// deep_copy_parse_node / _base（:143、:96）：把 src 深拷贝进 dst（dst 来自本 arena）。
    pub fn deep_copy_parse_node<'p>(&'p self, src: ParseNode<'_>, dst: ParseNode<'p>) -> ObResult { todo!() }
    pub fn deep_copy_parse_node_base<'p>(&'p self, src: ParseNode<'_>, dst: ParseNode<'p>) -> ObResult { todo!() }
}
```

#### 2.2.4 `ParseNode<'p>` 与 `LocalParseNode`

```rust
use core::{marker::PhantomData, ptr::NonNull};

/// C++：ParseNode *（parse_node.h:129-195）。RULEBOOK 0.12、§2.9。
/// - Copy；'p 不变（invariant），所以不同生命周期的树不能互相挂接（§3.1.7）；
/// - !Send、!Sync：解析树只在解析它的线程上用，写操作是经共享句柄的就地写（类似 Cell）；
/// - Option<ParseNode<'p>> 与 C 的可空 ParseNode* 布局相同。
#[repr(transparent)]
#[derive(Clone, Copy)]
pub struct ParseNode<'p> {
    raw: NonNull<sys::ParseNode>,
    _m: PhantomData<(fn(&'p ()) -> &'p (), *const ())>,
}

impl<'p> ParseNode<'p> {
    // ---- 读 ----
    pub fn kind(self) -> ObItemType { todo!() }                    // type_
    pub fn num_child(self) -> i32 { todo!() }                      // num_child_
    /// children_[i]：i 越界（< 0 或 >= num_child_）、children_ 为空、元素为空，都返回 None。
    pub fn child(self, i: i64) -> Option<ParseNode<'p>> { todo!() }
    pub fn param_num(self) -> i16 { todo!() }                      // param_num_
    pub fn flag(self) -> u32 { todo!() }                           // flag_（位域所在的整字）
    pub fn reserved(self) -> u32 { todo!() }                       // reserved_（联合体里位域后面的 uint32_t）
    pub fn value(self) -> i64 { todo!() }                          // value_
    /// int32_values_[i]、int16_values_[i]：联合体的通道，按本机字节序从 value_ 取（i 越界 abort，同 OB_ASSERT）。
    pub fn int32_values(self, i: usize) -> i32 { todo!() }
    pub fn int16_values(self, i: usize) -> i16 { todo!() }
    /// str_value_ 为空 → None；长度 = max(str_len_, 0)。
    pub fn str_value(self) -> Option<&'p [u8]> { todo!() }
    /// RULEBOOK 0.12：空指针得 &[]，负长度按 0。
    pub fn str_bytes(self) -> &'p [u8] { todo!() }
    pub fn str_len(self) -> i64 { todo!() }                        // 原值
    /// C++ 把 str_value_ 当 C 字符串用的地方（ObString(node->str_value_)、%s，约 5 处，
    /// 如 ob_raw_expr_resolver_impl.cpp:505）：str_bytes() 里第一个 NUL 之前的部分。
    pub fn str_cstr_bytes(self) -> &'p [u8] { todo!() }
    pub fn pl_str_off(self) -> i64 { todo!() }                     // 联合体，两名同址
    pub fn sql_str_off(self) -> i64 { todo!() }
    pub fn raw_text(self) -> Option<&'p [u8]> { todo!() }          // raw_text_ + text_len_
    pub fn raw_text_bytes(self) -> &'p [u8] { todo!() }
    pub fn text_len(self) -> i64 { todo!() }
    pub fn pos(self) -> i64 { todo!() }
    pub fn stmt_loc(self) -> sys::ObStmtLoc { todo!() }            // 4 个 int，Copy
    pub fn raw_param_idx(self) -> i64 { todo!() }                  // 联合体，两名同址
    pub fn raw_sql_offset(self) -> i64 { todo!() }
    /// C++ 的指针相等 node1 == node2。ParseNode 不实现 PartialEq：内容比较用 parsenode_equal。
    pub fn ptr_eq(self, other: ParseNode<'_>) -> bool { todo!() }

    // 19 个标志位（parse_node.h:137-154），每个一个读、一个写；length_semantics_ 是 2 位。
    pub fn is_neg(self) -> bool { todo!() }
    pub fn is_hidden_const(self) -> bool { todo!() }
    pub fn is_tree_not_param(self) -> bool { todo!() }
    pub fn length_semantics(self) -> u32 { todo!() }
    pub fn is_val_paramed_item_idx(self) -> bool { todo!() }
    pub fn is_copy_raw_text(self) -> bool { todo!() }
    pub fn is_column_varchar(self) -> bool { todo!() }
    pub fn is_trans_from_minus(self) -> bool { todo!() }
    pub fn is_assigned_from_child(self) -> bool { todo!() }
    pub fn is_num_must_be_pos(self) -> bool { todo!() }
    pub fn is_date_unit(self) -> bool { todo!() }
    pub fn is_literal_bool(self) -> bool { todo!() }
    pub fn is_empty(self) -> bool { todo!() }
    pub fn is_multiset(self) -> bool { todo!() }
    pub fn is_forbid_anony_parameter(self) -> bool { todo!() }
    pub fn is_input_quoted(self) -> bool { todo!() }
    pub fn is_forbid_parameter(self) -> bool { todo!() }
    pub fn is_default_literal_expression(self) -> bool { todo!() }
    // set_is_neg(v: bool) …… set_is_default_literal_expression(v: bool)、set_length_semantics(v: u32)：同上 19 个

    // ---- 写标量 ----
    pub fn set_kind(self, kind: ObItemType) { todo!() }
    pub fn set_param_num(self, v: i16) { todo!() }
    pub fn set_flag(self, v: u32) { todo!() }
    pub fn set_reserved(self, v: u32) { todo!() }
    pub fn set_value(self, v: i64) { todo!() }
    pub fn set_int32_values(self, i: usize, v: i32) { todo!() }
    pub fn set_int16_values(self, i: usize, v: i16) { todo!() }
    pub fn set_pl_str_off(self, v: i64) { todo!() }
    pub fn set_sql_str_off(self, v: i64) { todo!() }
    pub fn set_pos(self, v: i64) { todo!() }
    pub fn set_stmt_loc(self, v: sys::ObStmtLoc) { todo!() }
    pub fn set_raw_param_idx(self, v: i64) { todo!() }
    pub fn set_raw_sql_offset(self, v: i64) { todo!() }
    /// 只许缩小：0 <= n <= 当前值；否则 abort（放大会让 child(i) 越界读，C++ 里是未定义行为）。
    pub fn set_num_child(self, n: i32) { todo!() }
    /// 只许缩小：0 <= n <= max(当前 str_len_, 0)；否则 abort。
    pub fn set_str_len(self, n: i64) { todo!() }
    /// 只许缩小，同上。
    pub fn set_text_len(self, n: i64) { todo!() }

    // ---- 写指针（RULEBOOK 0.12：只接受同一解析 arena 生命周期的值）----
    /// str_value_ = s.as_ptr()，str_len_ = s.len()（两个字段一起写）。其他来源先 arena.copy_str(..)。
    pub fn set_str_value(self, s: &'p [u8]) { todo!() }
    /// str_value_ = NULL，str_len_ = 0
    pub fn set_str_value_null(self) { todo!() }
    pub fn set_raw_text(self, s: &'p [u8]) { todo!() }
    pub fn set_raw_text_null(self) { todo!() }
    /// children_[i] = child；i 越界 abort（同 RULEBOOK §2.5 的 arr[i]）。
    pub fn set_child(self, i: i64, child: Option<ParseNode<'p>>) { todo!() }
    /// children_ = arr，num_child_ = arr.len()；arr 被消费（之后只能经句柄读写）。
    pub fn set_children(self, arr: &'p mut [Option<ParseNode<'p>>]) { todo!() }
}

/// C++：解析器目录外在栈上构造的 ParseNode（35 处，如 ob_datum_cast.cpp:8897、ob_raw_expr_resolver_impl.cpp 的 ret_node）。
/// RULEBOOK 0.12。C++ 的 `ParseNode x;` 不初始化，这里全零。
pub struct LocalParseNode { raw: sys::ParseNode }
impl Default for LocalParseNode { fn default() -> Self { todo!() } }
impl LocalParseNode {
    pub fn new() -> Self { todo!() }
    /// 句柄的生命周期就是这次可变借用；因为 'p 不变，它不能挂进 arena 里的树，也不能把 arena 节点挂到它下面（§3.1.7）。
    pub fn as_node(&mut self) -> ParseNode<'_> { todo!() }
}
```

#### 2.2.5 `OwnedParseTree`

```rust
/// C++：PS 缓存里深拷贝出来、跨会话和线程使用的节点（ob_prepare_stmt_struct.cpp:345-378，
/// 挂在 raw_params_，ob_prepare_stmt_struct.h:264）。RULEBOOK 0.12。
/// 自带一个拥有型 arena；构造完成后只读，所以 Send + Sync（§4 P5）。
pub struct OwnedParseTree { /* arena: Box<Arena>, root: NonNull<sys::ParseNode> */ }

impl OwnedParseTree {
    /// get_deep_copy_size + deep_copy_parse_node（C 实现）拷进自带 arena。
    pub fn new(src: ParseNode<'_>) -> ObResult<OwnedParseTree> { todo!() }
    /// 同上，但在冻结之前用私有副本做一次修正（如 NEG_PARAM 的负号挪位，ob_prepare_stmt_struct.cpp:362-373）。
    /// fixup 拿到的句柄和 arena 只在闭包里有效。
    pub fn new_with(src: ParseNode<'_>, fixup: impl FnOnce(ParseNode<'_>, &ParseArena) -> ObResult) -> ObResult<OwnedParseTree> { todo!() }
    /// 用到时深拷贝进请求的解析 arena，得到普通的可写句柄（ob_sql.cpp:1604-1608 的 resolve_const 这样用）。
    pub fn copy_into<'p>(&self, arena: &'p ParseArena) -> ObResult<ParseNode<'p>> { todo!() }
    /// 拥有型的深拷贝（C++ 拷贝 ObPsStmtInfo 时）
    pub fn try_clone(&self) -> ObResult<OwnedParseTree> { todo!() }
    // 只读便捷访问（根节点）：
    pub fn kind(&self) -> ObItemType { todo!() }
    pub fn value(&self) -> i64 { todo!() }
    pub fn str_bytes(&self) -> &[u8] { todo!() }
}
```

#### 2.2.6 `ParseResult<'p>`

```rust
/// C++：ParseResult（parse_node.h:293-367），调用方栈上的局部变量，常先 memset 为 0（ob_parser.cpp:931,1089）。
/// 分配在解析 arena 里，地址固定：parse_init 之后扫描器的 yyextra 指着它（§3.1.4）。!Send。
pub struct ParseResult<'p> {
    raw: NonNull<sys::ParseResult>,
    _m: PhantomData<(fn(&'p ()) -> &'p (), *const ())>,
}

/// C++：ObQuestionMarkCtx（parse_node.h:236-244）的值拷贝；name_ 指向某个解析 arena，所以带 'p。
#[derive(Clone, Copy)]
pub struct ObQuestionMarkCtx<'p> { raw: sys::ObQuestionMarkCtx, _m: PhantomData<fn(&'p ()) -> &'p ()> }
impl<'p> ObQuestionMarkCtx<'p> {
    pub fn count(&self) -> i32 { todo!() }
    pub fn capacity(&self) -> i32 { todo!() }
    pub fn by_ordinal(&self) -> bool { todo!() }
    pub fn by_name(&self) -> bool { todo!() }
    pub fn by_defined_name(&self) -> bool { todo!() }
    /// name_[i]（C 字符串，不含 NUL）；i 越界或为空 → None
    pub fn name(&self, i: i32) -> Option<&'p [u8]> { todo!() }
}

impl<'p> ParseResult<'p> {
    /// = `ParseResult r; memset(&r, 0, sizeof r); r.malloc_pool_ = pool;`
    pub fn new(pool: &'p ParseArena) -> ObResult<ParseResult<'p>> { todo!() }
    pub fn malloc_pool(&self) -> &'p ParseArena { todo!() }

    /// input_sql_ + input_sql_len_（C 是 int；sql.len() > i32::MAX → abort）
    pub fn set_input_sql(&mut self, sql: &'p [u8]) { todo!() }
    pub fn input_sql(&self) -> &'p [u8] { todo!() }
    pub fn result_tree(&self) -> Option<ParseNode<'p>> { todo!() }
    pub fn set_result_tree(&mut self, t: Option<ParseNode<'p>>) { todo!() }
    /// error_msg_ 指向线程局部缓冲（sql_parser_base.c:36），所以拷出来（到第一个 NUL，最多 1024 字节）。
    pub fn error_msg(&self) -> Vec<u8> { todo!() }
    /// param_nodes_ 链表（ParamList，parse_node.h:199-203）
    pub fn param_nodes(&self) -> impl Iterator<Item = ParseNode<'p>> + '_ { core::iter::empty() }
    /// param_nodes_ = tail_param_node_ = NULL（ob_parser.cpp:1101-1102）
    pub fn clear_param_nodes(&mut self) { todo!() }
    /// no_param_sql_ / no_param_sql_buf_len_：缓冲由调用方从解析 arena 分配（ob_parser.cpp:976-982），交给 C 写。
    pub fn set_no_param_sql_buf(&mut self, buf: &'p mut [u8]) { todo!() }
    /// 前 no_param_sql_len_ 个字节（不超过缓冲长度）
    pub fn no_param_sql(&self) -> &'p [u8] { todo!() }
    pub fn set_charset_info(&mut self, cs: Option<ObCollationType>) { todo!() }        // §3.1.6
    pub fn charset_info(&self) -> Option<ObCollationType> { todo!() }
    pub fn set_charset_info_nls_db(&mut self, cs: Option<ObCollationType>) { todo!() }
    pub fn charset_info_nls_db(&self) -> Option<ObCollationType> { todo!() }
    pub fn question_mark_ctx(&self) -> ObQuestionMarkCtx<'p> { todo!() }
    pub fn set_question_mark_ctx(&mut self, ctx: ObQuestionMarkCtx<'p>) { todo!() }
    /// minus_ctx_ 全是标量，可以直接给 &mut（C 代码只在 parse_sql 期间访问，那时这里没有借用）。
    pub fn minus_ctx_mut(&mut self) -> &mut sys::ObMinusStatusCtx { todo!() }
    /// ins_multi_value_res_：由调用方在解析 arena 里分配（ob_parser.cpp:962-968）
    pub fn set_ins_multi_value_res(&mut self, r: &'p mut sys::InsMultiValuesResult) { todo!() }
    pub fn ins_multi_value_res(&self) -> Option<&sys::InsMultiValuesResult> { todo!() }
    /// pl_ns_ 只在 f 运行期间指向 ns，返回前恢复原值（§3.1.5）。C++ 在 prepare_parse 里设置后接着调 parse_sql（ob_parser.cpp:1090）。
    pub fn with_pl_ns<R>(&mut self, ns: &dyn PlSymbolLookup, f: impl FnOnce(&mut Self) -> R) -> R { todo!() }
    pub fn has_pl_ns(&self) -> bool { todo!() }
    pub fn ref_object_nodes(&self) -> impl Iterator<Item = (sys::RefType, ParseNode<'p>)> + '_ { core::iter::empty() }
}
```

标量字段一律"字段名去掉末尾下划线"作读、`set_` 前缀作写（与 §2.9 的 `num_child()` 同一规则），由一个宏展开。完整清单（类型照 C）：

| 分组 | 字段 → 方法 |
|---|---|
| `i32` | `param_node_num_`、`token_num_`、`extra_errno_`、`start_col_`、`end_col_`、`line_`、`yycolumn_`、`yylineno_`、`connection_collation_`、`semicolon_start_col_`；`no_param_sql_len_`（写入值不得超过缓冲长度，否则 abort） |
| `i64` | `last_escape_check_pos_` |
| `u64` | `sql_mode_`（`ObSQLMode`） |
| `bool` | `mysql_compatible_comment_`、`enable_compatible_comment_` |
| 23 个 1 位标志（`parse_node.h:308-330`） | `has_encount_comment_`、`is_fp_`、`is_multi_query_`、`is_ignore_hint_`、`is_ignore_token_`、`need_parameterize_`、`in_q_quote_`、`is_for_trigger_`、`is_dynamic_sql_`、`is_dbms_sql_`、`is_batched_multi_enabled_split_`、`is_not_utf8_connection_`、`may_bool_value_`、`is_include_old_new_in_trigger_`、`is_normal_ps_prepare_`、`is_multi_values_parser_`、`is_for_remap_`、`contain_sensitive_data_`、`may_contain_sensitive_data_`、`is_external_table_`、`is_returning_`、`is_into_cluster_`、`is_method_opt_parser_`（如 `is_fp()`/`set_is_fp(bool)`） |
| `pl_parse_info_` 的标量（`:219-232`） | 加 `pl_parse_info_` 前缀：`pl_parse_info_is_pl_parse()`/`set_…`、`…_is_pl_parse_expr`、`…_is_forbid_pl_fp`、`…_is_inner_parse`、`…_last_pl_symbol_pos`、`…_is_parse_dynamic_sql`、`…_plsql_line` |
| 不暴露 | `yyscan_info_`、`jmp_buf_`、`tmp_literal_`、`tail_param_node_`、`tail_ref_object_node_`（C 内部） |

#### 2.2.7 `ObParseCtx<'p>`（PL）

```rust
/// C++：ObParseCtx（src/sql/pl/parser/parse_stmt_node.h:57-91），ObPLParser 里 memset 后的栈上局部变量
/// （ob_pl_parser.cpp:96-111,182-197,215-230）。内嵌 jmp_buf，scanner 的 yyextra 指着它，所以同样分配在解析 arena 里。!Send。
pub struct ObParseCtx<'p> { raw: NonNull<sys::ObParseCtx>, _m: PhantomData<(fn(&'p ()) -> &'p (), *const ())> }

impl<'p> ObParseCtx<'p> {
    /// = memset 0 + mem_pool_ = pool + global_errno_ = OB_SUCCESS
    pub fn new(pool: &'p ParseArena) -> ObResult<ObParseCtx<'p>> { todo!() }
    pub fn set_stmt(&mut self, s: &'p [u8]) { todo!() }          // stmt_str_ + stmt_len_
    pub fn stmt(&self) -> &'p [u8] { todo!() }
    pub fn set_orig_stmt(&mut self, s: &'p [u8]) { todo!() }     // orig_stmt_str_ + orig_stmt_len_
    pub fn orig_stmt(&self) -> &'p [u8] { todo!() }
    pub fn stmt_tree(&self) -> Option<ParseNode<'p>> { todo!() }
    pub fn set_stmt_tree(&mut self, t: Option<ParseNode<'p>>) { todo!() }
    pub fn global_errno(&self) -> i32 { todo!() }
    pub fn global_errno_mut(&mut self) -> &mut i32 { todo!() }   // merge_tree 的出参（ob_pl_parser.cpp:262）
    pub fn global_errmsg(&self) -> &[u8] { todo!() }              // 内嵌缓冲，到第一个 NUL
    pub fn cur_error_info(&self) -> Option<sys::ObParseErrorInfo> { todo!() }
    pub fn question_mark_ctx(&self) -> ObQuestionMarkCtx<'p> { todo!() }
    pub fn set_charset_info(&mut self, cs: Option<ObCollationType>) { todo!() }
    pub fn set_charset_info_nls_db(&mut self, cs: Option<ObCollationType>) { todo!() }
    pub fn charset_info(&self) -> Option<ObCollationType> { todo!() }
    pub fn scanner_sql_mode(&self) -> u64 { todo!() }             // scanner_ctx_.sql_mode_
    pub fn set_scanner_sql_mode(&mut self, m: u64) { todo!() }
    // 标量：comp_mode_（i32）、is_not_utf8_connection_（bool）、last_escape_check_pos_（i64）、
    // connection_collation_（i32）、mysql_compatible_comment_（bool）；
    // 8 个位域：is_inner_parse_、is_for_trigger_、is_dynamic_、is_for_preprocess_、
    // is_include_old_new_in_trigger_、in_q_quote_、need_switch_to_wrap_、contain_sensitive_data_。
    // 读写规则同 ParseResult。
}
```

#### 2.2.8 `FtsParserResult<'p>` 与 `FtsNode<'p>`

```rust
/// C++：FtsParserResult（fts_base.h:85-93），调用方的局部变量；fts_parse_docment 返回后 C 不再保留指向它的指针
/// （yyscanner_ 字段留下一个已销毁的扫描器地址，从不再用），所以按值存放。
pub struct FtsParserResult<'p> { raw: sys::FtsParserResult, _m: PhantomData<(fn(&'p ()) -> &'p (), *const ())> }
impl<'p> FtsParserResult<'p> {
    pub fn new() -> Self { todo!() }
    pub fn ret(&self) -> i32 { todo!() }                          // FTS_OK / FTS_ERROR_MEMORY / FTS_ERROR_SYNTAX / FTS_ERROR_OTHER
    pub fn err_info(&self) -> &'p [u8] { todo!() }
    pub fn root(&self) -> Option<FtsNode<'p>> { todo!() }
    pub fn list_head(&self) -> Option<FtsNode<'p>> { todo!() }
    pub fn list_tail(&self) -> Option<FtsNode<'p>> { todo!() }
}
/// C++：FtsNode *（fts_base.h:66-74）。只读。
#[derive(Clone, Copy)]
pub struct FtsNode<'p> { raw: NonNull<sys::FtsNode>, _m: PhantomData<(fn(&'p ()) -> &'p (), *const ())> }
impl<'p> FtsNode<'p> {
    pub fn node_type(self) -> FtsNodeType { todo!() }             // type
    pub fn term(self) -> &'p [u8] { todo!() }                     // term.str_/len_
    pub fn oper(self) -> FtsOperType { todo!() }
    pub fn list_head(self) -> Option<FtsNode<'p>> { todo!() }
    pub fn list_tail(self) -> Option<FtsNode<'p>> { todo!() }
    pub fn next(self) -> Option<FtsNode<'p>> { todo!() }
    pub fn last(self) -> Option<FtsNode<'p>> { todo!() }
    pub fn go_up(self) -> bool { todo!() }
}
```

#### 2.2.9 23 个入口

```rust
// rust/port/parser/src/api.rs —— 名字和参数顺序照 C；返回 OB 错误码的改成 ObResult
pub fn parse_init(p: &mut ParseResult<'_>) -> ObResult { todo!() }
/// C 里 buf 为空或长度 0 → OB_PARSER_ERR_EMPTY_QUERY；节点可能直接指向 buf，所以 buf 要活过 'p。
pub fn parse_sql<'p>(p: &mut ParseResult<'p>, buf: &'p [u8]) -> ObResult { todo!() }
/// C 返回 0 或 -1/扫描器初始化的返回值，不是 OB 错误码；C++ 只判 0 != ret（ob_pl_parser.cpp:236,253）。
pub fn obpl_parser_init(ctx: &mut ObParseCtx<'_>) -> core::ffi::c_int { todo!() }
pub fn obpl_parser_parse(ctx: &mut ObParseCtx<'_>) -> ObResult { todo!() }
/// fts_parse_docment（fts_parse.c:23-40）：无返回值，结果在 ss 里。
pub fn fts_parse_docment<'p>(input: &'p [u8], pool: &'p ParseArena, ss: &mut FtsParserResult<'p>) { todo!() }
pub fn get_deep_copy_size(node: ParseNode<'_>, size: &mut i64) -> ObResult { todo!() }
/// 返回比较结果；栈溢出时 C 写 *ret = OB_PARSER_ERR_SIZE_OVERFLOW（parse_node.c:696-701）→ 这里写 Err。
pub fn parsenode_equal(a: Option<ParseNode<'_>>, b: Option<ParseNode<'_>>, ret: &mut ObResult) -> bool { todo!() }
pub fn parsenode_hash(node: Option<ParseNode<'_>>, ret: &mut ObResult) -> u64 { todo!() }
/// C 参数是以 NUL 结尾的字符串（FFI 边界用 &CStr，RULEBOOK §2.5）
pub fn mysql_sql_reserved_keyword_lookup(word: &core::ffi::CStr) -> i32 { todo!() }
pub fn ob_parse_binary_len(len: i64) -> i64 { todo!() }                 // (len + 1) / 2
/// dest.len() >= ob_parse_binary_len(src.len()) 否则 abort（C 不检查）
pub fn ob_parse_binary(src: &[u8], dest: &mut [u8]) { todo!() }
pub fn ob_parse_bit_string_len(len: i64) -> i64 { todo!() }             // (len + 7) / 8
pub fn ob_parse_bit_string(src: &[u8], dest: &mut [u8]) { todo!() }
/// 就地改写调用方拥有的字节（借来的字节按 §2.4 先拷贝再改）
pub fn str_tolower(buf: &mut [u8]) { todo!() }
pub fn str_toupper(buf: &mut [u8]) { todo!() }
// get_type_name 在 query::item_type（§2.5.2），这里 re-export。
// new_node 等 9 个节点函数是 ParseArena 的方法（§2.2.3）。
```

#### 2.2.10 回调蹦床、`ParserHost`、`PlSymbolLookup`

```rust
// rust/port/parser/src/host.rs
use oblib::error::ObResult;

/// 解析器回调里，函数体依赖 step 3 才翻译出来的 oblib 单元（字符集、strntoll、backtrace）的那部分。
/// RULEBOOK 0.12："蹦床把指针转成安全类型，再调用 sql 在启动时注册的 trait 方法"。
/// 由 sql 实现、在 main 的第 3 步（注册纯函数式 seam，RULEBOOK 0.5）绑定。所有方法都在解析线程上同步调用。
pub trait ParserHost: Sync {
    /// ob_convert（parse_malloc.cpp:190,195,216）：把 from（from_cs 编码）转成 to 编码写进 dst，返回写入字节数。
    fn ob_convert(&self, dst: &mut [u8], to: ObCollationType, from: &[u8], from_cs: ObCollationType,
                  trim_incomplete_tail: bool, replace_char: u32, errors: &mut u32) -> usize;
    /// cs->state & OB_CS_UNICODE（:180-182）
    fn is_unicode(&self, cs: ObCollationType) -> bool;
    /// cs->escape_with_backslash_is_dangerous（:328）
    fn escape_with_backslash_is_dangerous(&self, cs: ObCollationType) -> bool;
    /// cs->cset->well_formed_len(cs, b, e, nchars, error)（:334-335）
    fn well_formed_len(&self, cs: ObCollationType, s: &[u8], nchars: u64, error: &mut i32) -> usize;
    /// cs->cset->ismbchar(cs, p, e)（:340）
    fn ismbchar(&self, cs: ObCollationType, s: &[u8]) -> u32;
    /// ob_strntoll / ob_strntoull（ob_ctype_simple.cc:754,759）：end 是消费掉的字节数
    fn ob_strntoll(&self, s: &[u8], base: i32, end: &mut usize, err: &mut i32) -> i64;
    fn ob_strntoull(&self, s: &[u8], base: i32, end: &mut usize, err: &mut i32) -> i64;
    /// ob_backtrace / parray（ob_backtrace.cpp:192-199）
    fn ob_backtrace(&self, buffer: &mut [usize]) -> i32;
    /// 写进 buf（以 NUL 结尾），返回写入长度
    fn parray(&self, buf: &mut [u8], array: &[i64]) -> usize;
}
/// 槽位名按 §4："类型名去 Ob 前缀的大写蛇形 + _SLOT"
pub static PARSER_HOST_SLOT: oblib::slot::Slot<dyn ParserHost> = oblib::slot::Slot::new();

/// C++：lookup_pl_symbol（ob_pl_stmt.cpp:3376-3397）。由 sql::pl 的 ObPLBlockNS 实现。
/// 找到 → *idx = var_index；没找到 → Ok 且 idx 不变；参数非法 → Err(OB_ERR_UNEXPECTED)。
pub trait PlSymbolLookup {
    fn lookup_pl_symbol(&self, symbol: &[u8], idx: &mut i64) -> ObResult;
}
```

```rust
// rust/port/parser/src/callbacks.rs —— 只有 C 调用；Rust 代码不直接调用这些符号
use core::ffi::{c_char, c_int, c_void};
#[no_mangle] pub extern "C" fn parse_malloc(nbyte: usize, malloc_pool: *mut c_void) -> *mut c_void { todo!() }
#[no_mangle] pub extern "C" fn parse_realloc(ptr: *mut c_void, nbyte: usize, malloc_pool: *mut c_void) -> *mut c_void { todo!() }
#[no_mangle] pub extern "C" fn parse_free(ptr: *mut c_void) { todo!() }
#[no_mangle] pub extern "C" fn parse_strndup(str: *const c_char, nbyte: usize, malloc_pool: *mut c_void) -> *mut c_char { todo!() }
#[no_mangle] pub extern "C" fn parse_strdup(str: *const c_char, malloc_pool: *mut c_void, out_len: *mut i64) -> *mut c_char { todo!() }
#[no_mangle] pub extern "C" fn parse_str_convert_utf8(src_cs: *const sys::ObCharsetInfo, str: *const c_char,
    malloc_pool: *mut c_void, out_len: *mut i64, extra_errno: *mut c_int) -> *mut c_char { todo!() }
#[no_mangle] pub extern "C" fn replace_invalid_character(src_cs: *const sys::ObCharsetInfo, nls_db_cs: *const sys::ObCharsetInfo,
    str: *const c_char, out_len: *mut i64, malloc_pool: *mut c_void, extra_errno: *mut c_int) -> *mut c_char { todo!() }
#[no_mangle] pub extern "C" fn strndup_with_prefix(prefix: *const c_char, str: *const c_char, nbyte: usize, malloc_pool: *mut c_void) -> *mut c_char { todo!() }
#[no_mangle] pub extern "C" fn check_real_escape(cs: *const sys::ObCharsetInfo, str: *mut c_char, str_len: i64, last_escape_check_pos: i64) -> bool { todo!() }
#[no_mangle] pub extern "C" fn parser_alloc(malloc_pool: *mut c_void, alloc_size: i64) -> *mut c_void { todo!() }
#[no_mangle] pub extern "C" fn malloc_parentheses_info(nbyte: usize, malloc_pool: *mut c_void) -> *mut c_void { todo!() }
#[no_mangle] pub extern "C" fn murmurhash(data: *const c_void, len: i32, hash: u64) -> u64 { todo!() }
#[no_mangle] pub extern "C" fn ob_strntoll(ptr: *const c_char, len: usize, base: c_int, end: *mut *mut c_char, err: *mut c_int) -> i64 { todo!() }
#[no_mangle] pub extern "C" fn ob_strntoull(ptr: *const c_char, len: usize, base: c_int, end: *mut *mut c_char, err: *mut c_int) -> i64 { todo!() }
#[no_mangle] pub extern "C" fn lookup_pl_symbol(pl_ns: *const c_void, symbol: *const c_char, len: usize, find_idx: *mut i64) -> c_int { todo!() }
#[no_mangle] pub extern "C" fn check_stack_overflow_c() -> bool { todo!() }
#[no_mangle] pub extern "C" fn obpl_parser_check_stack_overflow() -> c_int { todo!() }
#[no_mangle] pub extern "C" fn ob_backtrace_c(buffer: *mut *mut c_void, size: c_int) -> c_int { todo!() }
#[no_mangle] pub extern "C" fn parray_c(buf: *mut c_char, len: i64, array: *mut i64, size: c_int) -> *mut c_char { todo!() }
#[cfg(target_arch = "x86_64")]
#[no_mangle] pub extern "C" fn ob_parse_binary_simd(src: *mut *const c_char, end: *const c_char, dest: *mut *mut c_char) { todo!() }
```

`check_mem_status`、`try_check_mem_status` 不在这里定义：C 侧的 weak 缺省实现保持不变（§3.1.9）。

#### 2.2.11 布局自检

```rust
/// 启动自检（RULEBOOK 0.12）：C 侧 parse_node_layout() 返回的 sizeof/alignof/offsetof 和位域探针，
/// 与 Rust 常量（core::mem::size_of、offset_of!、§3.1.8 的位号）逐项比对。不一致 → 记 ERROR、返回 OB_ERR_UNEXPECTED，
/// 调用方（observer 的 main，在第一次解析之前）随即 ob_abort。
pub fn check_parse_node_layout() -> ObResult { todo!() }
```

C 侧新文件 `rust/port/parser/csrc/parse_node_layout.c`（与解析器 C 用同一组参数编译）：

```c
typedef struct { const char *name; int64_t value; } ObParserLayoutItem;
/* 条目：ParseNode、ParseResult、ObStmtLoc、ParamList、RefObjList、PLParseInfo、ObQuestionMarkCtx、
   ObMinusStatusCtx、ParenthesesOffset、InsMultiValuesResult、ObParseCtx、ObScannerCtx、ObParseErrorInfo、
   FtsParserResult、FtsNode、FtsString、FtsNodeList 的 sizeof、alignof 和全部非位域字段的 offsetof；
   MAX_ERROR_MSG、MAX_QUESTION_MARK、PARSE_SELECT_MAX_IDX 和 18 个 OB_PARSER_* 常量；
   SQL_PARSER_COMPILATION 是否定义（必须为 0）。 */
const ObParserLayoutItem *parse_node_layout(int64_t *count);
/* 把第 idx 个位域全部置 1 时所在整字的值，覆盖 ParseNode 19 个、ParseResult 23 个、ObParseCtx 8 个。 */
uint32_t parse_node_flag_probe(int which_struct, int idx);
```

#### 2.2.12 常量与打印

```rust
// rust/port/parser/src/define.rs —— parse_define.h:26-43，C 的 int 值（与 OB 错误码相同）
pub const OB_MAX_PARSER_INT16_VALUE: i64 = 32738;
pub const OB_PARSER_SUCCESS: i32 = 0;
pub const OB_PARSER_ERR_NO_MEMORY: i32 = -4013;
pub const OB_PARSER_ERR_UNEXPECTED: i32 = -4016;
pub const OB_PARSER_ERR_EMPTY_QUERY: i32 = -5253;
pub const OB_PARSER_ERR_PARSE_SQL: i32 = -5001;
pub const OB_PARSER_ERR_SIZE_OVERFLOW: i32 = -4019;
pub const OB_PARSER_ERR_ILLEGAL_NAME: i32 = -5018;
// …… 另 10 个照 parse_define.h:34-43
pub const MAX_ERROR_MSG: usize = 1024;                 // parse_node.h:39

// rust/port/parser/src/print.rs
/// databuff_print_obj(ParseNode)（ob_parser_utils.h:47-73）：{"type":…, "int_value":…, "str_value_len":…, "str_value":…[, 位置], "children":[…]}
impl oblib::fmt::ObToString for ParseNode<'_> { /* … */ }
/// databuff_print_stmt_location（ob_parser_utils.h:30-45）
pub fn databuff_print_stmt_location(buf: &mut oblib::fmt::ObBuf, obj: &sys::ObStmtLoc) { todo!() }
```

#### 2.2.13 `build.rs`：gen_parser.sh 的等价物

```rust
// rust/port/parser/build.rs（要点；完整步骤见 §3.1.1）
include!("../build_support/cc_baseline.rs");
fn main() {
    // 1. 读 bindgen/flags.tsv 中本目标的一行；断言其 -D 集合里没有 SQL_PARSER_COMPILATION；
    //    断言 src/generated/sys_<target>.rs 文件头记录的参数与这一行逐字相同。
    // 2. native_build_required() 为假 → 到此为止（cargo check）。
    // 3. 校验 deps/3rd 里 bison -V == 2.4.1、flex --version == 2.5.35，否则 panic，信息照 gen_parser.sh:33。
    // 4. 在 OUT_DIR/parser_gen/ 下建镜像目录 src/{sql/parser,pl/parser,sql/pl/parser,query/api/query/parser}，
    //    拷入手写的 .y .l .c .h（不拷旧的 *_tab.* *_lex.* type_name.c _MD5 *.output）。
    // 5. 以镜像的 src/sql/parser 为工作目录，按 gen_parser.sh 的原参数和相对路径跑 bison、flex、sed（fts 在前，SQL 在后）；
    //    以镜像的 src/pl/parser 为工作目录跑 PL 的 bison、flex。环境：PATH 前置 devtools/bin，BISON_PKGDATADIR。
    // 6. 用 cc_build(Lang::C) 加 flags.tsv 的 -D 编译 14 个 C 文件和 csrc/parse_node_layout.c；
    //    -I 顺序：镜像 src、镜像 src/query/api，然后照 compile_commands.json 的顺序接仓库的 src、src/query/api、…（§3.1.2）。
    // 7. cargo:rerun-if-changed：拷贝的全部输入、§1.3.3 的 42 个头文件、flags.tsv、csrc、build.rs。
}
```

### 2.3 -sys crate

通用约定：
- 原始声明在各 crate 的 `raw` 模块（离线 bindgen 或手写，按目标分文件，放 `src/generated/raw_<target>.rs`，生成参数记在 `bindgen/flags.tsv`）；crate 根是安全层，函数名照 C。
- 链接：`cargo:rustc-link-search=native=<dep_dir>/lib` 加 `cargo:rustc-link-lib=static=<name>`；只在 `native_build_required()` 时发出（cargo check 不需要）。
- 版本断言函数 `assert_<lib>_version()`，由 observer 的 main 在第 3 步调用（§3.9.8）。

#### 2.3.1 `zstd138_sys`

```rust
pub mod raw {
    #[repr(C)] pub struct ZSTD_CCtx { _p: [u8; 0] }
    #[repr(C)] pub struct ZSTD_DCtx { _p: [u8; 0] }                         // ZSTD_DStream 是同一类型
    pub type ZSTD_allocFunction = unsafe extern "C" fn(opaque: *mut c_void, size: usize) -> *mut c_void;
    pub type ZSTD_freeFunction = unsafe extern "C" fn(opaque: *mut c_void, address: *mut c_void);
    #[repr(C)] pub struct ZSTD_customMem { pub customAlloc: Option<ZSTD_allocFunction>, pub customFree: Option<ZSTD_freeFunction>, pub opaque: *mut c_void }
    #[repr(C)] pub struct ZSTD_inBuffer { pub src: *const c_void, pub size: usize, pub pos: usize }
    #[repr(C)] pub struct ZSTD_outBuffer { pub dst: *mut c_void, pub size: usize, pub pos: usize }
    extern "C" { /* 18 个函数，签名照 zstd.h（含 OceanBase 加的 int *zstd_version） */ }
}
/// C++：OB_ZSTD_customMem（ob_zstd_wrapper.h）＋ ob_zstd_malloc/ob_zstd_free（ob_compress_util.h:76-92）。
/// 安全侧用 01 的 ForeignBlock；opaque 只区分"空"（分配返回空，与 C++ 相同）和"非空"。
#[derive(Clone, Copy)]
pub struct OB_ZSTD_customMem { has_opaque: bool }
impl OB_ZSTD_customMem { pub const fn new(has_opaque: bool) -> Self { todo!() } }
/// 拥有型句柄；Drop 调对应的 free 函数（C++ 的显式 ZSTD_free* 译成 drop）。Send，!Sync。
pub struct ZSTD_CCtx { /* NonNull<raw::ZSTD_CCtx> */ }
pub struct ZSTD_DCtx { /* … */ }
pub type ZSTD_DStream = ZSTD_DCtx;
pub fn ZSTD_createCCtx_advanced(mem: OB_ZSTD_customMem) -> Option<ZSTD_CCtx> { todo!() }
pub fn ZSTD_createDCtx_advanced(mem: OB_ZSTD_customMem) -> Option<ZSTD_DCtx> { todo!() }
pub fn ZSTD_createDStream_advanced(mem: OB_ZSTD_customMem) -> Option<ZSTD_DStream> { todo!() }
pub fn ZSTD_compressCCtx(cctx: &mut ZSTD_CCtx, dst: &mut [u8], src: &[u8], level: i32, zstd_version: Option<&mut i32>) -> usize { todo!() }
pub fn ZSTD_decompressDCtx(dctx: &mut ZSTD_DCtx, dst: &mut [u8], src: &[u8], zstd_version: Option<&mut i32>) -> usize { todo!() }
pub fn ZSTD_isError(code: usize) -> bool { todo!() }
pub fn ZSTD_compressBound(src_size: usize) -> usize { todo!() }
pub fn ZSTD_compressBegin(cctx: &mut ZSTD_CCtx, level: i32) -> usize { todo!() }
pub fn ZSTD_CCtx_reset(cctx: &mut ZSTD_CCtx, reset: u32) -> usize { todo!() }
// ZSTD_compressBlock、ZSTD_decompressBegin、ZSTD_decompressBlock、ZSTD_insertBlock、ZSTD_compressBound 只出现在
// ObZstdWrapper 里没有调用者的 compress_block / create_dctx / decompress_block / insert_block / compress_bound
// （ob_zstd_wrapper.cpp:175,197,227,238,248）。块模式下 dctx 会把之前的输出当窗口引用，安全封装需要另行设计；
// 这些方法交清单第 16 类按死代码处理，所以只保留 raw 声明，不提供安全封装。
pub fn ZSTD_decompressStream(zds: &mut ZSTD_DStream, output: &mut ZSTD_outBufferRef<'_>, input: &mut ZSTD_inBufferRef<'_>) -> usize { todo!() }
pub fn ZSTD_compressStream2(cctx: &mut ZSTD_CCtx, output: &mut ZSTD_outBufferRef<'_>, input: &mut ZSTD_inBufferRef<'_>, end_op: u32) -> usize { todo!() }
/// ZSTD_inBuffer/outBuffer 的借用视图：切片加 pos
pub struct ZSTD_inBufferRef<'a> { pub src: &'a [u8], pub pos: usize }
pub struct ZSTD_outBufferRef<'a> { pub dst: &'a mut [u8], pub pos: usize }
pub const ZSTD_e_continue: u32 = 0; pub const ZSTD_e_flush: u32 = 1; pub const ZSTD_e_end: u32 = 2;
pub const OB_ZSTD_LIB_VERSION_138: i32 = 138;                                  // zstd.h:16
```

#### 2.3.2 `zlib1213_sys`

```rust
pub mod raw { /* z_stream（bindgen，按目标：uLong 在 Windows 是 32 位）、14 个函数、zlibVersion、deflateInit2_、inflateInit2_ */ }
pub const ZLIB_VERSION: &str = "1.2.13";                                       // zlib.h:40
pub const Z_OK: i32 = 0; pub const Z_STREAM_END: i32 = 1; /* … 照 zlib.h */
pub fn zlibVersion() -> &'static core::ffi::CStr { todo!() }
/// 启动断言（RULEBOOK 0.10、0.14）：zlibVersion() == "1.2.13"，否则 ob_abort。
pub fn assert_zlib_version() { todo!() }
pub fn compressBound(source_len: u64) -> u64 { todo!() }
pub fn compress(dest: &mut [u8], dest_len: &mut u64, source: &[u8]) -> i32 { todo!() }
pub fn compress2(dest: &mut [u8], dest_len: &mut u64, source: &[u8], level: i32) -> i32 { todo!() }
pub fn uncompress(dest: &mut [u8], dest_len: &mut u64, source: &[u8]) -> i32 { todo!() }
pub fn crc32(crc: u64, buf: &[u8]) -> u64 { todo!() }
pub fn adler32(adler: u64, buf: &[u8]) -> u64 { todo!() }
/// 流式压缩/解压。z_stream 必须固定地址（zlib 在 state 里存回指针并在每次调用时比对），所以内部 Box 住。
/// 分配钩子：C++ 的 ob_zlib_alloc/ob_zlib_free（ob_compress_util.h:94-110），安全侧用 01 的 ForeignBlock，
/// 大小按 unsigned 乘法回绕（01 文档 S20）。Send，!Sync。
pub struct z_stream { /* Box<raw::z_stream> + 输入输出的借用状态 */ }
impl z_stream {
    pub fn new() -> Self { todo!() }
    /// next_in/avail_in 指向 input，在下一次 set_input 或 drop 之前有效
    pub fn set_input<'a>(&'a mut self, input: &'a [u8]) { todo!() }
    pub fn avail_in(&self) -> u32 { todo!() }
    pub fn total_out(&self) -> u64 { todo!() }
    pub fn msg(&self) -> Option<&core::ffi::CStr> { todo!() }
}
/// deflateInit2/inflateInit2 是宏（zlib.h:1814-1819）：展开成 deflateInit2_(…, ZLIB_VERSION, sizeof(z_stream))
pub fn deflateInit2(strm: &mut z_stream, level: i32, method: i32, window_bits: i32, mem_level: i32, strategy: i32) -> i32 { todo!() }
pub fn inflateInit2(strm: &mut z_stream, window_bits: i32) -> i32 { todo!() }
pub fn deflate(strm: &mut z_stream, output: &mut [u8], flush: i32) -> i32 { todo!() }
pub fn inflate(strm: &mut z_stream, output: &mut [u8], flush: i32) -> i32 { todo!() }
pub fn deflateReset(strm: &mut z_stream) -> i32 { todo!() }
pub fn inflateReset(strm: &mut z_stream) -> i32 { todo!() }
pub fn deflateEnd(strm: &mut z_stream) -> i32 { todo!() }
pub fn inflateEnd(strm: &mut z_stream) -> i32 { todo!() }
```

#### 2.3.3 `icu69_sys`

```rust
pub mod raw {
    extern "C" {
        #[link_name = "uregex_open_69"] pub fn uregex_open(pattern: *const UChar, len: i32, flags: u32, pe: *mut UParseError, status: *mut UErrorCode) -> *mut URegularExpression;
        // …… 共 15 个，link_name 一律带 _69 后缀（urename.h）
    }
}
pub type UChar = u16;                                           // C 里是 uint16_t（umachine.h:410-421）
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq)] pub struct UErrorCode(pub i32);
impl UErrorCode {
    pub const U_ZERO_ERROR: UErrorCode = UErrorCode(0);
    pub const U_BUFFER_OVERFLOW_ERROR: UErrorCode = UErrorCode(15);
    pub const U_STRING_NOT_TERMINATED_WARNING: UErrorCode = UErrorCode(-124);
    // U_REGEX_RULE_SYNTAX、U_REGEX_BAD_ESCAPE_SEQUENCE、U_REGEX_MISMATCHED_PAREN、U_REGEX_MISSING_CLOSE_BRACKET 等照 utypes.h
}
pub fn U_SUCCESS(c: UErrorCode) -> bool { todo!() }
pub fn U_FAILURE(c: UErrorCode) -> bool { todo!() }
#[repr(C)] #[derive(Clone, Copy, Default)] pub struct UParseError { pub line: i32, pub offset: i32, pub preContext: [UChar; 16], pub postContext: [UChar; 16] }
pub const UREGEX_UNIX_LINES: u32 = 1; pub const UREGEX_CASE_INSENSITIVE: u32 = 2; pub const UREGEX_MULTILINE: u32 = 8; pub const UREGEX_DOTALL: u32 = 32;
pub const U_ICU_VERSION: &str = "69.1";                         // uvernum.h:142；SQL 的 ICU_VERSION() 返回它
/// 拥有型；Drop → uregex_close。ICU 的正则对象不是线程安全的：Send、!Sync（每个表达式上下文一份）。
pub struct URegularExpression { /* NonNull<raw::URegularExpression> */ }
pub fn uregex_open(pattern: &[UChar], flags: u32, pe: &mut UParseError, status: &mut UErrorCode) -> Option<URegularExpression> { todo!() }
/// ICU 的 limit 参数是 int32_t，C++ 传 int64 字段（ob_expr_regexp_context.cpp:252-253）→ 照 C 的隐式转换截断。
pub fn uregex_setStackLimit(re: &mut URegularExpression, limit: i64, status: &mut UErrorCode) { todo!() }
pub fn uregex_setTimeLimit(re: &mut URegularExpression, limit: i64, status: &mut UErrorCode) { todo!() }
/// uregex_setText 不拷贝文本：文本只在 f 运行期间挂在对象上，返回前换回空文本。
pub fn with_uregex_setText<R>(re: &mut URegularExpression, text: &[UChar], status: &mut UErrorCode,
                              f: impl FnOnce(&mut URegexMatching<'_>) -> R) -> R { todo!() }
/// 挂着文本时才能调用的操作（名字照 C）
pub struct URegexMatching<'a> { /* &'a mut URegularExpression, text: &'a [UChar] */ }
impl URegexMatching<'_> {
    pub fn uregex_find(&mut self, start: i32, status: &mut UErrorCode) -> bool { todo!() }
    pub fn uregex_findNext(&mut self, status: &mut UErrorCode) -> bool { todo!() }
    pub fn uregex_start(&mut self, group: i32, status: &mut UErrorCode) -> i32 { todo!() }
    pub fn uregex_end(&mut self, group: i32, status: &mut UErrorCode) -> i32 { todo!() }
    pub fn uregex_getText(&mut self, status: &mut UErrorCode) -> &[UChar] { todo!() }
    /// dest 用完后 dest_pos 前进，容量不够时与 C 一样返回 U_BUFFER_OVERFLOW_ERROR 和需要的长度
    pub fn uregex_appendReplacement(&mut self, replacement: &[UChar], dest: &mut [UChar], dest_pos: &mut i32, status: &mut UErrorCode) -> i32 { todo!() }
    pub fn uregex_appendTail(&mut self, dest: &mut [UChar], dest_pos: &mut i32, status: &mut UErrorCode) -> i32 { todo!() }
}
pub fn u_strFromUTF8(dest: Option<&mut [UChar]>, dest_len: &mut i32, src: &[u8], status: &mut UErrorCode) { todo!() }
pub fn u_strToUTF8(dest: Option<&mut [u8]>, dest_len: &mut i32, src: &[UChar], status: &mut UErrorCode) { todo!() }
pub fn u_errorName(code: UErrorCode) -> &'static core::ffi::CStr { todo!() }
/// u_getVersion_69 == [69, 1, 0, 0]，否则 ob_abort
pub fn assert_icu_version() { todo!() }
```

#### 2.3.4 `libxml2_sys`

```rust
pub mod raw { /* 按目标 bindgen：xmlParserCtxt、xmlParserInput、xmlParserInputBuffer、_xmlSAXHandler、xmlError、
                 xmlChRangeGroup、15 个函数、xmlCharInRange、4 张 xmlIs*Group、xmlCheckVersion；
                 macOS/Android 用 2.10.4 的头，Linux 用 2.13.6 的头（布局不同） */ }
pub const LIBXML_VERSION: i32 = /* 21004 或 21306，按平台 */ 0;
/// C++ 的 ObLibXml2SaxHandler 的静态回调（ob_libxml2_sax_handler.h:26-45）改成 trait，由 oblib 的 ObLibXml2SaxParser 实现
/// （业务逻辑 1,029 行留在 oblib，RULEBOOK 0.14 的两层规则）。回调都在调用 xmlParse* 的线程上同步发生。
pub trait ObLibXml2SaxHandler {
    fn internal_subset(&mut self, name: Option<&[u8]>, external_id: Option<&[u8]>, system_id: Option<&[u8]>);
    fn start_document(&mut self);
    fn end_document(&mut self);
    fn start_element(&mut self, name: &[u8], attrs: &[(&[u8], Option<&[u8]>)]);
    fn end_element(&mut self, name: &[u8]);
    fn entity_reference(&mut self, name: &[u8]);
    fn characters(&mut self, ch: &[u8]);
    fn processing_instruction(&mut self, target: &[u8], data: Option<&[u8]>);
    fn comment(&mut self, value: &[u8]);
    fn cdata_block(&mut self, value: &[u8]);
    fn structured_error(&mut self, code: i32);              // 只取 error->code（:328）
}
pub enum SaxMode { Full, SyntaxCheck }                        // get_sax_handler / get_synax_handler（:27-103）
/// 拥有型；Drop → xmlFreeParserCtxt。
pub struct xmlParserCtxt { /* NonNull<raw::xmlParserCtxt> */ }
pub fn xmlInitParser() { todo!() }
pub fn xmlCleanupParser() { todo!() }
pub fn xmlResetLastError() { todo!() }
pub fn xmlNewParserCtxt() -> Option<xmlParserCtxt> { todo!() }
/// xmlParserInputBufferCreateMem(buf, len, XML_CHAR_ENCODING_NONE) → Grow → xmlNewIOInputStream → xmlPushInput，
/// 逐步返回 C 的结果码，失败时按 C++ 的顺序释放（:345-373）。input 要活过 ctxt 的使用期。
pub fn xmlPushInput_mem<'i>(ctxt: &mut xmlParserCtxtIn<'i>, input: &'i [u8]) -> i32 { todo!() }
pub struct xmlParserCtxtIn<'i> { /* xmlParserCtxt + PhantomData<&'i [u8]> */ }
impl xmlParserCtxt { pub fn with_input<'i>(self, input: &'i [u8]) -> xmlParserCtxtIn<'i> { todo!() } }
impl xmlParserCtxtIn<'_> {
    pub fn xmlCtxtUseOptions(&mut self, options: i32) -> i32 { todo!() }
    /// sax 换成本 crate 的蹦床表、_private 指向 handler，只在这次调用期间；返回前恢复原 sax 并清空 _private（:384-388）。
    pub fn xmlParseDocument(&mut self, mode: SaxMode, handler: &mut dyn ObLibXml2SaxHandler) -> i32 { todo!() }
    pub fn xmlParseContent(&mut self, mode: SaxMode, handler: &mut dyn ObLibXml2SaxHandler) { todo!() }
    // 字段访问（C++ 直接读写的那些，见 §1.5）：
    pub fn input_consumed_bytes(&self) -> u64 { todo!() }     // input->consumed + (cur - base)
    pub fn set_instate(&mut self, v: i32) { todo!() }
    pub fn set_disable_sax(&mut self, v: i32) { todo!() }
    pub fn set_recovery(&mut self, v: i32) { todo!() }
    pub fn set_str_xml_names(&mut self, xml: &'static [u8], xmlns: &'static [u8], xml_ns: &'static [u8]) { todo!() }
}
/// IS_LETTER / IS_DIGIT / IS_COMBINING / IS_EXTENDER（parserInternals.h:153-232）
pub fn IS_LETTER(c: u32) -> bool { todo!() }
pub fn IS_DIGIT(c: u32) -> bool { todo!() }
pub fn IS_COMBINING(c: u32) -> bool { todo!() }
pub fn IS_EXTENDER(c: u32) -> bool { todo!() }
pub fn assert_libxml2_version() { todo!() }                    // xmlCheckVersion(LIBXML_VERSION)
```

#### 2.3.5 `openssl_sys`

```rust
pub mod raw { /* 按目标 bindgen：63 个 libcrypto 函数、DES_cblock、DES_key_schedule、ASN1_STRING、X509_INFO 等公开结构 */ }
pub const OPENSSL_VERSION_NUMBER: u64 = 0x1010115f;              // opensslv.h:42
pub fn assert_openssl_version() { todo!() }                      // OpenSSL_version_num() == 上值
/// 静态的算法对象（EVP_aes_128_ecb() 等返回 const EVP_CIPHER*，生命周期是进程）
pub struct EVP_CIPHER { /* opaque */ }
pub struct EVP_MD { /* opaque */ }
pub fn EVP_aes_128_ecb() -> &'static EVP_CIPHER { todo!() }
// …… ecb/cbc/cfb1/cfb8/cfb128/ofb/gcm × 128/192/256、EVP_des_ede3_cbc、EVP_md4/md5/sha1/sha224/sha256/sha384/sha512/sm3
pub fn EVP_CIPHER_key_length(c: &EVP_CIPHER) -> i32 { todo!() }
pub fn EVP_CIPHER_iv_length(c: &EVP_CIPHER) -> i32 { todo!() }
pub fn EVP_CIPHER_mode(c: &EVP_CIPHER) -> u64 { todo!() }      // 宏：EVP_CIPHER_flags & EVP_CIPH_MODE（evp.h:470-471）
/// 拥有型；Drop → EVP_CIPHER_CTX_free。EVP_CIPHER_CTX_init 是 reset 的宏（evp.h:676）。
pub struct EVP_CIPHER_CTX { /* … */ }
impl EVP_CIPHER_CTX { pub fn new() -> Option<Self> { todo!() } pub fn init(&mut self) { todo!() } }
/// key/iv 的长度必须不小于算法要求，否则返回 0（C 会越界读）
pub fn EVP_EncryptInit_ex(ctx: &mut EVP_CIPHER_CTX, cipher: &EVP_CIPHER, key: Option<&[u8]>, iv: Option<&[u8]>) -> i32 { todo!() }
pub fn EVP_DecryptInit_ex(ctx: &mut EVP_CIPHER_CTX, cipher: &EVP_CIPHER, key: Option<&[u8]>, iv: Option<&[u8]>) -> i32 { todo!() }
pub fn EVP_CIPHER_CTX_set_padding(ctx: &mut EVP_CIPHER_CTX, pad: i32) -> i32 { todo!() }
/// out.len() >= inp.len() + 块大小，否则返回 0
pub fn EVP_EncryptUpdate(ctx: &mut EVP_CIPHER_CTX, out: Option<&mut [u8]>, outl: &mut i32, inp: &[u8]) -> i32 { todo!() }
pub fn EVP_DecryptUpdate(ctx: &mut EVP_CIPHER_CTX, out: Option<&mut [u8]>, outl: &mut i32, inp: &[u8]) -> i32 { todo!() }
pub fn EVP_EncryptFinal(ctx: &mut EVP_CIPHER_CTX, out: &mut [u8], outl: &mut i32) -> i32 { todo!() }
pub fn EVP_DecryptFinal_ex(ctx: &mut EVP_CIPHER_CTX, out: &mut [u8], outl: &mut i32) -> i32 { todo!() }
pub fn EVP_CIPHER_CTX_ctrl_gcm_get_tag(ctx: &mut EVP_CIPHER_CTX, tag: &mut [u8]) -> i32 { todo!() }
pub fn EVP_CIPHER_CTX_ctrl_gcm_set_tag(ctx: &mut EVP_CIPHER_CTX, tag: &[u8]) -> i32 { todo!() }
pub struct EVP_MD_CTX { /* Drop → EVP_MD_CTX_free；create/destroy 是 new/free 的宏（evp.h:541） */ }
pub fn EVP_DigestInit_ex(ctx: &mut EVP_MD_CTX, md: &EVP_MD) -> i32 { todo!() }
pub fn EVP_DigestUpdate(ctx: &mut EVP_MD_CTX, d: &[u8]) -> i32 { todo!() }
pub fn EVP_DigestFinal_ex(ctx: &mut EVP_MD_CTX, md: &mut [u8], s: &mut u32) -> i32 { todo!() }
pub fn EVP_BytesToKey(cipher: &EVP_CIPHER, md: &EVP_MD, salt: Option<&[u8; 8]>, data: &[u8], count: i32, key: &mut [u8], iv: &mut [u8]) -> i32 { todo!() }
pub type DES_cblock = [u8; 8];
#[repr(C)] pub struct DES_key_schedule { /* 128 字节，bindgen */ }
pub fn DES_set_key_unchecked(key: &DES_cblock, schedule: &mut DES_key_schedule) { todo!() }
pub fn DES_ede3_cbc_encrypt(input: &[u8], output: &mut [u8], ks1: &DES_key_schedule, ks2: &DES_key_schedule, ks3: &DES_key_schedule, ivec: &mut DES_cblock, enc: i32) { todo!() }
/// crypt(3)：ret 至少 14 字节（des.h:135）
pub fn DES_fcrypt<'a>(buf: &core::ffi::CStr, salt: &core::ffi::CStr, ret: &'a mut [u8; 14]) -> Option<&'a core::ffi::CStr> { todo!() }
pub fn RAND_bytes(buf: &mut [u8]) -> i32 { todo!() }
pub fn MD5(d: &[u8], md: &mut [u8; 16]) { todo!() }
pub fn HMAC(md: &EVP_MD, key: &[u8], d: &[u8], out: &mut [u8], out_len: &mut u32) -> bool { todo!() }
pub fn ERR_get_error() -> u64 { todo!() }
pub fn ERR_reason_error_string(e: u64) -> Option<&'static core::ffi::CStr> { todo!() }
/// 证书到期（ob_grpc_context.cpp:61-90、utility.cpp:2343-2382）：BIO、X509、STACK_OF(X509_INFO) 的拥有型句柄，
/// X509_get0_notAfter / X509_getm_notAfter 返回 ASN1_TIME 的借用视图，提供 length()/data()（C++ 直接读这两个字段）。
pub struct BIO { /* Drop → BIO_free */ }
pub struct X509 { /* Drop → X509_free */ }
pub struct X509_INFO_STACK { /* Drop → OPENSSL_sk_pop_free(X509_INFO_free)，即 sk_X509_INFO_pop_free */ }
pub struct ASN1_TIME_ref<'a> { /* &'a raw::ASN1_TIME */ }
pub fn BIO_new_file(path: &core::ffi::CStr, mode: &core::ffi::CStr) -> Option<BIO> { todo!() }
pub fn BIO_new_mem_buf<'a>(buf: &'a [u8]) -> Option<BIOMem<'a>> { todo!() }
pub struct BIOMem<'a> { /* BIO + PhantomData<&'a [u8]> */ }
pub fn PEM_read_bio_X509(bio: &mut BIO) -> Option<X509> { todo!() }
pub fn PEM_X509_INFO_read_bio(bio: &mut BIOMem<'_>) -> Option<X509_INFO_STACK> { todo!() }
pub fn X509_get0_notAfter(x: &X509) -> ASN1_TIME_ref<'_> { todo!() }
impl X509_INFO_STACK { pub fn len(&self) -> i32 { todo!() } pub fn x509(&self, i: i32) -> Option<&X509> { todo!() } }
/// C++ 的 init_ssl_malloc（ob_encryption_util.cpp:304-317）：Rust 版是空函数（§3.9.5）
pub fn init_ssl_malloc() -> i32 { 0 }
```

#### 2.3.6 `curl_sys`

```rust
pub mod raw { /* 按目标 bindgen：16 个函数、CURLMsg、CURLcode、CURLMcode、CURLINFO、CURLoption 常量；
                 curl_easy_setopt/getinfo 是 C 可变参数函数，Rust 侧按参数种类分别声明 */ }
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, Debug)] pub struct CURLcode(pub i32);
impl CURLcode { pub const CURLE_OK: CURLcode = CURLcode(0); pub const CURLE_URL_MALFORMAT: CURLcode = CURLcode(3);
                pub const CURLE_OPERATION_TIMEDOUT: CURLcode = CURLcode(28); /* … 照 curl.h */ }
pub const CURL_GLOBAL_ALL: i64 = 3;                               // SSL | WIN32（curl.h:2983-2985）
pub fn curl_global_init(flags: i64) -> CURLcode { todo!() }
pub fn curl_global_cleanup() { todo!() }
/// WRITEFUNCTION 的接收方（curl.h:276 的 typedef 名）。在 curl_easy_perform/curl_multi_perform 的线程上同步调用。
pub trait curl_write_callback { fn write(&mut self, data: &[u8]) -> usize; }
/// 拥有型 easy 句柄，'b 是交给 curl、又不被 curl 拷贝的借用（POSTFIELDS 正文、HTTPHEADER 链表、WRITEDATA 接收方）。
/// Drop → curl_easy_cleanup。Send，!Sync。
pub struct CURL<'b> { /* … */ }
pub fn curl_easy_init<'b>() -> Option<CURL<'b>> { todo!() }
/// 选项值按种类：long 选项只收 c_long（§3.9.6 的 FIXED），字符串收 &CStr（curl 会拷贝），
/// POSTFIELDS 收 &'b [u8]（不拷贝），HTTPHEADER 收 &'b curl_slist。种类与选项号（选项号 / 10000）不符时返回 CURLE_BAD_FUNCTION_ARGUMENT。
pub fn curl_easy_setopt<'b>(h: &mut CURL<'b>, opt: u32, v: CurlOptValue<'b>) -> CURLcode { todo!() }
pub enum CurlOptValue<'b> { Long(core::ffi::c_long), Str(&'b core::ffi::CStr), Bytes(&'b [u8]), Slist(&'b curl_slist) }
/// CURLOPT_WRITEFUNCTION + CURLOPT_WRITEDATA 一起设（蹦床固定，WRITEDATA 指向 sink）
pub fn curl_easy_setopt_write<'b>(h: &mut CURL<'b>, sink: &'b mut dyn curl_write_callback) -> CURLcode { todo!() }
pub fn curl_easy_perform(h: &mut CURL<'_>) -> CURLcode { todo!() }
/// CURLINFO_RESPONSE_CODE 的结果按 long 读（C++ 有一处传 int64_t*，§3.9.6）
pub fn curl_easy_getinfo_long(h: &CURL<'_>, info: u32, out: &mut core::ffi::c_long) -> CURLcode { todo!() }
/// 拥有型链表；Drop → curl_slist_free_all
pub struct curl_slist { /* … */ }
pub fn curl_slist_append(list: Option<curl_slist>, s: &core::ffi::CStr) -> Option<curl_slist> { todo!() }
/// multi 句柄拥有加进去的 easy 句柄，remove 时交还；Drop → curl_multi_cleanup
pub struct CURLM<'b> { /* … */ }
pub fn curl_multi_init<'b>() -> Option<CURLM<'b>> { todo!() }
pub fn curl_multi_add_handle<'b>(m: &mut CURLM<'b>, h: CURL<'b>) -> Result<usize, (i32, CURL<'b>)> { todo!() }
pub fn curl_multi_remove_handle<'b>(m: &mut CURLM<'b>, id: usize) -> Option<CURL<'b>> { todo!() }
pub fn curl_multi_perform(m: &mut CURLM<'_>, running: &mut i32) -> i32 { todo!() }
pub fn curl_multi_wait(m: &mut CURLM<'_>, timeout_ms: i32, numfds: &mut i32) -> i32 { todo!() }
/// CURLMSG_DONE 时给出 (句柄 id, CURLcode)
pub fn curl_multi_info_read(m: &mut CURLM<'_>, msgs_in_queue: &mut i32) -> Option<(usize, CURLcode)> { todo!() }
pub fn assert_curl_version() { todo!() }                          // curl_version_info()->version_num == 0x080c01（Android 0x080201）
```

#### 2.3.7 `libaio_sys`（只有 Linux）

```rust
#[cfg(target_os = "linux")]
pub mod raw { /* bindgen：io_context_t、struct iocb、struct io_event、timespec；5 个函数 */ }
/// 拥有型上下文；io_destroy 显式调用（C++ 照此），Drop 时若未销毁则销毁。
pub struct io_context { /* … */ }
/// 一次 AIO 请求：拥有 iocb 和数据缓冲（内核在完成前一直使用它们，所以提交时所有权移进上下文，完成时交还）。
/// B 是存储侧的 IO 缓冲类型（如 01 的 AlignedBuf）。iocb.data 存请求 id，不存指针。
pub struct AioRequest<B: AsMut<[u8]> + Send + 'static> { /* iocb, buf: B, id: u64 */ }
pub fn io_setup(nr_events: i32) -> Result<io_context, i32> { todo!() }
pub fn io_destroy(ctx: io_context) -> i32 { todo!() }
/// io_prep_pread / io_prep_pwrite：头文件里的内联函数，照 libaio.h 填 iocb 字段
pub fn io_prep_pread<B: AsMut<[u8]> + Send + 'static>(fd: i32, buf: B, offset: i64, id: u64) -> AioRequest<B> { todo!() }
pub fn io_prep_pwrite<B: AsMut<[u8]> + Send + 'static>(fd: i32, buf: B, offset: i64, id: u64) -> AioRequest<B> { todo!() }
/// 返回值照 io_submit；未被接受的请求原样交还
pub fn io_submit<B: AsMut<[u8]> + Send + 'static>(ctx: &io_context, reqs: Vec<AioRequest<B>>) -> (i32, Vec<AioRequest<B>>) { todo!() }
pub fn io_cancel(ctx: &io_context, id: u64) -> i32 { todo!() }
/// 完成的请求连同 io_event.res/res2 交还
pub fn io_getevents<B: AsMut<[u8]> + Send + 'static>(ctx: &io_context, min_nr: i64, nr: i64, timeout: Option<core::time::Duration>) -> Result<Vec<(AioRequest<B>, i64, i64)>, i32> { todo!() }
```

#### 2.3.8 `seekdb-jemalloc-sys`（构建配置）

不写新代码，只规定 `rust/Cargo.toml` 工作区和 `rust/.cargo/config.toml` 的设置，使 Rust 版构建出与 CMake 相同的 jemalloc：

```toml
# rust/port/oblib/Cargo.toml（由 01 的 feature link-jemalloc 控制是否链接）
seekdb-jemalloc-sys = { version = "=0.2.2", features = ["stats"], optional = true }

# rust/.cargo/config.toml
[env]
JEMALLOC_SYS_CONFIGURE_ARGS = "--with-jemalloc-prefix=je_"     # deps/external/cmake/Jemalloc.cmake:40
CFLAGS = "-O2 -fPIC"                                            # 同 :10；cc 也会带上，与基线参数一致
MACOSX_DEPLOYMENT_TARGET = "15.0"                               # 同 :15-35 的 -mmacosx-version-min
CC = { value = "../deps/3rd/usr/local/oceanbase/devtools/bin/clang", relative = true, force = false }
```

macOS 的 `-Wl,-u,_je_zone_register` 由 observer 的 build.rs 发出（01 文档 §2.8 末尾已写）。

### 2.4 C++ shim

通用约定：
- 每个 shim 是 `cxx::bridge`；C++ 源文件在 `rust/port/<shim>/cpp/`，从基线复制来的文件保持原文，只在文件头加一行来源注释；它们对 oblib 的依赖由 `cpp/ob_compat/` 下的小型兼容头提供（`ObString`、`ObIAllocator`（malloc 实现）、`ObArray`/`ObVector`、日志宏为空操作、`OB_*` 错误码）。错误码头由 shim 的 build.rs 从 `ob_errno.def` 生成（与 oblib 的 errno 生成器共用解析代码，§2.0）。
- 每个桥接函数的 C++ 实现都包在 `try { … } catch (...) { return <异常映射>; }` 里，返回 OB 错误码（`i32`），结果走出参；不声明 cxx 的 `Result`。
- 几何、索引等字节进出一律用 OB 的 WKB（不带 SRID 头、小端，即 `ObWkbGeom*`/`ObWkbGeog*` 视图类型的输入，`ob_geo_bin.h`）。

#### 2.4.1 `bgk_shim`

```rust
#[cxx::bridge(namespace = "seekdb::bgk")]
pub mod ffi {
    /// SRS 参数：取自 ObSrsItem（src/share/geo/ob_srs_info.h），Boost 的地理策略要用的那几项
    struct SrsParams {
        srid: u32, geographic: bool,
        semi_major_axis: f64, inverse_flattening: f64,
        prime_meridian: f64, angular_unit: f64, is_wgs84: bool,
    }
    /// ST_Buffer 的策略（ObGeoBufferStrategy）
    struct ObGeoBufferStrategy { distance: f64, point_strategy: u8, join_strategy: u8, end_strategy: u8, points_per_circle: i32 }
    unsafe extern "C++" {
        include!("bgk_shim/cpp/bgk_entries.h");
        // ---- 谓词：返回 OB 错误码，结果写 out（bool）----
        fn covered_by(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn within(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn intersects(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn disjoint(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn equals(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn touches(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn crosses(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn overlaps(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        fn relate(a: &[u8], b: &[u8], srs: &SrsParams, mask: &[u8], out: &mut bool) -> i32;
        fn is_valid(g: &[u8], srs: &SrsParams, out: &mut bool) -> i32;
        // ---- 数值 ----
        fn area(g: &[u8], srs: &SrsParams, out: &mut f64) -> i32;
        fn length(g: &[u8], srs: &SrsParams, out: &mut f64) -> i32;
        fn distance(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut f64) -> i32;
        fn distance_sphere(a: &[u8], b: &[u8], radius: f64, out: &mut f64) -> i32;
        // ---- 构造：WKB 出（由复制进来的 OB WKB 写出逻辑生成，ob_geo_wkb_visitor）----
        fn buffer(g: &[u8], srs: &SrsParams, strategy: &ObGeoBufferStrategy, out: &mut Vec<u8>) -> i32;
        fn difference(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn union_(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn intersection(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn sym_difference(a: &[u8], b: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn centroid(g: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn envelope(g: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        fn dissolve_polygon(g: &[u8], srs: &SrsParams, out: &mut Vec<u8>) -> i32;
        /// bg::correct 就地改写 WKB 视图里的点（ob_geo_func_covered_by1.cpp:65）
        fn correct(g: &mut [u8], srs: &SrsParams) -> i32;
        fn transform(g: &[u8], src: &SrsParams, dst: &SrsParams, out: &mut Vec<u8>) -> i32;
        // ---- SRS WKT（Boost.Spirit，ob_srs_wkt_parser.cpp）：解析结果按字段平铺进 out，由 Rust 组装 ObSrsInfo ----
        fn parse_srs_wkt(srid: u64, srs_str: &[u8], out: &mut Vec<f64>, names: &mut Vec<u8>, kind: &mut u8) -> i32;
        // ---- R 树（ob_geo_rstar_tree.h）：按需加入 ----
    }
}
```

入口清单不手写：1d 用脚本扫 716 个 `OB_GEO_*_FUNC_BEGIN` 特化体里的 `bg::`/`boost::geometry::` 调用，得到 `(算法, 坐标系, 类型 1, 类型 2, 结果)` 五元组，写成 `rust/port/bgk_shim/entries.tsv`；`bgk_entries.cpp` 由 shim 的 build.rs 按这张表生成：每个桥接函数先读 WKB 头的类型和坐标系，按表里列出的类型对实例化 Boost 模板，表外的组合返回与 C++ 分派器相同的错误码（`ob_geo_dispatcher.h` 里对应分支的值）。原来 `ob_geo_func_*.cpp` 的编排逻辑（集合拆分、子几何构造、`correct` 前的拷贝、结果整理）按普通单元翻译成 Rust，只把 `bg::X(...)` 换成上面的桥接调用。

#### 2.4.2 `s2_shim`

```rust
#[cxx::bridge(namespace = "seekdb::s2")]
pub mod ffi {
    struct S2Bound { /* ObSrsBoundsItem 的 4 个 double */ minx: f64, miny: f64, maxx: f64, maxy: f64 }
    unsafe extern "C++" {
        include!("s2_shim/cpp/s2_entries.h");
        type ObS2Adapter;                                         // 复制进 shim 的 C++ 类
        fn new_s2_adapter(is_geog: bool, buffer: bool, max_level: i32, max_cells: i32) -> UniquePtr<ObS2Adapter>;
        fn init(self: Pin<&mut ObS2Adapter>, wkb: &[u8], bound: &S2Bound, has_bound: bool) -> i64;
        fn get_cellids(self: Pin<&mut ObS2Adapter>, cells: &mut Vec<u64>, is_query: bool) -> i64;
        fn get_cellids_and_unrepeated_ancestors(self: Pin<&mut ObS2Adapter>, cells: &mut Vec<u64>, ancestors: &mut Vec<u64>) -> i64;
        fn get_inner_cover_cellids(self: Pin<&mut ObS2Adapter>, cells: &mut Vec<u64>) -> i64;
        fn get_mbr(self: Pin<&mut ObS2Adapter>, mbr: &mut [f64; 4]) -> i64;
        fn get_ancestors(self: Pin<&mut ObS2Adapter>, cell: u64, cells: &mut Vec<u64>) -> i64;
        fn get_child_of_cellid(id: u64, child_start: &mut u64, child_end: &mut u64);
    }
}
```

返回值照 C++ 是 `int64_t`（`ob_s2adapter.h:157-162`）。`ObSpatialMBR` 里只靠 s2 的部分（`generate_latlng_rect`）放进 shim，其余按普通单元翻译。

#### 2.4.3 `vsag_shim`

```rust
#[cxx::bridge(namespace = "seekdb::vsag")]
pub mod ffi {
    unsafe extern "C++" {
        include!("vsag_shim/cpp/vsag_entries.h");
        type VectorIndex;                                         // obvsag::VectorIndexPtr 指向的对象
        // ob_vector_util.h:57-147 的约 25 个函数，名字照 obvsag::，返回 OB 错误码
        fn create_index(index: &mut UniquePtr<VectorIndex>, index_type: i32, dtype: &str, metric: &str, dim: i32, max_degree: i32,
                        ef_construction: i32, ef_search: i32, allocator: usize, extra_info_size: i32) -> i32;
        fn build_index(index: Pin<&mut VectorIndex>, vectors: &[f32], ids: &[i64], dim: i32, extra_info: &[u8]) -> i32;
        fn add_index(index: Pin<&mut VectorIndex>, vectors: &[f32], ids: &[i64], dim: i32, extra_info: &[u8]) -> i32;
        fn get_index_number(index: &VectorIndex, size: &mut i64) -> i32;
        fn knn_search(index: Pin<&mut VectorIndex>, query: &[f32], dim: i32, topk: i64, dist: &mut Vec<f32>, ids: &mut Vec<i64>,
                      ef_search: i32, filter: usize, iter_ctx: &mut usize, is_last: bool) -> i32;
        fn fserialize(index: Pin<&mut VectorIndex>, buf: &mut [u8], cb: usize) -> i32;   // cb：Rust 回调的令牌（见下）
        fn fdeserialize(index: &mut UniquePtr<VectorIndex>, cb: usize) -> i32;
        fn delete_index(index: &mut UniquePtr<VectorIndex>) -> i32;
        // …… 其余照 ob_vector_util.h
    }
    extern "Rust" {
        // 分配器与日志、流：C++ 侧的 vsag::Allocator / vsag::Logger / std::streambuf 子类回调到这里（蹦床，第一层）
        fn vsag_allocate(ctx: usize, size: u64) -> usize;
        fn vsag_deallocate(ctx: usize, p: usize);
        fn vsag_reallocate(ctx: usize, p: usize, size: u64) -> usize;
        fn vsag_log(level: i32, msg: &[u8]);
        fn ostream_buf_callback(cb: usize, data: &[u8]) -> i32;
        unsafe fn istream_buf_callback(cb: usize, capacity: i64, data: &mut *const u8, read_size: &mut i64) -> i32;
    }
}
/// vsag 的分配器（C++：ObVsagMemContext : vsag::Allocator，ob_vector_allocator.h:71-118），由 storage 实现。
/// vsag 会从自己的线程回调（set_parallel(8)），所以 Sync，且不得访问非 const 初始化的线程局部（RULEBOOK 0.5、0.14）。
pub trait Allocator: Sync {
    fn Allocate(&self, size: u64) -> Option<oblib::mem::ForeignBlock>;
    fn Deallocate(&self, block: oblib::mem::ForeignBlock);
    fn Reallocate(&self, block: oblib::mem::ForeignBlock, size: u64) -> Result<oblib::mem::ForeignBlock, oblib::mem::ForeignBlock>;
}
/// C++：obvsag::VectorIndexPtr。持有索引和它的分配器（vsag 在索引里保存分配器指针）；
/// 字段顺序保证先销毁索引、再放掉分配器。安全层的 create_index 等照 ob_vector_util.h 的名字，收 &mut VectorIndexPtr。
pub struct VectorIndexPtr { index: cxx::UniquePtr<ffi::VectorIndex>, allocator: Option<std::sync::Arc<dyn Allocator>> }
pub fn create_index(index: &mut VectorIndexPtr, index_type: i32, dtype: &str, metric: &str, dim: i32, max_degree: i32,
                    ef_construction: i32, ef_search: i32, allocator: Option<std::sync::Arc<dyn Allocator>>, extra_info_size: i32) -> i32 { todo!() }
/// 流：C++ 是 std::ostream/istream 包着 ObOStreamBuf/ObIStreamBuf（ob_vector_index_serialize.h:51-94），
/// 两者的回调是 ObFunction（:56、:89）。streambuf 子类留在 shim 里，回调改成 Rust 实现的 trait，
/// 用模块名对应 C++ 的嵌套名 ObOStreamBuf::Callback、ObIStreamBuf::Callback。回调在调用 fserialize 的线程上同步发生。
#[allow(non_snake_case)]
pub mod ObOStreamBuf {
    /// int(const char *data, const int64_t len, CbParam &)：缓冲写满或结束时交出一段数据，返回 OB 错误码
    pub trait Callback { fn call(&mut self, data: &[u8]) -> i32; }
}
#[allow(non_snake_case)]
pub mod ObIStreamBuf {
    /// int(char *&data, const int64_t capacity, int64_t &read_size, CbParam &)。唯一的实现
    /// （ObHNSWDeserializeCallback，src/observer/vector_index/ob_plugin_vector_index_serialize.cpp:242-268）
    /// 不拷贝，把 data 换成自己当前块的地址、忽略 capacity；所以这里返回借来的切片，空切片表示结束（data = nullptr、read_size = 0）。
    /// 切片在下一次 call 之前有效，shim 把 streambuf 的 get 区设到它上面。
    pub trait Callback { fn call(&mut self, capacity: i64) -> Result<&[u8], i32>; }
}
/// C++：obvectorutil::fserialize(index, std::ostream&)（ob_vector_util.h:142）；buf 是 ObOStreamBuf 的缓冲（C++ 的 data/capacity）
pub fn fserialize(index: &mut VectorIndexPtr, buf: &mut [u8], cb: &mut dyn ObOStreamBuf::Callback) -> i32 { todo!() }
pub fn fdeserialize(index: &mut VectorIndexPtr, cb: &mut dyn ObIStreamBuf::Callback) -> i32 { todo!() }
```

#### 2.4.4 `regex_shim`

```rust
#[cxx::bridge(namespace = "seekdb::regex")]
pub mod ffi {
    unsafe extern "C++" {
        include!("regex_shim/cpp/regex_shim.h");
        type StdRegex;                                            // std::regex（ECMAScript 方言）
        /// std::regex(pattern, len[, icase])；构造抛 regex_error → status = 1（ob_json_schema.cpp:4014-4019 的判断）
        fn std_regex_new(pattern: &[u8], icase: bool, status: &mut i32) -> UniquePtr<StdRegex>;
        /// regex_search(const char*, re)：主体是 C 字符串，遇第一个 NUL 截断（:4037-4039）
        fn std_regex_search_cstr(re: &StdRegex, subject: &[u8]) -> bool;
        /// regex_search(std::string, smatch, re)：按长度，保留 NUL；给出各捕获组的 (起点, 长度)，未参与的组为 (-1, 0)
        fn std_regex_search(re: &StdRegex, subject: &[u8], start: usize, groups: &mut Vec<i64>) -> bool;
        fn std_regex_match(re: &StdRegex, subject: &[u8]) -> bool;
        fn std_regex_replace(subject: &[u8], re: &StdRegex, fmt: &[u8], out: &mut Vec<u8>) -> i32;
        type PosixRegex;                                          // regex_t
        /// regcomp(pattern 以 NUL 结尾, cflags)；返回 regcomp 的返回值
        fn posix_regcomp(pattern: &[u8], cflags: i32, out: &mut UniquePtr<PosixRegex>) -> i32;
        fn posix_regexec(re: &PosixRegex, subject: &[u8], eflags: i32) -> i32;
    }
}
pub const REG_EXTENDED: i32 = 1;   // 按平台的 <regex.h>，由 build.rs 生成
pub const REG_NOSUB: i32 = 0;      // 同上（值随平台不同）
```

### 2.5 生成器

#### 2.5.1 errno（`rust/port/oblib/build.rs`）

输出的形状由 04 规定（04 文档 §2.1：`generated/ob_errno.rs` 里的常量、`g_all_ob_errnos`、11 个查表函数、`__USER_ERROR_MSG` 常量、`user_msg::OB_X`；`generated/mysql_errno.rs`）。本模块规定生成器：

```rust
// rust/port/oblib/build/errno_def.rs —— oblib 与 bgk_shim 的 build.rs 用 include! 共享
/// ob_errno.def 的一条（gen_errno.pl:23-33 的 store_error）
pub struct ErrnoDef {
    pub name: String, pub code: i32, pub mysql_errno: MysqlErrno, pub sqlstate: Vec<u8>,
    pub str_error: Vec<u8>, pub str_user_error: Vec<u8>, pub cause: Vec<u8>, pub solution: Vec<u8>,
    pub dep: bool,                                   // _DEP 系列：原来进 oblib/lib/ob_errno.h
    pub line: usize,
}
pub enum MysqlErrno { Raw(i32), Symbol(String, i32) } // ER_* 符号及其值（mysql_errno.h）
pub struct OtherMsgFmt { pub name: String, pub of: String, pub fmt: Vec<u8>, pub fmt2: Vec<u8> }
/// 用 gen_errno.pl:35-79 的同一组正则逐行匹配（含其局限：字符串字面量里不能有 `"`）；C 字面量按 C 编译器规则反转义，
/// 遇到未知转义直接 panic。
pub fn parse_errno_def(text: &str, mysql_errno_h: &str) -> (Vec<ErrnoDef>, Vec<OtherMsgFmt>) { todo!() }
/// printf 规格 → 参数种类（04 §3.2.6 的表）；表外的规格（含宽度、* 宽度、%x 等）panic
pub fn user_msg_params(fmt: &[u8]) -> Vec<ParamKind> { todo!() }
pub enum ParamKind { PrecisionInt, Str, CLong, Int, UInt, CULong, LongLong, Double, Char }
```

- 失败条件（build.rs `panic!`）：错误码重复（`gen_errno.pl:82-93` 的检查，`OB_ERR_ARGUMENT_SHOULD_CONSTANT_OR_GROUP_EXPR` 这种同名同值的重复定义只告警，04 文档 §1.3）；M1 是未知的 `ER_*` 符号；消息不是合法 UTF-8（04 把消息表定为 `&'static str`）；出现表外的 printf 规格。
- 输入：`src/share/ob_errno.def`、`src/share/mysql_errno.h`；`cargo:rerun-if-changed` 只列这两个。
- 共存校验：见 §2.6.5。

#### 2.5.2 `ObItemType`（`rust/port/query/build.rs`）

```rust
// 生成物 rust/port/query/src/generated/item_type.rs；query 的 item_type 单元（ob_item_type 的桩）pub use 它
/// C++：enum ObItemType（query 那份 ob_item_type.h:27 起）。RULEBOOK 0.13、§2.12。
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct ObItemType(pub i32);
impl ObItemType {
    pub const T_INVALID: ObItemType = ObItemType(-1);
    pub const T_NULL: ObItemType = ObItemType(0);
    // …… 2,019 个，加 item_type_divergence.tsv 里判定为 emit 的 objit 独有值
    pub const T_MAX: ObItemType = ObItemType(4921);
}
pub const T_INVALID: ObItemType = ObItemType::T_INVALID;
// …… 同名模块级常量，随 query 的 crate 根导出（引用方照 C++ 不加限定地写 T_INT）
/// get_type_name（gen_type_name.sh:14-18 生成的 type_name.c）：只收录 sed 正则能匹配的枚举值，其余 "Unknown"。
pub fn get_type_name(type_: i32) -> &'static str { todo!() }
/// ob_item_type.h:2460-2472 的两个打印特化："\"%s\"" 与 key:"%s"
impl oblib::fmt::ObToString for ObItemType { /* … */ }
```

生成器规则：
1. 只解析 query 那份（解析器和名字表实际看到的那份，§1.4），按 C 规则求值（显式值或前一项加一），注释整段去掉。
2. 同时解析 objit 那份，算出差异集合，与 `migration/design/item_type_divergence.tsv`（列：`name	query_value	objit_value	decision	note`；`decision` ∈ `emit`/`omit`/`keep_query`）逐行比对：差异集合多出或少了任何一行就 `panic!("ObItemType copies diverge: …")`。基线的 6 行（§1.4）由清单第 23 类填写 `decision`；`emit` 的 objit 独有值只有在不与 query 那份的任何值冲突时才允许。
3. `get_type_name` 的收录规则逐字复刻 sed 正则 `\s*(T_[A-Z0-9_]+)[ =0-9]*,`：按行匹配，取行内第一个匹配；所以带负号的 `T_INVALID` 和没有逗号的 `T_MAX` 不收录。
4. `migration/design/item_type_tu.tsv`（1d 的一次性脚本产出，清单第 23 类的输入）：对 `compile_commands.json` 的每个编译单元跑 `clang -E -H`，记下先被包含的是哪一份；基线已知至少 `unity_ob_sql_ob_sql_engine_expr_13` 是 objit 那份（§1.4）。

#### 2.5.3 系统变量（`share/build.rs` 与 `sql/build.rs`）

```rust
// share/src/generated/sys_var_meta.rs —— 对应 share/system_variable 下的 6 个生成文件
#[repr(transparent)] #[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct ObSysVarClassType(pub i32);                            // ob_sys_var_class_type.h
pub const SYS_VAR_AUTO_INCREMENT_INCREMENT: ObSysVarClassType = ObSysVarClassType(0);
// …… 776 个，加 SYS_VAR_INVALID 等照原头文件
pub mod ObSysVarFlag { pub const NONE: i64 = 0; /* GLOBAL_SCOPE、SESSION_SCOPE、NEED_SERIALIZE … 照 ob_system_variable_init.h */ }
pub const OB_SV_AUTO_INCREMENT_INCREMENT: &str = "auto_increment_increment";   // ob_system_variable_alias.h
/// ObSysVarFromJson（ob_system_variable_init.h:43-63）：静态表，钩子名照 C++ 字符串原样保留（ob_system_variable_init.cpp:139-140）
pub static ObSysVars: [ObSysVarFromJson; 776] = [ /* … */ ];
pub static ESSENTIAL_SYS_VARS: [ObSysVarClassType; N] = [ /* … */ ];
// ObSysVarMeta 的查表函数（ob_sys_var_meta.h），名字照抄
```

```rust
// sql/src/generated/sys_var_factory.rs —— 对应 sql/session/ob_system_variable_factory.{h,cpp}
// 每个变量一个 struct（RULEBOOK §2.13：数据 struct + Virt trait），例如 ob_system_variable_factory.h:54：
pub struct ObSysVarCharacterSetClient { pub base: ObCharsetSysVar }
impl ObSysVarCharacterSetClient {
    pub fn new() -> Self { Self { base: ObCharsetSysVar::new(
        Some(ObSysVarOnCheckFuncs::check_and_convert_charset_not_null), None,
        Some(ObSysVarToObjFuncs::to_obj_charset), Some(ObSysVarToStrFuncs::to_str_charset),
        Some(ObSysVarGetMetaTypeFuncs::get_meta_type_varchar)) } }
}
impl ObBasicSysVarVirt for ObSysVarCharacterSetClient { /* get_type()、get_meta_type()… 照生成的 C++ */ }
// ObSysVarFactory 的按 id / 名字创建表
```

- 钩子映射表 `rust/port/sql/build/sysvar_hooks.tsv`（列：`cpp_name	rust_path	kind`），随 build.rs 入库。JSON 里 6 个键共 94 处、42 个不同的函数名（`on_check_and_convert_func` 35 处 23 个、`get_meta_type_func` 15/1、`to_select_obj_func` 16/5、`to_show_str_func` 16/5、`session_special_update_func` 6/4、`on_update_func` 6/4）。这 6 个类都在 `src/sql/session/ob_system_variable.h:775,890,916,936,956,967`，所以 Rust 路径是 `crate::ObSysVar*Funcs::<方法名>`（§4 的路径规则；重名或重载时按 `overloads.tsv` 改名，映射表由脚本从 `overloads.tsv` 生成）。JSON 里出现表外的钩子名 → build.rs panic。
- share 不引用 sql：share 的元数据里钩子只是字符串，函数指针只出现在 sql 的工厂里（与 C++ 相同的分层，见 §8.2 A4）。
- JSON 用 `serde_json` 读（白名单：只作 build-dependency）。

#### 2.5.4 proto 与版本号

```rust
// rust/port/standby/build.rs
// tonic_build::configure().build_server(true).build_client(true)
//     .out_dir("src/generated").compile(&["<repo>/src/oblib/grpc/standbyservice.proto"], &["<repo>/src/oblib/grpc"])
// protoc：PROTOC=<dep_dir>/bin/protoc（deps/3rd 里的 protoc 3.19.5）；产物 src/generated/standbyservice.rs
//
// rust/port/share/build.rs
// prost_build::Config::new().out_dir("src/generated").compile_protos(&["<repo>/src/share/geo/ob_vector_tile.proto"], …)
// 产物 src/generated/vector_tile.rs
```

```rust
// rust/port/share/build.rs 发出的 cargo:rustc-env（src/share/ob_version.cpp.in:34-50 的替代）
// OB_PACKAGE_NAME="OceanBase"、OB_PACKAGE_VERSION（读 CMakeLists.txt:10 的 VERSION）、OB_PACKAGE_STRING、OB_RELEASEID（= BUILD_NUMBER）
// OB_BUILD_VERSION="<BUILD_NUMBER>-<git rev-parse HEAD>"、OB_BUILD_BRANCH（git rev-parse --abbrev-ref HEAD）
// OB_BUILD_FLAGS、OB_BUILD_INFO（CMake 从未赋值，恒为空串）、OB_BUILD_DATE（__DATE__ 格式 "Mmm dd yyyy"，日不足两位补空格）、OB_BUILD_TIME（"hh:mm:ss"）
// share 的 ob_version 单元：
pub fn build_version() -> &'static str { env!("OB_BUILD_VERSION") }
pub fn build_date() -> &'static str { env!("OB_BUILD_DATE") }
pub fn build_time() -> &'static str { env!("OB_BUILD_TIME") }
pub fn build_flags() -> &'static str { env!("OB_BUILD_FLAGS") }
pub fn build_branch() -> &'static str { env!("OB_BUILD_BRANCH") }
pub fn build_info() -> &'static str { env!("OB_BUILD_INFO") }
pub const PACKAGE_VERSION: &str = env!("OB_PACKAGE_VERSION");
```

#### 2.5.5 bitpacking（`rust/port/oblib/build.rs`）

```rust
// 生成物：rust/port/oblib/src/simd/bitpacking/gen_scalar.rs、gen_simd.rs（RULEBOOK §3 第 8 项的白名单路径）
// 由 build.rs 里移植的两个 Python 生成器逻辑写出，函数名照原生成物：
pub fn scalar_fastpackwithoutmask_8_32_count(input: &[u8; 32], out: &mut [u32], bit: u32) { todo!() }
pub fn scalar_fastunpack_8_32_count(input: &[u32], out: &mut [u8; 32], bit: u32) { todo!() }
pub fn scalar_fastpackwithoutmask_16_32_count(input: &[u16; 32], out: &mut [u32], bit: u32) { todo!() }
pub fn scalar_fastunpack_16_32_count(input: &[u32], out: &mut [u16; 32], bit: u32) { todo!() }
pub fn scalar_fastpackwithoutmask_32(input: &[u32; 32], out: &mut [u32], bit: u32) { todo!() }
pub fn scalar_fastunpack_32(input: &[u32], out: &mut [u32; 32], bit: u32) { todo!() }
pub fn scalar_fastpackwithoutmask_64(input: &[u64; 64], out: &mut [u64], bit: u32) { todo!() }
pub fn scalar_fastunpack_64(input: &[u64], out: &mut [u64; 64], bit: u32) { todo!() }
/// uSIMD_*（ob_generated_unalign_simd_bp_func.h:33-41）：128 个值、4 路交错；out 至少 4 * bit 个 u32
pub fn uSIMD_fastpackwithoutmask_128_16(input: &[u16; 128], out: &mut [u32], bit: u32) { todo!() }
pub fn uSIMD_fastunpack_128_16(input: &[u32], out: &mut [u16; 128], bit: u32) { todo!() }
pub fn uSIMD_fastpackwithoutmask_128_32(input: &[u32; 128], out: &mut [u32], bit: u32) { todo!() }
pub fn uSIMD_fastunpack_128_32(input: &[u32], out: &mut [u32; 128], bit: u32) { todo!() }
```

函数体里的内建函数在 x86-64 上就是 `core::arch::x86_64::_mm_*`（SSE2 是 x86-64 的基线，不需要运行时检测）；aarch64 上用 `oblib::simd::sse_to_neon` 里同名的 Rust 函数，每个都照 `ob_sse_to_neon.h:33-79` 的映射用 `core::arch::aarch64` 的 NEON 指令实现（如 `_mm_srli_epi32(a, n)` = `vshlq_u32(a, vdupq_n_s32(-n))`）。所以两种架构的生成代码文本相同。长度不足时 `assert!`（C 里是越界写）。

#### 2.5.6 内部表

```rust
// 新 Python 发射器 src/share/inner_table/rust_emitter.py，generate_inner_table_schema.py 加两个选项：
//   --rust-share-output-dir <dir>  --rust-observer-output-dir <dir>
// share 和 observer 的 build.rs 用 `python3 -I` 运行它（CMake 本来就要求 Python3，CMakeLists.txt:31）。
// 生成物：share/src/generated/inner_table/{constants.rs, tables.rs, misc.rs, lob.rs, table_id_to_name}
//         observer/src/generated/virtual_table/sqlite_tables.rs
pub const OB_ALL_CORE_TABLE_TID: u64 = 1;                       // ob_inner_table_schema_constants.h
pub const OB_ALL_CORE_TABLE_TNAME: &str = "__all_core_table";
/// 一张表：ob_inner_table_schema.1_50.cpp:31-61 那些 set_* 的值
pub struct InnerTableDef {
    pub table_id: u64, pub database_id: u64, pub table_name: &'static str, pub rowkey_split_pos: i64,
    pub progressive_merge_num: i64, pub rowkey_column_num: i64, pub load_type: i32, pub table_type: i32,
    pub index_type: i32, pub def_type: i32, pub part_level: i32, pub compress_func_name: Option<&'static str>,
    pub view_definition: Option<&'static str>, pub columns: &'static [InnerColumnDef], /* …照 DSL 的全部表级属性 */
}
/// 一列：ADD_COLUMN_SCHEMA* 的实参（ob_schema_macro_define.h:25-290 的 8 种宏）
pub struct InnerColumnDef {
    pub macro_kind: AddColumnMacro, pub name: &'static str, pub rowkey_id: i64, pub index_id: i64, pub part_key_pos: i64,
    pub column_type: i32, pub collation_type: i32, pub length: i64, pub precision: i64, pub scale: i64,
    pub nullable: bool, pub autoincrement: bool, pub on_update_for_timestamp: bool,
    pub default: ColumnDefault, pub column_flags: i64,
}
pub enum AddColumnMacro { Plain, WithFlags, T, TWithFlags, TsT, TsTWithFlags, Ts, TsWithFlags }
pub enum ColumnDefault { None, Null, NowFlag, Int(i64), Str(&'static str), /* …DSL 里出现的种类 */ }
pub static ALL_CORE_TABLE_DEF: InnerTableDef = InnerTableDef { /* … */ };
/// 原来的每表函数保留名字（ob_inner_table_schema.h），函数体只调通用构建函数
impl ObInnerTableSchema { pub fn all_core_table_schema(table_schema: &mut ObTableSchema) -> ObResult { build_inner_table_schema(&ALL_CORE_TABLE_DEF, table_schema) } }
```

通用构建函数 `build_inner_table_schema(def: &InnerTableDef, table_schema: &mut ObTableSchema) -> ObResult` 不由生成器写：它是 `ob_schema_macro_define.h` 那 8 个宏的逐行翻译（展开顺序、每步的错误码与日志照宏），放在该单元的译文里。列类型、排序规则、各种 ID 用整数存（`*_data` 式的无依赖数据），由构建函数转成 `ObObjType` 等新类型。

#### 2.5.7 被删除的生成器的替代

- str_datum / str_cmp：一次性脚本 `migration/scripts/gen_coll_lists_rs.py` 读两个生成器的 `DEFINED_COLLS`，写出两个入库文件，各含一个按原顺序列出排序规则的宏；`__init_all_str_funcs`、`__init_all_str_expr_cmp_func` 按函数级重映射放进调用它们的单元（`ob_datum_funcs`、`ob_expr_cmp_func`）的译文，用 `ob_include_generated!` 引入宏再展开。每个排序规则 `C` 的步骤：str_datum 是 `str_cmp_initer<C>::init_array()`、`str_basic_initer<C, 0>::init_array()`、`str_basic_initer<C, 1>::init_array()` 三步（`.ipp:32-38`，这个 `.ipp` 不是单元，译者看不到，所以写在这里）；str_cmp 是 `_common.ipp` 的 `INIT_COMPILE_STR_FUNC(C)`。1c 把这两条重映射写进 `seams.tsv`。

```rust
// rust/port/share/src/generated/str_datum_colls.rs
macro_rules! for_each_str_datum_coll { ($m:ident) => { $m!(CS_TYPE_BINARY); $m!(CS_TYPE_UTF8MB4_GENERAL_CI); $m!(CS_TYPE_UTF8MB4_BIN); }; }
// rust/port/sql/src/generated/str_cmp_colls.rs：167 个，顺序照 gen_expr_str_cmp_func.py 的 DEFINED_COLLS（等于 part_0 到 part_7 依次展开）
macro_rules! for_each_str_cmp_coll { ($m:ident) => { $m!(CS_TYPE_GBK_CHINESE_CI); $m!(CS_TYPE_UTF8MB4_GENERAL_CI); /* … */ $m!(CS_TYPE_SWE7_BIN); }; }
```
- `gen_type_name.sh`：`get_type_name` 由 §2.5.2 产出。
- syspack：`rust/port/sql/src/generated/syspack.rs`（一次性脚本 `migration/scripts/gen_syspack_rs.py` 从 `syspack_codegen.py` 的清单生成，入库）：

```rust
pub struct ObSysPackageFile { pub package_name: &'static str, pub package_spec_file_name: &'static str, pub package_body_file_name: &'static str }
pub static mysql_syspack_file_list: [ObSysPackageFile; 13] = [ /* 顺序照 syspack_source.cpp:16-30 */ ];
pub static mysql_special_syspack_file_list: [ObSysPackageFile; 0] = [];
pub static syspack_source_contents: [(&str, &str); 26] = [
    ("dbms_stats_mysql.sql", include_str!("../../../../../src/share/inner_table/sys_package/dbms_stats_mysql.sql")),
    // ……
];
```

#### 2.5.8 数据转换脚本（`*_data`）

脚本放 `migration/scripts/data/`，一次性运行，产物入库到 `rust/port/<crate>_data/`；CI 用 `--check` 模式重跑并比对。数据 crate 不依赖任何移植 crate（RULEBOOK 0.3），按表选三种编码之一，选择记在 `migration/design/data_tables.tsv`（列：`source	symbol	encoding	target	elem_type	count`）：

```rust
// E1：元素是 OB 类型（枚举新类型、结构体）。数据 crate 导出一个 macro_rules!，由代码单元传入元素类型的路径展开，
//     保留符号名、由编译器在代码 crate 里核对。例：rust/port/sql_data/src/engine/expr/ob_expr_abs_result_type.rs
#[macro_export]
macro_rules! ABS_RESULT_TYPE { ($T:path) => { [ <$T>::ObDoubleType, <$T>::ObIntType, /* … */ ] }; }
//     代码单元（ob_expr_result_type_util 的译文）：
//     pub static ABS_RESULT_TYPE: [ObObjType; ObMaxType as usize] = sql_data::ABS_RESULT_TYPE!(ObObjType);
//     结构体表同理：time_zone_trans_table!(ObTimeZoneTrans) 展开成 [ObTimeZoneTrans { trans_: i64::MIN, offset_: 0, tz_id: 0 }, …]
//
// E2：大块字符串表。NUL 分隔的二进制块加条数，数据 crate 自带一个无依赖的小视图类型。
pub struct RawStrTable { pub blob: &'static [u8], pub count: usize }
impl RawStrTable { pub fn iter(&self) -> impl Iterator<Item = &'static [u8]> { core::iter::empty() } }
pub static IK_MAIN_DIC: RawStrTable = RawStrTable { blob: include_bytes!("ik/main_dic.bin"), count: 0 /* 实际条数 */ };
//
// E3：基本类型数组，直接 pub static，代码单元 pub use。
pub static CTYPE_BIN: [u8; 257] = [ /* … */ ];
```

### 2.6 `generated/` 目录契约

#### 2.6.1 位置与写入方式

- 一律在 `rust/port/<crate>/src/generated/`（RULEBOOK 0.13、§4），唯一例外是 bitpacking 在 `rust/port/oblib/src/simd/bitpacking/`（要落在 §3 第 8 项的 unsafe 白名单路径里）。
- **build.rs 的产物也写进这个目录并入库**，先例是 `rust/config/build.rs:404-411` 把 cbindgen 输出写到 `rust/config/include/config.h`、`rust/sql-nio/build.rs:21-24` 写 `include/nio.h`，两者都入库。这样第 3 步的译者和审查者不编译也能读到生成的名字和签名。
  - 用 `write_if_changed`（内容相同不写，写时原子替换），`cargo:rerun-if-changed` 只列输入，不列输出，所以不会循环重建；
  - CI 在构建后跑 `git diff --exit-code -- 'rust/port/*/src/generated' rust/port/oblib/src/simd/bitpacking`，生成物过期即失败；
  - 与目标平台有关的生成物按目标分文件（解析器和 -sys 的 bindgen 产物），由 `cfg` 选择；与平台无关的只有一份。
- 生成物不进 manifest；生成物里不得出现 `unsafe`，除了 bitpacking 和 -sys/shim/parser 的 bindgen 产物（这些路径本来就在白名单里）。

#### 2.6.2 文件头

```text
// @generated by <生成器>（如 rust/port/oblib/build.rs:errno）
// inputs: src/share/ob_errno.def sha256=<…>, src/share/mysql_errno.h sha256=<…>
// DO NOT EDIT
```

CI 另有一个不用构建的快速检查：重算输入的 sha256，与文件头比对。

#### 2.6.3 引入方式

```rust
// oblib 提供（rust/port/oblib/src/generated_support.rs，#[macro_export]）：
/// 在任意单元里引入本 crate 的 src/generated/<f>；env! 在展开处求值，所以是调用方 crate 的目录
#[macro_export]
macro_rules! ob_include_generated {
    ($f:literal) => { include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/generated/", $f)); };
}
```

- 注册表单元（RULEBOOK §2.10）：单元照常翻译，注册表本身写成 `oblib::ob_include_generated!("expr_op_registry.rs");`（item 位置或表达式位置都可以）。
- 其余生成物由各 crate 的 `src/generated/mod.rs` 声明为模块（`mod.rs` 由模块树脚本生成），crate 根 `pub use generated::<name>::*;` 或由对应单元的桩 `pub use crate::generated::<name>::*;`。

#### 2.6.4 文件清单

| crate | 文件 | 生成者 | 引入方式 |
|---|---|---|---|
| 每个移植 crate | `generated/mod.rs` | 模块树脚本（1c/1d） | `lib.rs` 的 `mod generated;` |
| 每个有槽位类型的 crate | `generated/slots.rs`：`pub static <NAME>_SLOT: oblib::slot::Slot<…> = Slot::new();`，每个 `slots.tsv` 行一条，放在类型所在的 crate | `migration/scripts/gen_slots.py`（读 `slots.tsv`） | crate 根 `pub use generated::slots::*;` |
| `observer` | `generated/bindings.rs`：`pub fn bind_all(mods: &Modules)`，按 `obs_construct_modules` 的顺序绑定 98 个槽位（`ob_server_runtime_controller.cpp:1248-1357`）再加 standby 的 2 个；另 `pub fn run_static_init_side_effects()`，按原版二进制的初始化顺序执行有副作用的静态初始化（RULEBOOK 0.4、0.5） | `migration/scripts/gen_bindings.py` | 组合根单元在 C++ 对应位置调用 `generated::bind_all(…)` |
| `observer` | `src/bin/seekdb.rs` | 模块树脚本 | 二进制入口（§4） |
| `oblib` | `generated/ob_errno.rs`、`generated/mysql_errno.rs` | oblib build.rs | 04：`include!("../generated/ob_errno.rs")` |
| `oblib` | `generated/errsim_registry.rs` | 04 的脚本 | 04 |
| `oblib` | `simd/bitpacking/gen_scalar.rs`、`gen_simd.rs` | oblib build.rs | `simd::bitpacking` 模块 `mod gen_scalar; mod gen_simd;` |
| `query` | `generated/item_type.rs` | query build.rs | crate 根 `pub use generated::item_type::*;` |
| `share` | `generated/sys_var_meta.rs`、`generated/vector_tile.rs`、`generated/inner_table/*.rs`；`generated/str_datum_colls.rs` | share build.rs；最后一个由一次性脚本写（§2.5.7） | 对应单元的桩 `pub use`；`str_datum_colls.rs` 用 `ob_include_generated!` |
| `sql` | `generated/sys_var_factory.rs`、`generated/syspack.rs`、`generated/str_cmp_colls.rs`；注册表 `generated/expr_op_registry.rs`（528 个 `REG_OP` 加 1 个 `REG_SAME_OP`，以 `ObItemType` 为下标）；枚举 `generated/{log_op,path,op_spec,op_state,extra_info,expr_op_ctx}.rs`（RULEBOOK 0.6、§2.10） | `sys_var_factory.rs` 由 sql build.rs 写；`syspack.rs`、`str_cmp_colls.rs` 由一次性脚本写（§2.5.7）；注册表与枚举由 1d 的脚本从各单元的变体 struct 和 `REG_OP` 收集 | 注册表和 `str_cmp_colls.rs` 用 `ob_include_generated!`；其余 `pub use` |
| `standby` | `generated/standbyservice.rs` | standby build.rs | `pub use` |
| `observer` | `generated/virtual_table/sqlite_tables.rs` | observer build.rs（内部表发射器） | 桩 `pub use` |
| `parser` | `generated/sys_<target>.rs` | 离线 bindgen（`bindgen/regen.sh`） | `sys.rs` 里按 `cfg` `include!` |
| 各 -sys | `generated/raw_<target>.rs` | 离线 bindgen 或手写 | `raw` 模块按 `cfg` `include!` |

#### 2.6.5 共存规则（RULEBOOK 0.13 "共存期间"）

迁移期间 C++ 构建原样保留（它是对齐的参照，第 5b 步还要用它产出 golden 文件），所以 C++ 的生成物继续由原生成器产出（`gen_errno.pl`、`gen_ob_sys_variables.py`、protoc、`generate_inner_table_schema.py` 的 C++ 后端）。"同一份源头同时输出两种语言"用下面的方式保证：

- 源头文件只有一份（`.def`、JSON、Python DSL、`.proto`、`ob_item_type.h`），两边读同一个文件；
- 内部表：同一次 Python 运行里，原 C++ 后端和新 Rust 后端都从同一棵 DSL 对象树输出；
- 每个 Rust 生成器带一个交叉校验测试（§5 的 G1–G6），读取入库的或 `build_release/generated` 里的 C++ 生成物，逐项比对错误码、SQLSTATE、MySQL errno、两种消息、cause/solution；系统变量的 id、名字、标志、默认值、范围；内部表的表 id、表名、列 id、列属性；`ObItemType` 的值和名字表。比对失败，CI 失败。

这与 RULEBOOK 字面上的"每个生成器同时输出 C++ 和 Rust"不同，作为 §8.1 Q1 交人确认。

### 2.7 `migration/design/third_party_versions.tsv`

由 1d 写出，一行一个（库，平台）。列：

| 列 | 含义 |
|---|---|
| `library` | 库名（与 -sys/shim 对应，如 `zlib`、`libcurl`、`vsag`） |
| `platform` | `macos-arm64`、`linux-x86_64`、`linux-aarch64`、`macos-x86_64`、`windows-x64`、`android-arm64` |
| `version` | 版本号；不知道写 `unknown`（Windows vcpkg 的包名不带版本） |
| `provenance` | `deps/3rd`、`in-repo`、`crates.io`、`vcpkg`、`system` |
| `package` | deps 清单里的包名和行号（如 `oceanbase.el8.x86_64.deps:21 devdeps-zlib-static-1.2.13-82024102414.el8.x86_64.rpm`） |
| `artifact` | 头文件和库的路径（相对仓库根） |
| `link` | `static-fullpath`、`static-built`（build.rs 编译）、`dynamic`、`header-only` |
| `baseline_link` | 基线 CMake 的 file:line |
| `version_evidence` | 头文件里的版本宏 file:line |
| `startup_assert` | 启动断言的函数（无则 `-`） |
| `notes` | 其他（如 libxml2 两个版本的布局差异） |

初始内容（macOS arm64 与 Linux x86-64 两个必须构建的平台；其余平台 1d 照 deps 清单补齐）：

| library | macos-arm64 | linux-x86_64 | 启动断言 |
|---|---|---|---|
| zstd | 1.3.8（仓内，改过） | 同左 | 解压/压缩的 `zstd_version` 出参为 138 |
| zlib | 1.2.13 | 1.2.13 | `zlibVersion()` |
| ICU | 69.1（stubdata） | 69.1 | `u_getVersion` |
| libxml2 | 2.10.4（+xz 5.2.2） | 2.13.6（+xz 5.2.2） | `xmlCheckVersion` |
| OpenSSL | 1.1.1u | 1.1.1u | `OpenSSL_version_num()` |
| libcurl | 8.12.1 | 8.12.1（Android 8.2.1） | `curl_version_info` |
| libaio | — | 0.3.112（动态） | — |
| jemalloc | 5.3.1（seekdb-jemalloc-sys 0.2.2） | 同左 | `mallctl("version")`（01 的启动检查） |
| Boost | 1.74.0 | 1.74.0 | 编译期 `BOOST_VERSION == 107400` |
| s2geometry / abseil | 0.10.0 / 20211102.0 | 同左 | — |
| vsag | 1.1.0 | 1.1.0 | — |
| SQLite | 3.38.1 | 3.38.1 | `sqlite3_libversion_number()` |
| protoc | 3.19.5（构建期） | 同左 | — |
| bison / flex / clang | 2.4.1 / 2.5.35 / 17.0.6 | 同左（gcc 12.3 工具链） | build.rs 检查 |
| libc++ / libstdc++ | MacOSX15.sdk（15.4）的 libc++ 19.1.2 | devtools 的 gcc 12.3 libstdc++，静态 | 编译期 `_LIBCPP_VERSION == 190102` |

## 3. 语义

### 3.1 解析器

**3.1.1 生成过程等价于 gen_parser.sh**（`src/sql/parser/gen_parser.sh`、`src/pl/parser/gen_parser.sh`）

- 工具：只用 `deps/3rd/usr/local/oceanbase/devtools/bin` 下的 bison、flex；子进程环境照 `gen_parser.sh:11-12` 把这个目录放到 `PATH` 前面并设 `BISON_PKGDATADIR`（bison 再调 m4）。版本不符 → panic，信息与 `:33` 相同。
- 镜像目录：`gen_parser.sh` 在源码目录里以 `../../../src/sql/parser/…` 的相对路径调用工具，SQL 词法器的 `%option header-file="../../../src/sql/parser/sql_parser_mysql_mode_lex.h"`（`.l:23`）也是相对工作目录的。build.rs 在 `OUT_DIR/parser_gen/` 下建同样三层深的 `src/sql/parser` 等目录作为工作目录，命令行与脚本逐字相同，这样相对路径落回镜像内，生成文件里的 `#line` 指令也与 CMake 的产物逐字相同（可做字节比对，§5 P1）。
- 顺序：fts 的 bison、flex、4 条 sed；SQL 的 bison、flex、4 条 sed；PL 的 bison、flex（`gen_parser.sh:81-96`、PL `:61-62`）。bison 参数 `-v -Werror -d`；返回非 0 或输出含子串 `conflict` → panic（`:62-76`）。flex：SQL 和 fts 用 `-Cfa -B -8`，PL 不用；`_tab.h` 都作为第二个输入文件传给 flex。
- sed 用 Rust 实现，逐条复刻 sed 的范围语义：`/起/,/止/{命令}` 从匹配"起"的行开始，"止"从下一行开始找（含 `}` 的第一行）；每条 sed 是独立的一遍。4 条的内容：删 `int i` 行、删 `for ( i = 0; i < _yybytes_len; ++i )` 行、把 `\tbuf[i] = yybytes[i]` 换成 `memcpy(buf, yybytes, _yybytes_len)`（都限于 `yy_scan_bytes` 函数体）；SQL 另有一条把 `yylex_init` 注释到 `yylex_init_extra` 注释之间的 `return 1` 换成 `return errno`（`:96`），fts 另有一条删含 `This var may be unused depending upon options.` 的行（`:84`）。第 4 条改变了 OOM 时 `parse_init` 的返回值（`sql_parser_base.c:46-55`：`ENOMEM` → `OB_PARSER_ERR_NO_MEMORY`，否则 `OB_PARSER_ERR_PARSE_SQL`），必须保留。
- 不生成 `type_name.c`（解析器 C 不调用 `get_type_name`，§1.3.1），没有 md5 缓存（由 cargo 的 `rerun-if-changed` 代替）。

**3.1.2 编译**

- 编译 14 个 C 文件：SQL 的 `sql_parser_mysql_mode_{tab,lex}.c`、`ftsparser_tab.c`、`ftsblex_lex.c`、`fts_base.c`、`fts_parse.c`、`non_reserved_keywords_mysql_mode.c`、`ob_non_reserved_keywords.c`、`parse_node.c`、`sql_parser_base.c`；PL 的 `pl_parser_mysql_mode_{tab,lex}.c`、`pl_non_reserved_keywords_mysql_mode.c`、`pl_parser_base.c`；加 `csrc/parse_node_layout.c`。
- 编译器和参数：§3.11；`-D` 取 `bindgen/flags.tsv` 本目标那一行，与 bindgen 用的完全相同。
- `-I` 顺序：镜像的 `src`、镜像的 `src/query/api`，然后照基线 `compile_commands.json` 的顺序接 `<repo>`、`<repo>/src`、`<repo>/src/query/api`、`<repo>/src/data_plane/api`、`<repo>/src/objit/include`、`<repo>/src/oblib`、`<repo>/src/oblib/common`（`build_release/generated` 和 Rust 的 include 目录不需要，§1.3.3 的闭包已确认）。镜像排在前面，是为了让 `non_reserved_keywords_mysql_mode.c:19` 这种 `"sql/parser/sql_parser_mysql_mode_tab.h"` 的包含找到新生成的文件，而不是源码目录里 CMake 留下的旧文件。

**3.1.3 布局与离线 bindgen**（RULEBOOK 0.12，S-26）

- 服务端解析器不定义 `SQL_PARSER_COMPILATION`；`parse_node.h:191-194`（`token_off_`/`token_len_`）、`:260-267`（`TokenPosInfo`）、`:360-366`（`comment_list_` 等）都不存在。build.rs 断言 `flags.tsv` 里没有它；`parse_node_layout()` 也报告这个宏未定义。
- 手算的 `ParseNode`（LP64）：`type_`@0、`num_child_`@4、`param_num_`@8、位域联合体@12（8 字节：标志字 4 字节加 `reserved_` 4 字节）、`value_` 联合体@24、`str_value_`@32、`str_len_`@40、`pl_str_off_`@48、`raw_text_`@56、`text_len_`@64、`pos_`@72、`children_`@80、`stmt_loc_`@88（16 字节）、`raw_param_idx_`@104，共 112 字节、8 字节对齐（有 `SQL_PARSER_COMPILATION` 时 120）。以 `parse_node_layout()` 实测为准。
- `ObParseCtx` 内嵌 `jmp_buf`（macOS arm64 是 48 个 int，glibc x86-64 是 200 字节的结构），所以绑定必须按目标生成。
- build.rs 校验两件事：`flags.tsv` 本目标那行与 `sys_<target>.rs` 文件头记录的参数逐字相同；编译 C 用的 `-D` 就是这一行。启动时 `check_parse_node_layout()` 再比对实际布局。

**3.1.4 入口与错误码**（`sql_parser_base.c`、`pl_parser_base.c`）

- `parse_init`：把 `error_msg_` 指向线程局部缓冲；`malloc_pool_` 为空 → `OB_PARSER_ERR_UNEXPECTED`（Rust 的 `ParseResult` 总有 pool，这个分支不可达）；`yylex_init_extra` 返回 0 → 成功，`ENOMEM` → `OB_PARSER_ERR_NO_MEMORY`，其余 → `OB_PARSER_ERR_PARSE_SQL`（`:45-56`）。扫描器的 `yyextra` 指向这个 `ParseResult`，所以 `ParseResult` 分配在解析 arena 里、不移动。
- `parse_sql`：空输入或长度 0、大于 `INT32_MAX` → `OB_PARSER_ERR_EMPTY_QUERY` 并写错误文本（`:73-75`）；按 `is_fp_`/`is_multi_query_`/`is_multi_values_parser_` 分派到 4 个入口之一（`:124-132`）；结果 0 → 成功，2 → `NO_MEMORY`，其余优先用 `extra_errno_`，否则 `PARSE_SQL`（`:133-143`）；longjmp 回来时 `extra_errno_ == NO_MEMORY` → `NO_MEMORY`，否则 `PARSE_SQL`（`:107-113`）。`question_mark_ctx_.count_` 只在非 PL 解析时清零（`:91-93`）。Rust 的 `parse_sql` 原样返回这些码（`ObError::from_code`）。
- C++ 的 `ObSQLParser::parse` 不调 `parse_terminate`（`ob_sql_parser.cpp:30-39`），扫描器的内存在 arena 里随 arena 释放；Rust 照此。
- `obpl_parser_init`：返回 0 或 -1 或 `yylex_init_extra` 的结果，C++ 只判非零（`ob_pl_parser.cpp:236,253`）→ `OB_ERR_PARSER_INIT`。`obpl_parser_parse`：空 → `EMPTY_QUERY`，longjmp 回来 → `global_errno_`，`yyparse` 非零 → `PARSE_SQL`（`pl_parser_base.c:42-61`）。
- `fts_parse_docment`：结果在 `ret_` 里（`FTS_OK` 等），C++ 再映射成 OB 码（如 `ob_das_text_retrieval_merge_iter.cpp:208-212`）；全文检索词法器的致命错误直接 `exit`，照旧。

**3.1.5 线程、跳转与重入**（RULEBOOK 0.12 的三条规则）

- 同一线程：`ParseResult`、`ObParseCtx`、`FtsParserResult`、`ParseNode` 都是 `!Send`，所以 `parse_init` 和 `parse_sql` 不可能在两个线程上分别调用（错误文本缓冲和 jmp_buf 都是线程局部的）。
- setjmp 只在 C 里（`parse_sql`、`obpl_parser_parse`）；longjmp 只在 C 里，目标是同一次 C 调用里更外层的 C 栈帧。这之间可能出现的 Rust 帧只有回调蹦床，它们都是叶子调用：返回空指针或错误码之后，才由 C 决定 longjmp，所以 longjmp 不会越过 Rust 帧。蹦床里 panic 直接 abort（`panic = "abort"`），不会展开进 C。
- 不重入：parser 的三个解析入口用一个线程局部计数（`const` 初始化的 `Cell<u32>`，没有 Drop）。计数非零时再进入任何一个入口 → 返回 `OB_ERR_UNEXPECTED` 并记 ERROR 日志，`FIXED(port): 回调里再解析会覆盖线程局部的 jmp_buf（sql_parser_base.c:104-106），外层随后的 longjmp 会跳进已返回的栈帧`。PL 解析在 C 内部调 `parse_sql_stmt`（C 到 C），不经过 Rust 入口，不受影响。C++ 里 `lookup_pl_symbol` → `ObPLBlockNS::resolve_symbol`（`ob_pl_stmt.cpp:1878`）会不会走到再解析，没有核实，见 §8.1 Q4。
- 解析 arena 活过 resolve：`ParseNode<'p>` 的 `'p` 就是 arena 的借用，`TableItem::node_` 之类持有句柄的结构必须带 `'p`，借用检查保证 arena 不先释放。

**3.1.6 字符集**

- C 从不解引用 `charset_info_`（§1.3.3），所以 Rust 往里装的是 `ObCollationType` 的整数值（就是 C++ `ObCharsetInfo::number`），`None` 装空指针；蹦床把它转回 `Option<ObCollationType>` 交给 `ParserHost`。这样 parser 不需要知道 oblib 字符集模块（第 3 步才翻译）的类型，也不需要 unsafe 的指针还原。
- C++ 的回调对空的 `src_cs` 不做检查就解引用（`parse_str_convert_utf8` 把它交给 `ob_convert`），Rust 蹦床在同样的位置遇到 `None` 就 `ob_abort`（与空指针解引用的可观察结果相同：进程终止）。`replace_invalid_character` 对空字符集有显式分支（`parse_malloc.cpp:177-178`，原样返回输入），照抄。
- 回调的语义逐行照 `parse_malloc.cpp`：
  - `parse_str_convert_utf8`（`:203-224`）：输出缓冲 `strlen(str) * 4 + 1`，用 `ob_convert` 转到 `utf8mb4_general_ci`、替换字符 `'?'`；有错误时 `*extra_errno = OB_PARSER_ERR_ILLEGAL_NAME`，但仍返回结果；末尾补 NUL。
  - `replace_invalid_character`（`:172-201`）：两个字符集都是 Unicode 时替换字符 `0xFFFD`，否则 `'?'`；先转到 `nls_db_cs` 再转回 `src_cs`，两次缓冲都按 4 倍分配。
  - `check_real_escape`（`:324-346`）：只在字符集"反斜杠危险"时检查；会把 `str[str_len]` 写成 `'\\'`（就地写 C 的词法缓冲，这块内存属于解析器、Rust 此时没有借用）。

**3.1.7 `ParseNode` 的读写规则**

- `str_bytes()`：空指针 → `&[]`，负长度 → 0（RULEBOOK 0.12）。`child(i)`：越界、`children_` 为空、元素为空都返回 `None`（§2.9）。`int32_values`/`int16_values`：按本机字节序从 `value_` 的 8 个字节里取（与 C 的联合体相同；6 个目标平台都是小端）。
- 标量写：直接写字段。`set_num_child`、`set_str_len`、`set_text_len` 只许缩小，放大即 abort：C 的不变式是 `children_` 至少有 `num_child_` 个槽、`str_value_` 至少有 `str_len_` 个可读字节，安全的读访问依赖它。C++ 里"先改 `num_child_` 再改 `children_`"、"先改 `str_value_` 再改 `str_len_`"的两步写（如 `ob_raw_expr_resolver_impl.cpp:4176-4203`、`ob_dml_resolver.cpp:750-752`、`ob_prepare_stmt_struct.cpp:372-373`），译成一次 `set_children`/`set_str_value`；这些站点由清单第 21 类逐处列出。
- 指针写只接受 `'p` 的值。`'p` 不变，所以 `LocalParseNode` 的句柄（生命周期是局部借用）不能挂进 arena 树，arena 节点也不能挂到局部节点下；需要混用的站点改在 arena 里分配（第 21 类逐处定）。只读、不保存节点的函数参数写 `ParseNode<'_>`（独立的生命周期），这样局部节点也能传进去；只有要存进结构体或挂进树的才写结构体的 `'p`。
- `replace_questionmarks`（`ob_sql_utils.cpp:193-216`）：VARCHAR 分支把 `str_value_` 指向参数 ObObj 的缓冲，NUMBER 分支指向 `ObNumber::format()` 的线程局部轮转缓冲且不更新 `str_len_`（S-19）。Rust 一律先 `arena.copy_str(..)` 再 `set_str_value`，同时更新长度；NUMBER 分支因此标 `FIXED(port): str_len_ 不同步、指向会被覆盖的轮转缓冲`。
- 就地改写节点字符串（`ObCharset::casedn` 等）：拷贝、修改、改指（RULEBOOK §2.4），即 `let s = node.str_bytes(); let mut v = arena.parse_malloc(..)…; 改 v; node.set_str_value(v)`。

**3.1.8 位域位号**（clang/gcc 小端，从低位起依次分配；由 `parse_node_flag_probe` 在启动和测试时核对）

- `ParseNode::flag_`：`is_neg_` 0、`is_hidden_const_` 1、`is_tree_not_param_` 2、`length_semantics_` 3–4、`is_val_paramed_item_idx_` 5、`is_copy_raw_text_` 6、`is_column_varchar_` 7、`is_trans_from_minus_` 8、`is_assigned_from_child_` 9、`is_num_must_be_pos_` 10、`is_date_unit_` 11、`is_literal_bool_` 12、`is_empty_` 13、`is_multiset_` 14、`is_forbid_anony_parameter_` 15、`is_input_quoted_` 16、`is_forbid_parameter_` 17、`is_default_literal_expression_` 18（共 19 个字段、20 位）。
- `ParseResult` 的 23 个标志：`parse_node.h:308-330` 的书写顺序即位 0–22。`ObParseCtx` 的 8 个：`parse_stmt_node.h:80-87` 的顺序即位 0–7，`reserved_` 是 8–31。

**3.1.9 内存检查与分配回调**

- `new_node` 每次都调用 `try_check_mem_status(1024)`（`parse_node.c:183-187`；参数是"每多少次才真正检查一次"），失败时打印 "Exceeded memory usage limit" 并返回空节点。基线里链接的是 `parse_node.c:177-178` 的 weak 缺省版本（恒返回 `OB_PARSER_SUCCESS`）：`src/sql/parser/ob_memory_tracker_wrapper.cpp` 不参与编译，`src/sql/executor/ob_memory_tracker_wrapper.cpp` 没有定义，`nm` 只在 `parse_node.c.o` 里找到这两个符号且是 weak。**Rust 版保持 C 的 weak 缺省版本，parser crate 不提供强定义**。01 文档 §1 第 81 行和 S 节第 869 行假定解析器会调到 01 的 `try_check_mem_status`，与基线不符，见 §8.1 Q5。
- `parse_malloc`：`nbyte == 0` → 空；分配 `8 + nbyte`（8 字节对齐），头里写 `nbyte`，载荷清零。用 01 的 `Arena::alloc_aligned`（单线程的解析 arena，不受"不接 bump arena"的限制，01 文档第 950 行）。
- `parse_realloc`：`ptr` 非空时分配新块、拷贝 `min(旧长, 新长)` 字节、旧块交给 `free`（arena 上是空操作）。`ptr` 为空时 C++ 分配**不带长度头**的块（`parse_malloc.cpp:127-128`），之后再对它 realloc 会读块前 8 个字节（`:133`）；Rust 版对空指针也分配带头的块（不清零，与 C++ 一样），标 `FIXED(port): parse_realloc(NULL) 的块不带长度头，再 realloc 越界读`（S-24）。现有调用方是否走到这条路径，由清单确认。
- `parse_free`：空操作。`parser_alloc`：`alloc_size <= 0` → 空，否则清零。`malloc_parentheses_info`：清零。`parse_strndup`：`nbyte + 1` 字节、末尾 NUL。`strndup_with_prefix`：`prefix_len + nbyte` 字节，**不补 NUL**（`:233`），照抄。
- 分配失败一律返回空指针，由 C 决定后续（多数走 `YY_FATAL_ERROR` → `OB_PARSER_ERR_NO_MEMORY`）。
- 栈检查：`check_stack_overflow_c` 在检查本身出错时返回 false（`ob_sql_utils.cpp:1964-1971`），`obpl_parser_check_stack_overflow` 在出错时返回 1（`ob_pl_parser.cpp:28-34`，初值是 true）；两者都调 03 的 `check_stack_overflow(reserved = 32 KB)`（RULEBOOK 0.8），按当前栈段计算。

**3.1.10 `OwnedParseTree`**

- 构造用 C 的 `get_deep_copy_size` + `deep_copy_parse_node`，与 `ObPsStmtInfo::add_fixed_raw_param`（`ob_prepare_stmt_struct.cpp:345-378`）相同；NEG_PARAM 的负号挪位（`:362-373`）在 `new_with` 的闭包里做，用拷贝、修改、改指（在私有副本上就地写也安全，但为了只用一套 API，照 §2.4 写）。
- 深拷贝只复制 `str_len_ > 0 && str_value_ != NULL` 的字符串和 `text_len_ > 0` 的原文（`parse_node.c:96-141`），其余字段原样拷贝，所以副本与原树在 `parsenode_equal` 意义下相等。
- `copy_into`：每次执行 PS 时把固定参数节点拷进请求的 arena，代价是几个常量节点的拷贝，标 `PERF(port): 共享只读访问可以免掉这次拷贝`。

### 3.2 `ObItemType`

- 新类型而不是 Rust 枚举（RULEBOOK §2.12、S-05）：C++ 会产生非枚举值，如 `ob_raw_expr_util.cpp:3833,3848,3881` 的 42、41、39；`static_cast<ObItemType>` 共 26 处（复核 C02）。`match` 用常量模式并加 `_` 分支（§2.12）。
- 取值以 query 那份为准（§2.5.2）；`T_MAX = 4921`。objit 那份生效的编译单元里 `T_MAX` 是 4920，这些单元在 Rust 里一律用 4921；由清单第 23 类逐个判定是否有行为差异（例如 `ob_raw_expr.h:4681,4692` 的哨兵比较）。
- `get_type_name`：收录规则见 §1.8；返回值是 `&'static str`（名字都是 ASCII）。

### 3.3 errno

形状和查表语义归 04；生成器保证的内容：
- 每条 `DEFINE_*` 按 `gen_errno.pl:35-79` 的正则取字段（6 个变体），`DEFINE_ERROR` 的用户消息等于普通消息（`store_error(…, $5, $5, …)`）；`_DEP` 只影响 C++ 的头文件归属，Rust 不区分（04 文档 §2.1）。
- M1 是 `ER_*` 符号时取 `mysql_errno.h` 的值；是 `-1` 时保留 `-1`（`ob_mysql_errno_with_check` 在运行时把负值换成 `-err`，`gen_errno.pl:444-451`）。
- 查表的缺省值（`gen_errno.pl:360-463`）由 04 实现；生成器保证表的内容与 `ob_errno.cpp` 逐项相同：只有负码进表（`:303`），所以 `OB_SUCCESS` 的条目是全空的缺省项。
- 格式串按字节原样保存（反转义后），用户消息函数的参数种类按 04 §3.2.6 的表从格式串推出；`DEFINE_OTHER_MSG_FMT` 的两条（`ob_errno.def:250,612`）各自一个格式化函数，错误码取所指错误的值（`gen_errno.pl:52-54,163,171`）。

### 3.4 系统变量

- 变量 id、名字、`flags`、`data_type`、默认值、`base_value`、范围、`enum_names`、`info` 等逐字段照 JSON；字符串照 JSON 原文（`gen_ob_sys_variables.py` 不改写的部分照样不改写，改写的部分（如标志拼接成 C++ 表达式）在 Rust 里求成同样的整数值）。
- `ESSENTIAL_SYS_VARS` 的内容和顺序照 `generate_essential_sys_vars_in_init_cpp`（`gen_ob_sys_variables.py:1092-1168`）。
- 钩子：JSON 的函数名经映射表变成 Rust 路径，函数指针为空的位置写 `None`；`ObSysVars[i].<hook>_func_` 的字符串仍是原 C++ 名。

### 3.5 proto 与版本号

- `ob_vector_tile.proto` 是 proto2，protobuf-c 按字段号顺序编码（`ob_vector_tile.pb-c.c:259-323` 的描述符按字段号排序），prost 也按字段号编码，oneof 放在它最小字段号的位置；ST_AsMVT 的输出字节用 golden 测试比对（§5 G4）。
- standby：tonic 默认的编解码与 gRPC C++ 在线兼容；服务端流的 6 个 RPC 用 tonic 的 streaming。tokio 只在 standby 里（RULEBOOK 0.8）。
- 版本号：`BUILD_NUMBER` 取环境变量，缺省 1（`cmake/Env.cmake:26`）；`GIT_REVISION`、`GIT_BRANCH` 在 build.rs 里跑 `git rev-parse`（CMake 在配置阶段跑，`src/observer/CMakeLists.txt:118-140`）；`BUILD_FLAGS` 照 CMake 是构建类型名（`:142-147`），Rust 的 release profile 对应 `"RelWithDebInfo"`（环境变量 `OB_BUILD_FLAGS` 可覆盖，见 §8.1 Q6）；`BUILD_INFO` 在 CMake 里从未赋值，恒为空串；`build_date`/`build_time` 是编译 `ob_version.cpp` 时的 `__DATE__`/`__TIME__`，build.rs 用本地时间按同一格式生成（`SOURCE_DATE_EPOCH` 有设置时用它）。这些值进入 `observer -V`（`ob_command_line_parser.cpp:539-543`）和 `version_comment`（`ob_system_variable.cpp:65`），裁判不比较。

### 3.6 内部表

- 表 id、表名、列的顺序和全部属性、视图 SQL、系统索引照 DSL；`ADD_COLUMN_SCHEMA*` 的 8 种宏各自的展开（默认值对象的构造、`++column_id`、每步的错误日志）由通用构建函数逐行照 `ob_schema_macro_define.h:25-290` 实现。
- 长度等表达式（如 `OB_MAX_CORE_TALBE_NAME_LENGTH`、`sizeof(int64_t)`，见 `ob_inner_table_schema.1_50.cpp:116,131`）由发射器求值成整数常量写入（发射器维护一张"C++ 表达式 → 值"的表，表外的表达式让发射器报错）。
- `table_id_to_name`、`ob_all_virtual_sqlite_tables.*` 的内容照原后端。

### 3.7 bitpacking

- 打包布局就是落盘格式（RULEBOOK 0.10）。Rust 生成器照两个 Python 脚本的循环和位运算逐条生成，x86-64 与 aarch64 的生成文本相同、结果逐位相同；`ob_sse_to_neon.h` 的 `_mm_slli_epi32(a, n)` = `vshlq_u32(a, vdupq_n_s32(n))`、`_mm_srli_epi32` = 负移位，移位量不小于 32 时两种实现都得 0。
- 调用方（`ob_bp_helpers.h`、`ob_simd_fixed_pfor.h`）照常翻译，调用上面的安全函数。

### 3.8 数据表

- 元素逐个、按原顺序转换；字符串表的字节原样保留（`ob_ik_dic.cpp` 的词条是 UTF-8，但按字节处理）；数值按 C 的字面量规则求值（`INT64_MIN` 等宏照 C 的定义）。
- E2 的视图按 NUL 切分；原 C 的字符串不可能含 NUL，所以切分与原数组一一对应。

### 3.9 -sys

**3.9.1 zstd**：Rust 版直接调用 `ZSTD_*`，所以不能像 CMake 那样把它们局部化（`ld -r` + `--localize-hidden`）；本仓库链接的其他库都不带 zstd（libcurl 不带 zstd，§1.5），xxHash 由纯 Rust 的 `xxhash-rust` 取代、没有 C 符号，所以不会冲突；CI 用 `nm` 检查最终二进制里 `ZSTD_`/`HUF_`/`FSE_`/`XXH` 符号只有一份（§5 S9）。`ZSTD_HEAPMODE` 保持缺省 1（`zstd_decompress.c:750` 的栈模式分支用 5 个参数调用，打开就编译不过）。分配钩子：opaque 为空 → 返回空；否则 `ForeignBlock::try_new(size)`，失败返回空（01 文档 S20）；`ZSTD_calloc` 不检查空指针就清零（`zstd_common.c:61-70`），照旧。

**3.9.2 zlib**：静态链接 `deps/3rd` 的同一个 `libz.a`（不是从源码重编）；`libxml2.a` 也要它的 `inflate`/`deflate`/`gz*`/`crc32`（`nm -u`），由同一个库满足。`COMPRESS()` 的输出字节因此不变（RULEBOOK 0.10）。`z_stream` 装箱固定地址（zlib 的 `deflateStateCheck` 比对 `strm->state->strm == strm`）。`ob_zlib_alloc` 的大小是 `unsigned` 乘法，回绕照抄（01 文档 S20）。

**3.9.3 ICU**：`uregex_setTimeLimit` 的单位是匹配引擎的步数，`uregex_setStackLimit` 是字节，0 表示不限（`uregex.h:1414-1462`）；会话变量 `regexp_time_limit` 缺省 32、`regexp_stack_limit` 缺省 8000000（`ob_system_variable_init.json:1380-1409`）。C++ 把 `int64_t` 传给 `int32_t` 形参，按 C 的转换截断（结果在 0..2^31-1 的范围内不变）。错误文本 `"%s, Syntax error in regular expression on line %d, character %d."` 用 `u_errorName` 和 `UParseError` 的行列（`ob_expr_regexp_context.cpp:597-600`）。静态数据只有 stubdata：用不到任何数据文件。

**3.9.4 libxml2**：macOS/Android 与 Linux 的版本不同，它们接受的输入、恢复模式、`xmlParserCtxt` 的布局都可能不同；Rust 版按平台链接与基线相同的版本，所以各平台的行为与该平台的 C++ 相同（不追求跨平台一致）。`xmlCtxtUseOptions(XML_PARSE_IGNORE_ENC | XML_PARSE_NOENT)` 在换 SAX 表之前调用（`:419-421`）；析构时先恢复 `ctxt->sax`、清 `_private`，再 `xmlFreeParserCtxt`（`:384-388`）。错误映射：`XML_ERR_UNSUPPORTED_ENCODING`、`XML_ERR_ENCODING_NAME` 视为成功，`XML_ERR_RESERVED_XML_NAME` 视选项而定，其余 → `OB_ERR_PARSER_SYNTAX`（`:512-545`），这部分在 oblib 的 trait 实现里，照抄。

**3.9.5 OpenSSL**：C++ 的 `init_ssl_malloc` 装了三个钩子（`ob_encryption_util.cpp:281-317`），它们只设置内存标签再调 libc 的 `malloc`/`realloc`/`free`；内存标签在 Rust 里没有对应物（RULEBOOK 0.7），OpenSSL 1.1.1 的缺省 `CRYPTO_malloc` 也是调 libc 的同名函数，所以 Rust 版不装钩子，`init_ssl_malloc` 是空函数，可观察行为相同。libc 的 `malloc` 在 Linux 上由 01 的 `malloc_hook.rs` 转到 jemalloc（与 C++ 相同）。`RAND_bytes` 的返回值 C++ 忽略（`ob_expr_random_bytes.cpp:66,95`），照抄。SM4 不可用（`OB_USE_BABASSL` 未定义，`SM4_ENCRYPT` 返回 `OB_NOT_SUPPORTED`，`ob_expr_symmetric_encrypt.cpp:342-354`），不绑定 `EVP_sm4_*`。MySQL 协议的 TLS 仍是 rustls（RULEBOOK 0.14）。

**3.9.6 libcurl**：CURLcode 的数值、URL 判定、代理环境变量、根证书路径都由同一个 libcurl 决定，与 C++ 相同（RULEBOOK 0.14）。C++ 给几个 `long` 型选项传了 `int32_t`/`int`（`CURLOPT_POSTFIELDSIZE` 收 `ObString::length()`，`ob_telemetry.cpp:1407`、`ob_ai_func_client.cpp:418`；`CURLOPT_CONNECTTIMEOUT_MS`、`CURLOPT_POST` 收 `int` 字面量，`:410,420`），而 Apple arm64 的可变参数放在栈上、每个占 8 字节，curl 按 `long` 读出时高 32 位是未初始化的栈内容。Rust 的 `CurlOptValue::Long` 只收 `c_long`，标 `FIXED(port): 可变参数类型与 curl 期望的 long 不符`。`CURLINFO_RESPONSE_CODE` 同理（`ob_ai_func_client.cpp:144,152` 传 `int64_t*`；在 LP64 上与 `long*` 等宽，结果相同）。`POSTFIELDS` 不拷贝正文，`CURL<'b>` 的生命周期保证正文活过传输。`curl_global_init(CURL_GLOBAL_ALL)` 在 `main` 里、`curl_global_cleanup` 在退出时（`main.cpp:751-752,827`），照原位置调用。

**3.9.7 libaio**：只在 Linux 编译；动态链接 `libaio.so.1`（或 `libaio.so.1t64`），与 CMake 相同。请求的 iocb 和缓冲在提交到完成之间归上下文所有，所以安全层不会让内核写已释放的缓冲；`iocb.data` 存请求 id（C++ 存 `ObLocalIOCB*`）。

**3.9.8 启动断言**：observer 的 `main` 在 RULEBOOK 0.5 的第 3 步依次调用 `parser::check_parse_node_layout()`、`zlib1213_sys::assert_zlib_version()`、`icu69_sys::assert_icu_version()`、`libxml2_sys::assert_libxml2_version()`、`openssl_sys::assert_openssl_version()`、`curl_sys::assert_curl_version()`，失败即 `ob_abort`；再绑定 `PARSER_HOST_SLOT`。这些都是纯函数，不创建线程、不取锁。

### 3.10 shim

- **异常映射**（`bgk_shim`）：`ob_boost_geometry_exception_handle`（`ob_geo_dispatcher.h:1314-1366`）整段复制、顺序不变，共 20 个 catch 子句、18 个不同的错误码：`std::bad_alloc`、`domain_error`、`length_error`、`invalid_argument`、`out_of_range`、`overflow_error`、`range_error`、`underflow_error`、`logic_error`、`runtime_error`、`bg::centroid_exception`、`turn_info_exception`、`empty_input_exception`、`inconsistent_turns_exception`、`overlay_invalid_input_exception`（Windows 上是 `invalid_input_exception`）、`bg::exception`、`boost::numeric::positive_overflow`、`negative_overflow`（这两个都映射到 `OB_OPERATE_OVERFLOW`）、`std::exception`（→ `OB_ERR_STD_RUNTIME_ERROR`）、`...`（→ `OB_ERR_GIS_UNKNOWN_EXCEPTION`）。顺序有意义：`invalid_argument` 派生自 `logic_error`，`overflow_error` 派生自 `runtime_error`，Boost 的几何异常派生自 `bg::exception` 再派生自 `std::exception`。每个桥接函数都在 C++ 里 `catch (...)` 后调用它，与分派器的两处 `catch (...)`（`:1195-1197`、`:1284-1286`）相同。
- **几何模型**：shim 里用 OB 自己的 bin/tree 类型和它们的 traits 注册（逆时针、闭合：`ob_geo_bin_traits.h:126-134,286-294`、`ob_geo_tree_traits.h:98-104,264-270`），不用 Boost 的缺省模型（顺时针）；构造类算法的结果写在 OB 的树类型里，再用复制进来的 OB WKB 写出逻辑转成字节（行为一致性审查 F5）。
- **SRS WKT**：`qi::double_` 的舍入就是用户自定义 SRS 的数值，所以解析留在 shim（RULEBOOK 0.14）；解析结果按字段平铺交回 Rust，由 Rust 组装 `ObSrsInfo`。
- **regex**：ECMAScript 方言、按字节匹配（`.` 匹配一个字节，`"^.{2}$"` 对 `"中文"` 不匹配）、主体是 C 字符串的调用点遇 NUL 截断、`std::regex` 构造失败的判断方式都与 C++ 相同，因为就是同一个标准库。macOS 的结果取决于 SDK 里 libc++ 的头文件（模板在编译时实例化），所以 shim 必须用 MacOSX15.sdk（RULEBOOK 0.11 的 ENV-1 固定项）。Linux 与 macOS 的 `std::regex` 本来就可能不同，与 C++ 一致即可。
- **C++ 里没有 try/catch 的调用点**（如 `ob_json_schema.cpp:4036` 构造 `std::regex` 不捕获异常，靠此前的 `is_valid_pattern` 保证合法）：shim 仍然返回错误状态，Rust 的调用点照 C++ "异常未被捕获 → `std::terminate`"译成 `ob_abort`，标 `PARITY(port): C++ 在这里不捕获异常`。
- **vsag 回调**：分配器和日志的蹦床可能在 vsag 或 OpenMP 的线程上被调用：只经 `ForeignBlock` 和原子计数，不访问 `WorkerCtx` 等非 const 初始化的线程局部（RULEBOOK 0.5、0.14）；回调上下文 `Sync`，蹦床里 `assert_sync::<T>()`。流回调（`ObOStreamBuf::Callback`、`ObIStreamBuf::Callback`）在调用 `fserialize`/`fdeserialize` 的线程上同步发生，以 `&mut dyn` 借给那一次调用。
- **同线程同步回调**（libxml2 的 SAX、curl 的 WRITEFUNCTION）：上下文以 `&mut dyn Trait` 借给这一次调用，不要求 `Sync`（见 §8.2 A7）。

### 3.11 构建参数与浮点（RULEBOOK 0.14、0.15、D17 (b)）

**3.11.1 编译器**：macOS arm64 与 Linux x86-64 都用 `deps/3rd/usr/local/oceanbase/devtools/bin/clang`、`clang++`（17.0.6）；Linux 另加 `--gcc-toolchain=<devtools>`（使用 gcc 12.3 的 libstdc++，`cmake/Env.cmake:367-372`）。check-only 的目标不编译 C/C++。

**3.11.2 参数**：
- 公共：`-O2 -g -DNDEBUG -fPIC -fno-strict-aliasing -fno-omit-frame-pointer -ffunction-sections -fdata-sections -fmax-type-align=8 -ffp-contract=off`，`-D` 取 `common_defines()`：`DEFAULT_LOG_FILE_SIZE_MB=256 DEFAULT_LOG_LEVEL=OB_LOG_LEVEL_ERROR OB_BUILD_SYS_VEC_IDX OB_HAVE_BUNDLED_JEMALLOC=1 _GLIBCXX_USE_CXX11_ABI=1 __STDC_LIMIT_MACROS __STDC_CONSTANT_MACROS _NO_EXCEPTION OCI_LINK_RUNTIME`；`PACKAGE_NAME/STRING/VERSION`、`RELEASEID` 只加给基线里也带它们的编译单元（解析器 C 带，zstd 不带）。
- macOS arm64：`-arch arm64 -isysroot <MacOSX15.sdk> -mmacosx-version-min=15.0 -march=armv8-a+crc+lse -mtune=generic -D_DARWIN_C_SOURCE`；SDK 路径固定为 `/Library/Developer/CommandLineTools/SDKs/MacOSX15.sdk`（ENV-1），不用 `xcrun` 的缺省 SDK。
- Linux x86-64：`-mtune=core2`，**没有** `-march`（`Env.cmake:411-413`），`-gdwarf-4`；Linux aarch64：`-march=armv8-a+crc+lse -mtune=generic`。
- C++：加 `-std=gnu++20`；异常和 RTTI 用缺省（打开）。
- 解析器 C 的 `-D` 以 `flags.tsv` 为准（等于上面的公共集合加 `PACKAGE_*`、`RELEASEID`）；zstd 另加 `-DZSTDERRORLIB_VISIBILITY= -DZDICTLIB_VISIBILITY= -DZSTDLIB_VISIBILITY= -fvisibility=hidden`。
- 警告参数照基线的 `-W` 列表（不影响代码生成），但不加 `-Werror`，避免编译器的小版本差异让构建失败。

**3.11.3 FMA**：D17 选 (b)：所有 shim、-sys、解析器 C 一律 `-ffp-contract=off`；C++ 参照按 DEV-016 也用 `-ffp-contract=off` 重建。s2 头文件里的 `#pragma STDC FP_CONTRACT OFF` 因此不再造成差别。Rust 侧不写 `mul_add`。

**3.11.4 链接**：
- 所有 -sys/shim 的 build.rs 不自己链接 C++ 运行时（`cpp_link_stdlib(None)`），由 observer 的 build.rs 统一给出：macOS 链接系统的 `libc++`（与基线一致）；Linux 静态链接 devtools 里 gcc 12.3 的 `libstdc++.a` 与 `libgcc_eh.a`（等价于 CMake 的 `-static-libstdc++ -static-libgcc`，`src/observer/CMakeLists.txt:101-102`）。
- 静态库的顺序由 crate 依赖保证：依赖方的库排在被依赖方前面（`curl_sys` 先于 `openssl_sys`，`libxml2_sys` 先于 `zlib1213_sys`）。macOS 的系统库和 framework（`-liconv`、`Security`、`CoreFoundation`、`SystemConfiguration`）由用到它们的 -sys 发出 `cargo:rustc-link-lib`。

### 3.12 FIXED、PARITY、PERF 汇总

| 标记 | 位置 | 内容 |
|---|---|---|
| `FIXED(port)` | `parse_realloc` 蹦床 | 空指针分配也带长度头（§3.1.9） |
| `FIXED(port)` | `replace_questionmarks` 的译文 | NUMBER 分支先拷进 arena 并更新 `str_len_`（§3.1.7） |
| `FIXED(port)` | parser 入口 | 回调里重入解析器返回 `OB_ERR_UNEXPECTED`（§3.1.5） |
| `FIXED(port)` | `curl_easy_setopt` 的调用点 | `long` 型选项传 `c_long`（§3.9.6） |
| `FIXED(port)` | `ParseNode::set_num_child`/`set_str_len`/`set_text_len`/`set_child` | 放大或越界即 abort（C++ 里越界读写） |
| `PARITY(port)` | `std::regex` 不捕获异常的调用点 | shim 返回错误后 `ob_abort`（§3.10） |
| `PERF(port)` | `OwnedParseTree::copy_into` | 每次执行 PS 拷贝固定参数节点（§3.1.10） |
| `PERF(port)` | `ob_parse_binary_simd` | 首版只做标量（处理 0 字节，C 的循环处理全部输入，结果相同），之后再移植 AVX2/AVX512 路径 |

## 4. unsafe

全部在 RULEBOOK §3 的白名单路径里：`rust/port/parser/**`（第 12 项）、各 -sys 和 shim crate（第 13 项）、`rust/port/oblib/src/simd/**`（第 8 项，bitpacking）。生成器的其余产物（errno、`ObItemType`、系统变量、proto、内部表、数据表）都是安全代码。对外 API 都是安全的；唯一的例外是 01 的 `ForeignBlock::from_raw`，只在蹦床里调用。

| # | 位置 | 为什么需要 | 安全论证要点 |
|---|---|---|---|
| P1 | `parser::node`：`ParseNode` 的读写 | 经 `NonNull<sys::ParseNode>` 读写 C 分配的节点 | (1) 句柄只由 C 返回的非空指针、`ParseArena` 的分配或 `LocalParseNode` 构造；(2) 节点内存活过 `'p`（arena 的借用或局部变量的借用）；(3) 只用 `addr_of!`/`addr_of_mut!` 逐字段读写，不长期持有 `&sys::ParseNode`/`&mut`，所以 C 后续经自己的指针修改节点不违反别名规则；(4) `!Send`/`!Sync`，没有并发访问；(5) `'p` 不变，写进去的指针都活过 `'p` |
| P2 | `str_bytes`/`str_value`/`raw_text`/`child` | 从指针和长度构造切片、按下标读子节点数组 | 空指针和负长度先处理（`from_raw_parts` 要求非空）；C 解析器保证 `str_value_` 至少有 `str_len_` 个字节、`children_` 至少有 `num_child_` 个槽；Rust 的写入只能保持或缩小这两个不变式（§3.1.7）；字节在 `'p` 内不被改写（就地改写一律拷贝、修改、改指） |
| P3 | `ParseResult`/`ObParseCtx` 分配在 arena 里、经指针读写 | C 的扫描器保存指向它们的指针 | 地址在 `'p` 内不变；Rust 只经原始指针逐字段访问；`&mut self` 的接收者保证调用 C 入口时 Rust 侧没有别的借用；包含指针的子结构（`pl_parse_info_`、`question_mark_ctx_`）不给 `&mut`，只给标量的读写 |
| P4 | `parser::api`：调用 23 个 C 函数 | FFI | 每个实参都来自安全类型（`&'p [u8]`、arena、句柄）；C 只在调用期间使用它们，或保存到 `'p` 期间（节点、输入 SQL）；入口带重入计数（§3.1.5） |
| P5 | `OwnedParseTree: Send + Sync` | 内含原始指针 | 构造完成后再不修改，也不再从内部 arena 分配；所有访问都是只读的（`copy_into`、根的只读访问）；内部 arena 与树一起被拥有和释放 |
| P6 | `parser::callbacks` 的 20 个蹦床 | `#[no_mangle] extern "C"`、把 `void*` 转回 `&ParseArena`、把 C 字符串和出参转成切片和 `&mut` | `malloc_pool` 只来自 `ParseArena::as_malloc_pool`（由安全 API 设置进结构）；所有回调都在解析线程上同步发生，Rust 侧此时持有 `&mut ParseResult`，没有别的借用；空指针按 C++ 的分支处理；`pl_ns` 指向 `with_pl_ns` 栈上的 `&dyn PlSymbolLookup`，只在那次调用期间非空 |
| P7 | 跨语言跳转 | setjmp/longjmp | setjmp、longjmp 都只在 C 里；蹦床是叶子调用，不调用可能 longjmp 的 C 代码；`panic = "abort"`；重入被拒绝（§3.1.5） |
| P8 | `parser::layout` | 读 C 返回的布局表 | 表是 C 的静态数组，长度由 C 返回 |
| P9 | -sys 的原始调用 | FFI | 安全层负责：长度与 C 的要求一致（不足即返回失败或 abort）、借给 C 而不被拷贝的内存活过使用期（`CURL<'b>`、`with_uregex_setText`、`xmlParserCtxtIn<'i>`、libaio 的所有权转移）、拥有型句柄在 Drop 里释放一次、需要固定地址的结构装箱（`z_stream`） |
| P10 | 第三方回调蹦床（zstd、zlib 的分配，SAX，curl 写回调，vsag 的分配、日志、流） | `extern "C"`、C++ 回调 | 两层规则（RULEBOOK 0.14）：蹦床只把指针转成安全类型就调安全的方法；分配类只做 `ForeignBlock::into_raw/from_raw`；可能在外部线程上调用的（vsag）上下文 `Sync` 并有 `assert_sync`，不碰非 const 初始化的线程局部 |
| P11 | cxx 的 `unsafe extern "C++"` 块 | cxx 的要求 | 签名只用 cxx 检查过的类型（切片、`Vec`、`UniquePtr`、`Pin<&mut T>`、POD 结构）；C++ 实现全部 `catch (...)`，不让异常越过边界 |
| P12 | bitpacking 的 SIMD | `core::arch` 内建函数 | x86-64 的 SSE2 是基线，不需要运行时检测；aarch64 的 NEON 是基线；切片长度在入口断言，内建函数只读写断言过的范围 |
| P13 | `unsafe impl Send` | -sys 的拥有型句柄（`ZSTD_CCtx`、`URegularExpression`、`EVP_CIPHER_CTX`、`CURL` 等） | 这些库的上下文对象可以在线程间移动，只要不并发使用（它们都不是 `Sync`）；ICU、zstd、OpenSSL、curl 的文档均如此规定 |

每处 `unsafe` 写 `// SAFETY:`；crate 根 `#![deny(unsafe_op_in_unsafe_fn)]`；禁止把任何 C 来的整数 `transmute` 成 Rust `enum`（C 枚举一律新类型）。

## 5. 测试

测试放在各 crate 的 `tests/`（RULEBOOK §4）；golden 文件由第 5b 步的 C++ 程序产出，C++ 源码放在 `rust/port/<crate>/tests/cpp/`，用基线构建链接，不放 `tools/`。本模块没有自己的并发结构（解析是单线程的；vsag 回调只经 01 的 `ForeignBlock` 和原子计数），所以没有 loom 测试；Miri 只能跑不调用 C 的部分（P16）。

**解析器**

| # | 类型 | 内容 |
|---|---|---|
| P1 | 构建 | build.rs 生成的 `*_tab.{c,h}`、`*_lex.{c,h}` 与 `gen_parser.sh` 在同一输入下的产物逐字节相同（macOS arm64、Linux x86-64） |
| P2 | 单测 + 启动 | `check_parse_node_layout()`：全部 sizeof/alignof/offsetof；50 个位域探针；`SQL_PARSER_COMPILATION` 未定义 |
| P3 | 构建 | `flags.tsv`、`sys_<target>.rs` 文件头、build.rs 实际使用的 `-D` 三者一致；故意改一项，构建失败 |
| P4 | golden | 对裁判全部 mysqltest 语句、sysbench、TPC-C 的 SQL，用 Rust 的 parser crate（测试用 `ParserHost`）与 C++ 的 `ObParser` 各解析一遍，导出整棵树（类型、值、字符串、19 个标志、子节点、位置、参数链表、`no_param_sql`、返回码、`start_col_`/`line_`/`end_col_`）逐字节比较 |
| P5 | golden | 1064 报错的输入：返回码、`start_col_`、`line_` 与 C++ 相同（near 文本由 sql 的译文生成，不在此测） |
| P6 | golden | FP_MODE、MULTI_MODE、INS_MULTI_VALUES 三种快速解析的输出 |
| P7 | golden | PL：裁判里全部存储过程和触发器的源文本，比较 `stmt_tree_`、`global_errno_`、`cur_error_info_` |
| P8 | golden | 全文检索布尔模式：`fts_parse_docment` 的 AST |
| P9 | 单测 | `ParseNode`：`str_bytes` 的空指针与负长度；`child` 越界与空槽；`int16/int32` 通道；`set_num_child`/`set_str_len` 只许缩小（放大时 abort，用子进程测）；`set_children`；`LocalParseNode` |
| P10 | 编译失败测试（doc test `compile_fail`） | `ParseNode` 不是 `Send`；不同 `'p` 的节点不能 `set_child`；`LocalParseNode` 的句柄不能挂进 arena 树；`OwnedParseTree` 是 `Send + Sync`（`static_assertions` 式的 const 断言） |
| P11 | 单测 | `OwnedParseTree`：与原树 `parsenode_equal`；`copy_into` 后相等；NEG_PARAM 修正与 C++ 结果相同 |
| P12 | 单测 | 回调：`parse_malloc` 的长度头与清零、`nbyte == 0`；`parse_realloc` 的拷贝长度与空指针路径；`parse_strndup` 末尾 NUL；`strndup_with_prefix` 不补 NUL；`check_real_escape` 在 gbk 下的结果（golden） |
| P13 | 故障注入 | 让 `ParseArena` 在第 N 次分配失败，验证返回 `OB_PARSER_ERR_NO_MEMORY`、没有 Rust 帧被 longjmp 越过（ASan 下运行） |
| P14 | 单测 | 重入：在测试用 `PlSymbolLookup` 里再调 `parse_sql`，得到 `OB_ERR_UNEXPECTED` |
| P15 | golden | `get_type_name` 对 `[-2, 5000]` 的每个整数，与 C 的 `type_name.c` 相同 |
| P16 | Miri | 只在纯 Rust 部分：用 Rust 构造的 `sys::ParseNode` 测句柄的读写、`LocalParseNode`、子节点数组（不调用 C） |

**-sys 与 shim**

| # | 类型 | 内容 |
|---|---|---|
| S1 | 启动 | 各版本断言（§3.9.8）通过；把期望值改错一位，断言失败 |
| S2 | golden | zstd：C++ 产出的压缩块能解压、Rust 压缩的字节与 C++ 相同（同级别、同输入）；`zstd_version` 出参为 138；流式与块接口 |
| S3 | golden | zlib：`COMPRESS()`、`crc32`、`adler32`、gzip 读写与 C++ 字节相同；`zlibVersion()` |
| S4 | golden | ICU：REGEXP_LIKE/INSTR/SUBSTR/REPLACE 的结果和错误文本（含 `u_errorName` 与行列）；时间和栈限制触发时的错误码 |
| S5 | golden | libxml2：XML 语料的 SAX 事件序列与错误码，分平台（2.10.4、2.13.6）各一份 golden |
| S6 | 测试向量 | OpenSSL：AES 全部模式（含 cfb1、gcm 的 tag）、DES-EDE3-CBC、`DES_fcrypt`、`EVP_BytesToKey`、MD4/MD5/SHA*/SM3、HMAC，期望值由 C++ 产出 |
| S7 | 集成 | curl：本地 HTTP 服务器；不可解析的主机、拒绝连接、超时、畸形 URL、错误证书，CURLcode 与 C++ 相同；写回调分块 |
| S8 | 集成（Linux） | libaio：读写往返；`io_cancel`；未接受的请求被交还 |
| S9 | 链接检查 | 最终二进制里 `ZSTD_`、`HUF_`、`FSE_`、`XXH`、zlib、OpenSSL 的符号各只有一份；macOS 链接系统 libc++，Linux 不出现动态的 libstdc++ |
| B1 | 单测 | `bgk_shim` 的异常映射：shim 的测试入口逐个抛出 20 种异常，得到的错误码与 C++ 相同 |
| B2 | golden | 裁判的 39 个 geometry 用例里的全部 ST_* 调用：输入 WKB、输出 WKB 或数值逐位相同（含环方向） |
| B3 | golden | SRS：全部内置 SRS 定义和若干自定义定义，解析出的 double 逐位相同 |
| B4 | 构建检查 | shim、-sys、解析器 C 的目标文件里没有 `fmadd`/`fmsub`/`fnmadd`/`fnmsub`（arm64，`objdump` 扫描），编译参数里有 `-ffp-contract=off` |
| B5 | golden | s2：语料的 cell id、内覆盖 |
| B6 | golden | vsag：同样输入与参数下的建索引、检索结果、序列化字节；分配器回调在多线程下的计数（压力测试） |
| B7 | golden | regex：`"^.{2}$"` 对 `"中文"`；后行断言非法；含 NUL 的主体截断；`icase`；`ob_array_cast.cpp:557-561` 的两个替换；POSIX ERE |

**生成器**

| # | 类型 | 内容 |
|---|---|---|
| G1 | 交叉校验 | errno：Rust 表与 `share/ob_errno.cpp` 的 `_error_*` 逐项相同（名字、码、MySQL errno、SQLSTATE、两种消息、cause、solution）；11 个查表函数对 `[-65536, 1]` 的每个整数与 C++ 相同；未知 printf 规格、重复码、未知 `ER_*` 让 build.rs 失败 |
| G2 | golden | 用户消息：每个格式串用一组样例实参，Rust 的 `user_msg::OB_X` 写进警告缓冲区的字节与 C++ `vsnprintf(buf, 512, …)` 相同（与 04 的 `ob_printf!` 测试共用 golden） |
| G3 | 交叉校验 | `ObItemType`：值与 C 枚举相同（C 程序打印每个枚举值）；差异表与源码一致；把某个值改掉，build.rs 失败 |
| G4 | golden | proto：ST_AsMVT 的输出字节与 protobuf-c 相同；standby 的 gRPC 与 C++ 客户端/服务端互通 |
| G5 | 交叉校验 | 系统变量：id、名字、标志、默认值、范围、`enum_names` 与生成的 C++ 相同；94 个钩子都有映射 |
| G6 | 交叉校验 | 内部表：用 Rust 数据表构建的每张 `ObTableSchema` 序列化后与 C++ 构建的相同（第 3 步之后才能跑） |
| G7 | golden | bitpacking：每个位宽（0–32；16 位版本 0–16）随机输入的打包字节与 C++ 相同，解包后还原；x86-64 与 aarch64 结果相同 |
| G8 | 交叉校验 | 数据表：逐元素与 C++ 的表相同（C++ 小程序打印原数组）；ik 词典的条数和顺序 |
| G9 | CI | `generated/` 过期检查（构建后 `git diff --exit-code`）；文件头 sha256 检查 |
| G10 | 单测 | 版本号：`build_date`/`build_time` 的格式；`build_info()` 为空串；`build_version()` 的形状 |

## 6. 翻译者怎么用

**1. 就地参数化**（`src/sql/plan_cache/ob_sql_parameterization.cpp:581-586`）

```cpp
node_type = ctx.tree_->type_;
ctx.tree_->type_ = T_QUESTIONMARK;
ctx.tree_->raw_param_idx_ = ctx.sql_info_->total_;
ctx.tree_->value_ = ctx.question_num_;
```
```rust
node_type = ctx.tree_.kind();
ctx.tree_.set_kind(T_QUESTIONMARK);
ctx.tree_.set_raw_param_idx(ctx.sql_info_.total_);
ctx.tree_.set_value(ctx.question_num_);
```

**2. 把节点字符串改指到别处**（`src/sql/ob_sql_utils.cpp:202-212`）

```cpp
tree->str_value_ = param.get_varchar().ptr();
tree->str_len_ = param.get_varchar().length();
…
tree->str_value_ = param.get_number().format();
```
```rust
tree.set_str_value(arena.copy_str(param.get_varchar())?);       // 先拷进解析 arena，长度一起写
…
let mut nbuf = ObBuf::new();
param.get_number().format(&mut nbuf);                            // 写进调用方缓冲（RULEBOOK §2.4）
// FIXED(port): 原版指向轮转缓冲且不更新 str_len_
tree.set_str_value(arena.copy_str(nbuf.as_bytes())?);
```

**3. 把节点字符串当 C 字符串用**（`src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:505`）

```cpp
ObString cs_name = ObString(node->str_value_);
```
```rust
let cs_name: &[u8] = node.str_cstr_bytes();
```

**4. 栈上的节点**（`src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:54-60`）

```cpp
ret_node.type_ = T_CAST_ARGUMENT;
ret_node.value_ = 0;
ret_node.int16_values_[OB_NODE_CAST_TYPE_IDX] = T_VARCHAR;
ret_node.int16_values_[OB_NODE_CAST_COLL_IDX] = 0;
ret_node.int32_values_[OB_NODE_CAST_C_LEN_IDX] = 20;
ret_node.length_semantics_ = 0;
ret_node.is_hidden_const_ = 1;
```
```rust
let mut ret_node_owner = LocalParseNode::new();          // `ParseNode ret_node;` 处
let ret_node = ret_node_owner.as_node();
ret_node.set_kind(T_CAST_ARGUMENT);
ret_node.set_value(0);
ret_node.set_int16_values(OB_NODE_CAST_TYPE_IDX, T_VARCHAR.0 as i16);
ret_node.set_int16_values(OB_NODE_CAST_COLL_IDX, 0);
ret_node.set_int32_values(OB_NODE_CAST_C_LEN_IDX, 20);
ret_node.set_length_semantics(0);
ret_node.set_is_hidden_const(true);
```

**5. 只用节点解码一个打包的整数**（`src/sql/engine/expr/ob_datum_cast.cpp:8897-8899`）

```cpp
ParseNode node;
node.value_ = dst_type_dat->get_int();
ObObjType obj_type = static_cast<ObObjType>(node.int16_values_[OB_NODE_CAST_TYPE_IDX]);
```
```rust
let mut node_owner = LocalParseNode::new();
let node = node_owner.as_node();
node.set_value(dst_type_dat.get_int());
let obj_type = ObObjType(node.int16_values(OB_NODE_CAST_TYPE_IDX) as i32);
```

**6. 在 resolver 里新建节点**（`src/sql/resolver/expr/ob_raw_expr_util.cpp:6442-6460`）

```cpp
node = (ParseNode *)alloc_buf->alloc(sizeof(ParseNode));
if (OB_ISNULL(node)) { ret = OB_ERR_UNEXPECTED; }
else { node->type_ = type; node->num_child_ = num; node->param_num_ = 0; node->flag_ = 0;
       node->value_ = INT64_MAX; node->str_len_ = 0; node->str_value_ = NULL; … }
```
```rust
// C++ 拿到的是未初始化内存再逐字段赋值；new_node 分配后其余字段为 0（pl_str_off_ 为 -1，
// C++ 没给它赋值，读它本来就是未定义行为），num > 0 时一并分配子节点槽
*node = arena.new_node(type_, num);
if node.is_none() { ret = Err(OB_ERR_UNEXPECTED); }
else { let n = node.unwrap(); n.set_param_num(0); n.set_flag(0);
       n.set_value(i64::MAX); n.set_str_value_null(); … }
```
（`num_child_` 与 `children_` 分两步写的其他站点，统一译成 `arena.alloc_children(n)` 加 `set_children`，由清单第 21 类逐处列出。）

**7. PS 缓存保存固定参数**（`src/sql/plan_cache/ob_prepare_stmt_struct.cpp:345-378`）

```cpp
if (OB_FAIL(deep_copy_parse_node(allocator_, node.node_, param->node_))) {
} else if (FALSE_IT(param->flag_ = node.flag_)) {
} else { if (NEG_PARAM == param->flag_) { … const_cast<char *>(param->node_->str_value_)[tmp_pos - 1] = '-'; … } }
```
```rust
let tree = OwnedParseTree::new_with(node.node_, |root, arena| {
    let mut ret: ObResult = Ok(());
    if NEG_PARAM == node.flag_ {
        // 拷贝、修改、改指（RULEBOOK §2.4）
        let s = root.str_bytes();
        …
        root.set_str_value(arena.copy_str(&fixed)?);
    }
    ret
});
```

**8. 执行 PS 时读固定参数**（`src/sql/ob_sql.cpp:1604-1608`）

```cpp
} else if (OB_ISNULL(raw_param = raw_params.at(i)->node_)) {
} else if (OB_FAIL(ObResolverUtils::resolve_const(raw_param, stmt_type, allocator, …))) {
```
```rust
} else if ob_fail!(ret = raw_params.at(i).node_.copy_into(parse_arena) => raw_param) {   // 04 §2.2.1 的第二种写法
} else if ob_fail!(ret = ObResolverUtils::resolve_const(raw_param, stmt_type, arena, …)) {
```

**9. 设置并调用 SQL 解析器**（`src/sql/parser/ob_parser.cpp:931-957` 与 `:883-896`）

```cpp
memset(&parse_result, 0, sizeof(ParseResult));
parse_result.is_fp_ = (FP_MODE == parse_mode || …);
parse_result.malloc_pool_ = allocator_;
parse_result.sql_mode_ = sql_mode_;
parse_result.charset_info_ = ObCharset::get_charset(charsets4parser_.string_collation_);
…
LOG_USER_ERROR(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX), error_length,
               stmt.empty() ? NULL : stmt.ptr() + error_offset, parse_result.line_ + 1);
```
```rust
let mut parse_result = ParseResult::new(ParseArena::from_arena(self.allocator_))?;   // 已清零，pool 已设
parse_result.set_is_fp(FP_MODE == parse_mode || …);
parse_result.set_sql_mode(self.sql_mode_);
parse_result.set_charset_info(ObCharset::get_charset(self.charsets4parser_.string_collation_)
                                  .map(|cs| ObCollationType(cs.number as i32)));
…
log_user_error!(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX), error_length,
                if stmt.is_empty() { None } else { Some(&stmt[error_offset as usize..]) }, parse_result.line() + 1);
```

**10. PL 解析**（`src/pl/parser/ob_pl_parser.cpp:236-263`）

```cpp
if (0 != obpl_parser_init(&parse_ctx)) {
  ret = OB_ERR_PARSER_INIT;
} else if (OB_FAIL(obpl_parser_parse(&parse_ctx))) {
  …
  parse_ctx.stmt_tree_ = merge_tree(parse_ctx.mem_pool_, &(parse_ctx.global_errno_), T_STMT_LIST, parse_ctx.stmt_tree_);
```
```rust
if 0 != obpl_parser_init(&mut parse_ctx) {
    ret = Err(OB_ERR_PARSER_INIT);
} else if ob_fail!(ret = obpl_parser_parse(&mut parse_ctx)) {
    …
    let src = parse_ctx.stmt_tree();
    let t = arena.merge_tree(parse_ctx.global_errno_mut(), T_STMT_LIST, src);
    parse_ctx.set_stmt_tree(t);
```

**11. 全文检索**（`src/sql/das/iter/ob_das_text_retrieval_merge_iter.cpp:207-212`）

```cpp
} else if (FALSE_IT(fts_parse_docment(static_cast<char *>(buf), str_dest.length(), &alloc, fts_parser))) {
} else if (FTS_OK != fts_parser->ret_) {
  if (FTS_ERROR_MEMORY == fts_parser->ret_) { ret = OB_ALLOCATE_MEMORY_FAILED; }
```
```rust
} else if false_it!(fts_parse_docment(buf, ParseArena::from_arena(&alloc), &mut fts_parser)) {
} else if FTS_OK != fts_parser.ret() {
    if FTS_ERROR_MEMORY == fts_parser.ret() { ret = Err(OB_ALLOCATE_MEMORY_FAILED); }
```

**12. 整数转 `ObItemType`**（`src/sql/resolver/dml/ob_default_value_utils.cpp:234-236`）

```cpp
expr_factory.create_raw_expr(static_cast<ObItemType>(column_schema->get_cur_default_value().get_type()), c_expr)
```
```rust
expr_factory.create_raw_expr(qa, ObItemType(column_schema.get_cur_default_value().get_type().0), &mut c_expr)
```

**13. ICU**（`src/sql/engine/expr/ob_expr_regexp_context.cpp:251-254`）

```cpp
regexp_engine_ = uregex_open(u_pattern, u_pattern_length, cflags, &parse_error, &u_error_code);
uregex_setStackLimit(regexp_engine_, regex_vars.regexp_stack_limit_, &u_error_code);
uregex_setTimeLimit(regexp_engine_, regex_vars.regexp_time_limit_, &u_error_code);
```
```rust
self.regexp_engine_ = uregex_open(u_pattern, cflags, &mut parse_error, &mut u_error_code);
if let Some(re) = self.regexp_engine_.as_mut() {
    uregex_setStackLimit(re, regex_vars.regexp_stack_limit_, &mut u_error_code);
    uregex_setTimeLimit(re, regex_vars.regexp_time_limit_, &mut u_error_code);
}
```
（C++ 对空指针调用这两个函数时 ICU 内部检查 `U_FAILURE` 后直接返回；Rust 用 `if let` 表达同样的结果。）

**14. zstd**（`src/oblib/lib/compress/zstd_1_3_8/ob_zstd_wrapper.cpp:56-61`）

```cpp
} else if (NULL == (zstd_cctx = ZSTD_createCCtx_advanced(zstd_mem))) {
  ret = OB_ALLOCATE_MEMORY_FAILED;
} else {
  compress_ret_size = ZSTD_compressCCtx(zstd_cctx, dst_buffer, dst_buffer_size, src_buffer, src_data_size, OB_ZSTD_COMPRESS_LEVEL, &zstd_version);
```
```rust
} else if { zstd_cctx = ZSTD_createCCtx_advanced(zstd_mem); zstd_cctx.is_none() } {
    ret = Err(OB_ALLOCATE_MEMORY_FAILED);
} else {
    compress_ret_size = ZSTD_compressCCtx(zstd_cctx.as_mut().unwrap(), dst_buffer, src_buffer,
                                          OB_ZSTD_COMPRESS_LEVEL, Some(&mut zstd_version));
```

**15. Boost.Geometry**（`src/share/geo/ob_geo_func_covered_by1.cpp:65-66`）

```cpp
boost::geometry::correct(*poly_copy);
result = boost::geometry::covered_by(*geo1, *poly_copy);
```
```rust
// C++ 的这两行在分派器的 try 块里（ob_geo_dispatcher.h:1195-1197），异常变成 ret；shim 直接返回同样的码
ret = ObError::from_code(bgk_shim::ffi::correct(&mut pol_data, &srs));
if ret.is_ok() {
    ret = ObError::from_code(bgk_shim::ffi::covered_by(geo1.wkb(), &pol_data, &srs, &mut result));
}
```

## 7. 依赖

**白名单 crate**（RULEBOOK §1）：`cc`、`cxx`、`cxx-build`（shim）；`bindgen`（只离线运行，不是 build-dependency）；`serde`、`serde_json`（只作 build-dependency：系统变量 JSON）；`prost`、`prost-build`、`tonic`、`tonic-build`、`tokio`（只在 standby）；`libc`（build.rs 取本地时间；libaio 的类型）；`seekdb-jemalloc-sys`、`rusqlite`/`libsqlite3-sys`（配置见 §1.10、§2.3.8）。不引入 `protox`、`libz-sys`、`openssl-sys`、`curl-sys` 等表外 crate。

**其他 1d 模块**：

| 模块 | 用到的 |
|---|---|
| 01 `mem` | `Arena::alloc`/`alloc_aligned`（解析 arena，置零、len == 0 失败）；`ForeignBlock`（zstd/zlib/vsag 的分配回调）；`check_mem_status`/`try_check_mem_status` 的单元归属（§3.1.9 的冲突，§8.1 Q5）；`malloc_hook.rs`（第三方 C 库的 `malloc` 走 jemalloc）；`link-jemalloc`/`define-malloc-conf` feature |
| 03 `thread-signal` | `oblib::thread::check_stack_overflow(reserved)`（两个栈检查回调）；`main` 的第 3 步顺序（版本断言、`PARSER_HOST_SLOT` 绑定、布局自检） |
| 04 `error-log-fmt` | `ObError::new_const`/`from_code`、`ObResult`、`ObErrcode`；errno 生成物的形状（§2.1）与用户消息的参数表（§3.2.6）；`ObToString`、`ObBuf`、`ObPrintfStr`；`ob_abort`；日志宏 |
| 05 `containers` | `oblib::hash::murmurhash`；若需要以 `ParseNode` 为键的哈希表，键的相等 trait 要能独立于 `PartialEq`（`ParseNode` 不实现 `PartialEq`，§2.2.4）。基线里没有这样的表（§1.3.1），默认不提供 |
| 06 `string-num-sort-serial` | `oblib::slot::Slot`（`PARSER_HOST_SLOT`）；`oblib::prelude` |
| 07 `sql-ir-frame-datum` | `ObCollationType`、`ObObjType` 的新类型（字符集令牌、`.map` 宏表的元素类型） |
| 1c | `units.tsv` 的处置（§1.2、§1.4–1.9，含与现有 `units.tsv` 的差异清单）；`seams.tsv`：§8.1 Q3，以及 §1.2、§1.6、§2.5.7 的函数级、类级重映射；清单第 16、21、23 类；`item_type_divergence.tsv`、`item_type_tu.tsv`、`data_tables.tsv` |

## 8. 开放问题

### 8.1 需要人决定

**Q1 生成器的"共存"怎么做**（RULEBOOK 0.13："每个生成器从同一份源头同时输出 C++ 和 Rust"）
- 推荐：C++ 构建原样保留、C++ 产物继续由原生成器产出；Rust 生成器读同一份源头，并带交叉校验测试（§2.6.5、§5 G1/G3/G5/G6）。理由：C++ 构建是对齐参照，不能改；让 Rust 生成器逐字节重现 `ob_errno.cpp`（15,636 行）、`ob_system_variable_factory.cpp`（10,705 行）等，工作量大而且没有额外收益。内部表本来就是同一次 Python 运行两个后端，字面上满足。
- 备选：Rust 生成器另有 `--emit-cpp` 模式，产出与入库 C++ 生成物逐字节相同的文件，CI 比对。

**Q2 OB 几何树类型的单元处置**（RULEBOOK §4："OB 的几何 traits 和树类型（keep-cxx:bgk_shim）"）
- 事实：树类型（`ob_geo_tree.h` 1,306 行、`.cpp` 453 行）和 bin 类型（`ob_geo_bin.h` 770 行）除了给 Boost 用，还被 `share/geo` 里 30 多个 visitor（`ob_geo_affine_visitor` 等）和 102 个 ST_* 表达式的编排逻辑使用。若这两个单元整体 `keep-cxx`，这些 Rust 代码就没有树类型可用。现有 `units.tsv` 两种处置混用：`ob_geo_tree.h` 记为 `keep-cxx:bgk_shim`，`ob_geo_bin.h`、`ob_geo_bin_iter.h` 记为 `translate`（REVIEW）。
- 推荐：traits 两个头文件 `keep-cxx:bgk_shim`；bin/tree 类型的单元保持 `translate`（Rust 要用；`ob_geo_tree.h` 那一行要改回来），另外把原文复制进 shim 供 Boost 使用（复制件不是单元）。两边经 WKB 交换，各自的布局互不依赖。
- 备选：bin/tree 类型也 `keep-cxx`，用到它们的 visitor 一并移进 shim（C++ 孤岛扩大到约 1 万行以上，接近被否决的 G1）。

**Q3 query 里有 3 个签名用到 `ParseNode`**（`src/query/api/query/vector/ob_vector_index_util.h:484,498,900`），而 `parser` 依赖 `query`（RULEBOOK 0.3）。
- 推荐：由 1c 的 seam 表处理（RULEBOOK 0.4 "前向声明的向上类型"）：把这 3 个静态方法从 query 的 `VectorIndexUtil` trait 里拿出来，放进 sql 侧定义的一个窄 trait（调用方只在 sql 和 rootserver 的 DDL resolver 里），实现仍在 observer。
- 备选：把 `ParseNode` 视图和 `sys` 下移到一个比 query 更低的 crate（`parser` 拆成 `parser_sys` 和 `parser`），`ObItemType` 随之下移；改动 crate 图，需要修订 0.3。

**Q4 回调里重入解析器**：RULEBOOK 0.12 规定"回调绝不重入"，本文据此在入口拒绝重入（§3.1.5）。但没有核实 C++ 的 `lookup_pl_symbol` → `ObPLBlockNS::resolve_symbol`（`ob_pl_stmt.cpp:1878` 起）→ 外部命名空间解析，会不会在 SQL 解析过程中触发包的编译、从而再次解析。
- 推荐：1c 用调用图确认；确认不会，则维持拒绝。
- 备选（确认会）：允许嵌套（计数只用于诊断），并在嵌套返回后、外层再次分配失败时由 `parse_malloc` 蹦床直接 `ob_abort`，把"外层 longjmp 跳进已返回栈帧"的未定义行为变成确定的终止（`FIXED(port)`）；内部错误路径（`YY_UNEXPECTED_ERROR`）的残余风险要接受。

**Q5 解析器的内存检查**：01 文档假定解析器的 `new_node` 每 1024 个节点会调到 01 的 `try_check_mem_status`（01 §1 第 81 行、S 节第 869 行），但基线链接的是恒成功的 weak 版本（§3.1.9）。
- 推荐：保持基线行为，parser crate 不提供强定义；01 的那一行改为"基线未使用"。
- 备选：提供强定义接到 01 的 `try_check_mem_status`，解析器会在超出单查询内存上限时返回空节点、报 4013；这是行为变化，需要记为偏差（A.4）。

**Q6 `build_flags()` 的取值**：C++ 是 CMake 的构建类型名（`"RelWithDebInfo"`）。推荐 Rust 的 release profile 映射为 `"RelWithDebInfo"`、dev 映射为 `"Debug"`，环境变量 `OB_BUILD_FLAGS` 可覆盖；备选：直接用 Cargo 的 profile 名（`"release"`），`observer -V` 的输出随之不同。

**Q7 拼音表是否按数据拆分**：RULEBOOK 0.13 的拆分范围没有列 `ob_expr_to_pinyin_tab.cpp`，1c 在 `units.tsv` 里标了 REVIEW（事实见 §1.9 最后一行）。不做决定，它就按 `translate` 进翻译队列。
- 推荐：并入拆分范围，`data:pinyin_table` → `sql_data`，E1（与 `TIME_ZONE_NAMES` 一样是含 `ObString` 的结构体表），由 §5 G8 逐条核对；代码单元 `ob_expr_to_pinyin` 照常翻译。理由：1,502 条纯数据，翻译等于逐条抄写数字和带声调的字符串，脚本转换不会抄错，也不占翻译批次。
- 备选：维持 `translate`，另加一个金标准测试，把译文的 1,502 条与 C++ 的表逐条比对。

### 8.2 RULEBOOK 与源码不符（已追加到 `migration/infra/amendments.tsv`）

| # | 章节 | 问题 | 建议 |
|---|---|---|---|
| A1 | 0.12 | "解析器回调 server 19 个函数"是 macOS arm64 的结果；x86-64 上 `parse_node.c:551-553` 还调 `ob_parse_binary_simd`（定义在 `ob_parse_simd.cpp:204`），共 20 个 | 改为"19 个（x86-64 上 20 个）" |
| A2 | 0.12 | "和字符集的 5 处直接耦合，打一个小补丁"：这 5 处（`sql_parser_base.h:776-806`）都在 `/* */` 注释里，活代码只把 `charset_info_` 原样传给 3 个回调 | 删去补丁一句；改为"C 不解引用字符集指针，Rust 装入字符集 id（`ObCollationType`）作令牌" |
| A3 | 0.12 | "19 个回调 … 调用 sql 在启动时注册的 trait 方法；lookup_pl_symbol 改成由 sql::pl 在启动时注册的函数指针" | 细化为：分配类 8 个、murmurhash、两个栈检查直接调 1d 基础设施；字符集 3 个、strntoll 2 个、backtrace 2 个经 `ParserHost` 槽位；`lookup_pl_symbol` 的上下文 `pl_ns_` 本来就是每次解析的数据，改为装在 `ParseResult` 上的 `&dyn PlSymbolLookup`（全局函数指针还得在 sql 里把 `void*` 转回对象，需要 unsafe） |
| A4 | 0.13 | "系统变量 JSON → share"：C++ 生成器写 share 6 个文件、sql/session 2 个文件；94 个钩子都在 `src/sql/session/ob_system_variable.h:775-967`，share 里只以字符串出现 | 改为"元数据进 share，带钩子函数指针的工厂进 sql，映射表随 sql 的 build.rs 入库" |
| A5 | 0.13 | 数据/代码拆分的"4 个 .def 表格"：`ob_errno.def` 是生成器输入，`ob_lib_cache_namespace.def` 是类型名的 X-macro（代码），`ob_sql_topic.def`、`ob_sql_category.def` 没有包含点（死数据） | 从拆分范围里删去 `.def` |
| A6 | 0.14 | Boost 异常映射"17 个分支"：实际 20 个 catch 子句、18 个不同错误码（`ob_geo_dispatcher.h:1319-1362`），第 15 个在 Windows 上换成 `invalid_input_exception` | 改为"20 个 catch 子句，顺序不变" |
| A7 | 0.14 | 回调规则"回调上下文类型必须 Sync"：libxml2 的 SAX 和 curl 的 WRITEFUNCTION 都在调用方线程上同步回调，上下文（如持有 `&'q Arena` 的 SAX 解析器）做不成 `Sync` | 限定为"可能在外部线程上调用的回调"（vsag、OpenMP）；同线程同步回调以 `&mut dyn Trait` 借给那一次调用 |
| A8 | §4 改名表 | 本模块引入表外的新名字 | `parser` 行加 `ParserHost`、`PlSymbolLookup`；shim 行加 `SrsParams`；并写明规则：-sys 安全层照抄 C 名，借用限定的辅助函数用 `with_<C 名>`，合并多步的用 `<C 名>_<后缀>` |
| A9 | 0.14 | 第三方库的若干计数和版本：ICU 实际用 15 个函数（12 个 `uregex_*`，另 3 个 `u_strFromUTF8`、`u_strToUTF8`、`u_errorName` 在 icuuc，不是"只用 uregex、约 20 个"）；libxml2 是 11 个 SAX 回调（语法检查模式 1 个），不是 15 个；libcurl 在 Android 上是 8.2.1（`oceanbase.android.arm64.deps:11`） | 按实数更正；版本以 `third_party_versions.tsv` 为准 |
