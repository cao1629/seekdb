# The dependency map, the manifests and the declaration index

This directory holds the dependency map of Step 1 (PLAN.md section 6, "The map"). Three scripts in migration/scripts/ write it, the three manifests beside PLAN.md and the declaration index in migration/decl-index/. The counts below come from the run of 2026-09-25 over the frozen base 834bbee1e.

## How to run the three scripts

Run them from the repository root, in this order, since each one reads what the one before wrote. They need only the Python standard library and the reference build at ../ref-834bbee1e/build_release, whose compile_commands.json and object files depmap_seekdb.py reads. While judge runs share the machine, keep them at `nice -n 10` and at most 4 jobs.

```
nice -n 10 python3 -B migration/scripts/depmap_seekdb.py --jobs 4
nice -n 10 python3 -B migration/scripts/make_manifest_seekdb.py \
    --order migration/depmap/order-crates.txt --out migration/manifest.tsv \
    --crates migration/design/evidence/rulebook/crates/crates-design.tsv \
    --outside-build tools/ob_error/src/ob_error \
    --new-module ob-platform/libc --new-module ob-platform/allocator
nice -n 10 python3 -B migration/scripts/decl_index.py --force --jobs 4
```

On this Mac they take about 20 s, 4 s and 90 s. Each run writes the same bytes as the run before it.

- **depmap_seekdb.py** reads every `#include` (following the 116 forwarding headers and 1 umbrella header), and turns the DWARF of the reference build's 364 objects, plus the source, into definition and link edges. It writes edges.tsv, units.tsv, order.txt, order-crates.txt, order-files.txt, the cycle files, definitions.tsv, forwarders.tsv, generated.txt, misses.tsv, aliases.tsv, crate-edges.tsv, crate-check.txt, ledger-needed.tsv and summary.txt. `--trial FILE...` prints one file's includes and edges for a hand check.
- **make_manifest_seekdb.py** writes migration/manifest.tsv (Step 3), migration/core-manifest.tsv (the core) and migration/not-translated.tsv (RULEBOOK section 4). Without `--sub` pairs, a target's file name is the source's stem, with a trailing `_` for lib, main and mod, and `.rs`. It writes nothing and exits 1 when two rows share a target, a target is a crate root or sits under `generated/`, an in-build unit appears nowhere, or a line of an in-build unit's files is placed less or more than once.
- **decl_index.py** writes migration/decl-index/<unit_id>.txt for every map unit, and with `--report FILE` a JSON summary. It reads the headers with its own parser. Every `#if` branch is kept except `#if 0`. A macro use is expanded when its expansion declares something, and a header without an include guard included inside a scope or a declaration is spliced in as an X-macro list. A unit's own headers are listed too when its rows write more than one module.

What the scripts read besides the source tree:

| File | What it holds | Read by |
|---|---|---|
| migration/design/evidence/rulebook/crates/crates-design.tsv | the crate map (prefix, crate, optional dir); migration/crates.tsv replaces it at sign-off | all three |
| migration/design/evidence/rulebook/crates/allowed-design.tsv | each crate's allowed crates, ARCHITECTURE §1.1 | depmap_seekdb.py |
| migration/design/evidence/rulebook/crates/out/crate_view.json, link_edges.json | the upward crate pairs the design's edge scripts counted | depmap_seekdb.py |
| islands.txt | kept C and C++, by prefix and island crate | all three |
| core-scope.txt | the prefixes whose units are core (s1-crates-core.md 2.1 rule 1), and the core API files s5 and s6 name | make_manifest_seekdb.py |
| core-placements.txt | core items the design places apart from the rest of their unit: a file or a line range, its crate, its module, and the design text that places it | all three |
| exclusions.txt | files, prefixes and line ranges that are not translated, each with its reason and design text | make_manifest_seekdb.py |
| aliases.tsv | written by depmap_seekdb.py: headers that are copies of a header in another unit | make_manifest_seekdb.py, decl_index.py |
| migration/crate-edges.tsv | the edge ledger (from, to, what, count, fix, how), once written | depmap_seekdb.py |

## Counts

**Files and units.** 6,678 C and C++ files are in the tree. The map has 6,587 of them as nodes: .h 3,626, .cpp 2,880, .ipp 40, .map 16, .c 11, .cc 8, .def 5 and .hpp 1. Of these, 6,467 are compiled by the reference build or reached from a compiled file, and 120 are not reached. The tree also holds 50 vendored files and 38 checked-in generated files. These are neither nodes nor misses. Includes found 1 miss, 8 stale paths and 168 headers from other platforms or absent libraries. The 6,587 files form 3,513 units, 3,422 of them in the reference build. 2 of the units are header copies of another unit's header (aliases.tsv).

**Edges by kind** (edges.tsv, 30,429 rows over 24,996 file pairs):

| include | own-header | definition | link | via-generated | island | from-island |
|---|---|---|---|---|---|---|
| 23,994 | 2,704 | 2,898 | 48 | 117 | 570 | 98 |

The crate check also counts 10 includes of rust/sql-nio/include/nio.h. It is the C header that sql-nio's build.rs writes, so these edges count against the sql-nio crate. Their kind is `rust-header`.

**Crate edges** between live files, as crate pairs and edges. The file level uses the crate map with the design placements. The placement level puts each file in the crate its manifest row writes it to.

| Class | File level | Placement level |
|---|---|---|
| listed (on the row) | 338 / 11,840 | 349 / 11,003 |
| upward-named (a pair the design counted; one of ARCHITECTURE §1.2's five fixes) | 107 / 994 | 119 / 1,068 |
| gone (ARCHITECTURE §1.2) | 6 / 10 | 6 / 10 |
| upward-unnamed (finding) | 6 / 6 | 10 / 15 |
| not-listed (finding) | 1 / 1 | 3 / 3 |
| uses-dropped (finding: translated code using x-dropped code) | 7 / 22 | 7 / 22 |
| uses-deferred (finding: translated code using standby) | 1 / 1 | 1 / 1 |
| deferred (edges out of standby) | 7 / 183 | 7 / 183 |

158 cross-crate edges touch a file outside the reference build and are not counted. crate-edges.tsv lists 473 crate pairs.

**Cycles.**

- File level: 27 cycles over 69 files through include edges alone, and 35 over 85 files through all edge kinds (cycles-files.txt).
- Unit level (order.txt): 1,033 batches in 23 levels, with 19 cycles holding 2,499 units, sizes 2,147, 196, 55, 33, 17, 17, 9, 3 and eleven of 2. These cycles form because a .cpp file includes headers from higher up.
- Order used in the redesign: the crate order (order-crates.txt) is acyclic by design, and inside each crate the units follow only the edges out of their headers. That gives 3,461 batches over the 37 crates that hold units. The cycles left inside crates are ob-base 14 and 2, ob-runtime 3 and 2, ob-values 2, storage-tx 4, 3, 3 and 2, storage-tablet 7 and 2, sql-expr 7, sql-engine 2 and 2, sql 2 and 2, pl 7, rootserver 2 and standby 3. Over the whole map, headers alone give 3,416 batches in 107 levels, and the largest cycle has 46 units.
- Directory level: 9 cycles among live files through includes, the largest with 110 directories and 4,100 files. Through all edges, one cycle holds 290 directories.
- Crate level: over every edge, island edges included, one cycle of 34 crates. After the edges the design cuts (upward-named and gone) are removed, one cycle of 18 crates is left at the file level (20 at the placement level). It runs only through the finding edges listed under "The crate check".

**Manifest rows** (`wc -l` counts the header line too):

| File | Lines | Rows |
|---|---|---|
| migration/manifest.tsv | 2,560 | 2,559: 2,283 `stem` and 276 `split` (81 heads, 195 pieces) |
| migration/core-manifest.tsv | 994 | 993 `subsystem` rows (75 of them pieces), 2 of them new-code modules of ob-platform |
| migration/not-translated.tsv | 257 | 256: 242 whole units (island 110, generated 3, dead 75, dropped 27, deferred 27) and 14 parts of translated units (island 1, data 3, dropped 9, deferred 1) |

- 54 files of 4,000 lines or more are split (18 in core rows, 36 in Step 3 rows). No row is over 3,999 lines or 30,000 tokens (characters / 4). The largest row is core/ob-schema/ob_schema_struct.p02, at 29,949 tokens.
- Every line of the 3,423 in-build units' files is placed exactly once across the three files. That count includes tools/ob_error/src/ob_error, which is translated though the build does not compile it. The only exception is 444 lines of src/sql/parser/parse_node.c, which are both island C and inputs of core/sql-parse-tree/parse_node.
- core-placements.txt holds 20 placements (9 whole files, 11 line ranges) with 11 targets. 2 header copies are dropped as aliases.

**Declaration index.** 3,513 files, 362,074,296 bytes. The median file is 69,693 bytes, p90 is 218,364 and the largest is 1,160,002. 179 units include no in-repo header. A unit has a median of 4 one-level headers, listed in full, and 15 deeper headers, listed only in part. 3,009 units have a deeper section, and the most deeper headers any unit has is 183. 123 units whose rows write more than one module (split units and units a placement divides) also list their own headers, with the module each declaration is written to. No `// rust:` name fails `(r#)?[A-Za-z_][A-Za-z0-9_]*`, and no header reports a parse problem.

## The island rule

The kept C and C++ of islands.txt is compiled from its src/ path and is never translated (ARCHITECTURE §9; defaults 17 and 32). It has 191 map files: geo-sys 173, sql-parser-sys 14 and vsag-sys 4.

- The map keeps island files as nodes. An edge into an island file has kind `island`, and an edge out of one has kind `from-island`. The evidence keeps the original kind.
- These edges take no part in the unit order, the header order or the crate order, since kept code is not translated.
- The crate check counts these edges as follows. An edge into an island file counts against the including crate's row. An edge out of an island file by definition or link counts against the island crate's row. Examples are ARCHITECTURE §9.2's forwarders: parse_node.c:1017-1018 to ob_backtrace.cpp, and sql_parser_base.h:69-70 to ob_ctype_simple.cc, are sql-parser-sys to ob-base and to ob-values, both on its row. An include out of an island file is `listed` when the island's row allows it, and `gone` otherwise, because the kept code compiles against the frozen header and makes no Rust edge. Island edges take part in the crate cycles. At the placement level, an island file sits in its island crate, not in its unit's crate.
- The manifests never take an island file as input, with one exception, which is design text. core-placements.txt ports five line ranges of src/sql/parser/parse_node.c into core/sql-parse-tree/parse_node: 58-175, 180-210, 368-414, 652-745 and 826-979. They are the ParseTree methods of s4-sql-front.md 1.1-1.3: the constructors, the deep copy, parsenode_hash and parsenode_equal, and the child helpers. The file stays compiled as island C.
- A unit made only of island files is one not-translated.tsv row, reason `island` (110 units). An island file inside a translated unit is a part row: src/sql/parser/parse_node.c, in unit src/sql/parser/parse_node.

## The crate check

1. **The design's table.** Run from migration/design/evidence/rulebook, `python3 -B check_crate_graph.py ../../ARCHITECTURE.md crates/allowed-design.tsv` prints: `acyclic: 39 crates, 419 allowed edges, every edge points to an earlier crate; topological order found for all 39 crates; rows equal crates/allowed-design.tsv`.
2. **The map against the table** (crate-check.txt). At the file level, 30 edges in 15 crate pairs are neither allowed nor named by the design as a cut:
   - ob-schema to storage-api: src/share/ob_rpc_struct.h:40 includes src/share/ob_est_row_count_record.h. s6-storage.md §1 puts ObEstRowCountRecord in storage-api, and fix 1 puts the DDL argument structs that use it in ob-schema.
   - storage-api to sql-ir, sql-engine, sql-codegen and storage-tablet: src/sql/engine/basic/ob_pushdown_filter.cpp:27-32, the SQL side of the filter executors. s5-execution.md §5 moves it behind `ScanHost`.
   - sql-exec to sql-nio: src/query/api/query/protocol/ob_mysql_rust_row.h includes nio.h. s1-crates-core.md 1.5 rule 5's trait in sql-exec covers it.
   - sql-codegen to storage-sstable (not-listed): src/sql/code_generator/ob_tsc_cg_service.h:19 includes the header copy of ob_index_block_util.h. The kept copy is in storage-sstable.
   - uses-dropped, 22 edges in 7 pairs: libeasy (src/oblib/easy, x-dropped). Examples are ObAddr(const easy_addr_t&) at src/oblib/lib/net/ob_addr.h:74, thread.h:25, utility.h:39, and the easy_* functions that src/oblib/rpc/frame/ob_net_easy.cpp defines. The design drops libeasy but names nothing to replace these uses.
   - uses-deferred, 1 edge: src/observer/ob_server.cpp:96 includes src/standby/standby_module.h.

   The placement level adds sql-parser to ob-schema (ob_pl_parser.h:22), sql-exec to storage-tx (ob_physical_plan_ctx.h), storage-tx to pl (ob_memtable.cpp:20), storage-tablet to sql-ir (ob_table_param.cpp) and storage-api to storage-tx and storage-sstable (data_plane/api headers placed with their .cpp). The one crate cycle left after the design's cuts runs only through these finding edges.
3. **The edge ledger.** migration/crate-edges.tsv does not exist yet. ledger-needed.tsv lists the 658 rows it needs, one per crate pair and target file, at the placement level:

   | Class | Rows |
   |---|---|
   | upward-named | 614 |
   | uses-dropped | 17 |
   | upward-unnamed | 14 |
   | gone | 9 |
   | not-listed | 3 |
   | uses-deferred | 1 |

   The script compares ledger-needed.tsv with the ledger on every run.

**Verdict.** The design's crate graph is acyclic. The map's crate graph is not acyclic until the ledger exists. Step 1's exit condition, "an acyclic crate graph", holds only once migration/crate-edges.tsv gives each of the 658 rows a fix, including a fix for the 30 finding edges above.

## Decisions taken on 2026-09-25

These choices were taken under decisions.md row 5c, which says to take the recommended option, record it and tell the developer. The developer may overrule any of them.

1. **A row is capped at 30,000 tokens** as well as 4,000 lines. PLAN §6 sizes units at 30K tokens. A unit over the cap splits even when no file of it reaches 4,000 lines. A head holds only the headers that fit under both caps. A header over the caps is cut at class boundaries, and .cpp and .ipp bodies go to pieces. RULEBOOK §4, s1-crates-core.md 5.2 and ARCHITECTURE §14 rule 2 were amended to match.
2. **The completeness check works on lines.** Every line of every in-build unit is placed exactly once. not-translated.tsv gains part rows for island files, x-dropped and standby files, header copies and excluded ranges inside translated units. A unit can have rows in more than one file. RULEBOOK §4 and s1-crates-core.md 5.3 rules 7 and 8 were amended to match.
3. **The parser's memory checks stay no-ops.** The reference build compiles src/sql/executor/ob_memory_tracker_wrapper.cpp, which is empty past its includes, and not src/sql/parser/ob_memory_tracker_wrapper.cpp. So the weak `check_mem_status` and `try_check_mem_status` of parse_node.c:177-178 run in the reference, and they run in the Rust build too. src/sql/parser/ob_memory_tracker_wrapper stays `dead`. ARCHITECTURE §1.1 row 22, §9.2, §11 (thread-locals) and §15 were amended, and so was RULEBOOK §3's sql-parser-sys row.
4. **The declaration index lists what a unit uses from deeper headers**, the option review B recommended. One-level headers stay in full. Deeper headers in the include closure show only the declarations whose names the unit's files use, the members of those classes that the files use, and the types that the selected declarations name (two rounds). Headers of units that are not translated are left out, except island and generated headers.
5. **Members that a macro generates are listed and take overload numbers.** A macro use is expanded when the full expansion, `##` pasting included, declares something. That covers DEF_PARAM, OB_UNIS_VERSION, TO_STRING_KV, DISALLOW_COPY_AND_ASSIGN and the rest. So a hand-written overload after OB_UNIS_VERSION_V becomes `serialize_2`.
6. **When two headers in different units share an include guard, only one is kept.** C++ sees only one of them in any translation unit. The kept header is the one whose unit defines its declarations; otherwise, the one other units include most. The copy is dropped and its includers are pointed at the kept header in the unit order, the crate check and the declaration index. The two ob_item_type.h files are not identical. The 2,017 enumerators they share have the same values. The copy, src/objit/include/objit/common/ob_item_type.h, also has T_AUTO_PARTITION, T_LS_ATTR_LIST and T_ALTER_LS, which nothing uses, and defines ObCacheType itself. The kept header also has T_RESERVED_3791 and T_FLUSH_PRIVILEGES, and includes ObCacheType from share/cache/ob_cache_type.h. aliases.tsv lists these differences.
7. **Dropped and deferred edges out of translated code are findings**, review A's first option for finding 12. They need a ledger row.
8. **ObIndexBlockScanEstimator's whole unit goes to storage-sstable.** The walk is declared in the header, and only lines 31-44 (ObPartitionEst) go to storage-api. s6-storage.md §1 says "the map sends the rest of its header to storage-tablet", but the walk's class and its methods must share a crate.
9. **ObBitmap and ObQueryCtx follow RULEBOOK §4's home-module table and s5-execution.md §1.** src/oblib/lib/container/ob_bitmap goes to storage-api. Only storage and the filter header use it. ObQueryCtx, src/sql/ob_sql_context.h:550-742 and ob_sql_context.cpp:498-520, becomes a core row in sql-ir. The reviews did not name these two, but the placement tables they cited do.

## What the reviews changed

- **Placements.** crates-design.tsv changed one row: src/sql/engine/basic/ob_pushdown_filter now goes to storage-api instead of sql-exec. It gained twelve rows:
  - the nio.h glue to observer (s1-crates-core.md 1.5 rule 7): ob_sql_request_operator.cpp, and in obmysql ob_sql_nio_server, ob_sql_sock_handler, ob_sql_sock_session, ob_login_info and ob_mysql_packet_storage;
  - the filter headers ob_pushdown_filter and ob_external_pushdown_filter, ob_est_row_count_record, ob_pushdown_aggregate_protocol and ob_bitmap to storage-api;
  - ob_index_sstable_estimator to storage-sstable.

  core-placements.txt keeps ob_sql_request_operator.h in ob-runtime and ob_tablet_scan.h in storage-api. It puts ObPartitionEst in storage-api, ObQueryCtx in sql-ir, and the five vector distance kernel headers in ob-simd, as core rows. core/sql-parse-tree/parse_node gets the ParseTree ranges of parse_node.c and the ParseNode hash traits of ob_hashutils.h:84-92, :729-745 and :818-835. The GeoJSON visitor becomes core/geo-sys/ob_wkb_to_json_bin_visitor, the Rust handler behind obgeo_rs_json_event. core-scope.txt marks the core API files that s5 and s6 name, and sql-parse-tree.
- **Exclusions** moved from command-line flags to exclusions.txt. The IK stop-word list (ob_ik_dic.cpp:276014-276017) is now `data` like the other two word lists.
- **Crate check**: depmap_seekdb.py changed in these ways:
  - line-range placements count at the lines each edge names;
  - header copies count as the kept header;
  - island files sit in their island crate at the placement level;
  - link and definition edges out of islands count against the island's row;
  - island edges count in the cycles;
  - translated code that uses dropped or deferred code is a finding;
  - nio.h includes count against sql-nio;
  - ledger-needed.tsv is new;
  - the default for `--jobs` is 4.
- **Declaration index**: decl_index.py changed in these ways:
  - deeper headers are listed with what the unit's files name (decision 4), and a unit's own headers are listed when its rows write several modules;
  - a macro use is expanded when its expansion declares something, and a macro that opens braces another macro closes (LOG_MOD_BEGIN and LOG_MOD_END) is expanded over the whole sequence;
  - a header without an include guard is spliced as an X-macro list wherever it is included inside a scope or a declaration, with the macro definitions in force at each point, and a declaration that any other include cuts is not listed (none is, in this tree);
  - a literal-aware whitespace squash keeps spaces inside string literals;
  - specialization members and out-of-class nested classes get valid Rust names;
  - a same-unit .ipp is listed in its header's section;
  - extern "C" linkage is noted, and so is the file and crate that define a re-declared function;
  - a declaration split by `#if`/`#else` gets one entry per branch;
  - attributes after a closing brace and `#pragma pack` are recorded;
  - generated headers get their crate and generated module;
  - header copies are shown as the kept header;
  - each declaration of a header written to several modules gets its module;
  - a split .cpp's macros are listed for the pieces after the one that defines them;
  - the note on a spliced X-macro list names the list's own unit and module.

## Open items

1. **Write the edge ledger**, migration/crate-edges.tsv, from ledger-needed.tsv, and give the 30 finding edges their fixes. For example: a trait or a move for ObEstRowCountRecord; the ScanHost side of ob_pushdown_filter.cpp; a `row` fix or a move for sql-codegen to storage-sstable; a replacement for easy_addr_t and the other libeasy uses; a crate for the disabled standby module.
2. **Decide which lines move for the design's partial moves.** The SQL side of ob_pushdown_filter.cpp, ObPushdownFilterConstructor and black-filter evaluation, sits with its unit in storage-api for now. s5-execution.md §5 moves it into sql-exec behind ScanHost, but no row names its lines. "The IR's parts of ObTransformUtils", which ARCHITECTURE §1.2 fix 1 moves into sql-ir, name no lines either. The core API sessions decide both and add them to core-placements.txt.
3. **ob_vector_op_common.h**, the NEON helpers the ob-simd kernels include, stays in storage-search. Its upward-named edge from ob-simd needs a ledger row; a fix-1 move to ob-simd is the likely one.
4. **Section files that still describe the parser's memory-check slot** need to follow ARCHITECTURE §9.2 (decision 3): s3-memory.md T5, its table row at :434 and objection 1 at :680; s7-islands-unsafe.md 7.5 rules 2-3 and objection 6; s4-sql-front.md :795; RESOLUTIONS.md s3-1 and C-20; s1-crates-core.md 1.3 and objection 1 (ob_memory_tracker_wrapper's row, now for a dead file).
5. **The declaration index still lacks two things RULEBOOK §0 promises a Step 3 unit**: each declaration's full Rust signature, and the parameters that RULEBOOK 2.4 adds.
6. **PLAN §6 describes the declaration index too narrowly.** It says the index holds "the declarations its one-level includes provide"; the index now also lists what a unit's files name from deeper headers (decision 4).
7. **Several modules of new code have no manifest row.** These are ob-epoch, ob-errno's ObError, ObResult and cfmt, the island crates' Rust wrappers, and the GeoJSON traversal as new C++ in rust/geo-sys/cpp/. Only ob-platform's libc and allocator modules have rows.
8. **The design's figures predate these changes.** ARCHITECTURE §1.1's Lines column and s1-crates-core.md 1.3's "212 lines" come from crates-design.tsv before the rows above were added, and so do the design's crate_view.json and link_edges.json.
