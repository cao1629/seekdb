# SQL front end: parser, resolver, raw expressions, rewrite and optimizer

Research report 04 for the Step 1 design document: how the parser, the resolver, the raw expression IR (`ObRawExpr`), the rewrite rules and the optimizer use pointer identity; how expressions are copied and shared; what the Rust mirror of `ParseNode` is and how the C parser stays C behind an ABI; and which IR ids mirror today's pointer identity one for one.

How this was measured. Source 834bbee1e (the worktree's `src/` has no diff against it). Counts come from `git grep`/`git ls-files` over tracked files unless a line says otherwise. The generated parser files are git-ignored (`.gitignore:140`); their md5 sums in `src/sql/parser/` equal those in /Users/colin/seekdb-dev/ref-834bbee1e, so they are what the archived C++ reference was built from. Symbol lists come from `nm` over the reference build's objects. Scratch outputs: migration/design/evidence/. Nothing was built or run.

## 1. What the C++ does today

### 1.1 Sizes

| Part | Size | Command or source |
|---|---|---|
| src/sql/parser (tracked) | 37 files, 31,537 lines; the grammar is sql_parser_mysql_mode.y 18,723 and .l 1,853 | `git ls-files <dir> \| grep -E '\.(h\|hpp\|cpp\|cc\|c\|ipp\|y\|l)$' \| xargs cat \| wc -l` |
| Generated at configure time, not tracked | sql_parser_mysql_mode_tab.c 81,297; _lex.c 75,028; _lex.h 68,929; _tab.h 1,261; ftsparser_tab.c 1,916; ftsblex_lex.c 2,344; type_name.c 2,030 | `wc -l` |
| src/pl/parser | 9 files, 4,642 lines (pl_parser_mysql_mode.y 2,976, .l 698); generated _tab.c 8,409, _lex.c 3,902 | as above |
| Shared parse types | src/query/api/query/parser/parse_node.h 485 lines; ob_item_type.h 2,477 lines with 2,019 `T_*` enumerators, each with an explicit value (ob_item_type.h:27-2279) | `grep -cE '^\s*T_[A-Z0-9_]+\s*='` |
| src/sql/resolver | 151,357 lines: ddl 40,489, dml 46,346, expr 35,318, cmd 10,428, dcl 5,262, tcl 766, prepare 582, top level 12,166 | as above |
| src/sql/rewrite | 95 files, 97,271 lines (3 more than at 076eb309b: ob_transform_const_propagate.cpp) | as above |
| src/sql/optimizer | 205 files, 155,780 lines | as above |
| src/sql/code_generator | 15 files, 20,519 lines | as above |

### 1.2 The grammars, the ParseNode struct and what the C core needs from outside

- gen_parser.sh requires bison 2.4.1 exactly (gen_parser.sh:30-32), aborts on any conflict (:62-74), runs `flex -Cfa -B -8` (:82, :91), then edits the lexer with sed (:84-96). On this Mac the sed edits fail, so the reference runs an unedited lexer (no `memcpy(buf, yybytes`; `return 1` still at sql_parser_mysql_mode_lex.c:73578, :73585, :73611). The edits only change speed (memcpy) and the code a failed `yylex_init` returns.
- SQL grammar: 1,186 tokens, 756 nonterminals, 3,605 rules, 6,550 states (sql_parser_mysql_mode_tab.c:1570-1579). The reduce switch has 2,675 case labels, the highest 2691 (tab.c:52950-80790), so 2,675 rules carry C actions and 930 carry none (assumption: the action-free ones are the keyword alternatives at the end of the grammar). The .y has 1,383 `malloc_non_terminal_node`, 656 `malloc_terminal_node`, 281 `merge_nodes` and 115 `make_name_node` calls (`grep -oE ... | sort | uniq -c`).
- PL grammar: 214 tokens, 156 nonterminals, 510 rules, 784 states (pl_parser_mysql_mode_tab.c:750-756), 211 node-building calls. Its actions call the SQL parser for embedded statements (`parse_sql_stmt` at pl_parser_mysql_mode.y:136 and :2936; `do_parse_sql_stmt` at :162), so a PL tree holds SQL subtrees built by the SQL core.
- A third grammar, the fulltext boolean-mode parser (ftsparser.y 250 lines, ftsblex.l 85), runs through `fts_parse_docment` at plan time and at run time (ob_join_order.cpp:16641; ob_das_text_retrieval_merge_iter.cpp:207; ob_das_legacy_tr_merge_iter.cpp:1081).

`ParseNode` (parse_node.h:129-195) is an `extern "C"` struct: `type_` (`ObItemType`), `num_child_`, `param_num_`, a `flag_` union over 18 bit fields and `reserved_` (:134-157), a `value_` union with `int32_values_[2]` and `int16_values_[4]` (:164-168), `str_value_` and `str_len_` (not NUL-terminated, :159-163), a `pl_str_off_`/`sql_str_off_` union, `raw_text_`, `text_len_`, `pos_`, `children_` (child pointers, NULL allowed), `stmt_loc_`, and a `raw_param_idx_`/`raw_sql_offset_` union. Two more fields exist only under `SQL_PARSER_COMPILATION` (:191-194), which no seekdb build defines (0 of the 364 entries in the reference compile_commands.json; src/query/BUILD.bazel:329-331), so there is one layout. `ParseResult` (:293-367) carries the input, `malloc_pool_` (an `ObIAllocator*` passed as `void*`), the charset pointers, the parameter list, `jmp_buf_`, the error position and the PL state.

Memory. Nodes and strings come from `parse_malloc`, which takes memory from the allocator behind `malloc_pool_`, zeroes it and keeps the size in an 8-byte header (parse_malloc.cpp:90-105). `new_node` calls `try_check_mem_status` every 1,024 nodes (parse_node.c:177-196). parse_node.c defines that function weak; the reference binary holds the strong one (`nm -gU` prints `T _try_check_mem_status`), which forwards to `ObMemTrackerGuard`, the query memory tracker (src/sql/parser/ob_memory_tracker_wrapper.cpp:20-28). Two allocations bypass `parse_malloc`: bison's `malloc`/`free` in the _tab.c object, and the keyword trie's `calloc` (ob_non_reserved_keywords.c:58, :218), built once by a C constructor (non_reserved_keywords_mysql_mode.c:1090).

What the C core calls outside itself. Undefined symbols of the seven C core objects (bison and flex output, sql_parser_base.c, parse_node.c, the two keyword files, type_name.c), minus libc:

| Symbols | Defined today in | Used for |
|---|---|---|
| `parse_malloc`, `parse_realloc`, `parse_free`, `parse_strndup`, `parse_strdup`, `parser_alloc`, `malloc_parentheses_info` | parse_malloc.cpp | memory from the statement allocator |
| `parse_str_convert_utf8`, `replace_invalid_character`, `check_real_escape` | parse_malloc.cpp (C++ over `ObCharset`) | charset work on literals and names |
| `ob_strntoll`, `ob_strntoull` | oblib ob_ctype_simple.cc:754 | integer literals (sql_parser_mysql_mode.l:202; sql_parser_base.h:849-856) |
| `murmurhash` | parse_node_hash.cpp | `parsenode_hash` |
| `lookup_pl_symbol` | src/pl/ob_pl_stmt.cpp:3376 | PL variable lookup in the middle of a SQL parse (sql_parser_base.h:418-441) |
| `check_stack_overflow_c` | src/sql/ob_sql_utils.cpp:1964 | recursion guard (parse_node.c:657, :696) |
| `try_check_mem_status` | ob_memory_tracker_wrapper.cpp, over the weak one | query memory tracker |
| `ob_backtrace_c`, `parray_c` | oblib ob_backtrace.cpp:192, :196 | debug text only (parse_node.c:1017-1023) |

The lexer also calls three functions through the charset table the caller puts in `ParseResult::charset_info_` (`cset->well_formed_len`, `cset->ismbchar`, `cset->numchars`) and reads `escape_with_backslash_is_dangerous` and `csname` (sql_parser_base.h:776-806). `ObCharsetInfo` (ob_ctype.h:360-397) points at `ObCharsetHandler`, a table of 25 function pointers (ob_ctype.h:220-287; `cset` at :394). The PL core needs the SQL core plus `obpl_parser_check_stack_overflow`, `strndup_with_prefix` and `mysql_sql_reserved_keyword_lookup` (nm over its four objects).

Error exit. `parse_sql` calls `setjmp` on a `static __thread jmp_buf` (sql_parser_base.c:104-106) and error text goes to a `static __thread` buffer (:36). The only `longjmp` is in `obsql_mysql_parser_fatal_error` (sql_parser_mysql_mode.l:1840-1853), reached through `YY_FATAL_ERROR` (.l:31) after an allocation returned NULL (`check_malloc`, sql_parser_base.h:109-117; also :356, :506, :844). The PL core has its own pair (pl_parser_base.c:48; pl_parser_mysql_mode.l:672). Every callback in the table returns normally; none re-enters the parser.

The C++ layer over the grammar is ObParser (ob_parser.cpp 1,275 + .h 224), ObFastParser (2,527 + 671), ob_parse_simd.cpp (218) and ObPLParser (src/pl/parser/ob_pl_parser.cpp, 297).

### 1.3 Who reads and writes parse trees after parsing

- The word `ParseNode` appears on 3,410 lines in 219 files of src/; outside the three parser directories, on 2,515 lines in 205 files (`git grep -cw`), 2,294 of them in 166 resolver files.
- Children are read by position: 2,672 `children_[` lines in 77 resolver files, 269 in src/pl/ob_pl_resolver.cpp, 72 in ob_sql_parameterization.cpp, 23 in ob_spi.cpp, 20 in ob_sql.cpp, 19 in ob_sql_utils.cpp (DAS files use `children_[` for their own trees). `ObResolver::resolve` has 142 `case T_` labels and 93 `REGISTER_STMT_RESOLVER` entries (ob_resolver.cpp); src/sql/resolver defines 95 `Ob*Resolver` and 90 `Ob*Stmt` classes (regex over class heads).
- The tree is edited in place. Parameterization sets `type_ = T_QUESTIONMARK` and `value_` (ob_sql_parameterization.cpp:583-585), lifts a grandchild into its parent's slot (:746), negates values (:1162, :1766, :2373, :2436), replaces strings (:1781, :2390, :2453) and turns a minus into an add (:2370, :2433). When it fails, ob_sql.cpp:3327-3356 notes that the tree "may have been partially parameterized", parses again and keeps the statement out of the plan cache. In files that name `ParseNode`, a regex finds 329 assignment lines to node fields (136 in ob_raw_expr_resolver_impl.cpp), many on nodes the resolver builds on its own stack: there are 35 local `ParseNode` values (PCRE `\bParseNode\s+[a-z_][a-z0-9_]*\s*(;|=|\[|\()`).
- C++ outside the parser calls the parse_node.c API about 30 times: node constructors (in ob_resolver_utils.cpp, ob_raw_expr_util.cpp, ob_inlist_resolver.cpp, ob_del_upd_resolver.cpp, ob_dml_resolver.cpp), deep copies (ob_prepare_stmt_struct.cpp:359; ob_inlist_resolver.cpp:554, :558, :647), `push_back_child`/`append_child` (ob_inlist_resolver.cpp:541-732) and `parsenode_equal` (:670). oblib's hash traits use `parsenode_hash`/`parsenode_equal` for `ParseNode*` keys (ob_hashutils.h:729-745, :819-834).
- Nodes outlive the parse. The fast parser builds its own `ParseNode`s for parameters (ob_fast_parser.h:243, :489), held in `ObPCParam::node_` (ob_plan_cache_util.h:250); the PS cache deep-copies them (ob_prepare_stmt_struct.cpp:359); 12 member fields keep node pointers, for example `ObAnonymousBlockStmt::body_` (ob_anonymous_block_stmt.h:83) and `node_` at ob_dml_stmt.h:290.
- Parse-node identity is used once: ob_select_resolver.cpp:4498 stores child addresses as `int64_t`; no pointer comparison between parse nodes turned up.
- Engine code declares local `ParseNode`s in 10 places (for example ob_expr_json_func_helper.cpp:1395, ob_aggregate_processor.cpp:6623); the two read for this report (ob_expr_cast.cpp:540-548, ob_datum_cast.cpp:8897-8905) use only the `value_` union, to unpack a cast target type packed into one integer.
- If the fast parser counts constants differently from the grammar, `parameterize_syntax_tree` returns OB_NOT_SUPPORTED (ob_sql_parameterization.cpp:189-197) and the statement silently skips the plan cache.

### 1.4 The raw expression IR and the statements it hangs off

- ob_raw_expr.h (5,278 lines) declares 29 subclasses of `ObRawExpr` (`grep -cE '^class Ob[A-Za-z]+RawExpr[[:space:]]*:'`), two of them intermediate bases (`ObTerminalRawExpr`, `ObNonTerminalRawExpr`) and one with two bases (`ObWinFunRawExpr : public ObRawExpr, public ObWindow`, :4675). 1,185 files in src/ name `ObRawExpr` (engine 816, resolver 112, optimizer 108, rewrite 86, code_generator 14); 791 engine files contain `cg_expr`. Optimizer and rewrite hold 492 `static_cast` lines to `Ob*RawExpr*`.
- Each expression carries `expr_factory_` (:2226) and `rt_expr_`, the engine `ObExpr` the code generator fills in (:2234), plus `magic_num_` for double-free detection (:2217), flags in `info_` (:2223), `is_shared_reference_` (:2237) and a structural `expr_hash_` (:2248). Cycles run through `ObExecParamRawExpr::outer_expr_` (:2786), `ObQueryRefRawExpr::ref_stmt_` (:2892), `ObColumnRefRawExpr::dependant_expr_` (:3128) and `TableItem::ref_query_` (ob_dml_stmt.h:278). An out-of-range `get_param_expr` returns a reference to the global `USELESS_POINTER` (ob_raw_expr.h:71, :3397, :3573), and ob_raw_expr.cpp:3157 compares addresses with it.
- `ObRawExprFactory` places each expression in its allocator and records it in `expr_store_` (ob_raw_expr.h:4996-5070). A factory built from another factory is a proxy: it creates in the other factory's store (:5010-5016, :5040-5048). Local factories are made on 41 lines in 23 files (`git grep -cP 'ObRawExprFactory\s+\w+\s*\('`); PL keeps long-lived ones (ob_pl_cache_object.h:243; ob_pl_stmt.h:1459).
- Slots are edited in place: `ObRawExpr*&` appears on 691 resolver, 357 rewrite, 131 optimizer and 32 PL lines; `get_param_expr(...) =` on 32 lines and `replace_param_expr(` on 25 in src/sql.
- `ObDMLStmt`/`ObSelectStmt` are named in 79 rewrite and 56 optimizer files. `Ob*Stmt*&` parameters: rewrite 317, resolver 18; `TableItem*&`/`JoinedTable*&`: resolver 93, rewrite 64, optimizer 8. `stmt_id_` is a counter from `query_ctx_->get_new_stmt_id()` (ob_stmt.cpp:76-86), copied with the statement (ob_stmt.cpp:37), and rewrite sorts statements by it (ob_transform_predicate_move_around.cpp:127; ob_transform_subquery_coalesce.cpp:2198; ob_transform_temp_table.cpp:2048).
- SMART_CALL appears on 303 resolver, 388 rewrite and 229 optimizer lines; `ObResolverParams` holds 20 raw pointers (ob_resolver_define.h:318-458).

### 1.5 How expressions and statements are copied

- `ObRawExprCopier` keeps `copied_exprs_` (old address to new address) and `new_exprs_` (addresses it created), both keyed by address (ob_raw_expr_copier.h:176-177), and a list of nodes not to copy (:182). `copy` hands back the earlier copy of an expression it has already seen, so a subexpression shared by several parents is copied once and stays shared in the copy (`find_in_copy_context`, ob_raw_expr_copier.cpp:108-128). A new expression is made only in `copy_expr_node` (:137-155), one per distinct old expression. Non-onetime `ObExecParamRawExpr`s are not copied (:91-106); `add_skipped_expr` maps a tree to itself (:310-319). `copy_on_replace` (:267-308) copies a node only when a child changed, so unchanged subtrees stay shared between the old and the new tree.
- `ObPLExprCopier` keeps no map (:56-70): every visit makes a new expression, so it does not keep sharing.
- `ObDMLStmt::assign` copies the pointer arrays, so both statements share table items and expressions (ob_dml_stmt.cpp:375-409). `ObDMLStmt::deep_copy` runs one copier over the statement and its child statements (:411-460) and makes new table items (:462-545), so sharing across a statement tree survives the copy; `stmt_id_` is copied, so the copy has the same `stmt_id_` at a different address.
- `ObRawExprReplacer` holds an address map from old to new and an address set of new expressions (ob_raw_expr_replacer.h:91-92) and does not descend into what it replaced (comment at :26-35). `ObTransformUtils::replace_expr` rewrites every slot that holds one of the given addresses; an equal expression at another address stays.
- Cost-based rewrite: `evaluate_cost` deep-copies the statements into the main factories, runs the heuristic rules on the copy, runs a whole `ObOptimizer` with a second `ObRawExprFactory` on a temporary memory context, then restores query-context counters by hand and frees the copied statements (ob_transform_rule.cpp:346-418, temporary context :379-398, `recover_context` :899-924). Late materialization and temp table build their own `ObOptimizer` (ob_transform_late_materialization.cpp:460; ob_transform_temp_table.cpp:2353, :2539, :2613). The transformer repeats its 33 rules (ob_transformer_impl.cpp:455-487) up to `max_iteration_count_` times (:213, :328-362).

### 1.6 Where pointer identity decides behavior

| Use | Where | Count (command) |
|---|---|---|
| `find_item`, documented as "Based on pointer comparison to determine if two expressions are the same" | ob_optimizer_util.h:264-286 | call lines: optimizer 109, rewrite 134, resolver 11 (`git grep -cP 'find_item\('`) |
| set helpers that compare with `==` | `append_array_no_dup`, `remove_item`, `intersect`, `is_subset`, `overlap` | optimizer plus rewrite: 132, 78, 35, 96, 85 |
| pointer first, then structure, then equal sets | `find_equal_expr` (doc at ob_optimizer_util.h:306) | 73 in optimizer plus rewrite |
| direct `==` between expression or statement pointers | e.g. ob_predicate_deduce.cpp:155; ob_transform_const_propagate.cpp:1425; ob_transform_utils.cpp:9016, :12818 | about 74 lines (rough regex over the four directories) |
| identity inside structural equality | `same_as` returns true when `this == &expr` (ob_raw_expr.cpp:727); query refs are equal only with the same `ref_stmt_` (:1659, :1678); set-op expressions only when they are the same object (:4933); alias refs compare the referenced pointer (:2144); column compare starts with `&left == &right` (:5689) | — |
| address as hash key | 48 lines on expressions, 10 on statements, 4 on query-range nodes, 1 on parse nodes (table below) | `git grep -nP 'reinterpret_cast<\s*(u)?int64_t\s*>'` |
| a mark bit on the object | `ObRawExprUniqueSet` de-duplicates with the `IS_MARKED` flag (ob_raw_expr_util.h:112, flag at :171-175) | — |
| groups of slots holding one address | `RelExprPointerChecker` builds one `ObRawExprPointer` (a list of `ObRawExpr**`) per distinct expression (ob_sql_utils.cpp:1885-1905; ob_raw_expr.h:5137-5150); `ObSelectStmtPointer` does the same for statement slots (ob_transform_utils.h:130; ob_transform_temp_table.cpp:2142-2177, :2900) | — |
| PL packs addresses into integers | expression addresses into int64 arrays and access indexes (ob_pl_build.cpp:1062; ob_pl_resolver.cpp:8515, :8597, :8806); a label namespace's own address as `var_idx` (ob_pl_stmt.cpp:1740, :1956) | — |

A rough split of the 243 `find_item` lines by argument name (python over `git grep -hP 'find_item\('`): about 139 expressions, 13 statements or query refs, 7 table items, 19 other objects (conflict detectors, paths), 23 integer ids, 42 not parsed (calls over several lines).

| Address-keyed objects | Files (lines) |
|---|---|
| expressions, 48 lines | ob_logical_operator.cpp (6); ob_column_index_provider.cpp (3); ob_raw_expr_copier.cpp (7); ob_raw_expr_replacer.cpp (7); ob_shared_expr_resolver.cpp (2); ob_aggr_expr_push_up_analyzer.cpp (4); ob_stmt_expr_visitor.cpp (2); ob_stmt.h:60-66 (1, a hash function that returns the address); ob_sql_utils.cpp:1839-1893, :3442, :3468 (6); ob_transform_pre_process.cpp (3); ob_transform_temp_table.cpp:915, :935, :972 (3); ob_transform_utils.cpp:6225, :6251, :7883, :9997 (4) |
| statements, 10 lines | ob_transform_temp_table.cpp:559, :2130, :3060, :3070, :3138, :3148; ob_transform_utils.cpp:8615, :11319, :11335, :14022 |
| query-range nodes, 4 lines | ob_range_graph_generator.cpp:1279, :1307, :1353, :1371 |
| parse nodes, 1 line | ob_select_resolver.cpp:4498 |

Other integer casts there carry a pointer as a value (ob_topk_hist_estimator.cpp:332-539; ob_raw_expr_util.cpp:6496; ob_spi.cpp).

Sharing is made on purpose, in three places. One `ObColumnRefRawExpr` per column item serves every reference to that column (ob_dml_resolver.cpp:1821, :3055, :4563, :4611, :6199; ob_select_resolver.cpp:3316, :3330; ob_insert_resolver.cpp:1007). Aggregates live in `agg_items_`, and the select and having expressions point at the same objects (ob_select_stmt.h:435-439). `ObSharedExprResolver` turns structurally equal expressions of one scope into one object (called at ob_dml_resolver.cpp:1110-1117; ob_shared_expr_resolver.cpp:133-200): it finds candidates by a hash that mixes in the children's addresses (:79, :87), confirms with `same_as`, rewrites child slots in place (:173), and adds the parameter-equality pairs it relied on to `all_equal_param_constraints_` (:233), which the plan cache checks before reusing a plan.

Identity reaches compared output. The output-allocation pass keeps expression producers in a map keyed by address (`ObAllocExprContext`, ob_logical_operator.cpp:246-322) and counts, per address, how many places reference each subexpression (`add_flattern_expr`; `get_expr_ref_cnt` at :309). `extract_shared_exprs` (:1836-1866) gives each subexpression that is referenced more often than its parent its own producer (`add_expr_to_ctx`, :1721-1795). That decides the `output(...)` lists behind 4,005 pinned lines (PLAN.md §3). The code generator makes one `ObExpr` per distinct raw expression (`get_rt_expr`, ob_static_engine_expr_cg.cpp:190), so a shared subexpression is also evaluated once per row: identity is run-time behavior too. The batch size depends on the number of distinct expressions only when `_rowsets_max_rows` is 0 (ob_static_engine_expr_cg.cpp:95-175); the default is 256 (ob_parameter_seed.ipp:293) and no configured test changes it (`git grep -i rowsets_max_rows -- tools/deploy` finds only the parameter listing), so `rowset=` does not depend on identity today.

Iterating an address-keyed map never reaches output, by reading. `ObHashMap` hashes an integer key to itself (ob_hashutils.h:837-852) and picks the bucket as hash modulo a prime (ob_hashtable.h:1139; `cal_next_prime` at ob_hashutils.h:677-687), so iterating an address-keyed map gives an order that changes with heap addresses from run to run. The four iteration sites fill other maps or free memory: `get_copied_exprs` (ob_raw_expr_copier.cpp:333-343; its one caller, ob_log_plan.cpp:11156, loads a replacer), `append_replace_exprs` (ob_raw_expr_replacer.cpp:372-383; callers ob_log_plan.cpp:9438, :9440), the `ObSharedExprResolver` destructor, and `extract_shared_expr` (ob_transform_utils.cpp:7926-7968), which has no callers. The sort comparators read for this report order statements by `stmt_id_`, tables by `table_id_` (ob_transform_utils.cpp:10200) or by name (ob_log_join.cpp:789); none orders by address, and these directories have no `std::map` or `std::set`.

Addresses are reused on purpose. A dominated `JoinPath` goes on a recycle list (ob_join_order.cpp:5424, :5435-5448), and `alloc_join_path` pops the last one and builds a new path in the same memory (:11037-11051). Operators hold `child_`, `my_plan_`, `parent_` and a `void *traverse_ctx_` (ob_logical_operator.h:1674, :1742, :1863, :1871); candidate plans share child subtrees (ob_select_log_plan.cpp:829-855), and `set_child` overwrites the child's `parent_` (ob_logical_operator.cpp:394-408); 195 optimizer lines take `ObLogicalOperator*&`.

## 2. Constraints from the decisions and the plan

| Source | What it fixes here |
|---|---|
| Decision 13 (a) | The parser's C core (bison/flex output, sql_parser_base.c, parse_node.c) stays C behind a C ABI for the first parity gate; `ObParser`, `ObPLParser`, `ObFastParser` are ported; revisit after parity. |
| Decision 10 (a); PLAN.md §3 | Resolvers, rewrite and optimizer keep algorithms, pass order and function boundaries. IR ids: "One id per allocation; an in-place edit keeps the id; new ids appear only where `ObRawExprCopier` makes a copy today." A Rust mirror of `ParseNode` "so the 89 resolvers keep their shape". |
| Decision 6 (b) | Only EST numbers and about 300 hash-order SELECTs are masked. `output(...)` lists, filter order and plan shape compare exactly, so which nodes are the same object must not change. |
| Decision 14 (b) | `unsafe` only in named crates: the parser island's shim can be one; resolver, IR, rewrite and optimizer crates cannot. |
| Decision 12 (b) | General out-of-memory aborts; the query memory tracker keeps its typed error, so `try_check_mem_status` and the parser's NO_MEMORY exit stay. |
| Decision 16 (a) | Stable toolchain; allocator-api2, not the nightly `allocator_api`, for arena-backed collections. |
| Decision 3 (a) | The grammar is frozen with the base, so generated C can be produced once and kept. |
| Decision 7 | The C core must stay buildable for the later targets; all are little-endian (assumption), which the `value_` packing relies on; wasm needs `setjmp`/`longjmp` in its C toolchain (assumption: Emscripten). |
| Decision 1 (b) | Memory use is not a criterion, so arenas that free only as a whole are acceptable. |
| PLAN.md §3, silent changes 2, 6, 7, 8 | No per-process hash seeds; SMART_CALL becomes stack growth; no Rust frame between the parse entry and a callback; fast-parser constant counts (00b's plan-cache counter, family 7). |
| PLAN.md §6, Step 2a | The narrow run uses "the C parser core over FFI with its tree converted to the owned Rust AST". |
| PLAN.md §8, items 19, 20, 22, 23 | Island memory charging; stack growth on macOS; coverage depth (the Statement IR group ran 72.7% of its functions and the parser was not measured, coverage README); false failures from exact comparison. |

Three plan figures do not reproduce and should be restated in the design document: "758 rules with C actions" (3,605 rules, 2,675 with actions, 756 nonterminals); "206 files, 2,610 references" (205 files, 2,515 lines outside the parser directories); "89 resolvers" (93 registered statement resolvers).

## 3. Options

### 3.1 How Rust code sees the parse tree

| Option | What it is | Cost | Risk |
|---|---|---|---|
| (a) Handles over the C tree | a `#[repr(C)]` `ParseNode` from the header; a copyable handle whose get and set methods go through raw pointers; the tree stays where C wrote it | least glue; no work per parse | every access in 2,500+ lines goes through `unsafe` in the island; sound only while one thread uses the tree and no Rust reference points into node memory; a later Rust parser must reproduce the C layout |
| (b) Convert once into an owned Rust tree | after `parse_sql` returns, one walk copies each node into a Rust arena; everything else sees only the Rust type | one pass per full parse; a converter of a few hundred lines | time per full parse (plan-cache hits use only the fast parser, so point selects do not pay it); `ParseResult`'s outputs are converted too |
| (c) Typed syntax tree | one Rust type per construct | rewrite of 2,672+ positional reads and every `case T_` | the resolvers lose their shape (Decision 10) |
| (d) A Rust parser now | a Rust LR generator; 2,675 actions and the lexer rewritten | largest | excluded by Decision 13 for the first gate |

### 3.2 Which charset code the C lexer calls

(a) The C++ oblib charset subset that PLAN.md §3 already keeps under share/geo and S2 supplies the `ObCharsetInfo` tables and `ob_strntoll`/`ob_strntoull`, and parse_malloc.cpp's three charset helpers stay with it. Identical to the reference by construction, and available at Step 2a, before any Rust charset code exists (value libraries fan out only after the foundation API freezes, PLAN.md §6). (b) Rust fills a C-layout `ObCharsetInfo` per connection collation with only the fields the lexer reads and the three handler slots, and exports the helpers. One charset implementation, but only after the Rust charset crate matches bit for bit; a slot left NULL crashes if an unlisted path uses it.

### 3.3 How the IR represents identity

| Option | Shape | For | Against |
|---|---|---|---|
| (a) Typed ids into arenas (the plan of record) | `ExprId`, `StmtId` and so on: copyable integers; objects reached as `exprs[e]`; maps keyed by ids | no lifetimes in IR types; exclusive writes checked by the compiler; ids fit in an `i64` as PL's packed addresses do; deterministic hashing | every access names its arena; a slot inside the arena is edited by read, compute, write back; a stale id is a logic error the compiler cannot see |
| (b) Arena references with interior mutability | `&'q RawExpr`, fields in `Cell`/`RefCell`, `ptr::eq` | reads like the C++ | a `'q` lifetime in every signature; a failed `RefCell` borrow aborts at run time; address hashing stays random per run |
| (c) `Rc<RefCell<_>>` graph | reference counting | — | cycles leak; run-time borrow failures; keeps the shared-subtree bug class (evidence-full.md: 71e2b595b) |
| (d) Raw pointers | a literal port | — | not allowed outside named crates (Decision 14) |
| (e) Immutable or copy-on-write expressions | copy instead of edit | removes aliasing bugs | changes which nodes are the same object, so `output(...)` lists and plan-cache constraints change (Decision 6) |

### 3.4 How wide an IR id is

(a) A `u32` index, valid only with the arena it came from: compact, but ids from two arenas can be equal by accident, and C++ does mix factories: `evaluate_cost`'s second factory feeds the same optimizer structures as the main one (ob_transform_rule.cpp:379-398), and PL copies between factories and packs the results into integers (ob_pl_build.cpp:1057-1062). (b) A `u64`: an arena number in the high 32 bits and the index in the low 32 bits, with arena numbers unique among live arenas. Equality and hashing stay exact across arenas, as addresses are; the cost is 4 more bytes per id, which Decision 1 allows.

## 4. Recommendation

### 4.1 The parser island and its ABI

1. One island crate, a named `unsafe` crate under Decision 14, holds the three grammars' generated C (SQL, PL, fulltext), sql_parser_base.c, parse_node.c, pl_parser_base.c, the keyword files and type_name.c. It checks in the exact generated files the reference was built from, with their md5 sums; the grammar is frozen (Decision 3), so the Rust build needs no bison or flex, and the sed edits that fail on macOS today stay out, as in the reference. build.rs compiles them with the reference flags (`-O2 -fno-strict-aliasing -fmax-type-align=8 -ffp-contract=off -march=armv8-a+crc+lse`, from the reference compile_commands.json).
2. Rust bindings for `ParseNode`, `ParseResult`, the `ObItemType` values and the entry points (`parse_init`, `parse_sql`, `parse_terminate`, the PL entry, `mysql_sql_reserved_keyword_lookup`, which ob_sql_utils.cpp:3703 also calls) are generated once from the existing headers and checked in; a gate regenerates and diffs them.
3. Rust implements the callbacks under their C names. Memory callbacks take memory from the statement arena and keep the 8-byte size header `parse_realloc` needs. `try_check_mem_status` forwards to the Rust query memory tracker; the two stack checks report from the Rust stack-growth scheme; `lookup_pl_symbol` asks the Rust PL namespace. Each callback returns, never panics (the binary is `panic=abort` anyway) and never calls back into the parser. Then Rust frames sit only below `parse_sql`, never between its `setjmp` and the `longjmp` (PLAN.md §3, item 7).
4. After linking, check with `nm` that the Rust `try_check_mem_status` replaced parse_node.c's weak one, as it does in the reference (assumption: a static archive member is not pulled into a Mach-O link just to replace a weak definition, so this can silently go wrong).
5. Charset: option (a) of 3.2 for Step 2a and the first parity gate; option (b) is revisited with the other islands after parity.
6. The caller copies the thread-local error text and the error position out of `ParseResult` before the thread parses again (sql_parser_base.c:36).
7. Island memory (PLAN.md §8 item 19): parse allocations go through the Rust arena and are charged with it; only bison's stack and the keyword trie use libc `malloc`.

### 4.2 The Rust mirror of ParseNode

Option (b) of 3.1: convert once, into a Rust tree the rest of the code owns.

- A safe crate owns `ParseTree`: nodes in an arena, `NodeId` handles (`u32`), one Rust node per C node. The walk keeps a map from C address to `NodeId`, so a node with two parents stays one node, and it uses an explicit stack, not recursion.
- Field names stay the C names (`type_`, `num_child_`, `param_num_`, `flag_`, `value_`, `str_value_`, `str_len_`, `raw_text_`, `text_len_`, `pos_`, `children_`, `stmt_loc_`, `raw_param_idx_`), so one grep finds both sides. `flag_` stays a `u32` with one accessor per bit field, bit positions checked against the C layout by a test in the island. `value_` stays an `i64` with helpers for the `int32_values_`/`int16_values_` views (little-endian lane order); the engine's cast decoding calls the helpers on the plain integer and needs no tree.
- Strings are not copied: `parse_malloc` already put them in the statement arena, so a Rust node borrows them as `&'q [u8]`. `str_len_` stays writable on its own, because C++ writes value and length separately (for example ob_sql_parameterization.cpp:1781, :2302).
- `children_` keeps NULL slots as `None`. Reading an index at or past `num_child_` aborts; C++ reads unrelated memory there, so this is a BUG(port) class for the rulebook.
- After the parse only the Rust tree is used. Parameterization and the resolvers edit it through `&mut ParseTree`; handles hold no borrows, so edits in the middle of resolution work as in C++. The Rust fast parser builds its parameter nodes in the same type. The 35 local nodes become arena nodes. The PS cache copies between trees. `parsenode_hash` and `parsenode_equal` get Rust versions that visit fields in the same order (parse_node.c:650-720). ob_select_resolver.cpp:4498 stores `NodeId`s.
- If Step 2a shows the conversion breaks the 1.2x speed gate, the same API can sit on option (a) handles; resolver code, which sees only `NodeId`s and tree methods, does not change.

Why: the plan's Step 2a already assumes conversion; `unsafe` stays in one converter instead of in every field access; and a Rust parser, when Decision 13 is revisited, fills the same tree.

### 4.3 IR ids that mirror pointer identity

Option (a) of 3.3 with option (b) of 3.4, for every type whose objects are compared or keyed by address. The rules:

1. One id per C++ allocation. An id is made exactly where the C++ makes an object: `ObRawExprFactory::create_raw_expr` (including the copies `copy_expr_node` and `ObPLExprCopier` make), the statement factory, `new TableItem`, the operator and plan factories. A proxy factory makes ids in the arena of the factory it points to.
2. An in-place edit keeps the id: slot writes, `assign`, `formalize`, type deduction and flag changes all write the object behind the id.
3. Equality is id equality. `find_item` and the set helpers keep their bodies over `T: PartialEq`, so they compare ids where C++ compares pointers and values where it compares values.
4. Address-keyed maps become id-keyed maps with a fixed hasher (PLAN.md §3 item 2). Where C++ iterates one, its order is random per run, so any order is faithful; use insertion order, and give debug builds a switch that iterates in reverse so a hidden order dependence shows up as a judge difference.
5. Slots. An `ObRawExpr*&` that names a local or a statement field becomes `&mut ExprId`, taken from the statement arena while the expression arena is borrowed separately (the two are separate fields of the statement context). A slot inside another expression (`get_param_expr(i)`) is read, computed on, and written back through a setter. `ObRawExprPointer` and `ObSelectStmtPointer` become lists of slot paths (statement id, field, index) written through the arena. `USELESS_POINTER` becomes `Option`.
6. Where C++ reuses memory, reuse the id in the same order: the `JoinPath` recycle list becomes a stack of `PathId`s popped from the back (ob_join_order.cpp:5435-5448, :11037-11051).
7. Where C++ frees, drop the arena: `evaluate_cost`'s second factory is its own arena, dropped at the end of the temporary context. A later arena may get the same number, as a later object may get the same address in C++; debug builds keep a generation per arena so a stale id aborts instead of reading a new object.
8. The ids the C++ already has stay separate fields with their own counters and never come from arena ids: `stmt_id_`, `table_id_`, column ids, `ref_id_`, exec-param indexes, operator numbering, producer ids. They are printed or sorted on; arena ids are neither.
9. Back-pointers become context: `expr_factory_` and `inner_alloc_` go (the arena is a parameter); `rt_expr_` becomes a code-generator table indexed by `ExprId`; `my_plan_` and `parent_` become id fields.
10. Cycles are plain id fields: `ref_stmt_`, `outer_expr_`, `dependant_expr_`, `ref_query_`.
11. Marks stay on the object: `IS_MARKED`, `is_shared_reference_`, `reference_type_`.
12. `ObSharedExprResolver`'s hash mixes in child ids instead of child addresses; the result is the same because the hash only finds candidates and `same_as` decides.
13. Recursion keeps SMART_CALL's call sites and wraps them in stack growth (PLAN.md §3 item 6; §8 item 20).

| C++ objects | Rust id | Arena | Notes |
|---|---|---|---|
| `ObRawExpr` and its 29 subclasses | `ExprId`, plus typed ids such as `ColumnRefId`, `AggFunId`, `WinFunId`, `QueryRefId`, `ExecParamId` for the element types of typed arrays | one per non-proxy `ObRawExprFactory` (41 construction lines, 23 files) | one `RawExpr` holds the common fields and an enum over the concrete classes; a checked accessor replaces `static_cast` (a wrong cast aborts); `ObWindow` becomes a field of the window-function variant |
| `ObDMLStmt` family | `StmtId` | the statement factory's | `stmt_id_` stays a field |
| `TableItem`, `JoinedTable` | `TableItemId` | statement arena | `table_id_` stays a field |
| `SemiInfo` | `SemiInfoId` | statement arena | assumption: compared by address; the 02 inventory confirms |
| `ColumnItem`, `OrderItem` | none | values in arrays | a column item is already named by (table id, column id) |
| `ParseNode` | `NodeId` | `ParseTree` | §4.2 |
| `ObLogicalOperator` family, `ObLogPlan` | `OpId`, `LogPlanId` | optimizer arena | operator numbering stays separate |
| `Path` family, `ObJoinOrder`, `ObConflictDetector` | `PathId` (with the recycle stack), `JoinOrderId`, `ConflictDetectorId` | optimizer arena | |
| query-range graph nodes | `RangeNodeId` | query-range arena | |

The expression, statement and table-item arenas belong to the core's Statement IR (PLAN.md §3); the others to their own crates.

Why: typed ids are what PLAN.md §3 names; they keep lifetimes out of about 0.75M lines of signatures; the borrow checker, not a run-time `RefCell`, rules out two writers; `u64` ids stay exact across the factories C++ mixes and fit where PL packs addresses into integers (each of the 6 PL sites has the factory that made the expression at hand; assumption, checked by the 02 inventory); and the rules keep "the same object" exactly where C++ has it, which `output(...)` lists, plan-cache constraints and once-per-row evaluation depend on.

### 4.4 What Step 2a checks

- Full-parse time with conversion against C++ (the 1.2x gate), and how many parse nodes have two parents over the 272 cases.
- `nm` on the Rust binary for the strong `try_check_mem_status`.
- On the 40 plan-bearing cases, `output(...)` lists and plan-cache hit counts (family 7) exact, also with the reverse-iteration switch on.
- Whether the typed-id IR survives the about 580 `find_item` and set-helper lines and the 34 rules in the disposable full pass (PLAN.md §6).

## 5. Conventions from the sql-nio port: adopted or rejected

From /Users/colin/obsidian/tech/seekdb/migrate to rust/abi-naming.md and notes/ffi-mechanics.md.

| Convention | Here | Reason |
|---|---|---|
| The header is generated from Rust by cbindgen | Rejected for the island | The C ABI already exists (parse_node.h, sql_parser_base.h, parse_malloc.h) and the frozen core includes it; ffi-mechanics.md itself calls bindgen right for an existing C API (§4.1). |
| Generated file checked in, with a regenerate-and-diff gate | Adopted | For the bindings and for the generated grammar C (md5 check). |
| `<tag>_` prefix on functions Rust implements; the other side's symbols keep their names | Prefix rejected for the callbacks, adopted for any new symbol; names adopted | The C core calls fixed names (`parse_malloc`, `lookup_pl_symbol`, `try_check_mem_status`); renaming means editing frozen C. Rust calls `parse_sql` and the rest by their C names. |
| Receiver as the first parameter, never a global | Adopted where the C core passes one | `malloc_pool` and `pl_ns` are receivers; the stack checks and `try_check_mem_status` work on thread state, the note's case without one. The C core's own thread-locals are frozen and stay. |
| No function-pointer tables | Cannot apply | The lexer calls charset functions through `ObCharsetInfo::cset` (sql_parser_base.h:776-806), a table it takes from the caller. |
| All extern declarations in one file | Adopted | One module of the island crate. |
| Logic in safe code, the boundary in a thin unsafe layer | Adopted | It is Decision 14's named-crate rule at file scale: the converter and the callbacks are the only `unsafe`. |
| The boundary does not re-check lengths and pointers from the other side | Adopted | The converter trusts `num_child_` and `str_len_`: the C core is frozen and built in the same tree. |
| A zero length becomes `&[]` without `from_raw_parts` | Adopted | The C core leaves `str_value_` NULL with `str_len_` 0. |
| `_view` and `_handle` vocabulary | Not needed | No borrowed view crosses: the tree is converted. |
| `#[cfg(test)]` stubs for symbols defined elsewhere | Adopted, for the island's tests | `lookup_pl_symbol`, `try_check_mem_status` and the stack checks live in other crates; the island's unit tests link the real C core plus these stubs. |
| Pure Rust code picks Rust names | Rejected for the tree type | The tree mirrors a C struct the resolvers read field by field; C names keep the resolvers searchable against the C++. |

## 6. Questions only the developer can answer

1. Does Decision 13's "bison/flex output" cover all three grammars (SQL, PL and fulltext boolean mode), with pl_parser_base.c and the fulltext C files, in one C island? This report assumes yes.
2. For the lexer's charset calls, is option (a) of 3.2 acceptable for the first parity gate: the kept C++ charset subset supplies the tables, and parse_malloc.cpp's three charset helpers stay C++ in the island? Or must every callback be Rust from the start?
3. Is the owned-tree conversion (option (b) of 3.1) the design, with handles over the C tree only as the fallback if Step 2a's full-parse timing misses the 1.2x gate?
4. Are 8-byte ids (arena number plus index) acceptable for every IR type, given that Decision 1 does not count memory?
