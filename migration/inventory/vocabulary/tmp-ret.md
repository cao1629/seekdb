# tmp-ret: classification vocabulary

How to write one inventory row (the seven columns of templates/inventory.tsv) for each site of migration/inventory/sweep/tmp-ret.tsv. A classifier who reads this file, the rules it cites and the C++ site can decide every value. Facts are at the frozen base 834bbee1e; every count comes from a command, given beside the count or in section 11, 12 or 13.

The file follows the design as design amendment 1 changed it (migration/design/AMENDMENTS.md, A1-1 to A1-11, revised 2026-09-27). A1-2, A1-3 and A1-8 decide what the calls of 14 rows become: a disk block's reference count and an `ObRecursiveMutex` keep their calls and codes, and another lock's explicit unlock is its guard's drop (section 5, the clause on lock and reference-count calls; 6.2). A1-3, A1-4, A1-6 and A1-7 change the wording of 6.10, 6.13, section 5's removed branches and section 7, and no value. Section 14 says which values a script can propose before the fan-out.

## 1. What the list holds

migration/inventory/sweep/summary.tsv, row `tmp-ret`, defines the list: every code line that declares `tmp_ret` or `temp_ret` (also `tmp_ret_code`, `tmp_ret_2`, and `INIT_SUCC(tmp_ret)`), calls `OB_TMP_FAIL`, assigns the variable, or merges it into `ret` (`ret = ... tmp_ret`, `COVER_SUCC`); plus the same continue-and-record idiom through any other variable or code X: the merge, and in the same function the declarations and assignments of X. Lines that only compare or log `tmp_ret` are not rows; the compare lines are rows of ret-compare.tsv.

- 3383 rows in 534 files.
- By the sweep's `construct` column: 1340 declarations, 806 assignments, 631 `OB_TMP_FAIL` calls, 605 merges and 1 line that assigns and merges. 176 rows go through another variable or a constant (their construct says "continue-and-record").
- By unit: 924 rows are in core units (migration/core-manifest.tsv), 2384 in Step 3 units (migration/manifest.tsv), 75 in files that migration/not-translated.tsv lists (36 dead, 9 island, 30 deferred). The 30 `[not built]` rows are all among the dead ones.

## 2. What a row decides

RULEBOOK section 5 gives ret-compare.tsv, reset.tsv and this list one job: what each code means at the site (end of data, lookup outcome, buffer too small, retry, becomes a warning, ignored, renamed), and whether a code-holding field is `ObResult` or `i32` (RULEBOOK 2.1). s2-errors.md 2.13 lists what each row records: the code or codes, what the code means there (the same list, plus "other"), and for code-holding fields `ObResult` or `i32`; its item 4 is for the comparators of ret-alias.tsv, and its item 5 for out-of-memory rows, whose tag a row of this list carries too (section 3, `evidence`). For this list that comes to three decisions, and a row's value carries the one that applies to it:

1. **The Rust type of each variable the list names**: `ObResult` when it only ever holds `OB_SUCCESS` and codes, otherwise its C++ integer type under its C++ name (s2-errors.md 2.2 rule 5). The same rule decides a field that receives the code.
2. **How each merge is written**: `a.and(b)`, or as the C++ writes it (s2-errors.md 2.3's table and 2.4 rule 4).
3. **What the code put into the variable at the row means**: a value the function tests (retry, renamed, end of data, lookup outcome, buffer too small, another code), data kept outside the function, a code passed on to the caller, or a code ignored.

"Becomes a warning" (s2-errors.md 2.4 rule 7) has no value here: no code held in `tmp_ret` or `temp_ret` becomes a warning (both commands of section 11 print nothing). The 7 `warning` variables in the list go the other way: a warning a callee recorded becomes the function's error (src/oblib/common/number/ob_number_v2.cpp:189, :193), so they are DECLARATION and MERGE_AS_WRITTEN rows. A row whose code becomes a warning is UNKNOWN until this file has a value for it.

## 3. The columns

| Column | What goes in it |
|---|---|
| `file`, `symbol` | the sweep row's `file` and `symbol`, unchanged (`#define NAME` included) |
| `source_construct` | `line N: ` and then the sweep row's `construct`, unchanged, tags such as `[not built]` included: `line 601: OB_TMP_FAIL (plan definition)`. The line keeps the merge key (file, symbol, source_construct) unique: without it 361 keys repeat, on 941 rows, while (file, line) never repeats |
| `classification` | one value of section 6 |
| `target_translation` | the Rust the row becomes, in section 4's form plus what the value adds (section 6), with the rules in parentheses, and the clauses of section 5 that apply (core unit, removed branch, callee that cannot fail) |
| `evidence` | first `sweep: tmp-ret.tsv:<L> (data row <L-1>)`, L being the line in tmp-ret.tsv; then what section 6 asks for the value; then the tag `allocation-guard` or `precondition-guard` when the row sets, merges or tests -4013 (RULEBOOK 2.10, the error-recovery rule); when the line is also a row of another sweep list, that row (`ret-compare.tsv:<L>`): the two rows name the same producer and give the same Rust for the line, while their value names may differ, since the lists' values differ (ret-compare.md section 7, item 6); a different producer or a different Rust is a conflict to report |
| `status` | `unknown` exactly when the value is UNKNOWN, else `confirmed` |

**Citations** (from the pilot's evidence defects): every line number names its file unless the same clause already names it; cite the path from src/ whenever `find src -name <name>` prints more than one file (many sql/ headers forward to src/query/api/...); cite the line that holds the construct, not a neighbouring `if`, declaration or comment; give no count that no command produced, and give the command; list no unused local as a writer and no function without a caller as a caller; put design rules in parentheses, so the checker does not read a following line number against the design file. Check every shard, and fix every flag:

```
python3 -B migration/scripts/cite_check.py <shard.tsv>
python3 -B migration/scripts/cite_check.py <shard.tsv> show
```

## 4. The Rust form of the row's statement, for every value

These forms follow RULEBOOK 2.1 and s2-errors.md 2.3. Each value in section 6 says only what it adds. They hold for a variable that holds only codes; NON_CODE_VALUE gives the forms for the others.

| C++ at the row | Rust | Rule |
|---|---|---|
| `int tmp_ret = OB_SUCCESS;`, `INIT_SUCC(tmp_ret)`, `int tmp_ret = 0;` | `let mut tmp_ret: ObResult = Ok(());` | RULEBOOK 2.1, the `int ret` and `tmp_ret` rows |
| `int tmp_ret = OB_X;`, `= f();`, `= ret;` | `let mut tmp_ret: ObResult = Err(OB_X);`, `= f();`, `= ret;` | the same |
| any of the declarations above when nothing assigns the variable after it (as for a C++ `const int`) | `let` without `mut` | the same |
| `int tmp_ret;` | `let mut tmp_ret: ObResult;` (`let` when it is assigned at most once) | the same |
| a parameter `int tmp_ret`, `const int tmp_ret` | `tmp_ret: ObResult`; an `int &` parameter is `&mut ObResult` | s2-errors.md 2.2 rules 2 and 5 |
| `OB_TMP_FAIL(x)`; `OB_SUCCESS != (tmp_ret = x)`; `(tmp_ret = x) != OB_SUCCESS`; `0 != (tmp_ret = x)` | `ob_fail!(tmp_ret = x)` | RULEBOOK 2.1; s2-errors.md 2.3 |
| `OB_SUCCESS == (tmp_ret = x)`; `OB_X == (tmp_ret = x)`; `OB_X != (tmp_ret = x)` | the blocks `{ tmp_ret = x; tmp_ret.is_ok() }`; `{ tmp_ret = x; matches!(tmp_ret, Err(OB_X)) }`; `{ tmp_ret = x; !matches!(tmp_ret, Err(OB_X)) }` | RULEBOOK 2.1, the row on a test on an assignment |
| `OB_FAIL(tmp_ret = x)`, which also assigns `ret` (src/oblib/lib/utility/ob_macro_utils.h:655) | `{ tmp_ret = x; ob_fail!(ret = tmp_ret) }` | the same row, keeping the macro's own assignment of `ret` |
| `OB_UNLIKELY(tmp_ret = x)`, the value used as a condition | `{ tmp_ret = x; tmp_ret.is_err() }` | the same row; `OB_UNLIKELY` goes (RULEBOOK 2.3) |
| `FALSE_IT(tmp_ret = x)` | `false_it!(tmp_ret = x)` | RULEBOOK 2.1 |
| `tmp_ret = x;`, `tmp_ret = OB_X;`, `tmp_ret = OB_SUCCESS;` | `tmp_ret = x;`, `tmp_ret = Err(OB_X);`, `tmp_ret = Ok(());` | RULEBOOK 2.1 |
| a test of the variable: `OB_X == tmp_ret`, `OB_SUCCESS != tmp_ret`, a `switch` | `matches!(tmp_ret, Err(OB_X))`, `tmp_ret.is_err()`, `match tmp_ret { Err(OB_X) => .., _ => .. }` | RULEBOOK 2.1 |
| a tracepoint value, `OB_E(EventTable::X) OB_SUCCESS` | `ObError::from_code(ob_e!(EventTable::X, 0))` | s2-errors.md 2.4 rule 8; RULEBOOK 2.3, the tracepoint row |
| a code read from an `i32` field or returned by an island | `ObError::from_code(v)` where it enters | s2-errors.md 2.2 rule 5 (the field); RULEBOOK 2.1, codes from outside the catalog (the island) |
| `LOG_WARN(...)`, `LOG_ERROR(...)` and module variants in these branches | `log_warn!(ret, ...)`: the `ret` in scope on the C++ line, never `tmp_ret`; `*_LOG_RET(level, tmp_ret, ...)` and `LOG_WARN_RET(tmp_ret, ...)` pass `tmp_ret` | s2-errors.md 2.5 rule 1 |
| under `#ifdef ERRSIM` | inside `#[cfg(feature = "errsim")]` | RULEBOOK 2.3 |
| a row in a `#define` body | the forms above inside the macro's `#[inline] fn`, or its lowercase `macro_rules!` when an argument is not evaluated once with a fixed type. A local of the caller that the body reads or writes by name (`ret`, `pos` and `expr_str` in `REPORT_OUT_OF_RANGE_ERROR`, src/sql/engine/expr/ob_expr_int_div.cpp:48-57; `tmp_ret` in `CLICK_TMP_FAIL`, src/share/ob_occam_time_guard.h:339) becomes an argument named at each use, as `ob_fail!` names its variable and `buf_printf!` passes the locals `BUF_PRINTF` reads | RULEBOOK 2.3, the function-like macro row and the printf-family row; s2-errors.md 2.3 (a `macro_rules!` macro cannot see the caller's `ret`) |
| the function that holds a MERGE_AND or MERGE_AS_WRITTEN row | the local-`ret` form, no `?` | s2-errors.md 2.3 |

## 5. How to choose the value

Terms the tests use:

- **The variable**: the one the row names (`tmp_ret`, `temp_ret`, `tmp_ret_code`, `tmp_ret_2`, or the variable of a continue-and-record row, such as `close_ret` or `warning`).
- **The code at the row**: what the row puts into the variable: a call's result, a constant, `ret`, another variable, a tracepoint value. For a reset (the row stores `OB_SUCCESS`) it is the code the reset clears; for `OB_TMP_FAIL(tmp_ret)` it is the code the variable already holds.
- **Tested**: compared with a named catalog code other than `OB_SUCCESS` (`==`, `!=`, a `case` label, or a function over codes such as `is_schema_error(tmp_ret)`), on the row's line or on a later line that reads this code before the variable is assigned again (a self-assignment `OB_TMP_FAIL(tmp_ret)` does not count as one, as under Reaches).
- **Reaches**: on some path from the row, before the variable is assigned again. A copy into another local code variable (`saved_ret = tmp_ret`) carries the code on: follow the copy. A self-assignment `OB_TMP_FAIL(tmp_ret)` does not end the path: it keeps the code (12 lines: `git grep -nP 'OB_TMP_FAIL\(\s*(tmp_ret|temp_ret)\s*\)' 834bbee1e -- src | wc -l`).
- **A merge line**: a line that gives `ret`, the function's `int &ret` parameter or the function's result (a `return`, or the value of a `({ ... })` statement expression) a value read from the variable, or a code constant of a continue-and-record row; also a line that gives the variable itself an AND-shaped value (`tmp_ret = tmp_ret == OB_SUCCESS ? ret : tmp_ret`). Every sweep row whose construct starts with `merge` is a merge line. So is every `OB_SUCC(x)` and `OB_FAIL(x)` whose `x` is a code variable other than `ret`, because the macros assign `ret = x` (src/oblib/lib/utility/ob_macro_utils.h:654, :655): `OB_SUCC(tmp_ret)`, which no sweep list holds (section 10), and `saved_ret = OB_SUCC(saved_ret) ? tmp_ret : saved_ret;` (src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:894, :899), which the list holds as assignments of `saved_ret`.
- **AND-shaped**: the value is `COVER_SUCC(b)`, or a conditional `c ? x : y` whose condition only tests whether one code holder `a` is `OB_SUCCESS` (`OB_SUCCESS == a`, `a == OB_SUCCESS`, `OB_SUCCESS != a`, `a != OB_SUCCESS`, with any namespace prefix and parentheses, and `OB_SUCC(ret)`, `OB_FAIL(ret)` when `a` is `ret`), whose value is `b` when `a` is `OB_SUCCESS` and `a` otherwise, and whose `b` is a variable or a code constant, not a call. `a.and(b)` evaluates `b` even when `a` failed, while `?:` evaluates only the branch it takes (section 9, Q3). A condition `OB_SUCC(a)` or `OB_FAIL(a)` on another holder is not a test only: it also assigns `ret = a`, and `a.and(b)` would drop that assignment, so the line is not AND-shaped (section 9, Q4).

Take the first step that matches:

1. The row's file (for a split unit, the entry whose line range holds the row) is in the `files` column of migration/not-translated.tsv: **NOT_TRANSLATED**.
2. A design rule replaces, as a whole, the mechanism the row belongs to, and its words cover the enclosing function or macro, or the block or run of statements of it whose only work is a call into that mechanism (6.2 lists the rules and the rows): **REPLACED**.
3. The variable, anywhere in its scope, holds a value that is not an OB code, or its value is used as a number: **NON_CODE_VALUE**, for every row of that variable. A merge through a field that holds such values is NON_CODE_VALUE too.
4. The row's line is a merge line: AND-shaped, **MERGE_AND**; otherwise **MERGE_AS_WRITTEN**.
5. The row declares the variable without a code: its initializer is `OB_SUCCESS` (with any namespace prefix, such as `common::`), `0`, `INIT_SUCC`, or missing, or the variable is a function parameter: **DECLARATION**. A tracepoint initializer, `OB_E(EventTable::X) OB_SUCCESS`, is a code (section 4), not `OB_SUCCESS`, so step 5 does not take its declarations (17 lines: `git grep -nP '^\s*(const\s+)?(int|int64_t)\s+(tmp_ret|temp_ret)\s*=\s*\(?\s*OB_E\(' 834bbee1e -- src | wc -l`).
6. The code at the row is tested, is itself a named code the tests below name, or makes the operation that produced it run again (RETRY's test, which a failure test with no named code can meet too). Take the first of these that holds, in this order (the order of ret-compare.md, which classifies the compare lines on these variables): **RETRY**, **RENAMED**, **END_OF_DATA**, **LOOKUP_OUTCOME**, **BUFFER_TOO_SMALL**, **OTHER_CODE**. A reset inside the branch of such a test takes the value of the code it clears.
7. The code at the row reaches a store other than `ret`: **STORED**.
8. The code at the row reaches `ret`, the function's `int &ret` parameter or the function's result: **PASSED_ON**.
9. Otherwise: **IGNORED**.

A step that cannot be answered from the function and the cited rules makes the row **UNKNOWN** (section 7).

These clauses go into `target_translation` on top of the value's form, whenever they apply:

- **Core units.** A row whose line core-manifest.tsv places (a ranged entry `file:a-b` covers only its lines, as in section 11's script) is classified the same way, since s2-errors.md binds the hand-designed core as well as the leaves; add `Core unit (core-manifest.tsv:<line>): this form where the core keeps the function (RULEBOOK section 0).`
- **Removed branches.** A row inside a branch that a section-2 rule removes keeps its value and adds `goes with its branch (<rule>)`: the branch of a failed general allocation (RULEBOOK 2.2, the `OB_ALLOCATE_MEMORY_FAILED` row; RULEBOOK section 0), with the tag `allocation-guard`; an `OB_INIT_TWICE` or `IS_NOT_INIT` branch the constructor makes meaningless, unless a caller tests the not-initialized state (RULEBOOK 2.1, the `init()` row). A comparator's "already failed" branch goes for a comparator whose error the caller sees, and stays for one whose error the C++ loses in a copy the algorithm made (s2-errors.md 2.4 rule 5; RULEBOOK 2.1, the comparator row); the comparator's ret-alias.tsv row says which, and ret-compare.md's clause on tests that go with their branch reads it the same way. No row of this list is in such a branch: the one row inside a comparator, tmp-ret.tsv:1450 in `ObDASFuncDataIter::FtsDocIdCmp::operator()`, holds the comparison result `cmp_func_` writes (src/sql/das/iter/ob_das_func_data_iter.h:134-140) and is NON_CODE_VALUE. A row in the branch that a null test takes when the pointer is NULL, where another list may drop that test (a service lookup that becomes a context field, server-service-slots.tsv), adds `kept unless the null test at <file:line> goes (<list> row)`. A row in the part that runs when the pointer is not NULL, the body of `SERVER_MODULE_SCOPE` included (src/share/rc/ob_server_runtime.h:230-232), stays whatever that list decides, and adds nothing (RULEBOOK 2.8, the slot null-check row).
- **Calls that can no longer fail.** When every code the callee can return, read from its body, comes from a general allocation failure, add `kept unless the callee's Rust signature cannot fail (s2-errors.md 2.2 rule 3)`: then the call becomes a plain statement and its failing branch goes. The declaration index gives the signature; this list does not decide it.
- **Lock and reference-count calls** (design amendment A1-2, A1-3 and A1-8; the commands are in section 11). A row whose call changes a reference count or takes or releases a lock takes its value by the steps like any other row; what the call becomes decides whether the row still holds a code.
  - The block manager's count of references to a disk block stays a count with its C++ codes, and a release the C++ makes explicitly stays a call at its place; one in a destructor runs in `Drop` (s3-memory.md R1 and the paragraph after it; RULEBOOK 2.2, the `inc_ref`/`dec_ref` row; ARCHITECTURE §3.1 rule 9). So `OB_STORAGE_OBJECT_MGR.inc_ref(id)` and `.dec_ref(id)`, which forward to it (src/storage/blocksstable/ob_object_manager.cpp:174-194), and a bare `inc_ref` or `dec_ref` inside `ObBlockManager` keep the call and its code, in section 4's form (9 rows; example 5). A count of objects in memory is different: s3-memory.md R1 makes a transaction-context reference an `Arc`, and the core API gives the release's Rust signature, so a row whose call is `revert_tx_ctx` or `revert_tx_ctx_without_lock` (8 rows) adds `kept unless the callee's Rust signature cannot fail (s3-memory.md R1; s2-errors.md 2.2 rule 3)`.
  - An `ObRecursiveMutex` keeps the C++ API: the `sync` module's `ObRecursiveMutex` returns `ObResult` from `lock()`, `trylock()` and `unlock()`, and every C++ lock and unlock site stays where it is, a lock taken in one function and released in another included (s8-numerics-platform.md 5 rules 5 and 8; RULEBOOK 2.7, the `ObRecursiveMutex` row). The call and its code stay, in section 4's form (2 rows, on the session's `query_mutex_`: src/sql/engine/px/ob_px_sqc_handler.cpp:262 and :318; example 4).
  - An explicit `unlock()` of any other OB lock is the drop of the lock's guard where the C++ unlocks, the owning guard where the C++ locked in another function (s8-numerics-platform.md 5 rules 5 and 6; RULEBOOK 2.7, the OB lock row). A drop has no code, so the statements whose only work is that unlock and the test of its code are REPLACED (6.2; 3 rows). A timed lock keeps its code (s8-numerics-platform.md 5 rule 2; RULEBOOK 2.7, the timed-lock row); outside src/oblib/lib/lock/, no row of this list takes a lock other than the `ObRecursiveMutex` above. One row tests a guard's lock result: src/share/cache/ob_kvcache_map.cpp:333, `guard.get_ret()` of the `ObBucketWLockGuard` of src/share/cache/ob_kvcache_map.cpp:332, whose C++ constructor keeps the code of `wrlock` (src/oblib/lib/lock/ob_bucket_lock.h:128). The `sync` module's `ObBucketLock` gives that guard, and whether it can fail is the core API's to say, so the row adds `kept unless the Rust guard of ObBucketLock cannot fail (s8-numerics-platform.md 5, the sync module)`.
- **Macro bodies.** A row in a `#define` body is decided by the body's own statements; where they depend on the use (the body only assigns the variable), by the macro's uses: the value they all give, else UNKNOWN. The evidence names at least one use.

## 6. The values

| # | Value | What the code, or the row, is |
|---|---|---|
| 1 | NOT_TRANSLATED | in a file the port does not translate |
| 2 | REPLACED | in a mechanism the design replaces as a whole |
| 3 | NON_CODE_VALUE | a variable that also holds values that are not codes |
| 4 | MERGE_AND | a merge meaning "a unless a is success, then b" |
| 5 | MERGE_AS_WRITTEN | any other merge |
| 6 | DECLARATION | a code variable declared with no code yet |
| 7 | RETRY | a code on which the work is tried again |
| 8 | RENAMED | a code replaced by another named code |
| 9 | END_OF_DATA | the end of an iteration |
| 10 | LOOKUP_OUTCOME | whether a key, id or name is present |
| 11 | BUFFER_TOO_SMALL | a full buffer or fixed-size container |
| 12 | OTHER_CODE | another named code the function tests |
| 13 | STORED | a code kept outside the function's locals |
| 14 | PASSED_ON | a code handed to the caller |
| 15 | IGNORED | a code that goes nowhere |
| 16 | UNKNOWN | a row the design or the site leaves open |

### 6.1 NOT_TRANSLATED

- **Test.** Step 1. A ranged entry (`file:a-b`) covers the row only when the row's line is in the range: `grep -nP '(^|[\t,])src/<path>([\t,:]|$)' migration/not-translated.tsv migration/manifest.tsv migration/core-manifest.tsv`.
- **target_translation.** `none: <reason> (not-translated.tsv:<line>)`. An island file stays C or C++, compiled from its src/ path (RULEBOOK section 0; s7-islands-unsafe.md 7.1).
- **Evidence.** The not-translated.tsv line with its unit and reason; nothing else is traced.
- **Sites.** src/sql/parser/parse_node.c:344 (island, not-translated.tsv:170); src/standby/standby_module.cpp:229 (deferred, not-translated.tsv:240).

### 6.2 REPLACED

- **Test.** Step 2. The rules that replace a whole mechanism with rows in this list:
  - RULEBOOK 2.1 maps every use of `OB_TMP_FAIL` and `COVER_SUCC` (ob-errno's `ob_fail!` serves the first, `Result::and` the second; s2-errors.md 2.3), so their `#define` lines, src/oblib/lib/utility/ob_macro_utils.h:656 and :657, have no Rust item.
  - RULEBOOK 2.7, the `SMART_VAR` row, and s8-numerics-platform.md 6 rule 6: a value over 16 KiB lives on the heap or in an arena, so the `__SMART_VAR` body (src/oblib/lib/utility/ob_smart_var.h:171, :172) goes.
  - RULEBOOK section 1, the Locks row, RULEBOOK 2.7 and s8-numerics-platform.md 5: ob-base's `sync` module is the only lock API, and rule 8 turns `ObThreadCond` and `ObCond` into `Condvar` with the waited-for state in `Mutex<T>` (RULEBOOK 2.7, the `ObThreadCond`/`ObCond` row). This covers the functions of the lock classes in src/oblib/lib/lock/ (`ObLatch` and its wait queue, `DRWLock`, `ObBucketLock` and its guards, `ObThreadCond`): 33 rows; and `ObCond::wait` and `ObCond::timedwait` (src/oblib/lib/thread/ob_queue_thread.cpp:56, :86): 2 rows, at src/oblib/lib/thread/ob_queue_thread.cpp:73 and :103.
  - RULEBOOK 2.7, the OB lock row, and s8-numerics-platform.md 5 rules 5 and 6 (design amendment A1-8): the guard of an OB lock lives exactly as long as the C++ holds the lock and drops where the C++ unlocks, so an explicit `unlock()` is the drop of the guard, which has no code. The timed-lock example of s8-numerics-platform.md 5 reads it that way: the guard's drop stands for `ObTabletDDLKvMgr::unlock`, the latch's unlock and the test of its code included (src/storage/ddl/ob_tablet_ddl_kv_mgr.cpp:187-192). This covers the statements whose only work is such an unlock and the test of its code: 3 rows, src/rootserver/ob_local_management_service.cpp:2318 and :2319 (the `if (lock_succ)` block of src/rootserver/ob_local_management_service.cpp:2317-2321, on the `ObLatch` `set_config_lock_` of src/rootserver/ob_local_management_service.h:371) and src/storage/blocksstable/ob_block_manager.cpp:1769 (with its test at :1770-1771, on the `lib::ObMutex` `running_mutex_` of src/storage/blocksstable/ob_io_bench_controller.h:45). An `ObRecursiveMutex` is not replaced: the `sync` module's type keeps its `lock()` and `unlock()` with their codes (section 5, the clause on lock and reference-count calls), and no row of this list is in src/oblib/lib/lock/ob_recursive_mutex.h.
  - RULEBOOK 2.7 (reclamation clocks: QClock, the retire station, hazard versions) and s8-numerics-platform.md 5, "Lock-free code": hand-written reclamation, the KV cache's hazard versions among it, is replaced, not translated (s3-memory.md R5). This covers the 4 rows that hold the code of `global_hazard_station_.retire()` in src/share/cache/ob_kvcache_map.cpp, and the 2 rows of the block of `ObKVCacheStore::pop_mb_handle_with_recovery` that finishes the reclamation of retired memory blocks through `HazardDomain::get_instance().reclaim(callback)` (src/share/cache/ob_kvcache_store.cpp:1001, :1004): s3-memory.md 3.7.3 keeps the handle pool's limit and maps its recovery steps onto the Rust reclamation of R5, in which a block's slot comes back when its last `Arc` drops (s3-memory.md E8).
  - The OB allocators' own internals, replaced by jemalloc and bumpalo (ARCHITECTURE §3.2; RULEBOOK section 1, the Memory row; RULEBOOK 2.2): a FIFO or slice allocator with a limit becomes a `Budget` at a named owner or plain allocation (s3-memory.md 3.5), and a pool that only reuses memory goes (s3-memory.md R3). This covers the functions of those allocators: `ObSliceAlloc::alloc` (src/oblib/lib/allocator/ob_slice_alloc.h:352; rows at :354, :357, :372) and `ObVSliceAlloc::alloc` (src/oblib/lib/allocator/ob_vslice_alloc.h:151; rows at :169, :189). The rows of `ObSimpleFifoAlloc::alloc` are NOT_TRANSLATED first: its file is dead (not-translated.tsv:17).
  - Any other rule counts only when its words name the class, function or macro the row sits in.
- **target_translation.** `none: replaced by <what replaces it> (<rule>)`.
- **Evidence.** The enclosing function's or macro's definition line (for a block or a run of statements, its first and last lines) and the words of the rule that cover it.
- **Sites.** src/oblib/lib/lock/ob_thread_cond.h:52 (`pthread_mutex_lock` in `ObThreadCond::lock`); src/oblib/lib/lock/ob_drw_lock.h:163; src/share/cache/ob_kvcache_map.cpp:127; src/share/cache/ob_kvcache_store.cpp:1004 (the hazard-domain reclaim of a core unit, which a classifier following only the enclosing function would call IGNORED); src/oblib/lib/utility/ob_smart_var.h:172; src/rootserver/ob_local_management_service.cpp:2319 (`none: replaced by the drop of set_config_lock_'s guard where the C++ unlocks`, a block whose only work is the unlock).

### 6.3 NON_CODE_VALUE

- **Test.** Step 3: some assignment stores a value that is not an OB code, or some read uses the value as a number: the return of a pthread or other OS call (an errno-style number), a comparison result, a length or a count, a sentinel such as `INT64_MAX`, a tracepoint value used as a number (`std::abs`, a seed, a count, a sleep time), arithmetic, a comparison with a constant that is not a code. A variable whose writers all produce OB codes, and which is compared only with codes, `OB_SUCCESS` or 0, is not NON_CODE_VALUE.
- **target_translation.** The variable keeps its C++ type under its C++ name (`let mut tmp_ret: i32 = 0;`, `i64` for `int64_t`). A comparison with a code compares the integers as the C++ promotes them (`tmp_ret == i64::from(OB_ERROR.code())`). Where the value enters an `ObResult` it goes through `ObError::from_code(v)` (with `as i32` where the C++ narrows `int64_t` to `int`), and an `ObResult` entering it through `.code()` (s2-errors.md 2.1, the `ErrCode` trait, and 2.2 rule 5). An AND-shaped merge of it is `ret = ret.and(ObError::from_code(v))`; any other merge keeps the C++ shape with the same conversion. A tracepoint value used as a number is `ob_e!(EventTable::X, 0)` with no `from_code` (RULEBOOK 2.3).
- **Evidence.** The line where the non-code value enters or is used as a number, and each line where the variable meets `ret` or another code holder.
- **Sites.** src/sql/das/iter/ob_das_text_retrieval_merge_iter.cpp:78 (`cmp_func_` writes a comparison result into it at src/sql/das/iter/ob_das_text_retrieval_merge_iter.cpp:81, copied into `cmp_ret` at :84); src/sql/ob_sql.cpp:2394 (a tracepoint value made into a seed with `std::abs` at src/sql/ob_sql.cpp:2398); src/sql/engine/table/ob_table_scan_op.cpp:3766 (stored as `rand_seed_` at src/sql/engine/table/ob_table_scan_op.cpp:3777); src/rootserver/ddl_task/ob_ddl_redefinition_task.cpp:458 (merges `complete_sstable_job_ret_code_`, an `int64_t` that starts at `INT64_MAX`, src/rootserver/ddl_task/ob_ddl_redefinition_task.h:110).

### 6.4 MERGE_AND

- **Test.** Step 4, AND-shaped. The operand that is tested is `a`; the value taken when it succeeded is `b`.
- **target_translation.** `<target> = a.and(b);`, a constant `b` written `Err(OB_X)`: `ret = ret.and(tmp_ret);`, `ret = ret.and(Err(OB_ERR_UNEXPECTED));`, `*ret = ret.and(tmp_ret);` for an `int &ret` parameter, `return ret.and(close_ret);` (RULEBOOK 2.1, the `COVER_SUCC` row: any merge meaning "a unless a is success, then b" is `a.and(b)`; s2-errors.md 2.3, 2.4 rule 4). A bare `COVER_SUCC(b);` statement, whose value nothing takes, merges nothing in the C++: `let _ = ret.and(b);` under `// BUG(port): COVER_SUCC's value is dropped, so b's error never reaches ret; repro: b fails while ret is OB_SUCCESS; <C++ file:line>` (RULEBOOK 2.10, the BUG rule).
- **Evidence.** Which operand is `a` and which is `b`; where `b`'s code comes from (its assignment rows) or that it is a constant; the function's form. When `b` or `a` is a field, the field's type and why, as STORED asks.
- **Sites.** src/oblib/rpc/frame/ob_sql_processor.cpp:49 (spelled `(OB_SUCCESS != ret) ? ret : tmp_ret_2`; example 2); src/pl/ob_pl.cpp:635 (into the `int &ret` parameter of `ObPLContext::reset_exec_env`, src/pl/ob_pl.cpp:617); src/storage/tablelock/ob_table_lock_service.cpp:793 (`(OB_SUCCESS == tmp_ret) ? ret : tmp_ret`, so `a` is `tmp_ret`: `ret = tmp_ret.and(ret)`); src/sql/das/iter/ob_das_hnsw_scan_iter.cpp:290 (into the variable itself: `tmp_ret = tmp_ret.and(ret)`); src/oblib/common/mysqlclient/ob_mysql_transaction.cpp:137 (`a` is the parameter `err_no` of src/oblib/common/mysqlclient/ob_mysql_transaction.cpp:130: `ret = err_no.and(tmp_ret)`); src/storage/tablelock/ob_obj_lock.cpp:623 (`ret = ret.and(Err(OB_TIMEOUT))`); src/sql/tablelock/ob_lock_executor.cpp:501 (one of the 6 bare `COVER_SUCC(tmp_ret);` statements).

### 6.5 MERGE_AS_WRITTEN

- **Test.** Step 4, not AND-shaped: a plain `ret = X;` under any guard, `if (OB_SUCC(ret)) { ret = tmp_ret; }` included (section 9, Q2); `int ret = tmp_ret;`; a conditional on another condition; a code passed through a function; a chained assignment; a hidden `OB_SUCC(x)` or `OB_FAIL(x)` on a code variable other than `ret`, alone or as the condition of a conditional (section 9, Q4).
- **target_translation.** The statement as the C++ writes it: `ret = tmp_ret;`; `if ret.is_ok() { ret = tmp_ret; }`; `let mut ret: ObResult = tmp_ret;`; `ret = if is_commit { temp_ret } else { ret };`; `ret = if matches!(tmp_ret, Err(OB_ITER_END)) { Ok(()) } else { tmp_ret };`; `tmp_ret = Err(OB_INNER_STAT_ERROR); ret = tmp_ret;`; `ob_fail!(ret = tmp_ret)` for a hidden merge, whose assignment of `ret` stays; `saved_ret = if ob_succ!(ret = saved_ret) { tmp_ret } else { saved_ret };` for a conditional whose condition is one (RULEBOOK 2.1: "any other merge as the C++ writes it", and the `OB_FAIL(x)` and `OB_SUCC(x)` rows; s2-errors.md 2.4 rule 4).
- **Evidence.** The guard the merge sits under, and whether `ret` can already hold an error there (the merge then keeps the first error or overwrites it); where `X`'s code comes from; a field `X`'s type, as STORED asks.
- **Sites.** src/sql/engine/ob_operator.cpp:1272 (`if (OB_SUCC(ret)) { ret = tmp_ret; }`); src/sql/dtl/ob_dtl_flow_control.cpp:122 (in a loop, over any earlier error: the last error wins); src/rootserver/ob_ddl_service.cpp:10942 (`is_commit` is a `bool` set at src/rootserver/ob_ddl_service.cpp:10937); src/oblib/common/number/ob_number_v2.cpp:2372 (`OB_ITER_END` becomes `OB_SUCCESS`); src/observer/ob_server_reload_config.cpp:52 (a chained assignment); src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:894 (`OB_SUCC(saved_ret)` sets `ret` to the first saved error on the second failure, which ends the loop of src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:872; `saved_ret.and(tmp_ret)` would run the loop on).

### 6.6 DECLARATION

- **Test.** Step 5.
- **target_translation.** Section 4's declaration: `let mut tmp_ret: ObResult = Ok(());`, `let tmp_ret: ObResult;` for an `int tmp_ret;` nothing assigns, a parameter `tmp_ret: ObResult` (RULEBOOK 2.1, the `int ret` and `tmp_ret` rows; s2-errors.md 2.2 rule 5). The declaration stays even when nothing reads the variable: RULEBOOK section 0 does not list it among the statements a translation may remove.
- **Evidence.** Every line that writes the variable and every line that reads it (merge, test, store, log), with their rows; for a variable nothing uses, that no line after the declaration writes or reads it, and no writer is listed.
- **Sites.** src/storage/tx_table/ob_tx_table.cpp:687; src/sql/resolver/dml/ob_standard_group_checker.cpp:104 (`int tmp_ret;`, never written or read); src/storage/memtable/ob_lock_wait_mgr.cpp:521 (the parameter `const int tmp_ret` of `ObLockWaitMgr::post_lock`, tested against `OB_TRY_LOCK_ROW_CONFLICT` at src/storage/memtable/ob_lock_wait_mgr.cpp:537); src/oblib/common/number/ob_number_v2.cpp:189 (`int warning`, written through the callee's `int &warning` at src/oblib/common/number/ob_number_v2.cpp:191).

### 6.7 RETRY

- **Test.** Step 6: some way through the branch that the code at the row selects repeats the operation that produced it, on the same input: the test that selects the branch, a named-code test or the failure test itself (`while (OB_TMP_FAIL(x))`, `do { .. } while (OB_TMP_FAIL(tmp_ret))`), is (part of) the condition of a loop that repeats it, or the branch jumps back to it, waits or sleeps before the enclosing loop repeats it, queues or schedules the same task again, or sets the flag or out-parameter that makes the caller repeat it (`need_retry = true`). Not a retry, as ret-compare.md's RETRY row says: a loop that stops on the code (`for (..; OB_SIZE_OVERFLOW != tmp_ret && ..; ..)`, `while (OB_ITER_END != (tmp_ret = it.get_next()))`); a loop that goes on to the next item after the code; a loop that runs the operation again whatever it returned (a worker's periodic loop). `OB_EAGAIN` alone does not make a row RETRY.
- **target_translation.** Section 4's form, and the loop, jump, wait or re-queue as the C++ writes it, the loop condition evaluated each round (RULEBOOK section 0, "Keep the control flow"; s2-errors.md 2.4 rule 1).
- **Evidence.** The test line and its code; the loop, wait, re-queue or flag line; where the other codes go.
- **Sites.** src/storage/tablelock/ob_table_lock_service.cpp:978 (`need_retry_partial_task_` at src/storage/tablelock/ob_table_lock_service.cpp:979 sends the failed locks back through `retry_ctx.retry_lock_ids_`); src/storage/ls/ob_ls.cpp:131 (`} while (OB_TMP_FAIL(tmp_ret));` repeats `remove_ls_inner_tablet()` of src/storage/ls/ob_ls.cpp:129 until it succeeds, with no named code); src/storage/ls/ob_freezer.cpp:897 (`push_back` repeated after a 100 ms sleep, src/storage/ls/ob_freezer.cpp:900).

### 6.8 RENAMED

- **Test.** Step 6: the code at the row is replaced by a different named code before it goes anywhere: the row is the assignment of that other code in the branch of a test that saw the first one, or the code at the row reaches such an assignment. A copy of the same code into the variable (`tmp_ret = OB_USER_NOT_EXIST` after `OB_USER_NOT_EXIST == ret`) is not a rename.
- **target_translation.** Section 4's form; the rename at the same place, as the C++ writes it: `tmp_ret = Err(OB_Y);`, never `map_err` or `if let Err` (s2-errors.md 2.4 rule 3; 2.15 item 7).
- **Evidence.** The test, the code it saw, the new code and its line.
- **Sites.** src/observer/ob_service.cpp:385 (`OB_SIZE_OVERFLOW`, tested at src/observer/ob_service.cpp:384, becomes `OB_EAGAIN`); src/observer/ob_service.cpp:380 (the `add_dag` code that reaches that rename).

### 6.9 END_OF_DATA

- **Test.** Step 6: the code at the row is `OB_ITER_END` or `OB_ITER_STOP`: tested against it, or assigned as a constant. ret-compare.md's END_OF_DATA row and reset.md 4.10 count the same two codes, so a line on two lists gets the same value.
- **target_translation.** Section 4's form, and every test, reset and rename of this code at the C++ place (`{ tmp_ret = x; !matches!(tmp_ret, Err(OB_ITER_END)) }`, `tmp_ret = Err(OB_ITER_END);`). The code stays `Err(OB_ITER_END)`, never an `Option` or a `bool`, until parity, and row and batch iterators keep it at their boundary (s2-errors.md 2.4 rules 1 and 2; ARCHITECTURE.md section 17, default 6).
- **Evidence.** The producer (the call, or the constant's line), the test and what each branch does (stop, reset, rename, error), and where the other codes go.
- **Sites.** src/share/ob_autoincrement_service.cpp:1459 (a second row is an error); src/oblib/lib/stat/ob_diagnose_info.cpp:284 (`OB_ITER_END` marks "not found yet" and is cleared at src/oblib/lib/stat/ob_diagnose_info.cpp:292); src/sql/das/iter/ob_das_ivf_scan_iter.cpp:692 (`int tmp_ret = (ret == OB_ITER_END) ? OB_SUCCESS : ret;`).

### 6.10 LOOKUP_OUTCOME

- **Test.** Step 6: (a) the code at the row is `OB_HASH_NOT_EXIST`, `OB_HASH_EXIST`, `OB_ENTRY_NOT_EXIST`, `OB_ENTRY_EXIST`, `OB_SEARCH_NOT_FOUND`, `OB_EMPTY_RESULT`, `OB_READ_NOTHING`, `OB_ERR_PRIMARY_KEY_DUPLICATE`, `OB_ERR_NULL_VALUE`, or a code whose name ends in `_NOT_EXIST`, `_NOT_EXISTS`, `_NOT_FOUND`, `_EXIST` or `_EXISTS`, tested or assigned; and (b) its producer looks up, reads, inserts or erases an item by key, id or name (a map, set, cache, catalog, schema guard, result-set column getter, inner-table read) and the code is that call's, passed on unchanged or renamed on the way (`ObDDLTaskQueue::push_task` turns the task map's `OB_HASH_EXIST` into `OB_ENTRY_EXIST`, src/rootserver/ddl_task/ob_ddl_scheduler.cpp:99-101), or the row sets the code right after such a lookup found nothing or found the item. These are ret-compare.md's LOOKUP_OUTCOME tests, so the compare row of the same site agrees.
- **target_translation.** Section 4's form and the tests, resets and renames at the C++ place. The lookup keeps its codes: `ObHashMap` and `ObHashSet` answer `OB_HASH_NOT_EXIST` and `OB_HASH_EXIST`, and `OB_NOT_INIT` for a map never created (s2-errors.md 2.4 rules 1 and 2; RULEBOOK 2.1, the row on a code used as a value), and so does `IdHashMap`, which has their codes (RULEBOOK 2.4, the row on a map or set keyed by address). Design amendment A1-4 changes how the ports hand out values, not these codes: `get_refactored` still copies the value out, callbacks get the stored pair in place, and `get` returns an `Option` and no code (s4-sql-front.md 2.1 rule 4; RULEBOOK 2.5, the `ObHashMap` row).
- **Evidence.** The lookup and its container's declaration; the test and what a miss or a hit does; where the other codes go.
- **Sites.** src/storage/access/ob_multiple_merge.cpp:197 (example 3); src/sql/resolver/cmd/ob_show_resolver.cpp:1269 (keeps `OB_USER_NOT_EXIST` while `ret` is reset at src/sql/resolver/cmd/ob_show_resolver.cpp:1270; tested at :1336); src/storage/tx/ob_tx_ctx_mds.cpp:112 (a miss is not logged, src/storage/tx/ob_tx_ctx_mds.cpp:113).

### 6.11 BUFFER_TOO_SMALL

- **Test.** Step 6: (a) the code at the row is `OB_SIZE_OVERFLOW`, `OB_BUF_NOT_ENOUGH` or `OB_HASH_FULL`, tested or assigned; and (b) its producer writes into a buffer or container of fixed size: a printer (`databuff_printf`, `BUF_PRINTF`, `to_string`), a serializer, a row or block writer, `push_back` on a fixed array, an insert into a full fixed-size map or queue. `OB_SIZE_OVERFLOW` from the stack check (`SMART_CALL`, `check_stack_overflow`) is not this value.
- **target_translation.** Section 4's form and the tests at the C++ place. The printers keep `OB_SIZE_OVERFLOW` (s2-errors.md 2.4 rule 2), and so does `push_back` on a fixed array (RULEBOOK 2.3, the `push_back` row).
- **Evidence.** The producer and the statement in it that returns the code; the test and what follows (stop, grow and retry, keep what fit).
- **Sites.** src/storage/checkpoint/ob_data_checkpoint.cpp:624 (the flush loop stops at `OB_SIZE_OVERFLOW`, src/storage/checkpoint/ob_data_checkpoint.cpp:620; the code comes from a full dag queue, src/storage/scheduler/ob_dag_scheduler.cpp:2126, reached through src/storage/memtable/ob_memtable.cpp:1671).

### 6.12 OTHER_CODE

- **Test.** Step 6, with a tested code none of the tests above takes.
- **target_translation.** Section 4's form and the test at the C++ place (s2-errors.md 2.4 rule 1). A test against `OB_ALLOCATE_MEMORY_FAILED` stays as written (s3-memory.md 3.5, the row on comparisons with it) and carries the -4013 tag.
- **Evidence.** The code, the test and what its branch does.
- **Sites.** src/storage/compaction/ob_tablet_scheduler.cpp:1132 (`OB_STATE_NOT_MATCH` only silences the log, src/storage/compaction/ob_tablet_scheduler.cpp:1133); src/sql/das/iter/ob_das_ivf_scan_iter.cpp:2662 (`OB_EAGAIN` from a busy cache writer marks the cache unusable for this call, src/sql/das/iter/ob_das_ivf_scan_iter.cpp:2667, and nothing repeats it).

### 6.13 STORED

- **Test.** Step 7: the code reaches a field, an array element, an out-parameter other than the function's `int &ret`, a result or packet struct, or a call that takes it as a code argument (the callee's parameter type then decides the slot). Logging or printing the code (`K(tmp_ret)`, `LOG_WARN_RET(tmp_ret, ...)`, `databuff_print_kv(..., tmp_ret)`) is not a store.
- **target_translation.** Section 4's form at the row; at the store, the receiving slot's type decides the statement: an `ObResult` slot takes `tmp_ret`, an `i32` slot takes `tmp_ret.code()` (s2-errors.md 2.1, the `ErrCode` trait: 0 for `Ok`), an atomic slot takes `x.store(tmp_ret.code(), Ordering::SeqCst)` and is read through `ObError::from_code` (RULEBOOK 2.1, a code shared between threads; s8-numerics-platform.md 4). The row records the slot's type (s2-errors.md 2.2 rules 5 and 6): `ObResult` when it holds only codes and is neither sent, stored nor shared; `i32` under its C++ name when it also holds values that are not codes, or is sent or stored (a member of a struct with `OB_UNIS_VERSION` or `OB_SERIALIZE_MEMBER`, a packet or RPC field, a row or inner-table column; section 9, Q1); `AtomicI32` when another thread reads it while this one can write it without a lock, as its atomic-fields.tsv row decides; inside the lock's `T` when a lock guards it, and for an `ObRecursiveMutex`, which holds no data, inside the `Mutex<T>` beside it (lock-guards.tsv; s8-numerics-platform.md 5 rule 8).
- **Evidence.** The store line; the slot's declaration; its other writers and its readers; what decided its type (a non-code writer, the serialization line, the atomic-fields.tsv or lock-guards.tsv row).
- **Sites.** src/observer/ob_service.cpp:601 (example 1); src/storage/tablelock/ob_table_lock_local_executor.cpp:163 (into `result.tx_result_ret_code_` at src/storage/tablelock/ob_table_lock_local_executor.cpp:166, an `int` member of `ObTableLockTaskResult` listed in its `OB_SERIALIZE_MEMBER` of src/storage/tablelock/ob_table_lock_rpc_struct.cpp:95, at src/storage/tablelock/ob_table_lock_rpc_struct.cpp:97).

### 6.14 PASSED_ON

- **Test.** Step 8: the code reaches a merge line, a `return` of the variable, the value of a `({ ... })` statement expression, or the function's `int &ret` parameter. A merge line counts even where `ret` always holds an error, so that the merge keeps the error (src/sql/ob_spi.cpp:919, inside the `OB_SUCCESS != ret` branch of src/sql/ob_spi.cpp:910); the merge row's evidence says so. A bare `COVER_SUCC(b);` statement assigns nothing (6.4) and does not count: a code that reaches only such a line is IGNORED, and its evidence names that line's `BUG(port)` row.
- **target_translation.** Section 4's form at the row; the merge line's own row decides how the merge is written.
- **Evidence.** The merge, return or parameter line the code reaches, and that no assignment of the variable comes in between on that path.
- **Sites.** src/sql/optimizer/stat/ob_dbms_stats_executor.cpp:1400 (`COVER_SUCC` at src/sql/optimizer/stat/ob_dbms_stats_executor.cpp:1401); src/sql/engine/expr/ob_expr_int_div.cpp:51 (in the `REPORT_OUT_OF_RANGE_ERROR` body, the printer's code replaces `OB_OPERATE_OVERFLOW` in `ret` at src/sql/engine/expr/ob_expr_int_div.cpp:57); src/rootserver/ob_admin_job_table_operator.h:108 (the value of the `ADMIN_JOB_FIND` statement expression, src/rootserver/ob_admin_job_table_operator.h:109); src/storage/compaction/ob_compaction_schedule_iterator.cpp:234 (the hidden merge `OB_SUCC(tmp_ret)` at src/storage/compaction/ob_compaction_schedule_iterator.cpp:235 writes the `int &ret` parameter).

### 6.15 IGNORED

- **Test.** Step 9: no merge, return, store or named-code test reads the code before the variable is assigned again or leaves scope. The failing branch may be empty, log, print the code, clean up, set a flag, fall back, sleep, abort, or set a different code into `ret`; the evidence says which. The code may also reach a bare `COVER_SUCC(tmp_ret);`, which drops it (6.4; src/sql/tablelock/ob_lock_executor.cpp:499 reaches :501).
- **target_translation.** Section 4's form at the row, nothing more; the code dies in the variable. A tracepoint value used as an on-off switch enters as `ObError::from_code(ob_e!(EventTable::X, 0))` (s2-errors.md 2.4 rule 8).
- **Evidence.** What the failing branch does, with its lines, and the line where the code is overwritten or leaves scope.
- **Sites.** src/storage/compaction/ob_partition_merger.cpp:908 (an empty branch); src/oblib/common/mysqlclient/ob_mysql_transaction.cpp:179 (the failure turns the commit into a rollback, src/oblib/common/mysqlclient/ob_mysql_transaction.cpp:182); src/observer/mysql/obmp_query.cpp:639 (a tracepoint switch with an empty branch, src/observer/mysql/obmp_query.cpp:640); src/storage/tx/ob_trans_functor.h:696 (the functor returns false, src/storage/tx/ob_trans_functor.h:704); src/storage/tx/ob_tx_on_demand_print.h:58 (the code is printed at src/storage/tx/ob_tx_on_demand_print.h:59); src/storage/memtable/mvcc/ob_mvcc_row.cpp:663 (`OB_ALLOCATE_MEMORY_FAILED` after `allocator.alloc` fails at src/storage/memtable/mvcc/ob_mvcc_row.cpp:662: goes with its branch, `allocation-guard`); src/sql/engine/px/ob_px_sqc_handler.cpp:262 (a failed lock of the session's `ObRecursiveMutex`, whose call keeps its code; example 4); src/storage/blocksstable/ob_sstable.cpp:1272 (a disk block's reference given back on a failure path, whose call keeps its code; example 5).

### 6.16 UNKNOWN

Section 7.

## 7. When a row is UNKNOWN

A row is UNKNOWN when:

- the code's path leaves what the function shows: a callee keeps the variable's address or a reference to it, a lambda that runs later or on another thread captures it, or `int &x = tmp_ret` aliases it;
- a writer's meaning cannot be read from its declaration: a call through a function pointer, a `std::function`, or an interface whose implementations return different kinds of value; a macro defined outside the files the site includes;
- a `#define` body's own statements do not decide the value and its uses give different values;
- the value needs a rule the design lacks: a code that becomes a warning (section 2); a field whose sharing between threads no atomic-fields.tsv or lock-guards.tsv row settles;
- the row sets, merges or tests a -4013 that is neither a general allocation failure nor a named owner, logical limit, tracepoint or kept library: s3-memory.md 3.7.3 sends these to the UNKNOWN rule itself, whatever value the steps of section 5 give. Its evidence carries `precondition-guard`, since the conservative translation keeps the code. The SQL work area's -4013 with dumping off is not one of these: it is a named owner's precondition-guard, kept as written (s3-memory.md 3.7.2, the work-area row, and the sentence after 3.7.3's table; design amendment A1-7), and no row of this list sets, merges or tests it. A -4013 set in the branch that a null test on an allocation, or on the object just built in it, selects is a general allocation failure, as oom-sites.tsv's definition counts it (such lines are no row there): src/storage/memtable/mvcc/ob_mvcc_row.cpp:663 and :667, after the tests of src/storage/memtable/mvcc/ob_mvcc_row.cpp:662 and :665;
- a skeptic reviewer refutes the row with evidence and one revision does not settle it (prompt 02).

**target_translation** (RULEBOOK 2.10, the UNKNOWN rule): the exact C++ statements and control flow, in the local-`ret` form; the variable keeps its C++ integer type under its C++ name, so it holds any value the C++ stores, converting with `ObError::from_code(v)` where its value enters an `ObResult` and `.code()` where an `ObResult` enters it; a merge written as the C++ writes it; and on its own line above the statement `// TODO(port): tmp-ret: <the question> (<C++ file:line>)` (RULEBOOK section 3, the marker table), counted in the file's trailer (RULEBOOK section 6, item 1).

**Evidence**: the facts found, ending with `Question:` and the question in one sentence.

**Site.** src/oblib/lib/container/ob_id_map.h:173, in a core unit (core-manifest.tsv:105), merges `OB_ALLOCATE_MEMORY_FAILED` when the id free list hands back no node (`free_list_.pop` at src/oblib/lib/container/ob_id_map.h:170). That is not an allocation, and not a budget owner or limit s3-memory.md names (3.7.2, 3.7.3, 3.7.5); oom-sites.tsv has no row for it, because its definition takes the null test `NULL == node` (src/oblib/lib/container/ob_id_map.h:171) in the governing condition for a general out-of-memory, though the node comes from a free list, not an allocation. Its translation is `ret = if ret.is_ok() { Err(OB_ALLOCATE_MEMORY_FAILED) } else { ret };` under the marker. Question: is the id map's fixed capacity a logical limit whose -4013 stays, or does it go with the limits of s3-memory.md 3.7.5?

## 8. Worked examples

Five real sites, as a shard holds them (header first). `cite_check.py` prints no flag for them, and its `show` output puts each citation on the line the row names (section 11 gives the command).

- **Example 1, STORED**: a Step 3 unit. An `OB_TMP_FAIL` in an else-if chain whose code goes into a field of a serialized result; the row decides that field is `i32`.
- **Example 2, MERGE_AND**: a Step 3 unit. The reversed spelling `(OB_SUCCESS != ret) ? ret : tmp_ret_2`, whose operand was set inside an `&&` condition.
- **Example 3, LOOKUP_OUTCOME**: a core unit. A declaration that holds a hash-map lookup's code, tested, reset on a miss and merged otherwise.
- **Example 4, IGNORED**: a Step 3 unit. The lock of an `ObRecursiveMutex` that another function unlocks; the call keeps its code under design amendment A1-3.
- **Example 5, IGNORED**: a core unit. A disk block's reference given back on a failure path; the release stays a call with its code under design amendment A1-2.

```
file	symbol	source_construct	classification	target_translation	evidence	status
src/observer/ob_service.cpp	ObService::check_schema_version_elapsed	line 601: OB_TMP_FAIL (plan definition)	STORED	} else if ob_fail!(tmp_ret = <the get_ls call, signature from the declaration index>) { with its empty branch, as the second test of the else-if chain at ob_service.cpp:600-610 (RULEBOOK 2.1, the tmp_ret row; s2-errors.md 2.3 table). At the store, ob_service.cpp:612 becomes single_result.ret_code_ = tmp_ret.code(); (ErrCode::code gives 0 for Ok; s2-errors.md 2.1, 2.2 rule 5). ObCheckTransElapsedResult::ret_code_ stays i32 under its C++ name, as a code field of a serialized result (s2-errors.md 2.2 rule 5, codes in packets; tmp-ret.md section 9, Q1); its readers convert with ObError::from_code. tmp_ret is the ObResult local declared at ob_service.cpp:599.	sweep: tmp-ret.tsv:378 (data row 377). Created: ob_service.cpp:601 puts the code of ls_service->get_ls(ls) into tmp_ret; this test runs only when DDL_SIM at ob_service.cpp:600 succeeded, and the later tests of the chain (ob_service.cpp:602, :606) run only when this one succeeded, so after a failure here tmp_ret still holds this code at the store. tmp_ret is declared once per loop iteration at ob_service.cpp:599 (tmp-ret.tsv:376). Tested: never against a named code in ObService::check_schema_version_elapsed; never merged into ret. Stored: ob_service.cpp:612 (single_result.ret_code_ = tmp_ret), inside the if (OB_SUCC(ret)) of ob_service.cpp:611, which always holds there: the loop condition at ob_service.cpp:594 requires OB_SUCC(ret) and no statement of ob_service.cpp:595-610 writes ret; the result is appended to result.results_ at ob_service.cpp:613. Field: int ret_code_ of ObCheckTransElapsedResult (src/share/ob_rpc_struct.h:2262), a struct with OB_UNIS_VERSION(1) at src/share/ob_rpc_struct.h:2253 and OB_SERIALIZE_MEMBER at src/share/ob_rpc_struct.cpp:1848. Other writers: ob_service.cpp:567 and src/rootserver/ddl_task/ob_ddl_task.cpp:1235. Reader: src/rootserver/ddl_task/ob_ddl_task.cpp:1251 copies it into the ObIArray<int> ret_array. ObService implements ObIRootserverLocalRuntime (src/observer/ob_service.h:76), and the one call of check_schema_version_elapsed through it is the direct call at src/rootserver/ddl_task/ob_ddl_task.cpp:1292, so no seekdb path serializes this result. Single thread: check_trans_end calls the handler directly, on its own thread (src/rootserver/ddl_task/ob_ddl_task.cpp:1219).	confirmed
src/oblib/rpc/frame/ob_sql_processor.cpp	ObSqlProcessor::run	line 49: merge into ret	MERGE_AND	ret = ret.and(tmp_ret_2); (RULEBOOK 2.1, the COVER_SUCC row: a merge meaning 'a unless a is success, then b' is a.and(b); s2-errors.md 2.3 table, 2.4 rule 4). The enclosing test at ob_sql_processor.cpp:48 is if deseri_succ && ob_fail!(tmp_ret_2 = self.after_process(ret)) { (s2-errors.md 2.3, the OB_SUCCESS != (tmp_ret = x) row; OB_UNLIKELY goes, RULEBOOK 2.3). The function keeps the local-ret form (s2-errors.md 2.3: it has tmp_ret merges, and cleanup() runs after an error).	sweep: tmp-ret.tsv:228 (data row 227). Shape: the condition OB_SUCCESS != ret tests only ret, the value is ret when ret failed and tmp_ret_2 when it succeeded, and tmp_ret_2 is a variable: a = ret, b = tmp_ret_2. b: declared at ob_sql_processor.cpp:42 (tmp-ret.tsv:224) and assigned only at ob_sql_processor.cpp:48 by after_process(ret), which runs only while deseri_succ is true (set false at ob_sql_processor.cpp:31 and :33); the merge sits in the failing branch of ob_sql_processor.cpp:48, so tmp_ret_2 holds an error there. a: ret holds the first error of the chain at ob_sql_processor.cpp:30-37, or the response error merged at ob_sql_processor.cpp:44 in the same shape (tmp-ret.tsv:226). After the merge, cleanup() at ob_sql_processor.cpp:52 runs whatever ret holds, and ob_sql_processor.cpp:54 returns ret.	confirmed
src/storage/access/ob_multiple_merge.cpp	ObMultipleMerge::build_extra_access_ctx	line 197: declare tmp_ret	LOOKUP_OUTCOME	let mut tmp_ret: ObResult = <the get_refactored call on self.extra_access_ctx_, signature from the declaration index>; then the C++ tests as written: if matches!(tmp_ret, Err(OB_HASH_NOT_EXIST)) { <fork_ctx cleared as its own row gives it>; tmp_ret = Ok(()); } else if tmp_ret.is_err() { ret = tmp_ret; } (RULEBOOK 2.1: a code used as a value stays Err(OB_X) and is tested and reset at the same place; OB_X == tmp_ret is matches!). The map port keeps OB_HASH_NOT_EXIST at its boundary (s2-errors.md 2.4 rule 2). Core unit (core-manifest.tsv:835): this form where the core keeps the function (RULEBOOK section 0).	sweep: tmp-ret.tsv:2327 (data row 2326). Created: ob_multiple_merge.cpp:197 puts the code of get_refactored on extra_access_ctx_ into tmp_ret; extra_access_ctx_ is a hash::ObHashMap<ObTabletID, ObTableAccessContext*> (src/storage/access/ob_multiple_merge.h:158), a lookup by tablet id. Tested: ob_multiple_merge.cpp:198 against OB_HASH_NOT_EXIST (ret-compare.tsv:4407); the miss clears fork_ctx at ob_multiple_merge.cpp:199 and resets tmp_ret at ob_multiple_merge.cpp:200 (tmp-ret.tsv:2328, LOOKUP_OUTCOME), and the branch from ob_multiple_merge.cpp:209 builds a fork context and inserts it with set_refactored at ob_multiple_merge.cpp:223. Any other failure goes into ret at ob_multiple_merge.cpp:202 (tmp-ret.tsv:2329, MERGE_AS_WRITTEN), where ret is success because the loop condition at ob_multiple_merge.cpp:191 holds OB_SUCC(ret). tmp_ret is declared in the loop body and not read after ob_multiple_merge.cpp:202.	confirmed
src/sql/engine/px/ob_px_sqc_handler.cpp	ObPxSqcHandler::init_env	line 262: assign tmp_ret	IGNORED	} else if ob_fail!(tmp_ret = <the session fetched at ob_px_sqc_handler.cpp:259>.get_query_lock().lock()) { with its empty branch, then the else of ob_px_sqc_handler.cpp:263-265 that sets self.is_session_query_locked_ = true; OB_UNLIKELY goes (RULEBOOK 2.1, the tmp_ret row; s2-errors.md 2.3 table; RULEBOOK 2.3). The lock call stays, with its code: query_mutex_ is the sync module's ObRecursiveMutex, whose lock() returns ObResult and which keeps every C++ lock and unlock site, the unlock in destroy_sqc at ob_px_sqc_handler.cpp:318 included (s8-numerics-platform.md 5 rules 5 and 8; RULEBOOK 2.7, the ObRecursiveMutex row; design amendment A1-3). tmp_ret is the ObResult local declared at ob_px_sqc_handler.cpp:253, and the code dies in it.	sweep: tmp-ret.tsv:1852 (data row 1851). Created: ob_px_sqc_handler.cpp:262 puts the code of session->get_query_lock().lock() into tmp_ret, the last test of the else-if chain of ob_px_sqc_handler.cpp:254-265, reached only when ob_px_sqc_handler.cpp:261 left ret at OB_SUCCESS. get_query_lock() returns the session's query_mutex_ (src/sql/session/ob_basic_session_info.h:725), a common::ObRecursiveMutex (src/sql/session/ob_basic_session_info.h:1686). Tested: never against a named code. The failing branch at ob_px_sqc_handler.cpp:262 is empty: after a failed lock the else of ob_px_sqc_handler.cpp:263-265 does not set is_session_query_locked_, ret keeps OB_SUCCESS, and tmp_ret, declared at ob_px_sqc_handler.cpp:253 (tmp-ret.tsv:1851), is not read again before init_env returns at ob_px_sqc_handler.cpp:273. The lock is released in another function: destroy_sqc unlocks at ob_px_sqc_handler.cpp:318 (tmp-ret.tsv:1855, IGNORED as well: an empty branch) only when is_session_query_locked_ is set (ob_px_sqc_handler.cpp:317), which is why the reentrant lock keeps its explicit lock() and unlock(). No row of ret-compare.tsv or reset.tsv holds this line.	confirmed
src/storage/blocksstable/ob_sstable.cpp	ObSSTable::inc_macro_ref	line 1272: OB_TMP_FAIL (plan definition)	IGNORED	} else if ob_fail!(tmp_ret = <the dec_ref call of the object manager on macro_id, reached through the context (RULEBOOK 2.8), signature from the declaration index>) { with its empty branch, then the else of ob_sstable.cpp:1273-1275 that logs at DEBUG (RULEBOOK 2.1, the tmp_ret row; s2-errors.md 2.3 table). The call stays, with its code: the block manager's count of references to a disk block stays a count with its C++ codes, and this explicit release on the failure path of inc_macro_ref stays a call at its place, not an Arc drop (s3-memory.md R1 and the paragraph after it; RULEBOOK 2.2, the inc_ref/dec_ref row; design amendment A1-2). tmp_ret is the ObResult local declared at ob_sstable.cpp:1267, and the code dies in it. Core unit (core-manifest.tsv:523): this form where the core keeps the function (RULEBOOK section 0).	sweep: tmp-ret.tsv:2393 (data row 2392). Created: ob_sstable.cpp:1272 puts the code of OB_STORAGE_OBJECT_MGR.dec_ref(macro_id) into the tmp_ret declared at ob_sstable.cpp:1267 (tmp-ret.tsv:2390). The call runs in the loop of ob_sstable.cpp:1270, which gives back the data-block references inc_macro_ref took, in the branch of ob_sstable.cpp:1266 that runs when ret failed and the meta handle is valid. OB_STORAGE_OBJECT_MGR is ObObjectManager::get_instance() (src/storage/blocksstable/ob_object_manager.h:146), whose dec_ref returns the block manager's (src/storage/blocksstable/ob_object_manager.cpp:185-194). Tested: never against a named code. The failing branch is empty and the else of ob_sstable.cpp:1273-1275 only logs at DEBUG; the loop goes on whatever the code (ob_sstable.cpp:1270), and the next OB_TMP_FAIL, at ob_sstable.cpp:1271 in the next round or ob_sstable.cpp:1279 after the loop, overwrites tmp_ret before anything reads it. It is never merged into ret, which already holds the error that led here (ob_sstable.cpp:1264). No row of ret-compare.tsv or reset.tsv holds this line.	confirmed
```

## 9. Open questions

The design leaves these undecided or open to two readings. Each gives the reading this file takes and its translation, which gives the C++ behavior under either answer, so the rows it covers stay `confirmed`; the question goes to the developer. Design amendment 1 settled none of them. Each ends with the design wording it proposes; nothing waits on a running classification, so the wording can go into the next amendment.

**Q1. Is a code field of a struct with a serializer `i32` when no seekdb path serializes it?** s2-errors.md 2.2 rule 5 keeps codes "in rows and packets" as `i32`, but does not say whether a struct with `OB_UNIS_VERSION` and `OB_SERIALIZE_MEMBER` that is only passed in memory counts. `ObCheckTransElapsedResult::ret_code_` (src/share/ob_rpc_struct.h:2262) holds only codes, and the two handlers that fill it are called directly, not through a serializer (src/rootserver/ddl_task/ob_ddl_task.cpp:1292, :1357). This file takes such a field as a packet field: `i32`, which holds every value exactly and keeps the encoder's output visibly the same; reset.md and ret-compare.md read "serialized" the same way. The other answer would make such fields `ObResult`; the rows it would change are the STORED rows whose evidence cites an `OB_SERIALIZE_MEMBER` or `OB_UNIS_VERSION` line.

**Q2. `if (OB_SUCC(ret)) { ret = tmp_ret; }`: `and` or as written?** RULEBOOK 2.1 writes "`COVER_SUCC(t)`, and any merge meaning 'a unless a is success, then b'" as `a.and(b)`, which can be read to cover this `if`. s2-errors.md 2.4 rule 4 counts it among "the 204 plain `ret = tmp_ret;` lines", written as the C++ writes them, and migration/design/research/02-errors.md 2.5 says why: those lines keep or overwrite depending on their guards. The design file wins over the RULEBOOK's summary (RULEBOOK section 0), so this file makes it MERGE_AS_WRITTEN. Both forms behave the same. For the next design amendment: RULEBOOK 2.1's row should say "an expression meaning".

**Q3. `a.and(b)` evaluates `b` even when `a` failed.** The C++ `?:` evaluates only the branch it takes, and the design's AND rule does not say that `b` must not be a call. No conditional merge line of this list has a call in either branch (292 checked), so every MERGE_AND row is exact. Five merges outside the list take a call's result (section 10): written as `a.and(b)` they would call `append` or `push_back` after an error. This file keeps calls out of the AND shape: they are MERGE_AS_WRITTEN, `ret = if ret.is_err() { ret } else { <the call> };`. For the next design amendment: RULEBOOK 2.1 and s2-errors.md 2.3 should say that `b` is a variable or a constant.

**Q4. `v = OB_SUCC(a) ? b : a` with `a` other than `ret`: `and` or as written?** s2-errors.md 2.3's table turns `v = (a == OB_SUCCESS) ? b : a` "in any spelling" into `v = a.and(b)`. The spelling `OB_SUCC(a)` is not a plain test when `a` is not `ret`: the macro assigns `ret = a` (src/oblib/lib/utility/ob_macro_utils.h:654). At src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:894 and :899, `saved_ret = OB_SUCC(saved_ret) ? tmp_ret : saved_ret;` puts the first saved error into `ret` on the second failure, which ends the loop of src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:872 and skips the erasures of :913-917; `saved_ret = saved_ret.and(tmp_ret);` would go on through the loop and erase them. src/sql/engine/table/ob_table_scan_op.cpp:1817 does the same through `first_fail_ret`, and there it can overwrite an earlier error in `ret` with `OB_SUCCESS`, since that loop (src/sql/engine/table/ob_table_scan_op.cpp:1791) does not test `ret`. The rule's words, "a merge whose C++ means 'a unless a is success, then b'" (s2-errors.md 2.4 rule 4), do not cover a line that also assigns `ret`, so this file makes such a line MERGE_AS_WRITTEN with the hidden assignment kept, `saved_ret = if ob_succ!(ret = saved_ret) { tmp_ret } else { saved_ret };`, which gives the C++ behavior under either reading. For the next design amendment: s2-errors.md 2.3's row should say that `OB_SUCC(a)` and `OB_FAIL(a)` spell the test only when `a` is `ret`.

## 10. Sweep gaps and findings for the orchestrator

The first four items are lines of the continue-and-record idiom that no sweep list holds (each looked up by file and line in every file of migration/inventory/sweep/; commands in section 11). They need rows, and the value each would take under this file is given. The last two items are a reading the list's own rows need and a C++ defect in rows the list does hold.

- **17 hidden merges.** `OB_SUCC(tmp_ret)` and `OB_FAIL(tmp_ret)` (and the same with `temp_ret`) assign `ret` (src/oblib/lib/utility/ob_macro_utils.h:654, :655), for example src/sql/engine/cmd/ob_table_executor.cpp:339, src/share/schema/ob_table_schema.cpp:908 and src/storage/compaction/ob_compaction_schedule_iterator.cpp:235. The grep finds 18 lines; the 18th, src/sql/optimizer/ob_opt_selectivity.cpp:3642, is in a comment. Value MERGE_AS_WRITTEN: `ob_succ!(ret = tmp_ret)`, `ob_fail!(ret = tmp_ret)`. Writing `tmp_ret.is_ok()` would drop the assignment of `ret`.
- **The idiom through `first_fail_ret`** in `ObTableScanOp::local_iter_reuse`: the declaration at src/sql/engine/table/ob_table_scan_op.cpp:1790, `first_fail_ret = OB_SUCC(first_fail_ret) ? tmp_ret : first_fail_ret;` at :1817, the hidden merge `OB_FAIL(first_fail_ret)` at :1830 and `ret = first_fail_ret;` at :1832. Only the `tmp_ret` rows at :1815 and :1816 are in the list. Values: DECLARATION for :1790; MERGE_AS_WRITTEN for :1817 (Q4), :1830 (`ob_fail!(ret = first_fail_ret)`) and :1832.
- **13 merges spelled `ret = OB_SUCCESS != ret ? ret : X`** (or `ret != OB_SUCCESS`): src/pl/ob_pl.cpp:560, :623; src/share/ob_dml_sql_splicer.cpp:133, :134, :165, :166; src/share/schema/ob_table_schema.cpp:6662, :6680, :6700, :6718, :6736, :6754; src/sql/session/ob_sql_session_info.cpp:1389. The definition lists `ret = OB_SUCCESS == ret ? X : ret` but not this spelling; with `tmp_ret` as X it is in the list (src/oblib/rpc/frame/ob_sql_processor.cpp:44, :49). In 8 of the 13 X is a code constant (`OB_ERR_UNEXPECTED` in the ob_pl.cpp lines, `OB_ERR_INDEX_KEY_NOT_FOUND` in the ob_table_schema.cpp lines): value MERGE_AND, `ret = ret.and(Err(OB_X))` with the line's code. In the other 5 X is a call's result, which the definition does not name (the ob_dml_sql_splicer.cpp lines and ob_sql_session_info.cpp:1389); they are listed because the AND form would be wrong for them: value MERGE_AS_WRITTEN (Q3).
- **1 wrapper use.** `CLICK_TMP_FAIL(rollback_remove_tablets(tablet_id_array))` at src/storage/tablet/ob_tablet_create_mds_helper.cpp:62 assigns `tmp_ret` through `OB_TMP_FAIL` (src/share/ob_occam_time_guard.h:339); the declaration at src/storage/tablet/ob_tablet_create_mds_helper.cpp:61 is a row, the use is not. Value IGNORED: the branch logs, sleeps and aborts. The `#define` row of `CLICK_TMP_FAIL` takes the value of this, its one use. `CLICK()` itself has no rule in the design.
- **2 rows that look AND-shaped but also assign `ret`**: `saved_ret = OB_SUCC(saved_ret) ? tmp_ret : saved_ret;` at src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:894 and :899, held as `assign saved_ret` rows. They are merge lines and MERGE_AS_WRITTEN, not MERGE_AND (section 5, the terms "A merge line" and "AND-shaped"; Q4).
- **6 bare `COVER_SUCC(tmp_ret);` statements** (src/sql/tablelock/ob_lock_executor.cpp:501, :542; src/sql/tablelock/ob_lock_func_executor.cpp:68, :88, :207; src/sql/tablelock/ob_mysql_lock_table_executor.cpp:70) compute the merge and drop it, so a failed `stack_ctx.destroy` never reaches `ret`. They are rows; MERGE_AND gives their `BUG(port)` form, and the `OB_TMP_FAIL` rows whose code reaches only them are IGNORED (6.14).

## 11. How the counts were made

Run from /Users/colin/seekdb-dev/migrate-to-rust; `S=migration/inventory/sweep`.

- Rows and files: `awk -F'\t' 'NR>1' $S/tmp-ret.tsv | wc -l` (3383); `awk -F'\t' 'NR>1{print $1}' $S/tmp-ret.tsv | sort -u | wc -l` (534).
- Construct kinds: `awk -F'\t' 'NR>1{c=$4; sub(/ \[not built\]$/,"",c); if (c ~ /^declare/) k="declare"; else if (c ~ /^OB_TMP_FAIL/) k="OB_TMP_FAIL"; else if (c ~ /^assign .*; merge/) k="assign+merge"; else if (c ~ /^assign/) k="assign"; else k="merge"; n[k]++} END{for (k in n) print n[k], k}' $S/tmp-ret.tsv`; continue-and-record rows: `awk -F'\t' 'NR>1 && $4 ~ /continue-and-record/' $S/tmp-ret.tsv | wc -l` (176); declared `warning` variables: `awk -F'\t' 'NR>1 && $4 ~ /^declare warning/' $S/tmp-ret.tsv | wc -l` (7).
- Units: this script (a ranged entry `file:a-b` covers only its lines):

  ```
  python3 - <<'EOF'
  import csv, re
  def rd(p):
      r = csv.reader(open(p), delimiter='\t', quoting=csv.QUOTE_NONE); next(r); return list(r)
  def spans(p, col):
      s = {}
      for row in rd(p):
          for f in row[col].split(','):
              m = re.match(r'(.*):(\d+)-(\d+)$', f.strip())
              s.setdefault(m.group(1) if m else f.strip(), []).append((int(m.group(2)), int(m.group(3))) if m else (0, 10**9))
      return s
  core, step3 = spans('migration/core-manifest.tsv', 4), spans('migration/manifest.tsv', 4)
  nt = {f.strip(): row[1] for row in rd('migration/not-translated.tsv') for f in row[2].split(',')}
  hit = lambda s, f, l: any(a <= l <= b for a, b in s.get(f, []))
  n = {}
  for row in rd('migration/inventory/sweep/tmp-ret.tsv'):
      f, l = row[0], int(row[1])
      k = 'not-translated ' + nt[f] if f in nt else 'core' if hit(core, f, l) else 'step3' if hit(step3, f, l) else 'none'
      k += ' [not built]' if '[not built]' in row[3] else ''
      n[k] = n.get(k, 0) + 1
  print(n)
  EOF
  ```

  It prints 924 core, 2384 step3, 36 dead (30 of them `[not built]`), 9 island and 30 deferred.
- Merge keys: `awk -F'\t' 'NR>1{print $1"\t"$3"\t"$4}' $S/tmp-ret.tsv | sort | uniq -d | wc -l` (361); the same with `uniq -D` (941 rows); `awk -F'\t' 'NR>1{print $1"\t"$2}' $S/tmp-ret.tsv | sort | uniq -d | wc -l` (0).
- REPLACED rows: `awk -F'\t' 'NR>1 && $1 ~ /^src\/oblib\/lib\/lock\//' $S/tmp-ret.tsv | wc -l` (33); `awk -F'\t' 'NR>1 && $3 ~ /^ObCond::/' $S/tmp-ret.tsv | wc -l` (2); `awk -F'\t' 'NR>1 && $5 ~ /global_hazard_station_/' $S/tmp-ret.tsv | wc -l` (4); `awk -F'\t' 'NR>1 && $1 == "src/share/cache/ob_kvcache_store.cpp" && ($2 == 1001 || $2 == 1004)' $S/tmp-ret.tsv | wc -l` (2); `awk -F'\t' 'NR>1 && ($3 == "ObSliceAlloc::alloc" || $3 == "ObVSliceAlloc::alloc")' $S/tmp-ret.tsv | wc -l` (5); the unlocks that become a guard's drop: `awk -F'\t' 'NR>1 && (($1 == "src/rootserver/ob_local_management_service.cpp" && ($2 == 2318 || $2 == 2319)) || ($1 == "src/storage/blocksstable/ob_block_manager.cpp" && $2 == 1769))' $S/tmp-ret.tsv | wc -l` (3). Rows that call into hazard or retire code, to check that list: `awk -F'\t' 'NR>1 && tolower($5) ~ /hazard|retire|qclock|qsync/' $S/tmp-ret.tsv` (6 lines: the 4 `retire()` rows, src/share/cache/ob_kvcache_store.cpp:1004, and src/share/cache/ob_kvcache_map.cpp:340, whose `internal_data_move` copies a hot entry into a new block under the bucket lock, a step of the cache's own policy, and only at its end hands the old node to the hazard station, src/share/cache/ob_kvcache_map.cpp:788; that row is not REPLACED).
- No code becomes a warning: `git grep -nP '(LOG_USER_WARN|LOG_USER_NOTE|FORWARD_USER_WARN|append_warning|add_warning|set_warning)\w*\s*\(\s*(tmp_ret|temp_ret)' 834bbee1e -- src` and `git grep -nP '\bwarning\s*=\s*(tmp_ret|temp_ret)\b' 834bbee1e -- src` print nothing.
- Conditional merges: this scan prints `292 conditional merge lines; 0 with a call in a branch`:

  ```
  python3 - <<'EOF'
  import csv, re
  n = bad = 0
  for r in list(csv.reader(open('migration/inventory/sweep/tmp-ret.tsv'), delimiter='\t', quoting=csv.QUOTE_NONE))[1:]:
      t = r[4]
      if '?' not in t or not ('merge' in r[3] or re.search(r'\b\w+\s*=\s*\w+\s*==\s*OB_SUCCESS\s*\?', t)): continue
      n += 1
      rest, d = t.split('?', 1)[1], 0
      for j, ch in enumerate(rest):
          d += (ch == '(') - (ch == ')')
          if ch == ':' and d == 0 and rest[j-1:j] != ':' and rest[j+1:j+2] != ':': break
      else:
          bad += 1; print('no branch split:', r[0], r[1]); continue
      if re.search(r'\w\s*\(', rest[:j]) or re.search(r'\w\s*\(', rest[j+1:].split(';')[0]):
          bad += 1; print('call in a branch:', r[0], r[1], t)
  print(n, 'conditional merge lines;', bad, 'with a call in a branch')
  EOF
  ```
- Hidden merges: `git grep -nP '\bOB_(SUCC|FAIL)\s*\(\s*(tmp_ret|temp_ret)\s*\)' 834bbee1e -- src` (18 lines, one in a comment); reversed merges: `git grep -nP '\bret\s*=\s*\(?\s*(\(?\s*(common::|::oceanbase::common::)?OB_SUCCESS\s*!=\s*ret\s*\)?|\(?\s*ret\s*!=\s*(common::)?OB_SUCCESS\s*\)?|OB_FAIL\(ret\))\s*\)?\s*\?\s*ret\s*:' 834bbee1e -- src` (15 lines, 2 of them in the list). To print the lines no sweep list holds, pipe either grep (or `git grep -n 'CLICK_TMP_FAIL(' 834bbee1e -- src | grep -v '#define'`) into `sed 's/^834bbee1e://' | while IFS=: read f l rest; do grep -qP "^\Q$f\E\t$l\t" $S/*.tsv || echo "$f:$l"; done`: it prints 18, 13 and 1 lines.
- Hidden merges through another variable that `tmp_ret` feeds: this prints src/rootserver/ddl_task/ob_ddl_common_rs_impl.cpp:894 and :899, which are rows of the list, and src/sql/engine/table/ob_table_scan_op.cpp:1817 and :1830, which are not: `git grep -nP '\bOB_(SUCC|FAIL)\s*\(\s*(?!(ret|tmp_ret|temp_ret)\s*\))[a-z_]\w*\s*\)' 834bbee1e -- src | sed 's/^834bbee1e://' | grep -vP '^[^:]+:\d+:\s*//' | while IFS=: read f l rest; do v=$(echo "$rest" | grep -oP 'OB_(SUCC|FAIL)\s*\(\s*\K[a-z_]\w*(?=\s*\))' | head -1); grep -qP "\b$v\s*=[^=;]*\b(tmp_ret|temp_ret)\b" "$f" && echo "$f:$l $v"; done`. No sweep list holds the `first_fail_ret` lines: `grep -P '^src/sql/engine/table/ob_table_scan_op\.cpp\t(1790|1817|1830|1832)\t' $S/*.tsv` prints nothing.
- Bare `COVER_SUCC`: `git grep -nP '^\s*COVER_SUCC\s*\(' 834bbee1e -- src | wc -l` (6), all 6 in the list (`awk -F'\t' 'NR>1 && $5 ~ /^COVER_SUCC\(/' $S/tmp-ret.tsv | wc -l`).
- Lock and reference-count calls (section 5): the block manager's count, `awk -F'\t' 'NR>1 && ($5 ~ /OB_(STORAGE_OBJECT|SERVER_BLOCK)_MGR[ \t]*\.[ \t]*(inc|dec)_ref[ \t]*\(/ || ($1 == "src/storage/blocksstable/ob_block_manager.cpp" && $5 ~ /(^|[^a-z_.>])(inc|dec)_ref[ \t]*\(/))' $S/tmp-ret.tsv | wc -l` (9); lock calls outside the lock classes, `awk -F'\t' 'NR>1 && $1 !~ /^src\/oblib\/lib\/lock\// && $5 ~ /(\.|->)[ \t]*(lock|trylock|unlock|wrlock|rdlock|try_lock|try_wrlock|try_rdlock)[ \t]*\(|(try_lock_query|unlock_query|lock_thread_data)[ \t]*\(/ {print $1":"$2}' $S/tmp-ret.tsv` (4 lines: src/rootserver/ob_local_management_service.cpp:2319 on an `ObLatch`, src/sql/engine/px/ob_px_sqc_handler.cpp:262 and :318 on the `ObRecursiveMutex` `query_mutex_`, src/storage/blocksstable/ob_block_manager.cpp:1769 on a `lib::ObMutex`); a guard's lock result, `awk -F'\t' 'NR>1 && $5 ~ /(guard|lock)[A-Za-z_]*\.get_ret[ \t]*\(/ {print $1":"$2}' $S/tmp-ret.tsv` (1 line, src/share/cache/ob_kvcache_map.cpp:333); transaction-context releases, `awk -F'\t' 'NR>1 && $5 ~ /revert_tx_ctx(_without_lock)?[ \t]*\(/' $S/tmp-ret.tsv | wc -l` (8).
- Worked examples: `awk '/^## 8\./{f=1} /^## 9\./{f=0} f && /\t/' migration/inventory/vocabulary/tmp-ret.md > /tmp/tmp-ret-examples.tsv; python3 -B migration/scripts/cite_check.py /tmp/tmp-ret-examples.tsv` prints nothing, and with `show` after the file name it puts every citation on the line the row names.
- Pre-classification (section 14): this script prints the counts of section 14 (`check-step-3` marks a proposal whose function writes the variable from a tracepoint, an OS or printf call or a comparison), and with `check` it compares its proposals with the rows this file classifies by reading, the sites of section 6 and the tables of sections 12 and 13:

  ```
  python3 - check <<'EOF'
  import csv, re, sys
  def rd(p):
      r = csv.reader(open(p), delimiter='\t', quoting=csv.QUOTE_NONE); next(r); return list(r)
  nt = {}
  for row in rd('migration/not-translated.tsv'):
      for f in row[2].split(','):
          m = re.match(r'(.*):(\d+)-(\d+)$', f.strip())
          nt.setdefault(m.group(1) if m else f.strip(), []).append((int(m.group(2)), int(m.group(3))) if m else (0, 10**9))
  S = r'(?:(?:::)?(?:oceanbase::)?(?:common::)?)OB_SUCCESS'; V = r'[A-Za-z_]\w*'; B = r'(?:(?:common::)?OB_[A-Z0-9_]+|[A-Za-z_]\w*)'
  TESTS = [(S + r'\s*==\s*(?P<a>%s)' % V, 1), (r'(?P<a>%s)\s*==\s*' % V + S, 1), (S + r'\s*!=\s*(?P<a>%s)' % V, 0),
           (r'(?P<a>%s)\s*!=\s*' % V + S, 0), (r'OB_SUCC\s*\(\s*(?P<a>ret)\s*\)', 1), (r'OB_FAIL\s*\(\s*(?P<a>ret)\s*\)', 0)]
  AND = [re.compile(r'^\s*(?:return\s+|(?:int\s+)?\*?(?P<t>%s)\s*=\s*)\(?\s*\(?\s*' % V + c + r'\s*\)?\s*\?\s*'
                    + (r'(?P<b>%s)\s*:\s*(?P<a2>%s)' % (B, V) if eq else r'(?P<a2>%s)\s*:\s*(?P<b>%s)' % (V, B)) + r'\s*\)?\s*;$')
         for c, eq in TESTS]
  COVER = re.compile(r'^\s*(?:return\s+|\*?%s\s*=\s*)?COVER_SUCC\s*\(\s*%s\s*\)\s*;$' % (V, B))
  HIDDEN = re.compile(r'\bOB_(?:SUCC|FAIL)\s*\(\s*(?:(?!ret\s*\))%s\s*\)|\(?\s*%s\s*=[^=])' % (V, V))
  DECL = re.compile(r'^\s*(?:const\s+)?(?:int|int32_t|int64_t)\s+(?:%s\s*=\s*%s\s*,\s*)?%s\s*(?:=\s*\(?\s*%s\s*\)?\s*)?;$'
                    r'|^\s*INIT_SUCC\s*\(\s*%s\s*\)\s*;$|[(,]\s*(?:const\s+)?int\s*&?\s*%s\s*(?:[,)]|$)' % (V, S, V, S, V, V))
  KEYS = {('src/share/cache/ob_kvcache_store.cpp', 1001), ('src/share/cache/ob_kvcache_store.cpp', 1004),
          ('src/oblib/lib/utility/ob_macro_utils.h', 656), ('src/oblib/lib/utility/ob_macro_utils.h', 657),
          ('src/oblib/lib/utility/ob_smart_var.h', 171), ('src/oblib/lib/utility/ob_smart_var.h', 172),
          ('src/rootserver/ob_local_management_service.cpp', 2318), ('src/rootserver/ob_local_management_service.cpp', 2319),
          ('src/storage/blocksstable/ob_block_manager.cpp', 1769)}
  NONCODE = re.compile(r'OB_E\s*\(|\bEN_[A-Z0-9_]+\b|snprintf|pthread_\w+\s*\(|\bio_(?:setup|submit|getevents)\s*\(|\berrno\b|cmp|compare|\babs\s*\(')
  def and_shaped(t, var=None):
      if COVER.match(t): return var is None
      return any(m and m.group('a') == m.group('a2') and var in (None, m.group('t')) for m in (r.match(t) for r in AND))
  def propose(f, l, sym, con, text):
      t = re.sub(r'/\*.*?\*/|//.*$', '', text).rstrip().rstrip('\\').rstrip()
      m = re.match(r'(?:declare|assign) (\w+)', con); var = m.group(1) if m else 'tmp_ret'
      if any(a <= l <= b for a, b in nt.get(f, [])): return 'NOT_TRANSLATED', var
      if (f.startswith('src/oblib/lib/lock/') or (f, l) in KEYS or 'global_hazard_station_' in text
              or sym in ('ObCond::wait', 'ObCond::timedwait', 'ObSliceAlloc::alloc', 'ObVSliceAlloc::alloc')): return 'REPLACED', var
      if 'OB_ALLOCATE_MEMORY_FAILED' in text: return None, var
      if 'merge' in con or (HIDDEN.search(t) and ('?' in t or '=' in t.split('OB_', 1)[1])) or (con.startswith('assign') and and_shaped(t, var)):
          if re.search(r'\b[a-z]\w*_\b(?!\s*\()|->|\w\.\w', re.sub(r'\bOB_\w+', '', t)): return None, var
          return ('MERGE_AS_WRITTEN' if HIDDEN.search(t) or not and_shaped(t) else 'MERGE_AND'), var
      if re.match(r'^\s*\}?\s*while\s*\(\s*OB_TMP_FAIL\s*\(', t): return 'RETRY', var
      if con.startswith('declare') and DECL.search(t): return 'DECLARATION', var
      return None, var
  rows = rd('migration/inventory/sweep/tmp-ret.tsv')
  res = [propose(r[0], int(r[1]), r[2], r[3], r[4]) for r in rows]
  risky = {(r[0], r[2], v) for r, (p, v) in zip(rows, res) if NONCODE.search(r[4]) and 'merge' not in r[3]}
  n = {}
  for r, (p, v) in zip(rows, res):
      k = (p or 'read') + (' check-step-3' if p and p not in ('NOT_TRANSLATED', 'REPLACED') and (r[0], r[2], v) in risky else '')
      n[k] = n.get(k, 0) + 1
  print(sorted(n.items()))
  if sys.argv[1:] == ['check']:
      md = open('migration/inventory/vocabulary/tmp-ret.md').read()
      at = {(r[0], int(r[1])): i for i, r in enumerate(rows)}
      hand = {int(m.group(1)) - 2: m.group(2) for m in re.finditer(r'^\| (\d+) \| src/\S+ \| ([A-Z_]+) \|', md, re.M)}
      for sec in re.split(r'^### 6\.\d+ ', md, flags=re.M)[1:]:
          m = re.search(r'\*\*Sites?\.\*\* (.*)', sec)
          for s in re.finditer(r'(?:^|; )(src/[\w/.+-]+):(\d+)', re.sub(r'\([^()]*(?:\([^()]*\)[^()]*)*\)', '', m.group(1)) if m else ''):
              if (s.group(1), int(s.group(2))) in at: hand[at[(s.group(1), int(s.group(2)))]] = sec.split('\n', 1)[0].strip()
      c = {}
      for i, v in hand.items():
          p = res[i][0]; k = 'none' if p is None else 'same' if p == v else 'DIFFERENT %s:%s %s %s' % (rows[i][0], rows[i][1], p, v)
          c[k] = c.get(k, 0) + 1
      print(len(hand), 'rows classified by reading:', sorted(c.items()))
  EOF
  ```

## 12. Rows read while writing this file

Every 75th data row of the list (`awk 'NR>1 && NR % 75 == 3' migration/inventory/sweep/tmp-ret.tsv`), 46 rows in 44 files, each read at its site, with the value this file gives it. The sites quoted under the values of section 6 were read too.

| tmp-ret.tsv | Site | Value | Why |
|---|---|---|---|
| 3 | src/logservice/applyservice/ob_log_apply_service.cpp:1232 | IGNORED | a failed push-back is logged and the status reverted; the function returns void |
| 78 | src/oblib/common/mysqlclient/ob_mysql_transaction.cpp:179 | IGNORED | the failure turns the commit into a rollback |
| 153 | src/oblib/lib/lock/ob_drw_lock.h:141 | REPLACED | `DRWLock` is an OB lock class |
| 228 | src/oblib/rpc/frame/ob_sql_processor.cpp:49 | MERGE_AND | example 2 |
| 303 | src/observer/mysql/obmp_query.cpp:437 | DECLARATION | assigned at obmp_query.cpp:439 and never read |
| 378 | src/observer/ob_service.cpp:601 | STORED | example 1 |
| 453 | src/observer/vector_index/ob_plugin_vector_index_scheduler.cpp:971 | DECLARATION | |
| 528 | src/observer/virtual_table/ob_all_virtual_ps_item_info.cpp:178 | MERGE_AS_WRITTEN | `ret = tmp_ret;` in the failure branch after the `OB_HASH_NOT_EXIST` test of ob_all_virtual_ps_item_info.cpp:176 |
| 603 | src/pl/ob_pl.cpp:635 | MERGE_AND | into the `int &ret` parameter |
| 678 | src/rootserver/ddl_task/ob_constraint_task.cpp:824 | MERGE_AND | inside `if (OB_SUCCESS != tmp_ret)` |
| 753 | src/rootserver/ddl_task/ob_drop_fts_index_task.cpp:320 | IGNORED | the code only decides the task status (ob_drop_fts_index_task.cpp:324) |
| 828 | src/rootserver/freeze/ob_major_merge_progress_checker.cpp:835 | IGNORED | logged |
| 903 | src/rootserver/ob_ddl_service.cpp:13372 | DECLARATION | |
| 978 | src/rootserver/ob_ddl_service.cpp:18354 | IGNORED | never read before its block ends (ob_ddl_service.cpp:18358) |
| 1053 | src/rootserver/ob_ddl_service.cpp:20173 | MERGE_AS_WRITTEN | under `if (OB_SUCC(ret) && tmp_ret != OB_SUCCESS)` |
| 1128 | src/rootserver/ob_index_builder.cpp:160 | MERGE_AND | |
| 1203 | src/rootserver/pl_ddl/ob_pl_ddl_service.cpp:548 | MERGE_AND | |
| 1278 | src/share/ob_autoincrement_service.cpp:1463 | MERGE_AS_WRITTEN | the code left after the `OB_ITER_END` test of ob_autoincrement_service.cpp:1459 |
| 1353 | src/share/schema/ob_multi_version_schema_service.cpp:658 | IGNORED | an empty branch |
| 1428 | src/share/session/ob_local_session_var.cpp:180 | DECLARATION | in a `bool operator==` that has no `ret` |
| 1503 | src/sql/das/iter/ob_das_ivf_scan_iter.cpp:2663 | OTHER_CODE | `OB_EAGAIN` at ob_das_ivf_scan_iter.cpp:2664 marks the cache unusable, and nothing repeats the write |
| 1578 | src/sql/dtl/ob_dtl_flow_control.cpp:121 | PASSED_ON | merged at ob_dtl_flow_control.cpp:122 |
| 1653 | src/sql/engine/cmd/ob_merge_table_executor.cpp:117 | MERGE_AS_WRITTEN | |
| 1728 | src/sql/engine/expr/ob_expr_int_div.cpp:51 | PASSED_ON | a macro body; merged at ob_expr_int_div.cpp:57 |
| 1803 | src/sql/engine/ob_operator.cpp:1272 | MERGE_AS_WRITTEN | |
| 1878 | src/sql/engine/px/ob_px_util.cpp:1389 | PASSED_ON | merged at ob_px_util.cpp:1390, over any earlier error |
| 1953 | src/sql/ob_sql.cpp:2780 | IGNORED | an empty branch |
| 2028 | src/sql/optimizer/stat/ob_dbms_stats_executor.cpp:1400 | PASSED_ON | |
| 2103 | src/sql/optimizer/stat/ob_opt_stat_sql_service.cpp:752 | IGNORED | a rollback whose failure is dropped |
| 2178 | src/sql/resolver/ddl/ob_ddl_resolver.cpp:1339 | IGNORED | the failure falls back to a shorter user message (ob_ddl_resolver.cpp:1340) |
| 2253 | src/sql/session/ob_sql_session_mgr.cpp:427 | IGNORED | an empty branch |
| 2328 | src/storage/access/ob_multiple_merge.cpp:200 | LOOKUP_OUTCOME | the reset after the miss (example 3) |
| 2403 | src/storage/blocksstable/ob_storage_object_handle.cpp:209 | IGNORED | an empty branch |
| 2478 | src/storage/compaction/ob_compaction_schedule_iterator.cpp:234 | PASSED_ON | the hidden merge `OB_SUCC(tmp_ret)` at ob_compaction_schedule_iterator.cpp:235 |
| 2553 | src/storage/compaction/ob_partition_merger.cpp:908 | IGNORED | an empty branch |
| 2628 | src/storage/compaction/ob_tablet_scheduler.cpp:1136 | IGNORED | only logged (ob_tablet_scheduler.cpp:1147) |
| 2703 | src/storage/lob/ob_lob_util.cpp:87 | MERGE_AS_WRITTEN | |
| 2778 | src/storage/ls/ob_ls_tx_service.cpp:607 | DECLARATION | |
| 2853 | src/storage/meta_mem/ob_tablet_meta_mem_mgr.cpp:1728 | NOT_TRANSLATED | a dead unit, `[not built]` |
| 2928 | src/storage/scheduler/ob_dag_scheduler.cpp:1600 | IGNORED | logged with `COMMON_LOG_RET`; the function returns void |
| 3003 | src/storage/slog/ob_storage_logger.cpp:271 | MERGE_AS_WRITTEN | |
| 3078 | src/storage/tablelock/ob_table_lock_service.cpp:991 | PASSED_ON | no named-code test after it; merged at ob_table_lock_service.cpp:998 |
| 3153 | src/storage/tmp_file/ob_shared_nothing_tmp_file.cpp:726 | IGNORED | cleanup after an error |
| 3228 | src/storage/tx/ob_trans_functor.h:696 | IGNORED | the functor returns false |
| 3303 | src/storage/tx/ob_tx_ctx_mds.cpp:112 | LOOKUP_OUTCOME | |
| 3378 | src/storage/tx_table/ob_tx_table.cpp:687 | DECLARATION | |

## 13. Review sample

A separate review drew 40 data rows with `random.Random(3).sample(range(3383), 40)` over the data rows of the list (`python3 -c "import csv,random; r=list(csv.reader(open('migration/inventory/sweep/tmp-ret.tsv'),delimiter='\t',quoting=csv.QUOTE_NONE))[1:]; print(sorted(i+2 for i in random.Random(3).sample(range(len(r)),40)))"` prints the tmp-ret.tsv lines) and read each at its site. Each gets exactly one value under this file. Under the first draft, three were open: line 55 (a tracepoint initializer read as `OB_SUCCESS`, step 5), line 1235 (the hazard-domain reclaim, IGNORED by the enclosing function; now REPLACED, 6.2) and line 1928 (a merge where `ret` already holds an error; now PASSED_ON, 6.14).

| tmp-ret.tsv | Site | Value | Why |
|---|---|---|---|
| 55 | src/logservice/replayservice/ob_log_replay_service.cpp:1168 | IGNORED | a tracepoint used as a switch: the branch sleeps and logs (ob_log_replay_service.cpp:1170); under `#ifdef ERRSIM`; core unit |
| 64 | src/logservice/replayservice/ob_replay_status.cpp:233 | END_OF_DATA | tested against `OB_ITER_END` at ob_replay_status.cpp:235; other codes fall into an empty `else`; core unit |
| 177 | src/oblib/lib/lock/ob_thread_cond.h:83 | REPLACED | `ObThreadCond::signal` |
| 264 | src/observer/dbms_scheduler/ob_dbms_sched_table_operator.cpp:234 | DECLARATION | assigned at ob_dbms_sched_table_operator.cpp:235, merged at :237 |
| 270 | src/observer/dbms_scheduler/ob_dbms_sched_table_operator.cpp:459 | END_OF_DATA | tested against `OB_ITER_END` at ob_dbms_sched_table_operator.cpp:463; a second row is an error (:461) |
| 536 | src/observer/virtual_table/ob_all_virtual_server_compaction_progress.cpp:126 | IGNORED | an empty branch in the body of `SERVER_MODULE_SCOPE` (ob_all_virtual_server_compaction_progress.cpp:125), which adds no clause |
| 618 | src/pl/ob_pl.cpp:2044 | DECLARATION | assigned at ob_pl.cpp:2048 |
| 623 | src/pl/ob_pl_build.cpp:539 | DECLARATION | assigned at ob_pl_build.cpp:547 |
| 654 | src/pl/sys_package/ob_dbms_stats.cpp:242 | PASSED_ON | saves `ret`, restored at ob_dbms_stats.cpp:245 |
| 787 | src/rootserver/ddl_task/ob_vec_index_build_task.cpp:1751 | PASSED_ON | merged at ob_vec_index_build_task.cpp:1754 |
| 951 | src/rootserver/ob_ddl_service.cpp:16178 | DECLARATION | assigned at ob_ddl_service.cpp:16179 |
| 961 | src/rootserver/ob_ddl_service.cpp:17561 | DECLARATION | assigned at ob_ddl_service.cpp:17562 |
| 976 | src/rootserver/ob_ddl_service.cpp:18349 | DECLARATION | assigned at ob_ddl_service.cpp:18353 and :18354 |
| 1064 | src/rootserver/ob_ddl_service.cpp:20548 | PASSED_ON | merged at ob_ddl_service.cpp:20550 |
| 1235 | src/share/cache/ob_kvcache_store.cpp:1004 | REPLACED | the hazard-domain reclaim of `pop_mb_handle_with_recovery` (6.2); core unit |
| 1517 | src/sql/das/ob_das_parallel_handler.cpp:164 | DECLARATION | assigned at ob_das_parallel_handler.cpp:165 and :168 |
| 1599 | src/sql/dtl/ob_dtl_utils.cpp:73 | DECLARATION | assigned at ob_dtl_utils.cpp:90 and :99 |
| 1628 | src/sql/engine/cmd/ob_dcl_executor.cpp:295 | MERGE_AS_WRITTEN | `ret = tmp_ret;` under `if (OB_SUCC(ret))` (Q2) |
| 1923 | src/sql/ob_result_set.cpp:832 | MERGE_AND | `a` is `ret`, `b` is `tmp_ret` |
| 1928 | src/sql/ob_spi.cpp:914 | PASSED_ON | reaches the merge at ob_spi.cpp:919, where `ret` always holds an error (6.14) |
| 1943 | src/sql/ob_spi.cpp:5556 | DECLARATION | assigned at ob_spi.cpp:5557 |
| 1953 | src/sql/ob_sql.cpp:2780 | IGNORED | an empty branch |
| 2144 | src/sql/plan_cache/ob_plan_cache.cpp:2151 | DECLARATION | the tracepoint value is assigned at ob_plan_cache.cpp:2152 |
| 2217 | src/sql/rewrite/ob_transform_utils.cpp:6193 | DECLARATION | assigned at ob_transform_utils.cpp:6194, merged at :6195 |
| 2231 | src/sql/session/ob_basic_session_info.cpp:4633 | MERGE_AND | `ret = COVER_SUCC(tmp_ret);` |
| 2253 | src/sql/session/ob_sql_session_mgr.cpp:427 | IGNORED | an empty branch |
| 2258 | src/sql/session/ob_system_variable.cpp:1820 | DECLARATION | assigned at ob_system_variable.cpp:1824 |
| 2381 | src/storage/blocksstable/ob_micro_block_row_scanner.cpp:1645 | IGNORED | the failure skips the conflict report of ob_micro_block_row_scanner.cpp:1647; core unit |
| 2423 | src/storage/compaction/ob_basic_tablet_merge_ctx.cpp:806 | IGNORED | logged with `LOG_ERROR_RET` (ob_basic_tablet_merge_ctx.cpp:807) |
| 2429 | src/storage/compaction/ob_basic_tablet_merge_ctx.cpp:968 | IGNORED | an empty branch |
| 2475 | src/storage/compaction/ob_compaction_diagnose.h:565 | LOOKUP_OUTCOME | a macro body: `delete_diagnose_tablet` erases by tablet id, and a miss is not logged (ob_compaction_diagnose.h:568) |
| 2482 | src/storage/compaction/ob_compaction_schedule_util.cpp:110 | DECLARATION | assigned at ob_compaction_schedule_util.cpp:111 |
| 2564 | src/storage/compaction/ob_schedule_tablet_func.cpp:69 | IGNORED | sets `need_diagnose` and logs (ob_schedule_tablet_func.cpp:70-71) |
| 2602 | src/storage/compaction/ob_tablet_scheduler.cpp:179 | DECLARATION | assigned at ob_tablet_scheduler.cpp:184 |
| 2619 | src/storage/compaction/ob_tablet_scheduler.cpp:879 | IGNORED | an empty branch |
| 2752 | src/storage/ls/ob_ls_tablet_service.cpp:1223 | IGNORED | an empty branch; core unit |
| 2939 | src/storage/scheduler/ob_dag_scheduler.cpp:2302 | END_OF_DATA | `OB_ITER_END` moves on to the next dag (ob_dag_scheduler.cpp:2303-2304), which is not a retry; other codes go to `ret` (:2306); core unit |
| 3038 | src/storage/tablelock/ob_obj_lock.cpp:623 | MERGE_AND | `ret = ret.and(Err(OB_TIMEOUT));` |
| 3107 | src/storage/tablet/ob_tablet.cpp:2062 | IGNORED | the self-assignment of ob_tablet.cpp:2071 keeps the code and nothing reads it; core unit |
| 3185 | src/storage/tmp_file/ob_tmp_file_flush_priority_manager.cpp:303 | IGNORED | only logged (ob_tmp_file_flush_priority_manager.cpp:304) |

Totals: 14 DECLARATION, 12 IGNORED, 4 PASSED_ON, 3 END_OF_DATA, 3 MERGE_AND, 2 REPLACED, 1 MERGE_AS_WRITTEN, 1 LOOKUP_OUTCOME.

## 14. Pre-classification

For the fan-out, a script can propose a value for 1,784 of the 3,383 rows from the sweep row alone (`file`, `line`, `symbol`, `construct`, `text`) and migration/not-translated.tsv; the other 1,599 always need the code read. The script is the last item of section 11 and prints the counts below. A proposal is not yet a row: the classifier still checks step 3, that the variable (for a merge, each operand) holds only codes, from the writers and readers the value's evidence lists anyway, and writes that evidence. Run with `check`, the script compares its proposals with the 128 rows this file classifies by reading (the sites of section 6 and the tables of sections 12 and 13): 61 get a proposal, each equal to the value given there, and 67 get none.

| Value | Rows | Pattern, tried in this order on the text with comments and a trailing `\` removed |
|---|---|---|
| NOT_TRANSLATED | 75 | the file, or a range of it that holds the line, is in the `files` column of migration/not-translated.tsv (step 1) |
| REPLACED | 53 | the keys of 6.2: a file under src/oblib/lib/lock/; the symbols `ObCond::wait`, `ObCond::timedwait`, `ObSliceAlloc::alloc` and `ObVSliceAlloc::alloc`; a text holding `global_hazard_station_`; src/share/cache/ob_kvcache_store.cpp:1001 and :1004, src/oblib/lib/utility/ob_macro_utils.h:656 and :657, src/oblib/lib/utility/ob_smart_var.h:171 and :172, src/rootserver/ob_local_management_service.cpp:2318 and :2319, src/storage/blocksstable/ob_block_manager.cpp:1769 |
| MERGE_AND | 354 | a merge line that is AND-shaped as section 5 defines it, with `a` and `b` variables that are not fields (`b` may also be an `OB_` constant): `COVER_SUCC(b);`, alone or after `t = ` or `return`, or `t = c ? x : y;` or `return c ? x : y;` with any parentheses, where `c` is `OB_SUCCESS == a` or `a == OB_SUCCESS` (and `x` is `b`, `y` is `a`), `OB_SUCCESS != a` or `a != OB_SUCCESS` (and `x` is `a`, `y` is `b`), with any `::oceanbase::common::` prefix, or `OB_SUCC(ret)` or `OB_FAIL(ret)` with `a` = `ret`. A merge line is a row whose construct holds `merge`; an `assign` row whose text gives its own variable such a value (`tmp_ret = tmp_ret == OB_SUCCESS ? ret : tmp_ret;`, 25 rows); or a text that hides a merge (next row) |
| MERGE_AS_WRITTEN | 259 | any other merge line, and a text that hides one (4 `assign` rows): `OB_SUCC(v)` or `OB_FAIL(v)` on a variable `v` other than `ret` in a conditional, or `OB_FAIL(v = x)` |
| RETRY | 6 | a text that starts with `while (OB_TMP_FAIL(` or `} while (OB_TMP_FAIL(`; for the second, the classifier checks that the loop body produces the code again (6.7) |
| DECLARATION | 1,037 | a `declare` row whose text is `int v;` or `int v = OB_SUCCESS;` (also `const`, `int32_t`, `int64_t`, a namespace prefix or parentheses around `OB_SUCCESS`), `INIT_SUCC(v);`, or a parameter `int v`, `const int v` or `int &v` |

The script proposes nothing for these, because the line alone misleads (they are among the 1,599):

- a text holding `OB_ALLOCATE_MEMORY_FAILED` (8 rows, 3 of them NOT_TRANSLATED or REPLACED first): section 7's -4013 test comes before the steps, and src/oblib/lib/container/ob_id_map.h:173 is AND-shaped and UNKNOWN;
- a merge through a field, a name ending in `_` or reached through `.` or `->` (3 rows): step 3 needs the field's writers, and src/rootserver/ddl_task/ob_ddl_redefinition_task.cpp:458 is NON_CODE_VALUE;
- a declaration initialized to `0` (8 translated rows, 7 of them NON_CODE_VALUE: four comparison results, a `snprintf` length, an `io_setup` and an `io_submit` return);
- every other `assign`, `OB_TMP_FAIL` and `declare` row. RENAMED, END_OF_DATA, LOOKUP_OUTCOME, BUFFER_TOO_SMALL, OTHER_CODE, STORED, PASSED_ON, IGNORED, most RETRY rows and every NON_CODE_VALUE row depend on what the function does with the code or the variable on other lines (the tests, merges, stores, reads and later assignments of section 5's terms), which the row's text does not show. UNKNOWN is found, never proposed.

Step 3 is what can still overturn a proposal. The script marks with `check-step-3` the 24 proposals whose function also writes the variable from a tracepoint, an OS or printf call or a comparison. src/storage/tx_table/ob_tx_data_table.cpp:1186 is one: the pattern proposes DECLARATION for `int tmp_ret = OB_SUCCESS;`, but the row is NON_CODE_VALUE, since the tracepoint value that src/storage/tx_table/ob_tx_data_table.cpp:1188 puts into the variable is used as a number at :1189.

Also exact from the row alone, whatever its value: the first item of `evidence` (`sweep: tmp-ret.tsv:<L> (data row <L-1>)`), the ret-compare.tsv row of the same file and line, the core-unit clause (section 11's unit script), the macro-body clause (`symbol` starts with `#define`), and the rows the clause on lock and reference-count calls names (section 11's commands).
