# Research 09: numbers and determinism

**Question (Step 1 task).** How the C++ handles ObNumber and its formatting, the dtoa port, float
and double printing, the four arithmetic matrices, `-ffp-contract=off` and `fma`, integer overflow
in expression evaluation, the hash functions of hash join, group by and partitioning, the sort
algorithms and their stability, and the judge build profile; and which rules the design document
should adopt for each.

**Sources.** The C++ in /Users/colin/seekdb-dev/migrate-to-rust/src, identical to 834bbee1e
(`git diff --stat 834bbee1e -- src` prints nothing); the reference build's
/Users/colin/seekdb-dev/ref-834bbee1e/build_release/compile_commands.json; the libc++ headers of
/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk, which the reference is built against;
rust/Cargo.toml; migration/decisions.md, PLAN.md, the feasibility report and its evidence. Counts
are `grep -rn` counts over src/ (`--include` of .cpp, .h, .ipp, .cc, .c) unless another command is
named. Nothing was built or run except a Python script over four .result files
(migration/design/evidence/09-numerics/matrix_lines.py, output beside it).
Facts about Rust and about C ABIs that were not checked on this machine say so.

## 1. Summary

| Topic | Proposed rule | Main reason |
|---|---|---|
| Number text | Translate ob_dtoa.cc, ObNumber (every version that has callers) and the decimal-int library line by line into one value-library crate; no direct Rust float formatting or parsing for any text that can reach output | 770,711 compared lines; MySQL's formats (dtoa mode 4 with a width, 6 digits for FLOAT, fast_float's grammar) have no Rust equivalent |
| Float evaluation | No `mul_add`: on arm64 the C++ calls no FMA; keep evaluation order and float/double types; libm through the `f64` methods, never `powi` | The reference is built with `-ffp-contract=off`; rustc never fuses |
| Float comparison | Port the C++ comparators literally: NaN sorts last, -0 equals 0, DOUBLE(M,D) compares with a tolerance | `f64` has no `Ord`; the tolerance comparator is not transitive |
| Integer overflow | `wrapping_*` wherever the C++ relies on wraparound; every C++ check kept with its error code and text; the C++ guards on `/` and `%` kept | Rust panics on overflow when checks are on, and on `/0` and `MIN / -1` always |
| Hashing | Bit-exact murmurhash64A, murmurhash2, fnv_hash2, per-type datum hashes and collation hashes; ObHashMap's bucket arithmetic ported; std `HashMap` banned where its order could reach output | KEY partitioning, NDV statistics, query-block names and hash-map iteration reach unmasked output |
| Sorting | A Rust translation of libc++ 20.1's `std::sort` and heap functions (SDK 26.2) for `lib::ob_sort`, `std::sort` and `std::*_heap`; ObAdaptiveQS and ObBinaryHeap translated line by line; `sort_unstable*` and `BinaryHeap` banned | Ties under ORDER BY are not masked; Rust's own sorts may panic on the non-transitive comparator |
| Build profiles | `release` for the product and performance runs; `judge` with release semantics (overflow checks off, debug assertions off, `panic = "abort"`); a non-gating `checked` profile with both on; kept C++ compiled with the reference's compiler and flags | Matches the reference's `-O2 -DNDEBUG`; finds silent overflows without turning them into judge failures |

## 2. What the C++ does today

### 2.1 What the four arithmetic matrices contain

The four .result files hold 770,711 lines: add 192,731 (tools/deploy/mysql_test/r/mysql/add.result),
minus 191,019 and div 198,622 (test_suite/datatype/r/mysql/), mul 188,339 (test_suite/expr/r/mysql/).
Each case creates a 16-column table (int, int zerofill, bigint, bigint unsigned, bigint zerofill,
decimal, decimal(35,15), decimal(35,7) zerofill, varchar, date, datetime(5), timestamp(5), year,
char, binary, varchar bin; add.test:6), inserts one value per row into one column, and selects
`(cX op Y)` for every column and about 20 literals. The div case tests the integer operator `DIV`,
not `/`, which is why its file has no fixed-point lines.

| Line class (matrix_lines.py) | Lines |
|---|---|
| `NULL` | 519,097 |
| Integer | 165,419 |
| Fixed point (`-?\d+\.\d+`) | 56,125 |
| Double in exponent form, such as `9.999999999999999e99` | 17,067 |
| `ERROR 22003` out-of-range messages | 365 |
| Statements and column headers | 12,484 |
| Other (EXPLAIN output in div and mul) | 154 |

The non-NULL value lines, by operand types: a string operand (a double result under MySQL's type
rules; for `DIV` an integer) 141,606; a decimal operand 63,606; integers only 32,196, of which 303
are errors; a temporal operand 1,519.

What this means for the port:
- Two thirds of the lines are NULL. The rest pin three formatters (integer, decimal, double) and
  the overflow messages, which print the operands: `BIGINT value is out of range in
  '(9223372036854775807 + 1)'`, built with `"'(%ld + %ld)'"` (src/sql/engine/expr/ob_expr_add.cpp:229-238).
- The DECIMAL columns are not ObNumber by default. `_enable_decimal_int_type` defaults to True
  (src/share/parameter/ob_parameter_seed.ipp:302-304), so DECIMAL columns and many decimal results
  use the decimal-int type: src/oblib/common/wide_integer, 4,242 lines (`wc -l`), with
  `unsigned __int128` as its double-width type (ob_wide_integer.h:48-50), sent to the client by
  `wide::to_string` (src/query/protocol/ob_mysql_protocol_util.cpp:562-567). Division can still turn
  decimal-int inputs into an ObNumber (ob_expr_div.cpp:66-138). The report and the plan name only
  ObNumber; the decimal-int library belongs next to it. Which matrix line takes which path is
  decided by the result-type code (ob_expr_add.cpp:55-130) and was not traced line by line.
- These files were recorded on Linux x86_64 CI and pass on the Mac reference built with
  `-ffp-contract=off`: 268 of 272 cases pass, and the 4 failures are quarantined
  (migration/judge/validation-834bbee1e.md). So for these inputs the C++ prints the same text on
  Linux x86_64, whose build uses libstdc++ (2.6), and on arm64 with libc++.
- The frozen tests already round some float output themselves: 327 `--replace_numeric_round` lines
  in 9 .test files, 85 of them added by f63482f57 ("fixed mac float number testcases"; `git show
  f63482f57 | grep -c '^+.*replace_numeric_round'`). Both builds see the same rounding; it is part
  of the tests, not a judge mask.

### 2.2 How numbers become text

| Value | Text-protocol path | File:line |
|---|---|---|
| Integers | `ObFastFormatInt`, zero-filled for ZEROFILL columns | ob_mysql_protocol_util.cpp:231-238 |
| FLOAT or DOUBLE with a declared scale | `ob_fcvt(value, scale, sizeof(tmp) - 1, ...)` | :281, :306 |
| FLOAT or DOUBLE otherwise | `ob_gcvt_opt(value, OB_GCVT_ARG_FLOAT or _DOUBLE, sizeof(tmp) - 1, ...)` | :283, :308 |
| ObNumber | `ObNumber::format`, which calls `format_v2` | :320-324; ob_number_v2.h:464-468 |
| Decimal int | `wide::to_string(value, int_bytes, scale, ...)` | :562-567 |
| Any number, binary protocol | raw bits handed to sql-nio | rust/sql-nio/src/response.rs:752-755 |

For text rows sql-nio only copies the bytes the C++ formatted (response.rs:734-737), so every
numeric formatter moves into Rust with the port.

**The dtoa port.** src/oblib/lib/charset/ob_dtoa.cc (2,440 lines, 56 `goto` lines) is MySQL's copy
of David Gay's dtoa and strtod.
- `ob_gcvt_strict` calls `dtoa` in mode 4 (the shortest digits that read back to the same double,
  at most `width` of them); for FLOAT it allows at most `FLT_DIG` = 6 digits (ob_dtoa.cc:291-295),
  so FLOAT text is not the shortest f32 text. The choice between fixed and exponent form depends on
  the decimal point and the width (:306-330).
- `width` is part of the output. Object printing passes the space left in the caller's buffer
  (`static_cast<int32_t>(length - pos)`, src/oblib/common/object/ob_obj_funcs.h:475-533); the
  protocol passes `sizeof(tmp) - 1`, with `FLOATING_POINT_BUFFER` = 311 + `NOT_FIXED_DEC` (31) for
  DOUBLE and 39 + `NOT_FIXED_DEC` for FLOAT (src/oblib/common/mysqlclient/ob_mysql_global.h:159-170).
- `ob_strtod` strips spaces at both ends and calls fast_float 6.1.3's `from_chars`
  (deps/init/oceanbase.macos.arm64.deps:16). A NaN or infinite result becomes 0 with nothing
  consumed. Any fast_float error falls back to Gay's `ob_strtod_int`, whose overflow result becomes
  ±DBL_MAX with an error (ob_dtoa.cc:344-405). fast_float 6.1.3 rejects a leading `+` by default
  (deps/3rd/usr/local/oceanbase/deps/devel/include/fast_float/ascii_number.h:265) and reports overflow and underflow as
  `result_out_of_range` (parse_number.h:257-260), so those inputs take the fallback.
- Two lines have the shape `a*b + c` that clang may fuse without the flag:
  `tens[k - 9] * dval(&rv) + z` in strtod (:1299) and `ieps*dval(&u) + 7.` in dtoa's fast path (:2074).
- Callers: `ob_gcvt`, `ob_gcvt_opt` or `ob_gcvt_strict` in 17 files and `ob_fcvt` in 8 files
  outside ob_dtoa.

**ObNumber.** ob_number_v2.cpp (6,499 lines) and ob_number_v2.h (3,604) hold a 32-bit descriptor
(`len_`, `reserved_`, `flag_`, and `se_` made of `exp_:7` and `sign_:1`; src/oblib/lib/ob_define.h:1786-1837)
and base-10^9 `uint32_t` digits (ob_number_v2.h:143-166).
- Arithmetic is integer only. sqrt, ln, exp, power and the trigonometric functions are computed in
  decimal (ob_number_v2.cpp:2917-5429); the one double input is `get_npi_(double n)` with n = -0.5,
  0.5 or 1.5, turned into text by `ob_gcvt_opt` (:2786-2798). The file reads no SQL mode or config
  (grep for `sql_mode`, `is_oracle_mode`, `GCONF`: 0 hits).
- Two versions of each operation are live. The SQL operators call `add_v3`, `sub_v3`, `mul_v3`,
  `div_v3` and `rem_v3` (ob_expr_add.cpp:357, 791, 852; ob_expr_minus.cpp:354; ob_expr_mul.cpp:330;
  ob_expr_div.cpp:331, 468; ob_expr_mod.cpp:190, 361). `add`, `sub`, `mul`, `div` and `rem` dispatch
  to the `_v2_` functions (ob_number_v2.h:1616-1740) and are called by window functions
  (ob_window_function_op.cpp:857, 2579), integer `DIV` (ob_expr_int_div.cpp:339) and date
  functions (ob_expr_unix_timestamp.cpp:190).
- Decimal to double goes through text: `ObNumber::format()` and then `ob_strtod`
  (ob_datum_cast.cpp:3708-3727).

**printf float formats.** 168 lines outside logging use `%e`, `%f` or `%g` (grep, LOG macros
excluded). Among them: the double overflow messages `"'(%e + %e)'"` (ob_expr_add.cpp:318; likewise
in minus, mul and div), `FORMAT_BYTES` and `FORMAT_PICO_TIME` output (`"%4.2f %s"`, `"%4.2e %s"`;
ob_expr_format_bytes.cpp:130-132, ob_expr_format_pico_time.cpp:133-135), and SAMPLE percentages
printed into SQL text with `%lf` (src/sql/printer/ob_dml_stmt_printer.cpp:1193-1201).

**Formatting reaches hashes too.** A DOUBLE with a declared scale is hashed as the text `ob_fcvt`
makes of it (src/share/datum/ob_datum_funcs_impl.h:1240-1256; ob_obj_funcs.h:395-398).

### 2.3 Floating point: contraction, fma and libm

- The reference's compile line (compile_commands.json, entry for ob_expr_add.cpp) is
  `clang++ 17.0.6 ... -O2 -g -DNDEBUG ... -ffp-contract=off -fno-strict-aliasing -fno-omit-frame-pointer
  -march=armv8-a+crc+lse -mtune=generic`; all 364 entries carry the flag
  (migration/judge/reference.md:18). cmake has no `-ffast-math`, `-fwrapv`, `-ftrapv` or
  sanitizer (grep of CMakeLists.txt and cmake/); src has no floating-point pragma, `fesetround` or
  flush-to-zero call (grep: 0 hits).
- No explicit FMA runs on arm64. src has no `fma(`, `std::fma`, `__builtin_fma`, `vfma*` or `vmla*`.
  The only fused calls are x86 intrinsics (`_mm256_fmadd_ps`, `_mm512_fmadd_ps`) on 16 lines of
  src/data_plane/api/data_plane/vector/ob_vector_l2_distance.h (lines 225-1096), and the targets
  built with `-mfma` are x86 only (src/sql/CMakeLists.txt:12-15, src/storage/CMakeLists.txt:20-23).
  On arm64 the L2 and inner-product kernels use separate `vmulq_f32` and `vaddq_f32` and end with
  `vaddvq_f32` (ob_vector_l2_distance.h:1102-1183; ob_vector_ip_distance.h:433-499; chosen at
  src/storage/vector_type/ob_vector_l2_distance.cpp:37-39 and ob_vector_ip_distance.cpp:35-37).
- 00b found the 40 plan-bearing cases identical with and without the flag (PLAN §8, item 3).
- About 270 lines call pow, exp, log, sqrt, the trigonometric functions, round, ceil, floor or fmod
  outside ObNumber; 21 transcendental calls and 22 `ceil` calls are in src/sql/optimizer, and 21
  lines are in the share/geo island. The casts use `rint`, which rounds halves to even
  (`common_double_int`, src/sql/engine/expr/ob_datum_cast.cpp:2225-2248). `long double` appears only
  in compile-time literal operators (src/oblib/lib/literals/ob_literals.h:29-120).
- Comparison. NaN sorts after every number and equals NaN; -0.0 equals 0.0 (`real_value_cmp`,
  src/share/datum/ob_datum_cmp_func_def.h:111-130). A DOUBLE with a declared scale compares with a
  tolerance, `fabs(l - r) < 5 / 10^(scale+1)` (`ObFixedDoubleCmp`, :139-171; used when sorting
  through `ObNullSafeFixedDoubleCmp`, ob_datum_funcs_impl.h:140-158; the same constants in
  ob_expr_cmp_func.cpp:74-86). That comparator is not transitive. `_enable_convert_real_to_decimal`
  defaults to False (ob_parameter_seed.ipp:299), so DOUBLE(M,D) columns keep it. Hashes turn -0.0
  into 0.0 and every NaN into `NAN` (ob_obj_funcs.h:405-418).
- Float to integer. The cast code checks the range before `static_cast<int64_t>(rint(in))`, with a
  comment that a double equal to `LLONG_MAX` may convert to `LLONG_MIN` or `LLONG_MAX`
  (ob_datum_cast.cpp:2231-2245). Unguarded casts get arm64's saturating conversion on this Mac and
  `INT64_MIN` on x86_64 (hardware behavior, not measured here).

### 2.4 Integer overflow

- The reference has no `-fwrapv`, so signed overflow is undefined behavior; clang at -O2 gives
  wraparound in practice, without a guarantee.
- The arithmetic operators add first and then test the sign bits of the result:
  `res.set_int(left_i + right_i)`, then `is_int_int_out_of_range` (ob_expr_add.cpp:229-238; the test
  at ob_expr_add.h:60-71). This relies on a signed add wrapping. Other checks use
  `__builtin_add_overflow` and `__builtin_mul_overflow` (8 lines in 5 files, such as
  ob_expr_add.h:56-58 and ob_expr_mul.h:182-200).
- 273 lines use `OB_OPERATE_OVERFLOW` (195 of them in src/sql/engine/expr) and 122 use
  `OB_DATA_OUT_OF_RANGE`. The matrices pin 365 of these messages.
- Division is guarded: `DIV` by zero and `INT64_MIN DIV -1` (ob_expr_int_div.cpp:102-105, 211-212);
  `INT64_MIN % -1` returns 0 (ob_expr_mod.cpp:110-111, 217-218, 404-405).
- Some code relies on wraparound with no check:
  - KEY partitioning takes `result_num < 0 ? -result_num : result_num` of a 64-bit hash
    (src/sql/engine/expr/ob_expr_func_partition_key.cpp:80-81, 120-121); at `INT64_MIN` the value
    stays negative.
  - `fnv_hash2` multiplies and shifts a signed `int32_t` and XORs in `char` values
    (src/oblib/lib/hash_func/murmur_hash.cpp:51-66).
  - The MySQL string hash updates `unsigned long` state (src/oblib/lib/charset/ob_ctype_utf8.cc:811-815),
    and `ObCharset::hash` passes its state as `ulong` (ob_charset.cpp:719-724). `unsigned long` is
    64 bits on macOS, Linux and Android and 32 bits on Windows and wasm32 (C data models, not
    checked here).
- `char` is signed on macOS arm64 and x86_64 and unsigned on Linux aarch64 and Android (ABI facts,
  not checked here), so C++ builds disagree on `fnv_hash2` for bytes of 0x80 and above. Query-block
  names only feed it ASCII.

### 2.5 Hash functions, and which values reach compared output

| Use | Function and inputs | Where | Reaches compared output |
|---|---|---|---|
| Hash join keys | per-key `murmur_hash_v2_`, chained from `HASH_SEED` = 16777213; a NULL key gets `murmurhash64A` of a counter; masked to 63 bits; bucket `hash & (bucket_cnt - 1)`; spill partition `(hash >> part_shift) & (part_count - 1)` | ob_static_engine_cg.cpp:4046-4047; ob_hash_join_op.cpp:4002-4035, 4049-4077; ob_hash_join_op.h:673, 964, 1123; ob_hash_join_basic.h:111-113 | through spill partitioning and the bucket layout (not traced further); such SELECTs are on the masked list |
| Hash group by | `murmur_hash_v2_`, open addressing with linear probing | ob_hash_groupby_op.cpp:713-714; ob_exec_hash_struct.h:143-147 | groups come out in first-seen order (`curr_group_id_` over `local_group_rows_`, ob_hash_groupby_op.cpp:478-552) unless spilled |
| Hash distinct, hash set operations, PX hash distribution, join filters | `set_murmur_hash_func`, which picks `murmur_hash_v2_` | ob_static_engine_cg.cpp:884-888, 891-954, 1074-1118, 2458-2522, 2882-2926, 3012-3022 | through PX row order and the masked list |
| HASH partitioning (MySQL mode) | no hash: `abs(value)` with `INT64_MIN` turned into `INT64_MAX`, then `val % part_num` | ob_expr_func_part_hash.cpp:38-78; src/share/schema/ob_schema_struct.cpp:5220-5233 | row placement, so scan order (unmasked) |
| KEY partitioning | `murmur_hash_` chained from seed 0, then the abs above | ob_expr_func_partition_key.cpp:66-82, 91-122 | row placement, so scan order (unmasked) |
| NDV statistics | `murmur_hash_` from seed 0 into a 1,024-bucket HyperLogLog, estimated with `log`; gathering stores the bitmap (src/sql/optimizer/stat/ob_stat_item.cpp:191-200) | ob_aggregate_processor.cpp:5689-5697, 5750-5767; ob_expr_estimate_ndv.cpp:75-125 | yes: `num_distinct` 97 where the next partition shows 100 (test_suite/histogram/r/mysql/dbms_stats_delete_stats.result:71-72) |
| Query-block names | `fnv_hash2`, printed with `%08X` | ob_sql_hint.cpp:79-90, 389 | yes, plan text |
| OB hash maps | integer keys hash to themselves; `ObString` keys to `murmurhash(ptr, len, 0)`; bucket `hash % bucket_num` with a prime from `cal_next_prime` | src/oblib/lib/hash/ob_hashutils.h:677, 749-853; ob_hashtable.h:1139 | where iterated into output: ob_join_order.cpp:16662 and 16727 (fulltext tokens; `ObFTWord` hashes with `default_hash_`, src/storage/fts/ob_fts_struct.cpp:28-40), ob_range_generator.cpp:1799, ob_key_part.cpp:289 and 305 |
| Per-type datum hash families | `default_hash_` (MySQL string hash, murmur for other types), `murmur_hash_`, `murmur_hash_v2_`, `xx_hash_`, `wy_hash_` | ob_datum_funcs_impl.h:880-895 | `xx_hash_` and `wy_hash_` have no callers outside this registration (grep) |
| Strings in all of the above | the collation's `hash_sort`, second state word 0xc6a4a7935bd1e995, `murmurhash64A` over collation weights in chunks | ob_charset.cpp:701-730; ob_ctype_utf8.cc:818-860 | through the rows above |
| ObNumber in all of the above | the `se_` byte, then the digits | ob_obj_funcs.h:820-829 | through the rows above |
| XXH64 (xxHash 0.6.2) | storage encoding hash tables, palf log entry headers | blocksstable/encoding/ob_encoding_hash_util.*, logservice/palf/log_entry_header.cpp | no |
| `ob_crc64` (a CRC32C register with no inversion, evidence-full.md:1660) | storage checksums | src/oblib/lib/checksum/ob_crc64.cpp | no: no configured .test file mentions a checksum (grep: 0) |

`std::unordered_map` and `std::unordered_set` have 0 uses in src. OB hash containers are declared on
836 lines; 57 lines declare an explicit iterator over one and 53 call `foreach_refactored`, not
counting iterations written with `auto`.

### 2.6 Sorting and heaps

| Call | Count | Algorithm | Stable |
|---|---|---|---|
| `lib::ob_sort` | 169 call lines, 67 of them in src/sql/{optimizer,rewrite,resolver,engine,das} | `std::sort` after an optional tracepoint check (src/oblib/lib/utility/ob_sort.h:27-41) | no |
| `std::sort` called directly | 3 | the same | no |
| `std::stable_sort` | 4: JSON object keys (ob_json_tree.cpp:743), XML (ob_tree_base.cpp:634, 642; ob_binary_aggregate.cpp:384) | libc++ | yes |
| `std::make_heap`, `push_heap`, `pop_heap` | 10: PX merge-sort receive (src/sql/engine/px/exchange/ob_row_heap.h:248-267), hash group by (ob_hash_groupby_op.cpp:2616-2623), plan cache, KV cache | libc++ | not applicable |
| `qsort` | 2: log file names (ob_log.cpp:1361), charset setup (ob_ctype_simple.cc:1131) | libSystem | no |
| SORT operator | encoded keys: `ObAdaptiveQS`, a three-way quicksort plus radix steps (ob_sort_op_impl.cpp:31-360); otherwise `lib::ob_sort` (:1848-1866); top-N uses `ObBinaryHeap` (ob_sort_op_impl.h:624; src/oblib/lib/container/ob_heap.h) | OB's own | no |

- `std::sort` with a comparator is inlined from the SDK's headers: `_LIBCPP_VERSION` 200100
  (SDK usr/include/c++/v1/__config:31). Its introsort uses sorting networks up to 5 elements,
  insertion sort below 24, a ninther above 128 and heap sort after 2 * log2(n) levels
  (__algorithm/sort.h:726-889). Only `std::sort` of arithmetic types with the default comparator
  calls the copy inside the runtime libc++ (the `extern template` lines, sort.h:853-877), where
  ties cannot be told apart.
- The hardening mode defaults to none (`_LIBCPP_HARDENING_MODE_DEFAULT 2` in __config_site:43;
  none is `1 << 1`, __config:127), so the reference checks no comparator and no index.
- libc++'s `pop_heap` uses `__floyd_sift_down` (pop_heap.h:47), which moves to the right child only
  when the left child is strictly less (sift_down.h:98). Rust's `BinaryHeap` moves right when
  left <= right (Rust standard library source, not checked here), so tie order differs.
- Linux C++ builds use GCC 9's libstdc++ (`--gcc-toolchain`, cmake/Env.cmake:368-370;
  `-D_GLIBCXX_USE_CXX11_ABI=1`, :148; no `-stdlib` flag in cmake/Env.cmake). The Linux-recorded
  .result files pass on the Mac with libc++ (2.1), so the passing cases do not show the places
  where the two libraries' `std::sort` differ. That bounds the risk; it does not make a third
  algorithm safe.
- 31 comparators keep an error in `ret_` (`int &ret = ret_;`, 31 lines in 18 files). After an error
  some compare pointers (ob_sort_op_impl.cpp:401-408, with a comment about an Apple libc++ check)
  and two return false for every pair (ob_sort_op_impl.cpp:440-445; ob_slice_calc.cpp:1046-1052).
  The SORT operator reads `comp_.ret_` right after sorting (ob_sort_op_impl.cpp:1867-1868).
  evidence-full.md:274 records an earlier out-of-bounds read caused by this pattern, and commit
  0dca2a5d3 replaced a -NaN rank that broke `std::sort` on macOS (evidence-full.md:406).

### 2.7 Assertions and the reference's build settings

- Under `-DNDEBUG`, `OB_ASSERT(x)` becomes `(void)(x)` and still evaluates `x` (549 lines;
  src/oblib/lib/utility/ob_macro_utils.h:707-719); `assert()` is off (about 440 lines, 432 of them in
  src/oblib/lib); `OB_ASSERT_MSG` logs and then calls the disabled `assert` (23 lines).
- Always on: `abort_unless` (251 lines; src/oblib/lib/ob_abort.h:35-42), `ob_assert`, which is
  `ob_release_assert` (7 lines; ob_macro_utils.h:753-764), and direct `ob_abort()` calls (136 lines).
  112 lines test `NDEBUG` directly.
- The Rust workspace today has `[profile.release]` with opt-level 3, thin LTO, one codegen unit,
  debug symbols and `panic = "abort"`, and `[profile.cmake-debug]`, which inherits `dev` with
  `panic = "abort"` (rust/Cargo.toml:9-23).

## 3. Constraints that bind this topic

- **Decision 6 (b):** exact comparison is the main mode; the only masks are EST.ROWS and EST.TIME in
  the 40 plan-bearing files and row order for the ~300 hash-order SELECTs. Ties under ORDER BY,
  scan order, number text, error text, plan text with its `%08X` names, and statistics values
  must all match.
- **Decision 3, row 3a:** the reference is 834bbee1e built by clang 17.0.6 against SDK 26.2
  (libc++ 200100) with `-ffp-contract=off`; the judge compares Rust with that build on this Mac.
- **Decision 7:** macOS arm64 is the only gate; nothing may rule out wasm, Android, Linux or Windows.
- **Decision 11:** persisted formats are free. ObNumber bytes, OB_UNIS and CRC32C get differential
  tests in the core build instead of judge comparison (PLAN §3).
- **Decision 13:** the parser's C core, vsag, S2, share/geo with boost.geometry and ICU regex stay
  C++, so the Rust build compiles and links them.
- **Decision 14:** `unsafe` only in named crates; the SIMD kernels are one of them.
- **Decision 16:** stable 1.98.1 and no nightly features. What the rules below need is stable:
  `f64::round_ties_even` (since 1.77) and the aarch64 NEON intrinsics.
- **Row 1a:** queries at most 1.2x slower than the C++, so the sort and formatting translations
  must not be slow.
- **Row 5a:** speed over tokens. The report prices each false failure at 1-3M tokens of triage
  (PLAN §8, item 23), so rules that prevent false failures are cheap by comparison.
- **PLAN:** §3 "Where a faithful translation still changes behavior silently", items 1-4 (sorting,
  hashing, floating point, integer overflow); §6 Step 1, which asks the design document for "sort
  and hash determinism, FMA (`mul_add` only where the C++ calls `fma`), overflow, and the judge
  build profile"; §8, items 18, 21, 23 and 29.

## 4. Options for the Rust design

### 4.1 Number text and number parsing

| Option | Cost | Risk |
|---|---|---|
| (a) Translate ob_dtoa.cc, ObNumber and the decimal-int library line by line into one value-library crate, and test them against the C++ | about 16.8K lines of leaf code; the 56 `goto` lines become labeled blocks | low: a differential test settles every function |
| (b) Use Rust's formatting and parsing (`Display`, `{:e}`, ryu, `str::parse`) with adapters | smaller | high: dtoa mode 4 with a width, 6-digit FLOAT, the fixed/exponent choice and fast_float's accepted grammar have no Rust equivalent; each mismatch is a triage item across 770K lines |
| (c) Keep ob_dtoa.cc as a C++ island | FFI and `unsafe` for pure leaf code; a new island is the developer's call (Decision 13) | low for output, but more C++ in the product for no gain |

**Recommendation: (a).** Parsing keeps `ob_strtod`'s two steps. The first step reproduces the span
fast_float's `from_chars` accepts and computes the value with Rust's `f64::from_str` over that span:
both are correctly rounded, so the values agree (to be confirmed by the differential test). The
second step is the translated `ob_strtod_int`.

### 4.2 Floating-point evaluation

The plan's rule, `mul_add` only where the C++ calls `fma`, holds, and the evidence narrows it: on
arm64 the C++ calls no FMA, so the first release has no `mul_add` at all, and the x86 AVX kernels
matter only when Linux x86_64 comes back. The other two options do not work: allowing `mul_add`
for speed changes last bits, and building the reference without the flag would ask Rust to fuse
exactly the expressions clang fuses, which rustc cannot do.

Four more points follow from 2.3:
- **libm.** The `f64` methods (`powf`, `exp`, `ln`, `log10`, `sin`, ...) call the same libSystem
  functions as the C++ on macOS. This is an assumption from how rustc lowers them, and the Rust
  documentation does not promise these results are deterministic (compile-time folding may differ);
  family 5's expression generator checks it. `powi` uses a different algorithm and never replaces
  `pow`.
- **Comparators** are ported as written; `total_cmp`, `partial_cmp().unwrap()`, `f64::max` and
  `f64::min` have different NaN or -0.0 behavior.
- **Types:** float stays `f32` wherever the C++ uses `float`.
- **Kept C++ numbers.** The islands must be compiled with the reference's compiler, SDK and flags,
  or boost.geometry's numbers can move; the 42 geometry tests pin them (PLAN §3).

### 4.3 Integer overflow

| Option | Behavior | Cost and risk |
|---|---|---|
| (a) The plan's text: the judge profile keeps overflow checks off; operators translated as written | wraps like the C++ does in practice | an overflow the translation introduces stays silent unless it changes output |
| (b) Overflow checks on in the judge profile | a panic aborts the server | the C++ wraps on purpose in hashes and where the operators add first and test the sign bits after; each such site aborts judge runs until rewritten: false failures |
| (c) (a), plus explicit arithmetic wherever the C++ relies on wraparound or checks, plus a non-gating `checked` profile | the judge matches the reference; diagnostics find silent overflows | inventory rows for 273 + 122 check sites and for the wraparound sites |

**Recommendation: (c).** The plan does not mention one fact: Rust's `/` and `%` panic on a zero
divisor and on `MIN / -1` in every profile (the Rust reference says these checks happen even with
`-C overflow-checks` off; not checked here). So the C++ guards are kept as written. Divisions the
C++ leaves unguarded stay plain `/`: on x86_64, where the .result files were recorded, those
inputs trap, so the recorded paths do not reach them. On arm64 the C++ would return 0 instead
(hardware behavior, not measured).

### 4.4 Hashing

| Option | Cost | Risk |
|---|---|---|
| (a) Bit-exact translations of every hash function and of ObHashMap's bucket arithmetic | small: murmur_hash.{h,cpp} are 146 lines; the per-type code lives in the datum library and the containers in the core, which are written anyway | none for output |
| (b) Exact only where output is known to depend (KEY partitioning, NDV, query-block names, the listed iterations) | slightly smaller | the list comes from a sweep; each miss costs triage |
| (c) Rust hashers (SipHash, ahash, FxHash) | smallest | moves rows between KEY partitions (unmasked scan order), changes NDV values in histogram tests and the names in plans |

**Recommendation: (a).** Exact hashes also keep the row order of most of the ~300 masked SELECTs,
provided the Rust tables keep their layouts and hash group by keeps first-seen group order. The
row-order mask then serves as a safety net; Decision 6 runs the masked classes exactly once more
at the final gate anyway.

### 4.5 Sorting

| Option | Cost | Risk |
|---|---|---|
| (a) Rust's `sort_unstable_by`, `sort_by` and `BinaryHeap` | none | tie order differs from libc++, both for ORDER BY ties and for plan choices among equal costs. Since 1.81 Rust's sorts may panic when the comparator is not a total order (release notes, not checked here), and DOUBLE(M,D)'s tolerance comparator is not transitive, so the server could abort |
| (b) Translate libc++ 20.1's `std::sort` (the path for comparators that are not the default, without the branchless partition) and its heap functions; translate OB's own algorithms as written | a few hundred lines from sort.h (974 lines including parts not needed) and the heap headers | pinned to SDK 26.2; another SDK needs a header comparison |
| (c) Add tie-breakers to every comparator | edits to many comparators | the Rust order still differs from the C++ reference, so false failures; it also changes behavior |

**Recommendation: (b).** `std::stable_sort` becomes `sort_by`, because a stable sort's output is fixed
by the comparator alone when the comparator is a strict weak ordering: `ObJsonKeyCompare` returns
0 or 1 from a length-then-bytes or bytes-only order (ob_json_tree.h:869-886); the XML comparators
are still to be checked. Comparators that can fail return the error, and the sort stops at the
first one. The C++ never uses the order after an error (it returns `comp_.ret_` right after the
sort), so stopping early cannot be seen from outside.

### 4.6 Build profiles

The judge profile may relax LTO and codegen units without touching numbers: Rust's integer and
float results depend on `overflow-checks` and `debug-assertions`, not on opt-level, LTO or codegen
units (language semantics, not checked here). Two exceptions are the libm caveat in 4.2 and the
sign and payload bits of a NaN result, which Rust leaves unspecified; the C++ normalizes NaN before
hashing, and MySQL mode turns most NaN results into errors or NULL, so neither is expected to reach
output (assumption). Opt-level stays 3 so that timing-sensitive cases (PLAN §8, item 21) see a speed
close to release.

Prior art from the earlier sql-nio port, adopted:
- `panic = "abort"` in every profile, so a panic never unwinds into C++ (rust/Cargo.toml:14-16).
- The note that debug assertions also switch on std's own checks of `unsafe` preconditions since
  Rust 1.78 (/Users/colin/obsidian/tech/seekdb/migrate to rust/notes/ffi-mechanics.md:137). The
  `checked` profile gets these for the named `unsafe` crates. This is a fact about Rust, so it
  needed no re-check against 834bbee1e.

The storage notes (/Users/colin/obsidian/tech/seekdb/storage/模块边界与依赖.md:210, written against
073e9b2f1) require bit-exact checksums, but that was for reading old data files under the
discarded plan. Decision 11 drops that need, and no configured test prints a checksum.

## 5. Proposed rules for the design document

1. **Value libraries.** ob_dtoa.cc (`dtoa`, `ob_gcvt*`, `ob_fcvt`, `ob_strtod`, `ob_strtod_int`), ObNumber,
   the decimal-int library and murmur_hash.{h,cpp} are translated line by line into one crate.
   Text that can reach a client, a result, a plan, an error message or a hash never comes
   directly from Rust's float formatting (`Display`, `Debug`, `{:e}`, `{:.N}`) or from a
   formatting crate, and doubles are never parsed with `str::parse::<f64>` outside the translated
   `ob_strtod`. The only use of Rust's formatting is inside the printf helper of rule 4.
2. **ObNumber versions.** Every ObNumber function with a caller is kept, the `_v2_` and `_v3` families
   alike, and every caller keeps the version it calls today; `format` keeps calling `format_v2`.
   The descriptor is a `u32` whose accessors reproduce the little-endian layout: `len_` in byte 0,
   `flag_` in byte 2, `se_` in byte 3 with `exp_` in bits 0-6 and `sign_` in bit 7. (That bit order
   is clang's on a little-endian target; the differential test confirms it.)
3. **Widths.** The `width` and `precision` arguments of `ob_gcvt` and `ob_fcvt` are part of the
   output. Callers keep the C++ buffer sizes (`FLOATING_POINT_BUFFER`, `NOT_FIXED_DEC`, the 39 +
   `NOT_FIXED_DEC` FLOAT buffer) and pass the same remaining-space widths the C++ passes.
4. **printf floats.** A C printf float conversion that reaches output (`%e`, `%f`, `%lf`, `%4.2e`, ...)
   goes through one helper that reproduces C99 printf text: exponent written as `e+NN`, width and
   padding. The digits come from Rust's exact-precision formatting (assumption: they equal libc's;
   the helper's differential test against `snprintf` settles it).
5. **Parsing.** String to double keeps `ob_strtod`'s two steps as described in 4.1.
6. **No FMA.** No `mul_add`, and no FMA intrinsic outside a translation of a C++ FMA call; on arm64
   there is none. Every float expression keeps the C++ order, grouping and precision. The NEON
   kernels are translated with `core::arch::aarch64` intrinsics in the same order, inside the SIMD
   kernels crate.
7. **libm.** `pow`, `exp`, `log`, `log10`, `log2`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`,
   `atan2`, `sqrt` and `fmod` map to `powf`, `exp`, `ln`, `log10`, `log2`, `sin`, `cos`, `tan`, `asin`,
   `acos`, `atan`, `atan2`, `sqrt` and `%`. `rint` maps to `round_ties_even`, `round` to `round`,
   `lround` to `round` followed by `as i64`, and `trunc`, `floor`, `ceil` and `fabs` to `trunc`,
   `floor`, `ceil` and `abs`. `std::max` and
   `std::min` on floats become `if a < b { b } else { a }` (and the mirror); only `fmax` and `fmin`
   map to `f64::max` and `f64::min`. `powi`, `total_cmp`, `clamp` and `signum` are not used where
   the C++ does something else.
8. **Float comparisons and hashes** are translated literally: NaN after every number, NaN equal to
   NaN, -0.0 equal to 0.0, the DOUBLE(M,D) tolerance table, and the -0.0 and NaN normalization
   before hashing.
9. **Float to integer.** Casts use `as`, which saturates and maps NaN to 0, the same as arm64's
   conversion instructions. Guarded C++ paths (`common_double_int` with `rint`) are translated as
   written.
10. **Wraparound and overflow checks.** Arithmetic where the C++ relies on wraparound is written
    with `wrapping_*`: hash mixing, the operators that add first and test the sign bits after, the
    absolute value of a KEY partition hash, `fnv_hash2`. Where the C++ checks (the `__builtin_*_overflow` calls, the
    `is_*_out_of_range` tests), Rust uses `overflowing_*` or `checked_*` or keeps the same test on a
    wrapped result, and keeps `OB_OPERATE_OVERFLOW` or `OB_DATA_OUT_OF_RANGE` with the exact message
    text. Plain operators remain only where overflow cannot happen.
11. **Division.** Every C++ guard before `/` and `%` is kept, because Rust panics on zero and on
    `MIN / -1` in all profiles.
12. **Implicit C++ conversions** are written out. A mixed signed/unsigned comparison or expression
    gets the conversion C++'s usual arithmetic conversions make, not the one that looks natural:
    the signed operand becomes unsigned when the unsigned type is at least as wide (`int64_t`
    against `uint64_t`), and the unsigned one becomes signed only when the signed type is wider.
    A C `char` read as a number is `i8`, as on macOS arm64. `long` and `unsigned long`, including the `ulong`
    hash state, are `i64` and `u64` on every target, never `isize`, `usize` or `c_long`.
13. **Hash functions are bit-exact:**
    - `murmurhash64A` with explicit little-endian loads, `murmurhash2`, `fnv_hash2`, and XXH64 from
      a crate (assumption: XXH64's output has not changed since xxHash 0.6.2);
    - the per-type datum hashes (`default_hash_`, `murmur_hash_`, `murmur_hash_v2_`), with the same
      input bytes, seeds and NULL handling;
    - the collation `hash_sort` functions with `u64` state;
    - the seeds (16777213 for hash join, 0 for KEY partitioning and for NDV) and the hash join's
      NULL-key counter.

    `wyhash` has no callers and is dropped.
14. **Hash containers.** ObHashMap and ObHashSet are translated with their bucket arithmetic (prime
    bucket counts from `cal_next_prime`, `hash % bucket_num`, chain order) and their key hashes. The
    std `HashMap` and `HashSet` are allowed only with a fixed hasher and only where the order cannot
    reach output. Enforcement: clippy's `disallowed_types` lists `std::collections::HashMap`,
    `HashSet` and `RandomState`, and each `#[allow]` carries an inventory row.
15. **Sorting.** A Rust translation of libc++ 200100's `std::sort` (without the branchless path) and
    of `make_heap`, `push_heap`, `pop_heap` and `sort_heap`, with checked indexing, replaces
    `lib::ob_sort`, `std::sort` with a comparator and the `std::*_heap` calls. `std::stable_sort`
    becomes `sort_by`. ObAdaptiveQS, its radix steps and ObBinaryHeap are translated as written.
    clippy's `disallowed_methods` and `disallowed_types` ban `sort_unstable*`,
    `select_nth_unstable*` and `BinaryHeap`.
16. **Comparators that can fail** return a `Result`; the translated sort stops at the first error
    and returns it.
17. **Seeded values.** `RAND(seed)` keeps MySQL's generator (ob_expr_rand.cpp:28, 39-49; the tests
    t/func_group_1 and t/func_group_7 call `rand(10)` and `RAND(0)`). NDV estimation keeps
    `LLC_BUCKET_BITS` = 10 and the estimate formula (ob_aggregate_processor.h:1250-1251;
    ob_expr_estimate_ndv.cpp:75-125).
18. **Assertions:**
    - `OB_ASSERT(x)` evaluates `x`, keeping any side effect, and checks it with `debug_assert!`;
    - `assert()` becomes `debug_assert!`;
    - `abort_unless` and `ob_assert` become `assert!`;
    - `OB_ASSERT_MSG` logs, then `debug_assert!`;
    - `#ifdef NDEBUG` becomes `#[cfg(not(debug_assertions))]`.
19. **Profiles:**
    - `release` (product, performance family 14): today's settings plus `overflow-checks = false`
      and `debug-assertions = false` written out.
    - `judge` (every C++-against-Rust parity run): inherits `release`; overflow checks and debug
      assertions off, `panic = "abort"`, opt-level 3; LTO and codegen units may be relaxed for
      rebuild time.
    - `checked` (diagnostic, not a gate): inherits `judge`, with overflow checks and debug
      assertions on. It runs the 272 cases and family 5's generator weekly, from the first Rust
      build that serves queries (Step 5) to the end of Step 6. Each panic becomes an inventory row:
      the C++ relies on wraparound, or the C++ checks, or a bug.
    - Unit and differential tests of the value-library crate use the default test profile (checks on).
20. **Kept C++.** The islands and the parser's C core are compiled with the reference's compiler
    (clang 17.0.6), SDK (26.2) and flags (`-O2 -g -DNDEBUG -ffp-contract=off -fno-strict-aliasing
    -fno-omit-frame-pointer -march=armv8-a+crc+lse -mtune=generic`), and they link the same prebuilt
    archives (S2, vsag, ICU) as the reference.
21. **Pinning.** The sort translation records the libc++ version it follows (200100). If the
    reference is ever rebuilt against another SDK, compare __algorithm/sort.h and sift_down.h
    before the next family 4 run (PLAN §8, item 29).
22. **Later platforms.** These rules give every platform the numeric behavior of the macOS arm64
    reference: libc++ tie order, signed `char`, 64-bit `long`, saturating float to integer. A later
    Linux, Windows, Android or wasm gate compares against that, not against the C++ built for the
    same platform, which differs (libstdc++ on Linux, unsigned `char` on Linux aarch64 and Android,
    32-bit `long` on Windows and wasm32). On wasm, Rust's `f64` libm functions come from another
    implementation, so last bits may differ (assumption).

## 6. Sweeps and tests these rules need

- **Inventory rows** (prompt 02's gap list already names "sort and hash order, pointer identity,
  integer overflow and float contraction"):
  - every `OB_OPERATE_OVERFLOW` and `OB_DATA_OUT_OF_RANGE` site (273 and 122 lines) and every
    wraparound site: which Rust arithmetic;
  - every hash-container iteration (57 + 53 lines plus `auto` iterations): can its order reach
    output;
  - the 31 comparators with `ret_`;
  - every comparator over floats;
  - the 168 non-log float printf lines: helper or log only;
  - the about 270 libm lines: the mapping of rule 7.
  - A clang `-Wsign-compare -Wsign-conversion` pass over compile_commands.json can list mixed
    signed/unsigned sites for rule 12, when a build is allowed again.
- **clippy.toml** at the workspace root with the bans of rules 7, 14 and 15; each allow cites its
  inventory row.
- **Differential tests of the value-library crate** (core build; the C++ sources compiled into a test-only
  binary with the reference's flags):
  - `ob_gcvt` and `ob_fcvt` over random doubles and floats, including subnormals, ±0, powers of ten
    and halfway cases, for every width the callers can pass and precisions 0-31;
  - `ob_strtod` over generated strings (spaces, signs, `inf`, `nan`, long digit runs, overflow,
    underflow), comparing value bits, consumed length and error;
  - every ObNumber and decimal-int operation, both versions;
  - every hash function and per-type hash;
  - the sort and heap translation against `std::sort` compiled with the SDK headers, on arrays with
    many ties, comparing the whole permutation;
  - the printf helper against `snprintf`.
- **00b family 5** should add: ORDER BY over DOUBLE(M,D) values closer together than the tolerance;
  NaN and -0.0 in ORDER BY and GROUP BY; KEY-partitioned tables scanned without ORDER BY; FLOAT
  columns near 6-digit boundaries.

## 7. Questions only the developer can answer

1. **Platform scope of the numeric behavior.** Should the Rust build follow the macOS arm64
   reference's numeric behavior on every platform (rule 22), accepting that it will differ from a
   Linux or Windows C++ build of 834bbee1e when those platforms get gates?
2. **The judge profile.** May parity runs use a `judge` profile that differs from `release` in LTO,
   codegen units and incremental builds (no effect on numbers, faster rebuilds), or must they use
   `release` exactly?
3. **libc++ code in the fork.** May the Rust tree carry a translation of libc++'s sort and heap code
   (Apache-2.0 WITH LLVM-exception) with attribution? (Assumption: compatible with the fork's
   Apache-2.0.)
4. **Test-only C++.** May the core build carry test-only C++ (the frozen ob_dtoa.cc, ObNumber,
   wide_integer and hash sources compiled into a differential test binary)? Decision 13 lists the
   C++ kept in the product; it does not say whether test-only C++ is allowed.
5. **The `checked` profile.** Is it a diagnostic only, as proposed, or should zero `checked`-profile
   panics be an exit condition of Step 6?
