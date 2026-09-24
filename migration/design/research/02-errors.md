# Research 02: errors

Input to the design document (PLAN.md section 6, Step 1: "the error type, and the rules for error codes used as values"). It covers the `int ret` idiom, the error catalog, codes used as values, `tmp_ret`, `int &ret = ret_;`, and the path by which a code, its SQLSTATE and its message reach the client.

**Base and method.** Every fact is about the frozen base 834bbee1e (`git diff --quiet 834bbee1e -- src` succeeds in /Users/colin/seekdb-dev/migrate-to-rust). Code counts come from four read-only scripts in migration/design/evidence/errors/ (`count_errors.py`, `count_more.py`, `count_cmp.py`, `count_pair.py`), run over one file set: `git ls-files src` with the extensions .h .hpp .cpp .cc .c .ipp .inc, minus the four generated errno files (src/share/ob_errno.cpp, src/share/ob_errno.h, src/oblib/lib/ob_errno.h, src/share/mysql_errno.h), which leaves 6,636 files. Counts are of lines unless they say otherwise. "Core", "SQL tier" and "other" use the directory lists of migration/judge/coverage-076eb309b/coverage_groups.py. Test counts are over the 272 configured cases in migration/judge/census/portable.txt.

## 1. Short answer

- **One error type that is only the code:** a `Copy` newtype over the `i32` OB code, with one generated constant per catalog entry under its C++ name (`OB_ITER_END`), and `Result<T, Error>` everywhere. It accepts any non-zero value, because tracepoints, the C islands, the parser's C core and PL `SIGNAL` produce codes the catalog does not list.
- **Codes used as values stay values.** Translated code returns and tests `Err(OB_ITER_END)` exactly where the C++ returns and tests `OB_ITER_END`. Core APIs that replace a C++ API keep that API's codes at their boundary for the port. `Option`-returning APIs come after parity, as changes of their own.
- **`?` only for the plain `OB_FAIL` chain**, and only when nothing in the C++ function runs after the error. Otherwise the function keeps an explicit `ret` local and the C++ shape.
- **The user-visible message does not travel inside the error.** A Rust copy of `ObWarningBuffer`, with the same limits, is kept per session and reached through a per-thread slot, as today. A `user_error!` macro records the message and yields the code. One function builds the error packet with the C++ rule.
- **One generator** reads ob_errno.def and mysql_errno.h and emits the catalog, the typed argument lists for the 468 formatted messages, and the data of the `ob_error` tool. Messages are formatted with C semantics (byte precision, the 511-byte cut), not with `format!`.
- **Comparators never keep an error in their own state.** They return `Result`, and the port's own sort, heap and search code stops at the first error.

## 2. What the C++ does today

### 2.1 The `int ret` idiom

The macros are in src/oblib/lib/utility/ob_macro_utils.h:654-658 (`OB_SUCC`, `OB_FAIL`, `OB_TMP_FAIL`, `COVER_SUCC`, `INIT_SUCC`). The check macros `CK`, `OX`, `OZ` and `OV` all start with `if (OB_SUCC(ret))` (src/oblib/lib/ob_check_macros.h:55-187), and `CK` sets `OB_ERR_UNEXPECTED`. Log calls at WARN and ERROR read the local variable named `ret`: `#define LOG_ERRCODE_FOR_ERROR ret` and `#define LOG_ERRCODE_FOR_WARN ret` (src/oblib/lib/oblog/ob_log_module.h:261-262). That is why a function with no error to return still declares `int ret`, and why the `*_LOG_RET` macros exist, which declare `int ret = __ret__;` (ob_log_module.h:777).

| Construct | Lines | Files | Core / SQL tier / other |
|---|---|---|---|
| `int ret = OB_SUCCESS;` | 38,256 | 3,169 | 5,732 / 13,211 / 19,313 |
| `OB_FAIL(` | 99,909 | 2,784 | 10,682 / 40,935 / 48,292 |
| `OB_SUCC(` | 31,213 | 2,169 | 3,383 / 13,502 / 14,328 |
| `OB_SUCC(ret) &&` (loop and condition guards) | 17,769 | 1,728 | 1,898 / 8,726 / 7,145 |
| `if (OB_SUCC(ret))` (continue only if no error so far) | 10,359 | 1,671 | |
| `if (OB_FAIL(ret)` (blocks that run because an error happened) | 7,002 | 1,358 | |
| `OZ(` / `CK(` / `OX(` / `OV(` | 5,685 / 2,935 / 2,127 / 341 | 294 / 251 / 165 / 52 | |
| `FALSE_IT(` | 2,591 | 727 | |
| `*_LOG_RET(` (log with an explicit code) | 1,794 | 463 | |
| `ret = OB_ERR_UNEXPECTED` / `OB_ISNULL(` | 27,583 / 30,896 | 2,119 / 2,326 | |
| `ret = OB_ALLOCATE_MEMORY_FAILED` | 4,722 | 1,165 | 498 / 1,421 / 2,803 |
| `ret = OB_NOT_INIT` / `ret = OB_INIT_TWICE` | 4,523 / 989 | 958 / 633 | |

A function runs its steps as an `if (OB_FAIL(a)) {...} else if (OB_FAIL(b)) {...}` chain and returns once, at the end. After the first error, any later statement that is not guarded still runs. The 7,002 `if (OB_FAIL(ret)` lines are cleanup, rollback and logging that exist for that reason. The plan's figures hold at 834bbee1e: 17,769 guards (plan: 17,767) and 2,906 resets (plan: 2,905).

### 2.2 The catalog

- **Source.** src/share/ob_errno.def (1,914 lines) has 1,546 entry lines: 858 `DEFINE_ERROR`, 326 `DEFINE_ERROR_EXT`, 172 `DEFINE_ERROR_DEP` and 190 `DEFINE_ERROR_EXT_DEP`. It also has two `DEFINE_OTHER_MSG_FMT` aliases, which add a second message format under a new name with the same code (lines 250, 612). Each entry carries a name, the OB code, the MySQL number (-1 means "send the OB code"), a SQLSTATE, a message without parameters (`str_error`), and a message with parameters (`str_user_error`, EXT entries only), plus an optional cause and solution (the field list is in the comment at ob_errno.def:35-55).
- **Generator.** src/share/gen_errno.pl (467 lines) writes src/share/ob_errno.h (2,792 lines), src/share/ob_errno.cpp (15,636) and src/oblib/lib/ob_errno.h (396, holding only the `_DEP` entries, so that oblib can see them without share). The build does not run it, and its outputs are checked in (evidence-full.md:1827-1828).
- **Oddities a generator must reproduce.**
  - `OB_ERR_ARGUMENT_SHOULD_CONSTANT_OR_GROUP_EXPR` is defined twice with the same text (ob_errno.def:1868 and 1872). The Perl map is keyed by name, so it keeps one (gen_errno.pl:23-31), which gives 1,545 distinct names and 1,544 non-zero codes, from -4000 down to -38105. `g_all_ob_errnos` has 1,545 entries with 0 (ob_errno.h:2772).
  - Line 575 ends in `;;` and line 702 has no closing `)`. The Perl regexes match on a prefix and accept both.
  - One entry puts an OB name in its MySQL-number field: `OB_ERR_CANNOT_REVOKE_PRIVILEGES_YOU_DID_NOT_GRANT` (-5362) has `OB_ERR_NO_GRANT` (-5133) there (ob_errno.def:792; ob_errno.cpp:6153). `ob_mysql_errno_with_check` turns the negative value into 5362, so the client sees 5362 (ob_errno.cpp:15614-15620).
- **Client numbers.** 1,014 entries send their own code, and 529 send a MySQL number. Those 529 use 457 distinct MySQL numbers, of which 38 are shared by 2 to 11 OB codes (110 codes in all). For example, 4012 is shared by 11 codes and 1064 by `OB_ERR_PARSER_INIT`, `OB_ERR_PARSE_SQL` and `OB_ERR_PARSE_PARTITION_RANGE`. So the identity is the OB code: two errors with the same client number can still differ in text, SQLSTATE and the code paths that test them.
- **SQLSTATE.** There are 77 distinct values. HY000 is used by 1,094 entries and 42000 by 248.
- **Messages.** 468 entries have printf formats, and 454 have a user message that differs from `str_error`. The conversions used are `%.*s` 343, `%s` 202, `%ld` 38, `%u` 30, `%d` 25, `%.192s` 17, `%f` 12, `%-.256s` 10, `%lu` 7, `%.64s` 5, `%c` 4, `%.80s` 3, `%%` 2, `%.20s` 2, `%-.384s` 1 and `%lf` 1. An explicit cause is given on 5 entries and a solution on 1. The rest default to "Internal Error" and "Contact OceanBase Support" (gen_errno.pl:20-21), and the `ob_error` tool prints these texts (tools/ob_error/test/expect_result.result).
- **Ranges** (entries by -code): 4000-4499: 295; 4500-4999: 70; 5000-5999: 846; 6000-6999: 89; 7000-7999: 130; 8000-8999: 3; 9000-9499: 18; 9500-9999: 57; 11000-11999: 31; 12000 and above: 5.

The lookup functions are in ob_errno.cpp:15529-15632. They index a 65,535-slot table filled with a zeroed default entry (ob_errno.cpp:55, 13957-13960):

| Function | Known code | Unknown code in (-65535, 0) | Other values |
|---|---|---|---|
| `ob_error_name` | name | "Unknown Error" | "Unknown error"; 0 gives "OB_SUCCESS" |
| `ob_strerror`, `ob_errpkt_strerror` | `str_error` | "Unknown Error" | "Unknown error" |
| `ob_sqlstate` | SQLSTATE | "HY000" | "HY000" |
| `ob_mysql_errno` | MySQL number or -1 | 0 | -1 |
| `ob_errpkt_errno` (the number sent) | MySQL number, or -code | **0** | a positive value is sent as is; a value of -65535 or below is sent as -value |

oblib declares `ob_strerror` as a weak symbol (src/oblib/lib/oblog/ob_log.cpp:73), and the generated share copy replaces it at link time (ob_errno.cpp:15572). `ob_error_name` is declared in oblib and resolved at link time (src/oblib/lib/oblog/ob_log_user_msg.h:21). In C++ the catalog sits above oblib. A Rust catalog crate at the bottom of the crate graph needs neither trick.

### 2.3 How a code and its message reach the client

**The store is `ObWarningBuffer`** (src/oblib/lib/oblog/ob_warning_buffer.h). It holds:
- one error slot: a message of up to `STR_LEN` = 512 bytes including the terminator (:51), a code, a 5-character SQLSTATE, and a line and column;
- a ring of `MAX_BUFFER_SIZE` = 64 warning and note items with a total count (:141, :183-210).

The worker thread reaches the buffer through a per-thread pointer (:226-270):
- `ObMPBase::setup_wb` points it at the session's buffer (src/observer/mysql/obmp_base.h:122-128);
- it is cleared afterwards (obmp_base.cpp:369), and the session buffer is reset for each statement (obmp_base.cpp:360), or by the callback when the response is sent asynchronously (src/observer/mysql/ob_mysql_end_trans_cb.cpp:273, 307);
- trigger bodies run with their own buffer and copy back only an unhandled error (src/sql/engine/dml/ob_trigger_handler.cpp:444-456);
- `ObWarningBufferIgnoreScope` swaps in a throwaway buffer (ob_warning_buffer.h:275-286);
- PX tasks install a default buffer and forward what it collects (src/sql/engine/px/ob_px_task_process.cpp:100-101, 461).

**The writers** are `LOG_USER_ERROR`, `LOG_USER_WARN` and `LOG_USER_NOTE` (ob_log_module.h:1055-1085). They go through `_LOG_USER_MSG` (:1033-1039) to `ObLogger::log_user_message`, which formats with `vsnprintf` into a 512-byte stack buffer (ob_log.cpp:654-666), and then to `insert_warning_buffer` (ob_log.cpp:1164-1182). An error overwrites the error slot, and a warning or note appends to the ring.
- The code must be a compile-time constant (`CHECK_LOG_USER_CONST_FMT`, ob_log_module.h:1046-1052).
- The format is the `<name>__USER_ERROR_MSG` macro in ob_errno.h.
- `log_user_message` carries `__attribute__((format(printf, 4, 5)))` (ob_log.h:473-475).
- `FORWARD_USER_*` pass a message that is already formatted (ob_log_module.h:1098-1113).

| Writer | Calls | Distinct codes |
|---|---|---|
| `LOG_USER_ERROR` | 2,799 | 367 |
| `LOG_USER_WARN` | 127 | 58 |
| `LOG_USER_NOTE` | 8 | 7 |
| `LOG_MYSQL_USER_*` / `FORWARD_USER_*` / `LOG_USER_ERROR_WITH_LINE_COL` | 14 / 30 / 1 | |

Of the 2,786 `LOG_USER_ERROR` calls outside comments, 2,630 set or test the same code within 4 lines (count_pair.py). 135 have no code assignment nearby, and 21 set a different code nearby.

**The error packet** is built by `ObMPPacketSender::send_error_packet` (src/observer/mysql/obmp_packet_sender.cpp:527-700):
1. **Message.** It is the buffer's error message if the buffer's code equals the final code, or if the code is `OB_ERR_SIGNAL_EXCEPTION` (:555-559). Otherwise it is the `errmsg` argument, and failing that `str_error` (:561-568). A message recorded for code A is therefore dropped when the statement ends with code B.
2. It is converted from utf8mb4 to the connection collation, replacing unknown characters, and cut to 512 bytes (:572-590).
3. If `enable_rich_error_msg` is on (default false, src/share/parameter/ob_parameter_seed.ipp:122), a time and a trace id are appended (:594-630).
4. **Number.** `static_cast<uint16_t>(ob_errpkt_errno(code))` (:651). For `OB_ERR_SIGNAL_EXCEPTION`, the buffer's own code and SQLSTATE are used instead (:642-649).
5. **SQLSTATE.** `ob_sqlstate(code)` (:652).

**SHOW WARNINGS.** At the end of a statement, `set_show_warnings_buf` copies the buffer (src/sql/session/ob_sql_session_info.cpp:660-672). If the statement failed and nothing recorded a message, it records `str_error` under the final code. If a message is already there, it keeps that message, even when its code differs from the final one. The virtual table prints the error row first, with `ob_errpkt_errno(code)` in the Code column (src/observer/virtual_table/ob_virtual_warning.cpp:81-175).

**Other readers of the catalog at run time:**
- PL handler matching (src/pl/ob_pl_interpreter.cpp:133-134);
- `GET DIAGNOSTICS` (src/sql/engine/cmd/ob_get_diagnostics_executor.cpp:143-219);
- `SQLCODE`/`SQLERRM` (src/sql/engine/expr/ob_expr_pl_sqlcode_sqlerrm.cpp:121-153);
- the syntax-error text, which is built as `LOG_USER_ERROR(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX), ...)` (src/sql/parser/ob_parser.cpp:892, 1129; src/sql/ob_sql.cpp:3243);
- DDL error messages written to a table (src/share/ob_ddl_error_message_table_operator.cpp:389-390).

### 2.4 Codes used as values

- **`ret`.** 5,718 lines in 1,155 files compare `ret` with a specific catalog code (count_cmp.py): 3,367 with `==` and 2,351 with `!=`.
- **Other variables.** 697 more lines compare another variable with a code, for example `hash_ret` 258, `ret_code` 128, `err` 68, `ret_` 64 and `warning` 33. In all, 6,415 lines.
- **The plan's 4,189.** The feasibility regex `\bOB_\w+\s*==\s*ret\b|\bret\s*==\s*OB_(?!SUCCESS)\w+` gives 4,192 lines here (4,189 at 076eb309b). 847 of those compare only with `OB_SUCCESS`, and the `!=` variant gives 2,498.
- **Distribution.** 347 distinct codes are compared somewhere. `OB_ITER_END` alone accounts for 32.3% of the (line, code) pairs, the top 5 codes for 55.9%, the top 10 for 64.6%, the top 50 for 83.9% and the top 100 for 91.3%. 108 codes are compared on one line only.

| Code | Lines naming it | Comparison lines | `==` then `ret = OB_SUCCESS` within 3 lines | `==` then another code within 3 lines | `ret = <code>;` lines | Files with comparisons |
|---|---|---|---|---|---|---|
| OB_ITER_END | 3,190 | 2,178 | 532 | 90 | 822 | 545 |
| OB_HASH_NOT_EXIST | 647 | 544 | 181 | 48 | 42 | 229 |
| OB_ENTRY_NOT_EXIST | 827 | 469 | 143 | 60 | 302 | 201 |
| OB_EAGAIN | 899 | 347 | 50 | 19 | 394 | 154 |
| OB_HASH_EXIST | 305 | 236 | 74 | 30 | 24 | 101 |
| OB_SIZE_OVERFLOW | 648 | 141 | 43 | 7 | 469 | 82 |
| OB_TRY_LOCK_ROW_CONFLICT | 175 | 132 | 4 | 4 | 14 | 46 |
| OB_BUF_NOT_ENOUGH | 435 | 116 | 7 | 7 | 303 | 37 |
| OB_TIMEOUT | 325 | 109 | 10 | 40 | 141 | 60 |
| OB_TABLET_NOT_EXIST | 133 | 93 | 27 | 8 | 33 | 42 |
| OB_SEARCH_NOT_FOUND | 113 | 90 | 60 | 1 | 20 | 12 |
| OB_ERR_NULL_VALUE | 497 | 83 | 45 | 1 | 392 | 23 |
| OB_ALLOCATE_MEMORY_FAILED | 4,971 | 78 | 6 | 18 | 4,722 | 47 |
| OB_SNAPSHOT_DISCARDED | 87 | 62 | 1 | 13 | 21 | 21 |
| OB_TABLE_NOT_EXIST | 472 | 61 | 18 | 10 | 353 | 25 |
| OB_ENTRY_EXIST | 114 | 60 | 22 | 3 | 48 | 28 |

The five codes of evidence-full.md:3221 (`OB_ITER_END`, `OB_ENTRY_NOT_EXIST`, `OB_HASH_NOT_EXIST`, `OB_HASH_EXIST`, `OB_EAGAIN`) are named on 5,868 lines here, the same figure as there. That sum is the plan's "about 6,000".

What the values mean at the call sites:
- **End of data.** `OB_ITER_END` comes from `get_next_row` / `inner_get_next_row` (150 and 279 lines that declare them) and other iterators. Most callers either stop and reset it, or pass it upward unchanged. Its 1,235 `!=` lines are mostly "log unless this is the normal end".
- **Lookup outcomes.** `OB_HASH_NOT_EXIST`, `OB_HASH_EXIST`, `OB_ENTRY_NOT_EXIST`, `OB_ENTRY_EXIST` and `OB_SEARCH_NOT_FOUND` come from the hash maps (`get_refactored` on 453 lines, `set_refactored` 648, `erase_refactored` 185) and other lookups. They are often renamed, for example `OB_HASH_NOT_EXIST` into `OB_ENTRY_NOT_EXIST` on 48 lines.
- **Buffer or capacity too small.** `OB_SIZE_OVERFLOW` and `OB_BUF_NOT_ENOUGH` come from `databuff_printf` (1,995 lines) and fixed arrays, and callers grow the buffer and retry. CREATE VIEW, for example, doubles its print buffer on `OB_SIZE_OVERFLOW` (evidence-full.md:739). `SMART_CALL` (1,131 lines) returns `OB_SIZE_OVERFLOW` too, when the stack it has added would pass `ALL_STACK_LIMIT` (src/oblib/lib/utility/ob_smart_call.h:82-83).
- **Retry and wait.** In the transaction and log area alone, `OB_EAGAIN` drives 239 retry loops (evidence-full.md:970, 1117). `ObQueryRetryCtrl` registers 64 codes in a hash map keyed by code (src/sql/ob_query_retry_ctrl.cpp:795-900). 22 predicates in src/share/ob_define.h:71-255, such as `is_schema_error` and `is_try_lock_row_err`, sort codes into groups.
- **Errors that become warnings.** Casts in the warn-on-fail mode keep the code in a `warning` variable and reset `ret` (src/sql/engine/expr/ob_datum_cast.cpp:166-168, 1433-1436). `CM_IS_WARN_ON_FAIL` appears on 75 lines in 19 files.
- **`switch` on codes** is rare: 107 `case` labels in 9 files name catalog codes.

### 2.5 Resets and `tmp_ret`

**Resets.** There are 2,906 whole-line `ret = OB_SUCCESS;` resets in 847 files. Judged by the three lines above each one (count_more.py), so approximately:
- 1,554 follow a test for a specific code;
- 549 follow a general failure test, where the error is logged and dropped;
- 803 are other cases, such as initialisation and loop restarts.

**`tmp_ret`.** `int tmp_ret = OB_SUCCESS` appears on 931 lines (394 files) and `OB_TMP_FAIL(` on 631 (184 files); together these are the plan's "about 1,560". 3,983 lines in 487 files mention either.
- Merges back into `ret`: `ret = tmp_ret;` on 204 lines, `ret = OB_SUCC(ret) ? tmp_ret : ret` and its variants on 111, and `COVER_SUCC(` on 75. `COVER_SUCC(e)` is `(OB_SUCCESS == ret ? e : ret)` (ob_macro_utils.h:657).
- The ternary and `COVER_SUCC` forms keep the first error. The plain `ret = tmp_ret;` lines keep or overwrite depending on their guards, so they are translated as written.
- The other `tmp_ret` sites only log.

### 2.6 `int &ret = ret_;`

This appears on 31 lines in 18 files, the plan's figure. With the other member names (`error_ret_`, `callback_ret_`) it is 39 lines in 24 files.

| Kind | Sites | Where |
|---|---|---|
| Comparators that keep the first error in a member and return a fixed answer afterwards | 27 | sort: ob_sort_op_impl.cpp:400, 440, 474, 500, 525, 544; PX: ob_px_ms_receive_op.cpp:920, ob_row_heap.cpp:58, 115, ob_slice_calc.cpp:1051, 1389; storage: ob_datum_range.h:133, 152, ob_datum_rowkey_vector.cpp:34, 54, 72, ob_index_block_builder.h:134, 157, ob_imicro_block_decoder.cpp:48, 89, ob_micro_block_reader.cpp:63, ob_index_tree_prefetcher.ipp:1350, ob_rows_info.h:294; others: ob_store_rowkey.h:247, ob_datum_compare.h:68, ob_stat_item.cpp:252, ob_topk_hist_estimator.cpp:33 |
| `operator=` of schema objects (`error_ret_`) | 7 | ob_dependency_info.cpp:55, 805; ob_error_info.cpp:58; ob_routine_info.cpp:52, 248; ob_trigger_info.cpp:60; ob_trigger_mgr.cpp:43 |
| Hash-map callbacks | 2 | ob_dtl_interm_result_manager.cpp:63; ob_ps_cache_callback.h:56 |
| Guard destructors | 2 | ob_spi.cpp:1520 (`~ObPLSPITraceIdGuard`), 6174 (`~ObSPIRetryCtrlGuard`) |
| Constructor | 1 | ob_sql_utils.cpp:53 |

- **Only one comparator stays consistent after an error.** `ObSortOpImpl::Compare::operator()` on stored rows falls back to pointer order in the error state (ob_sort_op_impl.cpp:395-432, fallback at 401-408). That fallback came from 5aa611c00 (2026-04-09). The other half of that fix was in ob_sort_compare_vec_op.ipp, which is gone at 834bbee1e.
- **The two comparators the plan calls unfixed feed no sort.** ob_sort_op_impl.cpp:434-466 is called once per row against the top-N heap's top, and the caller reads `ret_` afterwards (:2420, :2432). ob_slice_calc.cpp:1046-1071 feeds `std::lower_bound` (:1095, 1306, 1367). So neither can repeat the libc++ out-of-bounds read, though both still return false for every pair after an error.
- **The other 25 comparators** were not traced to their callers here. Their inventory rows should say whether each feeds a sort, a heap or a search.

### 2.7 Codes from outside the catalog

- **Tracepoints.** `alter system set_tp ..., error_code = N` stores -N (src/sql/resolver/cmd/ob_alter_system_resolver.cpp:749-755), and the tracepoint returns it (src/oblib/lib/utility/ob_tracepoint.h:110-127, 259-300).
  - init.sql sets 12 tracepoints. One of them uses `error_code = 4` (tools/deploy/init.sql:26), a code the catalog does not have, so the client would see number 0 (section 2.2).
  - 3 configured tests set tracepoint 368 themselves.
  - There are 186 `OB_E(EventTable::...)` lines.
- **The parser's C core** keeps hand-copied values of 19 codes in src/sql/parser/parse_define.h:26-44 ("errno keep consistency with ob_define.h"). It stays C (Decision 13).
- **The geometry island** turns 18 C++ exception types into distinct codes (src/share/geo/ob_geo_dispatcher.h:1313-1362).
- **PL `SIGNAL`** carries a positive MySQL number supplied by the user (src/pl/ob_pl_interpreter.cpp:133; src/pl/ob_pl_resolver.cpp:554-562).

### 2.8 What the judge compares

**Test directives and results** (272 configured cases):
- 2,675 `--error` directives in 129 .test files. The plan says 2,688, from a different pattern. They name 120 distinct numbers and 23 `ER_` names. OB's own numbers appear directly, for example 5083 (`OB_ERR_INVALID_TYPE_FOR_OP`) 103 times, 5935 29 times, 7600 20 times and 4012 19 times.
- The .result files hold 2,700 `ERROR <SQLSTATE>: <message>` lines in 129 files: 967 distinct lines with 27 SQLSTATEs.
- SHOW WARNINGS output accounts for 322 rows in 40 files (198 Note, 122 Warning, 2 Error), with 12 distinct codes.
- mysqltest checks the number against `--error`, and the .result holds the SQLSTATE and the message text, so all three are compared.

**Family 6** compares the output of 18 `ob_error` commands, which print the name, message, cause, solution and every OB code behind a MySQL number (tools/ob_error/test/ob_error_test.test, expect_result.result).

**Coverage.** About 45% of the lines in the core and the SQL tier run under no configured case, "mostly error paths and rare branches" (coverage-076eb309b/README.md; PLAN.md section 8, item 22).

## 3. Constraints from the decisions and the plan

- **Decision 6 (b):** exact comparison. The two masks cover EST numbers and hash-order rows, not errors, so the error number, SQLSTATE, message text and warning rows must match exactly. PLAN.md section 3, "What must stay the same", lists the 1,546 catalog entries with their text and the 2,688 `--error` directives.
- **Decision 10 (a):** the SQL tier is rewritten "keeping their control flow", the leaves are translated file by file, and the core is designed by hand. The error rules must let a translated function keep its C++ shape.
- **Decisions 2 and 3:** no upstream replay. Nothing needs to look like the C++ text for its own sake. Only behavior binds.
- **Decision 12 (b):** a general out-of-memory aborts. Most of the 4,722 `ret = OB_ALLOCATE_MEMORY_FAILED` lines therefore have no Rust counterpart. -4013 stays a typed error at the named budget owners and for the logical -4013 errors. The kit's error-recovery rule (RULEBOOK.md section 2: `allocation-guard` against `precondition-guard` rows) applies.
- **Decision 13 (a):** the parser's C core, vsag, S2, share/geo and ICU regex stay behind a C ABI. Codes cross that boundary as integers, and every island entry keeps a `try/catch` (PLAN.md section 3). The parser exits through `longjmp` (PLAN.md section 3, item 7).
- **Decision 14 (b):** `#![forbid(unsafe_code)]` outside the named crates. The error crate and the per-thread buffer slot must be safe code.
- **Decision 16 (a):** stable toolchain only. A custom `?` for a non-`Result` type needs the unstable `Try` trait (assumption: still unstable in 1.98.1), so the error type rides on `Result`.
- **Decision 7 guidelines and PLAN.md section 3:** `panic=abort` for the whole program, so any panic ends the server. Rust's sort "may panic when the comparator is not a total order" (PLAN.md section 3, item 1).
- **Decision 8 notes:** a library form comes later, so there must be no process-global per-request state. The buffer belongs to the session, and the per-thread slot is set for each request.
- **Decisions 4, 4a and 4c:** Opus 5.5 does every role, and the kit asks for one canonical mapping per construct (RULEBOOK.md section 2).
- **PLAN.md section 3** ("Outline of the redesigned core") asks for "one error type that carries the exact OB code, generated from ob_errno.def". Its item 5 says `?` is wrong for the guards, resets, `tmp_ret` lines and codes used as outcomes. It also says to point gen_errno.pl at Rust output instead of translating what it produces today.
- **PLAN.md section 8:**
  - item 20 (`stacker`): `SMART_CALL`'s `OB_SIZE_OVERFLOW` becomes stack growth on native targets;
  - item 22: coverage (section 2.8);
  - item 23: false failures in exact runs;
  - items 17-19 and 21 do not touch errors.

## 4. Options

### 4.1 The error type

| Option | What it is | For | Against |
|---|---|---|---|
| A. Code-only newtype | `struct Error(i32)`, `Copy`, one generated constant per entry | Exact; one-to-one with every C++ comparison; accepts any code; no allocation; `?` works | No payload; the type does not separate normal outcomes from failures (neither does the C++) |
| B. Generated enum with 1,545 variants plus `Other(i32)` | Same data as A | Named variants | `Other(i32)` is needed anyway; no C++ site tests more than a few codes at once, so exhaustive matching buys nothing |
| C. Code plus a payload (message, context, backtrace) | A boxed struct | Debugging; the message travels with the error | Breaks the message rule of section 2.3 (see 4.2); larger `Result`; allocation on every error |
| D. One error enum per crate | Typical `thiserror` style | Idiomatic in libraries | The C++ tests codes across layers (storage codes are tested in sql); every conversion must keep the exact code; 2,700-3,400 units written by many agents would produce many types |
| E. `anyhow` or `Box<dyn Error>` | Dynamic error | Quick to write | Loses the exact code, against the plan's outline |

### 4.2 Where the user-visible message lives

- **M1. Keep the buffer.** A Rust copy of `ObWarningBuffer` per session, reached through a per-thread slot that the request handler sets. It is exact by construction. Its cost is per-thread state, which is correct only while one request runs on one thread at a time, as today. The evidence finds no async runtime and no need for one (evidence-full.md:3127).
- **M2. The message inside the error value.** This changes observable behavior in three ways:
  - SHOW WARNINGS keeps a message recorded under code A after the statement ends with code B (ob_sql_session_info.cpp:666-667);
  - `send_error_packet` drops the recorded message when the codes differ, so a value that carries the message would have to be dropped at exactly the same point;
  - warnings and notes (127 `LOG_USER_WARN` and 8 `LOG_USER_NOTE` calls, plus 75 warn-on-fail cast lines) are not errors at all and need the buffer anyway. M2 therefore still needs M1.
- **M3. An explicit context parameter instead of the per-thread slot.** It changes the signature of every function on the way to the 2,799 `LOG_USER_ERROR` calls in 393 files, casts and value libraries included, and gains nothing in behavior. It could be done after parity.

### 4.3 Codes used as values

- **V1. Values everywhere.** `Err(OB_ITER_END)` is returned and tested line for line as in the C++. It is mechanical, and a reviewer can check it against the source. The cost is idiom.
- **V2. Typed outcomes everywhere.** `Result<Option<T>>` or small enums. The compiler catches a forgotten case. The cost is a contract analysis per function over 347 codes, and a conversion at every site where the C++ passes a code upward or renames it (90 `OB_ITER_END` sites and 60 `OB_ENTRY_NOT_EXIST` sites set another code within three lines). A mistake there changes behavior silently, mostly in paths the judge never runs (section 2.8).
- **V3. Typed outcomes only in the core's new APIs,** with the C++ code contract everywhere else and a conversion at each call. This mixes two conventions, and the translated call sites still carry the V2 conversions.

### 4.4 The catalog and message formatting

- **G1. A generator that emits Rust**, including a typed argument list per formatted entry, plus a small formatter with C semantics.
- **G2. The same generator, with a run-time printf over a slice of argument values.** It is simpler to generate, but a wrong argument shows up only at run time.
- **G3. Translate today's generated C++.** The plan rejects this.

`format!` cannot stand in for C here:
- string precision counts bytes in C and characters in Rust (`%.192s` against `{:.192}`);
- C prints infinities and NaN as "inf" and "nan", and Rust as "inf" and "NaN";
- `%.*s` takes a (length, pointer) pair;
- the `vsnprintf` cut at 511 bytes can split a UTF-8 sequence.

## 5. Recommendation

### 5.1 Types

- **`Error` (option A).** A `Copy` newtype over `i32`, never 0, with a constant for each of the 1,544 non-zero codes and the two aliases, spelled as in C++ (`OB_SUCCESS` is `Ok(())`). It has a constructor from any non-zero `i32`, a converter from an island's integer return (0 becomes `Ok(())`), and `type Result<T = ()> = core::result::Result<T, Error>`.
  - The 22 predicates of share/ob_define.h become functions on `Error`.
  - Keep the plain `i32` inside, so that the constants work as `match` patterns. Whether `NonZeroI32` constants can be used in patterns is an assumption to check in Step 2a. If they can, `NonZeroI32` makes `Result<(), Error>` 4 bytes instead of 8, which is otherwise immaterial.
  - The names used in this report (`Error`, `WarningBuffer`, `user_error!`) are placeholders for the naming section of the design document.
- **`WarningBuffer` (option M1).** It has the same fields and limits as `ObWarningBuffer`: 512-byte message storage, a 64-item ring and a total count. It is held per session as `Arc<Mutex<WarningBuffer>>`, and the per-thread slot holds a clone of that `Arc` while a request runs. It needs no `unsafe`. The slot keeps the same scoped swaps: triggers, the ignore scope and PX tasks.
- **`user_error!(OB_X, args...)`** records the formatted message, as `LOG_USER_ERROR` does, and evaluates to `OB_X`. `return Err(user_error!(...))` then covers the 2,630 paired sites. The unpaired 156 sites, `user_warn!` and `user_note!` are statements.
- **One function builds the error packet.** It repeats section 2.3 step by step and hands the result to sql-nio's existing `encode_error_payload(error_code: u16, sql_state: &[u8; 5], message)` (rust/sql-nio/src/response.rs:106-122). The SHOW WARNINGS copy rule of `set_show_warnings_buf` is kept.

### 5.2 Rules for the rulebook (one Rust form per C++ construct)

| C++ | Rust |
|---|---|
| A plain `if (OB_FAIL(a)) {LOG_WARN} else if (OB_FAIL(b)) ...` chain, where nothing in the function runs after an error | `a().inspect_err(\|e\| warn!(...))?;` for each step |
| Any function with a statement that runs after an error (a later `if (OB_FAIL(ret))`, an unguarded statement, a reset, a `tmp_ret` merge) | A local `let mut ret: Result = Ok(());` and the C++ statements in the same order, without `?` in that function |
| `for (...; OB_SUCC(ret) && c; ...)` | `while ret.is_ok() && c`, inside the local-`ret` form |
| `if (OB_X == ret) { ...; ret = OB_SUCCESS; }` | `match` on the result with `Err(e) if e == OB_X` |
| Renaming a code (`if (OB_HASH_NOT_EXIST == ret) ret = OB_ENTRY_NOT_EXIST;`) | `map_err` with the same `==` test |
| `ret = OB_SUCCESS;` after a general failure test | `if let Err(e) = ... { warn!(...) }`, never `?` |
| `tmp_ret` with `COVER_SUCC` or the ternary merge | `let tmp = ...;` then `ret = ret.and(tmp)`, which keeps the first error; log-only sites only log |
| `LOG_WARN` reading the local `ret` | The log macro takes the error as an argument |
| `LOG_USER_ERROR(OB_X, ...)` next to `ret = OB_X` | `return Err(user_error!(OB_X, ...))`; when they are apart, two statements as in the C++ |
| `CK` / `OX` / `OZ` / `OV` | Expanded into the forms above; `CK` keeps `OB_ERR_UNEXPECTED` |
| `int &ret = ret_;` in a comparator | The comparator returns `Result<bool>` or `Result<Ordering>`. The port's own sort, heap and binary search (the sort work needs its own copies anyway, for libc++ tie behavior, PLAN.md section 3 item 1) stop at the first error and return it. No fallible comparator goes to `slice::sort*`, `binary_search_by` or `BinaryHeap` |
| `operator=` that records `error_ret_` | A fallible `assign(&mut self, &Self) -> Result` |
| Hash-map callback with `callback_ret_` / `ret_` | A closure that returns `Result` |
| Guard destructor that writes into `ret_` | An explicit `finish(self) -> Result` on the normal path, with `Drop` for the error path, decided per site from the C++ |
| `ret = OB_ALLOCATE_MEMORY_FAILED` after a general allocation | No translation (Decision 12); kept only at the named budget owners and for the logical -4013 errors |
| Island entry point | Returns an `i32` code, 0 for success, which the Rust shim turns into `Result` at once |
| Tracepoint hit | The injected value becomes an `Error`, whatever its value |
| `switch (ret)` / `case OB_X:` | `match` on the error against the constants |

### 5.3 Codes used as values: V1, with the core keeping C++ contracts

Translated code keeps every code the C++ produces, tests and passes on. Core replacements for C++ APIs whose codes callers test keep those codes at their public boundary for the port, even if they use `Option` inside. This covers the hash maps behind `get_refactored`/`set_refactored`/`erase_refactored`, the row and batch iterators, and `databuff_printf`'s `OB_SIZE_OVERFLOW`. New core APIs that have no C++ counterpart choose freely. Moving the hot APIs to `Option` is a post-parity change of its own.

Reasons:
- 347 codes are compared, the top 10 cover only 64.6% of comparisons, and 108 codes appear once, so a typed API per code is impractical;
- codes are renamed right after tests, and a conversion layer would have to repeat each renaming;
- about 45% of core and SQL-tier lines run under no test, so a one-to-one mapping that reviewers check against the source is safer than a reshaping that depends on per-site judgment.

### 5.4 The generator and the formats

- **One generator** (option G1) replaces gen_errno.pl. It reads ob_errno.def and mysql_errno.h unchanged and emits:
  - the constants;
  - a table with name, `str_error`, `str_user_error`, SQLSTATE, the resolved MySQL number, cause and solution;
  - the reverse MySQL-to-OB map and the data for a Rust `ob_error` tool (family 6 runs it from the regenerated catalog; os_errno.def stays its second input);
  - a check that the 19 values in parse_define.h:26-44 match the catalog.
- **It reproduces every behavior of section 2.2 and its lookup table:** the duplicate name, the two malformed lines, the two aliases, the OB name used as a MySQL number, "Unknown Error" against "Unknown error", number 0 for unknown codes, and the handling of positive and very negative values. It emits one crate at the bottom of the graph.
- **Formatted messages.** Each of the 468 formatted entries gets a generated argument list: `%.*s` becomes one byte slice where C passes (length, pointer), `%ld` becomes `i64`, `%u` becomes `u32`, and so on. A wrong argument is then a compile error, where C++ only has the printf-format warning. The output comes from a small formatter for the 16 conversions with C semantics and the 511-byte cut.
- **Checks, run when the core builds** (they need a small C++ harness linked against ob_errno.cpp, so not now):
  - every lookup function against C++ for all 1,545 codes, plus unknown, positive and out-of-range values;
  - every one of the 468 formats against `vsnprintf` with generated arguments, edge values included.

### 5.5 What the inventory records

The inventory needs one row for each site of:
- the 5,718 `ret` comparison lines and the 697 lines that compare other variables;
- the 2,906 resets;
- the `tmp_ret` lines (1,562 declarations and `OB_TMP_FAIL`s, 3,983 lines in all);
- the 39 `ret_` sites;
- the 156 unpaired `LOG_USER_ERROR` calls;
- the out-of-memory sites.

Each row records:
- the code or codes involved;
- what the code means at that site: end of data, lookup outcome, buffer too small, retry, becomes a warning, ignored, or renamed;
- for comparators, whether they feed a sort, a heap or a search;
- the kit's `allocation-guard` or `precondition-guard` kind.

## 6. Prior art from the sql-nio port

- **The integer return code.** abi-naming.md says "返回码用 int：0 成功 / −1 失败；C++ 侧经翻译层映射成 OB errno，裸 rc 不外泄出包装类": return codes are ints, 0 for success and -1 for failure, and the C++ side maps them to OB codes in a wrapper, so the raw code never leaves the wrapper class.
  - **Rejected** for the island ABIs. The islands produce specific codes that the tests pin: the geometry island has 18 exception types with distinct codes, ICU regex reports `OB_ERR_REGEXP_*` (5813 appears in two `--error` directives), and vsag has `NO_ENOUGH_MEMORY`, which Decision 12 keeps as -4013. A 0 / -1 return would lose them.
  - **Kept:** the return is a plain integer, not a struct, and the raw value goes no further than the shim, which turns it into `Result` at once.
- **The packet encoder.** sql-nio's error-packet encoder is reused. Its two C-ABI files are dropped anyway (PLAN.md section 3).
- **ffi-mechanics.md** sets no error convention.

## 7. Questions only the developer can answer

1. **Codes as values or typed outcomes, and when?** This report recommends values for the port and `Option`-returning iterators and lookups only after parity. Should the core design sessions reshape those APIs now instead?
2. **The buffer path.** Is it acceptable to reach the warning buffer through a per-thread slot, as the C++ does? The alternative is an explicit context parameter, which touches every function on the way to the 2,799 `LOG_USER_ERROR` calls.
3. **Server log format.** No judge family compares server log text (assumption from PLAN.md section 4's family list). May the Rust logs drop today's `ret=-4008` form, or do people or tools depend on it?
4. **Generated catalog.** Should it be produced at build time (`build.rs` reading ob_errno.def), or checked in, as the C++ output is today?
