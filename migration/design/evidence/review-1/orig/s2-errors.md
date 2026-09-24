# 2. Errors

This section of the design document holds the rules for the error type, for error codes used as values, and for the codes, messages and SQLSTATEs the client sees. It follows migration/design/ARCHITECTURE.md §2, and §3.2, §8, §9.3, §10, §11 and §14 where they touch errors. §2.15 lists the objections this section raised against ARCHITECTURE.md, each with its settlement (RESOLUTIONS.md s2-N); both files follow the settlements.

- Facts are at the frozen base 834bbee1e; paths are from the repository root.
- "R02" cites migration/design/research/02-errors.md, whose counting scripts are in /Users/colin/.claude/jobs/39d4f781/tmp/design/errors/. "S2" cites this section's read-only scripts in /Users/colin/.claude/jobs/39d4f781/tmp/design/s2-errors/: catalog.py (parses ob_errno.def with gen_errno.pl's regexes), calls.py (every `LOG_USER_*` call against its format) and forms2.py (which functions can take the `?` form).
- "Must" marks a rule. A rule binds translated leaves, the rewritten SQL tier and the hand-designed core alike, unless it names one of them. §2.12 lists what a reviewer checks.

**What the judge compares.** For every failing statement the client sees a number, a SQLSTATE and a message: the 272 configured cases hold 2,675 `--error` directives and 2,700 `ERROR <SQLSTATE>: <message>` lines, plus 322 SHOW WARNINGS rows (R02 §2.8). Family 6 compares the output of the `ob_error` tool. No mask covers any of these (Decision 6). The Rust error type itself is not observable. What is observable is which code reaches the client and which text comes with it, so the rules keep, site by site, every code the C++ produces, tests and passes on, and they keep the C++ path from `LOG_USER_ERROR` to the error packet.

## 2.1 The error type

ob-errno (crate 1 of ARCHITECTURE §1.1) holds the type, the generated catalog (§2.8), the C formatter (§2.9) and the two assignment macros of §2.3. It depends on nothing, so every crate can use it.

```rust
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct ObError(i32);                 // never 0; the field is private to ob-errno

pub type ObResult<T = ()> = Result<T, ObError>;

impl ObError {
    pub const fn code(self) -> i32 { self.0 }
    pub const fn from_code(code: i32) -> ObResult {
        if code == 0 { Ok(()) } else { Err(ObError(code)) }
    }
    pub fn is_schema_error(self) -> bool { /* the C++ case list */ }   // 24 predicates
}

pub trait ErrCode { fn code(&self) -> i32; }   // ObError, ObResult<T> (0 for Ok), i32

// generated into src/catalog.rs
pub const OB_ITER_END: ObError = ObError(-4008);
pub const OB_ERR_DATA_TOO_LONG_MSG_FMT_V2: ObError = ObError(-5167);
pub const OB_MAX_ERROR_CODE: i32 = 65535;
```

1. A Rust function reports an OB code only through `ObResult`, which is `ObResult<()>`; hand-designed core APIs may return `ObResult<T>`. `Ok(())` is `OB_SUCCESS`. There is no `OB_SUCCESS` constant of type `ObError`, and `ObError` has no `Default`.
2. Each catalog entry is a `pub const` of type `ObError` under its C++ name. That is 1,544 non-zero codes plus the two `DEFINE_OTHER_MSG_FMT` names (src/share/ob_errno.def:250, :612), which share their target's code. Three names lack the `OB_` prefix: `INCORRECT_ARGUMENTS_TO_ESCAPE`, `INCORRECT_ARGUMENTS_TO_URL_DECODE` and `STATIC_ENG_NOT_IMPLEMENT` (S2 catalog.py). The integer constants of the C++ header (`OB_MAX_ERROR_CODE`, `OB_LAST_ERROR_CODE`, `OB_ERR_SQL_START`, `OB_ERR_SQL_END`) are `i32`. Code constants are imported by name like any other item (ARCHITECTURE §14 rule 6).
3. The constants work as `match` patterns because `ObError` derives `PartialEq` and `Eq`.
4. `from_code` accepts any `i32`, because codes outside the catalog occur:
   - init.sql arms tracepoint 311 with `error_code = 4`, which the resolver stores as -4 (tools/deploy/init.sql:26; src/sql/resolver/cmd/ob_alter_system_resolver.cpp:749-755);
   - five values in the parser's C core are not catalog codes (§2.4 rule 8);
   - a PL `SIGNAL` carries the user's positive MySQL number (src/pl/ob_pl_interpreter.cpp:123-134);
   - `ObPxTask::TASK_DEFAULT_RET_VALUE` is 1 (src/sql/engine/px/ob_dfo.h:908).
5. The error is only the code: no message, location or backtrace. Messages live in the warning buffer (§2.6).
6. The 24 predicates of src/share/ob_define.h:71-265 become methods on `ObError`, with the same names and code lists. They run from `is_schema_error` to `is_query_killed_return` (command: `awk 'NR>=71 && NR<=265 && /^inline bool is_/' src/share/ob_define.h | wc -l`).
   - A caller holding a result writes `ret.is_err_and(ObError::is_schema_error)`. It is false for `Ok(())`, as the C++ predicate is for 0.
   - Predicates defined elsewhere, such as `ObIDDLTask::is_retry` (src/share/ob_ddl_task_executor.h:45, :87), stay in their own files and take `ObError`.
7. `ObError` implements neither `std::error::Error` nor `Display`, and it has no `From` conversion to or from any other type. So `?` cannot turn it into `Box<dyn Error>` or `anyhow::Error`, and no second error type can grow around it. Its `Debug` prints the bare number (`-4008`), as the C++ `%d` does.
8. ob-base's hashing traits hash `ObError` exactly as its `i32`, so a map keyed by codes, such as `ObQueryRetryCtrl::map_` (src/sql/ob_query_retry_ctrl.cpp:33, :795-810), keeps the C++ bucket order (ARCHITECTURE §10).
9. `unwrap` and `expect` on an `ObResult` are banned in engine crates. `panic = "abort"` would turn into a crash what the C++ returns as a code.

## 2.2 Functions, parameters and fields that carry codes

1. **Return types.** A C++ function whose `int` return is an OB code returns `ObResult` in Rust. Such a function returns `ret`, a code constant or another such function's result. Any other `int` return stays `i32`.
2. **Output parameters.**
   - Parameters keep the C++ order, and output parameters stay `&mut` parameters. A value the callee wrote before it failed then stays visible, as in the C++. For example, `RowDesc::get_idx` sets `idx` before the lookup (§2.4, example 4). This is ARCHITECTURE §4.1 rule 4 (`ObRawExpr*&` becomes `&mut ExprId`) applied to every type.
   - Only a hand-designed core API may return `ObResult<T>`. A caller in the local-`ret` form stores the value with `map`: `if ob_fail!(ret = self.child_.get_next_batch(frame, max_rows).map(|b| brs = b)) {`.
3. **Calls that can no longer fail.** Some calls failed in C++ only on a general allocation failure, which aborts under Decision 12 (ARCHITECTURE §3.2). Their Rust counterparts cannot fail. Such a call is a plain statement at the same place, and the C++ branch that handled its failure goes. The Rust signature of the callee decides: the core documents it, and the translator follows it.
4. **Ignored codes.** A code the C++ drops without a test, as in `(void)f();` or in `f();` with the `int` ignored, becomes `let _ = f();`.
5. **Variables and fields that hold codes.**
   - A local, parameter or field that holds only `OB_SUCCESS` or codes is an `ObResult`. Examples: `ret`, `tmp_ret`, `hash_ret`, `ret_`, `error_ret_`, and the `warning` of the casts (src/sql/engine/expr/ob_datum_cast.cpp:158-171).
   - It stays `i32`, under its C++ name, when it also holds values that are not codes, or when it is data that is sent or stored. Examples:
     - `WarningItem::code_`, where 65535 means "no code" (src/oblib/lib/oblog/ob_warning_buffer.h:47, :64);
     - PX's `first_error_code_`, compared with `TASK_DEFAULT_RET_VALUE` (src/sql/engine/px/ob_px_util.h:905-968);
     - MySQL numbers, codes in rows and packets, and tracepoint settings.
   - The inventory row of each field says which of the two it is. Conversions use `ObError::from_code` and `code()`.
6. **Codes shared between threads.** Such a code is an `AtomicI32` holding the raw value (ARCHITECTURE §11), for example `ObIDag::dag_ret_` (src/data_plane/api/data_plane/scheduler/ob_dag_scheduler.h:572). Reads go through `ObError::from_code`.
7. **Constructors.** A constructor that replaces `init()` (ARCHITECTURE §7.1) returns `ObResult<Self>` if the C++ `init()` can fail with a code other than `OB_INIT_TWICE`, `OB_NOT_INIT` or a general allocation failure, and `Self` otherwise.
   - Once the type has no uninitialized state, the `OB_INIT_TWICE` check and the callers' `IS_NOT_INIT` checks have no counterpart.
   - Where a caller tests the not-initialized state as an outcome, the state and its code stay. 13 lines test `OB_NOT_INIT` that way (`git grep -nP '(OB_NOT_INIT\s*[!=]=|[!=]=\s*OB_NOT_INIT\b)' -- src`, generated files excluded). For example, a hash map that was never created answers `get_refactored` with `OB_NOT_INIT` (src/oblib/lib/hash/ob_hashtable.h:1133-1135), and src/sql/rewrite/ob_expr_range_converter.cpp:2038 treats that as "not found".

## 2.3 The `?` form and the local-`ret` form

39,576 function bodies declare `int ret = OB_SUCCESS` or `INIT_SUCC(ret)` (S2 forms2.py, which takes the innermost block around each declaration, nearly always the function body). Each function gets exactly one of two Rust forms.

**The `?` form** is allowed only when, from every point where `ret` can first become an error, nothing else runs before the function returns: no statement other than a log call in the failing branch itself and the final `return ret`.
- These do not count, because the C++ skips them after an error: `else` branches of the failing test, loops whose condition starts with `OB_SUCC(ret) &&`, and `if (OB_SUCC(ret))` blocks.
- Then `if (OB_FAIL(x)) {}` becomes `x?;`.
- A failing branch that logs becomes `x.inspect_err(|&ret| log_warn!(ret, ...))?;`.
- A branch that sets a code becomes its log, if it has one, followed by `return Err(OB_X);`.
- `return ret;` becomes `Ok(())`.

**The local-`ret` form** is required in every other function: one that has a code test on `ret`, a reset, a `tmp_ret` merge, a check macro (`CK`, `OX`, `OZ`, `OV`), an `if (OB_FAIL(ret))` block with a body, an unguarded statement after a failing call, or a guard or destructor that reads `ret`.
- The function declares `let mut ret: ObResult = Ok(());`, keeps every C++ statement in the C++ order, and ends with `ret`.
- It contains no `?`, so its control flow is the C++'s, line for line.

A rough scan finds one of these constructs in 7,764 of the 39,576 bodies (S2 forms2.py; the kinds overlap):

| Construct | Bodies |
|---|---|
| a code test on `ret` | 3,807 |
| a reset | 2,225 |
| a check macro | 2,091 |
| an `if (OB_FAIL(ret))` block with a body | 1,866 |
| `tmp_ret` | 975 |

The scan cannot see unguarded statements, so the translator decides by reading. The local-`ret` form is always correct; the `?` form is only a shorter way to write the same function.

**One Rust form per C++ construct:**

| C++ | Rust |
|---|---|
| `int ret = OB_SUCCESS;`, `INIT_SUCC(ret)` | `let mut ret: ObResult = Ok(());` |
| `if (OB_FAIL(x)) {A} else if (OB_FAIL(y)) {B} else {C}` | `if ob_fail!(ret = x) {A} else if ob_fail!(ret = y) {B} else {C}` |
| `OB_SUCC(x)` with a call in it | `ob_succ!(ret = x)` |
| `OB_SUCC(ret)`, `OB_SUCCESS == ret`; `OB_FAIL(ret)`, `OB_SUCCESS != ret` | `ret.is_ok()`; `ret.is_err()` |
| `OB_SUCC(ret) && c` in a loop condition | `ret.is_ok() && c` |
| `ret = OB_X;`, `ret = OB_SUCCESS;`, `return OB_X;` | `ret = Err(OB_X);`, `ret = Ok(());`, `return Err(OB_X);` |
| `OB_X == ret`, `OB_X != ret` | `matches!(ret, Err(OB_X))`, `!matches!(ret, Err(OB_X))` |
| `OB_X == ret \|\| OB_Y == ret` | `matches!(ret, Err(OB_X \| OB_Y))` |
| an `if`/`else if` chain or a `switch` over codes | `match ret { Err(OB_X) => .., Err(OB_Y) => .., _ => .. }` |
| `ret == code_var`, when the code is held in a variable | `ret == Err(code_var)` |
| `is_x_err(ret)` (a predicate) | `ret.is_err_and(ObError::is_x_err)` |
| `OB_TMP_FAIL(x)`, `OB_SUCCESS != (tmp_ret = x)` | `ob_fail!(tmp_ret = x)` |
| `ret = COVER_SUCC(t)`; `v = (a == OB_SUCCESS) ? b : a` in any spelling | `ret = ret.and(t)`; `v = a.and(b)` |
| any other merge, such as `ret = tmp_ret;` | written as the C++ writes it |
| `CK(a, b)`, `OX(s)`, `OZ(f(), k)`, `OV(c, OB_X, k)`, `OZX1`, `OZX2` | `ck!(ret, a, b)`, `ox!(ret, s)`, `oz!(ret = f(), k)`, `ov!(ret, c, OB_X, k)`, `ozx1!`, `ozx2!` |
| `FALSE_IT(s)` | `false_it!(s)` |
| `SMART_CALL(f(x))` | `smart_call!(f(x))`, which returns `ObResult` (ARCHITECTURE §11) |
| `return ret;` | `ret` |

- `ob_fail!` and `ob_succ!` are in ob-errno: `macro_rules! ob_fail { ($r:ident = $e:expr) => {{ $r = $e; $r.is_err() }} }`, and `ob_succ!` ends in `$r.is_ok()`.
- The variable is named at the call because a `macro_rules!` macro cannot see the caller's `ret`.
- The check macros are `macro_rules!` in ob-base (ARCHITECTURE §14 rule 3). Their expansions are exactly the rows above. For example, `ck!(ret, a, b)` tests `a`, then `b`, only while `ret` is `Ok`; on the first false condition it sets `Err(OB_ERR_UNEXPECTED)` if `ret` is `Ok` and logs "invalid arguments" (src/oblib/lib/ob_check_macros.h:55-66).
- A code test uses a pattern when the code is a catalog constant and `==` when it is a variable. `matches!` is the `match` arm of ARCHITECTURE §2 written as an expression.
- Empty branches stay, so a reviewer can put the two sources side by side. Step 2a records which clippy lints fire on these forms, and the workspace allows exactly those.

**Example 1: the `?` form.** src/sql/resolver/ob_resolver_utils.cpp:7215-7227:

```cpp
int ObResolverUtils::append_qualified_name_literal(ObSqlString &sql, const ObString &db_name,
                                                   const ObString &object_name)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(sql.append("CONCAT("))) {
  } else if (OB_FAIL(sql_append_hex_escape_str(db_name, sql))) {
  } else if (OB_FAIL(sql.append(", '.', "))) {
  } else if (OB_FAIL(sql_append_hex_escape_str(object_name, sql))) {
  } else if (OB_FAIL(sql.append(")"))) {
  }
  return ret;
}
```

```rust
impl ObResolverUtils {
    pub fn append_qualified_name_literal(sql: &mut ObSqlString, db_name: &[u8],
                                         object_name: &[u8]) -> ObResult {
        sql.append(b"CONCAT(")?;
        sql_append_hex_escape_str(db_name, sql)?;
        sql.append(b", '.', ")?;
        sql_append_hex_escape_str(object_name, sql)?;
        sql.append(b")")?;
        Ok(())
    }
}
```

If the core's `ObSqlString::append` cannot fail (§2.2 rule 3), those three lines lose their `?`.

**Example 2: two functions that need the local-`ret` form.** src/sql/dtl/ob_dtl_basic_channel.cpp:210-234:

```cpp
int ObDtlBasicChannel::wait_response()
{
  int ret = OB_SUCCESS;
  if (msg_response_.is_in_process()) {
    if (OB_FAIL(msg_response_.wait())) {
    }
    if (OB_HASH_NOT_EXIST == ret) {
      if (is_drain()) {
        ret = OB_SUCCESS;
      } else {
        ret = OB_ERR_SIGNALED_IN_PARALLEL_QUERY_SERVER;
      }
    }
  }
  return ret;
}

int ObDtlBasicChannel::clear_response_block()
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(wait_response())) {
  }
  msg_response_.reset_block();
  return ret;
}
```

```rust
impl ObDtlBasicChannel {
    pub fn wait_response(&mut self) -> ObResult {
        let mut ret: ObResult = Ok(());
        if self.msg_response_.is_in_process() {
            if ob_fail!(ret = self.msg_response_.wait()) {
            }
            if matches!(ret, Err(OB_HASH_NOT_EXIST)) {
                if self.is_drain() {
                    ret = Ok(());
                } else {
                    ret = Err(OB_ERR_SIGNALED_IN_PARALLEL_QUERY_SERVER);
                }
            }
        }
        ret
    }

    pub fn clear_response_block(&mut self) -> ObResult {
        let mut ret: ObResult = Ok(());
        if ob_fail!(ret = self.wait_response()) {
        }
        self.msg_response_.reset_block();
        ret
    }
}
```

- `wait_response` tests a code after the call, so it needs the local-`ret` form.
- `clear_response_block` has no code test, but `reset_block()` runs after a failed wait. `self.wait_response()?;` would skip it and leave the block set.

**Example 3: the end of an iterator.** src/sql/engine/basic/ob_material_op.cpp:72-87. The operator API itself belongs to ARCHITECTURE §5; the example shows only the error forms.

```cpp
  while (OB_SUCCESS == ret) {
    clear_evaluated_flag();
    if (OB_FAIL(child_->get_next_row())) {
    } else if (OB_FAIL(material_impl_.add_row(child_->get_spec().output_))) {
    }
  }
  if (OB_UNLIKELY(OB_ITER_END != ret)) {
  } else {
    ret = OB_SUCCESS;
    if (OB_FAIL(material_impl_.finish_add_row())) {
    } else {
      is_first_ = false;
    }
  }
  return ret;
```

```rust
    while ret.is_ok() {
        self.clear_evaluated_flag();
        if ob_fail!(ret = self.child_.get_next_row()) {
        } else if ob_fail!(ret = self.material_impl_.add_row(&self.child_.get_spec().output_)) {
        }
    }
    if !matches!(ret, Err(OB_ITER_END)) {
    } else {
        ret = Ok(());
        if ob_fail!(ret = self.material_impl_.finish_add_row()) {
        } else {
            self.is_first_ = false;
        }
    }
    ret
```

## 2.4 Codes used as values

1. **Values stay values.**
   - Where the C++ returns, tests, renames, stores or resets `OB_X`, the Rust does the same with `Err(OB_X)`, at the same place.
   - No site turns a code into an `Option`, a `bool` or an enum during the port. 5,718 lines compare `ret` with 347 distinct codes, and the top ten codes cover only 64.6% of those comparisons; 697 more lines compare other variables (R02 §2.4).
   - About 45% of core and SQL-tier lines run under no configured case (migration/judge/coverage-076eb309b/README.md), so a mistake in reshaping would mostly go unseen.
2. **Core APIs keep the codes of the C++ API they replace,** at their boundary and until parity, whatever they use inside (ARCHITECTURE §2; default 1 in §2.14):
   - `ObHashMap` and `ObHashSet`: `get_refactored`, `set_refactored` and `erase_refactored` answer `OB_HASH_NOT_EXIST` and `OB_HASH_EXIST`, and `OB_NOT_INIT` for a map that was never created (src/oblib/lib/hash/ob_hashtable.h:1130-1135);
   - row and batch iterators: `OB_ITER_END`;
   - `databuff_printf` and the printers: `OB_SIZE_OVERFLOW`;
   - `smart_call!`: `OB_SIZE_OVERFLOW`, and timed locks: each site's code (ARCHITECTURE §11);
   - the budget owners: -4013, -4030, -7603, -9124 and -11049, at their check points (ARCHITECTURE §3.2).

   A new core API with no C++ counterpart chooses its own contract. `Option`-returning versions come after parity, each as a change of its own.
3. **Renames are written as the C++ writes them:** `if matches!(ret, Err(OB_HASH_NOT_EXIST)) { ret = Err(OB_ENTRY_NOT_EXIST); }`. The C++ renames `OB_HASH_NOT_EXIST` to `OB_ENTRY_NOT_EXIST` on 48 lines and follows an `OB_ITER_END` test with another code on 90 lines (R02 §2.4).
4. **Merges.** `tmp_ret` is its own `ObResult` local. A merge whose C++ means "`a` unless `a` is success, then `b`" becomes `a.and(b)`. This covers `COVER_SUCC` (src/oblib/lib/utility/ob_macro_utils.h:657) and the 111 ternary lines of R02 §2.5, which come in both directions: `ret = OB_SUCC(ret) ? tmp_ret : ret` becomes `ret.and(tmp_ret)`, and `tmp_ret = tmp_ret == OB_SUCCESS ? ret : tmp_ret` becomes `tmp_ret.and(ret)` (`git grep -hP 'ret\s*=\s*[^;]*\?[^;]*tmp_ret' -- src`). Every other merge, including the 204 plain `ret = tmp_ret;` lines, is written as the C++ writes it.
5. **Comparators that keep an error in `int &ret = ret_;`.** 27 of the 39 `int &ret = <x>ret_;` sites are comparators (R02 §2.6; migration/inventory/sweep/ret-alias.tsv).
   - Such a comparator returns `ObResult<bool>`, or `ObResult<Ordering>` where the C++ returns `int`.
   - Its `ret_` member and its "already failed" branch go. So does the address-order fallback of `ObSortOpImpl::Compare` (src/sql/engine/sort/ob_sort_op_impl.cpp:401-408), which existed only to keep libc++ from aborting.
   - It is called by the port's sort, heap or binary-search transcriptions (ARCHITECTURE §10), which return the first `Err` and leave the slice a permutation of its input. The other caller allowed is a direct call that tests the result at once, like the top-N check (ob_sort_op_impl.cpp:434-466, whose caller reads the error at :2420 and :2432).
   - No fallible comparator reaches `slice::sort*`, `binary_search_by` or `BinaryHeap`.
   - The C++ went on sorting with a fixed answer after an error. The Rust stops at once. The code returned is the same first error, and no caller reads the slice after the error; the inventory row of each comparator confirms that.
6. **The other `int &ret = <x>ret_;` sites:**
   - The seven schema `operator=` that record `error_ret_` become `fn assign(&mut self, other: &Self) -> ObResult`. It still writes `error_ret_`, because `is_valid()` reads that field (src/share/schema/ob_dependency_info.h:323).
   - The two hash-map callbacks become closures that return `ObResult`.
   - The two SPI guards hold `int &ret_` pointing at the caller's `ret` (src/sql/ob_spi.h:63, :84). In Rust they hold no reference. Each place that reads or writes the caller's `ret` takes `ret: &mut ObResult`. `~ObPLSPITraceIdGuard` becomes a method the caller calls at each exit of its function, which is in the local-`ret` form.
   - The constructor of `ObSqlArrayExpandGuard` (src/sql/ob_sql_utils.cpp:49-70) stays a constructor that stores its result in `ret_`, because its destructor must still undo a partly built state.
7. **Warnings made from errors.** Casts in the warn-on-fail mode move the code into a `warning` variable and reset `ret`. The same assignments carry over:

```cpp
static OB_INLINE int get_cast_ret(const ObCastMode &cast_mode, int ret, int &warning)
{
  if (OB_UNLIKELY(OB_ERR_UNEXPECTED_TZ_TRANSITION == ret) ||
      OB_UNLIKELY(OB_ERR_UNKNOWN_TIME_ZONE == ret)) {
    ret = OB_INVALID_DATE_VALUE;
  } else if (OB_SUCCESS != ret && CM_IS_WARN_ON_FAIL(cast_mode)) {
    warning = ret;
    ret = OB_SUCCESS;
  }
  return ret;
}
```

```rust
fn get_cast_ret(cast_mode: ObCastMode, mut ret: ObResult, warning: &mut ObResult) -> ObResult {
    if matches!(ret, Err(OB_ERR_UNEXPECTED_TZ_TRANSITION | OB_ERR_UNKNOWN_TIME_ZONE)) {
        ret = Err(OB_INVALID_DATE_VALUE);
    } else if ret.is_err() && cm_is_warn_on_fail(cast_mode) {
        *warning = ret;
        ret = Ok(());
    }
    ret
}
```

8. **Codes from outside the catalog** become `ObError` through `ObError::from_code`, whatever their value, at the place they enter:
   - A tracepoint value: `OB_E(EventTable::X) OB_SUCCESS` (src/oblib/lib/utility/ob_tracepoint.h:127) becomes `ObError::from_code(ob_e!(EventTable::X, 0))`.
   - Island entries return OB codes as `i32` (ARCHITECTURE §9.3 rule 4), and the `-sys` crate converts them at once. The raw integer goes no further than the shim. The geometry island keeps its catch list (src/share/geo/ob_geo_dispatcher.h:1314-1366).
   - The parser's C core keeps 19 hand-copied values (src/sql/parser/parse_define.h:26-44): `OB_PARSER_SUCCESS`, 13 codes equal to catalog codes, and 5 values the catalog lacks: -9644, -9646, -9650, -9605 and -9670 (S2 catalog.py). Only -9644 is set anywhere, in `YYABORT_STRING_LITERAL_TOO_LONG` (src/sql/parser/sql_parser_base.h:95-103), which no grammar rule uses (`git grep -n YYABORT_STRING_LITERAL_TOO_LONG -- src`). If one of them reached a client, it would get number 0, "Unknown Error" and HY000 (§2.10), and the Rust must do the same.
9. **Out-of-memory** follows ARCHITECTURE §3.2.
   - An `OB_ALLOCATE_MEMORY_FAILED` assignment after a failed general allocation has no counterpart. Its branch goes, and the inventory marks it `allocation-guard`.
   - A budget owner or a logical -4013 is a `precondition-guard` and keeps its code at its check point (migration/inventory/sweep/oom-sites.tsv: 30 budget-backed, 30 logical).
   - The 78 lines that compare with -4013 stay as written. They cost nothing where the code can no longer arrive.

**Example 4: a rename.** src/sql/code_generator/ob_column_index_provider.cpp:116-126:

```cpp
int RowDesc::get_idx(const ObRawExpr *raw_expr, int64_t &idx) const
{
  idx = OB_INVALID_INDEX;
  int ret = OB_SUCCESS;
  if (OB_FAIL(expr_idx_map_.get_refactored(reinterpret_cast<int64_t>(raw_expr), idx))) {
    if (OB_HASH_NOT_EXIST == ret) {
      ret = OB_ENTRY_NOT_EXIST;
    }
  }
  return ret;
}
```

```rust
impl RowDesc {
    pub fn get_idx(&self, raw_expr: ExprId, idx: &mut i64) -> ObResult {
        *idx = OB_INVALID_INDEX;
        let mut ret: ObResult = Ok(());
        if ob_fail!(ret = self.expr_idx_map_.get_refactored(&raw_expr, idx)) {
            if matches!(ret, Err(OB_HASH_NOT_EXIST)) {
                ret = Err(OB_ENTRY_NOT_EXIST);
            }
        }
        ret
    }
}
```

- The address key becomes the id (ARCHITECTURE §4.1 rule 3).
- The map keeps `OB_HASH_NOT_EXIST` (rule 2).
- `idx` keeps `OB_INVALID_INDEX` on a miss, because the output parameter stayed `&mut` (§2.2 rule 2).

**Example 5: a code that drives a retry.** `ObCreateViewResolver::print_rebuilt_view_stmt` (src/sql/resolver/ddl/ob_create_view_resolver.cpp:583-620) prints the view into a 64 KiB buffer. On `OB_SIZE_OVERFLOW` it doubles the buffer, up to `OB_MAX_PACKET_LENGTH` (64 MiB; src/oblib/lib/ob_define.h:268, :387), and loops `while (OB_SIZE_OVERFLOW == ret && buf_len < OB_MAX_PACKET_LENGTH)`.
- The Rust keeps the test and the loop condition as `loop { ...; if !(matches!(ret, Err(OB_SIZE_OVERFLOW)) && buf_len < OB_MAX_PACKET_LENGTH) { break; } }`.
- It drops the two `OB_ALLOCATE_MEMORY_FAILED` branches (rule 9).
- It keeps a quirk. The loop condition fails once the buffer reaches 64 MiB, so the 64 MiB buffer is allocated but never printed into, and a view whose text needs more than 32 MiB fails with `OB_SIZE_OVERFLOW`. The site carries a `BUG(port):` line (the rulebook's BUG rule).

## 2.5 Server log lines

1. The C++ `LOG_WARN` and `LOG_ERROR`, and their module variants, fill the `[errcode=N]` field of the log header from the local variable named `ret` (src/oblib/lib/oblog/ob_log_module.h:224-226, :261-266; src/oblib/lib/oblog/ob_log.cpp:747). The Rust macros take that value as their first argument: `log_warn!(ret, "fail to get table schema", K(table_id))`.
   - The argument is whichever `ret` is in scope on the C++ line, even inside a `tmp_ret` branch.
   - In the `?` form it is the closure's `ret`.
   - `*_LOG_RET(level, code, ...)` passes its code (ob_log_module.h:777-778).
   - INFO, TRACE and DEBUG take no code, because they print none.
2. The argument is any `ErrCode`. `K(ret)` prints `ret=-4008`, and `ret=0` for `Ok(())`, because ob-base's log-value trait prints `ObError` and `ObResult<T>` as `code()` (default 3 in §2.14).

## 2.6 The warning buffer and the per-thread slot

`ObWarningBuffer` moves to ob-base's logging module under its C++ name (ARCHITECTURE §14 rules 3 and 7). It keeps the C++ layout and limits (src/oblib/lib/oblog/ob_warning_buffer.h:37-221):

```rust
pub struct WarningItem {
    pub msg_: [u8; 512],        // STR_LEN (:51), NUL-terminated
    pub timestamp_: i64,
    pub log_level_: i32,        // USER_WARN, USER_NOTE, USER_ERROR
    pub line_no_: i32,
    pub code_: i32,             // OB_MAX_ERROR_CODE (65535) when unset (:47, :64)
    pub column_no_: i32,
    pub sql_state_: [u8; 6],
}
pub struct ObWarningBuffer {
    item_: Vec<WarningItem>,    // grows to MAX_BUFFER_SIZE = 64 (:141), then reused as a ring
    err_: WarningItem,
    append_idx_: u32,
    total_warning_count_: u32,
}
```

1. **Methods keep their C++ behavior, quirks included.**
   - `set_error` writes only the message and the code (:117), so a SQLSTATE, line or column set earlier stays.
   - An append into a reused ring slot overwrites the message, code and level. It overwrites the SQLSTATE only when one is given, and leaves the timestamp, line and column (:183-211).
   - `reset` clears the counters and the error slot but not the ring (:157-163).
   - `get_warning_item` reads the ring from `append_idx_` once it has wrapped (:170-181).
   - Messages are cut at 511 bytes, and SQLSTATEs at 5.
2. **Two C++ members go.**
   - `error_ret_` only recorded a failed allocation of a ring slot, which now aborts (Decision 12).
   - The static `is_log_on_` switch is set on by `main` before any request (src/observer/main.cpp:791), so recording is always on.
3. **Where the buffers live.** The session holds `warnings_buf_: Arc<Mutex<ObWarningBuffer>>` and a plain `show_warnings_buf_`, as `ObSQLSessionInfo` holds both today. The mutex is parking_lot through ob-base's `sync` module (ARCHITECTURE §11).
4. **The per-thread slot** is `thread_local! { static G_WARNING_BUFFER: RefCell<Option<Arc<Mutex<ObWarningBuffer>>>> = const { RefCell::new(None) }; }`. It is the one non-`Copy` entry in ARCHITECTURE §11's list.
   - `ob_get_tsi_warning_buffer()` returns a clone of the `Arc`, or `None`, as the C++ returns NULL.
   - `ob_reset_tsi_warning_buffer()` keeps its name and meaning.
5. **The slot changes only through a scope guard.** `WarningBufferScope::install(Option<Arc<..>>)` saves the old value and restores it on drop. `ObWarningBufferIgnoreScope` keeps its name and wraps a fresh buffer. The C++ changes the slot at these sites (git grep over `ob_setup_tsi_warning_buffer`, `ob_setup_default_tsi_warning_buffer` and `ObWarningBufferIgnoreScope`), and each keeps its scope:
   - the request entries, where `ObMPBase::setup_wb` sets the slot to the session's buffer (src/observer/mysql/obmp_base.h:122-128) and the end of the request clears it (obmp_base.cpp:369; obmp_init_db.cpp:150; ob_mysql_end_trans_cb.cpp:169, :220);
   - the worker, request-queue and PX task entries (ob_worker_processor.cpp:110-111; ob_req_queue_thread.cpp:86-87; ob_px_task_process.cpp:100-101). They install a thread's default buffer and reset it, so in Rust they install a fresh buffer per task.
   - the four ignore scopes: ob_load_data_impl.cpp:1188, ob_spi.cpp:4953, ob_dynamic_sampling.cpp:521 and ob_create_package_resolver.cpp:202;
   - the trigger call's private buffer (ob_trigger_handler.cpp:447-463) and the two warning probes of ob_transform_utils.cpp:10088-10096 and :10137-10149.

   A new site needs an inventory row.
6. **Locking.** A guard from `.lock()` lives for one statement or one block that calls nothing that records a user message. The `log_user_*!` macros lock the same mutex, and parking_lot's mutex does not re-enter, so holding a guard across such a call would deadlock. The C++ holds a raw pointer across such calls (src/sql/ob_spi.cpp:3680-3715), and the Rust locks again for each statement.
7. **Other readers of the buffer:**
   - PX workers report their error message, cut at 511 bytes, their code and each warning's code and message through `ObPxUserErrorMsg` (src/sql/engine/px/ob_px_task_process.cpp:454-498; src/sql/engine/px/ob_px_dtl_msg.h:47-57). The coordinator replays them with `forward_user_*!` under `ObPxErrorUtil`'s first-error rule (src/sql/engine/px/ob_px_util.h:905-968). These pieces are translated as written, so forwarded warnings lose their SQLSTATE, line and column, as they do today.
   - PL handler matching (src/pl/ob_pl_interpreter.cpp:123-134), `GET DIAGNOSTICS` (src/sql/engine/cmd/ob_get_diagnostics_executor.cpp:143-219) and `SQLCODE`/`SQLERRM` (src/sql/engine/expr/ob_expr_pl_sqlcode_sqlerrm.cpp:121-153) read the buffer and the catalog, and are translated as written.

**Example 6: the trigger's private buffer.** src/sql/engine/dml/ob_trigger_handler.cpp:447-463 becomes:

```rust
let trigger_diagnostics = Arc::new(Mutex::new(ObWarningBuffer::new()));
let outer_diagnostics = ob_get_tsi_warning_buffer();
{
    let _wb = WarningBufferScope::install(Some(trigger_diagnostics.clone()));
    oz!(ret = pl_engine.execute(exec_ctx, &tmp_allocator, trigger_id, routine_id, &path, params, result),
        trigger_id, routine_id, params);
}
if ret.is_err() && !matches!(ret, Err(OB_ERR_FUNCTION_UNKNOWN)) {
    if let Some(outer) = &outer_diagnostics {
        let t = trigger_diagnostics.lock();
        if OB_MAX_ERROR_CODE != t.get_err_code() || !t.get_err_msg().is_empty() {
            let mut o = outer.lock();
            o.set_error(t.get_err_msg(), t.get_err_code());
            o.set_error_line_column(t.get_error_line(), t.get_error_column());
            o.set_sql_state(t.get_sql_state());
        }
    }
}
```

## 2.7 Recording user messages

The writers are `macro_rules!` in ob-base (ARCHITECTURE §14 rule 7). There are 2,942 calls outside comments and macro definitions: 2,798 `LOG_USER_ERROR`, 126 `LOG_USER_WARN`, 7 `LOG_USER_NOTE` and 11 `LOG_MYSQL_USER_*` (S2 calls.py).

1. **`log_user_error!(OB_X, args...)`** formats `OB_X`'s user message with the typed argument list of §2.9. It records the result in the thread's buffer as `ObLogger::log_user_message` does (src/oblib/lib/oblog/ob_log.cpp:654-666, :1164-1182), writes the WARN log line of `_LOG_USER_MSG` (src/oblib/lib/oblog/ob_log_module.h:1033-1039), and evaluates to `OB_X`.
   - The code must be a catalog name, as the C++ requires a constant (ob_log_module.h:1046-1052). The macro takes an identifier, so a variable cannot be passed.
   - `log_user_warn!` and `log_user_note!` append and evaluate to `()`.
   - The `LOG_MYSQL_USER_*` macros are the same as `LOG_USER_*` (ob_log_module.h:1055-1078), so they map to the same Rust macros.
2. **What gets recorded.** The message is formatted as `vsnprintf` into 512 bytes and then copied with `snprintf("%s")`. The stored message is therefore the formatted bytes up to the first NUL, at most 511 of them. An entry is recorded only when the formatted length before the cut is above 0 (ob_log.cpp:1167). An empty message leaves the error slot as it was, code included. Nothing is recorded when the slot is empty.
3. **Pairing with `ret`.** When the C++ assigns `ret = OB_X` right next to `LOG_USER_ERROR(OB_X, ...)`, the two become one statement: `ret = Err(log_user_error!(OB_X, ...));`, or `return Err(log_user_error!(OB_X, ...));` in the `?` form. 2,630 of the 2,786 calls set or test the same code within 4 lines (R02 §2.3). The other calls stay separate statements, as the C++ writes them.
4. **Forwarding.** `forward_user_error!`, `forward_user_warn!` and `forward_user_note!` take a code, which may be a variable, and a message that is already formatted (ob_log_module.h:1098-1113). The message is copied with the rule 2 cut. `forward_user_error_msg!(code, fmt, args)` serves the three call sites that pass their own format (src/observer/ai_service/ob_ai_service_executor.cpp:102, :156; src/sql/resolver/ob_resolver_utils.cpp:6168). It formats with the same C formatter and checks argument types at run time. `log_user_error_line_column!` sets the error slot's line and column (ob_log.cpp:1184-1205).
5. **A wrong argument count.** A call whose arguments do not match its format does not compile. The translator writes what C's `vsnprintf` actually read and marks the site `BUG(port):`. Two calls pass an argument the format never reads. `LOG_USER_ERROR(OB_SCHEMA_ERROR, col_def_msg)` at src/sql/resolver/ob_resolver_utils.cpp:7344 and :7357 records "Schema error" (src/share/ob_errno.def:99). In Rust both become `log_user_error!(OB_SCHEMA_ERROR)` (S2 calls.py).

**Example 7.** src/sql/resolver/ddl/ob_create_table_resolver.cpp:1268-1271:

```cpp
      ret = OB_ERR_BAD_FIELD_ERROR;
      LOG_USER_ERROR(OB_ERR_BAD_FIELD_ERROR,
          sort_column.column_name_.length(), sort_column.column_name_.ptr(),
          table_name_.length(), table_name_.ptr());
```

```rust
      ret = Err(log_user_error!(OB_ERR_BAD_FIELD_ERROR, &sort_column.column_name_, &self.table_name_));
```

The format is `"Unknown column '%.*s' in '%.*s'"` (src/share/ob_errno.def:660), so each `%.*s` takes one byte slice where C takes a length and a pointer.

## 2.8 The catalog generator

One generator replaces gen_errno.pl: rust/ob-errno/gen/gen_errno.py, in Python 3 like the migration's other scripts. It reads src/share/ob_errno.def and src/share/mysql_errno.h unchanged. Its output is checked in, and a gate regenerates it and diffs (ARCHITECTURE §1.4; default 4 in §2.14). tools/ob_error/src/gen_os_errno.pl gains a Rust back end for os_errno.def in the same way.

**Parsing, with every oddity kept:**
- The generator matches entries with gen_errno.pl's prefix regexes (src/share/gen_errno.pl:35-75). So line 575, which ends in `;;`, and line 702, which has no closing `)`, parse as they do today.
- It keys entries by name. The entry defined twice, `OB_ERR_ARGUMENT_SHOULD_CONSTANT_OR_GROUP_EXPR` (:1868, :1872), yields one entry: 1,545 names and 1,544 non-zero codes.
- It rejects a duplicated code, as gen_errno.pl does (:80-90).
- An identifier in the MySQL-number field resolves against mysql_errno.h first: 500 entries name an `ER_*` constant, two name `WARN_DATA_TRUNCATED` and one `WARN_OPTION_BELOW_LIMIT` (S2 catalog.py). Otherwise it resolves against the catalog itself. So `OB_ERR_CANNOT_REVOKE_PRIVILEGES_YOU_DID_NOT_GRANT` stores -5133 (:792), `ob_mysql_errno(-5362)` returns -5133, and the client still sees 5362.
- String literals are decoded as the C compiler decodes them: 261 `\'` and 3 `\\` (`grep -o '\\.' src/share/ob_errno.def | sort | uniq -c`). All other bytes are copied unchanged, such as the no-break space (C2 A0) at :1261. Messages are `&'static [u8]`.
- Cause and solution default to "Internal Error" and "Contact OceanBase Support" (gen_errno.pl:20-21).
- Every SQLSTATE has 5 bytes, and the generator fails if one ever does not (all 78 distinct values do; S2 catalog.py).
- Code 0 gets no table entry, as in gen_errno.pl (:303, :328).

**Output:**

| Output | Contents |
|---|---|
| rust/ob-errno/src/catalog.rs | the constants of §2.1, and a table of name, cause, solution, MySQL number, SQLSTATE, `str_error` and `str_user_error` |
| the lookups, same names as C++ | `ob_error_name`, `ob_error_cause`, `ob_error_solution`, `ob_strerror`, `ob_str_user_error`, `ob_sqlstate`, `ob_mysql_errno`, `ob_mysql_errno_with_check`, `ob_errpkt_errno`, `ob_errpkt_strerror`, `ob_errpkt_str_user_error` (src/share/ob_errno.cpp:15530-15633), taking `i32` |
| rust/ob-errno/src/user_msg.rs | one function per entry and per `DEFINE_OTHER_MSG_FMT` name, named after it, with the typed argument list of its user message (§2.9) |
| the `ob_error` tool's data | a binary target of ob-errno, translated from tools/ob_error/src/ob_error.cpp. It keeps the reverse map built in ascending `-code` order (tools/ob_error/src/ob_error.cpp:489-499) and os_errno.def's table. |
| a value check of the frozen headers | no C header is generated: geo and vsag keep the frozen src/share/ob_errno.h their kept code includes, and the parser's C core has its own values (s7-islands-unsafe.md 7.2 rule 5). A gate checks every constant in the frozen src/oblib/lib/ob_errno.h and src/share/ob_errno.h against ob-errno's. |
| a check of parse_define.h | the 13 values that must equal catalog codes, and the 5 known non-catalog values (§2.4 rule 8). Any change fails the check. |

**The lookups keep today's fallbacks exactly** (ob_errno.cpp:15530-15633). The table below reads "an empty slot" for a code in (-65535, 0) that has no entry.

| Lookup | Catalog code | 0 | Empty slot | Code ≤ -65535 or > 0 |
|---|---|---|---|---|
| `ob_error_name` | name | "OB_SUCCESS" | "Unknown Error" | "Unknown error" |
| `ob_error_cause` | cause | "Not an Error" | "Internal Error" | "Internal Error" |
| `ob_strerror`, `ob_errpkt_strerror` | `str_error` | "Unknown Error" | "Unknown Error" | "Unknown error" |
| `ob_str_user_error` | user message | none | none | none |
| `ob_sqlstate` | SQLSTATE | "HY000" | "HY000" | "HY000" |
| `ob_mysql_errno` | MySQL field, -1 meaning "use the code" | 0 | 0 | -1 |
| `ob_errpkt_errno` | MySQL number, or `-code` | 0 | 0 | `-code` if ≤ -65535; the value itself if > 0 |

`str_error` is never used as a format. 29 entries have `%` conversions in it, such as `OB_ERR_NO_COLUMN_PRIVILEGE` (:815) (S2 catalog.py). The client sees those conversions as literal text whenever the packet falls back to `str_error`, and the Rust keeps that.

## 2.9 C-format messages

The formatter is `ob_errno::cfmt`: a small hand-written printf for the conversions the catalog uses, plus a buffer type that behaves like `vsnprintf(buf, N, ...)`. It keeps the first `N-1` bytes and counts the full length. It is never `format!`, because:
- string precision counts bytes in C and characters in Rust;
- `%.*s` takes a length and a pointer;
- the cut at 511 bytes can split a UTF-8 sequence;
- ARCHITECTURE §10 bans Rust's float formatting for output text.

**Conversions** (468 entries have conversions in their user message, plus the two `DEFINE_OTHER_MSG_FMT` formats; S2 catalog.py):

| C conversion (uses) | Generated parameter | Output |
|---|---|---|
| `%.*s` (343) | `impl CStrArg`, one slice for C's length and pointer | the bytes up to the first NUL, at most the slice's length |
| `%s` (202), `%.192s` (17), `%-.256s` (10), `%.64s` (5), `%.80s` (3), `%.20s` (2), `%-.384s` (1) | `impl CStrArg` | the bytes up to the first NUL, at most the precision; `-` without a width changes nothing |
| `%d` (25), `%u` (30), `%ld` (38), `%lu` (7) | `i32`, `u32`, `i64`, `u64` | decimal |
| `%f` (12), `%lf` (1) | `f64` | fixed with 6 decimals, rounded exactly from the binary value, ties to even; `inf`, `-inf` and `nan` as the reference's libc prints them |
| `%c` (4) | `u8` | one byte, a NUL included |
| `%%` (2) | none | `%` |

- `CStrArg` is implemented for `&[u8]`, `&str`, `&[u8; N]` and `Option<&[u8]>`. `None` stands for a null pointer and prints what the reference's libc prints for one, `(null)` cut to the precision (assumption: macOS libc as FreeBSD's; the check below pins it).
- A C++ call that passes a length shorter than its string passes a shorter slice.
- Where the C++ passes a wider integer than the conversion reads, the Rust casts with `as`, which keeps the low bits (assumption: that is what `vsnprintf` reads from a 64-bit variadic slot on Apple arm64). The reviewer checks each such site.
- `%f` is implemented in ob-errno as an exact big-integer conversion. It cannot use ob-values' translated dtoa, which sits higher in the crate graph. All 13 `%f` uses are in five geometry messages (:1563, :1564, :1580, :1581, :1601).

## 2.10 What reaches the client

**The error packet.** One function in observer builds it as `ObMPPacketSender::send_error_packet` does (src/observer/mysql/obmp_packet_sender.cpp:527-715) and hands it to sql-nio's `encode_error_payload(error_code: u16, sql_state: &[u8; 5], message: &[u8], out: &mut [u8]) -> Option<usize>` (rust/sql-nio/src/response.rs:106-122). The steps:

1. **Message.**
   - It is the thread buffer's error message if the buffer's code equals `err`, or if `err` is `OB_ERR_SIGNAL_EXCEPTION` (:555-559).
   - If that is empty, it is the `errmsg` argument when it is non-empty. Otherwise it is nothing for `OB_ERR_SIGNAL_EXCEPTION`, and `ob_errpkt_strerror(err)` cut at 511 bytes for every other code (:561-569).
2. **Conversion.** With a session, the message is converted from utf8mb4 to `collation_connection`, replacing unknown characters, and cut to 512 bytes. That cut can split a character. Without a session, or if the conversion fails, the message stays as it was (:571-591).
3. **Rich messages.** If `enable_rich_error_msg` is on (default false, src/share/parameter/ob_parameter_seed.ipp:122), a time and a trace id are appended (:594-640).
4. **Number and SQLSTATE.**
   - For `OB_ERR_SIGNAL_EXCEPTION` with a buffer, they are the buffer's code as `u16` and the buffer's SQLSTATE, or `ob_sqlstate(err)` if that is empty (:643-650).
   - Otherwise they are `ob_errpkt_errno(err) as u16` and `ob_sqlstate(err)` (:651-653). `as u16` wraps as `static_cast<uint16_t>` does.
   - A SQLSTATE that is not 5 bytes fails `OMPKError::set_sqlstate` (src/oblib/rpc/obmysql/packet/ompk_error.cpp:46-55). The function then turns the error into `OB_ERR_UNEXPECTED` and closes the connection (:673-676, :698-701). The Rust keeps that check in front of the `[u8; 5]`.
5. **The rest of the function** is kept too: the rollback of an active autocommit transaction, closing the connection when building failed, and resetting the PL error message (:679-708). `err` is an `ObError`, so the C++ branch for `OB_SUCCESS`, which closed the connection (:544-547), cannot occur.

The async end-of-transaction callback, which may run on another thread, passes the session buffer's message as `errmsg` (src/observer/mysql/ob_mysql_end_trans_cb.cpp:254-272). Unless the running thread's own slot holds a message under the final code, that message is sent whatever code recorded it. Keeping the callers as written keeps this.

**SHOW WARNINGS.**
- At the end of each statement, `set_show_warnings_buf(code)` (src/sql/session/ob_sql_session_info.cpp:660-671) runs:
  - on an error with an empty message, it records `ob_errpkt_strerror(code)` under the code;
  - on success, it clears the error slot;
  - then it copies the buffer.
  So a message recorded under another code stays in SHOW WARNINGS even when the packet dropped it (R02 §2.3).
- The virtual table (src/observer/virtual_table/ob_virtual_warning.cpp:81-175):
  - it prints the error row first, when the message is not empty or the code is not 65535;
  - its Code column is `ob_errpkt_errno(code)`, and the original code and the slot's SQLSTATE fill two more columns;
  - inside PL it reads the live buffer instead.

**The syntax error text** is built with `str_error` as an argument: `LOG_USER_ERROR(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX), len, ptr, line)`. `ptr` may be NULL for an empty statement (src/sql/parser/ob_parser.cpp:891-895). Its format is `"%s near '%.*s' at line %d"` (src/share/ob_errno.def:478).

## 2.11 Checks

The checks live in ob-errno's tests. They must pass by the core build's exit (ARCHITECTURE §2); Step 2a runs them already, because its `--error` cases need exact errors. They use test-only C++ (ARCHITECTURE §10, default 21 there):

1. **Lookups.** A harness compiles the frozen src/share/ob_errno.cpp with `-D__ERROR_CODE_PARSER_`, as tools/ob_error/src/CMakeLists.txt does, so it needs no other engine source. It compares all eleven lookups for every value in [-70000, 70000]: every code, 0, empty slots, the 65535 boundary and positive values.
2. **Formats.** For each of the 470 formats, generated arguments go through the harness's `vsnprintf` into 512 bytes, then `snprintf("%s")`, and are compared byte for byte with `cfmt`. The arguments include empty strings, strings holding a NUL, lengths around 511, multi-byte characters across the cut, null pointers, `INT_MIN` and `INT_MAX`, `-0.0`, infinities, NaN, subnormals, 1e308 and exact halves.
3. **Generated code.** Regenerating ob-errno's output and the parse_define.h check leaves no diff, and every value in the frozen src/share/ob_errno.h and src/oblib/lib/ob_errno.h equals ob-errno's.
4. **Family 6** runs the Rust `ob_error` (PLAN §4).
5. **The buffer.** Differential tests replay recorded sequences of set, append, reset and copy against `ObWarningBuffer`, and compare every field.
6. **Code patterns.** An unimported code in a pattern binds a new variable and matches everything. The workspace allows `non_snake_case`, because names stay as in C++ (s8-numerics-platform.md 7.2), so that lint cannot catch it. A Step 4 gate therefore fails on any `unused_variables` or `unreachable_patterns` warning that names an identifier that is a catalog name (`matches!(ret, Err(OB_X))` with `OB_X` unimported leaves the binding unused; a `match` arm binding it makes the later arms unreachable).

## 2.12 What a reviewer checks

1. **The function's form.** The `?` form only where nothing but a log runs after the first error; the local-`ret` form otherwise, with the C++ statements in the C++ order and no `?` inside.
2. **Code tests.** Every C++ code test, rename, reset and merge is present, with the same codes, the same polarity and the same place. No `Option`, `bool`, `ok()`, `unwrap_or*` or `is_ok()` swallows a code the C++ passes on.
3. **Signatures.** Output parameters stay `&mut`, and values written before an error are still written. `let _ =` appears exactly where the C++ ignores a code.
4. **Allocation failures.** A dropped `OB_ALLOCATE_MEMORY_FAILED` branch has an `allocation-guard` row. The budget owners keep their codes.
5. **`int &ret = ret_` sites.** They follow §2.4 rules 5 and 6. No fallible comparator reaches a std sort, search or heap.
6. **Codes from outside the catalog.** Island, tracepoint and user codes enter only through `ObError::from_code`, in the shim or where they enter.
7. **User messages.** Each `log_user_*!` has the C++ code and arguments matching the format, and any dropped argument is marked `BUG(port):`. No user message is built with `format!`.
8. **The warning buffer.** The slot changes only through `WarningBufferScope` or `ObWarningBufferIgnoreScope` at a listed site, and no buffer guard is held across a call.
9. **Log macros.** Each log macro gets the `ret` that is in scope on the C++ line.
10. **Banned items.** There is no `unwrap` or `expect` on an `ObResult`, no second error type, and no conversion of `ObError` into another type.

## 2.13 Inventory rows

The sweep has 5,831 code-comparison rows, 3,016 resets, 3,205 `tmp_ret` rows, 39 `int &ret = <x>ret_;` rows and 183 out-of-memory rows (migration/inventory/sweep/summary.tsv). Each row records:
1. the code or codes;
2. what the code means there: end of data, lookup outcome, buffer too small, retry, becomes a warning, ignored, renamed, or other;
3. for code-holding fields, `ObResult` or `i32` (§2.2 rule 5);
4. for comparators, what consumes them: a sort, a heap, a search or a direct call, and whether any caller reads the slice after an error;
5. for out-of-memory rows, `allocation-guard` or `precondition-guard`.

The Rust form follows from this section, so the row does not repeat it.

## 2.14 Defaults for the developer to confirm

These are ARCHITECTURE §17's defaults 6, 7, 8, 5 and 21, as this section applies them. The section adds none.

1. Codes stay values until parity, and `Option` APIs come after (§2.4 rule 2).
2. The warning buffer is reached through a per-thread slot (§2.6).
3. Server logs keep the `ret=<code>` and `[errcode=N]` text (§2.5).
4. The generated catalog is checked in and diffed at gates (§2.8).
5. The lookup and format checks use test-only C++ (§2.11).

## 2.15 Objections to ARCHITECTURE.md, and how each was settled

This section is written consistent with ARCHITECTURE.md. These were the places where its text was inexact; each is followed by its settlement, recorded in RESOLUTIONS.md as s2-N.

1. **"The 22 predicates of src/share/ob_define.h:71-255"** are 24 predicates at :71-265. `is_query_killed_return` sits at :260 (command in §2.1 rule 6). The decision to make them methods is unchanged.

   **Settled:** ARCHITECTURE §2 now says 24 predicates at :71-265, with the command.
2. **"The five lookups"** are eleven functions in src/share/ob_errno.cpp:15530-15633, each with its own fallback (§2.8). All eleven are generated.

   **Settled:** ARCHITECTURE §2 now says eleven lookups at src/share/ob_errno.cpp:15530-15633.
3. **"A check of the 19 codes copied into src/sql/parser/parse_define.h:26-44"** cannot be a check that the values match the catalog. One of the 19 is `OB_PARSER_SUCCESS`, and 5 of the other 18 are not catalog codes. The check pins the 13 matches and the 5 known outsiders (§2.4 rule 8).

   **Settled:** ARCHITECTURE §2 now describes the check as pinning `OB_PARSER_SUCCESS`, the 13 matches and the 5 outsiders by value.
4. **"Client number 0 for an unknown code"** holds only for codes in (-65535, 0) that have no entry. A code at or below -65535 is sent as `-code`, and a positive code as itself, both cut to 16 bits (src/share/ob_errno.cpp:15614-15625; src/observer/mysql/obmp_packet_sender.cpp:651).

   **Settled:** ARCHITECTURE §2 now lists the three cases.
5. **"SHOW WARNINGS keeps a message recorded under another code while the packet drops it"** is true for the synchronous response only. The async end-of-transaction callback passes the session buffer's message as `errmsg`, which is sent whatever code recorded it unless the running thread's own slot matches the final code (src/observer/mysql/ob_mysql_end_trans_cb.cpp:254-272; src/observer/mysql/obmp_packet_sender.cpp:555-563). This supports the decision to keep messages out of the error.

   **Settled:** ARCHITECTURE §2's "Why" now names the synchronous packet and the asynchronous callback separately.
6. **"Comparators ... are called only by the port's sorts"** is too narrow. The top-N comparator is called once per row and its error is read at once (src/sql/engine/sort/ob_sort_op_impl.cpp:434-466, :2420, :2432). The slice comparator feeds `std::lower_bound` (src/sql/engine/px/ob_slice_calc.cpp:1046-1071, :1095). §2.4 rule 5 allows the port's sort, heap and binary-search functions, and direct calls that test the result at once.

   **Settled:** ARCHITECTURE §2 now allows the same callers.
7. **"One form per construct (R02 §5.2's table)."** Three of that table's rows contradict ARCHITECTURE §2's own rule that `?` is used only in plain chains and otherwise a local `ret` is kept in the C++ order:
   - renaming through `map_err`;
   - resets written as `if let Err(e)`;
   - `CK`, `OX`, `OZ` and `OV` expanded by hand.
   This section writes renames and resets as the C++ writes them, and keeps the check macros as `macro_rules!` whose expansion is those forms (ARCHITECTURE §14 rule 3).

   **Settled:** ARCHITECTURE §2 now points to §2.3's table and names the three R02 rows as not used. s4-sql-front.md's example and rules now use `ck!` and `oz!` too.
