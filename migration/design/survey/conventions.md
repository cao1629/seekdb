# 调查备忘：重复出现的 C++ 构造怎么译（RULEBOOK §1 生态取舍、§2 一构造一译法）

> 设计文档主题调查，只读。基线 `7d907abfa`：`git diff --stat 7d907abfa HEAD -- src rust deps unittest` 为空，工作区里的 `src/`、`rust/` 就是基线。日期 2026-10-11。除本文件外没有改动仓库，也没有构建；2.8 节的静态初始化和函数重载统计，读的是 00b 阶段留下的 `build_release/` 目标文件（`nm`、`c++filt`）。
>
> 两种计数口径：
> - **原始**：基线上受跟踪的全部 `src/**/*.{h,hpp,ipp,inl,cpp,cc,c}`（6,624 个），含注释。可直接复现：
>   `cnt() { git grep -P -o "$1" 7d907abfa -- 'src/*.h' 'src/*.hpp' 'src/*.ipp' 'src/*.inl' 'src/*.cpp' 'src/*.cc' 'src/*.c' | wc -l; }`
>   例如 `cnt '\bOB_FAIL\s*\('` 输出 99,951。
> - **代码**（默认口径）：只算 `migration/depmap/nodes.tsv` 里的 6,509 个 C/C++ 节点（已排除生成、第三方、不参与构建的文件），先去掉注释、把字符串字面量的内容置空，再匹配。
>
> 脚本在 `/tmp/conv-survey-7d907abfa-38738/scripts/`：`scan.py` 存放全部正则，`q.py`/`mq.py` 加同目录上一级的 `pats_*.tsv` 做补充计数，`retflow.py`/`tail.py` 做函数级错误流分析，`classes.py` 统计继承，`macros.py` 统计宏，`globals.py` 统计函数内静态量，`fmtspec.py`/`errno_def.py` 统计格式串，`fallthrough.py` 统计 switch 贯穿，`staticinit.py`/`overloads.py` 读目标文件。按本任务的规则，这些脚本没有入库。
>
> 正则计数都是近似值，标「估」的是推断。

## 1. 结论

1. **错误模型**：
   - 规模：声明 `int ret` 的函数有 39,077 个。其中约 27% 在失败后还会读写 `ret`，或者继续往下执行（重置、按错误码分派、`tmp_ret`、失败后的清理、没有守卫的尾部语句）。在这些函数里，改成 `?` 和原语义不等价。
   - 推荐：签名用 `ObResult<T> = Result<T, ObError>`，`ObError` 是 4 字节的错误码。函数体逐行保留 if/else 链，写成 `ob_fail!(ret = f())`，翻译阶段不用 `?`。行为对齐之后，再用同一套脚本把约 73% 的直通函数机械地改成 `?`。
2. **必须逐字节复刻的用户可见面**：
   - 错误码表：1,546 个错误码，其中 468 条消息带 printf 参数。消息经 512 字节的 `vsnprintf` 写进线程局部的警告缓冲区，错误码对上了才进错误包。
   - `TO_STRING` 的输出：`cmp_meta()` 原样返回它。
   - 哈希表的桶序和 `std::sort` 里并列值的顺序：它们决定没有 ORDER BY、或者 ORDER BY 有并列值时的行序。
   - 字符集、时间、ObNumber、浮点数的文本形式。
   - 因此容器、排序、哈希、格式化一律自写或逐行移植。翻译代码禁用 std `HashMap`、`sort_unstable`、用 `format!` 生成用户文本、chrono 这类"看起来等价"的替代品。
3. **宏 DSL 都能一对一承接**，每类各用一个同名的 `macro_rules!` 或 derive：
   - 日志约 2.76 万条语句，含 4.7 万个 `K()`；
   - 序列化约 1,000 个类、5,029 处宏；
   - `TO_STRING` 约 3,900 处定义。
   - 序列化格式会落盘（palf、事务日志、superblock），推荐保持字节兼容。
4. **生态**：
   - 禁用 async。
   - 新增依赖只有 proc-macro（syn/quote 已经在 Cargo.lock 里），外加并发、内存两个主题已经推荐的 parking_lot、stacker、smallvec、bumpalo 等。其余一律自写。
   - 沿用 rust/ 的现有约定：std::sync、cbindgen/cxx、`panic = "abort"`、serde 只用在 build.rs。
5. **共有 15 个问题需要你拍板**，见第 5 节。最关键的是：
   - Q1：错误流写法；
   - Q2：`ObError` 的形态，和内存、并发备忘里的枚举写法冲突；
   - Q9：落盘格式是否保持字节兼容；
   - Q6：`std::sort` 的并列顺序以哪个平台为准。

## 2. 事实

### 2.1 错误模型

| 构造 | 代码计数（原始） | 代表性位置 | 用户可见 |
|---|---|---|---|
| `int ret = OB_SUCCESS` | 38,028（38,228；只数字面量 `int ret = OB_SUCCESS;` 是 37,241）；另有 `INIT_SUCC(ret)` 1,622 | `src/sql/session/ob_basic_session_info.cpp:1081-1102`（典型的 if/else 链） | 无 |
| `OB_FAIL(x)` / `OB_SUCC(x)` | 99,431（99,951）/ 30,531（31,388）。失败分支是空 `{}` 的有 88,029 个，占 88.5%：seekdb 删掉了大部分失败日志，比如 `src/sql/ob_sql.cpp` 有 294 行 `OB_FAIL`，22 个 `LOG_WARN` 全在注释里 | 宏定义在 `src/oblib/lib/utility/ob_macro_utils.h:650-651`。可行性报告引的 654-655 是旧基线的行号 | — |
| 读累计结果：`OB_SUCC(ret)` / `OB_FAIL(ret)` | 36,941，其中：<br>• 循环条件 `for(…; OB_SUCC(ret) && …)` 10,798<br>• `while (OB_SUCC(ret)…` 1,571<br>• 守卫块 `if (OB_SUCC(ret)) {` 9,539<br>• 非空的 `if (OB_FAIL(ret)) {` 1,072 | `src/sql/ob_sql.cpp:162-171`：链结束后执行 `set_errcode(ret)` 和 `rollback_implicit_trans_when_fail` | — |
| 赋错误码 `ret = OB_X;` | 58,863 | | |
| 按错误码分派 `ret ==/!= OB_X` | 5,937。其中 `OB_ITER_END` 出现 3,043 次（650 个文件），`OB_HASH/ENTRY_NOT_EXIST` 1,374 次 | `src/sql/engine/aggregate/ob_hash_distinct_op.cpp:198-199` | 错误码被当作控制流 |
| 吞掉错误 `ret = OB_SUCCESS;` | 2,902 | 同上 | |
| `tmp_ret` / `OB_TMP_FAIL` / `COVER_SUCC` / `FAILEDx` | 917 / 616 / 75 / 629 | `ob_macro_utils.h:451,652-653` | |
| `CK` / `OX` / `OZ` / `OV`（隐式写 `ret`） | 2,902 / 2,109 / 5,662 / 336；`FALSE_IT` 2,574 | `src/oblib/lib/ob_check_macros.h:65,70,87,164` | |
| `OB_ISNULL` / `OB_NOT_NULL` / `IS_NOT_INIT` | 34,194 / 7,246 / 1,632 | `ob_macro_utils.h:656-658` | |
| `OB_ASSERT` / `abort_unless` / `ob_abort` | 732 / 240 / 140 | `ob_macro_utils.h:704`：Release 构建也会 abort | 进程退出 |
| 错误注入 `OB_E(` / `ERRSIM_POINT_DEF` | 192 / 99 | `src/oblib/lib/utility/ob_tracepoint.h:112,127` | 裁判 B 类（set_tp） |
| throw / try / catch | 41 / 28 / 49，共 118 处，分布在 15 个文件。`ob_geo_dispatcher.h` 一个文件就有 23 个 catch | • `src/share/geo/ob_geo_dispatcher.h:1195`：Boost.Geometry 的异常转成错误码<br>• `src/pl/ob_pl.cpp:178-180`：`catch (...)` 恢复 schema_guard 后重新抛出 | 通过错误码可见 |
| `LOG_USER_ERROR` / `_WARN` / `_NOTE` | 2,800 / 127 / 8；另有 `LOG_MYSQL_USER_*` 14、`FORWARD_USER_*` 30。有 652 行在参数里带 `.ptr()`，即 `%.*s` 同时传长度和指针 | `src/rootserver/fork_table/ob_fork_database_service.cpp:72-75`，消息定义在 `ob_errno.def:601` | 报错文本、SHOW WARNINGS |

**`ob_errno.def`**（`errno_def.py` 统计）：
- 共 1,546 个错误码：`DEFINE_ERROR` 858、`_EXT` 326、`_DEP` 172、`_EXT_DEP` 190。另有 2 条 `DEFINE_OTHER_MSG_FMT`。
- 531 个映射到 MySQL errno；1,015 个直接把 OB 码发给客户端（M1 = -1，比如 4012）。
- 共 78 种 SQLSTATE。
- 468 条用户消息带参数，用到 15 种 printf 写法：`%.*s` 343、`%s` 202、`%ld` 38、`%u` 30、`%d` 25、`%.192s` 17、`%f` 12、`%-.256s` 10、`%lu` 7、`%.64s` 5、`%c` 4、`%.80s` 3、`%.20s` 2、`%-.384s` 1、`%lf` 1。

**用户能看到什么**（错误码和报错文本都是裁判 A 类）：
1. **生成消息**：
   - `LOG_USER_*` 把 `<错误码>__USER_ERROR_MSG` 这个格式串交给 `ObLogger::log_user_message`，后者调用 `vsnprintf(buf, 512, …)`（`ob_log.cpp:654-665`）。
   - 结果写进线程局部的警告缓冲区（`ob_warning_buffer.h:241-259`）：每条最多 512 字节（`:51`），共 64 条，环形覆盖（`:141`）。
   - 格式和参数在编译期由 `__attribute__((format(printf,4,5)))` 检查（`ob_log.h:473-474`）；错误码必须是常量（`CHECK_LOG_USER_CONST_FMT`）。
2. **发错误包**（`src/observer/mysql/obmp_packet_sender.cpp:536-570`）：
   - 客户端收到的 errno 是 `ob_mysql_errno(err)`，它为 -1 时改用 OB 码。
   - 只有警告缓冲区里记录的错误码等于 `err`，才用缓冲区里的文本；否则用 `ob_errpkt_strerror(err)`。
   - 文本再按客户端字符集转码（`REPLACE_UNKNOWN_CHARACTER`），截到 512 字节。
   - 打开 `enable_rich_error_msg` 时，会在文本后追加时间和 trace id（`:595-640`），所以裁判里必须关掉它。
3. **结论：错误码走返回值，文本走线程局部的旁路，最后按错误码相等才拼到一起。** 如果把消息塞进错误值，或者把几个错误码合并成一个枚举变体，都会改变"错误码对不上就用默认文本"这一行为。

**函数级分析**（`retflow.py` + `tail.py`，用括号配对找函数体，估）：共 39,077 个函数声明了 `ret`。
- **7,855 个（20.1%）在失败后显式消费 `ret`**（各类可以重叠）：
  - 重置 2,179（5.6%）；
  - 按错误码分派 3,685（9.4%）；
  - 用 `tmp_ret` 946（2.4%）；
  - 非空的 `if (OB_FAIL(ret))` 991（2.5%）；
  - 在 `OB_FAIL` 之外写 `ret = f()` 2,117（5.4%）；
  - `COVER_SUCC` 62。
  - 另有 1,193 个（3.1%）不 `return ret`，是 void 或 bool 函数。
- **其余 31,222 个**：
  - 28,635 个（占全部 73.3%）在第一个可能失败的点之后，只剩受 `OB_SUCC(ret)` 守卫的语句和 `return ret`。**对它们来说，失败时提前返回和原语义完全等价。** 我从这 31,222 个里随机抽了 10 个手读，10 个都没有"失败后仍会执行"的语句。
  - 2,587 个（6.6%）在失败点之后还有没有守卫的语句，失败时照样执行：成员复位、清理、日志，还有少数没串进链里的 `OB_FAIL`，结果是"最后一个错误胜出"。这一类里含有误报，比如 `#undef`、`ARRAY_FOREACH`。
- **日志宏和 `CK/OX/OZ/OV/FAILEDx` 都按名字隐式读写局部变量 `ret`**（`LOG_ERRCODE_FOR_WARN ret`，`ob_log_module.h:262`）。Rust `macro_rules!` 的卫生规则不允许隐式捕获局部变量，所以 Rust 版的宏必须显式传入 `ret`。

### 2.2 日志

| 构造 | 代码计数 | 位置 / 备注 |
|---|---|---|
| `LOG_ERROR` / `LOG_WARN` / `LOG_INFO` / `LOG_DEBUG` / `LOG_TRACE` | 2,827 / 5,286 / 2,790 / 705 / 667（含 `*_RET` 646） | `src/oblib/lib/oblog/ob_log_module.h:1212-1225` |
| 模块宏 `XXX_LOG(level, …)` | 13,964；printf 式的 `_XXX_LOG` / `_LOG_*` 348 / 39；`OB_LOG` 816；`LOG_DBA_*` 135。日志语句合计约 27,600 条 | 定义了 125 个 `XXX_LOG`、126 个 `_XXX_LOG`、243 个 `_RET` 变体 |
| `K()` / `K_()` / `KR()` / `KP()` / `KPC()` / 其他 K 宏 | 47,224 / 12,427 / 3,178 / 2,967 / 3,152 / 275 | `ob_log_module.h:1240-1301` |
| `#define USING_LOG_PREFIX` | 2,336 个文件，46 种前缀（SQL_ENG 624、STORAGE 398、LIB 147、SQL_RESV 137…） | `ob_log_module.h:1198-1209` |
| 模块表 | 27 个父模块、84 个子模块（共 111 个 `DEFINE_LOG_SUB_MOD`） | 父模块在 `ob_log_module.h:48-75`，子模块紧随其后 |
| 限流 `REACH_TIME_INTERVAL` 等 | 353 | |
| 跟踪：`FLT_*` / `NG_TRACE`、`REC_TRACE` / `DEBUG_SYNC` | 208（4 个文件）/ 126 / 173 | `SHOW TRACE` 在语法里（`sql_parser_mysql_mode.y:12739`），但没有任何用例 |

**用户可见的部分**：
- 日志文件本身不进裁判。
- 但控制日志的 13 个公开参数，取值、默认值和检查器属于 A 类或灰色地带。它们定义在 `rust/config/parameters.yaml:168-322`：
  - 跟踪与报错：`trace_log_slow_query_watermark`、`enable_record_trace_log`、`max_string_print_length`、`enable_record_trace_id`、`enable_rich_error_msg`；
  - syslog：`syslog_level`、`syslog_io_bandwidth_limit`、`diag_syslog_per_error_limit`、`max_syslog_file_count`、`enable_async_syslog`、`syslog_disk_size`、`syslog_compress_func`、`syslog_file_uncompressed_count`。
- 系统变量 `ob_log_level`（`ob_system_variable_init.json:1468`）同理。
- `ob_log_level` 和 hint 设置的是**按线程、按模块**的级别表 `ObLogIdLevelMap`（`ob_log.h:135-227`，一张 64×65 的 `int8_t` 表，容量定义在 `ob_log.h:99-100`）。`log` 和 `tracing` 都没有对应物。

### 2.3 序列化

| 构造 | 代码计数 |
|---|---|
| `OB_SERIALIZE_MEMBER*` | 669 |
| `OB_UNIS_VERSION` / `_V` / `_PV` | 914 |
| `OB_DEF_SERIALIZE` / `DESERIALIZE` / `SERIALIZE_SIZE` | 598 |
| `OB_UNIS_ENCODE` / `DECODE` / `ADD_LEN*` | 1,940 |
| `LST_DO_CODE` | 668 |
| `NEED_SERIALIZE_AND_DESERIALIZE` + `DEFINE_SERIALIZE…` | 80 + 160 |
| 宏合计 | 5,029 处，795 个文件（sql 2,570、share 1,076、storage 740、oblib 431） |
| 可序列化的类（`OB_UNIS_VERSION*` + `NEED_SERIALIZE…`） | 993 处声明，400 个文件：sql 400、share 259、storage 160、oblib 75、query 43、standby 20、data_plane 17、logservice 12 |
| `serialization::xxx(` / `encode_*`·`decode_*` / 手写的 `int serialize(char *` | 1,213 / 864 / 238 |

格式要点：
- **unis 头**：
  - 由版本号（varint）加长度组成；长度占 5 个字节，是"补齐的 varint"（`src/oblib/lib/utility/serialization.h:96`、`:326-343`）。
  - 反序列化要求版本号**完全相等**，否则返回 `OB_NOT_SUPPORTED`（`ob_unify_serialize.h:143-155`）。
  - 读完之后跳到 `pos_orig + len`，不认识的尾部直接跳过（`:189-208`）。
  - 每个成员先判断 `pos < data_len` 再解码（`:60-66`）。因为是就地反序列化，旧数据里缺的尾部成员保留构造函数给的值。
- **基本类型**：
  - 定长整数按大端写（`serialization.h:212-223`）；
  - 泛型 `encode(int16/32/64)` 写成无符号 varint，负数占 10 字节；
  - float/double 写成位模式的 varint；
  - 枚举写成 `vi32`，解码时直接 `static_cast<T>(任意 int)`，不校验取值（`:1649-1670`）。
- **会落盘的**：
  - palf 的 `LogEntry`/`LogGroupEntry`（`log_entry.h:51`、`log_group_entry.h:58`）；
  - 事务日志（`ob_tx_log.h:201-591`）；
  - superblock 和 slog（`ob_super_block_struct.h:54-221`、`ob_storage_log_struct.h:60`）；
  - tablet 和 sstable 的元数据。
- **只在进程内用的**：sql 的 400 个多数是 PX/DTL/DAS 进程内消息，只需自洽，和 ir-graphs 备忘 2.8 一致。
- **线上格式**：seekdb 没有内部 RPC（`RPC_AP|RPC_S|OB_DEFINE_PROCESSOR` 0 处），唯一的线上格式是 MySQL 协议，已经是 Rust 写的 sql-nio。
- **字节兼容是政策问题**：裁判不跨二进制，也不读内部表（B 类），所以对齐不强制兼容。storage-txn 备忘结论 4 推荐"读写同一格式"。

### 2.4 TO_STRING

| 构造 | 代码计数 |
|---|---|
| `TO_STRING_KV` | 2,651 |
| `VIRTUAL_TO_STRING_KV` / `INHERIT_TO_STRING_KV` | 456 |
| `DECLARE_TO_STRING` + `DEF_TO_STRING` / `TO_STRING_EMPTY` | 206 + 126 / 22 |
| 手写的 `int64_t to_string(char *, const int64_t)` | 425 |
| `J_KV`/`J_OBJ_*` 等 / `databuff_print_*` | 1,382 / 213 |
| `ObCStringHelper`（`ob_print_utils.h:288`）/ `helper.convert(` | 92 个文件 / 364（原始口径） |

**输出格式**：
- 整体是 `{k:v, k:v}`；
- double 用 `%.18le`，float 用 `%.9e`，bool 输出 `True/False`，空指针输出 `NULL`（`ob_print_utils.h:269-276`）；
- 数组是 `[cnt:N, a, b]`（`ob_iarray.h:68-86`）。

**能流到用户面前的出口**：
1. SQL 函数 `cmp_meta(expr)` 直接返回 `helper.convert(obj_meta_)`（`src/sql/engine/expr/ob_expr_collation.cpp:373-386`）。期望输出是 `{type:"BIGINT", collation:"binary", coercibility:"NUMERIC"}`（`tools/deploy/mysql_test/test_suite/expr/r/mysql/collation_expr.result:289-291`；裁判灰色地带第 9 条）。
2. 报错文本：至少 77 处 `LOG_USER_*`（单行 grep 的结果）把 `helper.convert(x)` 或 `to_string` 的结果当参数，例如 `src/pl/sys_package/ob_dbms_stats.cpp:1157`。
3. 虚拟表：`observer/virtual_table` 下的 155 个 `.cpp` 里，有 35 个用 to_string、helper 或 databuff_printf 生成列值。属于裁判 B 类。
4. EXPLAIN 文本主要靠 `BUF_PRINTF`（1,139 处）生成。它也是 B 类，但 EXPLAIN 本身必须能执行成功（灰色地带第 10 条）。

### 2.5 容器

| 类型 | 代码计数 | 备注 |
|---|---|---|
| `ObIArray<` | 18,141 | 接口，有 14 个传递子类，是最主要的参数类型 |
| `ObSEArray<` / `ObArray<` / `ObFixedArray<` | 5,621 / 2,449 / 424 | `ObArray` 就是 `ObSEArrayImpl<T, 0>`（`ob_array.h:270`、`:859`），两者是同一套实现 |
| `ObArrayWrap`/`ObArrayHelper` / `Ob2DArray`/`ObSegmentArray` / `ObVector`/`ObSortedVector` | 182 / 80 / 176 | |
| `ObList<` / `ObDList<` / `ObDLinkBase<` | 67 / 155 / 93 | `ObDLinkBase` 有 739 个传递子类，其中 573 个是 `ObExprOperator`（`ob_expr_operator.h:304`）。但仓库里没有 `ObDList<…ExprOperator>`，唯一的用处是 `:992` 的 `reset()`，所以这 573 个类的链表基类看来是遗留（估） |
| `ObHashMap<` / `ObHashSet<` / `ObHashTable<` | 485 / 204 / 7 | 键是值类型（整数、ObString）的有 432 处 |
| `ObLinearHashMap<` / `ObLinkHashMap<` | 136 / 8 | 并发结构，归并发主题（DashMap） |
| 位集 `ObBitSet`/`ObFixedBitSet`/`ObSqlBitSet` / `ObBitmap`/`ObBitVector` | 603 / 1,102 | |
| `std::vector/map/set/...` / `std::string` / `std::function` / 智能指针 | 48 / 124 / 89 / 63 | |
| 调用：`.at(` / `push_back(` / `get/set/erase_refactored` / `foreach_refactored` | 20,425 / 7,513 / 1,253 / 53 | |

语义要点：
- **分配失败**：`push_back` 返回 int，分配失败时返回 `OB_ALLOCATE_MEMORY_FAILED`；数组类型都带分配器模板参数。
- **越界访问**：
  - `at(i)` 用 `OB_ASSERT` 检查，所有构建都会 abort（`ob_array_wrap.h:40-53`）；
  - `operator[]` 只记一条 ERROR 日志，然后照样返回元素（`ob_array.h:323-334`，源码注释写着 `// dangerous`）；
  - `at(i, obj)` 返回 `OB_ARRAY_OUT_OF_RANGE`（`:310-320`）。
- **键是值类型时，ObHashMap 的迭代顺序是确定的**：
  - 桶号 = `hash % bucket_num`（`ob_hashtable.h:1139`），新节点插到链头（`:1044`）；
  - 整数键的哈希就是值本身，`ObString` 用 `murmurhash(…, 0)`，其他类型调用可能失败的 `key.hash(uint64_t &)`（`ob_hashutils.h:749-855`）。
  - std `HashMap` 每个进程随机取种子，hashbrown 的探测方式也不一样，两者的迭代顺序都会变。
- **SQL 引擎自带的哈希表**：
  - 有 `ObExtendHashTable`（`ob_exec_hash_struct.h:42`，桶号是 `hash_val & (cnt - 1)`，`:143`）和 `ObHashPartInfrastructure`（`ob_hash_partitioning_infrastructure_op.h:2916`）。
  - 桶序就是 hash GROUP BY、DISTINCT，以及不带 ORDER BY 的连接的输出顺序。
  - 裁判灰色地带第 11 条（executor.basic 里约 80 个连接、subquery_sj_firstmatch、group_by_basic）目前按 A 类保留。
- **排序**：
  - `lib::ob_sort` 就是 `std::sort`（`ob_sort.h:28-40`），排序算子直接用它（`ob_sort_op_impl.cpp:1441-1512`）。
  - 这是不稳定排序，并列行的先后由标准库的 introsort 决定。
  - macOS 构建链接 libc++（符号里是 `std::__1`），Linux 链接 libstdc++（`CMakeLists.txt:221-223`）。也就是说，C++ 自己在两个平台上的结果就可能不同。
  - 裁判的期望输出是在 macOS 上录的（00b 报告 DEV-005）。

### 2.6 字符串与缓冲区

- **`ObString`**（17,398 处）：
  - 布局是 `int32 buffer_size_` + `int32 data_length_` + `char *ptr_`（`ob_string.h:794-796`），本身不拥有内存；
  - 也能当定长写缓冲用（`assign_buffer` 50 处、`write` 194 处）；
  - 作为成员声明 1,023 处；`.ptr()` 5,591、`.length()` 6,360、`assign_ptr` 931。
  - 相关类型：`ObSqlString` 1,374、`ObStringBuffer` 274；定长数组 `char x[N]` 1,106。
- **printf 一族**（`fmtspec.py` 统计）：
  - 带字面格式串的调用共 3,968 处：`databuff_printf` 1,911、`BUF_PRINTF` 1,104、`snprintf` 413、printf 式日志 317、`fprintf` 153、`sprintf` 34、`printf` 32。
  - 常见写法：`%s` 1,116、`%ld` 914、`%d` 525、`%.*s` 396、`%lu` 212、`%p` 150、`%02d` 73。
  - 浮点写法：`%e` 34、`%f` 14、`%.9g` 13、`%.2f`/`%.2lf` 24、`%lf` 11、`%.18le` 3。
  - Rust 的 `format!` 和 C 在以下几处不同：`%e`/`%g` 的格式、`nan`/`inf` 的拼写、`%p`、`%.*s` 遇到 NUL 就截断。
- **字符集**：
  - `ObCharset::` 2,825 处，其中比较、哈希、大小写转换 581 处；`CS_TYPE_*` 1,798；`ObCollationType` 1,776。
  - 字符集表来自 MySQL：`src/oblib/lib/charset` 共 24,815 行，其中 `ob_ctype_uca.cc` 7,129 行。
  - 按字节比较：`strcmp` 一族 251 处；`ObString::compare` 就是 memcmp 再比长度。
- **C 库语义**：
  - ctype 函数（`isspace` 等）184 处，分布在 52 个文件；其中 `ob_time_convert.cpp` 27 处，影响日期解析，用户可见。
  - `strtol`/`strtod`/`atoi` 一族 68 处。
  - Rust 的 `u8::is_ascii_whitespace` 不包含 `\v`；`str::parse` 不跳过前导空白，也不接受只解析一部分。

### 2.7 值类型（都属于用户可见的语义）

| 对象 | 规模 | 必须逐位复刻的部分 |
|---|---|---|
| `ObObj` | 6,142 处；`ObObjType` 2,287 处，具体类型值的记号 5,456 处 | 16 字节布局：<br>• `ObObjMeta`（`type_`/`cs_level_`/`cs_type_`/`scale_`，`ob_object.h:112`、`:508-518`）<br>• 联合体 `ObObjValue`（`:1257`）<br>• `val_len_`<br>另有代码依赖 `offsetof`（`:459` 共 15 处，`:1978`） |
| `ObDatum` | 5,539 | `ObDatumDesc` 的位域 `len_:29 flag_:2 null_:1`（`ob_datum.h:141-145`） |
| `ObNumber` | 1,770 处；实现 10,103 行 | • 10⁹ 进制，最多 9 段（`ob_number_v2.h:143-158`）<br>• `ObNumberDesc`（`ob_define.h:1786`）<br>• 舍入、除法精度、文本格式、比较、哈希 |
| `ObDecimalInt` | 230 处；`src/oblib/common/wide_integer` 约 4,100 行 | • int32…int512 的补码<br>• 按精度选宽度<br>• 溢出时的报错 |
| 时间 | `ObTimeConverter` 有 134 个静态函数（`ob_time_convert.h:319`），908 处调用 | • 解析和格式化规则<br>• 打包类型 `ObMySQLDate`（day:5 month:4 year:14）和 `ObMySQLDateTime` 直接按整数比较大小，所以比较顺序依赖位布局（`ob_time_def.h:29-80`）<br>• 时区数据 `ob_timezone_info.cpp` 39,989 行<br>00b 的变异 M3（改掉月份补零）被 38 个用例抓到 |
| 浮点数文本 | `ob_dtoa.cc` 2,440 行（含 56 个 goto）；`ob_gcvt`/`ob_fcvt` 91 处 | MySQL 的 dtoa 算法 |
| 哈希、校验和 | murmurhash/xxhash/fnv/crc64 调用 531 处 | • crc64 用自带的表，外加 SSE4.2 路径（`ob_crc64.cpp:51-360`），结果会落盘<br>• 数据的哈希值决定 hash 分区落在哪个分区（`ObObj::hash_murmur`，`ob_expr_func_part_hash.cpp:118-152`），也决定 hash 算子的输出顺序 |

### 2.8 类的形态与其他语言构造

| 构造 | 事实 | 来源 |
|---|---|---|
| 继承 | 带基类的 class/struct 共 3,920 个。继承深度 1/2/3/4/5/6 分别有 1,926/882/813/161/17/3 个；最深的是 `ob_vector_index_ddl_pipeline.h:364` | `classes.py`，按简单名解析，估 |
| 多重继承 | 120 个，多为接口混入：`ObICheckpointSubHandler` 15、`ObIReplaySubHandler` 14、`ObILocalLogHandler` 13。虚继承 2 个 | 同上 |
| 虚函数 | 11,591 个虚函数，另有 3,670 个虚析构；纯虚 2,487；`override` 6,889；`final` 1,186 | `scan.py` |
| 向下转型 | `static_cast` 到 `Ob*`/`Ob&` 共 5,813 处（这是上限，也包含 `void*`→`T*`）；`dynamic_cast` 272；`typeid` 143 | `q.py`、`scan.py` |
| CRTP | 109 个类的基类模板实参是派生类自己，多为 `ObDLinkBase<Self>`；`static_cast<D*>(this)` 186 处 | `classes.py`、`mq.py` |
| 模板 | `template<` 7,161 处（933 个文件）；全特化 1,045、偏特化 459、非类型模板参数 974、变参 212、SFINAE 181、`if constexpr` 35。用得最多的是 `ob_obj_funcs.h` 178、`ob_linear_hash_map.h` 153 | `scan.py`、`mq.py` |
| 函数重载 | 66,051 个非内联函数名里，3,178 个有多个参数表（4.8%）。最多的是 `ObRawExprPrinter::print` 116、`ObCmdExecutor::execute` 79、`ObStaticEngineCG::generate_spec` 62 | `overloads.py`（读目标文件），不计模板实例 |
| 默认参数 | 约 11,386 处 | 正则估算 |
| 运算符重载 | 比较 1,002；`operator=` 605；`()`/`[]`/`->`/`*` 1,033；算术和位运算 356；`new`/`delete` 34；转换运算符 18。另有 `assign()` 923、`deep_copy()` 438、`DISALLOW_COPY_AND_ASSIGN` 2,319 | `q.py`、`scan.py` |
| 静态初始化 | • 365 个目标文件里有 268 个带动态初始化（`_GLOBAL__sub_I_`）<br>• `__attribute__((constructor/init_priority))` 11 处（如 `ob_crc64.cpp:51`、`observer/main.cpp:28`）<br>• 函数内的 `static ObX` 258 处<br>• `thread_local`/`RLOCAL` 233 处 | `staticinit.py`、`globals.py` |
| 定义类型或函数的宏 | • `#define` 共 14,135 个，其中函数式 3,901 个<br>• 定义类型的宏 80 个，调用 1,321 次：`OB_GEO_*_FUNC_BEGIN` 287+244+78+52+37（`ob_geo_dispatcher.h:1434-1499`）、`FLT_DEF_SPAN` 74、`REG_LOG_SUB_MOD` 72<br>• 定义函数的宏 128 个，调用 956 次：`DEFINE_CMP_FUNCS_NULLSAFE_ENTRY` 70（`ob_obj_compare.cpp:2367`）、`DEFINE_PRINT_KV` 64<br>• `REGISTER_OPERATOR` 是一张编译期的模板特化表（`ob_operator_reg.h:68-100`） | `macros.py`，不含前面已单列的序列化和 TO_STRING 宏 |
| 枚举 | 1,412 个，其中 `enum class` 209 个；int→枚举的 `static_cast` 至少 781 处；以枚举上限作长度的数组 834 个 | `scan.py` |
| goto | 93 处，全在 5 个从 MySQL 派生的文件里：`ob_dtoa.cc` 56、`ob_ctype_simple.cc` 23、`serialization.h` 12 等 | 同上 |
| switch | 1,730 个，case 13,981 个；非空的贯穿（fall-through）约 144 处，例如 `ob_number_v2.cpp:315`、`ob_charset_string_helper.h:128-140` | `fallthrough.py`，估 |
| union / 位域 | union 304 个（179 个文件）；位域成员约 1,032 个（127 个文件），集中在 `ob_object.h:94`（60）、`ob_mysql_packet.h:113`（42）、`parse_node.h:137`（41）、`ob_basic_session_info.h:222`（38） | `scan.py` |
| 内联汇编 | 39 处，13 个文件：`ob_tsc_timestamp.h` 7、`easy_atomic.h` 6、`ob_smart_call.cpp` 5（用于换栈）、`atomic128.h` 4、`ob_crc64.cpp` 4 | 同上 |
| SIMD | • x86 共 628 处、20 个文件：`_mm_` 284/16、`_mm256_` 201/11、`_mm512_` 143/7；最多的是 `ob_vector_l2_distance.h` 212、`ob_parse_simd.cpp` 72<br>• NEON 333 处、6 个文件：`encoding/neon`、向量距离、`ob_sse_to_neon.h`<br>• 按 CPU 分派 164 处、23 个文件<br>• 另有生成文件 `ob_generated_unalign_simd_bp_func.cpp` 里 7,200 处，已排除，归生成器主题 | 同上 |
| `__builtin_*` | 195 处：prefetch 41、clz/ctz 70、crc32 18、popcount 9、溢出检查 7 | 同上 |
| `SMART_CALL` | 1,126 处、148 个文件，其中 sql 988 处。栈快用完时分配约 2 MiB 的扩展栈，累计上限 10 MiB，超出就返回 `OB_SIZE_OVERFLOW`（`ob_smart_call.h:28-36`、`:115-129`）。深层嵌套 SQL 的报错就来自这里，用户可见 | 同上 |
| 其他 | `DEFER` 23、`ARRAY_FOREACH` 191、用 `(void)x` 忽略返回值 1,481、`ATOMIC_*` 2,988、锁类型 825 处、锁守卫 1,177 处、`ObLatchIds` 275 处 | 同上 |

### 2.9 rust/ 已有的约定

- **工作区**：
  - 成员是 `config` 和 `sql-nio`（`rust/Cargo.toml:5`）；
  - release 配置：thin LTO、`codegen-units = 1`、`panic = "abort"`（`:11-16`）；`cmake-debug` 同样是 `panic = "abort"`（`:21-23`）；
  - 工具链钉在 1.98.1（`rust-toolchain.toml:6`）；edition 2021。
- **sql-nio**：
  - 用 mio，注释写明"no async runtime"（`sql-nio/Cargo.toml:22-24`）；
  - rustls 只开 ring（`:36`）；cbindgen 钉在 `=0.29.4`（`:45`）；
  - 同步原语用 `std::sync::{Mutex, RwLock, Condvar, LazyLock}`（`src/lib.rs:25`），哈希表用 std `HashMap`（`registry.rs:18`）；
  - 日志只有 3 处 `eprintln!`（`reactor.rs:327,597,963`）。
- **config**：
  - cxx bridge 由 build.rs 生成（`config/build.rs:153`）；serde/serde_yaml 只是 build 依赖（`config/Cargo.toml:15-16`）；
  - 错误类型是手写的 `Error` 加 `Display`（`config/src/lib.rs:36-75`）；C ABI 返回 int 加一个错误结构（`config/src/ffi.rs`）。
- **CI**：对 sql-nio 跑 `clippy -D warnings` 和 `cargo audit`（`.github/workflows/rust-checks.yml:34-67`）。
- **jemalloc**：`deps/external` 已经用 Cargo 构建 jemalloc（`seekdb-jemalloc-sys 0.2.2`，见 `deps/external/Cargo.toml`）。
- **Cargo.lock 里已有的传递依赖**：syn 2.0.119、quote、proc-macro2、thiserror 2.0.21、hashbrown 0.17.1、log 0.4.34、memchr。

## 3. 建议的 §1 生态取舍表

| 领域 | 决定 | 理由 / 证据 |
|---|---|---|
| 异步模型 | **禁用**。不引入 tokio、async-std 或任何执行器，翻译代码里不得出现 `async fn`；保持 OS 线程和线程池；网络层继续用 sql-nio 的 mio reactor | 没有协程：ucontext 只有 6 处，在信号处理和 libeasy 里；sql-nio 已经不用 async runtime；与并发备忘结论 1 一致 |
| 错误 | 自有 `ObError`（`NonZeroI32` 的新类型，4 字节）+ `ObResult<T = ()>`。常量由 errno 生成器（build.rs）产出，沿用 C++ 名字（如 `OB_ITER_END`）。不用 thiserror、anyhow | 错误码本身是用户数据，需要做整数比较（分派 5,937 处）。见 Q2 |
| 日志 | 自有 `ob_log`：宏加运行时，包括模块表、线程级级别、限流、异步写、轮转、压缩。翻译代码禁用 `log`、`tracing`、`eprintln!` | 13 个公开参数和 `ob_log_level` 的语义；`ObLogIdLevelMap` |
| 格式化 | 自有 proc-macro `ob_printf!`：编译期解析 C 格式串、检查参数类型，运行时逐字节复刻 C 的输出。`databuff_printf!`、`buf_printf!`、`append_fmt!`、`log_*_fmt!` 都建在它上面。errno 生成器为每条带参消息生成一个带类型的函数 | 3,968 处字面格式串；与第三方主题的选项（a）一致 |
| 容器 | • 存储类型按内存主题（`Vec`/`SmallVec`/`ArenaVec`），一律通过 oblib 的 `ObIArray<T>` trait 访问，方法名保持 `push_back`/`at`/`count` 等，`push_back` 可失败<br>• `ObHashMap`/`ObHashSet`/`ObSortedVector`/位集在 oblib 里逐行移植<br>• 翻译代码禁用 std `HashMap`/`HashSet`、hashbrown、indexmap<br>• `BTreeMap`/`BTreeSet` 只用来对应 C++ 的 `std::map`/`std::set` | 迭代顺序用户可见；分配失败要报 4013。见 Q5、Q11 |
| 排序 | oblib 的 `ob_sort` 逐行移植基准平台的 `std::sort`；禁用 `sort_unstable*`；稳定的 `sort_by` 只用来对应 `std::stable_sort` | 2.5 节，见 Q6 |
| 哈希、校验和 | murmurhash、fnv、wyhash、crc64（含 SSE4.2 路径）逐行移植；xxHash 按第三方主题可以用 xxhash-rust，前提是金标准测试证明等价 | 结果会落盘，也影响输出顺序 |
| 字符集、时间、数值、浮点文本 | 逐行移植；不得用 chrono、time、encoding_rs、icu4x、rust_decimal、num-bigint、ryu 来实现 SQL 语义 | 2.7 节 |
| 序列化 | 自有 `ob_serialize`（`macro_rules!` + trait），逐字节复刻 unis 格式；serde 只用在 build.rs | 与 `rust/config/build.rs` 的做法一致 |
| 锁、原子、无锁结构 | 按并发主题：`oblib::sync`（底层 parking_lot）；`std::sync::atomic` 一律 SeqCst；crossbeam-epoch/channel、dashmap、arc-swap | 并发备忘结论 2–4 |
| 栈 | 用 `stacker`，包在 `oblib::smart_call` 里，保留 64 KiB 余量、10 MiB 上限和 `OB_SIZE_OVERFLOW` | 与并发、IR 两个备忘一致 |
| 分配器 | 按内存主题：bumpalo arena + jemalloc 全局分配器 | 内存备忘结论 1–2 |
| 宏 | 新建一个 proc-macro crate（syn/quote 已在 Cargo.lock 里），提供 `ob_printf!`，可选 `#[derive(ObToString)]` | |
| FFI | 沿用 rust/ 的做法：Rust 导出走 cbindgen C ABI，调用 C++ 走 cxx；`panic = "abort"`；第三方 C++ 库抛出的异常在 C++ shim 里接住，转成错误码 | `rust/Cargo.toml:16` |
| SIMD、汇编 | 用 `std::arch` 里的同名 intrinsic（AVX-512 在 1.89 已稳定），配 `#[target_feature]` 和运行时检测；`asm!` 只留给 rdtsc 这类没有替代品的地方；不引入 SIMD crate | 2.8 节 |
| 时间源、随机数 | 移植 `ObTimeUtility`/`ObClockGenerator`、`ObRandom` 和 MySQL 的 `RAND(seed)` 算法；翻译代码不直接用 `std::time::SystemTime` 和 `rand` | 时间戳和 RAND 的结果用户可见 |
| 第三方 crate 的默认规则 | 本表没有点名的一律不准引入 | kit 模板 §1 |

## 4. 建议的 §2 映射表

| 源构造 | 唯一的 Rust 译法 | 说明 / 证据 |
|---|---|---|
| 返回 `ret` 的 `int f(…, T &out)` | `fn f(…, out: &mut T) -> ObResult` | 输出参数保持 `&mut`，不改成返回值：失败时写了一半的输出，调用方仍可能读到 |
| `int ret = OB_SUCCESS;` / `INIT_SUCC(ret)` | `let mut ret: ObResult = Ok(());` | |
| `OB_FAIL(e)` / `OB_SUCC(e)` / `OB_TMP_FAIL(e)` | `ob_fail!(ret = e)` / `ob_succ!(ret = e)` / `ob_fail!(tmp_ret = e)`，展开为 `{ ret = e; ret.is_err() }`；if/else 链的结构原样保留 | 2.1 节；翻译阶段不用 `?`（Q1） |
| `OB_FAIL(ret)` / `OB_SUCC(ret)` / `OB_SUCCESS !=/== ret` | `ret.is_err()` / `ret.is_ok()` | |
| `ret = OB_X;` / `return OB_X;` / `return ret;` | `ret = Err(OB_X);` / `return Err(OB_X);` / `ret`（或 `return ret;`） | |
| `OB_X == ret` / `ret != OB_X` | `ret.is(OB_X)` / `!ret.is(OB_X)` | `ObResultExt::is` |
| `ret = OB_SUCCESS;` | `ret = Ok(());` | |
| `COVER_SUCC(x)` / `FAILEDx(e)` | `ret.and(x)` / 按宏展开：`if ret.is_err() {} else if ob_fail!(ret = e) {…}` | `ob_macro_utils.h:451,653` |
| `CK` / `OX` / `OZ` / `OV` / `OZX1` / `OZX2` | 同名小写宏，显式传 `ret`：`ck!(ret, c1, c2)`、`ox!(ret, stmt)`、`oz!(ret, f(a); a)`、`ov!(ret, cond, OB_X; args)` | 宏的卫生规则不允许隐式捕获 |
| `FALSE_IT(stmt)` | `false_it!(stmt)`，等价于 `{ stmt; false }` | 2,574 处 |
| `OB_ISNULL(p)` / `OB_NOT_NULL(p)` | 按缺口清单定可空性：可空就用 `Option`，写成 `p.is_none()`/`p.is_some()`；清单判为不可空的，删掉检查 | 34,194 处 |
| `IS_NOT_INIT`、`is_inited_`、`int init(…)` | 保留两段式初始化：字段 `is_inited_: bool`，`fn init(&mut self, …) -> ObResult`；构造函数改成 `Default`/`new()`，不可失败 | `int init(` 3,047 处、`is_inited_` 5,264 处 |
| `OB_ASSERT` / `abort_unless` / `ob_abort` | `ob_assert!(x)`：记 ERROR 日志和调用栈后 `std::process::abort()`，Release 构建也一样 | `ob_macro_utils.h:704` |
| `OB_E(EN_X) OB_SUCCESS`、`ERRSIM_POINT_DEF` | `ob_e!(EN_X)`、`errsim_point!(…)`，保留注入点，用 `cfg(feature = "errsim")` 控制 | 裁判 B 类，但内部测试仍要用 |
| throw / try / catch | 不用异常：<br>• 内部的 throw 改成 `return Err(..)`<br>• 第三方 C++ 异常在 C++ shim 里 catch 后转错误码（如 `ob_geo_dispatcher.h:1195`）<br>• `catch(...){恢复; throw;}` 改成用 RAII 守卫恢复现场 | 118 处；在 `panic=abort` 下没法 `catch_unwind` |
| `ob_errno.def` | build.rs 生成：`pub const OB_X: ObError`；`ob_mysql_errno`/`ob_sqlstate`/`ob_errpkt_strerror`/`ob_str_user_error` 几张表；每条带参消息一个带类型的函数 | 第三方主题的 Ga |
| `LOG_USER_ERROR/WARN/NOTE(OB_X, args…)` | `log_user_error!(OB_X, args…)`，调用生成的 `user_msg::OB_X(level, args…)`。<br>参数类型由格式串决定：`%.*s`→`(i32, &[u8])`，`%s`→`&[u8]`，`%ld`→`i64`，`%d`→`i32`，`%u`→`u32`，`%lu`→`u64`，`%f`/`%lf`→`f64`，`%c`→`i32`。<br>输出要逐字节等于 `vsnprintf(buf, 512, …)`（截到 511 字节，`%s`/`%.*s` 遇 NUL 即停），写入线程局部的警告缓冲区 | `ob_log.cpp:654-665`；给 `ret` 赋值仍然单独写一句 |
| `FORWARD_USER_*`、`LOG_USER_ERROR_WITH_LINE_COL` | `forward_user_error!(…)`、`log_user_error_line_col!(…)` | |
| `LOG_WARN`/`LOG_ERROR(msg, kv…)` | `log_warn!(ret, "msg", kv…)`，错误码显式传入 | C++ 隐式读 `ret`（`ob_log_module.h:262`） |
| `LOG_INFO`/`DEBUG`/`TRACE(msg, kv…)` | `log_info!("msg", kv…)` | 这三级在 C++ 里错误码固定为 0 |
| `LOG_WARN_RET(OB_X, …)`、`XXX_LOG_RET(level, OB_X, …)`、`XXX_LOG(LEVEL, …)` | `log_warn!(OB_X, …)`、`ob_log!(XXX, LEVEL, OB_X, …)`、`ob_log!(XXX, LEVEL, ret, …)` | 错误码参数既接受 `ObResult` 也接受 `ObError` |
| `#define USING_LOG_PREFIX P` | 在模块顶部写 `const LOG_PREFIX: ObLogMod = ObLogMod::P;`，`log_*!` 在调用处解析这个名字 | 46 种前缀 |
| `_LOG_*`、`_XXX_LOG`（printf 式） | `log_warn_fmt!(ret, "C 格式串", args…)` | 走 `ob_printf!` |
| `K(x)` / `K_(x)` / `KR(x)` / `KP(x)` / `KPC(x)` / `"k", v` | `k!(x)`（键是 `stringify!(x)`）/ `kv!("x", self.x_)` / `kr!(x)` / `kp!(x)` / `kpc!(x)` / `kv!("k", v)`；`KCSTRING`/`KTIME`/`KPHEX`/`KS` 用同名小写宏 | 值都走 `ObToString` |
| `LOG_DBA_*`、`NG_TRACE`/`REC_TRACE`/`FLT_*`、`REACH_TIME_INTERVAL(t)` | 同名小写宏。`reach_time_interval!(t)` 在每个调用点放一个 `static AtomicI64` | 与并发备忘 O1 一致 |
| `OB_UNIS_VERSION(v)` + `OB_SERIALIZE_MEMBER(C, a_, b_)` | `ob_serialize_member!(C, v; a_, b_);`，生成 `impl ObSerialize`。成员顺序按宏里列出的顺序，不按字段声明顺序 | 669 / 914 处 |
| `OB_SERIALIZE_MEMBER_INHERIT`/`_SIMPLE`/`_TEMP`/`_IF` | 同名小写宏，变体一一对应 | |
| `OB_DEF_SERIALIZE/DESERIALIZE/SERIALIZE_SIZE(C)` 的函数体 | `impl ObUnisBody for C`（`serialize_`/`deserialize_`/`get_serialize_size_`），unis 头由 `ob_unis!(C, v)` 统一加上 | |
| `NEED_SERIALIZE_AND_DESERIALIZE` + `DEFINE_SERIALIZE…` | 手写 `impl ObSerialize for C`，不带 unis 头 | palf 的 `LogEntry` 属于这一类 |
| 签名 `serialize(char *buf, int64_t len, int64_t &pos) const` / `deserialize(const char *, int64_t, int64_t &)` / `get_serialize_size()` | `fn serialize(&self, buf: &mut [u8], pos: &mut i64) -> ObResult` / `fn deserialize(&mut self, buf: &[u8], pos: &mut i64) -> ObResult`（就地解码）/ `fn get_serialize_size(&self) -> i64` | 就地解码才能保住"缺失的尾部成员保留默认值" |
| `OB_UNIS_ENCODE/DECODE/ADD_LEN(x)`、`LST_DO_CODE(…)` | `ob_unis_encode!(ret, buf, pos, x)` 等；decode 保留 `pos < data_len` 的判断 | `ob_unify_serialize.h:52-88` |
| `serialization::encode_vi64` 等 | oblib 里的 `serialization::encode_vi64(buf, pos, v) -> ObResult`，同名、同字节序 | `serialization.h:212-343` |
| `TO_STRING_KV` / `VIRTUAL_…` / `INHERIT_…` / `DECLARE_TO_STRING`+`DEF_TO_STRING` / 手写的 `to_string` | `impl ObToString`，写成 `to_string_kv!(Self; "a" => self.a_, …)`；继承版先打印父类；手写的逐行翻译成 `fn to_string(&self, buf: &mut ObBuf) -> i64` | 键就是 C++ 宏参数字符串化的结果，格式见 2.4 节（Q12） |
| `ObCStringHelper::convert(x)`、`J_KV`、`BUF_PRINTF`、`databuff_printf`、`databuff_print_kv` | `helper.convert(&x) -> &[u8]`、`j_kv!`、`buf_printf!(buf, pos, "C 格式", …)`、`databuff_printf!(…)`、`databuff_print_kv!(…)`；返回值和 `pos` 的语义照旧，溢出时返回 `OB_SIZE_OVERFLOW` | |
| `ObSEArray<T, N>` / `ObArray<T>` / `ObFixedArray<T>` | 存储按内存主题：`SmallVec<[T; N]>` / `Vec<T>` / `ArenaVec<'q, T>`。<br>一律通过 oblib `ObIArray<T>` trait 的同名方法访问：`push_back(v) -> ObResult`（内部用 `try_reserve`）、`count() -> i64`、`at(i: i64)`、`reset()`、`assign(&other) -> ObResult` | `ob_array.h:859` |
| 参数 `const ObIArray<T> &` / `ObIArray<T> &` | `&[T]` / `&mut dyn ObIArray<T>` | 同一个参数要能接住三种存储（Q11） |
| `arr.at(i)` / `arr[i]` / `arr.at(i, obj)` | `arr.at(i)`，越界 abort，与 `OB_ASSERT` 一致 / 同前 / `arr.at_into(i, &mut obj) -> ObResult`，越界返回 `OB_ARRAY_OUT_OF_RANGE` | `operator[]` 原本只记日志（Q7） |
| `ObHashMap<K, V>` / `ObHashSet<K>` | 在 oblib 里逐行移植：桶数、`%` 取桶、头插、可失败的 `ObHashFunc` 都保持不变；方法名 `create`/`set_refactored`/`get_refactored`/`erase_refactored`/`foreach_refactored` 不改 | 迭代顺序（Q5） |
| `ObLinearHashMap` / `ObLinkHashMap` | 按并发主题，用 `DashMap<K, Arc<V>>` | |
| SQL 引擎自带的哈希表（`ObExtendHashTable` 等） | 逐行移植，桶数、增长策略、哈希函数都不变 | 决定输出行序 |
| `ObList<T>` / `ObSortedVector<T>` / `ObVector<T>` / 位集 | oblib 里的同名类型，底层分别是 `VecDeque`/`Vec`/`[u64]`；`lower_bound` 等语义照旧 | |
| `ObDList<T>` + `ObDLinkBase` | 按内存和 IR 主题定（arena 里用 id 串链表，或者 oblib 的侵入式链表）；`ObExprOperator` 的这个基类直接去掉 | 739 / 573 个子类 |
| `std::map` / `std::set` / `std::vector` / `std::string` | `BTreeMap` / `BTreeSet` / `Vec` / `Vec<u8>` | |
| `std::sort`、`lib::ob_sort` / `std::stable_sort` | `ob_sort(slice, cmp)`，逐行移植基准平台的 introsort / `slice.sort_by(cmp)` | Q6 |
| `ObString`（视图） | 按内存主题：查询作用域内用 `ObStr<'q> = &'q [u8]`，拥有型用 `Box<[u8]>`。`ObString` 的方法（`length()`、`ptr()`、`case_compare()`、`prefix_match()` 等）做成 `[u8]` 上的扩展 trait `ObStringExt`，调用处不用改名 | `ob_string.h:794-796` |
| `ObString` 当写缓冲用（`assign_buffer`/`write`） | oblib 的 `ObStringWriter<'a>`（`&'a mut [u8]` 加长度） | 244 处 |
| `const char *` C 字符串 | 内部用 `&[u8]`，不假定是 UTF-8，也不带结尾的 NUL；FFI 边界用 `&CStr`；`&str` 只用于字面量 | |
| `char buf[N]` + `snprintf`/`databuff_printf` | `[u8; N]` + `databuff_printf!` | |
| `ObSqlString` / `ObStringBuffer` | oblib 里的同名类型，追加操作可失败；`append_fmt!` 走 `ob_printf!` | |
| `strcmp`/`memcmp` / `strcasecmp` | 切片比较，顺序与 memcmp 相同 / `ob_strcasecmp`，按 C locale 只折叠 ASCII | |
| 排序规则相关的比较、哈希、大小写转换 | 一律调用移植后的 `ObCharset::*`；不得用 `str::cmp`、`to_lowercase`、`eq_ignore_ascii_case` 代替排序规则 | |
| `isspace`/`isalpha` 等、`strtol`/`strtod`/`atoi` | oblib 的 `ob_ctype::isspace` 等，按 C locale 实现（`isspace` 包含 `\v`）；`ob_strtol`/`ob_strtod`，要跳过前导空白、允许只解析一部分、溢出时夹到边界值 | Rust 的 `is_ascii_whitespace` 不含 `\v` |
| 整数类型 | `int64_t`→`i64` 等一一对应；作字节数据的 `char` → `u8`（C 的 `char` 在 x86 上有符号，在 aarch64 Linux 上无符号）；`long` → `i64`（Windows 上原本是 32 位，共 265 处，标 `TODO(port)`）；`size_t` → `usize`；溢出检查见 Q8 | |
| `ObObj`、`ObDatum` 以及其他落盘或依赖 `offsetof` 的结构 | 加 `#[repr(C)]`，字段顺序和类型都不变；只含标量的联合体改成原始位（`u64`）加访问方法（如 `f64::from_bits`）；含指针的联合体按缺口清单处理 | `ob_object.h:1257`、`:1978` |
| 位域 | 用 oblib 的 `ob_bitfield!` 宏，显式写出每个字段的偏移（按 Clang/GCC 小端的规则从低位起依次分配）；每个结构配一条和 C++ 对照的布局测试 | `ob_time_def.h:40-46` |
| C++ 枚举 | `#[repr(transparent)] pub struct T(pub i32)` 加同名关联常量；`match` 用常量模式，再加一个 `_` 分支 | 反序列化不校验取值（`serialization.h:1649-1670`）；int→枚举转换至少 781 处（Q10） |
| `ObNumber`/`ObDecimalInt`/`ObTimeConverter`/`ob_dtoa`/哈希/crc64 | 逐行移植，配上以 C++ 为准的金标准测试 | 2.7 节 |
| 带虚函数的类 | 一个 trait（方法一一对应）加一个数据 struct：<br>• 基类数据放在第一个字段 `base`，trait 提供 `base()`/`base_mut()`<br>• 基类的默认实现 → trait 的默认方法<br>• 派生类里调用 `Base::f()` → 自由函数 `base_f(self)` | 3,920 个派生类，深度不超过 6 |
| 多重继承（接口混入） | 拆成多个 trait；虚继承（2 处）逐处处理 | 120 个 |
| 向下转型 `static_cast<D*>(b)` | 对有类型标签的类层次：先查标签再转（`downcast_ref::<D>()`，标签不符就 abort）；能整体改成 enum 的由 IR 主题定 | 5,813 处（上限） |
| CRTP | 泛型 + trait（以 `Self` 作类型参数） | 109 / 186 处 |
| 模板 | 泛型加显式的 trait 约束；非类型参数 → const 泛型；特化 → 每个类型一个 trait impl；SFINAE、`if constexpr` → trait 分派 | 7,161 处 |
| 运算符重载 | • 比较 → `PartialEq`/`PartialOrd`；语义不同时保留 `compare()`<br>• 深拷贝的 `operator=` → `assign(&mut self, &T) -> ObResult`；浅拷贝 → `#[derive(Clone, Copy)]`<br>• `operator()` → 方法 `call()`；`operator[]` → `at()`<br>• 算术 → `std::ops` | 约 2,050 处 |
| `DISALLOW_COPY_AND_ASSIGN` | 不 derive `Clone`/`Copy` | 2,319 处 |
| 函数重载 | 按 §4 的确定性改名规则；只在一个参数的类型上分派的重载集（如 `ObRawExprPrinter::print`）改成一个 trait，每个类型一个 impl | 3,178 个名字 |
| 默认参数 | 调用方把所有参数显式写全，默认值取自 C++ 的声明 | 约 1.1 万处 |
| 命名空间作用域里带构造函数的全局对象 | 能用 `const` 初始化的写成 `static`；否则写 `static X: LazyLock<T>`；构造时有副作用（比如注册）的，改成启动时显式调用（按分层主题的组合根）；`__attribute__((constructor))` 改成在启动函数里显式调用 | 268 / 365 个目标文件 |
| 函数内的 `static ObX x` / `thread_local`、`RLOCAL` | `static X: LazyLock<…>` 或 `OnceLock` / `thread_local! { static X: Cell<…> = const { … } }` | 258 / 233 处 |
| 定义类型或函数的宏、X-macro 表 | 每个宏对应一个同名 `macro_rules!`，名字按 §4 改成小写 | 80 + 128 个宏 |
| `goto` | 带标签的块（`'l: { … break 'l; }`）或循环状态机，逐处翻译 | 93 处，5 个文件 |
| 非空的 `switch` 贯穿 | 重复代码，或改写成依次递减的 `if` 级联，并注明原来是贯穿；空 case 合并成 `A \| B =>` | 约 144 处 |
| 内联汇编 | pause/yield → `std::hint::spin_loop`；rdtsc、cpuid、crc32 → `core::arch` 的 intrinsic；`ob_smart_call.cpp` 的换栈汇编由 `stacker` 取代；其余的 `asm!` 加 `SAFETY:` 注释 | 39 处 |
| SIMD intrinsic | `std::arch` 里的同名函数 + `#[target_feature(enable = …)]` + 运行时检测；`ob_sse_to_neon.h` 拆成按 `cfg` 区分的两份实现 | 628 + 333 处 |
| `SMART_CALL(f(…))` / `SMART_VAR` | `smart_call!(f(…))`（oblib 实现，基于 stacker）/ 改成堆上分配 | 并发备忘 |
| `DEFER(…)`、`ARRAY_FOREACH`、`(void)f()` | 局部守卫 `defer!`，在 Drop 里执行 / `for` 循环加 `ret.is_ok()` 条件 / `let _ = f();` | |

## 5. 需要人决定的问题（★ = 推荐）

**Q1. 错误流的函数体怎么写？**
- (A) ★ 逐行保留 if/else 链，类型用 `ObResult`，翻译阶段禁用 `?`。行为对齐通过后，再按 `retflow.py`/`tail.py` 给每个函数打的标签，把约 73% 的直通函数机械地改成 `?`。
- (B) 按预先算好的逐函数标签混用：直通函数用 `?`，其余约 27% 用链式写法。
- (C) 一律用 `?`，失败后消费 `ret` 的地方逐处手改。

理由：(A) 只有一种写法，没有控制流风险，审查者可以逐行对照；(B) 成品更地道，但规则一分为二，一旦误用 `?`，就会悄悄跳过清理代码；(C) 风险最大。

**Q2. `ObError` 用什么形态？** 内存、并发两份备忘的草图里写的是 `ObError::AllocateMemoryFailed`、`ObError::ExceedQueryMemLimit { limit, hold }` 这样的枚举。
- (A) ★ `#[repr(transparent)] ObError(NonZeroI32)`，这样 `Result<(), ObError>` 只有 4 字节；常量沿用 C++ 的名字 `OB_X`；带参数的文本只走 `log_user_error!`。
- (B) 用 Rust 枚举：1,546 个变体，其中一部分带数据。

理由：错误码要做整数比较，错误码本身会发给客户端，文本走线程局部的旁路（2.1 节）；而每个函数都返回 `Result`，带数据的枚举会把它撑到 16–24 字节。

**Q3. 格式串怎么处理？**
- (A) ★ 保留 C 格式串，由 `ob_printf!` 在编译期解析、检查类型，并逐字节复刻输出。
- (B) 翻译时改写成 Rust 的 `format!`。
- (C) 运行时模拟 printf，不做编译期检查。

**Q4. 日志怎么做？**
- (A) ★ 自有门面，日志行的格式和 C++ 一致，现有的运维脚本和 obdiag 可以继续用。
- (B) 自有门面，只保留参数语义，日志行格式不作要求。
- (C) 用 `tracing` 加自定义 subscriber，自己实现模块表和线程级级别。

**Q5. 通用哈希容器怎么办？**
- (A) ★ `ObHashMap`/`ObHashSet` 在 oblib 里逐行移植；翻译代码禁用 std `HashMap` 和 hashbrown。IR 主题把以地址为键的表改成以 id 为键（`FxHashMap`/`IndexMap`），这属于重新设计；只要其迭代顺序不进入结果，就作为例外写进 RULEBOOK。
- (B) 用 hashbrown 加固定的哈希器。这样顺序是确定的，但和 C++ 不同，依赖行序的用例要重录或放宽。

**Q6. `std::sort` 里并列值的顺序以哪个平台为准？**
- (A) ★ 以录制裁判期望输出的平台为准，现在是 macOS（libc++），逐行移植那份 introsort。前提是在 Linux 上复跑裁判（DEV-005）时，没有因此出现差异。
- (B) 以 Linux 生产构建（libstdc++）为准，裁判改在 Linux 上重录。
- (C) 不复刻，改为让裁判对并列行不敏感：修改用例，加 `--sorted_result` 或补全 ORDER BY。

**Q7. `operator[]` 越界怎么处理？** C++ 记一条日志后继续执行，属于未定义行为。
- (A) ★ 和 `at()` 一样直接 abort。
- (B) 记日志后返回默认值，继续执行。

**Q8. 整数溢出检查怎么设？**
- (A) ★ 所有参与对齐的 profile（release、cmake-debug）都设 `overflow-checks = false`；C++ 显式检查溢出的地方，用 `checked_*`/`overflowing_*`。
- (B) debug 构建打开溢出检查。但 C++ 里无符号数回绕是有定义的行为，Rust debug 构建遇到回绕会 panic，进而 abort。

**Q9. 落盘格式和序列化格式要不要兼容？**
- (A) ★ 和 C++ 逐字节兼容，Rust 版能直接打开 C++ 写的数据目录。与 storage-txn 备忘一致。
- (B) 只要求自身前后一致，只支持新建的数据目录。

**Q10. 枚举怎么译？**
- (A) ★ 全部译成新类型加关联常量。
- (B) 不落盘、不做算术的 `enum class` 译成 Rust `enum`，其余译成新类型。这需要逐个枚举分类。

**Q11. `ObIArray<T> &` 参数怎么译？**
- (A) ★ 只读的译成 `&[T]`，可写的译成 `&mut dyn ObIArray<T>`。
- (B) 可写的也用具体类型 `&mut Vec<T>`，即内存备忘的写法。这样实参为 `ObSEArray` 的地方也得改成 `Vec`，等于放弃内联容量。
- (C) 用泛型 `&mut impl ObIArray<T>`：会单态化，编译更慢。

**Q12. `TO_STRING` 的输出要复刻到什么程度？**
- (A) ★ 所有类型都逐字节复刻 C++ 的格式。`cmp_meta()` 已经把它暴露给用户，而且统一复刻的成本很低。
- (B) 只复刻能到达用户出口的类型，其余用 `Debug`。

**Q13. 新依赖怎么定？** 并发、内存主题已经推荐了 `parking_lot`、`stacker`、`smallvec`、`bumpalo`、`crossbeam-*`、`dashmap`、`arc-swap`；本主题再加一个 proc-macro crate。
- (A) ★ 全部接受，除此之外的 crate 一律禁止。
- (B) 只用 std：带超时的锁、扩栈、内联数组都要自己写。

**Q14. 用哪个 edition？**
- (A) ★ 2021，加上 `#![deny(unsafe_op_in_unsafe_fn)]`，与 rust/ 现有 crate 一致。
- (B) 2024。

**Q15. 移植 crate 怎么跑 clippy？**
- (A) ★ 照样 `clippy -D warnings`，但统一放开链式写法必然触发的几条：空分支、`needless_return`、`non_upper_case_globals`、`too_many_arguments`。这份清单写进 RULEBOOK。
- (B) 行为对齐之前，移植 crate 不跑 clippy。

## 6. 对其他主题的依赖或影响

- **内存主题**：
  - `ObString`、容器的存储类型、`ObDList`、OOM 的映射都以它为准。
  - 本备忘建议把参数类型改成 `&[T]`/`&mut dyn ObIArray<T>`（Q11），原因是内存备忘写的 `&mut Vec<T>` 接不住 `SmallVec` 实参。
  - 它的 `ObError::…` 枚举写法与 Q2 冲突，设计文档只能留一种。
- **IR 主题**：
  - 573 个表达式算子用 trait object 注册表；顺带去掉 `ObDLinkBase` 基类。
  - 向下转型和 enum 化要一起定。
  - 它的 R4（`FxHashMap`/`IndexMap`）在 Q5 里作为例外处理。
- **并发主题**：锁、原子操作（一律 SeqCst，本备忘不再另立规则）、`DashMap`、`smart_call`、`REACH_TIME_INTERVAL` 都以并发主题为准。
- **分层与全局状态主题**：
  - 日志器单例 `OB_LOGGER`、线程局部的警告缓冲区指针（`ob_warning_buffer.h:241-259`）、线程级日志级别表，要放进它定的槽位。
  - 带动态初始化的全局对象（365 个目标文件里有 268 个含有这类对象，多是 unity 组），要按它的组合根改成显式的启动注册。
- **生成器与第三方主题**：
  - errno 生成器产出本备忘的 `ObError` 常量和带类型的消息函数。
  - bitpacking SIMD 生成器。
  - xxHash、fast_float 的替换，需要金标准测试证明等价；`ob_strtod` 的语法要包一层。
  - Boost.Geometry、vsag 等 C++ 库抛出的异常，在 shim 里接住。
- **命名主题（§4）**：
  - 保留 C++ 的类型名（含 `Ob` 前缀）和方法名；宏改成小写；常量沿用 `OB_X`；还要定重载的改名规则。
  - 需要放开 `non_camel_case_types`/`non_upper_case_globals` 这两条 lint。
  - 链式写法在 lint 上的影响见 Q15。
- **存储主题**：序列化兼容（Q9）、位域和 `#[repr(C)]` 的布局测试、crc64。
- **裁判**：
  - 并列值排序（Q6）、哈希行序（灰色地带第 11 条）、`cmp_meta`（第 9 条）都依赖本备忘的决定。
  - `enable_rich_error_msg` 必须保持关闭。
  - 深层嵌套 SQL 在多深时触发 `OB_SIZE_OVERFLOW`，会随 Rust 栈帧大小变化（估）。如果裁判里有这类用例，要放宽或记为偏差。
- **缺口清单**：
  - `OB_ISNULL` 的可空性；
  - `T *&` 输出参数；
  - "返回 int 的函数是否返回错误码"：建议由脚本预先标好，做法同本备忘的 `retflow` 标签；
  - 第 4 节的错误流规则依赖这三项。
