# Research 08: the C++ that stays behind a C ABI

**Question.** Which C++ stays behind a C ABI (PLAN.md section 3, "The C++ that stays"), what kept oblib code each island needs, how island memory is charged on macOS, which third-party libraries in deps/ have Rust equivalents, and which ABI rules the islands follow, with the sql-nio boundary reviewed as prior art. It feeds the Step 1 bullet "the island ABIs, the kept C++ oblib subset under share/geo and S2, and how island memory is charged on macOS" (PLAN section 6) and PLAN section 8, item 19.

**Base and method.** Source facts are from 834bbee1e (`git diff --stat 834bbee1e HEAD -- src` in the worktree prints nothing). Link and include facts come from the objects of the reference build at /Users/colin/seekdb-dev/ref-834bbee1e/build_release: its compiler depfiles (`*.o.d`) and `llvm-nm -g -P` from deps/3rd's LLVM 17, with each undefined symbol resolved against the other objects, the deps/3rd archives and the SDK 26.2 `.tbd` stubs. Nothing was built or run. Coverage figures are from /Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.summary.json (the coverage README's data, 076eb309b). The scripts are in migration/design/evidence/ (incl.py, linkscan.py, island_first_level.py, depclosure.py, objclosure.py).

## 1. Short answer

1. **Two of Decision 13's pieces are C++ islands in the full sense:** geo (share/geo with boost.geometry, S2 and protobuf-c) and vsag (src/oblib/lib/vector plus the bridge classes that sit in query/api and storage/allocator today: a filter, two allocators and the stream buffers). **ICU is not a C++ shim:** the kept part is ICU itself, used through 15 functions of its C API, and seekdb's wrapper around it is SQL-tier code that is translated. **The parser's C core is C**, with about 25 callbacks into code that becomes Rust.
2. **The kept oblib subset is small at link level and large at compile level.** The 29 geo objects need 124 out-of-line seekdb symbols (30 of them logging, 20 memory); the vsag object needs 38. At compile level geo reaches 196 oblib/share files (77,558 lines) and vsag 118 files (38,448 lines). Linking the real oblib `.cpp` files behind those symbols is not a subset: an object-level closure reaches 363 of 634 objects. Recommendation: freeze the headers, and replace the out-of-line functions with a few C++ files that forward logging, allocation, charset and number formatting to Rust; move the island's uses of JSON binary, ObNumber, object casts and LOB text to the Rust side.
3. **macOS charges island memory only through explicit allocators today.** The malloc hook is Linux-only and jemalloc ignores memory labels. Recommendation: the same in Rust. Charge through allocation callbacks whose first argument is the receiver, the object being charged (the caller's arena, the vector budget). Leave malloc/new inside the libraries uncharged, as today. No malloc interposition anywhere, and no freeing across sides.
4. **The judge pins some third-party bytes.** Keep zlib 1.2.13 for `COMPRESS()` and OUTFILE GZIP/DEFLATE and the vendored zstd 1.3.8 for OUTFILE ZSTD, as C code. That contradicts PLAN section 3's "vendored zstd ... become crates". relaxed-rapidjson has no Rust equivalent (non-standard flags, a number parser that is not correctly rounded). Port it with a C++ test oracle rather than make it a sixth island. The other libraries have Rust equivalents or follow their island.
5. **Keep most of sql-nio's rules.** Keep the generated header, named functions, receiver-first callbacks, same-name structs, the two-layer crate shape and the rule that callers guarantee valid pointers and lengths. Change the naming and the return codes. C++ now implements most functions and has no C names to follow, and `vsag_` is already taken by vsag's own C API. Return exact OB codes, not 0/−1. Add four new rules: a catch-all at every C++ entry, a thread rule, an ownership rule, and a rule that islands compile with the reference toolchain and flags.

## 2. What the C++ does today

### 2.1 The pieces

| Piece | Code | Used from outside today | Judged cases | Functions run by the 272 cases |
|---|---|---|---|---|
| share/geo (with S2) | 180 tracked files: 86 .cpp, 82 .h, 10 .ipp, 1 .c, 1 .proto; 47,033 hand-written lines (`git ls-files src/share/geo`) | 94 files include 31 of its headers (78 in sql/engine); 111 files use 64 of its 282 type names; 81 distinct `Class::member` references (21 `ObGeoFuncType` values, 41 `ObGeoTypeUtil`, 10 `ObGeoBoxUtil`, 3 `ObGeoMVTUtil`, 6 others) | geometry 39 (judge/census/portable.txt) | 55.0% (1,903/3,461); the `ob_geo_func_*` files 27.0% |
| S2 part of share/geo | ob_s2adapter.*, ob_geo_to_s2_visitor.*: 4 files, 1,343 lines | `ObS2Adapter` at ob_das_domain_utils.cpp:166 and ob_range_generator.cpp:1624-1816; `get_cellid_mbr_from_geom` at ob_table_scan_op.cpp:3527; `ObSpatialMBR::filter` per index row at ob_das_spatial_scan_iter.cpp:131-137 | within geometry | 85.3% |
| vsag adaptor | src/oblib/lib/vector: 4 files, 1,991 lines | 88 lines in 8 files call 17 distinct `obvectorutil` functions | vector_index 21 | 75.9% |
| ICU regex | ob_expr_regexp_context.{h,cpp}, 954 lines | REGEXP functions; 6 tests use REGEXP/RLIKE (evidence-full.md, deps C24) | expr.func_regexp | 82.1% |
| Parser C core | grammars: SQL 18,723 lines, PL 2,976, FTS boolean mode 250; sql_parser_base.c 333; parse_node.c 1,024 | ObParser, ObPLParser, ObFastParser (ported to Rust by Decision 13) | all cases | not instrumented (coverage README) |

**geo today.** SQL code builds `ObGeometry` objects (256 uses in 61 outside files). It calls `ObGeoFunc<ObGeoFuncType::X>` for 22 kernels (ob_geo_func_register.h, Area … DissolvePolygon) with an `ObGeoEvalCtx` that holds a `lib::MemoryContext` (ob_geo_func_common.h:63-66, :195). SRS objects come through an existing interface, `ObISrsProvider`/`ObISrsSnapshot`/`ObSrsCacheGuard` (ob_srs_provider.h:28-93), which observer/omt/ob_srs_service.cpp implements. Errors come back as codes plus values: `ObGeoErrLogInfo` is three doubles (ob_geo_common.h:186). The 104 GIS expression files (14,674 lines, 198 `LOG_USER_ERROR/WARN` lines) format the user messages themselves. share/geo has a single `LOG_USER_ERROR` (ob_geo_mvt.cpp:108). The couplings that do not fit an island are:
- MVT, which includes LOB access, object cast and JSON binary (ob_geo_mvt.cpp:19-24);
- the GeoJSON visitor, which writes `ObJsonBin` (ob_wkb_to_json_bin_visitor.*, 1,116 lines);
- WKT and SRS printing, which call MySQL's dtoa port `ob_gcvt`/`ob_fcvt` (ob_geo_to_wkt_visitor.cpp:49, :132; ob_srs_info.cpp:421-445);
- WKT parsing, which calls `ObCharset::strntod` (ob_wkt_parser.cpp:202).

**S2 today.** Cell ids are the first column of each spatial-index row (ob_das_domain_utils.cpp:193, "Index row[cellid_obj][mbr_obj][rowkey_obj]"). They come from `S2RegionCoverer` with max_cells 4, max_level 30, level_mod 1 (ob_geo_to_s2_visitor.h:54-56). `ObSpatialMBR` is four doubles plus flags with `OB_UNIS_VERSION(4)` (ob_s2adapter.h:58-60), carried in the scan parameters (ob_tablet_scan.h:26, :346). Its `filter` uses `S2LatLngRect::Contains/Intersects` for geographic SRSs (ob_s2adapter.cpp:25-103). libs2.a leaves 22 `BN_*` symbols and `CRYPTO_free` (OpenSSL libcrypto), 43 abseil symbols and the C++ exception runtime undefined (`llvm-nm -u lib64/libs2.a`). `ObWkbToS2Visitor::reset` calls a member's destructor and then reuses the object (ob_geo_to_s2_visitor.cpp:612; reuse at ob_s2adapter.cpp:349). This is a known latent defect, and it stays as it is inside the island.

**vsag today.** The `obvsag` namespace declares 33 functions (27 names, ob_vsag_adaptor.h) and `obvectorutil` 24 (18 names, ob_vector_util.h). The API passes C++ types: `std::string` (ob_vsag_adaptor.h:119), `std::ostream/istream` (:151-152), and C++ objects as `void*` (allocator, filter, iterator context). The bridge classes are:
- `ObVsagLogger : vsag::Logger`, 7 virtuals (ob_vector_util.h:35);
- `ObVasgFilter : vsag::Filter` (ob_vsag_adaptor.cpp:87);
- `ObHnswBitmapFilter : obvsag::FilterInterface` (ob_vector_index_adaptor.h:147);
- `ObVsagSearchAlloc : vsag::Allocator` (ob_vector_index_adaptor.h:237) and `ObVsagMemContext : vsag::Allocator` (ob_vector_allocator.h:71);
- `ObStreamBuf/ObOStreamBuf/ObIStreamBuf : std::streambuf` (ob_vector_index_serialize.h:32, :51, :83).

`NO_ENOUGH_MEMORY` maps to `OB_ALLOCATE_MEMORY_FAILED` (ob_vsag_adaptor.cpp:61-62). vsag's own C API is 20 `vsag_*` functions with no allocator or logger hooks (deps/3rd .../include/vsag/vsag_c_api.h; evidence-full.md, deps C10), and libvsag_static.a defines them. On macOS arm64, vsag brings 14 archives, including libgfortran, libquadmath, libgcc and libomp (src/oblib/lib/CMakeLists.txt:111-126). libvsag_static.a references `std::thread` and `pthread_create`, so vsag can run code on threads it starts. Android lists vsag 0.18.0 (deps/init/oceanbase.android.arm64.deps:19); the other platforms list 1.1.0.

**ICU today.** `ObExprRegexContext : ObExprOperatorCtx` includes ob_expr_operator.h and ob_raw_expr_util.h (ob_expr_regexp_context.h:32; .cpp:19), so the file belongs to the SQL tier. It calls 15 ICU C functions: `uregex_open/close/setText/getText/find/findNext/start/end/appendReplacement/appendTail/setStackLimit/setTimeLimit`, `u_strFromUTF8`, `u_strToUTF8` and `u_errorName`. The ICU C symbols carry the version suffix (`llvm-nm` on libicui18n.a shows `_uregex_open_69`). The build links the stub data library (src/oblib/CMakeLists.txt:320). ICU's error positions reach results ("Syntax error in regular expression on line 1, character N." in .result files).

**Parser C core today.** It calls back into C-linkage functions implemented in C++: 15 in parse_malloc.h, 2 in parser_utility.h, 2 in ob_memory_tracker_wrapper.h, 1 in parse_node_hash.h, 3 in ob_parser_charset_utils.cpp, and `ob_parse_binary_simd` (ob_parse_simd.cpp:204). The existing signatures put the receiver last (`parse_malloc(nbyte, malloc_pool)`, parse_malloc.cpp:90). The core exits through `longjmp` from a thread-local `jmp_buf` (sql_parser_base.c:104-106). The FTS boolean-mode parser has one C entry, `fts_parse_docment(input, length, pool, result)` (fts_parse.h:35), called from ob_das_text_retrieval_eval_node.h. The ParseNode report owns the details.

### 2.2 Exceptions

834bbee1e compiles with C++ exceptions enabled: no `-fno-exceptions` appears in the compile commands, and `-D_NO_EXCEPTION` is a seekdb macro (src/oblib/CMakeLists.txt:68). share/geo has 8 `try` blocks in 3 files. `ob_boost_geometry_exception_handle` (ob_geo_dispatcher.h:1314-1366) catches 18 named exception types, then `std::exception`, then everything else. `std::bad_alloc` becomes `OB_ERR_STD_BAD_ALLOC_ERROR`, not -4013 (:1319-1320), and boost exceptions become `OB_ERR_BOOST_GEOMETRY_*`. On Windows, `invalid_input_exception` takes the place of `overlay_invalid_input_exception` (:1347-1352). The S2 visitor catches std exceptions (ob_geo_to_s2_visitor.cpp:619-647). The vsag adaptor has one `try/catch` (ob_vsag_adaptor.cpp:221-230); its other calls into vsag are not guarded. The rest of src has 22 `catch` lines in 13 files.

### 2.3 The kept oblib subset, measured

**Compile level** (the union of the depfiles of the 29 geo objects and of the vsag object):

| | geo (29 objects: 3 unity C++ chunks, 1 unity C chunk, 25 func objects) | vsag (unity_oblib_lib_oblib_lib_ob_vector_util_0) |
|---|---|---|
| src files reached (own) | 374 (178) | 122 (4) |
| other src files, lines | 196 files, 77,558 lines (190 in oblib, 6 in share) | 118 files, 38,448 lines (all oblib) |
| largest groups | lib/utility 24 files (9,929 lines), lib/hash 11 (6,318), common/object 2 (5,511), lib/container 13 (4,895), lib/oblog 12 (4,772), lib/allocator 14 (4,225), common/number 1 (3,604), common/json_type 4 (3,099) | lib/utility 16 (8,106), lib/oblog 12 (4,772), lib/hash 6 (4,070) |
| third-party headers | boost 2,713 files (446,554 lines), abseil 115, OpenSSL 62, S2 68, protobuf-c 1 | vsag 24, OpenSSL 62 |

**Link level.** These are the undefined symbols the island objects need from outside themselves. Header-inline oblib code is compiled into the island objects: the geo objects define 22,201 external symbols.

| Needed from | geo | vsag |
|---|---|---|
| logging: `ObLogger`, `ObRingBufLogWriter`, `logdata_printf` | 30 | 30 |
| memory: `ObMallocAllocator`, `ObFIFOAllocator`, `ObMallocHookAttrGuard`, the malloc-backend switch, `MemoryContext::root`, `je_malloc/free/realloc` | 20 | 0 |
| `ObJsonBin` (JSON binary) | 15 | 0 |
| `ObStringBuffer` | 11 | 0 |
| small utilities: `databuff_printf`, `lbt`, `ObFastFormatInt`, `get_tp_switch`, hash `PRIME_LIST`, `ob_abort`, thread ids | 11 | 3 |
| stack and `SMART_CALL`: `check_stack_overflow`, `jump_call`, `ProtectedStackAllocator` | 9 | 0 |
| latches | 8 | 0 |
| time | 7 | 5 |
| charset and number formatting: `ObCharset::strntod/strntoll/get_system_collation`, `ob_gcvt`, `ob_gcvt_strict`, `ob_fcvt` | 6 | 0 |
| `ObObjCaster::to_type`, `ObTextStringIter` (3), `ObHexUtils::hex` | 5 | 0 |
| `ObNumber` | 2 | 0 |
| **seekdb total** | **124** | **38** |
| S2 / vsag / protobuf-c | 32 / – / 2 | – / 6 / – |
| libc++ / libc, libm and TLS runtime | 89 / 55 | 27 / 17 |

**Linking the real files behind those symbols** was checked with a closure over objects: each object that defines a needed symbol is added, and the step is repeated. The closure reaches 363 of the 634 objects and 1,945,005 source lines, SQL and storage included. Unity chunks make this an upper bound, but the cause is real. `ObObjCaster::to_type` is the entry to the cast matrix, `ObTextStringIter` reads LOBs, and `ObLogger` and `ObMallocAllocator` bring the log writer and the obmalloc backend.

### 2.4 How island memory is charged today, and on macOS

- **The malloc hook is Linux-only.** src/oblib/lib/CMakeLists.txt:34-40 ("malloc_hook is only part of the Linux production runtime") and src/observer/CMakeLists.txt:17-23 leave it out on macOS.
- **On macOS all malloc goes to jemalloc through zone promotion.** `inner_main` calls `configure_darwin_malloc_zone` (main.cpp:647-654; ob_malloc.cpp:178-195), and the link keeps `_je_zone_register` (src/observer/CMakeLists.txt:112-116).
- **Under the jemalloc backend, memory labels charge nothing.** `ob_malloc` calls `jemalloc_malloc` and uses the attribute only in a log line (ob_malloc.h:118-125). `ObMallocHookAttrGuard` (103 lines in 36 files) therefore has no effect on macOS. That includes geo's `"BoostCache"` guards (ob_geo_func_transform.cpp:46-51, :80-85) and the `"GISModule"` guard inside `ObGeoBoostAllocGuard` (ob_geo_utils.h:422-439).
- **What is charged on macOS** is memory that passes through an explicit allocator:
  - geo's `ObIAllocator` and `MemoryContext` arguments, which are children of the query's context (the `WITH_CONTEXT` blocks at ob_geo_dispatcher.h:1137-1138 and :1229-1230; `ObGeoBoostAllocGuard::init` at ob_geo_utils.cpp:2737-2748). The query tracker reads their `tree_mem_hold` for -11049 (sql/executor/ob_memory_tracker.cpp).
  - vsag, through `ObVsagMemContext::Allocate`, which adds to `all_vsag_use_mem_` (ob_vector_allocator.cpp:54-70), and `ObVectorMemContext::alloc`, which checks the vector limit and fails with -7603 (:131-150; ob_errno.def:1680).
  - CRoaring, through the roaring memory hook (share/roaringbitmap/ob_rb_memory_mgr.cpp).
- **Uncharged on macOS:** malloc/new inside boost, S2, abseil, ICU, libxml2, and the parts of vsag that bypass `vsag::Allocator`.

### 2.5 Other candidates

- **The IK tokenizer and the fulltext parsers.** src/storage/fts holds 71 files and 10,526 lines, plus the 276,043-line ob_ik_dic.cpp, with five parsers: whitespace, ngram, ngram2, beng and ik. They use no native library (evidence-full.md, search-vector-gis C14). Their includes reach the KV cache, table access, transactions and an inner-SQL table lock (storage/tablelock/ob_lock_inner_connection_util.h), so as a C++ island they would bring the storage core with them. At 834bbee1e the dictionary comes only from the compiled-in arrays: `check_need_load_dic` sets false (ob_dic_loader.cpp:144-149), and the hub builds from `build_cache_from_ik_dict` (ob_ft_dict_hub.cpp:76). The vault's storage note on retrieval and vectors described inner-table loading. That was already wrong at its base 073e9b2f1, and the note was corrected on 2026-09-24. The judge runs 15 fts_index cases, and the 272 cases execute 88.0% of storage/fts's functions (IK: 94.4%). `TOKENIZE()` exists as SQL (ob_expr_tokenize.cpp:44-45).
- **relaxed-rapidjson**, used by ob_json_parse.cpp:
  - it parses with non-standard flags (:23-29);
  - it reports errors with `GetParseError_En` and an offset (:122-125);
  - the default path is `Parse<kParseInsituFlag>`, without `kParseFullPrecisionFlag`, so numbers go through `StrtodNormalPrecision` (installed reader.h:1867-1870), which is not correctly rounded.

  The reader is 2,417 lines, with internal/strtod.h at 293 and error/en.h at 122.
- **Bytes the judge compares** (judge/golden-bytes-scope.md): `COMPRESS()` uses zlib's `compress()` (ob_expr_compress.cpp:89 on macOS); OUTFILE GZIP/DEFLATE uses zlib's `deflateInit2` at level 5 (ob_select_into_basic.cpp:258); OUTFILE ZSTD uses the vendored zstd 1.3.8's `ZSTD_compressStream2` (ob_zstd_wrapper.cpp:324). The MySQL wire's compressed frames already come from Rust: flate2 1.1.10 with miniz_oxide (rust/Cargo.lock:255-261).

### 2.6 Third-party libraries (macOS 15 arm64 list, deps/init/oceanbase.macos15.arm64.deps)

| Library | Used by (at 834bbee1e) | Judged? | Rust side |
|---|---|---|---|
| boost 1.74.0 | code in share/geo only: 38 files use it, 6 include it directly; 23 outside files include ob_geo_func_register.h, whose header chain brings it in | yes, geometry | stays with the geo island |
| s2geometry 0.10.0, abseil 20211102 | share/geo; abseil only for S2 (20 archives, src/oblib/CMakeLists.txt:230-250) | yes | stays with the geo island |
| openssl 1.1.1u | S2 (22 `BN_*`), curl, gRPC, and 9 seekdb files (encrypt, MD5, telemetry) | MD5/SHA/AES output (2 tests, evidence deps C24) | libcrypto stays while S2 does; seekdb's own functions move to RustCrypto |
| protobuf-c 1.4.1 | ST_AsMVT (share/geo MVT, 776 lines plus 651 generated) | no test | prost, with MVT moved to Rust |
| vsag 1.1.0 with its 14 archives | vector index | yes | stays as the vsag island |
| icu 69.1 | ICU regex only | yes | ICU C API through bindgen (versioned names) |
| zlib 1.2.13 | COMPRESS, CRC32, OUTFILE, LOAD DATA | yes, bytes | link the same zlib (for example libz-sys against the deps archive). Assumption: miniz_oxide and zlib-rs do not give identical output at levels 5 and 6 (not checked). `CRC32()` needs only a correct CRC-32 |
| zstd 1.3.8 (vendored C, 25,154 lines) | OUTFILE ZSTD, storage blocks | yes, OUTFILE bytes | keep the C source, compiled with cc |
| relaxed-rapidjson 1.0.0 | JSON parsing (7 files) | JSON values and error text | port the reader (see 4.1) |
| croaring 3.0.0 | vector-index bitmaps (4 files; not persisted, evidence C7) | no | croaring crate (same C library), memory hook into the vector budget |
| sqlite 3.38.1 | meta.db (3 files call it) | no (Decision 11) | rusqlite |
| libxml2 2.10.4 (+ xz) | XML functions (1 SAX file reads parser internals) | no test; 4.1% of oblib/common/xml functions run | keep as a C library over bindgen for the first gate |
| libcurl 8.12.1 | AI functions, embedding, telemetry (5 files) | no: the 5 judged ai_function cases are model DDL and `ai_prompt` formatting | reqwest or ureq |
| grpc 1.46.7 | standby (16 files, `OB_ENABLE_STANDBY` ON, CMakeLists.txt:29) | no | tonic/prost |
| fast-float 6.1.3 | ob_dtoa.cc:370, ob_array_cast.cpp (3 sites) | yes, casts | Rust port of fast_float (correctly rounded, partial parse) |
| jemalloc 5.3.1 | via seekdb-jemalloc-sys (deps/external/Cargo.toml) | no | already a Cargo crate |

## 3. Constraints that bind this topic

- **Decision 13 (a):** the parser's C core, vsag, S2, share/geo with boost.geometry, and ICU regex stay behind a C ABI for the first parity gate. `ObParser`, `ObPLParser` and `ObFastParser` are ported. The islands are revisited after parity, because they keep a C++ runtime inside the product.
- **Decision 14 (b):** `unsafe` is allowed only in named crates, island shims among them, so each island's FFI lives in its own crate.
- **Decision 12 (b):** general OOM aborts. The vector module is a named budget owner, and vsag `NO_ENOUGH_MEMORY` stays a typed -4013.
- **Decision 6 (b):** comparison is exact. The geometry tests have 2,834 ERROR lines (report §3), so the codes and numbers boost produces are the contract.
- **Decision 11 (a):** data directories are not compatible. That weakens the "persisted format" reason for S2 and vsag, but judged results still need the same cell ids (spatial-index order) and the same vsag graph (ANN result sets). One vsag case is already quarantined for ANN ordering (judge/quarantine.tsv).
- **Decision 7:** the first release is macOS arm64. wasm, Android, Linux and Windows must stay possible: islands will need Emscripten builds, Android's older vsag, and the Windows `#ifdef` in the dispatcher.
- **Decision 8's guideline:** no malloc hook, and no process ownership in engine crates.
- **Decision 16 (a):** one stable toolchain.
- **Decision 1 (b):** binary size is not a criterion.
- **PLAN section 3** requires, for every island: a `try/catch` at every `extern "C"` entry; OpenSSL for libs2.a; and a kept oblib subset sized at link level, listed with its build, its allocator hook, and a rule that its charset behavior matches the Rust charset code.
- **PLAN section 3, dropped list:** "vendored zstd and xxhash, which become crates", which conflicts with judge item 8 (section 2.5).
- **PLAN section 3, item 7:** no Rust frame between the parse entry and a callback.
- **PLAN section 3** (and the section 7 token rows): Step 3 adds 25-50 island-shim units.

## 4. Options and recommendation

### 4.1 Which pieces are islands

| Option | Cost | Risk |
|---|---|---|
| A. Treat all five of Decision 13's items as C++ shims, including a shim over ob_expr_regexp_context | That file includes the SQL expression framework (2.1) | Brings ObExpr headers into an island |
| **B. Two C++ islands (geo, vsag); ICU and the C libraries through their C APIs; the parser C core as C** | ICU gets a small Rust wrapper over 15 functions | ICU's versioned symbol names (`_69`); bindgen resolves them |

**Recommend B.** It is Decision 13's list read at the level of the code. The IK tokenizer and fulltext parsers stay as translated leaves (PLAN section 3). Family 13's IK corpus can compare the two builds through `TOKENIZE()`, so no C++ tokenizer needs to remain in the product. The FTS boolean-mode grammar goes with the parser's C core, since it is bison output behind one C entry. For relaxed-rapidjson, porting (about 2,800 lines of header to follow, with `StrtodNormalPrecision` and the error table copied exactly, checked against the C++ reader in a test-only harness) is cheaper than a sixth island, and it keeps Decision 13's list unchanged. Keep zlib 1.2.13 and zstd 1.3.8 as C code, as in 2.6.

### 4.2 The geo boundary

| Option | Cost | Risk |
|---|---|---|
| A. Keep today's C++ API (`ObGeometry`, `ObGeoEvalCtx`, visitors) and put the GIS expression code in the island too | 14,674 more lines stay C++, with `ObExpr` inside the island | The expressions are SQL tier; the island would need the execution framework |
| **B. A new bytes-in/bytes-out API: WKB bytes plus SRS and cached-geometry handles in; result bytes, values and error codes out** | About 76 entry points and 10 callbacks (report §3's estimate) | Width. The measured surface is 22 kernels and 81 member references, but about a third are WKB header reads (`get_srid_from_wkb`, `get_type_from_wkb`, `is_3d_geo_type`, name tables) that Rust can do itself |

**Recommend B**, with:
- SRS items parsed by the island into opaque handles, which the Rust SRS service owns;
- constant-argument geometries (`ObCachedGeom`, 17 uses in 5 outside files) as create/evaluate/destroy handles;
- `ObSpatialMBR` as a `#[repr(C)]` value, with its per-row `filter` still called through the island, because the geographic case uses S2's rectangle code;
- GeoJSON output sent as JSON events (begin object, key, double, …) through callbacks, so Rust builds its own JSON binary from the same doubles;
- MVT moved to Rust (prost), with its geometry steps (`affine_transformation`, `snap_to_grid`, `simplify_geometry`) as island calls.

The coverer options and other constants that decide judged output stay inside the island, not in the ABI.

### 4.3 The kept oblib subset

| Option | Cost | Risk |
|---|---|---|
| A. Link the real oblib/share `.cpp` files that define the 124 + 38 symbols | Little new code | Their own dependencies reach the obmalloc backend, the log writer and the cast matrix (2.3); two copies of core services in one process |
| **B. Compile the islands against the unchanged 834bbee1e headers (196 + 118 files), and link a small set of replacement `.cpp` files that define the same out-of-line symbols** | About 124 symbols to implement: logging forwards formatted lines to Rust; allocation and `MemoryContext` pages call a Rust allocation function; `SMART_CALL` checks the stack depth; charset and dtoa forward to Rust; leaves such as `ObStringBuffer`, `databuff_printf`, `ObFastFormatInt` and `PRIME_LIST` are copied as they are | The replacements must match the header-inline code's expectations (for example `ObLogger`'s fields). They compile against the same headers, and the 39 geometry cases check them |
| C. Rewrite the island's use of oblib to plain C++ | Changes kept code | Changes behavior under the judge |

**Recommend B.** The couplings to value libraries (JSON binary, `ObNumber`, `ObObjCaster`, `ObTextStringIter`, `ObHexUtils`) leave the island together with MVT and the GeoJSON visitor (4.2). All 6 charset and number-formatting functions forward to the Rust implementations. That meets PLAN's "charset behavior matches the Rust charset code" by having one implementation. The header list is the depfile union above; Step 1 writes it out as a file with the command that produced it.

### 4.4 Memory: charging and the allocator

| Option | Cost | Risk |
|---|---|---|
| A. Rebuild label accounting on macOS with a custom malloc zone | A zone wrapper with thread-local labels | A process-wide hook, which Decision 8's guideline keeps out of the engine; macOS-only |
| **B. Charge only through explicit allocation callbacks; library-internal malloc stays uncharged** | Callbacks in the replacement `MemoryContext`/`ObIAllocator` code and the vsag `Allocator` bridge | None against the judge: this is today's macOS behavior |
| C. A jemalloc arena per island, read from arena stats | Arena switching at each entry | tcache attribution is approximate; the islands must share Rust's jemalloc |

**Recommend B** (it agrees with 03-memory.md, section 4.5):
- **geo and the parser:** the receiver passed into each call is the caller's Rust arena, which is charged to the query's memory context, so -11049 keeps counting GIS memory as it does today.
- **vsag:** one `vsag::Allocator` bridge calls a Rust vector-budget function with the same check as `ObVectorMemContext::alloc` (every `CHECK_USAGE_INTERVAL` calls, or when the size reaches `CHECK_RESOURCE_UNIT_SIZE`). A refusal returns null, vsag reports `NO_ENOUGH_MEMORY`, and that maps to -4013 as today.
- **CRoaring:** `roaring_init_memory_hook` points at the same budget.
- **The allocator:** the islands use the process malloc. Whether the binary crate promotes Rust's jemalloc zone as main.cpp does today is a binary-crate choice. The rule that each side frees only what it allocated makes both choices safe.

### 4.5 Building and linking

Each island crate's build.rs compiles its C++ with the `cc` crate. The compiler, SDK and flags are those of the reference: deps/3rd clang 17.0.6, SDK 26.2, `-std=gnu++20 -O2 -ffp-contract=off -fno-strict-aliasing -march=armv8-a+crc+lse` (compile_commands.json of the reference build). A check compares the island's flags with that file. The reason is that boost and S2 numerics and `std::sort` ties (share/geo calls `lib::ob_sort`, ob_geo_interior_point_visitor.cpp:354) then match the C++ reference. Link lists come from src/oblib/CMakeLists.txt:310-322 and src/oblib/lib/CMakeLists.txt:111-126, plus libc++. No whole-archive link is needed: grep finds no constructor attributes, file-scope registration initializers or `REGISTER_` macros in share/geo or src/oblib/lib/vector. That rests on grep only, and the first island link confirms it.

### 4.6 sql-nio's conventions, adopted or rejected

sql-nio's boundary is nio.h: 33 Rust-implemented `nio_*` functions, 4 C++-implemented `ob_sql_sock_handler_on_*` functions with the handler first (ob_sql_nio_server.cpp:39-76), 18 structs, 2 opaque types and 74 constants. 123 of its 186 `unsafe` sit in the two C-ABI files that PLAN drops (row_encode.rs 70, response_api.rs 53).

| Convention (abi-naming.md, notes/ffi-mechanics.md) | For islands | Reason |
|---|---|---|
| One header per boundary, generated by cbindgen from Rust, checked in, with a CI regenerate-and-diff check | **Adopt for both directions** | The C++-implemented entry points are declared in a Rust `extern "C"` block, which cbindgen writes out as `extern` declarations. The C++ shim includes the header, so a signature drift is a compile error. 01-crates-core.md proposes hand-written headers for island entry points instead; two hand-written copies can drift silently, which is the `sm_conn_greeting_info` failure the note records |
| cbindgen settings (`cpp_compat`, `documentation = false`, `usize_is_size_t`, stdint/stddef only) | Adopt, but no `usize`/`size_t` in island signatures | Lengths as `int64_t` keep wasm32 open (Decision 7) |
| Structs `<Tag><Name>` with the same name on both sides; no rename table; constants `<TAG>_<NAME>`; ABI-only constants at the crate root; opaque handles without `#[repr(C)]` | Adopt | Same mechanics; the names grep as one word |
| The prefix is the whole namespace, and a name says which side implements it | Adopt the principle | |
| Rust-implemented `<tag>_*`; C++-implemented shims keep their existing C++ names, with no tag | **Change** | Island entry points are new functions with no C name to follow. Proposal: C++-implemented `obgeo_*` and `obvsag_*`; Rust-implemented callbacks `<tag>_rs_*` (`obgeo_rs_alloc`, `obvsag_rs_filter_test`). `vsag_` is taken: vsag's C API defines 20 `vsag_*` symbols in the linked archive |
| Named functions, no function-pointer tables; the receiver as the first argument, never a global | Adopt; the parser C core keeps its existing signatures | Many receivers are live at once (arenas, indexes, filters) on many threads |
| All reverse declarations in one file | Adopt as one `ffi` module per island crate, holding both the extern block and the `#[no_mangle]` callbacks | Decision 14 confines `unsafe` there |
| Two layers: safe logic, thin `extern "C"` adapter | Adopt | Decision 14 |
| Callers guarantee pointer/length validity; only length 0 becomes `&[]` | Adopt in both directions | Rust passes slices (valid by construction); C++ returns null plus 0 for empty strings |
| Return an `int`, 0/−1, mapped by a wrapper | **Reject** | Codes are user-visible: return the exact OB code (0 = `OB_SUCCESS`) from the same ob_errno.def the Rust error type is generated from (02-errors.md agrees) |
| `#[cfg(test)]` stubs for reverse symbols | Reject for islands | Island crates' tests link the real C++; a stub would test nothing |
| `shim` marker for transitional code | Reject | The islands are kept on purpose until after parity (Decision 13) |
| No runtime ABI version handshake | Adopt | Same tree, static link |
| bindgen only for existing third-party C APIs | Adopt | ICU, zstd 1.3.8, zlib, CRoaring, libxml2 |
| Contracts in the vault note, not in code | Adapt | No comments in code; each island's contract lives in the design document in the repo, where implementers and reviewers read it |

### 4.7 Proposed island ABI rules

1. **One crate and one generated header per island** (`obgeo.h`, `obvsag.h`). The crate is on Decision 14's unsafe list, and only its `ffi` module declares or defines C symbols.
2. **Names.** C++-implemented entry points are `<tag>_<verb>`. Rust-implemented callbacks are `<tag>_rs_<verb>`. Structs are `<Tag><Name>` (`ObgeoMbr`, `ObvsagSearchResult`), opaque handles too (`ObgeoSrs`, `ObvsagIndex`). Tags are registered in the design document's naming section, and a tag may not collide with the wrapped library's own C API.
3. **Types.** Allowed across the boundary: fixed-width integers, `double`, `bool`, `(const uint8_t*, int64_t)` byte views, `#[repr(C)]` plain structs (for example the three doubles of `ObGeoErrLogInfo`), and opaque handle pointers. Never C++ types, `ObString`, `size_t` or `long`.
4. **Errors.** Every entry returns an `int32_t` OB code. User messages are formatted in Rust from the code and plain out-values.
5. **A catch-all at every C++ entry.** Each C++ entry is `noexcept` with a `try/catch(...)`. The existing catch lists keep their mappings. An exception that escapes them becomes `OB_ERR_UNEXPECTED` (see question 3 for `std::bad_alloc`). Rust declares the functions `extern "C"` (not `"C-unwind"`), and the whole program is built with `panic=abort`.
6. **Ownership.** Each side frees only what it allocated. Bytes that outlive a call are allocated in Rust memory through `<tag>_rs_alloc(receiver, size)`. Island objects live behind handles with exactly one `<tag>_<thing>_destroy`, called from the Rust wrapper's `Drop`.
7. **Receivers and threads.** Every callback takes its receiver first. Callbacks may run on threads the island starts (vsag), so they reach state only through the receiver, which must be `Send + Sync` on the Rust side, and never through a Rust thread-local.
8. **No re-entry.** A callback returns before the island continues; it never calls back into the same island, and no island `longjmp` passes over a Rust frame.
9. **Build.** Islands use the reference compiler, SDK and flags (4.5). The frozen headers and the replacement `.cpp` files are listed in the design document; islands never see Rust types, and Rust never includes oblib headers.
10. **Library versions stay inside the island.** The ABI does not change when vsag differs by platform (Android 0.18.0).

### 4.8 Cost and risks

- **Units.** geo about 8-15 units for its entries and callbacks, vsag 3-5, the replacement oblib files 3-5, the ICU binding 1, the C-library bindings about 5, build and flag checks 3-5. That is about 25-40, inside PLAN's 25-50.
- **Risks:**
  - the replacement files misreading header-inline code (the geometry suite catches it);
  - numeric drift if the flags differ (the flag check);
  - vsag callbacks arriving on threads Rust did not start (rule 7);
  - OpenSSL 1.1.1u staying in the product for S2's `BN_*`;
  - two deflate implementations (miniz_oxide for the wire, zlib for SQL and files) that must not be swapped;
  - ST_AsMVT has no test: moving it to Rust needs a C++-vs-Rust corpus added to family 13.

## 5. Commands behind the counts

Run from the worktree root unless a path says otherwise; `NM` is deps/3rd/usr/local/oceanbase/devtools/bin/llvm-nm and `DEV` is deps/3rd/usr/local/oceanbase/deps/devel.

| Figure | Command |
|---|---|
| share/geo files and lines | `git ls-files src/share/geo \| sed 's/.*\.//' \| sort \| uniq -c`; `git ls-files src/share/geo \| grep -E '\.(h\|cpp\|ipp)$' \| grep -v pb-c \| xargs cat \| wc -l` |
| 94 including files, 31 headers | `git grep -l -P '#include\s*[<"]share/geo/' -- src ':!src/share/geo'`, and the same with `-h -o` piped to `sort \| uniq -c` |
| 282 type names, 64 used in 111 files, 81 member references | python over `git ls-files`: class/struct/enum names declared in share/geo headers, matched in every other src file; `(ObGeo\w+Util\|ObGeoFunc\w*\|ObS2Adapter\|ObSpatialMBR\|...)::(\w+)` for members |
| 104 GIS expression files, 14,674 lines, 198 user-error lines | `git ls-files src/sql/engine/expr \| grep -E 'ob_expr_(priv_)?st_\|ob_geo_expr_utils\|ob_expr_spatial\|ob_expr_sdo\|ob_expr_geo'`, then `xargs cat \| wc -l` and `xargs grep -c -E 'LOG_USER_ERROR\|LOG_USER_WARN'` |
| vsag callers: 88 lines in 8 files, 17 functions | `git grep -c -P 'obvectorutil::\|obvsag::\|\bvsag::' -- src ':!src/oblib/lib/vector'`; `git grep -h -o -P '(obvectorutil\|obvsag)::\w+' ... \| sort \| uniq -c` |
| 33/27 and 24/18 declarations | python regex over ob_vsag_adaptor.h and ob_vector_util.h (function declarations inside the namespace) |
| 15 ICU functions | `git grep -h -o -P '\bu(regex\|_str\|_err)\w*\(' -- src/sql/engine/expr/ob_expr_regexp_context.cpp \| sort -u` |
| `_69` suffix; 22 `BN_*`; 43 abseil; vsag threads | `$NM -g $DEV/lib/libicui18n.a \| grep ' T _uregex_open'`; `$NM -u $DEV/lib64/libs2.a \| grep BN_ \| sort -u`; `... \| grep -i absl \| sort -u`; `$NM -u $DEV/lib/vsag_lib/libvsag_static.a \| grep -E 'pthread_create\|thread'` |
| 20 `vsag_*` C functions | `grep -o -E '\bvsag_[a-z_]+\(' $DEV/include/vsag/vsag_c_api.h \| sort -u` |
| try/catch counts | `git grep -c -P '\btry\s*\{' -- src/share/geo`; `git grep -c -P '\bcatch\s*\(' -- src ':!src/share/geo'` |
| 103 `ObMallocHookAttrGuard` lines in 36 files | `git grep -c ObMallocHookAttrGuard -- src` |
| compile-level closure | depclosure.py: the union of the `*.o.d` depfiles of the island objects, split by directory, lines counted per file |
| link-level needs | island_first_level.py: `llvm-nm -g -P` over all 634 objects, the deps archives and the SDK 26.2 libc++/libSystem `.tbd` files; undefined minus defined inside the island, resolved to the first provider |
| 363-object closure | objclosure.py: repeat "add each object that defines a needed symbol" until nothing changes; unity chunks mapped back to their sources |
| coverage percentages | python over AB.summary.json, summing `functions.count/covered` for files under each prefix |
| judged cases | `grep -v '^#' migration/judge/census/portable.txt \| awk '{print $1}' \| grep -c '^geometry\.'` (and `vector_index.`, `fts_index.`, `ai_function.`) |
| storage/fts 71 files, 10,526 lines | `git ls-files src/storage/fts \| grep -v ob_ik_dic.cpp \| grep -E '\.(h\|cpp)$' \| xargs cat \| wc -l` |
| boost users | `git grep -l -P '\bboost::\|(^\|[^a-z_])bg::' -- src` (38 in share/geo; the one sql/engine hit, ob_expr_st_buffer.cpp:318 and :555, is in comments) |
| 16 gRPC files | `git grep -l -P '#\s*include\s*[<"][^>"]*grpc' -- src` |
| nio.h: 33 + 4 functions, 18 structs, 2 opaque types, 74 constants; 186 `unsafe` | python regex over rust/sql-nio/include/nio.h; `grep -o '\bunsafe\b' rust/sql-nio/src/*.rs \| wc -l` |

## 6. Questions only the developer can answer

1. **relaxed-rapidjson:** confirm it is ported (recommended), not added as a sixth island. Decision 13 lists the islands and does not name it.
2. **zstd and zlib:** PLAN section 3 says the vendored zstd becomes a crate, but judge item 8 compares OUTFILE ZSTD, GZIP and DEFLATE bytes and `COMPRESS()` bytes. Confirm that zstd 1.3.8 and zlib 1.2.13 are kept as C code. The alternative, comparing decompressed content, would be a new mask, which Decision 6 does not allow.
3. **`std::bad_alloc` inside islands:** keep today's island mappings (`OB_ERR_STD_BAD_ALLOC_ERROR` in geo, -4013 for vsag `NO_ENOUGH_MEMORY`), or abort, which is how Decision 12 treats a general out-of-memory in Rust? This report assumes today's mappings stay, since the island code is kept unchanged.
