# Phase 1c scripts (gap inventory)

Scripts that sweep sites and generate the design tables; contract in `migration/inventory/README.md`.
Each sweep set keeps its sections between its own BEGIN/END markers so that several authors can edit this file without overwriting each other.

<!-- BEGIN sweep-set S2 (units.tsv, c10 c11 c12 c13 c14 c16 c17 c18 c21 c23) -->
## Sweep set S2: shared conventions

- **Run**: `sh migration/scripts/inventory/run_sweeps_s2.sh` (make_units.py first, then each `sweep_cNN_*.py`).
  Every script is stdlib Python run with `python3 -I`, read-only on `src/` and `build_release/`; it writes
  only its `migration/inventory/sweep/<category>.tsv`, its row of `counts.tsv`, and the extra files named
  below. Two runs on the same inputs give byte-identical files (checked with sha256).
- **Library** `sweeplib.py`:
  - first-party set = the paths of `migration/depmap/nodes.tsv`, which already leaves out vendored
    third-party, generated and unbuilt files (`migration/depmap/excluded.tsv`);
  - comment/string stripping = `strip()` of `migration/feasibility/scripts/arch/strip.py` (loaded with
    `ast`, line-preserving), so file:line always refers to the original file;
  - a small brace-scope parser on the stripped text (directive lines blanked; of each `#if` only the
    first branch, of `#if 0` the `#else` branch, is kept for brace matching; all 6,509 files balance)
    names the enclosing function (`Class::method`, macro-made functions as `MACRO(args)`), the enclosing
    class, or `macro:<NAME>` for a site inside a `#define` body;
  - `write_sweep()` refuses a duplicate `(file, symbol, source_construct)` key and sorts rows by
    (file, line, symbol, construct); `update_counts()` rewrites only the caller's rows of `counts.tsv`.
- **symbol**: `<enclosing function>#<detail>` for statement-level sites, `<Class>::<member>` for data
  members; when the same symbol repeats in one file a `#2`, `#3`, … ordinal is appended in line order.
- **pretag** is a hint computed by a fixed rule (never a conclusion): `memo:N` marks the rows that the
  survey memo's original pattern finds, `broadened` the extra rows of a widened pattern, `guess:<CODE>`
  a path/name-based guess, `k=v` counts or types.
- **Blind spots common to all S2 sweeps**: macro-generated code is only seen at the macro definition or
  invocation, not at the expansion; code in `#if` branches is listed but the enclosing-function name may be
  missing in unselected branches; files that are only built on other platforms are scanned as text, while
  the link-level evidence (nm, init order) comes from the macOS arm64 `build_release` only (DEV-007).

## c10-plan-writes

- **Script**: `sweep_c10_plan_writes.py`.
- **Patterns** (stripped text):
  - A: inside every function whose parameter list has `const ObExpr &x` / `const ObExpr *x`, any
    `x.member[..] =`, `x->member =`, `x.args_[i]->member =`, `++/--`; inside `ObExpr::f(...) const`
    bodies, `args_[..] =` / `parents_[..] =` / `inner_functions_[..] =`. This reproduces the confirmed
    `ObExprColumnConv::eval_enumset` save/restore of `expr.args_[0]` (`ob_expr_column_conv.cpp:743,746`).
  - B (`memo:31`): ir-graphs memo, verbatim: `static_cast<\s*(Ob|[A-Z])\w*\s*\*\s*>\s*\(\s*expr\.extra_info_\s*\)`
    in `src/sql/engine/**/*.cpp`.
  - C (`broadened`): any non-const `static_cast`/`reinterpret_cast`/C-style cast of `<e>.extra_info_` or
    `<e>->extra_info_` in all first-party files, minus B; `cg-or-setup-fn:` / `in-codegen` mark casts in
    code-generation or setup functions.
  - D (`memo:6`): ir-graphs memo, verbatim: `const_cast<\s*(const\s+)?Ob\w*Spec\s*[*&]` in
    `src/sql/engine`, `src/sql/das`, `src/storage` (`.cpp`, `.h`).
  - E (`broadened`): `const_cast` to `ObExpr`, `ObExprFrameInfo`, `ObPhysicalPlan`, `*Spec`,
    `ObSqlExpression`, `*ExtraInfo`, all first-party files, minus D.
  - F: `mutable` data members of classes in `src/sql/engine` and `src/query/api/query/engine`.
- **symbol**: `<function>#<lhs>=` (A), `<function>#<TargetType>` (B, C), `<function>#const_cast<Type>`
  (D, E), `<Class>::<member>` (F).
- **Classification codes** (RULEBOOK 0.6 运行期写共享计划的地方):
  - `PLAN_WRITE_EXPLICIT_ARG`: a temporary rewrite of a shared expression for one call; translate by
    passing the substitute as an explicit argument (the `eval_enumset_with_arg0` pattern), `FIXED(port)`;
  - `PLAN_WRITE_PER_EXEC_COPY`: state that must live per execution; move it to the expression's
    `ExprOpCtx` / the `ObExecContext` tables (never clone `RtExpr` per row);
  - `PLAN_WRITE_ATOMIC`: a plan statistic or flag; atomic field on the shared plan;
  - `PLAN_WRITE_LOCKED`: a mutable member already guarded by its own lock (`Latch<T>` in the plan);
  - `CG_TIME_WRITE`: written only while the plan is built or deserialised (before it is shared), so a
    plain `&mut` in the builder;
  - `READ_ONLY_CAST`: the cast object is only read; translate as `&T`;
  - `UNKNOWN`.
- **Blind spots**: writes through a non-const pointer obtained in another function (e.g. a helper that
  returns `ObExpr *` from the plan) and writes through aliases of `expr` are not followed; plan writes
  outside these type names (operator specs reached via raw pointers, `ObPhysicalPlan` statistics without
  `const_cast`) are only covered by F and by the c08/c03 categories.

## c11-scopes

- **Script**: `sweep_c11_scopes.py`.
- **Patterns**:
  - A: `CREATE_WITH_TEMP_CONTEXT(` / `CREATE_WITH_TEMP_CONTEXT_P(` call sites (not `#define`);
    pretag `guess:SCOPE_REQUEST` for the 4 request-entry files (`ob_th_worker.cpp`, `obmp_query.cpp`,
    `obmp_stmt_execute.cpp`, `ob_req_queue_thread.cpp`), `guess:SCOPE_STMT_QA` when the block constructs
    an `ObRawExprFactory`/`ObStmtFactory`, else `guess:SCOPE_LOCAL_ARENA`.
  - B: function-local factories `Ob(RawExpr|Stmt|LogOperator|LogPlan)Factory x(...)|x;|x{...}`, including
    those in `#define` bodies (`macro:<NAME>`); `memo:33` marks the ir-graphs memo set
    (`ObRawExprFactory\s+\w+\s*\(` in `src/sql`, `src/pl` `.cpp`); `inside-temp-context` when it sits in an A block.
  - C: data members whose type name contains `Arena` (`ObArenaAllocator` and the other arena types,
    typedef'd `*Arena` types, `ObArenaObjPool<...>`), declared directly in a class body; pointer and
    reference members are not included (they are c04). The memo's 382 counts `ObArenaAllocator x_;` only.
    pretag: `infra-internal` (inside `lib/allocator`, `lib/alloc`), `guess:ARENA_REQUEST` (sql compile-time
    directories), `guess:ARENA_BATCH` / `guess:ARENA_CROSSCALL` (sql execution directories, by a
    `tmp|batch|row|eval|iter|cur|local|calc` member name), `guess:ARENA_CROSSCALL` elsewhere; `type:`.
  - D: every `OB_NEWx(T, pool, ...)` with its pool argument; pretag `pool-kind:` (addr-of, member,
    field-of, call, param-or-local) and `pool:`.
- **symbol**: `<function>#CREATE_WITH_TEMP_CONTEXT`, `<function>#<FactoryType>:<var>`,
  `<Class>::<member>`, `<function>#OB_NEWx(<T>,<pool>)`.
- **Classification codes** (RULEBOOK 0.6 临时上下文, 0.7, §2.4, A.3):
  - A/B: `SCOPE_REQUEST` (new request `MemCtx`), `SCOPE_STMT_QA` (`qa.scope()`, no node created inside
    is referenced from outside), `SCOPE_STMT_QA_OUTER_REF` (some nodes escape: allocate those in the outer
    scope; name them in evidence), `SCOPE_OWN_QA` (a factory with no enclosing statement, e.g. DDL code:
    its own `QueryArena`), `SCOPE_LOCAL_ARENA` (block-local `Arena`);
  - C/D: `ARENA_REQUEST` (caller-passed `&'q Arena`), `ARENA_BATCH` (batch arena of the frame module's
    store table), `ARENA_CROSSCALL` (lives across calls/requests or must be `Send`: owned chunk buffer,
    0.7 (b)), `OWNED_BOX` (D only: the pool is a long-lived/global allocator, `try_box`),
    `INFRA_REPLACED` (inside a unit replaced by `oblib::mem`), `UNKNOWN`.
- **Blind spots**: temporary contexts created through other macros/APIs (`WITH_CONTEXT`,
  `create_memory_context` + `DESTROY_CONTEXT`) are not listed; factories held by pointer or created with
  `new`/`OB_NEWx` are not B rows (D rows); arena members declared through macros or inside anonymous
  unions of macros are missed; the A/C guesses are path/name rules only.

## c12-side-channels

- **Script**: `sweep_c12_side_channels.py`.
- **Patterns**:
  - A: the ir-graphs memo method: `get_operator_kit(` calls outside `src/sql/engine/ob_exec_context.*`
    and `src/sql/engine/ob_operator.cpp` (memo: 28 on raw text; one of them,
    `ob_table_scan_with_index_back_op.cpp:43`, is inside a block comment, so 27 code sites); pretag `px`
    for `engine/px`.
  - B: operator pointers/references (`ObOperator`, `Ob*Op`) declared as data members of a class that is not
    an operator (its base clause names neither `ObOperator` nor a `*Op` class) in `src/sql`, `src/query`,
    `src/pl`; the tree links of `ObOperator` itself are excluded. This is where iterators and helpers hold
    operators (the memo's `ObSubQueryIterator::op_`, `parent_`).
- **symbol**: `<function>#get_operator_kit(<id expr>)`; `<Class>::<member>`.
- **Classification codes** (RULEBOOK 0.6 执行期):
  - `EXEC_CTX_KIT_LOOKUP`: lookup of another operator's kit/input by id; becomes an `ObExecContext` table
    access by `SpecId` (op input, SPF iterator);
  - `EXEC_CTX_OP_STATE`: operator state read or written by another operator/iterator (current group,
    batch counters, rescan parameters); move into the `SpecId`-indexed state table;
  - `OP_REF_TO_SPEC_ID`: a stored operator pointer in an iterator/helper; store the `SpecId` and reach the
    operator through `ObExecContext` (iterators must not hold operator pointers);
  - `OP_REF_CALL_ARG`: pass the operator as a call argument instead of storing it;
  - `OWNED_CHILD`: the holder owns/drives the operator as its child subtree (`Box<dyn …>`);
  - `NOT_SIDE_CHANNEL`, `UNKNOWN`.
- **Blind spots**: side channels through `void *`, through `ObExecContext` members other than the kit store,
  or through operator pointers kept in local containers are not listed.

## c13-weak

- **Script**: `sweep_c13_weak.py` (script-decided; writes the sweep and the decided shard
  `migration/inventory.c13-weak.tsv`, kit 7 columns, status `confirmed`).
- **Patterns**:
  - W: `OB_WEAK_SYMBOL` (`ob_macro_utils.h:20`, `__attribute__((weak))`) and `__attribute__((weak…))`,
    outside `#define` lines; the declarator gives the name and parameters; construct `weak definition`,
    `weak alias definition` or `weak declaration`.
  - X: hand-written prototypes at namespace or `extern "C"` scope of `.cpp/.cc/.c` files: `extern …;`
    declarations, and non-static function prototypes whose function is not defined in the same file
    (macro invocations, object definitions with constructor arguments and out-of-class member
    declarations are excluded); construct `extern function prototype` / `extern variable declaration`.
- **Decision rule** (link-level evidence: `nm -m -g` over all 365 objects of `build_release/src`, the
  same reading as `layering/weak_all.py` but per exact symbol):
  - `WEAK_OVERRIDDEN_IN_PROD`: the exact weak symbol has a strong definition in another module's object;
    target = the RULEBOOK 0.4 row for that symbol (hook / trait + slot / move down);
  - `WEAK_PLAIN`: no strong definition elsewhere (never-overridden test seams, same-module override such
    as `futex_hook`, code not compiled on the macOS baseline); target = ordinary function (RULEBOOK 0.4
    "其余约 34 处");
  - `EXTERN_PROTO`: every X row; target = delete the prototype and import the definition (§4), or the
    seam table when the definition's crate is not reachable from the caller's crate in
    `migration/depmap/crates/edges.tsv`, or the -sys/libc binding when no first-party definition exists;
  - rows in units whose disposition is not `translate` (`migration/design/units.tsv`: keep-c parser files,
    replaced oblib infrastructure) say so instead.
- **symbol**: `<qualified name>(<parameters as written>)` for functions, the name for variables.
- **Counts**: 40 weak sites (35 `OB_WEAK_SYMBOL` + 5 attribute; the layering memo's "36 + 5"), 6 of them
  overridden in production, which is RULEBOOK 0.4's list; the 134 prototypes include `.c` files and
  prototypes without `extern`, which the memo's "81 lines" did not.
- **Blind spots**: prototypes inside class or function bodies, and weak symbols created by macros other
  than `OB_WEAK_SYMBOL`, are not listed; Linux-only overrides are unknown (DEV-007).

## c14-init

- **Script**: `sweep_c14_init.py`; extra output `migration/inventory/sweep/c14-init-order.tsv`.
- **Patterns**:
  - A: `__attribute__((constructor[(p)]))` and `__attribute__((init_priority(n)))`; pretag priority and
    the position in the binary's initialiser list (`binary-init-index`) or
    `not-an-init-entry-of-the-macOS-binary` (not compiled, or constant-initialised).
  - B: dynamically initialised objects as compiled into `build_release/src/observer/seekdb`:
    1. the `__TEXT,__init_offsets` section (465 entries) gives the order in which dyld runs initialisers;
    2. the binary's debug map (N_OSO / N_FUN stabs) gives each initialiser's object file, matched to
       `build_release/src/**/*.o` by basename and mtime (members of `liboceanbase_static.a`);
    3. every instruction of the object's `_GLOBAL__sub_I*` / `__cxx_global_var_init*` functions is
       symbolised with the bundled `llvm-symbolizer --inlining`; each inlined `__cxx_global_var_init.N`
       frame with a line is one object; its first located line and column name it (variable name, the
       declaration of the enclosing array/brace initialiser, or `MACRO(args)` for macro-made objects);
    rows are first-party objects; pretag `init-seq:` (position in the order file), `tus:` (number of
    translation units that initialise their own copy, e.g. `static const` objects in headers), `ctor:`
    (the inlined constructor, which shows registrations such as `EventTable::global_item_list`).
  - C: object pools: members/globals of type `*ObjPool<...>`, `ObServerObjectPool`, `ObPooledAllocator`,
    `ObPool<...>`, `ObTabletMemtableMgrPool`, `ObIOCBPool`, `ObMemtableCtxObjPool`, `ObResourcePool`;
    uses of `sop_borrow/sop_return/op_alloc/op_alloc_args/op_free/op_reclaim_alloc/op_reclaim_free/
    rp_alloc/rp_free` (outside `lib/objectpool`); anchors for RULEBOOK 0.9's limits (`MAX_TX_CTX_COUNT`,
    `ObTxCtxFactory::alloc`, `alloc_log_cb`/`free_log_cb`, `_trx_max_log_cb_limit`).
- **c14-init-order.tsv**: `seq init_index init_function object tu var_file var_line var_symbol
  inlined_ctor note`, one row per initialiser entry and per object it initialises, in execution order;
  third-party entries (vsag, OpenBLAS, abseil, …) are listed with their object and no variables.
- **symbol**: function/variable name (A), `dyninit:<name>` (B), `<Owner>::<member>` or
  `<function>#<macro>(<arg>)` (C).
- **Classification codes** (RULEBOOK 0.4, 0.5, 0.9, §2.13):
  - `STATIC_CONST` (const-initialisable: plain `static`/`const`), `LAZY_FORCE` (non-trivial singleton:
    `static LazyLock`, forced by `main` in the c14-init-order.tsv order), `ROOT_REGISTRATION`
    (constructor with side effects such as registration: explicit call in the composition root /
    `generated::bind_all()`), `ROOT_CTOR_FN` (constructor-attribute function: explicit call at the start of
    `main`, in binary order), `POOL_REMOVED_KEEP_LIMIT` (object pool removed in the first version, count
    limit and error code kept), `NOT_AN_OBJECT_POOL`, `NOT_ON_BASELINE`, `UNKNOWN`.
- **Blind spots**: objects that only other platforms initialise dynamically; the two non-inlined
  `__cxx_global_var_init` functions keep instruction order but their call position is approximated;
  objects whose initialiser code has no line information are counted in `note=unlocated=N`; pool-like
  free lists that are not declared with these types (e.g. hand-written `ObFixedQueue` free lists) are
  missed.

## c16-dead

- **Script**: `sweep_c16_dead.py`.
- **Sites**: RULEBOOK 0.8's six dead units (`ob_bucket_qsync_lock`, `ob_delay_free_allocator`,
  `ob_map_queue_thread_pool`, `ob_ms_queue`, `ob_co_seq_queue`, `ob_ring_array`) and the definitions of
  `install_ob_signal_handler` (two platform variants) and `do_with_crash_restore` (two overloads).
- **Evidence (pretag)**: `includers` (first-party files outside the dead set including a member,
  depmap `edges.tsv`), `refs` (files outside the dead set naming a top-level class of the unit),
  `callers` (call sites outside the function's own definitions; definitions and declarations are told
  apart textually, so `#else` variants count), `bin_syms` (matching symbols in the release binary).
  All counts are 0 at the baseline.
- **Classification codes**: `DEAD_CONFIRMED` (disposition `delete`), `NOT_DEAD` (cite the live use).
- **Blind spots**: uses through macros that paste the name, and uses only compiled on other platforms.

## c17-bugs

- **Script**: `sweep_c17_bugs.py`. The site table was located by reading the code (agent investigation);
  every run checks that each anchor string is on the stated line and exits non-zero otherwise.
- **Sites**: 15 bugs of `migration/00b-judge-report.md` §6 D plus the KV cache retire condition
  (`ob_kvcache_hazard_version.cpp:75-102`) as bug 16; 1-4 sites per bug, 62 rows.
- **symbol**: `<enclosing function>#bugNN` (`#2`… for further sites in the same function); the bison
  grammar rows use the rule name. pretag: `conf:<high|medium|low>;<one-line reason>`.
- **Classification codes** (RULEBOOK 0.1.4, §5.2 #17): `BUG` (reproduce, `BUG(port): <repro>`), `FIXED`
  (reproducing would be UB in Rust: the KV cache retire, RULEBOOK 0.8, `FIXED(port)`), `NOT_CAUSAL` (the
  row is context, not the site to tag); bugs 13-15 need not be tagged (RULEBOOK §5.2).
- **Blind spots**: bugs 13-15 have low/medium confidence (13 may be client-side; 14 depends on the linked
  VSAG build; 15 is inferred from the timer logic, not traced at run time).

## c18-inplace

- **Script**: `sweep_c18_inplace.py`.
- **Patterns**: calls of `ObCharset::casedn(`/`ObCharset::caseup(` (definitions excluded; 31 calls of the
  survey's 36 matches); every `const_cast<char *>`/`const_cast<char*>` (510); calls of the in-place helpers
  `str_tolower(`, `str_toupper(` (parse_node.c), `str_to_lower(`, `easy_string_toupper/tolower(`.
- **pretag** (from the written operand: the 2nd argument of casedn/caseup, the cast operand, the 1st helper
  argument): `borrowed:parse-node` (`str_value_`/`raw_text_`), `borrowed:member` (`x_`),
  `borrowed:param(<type>)` (a parameter of the enclosing function), `local:stack-array` (`char x[N]`),
  `local:owned-buffer` (local `ObSqlString`/`ObStringBuffer`/`std::string`), `local:view` (a local
  `ObString`, whose bytes are open), `literal`.
- **symbol**: `<function>#ObCharset::casedn(<arg>)`, `<function>#const_cast<char*>(<operand>)`,
  `<function>#<helper>(<arg>)`.
- **Classification codes** (RULEBOOK §2.4 就地改写借来的字节): `OWNED_WRITE` (the writer owns the bytes:
  `&mut [u8]`), `COPY_MODIFY_REPOINT` (borrowed: copy into the same arena, modify, repoint the owner),
  `COPY_MODIFY_REPOINT_ALL_VIEWS` (also repoint other views that read the bytes later; list them),
  `NO_WRITE` (the cast only satisfies a non-const API; translate as a read), `UNKNOWN`.
- **Blind spots**: writes through `char *` obtained without a cast (non-const accessors, `ptr()` of a
  non-const `ObString`), `memcpy`/`MEMSET` into borrowed buffers, and other case helpers
  (`ObCharset::tolower` copies, so it is not listed).

## c21-parse-tree

- **Script**: `sweep_c21_parse_tree.py`; outside the parser directories (`src/sql/parser`, `src/pl/parser`,
  `src/sql/pl/parser`, `src/query/api/query/parser`), files that mention `ParseNode` (parser memo §2.3 /
  [C4] `parsenode_writes.py`).
- **Patterns**: A `const_cast<(const )?_?ParseNode…>` (39); B stack-constructed nodes `ParseNode x;`,
  `ParseNode x[N];`, `ParseNode x = …` in a function body (35); C writes to the pointer fields
  `str_value_ =`, `raw_text_ =`, `children_ =`, `children_[i] =` (pretag `receiver:`); D calls of
  `new_node`, `new_terminal_node`, `new_non_terminal_node`, `new_list_node`, `deep_copy_parse_node(_base)`,
  `push_back_child`, `push_front_child`, `append_child`, `merge_tree` (27).
- **symbol**: `<function>#const_cast<ParseNode*>(<arg>)`, `<function>#ParseNode <var>`,
  `<function>#<receiver>-><field>=`, `<function>#<call>()`.
- **Classification codes** (RULEBOOK 0.12, §2.9): `PARSE_NODE_HANDLE` (the cast only drops const:
  `ParseNode<'p>`), `PARSE_NODE_SCALAR_SETTER`, `LOCAL_PARSE_NODE` (stack node), `SETTER_SAME_ARENA`
  (pointer field set from the same parse arena), `COPY_INTO_PARSE_ARENA` (value from another source: copy
  into the parse arena, update `str_len_`, then set), `PARSE_ARENA_NEW_NODE` (construction),
  `OWNED_PARSE_TREE` (kept across statements/threads), `NOT_PARSE_NODE` (another struct's field), `UNKNOWN`.
- **Blind spots**: writes through helper functions that take `ParseNode *` (only their bodies are seen if
  they are outside the parser), and writes in files that never spell `ParseNode`.

## c23-itemtype

- **Script**: `sweep_c23_itemtype.py`; extra output `migration/inventory/sweep/c23-itemtype-tu.tsv`.
- **A, enumerator diff**: both copies (`src/objit/include/objit/common/ob_item_type.h`,
  `src/query/api/query/parser/ob_item_type.h`; `src/sql/parser/ob_item_type.h` forwards to the query copy)
  are parsed and evaluated (implicit values = previous + 1); one row per enumerator missing from one copy
  or with a different value (`T_AUTO_PARTITION`, `T_LS_ATTR_LIST`, `T_ALTER_LS`, `T_MAX`,
  `T_RESERVED_3791`, `T_FLUSH_PRIVILEGES`). `ObCacheType`, inline in objit and included from
  `share/cache/ob_cache_type.h` by the query copy, is compared with that definition (equal: no rows).
- **B, compilation units**: every command of `build_release/compile_commands.json` (363) is re-run as
  `<compiler> <its flags> -E -H -Wno-error -o /dev/null` in its build directory (preprocessing only,
  8 in parallel, about 30 s); the first copy in the `-H` trace is the effective one (shared guard
  `OCEANBASE_SQL_OB_ITEM_TYPE_H_`). `c23-itemtype-tu.tsv`: `tu effective_copy first_member include_chain
  objit_seen query_seen preprocess_rc` for all TUs (objit 23, query 166, none 174). Sweep rows: one per
  TU where objit wins, at the `#include` line that pulled the objit copy in
  (`ob_expr_operator.h:36`); pretag `uses:` lists the differing enumerators referenced anywhere in the
  TU's first-party include closure (`stmt::` names and `ob_stmt_type.h` excluded).
- **symbol**: `<Enum>::<NAME>` (A), `tu:<TU path>` (B).
- **Classification codes** (RULEBOOK 0.13): A: `GENERATE_FROM_EFFECTIVE` (the generator takes the copy the
  parser and server actually use; the other value disappears), `KEEP_AS_EXTRA_CONSTANT` (an objit-only
  value still used: emit it as an extra constant), `UNUSED_EXTRA`; B: `OBJIT_EFFECTIVE_SAFE` (uses no
  differing value), `OBJIT_EFFECTIVE_DIVERGES` (depends on a differing value, e.g. a `T_MAX` sentinel that is
  4920 here and 4921 in query TUs; name the use), `UNKNOWN`.
- **Blind spots**: the TU table is for the macOS arm64 release configuration only; the unity batching
  decides which member includes first, so other build configurations can differ.

## units.tsv (make_units.py)

- **Script**: `make_units.py` writes `migration/design/units.tsv` and
  `migration/inventory/sweep/units-review.tsv` (rows whose basis ends in `-REVIEW` or whose disposition is
  not `translate`).
- **Units**: the 3,754 depmap header groups (`migration/depmap/units/units.tsv`) merged when their targets
  coincide (36 merges: 33 `src/pl/X` + `src/sql/pl/X`, 3 `src/query/api/query/<rest>` + `src/query/<rest>`),
  plus `src/oblib/lib/charset/ob_uctype.h` (RULEBOOK 0.13 data table, excluded by the depmap as
  generated). `unit` = the representative member: a header first, the declaration-side header for merges.
- **Order**: the unit graph of `migration/depmap/units/edges.tsv` contracted by the merges; Tarjan SCCs
  dependencies first, inside an SCC by earliest file position in `migration/depmap/order.txt` (the
  depmap's own method); `batch` = floor(i/100)+1 over the `translate` rows in that order.
- **target**: RULEBOOK §4: `src/pl/<rest>` → `rust/port/sql/src/pl/<rest>`, `src/objit/include/objit/<rest>`
  → `rust/port/query/src/objit/<rest>`, `src/data_plane/api/data_plane/<rest>` → `rust/port/data_plane/src/<rest>`,
  `src/query/api/query/<rest>` → `rust/port/query/src/<rest>`, else `rust/port/<module>/src/<rest>`;
  storage core units → `rust/port/storage_core/src/<stem>.rs`; `src/observer/main` → `src/ob_main.rs`;
  path components that are Rust keywords or `lib`/`main`/`mod` get `_m` (`lib_m`, `static_m`);
  duplicate targets abort the script.
- **Disposition rules** (first match wins; `disposition_basis` = rule id):

  | basis | disposition | rule |
  |---|---|---|
  | `R-dead` | `delete` | RULEBOOK 0.8 dead units |
  | `R-itemtype` | `generate:item_type` | the two ObItemType copies and the sql/parser forwarder (0.13) |
  | `R-errno-def` | `generate:errno` | `ob_errno.def` (0.13) |
  | `R-libeasy-remnant` | `delete` | `src/oblib/easy/**` libeasy remnant headers (0.14) |
  | `R-data-map`, `R-data-def` | `data:map_table`, `data:def_table` | units made only of `.map` / `.def` (0.13) |
  | `R-data-split-REVIEW` | `translate` | named data units with code (`ob_ik_dic`, `ob_timezone_info`, `.def` + code): data part to `<crate>_data`, code translated (0.13) |
  | `R-data-charset-tab` | `data:charset_table` | `src/oblib/lib/charset/*_tab.h` (0.13) |
  | `R-data-excluded` | `data:uctype` | `ob_uctype.h` (extra row) |
  | `R-parser-c`, `R-parser-c-header` | `keep-c` | parser-directory units with a `.c` member, or header-only units reached through #include from the parser C sources (`.c`, `.y`, `.l`) (0.12) |
  | `R-parser-cxx-REVIEW` | `translate` | the other parser-directory units (C++ consumers, callback implementations) |
  | `R-storage-core` | `rewrite:storage_core` | the 11 units of RULEBOOK §4 (0.9) |
  | `R-share-cache` | `rewrite:share_cache` | KV cache hazard version/pointer/domain and map (0.8) |
  | `R-share-cache-adjacent-REVIEW` | `translate` | `ob_kvcache_struct`, `ob_kvcache_store` (KvBlockHandle) |
  | `R-datum`, `R-object` | `rewrite:datum`, `rewrite:object` | `ob_datum`, `ob_object` (§3 item 9, §2.12) |
  | `R-frame` | `rewrite:frame` | query `ob_expr` (RtExpr, eval ctx, frame access) and `ob_expr_frame_info` (0.6) |
  | `R-frame-adjacent-REVIEW` | `translate` | `src/sql/engine/expr/ob_expr` (ObExpr methods defined in sql) |
  | `R-bgk` | `keep-cxx:bgk_shim` | `ob_geo_bin_traits`, `ob_geo_tree_traits`, `ob_geo_tree` (0.14) |
  | `R-bgk-adjacent-REVIEW` | `translate` | `ob_geo_bin`, `ob_geo_bin_iter` (copied into the shim, also used by Rust) |
  | `R-infra-named:<name>` | `replace:<module>` | oblib units RULEBOOK names: `ob_smart_call`→thread, `ob_hazard_ref`→hash, `ob_qsync`→sync, `ob_sort`→sort, serialization→serialization, print utils→fmt, `context`→mem, `ob_string`/`ob_sql_string`/`ob_string_buffer`→string; `-REVIEW`: `ob_retire_station`→rewrite:storage_core, `ob_macro_utils`→prelude, `achunk_mgr`→mem |
  | `R-infra-dir:<dir>` | `replace:<module>` | `src/oblib/lib/<dir>/`: lock→sync, atomic, hash, queue, container, list→container, allocator/alloc/objectpool→mem, thread/thread_local/task→thread, oblog→log, signal |
  | `R-default-REVIEW` | `translate` | `lib/coro`, `lib/core_local`, `lib/stat/ob_latch_define` |
  | `R-data-candidate-REVIEW` | `translate` | table-like `*_tab` unit not in RULEBOOK's list (`ob_expr_to_pinyin_tab`) |
  | `R-thirdparty-shim:<libs>-REVIEW` | `translate` | units calling Boost.Geometry/Spirit, s2, vsag, `std::regex` or gRPC directly (parser memo [C6] regexes): the shim/translation boundary needs a decision (0.14) |
  | `R-default` | `translate` | everything else |

- **Blind spots**: function-level remapping (seams.tsv) is not applied; the frame/datum/object and
  share_cache sets are an identification for review, not RULEBOOK text; the parser split gives 12 keep-c
  and 13 translate rows where RULEBOOK §4 counts "解析器单元 25 行" without splitting them.
<!-- END sweep-set S2 -->

<!-- BEGIN ast_extract.py (libclang declaration database) -->
## ast_extract.py

A libclang walk over every first-party translation unit of the original release build, written as a
deduplicated declaration database. Sweeps and table generators should query it (with `astdb.py`)
instead of parsing declarations with regexes: it sees through typedefs, macros, multi-declarator
lines, default member initializers and templates, and it knows layout, vtables and overriding.

Files: `ast_extract.py` (extractor; needs libclang), `astdb.py` (query helper, stdlib only),
`ast_validate.py` (survey cross-check behind the numbers below, stdlib only). Outputs go to
`build_release/inventory-ast/` (not committed; `run` rebuilds them).

### Install (once)

```
/usr/bin/python3 -I -m venv /Users/meizhi/.claude/jobs/3985849d/tmp/venv-libclang
/Users/meizhi/.claude/jobs/3985849d/tmp/venv-libclang/bin/python -I -m pip install --disable-pip-version-check --no-cache-dir libclang==18.1.1
```

- Python 3.9.6 (`/usr/bin/python3`), pip 21.2.4; wheel `libclang-18.1.1-1-py2.py3-none-macosx_11_0_arm64.whl`
  (self-contained `clang/native/libclang.dylib`, sha256 `58ed3e93…6454`).
- The wheel has no clang builtin headers. Every TU therefore gets `-resource-dir
  deps/3rd/usr/local/oceanbase/devtools/lib/clang/17`, the resource dir of the clang 17.0.6 that built
  it, so the header search list is the real build's (`clang++ -E -v`: SDK libc++, clang-17 builtins,
  SDK `usr/include`).

### Usage

```
PY=/Users/meizhi/.claude/jobs/3985849d/tmp/venv-libclang/bin/python
$PY -I migration/scripts/inventory/ast_extract.py run --jobs 8   # parse all TUs (resumable), then merge + stats
python3 -I migration/scripts/inventory/ast_extract.py merge      # stdlib only: shards -> decls.jsonl, errors.tsv, files.tsv
python3 -I migration/scripts/inventory/ast_extract.py stats      # stdlib only: summary.json
python3 -I migration/scripts/inventory/ast_validate.py           # survey comparison (validation table below)
python3 -I migration/scripts/inventory/astdb.py fields oceanbase::common::ObString
```

Other subcommands: `list` (TU names and skip reasons), `args --tu NAME` (libclang argument vector),
`parse-one --tu NAME` (what `run` starts per TU), `probe FILE [clang args]` (debug: extract one
file under the repo and print its records; the feature tests used while writing the extractor are in
`build_release/inventory-ast/selftest/`). `run` is resumable: a TU is parsed again only if its
shard is missing, or was made with other libclang arguments or another version of the extractor
code (`args_sha`, `extractor_sha` in `tu/<tu>.meta.json`; docstring/comment edits do not count).

### Runtime (measured 2026-10-11, this Mac: 10 cores, 16 GB, other sweeps running)

- 341 TUs parsed and walked in 5.4–6.2 min with 8 workers (one large unity TU alone,
  `sql/engine/px_0`: 4.1 s parse + 3.8 s walk, peak RSS 1.2 GB per worker); merge 31 s (17.2 M shard lines to 291,323 records); stats 2 s;
  `ast_validate.py` 40 s; `astdb.AstDB()` loads everything in 3 s / 1.5 GB.
- Disk: shards 11 GB (`tu/*.jsonl`, needed only to re-merge; delete them if space matters),
  `decls.jsonl` 239 MB.

### What is parsed, and how

- **TUs**: the 363 entries of `build_release/compile_commands.json` minus the 22 whose main file is
  third-party (`zstd_src/*.c`, depmap `excluded.tsv`) = 341 (unity `.cxx`/`.c` files plus 103 single
  sources, including the bison/flex parsers and protoc output).
- **Arguments**: the entry's `command` without the compiler, `-o X`, `-c`, the input file,
  `-Werror`, `-Werror=*`, `-fcolor-diagnostics`, `-ffunction-sections`, `-fdata-sections`, `-g*`,
  `-f{debug,file,macro}-prefix-map=*`, `-M*`; plus `-resource-dir <clang 17>`, `-ferror-limit=0`,
  `-Wno-unknown-warning-option`, `-working-directory <entry dir>`. Every `-I`/`-D`/`-isysroot`/`-std`/
  `-arch`/`-march`/`-O2`/`-fopenmp` flag is kept. Nothing libclang rejected had to be dropped.
- **Parse options**: full parse. `PARSE_SKIP_FUNCTION_BODIES` was tested and rejected: with it
  `clang_isCursorDefinition()` returns 0 for every function whose body was skipped, so all function
  definitions vanish (and the full parse is cheap: 4.1 s vs 1.3 s on the largest TU; the walk never
  enters bodies either way). `KeepGoing` is not used, because it turns fatal errors into plain errors
  and would hide them. Used: `VisitImplicitAttributes` (makes `#pragma pack` visible) and
  `IgnoreNonErrorsFromIncludedFiles`.
- **File classes** (`generated` is in every record):
  - `generated: 0`: the 6,529 first-party paths of `migration/depmap/nodes.tsv`.
  - `generated: 1`: generator output compiled into the build. Translators need its types
    (`ObInnerTableSchema`, `ObSysVar*`, error codes, bison token enums) even though the generators,
    not these files, get ported (RULEBOOK §0.13), so the records are kept and flagged:
    (a) depmap `excluded.tsv` class `generated` (36 checked-in files: `ob_errno.h`,
    `ob_sys_var_class_type.h`, `ob_system_variable_factory.h`, `standbyservice.pb.*`, …);
    (b) build-time output that is not in git: the bison/flex files written into `src/sql/parser` and
    `src/pl/parser` plus `type_name.c` (13 files, listed in `merge-info.json`), `build_release/generated/**`
    (inner-table schema, sqlite virtual tables), `build_release/src/**` (`ob_version.cpp`,
    `syspack_source.cpp`). Use `generated == 0` for hand-written code only (all counts below do).
  - Not recorded: depmap `third_party`/`unbuilt` files, SDK/libc++/`deps/` headers, `rust/` and
    `build_release/rust-target/` (cxx bridges of code that is already Rust), CMake unity wrappers.

### Output files (`build_release/inventory-ast/`)

| file | content |
|---|---|
| `tu/<tu>.jsonl` | per-TU shard, one record per declaration located in a recorded file, sorted; byte-identical when re-parsed |
| `tu/<tu>.meta.json` | diagnostics by severity, first fatal/error message, timings, `files_seen`, `args_sha`, `extractor_sha`; written last, so its presence marks a complete shard |
| `decls.jsonl` | the merged database: one JSON object per line, keys sorted, ordered by (file, line, col, kind, usr) |
| `errors.tsv` | per TU: status, fatal/error/warning/note counts, record count, first fatal and first error message; skipped TUs with the reason |
| `files.tsv` | every first-party file plus every generated file seen: number of TUs that include it, number of records in it (`tus=0` = never parsed) |
| `summary.json`, `merge-info.json`, `validate.json` | counts used below; merge counters; `ast_validate.py --json` output |
| `run.log` | progress lines |

### Record schema

Key: `(usr, kind, file, line)`. One record per declaration: a class has one record per forward
declaration plus one for its definition; a method declared in its class and defined in a `.cpp` has
two records (the second with `out_of_line: 1`). Fields not applicable to a kind are omitted; flags
are 1 when present and omitted when false.

- **All records**: `kind` (class, struct, union, class_template, partial_specialization, enum,
  enum_constant, field, method, ctor, dtor, conversion, function, function_template, typedef, alias,
  var, friend), `usr` (clang's; anonymous structs/unions get `@file:line:col` appended because clang
  gives all anonymous records of one scope the same USR; a friend's usr is the befriended entity's),
  `name`, `qname` (fully qualified; class specializations with their arguments, `TT<int, 2>`;
  function template specializations as `tf<int>`; anonymous scopes as `(anonymous namespace)` and
  `(anonymous union at file:line:col)`), `file` (repo-relative), `line`, `col` (expansion location:
  for macro-made declarations the macro invocation), `access` (public/protected/private/none),
  `parent_usr`, `parent_qname` (semantic parent: the class for out-of-line members), `generated`,
  `in_macro` + `macro` (declaration produced by a macro; `macro` is the outermost macro invoked there,
  e.g. `OB_UNIS_VERSION`, `DISALLOW_COPY_AND_ASSIGN`), `is_definition`, `end_line` (records, enums and
  functions: last line of the extent, e.g. the closing brace of a class or function body).
- **Records** (class/struct/union/class_template/partial_specialization): `bases`
  `[{qname, usr, access, virtual, template_usr?, dependent?}]` (`template_usr` = primary template when
  the base is an implicit instantiation such as `ObSEArray<int64_t, 4>`, which has no record of its
  own), `is_abstract`, `has_vtable` (a virtual method in the record or any base, or a virtual base),
  `has_virtual_base`, `has_dependent_base`, `is_template`, `tag`, `template_params`
  `[{name, kind: type|non_type|template, type?}]`, `specialization_of` (USR of the template),
  `explicit_instantiation` (`template class X<int>;`), `size`/`align` (bytes, when computable: not for
  templates or incomplete types), `is_pod`, `is_final`, `aligned_attr`, `packed` + `packed_by`
  (`attribute`: `__attribute__((packed))`; `pragma`: the record's alignment is below the natural
  alignment of its members and libclang shows `#pragma pack`'s implicit attribute or the record lies
  in a `#pragma pack` region), `pragma_pack` (N of the enclosing `#pragma pack(N)` region, from a scan
  of the record's file), `has_flexible_array`, `has_bitfield`, `is_anonymous`, `is_anonymous_member`
  (`union { … };` inside a record), `n_fields` (data members incl. anonymous members).
- **Fields**: `type` (as written), `canonical_type` (fully qualified), `type_kind` (written type after
  stripping `struct`/namespace sugar: pointer, function_pointer, member_pointer, lvalue_ref, rvalue_ref,
  record, enum, typedef, builtin, constant_array, incomplete_array, array (dependent size),
  template_spec, template_param, dependent, vector, …), `canonical_kind` (same classes for the
  canonical type: a typedef'd pointer is `typedef`/`pointer`), `pointee_qname` + `pointee_kind` +
  `pointee_const` for pointers and references (canonical, unqualified pointee: the qualified record name
  for records, `void`, `char`, `int *` for `int *&`; as written for template parameters),
  `element_type`/`element_kind`/`array_len` for arrays, `is_const`, `is_mutable`, `bitwidth`,
  `offset_bits`, `field_index` (declaration order among data members), `has_init` (default member
  initializer `= …`/`{…}`; not computed for macro-made fields), `packed` (field attribute),
  `is_flexible_array` (the last field is `T[]` or `T[0]`; in a union every such member). Static data
  members are not fields: they are `var` records with `is_member: 1, is_static: 1`.
- **Functions** (function, method, ctor, dtor, conversion, function_template): `sig` (clang display
  name, `f(int, char *)`), `return_type`, `canonical_return_type`, `return_kind`, `params`
  `[{name, type, canonical_type, kind, pointee_kind?, pointee_const?, has_default?}]`, `is_const`,
  `is_static`, `is_virtual`, `is_pure`, `is_override` + `overrides` (USRs of the directly overridden
  methods, from `clang_getOverriddenCursors`, so it also works without the keyword), `override_kw`,
  `is_final`, `is_inline` (explicit or implicit), `is_deleted`, `is_defaulted` (always
  `is_definition: 1`), `is_explicit`, `is_copy_ctor`/`is_move_ctor`/`is_default_ctor`/
  `is_converting_ctor`, `is_copy_assign`/`is_move_assign`, `ref_qual`, `is_variadic`, `storage`
  (extern/static), `linkage`, `exception_spec` (explicit specs only), `weak`, `out_of_line`,
  `is_friend` (friend function defined in its class), `template_kind`, `template_params`,
  `template_args` + `specialization_of`, `explicit_instantiation`.
- **Vars**: `type`/`canonical_type`/`type_kind`/`canonical_kind` (+ pointee/array details), `is_const`,
  `is_static`, `is_member`, `is_thread_local` + `tls` (dynamic = `thread_local`, static = `__thread`),
  `storage`, `linkage`, `has_init` + `init_kind` (cursor kind of the initializer, e.g. `call_expr` for
  a constructor call), `weak`.
- **Enums**: `underlying_type`, `canonical_underlying_type`, `is_scoped`, `size`, `n_constants`;
  each constant is an `enum_constant` record with `value` and `index`.
- **Typedefs/aliases**: `underlying_type`, `canonical_type`, `type_kind`, `canonical_kind` (+ pointee/
  array details); alias templates have `is_template`, `template_params`.
- **Friends**: `friend_kind`, `friend_qname`; `usr` is the befriended entity's.
- **Added by `merge`** (from all TUs, linked by USR): `n_tu` (number of TUs that saw the record);
  `variants` (the TUs disagree; the C++ parse, then the majority, then the smallest text wins);
  functions: `has_body` (a non-deleted definition exists anywhere), `body_file`/`body_line` on
  declarations (where the definition is), `decl_file`/`decl_line` on out-of-line definitions (the
  in-class declaration); records, enums and vars: `def_file`/`def_line` on forward or `extern`/
  static-member declarations; `n_defs` when one USR has several definitions (same qualified name
  defined in different `.cpp` files).

### astdb.py

```
import sys; sys.path.insert(0, 'migration/scripts/inventory'); import astdb
db = astdb.AstDB(kinds=astdb.RECORD_KINDS + ('field',))   # load only what you need; AstDB() loads all
db.by_kind('union'); db.find('oceanbase::common::ObString'); db.get(usr); db.definition(usr)
db.children(usr, kinds=('field',)); db.bases(usr); db.ancestors(usr); db.derived(usr)   # derived: transitive
db.fields(usr, inherited=True)      # layout order, inherited first, anonymous struct/union members inlined
db.methods(usr, inherited=True)     # one record per USR; ctors/dtors not inherited, overridden ones dropped
db.overload_sets(min_size=21)       # {(parent_usr, name): [one record per USR]}
db.enclosing('src/sql/engine/px/ob_px_sqc_handler.cpp', 160)   # innermost class/function/enum at a line
```

`canonical(usr)` picks one record per function USR (the in-class or header declaration first).
`ancestors`/`derived` follow `template_usr` when a base is a template instantiation. CLI:
`python3 -I migration/scripts/inventory/astdb.py {find|fields [--inherited]|hierarchy|overloads [--min N]} …`.
Checks run: `fields(ObTableScanSpec, inherited=True)` gives 71 fields, `ObOpSpec`'s first;
`ObIAllocator` has 61 derived classes (the feasibility report counted 60 implementations); 33
overload sets have 21 or more members; `_ParseNode`'s anonymous-union bit-fields come out inline.

### Validation (final run; `decls.jsonl` sha256 `752d80d2b59eaf097a6a2fdcfcfec757c2b8ca47e3c31e50eab9ce63a62cc62f`)

**Parse errors.** `errors.tsv`: 341 TUs `ok`, 22 `skipped` (third-party main file); 0 fatal, 0 error, 0
warning diagnostics in total. No flag adjustment beyond the list above was needed.

**Determinism.** `merge` run twice: `decls.jsonl`, `errors.tsv` and `files.tsv` byte-identical
(sha256 above); three TUs re-parsed (`engine_px_0`, `sql_parser_mysql_mode_tab.c`, `share_geo_0`)
give byte-identical shards.

**Records by kind** (all / of them generated): method 154,460 / 4,721; field 39,549 / 475;
enum_constant 19,926 / 3,631; ctor 16,621 / 991; var 16,201 / 5,608; class 11,859 / 820; dtor 8,136 /
140; function 7,548 / 746; struct 5,929 / 93; function_template 3,478 / 40; typedef 3,264 / 144; enum
1,455 / 35; class_template 1,180 / 60; friend 633 / 81; alias 439 / 0; union 330 / 29;
partial_specialization 283 / 0; conversion 32 / 0. Total 291,323 (17,614 generated). 34,790
first-party records come out of macros. 284 keys have variants: C and C++ parses of the shared parser
headers (`parse_node.h`: `_Bool` vs `bool`, `enum ObItemType` vs `ObItemType`) and differently
printed dependent template names.

**First-party fields by `type_kind`** (39,074): typedef 13,571; record 6,949; pointer 6,386; builtin
6,014; template_spec 2,510; enum 1,325; lvalue_ref 1,268; constant_array 756; template_param 113;
function_pointer 91; array 73; incomplete_array 8; member_pointer 7; dependent 2; rvalue_ref 1. By
`canonical_kind`: builtin 18,075; record 10,567; pointer 6,400; enum 1,381; lvalue_ref 1,268;
constant_array 760; function_pointer 212; template_spec 170; template_param 116; array 75;
member_pointer 35; incomplete_array 8; dependent 4; vector 2; rvalue_ref 1.

**First-party layout and object-model counts** (record definitions: 13,015): with a vtable 4,835;
abstract 628; union definitions 300; bit-fields 1,034 (the conventions survey counts about 1,032);
flexible array members 73 in 72 records; packed records 59 (50 `__attribute__((packed))`, 9
`#pragma pack`; 11 records sit in `#pragma pack` regions, 2 of them without effect); records with a
virtual base 2; fields with a default member initializer 399; weak functions 40 (by USR: the 35
functions marked `OB_WEAK_SYMBOL` plus 5 written `__attribute__((weak[, noinline]))`; the weak aliases
in `malloc_hook.cpp` are not built on macOS).

**Comparison with the survey numbers** (`ast_validate.py`; *regex* = the survey's regex re-run on the
6,529 first-party files with comments and strings blanked; sites are paired by file and line span):

| construct | survey | regex | AST | AST definition | regex-only / AST-only sites |
|---|---:|---:|---:|---|---:|
| raw pointer members | 6,120 | 6,098 | 6,471 | fields of object-pointer type (6,400) or array of pointers (71) | 111 / 484 |
| `T *&` parameters | 9,454 | 9,442 | 10,508 | `(const) T *&` parameters (not `T *const &`, 121 more) in every declaration record; 5,839 by function USR, in 5,494 functions | 354 / 1,254 |
| flexible arrays | ≈70 | 76 | 73 | last field `T[]`/`T[0]`, every such member of a union | 13 / 10 |
| `ObString` members | 1,032 | 1,032 | 1,055 | canonical type `(const) oceanbase::common::ObString` or an array of it | 2 / 25 |
| reference members | 1,278 | 1,275 | 1,269 | fields of reference type | 42 / 36 |
| `void *` members | 121 | 133 | 186 | pointer fields with pointee `void` | 1 / 54 |
| virtual inheritance | 2 | 2 | 2 | record definitions with a virtual base | 0 / 0 |
| packed | ≈64 | 50 | 59 | packed record definitions | 0 / 9 |

Why they differ (counts are sites from `ast_validate.py`; the `regex` column re-runs the survey regexes
where the survey script exists: `unsafe_share.py` for raw pointers, `T *&`, flexible arrays;
`mem_census.py` for `ObString`; the reference, `void *`, virtual-inheritance and packed regexes were
reconstructed because no survey script was found):

- **Raw pointer members (+351 net).** The regex only accepts one indented `Type *name_;` per line.
  AST-only 484: 256 members whose name does not end in `_` (`ObDiagnoseInfoStruct::info_str`,
  `ob_diagnose_config.h:68`; libeasy C structs), 115 with a default member initializer
  (`bool *is_paused_{nullptr};` `ob_table_scan_param.h:81`; `ObStopWordChecker *stop_word_checker_ =
  nullptr;` `ob_fts_parser_helper.h:128`), 48 pointers spelled through a typedef (`value_ptr_t
  value_ptr_;` `ob_array_iterator.h:132`), 30 with `struct`/`mutable`/`volatile` (`struct HashNode
  *next_;` `ob_table_param.h:51`), 8 multi-declarator lines (`AChunk *prev_, *next_;`
  `alloc_struct.h:188`), 27 others such as `ObDatum *const *datums_;` (`ob_direct_insert.h:144`).
  Regex-only 111: 50 in headers no TU of this build includes (`ob_context_allocator.h:191`), 36
  statements (`return *schema_guard_;` `ob_tablet_scan.h:473`), 11 locals or members of classes
  local to a function (`ob_ctx_allocator.cpp:74`), 14 in `#define` bodies or inactive `#if` branches
  (`ob_diagnostic_info_guard.h:360` macro body; `ob_trans_define_v4.h:307` under `ENABLE_DEBUG_LOG`).
- **`T *&` parameters (+1,066).** The regex needs an upper-case type right before `*&`. Of the AST's
  10,508 parameters 9,519 have that shape; the regex misses 806 with lower-case or qualified pointee
  types (`const char *&word` `ob_fts_parser.h:122`, `void *&cache` `ob_iter_cache_api.h:29`,
  `table::ObTableLoadRowArray<…> *&` `ob_table_load_row_array_lifecycle.h:33`), 125 spelled through a
  typedef or with `__restrict` (`const uint8_t *__restrict &in` `ob_fast_bp_func.cpp:33`), 22 `T **&`,
  36 others (`ObIArray<ObString> *&` `ob_pl_type.cpp:553`). Regex-only 354 sites that are not
  parameters: 195 locals inside function bodies (`ABlock *&blist` `block_set.cpp:246`), 83 functions
  returning `T *&` (`ObTimeoutCtx *&ObTimeoutCtx::header()` `ob_timeout_ctx.cpp:60`), 35 in
  never-parsed headers, 38 parameters of function-pointer types or `ObFunction<…>`, inactive branches
  and one statement (`ob_pushdown_filter.h:559`, `ob_query_engine.h:76`), 3 friend redeclarations (the function itself is
  recorded at its own declarations). For c02, use `params[].kind == 'lvalue_ref' and pointee_kind ==
  'pointer' and not pointee_const`.
- **Flexible arrays.** The regex only matches `type name[0];`: the AST adds 8 `T[]` members (`char
  used_size_[];` `ob_json_bin.h:114`) and 2 arrays of pointers (`Segment *segs_[0];`
  `ob_ext_ring_buffer_impl.h:182`); regex-only are 11 `return x_[0];` statements (`ob_sql_define.h:282`),
  `#if 0` code (`mprotect.h:28`) and the non-built `malloc_hook.cpp:70`. `ObDecimalInt`'s five
  zero-length union members (`ob_wide_integer.h:666`) are all counted.
- **`ObString` members (+23).** 23 members without a trailing `_` (`ObParameterPrint::encode`
  `ob_multi_mode_interface.h:105`, `CtrlState::label` `ob_pl_interpreter.cpp:45`) and 2 spelled through
  `typedef ObString String` (`json::Pair::name_` `ob_json.h:76`); regex-only 2 in never-parsed headers
  (`ob_sensitive_string.h:74`, `ob_all_virtual_outline.h:55`).
- **Reference members (−9).** Regex-only 42: 20 `return &x_;` statements (`palf_env_impl.cpp:1009`), 13
  in never-parsed headers, 5 in function-local classes, 4 in `#define` bodies or `#if 0`
  (`ob_define.h:1860`, `mprotect.h:38`). AST-only 36: 21 references to pointers (`ObCacheMemBlock *&
  wash_blocks_;` `ob_kvcache_store.h:173`), 6 `volatile` (`volatile bool &stop_;`
  `ob_checksum_validator.h:133`), 6 names without `_`, 3 members made by `DEFINE_ALLOCATOR_WRAPPER`
  (`ob_mysql_result.h:1551`).
- **`void *` members (+65).** 46 members without `_`, mostly libeasy C structs (`easy_io_handler_pt::
  user_data` `easy_io_struct.h:207`), 3 with initializers, 5 others such as `CURLM *`/`CURL *` handles
  (`void` typedefs, `ob_vector_embedding_handler.h:356`) and `void *value_ CACHE_ALIGNED;`
  (`ob_darray.h:209`); regex-only 1 in a never-parsed header (`ob_ra_queue.h:46`).
- **Packed.** The survey's 64 is a plain grep that also counts zstd and comments; on first-party code
  without comments the attribute appears 50 times, all matched. The AST adds the 9 `#pragma pack(4)`/
  `(1)` records that no attribute marks (`ObXmlNode` `ob_xml_tree.h:57`, `ObLibTreeNodeBase`
  `ob_tree_base.h:45`, `ObWkbGeomPoint` `ob_geo_bin.h:35`).
- **Virtual inheritance** agrees (2).

### Known blind spots

- **One configuration**: macOS arm64 release. Code under `#if` branches not taken there is absent
  (Linux/x86-only paths, `ENABLE_DEBUG_LOG`, `__USE_AIO_FILE`, `#if 0`), and so are 81 first-party
  files no TU includes (71 headers, the 5 non-macOS `.cpp` of the depmap, 3 `.def`, 1 `.map`, 1 `.hpp`):
  `files.tsv` rows with `tus = 0`. Sweeps over those must stay regex-based.
- **Function bodies are not walked**: no local variables, lambdas, function-local statics, or classes
  defined inside functions (their members are invisible). Body-level analysis stays with the regex
  sweeps; use `end_line` and `astdb.enclosing()` to map a body line to its function or class.
- **Templates**: implicit instantiations are not records; members of class templates are recorded once
  with dependent types (no `size`/`align`/`offset_bits`); `has_vtable`/`is_abstract` of templates come
  from their own methods and resolvable bases only (`has_dependent_base` marks the rest); enum constant
  values inside templates may be meaningless.
- **Macros**: macro-made declarations are located at the (outermost) macro invocation; libclang cannot
  report a location inside the `#define` (`clang_getSpellingLocation` returns the file location), and
  `has_init` / explicit-instantiation detection are skipped for them.
- **`#pragma pack`**: libclang exposes neither the pragma nor its N (only an implicit attribute
  without a name). The extractor combines the layout test (record alignment below the natural
  alignment of its members, references counted as pointers) with a regex scan of the record's own file
  for `#pragma pack(...)` regions (`pragma_pack`). A pack region opened in another header than the
  record would only be caught by the layout test; templates in a pack region get `pragma_pack` but no
  `packed` (no layout).
- **Weak**: only the attribute forms `weak`, `__weak__`, `OB_WEAK_SYMBOL` (the only first-party macro
  that expands to it, `ob_macro_utils.h:20`) and the grpc/protobuf/absl spellings; `#pragma weak` is not
  seen (none in first-party code).
- **Layout values are this target's** (LP64, arm64; `-fmax-type-align=8` does not change layout).
- **USRs** are stable across TUs, but unrelated classes with the same qualified name in different
  `.cpp` files share one (they carry `n_defs`); members of anonymous structs/unions keep clang's USR
  (`…@Ua@FI@x`), their `parent_usr` is the record's suffixed USR.
- **Dependent spellings vary by TU** (284 `variants` keys); `canonical_type` of names in templates uses
  `type-parameter-D-I` placeholders, the written `type` keeps the parameter names.
- **libclang 18.1.1 vs the clang 17.0.6 that built the code**: no first-party code tests
  `__clang_major__`; all TUs parse without errors, so no difference was observed.
<!-- END ast_extract.py -->

<!-- BEGIN sweep-set S3a -->
## Sweep set S3a: shared conventions (c02 c03 c04 c05 c19 c20 c22, types.tsv)

- **Run**: `sh migration/scripts/inventory/run_sweeps_s3a.sh` (the seven `sweep_cNN_*.py`, then
  `make_types.py`; about 2 min). Stdlib Python, `python3 -I`, read-only on `src/`, `build_release/` and
  `migration/design/units.tsv`; each script writes only its `migration/inventory/sweep/<category>.tsv` and its
  row of `counts.tsv` (`make_types.py` writes `migration/design/types.tsv`). Two runs on the same inputs give
  byte-identical files (sha256 checked; every set is sorted before it reaches an output).
- **Inputs**: the libclang database `build_release/inventory-ast/decls.jsonl` (section `ast_extract.py`),
  queried through `astdb.py`; `files.tsv` for files no TU parses; `units.tsv` (draft) for crate and
  disposition; first-party sources stripped by `sweeplib.py` for body-level rules (function bodies are taken
  from the AST definition extents `line`..`end_line`).
- **Library** `s3alib.py` (`Model`, `Members`, helpers), shared by all S3a scripts:
  - **Sites** are first-party records only (`generated == 0`): generator output (`ob_errno.h`, inner-table
    schema, bison tables) is skipped because the generators are ported (RULEBOOK 0.13).
  - **Files no TU parses** (`files.tsv` tus = 0: 81 files, of which 77 are sources: 71 headers, 5 `.cpp` not
    built on macOS, 1 `.hpp`; the 4 `.def`/`.map` files hold no records): c02, c03, c04, c05, c20 and
    `types.tsv` add rows from a regex fallback over the sweeplib scope parser
    (class bodies, declarators), pretag `regex-only` (types.tsv: kind suffix `-regex`); c19 and c22 have no
    fallback (c19 B is the c03 AST rule; the session headers are parsed).
  - **symbol of a member**: `<Class chain>::<member>`: the enclosing records without namespaces, nested
    classes kept (`ObWindowFunctionOp::RowsStore::op_`), members of anonymous struct/union members flattened
    into the enclosing record, an unnamed record that is not such a member shown as `anon@<line>`,
    specializations with their arguments (`ObDITls<T[N], tag>`). Rule for namespaces: a namespace is added only
    when needed for uniqueness, i.e. when two rows of one file and construct would get the same key, those rows
    use the field's fully qualified name (`oceanbase::sql::X::f_`) instead (no such case at the baseline).
  - **Namespace stripping** in symbols and details (`strip_ns`): leading components that are namespaces of
    the code base are dropped (`oceanbase::common::ObSEArray<oceanbase::sql::ObRawExpr *, 4>` →
    `ObSEArray<ObRawExpr *, 4>`); class qualifiers stay; `std::__1::` → `std::`.
  - **pretag format**: `tag1,tag2;key=value;key=value`: hint tags (never contain `=`) comma-separated first,
    then `;`-separated details. A pretag that starts with `key=` has no tags (`s3alib.tags_of()` parses it).
    The AST rows of c02, c03, c04, c05, c19 and c20 add `disp=<disposition>` when the unit is not `translate`
    (`replace:`, `rewrite:`, `keep-`, `delete`, `data:`): such rows are usually decided by the
    replacement/rewrite, not translated here (types.tsv has a `disposition` column).
  - **Type sets** (derived from the database, printed by the scripts):
    - *modules* (190): pointee records of the pointer members of `ObGlobalContext` (GCTX); every `T` named in
      `server_service<T>()`, `bind_server_service<T>`, `unbind_server_service<T>`, `BIND_SERVICE(getter, T)`,
      `UNBIND_SERVICE(T)` anywhere in first-party text; records with a static `get_instance()`; an abstract
      service interface also makes its single concrete implementation a module (interfaces with several
      implementations, e.g. `ObILocalLogHandler`, do not).
    - *IR* (167): `ObRawExpr`, `ObStmt` (incl. `ObDMLStmt`), `TableItem`, `ObLogicalOperator`, `sql::Path` and all
      derived; *plan nodes* (78): `ObPhysicalPlan`, `ObOpSpec` and derived, `ObExpr`; *ObExprOperator hierarchy*
      (574); *session*: `ObSQLSessionInfo`, `ObBasicSessionInfo`.
    - *allocators* (109): `ObIAllocator` and every record derived from it, plus records named `*Alloc*`/`*Arena*`
      that define `alloc()`/`alloc_align()`/`allocate()` (PageArena, ModulePageAllocator, ObFifoArena, ...).
    - *locks* (48): the non-guard records of `src/oblib/lib/lock/` and the primitives RULEBOOK §2.7 and amendment
      #9 name (ObSmallSpinLock, ObByteLock, ObPtrSpinLock, ObRowLatch, CtxLock, ObCond, SCond, ...);
      pthread/std mutex and condition types by canonical type.
    - *infrastructure*: records of units whose disposition is `replace:*`, and `std::` types; when following
      what a member holds, only their template arguments count (containers are transparent).
  - **What a member holds** (`field_edges`): `str` (ObString by value, array, or container element), `ref`,
    `ptr` (object pointer, array of pointers, pointer as template argument), `value` (record by value, array or
    container element), `infra` (replaced infrastructure by value), `fn` (function or member pointer).
  - **Host rule** (`Members.host_of`, used by c03, c04, c19): a pointer/reference member of record O whose
    pointee record P is not O, a base or a derived record of O, and P or one of its bases holds O (or a base of
    O that is not infrastructure) by value, by pointer/reference, or as a container element.
  - **Self/sibling rule** (`Members.self_sibling`, c03, c04, c05): the member is set to `this`, to `&m` with `m`
    a by-value member of the owner (or its bases), in a constructor init list, a constructor body, or a
    default member initializer, or to a placement new into such a member (`p_ = new (buf_) T`) in any of the
    owner's functions; references: bound to `*this` or to a by-value member.
  - **Cleanup rule** (`Members.cleanup`): the owner's destructor or `destroy*/reset*/free*/clear*/release*/
    deinit*/destruct*/clean*/reuse*/fini*/uninit*` methods contain `OB_DELETE(x)(.., m)`, `ob_delete(m)`,
    `delete m`, `m->~T()`, `*free*/*destroy*/*delete*(m...)`, `op_reclaim_free/rp_free/sop_return(m)`
    (`owner-deletes`), or `m->dec_ref()/release()/revert()`, `*revert*/*dec_ref*/*release*(m)` (`owner-releases`).
  - **Scope hints** for a containing class (`Model.scope_hints`, c04, c05): `in-ir-node` (reachable by value from
    an IR record), `in-plan` (reachable by value from ObPhysicalPlan, the ObOpSpec family, ObExpr, the
    ObIExprExtraInfo family, the ObDASBaseCtDef family and every `*CtDef` record of sql/query), `in-session`
    (by value from the two session classes), `in-schema` (a record of `src/share/schema/` or reachable by value
    from one or from the ObSchema family), `long-lived-candidate` (reachable by value from a module, in-session,
    in-schema, has a lock member, or defined in `share/schema`, `sql/session`, `sql/plan_cache`,
    `storage/{meta_mem,tablet,ls,tx,tx_storage}`, `share/cache`), else `query-candidate` (in-ir-node, or defined
    in a sql/pl/query/objit directory and not in-plan); `crosses-calls` (the class or a base is named like a
    cursor, result set, iterator, scanner, operator, row/datum store, SPI or reader). "Reachable by value"
    includes the bases of every reached record.

## c02-ptr-params

- **Script**: `sweep_c02_ptr_params.py`.
- **Sites**: (A) every `(const) T *&` parameter, not `T *const &`, of a first-party function: one row per
  function USR and parameter, at the canonical declaration (in-class/header declaration first; the out-of-line
  definition is the same row), 5,839 AST + regex rows from files no TU parses; (B) functions whose canonical
  return type is an object pointer and whose body returns an allocation, directly or through a local assigned
  from one (`pointer return (allocating)`).
- **symbol**: `<Class chain>::<function>[<args>](<canonical parameter types>)[ const]#out:<i>`, `i` = 0-based
  position in the parameter list; B: `…#ret`; parameter types are canonical (typedefs resolved) with namespaces
  stripped; explicit specializations of function templates carry their arguments (`deepcopy<8>(…)`); regex rows
  use the written types; identical symbols in one file (SFINAE overloads that differ only in their
  `enable_if` conditions: 6 rows at the baseline) get `#2`, `#3` in line order (the S2 convention).
- **pretag** (from the definition body; details `type=`, `body=<file:line>`, `returns=`, `overriders=`, `macro=`,
  `disp=`): `callee-allocates:<expr>` (assigned from `x.alloc(`/`x->alloc_align(`/`OB_NEWx(T, pool)`/`OB_NEW`/
  `new (buf)` with `buf` from an allocation/`ob_malloc`/`op_alloc`/`new T`/a bare `alloc(`, directly or through a
  local; `<expr>` = receiver, pool, or function), `callee-points-existing` (a variable, member, `&x`, `x[i]`,
  `x->y`, an accessor call `get_*`/`at`/`top`/`ATOMIC_LOAD`/`server_service`...), `callee-assigns-call:<f>`
  (another call), `forwards:<f>` (passed to `f`, a function with a `T *&` parameter), `callee-advances`
  (`++`/`+=`: a cursor), `callee-resets` (only NULL), `passed-to:<f>` (only when nothing above matched),
  `no-assignment`, `no-body` (+ `pure-virtual`, `overriders=N`), `macro-body`, `unnamed`; from the pointee type:
  `ir-slot`, `plan-node`, `module`, `allocator`, `const-pointee`; B: `callee-allocates:`,
  `allocator-api` (a method of an allocator type, or `alloc*/realloc*` returning `void *`/`char *`).
- **Classification codes** (RULEBOOK §2.4 `T *&` 输出参数 / 普通指针参数, R3, §5.3):
  - `OUT_ARENA`: callee allocates from a query-scoped arena: `out: &mut Option<&'q T>` (`arena.try_alloc`);
  - `OUT_BOX`: callee allocates an owned object that the caller keeps or frees (ownership transfer):
    `out: &mut Option<Box<T>>`;
  - `OUT_BORROW`: callee points the slot at an existing object: `out: &mut Option<&'a T>` (or `&'a mut T`) with
    the lifetime of the source; `OUT_STATIC`: the source is a module: `&mut Option<&'static T>`;
  - `OUT_ARC`: callee hands out a shared/ref-counted object (handles, guards): `&mut Option<Arc<T>>`;
  - `IR_SLOT`: the slot is a compile-time IR pointer (`ObRawExpr *&`, `ObDMLStmt *&`, ...): id slot
    `&mut Option<ExprId>` etc., callers copy out, call, write back (`SLOT(port)`, R3);
  - `CURSOR`: an advancing pointer into a buffer: `&mut &[u8]` (or a position `&mut usize`);
  - `INOUT_READ`: the pointer is only read: an ordinary `Option<&T>`/`Option<&mut T>` parameter;
  - `RET_BOX` / `RET_ARENA` (B rows), `INFRA_REPLACED` (allocator internals, `disp=replace:*`), `UNKNOWN`
    (`&mut Option<Box<T>>`, §5.3).
- **Blind spots**: assignments through aliases (`T *&p2 = p; p2 = …`), macros that assign the parameter
  (bodies of macro-made functions are not visible: `macro-body`), allocation behind helper calls
  (`callee-assigns-call:<f>`, `forwards:<f>` name the next function only, not its result), function-pointer and
  template calls (`passed-to:`); `T **` out-parameters, functions returning `T *&`, and parameters of
  function-pointer types are not listed; B misses allocations returned through members or out of macros.

## c03-ptr-members

- **Script**: `sweep_c03_ptr_members.py`.
- **Sites**: fields whose canonical type is an object pointer (6,400) or an array of object pointers (71): the
  6,471 of `ast_validate.py` (`raw pointer member`, `raw pointer array member`); function pointers and arrays of
  them (214, `function pointer member`); regex rows for files no TU parses. Pointer-to-member fields (35) and
  pointers inside container members (`ObSEArray<T *>`) are not sites.
- **symbol**: `<Class chain>::<field>` (shared conventions).
- **pretag**: `ir-node`, `plan-node` (ObPhysicalPlan/ObOpSpec/ObExpr: `RtExprId`/`SpecId`, 0.6), `module`,
  `allocator` (decided in c04), `function-pointer`, `self-or-sibling`, `host-backptr`, `owner-deletes`,
  `owner-releases`, `const-pointee`, `self-type` (pointee is the owner, a base or a derived record: lists and
  trees), `void-pointee` (decided in c20), `byte-pointee` (char/uint8_t...), `template-param`, `regex-only`;
  details `pointee=`, `init=<how>:<expr>`, `host=<Pointee>(<member>)`, `frees=`/`releases=<methods>`, `type=`.
- **Classification codes** (RULEBOOK §2.4 裸指针成员, 0.6, 0.7 (c), §2.7 定时任务, §5.3; an `OPTION_` prefix
  means the field is `Option<…>` because it can be null after construction, the default for C++ pointers §2.1):
  - `ID` / `OPTION_ID`: compile-time IR (`ExprId`, `StmtId`, `TableItemId`, `LogOpId`, `PathId`) or plan
    (`RtExprId`, `SpecId`) id;
  - `REF_Q` / `OPTION_REF_Q`: query-scoped borrow `&'q T` (also `&'q [u8]` for byte buffers, `&'q Arena` for
    allocators);
  - `BOX` / `OPTION_BOX`: owned (`owner-deletes`; `Box<[u8]>` for owned byte buffers);
  - `ARC` / `OPTION_ARC`: shared ownership (ref-counted handles, `owner-releases`); `WEAK` / `OPTION_WEAK`:
    non-owning back-reference to an Arc-owned object (timer tasks, §2.7);
  - `STATIC_MODULE` / `OPTION_STATIC_MODULE`: `&'static T` of a module object (0.5);
  - `ANY_PIMPL`: `Option<Box<dyn Any + Send + Sync>>` (cross-layer pimpl, 0.4);
  - `ENUM`: the pointee is one of a closed set of types;
  - `REMOVE_HOSTPTR`: delete the field, pass the host at call time or move the state out (§2.4, c19 B);
  - `REMOVE_SELFPTR`: pointer to self or a sibling member: delete, use an accessor or a call argument
    (0.7 (c));
  - `REMOVE_ALLOC`: borrowed allocator of a long-lived/owned owner: delete (§2.4, c04);
  - `FN_PTR`: `Option<fn(..)>` / the typed `EvalFn` etc.;
  - `NONNULL_WHITELIST`: `NonNull<T>` or `Pin<Box<_>>`, only in §3 whitelist modules (storage_core, oblib
    containers/queues);
  - `UNKNOWN`: `Option<Arc<T>>`, `Option<Id>` for IR types (§5.3).
- **Blind spots**: pointers kept in containers, unions of macros, `#if` branches not built on macOS; the host
  rule sees members only (a host reached through a container of a base type or a `void *` is missed; two
  siblings pointing at each other are both tagged); `self-or-sibling` misses pointers set in non-constructor
  methods other than placement new; `owner-deletes` misses frees through helper objects or guards.

## c04-borrow-members

- **Script**: `sweep_c04_borrow_members.py`.
- **Sites**: `allocator member` (pointer or reference whose pointee is an allocator type: 363 pointers + 222
  references by AST; RULEBOOK's ≈509 counts only `ObIAllocator`), `reference member` (the other 1,047 of the
  1,269 AST references), `ObString member` (canonical type `(const) ObString` or an array of it, 1,055); regex
  rows for files no TU parses. Allocator pointers are also c03 rows (pretag `allocator`): c04 decides them and
  the c03 row cites the c04 row.
- **symbol**: `<Class chain>::<field>`.
- **pretag**: scope hints of the containing class (`in-ir-node`, `in-plan`, `in-session`, `in-schema`,
  `long-lived-candidate`, `query-candidate`, `crosses-calls`); for references and allocators also `module`,
  `ir-node`, `plan-node`, `host-backref` (host rule), `self-or-sibling` (an allocator member pointing at the
  owner's own arena: also a c05 row), `ctor-param` (initialised from a constructor parameter), `const-pointee`;
  details `pointee=`, `init=`, `host=`, `array=`, `disp=`.
- **Classification codes** (RULEBOOK §2.4, 0.5, 0.7, §5.3; conclusions also go to `types.tsv`):
  - references: `REF_Q` (`&'q T`), `STATIC_MODULE` (`&'static T`), `ID` (IR/plan node), `ARC` (`Arc<T>`),
    `REMOVE_HOSTREF` (host reference: delete, pass at call time), `REMOVE_SELFREF` (bound to self/sibling),
    `UNKNOWN` (`Arc<T>`, the "其余" choice of §2.4);
  - allocator members: `ARENA_REF_Q` (`&'q Arena`, query-scoped owner), `REMOVE_ALLOC` (owned/long-lived owner:
    delete, global heap), `SELF_ARENA` (points at the owner's own arena: decided by the c05 member row),
    `UNKNOWN`;
  - ObString members: `OBSTR_Q` (`ObStr<'q>`), `OWNED_BYTES` (`Box<[u8]>`), `UNKNOWN` (`Box<[u8]>`, §5.3).
    (Amendment #46 proposes `Option<…>` variants for members whose NULL is tested; not applied here.)
- **Blind spots**: ObString inside containers (`ObSEArray<ObString>`) and ObString pointers (c03) are not
  ObString rows; allocator types not derived from `ObIAllocator` and not named `*Alloc*`/`*Arena*` are
  reference/c03 rows; the scope hints are reachability and directory rules, not lifetimes.

## c05-self-arena

- **Script**: `sweep_c05_self_arena.py`.
- **Sites**: `self-arena class` (one row per record definition that owns an allocator: a member of an arena type
  by value or array, `lib::MemoryContext`, or a pointer to an arena type that the class itself frees or
  placement-news into its own buffer; and has members that can be allocated from it: object pointers (not
  modules, not function pointers, not the allocator pointers), ObString members, container members, or a
  constructor init list that passes the allocator member to another member or base); `member defaults to own
  allocator` (one row per pointer/reference member that a constructor or a default member initializer points at
  a by-value allocator of its own class: `allocator_(&arena_)`, `ObIAllocator *alloc_ = &inner_`,
  `alloc_(arena_)`); regex class rows for files no TU parses.
- **symbol**: `<Class chain>` (class rows), `<Class chain>::<member>` (member rows).
- **pretag**: `crosses-calls`, `long-lived`, `request-scope` (from the scope hints: long-lived-candidate →
  long-lived, else query-candidate/in-ir-node → request-scope), `unclassified`; details `arena=<members>` (`*m`:
  an owned pointer), `users=<n>(ptr=,str=,array=)`, `passes-arena=<members>`, `init=<how>:<expr>`,
  `also-external=<n>` (other constructors or methods set the member from elsewhere: the "也可改指外部分配器"
  case), `disp=`.
- **Classification codes** (RULEBOOK 0.7 自持 arena 的对象):
  - class rows: `ARENA_TO_CALLER` ((a) request-local: the arena moves to the caller's frame, the object gets
    `'q`), `OWNED_CHUNKS` ((b) crosses calls/requests or must be `Send`: `Vec<Box<[u8]>>` + `RowId`, no `&Arena`),
    `OWNED_LONG_LIVED` (long-lived object: owned `Box`/`Vec`/`Arc`, the arena and the `ObIAllocator` plumbing
    disappear), `NOT_SELF_ARENA` (the allocator is not used for the members), `UNKNOWN`;
  - member rows: `EXTERNAL_ARENA_PARAM` (always an external `&'q Arena` parameter), `OWNED_BUFFER` (owned
    buffer), `UNKNOWN`; pointers to self/siblings are c03 `REMOVE_SELFPTR` ((c)).
- **Blind spots**: allocations from the own arena through helper objects are not checked (the class/member
  pairing is the "same class has both" heuristic); arenas created on demand into a pointer without a matching
  free are missed; RULEBOOK's 288 long-lived classes are a survey count, not this rule.

## c19-multi-owner

- **Script**: `sweep_c19_multi_owner.py`.
- **Sites**: (A) `intrusive node also held elsewhere`: records deriving from an intrusive link base and also
  held by a non-intrusive container member somewhere (as template argument, by value or pointer), or by two or
  more intrusive-list members (`ObDList`, `SLL`/`DLL`/`Queue` of `ob_intrusive_list.h`, `ObLinkHashMap`), or
  deriving from two link bases. Link bases are derived from the database: records used as a base that have a
  pointer member `next_`/`prev_`/`next`/`prev`/`next_link_`/`prev_link_`/`hazard_next_`/`link_` to themselves, to
  their own hierarchy or to a template parameter, plus `LinkHashValue` (15 at the baseline: ObDLinkBase,
  ObLink, ObDLink, LinkHashValue, SLink/Link, ListNodeBase, ObITransCallback, HazardNode, ObKVCacheHazardNode,
  ObLightHashLink, ObMallocCallback, ObTagCtxBase, IObDedupTask, ObDtlChannel). (B) `host back-pointer`: every
  c03 pointer member with `host-backptr`. (C) `timer task`: records deriving from a record that declares
  `runTimerTask()` pure virtual (ObTimerTask, ObTimeWheelTask).
- **symbol**: A/C `<Class chain>`, B `<Class chain>::<field>`.
- **pretag**: A details `links=`, `in=<Holder(member)>(<Container>)` (at most 6), `n_plain=`, `n_intrusive=`;
  B `module`, `owner-deletes`, details `host=`, `pointee=`; C `module-host`, `no-host`, details
  `host=<Class>(<member>[,ptr|ref])` for each class holding a member of the task type, `backref=<member>(<pointee>
  [,host])`, `bases=`.
- **Classification codes** (RULEBOOK §2.7 队列项 / 定时任务, §2.5 ObDList, §2.4 宿主回指):
  - A: `SLAB_OWNER_IDS` (one owner slab, containers store ids), `ARC_OWNER_CLONES` (Arc owner, containers store
    Arc clones), `ARENA_ID_LIST` (arena list chained by ids), `PLAN_VEC` (read-only plan: `Vec`),
    `SINGLE_OWNER_INTRUSIVE` (really one container at a time: oblib intrusive list, §3 whitelist), `UNKNOWN`;
  - B: `HOST_PARAM` (delete the field; `fn f(&mut self, host: &Host)`), `HOST_STATE_BY_ID` (move the visited
    state out of the host; both sides use ids / ObExecContext tables), `HOST_STATIC` (host is a module:
    `&'static Host`), `HOST_WEAK` (host is Arc-owned and outlives calls: `Weak<Host>`), `NOT_HOSTPTR`,
    `UNKNOWN`;
  - C: `TIMER_STATIC_HOST` (task holds `&'static` host), `TIMER_WEAK_HOST` (host not a module: `Weak`, built with
    `Arc::new_cyclic`, upgraded per run), `TIMER_NO_HOST`, `UNKNOWN`; registration is the RULEBOOK §2.7
    `Arc<dyn TimerTask>` row (amendment #20 queues the receiver form).
- **Blind spots**: non-template queues (`ObSpLinkQueue`, `ObLinkQueue`, `ObFixedQueue` of `void *`) cannot show
  which record they hold; objects held by two containers through `void *` or by local containers are missed;
  task bases other than `runTimerTask` (ObAsyncTask, IObDedupTask, dag ObITask: copied into or owned by their
  queue) are not C rows; host back-references through reference members are c04 `host-backref`.

## c20-vinherit-voidptr

- **Script**: `sweep_c20_vinherit_voidptr.py`.
- **Sites**: the 2 virtual base specifiers (`virtual base specifier`); every member of type (cv) `void *` or an
  array of them (`void * member`, 186 + 6 arrays by AST) and regex rows. void * members are also c03 rows
  (`void-pointee`): c20 decides them.
- **symbol**: `<Derived>#virtual:<Base>`; `<Class chain>::<field>`.
- **pretag** (evidence: every first-party `static_cast`/`reinterpret_cast`/`const_cast<T *>(operand)` and C-style
  `(T *)operand` whose operand ends in the member name, counted when the operand is `x.m`/`x->m` in a file that
  names the owner, or a bare `m` inside a function of the owner or a file of its unit): `pimpl` (a cast to a
  record of a crate the owner's crate cannot depend on, or a cast site in such a crate; RULEBOOK 0.3 graph),
  `buffer` (every cast is to a byte/scalar pointer, or no cast and a buffer-like name that is not handle-like),
  `opaque-handle` (the rest); details `casts=`, `to=`, `cast-in=<crate>:<file>:<line>`; virtual bases:
  `siblings=`, `diamond=`.
- **Classification codes** (RULEBOOK 0.4 跨层 pimpl, §2.13 多重继承):
  - virtual base: `VBASE_SPLIT_TRAITS` (split into traits; the shared base's data held once by the most-derived
    struct, accessed through the trait), `UNKNOWN`;
  - void *: `ANY_PIMPL` (`Option<Box<dyn Any + Send + Sync>>`; the upper layer `downcast_mut`s), `TYPED` (one
    pointee type: use that type's c03 translation), `ENUM` (closed set of pointee types), `BYTES`
    (`&'q [u8]`/`Box<[u8]>`/an offset), `FFI_HANDLE` (C library or callback handle kept as a raw pointer inside an
    -sys/shim module, §3), `GENERIC` (a type parameter), `UNKNOWN`.
- **Blind spots**: casts through intermediate variables (`void *p = x.m; static_cast<T *>(p)`) and through
  helper functions are not followed; same-named members of other classes in the owner's unit can add casts.

## c22-session

- **Script**: `sweep_c22_session.py`.
- **Sites**: every data member of `ObSQLSessionInfo` and `ObBasicSessionInfo` (`session field`, 174) and of the
  records declared in the same two headers that they hold by value, pointer, array or container element, or as
  a base of such a record, transitively (`session member-struct field`; 13 records: MultiThreadData,
  SysVarsCache, SysVarsCacheData, TransFlags, CursorCache, ...).
- **symbol**: `<Class chain>::<field>`.
- **pretag**: `cross-thread` (the field, or a small accessor of it, i.e. a session method whose body references
  at most 3 session fields, excluding constructors and generic names such as reset/to_string/serialize/assign,
  is referenced from `src/sql/session/ob_sql_session_mgr.*`, `src/observer/virtual_table/ob_show_processlist.*`,
  `src/observer/virtual_table/ob_all_virtual_*session*`, `src/sql/engine/cmd/ob_kill_*`, or the functions with
  `kill` in their name in `src/sql/ob_sql_trans_control.*`), `under-query-mutex` / `under-thread-data-mutex`
  (referenced in a session method or a cross-thread function that takes `query_mutex_`/`get_query_lock()` or
  `thread_data_mutex_`/`get_thread_data_lock()`), `atomic` (`ATOMIC_*` on the name in the session or
  cross-thread files, or std::atomic/volatile type), `in-thread-data` (a MultiThreadData field), `lock`; details
  `type=`, `accessors=`, `xt=<first cross-thread reference>`.
- **Classification codes** (RULEBOOK 0.5 会话, §2.7 重入): `QUERY_LATCH` (only the worker holding `query_mutex_`:
  into the request's `Latch<…>`, guard passed down), `THREAD_DATA_LATCH` (read by other threads under
  `thread_data_mutex_`: its own `Latch`), `ATOMIC` (scalar read/written by other threads), `ARCSWAP` (non-scalar
  read by other threads: `ArcSwap` snapshot), `TX_DESC_SLOT` (`tx_desc_`: `ArcSwapOption<ObTxDesc>`),
  `INIT_ONLY` (set before the session is published), `UNKNOWN` (scalar → atomic, other → in the lock, §2.7).
- **Blind spots**: accessors that touch more than 3 fields, calls through other types (e.g. `ObSQLSessionMgr`
  helpers in other files), and locks taken by callers higher up the stack are not seen; a generic accessor name
  shared with another type in a cross-thread file can mark a field `cross-thread`.

## types.tsv (make_types.py)

- **Script**: `make_types.py` writes `migration/design/types.tsv` (RULEBOOK §5.1), header
  `class file line kind rust_struct rust_trait bases base_fields isa_set lifetime_candidates lifetime_class shared
  disposition guard_methods`; one row per first-party record definition (12,992 AST rows: classes, structs,
  unions, class templates, partial and explicit specializations, anonymous members; forward declarations and the
  23 explicit instantiations skipped) plus regex rows for files no TU parses (kind `class-regex`/`struct-regex`/
  `class_template-regex`: name, `Virt` trait when the body has `virtual`, bases and disposition only).
- **class**: fully qualified name; **kind**: AST kind, `explicit_specialization` for full specializations,
  `:anonymous-member`/`:anonymous` for unnamed records.
- **rust_struct / rust_trait** (§2.13): no vtable → struct only; vtable and data (own or inherited, or a
  dependent base with fields) → struct `<name>` + trait `<name>Virt`; vtable without data → trait `<name>` only;
  ObExprOperator hierarchy → `ExprOp/OpState (0.6)` in both; anonymous records → empty. `<name>` is the
  innermost C++ name (specialization arguments kept). Open item for the name tables: 22 names collide inside one
  unit (110 records, mostly nested classes such as `ObLSPersistentState::State`/`ObLSRunningState::State`);
  templates and their specializations share a name by design (one generic struct, per-type impls).
- **bases**: `;`-separated, namespaces stripped, `virtual ` prefix; **base_fields**: `base=<B>` for the first base
  with data (own or inherited fields; an unresolvable dependent base counts as data), `base_<snake>=<B>` for the
  others (`snake` of the base's template name: `ObINodeWithChild` → `base_ob_i_node_with_child`).
- **isa_set**: `as_<variant>=<record>;<derived>;…` for every record of a downcast hierarchy: the fixed RawExpr
  (ObRawExpr), Stmt (ObStmt), LogOp (ObLogicalOperator), Path (sql::Path), OpSpec (ObOpSpec), ExtraInfo
  (ObIExprExtraInfo) and ExprOpCtx (ObExprOperatorCtx), plus every first-party hierarchy whose root has an
  enum member (a tag), at least 2 derived records, and at least 10 `static_cast`/`dynamic_cast<D *|&>` sites
  into it (23 more at the baseline: ObNewRowIterator, ObPLStmt, ObDASBaseCtDef/RtDef, ObHint, ObILibCacheObject,
  ...). Variant = class name minus `Ob`, minus the hierarchy affix (RawExpr/Stmt/Spec/Path/ExtraInfo/Ctx suffix,
  `Log` prefix for LogOp), snake case (§2.13; Path/ExtraInfo/Ctx affixes are this script's extension).
- **lifetime_candidates** (a closure, not a conclusion): own members that hold ObString (`str`), a reference
  (`ref`), a raw pointer (`ptr`) or an allocator pointer/reference (`alloc`), where pointers and references to
  modules (`&'static`), IR records and plan nodes (ids, 0.6) and function pointers do not count; members held by
  value or as container element whose record is itself a candidate (`via:<Record>`); bases that are candidates
  (`base(via:B)`, `base_<snake>(via:B)`). Containers and other replaced infrastructure are transparent.
- **lifetime_class** (only mechanical rules; the reason follows in parentheses): `long-lived (module)`,
  `long-lived (session, 0.5/0.7)`, `plan (0.6 runtime plan)` for the plan nodes, `query (0.6 QueryArena IR
  node)` for IR records, `query (0.6 OpState on the RawExpr node)` for the ObExprOperator hierarchy; empty
  elsewhere (§5.3 default: long-lived).
- **shared**: `yes (…)`: module (GCTX/server_service/get_instance), session Arc (0.5), the RULEBOOK 0.9 Arc types
  (ObTransCtx, ObPartTransCtx, ObTxDesc, ObOBJLock, ObTxData, ObTablet, ObITable, ObSSTable, ObMemtable,
  ObStorageMetaMemMgr and derived), lock members, atomic members (std::atomic/volatile, or the member name in an
  `ATOMIC_*` of the unit's files), singleton statics (a non-const static of its own type in the class or in one
  of its methods). `candidate (…)`: other non-const statics (AST namespace-scope/static members; function-local
  statics by regex), refcounted (`inc_ref`/`dec_ref`/... methods or a `ref_cnt_` member), timer task,
  ObLinkHashMap value (`LinkHashValue`), thread/pool class, ObPhysicalPlan, direct by-value member of or pointed
  to by a module. Statics named ALL_CAPS (constants by convention) and const statics give no signal. Empty:
  no signal (§5.3 default: shared).
- **disposition**: `units.tsv` disposition of the definition file; **guard_methods**: empty (c07).
- **Blind spots**: records defined inside function bodies (not in the database); implicit template
  instantiations; the `-regex` rows; the lifetime closure ignores what a pointer member points at (a `ptr`
  trigger may be an owned `Box`); `shared` misses objects shared only through `Arc`s created at run time and
  types accessed across threads without locks (c08 lists those fields).
<!-- END sweep-set S3a -->

<!-- BEGIN seams S4 -->
## seams.tsv (sweep set S4: make_seams.py)

- **Run**: `sh migration/scripts/inventory/run_seams.sh` (= `python3 -I migration/scripts/inventory/make_seams.py`),
  about 30 s, stdlib only. Reads `build_release/src/**/*.o` (`nm -m -g`, `c++filt`), the AST database
  (`astdb.py`), `migration/design/units.tsv`, the depmap (`symbols/skipped.tsv`, `crates/union_cycles.txt`,
  `crates/edges.tsv`, file-level `edges.tsv`) and the c13 outputs. Writes `migration/design/seams.tsv`,
  `migration/inventory/sweep/seams-summary.tsv` (rows per `decl_crate, def_crate, kind, proposed_split`) and the
  `seams` row of `counts.tsv`; prints the reconciliation below. Two runs give byte-identical outputs (sha256).
- **Crate graph**: RULEBOOK 0.3's table, transitive (`CRATE_DEPS`): oblib; storage_core, share → oblib; query →
  share; logservice → share, storage_core; data_plane → query; parser → query; sql → data_plane, parser; storage →
  data_plane, logservice, storage_core; standby → storage; rootserver, observer → everything above. The script
  exits if a depmap include-level crate edge (pl → sql, objit → query applied) is not allowed. A file's crate is its
  unit's `crate` in units.tsv (`src/pl` is sql, the 11 storage-core units are storage_core), else `src/<module>`.
- **Populations** (`evidence` `pop=`; a row may carry several):
  - `link`: every symbol on the depmap's 12 against + 3 across module edges (`union_cycles.txt`), recomputed from nm
    as `symdeps_seekdb.py` does. The script exits unless symdeps' own line reader reproduces every edge count of the
    depmap; the rows use a reader that also parses `external [no dead strip] <name>` lines. One row per distinct
    symbol, matched to AST records by qualified name and parameter spelling (`ast=exact`,
    `exact-modulo-template-args`: clang drops default template arguments that c++filt prints; `class` for
    vtable/typeinfo/static members). All 707 symbols match.
  - `body`: every out-of-line member definition (method, constructor, destructor, conversion, member template,
    static data member) whose file is in another crate than its class. Rust allows an inherent impl, `Drop` and a
    foreign-trait impl only in the type's crate, whether or not a lower crate calls the member. This population
    contains RULEBOOK 0.4's out-of-module destructors and serialize/TO_STRING bodies.
  - `fwd`: one row per (record T, lower crate L): T is named in the canonical type of a field, variable, parameter,
    return, typedef/alias, or in the arguments of an explicit template specialization, of a declaration located in
    L; every definition of T is in crates L cannot reach; a forward declaration of T is visible to L (in L or below
    it). Records defined only in `.cpp` files are ignored (template parameters spelled like them). Upper types that
    L names with no visible forward declaration (complete types through upward includes) are printed, not rows; only
    storage_core has any.
  - `proto`: c13 `EXTERN_PROTO` rows whose AST declaration has definitions only in crates the caller's crate cannot
    reach (c13's own reachability used the module include DAG; this uses the 0.3 graph).
- **Columns**:
  - `seam_id` `S-NNNN` in plain string order of (`decl_crate`, `def_crate`, `symbol`), ties by kind, decl, def.
  - `symbol`: the c++filt name for link rows; AST-only rows use the same style built from the AST (qualified
    name, canonical parameter types as `T const&`); fwd rows the record's qualified name.
  - `kind`: function, method, ctor, dtor, var (static data member), vtable, typeinfo, fwd-type, impl-body
    (`serialize`, `deserialize`, `get_serialize_size` and their `*_` unis helpers, `to_string`, comparison
    operators, `hash`: bodies of foreign traits ObSerialize, ObToString, PartialEq/PartialOrd, ObHash).
  - `decl_file:decl_line`: members: the in-class declaration; free functions: the declaration callers see (lowest
    crate, there a header before a hand-written `.cpp` prototype before a weak default, which `weak-default=`
    names); vtable/typeinfo: the class definition; fwd: the forward declaration in L, else the first use in L
    (`fwd-decl=` then names the declaration L reaches).
  - `def_file:def_line`: the definition compiled into the defining object (link) or the out-of-line definition
    (body); vtable/typeinfo: the key function's definition; fwd: T's definition.
  - `decl_crate`/`def_crate`: crate of the class definition (members) or of the declaration (functions) / of the
    definition. In every row except `merge`, `decl_crate` cannot reach `def_crate`.
  - `callers`: link rows: `N: files`: sources of the referencing objects in crates that cannot reach `def_crate`,
    and headers of their modules, that name the symbol (class and member name); `N via-include: files` when only
    sources that include the declaring header (directly or transitively, depmap edges) are found: wrapper
    templates (`ex_rpc::sync_call<>`), implicit constructor/destructor/serialization calls. `0`: no caller outside
    the definition crate's reach (merge rows; body rows, which no lower crate references). fwd: `N uses: file:line`.
  - `rust_landing`: `move-down`, `impl-in-type-crate`, `pimpl-any`: target `.rs` of the unit owning the type
    (units.tsv `target`); `move-type-down`: the lower unit; `trait-slot`, `handle-trait`, `opaque-handle`: module
    path `<crate>::<module>` of the declaring unit, plus the trait name where RULEBOOK 0.4 gives one
    (`InnerConnFactory`, `TaskDispatcher`, `VectorIndexUtil`, `VectorIndexAdaptor`, `InnerSqlAccess`,
    `TabletReport`, `DdlCoordinator`, `PushdownFilter`); `merge`: the merged sql unit.
  - `evidence` keys: `pop`; link: `edges` (depmap module edges), `nm-def` (defining objects), `nm-flags`,
    `symdeps=dropped-by-symdeps-reader`, `ast`, `usr`, `weak-default`, `proto`; members and functions:
    `body-upper` (names in the definition that only upper crates declare), `movable=no(calls-unmovable:X::f)`,
    `opaque` (opaque parameter types), `pimpl-fields`, `rule` (the decision below), `note`, `type-lower-files`
    (files below `def_crate`, other than the class header, naming the class); fwd: `uses` (member/spec/signature
    sites), `layout-upper` (T's fields/bases L cannot reach), `upper-methods` (T's out-of-line methods),
    `fwd-decl`, `also-defined-in`; proto: `c13`, `calls`.
- **shape** (descriptive; first match):
  - `per-row-path`: `das_scan_next_row(s)`, `spill_row_store_*` (RULEBOOK 0.4's known per-row symbols);
  - `dtor-pimpl`: destructor whose body, or a reset/destroy/accessor (`ObTxExecResultAccess::destroy`) it calls,
    touches a pimpl field: a `void *` or a pointer to a record the class's crate cannot complete;
  - `foreign-trait-body`: any other destructor (`Drop`) and every `impl-body` row;
  - `value-type-body`: member (or vtable/typeinfo) whose body is movable (below);
  - `handle-object`: free function with a pointer/reference parameter to an opaque record (defined only above,
    or never defined such as `ObFTDocWordIterator`) unless its body reaches a process service (then
    `service-singleton`: `server_service<>`, `GCTX.`, `OBSERVER.`, `get_instance()`, `MTL(`,
    `get_*{manager,service,mgr}()`); member whose body touches a pimpl field; vtable/typeinfo of a class with
    unmovable virtual bodies;
  - `service-singleton`: other functions and static members whose body reaches a process service;
  - `method-on-lower-type`: other members (and merge members); `free-fn`: other functions;
  - fwd rows: `fwd-type-member` (a field/variable use), else `template-specialization` (uses in explicit
    specializations or their members), else `fwd-type-signature`.
- **Movable** (`rule=body-lower-only`): the definition text (comments/strings stripped, signature included,
  static-member initializers up to their `;`) names nothing whose every declaration lies in crates `decl_crate`
  cannot reach (qualified tokens resolved by qualified name, unqualified ones by kind precedence types > macros >
  functions > variables > enum constants), has no opaque parameter, and, to a fixed point, calls no seam member
  that is itself unmovable (unqualified/`this->` calls of the own class, `X::f(`, `v.f(`/`v->f(` with `v` a
  parameter, field or local of known record type). A vtable/typeinfo moves only with all its virtual bodies.
- **proposed_split** (first match; `rule=` says which):
  1. `merge`: link symbol left with no caller outside the definition crate's reach once pl is sql (old sql → pl);
  2. destructor: `pimpl-any` for `dtor-pimpl`, else `impl-in-type-crate` (Drop in the type's crate; upper needs go
     through a slot hook); `impl-body`: `impl-in-type-crate`;
  3. fwd: `template-specialization` → `impl-in-type-crate`; T's own fields and bases name only types L reaches →
     `move-type-down`; T is the opaque parameter of a handle-trait seam declared in L or below → `handle-trait`;
     a member use → `opaque-handle`; else `handle-trait`;
  4. RULEBOOK 0.4 per-edge table, by symbol or class: `ob_strerror`, `get_cpu_count`, `print_service_status`,
     `ObSqlEndTransCb` (to sql), `ObReqTimeInfo`/`ObGlobalReqTimeService` (to query) → `move-down`;
     `allow_next_syslog`, `current_resource_owner_id`, `request_finish_callback` (`OnceLock<fn>` hooks),
     `create_inner_sql_connection_for_proxy`, `ex_rpc::*`, `ObInnerSQLConnectionAccess`, the tablet-report and
     DDL-coordinator functions → `trait-slot`;
  5. `per-row-path` → `handle-trait`;
  6. movable → `move-down` (vector-index helpers "只依赖 schema 和参数", value-type bodies of 0.4);
  7. definition under `src/observer/vector_index/` → `trait-slot` (query traits);
  8. declared in `query/.../ob_pushdown_filter.h` or `ob_expr.h`, defined in sql → `handle-trait`
     (0.6 `PushdownFilter`);
  9. `handle-object` → `handle-trait`; everything else (`service-singleton`, `method-on-lower-type`, `free-fn`) →
     `trait-slot`.
- **Reconciliation with the depmap's 741** (stdout of each run):

  | | pairs |
  |---|---|
  | 12 against edges, symdeps reader / this reader | 364 / 365 (+1 sql → observer `lib_get_cpu_khz`: `external [no dead strip]`, `__attribute__((used))`, `ob_server.cpp:384`) |
  | 3 across edges | 377 / 377 |
  | total | 741 / 742 |
  | distinct symbols | 707: 677 on one edge, 27 on two, 1 on three, 2 on four (35 repeats: ref + weak_override of the same oblib → share symbol; `ex_rpc::*`, `ObReqTimeInfo`, vector-index helpers referenced from several modules) |
  | resolved by the pl → sql merge | 152 rows, `merge` (all of old sql → pl) |
  | cross-crate after the mapping | 555 symbols: sql → storage 271 (pl's 7 are all also sql's), sql → observer 131 (127 + 10 from pl, 6 shared), storage → sql 99, storage → observer 33, rootserver → observer 27, oblib → share 4, storage → rootserver 4, share → observer 2, oblib → observer 1, oblib → storage 1, sql → rootserver 1 (a symbol with callers in two crates counts in both: 574) |

  Link rows: 707 = 310 link only + 396 also `body` + 1 also `proto`.
- **Other RULEBOOK 0.4 numbers**: destructors outside the class's module "54": the AST finds 66 by `src/<module>`,
  13 of them sql ↔ pl, so 53 cross-crate (data_plane → storage 26, query → sql 16, query → observer 8,
  data_plane → observer 2, sql → observer 1): 36 `impl-in-type-crate`, 17 `pimpl-any`. Serialize/TO_STRING bodies
  "67" (a regex count of review S-20, script not in the repo): 331 impl-body rows at 134 definition sites in 79
  classes, i.e. 56 ObSerialize and 26 ObToString impls, plus 4 PartialEq and 4 ObHash. Forward-declared upward
  types "273": `layering/fwd_used.py` re-run gives 279 (module, type) pairs / 273 names; this gives 287 rows:
  −11 data_plane → query (allowed by the new edge), −7 friend-only declarations (`ObTransService`,
  `ObAggregateProcessor`, `ObAllVirtualMemoryInfo`, `ObDecintCmp`, `ObVectorStore`, `ObAllVirtualSqlPlan`,
  `ObGVSql`), −1 short-name collision (oblib's own `ObRowStore::BlockInfo`); +6 query → data_plane (upward under the
  new edge), +7 storage → sql (siblings now), +11 storage_core (new crate), +2 one-line `namespace x { class T; }`
  declarations the regex misses (oblib `Worker::session_`, sql `ObPxPool`), +1 use in a `.cpp` (sql
  `ObIMicroBlockRowScanner`). c13: `lib_get_cpu_khz` is the link row above; `get_max_schema_slot_num_for_add_schema`
  (share prototype, never called, body reads config only) is the one `proto` row; `free`/`realloc` in
  `malloc_hook_extended.cpp` are not seams (c13 matched same-named member functions; the prototypes name libc).
- **Findings for the reviewers**:
  - 1,698 `body`-only rows are not in RULEBOOK 0.4's 741/54/67: members of lower-crate classes (mostly api-header
    classes) compiled in an upper crate with no lower caller, e.g. the data_plane scheduler (`ObDagScheduler`,
    `ObIDag`, … 275 rows), the query expression operators (396 rows in `query/.../engine/expr` headers), vector
    index (335 rows). 991 member rows in 168 classes have `type-lower-files=0`: nothing below the body crate
    names the class, so remapping the class's header unit to the body crate would remove the seam; that is not a
    0.4 method (amendment candidate).
  - The 0.3 graph creates upward uses the link data cannot show: query → data_plane (6 fwd types in
    `ob_pushdown_filter.h`, `ob_das_iter_access.h`, `ob_vector_index_util.h`), storage → sql (7), and storage_core,
    whose rewrite units name 23 upper types through includes (`palf::LSN` 56 uses, `share::SCN`, `ObTransID`,
    `ObTxSEQ`, `ObStoreCtx`, …) while 0.3 gives it only oblib; 15 members of its classes (`ObITransCallback` 10,
    `ObMemstoreAllocator` 3, memstore info loggers 2) are compiled in storage.
  - RULEBOOK's `print_service_status` move-down: its body guards on
    `server_service<omt::ObServerRuntimeController>()` (`body-upper`), an observer type.
- **Blind spots**: link data is the macOS arm64 release build only (DEV-007): the nm reader is Mach-O `nm -m`
  (the script exits elsewhere) and a Linux re-run is pending; references are per object, and unity objects mix
  sources, so storage_core sources' references to storage/share cannot be told apart from storage's (only their
  signatures and fields are checked); objects the depmap skips (generated parsers, inner-table schema, syspack,
  `ob_version`) are not read. Body analysis is by name: receivers of unknown type, macros that expand to upper
  calls, function pointers and ADL are not followed, and an ambiguous short name counts as lower, so `move-down`
  is optimistic. The AST covers this configuration's TUs only (81 files never parsed). `callers` is name-based
  (`via-include` over-approximates). fwd uses inside function bodies are not seen; friend declarations are ignored
  on purpose. `proto` covers c13's `EXTERN_PROTO` rows only.
<!-- END seams S4 -->

<!-- BEGIN sweep-set S3b -->
## Sweep set S3b: shared conventions (c06 c07 c08 c09 c15, overloads.tsv slots.tsv qa_signatures.tsv symbols.tsv)

- **Run**: `sh migration/scripts/inventory/run_sweeps_s3b.sh` (about 2.5 min; stdlib `python3 -I`; read-only on
  `src/` and `build_release/`). Inputs: `build_release/inventory-ast/decls.jsonl` (+ `files.tsv`),
  `migration/design/units.tsv`, `migration/depmap/nodes.tsv`. Outputs: `migration/inventory/sweep/{c06-int-returns,
  c07-locks,c08-atomics,c09-layout,c15-overloads}.tsv`, their `counts.tsv` rows, the decided shard
  `migration/inventory.c06-int-returns.tsv`, and `migration/design/{overloads,slots,qa_signatures,symbols}.tsv`.
  Determinism: two full runs (separate processes, so different string-hash seeds) gave byte-identical files
  (sha256 compared for all ten files and the five `counts.tsv` rows, 2026-10-12).
- **Library** `s3blib.py` (on top of `sweeplib.py` and `astdb.py`):
  - **line-preserving stripper**: the feasibility `strip()` that `sweeplib.stripped()` uses drops the newline of a
    backslash-newline inside a string or character literal and the newline that ends an unterminated quote, so 16
    first-party files lose 1-119 lines (`src/sql/optimizer/stat/ob_dbms_stats_history_manager.cpp` 1433 -> 1314,
    `ob_sql_plan.cpp`, `ob_dbms_stats_export_import.cpp`, `ob_schema_service_sql_impl.cpp`, …) and every later line
    number in those files shifts. `s3blib` installs a fixed stripper into `sweeplib.strip_text` for the S3b scripts
    only (same algorithm, newlines emitted; checked: identical to the original on all 6,529 files except for the
    restored newlines). Sweeps of other sets that use `sweeplib.stripped()` are affected in these 16 files;
  - function bodies come from the astdb definition records (`line`..`end_line`: the brace block that ends on
    `end_line`); lambdas and local classes inside a body are blanked where it matters;
  - `TypeEnv`: declared type of a name in a function (parameter, field of the class and its bases, local by regex),
    record named by a type string; `macro_index()`: every `#define` of first-party files (continuations joined).
- **Files no TU parses** (`files.tsv` tus=0, 77 code files): each sweep has a regex fallback; such rows carry the
  construct suffix `(regex; file not parsed)` and pretag `regex-fallback(tus=0)`.
- **Common pretag**: `unit=<disposition>` when the row's unit is not `translate` (replace/rewrite/keep/delete units:
  the 1d module or the shim keeps the contract; agents may close those rows with the unit's decision).
- **Common blind spots**: one configuration (macOS arm64 release: other platforms' `#if` branches are seen by the
  regexes but not by the database); templates are seen once with dependent types; code made by macros is analysed
  at the macro definition (c06, c08) or not at all (c07 regions); everything regex-level is a hint.

## c06-int-returns

- **Script**: `sweep_c06_int_returns.py`. **Sites**: astdb functions, methods, function templates and conversions
  whose canonical return type is `int` (int, int32_t, ObLength, obstr_size_t, pid_t, …), one row per canonical
  declaration (52,371; generated files excluded) + 325 regex rows for files no TU parses.
- **symbol**: `<qualified name without oceanbase::>(<canonical parameter types, oceanbase:: dropped>)[ const]`;
  `#2`, `#3` for a symbol repeated in one file (explicit specializations, e.g. `ObObjCmpFuncs::cmp_op_func`).
  construct: `function|method|function template returning int`, `conversion to int`.
- **Body analysis**: the definition's brace block; for a definition made inside a `#define` whose body is not in
  the file, the function's block is found by name in the macro text (parameters replaced by the invocation's
  arguments) and recursively in the macros it invokes. Error constants = `DEFINE_*` names of `ob_errno.def`,
  `OB_PARSER_*`, `OK_`. Error-code variables: `int v = <error constant>`, `INIT_SUCC(v)`, `ret` when `OB_FAIL`/
  `OB_SUCC` is used or a macro whose text declares `int ret = OB_SUCCESS` is invoked (112 such macros, e.g.
  `EVAL_ARG`), `tmp_ret` with `OB_TMP_FAIL`, any v assigned or compared to an error constant.
- **Decision rules** (the shard's evidence names the rule and the lines):
  - `E1` every return is an error-code variable or constant (or a ternary of them), none of the variables is assigned a
    literal/arithmetic/libc value, incremented, or compared with 0 by `<`/`>` -> ERRCODE;
  - `E2` only error constants returned -> ERRCODE;
  - `E3` returns as E1/E2 or single calls whose callee resolves to functions all decided ERRCODE (fixpoint). Callee
    resolution: `f()`/`this->f()` -> methods of the class and bases, else free functions; `C::f()` -> class C or
    `…C::f`; `x.f()`/`x->f()` with x a parameter/field/local whose type names a record (`->` only through a raw
    pointer) -> that record's methods (the most-derived of identical signatures); otherwise every function named
    f; arity-filtered; unqualified libc names and `std::` calls stay unresolved;
  - `S` serialize/deserialize/serialize_/deserialize_ with the unis `(buf, len, pos)` parameters -> ERRCODE by
    RULEBOOK §2.11 (bodies generated by `OB_SERIALIZE_MEMBER` etc.);
  - `V1` no error constant, `OB_FAIL`/`OB_SUCC`/`INIT_SUCC`, error-code variable or POSIX errno name in the body;
    every return a literal, arithmetic/comparison/logical expression, ternary of values, sizeof, libc compare/length
    call, or a literal-initialised local only assigned values; not only `0`; not a C status set (literals only within
    {0,-1,-2,-3,1} without both a negative value and 1) -> VALUE;
  - `V2` conversion to int, or a value typedef return type (ObLength, obstr_size_t, pid_t, int_type) -> VALUE;
  - `V3` like V1 with calls that resolve to functions all decided VALUE -> VALUE;
  - `F` a virtual method without a visible body whose override family (astdb `overrides`, plus exact-signature
    matches in derived records for class templates, whose overrides libclang does not link) is decided one way ->
    the same; a family with both ERRCODE and VALUE members is not decided at all (`family-conflict`).
- **Result**: 48,659 rows decided in the shard (ERRCODE 48,471: E1 38,308, S 3,938, E3 2,903, E2 2,034, F 1,288;
  VALUE 188: V1 157, V2 28, F 2, V3 1), status `confirmed`; residue for agents 3,712 AST + 325 regex rows.
- **Residue pretags**: `errcode?:delegate=<calls> (unresolved …)` (803), `no-body:declaration-only|pure-virtual|
  macro=<M>`, `mixed:returns-ident=<x>` / `returns-errish-ident=<x>` (a variable/field such as `ret_`, `cmp`),
  `mixed:other-expr`, `mixed:errvar-value-use`, `mixed:zero-only`, `mixed:status-literals`, `mixed:posix-errno`
  (POSIX `-ENOENT`-style codes, all in replaced hash/thread units), `family-conflict; own=…`; extra tags `virtual`,
  `name=<value-ish name>`, `body-in-macro=<M>`, `type=<typedef>`.
- **Classification codes** (RULEBOOK §2.1 last row, §5.3): `ERRCODE` (`-> ObResult`; `return OB_X` -> `Err(OB_X)`;
  a stored error code returned by a getter such as `get_ret()` is ERRCODE with an `ObResult` field), `VALUE`
  (`-> i32`: compare results, sizes, counts, and C status codes that callers compare with 0/-1), `UNKNOWN` (§5.3:
  translate as ERRCODE + `TODO(port)`). All members of one override family must get one code (one trait signature).
- **Blind spots**: delegation through function pointers, functors and lambdas stored in variables; macro bodies
  whose function name is built by token pasting; `#if` branches not in the database; definitions only in files
  no TU parses (regex rows have a body pretag only).

## c07-locks

- **Script**: `sweep_c07_locks.py`. **Lock family** (derived, not listed by hand): the record names of RULEBOOK §2.7's
  table and amendment 02-sync row 8 (`ObSpinLock`, `lib::ObMutex`, `ObLatchMutex`, `SpinRWLock`, `ObLatch`,
  `obsys::ObRWLock`, `DRWLock`, `TCRWLock`, `ObQSyncLock`, `ObQSync*`, `ObBucketLock`, `ObBucketQSyncLock`,
  `ObRecursiveMutex`, `ObThreadCond`, `obutil::ObMonitor/ObUtilMutex/Cond`, `ObCond`, `SimpleCond`, `ObLightyCond`,
  `ObFutex`, `LWaitCond`, `SCondTemp`, `ObRowLatch`, `ObSmallSpinLock`, `ObPtrSpinLock`, `CtxLock`, `ObNullLock`,
  `hash::NLock/NCond`, plus `DIRWLock`, `RingSpinLock`, `MemtableMgrLock`, `ISetLocker` that §2.7 does not list) +
  every record deriving from them + pthread/std mutexes and condition variables; fields match on canonical type
  (typedefs resolved, arrays and template instantiations included). `ObByteLock` does not exist in this baseline.
- **Rows**: `lock member` 473, `lock array member` 7, `lock pointer member` 25, `lock reference member` 44 (in 447
  classes), `lock base class` 6, `lock accessor` 27 (any function returning a pointer/reference to a family type),
  6 regex rows. symbol `<Class>::<member>`, `<Class>::<base B>`, accessor `<Class>::<method>(<params>)`.
- **Pretag** (regex over the bodies of the owner's methods and of every class deriving from it):
  `lock=<type>-><§2.7 target>`; `writes-under-lock=<fields>` (assignment, `op=`, `++/--`, or a mutator call
  `push_back/set_refactored/erase_refactored/reset/reuse/clear/assign/insert/remove/…` on a data member, inside a
  region: from a guard declaration `<…Guard|…Lock|…Locker> g(L…)` to the end of its block, or from `L.lock()/wrlock()/
  rdlock()/try_*` to the next `L.*unlock*()` of the function, else to its end -> `cross-function-lock`);
  `reads-outside=<the written fields used outside every region; constructors/destructors excluded>`;
  `returns-lock-ref=<accessor>`; `reentry-site=<n>` (`L.is_wrlocked_by*`/`is_locked_by_self`/`self_locked`);
  `unlock-calls=<n>`; `external-guards=<n>` (guards on `x.L`/`x->L` in other classes' functions where x's declared
  type is the owner: rule-3 candidates).
- **Classification codes** (RULEBOOK §2.7 数据放进锁的规则 1-7): row level `GUARDS_FIELDS` (target_translation lists
  every protected field with a per-field code), `LOCK_ONLY` (orders actions, protects no field: `Latch<()>`),
  `FOREIGN_LOCK` (pointer/reference member: the lock and its data belong to the named owner row), `REENTRANT`
  (rule 5 guard passing; list the sites), `HOOKED` (CtxLock, rule 6), `INFRA` (inside a replaced unit: 1d decides),
  `UNKNOWN`; per-field codes `IN_LOCK` (rule 1), `ATOMIC_OUTSIDE` (rule 2, scalar read outside), `ARCSWAP_SNAPSHOT`
  (rule 2, non-scalar read outside: readers lock or read an `ArcSwap` snapshot; say which), `IN_OTHER_LOCK:<owner>`
  (rule 3), `NOT_PROTECTED` (also written outside: a c08 race field); accessor rows `GUARD_ACCESSOR` (rule 4: the
  owner's guard-returning method, name into types.tsv) or `UNKNOWN`. §5.3 default: scalars atomic, non-scalars in the lock.
- **Blind spots**: regions opened through accessors (`get_lock().lock()`) or macros, guards held in members or
  passed across calls, writes through pointers/references obtained from fields, writes in free functions or other
  classes (only counted as external guards), methods made by macros; regex regions ignore `#if` structure.

## c08-atomics

- **Script**: `sweep_c08_atomics.py`. **Sites**: 3,045 calls of the `ATOMIC_*` macros defined in
  `src/oblib/lib/atomic/ob_atomic.h` (read from its #defines), `inc_update`/`dec_update`, and `__atomic_*`/`__sync_*`
  builtins with an operand (ob_atomic.h itself excluded).
- **Operand mapping**: casts and parentheses removed; `&chain` parsed as base(`.m`|`->m`|`[i]`)*; the base is
  resolved in the enclosing function (astdb definition: parameters, fields of the class and bases, static members,
  locals by regex, `this`, singleton macros such as `GCTX` -> `ObGlobalContext`), then each step through field
  types; an element of an array field maps to the field; an unresolvable chain falls back to the field name (one
  candidate -> mapped with `by-name`, several -> an `ambiguous atomic operand` row); a bare pointer operand is an
  `unresolved atomic operand` (`pointer-field:<Class::f>` when it is a pointer member) unless an array field decays.
  Sites inside a `#define` body are resolved at every invocation of the macro (in a function, or at class scope for
  macros that define members), parameters replaced by the invocation's arguments.
- **Rows**: `atomic-accessed field` 919 (+21 regex rows), `atomic-accessed static member` 25, `atomic-accessed global
  variable` 5, `volatile field` 82 (volatile members never accessed atomically), `scalar reference accessor` 32 (a
  method returning a non-const `bool &`/integer reference to a field: symbol = the field, accessor in the pretag;
  `has_set_stop()` -> `lib::Threads::stop_`, 91 call sites), `ambiguous atomic operand` 41, `unresolved atomic operand`
  193. symbol `<Class>::<field>` (`<Class>::<name>`/`<name>` for statics/globals), `<function>#<operand>` for operand rows.
- **Pretag**: `macros=<LOAD:3,STORE:1,…>`, `sites=`, `type=`, `pointer-field`, `volatile`, `plain-reads=`,
  `plain-writes=`, `addr-taken=`, `ctor-dtor-uses=` (regex counts in the owner's and derived classes' methods with the
  atomic calls blanked), `publish-pattern` (a pointer stored/CAS'ed after the stored object was allocated or written
  through in the same function), `bcas-lazy-init` (CAS of a pointer from NULL/nullptr/0), `bcas-flag` (CAS of a scalar
  from false/0), `by-name`, `in-macro=<M>`; operand rows `ambiguous:<C1::f|C2::f|…>` / `unresolved:<reason>`.
- **Classification codes** (RULEBOOK 0.8 原子操作, §2.7 原子字段的规则): `ATOMIC_SEQCST` (default: `Atomic*`, `ATOMIC_*`
  -> SeqCst, plain reads Acquire / writes Release, `get_mut()` with `&mut self`), `RELAXED_COUNTER` (pure counter or
  statistic: plain accesses Relaxed), `RELAXED_STOP_FLAG` (stop flag: `AtomicBool`, Relaxed, `set_stop()`/
  `has_set_stop()`), `ACQREL_PUBLISH` (a plain access publishes or gates other data: Acquire/Release, say which
  access), `ARCSWAP` (pointer published or replaced, readers lock-free: `ArcSwapOption<T>`), `ONCELOCK` (pointer set
  once from null, BCAS lazy init / double-checked: `OnceLock`), `ATOMIC_ID` (pointer into an arena/pool: `AtomicU32`
  id), `ATOMICPTR_WHITELIST` (`AtomicPtr` only in §3 whitelisted modules), `FIXED_RACE` (raced plain field, volatile
  field or scalar-reference accessor: atomic with role-based ordering, `FIXED(port)`), and for operand rows
  `NOT_A_FIELD` (memory owned elsewhere: cite the owner's row) or `UNKNOWN` (§5.3: SeqCst + `TODO(port)`).
- **Blind spots**: `std::atomic<T>` members are not listed (already atomic types with explicit ordering); accesses
  through other objects (`obj->f_` in other classes) are not in the plain counts; ambiguous name fallbacks and
  template-dependent receivers; regex fallback rows for files no TU parses map `&x_` to the enclosing class only.

## c09-layout

- **Script**: `sweep_c09_layout.py`. **Rows**: `union` 300, `bit-field record` 200 (1,034 bit-fields), `packed record`
  59, `flexible array member` 73 (in 72 records), all astdb definitions; `reinterpret_cast` 3,672 (regex over the
  stripped sources; RULEBOOK's ≈3,692 counted comments too); 6 regex rows for files no TU parses.
- **symbol**: records `<qualified name>` (anonymous `<Parent>::(anonymous union@<line>:<col>)`), flexible members
  `<Record>::<field>`, casts `<enclosing function>#reinterpret_cast<T>` (`#2`… in line order).
- **Pretag**: records `scalar-only`/`has-pointer`/`dependent` (members recursively, through member records and
  arrays), `size=`, `members=`, `bitfields=`, `bits=`, `units=`, `in-union`, `packed_by=`, `pragma_pack=`, `elem=`,
  on-disk heuristics `on-disk?:serialize` (the record or its named owner has serialize/deserialize methods),
  `on-disk?:memcpy` (`sizeof(Name)` inside a MEMCPY/memcpy/memmove call anywhere), `on-disk?:buffer-view`
  (`reinterpret_cast<Name *>` of a buffer anywhere); casts `byte-read` 206 / `byte-write` 35 (dereferenced scalar
  pointer cast, read or assigned), `pod-view` 1,841 (record pointer cast of a byte buffer, or an object or a buffer
  viewed as scalars without dereference), `addr-as-key` 239 (pointer -> integer), `base-derived-slot` 78 (`T *&` or
  `T **` target), `fnptr` 32, `other` 1,241; plus `target=` and `operand=`.
- **Classification codes** (RULEBOOK 0.9, 0.10, §2.12, §5.2 #9): unions `RAW_BITS` (scalar-only: raw bits + accessors,
  `f64::from_bits`), `FIELDWISE_CODEC` (on disk / offsetof: per-field LE codec, pointer members -> buffer offsets +
  accessors), `REWRITE_MODULE` (ObDatum/ObObj and other rewrite units), `UNKNOWN` (in-memory union with pointers: no
  rule; `TODO(port)` and an amendment); bit-field records `OB_BITFIELD` (`ob_bitfield!` with explicit offsets + layout
  test); packed `PACKED_BYTE_VIEW` / `PACKED_REPR_BY_VALUE` (0.10); flexible arrays `BYTE_VIEW` (`XxxView<'a>(&'a [u8])`/
  `XxxMut`) or `STORAGE_CORE_RAW` (inside storage_core only); casts `BYTE_CODEC` (`from_le_bytes`/`to_le_bytes`),
  `POD_VIEW` (`oblib::pod`), `ADDR_TO_ID` (address key -> id, R4/§2.5), `SLOT_R3` (R3 copy-out/write-back),
  `FNPTR` (typed fn pointer or enum dispatch), `DOWNCAST_ISA` (object pointer cast -> §2.13 `as_x()`), `NO_OP` (byte
  type reinterpretation that disappears, e.g. `char *` <-> `uint8_t *`), `UNKNOWN`.
- **Blind spots**: casts written as C-style casts or `static_cast` through `void *` are not listed; the on-disk
  hints are text heuristics (a record copied with `sizeof(var)` instead of `sizeof(Type)` is missed); unions and
  bit-fields made by macros are recorded at the macro invocation.

## c15-overloads and overloads.tsv

- **Scripts**: `make_overloads.py` (table; exits non-zero on a Rust-name collision in one scope),
  `sweep_c15_overloads.py` (16 sets with more than 20 members, 576 members; pretag `members=`, `renamed=`,
  `overrides=`; symbol `<scope>::<name>`). astdb counts 33 sets of 21+ "overloads" because it includes explicit
  specializations (`cmp_op_func` 127, `obj_print_*` 44) and constructors; RULEBOOK §4's `ObRawExprPrinter::print`
  has 13 overloads in this baseline, not 116.
- **Members**: canonical records of functions, methods and function templates; excluded: generated files,
  constructors/destructors/conversions, operator functions (§2.13 maps them to traits/`call`/`at`/`assign`),
  explicit specializations (`sig` spelled `name<>(…)`), deleted functions. Set = parent scope + name; internal-linkage
  free functions are grouped per file.
- **Rules** (column `rule`): `first-declared` (units.tsv dependency order of the member's unit, header before
  implementation, then line), `distinguishing-types` (`name_<snake of the parameter types that differ by position from
  the first-declared member, compared canonically; spelled as written; const/volatile/&/*/namespaces dropped, Ob
  dropped from every word, template arguments kept>`, e.g. `print(ObConstRawExpr *)` -> `print_const_raw_expr`),
  `arity` (no differing type: `name_<n>`), `+arity-suffix`, `full-signature`, `ordinal` (`name_v<k>`) on collisions
  with any name of the scope, `+const-pair` (same parameters, differ only in const: const keeps the name, non-const
  `_mut`), `override-of:<Base::f>` (an overrider takes the overridden method's Rust name; also emitted for overriders
  alone in their scope whose inherited name differs). Bases are processed before derived classes; single names are
  reserved before sets are named.
- **Result**: 9,955 rows, 4,054 sets: first-declared 2,867, first-declared+const-pair 1,504, distinguishing-types 3,797,
  distinguishing-types+arity-suffix 66, distinguishing-types+const-pair 18, arity 250, arity+const-pair 8,
  arity+arity-suffix 1, full-signature 3, ordinal 2, override-of 1,439; no collisions.
- **c15 classification codes**: `NAMES_OK`, `NAMES_REVISED` (give the names; queue an amendment), `MERGE_GENERIC`
  (the set becomes one generic or trait-dispatched function, e.g. `databuff_print_kv`'s 64 arities or
  `serialization::encode` - for replace units amendment 06-A7 proposes the 1d API names), `UNKNOWN`.
- **Blind spots**: constructor sets (RULEBOOK has no naming rule for several constructors), operator sets
  (`at()` vs `operator[]` and `assign()` vs `operator=` meet in one Rust name by §2.5/§2.13), SFINAE-only templates get
  verbose names (`copy_or_assign_t_t_i_allocator_3`), names hidden by a derived class (C++ name hiding) are not
  modelled; amendment 06-A7 (replace units follow their API docs) is queued, not applied.

## slots.tsv

- **Script**: `make_slots.py`. 109 rows: 98 types of `server_service<T>()` (1,207 calls; `ob_server_runtime.h`
  `ObServerServiceSlot<T>`), every one bound in the composition root (`bind_server_service<T>`, also through the
  `BIND_SERVICE(getter, type)` macro: the invocations' arguments), 3 bound but never read through `server_service`;
  `server_obj_pool<T>()` counts for `ObServerObjectPool<T>` (only generic uses exist); + 11 pointer members of
  `share::ObGlobalContext` (GCTX).
- **Columns**: `cpp_type` (record qualified name without `oceanbase::`; GCTX rows: the pointee), `slot_static` (RULEBOOK
  §4: UPPER_SNAKE of the type name without `Ob` + `_SLOT`; template arguments appended; GCTX rows use the member name,
  `sql_proxy_` -> `SQL_PROXY_SLOT`, because `sql_proxy_` and `ddl_sql_proxy_` share `ObMySQLProxy *`; a repeated name
  would get its namespace prepended: none occurs), `crate` (units.tsv crate of the type's file: where the slot
  static goes; GCTX `diag_` points at observer's `obmysql::ObDiag` from share), `file`/`line` (type definition; GCTX
  rows: the member declaration), `uses` (`server_service=<n>;bind=<n>` or `GCTX.<member>=<n>`).
- **Blind spots**: generic call sites (`server_service<Service>()`) and calls made through other templates are not
  attributed to concrete types; GCTX scalar members are not slots (RULEBOOK 0.5: atomics).

## qa_signatures.tsv

- **Script**: `make_qa_signatures.py` (prints the IR type set with a justification per type). R1 IR types:
  node types `ObRawExpr`, `ObStmt` (amendment 07-A3; the task names ObStmt/ObDMLStmt), `TableItem`,
  `ObLogicalOperator`, `Path` and all derived records (167: 91 statements, 34 logical operators, 30 raw exprs, 10
  paths, 2 table items); factories (records `*Factory` with a method returning `X *`, outputting `X *&`/`X **`, or a
  free/destroy method taking `X *`, X a node type or ObLogPlan): `ObRawExprFactory`, `ObStmtFactory`,
  `ObLogOperatorFactory`, `ObLogPlanFactory`; context types: records with a member whose type mentions a factory, and
  their derived records (36: `ObTransformerCtx`, `ObResolverParams`, `ObOptimizerContext`, `ObLogPlan` and its 6
  subclasses, `ObQueryRangeCtx`, `ObRawExprCopier` family, `ObRawExprDeduceType`, `ObRawExprResolverImpl` (via
  `ObResolveContext<ObRawExprFactory>`), the PL resolver/router/AST/cache-object classes, `ObRTDatumArith`,
  `rootserver::ObTruncatePartKeyInfo`, …); excluded: `ObExecContext` (runtime context, RULEBOOK 0.6 执行期). Holders of
  a context (`ObTransformRule::ctx_`, `ObResolver::params_`, `ObJoinOrder::plan_`, `ObStaticEngineCG::opt_ctx_`) are
  not context types: their methods are listed only when the signature mentions an IR type (the closure reaches PL
  execution state and `ObSQLSessionInfo` within 3 steps).
- **Rows**: 11,444 functions (method 9,294, static method 1,397, constructor 660, method template 32, function 57,
  function template 4) whose parameter/return canonical types mention an IR type or whose owning class is one;
  destructors, conversions and operators excluded (Drop and operator traits cannot take `qa`).
- **Columns**: `function` (`<Class>::<sig>[ const]`), `file`/`line` (canonical declaration), `kind`, `ir_types`
  (`param:…; return:…; owner:<Class>(node|factory|context)`), `is_ir_node_method` (4,445 rows: amendment 07-A3 may turn
  them into node accessors or associated functions `Class::f(qa, this_id, …)`; its proposed columns form/this_id/
  reason are not produced), `qa_param` (`qa: &mut QueryArena`: first parameter after self, first parameter of
  free/static functions and constructors).
- **Blind spots**: template parameters standing for IR types, IR pointers hidden in `void *`/`ObIArray<void*>`,
  and nested types of IR classes (`ObDMLStmt::PartExprItem`) are not matched.

## symbols.tsv

- **Script**: `make_symbols.py`. One file for both collision lists of RULEBOOK §4, columns
  `crate name unit kind full_path header line rename`.
- **Symbol rows** (191 rows, 90 names: oblib 86 rows, sql 57, storage 29, share 7, rootserver 6, query 4, observer 2):
  units with disposition translate or replace:*; top-level names = namespace-scope class/struct/union/class template
  definitions and named enums (specializations excluded), typedefs/aliases (each declaring unit counts), free
  functions and function templates under their overloads.tsv Rust name (operators and explicit specializations
  excluded), namespace-scope variables, constants of unscoped namespace-scope enums; collision = one name exported
  by 2+ units of a crate in the same Rust namespace (types: records, typedefs, aliases, enums; values: functions,
  variables, enum constants, enums). `full_path` = `<crate>::<module path of the unit's target>::<name>`; `rename`
  empty (RULEBOOK §4: refer by full path).
- **Macro rows** (42 rows, 19 names): function-like macros defined in headers of 2+ translate/replace units of one
  crate, compared by Rust (lower-case) name; the first definition (units.tsv order, file, line) keeps the name,
  the others get the unit's file stem: `rename` = `<stem>_<macro>` lower case, `full_path` = `<crate>::<rename>!`.
  RULEBOOK §4's "73": 151 macro names have 2+ definitions in headers (same file, other `#if` branch included), 63 in
  2+ distinct headers, 59 within one crate, 18 in 2+ translate/replace units of one crate (19 compared in lower
  case); the others live in keep-c parser headers and the two generated `ob_item_type.h` copies (`IS_AGGR_FUN`,
  `malloc_terminal_node`, …).
- **Blind spots**: class-nested types and enums (Rust has no nested items: their module-level names come from
  types.tsv), object-like macros (constants), macros defined in `.cpp` files, names re-exported across crates.
<!-- END sweep-set S3b -->
