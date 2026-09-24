# 8. Numbers, concurrency, platform and toolchain

This design section turns into rules four parts of migration/design/ARCHITECTURE.md: §10's floating-point, overflow and profile decisions, §11 (atomics, locks, thread-locals, the stack), §13 (toolchain, dependencies, wasm guidelines) and the build side of §8. Implementers and core authors follow the rules; reviewers cite them by number ("rule 2.3" is section 2, item 3). §10's sorting and hashing rules are not restated; only their clippy bans appear (7.4).

- Authority is ARCHITECTURE's: decisions.md, PLAN.md, ARCHITECTURE.md, then research reports R09 (numerics), R10 (concurrency) and R12 (rules and toolchain). Where ARCHITECTURE leaves a detail open, the rule fills it and says so; section 13 lists the objections this section raised against ARCHITECTURE.md, each with its settlement (RESOLUTIONS.md s8-N); both files follow the settlements.
- Code facts are at 834bbee1e (`git diff --stat 834bbee1e -- src` prints nothing). Section 12 gives the commands behind new counts; other counts come from the report cited. "Assumption" marks what was not checked.
- Rust examples carry only the comments ARCHITECTURE §14.8 allows, since they show translated code.

## 1. Floating-point arithmetic

**Facts.**
- All 364 reference compile commands carry `-ffp-contract=off` (migration/judge/reference.md), so clang fuses no `a*b+c`; rustc does not fuse `*` and `+` either (assumption).
- No FMA runs on arm64: src has no `fma(`, `std::fma`, `__builtin_fma`, `vfma*` or `vmla*`. The 16 FMA lines are x86 AVX code in src/data_plane/api/data_plane/vector/ob_vector_l2_distance.h, with `-mfma` set only for x86 (src/sql/CMakeLists.txt:12-15, src/storage/CMakeLists.txt:20-23).
- `cpu_have_neon()` returns true on aarch64 (src/oblib/lib/cpu/ob_cpu_topology.cpp:190-197), so scalar kernels such as `l2_square_normal` (ob_vector_l2_distance.h:77-95, summing in `double`) never run on the reference.
- Rust 1.98.0 stabilized `f32`/`f64` `algebraic_add`, `_sub`, `_mul`, `_div` and `_rem` (core/src/num/f64.rs:1677-1724 in the 1.98.1 library source), which let the compiler fuse, reorder and ignore the sign of zero.

**Rules.**
1. No `mul_add`, no `algebraic_*` and no FMA intrinsic (`vfma*`, `vfms*`, `_mm*_fmadd_*`, `_mm*_fmsub_*`, `_mm*_fnmadd_*`, `_mm*_fnmsub_*`) anywhere, ob-simd included: PLAN's "`mul_add` only where the C++ calls `fma`" leaves nothing on arm64. The 16 x86 FMA lines lose their FMA too, since under default 22 (ARCHITECTURE §17) every platform follows arm64's numbers; if the developer rejects default 22, they get it back when x86 returns.
2. Every float expression keeps the C++ order, grouping and precision. `float` stays `f32` and `double` stays `f64`, and C++'s promotions are written out: an operation done in `float` and then widened is an `f32` operation followed by `as f64`; a `float` meeting a `double` or an unsuffixed literal is widened first (`x * 0.5` with `x: float` is `(x as f64) * 0.5`).
3. A float passed to a C variadic formatter (`databuff_printf`, `snprintf`, the log macros) is passed as `f64`, as C's default argument promotion does.
4. NEON kernels are translated with `core::arch::aarch64` intrinsics in the C++ lane order and reduction, inside ob-simd. Each also has a scalar version computing the same four `f32` lane sums in the same order and reducing them as `vaddvq_f32` does, so every target gets the NEON bits; a differential test on this Mac compares the two bit for bit, tails of 0-15 elements included. The C++'s own scalar kernels are translated for the targets that call them, which arm64 never does.
5. libm maps one to one, in the precision of the overload the C++ resolves (`std::pow(float, float)` is `powf`, so `f32::powf`):

| C++ | Rust |
|---|---|
| `pow`, `exp`, `log`, `log10`, `log2` | `powf`, `exp`, `ln`, `log10`, `log2`; never `powi` |
| `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sqrt` | the same names |
| `fmod`; `fabs`; `floor`, `ceil`, `trunc` | `%`; `abs`; `floor`, `ceil`, `trunc` |
| `rint`, `lrint`; `round`, `lround` | `round_ties_even`, `round_ties_even() as i64`; `round`, `round() as i64` |
| `fmax`, `fmin` | `f64::max`, `f64::min` |
| `std::max(a, b)`, `std::min(a, b)` on floats | `if a < b { b } else { a }`, `if b < a { b } else { a }` |
| `isnan`, `isinf`, `signbit` | `is_nan`, `is_infinite`, `is_sign_negative` |

6. Float comparisons are ported literally (ARCHITECTURE §10): NaN after every number and equal to NaN, -0.0 equal to 0.0, DOUBLE(M,D)'s `fabs(l - r) < P` (src/share/datum/ob_datum_cmp_func_def.h:111-171). `total_cmp`, `partial_cmp().unwrap()`, `clamp`, `signum` and `f64::max`/`min` never stand in for C++ code that does something else.
7. `static_cast<int64_t>(double)` becomes `as i64`, which saturates and maps NaN to 0 as arm64's conversion instruction does (assumption from the instruction set). Guarded C++ paths keep their guards.
8. Number text and parsing come only from the translated value libraries (ARCHITECTURE §10): no `Display`, `{:e}` or `{:.N}` of a float and no `str::parse::<f64>` outside the translated `ob_strtod` for anything that can reach output or a hash.

**Example: an `a*b+c` clang could fuse.** src/oblib/lib/charset/ob_dtoa.cc:1299, where `rv` is the file's `U` union (:420) and `z` a 32-bit `ULong` (:416):

```cpp
    dval(&rv)= tens[k - 9] * dval(&rv) + z;
```

```rust
        rv.d = tens[(k - 9) as usize] * rv.d + z as f64;
```

Two roundings, as the reference computes with `-ffp-contract=off`; `mul_add` would round once and change digits. `U` becomes a struct holding the `f64`, with word accessors over `to_bits`/`from_bits` (R12's union row).

**Example: FLOAT addition** (src/sql/engine/expr/ob_expr_add.cpp:691-713) adds in `float` (`res = l + r;`) and prints the operands with `"'(%e + %e)'", l, r`. In Rust `raw_op(res: &mut f32, l: f32, r: f32)` keeps `*res = l + r;` in `f32` (rule 2), and the message call passes `l as f64, r as f64` (rule 3); `databuff_printf_2!` (ob-base, over ob-errno's `cfmt`) prints `%e` as the reference libc does (s2-errors.md 2.9, "The printf family uses the same formatter").

**Example: a NEON kernel.** The 16-element loop of `l2_square_neon` (src/data_plane/api/data_plane/vector/ob_vector_l2_distance.h:1109-1130) becomes, in ob-simd:

```rust
    while d >= 16 {
        // SAFETY: dim - d + 16 <= len <= x.len(); ob_vector_l2_distance.h:1110
        let a = unsafe { vld1q_f32_x4(x[(dim - d) as usize..].as_ptr()) };
        // SAFETY: dim - d + 16 <= len <= y.len(); ob_vector_l2_distance.h:1111
        let b = unsafe { vld1q_f32_x4(y[(dim - d) as usize..].as_ptr()) };
        let mut c0 = vsubq_f32(a.0, b.0);
        ...
        c0 = vmulq_f32(c0, c0);
        ...
        c0 = vaddq_f32(c0, c1);
        c2 = vaddq_f32(c2, c3);
        c0 = vaddq_f32(c0, c2);
        sum = vaddq_f32(sum, c0);
        d -= 16;
    }
```

Each C++ statement keeps its place (the elided lines repeat the pattern for `c1` to `c3`), and no multiply and add pair becomes a `vfmaq_f32`. Loads are `unsafe fn` in stdarch, the arithmetic intrinsics safe (R12 §3). `vaddvq_f32` at :1180 stays `vaddvq_f32`, which rustc lowers to `llvm.aarch64.neon.faddv` (stdarch aarch64/neon/generated.rs:589-597), the intrinsic clang's `arm_neon.h` uses (clang side assumed).

**Example: rounding before a cast.** `common_double_int` (src/sql/engine/expr/ob_datum_cast.cpp:2225-2248) keeps both range checks and ends with `static_cast<int64_t>(rint(in))`, which becomes `r#in.round_ties_even() as i64` (`in` is a Rust keyword, ARCHITECTURE §14.3).

**The reviewer checks** for `mul_add`, `algebraic_*` and FMA intrinsics; that widenings and literal types match the C++ line by line; that variadic float arguments are `f64`; that libm calls follow the table; that no float comparator was simplified; and that no Rust float formatting or parsing feeds output.

## 2. Integer overflow and division

**Facts.** The reference has no `-fwrapv`; clang at -O2 compiles signed overflow, undefined behavior, as wraparound. Rust wraps with overflow checks off, but `dev`, `test` and `checked` turn them on (section 3). 273 lines use `OB_OPERATE_OVERFLOW` (195 in src/sql/engine/expr) and 122 `OB_DATA_OUT_OF_RANGE`; the arithmetic matrices pin 365 of those messages (R09 §2.1). Rust's `/` and `%` panic on a zero divisor and on `MIN / -1` in every profile.

**Rules.**
1. Where the C++ relies on wraparound, write `wrapping_*`: hash mixing (murmurhash, `fnv_hash2`, the collations' `hash_sort`), counters and sequences, the add-then-test operators, the absolute value of a KEY-partition hash. A plain `+`, `-`, `*` or unary `-` stays only where overflow cannot happen: the value libraries' differential tests run under the test profile, where a plain operator panics where the C++ wrapped.
2. Where the C++ checks, keep the check with its error code and message text. `is_*_out_of_range` tests are translated as written, over the wrapped result. `__builtin_add_overflow`/`__builtin_mul_overflow` (8 lines in 5 files) become `overflowing_add`/`overflowing_mul` when the three types are equal; with mixed types the value is computed in `i128`, range-tested against the result type and stored with `as`, as the builtin does.
3. Every C++ guard before `/` or `%` stays in place, and a division by a constant stays a plain operator. Every other integer `/` or `%` goes through ob-base's `arm64_div` and `arm64_rem`, which return what the arm64 reference computes: its `sdiv` and `udiv` give 0 for a zero divisor and wrap `MIN / -1` to `MIN`, and clang lowers `%` to a divide followed by `msub` (`a - (a / b) * b`, wrapping), so `a % 0` is `a` and `MIN % -1` is 0 (assumption from the architecture manual; a core-build unit test checks the helpers against the same operations compiled by the reference's clang). One form per operator:
   - signed division: `if b == 0 { 0 } else { a.wrapping_div(b) }`;
   - signed remainder: `if b == 0 { a } else { a.wrapping_rem(b) }`;
   - unsigned division and remainder: `a.checked_div(b).unwrap_or(0)` and `a.checked_rem(b).unwrap_or(a)`.

   `checked_div` and `checked_rem` alone are wrong for signed operands, since both return `None` for `MIN / -1` as for 0, and `wrapping_div` and `wrapping_rem` alone panic on a zero divisor. The helper is the default because the overflow sweep covers only value and expression code (migration/inventory/sweep/summary.tsv, the `overflow` row), and a plain `/` anywhere else would panic where the C++ returns: a plain `/` or `%` is written only where an inventory row shows the divisor is never 0 (nor -1 with a signed dividend that can be `MIN`). A site a row shows can reach 0 or `MIN / -1` also carries `BUG(port)`. x86_64, where the .result files were recorded, traps there, so no recorded case reaches such a site (ARCHITECTURE default 37).
4. A shift by a constant below the width stays plain: Rust never checks the bits shifted out, and `>>` on a signed type is arithmetic as in C++20. A shift by a computed amount that can reach the width is `wrapping_shl`/`wrapping_shr`, which masks the amount as arm64's shift instructions do.
5. A C `char` read as a number is `i8`. `long`, `unsigned long` and `ulong` are `i64`/`u64` on every target, never `isize`, `usize` or `c_long`. `size_t` stays `usize` for lengths and indexes and is `u64` where it reaches a hash, output or an encoded field (section 8).
6. Integer casts use `as`, which wraps and truncates as C++20's conversions do. An expression mixing signed and unsigned operands gets the conversion C++'s usual arithmetic conversions make, written out: `i64_value < u64_value` compares as `(i64_value as u64) < u64_value`.
7. Negation and absolute value of a value that can be the minimum are `wrapping_neg`/`wrapping_abs`.

**Example: the add-then-test operator,** src/sql/engine/expr/ob_expr_add.cpp:547-568, with the test at ob_expr_add.h:60-71 (`SHIFT_OFFSET` = 63, :205):

```cpp
  static void raw_op(int64_t &res, const int64_t l, const int64_t r)
  {
    res = l + r;
  }
```

```rust
impl ObIntIntBatchAddRaw {
    #[inline]
    pub fn raw_op(res: &mut i64, l: i64, r: i64) {
        *res = l.wrapping_add(r);
    }

    pub fn raw_check(res: i64, l: i64, r: i64) -> ObResult {
        let mut ret: ObResult = Ok(());
        if ObExprAdd::is_int_int_out_of_range(l, r, res) {
            let mut expr_str = [0u8; OB_MAX_TWO_OPERATOR_EXPR_LENGTH as usize];
            ret = Err(OB_OPERATE_OVERFLOW);
            let mut pos: i64 = 0;
            let _ = databuff_printf_2!(&mut expr_str, &mut pos, "'(%ld + %ld)'", l, r);
            log_user_error!(OB_OPERATE_OVERFLOW, "BIGINT", &expr_str[..pos as usize]);
        }
        ret
    }
}

impl ObExprAdd {
    #[inline]
    pub fn is_int_int_out_of_range(val1: i64, val2: i64, res: i64) -> bool {
        (val1 >> Self::SHIFT_OFFSET) != (res >> Self::SHIFT_OFFSET)
            && (val2 >> Self::SHIFT_OFFSET) != (res >> Self::SHIFT_OFFSET)
    }
}
```

With a plain `l + r`, a test-profile run would abort on `9223372036854775807 + 1` instead of printing the pinned `BIGINT value is out of range in '(9223372036854775807 + 1)'`.

**Example: `fnv_hash2`,** which names the query blocks printed as `%08X` in plans (src/oblib/lib/hash_func/murmur_hash.cpp:51-66; src/sql/resolver/dml/ob_sql_hint.cpp:89, :389). The C++ multiplies a signed `int32_t` and XORs in signed `char`s:

```rust
pub fn fnv_hash2(key: &[u8], seed: u32) -> u32 {
    let p: i32 = 16777619;
    let mut hash: i32 = 2166136261_u32 as i32;
    for &b in key {
        hash = (hash ^ (b as i8 as i32)).wrapping_mul(p);
    }
    hash = hash.wrapping_add(hash << 13);
    hash ^= hash >> 7;
    hash = hash.wrapping_add(hash << 3);
    hash ^= hash >> 17;
    hash = hash.wrapping_add(hash << 5);
    hash ^= seed as i32;
    hash as u32
}
```

`b as i8 as i32` is macOS's signed `char` (rule 5); `b as i32` would change the hash of every byte from 0x80 up.

**Example: the MySQL string hash state,** `unsigned long` in src/oblib/lib/charset/ob_ctype_utf8.cc:811-815: `n1[0]^= (((n1[0] & 63) + n2[0]) * (ch)) + (n1[0] << 8); n2[0]+= 3;` becomes

```rust
    *n1 ^= ((*n1 & 63).wrapping_add(*n2)).wrapping_mul(ch as u64).wrapping_add(*n1 << 8);
    *n2 = n2.wrapping_add(3);
```

**Example: KEY partitioning** keeps the C++'s negative result at `INT64_MIN` (src/sql/engine/expr/ob_expr_func_partition_key.cpp:80-81): `result_num = if result_num < 0 { result_num.wrapping_neg() } else { result_num };`.

**Example: integer DIV.** `ObExprIntDiv::div_int_int` (src/sql/engine/expr/ob_expr_int_div.cpp:191-222) tests `right->get_int() == 0` (NULL or `OB_DIVISION_BY_ZERO`) and then `INT64_MIN == left_i && (-1) == right_i` (`REPORT_OUT_OF_RANGE_ERROR`) before `left_i / right_i`. Both tests stay in that order, so the plain `/` after them cannot panic. `ObExprMod` keeps `INT64_MIN % -1` giving 0 (ob_expr_mod.cpp:110-111, :404-405).

**The reviewer checks** every arithmetic operator in hash, checksum, sequence and overflow-tested code; that each C++ check survives with its code and message; that each integer `/` and `%` has the C++ guard, a constant divisor, `arm64_div`/`arm64_rem`, or an inventory row showing its divisor is never 0; that no `c_long`, `isize` or `usize` carries a C++ `long` or a hashed value; and that mixed-sign comparisons convert as C++ does.

## 3. Build profiles, assertions and the judge build

**Decision** (ARCHITECTURE §10; defaults 21 and 22). rust/Cargo.toml carries these profiles (7.2 has the rest of the file):

```toml
[profile.release]
opt-level = 3
lto = "thin"
codegen-units = 1
debug = true
overflow-checks = false
debug-assertions = false
panic = "abort"

[profile.judge]
inherits = "release"
opt-level = 2
lto = "off"
codegen-units = 256
incremental = true
debug = "line-tables-only"

[profile.checked]
inherits = "judge"
overflow-checks = true
debug-assertions = true

[profile.dev]
panic = "abort"
```

- `release` is today's profile (rust/Cargo.toml:9-16) with the two checks written out, for the shipped binary and family 14; `codegen-units = 1` stays unless Step 2a's release build time says otherwise (R12 §3).
- `judge` builds every parity run. Its semantics equal `release`, since Rust's integer and float results depend on overflow checks and debug assertions, not on opt-level, LTO or codegen units (R09 §4.6); family 5 watches the two exceptions argued there, constant-folded libm calls and NaN payload bits. Step 2a may move the opt-level (default 21) and measures the rebuild and link time after an ob-base edit.
- `checked` runs weekly from Step 5 over the 272 cases and family 5's generator, diagnostic only; each panic becomes an inventory row under rules 2.1-2.3, or a bug. It also enables std's checks of `unsafe` preconditions (Rust 1.78; ffi-mechanics.md:137) in the named crates.
- `dev` serves `cargo check` and unit tests; `test` keeps Cargo's defaults for the differential tests. Tests unwind (Cargo ignores `panic` there), but every Rust function an island calls is `extern "C"`, so a panic still aborts at the boundary (Rust 1.81).
- From the sql-nio prior art (rust/Cargo.toml:14-23), `panic = "abort"` moves from named profiles to all, and `cmake-debug` goes, since Rust owns `main`.
- The final Step 6 gate runs family 1 once more on the `release` binary, so the shipped build is judged too (an addition ARCHITECTURE does not forbid).
- **Disk.** Each profile builds its own tree under rust/target/, and Decision 17 names the target directory (assumed 20-60 GB) as its risk; the Mac had 22 GiB free on 2026-09-25 (`df -h /`). So only the `judge` and `dev` trees persist. The `release` tree is built for family 14 and the final gate and the `checked` tree weekly from Step 5, and each is deleted after its run. Step 2a measures every tree against the free disk (PLAN §6, "What Step 2a measures") before the core build starts, and if they do not fit, `release` keeps `debug = "line-tables-only"` as `judge` does.
- **Other targets.** From the core build's exit, a weekly `cargo check --target x86_64-unknown-linux-gnu` of the engine crates, the islands and ob-platform's Mac-only parts left out by `cfg`, runs through the build daemon; it is diagnostic only, so shared code that stops building off the Mac shows early (Decision 7; ARCHITECTURE default 40). The Windows and Android targets 7.1 installs are checked the same way when their platform returns; aarch64 Linux and wasm are not installed and go unchecked until then.

**The judge binary** is built through the build daemon with `cargo build --profile judge --locked --offline -p seekdb` (rust/target/judge/seekdb). Each judge run records beside its recording (migration/judge/recordings.tsv) `rustc -Vv` (today `rustc 1.98.1 (48a229cea 2026-09-01)`), the profile, and the sha256 of Cargo.lock, of the island flags file (7.5) and of the binary, as reference.md does for the C++.

**Assertions.** The reference defines `NDEBUG` on all 364 compile commands, so:

| C++ | Rust |
|---|---|
| `OB_ASSERT(x)`, `(void)(x)` under `NDEBUG` (src/oblib/lib/utility/ob_macro_utils.h:707-708; 552 lines) | `ob_assert!(x)`, expanding to `let v: bool = x; debug_assert!(v);`, so `x` is evaluated in every profile. Plain `debug_assert!(x)` is wrong: it skips `x` when assertions are off |
| `OB_ASSERT_MSG(x, ...)` (:742-750) | evaluate; when false, log the ERROR line and backtrace; then `debug_assert!` |
| `assert(x)` (52 lines outside the vendored zstd) | `debug_assert!(x)` |
| `ob_assert(x)`, which is `ob_release_assert` (:752-764); `abort_unless(x)` (src/oblib/lib/ob_abort.h:35-43) | `assert!(x)`, on in every profile |
| `#ifdef NDEBUG`, `#ifndef NDEBUG` (113 lines) | `#[cfg(not(debug_assertions))]`, `#[cfg(debug_assertions)]` |
| `#ifdef ERRSIM` (off in the reference) | `#[cfg(feature = "errsim")]`, off in `judge` and `release`; tracepoints and `DEBUG_SYNC` are not behind it |

**The reviewer checks** that no crate or command line overrides these profile keys, that parity runs name `--profile judge`, and that every `OB_ASSERT` evaluates its argument.

## 4. Atomics

**Facts.** The `ATOMIC_*` macros (src/oblib/lib/atomic/ob_atomic.h:41-125) are sequentially consistent or full barriers, except `ATOMIC_LOAD_ACQ`, `_LOAD_RLX`, `_STORE_REL` and `_STORE_RLX`: 29 calls, 22 in src/share/cache (19 in its hazard-pointer files), 4 in src/storage/tx, 3 elsewhere; the `_AR` forms and `ATOMIC_CMP_AND_EXCHANGE` have no callers. Separately, 75 lines in 17 files pass a weaker `std::memory_order_*` to `std::atomic`. 724 of 966 member fields the macros touch are also accessed plainly (R10 §1.1).

**Rules** (ARCHITECTURE §11).
1. A field that one thread writes while another reads it is an `Atomic*` or sits inside a lock.
2. Every atomic operation uses `Ordering::SeqCst`, except the 29 calls above, which keep the order they name. The 75 `std::memory_order_*` lines become `SeqCst` too, as ARCHITECTURE §11 now says; that is never less correct, and they join the 29 only if Step 2a's sysbench shows a cost.
3. A plain C++ read or write of such a field becomes a `SeqCst` `load` or `store`. Constructors, and resets of an object no other thread can see, use `Atomic*::new` or `get_mut`.
4. Mapping: `ATOMIC_LOAD/STORE` → `load/store`; `ATOMIC_SET/TAS` → `swap`; `ATOMIC_FAA/FAS` → `fetch_add/fetch_sub`, and the `_AF` forms add the delta to the result; `ATOMIC_INC/DEC` → `fetch_add(1)/fetch_sub(1)`; `ATOMIC_BCAS` → `compare_exchange(old, new, SeqCst, SeqCst).is_ok()`; `ATOMIC_VCAS/CAS` → the same call's `Ok` or `Err` value; `ATOMIC_ANDF` → `fetch_and`; `ATOMIC_ADD_TAG/SUB_TAG` (src/storage/memtable/mvcc/ob_mvcc_row.h:43-58) → `fetch_or(tag)/fetch_and(!tag)` on an `AtomicU8`.
5. Reference counts, lock words, list links and reclamation clocks are replaced, not retyped: `Arc` (a custom `Drop` for pooled handles), `sync` locks, crossbeam queues, arc-swap or ob-epoch (section 5).
6. `SCN`, `ObTxSEQ` and LSN become `Copy` types plus atomic wrappers (`AtomicSCN`, `AtomicTxSEQ`, `AtomicLSN`) keeping the C++ member names (`atomic_load`, `atomic_store`, `atomic_set`, `atomic_get`, `atomic_bcas`, `atomic_vcas`, `inc_update`, `dec_update`), so call sites translate one to one. The integer templates `inc_update<T>`/`dec_update<T>` (ob_atomic.h:97-121) become `v.fetch_max(x, SeqCst).max(x)` and `v.fetch_min(x, SeqCst).min(x)`, since the C++ returns the new value. `SCN::inc_update`/`dec_update` are not `fetch_max`: see the example.
7. No 128-bit atomics (section 8): palf's `LSNAllocator` keeps its 16-byte `lsn_ts_meta_` (src/logservice/palf/lsn_allocator.h:91-107) under a `Mutex`; `REACH_COUNT_PER_SEC` (src/oblib/lib/utility/ob_macro_utils.h:842; src/storage/tx/ob_tx_ctx.cpp:3054, :5275) becomes a `Mutex` or two atomics.
8. Outside the named crates: no `AtomicPtr` (a published pointer is an `ArcSwap` or an arena id), no `fence`, `compiler_fence` or `std::hint::spin_loop` (`MEM_BARRIER`, `WEAK_BARRIER`, `PAUSE`). `volatile` becomes an atomic where it signals between threads and a plain field otherwise; `CACHE_ALIGNED` becomes `CachePadded`.

**Example: a flag read without the macros.** `ObServerRuntime::stopped_` is written with `ATOMIC_STORE` and read plainly (src/observer/omt/ob_server_runtime.h:193-197, :282; .cpp:313, :457):

```cpp
  void stop() { ATOMIC_STORE(&stopped_, ObTimeUtility::current_time()); }
  bool has_stopped() const { return stopped_ != 0; }
  const int64_t ts = ObTimeUtility::current_time() - runtime->stopped_;
```

```rust
    pub fn stop(&self) {
        self.stopped_.store(ObTimeUtility::current_time(), SeqCst);
    }
    pub fn has_stopped(&self) -> bool {
        self.stopped_.load(SeqCst) != 0
    }
    let ts = ObTimeUtility::current_time() - runtime.stopped_.load(SeqCst);
```

`stopped_` is an `AtomicI64` built with `AtomicI64::new(0)`; the plain C++ reads were data races.

**Example: why `SCN::inc_update` stays a loop.** `SCN` wraps a `uint64_t` whose invalid value is `UINT64_MAX` (src/share/scn.h:25, :33), and `operator<` orders an invalid SCN below every valid one (src/share/scn.cpp:330-342). `inc_update` loops on that operator (scn.cpp:168-179). `AtomicU64::fetch_max` on the raw bits would keep `UINT64_MAX` forever where the C++ replaces it with the first valid SCN:

```rust
    pub fn inc_update(&self, ref_scn: SCN) -> SCN {
        let mut old_scn;
        let mut new_scn = self.atomic_load();
        loop {
            old_scn = new_scn;
            if !(old_scn < ref_scn) {
                break;
            }
            new_scn = self.atomic_vcas(old_scn, ref_scn);
            if new_scn.val_ == old_scn.val_ {
                new_scn = ref_scn;
            }
        }
        new_scn
    }
```

**The reviewer checks** that every field the C++ touches with `ATOMIC_*` or `.atomic_*()` is atomic or locked; that every weaker `Ordering` traces to one of the 29 calls; that `fetch_max`/`fetch_min` replace only integer `inc_update`/`dec_update`; and that rule 8's items stay in named crates.

## 5. Locks, lock-free code and thread-locals

**Facts** (R10 §1.2). Every OB lock is built on `ObLatch`/`ObLatchMutex` (src/oblib/lib/lock/ob_latch.h:42-71, :159-253), with ids from 206 `LATCH_DEF` entries whose statistics are no longer visible; 479 lock declarations, 2,151 guard objects, about 27 timed calls and 49 timed guards; `ObRowLatch` spins with no pause (src/storage/memtable/mvcc/ob_row_latch.h:37-43).

**The `sync` module** in ob-base is the only lock API; nothing else names parking_lot:

```rust
pub use parking_lot::{Condvar, Mutex, MutexGuard, RwLock, RwLockReadGuard, RwLockWriteGuard, WaitTimeoutResult};
pub use crossbeam_utils::sync::ShardedLock;
pub use crossbeam_utils::CachePadded;

pub struct ObBucketLock { buckets: Box<[RwLock<()>]> }

pub fn abs_timeout_to_instant(abs_timeout_us: i64) -> std::time::Instant;
```

`abs_timeout_to_instant` turns the C++'s absolute wall-clock microseconds into the `Instant` that parking_lot's `try_lock_until`, `try_read_until` and `try_write_until` take (lock_api 0.4.14 src/mutex.rs:389, src/rwlock.rs:759, :793).

**Rules.**
1. `Mutex<T>` and `RwLock<T>` own the data they guard. A function the C++ calls with the lock held (names ending in `_unlock`, or checking `is_wrlocked_by`) takes the guarded data or the guard as a parameter. `Mutex<()>` and `ObBucketLock` serve only where the lock serializes something outside Rust memory or data held elsewhere, which the inventory row names. The rows come from the lock sweep, lock-guards.tsv (RULEBOOK section 5): one row per lock declaration (479, R10 §1.2), with the fields it guards (the `T`), the functions the C++ calls with it held, the nesting order observed, a timed lock's code, and its kind (`Mutex<T>`, `RwLock<T>`, `Mutex<()>`, `ObBucketLock`, reentrant). migration/design/evidence/lock_decls.py, which lists the declarations, seeds it; atomic-fields.tsv, which lists only fields touched by atomic operations, does not cover locks.
2. Timed locks keep each site's error code: the latch's `OB_TIMEOUT`, or what the site remaps it to.
3. No pure spin locks: `ObRowLatch` becomes a one-byte `sync::Mutex<T>` over the row state, which spins briefly and then parks. Latch ids, spin counts and the wait queue go.
4. Guards never cross threads (`send_guard` off, so moving one fails to compile). A handoff where the second of two parties frees the object (`ObDDLClogCbStatus::try_set_release_flag`, src/storage/ddl/ob_ddl_clog.cpp:38-41) becomes an `Arc` both hold.
5. Callbacks, IO and calls into another crate run after the guard drops; the locked section returns the work, as `CtxLock`'s `before_unlock`/`after_unlock` do (src/storage/tx/ob_trans_ctx_lock.h:82-83).
6. A guard is bound to a named local or a `match` arm, never a temporary in an `if let`/`while let` condition or a tail expression: edition 2024 moved where those temporaries drop (`if_let_rescope`, `tail_expr_drop_order`), and the unlock point must be visible.
7. Nested locks keep the C++ order, recorded in each lock's row of lock-guards.tsv, since ARCHITECTURE §14.8 allows no comment for it.
8. `ObThreadCond`/`ObCond` → `Condvar` with the `Mutex<T>` holding the waited-for state; `ObLightyQueue` → a `bounded` crossbeam channel; `ObFixedQueue` → `ArrayQueue`; `ObLinkQueue`/`ObSpLinkQueue` → `SegQueue` or a channel; `ObRecursiveMutex` (6 declarations) → `parking_lot::ReentrantMutex<T>` or a restructured site, per its row of lock-guards.tsv.
9. `TCRWLock` sites become `RwLock`, and `ShardedLock` only where Step 2a's sysbench at 64 threads shows the need, first at the plan cache node (src/sql/plan_cache/ob_i_lib_cache_node.h:214) and the schema manager cache (src/share/schema/ob_schema_mgr_cache.h:148).

**Example: a timed lock remapped to `OB_EAGAIN`.** `ObTabletDDLKvMgr` locks an `ObLatch` (src/storage/ddl/ob_tablet_ddl_kv_mgr.h:199) with a 10 s timeout (:190), turns `OB_TIMEOUT` into `OB_EAGAIN` and hands the caller a thread id to unlock with (ob_tablet_ddl_kv_mgr.cpp:161-191). `get_or_create_idem_ddl_kv` (:302-342) uses it:

```cpp
    uint32_t lock_tid = 0; // try lock to avoid hang in clog callback
    if (OB_FAIL(rdlock(TRY_LOCK_TIMEOUT, lock_tid))) {
    } else {
      try_get_ddl_kv_unlock(macro_redo_scn, kv_handle);
    }
    if (lock_tid != 0) {
      unlock(lock_tid);
    }
```

```rust
    pub fn rdlock(&self, timeout_us: i64) -> ObResult<RwLockReadGuard<'_, DdlKvState>> {
        let abs_timeout_us = timeout_us + ObTimeUtility::current_time();
        match self.lock_.try_read_until(abs_timeout_to_instant(abs_timeout_us)) {
            Some(guard) => Ok(guard),
            None => Err(OB_EAGAIN),
        }
    }

        match self.rdlock(Self::TRY_LOCK_TIMEOUT) {
            Err(e) => ret = Err(e),
            Ok(state) => self.try_get_ddl_kv_unlock(&state, macro_redo_scn, kv_handle),
        }
```

`DdlKvState` holds the fields the latch guards today (the name is illustrative). The guard dropping at the end of the arm is `unlock(lock_tid)`, and the thread id disappears. `try_get_ddl_kv_unlock` takes `&DdlKvState`, so it cannot be called without the lock (rule 1).

**Lock-free code** (ARCHITECTURE §11; default 23). Hand-written reclamation (QClock and the retire station, `ObQSync`, `HazardRef`, the KV cache's hazard versions and pointers: about 17K lines with the structures, R10 §1.3) is replaced, not translated. Every structure starts behind a lock; published snapshots use arc-swap; ob-epoch, the one crate over crossbeam-epoch, serves only where the performance gate shows lock-free reads are needed, with an API the core API sessions fix (s6-storage.md 10.3). Each lock-free structure has loom tests (`--cfg loom`, declared through `check-cfg`) and an Opus 5.5 lock-free reviewer; no Miri or ThreadSanitizer job, since both need nightly.

**Thread-locals** (ARCHITECTURE §11).
1. State that changes a statement's result, errors or limits is a parameter or a context field: the worker, the memory context, the memory tracker, the interrupt checker, the DAS and expression-serialization contexts, the freeze source, `is_doing_ddl_`, the memory label (C++ sites in R10 §1.4).
2. `thread_local!` holds only const-initialized `Copy` values from a closed list (the request deadline, the trace id, the thread name, diagnostics, caches that change no result, and section 6's stack bookkeeping: `all_stack_size`, `g_stackaddr` and `g_stacksize`, and on wasm the nesting count), declared `static x: Cell<T> = const { Cell::new(..) }`, plus two slots set and cleared by a scope guard: ARCHITECTURE §2's warning-buffer slot, and sql-parser-sys's per-call parse slot (ARCHITECTURE §11; s7-islands-unsafe.md 7.5 rule 3), a `Cell<*const ParsePool>` that is null outside a C parse call and points at the current parse's arena and memory tracker during one. The parse slot lives in sql-parser-sys's `ffi` module, since reading through its pointer is `unsafe`.
3. Diagnostics and the warning-buffer slot are set and cleared by a scope guard at a named entry (request, PX task, DAG task) and copied at each handoff. Engine code never assumes its own spawner initialized a thread-local; a later library host brings its own threads.
4. The warning-buffer slot is the one thread-local with a destructor (the parse slot holds a raw pointer, which is `Copy`), which on wasm needs what the wasm nightly had to patch (Decision 16 notes).

**The reviewer checks** that no lock guards data it does not own, that timed locks keep their codes, that no guard is a temporary in a condition, that callbacks run after unlock, that no `send_guard`, spin loop or hand-written reclamation appears, and that each `thread_local!` is on the closed list.

## 6. Stack growth and the wasm depth limit

**What the C++ does.** `SMART_CALL(f)` checks for a 64 KiB reserve (192 KiB for `SMART_CALL_LARGE`), calls `f` directly when enough is left, and otherwise switches to a 2 MiB heap extension with no guard page, refusing with `OB_SIZE_OVERFLOW` once the thread's total would pass 10 MiB (src/oblib/lib/utility/ob_smart_call.h:28-36, :72-90, :112-138). 1,130 lines call `SMART_CALL` and 6 `SMART_CALL_LARGE`. `check_stack_overflow` has a `bool&` form (90 calls, 32 KiB default reserve; ob_common_utility.cpp:35, :45-83) and a no-argument form returning `OB_SIZE_OVERFLOW` (16 calls; ob_common_utility.h:39-44). Workers get 224 KiB on this Mac (src/observer/ob_server.cpp:1966-1974), and the switch ran under the 272 cases (R10 §1.7).

**stacker on macOS (PLAN §8 item 20).** By reading stacker 0.1.25: `remaining_stack()` is the stack pointer minus a thread-local limit starting at `pthread_get_stackaddr_np - pthread_get_stacksize_np` (src/backends/macos.rs), the bounds the C++ reads (ob_common_utility.cpp:111-119); `grow` maps a stack with a guard page on each side (src/mmap_stack_restore_guard.rs), sets the limit to its base and runs the closure there (src/lib.rs). Both are safe functions, so ob-base stays `forbid(unsafe_code)`. stacker has no total cap; a failed `mmap` panics and so aborts, as Decision 12 wants.

**The API** is ob-base's stack module, the core unit replacing ob_smart_call.{h,cpp} and the stack functions of ob_common_utility.cpp. Names are the C++ ones; values are ARCHITECTURE §11's starting values, which Step 2a's probe fixes:

```rust
pub const STACK_RESERVED_SIZE: usize = 256 << 10;
pub const STACK_RESERVED_SIZE_LARGE: usize = 1 << 20;
pub const STACK_PER_EXTEND: usize = 8 << 20;
pub const ALL_STACK_LIMIT: usize = 64 << 20;
pub const DEFAULT_THREAD_STACK_SIZE: usize = 8 << 20;
pub fn get_reserved_stack_size() -> usize;

pub fn smart_call<T>(reserved_size: usize, func: impl FnOnce() -> ObResult<T>) -> ObResult<T>;
pub fn check_stack_overflow(is_overflow: &mut bool, reserved_stack_size: usize) -> ObResult;
pub fn check_stack_overflow_2() -> ObResult;
pub fn get_stackattr() -> Option<(usize, usize)>;
pub fn set_stackattr(stackaddr: usize, stacksize: usize);

#[macro_export]
macro_rules! smart_call {
    ($func:expr) => { $crate::stack::smart_call($crate::stack::STACK_RESERVED_SIZE, || $func) };
}
#[macro_export]
macro_rules! smart_call_large {
    ($func:expr) => { $crate::stack::smart_call($crate::stack::STACK_RESERVED_SIZE_LARGE, || $func) };
}
```

- `get_reserved_stack_size()` is 128 KiB, the C++'s 32 KiB scaled like the other reserves (ARCHITECTURE gives no value); a `const` assertion keeps the C++ check that it is below `STACK_RESERVED_SIZE` (ob_common_utility.cpp:36-37).
- `check_stack_overflow` keeps the C++ out-parameter, so call sites keep their `else if` chains. `check_stack_overflow_2` is the no-argument C++ overload, second in ob_common_utility.h (:31-44), named by ARCHITECTURE §14 rule 3's overload rule. The C++'s third parameter of the first form, `used_size`, is dropped: no call passes it (`git grep -n -P 'check_stack_overflow\([^()]*,[^()]*,' -- 'src/*.h' 'src/*.cpp' 'src/*.c'` prints nothing).
- Native `smart_call`: `OB_ERR_UNEXPECTED` if `stacker::remaining_stack()` is `None` (the C++ "stack incorrect params", ob_common_utility.cpp:66-69); `func()` if at least `reserved_size` is left; `OB_SIZE_OVERFLOW` if `all_stack_size + STACK_PER_EXTEND > ALL_STACK_LIMIT` (ob_smart_call.h:82-83); otherwise `stacker::grow(STACK_PER_EXTEND, ..)`, whose closure records the segment with `set_stackattr(sp - stacker::remaining_stack(), STACK_PER_EXTEND)` (`sp` the address of a local) before running `func`, restoring the record and `all_stack_size` after.
- `all_stack_size`, `g_stackaddr` and `g_stacksize` keep their C++ names as `Cell<usize>` thread-locals. `all_stack_size` counts extension bytes only (ARCHITECTURE §11); `get_stackattr()` is `None` on the thread's own stack.

**Rules.**
1. Every `SMART_CALL(f)` becomes `smart_call!(f)` and every `SMART_CALL_LARGE(f)` `smart_call_large!(f)`, at the same place around the same call.
2. Every `check_stack_overflow`, `check_stack_once` (src/sql/engine/ob_operator.cpp:517-526) and expression check (src/query/api/query/engine/expr/ob_expr.h:1055) calls the Rust function of the same form and keeps its C++ reaction: an error, or the silent skip of `ObRawExpr::get_name` (src/sql/resolver/expr/ob_raw_expr.cpp:319-330). The code generator keeps marking every 16th level (`STACK_OVERFLOW_CHECK_DEPTH`, src/sql/code_generator/ob_static_engine_expr_cg.h:74).
3. Recursive algorithms keep their shape (PLAN §3 item 6). The depth where `OB_SIZE_OVERFLOW` fires is not a contract (ARCHITECTURE §11); semantic limits such as `OB_MAX_SUBQUERY_LAYER_NUM` = 64 (src/oblib/lib/ob_define.h:304) are kept exactly.
4. ob-runtime's spawner (ARCHITECTURE §7.2) gives engine threads `max(stack_size, DEFAULT_THREAD_STACK_SIZE)`, rounded up to the page size; `stack_size` keeps its name, default and range [256K, 20M] (src/share/parameter/ob_parameter_seed.ipp:730-731) as a lower bound (default 23).
5. Deep recursive data is never dropped, cloned or printed recursively: parse, expression and plan trees live in id arenas (ARCHITECTURE §4); JSON and XML trees do too or have an iterative `Drop`, since a left-deep `1+1+...+1` tree dropped recursively would overflow.
6. A value over 16 KiB is built on the heap or in an arena, never as a local, replacing the 728 `SMART_VAR`/`HEAP_VAR` lines (R10 §1.6); clippy's `large_stack_arrays` (16,384 bytes) and `large_stack_frames` (64 KiB) warn.

**Example.** `ObRawExprResolverImpl::recursive_resolve` and the check opening `do_recursive_resolve` (src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:350-367):

```cpp
  return SMART_CALL(do_recursive_resolve(node, expr, is_root_expr));
  ...
  if (OB_ISNULL(node)) {
    ret = OB_INVALID_ARGUMENT;
  } else if (OB_FAIL(check_stack_overflow(is_stack_overflow))) {
  } else if (is_stack_overflow) {
    ret = OB_SIZE_OVERFLOW;
  } else {
```

```rust
        smart_call!(self.do_recursive_resolve(node, expr, is_root_expr))
        ...
        if node.is_none() {
            ret = Err(OB_INVALID_ARGUMENT);
        } else if let Err(e) = check_stack_overflow(&mut is_stack_overflow, get_reserved_stack_size()) {
            ret = Err(e);
        } else if is_stack_overflow {
            ret = Err(OB_SIZE_OVERFLOW);
        } else {
```

The C++ default argument is passed explicitly (R12's default-argument row).

**The islands share the bounds** (ARCHITECTURE §§9.4, 11).
- share/geo keeps its 5 `SMART_CALL` sites and its check at src/share/geo/ob_geo_topology_calculate.cpp:163. The kept oblib subset's `get_stackattr`/`set_stackattr` call two Rust callbacks in geo-sys, named by ARCHITECTURE §9.3 rule 2: `int32_t obgeo_rs_get_stackattr(uint64_t *stackaddr, uint64_t *stacksize)`, which returns `OB_ENTRY_NOT_EXIST` on the thread's own stack so the C++ falls back to its pthread path, and `int32_t obgeo_rs_set_stackattr(uint64_t stackaddr, uint64_t stacksize)`. Without them, geo code on a stack Rust grew sees its stack pointer outside its cached bounds and fails (ob_common_utility.cpp:66-69).
- sql-parser-sys implements `check_stack_overflow_c` (src/query/api/query/parser/parse_node.h:461; called at src/sql/parser/parse_node.c:657, :696) and `obpl_parser_check_stack_overflow` (called at src/pl/parser/pl_parser_mysql_mode.y:90) over `check_stack_overflow(&mut is_overflow, get_reserved_stack_size())`, answering true on an error as the C++ does (ob_common_utility.cpp:57, :63, :67). They check, never grow, and return normally (ARCHITECTURE §9.2).
- Island callbacks never call `smart_call` or a stack check: stacker's limit describes the Rust segment, not an extension geo allocated.

**The wasm depth limit** (ARCHITECTURE §§11, 13). Under `#[cfg(target_family = "wasm")]`, `smart_call` never grows: it counts nesting in a `Cell<u32>` thread-local and returns `OB_SIZE_OVERFLOW` above `MAX_SMART_CALL_DEPTH`, and `check_stack_overflow` reports overflow at that depth. The limit is measured when wasm returns; until then it is a provisional constant with a `TODO(port)` against ob_smart_call.h:66-68, and the arm is untested. stacker cannot serve wasm: there `remaining_stack()` is `None` and `grow` moves only the linear-memory stack (R10 §1.9).

**Step 2a runs** the stacker test (a 224 KiB thread recursing through `smart_call!` to `OB_SIZE_OVERFLOW` at the cap, not a signal, timing each switch), the stack probe (the deepest recursions the C++ survives; reserves at least twice the largest use between two checks) and one geo call on a grown stack.

**The reviewer checks** that each C++ stack site has its counterpart with the same reserve and reaction, that no recursion became a loop, that no tree type has a recursive `Drop` or `Clone`, and that no large value lives on the stack.

## 7. Toolchain and build configuration

Decision 16 (a): one pinned stable toolchain, no nightly features in shared code, allocator-api2 instead of `allocator_api`. Rejected: any nightly, including a nightly CI job for Miri or ThreadSanitizer (default 23).

### 7.1 rust/rust-toolchain.toml

```toml
[toolchain]
channel = "1.98.1"
profile = "minimal"
components = ["clippy", "rustfmt"]
targets = ["x86_64-unknown-linux-gnu", "x86_64-pc-windows-gnu", "aarch64-linux-android"]
```

rustfmt is new (the installed toolchain has none: `ls ~/.rustup/toolchains/1.98.1-aarch64-apple-darwin/bin`). A unit formats its file with `rustfmt --edition 2024 <file>`, which rewrites the file in place and compiles nothing, so loops may run it; gate 9's `rustfmt --edition 2024 --check` then fails on a parse error and on any formatting difference, which a formatted file does not have.

### 7.2 The workspace manifest

```toml
[workspace]
resolver = "3"

[workspace.package]
edition = "2024"
rust-version = "1.98"
publish = false

[workspace.lints.rust]
unsafe_code = "forbid"
unsafe_op_in_unsafe_fn = "deny"
non_camel_case_types = "allow"
non_snake_case = "allow"
non_upper_case_globals = "allow"

[workspace.lints.clippy]
correctness = { level = "deny", priority = -1 }
suspicious = { level = "deny", priority = -1 }
style = { level = "warn", priority = -1 }
complexity = { level = "warn", priority = -1 }
disallowed_methods = "deny"
disallowed_types = "deny"
disallowed_macros = "deny"
large_stack_arrays = "warn"
large_stack_frames = "warn"
too_many_arguments = "allow"
manual_clamp = "allow"
manual_midpoint = "allow"
manual_div_ceil = "allow"
manual_abs_diff = "allow"
suboptimal_flops = "allow"
imprecise_flops = "allow"
```

The manifest script writes the members list and every lib.rs (ARCHITECTURE §14.5). The last six lints are off because their suggestions change results (`clamp` treats NaN differently and panics when min > max; `suboptimal_flops` proposes `mul_add`); no translated code is rewritten to silence a style or complexity warning. Named crates (ARCHITECTURE §8) write their own `[lints]` without `unsafe_code`, denying `unsafe_op_in_unsafe_fn`, `undocumented_unsafe_blocks` and `multiple_unsafe_ops_per_block`; the last checks "`// SAFETY:` above each operation".

### 7.3 What edition 2024 changes for translators

- `unsafe extern "C"` blocks and `#[unsafe(no_mangle)]` in the named crates.
- `gen` is reserved, and the C++ uses it as a name (`UUID::gen`, src/oblib/lib/trace/ob_trace.h:88), so it becomes `r#gen` under ARCHITECTURE §14.3's keyword rule.
- `if let` temporaries drop before `else`, and tail-expression temporaries before locals: rule 5.6.
- Let chains (`if let Some(x) = map.get(k) && x.is_valid()`) are stable in edition 2024 (Rust 1.88 release notes, not checked here) and fit C++ conditions that test a lookup and a value together.

### 7.4 clippy.toml

One clippy.toml at rust/ holds the bans. sql-nio keeps its own, since crate 37 may use only ob-platform (ARCHITECTURE §1.1 row 37), not ob-base's `sync` and maps or ob-runtime's spawner, and so keeps std locks (rust/sql-nio/src/lib.rs:25), std `HashMap` (reactor.rs:215) and its reactor threads (reactor.rs:1113); RULEBOOK section 1's "sql-nio's exceptions" lists all it keeps. Its clippy.toml bans everything below except those, `std::process::abort` included, since its `catch_unwind` and `abort` around each reactor thread go. Clippy rejects a path it cannot resolve (`cargo clippy --explain disallowed_methods`), so a wrong path shows on first use. An exception is `#[allow(clippy::disallowed_methods, reason = "<inventory row>")]` at the site.

```toml
array-size-threshold = 16384
stack-size-threshold = 65536
disallowed-methods = [
  "f32::mul_add", "f64::mul_add", "f32::powi", "f64::powi",
  "f32::algebraic_add", "f32::algebraic_sub", "f32::algebraic_mul", "f32::algebraic_div", "f32::algebraic_rem",
  "f64::algebraic_add", "f64::algebraic_sub", "f64::algebraic_mul", "f64::algebraic_div", "f64::algebraic_rem",
  "f32::total_cmp", "f64::total_cmp", "str::parse",
  "slice::sort_unstable", "slice::sort_unstable_by", "slice::sort_unstable_by_key",
  "slice::select_nth_unstable", "slice::select_nth_unstable_by", "slice::select_nth_unstable_by_key",
  "std::collections::HashMap::new", "std::collections::HashMap::with_capacity",
  "std::collections::HashSet::new", "std::collections::HashSet::with_capacity",
  "std::thread::spawn", "std::thread::Builder::spawn",
  "std::sync::atomic::fence", "std::sync::atomic::compiler_fence", "std::hint::spin_loop",
  "std::env::set_current_dir", "std::process::exit", "std::process::abort",
  "ob_platform::process::_exit", "ob_platform::process::fork", "ob_platform::process::lockf",
  "ob_platform::alloc::configure_darwin_malloc_zone", "ob_platform::alloc::restore_malloc_backend_after_fork",
]
disallowed-types = [
  "std::collections::HashMap", "std::collections::HashSet", "std::hash::RandomState",
  "std::collections::BinaryHeap", "std::sync::Mutex", "std::sync::RwLock", "std::sync::Condvar",
  "std::sync::atomic::AtomicPtr",
]
disallowed-macros = ["std::println", "std::eprintln", "std::dbg"]
```

Rows, not clippy.toml, allow: `Builder::spawn` in ob-runtime's spawner; `str::parse` inside the translated `ob_strtod`; `process::exit`, `process::abort`, `eprintln!`, `set_current_dir` and the five ob-platform functions above in the seekdb crate; `fence`, `spin_loop` and `AtomicPtr` in named crates. ob-platform keeps these functions at the paths above, under the C++ names where the C++ has them (`configure_darwin_malloc_zone` and `restore_malloc_backend_after_fork`, src/oblib/lib/allocator/ob_malloc.cpp:178, :170); a path clippy cannot resolve fails on first use (above), so a renamed function cannot slip past the list. ob-runtime and sql-nio depend on ob-platform, and these entries keep them from calling the process functions.

### 7.5 C and C++ built by cargo

Every C and C++ file the Rust build compiles (the islands, zstd 1.3.8, SQLite, ring, psm's assembly, the test-only differential C++; jemalloc with its own flags, below) uses deps/3rd's clang 17.0.6, SDK 26.2 and the reference flags, in every profile. rust/.cargo/config.toml sets them for the Mac target only, so `cargo check --target` for the other three targets is unaffected:

```toml
[env]
CC_aarch64_apple_darwin = { value = "../deps/3rd/usr/local/oceanbase/devtools/bin/clang", relative = true }
CXX_aarch64_apple_darwin = { value = "../deps/3rd/usr/local/oceanbase/devtools/bin/clang++", relative = true }
SDKROOT = "/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk"
MACOSX_DEPLOYMENT_TARGET = "27.0"
CFLAGS_aarch64_apple_darwin = "-O2 -g -DNDEBUG -ffp-contract=off -fno-strict-aliasing -fno-omit-frame-pointer -fmax-type-align=8 -march=armv8-a+crc+lse -mtune=generic"
CXXFLAGS_aarch64_apple_darwin = "-O2 -g -DNDEBUG -ffp-contract=off -fno-strict-aliasing -fno-omit-frame-pointer -fmax-type-align=8 -march=armv8-a+crc+lse -mtune=generic -std=gnu++20"
JEMALLOC_SYS_CONFIGURE_ARGS = "--with-jemalloc-prefix=je_"

[net]
offline = true
```

- These are the code-generation flags all 364 reference compile commands share, and their deployment target (`-mmacosx-version-min=27.0` on all 364).
- jemalloc is the exception: the reference builds seekdb-jemalloc-sys with its own settings (deps/external/cmake/Jemalloc.cmake:10-40): `CC` the external C compiler, `CFLAGS` `-O2 -fPIC` plus `-arch` and the deployment target, and `JEMALLOC_SYS_CONFIGURE_ARGS=--with-jemalloc-prefix=je_`, which gives the `je_*` names ob-platform declares and the islands call (s7-islands-unsafe.md 7.8 rule 5). So config.toml also sets `JEMALLOC_SYS_CONFIGURE_ARGS = "--with-jemalloc-prefix=je_"`, and jemalloc's build gets those flags, not the shared ones above. How to keep the target `CFLAGS` off jemalloc's build script is fixed when the workspace first builds (assumption: seekdb-jemalloc-sys reads the target's flags through the cc crate, as tikv-jemalloc-sys does); the check is jemalloc's configure line in the build log against the reference's. The cc crate appends `CFLAGS`/`CXXFLAGS` last, "to allow these to override everything else" (cc 1.4.7 src/lib.rs:2195-2200), so its own `-O` loses.
- An island's defines and include paths (the other 10 `-D` flags on every entry, such as `-D_NO_EXCEPTION`, and per-target ones such as `-fvisibility=hidden` and `-fopenmp`) come from a flags file a script derives from compile_commands.json for the same sources, checked in and diffed at gates. No hand-written flag lists.
- If a Command Line Tools update removes SDK 26.2, `SDKROOT` points at the archive's copy (/Users/colin/seekdb-dev/ref-archive-834bbee1e/MacOSX26.2.sdk). Prebuilt archives come from deps/3rd unchanged (9.3).

### 7.6 Lockfile, pins, loop denies and gates

- Cargo.lock is committed: delete `/Cargo.lock` from rust/.gitignore (:5), which ignores it today. Every build runs `--locked --offline`.
- `=` pins only where bytes are judged: `flate2 = "=1.1.10"` and `miniz_oxide = "=0.9.1"` in sql-nio (the compressed-protocol frames, rust/sql-nio/src/compress.rs:93; naming miniz_oxide directly makes the pin bind) and `seekdb-jemalloc-sys = "=0.2.2"`. Cargo.lock holds the rest; they change only with a row in section 9.
- The loop denies add `cargo clippy`, `cargo doc`, `cargo bench`, `cargo fix` (ARCHITECTURE §13), and `cargo rustc`, `cargo +` (a toolchain override slips past every `cargo <verb>` prefix pattern) and `rustc`. All of them compile.
- A gate greps rust/, rust/.cargo/ and migration/scripts/ for `#![feature(`, `RUSTC_BOOTSTRAP` and `-Z` flags.
- Decision 16 reopens only if Step 2a measures per-crate checks above about 60 s; at the measured 19,000-44,000 lines per second, the largest crate (174,714 lines) checks in about 4-10 s (migration/measurements/cargo-check-rate.md). wasm gets its own pinned nightly when it returns.

## 8. The wasm guidelines

All six are adopted now (ARCHITECTURE §13; R12 §8). They cost little on the Mac and would be rework later.

| Guideline | The rule now | Enforced by |
|---|---|---|
| u64 for persisted and wire fields | Every encoded integer has a fixed width; no `usize`/`isize` in an encoding function or format struct. Hash values and bucket arithmetic (`hash % bucket_num`, src/oblib/lib/hash/ob_hashtable.h:1139) are `u64`, so a 32-bit `usize` cannot change them | reviewer; rule 2.5 |
| No 128-bit atomics | `AtomicU128` is unstable in 1.98.1 (core/src/sync/atomic.rs:3770-3786); portable-atomic and `AtomicCell<u128>` are banned (9.4). Plain `u128` arithmetic, which the decimal-int library uses (ob_wide_integer.h:48-50), is fine | rule 4.7 |
| Depth limit on wasm, growth on native | the wasm arm of `smart_call` (section 6) | one `cfg` arm |
| Networking behind cargo features | Only sql-nio and the HTTP client open sockets; observer takes them through its features `mysql` and `http`, which seekdb turns on. The AI function client and the embedding handler (src/sql/engine/expr/ob_expr_ai/ob_ai_func_client.cpp, src/query/vector/ob_vector_embedding_handler.cpp; libcurl today) reach HTTP through a trait in ob-runtime that observer implements. No crate below observer except sql-nio (crate 37, reached only through observer's `mysql` feature) depends on mio, socket2, rustls or ureq | `cargo tree` gate |
| SIMD chosen at compile time | ob-simd picks kernels by `cfg(target_arch)`; NEON is always present on aarch64-apple-darwin, as the C++'s run-time check always finds (section 1). Run-time detection, for x86 later, only inside ob-simd. Each kernel has its scalar version (rule 1.4) | reviewer |
| `panic = "abort"` everywhere | every profile (section 3) | profile gate |

Budget sizing from wasm's 2 GiB heap waits for wasm (Decision 12 notes).

## 9. Crates and C libraries: what is allowed and what is banned

This is RULEBOOK §1. A unit never adds a dependency: manifests are generated, and `use` names only workspace crates and the crates below (R12 §10). A new crate needs a row here (crate, pin, where, why) and a Cargo.lock change committed between batches. The rows are R12 §4's with R10 §4.3's versions (default 26), updated to ARCHITECTURE's choices: standby deferred, libxml2 kept, and wyhash dropped because `varchar_wy_hash` and the `wy_hash_` table entries have no callers.

### 9.1 Areas

| Area | Decision | Why |
|---|---|---|
| Async model | Banned: no async runtime, executor or `async fn`; threads and blocking calls as in the C++, and sql-nio's mio loop | control flow kept; standby deferred (ARCHITECTURE §7.4) |
| Threads | Only ob-runtime's spawner creates engine threads | names, stacks, QoS (ARCHITECTURE §7.2) |
| I/O | Files and devices through ob-runtime's IO layer, aligned buffers from ob-platform | direct IO, budgets |
| Strings and bytes | SQL values are bytes, never `str`/`String` | collations, invalid UTF-8 (ARCHITECTURE §3.1) |
| Number text | Translated formatters and `ob_strtod` only | 770,711 compared lines |
| Errors | `ObError`/`ObResult` only; no anyhow or thiserror | the code must survive (ARCHITECTURE §2) |
| Logging | ob-base's log macros; the `log` crate only as a bridge for third-party crates | log text kept for triage |
| Serialization | explicit little-endian encoders; no serde, bincode or similar for anything persisted or sent | Decision 11's explicit formats |
| Hash maps, sorting | ported `ObHashMap`/`ObHashSet` and sorts (ARCHITECTURE §10); hashbrown with a fixed hasher only for lookups | order reaches output |

### 9.2 Allowed crates

Versions are the newest on crates.io on 2026-09-25 or those in today's Cargo.lock (migration/design/evidence/s8/crates-2026-09-25.txt).

| Crate | Pin | Where, and why |
|---|---|---|
| parking_lot (lock_api, parking_lot_core) | 0.12.5 (0.4.14, 0.9.12), no `send_guard` | ob-base `sync`: timed locks, one-byte `Mutex`, no poisoning |
| crossbeam-utils; crossbeam-channel; crossbeam-queue | 0.8.23; 0.5.17; 0.3.14 | `sync` (`CachePadded`, `ShardedLock`); queues in ob-runtime and above |
| arc-swap; crossbeam-epoch | 1.9.2; 0.9.21 | published snapshots; reclamation in ob-epoch only |
| stacker (psm); loom | 0.1.25 (0.1.32); 0.7.2 | ob-base's stack module; dev-dependency of crates with lock-free code |
| bumpalo; allocator-api2 | 3.20.3 with `allocator-api2`; 0.2.21 | arenas (ARCHITECTURE §3.1); fallible collections at budget owners. bumpalo 3.20 and hashbrown 0.17 need allocator-api2 0.2.x, not the newest 0.4.0 |
| hashbrown | 0.17.1, `default-features = false`, with `allocator-api2`, `inline-more` | lookup-only maps; `default-hasher` is foldhash's random seed (src/hasher.rs:1-17), and with it off the type demands a hasher, ob-base's murmurhash64A with seed 0 |
| seekdb-jemalloc-sys | =0.2.2 (jemalloc 5.3.1), `stats` | ob-platform, behind its `jemalloc` feature, which only the seekdb crate turns on: the reference's allocator (deps/external/Cargo.toml:10) |
| libc; windows-sys; nix | 0.2.189; 0.61.2; 0.31.3 | named crates; nix only in the seekdb crate (signal mask, `raise`) |
| flate2, miniz_oxide; mio, socket2, slab | =1.1.10 default features, =0.9.1; 1.2.3, 0.6.5, 0.4.12 | sql-nio, as today |
| rustls (ring), rustls-pki-types, x509-parser | 0.23.45 with std, ring, tls12 (ring 0.17.14); 1.15.1; 0.18.1 | sql-nio, and rustls under ureq; no aws-lc-rs |
| crc32fast; crc32c | 1.5.2; 0.6.8 | `CRC32()`, zlib's CRC-32 (`CRC32('123456789')` = 3421780262); ob-base's `ob_crc64`, the C++ register without inversion as `!crc32c_append(!init, data)` (assumption until known-answer vectors pass) |
| xxhash-rust | 0.8.18, `xxh64` | storage encoding and log entry headers; vendored xxhash becomes a crate (ARCHITECTURE §1.4) |
| md4, md-5, sha1, sha2; sm3 | 0.11.0; 0.5.0 | digest functions, all on digest 0.11 |
| aes, sm4, des, ecb, cbc, cfb-mode, ofb, ctr, aes-gcm | 0.9.3, 0.6.0, 0.9.0, 0.2.1, 0.2.1, 0.9.1, 0.7.1, 0.10.1, 0.11.1 | cipher functions, all on cipher 0.5; key folding, padding and IVs ported from src/share/ob_encryption_util.cpp |
| ureq | 3.4.2, rustls with ring, no `json` | observer's HTTP client behind `http`; blocking |
| fancy-regex | 0.19.2 | JSON schema `pattern` only, each site with a `TODO(port)` on the dialect difference from libc++'s ECMAScript regex |
| rusqlite (libsqlite3-sys) | 0.40.2, `bundled` (0.38.2) | ob-runtime's SQLite pool, which opens meta.db (ARCHITECTURE §12 item 6; the pool's files move there by a map row, §1.2 fix 1) |
| roaring | 0.11.5 | vector-index code, only once known-answer vectors match CRoaring 3.0.0's serialized bytes; until then those users call CRoaring |
| log | 0.4 (0.4.34 locked) | ob-base's bridge for third-party crates' logs |
| cc; cbindgen; cmake | 1.4.7; =0.29.4; 0.1.58 | build scripts with 7.5's flags; island headers obgeo.h, obvsag.h (ARCHITECTURE §9.3); an island build only if cc cannot express it |
| bindgen-cli | 0.73.2, a tool, not a dependency | run once for the parser's headers, output checked in (ARCHITECTURE §9.2) |

### 9.3 C libraries in the product

As ARCHITECTURE §13 lists them, each from where the reference takes it: zlib 1.2.13 (deps/3rd libz.a: `COMPRESS()`, OUTFILE GZIP and DEFLATE bytes), zstd 1.3.8 (the vendored src/oblib/lib/compress/zstd_1_3_8, built by cc: OUTFILE ZSTD and table blocks), SQLite (bundled by libsqlite3-sys), libxml2 2.10.4 (deps/3rd libxml2.a), ICU 69.1 (deps/3rd libicuuc.a, libicui18n.a, libicustubdata.a), S2's libcrypto (deps/3rd libcrypto.a), ring and jemalloc 5.3.1; plus the island archives: libs2.a, and libvsag_static.a with its companions, among them libroaring.a (CRoaring 3.0.0), whose C API libvsag_static.a calls (ARCHITECTURE §13). The zlib, zstd, libxml2 and ICU bindings live in ob-clib-sys (ARCHITECTURE §1.1).

### 9.4 Banned

- Async: tokio, async-std, smol, futures executors, async-trait.
- Serialization of anything persisted or sent: serde, bincode, rkyv, postcard (serde remains only as a build-time dependency, through cbindgen).
- Parsers and regex engines for SQL: nom, pest, lalrpop, logos, chumsky, peg, the regex crate (nom remains only under x509-parser).
- Numbers and number text: ryu, itoa, dtoa, lexical, fast-float, num-format, rust_decimal, bigdecimal, num-bigint.
- Hashing in another order: std `RandomState`, ahash, foldhash (hashbrown's `default-hasher`), fxhash, dashmap, scc.
- Other compression: zstd and zstd-sys (they bundle 1.5.7), libz-sys, zlib-rs, and flate2's `zlib`, `zlib-ng` and `zlib-rs` features, which cargo would unify onto sql-nio, moving it off miniz_oxide.
- Allocators: tikv-jemallocator, jemallocator, mimalloc.
- Concurrency: rayon and other thread pools, portable-atomic, `AtomicCell<u128>`, crossbeam-skiplist (the memtable B-tree keeps its C++ shape, ARCHITECTURE §6.1), parking_lot's `send_guard`.
- Covered by std or by ported code: lazy_static, once_cell, bitflags, chrono, time, encoding_rs, rand.

## 10. What each gate checks

1. The `unsafe` count per crate, zero outside the named crates (ARCHITECTURE §8).
2. `cargo clippy --workspace --all-targets --locked --offline` with 7.2's lints and 7.4's bans, through the build daemon.
3. A grep of rust/ for `mul_add`, `algebraic_`, `vfma`, `vfms`, `_fmadd_`, `_fmsub_`, `_fnmadd_` and `_fnmsub_` finds nothing.
4. Each `Ordering::Relaxed`, `Acquire`, `Release` or `AcqRel` in rust/ outside rust/sql-nio maps to one of the 29 inventory rows; sql-nio's own orderings (78 `Acquire`, 41 `Release`, 8 `AcqRel` today, `grep -rnoE 'Ordering::(Acquire|Release|AcqRel)' rust/sql-nio/src`) are its own and are listed by the same count, so a new one shows.
5. `cargo tree -e features`: no async runtime; hashbrown without `default-hasher`; flate2 with `rust_backend` only; parking_lot without `send_guard`; no networking crate below observer except under sql-nio; seekdb-jemalloc-sys only under ob-platform's `jemalloc` feature, turned on only by seekdb.
6. The profile tables equal section 3.
7. The island flags file regenerated from compile_commands.json equals the checked-in one.
8. 7.6's nightly grep.
9. `rustfmt --edition 2024 --check` over changed files: a parse error or a formatting difference fails (7.1).
10. From Step 5, the weekly `checked` run's panics, each with an inventory row.
11. From the core build's exit, the weekly `cargo check --target x86_64-unknown-linux-gnu` of section 3, diagnostic only.

## 11. Defaults this section depends on

From ARCHITECTURE §17: 3 (the extra `unsafe` in ob-platform and ob-clib-sys, which also needs a decisions.md row), 4 (9.3's C libraries), 21 (the `judge` and `checked` profiles; test-only C++), 22 (arm64 numbers everywhere, behind rules 1.1, 1.4, 2.3 and 2.5), 23 (`stack_size` a lower bound; loom only), 26 (section 9's rows), 36 (family 1 on the `release` binary at the final gate, section 3), 37 (integer division through the arm64 helpers, rule 2.3) and 40 (the weekly check of another target, section 3). Rejecting 22 changes rules 1.1, 1.4 and 2.3 for the platform concerned, not on the Mac.

## 12. How the new counts were made

`grep -rn` over src with `--include` of .h, .cpp, .ipp, .cc and .c; outputs in migration/design/evidence/s8/.
- `-E 'ATOMIC_(LOAD_ACQ|LOAD_RLX|STORE_REL|STORE_RLX|FAA_AR|AAF_AR|VCAS_AR|BCAS_AR|CMP_AND_EXCHANGE)[ ]*\('` less ob_atomic.h: 29. `-E 'memory_order_(relaxed|acquire|release|acq_rel|consume)'` less zstd_1_3_8: 75 lines, 17 files.
- `'SMART_CALL('` less the two `#define` lines: 1,130; `'SMART_CALL_LARGE('`: 6. `'[^_a-z]check_stack_overflow()'` less the definition: 16; `-E '[^_a-z]check_stack_overflow\([a-z_]+(, *[A-Za-z_0-9<>]+)?\)'` less declarations: 90.
- `-F`: `OB_ASSERT(` 552, `abort_unless(` 251, `NDEBUG` 113, `OB_OPERATE_OVERFLOW` 273, `OB_DATA_OUT_OF_RANGE` 122; `-P '(?<![A-Za-z_])assert\s*\('` less zstd_1_3_8: 52; `'__builtin_[a-z]*_overflow'`: 8 lines, 5 files.
- Compile flags: Python with `shlex` over compile_commands.json. CRoaring: `nm -g` on libvsag_static.a, and `'roaring_bitmap_\|roaring64_bitmap_\|roaring::'` over .h and .cpp. Crate versions: the crates.io API on 2026-09-25 and rust/Cargo.lock.

## 13. Objections to ARCHITECTURE.md, and how each was settled

Each objection is followed by its settlement, recorded in RESOLUTIONS.md as s8-N.

1. **§11's "the 29 the C++ names weaker" misses 75 lines** in 17 files that pass explicit weaker `std::memory_order_*` to `std::atomic` (for example src/oblib/lib/allocator/ob_malloc.h:192-211, src/share/config/ob_server_config.cpp:264-310). Rule 4.2 makes them `SeqCst` as ARCHITECTURE words it, which is correct and at worst slower; if Step 2a's sysbench shows a cost, they should join the 29.

   **Settled:** accepted. ARCHITECTURE §11 now names the 29 `ATOMIC_*` calls and the 75 lines, with the Step 2a condition.
2. **§10's ban list lacks the `algebraic_*` methods,** stable since 1.98.0 (core/src/num/f64.rs:1677-1724), which permit the fusing and reordering "No FMA" forbids. Rules 1.1 and 7.4 ban them.

   **Settled:** accepted. ARCHITECTURE §10 now bans `algebraic_*` next to `mul_add`.
3. **§13's flag list is incomplete.** It omits `-DNDEBUG`, `-fmax-type-align=8`, `-fno-omit-frame-pointer`, `-mtune=generic`, `-g`, `-std=gnu++20`, `-mmacosx-version-min=27.0` and 10 more defines, all on every one of the 364 compile commands. Without `-DNDEBUG` the kept C and C++ would run their `assert`s and `OB_ASSERT`'s checking branch (ob_macro_utils.h:707-719). Section 7.5 takes the flags from compile_commands.json.

   **Settled:** accepted, with s7-islands-unsafe.md's objection 10. ARCHITECTURE §13 now lists the shared flags and takes the rest from the checked-in flags file.
4. **§10's "`OB_ASSERT(x)` evaluates `x` under `debug_assert!`" invites the wrong translation:** `debug_assert!(x)` skips `x` when assertions are off, while the reference's `OB_ASSERT(x)` is `(void)(x)` (ob_macro_utils.h:707-708). Section 3 evaluates first, then asserts.

   **Settled:** accepted. ARCHITECTURE §10 now says `ob_assert!(x)` evaluates `x` in every profile, then applies `debug_assert!` to the value.
5. **§13's loop denies leave a hole.** The template's patterns match a command's start (`Bash(cargo build:*)`), so `cargo +1.98.1 check`, the form PLAN §10 action 14 uses (PLAN.md:888), passes, as do `cargo rustc` and `rustc`. Section 7.6 adds them.

   **Settled:** accepted. ARCHITECTURE §13's loop denies now include `cargo rustc`, `cargo +<toolchain>` and `rustc`.
6. **§13's C-library list omits CRoaring,** which libvsag_static.a calls (`nm -g` lists undefined `_roaring_bitmap_*`), first-party C++ calls on 129 lines (103 in src/observer/vector_index/ob_plugin_vector_index_adaptor.cpp), and §§7.1 and 9.4 already assume. Section 9.3 lists it with the island archives.

   **Settled:** accepted. ARCHITECTURE §13 now lists libroaring.a with the island archives.
7. **§1.4 and §13 disagree on xxhash:** §1.4 keeps PLAN §3's "vendored ... xxhash ... become crates", while §13 accepts R12 §4's "port ... XXH64 into ob-base". Section 9.2 uses a crate, which also meets R12's condition for crates (a published algorithm checked by known-answer vectors).

   **Settled:** the crate. ARCHITECTURE §1.4 and §13 now say the vendored xxhash becomes xxhash-rust; its uses are storage and log formats, which Decision 11 frees, and the uncalled `xx_hash_` family (s5-execution.md rule 7.3).
8. **§11's "total cap" cannot count the thread's own stack,** as the C++ does (ob_smart_call.h:80-81): stacker exposes only `remaining_stack()`, pthread's bounds need `unsafe`, which ob-base forbids, and a library host's threads are not the spawner's. Section 6 counts extension bytes against the 64 MiB; the firing depth is not a contract, so nothing observable changes.

   **Settled:** accepted. ARCHITECTURE §11 now says the cap counts extension bytes only.
