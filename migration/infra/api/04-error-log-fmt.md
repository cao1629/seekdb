# 04 `error-log-fmt`：错误码、错误链宏、日志、格式化与 TO_STRING

> 依据：`migration/RULEBOOK.md` v0.2（2026-10-11 签字）§1 的错误、日志、格式化三行，§2.1 全表与错误恢复规则，§2.2，§2.3，§2.11 的 TO_STRING 部分，0.11 第 1–2 条，0.13 的 errno 生成器；`migration/infra/README.md` 的结构要求。基线 `7d907abfa`（`git diff --stat 7d907abfa HEAD -- src` 为空，工作区的 `src/` 就是基线）。
>
> 计数口径：除特别注明外，"约 N 处"是对 `src/**/*.{cpp,h,ipp,cc,c}` 做的正则计数（含宏定义本身和注释，所以是上限）。脚本放在 `/tmp/ob04scripts/`，按 1d 的规则不入库：`count_tokens.py`（按记号计数）、`fmt_specs.py`（抽取 printf 族调用里的字面格式串并统计转换说明）、`errno_parse.py`（按 `gen_errno.pl` 的正则解析 `ob_errno.def`）、`logmods.py`/`prefixes.py`（模块表、注册表、可用前缀）、`errsim_scope.py`（`OB_E`/`ERRSIM_POINT_DEF` 是否在 `#ifdef ERRSIM` 里）。平台 C 库行为用 `/tmp/ob04probe/p.c`、`p2.c` 在本机（macOS，系统 `cc`）实测，与行为一致性审查 F14 的做法相同。
>
> 本文所有"C++ 依据"都是基线源码的 file:line；"FIXED(port)"只用于照搬会构成未定义行为的地方；与 C++ 不同而又不属于 A.4 的地方一律列进 §8 和 `migration/infra/amendments.tsv`。

## 1. 范围

### 1.1 本模块负责的单元

单元按 RULEBOOK §4 的头文件组划分（同目录同名的 `.h/.cpp/.ipp/.def` 是一个单元），"manifest 行"是 `migration/manifest.tsv` 里的行号。处置为 `replace:*` 的单元，1d 在目标路径写只含 `pub use` 的桩文件（§4），文件头 `//! Disposition: <处置>`。

| # | 单元（代表文件，manifest 行） | 成员 | 处置 | Rust 去向 | 依据 / 说明 |
|---|---|---|---|---|---|
| 1 | `src/share/ob_errno.def`（3609） | `ob_errno.def`；生成物 `share/ob_errno.h`、`share/ob_errno.cpp`、`oblib/lib/ob_errno.h`（三者在 `depmap/excluded.tsv:68,84,85` 标为 generated） | `generate:errno` | `oblib::error`（build.rs 写 `rust/port/oblib/src/generated/ob_errno.rs`）；桩 `rust/port/share/src/ob_errno.rs` 只含 `pub use oblib::error::*;` | 生成器 `src/share/gen_errno.pl:1-467`；RULEBOOK 0.4 "`ob_strerror`：下移，错误表由生成器写进 oblib"、0.13 |
| 2 | `src/share/mysql_errno.h`（3608） | 1,021 个 `#define ER_*` 加 9 个 `WARN_*` | `generate:errno` | `oblib::error::mysql_errno`（同一个 build.rs）；桩 `rust/port/share/src/mysql_errno.rs` | `ob_errno.def` 的 M1 列用这些符号（448 个不同符号、504 处，`errno_parse.py`）；`share/ob_errno.h:136` include 它 |
| 3 | `src/oblib/lib/oblog/ob_log_user_msg.h`（35） | 4 个 `__USER_ERROR_MSG` 宏、`ob_error_name` 声明 | `replace:error` | 由 #1 的生成物覆盖 | `ob_log_user_msg.h:19-25`，与 `ob_errno.def` 的同名消息逐字相同 |
| 4 | `src/oblib/lib/utility/ob_macro_utils.h`（14） | 425 个 `#define` | `replace:error` | 错误、断言、限流宏在本模块（§2.2）；其余宏的去向见 §2.2.6 的对照表 | `ob_macro_utils.h:451,650-659,662-680,685-762,767-916` |
| 5 | `src/oblib/lib/ob_check_macros.h`（247） | `CK`/`OX`/`OZ`/`OZX1`/`OZX2`/`OV`/`OC` | `replace:error` | `oblib` 根上的 `ck!` 等 | `ob_check_macros.h:22-190` |
| 6 | `src/oblib/lib/ob_abort.h`（53） | `ob_abort.h`、`ob_abort.cpp` | `replace:error` | `oblib::error::{ob_abort, abort_unless}` | `ob_abort.h:31-43`、`ob_abort.cpp:31-44` |
| 7 | `src/oblib/lib/utility/ob_defer.h`（32） | `ScopedLambda`、`DEFER`/`DEFER_C`/`NAMED_DEFER` | `replace:error` | `oblib::error::ScopedLambda`、`defer!`/`defer_c!`/`named_defer!` | `ob_defer.h:28-67`；RULEBOOK §2.13 最后一行 |
| 8 | `src/oblib/lib/utility/ob_tracepoint.h`（150） | `ob_tracepoint.h`、`.cpp` | `replace:error` | `oblib::error::errsim` | `OB_I`、`OB_E`、`ERRSIM_POINT_DEF`、`EventItem`、`NamedEventItem`、`EventTable`、`TPSymbolTable`（`ob_tracepoint.h:36-411`，`.cpp:17-95`） |
| 9 | `src/oblib/lib/utility/ob_tracepoint_def.h`（49） | 322 行 `GLOBAL_ERRSIM_POINT_DEF` | `replace:error` | 由 1d 的转换脚本改写成 `global_errsim_point_defs! { … }` 的一次调用，放在 `oblib::error::errsim::defs` | X-macro 表，被 `ob_tracepoint.h:404-407` 和 `ob_tracepoint.cpp:18-21` 各包含一次 |
| 10 | `src/oblib/lib/oblog/ob_log.h`（63） | `ob_log.h`、`ob_log.cpp` | `replace:log` | `oblib::log`（`ObLogger` 等） | 1,437 + 1,817 行 |
| 11 | `src/oblib/lib/oblog/ob_log_module.h`（57） | `ob_log_module.h`、`ob_log_module.ipp` | `replace:log` | `oblib::log`（模块表、`ObLogMod`、全部日志宏、`LOG_USER_*`、`LOG_DBA_*`、K 系列） | `ob_log_module.h:32-1301`，`.ipp:21-…` |
| 12 | `src/oblib/lib/oblog/ob_log_level.h`（34） | 级别常量、`ObThreadLogLevel` | `replace:log` | `oblib::log` | `ob_log_level.h:27-58` |
| 13 | `src/oblib/lib/oblog/ob_log_print_kv.h`（55） | `logdata_*`、`ObLogKV`、`ObLogValue`、`ObSzString` 等 | `replace:log` | `oblib::log`（KV）与 `oblib::fmt`（`ObToString` 的 `logdata_*` 方法） | `ob_log_print_kv.h:46-893` |
| 14 | `src/oblib/lib/oblog/ob_log_time_fmt.h`（56） | `.h`、`.cpp`（`ObTime2Str`） | `replace:log` | `oblib::log::ObTime2Str` | `ob_log_time_fmt.cpp:25-72` |
| 15 | `src/oblib/lib/oblog/ob_async_log_struct.h`（58） | `.h`、`.cpp`（`ObPLogItem`、`ObPLogFileStruct`、`ObPLogFDType`） | `replace:log` | `oblib::log` | `ob_async_log_struct.h:49-133` |
| 16 | `src/oblib/lib/oblog/ob_ringbuf_log_writer.h`（59） | `.h`、`.cpp`（`ObRingBuf`、`ObRingBufLogWriter`） | `replace:log` | `oblib::log` | `ob_ringbuf_log_writer.h:37-160`，`.cpp:30-345` |
| 17 | `src/oblib/lib/oblog/ob_base_log_writer.h`（54） | `.h`、`.cpp`（`ObIBaseLogItem`、`ObBaseLogWriterCfg`、`ObBaseLogWriter`） | `replace:log` | `oblib::log` | 唯一的子类是 slog 的 `ObStorageLogWriter`（`src/storage/slog/ob_storage_log_writer.h:42`），队列里存的是借用的 `ObIBaseLogItem*`，需要所有权重设计（§2.5.10） |
| 18 | `src/oblib/lib/oblog/ob_syslog_rate_limiter.h`（60） | `.h`、`.cpp` | `replace:log` | `oblib::log` | `ob_syslog_rate_limiter.h:34-155`、`.cpp:24-111` |
| 19 | `src/oblib/lib/utility/ob_rate_limiter.h`（45） | `lib::ObRateLimiter` | `replace:log` | `oblib::log::ObRateLimiter` | 只有 oblog 和 `src/share/ob_task_define.{h,cpp}` 用它 |
| 20 | `src/oblib/lib/oblog/ob_log_compressor.h`（189） | `.h`、`.cpp` | `replace:log` | `oblib::log::ObLogCompressor` | `ob_log_compressor.h:36-226`、`.cpp:46-618` |
| 21 | `src/oblib/lib/oblog/ob_log_dba_event.h`（44） | `.h`、`.cpp` | `replace:log` | `oblib::log::dba_event` | `ob_log_dba_event.h:24-240`（事件名常量、`server_start`/`bootstrap` 两个步骤表） |
| 22 | `src/oblib/lib/oblog/ob_warning_buffer.h`（190） | `.h`、`.cpp` | `replace:log` | `oblib::log::warning_buffer` | `ob_warning_buffer.h:37-296`、`.cpp:23-61` |
| 23 | `src/oblib/lib/oblog/ob_trace_log.h`（115） | `.h`、`.cpp` | `replace:log` | `oblib::log` | `NG_TRACE_TIMES` 一族、`PRINT_TRACE`、`FORCE_PRINT_TRACE`、`ObTraceLogConfig`（`ob_trace_log.h:28-114`，`.cpp:24-52`） |
| 24 | `src/oblib/lib/utility/ob_backtrace.h`（121） | `.h`、`.cpp` | `replace:log` | `oblib::log::{lbt, parray, light_backtrace}` | `ob_backtrace.h:39-64`、`.cpp:122-198`；`lbt()` 共约 546 处 |
| 25 | `src/oblib/lib/utility/ob_print_utils.h`（103） | `.h`、`.cpp` | `replace:fmt` | `oblib::fmt` | `databuff_printf` 一族、`to_string<T>` 特化、`databuff_print_obj`/`_key_obj`、`ObCStringHelper`、`J_*`、`TO_STRING_*`、十六进制工具（`ob_print_utils.h:36-1106`，`.cpp:26-595`） |
| 26 | `src/oblib/lib/utility/ob_print_kv.h`（102） | `databuff_print_kv` 1–64 元 | `replace:fmt` | `oblib::fmt::databuff_print_kv`/`j_kv!` | `ob_print_kv.h:156-312` |
| 27 | `src/oblib/lib/oblog/ob_base_log_buffer.h`（863） | `.h`、`.cpp` | `delete` | — | 除自身外无任何引用（`git grep ObBaseLogBuffer` 只命中自身和 bazel 清单）；交清单第 16 类确认 |
| 28 | `src/oblib/lib/oblog/ob_easy_log.h`（864） | 一个声明 | `delete` | — | `ob_easy_log_format` 没有定义也没有调用；`src/observer/main.cpp:54` 只是 include；交清单第 16 类确认 |

本模块定义、但函数体所在单元由别人翻译的格式化入口：C 可变参数的格式化函数一律不按原样翻译（Rust 没有 `...`），改成接收 `&ObFmtArgs<'_>` 的同名方法，调用处写 `ob_printf!(…)`（§2.4.6）。涉及：`ObSqlString::append_fmt/assign_fmt/vappend`（`src/oblib/lib/string/ob_sql_string.cpp:82-95,132-146,183-243`，06 号模块）、`ob_alloc_printf`（`src/oblib/lib/utility/utility.cpp:1648-1685`）、`ObBufferWriter::append_fmt`（`src/storage/blocksstable/ob_data_buffer.h:265`）、`ObOptimizerContext::add_plan_note`（`src/sql/optimizer/ob_optimizer_context.h:640`）、`ObTabletPersister::set_extra_info`（`src/storage/tablet/ob_tablet_persister.h:127`）、`ObDIActionGuard` 的构造（`src/oblib/lib/stat/ob_diagnostic_info_guard.h:256`）。

### 1.2 相关但不由本模块负责的单元

| 单元 | 处置 | 与本模块的关系 |
|---|---|---|
| `src/observer/mysql/obmp_packet_sender.cpp`（`send_error_packet`，`:528-716`） | `translate`，逐行翻译（RULEBOOK §2.3） | 用本模块的错误表、警告缓冲区句柄（§2.6、§3.13） |
| `src/oblib/lib/trace/ob_trace_event.h`、`ob_seq_event_recorder.h`、`ob_trace.h`、`ob_trace_def.h`（manifest 113、114、888、887） | `translate` | `NG_TRACE`/`NG_TRACE_EXT`/`REC_TRACE`/`REC_TRACE_EXT`/`THE_TRACE`（`ob_trace_event.h:126-181`）和 `FLT_*`（`ob_trace.h:27-57`）定义在这些单元里，由该单元的译者按 §2.13 写成同名小写宏；本文 §2.5.9 规定它们的形状，保证调用点统一 |
| `src/oblib/lib/profile/ob_trace_id.h`（`ObCurTraceId`） | `translate` | 日志行头要打印 trace id；本模块经启动时注册的钩子读取（RULEBOOK 0.5 "ObCurTraceId…同理"），§2.5.11 |
| `src/oblib/lib/ob_lib_config.h`（`is_trace_log_enabled`） | `translate` | 跟踪宏用到 |
| `src/oblib/lib/utility/ob_common_utility.h`（`ObBasicTimeGuard`） | 由其所属模块决定 | `do_log_message` 统计格式化耗时（`ob_log.h:1228-1358`），见 §8 Q5 |
| `src/oblib/lib/utility/ob_fast_convert.h`（`ObFastFormatInt`） | 06 号模块 | `logdata_print_obj(int64/uint64)` 用它（`ob_log.cpp:212-238`） |
| `src/oblib/lib/string/ob_sensitive_string.h`（`ObSensitiveString`） | 06 号模块 | `ks!` |
| 容器的 `to_string`（`ob_iarray.h:67-85`、`ob_array.h:741-770`、`ob_fixed_array.h:281`） | 05 号模块 | 必须用本模块的 `ObToString` 及其切片钩子实现（§2.4.4、§3.7.5） |

### 1.3 规模（便于排期与审查）

| 构造 | 约数（`count_tokens.py`） | 说明 |
|---|---|---|
| `OB_FAIL`/`OB_SUCC` | 99,951 / 31,388 | 来自 conventions.md §2.1 的原始口径 |
| `LOG_WARN`/`LOG_ERROR`/`LOG_INFO`/`LOG_DEBUG`/`LOG_TRACE` | 5,286 / 2,827 / 2,790 / 705 / 667 | conventions.md §2.2 |
| `XXX_LOG(level, …)` | 13,964 | 同上；`OB_LOG(` 778、`_OB_LOG(` 286 |
| `K(`/`K_(`/`KR(`/`KP(`/`KPC(` | 49,192 / 12,461 / 3,212 / 2,305 / 2,784 | 另有 `KP_(` 733、`KPC_(` 431、`KCSTRING(` 208、`KERRMSG` 81 |
| `TO_STRING_KV(`/`VIRTUAL_…`/`INHERIT_…`/`TO_STRING_EMPTY(` | 2,653 / 187 / 270 / 22 | 另有 `DEF_TO_STRING(` 126、`J_KV(` 401、`BUF_PRINTF(` 1,146 |
| `LOG_USER_ERROR(`/`_WARN(`/`_NOTE(` | 2,807 / 127 / 8 | 另有 `LOG_MYSQL_USER_ERROR(` 11、`FORWARD_USER_*` 30 |
| `databuff_printf(`/`snprintf(`/`append_fmt(`/`fprintf(` | 1,980 / 446 / 822 / 176 | 带字面格式串的 printf 族调用共 3,968 处（conventions.md §2.6） |
| `REACH_TIME_INTERVAL(`/`REACH_THREAD_TIME_INTERVAL(`/`TC_REACH_TIME_INTERVAL(` | 244 / 47 / 42 | 另有 `REACH_COUNT_INTERVAL(` 23、`EXECUTE_COUNT_PER_SEC(` 8、`REACH_COUNT_PER_SEC(` 3 |
| `OB_ASSERT(`/`abort_unless(`/`ob_abort(` | 762 / 251 / 140 | |
| `OB_E(`/`ERRSIM_POINT_DEF(`/`EVENT_CALL(` | 192 / 99 / 38 | 不在 `#ifdef ERRSIM` 里的：`OB_E` 92 处、`ERRSIM_POINT_DEF` 92 处（`errsim_scope.py`） |
| `ob_errno.def` | 1,548 条 `DEFINE_*` | 858 `DEFINE_ERROR`、326 `_EXT`、172 `_DEP`、190 `_EXT_DEP`、2 `DEFINE_OTHER_MSG_FMT`；去重后 1,545 个名字（`OB_ERR_ARGUMENT_SHOULD_CONSTANT_OR_GROUP_EXPR` 在 `:1868`、`:1872` 定义了两次，值相同）；带参数的用户消息 470 条 |

## 2. Rust API

### 2.0 布局

| 位置 | 内容 | 依据 |
|---|---|---|
| `rust/port/ob_macros`（proc-macro） | `ob_printf!`；`to_string_kv!`、`virtual_to_string_kv!`、`inherit_to_string_kv!`、`to_string_empty!`、`to_string_kv_with_helper!` | RULEBOOK §1"格式化"行、0.3。TO_STRING 系列必须是过程宏：`macro_rules!` 生成的 `fn to_string(&self, …)` 里的 `self` 与调用方写在实参里的 `self.a_` 卫生上下文不同，编译不过；过程宏用 `Span::call_site()` 生成签名即可 |
| `rust/port/oblib/src/error/` | `ObError`、`ObResult`、`ObResultExt`、`ObErrcode`、错误码常量与各张表（`include!("../generated/ob_errno.rs")`）、`user_msg`、`mysql_errno`、`ob_abort`/`abort_unless`、`ScopedLambda`、`errsim` 子模块 | RULEBOOK §4 归属表 |
| `rust/port/oblib/src/fmt/` | printf 引擎、`ObFmtArgs`、`ObBuf`、`ObToString` 及基本类型实现、`databuff_*`、`ObCStringHelper`、十六进制工具、打印包装类型 | RULEBOOK §4："`ob_printf!` → `ob_macros`，运行时在 `oblib::fmt`" |
| `rust/port/oblib/src/log/` | `ObLogger`、模块表、级别表、写线程、轮转、压缩、限流、KV、警告缓冲区、DBA 事件、`lbt` | RULEBOOK §4："日志宏 → `oblib::log`" |
| `rust/port/oblib/src/log/sys/` | 少量 libc 调用（`dup2`、`statfs`、`localtime_r`/`timezone`、`strerror_r`），见 §4；能否放 unsafe 取决于修订 #6 | — |
| `rust/port/oblib/src/generated/` | `ob_errno.rs`、`mysql_errno.rs`（errno build.rs 的产物）；`errsim_registry.rs`（脚本收集全部 `errsim_point!` 定义，§2.3） | RULEBOOK 0.13"生成物的位置" |
| `oblib` 根 | 全部 `macro_rules!`（`#[macro_export]`）；`lib.rs` 里有 `extern crate self as oblib;`，使过程宏输出的 `::oblib::…` 路径在 oblib 自身里也能解析 | RULEBOOK §4"宏一律 `#[macro_export]`" |

进 `oblib::prelude` 的（交 06 号模块写进 prelude）：`error::*`（含全部 `OB_*` 常量）、`ObResultExt`、`ObErrcode`、`fmt::{ObBuf, ObBufExt, ObToString, ObFmtArgs}`、`log::{ObLogMod, ObLogger, ObWarningBufferRef}`，以及本文全部宏的 `pub use crate::{…}`。

### 2.1 `oblib::error`

```rust
use core::num::NonZeroI32;

/// C++：`int ret` 里的非零错误码（RULEBOOK §1"错误"行）。4 字节；`ObResult<()>` 也是 4 字节。
/// 任何非零 i32 都可以表示，包括正数（errsim 可以注入任意整数，`ob_tracepoint.h:264`）。
#[repr(transparent)]
#[derive(Copy, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ObError(NonZeroI32);

/// C++：返回 `int ret` 的函数。`T` 只在 1d 的基础设施 API 里不为 `()`（如 `lock_until -> ObResult<Guard>`）。
pub type ObResult<T = ()> = Result<T, ObError>;

/// C++：`OB_SUCCESS`。它不是 `ObError`；作为整数错误码出现的地方（日志的 errcode、`ob_mysql_errno(OB_SUCCESS)`、结构体里的 `int` 字段）用这个常量。
pub const OB_SUCCESS: i32 = 0;

impl ObError {
    /// 常量初始化专用；code == 0 时编译期报错（`Option::unwrap` 在 const 里已稳定）。
    pub const fn new_const(code: i32) -> ObError;
    pub const fn new(code: i32) -> Option<ObError>;
    /// `ret = code;`：0 → `Ok(())`，非 0 → `Err`
    pub const fn from_code(code: i32) -> ObResult;
    pub const fn code(self) -> i32;
    /// RULEBOOK §2.1 错误恢复规则：`self == OB_ALLOCATE_MEMORY_FAILED`
    pub const fn is_unrecoverable(self) -> bool;
}

/// C++：`OB_X == ret`、`ret != OB_X`、`ret` 当整数用。
pub trait ObResultExt {
    fn is(&self, e: ObError) -> bool;          // ret.is(OB_X)
    fn is_unrecoverable(&self) -> bool;        // Err(OB_ALLOCATE_MEMORY_FAILED)
    fn code(&self) -> i32;                     // Ok → 0
}
impl<T> ObResultExt for ObResult<T> { /* … */ }

/// 错误码实参：日志宏的 errcode、`forward_user_*!`、各张表的查询函数都接受它。
pub trait ObErrcode { fn ob_errcode(&self) -> i32; }
impl<T> ObErrcode for ObResult<T> {}   // Ok → 0
impl ObErrcode for ObError {}
impl ObErrcode for i32 {}               // 结构体里存的 int 错误码、ob_errpkt_errno 的结果等

/// C++：`ob_abort()`（ob_abort.cpp:31-44）。先往 stderr 写 "OB_ABORT, tid: %ld, lbt: %s\n"，再 abort。
pub fn ob_abort() -> !;
/// C++：`abort_unless(bool)`（ob_abort.h:35-43）：不记日志，条件为假时 `ob_abort()`。
#[inline] pub fn abort_unless(result: bool);

/// C++：`ScopedLambda`（ob_defer.h:28-56）。`DEFER`/`DEFER_C`/`NAMED_DEFER` 的守卫。
pub struct ScopedLambda<F: FnMut()> { fn_: F, active_: bool }
impl<F: FnMut()> ScopedLambda<F> {
    pub fn new(f: F) -> Self;
    pub fn run_and_expire(&mut self);
    pub fn activate(&mut self);
    pub fn deactivate(&mut self);
}
impl<F: FnMut()> Drop for ScopedLambda<F> { fn drop(&mut self) { /* active_ 时执行 fn_ */ } }
/// `DEFER` 的闭包要改写同一作用域里还在使用的变量时用它：守卫持有 `&mut T` 并 Deref 到 T，
/// 作用域结束时以 `&mut T` 调用闭包。只持有借用、不进线程局部，符合 RULEBOOK 0.5。
pub struct ScopedLambdaMut<'a, T: ?Sized, F: FnMut(&mut T)> { target_: &'a mut T, fn_: F, active_: bool }
impl<'a, T: ?Sized, F: FnMut(&mut T)> core::ops::Deref for ScopedLambdaMut<'a, T, F> { type Target = T; /* … */ }
impl<'a, T: ?Sized, F: FnMut(&mut T)> core::ops::DerefMut for ScopedLambdaMut<'a, T, F> { /* … */ }

// ---- 生成物 include!("../generated/ob_errno.rs")，形状如下 ----
pub const OB_MAX_ERROR_CODE: i32 = 65535;          // lib/ob_errno.h（gen_errno.pl:227）
pub const OB_LAST_ERROR_CODE: i32 = /* 最小的错误码 */ -38105;   // gen_errno.pl:31,145
pub const OB_ERR_SQL_START: i32 = -5000;            // gen_errno.pl:146
pub const OB_ERR_SQL_END: i32 = -5999;              // gen_errno.pl:147
pub const OB_ERROR: ObError = ObError::new_const(-4000);
pub const OB_ITER_END: ObError = ObError::new_const(-4008);
// …… 1,545 个，含原 lib/ob_errno.h 与 share/ob_errno.h 两部分（Rust 里不再区分 DEP）
pub const OB_ERR_DATA_TOO_LONG_MSG_FMT_V2: ObError = OB_ERR_DATA_TOO_LONG;   // DEFINE_OTHER_MSG_FMT（ob_errno.def:612）
pub const OB_ERR_INVALID_DATE_MSG_FMT_V2: ObError = OB_INVALID_DATE_VALUE;   // ob_errno.def:250
/// `${name}__USER_ERROR_MSG`（gen_errno.pl:167-172）：保留为字符串常量，供 C++ 代码里直接引用它的地方
pub const OB_ERR_BAD_DATABASE__USER_ERROR_MSG: &str = "Unknown database '%.*s'";
pub static g_all_ob_errnos: [i32; 1545] = [0, -4000, -4001, /* … */ -38105];   // gen_errno.pl:107,357
pub fn ob_error_name(err: impl ObErrcode) -> &'static str;
pub fn ob_error_cause(err: impl ObErrcode) -> &'static str;
pub fn ob_error_solution(err: impl ObErrcode) -> &'static str;
pub fn ob_mysql_errno(err: impl ObErrcode) -> i32;
pub fn ob_mysql_errno_with_check(err: impl ObErrcode) -> i32;
pub fn ob_sqlstate(err: impl ObErrcode) -> &'static str;
pub fn ob_strerror(err: impl ObErrcode) -> &'static str;
pub fn ob_str_user_error(err: impl ObErrcode) -> Option<&'static str>;     // C++ 返回 NULL 的情况 → None
pub fn ob_errpkt_errno(err: impl ObErrcode) -> i32;
pub fn ob_errpkt_strerror(err: impl ObErrcode) -> &'static str;
pub fn ob_errpkt_str_user_error(err: impl ObErrcode) -> Option<&'static str>;

pub mod mysql_errno {   // generated/mysql_errno.rs，读 src/share/mysql_errno.h
    pub const ER_HASHCHK: i32 = 1000;
    // …… 1,021 个 ER_*、9 个 WARN_*
    pub const WARN_DATA_TRUNCATED: i32 = 1265;
}
pub use mysql_errno::*;

/// 每条消息一个带类型的格式化函数（RULEBOOK §2.3、0.13）；1,547 个（1,545 个错误码加 2 个 OTHER_MSG_FMT）。
/// 参数类型由格式串决定（§3.2.4 的表）；`loc` 是调用点，由 `log_user_*!` 填（§3.13.2）。
pub mod user_msg {
    use crate::log::{ObLogLoc, UserMsgLevel};
    use crate::fmt::ObPrintfStr;
    #[allow(non_snake_case)]
    pub fn OB_ERR_BAD_DATABASE(level: UserMsgLevel, loc: &ObLogLoc, p0: i32, s0: impl ObPrintfStr);
    #[allow(non_snake_case)]
    pub fn OB_TABLE_NOT_EXIST(level: UserMsgLevel, loc: &ObLogLoc, s0: impl ObPrintfStr, s1: impl ObPrintfStr);
    #[allow(non_snake_case)]
    pub fn OB_TIMEOUT(level: UserMsgLevel, loc: &ObLogLoc, ld0: core::ffi::c_long);
    #[allow(non_snake_case)]
    pub fn OB_ERR_UNEXPECTED(level: UserMsgLevel, loc: &ObLogLoc, s0: impl ObPrintfStr);
    #[allow(non_snake_case)]
    pub fn OB_ALLOCATE_MEMORY_FAILED(level: UserMsgLevel, loc: &ObLogLoc);    // 无参数
    // ……
}
```

### 2.2 错误链与断言宏（`oblib` 根，`#[macro_export]`）

下面给出每个宏的**确切展开**（实现照抄即可）。所有宏都显式接收 `ret`（RULEBOOK §2.1：`macro_rules!` 的卫生规则不允许隐式捕获局部变量）；日志宏在调用点解析 `LOG_PREFIX`（条目名不受卫生规则约束，§2.5.4）。

#### 2.2.1 `ob_succ!` / `ob_fail!` / `cover_succ!` / `failedx!` / `false_it!`

```rust
/// OB_SUCC(e)（ob_macro_utils.h:650）→ ob_succ!(ret = e)；OB_FAIL(e)（:651）→ ob_fail!(ret = e)；
/// OB_TMP_FAIL(e)（:652）→ ob_fail!(tmp_ret = e)。
/// 第二种写法给返回 ObResult<T>（T≠()）的 1d API 用：成功时把值写进 `out`（一个 Option<T> 左值）。
#[macro_export]
macro_rules! ob_fail {
    ($ret:ident = $e:expr) => {{ $ret = $e; $ret.is_err() }};
    ($ret:ident = $e:expr => $out:expr) => {{
        match $e { Ok(__v) => { $out = Some(__v); $ret = Ok(()); false }
                   Err(__e) => { $ret = Err(__e); true } }
    }};
}
#[macro_export]
macro_rules! ob_succ {
    ($ret:ident = $e:expr) => {{ $ret = $e; $ret.is_ok() }};
    ($ret:ident = $e:expr => $out:expr) => { !$crate::ob_fail!($ret = $e => $out) };
}

/// COVER_SUCC(x)（:653）：`ret = COVER_SUCC(tmp_ret)` → `ret = cover_succ!(ret, tmp_ret)`
#[macro_export]
macro_rules! cover_succ { ($ret:ident, $x:expr) => { if $ret.is_ok() { $x } else { $ret } }; }

/// FAILEDx(e)（:451）展开成 `if (OB_FAIL(ret)) {} else if (OB_FAIL(e)) …`：ret 已失败时，then 和后续 else 都不执行。
/// 所以它包住整条 if 链：
///   if (FAILEDx(f())) { A } else { B }   →   failedx!(ret = f(), { A } else { B });
#[macro_export]
macro_rules! failedx {
    ($ret:ident = $e:expr, $then:block $(else $($rest:tt)+)?) => {
        if $ret.is_err() {} else if $crate::ob_fail!($ret = $e) $then $(else $($rest)+)?
    };
}

/// FALSE_IT(stmt) → false_it!(stmt)：`{ stmt; false }`（RULEBOOK §2.1）
#[macro_export]
macro_rules! false_it { ($($s:tt)*) => {{ $($s)*; false }}; }
```

#### 2.2.2 `ck!` / `ox!` / `oz!` / `ozx1!` / `ozx2!` / `ov!`

```rust
/// CK(c1, c2, …)（ob_check_macros.h:22-66）：ret 成功时依次检查，第一个为假的条件把 ret 置成
/// OB_ERR_UNEXPECTED（若原来成功）并记 WARN "invalid arguments"；KV 的键是条件的字符串，值是**再次求值**的条件
/// （C++ 的 LOG_WARN 实参只在 WARN 可打印时求值，这里同样经 log_warn! 惰性求值，保留这一行为）。
#[macro_export]
macro_rules! ck {
    ($ret:ident, $($c:expr),+ $(,)?) => { if $ret.is_ok() { $crate::__ck_chain!($ret; $($c),+) } };
}
/// CK_n 是 if/else-if 链（:22-53）：宏调用之后不能接 else，所以 if 写在 __ck_chain 里，失败分支放进 __ck_0
#[doc(hidden)] #[macro_export]
macro_rules! __ck_chain {
    ($ret:ident; $c:expr) => { if !($c) { $crate::__ck_0!($ret, $c) } };
    ($ret:ident; $c:expr, $($rest:expr),+) => {
        if !($c) { $crate::__ck_0!($ret, $c) } else { $crate::__ck_chain!($ret; $($rest),+) }
    };
}
/// CK_0 的失败分支（:55-61）
#[doc(hidden)] #[macro_export]
macro_rules! __ck_0 {
    ($ret:ident, $c:expr) => {
        if $ret.is_ok() { $ret = Err($crate::error::OB_ERR_UNEXPECTED); }
        $crate::log_warn!($ret, "invalid arguments", $crate::kv!(stringify!($c), $c));
    };
}

/// OX(stmt)（:70-73）
#[macro_export]
macro_rules! ox { ($ret:ident, $($s:tt)*) => { if $ret.is_ok() { $($s)*; } }; }

/// OZ(f(a), a2, a3)（:87-112）→ oz!(ret, f(a); a2, a3)。消息是 "fail to exec " 加函数表达式的 stringify。
#[macro_export]
macro_rules! oz {
    ($ret:ident, $f:expr $(; $($kv:expr),* $(,)?)?) => {
        if $ret.is_ok() {
            if $crate::ob_fail!($ret = $f) {
                $crate::log_warn!($ret, concat!("fail to exec ", stringify!($f)),
                                  $crate::k!($ret) $($(, $crate::k!($kv))*)?);
            }
        }
    };
}
/// OZX1(f, OB_X, args)（:121-137）：失败码等于 OB_X 时降为 DEBUG
#[macro_export]
macro_rules! ozx1 { ($ret:ident, $f:expr, $c1:expr $(; $($kv:expr),* $(,)?)?) => { /* 同 oz!，`if $ret.is($c1) { log_debug!(…) } else { log_warn!(…) }` */ }; }
/// OZX2(f, OB_X, OB_Y, args)（:145-162）
#[macro_export]
macro_rules! ozx2 { ($ret:ident, $f:expr, $c1:expr, $c2:expr $(; $($kv:expr),* $(,)?)?) => { /* … */ }; }

/// OV(…)（:164-186）：OV() → (false, OB_ERR_UNEXPECTED)；OV(c) → (c, OB_ERR_UNEXPECTED)；
/// OV(c, OB_X, args…) → ov!(ret, c, OB_X; args…)。消息 "fail to check (" #c ")"，KV 是 ret 加 args。
#[macro_export]
macro_rules! ov {
    ($ret:ident) => { $crate::ov!($ret, false, $crate::error::OB_ERR_UNEXPECTED) };
    ($ret:ident, $c:expr) => { $crate::ov!($ret, $c, $crate::error::OB_ERR_UNEXPECTED) };
    ($ret:ident, $c:expr, $code:expr $(; $($kv:expr),* $(,)?)?) => {
        if $ret.is_ok() {
            if !($c) {
                $ret = Err($code);
                $crate::log_warn!($ret, concat!("fail to check (", stringify!($c), ")"),
                                  $crate::k!($ret) $($(, $crate::k!($kv))*)?);
            }
        }
    };
}
```

#### 2.2.3 断言

```rust
/// OB_ASSERT(x)（ob_macro_utils.h:704-711）与 ob_assert(x)/ob_release_assert(x)（:744-756，abort 之后的 exit(1) 不可达）。
/// 小写后同名，语义相同，都译成 ob_assert!。
#[macro_export]
macro_rules! ob_assert {
    ($x:expr) => {
        if !($x) {
            $crate::ob_log_fmt!(NONE, ERROR, $crate::error::OB_ERROR, "assert fail, exp=%s", stringify!($x));
            $crate::backtrace_ret!(ERROR, $crate::error::OB_ERROR, true, "assert fail");
            $crate::error::ob_abort();
        }
    };
}
/// OB_SAFE_ASSERT(x)（:713-732）：记两行 ERROR，不 abort；cfg(feature = "ENABLE_DEBUG_LOG") 时再 debug 断言
#[macro_export] macro_rules! ob_safe_assert { ($x:expr) => { /* … */ }; }
/// OB_ASSERT_MSG(x, fmt, args…)（:734-742）：第二行是 BACKTRACE_RET(ERROR, OB_ERROR, 1, fmt, args…)；随后 assert(v)
/// 在 NDEBUG 下为空 → debug_assert!(v)
#[macro_export] macro_rules! ob_assert_msg { ($x:expr, $fmt:literal $(, $a:expr)* $(,)?) => { /* … */ }; }
/// BACKTRACE(LEVEL, cond, fmt, args…)（:685-692）→ backtrace!(code, LEVEL, cond, "fmt", args…)：
/// C++ 隐式读 ret（_OB_LOG 对 WARN/ERROR 取 ret），所以 Rust 显式传错误码。
/// 打印 `_OB_LOG(LEVEL, fmt " BACKTRACE:%s", args…, lbt())`。
#[macro_export] macro_rules! backtrace { ($code:expr, $lv:ident, $c:expr, $fmt:literal $(, $a:expr)* $(,)?) => { /* … */ }; }
/// BACKTRACE_RET(LEVEL, errcode, cond, fmt, args…)（:694-701）
#[macro_export] macro_rules! backtrace_ret { ($lv:ident, $code:expr, $c:expr, $fmt:literal $(, $a:expr)* $(,)?) => { /* … */ }; }
/// OB_SAFE_ABORT()（:758-762）：只有 feature "ENABLE_DEBUG_LOG" 时 ob_abort()
#[macro_export] macro_rules! ob_safe_abort { () => { /* … */ }; }
```

#### 2.2.4 `defer!` 一族、`retry_func!`、`min!`/`max!`

```rust
/// DEFER(stmts)（ob_defer.h:64-65）：闭包按引用捕获（同 C++ 的 [&]），作用域结束时执行。
/// 第二种写法：闭包要改写作用域里仍在使用的变量时，守卫持有 &mut 并 Deref 到它，之后通过守卫访问。
///   DEFER(exec_ctx.get_sql_ctx()->is_prepare_protocol_ = is_ps_backup);    (ob_expr_in.cpp:503)
///   → let mut sql_ctx = defer!(&mut *sql_ctx => |c| { c.is_prepare_protocol_ = is_ps_backup; });
#[macro_export]
macro_rules! defer {
    (&mut $t:expr => |$v:ident| $body:block) => { $crate::error::ScopedLambdaMut::new(&mut $t, |$v| $body) };
    ($($body:tt)*) => { let __ob_defer = $crate::error::ScopedLambda::new(|| { $($body)*; }); };
}
/// DEFER_C(stmts)（:66-67）：按值捕获 → move 闭包
#[macro_export] macro_rules! defer_c { ($($body:tt)*) => { let __ob_defer = $crate::error::ScopedLambda::new(move || { $($body)*; }); }; }
/// NAMED_DEFER(name, stmts)（:61-63）：守卫有名字，可 deactivate()/run_and_expire()
#[macro_export] macro_rules! named_defer { ($name:ident, $($body:tt)*) => { let mut $name = $crate::error::ScopedLambda::new(|| { $($body)*; }); }; }

/// RETRY_FUNC_ON_ERROR(err_no, stop_flag, var, func, args…)（ob_macro_utils.h:665-680）
///   → retry_func_on_error!(ret, err_no, stop_flag, var.func(args…))
/// RETRY_FUNC(stop_flag, var, func, args…)（:662-663）→ retry_func!(ret, stop_flag, var.func(args…))（err_no = OB_TIMEOUT）
#[macro_export]
macro_rules! retry_func_on_error {
    ($ret:ident, $err:expr, $stop:expr, $call:expr) => {
        if $ret.is_ok() {
            $ret = Err($err);
            while $ret.is($err) && !($stop) { $ret = Ok(()); $ret = $call; }
            if $stop { $ret = Err($crate::error::OB_IN_STOP_STATE); }
        }
    };
}
#[macro_export] macro_rules! retry_func { ($ret:ident, $stop:expr, $call:expr) => { $crate::retry_func_on_error!($ret, $crate::error::OB_TIMEOUT, $stop, $call) }; }

/// MIN/MAX/MIN3/MAX3（:466-476）：照抄三目运算，被选中的实参**求值两次**，与 C 宏相同；
/// 对 f64 的 NaN 语义也与 C 相同（与 f64::min/max 不同）。
#[macro_export] macro_rules! min { ($x:expr, $y:expr) => { if ($x) < ($y) { $x } else { $y } }; }
#[macro_export] macro_rules! max { ($x:expr, $y:expr) => { if ($x) > ($y) { $x } else { $y } }; }
#[macro_export] macro_rules! min3 { ($x:expr, $y:expr, $z:expr) => { $crate::min!($crate::min!($x, $y), $z) }; }
#[macro_export] macro_rules! max3 { ($x:expr, $y:expr, $z:expr) => { $crate::max!($crate::max!($x, $y), $z) }; }
```

#### 2.2.5 限流宏（`ob_macro_utils.h:767-916`）

每个调用点各有一份状态（`static` 放在宏展开出的块里，RULEBOOK §2.2）。时间源是 `ObClockGenerator::getClock()`（§8 Q5）。

```rust
#[macro_export] macro_rules! reach_time_interval { ($i:expr) => {{
    static LAST_TIME: ::core::sync::atomic::AtomicI64 = ::core::sync::atomic::AtomicI64::new(0);
    /* 展开见 §3.12：old = load；(i + load) < cur && CAS(old → cur) */
}}; }
#[macro_export] macro_rules! reach_time_interval_no_instant { ($i:expr) => {{ /* 外层 reach_time_interval! + static AtomicBool FIRST_CALL */ }}; }
#[macro_export] macro_rules! reach_time_interval_range { ($i:expr, $j:expr) => {{ /* static LAST_TIME 初值是首次求值时的时钟，见 §3.12 */ }}; }
#[macro_export] macro_rules! reach_count_interval { ($i:expr) => {{ /* static COUNT: AtomicI64；fetch_add(1)+1 后 % i == 0 */ }}; }
#[macro_export] macro_rules! reach_thread_time_interval { ($i:expr) => {{ /* thread_local! Cell<i64>，常量初始化为 i64::MIN 表示"未初始化" */ }}; }
#[macro_export] macro_rules! tc_reach_time_interval { ($i:expr) => {{ /* thread_local! Cell<i64> 初值 0 */ }}; }
#[macro_export] macro_rules! tc_reach_count_interval { ($i:expr) => {{ /* thread_local! Cell<i64> 初值 0 */ }}; }
/// REACH_COUNT_PER_SEC / EXECUTE_COUNT_PER_SEC（:834-859、:891-916）：128 位 CAS 改成每个调用点一把 Latch（RULEBOOK §2.7"128 位 CAS"行；
/// EXECUTE_COUNT_PER_SEC 同理，见修订 #11）
#[macro_export] macro_rules! reach_count_per_sec { ($i:expr) => {{ /* static Latch<(u64 lo, u64 hi)> */ }}; }
#[macro_export] macro_rules! execute_count_per_sec { ($i:expr) => {{ /* 同上 */ }}; }
```

#### 2.2.6 `ob_macro_utils.h` 其余宏的去向

`ob_macro_utils.h` 整体处置为 `replace:error`，下表是不在本模块提供宏、由翻译者直接写 Rust 的部分（全部是"一构造一译法"，对应 RULEBOOK 的条目已注明）。

| C++（行号） | Rust | 说明 |
|---|---|---|
| `OB_LIKELY(x)`/`OB_UNLIKELY(x)`（626-627） | `x` | 只是分支预测提示 |
| `OB_ISNULL`/`OB_NOT_NULL`/`IS_NOT_INIT`/`IS_INIT`/`INIT_SUCC`（654-659） | RULEBOOK §2.1 | |
| `ARRAY_FOREACH(arr, i)`/`_N`/`_X`/`_NORET`（509-513） | `for i in 0..arr.count() { if ret.is_err() { break; } … }`；`_NORET` 不检查 ret；`_X` 的额外条件同样放在循环体开头 | count 在进入循环时求值一次，与 C++ 相同；RULEBOOK §2.13 |
| `FOREACH`/`FOREACH_CNT`（493-506） | `for it in c.iter()` / `for it in c.iter()`（越界语义同 `at`） | |
| `ARRAYSIZEOF(a)`/`SIZEOF(x)`（629-630） | `a.len() as i64` / `core::mem::size_of_val(&x) as i64` | |
| `UNUSED`/`UNUSEDx`/`IGNORE_RETURN`（598-604） | `let _ = v;` | RULEBOOK §2.13 |
| `DISALLOW_COPY_AND_ASSIGN`/`DISABLE_COPY_ASSIGN`（606-612） | 不 derive `Clone`/`Copy` | RULEBOOK §2.13 |
| `OB_INLINE`/`OB_NOINLINE`（614-615） | `#[inline(always)]`/`#[inline(never)]` | |
| `CACHE_ALIGN_SIZE`/`CACHE_ALIGNED`（617-623） | 02 号 `CacheAligned<T>` | RULEBOOK §2.7 |
| `CONTAINER_OF`/`ADDRESS_OF`（632-640） | 不译；按 RULEBOOK §2.4 改成随身携带下标或 id | |
| `CHAR_CARRAY_INIT(a)`（642-648） | `a[0] = 0;` | 对齐构建定义了 NDEBUG |
| `LST_DO`/`LST_DO_CODE`/`LST_DEFINE`/`ARGS_NUM`/`CONCAT`/`SELECT`/`APPLY*`/`EXPAND`/`IF_PAREN`/`CAT`/`MSTR`/`ONE_TO_HUNDRED`/`FIRST`/`REMOVE_FIRST`（与预处理元编程有关的其余宏） | 不对应 Rust 宏；用到它们的宏按各自的规则展开（如 `LST_DO_CODE(OB_UNIS_ENCODE, a, b)` 由 06 号的序列化宏处理，RULEBOOK §2.11） | |
| `DECLARE_ENUM`/`DEFINE_ENUM_FUNC`（553-586） | `declare_enum!(Type, get_x_string, get_x_value; A = 0, B, C = 1024)`：生成 RULEBOOK §2.12 的新类型枚举加关联常量和模块级常量，以及两个函数（`get_x_string(v) -> Option<&'static str>`，未知值记 `LIB_LOG_RET(WARN, OB_ERR_UNEXPECTED, "unknown" #type "value", "value", v)`；`get_x_value(&[u8]) -> Type`，空串记 WARN 返回 0，按 `case_compare` 查找，找不到记 WARN 返回 0） | Rust 宏不能拼接标识符，所以函数名由调用方写全 |
| `EXTERN_C_BEGIN/END`（588-594） | 不译 | |
| `TLOCAL`（918） | `thread_local!` | RULEBOOK §2.13 |
| `OB_WEAK_SYMBOL` | RULEBOOK 0.4"weak 符号" | |

### 2.3 `oblib::error::errsim`（错误注入）

```rust
use core::sync::atomic::AtomicI64;

/// C++ EventItem（ob_tracepoint.h:223-311）。C++ 里除 occur_ 外都是普通字段、跨线程读写（数据竞争），
/// 这里全部改成原子量，标 FIXED(port)（§3.4）。
pub struct EventItem {
    pub no_: i64,
    pub name_: &'static str,
    pub describe_: &'static str,
    occur_: AtomicI64,
    trigger_freq_: AtomicI64,
    error_code_: AtomicI64,
    cond_: AtomicI64,
}
/// TP_SET_EVENT 构造的临时 EventItem 里有用的四个字段（:135-143、:251-257）
#[derive(Copy, Clone, Default)]
pub struct EventItemSetting { pub error_code_: i64, pub occur_: i64, pub trigger_freq_: i64, pub cond_: i64 }
impl EventItem {
    pub const fn new(no: i64, name: &'static str, describe: &'static str) -> Self;
    pub fn set_event(&self, other: &EventItemSetting);
    /// int call(const int64_t v)（:259，先声明，保留原名）
    pub fn call(&self, v: i64) -> i32;
    /// int call()（:260）：按 RULEBOOK §4 的重载规则，无参版本加参数个数后缀
    pub fn call_0(&self) -> i32;
    pub fn get_event_code(&self) -> i64;
}
/// C++ NamedEventItem（:313-341）；`operator int()` → `ob_e!(item)`
pub struct NamedEventItem { pub item_: EventItem }
impl NamedEventItem { pub const fn new(no: i64, name: &'static str, describe: &'static str) -> Self; }

/// C++ class EventTable（:343-408）的静态成员和静态函数。Rust 没有关联静态量，所以 EventTable 是模块，
/// 调用处照写 `EventTable::EN_X`、`EventTable::set_event(…)`。
#[allow(non_snake_case)]
pub mod EventTable {
    /// set_event(int64_t no, const EventItem &)（:350）
    pub fn set_event(no: i64, item: &super::EventItemSetting) -> crate::error::ObResult;
    /// set_event(const char *name, const EventItem &)（:371）：§4 的重载改名
    pub fn set_event_char(name: Option<&[u8]>, item: &super::EventItemSetting) -> crate::error::ObResult;
    pub fn global_item_list() -> &'static [&'static super::NamedEventItem];
    // 322 个全局点：pub static EN_1: NamedEventItem = NamedEventItem::new(1, "EN_1", "");  ……（ob_tracepoint_def.h）
}
/// C++ get_tp_switch()（ob_tracepoint.cpp:25-29，线程局部 bool）与 TP_SWITCH_GUARD（ob_tracepoint.h:81）
pub fn get_tp_switch() -> bool;
pub struct ObSwitchGuard { oldval_: bool }   // new(v) 记下旧值并设新值；Drop 时恢复
impl ObSwitchGuard { pub fn new(newval: bool) -> Self; }
```

```rust
/// OB_E(name) OB_SUCCESS（:127-128）→ ob_e!(name)；OB_E(name) expr → ob_e!(name; expr)；
/// OB_E(name, cond) … → ob_e!(name, cond) / ob_e!(name, cond; expr)。结果是 ObResult：
/// 注入码非 0 → Err(注入码)，否则 → Ok(()) 或 expr 的值。
/// `if (OB_FAIL(ERRSIM_X))`（operator int）→ `if ob_fail!(ret = ob_e!(ERRSIM_X))`。
#[macro_export] macro_rules! ob_e { … }
/// EVENT_CALL(name, …)（:108）→ event_call!(name)/event_call!(name, cond)：i32
#[macro_export] macro_rules! event_call { … }
/// ERRSIM_POINT_DEF(name)/ERRSIM_POINT_DEF(name, "describe")（:112-115）→ errsim_point!(NAME) / errsim_point!(NAME, "describe")：
/// 展开成 `static NAME: NamedEventItem = NamedEventItem::new(-1, "NAME", "describe");`；
/// 1d 的脚本扫描全部 errsim_point! 生成 generated/errsim_registry.rs（§3.4）。
#[macro_export] macro_rules! errsim_point { … }
/// TP_SET_EVENT(name, error_in, occur, trigger_freq[, cond])（:135-143）
#[macro_export] macro_rules! tp_set_event { … }
/// OB_I(key, args…) expr（:71-76）：只有 cfg(debug_assertions) 时生效；对齐构建（NDEBUG）展开为 expr
#[macro_export] macro_rules! ob_i { ($key:ident; $e:expr) => { … }; }
/// GLOBAL_ERRSIM_POINT_DEF(no, name, describe) 的表（ob_tracepoint_def.h）→ 一次调用：
#[macro_export] macro_rules! global_errsim_point_defs { ($(($no:expr, $name:ident, $desc:expr)),* $(,)?) => { … }; }
```

### 2.4 `oblib::fmt` 与 `ob_printf!`

#### 2.4.1 过程宏（`ob_macros`）

```rust
/// ob_printf!("C 格式串", args…)      → ::oblib::fmt::ObFmtArgs<'_>，Libc 方言（等价于直接调 vsnprintf）
/// ob_printf!(@ob "C 格式串", args…)  → Ob 方言（等价于 databuff_vprintf/logdata_vprintf/ObSqlString::vappend）
/// 格式串必须是字面量（含相邻字面量拼接与 concat!）；编译期解析、核对实参个数与类型（§3.5.3），
/// 拒绝平台间行为不一致或未定义的组合（§3.5.4），并生成静态的分段表。结果借用实参临时量，只能在同一条语句里用（同 format_args!）。
#[proc_macro] pub fn ob_printf(input: TokenStream) -> TokenStream;

/// TO_STRING_KV(K_(a), K(b), "c", d)（ob_print_utils.h:1038）→ to_string_kv!(Self; "a" => self.a_, "b" => b, "c" => d)
/// 生成 `impl ObToString for Self { fn to_string(&self, buf: &mut ObBuf) -> i64 { … } }`，展开见 §3.7.4。
#[proc_macro] pub fn to_string_kv(input: TokenStream) -> TokenStream;
/// VIRTUAL_TO_STRING_KV（:1040）：同 to_string_kv!；类的 `<类名>Virt` trait 以 ObToString 为 supertrait（§3.7.3）
#[proc_macro] pub fn virtual_to_string_kv(input: TokenStream) -> TokenStream;
/// INHERIT_TO_STRING_KV("ObTableStmt", ObTableStmt, kv…)（:1041-1042）
///   → inherit_to_string_kv!(Self, "ObTableStmt" => self.base; "k" => v, …)
#[proc_macro] pub fn inherit_to_string_kv(input: TokenStream) -> TokenStream;
/// TO_STRING_EMPTY()（:1043-1049）→ to_string_empty!(Self)
#[proc_macro] pub fn to_string_empty(input: TokenStream) -> TokenStream;
/// TO_STRING_KV_WITH_HELPER(…)（:1039）：函数体先建 `let helper = ObCStringHelper::new();`，值表达式里可以写 helper.convert(..)
#[proc_macro] pub fn to_string_kv_with_helper(input: TokenStream) -> TokenStream;
```

#### 2.4.2 引擎与入口

```rust
/// to_string 的输出缓冲。C++ 的 (char *buf, int64_t buf_len) 合成一个切片，buf_len == buf.len()。
pub type ObBuf = [u8];
pub trait ObBufExt {
    fn buf_len(&self) -> i64;
    /// C++ `buf + pos, buf_len - pos`；pos 不在 [0, len] 内时得到空切片（C++ 里是越界，FIXED(port)）
    fn tail_mut(&mut self, pos: i64) -> &mut [u8];
}
impl ObBufExt for [u8] { /* … */ }

#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum ObFmtDialect { Libc, Ob }

/// ob_printf! 的结果：静态分段表加已按类型转换的实参
pub struct ObFmtArgs<'a> { /* dialect: ObFmtDialect, pieces: &'static [ObFmtPiece], args: &'a [ObFmtArg<'a>] */ }
#[doc(hidden)] pub struct ObFmtPiece { /* 字面量字节或一个转换说明：flags/width/precision/length/conversion */ }
#[doc(hidden)] pub enum ObFmtArg<'a> { Int(u64 /* 按 C 默认提升后的位模式 */), Float(f64), Str(Option<&'a [u8]>), Ptr(usize), Star(i32) }

/// 非字面量格式串（§3.5.8）：运行期解析，类型在运行期核对，不符返回 OB_INVALID_ARGUMENT
pub struct ObFmtRuntime { /* 解析出的分段 */ }
impl ObFmtRuntime {
    pub fn parse(dialect: ObFmtDialect, fmt: &[u8]) -> ObResult<Self>;
    pub fn bind<'a>(&'a self, args: &'a [ObFmtArg<'a>]) -> ObResult<ObFmtArgs<'a>>;
}

/// `%s` 的实参。None 即 C 的 NULL。
pub trait ObPrintfStr { fn printf_bytes(&self) -> Option<&[u8]>; }
// impl for &[u8], &str, &[u8; N], &CStr, &Vec<u8>, &Box<[u8]>, Option<&[u8]>, Option<&str>

/// C++ vsnprintf(buf, n, fmt, ap)：n = buf.len()；返回不截断时的长度；超过 i32::MAX 返回 -1
pub fn vsnprintf(buf: &mut [u8], args: &ObFmtArgs<'_>) -> i32;
/// C++ vsnprintf(NULL, 0, fmt, ap)
pub fn vsnprintf_len(args: &ObFmtArgs<'_>) -> i64;
/// C++ databuff_vprintf（ob_print_utils.cpp:456-521）
pub fn databuff_vprintf(buf: &mut [u8], buf_len: i64, pos: &mut i64, args: &ObFmtArgs<'_>) -> ObResult;
/// C++ logdata_vprintf（ob_log.cpp:145-210），与上一个逐字相同
pub fn logdata_vprintf(buf: &mut [u8], buf_len: i64, pos: &mut i64, args: &ObFmtArgs<'_>) -> ObResult;
/// C++ databuff_memcpy（ob_print_utils.cpp:523-534）
pub fn databuff_memcpy(buf: &mut [u8], buf_len: i64, pos: &mut i64, src: &[u8]) -> ObResult;
/// C++ multiple_extend_buf（:437-454）：空 → 1024 字节，否则翻倍，拷贝原内容
pub fn multiple_extend_buf(buf: &mut Vec<u8>) -> ObResult;
```

#### 2.4.3 格式化宏（`oblib` 根）

```rust
/// snprintf(buf, n, fmt, …) / sprintf(buf, fmt, …) → snprintf!(buf, "fmt", …) -> i32（Libc 方言；buf 为 &mut [u8] 或数组）
#[macro_export] macro_rules! snprintf { ($buf:expr, $fmt:literal $(, $a:expr)* $(,)?) => {
    $crate::fmt::vsnprintf(&mut $buf[..], &$crate::ob_printf!($fmt $(, $a)*)) }; }
/// databuff_printf(buf, buf_len, pos, fmt, …)（ob_print_utils.h:191）→ databuff_printf!(buf, buf_len, pos, "fmt", …) -> ObResult
/// databuff_printf(buf, buf_len, fmt, …)（:188，无 pos）→ databuff_printf!(buf, buf_len; "fmt", …)
/// databuff_printf(buf, buf_len, pos, obj)（:198-228，模板）→ databuff_printf!(buf, buf_len, pos; obj)
/// databuff_printf(char *&buf, int64_t &buf_len, pos, alloc, fmt, …)（:194，自动扩容）→ databuff_printf!(@grow vec, pos, "fmt", …)
#[macro_export] macro_rules! databuff_printf { … }
/// BUF_PRINTF(fmt, …)（:1016）→ buf_printf!(buf, pos, "fmt", …)：buf_len = buf.len()，Ob 方言
#[macro_export] macro_rules! buf_printf { ($buf:expr, $pos:expr, $fmt:literal $(, $a:expr)* $(,)?) => {{
    let __b: &mut [u8] = &mut $buf[..];        // 先重借用再取长度：同一次调用里不能既 &mut 又 &
    let __l = __b.len() as i64;
    $crate::fmt::databuff_vprintf(__b, __l, &mut $pos, &$crate::ob_printf!(@ob $fmt $(, $a)*)) }}; }
/// BUF_PRINTO(obj)（:1017）→ buf_printo!(buf, pos, obj)：let _ = databuff_print_obj(…)
#[macro_export] macro_rules! buf_printo { … }
/// logdata_printf(buf, buf_len, pos, fmt, …)（ob_log_print_kv.h:62）→ logdata_printf!(buf, buf_len, pos, "fmt", …)
#[macro_export] macro_rules! logdata_printf { … }
/// ObSqlString::append_fmt/assign_fmt（06 号的方法收 &ObFmtArgs）→ append_fmt!(s, "fmt", …) / assign_fmt!(s, "fmt", …)，Ob 方言
#[macro_export] macro_rules! append_fmt { ($s:expr, $fmt:literal $(, $a:expr)* $(,)?) => { $s.append_fmt(&$crate::ob_printf!(@ob $fmt $(, $a)*)) }; }
#[macro_export] macro_rules! assign_fmt { ($s:expr, $fmt:literal $(, $a:expr)* $(,)?) => { $s.assign_fmt(&$crate::ob_printf!(@ob $fmt $(, $a)*)) }; }
/// ob_alloc_printf(result, alloc, fmt, …)（utility.cpp:1648-1685）→ ob_alloc_printf!(result, arena, "fmt", …) -> ObResult
#[macro_export] macro_rules! ob_alloc_printf { … }
/// fprintf(stderr|stdout, fmt, …) → fprintf!(stderr, "fmt", …)；printf(fmt, …) → printf!("fmt", …)（Libc 方言，写 fd 2/1）
#[macro_export] macro_rules! fprintf { … }
#[macro_export] macro_rules! printf { … }
/// LOG_STDERR/LOG_STDOUT（ob_log_print_kv.h:66-67）：isatty 为真时才写
#[macro_export] macro_rules! log_stderr { … }
#[macro_export] macro_rules! log_stdout { … }
```

#### 2.4.4 `ObToString`

```rust
/// C++ 的 to_string 成员加上四条按上下文分开的打印路径（§3.7 的表）。
/// 用户类型只写 to_string（由 to_string_kv! 等生成），其余用默认实现，与 C++ 对类类型的模板分支一致；
/// 基本类型、字符串、指针类按 C++ 各自的特化覆盖。trait 是 dyn 兼容的（带 Self: Sized 的方法不进 vtable）。
pub trait ObToString {
    /// C++ int64_t to_string(char *buf, const int64_t buf_len) const；基本类型对应 common::to_string<T> 特化
    /// （ob_print_utils.cpp:227-358）。返回写入的字节数。
    fn to_string(&self, buf: &mut ObBuf) -> i64;

    /// C++ has_to_string<T>（ob_iarray.h:29-37）：基本类型和指针类为 false
    fn has_to_string() -> bool where Self: Sized { true }

    /// C++ databuff_print_obj（ob_print_utils.h:396-669）：BUF_PRINTO、数组元素
    fn databuff_print_obj(&self, buf: &mut ObBuf, pos: &mut i64) -> ObResult { /* §3.7.2 */ }
    /// C++ databuff_print_key_obj（:672-916）：J_KV/TO_STRING_KV 的 "key:value"
    fn databuff_print_key_obj(&self, buf: &mut ObBuf, pos: &mut i64, key: &str, with_comma: bool) -> ObResult { /* … */ }
    /// C++ logdata_print_obj（ob_log_print_kv.h:261-389）：databuff_print_multi_objs、DBA 日志的值
    fn logdata_print_obj(&self, buf: &mut ObBuf, pos: &mut i64) -> ObResult { /* … */ }
    /// C++ logdata_print_key_obj（:412-580）：日志 KV 的 "key=value"
    fn logdata_print_key_obj(&self, buf: &mut ObBuf, pos: &mut i64, key: &str, with_comma: bool) -> ObResult { /* … */ }
    /// C++ logdata_print_value（:629-656）：LOG_DBA_*_V2 的 LOG_VALUES；C 字符串不加引号
    fn logdata_print_value(&self, buf: &mut ObBuf, pos: &mut i64) -> ObResult { self.logdata_print_obj(buf, pos) }

    /// 切片钩子：[T]/Vec<T>/SmallVec<[T; N]>/Box<[T]> 的 ObToString 调用元素类型的这三个函数。
    /// 默认是 ObSEArray 的格式（05 号文档规定，ob_array.h:741-770）；u8 覆盖成 ObString 的语义（§3.7.5）。
    fn slice_to_string(s: &[Self], buf: &mut ObBuf) -> i64 where Self: Sized { /* ObSEArray 格式 */ }
    fn slice_databuff_print_obj(s: &[Self], buf: &mut ObBuf, pos: &mut i64) -> ObResult where Self: Sized { /* 类类型默认 */ }
    fn slice_databuff_print_key_obj(s: &[Self], buf: &mut ObBuf, pos: &mut i64, key: &str, with_comma: bool) -> ObResult where Self: Sized { /* … */ }
}

// 本模块提供的实现（打印规则见 §3.7.2 的表）：
// i8 i16 i32 i64 isize u8 u16 u32 u64 usize f32 f64 bool str
// [T] Vec<T> Box<[T]> smallvec::SmallVec<[T; N]>（T: ObToString）
// &T &mut T Box<T> Arc<T>（对 T 透明）
// Option<&T> Option<&mut T> Option<Box<T>> Option<Arc<T>>（可空指针语义）
// (A, B)（std::pair）  ObError  Result<(), ObError>（按 int 错误码打印）
// 以及下面的包装类型

/// C++ 的 `char` 当字符打印时（%c）。u8 一律按 uint8_t 打印（RULEBOOK §2.12 把作数据的 char 译成 u8）。
#[derive(Copy, Clone)] pub struct ObCChar(pub u8);
/// C++ `reinterpret_cast<const void*>(x)`（KP、KP_、OB_P）：只打印地址
#[derive(Copy, Clone)] pub struct ObVoidPtr(pub usize);
impl ObVoidPtr { pub fn of<T: ?Sized>(r: Option<&T>) -> Self; }
/// C++ 枚举译成的新类型（RULEBOOK §2.12）实现 ObToString：所有路径按 "%ld" 打印数值
#[macro_export] macro_rules! ob_to_string_enum { ($t:ty) => { … }; }
```

#### 2.4.5 打印辅助类型与函数（名字照 C++）

```rust
pub struct ObSzString<'a>(pub Option<&'a [u8]>);         // KCSTRING：NULL 或原样（ob_log_print_kv.h:93-113）
pub struct ObQuoteSzString<'a>(pub Option<&'a [u8]>);    // NULL 或 "\"%s\""（:115-135）
pub struct ObLenString<'a> { str_: &'a [u8], len_: i32 } // :140-160
pub struct ObLogPrintPointerCnt<'a, T: ?Sized>(pub Option<&'a T>);   // KPC/PC：NULL 或 to_string（:162-180）
pub trait ObGetName { fn get_name(&self, buf: &mut ObBuf, pos: &mut i64) -> ObResult; }
pub struct ObLogPrintName<'a, T: ?Sized>(pub Option<&'a T>);         // KPNAME/PNAME：'"' + get_name + '"'（:182-219）
pub struct ObLogPrintErrMsg;                              // KERRMSG：'"%m"'（:223-237）
pub struct ObLogPrintErrNoMsg(pub i32);                   // KERRNOMSG(n)（:241-259）
pub struct ObLogPrintHex<'a>(pub Option<&'a [u8]>);      // KPHEX：'"data_size:%ld, data:' + HEX + '"'（ob_print_utils.h:88-107）
pub struct ObHexStringWrap<'a>(pub &'a [u8]);            // :109-117
pub struct ObTimestampStr(pub i64);                       // KTIME 的值（§2.5.7）

pub fn to_string<T: ObToString + ?Sized>(obj: &T, buf: &mut ObBuf) -> i64;               // common::to_string<T>
pub fn databuff_print_obj<T: ObToString + ?Sized>(buf: &mut ObBuf, pos: &mut i64, obj: &T) -> ObResult;
pub fn databuff_print_key_obj<T: ObToString + ?Sized>(buf: &mut ObBuf, pos: &mut i64, key: &str, with_comma: bool, obj: &T) -> ObResult;
pub fn databuff_print_json_kv<T: ObToString + ?Sized>(buf: &mut ObBuf, pos: &mut i64, key: Option<&str>, obj: &T) -> ObResult;
pub fn databuff_print_json_kv_comma<T: ObToString + ?Sized>(buf: &mut ObBuf, pos: &mut i64, key: Option<&str>, obj: &T) -> ObResult;
pub fn databuff_print_obj_array<T: ObToString>(buf: &mut ObBuf, pos: &mut i64, obj: Option<&[T]>) -> ObResult;
/// J_KV / databuff_print_kv(buf, buf_len, pos, k1, v1, …)（ob_print_kv.h:156-312）
pub fn databuff_print_kv(buf: &mut ObBuf, pos: &mut i64, kvs: &[(&str, &dyn ObToString)]) -> ObResult;
/// databuff_print_multi_objs（ob_print_utils.h:238-282）
pub fn databuff_print_multi_objs(buf: &mut ObBuf, pos: &mut i64, objs: &[&dyn ObToString]) -> ObResult;
pub fn logdata_print_info(buf: &mut ObBuf, pos: &mut i64, info: Option<&[u8]>) -> ObResult;          // :390-399
pub fn logdata_print_info_begin(buf: &mut ObBuf, pos: &mut i64, info: Option<&[u8]>) -> ObResult;    // :401-410

// 十六进制与位串工具（ob_print_utils.cpp:26-224、536-562），签名把 (ptr, len) 合成切片，其余照旧
pub fn get_xdigit(c1: u8) -> u8;
pub fn to_hex_cstr(in_data: &[u8], buf: &mut [u8], pos: &mut i64, cstr_pos: &mut i64) -> ObResult;
pub fn hex_to_cstr(in_data: &[u8], buff: &mut [u8], pos: &mut i64) -> ObResult;
pub fn hex_print(in_buf: &[u8], buffer: &mut [u8], pos: &mut i64) -> ObResult;
pub fn bit_print(bit_val: u64, buffer: &mut [u8], pos: &mut i64) -> ObResult;
pub fn is_printable(buf: &[u8], is_printable: &mut bool) -> ObResult;
pub fn str_to_hex(in_data: &[u8], buff: &mut [u8]) -> i32;
pub fn bit_to_char_array(value: u64, bit_len: i32, buf: &mut [u8], pos: &mut i64) -> ObResult;

/// C++ ObCStringHelper（ob_print_utils.h:288-385）。转换结果借用 &self，同一条语句里可以多次 convert。
pub struct ObCStringHelper { /* arena: crate::mem::Arena，虚拟缓冲区 buf_len_/pos_（§3.7.7），ob_errno_: Cell<i32> */ }
impl ObCStringHelper {
    pub fn new() -> Self;
    /// const char *convert(const T &obj)：None 即 nullptr（超过 64 MB 或分配失败）
    pub fn convert<T: ObToString + ?Sized>(&self, obj: &T) -> Option<&[u8]>;
    /// int convert(const T &obj, const char *&str)（§4 的重载改名）
    pub fn convert_char<'s, T: ObToString + ?Sized>(&'s self, obj: &T, out: &mut Option<&'s [u8]>) -> ObResult;
    pub fn reset(&mut self);
    pub fn get_ob_errno(&self) -> i32;
}

/// J_* 宏（ob_print_utils.h:1022-1080）：C++ 隐式用局部的 buf、buf_len、pos，Rust 显式传 buf 和 pos
#[macro_export] macro_rules! j_kv { ($buf:expr, $pos:expr; $($k:expr => $v:expr),+ $(,)?) => {
    $crate::fmt::databuff_print_kv($buf, &mut $pos, &[$(($k, &$v as &dyn $crate::fmt::ObToString)),+]) }; }
// j_name!(buf, pos, key) j_colon! j_comma! j_quote! j_obj_start! j_obj_end! j_array_start! j_array_end!
// j_null! j_nop! j_empty_obj! j_newline! j_obj!(buf, pos, obj) j_ko!(buf, pos, key, obj) j_kw!(buf, pos, key, { body })
// j_ow!(buf, pos, { body }) j_kn!(buf, pos, key)
/// databuff_print_kv(buf, buf_len, pos, k, v, …) → databuff_print_kv!(buf, pos; "k" => v, …)
#[macro_export] macro_rules! databuff_print_kv { … }
/// databuff_print_multi_objs(buf, buf_len, pos, a, b, …) → databuff_print_multi_objs!(buf, pos; a, b, …)
#[macro_export] macro_rules! databuff_print_multi_objs { … }
```

#### 2.4.6 C 可变参数格式化函数的统一译法

`int f(…, const char *fmt, ...)` 与配套的 `int vf(…, const char *fmt, va_list)`：Rust 只留一个同名方法 `fn f(&mut self, …, args: &ObFmtArgs<'_>) -> ObResult`（有 `v` 版本的，`v` 版本是实现、`f` 转发给它），调用处写 `x.f(…, &ob_printf!("fmt", a, b))`；方言按函数体里调的是哪一个（直接调 `vsnprintf` 的用默认 Libc 方言，调 `databuff_vprintf`/`logdata_vprintf`/`ObSqlString::vappend` 的用 `@ob`）。适用于 §1.1 末尾列出的六个函数。

### 2.5 `oblib::log`

#### 2.5.1 级别、模块表、前缀

```rust
// ob_log_level.h:27-46
pub const OB_LOG_LEVEL_NONE: i32 = 7;
pub const OB_LOG_LEVEL_NP: i32 = -1;
pub const OB_LOG_LEVEL_DBA_ERROR: i32 = 0;   // 行头打印 "ERROR"
pub const OB_LOG_LEVEL_DBA_WARN: i32 = 1;    // "WARN"
pub const OB_LOG_LEVEL_DBA_INFO: i32 = 2;
pub const OB_LOG_LEVEL_INFO: i32 = 2;        // "INFO"
pub const OB_LOG_LEVEL_ERROR: i32 = 3;       // "EDIAG"
pub const OB_LOG_LEVEL_EDIAG: i32 = 3;
pub const OB_LOG_LEVEL_WARN: i32 = 4;        // "WDIAG"
pub const OB_LOG_LEVEL_WDIAG: i32 = 4;
pub const OB_LOG_LEVEL_TRACE: i32 = 5;
pub const OB_LOG_LEVEL_DEBUG: i32 = 6;
pub const OB_LOG_LEVEL_MAX: i32 = 7;
pub const OB_LOG_MAX_PAR_MOD_SIZE: u64 = 64;   // ob_log.h:99-100
pub const OB_LOG_MAX_SUB_MOD_SIZE: u64 = 64;
// ObThreadLogLevel（ob_log_level.h:51-56）没有使用者（`git grep` 只命中 ob_log.h:870 的注释），不提供，交清单第 16 类确认

/// LOG_MOD_BEGIN/DEFINE_LOG_SUB_MOD（ob_log_module.h:32-216）：15 组、111 个枚举值，取值照抄（附录 A）
#[allow(non_snake_case)] pub mod OB_LOG_ROOT { pub const M_CLIENT: u64 = 0; /* … */ pub const M_MVCC: u64 = 25; }
#[allow(non_snake_case)] pub mod OB_LOG_SQL  { pub const M_ENG: u64 = 0; /* … */ pub const M_DAS: u64 = 12; }
// OB_LOG_WRS OB_LOG_LIB OB_LOG_OFS OB_LOG_RPC OB_LOG_COMMON OB_LOG_SHARE OB_LOG_STORAGE OB_LOG_CLOG
// OB_LOG_SERVER OB_LOG_RS OB_LOG_EASY OB_LOG_STORAGETEST OB_LOG_PL

/// 一个日志前缀，对应一组 `XXX_LOG`/`_XXX_LOG`/`XXX_LOG_RET` 宏（ob_log_module.h:340-1025）。
/// 每个单元顶部写 `const LOG_PREFIX: ObLogMod = ObLogMod::SQL_ENG;`（RULEBOOK §2.2）。
#[derive(Copy, Clone)]
pub struct ObLogMod {
    pub par_mod_id_: u64,           // OB_LOG_ROOT::M_xxx；NONE 为 u64::MAX
    pub sub_mod_id_: u64,           // 只有父模块的前缀为 u64::MAX
    pub mod_name_: &'static str,    // 行头里的 "[SQL.ENG] "、"[LIB] "；NONE 为 ""
    pub prefix_name_: &'static str, // "SQL_ENG"：LOG_DBA_* 打印的是前缀名本身（:1121-1123）
    pub fmt_as_kv_: bool,           // 只有 TABLELOCK 为 true（:390 的 _TABLELOCK_LOG 接到了 kv 宏）
}
impl ObLogMod {
    /// OB_LOG/_OB_LOG/OB_LOG_RET/_OB_LOG_RET（:268-278、:776-777）：mod_name ""，级别按全局非模块级别判断
    pub const NONE: ObLogMod;
    pub const SQL_ENG: ObLogMod;    // …… 共 99 个，名字就是 C++ 的 XXX；全表见附录 A
    /// 日志宏内部用（C++ 由各 XXX_LOG 宏在预处理期选定，:268-336）：NONE → OB_LOGGER.need_to_print(level)，
    /// 只有父模块 → need_to_print_uint64_t(par, level)，否则 need_to_print_uint64_t_3(par, sub, level)
    pub fn need_to_print(&self, level: i32) -> bool;
}
```

#### 2.5.2 级别表与名字表

```rust
/// C++ ObLogIdLevelMap（ob_log.h:135-164）。元素改成 AtomicI8（C++ 是普通 int8 数组，配置线程写、所有线程读：
/// 数据竞争，FIXED(port)，Relaxed）。全部方法取 &self。重载名按 RULEBOOK §4（§2.7 的说明）。
pub struct ObLogIdLevelMap {
    pub log_level_: [[core::sync::atomic::AtomicI8; 65]; 64],   // [i][0] 是父模块级别
    pub non_mod_level_: core::sync::atomic::AtomicI8,
}
impl ObLogIdLevelMap {
    pub const fn new() -> Self;
    pub fn reset_level(&self);                                                // set_level(INFO)
    pub fn set_level(&self, level: i8);                                       // ob_log.cpp:259-267
    pub fn set_level_uint64_t(&self, par_mod_id: u64, level: i8) -> ObResult; // :269-280，越界 OB_LOG_INVALID_MOD_ID
    pub fn set_level_uint64_t_3(&self, par: u64, sub: u64, level: i8) -> ObResult;  // :283-293
    pub fn get_level(&self) -> i8;
    pub fn get_level_uint64_t(&self, par_mod_id: u64) -> i8;                 // ob_log.h:912-916，越界 NP
    pub fn get_level_uint64_t_2(&self, par: u64, sub: u64) -> i8;            // :918-922
    pub fn assign(&self, other: &ObLogIdLevelMap);                           // C++ 的结构体拷贝
}
/// C++ ObLogNameIdMap（ob_log.h:170-195）：静态初始化时登记的名字（ob_log_module.ipp），Rust 里是常量表（§3.8.2 第 8 条）
pub struct ObLogNameIdMap { /* &'static [[Option<&'static str>; 65]; 64] */ }
impl ObLogNameIdMap {
    pub fn get_mod_id(&self, mod_name: Option<&[u8]>, sub_mod_name: Option<&[u8]>, par: &mut u64, sub: &mut u64) -> ObResult;
    pub fn get_mod_id_uint64_t(&self, mod_name: Option<&[u8]>, par: &mut u64) -> ObResult;
}
/// C++ ObThreadLogLevelUtils（ob_log.h:204-227、924-967）。线程局部状态是"有无会话表 + 一份表的拷贝 + 线程级别"，
/// 全部 const 初始化、没有 Drop（RULEBOOK 0.5 的外部线程规则），见 §3.8.6。
pub struct ObThreadLogLevelUtils;
impl ObThreadLogLevelUtils {
    pub fn init();                                                     // 清空会话表
    pub fn init_log_id_level_map(id_level_map: Option<&ObLogIdLevelMap>);  // 拷贝进线程局部；None → level NONE
    pub fn init_int8_t(level: i8);
    pub fn clear();
    pub fn get_level() -> i8;
    /// C++ get() 返回指针，只被 ObLogger::get_log_level 和 px 用来判空；Rust 给"有没有"加上按模块取级别
    pub fn has_map() -> bool;
}
pub fn get_thread_log_id_level_map_level(par: u64, sub: u64) -> i8;   // 内部用
/// process_thread_log_id_level_map(str, len)（ob_log.cpp:246-257）：解析进线程局部的表并启用
pub fn process_thread_log_id_level_map(s: &[u8]) -> ObResult;
// ObThreadFlags / ObRSThreadFlag（ob_log.h:229-273）在 ob_log.h 之外没有使用者，不提供，交清单第 16 类确认
```

#### 2.5.3 `ObLogger`

```rust
/// C++ ObLogger::UserMsgLevel（ob_log.h:345-351）
#[derive(Copy, Clone, PartialEq, Eq)] pub struct UserMsgLevel(pub i32);
/// 调用点（__FILE__、__LINE__、__FUNCTION__）。function 由 ob_log_loc!() 取（§3.8.1）。
#[derive(Copy, Clone)] pub struct ObLogLoc { pub file: &'static str, pub line: u32, pub function: &'static str }
#[macro_export] macro_rules! ob_log_loc { () => { $crate::log::ObLogLoc {
    file: file!(), line: line!(),
    function: { fn __ob_f() {} $crate::log::fn_name_of(::core::any::type_name_of_val(&__ob_f)) } } }; }
pub fn fn_name_of(type_name: &'static str) -> &'static str;

/// C++ ObLogger（ob_log.h:310-904）。C++ 继承 ObRingBufLogWriter；Rust 按 RULEBOOK §2.13 拆成数据字段 `base`
/// 加 `impl ObRingBufLogWriterVirt for ObLogger`。单例：`static OB_LOGGER: LazyLock<ObLogger>`，构造不起线程。
/// 可变状态全部是原子量或 Latch（C++ 多数是无保护的普通字段，§3.15 列出 FIXED(port)）。
pub struct ObLogger { pub base: ObRingBufLogWriter, /* … */ }
impl ObLogger {
    pub const USER_NONE: UserMsgLevel = UserMsgLevel(0);
    pub const USER_WARN: UserMsgLevel = UserMsgLevel(1);
    pub const USER_ERROR: UserMsgLevel = UserMsgLevel(2);
    pub const USER_NOTE: UserMsgLevel = UserMsgLevel(3);
    pub const DEFAULT_MAX_FILE_SIZE: i64 = 256 * 1024 * 1024;   // ob_log.h:315-338 的常量逐个照抄
    pub const BASE_LOG_SIZE: i64 = 1024;
    pub const MAX_LOG_SIZE: i64 = 64 * 1024;
    pub const NORMAL_LOG_SIZE: i64 = 1024;
    pub const GROUP_COMMIT_MAX_WAIT_US: i64 = 500 * 1000;
    pub const MAX_TASK_LOG_TYPE: u64 = 5;
    pub const MAX_LOG_FILE_COUNT: i32 = 10 * 1024;
    // DISK_SAMPLE_TIME FILE_SAMPLE_TIME FLUSH_SAMPLE_TIME CAN_PRINT_DISK_SIZE MAX_LOG_HEAD_SIZE
    // GROUP_COMMIT_MIN_ITEM_COUNT GROUP_COMMIT_MAX_ITEM_COUNT PERF_LEVEL("PERF") ……

    pub fn get_logger() -> &'static ObLogger;                                  // OB_LOGGER（ob_log.h:128、906-910）
    /// 启动写线程（经 oblib::thread）；由组合根显式调用（RULEBOOK 0.5，时机见修订 #4）
    pub fn init(&'static self, log_cfg: &ObBaseLogWriterCfg) -> ObResult;      // ob_log.cpp:1381-1415
    pub fn stop(&self); pub fn wait(&self); pub fn destroy(&self);              // :569-584

    pub fn set_probe(&self, s: &[u8]) -> i32;                                   // :454-486
    /// C++ 返回函数内 static 缓冲（:488-506）→ 写调用方缓冲（RULEBOOK §2.4），FIXED(port)
    pub fn show_probe(&self, buf: &mut ObBuf) -> i64;
    pub fn is_async_log_used(&self) -> bool;                                    // ob_log.h:403
    pub fn is_in_async_logging(&self) -> bool;                                  // :404，容器 to_string 用
    pub fn get_generic_log_write_count(&self) -> i64;                           // :406-427 的统计读函数逐个照抄
    pub fn enable_async_log(&self) -> bool; pub fn set_enable_async_log(&self, flag: bool);
    pub fn set_stop_append_log(&self); pub fn disable(&self);
    pub fn set_disable_logging(flag: bool) -> bool; pub fn is_enable_logging() -> bool;   // 线程局部
    pub fn set_use_multi_flush(&self, flag: bool); pub fn is_use_multi_flush(&self) -> bool;
    pub fn is_force_allows(&self) -> bool; pub fn check_reset_force_allows(&self);       // ob_log.cpp:1530-1548
    pub fn get_reimbursation_time(&self) -> i64; pub fn get_wait_us(&self, level: i32) -> i64;   // :1550-1563、1625-1658
    pub fn set_trace_mode(&self, trace_mode: bool); pub fn is_trace_mode(&self) -> bool;   // :586-599
    pub fn print_trace_buffer(&self, mod_name: &str, level: i32, loc: &ObLogLoc);          // :513-533
    pub fn get_write_size(&self) -> i64;   // 及 get_trace_write_size 等（ob_log.h:460-467）
    pub fn insert_warning_buffer(&self, lvl: UserMsgLevel, errcode: i32, data: &[u8], data_len: i64);   // ob_log.cpp:1164-1182
    /// log_user_message（:654-666）：只写警告缓冲区，不记日志（日志由 _LOG_USER_MSG 另记，§3.13.2）
    pub fn log_user_message(&self, lvl: UserMsgLevel, errcode: i32, args: &ObFmtArgs<'_>);
    pub fn log_message_fmt(&self, mod_name: &str, level: i32, loc: &ObLogLoc, errcode: i32, args: &ObFmtArgs<'_>);   // ob_log.h:1052-1087
    pub fn log_user_error_line_column(&self, lvl: UserMsgLevel, line: i32, column: i32);  // ob_log.cpp:1200-1205
    pub fn log_message_kv(&self, mod_name: &str, level: i32, loc: &ObLogLoc, errcode: i32, info: Option<&[u8]>, kvs: &[&dyn ObILogKV]);
    pub fn log_message_value(&self, mod_name: &str, dba_event: Option<&str>, level: i32, loc: &ObLogLoc, errcode: i32,
                             with_quotes: bool, values: &[&dyn ObToString]);
    pub fn need_to_print(&self, level: i32) -> bool;                                       // ob_log.cpp:1769-1783
    pub fn need_to_print_uint64_t(&self, par: u64, level: i32) -> bool;
    pub fn need_to_print_uint64_t_3(&self, par: u64, sub: u64, level: i32) -> bool;
    pub fn need_to_print_dba(&self, level: i32, force: bool) -> bool;                      // :1785-1812
    pub fn set_file_name(&self, filename: &[u8], no_redirect_flag: bool, unused: bool);     // :624-634
    pub fn check_file(&self);                                                               // :846-863
    pub fn set_check(&self, force_check: bool);
    pub fn set_max_file_size(&self, max_file_size: i64);                                    // :876-883
    pub fn set_max_file_index(&self, max_file_index: i64) -> ObResult;                      // :885-900
    pub fn set_record_old_log_file(&self) -> ObResult;                                      // :902-910
    pub fn set_log_compressor(&self, c: Option<&'static ObLogCompressor>) -> ObResult;      // :912-921
    pub fn get_max_file_index(&self) -> i64;
    pub fn set_log_level(&self, level: Option<&[u8]>, version: i64);                        // :601-612
    pub fn set_log_level_int32_t(&self, level: i32, version: i64);                          // ob_log.h:668-669
    pub fn set_log_level_int8_t(&self, level: i8, version: i64);                            // ob_log.cpp:614-622
    pub fn set_mod_log_levels(&self, level_str: &[u8], version: i64) -> ObResult;           // ob_log.h:1152-1155
    pub fn down_log_level(&self, version: i64); pub fn up_log_level(&self, version: i64);
    pub fn get_level(&self) -> i32;
    pub fn get_log_level(&self) -> i32;                                                      // ob_log.h:1089-1150
    pub fn get_log_level_uint64_t(&self, par: u64) -> i32;
    pub fn get_log_level_uint64_t_2(&self, par: u64, sub: u64) -> i32;
    pub fn get_level_str(&self) -> &'static str; pub fn get_level_str_int8_t(level: i8) -> &'static str;
    pub fn disable_thread_log_level(&self);
    pub fn level_str2int(&self, level_name: Option<&[u8]>, level_int: &mut i8, is_alert_log: bool) -> ObResult;   // ob_log.cpp:1141-1162
    pub fn parse_check(&self, s: &[u8]) -> ObResult;                                         // :966-970
    pub fn parse_set(&self, s: &[u8], version: i64) -> ObResult;                             // :1058-1066
    pub fn parse_set_int32_t(&self, s: &[u8], valid_length: &mut i32, map: &ObLogIdLevelMap) -> ObResult;   // :1068-1089
    pub fn get_name_id_map(&self) -> &ObLogNameIdMap; pub fn get_id_level_map(&self) -> &ObLogIdLevelMap;
    pub fn set_enable_log_limit(&self, enable_log_limit: bool);
    pub fn set_tl_limiter(limiter: &'static dyn ObRateLimiterVirt);                          // ob_log.h:709-722，线程局部
    pub fn set_tl_type(t: i32);
    pub fn set_default_limiter(&self, limiter: &'static dyn ObRateLimiterVirt);
    pub fn set_new_file_info(&self, info: &'static [u8]);
    pub fn set_info_as_wdiag(&self, v: bool); pub fn is_info_as_wdiag(&self) -> bool;
    pub fn issue_dba_error(&self, errcode: i32, loc: &ObLogLoc, info_str: &[u8]);            // ob_log.cpp:1759-1767
}
/// C++ OB_PRINT / OB_PRINT_DBA（ob_log.h:1361-1405）与自由函数 need_to_print（:1407-1433）
#[allow(non_snake_case)]
pub fn OB_PRINT(mod_name: &str, level: i32, loc: &ObLogLoc, errcode: i32, info: Option<&[u8]>, kvs: &[&dyn ObILogKV]);
#[allow(non_snake_case)]
pub fn OB_PRINT_DBA(mod_name: &str, mod_name_bracket: &str, dba_event: &str, print_rd_log: bool, level: i32,
                    loc: &ObLogLoc, errcode: i32, values: &[&dyn ObToString]);
pub fn need_to_print(level: i32) -> bool;
pub fn need_to_print_uint64_t(par: u64, level: i32) -> bool;
pub fn need_to_print_uint64_t_3(par: u64, sub: u64, level: i32) -> bool;
/// allow_next_syslog（ob_log.cpp:68-71 的 weak 版本空函数，share/ob_task_define.cpp:29 覆盖）：RULEBOOK 0.4 的钩子
pub static ALLOW_NEXT_SYSLOG_HOOK: std::sync::OnceLock<fn(i64)> = std::sync::OnceLock::new();
pub fn allow_next_syslog(count: i64);
/// 日志行头的 trace id（ObCurTraceId::get_trace_id_str，ob_trace_id.h:286-298）：启动时注册；未注册时写 "Y0-0000000000000000-0-0"
pub static TRACE_ID_STR_HOOK: std::sync::OnceLock<fn(&mut [u8]) -> usize> = std::sync::OnceLock::new();
/// C++ get_fd_type 的两个重载（ob_log.cpp:104-133）
pub fn get_fd_type(mod_name: &str) -> ObPLogFDType;
pub fn get_fd_type_int32_t(mod_name: &str, level: i32, dba_event: Option<&str>) -> ObPLogFDType;
```

#### 2.5.4 日志宏

```rust
/// LOG_ERROR(msg, kv…)/LOG_WARN(msg, kv…)（ob_log_module.h:1212-1219）→ log_error!(code, "msg", kv…)/log_warn!(code, "msg", kv…)
///   code：ret（ObResult）、OB_X（ObError）或 i32（ObErrcode）；LOG_WARN_RET(OB_X, …)（:1217）同样写 log_warn!(OB_X, …)
/// LOG_INFO/LOG_TRACE/LOG_DEBUG(msg, kv…)（:1220-1225）→ log_info!("msg", kv…) 等（这三级 C++ 的 errcode 固定为 0，:261-266）
/// 前缀取调用处作用域里的 `LOG_PREFIX`。msg 可以是字面量或 &[u8]/&str 表达式；kv 是 k!/kv!/kr!/kp!/kpc!… 的结果。
#[macro_export] macro_rules! log_error { ($code:expr, $info:expr $(, $kv:expr)* $(,)?) => {
    $crate::__ob_log_kv!(LOG_PREFIX, ERROR, $crate::error::ObErrcode::ob_errcode(&$code), $info $(, $kv)*) }; }
#[macro_export] macro_rules! log_warn { /* 同上，WARN */ }
#[macro_export] macro_rules! log_info { ($info:expr $(, $kv:expr)* $(,)?) => { $crate::__ob_log_kv!(LOG_PREFIX, INFO, 0, $info $(, $kv)*) }; }
/// 所有 KV 日志宏的共同展开（ob_log_module.h:302-320）：先判断编译期开关和级别，再求值实参，一条语句内用完临时量
#[doc(hidden)] #[macro_export]
macro_rules! __ob_log_kv { ($m:expr, $lv:ident, $code:expr, $info:expr $(, $kv:expr)*) => {
    if $crate::__ob_log_enabled!($lv) {                    // DEBUG：cfg!(any(feature = "ENABLE_DEBUG_LOG", debug_assertions))；其余为 true
        let __m: $crate::log::ObLogMod = $m;
        if __m.need_to_print($crate::__ob_log_level!($lv)) {   // NONE/父模块/子模块分别走 need_to_print 的三个重载
            $crate::log::OB_PRINT(__m.mod_name_, $crate::__ob_log_level!($lv), &$crate::ob_log_loc!(),
                                  $code, Some(::core::convert::AsRef::<[u8]>::as_ref(&$info)), &[$($kv),*]);
        }
    }
}; }
// __ob_log_level!(WARN) → $crate::log::OB_LOG_LEVEL_WARN 等（宏不能拼接标识符，逐个列出 10 个级别名）
#[macro_export] macro_rules! log_trace { /* … */ }
#[macro_export] macro_rules! log_debug { /* … */ }
/// XXX_LOG(LEVEL, msg, kv…)（:340-773）→ ob_log!(XXX, LEVEL, code, "msg", kv…)；INFO/TRACE/DEBUG/DBA_* 可省略 code
/// XXX_LOG_RET(LEVEL, OB_X, …)（:783-1025）→ ob_log!(XXX, LEVEL, OB_X, …)；OB_LOG(LEVEL, …)（:268-272）→ ob_log!(NONE, LEVEL, …)
#[macro_export] macro_rules! ob_log { /* ($m:ident, $lv:ident, $code:expr, $info:expr $(, $kv:expr)*) 与省略 code 的形式 */ }
/// _LOG_ERROR/_LOG_WARN(fmt, args…)（:1214、1218）→ log_error_fmt!(code, "fmt", args…)/log_warn_fmt!(code, "fmt", args…)
/// _LOG_INFO/_LOG_TRACE/_LOG_DEBUG → log_info_fmt!("fmt", args…) 等；_XXX_LOG(LEVEL, fmt, …) → ob_log_fmt!(XXX, LEVEL, code, "fmt", …)；
/// _OB_LOG(LEVEL, …)（:274-278）→ ob_log_fmt!(NONE, LEVEL, code, …)。格式串走 ob_printf!(@ob …)。
#[macro_export] macro_rules! ob_log_fmt { /* … */ }
// log_error_fmt! log_warn_fmt! log_info_fmt! log_trace_fmt! log_debug_fmt!
/// OB_MOD_LOG_RET(mod_ptr, LEVEL, errcode, …)（:330-336）→ ob_mod_log_ret!("mod", LEVEL, errcode, "msg", kv…)
#[macro_export] macro_rules! ob_mod_log_ret { /* … */ }
/// OB_LOG_MOD_NEED_TO_PRINT(SQL, WARN)/OB_LOG_SUBMOD_NEED_TO_PRINT(SQL, ENG, WARN)（:294-300）
#[macro_export] macro_rules! ob_log_mod_need_to_print { /* … */ }
#[macro_export] macro_rules! ob_log_submod_need_to_print { /* … */ }
/// IS_OB_LOG_TRACE_MODE()/SET_OB_LOG_TRACE_MODE()/CANCLE_OB_LOG_TRACE_MODE()/PRINT_OB_LOG_TRACE_BUF(mod, LEVEL)（:287-291）
#[macro_export] macro_rules! is_ob_log_trace_mode { () => { … }; }
#[macro_export] macro_rules! set_ob_log_trace_mode { () => { … }; }
#[macro_export] macro_rules! cancle_ob_log_trace_mode { () => { … }; }
#[macro_export] macro_rules! print_ob_log_trace_buf { ($m:ident, $lv:ident) => { … }; }
/// LOG_PRINT_EXPR(LEVEL, statement, expr, kv…)（:1228-1231）
#[macro_export] macro_rules! log_print_expr { /* … */ }
```

#### 2.5.5 KV

```rust
/// C++ ObILogKV/ObLogKV（ob_log_print_kv.h:588-627）
pub trait ObILogKV { fn print(&self, buf: &mut ObBuf, pos: &mut i64, with_comma: bool) -> ObResult; }
pub struct ObLogKV<'a, T: ?Sized> { key_: &'a str, value_: &'a T }
impl<'a, T: ObToString + ?Sized> ObILogKV for ObLogKV<'a, T> { /* value_.logdata_print_key_obj(buf, pos, key_, with_comma) */ }

/// K(x)/LITERAL_K(x)（ob_log_module.h:1240、1267）→ k!(x)：键 stringify!(x)
#[macro_export] macro_rules! k { ($x:expr) => { &$crate::log::ObLogKV::new(stringify!($x), &$x) as &dyn $crate::log::ObILogKV }; }
/// K_(x)（:1241）→ kv!("x", self.x_)；"k", v → kv!("k", v)
#[macro_export] macro_rules! kv { ($k:expr, $v:expr) => { &$crate::log::ObLogKV::new($k, &$v) as &dyn $crate::log::ObILogKV }; }
/// KR(x)（:1242）：两对 KV，(#x, x) 与 (#x, ob_error_name(x))
#[macro_export] macro_rules! kr { ($x:expr) => { /* ObLogKVKR */ }; }
/// KP(x)/KP_(x)（:1274-1275）→ kp!(x)/kv!("x", ObVoidPtr::of(…))
#[macro_export] macro_rules! kp { ($x:expr) => { /* … */ }; }
/// KPC(x)/KPC_(x)（:1279-1280）→ kpc!(x)：值 ObLogPrintPointerCnt
#[macro_export] macro_rules! kpc { ($x:expr) => { /* … */ }; }
/// KCSTRING(x)/KCSTRING_(x)（:1245-1246）→ kcstring!(x)：值 ObSzString
#[macro_export] macro_rules! kcstring { ($x:expr) => { /* … */ }; }
/// KTIME(ts)/KTIME_(ts)（:1255-1256）→ ktime!(ts)；KTIMERANGE(ts, b, e)（:1257-1260）→ ktimerange!(ts, DAY, MSECOND)
#[macro_export] macro_rules! ktime { ($x:expr) => { /* … */ }; }
#[macro_export] macro_rules! ktimerange { ($x:expr, $b:ident, $e:ident) => { /* … */ }; }
/// KPHEX(data, size)（:1289）→ kphex!(slice)；PHEX(data, size)（:1288）→ ObLogPrintHex(Some(slice))
#[macro_export] macro_rules! kphex { ($x:expr) => { /* … */ }; }
/// KERRMSG/KERRNOMSG(n)（:1295、1298）→ kerrmsg!()/kerrnomsg!(n)：键 "errmsg"
#[macro_export] macro_rules! kerrmsg { () => { /* … */ }; }
#[macro_export] macro_rules! kerrnomsg { ($n:expr) => { /* … */ }; }
/// KS(x)（:1301）→ ks!(x)：值 06 号的 ObSensitiveString
#[macro_export] macro_rules! ks { ($x:expr) => { /* … */ }; }
/// KPNAME(x)/KPNAME_(x)（:1284-1285）→ kpname!(x)
#[macro_export] macro_rules! kpname { ($x:expr) => { /* … */ }; }
// GETK(obj, member)（:1243，1 处）→ kv!("member", obj.get_member())；Rust 宏不能拼接标识符
```

#### 2.5.6 限流宏

见 §2.2.5；语义见 §3.12。

#### 2.5.7 时间串、调用栈

```rust
/// C++ ObTime2Str（ob_log_time_fmt.h:37-46）。C++ 返回 16 槽线程局部轮转缓冲里的指针（RULEBOOK §2.4）→ 返回值类型，打印时格式化
pub struct ObTime2Str;
impl ObTime2Str {
    pub fn ob_timestamp_str(ts: i64) -> ObTimestampStr;
    pub fn ob_timestamp_str_range(ts: i64, begin: TimeRange, to: TimeRange) -> ObTimestampStrRange;
}
#[derive(Copy, Clone)] pub struct TimeRange(pub i32);   // YEAR=0 … USECOND=7（ob_log_time_fmt.h:21-30）
/// C++ lbt()/lbt(buf, len)/parray（ob_backtrace.cpp:139-198）：返回捕获好的地址，打印时格式化成 "0x%lx 0x%lx …"
pub struct ObLbt { /* 最多 100 个相对偏移 */ }
pub fn lbt() -> ObLbt;
pub fn lbt_char(buf: &mut [u8]) -> usize;                    // lbt(char *buf, int32_t len)（§4 重载改名）
pub fn parray(buf: &mut [u8], array: &[i64]) -> usize;
pub fn light_backtrace(buffer: &mut [usize]) -> i32;         // ob_backtrace.h:41-42，用 backtrace crate 实现
pub static g_enable_backtrace: core::sync::atomic::AtomicBool;   // :122，默认 true；配置 _enable_backtrace_function
```

#### 2.5.8 DBA 日志与 DBA 事件

```rust
/// LOG_DBA_ERROR(OB_X, kv…)/LOG_DBA_WARN（ob_log_module.h:1126-1142）→ log_dba_error!(OB_X, kv…)/log_dba_warn!(OB_X, kv…)
/// LOG_DBA_ERROR_V2/WARN_V2(event, OB_X, v…)、LOG_DBA_INFO_V2(event, v…)（:1188-1191）→ log_dba_error_v2!(event, OB_X, v…) 等
/// LOG_DBA_ERROR_/WARN_/INFO_（:1194-1196）→ log_dba_error_!(…) 等
/// LOG_DBA_FORCE_PRINT(LEVEL, event, errcode, v…)（:1178-1185）→ log_dba_force_print!(LEVEL, event, errcode, v…)
/// 值列表（LOG_VALUES）的每一项是实现 ObToString 的表达式，按 logdata_print_value 打印、首尾相接
#[macro_export] macro_rules! log_dba_error { /* … */ }   // 以及上面列出的其余 8 个
/// DBA_STEP_INC_INFO(list)/DBA_STEP_INFO(list)/DBA_STEP_RESET(list)（ob_log_dba_event.h:98-104）
/// → dba_step_inc_info!(server_start)：展开成 7 个值 "[", "server_start", " ", step, "/", count, "] "
#[macro_export] macro_rules! dba_step_inc_info { /* … */ }
#[macro_export] macro_rules! dba_step_info { /* … */ }
#[macro_export] macro_rules! dba_step_reset { /* … */ }
pub mod dba_event {
    pub const OB_SERVER_SYSLOG_SERVICE_INIT_BEGIN: &str = "OB_SERVER_SYSLOG_SERVICE_INIT_BEGIN";   // DBA_EVENT(x) 即 #x
    // …… ob_log_dba_event.h:106-240 的全部事件名
    pub mod server_start { pub const EVENT_COUNT: i32 = /* ARGS_NUM */ 0;
        pub fn thread_local_step_reset(); pub fn thread_local_step_inc() -> i32; pub fn thread_local_step_get() -> i32; }
    pub mod bootstrap { /* 同上 */ }
}
```

#### 2.5.9 跟踪日志

```rust
/// C++ ObTraceLogConfig（ob_trace_log.h:88-108、.cpp:24-52）
pub struct ObTraceLogConfig;
impl ObTraceLogConfig {
    pub fn set_log_level(log_level_str: Option<&[u8]>) -> i32;
    pub fn get_log_level() -> i32;     // 首次调用读环境变量 _OB_TRACE_LOG_LEVEL_（std::env::var_os）
}
/// CHECK_TRACE_TIMES（ob_trace_log.h:28-52）
#[allow(non_snake_case)] pub fn CHECK_TRACE_TIMES(times: u64, cur_trace_id: Option<&[u64]>) -> bool;
/// NG_TRACE_TIMES(times, …)/NG_TRACE_EXT_TIMES/NG_TRACE_TIMES_WITH_TRACE_ID（:54-66）→ ng_trace_times!(times, …) 等
/// PRINT_TRACE(buf)/FORCE_PRINT_TRACE(buf, HEAD)（:68-86）→ print_trace!(buf)/force_print_trace!(buf, HEAD)
#[macro_export] macro_rules! ng_trace_times { /* … */ }
#[macro_export] macro_rules! ng_trace_ext_times { /* … */ }
#[macro_export] macro_rules! ng_trace_times_with_trace_id { /* … */ }
#[macro_export] macro_rules! print_trace { /* … */ }
#[macro_export] macro_rules! force_print_trace { /* … */ }
```

由 `translate` 单元写出、但形状在这里统一的跟踪宏（RULEBOOK §2.2 最后一行"同名小写宏"）：

| C++（位置） | Rust 形状 |
|---|---|
| `NG_TRACE(event)`、`NG_TRACE_EXT(event, OB_ID(k), v, …)`（`ob_trace_event.h:170-181`） | `ng_trace!(event)`、`ng_trace_ext!(event; OB_ID(k) => v, …)` |
| `REC_TRACE(r, event)`、`REC_TRACE_EXT(r, event, …)`（`:126-163`） | `rec_trace!(r, event)`、`rec_trace_ext!(r, event; OB_ID(k) => v, …)` |
| `THE_TRACE`（`:166`） | `the_trace!()` |
| `FLT_BEGIN_TRACE()`、`FLT_END_TRACE()`、`FLT_BEGIN_SPAN(t)`、`FLT_BEGIN_CHILD_SPAN(t)`、`FLT_BEGIN_FOLLOW_SPAN(t)`、`FLT_END_SPAN(s)`、`FLT_RESET_SPAN()`、`FLT_END_CURRENT_SPAN()`、`FLT_SET_TAG(k, v, …)`、`FLT_SET_TRACE_LEVEL(l)`、`FLT_SET_AUTO_FLUSH(v)`、`FLT_RESTORE_DDL_TRACE_CTX(c)`（`ob_trace.h:27-57`） | 同名小写宏，参数个数不变；`flt_set_tag!(k => v, …)` |

#### 2.5.10 写线程、日志文件、轮转、压缩、限流

```rust
/// C++ ObPLogFDType（ob_async_log_struct.h:49-55）
#[derive(Copy, Clone, PartialEq, Eq)] pub struct ObPLogFDType(pub i32);
impl ObPLogFDType { pub const FD_SVR_FILE: Self = Self(0); pub const FD_TRACE_FILE: Self = Self(1);
                    pub const FD_AUDIT_FILE: Self = Self(2); pub const FD_ALERT_FILE: Self = Self(3); pub const MAX_FD_FILE: Self = Self(4); }
/// C++ ObPLogItem（:58-114）：buf_ 是拥有的字节缓冲，容量 = buf_size_
pub struct ObPLogItem { ring_offset_: i64, fd_type_: ObPLogFDType, log_level_: i32, tl_type_: i32, is_force_allow_: bool,
                        is_size_overflow_: bool, timestamp_: i64, header_pos_: i64, buf_size_: i64, pos_: i64, buf_: Box<[u8]> }
/// C++ ObPLogFileStruct（:116-133，.cpp:39-123）。fd_ 的编号在 reopen 时用 dup2 保持不变（§3.9.4）
pub struct ObPLogFileStruct { /* filename_: Latch<[u8; 256]>，fd_: AtomicI32，write_count_: AtomicU32，write_size_: AtomicI64，
                                 file_size_: AtomicI64，stat_: Latch<(u64 dev, u64 ino)>，file_: Latch<Option<std::fs::File>> */ }
impl ObPLogFileStruct { pub const MAX_LOG_FILE_NAME_SIZE: i32 = 256; pub const LOG_FILE_MODE: u32 = 0o644;
    pub fn open(&self, log_file: Option<&[u8]>, redirect_flag: bool) -> ObResult; pub fn reopen(&self, redirect_flag: bool) -> ObResult;
    pub fn close_all(&self) -> ObResult; pub fn is_opened(&self) -> bool; pub fn get_write_size(&self) -> i64; }

/// C++ ObRingBuf（ob_ringbuf_log_writer.h:77-112）：保留 push_/pop_ 两个整数游标和 2 MB 的记账方式，
/// 条目内容不再放在字节环里，而是 VecDeque 里的拥有型条目（§3.9.1）。
pub struct ObRingBuf { /* buf_len_: i64, push_: Latch 内, pop_: AtomicI64, entries_: Latch<VecDeque<RingEntry>> */ }
impl ObRingBuf {
    pub fn init(&self, buf_len: i64) -> ObResult; pub fn destroy(&self);
    pub fn alloc(&self, total_len: i64) -> i64;                  // 失败 -1
    pub fn commit(&self, pos: i64, item: Box<ObPLogItem>); pub fn rollback(&self, pos: i64);
    pub fn get_pop(&self) -> i64; pub fn get_push(&self) -> i64; pub fn advance_pop(&self, new_pop: i64);
    pub fn is_queue_full(&self) -> bool; pub fn get_queue_depth(&self) -> i64;
}
/// C++ ObRingBufLogWriter（:114-160）：数据 struct 加 Virt trait（RULEBOOK §2.13）
pub struct ObRingBufLogWriter { /* has_stopped_、is_inited_: AtomicBool，group_commit_max_wait_us_，thread_name_，ringbuf_，flush_cond_: FutexCond，flush_tid_ */ }
pub trait ObRingBufLogWriterVirt: Sync { fn base(&self) -> &ObRingBufLogWriter; fn process_batch(&self, entries: Vec<Box<ObPLogItem>>); }
impl ObRingBufLogWriter {
    pub const RINGBUF_SIZE: u64 = 2 * 1024 * 1024; pub const ENTRY_HEADER_SIZE: u64 = 8; pub const MAX_ENTRY_SIZE: u64 = 8 + 65536 + 64;
    pub fn init(&self, owner: &'static dyn ObRingBufLogWriterVirt, group_commit_max_wait_us: i64, thread_name: &str) -> ObResult;
    pub fn stop(&self); pub fn wait(&self); pub fn destroy(&self);
    pub fn alloc(&self, total_len: i64) -> i64; pub fn rollback(&self, pos: i64); pub fn commit(&self, pos: i64, item: Box<ObPLogItem>);
    pub fn is_inited(&self) -> bool; pub fn has_stopped(&self) -> bool; pub fn is_queue_full(&self) -> bool; pub fn get_queue_depth(&self) -> i64;
}

/// C++ ObIBaseLogItem/ObBaseLogWriterCfg/ObBaseLogWriter（ob_base_log_writer.h:36-133）。队列里改存 Arc，
/// 调用方与写线程共享条目（C++ 存借用指针，slog 的调用方在条目完成前等待）。
pub trait ObIBaseLogItem: Send + Sync { fn get_buf(&self) -> &[u8]; fn get_data_len(&self) -> i64; }
#[derive(Copy, Clone)]
pub struct ObBaseLogWriterCfg { pub max_buffer_item_cnt_: u64, pub group_commit_max_wait_us_: u64,
                                pub group_commit_min_item_cnt_: u64, pub group_commit_max_item_cnt_: u64 }
impl ObBaseLogWriterCfg { pub const DEFAULT_MAX_BUFFER_ITEM_CNT: u64 = 1024; pub fn is_valid(&self) -> bool; }
pub struct ObBaseLogWriter { /* 有界队列 Latch<VecDeque<Arc<dyn ObIBaseLogItem>>>、push/pop 计数、log_write_cond_/log_flush_cond_: FutexCond */ }
pub trait ObBaseLogWriterVirt: Sync {
    fn base(&self) -> &ObBaseLogWriter;
    fn process_log_items(&self, items: &[std::sync::Arc<dyn ObIBaseLogItem>], finish_cnt: &mut i64);
    fn drop_log_items(&self, items: &[std::sync::Arc<dyn ObIBaseLogItem>]) { /* C++ 默认实现 */ }
}
impl ObBaseLogWriter {
    pub fn init(&self, log_cfg: &ObBaseLogWriterCfg, thread_name: &'static str) -> ObResult;
    pub fn start(&self, owner: &'static dyn ObBaseLogWriterVirt) -> ObResult;
    pub fn stop(&self); pub fn wait(&self); pub fn destroy(&self);
    pub fn append_log(&self, log_item: std::sync::Arc<dyn ObIBaseLogItem>, timeout_us: u64) -> ObResult;
    pub fn get_queued_item_cnt(&self) -> i64; pub fn is_queue_full(&self) -> bool;
    pub fn flush_log(&self); pub fn flush_log_once(&self);
}
/// C++ ObPLogWriterCfg（ob_log.h:276-284、ob_log.cpp:389-395）
pub fn ob_p_log_writer_cfg() -> ObBaseLogWriterCfg;   // (64<<10, 500000, 1, 64)

/// C++ ObLogCompressor（ob_log_compressor.h:167-225）：后台定时任务（oblib::thread::Timer），每 5 s 一轮
pub struct ObLogCompressor { /* … */ }
impl ObLogCompressor {
    pub fn get_log_compressor() -> &'static ObLogCompressor;     // OB_LOG_COMPRESSOR（:67）
    pub fn init(&'static self) -> ObResult; pub fn stop(&self); pub fn wait(&self); pub fn awake(&self); pub fn destroy(&self);
    pub fn set_max_disk_size(&self, v: i64) -> ObResult; pub fn set_compress_func(&self, name: Option<&[u8]>) -> ObResult;
    pub fn set_min_uncompressed_count(&self, v: i64) -> ObResult; pub fn is_enable_compress(&self) -> bool;
    pub fn run_timer_task(&self);
}

/// C++ lib::ObRateLimiter（ob_rate_limiter.h:25-80）与 syslog 限流器（ob_syslog_rate_limiter.h:36-155）
pub struct ObRateLimiter { pub rate_: core::sync::atomic::AtomicI64, pub name_: Option<&'static str> }
pub trait ObRateLimiterVirt: Sync {
    fn base(&self) -> &ObRateLimiter;
    fn is_force_allows(&self) -> bool; fn reset_force_allows(&self);
    fn try_acquire(&self, permits: i64, arg0: i64, arg1: i64) -> ObResult;
    fn acquire(&self, permits: i64, arg0: i64, arg1: i64) -> ObResult;
}
pub struct ObSimpleRateLimiterImpl { /* permits_: AtomicI64, last_ts_: AtomicI64 */ }
pub struct ObSimpleRateLimiter { pub base: ObRateLimiter, impl_: ObSimpleRateLimiterImpl }
pub struct ObISyslogRateLimiter { pub base: ObRateLimiter }           // try_acquire 带线程局部重入保护（:51-66）
pub trait ObISyslogRateLimiterVirt: ObRateLimiterVirt { fn do_acquire(&self, permits: i64, log_level: i32, errcode: i32) -> ObResult; }
pub struct ObSyslogSimpleRateLimiter { pub base: ObISyslogRateLimiter, impl_: ObSimpleRateLimiterImpl }
pub struct ObSyslogSampleRateLimiter { pub base: ObISyslogRateLimiter, /* initial_ thereafter_ duration_ reset_ts_ count_ */ }
```

#### 2.5.11 钩子与系统调用

| 钩子 | 默认 | 注册方 | 依据 |
|---|---|---|---|
| `ALLOW_NEXT_SYSLOG_HOOK` | 空函数 | share（`ob_task_define.cpp:29`） | RULEBOOK 0.4 oblib→share 行 |
| `TRACE_ID_STR_HOOK` | 写 "Y0-0000000000000000-0-0"（全零 trace id 的 `TRACE_ID_FORMAT_V4`，`ob_trace_id.h:30,123-135`） | `ob_trace_id` 单元 | RULEBOOK 0.5 |

`oblib::log::sys`（§4）：`open_append(path) -> io::Result<File>`、`dup2(src_fd, dst_fd) -> i32`、`statfs_free_bytes(path) -> Option<u64>`、`localtime_r(t: i64) -> ObTm`、`timezone_minutes() -> i32`、`strerror(errnum, buf) -> usize`。线程号、线程名来自 03 号模块（`GETTID()`、`GETTNAME_V2()`，`ob_log.cpp:740,762`）。

### 2.6 警告缓冲区与用户消息

```rust
/// C++ ObWarningBuffer::WarningItem（ob_warning_buffer.h:40-84）
#[derive(Clone)]
pub struct WarningItem {
    pub msg_: [u8; 512], pub timestamp_: i64, pub log_level_: i32, pub line_no_: i32,
    pub code_: i32, pub column_no_: i32, pub sql_state_: [u8; 6],
}
impl WarningItem {
    pub const STR_LEN: u32 = 512;
    pub fn new() -> Self;                       // code_ = OB_MAX_ERROR_CODE，log_level_ = USER_WARN
    pub fn reset(&mut self);
    pub fn set(&mut self, s: &[u8]);            // snprintf(msg_, 512, "%s", s)：遇 NUL 停，最多 511
    pub fn set_code(&mut self, code: i32); pub fn set_log_level(&mut self, l: UserMsgLevel);
    pub fn get(&self) -> &[u8];                 // 到第一个 NUL 为止
    pub fn get_code(&self) -> i32;
    pub fn set_line_no(&mut self, v: i32); pub fn get_line_no(&self) -> i32;
    pub fn set_column_no(&mut self, v: i32); pub fn get_column_no(&self) -> i32;
    pub fn set_sql_state(&mut self, s: &[u8]);  // snprintf(sql_state_, 6, "%s", s)
    pub fn get_sql_state(&self) -> &[u8];
    pub fn assign(&mut self, other: &WarningItem);   // operator=（.cpp:49-61）
}
// to_string_kv!(WarningItem; "msg" => ObSzString(Some(self.get())), "code" => self.code_)   （:83）
// impl ObSerialize（.cpp:26-31，06 号的 ob_serialize_member!：msg_, code_, log_level_, line_no_, column_no_）

/// C++ ObWarningBuffer（:37-149）
pub struct ObWarningBuffer {
    item_: Vec<WarningItem>,         // 惰性增长到 64（alloc_place_holder）
    err_: WarningItem, append_idx_: u32, total_warning_count_: u32, error_ret_: i32,
}
impl ObWarningBuffer {
    pub fn new() -> Self;
    pub fn reset(&mut self); pub fn reset_warning(&mut self);
    pub fn set_warn_log_on(is_log_on: bool); pub fn is_warn_log_on() -> bool;   // 静态 is_log_on_，AtomicBool
    pub fn get_total_warning_count(&self) -> u32; pub fn get_buffer_size(&self) -> u32; pub fn get_readable_warning_count(&self) -> u32;
    pub fn get_max_warn_len(&self) -> u32;
    pub fn append_warning(&mut self, s: &[u8], errcode: i32, sql_state: Option<&[u8]>);
    pub fn append_note(&mut self, s: &[u8], errcode: i32);
    pub fn reset_err(&mut self);
    pub fn set_error(&mut self, s: &[u8], error_code: i32);        // C++ 默认实参 OB_MAX_ERROR_CODE，调用处写全
    pub fn set_error_code(&mut self, error_code: i32);
    pub fn get_err_msg(&self) -> &[u8]; pub fn get_err_code(&self) -> i32;
    pub fn set_error_line_column(&mut self, line: i32, column: i32);
    pub fn get_error_line(&self) -> i32; pub fn get_error_column(&self) -> i32;
    pub fn set_sql_state(&mut self, s: &[u8]); pub fn get_sql_state(&self) -> &[u8];
    pub fn assign(&mut self, other: &ObWarningBuffer);             // operator=（.cpp:33-47）
    pub fn get_warning_item(&self, idx: u32) -> Option<&WarningItem>;
}
// to_string_kv!(ObWarningBuffer; "err" => self.err_, "item" => self.item_, "total_warning_count" => …, "is_log_on" => …)  （:136）

/// 线程局部指向的、可被会话和线程局部同时持有的警告缓冲区（§3.13.1，修订 #3）
#[derive(Clone)]
pub struct ObWarningBufferRef(std::sync::Arc<crate::sync::Latch<ObWarningBuffer>>);
impl ObWarningBufferRef {
    pub fn new() -> Self;
    pub fn lock(&self) -> crate::sync::LatchGuard<'_, ObWarningBuffer>;
    // 便捷方法：各自加锁一次、立刻放锁，便于逐行翻译 `wb->xxx(…)`
    pub fn set_error(&self, s: &[u8], error_code: i32); pub fn set_sql_state(&self, s: &[u8]);
    pub fn get_err_code(&self) -> i32; pub fn get_err_msg(&self) -> ObWarnMsg; pub fn get_sql_state(&self) -> ObWarnMsg;
    pub fn reset(&self); pub fn append_warning(&self, s: &[u8], errcode: i32, sql_state: Option<&[u8]>); pub fn append_note(&self, s: &[u8], errcode: i32);
    pub fn get_readable_warning_count(&self) -> u32;
}
/// get_err_msg/get_sql_state 的拷贝结果（≤ 512 字节），Deref 到 [u8]
pub struct ObWarnMsg { buf_: [u8; 512], len_: usize }

/// ob_warning_buffer.h:226-273
pub fn ob_get_tsi_warning_buffer() -> Option<ObWarningBufferRef>;
pub fn ob_get_tsi_err_msg(code: i32) -> ObWarnMsg;                       // 码不等或无缓冲区 → 空
pub fn ob_setup_tsi_warning_buffer(buffer: Option<ObWarningBufferRef>);
pub fn ob_setup_default_tsi_warning_buffer();                            // GET_TSI(ObWarningBuffer) → 每线程一个默认缓冲区
pub fn ob_reset_tsi_warning_buffer();
/// C++ ObWarningBufferIgnoreScope（:276-296）：守卫自己拥有一个新缓冲区和原来的句柄，不持借用
pub struct ObWarningBufferIgnoreScope { ignore_scope_warn_buf_: ObWarningBufferRef, ori_warn_buf_: Option<ObWarningBufferRef> }
impl ObWarningBufferIgnoreScope { pub fn new() -> Self; }
impl Drop for ObWarningBufferIgnoreScope { fn drop(&mut self) { /* 恢复原句柄并 reset 自己的缓冲区 */ } }
/// RULEBOOK 0.5 的闭包写法：装入 buf、执行 f、恢复原句柄（给 ob_trigger_handler.cpp:449-463、ob_transform_utils.cpp:10090-10149 这类成对调用）
pub fn with_warning_buffer<R>(buf: &ObWarningBufferRef, f: impl FnOnce() -> R) -> R;
```

```rust
/// LOG_USER_ERROR/WARN/NOTE(OB_X, args…)（ob_log_module.h:1073-1078）→ log_user_error!(OB_X, args…) 等
#[macro_export] macro_rules! log_user_error { ($code:ident $(, $a:expr)* $(,)?) => {
    $crate::error::user_msg::$code($crate::log::ObLogger::USER_ERROR, &$crate::ob_log_loc!() $(, $a)*) }; }
#[macro_export] macro_rules! log_user_warn { /* USER_WARN */ }
#[macro_export] macro_rules! log_user_note { /* USER_NOTE */ }
/// LOG_MYSQL_USER_ERROR/WARN/NOTE（:1061-1072）：与上面相同
#[macro_export] macro_rules! log_mysql_user_error { /* … */ }
#[macro_export] macro_rules! log_mysql_user_warn { /* … */ }
#[macro_export] macro_rules! log_mysql_user_note { /* … */ }
/// LOG_USER_ERROR_WITH_LINE_COL(OB_X, line, col, args…)（:1083-1085）；LOG_USER_ERROR_LINE_COLUMN(line, col)（:1079-1082）
#[macro_export] macro_rules! log_user_error_with_line_col { ($code:ident, $line:expr, $col:expr $(, $a:expr)* $(,)?) => { /* … */ }; }
#[macro_export] macro_rules! log_user_error_line_column { ($line:expr, $col:expr) => { /* … */ }; }
/// FORWARD_USER_ERROR(errcode, msg)/FORWARD_USER_WARN（:1098-1102）：等于 _LOG_USER_MSG(level, errcode, "%s", msg)
/// FORWARD_USER_ERROR_MSG(errcode, fmt, args…)（:1104-1105）；FORWARD_USER_NOTE(errcode, msg)（:1107-1113）
/// errcode 可以是变量（这组宏没有 CHECK_LOG_USER_CONST_FMT），接受 ObErrcode
#[macro_export] macro_rules! forward_user_error { ($code:expr, $msg:expr) => { /* … */ }; }
#[macro_export] macro_rules! forward_user_warn { ($code:expr, $msg:expr) => { /* … */ }; }
#[macro_export] macro_rules! forward_user_error_msg { ($code:expr, $fmt:literal $(, $a:expr)* $(,)?) => { /* … */ }; }
#[macro_export] macro_rules! forward_user_note { ($code:expr, $msg:expr) => { /* … */ }; }
/// 生成的 user_msg::* 与 forward_user_* 共用的实现（_LOG_USER_MSG，:1033-1039）
pub fn log_user_msg(lvl: UserMsgLevel, errcode: i32, loc: &ObLogLoc, args: &ObFmtArgs<'_>);
```

### 2.7 本文引入的新名字

RULEBOOK §4 要求"表外的一律保留 C++ 名"。下列名字在 C++ 里没有对应物，由本文引入，请在审查时一并确认；重载改名按 RULEBOOK §4 的规则推出，与 1c 生成的 `overloads.tsv` 不一致时以 `overloads.tsv` 为准、本文随之改。

| 名字 | 为什么需要 |
|---|---|
| `ObResultExt`、`ObErrcode`、`ObError::{new_const, new, from_code, code, is_unrecoverable}` | 错误值与整数码之间的转换（`ObResultExt::is`/`is_unrecoverable` 是 RULEBOOK 点名的） |
| `ScopedLambdaMut`、`defer!(&mut x => |v| …)` | C++ `[&]` 捕获在 Rust 借用规则下的写法 |
| `EventItemSetting`、`EventItem::call_0`、`EventTable::set_event_char`、`ob_e!`/`event_call!`/`tp_set_event!`/`ob_i!`/`global_errsim_point_defs!` | 前一个是 C++ 临时 `EventItem` 的有效部分，其余是重载改名或宏的小写名 |
| `ObFmtArgs`、`ObFmtDialect`、`ObFmtRuntime`、`ObPrintfStr`、`fmt::arg::*`、`vsnprintf_len` | `ob_printf!` 的结果类型与类型检查 |
| `ObBuf`、`ObBufExt` | RULEBOOK §2.4、§2.11 已用 `ObBuf`；`ObBufExt` 是越界安全的切片辅助 |
| `ObToString::{has_to_string, slice_to_string, slice_databuff_print_obj, slice_databuff_print_key_obj}` | 对应 C++ 的 `has_to_string<T>` 与 `ObString`/数组的模板分支 |
| `ObCChar`、`ObVoidPtr`、`ObTimestampStr`、`ObTimestampStrRange`、`ObLbt`、`ObWarnMsg`、`ob_to_string_enum!` | 分别对应 `char`、`const void*`、`ObTime2Str` 的轮转缓冲、`lbt()` 的线程局部缓冲、`const char *get_err_msg()`、`__is_enum` 分支 |
| `ObLogMod`、`LOG_PREFIX`、`ObLogLoc`、`ob_log_loc!`、`fn_name_of` | RULEBOOK §2.2 已用 `ObLogMod`/`LOG_PREFIX`；后三个代替 `__FILE__`/`__LINE__`/`__FUNCTION__` |
| `ObLogMod::need_to_print`；隐藏的展开辅助宏 `__ob_log_kv!`、`__ob_log_level!`、`__ob_log_enabled!`、`__ck_chain!`、`__ck_0!` | C++ 由预处理器按前缀选定 `need_to_print` 的重载、拼接级别名、展开 `CK_n` 的 if/else-if 链；Rust 宏不能拼接标识符，链式展开要靠递归的内部宏，所以用这几个 `#[doc(hidden)]` 辅助，译者不直接写 |
| `ObILogKV` 已是 C++ 名；`ObLogKVKR` | `KR` 的两对 KV |
| `ObWarningBufferRef`、`with_warning_buffer` | 修订 #3；后者是 RULEBOOK 0.5 点名的 |
| `ALLOW_NEXT_SYSLOG_HOOK`、`TRACE_ID_STR_HOOK`、`log::sys` | RULEBOOK 0.4、0.5 的钩子与 §4 的系统调用封装 |
| `ObRingBufLogWriterVirt`、`ObBaseLogWriterVirt`、`ObRateLimiterVirt`、`ObISyslogRateLimiterVirt` | RULEBOOK §2.13 的 `<类名>Virt` 规则 |
| `ob_p_log_writer_cfg()` | C++ `ObPLogWriterCfg` 是只有构造函数的子类，Rust 改成返回配置的函数 |

## 3. 语义

### 3.1 `ObError` / `ObResult`

1. `size_of::<ObError>() == 4`、`size_of::<ObResult>() == 4`（`NonZeroI32` 的空位优化），配断言测试。
2. `OB_SUCCESS` 不是 `ObError`；C++ 的 `ret = OB_SUCCESS` 一律是 `Ok(())`（RULEBOOK §2.1）。`ObError::from_code(0) == Ok(())`。
3. 错误值只有 4 字节的码，报错文本走线程局部的警告缓冲区（RULEBOOK §2.3"消息不得塞进错误值里"，conventions.md §2.1 第 3 点）。
4. `is_unrecoverable()` 恰好等于 `== OB_ALLOCATE_MEMORY_FAILED`（-4013），对应 C++ 的 `CM_IS_WARN_ON_FAIL && ret != OB_ALLOCATE_MEMORY_FAILED`（RULEBOOK §2.1 错误恢复规则）。
5. `ObErrcode`：`Ok(_) → 0`、`Err(e) → e.code()`、`i32` 原样。C++ 的日志宏把 `ret` 当 `int` 打印（`ob_log_module.h:261-262`），`k!(ret)` 打印同一个整数（§3.7.2 表中 `ObResult` 行）。

### 3.2 errno 生成器（build.rs）与各张表

#### 3.2.1 解析

- 逐行读 `src/share/ob_errno.def`，只认行首的 `DEFINE_`，按 `gen_errno.pl:40-70` 的 9 条正则的**顺序**匹配（7 字段的 `DEFINE_ERROR` 先于 5 字段的，8 字段的 `_EXT` 先于 6 字段的，依此类推）。字符串字段是 `"[^"]*"`；现有文件里没有 `\"`（`errno_parse.py`），build.rs 遇到 `\"` 直接报错，避免与 perl 的正则不一致。
- 字符串字面量按 C 的规则反转义：现有文件里有 `\'` 261 处、`\\` 3 处（如 `ob_errno.def:494` 的 `Table \'%s.%s\' doesn\'t exist`）；遇到其他转义同样按 C 处理，未知转义报错。
- `$1` 是名字，`$2` 是错误码，`$3` 是 M1，`$4` 是 SQLSTATE（带引号），其后依次是 M3、M4（仅 `_EXT`）、cause、solution（可选）。`_DEP` 后缀只决定 C++ 写进哪个头文件，Rust 不区分。
- 名字重复：后出现的覆盖先出现的（perl 的散列，`gen_errno.pl:29-30`）。现有的唯一一处是 `OB_ERR_ARGUMENT_SHOULD_CONSTANT_OR_GROUP_EXPR`（`:1868`、`:1872`，两次完全相同），所以有 1,546 条定义、1,545 个错误码。
- 错误码重复（不同名字同一个码）：生成失败（`gen_errno.pl:81-91`）。
- `DEFINE_OTHER_MSG_FMT(C1, C0, M4, …)`：`C1` 是别名常量，码取 `C0` 的码，`C0` 不存在时生成失败（`:154-165`）；`C1__USER_ERROR_MSG` 取第一个字符串（`:170-172`）。

#### 3.2.2 M1（MySQL 错误码）

- 写成 `-1`：表里存 **-1**，不是 C0。`.def` 文件头的注释（`ob_errno.def:45-52`"M1 = (M1 == -1 ? C0 : M1)"）描述的是 `ob_mysql_errno_with_check` 的效果，生成的表里确实是 -1（如 `src/share/ob_errno.cpp` 的 `_error_OB_ERROR.mysql_errno = -1`）。
- 写成符号（`ER_WRONG_ARGUMENTS` 等，448 个不同符号）：取 `src/share/mysql_errno.h` 里同名 `#define` 的整数值；找不到时生成失败。
- 写成整数：原样。

#### 3.2.3 表

C++ 的表是 `_errors[OB_MAX_ERROR_CODE]`，下标是 `-err`；码 < 0 的错误才登记（`gen_errno.pl:302-331`），其余下标（包括 0，即 `OB_SUCCESS` 自己）指向全零的 `_error_default`。cause/solution 缺省时是 `"Internal Error"`/`"Contact OceanBase Support"`（`:20-27`；现有 1,546 条都没写，`errno_parse.py`）。Rust 生成同样的稠密表（`static ERRORS: [Option<&ErrorEntry>; 65535]` 或等价的有序数组加二分），查询函数**逐字**照下表（`gen_errno.pl:360-463`），注意大小写不同的两种 "Unknown":

| 函数 | `err == 0` | `-65535 < err < 0` 且已登记 | `-65535 < err < 0` 未登记 | 其余（`err > 0` 或 `err <= -65535`） |
|---|---|---|---|---|
| `ob_error_name` | `"OB_SUCCESS"` | 名字 | `"Unknown Error"` | `"Unknown error"` |
| `ob_error_cause` | `"Not an Error"` | cause（空则 `"Internal Error"`） | `"Internal Error"` | `"Internal Error"` |
| `ob_error_solution` | `"Contact OceanBase Support"` | solution（空则同左） | 同左 | 同左 |
| `ob_strerror` | `"Unknown Error"`（下标 0 是缺省项） | M3（空则 `"Unknown Error"`） | `"Unknown Error"` | `"Unknown error"` |
| `ob_str_user_error` | `None` | M4（空则 `None`） | `None` | `None` |
| `ob_sqlstate` | `"HY000"` | SQLSTATE（空则 `"HY000"`） | `"HY000"` | `"HY000"` |
| `ob_mysql_errno` | `0` | M1 | **`0`**（缺省项的 0） | `-1` |
| `ob_mysql_errno_with_check` | `0` | `M1 < 0 ? -err : M1` | `0` | `err > 0` 时 `err`；`err <= -65535` 时 `-err` |

- `ob_errpkt_errno` = `ob_mysql_errno_with_check`，`ob_errpkt_strerror` = `ob_strerror`，`ob_errpkt_str_user_error` = `ob_str_user_error`（`:452-463`）。
- `g_all_ob_errnos`：全部 1,545 个码（含 0），按数值**降序**（`:107` 的 `reverse sort { $a <=> $b }`），即 `0, -4000, -4001, …, -38105`，与 `src/share/ob_errno.cpp:15529` 的数组逐项相同。
- `OB_LAST_ERROR_CODE` = 最小的码（`:31`、`:145`）。`OB_ERR_SQL_START = -5000`、`OB_ERR_SQL_END = -5999`、`OB_MAX_ERROR_CODE = 65535`。
- 原 `ob_errno.cpp:288-340` 的懒初始化（函数内 `static ObStrErrorInit`）在 Rust 里是常量表，不需要初始化。
- `ob_strerror` 原来在 `ob_log.cpp:73-77` 有一个返回 `"ob_strerror"` 的 weak 版本，被 share 的生成物覆盖；Rust 只有生成的那一个（RULEBOOK 0.4"下移"）。

#### 3.2.4 用户消息函数

- 每个错误码（含两个 OTHER_MSG_FMT 别名）一个 `user_msg::<名字>`。格式串：`_EXT` 取 M4，非 `_EXT` 取 M3（`gen_errno.pl:42-66` 的第 5、6 个实参），OTHER_MSG_FMT 取第一个字符串。码：本错误的码；OTHER_MSG_FMT 取 `C0` 的码（C++ 的 `LOG_USER_ERROR(OB_ERR_DATA_TOO_LONG_MSG_FMT_V2, …)` 把别名常量当 errcode 传，值等于 `OB_ERR_DATA_TOO_LONG`）。
- 参数按格式串的转换说明逐个生成（RULEBOOK §2.3）；全库消息里出现过的写法和个数（`errno_parse.py`，`%.ld` 另计）：

| 写法 | 次数 | 参数 |
|---|---|---|
| `%.*s` | 345 | `p: i32, s: impl ObPrintfStr`（两个参数，与 C++ 调用处的 `len, ptr` 一一对应） |
| `%s`、`%.192s`、`%-.256s`、`%.64s`、`%.80s`、`%.20s`、`%-.384s` | 202、17、10、5、3、2、1 | `s: impl ObPrintfStr` |
| `%ld`、`%.ld` | 40、1 | `core::ffi::c_long` |
| `%lu` | 7 | `core::ffi::c_ulong` |
| `%d` | 25 | `i32` |
| `%u` | 30 | `u32` |
| `%f`、`%lf` | 12、1 | `f64` |
| `%c` | 4 | `i32` |
| `%%` | 2 | 无 |

  `%.ld`（`ob_errno.def:1862` 的 `OB_EST_DEVIA_TOO_LARGE`）是精度为 0 的 `%ld`：值为 0 时输出空串（本机实测 `[%.ld]` 与 0 → `[]`）。
- 函数体：`log_user_msg(level, 码, loc, &ob_printf!("<格式串>", 参数…))`（Libc 方言，因为 `log_user_message` 直接调 `vsnprintf`，`ob_log.cpp:663`）。
- 同时生成 `<名字>__USER_ERROR_MSG: &str` 常量（`gen_errno.pl:167-172`），给 C++ 里直接引用这些宏的地方用。

#### 3.2.5 共存期

`gen_errno.pl` 继续生成 C++ 的三个文件；build.rs 读同一份 `.def` 和 `mysql_errno.h`（`cargo:rerun-if-changed`），两边的码、表逐项一致由 §5 的金标准测试保证（RULEBOOK 0.13"共存期间"）。

### 3.3 错误链与断言宏

1. **求值次数与顺序**：`ob_fail!`/`ob_succ!` 的右值恰好求值一次，先赋值后判断（`ob_macro_utils.h:650-652`）。`ck!`、`oz!`、`ov!` 的日志 KV 里出现的表达式只在 WARN 可打印时**再**求值一次，与 C++ 相同（C++ 的 `LOG_WARN` 实参在 `OB_LOG_NEED_TO_PRINT` 之后才求值，`ob_log_module.h:314-320`）。`min!`/`max!` 被选中的实参求值两次（`:466-476`）。
2. **`failedx!`**：`ret` 已失败时 then 块和全部 else 分支都不执行（`:451` 展开成 `if (OB_FAIL(ret)) {} else if (OB_FAIL(X)) …`）。不能把它写成布尔表达式放进 `if`，那样 ret 已失败时会走进 else。
3. **`ck!`**：条件按顺序检查，第一个为假的生效后停止（`CK_n` 是 if/else-if 链，`ob_check_macros.h:22-53`）；只有 ret 原来成功时才改成 `OB_ERR_UNEXPECTED`（`:57-59`）；日志消息 `"invalid arguments"`，KV 键是条件的字符串化。
4. **`oz!`**：消息 `"fail to exec "` 加函数表达式的字符串化，KV 依次是 `ret` 和附加参数（`:87-112`）。`ozx1!`/`ozx2!` 的码等于给定码时记 DEBUG，否则 WARN（`:121-162`）。
5. **`ov!`**：比较用 `OB_SUCCESS == ret`（`:180`），失败时先赋码再记 WARN `"fail to check (" #cond ")"`，KV 是 ret 加附加参数（`:178-186`）。
6. **字符串化的文本**：C++ 是预处理器对实参的字符串化；Rust 的 `stringify!` 对同一表达式的空白可能不同，表达式本身也可能不同。只出现在日志里（修订 #8 的 `log-content`）。**TO_STRING 的键不同**：键会进入用户可见的文本，译者照 C++ 的字符串化结果写字面量（§3.7.3）。
7. **`*_LOG_RET` 里的 `K(ret)`**：C++ 的 `XXX_LOG_RET(level, errcode, …)` 在块里重新声明 `int ret = errcode`（`ob_log_module.h:776-777`、`:784-1025`），所以其中的 `K(ret)` 打印的是 errcode。Rust 宏不能遮蔽调用方的 `ret`，译者写 `kv!("ret", OB_X)`。
8. **断言**：`ob_assert!` 依次记两行 EDIAG：`assert fail, exp=<表达式>`（mod 为空，`_OB_LOG_RET`，errcode `OB_ERROR`）和 `assert fail BACKTRACE:<lbt>`（`BACKTRACE_RET`，`ob_macro_utils.h:694-701`；ERROR 级别的行尾还会再追加一次 ` BACKTRACE:`，§3.8.3），然后 `ob_abort()`：往 stderr 写 `"OB_ABORT, tid: %ld, lbt: %s\n"` 再 `abort()`（`ob_abort.cpp:42-43`；Windows 先多写一行 `"OB_ABORT called, tid: %ld\r\n"`，`:33-41`）。release 构建同样 abort（RULEBOOK §2.1）。`abort_unless(b)` 不记日志，直接 `ob_abort()`（`ob_abort.h:35-43`，修订 #9）。`ob_safe_assert!` 在对齐构建里只记日志不 abort（`:713-721`）。
9. **`defer!`**：守卫按声明的逆序在作用域结束时执行（C++ 局部对象析构顺序）；`panic = "abort"` 下没有展开路径。`named_defer!` 的 `deactivate()` 之后不执行，`run_and_expire()` 立即执行一次并失效（`ob_defer.h:41-47`）。
10. **`retry_func_on_error!`**：照 `ob_macro_utils.h:665-680`：先置 `ret = err_no`，循环条件 `ret == err_no && !stop`，循环体先清零再调用；循环结束后若 `stop` 为真则 `ret = OB_IN_STOP_STATE`（不论调用结果）。

### 3.4 错误注入（errsim）

1. **始终编译**：C++ 的 `OB_E`、`ERRSIM_POINT_DEF`、`EventTable` 不受 `ERRSIM` 宏控制（`ob_tracepoint.h:108-128` 无条件定义；`OB_E` 有 92 处、`ERRSIM_POINT_DEF` 有 92 处不在 `#ifdef ERRSIM` 里，`errsim_scope.py`），release 二进制里可以用 `ALTER SYSTEM SET_TP` 打开（`src/observer/ob_service.cpp:866-868`）。Rust 同样始终编译；C++ 里 `#ifdef ERRSIM` 包住的代码才用 `#[cfg(feature = "ERRSIM")]`（RULEBOOK 0.15）。这与 RULEBOOK §2.1 的写法不同，见修订 #2。
2. **`call(v)`**：`cond_ == v ? call_0() : 0`（`:259`）。**`call_0()`**：`trigger_freq_ == 0 && occur_ == 0` 时返回 0，否则 `get_event_code()` 截成 i32（`:260-266`）。
3. **`get_event_code()`**（`:267-309`）：先读 `trigger_freq`；`occur_ > 0` 时循环 CAS 把 `occur_` 减一，成功则返回 `error_code_`，读到 ≤ 0 则返回 0；否则 `trigger_freq == 0` → 0；`get_tp_switch()` 为真 → 0；`trigger_freq == 1` → 返回 `error_code_`，并记 WARN `"[ERRSIM] sim error"`（KV：`ret`、`no`、`"name"`、`"describe"`、`error_code`、`trigger_freq`、`KCSTRING(lbt())`），对齐构建（NDEBUG）下这条日志受 `tc_reach_time_interval!(1s)` 限流；`trigger_freq > 1` → `rand() % trigger_freq == 0` 时返回 `error_code_` 并记同样的 WARN（不限流），否则 0。`rand()` 按 RULEBOOK §1 译成以时间为种子的 `ObRandom`，注释"原本就不确定"。
4. **`set_event`**：只拷四个字段（`:251-257`）。`EventTable::set_event(no)`：`no < 0` → `OB_INVALID_ARGUMENT`；在全局表里找第一个 `no_ == no` 的项，找不到 → `OB_INVALID_ARGUMENT`（`:350-369`）。`set_event_char(name)`：`None` → `OB_INVALID_ARGUMENT`；按 ASCII 忽略大小写比较名字，取第一个（`:371-390`）。
5. **登记顺序**：C++ 的全局链表按静态构造顺序追加（`:324,337`），跨编译单元的顺序由链接顺序决定。Rust 的 `generated/errsim_registry.rs` 先列 `ob_tracepoint_def.h` 的 322 项（原顺序），再列各单元的 `errsim_point!`（按源文件路径、行号）。重名时"取第一个"的结果在 C++ 里本来就依赖链接顺序。
6. **原子性**：C++ 里只有 `occur_` 用 `ATOMIC_VCAS`，其余字段在 SET_TP 的线程写、业务线程读，是数据竞争；Rust 全部 `AtomicI64`，`occur_` 的 CAS 用 `SeqCst`，其余 `Relaxed`，标 `FIXED(port): EventItem 字段的数据竞争`。
7. **`OB_I`**：只在非 NDEBUG 构建生效（`ob_tracepoint.h:17-21,71-76`），对齐构建里是空。Rust 在 `cfg(debug_assertions)` 下按 `refine_tp_key` 的键（`"%s:%s:%s"`，文件名取 basename，最长 256 字节，`:160-176`）查表；表里只支持无参处理函数（即 `TP_SET_ERROR` 的形式，`:147-148`），其他形式的 `TP_SET` 写 `TODO(port)`。
8. **`get_tp_switch`/`ObSwitchGuard`**：线程局部 bool，守卫记下旧值、设新值，Drop 时恢复（`:81-104`，`.cpp:25-29`）。

### 3.5 printf 引擎

#### 3.5.1 两种方言

| 方言 | C++ 里对应的实现 | Rust 入口 |
|---|---|---|
| Libc（直接调平台的 `vsnprintf`） | `snprintf`/`sprintf`/`vsnprintf`/`fprintf`/`printf`；`ObLogger::log_user_message`（`ob_log.cpp:663`）；`ob_alloc_printf`（`utility.cpp:1653,1663`）；`ObBufferWriter::vappend_fmt`（`ob_data_buffer.h:280`）；`ObOptimizerContext::add_plan_note`（`ob_optimizer_context.h:646`）；`ObDIActionGuard`（`ob_diagnostic_info_guard.h:265`）；`parray`、`ObString::to_string`、`WarningItem::set` 等内部的 `snprintf` | `ob_printf!(…)`、`snprintf!`、`fprintf!`、`printf!`、`ob_alloc_printf!`、`user_msg::*` |
| Ob | `databuff_vprintf`（`ob_print_utils.cpp:456-521`）、`logdata_vprintf`（`ob_log.cpp:145-210`）、`ObSqlString::vappend`（`ob_sql_string.cpp:183-243`）；经它们的 `databuff_printf`、`BUF_PRINTF`、`logdata_printf`、`_LOG_*`、`append_fmt`、`ObTabletPersister::set_extra_info` | `ob_printf!(@ob …)`、`databuff_printf!`、`buf_printf!`、`logdata_printf!`、`append_fmt!`、`ob_log_fmt!`/`log_*_fmt!` |

两种方言在 Linux 和 macOS 上**完全相同**。只在 Windows 上不同：Ob 方言先改写格式串，`%l` 后跟 `d i o u x X` 改成 `%ll`，并删掉 `'` 标志（上面三处 `#ifdef _WIN32` 块）；所以 Ob 方言的 `%ld`/`%lu`/`%lx` 在所有平台上都是 64 位，Libc 方言的 `%ld` 是 `c_long`（Windows 上 32 位）。这影响 Rust 的参数类型（§3.5.3），见修订 #10。

#### 3.5.2 支持的写法

语法 `%[标志][宽度][.精度][长度]转换`：标志 `- + 空格 # 0 '`；宽度为数字或 `*`；精度为 `.`、`.数字` 或 `.*`（`.` 后为空即精度 0，`%.ld` 就是这样）；长度 `hh h l ll z j`；转换 `d i u o x X c s p e E f F g G a A % m`。全库带字面格式串的调用里出现过的（`fmt_specs.py`，约 3,968 处调用）：转换 `d` 2,576、`s` 2,560、`u` 567、`p` 255、`c` 77、`f` 64、`e` 50、`x` 49、`g` 27、`X` 24、`%` 16、`m` 3、`A` 1、`i` 1；长度 `ld` 1,544、`lu` 435、`lld` 53、`lf` 31、`lx` 29、`hhu` 14、`llu` 10、`le` 9、`lX` 8、`hu` 7、`llX` 7、`llx` 6、`hd` 5、`hhd` 3、`hhx` 2、`ju` 2；标志 `0` 157、`-` 112、`' `（空格加 `'`）51、`'` 36、空格 1；另有 `%*s`、`%-*s`、`%-*.*s`、`%0*d`、`%-*ld`。

#### 3.5.3 实参类型（编译期检查）

`ob_printf!` 为每个转换生成 `::oblib::fmt::arg::<类>(&实参)`，类由 trait 约束，不符即编译错误。整数先按 C 的默认实参提升转成 64 位位模式，再按长度修饰取低位、按转换的有无符号解释，与 C 在类型匹配时的结果一致。

| 转换 | 接受的 Rust 类型 |
|---|---|
| `%d %i %u %o %x %X %c`（无长度、`hh`、`h`）；`*` 宽度/精度 | `i8 u8 i16 u16 i32 u32 bool`（C 的默认提升都是 `int`） |
| Libc 方言的 `%ld %li %lu %lo %lx %lX` | `c_long c_ulong`（LP64 上即 `i64 u64`，也接受 `isize usize`） |
| `%lld %llu %llx %llX %jd %ju`；Ob 方言的 `%ld %lu %lx %lX` | `i64 u64 isize usize` |
| `%zd %zu` | `isize usize` |
| `%f %F %e %E %g %G %a %A`（含 `%lf %le`） | `f64 f32` |
| `%s` | `ObPrintfStr`：`&[u8]`、`&str`、`&[u8; N]`、`&CStr`、`&Vec<u8>`、`&Box<[u8]>`、`Option<&[u8]>`、`Option<&str>` |
| `%p` | `&T`、`*const T`、`*mut T`、`Option<&T>`、`ObVoidPtr` |
| `%m`、`%%` | 不占实参 |

同宽度的有无符号互相接受（`%d` 接 `u32` 时按位解释为有符号），与 C 相同。C++ 编译时关闭了格式检查（`src/oblib/CMakeLists.txt:82,129,173` 的 `-Wno-format`），所以基线里可能有类型不符的调用（如 `%d` 传 `int64_t`）：那是未定义行为，Rust 编译不过时译者按格式串要求的类型做 `as` 转换，标 `FIXED(port): printf 实参与格式不符`。

#### 3.5.4 编译期拒绝的写法

下列写法在 C 里未定义或在平台间结果不同，`ob_printf!` 直接报编译错误（全库现有调用都不涉及；将来出现时译者写 `TODO(port)` 交人决定）：`%n`；`0` 标志用于 `s c p %`（macOS 对 `%05s` 输出 `000ab`，glibc 输出 `   ab`，本机实测）；精度用于 `c p`；`#` 用于 `d i u c s p`；`+`/空格用于无符号转换和 `s c p`；`%%` 带任何标志或宽度（macOS 的 `%5%` 输出 `    %`）；长度 `L t q`；`%ls %lc`；位置参数 `%1$d`；实参个数不符。

#### 3.5.5 输出规则

- 逐字节等于目标平台 C 库的 `vsnprintf`（RULEBOOK §1、0.11.1）。整数：精度是最少位数，精度 0 且值为 0 时不输出数字（`%.0d` → 空，`%5.0d` → 5 个空格，本机实测）；`'` 标志见 §3.5.7；`#` 给 `o` 加前导 0、给非零 `x X` 加 `0x`/`0X`。
- `%c`：写 `(unsigned char)` 的一个字节，值为 0 时写入 NUL（返回值照算，本机实测 `"[%c]"` 与 0 返回 3）。
- `%s`：遇 NUL 即停，有精度时最多输出精度个**字节**（可能截断多字节字符，所以缓冲是字节而不是 `String`，RULEBOOK §2.3）；实参是切片时也在切片末尾停（C 会越过末尾读到 NUL 为止，那是越界）。`*` 精度为负视为未给精度；`*` 宽度为负视为 `-` 加绝对值。
- 浮点：C99 7.21.6.1 的规则；十进制展开必须是二进制值的精确展开再按"四舍六入、恰好一半取偶"舍入（glibc 与 macOS 都如此；本机实测 `%.0f` 对 0.5、1.5、2.5 输出 0、2、2）；`%e` 的指数至少两位；`%g` 先按 `%e` 的指数 X 与精度 P（0 视为 1）选格式再去尾零；`-0.0` 保留负号（`%.18le` → `-0.000000000000000000e+00`）。实现可以用 `core` 的定精度格式化（`{:.*e}` 是精确的、取偶的）产生数字串再按 C 的版式重排，但必须通过 §5 的金标准测试。
- `%p`：非空输出 `0x` 加小写十六进制。
- 返回值是"不截断时应写的字节数"；`buf.len() == 0` 时什么都不写；否则写入 `min(总长, len - 1)` 个字节再写 NUL（与 C 相同，后续代码可能把缓冲区当 C 字符串读）。总长超过 `i32::MAX` 返回 -1。

#### 3.5.6 平台差异

`ob_printf!` 用 `cfg` 区分；基准是 macOS arm64（RULEBOOK 0.15）。macOS 一列是本机实测（`/tmp/ob04probe/p.c`、`p2.c`），glibc 一列由 §5 的 Linux 金标准文件确认。

| 行为 | macOS | glibc（Linux） | 依据 |
|---|---|---|---|
| NaN | 一律 `nan`/`NAN`，任何情况都不带符号（`%+f`、`% f`、负 NaN 都是 `nan`） | 负 NaN 为 `-nan`；`+` 标志 `+nan`；空格标志 ` nan` | RULEBOOK §2.3；行为一致性审查 F14 |
| inf | `inf`/`-inf`/`INF`；`0` 标志不补零（`%05f` → `  inf`） | 同 | 实测 |
| `%p` 空指针 | `0x0` | `(nil)` | 实测 |
| `%s` 空指针 | `(null)`，精度照常截断（`%.3s` → `(nu`） | 精度 < 6 时输出空串，否则 `(null)` | 实测 |
| `%m` | 不支持，原样输出 `m`（`"[%m]"` → `[m]`） | `strerror(errno)` | 实测；`ob_log_print_kv.h:233,253` 的 `KERRMSG` 用它，日志里 macOS 打印 `"m"` |
| `%a` 非规格化数 | 规格化输出（`5e-324` → `0x1p-1074`） | `0x0.0000000000001p-1022` | 实测 |
| Windows | §3.5.1 的改写；UCRT 的 NaN、`%p` 等写法按 UCRT 实测，Windows 的对齐在第 6 步之后（D18），此前标 `TODO(port)` | | |

#### 3.5.7 数值 locale

`src/observer/main.cpp:693-698` 在解析参数之前依次调用 `setlocale(LC_ALL, "")`、`setlocale(LC_CTYPE, "C")`、`setlocale(LC_TIME, "en_US.UTF-8")`、`setlocale(LC_NUMERIC, "en_US.UTF-8")`。所以 C++ 的 `'` 标志在 en_US 下按 `,` 每三位分组（`%'ld` 1234567 → `1,234,567`；`%'.2f` → `1,234,567.89`；`%'e`、`%'g` 的指数形式不分组），小数点是 `.`（本机实测，C locale 下 `'` 不分组）。`'` 标志共约 87 处，都在内存诊断日志里（`ob_memstore_freezer.cpp`、`achunk_mgr.cpp`、`ob_ctx_allocator.cpp` 等）。`ob_printf!` 固定按 en_US.UTF-8 的 LC_NUMERIC 输出（基准平台总有这个 locale）；Rust 的 `main` 照样调用这四个 `setlocale`（`c_strtod` 等依赖 C 库 locale 的函数需要）。RULEBOOK 没有这条规则，见修订 #12。

#### 3.5.8 非字面量格式串

C++ 少数调用传的是变量或常量指针（如 `BUF_PRINTF(OB_LOG_ELLIPSIS)`，`ob_array.h:756`）。值是编译期常量的，译者把它换成字面量（`"..."`，`ob_define.h:1277`）；真正运行期决定的，用 `ObFmtRuntime::parse` + `bind`，类型在运行期核对，不符返回 `OB_INVALID_ARGUMENT`，标 `TODO(port)`。

### 3.6 写缓冲各入口的返回值与 `pos`

- **`databuff_vprintf`/`logdata_vprintf`**（`ob_print_utils.cpp:456-521`、`ob_log.cpp:145-210`）：`buf` 非空且 `0 <= pos < buf_len` 时，在 `buf[pos..buf_len]` 上做一次 `vsnprintf`：返回负数 → `OB_ERR_UNEXPECTED`；`len < buf_len - pos` → `pos += len`；否则 `pos = buf_len - 1`（截断的输出保留，`buf[buf_len-1]` 是 NUL）并返回 `OB_SIZE_OVERFLOW`；`pos` 不在范围内 → 什么都不写，`OB_SIZE_OVERFLOW`。`buf_len` 大于切片长度时按切片长度算（C++ 里是越界写，`FIXED(port)`）。
- **`databuff_printf!(buf, buf_len; …)`**（无 pos，`:381-391`）：局部 `pos = 0`，其余同上。
- **`databuff_printf!(buf, buf_len, pos; obj)`**（模板，`ob_print_utils.h:198-216`）：在剩余空间上调 `to_string(obj)`；`0 <= str_len < buf_len - pos` 时写 NUL、`pos += str_len`；否则 `buf[buf_len-1] = 0`、`pos = buf_len - 1`、`OB_SIZE_OVERFLOW`。`obj` 是空指针（`None`）时打印 `"NULL"`（`:218-228`）。
- **`databuff_printf!(@grow …)`**（`:404-430`）：循环：从保存的 `pos` 起格式化；`OB_SIZE_OVERFLOW` → `multiple_extend_buf`（空缓冲 1024 字节，否则翻倍并拷贝，`:437-454`）后从保存的 `pos` 重来；其他错误直接返回。
- **`ObSqlString::vappend`**（06 号实现，本条是规格，`ob_sql_string.cpp:183-243`）：`n = vsnprintf(data_ + len_, data_size_ - len_)`；`n < 0` → `OB_ERR_SYS`；`n >= 剩余` → `reserve(n + len_)` 后用同一组实参再格式化一次，第二次 `n < 0` → `OB_ERR_SYS`，仍放不下 → `OB_ERR_UNEXPECTED`，否则 `len_ += n`。
- **`ob_alloc_printf!`**（`utility.cpp:1648-1676`）：`n = vsnprintf_len`；`n < 0` → 记 ERROR `"vsnprintf failed"` 返回 `OB_ERR_SYS`；在 arena 里分配 `n + 1` 字节，失败 → 记 ERROR `"no memory"` 返回 `OB_ALLOCATE_MEMORY_FAILED`；格式化；两次长度不等 → `OB_ERR_SYS`；成功时 `result` 是这 `n` 个字节。
- **`snprintf!`**：同 C 的 `snprintf`（§3.5.5 最后一条）。
- **`fprintf!`/`printf!`**：格式化后整段写 fd 2/1（stderr 经 dup2 重定向到 observer.log 时也一样，§3.9.4）。C 的 stdout 在重定向后是全缓冲，Rust 的 stdout 是行缓冲，内容相同、刷出时机不同，只影响日志文件。

### 3.7 TO_STRING 与对象打印

RULEBOOK 0.11.2、§2.11 要求逐字节复刻（`cmp_meta()` 原样返回 `ObCStringHelper::convert` 的结果，`src/sql/engine/expr/ob_expr_collation.cpp:373-387`）。C++ 有五条打印路径，同一类型在不同路径下写法不同；RULEBOOK §2.11 的一句话摘要（"double 用 %.18le…bool 输出 True/False，空指针输出 NULL"）只对其中一部分路径成立，以本节为准，见修订 #1。

#### 3.7.1 五条路径

| 路径 | C++ 入口 | 谁用 |
|---|---|---|
| A `to_string` | 类的 `to_string` 成员；基本类型的 `common::to_string<T>` 特化（`ob_print_utils.h:123-174`、`.cpp:227-358`） | `databuff_printf(buf, len, pos, obj)`、`ObCStringHelper::convert`、`KPC`、类的默认分支 |
| B `databuff_print_obj` | `ob_print_utils.h:396-669` | `BUF_PRINTO`、数组元素、`J_OBJ` |
| C `databuff_print_key_obj` | `:672-916` | `J_KV`、`TO_STRING_KV` 一族（键值之间是 `:`） |
| D `logdata_print_obj` / `logdata_print_value` | `ob_log_print_kv.h:261-389`、`:629-656`；`databuff_print_one_obj`（`ob_print_utils.cpp:432-435`） | `databuff_print_multi_objs`、`LOG_DBA_*_V2` 的值 |
| E `logdata_print_key_obj` | `ob_log_print_kv.h:412-580` | 日志 KV（`K(x)` 等，键值之间是 `=`） |

#### 3.7.2 各类型在五条路径上的写法

下表"k"代表键；C 列、E 列前面按 `with_comma` 先写 `", "`（`WITH_COMMA`，`ob_print_utils.h:32-33`、`ob_log_print_kv.h:52-53`）。

| Rust 类型（C++ 来源） | A | B | C | D | E |
|---|---|---|---|---|---|
| `i8`（int8_t） | `%hhd` | `%hhd` | `k:%hhd` | `%ld`（ObFastFormatInt） | `k=%d` |
| `u8`（uint8_t） | `%hhu` | `%hhu` | `k:%hhu` | `%lu`（同上） | `k=%u` |
| `i16` / `u16` | `%hd` / `%hu` | `%d` / `%u` | `k:%d` / `k:%u` | `%ld` / `%lu` | `k=%d` / `k=%u` |
| `i32` / `u32` | `%d` / `%u` | `%d` / `%u` | `k:%d` / `k:%u` | `%ld` / `%lu` | `k=%d` / `k=%u` |
| `i64` `isize` / `u64` `usize`（int64_t、long、size_t） | `%ld` / `%lu` | `%ld` / `%lu` | `k:%ld` / `k:%lu` | `%ld` / `%lu` | `k=%ld` / `k=%lu` |
| `f32` | `%.9e` | `"%.9e"`（带引号） | `k:"%.9e"` | `%.9e` | `k=%.9e` |
| `f64` | `%.18le` | `"%.18le"` | `k:"%.18le"` | `%.18le` | `k=%.18le` |
| `bool` | `True`/`False` | `true`/`false` | `k:true`/`k:false` | `true`/`false` | `k=true`/`k=false` |
| `ObCChar`（char） | `%c` | `%c` | `k:%c` | `%c` | `k=%c` |
| `str`（字符串字面量，C 字符串） | 原样 | `"%s"` | `k:"%s"` | `"%s"`；D 的 value/multi_objs 分支不加引号 | `k="%s"` |
| `Option<&[u8]>`（可空的 `const char *`） | `NULL` 或原样 | `null` 或 `"%s"` | `k:null` 或 `k:"%s"` | `NULL` 或 `"%s"`；value/multi_objs 分支 `NULL` 或原样 | `k=NULL` 或 `k="%s"` |
| `[u8]`、`Box<[u8]>`（ObString） | `%.*s`，长度取 `min(buf_len, len)` | `"%.*s"`，首尾都是 `"` 时去掉这对引号 | `k:"%.*s"`，同样去引号 | 类的默认分支（A） | 类的默认分支：`k=` 加 A（不加引号） |
| `Option<&T>` `Option<Box<T>>` `Option<Arc<T>>`（可空指针，T 有 to_string） | `NULL` 或 `T` 的 A | `null` 或 `T` 的 B | `k:null` 或 `T` 的 C | `NULL` 或 `T` 的 D | `k=NULL` 或 `k=%p` |
| `ObVoidPtr`（`const void *`、KP） | `NULL` 或 `%p` | `null` 或 `%p` | `k:null` 或 `k:%p` | `NULL` 或 `%p` | `k=NULL` 或 `k=%p` |
| C++ 枚举的新类型（`ob_to_string_enum!`） | `%ld` | `%ld` | `k:%ld` | `%ld` | `k=%ld` |
| `ObError` / `ObResult<()>`（`int ret`） | `%d` | `%d` | `k:%d` | `%ld` | `k=%d` |
| `(A, B)`（std::pair） | — | `[a, b]` | `k:[a, b]` | — | — |
| `&T` `&mut T` `Box<T>` `Arc<T>` | 与 `T` 相同 | 同 | 同 | 同 | 同 |
| 用户类型（只实现 A） | 自己的 to_string | `pos += A`（`pos < 0` 时 `OB_INVALID_ARGUMENT`，`:398-406`） | `k:` 写成功后 `pos += A`；**总返回 OB_SUCCESS**（`:695-717`） | `pos < 0` → `OB_INVALID_ARGUMENT`，`pos >= len` → `OB_SIZE_OVERFLOW`，否则 `pos += A`（`ob_log_print_kv.h:262-274`） | 同样的两项检查，再写 `k=`，成功后 `pos += A`（`:437-454`） |

补充：
- D 列的整数用 `ObFastFormatInt`，要求剩余空间 ≥ 22 字节（`MAX_DIGITS10_STR_SIZE = digits10(uint64) + 3`，`ob_fast_convert.h:164`），不够时直接 `OB_SIZE_OVERFLOW`，即使数字本身放得下（`ob_log.cpp:212-238`）。
- `databuff_print_multi_objs` 的 C 字符串走 `databuff_print_one_obj(const char *)` 这个非模板重载：不加引号，空指针打印 `NULL`（`ob_print_utils.cpp:432-435`）；其余类型走 D。Rust 用 `logdata_print_value` 实现这一列（与 DBA 值的 `logdata_print_value<const char*>` 规则相同，`ob_log_print_kv.h:646-656`）。结束时 `pos < buf_len` 则写 NUL，否则 `OB_SIZE_OVERFLOW`（`ob_print_utils.h:238-247`）。
- C 列对 `const char *` 指针的类型用的是 `T *obj` 模板：空 → `k:null`；函数指针 → `k:%p`；其余递归打印指向的对象（`:718-749`）。E 列对任何指针都只打地址（`:475-498`）。
- B/C 列对 ObString 的长度是 `min(buf_len - pos, length)`（`.cpp:564-595`），`%.*s` 照常遇 NUL 即停。
- 用户类型如果自己需要 `%p`/引号等，照 C++ 在手写的 `to_string` 里写 `buf_printf!`。

#### 3.7.3 `TO_STRING_KV` 一族的结构

- `DEFINE_TO_STRING(body)`（`ob_print_utils.h:977-984`）：`pos = 0`；`BUF_PRINTF("{")`；body；`BUF_PRINTF("}")`；返回 `pos`。三步的返回值都被丢弃。
- `J_KV(k1, v1, k2, v2, …)` → `databuff_print_kv`（`ob_print_kv.h:156-168,236-312`）：第一项 `databuff_print_json_kv`（键为空时只打印值），其余 `databuff_print_json_kv_comma`（键为空时以 `", "` 作键）；每一项只在前面全部成功时才执行。因为类类型的 C 列总返回成功、基本类型的 C 列在溢出时返回 `OB_SIZE_OVERFLOW`，所以溢出之后：遇到基本类型的键就停，遇到类类型的键会继续往后写（结果都是截断在 `buf_len - 1`）。
- `INHERIT_TO_STRING_KV("P", Parent, kv…)`（`:994-1005`）：`{`、`P`（`%s`，不加引号）、`:`、`pos += Parent::to_string(剩余)`、`, `、kv…、`}`。kv 为空时也照样写出 `", "`。父类的 `to_string` 是静态调用（Rust：`ObToString::to_string(&self.base, buf.tail_mut(pos))`，`base` 字段按 RULEBOOK §2.13）。
- `TO_STRING_EMPTY()`（`:1043-1049`）：`{}`。`TO_STRING_KV_WITH_HELPER`（`:1006-1014`）：先建一个 `ObCStringHelper`。
- **键的文本**：`K(x)`/`K_(x)` 的键是预处理器对实参的字符串化（`ob_log_module.h:1240-1241`），会出现在用户可见的输出里（如 `cmp_meta` 的 `{type:"BIGINT", collation:"binary", coercibility:"NUMERIC"}`，conventions.md §2.4）。译者在 `to_string_kv!` 里**照抄 C++ 字符串化的结果**（空白按 C 预处理器的规则合并），不要写 Rust 表达式的 `stringify!`。
- `VIRTUAL_TO_STRING_KV`/`DECLARE_VIRTUAL_TO_STRING`：类的 `<类名>Virt` trait 以 `ObToString` 为 supertrait，不再单独声明 `to_string`；派生类各自 `impl ObToString`。经 `&dyn <类名>Virt` 调 `to_string` 就是 C++ 的虚调用。这条约束 `types.tsv` 生成的 trait 定义（RULEBOOK §2.13），见 §7。

#### 3.7.4 过程宏的展开（实现照抄）

```rust
// to_string_kv!(ObX; "a" => self.a_, "b" => self.b_)
impl ::oblib::fmt::ObToString for ObX {
    fn to_string(&self, buf: &mut ::oblib::fmt::ObBuf) -> i64 {
        let mut pos: i64 = 0;
        let _ = ::oblib::buf_printf!(buf, pos, "{");
        let _ = ::oblib::fmt::databuff_print_kv(buf, &mut pos, &[("a", &self.a_), ("b", &self.b_)]);
        let _ = ::oblib::buf_printf!(buf, pos, "}");
        pos
    }
}
// inherit_to_string_kv!(ObX, "ObTableStmt" => self.base; "a" => self.a_)
//   … "{" / "%s" "ObTableStmt" / ":" / pos += ObToString::to_string(&self.base, buf.tail_mut(pos)) / ", " / kv… / "}"
```

#### 3.7.5 切片与容器

- `[T]`、`Vec<T>`、`Box<[T]>`、`SmallVec<[T; N]>` 的 `ObToString` 一律转发给元素类型的三个切片钩子（§2.4.4）。`u8` 覆盖成 ObString 的写法（上表 ObString 行），所以 `ObStr<'q>`/`Box<[u8]>` 都按 ObString 打印。
- 其余元素类型的默认格式由 05 号文档规定：`ObSEArray`/`ObArray` 是 `[a, b, …]`，元素经 B 列打印，最多 `max_print_count_`（默认 32，`ob_array.h:445`）个，剩余空间不足 `OB_LOG_KEEP_SIZE`（512；正在异步写日志时为 `OB_ASYNC_LOG_KEEP_SIZE` 0，`ob_define.h:1274-1277`）时写 `...` 加 `, ` 后停止（`ob_array.h:741-770`）；元素没有 `to_string` 时（`has_to_string() == false`，即基本类型和指针类）只写 `[cnt:N]`。`ObIArray` 的默认实现是 `[cnt:N, a, b]`（`ob_iarray.h:67-85`）。本模块提供 `ObLogger::is_in_async_logging()` 和这两个常量。
- 05 号的容器类型自己实现 `ObToString`，按各自 C++ 的 `to_string`。

#### 3.7.6 持有锁内数据的类型

RULEBOOK §2.2：`ObToString` 只直接打印锁外字段；锁内部分用 `try_lock()`，拿不到（包括本线程已持有）时在值的位置打印 `<locked>`（C 列 `k:<locked>`，E 列 `k=<locked>`）。任何 `to_string` 都不阻塞。02 号模块为 `Latch<T>`、`RwLatch<T>` 等实现 `ObToString` 时照此办理；本模块不另设包装。

#### 3.7.7 `ObCStringHelper`

照 `ob_print_utils.h:288-385` 的缓冲区规则计算，因为 `to_string` 的输出可能随可用空间变化（如 §3.7.5 的省略号），可用空间必须与 C++ 相同：

1. 初始"当前缓冲"是 1024 字节的预留缓冲，`pos_ = 0`。
2. `convert(obj)`：在长度为 `buf_len_ - pos_` 的空间上做 `databuff_printf(obj)`（A 列，§3.6）。成功且结果长度 `< buf_len_ - pos_ - 1` → 接受，`pos_ += 长度 + 1`（保留 NUL）。
3. 否则若是 `OB_SIZE_OVERFLOW` 或长度达到了 `buf_len_ - pos_ - 1`：已分配的扩展缓冲总量（预留缓冲不计）≥ 64 MB → 返回 `None`，`ob_errno_ = OB_ALLOCATE_MEMORY_FAILED`，记 ERROR `"ObCStringHelper memory has reached the upper limit"`；否则新缓冲大小 = 当前是预留缓冲时 16 KB，否则翻倍，`pos_ = 0`，从第 2 步重试（分配失败记 WARN，恢复原缓冲，返回 `None`）。
4. 其他错误 → `None`，记 WARN `"failed to convert the obj to cstring"`。每次调用结束时 `ob_errno_ = ret`。
5. Rust 的实现可以把接受的结果拷进内部 arena（`&self` 分配），只要第 2、3 步用到的"可用空间"按上面的虚拟缓冲计算；返回的切片借用 `&self`，所以同一条语句里可以多次 `convert`。`is_force_alloc`/`force_alloc`（`.cpp:362-379`，errsim 点 `EN_CSTRING_HELPER_FORCE_MALLOC`）只决定预留缓冲放在栈上还是 arena 里，不可观察，不移植。
6. `convert` 走 A 列：`bool` 是 `True`/`False`，`f64` 是不带引号的 `%.18le`。C++ 对 `const char *` 实参会选中 `databuff_printf(buf, len, pos, fmt, ...)`，把字符串当格式串（`%` 会被解释）；Rust 原样拷贝，含 `%` 时与 C++ 不同而 C++ 本来是读野参数，`FIXED(port)`。

### 3.8 日志行

#### 3.8.1 调用点信息

`ob_log_loc!()` 给出 `file!()`、`line!()` 和函数名；函数名取 `type_name_of_val` 得到的路径里 `__ob_f` 之前、跳过 `{{closure}}` 后的最后一段，对应 C++ 的 `__FUNCTION__`（不带类名）。行头只打印文件的 basename（`strrchr(file, '/')`，`ob_log.cpp:722-723`）。文件名、行号、函数名、`stringify!` 文本、地址、调用栈与 C++ 必然不同，只影响日志内容（修订 #8）。

#### 3.8.2 是否打印

1. **编译期**：DEBUG 级别只在 `ENABLE_DEBUG_LOG` 或非 NDEBUG 时编译（`ob_log_module.h:241-253` 的 `IS_LOG_ENABLED_FOR_DEBUG`）。Rust：`cfg!(any(feature = "ENABLE_DEBUG_LOG", debug_assertions))` 为假时整条语句的实参都不求值（对齐构建两者都为假，RULEBOOK 0.15）。
2. **运行期**：先判断级别，再求值实参（`:268-336`）。`ObLogMod::NONE` 用 `need_to_print(level)`，父模块前缀用 `need_to_print_uint64_t(par, level)`，子模块用 `need_to_print_uint64_t_3(par, sub, level)`；都是 `level <= get_log_level(…)`（`ob_log.cpp:1769-1783`）。
3. **`get_log_level`**（`ob_log.h:1089-1150`）：perf 模式 → `DBA_ERROR`；否则取全局表的对应项；若没有 `disable_thread_log_level()`：线程有会话表 → 取会话表的对应项；否则线程级别不是 `NONE` → 取线程级别。
4. **全局表初值**：构造时全部置为 `DBA_ERROR`（`ob_log.cpp:547`），直到 `set_log_level`/`parse_set`。
5. **级别名**：`{"ERROR","WARN","INFO","EDIAG","WDIAG","TRACE","DEBUG"}` 依次是 0–6，忽略大小写、从后往前找（`ob_log.cpp:1141-1162`、`ob_log.h:840`）；`info_as_wdiag_`（默认真，`ob_log.cpp:545`）且不是 alert 时 `INFO` 映射成 4（`WDIAG`）；所以把级别设成 `"ERROR"` 得到 0，只打 DBA_ERROR。全局表用 `parse_set` 设成 `"PERF"`（忽略大小写）时进入 perf 模式（`:1077-1078`）；其余任何一次成功的 `parse_set`，包括写会话表和线程表的（`:1080-1084` 不区分目标表），都会退出 perf 模式。
6. **`set_log_level_int8_t`**：版本检查通过且 `0 <= level < 7` 时设置全局表（`:614-622`）；`check_and_set_level_version`：`version <= 0` 总是通过，否则只接受更大的版本（`:668-679`）。
7. **`parse_check`/`parse_set`**（`:966-1117`）：输入先去首尾空白（C locale 的 `isspace`）拷进 64 KB 缓冲（`OB_MAX_CONFIG_VALUE_LEN`，`ob_define.h:1448`）。没有 `:` 时整串是一个级别名，作用于全部模块。否则按 `父.子:级别` 逐项解析，项之间用 `,` 或 `;`，各段去空白后截到 19/9 个字节（`MAX_MOD_NAME_LENGTH = 20`、`MAX_LEVEL_NAME_LENGTH = 10`）；`ALL.*` 是全部，`父.*` 是父模块整行，否则是子模块。错误码 `OB_LOG_PARSER_SYNTAX_ERR`/`OB_LOG_MODULE_UNKNOWN`/`OB_LOG_LEVEL_INVALID`，这三种记 WARN `"invalid log_level=%s, ret=%d"`（`%s` 是从 `valid_length` 起的剩余输入）。全部项解析成功后才按顺序应用。末尾调用的 `update_easy_log_level()` 是空函数（`src/oblib/rpc/frame/ob_net_easy.cpp:538-540`），Rust 不调用。
8. **名字表**：只登记了 16 个父模块和 72 个子模块（`ob_log_module.ipp`，`logmods.py`），没登记的名字（如 `MVCC`、`SQL.DAS`、`PL.RESV`）解析时得到 `OB_LOG_MODULE_UNKNOWN`。C++ 在静态构造里登记；Rust 用常量表，内容等于静态初始化完成后的状态（附录 A）。

#### 3.8.3 行格式

1. **行头**（`ob_log.cpp:708-769`），三种：
   - 普通（observer.log）：`"[%04d-%02d-%02d %02d:%02d:%02d.%06ld] %-5s %s%s (%s:%d) [%ld][%s][%s] [lt=%ld]%s "`，依次是本地时间（微秒）、级别名（`ob_log.h:840`，左对齐到 5 列）、`mod_name_`（自带尾随空格，`NONE` 为空串）、函数名、文件 basename、行号、线程号、线程名、trace id、本线程**上一条**日志的耗时 `last_logging_cost_time_us_`、`"[errcode=%d]"`（只有 DBA_ERROR、DBA_WARN、WARN、ERROR 四个级别有，其余为空串）。
   - trace 文件（`mod_name_` 以 `"[FLT"` 开头，`:104-112`）：`"[%04d-%02d-%02d %02d:%02d:%02d.%06ld] [%ld][%s][%s] "`。
   - alert 文件（DBA 级别且 dba_event 非空）：`"%04d-%02d-%02d %02d:%02d:%02d.%06ld|%s|%s|%s|%d|%ld|%s|%s|%s|%s:%d|"`。seekdb 里所有 DBA 日志都经 `OB_PRINT_DBA` 传入空的 dba_event（`ob_log.h:1401`），这个分支实际不会走到；照样移植。
2. **时间**：日志项的时间戳是 TSC 时钟的微秒数（`OB_TSC_TIMESTAMP.current_time()`，`ob_log.h:1229`）；转本地时间用 `ob_fast_localtime`：每线程第一次（缓存为空）调 `localtime_r`，之后秒数变化时用 `ob_localtime` 的算术和**固定的** `timezone` 偏移（`src/oblib/lib/utility/utility.cpp:2153-2211`），不处理夏令时，照样复刻，标 `BUG(port): ob_localtime 用固定的 timezone 偏移，不处理夏令时（复现：TZ=America/New_York，夏令时期间第二秒起的日志时间差一小时）`。
3. **正文**：KV 形式是 `info(k=v, k=v)`，没有 KV 时只有 `info`（`ob_log.h:523-547`），`info` 为空时写 `NULL`/`NULL(`（`ob_log_print_kv.h:390-410`）；printf 形式是格式化结果；DBA 值形式是各值首尾相接（`ob_log.h:589-603`）。
4. **行尾**（`check_log_end`，`ob_log.h:969-987`）：最后一个字符不是 `\n` 时补一个，再写 NUL；超出时截到 `buf_size - 2`。ERROR（即 EDIAG，3）级别再调用 `backtrace_if_needed`：从最后的 `\n` 处开始写 `" BACKTRACE:"` 加 `lbt()`，空间不够时从后往前保留调用栈，然后重新 `check_log_end`（`ob_log.cpp:1565-1593`）；探针要求的回溯写 `" PROBE_BACKTRACE:"`。
5. **两遍格式化**（`ob_log.h:1239-1307`）：第一遍缓冲 1024 − 8 = 1016 字节；溢出则丢掉、换 65536 − 8 = 65528 字节重来；仍溢出则把 `pos` 截到 `buf_len - 2`、补行尾、标记 `size_overflow`。8 字节是 C++ 的尾部魔数（`TAIL_MAGIC`），Rust 不需要魔数检查（缓冲区不会越界），但两个容量必须照抄，因为对象的 `to_string` 可能按可用空间改变输出（§3.7.5）。
6. **trace 模式**（`ob_log.h:1017-1043`、`ob_log.cpp:513-533,586-650`）：`set_trace_mode(true)` 之后本线程的日志写进线程局部的 64 KB 缓冲（按需分配），缓冲满时先把已有内容作为一条日志刷出、再重试一次；`print_ob_log_trace_buf!` 刷出；关闭 trace 模式时释放缓冲。

#### 3.8.4 防重入

`OB_PRINT` 用线程局部的 guard（`ob_log.h:1378-1384`），`do_log_message` 在格式化期间把线程局部的 `disable_logging_` 置真、结束时恢复（`:1215-1217`）。所以格式化一条日志时（比如对象的 `to_string` 里）再记的日志被丢弃。Rust 的守卫只拥有旧值（RULEBOOK 0.5 允许）。

#### 3.8.5 `do_log_message` 的流程（`ob_log.h:1202-1359`）

1. 探针：`probe_cnt_ > 0` 时按"文件 basename + 行号"匹配（`:1159-1200`），`disable` → 不打印；`bt` → 强制回溯；`abort` → `ob_abort()`。
2. 文件类型 `get_fd_type_int32_t`；`log_size = limited_left_log_size_ + 1024`。
3. 队列满（`is_queue_full()`）→ 直接放弃，不计数。
4. 限流：`check_tl_log_limiter` 在 `ob_log.cpp:1603` 提前返回，总是允许（后面的代码不可达），所以 `need_print_log_limit_msg` 和丢弃计数分支实际不会走到。Rust 照样总是允许，不移植不可达部分。
5. 分配日志项（§3.9.3）、两遍格式化（§3.8.3）、成功后 `limited_left_log_size_ = max(0, 长度 − 1024)`。
6. 异步：提交到环形队列，`current_written_count_[tl_type]` 加一；同步（`enable_async_syslog = false`）：当前线程直接 `flush_logs_to_file(&item, 1)` 后释放（`:1311-1335`）。
7. 耗时：超过 500 ms（`OB_BUILD_PACKAGE` 时 1 s）时往 stderr 写 `<行头>LOGGER COST TOO MUCH TIME, cost: %ld, <时间守卫的 to_string>, BACKTRACE: <lbt>`（`:1338-1357`）；`last_logging_cost_time_us_` 记本次耗时。
8. 初始化之前（`ObLogger::init` 之前）分配一律失败（`OB_NOT_INIT`，`ob_log.cpp:1666-1668`），日志被丢弃，只在 stderr 是终端时写一行 `LOG_STDERR`。

#### 3.8.6 线程级别与会话表

- C++ 的线程局部存一个指向会话 `ObLogIdLevelMap` 的指针（`ob_log.h:957-961`），会话在请求开始时装入、结束时清掉（如 `src/observer/mysql/obmp_query.cpp:369,427`）。Rust 的线程局部不能持借用，也不能放带 Drop 的类型（外部线程也会记日志，RULEBOOK 0.5），所以 `init_log_id_level_map(Some(map))` 把会话表**拷贝**进线程局部（4,161 个字节，每请求一次，且只在会话设置过 `ob_log_level` 时发生）。
- 区别：C++ 在同一请求里修改会话表（`SET ob_log_level`，`src/sql/session/ob_basic_session_info.cpp:2745`）后立刻影响本线程后续日志的取舍；Rust 要到下一次装入才生效。只影响哪些日志行被打印（修订 #8 的 `log-content`）。
- `process_thread_log_id_level_map`（hint 的 `log_level`，`src/sql/ob_sql.cpp:2222,3500`）直接解析进线程局部的表并启用，与 C++ 相同（`ob_log.cpp:240-257`）。

#### 3.8.7 DBA 日志

- `LOG_DBA_ERROR/WARN(OB_X, kv…)`（非 V2，`ob_log_module.h:1126-1142`）：`need_to_print(DBA_ERROR/DBA_WARN)` 时 `OB_PRINT("[" 前缀名 "] ", 级别, OB_X, ob_strerror(OB_X), kv…)`。前缀名是 `ObLogMod::prefix_name_`（`LOG_PREFIX_TO_STRING(USING_LOG_PREFIX)` 字符串化的是前缀名本身，如 `[SQL_ENG] `，不是 `[SQL.ENG] `）。
- `LOG_DBA_*_V2`/`LOG_DBA_*_`（`:1150-1196`）：ERROR/WARN 的条件是 `(trace id 非默认 || reach_time_interval!(10 s)) && need_to_print_dba(level, false)`，INFO 是 `reach_time_interval!(10 s) && need_to_print_dba(INFO, false)`（`DBA_LOG_PRINT_INTERVAL`，`:1124`）；输出经 `OB_PRINT_DBA` → `log_message_value(mod_name_bracket, dba_event = None, …)`，写进 observer.log，值首尾相接、不加引号（`ob_log.h:1387-1405`）；`dba_event` 和 `print_rd_log` 实参被忽略。
- `need_to_print_dba`（`ob_log.cpp:1785-1812`）：`level > alert_log_level_`（常量 `DBA_INFO`，`:540`）→ 否；`DBA_INFO` → 是；`force` → 是；`DBA_ERROR/WARN`：trace id 为默认值 → 是；与本线程上次的 trace id 不同 → 记下、计数置 1、是；相同 → 计数加一，每第 20 次记一条 WARN `"print too many alert logs with same trace id!"`，返回否。
- 每条 ERROR 级别的 `OB_PRINT` 先调 `issue_dba_error`（`ob_log.h:1374-1376`，Windows 除外），它用 `LOG_DBA_FORCE_PRINT(ERROR, …)`，而 `need_to_print_dba(3, true)` 因 `3 > alert_log_level_` 返回否，所以什么也不输出（`ob_log.cpp:1759-1767`）。照样调用。
- DBA 步骤表（`ob_log_dba_event.h:75-104`）：每个步骤表一个线程局部计数，`inc` 返回 `(cur % count) + 1`；`DBA_STEP_INC_INFO(list)` 展开成值 `"["`、`"list"`、`" "`、步骤号、`"/"`、总数、`"] "`。

#### 3.8.8 用户消息附带的日志

`_LOG_USER_MSG`（`ob_log_module.h:1033-1039`）在写警告缓冲区之后，若 `need_to_print(WARN)`，再以 mod 为空、WARN 级别、给定 errcode 记一条 printf 形式的日志，正文是**完整的**格式化结果（日志缓冲区 1016/65528 字节，不受 512 字节限制）。`FORWARD_USER_NOTE` 用 KV 形式：正文是消息本身，KV 是 `ret=<errcode>`（`:1107-1113`）。

### 3.9 写线程、日志文件、轮转

#### 3.9.1 环形队列的记账

C++ 的 `ObRingBuf` 是 2 MB 字节环（`ob_ringbuf_log_writer.h:77-112`、`.cpp:68-160`）。Rust 保留**同样的整数记账**、把条目内容换成拥有型对象，因此"何时分配失败、丢几条日志"与 C++ 逐条相同：

1. `alloc(total_len)`：`total_len` 向上取 8 的倍数；在锁内计算 `ring_off = push % buf_len`；`ring_off + total_len > buf_len` 时先插一个长度为 `buf_len - ring_off` 的填充项（已回滚），真正的条目从环首开始；`push + needed - pop > buf_len` 时返回 -1；否则插入"忙"条目、`push += needed`、返回条目的位置（`.cpp:68-120`）。
2. `commit(pos, item)`/`rollback(pos)`：把对应条目改成已提交（带上日志项）或已回滚；`commit`/`rollback` 之后唤醒写线程一次（`signal(1)`，`.cpp:259-275`）。
3. 写线程每次从 `pop` 起取条目，遇到"忙"就停，最多 64 条已提交的组成一批，跳过回滚项；处理完一批之后才 `advance_pop`（只增不减，`.cpp:148-153,306-344`）。
4. ObLogger 申请的长度是 `ENTRY_HEADER_SIZE(8) + LOG_ITEM_SIZE + alloc_len`（`ob_log.h:1249`、`ob_log.cpp:1669`），`LOG_ITEM_SIZE = sizeof(ObPLogItem)`，64 位平台上是 72 字节（虚表指针 8 加字段 64），5b 用 C++ 的布局测试确认。

#### 3.9.2 写线程

`ObLogger::init` 启动一个名为 `OB_PLOG` 的线程（`ob_log.cpp:1388-1399`）：`while !stopped { key = cond.get_key(); if !do_flush() && !stopped { cond.wait(key, 500 ms) } }`（`ob_ringbuf_log_writer.cpp:292-304`）。`stop` 置停止标志并唤醒全部；`wait` 合并线程；`destroy` 释放环。ObLogger 的 `stop` 先关异步（`ob_log.cpp:569-573`），`destroy` 还置 `stop_append_log_`、关异步、关限流（`:578-584`）。

#### 3.9.3 分配失败的等待

`alloc_log_item`（`ob_log.cpp:1660-1701`）：`stop_append_log_` → `OB_ERR_UNEXPECTED`；未初始化 → `OB_NOT_INIT`；环满 → `alloc_wait_count_ += 1`，按 `get_wait_us(level)` 每次睡 `min(剩余, 10)` 微秒并重试；仍失败 → `alloc_drop_count_ += 1`、`OB_ALLOCATE_MEMORY_FAILED`、`LOG_STDERR`。`get_wait_us`：force allow → 100；DBA_ERROR、DBA_WARN、ERROR → 100；WARN → 10 + 2 × 补偿；INFO、TRACE → 2 + 补偿；其余 0（`:1625-1658`）。补偿 = `POP_COMPENSATED_TIME[本任务类型之外待写日志更多的类型数]` = 0–4 µs（`:102,1550-1563`）。Rust 的日志项缓冲用 `try_reserve`，分配失败按同一分支处理。

#### 3.9.4 文件

- `ObPLogFileStruct::open/reopen`（`ob_async_log_struct.cpp:48-113`）：`O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC`，权限 0644，`fstat` 记下 dev/ino；`redirect_flag` 时把新 fd `dup2` 到 2 和 1；已打开时 `dup2` 到原 fd 号再关闭新 fd（**fd 号不变**，其他线程手里的 fd 继续可用）；`file_size_ = st_size`。文件名超过 251 字节 → `OB_SIZE_OVERFLOW`。未打开的文件的 fd 是 2（`:39-46`），所以 trace.log、alert.log（seekdb 从不打开）的内容写到 fd 2，即重定向后的 observer.log。observer 以 `no_redirect_flag = false` 打开 observer.log（`src/observer/main.cpp:758`），即重定向 stderr/stdout。
- `flush_logs_to_file`（`ob_log.cpp:1444-1528`）：批内第一项的时间戳距上次超过 2 s → 检查 observer.log 是否被删或换了 inode（`check_file`，`:853-863`，是则 `reopen`），并用 `statfs` 取剩余空间，`> 32 MB` 才可写（`can_print_`）；可写时，距上次超过 5 s（或 `force_check_`）且 CAS 抢到 → `check_file()` 三个文件；按文件类型分组 `writev`（fd > 0 才写），累计写入字节和次数；`max_file_size_ > 0` 时逐个检查是否轮转。
- **轮转**（`:771-844`）：本次写了字节、且 `file_size_ >= max_file_size_` 时 `trylock`（抢不到就跳过）；`access(R_OK)` 成功才改名为 `"%s.%04d%02d%02d%02d%02d%02d%03d"`（本地时间、毫秒）；改名成功且 `max_file_index_ > 0` → 在 `file_index_mutex_` 下把旧文件名放进 `file_list_`，长度 ≥ `max_file_index_` 时弹出最旧的并 `unlink`（压缩开启时不删，`:1752-1757`）；重新打开（同上的 dup2 规则）；`file_size_ = 0`；observer.log 写一行新文件信息；唤醒压缩器。
- **新文件信息行**（`:1727-1750`）：`set_new_file_info` 设置过时写 `"[%04d-…%06ld] INFO  New syslog file info: [%s]\n"`，截到 512 字节并强制以 `\n` 结尾。`set_file_name`（`:624-634`）只在 `open` **失败**时才调用它（`&&` 写反），照样复刻，标 `BUG(port): set_file_name 只在 open 失败时写新文件信息（复现：正常启动，observer.log 第一行没有 "New syslog file info"）`。
- **`set_max_file_size`**：负数或大于 1 GB → 1 GB（`:876-883`）。**`set_max_file_index`**：负数 → 15；大于 10,240 → 10,240；大于 0 时 `record_old_log_file`（`:885-900`）。
- **`record_old_log_file`**（`:1207-1379`）：对 observer.log、trace.log 和 alert.log 所在目录，收集"文件名以 `basename.` 开头，且（未开压缩，或匹配 `^[a-z]+\.log\.[0-9]+$`）"的普通文件；按文件名最后 17 个字符（时间后缀）排序，短于 17 的排在前；依次放进 `file_list_`，超过上限时删最旧的。未打开的文件名为空串，`dirname("")`/`basename("")` 都是 `"."`，前缀就是 `".."`，照 POSIX 的这两个函数的规则实现。正则用手写匹配代替 `regcomp`（三种模式都是固定形状，`ob_log_compressor.h:56-58`）。
- C++ 的 `file_size_mutex_` 用 `trylock`，`file_index_mutex_` 用 `lock`；Rust 用 02 号的 `Latch`，语义相同。

#### 3.9.5 `process_batch`

（`ob_log.cpp:1417-1442`）写文件、统计每秒异步刷出条数（用批内最后一项的时间戳与上次统计时间比较，超过 1 s 时用 `ObTimeUtility::current_time()` 重算），然后在 `disable_logging_` 下析构这批日志项。

### 3.10 日志压缩

`ObLogCompressor`（`ob_log_compressor.h:36-226`、`.cpp:46-618`）逐行移植：每 5 s 一轮的定时任务（`oblib::thread` 的 Timer）；只处理 observer.log、trace.log 两类（`:59-63`）；`set_compress_func` 只接受 `none` 和 `zstd_1.3.8`（`.cpp:184-209`，经 `ObCompressorPool`，即 `zstd138_sys`）；压缩块大小 `OB_MALLOC_BIG_BLOCK_SIZE`，输出缓冲多留 `块/128 + 531` 字节；压缩后的文件名加 `.zst`、保留原 mtime（`utime`，Rust 用 `File::set_modified`）；磁盘占用超过 `syslog_disk_size` 或剩余空间不足（2 GB/4 GB 的保留阈值，`:50-52`）时按时间删最旧的，候选集是容量 21 的二叉堆（`ObBinaryHeap`，05 号）。剩余空间用 `log::sys::statfs_free_bytes`。

### 3.11 限流

- `check_tl_log_limiter` 总是允许（§3.8.5 第 4 条），所以限流器的 `try_acquire` 不会被日志模块调用；但 `is_force_allows`/`reset_force_allows` 每条日志都会用到（`ob_log.cpp:1530-1548`，取线程局部的限流器，没有则取默认限流器，`enable_log_limit_` 为假时一律否）。
- 限流器类型照 C++ 移植：`ObISyslogRateLimiter::try_acquire` 有线程局部的重入保护、`rate_ == 0` 返回 `OB_EAGAIN`（`ob_syslog_rate_limiter.h:51-66`）；`ObSimpleRateLimiterImpl::try_acquire` 的令牌算法（`.cpp:58-84`，时间用 `ObTimeUtility::current_time()`，原来是 `gettimeofday`，同一时钟）；`ObSyslogSimpleRateLimiter` 对 ERROR、DBA_WARN、DBA_ERROR 放行（`.h:120-131`）；`ObSyslogSampleRateLimiter` 前 `initial` 个放行、之后每 `thereafter` 个放行一个（`.cpp:86-111`）。share 的 `ObLogRateLimiter`（`src/share/ob_task_define.cpp:37-57`）实现 `ObISyslogRateLimiterVirt`。

### 3.12 限流宏

全部以 `ObClockGenerator::getClock()`（微秒）为时间源（`ob_macro_utils.h:767-916`）；`i` 先转成 `i64`。

| 宏 | 状态 | 何时为真 |
|---|---|---|
| `reach_time_interval!(i)` | 每调用点 `static AtomicI64 LAST = 0` | `old = LAST.load(Acquire)`；`i + LAST.load(Acquire) < cur` 且 `LAST.compare_exchange(old, cur, SeqCst)` 成功。初值 0，所以**第一次调用为真**（`:791-803`；C++ 读了两次 volatile，照样读两次） |
| `reach_time_interval_no_instant!(i)` | 外层同上，另有 `static AtomicBool FIRST_CALL` | 外层为真且不是第一次为真（`:805-817`） |
| `reach_time_interval_range!(i, j)` | `static LAST`，初值是**第一次执行到该语句时**的时钟（C++ 的函数内 static 动态初始化） | 若 `j + LAST < cur` 先把 `LAST` CAS 成 `cur`（不看结果）；再按 `i` 做一次与第一行相同的判断（`:861-878`）。Rust 用 `OnceLock<AtomicI64>` 或"0 表示未初始化 + CAS 初始化"实现首次初始化 |
| `reach_count_interval!(i)` | `static AtomicI64 COUNT = 0` | `COUNT.fetch_add(1, SeqCst) + 1` 能被 `i` 整除（`:880-889`） |
| `reach_thread_time_interval!(i)` | `thread_local! Cell<i64>`，常量初始化为 `i64::MIN` 表示未初始化 | 首次访问时置为当前时钟并返回假（C++ 的 `RLOCAL_STATIC(…) = getClock()`，`:819-831`）；之后同第一行的判断（C++ 在线程局部上用 CAS，单线程下等价于比较后赋值） |
| `tc_reach_time_interval!(i)` | `thread_local! Cell<i64> = 0` | `i + last < cur` 时更新并为真（`:767-778`）；第一次为真 |
| `tc_reach_count_interval!(i)` | `thread_local! Cell<i64> = 0` | `++count % i == 0`（`:780-789`） |
| `reach_count_per_sec!(i)` | 每调用点一个 `static Latch<(lo, hi)>`（RULEBOOK §2.7） | 锁内：`lo + 1 s > cur` 时 `hi += 1`，`hi > i` 为真；否则 `(lo, hi) = (cur, 1)`（`:834-859`） |
| `execute_count_per_sec!(i)` | 同上 | 锁内：`lo + 1 s < cur` 时 `(cur, 1)`，否则 `hi += 1`；`hi <= i` 为真（`:891-916`；修订 #11） |

### 3.13 警告缓冲区、用户消息与错误包

#### 3.13.1 所有权

C++ 的线程局部是一个裸指针（`ob_warning_buffer.h:241-245`，`.cpp:24`），指向会话的 `warnings_buf_`（`src/observer/mysql/obmp_base.h:122-128`）、线程默认缓冲（`GET_TSI`，`:261-265`）或某个局部缓冲（`ObWarningBufferIgnoreScope`、`ob_trigger_handler.cpp:449-453`、`ob_transform_utils.cpp:10090-10149`）。装入和清除常在不同函数里（`obmp_query.cpp:361` 装入，`obmp_base.cpp:370` 在 `do_after_process` 里清除），会话代码在装入期间还直接读同一个缓冲区（`src/observer/virtual_table/ob_virtual_warning.cpp:84-85`、`src/observer/mysql/ob_mysql_end_trans_cb.cpp:262,267`），异步回调还可能在另一个线程上读它。所以 Rust 用共享句柄 `ObWarningBufferRef`（`Arc<Latch<ObWarningBuffer>>`）：会话字段、线程局部、局部作用域都持有句柄（拥有型，符合 RULEBOOK 0.5"线程局部只存拥有型或 'static 的数据"），调用形状与 C++ 一一对应。RULEBOOK 0.5 以 `with_warning_buffer(&mut buf, || …)` 为例的闭包写法放不下这些用法，见修订 #3；`with_warning_buffer(&ObWarningBufferRef, f)` 仍提供给成对出现的局部装入。

规则：持有 `lock()` 守卫期间不得调用会写警告缓冲区的宏或函数（`log_user_*!`、`forward_user_*!`、`insert_warning_buffer`）；`Latch` 不可重入，违反时死锁，实现里在加锁前 `debug_assert!(!is_wrlocked_by_self())`。

#### 3.13.2 写入

1. `log_user_msg(level, code, loc, args)` = `_LOG_USER_MSG`（`ob_log_module.h:1033-1039`）：
   1. `len = vsnprintf(&mut [u8; 512], args)`（`log_user_message`，`ob_log.cpp:654-666`；`len` 是不截断时的长度，可能大于 511）；
   2. `insert_warning_buffer(level, code, buf, len)`：仅当 `ObWarningBuffer::is_warn_log_on()`（静态开关，observer 在 `src/observer/main.cpp:791` 打开）且 `len > 0`，且线程局部有缓冲区时：`USER_ERROR` → `set_error(buf, code)`；`USER_WARN` → `append_warning(buf, code, None)`；`USER_NOTE` → `append_note(buf, code)`（`ob_log.cpp:1164-1182`）。**格式化结果为空串时什么都不写**，之后发错误包时会用默认文本（§3.13.4）；
   3. 记 §3.8.8 的 WARN 日志。
2. `WarningItem::set` 用 `snprintf(msg_, 512, "%s", s)`：遇 NUL 停、最多 511 字节（`ob_warning_buffer.h:70`）；`set_sql_state` 最多 5 字节（`:79`）。`set_error` 只改消息和码，不动 SQLSTATE、行列号（`:117`）。
3. `append_msg`（`:183-211`）：`error_ret_` 不为成功时什么都不做；`append_idx_ >= item_.count()` 时追加一个新项（`try_reserve` 失败 → `error_ret_ = OB_INIT_FAIL`，之后直到 `reset` 都不再追加），否则覆盖 `item_[append_idx_]`；写消息、码、级别、SQLSTATE（给了才写）；`append_idx_ = (append_idx_ + 1) % 64`；`total_warning_count_ += 1`（u32，不封顶）。
4. 读：`get_readable_warning_count = min(total, 64)`；`get_warning_item(idx)`：`idx` 越界 → `None`；`total > 64` 时位置是 `(append_idx_ + idx) % 64`，否则 `idx`（`:165-181`）。
5. `reset` 清 `append_idx_`、`total`、`err_`、`error_ret_`，**不**清 `item_` 的内容（`:157-163`）；`reset_warning` 只清两个计数。
6. `assign`（`operator=`，`.cpp:33-47`）：先 `reset`，拷贝 `item_`，失败时 `error_ret_ = 失败码` 且不拷其余字段，否则拷 `err_`、`append_idx_`、`total`。
7. `log_user_error_with_line_col!` 在写入之后，若 `USER_ERROR`、开关打开、有缓冲区，则 `set_error_line_column(line, col)`（`ob_log.cpp:1184-1205`）。
8. `forward_user_error!(code, msg)`/`forward_user_warn!` = `_LOG_USER_MSG(level, code, "%s", msg)`；`forward_user_error_msg!(code, "fmt", …)` 用给定格式串；`forward_user_note!(code, msg)`：`msg` 非空时直接 `insert_warning_buffer(USER_NOTE, code, msg, strlen(msg))`，再按 §3.8.8 记 KV 日志（`ob_log_module.h:1098-1113`）。这组宏的 errcode 可以是变量（如 `src/sql/engine/cmd/ob_ddl_executor_util.cpp:85` 的 `FORWARD_USER_ERROR(ret, …)`），可以是 0。

#### 3.13.3 线程局部函数

`ob_get_tsi_err_msg(code)`：有缓冲区且其错误码等于 `code` 时返回其消息，否则空（`:246-254`）。`ob_setup_default_tsi_warning_buffer` 装入本线程的默认缓冲区（首次使用时创建）。`ob_reset_tsi_warning_buffer` 对当前缓冲区 `reset`。`ObWarningBufferIgnoreScope::new` 新建一个已 reset 的缓冲区装入并记下原句柄，Drop 时装回原句柄并 reset 自己的缓冲区（`:276-296`）。

#### 3.13.4 `send_error_packet` 需要的支持

`send_error_packet`（`src/observer/mysql/obmp_packet_sender.cpp:528-716`）由 observer 的译者逐行翻译（RULEBOOK §2.3）；本模块保证它用到的每一项与 C++ 相同：

1. 日志里的客户端错误码：`ob_mysql_errno(err)`，为 -1 时用 `err`（`:536-541`）。
2. 文本选择（`:553-571`）：`wb = ob_get_tsi_warning_buffer()`；有 `wb` 且（`wb.get_err_code() == err` 或 `err == OB_ERR_SIGNAL_EXCEPTION`）时取 `wb.get_err_msg()`（到 NUL 为止）；结果为空时：调用方传入的 `errmsg` 非空 → 用它；否则 `err == OB_ERR_SIGNAL_EXCEPTION` → 留空；否则 `snprintf(msg_buf, 512, "%s", ob_errpkt_strerror(err))`。
3. 按客户端字符集转码后截到 **512** 字节（不是 511，`:588`）。
4. `enable_rich_error_msg` 的追加（`:595-641`）在裁判里关闭（RULEBOOK 0.11.1）。
5. 错误码与 SQLSTATE（`:644-655`）：`err == OB_ERR_SIGNAL_EXCEPTION` 且有 `wb` 时，错误码取 `wb.get_err_code()` 截成 `u16`，SQLSTATE 取 `wb.get_sql_state()`，为空则 `ob_sqlstate(err)`；否则错误码 `ob_errpkt_errno(err)` 截成 `u16`，SQLSTATE `ob_sqlstate(err)`。
6. `wb` 在 Rust 里是 `Option<ObWarningBufferRef>`；上面三次读取各用一次便捷方法（各自加锁），中间不持锁。

### 3.14 其他

- **`KTIME`**（`ob_log_time_fmt.cpp:47-64`）：`"%04d-%02d-%02d %02d:%02d:%02d"` 加 `".%ld"`（微秒部分**不补零**：`ts % 1000000` 为 5 时写 `.5`，负数写 `.-5`），本地时间用该文件自己的线程局部缓存走 `ob_fast_localtime`。作为日志 KV 的值按 C 字符串打印（E 列 `k="…"`）。`KTIMERANGE(ts, b, e)`（`:66-72`）在 27 字节缓冲里把下标 `TIME_RANGE_INDEX[2e+1]` 处置 NUL、从 `TIME_RANGE_INDEX[2b]` 开始取；起点越过实际长度时 C++ 读到上一次的残留字节，Rust 输出空串，`FIXED(port)`。
- **`lbt()`**：最多 100 帧，每帧是相对于所在模块基址的偏移，格式 `"0x%lx"`，帧间一个空格，总长不超过 1024 字节（`ob_backtrace.cpp:139-182`）；`g_enable_backtrace` 为假时为空。实现用 `backtrace::trace`（安全接口）和 `Frame::module_base_address()`。
- **`KERRMSG`/`KERRNOMSG(n)`**：`"\"%m\""`；`KERRNOMSG` 先把 errno 设为 n、打印后恢复（`ob_log_print_kv.h:223-259`）。Rust 在格式化时读当前线程的 errno（`std::io::Error::last_os_error()`）；macOS 上 `%m` 本来就输出 `m`（§3.5.6）。
- **`LOG_STDERR`/`LOG_STDOUT`**：只有 `isatty(fd) == 1` 时才写（`ob_log_print_kv.h:66-67`），重定向到日志文件之后都不写。Rust 用 `std::io::IsTerminal`。
- **`ObTraceLogConfig::get_log_level`**：第一次调用读环境变量 `_OB_TRACE_LOG_LEVEL_`，按与日志相同的 7 个名字忽略大小写**从前往后**找（与 `level_str2int` 的方向相反），找不到保持 `TRACE`（`ob_trace_log.cpp:24-52`）。`PRINT_TRACE` 在 `is_trace_log_enabled()` 且 `get_log_level() <= OB_LOGGER.get_log_level()` 时以 mod `"[TRACE]"`、TRACE 级别打印 `"[normal]"` 加 `TRACE=<buf>`；`FORCE_PRINT_TRACE` 在 `is_trace_log_enabled()` 且全局级别不是 `DBA_ERROR` 时以 mod `"[TRACE] "` 打印（`ob_trace_log.h:68-86`）。
- **`CHECK_TRACE_TIMES`**（`:28-52`）：线程局部的 `{id0, id1, count}`；trace id 为空或首段为 0 时可打印；与上次相同则计数加一，超过 `times` 后不打印；不同则重置为 1。

### 3.15 并发、内存序与 FIXED(port) 清单

本模块的共享可变状态全部改成原子量或 Latch。按 RULEBOOK §2.7：`ATOMIC_*` 一律 `SeqCst`；纯计数、统计、配置开关用 `Relaxed`（这些字段只在本模块内部，由本文直接认定）。C++ 里下列字段无锁、非原子地跨线程读写，是数据竞争，Rust 改成原子量并标 `FIXED(port)`：

| 字段（C++ 位置） | Rust | 内存序 |
|---|---|---|
| `ObLogIdLevelMap::log_level_`、`non_mod_level_`（`ob_log.h:162-163`） | `AtomicI8` | Relaxed |
| `ObLogger` 的 `enable_perf_mode_`、`disable_thread_log_level_`、`force_check_`、`redirect_flag_`、`can_print_`（volatile）、`enable_async_log_`、`use_multi_flush_`、`stop_append_log_`、`enable_log_limit_`、`info_as_wdiag_`、`level_version_`、`max_file_size_`、`max_file_index_`（`ob_log.h:855-902`） | `Atomic*` | Relaxed |
| `ObLogger::probes_`、`probe_cnt_`（`:894-899`） | `Latch<…>` 加 `AtomicI32` 计数（为 0 时不加锁） | — |
| `ObLogger::new_file_info_`（`:901`） | `Latch<Option<&'static [u8]>>` | — |
| `last_check_file_ts`、`last_check_disk_ts`（`ob_log.cpp:95-96`，前者用 CAS） | `AtomicI64` | CAS 为 SeqCst，其余 Relaxed |
| 统计计数（`dropped_log_count_`、`written_count_` 等，C++ 用 `ATOMIC_AAF`） | `AtomicI64` | 增加 SeqCst，读取 Relaxed |
| `ObWarningBuffer::is_log_on_`（`.cpp:23`）、`g_enable_backtrace`（`ob_backtrace.cpp:122`） | `AtomicBool` | Relaxed |
| `ObTraceLogConfig::log_level_`（volatile）、`got_env_`（`ob_trace_log.cpp:26-27`） | `AtomicI32`/`AtomicBool` | Relaxed |
| `EventItem` 除 `occur_` 外的字段（§3.4） | `AtomicI64` | Relaxed |
| `REACH_*` 的 `static volatile int64_t` | `AtomicI64` | 见 §3.12 |
| `ObPLogFileStruct` 的 `fd_`、`write_count_`、`write_size_`、`file_size_`（`ob_async_log_struct.h:129-133`） | 原子量 | 写入统计 SeqCst，其余 Relaxed |

其他：
- 环形队列的条目状态由 Latch 保护，不再依赖 `WEAK_BARRIER` 与 busy 位的发布顺序（`ob_ringbuf_log_writer.cpp:90-130`）。
- 线程局部全部是 `const` 初始化、无 Drop 的 `Cell`（日志的防重入、级别、`tl_type_`、限流器指针 `Cell<Option<&'static dyn ObRateLimiterVirt>>`、时间缓存等），外部线程也能安全记日志（RULEBOOK 0.5）；只有 trace 模式缓冲区和警告缓冲区句柄带 Drop，外部线程不会用到。
- 本模块没有接受的偏差需要 `DIVERGE(port)`；与 C++ 不同的只有上表，§2.4.2 的 `tail_mut`、§2.5.3 的 `show_probe`、§3.5.3、§3.6、§3.7.7、§3.14 里标出的 `FIXED(port)`，以及日志内容（修订 #8）。§3.5.4 的写法在编译期拒绝，现有代码不涉及。

## 4. unsafe

1. `ob_macros`、`oblib::error`（含 errsim）、`oblib::fmt`、`oblib::log`（`log::sys` 除外）**没有 unsafe**。`oblib` 的 crate 级 lint 是 `#![deny(unsafe_code)]`（RULEBOOK 0.3），这些模块不加 `allow`。对外暴露的 API 全部是安全的。
2. 下列做法使安全代码足够：错误码常量用 `const` 里的 `NonZeroI32::new(c).unwrap()`；`ObCStringHelper` 借用 `&self` 的结果来自 01 号的 arena（bumpalo 的 `&self` 分配）；环形队列用整数记账加拥有型条目（§3.9.1），不在字节环里放置对象；警告缓冲区用 `Arc<Latch<…>>`；线程局部只有 `Cell`；`lbt` 用 `backtrace::trace`（安全接口）；文件读写、`writev`、`fstat` 的 dev/ino、改名、删除、目录遍历、`isatty`、改 mtime 都用 std。
3. `oblib::log::sys` 需要 libc，**目前不在 RULEBOOK §3 的白名单里**（修订 #6）。每处的安全论证：

| 函数 | libc 调用 | 安全论证要点 | C++ 依据 |
|---|---|---|---|
| `dup2(src, dst)` | `libc::dup2` | `src` 是本模块持有的 `File` 的 fd，调用期间不会被关闭；`dst` 只能是 1、2 或同一个 `ObPLogFileStruct` 持有的 `File` 的 fd，替换后该 `File` 继续拥有原 fd 号；不涉及内存 | `ob_async_log_struct.cpp:85-104`、`ob_log.cpp:817-836` |
| `statfs_free_bytes(path)` | `libc::statfs` | 路径拷成 `CString`；输出用 `MaybeUninit`，只在返回 0 后读取；按平台取 `f_bfree * f_bsize` | `ob_log.cpp:1464-1468`、`ob_log_compressor.cpp:591-618` |
| `localtime_r(t)` | `libc::localtime_r` | 输入是栈上的 `time_t`，输出 `MaybeUninit<tm>`，返回空指针时按全零处理；拷成纯 Rust 的 `ObTm` | `utility.cpp:2204` |
| `timezone_minutes()` | 读 `timezone`（Linux 为 `__timezone`） | 只在本线程第一次 `localtime_r` 之后读（C 库此时已完成 tzset），之后不再被写；用 `read_volatile` 读外部静态量 | `utility.cpp:2161-2168` |
| `strerror(errnum, buf)` | `libc::strerror_r`（XSI 版本） | `buf` 是调用方的切片，长度如实传入；只在 glibc 上用（macOS 的 `%m` 不展开） | `ob_log_print_kv.h:233,253` |
| `setlocale_like_main()` | `libc::setlocale` ×4 | 参数是字面量 `CString`；只在 `main` 起任何线程之前调用一次（RULEBOOK 0.5 的 main 顺序） | `src/observer/main.cpp:693-698`（修订 #12） |

   - 每处写 `// SAFETY:`，模块开 `#![deny(unsafe_op_in_unsafe_fn)]`，配 ASan 下跑的单测（RULEBOOK §3）。
   - 修订 #6 不被接受时的退路：去掉 `dup2`（stderr/stdout 不再进 observer.log）、`can_print_` 恒为真、`%m` 在 glibc 上也输出 `m`，三者都要记成 A.4 偏差；`localtime_r` 没有安全的替代（std 没有本地时间），只能由别的白名单模块提供同样的封装。

## 5. 测试

全部放在 `rust/port/oblib/tests/` 与 `rust/port/ob_macros/tests/`（RULEBOOK §4"测试"）；金标准文件放 `rust/port/oblib/tests/golden/`。

### 5.1 1d 就能产出的金标准

| 名称 | 怎么产出 | 比什么 |
|---|---|---|
| `golden/errno/` | 不需要构建 C++：测试运行时直接解析已入库的 `src/share/ob_errno.cpp`（`_error_X` 结构体各字段、`g_all_ob_errnos`）和 `src/share/mysql_errno.h` | 生成表的每个字段；`-65536..=1` 加若干正数上全部查询函数的结果按 §3.2.3 的表算出的期望值 |
| `golden/printf/<平台>.tsv` | 1d 写一个纯 C 程序 `tests/golden/src/printf_golden.c`（只调平台 `snprintf`，不依赖 seekdb），在 macOS arm64 与 Linux x86-64 上用系统编译器各跑一次，入库输出；每行是"格式串、实参的类型与位模式、返回值、输出字节的十六进制" | `ob_printf!` 的输出与返回值逐字节相同。语料：`fmt_specs.py` 抽出的全部字面格式串 × 每种实参类型的边界值（0、±1、各宽度的最小最大值、`0x80` 以上的字节）；浮点的恰好一半、非规格化数、±0、±inf、带符号的 NaN 配 `+`/空格/`0`/`-` 标志；`%.*s` 的负精度与超长精度；嵌入 NUL 的 `%s`；`%c` 为 0；512/1016/65528 字节附近的截断；en_US.UTF-8 下的 `'` 标志；再加 10⁶ 条随机组合 |
| `golden/user_msg/<平台>.tsv` | 同一个 C 程序对 1,547 条用户消息的格式串调用 `snprintf(buf, 512, …)`（实参取固定集合，含超过 511 字节的结果） | `user_msg::*` 写进警告缓冲区的字节与码 |

### 5.2 5b 由 C++ 测试程序产出的金标准

C++ 驱动程序链接基线构建（`build_release`）里的 oblib 目标文件，源码放 `rust/port/oblib/tests/golden/src/`：

| 名称 | 内容 |
|---|---|
| `golden/to_string/` | 一组覆盖 §3.7.2 每个单元格的 C++ 结构体（`TO_STRING_KV`、`INHERIT_TO_STRING_KV`、指针、`ObString` 首尾引号、嵌套、`ObSEArray` 0/1/33 个元素与 `[cnt:N]` 的情形），在缓冲区长度 0–2,048 的每一个值上调用 `to_string` 的输出；`ObCStringHelper::convert` 对各基本类型与超过 1 KB/16 KB 的对象的输出；`databuff_print_multi_objs`、`BUF_PRINTO` |
| `golden/log_line/` | 对固定的时间、线程号、线程名、trace id 调 `log_head`/`log_tail`/`check_log_end`/`backtrace_if_needed`（驱动程序把这些私有函数抄一份）；各种 K 宏、`KR`、`KPC`、`KCSTRING`、`KTIME`、`KPHEX` 的正文；1016/65528 两遍格式化的边界 |
| `golden/errno_lookup/` | 用 C++ 的查询函数跑 `-65536..=65536`，与 5.1 的期望值互相印证 |
| 布局测试 | `sizeof(ObPLogItem) == 72`（§3.9.1 的记账依赖它）、`sizeof(RingBufEntry) == 8`、`ObWarningBuffer::WarningItem::STR_LEN`、`ObLogIdLevelMap` 的尺寸 |

### 5.3 单元测试（1d）

1. `ObError`/`ObResult` 的尺寸断言；`ObResultExt`、`ObErrcode` 的各分支。
2. 每个错误链宏：`ret` 的取值变化、求值次数（带计数器的表达式）、日志内容（测试用的日志接收端）；`failedx!` 带 else/else-if 的三种情形；`ck!` 的条件在 WARN 关闭时只求值一次、打开时两次；`*_LOG_RET` 的 `kv!("ret", OB_X)` 规则。
3. `ob_assert!`/`abort_unless`：在子进程里触发，检查退出信号与 stderr 文本。
4. `defer!` 三种写法与执行顺序；`min!`/`max!` 对 NaN 的结果与 C 宏相同。
5. errsim：`call`/`call_0`/`get_event_code` 的每个分支、`occur_` 计数、`cond`、`set_event`/`set_event_char`（大小写、找不到、负数）、登记表顺序、`ObSwitchGuard`、`ob_e!` 的四种写法、`errsim_point!`。
6. 编译期检查：类型不符、个数不符、§3.5.4 的每一种写法都编译失败（`compile_fail` 文档测试）。
7. `databuff_vprintf`、`databuff_printf!` 四种写法、`snprintf!`、`ob_alloc_printf!`、`multiple_extend_buf` 在 `pos` 的各种边界上的返回值、`pos` 与 NUL 位置。
8. TO_STRING：§3.7.2 每个单元格一条字面期望；溢出后"类类型继续、基本类型停止"；`inherit_to_string_kv!`；`<locked>`（与 02 号联测）；`ObCStringHelper` 的虚拟缓冲规则（用输出随可用空间变化的测试对象）、64 MB 上限（用报告超大长度的测试对象）。
9. 日志级别：`parse_set` 的语法与错误码、`valid_length`、未登记模块、`PERF`、`level_str2int` 的方向与 `INFO→WDIAG`、版本号、线程级别与会话表的优先级、`disable_thread_log_level`；DEBUG 在对齐构建下不求值实参；级别不够时不求值实参。
10. 日志行：三种行头、`[errcode=…]` 只在四个级别出现、`lt=` 是上一条的耗时、KV 正文与无 KV 正文、行尾换行与 ERROR 回溯、两遍格式化、`size_overflow`、trace 模式、防重入（`to_string` 里记日志被丢弃）、探针、初始化之前丢弃、DBA 日志的条件与每第 20 次的 WARN、`issue_dba_error` 无输出、`_LOG_USER_MSG` 的日志行。
11. 写线程与文件（临时目录）：环形队列的记账与 C++ 算术的逐步对照（直接把 `ob_ringbuf_log_writer.cpp:68-120` 的算术写成测试里的参照实现）、回绕填充、分配失败的等待与丢弃计数、同步模式、轮转文件名与 `file_list_` 淘汰、压缩开启时不删、`record_old_log_file`（17 字符排序、短名、`..` 前缀）、`check_file` 在删除或改名后重开、`dup2` 重定向（子进程）、磁盘空间门槛（测试钩子注入 `statfs` 结果）、新文件信息行与 `set_file_name` 的复刻缺陷。
12. 压缩：`zstd_1.3.8` 往返、文件名模式、删除策略（注入的磁盘大小）、mtime 保留。
13. 限流宏：用 `ObClockGenerator` 的测试时钟逐条验证 §3.12 的表（首次是否为真、调用点之间互不影响、线程局部版本）。
14. 警告缓冲区：64 条环的顺序、511 字节截断、遇 NUL 停、空消息不写、开关关闭不写、`OB_INIT_FAIL` 之后停止追加（测试钩子让 `try_reserve` 失败）、`reset` 不清 `item_`、`assign`、`ObWarningBufferIgnoreScope`、`with_warning_buffer`、会话句柄与线程局部句柄看到同一份数据、行列号、`forward_user_*!`、`ob_get_tsi_err_msg`。
15. `send_error_packet` 的支持：用 §2.6 的 API 写出 §3.13.4 的选择逻辑做表驱动测试（含 SIGNAL、`errmsg`、空文本、码截成 u16）。

### 5.4 loom 与 Miri

- loom：`ObRingBuf`（两到三个写者 `alloc/commit/rollback`、一个读者：已提交项不丢、按分配顺序交付、记账不超过 2 MB、`pop` 单调）；`reach_time_interval!`（同一区间内并发调用恰好一个为真）；`EventItem` 的 `occur_`（并发调用恰好注入 k 次）；`ObBaseLogWriter`（`append_log` 满时等待与超时、`stop` 排空）。
- Miri：printf 引擎、TO_STRING、`ObCStringHelper`、单线程的环形队列记账、警告缓冲区、KV；`log::sys` 不跑 Miri，跑 ASan。

## 6. 翻译者怎么用

| # | C++（位置） | Rust |
|---|---|---|
| 1 | `src/sql/session/ob_basic_session_info.cpp:1085-1100`<br>`int ret = OB_SUCCESS;`<br>`if (OB_UNLIKELY(SYS_VAR_INVALID == (sys_var_id = find(...)))) { ret = OB_ERR_SYS_VARIABLE_UNKNOWN; }`<br>`else if (OB_FAIL(ensure_sys_var_loaded(sys_var_id))) {}`<br>`else if (OB_UNLIKELY(store_idx < 0) …) { ret = OB_ERR_UNEXPECTED; LOG_ERROR("got store_idx is invalid", K(ret), K(store_idx)); }` | `let mut ret: ObResult = Ok(());`<br>`if { sys_var_id = find(…); SYS_VAR_INVALID == sys_var_id } { ret = Err(OB_ERR_SYS_VARIABLE_UNKNOWN); }`<br>`else if ob_fail!(ret = self.ensure_sys_var_loaded(sys_var_id)) {}`<br>`else if *store_idx < 0 \|\| … { ret = Err(OB_ERR_UNEXPECTED); log_error!(ret, "got store_idx is invalid", k!(ret), k!(store_idx)); }` |
| 2 | `src/rootserver/fork_table/ob_fork_database_service.cpp:71-74`<br>`ret = OB_ERR_BAD_DATABASE;`<br>`LOG_USER_ERROR(OB_ERR_BAD_DATABASE, arg.src_database_name_.length(), arg.src_database_name_.ptr());` | `ret = Err(OB_ERR_BAD_DATABASE);`<br>`log_user_error!(OB_ERR_BAD_DATABASE, arg.src_database_name_.length(), arg.src_database_name_.ptr());`<br>（`%.*s` 对应 `(i32, &[u8])` 两个实参） |
| 3 | `src/pl/sys_package/ob_dbms_stats.cpp:1155-1157`<br>`ObCStringHelper helper;`<br>`LOG_USER_ERROR(OB_TABLE_NOT_EXIST, helper.convert(p.db_name_), helper.convert(p.tab_name_));` | `let helper = ObCStringHelper::new();`<br>`log_user_error!(OB_TABLE_NOT_EXIST, helper.convert(&p.db_name_), helper.convert(&p.tab_name_));`<br>（`convert` 返回 `Option<&[u8]>`，`None` 按 `(null)` 打印，与 C++ 的 nullptr 相同） |
| 4 | `src/sql/resolver/ddl/ob_create_table_resolver.cpp:1121`<br>`OZ(column.set_column_name(select_item.alias_name_));` | `oz!(ret, column.set_column_name(&select_item.alias_name_));` |
| 5 | `src/pl/ob_pl_resolver.cpp:829`<br>`CK(PL_VAR == type \|\| PL_COND == type \|\| PL_HANDLER == type \|\| PL_CURSOR == type);` | `ck!(ret, PL_VAR == type_ \|\| PL_COND == type_ \|\| PL_HANDLER == type_ \|\| PL_CURSOR == type_);` |
| 6 | `src/oblib/common/json_type/ob_json_base.cpp:6423-6425`<br>`int tmp_ret = vec.insert_unique(…);`<br>`if (tmp_ret != OB_SUCCESS && tmp_ret != OB_CONFLICT_VALUE) { ret = COVER_SUCC(tmp_ret); }` | `let tmp_ret: ObResult = vec.insert_unique(…);`<br>`if tmp_ret.is_err() && !tmp_ret.is(OB_CONFLICT_VALUE) { ret = cover_succ!(ret, tmp_ret); }` |
| 7 | `src/sql/code_generator/ob_dml_cg_service.cpp:3292-3293`<br>`if (FAILEDx(cg_.generate_rt_exprs(access_exprs, ctdef->pd_expr_spec_.access_exprs_))) {}` | `failedx!(ret = self.cg_.generate_rt_exprs(qa, &access_exprs, &mut ctdef.pd_expr_spec_.access_exprs_), {});` |
| 8 | `src/storage/blocksstable/ob_block_manager.cpp:73-76`<br>`if (REACH_TIME_INTERVAL(300 * 1000 * 1000L)) { LOG_INFO("no more disk space to extend", K(ret), K(datafile_maxsize), K(datafile_size)); }` | `if reach_time_interval!(300 * 1000 * 1000i64) { log_info!("no more disk space to extend", k!(ret), k!(datafile_maxsize), k!(datafile_size)); }` |
| 9 | `src/sql/resolver/expr/ob_raw_expr_util.cpp:1128-1129`<br>`_LOG_WARN("expr_str is %p, sub_query_info = %p", expr_str, sub_query_info);`<br>`… sql_str.append_fmt("SELECT %.*s", static_cast<int>(buf_len), expr_str)` | `log_warn_fmt!(ret, "expr_str is %p, sub_query_info = %p", expr_str, sub_query_info);`（两个实参是 `Option<&_>`）<br>`… ob_fail!(ret = append_fmt!(sql_str, "SELECT %.*s", buf_len as i32, expr_str))` |
| 10 | `src/storage/tx/ob_tx_ctx.cpp:587-593`<br>`CtxLockGuard guard(lock_);`<br>`… TRANS_LOG(WARN, "invalid argument", K(ret), K(expire_ts), KPC(this));` | 守卫里是锁内数据 `g`：`ob_log!(TRANS, WARN, ret, "invalid argument", k!(ret), k!(expire_ts), kpc!(Some(&*g)));`；锁外字段另行列出；不得 `kpc!(Some(self))`（RULEBOOK §2.2） |
| 11 | `src/sql/resolver/ddl/ob_alter_table_stmt.h:91`<br>`INHERIT_TO_STRING_KV("ObTableStmt", ObTableStmt, K_(stmt_type), K_(alter_table_arg), K_(index_arg_list));` | `inherit_to_string_kv!(ObAlterTableStmt, "ObTableStmt" => self.base; "stmt_type" => self.stmt_type_, "alter_table_arg" => self.alter_table_arg_, "index_arg_list" => self.index_arg_list_);` |
| 12 | `src/pl/ob_pl_allocator.cpp:80`<br>`ret = OB_E(EventTable::EN_4) OB_SUCCESS;` | `ret = ob_e!(EventTable::EN_4);` |
| 13 | `src/sql/engine/cmd/ob_ddl_executor_util.cpp:85`<br>`FORWARD_USER_ERROR(ret, error_message.user_message_);` | `forward_user_error!(ret, error_message.user_message_);`（errcode 可以是变量） |
| 14 | `src/sql/engine/dml/ob_trigger_handler.cpp:447-463`<br>`ObWarningBuffer trigger_diagnostics;`<br>`ObWarningBuffer *outer = ob_get_tsi_warning_buffer();`<br>`ob_setup_tsi_warning_buffer(&trigger_diagnostics);`<br>`OZ(…);`<br>`ob_setup_tsi_warning_buffer(outer);`<br>`if (… && OB_NOT_NULL(outer) && …) { outer->set_error(trigger_diagnostics.get_err_msg(), trigger_diagnostics.get_err_code()); … }` | `let trigger_diagnostics = ObWarningBufferRef::new();`<br>`let outer = ob_get_tsi_warning_buffer();`<br>`ob_setup_tsi_warning_buffer(Some(trigger_diagnostics.clone()));`<br>`oz!(ret, …);`<br>`ob_setup_tsi_warning_buffer(outer.clone());`<br>`if … && outer.is_some() && … { let td = trigger_diagnostics.lock(); outer.as_ref().unwrap().set_error(td.get_err_msg(), td.get_err_code()); … }` |
| 15 | `src/sql/engine/expr/ob_expr_collation.cpp:378-384`<br>`ObCStringHelper helper;`<br>`const char *ptr = helper.convert(expr.args_[0]->obj_meta_);`<br>`if (OB_ISNULL(ptr)) { ret = OB_ERR_UNEXPECTED; }`<br>`else if (OB_FAIL(ob_alloc_printf(res_str, res_alloc, "%s", ptr))) {}` | `let helper = ObCStringHelper::new();`<br>`let ptr = helper.convert(&arg0.obj_meta_);`<br>`if ptr.is_none() { ret = Err(OB_ERR_UNEXPECTED); }`<br>`else if ob_fail!(ret = ob_alloc_printf!(res_str, res_alloc, "%s", ptr)) {}` |
| 16 | `src/sql/engine/expr/ob_expr_in.cpp:503`<br>`DEFER(exec_ctx.get_sql_ctx()->is_prepare_protocol_ = is_ps_backup);`（之后函数体继续使用 `exec_ctx`） | `let mut sql_ctx = defer!(&mut *sql_ctx => \|c\| { c.is_prepare_protocol_ = is_ps_backup; });`，之后经 `sql_ctx` 访问；只改不再使用的变量时直接写 `defer!(x = y);` |

## 7. 依赖

**白名单 crate**（RULEBOOK §1）：

| crate | 用在哪里 |
|---|---|
| `syn`、`quote`、`proc-macro2` | `ob_macros`（`ob_printf!`、TO_STRING 系列） |
| `libc` | `oblib::log::sys`（§4） |
| `backtrace` | `lbt`、`light_backtrace` |
| `smallvec` | 为 `SmallVec<[T; N]>` 实现 `ObToString` |
| `zstd138_sys`（经 oblib 的压缩器封装） | 日志压缩 |

errno 的 build.rs 只做字符串处理，不需要 serde。不使用 `log`、`tracing`、`thiserror`、`anyhow`、`regex`。

**其他 1d 模块**：

| 模块 | 用到的东西 |
|---|---|
| 01 `mem` | `Arena`（`ObCStringHelper`、`ob_alloc_printf!`），`try_reserve` 约定 |
| 02 `sync` | `Latch`、`LatchGuard`、`is_wrlocked_by_self`、`FutexCond`（写线程、`ObBaseLogWriter`）；为 `Latch<T>`/`RwLatch<T>` 实现 `ObToString` 的 `<locked>` 规则（§3.7.6） |
| 03 `thread-signal` | 启动写线程和压缩定时器（`oblib::thread`）；`GETTID`、线程名；`main` 开头调用 `log::sys::setlocale_like_main()` 和 `ObLogger::init` 的位置；时钟 `ObClockGenerator`/`ObTimeUtility`/`OB_TSC_TIMESTAMP`（见 §8 Q5） |
| 05 `containers` | 容器的 `to_string` 格式（用本模块的切片钩子）；`ObBinaryHeap`（压缩器） |
| 06 `string-num-sort-serial` | `ObStringExt`（`ptr()`/`length()` 给 `%.*s`）、`ObSqlString::{append_fmt, assign_fmt, vappend}` 收 `&ObFmtArgs`、`ObSensitiveString`、`ObFastFormatInt`、`ob_serialize_member!`（`WarningItem`）、prelude |
| 09 `parser-sys-generators` | build.rs 的组织方式与 `generated/` 目录约定；`errsim_point!` 登记表的扫描脚本 |

**对其他文档的约束**：`types.tsv` 生成 `<类名>Virt` trait 时，凡 C++ 类有 `VIRTUAL_TO_STRING_KV`/`DECLARE_VIRTUAL_TO_STRING` 的，trait 以 `ObToString` 为 supertrait（§3.7.3）；会话的 `warnings_buf_` 字段类型是 `ObWarningBufferRef`（§3.13.1）；会话的日志级别表类型是 `ObLogIdLevelMap`。

## 8. 开放问题

每题给推荐项；Q1–Q12 同时排进 `migration/infra/amendments.tsv`（编号即修订号，行首的 doc 列是 `04-error-log-fmt`）；Q13–Q15 不涉及 RULEBOOK 的错误，只在本文决定。

- **Q1（修订 #1）TO_STRING 格式摘要**：RULEBOOK §2.11 的"bool 输出 True/False，空指针输出 NULL，double 用 %.18le"只对 §3.7.1 的 A 路径成立；`TO_STRING_KV` 实际是 `true/false`、`null`、带引号的 `"%.18le"`，`ObSEArray` 是 `[a, b]` 而不是 `[cnt:N, a, b]`。推荐：把 §2.11 的格式条目改成"按 04 号 API 文档 §3.7 的分路径表逐字节复刻"。备选：在 RULEBOOK 里展开整张表。
- **Q2（修订 #2）errsim 的编译开关**：C++ 的注入点在 release 里有效，RULEBOOK §2.1 要求用 `cfg(feature = "errsim")` 控制（且与 0.15 的 `ERRSIM` 特性名大小写不一致）。推荐：`ob_e!`/`errsim_point!` 始终编译，只有 C++ 里 `#ifdef ERRSIM` 的代码用 `cfg(feature = "ERRSIM")`。备选：照 RULEBOOK 关掉，把"release 里 `SET_TP` 不生效"记成 A.4 偏差（裁判 B 类不受影响）。
- **Q3（修订 #3）警告缓冲区的所有权**：推荐共享句柄 `Arc<Latch<ObWarningBuffer>>`，线程局部存句柄，保留 C++ 的装入/清除调用形状（§3.13.1）。备选：RULEBOOK 0.5 的闭包式 `with_warning_buffer(&mut buf, …)`，需要把 obmp 一族的请求处理改写成闭包结构，并规定会话代码在装入期间一律经线程局部访问。
- **Q4（修订 #4）日志写线程的启动时机**：RULEBOOK 0.5 要求在 `main` 开头启动，C++ 在 `ObServer::init` 里（`src/observer/ob_server.cpp:650`），之前的日志全部丢弃（§3.8.5 第 8 条）。推荐：组合根在 C++ 的同一位置显式调用 `ObLogger::init`（仍是显式启动，`LazyLock` 不起线程）。备选：照 RULEBOOK 提前启动，`ObServer::init` 之前的日志在 Rust 版会落盘（只影响日志文件）。
- **Q5（修订 #5）时钟的归属**：`reach_*` 宏、日志行头、限流器要用 `ObClockGenerator::getClock()`、`ObTimeUtility::current_time()`、`OB_TSC_TIMESTAMP`，日志的耗时统计要用 `ObBasicTimeGuard`（`ob_common_utility.h:67-…`），RULEBOOK 0.2 的 1d 范围里没有 `time`。推荐：把这几个时钟门面和 `ObBasicTimeGuard` 划给 03 号模块在 1d 实现。备选：本模块在 1d 内部各写一份最小实现，第 3 步再换成译出的单元。
- **Q6（修订 #6）`log::sys` 的 unsafe**：推荐把 `rust/port/oblib/src/log/sys/**` 加进 RULEBOOK §3 白名单（§4 列出全部 6 个函数）。备选：放进 03 号的某个已在白名单的模块（如新设 `oblib::signal::sys`），本模块调用它。
- **Q7（修订 #7）`snprintf` 的译法**：RULEBOOK §2.3、§2.5 把写定长缓冲的 `snprintf` 译成 `databuff_printf!`，但很多调用点用了 `snprintf` 的返回值（"应写长度"，如 `ob_log.cpp:402`、`ob_string.h:633`、`ob_backtrace.cpp:168`），`databuff_printf!` 的返回值是错误码。推荐：`snprintf(buf, n, …)` → `snprintf!(buf, …) -> i32`（保留 C 名与返回值），`databuff_printf` 只对应 `databuff_printf!`。
- **Q8（修订 #8）日志内容的偏差项**：文件名、行号、函数名、`stringify!` 文本、地址、调用栈、会话日志级别在请求内修改后的生效时机（§3.8.6）都必然或按设计与 C++ 不同，只影响日志。推荐：A.4 加一项 `log-content`（裁判不比较日志），代码里不逐处标记。
- **Q9（修订 #9）`abort_unless`/`ob_abort`**：RULEBOOK §2.1 把它们和 `OB_ASSERT` 一起译成 `ob_assert!`，但它们不记日志、`ob_abort()` 也没有条件。推荐：保留同名函数（§2.1）。
- **Q10（修订 #10）Windows 上 `%ld` 的宽度**：Ob 方言在 Windows 上把 `%ld` 当 64 位（§3.5.1），与 RULEBOOK 0.11.7"`long` 用 `c_long`"冲突。推荐：Ob 方言的 `%l` 一律收 64 位整数，Libc 方言收 `c_long`。
- **Q11（修订 #11）`EXECUTE_COUNT_PER_SEC`**：与 `REACH_COUNT_PER_SEC` 同为 128 位 CAS，RULEBOOK §2.7 只点名了后者。推荐：同样用 `Latch`。
- **Q12（修订 #12）进程的 locale**：`main` 设置了 `LC_NUMERIC=en_US.UTF-8` 等（`main.cpp:693-698`），影响 printf 的 `'` 标志和 C 库的 `strtod`/`strftime`；RULEBOOK 没有规定。推荐：Rust 的 `main` 在同一位置做同样的四次 `setlocale`（`log::sys::setlocale_like_main`），`ob_printf!` 固定按 en_US.UTF-8 的 LC_NUMERIC 输出。备选：`ob_printf!` 运行时读 `localeconv()`（需要 unsafe，并在 Linux 缺少该 locale 时与 C++ 一致地退回）。
- **Q13 IR 节点里指针成员的打印**：C++ 的 `TO_STRING_KV` 遇到 `ObRawExpr *` 等指针成员会递归打印对象（§3.7.2 C 列），改成 id 之后 `to_string` 拿不到 `QueryArena`，只能打印 id。推荐：打印 id，1c 的清单确认没有用户可见的出口（目前已知的出口 `cmp_meta`、报错文本、虚拟表都不涉及 IR 节点）。备选：给这类类型另写带 `qa` 参数的打印函数。
- **Q14 跟踪宏的归属**：推荐 `ob_trace_event.h`、`ob_trace.h` 等保持 `translate`，宏由单元译者按 §2.5.9 的形状写。备选：整组改成 `replace:log`，由 1d 移植 `ObTraceEventRecorder`、yson 编码和 FLT。
- **Q15 `ObBaseLogWriter` 的归属**：它在 oblog 目录，唯一用户是 slog。推荐：本模块按 §2.5.10 提供（队列存 `Arc<dyn ObIBaseLogItem>`）。备选：交给 08 号与 slog 一起设计。

## 附录 A：模块表与前缀

**父模块**（`OB_LOG_ROOT`，`ob_log_module.h:48-75`）：0 CLIENT、1 CLOG、2 COMMON、3 OCCAM、4 LIB、5 OFS、6 RPC、7 RS、8 BOOTSTRAP、9 SERVER、10 SHARE、11 SQL、12 PL、13 JIT、14 STORAGE、15 STORAGETEST、16 LOGTOOL、17 WRS、18 EASY、19 DETECT、20 PALF、21 COORDINATOR、22 FLT、23 OBTRACE、24 MDS、25 MVCC。

**子模块**（`:77-216`，各组从 0 编号）：WRS{CLUSTER, SERVER}；LIB{ALLOC, CONT, FILE, HASH, LOCK, MYSQLC, STRING, TIME, UTIL, CHARSET, WS, OCI}；OFS{BLOCK, BLOCKSERVER, CLIENT, CLUSTER, COMMON, FILE, MASTER, STREAM, RPC, UTIL, FS, REGRESSION, LIBS, SHARE}；RPC{FRAME, OBRPC, OBMYSQL, TEST}；COMMON{CACHE, EXPR, LEASE, MYSQLP, PRI, STAT, UPSR}；SHARE{CONFIG, FILE, INNERT, INTERFACE, LOG, PT, SCHEMA, TRIGGER, LOCATION}；STORAGE{REDO, COMPACTION, BSST, MEMT, TRANS, RU, REPLAY, IMC, TABLELOCK, BLKMGR, FTS}；CLOG{CSR}；SQL{ENG, EXE, OPT, JO, PARSER, PC, RESV, REWRITE, SESSION, CG, MONITOR, DTL, DAS}；SERVER{OMT}；RS{COMPACTION}；EASY{IO}；STORAGETEST{TEST}；PL{RESV, CG, SPI, PACK, TYPE, CACHE, STORAGEROUTINE, DEPENDENCY}。共 26 + 85 = 111 个。

**已登记的名字**（`ob_log_module.ipp`，`parse_set` 只认这些）：父模块 CLIENT、CLOG、COMMON、LIB、OFS、RPC、RS、SERVER、SHARE、SQL、STORAGE、STORAGETEST、WRS、PALF、LOGTOOL、PL（16 个）；子模块 72 个：WRS 2 个全部；LIB 的 ALLOC…CHARSET（不含 WS、OCI）；OFS 14 个全部；RPC 4 个全部；COMMON 7 个全部；SHARE 9 个全部；STORAGE 除 RU 外 10 个；CLOG.CSR；SQL 除 DTL、DAS 外 11 个；SERVER.OMT；STORAGETEST.TEST；PL 只有 STORAGEROUTINE、CACHE。

**`ObLogMod` 的 99 个值**（`prefixes.py`；名字 → 行头的模块名）：BOOTSTRAP、CLIENT、CLOG、COMMON、COORDINATOR、DETECT、FLT、JIT、LIB、LOGTOOL、MDS、MVCC、OBTRACE、OCCAM、OFS、PALF、PL、RPC、RS、SERVER、SHARE、SQL、STORAGE、WRS（父模块，`[X] `）；COMMON_CACHE、COMMON_EXPR、COMMON_LEASE、COMMON_MYSQLP、COMMON_PRI、COMMON_STAT、COMMON_UPSR、CSR（CLOG.CSR）、IMC（STORAGE.IMC）、LIB_ALLOC、LIB_CHARSET、LIB_FILE、LIB_HASH、LIB_LOCK、LIB_MYSQLC、LIB_OCI、LIB_STRING、LIB_TIME、LIB_WS、OFS_BLOCK、OFS_BLOCKSERVER、OFS_CLIENT、OFS_CLUSTER、OFS_COMMON、OFS_FILE、OFS_FS、OFS_LIBS、OFS_MASTER、OFS_REGRESSION、OFS_RPC、OFS_SHARE、OFS_STREAM、OFS_UTIL、PL_CACHE、PL_DEPENDENCY、PL_STORAGEROUTINE、REPLAY（STORAGE.REPLAY）、RPC_FRAME、RPC_OBCALL（RPC.OBRPC）、RPC_OBMYSQL、RPC_TEST、RS_COMPACTION、RU（STORAGE.RU）、SERVER_OMT、SHARE_CONFIG、SHARE_FILE、SHARE_INNERT、SHARE_INTERFACE、SHARE_LOCATION、SHARE_LOG、SHARE_PT、SHARE_SCHEMA、SHARE_TRIGGER、SQL_CG、SQL_DAS、SQL_DTL、SQL_ENG、SQL_EXE、SQL_JO、SQL_MONITOR、SQL_OPT、SQL_PARSER、SQL_PC、SQL_RESV、SQL_REWRITE、SQL_SESSION、STORAGETEST（STORAGETEST.TEST）、STORAGE_BLKMGR、STORAGE_COMPACTION、STORAGE_FTS、STORAGE_REDO、TABLELOCK（STORAGE.TABLELOCK，`fmt_as_kv_`）、TRANS（STORAGE.TRANS）、WRS_CLUSTER、WRS_SERVER（子模块，`[P.S] `）；再加 `NONE`（`""`）。

C++ 另外定义了 44 个引用不存在的模块的宏（BLSST、IMPS、MEMT、MRSST、MYSQL、PS、STORAGE.TX、LIB 下的 CONTAIN/CPU/ENCRYPT/JASON/LIST/NUM/OBJP/PROB/PROFILE/QUEUE/REGEX/TASK/THREADL/UTILITY、SQL.PLANCACHE、DETECT.LOG），用了就编译不过，Rust 不提供。代码里用到的 46 个 `USING_LOG_PREFIX` 都在上面的 99 个之内（SQL_ENG 624、STORAGE 401、LIB 148、SQL_RESV 137…，`prefixes.py`）。`TABLELOCK` 前缀下的 `log_*_fmt!` 按 C++ 的错接打印原样的格式串、忽略参数（`ob_log_module.h:390`）；目前 19 个 TABLELOCK 文件里没有 printf 式日志。

