# printf-calls: classification vocabulary

How to write one inventory row (the seven columns of templates/inventory.tsv) for each site of migration/inventory/sweep/printf-calls.tsv: a call of a printf-family function or macro (`databuff_printf`, `BUF_PRINTF`, `DATA_PRINTF`, `append_fmt`, `snprintf`, the printf-style log macros, `fprintf` and the rest; definition and counts: the `printf-calls` row of migration/inventory/sweep/summary.tsv). The list has 6,506 rows (`wc -l` prints 6,507 lines with the header). A classifier who reads this file, the rules it cites and the site can decide every value. This file follows the design as design amendment 1 left it (migration/design/AMENDMENTS.md, entries A1-1 to A1-11; section 1 says which entries reach this list); its review sample is section 14.2, and section 15 says what a script may fill in before a classifier reads the code.

## 1. What a row decides

RULEBOOK section 5 gives this list's rows three answers: the call's format, its argument types, and whether the text can reach the client (RULEBOOK 2.3; s2-errors.md 2.9). So:

- `classification` says where the formatted bytes go (section 3). It answers "can the text reach the client" and says whether anything the engine does depends on the exact bytes.
- `target_translation` gives the Rust call: which macro, the format, and each argument as the Rust value C would read (sections 5 and 6), plus what the classification adds. It does not re-derive the rest of the function, which follows from the rulebook (RULEBOOK section 5, last item).
- `evidence` shows the types, the format's source when it is not a literal, and the path from the call to where the bytes end (section 8).

The rules that decide these rows:

- RULEBOOK 2.3, the printf row: the three printf overloads of `databuff_printf` become `databuff_printf!`, `databuff_printf_2!` and `databuff_printf_3!` in header order (src/oblib/lib/utility/ob_print_utils.h:188-196), `BUF_PRINTF` becomes `buf_printf!(buf, pos, "..", ..)`, `snprintf` becomes `snprintf!`, all formatting through ob-errno's `cfmt`; never `format!` or `write!` for text that can reach the client. Also its rows for function-like macros, `TO_STRING_KV`, platform `#if`, unscoped enums and exceptions.
- RULEBOOK 1, the Number text row (C-format text only through `cfmt`; no `format!` of anything that can reach output or a hash) and the Logging row (ob-base's log macros with the C++ text; no `println!`, `eprintln!` or `dbg!`, `eprintln!` only in the seekdb crate).
- RULEBOOK 2.1 (a code the C++ ignores is `let _ = f();`; any other `int` return stays `i32`), 2.2 (the `ObMemAttr` row: memory labels and ctx ids are dropped, and a label survives only as a `Budget` name, s3-memory.md 3.6.5), 2.4 (the id row, which design amendment A1-1 extends to plan and execution objects; an address packed into an integer is `id.to_i64()`), 2.5 (the `sizeof` row, as design amendment A1-9 widened it), 2.6 (a float passed to a C variadic formatter is `as f64`; `long` is `i64`, `size_t` is `u64` where it reaches output), 2.10 (the BUG and UNKNOWN rules), section 0 with 2.1, 2.2 and 2.3 (the branches a translation removes: a general allocation failure, an `is_inited_` check, a null test on what cannot be null, a `catch` outside an island edge), section 6 item 9 (every C++ function keeps a Rust function of its name).
- s3-memory.md 3.7.1 to 3.7.3 (which out-of-memory branches go: a general allocation failure goes, a check point of a named owner or logical limit stays; design amendments A1-7 and A1-10).
- ARCHITECTURE.md §2 (every C-format text goes through `cfmt`, which implements every conversion this sweep finds), §4.1 rule 6 (arena ids are never printed), §14 rules 3, 7 and 8 (macros become lowercase `macro_rules!`; logging lives in ob-base; the `TODO(port)` marker).
- s2-errors.md 2.5 rules 1 and 2 (a log macro's first argument is the `ret` in scope for WARN and ERROR; INFO, TRACE and DEBUG take none; a `*_LOG_RET` form passes its code; `K(ret)` prints the code), 2.7 rules 1, 2, 4 and 5 (user messages), 2.9 (what `cfmt` implements and what each argument becomes), 2.11 rule 7 (the differential test runs every format and argument kind this list names).
- s4-sql-front.md 2.1 rule 3 (an id is never printed; `KP(` keys leave the Rust log lines), s8-numerics-platform.md section 1 rule 3 (float promotion) and 7.4 (the `println!`/`eprintln!`/`dbg!` ban), s6-storage.md 4.3 (the seekdb crate's stderr write).
- s1-crates-core.md 5.3 rules 7 and 8 and s7-islands-unsafe.md 7.1 (which lines are translated).

Design amendment 1 reaches this list through four entries, and none of them changes the value of a row the sweep has today:
- A1-1: which pointees have an id, for `%p` and addresses printed as integers (section 6, item 7; Q3).
- A1-7 and A1-10: which -4013 branches stay, for a call inside one (section 7).
- A1-9: the `sizeof` row, for a `sizeof` among the arguments (section 6, item 12).

The other entries change no call's format, arguments or reach. No printf-family call sits in a `ret_` comparator (A1-6): the only (file, symbol) pair printf-calls.tsv shares with ret-alias.tsv is `ObResultSet::inner_get_next_row`, which ret-alias.tsv:31 marks "not a comparator". The command is `awk -F'\t' 'NR==FNR{if(FNR>1)a[$1"\t"$3]=1;next} FNR>1&&($1"\t"$3) in a{print $1"\t"$3}' migration/inventory/sweep/ret-alias.tsv migration/inventory/sweep/printf-calls.tsv | sort -u`. A1-2 to A1-5, A1-8 and A1-11 concern counts, locks, hash-map access and kept frames.

## 2. The columns

| Column | What goes in it |
|---|---|
| `file` | the sweep row's `file`, unchanged |
| `symbol` | the sweep row's `symbol`, unchanged (`-` and `#define NAME` included) |
| `source_construct` | `line N: ` and then the sweep row's `construct`, unchanged, tags such as `[not built]` and `[vendored]` included. The line keeps the merge key (file, symbol, source_construct) unique: without it 1,087 rows share their key with another row, while no two rows share a file and a line (section 12) |
| `classification` | one value of section 3 |
| `target_translation` | section 7's form for the value, with the call form of section 5 and the arguments of section 6 |
| `evidence` | section 8 |
| `status` | `confirmed`, or `unknown` (section 9) |

## 3. The values

| Value | Meaning | Decided by (section 4 gives the order) |
|---|---|---|
| `CLIENT_TEXT` | the formatted bytes, or a value read back from them, can reach the client | step 3 (a user message) or step 4 (a path from the buffer ends at a client output) |
| `ENGINE_TEXT` | the bytes never reach the client, but what the engine does depends on them (SQL it runs itself, a name or key it looks up, compares or hashes, a path it opens, values it parses back), or nothing reads them | step 4: every path ends, at least one at an engine use, none at a client output |
| `LOG_TEXT` | the bytes reach only the server log | step 3 (a server-log macro) or step 4 (every path ends at a log line) |
| `STREAM_TEXT` | the bytes go to stdout, stderr, a file descriptor or a `FILE *`, not to the server log, and neither the client nor the engine reads them | step 3 (the sweep marks `writes stdout` or `writes a stream or file`) or step 4 (every path ends, at least one at a stream or file write, none at a client output or an engine use) |
| `NOT_TRANSLATED` | the line is in code the port does not translate | step 1 |
| `NOT_PRINTF` | the call binds one of `databuff_printf`'s object templates in every instantiation, so it prints an object, not a format | step 2 |
| `UNKNOWN` | the steps cannot decide where the line is placed, which overload the call binds, or where the bytes go | steps 1, 2 or 4 (section 9) |

`CLIENT_TEXT` and `ENGINE_TEXT` get the same Rust call; they differ in what a reviewer and the judge look for. `LOG_TEXT` gets it too (the logs of both builds are compared in Steps 5-6, RULEBOOK 1). A `STREAM_TEXT` call that writes the stream itself (step 3) waits on open question 2; one whose buffer reaches a stream later (step 4) keeps its own Rust call, and the write that reaches the stream is not a printf-family call. Whatever the value, a call in a branch the port removes is not translated (section 7).

## 4. How to decide

Take the first step that settles the value.

**Step 1: placement.** Look up the row's file and line in the `inputs` of migration/manifest.tsv and migration/core-manifest.tsv and the `files` of migration/not-translated.tsv. An entry may carry a range (`file:a-b`); it covers the row only when the line is inside it. For example `grep -nP '(^|[\t,])src/sql/parser/parse_node\.c([\t,:]|$)' migration/core-manifest.tsv migration/manifest.tsv migration/not-translated.tsv`.
- Covered by a manifest or core-manifest input: the line is translated; go on. This holds even when not-translated.tsv also lists the file as an island: those are the island C lines a design placement ports to Rust while the C stays compiled (s1-crates-core.md 5.3 rule 8), such as src/sql/parser/parse_node.c:58-175, which core/sql-parse-tree/parse_node takes.
- Otherwise covered by not-translated.tsv (island, dead, dropped, deferred, generated, data; s1-crates-core.md 5.3 rule 7): `NOT_TRANSLATED`. Rows tagged `[not built]` are here (their files are listed as dead).
- Otherwise, a row tagged `[vendored]` (the zstd 1.3.8 sources ob-clib-sys compiles as C, s7-islands-unsafe.md 7.1) or in a `.y` or `.l` grammar (its actions go into the bison and flex output that sql-parser-sys copies, s7-islands-unsafe.md 7.1 rule 3; Decision 13): `NOT_TRANSLATED` (finding F1).
- Otherwise: `UNKNOWN`, an inventory bug to flag (RULEBOOK section 5).

**Step 2: overload** (only rows the sweep marks `overload not resolved`). The argument at the format position has a template type. List the instantiations: every call of the enclosing template and the type it passes there.
- None passes `char *` or `const char *`: `NOT_PRINTF`.
- All do: go on; the format is whatever those strings hold (section 6, item 9).
- Some do and some do not, or the instantiations cannot all be listed: `UNKNOWN`.

**Step 3: where the callee itself sends the bytes.**
- The sweep marks `records a user message`, or the callee is `FORWARD_USER_ERROR_MSG` or `_LOG_USER_MSG`: `CLIENT_TEXT` (the message reaches the client in the error packet and SHOW WARNINGS, s2-errors.md 2.7 and 2.10).
- The callee is a server-log macro (row 7 of section 5): `LOG_TEXT`.
- The sweep marks `writes stdout` or `writes a stream or file`: `STREAM_TEXT`.
- Otherwise the call writes a buffer, a string or a field: step 4, even when that buffer is later written to a stream. `logdata_printf` and `LOG_DATA_PRINTF` carry `writes the server log`, but they write a buffer their caller passes: follow it.

**Step 4: follow the bytes.** Start at what the call writes: the buffer argument (`buf`, the printer's `buf_`, the first argument of `snprintf`), the `ObSqlString` that `append_fmt` or `assign_fmt` is called on, the field it fills. Follow the bytes each time they move to a new holder: a copy into another buffer or string, a return, an output parameter, a store into a field or container, a serialized form or a table row, a call that reads them. A buffer the function got from its caller (a parameter, or a member that points into the caller's buffer, as the SQL printers' `buf_` does) is an output parameter: the path goes on at each caller, with the buffer that caller passes. A stored value is followed to the places that read it back. A callee that writes more text into the same buffer is not a move. A parse of the bytes into other values (a number, a date, a JSON document, a set of options: `ObNumber::from_`, `load_from_string`) ends the path: at a client output when a parsed value is itself output (client output 1), otherwise at an engine use. Each path ends where the bytes first reach one of these:

- **A client output.**
  1. A result the client receives: a cell of a row a statement returns (a virtual table's `cur_row_.cells_[i].set_*`, an EXPLAIN row, a SHOW row), an expression's result, including a value read back from the bytes, or a column name of the result. src/observer/virtual_table/ob_all_virtual_kvcache_store_memblock.cpp:144 formats a double that ob_all_virtual_kvcache_store_memblock.cpp:147 reads back into the number the cell holds at ob_all_virtual_kvcache_store_memblock.cpp:149. The SHOW resolver parses the SELECT it builds as the client's own statement (src/sql/resolver/cmd/ob_show_resolver.cpp:1141), so that SELECT's aliases, such as `Tables_in_%.*s` (ob_show_resolver.cpp:2338), are the column names the client receives.
  2. Plan text: the `PlanText` that `ObSqlPlan` fills for EXPLAIN. Each operator's `get_plan_item_info` is called at src/sql/monitor/ob_sql_plan.cpp:583, and the formatted lines become EXPLAIN rows at src/sql/optimizer/ob_explain_log_plan.cpp:87.
  3. A user message: an argument of `LOG_USER_ERROR`, `LOG_USER_WARN`, `LOG_USER_NOTE`, the `LOG_MYSQL_USER_*` macros or `FORWARD_USER_*`. src/rootserver/ob_ddl_service.cpp:326 formats `err_msg`, which `LOG_USER_ERROR` records at ob_ddl_service.cpp:336.
  4. Text a later statement returns: a field of a schema object (a view definition, printed at src/sql/resolver/ddl/ob_create_view_resolver.cpp:606; a generated name; a default value), a system variable's value, a session field a SQL function returns. The evidence cites the line that returns it; SHOW CREATE TABLE puts `ObSchemaPrinter`'s text in a cell at src/observer/virtual_table/ob_show_create_table.cpp:239. A value stored in an inner table counts when a client can SELECT that table; the evidence then cites the line that writes the column and a statement that reads it. For example, a full-text index's hidden column gets a generated name (src/sql/resolver/ddl/ob_fts_index_builder_util.cpp:1425). The DDL writes that name into `__all_column` (src/share/schema/ob_table_sql_service.cpp:3190), and a judged test selects it (tools/deploy/mysql_test/test_suite/fts_index/t/create_table_with_fts_index.test:38-39).
  5. The file a statement writes for the user, `SELECT ... INTO OUTFILE` or `DUMPFILE`: the select-into operator formats each row (src/sql/engine/basic/ob_select_into_op.cpp:333) and appends it to the file (ob_select_into_op.cpp:1062).
- **An engine use.**
  1. SQL the engine runs itself: text passed to a read or write of `ObISQLClient`, `ObCommonSqlProxy`, `ObMySQLProxy` (`GCTX.sql_proxy_` is one, src/share/ob_server_struct.h:74), `ObMySQLTransaction` or `ObInnerSQLConnection`, or to a helper that builds and runs such a statement. This holds whatever the statement stores: what a client later reads is the stored values, not this text. It covers the statement text the call builds. A value the call builds that a statement later stores (a name, a default value or a comment, passed to `dml.add_column`) is not statement text: follow it as a stored value to where it is read back (client output 4). The copy of a statement the SQL audit and plan cache views keep does not count as a client output. A SELECT a resolver parses as the client's own statement is client output 1, not this.
  2. A name or key the engine looks up, compares or hashes.
  3. A file or directory path it opens, creates or removes (src/logservice/palf/log_io_utils.cpp:57 builds the path `_stat64` reads at log_io_utils.cpp:64).
  4. Values the engine parses back from the bytes and acts on (the JSON of options that src/sql/engine/cmd/ob_load_data_parser.cpp:195 helps build is parsed at src/sql/engine/basic/ob_select_into_op.cpp:57).
  5. Bytes nothing reads.
- **A stream or file write.** The bytes written to stdout, stderr, a file descriptor or a `FILE *` (`write`, `writev`, `fwrite`, `fputs`, `WriteFile`, or a stream writer of section 5 row 11 given the buffer as an argument), or to a file through the IO layer, when nothing reads that file back (a file the engine reads back is followed to its reader; the user's file is client output 5). src/oblib/lib/signal/ob_signal_handlers.cpp:194 formats into `crash_info`, which ob_signal_handlers.cpp:198 copies into `print_buf`, which `writev` sends to stderr at ob_signal_handlers.cpp:220.
- **A log line.** An argument of a server-log macro (row 7 of section 5, and the key-value `LOG_*` macros), a `K()` or `KP()` key among them, or the item buffer `ObLogger` fills and writes to the server log files (the line header at src/oblib/lib/oblog/ob_log.cpp:757).

The result:
- `CLIENT_TEXT` if any path ends at a client output. One such path decides; the others need not be followed.
- `ENGINE_TEXT` if every path ends and at least one ends at an engine use.
- `STREAM_TEXT` if every path ends, at least one at a stream or file write, none at an engine use.
- `LOG_TEXT` if every path ends at a log line.
- `UNKNOWN` if a path cannot be followed (the bytes pass through a function pointer or `std::function`, a virtual call whose overrides cannot all be listed, or a container whose readers cannot all be listed) or has made 5 moves without ending, and no path reached a client output.

Four kinds of row start differently:
- **Inside a `to_string`** (the sweep marks `inside to_string`; `DEF_TO_STRING` bodies included): the text goes wherever that `to_string` is called from. Search the explicit uses of the class's `to_string`: `x.to_string(` or `x->to_string(`, the free `to_string(x, ...)` (src/oblib/lib/utility/ob_print_utils.h:141), the object template `databuff_printf(buf, len, pos, x)` (ob_print_utils.h:198-199), `databuff_print_obj`, `BUF_PRINTO(x)`, `ObCStringHelper::convert(x)` (ob_print_utils.h:305) and `to_cstring(x)`, with `x` an object of the class or of a subclass that does not override `to_string`; `LOG_USER_ERROR(OB_X, helper.convert(x))` is a user message. A `K()`, `KP()` or `J_KV` key in a log macro is a log line. A key inside another class's `to_string` hands the text to that `to_string`: follow its uses in turn (one move). `LOG_TEXT` only when every use ends at a log line; the evidence gives the search.
- **A `#define` body row** (the sweep marks `inside #define X (a fixed format: its uses are not rows)`): the row stands for every use of `X`. Take the union of their results: one use that reaches a client output decides `CLIENT_TEXT`; `LOG_TEXT` needs every use listed. The uses come from `grep -rn 'X(' src`.
- **Inside the definition of a printf-family function or macro** (the sweep marks `forwards a va_list`, or a `vsnprintf` or `databuff_vprintf` row sits in such a definition): the union over that callee's calls in this list. It is `CLIENT_TEXT` when one of them is; cite that row. When the callee's calls are not rows (its format parameter has another name, as `select_str` of `ObShowResolver::ObSqlStrGenerator::gen_select_str`, src/sql/resolver/cmd/ob_show_resolver.cpp:2250, finding F7; or nothing calls it), and for a variadic template that passes its own `format` parameter on (`ObOptimizerTraceImpl::append_format`, src/sql/ob_optimizer_trace_impl.h:548), follow the bytes the definition writes (step 4), and list the callers' formats in the evidence.
- **Inside a generic printer.** These are the overloads and specializations that print one value of a fixed type for the object and key-value printers: the free `to_string` (src/oblib/lib/utility/ob_print_utils.h:141), `databuff_print_obj`, `databuff_print_key_obj` and `databuff_print_obj_array` (in ob_print_utils.h, with specializations in src/oblib/lib/utility/ob_print_utils.cpp), and `logdata_print_obj`, `logdata_print_key_obj` and `logdata_print_value` (src/oblib/lib/oblog/ob_log_print_kv.h). They hold 149 rows (`awk -F'\t' 'NR>1 && $3 ~ /^(to_string|databuff_print_obj|databuff_print_key_obj|databuff_print_obj_array|logdata_print_obj|logdata_print_key_obj|logdata_print_value)$/ && $1 ~ /(ob_print_utils|ob_log_print_kv)\.(h|cpp)$/' migration/inventory/sweep/printf-calls.tsv | wc -l`).
  - Such a row stands for every value of its parameter type that reaches it. A value reaches it as an object passed to the object template `databuff_printf(buf, buf_len, pos, x)`, to `ObCStringHelper::convert(x)` (ob_print_utils.h:305) or to `BUF_PRINTO(x)`, or as a key of `K()`, `K_()`, `J_KV` or `TO_STRING_KV`. For the `logdata_` forms it is a log macro's key, or a full-link trace tag written by `tag_to_string` (src/oblib/lib/trace/ob_trace.h:181, :194), whose buffer is followed as in step 4. A key inside a `to_string` hands the text to that `to_string` (one move).
  - Take the union, as for a `#define` body row. One use that reaches a client output decides `CLIENT_TEXT`. For example, the `uint64_t` key printer (ob_print_utils.h:782-789) prints SCN's keys (`TO_STRING_KV(K_(val), K_(v))`, src/share/scn.h:138). `helper.convert` puts an SCN's text into the memtables_info string (src/observer/virtual_table/ob_all_virtual_minor_freeze_info.cpp:197), which becomes a cell (ob_all_virtual_minor_freeze_info.cpp:139).
  - `LOG_TEXT` needs every use listed. Find the uses from the declarations of that type. For `volatile int32_t`, `grep -rnE '^\s*(mutable\s+)?volatile\s+(int32_t|int)\s+\w+' src` prints seven. Only three classes print theirs, in `to_string`s whose text reaches only log keys: src/share/io/ob_io_define.h:416, :486 and src/storage/access/ob_empty_read_bucket.h:94.
  - A parameter of template type (`const T &`, `T *`), or a type whose uses cannot all be listed, is `UNKNOWN` when no client use is found.
  - The review found two such rows that fit no value before this bullet (section 14.2).

## 5. The Rust call for each callee

The callee is the construct's first word. Rows 1-8 are decided; rows 9-11 wait on an open question and carry its conservative form (section 10).

| # | Callee | Rust call | Rule |
|---|---|---|---|
| 1 | `databuff_printf` with the format third (`buf, buf_len, fmt`) | `databuff_printf!` | RULEBOOK 2.3; s2-errors.md 2.9 rule 2; ob_print_utils.h:188-189 |
| 2 | `databuff_printf` with the format fourth (`buf, buf_len, pos, fmt`) | `databuff_printf_2!` | as row 1; ob_print_utils.h:191-192 |
| 3 | `databuff_printf` with the format fifth (`buf, buf_len, pos, alloc, fmt`) | `databuff_printf_3!` | as row 1; ob_print_utils.h:194-196 |
| 4 | `BUF_PRINTF` | `buf_printf!(buf, pos, fmt, ..)`, which passes the locals `BUF_PRINTF` reads (ob_print_utils.h:1016) | RULEBOOK 2.3 |
| 5 | `snprintf`, and `sprintf` | `snprintf!`; for `sprintf`, into the destination slice, whose length the C++ does not check | RULEBOOK 2.3 ("snprintf and the other printf-family calls") |
| 6 | `FORWARD_USER_ERROR_MSG`; `_LOG_USER_MSG` inside the `LOG_USER_*` definitions | `forward_user_error_msg!(code, fmt, ..)`; the definitions become `log_user_error!`, `log_user_warn!`, `log_user_note!` | s2-errors.md 2.7 rules 1 and 4 |
| 7 | the server-log macros: `_OB_LOG`, `_OB_LOG_RET`, `HASH_WRITE_LOG`, `HASH_WRITE_LOG_RET`, `_LOG_INFO`, `_LOG_WARN`, `_LOG_ERROR`, `_LOG_DEBUG`, `_COMMON_LOG`, `_COMMON_LOG_RET`, `_LIB_LOG`, `_LIB_LOG_RET`, `_SHARE_LOG`, `_SHARE_SCHEMA_LOG`, `_STORAGE_LOG`, `_TRANS_LOG`, `_SQL_RESV_LOG`, `_BOOTSTRAP_LOG`, `_OB_NUM_LEVEL_LOG` | the lowercase `macro_rules!` of the same name in ob-base's logging module (`_ob_log!`, `hash_write_log!`, `_log_warn!`...). When the level is WARN or ERROR and the macro takes no code, the first argument is the `ret` in scope, ahead of the level; a macro that takes a code (the `_RET` forms, `_OB_NUM_LEVEL_LOG`) passes it instead; then the C++ arguments in order | ARCHITECTURE.md §14 rules 3 and 7; s2-errors.md 2.5 rule 1; RULEBOOK 1, Logging row; RULEBOOK 2.3, printf row |
| 8 | macros that define functions and read no name from a caller: `DEF_TYPE_STR_FUNCS_WITHOUT_ACCURACY_FOR_NON_STRING`, `..._FOR_STRING`, `..._FOR_ODATE`, `DEF_TYPE_TEXT_FUNCS_LENGTH`, `DEF_NUMERIC_FUNCS`; and the `vsnprintf` and `databuff_vprintf` rows inside `databuff_printf`'s own definitions (src/oblib/lib/utility/ob_print_utils.cpp) and inside `ObLogger::log_user_message` (src/oblib/lib/oblog/ob_log.cpp:663) | the lowercase `macro_rules!` with the same arguments, whose functions call rows 1-5; `databuff_printf`'s definitions are ob-base's macros over `cfmt`, keeping `databuff_vprintf`'s codes and cut; `log_user_message`'s formatting is what `log_user_error!` records | RULEBOOK 2.3, function-like macro row; ARCHITECTURE.md §14 rule 3; s2-errors.md 2.9 rule 2, 2.7 rules 1-2 |
| 9 | C-variadic functions and their `va_list` forms: `append_fmt`, `assign_fmt` (src/oblib/lib/string/ob_sql_string.h:58, :64), `ObBufferWriter::append_fmt` (src/storage/blocksstable/ob_data_buffer.h:265), `ObShowResolver::ObSqlStrGenerator::gen_select_str` and `gen_from_str` (src/sql/resolver/cmd/ob_show_resolver.cpp:2250, :2271; finding F7), the variadic constructor of `ObDIActionGuard` (src/oblib/lib/stat/ob_diagnostic_info_guard.h:256), `logdata_printf` (src/oblib/lib/oblog/ob_log_print_kv.h:62), `lnprintf` (src/oblib/easy/util/easy_string.h:22), `add_plan_note` (src/sql/optimizer/ob_optimizer_context.h:639), `set_extra_info` (src/storage/tablet/ob_tablet_persister.h:127), `ob_alloc_printf` (src/oblib/lib/utility/utility.h:780), `ObLogger::log_message_fmt` and `log_message_va` (their rows sit at src/oblib/lib/oblog/ob_log.h:1074 and :1092), and `vappend`, `vappend_fmt`, `logdata_vprintf`, `easy_vsnprintf`, `__wrap_vsnprintf`, `__wrap_vsprintf` and the `vsnprintf`/`databuff_vprintf` rows inside such definitions | open question 1 | none names it |
| 10 | macros that read names from their caller's scope, other than `BUF_PRINTF` and row 7: `DATA_PRINTF` (`ret`, `buf_`, `buf_len_`, `pos_`: src/sql/printer/ob_raw_expr_printer.h:35-46), `LOG_DATA_PRINTF`, `BUF_PRINT_CONST_STR`, `BUF_PRINT_STR`, `TX_KV_PRINT_WITH_ERR`, `TX_PRINT_FUNC_WITH_ERR`, `REPORT_OUT_OF_RANGE_ERROR` | lowercase `macro_rules!` (RULEBOOK 2.3), but the names it takes: open question 1 | RULEBOOK 2.3 gives the name only |
| 11 | stream writers: `printf`, `fprintf`, `LOG_STDERR`, `LOG_STDOUT` (src/oblib/lib/oblog/ob_log_print_kv.h:66-67), `FPRINTF`, `P_COLOR` (src/storage/blocksstable/ob_sstable_printer.cpp:28, :41), `MPRINT` | open question 2 | none names it |

`RAWLOG` appears only in vendored zstd (step 1). A macro not in this table: read its definition; it is row 8 if it reads no name from its caller, row 10 if it does, row 11 if it writes a stream.

## 6. Arguments and the format

Each argument becomes the Rust value of what C passes through `...`; the macros check each against its conversion at run time (s2-errors.md 2.9 rule 2). The sweep gives a type where it resolved one; check every type against its declaration first (finding F3), and resolve every `?`.

1. **Integers.** The value, as its C++ type maps to Rust (RULEBOOK 2.6), passed as the type the conversion reads: `i32` for `%d` and `%i`, `u32` for `%u`, `%o`, `%x` and `%X`, also under `h` or `hh` (C passes an `int` there, and the conversion narrows it); under `l`, `ll`, `j` or `z`, `i64` for the signed conversions and `u64` for the others. `%c` is item 6. Where the Rust type differs, the cast is `as` that type, which gives the bits C reads:
   - wider than the conversion reads (an `int64_t` to `%d`): the low bits (s2-errors.md 2.9, the bullet on wider integers); the evidence names the argument, since the reviewer checks each such site;
   - the conversion's width but the other signedness (a `uint32_t` to `%d`, an `int64_t` to `%lu`): the same bits;
   - narrower than `int` (`bool`, `char`, `short`, 8- and 16-bit types, a small enum, a bit field): C's default argument promotion, which `as` reproduces for a signed and an unsigned value alike (a `uint8_t` to `%hhu` is `x as u32`, an `int16_t` to `%hd` is `x as i32`);
   - `int`-wide but narrower than the conversion (an `int` to `%ld` or `%lu`): open question 4.
2. **An error code** held in an `int` (`ret`, `tmp_ret`) that the Rust holds as `ObResult`: its `i32` code, 0 for `Ok(())`, as `K(ret)` prints it (s2-errors.md 2.5 rule 2).
3. **An enumerator**: its integer (the `.0` of the newtype RULEBOOK 2.3 gives an unscoped enum; `as` the conversion's type for a Rust `enum`), then item 1.
4. **Floats**: a `float` is `as f64`, a `double` is `f64` (RULEBOOK 2.6; s8-numerics-platform.md section 1 rule 3). `%e`, `%f`, `%g` and `%a` are `cfmt`'s exact conversions (s2-errors.md 2.9 rule 1).
5. **Strings.**
   - `%s` of a C string: a byte slice without its NUL, or the whole array for a `char` array; `cfmt` prints up to the first NUL, at most the precision (s2-errors.md 2.9, the `CStrArg` bullet). A pointer that can be NULL is `Option<&[u8]>`, `None` printing `(null)` (same bullet).
   - `%.*s` and `%-*.*s`: the precision and the pointer are one slice of that length; a length shorter than the string is a shorter slice (s2-errors.md 2.9's table, which rule 1 keeps for the printf family). A `*` width stays an `i32` argument (rule 1).
   - `%s` of an `ObString`'s `ptr()` with no length: the `ObString`'s bytes. In a `CLIENT_TEXT` or `ENGINE_TEXT` row the evidence shows where the NUL after those bytes comes from (an `ObSqlString`, a literal, a copy that writes the terminator); if it cannot, open question 4.
6. **`%c`**: a `u8` (s2-errors.md 2.9's table), the argument `as u8`, which is the byte C prints from the `int` it receives.
7. **`%p`**, and an address cast to an integer for an integer conversion (`(uintptr_t)this` to `%llx`, src/storage/multi_data_source/mds_node.ipp:98): open question 3. If migration/inventory/sweep/pointer-identity.tsv has a row for the same cast, the answer agrees with it and the evidence cites it.
   - Which pointees have an id follows RULEBOOK 2.4's id row as design amendment A1-1 extends it: the IR objects of s4-sql-front.md 2.2's table, and the temporary expressions and their cached contexts (`TempExprId`, `TempExprCtxId`).
   - A plan's runtime `ObExpr` is named by its position in its expression table (`ExprIdx`), which is not an id. For Q3 it is an object without an id.
   - No row prints one of these objects today. The `%p` pairs name no `ObExpr`, `ObTempExpr` or `ObTempExprCtx`: `grep -cE '%p <- (const )?(ObExpr|ObTempExpr|ObTempExprCtx) \*' migration/inventory/sweep/printf-calls.tsv` prints 0. The 17 pairs typed `?` (`grep -oE '%[-+ #0-9.*]*p <- \?' migration/inventory/sweep/printf-calls.tsv | wc -l`) are none of these either: they point to a datum's bytes, an I/O control block, a hash node's value, three locks, the parser's result fields and tablets.
8. **Conversions outside the list in s2-errors.md 2.9 rule 1** (the `'` grouping flag, the `j` modifier, `%i`, `l` on a float conversion) are still `cfmt`'s (ARCHITECTURE.md §2). The `'` flag groups by `LC_NUMERIC`, which the seekdb binary sets to en_US.UTF-8 (src/observer/main.cpp:698), so the reference prints commas between groups of three digits. The row names such a conversion so the differential test covers it under that locale (s2-errors.md 2.11 rule 7).
9. **The format.**
   - A literal: the source's literal, with adjacent literals joined and `PRI*` macros expanded as macOS arm64 defines them (`PRIu64` is `"llu"`, `PRId64` is `"lld"`; the sweep's `conversions` show the expansion). Copy it from the source, never from the sweep's `format` field. That field shows the argument as written, cuts each run of blanks to one space, and ends in `...` past 160 characters (finding F8). The format of src/sql/printer/ob_schema_printer.cpp:1088 is `",\n  CONSTRAINT "`, with two spaces, where the sweep shows one.
   - A format that names object-like macros (the sweep's `format uses macros`, such as `NEW_LINE` and `COLUMN_SEPARATOR`, src/sql/monitor/ob_sql_plan.h:37-38): each macro is its lowercase `macro_rules!`, which expands to the same literal (ARCHITECTURE.md §14 rule 3; RULEBOOK section 4), and literals and macros that C joins stay joined with `concat!`, which takes both (`buf_printf!(buf, pos, new_line!())`, `concat!("%s", new_line!())`). A macro the sweep did not resolve (`format not a literal: FETCH_ALL_TABLE_HISTORY_WITH_ROWKEY`) is read at its `#define` (src/observer/schema/ob_schema_service_sql_impl.cpp:978), and the evidence gives its conversions. `WITH_COMMA(..)` (src/oblib/lib/utility/ob_print_utils.h:33) also reads its caller's `with_comma`: open question 1 (b).
   - A parameter of the enclosing function (the `format` a variadic template passes on, src/sql/ob_optimizer_trace_impl.h:552): each caller's format, as for the rows inside a printf-family definition (section 4).
   - A `const char *` constant, a ternary of literals, a function that returns literals (`adjust_ddl_format_str(...)`): the same Rust expression; the evidence cites where each value is defined.
   - A catalog format (`ob_errpkt_str_user_error(code)`, a `str_user_error` local): the evidence names each code and its src/share/ob_errno.def line.
   - A format that can hold data (a path, a key, a string from the client or a table; src/observer/main.cpp:712 passes the base dir as `fprintf`'s format): open question 4.
10. **The count.** The arguments must be what the conversions read, each branch of a ternary format checked on its own (finding F2). More arguments, or fewer: open question 4.
11. **The returned value.** The OB code that `databuff_printf`, `BUF_PRINTF`, `append_fmt` and the other code-returning callees give keeps the C++'s handling (RULEBOOK 2.1: `ob_fail!(ret = ...)`; `let _ =` where the C++ ignores it). `snprintf` and `sprintf` return C's `int` count instead (as `lnprintf` and the `v` forms do), the length of the whole text or a negative value on an error: `snprintf!` gives that count as an `i32` (RULEBOOK 2.1: any other `int` return stays `i32`; s2-errors.md 2.9: `cfmt`'s buffer counts the full length as `vsnprintf` does), and the C++'s use of it stays (`nth += snprintf(...)` at src/oblib/common/number/ob_number_v2.cpp:369). The row does not restate either.
12. **A `sizeof` among the arguments.** 18 rows have one (`grep -cE 'arguments: [^;]*sizeof' migration/inventory/sweep/printf-calls.tsv`), and four of them print it: src/oblib/lib/allocator/page_arena.h:495, :508; src/oblib/lib/hash/ob_hashutils.h:1017, which sits in a branch the port removes (section 7); src/storage/memtable/mvcc/ob_keybtree.cpp:1493.
    - RULEBOOK 2.5's `sizeof` row, as design amendment A1-9 widened it, keeps the C++ number as a named constant where the figure feeds a plan, a statistic or client output. That covers a `CLIENT_TEXT` row.
    - A figure printed only to the server log or a stream feeds none of these. It is `size_of::<T>()` of the Rust type, as a storage-side size of an in-memory object is (ARCHITECTURE.md §3.2, default 9).
    - The two readings agree for a type whose Rust size the design fixes (`ObDatum`, the integer types).
    - In the other 14 rows `sizeof` only sizes a buffer passed to a helper, as `sizeof(trace_id_buf)` does in the call at src/observer/mysql/obmp_packet_sender.cpp:630. Nothing prints it.
13. **An argument the design drops**: a memory label or ctx id (`attr_.label_.str_`, a `ctx_id`), which RULEBOOK 2.2 drops, keeping a label only as a `Budget` name (s3-memory.md 3.6.5).
    - The argument is the `Budget`'s name where the C++ allocator becomes a `Budget`.
    - Elsewhere it has no Rust value: pass an empty byte string under `// TODO(port): printf-calls dropped: <argument> (<C++ file:line>)` (section 9), for the author of the core module that replaces the allocator. src/oblib/lib/allocator/ob_slice_alloc.cpp:74 is such a row.
    - A call whose text only names an allocator or a container builds nothing the Rust keeps (src/storage/compaction/ob_compaction_diagnose.cpp:183, :193-194 build the labels of a FIFO allocator and a hash map). As for a removed branch (section 7), the row keeps the value of where the C++ text goes, its target_translation is "None: RULEBOOK 2.2 drops memory labels", and its status is `confirmed`. Where the allocator becomes a `Budget`, the text is that `Budget`'s name, and the call is translated as its value says.

## 7. What target_translation says for each value

- `CLIENT_TEXT`: "`<Rust call>` ...: <the argument notes of section 6>. The text reaches the client (<which output>): `cfmt` only, never `format!` or `write!` (RULEBOOK 2.3 printf row; RULEBOOK 1 Number text row)." Inside a `to_string`, add: that `to_string` stays a function of its name whose text equals the C++'s (RULEBOOK 2.3, `TO_STRING_KV` row: an exact named function where the text reaches the client; RULEBOOK section 6 item 9).
- `ENGINE_TEXT`: "`<Rust call>` ...: <argument notes>. The engine acts on these bytes (<how>): `cfmt` only (RULEBOOK 1 Number text row)."
- `LOG_TEXT`: "`<Rust call>` ...: <argument notes>. Server-log text, kept as the C++ writes it (RULEBOOK 1 Logging row)."
- `STREAM_TEXT`: for a call that writes the stream itself (step 3), open question 2's conservative form; for a call whose buffer reaches a stream later (step 4), "`<Rust call>` ...: <argument notes>. The text reaches <the stream> through <the write> (<C++ file:line>)", with the markers its own callee needs.
- `NOT_TRANSLATED`: "None: <why> (<rule>)", for example "None: the C stays compiled in sql-parser-sys (s7-islands-unsafe.md 7.1)".
- `NOT_PRINTF`: "None under this list: every instantiation binds one of `databuff_printf`'s object templates (src/oblib/lib/utility/ob_print_utils.h:198-199 for `const T &`, ob_print_utils.h:218-219 for `T *const`), which print the object through its `to_string`, or `NULL` for a null pointer."
- `UNKNOWN`: "Conservative: `<Rust call>` as `CLIENT_TEXT` would have it (the exact format and arguments, `cfmt` only)", with the marker `// TODO(port): printf-calls reach: <where the tracing stopped> (<C++ file:line>)`. For a placement gap the marker says the line is placed in no list. For a mixed overload: "Conservative: a trait with one impl per instantiated type (RULEBOOK 2.3, trait dispatch row); the `char *` impls call the printf macro with the string as the format (open question 4), the others print the object", with `// TODO(port): printf-calls overload: ...`.

In every value, a `#define` body row's call sits in what RULEBOOK 2.3 makes of that macro: its own row where it has one (`OB_ASSERT_MSG`), else the function-like macro row (an `#[inline] fn` when the macro evaluates each argument once with fixed types and reads no name from its caller, otherwise a lowercase `macro_rules!`). A call in a platform `#if` arm sits in the matching `#[cfg]` arm, and one under `#ifdef ERRSIM` or `NDEBUG` in the `#[cfg]` RULEBOOK 2.3 gives that test. Each open question that applies adds its marker (section 10), on its own line above the call (ARCHITECTURE.md §14 rule 8), naming the C++ file:line (RULEBOOK 2.10).

**A call in a branch the port removes.** RULEBOOK section 0 lets a translation remove a branch that handled a general allocation failure (2.2), an `is_inited_` or `OB_NOT_INIT` check that a constructor makes meaningless (2.1), a null test on something that cannot be null in Rust (2.3), and what another rule of section 2 removes, such as a `catch` outside an island edge, which has nothing to catch (2.3, the exceptions row). A row whose call sits in such a branch keeps the value of where its bytes would go; its target_translation is "None: the branch is removed (<rule>)", its status `confirmed`, and its evidence cites the test or the `catch`. Which out-of-memory branches go:
- A branch that handles the failure of a general allocation goes, whatever code it sets (s3-memory.md 3.7.1). This covers a raise of `OB_ALLOCATE_MEMORY_FAILED` or `OB_PARSER_ERR_NO_MEMORY` after a null test of an allocation, which oom-sites.tsv leaves out by its definition. It also covers another code: `ret = -1` after the null test at src/oblib/lib/hash/ob_hashutils.h:1016.
- A raise at a check point of an owner or logical limit that s3-memory.md 3.7.2's table names keeps its branch, and so does an errsim raise. The SQL work area's -4013 with dumping off is one of these (design amendment A1-7).
- An oom-sites.tsv row alone does not decide, since its label can be wrong. oom-sites.tsv:158 calls the raise at src/storage/blocksstable/ob_micro_block_cache.cpp:503 budget-backed, but its buffer comes from the caller's allocator (:502), a plain allocation (design amendment A1-10), so that branch goes. No printf-family call sits in a branch A1-7 or A1-10 decides today.

Examples: src/sql/parser/parse_node.c:130, after the null test of `parser_alloc` at parse_node.c:128; src/observer/omt/ob_worker_processor.cpp:116, in a `catch (OB_BASE_EXCEPTION &)` (ob_worker_processor.cpp:115), while the only throws of that type in src (src/sql/engine/px/ob_px_util.cpp:757, :759; `grep -rn 'throw OB_EXCEPTION' src`) are caught at ob_px_util.cpp:796. When no rule and no row show that the branch goes, the call is translated as its value says.

## 8. Evidence

Every row starts `sweep: printf-calls.tsv:<N> (data row <N-1>).`

Every translated row then shows:
1. `Types:` for each argument the sweep types `?` or types wrongly, the declaration that gives the type; each cast item 1 of section 6 adds, named.
2. `Format:` only for a format that is not a literal: each value it can hold and where that value is defined.
3. The path, by value:

| Value | The evidence also shows |
|---|---|
| `CLIENT_TEXT` | the buffer or object the call writes (its declaration) and one path to a client output, each move with its file:line, ending at the line where the text enters the output (the `LOG_USER_*` call, the `cells_` set, the `PlanText` write, the schema field and the line that returns it) |
| `ENGINE_TEXT` | the buffer and every path to its engine use, each with file:line (the read or write that runs the SQL, the compare, the open) |
| `LOG_TEXT` | for a server-log macro, the macro's definition line; for a buffer or a `to_string`, every path to its log lines, and for a `to_string` the search for explicit uses (the command) and what each hit does |
| `STREAM_TEXT` | the stream: `stdout` or `stderr` as written, or the `FILE *` variable and the line that opens or passes it; for a buffer (step 4), every path to its write, each move with its file:line |
| `NOT_TRANSLATED` | the not-translated.tsv line and its reason, or s7-islands-unsafe.md 7.1 for vendored zstd and grammar actions; for a file split between lists, the range that covers the line; nothing else |
| `NOT_PRINTF` | each instantiation's type at the format position and the call that fixes it |
| `UNKNOWN` | what was followed, each move with its file:line, and where it stopped |

A row with status `unknown` ends with `Question:` and the question in one sentence.

## 9. Unknown rows

- The classification is `UNKNOWN`, with status `unknown`, when step 1, 2 or 4 cannot settle the value, or when a skeptic reviewer's refutation stands after one revision (prompt 02).
- The classification stays the value and only the status is `unknown` when the value is settled but the translation carries a `TODO(port)`: an open question of section 10 applies, or an argument's type cannot be resolved (then the argument is passed `as` the conversion's C type, under `// TODO(port): printf-calls type: <argument> not resolved (<C++ file:line>)`), or an argument the design drops has no Rust value (section 6, item 13). A row can be `CLIENT_TEXT` and `unknown` because its callee's Rust form is open. Count unknown rows by the `status` column.
- `confirmed` means: the classification is not `UNKNOWN` and the target_translation carries no `TODO(port)`.
- The target_translation is deterministic either way: RULEBOOK 2.10's UNKNOWN rule, in the conservative forms of sections 7 and 10. When an open question's answer is recorded (in decisions.md or a RULEBOOK amendment), rows follow it, drop its marker, and become `confirmed` if nothing else is open.

## 10. Open questions

The design leaves these cases undecided. Each gives a recommended answer: under decisions.md row 5c the orchestrator takes it and records it (in decisions.md, or in the design section that owns the rule) instead of waiting for the developer, and rows then follow section 9's last item. Until that record exists, each case takes the conservative translation below, with status `unknown` and its marker.

**Q1. What is the Rust form of a printf-family callee the design does not name?** The design names `databuff_printf!`, `databuff_printf_2!`, `databuff_printf_3!`, `buf_printf!` and `snprintf!` (RULEBOOK 2.3; s2-errors.md 2.9 rule 2), `forward_user_error_msg!` and `log_user_error!` with its siblings (s2-errors.md 2.7), and the log macros' leading `ret` (s2-errors.md 2.5 rule 1). It gives no form for (a) the C-variadic functions and their `va_list` forms of section 5 row 9: a Rust function takes a fixed argument list, and RULEBOOK 2.3's slice-or-macro answer is for variadic templates; nor for (b) the macros of row 10, and `WITH_COMMA` in a format (section 6, item 9), which read names from their caller's scope that a `macro_rules!` cannot see, so its parameters must say which it takes, and RULEBOOK 2.3 settles that only for `BUF_PRINTF`.
- Conservative translation: the call written as a macro named after the callee in lowercase (ARCHITECTURE.md §14 rule 3), with the C++ arguments in the C++ order, a method's receiver first (`assign_fmt!(sql, "...", a, b)`, `data_printf!("...", x)`), each argument as section 6 gives it, under `// TODO(port): printf-calls Q1: no Rust form named for <callee> (<C++ file:line>)`.
- Recommended answer: (a) a macro of the same name beside the function's translation, taking the receiver or the fixed leading arguments, then the format and the arguments as `CArg` values checked at run time, formatting through `cfmt` and returning the function's codes, as s2-errors.md 2.9 rule 2's macros do; the `va_list` forms become functions taking the `CArg` slice. (b) The macro takes the names its C++ body reads from the caller as its first arguments, in the order the body first uses them, a buffer and its length passing as one buffer, as `buf_printf!(buf, pos, ..)` does.

**Q2. What does translated code do with a printf-family write to stdout, stderr or a `FILE *`?** RULEBOOK 1 bans `println!`, `eprintln!` and `dbg!`, allowing only `eprintln!` and only in the seekdb crate (s8-numerics-platform.md 7.4), and sends files through ob-runtime's IO layer; s6-storage.md 4.3 shows the seekdb crate writing a message with `std::io::stderr().write_all`. No rule says what `printf`, `fprintf` and the macros over them (section 5 row 11) become, in the seekdb crate or elsewhere.
- Conservative translation: the call written as a macro named after the callee in lowercase with the C++ arguments, the stream included where the C++ passes one (`fprintf!(stderr, "...", x)`, `log_stderr!("...", x)`), each argument as section 6 gives it, under `// TODO(port): printf-calls Q2: stream write to <stream> (<C++ file:line>)`.
- Recommended answer: format through `cfmt` into an owned buffer and write it with `std::io::Write::write_all` to the same stream (for a `FILE *`, the handle ob-runtime's IO layer gives), ignoring the result where the C++ ignores `fprintf`'s, and keeping `LOG_STDERR`'s and `LOG_STDOUT`'s `isatty` test.

**Q3. What does a `%p`, or an address cast to an integer, print?** s2-errors.md 2.9 rule 1 says a translated `%p` call passes an id's `to_i64()`, or 0 for NULL, and RULEBOOK 2.4 turns an address packed into an integer into `id.to_i64()`. ARCHITECTURE.md §4.1 rule 6 and s4-sql-front.md 2.1 rule 3 say an arena id is never printed and drop the `KP(` log keys. The two disagree for an object with an id. That is an IR object, and since design amendment A1-1 also a temporary expression or its cached context (`TempExprId`, `TempExprCtxId`; RULEBOOK 2.4). Neither rule says what to pass for an object that has no id: a buffer, a memtable, an allocator block, a thread, a session, or a plan's runtime `ObExpr`, which A1-1 names by its position in its expression table (`ExprIdx`).
- Conservative translation: keep the conversion, under `// TODO(port): printf-calls Q3: %p of <what> (<C++ file:line>)`. Pass `id.to_i64()` for an object with an id (the rule that names `%p`) and 0 for a null pointer. For any other object pass the address of the Rust value that stands for the pointee (`std::ptr::from_ref(x).addr() as u64`). For a runtime `ObExpr` that value is its element of the expression table, or of the list the run's frame state keeps past the table's end (s5-execution.md rule 3.10).
- Recommended answer: record s2-errors.md 2.9 rule 1 as the exception to ARCHITECTURE.md §4.1 rule 6 for `%p` and for addresses printed as integers, and pass the Rust value's address for objects without an id. Neither can equal the reference's text, since its addresses change from run to run; the `CLIENT_TEXT` ones (EXPLAIN EXTENDED's expression addresses, src/sql/resolver/expr/ob_raw_expr.cpp:1309; a virtual table's `%p` or `%lx` cell, src/observer/virtual_table/ob_all_virtual_memstore_allocator_info.cpp:154) are listed for the judge's owner.

**Q4. What do calls whose arguments do not match their conversions become, where s2-errors.md 2.9 does not say?** It settles a wider integer (cast with `as`). s2-errors.md 2.7 rule 5 and RULEBOOK 2.1 settle a wrong count only for user messages (write what `vsnprintf` read, mark `BUG(port)`); for the printf family s2-errors.md 2.9 rule 2 says only that a mismatch is a `debug_assert!` failure. Four cases are left:
- (a) more arguments than the conversions read. Conservative: pass only those C reads, in order; an extra argument that calls a function stays as `let _ = <call>;` just before the call.
- (b) an `int`-wide argument for a 64-bit conversion (an `int` to `%ld`): C reads 8 bytes where 4 were passed. Conservative: `as i64` or `as u64` by the argument's own signedness.
- (c) fewer arguments than the conversions read, or a format that can hold data, whose `%` C interprets. Conservative: pass the data as the format and the arguments as the C++ does.
- (d) `%s` of an `ObString`'s `ptr()` in a `CLIENT_TEXT` or `ENGINE_TEXT` row with no NUL shown after the bytes: C prints up to the first NUL, possibly past the string. Conservative: pass the `ObString`'s bytes.

Each under `// TODO(port): printf-calls Q4: <which case, and the argument> (<C++ file:line>)`. Recommended answer: (a) drop them and mark `BUG(port)`, as s2-errors.md 2.7 rule 5 does for user messages; (b) and (c) the conservative forms, marked `BUG(port)` (the C++ reads what was never passed); (d) the slice, marked `BUG(port)` only where the bytes are not NUL-terminated.

## 11. Findings for the orchestrator

These are not rules; each concerns a file this vocabulary does not own.

- **F1.** The rows in src/sql/parser/sql_parser_mysql_mode.y and .l, src/pl/parser/pl_parser_mysql_mode.y and .l, and the vendored src/oblib/lib/compress/zstd_1_3_8/zstd_src files are on none of not-translated.tsv, manifest.tsv and core-manifest.tsv (`grep -c -E 'zstd_src|parser_mysql_mode\.[yl]'` over the three lists prints 0 for each). They hold 14 rows (`awk -F'\t' '$1 ~ /\.(y|l)$/ || $1 ~ /\/zstd_src\//' migration/inventory/sweep/printf-calls.tsv | wc -l`). Step 1 classifies them `NOT_TRANSLATED`; not-translated.tsv should list them.
- **F2.** For a ternary of two literal formats the sweep adds the conversions of both branches, so it can report a count mismatch that is not there: src/sql/optimizer/stat/ob_opt_stat_sql_service.cpp:733 has two `%.*s` in each branch, and the call passes the four arguments each branch reads; the sweep says 8.
- **F3.** Sweep argument types can be wrong: `%ld <- int` for `types_.at(i)` at src/pl/ob_pl_stmt.cpp:2669, whose array is `ObPLSEArray<int64_t>` (src/sql/pl/ob_pl_stmt.h:1297). Of the 37 rows the sweep marks with a count mismatch (`grep -c 'the conversions take' migration/inventory/sweep/printf-calls.tsv`), check each against its format too.
- **F4.** Some `#define` rows the sweep calls a fixed format pass their format parameter on, so their uses, which carry the conversions, have no rows: `MPRINT` (src/observer/main.cpp:406, src/observer/ob_command_line_parser.cpp:114, src/observer/ob_win_service.cpp:26), `BACKTRACE` and `BACKTRACE_RET` (src/oblib/lib/utility/ob_macro_utils.h:694, :703), `SQL_COL_APPEND_VALUE` and `SQL_COL_APPEND_TWO_VALUE` (src/oblib/common/mysqlclient/ob_mysql_proxy.h:231-244). Classify such a row by its uses (section 4), list their formats in the evidence with the grep that finds them, and say they have no rows. The sweep should give the uses rows.
- **F5.** In summary.tsv's `printf-calls` row, the `plan_figure` column holds the current definition, and the `definition` column holds older prose (its "Not seen" list says the rows list argument expressions without types) followed by the current measured counts. The rows match the `plan_figure` text.
- **F6.** The sibling vocabularies differ. The line: ret-compare.md, reset.md and tmp-ret.md write a `line N: ` prefix, as here; const-cast.md writes a ` (line N)` suffix. The status: const-cast.md, reset.md and tmp-ret.md mark `unknown` exactly when the value is `UNKNOWN` (tmp-ret.md keeps a row that an open question touches `confirmed`); ret-compare.md and this file also mark a settled value `unknown` while its translation carries a `TODO(port)`. The merge key is per list, so nothing collides, but the joint audit and any count of unknown rows read them together. Recording the recommended answers of section 10 (decisions.md row 5c) turns this file's rows whose only open item is Q1-Q4 `confirmed`, which removes most of the difference.
- **F7.** `ObShowResolver::ObSqlStrGenerator::gen_select_str` and `gen_from_str` (src/sql/resolver/cmd/ob_show_resolver.cpp:2250, :2271) are C-variadic and hand their `va_list` to `databuff_vprintf`, but their format parameters are named `select_str` and `subquery_str`, so the sweep has only the two `databuff_vprintf` rows inside them (ob_show_resolver.cpp:2262, :2287) and none for their calls: the ones in `GEN_SQL_STEP_1` and `GEN_SQL_STEP_2` (ob_show_resolver.cpp:49, :58), whose uses carry the SHOW formats of the 54 `DEFINE_SHOW_CLAUSE_SET` lines (`grep -c '^DEFINE_SHOW_CLAUSE_SET(' src/sql/resolver/cmd/ob_show_resolver.cpp`), and the direct ones at ob_show_resolver.cpp:976-977 and :984-985. Their text is the SELECT the SHOW resolver parses as the client's statement, whose aliases are column names (section 4, client output 1). The sweep should give those calls rows; until then the two `databuff_vprintf` rows stand for them (section 4). The same parameter-name test leaves out the variadic constructor of `ObDIActionGuard` (src/oblib/lib/stat/ob_diagnostic_info_guard.h:256), which nothing calls (`grep -rn 'NS_PROGRAM\|NS_MODULE\|NS_ACTION' src` finds only its own header).
- **F8.** The sweep's `format` field is not the format, and the `text` column holds only the call's first line.
  - The field shows the format argument as written: adjacent literals are not joined, and `PRI*` macros keep their names. Each run of blanks becomes one space, and the field ends in `...` past 160 characters. This is the `collapse` of migration/scripts/sweep_inventory.py:5723-5725, called at sweep_inventory.py:7711. Only `conversions` comes from the joined and expanded value.
  - Section 12 gives the counts: 47 formats cut, and at least 59 whose literal on the call's first line lost blanks. Among them is src/sql/printer/ob_schema_printer.cpp:1088, whose SHOW CREATE TABLE text has `CONSTRAINT` after two spaces.
  - A row's format is copied from the source (section 6, item 9). The sweep should print the joined literal and the whole call.

## 12. Citation rules and the check

From the pilot's evidence defects:

- Every line number names its file, unless the same clause already names it. A file named inside parentheses holds only inside them: after the `)`, name the file again.
- Cite the path from src/ when the file name is not unique (many headers under src/sql and src/pl forward to src/query/api or src/sql/pl; src/pl/ob_pl_stmt.h includes src/sql/pl/ob_pl_stmt.h): `find src -name <basename> | wc -l`.
- Cite the line that holds the construct, not a neighbouring `if`, declaration or comment.
- Never state a count you did not get from a command; give the command.
- Do not list unused locals as readers, or functions with no caller as callers.
- Put rule citations that name a `.md` file in parentheses, so the checker does not read a following line number against the design file.

Check every shard, and fix every flag:

```
python3 -B migration/scripts/cite_check.py <shard.tsv>
python3 -B migration/scripts/cite_check.py <shard.tsv> show
```

The first prints `MISSING`, `AMBIGUOUS` and `OUT OF RANGE` flags; the second prints each cited line, to confirm it holds what the row says.

The counts in this file:

```
wc -l migration/inventory/sweep/printf-calls.tsv
awk -F'\t' 'NR>1{c[$1"\t"$3"\t"$4]++} END{for(k in c) if(c[k]>1) n+=c[k]; print n}' migration/inventory/sweep/printf-calls.tsv
awk -F'\t' 'NR>1{c[$1":"$2]++} END{for(k in c) if(c[k]>1) n++; print n+0}' migration/inventory/sweep/printf-calls.tsv
grep -c 'the conversions take' migration/inventory/sweep/printf-calls.tsv
```

They print 6507, 1087, 0 and 37. Finding F8's counts, of formats cut at 160 characters and of formats whose literal on the call's first line lost blanks, come from these commands, which print 47 and 59:

```
grep -cE '; format .{150,157}\.\.\.; conversions ' migration/inventory/sweep/printf-calls.tsv
python3 - <<'EOF'
import re
lit = re.compile(r'"((?:[^"\\]|\\.)*)"')
n = 0
for r in filter(None, open('migration/inventory/sweep/printf-calls.tsv').read().split('\n')[1:]):
    c, text = r.split('\t')[3:5]
    m = re.search(r'(?:^|; )format (?!is |not a literal)(.*?); conversions ', c)
    if m and not m[1].endswith('...'):
        shown = lit.findall(m[1])
        n += any(re.search('[ \t]{2,}', s) and re.sub('[ \t]+', ' ', s) in shown and s not in shown for s in lit.findall(text))
print(n)
EOF
```

## 13. Worked examples

Three real sites. The block below is the exact TSV (header and rows); `cite_check.py` prints no flag for it, and its `show` output puts each citation on the line the row names.

- **Example 1**, `CLIENT_TEXT`, `confirmed`: `BUF_PRINTF("%08X", hash_val)`, the query-block name that EXPLAIN prints (the text ARCHITECTURE.md §2 and §10 name). The path runs through a copy, a container and the plan printer to an EXPLAIN row.
- **Example 2**, `ENGINE_TEXT`, `unknown`: `assign_fmt` builds SQL the engine runs itself; the value is settled, but `assign_fmt`'s Rust form is open (Q1), so the row carries Q1's conservative form and marker. It also shows two `?` types resolved and the `%.*s` pairs becoming slices.
- **Example 3**, `LOG_TEXT`, `confirmed`: a server-log macro at INFO (no `ret`), in the non-Windows arm of an `#ifdef`, with the `'` flag, which the reference prints with commas because the binary sets `LC_NUMERIC`.

They were checked again on 2026-09-27, after design amendment 1:
- No amendment entry reaches them.
- Each format equals the source's literal, so finding F8 changes none of them.
- `cite_check.py` still prints no flag, and each cited line still holds what the row says.

```
file	symbol	source_construct	classification	target_translation	evidence	status
src/sql/resolver/dml/ob_sql_hint.cpp	ObQueryHint::generate_qb_name_for_stmt	line 389: BUF_PRINTF call; format "%08X"; conversions %08X; 1 arguments: hash_val; argument types: uint32_t; conversion and argument pairs: %08X <- uint32_t	CLIENT_TEXT	buf_printf!(buf, pos, "%08X", hash_val) inside the C++'s OB_FAIL test, the locals BUF_PRINTF reads passed by name (RULEBOOK 2.3, printf row). hash_val is a u32 and %08X reads an unsigned int, so it is passed as it is; the 0 flag and the width 8 are in cfmt's list (s2-errors.md 2.9 rule 1). The text is a query-block name that EXPLAIN prints: cfmt only, never format! or write! (RULEBOOK 2.3, printf row; RULEBOOK 1, Number text row).	sweep: printf-calls.tsv:5740 (data row 5739). Types: hash_val is a uint32_t (ob_sql_hint.cpp:374), filled by get_qb_name_source_hash_value (ob_sql_hint.cpp:382). Buffer: the local char array buf (ob_sql_hint.cpp:369) with buf_len (ob_sql_hint.cpp:370) and pos (ob_sql_hint.cpp:371), the names BUF_PRINTF reads (ob_print_utils.h:1016). Path: try_add_new_qb_name receives buf and pos (ob_sql_hint.cpp:391-392) and copies the name into qb_name with ob_write_string when it is new (ob_sql_hint.cpp:430); otherwise the loop (ob_sql_hint.cpp:387) tries the next hash_val (ob_sql_hint.cpp:396). qb_name joins the statement's QbNames (ob_sql_hint.cpp:386, :407). QbNames::print_qb_names writes each name into a PlanText (ob_sql_hint.cpp:869); ObSqlPlan::get_qb_name_trace calls it for the query's stmt_id_map_ (ob_sql_plan.cpp:669, :672) and keeps the text as the first plan item's remarks_ (ob_sql_plan.cpp:678). For EXPLAIN, store_sql_plan_for_explain fills the plan items (ob_sql_plan.cpp:116), which reaches get_qb_name_trace (ob_sql_plan.cpp:552), and formats them (ob_sql_plan.cpp:122); for EXPLAIN EXTENDED and EXTENDED_NOADDR the remarks are printed under the Qb name trace heading (ob_sql_plan.cpp:1014, :1019, :1963), and each formatted line becomes an EXPLAIN result row (ob_explain_log_plan.cpp:72, :87). (ARCHITECTURE.md section 10 names these query-block names among the texts the judge compares unmasked.) One conversion, one argument; no %p.	confirmed
src/pl/sys_package/ob_dbms_vector_mysql.cpp	ObDBMSVectorMySql::index_vector_memory_estimate	line 299: assign_fmt call; format "SELECT cast(sum(table_rows) as unsigned) as sum, max(table_rows) as max from information_schema.PARTITIONS WHERE table_schema='%.*s' and table_name='%.*s'"; conversions %.*s %.*s; 4 arguments: database_name.length(), database_name.ptr(), table_name.length(), table_name.ptr(); argument types: int32_t | char * or const char * | ? | ?; conversion and argument pairs: %.*s width or precision <- int32_t, %.*s <- char * or const char *, %.*s width or precision <- ?, %.*s <- ?	ENGINE_TEXT	Conservative (Q1): assign_fmt!(query_string, "SELECT cast(sum(table_rows) as unsigned) as sum, max(table_rows) as max from information_schema.PARTITIONS WHERE table_schema='%.*s' and table_name='%.*s'", database_name, table_name) inside the C++'s OB_FAIL test, the receiver first, then the format and the arguments in the C++ order, under the marker // TODO(port): printf-calls Q1: no Rust form named for the C-variadic ObSqlString::assign_fmt (src/pl/sys_package/ob_dbms_vector_mysql.cpp:299). Each %.*s takes one byte slice for the C++ length and pointer, here each whole ObString, since each length is that string's own (s2-errors.md 2.9: the %.*s row of its table, which rule 1 keeps for the printf family). The engine runs this text as SQL: cfmt only, never format! (RULEBOOK 1, Number text row; RULEBOOK 2.3, printf row).	sweep: printf-calls.tsv:1998 (data row 1997). Types: database_name and table_name are ObString locals (ob_dbms_vector_mysql.cpp:246); ObString::length returns obstr_size_t, an int32_t (ob_string.h:285, :44), and ptr returns a char pointer (ob_string.h:287-288), so the pair the sweep left as ? is an int32_t and a char pointer, like the first. Buffer: the local ObSqlString query_string (ob_dbms_vector_mysql.cpp:297). Engine use: GCTX.sql_proxy_->read runs the text as an inner query (ob_dbms_vector_mysql.cpp:301); the function reads two result columns (ob_dbms_vector_mysql.cpp:305-306) into num_vectors and tablet_max_num_vectors (ob_dbms_vector_mysql.cpp:310-311), and what it returns is the estimate string built from those numbers (ob_dbms_vector_mysql.cpp:325, :327), not the query text; nothing else in the block reads query_string (ob_dbms_vector_mysql.cpp:296-313). Two %.*s read four arguments and the call passes four; no %p. Question: what is the Rust form of a C-variadic printf-family function such as ObSqlString::assign_fmt (ob_sql_string.h:64), which the design does not name?	unknown
src/observer/main.cpp	inner_main	line 771: _LOG_INFO call; format "Virtual memory : %'15ld byte"; conversions %'15ld; 1 arguments: memory_used; argument types: int64_t; conversion and argument pairs: %'15ld <- int64_t; writes the server log	LOG_TEXT	_log_info!("Virtual memory : %'15ld byte", memory_used) in the #[cfg(not(windows))] arm that stands for the C++'s #else of #ifdef _WIN32 (RULEBOOK 2.3, platform #if row); INFO takes no ret (s2-errors.md 2.5 rule 1). memory_used is an i64 and %'15ld reads a long, so it is passed as it is. The ' flag is not among the flags cfmt's list names (s2-errors.md 2.9 rule 1), but cfmt implements every conversion this list finds (ARCHITECTURE.md section 2); the reference prints the digits in groups of three with commas, since the binary has set LC_NUMERIC to en_US.UTF-8 by then (main.cpp:698). Server-log text, kept as the C++ writes it (RULEBOOK 1, Logging row; ARCHITECTURE.md section 14 rules 3 and 7).	sweep: printf-calls.tsv:1442 (data row 1441). Types: memory_used is an int64_t (main.cpp:665). Server log: _LOG_INFO (ob_log_module.h:1221) joins the file's USING_LOG_PREFIX, SERVER (main.cpp:17), into _SERVER_LOG (ob_log_module.h:375), which is _OB_MOD_LOG (ob_log_module.h:308); that calls _OB_PRINT (ob_log_module.h:311), which hands the format and the arguments to OB_LOGGER.log_message_fmt (ob_log_module.h:232); nothing else reads the text. The call is the #else arm of #ifdef _WIN32 (main.cpp:768, :770, :772); the Windows arm is its own row (main.cpp:769). inner_main (main.cpp:645) calls setlocale for LC_NUMERIC (main.cpp:698) before this line. One conversion, one argument; no %p.	confirmed
```

## 14. The samples

### 14.1 The sample this vocabulary was drawn from

47 files, one row each: the first row of every 14th distinct file of the sweep (`awk -F'\t' 'NR>1 && !seen[$1]++ {print NR}' migration/inventory/sweep/printf-calls.tsv | awk 'NR%14==7'`), plus the rows the marks single out (`%p`, `forwards a va_list`, `format not a literal`, `overload not resolved`, the count marks, main.cpp). Read at the source. The value is this file's answer; a row written from it still needs the full evidence of section 8. "Line" is the line in printf-calls.tsv.

| Line | Site | Value (status) | Why |
|---|---|---|---|
| 25 | src/logservice/palf/log_io_utils.cpp:57 | `ENGINE_TEXT` | a path that `_stat64` reads (log_io_utils.cpp:64), in a `_WIN32` arm |
| 79 | src/oblib/common/log/ob_log_generator.cpp:141 | `LOG_TEXT` (unknown: Q3) | `_OB_LOG(ERROR, ..)` with two `%p` |
| 328 | src/oblib/common/object/ob_obj_type.cpp:168 | `CLIENT_TEXT` | a `#define` row whose uses (ob_obj_type.cpp:306, :325, :335, :336) define `ob_<type>_str`, entries of `ob_sql_type_str`'s table (ob_obj_type.cpp:595; `ob_date_str` at ob_obj_type.cpp:622), which gives information_schema.COLUMNS its type text (src/observer/virtual_table/ob_information_columns_table.cpp:430) |
| 526 | src/oblib/lib/allocator/ob_block_alloc_mgr.h:44 | `LOG_TEXT` | `_OB_LOG_RET`: passes its code, no `ret` |
| 569 | src/oblib/lib/compress/zstd_1_3_8/zstd_src/zstdmt_compress.c:46 | `NOT_TRANSLATED` | vendored zstd, on no list (F1) |
| 674 | src/oblib/lib/hash/ob_darray.h:364 | `STREAM_TEXT` (unknown: Q2) | `fprintf` to the `FILE *fp` parameter (ob_darray.h:359) |
| 917 | src/oblib/lib/oblog/ob_async_log_struct.cpp:53 | `STREAM_TEXT` (unknown: Q2, Q3) | `LOG_STDERR` with `%p` of a pointer that is NULL on this branch |
| 1066 | src/oblib/lib/signal/ob_signal_handlers.cpp:116 | `ENGINE_TEXT` (unknown: Q1) | `lnprintf` builds the path `opendir` reads (ob_signal_handlers.cpp:117) |
| 1105 | src/oblib/lib/thread/thread.cpp:216 | `ENGINE_TEXT` (unknown: Q3) | a dump file path `open` creates (thread.cpp:223), with `%p`, in an `__APPLE__` arm |
| 1281 | src/oblib/lib/utility/utility.cpp:76 | `ENGINE_TEXT` | `copy_path` fills the cgroup mount info (utility.cpp:163-176), compared at utility.cpp:273-274 and joined into a file path at utility.cpp:334 |
| 1406 | src/observer/main.cpp:38 | `ENGINE_TEXT` | the coverage profile path given to `__llvm_profile_set_filename` (main.cpp:46) |
| 1471 | src/observer/omt/ob_worker_processor.cpp:116 | `LOG_TEXT` | `_LOG_ERROR` takes `ret` first; the `catch` it sits in follows RULEBOOK 2.3's exceptions row |
| 1783 | src/observer/virtual_table/ob_all_virtual_kvcache_store_memblock.cpp:144 | `CLIENT_TEXT` | a `%lf` string read back into a number cell (section 4) |
| 1814 | src/observer/virtual_table/ob_information_columns_table.cpp:742 | `CLIENT_TEXT` | appended to the COLUMN_TYPE text set into a cell at ob_information_columns_table.cpp:748 |
| 1918 | src/observer/virtual_table/ob_virtual_show_trace.cpp:40 | `CLIENT_TEXT` | the tags JSON (ob_virtual_show_trace.cpp:166, :226) goes into the TAGS cell (ob_virtual_show_trace.cpp:395) |
| 1998 | src/pl/sys_package/ob_dbms_vector_mysql.cpp:299 | `ENGINE_TEXT` (unknown: Q1) | example 2 |
| 2128 | src/rootserver/ddl_task/ob_fts_index_build_task.cpp:1595 | `CLIENT_TEXT` | the long-ops message, copied at src/rootserver/ddl_task/ob_ddl_task.cpp:912 and shown by src/observer/virtual_table/ob_all_virtual_long_ops_status.cpp:94; the call's code is ignored (`let _ =`) |
| 2213 | src/rootserver/ob_ddl_service.cpp:326 | `CLIENT_TEXT` | `LOG_USER_ERROR` at ob_ddl_service.cpp:336 |
| 2394 | src/share/cache/ob_kvcache_hazard_domain.cpp:136 | `LOG_TEXT` | `_OB_LOG(INFO, ..)`; the sweep's `?` for `ATOMIC_LOAD_RLX(&retired_memory_size_)` is resolved from the member's declaration |
| 2444 | src/share/config/ob_system_config_key.h:78 | `ENGINE_TEXT` | the key of `ObSystemConfig`'s hash map (src/share/config/ob_system_config.h:35), compared at ob_system_config_key.h:60, :67 |
| 2488 | src/share/ob_admin_dump_helper.cpp:27 | `STREAM_TEXT` (unknown: Q2) | `fprintf(stdout, ..)` |
| 2755 | src/share/object/ob_obj_cast.cpp:167 | `CLIENT_TEXT` | `LOG_USER_ERROR` at ob_obj_cast.cpp:170 |
| 2982 | src/sql/das/ob_das_domain_utils.cpp:249 | `ENGINE_TEXT` | an index name compared at ob_das_domain_utils.cpp:256 |
| 3068 | src/sql/engine/cmd/ob_vector_index_refresh.cpp:106 | `ENGINE_TEXT` (unknown: Q1) | inner SQL read at ob_vector_index_refresh.cpp:113 |
| 3127 | src/sql/engine/expr/ob_expr_format_bytes.cpp:126 | `CLIENT_TEXT` | `sprintf` into the result datum (ob_expr_format_bytes.cpp:135): `snprintf!` |
| 3198 | src/sql/engine/expr/ob_expr_st_distance_sphere.cpp:139 | `CLIENT_TEXT` | `LOG_USER_ERROR` at ob_expr_st_distance_sphere.cpp:142 |
| 3542 | src/sql/monitor/show_trace/ob_show_trace.cpp:205 | `CLIENT_TEXT` | the trace id, read at src/observer/virtual_table/ob_virtual_show_trace.cpp:163 and set into the TRACE_ID cell at ob_virtual_show_trace.cpp:352 |
| 3691 | src/sql/optimizer/ob_log_expand.cpp:45 | `CLIENT_TEXT` | plan text: `ObLogExpand::get_plan_item_info` |
| 3789 | src/sql/optimizer/ob_log_subplan_filter.cpp:146 | `CLIENT_TEXT` | plan text: `ObLogSubPlanFilter::get_plan_item_info` |
| 4088 | src/sql/optimizer/stat/ob_dbms_stats_history_manager.cpp:245 | `ENGINE_TEXT` (unknown: Q1) | a predicate placed into inner SQL (ob_dbms_stats_history_manager.cpp:246-249) |
| 4255 | src/sql/parser/ob_fast_parser.cpp:984 | `STREAM_TEXT` (unknown: Q2) | `fprintf(stderr, ..)`, its code ignored |
| 4379 | src/sql/printer/ob_dml_stmt_printer.cpp:73 | `CLIENT_TEXT` (unknown: Q1) | `DATA_PRINTF` in the SQL printer |
| 5484 | src/sql/resolver/ddl/ob_ddl_resolver.h:907 | `CLIENT_TEXT` | a generated partition name set on the partition (ob_ddl_resolver.h:913), which the schema printer prints (src/sql/printer/ob_schema_printer.cpp:2154) |
| 5752 | src/sql/resolver/expr/ob_raw_expr.cpp:348 | `CLIENT_TEXT` | `get_type_and_length` for EXPLAIN EXTENDED |
| 5961 | src/sql/session/ob_basic_session_info.cpp:524 | `LOG_TEXT` | the text becomes `user_at_host_name_` (ob_basic_session_info.cpp:530), which the session's serialization writes and reads back into the same field (ob_basic_session_info.cpp:3406, :3555, :3573-3574) and which only `get_user_at_host` reads (src/sql/session/ob_basic_session_info.h:614), whose one use is the session's `to_string` key (ob_basic_session_info.cpp:3169); the `%.*s` pairs become slices |
| 6003 | src/storage/blocksstable/ob_macro_block_handle.cpp:95 | `CLIENT_TEXT` | copied into the bad-block info (src/storage/blocksstable/ob_block_manager.cpp:562-564, :572) that src/observer/virtual_table/ob_all_virtual_bad_block_table.cpp:71 shows; `ret` passes its code (section 6, item 2) |
| 6115 | src/storage/lob/ob_lob_seq.cpp:401 | `LOG_TEXT` | a `to_string` with no explicit use: `grep -rnE 'seq_id(_end\|_generator)?_?(\.\|->)to_string\(\|convert\((\*)?seq_id' src` prints nothing (the pipes are escaped for the table) |
| 6355 | src/storage/tablelock/ob_table_lock_live_detector.cpp:245 | `ENGINE_TEXT` | a table name for the inner `delete_row` (ob_table_lock_live_detector.cpp:261) |
| 6426 | src/storage/tx/ob_tx_data_define.cpp:228 | `STREAM_TEXT` (unknown: Q2) | `fprintf` to the `FILE *fd` parameter (ob_tx_data_define.cpp:218) |
| 6503 | src/storage/tx_table/ob_tx_table.cpp:1036 | `ENGINE_TEXT` | a dump file name that `fopen` opens (ob_tx_table.cpp:1041) |

Not finished in the sample (each is a to_string or a buffer whose readers were not all followed): 466 (src/oblib/common/xml/ob_tree_base.h:93), 823 (src/oblib/lib/json/ob_yson.cpp:47), 2640 (src/share/ob_lease_struct.cpp:98), 2807 (src/share/schema/ob_schema_struct.cpp:695, a system variable's name stored through the bootstrap's `ObSysParam` array), 6081 (src/storage/concurrency_control/ob_trans_stat_row.cpp:52, a row's `trans_info_` read by DML), 6189 (src/storage/memtable/ob_memtable_key.h:122), 6255 (src/storage/multi_data_source/mds_node.ipp:98, an address printed through `%llx`: Q3).

What the sample taught: the reach is rarely the file's first guess (row 5961's `user@host` looks client-bound and, after a round trip through the session's serialization, reaches only a log key; row 6003's I/O error text looks like a log line and reaches a virtual table); most `unknown` statuses come from a callee's open form (Q1, Q2), not from the reach; and the sweep's types and `#define` rows need checking (F3, F4).

### 14.2 The review sample

The review drew 40 rows with `random.Random(3).sample` over the list's 6,506 data rows. The draw is the same whether the population is the rows or their indexes. This command prints the printf-calls.tsv lines:

```
python3 -c "import random; r=open('migration/inventory/sweep/printf-calls.tsv').read().split('\n')[1:-1]; print(sorted(i+2 for i in random.Random(3).sample(range(len(r)),40)))"
```

Each row was classified with this file on 2026-09-27, after design amendment 1, and read at its site. Line 526 is also in 14.1. Step 1 places all 40 in a manifest.

Before the review:
- Lines 1235 and 1244 fitted no value. They sit in specializations of `databuff_print_key_obj`, whose callers are every key of their type in every `to_string`, and step 4 gave no way to list those callers. Section 4's bullet on generic printers now does.
- Line 5502 fitted two values. Its generated column name reaches the client through `__all_column` (client output 4), but it also passes through the DDL's inner SQL (engine use 1). Engine use 1 and client output 4 now say which wins.
- Two translations were wrong. Line 5202's format has two spaces that the sweep's `format` field drops (finding F8; section 6, item 9 now says to copy the literal from the source). Line 538 prints a memory label that RULEBOOK 2.2 drops (section 6, item 13 now covers it).

No row's value changes under design amendment 1. The totals are 27 `CLIENT_TEXT`, 7 `LOG_TEXT`, 4 `ENGINE_TEXT` and 2 `STREAM_TEXT`: 19 rows `confirmed` and 21 `unknown`. Q1 accounts for 20 of the unknown rows, and section 6, item 13 for the other.

| Line | Site | Value (status) | Why |
|---|---|---|---|
| 109 | src/oblib/common/number/ob_number_v2.cpp:369 | `CLIENT_TEXT` | `from_` parses `full_str` back (ob_number_v2.cpp:430) into the number the string-to-number cast returns (src/sql/engine/expr/ob_datum_cast.cpp:990, :3098, :3100). A parsed value that is itself output counts as client output. `nth += snprintf!(..)` keeps the count (section 6, item 11) |
| 126 | src/oblib/common/number/ob_number_v2.cpp:1051 | `CLIENT_TEXT` | inside `ObNumber::to_string`, which DUMP() returns: `helper.convert(nmb)` (src/sql/engine/expr/ob_expr_func_dump.cpp:228) becomes the result at ob_expr_func_dump.cpp:233. The bit fields `d_.sign_` and the rest are getters, passed `as u32` (section 6, item 1) |
| 352 | src/oblib/common/object/ob_obj_type.cpp:359 | `CLIENT_TEXT` | a use of a row-8 macro. It defines `ob_utinyint_str_without_accuracy`, entry ob_obj_type.cpp:692 of the table at ob_obj_type.cpp:682, which is called at ob_obj_type.cpp:754. The function prints only `STYPE1` (ob_obj_type.cpp:276), "tinyint", as the DATA_TYPE of information_schema.COLUMNS (src/observer/virtual_table/ob_information_columns_table.cpp:613, :623) |
| 526 | src/oblib/lib/allocator/ob_block_alloc_mgr.h:44 | `LOG_TEXT` | as in 14.1. The over-limit branch is the owner's check, not an allocation failure, so it stays (section 7) |
| 538 | src/oblib/lib/allocator/ob_slice_alloc.cpp:74 | `LOG_TEXT` (unknown: section 6, item 13) | `_LIB_LOG_RET` passes its code. `attr_.label_.str_` is a memory label, which RULEBOOK 2.2 drops: it is a `Budget` name only where the slice allocator becomes a `Budget` |
| 1070 | src/oblib/lib/signal/ob_signal_handlers.cpp:194 | `STREAM_TEXT` (unknown: Q1) | section 4's stream example: `crash_info` is copied into `print_buf` (ob_signal_handlers.cpp:198), which `writev` sends to stderr (ob_signal_handlers.cpp:220). `lnprintf` is section 5, row 9 |
| 1235 | src/oblib/lib/utility/ob_print_utils.h:816 | `LOG_TEXT` (unknown: Q1) | the `volatile int32_t` key printer, a generic printer (section 4). Only ObIOResult and ObIORequest (src/share/io/ob_io_define.h:416, :486) and ObEmptyReadCell (src/storage/access/ob_empty_read_bucket.h:94) print such keys, and their text reaches only log keys. `WITH_COMMA` is Q1 (b) |
| 1244 | src/oblib/lib/utility/ob_print_utils.h:861 | `LOG_TEXT` (unknown: Q1) | as line 1235, for `volatile bool`: ObDynamicThreadPool (src/oblib/lib/thread/ob_dynamic_thread_pool.h:70) and ObEmptyReadCell (src/storage/access/ob_empty_read_bucket.h:94) |
| 1307 | src/oblib/lib/utility/utility.cpp:951 | `LOG_TEXT` | `_OB_LOG(WARN, ..)` takes no code, so its first argument is the `ret` in scope. That `ret` is `Ok(())` there (utility.cpp:949) and becomes `OB_ERROR` only after the call (utility.cpp:952) |
| 1572 | src/observer/schema/ob_schema_service_sql_impl.cpp:3545 | `ENGINE_TEXT` (unknown: Q1) | the format is the macro `FETCH_ALL_TABLE_HISTORY_WITH_ROWKEY` (ob_schema_service_sql_impl.cpp:978-992: `%s`, `%s`, `%ld`), and `sql_client_retry_weak.read` runs the text (ob_schema_service_sql_impl.cpp:3555) |
| 1901 | src/observer/virtual_table/ob_table_columns.cpp:562 | `CLIENT_TEXT` | the Extra column of SHOW COLUMNS: `extra_val = ObString(pos, buf)` (ob_table_columns.cpp:583) goes into the cell at ob_table_columns.cpp:616 |
| 1921 | src/observer/virtual_table/ob_virtual_show_trace.cpp:94 | `CLIENT_TEXT` | `close_json_tags` writes its caller's `tags_buf` (ob_virtual_show_trace.cpp:202), which becomes the TAGS cell of line 1918 in 14.1. The count `n` is tested and then added to `pos` (ob_virtual_show_trace.cpp:95, :99) |
| 1951 | src/pl/ob_pl_resolver.cpp:10757 | `CLIENT_TEXT` (unknown: Q1) | `err_msg` is the session's PL error text (ob_pl_resolver.cpp:10752). Its one reader copies it into a PX task's error message (src/sql/engine/px/ob_px_task_process.cpp:493), which the coordinator records with `FORWARD_USER_ERROR` (src/sql/engine/px/ob_px_util.h:920). The error packet only resets it (src/observer/mysql/obmp_packet_sender.cpp:707) |
| 2126 | src/rootserver/ddl_task/ob_ddl_task.cpp:3260 | `ENGINE_TEXT` (unknown: Q1) | `proxy.read` runs the text (ob_ddl_task.cpp:3278). Its other path, the `LOG_INFO` key at ob_ddl_task.cpp:3274, is a log line. The seven `%c` take `spec_charater as u8` (section 6, item 6) |
| 2469 | src/share/io/ob_io_define.cpp:1760 | `LOG_TEXT` | inside `ObIOServiceConfig::to_string`. Its uses are log keys (src/observer/virtual_table/ob_all_virtual_io_status.cpp:349; src/share/io/ob_io_manager.cpp:1260) and a key of ObIOService's `TO_STRING_KV` (src/share/io/ob_io_manager.h:178), which is itself only a log key (ob_io_manager.cpp:540). A search for explicit uses of either prints nothing |
| 3032 | src/sql/engine/cmd/ob_load_data_parser.cpp:195 | `ENGINE_TEXT` | section 4's engine use 4. The options JSON is built only for SELECT ... INTO (src/sql/resolver/dml/ob_select_resolver.cpp:3035) and is parsed back at src/sql/engine/basic/ob_select_into_op.cpp:57. `STR_BOOL` is a ternary of literals (src/oblib/lib/utility/ob_print_utils.h:1080) |
| 3196 | src/sql/engine/expr/ob_expr_regexp_context.cpp:597 | `CLIENT_TEXT` (unknown: Q1) | `LOG_USER_ERROR(OB_ERR_REGEXP_ERROR, errmsg.ptr())` (ob_expr_regexp_context.cpp:603). ICU's headers give the three `?` types: `u_errorName` returns `const char *` (deps/3rd/usr/local/oceanbase/deps/devel/include/icu/common/unicode/utypes.h:728-729), and `UParseError`'s `line` and `offset` are `int32_t` (parseerr.h:67, :76 in the same directory; cite_check.py indexes only src and migration, so it reports both as missing) |
| 3255 | src/sql/hybrid_search/ob_query_translator.cpp:126 | `CLIENT_TEXT` (unknown: Q1) | the translator writes the `buf` of `ObQueryReqFromJson::translate` (src/sql/hybrid_search/ob_query_request.cpp:29). `do_get_sql` returns it (src/sql/hybrid_search/ob_hybrid_search_executor.cpp:163, :167) as the result of DBMS_HYBRID_VECTOR.GET_SQL (src/pl/sys_package/ob_dbms_hybrid_vector_mysql.cpp:82, :90) |
| 3845 | src/sql/optimizer/ob_log_table_scan.cpp:1640 | `CLIENT_TEXT` (unknown: Q1) | `BUF_PRINT_STR` (src/sql/monitor/ob_sql_plan.h:60) points `plan_item.object_type_` at the text, which `__all_virtual_sql_plan` puts into a cell (src/observer/virtual_table/ob_all_virtual_sql_plan.cpp:216) |
| 3854 | src/sql/optimizer/ob_log_table_scan.cpp:1703 | `CLIENT_TEXT` | plan text: `explain_index_selection_info` writes the `PlanText` of `get_plan_item_info` (ob_log_table_scan.cpp:1407). `NEW_LINE` becomes `new_line!()` (section 6, item 9) |
| 3885 | src/sql/optimizer/ob_log_table_scan.cpp:1768 | `CLIENT_TEXT` | as line 3854 |
| 3904 | src/sql/optimizer/ob_log_table_scan.cpp:1901 | `CLIENT_TEXT` | plan text: `get_plan_item_info` calls `print_range_annotation` (ob_log_table_scan.cpp:1474) |
| 4287 | src/sql/parser/parse_node.c:130 | `STREAM_TEXT` | section 7's example. The call follows the null test of `parser_alloc` (parse_node.c:128), so its target_translation is "None: the branch is removed (RULEBOOK 2.2)" |
| 4433 | src/sql/printer/ob_dml_stmt_printer.cpp:518 | `CLIENT_TEXT` (unknown: Q1) | the statement printer's JSON return type (ob_dml_stmt_printer.cpp:418, :916), which goes into a view definition (src/sql/resolver/ddl/ob_create_view_resolver.cpp:606) |
| 4460 | src/sql/printer/ob_dml_stmt_printer.cpp:618 | `CLIENT_TEXT` (unknown: Q1) | as line 4433 |
| 4504 | src/sql/printer/ob_dml_stmt_printer.cpp:772 | `CLIENT_TEXT` (unknown: Q1) | as line 4433 |
| 4514 | src/sql/printer/ob_dml_stmt_printer.cpp:921 | `CLIENT_TEXT` (unknown: Q1) | `print_json_table_nested_column`, which `print_json_table` calls (ob_dml_stmt_printer.cpp:1156); otherwise as line 4433 |
| 4760 | src/sql/printer/ob_raw_expr_printer.cpp:1148 | `CLIENT_TEXT` (unknown: Q1) | the expression printer writes the `buf_` the statement printer passes, as in line 4433. `DATA_PRINTF("")` prints nothing but keeps its row |
| 4844 | src/sql/printer/ob_raw_expr_printer.cpp:1912 | `CLIENT_TEXT` (unknown: Q1) | as line 4760 |
| 4856 | src/sql/printer/ob_raw_expr_printer.cpp:1999 | `CLIENT_TEXT` (unknown: Q1) | as line 4760 |
| 4949 | src/sql/printer/ob_raw_expr_printer.cpp:2672 | `CLIENT_TEXT` (unknown: Q1) | as line 4760 |
| 4963 | src/sql/printer/ob_raw_expr_printer.cpp:2782 | `CLIENT_TEXT` (unknown: Q1) | as line 4760. `LEN_AND_PTR(func_name)` (src/sql/printer/ob_raw_expr_printer.h:34) gives the `%.*s` its length and pointer, which become one slice |
| 5127 | src/sql/printer/ob_raw_expr_printer.cpp:3822 | `CLIENT_TEXT` (unknown: Q1) | as line 4760 |
| 5202 | src/sql/printer/ob_schema_printer.cpp:1088 | `CLIENT_TEXT` | SHOW CREATE TABLE (src/observer/virtual_table/ob_show_create_table.cpp:239). The format is `",\n  CONSTRAINT "`, with two spaces, where the sweep shows one (finding F8) |
| 5237 | src/sql/printer/ob_schema_printer.cpp:1559 | `CLIENT_TEXT` | SHOW CREATE TABLE's table options. `get_dop()` returns `int64_t` (src/share/schema/ob_table_schema.h:1171) |
| 5502 | src/sql/resolver/ddl/ob_fts_index_builder_util.cpp:1425 | `CLIENT_TEXT` | the name of the hidden word-segment column (ob_fts_index_builder_util.cpp:1083, :1163, :1167). The DDL writes it into `__all_column` (src/share/schema/ob_table_sql_service.cpp:3190), and a judged test selects it (tools/deploy/mysql_test/test_suite/fts_index/t/create_table_with_fts_index.test:38-39). The format is the constant `OB_WORD_SEGMENT_COLUMN_NAME_PREFIX` (src/oblib/lib/ob_define.h:654) |
| 5876 | src/sql/resolver/expr/ob_raw_expr.cpp:5841 | `CLIENT_TEXT` | `get_name` calls it (ob_raw_expr.cpp:327) to print the expression's name in EXPLAIN, as for line 5752 in 14.1 |
| 6074 | src/storage/compaction/ob_server_compaction_event_history.cpp:77 | `CLIENT_TEXT` | the overload with the format third (section 5, row 1). The text is the EVENT cell of the compaction event history (src/observer/virtual_table/ob_all_virtual_server_compaction_event_history.cpp:89, :93). `comment_` is a `char` array (src/storage/compaction/ob_server_compaction_event_history.h:92) |
| 6213 | src/storage/multi_data_source/adapter_define/mds_dump_kv_wrapper.cpp:91 | `LOG_TEXT` | inside a `to_string` whose only uses are log keys: src/storage/tablet/ob_tablet_mds_table_mini_merger.cpp:244, :265; src/storage/multi_data_source/ob_mds_minor_compaction_filter.cpp:120; the `PRINT_WRAPPER` of src/storage/multi_data_source/adapter_define/mds_dump_node.cpp:303. A search for explicit uses prints nothing |
| 6369 | src/storage/tablelock/ob_table_lock_live_detector.cpp:783 | `ENGINE_TEXT` (unknown: Q1) | `where_cond` is appended to `sql` (ob_table_lock_live_detector.cpp:790), which `execute_read` runs (ob_table_lock_live_detector.cpp:385). `ObILockMetadataSession` has one override, and it runs inner SQL (src/query/api/query/session/ob_session_inner_sql.h:34) |

## 15. Pre-classification

Before a classifier reads the code, a script may draft some fields. It may use only a row's five sweep columns, the three placement lists, and the source lines of the call statement. The sweep's `text` column holds only the call's first line (finding F8). A classifier then confirms each drafted field as for any other row, section 8's evidence included, and decides everything else by reading the code.

**Values a script can propose**, each by an exact test and in this order, since step 1 comes first:

| Value | Exact test | Rows today | What the classifier still reads |
|---|---|---|---|
| `NOT_TRANSLATED` | step 1. No `inputs` range of manifest.tsv or core-manifest.tsv covers the file and line, and one of these holds: a `files` entry of not-translated.tsv covers them, the construct is tagged `[vendored]`, or the file ends in `.y` or `.l` | 176 | nothing. The evidence is the list line, or s7-islands-unsafe.md 7.1 for finding F1 |
| `UNKNOWN`, placed in no list | step 1 finds no entry at all | 0 | nothing: flag the row |
| `CLIENT_TEXT` | the construct carries `records a user message`, or its first word is `FORWARD_USER_ERROR_MSG` or `_LOG_USER_MSG` | 7 | argument types; whether the call sits in a branch the port removes (section 7) |
| `LOG_TEXT` | the construct's first word, the text before ` call;`, is a macro of section 5, row 7. The sweep's mark `writes the server log` is not the test: it also marks `logdata_printf` and `LOG_DATA_PRINTF`, which write a buffer | 480 | the `ret` in scope for WARN and ERROR; argument types; the branch check |
| `STREAM_TEXT` | the construct carries `writes stdout` or `writes a stream or file` | 216 | where the `FILE *` is opened or passed, for the evidence; the branch check (src/sql/parser/parse_node.c:130 is a removed branch) |

**Values that always need the code**:
- `NOT_PRINTF`, and anything else step 2 decides (3 rows marked `overload not resolved`). The enclosing template's instantiations have to be listed.
- Every value step 4 decides (5,624 rows: 486 inside a `to_string`, 202 `#define` bodies, and the rest). These are `CLIENT_TEXT`, `ENGINE_TEXT`, `STREAM_TEXT` through a buffer, `LOG_TEXT` through a buffer or a `to_string`, and `UNKNOWN`.
- No pattern of the site decides a step-4 value, because the same callee writes client text in one function and a log key in the next. In section 14.2, the `to_string` of line 126 reaches DUMP()'s result, and that of line 2469 reaches only log keys. A buffer that looks like SQL can be a name a client reads (line 5502). What looks like a log line can reach a virtual table (line 6003 in 14.1).
- A script can still group step-4 rows so that one trace decides a group. Group by the buffer the call writes: the first argument of `databuff_printf` or `snprintf`, the receiver of `append_fmt` or `assign_fmt`, or the `buf_` of the SQL printers that `DATA_PRINTF` writes. For example, src/sql/printer/ob_raw_expr_printer.cpp's 473 rows all write the printer's `buf_`. The value is then decided once per group, by reading the code.

**Fields other than the value that a script can draft**, for every row:
- `file`, `symbol` and `source_construct` (section 2), and the first item of `evidence` (`sweep: printf-calls.tsv:<N> (data row <N-1>).`, section 8). These are exact from the row alone.
- The Rust call, from the construct's first word and section 5's table. The `databuff_printf` overload depends on the format's position in the call, read from the source.
- The format, from the source lines of the call, with adjacent literals joined and `PRI*` macros expanded. Never take it from the sweep's `format` field (finding F8).
- The casts of section 6, item 1, from the sweep's pairs. The classifier checks each type against its declaration (finding F3) and resolves every `?`.
- The markers of section 10:
  - Q1 when the callee is in section 5, row 9 or 10, or the format uses `WITH_COMMA`.
  - Q2 when the callee is in section 5, row 11.
  - Q3 when the construct carries `has %p` (148 rows), or an argument casts an address to an integer.
  - A Q4 candidate when the construct carries `the conversions take` (37 rows, but see finding F2), or a pair such as `%ld <- int`.
- Whether any marker stays is decided with the value.

The counts come from one pass over the list:

```
python3 - <<'EOF'
import re, collections
def ranges(name, col):
    rows = open('migration/' + name).read().split('\n'); c = rows[0].split('\t').index(col); d = collections.defaultdict(list)
    for r in filter(None, rows[1:]):
        for item in r.split('\t')[c].split(','):
            m = re.match(r'(.*?)(?::(\d+)-(\d+))?$', item.strip())
            d[m[1]].append((int(m[2] or 0), int(m[3] or 10**9)))
    return d
lists = [ranges('manifest.tsv', 'inputs'), ranges('core-manifest.tsv', 'inputs'), ranges('not-translated.tsv', 'files')]
hit = lambda d, f, n: any(a <= n <= b for a, b in d.get(f, []))
row7 = set('_OB_LOG _OB_LOG_RET HASH_WRITE_LOG HASH_WRITE_LOG_RET _LOG_INFO _LOG_WARN _LOG_ERROR _LOG_DEBUG _COMMON_LOG _COMMON_LOG_RET _LIB_LOG _LIB_LOG_RET _SHARE_LOG _SHARE_SCHEMA_LOG _STORAGE_LOG _TRANS_LOG _SQL_RESV_LOG _BOOTSTRAP_LOG _OB_NUM_LEVEL_LOG'.split())
count = collections.Counter()
for r in filter(None, open('migration/inventory/sweep/printf-calls.tsv').read().split('\n')[1:]):
    f, n, _, c, _ = r.split('\t'); n = int(n); callee = c.split(' call;')[0]
    if not (hit(lists[0], f, n) or hit(lists[1], f, n)):
        v = 'NOT_TRANSLATED' if hit(lists[2], f, n) or '[vendored]' in c or f.endswith(('.y', '.l')) else 'UNKNOWN (placed nowhere)'
    elif 'records a user message' in c or callee in ('FORWARD_USER_ERROR_MSG', '_LOG_USER_MSG'): v = 'CLIENT_TEXT (step 3)'
    elif callee in row7: v = 'LOG_TEXT (step 3)'
    elif 'writes stdout' in c or 'writes a stream or file' in c: v = 'STREAM_TEXT (step 3)'
    elif 'overload not resolved' in c: v = 'step 2: read the code'
    else: v = 'step 4, inside to_string' if 'inside to_string' in c else 'step 4, #define body' if 'inside #define' in c else 'step 4, other'
    count[v] += 1
for v, k in sorted(count.items()): print(k, v)
EOF
```

It prints 7 `CLIENT_TEXT (step 3)`, 480 `LOG_TEXT (step 3)`, 176 `NOT_TRANSLATED`, 216 `STREAM_TEXT (step 3)` and 3 for step 2. For step 4 it prints 202 `#define` bodies, 486 rows inside a `to_string` and 4,936 others.
