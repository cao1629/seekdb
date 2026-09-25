#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import bisect
import collections
import concurrent.futures
import importlib.util
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import warnings

FROZEN_REV = '834bbee1e'
CODE_EXTS = ('.h', '.hh', '.hpp', '.hxx', '.c', '.cc', '.cpp', '.cxx', '.ipp', '.inc', '.def', '.y', '.l')

CATEGORIES = (
    'atomic-fields', 'refcount', 'const-cast', 'ret-compare', 'reset', 'tmp-ret', 'ret-alias', 'oom-sites',
    'server-service-slots', 'subclassed-bases', 'arena-handoff', 'borrowed-views', 'sort-hash-order',
    'pointer-identity', 'overflow', 'float-contraction', 'lock-guards', 'printf-calls', 'fast-parser', 'sizeof-formulas',
    'work-area-formulas', 'memmove', 'frame-internals', 'storage-sql-uses', 'batch-end-tracepoints', 'local-expr-factories',
    'null-arrays', 'expr-factory-callers', 'warning-buffer-sites',
)

PLAN_FIGURES = {
    'atomic-fields': 'about 823 field names',
    'refcount': '441 inc_ref/dec_ref lines and 130 Handle classes',
    'const-cast': '1,750 lines',
    'ret-compare': '4,189 lines',
    'reset': '2,905 resets',
    'tmp-ret': 'about 1,560 lines',
    'ret-alias': '31 uses in 18 files',
    'oom-sites': 'about 40 budget-backed sites plus the logical -4013 errors and the micro block cache FIFO',
    'server-service-slots': '122 slot types',
    'subclassed-bases': 'ObTimerTask 72, ObDLinkBase 72, ObFuncExprOperator 313',
    'arena-handoff': 'none (no figure in the plan)',
    'borrowed-views': 'none (no figure in the plan)',
    'sort-hash-order': 'about 169 lib::ob_sort call lines; 5 hash-order sites reaching plans',
    'pointer-identity': 'none (evidence: 34 pointer-keyed map sites in optimizer and rewrite)',
    'overflow': 'none (no figure in the plan)',
    'float-contraction': 'none (no figure in the plan)',
    'lock-guards': "One row per lock declaration (RULEBOOK section 5; s8-numerics-platform.md rules 5.1, 5.3, 5.7, 5.8), outside the primitives' own directory src/oblib/lib/lock, comments and strings blanked. A lock type is an OB lock primitive (ObLatch, ObLatchMutex, ObSpinLock, lib::ObMutex, ObUtilMutex, ObFutex, SpinRWLock, TCRWLock, DRWLock, ObRWLock, ObQSyncLock, ObSmallSpinLock and ObByteLock, ObPtrSpinLock, ObRowLatch, ObBucketLock, ObBucketQSyncLock, ObRecursiveMutex, ObThreadCond, ObCond, Cond, SimpleCond, the per-CPU condition SCondTemp and its alias SCond, LWaitCond, ObMonitor, the reader-count syncs ObQSync, ObDynamicQSync and TCRef, and the no-op ObNullLock, NLock and NCond); a pthread mutex, spin lock, read-write lock or condition; libeasy's easy_spinrwlock_t; a std mutex, shared mutex or condition variable; a class derived from one of these; a class whose name ends in Lock, Latch, Mutex or Cond whose primary definition (not an explicit or partial specialization) holds a lock member by value, or that defines lock, unlock, rdlock, wrlock, try_lock or a similar operation other than an accessor that takes no argument and returns a reference or pointer (CtxLock, ObLSLock, MemtableMgrLock, the keybtree's own RWLock; ObBucketLockCond, which only holds a reference to its bucket and whose lock() and cond() return the bucket's members, is not one, so its locals are not rows); or a typedef, using alias or object-like #define of any of them (zstd's ZSTD_pthread_mutex_t; an alias declared in a class applies to that class and its file; a name that is both a lock class and an alias is the class only in the class's own file stem; a primitive with a platform typedef keeps its own kind, and the row notes the typedef: pthread_spinlock_t is a pthread_mutex_t under #ifdef __APPLE__). Names ending in Monitor are not lock words: the one synchronization monitor, obutil::ObMonitor, is a primitive, and the other classes named so are services such as ObPxTargetMonitor. Rows: every field, static member, global, function static and local declared with such a type, arrays included (a declarator list gives one row per name; a declaration inside a #define body is one row per body line and the uses of the macro are not rows; members of C structs declared with typedef struct {...} name are members of that name); every pointer or reference member or global of a lock type, which points at a lock declared elsewhere or allocated at run time; every declaration whose type holds lock objects as elements (std::pair, std::tuple, std::array and the OB and std arrays and lists: std::pair<ObBucketLock, ObBucketLock>); every member whose type is a template parameter when the type or the member name has a lock, latch, mutex or cond word, pointer and reference members included (SetLocker's t_lock &mutex_); and every integer, char or bool field, static member or global whose name has a lock, latch, mutex or spin word and that its file stem acquires with a test-and-set or compare-and-swap (ATOMIC_TAS, ATOMIC_BCAS, ATOMIC_CAS, the __sync and __atomic builtins, and macros that wrap them, such as TP_BCAS), with a guard object built on it, or with an acquire call that takes its address: a hand-made spin lock, whose atomic accesses are also rows of atomic-fields.tsv (the state fields of a lock class and the references held by a guard are left out). Instances of templates that take a lock type as a parameter are not rows: ObLightHashMap<.., SpinRWLock>, ObPool<.., ObNullLock>, and ObHashMap and ObHashSet, whose default LatchReadWriteDefendMode locks every bucket; the template's own lock member is a row (ob_light_hashmap.h:532, ob_pool.h:77, ob_hashtable.h:247), and an instance's lock follows from its template argument. Rows in libeasy and zstd carry [vendored]. Each row gives the kind (mutex, read-write lock, bucket lock, reentrant mutex, condition, spin lock, reader-count sync, no-op lock, lock class, hand-made spin lock), where it is declared, and the guard objects (a *Guard type, std::lock_guard, unique_lock; each argument of the guard that names a lock counts, a leading this does not) and lock calls (lock, unlock, rdlock, wrlock, try_*, wait, signal, pthread_*) that name it in its file stem: for a member, only those in methods of its class and its subclasses, with those through another object and those inside #define bodies given separately as not attributed; for a local or function static, those in its function; with the timed ones (a guard type with Timeout or Retry, or an argument with a time, timeout, deadline, expire, ts or us word, so ObLatchIds::SERVER_RUNTIME_LOCK is not timed) and the try ones. The guarded fields, the functions called with the lock held and the nesting order are left to the classifier. Not seen: uses through an accessor (get_lock()), locks whose type a macro pastes together, local pointers and references to locks, function parameters of lock type, lock-like classes whose names do not end in Lock, Latch, Mutex or Cond and do not derive from a primitive, and hand-made spin locks whose names have no lock word.",
    'printf-calls': 'Every call of a printf-family function or macro, comments and strings blanked, definitions and declarations excluded (a call whose arguments are all parameter declarations is a declaration): the C library\'s printf, fprintf, dprintf, sprintf, snprintf, asprintf and their v forms; every function whose definitions end their parameters with a const char * format parameter (named like fmt or format) and ..., or pass a va_list after it; and every function-like macro that passes one of its parameters, or its variadic arguments, on as the format of such a call (BUF_PRINTF, DATA_PRINTF, _OB_LOG and the other underscore log macros, HASH_WRITE_LOG, FORWARD_USER_ERROR_MSG, the type-name printers of ob_obj_type.cpp), found to a fixed point. Where other overloads of a name are not printf-like (databuff_printf\'s object templates, add_plan_note(const ObString &), set_extra_info in other classes), each call is matched to an overload: by the receiver\'s class when the overloads sit in different classes (a call whose receiver does not resolve is counted below), then by argument count (a databuff_printf call with 3 arguments or with 5 and more can only be a printf overload), then by the argument at the format position: a string literal, a string macro, a concatenation of string macro parameters, a const char * constant, local or expression, or a char array is the printf overload (a non-const char * binds the T * template instead); a call whose argument there has a type the script does not resolve is a row marked overload not resolved. Calls count in function bodies the scanner does not recognize (explicit specializations such as ob_tuple.h\'s print_<sizeof...(T) - 1>, functions with trailing macros such as __THROW) and in the grammar actions of .y and .l files. A call inside the body of a macro that passes its format on is not a row, the macro\'s uses are; a call with a fixed format inside any other #define body is one row per body line (J_NAME, PRINT_BOUND), and that macro\'s uses are not rows. Each row gives the format as written with string literals (backslash-newline splices joined, raw string literals read), PRI* macros expanded to their macOS arm64 values and object-like macros whose body is a string literal or a concatenation of such macros (NEW_LINE, FETCH_ALL_COLUMN_SQL) resolved; a format held in a const char * constant or local initialized with literals (DIGIT_FORMAT, the array BACK_DIGIT_FORMAT, a ternary of literals) is shown with its values, and a ternary of literals as the format gives the conversions of each branch; preprocessor branches inside the argument list are resolved for macOS arm64 (_WIN32 and __linux__ off, __APPLE__ on). Then the conversions, the argument expressions after the format and each argument\'s type where the script resolves it (literals, casts, sizeof, locals and parameters, members of the enclosing class and its bases, globals and const char * constants, member and method steps through resolved classes, typedefs, functions whose return types the tree declares, a few libc functions, and macros that expand to a call, a cast or several arguments such as LEN_AND_PTR; ? where it does not), the conversions paired with the argument types, a mark when the argument count differs from what the conversions take, and marks: format not a literal, forwards a va_list, has %p, writes the server log (the underscore log macros, logdata_printf), records a user message (it reaches the client), writes stdout, stderr or a file, inside to_string. LOG_USER_ERROR, LOG_USER_WARN and LOG_USER_NOTE are not rows: their format is the catalog\'s, which s2-errors.md 2.7 and 2.11 check. The key-value log macros (LOG_WARN("msg", K(x))) are not printf calls. Not seen: calls through function pointers or std::function, formats built at run time (marked format not a literal), argument types the resolution does not reach, templates\' argument types, and whether a buffer\'s text reaches the client, which the classifier decides.',
    'fast-parser': "Each branch of ObFastParser's lexer (src/sql/parser/ob_fast_parser.{h,cpp}: ObFastParser, ObFastParserBase, ObFastParserMysql and the macros defined in those files) that makes or selects a parameter: the innermost case label group, or if, else-if or else branch, around a statement that sets cur_token_type_ = PARAM_TOKEN, builds a node (new_node with a T_ type, add_bool_type_node, add_null_type_node, lex_store_param), assigns a T_ node type (param_type = T_INT, node->type_ = T_NUMBER), calls a function or macro with a T_ type argument (CHECK_AND_PROCESS_NUMBER(T_DOUBLE)), or calls a lexer function that does any of these, found to a fixed point (process_number, process_string, process_hex_number, process_binary, process_time_relate_type, process_negative and the rest), in its body or in its own condition (else if (OB_FAIL(process_number(true)))). One row per branch, at its header, with what it produces and the chain of branches around it. A branch whose condition only tests an error or an allocation (every part of it joined by && or || is OB_FAIL, OB_SUCC, OB_ISNULL, OB_NOT_NULL, a NULL, OB_SUCCESS or ret comparison, and it calls no lexer function that produces a parameter), or the else of such a test, is not a row: what it produces moves to the nearest enclosing branch that is one (if (OB_SUCC(ret) && PARAM_TOKEN == cur_token_type_) gets node T_HEX_STRING, the else of process_string's allocation test gives its enclosing else T_VARCHAR and lex_store_param). A branch that only sets PARAM_TOKEN names the node types its function builds (T_HEX_STRING for process_hex_number and process_binary). T_INVALID assignments are not products. Whether the grammar makes the same constant a parameter is left to the classifier, against sql_parser_mysql_mode.y. Not seen: the identifier path reached through the process_idf_func_ member-function pointer from the default case (its own branches are rows), and literal recognition in helpers that branch on nothing.",
    'sizeof-formulas': "Proxy for s5-execution.md rule 4.9. Every sizeof in the execution code (src/sql/engine, src/sql/code_generator, src/sql/das, src/sql/dtl, src/query/api/query/engine), comments and strings blanked, whose statement names a batch size, bucket, dump, bypass, cache size, memory bound or limit, frame size, rowset, row count, partition count or shift, threshold, memory size, used, hold or data size, capacity or max size, or whose enclosing function's name says batch size, bucket, dump, bypass, memory bound, cache size, frame size, header size, memory size, memory used or partition; left out when the innermost call around it only allocates, copies, compares bytes, hashes, serializes, sets, creates, extends or resets, and in static_asserts and array dimensions. A sizeof whose statement assigns a local is followed through the function (and through the locals computed from that one, three steps deep; logging is ignored): when every later use only sizes an allocation, a copy or a memset or offsets a pointer (a buffer layout: buf + pos, mem += size, mem - begin != size), it is left out whatever its words, the same as a size written inside alloc(...); when a use reaches a statement with the decision words above or a call that decides (check_can_insert, need_dump, dump and the like), it is a row even without decision words in its own statement, and the row names the local and the statement it reaches (the subquery result cache's need_size, the frame packing's item_size). A named constant whose value is a sizeof expression (DATUM_EVAL_INFO_SIZE, FIX_SIZE_PER_PART, BIG_BLOCK_SIZE), defined in these directories, is a row at each use there that meets the same tests. One row per sizeof or constant use. Not seen: sizes that reach a decision through a member computed elsewhere (a store's mem_used_ summed per row), decision formulas outside these directories, and statements and callees whose words the vocabulary misses.",
    'work-area-formulas': "Proxy for s5-execution.md rule 6.9, outside the work area's own files (src/sql/engine/ob_sql_mem_mgr_processor.{h,cpp}, ob_sql_memory_manager.{h,cpp}), comments and strings blanked: each call on an ObSqlMemMgrProcessor (a member, pointer or local named like mem_processor) that reports a figure to the work area: init with its cache-size argument, alloc, update_used_mem_size, update_cache_size, update_delta_used_mem_size, set_number_pass, and update_max_available_mem_size_periodically and extend_max_memory_size with the return expressions of their callbacks; each statement that compares (<, >, <=, >=) a work-area figure (get_data_size, get_mem_bound, get_max_bound, get_max_available_mem_size, get_expect_size, get_cache_size) read next to a processor, a profile or a memory context, a getter of the same class whose body only returns such a figure (get_mem_bound_size(), get_memory_limit(), found to a fixed point; a getter of another class with the same name does not count), or a local computed from either in the same function (detect_part_cnt's availble_mem_size, adjust_part_cnt's max_part_cnt): the dump and partition decisions; and in the row stores, row arrays and hash tables under src/sql/engine and src/query/api/query/engine, each comparison with the store's own mem_limit_: the stores' own dump decisions. The formula behind each reported value, often in a helper such as get_mem_used_size(), is the classifier's to follow. Not seen: memory accounted without a processor call, processors reached through a name that does not say mem_processor, and figures that reach a decision through a member or another function.",
    'memmove': "Every MEMMOVE, memmove, std::memmove and __builtin_memmove call, comments and strings blanked, the MEMMOVE macro's own definitions and ob_memmove_safe left out, tagged by directory: src/sql/engine/expr (the 17 lines of s5-execution.md rule 3.5), the expression core under src/query/api/query/engine/expr (ObExpr's deep copy, the overlap ob_expr.h:1108 marks), and elsewhere. Calls in the C parser cores, in the grammar actions of the .y and .l files, and in vendored zstd are rows too, with their file flags. Not seen: result writes whose source may be the destination's own buffer but that copy with MEMCPY, memcpy or a loop, which rule 3.5 also covers.",
    'frame-internals': "Lines of the operator files, the *_op.{h,cpp} and *_op_impl.{h,cpp} files under src/sql/engine (comments and strings blanked), that reach into frame internals: frames_; the frame layout members frame_idx_, datum_off_, res_buf_off_, res_buf_len_, eval_info_off_, eval_flags_off_, pvt_skip_off_, dyn_buf_header_offset_ and vector_header_off_; locate_expr_datum, locate_batch_datums, locate_datum_for_write, locate_datums_for_update, locate_param_datum and the datum-vector forms; get_evaluated_flags, get_eval_info, get_pvt_skip, get_str_res_mem, get_reset_tmp_alloc, reset_ptr_in_datum; the evaluated and projected flag setters; BatchInfoScopeGuard; the batch index and size accessors; and reinterpret_cast to ObDatum. One row per line, listing what it uses and marking the lines R05's narrower pattern matches as whole names (frames_, locate_batch_datums, locate_expr_datum, locate_datum_for_write, get_evaluated_flags, get_eval_info, get_pvt_skip, reinterpret_cast<ObDatum). A call of the operator base method clear_evaluated_flag() (on this or another operator) is marked: it clears the eval flags of the operator's own output exprs (ob_operator.h:700-705), and a line with nothing else says it is no direct frame access. Not seen: helpers outside the operator files that operators call to reach the frame (the row stores, the aggregate processor, ob_batch_eval_util.h), and frame access through local aliases of these members.",
    'storage-sql-uses': "Lines under src/storage, comments and strings blanked, that use SQL objects: every line naming the sql namespace (sql::X, oceanbase::sql::X), with the names; every using namespace sql and using sql::X line; in a file with such a using line, every line that names without qualification a type declared at namespace scope inside namespace sql and in no other namespace (or the X of using sql::X); and each namespace sql { } block in a storage file: its opening line (with what it declares or defines), each line inside it that names such a type, and each SQL function it defines, at the function's header with its line range (ob_block_row_store.cpp defines sql::PushdownFilterInfo::init). R05's 1,070 lines were git grep over raw text, comments included. Not seen: SQL types reached through storage's own typedefs or templates, SQL functions and constants named without qualification, the lines of a defined SQL function that name no SQL type (its row gives the range), and SQL objects handed over as void pointers.",
    'batch-end-tracepoints': "Reads of tracepoints (EventTable::EN_* through EVENT_CALL, OB_E or EVENT_CODE, and errsim points a file defines with ERRSIM_POINT_DEF and reads by name), comments and strings blanked: every read of the four tracepoints s5-execution.md rule 4.6 names (311 EN_DAS_SIMULATE_GROUP_SIZE, 1200 EN_ENABLE_NEWSORT_FORCE, 2206 EN_ENABLE_RANDOM_BATCH_SIZE, 2501 EN_CHECK_SORT_CMP), tree-wide, and their definitions in ob_tracepoint_def.h; and, in the execution side and the optimizer (src/sql, src/query, src/storage/access, src/storage/blocksstable, src/share/vector, src/share/aggregate), every other read used as a value, a switch or a number, whatever its name says, since rule 4.6 also keeps tracepoints that pick algorithms and a name can hide the effect (EN_ENABLE_RANDOM_TSC randomizes table-scan batch sizes, EN_GENERATE_PLAN_WITH_NLJ and EN_FORCE_SLAVE_MAPPING pick plans). A read counts as a number when it is negated or passed through abs (EVENT_CALL, OB_E and EVENT_CODE alike), or when the code stores it in ret, reads ret back as a number and resets ret to OB_SUCCESS (EN_ENABLE_THREE_STAGE_AGGREGATE's parity picks the algorithm). A read inside OB_FAIL or OB_SUCC is an error injection only when the branch it takes when the tracepoint fires does nothing but log, set ret or return; otherwise it is a switch that also sets ret (tracepoint 551 at ob_explain_log_plan.cpp:143). Each row says how it is read, whether init.sql sets it, what batch, row, dump, sort, hash, bypass or cache word its name has, and the statement. Reads that only inject an error into ret (ret = OB_E(..), OB_FAIL(OB_E(..)) with such a branch, return OB_E(..)) and sets (TP_SET_EVENT) are not rows. Not seen: errsim points read through another file, and effects decided far from the read.",
    'local-expr-factories': '41 lines (s4-sql-front.md 2.3, evidence/s4-sql-front/counts2.txt)',
    'null-arrays': "Places where an array of IR pointers holds NULL (s4-sql-front.md 2.5 rule 15), comments and strings blanked, outside the C parser cores: push_back(NULL or nullptr); an element set to NULL (x.at(i) = NULL, x[i] = NULL); prepare_allocate and prepare_allocate_and_keep_count, whose new slots hold NULL until set; and extend_param_exprs. The array must resolve to pointers of an IR type (the pointer-identity list, ObExpr included), the element type being the outermost container's first template argument (an array of arrays, ObSEArray<ObSEArray<ObLogicalOperator*, 4>, 3>, holds empty arrays, not NULL, and is left out): a local or parameter declared as a container, C array or pointer-to-pointer of them (the nearest declaration of the name before the call); a member reached through a receiver, x.member or x->member, by the declared type of x (spec.values_ in the code generator, typedefs of arrays such as ExprFixedArray expanded); a member of the enclosing class whose declared container type holds them (by class, then by file stem); a member name that holds IR pointers in at least 80% of its declarations; a getter that returns such an array in at least 80% of its declarations; or, failing those, the declared type the argument-type resolution of printf-calls finds for the receiver expression. An array that resolves to other pointers or to non-pointers is left out; in the resolver, rewrite, optimizer, code generator, printer and PL directories a push_back(NULL), NULL store or prepare_allocate on an array that does not resolve is a row marked not resolved. Parse-tree children_ stores are left out: 1.2 rule 2 already makes a NULL child None. Not seen: arrays filled by assign or copy from an array that holds NULL, NULL passed through setters such as set_param_expr, and arrays reached through chains the resolution cannot follow.",
    'expr-factory-callers': "Every call of get_expr_factory(), comments and strings blanked, definitions and declarations excluded, in files outside src/sql/resolver, src/sql/rewrite and src/sql/optimizer (inside them RULEBOOK 2.4 makes the exec context's factory f.own()). Each row names the receiver: the exec context, an expression's own factory (2.3 rule 4), a PL AST's or package's factory, the optimizer context's, or unresolved, by the receiver's declared type when it resolves and by its name otherwise; marks a call whose result is stored into a compile context's expr_factory_ or passed to the constructor of a resolver, transformer or optimizer context (ObPLResolver, ObOptimizerContext, ObResolverParams): a hand-off to a compilation; says whether s4-sql-front.md 2.3 rule 5 counts the call among the ten hand-offs (the six in ob_sql.cpp and the four in ob_pl_build.cpp) or names it among the execution-time callers (SET, the outline executor, LOAD DATA, SPI, PL user-defined aggregates), so a row where the code and that list disagree (LOAD DATA's resolver context at ob_load_data_impl.cpp:94, the outline executor's optimizer context at ob_outline_executor.cpp:233) shows both; and gives the statement. Whether each runs at execution time or hands the factory to a compilation is the classifier's. Not seen: the exec context's factory used through a pointer cached earlier (ObRawExprFactory *f = ...; later uses of f), and calls inside the three compilation directories that run at execution time.",
    'warning-buffer-sites': 'the 15 sites s2-errors.md 2.6 rule 5 lists',
}

GENERATED_PATH_RE = re.compile(
    r'(?:\.pb\.(?:h|cc|cpp)$|\.pb-c\.(?:h|c)$|/ob_generated_[^/]*$|^src/share/system_variable/|'
    r'/ob_system_variable_factory\.(?:h|cpp)$|^src/share/ob_errno\.(?:h|cpp)$|^src/oblib/lib/ob_errno\.h$)')
VENDORED_PATH_RE = re.compile(r'(?:/zstd_1_3_8/zstd_src/|^src/oblib/easy/|^src/oblib/lib/hash_func/)')
DATA_PATHS = frozenset(('src/storage/fts/dict/ob_ik_dic.cpp', 'src/oblib/common/timezone/ob_timezone_info.cpp'))

LEX_RE = re.compile(
    r'''(?=[/"'uULR])(?://[^\n]*|/\*.*?\*/|(?<!\w)(?:u8|u|U|L)?R"([^()\\\s"]{0,16})\(.*?\)\1"|'''
    r'''(?:(?<!\w)(?:u8|u|U|L))?"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')''', re.S)
DIRECTIVE_RE = re.compile(r'^[ \t]*#[ \t]*(\w*)[ \t]*(\w*)', re.M)
DELIM_RE = re.compile(r'[{};]')

ACCESS_RE = re.compile(r'^(?:\s*(?:public|private|protected)\s*:(?!:))+')
NAMESPACE_RE = re.compile(r'^(?:inline\s+)?namespace\b')
EXTERN_BLOCK_RE = re.compile(r'^extern\s*""\s*$')
ENUM_RE = re.compile(r'^(?:typedef\s+)?enum\b')
INIT_LIST_RE = re.compile(r'\)\s*(?:(?:const|noexcept|override|final)\s*)*:(?!:)')
TRAILING_RETURN_RE = re.compile(r'\)\s*(?:const\s*)?->\s*[^()]*$')
TAIL_QUALIFIERS_RE = re.compile(
    r'(?:\s*(?:\b(?:const|volatile|override|final|noexcept|mutable)\b|&&|&|throw\s*\(\s*\)|'
    r'noexcept\s*\([^()]*\)|\bOB_WARN_UNUSED_RESULT\b))+\s*$')
OPERATOR_TAIL_RE = re.compile(
    r'\boperator\b\s*(\(\s*\)|\[\s*\]|new\s*\[\s*\]|delete\s*\[\s*\]|->\*?|""\s*\w+|'
    r'[-+*/%^&|~!=<>,]{1,3}|[A-Za-z_][\w\s:*&<>]*?)\s*$')
TEMPLATE_ARGS = r'<(?:[^<>;{}()]|<(?:[^<>;{}()]|<[^<>;{}()]*>)*>)*>'
CLASS_HEAD_RE = re.compile(
    r'^(?:typedef\s+)?(?:(?:static|extern|const|volatile|constexpr|inline|thread_local)\s+)*(class|struct|union)\b\s*(.*)$', re.S)
ATTRIBUTE_RE = re.compile(
    r'^(?:(?:alignas|__attribute__|__declspec|DEF_ALIGN|ALIGN_AS|CACHE_ALIGNED)\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*|'
    r'\[\[[^\]]*\]\]\s*)+')
CLASS_NAME_RE = re.compile(
    r'((?:[A-Za-z_]\w*\s*(?:' + TEMPLATE_ARGS + r')?\s*::\s*)*[A-Za-z_]\w*)\s*(' + TEMPLATE_ARGS + r')?\s*(?:\bfinal\b)?\s*')
NOT_FUNCTION_NAMES = frozenset((
    'if', 'for', 'while', 'switch', 'catch', 'return', 'sizeof', 'alignof', 'decltype', 'static_assert', 'defined',
    '__attribute__', 'alignas', 'new', 'delete', 'typeid', 'noexcept', 'throw', 'case', 'do', 'else', 'try', 'asm',
    '__asm__', 'volatile', 'typeof', '__typeof__', 'offsetof', 'const', 'void', 'int', 'char', 'bool'))
DECL_SKIP_RE = re.compile(
    r'^(?:typedef|using|friend|static_assert|template|namespace|enum|return|goto|case|default|operator)\b|'
    r'^(?:class|struct|union)\s+[\w:]+\s*$|^extern\s*""')
MACRO_STMT_RE = re.compile(r'^[A-Z_][A-Z0-9_]*\s*(?:\(|$)')
LEADING_MACRO_RE = re.compile(r'[A-Z_][A-Z0-9_]*\s*\(')
ACCESS_AT_RE = re.compile(r'(?:\s*(?:public|private|protected)\s*:(?!:))+')
DECLARATOR_LIST_RE = re.compile(
    r'^\s*[*&\s]*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*(?:=[^,;]*)?(?:,\s*[*&\s]*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*(?:=[^,;]*)?)*\s*$')
QUAL = r'(?:(?:::\s*)?(?:oceanbase\s*::\s*)?(?:common\s*::\s*)?)'
TRAILING_ATTRIBUTE_RE = re.compile(r'\b([a-z_][a-z0-9_]*)\s+([A-Z][A-Z0-9_]+(?:\s*\([^()]*\))?)\s*$')
LOWER_TYPE_RE = re.compile(r'u?int\d*_t|int|char|bool|double|float|long|short|unsigned|signed|size_t|ssize_t|void|auto|uchar|uint|int128_t|__int128|const|volatile|static|mutable')

HASH_TYPE_NAMES = (
    'ObHashMap', 'ObHashSet', 'ObHashTable', 'ObLinearHashMap', 'ObLinkHashMap', 'ObILinkHashMap',
    'ObAllocatingLinkHashMap', 'ObPointerHashMap', 'ObPlacementHashMap', 'ObPlacementHashSet',
    'ObIteratableHashMap', 'ObIteratableHashSet', 'ObCuckooHashMap', 'ObBuildInHashMap', 'ObArrayHashMap',
    'ObArrayIndexHashSet', 'ObDCHash', 'ObFixedHash2', 'ObSmallHashSet', 'ObSmallHashMap',
)
HASH_TYPE_GENERIC = r'Ob\w*Hash(?:Map|Set|Table)'
HASH_ANY_RE = re.compile(r'\b(' + '|'.join(HASH_TYPE_NAMES) + '|' + HASH_TYPE_GENERIC + r')\b')
CONTAINER_RET_RE = re.compile(r'Map|Set|Hash|Heap|Container|Table')
HEAP_NAME_RE = re.compile(r'Heap(?:Base)?$')
HEAP_OPS = ('push', 'pop', 'top', 'replace_top', 'remove', 'remove_by_index', 'update')
ITERATE_METHODS = r'(?:begin|cbegin|foreach_refactored|foreach|for_each)'
RECEIVER_GETTER_RE = re.compile(
    r'(?<![\w.>])([A-Za-z_]\w*)\s*(?:\.|->)\s*(get_\w+)\s*\(\s*\)\s*(?:\.|->)\s*' + ITERATE_METHODS + r'\s*\(')
IR_ROOTS = ('ObRawExpr', 'ObStmt', 'ObDMLStmt', 'ObLogicalOperator', 'ObLogPlan', 'ObJoinOrder', 'TableItem',
            'ColumnItem', 'SemiInfo', 'JoinedTable', 'ObExpr')
IR_CANDIDATE_RE = re.compile(
    r'\b(?:Ob\w*RawExpr|Ob\w*Stmt|TableItem|ColumnItem|SemiInfo|JoinedTable|FromItem|OrderItem|ObLogicalOperator|'
    r'ObLog\w+|ObJoinOrder|Ob\w*LogPlan|ObExpr)\b')
CROSS_THREAD_ROOTS = ('ObITask', 'ObIDag', 'ObIDagNet', 'ObTimerTask', 'ObAsyncTask', 'ObIOCallback', 'ObITransCallback',
                      'ObIKVCacheValue', 'ObIKVCacheKey', 'ObILibCacheObject', 'ObILibCacheNode', 'ObLink',
                      'ObSimpleThreadPool', 'ObThreadPool', 'ThreadPool', 'Threads', 'ObReentrantThread',
                      'ObAsyncTaskQueue', 'ObLinkQueueThreadPool', 'IObDedupTask', 'ObDDLTask')
PLAN_SUBCLASS_BASES = ('ObTimerTask', 'ObDLinkBase', 'ObFuncExprOperator')
SUBCLASS_BASES = PLAN_SUBCLASS_BASES + ('ObLink', 'ObIKVCacheKey', 'ObIKVCacheValue', 'ObITask', 'ObIDag',
                                        'ObIReplaySubHandler', 'ObICheckpointSubHandler', 'AppendCb')
MACRO_CLASS_RE = re.compile(r'\b(class|struct)\s+(\w+)\s*(?:final\s*)?:(?!:)\s*([^{;]*)\{')
INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)
ANY_INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^">]+)[">]', re.M)
SOURCE_EXTS = ('.c', '.cc', '.cpp', '.cxx')
INVENTORY_TOOL = 'tools/cmake/emit_bazel_source_inventory.py'
CMAKE_SOURCE_RE = re.compile(r'(?<![\w./$}-])([\w./-]+\.(?:c|cc|cpp|cxx|y|l))\b')
CMAKE_DIR_SOURCE_RE = re.compile(r'\$\{CMAKE_CURRENT_SOURCE_DIR\}/([\w./-]+\.(?:c|cc|cpp|cxx|y|l))\b')
CMAKE_ROOT_SOURCE_RE = re.compile(r'\$\{(?:CMAKE_SOURCE_DIR|PROJECT_SOURCE_DIR)\}/([\w./-]+\.(?:c|cc|cpp|cxx|y|l))\b')
VIEW_TYPES = ('ObString', 'ObDatum', 'ObObj', 'ObNewRow', 'ObRowkey', 'ObStoreRowkey', 'ObDatumRowkey', 'ObDatumRow',
              'ObDatumRange', 'ObNewRange', 'StoredRow', 'ObCompactRow', 'ObLobLocatorV2')
VIEW_TYPE_RE = re.compile(r'\b(' + '|'.join(VIEW_TYPES) + r')\b')
RAW_BYTES_RE = re.compile(r'^(?:const\s+)?(?:unsigned\s+)?(?:char|uchar|uint8_t|int8_t)\s*\*')
ALLOCATOR_TYPE_RE = re.compile(r'\b(\w*(?:Allocator|Arena|Alloc|MemoryContext|MemContext)\w*)\b')
NOT_ALLOCATOR_TYPES = frozenset(('ObMemAttr', 'ObAllocatorStat', 'ObMallocAllocator'))
FLOAT_TYPE_RE = re.compile(r'(?:^|\s)(?:const\s+)?(?:long\s+)?(?:double|float)\s*$')

PRIMITIVE_HEADERS = ('src/oblib/lib/atomic/ob_atomic.h', 'src/oblib/lib/atomic/atomic128.h')
ATOMIC_CALL_RE = re.compile(
    r'(?<![\w.>])(ATOMIC_[A-Z0-9_]+|__sync_\w+|__atomic_\w+|CAS128(?:_ASM)?|LOAD128(?:_ASM)?|_?Interlocked\w+|'
    r'(?:common\s*::\s*)?(?:inc|dec)_update)\s*\(')
NOT_ATOMIC_BUILTINS = frozenset(('__sync_synchronize', '__atomic_thread_fence', '__atomic_signal_fence',
                                 '__atomic_always_lock_free', '__atomic_is_lock_free'))
ATOMIC_TOKENS = ('ATOMIC_', '__sync_', '__atomic_', 'CAS128', 'LOAD128', 'inc_update', 'dec_update', 'Interlocked')
CAST_KEYWORD_RE = re.compile(r'\b(?:reinterpret_cast|static_cast|const_cast)\s*<[^()]*?>')
C_POINTER_CAST_RE = re.compile(r'\(\s*(?:(?:const|volatile|unsigned|struct)\s+)*[\w:]+(?:\s*<[^()]*?>)?\s*\*+\s*\)')
C_REF_CAST_RE = re.compile(r'\(\s*(?:(?:const|volatile|unsigned|struct)\s+)*[\w:]+(?:\s*<[^()]*?>)?\s*&\s*\)')
LOCAL_DECL_TEMPLATE = (
    r'(?:^|(?<=[;{}(]))\s*(?:(?:static|const|volatile|constexpr|thread_local|register|mutable|RLOCAL_STATIC|'
    r'__thread)\s+)*((?:[A-Za-z_][\w:]*)(?:\s*<(?:[^<>;{}()]|<(?:[^<>;{}()]|<[^<>;{}()]*>)*>)*>)?'
    r'(?:\s*::\s*[A-Za-z_]\w*)*(?:\s+const|\s+volatile)*)\s*((?:[*&\s]|\bconst\b|\bvolatile\b)+?)\s*%s\s*(\[[^\]]*\]\s*)?'
    r'(=(?!=)|;|\(|\{|,|:(?!:))')
GENERIC_ALIAS_NAMES = frozenset((
    'iterator', 'const_iterator', 'pointer', 'const_pointer', 'reference', 'const_reference', 'value_type', 'key_type',
    'mapped_type', 'size_type', 'difference_type', 'node_t', 'Container', 'self_type', 'base_type', 'BaseType', 'Base',
    'Self', 'Parent', 'SelfType', 'ElementType', 'KeyType', 'ValueType', 'Item', 'Node', 'Iter', 'Iterator', 'Value', 'Key',
    'Handle'))
CONTAINER_NAME_RE = re.compile(r'(?i)array|list|map|set|hash|heap|vector|queue|pair|store|buf|table|tree|deque')
RLOCAL_DECL_TEMPLATE = r'\bRLOCAL\w*\s*\(\s*([^,()]+?(?:<[^()]*>)?)\s*,\s*%s\s*\)'
NOT_TYPE_WORDS = frozenset(('return', 'else', 'if', 'case', 'delete', 'throw', 'goto', 'do', 'new', 'co_return',
                            'while', 'for', 'switch', 'sizeof', 'typedef', 'using', 'break', 'continue', 'default'))

SERVICE_TOKEN_RE = re.compile(
    r'(?<![\w:])(?:::\s*)?(?:oceanbase\s*::\s*)?(?:share\s*::\s*)?'
    r'(server_service|bind_server_service|unbind_server_service|server_obj_pool|borrow_server_object|'
    r'return_server_object|ObServerServiceSlot)\s*<')
SERVICE_MACRO_RE = re.compile(r'\b(BIND_SERVICE|UNBIND_SERVICE)\s*\(')

REFCOUNT_CALL_RE = re.compile(r'\b((?=[A-Za-z_])\w*ref\w*)\s*\(')
REFCOUNT_VERBS = frozenset(('inc', 'dec', 'incr', 'decr', 'acquire', 'release', 'revert', 'add', 'retain'))
REFCOUNT_TOKENS = frozenset(('ref', 'refs', 'uref', 'href', 'refcnt', 'refcount'))
REFCOUNT_EXCLUDED_TOKENS = frozenset(('query', 'subquery', 'obj', 'object'))
REF_WORD_TOKENS = frozenset(('ref', 'refs', 'uref', 'href', 'refcnt', 'refcount', 'reference'))
STRONG_REF_WORDS = frozenset(('unref', 'deref', 'xref', 'xhref'))
LIFECYCLE_VERBS = frozenset(('retain', 'release', 'acquire', 'aquire', 'born', 'end', 'retire'))
ATOMIC_CHANGE_RE = re.compile(
    r'\b(ATOMIC_(?:AAF|SAF|FAA|FAS|INC|DEC|BCAS|VCAS|CAS)\w*|__sync_(?:add|sub|fetch)_\w+)\s*\(\s*&?\s*\(?\s*'
    r'([A-Za-z_][\w.\->\[\]]*)')
PLAIN_CHANGE_RE = re.compile(r'\+\+|--|\+=|-=')
PLAIN_REF_FIELD_RE = re.compile(r'(?:^|_)(?:ref|reference)_?c(?:ou)?nt(?:_|$)')
IDENT_CALL_RE = re.compile(r'(?:\b([A-Za-z_]\w*)\s*(?:\.|->)\s*|\b([A-Za-z_]\w*)\s*::\s*)?\b([A-Za-z_]\w*)\s*(?:<[^<>;(){}]*>)?\s*\(')
CALL_KEYWORDS = frozenset(('if', 'for', 'while', 'switch', 'return', 'sizeof', 'catch', 'new', 'delete', 'static_cast',
                           'reinterpret_cast', 'const_cast', 'dynamic_cast', 'decltype', 'alignof', 'typeid', 'defined'))
DISK_REF_TOKENS = frozenset(('macro', 'block', 'blocks', 'addr', 'linked'))
DISK_REF_SEED_RE = re.compile(
    r'\b(?:OB_STORAGE_OBJECT_MGR|OB_SERVER_BLOCK_MGR|\w*object_mgr\w*|\w*block_mgr\w*|\w*block_manager\w*)\s*(?:\.|->)\s*'
    r'(?:inc|dec)_ref\s*\(')
DISK_REF_PARAM_RE = re.compile(r'\bMacroBlockId\b')
GUARD_CLASS_RE = re.compile(r'Guard$')
DEFINITION_PREFIX_RE = re.compile(
    r'\s*(?:template\s*<[^<>]*>\s*)?(?:(?:virtual|static|inline|OB_INLINE|OB_NOINLINE|explicit|constexpr|const|unsigned|signed|'
    r'extern|friend)\s+)*[A-Za-z_][\w:]*(?:\s*<[^()]*>)?[\s*&]+(?:[A-Z][A-Z0-9_]*\s+)*(?:[A-Za-z_]\w*\s*::\s*)*')
NON_TYPE_WORDS = frozenset(('return', 'else', 'if', 'case', 'delete', 'throw', 'goto', 'do', 'new', 'co_return'))
DECL_TAIL_RE = re.compile(
    r'\s*(?:(?:const|volatile|override|final|noexcept|OB_WARN_UNUSED_RESULT|__attribute__\s*\(\([^()]*\)\))\s*|&&\s*|&\s*|'
    r'noexcept\s*\([^()]*\)\s*|->\s*[\w:<>*&\s]+?(?=[;{=]))*(?:=\s*(?:0|default|delete)\s*)?([;{:]|$)')
CONST_CAST_RE = re.compile(r'\b(?:const_cast|const_pointer_cast)\s*<')
C_CONST_DROP_RE = re.compile(
    r'\(\s*(?!const\b)(?:(?:unsigned|signed|struct|volatile)\s+)*([A-Za-z_][\w:]*(?:\s*<[^()]*?>)?)\s*(\*+|&)\s*\)\s*'
    r'\(?\s*(&?)\s*([A-Za-z_]\w*)\b')
CONST_METHOD_RE = re.compile(r'\)\s*const\b[^()]*$')
CONST_NAME_DECL_TEMPLATE = (
    r'(?:\bconst\s+(?:unsigned\s+|signed\s+|struct\s+)?[A-Za-z_][\w:]*(?:\s*<[^;{}()]*?>)?\s*(?:\*\s*(?:const\s*)?)*[*&]\s*|'
    r'\b[A-Za-z_][\w:]*(?:\s*<[^;{}()]*?>)?\s+const\s*[*&]\s*)%s\b(?!\s*\()')
RET_VARS = r'(?:ret|tmp_ret|temp_ret)'
ERRNO_TOKEN_RE = re.compile(r'(?<![\w.>])' + QUAL + r'(OB_\w+)\b(?!\s*\()')
ASSIGN_IN_PARENS_RE = re.compile(r'\(\s*(' + RET_VARS + r')\s*=(?!=)')
SWITCH_RE = re.compile(r'\bswitch\s*\(')
CODE_COMPARE_RE = re.compile(
    r'(?:==|!=)\s*' + QUAL + r'(OB_\w+)\b(?!\s*\()|(?<![\w.>])(OB_\w+)\s*(?:==|!=)|\bcase\s+' + QUAL + r'(OB_\w+)\s*:(?!:)')
CASE_CODE_RE = re.compile(r'\bcase\s+' + QUAL + r'(OB_\w+)\s*:(?!:)')
RESET_RE = re.compile(r'(?<![\w.>:&*])ret\s*=\s*' + QUAL + r'OB_SUCCESS\s*(?=[;),])')
RESET_ZERO_RE = re.compile(r'(?<![\w.>:&*])ret\s*=\s*0\s*;')
ERROR_RET_DECL_RE = re.compile(r'\bint\s+ret\s*=\s*' + QUAL + r'OB_SUCCESS\b|\bINIT_SUCC\s*\(\s*ret\s*\)')
RESET_LINE_START_RE = re.compile(r'^\s*ret\s*=\s*(?:common::)?OB_SUCCESS\s*;')
RESET_DECL_RE = re.compile(r'\b(?:int|int32_t|int64_t|auto)\s*[&*]?\s*$')
COND_RESET_RE = re.compile(
    r'(?<![\w.>:&*])ret\s*=(?!=)([^;]*)\?\s*' + QUAL + r'OB_SUCCESS\s*:|(?<![\w.>:&*])ret\s*=(?!=)([^;]*)\?[^;]*:\s*' + QUAL
    + r'OB_SUCCESS\s*;')
CODE_VAR_RE = re.compile(r'\b(?:\w+_)?(?:ret|err|errno|err_code|error_code|ret_code|errcode)(?:_\w+)?_?\b|\bis_\w*error\w*\s*\(')
ERRSIM_RE = re.compile(r'\b(?:ERRSIM\w*|EN_\w+|EVENT_CALL|OB_E|TP_\w+|\w+_ERRSIM|errsim\w*)\b')
UPPER_CONDITION_RE = re.compile(r'^\s*\(?\s*[A-Z][A-Z0-9_]*(?:\s*::\s*[A-Z][A-Z0-9_]*)?\s*\)?\s*$')
TMP_VAR = r'(?:tmp_ret\w*|temp_ret\w*)'
TMP_DECL_RE = re.compile(r'\b(?:int|int32_t|int64_t|auto)\s*&?\s*(' + TMP_VAR + r')\b')
TMP_FAIL_RE = re.compile(r'\bOB_TMP_FAIL\s*\(')
TMP_ASSIGN_RE = re.compile(r'(?<![\w.>])(' + TMP_VAR + r')\s*=(?!=)')
TMP_MERGE_RE = re.compile(r'(?<![\w.>:&*])ret\s*=(?!=)[^;]*\b' + TMP_VAR + r'\b|\bCOVER_SUCC\s*\(\s*' + TMP_VAR + r'\b')
TMP_PLAN_DECL_RE = re.compile(r'\bint\s+tmp_ret\s*=\s*' + QUAL + r'OB_SUCCESS\s*;|\bINIT_SUCC\s*\(\s*tmp_ret\s*\)')
TMP_INIT_SUCC_RE = re.compile(r'\bINIT_SUCC\s*\(\s*(' + TMP_VAR + r')\s*\)')
SUCCESS_TEST = r'(?:OB_SUCC\s*\(\s*ret\s*\)|' + QUAL + r'OB_SUCCESS\s*==\s*ret|ret\s*==\s*' + QUAL + r'OB_SUCCESS)'
IF_MERGE_RE = re.compile(
    r'\bif\s*\(\s*' + SUCCESS_TEST + r'\s*(?:&&\s*(?:' + QUAL + r'OB_SUCCESS\s*!=\s*\w+|\w+\s*!=\s*' + QUAL
    + r'OB_SUCCESS)\s*)?\)\s*\{\s*(ret\s*=\s*([A-Za-z_]\w*)\s*;)\s*\}')
MERGE_OTHER_RE = re.compile(
    r'(?:(?<![\w.>:&*])ret\s*=(?!=)|\breturn\b)\s*\(*\s*(?:' + QUAL + r'OB_SUCCESS\s*==\s*ret|ret\s*==\s*' + QUAL
    + r'OB_SUCCESS|OB_SUCC\s*\(\s*ret\s*\))\s*\)*\s*\?\s*\(*\s*([A-Za-z_][\w.\->]*)\s*\)*\s*:\s*\(*\s*ret\b|'
    r'\bCOVER_SUCC\s*\(\s*([A-Za-z_][\w.\->]*)\s*\)')
RET_ALIAS_RE = re.compile(r'\bint\s*&\s*ret\s*=\s*([\w.\->]+?)\s*;')
MEMBER_ERROR_ASSIGN_RE = re.compile(
    r'(?<![\w.>])((?:this\s*->\s*)?(?:\w+_)?(?:ret|err|err_code|error_code|errcode|error_ret|ret_code)_)\s*=(?!=)')
MEMBER_ASSIGN_RE = re.compile(r'(?<![\w.>])\*?\s*((?:this\s*->\s*)?[A-Za-z_]\w*_)\s*=(?!=)')
SEARCH_ALGO_RE = re.compile(
    r'\b(std\s*::\s*(?:sort|stable_sort|lower_bound|upper_bound|binary_search|equal_range|partial_sort|nth_element|'
    r'make_heap|push_heap|pop_heap|min_element|max_element)|(?:(?:oceanbase\s*::\s*)?lib\s*::\s*)?ob_sort)\s*\(')

OOM_CODE = r'(?:OB_ALLOCATE_MEMORY_FAILED|OB_PARSER_ERR_NO_MEMORY)'
OOM_RAISE_RE = re.compile(
    r'(?<![=!<>])=\s*' + QUAL + OOM_CODE + r'\b(?!\s*(?:==|!=))|\breturn\s+' + QUAL + OOM_CODE + r'\b|'
    r'(?<!:)[?:](?!:)\s*' + QUAL + OOM_CODE + r'\b|\b(?:O[VXZ]|CK)\s*\(.*,\s*' + QUAL + OOM_CODE + r'\s*\)')
OOM_DECL_RE = re.compile(r'\b(?:int|int32_t|int64_t|auto)\s+\w+\s*=\s*' + QUAL + OOM_CODE + r'\b')
OOM_TOKEN_RE = re.compile(OOM_CODE)
OOM_COMPARE_RE = re.compile(r'(?:==|!=)\s*' + QUAL + OOM_CODE + r'\b|\b' + OOM_CODE + r'\s*(?:==|!=)')
OOM_CASE_RE = re.compile(r'\bcase\s+' + QUAL + OOM_CODE + r'\s*:(?!:)')
BUDGET_CODE_NAME_RE = re.compile(r'OB_\w*(?:MEM|MEMORY)\w*(?:LIMIT|EXHAUSTED|EXCEED)\w*|OB_EXCEED_\w*MEM\w*|OB_\w*OUT_OF_MEM\w*')
EXTRA_BUDGET_CODES = ('OB_ALLOCATE_TMP_FILE_PAGE_FAILED',)
TARGS = r'\s*(?:<[^<>;]*(?:<[^<>;]*>[^<>;]*)*>)?\s*\('
ALLOCATION_TOKEN_RE = re.compile(
    r'\b\w*(?:alloc|Alloc|ALLOC)\w*' + TARGS + r'|\bnew\b|\bOB_NEW\w*|\breserve\w*' + TARGS + r'|\bpush_back' + TARGS
    + r'|\bdeep_copy\w*' + TARGS + r'|\bcopy\w*' + TARGS + r'|\bclone\w*' + TARGS + r'|\bcreate\w*' + TARGS
    + r'|\bborrow\w*' + TARGS + r'|\bacquire\w*' + TARGS + r'|\bexpand\w*' + TARGS + r'|\bextend\w*' + TARGS
    + r'|\bob_write_string' + TARGS + r'|\bwrite_string' + TARGS + r'|\bdup\w*' + TARGS + r'|\bprepare\w*' + TARGS
    + r'|\bassign' + TARGS + r'|\bappend\w*' + TARGS + r'|\binit\w*' + TARGS + r'|\bset_\w*' + TARGS
    + r'|\bget_\w*(?:mem|buf|op)\w*' + TARGS + r'|\bmake_\w*' + TARGS + r'|\bbuild_\w*' + TARGS + r'|\bconstruct\w*' + TARGS
    + r'|\bnew_\w*node' + TARGS + r'|\bbad_alloc\b')
NULL_TESTED_RE = re.compile(
    r'(?:ISNULL|NOT_NULL|is_null)\s*\(\s*([^()]+?)\s*\)|\b(?:NULL|nullptr)\s*[!=]=\s*([\w.\->\[\]]+)|'
    r'([\w.\->\[\]]+)\s*[!=]=\s*(?:NULL|nullptr)\b')
NULL_TEST_RE = re.compile(
    r'ISNULL\s*\(|\bNULL\s*[!=]=|[!=]=\s*NULL\b|\bnullptr\s*[!=]=|[!=]=\s*nullptr\b|\bOB_NOT_NULL\s*\(|'
    r'\bif\s*\(\s*!\s*[A-Za-z_]\w*\s*\)|^\s*!\s*[A-Za-z_][\w.\->]*\s*$|\bis_null\s*\(|\bNOT_NULL\s*\(')
BUDGET_CHECK_CALL_RE = re.compile(
    r'\bObMemTrackerGuard\s*::\s*(?:try_)?check_status\s*\(|\b(?:TRY_)?CHECK_MEM_STATUS\s*\(|\b(?:try_)?check_mem_status\s*\(')
PLAN_LOGICAL_CASES = (
    ('hash-join partition depth', r'^src/sql/engine/(?:join/ob_hash_join|basic/ob_hash_partitioning)',
     r'\bpart_shift_\b|\bpart_level\w*|\bMAX_PART_LEVEL\b'),
    ('KV-cache handle pool', r'^src/share/cache/ob_kvcache_store\.cpp$', r'\bmb_handles_pool_\b'),
    ('IVF cache', r'^src/observer/vector_index/ob_vector_index_ivf_cache_mgr\.', r'\breach_limit\w*|\bmemory_limit\w*'),
    ('vsag NO_ENOUGH_MEMORY', r'^src/oblib/lib/vector/ob_vsag_adaptor\.', r'\bNO_ENOUGH_MEMORY\b'),
)
BUDGET_SEEDS = (
    ('clog and replay log allocator', True, r'^src/logservice/',
     r'\balloc_mgr_\s*->\s*alloc_\w+\s*\(|\b(?:clog_ge_alloc_|replay_log_task_alloc_|log_handle_submit_task_alloc_|'
     r'log_io_\w+_task_alloc_|clog_blk_alloc_|replay_log_task_blk_alloc_)\b'),
    ('IO allocator', True, r'^src/share/io/', r'\b(?:io_allocator_|inner_allocator_)\s*(?:\.|->)\s*alloc\w*\s*\('),
    ('IO manager FIFO', False, r'^src/share/io/ob_io_manager\.cpp$', r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('KV cache store', True, r'^src/share/cache/ob_kvcache_store\.cpp$', r'\b(?:alloc_mb|alloc_cache_mb|reserve_store_size)\b'),
    ('micro block cache FIFO (4GB)', True, r'^src/storage/blocksstable/ob_micro_block_cache\.cpp$',
     r'\ballocator_\s*(?:\.|->)\s*alloc\w*\s*\('),
    ('temp-file write buffer pool', True, r'^src/storage/tmp_file/ob_tmp_file_write_buffer_pool\.cpp$',
     r'\ballocator_\s*\.\s*alloc\w*\s*\('),
    ('SQL work-area spill', 'condition', r'^src/(?:sql|query)/',
     r'\bsql_mem_processor_\b|\bneed_dump\s*\(|\benable_sql_dumped_\b|\bextend_max_memory_size\s*\('),
    ('LS allocator FIFO (1GB)', False, r'', r'\bls_allocator_\s*\.\s*alloc\w*\s*\('),
    ('resource map FIFO', False, r'^src/storage/ob_resource_map\.h$', r'\b(?:default_allocator_|allocator_)\s*(?:\.|->)\s*alloc\w*\s*\('),
    ('meta cache IO FIFO (4GB)', False, r'', r'\bmeta_cache_io_allocator_\s*(?:\.|->)\s*alloc\w*\s*\('),
    ('async task queue FIFO (1GB)', False, r'^src/oblib/lib/thread/ob_async_task_queue\.', r'\ballocator_\s*\.\s*alloc\w*\s*\('),
    ('dedup queue FIFO (1GB)', False, r'^src/oblib/lib/thread/ob_dedup_queue\.', r'\ballocator_\s*\.\s*alloc\w*\s*\(|\bcopy_task_\s*\('),
    ('DDL scheduler FIFO (1GB)', False, r'^src/rootserver/ddl_task/ob_ddl_scheduler\.', r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('DDL task executor FIFO (1GB)', False, r'^src/share/ob_ddl_task_executor\.', r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('DDL redo replayer FIFO (10GB)', False, r'^src/storage/ddl/ob_ddl_redo_log_replayer\.',
     r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('storage meta persister FIFO (512MB)', False, r'^src/storage/meta_store/ob_server_storage_meta_persister\.',
     r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('checkpoint writer FIFO (128MB)', False, r'^src/storage/slog_ckpt/ob_server_checkpoint_writer\.',
     r'(?<![\w>.])allocator_\s*\.\s*alloc\w*\s*\('),
    ('vector module (vsag allocator, vector memory limit)', True, r'', r'(?:\.|->)\s*Allocate\s*\('),
)
INDIRECT_OOM_RE = re.compile(r'\bENOMEM\b|!\s*[\w.\->\[\]]+\s*(?:\.|->)\s*is_valid\s*\(\s*\)|\b[\w\]]+\s*(?:\.|->)\s*empty\s*\(\s*\)')
LOG_CALLEE_RE = re.compile(r'\w*LOG\w*|K|KR|KP|KPC|K_|KP_|KPC_')
BUDGET_MEMBER = 'allocator_'
ALIAS_OF_MEMBER_RE = re.compile(r'\b([A-Za-z_]\w*)\s*=\s*&\s*' + BUDGET_MEMBER + r'\s*;')
FIFO_FILE_RE = re.compile(r'^src/storage/blocksstable/ob_micro_block_cache\.(?:h|cpp)$')
FIFO_LINE_RE = re.compile(
    r'\bObConcurrentFIFOAllocator\s+allocator_\b|\ballocator_\s*\.\s*(?:init|set_attr|destroy)\s*\(|'
    r'=\s*&\s*allocator_\b|\bALLOC_BUF_RETRY_\w+|\bmem_limit\s*=\s*4\b|\bcallback\s*->\s*allocator_\s*=|'
    r'\bObIAllocator\s*\*\s*allocator_\s*;')

SORT_CALL_RE = re.compile(
    r'\b(std\s*::\s*(?:sort|stable_sort|partial_sort|partial_sort_copy|nth_element|make_heap|push_heap|pop_heap|'
    r'sort_heap))\s*\(|(?<![\w.>])((?:(?:oceanbase\s*::\s*)?lib\s*::\s*|common\s*::\s*)?ob_sort)\s*\(|'
    r'(?<![\w.>])(qsort(?:_r)?)\s*\(|(?:\.|->)\s*(sort|stable_sort)\s*\(|(?<![\w.>:~])(sort|stable_sort)\s*\(\s*\)\s*;')
SORT_FUNCTION_NAMES = frozenset(('sort', 'stable_sort', 'ob_sort'))
ID_NAME_RE = re.compile(r'(?:^|_)(?:id|ids|idx|index|seq|key|no)(?:_|$)', re.I)
POINTER_CAST_RE = re.compile(
    r'reinterpret_cast\s*<\s*(?:const\s+)?(?:u?int64_t|uintptr_t|intptr_t|uint64|int64|u?int32_t|size_t)\s*>\s*(\()|'
    r'\(\s*(?:u?int64_t|uintptr_t|intptr_t|uint64|int64|size_t)\s*\)\s*(\(?\s*[&*]?\s*[A-Za-z_][\w.\->]*)')
MEMBERSHIP_RE = re.compile(
    r'\b((?:(?:ObOptimizerUtil|ObTransformUtils|ObRawExprUtils)\s*::\s*)?(?:find_item|find_item_idx|has_exist_in_array|is_contain|'
    r'add_var_to_array_no_dup|append_array_no_dup|remove_item|intersect|is_subset|overlap|append_exprs_no_dup|find_expr|'
    r'get_expr_idx))\s*\(')
MIXED_MEMBERSHIP_RE = re.compile(r'append_exprs_no_dup|ObTransformUtils\s*::\s*find_expr')
IR_PTR_DECL_RE = re.compile(r'\b([A-Z]\w*)\s*\*+\s*(?:const\s*)?&?\s*([A-Za-z_]\w*)\b')
IR_ARRAY_DECL_RE = re.compile(
    r'\b(?:ObIArray|ObSEArray|ObArray|ObFixedArray|ObSqlArray|ObArrayWrap|ObList)\s*<\s*(?:const\s+)?([A-Z]\w*)\s*\*'
    r'[^;{}()]*?>\s*[&*]?\s*([A-Za-z_]\w*)')
OPERAND_END_RE = re.compile(
    r'([*&]?\s*[A-Za-z_]\w*(?:\s*(?:\.|->|::)\s*[A-Za-z_]\w*|\s*\[[^\[\]]*\]|\s*\((?:[^()]|\([^()]*\))*\))*)\s*$')
OPERAND_START_RE = re.compile(
    r'\s*([*&]?\s*[A-Za-z_]\w*(?:\s*(?:\.|->|::)\s*[A-Za-z_]\w*|\s*<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>(?=\s*\()|'
    r'\s*\[[^\[\]]*\]|\s*\((?:[^()]|\([^()]*\))*\))*)')
LITERAL_OPERANDS = frozenset(('NULL', 'nullptr', 'this', 'true', 'false'))
EQ_OP_RE = re.compile(r'(?<![=!<>])(==|!=)(?!=)')

VALUE_DIRS = ('src/sql/engine/expr/', 'src/sql/engine/aggregate/', 'src/sql/engine/window_function/',
              'src/oblib/common/json_type/', 'src/storage/access/ob_pushdown_aggregate',
              'src/oblib/common/number/', 'src/oblib/common/object/', 'src/oblib/common/wide_integer/',
              'src/oblib/common/timezone/', 'src/share/object/', 'src/share/datum/', 'src/query/api/query/engine/expr/',
              'src/query/api/query/engine/aggregate/')
FLOAT_DIRS = ('src/oblib/common/number/', 'src/sql/engine/expr/', 'src/sql/engine/aggregate/',
              'src/sql/engine/window_function/', 'src/sql/optimizer/', 'src/query/api/query/engine/expr/',
              'src/query/api/query/engine/aggregate/', 'src/data_plane/api/data_plane/vector/')
BUILTIN_OVERFLOW_RE = re.compile(r'\b__builtin_(?:[su]?(?:add|sub|mul))(?:l|ll)?_overflow\s*\(')
OVERFLOW_HELPER_RE = re.compile(r'\b(\w*(?:out_of_range|overflow|OVERFLOW|Overflow)\w*)\s*\(')
OVERFLOW_HELPER_SKIP_RE = re.compile(
    r'stack_overflow|STACK_OVERFLOW|overflow_size|overflow_slot|sstable_overflow|dag_count|child_no_overflow|ZSTD_|'
    r'error$|_err$|set_err|^OB_|double|float|DOUBLE|FLOAT|Double|Float')
FLOAT_CONDITION_RE = re.compile(
    r'\b\w*(?:double|float|DOUBLE|FLOAT|Double|Float)\w*\s*\(|\bget_(?:double|float)\s*\(|\bfabs\w*\s*\(|'
    r'\bisinf\s*\(|\bisnan\s*\(|\bstd\s*::\s*is(?:inf|nan|finite)\s*\(|\b(?:DBL|FLT|LDBL)_(?:MAX|MIN|EPSILON)\b|'
    r'\bHUGE_VALF?\b|\bINFINITY\b|\bNAN\b|(?<![\w.])(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?[fFlL]?\b')
BUFFER_TERM_RE = re.compile(r'\b\w*(?:len|length|size|buf|pos|capacity|remain)\w*\b', re.I)
SUBSCRIPT_RE = re.compile(r'\[[^\[\]]*\]')
OVERFLOW_ERROR_RE = re.compile(
    r'(?:(?<![=!<>])=\s*|\breturn\s+|(?<!:)[?:](?!:)\s*)' + QUAL
    + r'(OB_OPERATE_OVERFLOW|OB_DATA_OUT_OF_RANGE|OB_DATETIME_FUNCTION_OVERFLOW|OB_INTEGER_PRECISION_OVERFLOW|'
    r'OB_DECIMAL_PRECISION_OVERFLOW|OB_NUMERIC_OVERFLOW|OB_ERR_CAST_NUMBER_OVERFLOW|OB_ERR_WARN_DATA_OUT_OF_RANGE|'
    r'OB_DOUBLE_OVERFLOW|OB_DECIMAL_OVERFLOW_WARN)\b(?!\s*(?:==|!=))')
LIMIT_CONST_RE = re.compile(
    r'\b(?:INT64_MAX|INT64_MIN|INT32_MAX|INT32_MIN|UINT64_MAX|UINT32_MAX|INT_MAX|INT_MIN|LLONG_MAX|LLONG_MIN|'
    r'INT16_MAX|INT16_MIN|INT8_MAX|INT8_MIN|UINT16_MAX|UINT8_MAX)\b|numeric_limits\s*<[^>]*>\s*::\s*(?:max|min|lowest)')
COMPARISON_RE = re.compile(r'(?<![<>=!-])(?:<=|>=|<|>|==|!=)(?![<>=])')
BINARY_ARITH_RE = re.compile(r'[\w)\]]\s*(?:\+|-(?!>)|\*|/)(?![+\-=])\s*[\w(]')
SIGNED_GETTER = (r'get_(?:(?:\w*_)?int(?:8|16|32|64)?|(?:tiny|small|medium)int|datetime|date|time|timestamp|mysql_datetime|'
                 r'mysql_date|interval_nmonth|interval_ym|nmonth)')
UNSIGNED_GETTER = r'get_(?:(?:\w*_)?uint(?:8|16|32|64)?|u(?:tiny|small|medium)int|bit|enum|set|year)'
INT_GETTER = r'(?:' + SIGNED_GETTER + r'|' + UNSIGNED_GETTER + r')'
UNSIGNED_GETTER_RE = re.compile(r'\b' + UNSIGNED_GETTER + r'\b')
INT_TYPES = r'(?:(?:const\s+)?(?:u?int(?:8|16|32|64)_t|u?int128_t|__int128|uint|int|long|long\s+long|unsigned(?:\s+(?:int|long))?))'
UNSIGNED_TYPE_RE = re.compile(r'\buint|\bunsigned\b')
POST_CHECK_RE = re.compile(r'\b(is_\w*out_of_range)\s*\(')
GETTER_ARGS = r'\s*\(\s*[\w.\->\[\]]*\s*\)'
SIGNED_GETTER_RE = re.compile(r'\b' + INT_GETTER + GETTER_ARGS)
VALUE_LOCAL_RE = re.compile(r'\b' + INT_TYPES + r'\s+([A-Za-z_]\w*)\s*=\s*[^;]*\b' + INT_GETTER + GETTER_ARGS)
VALUE_ASSIGN_RE = re.compile(r'(?<![\w.>])([A-Za-z_]\w*)\s*=(?!=)[^;]*\b' + INT_GETTER + GETTER_ARGS)
INT_LOCAL_DECL_TEMPLATE = r'\b(' + INT_TYPES + r')\s+%s\s*[=;,)]'
SIGNED_PARAM_RE = re.compile(r'\b' + INT_TYPES + r'\s+([A-Za-z_]\w*)\s*(?:,|$)')
GETTER_ARITH_RE = re.compile(
    r'\b(' + INT_GETTER + r')' + GETTER_ARGS + r'\s*(?:[-+*]|<<)(?![-+=>])|(?<![-+])(?:[-+*]|<<)(?![->=])\s*(?:[\w\[\]]+\s*(?:\.|->)\s*)*'
    r'(' + INT_GETTER + r')' + GETTER_ARGS)
FMA_CALL_RE = re.compile(
    r'(?<![\w.])(?:std\s*::\s*)?(fma[fl]?)\s*\(|\b(__builtin_fma[fl]?)\s*\(|'
    r'\b(_mm(?:256|512)?_(?:mask3?_|maskz_)?f(?:n)?m(?:add|sub|addsub|subadd)_\w+)\s*\(|\b(v(?:q)?fm[as]q?_\w+)\s*\(')
PRODUCT_RE = re.compile(r'([\w)\]])\s*\*\s*[\w(]')
ADD_SPLIT_RE = re.compile(r'(?<=[\w)\]\s])(?<![+\-*/=<>!&|^%])(?:\+|-)(?![+\->=])')
ADDITIVE_RE = re.compile(r'(?<![+\-*/=<>!&|^%])(?:\+|-)(?![+\->=])|\+=|-=')
FLOAT_LITERAL_RE = re.compile(r'(?<![\w.])(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?[fFlL]?\b|\b\d+[eE][-+]?\d+\b')
FLOAT_SEGMENT_HINT_RE = re.compile(
    r'\b(?:double|float|c_cast_double|c_cast_float|static_cast_double|static_cast_float)\b|\bget_(?:double|float)\b')
SEGMENT_SPLIT_RE = re.compile(r'&&|\|\||\?|(?<!:):(?!:)|,|<=|>=|==|!=|\s<\s|\s>\s|;|\breturn\b')
TYPE_WORDS = frozenset(('double', 'float', 'int', 'char', 'void', 'const', 'auto', 'int64_t', 'int32_t', 'uint64_t',
                        'uint32_t', 'bool', 'long', 'short', 'unsigned', 'signed', 'size_t'))
HANDOFF_METHODS = (
    'push', 'push_task', 'add_task', 'add_dag', 'add_dag_net', 'schedule', 'schedule_task', 'submit', 'submit_task',
    'async_submit', 'post', 'put', 'put_kvpair', 'put_and_fetch', 'add_cache_obj', 'add_plan', 'enqueue',
    'add_async_task', 'push_back_task', 'add_timer_task', 'async_process', 'async_call')
UNQUALIFIED_HANDOFF_METHODS = ('add_task', 'add_dag', 'add_dag_net', 'push_task', 'submit_task', 'add_async_task',
                               'add_timer_task', 'schedule_task')
HANDOFF_CALL_RE = re.compile(
    r'(?:(?:\.|->)\s*(' + '|'.join(HANDOFF_METHODS) + r')|\b(TG_PUSH_TASK|TG_SCHEDULE|TG_SUBMIT\w*)|(?<![\w.>:~])('
    + '|'.join(UNQUALIFIED_HANDOFF_METHODS) + r'))\s*\(')
ALLOC_ASSIGN_RE = re.compile(
    r'\b([A-Za-z_]\w*)\s*=\s*(?:static_cast\s*<[^;]*?>\s*\(\s*|reinterpret_cast\s*<[^;]*?>\s*\(\s*|\([^();]*\*\s*\)\s*)?'
    r'(?:[\w.\->\[\]]*?\balloc\w*\s*\(|OB_NEWx?\s*\(|new\s*\(|op_alloc\w*\s*\()')
ALLOC_OUT_PARAM_RE = re.compile(r'\b(?:alloc|create)_\w*\s*(?:<[^<>;()]*>)?\s*\((?:[^();]|\([^()]*\))*?,?\s*&?\s*([A-Za-z_]\w*)\s*\)')
ALLOCATOR_ARG_RE = re.compile(
    r'(?<!\w)(?:&\s*|\*\s*)?(?:(?:allocator_?|alloc_?|arena_?|\w+_allocator_?|\w*mem_context_?|\w*mem_ctx_?)(?![\w(])|'
    r'get_\w*(?:allocator|mem_context|arena)\w*\s*\(\s*\))')
THREAD_POOL_ROOTS = ('ObSimpleThreadPool', 'ObThreadPool', 'ThreadPool', 'Threads', 'ObReentrantThread', 'ObAsyncTaskQueue',
                     'ObLinkQueueThreadPool')
POOL_PUSH_RE = re.compile(r'(?<![\w.>:~])(push)\s*\(')
PLACEMENT_NEW_RE = re.compile(r'\bnew\s*\((?:[^()]|\([^()]*\))*\)\s*([A-Za-z_][\w:]*)\s*(?:\(([^;]*))?')
OB_NEWX_RE = re.compile(r'\bOB_NEWx\s*\(\s*([A-Za-z_][\w:]*)\s*,\s*([^,;)]+)')
ALLOC_PARAM_NAMES = r'\w*(?:Allocator|Alloctor|Alloc|Arena|MemoryContext|MemContext)\w*|ObDataBuffer|PageArena'
RAW_BYTES_OUT_RE = re.compile(r'\b(?:char|uchar|uint8_t)\s*\*\s*&\s*[A-Za-z_]\w*\s*$')

_CTX = None
_NOT_BUILT = frozenset()


def repo_root_default():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))


def list_sources(repo):
    out = subprocess.run(['git', '-C', repo, 'ls-files', '-z', '--', 'src'], check=True, capture_output=True).stdout
    paths = []
    for p in out.decode('utf-8', 'surrogateescape').split('\0'):
        if not p.endswith(CODE_EXTS):
            continue
        dirs = p.split('/')[:-1]
        if any(d == 'deps' or d == 'rust' or d.startswith('build_') for d in dirs):
            continue
        paths.append(p)
    return sorted(paths)


def frozen_state(repo):
    r = subprocess.run(['git', '-C', repo, 'diff', '--quiet', FROZEN_REV, '--', 'src'], capture_output=True)
    return r.returncode == 0


def blank_literal(m):
    s = m.group(0)
    n = s.count('\n')
    if s[0] == '/':
        return ' ' + '\n' * n
    if s[0] == "'":
        return "' '"
    return '""' if not n else '"' + '\n' * n + '"'


def file_flag(path):
    if path in _NOT_BUILT:
        return 'not built'
    if path in DATA_PATHS:
        return 'data'
    if GENERATED_PATH_RE.search(path):
        return 'generated'
    if VENDORED_PATH_RE.search(path):
        return 'vendored'
    return ''


def split_top(s, sep=','):
    out = []
    depth = 0
    start = 0
    for i, c in enumerate(s):
        if c in '<([{':
            depth += 1
        elif c in '>)]}':
            depth -= 1
        elif c == sep and depth <= 0:
            out.append(s[start:i])
            start = i + 1
    out.append(s[start:])
    return out


def strip_template_args(name):
    prev = None
    while prev != name:
        prev = name
        name = re.sub(r'<[^<>]*>', '', name)
    return name.replace(' ', '')


def last_component(name):
    return strip_template_args(name).rsplit('::', 1)[-1]


def trailing_name(s):
    j = len(s)
    while j > 0 and s[j - 1].isspace():
        j -= 1
    parts = []
    while True:
        k = j
        while k > 0 and (s[k - 1].isalnum() or s[k - 1] == '_'):
            k -= 1
        if k == j:
            return None
        ident = s[k:j]
        if ident[0].isdigit():
            return None
        if k > 0 and s[k - 1] == '~':
            k -= 1
            ident = '~' + ident
        parts.append(ident)
        j = k
        t = j
        while t > 0 and s[t - 1].isspace():
            t -= 1
        if t >= 2 and s[t - 2:t] == '::':
            t -= 2
            while t > 0 and s[t - 1].isspace():
                t -= 1
            if t > 0 and s[t - 1] == '>':
                depth = 0
                while t > 0:
                    c = s[t - 1]
                    if c == '>':
                        depth += 1
                    elif c == '<':
                        depth -= 1
                        if depth == 0:
                            t -= 1
                            break
                    elif c in ';{}':
                        return None
                    t -= 1
                while t > 0 and s[t - 1].isspace():
                    t -= 1
            j = t
            if j == 0 or not (s[j - 1].isalnum() or s[j - 1] == '_'):
                break
            continue
        break
    return '::'.join(reversed(parts)), j


def function_signature(header):
    s = header
    if ')' not in s:
        return None
    for m in INIT_LIST_RE.finditer(s):
        end = m.start() + 1
        if s.count('(', 0, end) == s.count(')', 0, end):
            s = s[:end]
            break
    s = TRAILING_RETURN_RE.sub(')', s)
    s = TAIL_QUALIFIERS_RE.sub('', s).rstrip()
    if not s.endswith(')'):
        return None
    depth = 0
    open_at = -1
    for j in range(len(s) - 1, -1, -1):
        c = s[j]
        if c == ')':
            depth += 1
        elif c == '(':
            depth -= 1
            if depth == 0:
                open_at = j
                break
    if open_at <= 0:
        return None
    before = s[:open_at].rstrip()
    name = None
    start = None
    tail = before[-80:]
    if 'operator' in tail:
        m = OPERATOR_TAIL_RE.search(tail)
        if m:
            op = re.sub(r'\s+', ' ', m.group(1)).strip()
            op = op if re.match(r'[A-Za-z_]', op) is None else ' ' + op
            prefix = before[:len(before) - len(tail) + m.start()]
            qual = ''
            p = prefix.rstrip()
            start = len(p)
            if p.endswith('::'):
                q = trailing_name(p[:-2])
                if q:
                    qual = q[0] + '::'
                    start = q[1]
            name = qual + 'operator' + op
    if name is None:
        b = before
        if b.endswith('>'):
            depth = 0
            k = len(b)
            while k > 0:
                c = b[k - 1]
                if c == '>':
                    depth += 1
                elif c == '<':
                    depth -= 1
                    if depth == 0:
                        k -= 1
                        break
                elif c in ';{}()':
                    return None
                k -= 1
            b = b[:k].rstrip()
        t = trailing_name(b)
        if not t:
            return None
        name, start = t
    if last_component(name) in NOT_FUNCTION_NAMES:
        return None
    rettype = before[:start].strip()
    if rettype.endswith(('.', '->', '=', ',', '(', '&&', '||', '!', '?', ':')) and not rettype.endswith('::'):
        return None
    return name, s[open_at + 1:-1], rettype


def class_signature(h):
    m = CLASS_HEAD_RE.match(h)
    if not m:
        return None
    keyword, rest = m.group(1), m.group(2)
    rest = ATTRIBUTE_RE.sub('', rest.lstrip())
    mm = re.match(r'([A-Z][A-Z0-9_]+)\s+(?=[A-Za-z_])', rest)
    if mm and not re.match(r'final\b', rest[mm.end():]):
        rest = rest[mm.end():]
    if not rest.strip() or rest.lstrip().startswith(':') and not rest.lstrip().startswith('::'):
        tail = rest.lstrip()
        name = ''
    else:
        nm = CLASS_NAME_RE.match(rest)
        if not nm:
            return None
        name = strip_template_args(nm.group(1))
        tail = rest[nm.end():].strip()
    if '=' in tail or '(' in tail and not tail.startswith(':'):
        return None
    bases = []
    if tail:
        if not tail.startswith(':') or tail.startswith('::'):
            return None
        for b in split_top(tail[1:]):
            b = re.sub(r'\b(?:public|private|protected|virtual)\b', ' ', b).strip()
            if b:
                bases.append(re.sub(r'\s+', ' ', b))
    return keyword, name, bases


def classify(header, chain):
    h = ACCESS_RE.sub('', header).strip()
    if not h:
        return 'init', '', None
    if NAMESPACE_RE.match(h):
        return 'ns', '', None
    if EXTERN_BLOCK_RE.match(h):
        return 'ns', '', None
    while h.startswith('template'):
        i = h.find('<')
        if i < 0:
            break
        depth = 0
        j = i
        while j < len(h):
            c = h[j]
            if c == '<':
                depth += 1
            elif c == '>':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            break
        h = h[j + 1:].lstrip()
    if ENUM_RE.match(h):
        m = re.match(r'(?:typedef\s+)?enum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)?', h)
        return 'enum', (m.group(1) if m and m.group(1) else 'enum'), None
    f = function_signature(h)
    if f:
        name, args, rettype = f
        simple = last_component(name)
        if re.fullmatch(r'[A-Z][A-Z0-9_]*', simple):
            symbol = simple + '(' + re.sub(r'\s+', ' ', args).strip()[:60] + ')'
        else:
            symbol = strip_template_args(name)
        if chain and '::' not in symbol:
            symbol = chain + '::' + symbol
        return 'func', symbol, (h, simple, rettype, args)
    c = class_signature(h)
    if c:
        keyword, name, bases = c
        if not name:
            return 'class', chain, None
        return 'class', (chain + '::' + name) if chain else name, (keyword, last_component(name), bases, h)
    return 'init', '', None


def macro_prefix_end(stmt):
    i = 0
    n = len(stmt)
    while i < n:
        while i < n and (stmt[i].isspace() or stmt[i] == ')'):
            i += 1
        am = ACCESS_AT_RE.match(stmt, i)
        if am and am.end() > i:
            i = am.end()
            continue
        m = LEADING_MACRO_RE.match(stmt, i)
        if not m:
            break
        close = matching_close(stmt, m.end() - 1)
        if close < 0:
            if stmt[m.end():].strip():
                i = m.end()
                continue
            break
        if not stmt[close + 1:].strip():
            break
        i = close + 1
    return i


def template_params(header):
    out = set()
    k = 0
    h = ACCESS_RE.sub('', header).lstrip()
    while h.startswith('template', k):
        i = h.find('<', k)
        if i < 0:
            break
        depth = 0
        j = i
        while j < len(h):
            if h[j] == '<':
                depth += 1
            elif h[j] == '>':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            break
        for piece in split_top(h[i + 1:j]):
            m = re.search(r'([A-Za-z_]\w*)\s*(?:=.*)?$', piece.strip())
            if m and m.group(1) not in ('typename', 'class'):
                out.add(m.group(1))
        k = j + 1
        while k < len(h) and h[k].isspace():
            k += 1
    return frozenset(out)


def head_offset(header):
    h = header
    k = 0
    while True:
        m = ACCESS_AT_RE.match(h, k)
        if m and m.end() > k:
            k = m.end()
        while k < len(h) and h[k].isspace():
            k += 1
        if not h.startswith('template', k):
            return k
        i = h.find('<', k)
        if i < 0:
            return k
        depth = 0
        j = i
        while j < len(h):
            if h[j] == '<':
                depth += 1
            elif h[j] == '>':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            return k
        k = j + 1


def part_line(parts, pos):
    off = 0
    for ln, t in parts:
        if off + len(t) > pos and t[max(0, pos - off):].strip():
            return ln
        off += len(t) + 1
    return parts[0][0]


def statement_from_parts(parts):
    stmt = ' '.join(t for _, t in parts)
    start = macro_prefix_end(stmt)
    return stmt[start:], part_line(parts, start)


class Source:
    def __init__(self, path, text):
        self.path = path
        self.flag = file_flag(path)
        self.suffix = ' [' + self.flag + ']' if self.flag else ''
        self.raw = text.split('\n')
        self.code = LEX_RE.sub(blank_literal, text)
        self.lines = self.code.split('\n')
        self.starts = []
        pos = 0
        for line in self.lines:
            self.starts.append(pos)
            pos += len(line) + 1
        self.dkind = {}
        self.dname = {}
        n = len(self.lines)
        for m in DIRECTIVE_RE.finditer(self.code):
            ln = bisect.bisect_right(self.starts, m.start()) - 1
            word = m.group(1)
            if word in ('if', 'ifdef', 'ifndef'):
                kind = 'if'
            elif word in ('else', 'elif', 'elifdef', 'elifndef'):
                kind = 'else'
            elif word == 'endif':
                kind = 'endif'
            elif word == 'define':
                kind = 'define'
            else:
                kind = 'other'
            self.dkind[ln] = kind
            if kind == 'define':
                self.dname[ln] = m.group(2)
            j = ln
            while self.raw[j].rstrip().endswith('\\') and j + 1 < n:
                j += 1
                self.dkind[j] = 'cont'
                if kind == 'define':
                    self.dname[j] = m.group(2)
        self._scanned = False
        self._symbols = None

    def line_of(self, offset):
        return bisect.bisect_right(self.starts, offset) - 1

    def text(self, ln):
        return self.raw[ln].replace('\t', ' ').replace('\r', '').strip() if ln < len(self.raw) else ''

    def scan(self, want_decls=False):
        if self._scanned and (not want_decls or self.decls is not None):
            return
        stack = []
        saved = []
        parts = []
        classes = []
        funcs = []
        decls = [] if want_decls else None
        body_open = {}
        head_line = {}
        tparams = {}
        pending_type = None
        lines = self.lines
        dkind = self.dkind
        for i, line in enumerate(lines):
            k = dkind.get(i)
            if k is not None:
                if k == 'if':
                    saved.append(stack[:])
                elif k == 'else':
                    if saved:
                        stack = saved[-1][:]
                elif k == 'endif':
                    if saved:
                        saved.pop()
                continue
            if '{' not in line and '}' not in line and ';' not in line:
                if parts or line.strip():
                    parts.append((i, line))
                continue
            pos = 0
            for m in DELIM_RE.finditer(line):
                ch = m.group()
                seg = line[pos:m.start()]
                pos = m.end()
                if seg.strip():
                    parts.append((i, seg))
                top = stack[-1][0] if stack else 'ns'
                if ch == ';':
                    if want_decls and top in ('ns', 'class') and parts:
                        chain = stack[-1][1] if stack and top == 'class' else ''
                        stmt, first = statement_from_parts(parts)
                        if pending_type and DECLARATOR_LIST_RE.match(ACCESS_RE.sub('', stmt)):
                            stmt = pending_type + ' ' + ACCESS_RE.sub('', stmt).strip()
                        anon = any(e[0] == 'class' and e[4] is None for e in stack)
                        decls.append((top, chain, stmt, first) if not anon else (top, chain, stmt, first, 'anon'))
                    parts = []
                    pending_type = None
                elif ch == '{':
                    first = next((ln for ln, t in parts if ACCESS_RE.sub('', t).strip()), i)
                    pending_type = None
                    if top in ('func', 'block'):
                        stack.append(('block', '', i, first, None))
                    elif top in ('init', 'enum'):
                        stack.append(('init', '', i, first, None))
                    else:
                        chain = ''
                        for e in reversed(stack):
                            if e[0] == 'class':
                                chain = e[1]
                                break
                        if parts:
                            joined = ' '.join(t for _, t in parts)
                            start = macro_prefix_end(joined)
                            header, hfirst = joined[start:], part_line(parts, start)
                        else:
                            header, hfirst = '', i
                        kind, name, extra = classify(header, chain)
                        stack.append((kind, name, i, hfirst, extra))
                        if kind in ('func', 'class') and parts:
                            head_line[(name, hfirst)] = part_line(parts, start + head_offset(header))
                            tp = template_params(header)
                            if tp:
                                tparams[(name, hfirst)] = tp
                        if kind == 'func':
                            body_open[(name, hfirst)] = self.starts[i] + m.start()
                        elif kind == 'init' and want_decls and top in ('ns', 'class') and header.strip():
                            decls.append((top, stack[-2][1] if len(stack) > 1 and top == 'class' else '', header, hfirst))
                    parts = []
                else:
                    pending_type = None
                    if stack:
                        kind, name, ol, hl, extra = stack.pop()
                        if kind == 'func':
                            funcs.append((name, hl, ol, i, extra))
                        elif kind == 'class' and extra is not None:
                            classes.append((name, hl, ol, i, extra))
                        if kind == 'enum':
                            pending_type = name
                        elif kind == 'class':
                            pending_type = extra[1] if extra is not None and extra[1] else 'struct'
                    parts = []
            rest = line[pos:]
            if rest.strip():
                parts.append((i, rest))
        last = len(lines) - 1
        while stack:
            kind, name, ol, hl, extra = stack.pop()
            if kind == 'func':
                funcs.append((name, hl, ol, last, extra))
            elif kind == 'class' and extra is not None:
                classes.append((name, hl, ol, last, extra))
        funcs.sort(key=lambda f: (f[1], f[3]))
        self.classes = classes
        self.funcs = funcs
        self.decls = decls
        self.body_open = body_open
        self.head_line = head_line
        self.tparams = tparams
        self._scanned = True

    def template_params_at(self, ln):
        self.scan()
        out = set()
        for spans in (self.classes, self.funcs):
            for name, hl, ol, end, extra in spans:
                if hl <= ln <= end and (name, hl) in self.tparams:
                    out |= self.tparams[(name, hl)]
        return frozenset(out)

    def name_line(self, name, hl):
        self.scan()
        return self.head_line.get((name, hl), hl)

    def symbols(self):
        if self._symbols is None:
            self.scan()
            n = len(self.lines)
            sym = [''] * n
            for name, hl, ol, end, extra in sorted(self.classes, key=lambda c: c[1] - c[3]):
                for j in range(hl, min(end, n - 1) + 1):
                    sym[j] = name
            for name, hl, ol, end, extra in self.funcs:
                for j in range(hl, min(end, n - 1) + 1):
                    sym[j] = name
            for j, name in self.dname.items():
                sym[j] = '#define ' + name
            self._symbols = sym
        return self._symbols

    def symbol(self, ln):
        s = self.symbols()[ln]
        return s if s else '-'

    def function_spans(self):
        self.scan()
        return self.funcs

    def function_names(self):
        self.scan()
        if getattr(self, '_fnames', None) is None:
            self._fnames = frozenset(f[0] for f in self.funcs)
        return self._fnames

    def stem(self):
        return self.path.rsplit('.', 1)[0]


def read_source(repo, path):
    with open(os.path.join(repo, path), 'rb') as fh:
        text = fh.read().decode('utf-8', 'replace')
    return Source(path, text[1:] if text.startswith('\ufeff') else text)


def balanced_arg(code, open_paren):
    depth = 0
    i = open_paren
    n = len(code)
    start = open_paren + 1
    while i < n:
        c = code[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                return code[start:i], i
        elif c == ',' and depth == 1:
            return code[start:i], i
        i += 1
    return code[start:], n


def balanced_template(code, lt):
    depth = 0
    i = lt
    n = len(code)
    while i < n:
        c = code[i]
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
            if depth == 0:
                return code[lt + 1:i], i
        elif c in ';{}':
            break
        i += 1
    return None, lt


Access = collections.namedtuple(
    'Access', 'name qual chain self_ref op via path ln symbol cls stem text flag local root_type deref root_local tparams seps',
    defaults=(False, None, frozenset(), None))
ELEMENT_ACCESSORS = frozenset(('at', 'get', 'front', 'back', 'top', 'head', 'tail', 'val', 'value', 'ref'))
_LOCAL_DECL_CACHE = {}


def matching_close(s, i):
    pairs = {'(': ')', '[': ']', '{': '}'}
    opener = s[i]
    closer = pairs[opener]
    depth = 0
    for j in range(i, len(s)):
        c = s[j]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j
    return -1


def split_call_args(code, open_paren):
    body = balanced_call_args(code, open_paren)
    return [a.strip() for a in split_top(body)] if body.strip() else []


def strip_casts(a):
    a = CAST_KEYWORD_RE.sub('', a)
    a = C_POINTER_CAST_RE.sub('', a)
    return C_REF_CAST_RE.sub('', a)


def strip_outer(a):
    changed = True
    while changed:
        changed = False
        a = a.strip()
        while a[:1] in ('&', '*'):
            a = a[1:].strip()
            changed = True
        if a.startswith('(') and matching_close(a, 0) == len(a) - 1:
            a = a[1:-1]
            changed = True
    return a


def access_object(arg):
    a = re.sub(r'\s+', '', strip_casts(arg))
    if '#' in a:
        return None
    a = strip_outer(a)
    parts = split_top(a, '+')
    if len(parts) > 1:
        a = strip_outer(parts[0])
    m = re.match(r'(?:::)?((?:[A-Za-z_]\w*(?:<[^<>]*>)?::)*)([A-Za-z_]\w*)', a)
    if not m:
        return None
    qual = strip_template_args(m.group(1)).rstrip(':')
    comps = [['', m.group(2), None]]
    i = m.end()
    n = len(a)
    while i < n:
        c = a[i]
        if c in '[(':
            j = matching_close(a, i)
            if j < 0:
                break
            if c == '(':
                comps[-1][2] = a[i + 1:j]
            i = j + 1
            continue
        if a.startswith('->', i):
            sep = '->'
            i += 2
        elif c == '.':
            sep = '.'
            i += 1
        else:
            break
        mm = re.match(r'(?:template)?([A-Za-z_]\w*)', a[i:])
        if not mm:
            break
        comps.append([sep, mm.group(1), None])
        i += mm.end()
    if comps[0][1] == 'this' and len(comps) > 1:
        comps = comps[1:]
        comps[0][0] = ''
    last = comps[-1]
    if last[2] is not None:
        if len(comps) >= 2 and last[0] == '.':
            target = len(comps) - 2
        else:
            return (last[1] + '()', qual, tuple(c[1] for c in comps), False, tuple(c[0] for c in comps))
    else:
        target = len(comps) - 1
    seps = tuple(c[0] for c in comps[:target + 1])
    if comps[target][2] is not None:
        return (comps[target][1] + '()', qual, tuple(c[1] for c in comps[:target + 1]), False, seps)
    chain = tuple(c[1] for c in comps[:target + 1])
    return (comps[target][1], qual, chain, target == 0 and not qual, seps)


def local_decl_re(name):
    rx = _LOCAL_DECL_CACHE.get(name)
    if rx is None:
        rx = re.compile(LOCAL_DECL_TEMPLATE % re.escape(name))
        _LOCAL_DECL_CACHE[name] = rx
    return rx


def param_line(src, func, name):
    fname, hl = func[0], func[1]
    first = src.name_line(fname, hl)
    stop = src.body_open.get((fname, hl))
    if stop is None:
        return first
    m = re.compile(r'\b' + re.escape(name) + r'\s*(?:\[[^\]]*\]\s*)*(?=[,)=])').search(src.code, src.starts[first], stop)
    return src.line_of(m.start()) if m else first


def find_local(src, func, name, upto):
    fname, hl, ol, end, extra = func
    args = extra[3]
    for p in split_top(args):
        p2 = p.split('=', 1)[0].strip()
        mm = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', p2)
        if mm and mm.group(1) == name and p2[:mm.start(1)].strip():
            return ('param', re.sub(r'\s+', ' ', p2[:mm.start(1)]).strip(), param_line(src, func, name), '')
    start = src.body_open.get((fname, hl))
    if start is None or start >= upto:
        return None
    body = src.code[start:upto]
    if name not in body:
        return None
    found = None
    for m in local_decl_re(name).finditer(body):
        typ = m.group(1).strip()
        if typ.split()[0] in NOT_TYPE_WORDS or typ in NOT_TYPE_WORDS:
            continue
        found = m
    if found is None:
        rm = None
        for rm in re.finditer(RLOCAL_DECL_TEMPLATE % re.escape(name), body):
            pass
        if rm is None:
            return None
        decl_ln = src.line_of(start + rm.start())
        return ('static', 'thread-local ' + re.sub(r'\s+', ' ', rm.group(1)).strip(), decl_ln, '')
    decl_ln = src.line_of(start + found.start(2))
    typ = re.sub(r'\s+', ' ', found.group(1) + found.group(2)).strip()
    head = found.group(0)
    init = ''
    if found.group(4) == '=':
        rest = src.code[start + found.end():]
        k = 0
        depth = 0
        while k < len(rest):
            c = rest[k]
            if c in '([{':
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif c in ';,' and depth == 0:
                break
            k += 1
        init = rest[:k].strip()
    if re.search(r'\b(?:static|thread_local|__thread|RLOCAL_STATIC)\b', head):
        return ('static', typ, decl_ln, init)
    if ('&' in found.group(2) or '*' in found.group(2)) and init:
        return ('alias', typ, decl_ln, init)
    return ('local', typ, decl_ln, init)


def enclosing_function(src, ln):
    best = None
    for f in src.function_spans():
        if f[1] > ln:
            break
        if f[3] >= ln and (best is None or f[1] >= best[1]):
            best = f
    return best


def primitive_target_index(op):
    return 1 if op.startswith('LOAD128') else 0


def normalize_op(op):
    return re.sub(r'\s+', '', op).replace('common::', '')


def atomic_wrappers(defines):
    by_name = collections.defaultdict(list)
    for d in defines:
        if d[3] not in PRIMITIVE_HEADERS:
            by_name[d[0]].append(d)
    effects = collections.defaultdict(dict)
    macro_locals = []
    for name, ds in by_name.items():
        for d in ds:
            dname, params, body, path, start, flag, raw = d
            plist = list(params or ())
            for m in ATOMIC_CALL_RE.finditer(body):
                op = normalize_op(m.group(1))
                if op in NOT_ATOMIC_BUILTINS or (op.startswith('ATOMIC_') and op in by_name):
                    continue
                args = split_call_args(body, m.end() - 1)
                idx = primitive_target_index(op)
                if idx >= len(args):
                    continue
                if op in ('inc_update', 'dec_update') and not strip_casts(args[idx]).lstrip().startswith('&'):
                    continue
                obj = access_object(args[idx])
                if obj is None:
                    continue
                root = obj[2][0] if obj[2] else obj[0]
                if not obj[1] and root in plist:
                    k = plist.index(root)
                    if obj[0] == root:
                        effects[name][('param', k, op, '')] = None
                    else:
                        effects[name][('param-member', k, op, obj[0])] = None
                    continue
                if obj[3]:
                    decl = local_decl_re(obj[0]).search(body[:m.start()])
                    at = decl.start(2) if decl and decl.group(1).split()[0] not in NOT_TYPE_WORDS else None
                    if at is None:
                        rdecl = re.search(RLOCAL_DECL_TEMPLATE % re.escape(obj[0]), body[:m.start()])
                        at = rdecl.start() if rdecl else None
                    if at is not None:
                        k = body.count('\n', 0, at)
                        macro_locals.append((path, start + k, name, obj[0], op, flag, raw[k] if k < len(raw) else ''))
                        continue
                effects[name][('name', obj, op, '')] = None
    for _ in range(6):
        changed = False
        names = [n for n in effects if effects[n]]
        if not names:
            break
        rx = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(names)) + r')\b')
        for name, ds in by_name.items():
            for d in ds:
                dname, params, body, path, start, flag, raw = d
                plist = list(params or ())
                for m in rx.finditer(body):
                    inner = m.group(1)
                    if inner == name:
                        continue
                    rest = body[m.end():]
                    call = rest.lstrip().startswith('(')
                    args = split_call_args(body, m.end() + len(rest) - len(rest.lstrip())) if call else []
                    for eff in list(effects[inner]):
                        kind = eff[0]
                        if kind in ('param', 'param-member'):
                            if eff[1] >= len(args):
                                continue
                            obj = access_object(args[eff[1]])
                            if obj is None:
                                continue
                            root = obj[2][0] if obj[2] else obj[0]
                            if kind == 'param' and not obj[1] and root in plist and obj[0] == root:
                                key = ('param', plist.index(root), eff[2], eff[3] or inner)
                            elif kind == 'param-member' and not obj[1] and root in plist:
                                key = ('param-member', plist.index(root), eff[2], eff[3])
                            elif kind == 'param':
                                key = ('name', obj, eff[2], eff[3] or inner)
                            else:
                                key = ('name', (eff[3], '', (obj[0], eff[3]), False), eff[2], inner)
                        else:
                            key = ('name', eff[1], eff[2], eff[3] or inner)
                        if key not in effects[name]:
                            effects[name][key] = None
                            changed = True
        if not changed:
            break
    function_like = {}
    for name, ds in by_name.items():
        function_like[name] = any(d[1] is not None for d in ds)
    return {n: list(e) for n, e in effects.items() if e}, function_like, macro_locals


def atomic_access_records(src, ctx):
    out = []
    path = src.path
    code = src.code
    if path in PRIMITIVE_HEADERS:
        return out
    wrappers = ctx['atomic_wrappers']
    wrapper_re = ctx['atomic_wrapper_re']
    found = []
    if any(t in code for t in ATOMIC_TOKENS):
        for m in ATOMIC_CALL_RE.finditer(code):
            op = normalize_op(m.group(1))
            if op in NOT_ATOMIC_BUILTINS or op in wrappers:
                continue
            ln = src.line_of(m.start())
            if src.dkind.get(ln) in ('define', 'cont'):
                continue
            args = split_call_args(code, m.end() - 1)
            idx = primitive_target_index(op)
            if idx >= len(args):
                continue
            if op in ('inc_update', 'dec_update') and not strip_casts(args[idx]).lstrip().startswith('&'):
                continue
            obj = access_object(args[idx])
            if obj is not None:
                found.append((obj, op, '', ln, m.start(), not strip_casts(args[idx]).lstrip().startswith('&')))
    if wrapper_re is not None and wrapper_re.search(code):
        for m in wrapper_re.finditer(code):
            name = m.group(1)
            ln = src.line_of(m.start())
            if src.dkind.get(ln) in ('define', 'cont'):
                continue
            rest = code[m.end():]
            call = rest.lstrip().startswith('(')
            if ctx['atomic_function_like'].get(name) and not call:
                continue
            args = split_call_args(code, m.end() + len(rest) - len(rest.lstrip())) if call else []
            for kind, a, op, via in wrappers[name]:
                via_name = name if not via or via == name else name + ' > ' + via
                if kind == 'param':
                    if a >= len(args):
                        continue
                    obj = access_object(args[a])
                elif kind == 'param-member':
                    if a >= len(args):
                        continue
                    base = access_object(args[a])
                    obj = (via, '', ((base[0],) if base else ()) + (via,), False) if base else None
                    via_name = name
                else:
                    obj = a
                if obj is not None:
                    found.append((obj, op, via_name, ln, m.start(), False))
    if not found:
        return out
    return [make_access(src, obj, op, via, ln, off, deref) for obj, op, via, ln, off, deref in found]


def param_index(args, name):
    for k, p in enumerate(split_top(args)):
        mm = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', p.split('=', 1)[0].strip())
        if mm and mm.group(1) == name:
            return k
    return -1


def make_access(src, obj, op, via, ln, off, deref):
    name, qual, chain, self_ref = obj[:4]
    seps = obj[4] if len(obj) > 4 else None
    local = None
    root_local = None
    root_type = ''
    func = enclosing_function(src, ln)
    cls = enclosing_class(func[0]) if func is not None else enclosing_class_at(src, ln)
    if func is not None and chain and not qual:
        info = find_local(src, func, chain[0], off)
        if info is not None:
            root_local = (info[0], func[0], info[1], info[2], src.text(info[2]), info[3],
                          param_index(func[4][3], chain[0]) if info[0] == 'param' else -1)
            if self_ref:
                if info[0] == 'alias':
                    init = info[3].lstrip()
                    target = access_object(init)
                    if target is not None and target[0] not in LITERAL_OPERANDS:
                        local = ('alias', chain[0], func[0])
                        name, qual, chain, self_ref = target[:4]
                        seps = target[4] if len(target) > 4 else None
                        deref = deref and '*' in info[1] and not init.startswith('&')
                    else:
                        local = ('local', func[0], info[1], info[2], src.text(info[2]))
                else:
                    local = (info[0], func[0], info[1], info[2], src.text(info[2]))
            else:
                root_type = info[1]
    return Access(name, qual, tuple(chain), self_ref, op, via, src.path, ln + 1, src.symbol(ln), cls, src.stem(), src.text(ln),
                  src.flag, local, root_type, deref, root_local, src.template_params_at(ln), seps)


def enclosing_class_at(src, ln):
    best = None
    for name, hl, ol, end, extra in src.classes:
        if hl <= ln <= end and (best is None or hl >= best[0]):
            best = (hl, extra[1])
    return best[1] if best else ''


def normalize_type(t):
    t = re.sub(r'\s+', '', t)
    t = re.sub(r'(?<![\w>])(?:::)?(?:[a-z_][a-z0-9_]*::)+', '', t)
    return t


def service_tokens(src):
    out = []
    code = src.code
    if 'server_service' not in code and 'server_obj' not in code and 'SERVICE' not in code \
            and 'ObServerServiceSlot' not in code and 'server_object' not in code:
        return out
    for m in SERVICE_TOKEN_RE.finditer(code):
        body, _ = balanced_template(code, m.end() - 1)
        if body is None:
            continue
        typ = normalize_type(body)
        if typ in ('Service', 'T', 'Type', 'type', 'service') or not typ or re.search(r'<(?:T|Service)>', typ):
            continue
        fn = m.group(1)
        raw = re.sub(r'\s+', '', body)
        if fn in ('server_obj_pool', 'borrow_server_object', 'return_server_object'):
            typ = 'ObServerObjectPool<' + typ + '>'
            kind = 'pool'
        elif fn == 'server_service':
            kind = 'use'
        elif fn == 'bind_server_service':
            kind = 'bind'
        elif fn == 'unbind_server_service':
            kind = 'unbind'
        else:
            kind = 'slot'
        out.append((typ, raw, kind, src.line_of(m.start())))
    for m in SERVICE_MACRO_RE.finditer(code):
        ln = src.line_of(m.start())
        if src.dkind.get(ln) in ('define', 'cont'):
            continue
        whole = balanced_call_args(code, m.end() - 1)
        pieces = split_top(whole)
        typ = normalize_type(pieces[-1]) if pieces else ''
        if typ and typ not in ('type', 'Type', 'T', 'Service') and not re.search(r'<(?:T|Service)>', typ):
            out.append((typ, re.sub(r'\s+', '', pieces[-1]), 'bind' if m.group(1) == 'BIND_SERVICE' else 'unbind', ln))
    return out


def balanced_call_args(code, open_paren):
    depth = 0
    i = open_paren
    n = len(code)
    while i < n:
        c = code[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return code[open_paren + 1:i]
        elif c in ';{}':
            break
        i += 1
    return code[open_paren + 1:i]


def strip_trailing_attribute(head):
    while True:
        m = TRAILING_ATTRIBUTE_RE.search(head)
        if not m or LOWER_TYPE_RE.fullmatch(m.group(1)):
            return head
        head = head[:m.start(2)].rstrip()


def parse_statement(stmt):
    s = ACCESS_RE.sub('', stmt).strip()
    if not s or DECL_SKIP_RE.match(s) or MACRO_STMT_RE.match(s):
        return None
    pieces = split_top(s)
    first = pieces[0]
    eq = -1
    depth = 0
    for i, c in enumerate(first):
        if c in '<([{':
            depth += 1
        elif c in '>)]}':
            depth -= 1
        elif c == '=' and depth <= 0:
            eq = i
            break
    if re.search(r'\boperator\b', first):
        f = function_signature(first.split('=', 1)[0] if re.search(r'\)\s*=\s*(?:0|default|delete)\s*$', first) else first)
        return ('func', last_component(f[0]), f[2]) if f else None
    head = first if eq < 0 else first[:eq]
    if '(' in head:
        f = function_signature(head)
        if f:
            return ('func', last_component(f[0]), f[2])
        return None
    head = head.split('{', 1)[0]
    head = re.sub(r'\[[^\]]*\]', ' ', head)
    head = re.sub(r'(?<!:):(?!:)\s*\w+\s*$', '', head).rstrip()
    head = strip_trailing_attribute(head)
    m = re.search(r'((?:[A-Za-z_]\w*\s*::\s*)*)([A-Za-z_]\w*)$', head)
    if not m:
        return None
    typ = re.sub(r'\s+', ' ', head[:m.start()]).strip()
    if not typ or typ.split(' ')[0] in NON_TYPE_WORDS or not re.search(r'[\w>*&]$', typ):
        return None
    qual = re.sub(r'\s+', '', m.group(1)).rstrip(':')
    names = [m.group(2)]
    for p in pieces[1:]:
        p = p.split('=', 1)[0].split('{', 1)[0]
        p = re.sub(r'\[[^\]]*\]', ' ', p)
        p = re.sub(r'(?<!:):(?!:)\s*\w+\s*$', '', p)
        p = strip_trailing_attribute(p)
        mm = re.search(r'([A-Za-z_]\w*)\s*$', p)
        if mm:
            names.append(mm.group(1))
    return ('var', typ, names, qual)


def member_type_tags(typ):
    tags = set()
    base = re.sub(r'\b(?:static|mutable|volatile|constexpr|inline|thread_local|extern)\b', ' ', typ)
    base = re.sub(r'\s+', ' ', base).strip()
    if VIEW_TYPE_RE.search(base) or RAW_BYTES_RE.match(base):
        tags.add('view')
    for m in ALLOCATOR_TYPE_RE.finditer(base):
        if m.group(1) not in NOT_ALLOCATOR_TYPES and not m.group(1).startswith(('get_', 'is_')):
            tags.add('allocator')
    if HASH_ANY_RE.search(base):
        tags.add('hash')
    if IR_CANDIDATE_RE.search(base):
        tags.add('ir')
    if FLOAT_TYPE_RE.search(' ' + base):
        tags.add('float')
    if re.search(r'\bvolatile\b', typ):
        tags.add('volatile')
    return tags


def define_bodies(src):
    groups = collections.OrderedDict()
    for ln in sorted(src.dname):
        if src.dkind.get(ln) == 'define':
            groups[ln] = [ln]
        elif groups:
            last = next(reversed(groups))
            groups[last].append(ln)
    for start, lns in groups.items():
        head = re.match(r'\s*#\s*define\s+(\w+)(?:\(([^)]*)\))?', src.lines[start])
        if not head:
            continue
        params = [x.strip() for x in head.group(2).split(',')] if head.group(2) is not None else []
        yield head.group(1), params, lns


def macro_subclasses(src):
    out = []
    if '#' not in src.code or not any(b in src.code for b in SUBCLASS_BASES):
        return out
    for name, params, lns in define_bodies(src):
        text = '\n'.join(src.lines[k].rstrip().rstrip('\\') for k in lns)
        if not any(b in text for b in SUBCLASS_BASES):
            continue
        for m in MACRO_CLASS_RE.finditer(text):
            for b in split_top(m.group(3)):
                b = re.sub(r'\b(?:public|private|protected|virtual)\b', ' ', b).strip()
                lb = last_component(b)
                if lb in SUBCLASS_BASES:
                    ln = lns[0] + text.count('\n', 0, m.start())
                    index = params.index(m.group(2)) if m.group(2) in params else -1
                    out.append((name, index, m.group(2), lb, src.path, ln + 1, src.text(ln), src.flag))
    return out


def include_list(src):
    return [m.group(1) for m in INCLUDE_RE.finditer('\n'.join(src.raw))]


def refcount_kind(name):
    if re.fullmatch(r'_?(?:inc|dec)_ref\w*', name):
        return 'plain'
    if re.fullmatch(r'\w+_(?:inc|dec)_ref\w*', name):
        return 'prefixed'
    tokens = [t for t in name.strip('_').split('_') if t]
    if any(t in STRONG_REF_WORDS for t in tokens):
        return 'other'
    verb_at = next((k for k, t in enumerate(tokens) if t in REFCOUNT_VERBS), None)
    if verb_at is None:
        return None
    if not any(t in REFCOUNT_TOKENS for t in tokens[verb_at + 1:]):
        return None
    if any(t in REFCOUNT_EXCLUDED_TOKENS for t in tokens):
        return None
    return 'other'


def refcount_calls_in(text):
    return set(m.group(1) for m in REFCOUNT_CALL_RE.finditer(text) if refcount_kind(m.group(1)))


ACCESSOR_RETURN_RE = re.compile(r'\breturn\s+([^;]+);')


def accessor_record(src, name, hl, end, extra):
    h, simple, rettype, args = extra
    rt = rettype.strip()
    if rt.endswith('&') and not const_pointee(rt):
        kind = 'ref'
    elif rt.endswith('*'):
        kind = 'ptr'
    else:
        return None
    open_off = src.body_open.get((name, hl))
    if open_off is None:
        return None
    body = src.code[open_off:src.starts[end] + len(src.lines[end])]
    if body.count(';') > 4:
        return None
    returns = ACCESSOR_RETURN_RE.findall(body)
    if len(returns) != 1:
        return None
    expr = returns[0].strip()
    if kind == 'ptr' and not expr.startswith('&'):
        return None
    obj = access_object(expr)
    if obj is None or obj[0].endswith('()') or obj[0] in LITERAL_OPERANDS:
        return None
    root_type = ''
    if obj[2] and not obj[1] and not obj[3]:
        info = find_local(src, (name, hl, None, end, extra), obj[2][0], open_off + body.rfind('return'))
        if info:
            root_type = info[1]
    return (simple, enclosing_class(name), src.path, src.stem(), obj, root_type, src.template_params_at(hl), kind)


def refcount_candidate(name):
    tokens = [t for t in name.strip('_').split('_') if t]
    if not tokens or refcount_kind(name) is not None or ref_first(name):
        return False
    return tokens[0] in LIFECYCLE_VERBS or any(t in REF_WORD_TOKENS for t in tokens)


def ref_first(name):
    tokens = [t for t in name.strip('_').split('_') if t]
    return len(tokens) >= 2 and tokens[0] == 'ref' and refcount_kind(name) is None


def member_like(expr):
    return '.' in expr or '->' in expr or expr.rstrip(']').split('[')[0].endswith('_')


def refcount_changes(text):
    out = []
    if 'ATOMIC_' in text or '__sync_' in text:
        for m in ATOMIC_CHANGE_RE.finditer(text):
            target = m.group(2)
            last = re.sub(r'\[[^\]]*\]', '', re.split(r'\.|->', target)[-1])
            if member_like(target) and any(t in REF_WORD_TOKENS for t in last.strip('_').split('_')):
                out.append((m.start(), re.sub(r'\s+', '', m.group(1)), last))
    if '++' in text or '--' in text or '+=' in text or '-=' in text:
        for m in PLAIN_CHANGE_RE.finditer(text):
            targets = []
            bm = re.search(r'([A-Za-z_][\w.\->\[\]]*)\s*$', text[max(0, m.start() - 80):m.start()])
            if bm:
                targets.append(bm.group(1))
            if m.group() in ('++', '--'):
                am = re.match(r'\s*([A-Za-z_][\w.\->\[\]]*)', text[m.end():m.end() + 80])
                if am:
                    targets.append(am.group(1))
            for target in targets:
                last = re.sub(r'\[[^\]]*\]', '', re.split(r'\.|->', target)[-1])
                if member_like(target) and PLAIN_REF_FIELD_RE.search(last):
                    out.append((m.start(), m.group(), last))
                    break
    return out


def pass_a(repo, paths):
    classes = []
    members = []
    services = []
    aliases = []
    methods = []
    macros = []
    defines = []
    includes = {}
    guards = collections.defaultdict(set)
    refcount_defs = []
    spellings = set()
    handle_raw = [0, 0]
    member_names = collections.Counter()
    method_names = collections.Counter()
    const_ret_names = collections.Counter()
    typed_rets = []
    heap_methods = collections.defaultdict(set)
    func_index = []
    func_defs = collections.Counter()
    refcount_candidates = []
    ref_first_decls = collections.defaultdict(set)
    class_tparams = {}
    accessors = []
    for path in paths:
        src = read_source(repo, path)
        src.scan(want_decls=True)
        stem = src.stem()
        macros += macro_subclasses(src)
        includes[path] = include_list(src)
        if 'server_service' in src.code:
            for line in src.raw:
                if 'server_service<' in line:
                    spellings.update(re.findall(r'server_service<[^>]+>', line))
        if 'Handle' in src.code:
            for line in src.raw:
                if re.search(r'class \w+Handle\b', line):
                    handle_raw[0] += 1
                    if re.search(r'\bclass\s+\w+Handle\s*(?:<[^;{}]*>)?\s*;', line):
                        handle_raw[1] += 1
        for name, params, lns in define_bodies(src):
            head = re.match(r'\s*#\s*define\s+\w+(\()?', src.lines[lns[0]])
            body = '\n'.join(src.lines[k].rstrip().rstrip('\\') for k in lns)
            defines.append((name, tuple(params) if head and head.group(1) else None, body, path, lns[0], src.flag,
                            tuple(src.text(k) for k in lns)))
        for name, hl, ol, end, extra in src.classes:
            keyword, simple, bases, header = extra
            nl = src.name_line(name, hl)
            classes.append((name, simple, keyword, path, nl + 1, bases, src.text(nl), src.flag))
            if (name, hl) in src.tparams:
                class_tparams[strip_template_args(name)] = src.tparams[(name, hl)]
        for decl in src.decls:
            scope, chain, stmt, ln = decl[:4]
            anon = len(decl) > 4
            p = parse_statement(stmt)
            if not p:
                continue
            if p[0] == 'func':
                method_names[p[1]] += 1
                if ref_first(p[1]):
                    ref_first_decls[p[1]].add(bool(re.search(r'\(\s*\)\s*const\b', stmt)))
                if const_pointee(p[2]) and re.search(r'[*&]\s*$', p[2]):
                    const_ret_names[p[1]] += 1
                if CONTAINER_RET_RE.search(p[2]) or re.search(r'\b[A-Z][A-Z0-9_]*\s*[&*]', p[2]):
                    typed_rets.append((p[1], p[2], chain if scope == 'class' else '', path))
                if chain and HEAP_NAME_RE.search(last_component(chain)):
                    heap_methods[last_component(chain)].add(p[1])
                if '*' in p[2] and IR_CANDIDATE_RE.search(p[2]):
                    methods.append((p[1], p[2]))
                continue
            _, typ, names, qual = p
            if typ.startswith(('typedef', 'using')) or re.search(r'\btypedef\b', typ):
                continue
            if scope == 'class':
                member_names.update(names)
            tags = member_type_tags(typ)
            if anon:
                tags.add('anon')
            owner = chain if scope == 'class' else qual
            for nm in names:
                members.append((owner, nm, typ, path, ln + 1, scope, tuple(sorted(tags)), src.text(ln), stem, src.flag))
        for decl in src.decls:
            scope, chain, stmt, ln = decl[:4]
            s = ACCESS_RE.sub('', stmt).strip()
            m = re.match(r'using\s+([A-Za-z_]\w*)\s*=\s*(?:typename\s+)?(.+)$', s, re.S)
            if m:
                aliases.append((m.group(1), re.sub(r'\s+', ' ', m.group(2)).strip(), path, ln + 1, src.text(ln), src.flag, chain))
                continue
            if s.startswith('typedef'):
                m = re.match(r'typedef\s+(?:typename\s+)?(.+?)\s*\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', s, re.S)
                if m and '(' not in m.group(1):
                    aliases.append((m.group(2), re.sub(r'\s+', ' ', m.group(1)).strip(), path, ln + 1, src.text(ln), src.flag, chain))
        for name, hl, ol, end, extra in src.funcs:
            h, simple, rettype, args = extra
            method_names[simple] += 1
            if const_pointee(rettype) and re.search(r'[*&]\s*$', rettype):
                const_ret_names[simple] += 1
            if CONTAINER_RET_RE.search(rettype) or re.search(r'\b[A-Z][A-Z0-9_]*\s*[&*]', rettype):
                typed_rets.append((simple, rettype, name.rsplit('::', 1)[0] if '::' in name else '', path))
            if HEAP_NAME_RE.search(enclosing_class(name)):
                heap_methods[enclosing_class(name)].add(simple)
            if '*' in rettype and IR_CANDIDATE_RE.search(rettype):
                methods.append((simple, rettype))
            cls = enclosing_class(name)
            func_index.append((cls, simple, path, hl, end))
            if re.search(r'[&*]\s*$', rettype) and 'return' in src.lines[end] + ''.join(src.lines[hl:end]):
                rec = accessor_record(src, name, hl, end, extra)
                if rec is not None:
                    accessors.append(rec)
            func_defs[simple] += 1
            if ref_first(simple):
                ref_first_decls[simple].add(not args.strip() and const_function_header(h))
            want_guard = GUARD_CLASS_RE.search(cls) is not None
            want_def = refcount_kind(simple) is not None
            candidate = refcount_candidate(simple)
            if want_guard or want_def or candidate:
                body = '\n'.join(src.lines[hl:end + 1])
                inner = body[body.find('{') + 1:] if '{' in body else body
                calls = refcount_calls_in(inner)
                if want_guard and calls:
                    guards[cls].update(calls)
                if want_def:
                    refcount_defs.append((simple, frozenset(calls - {simple}), DISK_REF_SEED_RE.search(body) is not None,
                                          DISK_REF_PARAM_RE.search(args) is not None))
                if candidate:
                    named = set(m.group(3) for m in IDENT_CALL_RE.finditer(inner)
                                if refcount_kind(m.group(3)) or refcount_candidate(m.group(3)))
                    nl = src.name_line(name, hl)
                    refcount_candidates.append((simple, frozenset(named - {simple}), bool(refcount_changes(inner)), path,
                                                nl + 1, name, src.text(nl), src.flag))
        for typ, raw, kind, ln in service_tokens(src):
            macro = src.dname.get(ln, '') if src.dkind.get(ln) in ('define', 'cont') else ''
            services.append((typ, raw, kind, path, ln + 1, src.text(ln), src.flag, macro, src.symbol(ln)))
    return (classes, members, services, aliases, methods, macros, includes, member_names, method_names, defines,
            {k: frozenset(v) for k, v in guards.items()}, refcount_defs, spellings, tuple(handle_raw), const_ret_names,
            typed_rets, dict(heap_methods), func_index, func_defs, refcount_candidates, dict(ref_first_decls), class_tparams,
            accessors)


def init_worker(ctx):
    global _CTX
    _CTX = ctx
    set_not_built(ctx['not_built'])
    hash_names = list(HASH_TYPE_NAMES) + sorted(ctx['hash_aliases'])
    specific = sorted(ctx['refcount_specific'])
    ctx['refcount_specific_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in specific) + r')\s*\(') if specific else None
    ctx['hash_member_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(ctx['hash_member_names'])) + r')\b') \
        if ctx['hash_member_names'] else None
    ctx['heap_member_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(ctx['heap_member_names'])) + r')\b') \
        if ctx['heap_member_names'] else None
    heaps = sorted(ctx['heap_types'])
    ctx['heap_name_re'] = re.compile(r'\b(?:' + '|'.join(re.escape(n) for n in heaps) + r')\b') if heaps else None
    ctx['heap_decl_re'] = re.compile(
        r'\b(' + '|'.join(re.escape(n) for n in heaps) + r')\b\s*(' + TEMPLATE_ARGS
        + r')?\s*[*&]*\s*(?:const\s+)?([A-Za-z_]\w*)\s*[;,)=({\[]') if heaps else None
    ctx['hash_iter_re'] = re.compile(
        r'\b(' + '|'.join(re.escape(n) for n in hash_names) + '|' + HASH_TYPE_GENERIC + r')\b\s*(?:' + TEMPLATE_ARGS
        + r')?\s*::\s*(?:const_)?iterator\b')
    ctx['hash_getter_re'] = re.compile(
        r'\b(' + '|'.join(re.escape(n) for n in sorted(ctx['hash_getters'])) + r')\s*\(\s*\)\s*(?:\.|->)\s*'
        + ITERATE_METHODS + r'\s*\(') if ctx['hash_getters'] else None
    ctx['hash_decl_re'] = re.compile(
        r'\b(' + '|'.join(re.escape(n) for n in hash_names) + '|' + HASH_TYPE_GENERIC + r')\b\s*(' + TEMPLATE_ARGS
        + r')?\s*[*&]*\s*(?:const\s+)?([A-Za-z_]\w*)\s*[;,)=({\[]')
    ctx['pointer_key_re'] = re.compile(
        r'\b(' + '|'.join(re.escape(n) for n in hash_names) + '|' + HASH_TYPE_GENERIC
        + r'|std\s*::\s*(?:map|set|multimap|multiset|unordered_map|unordered_set))\s*<\s*(?:const\s+)?[\w:]+(?:\s*<[^<>]*>)?\s*\*')
    ctx['budget_seeds'] = [(label, plan, re.compile(p) if p else None, re.compile(w)) for label, plan, p, w in BUDGET_SEEDS]
    ctx['logical_cases'] = [(label, re.compile(p), re.compile(w)) for label, p, w in PLAN_LOGICAL_CASES]
    codes = r'(' + '|'.join(re.escape(c) for c in ctx['budget_codes']) + r')'
    ctx['budget_compare_re'] = re.compile(
        r'(?:==|!=)\s*' + QUAL + codes + r'\b|\b' + codes + r'\s*(?:==|!=)|\bcase\s+' + QUAL + codes + r'\s*:(?!:)')
    ctx['budget_error_re'] = re.compile(
        r'(?:(?<![=!<>])=\s*|\breturn\s+|(?<!:)[?:](?!:)\s*)' + QUAL + codes + r'\b(?!\s*(?:==|!=))')
    wrappers = sorted(ctx['atomic_wrappers'])
    ctx['atomic_wrapper_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in wrappers) + r')\b') if wrappers else None
    views = sorted(ctx['view_types'])
    ctx['view_type_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in views) + r')\b')
    ctx['view_ref_param_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in views) + r')\s*(?:\*\s*)?&\s*[A-Za-z_]\w*\s*$')
    extra = sorted(ctx['allocator_types'])
    ctx['alloc_param_re'] = re.compile(r'\b(?:' + ALLOC_PARAM_NAMES + ('|' + '|'.join(re.escape(n) for n in extra) if extra else '')
                                       + r')\b')
    counted = sorted(ctx['count_macros'])
    ctx['count_macro_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in counted) + r')\b') if counted else None
    init_new_lists(ctx)


def enclosing_class(symbol):
    if not symbol or symbol == '-' or symbol.startswith('#define'):
        return ''
    parts = symbol.split('::')
    return parts[-2] if len(parts) >= 2 else ''


def call_kind(src, i, name, prefix, paren_off):
    inside = False
    for fname, hl, ol, fend, extra in src.function_spans():
        if hl > i:
            break
        if fend < i:
            continue
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        if paren_off > open_off:
            inside = True
        elif last_component(fname) == name:
            return 'definition'
        elif INIT_LIST_RE.search(src.code, src.starts[hl], paren_off) and re.search(
                r'(?:\)\s*:|[,}]|\)\s*,)\s*$', src.code[src.starts[hl]:src.code.rfind(name, 0, paren_off)]):
            return 'initializer'
    if inside:
        return 'call'
    close = matching_close(src.code, paren_off)
    after = src.code[close + 1:close + 300] if close >= 0 else ''
    t = DECL_TAIL_RE.match(after)
    words = prefix.split()
    typed = DEFINITION_PREFIX_RE.fullmatch(prefix) is not None and bool(words) and words[0] not in NON_TYPE_WORDS
    bare = not prefix.strip()
    in_macro = src.dkind.get(i) in ('define', 'cont')
    if t:
        if t.group(1) == ';' and (typed or bare and not in_macro):
            return 'declaration'
        if t.group(1) in ('{', ':') and (typed or bare):
            return 'definition'
    return 'call'


def body_call_kind(src, i, name, prefix, paren_off):
    ck = call_kind(src, i, name, prefix, paren_off)
    if ck != 'declaration' or prefix.strip() or src.dkind.get(i) in ('define', 'cont'):
        return ck
    if src.path.endswith(('.y', '.l')):
        return 'call'
    code = src.code
    opener = block_opener(code, paren_off)
    if opener < 0:
        return ck
    hs, header = header_before(code, opener)
    h = re.sub(r'^[ \t]*#.*$', ' ', header, flags=re.M).strip()
    if re.search(r'\)\s*(?:(?:const|volatile|noexcept|override|final|mutable|__THROW|[A-Z_][A-Z0-9_]*(?:\s*\([^()]*\))?)\s*)*$', h) \
            and not re.match(r'(?:(?:typedef|template\s*<[^>]*>)\s+)?(?:class|struct|union|namespace|enum)\b', h) \
            or re.fullmatch(r'(?:else|do|try)', h):
        return 'call'
    return ck


def refcount_rows(src, ctx):
    out = []
    code = src.code
    confirmed = ctx['refcount_confirmed']
    ref_names = ctx['ref_first_names']
    specific_re = ctx['refcount_specific_re']
    if specific_re is not None and not specific_re.search(code):
        specific_re = None
    disk_names = ctx['disk_ref_names']
    for i, line in enumerate(src.lines):
        if '(' not in line:
            continue
        found = []
        if 'ref' in line:
            for m in REFCOUNT_CALL_RE.finditer(line):
                fname = m.group(1)
                kind = refcount_kind(fname) or ('other' if fname in ref_names else None)
                if kind is None:
                    continue
                paren_off = src.starts[i] + m.end() - 1
                ck = call_kind(src, i, fname, line[:m.start()], paren_off)
                if ck == 'initializer':
                    continue
                label = fname + ' ' + ck
                if kind == 'prefixed':
                    label += ' (prefixed name)'
                elif kind == 'other':
                    label += ' (other name)'
                tokens = set(fname.strip('_').split('_'))
                disk = bool(tokens & DISK_REF_TOKENS) or fname in disk_names
                if not disk and DISK_REF_SEED_RE.search(line[max(0, m.start() - 80):m.end()]):
                    disk = True
                if not disk and ck != 'call' and DISK_REF_PARAM_RE.search(balanced_call_args(code, paren_off)):
                    disk = True
                if disk:
                    label += ' [on-disk block reference]'
                found.append(label)
        if specific_re is not None:
            for m in specific_re.finditer(line):
                fname = m.group(1)
                ck = call_kind(src, i, fname, line[:m.start()], src.starts[i] + m.end() - 1)
                if ck in ('call', 'declaration'):
                    found.append(fname + ' ' + ck + ' (reference-count function found by its body)')
        if found:
            out.append((i, '; '.join(found)))
    if '++' in code or '--' in code or '+=' in code or '-=' in code or 'ATOMIC_' in code or '__sync_' in code:
        for fname, hl, ol, end, extra in src.function_spans():
            simple = extra[1]
            if refcount_kind(simple) is not None or simple in confirmed:
                continue
            open_off = src.body_open.get((fname, hl))
            if open_off is None:
                continue
            body_end = src.starts[end] + len(src.lines[end])
            for off, op, field in refcount_changes(code[open_off:body_end]):
                out.append((src.line_of(open_off + off), 'reference-count change: %s on %s' % (op, field)))
    return out


def confirm_refcount_candidates(candidates, func_defs, ref_names):
    confirmed_defs = set()
    names = set()
    for c in candidates:
        if c[2] or any(refcount_kind(x) is not None or x in ref_names for x in c[1]):
            confirmed_defs.add((c[3], c[4]))
            names.add(c[0])
    rows = []
    for c in candidates:
        if (c[3], c[4]) in confirmed_defs:
            simple, calls, changes, path, ln, symbol, text, flag = c
            how = 'changes a reference-count field' if changes else 'calls ' + ', '.join(
                sorted(x for x in calls if refcount_kind(x) is not None or x in ref_names)[:3])
            rows.append((path, ln, symbol, '%s definition (reference-count function found by its body: %s)%s'
                         % (simple, how, flag_suffix(flag)), text))
    counts = collections.Counter(c[0] for c in candidates if (c[3], c[4]) in confirmed_defs)
    specific = set(n for n, k in counts.items() if k >= 0.8 * max(1, func_defs.get(n, 0)))
    keys = frozenset((enclosing_class(c[5]), c[0]) for c in candidates if (c[3], c[4]) in confirmed_defs)
    return rows, names, specific, keys


def drop_template_args(t):
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r'<[^<>]*>', ' ', t)
    return t


def const_pointee(typ):
    t = re.sub(r'\b(?:static|mutable|inline|constexpr)\b', ' ', drop_template_args(typ.replace('<<', ' '))).strip()
    if '*' in t:
        return re.search(r'\bconst\b', t.split('*')[0]) is not None
    if '&' in t:
        return re.search(r'\bconst\b', t.split('&')[0]) is not None
    return False


def const_function_header(h):
    for m in INIT_LIST_RE.finditer(h):
        end = m.start() + 1
        if h.count('(', 0, end) == h.count(')', 0, end):
            h = h[:end]
            break
    return CONST_METHOD_RE.search(h) is not None


def cast_operand(line, m):
    rest = line[line.index(')', m.end(2)) + 1:]
    om = OPERAND_START_RE.match(rest)
    return om.group(1).strip() if om else ''


def const_cast_rows(src, ctx):
    out = {}
    code = src.code
    if 'const_cast' in code or 'const_pointer_cast' in code:
        for i, line in enumerate(src.lines):
            if 'const_cast' not in line and 'const_pointer_cast' not in line:
                continue
            types = []
            for m in CONST_CAST_RE.finditer(line):
                body, _ = balanced_template(line, m.end() - 1)
                if body is None:
                    off = src.starts[i] + m.end() - 1
                    body, _ = balanced_template(code, off)
                kw = 'const_pointer_cast' if 'pointer' in m.group() else 'const_cast'
                target = re.sub(r'\s+', ' ', body or '?').strip()
                types.append(kw + '<' + target + '>' + (' (adds const only)' if re.match(r'const\b', target) else ''))
            if types:
                out[i] = types
    if '(' not in code or ('*' not in code and '&' not in code):
        return sorted((i, '; '.join(v)) for i, v in out.items())
    const_members = ctx['const_members']
    const_returns = ctx['const_returns']
    stem_members = ctx['const_members_stem'].get(src.stem(), frozenset())
    spans = [(f[1], f[3], f) for f in src.function_spans()]
    spans += [(lns[0], lns[-1], name) for name, _, lns in define_bodies(src)]
    for hl, end, owner in spans:
        if isinstance(owner, tuple):
            fname = owner[0]
            members = class_chain_lookup(const_members, fname)
            const_fn = const_function_header(owner[4][0])
            where = ''
        else:
            fname = None
            members = stem_members
            const_fn = False
            where = ' (inside #define %s)' % owner
        for i in range(hl, end + 1):
            line = src.lines[i]
            if '(' not in line or ('*' not in line and '&' not in line):
                continue
            for m in C_CONST_DROP_RE.finditer(line):
                name = m.group(4)
                cast = re.sub(r'\s+', ' ', line[m.start() + 1:line.index(')', m.end(2))]).strip()
                label = None
                if name == 'this':
                    if const_fn:
                        label = 'C-style cast (%s) drops const from this (const member function)' % cast
                elif name in LITERAL_OPERANDS or name in TYPE_WORDS:
                    continue
                else:
                    lo = max(hl, i - 60)
                    window = '\n'.join(src.lines[lo:i]) + '\n' + line[:m.start()]
                    operand = cast_operand(line, m)
                    call = re.search(r'([A-Za-z_]\w*)\s*\([^()]*\)$', operand)
                    if name in window and re.search(CONST_NAME_DECL_TEMPLATE % re.escape(name), window):
                        label = 'C-style cast (%s) drops const from %s' % (cast, name)
                    elif m.group(3) == '' and name in members and not local_decl_re(name).search(window):
                        label = 'C-style cast (%s) drops const from member %s' % (cast, name)
                    elif call and call.group(1) in const_returns and operand.endswith(')'):
                        label = 'C-style cast (%s) drops const from the result of %s()' % (cast, call.group(1))
                if label:
                    label += where
                    if label not in out.setdefault(i, []):
                        out[i].append(label)
    return sorted((i, '; '.join(v)) for i, v in out.items() if v)


def matching_open(s, k):
    closer = s[k]
    opener = {')': '(', ']': '[', '}': '{'}[closer]
    depth = 0
    while k >= 0:
        c = s[k]
        if c == closer:
            depth += 1
        elif c == opener:
            depth -= 1
            if depth == 0:
                return k
        k -= 1
    return -1


def short_expr(t):
    t = re.sub(r'\s+', '', t)
    return t if len(t) <= 60 else t[:57] + '...'


def comparison_operand(line, m):
    after = line[m.end():]
    om = re.match(r'\s*([=!]=)(?!=)', after)
    if om:
        rest = after[om.end():]
        am = ASSIGN_IN_PARENS_RE.match(rest.lstrip())
        if am:
            return om.group(1), 'assign', am.group(1), None
        r = rest.lstrip()
        base = m.end() + om.end() + len(rest) - len(r)
        if r.startswith('('):
            close = matching_close(r, 0)
            if close > 0:
                return om.group(1), 'expr', r[1:close].strip(), base + close + 1
        sm = OPERAND_START_RE.match(rest)
        return (om.group(1), 'expr', sm.group(1).strip(), m.end() + om.end() + sm.end()) if sm else None
    before = line[:m.start()]
    bm = re.search(r'(?<![=!<>])([=!]=)\s*$', before)
    if not bm:
        return None
    left = before[:bm.start()].rstrip()
    if left.endswith(')'):
        k = matching_open(left, len(left) - 1)
        if k >= 0 and (k == 0 or not re.match(r'[\w)\]>]', left[k - 1])):
            am = ASSIGN_IN_PARENS_RE.match(left[k:])
            if am:
                return bm.group(1), 'assign', am.group(1), None
            return bm.group(1), 'expr', left[k + 1:-1].strip(), None
    em = OPERAND_END_RE.search(left)
    return (bm.group(1), 'expr', em.group(1).strip(), None) if em else None


def continues_as_call(src, i, end):
    if end is None or src.lines[i][end:].strip():
        return False
    for j in range(i + 1, min(i + 3, len(src.lines))):
        nxt = src.lines[j].strip()
        if nxt:
            return re.match(r'(?:\.|->)\s*[A-Za-z_]\w*\s*(?:<[^()]*>)?\s*\(|\(|<[^()]*>\s*\(', nxt) is not None
    return False


def ret_compare_rows(src, errnos, stats):
    out = []
    code = src.code
    if 'OB_' not in code:
        return out
    for i, line in enumerate(src.lines):
        if 'OB_' not in line or ('==' not in line and '!=' not in line):
            continue
        found = []
        for m in ERRNO_TOKEN_RE.finditer(line):
            cname = m.group(1)
            if cname == 'OB_SUCCESS':
                continue
            r = comparison_operand(line, m)
            if r is None:
                continue
            op, kind, text, end = r
            t = text.lstrip('*&').strip()
            if kind == 'expr' and (not t or t in LITERAL_OPERANDS or re.fullmatch(r'(?:\w+::)*[A-Z][A-Z0-9_]*', t)):
                continue
            is_ret = kind == 'assign' or re.fullmatch(RET_VARS, t) is not None
            if cname not in errnos:
                if is_ret:
                    stats['ret-compare non-errno'] += 1
                continue
            if kind == 'assign':
                found.append(text + ' (assigned in the comparison) ' + op + ' ' + cname)
            elif is_ret:
                found.append(t + ' ' + op + ' ' + cname)
            elif ERRSIM_RE.search(t):
                found.append('errsim expression ' + short_expr(t) + ' ' + op + ' ' + cname)
            elif t.endswith(')') or continues_as_call(src, i, end):
                found.append('call result ' + short_expr(t) + ' ' + op + ' ' + cname)
            else:
                found.append('other variable ' + short_expr(t) + ' ' + op + ' ' + cname)
        if found:
            out.append((i, '; '.join(found)))
    if 'switch' in code and 'case' in code:
        spans = []
        for m in SWITCH_RE.finditer(code):
            close = matching_close(code, m.end() - 1)
            if close < 0:
                continue
            k = close + 1
            while k < len(code) and code[k].isspace():
                k += 1
            if k >= len(code) or code[k] != '{':
                continue
            end = matching_close(code, k)
            spans.append((k, end if end >= 0 else len(code), re.sub(r'\s+', ' ', code[m.end():close]).strip()))
        for cm in CASE_CODE_RE.finditer(code):
            cname = cm.group(1)
            if cname == 'OB_SUCCESS' or cname not in errnos:
                continue
            inner = None
            for start, end, expr in spans:
                if start < cm.start() < end and (inner is None or start > inner[0]):
                    inner = (start, end, expr)
            if inner is not None:
                out.append((src.line_of(cm.start()), 'switch (' + inner[2] + ') case ' + cname))
    return out


def cond_reset_kind(line, cm):
    cond = cm.group(1) if cm.group(1) is not None else (cm.group(2) or '')
    if ERRSIM_RE.search(cond) or (UPPER_CONDITION_RE.match(cond) and re.search(r'\?\s*:', line)):
        return 'errsim injection point (ternary yields OB_SUCCESS when the tracepoint is off; not a reset)'
    if CODE_VAR_RE.search(cond):
        return 'conditional reset (ternary yields OB_SUCCESS)'
    return 'code chosen by a flag (ternary yields OB_SUCCESS; no error is swallowed)'


def reset_rows(src):
    out = collections.OrderedDict()
    code = src.code
    if 'OB_SUCCESS' not in code and 'ret' not in code:
        return []
    for i, line in enumerate(src.lines):
        if 'ret' not in line or 'OB_SUCCESS' not in line:
            continue
        kinds = []
        for m in RESET_RE.finditer(line):
            if RESET_DECL_RE.search(line[:m.start()]):
                continue
            if RESET_LINE_START_RE.match(line):
                kinds.append('ret = OB_SUCCESS (line start)')
            elif line[m.end():].lstrip().startswith(';'):
                kinds.append('ret = OB_SUCCESS (after other code on the line)')
            else:
                kinds.append('ret = OB_SUCCESS inside an expression (FALSE_IT or a comma expression)')
            break
        cm = COND_RESET_RE.search(line)
        if cm and not RESET_DECL_RE.search(line[:line.find('ret')]):
            kinds.append(cond_reset_kind(line, cm))
        if kinds:
            out[i] = kinds
    if '?' in code and 'OB_SUCCESS' in code:
        done = set()
        for q in re.finditer(r'\?', code):
            start = max(code.rfind(';', 0, q.start()), code.rfind('{', 0, q.start()), code.rfind('}', 0, q.start())) + 1
            end = code.find(';', q.start())
            if end < 0 or start in done:
                continue
            done.add(start)
            text = code[start:end + 1]
            if 'OB_SUCCESS' not in text or '\n' not in text.strip() or '{' in text or '}' in text:
                continue
            joined = ' '.join(text.replace('\\', ' ').split())
            cm = COND_RESET_RE.search(joined)
            if not cm or RESET_DECL_RE.search(joined[:cm.start()]):
                continue
            rm = re.search(r'(?<![\w.>:&*])ret\s*=(?!=)', text)
            if rm is None:
                continue
            a = src.line_of(start + rm.start())
            b = src.line_of(end)
            if a == b or any(k.startswith(('conditional', 'code chosen', 'errsim')) for k in out.get(a, [])):
                continue
            out.setdefault(a, []).append(cond_reset_kind(joined, cm) + ' (statement spans lines %d-%d)' % (a + 1, b + 1))
    if 'ret' in code and RESET_ZERO_RE.search(code):
        for fname, hl, ol, end, extra in src.function_spans():
            open_off = src.body_open.get((fname, hl))
            if open_off is None:
                continue
            body = code[open_off:src.starts[end] + len(src.lines[end])]
            if not RESET_ZERO_RE.search(body) or not ERROR_RET_DECL_RE.search(body):
                continue
            for i in range(src.line_of(open_off), end + 1):
                line = src.lines[i]
                m = RESET_ZERO_RE.search(line)
                if m and not RESET_DECL_RE.search(line[:m.start()]):
                    out.setdefault(i, []).append('ret = 0 (a reset of the error code, spelled 0)')
    return sorted((i, '; '.join(k)) for i, k in out.items())


def tmp_ret_rows(src, stats):
    rows = collections.OrderedDict()
    code = src.code
    if 'tmp_ret' in code or 'temp_ret' in code or 'OB_TMP_FAIL' in code:
        for i, line in enumerate(src.lines):
            if 'tmp_ret' not in line and 'temp_ret' not in line and 'OB_TMP_FAIL' not in line:
                continue
            kinds = []
            d = TMP_DECL_RE.search(line) or TMP_INIT_SUCC_RE.search(line)
            if d:
                kinds.append('declare ' + d.group(1) + (' (plan definition)' if TMP_PLAN_DECL_RE.search(line) else ''))
            if TMP_FAIL_RE.search(line):
                kinds.append('OB_TMP_FAIL (plan definition)')
            if not d:
                a = TMP_ASSIGN_RE.search(line)
                if a and not TMP_FAIL_RE.search(line):
                    kinds.append('assign ' + a.group(1))
            if TMP_MERGE_RE.search(line):
                kinds.append('merge into ret')
            if kinds:
                rows[i] = kinds
            else:
                stats['tmp-ret compare or log only'] += 1
    if_merges = collections.defaultdict(list)
    if 'if' in code and 'ret' in code:
        for m in IF_MERGE_RE.finditer(code):
            x = m.group(2)
            if x != 'ret' and not re.fullmatch(TMP_VAR, x):
                if_merges[x].append(src.line_of(m.start(1)))
    if ('?' in code and 'OB_SUCC' in code) or 'COVER_SUCC' in code or if_merges:
        spans = [(f[1], f[3]) for f in src.function_spans()]
        spans += [(lns[0], lns[-1]) for _, _, lns in define_bodies(src)]
        for hl, end in spans:
            merged = collections.OrderedDict()
            for i in range(hl, end + 1):
                line = src.lines[i]
                if 'ret' not in line or ('?' not in line and 'COVER_SUCC' not in line):
                    continue
                for m in MERGE_OTHER_RE.finditer(line):
                    x = re.sub(r'\s+', '', m.group(1) or m.group(2) or '')
                    if not x or x == 'ret' or re.fullmatch(TMP_VAR, x):
                        continue
                    merged.setdefault(x, set()).add(i)
            if_lines = set()
            for x, lns in if_merges.items():
                for i in lns:
                    if hl <= i <= end:
                        merged.setdefault(x, set()).add(i)
                        if_lines.add((x, i))
            for x, lns in merged.items():
                for i in sorted(lns):
                    form = ' through if (OB_SUCC(ret))' if (x, i) in if_lines else ''
                    kind = 'merge ' + x + ' into ret' + form + ' (continue-and-record through another variable)'
                    if kind not in rows.setdefault(i, []):
                        rows[i].append(kind)
                if not re.fullmatch(r'[A-Za-z_]\w*', x):
                    continue
                decl_re = re.compile(r'\b(?:int|int32_t|int64_t)\s+' + re.escape(x) + r'\s*(?:=|;)')
                asg_re = re.compile(r'(?<![\w.>])' + re.escape(x) + r'\s*=(?!=)')
                for i in range(hl, end + 1):
                    line = src.lines[i]
                    if x not in line or i in lns:
                        continue
                    if decl_re.search(line):
                        kind = 'declare ' + x + ' (continue-and-record variable)'
                    elif asg_re.search(line):
                        kind = 'assign ' + x + ' (continue-and-record variable)'
                    else:
                        continue
                    if kind not in rows.setdefault(i, []):
                        rows[i].append(kind)
    return sorted((i, '; '.join(k)) for i, k in rows.items())


def error_branch(code, start, end):
    m = re.compile(r'\bif\s*\(').search(code, start, end)
    if not m:
        return None
    depth = 0
    i = m.end() - 1
    while i < end:
        c = code[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                break
        i += 1
    cond = code[m.end():i]
    if not re.search(r'\bret_?\b', cond):
        return None
    j = code.find('{', i, end)
    if j < 0 or code[i + 1:j].strip():
        return None
    depth = 0
    k = j
    while k < end:
        c = code[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return code[j + 1:k]
        k += 1
    return None


def param_type(p):
    p = p.split('=', 1)[0]
    p = re.sub(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', '', p.strip())
    return re.sub(r'\s+|\bconst\b|[&*]', '', p)


def comparator_kind(extra, fname):
    h, simple, rettype, args = extra
    ret_kind = 'bool' if re.search(r'\bbool\b', rettype) else ('int' if re.search(r'\bint\b', rettype) else rettype or '?')
    params = [p for p in split_top(args) if p.strip() and p.strip() != 'void']
    named = re.search(r'cmp|compar|less', enclosing_class(fname), re.I) is not None
    if simple == 'operator<':
        is_cmp = ret_kind == 'bool'
    elif simple == 'operator()':
        same = len(params) == 2 and param_type(params[0]) == param_type(params[1])
        is_cmp = ret_kind == 'bool' and (same or named)
    else:
        is_cmp = re.search(r'compare|cmp|less', simple, re.I) is not None and ret_kind in ('bool', 'int')
    return ret_kind, is_cmp


def ret_alias_rows(src):
    out = []
    code = src.code
    if 'ret' not in code:
        return out
    funcs = src.function_spans()
    aliased = set()
    for i, line in enumerate(src.lines):
        if 'ret' not in line or '&' not in line:
            continue
        m = RET_ALIAS_RE.search(line)
        if not m:
            continue
        construct = 'int &ret = ' + re.sub(r'\s+', '', m.group(1))
        fn = None
        for f in funcs:
            if f[2] <= i <= f[3]:
                fn = f
        if fn:
            aliased.add((fn[0], fn[1]))
            ret_kind, is_cmp = comparator_kind(fn[4], fn[0])
            if is_cmp:
                branch = error_branch(code, src.starts[i + 1] if i + 1 < len(src.starts) else len(code),
                                      src.starts[fn[3]] + len(src.lines[fn[3]]))
                if branch is None:
                    construct += '; ' + ret_kind + ' comparator, no error branch found'
                elif re.search(r'(?<![=!<>])=(?!=)|\breturn\b', branch):
                    construct += '; ' + ret_kind + ' comparator, error branch sets a result'
                else:
                    construct += '; ' + ret_kind + ' comparator, error branch sets no result'
            else:
                construct += '; not a comparator (' + fn[4][1] + ')'
        out.append((i, construct))
    for fname, hl, ol, end, extra in funcs:
        if (fname, hl) in aliased:
            continue
        ret_kind, is_cmp = comparator_kind(extra, fname)
        if not is_cmp:
            continue
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        body = code[open_off:src.starts[end] + len(src.lines[end])]
        member = member_error(body)
        if member:
            out.append((src.name_line(fname, hl), '%s comparator keeps its error in member %s without an int &ret alias'
                         % (ret_kind, member)))
    if '[' in code and 'ret' in code:
        for m in SEARCH_ALGO_RE.finditer(code):
            close = matching_close(code, m.end() - 1)
            if close < 0:
                continue
            inner = code[m.end():close]
            for lm in re.finditer(r'\[([^\[\]]*)\]\s*\(', inner):
                cap = lm.group(1)
                if not (re.search(r'&\s*ret\b', cap) or re.match(r'\s*&\s*(?:,|$)', cap)):
                    continue
                rest = inner[lm.end():]
                b = rest.find('{')
                e = matching_close(rest, b) if b >= 0 else -1
                if e < 0 or not re.search(r'\bOB_FAIL\s*\(|(?<![\w.>])ret\s*=(?!=)', rest[b:e + 1]):
                    continue
                ln = src.line_of(m.end() + lm.start())
                out.append((ln, 'lambda comparator passed to %s captures ret by reference and records errors in it'
                             % re.sub(r'\s+', '', m.group(1))))
    return out


def member_error(body):
    m = MEMBER_ERROR_ASSIGN_RE.search(body)
    if m:
        return re.sub(r'\s+', '', m.group(1))
    for m in MEMBER_ASSIGN_RE.finditer(body):
        name = re.sub(r'^this\s*->\s*', '', m.group(1))
        n = re.escape(name)
        if re.search(r'\bOB_SUCCESS\s*[!=]=\s*\*?\s*(?:this\s*->\s*)?' + n + r'\b|(?<![\w.>])\*?\s*' + n
                     + r'\s*[!=]=\s*(?:common\s*::\s*)?OB_SUCCESS\b|\bOB_(?:SUCC|FAIL)\s*\(\s*\*?\s*' + n + r'\s*\)', body):
            return name
    return None


def block_opener(code, pos, limit=20000):
    depth = 0
    k = pos - 1
    lo = max(0, pos - limit)
    while k >= lo:
        c = code[k]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                return k
            depth -= 1
        k -= 1
    return -1


def header_before(code, brace):
    k = brace - 1
    depth = 0
    while k >= 0:
        c = code[k]
        if c == ')':
            depth += 1
        elif c == '(':
            depth -= 1
        elif depth == 0 and c in ';{}':
            break
        k -= 1
    return k + 1, code[k + 1:brace]


def if_condition(header):
    h = re.sub(r'^[ \t]*#.*$', ' ', header, flags=re.M).replace('\\', ' ')
    h = re.sub(r'^(?:\s*(?:case\s[^:]*|default)\s*:(?!:))+', ' ', h.strip()).strip()
    if h == 'else':
        return 'else', None
    m = re.match(r'(else\s+)?if\s*(?:constexpr\s*)?\(|(catch)\s*\(', h)
    if not m:
        return None, None
    close = matching_close(h, m.end() - 1)
    kind = 'catch' if m.group(2) else ('else if' if m.group(1) else 'if')
    return kind, h[m.end():close if close >= 0 else len(h)]


def chain_conditions(code, brace):
    start, header = header_before(code, brace)
    kind, cond = if_condition(header)
    if kind is None:
        return None, None, start
    direct = cond
    conds = [cond] if cond is not None else []
    while kind in ('else', 'else if'):
        j = start - 1
        while j >= 0 and code[j].isspace():
            j -= 1
        if j < 0 or code[j] != '}':
            break
        ob = matching_open(code, j)
        if ob < 0:
            break
        start, header = header_before(code, ob)
        kind, cond = if_condition(header)
        if kind is None:
            break
        if cond is not None:
            conds.append(cond)
    return direct, conds, start


def governing_conditions(code, pos, outer=True):
    k = pos - 1
    while k >= 0 and code[k].isspace():
        k -= 1
    if k >= 0 and code[k] == ')':
        o = matching_open(code, k)
        im = re.search(r'\bif\s*$', code[max(0, o - 20):o]) if o > 0 else None
        if im is None:
            return None, None, None, None, None, None
        direct = code[o + 1:k]
        conds = [direct]
        top = max(0, o - 20) + im.start()
        opener = k
    else:
        opener = block_opener(code, pos)
        if opener < 0:
            return None, None, None, None, None, None
        direct, conds, top = chain_conditions(code, opener)
        if conds is None:
            return None, None, None, None, opener, None
    parent = []
    parent_top = None
    brace = block_opener(code, top) if outer else -1
    if brace >= 0:
        pdirect, found, parent_top = chain_conditions(code, brace)
        parent = [pdirect] if pdirect is not None else []
    return direct, conds or None, parent, top, opener, parent_top


def null_tested(cond):
    out = []
    for m in NULL_TESTED_RE.finditer(cond):
        t = re.sub(r'\s+', '', next(g for g in m.groups() if g))
        if t:
            out.append(t)
    return out


def statement_start(code, p):
    while p > 0 and (code[p - 1].isalnum() or code[p - 1] in '_.:>-[] \t'):
        p -= 1
    return p


def allocator_aliases(src, body, accessors):
    names = set(m.group(1) for m in ALIAS_OF_MEMBER_RE.finditer(body))
    for acc in accessors:
        for m in re.finditer(r'\b' + re.escape(acc) + r'\s*\(\s*([A-Za-z_]\w*)\s*\)', body):
            names.add(m.group(1))
    names.discard(BUDGET_MEMBER)
    return names


def enclosing_call(code, pos):
    depth = 0
    k = pos - 1
    while k >= 0:
        c = code[k]
        if c == ')':
            depth += 1
        elif c == '(':
            if depth == 0:
                m = re.search(r'([A-Za-z_]\w*)\s*$', code[max(0, k - 80):k])
                if not m:
                    return None, ''
                close = matching_close(code, k)
                inner = code[k + 1:close] if close > 0 else code[k + 1:pos]
                return m.group(1), split_top(inner)[0]
            depth -= 1
        elif c in ';{}' and depth == 0:
            return None, ''
        k -= 1
    return None, ''


def oom_argument_row(src, i):
    line = src.lines[i]
    for m in OOM_TOKEN_RE.finditer(line):
        callee, first = enclosing_call(src.code, src.starts[i] + m.start())
        if callee is None or LOG_CALLEE_RE.fullmatch(callee) or callee.startswith('DEFINE_ERROR'):
            continue
        if callee == 'RET_ERR':
            return 'errsim injection point raising -4013 (simulation point)'
        if callee.startswith('CASE'):
            return '-4013 handled (case label through %s)' % callee
        if callee in ('OV', 'OX', 'OZ', 'CK'):
            if ALLOCATION_TOKEN_RE.search(first) or NULL_TEST_RE.search(first):
                return None
            return '-4013 raised through %s with a condition that tests no allocation (not classified)' % callee
        return '-4013 passed to %s (raised by the callee; not classified)' % callee
    return None


def oom_rows(src, ctx):
    out = []
    code = src.code
    path = src.path
    seeds = [(label, plan, w) for label, plan, p, w in ctx['budget_seeds'] if p is None or p.search(path)]
    cases = [(label, w) for label, p, w in ctx['logical_cases'] if p.search(path)]
    member_seed = next(((label, plan) for label, plan, p, w in ctx['budget_seeds'] if p is not None and p.search(path)
                        and BUDGET_MEMBER in w.pattern), None)
    has_oom = 'OB_ALLOCATE_MEMORY_FAILED' in code or 'OB_PARSER_ERR_NO_MEMORY' in code
    if has_oom:
        funcs = src.function_spans()
        starts = [f[1] for f in funcs]
        accessors = set()
        if member_seed and BUDGET_MEMBER in code:
            for fname, hl, ol, end, extra in funcs:
                body = '\n'.join(src.lines[hl:end + 1])
                for m in ALIAS_OF_MEMBER_RE.finditer(body):
                    if re.search(r'\b' + re.escape(m.group(1)) + r'\b', extra[3]):
                        accessors.add(extra[1])
        for i, line in enumerate(src.lines):
            if 'OB_ALLOCATE_MEMORY_FAILED' not in line and 'OB_PARSER_ERR_NO_MEMORY' not in line:
                continue
            if OOM_CASE_RE.search(line):
                out.append((i, '-4013 handled (case label)'))
                continue
            if OOM_COMPARE_RE.search(line):
                out.append((i, '-4013 handled (compared against)'))
                continue
            if OOM_DECL_RE.search(line):
                out.append((i, 'variable initialized to -4013 (not a raise)'))
                continue
            if not OOM_RAISE_RE.search(line):
                row = oom_argument_row(src, i)
                if row:
                    out.append((i, row))
                continue
            fn = None
            if src.dkind.get(i) in ('define', 'cont'):
                lo = i
                while lo > 0 and src.dkind.get(lo) == 'cont':
                    lo -= 1
            else:
                k = bisect.bisect_right(starts, i) - 1
                while k >= 0 and funcs[k][3] < i:
                    k -= 1
                fn = funcs[k] if k >= 0 else None
                lo = fn[1] if fn is not None else 0
            m = OOM_RAISE_RE.search(line)
            raise_at = statement_start(code, src.starts[i] + m.start())
            direct, conds, parent, top, opener, parent_top = governing_conditions(code, raise_at)
            if direct is not None and OOM_COMPARE_RE.search(direct):
                continue
            cond_text = OOM_TOKEN_RE.sub(' ', '\n'.join(conds or []))
            parent_text = OOM_TOKEN_RE.sub(' ', '\n'.join(parent or []))
            stmt = OOM_TOKEN_RE.sub(' ', line)
            ws = src.starts[max(lo, i - 4)]
            if conds is None:
                before = code[ws:raise_at]
            elif top is not None and top > ws:
                before = code[ws:top] + '\n' + (code[opener + 1:raise_at] if opener is not None and opener >= top else '')
            else:
                before = code[max(ws, opener + 1) if opener is not None else ws:raise_at]
            lines_before = OOM_TOKEN_RE.sub(' ', before + '\n' + line)
            window = lines_before + '\n' + cond_text + '\n' + parent_text
            if direct is not None and ERRSIM_RE.search(direct):
                out.append((i, 'errsim injection point raising -4013'))
                continue
            label = None
            fname = fn[0] if fn is not None else ''
            for lb, plan, w in seeds:
                if plan != 'condition' and (w.search(window) or w.search(fname)):
                    label = (lb, plan)
                    break
            if label is None and member_seed and fn is not None:
                body = '\n'.join(src.lines[fn[1]:fn[3] + 1])
                aliases = allocator_aliases(src, body, accessors)
                if aliases:
                    rx = re.compile(r'\b(?:' + '|'.join(re.escape(a) for a in sorted(aliases)) + r')\s*(?:\.|->)\s*alloc\w*\s*\(')
                    if rx.search(window):
                        label = (member_seed[0] + ', allocator obtained from ' + BUDGET_MEMBER, member_seed[1])
            direct_null = any(NULL_TEST_RE.search(c) or INDIRECT_OOM_RE.search(c) for c in (conds or []))
            if label is None and not direct_null:
                label = next(((lb, True) for lb, plan, w in seeds if plan == 'condition'
                              and w.search(cond_text + '\n' + parent_text)), None)
            if label:
                if label[1]:
                    out.append((i, 'budget-backed -4013: ' + label[0]))
                else:
                    out.append((i, 'bounded allocator not named in the plan: %s (a general out-of-memory under Decision 12 '
                                   'unless the design names it a budget owner)' % label[0]))
                continue
            if direct_null or re.search(r'\bENOMEM\b', window):
                continue
            case = next((lb for lb, w in cases if w.search(window)), None)
            if case:
                out.append((i, 'logical -4013 (plan case: %s)' % case))
                continue
            null_judged = (cond_text + '\n' + stmt) if conds is not None else lines_before
            if parent and parent_top is not None:
                pl = src.line_of(parent_top)
                assigned = re.sub(r'\s+', '', code[src.starts[max(lo, pl - 8)]:parent_top])
                for c in parent:
                    for t in null_tested(c):
                        if re.search(re.escape(t) + r'=(?!=)[^;]*\(', assigned):
                            null_judged += '\n' + c
            how = 'governing condition' if conds is not None else '4 lines before'
            if not ALLOCATION_TOKEN_RE.search(window) and not NULL_TEST_RE.search(null_judged):
                owner = next((lb for lb, plan, p, w in ctx['budget_seeds']
                              if p is not None and p.search(path) and plan != 'condition'), None)
                out.append((i, 'logical -4013, not a named plan case (provisional: no allocation call and no null test in the %s)'
                            % how + ('; budget owner file: ' + owner if owner else '')))
    compare_re = ctx['budget_compare_re']
    error_re = ctx['budget_error_re']
    for i, line in enumerate(src.lines):
        if 'OB_' in line:
            m = compare_re.search(line)
            if m:
                out.append((i, 'budget error handled (compared against or case label): ' + next(g for g in m.groups() if g)))
            else:
                m = error_re.search(line)
                if m:
                    out.append((i, 'budget error raised: ' + m.group(1)))
        if 'check_status' in line or 'CHECK_MEM_STATUS' in line or 'check_mem_status' in line:
            m = BUDGET_CHECK_CALL_RE.search(line)
            if m and src.dkind.get(i) not in ('define', 'cont'):
                name = re.search(r'(\w+)\s*\($', m.group()).group(1)
                ck = call_kind(src, i, name, line[:m.start()], src.starts[i] + m.end() - 1)
                out.append((i, 'query memory tracker check' if ck == 'call' else 'query memory tracker check function ' + ck))
    if FIFO_FILE_RE.search(path):
        for i, line in enumerate(src.lines):
            if FIFO_LINE_RE.search(line):
                out.append((i, 'micro block cache FIFO: owner, limit, hand-out or retry'))
    return out


def ancestors(bases, cls):
    out = []
    todo = [cls]
    seen = set()
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
        todo.extend(sorted(bases.get(c, ())))
    return out


def sort_hash_rows(src, ctx, member_hash):
    out = []
    code = src.code
    if 'sort' in code or 'heap' in code or 'nth_element' in code:
        for i, line in enumerate(src.lines):
            if 'sort' not in line and 'heap' not in line and 'nth_element' not in line:
                continue
            found = []
            for m in SORT_CALL_RE.finditer(line):
                g = next(x for x in m.groups() if x)
                g = re.sub(r'\s+', '', g)
                if m.group(4):
                    found.append('member ' + g + '() call')
                elif m.group(5):
                    paren = line.index('(', m.start(5))
                    if call_kind(src, i, g, line[:m.start(5)], src.starts[i] + paren) != 'call':
                        continue
                    found.append(g + '() call on this object')
                else:
                    found.append(g + ' call')
            if found:
                out.append((i, '; '.join(found)))
        for name, hl, ol, end, extra in src.function_spans():
            if extra[1] in SORT_FUNCTION_NAMES:
                out.append((src.name_line(name, hl), 'sort function definition: ' + name))
    out += heap_rows(src, ctx)
    if 'begin' not in code and 'foreach' not in code and 'for_each' not in code and ':' not in code:
        return out
    decl_re = ctx['hash_decl_re']
    iter_re = ctx['hash_iter_re']
    getter_re = ctx['hash_getter_re']
    tree_names = ctx['hash_member_names']
    tree_re = ctx['hash_member_re']
    getters_by_class = ctx['hash_getters_by_class']
    hash_classes = ctx['hash_classes']
    bases = ctx['class_bases']
    stem = src.stem()
    file_tree = set(tree_re.findall(code)) if tree_re is not None else set()
    for name, hl, ol, end, extra in src.function_spans():
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        if 'begin' not in body and 'foreach' not in body and 'for_each' not in body and ':' not in body:
            continue
        cls = enclosing_class(name)
        if cls in hash_classes or HASH_ANY_RE.fullmatch(cls or '-'):
            continue
        names = {}
        for m in decl_re.finditer(body):
            names[m.group(3)] = m.group(1)
        for anc in ancestors(bases, cls) if cls else ():
            for nm, typ in member_hash.get(('class', anc), {}).items():
                names.setdefault(nm, typ)
        for nm, typ in member_hash.get(('stem', stem), {}).items():
            names.setdefault(nm, typ)
        chained = {}
        for nm in file_tree:
            if nm not in names and nm in body:
                chained[nm] = tree_names[nm]
        it_re = None
        if names or chained:
            alt = '|'.join(re.escape(n) for n in sorted(set(names) | set(chained)))
            it_re = re.compile(r'(?<![\w])(' + alt + r')\s*(?:\.|->)\s*' + ITERATE_METHODS + r'\s*\(|'
                               r'\bfor\s*\([^;]*?:\s*\*?\s*(?:this\s*->\s*)?(?:[\w.\->]*?(?:\.|->))?(' + alt + r')\s*\)')
        for j, line in enumerate(body_lines):
            row = None
            if it_re is not None:
                for m in it_re.finditer(line):
                    nm = m.group(1) or m.group(2)
                    if nm in chained and not re.search(r'(?:\.|->)\s*$', line[:m.start(1) if m.group(1) else m.start(2)]):
                        continue
                    row = 'iterate hash container ' + nm + ' (' + names.get(nm, chained.get(nm, '?')) + ')'
                    break
            if row is None and getter_re is not None and 'get_' in line:
                m = getter_re.search(line)
                if m:
                    row = 'iterate hash container returned by ' + m.group(1) + '()'
            if row is None and 'get_' in line:
                for m in RECEIVER_GETTER_RE.finditer(line):
                    recv, getter = m.group(1), m.group(2)
                    info = find_local(src, (name, hl, ol, end, extra), recv, src.starts[hl + j] + m.start())
                    types = type_words(info[1]) if info else set()
                    if any((t, getter) in getters_by_class for t in types):
                        row = 'iterate hash container returned by ' + getter + '()'
                        break
            if row is None and '::' in line and 'iterator' in line:
                m = iter_re.search(line)
                if m:
                    row = 'iterate hash container through an iterator of ' + m.group(1)
            if row:
                out.append((hl + j, row))
    return out


def heap_rows(src, ctx):
    out = []
    decl_re = ctx['heap_decl_re']
    if decl_re is None or not ctx['heap_name_re'].search(src.code):
        return out
    heap_classes = ctx['heap_classes']
    bases = ctx['class_bases']
    member_heap = ctx['member_heap']
    tree_names = ctx['heap_member_names']
    heap_tree_re = ctx['heap_member_re']
    stem = src.stem()
    rows = {}
    for m in decl_re.finditer(src.code):
        ln = src.line_of(m.start())
        rows.setdefault(ln, 'heap declared: %s (%s)' % (m.group(3), m.group(1)))
    for name, hl, ol, end, extra in src.function_spans():
        cls = enclosing_class(name)
        if cls in heap_classes and extra[1] in HEAP_OPS:
            rows[src.name_line(name, hl)] = 'heap operation definition: ' + name
            continue
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        names = {}
        for m in decl_re.finditer(body):
            names[m.group(3)] = m.group(1)
        for anc in ancestors(bases, cls) if cls else ():
            for nm, typ in member_heap.get(('class', anc), {}).items():
                names.setdefault(nm, typ)
        for nm, typ in member_heap.get(('stem', stem), {}).items():
            names.setdefault(nm, typ)
        if heap_tree_re is not None:
            for nm in set(heap_tree_re.findall(body)):
                names.setdefault(nm, tree_names[nm])
        if not names:
            continue
        alt = '|'.join(re.escape(n) for n in sorted(names))
        op_re = re.compile(r'(?<![\w])(' + alt + r')\s*(?:\.|->)\s*(' + '|'.join(HEAP_OPS) + r')\s*\(')
        for j, line in enumerate(body_lines):
            m = op_re.search(line)
            if m:
                rows.setdefault(hl + j, 'heap operation %s on %s (%s)' % (m.group(2), m.group(1), names[m.group(1)]))
    return sorted(rows.items())


def ir_names_in(text, ir_types):
    ptrs = set()
    arrays = set()
    for m in IR_PTR_DECL_RE.finditer(text):
        if m.group(1) in ir_types:
            ptrs.add(m.group(2))
    for m in IR_ARRAY_DECL_RE.finditer(text):
        if m.group(1) in ir_types:
            arrays.add(m.group(2))
    return ptrs, arrays


def operand_is_ir(op, ptrs, arrays, ctx):
    op = op.strip().lstrip('*&').strip()
    if not op or op in LITERAL_OPERANDS or re.fullmatch(r'[A-Z][A-Z0-9_]*', op):
        return False
    m = re.search(r'([A-Za-z_]\w*)\s*(\((?:[^()]|\([^()]*\))*\))?\s*(\[[^\[\]]*\])?\s*$', op)
    if not m:
        return False
    name, call, sub = m.groups()
    receiver = op[:m.start()].rstrip()
    rname = re.search(r'([A-Za-z_]\w*)\s*(?:\.|->)$', receiver)
    if call:
        if name in ('at', 'get') and rname:
            r = rname.group(1)
            return r in arrays or r in ctx['ir_array_members']
        return name in ctx['ir_methods']
    if sub:
        return name in arrays or name in ctx['ir_array_members']
    if not receiver:
        return name in ptrs
    return name in ctx['ir_ptr_members']


def pointer_identity_rows(src, ctx, member_ir):
    out = []
    code = src.code
    ir_types = ctx['ir_types']
    for i, line in enumerate(src.lines):
        if '<' in line and '*' in line:
            m = ctx['pointer_key_re'].search(line)
            if m:
                out.append((i, 'container keyed by pointer: ' + re.sub(r'\s+', '', m.group(1))))
    out += pointer_cast_rows(src, ctx, member_ir)
    if not IR_CANDIDATE_RE.search(code):
        return out
    stem = src.stem()
    for name, hl, ol, end, extra in src.function_spans():
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        ptrs, arrays = ir_names_in(body, ir_types)
        cls = enclosing_class(name)
        mp, ma = member_ir.get(('class', cls), (set(), set()))
        sp, sa = member_ir.get(('stem', stem), (set(), set()))
        ptrs = ptrs | mp | sp
        arrays = arrays | ma | sa
        if '==' not in body and '!=' not in body and 'reinterpret_cast' not in body and 'int64_t)' not in body \
                and not MEMBERSHIP_RE.search(body):
            continue
        for j, line in enumerate(body_lines):
            if '==' in line or '!=' in line:
                for m in EQ_OP_RE.finditer(line):
                    lm = OPERAND_END_RE.search(line[max(0, m.start() - 120):m.start()])
                    rm = OPERAND_START_RE.match(line, m.end())
                    if not lm or not rm:
                        continue
                    left, right = lm.group(1), rm.group(1)
                    left = left.strip().lstrip('*&').strip()
                    right = right.strip().lstrip('*&').strip()
                    li = operand_is_ir(left, ptrs, arrays, ctx)
                    ri = operand_is_ir(right, ptrs, arrays, ctx)
                    lit = {left, right} & LITERAL_OPERANDS
                    if li and ri:
                        out.append((hl + j, 'IR pointer ' + m.group(1) + ' IR pointer'))
                        break
                    if (li or ri) and not lit:
                        other = right if li else left
                        o = other.strip()
                        if re.fullmatch(r'[A-Z][A-Z0-9_]*|\d+|[\w.\->]*(?:type|count|id|idx|size|len|num)\w*(?:\([^()]*\))?', o, re.I):
                            continue
                        out.append((hl + j, 'IR pointer ' + m.group(1) + ' unresolved pointer'))
                        break
            if '(' in line:
                for m in MEMBERSHIP_RE.finditer(line):
                    args, _ = balanced_arg(line, m.end() - 1)
                    whole = line[m.end():]
                    depth = 1
                    k = 0
                    while k < len(whole) and depth:
                        if whole[k] == '(':
                            depth += 1
                        elif whole[k] == ')':
                            depth -= 1
                        k += 1
                    call_args = split_top(whole[:k - 1] if depth == 0 else whole)
                    if any(operand_is_ir(a, ptrs, arrays, ctx) or a.strip().lstrip('&*') in arrays for a in call_args):
                        called = re.sub(r'\s+', '', m.group(1))
                        mixed = MIXED_MEMBERSHIP_RE.search(called) is not None or (
                            called == 'find_expr' and src.path.startswith('src/sql/rewrite/'))
                        out.append((hl + j, 'pointer membership test: ' + called
                                    + (' (pointer identity, then same_as)' if mixed else '')))
                        break
    return out


def pointer_cast_use(src, ln, off, end):
    code = src.code
    a = off
    while a > 0 and code[a - 1] not in ';{}':
        a -= 1
    b = end
    while b < len(code) and code[b] not in ';{}':
        b += 1
    stmt = code[a:b]
    after = code[end:b]
    before = code[a:off]
    if '_refactored' in stmt or re.search(r'key', stmt, re.I):
        return 'used as a key'
    constant = r'\s*(?:\d|0x|[A-Z][A-Z0-9_]*\b(?!\s*[(.\-]))'
    om = re.match(r'\s*(?:<=|>=|<(?![<=])|>(?![>=]))', after)
    bm = re.search(r'(?:<=|>=|(?<![<\-])<(?!<)|(?<![>\-])>(?!>))\s*$', before)
    if om and not re.match(constant, after[om.end():]) or bm and not re.search(
            r'(?:\b\d\w*|\b[A-Z][A-Z0-9_]*)\s*$', before[:bm.start()]):
        return 'compared for order (address ordering)'
    hashed = re.search(r'\b\w*hash\w*\s*\([^;]*$', before, re.I)
    mm = re.match(r'\s*%\s*([\w.\->]+)', after)
    if hashed or mm and not re.search(r'align|page', mm.group(1), re.I) and not re.search(r'\b0\s*[!=]=\s*\(?\s*$', before) \
            and not re.match(r'\s*%\s*[\w.\->]+\s*\)?\s*[!=]=\s*0\b', after):
        return 'used for hashing or a bucket choice'
    am = re.match(r'\s*(?:[\w:<>*&\s]+\s)?\(?\s*([A-Za-z_][\w.\->\[\]]*?)\s*=(?!=)', stmt)
    if am and ID_NAME_RE.search(re.split(r'\.|->', am.group(1))[-1].strip('[]')):
        return 'used as an id'
    if re.match(r'\s*return\b', stmt):
        fn = enclosing_function(src, ln)
        if fn is not None:
            if re.search(r'hash', fn[0], re.I):
                return 'used for hashing or a bucket choice'
            if ID_NAME_RE.search(fn[4][1]):
                return 'used as an id'
    em = re.match(r'\s*[!=]=(?!=)\s*', after)
    bm = re.search(r'[!=]=\s*$', before)
    if em and not re.match(constant + r'|\s*(?:NULL|nullptr)\b', after[em.end():]) or bm and not re.search(
            r'(?:\b\d\w*|\b[A-Z][A-Z0-9_]*|\bNULL|\bnullptr)\s*[!=]=\s*$', before):
        return 'compared for identity'
    return None


def pointer_cast_rows(src, ctx, member_ir):
    out = []
    code = src.code
    if 'reinterpret_cast' not in code and 'int64_t)' not in code and 'intptr_t)' not in code and 'int64)' not in code \
            and 'size_t)' not in code:
        return out
    ptr_members = ctx['ptr_members']
    ir_types = ctx['ir_types']
    stem = src.stem()
    for fname, hl, ol, end, extra in src.function_spans():
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        body_end = src.starts[end] + len(src.lines[end])
        body = code[open_off:body_end]
        if not POINTER_CAST_RE.search(body):
            continue
        members = class_chain_lookup(ptr_members, fname)
        span = code[src.starts[hl]:body_end]
        ptrs, arrays = ir_names_in(span, ir_types) if IR_CANDIDATE_RE.search(span) else (set(), set())
        mp, ma = member_ir.get(('class', enclosing_class(fname)), (set(), set()))
        sp, sa = member_ir.get(('stem', stem), (set(), set()))
        ptrs = ptrs | mp | sp
        arrays = arrays | ma | sa
        params = extra[3]
        for m in POINTER_CAST_RE.finditer(code, open_off, body_end):
            if m.group(1):
                close = matching_close(code, m.start(1))
                if close < 0:
                    continue
                operand = code[m.start(1) + 1:close].strip()
                end_off = close + 1
                pointer = True
            else:
                operand = m.group(2).strip().lstrip('(').strip()
                end_off = m.end(2)
                last = re.split(r'\.|->', operand)[-1].strip('*& ')
                root = re.match(r'[&*]?\s*([A-Za-z_]\w*)', operand)
                root = root.group(1) if root else ''
                pointer = operand.startswith('&') or root == 'this' and last == 'this'
                if not pointer and last:
                    if last in members and (root == 'this' or root == last):
                        pointer = True
                    else:
                        decl = None
                        for dm in local_decl_re(last).finditer(code, open_off, m.start()):
                            decl = dm
                        if decl is not None and '*' in decl.group(2) and root == last:
                            pointer = True
                        elif root == last and re.search(r'\*\s*&?\s*' + re.escape(last) + r'\s*(?:,|$|=)', params):
                            pointer = True
            ln = src.line_of(m.start())
            ir = operand_is_ir(operand, ptrs, arrays, ctx) or re.match(r'[&*]?\s*([A-Za-z_]\w*)', operand) is not None \
                and re.match(r'[&*]?\s*([A-Za-z_]\w*)', operand).group(1) in ptrs
            if not pointer and not ir:
                continue
            use = pointer_cast_use(src, ln, m.start(), end_off)
            if ir and use is None:
                use = 'IR identity'
            if use is None:
                continue
            out.append((ln, 'pointer cast to integer ' + use))
    seen = set()
    rows = []
    for ln, construct in out:
        if ln not in seen:
            seen.add(ln)
            rows.append((ln, construct))
    return rows


def in_dirs(path, dirs):
    return path.startswith(dirs)


def overflow_rows(src, ctx):
    out = []
    code = src.code
    path = src.path
    value_code = in_dirs(path, VALUE_DIRS)
    if '__builtin_' in code:
        for i, line in enumerate(src.lines):
            if BUILTIN_OVERFLOW_RE.search(line):
                out.append((i, 'check: __builtin_*_overflow'))
    seen = set(i for i, _ in out)
    if 'OB_' in code and 'OVERFLOW' in code or 'OUT_OF_RANGE' in code:
        for i, line in enumerate(src.lines):
            if i in seen or 'OB_' not in line:
                continue
            m = OVERFLOW_ERROR_RE.search(line)
            if m and integer_overflow_raise(src, i, m):
                out.append((i, 'check: raises ' + m.group(1)))
                seen.add(i)
    if 'out_of_range' in code:
        out += post_checked_arithmetic(src, seen)
    if not value_code:
        return out
    for i, line in enumerate(src.lines):
        if i in seen:
            continue
        kinds = []
        if 'overflow' in line or 'out_of_range' in line or 'OVERFLOW' in line or 'Overflow' in line:
            for m in OVERFLOW_HELPER_RE.finditer(line):
                if OVERFLOW_HELPER_SKIP_RE.search(m.group(1)) or src.dname.get(i) == m.group(1):
                    continue
                ck = call_kind(src, i, m.group(1), line[:m.start()], src.starts[i] + m.end() - 1)
                if ck == 'declaration':
                    continue
                kinds.append('check: helper ' + m.group(1) + (' (definition)' if ck == 'definition' else ''))
                break
        if LIMIT_CONST_RE.search(line) and COMPARISON_RE.search(line) and BINARY_ARITH_RE.search(SUBSCRIPT_RE.sub(' ', line)):
            kinds.append('check: limit comparison')
        if kinds:
            out.append((i, '; '.join(kinds)))
            seen.add(i)
    for name, hl, ol, end, extra in src.function_spans():
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        locals_ = {}
        if 'get_' in body:
            for line in body_lines:
                if 'get_' not in line:
                    continue
                for m in VALUE_LOCAL_RE.finditer(line):
                    locals_[m.group(1)] = UNSIGNED_TYPE_RE.search(m.group(0)) is not None
                for m in VALUE_ASSIGN_RE.finditer(line):
                    nm = m.group(1)
                    if nm in locals_:
                        continue
                    dm = re.search(INT_LOCAL_DECL_TEMPLATE % re.escape(nm), body)
                    if dm:
                        locals_[nm] = UNSIGNED_TYPE_RE.search(dm.group(1)) is not None
        if extra[1] == 'raw_op':
            for m in SIGNED_PARAM_RE.finditer(extra[3]):
                locals_[m.group(1)] = UNSIGNED_TYPE_RE.search(m.group(0)) is not None
        arith = None
        if locals_:
            alt = '|'.join(re.escape(n) for n in sorted(locals_))
            arith = re.compile(
                r'(?<![\w.>])(' + alt + r')\s*(?:[-+*]|<<)(?![-+=>])\s*[\w(]|[\w)\]]\s*(?:[-+*]|<<)(?![-+=>])\s*(' + alt
                + r')\b(?!\s*(?:\(|\.|->))|(?<![\w.>])(' + alt + r')\s*(?:[-+*]|<<)=|(?:^|[=(,?:])\s*-\s*(' + alt + r')\b')
        for j, line in enumerate(body_lines):
            ln = hl + j
            if ln in seen:
                continue
            if 'get_' in line:
                gm = GETTER_ARITH_RE.search(line)
                if gm:
                    getter = gm.group(1) or gm.group(2)
                    kind = 'an unsigned' if UNSIGNED_GETTER_RE.match(getter) else 'a signed'
                    out.append((ln, 'unchecked: arithmetic on %s value getter' % kind))
                    seen.add(ln)
                    continue
            if arith is not None:
                m = arith.search(line)
                if m and not VALUE_LOCAL_RE.search(line):
                    nm = next(g for g in m.groups() if g)
                    out.append((ln, 'unchecked: arithmetic on %s value %s' % ('unsigned' if locals_[nm] else 'signed', nm)))
                    seen.add(ln)
    return out


def post_checked_arithmetic(src, seen):
    out = []
    code = src.code
    for fname, hl, ol, end, extra in src.function_spans():
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        body_end = src.starts[end] + len(src.lines[end])
        for m in POST_CHECK_RE.finditer(code, open_off, body_end):
            args = split_call_args(code, m.end() - 1)
            if len(args) < 3 or not re.fullmatch(r'[A-Za-z_]\w*', args[-1]):
                continue
            res = args[-1]
            call_ln = src.line_of(m.start())
            asg = re.compile(r'(?<![\w.>])' + re.escape(res) + r'\s*=(?!=)\s*([^;]*)')
            for ln in range(call_ln, src.line_of(open_off) - 1, -1):
                am = asg.search(src.lines[ln])
                if not am:
                    continue
                if ln not in seen and BINARY_ARITH_RE.search(SUBSCRIPT_RE.sub(' ', am.group(1))):
                    out.append((ln, 'unchecked: arithmetic checked for overflow only afterwards by ' + m.group(1)))
                    seen.add(ln)
                break
    return out


def integer_overflow_raise(src, i, m):
    code = src.code
    direct, conds, parent, top, opener, parent_top = governing_conditions(
        code, statement_start(code, src.starts[i] + m.start()), outer=False)
    if not conds:
        return True
    text = direct if direct is not None else '\n'.join(conds)
    if FLOAT_CONDITION_RE.search(text):
        return False
    if LIMIT_CONST_RE.search(text) or OVERFLOW_HELPER_RE.search(text) or SIGNED_GETTER_RE.search(text):
        return True
    if BINARY_ARITH_RE.search(SUBSCRIPT_RE.sub(' ', text)):
        return True
    return not BUFFER_TERM_RE.search(text)


def split_groups(expr):
    flat = []
    groups = []
    depth = 0
    start = 0
    for i, c in enumerate(expr):
        if c == '(':
            if depth == 0:
                start = i
            depth += 1
        elif c == ')':
            if depth:
                depth -= 1
                if depth == 0:
                    text = ''.join(flat)
                    groups.append((len(text), expr[start + 1:i], re.search(r'[\w>\]]\s*$', text) is not None))
                    flat.append(' X ')
            continue
        elif depth == 0:
            flat.append(c)
    if depth:
        flat.append(' ' + expr[start + 1:])
    return ''.join(flat), groups


def float_hint(seg, float_re):
    return FLOAT_LITERAL_RE.search(seg) or FLOAT_SEGMENT_HINT_RE.search(seg) or (float_re is not None and float_re.search(seg))


def float_walk(expr, inherited, float_re):
    flat, groups = split_groups(expr)
    bounds = [0] + [m.end() for m in SEGMENT_SPLIT_RE.finditer(flat)] + [len(flat) + 1]
    segments = [(bounds[k], bounds[k + 1], flat[bounds[k]:bounds[k + 1]]) for k in range(len(bounds) - 1)]
    hints = [bool(inherited or float_hint(seg, float_re)) for _, _, seg in segments]
    for (a, b, seg), h in zip(segments, hints):
        if h and fusable(seg):
            return True
    for pos, inner, call in groups:
        h = False
        if not call:
            for (a, b, seg), sh in zip(segments, hints):
                if a <= pos < b:
                    h = sh
                    break
        if float_walk(inner, h, float_re):
            return True
    return False


def float_multiply_add(text, float_re):
    t = re.sub(r'\b(static_cast|reinterpret_cast)\s*<\s*(?:const\s+)?(double|float)\s*>', r'\1_\2', text)
    t = re.sub(r'\(\s*(?:const\s+)?(?:long\s+)?(double|float)\s*\)', r' c_cast_\1 ', t)
    return float_walk(t, False, float_re)


def fusable(seg):
    if '*' not in seg:
        return False
    compound = re.search(r'(?:\+|-)=(?!=)\s*(.*)$', seg)
    if compound:
        term = compound.group(1)
        if product_term(term) and not ADD_SPLIT_RE.search(term):
            return True
    rhs = re.split(r'(?<![=!<>+\-*/%&|^])=(?!=)', seg)[-1]
    terms = [x for x in ADD_SPLIT_RE.split(rhs) if x.strip()]
    return len(terms) >= 2 and any(product_term(x) for x in terms)


def product_term(term):
    if '/' in term or '%' in term:
        return False
    for m in PRODUCT_RE.finditer(term):
        before = re.search(r'([A-Za-z_]\w*)\s*$', term[:m.start() + 1])
        if before and before.group(1) in TYPE_WORDS:
            continue
        return True
    return False


def float_rows(src, ctx, member_float):
    out = []
    code = src.code
    if 'fma' in code or 'fm' in code:
        for i, line in enumerate(src.lines):
            if 'fm' not in line:
                continue
            m = FMA_CALL_RE.search(line)
            if m:
                out.append((i, 'fma call: ' + next(g for g in m.groups() if g)))
    if not in_dirs(src.path, FLOAT_DIRS):
        return out
    seen = set(i for i, _ in out)
    stem = src.stem()
    for name, hl, ol, end, extra in src.function_spans():
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        if '*' not in body:
            continue
        floats = set(re.findall(r'\b(?:double|float)\s+(?:const\s+)?&?\s*([A-Za-z_]\w*)', body))
        cls = enclosing_class(name)
        floats |= member_float.get(('class', cls), set()) | member_float.get(('stem', stem), set())
        float_re = re.compile(r'\b(?:' + '|'.join(re.escape(n) for n in sorted(floats)) + r')\b') if floats else None
        for j, line in enumerate(body_lines):
            ln = hl + j
            if ln in seen or '*' not in line:
                continue
            if ADDITIVE_RE.search(line) and float_multiply_add(line, float_re):
                out.append((ln, 'multiply-add on floating values'))
                seen.add(ln)
        open_off = src.body_open.get((name, hl))
        if open_off is None:
            continue
        end_off = src.starts[end] + len(src.lines[end])
        for sm in re.finditer(r'[^;{}]+', code[open_off + 1:end_off]):
            text = sm.group()
            if '\n' not in text.strip() or '*' not in text:
                continue
            first = open_off + 1 + sm.start() + (len(text) - len(text.lstrip()))
            a = src.line_of(first)
            b = src.line_of(open_off + 1 + sm.end() - 1)
            if any(k in seen for k in range(a, b + 1)):
                continue
            joined = ' '.join(text.split())
            if ADDITIVE_RE.search(joined) and float_multiply_add(joined, float_re):
                out.append((a, 'multiply-add on floating values (statement spans lines %d-%d)' % (a + 1, b + 1)))
                seen.update(range(a, b + 1))
    return out


def handoff_rows(src, ctx, stats):
    out = []
    code = src.code
    cross = ctx['cross_thread']
    if 'new' in code or 'OB_NEWx' in code:
        for i, line in enumerate(src.lines):
            if 'new' not in line and 'OB_NEWx' not in line:
                continue
            m = PLACEMENT_NEW_RE.search(line)
            if m and last_component(m.group(1)) in cross and m.group(2) and ALLOCATOR_ARG_RE.search(m.group(2)):
                out.append((i, 'cross-thread object constructed with an allocator: ' + last_component(m.group(1))))
                continue
            m = OB_NEWX_RE.search(line)
            if m and last_component(m.group(1)) in cross:
                out.append((i, 'cross-thread object placed in allocator memory: ' + last_component(m.group(1))))
    pools = ctx['thread_pools']
    pool_file = 'push' in code and any(enclosing_class(f[0]) in pools for f in src.function_spans())
    if not HANDOFF_CALL_RE.search(code) and not pool_file:
        return out
    for name, hl, ol, end, extra in src.function_spans():
        body_lines = src.lines[hl:end + 1]
        body = '\n'.join(body_lines)
        in_pool = pool_file and enclosing_class(name) in pools
        if not HANDOFF_CALL_RE.search(body) and not (in_pool and POOL_PUSH_RE.search(body)):
            continue
        open_off = src.body_open.get((name, hl))
        inner = code[open_off:src.starts[end] + len(src.lines[end])] if open_off is not None else body
        allocated = set(m.group(1) for m in ALLOC_ASSIGN_RE.finditer(inner))
        allocated |= set(m.group(1) for m in ALLOC_OUT_PARAM_RE.finditer(inner))
        for j, line in enumerate(body_lines):
            calls = []
            for m in HANDOFF_CALL_RE.finditer(line):
                verb = m.group(1) or m.group(2) or m.group(3)
                if verb in ('push', 'put'):
                    recv = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]|\(\s*\))?\s*$', line[:m.start()])
                    rname = recv.group(1).lower() if recv else ''
                    if verb == 'push' and not re.search(r'queue|_q_?$|task', rname):
                        continue
                    if verb == 'put' and 'cache' not in rname:
                        continue
                if m.group(3):
                    prefix = line[:m.start()]
                    if DEFINITION_PREFIX_RE.fullmatch(prefix) and prefix.split() and prefix.split()[0] not in NON_TYPE_WORDS:
                        continue
                calls.append((m, verb))
            if in_pool:
                for m in POOL_PUSH_RE.finditer(line):
                    if call_kind(src, hl + j, 'push', line[:m.start()], src.starts[hl + j] + m.end() - 1) == 'call':
                        calls.append((m, 'push inside a thread pool'))
            for m, verb in calls:
                stats['arena-handoff calls'] += 1
                args = balanced_call_args(src.code, src.starts[hl + j] + m.end() - 1)
                idents = set(re.findall(r'[A-Za-z_]\w*', args))
                hit = sorted(idents & allocated)
                if hit:
                    out.append((hl + j, 'hand-off of allocator-backed object ' + hit[0] + ' via ' + verb))
                    break
                if ALLOCATOR_ARG_RE.search(args):
                    out.append((hl + j, 'hand-off with an allocator argument via ' + verb))
                    break
    return out


def view_out_param(param, ctx):
    p = re.sub(r'\s+', ' ', param.split('=', 1)[0]).strip()
    if RAW_BYTES_OUT_RE.search(p):
        return True
    m = ctx['view_ref_param_re'].search(p)
    if not m:
        return False
    return re.search(r'\bconst\b', p[:m.start(1)]) is None


def view_function_rows(src, ctx):
    out = []
    code = src.code
    alloc_re = ctx['alloc_param_re']
    if not alloc_re.search(code):
        return out
    for name, hl, ol, end, extra in src.function_spans():
        h, simple, rettype, args = extra
        if not alloc_re.search(args):
            continue
        outs = [p for p in split_top(args) if view_out_param(p, ctx)]
        ret_view = ctx['view_type_re'].search(rettype) is not None or RAW_BYTES_RE.match(rettype.replace('static ', '').replace('inline ', '').strip()) is not None
        if outs:
            out.append((src.name_line(name, hl), 'allocator in, arena view out: ' + re.sub(r'\s+', ' ', outs[0]).strip()))
        elif ret_view:
            out.append((src.name_line(name, hl), 'allocator in, arena view returned: ' + re.sub(r'\s+', ' ', rettype).strip()))
    return out


def macro_invocation_rows(src, ctx):
    out = []
    macros = ctx['subclass_macros']
    if not macros:
        return out
    code = src.code
    for name, (index, base) in macros.items():
        if name not in code:
            continue
        for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', code):
            ln = src.line_of(m.start())
            if src.dkind.get(ln) in ('define', 'cont'):
                continue
            args = split_top(balanced_call_args(code, m.end() - 1))
            cname = args[index].strip() if index < len(args) else '?'
            outer = src.symbol(ln)
            owner = outer if outer != '-' and not outer.startswith('#define') and outer not in src.function_names() else ''
            symbol = (owner + '::' + cname) if owner and cname != '?' else (cname if cname != '?' else outer)
            out.append((ln, 'direct subclass of %s via macro %s (%s)%s' % (base, name, cname, base_note(base)), symbol))
    return out


def base_note(base):
    return '' if base in PLAN_SUBCLASS_BASES else ' [base named in the evidence, not in the plan list]'


def count_macro_uses(src, ctx, counts, sites):
    rx = ctx['count_macro_re']
    if rx is None or not rx.search(src.code):
        return
    site_macros = ctx['site_macros']
    seen = set()
    for m in rx.finditer(src.code):
        name = m.group(1)
        ln = src.line_of(m.start())
        if src.dkind.get(ln) in ('define', 'cont') and src.dname.get(ln) == name:
            continue
        if ctx['count_macro_function_like'].get(name) and not src.code[m.end():].lstrip().startswith('('):
            continue
        if (name, ln) in seen:
            continue
        seen.add((name, ln))
        counts[name][0] += 1
        counts[name][1].add(src.path)
        if name in site_macros:
            sites.append((name, src.path, ln + 1, src.symbol(ln), src.text(ln), src.flag))


def pass_b(repo, paths):
    ctx = _CTX
    results = collections.defaultdict(list)
    stats = collections.Counter()
    accesses = []
    macro_counts = collections.defaultdict(lambda: [0, set()])
    macro_sites = []
    member_hash = ctx['member_hash']
    member_ir = ctx['member_ir']
    member_float = ctx['member_float']
    for path in paths:
        src = read_source(repo, path)
        accesses += atomic_access_records(src, ctx)
        count_macro_uses(src, ctx, macro_counts, macro_sites)
        per = {
            'refcount': refcount_rows(src, ctx),
            'const-cast': const_cast_rows(src, ctx),
            'ret-compare': ret_compare_rows(src, ctx['errnos'], stats),
            'reset': reset_rows(src),
            'tmp-ret': tmp_ret_rows(src, stats),
            'ret-alias': ret_alias_rows(src),
            'oom-sites': oom_rows(src, ctx),
            'sort-hash-order': sort_hash_rows(src, ctx, member_hash),
            'pointer-identity': pointer_identity_rows(src, ctx, member_ir),
            'overflow': overflow_rows(src, ctx),
            'float-contraction': float_rows(src, ctx, member_float),
            'arena-handoff': handoff_rows(src, ctx, stats),
            'borrowed-views': view_function_rows(src, ctx),
            'subclassed-bases': macro_invocation_rows(src, ctx),
            'lock-guards': lock_guard_rows(src, ctx, stats),
            'printf-calls': printf_rows(src, ctx, stats),
            'fast-parser': fast_parser_rows(src, ctx, stats),
            'sizeof-formulas': sizeof_rows(src, ctx, stats),
            'work-area-formulas': work_area_rows(src, ctx),
            'memmove': memmove_rows(src, ctx),
            'frame-internals': frame_internal_rows(src, ctx, stats),
            'storage-sql-uses': storage_sql_rows(src, ctx, stats),
            'batch-end-tracepoints': tracepoint_rows(src, ctx, stats),
            'local-expr-factories': expr_factory_decl_rows(src, ctx),
            'null-arrays': null_array_rows(src, ctx, stats),
            'expr-factory-callers': expr_factory_caller_rows(src, ctx),
            'warning-buffer-sites': warning_buffer_rows(src, ctx),
        }
        for cat, items in per.items():
            for item in items:
                ln, construct = item[0], item[1]
                sym = item[2] if len(item) > 2 else src.symbol(ln)
                results[cat].append((path, ln + 1, sym, construct + src.suffix, src.text(ln)))
    return dict(results), stats, accesses, {k: (v[0], frozenset(v[1])) for k, v in macro_counts.items()}, macro_sites


def chunked(paths, n):
    size = max(1, (len(paths) + n * 4 - 1) // (n * 4))
    return [paths[i:i + size] for i in range(0, len(paths), size)]


def set_not_built(names):
    global _NOT_BUILT
    _NOT_BUILT = names


def set_ctx(ctx):
    global _CTX
    _CTX = ctx
    set_not_built(ctx['not_built'])


def run_parallel(fn, repo, paths, jobs, ctx=None, init=None):
    init = init or init_worker
    chunks = chunked(paths, jobs)
    results = []
    if jobs <= 1:
        if ctx is not None:
            init(ctx)
        for c in chunks:
            results.append(fn(repo, c))
        return results
    kwargs = {'max_workers': jobs}
    if ctx is not None:
        kwargs['initializer'] = init
        kwargs['initargs'] = (ctx,)
    else:
        kwargs['initializer'] = set_not_built
        kwargs['initargs'] = (_NOT_BUILT,)
    with concurrent.futures.ProcessPoolExecutor(**kwargs) as ex:
        futures = [ex.submit(fn, repo, c) for c in chunks]
        for f in futures:
            results.append(f.result())
    return results


def include_scan(repo, paths):
    out = {}
    for path in paths:
        with open(os.path.join(repo, path), 'rb') as fh:
            text = fh.read().decode('utf-8', 'replace')
        out[path] = ANY_INCLUDE_RE.findall(text)
    return out


def inventory_sources(repo):
    tool = os.path.join(repo, INVENTORY_TOOL)
    if not os.path.exists(tool):
        return None
    spec = importlib.util.spec_from_file_location('seekdb_source_inventory', tool)
    mod = importlib.util.module_from_spec(spec)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            spec.loader.exec_module(mod)
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, 'inventory.cmake')
                mod.emit(pathlib.Path(repo), pathlib.Path(out))
                with open(out, encoding='utf-8') as fh:
                    text = fh.read()
    except Exception as exc:
        print('warning: %s failed (%s); no file is flagged not built' % (INVENTORY_TOOL, exc), file=sys.stderr)
        return None
    return set(re.findall(r'\$\{CMAKE_SOURCE_DIR\}/(src/[^"\s)]+)', text))


def cmake_sources(repo):
    listed = set()
    out = subprocess.run(['git', '-C', repo, 'ls-files', '-z', '--', 'CMakeLists.txt', 'src'], check=True,
                         capture_output=True).stdout
    for p in out.decode('utf-8', 'surrogateescape').split('\0'):
        if not p.endswith('CMakeLists.txt'):
            continue
        d = posixpath.dirname(p)
        with open(os.path.join(repo, p), encoding='utf-8', errors='replace') as fh:
            text = re.sub(r'#[^\n]*', ' ', fh.read())
        for m in CMAKE_SOURCE_RE.finditer(text):
            listed.add(posixpath.normpath(posixpath.join(d, m.group(1))))
        for m in CMAKE_DIR_SOURCE_RE.finditer(text):
            listed.add(posixpath.normpath(posixpath.join(d, m.group(1))))
        for m in CMAKE_ROOT_SOURCE_RE.finditer(text):
            listed.add(posixpath.normpath(m.group(1)))
    return listed


def include_graph(repo, paths, jobs):
    include_map = {}
    for part in run_parallel(include_scan, repo, paths, jobs):
        include_map.update(part)
    by_suffix = collections.defaultdict(list)
    for p in paths:
        parts = p.split('/')
        for k in range(len(parts)):
            by_suffix['/'.join(parts[k:])].append(p)
    targets = {}
    for p, incs in include_map.items():
        ts = set()
        for inc in incs:
            ts.update(by_suffix.get(posixpath.normpath(inc), ()))
        targets[p] = frozenset(ts)
    return targets


def unbuilt_files(repo, paths, targets):
    compiled = inventory_sources(repo)
    if compiled is None:
        return frozenset()
    pset = set(paths)
    compiled = (compiled | cmake_sources(repo)) & pset
    included = set()
    for ts in targets.values():
        included |= ts
    reach = set(compiled)
    todo = list(compiled)
    while todo:
        for q in targets.get(todo.pop(), ()):
            if q not in reach:
                reach.add(q)
                todo.append(q)
    return frozenset(p for p in paths if p not in reach and (p.endswith(SOURCE_EXTS) or p in included))


def load_errnos(repo):
    names = set()
    for rel in ('src/oblib/lib/ob_errno.h', 'src/share/ob_errno.h'):
        p = os.path.join(repo, rel)
        if os.path.exists(p):
            with open(p, encoding='utf-8', errors='replace') as fh:
                for m in re.finditer(r'constexpr\s+int\s+(OB_\w+)\s*=\s*-\d+', fh.read()):
                    names.add(m.group(1))
    p = os.path.join(repo, 'src/share/ob_errno.def')
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            for m in re.finditer(r'^DEFINE_\w+\(\s*(OB_\w+)', fh.read(), re.M):
                names.add(m.group(1))
    p = os.path.join(repo, 'src/sql/parser/parse_define.h')
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            for m in re.finditer(r'\bconst\s+int(?:32_t)?\s+(OB_PARSER_\w+)\s*=\s*-\d+', fh.read()):
                names.add(m.group(1))
    return names


def descendants(classes, roots):
    children = collections.defaultdict(set)
    for c in classes:
        for b in c[5]:
            children[last_component(b)].add(c[1])
    seen = set(roots)
    todo = list(roots)
    while todo:
        x = todo.pop()
        for ch in children.get(x, ()):
            if ch not in seen:
                seen.add(ch)
                todo.append(ch)
    return seen


def flag_suffix(flag):
    return ' [' + flag + ']' if flag else ''


def type_words(typ):
    t = re.sub(r'\b(?:const|volatile|static|mutable|struct|class|unsigned|signed|typename|inline|constexpr)\b', ' ', typ)
    return set(last_component(w) for w in re.findall(r'[A-Za-z_][\w:]*', t))


def op_histogram(accs):
    hist = collections.Counter(a.op for a in accs)
    return ', '.join('%s %d' % (k, v) for k, v in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])))


def via_note(accs):
    groups = collections.defaultdict(set)
    for a in accs:
        if not a.via:
            continue
        for part in a.via.split(' | '):
            kind, _, what = part.partition(':')
            if not what:
                kind, what = 'macro', part
            groups[kind].add(what)
    aliases = sorted(set(a.local[1] for a in accs if a.local and a.local[0] == 'alias'))
    labels = (('macro', 'macros'), ('method', 'member functions'), ('function', 'functions with an atomic parameter'),
              ('accessor', 'accessors'), ('value', 'the single member of value classes'), ('pointer', 'pointer members'))
    note = ''
    for kind, label in labels:
        names = sorted(groups.get(kind, ()))
        if names:
            note += '; through %s %s%s' % (label, ', '.join(names[:4]), (' and %d more' % (len(names) - 4)) if len(names) > 4 else '')
    if aliases:
        note += '; through local references ' + ', '.join(aliases[:4])
    return note


def add_via(acc, via):
    return acc._replace(via=(acc.via + ' | ' + via) if acc.via else via)


VALUE_WRAPPER_RE = re.compile(r'atomic_\w+|inc_update|dec_update')


class AtomicModel:
    def __init__(self, members, classes, aliases, include_targets, class_tparams, accessors):
        self.bases = collections.defaultdict(set)
        self.chains = collections.defaultdict(set)
        keyword = {}
        for c in classes:
            chain = strip_template_args(c[0])
            keyword[chain] = c[2]
            self.chains[c[1]].add(chain)
            for b in c[5]:
                self.bases[c[1]].add(last_component(b))
        class_owned = collections.defaultdict(set)
        externs = set()
        for mem in members:
            if mem[5] == 'class' and mem[0]:
                class_owned[mem[1]].add(last_component(mem[0]))
            elif mem[5] == 'ns' and re.search(r'\bextern\b', mem[2]):
                externs.add((last_component(mem[0]) if mem[0] else '', mem[1]))
        self.decls = collections.defaultdict(list)
        self.merged = set()
        self.member_types = {}
        data = collections.defaultdict(lambda: [0, 0])
        for mem in members:
            owner, nm, typ, path, ln, scope, tags, text, stem, mflag = mem
            if scope == 'ns' and owner and last_component(owner) in class_owned.get(nm, ()):
                self.merged.add((path, ln, owner, nm))
                continue
            if scope == 'ns' and not re.search(r'\bextern\b', typ) and (last_component(owner) if owner else '', nm) in externs:
                self.merged.add((path, ln, owner, nm))
                continue
            self.decls[nm].append(mem)
            if scope == 'class' and owner:
                self.member_types.setdefault((last_component(owner), nm), typ)
                if not re.search(r'\b(?:static|constexpr)\b', typ):
                    data[strip_template_args(owner)][1 if 'anon' in tags else 0] += 1
        for nm in self.decls:
            self.decls[nm].sort(key=lambda d: (d[3], d[4], d[0]))
        self.value_classes = set()
        for owner, (plain, anon) in data.items():
            if self.bases.get(last_component(owner)):
                continue
            if keyword.get(owner) == 'union' or (plain == 1 and anon == 0) or (plain == 0 and anon > 0):
                self.value_classes.add(owner)
        self.aliases = collections.defaultdict(list)
        for alias, target, path, ln, text, flag, chain in aliases:
            self.aliases[alias].append((outer_type(target), path, path.rsplit('.', 1)[0], last_component(chain) if chain else ''))
        self.include_targets = include_targets
        self.closures = {}
        self.class_tparams = class_tparams
        self.accessors = collections.defaultdict(list)
        for rec in accessors:
            self.accessors[rec[0]].append(rec)
        self.lift_log = collections.Counter()

    def reachable(self, path):
        if path not in self.closures:
            seen = {path}
            todo = [path]
            while todo:
                for q in self.include_targets.get(todo.pop(), ()):
                    if q not in seen:
                        seen.add(q)
                        todo.append(q)
            self.closures[path] = seen
        return self.closures[path]

    def value_owner(self, owner):
        return strip_template_args(owner) in self.value_classes if owner else False

    def expand(self, word, acc, depth=0):
        if depth > 5 or word not in self.aliases:
            return {word}
        seen = self.reachable(acc.path)
        picks = [a for a in self.aliases[word] if a[1] == acc.path or a[2] == acc.stem or (acc.cls and a[3] == acc.cls)]
        if not picks:
            picks = [a for a in self.aliases[word] if a[1] in seen]
        targets = set(a[0] for a in picks if a[0] and a[0] != word)
        if len(targets) != 1:
            return {word}
        return self.expand(targets.pop(), acc, depth + 1)

    def owner_words(self, typ, acc, owner_chain=None):
        drop = set(acc.tparams)
        if owner_chain:
            drop |= self.class_tparams.get(strip_template_args(owner_chain), frozenset())
        words = set()
        for w in type_words(typ):
            if w in drop:
                continue
            words |= self.expand(w, acc)
        return words - drop

    def nearby(self, cands, acc):
        local = [d for d in cands if d[3] == acc.path or d[8] == acc.stem]
        if local:
            return local
        incs = set(self.include_targets.get(acc.path, ()))
        for ext in ('.h', '.hpp', '.ipp'):
            incs |= set(self.include_targets.get(acc.stem + ext, ()))
        return [d for d in cands if d[3] in incs]

    def pick(self, cands, acc):
        near = self.nearby(cands, acc)
        return (near or cands)[0]

    def resolve_decl(self, acc):
        cands = self.decls.get(acc.name, [])
        if acc.flag != 'vendored':
            cands = [d for d in cands if d[9] != 'vendored']
        if not cands:
            return None, 0
        if acc.qual:
            q = last_component(acc.qual)
            own = [d for d in cands if d[0] and last_component(d[0]) == q]
            if own:
                return self.pick(own, acc), len(cands)
        if acc.self_ref:
            cls = acc.cls
            if cls:
                for c in ancestors(self.bases, cls):
                    own = [d for d in cands if d[5] == 'class' and d[0] and last_component(d[0]) == c]
                    if own:
                        return self.pick(own, acc), len(cands)
            glob = [d for d in cands if d[5] == 'ns']
            near = self.nearby(glob, acc)
            if len(near) == 1:
                return near[0], len(cands)
            if not cls and len(glob) == 1:
                return glob[0], len(cands)
            return None, len(cands)
        prev = None
        for nm in reversed(acc.chain[:-1]):
            if nm not in ELEMENT_ACCESSORS:
                prev = nm
                break
        if prev is not None:
            owners = set()
            if prev == acc.chain[0] and acc.root_type:
                owners |= self.owner_words(acc.root_type, acc)
            for d in self.decls.get(prev, []):
                owners |= self.owner_words(d[2], acc, d[0])
            if owners:
                expanded = set()
                for o in owners:
                    expanded |= set(ancestors(self.bases, o))
                own = [d for d in cands if d[0] and last_component(d[0]) in expanded]
                reach = self.reachable(acc.path)
                if len(own) > 1 or len(own) == 1 and own[0][3] not in reach:
                    visible = [d for d in own if d[3] in reach or d[8] == acc.stem]
                    own = visible or own
                if len(own) == 1:
                    return own[0], len(cands)
                if len(own) > 1:
                    near = self.nearby(own, acc)
                    if len(near) == 1:
                        return near[0], len(cands)
        if len(cands) == 1:
            return cands[0], 1
        near = self.nearby(cands, acc)
        if len(near) == 1:
            return near[0], len(cands)
        return None, len(cands)

    def through_accessor(self, acc):
        getter = acc.name[:-2]
        recs = self.accessors.get(getter)
        if not recs:
            return None
        owners = set()
        if len(acc.chain) == 1 and acc.cls:
            owners = set(ancestors(self.bases, acc.cls))
        elif len(acc.chain) == 2 and acc.root_type:
            for w in self.owner_words(acc.root_type, acc):
                owners |= set(ancestors(self.bases, w))
        typed = [r for r in recs if r[1] in owners]
        recs = typed or recs
        found = set()
        for simple, cls, path, stem, obj, root_type, tparams, kind in recs:
            name, qual, chain, self_ref = obj[:4]
            probe = Access(name, qual, tuple(chain), self_ref, acc.op, acc.via, path, acc.ln, acc.symbol, cls, stem, acc.text,
                           acc.flag, None, root_type, False, None, tparams, obj[4] if len(obj) > 4 else None)
            d, _ = self.resolve_decl(probe)
            if d is None:
                return None
            found.add((d[3], d[4], d[0], d[1]))
        if len(found) != 1:
            return None
        return found.pop()

    def lift(self, acc, decl):
        if acc.seps is None or len(acc.seps) != len(acc.chain) or acc.seps[-1] != '.':
            return None
        chain = acc.chain[:-1]
        seps = acc.seps[:-1]
        while chain and chain[-1] in ELEMENT_ACCESSORS:
            if seps[-1] != '.':
                return None
            chain = chain[:-1]
            seps = seps[:-1]
        if not chain:
            return None
        name = chain[-1]
        self_ref = len(chain) == 1 and not acc.qual
        lifted = acc._replace(name=name, chain=tuple(chain), self_ref=self_ref, local=None, seps=tuple(seps))
        lifted = add_via(lifted, 'value:%s::%s' % (last_component(decl[0]), decl[1]))
        if self_ref and acc.root_local is not None:
            kind, func, typ, decl_ln, text, init, index = acc.root_local
            if kind == 'alias':
                target = access_object(init.lstrip()) if init else None
                if target is not None and target[0] not in LITERAL_OPERANDS:
                    lifted = lifted._replace(name=target[0], qual=target[1], chain=tuple(target[2]), self_ref=target[3],
                                             local=('alias', chain[0], func), seps=target[4])
                else:
                    lifted = lifted._replace(local=('local', func, typ, decl_ln, text))
            else:
                lifted = lifted._replace(local=(kind, func, typ, decl_ln, text))
        return lifted

    def resolve(self, acc, depth=0):
        if acc.local is not None and acc.local[0] in ('param', 'static', 'local'):
            return 'local', acc
        if acc.name.endswith('()'):
            key = self.through_accessor(acc)
            if key is not None:
                return 'decl', (key, add_via(acc, 'accessor:' + acc.name[:-2]))
            return 'unresolved', (0, acc)
        d, ncand = self.resolve_decl(acc)
        if d is None:
            return 'unresolved', (ncand, acc)
        if depth < 4 and d[5] == 'class' and self.value_owner(d[0]) and len(acc.chain) >= 2 \
                and not re.search(r'\bstatic\b', d[2]):
            lifted = self.lift(acc, d)
            if lifted is not None:
                kind, res = self.resolve(lifted, depth + 1)
                if kind != 'unresolved':
                    self.lift_log[(d[3], d[4], d[0], d[1])] += 1
                    return kind, res
        return 'decl', ((d[3], d[4], d[0], d[1]), acc)


def pointer_type(typ):
    t = drop_template_args(typ)
    return '*' in t and '[' not in t


def param_effect(acc):
    kind, func, typ, decl_ln, text = acc.local
    root = acc.root_local
    index = root[6] if root is not None else -1
    if index < 0:
        return None
    t = drop_template_args(typ)
    if acc.deref and ('*' in t or '&' in t):
        return index, 'pointee'
    if not acc.deref and '&' in t:
        return index, 'object'
    return None


def wrapper_tables(resolved, model, func_defs):
    methods = collections.defaultdict(set)
    functions = collections.defaultdict(set)
    for kind, res in resolved:
        if kind == 'decl':
            key, acc = res
            owner = key[2]
            if acc.self_ref and len(acc.chain) == 1 and owner and model.value_owner(owner) and acc.cls == last_component(owner):
                method = last_component(acc.symbol) if acc.symbol and acc.symbol != '-' else ''
                if VALUE_WRAPPER_RE.fullmatch(method):
                    methods[(acc.cls, method)].add(acc.op)
        elif kind == 'local' and acc_is_param(res):
            eff = param_effect(res)
            if eff is None:
                continue
            func = res.local[1]
            simple = last_component(func)
            cls = enclosing_class(func)
            functions[(cls, simple)].add((eff[0], eff[1], res.op))
    by_name = collections.Counter(n for _, n in functions)
    specific = set(n for n, k in by_name.items() if k >= 0.8 * max(1, func_defs.get(n, 0)))
    method_names = collections.Counter(n for _, n in methods)
    specific_methods = set(n for n, k in method_names.items() if k >= 0.8 * max(1, func_defs.get(n, 0)))
    return ({k: frozenset(v) for k, v in methods.items()}, {k: frozenset(v) for k, v in functions.items()},
            frozenset(specific), frozenset(specific_methods))


def acc_is_param(acc):
    return acc.local is not None and acc.local[0] == 'param'


def expr_type(src, fdef, cls, chain, member_types, bases):
    if not chain:
        return set()
    typ = None
    if fdef is not None:
        info = find_local(src, fdef, chain[0], src.starts[fdef[3]] + len(src.lines[fdef[3]]))
        if info is not None:
            typ = info[1]
    if typ is None and cls:
        for a in ancestors(bases, cls):
            typ = member_types.get((a, chain[0]))
            if typ:
                break
    if typ is None:
        return set()
    words = type_words(typ)
    for comp in chain[1:]:
        nxt = None
        for w in sorted(words):
            for a in ancestors(bases, w):
                nxt = member_types.get((a, comp))
                if nxt:
                    break
            if nxt:
                break
        if nxt is None:
            return set()
        words = type_words(nxt)
    return words


def wrapper_calls(src, ctx):
    out = []
    code = src.code
    names_re = ctx['wrapper_name_re']
    if names_re is None or not names_re.search(code):
        return out
    methods = ctx['member_wrappers']
    functions = ctx['function_wrappers']
    specific = ctx['specific_functions']
    specific_methods = ctx['specific_methods']
    member_types = ctx['member_types']
    bases = ctx['class_bases_all']
    for m in names_re.finditer(code):
        name = m.group(1)
        ln = src.line_of(m.start())
        if src.dkind.get(ln) in ('define', 'cont'):
            continue
        before = code[max(0, m.start() - 200):m.start()]
        rm = re.search(r'(\.|->)\s*$', before)
        qm = re.search(r'([A-Za-z_]\w*)\s*::\s*$', before)
        paren = m.end() - 1
        fdef = enclosing_function(src, ln)
        cls = enclosing_class(fdef[0]) if fdef is not None else enclosing_class_at(src, ln)
        if fdef is not None and fdef[1] <= ln and src.body_open.get((fdef[0], fdef[1]), 1 << 60) > m.start():
            continue
        if fdef is None:
            continue
        receiver = None
        types = set()
        if rm:
            em = OPERAND_END_RE.search(before[:rm.start()])
            if not em:
                continue
            receiver = em.group(1).strip()
            obj = access_object(receiver)
            if obj is not None and not obj[1]:
                types = expr_type(src, fdef, cls, obj[2], member_types, bases)
            owners = set()
            for t in types:
                owners |= set(ancestors(bases, t))
        elif qm:
            owners = set(ancestors(bases, qm.group(1))) | {''}
        else:
            owners = set(ancestors(bases, cls)) | {''} if cls else {''}
        args = split_call_args(code, paren)
        if rm and rm.group(1) == '.':
            ops = set()
            if types:
                for o in owners:
                    ops |= methods.get((o, name), frozenset())
            elif name in specific_methods:
                for (o, n), v in methods.items():
                    if n == name:
                        ops |= v
            if ops and receiver:
                obj = access_object(receiver)
                if obj is not None and obj[0] not in LITERAL_OPERANDS:
                    for op in sorted(ops):
                        out.append(make_access(src, obj, op, 'method:%s' % name, ln, m.start(), False))
        effects = set()
        if types or not rm:
            for o in owners:
                effects |= functions.get((o, name), frozenset())
        if not effects and name in specific:
            for (o, n), v in functions.items():
                if n == name:
                    effects |= v
        for index, effect, op in sorted(effects):
            if index >= len(args):
                continue
            arg = args[index]
            obj = access_object(arg)
            if obj is None or obj[0] in LITERAL_OPERANDS or re.fullmatch(r'[A-Z][A-Z0-9_]*', obj[0]):
                continue
            has_amp = strip_casts(arg).lstrip().startswith('&')
            deref = effect == 'pointee' and not has_amp
            if effect == 'object' and has_amp:
                continue
            out.append(make_access(src, obj, op, 'function:%s' % name, ln, m.start(), deref))
    return out


def pointer_assignments(src, ctx):
    out = []
    code = src.code
    rx = ctx['pointer_name_re']
    if rx is None or not rx.search(code):
        return out
    owners = ctx['pointer_owners']
    for m in rx.finditer(code):
        name = m.group(1)
        ln = src.line_of(m.start())
        before = code[max(0, m.start() - 40):m.start()]
        if re.search(r'[\w.]\s*$', before) and not re.search(r'this\s*->\s*$', before):
            continue
        after = code[m.end():m.end() + 400]
        am = re.match(r'\s*=(?!=)\s*([^;]+);', after) or re.match(r'\s*\(\s*(&[^;{}]*?)\)\s*[,{]', after)
        if not am:
            continue
        rhs = am.group(1).strip()
        if not strip_casts(rhs).lstrip().startswith('&'):
            continue
        fdef = enclosing_function(src, ln)
        cls = enclosing_class(fdef[0]) if fdef is not None else enclosing_class_at(src, ln)
        if cls not in owners.get(name, ()):
            continue
        obj = access_object(rhs)
        if obj is None or obj[0] in LITERAL_OPERANDS:
            continue
        acc = make_access(src, obj, 'pointer', 'pointer:%s::%s' % (cls, name), ln, m.start(), False)
        out.append((cls, name, acc))
    return out


def pass_c(repo, paths):
    ctx = _CTX
    accesses = []
    assignments = []
    probes = [rx for rx in (ctx['wrapper_name_re'], ctx['pointer_name_re']) if rx is not None]
    for path in paths:
        with open(os.path.join(repo, path), 'rb') as fh:
            raw = fh.read().decode('utf-8', 'replace')
        if not any(rx.search(raw) for rx in probes):
            continue
        src = read_source(repo, path)
        accesses += wrapper_calls(src, ctx)
        assignments += pointer_assignments(src, ctx)
    return accesses, assignments


def atomic_field_rows(accesses, members, model, macro_locals, pointer_links):
    model.lift_log.clear()
    by_decl = collections.defaultdict(list)
    by_local = collections.defaultdict(list)
    unresolved = collections.defaultdict(list)
    local_text = {}
    for acc in accesses:
        kind, res = model.resolve(acc)
        if kind == 'local':
            a = res
            lkind, func, typ, decl_ln, decl_text = a.local
            by_local[(a.path, decl_ln + 1, func, a.chain[0], lkind, typ, a.flag)].append(a)
            local_text[(a.path, decl_ln + 1)] = decl_text
        elif kind == 'decl':
            key, a = res
            by_decl[key].append(a)
        else:
            ncand, a = res
            unresolved[a.name].append((a, ncand))
    pointee_notes = collections.defaultdict(list)
    for pkey, targets in pointer_links.items():
        accs = by_decl.get(pkey)
        if not accs:
            continue
        derefs = [a for a in accs if a.deref]
        for tkey, where in targets:
            for a in derefs:
                by_decl[tkey].append(add_via(a, 'pointer:%s::%s' % (last_component(pkey[2]), pkey[3])))
            pointee_notes[pkey].append('%s::%s (assigned at %s)' % (tkey[2], tkey[3], where))
    rows = []
    member_index = {(m[3], m[4], m[0], m[1]): m for m in members}
    for key, accs in by_decl.items():
        m = member_index[key]
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        kind = 'field' if scope == 'class' else 'global'
        note = ''
        if model.value_owner(owner) and scope == 'class' and model.lift_log.get(key):
            note = '; single member of a value class: %d more accesses reach it through fields and variables of type %s, ' \
                   'listed where their own accesses resolve' % (model.lift_log[key], last_component(owner))
        if key in pointee_notes:
            note += '; pointer whose pointee is the atomic object: ' + ', '.join(sorted(set(pointee_notes[key]))[:3])
        rows.append((path, ln, (owner + '::' + nm) if owner else nm,
                     '%s %s; %d atomic accesses (%s)%s%s%s' % (kind, re.sub(r'\s+', ' ', typ), len(accs), op_histogram(accs),
                                                              via_note(accs), note, flag_suffix(mflag)), text))
    for key, n in sorted(model.lift_log.items()):
        if key in by_decl:
            continue
        m = member_index[key]
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        rows.append((path, ln, owner + '::' + nm, 'field %s; single member of a value class; no direct atomic access, %d atomic '
                     'accesses reach it through fields and variables of type %s, listed where their own accesses resolve%s'
                     % (re.sub(r'\s+', ' ', typ), n, last_component(owner), flag_suffix(mflag)), text))
    for key, accs in by_local.items():
        path, ln, func, nm, kind, typ, flag = key
        if kind == 'param':
            what = 'atomic access through parameter %s (%s) of %s; the atomic object is the caller\'s argument' % (nm, typ, func)
            sym = func
        elif kind == 'static':
            what = 'function-local static %s in %s' % (typ, func)
            sym = func + '::' + nm
        else:
            what = 'local variable %s in %s' % (typ, func)
            sym = func + '::' + nm
        rows.append((path, ln, sym, '%s; %d atomic accesses (%s)%s%s' % (what, len(accs), op_histogram(accs), via_note(accs),
                                                                     flag_suffix(flag)), local_text.get((path, ln), None)))
    macro_local_groups = collections.defaultdict(list)
    for path, ln, macro, nm, op, flag, text in macro_locals:
        macro_local_groups[(path, ln + 1, macro, nm, flag, text)].append(op)
    for (path, ln, macro, nm, flag, text), ops in macro_local_groups.items():
        hist = ', '.join('%s %d' % (k, v) for k, v in sorted(collections.Counter(ops).items(), key=lambda kv: (-kv[1], kv[0])))
        rows.append((path, ln, '#define ' + macro, 'static or local %s declared inside #define %s; %d atomic accesses in the body (%s)%s'
                     % (nm, macro, len(ops), hist, flag_suffix(flag)), text))
    for name, lst in unresolved.items():
        lst.sort(key=lambda x: (x[0].path, x[0].ln))
        acc, ncand = lst[0]
        accs = [a for a, _ in lst]
        reason = 'no declaration found' if ncand == 0 else '%d candidate declarations, none chosen' % ncand
        files = len(set(a.path for a in accs))
        rows.append((acc.path, acc.ln, name, '%s; %d atomic accesses in %d files (%s)%s; first access shown%s'
                     % (reason, len(accs), files, op_histogram(accs), via_note(accs), flag_suffix(acc.flag)), acc.text))
    keyed = set(by_decl) | model.merged
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        if 'volatile' in tags and (path, ln, owner, nm) not in keyed:
            kind = 'field' if scope == 'class' else 'global'
            rows.append((path, ln, (owner + '::' + nm) if owner else nm,
                         'volatile %s %s; no atomic access found%s' % (kind, re.sub(r'\s+', ' ', typ), flag_suffix(mflag)), text))
    attributed = [a for accs in by_decl.values() for a in accs] + [a for accs in by_local.values() for a in accs]
    attributed += [a for lst in unresolved.values() for a, _ in lst]
    counts = (len(by_decl), len(by_local), len(unresolved), sum(len(v) for v in unresolved.values()), via_kinds(attributed))
    return rows, counts, by_decl


def resolve_atomic(repo, paths, jobs, base_ctx, accesses, members, model, func_defs, macro_locals):
    all_accesses = list(accesses)
    seen_methods = set()
    seen_functions = set()
    pointer_links = collections.defaultdict(list)
    seen_pointers = set()
    for _ in range(4):
        resolved = [model.resolve(a) for a in all_accesses]
        methods, functions, specific, specific_methods = wrapper_tables(resolved, model, func_defs)
        new_methods = {k: v for k, v in methods.items() if (k, v) not in seen_methods}
        new_functions = {k: v for k, v in functions.items() if (k, v) not in seen_functions}
        pointers = collections.defaultdict(set)
        for kind, res in resolved:
            if kind == 'decl' and res[1].deref:
                key = res[0]
                d = model_member(model, key)
                if d is not None and d[5] == 'class' and pointer_type(d[2]) and key not in seen_pointers:
                    pointers[d[1]].add(last_component(d[0]))
                    seen_pointers.add(key)
        if not new_methods and not new_functions and not pointers:
            break
        seen_methods |= set(new_methods.items())
        seen_functions |= set(new_functions.items())
        names = sorted(set(n for _, n in new_methods) | set(n for _, n in new_functions))
        ctx = dict(base_ctx)
        ctx['member_types'] = model.member_types
        ctx['class_bases_all'] = {k: frozenset(v) for k, v in model.bases.items()}
        ctx['member_wrappers'] = methods
        ctx['function_wrappers'] = functions
        ctx['specific_functions'] = specific
        ctx['specific_methods'] = specific_methods
        ctx['wrapper_name_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in names) + r')\s*(?:<[^<>;(){}]*>)?\s*\(') \
            if names else None
        ctx['pointer_owners'] = {k: frozenset(v) for k, v in pointers.items()}
        ctx['pointer_name_re'] = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(pointers)) + r')\b') \
            if pointers else None
        found = []
        for accs, assigns in run_parallel(pass_c, repo, paths, jobs, ctx, set_ctx):
            found += accs
            for cls, name, acc in assigns:
                kind, res = model.resolve(acc)
                if kind != 'decl':
                    continue
                for pkey in list(model_pointer_keys(model, cls, name)):
                    pointer_links[pkey].append((res[0], '%s:%d' % (acc.path, acc.ln)))
        known = set((a.path, a.ln, a.name, a.op, a.via) for a in all_accesses)
        all_accesses += [a for a in found if (a.path, a.ln, a.name, a.op, a.via) not in known]
    for k in pointer_links:
        pointer_links[k] = sorted(set(pointer_links[k]))
    rows, counts, by_decl = atomic_field_rows(all_accesses, members, model, macro_locals, pointer_links)
    return rows, counts, all_accesses


def model_member(model, key):
    for d in model.decls.get(key[3], ()):
        if (d[3], d[4], d[0], d[1]) == key:
            return d
    return None


def model_pointer_keys(model, cls, name):
    for d in model.decls.get(name, ()):
        if d[5] == 'class' and d[0] and last_component(d[0]) == cls:
            yield (d[3], d[4], d[0], d[1])


SERVICE_SITE_KINDS = {'use': 'lookup of slot type', 'pool': 'object pool use of', 'bind': 'bind of slot type',
                      'unbind': 'unbind of slot type', 'slot': 'slot declaration for'}


def service_rows(services, macro_counts, macro_sites):
    by_type = collections.defaultdict(list)
    for s in services:
        by_type[s[0]].append(s)
    macro_types = collections.defaultdict(set)
    rows = []
    for typ, lst in by_type.items():
        lst.sort(key=lambda s: (s[3], s[4]))
        uses = [s for s in lst if s[2] in ('use', 'pool')]
        direct = [s for s in uses if not s[7]]
        wrapped = sorted(set(s[7] for s in uses if s[7]))
        for w in wrapped:
            macro_types[w].add(typ)
        n_uses = len(direct) + sum(macro_counts.get(w, (0, ()))[0] for w in wrapped)
        files = set(s[3] for s in direct)
        for w in wrapped:
            files |= set(macro_counts.get(w, (0, ()))[1])
        binds = [s for s in lst if s[2] == 'bind']
        unbinds = [s for s in lst if s[2] == 'unbind']
        spellings = len(set(s[1] for s in lst))
        anchor = binds[0] if binds else lst[0]
        construct = 'slot type; %d lookups in %d files%s; %d binds; %d unbinds; %d spellings%s' % (
            n_uses, len(files), (' (uses of wrapper macro %s counted)' % ', '.join(wrapped)) if wrapped else '',
            len(binds), len(unbinds), spellings, '' if binds else '; never bound through bind_server_service or BIND_SERVICE')
        rows.append((anchor[3], anchor[4], typ, construct + flag_suffix(anchor[6]), anchor[5]))
        for s in lst:
            if s is anchor:
                continue
            what = '%s %s' % (SERVICE_SITE_KINDS[s[2]], typ)
            if s[7]:
                what += ' inside #define %s (%d uses of the macro)' % (s[7], macro_counts.get(s[7], (0, ()))[0])
            rows.append((s[3], s[4], s[8], what + flag_suffix(s[6]), s[5]))
    for name, path, ln, symbol, text, flag in macro_sites:
        for typ in sorted(macro_types.get(name, ())):
            rows.append((path, ln, symbol, 'lookup of slot type %s through macro %s%s' % (typ, name, flag_suffix(flag)), text))
    return rows


def subclass_rows(classes, macros, invocation_rows):
    invoked = collections.Counter()
    for r in invocation_rows:
        m = re.search(r'via macro (\w+)', r[3])
        if m:
            invoked[m.group(1)] += 1
    rows = []
    never = set()
    for name, index, cname, base, path, ln, text, flag in macros:
        if not invoked.get(name):
            never.add(name)
            continue
        rows.append((path, ln, '#define ' + name, 'direct subclass of %s written inside #define %s (one class per invocation, %d invocations)%s%s' % (
            base, name, invoked[name], base_note(base), flag_suffix(flag)), text))
    for c in classes:
        name, simple, keyword, path, ln, bases, text, flag = c
        for b in bases:
            lb = last_component(b)
            if lb in SUBCLASS_BASES:
                rows.append((path, ln, name, 'direct subclass of %s (%s %s)%s%s' % (
                    lb, keyword, re.sub(r'\s+', '', b), base_note(lb), flag_suffix(flag)), text))
    return rows, never


def handle_class_rows(classes):
    rows = []
    for c in classes:
        name, simple, keyword, path, ln, bases, text, flag = c
        if simple.endswith('Handle'):
            rows.append((path, ln, name, 'Handle %s definition%s%s' % (
                keyword, (' : ' + ', '.join(bases)) if bases else '', flag_suffix(flag)), text))
    return rows


def guard_reach_rows(repo, classes, members, func_index, guards, refcount_names, confirmed_keys, max_depth=6):
    index = collections.defaultdict(list)
    methods = collections.defaultdict(set)
    for cls, simple, path, hl, end in func_index:
        index[(cls, simple)].append((path, hl, end))
        methods[cls].add(simple)
    bases = collections.defaultdict(set)
    for c in classes:
        for b in c[5]:
            bases[c[1]].add(last_component(b))
    member_types = {}
    for m in members:
        if m[5] == 'class' and m[0]:
            member_types.setdefault((last_component(m[0]), m[1]), m[2])
    sources = {}
    memo = {}
    local_types = {}

    def source(path):
        if path not in sources:
            sources[path] = read_source(repo, path)
        return sources[path]

    def member_type(cls, name):
        for a in ancestors(bases, cls):
            if (a, name) in member_types:
                return member_types[(a, name)]
        return None

    def targets(src, fdef, cls, recv, qual, name, off):
        if qual:
            if (qual, name) in index:
                return [(qual, name)]
            if qual[0].islower() and len(index.get(('', name), ())) == 1:
                return [('', name)]
            return []
        if recv:
            ck = (src.path, fdef[1], recv)
            if ck not in local_types:
                info = find_local(src, fdef, recv, src.starts[fdef[3]] + len(src.lines[fdef[3]]))
                local_types[ck] = info[1] if info else None
            typ = local_types[ck] or member_type(cls, recv)
            for t in sorted(type_words(typ)) if typ else ():
                for a in ancestors(bases, t):
                    if (a, name) in index:
                        return [(a, name)]
            return []
        for a in ancestors(bases, cls) if cls else ():
            if (a, name) in index:
                return [(a, name)]
        if len(index.get(('', name), ())) == 1:
            return [('', name)]
        return []

    def reach(key, depth, stack):
        if key in memo:
            return memo[key], False
        if depth > max_depth or key in stack:
            return None, True
        found = None
        truncated = False
        for path, hl, end in index.get(key, ()):
            src = source(path)
            fdef = next((f for f in src.function_spans() if f[1] == hl and f[3] == end), None)
            open_off = src.body_open.get((fdef[0], hl)) if fdef else None
            if open_off is None:
                continue
            body_end = src.starts[end] + len(src.lines[end])
            for m in IDENT_CALL_RE.finditer(src.code, open_off, body_end):
                recv, qual, name = m.group(1), m.group(2), m.group(3)
                if name in CALL_KEYWORDS or (name == key[1] and not recv and not qual):
                    continue
                if refcount_kind(name) is not None or name in refcount_names:
                    found = [name]
                    break
                for k2 in targets(src, fdef, key[0], recv, qual, name, m.start()):
                    if k2 in confirmed_keys:
                        found = [(k2[0] + '::' if k2[0] else '') + k2[1]]
                        break
                    r, t = reach(k2, depth + 1, stack | {key})
                    truncated = truncated or t
                    if r:
                        found = [(k2[0] + '::' if k2[0] else '') + k2[1]] + r
                        break
                if found:
                    break
            if found:
                break
        if found or not truncated:
            memo[key] = found
        return found, truncated

    rows = []
    for c in classes:
        name, simple, keyword, path, ln, cbases, text, flag = c
        if not GUARD_CLASS_RE.search(simple) or guards.get(simple):
            continue
        for fn in sorted(methods.get(simple, ())):
            cls = simple
            r, _ = reach((cls, fn), 0, frozenset())
            if r:
                chain = ' > '.join([simple + '::' + fn] + r)
                rows.append((path, ln, name, 'Guard %s definition whose methods reach %s through other functions (%s)%s'
                             % (keyword, r[-1], chain, flag_suffix(flag)), text))
                break
    return rows


def guard_class_rows(classes, guards):
    rows = []
    for c in classes:
        name, simple, keyword, path, ln, bases, text, flag = c
        calls = guards.get(simple)
        if calls and GUARD_CLASS_RE.search(simple):
            listed = sorted(calls)
            rows.append((path, ln, name, 'Guard %s definition whose methods call %s%s%s' % (
                keyword, ', '.join(listed[:4]), (' and %d more' % (len(listed) - 4)) if len(listed) > 4 else '',
                flag_suffix(flag)), text))
    return rows


def handle_alias_rows(aliases):
    rows = []
    for alias, target, path, ln, text, flag, chain in aliases:
        if alias.endswith('Handle'):
            rows.append((path, ln, (chain + '::' + alias) if chain else alias, 'Handle alias of %s%s' % (target, flag_suffix(flag)), text))
    return rows


def member_rows(members, cross_thread, view_re):
    views = []
    handoff = []
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        if scope != 'class' or not owner:
            continue
        t = re.sub(r'\s+', ' ', typ)
        if re.search(r'\bstatic\b', t):
            continue
        suffix = flag_suffix(mflag)
        sym = owner + '::' + nm
        outer = t
        prev = None
        while prev != outer:
            prev = outer
            outer = re.sub(r'<[^<>]*>', '', outer)
        outer = re.sub(r'([A-Za-z_])(?=[*&])', r'\1 ', outer)
        view_kind = None
        if 'view' in tags:
            vm = view_re.search(outer)
            if vm:
                view_kind = 'member %s (%s)' % (vm.group(1), 'pointer' if '*' in t.split('<')[-1] else ('reference' if '&' in t else 'value'))
            elif RAW_BYTES_RE.match(re.sub(r'\b(?:mutable|volatile)\b', '', t).strip()):
                view_kind = 'member raw bytes (pointer)'
            else:
                vm = view_re.search(t)
                view_kind = 'member container of %s' % (vm.group(1) if vm else 'views')
            views.append((path, ln, sym, '%s of type %s%s' % (view_kind, t, suffix), text))
        if last_component(owner) in cross_thread:
            if 'allocator' in tags:
                am = [a for a in ALLOCATOR_TYPE_RE.findall(outer) if a not in NOT_ALLOCATOR_TYPES]
                what = 'allocator member' if am else 'allocator-backed container member'
                handoff.append((path, ln, sym, '%s of cross-thread class: %s%s' % (what, t, suffix), text))
            elif view_kind:
                handoff.append((path, ln, sym, 'borrowed view member of cross-thread class: %s%s' % (t, suffix), text))
    return views, handoff


def write_tsv(path, header, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\t'.join(header) + '\n')
        for r in rows:
            fh.write('\t'.join(str(x).replace('\t', ' ').replace('\n', ' ').replace('\r', '') for x in r) + '\n')


def clean_rows(rows):
    uniq = {}
    for r in rows:
        key = (r[0], int(r[1]), r[2], r[3])
        if key not in uniq:
            uniq[key] = (r[0], int(r[1]), r[2] if r[2] else '-', r[3], r[4])
    return sorted(uniq.values(), key=lambda r: (r[0], r[1], r[2], r[3], r[4]))


def outer_type(target):
    target = target.replace('<<', ' ')
    t = re.sub(r'\b(?:const|volatile|typename|struct|class|unsigned|signed)\b', ' ', strip_template_args(target))
    t = t.strip().rstrip('*& ').strip()
    return last_component(t) if t else ''


def distinctive_alias(name):
    return name not in GENERIC_ALIAS_NAMES and len(name) > 3 and not re.fullmatch(r'[A-Z]\d*|T\w?\d*', name)


def template_words(target):
    words = set()
    for m in re.finditer(r'<(.*)>', target.replace('<<', ' ')):
        words |= set(last_component(w) for w in re.findall(r'[A-Za-z_][\w:]*', m.group(1)))
    return words


def alias_closure(aliases, seeds, containers=False):
    names = set(seeds)
    pending = [(a[0], outer_type(a[1]), template_words(a[1])) for a in aliases if a[0] not in GENERIC_ALIAS_NAMES]
    changed = True
    while changed:
        changed = False
        for alias, outer, inner in pending:
            if alias not in names and (outer in names or containers and inner & names and CONTAINER_NAME_RE.search(outer)):
                names.add(alias)
                changed = True
    return names - set(seeds)


def disk_ref_names(refcount_defs):
    by_name = collections.defaultdict(list)
    for simple, calls, seed, param in refcount_defs:
        tokens = set(simple.strip('_').split('_'))
        by_name[simple].append([calls, seed or param or bool(tokens & DISK_REF_TOKENS)])
    disk = set()
    changed = True
    while changed:
        changed = False
        for name, defs in by_name.items():
            if name in disk or name in ('inc_ref', 'dec_ref'):
                continue
            for d in defs:
                if not d[1] and any(c in disk or set(c.strip('_').split('_')) & DISK_REF_TOKENS for c in d[0]):
                    d[1] = True
            if all(d[1] for d in defs):
                disk.add(name)
                changed = True
    return disk


def alias_scopes(aliases, names):
    scopes = collections.defaultdict(list)
    for alias, target, path, ln, text, flag, chain in aliases:
        if alias in names:
            scopes[alias].append((chain, path, path.rsplit('.', 1)[0]))
    return scopes


def alias_applies(scopes, name, owner, path, stem):
    for chain, apath, astem in scopes.get(name, ()):
        if apath == path or astem == stem:
            return True
        if not chain and distinctive_alias(name):
            return True
        if chain and owner:
            c = last_component(chain)
            if c == last_component(owner) or ('::' + c + '::') in ('::' + owner + '::'):
                return True
    return False


def retag_members(members, view_types, view_scopes, hash_scopes):
    out = []
    hash_names = {}
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        t = set(tags)
        words = set(re.findall(r'[A-Za-z_]\w*', typ))
        if 'view' not in t:
            for w in sorted(words & view_types):
                if w not in view_scopes or alias_applies(view_scopes, w, owner, path, stem):
                    t.add('view')
                    break
        if 'hash' not in t:
            for w in sorted(words & set(hash_scopes)):
                if alias_applies(hash_scopes, w, owner, path, stem):
                    t.add('hash')
                    hash_names[(path, ln, owner, nm)] = w
                    break
        out.append((owner, nm, typ, path, ln, scope, tuple(sorted(t)), text, stem, mflag))
    return out, hash_names


def sweep(repo, paths, jobs):
    t0 = time.time()
    include_targets = include_graph(repo, paths, jobs)
    set_not_built(unbuilt_files(repo, paths, include_targets))
    errnos = load_errnos(repo)
    a_results = run_parallel(pass_a, repo, paths, jobs)
    classes, members, services, aliases, methods, macros, defines, refcount_defs = [], [], [], [], [], [], [], []
    includes = {}
    guards = collections.defaultdict(set)
    spellings = set()
    handle_raw = [0, 0]
    member_names = collections.Counter()
    method_names = collections.Counter()
    const_ret_names = collections.Counter()
    typed_rets = []
    heap_methods = collections.defaultdict(set)
    func_index = []
    func_defs = collections.Counter()
    refcount_candidates = []
    ref_first_decls = collections.defaultdict(set)
    class_tparams = {}
    accessors = []
    for c, m, s, al, me, ma, inc, mn, fn, de, gu, rd, sp, hr, cr, tr, hm, fi, fd, rcand, rf, ctp, acs in a_results:
        class_tparams.update(ctp)
        accessors += acs
        for k, v in rf.items():
            ref_first_decls[k] |= v
        func_index += fi
        func_defs.update(fd)
        refcount_candidates += rcand
        member_names.update(mn)
        method_names.update(fn)
        const_ret_names.update(cr)
        typed_rets += tr
        for k, v in hm.items():
            heap_methods[k] |= v
        classes += c
        members += m
        services += s
        aliases += al
        methods += me
        macros += ma
        defines += de
        refcount_defs += rd
        includes.update(inc)
        spellings |= sp
        for k, v in gu.items():
            guards[k] |= v
        handle_raw[0] += hr[0]
        handle_raw[1] += hr[1]
    t1 = time.time()
    ir_types = descendants(classes, IR_ROOTS)
    cross_thread = descendants(classes, CROSS_THREAD_ROOTS)
    view_classes = descendants(classes, VIEW_TYPES)
    view_aliases = alias_closure(aliases, view_classes, containers=True)
    view_types = view_classes | view_aliases
    allocator_types = descendants(classes, ('ObIAllocator',))
    allocator_types |= set(a for a in alias_closure(aliases, allocator_types) if distinctive_alias(a))
    hash_classes = descendants(classes, HASH_TYPE_NAMES)
    hash_derived = set(c for c in hash_classes if c not in HASH_TYPE_NAMES and not HASH_ANY_RE.fullmatch(c))
    hash_seed = set(a[0] for a in aliases if HASH_ANY_RE.fullmatch(outer_type(a[1]) or '-')
                    or outer_type(a[1]) in hash_derived) - GENERIC_ALIAS_NAMES
    hash_all = hash_seed | alias_closure(aliases, hash_seed | hash_derived)
    hash_alias = set(a for a in hash_all | hash_derived if distinctive_alias(a))
    hash_scopes = alias_scopes(aliases, hash_all)
    for c in hash_derived:
        hash_scopes[c].append(('', '', ''))
    view_re = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(view_types)) + r')\b')
    members, hash_names = retag_members(members, view_types, alias_scopes(aliases, view_aliases), hash_scopes)
    heap_roots = set(c[1] for c in classes if HEAP_NAME_RE.search(c[1]) and heap_methods.get(c[1], set()) & set(HEAP_OPS))
    heap_classes = descendants(classes, heap_roots)
    heap_types = heap_classes | set(a for a in alias_closure(aliases, heap_classes) if distinctive_alias(a))
    heap_type_re = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(heap_types)) + r')\b') if heap_types else None
    hash_type_re = re.compile(r'\b(' + '|'.join(HASH_TYPE_NAMES) + '|' + HASH_TYPE_GENERIC
                              + ''.join('|' + re.escape(n) for n in sorted(hash_alias)) + r')\b')
    getter_counts = collections.Counter()
    getters_by_class = set()
    for n, rt, owner, path in typed_rets:
        words = set(re.findall(r'[A-Za-z_]\w*', rt))
        hashed = hash_type_re.search(rt) is not None or any(
            w in hash_scopes and alias_applies(hash_scopes, w, owner, path, path.rsplit('.', 1)[0]) for w in words)
        if hashed:
            getter_counts[n] += 1
            if owner:
                getters_by_class.add((last_component(owner), n))
    hash_getters = set(n for n, c in getter_counts.items() if c >= 0.8 * max(1, method_names.get(n, 0)))
    class_bases = collections.defaultdict(set)
    for c in classes:
        for b in c[5]:
            class_bases[c[1]].add(last_component(b))
    ir_method_counts = collections.Counter(name for name, rettype in methods
                                           if any(t in ir_types for t in re.findall(r'[A-Za-z_]\w*', rettype)))
    ir_methods = set(n for n, c in ir_method_counts.items() if c >= 0.8 * max(1, method_names.get(n, 0)))
    member_hash = collections.defaultdict(dict)
    member_heap = collections.defaultdict(dict)
    hash_member_counts = collections.Counter()
    heap_member_counts = collections.Counter()
    hash_member_types = {}
    heap_member_types = {}
    member_ir = collections.defaultdict(lambda: (set(), set()))
    member_float = collections.defaultdict(set)
    ir_ptr_counts = collections.Counter()
    ir_array_counts = collections.Counter()
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        if scope != 'class':
            continue
        cls = last_component(owner) if owner else ''
        if 'hash' in tags:
            hm = re.search(r'\b(' + '|'.join(HASH_TYPE_NAMES) + '|' + HASH_TYPE_GENERIC + r')\b', typ)
            tname = hm.group(1) if hm else hash_names.get((path, ln, owner, nm), typ)
            member_hash[('class', cls)][nm] = tname
            member_hash[('stem', stem)][nm] = tname
            hash_member_counts[nm] += 1
            hash_member_types[nm] = tname
        if heap_type_re is not None:
            hp = heap_type_re.search(typ)
            if hp:
                member_heap[('class', cls)][nm] = hp.group(1)
                member_heap[('stem', stem)][nm] = hp.group(1)
                heap_member_counts[nm] += 1
                heap_member_types[nm] = hp.group(1)
        if 'ir' in tags:
            words = re.findall(r'[A-Za-z_]\w*', typ)
            is_ir = any(w in ir_types for w in words)
            if is_ir and '*' in typ:
                container = re.search(r'\b(?:ObIArray|ObSEArray|ObArray|ObFixedArray|ObSqlArray|ObArrayWrap|ObList)\s*<', typ)
                if container:
                    member_ir[('class', cls)][1].add(nm)
                    member_ir[('stem', stem)][1].add(nm)
                    ir_array_counts[nm] += 1
                else:
                    member_ir[('class', cls)][0].add(nm)
                    member_ir[('stem', stem)][0].add(nm)
                    ir_ptr_counts[nm] += 1
        if 'float' in tags:
            member_float[('class', cls)].add(nm)
            member_float[('stem', stem)].add(nm)
    ir_ptr_members = set(n for n, c in ir_ptr_counts.items() if c >= 0.8 * max(1, member_names.get(n, 0)))
    ir_array_members = set(n for n, c in ir_array_counts.items() if c >= 0.8 * max(1, member_names.get(n, 0)))
    wrappers, function_like, macro_locals = atomic_wrappers(defines)
    define_function_like = collections.defaultdict(bool)
    for d in defines:
        define_function_like[d[0]] |= d[1] is not None
    service_macros = set(s[7] for s in services if s[7] and s[2] == 'use')
    reset_macros = set(d[0] for d in defines if RESET_RE.search(d[2]) or COND_RESET_RE.search(d[2]))
    const_macros = set(d[0] for d in defines if CONST_CAST_RE.search(d[2]))
    ret_compare_macros = set(d[0] for d in defines if define_compares_code(d[2], errnos))
    subclass_macro_names = set(m[0] for m in macros)
    count_macros = service_macros | reset_macros | const_macros | subclass_macro_names | ret_compare_macros
    ref_names = frozenset(n for n, getters in ref_first_decls.items() if False in getters)
    refcount_def_rows, refcount_names, refcount_specific, refcount_keys = confirm_refcount_candidates(
        refcount_candidates, func_defs, ref_names)
    refcount_names = set(refcount_names) | ref_names
    const_members, const_members_stem = const_member_maps(members, classes)
    ptr_members, _ = const_member_maps(members, classes, pointer_member)
    ctx = {
        'budget_codes': sorted(set(n for n in errnos if BUDGET_CODE_NAME_RE.fullmatch(n)) | set(EXTRA_BUDGET_CODES)),
        'const_members': const_members,
        'ptr_members': ptr_members,
        'const_members_stem': const_members_stem,
        'const_returns': frozenset(n for n, c in const_ret_names.items() if c == method_names.get(n, 0)),
        'not_built': _NOT_BUILT,
        'errnos': errnos,
        'ir_types': ir_types,
        'ir_methods': ir_methods,
        'ir_ptr_members': ir_ptr_members,
        'ir_array_members': ir_array_members,
        'cross_thread': cross_thread,
        'thread_pools': frozenset(descendants(classes, THREAD_POOL_ROOTS)),
        'hash_aliases': hash_alias,
        'member_hash': dict(member_hash),
        'member_heap': dict(member_heap),
        'hash_member_names': {n: hash_member_types[n] for n, c in hash_member_counts.items()
                              if c >= 0.8 * max(1, member_names.get(n, 0))},
        'heap_member_names': {n: heap_member_types[n] for n, c in heap_member_counts.items()
                              if c >= 0.8 * max(1, member_names.get(n, 0))},
        'hash_getters': hash_getters,
        'hash_getters_by_class': frozenset(getters_by_class),
        'hash_classes': frozenset(hash_classes),
        'heap_classes': frozenset(heap_classes),
        'heap_types': frozenset(heap_types),
        'class_bases': {k: frozenset(v) for k, v in class_bases.items()},
        'member_ir': dict(member_ir),
        'member_float': dict(member_float),
        'subclass_macros': {(m[0]): (m[1], m[3]) for m in macros if m[1] >= 0},
        'atomic_wrappers': wrappers,
        'atomic_function_like': function_like,
        'view_types': set(v for v in view_types if v in view_classes or distinctive_alias(v)),
        'allocator_types': allocator_types,
        'count_macros': count_macros,
        'count_macro_function_like': {n: define_function_like[n] for n in count_macros},
        'site_macros': frozenset(service_macros),
        'disk_ref_names': disk_ref_names(refcount_defs),
        'refcount_confirmed': frozenset(refcount_names),
        'ref_first_names': ref_names,
        'refcount_specific': frozenset(refcount_specific),
    }
    new_ctx = new_list_context(repo, paths, jobs, classes, aliases, members, func_index, func_defs, defines, methods,
                               method_names, ir_types)
    ctx.update(new_ctx)
    b_results = run_parallel(pass_b, repo, paths, jobs, ctx)
    rows = collections.defaultdict(list)
    stats = collections.Counter()
    accesses = []
    macro_counts = collections.defaultdict(lambda: [0, set()])
    macro_sites = []
    for res, st, acc, mc, ms in b_results:
        macro_sites += ms
        for cat, lst in res.items():
            rows[cat] += lst
        stats.update(st)
        accesses += acc
        for k, (n, files) in mc.items():
            macro_counts[k][0] += n
            macro_counts[k][1] |= files
    model = AtomicModel(members, classes, aliases, include_targets, class_tparams, accessors)
    atomic_rows, (n_fields, n_locals, n_unresolved, n_unresolved_acc, via_counts), accesses = resolve_atomic(
        repo, paths, jobs, {'not_built': _NOT_BUILT}, accesses, members, model, func_defs, macro_locals)
    rows['atomic-fields'] = atomic_rows
    rows['server-service-slots'] = service_rows(services, macro_counts, macro_sites)
    sub_rows, never_invoked = subclass_rows(classes, macros, rows['subclassed-bases'])
    rows['subclassed-bases'] += sub_rows
    rows['refcount'] += handle_class_rows(classes) + guard_class_rows(classes, guards) + handle_alias_rows(aliases)
    rows['refcount'] += refcount_def_rows
    rows['refcount'] += guard_reach_rows(repo, classes, members, func_index, guards, ref_names | refcount_specific,
                                         refcount_keys)
    views, handoff = member_rows(members, cross_thread, view_re)
    rows['borrowed-views'] += views
    rows['arena-handoff'] += handoff
    t2 = time.time()
    info = {
        'accesses': accesses, 'services': services, 'spellings': spellings, 'classes': classes, 'stats': stats,
        'cross_thread': cross_thread, 'ir_types': ir_types, 'macro_counts': macro_counts, 'reset_macros': reset_macros,
        'const_macros': const_macros, 'ret_compare_macros': ret_compare_macros, 'never_invoked': never_invoked,
        'handle_raw': handle_raw,
        'atomic': (n_fields, n_locals, n_unresolved, n_unresolved_acc, via_counts), 'view_types': view_types,
        'hash_alias': hash_alias,
        'timing': (t1 - t0, t2 - t1), 'budget_codes': ctx['budget_codes'],
    }
    info.update(new_ctx)
    return rows, info


def main():
    ap = argparse.ArgumentParser(description='Sweep the seekdb C++ source for the Step 1 gap inventory.')
    ap.add_argument('--repo', default=repo_root_default())
    ap.add_argument('--out', default=None)
    ap.add_argument('--jobs', type=int, default=min(6, os.cpu_count() or 1))
    ap.add_argument('--only', default='', help='comma-separated file paths to sweep instead of the whole tree')
    args = ap.parse_args()
    t0 = time.time()
    repo = os.path.abspath(args.repo)
    out_dir = args.out or os.path.join(repo, 'migration', 'inventory', 'sweep')
    os.makedirs(out_dir, exist_ok=True)
    paths = list_sources(repo)
    if args.only:
        wanted = set(p.strip() for p in args.only.split(',') if p.strip())
        paths = [p for p in paths if p in wanted]
    if not frozen_state(repo):
        print('warning: src differs from %s; the sweep reads the working tree' % FROZEN_REV, file=sys.stderr)
    rows, info = sweep(repo, paths, args.jobs)
    header = ('file', 'line', 'symbol', 'construct', 'text')
    summary = []
    extra = derived_figures(rows, info)
    for cat in CATEGORIES:
        lst = clean_rows(rows.get(cat, []))
        write_tsv(os.path.join(out_dir, cat + '.tsv'), header, lst)
        summary.append((cat, len(lst), len(set(r[0] for r in lst)), PLAN_FIGURES[cat], DEFINITIONS[cat] + ' ' + extra.get(cat, '')))
    write_tsv(os.path.join(out_dir, 'summary.tsv'), ('category', 'rows', 'distinct_files', 'plan_figure', 'definition'), summary)
    ta, tb = info['timing']
    print('swept %d files in %.1fs (pass A %.1fs, pass B %.1fs)' % (len(paths), time.time() - t0, ta, tb), file=sys.stderr)
    for s in summary:
        print('%-22s %7d rows %5d files' % (s[0], s[1], s[2]), file=sys.stderr)


def pointer_member(typ):
    return '*' in drop_template_args(typ)


def const_member_maps(members, classes, test=None):
    test = test or const_pointee
    own = collections.defaultdict(set)
    by_stem = collections.defaultdict(set)
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        if scope == 'class' and owner and test(typ) and not re.search(r'\bstatic\b', typ):
            own[strip_template_args(owner)].add(nm)
            by_stem[stem].add(nm)
    chains = collections.defaultdict(set)
    bases = collections.defaultdict(set)
    for c in classes:
        chain = strip_template_args(c[0])
        chains[c[1]].add(chain)
        for b in c[5]:
            bases[chain].add(last_component(b))
    out = {}
    for cls in set(own) | set(bases):
        names = set()
        todo = [cls]
        seen = set()
        while todo:
            c = todo.pop()
            if c in seen:
                continue
            seen.add(c)
            names |= own.get(c, set())
            for b in bases.get(c, ()):
                if len(chains.get(b, ())) == 1:
                    todo.append(next(iter(chains[b])))
        if names:
            out[cls] = frozenset(names)
    return out, {k: frozenset(v) for k, v in by_stem.items()}


def class_chain_lookup(table, fname):
    chain = strip_template_args(fname).rsplit('::', 1)[0] if '::' in fname else ''
    while chain:
        if chain in table:
            return table[chain]
        if '::' not in chain:
            break
        chain = chain.split('::', 1)[1]
    return frozenset()


def define_compares_code(body, errnos):
    for m in CODE_COMPARE_RE.finditer(body):
        name = next(g for g in m.groups() if g)
        if name != 'OB_SUCCESS' and name in errnos:
            return True
    return False


def count_constructs(rows, pattern):
    rx = re.compile(pattern)
    return sum(1 for r in rows if rx.search(r[3]))


def macro_body_note(rows_list, macro_counts, candidates):
    in_macro = [r for r in rows_list if r[2].startswith('#define ')]
    names = set(r[2][len('#define '):] for r in in_macro) & set(candidates)
    uses = sum(macro_counts.get(n, (0, ()))[0] for n in names)
    return len(in_macro), len(names), uses


def via_kinds(accesses):
    kinds = collections.Counter()
    for a in accesses:
        if not a.via:
            continue
        seen = set()
        for part in a.via.split(' | '):
            kind = part.partition(':')[0] if ':' in part else 'macro'
            if kind not in seen:
                kinds[kind] += 1
                seen.add(kind)
    return kinds


def derived_figures(rows, info):
    out = {}
    accesses = info['accesses']
    fam = collections.Counter()
    for a in accesses:
        if a.op.startswith('ATOMIC_'):
            fam['ATOMIC_*'] += 1
        elif a.op.startswith('__sync_'):
            fam['__sync_*'] += 1
        elif a.op.startswith('__atomic_'):
            fam['__atomic_*'] += 1
        elif '128' in a.op:
            fam['CAS128/LOAD128'] += 1
        elif 'Interlocked' in a.op:
            fam['Interlocked*'] += 1
        else:
            fam['inc_update/dec_update'] += 1
    n_fields, n_locals, n_unres, n_unres_acc, kinds = info['atomic']
    af = clean_rows(rows.get('atomic-fields', []))
    out['atomic-fields'] = (
        '(Measured: %d atomic accesses (%s), touching %d distinct names; %d reached through wrapper macros, %d through member '
        'functions of value classes, %d through functions with an atomic parameter, %d through accessors, %d through the single '
        'member of a value class and %d through pointer members. Rows: %d fields or globals (%d reached through member '
        'functions, %d value-class members reached only through the fields that hold them, %d pointers whose pointee is the '
        'atomic object), %d parameter, local or function-static rows, %d statics declared inside macros, %d name-only rows '
        'covering %d accesses, %d volatile fields or globals with no atomic access.)') % (
        len(accesses), ', '.join('%s %d' % (k, fam[k]) for k in ('ATOMIC_*', '__sync_*', '__atomic_*', 'CAS128/LOAD128',
                                                                  'inc_update/dec_update', 'Interlocked*')),
        len(set(a.name for a in accesses)), kinds['macro'], kinds['method'], kinds['function'], kinds['accessor'],
        kinds['value'], kinds['pointer'], n_fields, count_constructs(af, r'through member functions'),
        count_constructs(af, r'no direct atomic access'), count_constructs(af, r'pointer whose pointee'), n_locals,
        count_constructs(af, r'declared inside #define'), n_unres, n_unres_acc, count_constructs(af, r'^volatile '))
    rc = clean_rows(rows.get('refcount', []))
    evidence = r'(?:^|; )_?(?:inc|dec)_ref\w* (?:call|definition|declaration)(?:;|$| \[)'
    plain_lines = count_constructs(rc, evidence)
    call_lines = [r for r in rc if re.search(r' (?:call|definition|declaration)\b', r[3]) and not r[3].startswith(('Handle ', 'Guard '))]
    prefixed = sum(1 for r in call_lines if 'prefixed name' in r[3] and not re.search(evidence, r[3]))
    other = sum(1 for r in call_lines if 'other name' in r[3] and 'prefixed name' not in r[3] and not re.search(evidence, r[3]))
    body = [r for r in call_lines if 'found by its body' in r[3] and not re.search(r'other name|prefixed name|' + evidence, r[3])]
    hc = count_constructs(rc, r'^Handle class definition')
    hs = count_constructs(rc, r'^Handle struct definition')
    out['refcount'] = (
        '(Measured: %d lines with an inc_ref/dec_ref name, the evidence regex; %d more lines with only a prefixed name; %d lines '
        'with only another reference-count name; %d lines with only a function found by its body (%d definitions); %d '
        'reference-count changes inside functions that are not reference-count functions; %d lines tagged on-disk block '
        'reference; %d lines that only declare a function; %d Handle definitions (%d class, %d struct); %d Guard classes that '
        'call a reference-count function directly and %d that reach one through other functions; %d Handle aliases. The '
        'evidence regex class \\w+Handle\\b matches %d raw lines today, %d of them forward or friend declarations, and never '
        'counted struct definitions, so the plan\'s 130 Handle classes are %d Handle definitions.)') % (
        plain_lines, prefixed, other, len(body), sum(1 for r in body if ' definition ' in r[3] + ' '),
        count_constructs(rc, r'^reference-count change'), count_constructs(rc, r'on-disk block reference'),
        sum(1 for r in call_lines if 'declaration' in r[3] and not re.search(r' (?:call|definition)\b', r[3])),
        hc + hs, hc, hs, count_constructs(rc, r'^Guard \w+ definition whose methods call'),
        count_constructs(rc, r'through other functions'), count_constructs(rc, r'^Handle alias'),
        info['handle_raw'][0], info['handle_raw'][1], hc + hs)
    cc = clean_rows(rows.get('const-cast', []))
    cc_rows, cc_macros, cc_uses = macro_body_note(cc, info['macro_counts'], info['const_macros'])
    out['const-cast'] = (
        '(Measured: %d lines with const_cast<, %d of them only adding const; %d lines with a C-style cast that drops const, '
        'found by the heuristic: %d on this in a const member function, %d on a member declared const, %d on the result of a '
        'method that only returns const, %d on a name declared const in the function; %d rows sit inside %d #define bodies, '
        'whose %d invocation lines are not rows.)') % (
        count_constructs(cc, r'const_(?:pointer_)?cast<'), count_constructs(cc, r'adds const only'),
        count_constructs(cc, r'C-style cast'), count_constructs(cc, r'drops const from this'),
        count_constructs(cc, r'drops const from member'), count_constructs(cc, r'drops const from the result'),
        count_constructs(cc, r'drops const from (?!this|member|the result)\w'), cc_rows, cc_macros, cc_uses)
    rcmp = clean_rows(rows.get('ret-compare', []))
    rc_rows, rc_macros, rc_uses = macro_body_note(rcmp, info['macro_counts'], info['ret_compare_macros'])
    out['ret-compare'] = ('(Measured: %d lines with ret == OB_X, the plan-comparable subset; %d lines where the code is assigned '
                          'inside the comparison; %d lines comparing another code variable; %d lines comparing a call result; '
                          '%d lines comparing an errsim expression; %d case labels; %d comparisons of ret against non-error OB_ '
                          'constants dropped; %d rows sit inside %d #define bodies, whose %d invocation lines, counted by '
                          'macro name, are not rows.)') % (
        sum(1 for r in rcmp if re.search(r'(?:^|; )ret == ', r[3])), count_constructs(rcmp, r'assigned in the comparison'),
        count_constructs(rcmp, r'(?:^|; )other variable '), count_constructs(rcmp, r'(?:^|; )call result '),
        count_constructs(rcmp, r'(?:^|; )errsim expression '), count_constructs(rcmp, r'^switch \('),
        info['stats'].get('ret-compare non-errno', 0), rc_rows, rc_macros, rc_uses)
    rs = clean_rows(rows.get('reset', []))
    rs_rows, rs_macros, rs_uses = macro_body_note(rs, info['macro_counts'], info['reset_macros'])
    out['reset'] = ('(Measured: %d line-start resets, the plan definition; %d resets after other code on the line; %d resets '
                    'inside an expression (FALSE_IT or a comma expression); %d resets spelled ret = 0; %d conditional resets '
                    'that swallow a code, %d of them statements over several lines; %d codes chosen by a flag; %d errsim '
                    'injection points; %d rows sit inside %d #define bodies, whose %d invocation lines are not rows.)') % (
        count_constructs(rs, r'line start'), count_constructs(rs, r'after other code'), count_constructs(rs, r'inside an expression'),
        count_constructs(rs, r'spelled 0'), count_constructs(rs, r'conditional reset'),
        count_constructs(rs, r'conditional reset[^;]*statement spans'), count_constructs(rs, r'chosen by a flag'),
        count_constructs(rs, r'errsim injection'), rs_rows, rs_macros, rs_uses)
    tr = clean_rows(rows.get('tmp-ret', []))
    out['tmp-ret'] = ('(Measured: %d lines with a plan-definition construct, %d of them INIT_SUCC(tmp_ret); %d lines through other '
                      'continue-and-record variables or constants (merge, declaration or assignment), %d of them merges written '
                      'as if (OB_SUCC(ret)) { ret = X; }; %d tmp_ret lines that only compare or log were left out.)') % (
        count_constructs(tr, r'plan definition'), sum(1 for r in tr if 'plan definition' in r[3] and 'INIT_SUCC' in r[4]),
        count_constructs(tr, r'continue-and-record'), count_constructs(tr, r'through if \(OB_SUCC'),
        info['stats'].get('tmp-ret compare or log only', 0))
    ra = clean_rows(rows.get('ret-alias', []))
    no_result = count_constructs(ra, r'sets no result')
    out['ret-alias'] = ('(Measured: %d exact int &ret = ret_ lines; %d aliases of other members; %d comparators whose error branch '
                        'sets no result; %d comparators that keep their error in a member without an alias; %d lambda comparators '
                        'passed to a sort or search that record errors in the caller\'s ret. PLAN section 6 says "the two unfixed '
                        'comparators"; the sweep finds %d comparators with an int &ret alias whose error branch sets no result, '
                        'so that figure undercounts.)') % (
        count_constructs(ra, r'^int &ret = ret_(?:;|$)'), count_constructs(ra, r'^int &ret = (?!ret_(?:;|$))'), no_result,
        count_constructs(ra, r'without an int &ret alias'), count_constructs(ra, r'^lambda comparator'), no_result)
    om = clean_rows(rows.get('oom-sites', []))
    cases = collections.Counter(re.search(r'plan case: ([^)]*)\)', r[3]).group(1) for r in om if 'plan case:' in r[3])
    out['oom-sites'] = (
        '(Measured: budget-backed -4013 at an owner the plan names: %d; at a bounded allocator the plan does not name: %d; '
        'logical -4013 at the plan\'s named cases: %d (%s); other logical-looking -4013, provisional: %d; errsim -4013 '
        'injections: %d; -4013 passed to a function or macro that raises it: %d; variables initialized to -4013: %d; -4013 '
        'comparisons: %d; -4013 case labels: %d; budget codes: %s; budget error raises: %d; budget error comparisons or case '
        'labels: %d; tracker check calls: %d; tracker function declarations or definitions: %d; FIFO lines: %d.)') % (
        count_constructs(om, r'^budget-backed'), count_constructs(om, r'^bounded allocator not named'),
        sum(cases.values()), ', '.join('%s %d' % kv for kv in sorted(cases.items())) or 'none',
        count_constructs(om, r'^logical -4013, not a named'), count_constructs(om, r'^errsim'),
        count_constructs(om, r'^-4013 (?:passed to|raised through)'), count_constructs(om, r'^variable initialized'),
        count_constructs(om, r'^-4013 handled \(compared'), count_constructs(om, r'^-4013 handled \(case'),
        ', '.join(info['budget_codes']), count_constructs(om, r'^budget error raised'),
        count_constructs(om, r'^budget error handled'), count_constructs(om, r'^query memory tracker check(?: \[|$)'),
        count_constructs(om, r'^query memory tracker check function'), count_constructs(om, r'^micro block cache FIFO'))
    ss = [r for r in clean_rows(rows.get('server-service-slots', [])) if r[3].startswith('slot type;')]
    sites = clean_rows(rows.get('server-service-slots', []))
    spelled = set()
    for sp in info['spellings']:
        t = normalize_type(sp[len('server_service<'):-1])
        if t and t.count('<') == t.count('>') and t not in ('type', 'Type', 'T', 'Service', 'service'):
            spelled.add(t)
    row_types = set(r[2] for r in ss)
    out['server-service-slots'] = ('(Measured: the evidence command git grep -h -oP \'server_service<[^>]+>\' | sort -u gives %d '
                                   'distinct raw spellings today; it also matches inside bind_server_service and '
                                   'unbind_server_service, a macro parameter and one cut-off template. With namespaces removed '
                                   'they name %d distinct types; the summary rows hold %d types, %d of them written with the type '
                                   'on the next line and %d reached only through server_obj_pool or the pool helpers. So the '
                                   'plan\'s 122 slot types are %d distinct types. %d lookup lines, uses of wrapper macros counted. '
                                   'Besides the one summary row per type, every lookup, bind, unbind and pool site is a row (%d '
                                   'rows; a use of a wrapper macro is a row at the use), so the table can be searched by file.)') % (
        len(info['spellings']), len(spelled), len(row_types), len(set(t for t in row_types - spelled if not t.startswith('ObServerObjectPool<'))),
        len(set(t for t in row_types - spelled if t.startswith('ObServerObjectPool<'))), len(row_types),
        sum(int(re.search(r'slot type; (\d+) lookups', r[3]).group(1)) for r in ss), len(sites) - len(ss))
    sb = clean_rows(rows.get('subclassed-bases', []))
    per_base = [count_constructs(sb, r'^direct subclass of ' + b + r' \(') for b in SUBCLASS_BASES]
    per_macro = [count_constructs(sb, r'^direct subclass of ' + b + r' via macro') for b in SUBCLASS_BASES]
    macro_bodies = count_constructs(sb, r'written inside #define')
    timer_inv = per_macro[0]
    out['subclassed-bases'] = ('(Measured: class-header subclasses %s; macros that write such a class and are used: %d; macros '
                               'that write one and are never used, so have no row: %d (%s); macro uses: %d (%s). The plan\'s '
                               'ObTimerTask 72 counts the %d class headers plus the macro bodies; counting classes, there are %d '
                               'direct ObTimerTask subclasses (%d class headers plus %d macro instances).)') % (
        ', '.join('%s %d' % (b, n) for b, n in zip(SUBCLASS_BASES, per_base)), macro_bodies, len(info['never_invoked']),
        ', '.join(sorted(info['never_invoked'])) or 'none', count_constructs(sb, r'via macro'),
        ', '.join('%s %d' % (b, n) for b, n in zip(SUBCLASS_BASES, per_macro) if n) or 'none', per_base[0], per_base[0] + timer_inv,
        per_base[0], timer_inv)
    sh = clean_rows(rows.get('sort-hash-order', []))
    alias_iter = sum(1 for r in sh if r[3].startswith('iterate hash')
                     and re.search(r'\((\w+)\)', r[3]) and re.search(r'\((\w+)\)', r[3]).group(1) in info['hash_alias'])
    out['sort-hash-order'] = ('(Measured: %d lib::ob_sort call lines; %d std heap operation lines; %d hash iteration lines, %d of '
                              'them over a container declared through a typedef of an OB hash type or a class derived from one, '
                              '%d over a container returned by a getter and %d through an iterator of a hash type; OB heap '
                              'classes: %d declarations, %d push, pop, top or replace_top lines and %d definitions of those '
                              'operations.)') % (
        count_constructs(sh, r'lib::ob_sort call'), count_constructs(sh, r'_heap call'), count_constructs(sh, r'^iterate hash'),
        alias_iter, count_constructs(sh, r'^iterate hash container returned by'),
        count_constructs(sh, r'^iterate hash container through an iterator'), count_constructs(sh, r'^heap declared'),
        count_constructs(sh, r'^heap operation (?!definition)'), count_constructs(sh, r'^heap operation definition'))
    pi = clean_rows(rows.get('pointer-identity', []))
    out['pointer-identity'] = ('(Measured: %d IR types; %d pointer-keyed container lines; %d IR==IR; %d IR==unresolved; pointer-to-'
                               'integer casts: %d used as a key, %d of an IR pointer, %d compared for identity, %d compared for '
                               'order, %d used for hashing or a bucket choice, %d used as an id; %d membership tests, %d of them '
                               'identity then same_as.)') % (
        len(info['ir_types']), count_constructs(pi, r'^container keyed'), count_constructs(pi, r'IR pointer [=!]= IR pointer'),
        count_constructs(pi, r'unresolved pointer'), count_constructs(pi, r'^pointer cast to integer used as a key'),
        count_constructs(pi, r'^pointer cast to integer IR identity'), count_constructs(pi, r'compared for identity'),
        count_constructs(pi, r'compared for order'), count_constructs(pi, r'hashing or a bucket'),
        count_constructs(pi, r'used as an id'), count_constructs(pi, r'^pointer membership'),
        count_constructs(pi, r'identity, then same_as'))
    ah = clean_rows(rows.get('arena-handoff', []))
    out['arena-handoff'] = ('(Measured: %d cross-thread classes; %d allocator members; %d allocator-backed container members; %d '
                            'view members; %d constructions; %d hand-off rows out of %d calls that match the hand-off verbs and '
                            'receiver filters: the other calls hand off an object the same function did not allocate, or pass '
                            'no allocator, and are not rows.)') % (
        len(info['cross_thread']), count_constructs(ah, r'^allocator member'), count_constructs(ah, r'^allocator-backed container'),
        count_constructs(ah, r'^borrowed view member'), count_constructs(ah, r'^cross-thread object'),
        count_constructs(ah, r'^hand-off'), info['stats'].get('arena-handoff calls', 0))
    bv = clean_rows(rows.get('borrowed-views', []))
    listed = re.compile(r'^member (?:' + '|'.join(VIEW_TYPES) + r') |^member raw bytes|^member container of (?:'
                        + '|'.join(VIEW_TYPES) + r')\b')
    out['borrowed-views'] = ('(Measured: %d view types after adding subclasses and typedef aliases of the listed ones; %d members, '
                             '%d of them typed with a subclass or alias; %d allocator-in functions returning an arena view.)') % (
        len(info['view_types']), count_constructs(bv, r'^member '),
        sum(1 for r in bv if r[3].startswith('member ') and not listed.search(r[3])), count_constructs(bv, r'^allocator in'))
    ov = clean_rows(rows.get('overflow', []))
    out['overflow'] = ('(Measured: %d explicit checks, %d of them raises of an overflow code, %d of those outside the value '
                       'directories; %d unchecked arithmetic lines: %d on a signed and %d on an unsigned value getter, %d on a '
                       'signed and %d on an unsigned value, %d checked for overflow only after the arithmetic.)') % (
        count_constructs(ov, r'^check'), count_constructs(ov, r'check: raises'),
        sum(1 for r in ov if 'check: raises' in r[3] and not in_dirs(r[0], VALUE_DIRS)), count_constructs(ov, r'^unchecked'),
        count_constructs(ov, r'on a signed value getter'), count_constructs(ov, r'on an unsigned value getter'),
        count_constructs(ov, r'on signed value '), count_constructs(ov, r'on unsigned value '),
        count_constructs(ov, r'only afterwards'))
    fc = clean_rows(rows.get('float-contraction', []))
    out['float-contraction'] = '(Measured: %d fma calls; %d multiply-add rows, %d of them statements over several lines.)' % (
        count_constructs(fc, r'^fma call'), count_constructs(fc, r'^multiply-add'), count_constructs(fc, r'statement spans lines'))
    out.update(new_list_figures(rows, info))
    for cat in CATEGORIES:
        n = sum(1 for r in clean_rows(rows.get(cat, [])) if '[not built]' in r[3])
        out[cat] = out.get(cat, '') + (' %d rows are in files the build never compiles and carry [not built].' % n if n else '')
    return out


LOCK_PRIMITIVES = {
    'ObLatchMutex': 'mutex', 'ObSpinLock': 'mutex', 'ObMutex': 'mutex', 'ObUtilMutex': 'mutex', 'ObFutex': 'futex',
    'ObSmallSpinLock': 'spin lock', 'ObByteLock': 'spin lock', 'ObPtrSpinLock': 'spin lock', 'ObRowLatch': 'spin lock',
    'ObLatch': 'read-write lock', 'SpinRWLock': 'read-write lock', 'TCRWLock': 'read-write lock',
    'DRWLock': 'read-write lock', 'ObRWLock': 'read-write lock', 'ObQSyncLock': 'read-write lock',
    'ObBucketLock': 'bucket lock', 'ObBucketQSyncLock': 'bucket lock', 'ObRecursiveMutex': 'reentrant mutex',
    'ObThreadCond': 'condition', 'ObCond': 'condition', 'Cond': 'condition', 'SimpleCond': 'condition',
    'SCondTemp': 'condition', 'LWaitCond': 'condition', 'ObMonitor': 'condition', 'ObQSync': 'reader-count sync',
    'ObDynamicQSync': 'reader-count sync', 'TCRef': 'reader-count sync', 'ObNullLock': 'no-op lock', 'NLock': 'no-op lock',
    'NCond': 'no-op lock', 'pthread_mutex_t': 'mutex', 'pthread_spinlock_t': 'spin lock',
    'pthread_rwlock_t': 'read-write lock', 'pthread_cond_t': 'condition', 'easy_spinrwlock_t': 'read-write lock',
}
LOCK_PLATFORM_ALIASES = {'pthread_spinlock_t': 'a typedef of pthread_mutex_t under #ifdef __APPLE__, ob_platform_utils.h'}
LOCK_ELEMENT_CONTAINER_RE = re.compile(
    r'^(?:std::)?(?:pair|tuple|array|vector|deque|list)$|^Ob\w*(?:Array|List|Vector|Wrap)$|^ObSEArray$|^ObFixedArray$')
LOCK_ACCESSOR_RET_RE = re.compile(r'[&*]\s*$')
SPIN_ACQUIRE_PRIMITIVES = ('ATOMIC_TAS', 'ATOMIC_BCAS', 'ATOMIC_CAS', 'ATOMIC_VCAS', '__sync_lock_test_and_set',
                           '__sync_bool_compare_and_swap', '__sync_val_compare_and_swap', '__atomic_test_and_set',
                           '__atomic_compare_exchange_n', '__atomic_compare_exchange', '__atomic_exchange_n')
SPIN_LOCK_WORDS = frozenset(('lock', 'locks', 'latch', 'mutex', 'spin', 'spinlock', 'spinlocks'))
SPIN_LOCK_DECL_RE = re.compile(
    r'(?<![\w:.>~#])(?P<quals>(?:(?:static|mutable|volatile|thread_local|extern|inline)\s+)*)'
    r'(?P<type>(?:(?:unsigned|signed)\s+)?(?:u?int(?:8|16|32|64)?(?:_t)?|char|bool|long(?:\s+long)?|short|int32|int64|'
    r'uint32|uint64|easy_atomic_t|easy_atomic32_t)(?:\s+volatile)?)\b(?P<ptr>\s*[*&]*\s*|\s+)(?P<name>[A-Za-z_]\w*)\s*'
    r'(?P<arr>(?:\[[^\]\n;]*\]\s*)*)(?:CACHE_ALIGNED\s*)?(?P<end>[;=({,])')
LOCK_STD_KINDS = {
    'mutex': 'mutex', 'timed_mutex': 'mutex', 'recursive_mutex': 'reentrant mutex',
    'recursive_timed_mutex': 'reentrant mutex', 'shared_mutex': 'read-write lock', 'shared_timed_mutex': 'read-write lock',
    'condition_variable': 'condition', 'condition_variable_any': 'condition',
}
LOCK_WRAPPER_NAME_RE = re.compile(r'(?:Lock|Latch|Mutex|Cond)$')
LOCK_OP_NAMES = frozenset(('lock', 'unlock', 'rdlock', 'wrlock', 'rdunlock', 'wrunlock', 'try_lock', 'try_rdlock',
                           'try_wrlock'))
LOCK_PRIMITIVE_DIR = 'src/oblib/lib/lock/'
LOCK_NAME_TOKENS = frozenset(('lock', 'locks', 'latch', 'latches', 'mutex', 'mutexes', 'cond', 'rwlock', 'spinlock'))
LOCK_DECL_TEMPLATE = (
    r'(?<![\w:.>~#])(?P<quals>(?:(?:static|mutable|volatile|thread_local|extern|inline|constexpr|const)\s+)*)'
    r'(?P<ns>(?:::\s*)?(?:[A-Za-z_]\w*\s*::\s*)*)(?P<word>%s)\b(?P<targs>\s*<(?:[^<>;{}()]|<[^<>;{}()]*>)*>)?'
    r'(?P<cv>(?:\s+(?:const|volatile)\b)*)(?P<ptr>\s*[*&]+\s*|\s+)(?:const\s+)?(?P<name>[A-Za-z_]\w*)\s*'
    r'(?P<arr>(?:\[[^\]\n;]*\]\s*)*)(?:(?:CACHE_ALIGNED|__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)|'
    r'DEF_ALIGN\s*\([^()]*\)|alignas\s*\([^()]*\))\s*)*(?P<end>[;=({,])')
LOCK_CONTAINER_TEMPLATE = (
    r'(?<![\w:.>~#])(?:(?:static|mutable)\s+)*(?P<outer>(?:(?:::\s*)?[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*'
    r'<(?P<inner>[^;{}()]*?\b(?:%s)\b[^;{}()]*?)>\s*(?P<ptr>[*&]*)\s*(?P<name>[A-Za-z_]\w*)\s*(?P<arr>(?:\[[^\]\n;]*\]\s*)*)'
    r'(?P<end>[;=({])')
LOCK_GUARD_RE = re.compile(
    r'(?<![\w:.>])((?:[A-Za-z_]\w*\s*::\s*)*\w*?(?:Guard|lock_guard|unique_lock|shared_lock|scoped_lock)\w*)\s*'
    r'(?:<[^;{}()]*>)?\s+[A-Za-z_]\w*\s*([({])')
LOCK_GUARD_TYPE_RE = re.compile(r'Lock|Latch|Mutex|Cond|Spin|QSync|Bucket|lock_guard|unique_lock|shared_lock|scoped_lock')
LOCK_CALL_RE = re.compile(
    r'([A-Za-z_]\w*)\s*(\[[^\[\];]*\]\s*)?(\(\s*\)\s*)?(?:\.|->)\s*(lock|unlock|rdlock|wrlock|rdunlock|wrunlock|wr2rdlock|'
    r'try_lock|try_rdlock|try_wrlock|trylock|timedlock|lock_timeout|try_lock_for|try_lock_until|wait|wait_us|'
    r'timedwait|timed_wait|signal|broadcast|notify_one|notify_all)\s*\(')
LOCK_ACQUIRE_OPS = frozenset(('lock', 'rdlock', 'wrlock', 'try_lock', 'try_rdlock', 'try_wrlock', 'trylock', 'timedlock',
                              'lock_timeout', 'try_lock_for', 'try_lock_until'))
PTHREAD_LOCK_CALL_RE = re.compile(r'\bpthread_(mutex|rwlock|spin|cond)_(\w+)\s*\(\s*&?\s*([A-Za-z_][\w.\->\[\]]*)')
TIMED_ARG_WORDS = frozenset(('time', 'timeout', 'timeouts', 'deadline', 'expire', 'expired', 'us', 'ts'))

C_PRINTF_FUNCTIONS = {
    'printf': (0, 'variadic'), 'fprintf': (1, 'variadic'), 'dprintf': (1, 'variadic'), 'sprintf': (1, 'variadic'),
    'snprintf': (2, 'variadic'), 'asprintf': (1, 'variadic'), 'vprintf': (0, 'va_list'), 'vfprintf': (1, 'va_list'),
    'vdprintf': (1, 'va_list'), 'vsprintf': (1, 'va_list'), 'vsnprintf': (2, 'va_list'), 'vasprintf': (1, 'va_list'),
}
PRINTF_SINKS = {
    'log_message_fmt': 'server log', 'log_message_va': 'server log', 'logdata_printf': 'server log',
    'logdata_vprintf': 'server log', 'log_user_message': 'user message', 'printf': 'standard stream',
    'vprintf': 'standard stream', 'fprintf': 'stream', 'vfprintf': 'stream', 'dprintf': 'stream', 'vdprintf': 'stream',
}
PRINTF_SINK_TEXT = {
    'server log': 'writes the server log', 'user message': 'records a user message, which reaches the client',
    'standard stream': 'writes stdout', 'stream': 'writes a stream or file',
}
IDENT_PAREN_RE = re.compile(r'(?<![\w])([A-Za-z_]\w*)\s*\(')
PRINTF_FMT_PARAM_RE = re.compile(r'\bconst\s+char\s*\*\s*(?:const\s+)?(\w*(?:fmt|format|FMT|Format)\w*)\s*$')
PRINTF_LOG_JOIN_RE = re.compile(r'\b(_?)LOG_MACRO_JOIN\s*\(\s*USING_LOG_PREFIX\s*,\s*(_LOG\w*)\s*\)\s*\(')
PRINTF_ATTR_RE = re.compile(
    r'\b([A-Za-z_]\w*)\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*(?:const\s*)?__attribute__\s*\(\(\s*(?:__)?format(?:__)?\s*'
    r'\(\s*(?:__)?printf(?:__)?\s*,')
STRING_MACRO_RE = re.compile(r'\s*#\s*define\s+\w+\s+((?:(?:u8|u|U|L)?"(?:[^"\\]|\\.)*"\s*)+)(?://.*|/\*.*\*/)?\s*$')
STRING_PIECE_RE = re.compile(r'(?:u8|u|U|L)?R"(?P<d>[^()\\\s"]{0,16})\((?P<raw>.*?)\)(?P=d)"|'
                             r'(?:u8|u|U|L)?"(?P<s>(?:[^"\\\n]|\\.)*)"|(?P<id>[A-Za-z_]\w*)', re.S)
PRINTF_CONVERSION_RE = re.compile(r'%(?:%|[-+ #0\']*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|L|q|j|z|t)?[diouxXeEfFgGaAcspn])')
PRI_MACRO_RE = re.compile(r'PRI([diouxX])(8|16|32|64|PTR|MAX|LEAST8|LEAST16|LEAST32|LEAST64|FAST8|FAST16|FAST32|FAST64)$')
PRI_LENGTH = {'8': 'hh', '16': 'h', '32': '', '64': 'll', 'PTR': 'l', 'MAX': 'j'}

FAST_PARSER_PATHS = ('src/sql/parser/ob_fast_parser.cpp', 'src/sql/parser/ob_fast_parser.h')
FAST_PRODUCER_RE = re.compile(
    r'\bcur_token_type_\s*=\s*PARAM_TOKEN\b|\bnew_node\s*\(\s*\w+\s*,\s*(\w+)\s*\)|(?<![\w.>])(\w*type\w*)\s*=\s*(T_[A-Z0-9_]+)\b|'
    r'->\s*type_\s*=\s*(T_[A-Z0-9_]+)\b|\b(add_bool_type_node|add_null_type_node|lex_store_param)\s*\(')
FAST_T_ARG_CALL_RE = re.compile(r'\b([A-Za-z_]\w*)\s*\(([^;{}()]*\bT_[A-Z0-9_]+\b[^;{}()]*)\)')
FAST_NOT_CALLEES = frozenset(('if', 'while', 'for', 'switch', 'return', 'OZ', 'OX', 'OB_FAIL', 'OB_SUCC', 'OB_UNLIKELY',
                              'OB_LIKELY', 'CK', 'OV', 'new_node'))
FAST_NODE_HELPERS = frozenset(('new_node', 'reset_parser_node', 'lex_store_param', 'add_bool_type_node',
                               'add_null_type_node'))
FAST_ERROR_COND_RE = re.compile(
    r'^\s*!?\s*(?:OB_(?:FAIL|SUCC|ISNULL|NOT_NULL)\s*\(|\(?\s*(?:NULL|nullptr|OB_SUCCESS|ret)\s*[!=]=|'
    r'[^&|]*[!=]=\s*(?:NULL|nullptr|OB_SUCCESS|ret)\s*\)?\s*$)')
CASE_LABELS_RE = re.compile(r'((?:\b(?:case\s[^;{}]*?[^:]:(?!:)|default\s*:(?!:))\s*)+)\s*$')

SIZEOF_DIRS = ('src/sql/engine/', 'src/sql/code_generator/', 'src/sql/das/', 'src/sql/dtl/', 'src/query/api/query/engine/')
SIZEOF_RE = re.compile(r'\bsizeof\s*(?:\.\.\.\s*)?\(')
SIZEOF_SKIP_CALLEE_RE = re.compile(
    r'(?i)^(?:\w*alloc\w*|malloc|calloc|realloc|reserve\w*|prepare_allocate\w*|OB_NEW\w*|new|mem(?:cpy|set|move|cmp|ccpy)|'
    r'strn?cpy|strn?cmp|strncat|\w*printf|\w*serializ\w*|encode\w*|decode\w*|static_assert|STATIC_ASSERT|OB_ASSERT|assert|'
    r'abort_unless|set_string|assign_ptr|from_buf|write\w*|read\w*|append\w*|copy\w*|deep_copy\w*|push_back|\w*hash\w*|crc\w*|'
    r'ob_crc64\w*|bzero|placement_new|construct\w*|MEMSET|MEMCPY|MEMMOVE|MEMCMP|OB_UNIS\w*|LST_DO_CODE|OB_SERIALIZE\w*|'
    r'fill\w*|get_serialize_size|set_\w+|init_buf\w*|reset\w*|create_\w+|extend|prepare\w*|resize)$')
DECISION_WORD_RE = re.compile(
    r'(?i)batch_size|max_batch|batch_cnt|batch_row|bucket|dump|bypass|cache_size|l2_cache|l3_cache|mem_bound|max_bound|'
    r'mem_limit|memory_limit|max_mem|frame_size|rowset|max_row|row_cnt|row_count|part_cnt|part_shift|partition|threshold|'
    r'mem_size|mem_used|used_size|hold_size|data_size|extend_max|capacity|max_size')
DECISION_FUNCTION_RE = re.compile(
    r'(?i)batch_size|bucket|dump|bypass|mem_bound|cache_size|frame_size|header_size|mem_size|mem_used|partition|part_cnt')

WORK_AREA_SKIP = ('src/sql/engine/ob_sql_mem_mgr_processor.h', 'src/sql/engine/ob_sql_mem_mgr_processor.cpp',
                  'src/sql/engine/ob_sql_memory_manager.h', 'src/sql/engine/ob_sql_memory_manager.cpp')
WORK_AREA_CALL_RE = re.compile(
    r'\b([A-Za-z_]\w*(?:mem_processor|mem_mgr_processor)\w*)\s*(?:\.|->)\s*(init|alloc|update_used_mem_size|'
    r'update_cache_size|update_max_available_mem_size_periodically|extend_max_memory_size|set_number_pass|'
    r'update_delta_used_mem_size)\s*\(')
WORK_AREA_OWNER_PATTERN = r'mem_processor|sql_mem|profile_|mem_context'
ROW_STORE_DIRS = ('src/sql/engine/', 'src/query/api/query/engine/')
WORK_AREA_FIGURE_NAMES = ('get_mem_bound', 'get_max_bound', 'get_max_available_mem_size', 'get_expect_size', 'get_cache_size',
                          'get_data_size')
WORK_AREA_FIGURE_RE = re.compile(
    r'\b(get_mem_bound|get_max_bound|get_max_available_mem_size|get_expect_size|get_cache_size|get_data_size)\s*\(\s*\)')
WORK_AREA_DECISION_RE = re.compile(r'(?<![<>=!-])(?:<=|>=|<(?![<=])|>(?![>=]))(?!=)')

MEMMOVE_RE = re.compile(r'(?<![\w.>])(?:std\s*::\s*)?(MEMMOVE|memmove|__builtin_memmove)\s*\(')
MEMMOVE_PRIMITIVES = frozenset(('ob_memmove_safe',))

OPERATOR_FILE_RE = re.compile(r'^src/sql/engine/.*_op(?:_impl)?\.(?:h|cpp|ipp)$')
FRAME_INTERNAL_RE = re.compile(
    r'\b(frames_|frame_idx_|datum_off_|res_buf_off_|res_buf_len_|eval_info_off_|eval_flags_off_|pvt_skip_off_|'
    r'dyn_buf_header_offset_|vector_header_off_|locate_expr_datum|locate_batch_datums|locate_datum_for_write|'
    r'locate_datums_for_update|locate_param_datum|locate_param_datumvector|locate_expr_datumvector|get_evaluated_flags|'
    r'get_eval_info|get_pvt_skip|get_str_res_mem|get_reset_tmp_alloc|reset_ptr_in_datum|clear_evaluated_flag|'
    r'set_evaluated_flag|set_evaluated_projected|BatchInfoScopeGuard|set_batch_idx|set_batch_size|get_batch_idx|'
    r'get_batch_size)\b|\b(reinterpret_cast\s*<\s*(?:const\s+)?(?:common\s*::\s*)?ObDatum\b)')
FRAME_INTERNAL_R05 = frozenset(('frames_', 'locate_batch_datums', 'locate_expr_datum', 'locate_datum_for_write',
                                'get_evaluated_flags', 'get_eval_info', 'get_pvt_skip', 'reinterpret_cast<ObDatum'))

SQL_QUALIFIED_RE = re.compile(r'(?<![\w])(?:(?:::\s*)?oceanbase\s*::\s*)?sql\s*::\s*(~?[A-Za-z_]\w*)')
USING_SQL_RE = re.compile(
    r'\busing\s+namespace\s+(?:::\s*)?(?:oceanbase\s*::\s*)?sql\s*;|\busing\s+(?:::\s*)?(?:oceanbase\s*::\s*)?sql\s*::\s*'
    r'([A-Za-z_]\w*)\s*;')
NAMESPACE_OPEN_RE = re.compile(r'\bnamespace\s+([A-Za-z_][\w:]*)\s*\{')
SQL_NAMESPACE_OPEN_RE = re.compile(r'\bnamespace\s+(?:oceanbase\s*::\s*)?sql\s*\{')
NS_TYPE_RE = re.compile(
    r'\b(?:class|struct|union|enum(?:\s+class|\s+struct)?)\s+(?:(?:alignas|__attribute__|DEF_ALIGN)\s*\((?:[^()]|\([^()]*\))*\)\s*)*'
    r'([A-Za-z_]\w*)\s*(?:final\s*)?(?::(?!:)[^;{}]*)?\{|\busing\s+([A-Za-z_]\w*)\s*=|'
    r'\btypedef\b[^;{}]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*;')

RULE_46_TRACEPOINTS = (311, 1200, 2206, 2501)
TRACEPOINT_DEF_PATH = 'src/oblib/lib/utility/ob_tracepoint_def.h'
TRACEPOINT_INIT_SQL = 'tools/deploy/init.sql'
TRACEPOINT_DIRS = ('src/sql/', 'src/query/', 'src/storage/access/', 'src/storage/blocksstable/', 'src/share/vector/',
                   'src/share/aggregate/', 'src/oblib/lib/utility/ob_sort.h')
GLOBAL_TP_RE = re.compile(r'\bEventTable\s*::\s*(EN_\w+)')
LOCAL_TP_DEF_RE = re.compile(r'\bERRSIM_POINT_DEF\s*\(\s*([A-Za-z_]\w*)')
TP_SET_CALLEE_RE = re.compile(r'^(?:TP_SET\w*|TP_SWITCH_GUARD|SET_TP\w*)$')
TP_TOPIC_RE = re.compile(r'(?i)BATCH|ROW_?COUNT|ROW_?CNT|ROWSET|GROUP_SIZE|DUMP|SORT|HASH|BYPASS|CACHE')

FACTORY_DECL_RE = re.compile(
    r'(?<![\w:.>~#])(?:(?:static|mutable)\s+)*(?:(?:::\s*)?(?:oceanbase\s*::\s*)?sql\s*::\s*)?ObRawExprFactory\s+'
    r'([A-Za-z_]\w*)\s*([;({])')
FACTORY_NEW_RE = re.compile(
    r'\bOB_NEW\w*\s*\(\s*(?:(?:::\s*)?(?:oceanbase\s*::\s*)?sql\s*::\s*)?ObRawExprFactory\b|'
    r'\bnew\s*(?:\((?:[^()]|\([^()]*\))*\)\s*)?(?:(?:::\s*)?(?:oceanbase\s*::\s*)?sql\s*::\s*)?ObRawExprFactory\s*\(')
EXPR_FACTORY_CALL_RE = re.compile(r'\bget_expr_factory\s*\(\s*\)')
COMPILE_CONTEXT_CTOR_RE = re.compile(
    r'\s*(?:(?:HEAP_VAR|SMART_VAR)\s*\(\s*(?:[A-Za-z_]\w*::)*((?:Ob\w*(?:Resolver|Transformer|Optimizer)\w*|ObResolverParams|'
    r'ObTransformerCtx|ObOptimizerContext))\s*,|(?:const\s+)?(?:[A-Za-z_]\w*::)*((?:Ob\w*(?:Resolver|Transformer|Optimizer)\w*|'
    r'ObResolverParams|ObTransformerCtx|ObOptimizerContext))\s+[A-Za-z_]\w*\s*\()')
DESIGN_EXEC_TIME_FACTORY_CALLERS = (
    ('ob_variable_set_executor.cpp', 119, 127, 'SET'),
    ('ob_outline_executor.cpp', 221, 233, 'the outline executor'),
    ('ob_load_data_impl.cpp', 94, 94, 'LOAD DATA'),
    ('ob_load_data_impl.cpp', 1752, 1752, 'LOAD DATA'),
    ('ob_spi.cpp', 657, 657, 'SPI'),
    ('ob_spi.cpp', 682, 682, 'SPI'),
    ('ob_pl_user_defined_agg_function.cpp', 40, 50, 'PL user-defined aggregates'),
)
COMPILATION_DIRS = ('src/sql/resolver/', 'src/sql/rewrite/', 'src/sql/optimizer/')

NULL_PUSH_RE = re.compile(r'(?:\.|->)\s*push_back\s*\(\s*(?:NULL|nullptr)\s*\)')
NULL_ELEMENT_RE = re.compile(r'(?:(?:\.|->)\s*at\s*\((?:[^()]|\([^()]*\))*\)|\[[^\[\]]+\])\s*=\s*(?:NULL|nullptr)\s*;')
NULL_FILL_RE = re.compile(r'(?:\.|->)\s*(prepare_allocate(?:_and_keep_count)?|extend_param_exprs)\s*\(')
NULL_ARRAY_DIRS = ('src/sql/resolver/', 'src/sql/rewrite/', 'src/sql/optimizer/', 'src/sql/code_generator/', 'src/pl/',
                   'src/sql/pl/', 'src/sql/printer/')
C_POINTER_ARRAY_RE = re.compile(r'\b((?:[A-Za-z_]\w*::)*[A-Z]\w*)\s*\*\s*([A-Za-z_]\w*)\s*\[[^\]]*\]\s*[;=]')
POINTER_ARRAY_TYPE_RE = re.compile(
    r'\b\w*(?:Array|List|Vector|Wrap)\s*<\s*(?:const\s+)?((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\*')
LOCAL_POINTER_ARRAY_RE = re.compile(
    r'\b\w*(?:Array|List|Vector|Wrap)\s*<\s*(?:const\s+)?((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\*[^;{}()]*?>\s*[&*]?\s*'
    r'([A-Za-z_]\w*)\s*[;,)=({]|\b((?:[A-Za-z_]\w*::)*[A-Z]\w*)\s*\*\s*\*\s*([A-Za-z_]\w*)\s*[;=,)]')
NULL_ARRAY_SKIP_DIRS = ('src/sql/parser/', 'src/pl/parser/')
IR_ARRAY_TYPE_RE = re.compile(
    r'\b(?:ObIArray|ObSEArray|ObArray|ObFixedArray|ObSqlArray|ObArrayWrap|ObList|Ob2DArray|ObRawExprUniqueSet)\s*<\s*'
    r'(?:const\s+)?([A-Z]\w*)\s*\*')

WARNING_BUFFER_PRIMITIVES = ('src/oblib/lib/oblog/ob_warning_buffer.h', 'src/oblib/lib/oblog/ob_warning_buffer.cpp')
WARNING_BUFFER_CALL_RE = re.compile(r'\b(ob_setup_tsi_warning_buffer|ob_setup_default_tsi_warning_buffer)\s*\(')
WARNING_BUFFER_SCOPE_RE = re.compile(r'\bObWarningBufferIgnoreScope\s+[A-Za-z_]\w*\s*[;({]')
WARNING_BUFFER_ASSIGN_RE = re.compile(r'\bob_get_tsi_warning_buffer\s*\(\s*\)\s*=(?!=)')
WARNING_BUFFER_LISTED = (
    ('obmp_base.h', 122, 128, 'the request entries, ObMPBase::setup_wb'),
    ('obmp_base.cpp', 369, 369, 'the end of a request'),
    ('obmp_init_db.cpp', 150, 150, 'the end of a request'),
    ('ob_mysql_end_trans_cb.cpp', 169, 169, 'the end of a request'),
    ('ob_mysql_end_trans_cb.cpp', 220, 220, 'the end of a request'),
    ('ob_worker_processor.cpp', 110, 111, 'the worker entry'),
    ('ob_req_queue_thread.cpp', 86, 87, 'the request-queue entry'),
    ('ob_px_task_process.cpp', 100, 101, 'the PX task entry'),
    ('ob_load_data_impl.cpp', 1188, 1188, 'an ignore scope'),
    ('ob_spi.cpp', 4953, 4953, 'an ignore scope'),
    ('ob_dynamic_sampling.cpp', 521, 521, 'an ignore scope'),
    ('ob_create_package_resolver.cpp', 202, 202, 'an ignore scope'),
    ('ob_trigger_handler.cpp', 447, 463, "the trigger call's private buffer"),
    ('ob_transform_utils.cpp', 10088, 10096, 'a warning probe'),
    ('ob_transform_utils.cpp', 10137, 10149, 'a warning probe'),
)


def aligned_blank(m):
    s = m.group(0)
    if s[0] == '/':
        return re.sub(r'[^\n]', ' ', s)
    q = min(i for i in (s.find('"'), s.find("'")) if i >= 0)
    return s[:q + 1] + re.sub(r'[^\n]', '\x01', s[q + 1:-1]) + s[-1:]


def aligned_view(src):
    view = getattr(src, '_aligned', None)
    if view is None:
        text = '\n'.join(src.raw)
        code = LEX_RE.sub(aligned_blank, text)
        lines = code.split('\n')
        starts = []
        pos = 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        view = (code, lines, starts, text)
        src._aligned = view
    return view


def aligned_offset(src, ln, rx, occurrence):
    code, lines, starts, text = aligned_view(src)
    if ln >= len(lines):
        return None
    for k, m in enumerate(rx.finditer(lines[ln])):
        if k == occurrence:
            return starts[ln] + m.end() - 1
    return None


def aligned_args(code, open_paren):
    depth = 0
    spans = []
    start = open_paren + 1
    for j in range(open_paren, len(code)):
        c = code[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                if code[start:j].strip() or spans:
                    spans.append((start, j))
                return spans, j
        elif c == ',' and depth == 1:
            spans.append((start, j))
            start = j + 1
        elif c == ';' and depth <= 1:
            break
    return spans, -1


def collapse(text, limit=160):
    t = ' '.join(text.replace('\x01', ' ').split())
    return t if len(t) <= limit else t[:limit - 3].rstrip() + '...'


def statement_span(code, pos):
    a = pos
    depth = 0
    while a > 0:
        c = code[a - 1]
        if c == ')':
            depth += 1
        elif c == '(':
            if depth == 0:
                pass
            else:
                depth -= 1
        elif c in ';{}' and depth == 0:
            break
        a -= 1
    b = pos
    depth = 0
    n = len(code)
    while b < n:
        c = code[b]
        if c == '(':
            depth += 1
        elif c == ')':
            if depth > 0:
                depth -= 1
        elif c in ';{}' and depth == 0:
            break
        b += 1
    return a, b


def paren_depth_between(code, a, b):
    depth = 0
    for c in code[a:b]:
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
    return depth


def innermost_function(src, ln, off=None):
    best = None
    for f in src.function_spans():
        if f[1] > ln:
            break
        if f[3] >= ln:
            open_off = src.body_open.get((f[0], f[1]))
            if open_off is None or off is not None and off <= open_off:
                continue
            if best is None or f[1] >= best[1]:
                best = f
    return best


def innermost_class(src, ln):
    src.scan()
    best = None
    for c in src.classes:
        if c[2] <= ln <= c[3] and (best is None or c[1] >= best[1]):
            best = c
    return best


def pass_e(repo, paths):
    sql_types = set()
    other_types = set()
    printf_defs = []
    wb_wrappers = []
    lock_uses = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    fast_funcs = []
    attr_names = set()
    ectx = _CTX or {}
    spin_re = re.compile(ectx['spin_pattern']) if ectx.get('spin_pattern') else None
    extras = {'rets': collections.defaultdict(set), 'str_consts': collections.defaultdict(set), 'sizeof_consts': [],
              'wa_getters': []}
    for path in paths:
        src = read_source(repo, path)
        code = src.code
        stem = src.stem()
        if 'namespace' in code:
            namespace_types(src, sql_types, other_types)
        if '...' in code or 'va_list' in code:
            for fname, hl, ol, end, extra in src.function_spans():
                info = printf_signature(extra[3])
                if info is not None:
                    printf_defs.append((extra[1], info[0], info[1], path, hl + 1))
        if '__attribute__' in code and 'printf' in code:
            for m in PRINTF_ATTR_RE.finditer(code):
                attr_names.add(m.group(1))
        if ('tsi_warning_buffer' in code or 'ObWarningBufferIgnoreScope' in code) and path not in WARNING_BUFFER_PRIMITIVES:
            for fname, hl, ol, end, extra in src.function_spans():
                open_off = src.body_open.get((fname, hl))
                if open_off is None:
                    continue
                body = code[open_off:src.starts[end] + len(src.lines[end])]
                changes = len(WARNING_BUFFER_CALL_RE.findall(body)) + len(WARNING_BUFFER_SCOPE_RE.findall(body)) \
                    + len(WARNING_BUFFER_ASSIGN_RE.findall(body))
                if changes == 1 and WARNING_BUFFER_CALL_RE.search(body) and body.count(';') <= 3:
                    wb_wrappers.append((extra[1], fname, path, hl + 1))
        if not path.startswith(LOCK_PRIMITIVE_DIR):
            for key, counts in lock_use_counts(src, spin_re).items():
                acc = lock_uses[(stem,) + key]
                for k in range(5):
                    acc[k] += counts[k]
        if path in FAST_PARSER_PATHS:
            fast_funcs += fast_parser_function_facts(src)
        collect_return_types(src, extras['rets'])
        if 'char' in code and '=' in code:
            collect_string_constants(src, extras['str_consts'])
        if path.startswith(SIZEOF_DIRS) and 'sizeof' in code:
            collect_sizeof_constants(src, extras['sizeof_consts'])
        if path.startswith(WORK_AREA_DIRS) and path not in WORK_AREA_SKIP:
            collect_work_area_getters(src, extras['wa_getters'])
    extras['rets'] = {k: frozenset(v) for k, v in extras['rets'].items()}
    extras['str_consts'] = {k: frozenset(v) for k, v in extras['str_consts'].items()}
    return sql_types, other_types, printf_defs, wb_wrappers, dict(lock_uses), fast_funcs, attr_names, extras


TYPE_SPECIFIER_RE = re.compile(
    r'\b(?:inline|static|virtual|extern|explicit|constexpr|friend|register|mutable|thread_local|typename|OB_INLINE|'
    r'OB_NOINLINE|ALWAYS_INLINE|NEVER_INLINE|__inline__|__inline|OB_WARN_UNUSED_RESULT|OB_NOTHROW|OB_WEAK_SYMBOL|'
    r'OB_NORETURN|__THROW)\b|__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)|\bDEF_ALIGN\s*\([^()]*\)')
STRING_CONST_DECL_RE = re.compile(
    r'(?<![\w:.>])(?:(?:static|constexpr|extern|inline)\s+)*(?:(const)\s+)?char\s*(const\s*)?(\*\s*(?:const\s*)?)?'
    r'((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*(\[[^\]]*\]\s*)?=\s*')
SIZEOF_CONST_DECL_RE = re.compile(
    r'(?<![\w:.>])(?:(?:static|constexpr|const|inline)\s+)+(?:(?:unsigned|signed)\s+)?(?:u?int(?:8|16|32|64)?_t|int|long|'
    r'size_t|uint32|uint64|int32|int64)\s+([A-Za-z_]\w*)\s*=\s*([^;{}]*\bsizeof\s*\([^;{}]*);')
SIZEOF_CONST_DEFINE_RE = re.compile(r'^\s*#\s*define\s+([A-Za-z_]\w*)\s+(.*\bsizeof\s*\(.*)$')
WORK_AREA_DIRS = ('src/sql/', 'src/query/', 'src/storage/', 'src/share/')


def norm_type(t):
    if t and ' or ' in t:
        return ' or '.join(norm_type(x) for x in t.split(' or '))
    t = TYPE_SPECIFIER_RE.sub(' ', t or '')
    t = re.sub(r'\s*::\s*', '::', t)
    t = re.sub(r'\s*([*&]+)\s*', r' \1', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'^(?:::)?(?:oceanbase::)?', '', t)
    return t


def collect_return_types(src, rets):
    src.scan(want_decls=True)
    for fname, hl, ol, end, extra in src.function_spans():
        rt = norm_type(extra[2])
        if rt:
            rets[(enclosing_class(fname), extra[1])].add(rt)
    for decl in src.decls:
        scope, chain, stmt, ln = decl[:4]
        if '(' not in stmt:
            continue
        p = parse_statement(stmt)
        if p and p[0] == 'func':
            rt = norm_type(p[2])
            if rt:
                rets[(last_component(chain) if scope == 'class' and chain else '', p[1])].add(rt)


STRING_TOKEN = (r'(?:(?:u8|u|U|L)?R"(?P<rd>[^()\\\s"]{0,16})\((?:(?!\)(?P=rd)").)*\)(?P=rd)"|'
                r'(?:u8|u|U|L)?"(?:[^"\\\n]|\\.)*"|[A-Z_][A-Z0-9_]*\b)')
STRING_ONLY_RE = re.compile(r'\s*(?:%s)(?:\s*(?:%s))*\s*' % (STRING_TOKEN, STRING_TOKEN.replace('rd', 'rd2')), re.S)


def literal_values(raw, string_macros=None):
    raw = re.sub(r'\\\r?\n', '', raw).strip()
    while raw.startswith('(') and matching_close(raw, 0) == len(raw) - 1:
        raw = raw[1:-1].strip()
    if raw.startswith('{'):
        close = matching_close(raw, 0)
        inner = raw[1:close] if close > 0 else raw[1:]
        out = []
        for piece in split_top(inner):
            out += literal_values(piece, string_macros)
        return out
    q = split_top_operator(raw, ('?',))
    if q is not None:
        colon = split_top_operator(raw[q[0] + 1:], (':',))
        if colon is not None:
            a = literal_values(raw[q[0] + 1:q[0] + 1 + colon[0]], string_macros)
            b = literal_values(raw[q[0] + 2 + colon[0]:], string_macros)
            return a + b if a and b else []
    if not STRING_ONLY_RE.fullmatch(raw):
        return []
    has, value, macros = format_text(raw, string_macros)
    return [value] if has else []


def collect_string_constants(src, out):
    code, lines, starts, text = aligned_view(src)
    for m in STRING_CONST_DECL_RE.finditer(code):
        if not (m.group(1) or m.group(2) and m.group(3)):
            continue
        ln = bisect.bisect_right(starts, m.start()) - 1
        f = innermost_function(src, ln)
        if f is not None and src.body_open.get((f[0], f[1])) is not None and ln > src.line_of(src.body_open[(f[0], f[1])]):
            continue
        a = m.end()
        depth = 0
        k = a
        while k < len(code):
            c = code[k]
            if c in '([{':
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif c in ';,' and depth == 0:
                break
            k += 1
        values = literal_values(text[a:k])
        if values:
            name = last_component(re.sub(r'\s+', '', m.group(4)))
            for v in values:
                out[name].add(v)


def collect_sizeof_constants(src, out):
    code = src.code
    for m in SIZEOF_CONST_DECL_RE.finditer(code):
        ln = src.line_of(m.start(1))
        if innermost_function(src, ln, m.start(1)) is not None:
            continue
        out.append((m.group(1), src.path, ln, collapse(m.group(2), 120)))
    for name, params, lns in define_bodies(src):
        if params:
            continue
        body = ' '.join(src.lines[k].rstrip().rstrip('\\') for k in lns)
        dm = SIZEOF_CONST_DEFINE_RE.match(body)
        if dm:
            out.append((dm.group(1), src.path, lns[0], collapse(dm.group(2), 120)))


def collect_work_area_getters(src, out):
    code = src.code
    if 'get_mem_bound' not in code and 'get_max_bound' not in code and 'get_cache_size' not in code \
            and 'get_expect_size' not in code and 'get_data_size' not in code and 'get_max_available_mem_size' not in code:
        return
    for fname, hl, ol, end, extra in src.function_spans():
        open_off = src.body_open.get((fname, hl))
        if open_off is None or end - hl > 6:
            continue
        body = code[open_off + 1:src.starts[end] + len(src.lines[end])]
        rm = re.fullmatch(r'\s*return\s+([^;]*);\s*}\s*', body)
        if not rm:
            continue
        calls = frozenset(c.group(1) for c in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', rm.group(1)))
        out.append((extra[1], enclosing_class(fname), calls, rm.group(1).strip(), src.path, hl))


def namespace_types(src, sql_types, other_types):
    src.scan()
    code = src.code
    spans = []
    for m in NAMESPACE_OPEN_RE.finditer(code):
        close = matching_close(code, m.end() - 1)
        spans.append((m.end() - 1, close if close >= 0 else len(code), m.group(1).split('::')))
    if not spans:
        return
    bodies = [(c[2], c[3]) for c in src.classes] + [(f[2], f[3]) for f in src.function_spans()]
    for m in NS_TYPE_RE.finditer(code):
        name = m.group(1) or m.group(2) or m.group(3)
        if not name:
            continue
        ln = src.line_of(m.start())
        if any(o < ln <= e for o, e in bodies):
            continue
        chain = []
        for o, c, parts in spans:
            if o < m.start() < c:
                chain += parts
        if not chain:
            continue
        if chain[-1] == 'sql':
            sql_types.add(name)
        else:
            other_types.add(name)


def printf_signature(args):
    params = [p.strip() for p in split_top(args)]
    if len(params) >= 2 and params[-1] == '...':
        if PRINTF_FMT_PARAM_RE.search(params[-2].split('=', 1)[0]):
            return len(params) - 2, 'variadic'
        return None
    for i, p in enumerate(params):
        if re.search(r'\bva_list\b', p) and i > 0 and PRINTF_FMT_PARAM_RE.search(params[i - 1].split('=', 1)[0]):
            return i - 1, 'va_list'
    return None


def lock_expr_name(expr):
    e = re.sub(r'\s+', '', strip_casts(expr))
    e = strip_outer(e)
    e = re.sub(r'^(?:this->)+', '', e)
    m = re.search(r'([A-Za-z_]\w*)(\(\))?(?:\[[^\[\]]*\])*$', e)
    if not m:
        return None
    return m.group(1) + ('()' if m.group(2) else '')


def lock_expr_other(expr):
    e = re.sub(r'\s+', '', strip_casts(expr))
    e = strip_outer(e)
    e = re.sub(r'^(?:this->)+', '', e)
    e = re.sub(r'(?:\[[^\[\]]*\])+$', '', e)
    e = re.sub(r'\(\)$', '', e)
    return bool(re.search(r'(?:\.|->)[A-Za-z_]\w*$', e))


def timed_arg(text):
    for word in re.findall(r'[A-Za-z_]\w*', text):
        if TIMED_ARG_WORDS & set(name_tokens(word)):
            return True
    return False


def lock_use_keys(src, off, other):
    if other:
        return (('other', ''),)
    ln = src.line_of(off)
    if src.dkind.get(ln) in ('define', 'cont'):
        return (('stem', ''), ('macro', ''))
    f = innermost_function(src, ln, off)
    keys = [('stem', '')]
    if f is not None:
        keys.append(('func', f[0]))
        cls = enclosing_class(f[0])
        if cls:
            keys.append(('class', cls))
    return tuple(keys)


def lock_use_counts(src, spin_re=None):
    code = src.code
    out = collections.defaultdict(lambda: [0, 0, 0, 0, 0])

    def add(name, off, other, idx, timed=False, tried=False):
        for key in lock_use_keys(src, off, other):
            acc = out[(key[0], key[1], name)]
            acc[idx] += 1
            if timed:
                acc[2] += 1
            if tried:
                acc[3] += 1

    if 'Guard' in code or 'lock_guard' in code or 'unique_lock' in code:
        for m in LOCK_GUARD_RE.finditer(code):
            gtype = re.sub(r'\s+', '', m.group(1))
            if not LOCK_GUARD_TYPE_RE.search(gtype.rsplit('::', 1)[-1]):
                continue
            args = split_call_args(code, m.start(2)) if m.group(2) == '(' else []
            if not args:
                continue
            timed = bool(re.search(r'Timeout|Retry', gtype) or any(timed_arg(a) for a in args[1:]))
            named = set()
            for arg in args:
                if re.fullmatch(r'\s*(?:this|nullptr|NULL|true|false|\d+\w*)\s*', arg) or timed_arg(arg) and arg is not args[0]:
                    continue
                name = lock_expr_name(arg)
                if not name or name in named or re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
                    continue
                named.add(name)
                add(name, m.start(), lock_expr_other(arg), 0, timed=timed, tried='Try' in gtype)
    for m in LOCK_CALL_RE.finditer(code):
        name = m.group(1) + ('()' if m.group(3) else '')
        op = m.group(4)
        before = code[max(0, m.start(1) - 8):m.start(1)]
        other = bool(re.search(r'(?:\.|->)\s*$', before)) and not re.search(r'\bthis\s*->\s*$', before)
        timed = tried = False
        if op in LOCK_ACQUIRE_OPS:
            args = balanced_call_args(code, m.end() - 1)
            timed = op in ('timedlock', 'lock_timeout', 'try_lock_for', 'try_lock_until') or timed_arg(args)
            tried = op.startswith('try') or op == 'trylock'
        add(name, m.start(1), other, 1, timed=timed, tried=tried)
    if 'pthread_' in code:
        for m in PTHREAD_LOCK_CALL_RE.finditer(code):
            op = m.group(2)
            if op in ('init', 'destroy') or op.startswith('attr'):
                continue
            name = lock_expr_name(m.group(3))
            if not name:
                continue
            add(name, m.start(), lock_expr_other(m.group(3)), 1, timed='timed' in op, tried='try' in op)
    if spin_re is not None and spin_re.search(code):
        for m in spin_re.finditer(code):
            args = split_call_args(code, m.end() - 1)
            if not args:
                continue
            name = lock_expr_name(args[0])
            if name and not name.endswith('()'):
                add(name, m.start(), lock_expr_other(args[0]), 4)
    for m in LOCK_CALL_RE.finditer(code):
        if m.group(4) not in LOCK_ACQUIRE_OPS:
            continue
        args = split_call_args(code, m.end() - 1)
        if args and args[0].lstrip().startswith('&'):
            name = lock_expr_name(args[0])
            if name and not name.endswith('()'):
                add(name, m.start(), lock_expr_other(args[0]), 4)
    return out


def fast_parser_function_facts(src):
    out = []
    code = src.code
    for fname, hl, ol, end, extra in src.function_spans():
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        body = code[open_off:src.starts[end] + len(src.lines[end])]
        calls = frozenset(m.group(3) for m in IDENT_CALL_RE.finditer(body))
        out.append((extra[1], fast_direct_producer(body), calls))
    for name, params, lns in define_bodies(src):
        body = '\n'.join(src.lines[k] for k in lns)
        if fast_direct_producer(body):
            out.append((name, True, frozenset()))
    return out


def fast_direct_producer(body):
    for m in FAST_PRODUCER_RE.finditer(body):
        if m.group(2) == 'cur_token_type_' or 'T_INVALID' in (m.group(3), m.group(4)):
            continue
        return True
    return False


def lock_class_facts(repo, classes, candidates, func_index):
    by_path = collections.defaultdict(list)
    for c in classes:
        if c[1] in candidates:
            by_path[c[3]].append(c)
    held = collections.defaultdict(list)
    sources = {}
    for path in sorted(by_path):
        src = read_source(repo, path)
        src.scan(want_decls=True)
        sources[path] = src
        spans = []
        for name, hl, ol, end, extra in src.classes:
            if extra is None or extra[1] not in candidates:
                continue
            special = re.search(r'\b(?:class|struct|union)\s+(?:[A-Za-z_]\w*\s*::\s*)*' + re.escape(extra[1]) + r'\s*<',
                                extra[3]) is not None
            spans.append((name, hl, end, extra[1], special))
        for decl in src.decls:
            scope, chain, stmt, ln = decl[:4]
            if scope != 'class':
                continue
            owner = next((sp for sp in spans if sp[0] == chain and sp[1] <= ln <= sp[2]), None)
            if owner is None or owner[4]:
                continue
            p = parse_statement(stmt)
            if not p or p[0] == 'func':
                continue
            typ = p[1]
            if typ.startswith(('typedef', 'using')) or re.search(r'\btypedef\b', typ):
                continue
            if re.search(r'[*&]', drop_template_args(typ)):
                continue
            held[owner[3]].append(typ)
    ops = collections.defaultdict(set)
    for cls, simple, path, hl, end in func_index:
        if simple not in LOCK_OP_NAMES or cls not in candidates:
            continue
        src = sources.get(path)
        if src is None:
            src = read_source(repo, path)
            sources[path] = src
        span = next((f for f in src.function_spans() if f[1] == hl and f[4][1] == simple), None)
        if span is not None:
            rettype, args = span[4][2] or '', (span[4][3] or '').strip()
            if (not args or args == 'void') and LOCK_ACCESSOR_RET_RE.search(rettype):
                continue
        ops[cls].add(simple)
    return held, ops


def define_aliases(defines):
    out = []
    for d in defines:
        name, params, body, path, ln, flag, texts = d
        if params is not None:
            continue
        m = re.match(r'\s*#\s*define\s+\w+\s+((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*$', body)
        if m and m.group(1) != name:
            out.append((name, m.group(1), path, ln, texts[0] if texts else '', flag, ''))
    return out


def lock_type_table(repo, classes, aliases, members, func_index, defines):
    kinds = dict(LOCK_PRIMITIVES)
    for name in sorted(descendants(classes, set(LOCK_PRIMITIVES)) - set(kinds)):
        kinds[name] = 'lock subclass'
    aliases = list(aliases) + define_aliases(defines)
    candidates = sorted(set(c[1] for c in classes if c[1] and LOCK_WRAPPER_NAME_RE.search(c[1]) and 'Guard' not in c[1]
                            and c[1] not in kinds and not c[3].startswith(LOCK_PRIMITIVE_DIR)))
    held, ops = lock_class_facts(repo, classes, set(candidates), func_index)
    wrappers = {}
    while True:
        alias_names = alias_closure(aliases, set(kinds))
        known = set(kinds) | alias_names
        added = False
        for c in candidates:
            if c in kinds:
                continue
            inner = sorted(set(w for t in held.get(c, ()) for w in re.findall(r'[A-Za-z_]\w*', t) if w in known))
            if inner or ops.get(c):
                kinds[c] = 'lock class'
                wrappers[c] = ('holds ' + ', '.join(inner)) if inner else ('defines ' + ', '.join(sorted(ops[c])))
                added = True
        if not added:
            break
    class_stems = collections.defaultdict(set)
    for c in classes:
        if c[1] in kinds:
            class_stems[c[1]].add(c[3].rsplit('.', 1)[0])
    targets = collections.defaultdict(set)
    for alias, target, path, ln, text, flag, chain in aliases:
        targets[alias].add((chain, path, path.rsplit('.', 1)[0], outer_type(target)))
    lockish = set(kinds)
    changed = True
    while changed:
        changed = False
        for alias in sorted(targets):
            if alias not in lockish and any(t[3] in lockish and t[3] != alias for t in targets[alias]):
                lockish.add(alias)
                changed = True
    entries = {a: tuple(sorted(v)) for a, v in targets.items() if a in lockish}
    return kinds, wrappers, entries, {k: frozenset(v) for k, v in class_stems.items()}


def printf_tables(printf_defs, func_defs, defines, attr_names):
    by_name = collections.defaultdict(list)
    for name, idx, kind, path, ln in printf_defs:
        by_name[name].append((idx, kind))
    funcs = {}
    for name, (idx, kind) in C_PRINTF_FUNCTIONS.items():
        funcs[name] = (frozenset((idx,)), kind, PRINTF_SINKS.get(name, ''), False)
    for name in sorted(by_name):
        entries = by_name[name]
        if name in funcs:
            continue
        mixed = len(entries) < 0.8 * max(1, func_defs.get(name, 0))
        kinds = set(k for i, k in entries)
        funcs[name] = (frozenset(i for i, k in entries), 'va_list' if kinds == {'va_list'} else 'variadic',
                       PRINTF_SINKS.get(name, ''), mixed)
    macros = {}
    function_like = [d for d in defines if d[1] is not None]
    for _ in range(12):
        changed = False
        for d in function_like:
            name, params, body = d[0], d[1], d[2]
            if name in macros or name in funcs:
                continue
            found = printf_macro_format(params, body, funcs, macros)
            if found is not None:
                macros[name] = found
                changed = True
        if not changed:
            break
    return funcs, macros


def printf_macro_format(params, body, funcs, macros):
    names = [p.strip() for p in params]
    variadic = None
    if names and names[-1].endswith('...'):
        variadic = len(names) - 1
    plain = [re.sub(r'\.\.\.$', '', n).strip() for n in names]
    head = re.match(r'\s*#\s*define\s+\w+(?:\([^)]*\))?', body)
    text = PRINTF_LOG_JOIN_RE.sub(lambda m: ('_SQL' if m.group(1) else 'SQL') + m.group(2) + '(',
                                  body[head.end():] if head else body)
    for m in re.finditer(r'(?<![\w])([A-Za-z_]\w*)\s*\(', text):
        callee = m.group(1)
        if callee in funcs:
            idxs, kind, sink, mixed = funcs[callee]
            sinks = {sink} if sink else set()
        elif callee in macros:
            idxs, sinks = frozenset((macros[callee][0],)), set(macros[callee][1])
        else:
            continue
        args = [a.strip() for a in split_top(balanced_call_args(text, m.end() - 1))]
        if any(i < len(args) and ('""' in args[i] or "' '" in args[i]) for i in idxs):
            continue
        pick = None
        for i in sorted(idxs):
            if i < len(args):
                a = args[i].lstrip('#').strip()
                if a == '__VA_ARGS__' or variadic is not None and plain[variadic] and a == plain[variadic]:
                    pick = variadic
                    break
                if a in plain[:len(plain) if variadic is None else variadic]:
                    pick = plain.index(a)
                    break
        if pick is None and variadic is not None:
            vi = next((i for i, a in enumerate(args) if a.lstrip('#').strip() == '__VA_ARGS__'
                       or plain[variadic] and a.lstrip('#').strip() == plain[variadic]), None)
            if vi is not None and vi > 0:
                prev = args[vi - 1].strip()
                if prev in plain[:variadic] and re.search(r'(?i)fmt|format|msg', prev):
                    pick = plain.index(prev)
        if pick is not None:
            return pick, tuple(sorted(sinks)), variadic is not None and pick in (variadic, variadic - 1)
    return None


def tracepoint_tables(repo):
    numbers = {}
    names = {}
    p = os.path.join(repo, TRACEPOINT_DEF_PATH)
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            for m in re.finditer(r'GLOBAL_ERRSIM_POINT_DEF\s*\(\s*(\d+)\s*,\s*(\w+)', fh.read()):
                numbers[m.group(2)] = int(m.group(1))
                names[int(m.group(1))] = m.group(2)
    init = set()
    p = os.path.join(repo, TRACEPOINT_INIT_SQL)
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            for m in re.finditer(r'set_tp\s+tp_no\s*=\s*(\d+)', re.sub(r'(?m)^\s*(?:--|#).*$', '', fh.read())):
                init.add(int(m.group(1)))
    return numbers, names, frozenset(init)


def new_list_context(repo, paths, jobs, classes, aliases, members, func_index, func_defs, defines, methods, method_names,
                     ir_types):
    sql_types, other_types, printf_defs, wb_wrappers = set(), set(), [], []
    lock_uses = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    fast_facts = []
    attr_names = set()
    spin_names = set(SPIN_ACQUIRE_PRIMITIVES)
    function_like = [d for d in defines if d[1] is not None]
    for _ in range(4):
        spin_call_re = re.compile(r'(?<![\w.>])(?:%s)\s*\(' % '|'.join(re.escape(n) for n in sorted(spin_names)))
        added = set(d[0] for d in function_like if d[0] not in spin_names and spin_call_re.search(d[2]))
        if not added:
            break
        spin_names |= added
    spin_pattern = r'(?<![\w.>])(?:%s)\s*\(' % '|'.join(re.escape(n) for n in sorted(spin_names, key=lambda n: (-len(n), n)))
    e_ctx = {'not_built': _NOT_BUILT, 'spin_pattern': spin_pattern}
    rets = collections.defaultdict(set)
    str_consts = collections.defaultdict(set)
    sizeof_consts = collections.defaultdict(list)
    wa_getters = []
    for st, ot, pd, wb, lu, ff, an, ex in run_parallel(pass_e, repo, paths, jobs, ctx=e_ctx, init=set_ctx):
        for k, v in ex['rets'].items():
            rets[k] |= v
        for k, v in ex['str_consts'].items():
            str_consts[k] |= v
        for name, path, ln, expr in ex['sizeof_consts']:
            sizeof_consts[name].append((path, ln, expr))
        wa_getters += ex['wa_getters']
        attr_names |= an
        sql_types |= st
        other_types |= ot
        printf_defs += pd
        wb_wrappers += wb
        for k, v in lu.items():
            acc = lock_uses[k]
            for i in range(5):
                acc[i] += v[i]
        fast_facts += ff
    lock_use_owners = collections.defaultdict(list)
    for stem, kind, who, name in lock_uses:
        if kind == 'class':
            lock_use_owners[(stem, name)].append((kind, who))
    lock_use_owners = {k: tuple(sorted(v)) for k, v in lock_use_owners.items()}
    kinds, wrappers, alias_entries, class_stems = lock_type_table(repo, classes, aliases, members, func_index, defines)
    printf_funcs, printf_macros = printf_tables(printf_defs, func_defs, defines, attr_names)
    printf_overloads = printf_overload_table(repo, printf_funcs, printf_defs, func_index)
    member_types = collections.defaultdict(dict)
    global_types = collections.defaultdict(list)
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        t = norm_type(typ)
        if re.search(r'\b' + re.escape(nm) + r'\s*\[', text or '') and not t.endswith(']'):
            t += ' []'
        if scope == 'class' and owner:
            member_types[last_component(owner)].setdefault(nm, t)
        elif scope != 'class':
            global_types[nm].append((stem, t))
    type_aliases = {}
    alias_owners = collections.defaultdict(set)
    for alias, target, path, ln, text, flag, chain in aliases:
        owner = last_component(chain) if chain else ''
        type_aliases.setdefault((owner, alias), norm_type(target))
        if owner:
            alias_owners[alias].add(owner)
    comma_macros = {}
    for d in defines:
        if d[1] is None or not d[1]:
            continue
        head = re.match(r'\s*#\s*define\s+\w+\s*\([^)]*\)', d[2])
        body = d[2][head.end():].strip() if head else ''
        pieces = split_top(body)
        if len(pieces) > 1 and all(p.strip() for p in pieces) and not re.search(r'[;{}]', body):
            comma_macros.setdefault(d[0], (tuple(x.strip() for x in d[1]), tuple(p.strip() for p in pieces)))
    macro_calls = {}
    for d in defines:
        if d[1] is None:
            continue
        head = re.match(r'\s*#\s*define\s+\w+\s*\([^)]*\)', d[2])
        body = d[2][head.end():].strip() if head else ''
        cm = re.match(r'(?:\(\s*)?(?:static_cast|reinterpret_cast)\s*<\s*([^<>]+?)\s*>\s*\(', body)
        if cm:
            macro_calls[d[0]] = ('cast', norm_type(cm.group(1)))
            continue
        cm = re.match(r'(?:::)?(?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)\s*\(.*\)\s*$', body, re.S)
        if cm and cm.group(1) not in ('do', 'if', 'for', 'while', 'switch'):
            macro_calls.setdefault(d[0], ('call', cm.group(1)))
    figure_names = set(WORK_AREA_FIGURE_NAMES)
    wa_wrapper_rows = {}
    for _ in range(4):
        added = False
        for simple, cls, calls, expr, path, hl in wa_getters:
            if simple in wa_wrapper_rows and cls in wa_wrapper_rows[simple][0]:
                continue
            direct = calls & set(WORK_AREA_FIGURE_NAMES) and re.search(WORK_AREA_OWNER_PATTERN, expr)
            if direct or calls & (set(wa_wrapper_rows) - set(WORK_AREA_FIGURE_NAMES)):
                figure_names.add(simple)
                prev = wa_wrapper_rows.get(simple)
                wa_wrapper_rows[simple] = ((prev[0] if prev else frozenset()) | {cls}, expr, path, hl + 1)
                added = True
        if not added:
            break
    producers = set(n for n, direct, calls in fast_facts if direct)
    changed = True
    while changed:
        changed = False
        for n, direct, calls in fast_facts:
            if n not in producers and calls & producers:
                producers.add(n)
                changed = True
    tp_numbers, tp_names, tp_init = tracepoint_tables(repo)
    array_getters = collections.Counter()
    for n, rt in methods:
        tm = IR_ARRAY_TYPE_RE.search(rt)
        if tm and tm.group(1) in ir_types:
            array_getters[n] += 1
    factory_macros = set(d[0] for d in defines if FACTORY_DECL_RE.search(d[2]))
    string_macros = {}
    for d in defines:
        if d[1] is not None or d[0] in string_macros:
            continue
        sm = STRING_MACRO_RE.match(' '.join(t.rstrip('\\').strip() for t in d[6]))
        if sm:
            string_macros[d[0]] = ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', sm.group(1)))
    pending = []
    for d in defines:
        if d[1] is not None or d[0] in string_macros:
            continue
        body = ' '.join(t.rstrip('\\').strip() for t in d[6])
        bm = re.match(r'\s*#\s*define\s+\w+\s+(.*?)\s*(?://.*|/\*.*\*/)?$', body)
        if bm and STRING_ONLY_RE.fullmatch(bm.group(1)) \
                and re.search(r'[A-Z_][A-Z0-9_]*', re.sub(r'"(?:[^"\\]|\\.)*"', '', bm.group(1))):
            pending.append((d[0], bm.group(1)))
    for _ in range(6):
        added = False
        for name, body in pending:
            if name in string_macros:
                continue
            idents = re.findall(r'[A-Z_][A-Z0-9_]*', re.sub(r'"(?:[^"\\]|\\.)*"', '', body))
            if all(i in string_macros for i in idents):
                has, value, macros = format_text(body, string_macros)
                string_macros[name] = value
                added = True
        if not added:
            break
    member_arrays = {}
    array_aliases = {}
    for alias, target, path, ln, text, flag, chain in aliases:
        if POINTER_ARRAY_TYPE_RE.search(target):
            array_aliases.setdefault(alias, target)
    for m in members:
        owner, nm, typ, path, ln, scope, tags, text, stem, mflag = m
        if scope != 'class' or not owner:
            continue
        elem = pointer_array_elem(typ, array_aliases)
        if elem:
            member_arrays.setdefault(('class', last_component(owner), nm), elem)
            member_arrays.setdefault(('stem', stem, nm), elem)
    return {
        'lock_kinds': kinds,
        'lock_wrappers': wrappers,
        'lock_alias_entries': alias_entries,
        'lock_class_stems': class_stems,
        'lock_uses': {k: tuple(v) for k, v in lock_uses.items()},
        'lock_use_owners': lock_use_owners,
        'printf_funcs': printf_funcs,
        'printf_macros': printf_macros,
        'fast_producers': frozenset(producers),
        'sql_types': frozenset(sql_types - other_types),
        'wb_wrappers': frozenset(w[0] for w in wb_wrappers),
        'tp_numbers': tp_numbers,
        'tp_names': tp_names,
        'tp_init': tp_init,
        'ir_array_getters': frozenset(n for n, c in array_getters.items() if c >= 0.8 * max(1, method_names.get(n, 0))),
        'factory_macros': frozenset(factory_macros),
        'member_arrays': member_arrays,
        'string_macros': string_macros,
        'printf_overloads': printf_overloads,
        'func_rets': {k: ' or '.join(sorted(v)) for k, v in rets.items()},
        'str_consts': {k: tuple(sorted(v)) for k, v in str_consts.items()},
        'sizeof_consts': {k: tuple(v) for k, v in sizeof_consts.items()},
        'member_types': dict(member_types),
        'global_types': {k: tuple(v) for k, v in global_types.items()},
        'type_aliases': type_aliases,
        'alias_owners': {k: tuple(sorted(v)) for k, v in alias_owners.items()},
        'comma_macros': comma_macros,
        'macro_calls': macro_calls,
        'wa_figures': frozenset(figure_names),
        'wa_wrappers': wa_wrapper_rows,
    }


def init_new_lists(ctx):
    words = sorted(set(ctx['lock_kinds']) | set(ctx['lock_alias_entries']) | set(LOCK_STD_KINDS),
                   key=lambda w: (-len(w), w))
    alt = '|'.join(re.escape(w) for w in words)
    ctx['lock_decl_re'] = re.compile(LOCK_DECL_TEMPLATE % alt)
    ctx['lock_container_re'] = re.compile(LOCK_CONTAINER_TEMPLATE % alt)
    ctx['lock_hint_re'] = re.compile(r'\b(?:%s)\b' % alt)
    ctx['printf_names'] = frozenset(ctx['printf_funcs']) | frozenset(ctx['printf_macros'])
    wrappers = sorted(ctx['wb_wrappers'])
    ctx['wb_wrapper_re'] = re.compile(r'(?<![\w])(%s)\s*\(' % '|'.join(re.escape(n) for n in wrappers)) if wrappers else None
    macros = sorted(ctx['factory_macros'])
    ctx['factory_macro_re'] = re.compile(r'(?<![\w])(%s)\b' % '|'.join(re.escape(n) for n in macros)) if macros else None
    ctx['rule46_names'] = frozenset(ctx['tp_names'][n] for n in RULE_46_TRACEPOINTS if n in ctx['tp_names'])
    wa = sorted(ctx.get('wa_wrappers', {}))
    ctx['wa_figure_re'] = re.compile(r'(?<![\w])(%s)\s*\(\s*\)' % '|'.join(re.escape(n) for n in wa)) if wa else None
    names = sorted(ctx.get('sizeof_consts', {}))
    ctx['sizeof_const_re'] = re.compile(r'(?<![\w.>:])(%s)\b' % '|'.join(re.escape(n) for n in names)) if names else None


def lock_alias_target(word, ctx, owner, path, stem):
    best = None
    lockish = ctx['lock_kinds'].keys() | ctx['lock_alias_entries'].keys() | LOCK_STD_KINDS.keys()
    for chain, apath, astem, target in ctx['lock_alias_entries'].get(word, ()):
        if target == word:
            continue
        same_file = apath == path or astem == stem
        owner_match = bool(chain) and bool(owner) and (last_component(chain) == last_component(owner)
                                                       or ('::' + last_component(chain) + '::') in ('::' + owner + '::'))
        if same_file and (not chain or owner_match):
            rank = 0
        elif owner_match:
            rank = 1
        elif same_file:
            rank = 2
        elif not chain and distinctive_alias(word):
            rank = 3
        else:
            continue
        key = (rank, target not in lockish)
        if best is None or key < best[0]:
            best = (key, target)
    return None if best is None else best[1]


def lock_word_kind(word, ctx, owner, path, stem, depth=0):
    in_kinds = word in ctx['lock_kinds']
    target = lock_alias_target(word, ctx, owner, path, stem) if word in ctx['lock_alias_entries'] else None
    if in_kinds and (target is None or stem in ctx['lock_class_stems'].get(word, ()) or word in LOCK_PLATFORM_ALIASES):
        kind = ctx['lock_kinds'][word]
        if kind == 'lock class':
            kind = 'lock class (%s)' % ctx['lock_wrappers'][word]
        return kind, word
    if target is not None and depth <= 6:
        inner = lock_word_kind(target, ctx, owner, path, stem, depth + 1)
        if inner is not None:
            return inner
    return None


def lock_kind_of(word, ns, ctx, owner, path, stem):
    if word in LOCK_STD_KINDS and word not in ctx['lock_kinds'] and word not in ctx['lock_alias_entries']:
        return (LOCK_STD_KINDS[word], word) if re.sub(r'\s+', '', ns).endswith('std::') else None
    qual = re.sub(r'\s+', '', ns).rstrip(':')
    k = lock_word_kind(word, ctx, qual.rsplit('::', 1)[-1] if qual and qual not in ('common', 'lib', 'oceanbase', 'share',
                                                                                      'obsys', 'obutil', 'sql', 'storage')
                       else owner, path, stem)
    if k is None:
        return None
    kind, target = k
    if target == word and word in LOCK_PLATFORM_ALIASES:
        return kind, '%s (%s)' % (word, LOCK_PLATFORM_ALIASES[word])
    return kind, word if target == word else '%s (alias of %s)' % (word, target)


def lock_scope(src, ln, off):
    if src.dkind.get(ln) in ('define', 'cont'):
        return 'macro', src.dname.get(ln, ''), None
    f = innermost_function(src, ln, off)
    c = innermost_class(src, ln)
    if f is not None and (c is None or f[1] >= c[1]):
        return 'function', f[0], f
    if c is not None:
        return 'class', c[0], c
    owner = anonymous_owner(src, off)
    if owner:
        return 'class', owner, None
    return 'global', '', None


def anonymous_owner(src, off):
    code = src.code
    opener = block_opener(code, off)
    if opener < 0:
        return None
    hs, header = header_before(code, opener)
    hm = re.search(r'(?:^|[;{}\s])(?:typedef\s+)?(struct|union|class)\s*([A-Za-z_]\w*)?\s*$', header)
    if not hm:
        return None
    if hm.group(2):
        return hm.group(2)
    close = matching_close(code, opener)
    if close < 0:
        return None
    tm = re.match(r'\s*([A-Za-z_]\w*)', code[close + 1:close + 200])
    return tm.group(1) if tm else '(anonymous %s)' % hm.group(1)


def lock_uses_note(ctx, stem, name, scope='stem', owner=''):
    uses = ctx['lock_uses']
    base = posixpath.basename(stem)
    total = [0, 0, 0, 0]
    if scope == 'class':
        cls = last_component(owner)
        bases = ctx.get('class_bases', {})
        for kind, who in ctx['lock_use_owners'].get((stem, name), ()):
            if kind == 'class' and (who == cls or cls in ancestors(bases, who)):
                u = uses[(stem, kind, who, name)]
                for k in range(4):
                    total[k] += u[k]
        where = 'methods of %s (%s.*)' % (cls, base)
    elif scope == 'function':
        u = uses.get((stem, 'func', owner, name))
        if u:
            total = list(u[:4])
        where = owner
    else:
        u = uses.get((stem, 'stem', '', name))
        if u:
            total = list(u[:4])
        where = '%s.*' % base
    if not (total[0] or total[1]):
        note = 'no guard or lock call names it in %s' % where
    else:
        parts = ['%d guards' % total[0], '%d lock calls' % total[1]]
        if total[2]:
            parts.append('%d timed' % total[2])
        if total[3]:
            parts.append('%d try' % total[3])
        note = 'uses in %s: %s' % (where, ', '.join(parts))
    if scope != 'function':
        o = uses.get((stem, 'other', '', name))
        if o and (o[0] or o[1]):
            note += '; %d more through another object with a member of this name in %s.* (not attributed)' % (
                o[0] + o[1], base)
        mc = uses.get((stem, 'macro', '', name))
        if mc and (mc[0] or mc[1]) and scope != 'macro':
            note += '; %d more inside #define bodies in %s.* (not attributed)' % (mc[0] + mc[1], base)
    return note


def spin_acquires(ctx, stem, name, scope, owner):
    uses = ctx['lock_uses']
    n = 0
    if scope == 'class':
        cls = last_component(owner)
        for kind, who in ctx['lock_use_owners'].get((stem, name), ()):
            if kind == 'class' and who == cls:
                n += uses[(stem, kind, who, name)][4] + uses[(stem, kind, who, name)][0]
    else:
        u = uses.get((stem, 'stem', '', name))
        if u:
            n += u[4] + u[0]
    return n


def lock_guard_rows(src, ctx, stats=None):
    out = []
    path = src.path
    if path.startswith(LOCK_PRIMITIVE_DIR):
        return out
    if stats is None:
        stats = collections.Counter()
    code = src.code
    if not ctx['lock_hint_re'].search(code):
        return template_lock_rows(src, ctx) + spin_lock_rows(src, ctx)
    stem = src.stem()
    seen = set()
    for m in ctx['lock_decl_re'].finditer(code):
        off = m.start('word')
        ln = src.line_of(off)
        a, b = statement_span(code, off)
        head = code[a:m.start()]
        if paren_depth_between(code, a, m.start()) > 0 or re.search(r'\b(?:typedef|using|friend|return|new|delete|case)\b', head):
            continue
        if re.search(r'[=?:]\s*$', head) and not re.search(r'(?:public|private|protected)\s*:\s*$', head):
            continue
        kind_scope, owner_name, span = lock_scope(src, ln, off)
        owner = owner_name if kind_scope == 'class' else ''
        k = lock_kind_of(m.group('word'), m.group('ns'), ctx, owner, path, stem)
        if k is None:
            continue
        kind, typ = k
        name = m.group('name')
        if name in NOT_FUNCTION_NAMES or name in ('operator', 'override', 'final'):
            continue
        ptr = m.group('ptr').strip()
        end = m.group('end')
        if end == '(':
            if kind_scope == 'class' or ptr:
                continue
            args = split_call_args(code, m.end('end') - 1)
            if not args or any(re.fullmatch(r'(?:const\s+)?[\w:<>]+(?:\s*[*&]+\s*|\s+)[A-Za-z_]\w*(?:\s*=.*)?', x) for x in args):
                continue
        if ptr and kind_scope in ('function',):
            continue
        names = [name]
        if end == ',':
            rest = code[m.end('end'):b]
            for piece in split_top(rest):
                pm = re.match(r'\s*([*&\s]*)([A-Za-z_]\w*)', piece)
                if pm:
                    names.append(pm.group(2))
        for nm in names:
            key = (ln, nm)
            if key in seen:
                continue
            seen.add(key)
            quals = m.group('quals')
            parts = ['%s %s' % (kind, re.sub(r'\s+', '', m.group('ns')) + typ + re.sub(r'\s+', '', m.group('targs') or ''))]
            if kind_scope == 'macro':
                parts.append('declared inside #define %s (its uses are not rows)' % owner_name)
                symbol = src.symbol(ln)
            elif kind_scope == 'function':
                parts.append(('function static in %s' if 'static' in quals else 'local in %s') % owner_name)
                symbol = owner_name + '::' + nm
            elif kind_scope == 'class':
                parts.append(('static member of %s' if 'static' in quals else 'member of %s') % owner_name)
                symbol = owner_name + '::' + nm
            else:
                if 'extern' in quals:
                    continue
                parts.append('global' + (' static' if 'static' in quals else ''))
                symbol = nm
            if m.group('arr').strip():
                parts.append('array ' + re.sub(r'\s+', '', m.group('arr')))
            if ptr:
                parts.append(('pointer' if '*' in ptr else 'reference')
                             + ' to a lock declared elsewhere or allocated at run time')
            parts.append(lock_uses_note(ctx, stem, nm, kind_scope, owner_name))
            out.append((ln, '; '.join(parts), symbol))
    for m in ctx['lock_container_re'].finditer(code):
        outer = re.sub(r'\s+', '', m.group('outer'))
        if re.search(r'(?i)guard|_lock$|lock_|cast$|^std::(?:unique|shared|scoped)_lock', last_component(outer)) \
                or last_component(outer) in ('static_cast', 'reinterpret_cast', 'const_cast', 'dynamic_cast', 'sizeof'):
            continue
        policy = not LOCK_ELEMENT_CONTAINER_RE.search(last_component(outer))
        off = m.start('outer')
        ln = src.line_of(off)
        a, b = statement_span(code, off)
        head = code[a:off]
        if paren_depth_between(code, a, m.start()) > 0 or re.search(r'\b(?:typedef|using|friend|return|new)\b', head):
            continue
        kind_scope, owner_name, span = lock_scope(src, ln, off)
        owner = owner_name if kind_scope == 'class' else ''
        inner_kinds = []
        for w in re.findall(r'[A-Za-z_]\w*', m.group('inner')):
            k = lock_kind_of(w, '', ctx, owner, path, stem)
            if k is not None and k[0] not in inner_kinds:
                inner_kinds.append(k[0])
        if not inner_kinds:
            continue
        nm = m.group('name')
        if (ln, nm) in seen or m.group('end') == '(' and kind_scope == 'class':
            continue
        if policy:
            stats['lock-guards policy-parameter instances'] += 1
            continue
        seen.add((ln, nm))
        where = {'macro': 'declared inside #define %s' % owner_name, 'function': 'local in %s' % owner_name,
                 'class': 'member of %s' % owner_name}.get(kind_scope, 'global')
        symbol = src.symbol(ln) if kind_scope in ('macro', 'global') else owner_name + '::' + nm
        if kind_scope == 'global':
            symbol = nm
        out.append((ln, 'container of locks (%s) %s<%s>; %s; %s' % (
            ', '.join(inner_kinds), outer, collapse(m.group('inner'), 80), where,
            lock_uses_note(ctx, stem, nm, kind_scope, owner_name)), symbol))
    out += template_lock_rows(src, ctx)
    out += spin_lock_rows(src, ctx, set(r[0] for r in out))
    return out


def name_tokens(name):
    out = []
    for part in name.split('_'):
        out += re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', part)
    return [t.lower() for t in out]


def template_lock_rows(src, ctx):
    out = []
    src.scan()
    if not src.tparams:
        return out
    stem = src.stem()
    for name, hl, ol, end, extra in src.classes:
        tps = src.tparams.get((name, hl))
        if not tps:
            continue
        funcs = [(f[1], f[3]) for f in src.function_spans() if hl <= f[1] <= end]
        for ln in range(ol + 1, end + 1):
            if any(fh <= ln <= fe for fh, fe in funcs):
                continue
            inner = innermost_class(src, ln)
            owner = inner[0] if inner is not None else name
            mm = re.match(r'\s*(?:(?:public|private|protected)\s*:\s*)?(?:mutable\s+)?(?:const\s+)?([A-Za-z_]\w*)'
                          r'(\s*[*&]+\s*|\s+)(?:const\s+)?([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*);', src.lines[ln])
            if not mm or mm.group(1) not in tps:
                continue
            if not LOCK_NAME_TOKENS & set(name_tokens(mm.group(1)) + name_tokens(mm.group(3))):
                continue
            ptr = mm.group(2).strip()
            if any(r[0] == ln for r in out):
                continue
            out.append((ln, 'lock whose type is the template parameter %s; member of %s%s%s; %s' % (
                mm.group(1), owner, '; array ' + re.sub(r'\s+', '', mm.group(4)) if mm.group(4).strip() else '',
                ('; %s to a lock declared elsewhere' % ('pointer' if '*' in ptr else 'reference')) if ptr else '',
                lock_uses_note(ctx, stem, mm.group(3), 'class', owner)), owner + '::' + mm.group(3)))
    return out


def spin_lock_rows(src, ctx, taken=frozenset()):
    out = []
    code = src.code
    if not re.search(r'(?i)lock|latch|mutex|spin', code):
        return out
    stem = src.stem()
    seen = set(taken)
    for m in SPIN_LOCK_DECL_RE.finditer(code):
        name = m.group('name')
        if not SPIN_LOCK_WORDS & set(name_tokens(name)):
            continue
        if m.group('end') == '(' or m.group('ptr').strip():
            continue
        off = m.start('name')
        ln = src.line_of(off)
        if ln in seen:
            continue
        a, b = statement_span(code, off)
        head = code[a:m.start()]
        if paren_depth_between(code, a, m.start()) > 0 or re.search(r'\b(?:typedef|using|friend|return|new|delete|case)\b', head):
            continue
        if re.search(r'[=?:]\s*$', head) and not re.search(r'(?:public|private|protected)\s*:\s*$', head):
            continue
        kind_scope, owner_name, span = lock_scope(src, ln, off)
        quals = m.group('quals')
        if kind_scope == 'function' and 'static' not in quals or kind_scope == 'macro':
            continue
        if kind_scope == 'global' and 'extern' in quals:
            continue
        if kind_scope == 'class' and (last_component(owner_name) in ctx['lock_kinds'] or 'Guard' in last_component(owner_name)):
            continue
        scope = 'class' if kind_scope == 'class' else 'stem'
        n = spin_acquires(ctx, stem, name, scope, owner_name)
        if not n:
            continue
        seen.add(ln)
        where = {'class': ('static member of %s' if 'static' in quals else 'field of %s') % owner_name,
                 'function': 'function static in %s' % owner_name}.get(kind_scope, 'global' + (' static' if 'static' in quals else ''))
        typ = re.sub(r'\s+', ' ', m.group('type')).strip()
        arr = re.sub(r'\s+', '', m.group('arr'))
        text = ('hand-made spin lock: %s %s; %s%s; acquired at %d %s in %s by a test-and-set or compare-and-swap, a guard '
                'object or an acquire call that takes its address; its atomic accesses are rows of atomic-fields.tsv') % (
            typ, name, where, '; array ' + arr if arr else '', n, 'place' if n == 1 else 'places',
            ('methods of %s (%s.*)' % (last_component(owner_name), posixpath.basename(stem))) if scope == 'class'
            else posixpath.basename(stem) + '.*')
        symbol = owner_name + '::' + name if kind_scope in ('class', 'function') else name
        out.append((ln, text, symbol))
    return out


def pri_value(name):
    m = PRI_MACRO_RE.match(name)
    if not m:
        return None
    size = m.group(2)
    for k in ('LEAST', 'FAST'):
        if size.startswith(k):
            size = size[len(k):]
    return PRI_LENGTH.get(size, '') + m.group(1)


def format_text(raw, string_macros=None):
    pieces = []
    macros = []
    has_literal = False
    raw = re.sub(r'\\\r?\n', '', raw)
    for m in STRING_PIECE_RE.finditer(raw):
        if m.group('raw') is not None:
            pieces.append(m.group('raw'))
            has_literal = True
        elif m.group('s') is not None:
            pieces.append(m.group('s'))
            has_literal = True
        elif m.group('id'):
            ident = m.group('id')
            v = pri_value(ident)
            if v is not None:
                pieces.append(v)
            elif string_macros is not None and ident in string_macros:
                pieces.append(string_macros[ident])
                has_literal = True
                macros.append(ident)
            else:
                macros.append(ident)
    return has_literal, ''.join(pieces), macros


ARG_INT_LITERAL_RE = re.compile(r'(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)([uUlL]*)')
ARG_FLOAT_LITERAL_RE = re.compile(r'(?:\d+\.\d*|\.\d+|\d+(?=[eE]))(?:[eE][-+]?\d+)?([fFlL]?)')
CAST_START_RE = re.compile(r'(static_cast|reinterpret_cast|const_cast|dynamic_cast)\s*<')
TYPE_WORD_RE = re.compile(
    r'(?:const\s+|volatile\s+)*(?:(?:unsigned|signed)\s+)?(?:u?int(?:8|16|32|64)?_t|u?int(?:8|16|32|64)|int|char|short|'
    r'long(?:\s+long)?(?:\s+int)?|bool|float|(?:long\s+)?double|size_t|ssize_t|uintptr_t|intptr_t|ptrdiff_t|void|unsigned|'
    r'signed|pid_t|off_t|time_t)(?:\s+const)?(?:\s*\*+(?:\s*const)?)?')
INTEGER_RANKS = {
    'bool': 0, 'char': 1, 'signed char': 1, 'unsigned char': 1, 'int8_t': 1, 'uint8_t': 1, 'int8': 1, 'uint8': 1,
    'short': 2, 'unsigned short': 2, 'int16_t': 2, 'uint16_t': 2, 'int16': 2, 'uint16': 2,
    'int': 3, 'unsigned': 3, 'unsigned int': 3, 'signed': 3, 'int32_t': 3, 'uint32_t': 3, 'int32': 3, 'uint32': 3,
    'pid_t': 3,
    'long': 4, 'unsigned long': 4, 'long int': 4, 'size_t': 4, 'ssize_t': 4, 'intptr_t': 4, 'uintptr_t': 4, 'ptrdiff_t': 4,
    'off_t': 4, 'time_t': 4,
    'long long': 5, 'unsigned long long': 5, 'long long int': 5, 'int64_t': 5, 'uint64_t': 5, 'int64': 5, 'uint64': 5,
}
FLOAT_TYPES = ('float', 'double', 'long double')
LIBC_RETURN_TYPES = {
    'abs': 'int', 'labs': 'long', 'llabs': 'long long', 'strlen': 'size_t', 'strnlen': 'size_t', 'fabs': 'double',
    'floor': 'double', 'ceil': 'double', 'sqrt': 'double', 'pow': 'double', 'log': 'double', 'log10': 'double',
    'exp': 'double', 'strerror': 'char *', 'getpid': 'pid_t', 'time': 'time_t', 'strchr': 'char *', 'strstr': 'char *',
    'strdup': 'char *', 'atoi': 'int', 'atol': 'long', 'atoll': 'long long', 'strtol': 'long', 'strtoll': 'long long',
    'strtoul': 'unsigned long', 'strtoull': 'unsigned long long', 'strtod': 'double', 'toupper': 'int', 'tolower': 'int',
    'getenv': 'char *', 'pthread_self': 'pthread_t', 'gettid': 'pid_t', 'syscall': 'long',
}
ELEMENT_CONTAINER_RE = re.compile(
    r'^(?:ObIArray|ObSEArray|ObArray|ObFixedArray|ObSqlArray|ObArrayWrap|ObList|Ob2DArray|ObVector|ObArrayImpl|'
    r'ObSArray|vector|array|deque)$')


def bare_type(t):
    t = re.sub(r'\b(?:const|volatile)\b', ' ', t or '')
    t = t.replace('&', ' ').replace('*', ' ')
    return re.sub(r'\s+', ' ', t).strip()


def class_of(t):
    if not t:
        return ''
    b = bare_type(strip_template_args(t) if '<' in t else t)
    return last_component(b) if b else ''


def is_pointer(t):
    return bool(t) and bare_type(t) != '' and '*' in drop_template_args(t)


def char_pointer_kind(t):
    if not t:
        return None
    s = re.sub(r'\s+', ' ', drop_template_args(t)).strip()
    if '(string literal)' in t or re.fullmatch(r'(?:const char|char const) ?\[.*\]', s):
        return 'const'
    if re.fullmatch(r'(?:unsigned |signed )?char ?\[.*\]', s):
        return 'mutable'
    m = re.fullmatch(r'(const )?(?:unsigned |signed )?char( const)? \*( const)?', s)
    if not m:
        return None
    return 'const' if m.group(1) or m.group(2) else 'mutable'


def integer_rank(t):
    b = bare_type(t)
    b = re.sub(r'^(?:std::|common::)', '', b)
    return INTEGER_RANKS.get(b)


def expand_alias(t, ctx, *scopes):
    seen = 0
    aliases = ctx['type_aliases']
    while t and seen < 4 and ' or ' not in t:
        b = bare_type(t)
        if not b or '<' in b:
            break
        target = None
        if '::' in b:
            q, last = b.rsplit('::', 1)
            target = aliases.get((q.rsplit('::', 1)[-1], last))
        else:
            for sc in scopes:
                if sc and (sc, b) in aliases:
                    target = aliases[(sc, b)]
                    break
            if target is None:
                target = aliases.get(('', b))
            if target is None:
                owners = ctx['alias_owners'].get(b, ())
                if len(owners) == 1:
                    target = aliases.get((owners[0], b))
        if not target or target == b or integer_rank(b) is not None:
            break
        t = re.sub(r'(?<![\w:])' + re.escape(b) + r'(?![\w:])', target, t, count=1) if re.search(
            r'(?<![\w:])' + re.escape(b) + r'(?![\w:])', t) else target
        seen += 1
    return t


def arith_result(lt, rt):
    if lt is None or rt is None:
        return None
    for ft in ('long double', 'double', 'float'):
        if bare_type(lt) == ft or bare_type(rt) == ft:
            return 'double' if ft == 'float' else ft
    lr, rr = integer_rank(lt), integer_rank(rt)
    if lr is None or rr is None:
        return None
    lb, rb = bare_type(lt), bare_type(rt)
    if lr < 3:
        lb, lr = 'int', 3
    if rr < 3:
        rb, rr = 'int', 3
    if lr != rr:
        return lb if lr > rr else rb
    lu = lb.startswith('u') or lb.startswith('unsigned') or lb == 'size_t'
    return lb if lu or not (rb.startswith('u') or rb.startswith('unsigned') or rb == 'size_t') else rb


def split_top_operator(e, ops):
    depth = 0
    best = None
    i = 0
    n = len(e)
    while i < n:
        c = e[i]
        if c in '"\'':
            j = i + 1
            while j < n and e[j] != c:
                j += 2 if e[j] == '\\' else 1
            i = j + 1
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif depth == 0:
            for op in ops:
                if e.startswith(op, i):
                    prev = e[:i].rstrip()
                    nxt = e[i + len(op):i + len(op) + 1]
                    if op in ('<', '>') and (e.startswith(op * 2, i) or (i > 0 and e[i - 1] in '<>-') or nxt in '<>='):
                        break
                    if op in ('<', '>') and not (e[i - 1:i] == ' ' and nxt == ' '):
                        break
                    if op in ('&', '|') and (nxt == op or e[i - 1:i] == op):
                        break
                    if op in ('-', '+') and (nxt == op or e[i - 1:i] == op or nxt == '>' and op == '-'):
                        break
                    if op in ('*', '&', '-', '+') and (not prev or prev[-1] in '(,=!<>&|^~?:+-*/%[' or
                                                       re.search(r'\b(?:return|case)$', prev)):
                        break
                    if op == '=' or op in ('<', '>') and nxt == '=' and op * 2 not in ops:
                        break
                    best = (i, op)
                    i += len(op) - 1
                    break
        i += 1
    return best


def arg_expr_type(src, f, cls, e, ctx, off, depth=0):
    if depth > 8 or e is None:
        return None
    e = e.strip()
    while e.startswith('(') and matching_close(e, 0) == len(e) - 1:
        e = e[1:-1].strip()
    if not e:
        return None
    if re.match(r'(?:u8|u|U|L)?"', e) or (e.split()[0] in ctx['string_macros'] and format_text(e, ctx['string_macros'])[0]):
        has, value, macros = format_text(e, ctx['string_macros'])
        if has and not re.sub(r'(?:(?:u8|u|U|L)?"(?:[^"\\]|\\.)*"|[A-Za-z_]\w*|\s)', '', e.replace('\\\n', '')):
            return 'const char * (string literal)'
    if e[0] == "'":
        return 'char'
    if e in ('true', 'false'):
        return 'bool'
    if e in ('NULL', 'nullptr'):
        return 'null pointer'
    m = ARG_FLOAT_LITERAL_RE.fullmatch(e)
    if m:
        return 'float' if m.group(1) in ('f', 'F') else 'long double' if m.group(1) in ('l', 'L') else 'double'
    m = ARG_INT_LITERAL_RE.fullmatch(e)
    if m:
        suf = m.group(1).lower()
        return {'': 'int', 'l': 'long', 'll': 'long long', 'u': 'unsigned int', 'ul': 'unsigned long', 'lu': 'unsigned long',
                'ull': 'unsigned long long', 'llu': 'unsigned long long'}.get(suf, 'int')
    q = split_top_operator(e, ('?',))
    if q is not None:
        colon = split_top_operator(e[q[0] + 1:], (':',))
        if colon is not None:
            a = arg_expr_type(src, f, cls, e[q[0] + 1:q[0] + 1 + colon[0]], ctx, off, depth + 1)
            b = arg_expr_type(src, f, cls, e[q[0] + 2 + colon[0]:], ctx, off, depth + 1)
            if a and b and char_pointer_kind(a) and char_pointer_kind(b):
                return 'const char *' if 'const' in (char_pointer_kind(a), char_pointer_kind(b)) else 'char *'
            return a if a == b or b is None or b == 'null pointer' else (b if a is None or a == 'null pointer' else
                                                                         arith_result(a, b) or a)
    for ops, kind in ((('||',), 'bool'), (('&&',), 'bool'), (('|',), 'arith'), (('^',), 'arith'), (('&',), 'arith'),
                      (('==', '!='), 'bool'), (('<=', '>=', '<', '>'), 'bool'), (('<<', '>>'), 'shift'),
                      (('+', '-'), 'arith'), (('*', '/', '%'), 'arith')):
        hit = split_top_operator(e, ops)
        if hit is None:
            continue
        if kind == 'bool':
            return 'bool'
        lt = arg_expr_type(src, f, cls, e[:hit[0]], ctx, off, depth + 1)
        if kind == 'shift':
            return arith_result(lt, 'int') if lt else None
        rt = arg_expr_type(src, f, cls, e[hit[0] + len(hit[1]):], ctx, off, depth + 1)
        if hit[1] in ('+', '-') and lt and is_pointer(lt):
            return 'ptrdiff_t' if hit[1] == '-' and rt and is_pointer(rt) else lt
        if hit[1] == '+' and rt and is_pointer(rt):
            return rt
        return arith_result(lt, rt)
    if e.startswith('!'):
        return 'bool'
    if e[0] in '-+~' and not e.startswith(('--', '++', '->')):
        t = arg_expr_type(src, f, cls, e[1:], ctx, off, depth + 1)
        return arith_result(t, 'int') if t and integer_rank(t) is not None else t
    if e.startswith(('++', '--')):
        return arg_expr_type(src, f, cls, e[2:], ctx, off, depth + 1)
    if e[0] == '*':
        t = arg_expr_type(src, f, cls, e[1:], ctx, off, depth + 1)
        if t and is_pointer(t):
            return re.sub(r'\s*\*(?:\s*const)?\s*$', '', drop_template_args(t) if '<' not in t else t).strip() or None
        return None
    if e[0] == '&':
        t = arg_expr_type(src, f, cls, e[1:], ctx, off, depth + 1)
        return (t.replace('&', '').strip() + ' *') if t else None
    if e[0] == '(':
        close = matching_close(e, 0)
        if close > 0:
            inner = e[1:close].strip()
            rest = e[close + 1:].strip()
            if rest and (TYPE_WORD_RE.fullmatch(inner) or re.fullmatch(r'(?:const\s+)?[A-Za-z_][\w:]*(?:<[^()]*>)?\s*\*+', inner)
                         or re.fullmatch(r'(?:const\s+)?(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*_t', inner)):
                return norm_type(inner)
    m = CAST_START_RE.match(e)
    if m:
        lt = m.end() - 1
        depth_a = 0
        j = lt
        while j < len(e):
            if e[j] == '<':
                depth_a += 1
            elif e[j] == '>':
                depth_a -= 1
                if depth_a == 0:
                    break
            j += 1
        target = e[lt + 1:j]
        k = j + 1
        while k < len(e) and e[k].isspace():
            k += 1
        if k < len(e) and e[k] == '(':
            close = matching_close(e, k)
            if close > 0:
                return chain_type(src, f, cls, norm_type(target), e[close + 1:], ctx, off, depth)
        return norm_type(target)
    if re.match(r'(?:sizeof|alignof|offsetof)\s*\(', e) and matching_close(e, e.index('(')) == len(e) - 1:
        return 'size_t'
    return postfix_type(src, f, cls, e, ctx, off, depth)


def split_postfix(e):
    m = re.match(r'\s*((?:::\s*)?(?:[A-Za-z_]\w*\s*(?:<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>)?\s*::\s*)*~?[A-Za-z_]\w*)', e)
    if not m:
        return None
    root = re.sub(r'\s+', '', m.group(1))
    return root, e[m.end():]


def parse_steps(rest):
    steps = []
    i = 0
    n = len(rest)
    while i < n:
        c = rest[i]
        if c.isspace():
            i += 1
            continue
        if rest.startswith('->', i) or c == '.':
            j = i + (2 if c == '-' else 1)
            mm = re.match(r'\s*(?:template\s+)?(~?[A-Za-z_]\w*)', rest[j:])
            if not mm:
                return None
            steps.append(('.', mm.group(1)))
            i = j + mm.end()
            continue
        if c == '(':
            close = matching_close(rest, i)
            if close < 0:
                return None
            steps.append(('()', rest[i + 1:close]))
            i = close + 1
            continue
        if c == '[':
            close = matching_close(rest, i)
            if close < 0:
                return None
            steps.append(('[]', rest[i + 1:close]))
            i = close + 1
            continue
        if c == '<':
            depth = 0
            j = i
            while j < n:
                if rest[j] == '<':
                    depth += 1
                elif rest[j] == '>':
                    depth -= 1
                    if depth == 0:
                        break
                elif rest[j] in ';{}':
                    return None
                j += 1
            if j >= n:
                return None
            i = j + 1
            continue
        if rest.startswith(('++', '--'), i):
            i += 2
            continue
        return None
    return steps


def member_type(ctx, c, name):
    if not c:
        return None
    bases = ctx.get('class_bases', {})
    for k in ancestors(bases, c):
        t = ctx['member_types'].get(k, {}).get(name)
        if t:
            return t
    return None


def method_type(ctx, c, name):
    bases = ctx.get('class_bases', {})
    for k in (ancestors(bases, c) if c else ['']):
        t = ctx['func_rets'].get((k, name))
        if t:
            return t
    return None


def global_type(ctx, name, stem):
    cands = ctx['global_types'].get(name)
    if not cands:
        return None
    same = [t for s, t in cands if s == stem]
    types = sorted(set(same or [t for s, t in cands]))
    return types[0] if len(types) == 1 else None


def root_type(src, f, cls, root, ctx, off, is_call):
    name = root.split('::')[-1]
    if '::' in root and not root.startswith('::'):
        q = last_component(root.rsplit('::', 1)[0])
        if is_call:
            return method_type(ctx, q, name) or ctx['func_rets'].get(('', name))
        return member_type(ctx, q, name) or global_type(ctx, name, src.stem())
    if is_call:
        if name in ctx['macro_calls']:
            kind, val = ctx['macro_calls'][name]
            if kind == 'cast':
                return val
            return method_type(ctx, cls, val) if cls and method_type(ctx, cls, val) else ctx['func_rets'].get(('', val))
        if TYPE_WORD_RE.fullmatch(name):
            return name
        if cls:
            t = method_type(ctx, cls, name)
            if t:
                return t
        t = ctx['func_rets'].get(('', name))
        if t:
            return t
        if name in LIBC_RETURN_TYPES:
            return LIBC_RETURN_TYPES[name]
        if name in ctx['member_types'] or name in ctx['class_bases']:
            return name
        return None
    if name == 'this':
        return cls + ' *' if cls else None
    if f is not None:
        typ, info = local_type(src, f, name, off)
        if info is not None:
            return typ
    if cls:
        t = member_type(ctx, cls, name)
        if t:
            return t
    if name in ctx['str_consts']:
        return 'const char *'
    return global_type(ctx, name, src.stem())


def element_type(t):
    s = t.strip()
    am = re.search(r'\s*\[[^\]]*\]\s*$', s)
    if am:
        return s[:am.start()].strip() or None
    m = re.match(r'(?:const\s+)?((?:[A-Za-z_]\w*::)*([A-Za-z_]\w*))\s*<', s)
    if m and ELEMENT_CONTAINER_RE.match(m.group(2)):
        lt = s.index('<', m.start(1))
        depth = 0
        for j in range(lt, len(s)):
            if s[j] == '<':
                depth += 1
            elif s[j] == '>':
                depth -= 1
                if depth == 0:
                    break
        args = split_top(s[lt + 1:j])
        return norm_type(args[0]) if args else None
    if is_pointer(s):
        return re.sub(r'\s*\*(?:\s*const)?\s*$', '', s).strip()
    b = bare_type(s)
    if integer_rank(b) is not None or b in FLOAT_TYPES:
        return b
    if b and '<' not in s and not re.search(r'(?:Array|List|Vector|Map|Set|String|Str)$', class_of(s)):
        return b
    return None


def walk_steps(t, steps, ctx, cls):
    scope = cls
    k = 0
    while k < len(steps):
        if t is None:
            return None
        t = expand_alias(norm_type(t), ctx, scope, cls)
        kind, val = steps[k]
        if kind == '.':
            c = class_of(t)
            if k + 1 < len(steps) and steps[k + 1][0] == '()':
                t = method_type(ctx, c, val)
                k += 2
            else:
                t = member_type(ctx, c, val)
                k += 1
            scope = c
        elif kind == '[]':
            t = element_type(t)
            k += 1
        else:
            return None
    return expand_alias(norm_type(t), ctx, scope, cls) if t else None


def chain_type(src, f, cls, t, rest, ctx, off, depth):
    steps = parse_steps(rest)
    if steps is None:
        return None
    return walk_steps(t, steps, ctx, cls)


def postfix_type(src, f, cls, e, ctx, off, depth):
    sp = split_postfix(e)
    if sp is None:
        return None
    root, rest = sp
    steps = parse_steps(rest)
    if steps is None:
        return None
    is_call = bool(steps) and steps[0][0] == '()'
    t = root_type(src, f, cls, root, ctx, off, is_call)
    if t is None:
        return None
    return walk_steps(t, steps[1:] if is_call else steps, ctx, cls)


def expand_comma_macros(args, ctx):
    out = []
    for a in args:
        m = re.fullmatch(r'\s*([A-Za-z_]\w*)\s*\((.*)\)\s*', a, re.S)
        if m and m.group(1) in ctx['comma_macros']:
            params, pieces = ctx['comma_macros'][m.group(1)]
            actual = [x.strip() for x in split_top(m.group(2))]
            if len(actual) == len(params):
                for piece in pieces:
                    for pname, val in zip(params, actual):
                        piece = re.sub(r'(?<![\w#])' + re.escape(pname) + r'(?!\w)', val, piece)
                    out.append(piece)
                continue
        out.append(a)
    return out


def show_type(t):
    if t is None:
        return '?'
    t = t.replace('const char * (string literal)', 'string literal')
    return re.sub(r'\s+', ' ', t).strip()


def conversion_slots(convs):
    slots = []
    for c in convs:
        if c == '%%':
            continue
        for _ in range(c.count('*')):
            slots.append(c + ' width or precision')
        slots.append(c)
    return slots


def argument_type_note(src, f, cls, args, convs, ctx, off):
    types = [show_type(arg_expr_type(src, f, cls, a, ctx, off)) for a in args]
    note = 'argument types: ' + (' | '.join(collapse(t, 60) for t in types) if types else 'none')
    if convs is not None:
        slots = conversion_slots(convs)
        if len(slots) != len(args):
            note += '; the conversions take %d argument%s, the call passes %d' % (len(slots), '' if len(slots) == 1 else 's',
                                                                              len(args))
        else:
            pairs = []
            for slot, t in zip(slots, types):
                pairs.append('%s <- %s' % (slot, collapse(t, 50)))
            if pairs:
                note += '; conversion and argument pairs: ' + ', '.join(pairs)
    return note, types


def printf_overload_table(repo, printf_funcs, printf_defs, func_index):
    mixed = set(n for n, v in printf_funcs.items() if v[3])
    printf_at = {}
    for name, idx, kind, path, ln in printf_defs:
        if name in mixed:
            printf_at[(path, ln - 1)] = (idx, kind)
    table = collections.defaultdict(list)
    sources = {}
    for cls, simple, path, hl, end in func_index:
        if simple not in mixed:
            continue
        src = sources.get(path)
        if src is None:
            src = read_source(repo, path)
            sources[path] = src
        span = next((fs for fs in src.function_spans() if fs[1] == hl and fs[4][1] == simple), None)
        if span is None:
            continue
        params = [p.strip() for p in split_top(span[4][3]) if p.strip() and p.strip() != 'void']
        pack = lambda p: p == '...' or bool(re.search(r'\.\.\.\s*[A-Za-z_]?\w*$', p))
        variadic = bool(params) and pack(params[-1])
        fixed = [p for p in params if not pack(p)]
        minp = sum(1 for p in fixed if '=' not in p)
        maxp = 1 << 30 if variadic else len(fixed)
        pf = printf_at.get((path, hl))
        tps = src.tparams.get((span[0], span[1]), frozenset())
        table[simple].append((cls, minp, maxp, pf[0] if pf else None, tuple(fixed), frozenset(tps)))
    return {k: tuple(sorted(set(v), key=lambda o: (o[0], o[1], o[2], -1 if o[3] is None else o[3], o[4]))) for k, v in
            table.items()}


def template_param_at(overload, idx):
    fixed, tps = overload[4], overload[5]
    if idx >= len(fixed):
        return False
    words = set(re.findall(r'[A-Za-z_]\w*', fixed[idx].split('=', 1)[0]))
    return bool(words & set(tps))


def format_arg_binding(src, f, cls, arg, ctx, off, others, idx):
    if format_text(arg, ctx['string_macros'])[0]:
        return 'printf', 'a string literal'
    if re.fullmatch(r'\s*(?:[A-Za-z_]\w*\s+)+[A-Za-z_]\w*\s*', arg):
        return 'printf', 'a concatenation of string macros'
    name = re.fullmatch(r'\s*(?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)(?:\s*\[[^\[\]]*\])?\s*', arg)
    if name and name.group(1) in ctx['str_consts'] and not (f is not None and find_local(src, f, name.group(1), off)):
        return 'printf', 'the const char * constant %s' % name.group(1)
    t = arg_expr_type(src, f, cls, arg, ctx, off)
    kind = char_pointer_kind(t)
    if kind == 'const':
        return 'printf', 'a %s' % show_type(t)
    if kind == 'mutable':
        if any(template_param_at(o, idx) for o in others):
            return 'other', 'a char * binds the template overload'
        return 'printf', 'a %s' % show_type(t)
    if t is None:
        return 'unknown', 'the type of %s is not resolved' % collapse(arg, 60)
    if integer_rank(t) is not None or bare_type(t) in FLOAT_TYPES:
        return 'number', show_type(t)
    return 'other', show_type(t)


def resolve_mixed_call(src, ctx, name, prefix, raw_args, f, cls, off):
    overloads = list(ctx['printf_overloads'].get(name, ()))
    if not overloads:
        return None, 'other', 'no overload found'
    n = len(raw_args)
    rm = re.search(r'((?:[A-Za-z_]\w*\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*(?:\[[^\[\]]*\])?\s*(?:\.|->)\s*)+)$', prefix)
    classes = set(o[0] for o in overloads)
    if rm:
        recv = re.sub(r'(?:\.|->)\s*$', '', rm.group(1).strip())
        t = arg_expr_type(src, f, cls, recv, ctx, off)
        rc = class_of(t) if t else ''
        if rc:
            bases = ctx.get('class_bases', {})
            overloads = [o for o in overloads if o[0] in ancestors(bases, rc)]
        elif len(classes) > 1:
            return None, 'other', 'receiver not resolved'
    elif len(classes) > 1:
        bases = ctx.get('class_bases', {})
        mine = [o for o in overloads if o[0] == '' or cls and o[0] in ancestors(bases, cls)]
        overloads = mine
    fitting = [o for o in overloads if o[1] <= n <= o[2]]
    pf = sorted((o for o in fitting if o[3] is not None and o[3] < n), key=lambda o: o[3])
    others = [o for o in fitting if o[3] is None]
    if not pf:
        return None, 'other', 'another overload'
    verdicts = [(o,) + format_arg_binding(src, f, cls, raw_args[o[3]], ctx, off, others, o[3]) for o in pf]
    unknowns = sorted((x for x in verdicts if x[1] == 'unknown'), key=lambda x: -x[0][1])
    if not others:
        why = 'only a printf overload takes %d arguments' % n
        for o, v, w in verdicts:
            if v == 'printf':
                return o[3], 'printf', why
        if unknowns:
            return unknowns[0][0][3], 'printf', why
        rest = [x for x in verdicts if x[1] == 'other']
        return (rest[0][0][3] if rest else pf[-1][3]), 'printf', why
    for o, v, w in verdicts:
        if v == 'printf':
            return o[3], 'printf', 'the argument at the format position is %s' % w
    if unknowns:
        return unknowns[0][0][3], 'unknown', unknowns[0][2]
    return None, 'other', 'another overload'
    if not others:
        for o in pf:
            if format_arg_binding(src, f, cls, raw_args[o[3]], ctx, off, (), o[3])[0] == 'printf':
                return o[3], 'only a printf overload takes %d arguments' % n
        for o in pf:
            if not re.search(r'(?i)alloc', raw_args[o[3]]):
                return o[3], 'only a printf overload takes %d arguments' % n
        return pf[0][3], 'only a printf overload takes %d arguments' % n
    unknown = None
    for o in pf:
        verdict, why = format_arg_binding(src, f, cls, raw_args[o[3]], ctx, off, others, o[3])
        if verdict == 'printf':
            return o[3], 'the argument at the format position is %s' % why
        if verdict == 'unknown':
            unknown = why
    if unknown:
        return -1, unknown
    return None, 'another overload'


def printf_rows(src, ctx, stats):
    out = []
    code = src.code
    names = ctx['printf_names']
    funcs = ctx['printf_funcs']
    macros = ctx['printf_macros']
    if not any(m.group(1) in names for m in IDENT_PAREN_RE.finditer(code)):
        return out
    acode, alines, astarts, text = aligned_view(src)
    smacros = ctx['string_macros']
    for i, line in enumerate(src.lines):
        if '(' not in line:
            continue
        for k, m in enumerate(IDENT_PAREN_RE.finditer(line)):
            name = m.group(1)
            if name not in names:
                continue
            in_define = src.dkind.get(i) in ('define', 'cont')
            macro_name = src.dname.get(i, '') if in_define else ''
            mixed = False
            if name in macros:
                if in_define and macro_name == name:
                    continue
                fmt_indexes = (macros[name][0],)
                sinks = macros[name][1]
                kind = 'variadic'
                passes_args = macros[name][2]
            else:
                ck = body_call_kind(src, i, name, line[:m.start()], src.starts[i] + m.end() - 1)
                if ck != 'call':
                    continue
                idxs, kind, sink, mixed = funcs[name]
                fmt_indexes = tuple(sorted(idxs))
                sinks = (sink,) if sink else ()
                passes_args = True
            if in_define and macro_name in macros:
                stats['printf-calls inside forwarding macros'] += 1
                continue
            open_paren = aligned_offset(src, i, IDENT_PAREN_RE, k)
            spans, close = aligned_args(acode, open_paren) if open_paren is not None else ([], -1)
            raw_args = [text[s:e] for s, e in spans]
            if close > open_paren and re.search(r'^[ \t]*#[ \t]*(?:if|else|elif|endif)', text[open_paren + 1:close], re.M):
                raw_args = split_args_plain(resolve_directives(text[open_paren + 1:close]))
                stats['printf-calls with preprocessor branches in the arguments'] += 1
            raw_args = merge_template_splits(raw_args)
            if looks_like_params(raw_args):
                stats['printf-calls declarations'] += 1
                continue
            off = src.starts[i] + m.start()
            f = innermost_function(src, i, off)
            cls = enclosing_class(f[0]) if f else ''
            overload_note = ''
            if mixed:
                pick, status, why = resolve_mixed_call(src, ctx, name, line[:m.start()], raw_args, f, cls, off)
                if pick is None:
                    stats['printf-calls other overloads'] += 1
                    if why == 'receiver not resolved':
                        stats['printf-calls receiver not resolved'] += 1
                    continue
                fmt_indexes = (pick,)
                if status == 'unknown':
                    stats['printf-calls overload not resolved'] += 1
                    overload_note = 'overload not resolved (%s): a const char * there binds the printf overload, anything ' \
                                    'else another overload' % why
                elif not format_text(raw_args[pick], smacros)[0]:
                    overload_note = 'the printf overload, since %s' % why
            fmt_at = None
            for idx in fmt_indexes:
                if idx < len(raw_args) and format_text(raw_args[idx], smacros)[0]:
                    fmt_at = idx
                    break
            if fmt_at is None and name in macros:
                fmt_at = next((j for j, a in enumerate(raw_args) if format_text(a, smacros)[0]), None)
            if fmt_at is None and fmt_indexes and fmt_indexes[0] < len(raw_args) and \
                    re.fullmatch(r'\s*(?:[A-Za-z_]\w*\s+)+[A-Za-z_]\w*\s*', raw_args[fmt_indexes[0]]):
                fmt_at = fmt_indexes[0]
            parts = ['%s call' % name]
            if overload_note:
                parts.append(overload_note)
            convs = None
            has_p = False
            if fmt_at is None:
                pos = fmt_indexes[0] if fmt_indexes else 0
                shown = collapse(raw_args[pos], 80) if pos < len(raw_args) else '(arguments not parsed)'
                rest = raw_args[pos + 1:] if pos < len(raw_args) else []
                const = format_constant(raw_args[pos], ctx, src, f, off) if pos < len(raw_args) else None
                if kind == 'va_list':
                    parts.append('forwards a va_list: the format and arguments come from the callers of %s' % src.symbol(i))
                elif const is not None:
                    cname, values, element = const
                    per_value = [PRINTF_CONVERSION_RE.findall(v) for v in values]
                    shown_values = ', '.join('"%s"' % collapse(v, 60) for v in values[:4]) + (', ...' if len(values) > 4 else '')
                    parts.append('format is the const char * constant %s%s: %s' % (
                        cname, ' (an element of the array)' if element else '', shown_values))
                    if all(pv == per_value[0] for pv in per_value):
                        convs = per_value[0]
                        parts.append('conversions ' + (' '.join(c for c in convs if c != '%%') or 'none')
                                     + (' (each value)' if len(values) > 1 else ''))
                    else:
                        parts.append('conversions by value: ' + '; '.join(' '.join(c for c in pv if c != '%%') or 'none'
                                                                        for pv in per_value[:6]))
                        convs = None
                    has_p = any(c.endswith('p') for pv in per_value for c in pv)
                else:
                    parts.append('format not a literal: %s' % shown)
            else:
                has_literal, value, fmt_macros = format_text(raw_args[fmt_at], smacros)
                alternatives = literal_values(raw_args[fmt_at], smacros) \
                    if split_top_operator(raw_args[fmt_at], ('?',)) is not None else []
                parts.append('format %s' % collapse(raw_args[fmt_at], 160))
                if len(alternatives) > 1:
                    per_value = [PRINTF_CONVERSION_RE.findall(v) for v in alternatives]
                    has_p = any(c.endswith('p') for pv in per_value for c in pv)
                    if all(pv == per_value[0] for pv in per_value):
                        convs = per_value[0]
                        parts.append('conversions ' + (' '.join(c for c in convs if c != '%%') or 'none') + ' (each branch)')
                    else:
                        convs = None
                        parts.append('conversions by branch: ' + '; '.join(' '.join(c for c in pv if c != '%%') or 'none'
                                                                         for pv in per_value))
                else:
                    convs = PRINTF_CONVERSION_RE.findall(value)
                    has_p = any(c.endswith('p') for c in convs)
                    parts.append('conversions ' + (' '.join(c for c in convs if c != '%%') or 'none'))
                    if fmt_macros:
                        parts.append('format uses macros ' + ', '.join(sorted(set(fmt_macros))))
                rest = raw_args[fmt_at + 1:] if passes_args else []
            if fmt_at is not None or kind != 'va_list':
                parts.append('%d arguments%s' % (len(rest), (': ' + collapse(', '.join(collapse(a, 60) for a in rest), 200))
                                                  if rest else ''))
                if rest:
                    note, types = argument_type_note(src, f, cls, expand_comma_macros(rest, ctx),
                                                     None if in_define else convs, ctx, off)
                    parts.append(note)
                    stats['printf-calls arguments'] += len(types)
                    stats['printf-calls arguments typed'] += sum(1 for t in types if t != '?')
            if has_p:
                parts.append('has %p')
            sym = src.symbol(i)
            if last_component(sym) == 'to_string' or sym.startswith(('DEF_TO_STRING', '#define TO_STRING', '#define DEF_TO_STRING')):
                parts.append('inside to_string')
            for s in sinks:
                if s in PRINTF_SINK_TEXT:
                    parts.append(PRINTF_SINK_TEXT[s])
            if in_define:
                parts.append('inside #define %s (a fixed format: its uses are not rows)' % macro_name)
            out.append((i, '; '.join(parts)))
    return out


def local_type(src, f, name, off):
    info = find_local(src, f, name, off)
    if info is None:
        return None, None
    typ = norm_type(info[1])
    if info[0] != 'param' and info[2] < len(src.lines):
        m = re.search(r'([^;{}(),]*?)\b' + re.escape(name) + r'\b(\s*\[[^\]]*\])?', src.lines[info[2]])
        if m and not typ.startswith('const ') and re.search(r'\bconst\b', m.group(1).split('*')[0]):
            typ = 'const ' + typ
        if m and m.group(2) and '*' not in typ:
            typ = typ + ' ' + re.sub(r'\s+', '', m.group(2))
    return typ, info


PP_TRUE = frozenset(('__APPLE__', '__MACH__', '__aarch64__', '__arm64__', '__clang__', '__GNUC__', '__cplusplus',
                     '__LP64__', '__unix__'))
PP_FALSE = frozenset(('_WIN32', '_WIN64', '__linux__', '__x86_64__', '_MSC_VER', '__FreeBSD__', '__MINGW32__', '__CYGWIN__',
                      '__i386__', '__ANDROID__', '__EMSCRIPTEN__'))


def pp_condition(kind, expr):
    names = re.findall(r'[A-Za-z_]\w*', expr.replace('defined', ' '))
    if not names or any(n not in PP_TRUE and n not in PP_FALSE for n in names):
        return None
    if kind == 'ifdef':
        return names[0] in PP_TRUE
    if kind == 'ifndef':
        return names[0] not in PP_TRUE
    e = re.sub(r'defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)',
               lambda m: ' True ' if (m.group(1) or m.group(2)) in PP_TRUE else ' False ', expr)
    e = re.sub(r'\b(\w+)\b', lambda m: ' True ' if m.group(1) in PP_TRUE else ' False ' if m.group(1) in PP_FALSE
               else m.group(1), e)
    e = e.replace('&&', ' and ').replace('||', ' or ').replace('!', ' not ')
    try:
        return bool(eval(e, {'__builtins__': {}}, {'True': True, 'False': False}))
    except Exception:
        return None


def resolve_directives(text):
    out = []
    stack = []
    for line in text.split('\n'):
        dm = re.match(r'[ \t]*#[ \t]*(ifdef|ifndef|if|elif|else|endif)\b(.*)$', line)
        if not dm:
            if all(active for active, taken in stack):
                out.append(line)
            continue
        kind, rest = dm.group(1), dm.group(2)
        if kind in ('if', 'ifdef', 'ifndef'):
            val = pp_condition(kind, rest)
            val = True if val is None else val
            stack.append((val, val))
        elif kind == 'elif' and stack:
            active, taken = stack[-1]
            val = pp_condition('if', rest)
            val = (not taken) if val is None else (val and not taken)
            stack[-1] = (val, taken or val)
        elif kind == 'else' and stack:
            active, taken = stack[-1]
            stack[-1] = (not taken, True)
        elif kind == 'endif' and stack:
            stack.pop()
    return '\n'.join(out)


def split_args_plain(inner):
    args = []
    depth = 0
    start = 0
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c in '"\'':
            j = i + 1
            while j < n and inner[j] != c:
                j += 2 if inner[j] == '\\' else 1
            i = j + 1
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            args.append(inner[start:i])
            start = i + 1
        i += 1
    if inner[start:].strip() or args:
        args.append(inner[start:])
    return args


def merge_template_splits(args):
    out = []
    for a in args:
        if out:
            prev = re.sub(r'"(?:[^"\\]|\\.)*"|<<|>>|->|<=|>=', ' ', out[-1])
            if re.search(r'[A-Za-z_]\w*\s*<[^<>()]*$', prev) and prev.count('<') > prev.count('>') and '>' in a:
                out[-1] = out[-1] + ',' + a
                continue
        out.append(a)
    return out


def looks_like_params(raw_args):
    if not raw_args:
        return False
    if any(a.strip() == '...' for a in raw_args):
        return True
    return all(re.fullmatch(r'(?:const\s+)?(?:(?:unsigned|signed)\s+)?[A-Za-z_][\w:]*(?:\s*<[^()]*>)?(?:\s+const)?'
                            r'(?:\s*[*&]+\s*|\s+)(?:const\s+)?[A-Za-z_]\w*(?:\s*\[[^\]]*\])?(?:\s*=.*)?',
                            ' '.join(a.split()), re.S) for a in raw_args)


def format_constant(arg, ctx, src=None, f=None, off=0):
    m = re.fullmatch(r'\s*((?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*))(\s*\[[^\[\]]*\])?\s*', arg)
    if not m:
        return None
    if f is not None and '::' not in m.group(1):
        typ, info = local_type(src, f, m.group(2), off)
        if info is not None:
            if char_pointer_kind(typ) != 'const':
                return None
            acode, alines, astarts, atext = aligned_view(src)
            values = []
            if info[2] < len(astarts):
                dm = re.compile(r'\b' + re.escape(m.group(2)) + r'\s*(?:\[[^\]]*\]\s*)?=\s*').search(acode, astarts[info[2]])
                if dm and bisect.bisect_right(astarts, dm.start()) - 1 <= info[2] + 1:
                    k = dm.end()
                    depth = 0
                    while k < len(acode):
                        c = acode[k]
                        if c in '([{':
                            depth += 1
                        elif c in ')]}':
                            if depth == 0:
                                break
                            depth -= 1
                        elif c in ';,' and depth == 0:
                            break
                        k += 1
                    values = literal_values(atext[dm.end():k], ctx['string_macros'])
            if not values:
                return None
            return m.group(2), values, bool(m.group(3))
    if m.group(2) not in ctx['str_consts']:
        return None
    return re.sub(r'\s+', '', m.group(1)), list(ctx['str_consts'][m.group(2)]), bool(m.group(3))


def aligned_body_open(src, fname, hl):
    open_off = src.body_open.get((fname, hl))
    if open_off is None:
        return None
    ln = src.line_of(open_off)
    k = src.lines[ln][:open_off - src.starts[ln]].count('{')
    code, lines, starts, text = aligned_view(src)
    j = -1
    for _ in range(k + 1):
        j = lines[ln].find('{', j + 1)
        if j < 0:
            return None
    return starts[ln] + j


def fast_parser_rows(src, ctx, stats):
    out = []
    if src.path not in FAST_PARSER_PATHS:
        return out
    code, alines, astarts, text = aligned_view(src)
    producers = ctx['fast_producers']
    found = collections.OrderedDict()
    skipped = set()
    node_types = collections.defaultdict(list)
    for fname, hl, ol, end, extra in src.function_spans():
        open_off = aligned_body_open(src, fname, hl)
        if open_off is None:
            continue
        body_end = astarts[end] + len(alines[end])
        items = []
        for m in FAST_PRODUCER_RE.finditer(code, open_off, body_end):
            if m.group(1):
                what = 'node %s' % m.group(1)
            elif m.group(3):
                if m.group(2) == 'cur_token_type_' or m.group(3) == 'T_INVALID':
                    continue
                what = '%s = %s' % (m.group(2), m.group(3))
            elif m.group(4):
                if m.group(4) == 'T_INVALID':
                    continue
                what = 'type_ = %s' % m.group(4)
            elif m.group(5):
                what = m.group(5) + '()'
            else:
                what = 'PARAM_TOKEN'
            items.append((m.start(), what))
        for m in IDENT_CALL_RE.finditer(code, open_off, body_end):
            callee = m.group(3)
            if callee in producers and callee != extra[1] and callee not in FAST_NODE_HELPERS:
                types = sorted(set(t for t in re.findall(r'\bT_[A-Z0-9_]+\b', balanced_call_args(code, m.end() - 1))
                                   if t != 'T_INVALID'))
                items.append((m.start(3), '%s(%s)' % (callee, ', '.join(types))))
        for m in FAST_T_ARG_CALL_RE.finditer(code, open_off, body_end):
            if m.group(1) in FAST_NOT_CALLEES or m.group(1) in producers:
                continue
            types = sorted(set(t for t in re.findall(r'\bT_[A-Z0-9_]+\b', m.group(2)) if t != 'T_INVALID'))
            if types:
                items.append((m.start(), '%s(%s)' % (m.group(1), ', '.join(types))))
        for pos, what in items:
            br = fast_parser_branch(code, text, pos, open_off)
            hops = 0
            while br is not None and br[2] is not None and fast_error_only(br[2], producers, code, br[0]) and hops < 12:
                skipped.add((fname, br[0]))
                br = fast_outer_branch(code, text, br, open_off)
                hops += 1
            if br is None:
                stats['fast-parser products outside any branch'] += 1
                continue
            start, label, cond = br
            key = (fname, start)
            if key not in found:
                path = []
                p, current = start, label
                for _ in range(8):
                    if current.startswith(('case ', 'default')):
                        opener = block_opener(code, p)
                        if opener <= open_off:
                            break
                        p = header_before(code, opener)[0]
                    outer = fast_parser_branch(code, text, p, open_off)
                    if outer is None:
                        break
                    path.append(outer[1])
                    p, current = outer[0], outer[1]
                found[key] = [bisect.bisect_right(astarts, start) - 1, label, [], list(reversed(path))]
            if what not in found[key][2]:
                found[key][2].append(what)
            for t in re.findall(r'\bT_[A-Z0-9_]+\b', what):
                if t != 'T_INVALID' and t not in node_types[fname]:
                    node_types[fname].append(t)
    stats['fast-parser error branches'] += len(skipped)
    for (fname, start), (ln, label, whats, path) in found.items():
        where = (' (inside %s)' % ' > '.join(path)) if path else ''
        note = ''
        if not any(re.search(r'\bT_[A-Z0-9_]+\b', w) for w in whats):
            others = node_types.get(fname, [])
            var = next((w.split(' ', 1)[1] for w in whats if w.startswith('node ')), None)
            if others:
                note = '; node types this function builds: %s' % ', '.join(others)
            elif var:
                note = '; the node type is %s, set earlier or passed in by the caller' % var
            else:
                note = '; no node type is named in this function (the callee builds the node)'
        out.append((ln, '%s%s -> %s%s' % (label, where, ', '.join(whats), note)))
    return out


def fast_outer_branch(code, text, br, lo):
    start, label = br[0], br[1]
    p = start
    if label.startswith(('case ', 'default')):
        opener = block_opener(code, p)
        if opener <= lo:
            return None
        p = header_before(code, opener)[0]
    return fast_parser_branch(code, text, p, lo)


def fast_error_only(cond, producers, code=None, pos=None):
    c = re.sub(r'OB_(?:UN)?LIKELY\s*\(', '(', cond)
    if re.search(r'\bT_[A-Z0-9_]+\b|\bPARAM_TOKEN\b', c):
        return False
    for m in IDENT_CALL_RE.finditer(c):
        if m.group(3) in producers:
            return False
    parts = [c]
    for op in ('||', '&&'):
        nxt = []
        for part in parts:
            pieces = []
            rest = part
            while True:
                hit = split_top_operator(rest, (op,))
                if hit is None:
                    pieces.append(rest)
                    break
                pieces.append(rest[:hit[0]])
                rest = rest[hit[0] + 2:]
            nxt += pieces
        parts = nxt
    for part in parts:
        q = part.strip()
        while q.startswith('(') and matching_close(q, 0) == len(q) - 1:
            q = q[1:-1].strip()
        q = q.lstrip('!').strip()
        while q.startswith('(') and matching_close(q, 0) == len(q) - 1:
            q = q[1:-1].strip()
        if re.fullmatch(r'OB_(?:FAIL|SUCC|ISNULL|NOT_NULL)\s*\(.*\)', q, re.S) or q == 'ret':
            continue
        if re.fullmatch(r'.*?[!=]=\s*(?:NULL|nullptr|OB_SUCCESS|ret)', q, re.S) or \
                re.fullmatch(r'(?:NULL|nullptr|OB_SUCCESS|ret)\s*[!=]=.*', q, re.S):
            continue
        return False
    return True


def fast_parser_branch(code, text, pos, lo):
    p = pos
    for _ in range(40):
        depth = 0
        k = p - 1
        while k > lo:
            c = code[k]
            if c == ')':
                depth += 1
            elif c == '(':
                if depth == 0:
                    km = re.search(r'\b(if|while|for|switch)\s*$', code[max(lo, k - 12):k])
                    if km and km.group(1) == 'if':
                        top = max(lo, k - 12) + km.start()
                        close = matching_close(code, k)
                        head = re.search(r'\belse\s*$', code[max(lo, top - 12):top])
                        start = max(lo, top - 12) + head.start() if head else top
                        return start, '%s (%s)' % ('else if' if head else 'if', collapse(text[k + 1:close], 120)), \
                            code[k + 1:close]
                else:
                    depth -= 1
            elif c in ';{}' and depth == 0:
                break
            k -= 1
        stmt = code[k + 1:p]
        cm = re.match(r'\s*((?:(?:case\s[^;{}]*?[^:]:(?!:)|default\s*:(?!:))\s*)+)', stmt)
        if cm:
            a = k + 1 + cm.start(1)
            return a, case_label_text(text[a:k + 1 + cm.end(1)]), None
        im = re.match(r'\s*(else\s+)?if\s*\(', stmt)
        if im:
            o = k + 1 + im.end() - 1
            close = matching_close(code, o)
            return k + 1 + len(stmt) - len(stmt.lstrip()), '%s (%s)' % ('else if' if im.group(1) else 'if',
                                                                       collapse(text[o + 1:close], 120)), code[o + 1:close]
        if re.match(r'\s*else\b', stmt):
            a = k + 1 + len(stmt) - len(stmt.lstrip())
            return a, 'else', else_condition(code, a)
        opener = k if k > lo and code[k] == '{' else block_opener(code, p)
        if opener <= lo:
            return None
        between = code[opener + 1:p]
        labels = [lm for lm in re.finditer(r'\b(?:case\s[^;{}]*?[^:]:(?!:)|default\s*:(?!:))', between)
                  if between[:lm.start()].count('{') == between[:lm.start()].count('}')]
        if labels:
            last = labels[-1]
            first = last
            for lm in reversed(labels[:-1]):
                if between[lm.end():first.start()].strip():
                    break
                first = lm
            a = opener + 1 + first.start()
            return a, case_label_text(text[a:opener + 1 + last.end()]), None
        hs, header = header_before(code, opener)
        kind, cond = if_condition(header)
        if kind in ('if', 'else if'):
            hm = re.search(r'\b(?:else\s+)?if\s*\(', code[hs:opener])
            o = hs + hm.end() - 1
            close = matching_close(code, o)
            return hs + hm.start(), '%s (%s)' % (kind, collapse(text[o + 1:close], 120)), cond
        if kind == 'else':
            a = hs + len(header) - len(header.lstrip())
            return a, 'else', else_condition(code, a)
        hc = CASE_LABELS_RE.search(header)
        if hc:
            return hs + hc.start(1), case_label_text(text[hs + hc.start(1):hs + hc.end(1)]), None
        if re.match(r'\s*(?:while|for|do|switch)\b', header) or not header.strip():
            p = hs
            continue
        return None
    return None


def else_condition(code, pos):
    j = pos - 1
    while j >= 0 and code[j].isspace():
        j -= 1
    if j < 0 or code[j] != '}':
        return None
    ob = matching_open(code, j)
    if ob < 0:
        return None
    direct, conds, top = chain_conditions(code, ob)
    return conds[-1] if conds else None


def case_label_text(raw):
    labels = re.findall(r"\b(?:case\s+(?:'(?:[^'\\]|\\.)*'(?:\s*\.\.\.\s*'(?:[^'\\]|\\.)*')?|[^:]+?)\s*:(?!:)|default\s*:(?!:))",
                        raw)
    return ' '.join(' '.join(l.split()) for l in labels) if labels else collapse(raw, 80)


DECISION_CALLEE_RE = re.compile(r'(?i)can_insert|check_can|need_dump|dump|bypass|spill|limit|bound|exceed|enough')
ASSIGN_TARGET_RE = re.compile(
    r'^\s*(?:(?:const|static|constexpr|volatile)\s+)*(?:(?:[A-Za-z_][\w:]*(?:\s*<[^;{}()]*?>)?)(?:\s*[*&]+\s*|\s+))?'
    r'([A-Za-z_]\w*)\s*(\+=|-=|\*=|/=|=(?!=))')
LOG_STATEMENT_RE = re.compile(r'\s*(?:[A-Z_][A-Z0-9_]*_)?LOG(?:_[A-Z]+)*\w*\s*\(|\s*_?OB_LOG\w*\s*\(')


def without_templates(stmt):
    s = stmt.replace('->', '  ')
    s = re.sub(r'\b(?:static_cast|reinterpret_cast|const_cast|dynamic_cast)\s*<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>', ' ', s)
    s = re.sub(r'(?<=[\w])\s*<[\w:\s,*&]*(?:<[\w:\s,*&]*>[\w:\s,*&]*)*>(?=\s*(?:[(:&*]|\w))', ' ', s)
    return s.replace('<<', '  ').replace('>>', '  ')


def local_target(src, f, stmt, pos):
    tm = ASSIGN_TARGET_RE.match(stmt)
    if not tm or tm.group(1) in NOT_TYPE_WORDS or tm.group(1) in ('if', 'while', 'for', 'switch'):
        return None, None
    typ, info = local_type(src, f, tm.group(1), pos)
    if info is None or info[0] == 'param' and tm.group(2) == '=' and '&' not in (typ or ''):
        return (tm.group(1), typ) if info is not None else (None, None)
    return tm.group(1), typ


def follow_size(src, f, name, from_pos, fname, depth=0, seen=None):
    code = src.code
    seen = seen if seen is not None else set()
    if name in seen or depth > 3:
        return 'other', None
    seen.add(name)
    body_end = src.starts[f[3]] + len(src.lines[f[3]])
    verdicts = []
    decision = None
    for m in re.finditer(r'(?<![\w.>])' + re.escape(name) + r'\b', code[from_pos:body_end]):
        pos = from_pos + m.start()
        sa, sb = statement_span(code, pos)
        ustmt = code[sa:sb]
        tm = ASSIGN_TARGET_RE.match(ustmt)
        if tm and tm.group(1) == name and sa + tm.start(1) <= pos < sa + tm.end(1):
            continue
        if LOG_STATEMENT_RE.match(ustmt):
            continue
        callee, first = enclosing_call(code, pos)
        if callee and SIZEOF_SKIP_CALLEE_RE.match(callee):
            verdicts.append('alloc')
            continue
        rel = pos - sa
        near = [x.group(1) for x in (re.search(r'([A-Za-z_]\w*)\s*[-+]=?\s*$', ustmt[:rel]),
                                     re.match(r'\s*[-+]\s*([A-Za-z_]\w*)', ustmt[rel + len(name):])) if x]
        diffs = re.findall(r'\b([a-z_]\w*)\s*-\s*([a-z_]\w*)\b', ustmt)
        if any(pointer_kind(src, f, o, pos) for o in near if o not in NOT_TYPE_WORDS) or any(
                pointer_kind(src, f, x, pos) and pointer_kind(src, f, y, pos) for x, y in diffs):
            verdicts.append('alloc')
            continue
        if callee and DECISION_CALLEE_RE.search(callee) or DECISION_WORD_RE.search(ustmt):
            verdicts.append('decision')
            decision = decision or ustmt
            continue
        if tm and tm.group(1) != name:
            other, otyp = local_target(src, f, ustmt, pos)
            if other and otyp and '*' in otyp:
                verdicts.append('alloc')
                continue
            if other:
                v, d = follow_size(src, f, other, sb, fname, depth + 1, seen)
                verdicts.append(v)
                if v == 'decision':
                    decision = decision or d
                continue
        if re.match(r'\s*return\b', ustmt):
            if DECISION_FUNCTION_RE.search(last_component(fname)):
                verdicts.append('decision')
                decision = decision or ustmt
            else:
                verdicts.append('other')
            continue
        verdicts.append('other')
    if 'decision' in verdicts:
        return 'decision', decision
    if verdicts and all(v == 'alloc' for v in verdicts):
        return 'alloc', None
    return 'other', None


def pointer_kind(src, f, name, pos):
    typ, info = local_type(src, f, name, pos)
    if typ and ('*' in typ or typ.endswith(']')):
        return 'ptr'
    return None


def sizeof_rows(src, ctx, stats):
    out = []
    if not src.path.startswith(SIZEOF_DIRS):
        return out
    code = src.code
    consts = ctx.get('sizeof_consts', {})
    const_re = ctx.get('sizeof_const_re')
    if 'sizeof' not in code and (const_re is None or not const_re.search(code)):
        return out
    seen = set()
    hits = [(m.start(), m.end() - 1, None) for m in SIZEOF_RE.finditer(code)]
    if const_re is not None:
        for m in const_re.finditer(code):
            defs = consts.get(m.group(1), ())
            if any(p == src.path and src.line_of(m.start()) == ln for p, ln, e in defs):
                continue
            if len(set((p, ln) for p, ln, e in defs)) != 1 and not any(p.rsplit('.', 1)[0] == src.stem() for p, ln, e in defs):
                continue
            hits.append((m.start(), None, m.group(1)))
    for start, open_paren, cname in sorted(hits):
        if open_paren is not None:
            close = matching_close(code, open_paren)
            if close < 0:
                continue
            operand = code[open_paren + 1:close].strip()
        else:
            operand = cname
        ln = src.line_of(start)
        if src.dkind.get(ln) in ('define', 'cont') and src.dname.get(ln, '').startswith(('OB_UNIS', 'DEFINE_')):
            continue
        a, b = statement_span(code, start)
        stmt = code[a:b]
        callee, first = enclosing_call(code, start)
        if callee is not None and SIZEOF_SKIP_CALLEE_RE.match(callee):
            stats['sizeof-formulas allocation, copy or serialization'] += 1
            continue
        before = code[a:start]
        if before.count('[') > before.count(']') or re.search(r'\b(?:static_assert|STATIC_ASSERT|alignas)\b', stmt):
            stats['sizeof-formulas allocation, copy or serialization'] += 1
            continue
        f = innermost_function(src, ln, start)
        fname = f[0] if f else src.symbol(ln)
        reach = None
        if f is not None:
            target, ttyp = local_target(src, f, stmt, start)
            if target and ttyp and ('*' in ttyp or ttyp.endswith(']')):
                stats['sizeof-formulas allocation or buffer layout through a local'] += 1
                continue
            if target:
                verdict, decision = follow_size(src, f, target, b, fname)
                if verdict == 'alloc':
                    stats['sizeof-formulas allocation or buffer layout through a local'] += 1
                    continue
                if verdict == 'decision':
                    reach = (target, decision)
        decided = reach or DECISION_WORD_RE.search(stmt) or DECISION_FUNCTION_RE.search(
            last_component(fname) if fname and fname != '-' else '')
        if not decided:
            stats['sizeof-formulas without decision words'] += 1
            continue
        key = (ln, operand)
        if key in seen:
            continue
        seen.add(key)
        if cname is not None:
            p, dln, expr = consts[cname][0]
            text = '%s (the named constant = %s, %s:%d) in: %s' % (cname, expr, posixpath.basename(p), dln + 1, collapse(stmt, 150))
            stats['sizeof-formulas named constants'] += 1
        else:
            text = 'sizeof(%s) in: %s' % (re.sub(r'\s+', ' ', operand), collapse(stmt, 180))
        if reach:
            text += '; reaches a decision through %s: %s' % (reach[0], collapse(reach[1], 140))
            stats['sizeof-formulas decisions through a local'] += 1
        out.append((ln, text))
    return out


def full_call_args(code, open_paren):
    close = matching_close(code, open_paren)
    if close < 0:
        return []
    inner = code[open_paren + 1:close]
    return [a.strip() for a in split_top(inner)] if inner.strip() else []


def work_area_rows(src, ctx):
    out = []
    path = src.path
    if path in WORK_AREA_SKIP:
        return out
    code = src.code
    wrx = ctx.get('wa_figure_re')
    has_limit = path.startswith(ROW_STORE_DIRS) and 'mem_limit_' in code
    if 'mem_processor' not in code and 'get_mem_bound' not in code and 'get_max_bound' not in code and not has_limit \
            and (wrx is None or not wrx.search(code)):
        return out
    for m in WORK_AREA_CALL_RE.finditer(code):
        ln = src.line_of(m.start())
        args = full_call_args(code, m.end() - 1)
        method = m.group(2)
        if method == 'init':
            shown = 'cache size %s' % collapse(args[1], 100) if len(args) > 1 else collapse(', '.join(args), 120)
        elif method in ('update_max_available_mem_size_periodically', 'extend_max_memory_size'):
            lam = next((x for x in args if x.startswith('[')), '')
            if lam:
                rets = [collapse(r, 100) for r in re.findall(r'\breturn\b([^;]*);', lam)]
                shown = 'callback returns %s' % ('; '.join(rets) if rets else collapse(lam, 140))
            else:
                shown = 'callback %s' % collapse(args[1], 100) if len(args) > 1 else collapse(', '.join(args), 140)
        else:
            shown = collapse(', '.join(args), 160)
        out.append((ln, 'work-area report %s.%s: %s' % (m.group(1), method, shown)))
    wrappers = ctx.get('wa_wrappers', {})
    seen = set()
    for fname, hl, ol, fend, extra in src.function_spans():
        open_off = src.body_open.get((fname, hl))
        if open_off is None:
            continue
        body_end = src.starts[fend] + len(src.lines[fend])
        body = code[open_off:body_end]
        if not WORK_AREA_FIGURE_RE.search(body) and (wrx is None or not wrx.search(body)) \
                and not (has_limit and 'mem_limit_' in body):
            continue
        fcls = enclosing_class(fname)
        tainted = {}
        for _ in range(3):
            for sm in re.finditer(r'[^;{}]+', body):
                stmt = sm.group(0)
                tm = ASSIGN_TARGET_RE.match(stmt)
                if not tm or tm.group(1) in tainted or re.fullmatch(r'(?:tmp_|temp_)?ret\w*|\w*ret_code\w*', tm.group(1)):
                    continue
                rhs = stmt[tm.end():]
                src_fig = work_area_figures(rhs, wrx, wrappers, fcls, ctx)
                via = [t for t in tainted if re.search(r'(?<![\w.>])' + re.escape(t) + r'\b', rhs)]
                if src_fig or via:
                    tainted[tm.group(1)] = src_fig + [x for v in via for x in tainted[v] if x not in src_fig]
        for sm in re.finditer(r'[^;{}]+', body):
            stmt = sm.group(0)
            a = open_off + sm.start()
            if a in seen or not WORK_AREA_DECISION_RE.search(without_templates(stmt)):
                continue
            figures = work_area_figures(stmt, wrx, wrappers, fcls, ctx)
            via = [t for t in tainted if re.search(r'(?<![\w.>])' + re.escape(t) + r'\b', stmt)
                   and not re.match(r'\s*(?:(?:const|static)\s+)?[A-Za-z_][\w:<>]*\s+' + re.escape(t) + r'\s*=', stmt)]
            limit = has_limit and re.search(r'(?<![\w.>])mem_limit_\b', stmt) is not None
            if not figures and not via and not limit:
                continue
            seen.add(a)
            marks = [m.start() for m in WORK_AREA_FIGURE_RE.finditer(stmt)]
            if wrx is not None:
                marks += [m.start() for m in wrx.finditer(stmt)]
            marks += [m.start() for t in via for m in re.finditer(r'(?<![\w.>])' + re.escape(t) + r'\b', stmt)]
            if limit:
                marks += [m.start() for m in re.finditer(r'(?<![\w.>])mem_limit_\b', stmt)]
            pos = a + (min(marks) if marks else len(stmt) - len(stmt.lstrip()))
            ln = src.line_of(pos)
            if figures:
                what = 'work-area decision on %s' % ', '.join(figures)
            elif via:
                what = 'work-area decision through %s (from %s)' % (', '.join(sorted(via)), ', '.join(
                    sorted(set(x for v in via for x in tainted[v]))))
            else:
                what = "row store's own dump decision on mem_limit_"
            out.append((ln, '%s: %s' % (what, collapse(stmt, 180))))
    return out


def work_area_figures(text, wrx, wrappers, fcls='', ctx=None):
    out = []
    for m in WORK_AREA_FIGURE_RE.finditer(text):
        if re.search(WORK_AREA_OWNER_PATTERN, text) and m.group(1) not in out:
            out.append(m.group(1))
    if wrx is not None:
        bases = ctx.get('class_bases', {}) if ctx else {}
        for m in wrx.finditer(text):
            name = m.group(1)
            if re.search(r'(?:\.|->)\s*$', text[:m.start()]):
                continue
            classes = wrappers.get(name, (frozenset(), '', '', 0))[0]
            if not fcls or not (set(ancestors(bases, fcls)) & set(classes)):
                continue
            shown = '%s (returns %s)' % (name, collapse(wrappers[name][1], 60))
            if shown not in out:
                out.append(shown)
    return out


def memmove_rows(src, ctx):
    out = []
    code = src.code
    if 'MEMMOVE' not in code and 'memmove' not in code:
        return out
    path = src.path
    if path.startswith('src/sql/engine/expr/'):
        where = 'expression code, one of the sites of s5-execution.md rule 3.5'
    elif path.startswith('src/query/api/query/engine/expr/'):
        where = "the expression core (ObExpr's deep copy)"
    else:
        where = 'outside the expression directories'
    for i, line in enumerate(src.lines):
        if 'emmove' not in line and 'MEMMOVE' not in line:
            continue
        if src.dkind.get(i) in ('define', 'cont') and src.dname.get(i) in ('MEMMOVE', 'MEMCPY', 'MEMCMP'):
            continue
        for m in MEMMOVE_RE.finditer(line):
            off = src.starts[i] + m.end() - 1
            if m.group(1) != 'MEMMOVE' and body_call_kind(src, i, m.group(1), line[:m.start()], off) != 'call':
                continue
            f = innermost_function(src, i, off)
            if f is not None and f[4][1] in MEMMOVE_PRIMITIVES:
                continue
            args = split_call_args(code, off)
            out.append((i, '%s(%s); %s' % (m.group(1), collapse(', '.join(args), 160), where)))
    return out


def frame_internal_rows(src, ctx, stats=None):
    out = []
    if not OPERATOR_FILE_RE.match(src.path):
        return out
    if stats is None:
        stats = collections.Counter()
    r05_raw = [t for t in FRAME_INTERNAL_R05 if t != 'reinterpret_cast<ObDatum']
    for rline in src.raw:
        if any(t in rline for t in r05_raw) or re.search(r'reinterpret_cast\s*<\s*(?:const\s+)?(?:common::)?ObDatum', rline):
            stats['frame-internals R05 raw file ' + src.path] = 1
            break
    for i, line in enumerate(src.lines):
        found = []
        base_only = True
        for m in FRAME_INTERNAL_RE.finditer(line):
            tok = m.group(1) or 'reinterpret_cast<ObDatum'
            if tok == 'clear_evaluated_flag' and re.match(r'\s*\(\s*\)', line[m.end():]):
                before = line[:m.start()]
                rm = re.search(r'([A-Za-z_]\w*)\s*(?:\.|->)\s*$', before)
                if not re.search(r'[)\]]\s*(?:\.|->)\s*$', before) and (rm is None or re.search(r'(?i)op', rm.group(1))):
                    tok = 'clear_evaluated_flag() (the operator base method)'
            if not tok.startswith('clear_evaluated_flag() (the operator'):
                base_only = False
            if tok not in found:
                found.append(tok)
        if found:
            narrow = [t for t in found if t in FRAME_INTERNAL_R05]
            if narrow:
                stats['frame-internals R05 whole-name file ' + src.path] = 1
            note = '; only the operator base method ObOperator::clear_evaluated_flag() (ob_operator.h:700-705), ' \
                   'which clears the eval flags of the operator\'s own output exprs: no direct frame access' if base_only else ''
            if base_only:
                stats['frame-internals base-method-only lines'] += 1
            out.append((i, 'frame internals: %s%s%s' % (', '.join(found), '; in R05 pattern' if narrow else '', note)))
    return out


def storage_sql_rows(src, ctx, stats):
    out = []
    if not src.path.startswith('src/storage/'):
        return out
    code = src.code
    has_using = False
    using_names = set()
    for m in USING_SQL_RE.finditer(code):
        if m.group(1):
            using_names.add(m.group(1))
        else:
            has_using = True
    if 'sql' not in code:
        return out
    blocks = []
    for m in SQL_NAMESPACE_OPEN_RE.finditer(code):
        close = matching_close(code, m.end() - 1)
        blocks.append((src.line_of(m.start()), src.line_of(close) if close >= 0 else len(src.lines) - 1))
    sql_types = ctx['sql_types']
    funcs = src.function_spans() if blocks else []
    defined = {}
    for fname, hl, ol, fend, extra in funcs:
        for b0, b1 in blocks:
            if b0 < hl and fend <= b1:
                defined[src.name_line(fname, hl)] = (fname, hl, fend)
    for i, line in enumerate(src.lines):
        inside = next(((b0, b1) for b0, b1 in blocks if b0 < i < b1), None)
        opening = next(((b0, b1) for b0, b1 in blocks if b0 == i), None)
        if 'sql' not in line and not has_using and not using_names and inside is None and opening is None:
            continue
        if opening is not None:
            inner = [d for d in defined.values() if opening[0] < d[1] <= opening[1]]
            what = ('defines %s' % ', '.join(d[0] for d in inner)) if inner else 'declares SQL types'
            out.append((i, 'opens namespace sql (lines %d-%d): storage %s here' % (opening[0] + 1, opening[1] + 1, what)))
            stats['storage-sql-uses namespace sql blocks'] += 1
            continue
        um = USING_SQL_RE.search(line)
        if um:
            out.append((i, 'using ' + ('sql::' + um.group(1) if um.group(1) else 'namespace sql')))
            continue
        names = []
        for m in SQL_QUALIFIED_RE.finditer(line):
            if m.group(1) not in names:
                names.append(m.group(1))
        loose = []
        if has_using or using_names or inside is not None:
            for m in re.finditer(r'(?<![\w:.>])([A-Z]\w*)\b', line):
                w = m.group(1)
                if w in names or w in loose:
                    continue
                if w in using_names or (has_using or inside is not None) and w in sql_types:
                    loose.append(w)
        if names:
            out.append((i, 'sql:: use: ' + ', '.join(names)))
        if i in defined:
            fname, hl, fend = defined[i]
            out.append((i, 'defines the SQL function %s inside namespace sql (lines %d-%d)' % (fname, hl + 1, fend + 1)))
            stats['storage-sql-uses SQL functions defined in storage'] += 1
        elif loose:
            if inside is not None:
                out.append((i, 'unqualified SQL name inside namespace sql: ' + ', '.join(loose)))
                stats['storage-sql-uses lines inside namespace sql'] += 1
            else:
                out.append((i, 'unqualified SQL name under a using declaration: ' + ', '.join(loose)))
                stats['storage-sql-uses unqualified lines'] += 1
    return out


def tracepoint_use(code, start, end):
    a, b = statement_span(code, start)
    stmt = code[a:b]
    before = code[a:start]
    callee, first = enclosing_call(code, start)
    if callee is not None and TP_SET_CALLEE_RE.match(callee):
        return 'set', stmt
    if re.search(r'-\s*(?:EVENT_CALL|OB_E|EVENT_CODE)\s*\(\s*$', before) \
            or re.search(r'\babs\s*\(\s*(?:(?:EVENT_CALL|OB_E|EVENT_CODE)\s*\(\s*)?$', before):
        return 'number', stmt
    wrapper = callee
    wpos = start
    if wrapper in ('EVENT_CALL', 'OB_E', 'EVENT_CODE'):
        wpos = code.rfind(wrapper, a, start)
        wrapper, _ = enclosing_call(code, wpos)
    if wrapper in ('OB_FAIL', 'OB_SUCC'):
        fired = fired_branch(code, a, b, wrapper)
        if fired is not None and not error_only_block(fired):
            return 'switch that sets ret', stmt
        return 'error injection', stmt
    if re.search(r'(?<![\w.>])ret\s*=(?!=)\s*[^;]*$', before) and not re.search(r'\b(?:int|auto)\s+ret\s*=', before):
        opener = block_opener(code, start)
        close = matching_close(code, opener) if opener >= 0 else -1
        after = code[b:close if close > b else b + 800]
        after = after[:re.search(r'\breturn\b|$', after).start()]
        if re.search(r'-\s*ret\b|\babs\s*\(\s*ret\b|\bret\s*%', after) and re.search(r'\bret\s*=\s*(?:common::)?OB_SUCCESS\b', after):
            return 'number kept in ret', stmt
        return 'error injection', stmt
    if wrapper in ('OZ', 'OX', 'OV', 'CK') or re.search(r'\breturn\s*[^;]*$', before):
        return 'error injection', stmt
    return 'value or switch', stmt


def fired_branch(code, a, b, wrapper):
    if b >= len(code) or code[b] != '{' or not re.match(r'\s*(?:else\s+)?if\s*\(', code[a:b]):
        return None
    close = matching_close(code, b)
    if close < 0:
        return None
    if wrapper == 'OB_FAIL':
        return code[b + 1:close]
    k = close + 1
    parts = []
    while True:
        em = re.match(r'\s*else\b', code[k:])
        if not em:
            break
        k += em.end()
        im = re.match(r'\s*if\s*\(', code[k:])
        if im:
            o = k + im.end() - 1
            c = matching_close(code, o)
            if c < 0:
                break
            parts.append(code[o + 1:c])
            k = c + 1
        bm = re.match(r'\s*\{', code[k:])
        if not bm:
            break
        o = k + bm.end() - 1
        c = matching_close(code, o)
        if c < 0:
            break
        parts.append(code[o + 1:c])
        k = c + 1
    return '\n'.join(parts)


def error_only_block(text):
    t = re.sub(r'\b[A-Z_]*LOG\w*\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*;', ' ', text)
    t = re.sub(r'(?<![\w.>])ret\s*=[^;]*;', ' ', t)
    t = re.sub(r'\breturn\b[^;]*;', ' ', t)
    t = re.sub(r'\b(?:OB_UNLIKELY|OB_LIKELY)\s*\(', '(', t)
    return not re.search(r'[A-Za-z_]', t)


def tracepoint_rows(src, ctx, stats):
    out = []
    code = src.code
    path = src.path
    numbers = ctx['tp_numbers']
    rule46 = ctx['rule46_names']
    in_scope = path.startswith(TRACEPOINT_DIRS) or path == TRACEPOINT_DIRS[-1]
    if path == TRACEPOINT_DEF_PATH:
        for i, line in enumerate(src.lines):
            dm = re.search(r'GLOBAL_ERRSIM_POINT_DEF\s*\(\s*(\d+)\s*,\s*(\w+)', line)
            if dm and dm.group(2) in rule46:
                out.append((i, 'definition of tracepoint %s %s; named by s5-execution.md rule 4.6%s' % (
                    dm.group(1), dm.group(2), '; set by tools/deploy/init.sql' if int(dm.group(1)) in ctx['tp_init']
                    else '')))
        return out
    if 'EventTable' not in code and 'ERRSIM_POINT_DEF' not in code:
        return out
    local = set(m.group(1) for m in LOCAL_TP_DEF_RE.finditer(code))
    hits = [(m.start(), m.end(), m.group(1), True) for m in GLOBAL_TP_RE.finditer(code)]
    if local:
        lrx = re.compile(r'(?<![\w.>:])(%s)\b(?!\s*\()' % '|'.join(re.escape(n) for n in sorted(local)))
        for m in lrx.finditer(code):
            if re.search(r'\bERRSIM_POINT_DEF\s*\(\s*$', code[max(0, m.start() - 40):m.start()]):
                continue
            hits.append((m.start(), m.end(), m.group(1), False))
    seen = set()
    for start, end, name, is_global in sorted(hits):
        named = name in rule46
        if not named and not in_scope:
            continue
        ln = src.line_of(start)
        if src.dkind.get(ln) in ('define', 'cont'):
            continue
        use, stmt = tracepoint_use(code, start, end)
        if use == 'set':
            continue
        if use == 'error injection' and not named:
            stats['batch-end-tracepoints error injections'] += 1
            continue
        num = numbers.get(name)
        if (ln, name) in seen:
            continue
        seen.add((ln, name))
        if not named and not use.startswith('number') and (num is None or num not in ctx['tp_init']) and not TP_TOPIC_RE.search(name):
            stats['batch-end-tracepoints switches whose names say nothing about batches'] += 1
        label = ('tracepoint %d %s' % (num, name)) if num is not None else (
            'tracepoint %s' % name if is_global else 'named errsim point %s (no number)' % name)
        how = {'number': 'a number', 'error injection': 'a switch (error injection form)',
               'number kept in ret': 'a number (the code stores it in ret, reads it back as a number, then resets ret to '
                                     'OB_SUCCESS)',
               'switch that sets ret': 'a switch (its OB_FAIL or OB_SUCC also sets ret, and the branch it takes does more '
                                       'than return the error)'}.get(use, 'a value or switch')
        parts = ['%s read as %s' % (label, how)]
        if named:
            parts.append('named by s5-execution.md rule 4.6')
        if num is not None and num in ctx['tp_init']:
            parts.append('set by tools/deploy/init.sql')
        if not named and TP_TOPIC_RE.search(name):
            parts.append('its name says %s' % TP_TOPIC_RE.search(name).group(0).lower().replace('_', ' '))
        parts.append('statement: ' + collapse(stmt, 160))
        out.append((ln, '; '.join(parts)))
    return out


def expr_factory_decl_rows(src, ctx):
    out = []
    code = src.code
    if 'ObRawExprFactory' not in code and (ctx['factory_macro_re'] is None or not ctx['factory_macro_re'].search(code)):
        return out
    for m in FACTORY_DECL_RE.finditer(code):
        off = m.start()
        ln = src.line_of(off)
        a, b = statement_span(code, off)
        if paren_depth_between(code, a, off) > 0 or re.search(r'\b(?:typedef|using|friend|return)\b', code[a:off]):
            continue
        kind_scope, owner_name, span = lock_scope(src, ln, off)
        name = m.group(1)
        args = split_call_args(code, m.end() - 1) if m.group(2) == '(' else []
        if m.group(2) == '(' and kind_scope == 'class':
            continue
        arg = collapse(', '.join(args), 100) if args else ''
        if kind_scope == 'macro':
            out.append((ln, 'local ObRawExprFactory %s(%s) inside #define %s (each use is a row too)' % (name, arg, owner_name)))
        elif kind_scope == 'function':
            tag = '; the allocator is a temporary memory context' if 'CURRENT_CONTEXT' in arg else ''
            out.append((ln, 'local ObRawExprFactory %s(%s) in %s%s' % (name, arg, owner_name, tag), owner_name + '::' + name))
        elif kind_scope == 'class':
            out.append((ln, 'ObRawExprFactory member %s of %s (a factory held by value)' % (name, owner_name),
                        owner_name + '::' + name))
        else:
            out.append((ln, 'global ObRawExprFactory %s(%s)' % (name, arg), name))
    for m in FACTORY_NEW_RE.finditer(code):
        ln = src.line_of(m.start())
        a, b = statement_span(code, m.start())
        out.append((ln, 'ObRawExprFactory allocated at run time: %s' % collapse(code[a:b], 160)))
    if ctx['factory_macro_re'] is not None:
        for m in ctx['factory_macro_re'].finditer(code):
            ln = src.line_of(m.start())
            if src.dkind.get(ln) in ('define', 'cont'):
                continue
            f = innermost_function(src, ln, m.start())
            out.append((ln, 'local ObRawExprFactory declared by macro %s in %s' % (m.group(1), f[0] if f else '-')))
    return out


def expr_factory_receiver(src, ln, off, recv):
    r = re.sub(r'\s+', '', recv)
    root = re.match(r'\(?\*?([A-Za-z_]\w*)', r)
    rname = root.group(1) if root else ''
    typ = ''
    f = innermost_function(src, ln, off)
    if f is not None and rname:
        info = find_local(src, f, rname, off)
        if info is not None:
            typ = info[1]
    probe = typ + ' ' + r
    if re.search(r'ObExecContext|get_exec_context\(\)|exec_ctx|\bctx_?$|^ctx\b|^\(?\*?ctx\b', probe):
        return 'exec context'
    if re.search(r'Ob\w*RawExpr\b|\bexpr\b', probe):
        return "the expression's own factory (s4-sql-front.md 2.3 rule 4)"
    if re.search(r'ObPL\w*AST|_ast\b|ast_?$', probe):
        return "a PL AST's factory"
    if re.search(r'(?i)package', probe):
        return "a PL package's factory"
    if re.search(r'optimizer_context|opt_ctx|ObOptimizerContext', probe):
        return "the optimizer context's factory"
    return 'receiver %s (type not resolved)' % (r or '-')


def expr_factory_caller_rows(src, ctx):
    out = []
    path = src.path
    if path.startswith(COMPILATION_DIRS):
        return out
    code = src.code
    if 'get_expr_factory' not in code:
        return out
    for i, line in enumerate(src.lines):
        if 'get_expr_factory' not in line:
            continue
        for m in EXPR_FACTORY_CALL_RE.finditer(line):
            off = src.starts[i] + m.start()
            paren = src.starts[i] + line.index('(', m.start())
            ck = body_call_kind(src, i, 'get_expr_factory', line[:m.start()], paren)
            if ck != 'call':
                continue
            rm = re.search(r'((?:[A-Za-z_]\w*(?:\([^()]*\))?\s*(?:\.|->)\s*)*)$', line[:m.start()])
            recv = rm.group(1).rstrip('.->').strip() if rm else ''
            recv = re.sub(r'(?:\.|->)\s*$', '', recv)
            if not recv:
                pm = re.search(r'([A-Za-z_]\w*)\s*(?:\.|->)\s*$', line[:m.start()])
                recv = pm.group(1) if pm else ''
            who = expr_factory_receiver(src, i, off, recv) if recv else 'no receiver (a member function of this class)'
            a, b = statement_span(code, off)
            stmt = code[a:b]
            hint = ''
            ctor = COMPILE_CONTEXT_CTOR_RE.match(stmt)
            if re.search(r'\bexpr_factory_\s*=(?!=)', stmt):
                hint = '; stored into a compile context (hands the factory to a compilation)'
            elif ctor and paren_depth_between(code, a, off) >= 1:
                hint = '; passed to the %s constructor (hands the factory to a compilation)' % (ctor.group(1) or ctor.group(2))
            listed = design_factory_listing(path, i + 1)
            if listed:
                hint += '; ' + listed
            out.append((i, 'get_expr_factory() on %s%s; statement: %s' % (who, hint, collapse(stmt, 140))))
    return out


def design_factory_listing(path, ln):
    base = posixpath.basename(path)
    if path == 'src/sql/ob_sql.cpp':
        return 's4-sql-front.md 2.3 rule 5 counts the six calls in ob_sql.cpp among the hand-offs to a compilation'
    if path == 'src/pl/ob_pl_build.cpp':
        return 's4-sql-front.md 2.3 rule 5 counts the four calls in ob_pl_build.cpp among the hand-offs to a compilation'
    for b, lo, hi, label in DESIGN_EXEC_TIME_FACTORY_CALLERS:
        if base == b and lo <= ln <= hi:
            return 's4-sql-front.md 2.3 rule 5 names it among the execution-time callers (%s)' % label
    return ''


def nested_in_template(text, pos):
    depth = 0
    k = pos - 1
    lo = max(0, pos - 400)
    while k >= lo:
        c = text[k]
        if c == '>':
            depth += 1
        elif c == '<':
            if depth == 0:
                return True
            depth -= 1
        elif c in '(;{},)' and depth == 0:
            return False
        k -= 1
    return False


def pointer_array_elem(typ, array_aliases=None):
    t = typ
    om = re.search(r'\b((?:[A-Za-z_]\w*::)*([A-Za-z_]\w*))\s*(<|\b)', t)
    if om and array_aliases and om.group(2) in array_aliases and '<' not in t:
        t = array_aliases[om.group(2)]
    am = POINTER_ARRAY_TYPE_RE.search(t)
    if not am:
        return None
    if nested_in_template(t, am.start()):
        return '(arrays)'
    return last_component(am.group(1))


def local_pointer_arrays(src, f):
    decls = []
    if f is None:
        return decls
    base = src.starts[f[1]]
    text = src.code[base:src.starts[f[3]] + len(src.lines[f[3]])]
    for m in IR_ARRAY_DECL_RE.finditer(text):
        decls.append((m.group(2), '(arrays)' if nested_in_template(text, m.start()) else m.group(1), base + m.start()))
    for m in C_POINTER_ARRAY_RE.finditer(text):
        decls.append((m.group(2), last_component(m.group(1)), base + m.start()))
    for m in LOCAL_POINTER_ARRAY_RE.finditer(text):
        if m.group(1):
            decls.append((m.group(2), '(arrays)' if nested_in_template(text, m.start()) else last_component(m.group(1)),
                          base + m.start()))
        else:
            decls.append((m.group(4), last_component(m.group(3)), base + m.start()))
    decls.sort(key=lambda d: d[2])
    return decls


def local_array_elem(decls, name, pos):
    best = None
    for n, elem, off in decls:
        if n == name and off < pos:
            best = elem
    if best is None:
        for n, elem, off in decls:
            if n == name:
                return elem
    return best


def null_array_rows(src, ctx, stats):
    out = []
    code = src.code
    if 'NULL' not in code and 'nullptr' not in code and 'prepare_allocate' not in code and 'extend_param_exprs' not in code:
        return out
    path = src.path
    if path.startswith(NULL_ARRAY_SKIP_DIRS):
        return out
    ir_dir = path.startswith(NULL_ARRAY_DIRS)
    stem = src.stem()
    member_ir = ctx['member_ir']
    cache = {}
    for pattern, what in ((NULL_PUSH_RE, 'push_back(NULL)'), (NULL_ELEMENT_RE, 'element set to NULL'),
                          (NULL_FILL_RE, None)):
        for m in pattern.finditer(code):
            ln = src.line_of(m.start())
            if src.dkind.get(ln) in ('define', 'cont'):
                continue
            op = what or m.group(1)
            if op == 'extend_param_exprs':
                out.append((ln, 'extend_param_exprs: parameter slots of an expression hold NULL until set'))
                continue
            rm = re.search(r'((?:[A-Za-z_]\w*\s*(?:\(\s*\))?\s*(?:\.|->)\s*)*)([A-Za-z_]\w*)\s*(\(\s*\))?\s*'
                           r'(?:\[[^\[\]]*\]\s*)?(?:(?:\.|->)\s*)?$', code[max(0, m.start() - 160):m.start()])
            if not rm:
                continue
            chain = re.sub(r'(?:\.|->)\s*$', '', rm.group(1).strip())
            recv = rm.group(2)
            getter = bool(rm.group(3))
            f = innermost_function(src, ln, m.start())
            key = f[:2] if f else None
            if key not in cache:
                cache[key] = local_pointer_arrays(src, f)
            local = cache[key]
            cls = enclosing_class(f[0]) if f else ''
            mp, ma = member_ir.get(('class', cls), (set(), set()))
            sp, sa = member_ir.get(('stem', stem), (set(), set()))
            if recv == 'children_':
                stats['null-arrays parse-tree children'] += 1
                continue
            elem = None
            known = None
            if chain and chain != 'this':
                rt = arg_expr_type(src, f, cls, chain, ctx, m.start()) if f is not None else None
                rc = class_of(rt) if rt else ''
                if rc:
                    for k in ancestors(ctx.get('class_bases', {}), rc):
                        known = ctx['member_arrays'].get(('class', k, recv))
                        if known:
                            break
                    if known is None:
                        mt = member_type(ctx, rc, recv)
                        if mt:
                            known = pointer_array_elem(mt) or '(not a pointer array)'
                    if known is None and not getter:
                        stats['null-arrays receiver member not found'] += 1
            if known is None and not (chain and chain != 'this'):
                known = local_array_elem(local, recv, m.start()) or ctx['member_arrays'].get(('class', cls, recv)) or \
                    ctx['member_arrays'].get(('stem', stem, recv))
            if getter:
                if recv in ctx['ir_array_getters']:
                    elem = 'returned by %s()' % recv
            elif known is not None:
                if known not in ctx['ir_types']:
                    stats['null-arrays arrays of other pointers'] += 1
                    continue
                elem = 'of %s *' % known
            elif recv in ma or recv in sa or recv in ctx['ir_array_members']:
                elem = 'member array of IR pointers'
            if elem is None and known is None and f is not None:
                full = (chain + '.' if chain and chain != 'this' else '') + recv + ('()' if getter else '')
                ft = arg_expr_type(src, f, cls, full, ctx, m.start())
                if ft:
                    fe = pointer_array_elem(ft)
                    if fe is None or fe == '(arrays)' or fe not in ctx['ir_types']:
                        stats['null-arrays arrays of other types (by declared type)'] += 1
                        continue
                    elem = 'of %s *' % fe
            if elem is None:
                if not ir_dir:
                    stats['null-arrays unresolved outside IR code'] += 1
                    continue
                elem = 'array type not resolved'
                stats['null-arrays not resolved in IR code'] += 1
            if op.startswith('prepare_allocate'):
                text = '%s on %s (%s): new slots hold NULL until set' % (op, recv, elem)
            else:
                text = '%s on %s (%s)' % (op, recv, elem)
            out.append((ln, text))
    return out


def warning_buffer_listed(path, ln):
    base = posixpath.basename(path)
    for b, lo, hi, label in WARNING_BUFFER_LISTED:
        if base == b and lo <= ln <= hi:
            return label
    return None


def warning_buffer_rows(src, ctx):
    out = []
    path = src.path
    if path in WARNING_BUFFER_PRIMITIVES:
        return out
    code = src.code
    wrx = ctx['wb_wrapper_re']
    if 'tsi_warning_buffer' not in code and 'ObWarningBufferIgnoreScope' not in code and (wrx is None or not wrx.search(code)):
        return out
    for i, line in enumerate(src.lines):
        found = []
        for m in WARNING_BUFFER_CALL_RE.finditer(line):
            if body_call_kind(src, i, m.group(1), line[:m.start()], src.starts[i] + m.end() - 1) == 'call':
                found.append('%s()' % m.group(1))
        if WARNING_BUFFER_SCOPE_RE.search(line):
            found.append('ObWarningBufferIgnoreScope declared')
        if WARNING_BUFFER_ASSIGN_RE.search(line):
            found.append('the slot assigned through ob_get_tsi_warning_buffer()')
        via = None
        if wrx is not None and not found:
            for m in wrx.finditer(line):
                if body_call_kind(src, i, m.group(1), line[:m.start()], src.starts[i] + m.end() - 1) == 'call':
                    via = m.group(1)
                    found.append('through %s()' % via)
                    break
        if not found:
            continue
        label = warning_buffer_listed(path, i + 1)
        if label is None and via == 'setup_wb':
            label = 'the request entries, through ObMPBase::setup_wb'
        status = ('listed in s2-errors.md 2.6 (%s)' % label) if label else 'not in the list of s2-errors.md 2.6: a new site'
        out.append((i, '%s; %s' % (', '.join(found), status)))
    return out


def new_list_figures(rows, info):
    out = {}
    stats = info['stats']
    lg = clean_rows(rows.get('lock-guards', []))
    special = re.compile(r'; (?:pointer|reference) to a lock|^container of locks|^lock whose type is the template parameter|'
                         r'^hand-made spin lock')
    plain = [r for r in lg if not special.search(r[3])]
    out['lock-guards'] = (
        '(Measured: %d lock types: %d primitives, %d subclasses of them, %d lock classes found by name and contents, %d names '
        'with a typedef, using alias or #define of a lock; rows: %d declarations of a lock object (%d members or static members, '
        '%d globals, %d locals or function statics, %d inside #define bodies; %d in vendored libeasy and zstd), %d pointer or '
        'reference members, %d containers of locks, %d locks whose type is a template parameter (%d of them references), %d '
        'hand-made spin locks; %d rows with no guard or lock call on the name in methods of their class or in their function; '
        '%d with uses through another object and %d with uses inside #define bodies that are not attributed; %d with a timed '
        'call; %d with a try call; left out: %d instances of templates that take a lock type as a parameter. The census behind '
        'the plan figure (evidence/conc/lock_census.py) matched 479 lines at 834bbee1e: 19 in src/oblib/lib/lock, one the '
        'predicate functor SlidingCond, and 459 that are rows here.)') % (
        len(info['lock_kinds']), sum(1 for v in info['lock_kinds'].values() if v not in ('lock subclass', 'lock class')),
        sum(1 for v in info['lock_kinds'].values() if v == 'lock subclass'),
        sum(1 for v in info['lock_kinds'].values() if v == 'lock class'), len(info['lock_alias_entries']),
        len(plain), count_constructs(plain, r'; (?:static )?member of '), count_constructs(plain, r'; global'),
        count_constructs(plain, r'; (?:local|function static) in '), count_constructs(plain, r'declared inside #define'),
        count_constructs(plain, r'\[vendored\]'),
        count_constructs(lg, r'; (?:pointer|reference) to a lock declared elsewhere or allocated'),
        count_constructs(lg, r'^container of locks'),
        count_constructs(lg, r'^lock whose type is the template parameter'),
        count_constructs(lg, r'^lock whose type is the template parameter.*; (?:pointer|reference) to a lock'),
        count_constructs(lg, r'^hand-made spin lock'), count_constructs(lg, r'no guard or lock call'),
        count_constructs(lg, r'more through another object'), count_constructs(lg, r'more inside #define bodies'),
        count_constructs(lg, r'\d+ timed'), count_constructs(lg, r'\d+ try'),
        stats.get('lock-guards policy-parameter instances', 0))
    pc = clean_rows(rows.get('printf-calls', []))
    fam = collections.Counter(r[3].split(' ', 1)[0] for r in pc)
    top = ', '.join('%s %d' % kv for kv in sorted(fam.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    n_args = stats.get('printf-calls arguments', 0)
    n_typed = stats.get('printf-calls arguments typed', 0)
    out['printf-calls'] = (
        '(Measured: %d printf-family functions (%d with other overloads) and %d macros that pass a format on; calls by callee: '
        '%s; %d calls matched to a printf overload by argument count, %d by the argument at the format position (%d of them '
        'a const char * constant), %d marked overload not resolved; left out: %d calls of the other overloads, %d of them '
        'because the receiver did not resolve to a class; %d calls whose format is not a literal; %d whose format is a '
        'const char * constant or local shown with its values; %d that forward a va_list; %d with %%p; %d that write the '
        'server log; %d that record a user message; %d that write stdout, stderr or a file; %d inside to_string; %d rows sit '
        'inside #define bodies with a fixed format; %d calls inside macros that pass their format on are not rows, their uses '
        'are; %d declarations with parameter lists left out; %d calls with preprocessor branches in their arguments; '
        '%d of %d arguments typed (%.1f%%); %d calls pass a different number of arguments than their conversions take.)') % (
        len(info['printf_funcs']), sum(1 for v in info['printf_funcs'].values() if v[3]), len(info['printf_macros']), top,
        count_constructs(pc, r'the printf overload, since only a printf overload takes'),
        count_constructs(pc, r'the printf overload, since the argument at the format position'),
        count_constructs(pc, r'the printf overload, since the argument at the format position is the const char \* constant'),
        count_constructs(pc, r'overload not resolved'),
        stats.get('printf-calls other overloads', 0), stats.get('printf-calls receiver not resolved', 0),
        count_constructs(pc, r'; format not a literal'), count_constructs(pc, r'; format is the const char \* constant'),
        count_constructs(pc, r'; forwards a va_list'), count_constructs(pc, r'; has %p'),
        count_constructs(pc, r'writes the server log'), count_constructs(pc, r'records a user message'),
        count_constructs(pc, r'writes (?:stdout|a stream)'), count_constructs(pc, r'; inside to_string'),
        count_constructs(pc, r'inside #define'), stats.get('printf-calls inside forwarding macros', 0),
        stats.get('printf-calls declarations', 0), stats.get('printf-calls with preprocessor branches in the arguments', 0),
        n_typed, n_args, 100.0 * n_typed / max(1, n_args), count_constructs(pc, r'the conversions take'))
    fp = clean_rows(rows.get('fast-parser', []))
    out['fast-parser'] = (
        '(Measured: %d branches: %d case labels, %d if or else-if conditions, %d else branches; %d branches that only test '
        'an error or an allocation are not rows, their products moved to the enclosing branch; %d rows that only set '
        'PARAM_TOKEN name the node types of their function; %d lexer functions produce parameters.)') % (
        len(fp), count_constructs(fp, r'^(?:case|default)'), count_constructs(fp, r'^(?:else )?if \('),
        count_constructs(fp, r'^else(?! if)'), stats.get('fast-parser error branches', 0),
        count_constructs(fp, r'-> PARAM_TOKEN; node types'), len(info['fast_producers']))
    so = clean_rows(rows.get('sizeof-formulas', []))
    out['sizeof-formulas'] = (
        '(Measured: %d rows: %d sizeof in statements with decision words, %d sizeof that reach a decision through a local, '
        '%d uses of %d named constants whose value is a sizeof expression; left out: %d that size an allocation, a copy, a '
        'hash, serialization or an array, %d whose local only sizes an allocation or lays out a buffer, and %d in arithmetic '
        'without decision words.)') % (
        len(so), sum(1 for r in so if 'reaches a decision' not in r[3] and 'the named constant' not in r[3]),
        sum(1 for r in so if 'reaches a decision' in r[3] and 'the named constant' not in r[3]),
        count_constructs(so, r'the named constant'),
        len(set(re.match(r'(\w+) \(the named constant', r[3]).group(1) for r in so if 'the named constant' in r[3])),
        stats.get('sizeof-formulas allocation, copy or serialization', 0),
        stats.get('sizeof-formulas allocation or buffer layout through a local', 0),
        stats.get('sizeof-formulas without decision words', 0))
    wa = clean_rows(rows.get('work-area-formulas', []))
    methods = collections.Counter(re.search(r'\.(\w+):', r[3]).group(1) for r in wa if r[3].startswith('work-area report'))
    out['work-area-formulas'] = (
        '(Measured: %d report calls (%s); %d decisions that compare a work-area figure directly or through a getter of the '
        'same class (%d getters found: %s); %d decisions through a local computed from a figure; %d comparisons with a row '
        "store's own mem_limit_; %d files.)") % (
        count_constructs(wa, r'^work-area report'), ', '.join('%s %d' % kv for kv in sorted(methods.items())) or 'none',
        count_constructs(wa, r'^work-area decision on'), len(info['wa_wrappers']),
        ', '.join(sorted(info['wa_wrappers'])) or 'none', count_constructs(wa, r'^work-area decision through'),
        count_constructs(wa, r"^row store's own dump decision"), len(set(r[0] for r in wa)))
    mm = clean_rows(rows.get('memmove', []))
    out['memmove'] = (
        '(Measured: %d in src/sql/engine/expr, the plan figure; %d in the expression core; %d elsewhere, %d of them in the '
        'grammar files.)') % (
        count_constructs(mm, r'rule 3\.5'), count_constructs(mm, r'expression core'),
        count_constructs(mm, r'outside the expression directories'), sum(1 for r in mm if r[0].endswith(('.y', '.l'))))
    fi = clean_rows(rows.get('frame-internals', []))
    files = set(r[0] for r in fi)
    impl = set(f for f in files if re.search(r'_op_impl\.\w+$', f))
    base_only = [r for r in fi if 'no direct frame access' in r[3]]
    other_files = set(r[0] for r in fi if 'no direct frame access' not in r[3])
    r05_whole = set(r[0] for r in fi if 'in R05 pattern' in r[3])
    r05_raw = set(k.rsplit(' ', 1)[1] for k in stats if k.startswith('frame-internals R05 raw file '))
    op_only = lambda fs: set(f for f in fs if re.search(r'_op\.(?:h|cpp)$', f))
    out['frame-internals'] = (
        "(Measured: %d lines in %d operator files: %d *_op files and %d *_op_impl files (the feasibility evidence's 66 counted "
        '*_op.{h,cpp} files only, so the equal totals are a coincidence); %d lines only call the operator base method '
        'clear_evaluated_flag(), and without them %d files remain. R05\'s pattern, as whole names, matches %d lines in %d '
        'files (%d of them *_op.{h,cpp}); matched as substrings of the raw text, comments included, over the *_op.{h,cpp} '
        'files the way R05\'s grep did, it matches %d files (%d with the *_op_impl files), since locate_expr_datum is also a '
        'prefix of locate_expr_datumvector (ob_limit_op.cpp:427, ob_select_into_op.cpp:997) and a commented-out line '
        'counts (ob_table_scan_with_index_back_op.cpp:124).)') % (
        len(fi), len(files), len(files - impl), len(impl), len(base_only), len(other_files),
        count_constructs(fi, r'in R05 pattern'), len(r05_whole), len(op_only(r05_whole)), len(op_only(r05_raw)), len(r05_raw))
    ss = clean_rows(rows.get('storage-sql-uses', []))
    out['storage-sql-uses'] = (
        '(Measured: %d lines with sql::, in %d files; %d lines with an unqualified SQL name under using namespace sql or '
        'using sql::X; %d using lines; %d namespace sql blocks in storage files, with %d lines inside that name an SQL type '
        'and %d SQL function definitions; %d SQL type names known.)') % (
        count_constructs(ss, r'^sql:: use'), len(set(r[0] for r in ss if r[3].startswith('sql:: use'))),
        count_constructs(ss, r'^unqualified SQL name under'), count_constructs(ss, r'^using '),
        count_constructs(ss, r'^opens namespace sql'), count_constructs(ss, r'^unqualified SQL name inside namespace sql'),
        count_constructs(ss, r'^defines the SQL function'), len(info['sql_types']))
    tp = clean_rows(rows.get('batch-end-tracepoints', []))
    other = [r for r in tp if 'rule 4.6' not in r[3]]
    out['batch-end-tracepoints'] = (
        '(Measured: %d rows for the tracepoints rule 4.6 names; %d other reads in the execution side and the optimizer: %d '
        'read as a number, %d switches that also set ret, %d other values or switches; %d set by init.sql; %d whose names '
        'have a batch, row, dump, sort, hash, bypass or cache word; left out: %d error injections.)') % (
        count_constructs(tp, r'named by s5-execution.md rule 4\.6'), len(other),
        sum(1 for r in other if 'read as a number' in r[3]),
        sum(1 for r in other if 'also sets ret' in r[3]),
        sum(1 for r in other if 'read as a value or switch' in r[3]),
        sum(1 for r in other if 'set by tools/deploy/init.sql' in r[3]),
        sum(1 for r in other if 'its name says' in r[3]),
        stats.get('batch-end-tracepoints error injections', 0))
    lf = clean_rows(rows.get('local-expr-factories', []))
    out['local-expr-factories'] = (
        '(Measured: %d local factories, %d of them on a temporary memory context; %d inside #define bodies; %d uses of such '
        'macros; %d members held by value; %d allocated at run time.)') % (
        count_constructs(lf, r'^local ObRawExprFactory \w+\(.*\) in '), count_constructs(lf, r'temporary memory context'),
        count_constructs(lf, r'inside #define'), count_constructs(lf, r'declared by macro'),
        count_constructs(lf, r'member'), count_constructs(lf, r'allocated at run time'))
    na = clean_rows(rows.get('null-arrays', []))
    out['null-arrays'] = (
        '(Measured: %d push_back(NULL), %d elements set to NULL, %d prepare_allocate fills, %d extend_param_exprs; %d rows '
        'whose array type is not resolved; left out: %d NULL stores and fills on arrays of other pointers or of arrays, %d on '
        'arrays of other types found by the declared type, %d that do not resolve outside the IR directories, %d parse-tree '
        'children_ stores.)') % (
        count_constructs(na, r'^push_back\(NULL\)'), count_constructs(na, r'^element set to NULL'),
        count_constructs(na, r'^prepare_allocate'), count_constructs(na, r'^extend_param_exprs'),
        count_constructs(na, r'array type not resolved'), stats.get('null-arrays arrays of other pointers', 0),
        stats.get('null-arrays arrays of other types (by declared type)', 0),
        stats.get('null-arrays unresolved outside IR code', 0), stats.get('null-arrays parse-tree children', 0))
    ef = clean_rows(rows.get('expr-factory-callers', []))
    out['expr-factory-callers'] = (
        '(Measured: %d on the exec context, %d on an expression, %d on a PL AST or package, %d others; %d hand the factory to '
        'a compilation (%d stored into a compile context, %d passed to a resolver or optimizer context constructor); %d are '
        'among the ten hand-offs s4-sql-front.md 2.3 rule 5 counts, %d among the execution-time callers it names.)') % (
        count_constructs(ef, r'on exec context'), count_constructs(ef, r"expression's own factory"),
        count_constructs(ef, r'PL (?:AST|package)'),
        sum(1 for r in ef if not re.search(r"on exec context|expression's own|PL (?:AST|package)", r[3])),
        count_constructs(ef, r'hands the factory to a compilation'),
        count_constructs(ef, r'stored into a compile context'), count_constructs(ef, r'constructor \(hands the factory'),
        count_constructs(ef, r'counts the (?:six|four) calls'), count_constructs(ef, r'names it among the execution-time'))
    wb = clean_rows(rows.get('warning-buffer-sites', []))
    out['warning-buffer-sites'] = (
        '(Measured: %d sites listed in s2-errors.md 2.6; %d not listed (new sites); %d wrapper functions found: %s.)') % (
        count_constructs(wb, r'listed in s2-errors'), count_constructs(wb, r'a new site'), len(info['wb_wrappers']),
        ', '.join(sorted(info['wb_wrappers'])) or 'none')
    return out


NOT_BUILT_NOTE = (' A row from a file the build never compiles carries [not built]: a C or C++ source that neither the '
                  'production source inventory the CMake build reads from tools/cmake/emit_bazel_source_inventory.py nor a '
                  'CMakeLists.txt file lists (so Bazel-only manual targets and ignored sources count as not built), or a header '
                  'that only such files include.')

DEFINITIONS = {
    'atomic-fields': 'Every atomic access in code (comments and strings blanked): ATOMIC_* macro calls, __sync_* and __atomic_* builtins, CAS128/LOAD128, the inc_update/dec_update helpers of ob_atomic.h, and the Windows Interlocked* calls; the primitives themselves (ob_atomic.h, atomic128.h) are skipped. Wrappers are expanded at each use: a macro whose body makes atomic calls (an access on a macro parameter goes to the argument, an access on another name is resolved where the macro is used, and a static declared inside the macro body gets its own row); a function whose atomic access targets one of its reference or pointer parameters (at each call the argument is the atomic object, or what the argument points to for a pointer parameter; calls are matched by the receiver\'s type when it resolves, else by name when at least 80% of the definitions with that name are such functions; repeated up to 4 rounds for functions that pass their own parameter on); and a member function named atomic_*, inc_update or dec_update of a value class (a class or struct whose only non-static data member, or a union, holds its whole value, such as SCN, LSN or ObTxSEQ) that works on that member: a call through an object, x.atomic_load(), is an atomic access on x, a call through a pointer or on this is not. The target is the object the call changes: the last member of the argument, the receiver of an accessor called with a dot, or func() for an accessor with no receiver or a pointer receiver; an accessor returning a non-const reference or the address of a member is followed to that member (by the receiver\'s type when known). An access on the single member of a value class through an object member (x.val_) is attributed to x, the field or variable holding the value; the member keeps a row that counts those accesses. An access through a pointer member without & (ATOMIC_LOAD(ptr_)) is also counted on the object ptr_ is assigned to point at (ptr_ = &x or ptr_(&x) in methods of its class), and the pointer\'s row names it. A bare name is looked up among the enclosing function\'s parameters, locals and function statics first (a local pointer or reference initialized from a member is followed to that member), then among the members of the enclosing class and its bases, then among globals in the same file, stem or a directly included header. A name reached through a member chain is resolved by the declared type of the member before it, with typedef and using aliases visible from the access expanded, template parameters not taken as class names, and candidates the access\'s file cannot reach through its includes dropped when a reachable one exists; else by the only non-vendored declaration, else by the only one in the same file, stem or included header. A Cls:: qualifier picks the class; out-of-line definitions of static members and of extern globals merge into their declaration. Rows: one per field or global, one per parameter, local or function static, one per static inside a macro, one per name left over (at its first access, with the number of candidates), and one per volatile field or global that has no atomic access.',
    'refcount': 'Reference-count call, definition and declaration lines (constructor member initializers are not calls): the evidence regex \\b(inc_ref|dec_ref)\\w*\\s*\\( on code lines, names with a prefix such as try_inc_ref_count (marked), and other reference-count names (marked): an inc, dec, incr, decr, acquire, release, revert, add or retain word followed by a ref, refs, uref, href, refcnt or refcount word (the SQL reference words query, subquery, obj and object excluded), a name with an unref, deref, xref or xhref word, or a name starting with ref_ that is not a getter (every declaration without parameters and const). Other functions whose name has a ref, refs, reference or similar word, or starts with retain, release, acquire, aquire, born, end or retire, count when their body changes a reference-count field or calls a reference-count function of the kinds above (one step); their definitions are rows, and their calls and declarations are rows when at least 80% of the definitions with that name count. A change of a reference-count field (an ATOMIC_ add, subtract, increment, decrement or compare-and-swap on a member whose name has a ref word, or ++, --, += or -= on a member named like ref_cnt_, ref_count_ or refcnt_) inside a function that is not itself a reference-count function is a row. Each call is tagged call, definition or declaration, and on-disk block reference when it goes through the storage object or block manager, takes a MacroBlockId, has a macro, block, addr or linked word in its name, or names a function whose every definition reaches such a call. Plus every class or struct definition whose name ends in Handle (the evidence regex counted classes only), every class whose name ends in Guard and whose methods make reference-count calls directly or reach one through up to six calls resolved by qualifier, receiver type or enclosing class (the row gives the call path), and every typedef or using alias whose name ends in Handle.',
    'const-cast': 'Code lines containing \\bconst_cast\\s*< or const_pointer_cast; the construct lists the target types and marks a target type that starts with const as adding const only. Also, found by a heuristic, C-style casts to a non-const pointer or reference type applied to: this inside a const member function; a member declared const T * or const T & in the enclosing class or its bases (inside a #define body, a member of a class in the same file stem); the result of a method whose every declaration returns const T & or const T *; or a name declared const T * or const T & in the same function within the 60 lines before. The plan figure 1,750 is git grep -c const_cast on raw text, comments and strings included. A const_cast inside a #define body is one row per body line; the uses of the macro are not rows.',
    'ret-compare': 'An OB_ error code from ob_errno or the parser\'s parse_define.h (OB_SUCCESS excluded) compared with == or != against: ret, tmp_ret or temp_ret, in either operand order and also when the code is assigned inside the comparison (OB_X == (ret = f())); any other variable or member that holds a code (hash_ret, ret_, err, result->extra_errno_); a call result (a template call and a call continued on the next line included); or an errsim expression. Plus case labels with such codes inside any switch. The evidence regex counted OB_SUCCESS == ret (831 lines) but not ret == OB_SUCCESS, which is inconsistent, and left != out. A comparison inside a #define body is one row per body line; the uses of the macro are not rows, and their count, by macro name, is given below.',
    'reset': 'Assignments ret = OB_SUCCESS anywhere on a code line (declarations excluded), also inside an expression such as FALSE_IT(ret = OB_SUCCESS); ret = 0 in a function whose ret is an error code (declared int ret = OB_SUCCESS or INIT_SUCC(ret)); and ternaries that assign OB_SUCCESS to ret, on one line or a statement over several lines, tagged by their condition: a conditional reset when the condition tests an error code (the code is swallowed), an errsim injection point when the condition is an errsim switch or tracepoint, and a code chosen by a flag otherwise. The evidence definition ^\\s*ret\\s*=\\s*(common::)?OB_SUCCESS\\s*; is the line-start subset. A reset inside a #define body is one row per body line; the uses of the macro are not rows.',
    'tmp-ret': 'Code lines that declare tmp_ret or temp_ret (also tmp_ret_code, tmp_ret_2; as int, int32_t, int64_t or auto, or through INIT_SUCC(tmp_ret), which expands to int tmp_ret = OB_SUCCESS), call OB_TMP_FAIL, assign the variable, or merge it into ret (ret = ... tmp_ret, COVER_SUCC). Also the same continue-and-record idiom through any other variable or code X, in functions and #define bodies: the merge (ret = OB_SUCCESS == ret ? X : ret, OB_SUCC(ret) ? X : ret, return ... ? X : ret, COVER_SUCC(X), and if (OB_SUCC(ret) or OB_SUCCESS == ret [&& X failed]) { ret = X; }) and, in the same function, the declarations and assignments of X. Lines that only compare or log tmp_ret are left out. The plan figure counted int tmp_ret = OB_SUCCESS; plus OB_TMP_FAIL( lines.',
    'ret-alias': 'int &ret = <member>; lines (ret_, error_ret_, callback_ret_, err_code_, errcode_ and other members), each tagged by its enclosing function: a bool operator<, a bool operator() whose two parameters have the same type or whose class name says compare, cmp or less, or a function named compare, cmp or less that returns bool or int, is a comparator, and the first if on ret after the alias is checked for an assignment or return in its branch. Plus comparators of the same kind that keep their error in a member without such an alias: a member named like ret_, sort_ret_ or err_code_, or any member that the comparator both assigns and compares with OB_SUCCESS (result_code_); and lambdas passed to std::sort, lower_bound, upper_bound and the other sort, search and heap algorithms or to ob_sort that capture ret by reference and set it.',
    'oom-sites': 'Every raise of OB_ALLOCATE_MEMORY_FAILED or the parser\'s OB_PARSER_ERR_NO_MEMORY (assignment, return, ternary, OV/OX/OZ/CK argument), judged on the if/else-if chain that governs it (multi-line) plus the condition of the branch around that chain, and on the statements before it on the same path (at most 4 lines, other branches excluded). In order: errsim injection when its own condition is an errsim switch or tracepoint; budget-backed when an allocation through a budget owner the plan names appears (clog and replay, the IO allocator, the KV cache store, the micro block cache FIFO, the temp-file write buffer pool, the vector module; as file and receiver patterns, or the raise sits in the owner\'s allocation or reservation function) and, marked separately, when the allocation goes through a bounded FIFO the evidence names but the plan does not (a general out-of-memory under Decision 12 unless the design names it); a general out-of-memory, not a row, when the governing condition is a null test, a validity or emptiness test of an object that allocates (!x.is_valid(), x.empty()), or ENOMEM; budget-backed at the SQL work-area spill when the governing condition is the spill decision (need_dump, enable_sql_dumped_, sql_mem_processor_); logical at one of the plan\'s named cases (hash-join partition depth, the KV-cache handle pool, the IVF cache, vsag NO_ENOUGH_MEMORY) by file and pattern; else logical, not a named plan case and provisional, when no allocation-like call appears (caught bad_alloc counts as one) and no null test appears in the governing chain (a null test in the outer condition counts only when its value was just assigned from a call); otherwise a general out-of-memory, not a row. A raise whose own condition compares a code with -4013 passes an earlier -4013 on and is not a row (the comparison is). -4013 passed to a function or macro that raises it (a parser fatal-error call, YYABORT_WITH_ERROR, a DDL simulation point\'s RET_ERR, CASE_* helpers as case labels) is a row; logging calls are not. Variables initialized to -4013 are marked, not treated as raises. Also: -4013 comparisons and case labels; raises, comparisons and case labels of the memory-budget codes, every errno whose name says a memory limit was reached, exceeded or exhausted plus OB_ALLOCATE_TMP_FILE_PAGE_FAILED (listed below); query memory tracker check calls, with the check functions\' declarations and definitions marked; and the micro block cache FIFO lines.',
    'server-service-slots': 'Template arguments of server_service, bind_server_service, unbind_server_service, server_obj_pool, borrow_server_object, return_server_object and ObServerServiceSlot, and the type argument of BIND_SERVICE/UNBIND_SERVICE, namespaces removed. One summary row per type, at its first bind site (else first use), with its lookup count (a lookup written inside a wrapper macro counts once per use of the macro); plus one row per lookup, bind, unbind, pool and slot site, and one row per use of a wrapper macro that looks a type up, so an implementer can search by file.',
    'subclassed-bases': 'Class and struct definitions (multi-line headers parsed, qualified names allowed, the row on the line that names the class) with a direct base among the plan\'s three, ObTimerTask, ObDLinkBase and ObFuncExprOperator, or the eight the evidence names for the same reason (a base class moved to Rust cannot be subclassed from C++): ObLink, ObIKVCacheKey, ObIKVCacheValue, ObITask, ObIDag, ObIReplaySubHandler, ObICheckpointSubHandler and AppendCb, whose rows carry a note. Class templates written inside #define bodies (one row per macro that is used at least once) and one row per use of such a macro, whose symbol is the class the macro writes. Indirect subclasses are not rows.',
    'arena-handoff': 'Proxy: (a) allocator members (memory contexts included), allocator-backed containers and borrowed-view members of classes that derive, directly or not, from a work-item, cache-entry, queue-node or thread-pool base (ObITask, ObIDag, ObIDagNet, ObTimerTask, ObAsyncTask, ObIOCallback, ObITransCallback, ObIKVCacheValue, ObIKVCacheKey, ObILibCacheObject, ObILibCacheNode, ObLink, ObSimpleThreadPool, ObThreadPool, ThreadPool, Threads, ObReentrantThread, ObAsyncTaskQueue, ObLinkQueueThreadPool, IObDedupTask, ObDDLTask); classes that reach other threads without such a base (a task a thread pool pushes as this) are not found; (b) add_task/add_dag/add_dag_net/push_task/schedule/submit/post/enqueue/TG_PUSH_TASK calls (add_task, add_dag, add_dag_net, push_task, submit_task, add_async_task, add_timer_task and schedule_task also without a receiver), push on a queue or task receiver, push without a receiver inside a thread-pool class, and put on a cache receiver, whose arguments (read across lines) name a variable the same function got from an allocation (assignment or alloc_*/create_* out-parameter) or pass an allocator or memory context (also through an accessor on another object, x.get_allocator()); IO hand-offs of arena buffers (aio_read, aio_write) are not covered; (c) placement new or OB_NEWx of such a class with an allocator or memory context argument.',
    'borrowed-views': 'Non-static class and struct data members whose type is ObString, ObDatum, ObObj, ObNewRow, ObRowkey, ObStoreRowkey, ObDatumRowkey, ObDatumRow, ObDatumRange, ObNewRange, a stored-row pointer, ObCompactRow or ObLobLocatorV2, a subclass of one (ObStorageDatum, ObObjParam, the stored-row subclasses), a typedef or using alias of one or of a container of them (DmlRow, ParamStore; an alias declared inside a class applies to that class and to its file), or a char/uint8_t pointer (value, pointer, reference, array, or a container of them); classes defined out of line with a qualified name (class A::B { ... }) and classes written inside a macro argument are included. Plus function definitions that take an allocator (a type named like an allocator or arena, a subclass or alias of ObIAllocator, or a template allocator parameter) and hand back a view through a non-const reference parameter or the return type.',
    'sort-hash-order': 'Sort call sites: std::sort/stable_sort/partial_sort/nth_element and heap operations, lib::ob_sort, qsort, member .sort()/.stable_sort(), sort() on this object (declarations excluded), and definitions of functions named sort, stable_sort or ob_sort. OB heap classes (a class named ...Heap or ...HeapBase with push, pop, top or replace_top, its subclasses and typedefs, such as ObBinaryHeap, ObRowHeap and the sort operator\'s TopnHeap, IMMSHeap and EMSHeap): declarations, push/pop/top/replace_top/remove/update calls on names declared with them, and the definitions of those operations. Hash iteration: begin()/foreach_refactored/for_each or range-for over a local, parameter or member (of the enclosing class or its bases, or reached through a member chain when at least 80% of the members with that name are hash containers) whose declared type is an OB hash container, a class derived from one (ObConfigContainer) or a typedef of one (an alias declared inside a class applies to that class and to its file); over the result of a getter that returns one (by the receiver\'s type, else when at least 80% of that getter\'s declarations do); and loops over an iterator of a hash type. The hash containers\' own methods are not rows.',
    'pointer-identity': 'Declarations of hash or ordered containers keyed by a pointer; inside functions, == and != where one side resolves to an IR pointer and the other is not NULL, a constant or a count (IR = transitive subclasses of ObRawExpr, ObStmt, ObDMLStmt, ObLogicalOperator, ObLogPlan, ObJoinOrder, TableItem, ColumnItem, SemiInfo, JoinedTable and ObExpr), resolved through locals, parameters, members, IR array elements and methods returning IR pointers (a name counts only if at least 80% of its declarations are IR pointers); pointer-to-integer casts (reinterpret_cast always; a C-style cast when its operand is this, an address or a name declared as a pointer), tree-wide, tagged by use: a key (the statement mentions a key or a _refactored call), an IR pointer, compared for identity or for order with another pointer value, hashed or reduced modulo a bucket count (alignment checks excluded), or used as an id; find_item/has_exist_in_array/is_contain/add_var_to_array_no_dup/find_expr/get_expr_idx and similar membership calls on IR pointers, the ones that test identity and then same_as (append_exprs_no_dup, ObTransformUtils::find_expr) marked.',
    'overflow': 'Proxy. Tree-wide: __builtin_*_overflow calls, raises of the overflow error codes unless their condition is a floating-point test (floating calls or constants such as DBL_MAX, or a floating literal) or a length or size comparison with no arithmetic, limit or helper, and arithmetic whose result is checked afterwards by an is_*_out_of_range(l, r, res) helper (the statement that computes res). In value and expression code (sql/engine/expr, aggregate, window_function, oblib/common/number, object, wide_integer, timezone and json_type, share/object, share/datum, query api expr and aggregate, and the storage pushdown aggregates): explicit checks (calls of overflow and out_of_range helpers and definitions of the integer ones, floating-point helpers and bare declarations excluded; limit comparisons next to arithmetic outside array subscripts) and unchecked + - * << on signed or unsigned datum or ObObj getters (indexed vector getters included), on integer locals initialized or assigned from them, or on the integer parameters of raw_op arithmetic kernels. Rust debug builds panic on signed and unsigned overflow alike.',
    'float-contraction': 'fma/fmaf/fmal, __builtin_fma and FMA intrinsics (x86 and NEON) tree-wide: the calls the Rust port maps to mul_add (PLAN section 3, item 3). The multiply-add rows are a fallback in case the -ffp-contract=off build of the C++ reference is dropped, since under it they all translate to a plain multiply and add: in number, expression, aggregate, window-function, optimizer and vector-kernel code (data_plane vector), statements (on one line or several) where a product term (no division in it) is added or subtracted in the same expression (not across a comparison, a logical operator, a ternary branch or an argument comma), or added with += or -=, and a double or float local or member, a floating literal, a double cast or get_double/get_float appears in that expression or in the parenthesized expression it sits in.',
    'lock-guards': "One row per lock declaration (RULEBOOK section 5; s8-numerics-platform.md rules 5.1, 5.7, 5.8), outside the primitives' own directory src/oblib/lib/lock, comments and strings blanked. A lock type is an OB lock primitive (ObLatch, ObLatchMutex, ObSpinLock, lib::ObMutex, ObUtilMutex, ObFutex, SpinRWLock, TCRWLock, DRWLock, ObRWLock, ObQSyncLock, ObSmallSpinLock and ObByteLock, ObPtrSpinLock, ObRowLatch, ObBucketLock, ObBucketQSyncLock, ObRecursiveMutex, ObThreadCond, ObCond, Cond, SimpleCond, LWaitCond, ObMonitor, the reader-count syncs ObQSync, ObDynamicQSync and TCRef, and the no-op ObNullLock, NLock and NCond); a pthread mutex, spin lock, read-write lock or condition; a std mutex, shared mutex or condition variable; a class derived from one of these; a class whose name ends in Lock, Latch, Mutex, Cond or Monitor and that holds a lock member or defines lock, unlock, rdlock, wrlock, try_lock or a similar operation (CtxLock, ObLSLock, MemtableMgrLock, the keybtree's own RWLock); or a typedef or using alias of any of them (an alias declared in a class applies to that class and its file; a name that is both a lock class and an alias is the class only in the class's own file stem). Rows: every field, static member, global, function static and local declared with such a type, arrays included (a declarator list gives one row per name; a declaration inside a #define body is one row per body line and the uses of the macro are not rows); every pointer or reference member or global of a lock type, which points at a lock declared elsewhere or allocated at run time; every declaration whose type is a container or pair of locks (ObLightHashMap<.., SpinRWLock>, std::pair<ObBucketLock, ObBucketLock>); and every member whose type is a template parameter when the type or the member name has a lock, latch, mutex or cond word. Each row gives the kind (mutex, read-write lock, bucket lock, reentrant mutex, condition, spin lock, reader-count sync, no-op lock, lock class), where it is declared, and the guard objects (a *Guard type, std::lock_guard, unique_lock) and lock calls (lock, unlock, rdlock, wrlock, try_*, wait, signal, pthread_*) that name it in its file stem, with the timed ones (a guard type with Timeout or Retry, or a timeout argument) and the try ones. The guarded fields, the functions called with the lock held and the nesting order are left to the classifier. Not seen: uses through an accessor (get_lock()) or through another object in the counts, locks whose type a macro pastes together, local pointers and references to locks, function parameters of lock type, and lock-like classes whose names do not end in Lock, Latch, Mutex, Cond or Monitor and do not derive from a primitive.",
    'printf-calls': 'Every call of a printf-family function or macro, comments and strings blanked, definitions and declarations excluded: the C library\'s printf, fprintf, dprintf, sprintf, snprintf, asprintf and their v forms; every function whose definitions end their parameters with a const char * format parameter (named like fmt or format) and ..., or pass a va_list after it, when at least 80% of the definitions with that name do so or the name is declared with the printf format attribute (databuff_printf, which also has template overloads that print objects: its calls count only when a string literal sits at a format position; the others are counted below); and every function-like macro that passes one of its parameters, or its variadic arguments, on as the format of such a call (BUF_PRINTF, DATA_PRINTF, _OB_LOG and the other underscore log macros, HASH_WRITE_LOG, FORWARD_USER_ERROR_MSG, the type-name printers of ob_obj_type.cpp), found to a fixed point. A call inside the body of such a macro is not a row, the macro\'s uses are; a call with a fixed format inside any other #define body is one row per body line (J_NAME, PRINT_BOUND), and that macro\'s uses are not rows. Each row gives the format as written with string literals, PRI* macros expanded to their macOS arm64 values and object-like macros whose body is a string literal (NEW_LINE) resolved, the conversions in it, the argument expressions after it, and marks: format not a literal, forwards a va_list, has %p, writes the server log (the underscore log macros, logdata_printf), records a user message (it reaches the client), writes stdout, stderr or a file, inside to_string. LOG_USER_ERROR, LOG_USER_WARN and LOG_USER_NOTE are not rows: their format is the catalog\'s, which s2-errors.md 2.7 and 2.11 check. The key-value log macros (LOG_WARN("msg", K(x))) are not printf calls. Not seen: calls through function pointers or std::function, formats built at run time or held in variables (marked format not a literal, not resolved), the argument types (the row lists expressions), and whether a buffer\'s text reaches the client, which the classifier decides.',
    'fast-parser': "Each branch of ObFastParser's lexer (src/sql/parser/ob_fast_parser.{h,cpp}: ObFastParser, ObFastParserBase, ObFastParserMysql and the macros defined in those files) that makes or selects a parameter: the innermost case label group, or if, else-if or else branch, around a statement that sets cur_token_type_ = PARAM_TOKEN, builds a node (new_node with a T_ type, add_bool_type_node, add_null_type_node, lex_store_param), assigns a T_ node type (param_type = T_INT, node->type_ = T_NUMBER), calls a function or macro with a T_ type argument (CHECK_AND_PROCESS_NUMBER(T_DOUBLE)), or calls a lexer function that does any of these, found to a fixed point (process_number, process_string, process_hex_number, process_binary, process_time_relate_type and the rest). One row per branch, at its header, with what it produces and the chain of branches around it. A branch whose own condition only tests an error or an allocation (OB_FAIL, OB_ISNULL, a NULL, OB_SUCCESS or ret comparison), or the else of such a test, is left out; T_INVALID assignments are not products. Whether the grammar makes the same constant a parameter is left to the classifier, against sql_parser_mysql_mode.y. Not seen: the identifier path reached through the process_idf_func_ member-function pointer from the default case (its own branches are rows), and literal recognition in helpers that branch on nothing.",
    'sizeof-formulas': "Proxy for s5-execution.md rule 4.9. Every sizeof in the execution code (src/sql/engine, src/sql/code_generator, src/sql/das, src/sql/dtl, src/query/api/query/engine), comments and strings blanked, whose statement names a batch size, bucket, dump, bypass, cache size, memory bound or limit, frame size, rowset, row count, partition count or shift, threshold, memory size, used, hold or data size, capacity or max size, or whose enclosing function's name says batch size, bucket, dump, bypass, memory bound, cache size, frame size, header size, memory size, memory used or partition; left out when the innermost call around it only allocates, copies, compares bytes, hashes, serializes, sets, creates, extends or resets, and in static_asserts and array dimensions. One row per sizeof. Not seen: sizes that reach a decision through a variable or member computed elsewhere (a store's mem_used_ summed per row), decision formulas outside these directories, and statements whose words the vocabulary misses.",
    'work-area-formulas': "Proxy for s5-execution.md rule 6.9, outside the work area's own files (src/sql/engine/ob_sql_mem_mgr_processor.{h,cpp}, ob_sql_memory_manager.{h,cpp}), comments and strings blanked: each call on an ObSqlMemMgrProcessor (a member, pointer or local named like mem_processor) that reports a figure to the work area: init with its cache-size argument, alloc, update_used_mem_size, update_cache_size, update_delta_used_mem_size, set_number_pass, and update_max_available_mem_size_periodically and extend_max_memory_size with the return expressions of their callbacks; and each statement that compares a work-area figure (get_data_size, get_mem_bound, get_max_bound, get_max_available_mem_size, get_expect_size, get_cache_size) with <, >, <= or >= next to a processor, a profile or a memory context: the dump decisions. The formula behind each reported value, often in a helper such as get_mem_used_size(), is the classifier's to follow. Not seen: memory accounted without a processor call, and processors reached through a name that does not say mem_processor.",
    'memmove': "Every MEMMOVE, memmove, std::memmove and __builtin_memmove call, comments and strings blanked, the MEMMOVE macro's own definitions and ob_memmove_safe left out, tagged by directory: src/sql/engine/expr (the 17 lines of s5-execution.md rule 3.5), the expression core under src/query/api/query/engine/expr (ObExpr's deep copy, the overlap ob_expr.h:1108 marks), and elsewhere. Calls in the C parser cores and in vendored zstd are rows too, with their file flags. Not seen: result writes whose source may be the destination's own buffer but that copy with MEMCPY, memcpy or a loop, which rule 3.5 also covers.",
    'frame-internals': "Lines of the operator files, the *_op.{h,cpp} and *_op_impl.{h,cpp} files under src/sql/engine (comments and strings blanked), that reach into frame internals: frames_; the frame layout members frame_idx_, datum_off_, res_buf_off_, res_buf_len_, eval_info_off_, eval_flags_off_, pvt_skip_off_, dyn_buf_header_offset_ and vector_header_off_; locate_expr_datum, locate_batch_datums, locate_datum_for_write, locate_datums_for_update, locate_param_datum and the datum-vector forms; get_evaluated_flags, get_eval_info, get_pvt_skip, get_str_res_mem, get_reset_tmp_alloc, reset_ptr_in_datum; the evaluated and projected flag setters; BatchInfoScopeGuard; the batch index and size accessors; and reinterpret_cast to ObDatum. One row per line, listing what it uses and marking the lines R05's narrower pattern matches (frames_, locate_batch_datums, locate_expr_datum, locate_datum_for_write, get_evaluated_flags, get_eval_info, get_pvt_skip, reinterpret_cast<ObDatum). Not seen: helpers outside the operator files that operators call to reach the frame (the row stores, the aggregate processor, ob_batch_eval_util.h), and frame access through local aliases of these members.",
    'storage-sql-uses': "Lines under src/storage, comments and strings blanked, that use SQL objects: every line naming the sql namespace (sql::X, oceanbase::sql::X), with the names; every using namespace sql and using sql::X line; and, in a file with such a using line, every line that names without qualification a type declared at namespace scope inside namespace sql and in no other namespace (or the X of using sql::X). R05's 1,070 lines were git grep over raw text, comments included. Not seen: SQL types reached through storage's own typedefs or templates, SQL functions and constants named without qualification, and SQL objects handed over as void pointers.",
    'batch-end-tracepoints': 'Reads of tracepoints (EventTable::EN_* through EVENT_CALL, OB_E or EVENT_CODE, and errsim points a file defines with ERRSIM_POINT_DEF and reads by name), comments and strings blanked: every read of the four tracepoints s5-execution.md rule 4.6 names (311 EN_DAS_SIMULATE_GROUP_SIZE, 1200 EN_ENABLE_NEWSORT_FORCE, 2206 EN_ENABLE_RANDOM_BATCH_SIZE, 2501 EN_CHECK_SORT_CMP), tree-wide, and their definitions in ob_tracepoint_def.h; and, in the execution side (src/sql, src/query, src/storage/access, src/storage/blocksstable, src/share/vector, src/share/aggregate), every other read used as a value or a switch when tools/deploy/init.sql sets the tracepoint, when it is read as a number (negated or through abs), or when its name says batch, row count, rowset, group size, dump, sort, hash, bypass or cache. Each row says how it is read, whether init.sql sets it, and the statement. Reads that only inject an error into ret (ret = OB_E(..), OB_FAIL(OB_E(..)), return OB_E(..)) and sets (TP_SET_EVENT) are not rows. Not seen: tracepoints whose names hide their effect and that init.sql does not set, errsim points read through another file, and effects decided far from the read.',
    'local-expr-factories': "Every ObRawExprFactory object held by value (comments and strings blanked): the local factories ObRawExprFactory x(...) of s4-sql-front.md 2.3, a declaration inside a #define body included (RESOLVE_SELECT_VIEW_STMT), and each use of such a macro; the data members that hold a factory by value (the PL resolver's proxy, the PL router, PL ASTs and cache objects, the truncate-info service, ObRTDatumArith); and factories allocated at run time (the exec context's OB_NEWx). Each row gives the constructor argument and marks a temporary memory context (CURRENT_CONTEXT). Which of the four kinds of 2.3 each is, is the classifier's. Not seen: a factory built by a raw allocation and placement new under a type alias.",
    'null-arrays': 'Places where an array of IR pointers holds NULL (s4-sql-front.md 2.5 rule 15), comments and strings blanked, outside the C parser cores: push_back(NULL or nullptr); an element set to NULL (x.at(i) = NULL, x[i] = NULL); prepare_allocate and prepare_allocate_and_keep_count, whose new slots hold NULL until set; and extend_param_exprs. The array must resolve to pointers of an IR type (the pointer-identity list, ObExpr included): a local or parameter declared as a container, C array or pointer-to-pointer of them, a member whose declared container type holds them (by class, then by file stem), a member name that holds IR pointers in at least 80% of its declarations, or a getter that returns such an array in at least 80% of its declarations. An array that resolves to other pointers is left out; in the resolver, rewrite, optimizer, code generator, printer and PL directories a push_back(NULL) or NULL store on an array that does not resolve is a row marked not resolved. Parse-tree children_ stores are left out: 1.2 rule 2 already makes a NULL child None. Not seen: arrays filled by assign or copy from an array that holds NULL, NULL passed through setters such as set_param_expr, and arrays reached through chains the resolution cannot follow.',
    'expr-factory-callers': "Every call of get_expr_factory(), comments and strings blanked, definitions and declarations excluded, in files outside src/sql/resolver, src/sql/rewrite and src/sql/optimizer (inside them RULEBOOK 2.4 makes the exec context's factory f.own()). Each row names the receiver: the exec context, an expression's own factory (2.3 rule 4), a PL AST's or package's factory, the optimizer context's, or unresolved, by the receiver's declared type when it resolves and by its name otherwise; marks a call whose result is stored into a compile context's expr_factory_ (a hand-off to a compilation); and gives the statement. Whether each runs at execution time or hands the factory to a compilation is the classifier's. Not seen: the exec context's factory used through a pointer cached earlier (ObRawExprFactory *f = ...; later uses of f), and calls inside the three compilation directories that run at execution time.",
    'warning-buffer-sites': "Every site that changes the thread's warning-buffer slot, outside src/oblib/lib/oblog/ob_warning_buffer.{h,cpp}, comments and strings blanked: calls of ob_setup_tsi_warning_buffer and ob_setup_default_tsi_warning_buffer; ObWarningBufferIgnoreScope declarations; assignments through ob_get_tsi_warning_buffer(); and calls of a small wrapper whose body makes exactly one such change and at most three statements (ObMPBase::setup_wb). Each row says whether s2-errors.md 2.6 rule 5 lists the site (matched by file name and line range, the setup_wb calls as the request entries) or not, in which case it is a new site that needs an inventory decision. ob_reset_tsi_warning_buffer, which clears the buffer the slot points to, is not a slot change. Not seen: slot changes through wrappers that do more than one thing, and code in other languages or libraries that swaps the thread-local directly.",
}
for _cat in DEFINITIONS:
    DEFINITIONS[_cat] += NOT_BUILT_NOTE


if __name__ == '__main__':
    main()
