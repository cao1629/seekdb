# 06 `string-num-sort-serial`：字符串、数值、ctype、排序、序列化、POD、槽位与 prelude

> - **依据**：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）的 0.10、0.11（第 3、5、6、7 项）、§2.5 的字符串和数值行、§2.11 的序列化行、§2.12 的整型/浮点/除法/数学库/位域行、§3 白名单第 7 项、§4 的 `Slot` 与 prelude；`migration/infra/README.md` 的结构要求。基线 `7d907abfa`（`git diff --stat 7d907abfa HEAD -- src rust deps unittest` 为空，工作区的 `src/` 就是基线）。
> - **状态**：API 草案，2026-10-11，待两名审查者审查后冻结。
> - **计数口径**：未注明的计数都是 `rg -o` 在 `src/**/*.{h,hpp,ipp,inl,cpp,cc}` 上的原始匹配数，含注释，排除生成的解析器 `*_tab.c`、`*_lex.c`。同名方法会把别的类型一起算进来，所以都写"约"。
> - **libc++ 的位置**：基线的编译参数是 `-isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX15.sdk`（`build_release/compile_commands.json`），它是指向 `MacOSX15.4.sdk` 的符号链接，`xcrun --sdk macosx15.4 --show-sdk-path` 给出同一路径。下文 `$LIBCXX` 指 `/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk/usr/include/c++/v1`，`$LIBCXX/__config:30` 是 `_LIBCPP_VERSION 190102`。`xcrun --show-sdk-path` 的默认值是 26.5 SDK（libc++ 21），不能用。
> - **已对齐的兄弟文档**（草稿）：01 `mem`（`Arena::try_str`/`try_str_c`/`alloc`、`try_bytes`、`ObStr`）、02 `sync`（prelude 导出建议）、04 `error-log-fmt`（`ObFmtArgs`、`fmt::vsnprintf`、`append_fmt!`/`assign_fmt!` 两个宏归 04、`ObToString` 的切片钩子）。

## 1. 范围

### 1.1 处置一览

**字符串**

| C++ 单元（代表文件） | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/oblib/lib/string/ob_string.h`、`.cpp` | `ObString`（约 17,398 处，`ob_string.h:41-797`）；6 个带分配器的模板函数（`:805-974`）；序列化（`.cpp:25-76`） | `replace:string` | 只读视图 → `&[u8]`/`ObStr<'q>`/`Box<[u8]>` 加扩展 trait `ObStringExt`；写缓冲 → `ObStringWriter<'a>`；`ob_write_string` → 01 的 `try_str`/`try_str_c`/`try_bytes`（RULEBOOK §2.4，01 §2.12）；其余 4 个模板函数在 `oblib::string`；带 padding 的 `ob_write_string` 重载（`:827-852`）没有调用者（按实参个数扫描，0 处），不提供 |
| `ob_sql_string.h`、`.cpp` | `ObSqlString`（约 1,374 处） | `replace:string` | |
| `ob_string_buffer.h`、`.cpp` | `ObStringBuffer`（约 255 处），别名 `ObJsonBuffer`（约 198 处，`src/oblib/common/json_type/ob_json_common.h:25`） | `replace:string` | 别名留在 json 单元：`pub type ObJsonBuffer<'q> = ObStringBuffer<'q>;` |
| `src/oblib/lib/utility/alloc_assist.h`、`.cpp` | `MEMCPY`/`MEMCMP`/`STRCMP`/`STRNCASECMP`/… 宏（`:46-119`），Windows 的空指针保护（`:47-72`），常量 `ACHUNK_PRESERVE_SIZE`（`:120`） | `replace:string` | 宏不译，调用点按 §2.1.6 的对照表改写；桩文件定义 `ACHUNK_PRESERVE_SIZE`（01 文档第 74 行约定由 06 给出） |
| `ob_sensitive_string.h` | `ObSensitiveString` | `translate`，**1d 期间由 06 提前翻译** | 04 的 `ks!` 依赖它（04 §1.2） |
| `ob_fixed_length_string.h`、`ob_string_holder.h`、`ob_strings.h/.cpp` | 建在 `ObString` 上的普通类 | `translate` | 用本模块的 API |

**数值**

| C++ 单元 | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/oblib/lib/charset/ob_dtoa.h`、`.cc`（2,440 行，56 个 goto） | `ob_strtod`（`.cc:386-405`）= fast_float 快路径 `ob_fast_strtod`（`:343-384`）+ MySQL dtoa `ob_strtod_int`（`:1121`）；`ob_atof`（`:407-412`）；`ob_gcvt`/`ob_fcvt` | `translate` | 0.11.5 逐行移植加金标准测试；依赖本模块的 `fast_float_from_chars` 和 `ctype` |
| `src/oblib/lib/charset/ob_ctype_simple.cc` | MySQL 的整数解析 `ob_strntol_8bit`…`ob_strntoull10rnd_8bit`（`:60-420`）；C ABI 包装 `ob_strntoll`/`ob_strntoull`（`:754-762`） | `translate`（字符集单元） | 后两个被保留的 C 解析器调用（`src/sql/parser/sql_parser_base.h:69-70`、`src/pl/parser/pl_parser_base.h:35-36`），它们的 `extern "C"` 蹦床归 09 |
| `src/oblib/lib/utility/utility.cpp:2082-2132` | `ob_strtoll`/`ob_strtoull`/`ob_atoll`：包 C 库加 `errno` | 所在单元 `translate` | 用 `c_strtoll`/`c_strtoull` 加 `errno()`/`set_errno()` |
| `src/oblib/lib/utility/ob_fast_convert.h` | `ObFastFormatInt`、`ObFastAtoi` | `translate`，**1d 期间由 06 提前翻译** | 04 的 `logdata_print_obj(int64/uint64)` 依赖它（04 §1.2） |
| fast_float 6.1.3（`deps/3rd/usr/local/oceanbase/deps/devel/include/fast_float/`，包 `deps/3rd/pkg/devdeps-fast-float-6.1.3-20251205.tar.gz`） | 头文件库；调用 4 处（`ob_dtoa.cc:370`、`src/share/object/ob_array_cast.cpp:705,773,989`） | 第三方，按 0.14 换掉 | `oblib::num::fast_float_from_chars` |
| C 库数值解析（约 76 处：`strtoll` 21、`strtoull` 11、`atoll` 11、`atoi` 11、`strtol` 8、`strtod` 6、`atol` 3、`atof` 3、`strtoul` 2；其中约 5 处在保留的 C 解析器里，不译） | | 调用点 `translate` | `oblib::num::c_*` |
| C 的 `errno`（约 440 处） | | 调用点 `translate` | `oblib::num::errno()`/`set_errno()`（§8 Q4） |
| 以浮点数为键的 `std::map`（`src/sql/engine/expr/ob_expr_map.cpp:235-252`、`src/oblib/common/udt/ob_map_type.cpp:247-266`，各实例化 float、double 两次） | | 调用点 `translate` | `CppFloatMap`（推荐）或 `BTreeMap<CppFloatKey>`（§8 Q2） |
| C 数学库（约 480 处：`fabs` 69、`ceil` 59、`log` 50、`pow` 42、`sqrt` 40、`floor` 36、`round` 26、`rint` 15、`remainder` 8、`log2` 8、三角函数约 50、`lround` 6、`fmod` 2、`lrint` 1…） | | 调用点 `translate` | §3.3 S-NUM-6 的映射表 |
| 浮点转窄整型、没有守卫的整数除法和取余 | | 调用点 `translate` | `c_fptosi_*`、`c_div`/`c_rem` |

**ctype**

| C++ | 内容 | 处置 | 说明 |
|---|---|---|---|
| libc `<ctype.h>`（约 183 处、52 个文件：`isspace` 85、`isdigit` 39、`isxdigit` 15、`tolower` 14、`isprint` 11、`isalpha` 8、`isupper`/`isalnum` 各 3、`ispunct`/`islower` 各 2、`toupper` 1；`tolower`/`toupper` 里约 4 处是 `ObCharset` 的同名静态方法） | 最多的是 `ob_time_convert.cpp`（27）、`utility.cpp`（18）、`ob_path_parser.cpp`（13）、`ob_geo_expr_utils.cpp`（8）、`ob_number_v2.cpp`（7） | 调用点 `translate` | `oblib::ctype` |
| `src/oblib/lib/charset/ob_ctype.h` 的 `ob_isspace(cs, c)`、`ob_toupper(s, c)`（`:138-139`）等 | 按字符集表分类 | 不在本模块 | 字符集单元，`translate` |

**排序与堆**

| C++ | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/oblib/lib/utility/ob_sort.h` | `lib::ob_sort`（约 172 处，`:27-55`） | `replace:sort` | |
| libc++ 19.1.2 的 `std::sort`（4）、`std::push_heap`（4）、`std::pop_heap`（5）、`std::make_heap`（1）、`std::lower_bound`（64）、`std::upper_bound`（17）、`std::binary_search`（2） | 第三方头文件 | `replace:sort` | 逐行移植 `$LIBCXX/__algorithm/` 下的 `sort.h`、`partial_sort.h`、`sift_down.h`、`push_heap.h`、`pop_heap.h`、`make_heap.h`、`sort_heap.h`、`lower_bound.h`、`upper_bound.h`、`binary_search.h` |
| `std::stable_sort`（4：`ob_json_tree.cpp:743`、`ob_binary_aggregate.cpp:384`、`ob_tree_base.cpp:634,642`） | | 调用点 `translate` | `slice::sort_by`（RULEBOOK §2.5） |
| `qsort`（2：`ob_ctype_simple.cc:1131`、`ob_log.cpp:1361`） | | 调用点 `translate` | `ob_sort`（§8 Q6） |
| `src/oblib/lib/container/ob_heap.h` 的 `ObBinaryHeap` | 自写的二叉堆 | 不在本模块 | RULEBOOK §2.5"按原代码移植"，05 或 `translate` |

**序列化**

| C++ 单元 | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/oblib/lib/utility/serialization.h`（2,020 行） | 定长大端、变长整数、vstr、`number_type`、按类型分派的 `encode`/`decode`/`encoded_length`（`:1645-2014`） | `replace:serialization` | 旧 ObObj 编码一族（`encode_int`/`fast_encode`/`encode_str`/`encode_*_type`/`safe_int64_abs` 等，`:615-1643`，含 12 个 goto）在外部只有 `encode_number_type`/`decode_number_type`/`encode_length_number_type` 有调用（`ob_obj_funcs.h:852,863`、`ob_number_v2.cpp:65,71`），其余不移植，由 1c 第 16 类确认没有调用者 |
| `ob_unify_serialize.h`（468 行） | unis 头与成员宏 | `replace:serialization` | |
| `ob_serialization_helper.h`、`.cpp` | `encode_item`/`decode_item`/`encoded_length_item`（各 8 处） | `replace:serialization` | `char[SIZE]` 的 `decode_item`（`:189-201`）把 `const char*` 赋给 `int`，从未实例化，不提供 |
| `src/oblib/lib/ob_define.h:1717-1744` | `NEED_SERIALIZE_AND_DESERIALIZE` 一族、`DEFINE_SERIALIZE` 一族 | 宏随所在单元 | 宏本身按 §2.11 改成手写 `impl`；`ob_define.h` 的归属见 §8 Q8 |
| `src/oblib/lib/utility/ob_macro_utils.h:398-408` | `LST_DO`/`LST_DO_CODE` | 所在单元归 04 | `lst_do_code!` 和变长的 `ob_unis_*!` 由本模块提供（04 §2.2.6 约定） |
| `src/oblib/lib/container/ob_array_serialization.h` 等容器的序列化 | | 05 | 用本模块的 trait |

**POD、位域、packed、柔性数组**（不是单元，是散在各单元里的构造，缺口清单第 9 类）

| 构造 | 规模 | 代表 | 处置 |
|---|---|---|---|
| 位域成员 | 约 1,032 个、127 个文件 | `ob_micro_block_header.h:38-46`、`ob_time_def.h:41-48,72-81`、`ob_datum.h:141-145`、`ob_define.h:1786` 的 `ObNumberDesc` | 所在单元用 `ob_bitfield!` |
| packed | 约 57 行 `__attribute__((packed))` 加 14 行 `#pragma pack`（33 个文件，不含 zstd/xxhash） | `ob_micro_block_header.h:120`、`ob_chunk_datum_store.h:165` | 落盘的用 `#[derive(ObPod)]`；只在内存里的见 S-POD-7 |
| 柔性数组 | 约 68 个 `T name[0];` | `ob_chunk_datum_store.h:165,522`、`ob_mvcc_row.h:99` | 视图 `<名字>View<'a>`/`<名字>Mut<'a>`（0.9）；`storage_core` 例外 |
| 按内存布局落盘的头 | 约 25 个 | `ob_micro_block_header.cpp:230-298`、`ob_macro_block_common_header.cpp:90-106` | `#[derive(ObPod)]`（0.10） |

**哈希与校验：逐行翻译，不替换**（RULEBOOK §1"哈希、校验和"行，§2.12）

| C++ 单元 | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/oblib/lib/hash_func/murmur_hash.h`、`.cpp`（146 行） | `murmurhash64A`、`murmurhash2`、`fnv_hash2` | `translate`，**1d 期间由 06 提前翻译** | `ObStringExt::hash` 和 05 的哈希容器依赖它 |
| `wyhash.h` | | `translate` | |
| `ob_hash_func.h` | `do_hash` 分派（`:28-62`） | `translate` | 默认分支按对象内存字节做 murmurhash（`:31`），05 决定哈希容器怎么接 |
| `xxhash.h`、`xxhash.c` | 第三方 xxHash | 换成 `xxhash-rust`（0.14、§1） | 金标准测试证明等价 |
| `src/oblib/lib/checksum/ob_crc64.h`、`.cpp` | crc64：软件查表加硬件 CRC32C 路径 | `translate` | 内联汇编和 `__crc32cd` 路径（`.cpp:341-367`）按 §2.13 函数级重映射到 `rust/port/oblib/src/simd/`；`__attribute__((constructor))`（`:51`）改成组合根里的显式调用（0.4） |
| `ob_crc16.h`、`ob_parity_check.h/.cpp` | | `translate` | |

**槽位与 prelude**

| C++ | 内容 | 处置 | 说明 |
|---|---|---|---|
| `src/share/rc/ob_server_runtime.h:117-149` | `ObServerServiceSlot<T>`、`bind_server_service`/`server_service`/`unbind_server_service` | share 单元 `translate` | 槽位静态量由脚本按 `slots.tsv` 生成，类型是本模块的 `Slot<T>`；`unbind_*` 不译（0.5） |
| GCTX 的指针成员（0.5） | | | `Slot<T>` |
| （新建）`oblib::prelude` | | | §2.8 |

### 1.2 桩文件（1d 写，RULEBOOK §4）

`rust/port/oblib/src/lib_m/string/ob_string.rs`、`ob_sql_string.rs`、`ob_string_buffer.rs`、`rust/port/oblib/src/lib_m/utility/alloc_assist.rs`、`serialization.rs`、`ob_unify_serialize.rs`、`ob_serialization_helper.rs`、`ob_sort.rs`：文件头 `//! Disposition: replace:<模块>`，正文只有 `pub use crate::<模块>::…;`（`alloc_assist.rs` 另加 `pub const ACHUNK_PRESERVE_SIZE: u32 = 17 << 10;`），文件尾是 §6 的状态行。路径按 §4 的规则把 `lib` 改成 `lib_m`；临时 manifest 里还写作 `src/lib/…`（第 52、64、74、75、146、151、196、199 行），以 1c 的 `units.tsv` 为准。

## 2. Rust API

约定：模块都在 `rust/port/oblib/src/` 下；`ObError`、`ObResult<T = ()>`、`OB_*` 常量来自 04 的 `oblib::error`，`Arena`、`ObStr<'q>`（= `&'q [u8]`）来自 01 的 `oblib::mem`。函数体省略：`impl` 块里只列签名。每项注明对应的 C++ 名字与位置。

### 2.1 `oblib::string`

#### 2.1.1 只读视图：`ObStringExt`

```rust
use core::ffi::{c_int, CStr};
use crate::error::ObResult;
use crate::mem::{Arena, ObStr};

/// C++ ObString 的只读方法（ob_string.h:41-797）。
/// 实现在 `&'a [u8]` 上：`&[u8]`、`ObStr<'q>`、`Box<[u8]>`、`Vec<u8>`、`ObStringWriter` 经自动解引用都能调用，
/// 返回子切片的方法能带上 `'a`。NULL 与空串的处理见 S-STR-2。
pub trait ObStringExt<'a>: Copy {
    /// length()（:285）。obstr_size_t = int32_t（:44）。
    fn length(self) -> i32;
    /// ptr()（:287）：返回切片本身。`MEMCPY(d, s.ptr(), s.length())` → `d[..n].copy_from_slice(s.ptr())`。
    fn ptr(self) -> &'a [u8];
    /// empty()（:105-108）
    fn empty(self) -> bool;
    /// hash(seed)（:295-302）；hash() 写成 hash(0)；int hash(uint64_t&, seed)（:304-308）由 05 的哈希接口包装。
    fn hash(self, seed: u64) -> u64;
    /// case_compare_equal(ObString / const char*)（:310-337）
    fn case_compare_equal(self, other: &[u8]) -> bool;
    /// case_compare(ObString / const char*)（:339-366）
    fn case_compare(self, other: &[u8]) -> c_int;
    /// compare_equal（:368-392）
    fn compare_equal(self, other: &[u8]) -> bool;
    /// compare（:394-416）；operator< 等比较运算符（:508-566）直接用切片比较，结果相同（S-STR-4）
    fn compare(self, other: &[u8]) -> c_int;
    /// prefix_match（:418-429、:463-472）
    fn prefix_match(self, other: &[u8]) -> bool;
    /// prefix_match_ci（:474-496）
    fn prefix_match_ci(self, other: &[u8]) -> bool;
    /// suffix_match（:431-441）
    fn suffix_match(self, other: &[u8]) -> bool;
    /// suffix_match_ci（:443-461）
    fn suffix_match_ci(self, other: &[u8]) -> bool;
    /// length_without_blank（:269-283）
    fn length_without_blank(self) -> i32;
    /// trim（:568-583）、trim_space_only（:585-600）、trim_end_space_only（:602-614）
    fn trim(self) -> &'a [u8];
    fn trim_space_only(self) -> &'a [u8];
    fn trim_end_space_only(self) -> &'a [u8];
    /// find(char)（:690，memchr）、reverse_find(char)（:695，memrchr）：返回偏移
    fn find(self, c: u8) -> Option<usize>;
    fn reverse_find(self, c: u8) -> Option<usize>;
    /// after(const char*)（:742-745）、after(char)（:751）
    fn after(self, off: Option<usize>) -> &'a [u8];
    fn after_char(self, c: u8) -> &'a [u8];
    /// is_numeric（:768-785）
    fn is_numeric(self) -> bool;
    /// split_on(const char*)（:713-722）、split_on(char)（:738）：改写视图本身
    fn split_on(&mut self, off: Option<usize>) -> &'a [u8];
    fn split_on_char(&mut self, c: u8) -> &'a [u8];
    /// clip(const char*)（:759-765）
    fn clip(&mut self, off: Option<usize>);
    /// operator+=(int64_t)（:660-670）；operator++（:649-656）写成 add_assign(1)，结果相同
    fn add_assign(&mut self, n: i64);
}
impl<'a> ObStringExt<'a> for &'a [u8] { /* … */ }
```

#### 2.1.2 写缓冲：`ObStringWriter<'a>`

```rust
/// C++ 把 ObString 当定长写缓冲用的那一面（assign_buffer/write/set_length，ob_string.h:134-267、:498-506）。
/// 已写部分经 Deref 当 &[u8] 用（C++ 同一个对象既写又读）。
pub struct ObStringWriter<'a> {
    buf_: &'a mut [u8],   // C++ ptr_
    buffer_size_: i32,    // C++ buffer_size_；shrink() 之后可以小于 buf_.len()
    data_length_: i32,    // C++ data_length_
}
impl<'a> ObStringWriter<'a> {
    /// ObString()（:46-49）：没有缓冲，写入一律返回 0
    pub fn new() -> Self;
    /// ObString s; s.assign_buffer(buf, size)（:240-250）；size = min(buffer.len(), i32::MAX)
    pub fn with_buffer(buffer: &'a mut [u8]) -> Self;
    pub fn assign_buffer(&mut self, buffer: &'a mut [u8]);
    /// assign(char*, len)（:188-197）与 ObString(size, length, ptr)（:65-72、:96-103）：
    /// buffer_size_ = buffer.len()，data_length_ = length（length > buffer.len() 时按 buffer.len()）
    pub fn with_length(buffer: &'a mut [u8], length: i32) -> Self;
    /// write（:134-147）：放不下时一个字节也不写，返回 0
    pub fn write(&mut self, bytes: &[u8]) -> i32;
    /// write_front（:155-171）
    pub fn write_front(&mut self, bytes: &[u8]) -> i32;
    /// set_length（:252-258）：length > size() 时不改，返回当前长度
    pub fn set_length(&mut self, length: i32) -> i32;
    /// remain（:264-267）、size（:286）、length（:285）、shrink（:498-506）、reset（:118-123）
    pub fn remain(&self) -> i32;
    pub fn size(&self) -> i32;
    pub fn length(&self) -> i32;
    pub fn shrink(&mut self) -> i32;
    pub fn reset(&mut self);
    /// 非 const 的 ptr()（:288）：整个缓冲 [0, buffer_size_)
    pub fn ptr_mut(&mut self) -> &mut [u8];
    /// 已写部分 [0, data_length_)
    pub fn string(&self) -> &[u8];
    /// 结束写入，交出 'a 的只读视图（C++ 里接着把同一个 ObString 当视图传出去）
    pub fn into_string(self) -> &'a [u8];
}
impl core::ops::Deref for ObStringWriter<'_> { type Target = [u8]; /* = string() */ }
```

#### 2.1.3 `ObSqlString`

```rust
/// C++ ObSqlString（ob_sql_string.h:33-98，.cpp）。全局堆；DISALLOW_COPY_AND_ASSIGN → 不 derive Clone。
pub struct ObSqlString {
    data_: Option<Vec<u8>>,   // None ⇔ C++ data_ == NULL；Some(v) 时 v.len() == C++ data_size_
    len_: i64,
}
impl ObSqlString {
    pub const MAX_SQL_STRING_LEN: i64 = 512;                       // h:91
    /// ObSqlString(label)（.cpp:25-28）：标签删掉（0.7）；set_attr/set_label（h:42-48）的调用点删掉
    pub fn new() -> Self;
    pub fn is_valid(&self) -> bool;                                 // .cpp:35-38
    pub fn reset(&mut self);                                        // :40-49
    pub fn reuse(&mut self);                                        // :270-276
    pub fn reserve(&mut self, size: i64) -> ObResult;               // :278-300
    pub fn extend(&mut self, size: i64) -> ObResult;                // :302-320
    /// append(const char*)、append(const char*, int64_t)、append(const ObString&)（:51-80）合成一个
    pub fn append(&mut self, s: &[u8]) -> ObResult;
    /// assign 的三个同类重载（:97-120）
    pub fn assign(&mut self, s: &[u8]) -> ObResult;
    /// assign(const ObSqlString&)（:122-130），按 §4 的重载规则改名
    pub fn assign_sql_string(&mut self, sql: &ObSqlString) -> ObResult;
    /// vappend（:183-268），实现；append_fmt（:82-95）转发给它；assign_fmt（:132-146）先 reuse 再转发。
    /// 调用处写 04 的 append_fmt!(s, "fmt", …)/assign_fmt!(s, "fmt", …)（04 §2.4.3，Ob 方言）。
    pub fn vappend(&mut self, args: &crate::fmt::ObFmtArgs<'_>) -> ObResult;
    pub fn append_fmt(&mut self, args: &crate::fmt::ObFmtArgs<'_>) -> ObResult;
    pub fn assign_fmt(&mut self, args: &crate::fmt::ObFmtArgs<'_>) -> ObResult;
    pub fn string(&self) -> &[u8];                                  // :148-151
    /// ptr()（h:63）：C++ 是以 NUL 结尾的 C 串，Rust 给 [0, len_)；未分配过时是空切片
    pub fn ptr(&self) -> &[u8];
    /// 非 const ptr()（h:79）：[0, capacity())，"危险接口"照旧，配 set_length 用
    pub fn ptr_mut(&mut self) -> &mut [u8];
    pub fn length(&self) -> i64;                                    // h:64
    pub fn capacity(&self) -> i64;                                  // h:65
    pub fn empty(&self) -> bool;                                    // h:66
    pub fn set_length(&mut self, len: i64) -> ObResult;             // :153-169
}
impl crate::fmt::ObToString for ObSqlString { /* to_string（:171-181）：委托给 [u8] 的 ObString 语义 */ }
```

#### 2.1.4 `ObStringBuffer<'q>`

```rust
/// C++ ObStringBuffer（ob_string_buffer.h:26-80，.cpp）。分配器是查询作用域的 arena：
/// get_result_string 交出去的字节必须活得和 arena 一样长。
pub struct ObStringBuffer<'q> {
    allocator_: Option<&'q Arena>,  // None ⇔ C++ allocator_ == NULL
    data_: &'q mut [u8],            // 长度 == cap_；未分配时为空切片
    len_: u64,
    cap_: u64,
}
impl<'q> ObStringBuffer<'q> {
    pub const STRING_BUFFER_INIT_STRING_LEN: u64 = 512;            // h:74
    /// ObStringBuffer(ObIAllocator*)、ObStringBuffer()（.cpp:23-29）
    pub fn new(allocator: Option<&'q Arena>) -> Self;
    pub fn set_allocator(&mut self, allocator: Option<&'q Arena>); // h:32-35
    pub fn get_allocator(&self) -> Option<&'q Arena>;               // h:36
    pub fn reset(&mut self);                                        // .cpp:36-45
    pub fn reuse(&mut self);                                        // :47-50
    /// get_result_string(ObString&)（:52-61）：交出 [0, len_)，自身清空
    pub fn get_result_string(&mut self, buffer: &mut ObStr<'q>) -> ObResult;
    /// append(const char*, uint64_t, int8_t flag = -1)、append(const ObString&, int8_t flag = -1)（:68-96）合成一个，flag 显式写；
    /// append(const char*)（:63-66）传 flag = 0
    pub fn append(&mut self, s: &[u8], flag: i8) -> ObResult;
    pub fn reserve(&mut self, len: u64) -> ObResult;                // :98-118
    pub fn extend(&mut self, len: u64) -> ObResult;                 // :120-141
    pub fn ptr(&self) -> &[u8];                                     // h:44，[0, len_)
    pub fn ptr_mut(&mut self) -> &mut [u8];                         // h:52，[0, cap_)
    pub fn length(&self) -> u64;
    pub fn capacity(&self) -> u64;
    pub fn remain(&self) -> u64;                                    // h:47-50
    pub fn empty(&self) -> bool;
    pub fn set_length(&mut self, len: u64) -> ObResult;             // .cpp:143-159
    pub fn back(&self) -> u8;                                       // h:54
    pub fn string(&self) -> &[u8];                                  // .cpp:176-179
}
impl crate::fmt::ObToString for ObStringBuffer<'_> { /* "len = %ld, cap = %ld"（h:57-62） */ }
```

`deep_copy(ObIAllocator*, ObStringBuffer&)`（`.cpp:161-174`）不提供：它其实是让两个缓冲指向同一块内存，按 `deep_copy\([^,()]+,\s*[a-z_]*buf` 等写法扫描没有调用者；1c 若找到调用者，走修订。

#### 2.1.5 带分配器的辅助函数（`ob_string.h:854-974`）

```rust
/// ob_strip_space（:854-877）
pub fn ob_strip_space<'q>(arena: &'q Arena, src: &[u8], dst: &mut ObStr<'q>) -> ObResult;
/// ob_simple_low_to_up（:879-906）
pub fn ob_simple_low_to_up<'q>(arena: &'q Arena, src: &[u8], dst: &mut ObStr<'q>) -> ObResult;
/// ob_sub_str 的两个重载（:908-930、:932-954）合成一个：4 参数的写 end_index = None
pub fn ob_sub_str<'q>(arena: &'q Arena, src: &[u8], start_index: i32, end_index: Option<i32>, dst: &mut ObStr<'q>) -> ObResult;
/// ob_dup_cstring（:956-974）：dst 是带 NUL 的 C 串（交给 FFI，或 .to_bytes() 后当字节用）
pub fn ob_dup_cstring<'q>(arena: &'q Arena, src: &[u8], dst: &mut Option<&'q CStr>) -> ObResult;
```

`ob_write_string` 不在这里：按 RULEBOOK §2.4 与 01 §2.12，`ob_write_string(alloc, src, dst)` → `arena.try_str(src)`（拥有型 `try_bytes(src)`），`c_style = true` → `arena.try_str_c(src)`；空 `src` 返回 `Ok(&[])` 不分配（01 第 762 行）。

#### 2.1.6 C 字符串函数

```rust
/// strcasecmp（含 STRCASECMP、Windows 的 _stricmp）：C locale，只折叠 ASCII（RULEBOOK §2.5）。
/// 输入是"到 NUL 为止"的字节：切片末尾当作 NUL，切片里的 NUL 也终止比较。
pub fn ob_strcasecmp(s1: &[u8], s2: &[u8]) -> c_int;
/// strncasecmp / STRNCASECMP / _strnicmp：同上，最多比 n 个字节
pub fn ob_strncasecmp(s1: &[u8], s2: &[u8], n: usize) -> c_int;
/// strlen / STRLEN 作用在定长缓冲（char buf[N] → [u8; N]）上：第一个 NUL 的下标，没有 NUL 就是 len
pub fn strlen(s: &[u8]) -> usize;
/// memmem / MEMMEM（Windows 的 ob_memmem，alloc_assist.h:79-92）：needle 为空返回 Some(0)
pub fn memmem(haystack: &[u8], needle: &[u8]) -> Option<usize>;
/// strcasestr / STRCASESTR（Windows 版 alloc_assist.h:30-41）：ASCII 折叠
pub fn strcasestr(haystack: &[u8], needle: &[u8]) -> Option<usize>;
/// strtok_r / STRTOK_R（约 27 处）：跳过前导分隔符、不返回空记号；不往缓冲里写 NUL（S-STR-16）
pub fn strtok_r<'a>(s: Option<&'a [u8]>, delim: &[u8], saveptr: &mut &'a [u8]) -> Option<&'a [u8]>;
```

其余 C 字符串函数没有辅助函数，按下表写（`const char *` 一律是 `&[u8]`，不带 NUL）：

| C++ | Rust |
|---|---|
| `memcmp`/`MEMCMP`（约 113 处）、`strcmp`/`STRCMP`（约 198）、`strncmp`/`STRNCMP`（约 63） | 切片比较：`a.cmp(b)`、`a == b`、`a[..n].cmp(&b[..n])`（`strncmp` 先各自截到 NUL）；调用点只用符号（S-STR-4） |
| `strlen`（约 852 处）作用在 `const char *`（Rust 里已是不带 NUL 的切片） | `s.len()`；作用在定长缓冲上用 `strlen(&buf)` |
| `memchr`/`strchr` / `memrchr`/`strrchr` | `s.find(c)` / `s.reverse_find(c)`（`ObStringExt`） |
| `strstr`/`STRSTR` | `memmem(h, n)` |
| `MEMCPY`/`MEMMOVE`/`MEMSET` | `copy_from_slice` / `copy_within` / `fill` |
| `strspn`/`strcspn`/`strpbrk`/`strsep`/`strtok`（共约 8 处） | 迭代器写法，逐处翻译；`strtok` 改用 `strtok_r` |
| 字符串字面量 `"abc"` 交给字节接口 | `b"abc"`；FFI 边界要 C 串时用 `c"abc"`（`&CStr`） |

### 2.2 `oblib::ctype`

```rust
//! C locale 的 <ctype.h>。main 在启动时把 LC_CTYPE 设成 "C"（src/observer/main.cpp:693-696）。
use core::ffi::c_int;
/// 参数照 C 的写法传 int：字节写 `b as c_int`（S-CTYPE-3）。只有 0..=127 会被分类。
pub const fn isspace(c: c_int) -> bool;   // ' ' \t \n \v \f \r
pub const fn isblank(c: c_int) -> bool;   // ' ' \t
pub const fn isdigit(c: c_int) -> bool;
pub const fn isxdigit(c: c_int) -> bool;
pub const fn isalpha(c: c_int) -> bool;
pub const fn isalnum(c: c_int) -> bool;
pub const fn isupper(c: c_int) -> bool;
pub const fn islower(c: c_int) -> bool;
pub const fn isprint(c: c_int) -> bool;   // 0x20..=0x7E
pub const fn isgraph(c: c_int) -> bool;   // 0x21..=0x7E
pub const fn ispunct(c: c_int) -> bool;
pub const fn iscntrl(c: c_int) -> bool;   // 0x00..=0x1F、0x7F
pub const fn isascii(c: c_int) -> bool;
/// 'A'..='Z' 之外原样返回（含负数和 128..=255）
pub const fn tolower(c: c_int) -> c_int;
pub const fn toupper(c: c_int) -> c_int;
```

### 2.3 `oblib::num`（§3 白名单第 7 项）

```rust
use core::ffi::{c_int, c_long, c_ulong};
use crate::error::ObResult;

// ---- C 库的数值解析（RULEBOOK §2.5 倒数第二行；改名表的 c_strtod "等"） ----
/// strtod(nptr, &endptr)。nptr 是"C 会扫描的字节"：扫到切片里第一个 NUL 或切片末尾为止。
/// endptr 是相对 nptr 的偏移（C 的 endptr - nptr）；None ⇔ C 传 NULL。errno 由 C 库照常设置，本函数不清零。
pub fn c_strtod(nptr: &[u8], endptr: Option<&mut usize>) -> f64;
pub fn c_strtol(nptr: &[u8], endptr: Option<&mut usize>, base: c_int) -> c_long;
pub fn c_strtoll(nptr: &[u8], endptr: Option<&mut usize>, base: c_int) -> i64;
pub fn c_strtoul(nptr: &[u8], endptr: Option<&mut usize>, base: c_int) -> c_ulong;
pub fn c_strtoull(nptr: &[u8], endptr: Option<&mut usize>, base: c_int) -> u64;
pub fn c_atof(nptr: &[u8]) -> f64;
pub fn c_atoi(nptr: &[u8]) -> c_int;
pub fn c_atol(nptr: &[u8]) -> c_long;
pub fn c_atoll(nptr: &[u8]) -> i64;
/// C 的 errno（读）和 errno = v（写）：当前线程的 C errno（§8 Q4）
pub fn errno() -> c_int;
pub fn set_errno(value: c_int);

// ---- fast_float 6.1.3 的 from_chars 契约（RULEBOOK §2.5 最后一行，0.14） ----
/// std::errc。errc::default() 即 C++ 的 std::errc()（成功）。
#[allow(non_camel_case_types, non_upper_case_globals)]
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
#[repr(transparent)]
pub struct errc(pub c_int);
#[allow(non_upper_case_globals)]
impl errc {
    pub const invalid_argument: errc = errc(libc::EINVAL);
    pub const result_out_of_range: errc = errc(libc::ERANGE);
}
/// fast_float::from_chars_result（float_common.h:35-39）。ptr 是相对输入切片起点的偏移。
#[allow(non_camel_case_types)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct from_chars_result { pub ptr: usize, pub ec: errc }
/// 能解析的类型：f64、f32（parse_number.h:183-188），u32、u64、i32、i64（10 进制，parse_number.h:299-316）
pub trait FastFloatValue: Copy + sealed::Sealed {}
/// fast_float::from_chars(first, last, value)：s = [first, last)
pub fn fast_float_from_chars<T: FastFloatValue>(s: &[u8], value: &mut T) -> from_chars_result;

// ---- 以浮点数为键的有序容器（RULEBOOK §2.5；§8 Q2） ----
/// 复刻 C++ operator< 的等价类的全序键：非 NaN 按 < 比较（−0.0 与 +0.0 相等）；
/// 所有 NaN（不论符号和载荷）彼此相等、大于一切非 NaN。只给 BTreeMap/BTreeSet 用。
#[derive(Clone, Copy, Debug, Default)]
#[repr(transparent)]
pub struct CppFloatKey<T>(pub T);
impl PartialEq for CppFloatKey<f64> { /* … */ }  impl Eq for CppFloatKey<f64> {}
impl PartialOrd for CppFloatKey<f64> { /* … */ } impl Ord for CppFloatKey<f64> { /* … */ }
// f32 同上

/// std::map<float/double, V> 的 operator[] 与中序遍历：逐行移植 libc++ 19.1.2 的 __tree（§3.3 S-NUM-3）。
pub trait CppFloat: Copy + PartialOrd + sealed::Sealed {}   // f32、f64
pub struct CppFloatMap<K: CppFloat, V> { /* nodes: Vec<Node<K, V>>（下标代替指针），root、begin、size */ }
impl<K: CppFloat, V: Default> CppFloatMap<K, V> {
    pub fn new() -> Self;
    /// operator[]（$LIBCXX/map:1510-1515）：没有等价键时插入 V::default()。
    /// 新节点的空间用 try_reserve，失败返回 OB_ALLOCATE_MEMORY_FAILED（C++ 在 -D_NO_EXCEPTION 下终止进程）。
    pub fn at(&mut self, k: K) -> ObResult<&mut V>;
    pub fn len(&self) -> usize;
    /// begin()..end() 的中序遍历（__tree_next，$LIBCXX/__tree:183-192）
    pub fn iter(&self) -> impl Iterator<Item = (&K, &V)> + '_;
}

// ---- 浮点转窄整型（RULEBOOK §2.12；FIXED(port)） ----
/// static_cast<int8_t/uint8_t/int16_t/uint16_t>(浮点)：先饱和到 i32（NaN → 0），再截断。f32 实参写 `x as f64`（精确）。
pub fn c_fptosi_i8(x: f64) -> i8;
pub fn c_fptosi_u8(x: f64) -> u8;
pub fn c_fptosi_i16(x: f64) -> i16;
pub fn c_fptosi_u16(x: f64) -> u16;

// ---- 没有守卫的整数除法和取余（RULEBOOK §2.12） ----
pub trait CDivRem: Copy + sealed::Sealed {}   // i32、i64、u32、u64、isize、usize（C 的 int 以下先提升到 int）
pub fn c_div<T: CDivRem>(a: T, b: T) -> T;
pub fn c_rem<T: CDivRem>(a: T, b: T) -> T;

// ---- libc crate 没有声明的 libm 函数（RULEBOOK §2.12；§8 Q3） ----
pub fn remainder(x: f64, y: f64) -> f64;
pub fn lround(x: f64) -> c_long;
pub fn lrint(x: f64) -> c_long;
pub fn frexp(x: f64, exp: &mut c_int) -> f64;
pub fn ldexp(x: f64, exp: c_int) -> f64;
pub fn modf(x: f64, iptr: &mut f64) -> f64;

mod sealed { pub trait Sealed {} }
```

### 2.4 `oblib::sort`

```rust
/// lib::ob_sort(first, last, comp)（ob_sort.h:27-41）= std::sort 的通用路径（$LIBCXX/__algorithm/sort.h:1000-1004 →
/// __sort_impl :986-998 → __sort_dispatch :919-932 → __introsort<…, false> :756-869）。
/// comp(a, b) 就是 C++ 的 comp(*a, *b)："a 排在 b 前面"。C++ 的 [first, last) 写成 &mut v[first..last]。
/// ob_sort(first, last)（:43-55）写成 ob_sort(v, |l, r| l < r)。
pub fn ob_sort<T, F: FnMut(&T, &T) -> bool>(v: &mut [T], comp: F);
/// std::push_heap(first, last, comp)（push_heap.h:58-71）：新元素在 v 的最后一个位置
pub fn push_heap<T, F: FnMut(&T, &T) -> bool>(v: &mut [T], comp: F);
/// std::pop_heap（pop_heap.h:33-68）：堆顶换到最后
pub fn pop_heap<T, F: FnMut(&T, &T) -> bool>(v: &mut [T], comp: F);
/// std::make_heap（make_heap.h:29-49）
pub fn make_heap<T, F: FnMut(&T, &T) -> bool>(v: &mut [T], comp: F);
/// std::sort_heap（sort_heap.h:32-50）
pub fn sort_heap<T, F: FnMut(&T, &T) -> bool>(v: &mut [T], comp: F);
/// std::lower_bound(first, last, value, comp)（lower_bound.h:30-48、:87-93）：comp(元素, value)
pub fn lower_bound<T, U: ?Sized, F: FnMut(&T, &U) -> bool>(v: &[T], value: &U, comp: F) -> usize;
/// std::upper_bound（upper_bound.h:30-55）：comp(value, 元素)
pub fn upper_bound<T, U: ?Sized, F: FnMut(&U, &T) -> bool>(v: &[T], value: &U, comp: F) -> usize;
/// std::binary_search（binary_search.h:24-29）
pub fn binary_search<T, F: FnMut(&T, &T) -> bool>(v: &[T], value: &T, comp: F) -> bool;
```

不提供、也不需要的：分区的位集路径和无分支的 `__sort3/4/5`（`sort.h:130-266`、`:394-618`），以及算术类型默认比较时转到系统 dylib 的 `std::__sort<__less<T>&, T*>`（`:890-917`、`:958-984`），理由见 S-SORT-3。

### 2.5 `oblib::serialization`

#### 2.5.1 原语（`serialization.h`，名字与字节序不变，RULEBOOK §2.11）

C++ 的 `(char *buf, int64_t buf_len, int64_t &pos)` 一律是 `(buf: &mut [u8], pos: &mut i64)`，`buf_len == buf.len()`；`(const char *buf, int64_t data_len, int64_t &pos)` 是 `(buf: &[u8], pos: &mut i64)`。输出指针参数改成 `&mut`。

```rust
pub const OB_MAX_V1B: u64 = (1 << 7) - 1;   /* … OB_MAX_V9B，:33-41 */
pub const OB_SERIALIZE_SIZE_NEED_BYTES: i8 = 5;                                    // :96
// 定长，大端（:120-269）
pub fn encoded_length_i8(val: i8) -> i64;
pub fn encode_i8(buf: &mut [u8], pos: &mut i64, val: i8) -> ObResult;            // :125-133
pub fn decode_i8(buf: &[u8], pos: &mut i64, val: &mut i8) -> ObResult;           // :135-142
pub fn encoded_length_i16(val: i16) -> i64;
pub fn encode_i16(buf: &mut [u8], pos: &mut i64, val: i16) -> ObResult;
pub fn decode_i16(buf: &[u8], pos: &mut i64, val: &mut i16) -> ObResult;
pub fn encoded_length_i32(val: i32) -> i64;
pub fn encode_i32(buf: &mut [u8], pos: &mut i64, val: i32) -> ObResult;
pub fn decode_i32(buf: &[u8], pos: &mut i64, val: &mut i32) -> ObResult;
pub fn encoded_length_i64(val: i64) -> i64;
pub fn encode_i64(buf: &mut [u8], pos: &mut i64, val: i64) -> ObResult;          // :212-227
pub fn decode_i64(buf: &[u8], pos: &mut i64, val: &mut i64) -> ObResult;
pub fn encoded_length_bool(val: bool) -> i64;
pub fn encode_bool(buf: &mut [u8], pos: &mut i64, val: bool) -> ObResult;
pub fn decode_bool(buf: &[u8], pos: &mut i64, val: &mut bool) -> ObResult;
// 变长（:271-490）
pub fn encoded_length_vi64(val: i64) -> i64;
pub fn encode_vi64(buf: &mut [u8], pos: &mut i64, val: i64) -> ObResult;         // :308-323
pub fn encode_fixed_bytes_i64(buf: &mut [u8], pos: &mut i64, val: i64) -> ObResult; // :326-343
pub fn decode_vi64(buf: &[u8], pos: &mut i64, val: &mut i64) -> ObResult;        // :345-369
pub fn encoded_length_vi32(val: i32) -> i64;
pub fn encode_vi32(buf: &mut [u8], pos: &mut i64, val: i32) -> ObResult;
pub fn decode_vi32(buf: &[u8], pos: &mut i64, val: &mut i32) -> ObResult;
pub fn encoded_length_float(val: f32) -> i64;
pub fn encode_float(buf: &mut [u8], pos: &mut i64, val: f32) -> ObResult;
pub fn decode_float(buf: &[u8], pos: &mut i64, val: &mut f32) -> ObResult;
pub fn encoded_length_double(val: f64) -> i64;
pub fn encode_double(buf: &mut [u8], pos: &mut i64, val: f64) -> ObResult;
pub fn decode_double(buf: &[u8], pos: &mut i64, val: &mut f64) -> ObResult;
// vstr（:492-604）
pub fn encoded_length_vstr(len: i64) -> i64;                                       // :499-502
pub fn decoded_length_vstr(buf: &[u8], pos: i64) -> i64;                           // :514-524
pub fn encode_vstr(buf: &mut [u8], pos: &mut i64, vbuf: &[u8]) -> ObResult;      // :533-552
/// decode_vstr(buf, data_len, pos, &len)（:559-580）：零拷贝，lenp 照 C++ 置 -1 或长度
pub fn decode_vstr<'de>(buf: &'de [u8], pos: &mut i64, lenp: &mut i64) -> Option<&'de [u8]>;
/// decode_vstr(buf, data_len, pos, dest, buf_len, &len)（:582-604）：拷进 dest
pub fn decode_vstr_copy<'de>(buf: &'de [u8], pos: &mut i64, dest: &mut [u8], lenp: &mut i64) -> Option<&'de [u8]>;
// ObNumber（:1191-1249）。digits 是 len_ 个本机序（各目标平台都是小端）u32 的原始字节
pub fn encode_length_number_type(desc: ObNumberDesc) -> i64;
pub fn encode_number_type(buf: &mut [u8], pos: &mut i64, desc: ObNumberDesc, digits: &[u32]) -> ObResult;
pub fn decode_number_type<'de>(buf: &'de [u8], pos: &mut i64, desc: &mut ObNumberDesc, digits: &mut &'de [u8]) -> ObResult;
// C 数组（:1754-1785、:1879-1914、:1988-2005）
/// T (&val)[CNT]：vi64(CNT) 加逐个元素；解码时个数必须等于 CNT
pub fn encode_array<T: ObSerialize>(buf: &mut [u8], pos: &mut i64, val: &[T]) -> ObResult;
pub fn decode_array<'de, T: ObDeserialize<'de>>(buf: &'de [u8], pos: &mut i64, val: &mut [T]) -> ObResult;
pub fn encoded_length_array<T: ObSerialize>(val: &[T]) -> i64;
/// char (&val)[CNT]：vi64(strlen + 1) 加含 NUL 的字节
pub fn encode_cstr_array(buf: &mut [u8], pos: &mut i64, val: &[u8]) -> ObResult;
pub fn decode_cstr_array(buf: &[u8], pos: &mut i64, val: &mut [u8]) -> ObResult;
pub fn encoded_length_cstr_array(val: &[u8]) -> i64;
// 按类型分派（:1691-2014）
pub fn encode<T: ObSerialize + ?Sized>(buf: &mut [u8], pos: &mut i64, val: &T) -> ObResult;
pub fn decode<'de, T: ObDeserialize<'de> + ?Sized>(buf: &'de [u8], pos: &mut i64, val: &mut T) -> ObResult;
pub fn encoded_length<T: ObSerialize + ?Sized>(val: &T) -> i64;
// ob_serialization_helper.h（:28-176）
pub fn encode_item<T: ObSerialize + ?Sized>(buf: &mut [u8], pos: &mut i64, item: &T) -> ObResult;
pub fn decode_item<'de, T: ObDeserialize<'de> + ?Sized>(buf: &'de [u8], pos: &mut i64, item: &mut T) -> ObResult;
pub fn encoded_length_item<T: ObSerialize + ?Sized>(item: &T) -> i64;
```

`ObNumberDesc` 是 `ob_define.h:1786` 的位域联合体（`ob_bitfield!`，归属见 §8 Q8）。

#### 2.5.2 trait（RULEBOOK §2.11）

```rust
/// serialize(char*, int64_t, int64_t&) const + get_serialize_size() const
pub trait ObSerialize {
    fn serialize(&self, buf: &mut [u8], pos: &mut i64) -> ObResult;
    fn get_serialize_size(&self) -> i64;
}
/// deserialize(const char*, int64_t, int64_t&)：就地解码；'de 是输入缓冲的生命周期（零拷贝字段借用它）
pub trait ObDeserialize<'de> {
    fn deserialize(&mut self, buf: &'de [u8], pos: &mut i64) -> ObResult;
}
/// RULEBOOK §2.11 的 deserialize_in：输入缓冲活不过对象、字符串要拷进 arena 的类型实现它
pub trait ObDeserializeIn<'q> {
    fn deserialize_in(&mut self, buf: &[u8], pos: &mut i64, arena: &'q Arena) -> ObResult;
}
/// OB_DEF_SERIALIZE/DESERIALIZE/SERIALIZE_SIZE 的函数体：serialize_/deserialize_/get_serialize_size_
/// （ob_unify_serialize.h:225-231）。unis 头由 ob_unis! 统一加上。
pub trait ObUnisBody<'de> {
    fn serialize_(&self, buf: &mut [u8], pos: &mut i64) -> ObResult;
    fn deserialize_(&mut self, buf: &'de [u8], pos: &mut i64) -> ObResult;
    fn get_serialize_size_(&self) -> i64;
}

// 现成的实现（格式见 S-SER-2 的表；方法体省略）：
// ObSerialize：i8 u8 bool i16 u16 i32 u32 i64 u64 isize usize f32 f64；
//   (A, B)（std::pair，:1787-1795）；&T、Box<T>（委托给 T，T: ?Sized）；
//   [u8]（ObString，ob_string.cpp:25-37、:73-76）；ObStringWriter<'_>（已写部分）。
// ObDeserialize<'de>：上面的标量、(A, B)、Box<T>（T: Sized）；
//   &'de [u8]（ObString 的零拷贝分支，ob_string.cpp:48-53）；
//   ObStringWriter<'_>（拷贝分支，:54-65）；
//   Box<[u8]>（拥有型：零拷贝解码后 try_reserve 拷贝）。
// ObDeserializeIn<'q>：标量委托给 ObDeserialize；&'q [u8] 解码后 arena.try_str。
// 数组 [T; N] 故意不实现（S-SER-8）。

/// OB_SERIALIZE_MEMBER_TEMP(template<int N>, UNFDummy<N>)（ob_unify_serialize.h:444-448）：只有 unis 头
pub struct UNFDummy<const N: i64>;

/// unis 头的三段（OB_UNIS_SERIALIZE/DESERIALIZE/SERIALIZE_SIZE，:165-216），供 ob_unis! 展开和手写代码调用
#[doc(hidden)] pub fn unis_serialize(buf: &mut [u8], pos: &mut i64, unis_version: i64,
    expect_size: impl FnOnce() -> i64, body: impl FnOnce(&mut [u8], &mut i64) -> ObResult) -> ObResult;
#[doc(hidden)] pub fn unis_deserialize<'de>(buf: &'de [u8], pos: &mut i64, unis_version: i64, cls: &'static str,
    body: impl FnOnce(&'de [u8], &mut i64) -> ObResult) -> ObResult;
#[doc(hidden)] pub fn unis_serialize_size(unis_version: i64, body_size: i64) -> i64;
```

#### 2.5.3 宏（`oblib` 根，`#[macro_export]`，`macro_rules!`，RULEBOOK §1"序列化"行）

成员列表里写 C++ 宏参数里的字段名，宏展开成 `self.<名字>`。C++ 的 `char x_[N]` 默认按 C 串数组编码（`[u8; N]`）；`uint8_t`/`unsigned char` 数组和其他元素类型的 C 数组在名字前加 `@array`（S-SER-8）。

| C++ | Rust |
|---|---|
| `OB_UNIS_VERSION(v)` + `OB_SERIALIZE_MEMBER(C, a_, b_)` | `ob_serialize_member!(C, v; a_, b_);` |
| `OB_SERIALIZE_MEMBER((C, P), a_)` | `ob_serialize_member!((C, base: P), v; a_);`（`base` 换成 §2.13 规定的基类字段名） |
| 含借用字段的类型 | `ob_serialize_member!(<'de> C<'de>, v; a_);` |
| `OB_SERIALIZE_MEMBER_INHERIT(C, P, a_)` | `ob_serialize_member_inherit!(C, base: P; a_);` |
| `OB_SERIALIZE_MEMBER_SIMPLE(C, a_)` | `ob_serialize_member_simple!(C; a_);` |
| `OB_SERIALIZE_MEMBER_TEMP(template<typename T>, C<T>, a_)` | `ob_serialize_member_temp!([T: ObSerialize + for<'x> ObDeserialize<'x>] C<T>, v; a_);` |
| `OB_SERIALIZE_MEMBER_TEMP_INHERIT(TEMP, C, P, …)` | `ob_serialize_member_temp_inherit!([…] C<…>, base: P, v; …);` |
| `OB_SERIALIZE_MEMBER_IF(C, PRED, a_)` | `ob_serialize_member_if!(C, v, \|s: &C\| <PRED>; a_);` |
| `OB_DEF_SERIALIZE(C)` / `OB_DEF_DESERIALIZE(C)` / `OB_DEF_SERIALIZE_SIZE(C)` 的函数体 | `impl<'de> ObUnisBody<'de> for C { … }` 加 `ob_unis!(C, v);`（模板：`ob_unis!([T: …] C<T>, v);`；借用：`ob_unis!(<'de> C<'de>, v);`） |
| `OB_DEF_SERIALIZE_SIMPLE` 一族、`NEED_SERIALIZE_AND_DESERIALIZE` + `DEFINE_SERIALIZE` 一族 | 手写 `impl ObSerialize` + `impl<'de> ObDeserialize<'de>`，不带 unis 头 |
| `OB_UNIS_DEF_SERIALIZE` / `_SIZE`（各 1 处） | 手写 `impl ObUnisBody`，`serialize_`/`get_serialize_size_` 里用 `ob_unis_encode!`/`ob_unis_add_len!` |
| `OB_UNIS_ENCODE(x)`、`LST_DO_CODE(OB_UNIS_ENCODE, a, b)` | `ob_unis_encode!(ret, buf, pos, a, b);`（变长，一个也行） |
| `OB_UNIS_DECODE(x)`、`LST_DO_CODE(OB_UNIS_DECODE, …)` | `ob_unis_decode!(ret, buf, pos, a, b);`（实参是可写的位置表达式） |
| `OB_UNIS_ADD_LEN(x)`、`LST_DO_CODE(OB_UNIS_ADD_LEN, …)` | `ob_unis_add_len!(len, a, b);` |
| `OB_UNIS_ENCODE_IF/DECODE_IF/ADD_LEN_IF(x, PRED)` | `ob_unis_encode_if!(ret, buf, pos, x, pred)` 等 |
| `OB_UNIS_DECODE_AND_FUNC(obj, FUNC)` | `ob_unis_decode_and_func!(ret, buf, pos, obj, \|tmp, obj\| func(tmp, obj))` |
| `OB_UNIS_ENCODE_ARRAY` / `DECODE_ARRAY` / `ADD_LEN_ARRAY(objs, cnt)` | `ob_unis_encode_array!(ret, buf, pos, objs, cnt)` 等 |
| `OB_UNIS_DECODE_ARRAY_AND_FUNC(objs, cnt, FUNC)` | `ob_unis_decode_array_and_func!(ret, buf, pos, cnt, T, \|tmp\| func(tmp))` |
| `OB_UNIS_ENCODE_ARRAY_POINTER[_IF]` 等（各 3–6 处） | `ob_unis_encode_array_pointer!(ret, buf, pos, objs, cnt[, pred])` 等，`objs` 是 `&[Option<&T>]` 一类 |
| `BASE_SER(…)` / `BASE_DESER(…)` / `BASE_ADD_LEN(…)` | `base_ser!(ret, buf, pos, self.base)` / `base_deser!(…)` / `base_add_len!(len, self.base)` |
| `SERIALIZE_SIZE_HEADER(version)` | `serialize_size_header!(len, version)` |
| `CHECK_VERSION_LENGTH(CLS, VER, LEN)`（直接用 2 处） | `check_version_length!(ret, "CLS", unis_version, ver, len, buf, pos)` |
| `LST_DO_CODE(M, …)`，`M` 不是 unis 宏（6 处） | `lst_do_code!(m, (固定实参), x1, x2)` 展开成 `m!(固定实参, x1); m!(固定实参, x2);` |
| C++ 枚举作序列化成员（`EnumEncoder`，:1645-1670） | 枚举新类型旁边写 `ob_serialize_enum!(T);`（RULEBOOK §2.12 的每个枚举都写，不论是否被序列化） |
| `UNIS_VERSION`（类里的常量） | `Self::UNIS_VERSION`（由 `ob_unis!`/`ob_serialize_member*!` 生成的固有常量） |
| `UNIS_VERSION_GUARD(x)`（空宏）、`OB_DECLARE_UNIS` | 不译 |
| `get_unis_compat_version()`（1 处） | 所在单元照译成 `static AtomicU64` |

### 2.6 `oblib::pod` 与 `ob_bitfield!`（RULEBOOK 0.10、§2.12）

```rust
/// 定宽字段的小端编解码。实现：u8 i8 u16 i16 u32 i32 u64 i64 f32 f64 bool [u8; N]，
/// 以及 ob_bitfield! 和 derive(ObPod) 生成的类型（可以嵌套）。
pub trait PodField: Copy {
    const SIZE: usize;
    /// bytes.len() >= SIZE；读 bytes[..SIZE]
    fn read_le(bytes: &[u8]) -> Self;
    /// bytes.len() >= SIZE；写 bytes[..SIZE]
    fn write_le(self, bytes: &mut [u8]);
}
pub fn read_le<F: PodField>(buf: &[u8], off: usize) -> F;
pub fn write_le<F: PodField>(buf: &mut [u8], off: usize, v: F);

/// 布局表的一行，供 golden 测试与 C++ 的 offsetof/位模式对照
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PodFieldLayout { pub name: &'static str, pub offset: usize, pub size: usize, pub bit_lo: u32, pub bit_hi: u32 }

/// 落盘头的逐字段编解码（0.10）
pub trait PodCodec: Sized {
    /// C++ 的 sizeof
    const SIZE: usize;
    const LAYOUT: &'static [PodFieldLayout];
    /// buf.len() < SIZE → Err(OB_BUF_NOT_ENOUGH)（后备检查，C++ 在 reinterpret_cast 前的长度检查照原样保留）
    fn read_from(buf: &[u8]) -> ObResult<Self>;
    /// buf.len() >= SIZE 是前提，不满足就 ob_abort（C++ 里是越界写）；不属于任何字段的字节写 0
    fn write_to(&self, buf: &mut [u8]);
}

// 实现在 ob_macros（过程宏，由 06 设计和测试），由 oblib::pod 和 oblib 根重新导出
pub use ob_macros::{ob_bitfield, ObPod};
```

`ob_bitfield!` 的写法与生成物：

```rust
ob_bitfield! {
    /// ob_datum.h:141-145
    #[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
    pub struct ObDatumDesc(pub pack_: u32) {       // 整个存储字的字段名照 C++ 联合体里的整数成员
        len_:  u32 @ 0..29,                         // 名字: C++ 声明类型 @ 低位..高位（从最低位数起，左闭右开）
        flag_: u32 @ 29..31,
        null_: u32 @ 31..32,
    }
}
// 生成：#[repr(transparent)] pub struct ObDatumDesc { pub pack_: u32 }
//   pub const fn len_(&self) -> i32          —— 读：返回 C++ 整型提升后的类型（S-POD-3）
//   pub fn set_len_(&mut self, v: u32)       —— 写：参数是声明类型，按位宽截断
//   pub const BITS: &'static [PodFieldLayout]；impl PodField（SIZE = 4）
// C++ 联合体的多种视图（ob_micro_block_header.h:49-62 的 opt_）允许位段重叠；
// 声明类型可以是 bool（读出 bool）或枚举新类型（读出该类型）。
```

`#[derive(ObPod)]` 的写法与生成物：

```rust
#[derive(Clone, Copy, Default, Debug, ObPod)]
#[ob_pod(size = 24)]                                // C++ sizeof；ob_macro_block_common_header.h:96-104
pub struct ObMacroBlockCommonHeader {
    #[ob_pod(offset = 0)]  pub header_size_: i32,
    #[ob_pod(offset = 4)]  pub version_: i32,
    #[ob_pod(offset = 8)]  pub magic_: i32,
    #[ob_pod(offset = 12)] pub attr_: i32,
    #[ob_pod(offset = 16)] pub payload_size_: i32,
    #[ob_pod(offset = 20)] pub payload_checksum_: i32,
}
// 生成：impl PodCodec、impl PodField（可嵌套）；
//   pub struct ObMacroBlockCommonHeaderView<'a>(&'a [u8])：new(bytes) -> ObResult<Self>、每个字段一个读函数、bytes()、payload()（[SIZE..]）
//   pub struct ObMacroBlockCommonHeaderMut<'a>(&'a mut [u8])：读函数 + set_<字段>()、payload_mut()
// 字段属性：offset = N（必填）；flatten（字段是 ob_bitfield! 类型时，把它的位段读写函数转发到外层和视图上，
//   用于 C++ 的匿名位域结构）；alias(a, b)（同一位置的标量联合体成员，生成同名读写函数）。
```

### 2.7 `oblib::slot`

```rust
/// 类型化槽位（RULEBOOK 0.4 第 3 种拆法、0.5）：组合根绑定一次，之后只读。
/// 放进 static 要求 T: Sync（dyn Trait 写成 trait 以 Sync 为超 trait，或 Slot<dyn Trait + Sync>）。
pub struct Slot<T: ?Sized + 'static> { cell: std::sync::OnceLock<&'static T> }
impl<T: ?Sized + 'static> Slot<T> {
    pub const fn new() -> Self;
    /// bind_server_service(p)（ob_server_runtime.h:132-137）：第二次绑定返回 Err(OB_INIT_TWICE) 并记 ERROR
    pub fn bind(&self, v: &'static T) -> ObResult;
    /// server_service<T>()（:139-143）：没绑定时 None，对应 C++ 的空指针
    pub fn get(&self) -> Option<&'static T>;
    /// 转发函数用：没绑定时返回 C++ 空指针分支的错误码（OB_NOT_INIT、OB_NOT_SUPPORTED 等，由 seams.tsv 给出）
    pub fn get_or(&self, empty: ObError) -> ObResult<&'static T>;
    pub fn is_bound(&self) -> bool;
}
impl<T: ?Sized + 'static> Default for Slot<T> { /* new() */ }
// 生成物（generated/slots.rs，§4）：pub static I_LOB_READ_SERVICE_SLOT: Slot<dyn ObILobReadService> = Slot::new();
```

### 2.8 `oblib::prelude`

每个单元文件的第一条 `use` 是 `use oblib::prelude::*;`（RULEBOOK §4）。oblib 自己的单元也这样写：`oblib/src/lib.rs` 声明 `extern crate self as oblib;`。收录原则：几乎每个单元都要用的错误、日志、格式化宏；方法解析需要在作用域里的 trait；C 的整型别名；01、02、04 文档点名要进 prelude 的名字。其余一律显式 `use`。名单里别的模块的项以该模块冻结后的 API 为准，06 只做 `pub use`。

```rust
//! rust/port/oblib/src/prelude.rs
// 04（04 §2.0 末段）
pub use crate::error::*;                                   // ObError、ObResult、全部 OB_* 常量
pub use crate::error::{ObResultExt, ObErrcode};
pub use crate::fmt::{ObBuf, ObBufExt, ObToString, ObFmtArgs};
pub use crate::log::{ObLogMod, ObLogger, ObWarningBufferRef};
pub use crate::{ob_fail, ob_succ, cover_succ, failedx, false_it, ck, ox, oz, ov, ozx1, ozx2, ob_assert,
                defer, ob_e, errsim_point, ob_printf, snprintf, databuff_printf, buf_printf, append_fmt, assign_fmt,
                to_string_kv, k, kv, kr, kp, kpc, log_error, log_warn, log_info, log_debug, log_trace, ob_log,
                log_user_error /* …04 的全部宏，以 04 冻结的清单为准 */};
// 01（01 §7）
pub use crate::mem::{Arena, ArenaVec, ObStr, MemCtx, MemBudget, try_box};
pub use crate::{smart_var, smart_var_independent, heap_var, smart_vars_2, smart_vars_3, smart_vars_4,
                heap_vars_2, heap_vars_3, heap_vars_4};
// 02（02 §2.0）
pub use crate::sync::{Latch, RwLatch /* …02 §2 的全部公开名字 */};
pub use std::sync::atomic::{AtomicBool, AtomicI8, AtomicI16, AtomicI32, AtomicI64, AtomicU8, AtomicU16, AtomicU32,
                            AtomicU64, AtomicUsize, AtomicIsize, AtomicPtr, Ordering, fence, compiler_fence};
// 05
pub use crate::container::ObIArray;
// 06
pub use crate::string::{ObStringExt, ObStringWriter, ObSqlString, ObStringBuffer, ob_strcasecmp, ob_strncasecmp};
pub use crate::serialization::{self, ObSerialize, ObDeserialize, ObDeserializeIn, ObUnisBody};
pub use crate::{ob_serialize_member, ob_serialize_member_inherit, ob_serialize_member_simple, ob_serialize_member_temp,
                ob_serialize_member_temp_inherit, ob_serialize_member_if, ob_serialize_enum, ob_unis, ob_unis_encode,
                ob_unis_decode, ob_unis_add_len, ob_unis_encode_if, ob_unis_decode_if, ob_unis_add_len_if,
                ob_unis_decode_and_func, ob_unis_encode_array, ob_unis_decode_array, ob_unis_add_len_array,
                ob_unis_decode_array_and_func, ob_unis_encode_array_pointer, ob_unis_decode_array_pointer,
                ob_unis_add_len_array_pointer, base_ser, base_deser, base_add_len, serialize_size_header,
                check_version_length, lst_do_code};
pub use crate::sort::ob_sort;
pub use crate::pod::{PodCodec, PodField};
pub use crate::ob_bitfield;
// C 类型与 std
pub use core::ffi::{c_char, c_int, c_uint, c_long, c_ulong, c_longlong, c_ulonglong, c_double, CStr};
pub use std::sync::{Arc, OnceLock, LazyLock};
```

`Slot`、`ctype`、`num`、其余 `sort` 函数和 C 字符串辅助函数不进 prelude，显式 `use`（C++ 里有同名的嵌套类 `Slot`，如 `ob_ctx_allocator.h:319`、`ob_hnsw_embedmgr.h:150`）。

### 2.9 C++ 重载 → Rust 名

被替换的单元，Rust 名以本表为准，`overloads.tsv` 对这些单元从本表取名（§8 Q9）。

| C++ 重载集 | Rust |
|---|---|
| `ObString::compare/case_compare/compare_equal/case_compare_equal/prefix_match/prefix_match_ci/suffix_match_ci(const ObString&)` 与 `(const char*)` | 各合成一个，参数 `&[u8]` |
| `ObString::hash()` / `hash(uint64_t)` / `hash(uint64_t&, uint64_t)` | `hash(seed)`；第一个写 `hash(0)`；第三个归 05 的哈希接口 |
| `ObString::after(const char*)` / `after(char)`；`split_on` 同 | `after(off)` / `after_char(c)`；`split_on(off)` / `split_on_char(c)` |
| `ObString::assign_ptr`（4 个整数宽度的重载）、`assign`（2 个） | 视图直接赋值 `s = &p[..len]`；`ObStringWriter::with_length` |
| `ObSqlString::append` ×3、`assign` ×3 / `assign(const ObSqlString&)` | `append(&[u8])`、`assign(&[u8])` / `assign_sql_string` |
| `ObStringBuffer::append` ×3 | `append(&[u8], flag)`；`append(const char*)` 传 flag 0 |
| `ob_sub_str` ×2 | `ob_sub_str(…, end_index: Option<i32>, …)` |
| `serialization::decode_vstr` ×2 | `decode_vstr` / `decode_vstr_copy` |
| `serialization::encode/decode/encoded_length`（按类型的重载与模板） | 泛型函数 + trait；`T (&)[CNT]` → `encode_array`/`decode_array`/`encoded_length_array`；`char (&)[CNT]` → `*_cstr_array` |
| `encode_vstr(…, const char*)`、`encoded_length_vstr(const char*)`、`ob_write_string` 的 padding 重载 | 无调用者，不提供 |

### 2.10 本文引入的新名字（RULEBOOK §4 改名表之外，§8 Q9 与修订 A7）

| 名字 | 为什么需要 |
|---|---|
| `ObStringExt::{after_char, split_on_char, add_assign}`、`ObStringWriter::{with_buffer, with_length, ptr_mut, string, into_string}` | C++ 重载按参数类型改名；`operator+=`/`operator++` 没有方法名；writer 的构造与只读视图 |
| `ObSqlString::{assign_sql_string, ptr_mut}`、`ObStringBuffer::ptr_mut` | 重载改名；非 const 版本按 §2.13 加 `_mut` |
| `ob_strncasecmp`、`strlen`/`memmem`/`strcasestr`/`strtok_r`（C 名，切片版） | `ob_strcasecmp` 的 n 版本；C 字符串函数在切片上的等价物 |
| `c_strtol`/`c_strtoll`/`c_strtoul`/`c_strtoull`/`c_atof`/`c_atoi`/`c_atol`/`c_atoll`、`errno`/`set_errno` | 改名表"`c_strtod` 等"的其余成员；C 的 errno |
| `errc`、`from_chars_result`、`FastFloatValue` | `std::errc`、`fast_float::from_chars_result` 保留 C++ 名；后者是类型约束 |
| `CppFloatMap`、`CppFloat` | §8 Q2 |
| `c_fptosi_i8/u8/i16/u16`、`CDivRem` | 改名表 `c_fptosi_*` 的展开；`c_div`/`c_rem` 的类型约束 |
| `remainder`/`lround`/`lrint`/`frexp`/`ldexp`/`modf` | C 名 |
| `push_heap`/`pop_heap`/`make_heap`/`sort_heap`/`lower_bound`/`upper_bound`/`binary_search` | C++ 名 |
| `ObSerialize`、`ObDeserialize`、`ObUnisBody`（RULEBOOK §2.11 已用）；`ObDeserializeIn`、`decode_vstr_copy`、`encode_array` 一族、`encode_cstr_array` 一族、`unis_*`、`ob_serialize_enum!`、`ob_unis_*!`、`base_ser!` 等小写宏 | §2.11 点名的 `deserialize_in` 要一个 trait 才能组合；其余是重载改名或 C++ 宏的小写名 |
| `PodField`、`PodCodec`、`PodFieldLayout`、`ObPod`（derive）、`<名字>View`/`<名字>Mut`、`read_le`/`write_le` | 0.10 的"派生宏"和 `HeaderView` 模式 |
| `Slot::{bind, get, get_or, is_bound}` | 改名表的 `Slot` 的方法 |

## 3. 语义

每条给出 C++ 证据；与 C++ 不同的地方标 `FIXED(port)`（0.1.4）或指向 §8 的开放问题。

### 3.1 字符串

- **S-STR-1 表示**（RULEBOOK §2.4、§2.5）：只读的 `ObString` 是 `ObStr<'q>`/`&[u8]`（查询作用域）或 `Box<[u8]>`（拥有型），按 `types.tsv`；当写缓冲用的是 `ObStringWriter<'a>`。C++ 的 `const char *` 是不带 NUL 的 `&[u8]`。
- **S-STR-2 NULL 与空串**：`ObStringExt` 把 NULL 当空串。C++ 的 `empty()`、`hash`、`compare` 一类本来就不区分两者（`ob_string.h:105-108`、`:298`、`:373`）；区别只在两处：`case_compare`/`case_compare_equal` 在"一边 NULL、另一边非 NULL 的空串"时返回 −1/1/false（`:313-320`、`:342-347`）；显式判断 `OB_ISNULL(s.ptr())` 一类约 172 处（含别的类型的 `ptr()`），其中有语义的，例如 `src/share/ob_core_table_proxy.cpp:855` 用 NULL 生成 SQL 的 `NULL`、空串生成 `''`。推荐：显式判断 NULL 的成员和参数由 1c 第 4 类标成 `Option<…>`，判断译成 `is_none()`；其余位置的差别记为偏差（§8 Q1）。
- **S-STR-3 大小写比较**：`case_compare`、`case_compare_equal`、`prefix_match_ci`、`suffix_match_ci` 用 `strncasecmp`（`:322`、`:349`、`:447`、`:481`），它遇到 NUL 就停：`"a\0b"` 与 `"a\0c"` 在 C++ 里相等。Rust 用 `ob_strncasecmp`，同样在 NUL 处停。返回值是折叠成小写后的首个不同字节之差（macOS 与 glibc 都如此；Windows 的 `_strnicmp` 也按小写折叠，`CMakeLists.txt:203`）。`case_compare` 在前缀相等时返回 `data_length_ - obstr.data_length_`（`:350-352`）。
- **S-STR-4 字节比较**：`compare`、`compare_equal`、`prefix_match`、`suffix_match` 用 `memcmp`（`alloc_assist.h:77`），不在 NUL 处停；`ptr_ == obstr.ptr_` 的捷径（`:371`、`:397`、`:421`）在切片上结果相同。`compare` 的返回值：前缀相等时是长度差，否则是首个不同字节之差（u8 相减）；C 只保证符号，调用点也只用符号。`operator<` 等（`:508-566`）就是切片的 `Ord`（字典序加长度），与 `compare() < 0` 等价。
- **S-STR-5 长度**：`length()` 是 `i32`（`obstr_size_t`，`:44`）；超过 `i32::MAX` 的切片按 `as i32` 截断，与 C++ 构造函数里的 `static_cast<obstr_size_t>`（`:76`）一致。
- **S-STR-6 哈希**：空串返回 seed，否则 `murmurhash64A(bytes, len as i32, seed)`（`:297-301`、`murmur_hash.h:64-67`）。
- **S-STR-7 空白**：`trim`、`length_without_blank`、`ob_strip_space` 用 C locale 的 `isspace`，含 `\v`、`\f`（`:276`、`:574`、`:577`、`:870`）；`trim_space_only`、`trim_end_space_only` 只认 `' '`（`:591`、`:594`、`:608`）。
- **S-STR-8 `is_numeric`**：长度 ≥ 2 且首字节是 `'-'` 时才跳过符号；`"-"` 返回 false（`:775-783`）。
- **S-STR-9 改写视图的方法**：`split_on(Some(off))` 返回 `[..off]`、自身变成 `[off+1..]`；`None` 或越界时返回空、自身不变（`:713-722`）。`after(off)` 返回 `[off+1..]`，越界返回空（`:742-745`）。`clip(off)` 截到 `[..off]`（`:759-765`）。`add_assign(n)`：`n <= len` 时前移 n，否则变空（`:660-670`）；`n < 0` 时 C++ 把指针往前移、可能越界，Rust 变空，`FIXED(port)`。
- **S-STR-10 下标**：C++ `operator[]`（`:682`）不检查越界；Rust 的 `s[i]` 越界 abort，`FIXED(port)`。
- **S-STR-11 `to_string`**：`ObString::to_string`（`:628-642`）= `snprintf(buf, len, "%.*s", min(len, length), ptr)`，遇 NUL 即停，返回值夹到 `[0, len-1]`。`[u8]` 的 `ObToString` 由 04 的切片钩子实现（04 §2.4.4 第 625 行），06 不另写；`ObSqlString`（`ob_sql_string.cpp:171-181`）委托给它。`K(obstring)` 加引号的路径（`ob_print_utils.cpp:565-595`）归 04。
- **S-STR-12 `ObStringWriter`**：`write` 放不下时一个字节也不写、返回 0（`:140-146`）；`write_front` 先整体后移再写（`:162-167`）；`set_length(len)` 在 `len > buffer_size_` 时不改、返回当前长度（`:254-257`），C++ 接受负数，Rust 照存，但 `string()` 对负长度返回空切片，`FIXED(port)`（C++ 之后的拷贝会越界）；`remain()` 在 `buffer_size_ == 0` 时返回 0（`:266`）；`shrink()` 只在 `buffer_size_ > 0` 时把它改成 `data_length_`，返回改之前的 `remain()`（`:501-505`）。
- **S-STR-13 `ObString` 的序列化**：`serialize`：所需字节数大于剩余空间时返回 `OB_SIZE_OVERFLOW`（`ob_string.cpp:30-33`），否则 `encode_vstr`。零拷贝反序列化：剩余不足 2 字节返回 `OB_INVALID_ARGUMENT`（`:44-46`）；`decode_vstr` 失败返回 `OB_ERROR`（`:49-53`），此时 C++ 留下"指针为 NULL、长度是旧值"，Rust 把视图置空，`FIXED(port)`；成功时视图指向输入缓冲（`'de`）。拷贝分支（writer）：`str_len < 0`、放不下或剩余字节不够时返回 `OB_BUF_NOT_ENOUGH`（`:57-60`），拷贝失败返回 `OB_ERROR`（`:61-63`）。拥有型 `Box<[u8]>` 先零拷贝解码，再 `try_reserve` 拷贝，分配失败返回 `OB_ALLOCATE_MEMORY_FAILED`。
- **S-STR-14 `ObSqlString`**：
  - `reserve(size)`：需要 `size + 1`；从 `max(data_size_, 512)` 起翻倍，最多 64 次，仍不够返回 `OB_SIZE_OVERFLOW` 并记 ERROR（`ob_sql_string.cpp:278-300`）。翻倍用 `i64` 的 `<<`，溢出成负数后继续比较，与 C++ 相同。
  - `extend(size)`：分配恰好 `size` 字节（`try_reserve_exact` 后补零，PERF(port)：C++ 不清零），拷贝 `len_ + 1` 字节（含 NUL，`:302-320`）；分配失败返回 `OB_ALLOCATE_MEMORY_FAILED` 并记 ERROR。`size < len_ + 1` 时 C++ 越界写，Rust 返回 `OB_INVALID_ARGUMENT`，`FIXED(port)`。
  - `append`：写完补 NUL（`:66-69`）；空切片也会先 `reserve(len_)`，与 C++ 相同（C++ 只在 `str == NULL` 时跳过，Rust 的切片没有 NULL）。
  - `assign` = `reuse` + `append`（`:97-120`）；`assign_sql_string`：源从未分配过（`is_valid()` 为假）时返回 `OB_INVALID_ARGUMENT`，否则按 C 串拷贝，到源里第一个 NUL 为止（`:122-130`，`assign(sql.ptr())` 走 `strlen`）。
  - `set_length(len)`：`len > capacity()` 返回 `OB_BUF_NOT_ENOUGH` 并记 ERROR；已分配时写 NUL（`:153-169`）。`capacity()` 是 `data_size_ - 1`（`h:65`）。`reuse()` = `set_length(0)`（`:270-276`）。
  - `vappend`（`:183-268`）：先 `fmt::vsnprintf(&mut data[len_..], args)`（未分配时传空切片）；返回负数 → `OB_ERR_SYS`；返回值 ≥ 剩余空间 → `reserve(n + len_)` 后再格式化一次，第二次仍放不下 → `OB_ERR_UNEXPECTED`；否则 `len_ += n`。`ObFmtArgs` 可以格式化两次、结果相同（04）。Windows 分支（`:192-236`）把 `%ld` 等改写成 64 位、去掉 `'` 标志，由 04 的 Ob 方言负责（04 §2.4.1）。
- **S-STR-15 `ObStringBuffer`**：
  - 分配器为 `None` 时 `append`/`reserve`/`extend` 返回 `OB_ERR_NULL_VALUE`（`ob_string_buffer.cpp:71-72`、`:102-103`、`:124-125`）。
  - `append(s, flag)`：`flag == -1` 时 `reserve(len_ + len)`，即需要 `2*len_ + len + 8`；否则 `reserve(len)`（`:79-84`）；`len_ + len` 回绕时返回 `OB_SIZE_OVERFLOW`（`:80-81`）；写完补 NUL。
  - `reserve(len)`：需要 `len_ + len + 8`；从 `cap_`（为 0 时 512）起翻倍，最多 64 次（`:98-118`）。`extend(len)`：`len > i64::MAX` 返回 `OB_INVALID_ARGUMENT`；用 01 的 `arena.alloc(len)`，失败 `OB_ALLOCATE_MEMORY_FAILED` 并记 ERROR；拷贝 `len_ + 1` 字节；旧块留在 arena 里（C++ 的 `ObArenaAllocator::free` 也不释放）。
  - `set_length(len)`：`len > cap_` 返回 `OB_BUF_NOT_ENOUGH`；`len == cap_` 时 C++ 在 `data_[cap_]` 写 NUL、越界一个字节（`:150-156`），Rust 不写，`FIXED(port)`。
  - `reuse()` 只把 `len_` 置 0，不清内容（`:47-50`）；`reset()` 清空并忘掉缓冲（`:36-45`）；`get_result_string` 交出 `[0, len_)` 后自身清空（`:52-61`），C++ 交出的 `ObString` 还带着 `cap_` 的写容量，接收方写这段余量的用法要改用 `ObStringWriter`（未发现这样的调用）。
  - `back()` 在 `len_ == 0` 时 C++ 读 `data_[-1]`，Rust 返回 0，`FIXED(port)`。
- **S-STR-16 C 字符串辅助函数**：`ob_strcasecmp`/`ob_strncasecmp` 见 S-STR-3。`strtok_r` 不往缓冲里写 NUL：C++ 之后若把原缓冲当 C 串读会看到 NUL，Rust 看不到；这样的读法由 1c 第 18 类逐处核对。`memmem`、`strcasestr` 对空 needle 返回 0。

### 3.2 ctype

- **S-CTYPE-1 locale**：main 先 `setlocale(LC_ALL, "")`，再把 `LC_CTYPE` 设成 `"C"`、`LC_TIME` 和 `LC_NUMERIC` 设成 `"en_US.UTF-8"`（`src/observer/main.cpp:693-698`）。所以 ctype 一律是 C locale：只有 0..=127 被分类，128..=255 和负数不属于任何类，`tolower`/`toupper` 原样返回它们（macOS 的 `___tolower` 与 glibc 的表都这样）。03 必须在 `main` 的开头照抄这四句（RULEBOOK 0.5"其余照 C++ 的 main"）：`LC_NUMERIC` 影响 `c_strtod` 的小数点（S-NUM-1）。
- **S-CTYPE-2 `isspace`** 包含 `\v`（0x0B）和 `\f`（0x0C）；Rust 的 `u8::is_ascii_whitespace` 不含 `\v`，禁止代用（RULEBOOK §2.5）。
- **S-CTYPE-3 实参**：C++ 把 `char` 提升成 `int` 传入，0x80 以上在 macOS 和 x86 上是负数；C locale 下负数与 128..=255 的结果相同（都不属于任何类、都原样返回），所以统一写 `b as c_int`。`tolower(b as c_int) as u8` 等于 C++ 的 `static_cast<char>(tolower(c))`。
- **S-CTYPE-4 返回值**：C 返回非零的 `int`（glibc 返回掩码位，如 `isspace` 是 8192；macOS 返回 1）；Rust 返回 `bool`。调用点都只在布尔上下文或与 0 比较里用它（`utility.cpp:450`、`:780` 的 `isprint(c) == 0`/`!= 0` 写成 `!isprint(..)`/`isprint(..)`）。
- **S-CTYPE-5 先读后判边界**：`for (; !isdigit(*pos) && pos < end; ++pos)`（`ob_time_convert.cpp:878`）在 `pos == end` 时多读一个字节；Rust 先判边界，`FIXED(port)`。

### 3.3 数值

- **S-NUM-1 `c_strto*`/`c_ato*`**（RULEBOOK §2.5）：
  - 调用平台 C 库（`libc::strtod` 等），返回值、结束位置、`errno` 与 C 相同：上溢 ±HUGE_VAL 加 `ERANGE`，接受十六进制浮点、`inf`、`nan`，跳过前导空白；平台差异照留，例如 macOS 的 `strtol` 在没有任何数字时置 `EINVAL`（FreeBSD 的 `noconv` 分支），glibc 不置；`ob_strtoll`（`utility.cpp:2082-2097`）因此在两个平台上对 `"abc"` 的结果不同，这本来就是 C++ 的行为。
  - 输入：C 会扫描的字节。切片里有 NUL 时直接把切片交给 C 库（不拷贝，C 在 NUL 处停）；没有 NUL 时拷进 512 字节的栈缓冲并补 NUL，更长的拷到堆上（不可失败的分配，§8 Q5）。拷贝之前保存 `errno`、调用前恢复，所以 C++ 的 `errno = 0; v = strtoll(...); if (ERANGE == errno)` 译成 `set_errno(0); v = c_strtoll(...); if libc::ERANGE == errno()` 仍然成立。
  - C++ 把不以 NUL 结尾的 `ObString` 交给 `strtoll`（如 `ob_get_diagnostics_executor.cpp:305`、`ob_json_schema.cpp:2381`、`ob_core_table_proxy.cpp:82`）时会越过逻辑长度继续读；Rust 只扫给定的切片，`FIXED(port)`（越界读）。
  - `LC_NUMERIC` 是 `en_US.UTF-8`（S-CTYPE-1），小数点是 `'.'`；系统缺这个 locale 时 C++ 退回环境变量给的 locale，Rust 照抄 `setlocale` 之后行为相同。
  - `atoi` 等直接调 C 库的同名函数（macOS 与 glibc 都实现为 `(int)strtol(…, 10)`），溢出时的截断结果相同。
- **S-NUM-2 `fast_float_from_chars`**：复刻 fast_float 6.1.3 的契约（seekdb 没有定义任何 `FASTFLOAT_*` 宏，`rg FASTFLOAT_ src cmake CMakeLists.txt` 为 0）：
  - 浮点语法（`ascii_number.h:257-420`）：可选 `'-'`（不接受 `'+'`，`:265-268`）；`'-'` 后必须是数字或小数点；整数部分和小数部分合计至少一位数字；指数 `e`/`E` 加可选符号加数字，`e` 后没有数字时忽略这个 `e`（`:351-357`）；不认十六进制；不跳过空白（`parse_number.h:273-277`）。
  - 失败：输入为空（`parse_number.h:278-282`）或语法不成立且不是 inf/nan 时返回 `invalid_argument`、`ptr = 0`，`value` 不动。
  - inf/nan（`parse_number.h:22-67`）：不分大小写的 `nan`、`inf`、`infinity`（要求 8 个字符全对，否则只吃 3 个），前面可有 `'-'`；`nan(` 后跟 `[A-Za-z0-9_]*)` 时一并吃掉；`-nan` 的结果带符号位（位模式 `0xFFF8000000000000`）。
  - 数值：把语法匹配到的 ASCII 子串交给 `core` 的 `str::parse::<f64/f32>()`，它与 fast_float 一样正确舍入（就近取偶）。上溢（结果为 ±inf）或下溢（结果为 0 而尾数里有非零数字）时返回 `result_out_of_range`，`ptr` 指向数字末尾，`value` 照 fast_float 写成 ±inf 或 ±0（`parse_number.h:256-260`，先 `to_float` 后判断）。
  - 整数（`ascii_number.h:427-524`）：无符号类型遇 `'-'` 返回 `invalid_argument`；跳过前导零，全是零时得 0；没有数字时 `invalid_argument`、`ptr = 0`；超出 u64 或超出目标类型时返回 `result_out_of_range`，`value` 不动，`ptr` 指向数字末尾。只支持 10 进制（4 处调用都是）。
  - `ob_dtoa.cc:370-380` 的调用方在拿到 NaN/inf 后自己把结果改成 0、`end` 退回开头，那是被翻译的代码的逻辑，不在本函数里。
- **S-NUM-3 浮点键**：
  - C++ 的 `std::map<double, uint32_t>::operator[]` 走 `__emplace_unique_key_args` → `__find_equal`（`$LIBCXX/__tree:1673-1703`、`:1771-1785`，`map:1510-1515`）：从根开始，`k < 节点` 往左、`节点 < k` 往右，否则视为相等。−0.0 与 +0.0 互不小于，所以是同一个键，保留先插入的那个、值被后来的覆盖。NaN 与任何值都"互不小于"，于是 `m[NaN]` 落在**当时的根**上：表空时 NaN 成为根，之后所有键都落在它上面；表非空时 NaN 不会被插入，而是覆盖根节点的值。根是谁取决于红黑树的插入调整（`__tree_balance_after_insert`，`:279-322`），所以这个结果不是比较关系的性质，任何 `Ord` 包装加 `BTreeMap` 都复刻不了。
  - NaN 能到达这两处：数组的文本解析用 fast_float（`ob_array_cast.cpp:989`），它接受 `nan`，没有像 `ob_fast_strtod` 那样把 NaN 改成 0。
  - **推荐**（§8 Q2）：两处改用 `CppFloatMap`，逐行移植 `__find_equal`、`__insert_node_at`（`:1756-1768`）、`__tree_balance_after_insert`、`__tree_left_rotate`/`__tree_right_rotate`（`:236-273`）和中序遍历（`__tree_min`、`__tree_next`，`:164-192`），节点用下标；比较直接用 `PartialOrd::lt`，即 C++ 的 `<`。这样对任何键序列都与基线相同。`CppFloatKey` 仍按 RULEBOOK 提供：非 NaN 时与 C++ 的等价类一致。
  - **怎么测基线**：C++ 程序 `06_float_map.cpp`（§5 T-NUM-4）用基线工具链编译，对一组键序列逐个执行 `m[k] = i`，按迭代序输出 `(键的位模式, 值)`。序列覆盖：NaN 在首位、中间、末位；多个 NaN、`-NaN`；±0 的先后；NaN 与 ±0 混合；随机长度 1–64、每个键从 {有限值池、±0、±inf、NaN、−NaN} 里抽的 2,000 条；`float` 和 `double` 各一份。Rust 用同样的序列驱动 `CppFloatMap`，要求全部相同；若人选了备选方案，只要求不含 NaN 的序列相同，含 NaN 的差异进 A.4。
- **S-NUM-4 浮点转窄整型**（RULEBOOK §2.12、复核 C20）：越界时在 C++ 里是未定义行为。基线 arm64 的代码生成是 `fcvtzs w0, d0`（饱和到 i32，NaN 得 0），存进 8 位或 16 位变量时截断：300.0 → 44（u8），−5.0 → 251（u8），1e10 → −1（i8）。`c_fptosi_*` 一律实现为 `(x as i32) as T`，所有平台相同，`FIXED(port)`（x86 上的 C++ 结果不同，同样是未定义行为）。转 32 位和 64 位整型时 Rust 的 `as`（饱和，NaN → 0）与 arm64 的 `fcvtzs`/`fcvtzu` 相同，直接写 `as`，例如 `static_cast<int64_t>(rint(in))`（`ob_obj_cast.cpp:1953`）→ `in.round_ties_even() as i64`。
- **S-NUM-5 `c_div`/`c_rem`**（RULEBOOK §2.12、行为审查 M4）：`cfg(target_arch = "aarch64")`（macOS arm64、Linux aarch64、Android）照硬件：除以 0 得 0、余数等于被除数；`MIN / -1` 得 `MIN`、余数 0。其余目标（x86-64）在这两种情况下记 ERROR 后 `std::process::abort()`：C++ 在那里触发 SIGFPE，进入崩溃处理（`SIG_SET` 含 `SIGFPE` 与 `SIGABRT`，`ob_signal_handlers.cpp:45`），信号号不同。其余情况就是 `/`、`%`（都向零截断，与 C 相同）。
- **S-NUM-6 C 数学库映射**（RULEBOOK §2.12；基线不改舍入模式，`rg fesetround` 为 0）：

  | C/C++ | Rust | 说明 |
  |---|---|---|
  | `rint`/`nearbyint`（15） | `round_ties_even` | 不得用 `round`：`ROUND(2.5e0)` 基线是 2（`ob_expr_util.cpp:392-405`） |
  | `round`（26）/`trunc`/`floor`/`ceil`/`floorf`/`ceilf` | 同名方法 | `round` 是远离零 |
  | `fabs`（69） | `abs` | |
  | `sqrt`（40） | `sqrt` | 都正确舍入 |
  | `pow`（42） | `powf` | 走系统 libm 的 `pow`；禁用 `powi`（§3 lint） |
  | `fmod`（2） | `%` | LLVM 的 `frem` 调 `fmod`，本来就精确 |
  | `log`（约 50） | **`ln`** | Rust 的 `f64::log(self, base)` 是另一个函数 |
  | `log2`/`log10`/`exp`/`sin`/`cos`/`tan`/`asin`/`acos`/`atan`/`sinh`/`cosh`/`tanh` | 同名方法 | 都调系统 libm |
  | `atan2(y, x)` | `y.atan2(x)` | |
  | `remainder`（8，GIS）、`lround`（6）、`lrint`（1）、`frexp`/`ldexp`/`modf`（0 处，RULEBOOK 点名） | `oblib::num` 的同名函数 | libc crate 0.2.190 在 unix/windows 下都没有声明它们（§8 Q3） |
  | `fma`、`a*b+c` | 普通运算 | D17 选 (b)，不用 `mul_add` |
- **S-NUM-7 回绕**（RULEBOOK §2.12）：哈希函数里有意依赖回绕的运算一律写 `wrapping_*`，不依赖 overflow-checks 的设置，debug 构建也不会 panic。例：`fnv_hash2`（`murmur_hash.cpp:51-66`）是 `i32` 运算：`hash = (hash ^ (data[i] as c_char as i32)).wrapping_mul(16777619)`（`char` 按平台符号扩展，复核 C19），之后 `hash.wrapping_add(hash << 13)`（`gnu++20` 下负数左移有定义），`hash ^= hash >> 7`（算术右移）；`murmurhash64A` 的 `seed ^ (len * multiply)` 写成 `seed ^ (len as i64 as u64).wrapping_mul(M)`，8 字节块用 `u64::from_le_bytes`（6 个目标平台都是小端，C++ 是本机序的非对齐读）。

### 3.4 排序与堆

- **S-SORT-1 `ob_sort` 的算法**：逐行移植 `__introsort<_ClassicAlgPolicy, _Comp&, _Iter, false>`（`$LIBCXX/__algorithm/sort.h:756-869`）：
  - 深度上限 `2 * __log2i(len)`（`:871-888`、`:923`），`len == 0` 时为 0；
  - 长度 0–5 用 `__sort3`/`__sort4`/`__sort5`（`:49-128`、`:771-795`）；长度 < 24 时最左段用 `__insertion_sort`（`:282-305`），其余用 `__insertion_sort_unguarded`（`:312-338`，依赖左边相邻元素不大于本段）；
  - 深度用尽时退化为堆排序 `__partial_sort(first, last, last)`（`:805-809`，`partial_sort.h:35-69`）；
  - 选枢轴：长度 > 128 用 Tukey 九数取中（4 次 `__sort3` 加一次交换，`:815-823`），否则 `__sort3(first + half, first, last - 1)`（`:825`）；
  - 非最左段且左邻不小于枢轴时用 `__partition_with_equals_on_left`（`:836-840`、`:693-747`），否则 `__partition_with_equals_on_right`（`:625-689`）；
  - 分区前已有序时对两侧各做一次最多移动 8 个元素的 `__insertion_sort_incomplete`（`:340-392`、`:849-862`）；
  - 左段递归、右段循环（`:864-867`）。
  - 实现以下标在整个切片上运算（不切子切片），因为无守卫插入排序要访问 `first - 1`。
- **S-SORT-2 比较器调用序列相同**：每一次 `comp` 调用的实参和先后次序都与 C++ 相同，所以在比较器里记录错误的写法（`ob_sort_op_impl.cpp` 的 `comp_.ret_`）照样成立。C++ 把枢轴或待插入的元素移出、留下"空洞"；在严格弱序下空洞不会被读到（`sort.h:636-638`、`:716-718` 的注释："median-of-three 保证有守卫"），Rust 让它们留在原位，最后一次交换落位，读到的值相同。比较器不得依赖元素自身的地址。
- **S-SORT-3 不移植的路径**：
  - 位集分区和无分支的 `__sort3/4/5` 只在"连续迭代器、元素是不大于指针的算术类型、比较器是 `__less<>`/`less<T>`/`greater<T>`"时启用（`sort.h:146-150`、`:928-931`）；
  - 算术类型用默认比较器且迭代器是裸指针时，`std::sort` 转去调用系统 `libc++.1.dylib` 里编译好的 `std::__sort<__less<T>&, T*>`（`:890-917`、`:958-984`），那份代码的版本取决于运行时的系统，不是 SDK。
  - 代码里可能走到这两条路的只有对 `uint64_t` 排序的两处（`src/rootserver/ob_ddl_service.cpp:1236`、`src/observer/schema/ob_schema_service_sql_impl.cpp:382`），整数排序的结果对任何正确的算法都唯一，所以照样译成 `ob_sort(v, |a, b| a < b)` / `ob_sort(v, |a, b| a > b)`。`lib::ob_sort(first, last)` 用的是自定义的 `Compare` 结构（`ob_sort.h:47-54`），不是 `__less`，总走通用路径。将来若出现对 `float`/`double` 用默认比较器的 `std::sort`，±0.0 与 NaN 的先后可能不同，审查清单加一条。
- **S-SORT-4 不满足严格弱序的比较器**：基线的 libc++ 加固模式是 NONE（`$LIBCXX/__config_site:42` 定义 `_LIBCPP_HARDENING_MODE_DEFAULT 2`，`__config:136-145`），`_LIBCPP_ASSERT_VALID_ELEMENT_ACCESS` 全是空的，无守卫的循环（`sort.h:331-334`、`:639-644`、`:652-657`、`:669-680`、`:705-710`、`:719-739`）在比较器出错时会越界读写。Rust 在这些循环里加切片边界的守卫：对严格弱序没有影响，否则结果是某个排列而不是越界，`FIXED(port)`。
- **S-SORT-5 errsim 检查**：`ob_sort` 的 `EN_CHECK_SORT_CMP`（`ob_sort.h:30-39`）在 `cfg(feature = "errsim")` 下照写：`ob_e!(EN_CHECK_SORT_CMP)` 失败且 `size_of::<F>() == 0`（对应 `std::is_empty<Compare>`）时检查 `comp(x, x)` 为假，否则记 ERROR。
- **S-SORT-6 堆**：`push_heap` = `__sift_up`（`push_heap.h:30-56`）；`pop_heap` = `__floyd_sift_down` 再 `__sift_up`（`pop_heap.h:33-58`、`sift_down.h:82-112`），长度 ≤ 1 时什么也不做；`make_heap` 从 `(n-2)/2` 到 0 逐个 `__sift_down`（`make_heap.h:29-42`、`sift_down.h:27-80`）；`sort_heap` 重复 `__pop_heap`（`sort_heap.h:32-42`）。两个子节点相等时选左子（`sift_down.h:47`、`:71`、`:98`）。翻译代码禁用 `BinaryHeap`（RULEBOOK §3 lint）。
- **S-SORT-7 二分查找**：`lower_bound`/`upper_bound` 是对分循环（`lower_bound.h:30-48`、`upper_bound.h:30-45`，`__half_positive` 是无符号除以 2），`binary_search` = `lower_bound` 后再判 `!comp(value, *it)`（`binary_search.h:24-29`）。区间满足前提时结果与 `slice::partition_point` 相同；照移植是为了在比较器有副作用、或区间不满足前提时也与 C++ 相同。
- **S-SORT-8 `stable_sort`**：译成 `v.sort_by(|a, b| if less(a, b) { Less } else if less(b, a) { Greater } else { Equal })`。对严格弱序，稳定排序的结果唯一。临时缓冲不可失败（C++ 拿不到缓冲时退化为原地归并），见 §8 Q5。
- **S-SORT-9 平台**：Linux 上的 C++ 用 libstdc++（`CMakeLists.txt:221-223`），Android 用 NDK 的 libc++；Rust 在所有平台上都用基线 macOS 这一份算法（D5，前提见 RULEBOOK A.3）。

### 3.5 序列化

- **S-SER-1 字节格式**（`serialization.h`）：
  - 定长整数大端（`:125-243`）；`bool` 写 1 字节 0/1，读时非 0 即真（`:251-269`）。
  - 变长整数：每字节低 7 位，从低位起，最高位是续位；`int64` 负数占 10 字节（`:271-323`）；`vi32` 先转 `uint32`，最多 5 字节（`:371-416`）。`float`/`double` 是位模式的 `vi32`/`vi64`（`:444-490`）。
  - `encode_fixed_bytes_i64` 恒写 5 字节：前 4 字节带续位，第 5 字节是 `(v >> 28) & 0x7f`，`2^35` 以上的位丢掉（`:326-343`）；用普通的 `decode_vi64` 就能读回。
  - vstr = `vi64(len)` + `len` 字节 + 一个 NUL（`:499-552`）；解码返回指向输入的指针，`pos` 跳过 NUL（`:569-578`），即使末尾实际没有 NUL 也照跳，`pos` 可能比 `data_len` 大 1；后续解码先算 `data_len - pos`（`i64`，可以为负）再判断，不在判断前切片。
  - `number_type` = 大端 `i32` 的 `desc_` 加 `len_` 个本机序 `u32`（`:1191-1249`）；编码时空间不够返回 `OB_BUF_NOT_ENOUGH`，解码时 `OB_DESERIALIZE_ERROR`，`len_ == 0` 时 digits 为空。
  - 错误码：编码一律 `OB_SIZE_OVERFLOW`，解码一律 `OB_DESERIALIZE_ERROR`，例外照各函数（`encode_number_type`、`char[]` 编码返回 `OB_BUF_NOT_ENOUGH`，`:1767`）。
- **S-SER-2 按类型分派**（`:1691-2014`）：

  | Rust 类型（C++） | 格式 | 解码失败时 |
  |---|---|---|
  | `i8`、`u8`（`int8_t`、`uint8_t`、`char`） | 1 字节 | 先置 0（`:1806`、`:1811`、`:1816`） |
  | `bool` | 1 字节 | 先置 false |
  | `i16`、`u16` | `vi32`（`i16` 符号扩展、`u16` 零扩展）；解码后截断 | 0 |
  | `i32`、`u32` | `vi32` | 0 |
  | `i64`、`u64`、`isize`、`usize`（`size_t`） | `vi64` | 0 |
  | `f32`、`f64` | 位模式的 `vi32`/`vi64` | 0.0 |
  | 枚举新类型（`ob_serialize_enum!`） | `vi32(self.0)`；解码不校验取值（`:1656-1664`） | 0（C++ 在解码后无条件赋值） |
  | `(A, B)` | 先 A 后 B | |
  | `[u8]`、`&[u8]`、`Box<[u8]>`（`ObString`） | vstr | S-STR-13 |
  | 类类型 | 调它的 `serialize`/`deserialize`/`get_serialize_size`（`:1672-1689`），不预先清零 | 不动 |
- **S-SER-3 unis 头**（`ob_unify_serialize.h:165-216`）：
  - 编码：`vi64(UNIS_VERSION)`，然后**不检查空间**地预留 5 字节（`:171-172`），编码成员体，成功后在预留处写 `encode_fixed_bytes_i64(成员体长度)`（`:182-183`）。成员体失败时 `pos` 停在已写到的位置、不回退，照抄。
  - 解码：`pos < data_len` 时才解码 `version`、`len`（缺失时都是 0）；`version != UNIS_VERSION` → `OB_NOT_SUPPORTED`；`len < 0` → `OB_ERR_UNEXPECTED`；`data_len < len + pos` → `OB_DESERIALIZE_ERROR`（`:143-155`）；然后在 `[pos, pos + len)` 这个子缓冲上从 0 开始解码成员体，结束后无论成败都把 `pos` 设为 `pos_orig + len`（`:199-206`）：新版本多出的尾部被跳过，旧数据缺的尾部成员不解码、保留构造时的值（每个成员先判 `pos < data_len`，`:60-66`）。
  - 长度：`get_serialize_size_() + encoded_length(UNIS_VERSION) + 5`（`:210-216`）。
  - `CHECK_SERIALIZE_SIZE`（`:157-163`）只在 debug 构建里断言"预估 ≥ 实际"：`#[cfg(debug_assertions)]` 的 `assert!`（NDEBUG → `cfg(debug_assertions)` 取反，0.15）。
- **S-SER-4 成员宏的变体**：
  - `OB_SERIALIZE_MEMBER((C, P), …)`：成员体先调父类**完整的** `serialize`（含父类自己的 unis 头），用限定调用、不走虚分派（`:89-97`），Rust 是 `<P as ObSerialize>::serialize(&self.base, …)`。
  - `_INHERIT`：没有本类的头，`serialize` = 父类 `serialize` + 成员（`:117-120`），成员解码时的 `pos < data_len` 对的是整个外层缓冲。
  - `_SIMPLE` = 父类为空的 `_INHERIT`（`:137`）。
  - `_IF`：谓词只作用于编码和长度，解码总是解所有成员（`:450-456`），照抄这个不对称。
  - `_TEMP`/`_TEMP_INHERIT`：模板版本（`:249-274`）。
  - `OB_UNIS_DECODE_ARRAY` 不解码个数，`OB_UNIS_DECODE_ARRAY_AND_FUNC` 解码个数且与 `objs_count` 不等时返回 `OB_ERR_UNEXPECTED`（`:289-334`）；`_POINTER` 版本见 `:336-423`。
  - 宏里的 `RPC_WARN` 日志（`:55-57` 等）照写 `log_warn!`，`"name"` 的值是成员表达式字符串化的结果（Rust 里是 `self.a_`，C++ 是 `a_`，日志不参与比较）。
- **S-SER-5 `FIXED(port)` 一览**（照样复刻会越界或是未定义行为；有效输入上结果不变）：
  - `decode_vi64`/`decode_vi32` 先读 `*(buf + tmp_pos)` 再判边界（`:351`、`:424`），`tmp_pos == data_len` 时越界读一个字节；Rust 先判边界，返回同一个 `OB_DESERIALIZE_ERROR`。
  - 续位过多时移位量超过位宽（`:356`、`:429`，未定义行为）；Rust 用 `wrapping_shl`（按位宽取模），与 arm64、x86 的移位指令结果相同。
  - unis 编码在剩余空间不足 5 字节、而成员体为空时，C++ 在 `buf + pos_bak - 5` 处越界写长度（`:172`、`:182-183`）；Rust 返回 `OB_SIZE_OVERFLOW`（成员体非空时 C++ 的第一个成员本来就返回它）。
  - `len + pos` 溢出（`:151`）用 `checked_add`，溢出按 `OB_DESERIALIZE_ERROR`。
  - `pos < 0`：C++ 会越界读写；Rust 编码返回 `OB_SIZE_OVERFLOW`、解码返回 `OB_DESERIALIZE_ERROR`。
  - `encode_vstr` 的空间检查把长度转成 `int32_t`（`:537`），`len ≥ 2^31` 时检查失效；Rust 用 `i64` 比较。
  - `char[]` 解码时负的 `rsize` 会让 `MEMCPY` 的长度变成巨大的正数（`:1888`）；Rust 返回 `OB_DESERIALIZE_ERROR`。`char[]` 编码时数组里没有 NUL，C++ 的 `strlen` 越界（`:1759`）；Rust 按"NUL 紧跟在数组之后"编码（长度 N+1，补一个 0 字节）。
- **S-SER-6 空缓冲指针**：C++ 的 `NULL == buf` 检查（如 `:127`、`ob_string.cpp:30`、`ob_serialization_helper.h:35`）在 Rust 里不可达（切片没有 NULL），一律删去，链的其余部分原样保留。C++ 没有以空指针调用 `serialize`/`deserialize` 的地方（`rg 'serialize\(\s*(NULL|nullptr)\s*,'` 为 0）；`buf_len <= 0` 一类检查照留（`log_entry_header.cpp:228`）。
- **S-SER-7 不翻译的序列化**：计划和表达式类（`ObOpSpec`、`ObExprOperator` 及其派生类）上的序列化宏按 RULEBOOK 0.6"不再做计划和表达式的字节序列化"不译，原位置写注释；DTL/PX 消息等进程内格式照译（自洽即可，但照样按字节复刻）。
- **S-SER-8 C 数组**：C++ 里 `char x[N]` 与 `uint8_t x[N]` 是不同的类型，走不同的重载（`:1754-1771` 与 `:1773-1785`），在 Rust 里都是 `[u8; N]`。所以数组不实现 `ObSerialize`：成员宏里 `[u8; N]` 默认按 `char[]`（约 200 个 `char` 数组成员，对 12 个 `uint8_t`/`unsigned char` 数组成员）走 `*_cstr_array`，`uint8_t` 数组和其他元素类型的数组要写 `@array` 走 `*_array`；手写代码直接调这两组函数。写错时编译不过（没有 `ObSerialize for [T; N]`），不会悄悄换格式。
- **S-SER-9 虚分派**：带 `OB_UNIS_VERSION_V`（约 276 处）的类层次，`<类名>Virt` trait 以 `ObSerialize` 和 `for<'x> ObDeserialize<'x>` 为超 trait（含借用字段的层次带上 `'de`，由 `types.tsv` 给出），`OB_UNIS_VERSION_PV`（5 处）的纯虚基类不生成实现。
- **S-SER-10 缺定义的函数体**：C++ 声明了 `deserialize_` 但从未定义（只编码、从不解码）的类，`ObUnisBody::deserialize_` 写成返回 `Err(OB_NOT_SUPPORTED)`，标 `TODO(port)`。

### 3.6 POD 与位域

- **S-POD-1 编解码**：落盘头一律逐字段小端编解码（RULEBOOK 0.10），6 个目标平台都是小端，所以就是 C++ 内存里的字节；没有对齐要求，`buf + pos` 可以是任意偏移（C++ 的 `reinterpret_cast` 读写在这里是未对齐访问）。`#[repr(C)]` 只出现在文档和测试里。
- **S-POD-2 位域布局**：`ob_bitfield!` 不推算布局，每个位段的位置显式写出。写法所依据的规则（Clang/GCC 在小端上的 SysV/Itanium 规则，RULEBOOK §2.12）：同一存储单元里从最低位起依次分配；放不进声明类型的当前对齐单元时从下一个单元开始；不同声明类型的相邻位段可以共用存储；packed 结构里可以跨单元；零宽位段强制对齐。Windows 上 C++（MS ABI）对不同声明类型的相邻位段不共用存储，布局不同；Rust 在所有平台上都按 Clang/GCC 的布局（RULEBOOK §2.12），Windows 的对齐在第 6 步之后（D18）。每个落盘的位域结构配一条布局测试（§5 T-POD-2）。
- **S-POD-3 读出的类型**：C++ 读位段时有整型提升：宽度 < 32 的位段（不论声明成什么整型）提升为 `int`，宽度 = 32 的提升为 `int` 或 `unsigned int`，更宽的保持声明类型（C++20 [conv.prom]/5，Clang 的 `isPromotableBitField`）。例如 `uint32_t len_ : 29` 为 0 时 `desc.len_ - 1` 是 −1。所以生成的读函数返回提升后的类型（`i32`、`u32` 或声明类型），算术语义自动与 C++ 一致；赋给更宽或无符号的变量时编译器会要求显式 `as`。声明类型是 `bool` 的读出 `bool`，是枚举的读出枚举新类型；有符号位段做符号扩展。写函数收声明类型，按位宽取模截断（有符号位段按补码截断）。
- **S-POD-4 联合体**：同一位置的多个标量名字（`ob_micro_block_header.h:65-69` 的 `row_index_offset_`/`row_data_offset_`/`row_offset_`）是一个字段加 `alias`；位域结构与整数的联合体（`ob_datum.h:141-149`、`ob_time_def.h:40-48`）是一个 `ob_bitfield!` 类型，整数成员名就是存储字段名；指针与标量的联合体（`ob_micro_block_header.h:75-78`）存原始的 8 字节，指针的含义改成相对缓冲区的偏移加访问函数（RULEBOOK 0.10），例如列校验和的位置是 `COLUMN_CHECKSUM_PTR_OFFSET`（64，`ob_micro_block_header.cpp:24`）。
- **S-POD-5 填充字节与校验和**：`write_to` 把不属于任何字段的字节写 0（C++ 的结构赋值会带上不确定的填充字节）。读到的头在验证校验和时，一律对缓冲区里的原始字节计算，不得对重新编码的结构计算，否则 C++ 写下的填充字节会让校验失败。
- **S-POD-6 `bool` 字段**：盘上不是 0/1 的字节，在 C++ 里读成 `bool` 是未定义行为；Rust 按 `!= 0` 读，`FIXED(port)`。
- **S-POD-7 只在内存里的 packed 结构**：能改成普通 Rust 结构（大小变化不可见）的就改；大小可见的（如 `ObDatum` 的 12 字节，07 负责）用 `#[repr(C, packed)]` 加按值读写，不取字段引用、不 derive 需要字段引用的 trait（RULEBOOK 0.10）。
- **S-POD-8 头加尾随数据**：`#[derive(ObPod)]` 生成的 `<名字>View<'a>`/`<名字>Mut<'a>` 就是 RULEBOOK 0.9 的 `StoredRowView<'a>(&'a [u8])`/`StoredRowMut<'a>(&'a mut [u8])` 模式，`payload()` 是头之后的字节；只有 `storage_core` 允许原始指针形式（0.9）。

### 3.7 槽位

- **S-SLOT-1 绑定**：C++ 的 `bind_server_service` 是无条件覆盖（`ob_server_runtime.h:132-137`）；Rust 只能绑定一次，第二次返回 `OB_INIT_TWICE` 并记 ERROR。组合根（`generated::bind_all()`）对每个槽位只绑一次，所以正常路径没有差别。
- **S-SLOT-2 读取**：`get()` 返回 `Option`，保留 C++ 的 `OB_ISNULL`/`OB_NOT_NULL` 分支（`ob_datum_funcs_impl.h:306-307`）；转发函数用 `get_or(OB_NOT_INIT)` 一类，错误码取自 C++ 空指针分支（RULEBOOK 0.4），由 `seams.tsv` 逐个给出。
- **S-SLOT-3 内存序**：`OnceLock` 的写是 Release、读是 Acquire，绑定之前对被绑对象的初始化对读者可见。C++ 的静态指针是普通读写，靠"先构造、再绑定、再启动线程"的顺序（0.5）。
- **S-SLOT-4 解绑**：`unbind_server_service` 不译（RULEBOOK 0.5：不支持解绑和进程内重启）。

### 3.8 prelude

- **S-PRE-1**：prelude 只做 `pub use`，不定义任何东西；glob 导入的优先级低于单元里的显式 `use` 和本地定义，所以与翻译出来的同名项不冲突。两个 glob 同时提供同名项时，使用处会报歧义，这时按 `symbols.tsv` 写完整路径（RULEBOOK §4）。

## 4. unsafe

只在 `rust/port/oblib/src/num/**`（RULEBOOK §3 白名单第 7 项"调用 C 库的数值解析"）。`string`、`ctype`、`sort`、`serialization`、`pod`、`slot`、`prelude` 不含 unsafe，也不加 `#[allow(unsafe_code)]`；`ob_macros` 为 `ob_bitfield!`/`derive(ObPod)` 生成的代码不含 unsafe（使用方 `storage` 是 `forbid(unsafe_code)`）。白名单第 7 项里的 `pod/**` 本设计用不上，留空。

| # | 位置 | 为什么需要 | 安全论证要点 |
|---|---|---|---|
| U1 | `c_strtod`…`c_strtoull`、`c_atof`…`c_atoll` 调 `libc::strtod` 等 | C 库函数是 `unsafe extern "C"` | 传入的指针指向一段以 NUL 结尾、在函数调用期间不变的字节：要么是调用方切片里第一个 NUL 之前的部分（NUL 在切片内），要么是本函数自己的栈或堆拷贝（末尾补 NUL）；C 库只读到 NUL 为止。`endptr` 由 C 库写成 `[nptr, NUL]` 内的指针，用 `offset_from` 换成偏移（同一次分配内）；拷贝时偏移相对拷贝的起点，与原切片相同。`setlocale` 只在 `main` 开头、线程创建之前调用（S-CTYPE-1），之后读 locale 是安全的 |
| U2 | `errno`/`set_errno`：macOS `libc::__error()`、Linux `libc::__errno_location()`、Android `libc::__errno()`、Windows 自己声明的 `_errno()` | 取线程局部 errno 的地址 | 这些函数返回当前线程 errno 的有效地址，线程存活期间不变；只在当前线程上读写 |
| U3 | `remainder`、`lround`、`lrint`、`frexp`、`ldexp`、`modf` 的 `extern "C"` 声明 | libc crate 没有声明它们；系统 libm 已由 std 链接（macOS 在 libSystem 里） | 纯函数，签名与 C 原型一致（`double`/`int`/`long`）；`frexp`、`modf` 的输出指针指向调用方的局部变量 |

对外的 API 全是安全函数。`CppFloatMap` 用下标代替指针，`sort` 用 `swap`/`rotate_right` 代替移出移入（PERF(port)：比 C++ 多一些搬移），都不需要 unsafe。

## 5. 测试

放在 `rust/port/oblib/tests/`（RULEBOOK §4）。golden 文件放在 `rust/port/oblib/tests/golden/06/`，生成它们的 C++ 程序放在 `rust/port/oblib/tests/golden/gen/06_*.cpp`，用基线工具链编译：`deps/3rd/usr/local/oceanbase/devtools/bin/clang++`（17.0.6），参数取自 `build_release/compile_commands.json` 的 oblib 编译单元（`-std=gnu++20 -O2 -DNDEBUG -isysroot <MacOSX15.4.sdk> -mmacosx-version-min=15.0 -march=armv8-a+crc+lse -fno-strict-aliasing -D_NO_EXCEPTION`，按 D17 再加 `-ffp-contract=off`），需要 oblib 代码的（字符串、序列化、哈希）链接 `build_release` 下的 oblib 目标文件。golden 文件入库，生成方法写在同目录的 README 里；与平台有关的项（`fnv_hash2`、ctype、`c_strto*` 的 errno）在 Linux x86-64 上再生成一份（DEV-005 的环境）。

| # | 种类 | 内容 | 依据 |
|---|---|---|---|
| T-STR-1 | 金标准 | `06_obstring.cpp`：对一组字符串对（空、含 NUL、含 0x80 以上字节、前后空白含 `\v`、大小写混合、不同长度）调用 `ObString` 的每个只读方法，输出结果；Rust 逐项比对 `ObStringExt` | S-STR-2…9 |
| T-STR-2 | 单测 | `ObStringWriter`：`write` 放不下时返回 0、`write_front`、`set_length` 越界与负数、`shrink`、`remain`、`into_string` | S-STR-12 |
| T-STR-3 | 金标准 | `06_sqlstring.cpp`：随机长度的 `append`/`append_fmt`/`assign`/`set_length` 序列，每步输出 `length()`、`capacity()`、返回码和内容；未分配时的 `assign(ObSqlString)` | S-STR-14 |
| T-STR-4 | 金标准 | `06_stringbuffer.cpp`：同上，覆盖 `flag` 为 −1 与 0、分配器为空、`get_result_string` | S-STR-15 |
| T-STR-5 | 单测 | `ob_strcasecmp`/`ob_strncasecmp` 与 `libc::strcasecmp`/`strncasecmp`（C locale）对 0–255 全部字节对比符号；`strlen`/`memmem`/`strcasestr`/`strtok_r` 与 C 库对比 | S-STR-3、16 |
| T-STR-6 | 金标准 | ObString 的序列化：各长度的编码字节；截断的输入、`buffer_size_` 不够、`str_len < 0` 的返回码 | S-STR-13 |
| T-CTYPE-1 | 单测 | `setlocale(LC_CTYPE, "C")` 之后，对 −128..=255 逐值比较 `oblib::ctype` 与 `libc` 的同名函数（布尔化） | S-CTYPE-1…3 |
| T-NUM-1 | 单测 | `c_*` 与直接调 C 库（自己补 NUL）对比值、结束偏移、errno：含 NUL 的切片、不含 NUL、≥ 512 字节、前导空白、十六进制浮点、`inf`/`nan`、上溢下溢、`base = 0/8/16/36`、没有数字；`set_errno(0)` 后 errno 只被 C 库改；先 `setlocale(LC_NUMERIC, "en_US.UTF-8")` 再测一遍 | S-NUM-1 |
| T-NUM-2 | 金标准 | `06_fast_float.cpp`：用 deps 里的 fast_float 6.1.3 对约 10 万个输入输出 `(ec, ptr, 位模式)`：随机十进制串（1–800 位尾数、±400 指数）、最大有限值附近（`1.7976931348623157e308`、`…58e308`、`…59e308`）、最小次正规数附近（`2.4703282292062327e-324`、`…28e-324`、`4.9406564584124654e-324`）、`"1."`、`".5"`、`"."`、`"-"`、`"+1"`、`"1e"`、`"1e+"`、`"0x1p3"`、`" 1"`、inf/nan 的各种大小写和 `nan(…)`；`f32` 一份；`u32`/`u64`/`i64` 的前导零、`"-0"`、溢出边界 | S-NUM-2 |
| T-NUM-3 | 单测 | `CppFloatKey`：非 NaN 时 `cmp` 与 `<` 一致、±0 相等、全序公理（固定种子的随机三元组） | S-NUM-3 |
| T-NUM-4 | 金标准 | `06_float_map.cpp`：S-NUM-3 的测基线方法；`CppFloatMap` 全部相同 | S-NUM-3 |
| T-NUM-5 | 金标准 | `06_fptosi.cpp`：在 arm64 上把 `static_cast<int8_t/uint8_t/int16_t/uint16_t>(d)` 存进 `volatile` 变量（复核 C20 的方法），覆盖 ±0、±inf、NaN、±2^31 附近、300.0、−5.0、1e10 | S-NUM-4 |
| T-NUM-6 | 单测 | `c_div`/`c_rem`：aarch64 上的四种特殊值；x86-64 上在子进程里运行，期望 abort | S-NUM-5 |
| T-NUM-7 | 单测 | `round_ties_even(2.5) == 2.0`；`powf`、`ln`、三角函数与直接调 libm 逐位相同（固定种子的随机输入）；`remainder`/`lround`/`lrint`/`frexp`/`ldexp`/`modf` 与 libm 相同 | S-NUM-6 |
| T-SORT-1 | 金标准 | `06_sort.cpp`：`std::sort` 加自定义比较器对象（走通用路径）排序 `(键, 原下标)`，比较器只看键、并累计调用次数和调用序列的滚动哈希；长度 0–7、8–24、25–128、129–4096、10^5；分布：键取值 0..k（k = 1、2、10、n/4、n）、已排序、逆序、风琴管、锯齿、全相等；输出排列、调用次数、调用序列哈希。Rust 的 `ob_sort` 必须三项全同 | S-SORT-1、2 |
| T-SORT-2 | 金标准 | 用 McIlroy 的 antiqsort 对手比较器（按需给键赋值）逼出深度用尽、退化堆排序的路径；Rust 用同一个对手 | S-SORT-1 |
| T-SORT-3 | 金标准 | `push_heap`/`pop_heap`/`make_heap`/`sort_heap` 的随机操作序列（含大量相等键），每步输出整个数组 | S-SORT-6 |
| T-SORT-4 | 金标准 | `lower_bound`/`upper_bound`/`binary_search`：有序和无序区间、调用序列 | S-SORT-7 |
| T-SORT-5 | Miri | 长度 ≤ 64 的随机输入跑 `ob_sort` 和堆函数；不满足严格弱序的比较器（随机返回）不 panic、结果是原输入的一个排列 | S-SORT-4 |
| T-SER-1 | 金标准 | `06_serial_prim.cpp`：所有原语对边界值（0、±1、各 `OB_MAX_V*B` 两侧、`i64::MIN`、NaN 位模式）的编码字节、长度、截断输入的返回码和 `pos` | S-SER-1、2 |
| T-SER-2 | 金标准 | `06_serial_unis.cpp`：在生成器里定义一组测试类，覆盖 `OB_SERIALIZE_MEMBER`、`(C, P)`、`_INHERIT`、`_SIMPLE`、`_TEMP`、`_IF`、`OB_DEF_*` 加 `BASE_SER`、嵌套、`char[]` 与 `uint8_t[]` 成员、枚举、`std::pair`、各种数组宏；输出编码字节和 `get_serialize_size`。Rust 照写同样的类比对 | S-SER-3、4、8 |
| T-SER-3 | 金标准 | 兼容性：v1 的类解码 v2 的数据（多出尾部成员被跳过）、v2 解码 v1（缺的成员保留默认值）、版本不符、`len < 0`、`len` 超出、每一个截断长度、11 个以上续位字节；输出返回码、`pos`、各字段值 | S-SER-3、5 |
| T-SER-4 | 单测 | 往返：随机生成上述类的值，编码后解码相等；`ObDeserializeIn` 拷进 arena 后输入缓冲可以丢弃 | |
| T-POD-1 | 单测 | `PodField` 的往返与小端字节；`read_from` 短缓冲返回 `OB_BUF_NOT_ENOUGH`；`write_to` 把填充字节写 0 | S-POD-1、5 |
| T-POD-2 | 金标准 | `06_layout.cpp`：对一组结构（`ObMicroBlockHeader`、`ObMacroBlockCommonHeader`、`ObDatumDesc`、`ObMySQLDate`、`ObMySQLDateTime`、`ObNumberDesc`，以及人造的混合类型位域、有符号位段、packed 跨单元、零宽位段、联合体视图）输出 `sizeof`、每个字段的 `offsetof`，以及每个位段单独置全 1 时存储字节的样子；Rust 比对 `LAYOUT`/`BITS` 和 `write_to` 的结果 | S-POD-2、3 |
| T-POD-3 | 单测 | 位段读出的类型：`len_ - 1` 在 0 时是 −1；有符号位段符号扩展；写入按位宽截断 | S-POD-3 |
| T-SLOT-1 | 单测 | `bind`/`get`/`get_or`/重复绑定返回 `OB_INIT_TWICE`；8 个线程在绑定前后并发 `get` 只看到 `None` 或完整的对象 | S-SLOT-1…3 |
| T-HASH-1 | 金标准 | 1d 提前翻译的 `murmur_hash`：`murmurhash64A`、`murmurhash2`、`fnv_hash2` 对随机输入（长度 0–64、含 0x80 以上字节）的结果；`fnv_hash2` 的 golden 在 macOS arm64 上生成（`char` 有符号，与 x86-64 相同），另在 Linux aarch64（`char` 无符号）上生成一份，Rust 按 `cfg` 选用 | S-NUM-7 |

不需要 loom 测试：本模块没有自写的并发结构，`Slot` 用 std 的 `OnceLock`。Miri 只跑纯 Rust 的部分（`sort`、`serialization`、`pod`、`string`、`CppFloatMap`、`fast_float_from_chars`），`num` 里调 C 库的函数不跑（Miri 不支持这些外部函数）。

## 6. 翻译者怎么用

**例 1 写缓冲**（`src/sql/engine/ob_subschema_ctx.cpp:471-473`）

```cpp
type_info.assign_buffer(name_def, type_def.length());
type_info.write(type_def.ptr(), type_def.length());
buf->set_name(type_info);
```
```rust
let mut type_info = ObStringWriter::with_buffer(&mut name_def[..type_def.length() as usize]);
type_info.write(type_def);
buf.set_name(type_info.into_string());
```

**例 2 NULL 与空串有语义的成员**（`src/share/ob_core_table_proxy.cpp:855-866`；`cell_.value_` 由 1c 第 4 类标成 `Option<ObStr<'q>>`，S-STR-2）

```cpp
} else if (NULL == uc->cell_.value_.ptr()) {
  if (OB_FAIL(value_sql.append_fmt("%s ('%s', %ld, '%.*s', NULL)", value_sql.empty() ? "" : ",", table_name_,
                                   row.get_row_id(), uc->cell_.name_.length(), uc->cell_.name_.ptr()))) {
  }
} else { …
  } else if (OB_FAIL(value_sql.append_fmt(uc->cell_.is_hex_value_ ? "%.*s)" : "'%.*s')",
                                          uc->cell_.value_.length(), uc->cell_.value_.ptr()))) {
```
```rust
} else if uc.cell_.value_.is_none() {
    if ob_fail!(ret = append_fmt!(value_sql, "%s ('%s', %ld, '%.*s', NULL)",
                                  if value_sql.empty() { &b""[..] } else { &b","[..] }, table_name_,
                                  row.get_row_id(), uc.cell_.name_.length(), uc.cell_.name_)) {
    }
} else { …
    // value = uc.cell_.value_ 里的 Some 值；格式串必须是字面量（04 §2.4.1）：三目运算拆成两个调用
    } else if uc.cell_.is_hex_value_ {
        if ob_fail!(ret = append_fmt!(value_sql, "%.*s)", value.length(), value)) {}
    } else if ob_fail!(ret = append_fmt!(value_sql, "'%.*s')", value.length(), value)) {
    }
```
（`%.*s` 的实参形式以 04 的 `ob_printf!` 为准。）

**例 3 C 库的 strtod**（`src/sql/hybrid_search/ob_query_parse.cpp:1981-1988`；`pure_field_str` 是带 NUL 的 arena 拷贝，`weight_start` 是 `^` 之后到 NUL 的字节）

```cpp
char *end_ptr = nullptr;
weight = strtod(weight_start, &end_ptr);
if (end_ptr <= weight_start || weight < 0) {
```
```rust
let mut end_ptr = 0usize;
weight = c_strtod(weight_start, Some(&mut end_ptr));
if end_ptr == 0 || weight < 0.0 {
```

**例 4 errno**（`src/oblib/lib/utility/utility.cpp:2082-2097`）

```cpp
res = 0;
errno = 0;
if (OB_ISNULL(str)) {
  ret = OB_INVALID_ARGUMENT;
} else {
  res = strtoll(str, &endptr, 10);
  if (ERANGE == errno) { ret = OB_SIZE_OVERFLOW; } else if (errno != 0) { ret = OB_INVALID_ARGUMENT; }
}
```
```rust
*res = 0;
set_errno(0);
if str.is_none() {                         // const char* 参数显式判 NULL → Option<&[u8]>（S-STR-2）
    ret = Err(OB_INVALID_ARGUMENT);
} else if let Some(str) = str {            // 保持 if/else 链的形状
    *res = c_strtoll(str, Some(&mut *endptr), 10);
    if libc::ERANGE == errno() { ret = Err(OB_SIZE_OVERFLOW); } else if errno() != 0 { ret = Err(OB_INVALID_ARGUMENT); }
}
```

**例 5 fast_float**（`src/share/object/ob_array_cast.cpp:705-713`，`dim: u32`）

```cpp
fast_float::from_chars_result result = fast_float::from_chars(key_start, ptr, dim);
if (result.ec == std::errc::invalid_argument) { ret = OB_INVALID_ARGUMENT; }
else if (result.ec == std::errc::result_out_of_range) { ret = OB_DATA_OUT_OF_RANGE; }
else if (result.ptr != ptr) { ret = OB_INVALID_ARGUMENT; }
```
```rust
let result = fast_float_from_chars(&text[key_start..ptr], dim);
if result.ec == errc::invalid_argument { ret = Err(OB_INVALID_ARGUMENT); }
else if result.ec == errc::result_out_of_range { ret = Err(OB_DATA_OUT_OF_RANGE); }
else if key_start + result.ptr != ptr { ret = Err(OB_INVALID_ARGUMENT); }
```

**例 6 浮点键的 std::map**（`src/sql/engine/expr/ob_expr_map.cpp:235-252`，宏 `CALC_KEY_IDX(double, get_double)`）

```cpp
std::map<Element_Type, uint32_t> key_idx;
…   key_idx[datum->Get_Func()] = i;
std::map<Element_Type, uint32_t>::iterator it = key_idx.begin();
for (; it != key_idx.end() && OB_SUCC(ret); ++it) { idx_arr[idx_count++] = it->second; }
```
```rust
let mut key_idx: CppFloatMap<f64, u32> = CppFloatMap::new();
…   match key_idx.at(datum.get_double()) { Ok(v) => *v = i, Err(e) => ret = Err(e) }   // 新分支：分配失败（§2.1 的恢复规则）
for (_, v) in key_idx.iter() { if ret.is_err() { break; } idx_arr[idx_count as usize] = *v; idx_count += 1; }
```
（整数和 `ObString` 键的实例化照 RULEBOOK §2.5 用 `BTreeMap<i8…u64, u32>`/`BTreeMap<ObStr<'q>, u32>`：切片的 `Ord` 与 `ObString::operator<` 相同。）

**例 7 浮点转窄整型**（`src/share/object/ob_array_cast.cpp:452-456`，`Element_Type = int8_t`，`value: double`）

```cpp
if (OB_FAIL(dst_arr->push_back(static_cast<Element_Type>(value)))) {
```
```rust
if ob_fail!(ret = dst_arr.push_back(c_fptosi_i8(value))) {   // FIXED(port): 越界的浮点转窄整型是 UB，按"先饱和到 32 位再截断"
```

**例 8 数学库**（`src/sql/engine/expr/ob_expr_util.cpp:392-405`）

```cpp
const double pow_val = std::pow(10.0, static_cast<double>(std::abs(dec)));
…
res = dec < 0 ? rint(val_div_tmp) * pow_val : rint(val_mul_tmp) / pow_val;
```
```rust
let pow_val = 10.0f64.powf(dec.wrapping_abs() as f64);
…
res = if dec < 0 { val_div_tmp.round_ties_even() * pow_val } else { val_mul_tmp.round_ties_even() / pow_val };
```

**例 9 ob_sort 与堆**（`src/sql/engine/sort/ob_sort_op_impl.cpp:1441`；`src/sql/engine/aggregate/ob_hash_groupby_op.cpp:2617-2626`）

```cpp
lib::ob_sort(&bucket_nodes.at(0), &bucket_nodes.at(0) + bucket_part_cnt, HashNodeComparer(comp_));
…
std::make_heap(popular_array_temp.begin(), popular_array_temp.end(), group_row_items_greater);
std::pop_heap(popular_array_temp.begin(), popular_array_temp.end(), group_row_items_greater);
popular_array_temp.at(SKEW_HEAP_SIZE - 1) = &item;
std::push_heap(popular_array_temp.begin(), popular_array_temp.end(), group_row_items_greater);
```
```rust
let mut cmp = HashNodeComparer::new(&mut comp_);              // 比较器借用 comp_，错误照旧记在 comp_.ret_ 里
ob_sort(&mut bucket_nodes[..bucket_part_cnt as usize], |a, b| cmp.call(a, b));
…
make_heap(&mut popular_array_temp[..], |a, b| group_row_items_greater(a, b));
pop_heap(&mut popular_array_temp[..], |a, b| group_row_items_greater(a, b));
*popular_array_temp.at_mut(SKEW_HEAP_SIZE - 1) = item_ref;   // 元素表示以清单为准
push_heap(&mut popular_array_temp[..], |a, b| group_row_items_greater(a, b));
```

**例 10 OB_SERIALIZE_MEMBER**（`src/storage/tx/ob_tx_log.h:463` 的 `OB_UNIS_VERSION(1)`，`src/storage/tx/ob_tx_log.cpp:222-229`）

```cpp
OB_SERIALIZE_MEMBER(ObTxCommitLog, /* 1 */ commit_version_, /* 2 */ checksum_, /* 3 */ multi_source_data_,
                    /* 4 */ tx_data_backup_, /* 5 */ prev_lsn_, /* 6 */ checksum_sig_serde_, /* 7 */ prev_log_type_);
```
```rust
ob_serialize_member!(ObTxCommitLog, 1; commit_version_, checksum_, multi_source_data_,
                     tx_data_backup_, prev_lsn_, checksum_sig_serde_, prev_log_type_);
```

**例 11 OB_DEF_SERIALIZE 加 LST_DO_CODE，联合体里的整数**（`src/oblib/lib/net/ob_addr.cpp:473-494`，`ob_addr.h:35` 的 `OB_UNIS_VERSION(1)`）

```cpp
OB_DEF_SERIALIZE(ObAddr) {
  int ret = OB_SUCCESS;
  uint32_t ip_1 = *reinterpret_cast<const uint32_t *>(&ip_.v6_[0]);
  …
  LST_DO_CODE(OB_UNIS_ENCODE, version_, ip_1, ip_2, ip_3, ip_4, port_);
  return ret;
}
OB_DEF_DESERIALIZE(ObAddr) { … LST_DO_CODE(OB_UNIS_DECODE, version_, ip_1, ip_2, ip_3, ip_4, port_);
  *reinterpret_cast<uint32_t *>(&ip_.v6_[0]) = ip_1; … }
```
```rust
impl<'de> ObUnisBody<'de> for ObAddr {
    fn serialize_(&self, buf: &mut [u8], pos: &mut i64) -> ObResult {
        let mut ret: ObResult = Ok(());
        let ip_1: u32 = oblib::pod::read_le(&self.ip_, 0);      // 联合体 ip_ 存成 [u8; 16]（S-POD-4）；本机序即小端
        …
        ob_unis_encode!(ret, buf, pos, self.version_, ip_1, ip_2, ip_3, ip_4, self.port_);
        ret
    }
    fn deserialize_(&mut self, buf: &'de [u8], pos: &mut i64) -> ObResult {
        let mut ret: ObResult = Ok(());
        let (mut ip_1, mut ip_2, mut ip_3, mut ip_4) = (0u32, 0u32, 0u32, 0u32);
        ob_unis_decode!(ret, buf, pos, self.version_, ip_1, ip_2, ip_3, ip_4, self.port_);
        oblib::pod::write_le(&mut self.ip_, 0, ip_1); …
        ret
    }
    fn get_serialize_size_(&self) -> i64 { /* … ob_unis_add_len!(len, …) */ }
}
ob_unis!(ObAddr, 1);
```

**例 12 手写的落盘格式，定长大端**（`src/logservice/palf/log_entry_header.cpp:224-244`；零拷贝的 `LogEntry::deserialize`，`log_entry.cpp:83-99`）

```cpp
} else if (OB_FAIL(serialization::encode_i16(buf, buf_len, new_pos, magic_))
           || OB_FAIL(serialization::encode_i16(buf, buf_len, new_pos, version_))
           || OB_FAIL(serialization::encode_i32(buf, buf_len, new_pos, log_size_)) …) {
  ret = OB_BUF_NOT_ENOUGH;
}
…
buf_ = header_.get_data_len() > 0 ? const_cast<char *>(buf + new_pos) : NULL;
```
```rust
} else if ob_fail!(ret = serialization::encode_i16(buf, &mut new_pos, self.magic_))
       || ob_fail!(ret = serialization::encode_i16(buf, &mut new_pos, self.version_))
       || ob_fail!(ret = serialization::encode_i32(buf, &mut new_pos, self.log_size_)) … {
    ret = Err(OB_BUF_NOT_ENOUGH);
}
…
// impl<'de> ObDeserialize<'de> for LogEntry<'de>：buf_: Option<&'de [u8]>
let n = self.header_.get_data_len();
self.buf_ = if n > 0 { Some(&buf[new_pos as usize..(new_pos + n) as usize]) } else { None };   // 前一行已检查 data_len - new_pos >= n
```

**例 13 位域与落盘头**（`src/oblib/common/timezone/ob_time_def.h:29-50`；`src/storage/blocksstable/ob_macro_block_common_header.cpp:99-106`）

```cpp
union { struct { uint32_t day_ : 5; uint32_t month_ : 4; uint32_t year_ : 14; uint32_t reserved_ : 9; }; int32_t date_; };
…
ObMacroBlockCommonHeader *common_header = reinterpret_cast<ObMacroBlockCommonHeader*>(buf + pos);
common_header->header_size_ = header_size_;  …  common_header->payload_checksum_ = payload_checksum_;
pos += common_header->get_serialize_size();
```
```rust
ob_bitfield! {
    #[derive(Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Debug)]   // 比较就是比较 date_（:33-38）
    pub struct ObMySQLDate(pub date_: i32) {
        day_: u32 @ 0..5, month_: u32 @ 5..9, year_: u32 @ 9..23, reserved_: u32 @ 23..32,
    }
}
…
// 前面 C++ 的 `pos + get_serialize_size() > buf_len` 检查照留；6 个字段正好铺满 24 字节，所以整体写等于逐字段写
self.write_to(&mut buf[*pos as usize..]);
*pos += ObMacroBlockCommonHeader::SIZE as i64;
```
（只改头里几个字段、其余字节要保留的写法，用 `ObMacroBlockCommonHeaderMut` 的 `set_<字段>()`。）

**例 14 槽位**（`src/share/datum/ob_datum_funcs_impl.h:306-307`）

```cpp
ObILobReadService *read_service = share::server_service<ObILobReadService>();
if (OB_NOT_NULL(read_service)) {
```
```rust
let read_service = I_LOB_READ_SERVICE_SLOT.get();     // 槽位名来自 slots.tsv
if let Some(read_service) = read_service {
```

**例 15 ctype 与先读后判**（`src/oblib/common/timezone/ob_time_convert.cpp:878`、`:2026-2029`）

```cpp
for (; !isdigit(*pos) && pos < end; ++pos) {}
…
for (; pos < end && isspace(*pos); ++pos) {}
for (i = 0; pos < end && i < OB_MAX_TZ_NAME_LEN -1 && !isspace(*pos); ++pos, ++i) {
  ob_time.tz_name_[i] = static_cast<char>(tolower(*pos));
```
```rust
while pos < end && !isdigit(s[pos] as c_int) { pos += 1; }   // FIXED(port): C++ 先读 *pos 再判 pos < end
…
while pos < end && isspace(s[pos] as c_int) { pos += 1; }
while pos < end && i < OB_MAX_TZ_NAME_LEN - 1 && !isspace(s[pos] as c_int) {
    ob_time.tz_name_[i as usize] = tolower(s[pos] as c_int) as u8;
    pos += 1; i += 1;
}
```

## 7. 依赖

- **白名单 crate**（RULEBOOK §1）：`libc`（`num` 的 FFI、`errc` 的常量、测试里的对照）；`syn`、`quote`、`proc-macro2`（只在 `ob_macros` 里实现 `ob_bitfield!` 和 `derive(ObPod)`）。不需要新 crate。`smallvec`、`bumpalo` 等经 01、05 间接使用。
- **其他 1d 模块**：
  - 01 `mem`：`Arena`（`alloc`、`try_str`、`try_str_c`）、`ObStr`、`try_bytes`；`ObStringBuffer` 的块分配用 `Arena::alloc`（01 §2.2）。
  - 02 `sync`：只在 prelude 里转导出。
  - 03 `thread-signal`：`main` 开头照抄 `main.cpp:693-698` 的四句 `setlocale`（S-CTYPE-1），早于任何解析数值的代码。
  - 04 `error-log-fmt`：`ObError`/`ObResult`/`OB_*`、`ob_fail!` 等错误链宏、`log_warn!`/`log_error!`、`ob_e!`（errsim）、`ObFmtArgs` 与 `fmt::vsnprintf`（`ObSqlString::vappend`）、`append_fmt!`/`assign_fmt!`、`ObToString`（含 `u8` 的切片钩子实现 ObString 的打印）、`ob_macros` crate 本身。
  - 05 `containers`：`ObIArray`（prelude）；05 用本模块的 `ObStringExt::hash`、`ObSerialize`/`ObDeserialize` 实现容器的哈希和序列化。
  - 07：`ObDatumDesc`、`ObObj` 等用 `ob_bitfield!`/`PodField`；`ObNumber` 的 digits 用 `decode_number_type`。
  - 09：`ob_strntoll`/`ob_strntoull` 给 C 解析器的 `extern "C"` 蹦床；`ob_macros` 的构建。
- **1d 期间提前翻译的单元**（处置仍是 `translate`，按第 3 步的规则写到各自的目标路径，带状态行，第 3 步视为已完成）：`murmur_hash.h/.cpp`、`ob_fast_convert.h`、`ob_sensitive_string.h`；`ob_define.h` 见 §8 Q8。

## 8. 开放问题

**Q1 `ObString` 与 `const char *` 的 NULL 和空串**（修订 A3）
- 现状：RULEBOOK 把它们映射成 `&[u8]`/`Box<[u8]>`，表达不了 NULL；§2.1 又说"指针一律 `Option`"。C++ 约 172 处显式判断 `.ptr()` 是否为空，其中有语义差别的例子见 `ob_core_table_proxy.cpp:855`；`case_compare` 对"NULL 对非 NULL 空串"返回 ±1（S-STR-2）。
- **推荐 (A)**：`ObStringExt` 把 NULL 当空串；显式判断 NULL 的成员、参数（含 `const char *` 参数）由 1c 第 4 类标成 `Option<ObStr<'q>>`/`Option<Box<[u8]>>`/`Option<&[u8]>`，判断译成 `is_none()`；其余位置的差别（只影响 `case_compare` 一类比较）在 A.4 新增 `obstring-null`。
- 备选 (B)：`ObStr<'q>` 一律定义成 `Option<&'q [u8]>`，`ObStringExt` 实现在 `Option<&[u8]>` 上。完全忠实，但全库约 1.7 万处 `ObString` 都要多一层 `Option`，并且要改 01 的定义。

**Q2 以浮点数为键的 `std::map`**（修订 A1）
- 现状：RULEBOOK §2.5 要求 `CppFloatKey` 复刻 C++ 的等价类、"NaN 照基线实测"。实测的对象其实是红黑树的形状，`Ord` 包装做不到（S-NUM-3）。
- **推荐 (A)**：两处（4 个实例化）改用 `CppFloatMap`，逐行移植 libc++ `__tree` 的插入、旋转和中序遍历，约 150 行，对任何输入与基线相同；`CppFloatKey` 保留为非 NaN 的全序包装。
- 备选 (B)：保留 `BTreeMap<CppFloatKey>`，所有 NaN 合成一个排在最后的等价类；含 NaN 的输入与基线不同，A.4 新增 `float-key-nan`。

**Q3 C 数学库的映射**（修订 A2）：RULEBOOK §2.12 写"`frexp`/`ldexp`/`modf` → libc 同名函数"，但 libc crate 没有声明它们；表里也缺 `remainder`、`lround`、`lrint` 和最容易译错的 `log → ln`。**推荐**：采用 S-NUM-6 的表，6 个函数由 `oblib::num` 声明系统 libm 后提供安全包装。备选：引入 `libm` crate（纯 Rust 实现，结果与系统 libm 不一定逐位相同，不推荐）。

**Q4 C 的 `errno`**（修订 A5）：RULEBOOK 没有译法（约 440 处），而 `c_strto*` 的溢出要靠它判断。**推荐**：`errno` → `oblib::num::errno()`，`errno = x` → `set_errno(x)`。备选：放到 04 的 `oblib::error`，签名不变，只改路径。

**Q5 两处用户数据大小的不可失败分配**（修订 A8）：RULEBOOK 0.7 要求"大小取决于用户数据的分配必须可失败"。`c_*` 要按 RULEBOOK 拷成以 NUL 结尾的缓冲，返回值是 C 的形状、没有错误通道；`slice::sort_by` 的临时缓冲也不可失败。**推荐**：A.4 `oom-abort` 的口径扩到这两处（`c_*` 只对不含 NUL 且 ≥ 512 字节的输入在堆上拷贝，实际的数值串都更短）。备选：`c_*` 返回 `ObResult<…>`（约 70 个调用点都要多写错误分支）；移植 libc++ 的 `stable_sort`（约 270 行）以拿不到缓冲时退化为原地归并。

**Q6 `qsort`**（修订 A6）：RULEBOOK 没有译法（2 处）。**推荐**：`qsort(base, n, size, cmp)` → `ob_sort(slice, |a, b| cmp(a, b) < 0)`；两处的并列次序都不进入输出（`ob_ctype_simple.cc:1131` 排的是互不相交的码位平面，`ob_log.cpp:1361` 只影响日志文件的清理顺序），不需要 `DIVERGE`。备选：经 FFI 调平台 `qsort`（要多一处 unsafe）。

**Q7 `ob_strtol` 的名字**（修订 A9）：RULEBOOK §2.5 的"`ob_strtol`/`ob_strtod`（MySQL 的 dtoa 一族）"里，`ob_strtol` 在基线里不存在；对应的是 §1.1 数值表的三组函数，其中 `ob_strtoll`/`ob_strtoull`/`ob_atoll` 是 C 库语义。`ob_strtod` 也不只是 MySQL dtoa，先走 fast_float 快路径。**推荐**：§2.5 那一行按 §1.1 改写；三组都是 `translate`，在第 3 步逐行翻译，本模块只提供它们依赖的 `fast_float_from_chars`、`ctype`、`c_strtoll`/`c_strtoull`、`errno`。

**Q8 1d 依赖的普通单元由谁先翻译**：01 的文档假定 `ob_define.h` 的常量由 06 给出（01 第 74、1320 行），04 依赖 `ObFastFormatInt`、`ObSensitiveString`（04 §1.2），05 和本模块依赖 `murmurhash`。它们的处置都是 `translate`，但第 3 步之前 1d 的代码就要编译。**推荐**：由 06 的实现者在 1d 期间按第 3 步的规则先翻译 `murmur_hash`、`ob_fast_convert.h`、`ob_sensitive_string.h`，以及 `ob_define.h`（2,014 行，约 852 个常量、60 个宏、十几个枚举和 `ObNumberDesc`；其中的序列化宏和位域本来就在本模块的范围内），写到各自的目标路径。备选：由 1d 的总负责人另行指派。

**Q9 新名字与重载名**（修订 A7）：§2.9、§2.10 列出的名字不在 RULEBOOK §4 的改名表里。**推荐**：审查时一并确认、补进改名表；被替换单元的 C++ 重载集以本文 §2.9 为准，`overloads.tsv` 对这些单元从 API 文档取名（与 04 §2.7 的做法一致）。

已在 `migration/infra/amendments.tsv` 追加 8 行，`section` 列标作 `06-A1`…`06-A9`，与上文的修订编号一一对应（A4 原计划处理 `ob_write_string` 的 `c_style`，01 已用 `try_str_c` 覆盖，撤销）。Q8 是 1d 内部的分工，不是 RULEBOOK 的错，没有修订行。
