# The SQL front end: the parse tree, IR ids, and the resolver, rewrite and optimizer rules

This section of the design document (PLAN §6, Step 1) settles what ARCHITECTURE.md §4 decides in outline: the Rust mirror of `ParseNode` (§4.2), the ids that mirror pointer identity one for one (§4.1), and how the resolver, rewrite and optimizer code is rewritten against them while keeping its control flow (PLAN §3). It follows ARCHITECTURE.md; where this section disagrees with it, it follows it anyway and section 6 lists the objection.

- Facts are at 834bbee1e; paths are from the repository root. Counts are `git grep` over tracked files; each command and its output is in migration/design/evidence/s4-sql-front/ (counts1.txt, counts2.txt). "R04" and "R12" are research reports 04 and 12.
- It binds sql-parse-tree (crate 21), the converter in sql-parser-sys (22), sql-ir (24), sql-parser (29), sql-resolver (30), sql-rewrite (31) and sql-optimizer (32), and the places where sql-codegen (33), sql (34) and pl (35) touch the IR.
- Core authors write the types of 1.1, 2.1, 2.3 and 2.4 and freeze them with the core API. Implementers follow 1.3, 2.5 and section 3. Reviewers use 3.7.

## 1. The parse tree

### 1.1 What the tree looks like

The C tree is converted once, when `parse_sql` returns, into one `ParseTree` (ARCHITECTURE §4.2). Fields keep the C names of `ParseNode` (src/query/api/query/parser/parse_node.h:129-195):

```rust
// crate sql-parse-tree
pub type NodeId = Id<ParseNode<'static>>;          // ob-base Id, 64 bits (2.1)

#[derive(Clone, Default)]
pub struct ParseNode<'q> {
    pub type_: ObItemType,        // repr(transparent) i32; T_* consts generated from ob_item_type.h
    pub num_child_: i32,
    pub param_num_: i16,
    pub flag_: u32,               // is_neg_() / set_is_neg_(v) ...: one pair per bit field, :136-153
    pub reserved_: u32,           // the flag union's second word, :155
    pub value_: i64,              // int32_values_(i), int16_values_(i) and setters: little-endian lanes
    pub str_value_: &'q [u8],
    pub str_len_: i64,
    pub pl_str_off_: i64,         // sql_str_off_() / set_sql_str_off_(v) name the same word
    pub raw_text_: &'q [u8],
    pub text_len_: i64,
    pub pos_: i64,
    pub children_: Vec<Option<NodeId>>,
    pub stmt_loc_: ObStmtLoc,
    pub raw_param_idx_: i64,      // raw_sql_offset_() / set_raw_sql_offset_(v) name the same word
}

pub struct ParseTree<'q> { nodes: Arena<ParseNode<'q>>, bump: &'q Bump }

impl<'q> ParseTree<'q> {
    pub fn child(&self, n: NodeId, i: i64) -> Option<NodeId>;      // aborts if i >= num_child_
    pub fn set_child(&mut self, n: NodeId, i: i64, c: Option<NodeId>);
    pub fn new_node(&mut self, type_: ObItemType, num: i32) -> NodeId; // parse_node.c:180-210
    pub fn new_zeroed_node(&mut self) -> NodeId;
    pub fn alloc_bytes(&self, b: &[u8]) -> &'q [u8];
    pub fn str_value_is_null(&self, n: NodeId) -> bool;
    pub fn raw_text_is_null(&self, n: NodeId) -> bool;
    pub fn to_owned_node(&self, n: NodeId) -> OwnedParseNode;
    pub fn add_owned_node(&mut self, o: &OwnedParseNode) -> NodeId;
    // and a method for every parse_node.c function C++ calls, under its C name:
    // new_terminal_node, new_non_terminal_node, new_list_node, push_back_child,
    // push_front_child, append_child, deep_copy_parse_node, nodename_equal,
    // parsenode_hash, parsenode_equal
}
// Index<NodeId> and IndexMut<NodeId> give &ParseNode and &mut ParseNode.
```

Why these choices:
- `reserved_` is its own field. It is not part of `flag_`: the union's struct puts it in the second 32-bit word. Eleven lines outside the parser read or write it (src/sql/resolver/ddl/ob_create_view_resolver.cpp:71-72, src/sql/resolver/dml/ob_dml_resolver.cpp:700, :751, src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:2187, :2236 and five more; counts2.txt).
- `num_child_` is a field apart from `children_.len()`: 15 lines outside the parser write it on its own (ob_raw_expr_resolver_impl.cpp:4182-4276, ob_dml_resolver.cpp:749). `child` checks `i < num_child_` and the `Vec`'s bounds. C++ reads unrelated memory in both cases, so a case that reaches the abort is a `BUG(port)`.
- `str_len_` and `text_len_` stay fields. C++ writes a pointer and its length separately (src/sql/plan_cache/ob_sql_parameterization.cpp:1782-1785) and moves a pointer forward while shrinking the length (src/sql/plan_cache/ob_prepare_stmt_struct.cpp:372-373). `str()` returns `&str_value_[..str_len_]` and aborts when `str_len_` is longer than the bytes.
- `children_` is a `Vec` owned by the node. `Arena<T>` is `Vec`-backed and drops its items, so this does not break ARCHITECTURE §3.1 rule 7, which is about bump arenas.

### 1.2 How a tree is made

1. **One tree per statement attempt.** The code that runs a statement (ObSql's handle functions) owns it, and it lives until code generation ends, because `TableItem::node_` (src/sql/resolver/dml/ob_dml_stmt.h:290) is read by the printer after rewrite (src/sql/printer/ob_dml_stmt_printer.cpp:1621). The Rust `ObFastParser` puts its parameter nodes in it first (`ObPCParam::node_`, src/sql/plan_cache/ob_plan_cache_util.h:250). The full parse appends its converted tree. A parse repeated after a failed parameterization (src/sql/ob_sql.cpp:3344-3357) appends a second tree, and the first stays unused, as its memory does in C++.
2. **The converter** is in sql-parser-sys and holds the only `unsafe` of this section. It runs once after `parse_sql` returns and before the thread parses again, since the error text sits in a thread-local buffer (src/sql/parser/sql_parser_base.c:36). It walks with an explicit stack, keeps a map from C address to `NodeId` so a node reached twice stays one node, and turns a NULL child into `None`. It trusts `num_child_`, `str_len_` and `text_len_`: the C core is frozen and built in the same tree.
3. **Strings are not copied.** `parse_malloc` puts them in the statement bump (ARCHITECTURE §9.2), so the converter borrows them as `&'q [u8]` from the `&'q Bump` it is given. Its `// SAFETY:` note says the bytes came from that bump and are never written or freed while it lives (`parse_free` on a bump frees nothing). A NULL pointer becomes `&[]` without `from_raw_parts` (objection 1).
4. **`ParseResult`'s outputs are converted too,** into a Rust `ParseResult` in sql-parse-tree under the C field names (parse_node.h:293-367): `result_tree_: Option<NodeId>`; `param_nodes_: Vec<NodeId>` in list order (`tail_param_node_` goes); `question_mark_ctx_.name_: Vec<&'q [u8]>`; `pl_parse_info_.ref_object_nodes_: Vec<(RefType, NodeId)>`; `no_param_sql_: &'q [u8]` with its lengths; the parentheses list of `ins_multi_value_res_` as a `Vec`; `error_msg_` copied into the statement bump; the 23 bit fields as one `u32` with accessors. Callers write it after the parse (`question_mark_ctx_.count_`, ob_sql.cpp:3360), so it is mutable. `yyscan_info_`, `malloc_pool_`, `jmp_buf_`, `charset_info_`, `charset_info_nls_db_` and `tmp_literal_` stay on the C side.
5. **Nodes made after the parse.** A call to a parse_node.c constructor becomes the `ParseTree` method of the same name, with the same starting values (`value_ = INT64_MAX`, `pl_str_off_ = -1`, src/sql/parser/parse_node.c:195-196). A `memset(node, 0, sizeof(ParseNode))` (ob_raw_expr_resolver_impl.cpp:4175), a node carved from `alloc(sizeof(ParseNode))` and filled by hand (ob_dml_resolver.cpp:189), or a local `ParseNode` variable becomes `new_zeroed_node()`: every field 0, strings empty. Helpers that set fields their own way, such as `ObRawExprUtils::new_parse_node` (src/sql/resolver/expr/ob_raw_expr_util.cpp:6442-6472), keep their bodies.
6. **Bytes stored into a node must live for `'q`.** Bytes from elsewhere, such as a schema column name (`col_node->str_value_ = col_name.ptr()`, ob_raw_expr_resolver_impl.cpp:4178), are first copied into the statement bump with `alloc_bytes`. The bytes are the same, so nothing visible changes.

### 1.3 How code reads and edits the tree

After the conversion only the Rust tree exists. Resolvers reach it as `params_.parse_tree_` (3.2); other code takes `pt: &ParseTree<'q>`, or `&mut` when it or a callee writes, as its first parameter.

| C++ | Rust |
|---|---|
| `const ParseNode &node`, `const ParseNode *node` parameter | `node: NodeId`, `node: Option<NodeId>` |
| `node->children_[i]` | `pt.child(node, i)` |
| `node->children_[i] = x` | `pt.set_child(node, i, x)` |
| `node.children_ = vec; node.num_child_ = n;` (ob_dml_resolver.cpp:749-750) | the same two assignments, in the same order |
| `const_cast<ParseNode *>(&parse_tree)`, then a write (57 cast lines outside the parser, counts2.txt) | a write through `&mut ParseTree`; the tree is mutable for the whole resolve |
| `node->is_neg_ = 1`; `node->int16_values_[i]` | `pt[node].set_is_neg_(1)`; `pt[node].int16_values_(i)` |
| `ObString(node->str_len_, node->str_value_)` | `pt[node].str()` |
| `str_value_ += k; str_len_ -= k;` (ob_prepare_stmt_struct.cpp:372-373) | `str_value_ = &str_value_[k..]; str_len_ -= k;` |
| a write into the bytes (`const_cast<char *>(str_value_)[k] = '-'`, :371) | copy into the bump with the byte changed, then assign; the bytes were deep-copied just before, so nothing else sees them |
| `NULL == node->str_value_` (38 lines outside the parser); the same for `raw_text_` (14) | `pt.str_value_is_null(node)`, `pt.raw_text_is_null(node)`; under ARCHITECTURE's `&'q [u8]` these test for no bytes (objection 1) |
| a node address packed into `int64_t` (src/sql/resolver/dml/ob_select_resolver.cpp:4498, read back at :4597, :4637) | `node.to_i64()`, `NodeId::from_i64(v)` (2.1) |
| `parsenode_hash`, `parsenode_equal` (parse_node.c:654-745) and the `ParseNode*` hash traits (src/oblib/lib/hash/ob_hashutils.h:729-745, :819-834) | the `ParseTree` methods, hashing the same bytes in the same order: `type_` as 4 little-endian bytes, `value_`, `str_len_`, the string bytes with the length cut to `i32`, then each non-null child's hash; recursion through `smart_call!`, returning `OB_PARSER_ERR_SIZE_OVERFLOW` as today |
| `deep_copy_parse_node` into memory that outlives the statement (the PS cache, ob_prepare_stmt_struct.cpp:346-380) | `pt.to_owned_node(n)`, an `OwnedParseNode` with owned bytes and children. At execute, `add_owned_node` puts it into that statement's tree before `ObResolverUtils::resolve_const` reads it (ob_sql.cpp:1610) |
| engine code unpacking a cast type from `value_` (src/sql/engine/expr/ob_expr_cast.cpp:540-548, ob_datum_cast.cpp:8897-8905) | the lane helpers as free functions on `i64`; no tree |

Example: parameterization edits the tree in place (ob_sql_parameterization.cpp:581-586 and :748-749).

```cpp
if (!is_execute_mode(ctx.mode_)) {
  node_type = ctx.tree_->type_;
  ctx.tree_->type_ = T_QUESTIONMARK;
  ctx.tree_->raw_param_idx_ = ctx.sql_info_->total_;
  ctx.tree_->value_ = ctx.question_num_;
}
// ...
root->children_[i] = root->children_[i]->children_[0];
root->children_[i]->is_neg_ = 1;
```

```rust
if !is_execute_mode(ctx.mode_) {
    let n = &mut pt[ctx.tree_];                  // ctx.tree_ is a NodeId
    node_type = n.type_;
    n.type_ = T_QUESTIONMARK;
    n.raw_param_idx_ = ctx.sql_info_.total_;
    n.value_ = ctx.question_num_;
}
// ...
let lifted = pt.child(pt.child(root, i).unwrap(), 0);
pt.set_child(root, i, lifted);
pt[lifted.unwrap()].set_is_neg_(1);
```

The `unwrap`s stand where the C++ dereferences without a check (R12 §10 rule 8).

## 2. Ids that mirror pointer identity

### 2.1 What an id is, and what replaces address-keyed maps (ob-base)

```rust
pub struct Id<T> { raw: NonZeroU64, _t: PhantomData<fn() -> T> }  // arena number << 32 | index
// Copy, Eq, Hash on the raw bits; no PartialOrd or Ord: nothing sorts on an id.
impl<T> Id<T> {
    pub fn arena_no(self) -> u32;
    pub fn index(self) -> u32;
    pub fn to_i64(self) -> i64;               // where the C++ packs an address into int64_t
    pub fn from_i64(v: i64) -> Option<Self>;  // 0 is NULL, as a packed NULL pointer is 0
}

pub trait ArenaItem { type Tag; }             // the item type with 'static for its lifetime
pub struct Arena<T: ArenaItem> { no: u32, items: Vec<T> }     // Drop gives `no` back
impl<T: ArenaItem> Arena<T> {
    pub fn new() -> Self;
    pub fn alloc(&mut self, v: T) -> Id<T::Tag>;
    pub fn replace(&mut self, id: Id<T::Tag>, v: T) -> T;
}
// Index<Id<T::Tag>> and IndexMut<Id<T::Tag>> for Arena<T>.
```

The tag keeps `'q` out of every id: `ExprId` is `Id<RawExpr<'static>>` whatever the arena's lifetime. Arena numbers start at 1, so an id is never 0 and `Option<Id<T>>` is 8 bytes.

1. **Numbers.** Arena numbers come from one process-wide counter that skips any number still held by a live arena, so two live arenas never share a number (objection 2 says why the skip is needed).
2. **Stale ids.** A lookup that chooses among several arenas by number (the expression factory, 2.3) aborts when no live arena has the number, in every build. A lookup in a single arena compares the number under `debug_assertions`. Numbers are not reused while their arena lives, so this is the stale-id check ARCHITECTURE §4.1 asks for, and no separate generation is kept. A slot the C++ reuses on purpose (2.5 rule 9) keeps its id and is not stale.
3. **Text.** An id may appear in log lines where the C++ logs the address; it never reaches client-visible text: plan text, messages, result sets.
4. **Maps and sets keyed by address** become `IdHashMap<K, V>` and `IdHashSet<K>`. They have the method names, arguments and return codes of ob-base's `ObHashMap` and `ObHashSet` ports (`create`, `created`, `get_refactored`, `set_refactored` with its overwrite flag, `exist_refactored`, `erase_refactored`, `reuse`, `destroy`, `size`), a fixed hasher, and iteration in insertion order; erasing keeps the order of the rest. The ob-base cargo feature `reverse-id-order` iterates them backwards, so a hidden dependence on the order shows up as a judge difference (ARCHITECTURE §4.1 rule 3). Keyed by address today: 48 lines on expressions, 10 on statements, 4 on query-range nodes and 1 on parse nodes (R04 §1.6), plus `expr_hash_func`, which hashes an address (src/sql/resolver/ob_stmt.h:60-66).

### 2.2 Which objects get ids, and who owns their arenas

An object gets an id when the C++ compares it by address, keys a map by its address, packs its address into an integer, or keeps pointers to it in more than one place. Everything else is a value: `ColumnItem`, `OrderItem` and `SelectItem` are values in arrays today (R04 §4.3).

| C++ objects | Rust type and id | Arena owner |
|---|---|---|
| `ParseNode` | `ParseNode<'q>`, `NodeId` | `ParseTree` (section 1) |
| `ObRawExpr` and its subclasses | `RawExpr<'q>`, `ExprId` | `ObRawExprFactory` (2.3) |
| `ObDMLStmt` and its subclasses `ObSelectStmt`, `ObInsertStmt`, `ObUpdateStmt`, `ObDeleteStmt` (the last three through `ObDelUpdStmt`) | `DMLStmt<'q>`, `StmtId` | `ObStmtFactory` (2.4) |
| `TableItem` and its subclass `JoinedTable` (src/sql/resolver/dml/ob_dml_stmt.h:132, :427); `SemiInfo` (compared by address: R04's assumption, which the inventory checks); the `ObRawExprSet`s that `EqualSets` point to (src/sql/resolver/dml/ob_raw_expr_sets.h:29-35) | `TableItem`/`TableItemId` (a family with a `JoinedTable` variant), `SemiInfoId`, `ExprSetId` | `ObStmtFactory`; equal sets built by the optimizer go to its context |
| the `ObLogPlan` family; the `ObLogicalOperator` family (30 classes declared `public ObLogicalOperator`, counts2.txt); the `Path` family (`Path` and nine subclasses, src/sql/optimizer/ob_join_order.h:331-1237); `ObJoinOrder`; `ObConflictDetector` | `LogPlan`/`LogPlanId`, `LogicalOperator`/`OpId`, `Path`/`PathId`, `JoinOrderId`, `ConflictDetectorId` | one arena of each per `ObOptimizerContext`; each plan's `log_op_factory_` (src/sql/optimizer/ob_log_plan.cpp:78) allocates into the context's operator arena |
| `ObRangeNode` (src/sql/rewrite/ob_query_range_define.h:153) | `RangeNodeId` | the query-range arena; code generation copies the graph into the plan's own arena, one new id per node the C++ copy allocates |
| PL objects whose addresses PL packs into integers (`ObPLBlockNS`, src/pl/ob_pl_stmt.cpp:1740, :1956) | ids in PL's arenas | pl |

The other statement classes (119 `Ob*Stmt` classes are declared under src/sql/resolver, counts2.txt: DDL, DCL, TCL, commands) are owned values; rewrite and the optimizer never see them (assumption, checked by the inventory's pointer-identity rows). The optimizer's arenas belong to one `ObOptimizerContext`, so the optimizer that `evaluate_cost` builds on a temporary memory context (src/sql/rewrite/ob_transform_rule.cpp:379-398) drops its plans, operators and paths with that context, as `CREATE_WITH_TEMP_CONTEXT` frees them today.

**Families.** Arena-held classes with subclasses become one family type: the C++ base class's fields plus `kind`, an enum with one variant per concrete subclass, named after the base without `Ob`: `RawExpr`, `DMLStmt`, `LogPlan`, `LogicalOperator`, `Path`. A subclass struct keeps its C++ name and holds its own fields, with its parent class's fields in a field named `base` (R12 §5). For expressions this gives 27 variants: the 29 classes declared in src/sql/resolver/expr/ob_raw_expr.h less the two abstract bases `ObTerminalRawExpr` (:2528) and `ObNonTerminalRawExpr` (:3270), whose fields sit in their subclasses' `base`. `ObWinFunRawExpr`'s second base, `ObWindow` (:4675), is a field `ob_window`; no code holds an `ObWindow *`. For every class in a family, concrete or not, the family type has `as_<class>()` and `as_<class>_mut()` (for example `as_column_ref_raw_expr()`), which return that class's part of the variant and abort when the variant does not derive from it. They replace `static_cast`: 507 lines in rewrite and the optimizer cast to an `Ob*RawExpr *` (counts1.txt).

### 2.3 How expressions are stored, and where new ones go

```rust
// crate sql-ir
pub type ExprId = Id<RawExpr<'static>>;

pub struct RawExpr<'q> {
    // ObRawExpr's fields (ob_raw_expr.h:2213-2248) under the same names, without
    // magic_num_, inner_alloc_, expr_factory_ and rt_expr_ (2.5 rule 13)
    pub type_: ObItemType,
    pub expr_class_: ExprClass,
    pub result_type_: ObRawExprResType,
    pub reference_type_: i32,
    pub info_: ObExprInfo,
    pub rel_ids_: ObRelIds,
    pub alias_column_name_: Cow<'q, [u8]>,
    pub expr_name_: Cow<'q, [u8]>,
    pub extra_: ObRawExprExtraInfo,
    pub is_shared_reference_: bool,
    pub is_called_in_sql_: bool,
    pub is_calculated_: bool,
    pub is_deterministic_: bool,
    pub local_session_var_id_: i64,   // partition_id_calc_type_() etc. name the union's other view
    pub expr_hash_: u64,
    pub kind: RawExprKind<'q>,        // 27 variants: Const(ObConstRawExpr<'q>), ColumnRef(..), ...
}

pub struct ObRawExprFactory<'q> {
    arenas: Vec<Arena<RawExpr<'q>>>,  // [0] its own; then temporary arenas, innermost last
    // per arena: try_check_tick_ and worker_check_status_times_ (ob_raw_expr.h:5074-5079)
    is_called_sql_: bool,
}

impl<'q> ObRawExprFactory<'q> {
    pub fn new(bump: &'q Bump) -> Self;
    pub fn create_raw_expr(&mut self, c: ExprClass, t: ObItemType) -> ObResult<ExprId>; // innermost arena
    pub fn create_raw_expr_in(&mut self, arena_no: u32, c: ExprClass, t: ObItemType) -> ObResult<ExprId>;
    pub fn push_temp_arena(&mut self) -> TempArena;         // #[must_use]; debug builds abort if dropped unpopped
    pub fn pop_temp_arena(&mut self, t: TempArena);         // aborts unless t is the innermost arena
}
// Index<ExprId>/IndexMut<ExprId> find the arena by number and abort when none has it (2.1 rule 2).
```

1. **Bytes.** Byte fields are `Cow<'q, [u8]>`: borrowed from the statement bump while compiling, as ARCHITECTURE §3.1 rule 1 has it, and owned in factories that outlive a statement (objection 3). A copier whose `deep_copy_attributes()` is true (`ObPLExprCopier`, src/sql/resolver/expr/ob_raw_expr_copier.h:68-86) turns borrowed bytes into owned ones, which is what that flag does to strings today. `ObObj` fields follow the value section's owned-or-borrowed rule.
2. **Parameters.** Parameter arrays are `Vec<Option<ExprId>>`, since a UDF's parameters are filled with NULL first (ob_raw_expr_util.cpp:784-795). `get_param_expr(i)` returns `Option<ExprId>`, `None` past the end, where the C++ returns the global `USELESS_POINTER` (src/sql/resolver/expr/ob_raw_expr.cpp:42, :831). The write `get_param_expr(i) = x` becomes `set_param_expr(i, x)`, which aborts past the end, where the C++ writes into that global.
3. **Creation.** `create_raw_expr` counts its calls and checks the memory tracker every 1,024, as `try_check_status` does (ob_raw_expr.h:5074-5079), with one counter per arena, since the C++ keeps one per factory. It returns the new id: a general out-of-memory aborts (Decision 12), so only the tracker's -11049 can fail it.
4. **An expression's own factory.** Where the C++ goes through the expression's `expr_factory_` (`deduce_type` builds its deducer on it, ob_raw_expr.cpp:376-377; `fast_check_status` counts on it, :1010-1016), the Rust uses the arena whose number the expression's id carries: `create_raw_expr_in(e.arena_no(), ..)`.

**The 41 local factories.** `ObRawExprFactory x(...)` appears on 41 lines (counts2.txt). Its destructor frees nothing on request threads (ob_raw_expr.h:5019-5025), so in C++ an expression lives as long as the allocator it was made in, not as long as its factory object. Each site gets an inventory row naming which of four kinds it is:
- **(a) A temporary factory inside a live compilation, whose expressions die at the end of the C++ scope:** the five `tmp_expr_factory` sites that run an `ObOptimizer` (ob_transform_rule.cpp:380; src/sql/rewrite/ob_transform_late_materialization.cpp:445; src/sql/rewrite/ob_transform_temp_table.cpp:2338, :2524, :2599), and helpers over a local `ObArenaAllocator` such as src/sql/rewrite/ob_transform_utils.cpp:3606 and :3712. The Rust calls `push_temp_arena` on the compilation's factory where the C++ constructs the factory, and `pop_temp_arena` where the C++ scope ends, on every path; in between, new expressions go into the temporary arena and the compilation's expressions stay readable and writable. A pair of calls, not a closure, because `evaluate_cost` passes the whole `ctx_` to `recover_context` before the C++ scope ends (ob_transform_rule.cpp:404-406), which a closure borrowing `ctx_.expr_factory_` would not allow. In the five `ObOptimizer` blocks nothing creates through `ctx_->expr_factory_` while the temporary factory exists (read in each block), so "new expressions go into the innermost arena" matches the C++.
- **(b) A local factory over an allocator the function does not own, whose expressions escape:** `ObOptimizerUtil::preprocess_multivalue_range_exprs` builds an OR expression in a local factory over its caller's allocator and hands it back (src/sql/optimizer/ob_optimizer_util.cpp:8111-8140; caller src/sql/optimizer/ob_join_order.cpp:4434). No arena is made; the function takes the caller's factory.
- **(c) A standalone factory whose expressions never meet a compilation's** (ob_ddl_service.cpp, ob_schema_printer.cpp, the virtual tables): `ObRawExprFactory::new(bump)`.
- **(d) A proxy,** built from another factory (ob_raw_expr.h:5010-5017), as the PL resolver's is (src/sql/pl/ob_pl_resolver.h:209): the target factory itself. The proxy's own steps (its tick, the stack check and `set_is_called_in_sql`, ob_raw_expr.h:5043-5051) move into one PL resolver function that wraps `create_raw_expr`.

### 2.4 How statements are stored and reused

`ObStmtFactory` owns `Arena<DMLStmt>`, `Arena<TableItem>` and the smaller statement-level arenas. `DMLStmt` holds `ObStmt`'s and `ObDMLStmt`'s fields, and `kind`.
1. **Freed select statements are reused first in, first out.** `free_stmt` moves a statement to `free_list_` (src/sql/resolver/ob_stmt.cpp:207-226; `store_obj` appends, src/oblib/lib/list/ob_obj_store.h:114-124), and `create_stmt<ObSelectStmt>` builds the next select statement in the oldest freed one's memory (ob_stmt.cpp:229-253). The Rust keeps a `VecDeque<StmtId>` and writes the new statement into the same slot, so it gets the old id as the C++ statement gets the old address. `ObTransformUtils::free_stmt` (src/sql/rewrite/ob_transform_utils.cpp:7855-7873) is called on eight lines of cost-based rewrite and the temp-table rule, for example ob_transform_rule.cpp:407.
2. **`stmt_id_`** stays a field taken from `query_ctx_->get_new_stmt_id()` (ob_stmt.cpp:76-86) and copied by `assign` (:37), so a deep copy has the same `stmt_id_` and a new `StmtId`.
3. **One `ObQueryCtx` per compilation.** Every `set_query_ctx` call on a statement (8 lines) passes the compilation's one query context: the resolver's `params_.query_ctx_` (src/sql/resolver/ob_stmt_resolver.h:117), which is the statement factory's (src/sql/ob_sql.cpp:2080-2081), the factory's directly (src/sql/rewrite/ob_transform_utils.cpp:5509), or another statement's (:5794). So statements keep only whether it is set: `query_ctx_: bool`, which `set_stmt_id` still tests (ob_stmt.cpp:79). The context holds the one `ObQueryCtx<'q>` next to the factories.

### 2.5 How translated code keeps pointer identity

1. **New ids appear only where the C++ allocates:** `create_raw_expr` (including the copies made by `ObRawExprCopier::copy_expr_node` and `ObPLExprCopier::do_copy_expr`), `create_stmt`, `create_table_item`, the plan and operator factories, and `alloc_join_path`. A raw allocation followed by placement new becomes `alloc(T::default())` at the allocation and `replace(id, value)` at the placement new.
2. **Edits keep the id:** slot writes, `assign`, `formalize`, type deduction, flag changes.
3. **Equality is id equality.** `find_item` (src/sql/optimizer/ob_optimizer_util.h:274-286) becomes `fn find_item<T: PartialEq<E>, E: Copy>(items: &[T], item: E, idx: Option<&mut i64>) -> bool` with the same loop. `append_array_no_dup`, `remove_item`, `intersect`, `is_subset`, `overlap` and `find_equal_expr` keep their bodies. `this == &expr` in `same_as` (ob_raw_expr.cpp:727) compares ids.
4. **Address-keyed maps and sets** become `IdHashMap`/`IdHashSet` (2.1 rule 4). The four places that iterate one only fill other maps or free memory (R04 §1.6), so insertion order is faithful.
5. **Pointer slots.** `ObRawExpr *&` (691 resolver, 357 rewrite and 131 optimizer lines, counts1.txt) becomes `&mut ExprId`, or `&mut Option<ExprId>` where the slot can hold NULL (rule 15); the same holds for `ObDMLStmt *&`, `TableItem *&` and `ObLogicalOperator *&`. A slot inside an arena (a statement's array element, an expression's parameter) is copied into a local, passed as `&mut local`, and written back right after the call on every path where the C++ writes through the reference, error paths included. If the callee reads the slot's container after writing the slot (for example it walks the statement's expressions), the caller passes the owner's id and the index instead, and the callee writes at the point the C++ writes. Out-parameters keep the `&mut` form rather than becoming return values, so what a caller's variable holds after an error stays as in C++; only the core API functions of this section return `ObResult<Id>`.
6. **Expression parameters** follow 2.3 rule 2.
7. **Groups of slots.** `ObRawExprPointer` keeps `ObRawExpr **` addresses (ob_raw_expr.h:5137-5150), filled by `RelExprPointerChecker` (src/sql/ob_sql_utils.cpp:1885-1905). In Rust each entry is `(StmtId, n)`: the n-th slot that `iterate_stmt_expr` visits (src/sql/resolver/dml/ob_dml_stmt.cpp:649). `set` walks the statement once and writes the listed slots; debug builds check that the statement's slot count has not changed since the group was built, a change under which the C++ addresses would dangle. `ObSelectStmtPointer` (src/sql/rewrite/ob_transform_utils.h:123-135) keeps three kinds of slot (ob_transform_temp_table.cpp:2123-2180): an element of a select statement's `set_query_` (`StmtId`, index), `TableItem::ref_query_` (`TableItemId`), and a query-ref expression's `ref_stmt_` (`ExprId`). `get` and `set` keep their bodies, including `set`'s `break` when a slot already holds the value (ob_raw_expr.cpp:5649-5663).
8. **Marks stay on the object:** `IS_MARKED` for `ObRawExprUniqueSet` (src/sql/resolver/expr/ob_raw_expr_util.h:164-178), `BE_USED` (ob_sql_utils.cpp:1856-1878), `is_shared_reference_`, `reference_type_`.
9. **Reused memory keeps its order.** The `JoinPath` recycle list is a stack popped from the back (ob_join_order.cpp:5435-5448, :11037-11052); the select-statement free list is first in, first out (2.4 rule 1).
10. **Freed memory:** a temporary arena or an optimizer context is dropped where the C++ frees the memory context (2.3 (a), 2.2).
11. **The C++'s own ids stay separate fields** with their own counters: `stmt_id_`, `table_id_`, column ids, `ref_id_`, exec-param indexes, operator numbering, producer ids. They are printed and sorted on; arena ids are neither. Query-block names print `%08X` of a hash over the statement type, `stmt_id_` and the source query block (src/sql/resolver/dml/ob_sql_hint.cpp:370-389), so they do not change.
12. **Addresses packed into integers** use `to_i64`/`from_i64`: PL's expression addresses (src/pl/ob_pl_build.cpp:1062; src/pl/ob_pl_resolver.cpp:8515, :8597, :8806) and ob_select_resolver.cpp:4498. The unpacking side looks the id up in the factory the packing side used.
13. **Back-pointers become context.** `expr_factory_` and `inner_alloc_` go (2.3 rule 4). `rt_expr_` (ob_raw_expr.h:2234) becomes an `IdHashMap<ExprId, ..>` in sql-codegen's expression generator, written where `set_rt_expr` is called and read where `get_rt_expr` is (src/sql/code_generator/ob_static_engine_expr_cg.cpp:190); the other files that touch `rt_expr_` (ob_rt_datum_arith.cpp, ob_json_table_op.cpp, ob_pl_build.cpp and four more) reach that map from their context. `my_plan_` is a `LogPlanId`; `parent_` an `Option<OpId>`; `child_` a `Vec<Option<OpId>>`, because `set_child` pads with NULL (src/sql/optimizer/ob_logical_operator.cpp:394-408); `traverse_ctx_`, a `void *` that always holds an `OpCtx` (src/sql/optimizer/ob_log_exchange.cpp:589; ob_logical_operator.cpp:3424), an `Option<OpCtx>`.
14. **Cycles** are plain id fields: `ref_stmt_`, `outer_expr_`, `dependant_expr_`, `ref_query_`.
15. **NULL.** An IR pointer that the unit ever sets, passes or returns as NULL is an `Option` of the id; one that is never NULL is the id itself, and an `OB_ISNULL` test on it is dropped with the rest of its `else if` chain kept in order. Arrays hold plain ids, except where the C++ stores NULL in them: 6 `push_back(NULL)` lines in resolver, rewrite and optimizer (counts2.txt), plus pointer arrays filled by `prepare_allocate` or `extend_*`; the inventory lists them. `unwrap` appears only where the C++ dereferences without a check, so a panic stands where the C++ would crash.
16. **`ObSharedExprResolver`** hashes child ids where it hashed child addresses (src/sql/resolver/expr/ob_shared_expr_resolver.cpp:67-93). Two ids are equal exactly when the two addresses were, so the candidate lists are the same up to 64-bit hash collisions, and `same_as` decides (:55-65); the equality constraints it records for the plan cache do not change.

### 2.6 Examples

**Copying an expression** (ob_raw_expr_copier.cpp:108-128 and :243-258). This is where sharing inside a copy is kept: a subexpression reached twice is copied once.

```cpp
int ObRawExprCopier::find_in_copy_context(const ObRawExpr *old_expr, ObRawExpr *&new_expr)
{
  int ret = OB_SUCCESS;
  int tmp = OB_SUCCESS;
  uint64_t key = reinterpret_cast<uint64_t>(old_expr);
  uint64_t val = 0;
  new_expr = NULL;
  if (OB_UNLIKELY(!copied_exprs_.created())) {
    // do nothing
  } else if (OB_HASH_EXIST == (tmp = new_exprs_.exist_refactored(key))) {
    new_expr = const_cast<ObRawExpr *>(old_expr);
  } else if (OB_UNLIKELY(OB_HASH_NOT_EXIST != tmp)) {
    ret = tmp;
  } else if (OB_SUCCESS == (tmp = copied_exprs_.get_refactored(key, val))) {
    new_expr = reinterpret_cast<ObRawExpr *>(val);
  } else if (OB_UNLIKELY(OB_HASH_NOT_EXIST != tmp)) {
    ret = tmp;
  }
  return ret;
}

int ObRawExprCopier::do_copy_expr(const ObRawExpr *old_expr, ObRawExpr *&new_expr)
{
  int ret = OB_SUCCESS;
  new_expr = NULL;
  if (OB_ISNULL(old_expr)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_FAIL(copy_expr_node(old_expr, new_expr))) {
  } else {
    for (int64_t i = 0; OB_SUCC(ret) && i < new_expr->get_param_count(); ++i) {
      if (OB_FAIL(SMART_CALL(copy(new_expr->get_param_expr(i))))) {
      }
    }
  }
  return ret;
}
```

```rust
pub struct ObRawExprCopier {
    new_exprs_: IdHashSet<ExprId>,              // was ObHashSet<uint64_t> of addresses
    copied_exprs_: IdHashMap<ExprId, ExprId>,   // was ObHashMap<uint64_t, uint64_t>
    uncopy_expr_nodes_: Vec<ExprId>,
}

impl ObRawExprCopier {
    pub fn find_in_copy_context(&mut self, old_expr: ExprId, new_expr: &mut Option<ExprId>) -> ObResult {
        let mut ret: ObResult = Ok(());
        *new_expr = None;
        if !self.copied_exprs_.created() {
        } else {
            match self.new_exprs_.exist_refactored(&old_expr) {
                OB_HASH_EXIST => *new_expr = Some(old_expr),
                OB_HASH_NOT_EXIST => match self.copied_exprs_.get_refactored(&old_expr) {
                    Ok(val) => *new_expr = Some(val),
                    Err(OB_HASH_NOT_EXIST) => {}
                    Err(e) => ret = Err(e),
                },
                tmp => ret = Err(tmp),
            }
        }
        ret
    }

    pub fn do_copy_expr(&mut self, expr_factory_: &mut ObRawExprFactory<'_>,
                        old_expr: Option<ExprId>, new_expr: &mut Option<ExprId>) -> ObResult {
        let mut ret: ObResult = Ok(());
        *new_expr = None;
        if old_expr.is_none() {
            ret = Err(OB_ERR_UNEXPECTED);
        } else if let Err(e) = self.copy_expr_node(expr_factory_, old_expr.unwrap(), new_expr) {
            ret = Err(e);
        } else {
            let ne = new_expr.unwrap();
            let mut i = 0;
            while ret.is_ok() && i < expr_factory_[ne].get_param_count() {
                let mut slot = expr_factory_[ne].get_param_expr(i);
                ret = smart_call!(self.copy_slot(expr_factory_, &mut slot));
                expr_factory_[ne].set_param_expr(i, slot);
                i += 1;
            }
        }
        ret
    }
}
```

`copy_slot` is the core API's name for the `copy(ObRawExpr *&)` overload, which sets the slot to NULL before copying (ob_raw_expr_copier.cpp:24-46), so the slot is written back on the error path too (2.5 rule 5). The loop re-reads `get_param_count()` each time round, as the C++ does. `copy_expr_node` is the one place a copy gets a new id: `let tmp = expr_factory_.create_raw_expr(class, t)?;` followed by `RawExpr::deep_copy(tmp, expr_factory_, self, expr)?` and `self.add_expr(expr, tmp)?`. It may use `?` because its C++ body is a plain `OB_FAIL` chain, and its `OB_ISNULL(expr)` test is dropped because every caller tests first (`do_copy_expr` above; `copy_on_replace`, :274-275).

**Counting references per expression** (ob_logical_operator.cpp:283-307; line breaks and one empty `else` removed). The count per address decides which subexpressions get their own producer (`extract_shared_exprs`, :1836-1866) and so the pinned `output(...)` lists; counting per id gives the same counts.

```cpp
int ObAllocExprContext::add_flattern_expr(const ObRawExpr* expr)
{
  int ret = OB_SUCCESS;
  int64_t ref_cnt = 0;
  if (OB_FAIL(flattern_expr_map_.get_refactored(reinterpret_cast<uint64_t>(expr), ref_cnt))) {
    if (OB_HASH_NOT_EXIST == ret) {
      ret = OB_SUCCESS;
      if (OB_FAIL(flattern_expr_map_.set_refactored(reinterpret_cast<uint64_t>(expr), 1))) {
      }
    }
  } else if (OB_UNLIKELY(ref_cnt < 0)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_FAIL(flattern_expr_map_.set_refactored(reinterpret_cast<uint64_t>(expr), ref_cnt + 1, 1))) {
  }
  for (int64_t i = 0; OB_SUCC(ret) && i < expr->get_param_count(); ++i) {
    ret = SMART_CALL(add_flattern_expr(expr->get_param_expr(i)));
  }
  return ret;
}
```

```rust
pub fn add_flattern_expr(&mut self, expr_factory_: &ObRawExprFactory<'_>, expr: ExprId) -> ObResult {
    let mut ret: ObResult = Ok(());
    match self.flattern_expr_map_.get_refactored(&expr) {    // IdHashMap<ExprId, i64>
        Err(OB_HASH_NOT_EXIST) => ret = self.flattern_expr_map_.set_refactored(expr, 1, 0),
        Err(e) => ret = Err(e),
        Ok(ref_cnt) if ref_cnt < 0 => ret = Err(OB_ERR_UNEXPECTED),
        Ok(ref_cnt) => ret = self.flattern_expr_map_.set_refactored(expr, ref_cnt + 1, 1),
    }
    let mut i = 0;
    while ret.is_ok() && i < expr_factory_[expr].get_param_count() {
        let param = expr_factory_[expr].get_param_expr(i).unwrap();
        ret = smart_call!(self.add_flattern_expr(expr_factory_, param));
        i += 1;
    }
    ret
}
```

The `unwrap` stands where the C++ passes the parameter on unchecked and the callee dereferences it.

**Reusing a join path** (ob_join_order.cpp:11037-11052). A member function that reaches another IR object becomes an associated function with the object's id first (3.3).

```cpp
int ObJoinOrder::alloc_join_path(JoinPath *&join_path)
{
  int ret = OB_SUCCESS;
  join_path = NULL;
  if (OB_ISNULL(get_plan())) {
    ret = OB_ERR_UNEXPECTED;
  } else if (!get_plan()->get_recycled_join_paths().empty() &&
             OB_FAIL(get_plan()->get_recycled_join_paths().pop_back(join_path))) {
  } else if (NULL == join_path &&
            OB_ISNULL(join_path = static_cast<JoinPath*>(allocator_->alloc(sizeof(JoinPath))))) {
    ret = OB_ALLOCATE_MEMORY_FAILED;
  }
  return ret;
}
```

```rust
impl ObJoinOrder {
    pub fn alloc_join_path(this: JoinOrderId, optimizer_context_: &mut ObOptimizerContext<'_, '_>,
                           join_path: &mut Option<PathId>) -> ObResult {
        *join_path = None;
        let plan = optimizer_context_.join_orders_[this].get_plan();
        let recycled = &mut optimizer_context_.log_plans_[plan].recycled_join_paths_;
        if !recycled.is_empty() {
            *join_path = recycled.pop();
        }
        if join_path.is_none() {
            *join_path = Some(optimizer_context_.paths_.alloc(Path::default()));
        }
        Ok(())
    }
}
```

The caller's `new (join_path) JoinPath(...)` becomes `paths_.replace(id, ..)`, so a recycled path comes back with its old id as it comes back at its old address. The `OB_ISNULL(get_plan())` test goes, since a `LogPlanId` is never NULL, and so does the allocation-failure branch (Decision 12).

## 3. How resolver, rewrite and optimizer code is written against the core

### 3.1 What keeping the control flow means

- One Rust function per C++ function, with the same name, in the file ARCHITECTURE §14 gives; parameters in the C++ order after the context parameters of 3.2; overloads under the names the declaration index gives them.
- Statements in the C++ order. Loops keep their order and re-evaluate their bounds each time round where the C++ does. Early exits (`break`, `return`) stay where the C++ has them.
- Errors as ARCHITECTURE §2: `?` only in plain `OB_FAIL` chains, otherwise a local `ret` in the C++ order; `CK`, `OX`, `OZ` and `OV` expanded (R02 §5.2). Every `SMART_CALL` is `smart_call!` (ARCHITECTURE §11). A general allocation failure has no Rust branch (Decision 12). Log lines keep their text and keys.
- The only statements the Rust adds are an id lookup, a slot copied out and written back (2.5 rule 5), and a context passed on (3.2). The only ones it removes are an `OB_ISNULL` test on an id that cannot be NULL (2.5 rule 15) and an allocation-failure branch.

### 3.2 How functions reach contexts and factories

In a resolver, transform rule, plan, operator, copier or helper, a C++ member that points to a context, a factory or another arena owner becomes a parameter of the functions that use it, never a field: the resolver's `params_` (src/sql/resolver/ob_stmt_resolver.h:155) and its copies `allocator_`, `schema_checker_` and `session_info_` (:152-154, taken from `params_` at :44-47); a transform rule's `ctx_`; a plan's `optimizer_context_`; the copier's `expr_factory_`. The parameter comes first after `self` (ARCHITECTURE §7.1) and keeps the member's name, so `params_.expr_factory_` reads the same in both trees; a resolver's `session_info_` is `params_.session_info_`. Why: child resolvers, nested transformers and nested optimizers use the context while the object that made them is still inside one of its methods, and a `&mut` field would lock that object for the child's whole life.

Each context holds every arena owner in a field of its own, so a function can hold a slot inside a statement and the expression factory at the same time:

| Context (`'a` borrow, `'q` statement) | Arena owners it holds | Other changes |
|---|---|---|
| `ObResolverParams<'a, 'q>` (src/sql/resolver/ob_resolver_define.h:318-458) | `expr_factory_: &'a mut ObRawExprFactory<'q>`, `stmt_factory_: &'a mut ObStmtFactory<'q>`, `query_ctx_: &'a mut ObQueryCtx<'q>`, and a new `parse_tree_: &'a mut ParseTree<'q>` | `allocator_: &'q Bump`; `outline_parse_result_` is a Rust `ParseResult` |
| `ObTransformerCtx<'a, 'q>` (src/sql/rewrite/ob_transform_rule.h, `struct ObTransformerCtx`) | `expr_factory_`, `stmt_factory_`, and a new `query_ctx_` | `allocator_: &'q Bump`; `push_down_filters_` and `temp_table_ignore_stmts_` hold ids |
| `ObOptimizerContext<'a, 'q>` (src/sql/optimizer/ob_optimizer_context.h) | `expr_factory_`, a new `stmt_factory_` (the C++ follows statement pointers), `query_ctx_`, and its own arenas of plans, operators, paths, join orders, conflict detectors and equal sets | an optimizer run inside `evaluate_cost` gets the factory while its temporary arena is pushed |

Session, schema guard and service pointers follow the server-context section; this section only needs them to be shared (`&`) references, so the split borrows above stay possible.

### 3.3 How member functions of arena objects are written

- A member function that only reads or writes its own object's fields stays a `&self` or `&mut self` method of the family type or subclass struct, called as `params_.expr_factory_[e].get_param_count()`.
- One that follows a pointer to another IR object (a child, a parent, its plan, its statement) becomes an associated function of the same class, with the object's id first, named `this`, and the context next: `ObLogicalOperator::allocate_expr_post(this: OpId, optimizer_context_, ..)`, `RawExpr::same_as(this: ExprId, expr: ExprId, expr_factory_, check_context)`. The C++ qualified name and the Rust path are the same, so a reviewer finds one from the other.
- A virtual function is a `match` on `kind` inside the base class's function, calling the subclass's function with the same id: `ObLogicalOperator::est_cost(this, ..)` calls `ObLogJoin::est_cost(this, ..)`.
- Inside such a function, read the fields it needs into locals before calling anything that takes the context mutably. Ids, numbers and `&'q` bytes are `Copy`, so this copies a few words.

### 3.4 How virtual calls dispatch outside the arenas

- **Transform rules** (34 classes declared `public ObTransformRule`, counts2.txt) become a trait `TransformRule`. Its provided methods are `ObTransformRule`'s own functions (`transform`, `transform_self`, `evaluate_cost`, `accept_transform` and the rest), its other methods are the virtual functions, and `base()`/`base_mut()` reach the base fields. The driver calls each rule by its concrete type, as `transform_one_rule<c>` does today (src/sql/rewrite/ob_transformer_impl.h:34-52), in the order of ob_transformer_impl.cpp:455-487. This departs from R12 §5's enum row: a rule's own code calls base functions that call its virtual functions (`ObTransformGroupByPullup` calls `accept_transform` at src/sql/rewrite/ob_transform_groupby_pullup.cpp:64, which calls `evaluate_cost` at ob_transform_rule.cpp:296, which calls the `is_expected_plan` the rule overrides at :400), and from inside one variant's method there is no enum value to dispatch on.
- **DML resolvers** dispatch `ObDMLResolver`'s virtual functions through a trait `DMLResolver`, whose provided methods are `ObDMLResolver`'s own functions. `parent_namespace_resolver_` (src/sql/resolver/dml/ob_dml_resolver.h:852) is `Option<&'p mut dyn DMLResolver<'q>>`: children call functions on their parents and walk up the chain (ob_select_resolver.cpp:3181-3200; src/sql/resolver/dml/ob_aggr_expr_push_up_analyzer.cpp:205-275). An enum of `&mut` references would make each resolver's type name its parent's lifetime, and its parent's parent's, all the way up, because `&mut T` does not let a lifetime inside `T` shrink; `&'p mut dyn DMLResolver<'q>` hides them. The parent makes the child, the child resolves, the parent reads the child's results and drops it, and only then uses `self` again.
- **Other resolvers, copiers, replacers and visitors** are plain structs. The expression and statement visitors (`ObRawExprVisitor`, `ObStmtExprVisitor`) become traits whose slot callback receives the slot as `&mut ExprId` and the factory as a separate parameter.

### 3.5 Example: `ObDMLResolver::resolve_limit_clause`

C++, src/sql/resolver/dml/ob_dml_resolver.cpp:5377-5449, with the two branches that call `ObResolverUtils::resolve_const_expr` shortened to a comment:

```cpp
int ObDMLResolver::resolve_limit_clause(const ParseNode *node, bool disable_offset)
{
  int ret = OB_SUCCESS;
  if (node) {
    current_scope_ = T_LIMIT_SCOPE;
    ObDMLStmt *stmt = get_stmt();
    ParseNode *limit_node = NULL;
    ParseNode *offset_node = NULL;
    if (node->type_ == T_LIMIT_CLAUSE) {
      limit_node = node->children_[0];
      offset_node = node->children_[1];
    } else if (node->type_ == T_COMMA_LIMIT_CLAUSE) {
      limit_node = node->children_[1];
      offset_node = node->children_[0];
    }
    ObRawExpr* limit_count = NULL;
    ObRawExpr* limit_offset = NULL;
    if (disable_offset && OB_NOT_NULL(offset_node)) {
      ret = OB_ERR_PARSE_SQL;
      int32_t str_len = static_cast<int32_t>(offset_node->text_len_);
      int32_t line_no = 1;
      LOG_WARN("can't set offset for limit clause in delete/update stmt");
      LOG_USER_ERROR(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX),
                    str_len, offset_node->raw_text_, line_no);
    } else {
      /* two branches: resolve_const_expr(params_, *node, expr, NULL) on offset_node and limit_node */
    }
    CK(session_info_)
    if (OB_SUCC(ret)) {
      ObRawExpr **exprs[] = { &limit_count, &limit_offset };
      for (int64_t i = 0; i < ARRAYSIZEOF(exprs) && OB_SUCC(ret); i++) {
        ObRawExprResType dst_type;
        dst_type.set_int();
        ObSysFunRawExpr *cast_expr = NULL;
        if (NULL != (*exprs[i]) && !ob_is_int_tc((*exprs[i])->get_result_type().get_type())) {
          OZ(ObRawExprUtils::create_cast_expr(
                  *params_.expr_factory_, *exprs[i], dst_type, cast_expr, session_info_));
          CK(NULL != cast_expr);
          if (OB_SUCC(ret)) {
            *exprs[i] = cast_expr;
          }
        }
      }
    }
    if (OB_SUCC(ret)) {
      stmt->set_limit_offset(limit_count, limit_offset);
    }
  }
  return ret;
}
```

Rust, as a provided method of `DMLResolver` (log texts abbreviated here):

```rust
fn resolve_limit_clause(&mut self, params_: &mut ObResolverParams<'_, 'q>,
                        node: Option<NodeId>, disable_offset: bool) -> ObResult {
    let mut ret: ObResult = Ok(());
    if let Some(node) = node {
        self.base_mut().current_scope_ = T_LIMIT_SCOPE;
        let stmt = self.get_stmt();
        let mut limit_node: Option<NodeId> = None;
        let mut offset_node: Option<NodeId> = None;
        let pt = &*params_.parse_tree_;
        if pt[node].type_ == T_LIMIT_CLAUSE {
            limit_node = pt.child(node, 0);
            offset_node = pt.child(node, 1);
        } else if pt[node].type_ == T_COMMA_LIMIT_CLAUSE {
            limit_node = pt.child(node, 1);
            offset_node = pt.child(node, 0);
        }
        let mut limit_count: Option<ExprId> = None;
        let mut limit_offset: Option<ExprId> = None;
        if disable_offset && offset_node.is_some() {
            ret = Err(OB_ERR_PARSE_SQL);
            let off = &params_.parse_tree_[offset_node.unwrap()];
            let str_len = off.text_len_ as i32;
            let line_no: i32 = 1;
            log_warn!(OB_ERR_PARSE_SQL, "can't set offset for limit clause ...");
            log_user_error!(OB_ERR_PARSE_SQL, ob_errpkt_strerror(OB_ERR_PARSER_SYNTAX),
                            str_len, off.raw_text_, line_no);
        } else {
            // two branches: ObResolverUtils::resolve_const_expr(params_, n, &mut expr, None)
        }
        if ret.is_ok() && params_.session_info_.is_none() {
            ret = Err(OB_ERR_UNEXPECTED);
            log_warn!(OB_ERR_UNEXPECTED, "invalid arguments", "session_info_");
        }
        if ret.is_ok() {
            for slot in [&mut limit_count, &mut limit_offset] {
                if ret.is_err() {
                    break;
                }
                let mut dst_type = ObRawExprResType::default();
                dst_type.set_int();
                let mut cast_expr: Option<ExprId> = None;
                if let Some(e) = *slot
                    && !ob_is_int_tc(params_.expr_factory_[e].get_result_type().get_type())
                {
                    ret = ObRawExprUtils::create_cast_expr(params_.expr_factory_, e, &dst_type,
                                                           &mut cast_expr, params_.session_info_);
                    if let Err(err) = ret {
                        log_warn!(err, "fail to exec ObRawExprUtils::create_cast_expr(...)");
                    }
                    if ret.is_ok() && cast_expr.is_none() {
                        ret = Err(OB_ERR_UNEXPECTED);
                        log_warn!(OB_ERR_UNEXPECTED, "invalid arguments", "NULL != cast_expr");
                    }
                    if ret.is_ok() {
                        *slot = cast_expr;
                    }
                }
            }
        }
        if ret.is_ok() {
            params_.stmt_factory_[stmt.unwrap()].set_limit_offset(limit_count, limit_offset);
        }
    }
    ret
}
```

What changed and why: `ObRawExpr **exprs[]`, an array of pointers to two local slots, is an array of two `&mut Option<ExprId>`; `CK` and `OZ` are expanded (R02 §5.2); the tree is read through `params_.parse_tree_`, and the borrow `pt` ends before the first call that takes `params_` mutably; `stmt->set_limit_offset` touches only the statement's own fields, so it is a `&mut self` method reached through the statement arena; `stmt.unwrap()` stands where the C++ dereferences `stmt` without a check.

### 3.6 Order and determinism

- Address-keyed containers iterate in insertion order (2.1 rule 4); runs with `reverse-id-order` belong to Step 2a and Step 6.
- Every sort uses ob-base's transcriptions (ARCHITECTURE §10). The comparators order by `stmt_id_` (src/sql/rewrite/ob_transform_predicate_move_around.cpp:127), `table_id_` (ob_transform_utils.cpp:10200) or names (src/sql/optimizer/ob_log_join.cpp:789) and are translated as they are; none orders by address (R04 §1.6), so none orders by id.
- No hash that feeds output includes an id; the only hash over ids is `ObSharedExprResolver`'s, which only picks candidates (2.5 rule 16).
- Cost and estimate arithmetic follows ARCHITECTURE §10.

### 3.7 What a reviewer checks

1. Every C++ function has one Rust function of the same name, with statements, loops, early exits and log lines in the same order, and only the additions and removals 3.1 allows.
2. No new id except at a C++ allocation; no `clone` of an IR object where the C++ copies a pointer; copies only through the copiers.
3. `find_item`, `==` and the set helpers compare ids exactly where the C++ compares pointers, and values where it compares values.
4. Every address-keyed map or set is an `IdHashMap` or `IdHashSet`; no std `HashMap`; no sort on ids.
5. Every slot write lands where the C++ write lands, error paths included; a callee that re-reads the slot's container gets the owner and index.
6. `Option` exactly where the C++ can hold NULL; `unwrap` only where the C++ dereferences unchecked; a dropped `OB_ISNULL` only on an id that is not an `Option`.
7. Each local `ObRawExprFactory` is the kind its inventory row names; every `push_temp_arena` has its `pop_temp_arena` where the C++ scope ends.
8. Contexts are parameters named after the C++ members. Only the three contexts of 3.2 hold `&mut` references to arena owners; apart from them, no struct keeps a `&mut` to a context, factory or tree, except a child resolver's parent link.
9. The parse tree is read through `child`, the flag and lane accessors and `str()`; NULL tests go through the `_is_null` functions; bytes stored into a node come from the statement bump.
10. `as_<class>()` wherever the C++ casts; no `match` arm that quietly skips a variant the C++ would have cast.
11. `smart_call!` at every `SMART_CALL`.
12. No arena id in client-visible text.

## 4. What Step 2a measures for this section

- Full-parse time with the conversion against the C++ (the 1.2x gate; if it misses, the handle fallback of ARCHITECTURE §4.2 goes in under the same `ParseTree` API), and how many nodes the converter's address map finds with two parents over the 272 cases.
- `output(...)` lists and plan-cache hit counts on the plan-bearing cases the narrow path reaches, exact with `reverse-id-order` off and on.
- In the disposable full pass, the borrow errors (E0499, E0502, E0505) per 1,000 lines of sql-resolver, sql-rewrite and sql-optimizer, grouped by the rule of section 3 behind them; a rule behind a recurring group is amended before the core API freezes.
- How often an expression lookup goes past the first arena, which is what 2.3's lookup by number costs.

## 5. Which sql-nio conventions the converter adopts

| Convention (abi-naming.md, notes/ffi-mechanics.md) | Here | Why |
|---|---|---|
| Logic in safe code, the boundary in a thin `unsafe` layer | adopted | the converter is the boundary; `ParseTree` and everything that uses it are safe |
| The boundary does not re-check pointers and lengths from the other side | adopted | the C core is frozen and built in the same tree (1.2 rule 2) |
| A zero length becomes `&[]` without `from_raw_parts` | adopted | the C core leaves `str_value_` NULL with `str_len_` 0 (src/sql/parser/sql_parser_mysql_mode.l:744) |
| Receiver as the first parameter, never a global | adopted | `malloc_pool_` is the receiver of the memory callbacks |
| `#[cfg(test)]` stubs for symbols defined elsewhere | adopted | sql-parser-sys tests link the C core with stubs for `lookup_pl_symbol`, `try_check_mem_status` and the stack checks |
| Header generated from Rust by cbindgen | rejected | the parser's C headers exist; bindgen output is checked in (ARCHITECTURE §9.2) |
| Pure Rust code picks Rust names | rejected | the tree mirrors a C struct that resolvers read field by field; C names keep both trees searchable |
| `_view` and `_handle` vocabulary | not needed | no borrowed C structure crosses: the tree is converted |

## 6. Objections to ARCHITECTURE.md

1. **`str_value_` as `&'q [u8]` (§4.2) cannot tell NULL from an empty string, and the C++ does.** The lexer gives a double-quoted `""` a non-NULL, zero-length `str_value_` (`parse_strndup(tmp_literal, str_len_ + 1, ...)` with `str_len_` 0, src/sql/parser/sql_parser_mysql_mode.l:495-496), while `X''` gets NULL (:744). `parsenode_hash` hashes the bytes only for a non-NULL pointer (parse_node.c:666-667), and `murmurhash64A` of zero bytes still changes the value (src/oblib/lib/hash_func/murmur_hash.h:19-51); `parsenode_equal` treats NULL and non-NULL as different (parse_node.c:713-717). Outside the parser, 38 lines test `str_value_` for NULL and 14 test `raw_text_`, some with no length test (ob_dml_resolver.cpp:8796, :10071; ob_raw_expr_resolver_impl.cpp:503, :2224; ob_sql_parameterization.cpp:809, :2555). Proposed: `str_value_: Option<&'q [u8]>` and `raw_text_: Option<&'q [u8]>`. This section sends every NULL test through `str_value_is_null` and `raw_text_is_null`, so the change stays inside sql-parse-tree and the converter.
2. **"From one process-wide counter, so ids are unique among live arenas" (§4.1) does not follow.** A 32-bit counter wraps after 2^32 arenas, and some arenas live as long as a cache entry: PL cache objects own an expression factory (src/sql/pl/pl_cache/ob_pl_cache_object.h:185, :243), as do PL function ASTs (src/sql/pl/ob_pl_stmt.h:1333, :1459) and the truncate-info service (src/rootserver/truncate_info/ob_truncate_info_service.h:100). After a wrap a new arena could take a live arena's number, and ids from the two would compare equal. 2.1 rule 1 makes the counter skip numbers held by a live arena, which keeps ARCHITECTURE's wording and makes its reason hold; the per-arena generation then catches nothing that the number check does not.
3. **Raw expressions that outlive a statement are not covered.** §3.1 rule 1 puts compilation's names and literals in the statement bump as `&'q`, and §4.1 puts expressions in arenas owned by the compile context. PL packages copy expressions into the package's factory and keep their addresses (src/pl/ob_pl_build.cpp:1055-1062), and PL cache objects, function ASTs and the PL router own factories (ob_pl_cache_object.h:243; ob_pl_stmt.h:1459; src/sql/pl/ob_pl_router.h:62). A `RawExpr` with `&'q [u8]` fields cannot live in such an owner without borrowing from itself, which safe Rust does not allow. This section makes `RawExpr`'s byte fields `Cow<'q, [u8]>` (2.3 rule 1). The parse tree keeps `&'q [u8]`, since its one long-lived copy, the PS cache's fixed parameters, goes through `OwnedParseNode` (1.3). The value section must give `ObObj` the same owned-or-borrowed choice, since `ObConstRawExpr` holds one.
4. **"An enum over the 29 subclasses" (§4.1 rule 7).** Two of the 29 classes declared in ob_raw_expr.h are abstract bases, never allocated (`ObTerminalRawExpr`, :2528; `ObNonTerminalRawExpr`, :3270), and three concrete classes are also bases of others (`ObConstRawExpr`, `ObOpRawExpr`, and `ObSysFunRawExpr`, the base of six classes at :3934-4396). The enum has 27 variants, and the rest is `base` fields (2.2).
5. **Rule 5 of §4.1 names one kind of address reuse; there are two.** Besides the `JoinPath` recycle list, `ObStmtFactory` builds new select statements in freed ones' memory (src/sql/resolver/ob_stmt.cpp:207-253), and cost-based rewrite frees statements through `ObTransformUtils::free_stmt` (eight call lines, for example ob_transform_rule.cpp:407). 2.4 rule 1 applies rule 5's reasoning to it.
