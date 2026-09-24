# Research 12: the rules implementers need, and the toolchain

Research input for the design document, covering RULEBOOK template sections 1-4 and 6 (templates/RULEBOOK.md, copied to migration/RULEBOOK.md). It decides nothing: the developer signs the design document. Written 2026-09-24 against worktree HEAD 7d6dd9141, whose src/ is the frozen base 834bbee1e (`git diff --stat 834bbee1e -- src` prints nothing). Nothing was built. The only tools run were `git grep`, Python over the tree, `cargo tree` (resolves features, compiles nothing) and the preprocessor (`clang -dM -E`). Section 13 says how the counts were made.

## Summary

- **Toolchain (Decision 16).** Keep 1.98.1 and add rustfmt; edition 2024 and resolver 3 for every crate; no `#![feature]`; three profiles, with debug assertions off in the judge profile because the reference has `-DNDEBUG`. One new rule: every C and C++ file the Rust build compiles uses the reference's compiler, SDK and `-ffp-contract=off`, and prebuilt archives come from deps/3rd unchanged.
- **Crates (section 1).** None by default; each allowed crate has a row; no async runtime in the engine. Three facts change the plan. The reference's compressed-protocol frames come from miniz_oxide 0.9.1, not zlib. `COMPRESS()` and OUTFILE GZIP/DEFLATE need zlib 1.2.13's exact bytes, and ZSTD needs zstd 1.3.8's. Physical standby over gRPC is live at 834bbee1e, so "protobuf/gRPC become prost/tonic" (PLAN section 3) brings tokio unless standby waits.
- **Constructs (section 2).** One Rust form each for inheritance, templates, macros, unions, goto, default arguments, operator overloading, friend, enums, `char`, indexing, exceptions and more. Errors follow research 02, memory research 03, and atomics, locks and the stack research 10.
- **unsafe (Decision 14).** Forbidden through workspace lints and the generated lib.rs. The closed list of named crates misses four needs: the jemalloc `GlobalAlloc` impl, the zlib and zstd wrappers, `_exit` in the binary crate, and sql-nio's socket code.
- **wasm guidelines.** Adopt all six now; only the budget sizing waits.
- **Naming (section 4).** One unit writes exactly one file; `lib.rs` and `mod.rs` are generated; C++ names are kept as they are; split pieces are sibling files `<stem>_pNN.rs`.
- **Markers.** `TODO(port)`, `PERF(port)`, `BUG(port)` and `SAFETY` with exact prefixes, on their own line, with the C++ file:line. With the status trailer, they are the only comments in translated code.

## 1. What the code does today

### 1.1 Build facts

| Fact | Evidence |
|---|---|
| The reference compiles with `-O2 -g -DNDEBUG -ffp-contract=off -fno-strict-aliasing -fno-omit-frame-pointer -march=armv8-a+crc+lse -mtune=generic -std=gnu++20`, deps/3rd's clang 17.0.6, SDK 26.2, `_LIBCPP_VERSION` 200100 | ref-834bbee1e/build_release/compile_commands.json (364 unity units); judge/reference.md:14-16 |
| Under `-DNDEBUG`, `OB_ASSERT(x)` is `(void)(x)`, so `ObIArrayWrap::at(idx)` checks no bounds | src/oblib/lib/utility/ob_macro_utils.h:708; src/oblib/lib/container/ob_array_wrap.h:39-54 |
| `char` is signed on the reference target and on the Linux x86_64 CI that recorded the .result files, unsigned on Linux arm64 | `clang -target <t> -dM -E -x c /dev/null \| grep -c __CHAR_UNSIGNED__` with deps/3rd's clang: arm64-apple-macos 0, x86_64-unknown-linux-gnu 0, aarch64-unknown-linux-gnu 1 |
| rust/: toolchain 1.98.1, profile minimal, clippy only, extra targets x86_64 Linux, x86_64 windows-gnu, aarch64 Android; workspace resolver 2; release profile opt-level 3, thin LTO, 1 codegen unit, debug info, panic abort; a `cmake-debug` profile | rust/rust-toolchain.toml; rust/Cargo.toml |
| sql-nio: edition 2021, 33 `#[no_mangle]` functions, 186 lines with `unsafe`, 123 of them in response_api.rs and row_encode.rs | rust/sql-nio/Cargo.toml; `grep -c unsafe rust/sql-nio/src/*.rs` |
| The compressed-protocol frames come from sql-nio's `flate2::Compress::new(Compression::default(), true)`. Only flate2's default `rust_backend` is enabled, so they are miniz_oxide 0.9.1's (flate2 1.1.10); zlib-rs 0.6.8 is in Cargo.lock but not compiled | rust/sql-nio/src/compress.rs:93 (`try_reserve_exact` at :78); `cargo +1.98.1 tree --offline --locked -e features -i flate2` |
| The C++ links zlib 1.2.13 from deps/3rd. `COMPRESS()` calls `compress()`. OUTFILE GZIP and DEFLATE both use `ObOutfileGzipStreamCompressor`: `deflateInit2`, level 5, window bits 15+16, memLevel 8 | src/oblib/CMakeLists.txt:318; deps/3rd zlib.h:40; src/sql/engine/expr/ob_expr_compress.cpp:81-104; src/sql/engine/basic/ob_select_into_basic.cpp:159-162, :258-260 |
| OUTFILE ZSTD uses the vendored zstd 1.3.8 (22 .c files, 25,154 lines); the crates.io zstd-sys in the local registry bundles 1.5.7 | src/oblib/lib/compress/zstd_1_3_8/zstd_src/zstd.h:72-73; registry directory zstd-sys-2.1.0+zstd.1.5.7 |
| jemalloc 5.3.1 comes through Cargo as seekdb-jemalloc-sys =0.2.2 | deps/external/Cargo.toml:10 |
| Physical standby over gRPC is in the reference: 53 files in src/standby, wired into `ObServer`, with a disabled variant. No test file mentions it | CMakeLists.txt:29; the reference's CMakeCache.txt:300; src/observer/ob_server.cpp:96, :112, :960-963; src/standby/CMakeLists.txt:1, :30; `grep -rli standby tools/deploy/mysql_test/test_suite/*/t/*.test` prints nothing |

The 2026-08-04 dependency survey (prior art, notes/rust-crate-dep-survey.md, at d65d43bed) found no gRPC callers; at 834bbee1e that is no longer true.

### 1.2 Third-party code and where its output shows

| Library | First-party users | Reaches the judge? |
|---|---|---|
| zlib 1.2.13 | `COMPRESS()`, OUTFILE, LOAD DATA inflate (ob_load_data_file_reader.cpp:616), table compression | yes: `COMPRESS()` and OUTFILE bytes (judge/golden-bytes-scope.md) |
| zstd 1.3.8, vendored | OUTFILE ZSTD, table blocks | yes: OUTFILE bytes |
| OpenSSL libcrypto | 9 files: MD4, MD5, SHA-1, SHA-224 to 512, SM3; AES and SM4 in ECB, CBC, CFB128, OFB, CTR, GCM | yes; libs2.a also needs it (PLAN section 3) |
| rapidjson, OB's patched copy | 6 files. The parser calls `reader.Parse<RELAXJSON_FLAG>`, `<STRICTJSON_FLAG>` or `<kParseInsituFlag>`, never with `kParseFullPrecisionFlag`, so doubles come from rapidjson's `StrtodNormalPrecision` | yes. src/oblib/common/json_type/ob_json_parse.cpp:23-29, :94-98; deps/3rd rapidjson/reader.h:1866-1870 |
| fast_float | ob_dtoa.cc, ob_array_cast.cpp | yes: number text |
| libcurl | main.cpp, the embedding handler, ob_telemetry.cpp, ob_ai_func_client.h | no (network) |
| libxml2; CRoaring | ob_libxml2_sax_handler.*, ob_xml_parser.*; 2 headers | no test file uses ExtractValue, UpdateXML or `rb_build` |
| SQLite | ob_sqlite_connection.*, ob_config_storage.h (meta.db) | no: data dir (Decision 11) |
| gRPC, protobuf | src/oblib/grpc and src/standby, 11 files; 12,345 generated .pb lines | no test |
| boost, S2, vsag, ICU, abseil, protobuf-c | the islands (Decision 13) | through the islands |
| Vendored hash code | murmurhash64A (src/oblib/lib/hash_func/murmur_hash.h:19), fnv_hash2 (:75), xxhash.c, wyhash.h | yes: query-block names print `fnv_hash2` as `%08X` (src/sql/resolver/dml/ob_sql_hint.cpp:89, :389); partitioning uses `hash_murmur` (src/sql/engine/expr/ob_expr_func_part_hash.cpp:141-148) |

Hash iteration order reaches plan text too: an `ObHashMap<ObString, int32_t>` is iterated at src/sql/optimizer/ob_join_order.cpp:16655-16666, an `ObHashSet<uint64_t>` at src/sql/rewrite/ob_range_generator.cpp:1797-1799. The order follows murmurhash (src/oblib/lib/hash/ob_hashutils.h:773-777) and a bucket count rounded by `cal_next_prime` (ob_hashutils.h:677; ob_hashmap.h:184).

### 1.3 C++ constructs Rust lacks

"G" counts are lines from `git grep -hP`, comments included; "S" counts come from a script that strips comments and strings (section 13).

| Construct | Count | Notes |
|---|---|---|
| Class or struct with a base | G 4,366 lines; S 4,646 declarations, 121 with several bases, 1,127 distinct bases | Top bases: `ObIntSysVar` 348, `ObFuncExprOperator` 313, `ObBoolSysVar` 145, `ObVirtualTableScannerIterator` 136, `ObVarcharSysVar` 134, `ObDLinkBase` 72, `ObTimerTask` 72; the SysVar classes are generated |
| `virtual`; pure virtual; `override` | S 16,876; G 2,586; S 7,105 | |
| `dynamic_cast`; `static_cast` to an `Ob*` pointer | G 263; G 5,142 | mostly downcasts after a type test |
| `template <`; full specializations; variadic | G 7,352 in 942 files; 1,084 in 156; 241 | S: `enable_if` 181 |
| Function-like `#define` | G 4,106 in 603 files | |
| `OB_FAIL`, `OB_SUCC`, `OB_ISNULL`, `OB_UNLIKELY`, `OB_TMP_FAIL` | G 99,909; 31,213; 30,896; 15,879; 631 | research 02 |
| `LOG_*`; `LOG_USER_*`; `K(`; `SMART_CALL` | S 29,845; G 2,948; G 28,619; G 1,131 | |
| Placement new; explicit destructor call | G 2,709 in 706 files; G 1,274 in 497 | |
| Unions | S 332: 304 anonymous, 144 holding bitfields, 5 mixing float and integer members | S: 897 bitfield members |
| `goto` | S 227 in 8 files | 103 in generated protobuf, 31 in dead files (ob_ctype_uca.cc, tzcode); 93 live: ob_dtoa.cc 56, ob_ctype_simple.cc 23, serialization.h 12, ob_ctype_bin.cc 1, ob_ctype_mb.cc 1 |
| Default arguments | about 3,146 header declarations, 4,392 defaulted parameters | regex count, approximate |
| Operator overloads | G: `()` 833, `=` 670, `==`/`!=` 752, `<`/`>`/`<=`/`>=` 260, `[]` 82, `++`/`--`/`->`/`*` 264 | S: 27 conversion operators; 34 `operator new/delete` in 4 files |
| `friend` | G 656: 583 classes (13 of them tests), 73 functions or operators | |
| `try`/`catch`/`throw` | G 170 lines in 50 files | geo's island edge (ob_geo_dispatcher.h:1314-1363 maps 18 exception types); `std::bad_alloc` catches (ob_json_parse.cpp:100, ob_rb_memory_mgr.h:35); PL blocks that restore state and rethrow (src/pl/ob_pl.cpp:178); one local throw and catch (src/sql/engine/px/ob_px_util.cpp:757-796) |
| `std::regex` (libc++) | JSON schema `pattern` (src/oblib/common/json_type/ob_json_schema.cpp:4003-4020); two log-file path patterns (src/logservice/ob_server_log_block_mgr.cpp:388-454) | no test uses JSON schema validation |
| Enums | G 473 unscoped, 214 `enum class`; 739 casts into a `*Type/Enum/Mode/Kind/Status/State` | `ObItemType`, shared with the C parser core, has 2,019 enumerators (src/query/api/query/parser/ob_item_type.h:27) |
| C++ identifiers that are Rust keywords | S about 10,216 uses in 1,750 files: `in` 5,268, `type` 4,252, `ref` 188, `fn` 97, `match` 90, `self` 68 | and a directory named `static` (src/sql/engine/pdml/static) |
| Variadic C functions | `new_non_terminal_node`, `new_list_node`, `yyerror` | in the parser's C core (src/query/api/query/parser/parse_node.h:397-399; src/sql/parser/sql_parser_base.h:64-67) |
| `#ifdef ERRSIM`; `#ifndef NDEBUG`; platform `#if`s | G 228; 105; 751 | `ERRSIM` is in neither the reference's CMakeCache.txt nor its compile commands |
| Tracepoints `OB_E(EventTable::`; `DEBUG_SYNC(` | G 186; 174 | pinned: init.sql sets 12 tracepoints; 7 cases use DEBUG_SYNC (PLAN section 3) |
| Explicit FMA | 16 lines of `_mm256/_mm512_fmadd_ps`, all in x86 kernels (ob_vector_l2_distance.h) | none on arm64 |

### 1.4 Facts the manifest depends on

- The map in progress keys units by repo-relative stem path with a class key: `src/sql/optimizer/ob_join_order`, 20,670 lines, `oceanbase::sql::ObJoinOrder` (migration/depmap/units.tsv).
- 3,787 stems in 324 directories. Dropping `ob_` would collide twice (src/oblib/lib/lock: `ob_mutex`/`mutex`; src/oblib/lib/utility: `ob_utility`/`utility`). No stem is a Rust keyword; 3 are not identifiers (generated .pb files); none shares its name with a sibling directory; 148 names occur in more than one directory, mostly API header and implementation pairs.
- src/query/api and src/data_plane/api hold 224 headers and no .cpp. The map counts 114 forwarding headers and 1 umbrella header (migration/depmap/summary.txt).
- 54 hand-written files have 4,000 lines or more, 383,109 lines at 834bbee1e (report section 3: 397K at 076eb309b).
- make_manifest.py derives `target` by string substitution. queue_runner.mjs reads only `source` and `target`, counts a unit done when `target` exists, and `verify` rejects empty targets.
- The kit's settings.json denies `cargo build/check/test/run`, `make` and `cmake`, but not `cargo clippy`, which compiles.

## 2. Constraints that bind this topic

- **Decision 16:** one pinned stable toolchain (1.98.1), no nightly features in shared code, allocator-api2 instead of `allocator_api`; reopen only if Step 2a measures per-crate checks above about 60 s. At the measured 19,000-44,000 lines per second on one thread (measurements/cargo-check-rate.md), a 180K-line crate checks in about 4-10 s.
- **Decision 14:** `#![forbid(unsafe_code)]` except in named crates (island shims, SIMD kernels, IO buffers, thin wrappers over vetted reclamation crates), with an `unsafe` count per crate at every gate.
- **Decision 12:** general out-of-memory aborts; typed errors only at the named budget owners and the logical -4013s; fallible collections only there and in client-sized buffers.
- **Decision 13:** the parser's C core, vsag, S2, share/geo with boost and ICU regex stay behind a C ABI. **Decision 11:** on-disk bytes are no contract; wire and SQL-function bytes still are. **Decision 6:** exact comparison apart from two masks. **Decisions 7 and 8:** keep wasm, Android, Linux and Windows open; the guidelines of PLAN section 3 are adopted or rejected here. **Decision 9:** kill-only stops. **Row 3a:** the reference's compiler and `-ffp-contract=off`.
- **PLAN section 3, items 1-8** (sort ties, hash order, FMA, overflow, codes as values, recursion, the parser's `longjmp`, plan-cache hits).
- **The kit:** marker formats are "load-bearing, do not vary it"; a unit is done when its output file exists; the rulebook is read-only inside loops.
- **The developer's convention:** no comments and no rustdoc, except where a convention requires them (~/repo/dev-plugin/skills/dev/CONVENTIONS.md, "Code style"). The kit's markers and `// SAFETY:` are such a case (section 7).

## 3. Toolchain (Decision 16)

Decision 16 fixes the channel; the rest is set-up.

| Item | Recommendation | Reason |
|---|---|---|
| Components | clippy and rustfmt | rustfmt is Step 2b's `[target formatter]` and the parse check units may run (section 10) |
| Extra targets | keep the three; optionally `cargo check --target` crates with no C code at gates | shows early what assumes macOS |
| Edition | 2024 for every crate, sql-nio included once its C ABI goes | 2024 requires `unsafe extern` blocks and `#[unsafe(no_mangle)]` and reserves `gen`. Let chains (`if let ... && ...`), which fit C++ conditions testing a lookup and a value together, are stable only in 2024 (Rust 1.88 release notes; not checked here) |
| Resolver | `resolver = "3"`, `workspace.package.rust-version = "1.98"` | updates never pick a crate needing a newer compiler (Cargo documentation; assumption) |
| Nightly | no `#![feature]`, no `-Z` flag, no `RUSTC_BOOTSTRAP`; a gate grep fails on any | Decision 16 |
| Lockfile, pins | commit Cargo.lock; the daemon builds `--locked --offline`; `=` versions for crates whose output is compared byte for byte (flate2 =1.1.10, miniz_oxide =0.9.1, seekdb-jemalloc-sys =0.2.2) | a minor update must not change judged bytes |
| C and C++ built by cargo | deps/3rd's clang 17.0.6, `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk`, `CFLAGS`/`CXXFLAGS` with `-ffp-contract=off -fno-strict-aliasing -march=armv8-a+crc+lse`, set once in `.cargo/config.toml` `[env]` | the islands must reproduce the reference's numbers (42 geometry tests pin boost's); clang fuses `a*b+c` by default (PLAN item 3); row 3a |
| Prebuilt archives | link the deps/3rd archives the reference links (libs2.a, vsag, ICU, OpenSSL, libz.a) unchanged until parity | same bytes as the reference |
| Loop denies | add `cargo clippy`, `cargo doc`, `cargo bench`, `cargo fix` to settings.json; leave `rustfmt` allowed | they compile; the template misses them |

| Profile | Settings | Used by |
|---|---|---|
| dev, test | defaults | `cargo check`, unit tests |
| judge | inherits release; opt-level 1 or 2 (Step 2a picks), no LTO, 256 codegen units, incremental, `debug-assertions = false`, `overflow-checks = false`, `panic = "abort"`, line tables only | every judge run (PLAN Step 6); assertions off to match `-DNDEBUG` |
| release | today's, with 1 codegen unit only if Step 2a's build time allows (research 01 section 5.4) | family 14, the shipped binary |

`cmake-debug` is dropped: there is no CMake host once Rust owns `main`.

| Nightly feature an implementer may reach for (1.98.1 source) | Stable form |
|---|---|
| `allocator_api`, `Box::try_new` (alloc/src/alloc.rs:54; boxed.rs:372) | allocator-api2; `Vec::try_reserve` is stable since 1.57 (vec/mod.rs:1534) |
| `integer_atomics`, `AtomicU128` (core/src/sync/atomic.rs:3753-3771) | none (section 8) |
| `portable_simd` (core/src/lib.rs:378) | `std::arch`: NEON arithmetic is a safe fn (`vaddq_f32`, core_arch arm_shared/neon/generated.rs:1873); pointer loads are `unsafe fn` (`vld1q_f32`, :18837) |
| `c_variadic` (core/src/ffi/va_list.rs:5-9) | no island callback into Rust is C-variadic; calling the C core's variadic functions is stable |
| `likely_unlikely` (core/src/hint.rs) | drop the hint; `std::hint::cold_path` is stable since 1.95.0 (hint.rs:778) |
| `alloc_error_hook` (std/src/alloc.rs:338) | the default, which prints and aborts as Decision 12 wants |
| `specialization`, `generic_const_exprs`, `fn_traits`, `try_blocks`, the `thread_local` attribute | the section 5 rows |

**Lints.** `[workspace.lints.rust]`: `unsafe_code = "forbid"`, `unsafe_op_in_unsafe_fn = "deny"`, and `allow` for `non_camel_case_types`, `non_snake_case`, `non_upper_case_globals`, since names stay as in C++. Clippy runs at gates only. Correctness and suspicious deny; style and complexity warn; `too_many_arguments` is allowed because C++ signatures are kept. A `clippy.toml` `disallowed-methods` list makes bans compile errors: `HashMap`/`HashSet` `new` and `with_capacity` (random hasher); `f32`/`f64` `mul_add`; the std slice sorts in engine crates; `std::env::set_current_dir` and process exit outside the binary crate; `std::thread::spawn` outside the runtime spawner (research 07 section 4.4). That clippy resolves each path is an assumption to check when the file is written.

## 4. RULEBOOK section 1: crates allowed and banned

**Async model.** (a) No async runtime: threads and blocking calls as in C++ (114 thread-pool and timer subclasses, no coroutines, research 10 section 1.5), and sql-nio's mio loop. (b) tokio for IO, which changes every signature in the 0.75M-line tier whose control flow is kept. (c) tokio in one edge crate, needed only if standby is kept: tonic 0.14.6's `channel` and `server` features pull tokio (registry tonic-0.14.6/Cargo.toml). **Recommendation: (a), plus (c) if standby stays (question 1).**

**Byte-exact compression.** For zlib: (a) deps/3rd's libz.a through a small FFI crate; (b) flate2's `zlib` feature; (c) miniz_oxide or zlib-rs everywhere. Option (b) breaks the protocol: cargo unifies features, so any zlib feature moves every flate2 user, sql-nio included, off miniz_oxide, whose output the reference's frames carry. Option (c) gives deflate streams unlike zlib 1.2.13's (assumption, to check with known-answer vectors before the choice is final). For zstd: the vendored 1.3.8 source built by cc, since the registry's zstd-sys bundles 1.5.7. **Recommendation: (a) and the vendored zstd.** Both wrappers need `unsafe`, which Decision 14's list does not name (question 2).

| Area | Decision | Why |
|---|---|---|
| Async runtime | banned: no `async fn`, tokio, async-std, smol or futures executors; the one exception is the standby crate, if kept | above |
| Global allocator | seekdb-jemalloc-sys =0.2.2 as `#[global_allocator]` in the binary crate | the reference's jemalloc (research 03 section 4.3, question 4) |
| Arenas, fallible collections | bumpalo (allocator-api2 feature), allocator-api2, hashbrown (allocator-api2 feature) for maps never iterated into output | Decision 16; research 03 |
| Locks, queues, snapshots, reclamation, stack | parking_lot through ob-base's `sync`; crossbeam-utils, -channel, -queue; arc-swap; crossbeam-epoch only in the named reclamation crate; stacker only in ob-base's stack module; loom for tests | research 10 section 4.3 |
| Hash functions | port murmurhash64A, fnv_hash2, the OB crc64, wyhash and XXH64 into ob-base. A crate only for a published algorithm confirmed by known-answer vectors from the C++: crc32fast for `CRC32()` (zlib CRC-32; `CRC32('123456789')` = 3421780262), crc32c | values reach output (section 1.2) |
| Hash maps | no random hasher in engine crates. An iterated Ob hash container becomes ob-base's port of `ObHashMap`/`ObHashSet` (same hash, same `cal_next_prime` bucket count, same chain order); lookup-only maps may use hashbrown with a fixed hasher | PLAN item 2 |
| Sorting | no std slice sort where order can reach output; ob-base's ports of the C++ algorithms | PLAN item 1; research 02 |
| Protocol compression | sql-nio's compress.rs unchanged: flate2 =1.1.10, default features, miniz_oxide =0.9.1; no crate may enable another flate2 backend | section 1.1 |
| `COMPRESS()`, OUTFILE, LOAD DATA | deps/3rd libz.a (1.2.13) through one FFI crate; the vendored zstd 1.3.8 for ZSTD | byte-exact output |
| Digests, ciphers | RustCrypto: md4, md-5, sha1, sha2, sm3, aes, sm4, des, with ecb, cbc, cfb, ofb, ctr and gcm; key folding, padding and IVs ported line by line from src/share/ob_encryption_util.cpp | published algorithms; OpenSSL stays only inside the S2 island |
| TLS | rustls (ring, tls12), rustls-pki-types, x509-parser, as sql-nio today | already the reference's TLS |
| HTTP | ureq (blocking, rustls) for the curl users | no async runtime |
| JSON | no JSON crate in the JSON SQL type: port json_type and the parts of the patched rapidjson reader it calls, `StrtodNormalPrecision` included | syntax, error positions and doubles reach output |
| Number text | `str::parse::<f64>` on the exact slice the C++ gives fast_float; ob_dtoa.cc ported as it is | both round correctly (assumption, confirmed by family 5's vectors); dtoa output is pinned |
| `std::regex` | fancy-regex for JSON schema `pattern` (it has the backreferences and lookaround the regex crate lacks), each site with a `TODO(port)` naming the dialect difference; plain string matching for the two log paths | libc++'s ECMAScript dialect has no exact Rust copy |
| XML | question 2: quick-xml with a SAX-style port, or deps/3rd's libxml2 behind an FFI crate | libxml2's behavior is the contract; no test |
| SQLite, roaring | rusqlite with bundled SQLite for meta.db (research 11 section 8.3); roaring only after known-answer vectors match its serialized bytes | internal; untested |
| gRPC | prost and tonic in the standby crate only, if kept | question 1 |
| Parsers, regex engines | nom, pest, lalrpop, logos and the regex crate banned for SQL | the grammar stays C; REGEXP is ICU |
| Logging, serialization | ob-base's log macros (the `log` facade only as a bridge for third-party crates); no serde or bincode for anything persisted or sent | log text kept for triage; Decision 11's explicit formats |
| Process, signals | nix (safe `pthread_sigmask` and `raise`: nix-0.31.3/src/sys/signal.rs:1045, :1126), binary crate only | Decision 9; research 07 section 4.5 |
| Build-time | cc, cmake; bindgen for existing C headers (output checked in); cbindgen =0.29.4 for island headers generated from Rust | section 11 |
| Anything else | not allowed without a row naming the crate, its pin and the reason | the kit's default |

## 5. RULEBOOK section 2: one Rust form per C++ construct

Adopted as they stand from other reports: errors and `ret` handling (research 02 section 5.2); memory, arenas, placement new and destructors (research 03 section 4.2); atomics, locks, thread-locals and `SMART_CALL` (research 10 section 4.2); service slots, singletons, timers and `ObDLinkBase` lists (research 07 section 4.3). The core design may choose another form for a core type.

| C++ | Rust | Reason, evidence |
|---|---|---|
| Base class with virtual methods whose subclasses all live in the dispatching crate (operators, logical operators, transform rules) | an `enum`, one variant per subclass struct, dispatch by `match` | a closed set; PLAN section 3 wants enum or table dispatch |
| Base class with subclasses in higher crates (`ObVirtualTableScannerIterator` 136, `ObTimerTask` 72) | a trait, used as `Box<dyn Trait>` or `&dyn Trait` | a lower crate cannot name higher types |
| Base-class data | a field named `base`; with several bases, one field each, named after the base type in snake case | 121 declarations |
| Pure-virtual interface (`ObI*`) | a trait | |
| `dynamic_cast`; downcast after a type test | `match` on the variant; for trait objects `as_any()` and `downcast_ref` | |
| Destructor | `Drop` only when it releases something besides field memory | |
| Template | generics whose bounds name exactly the operations the body uses | |
| Full specialization, `enable_if`, trait dispatch | a trait with one impl per type (`macro_rules!` when repetitive); the primary body as a default method only when no impl overlaps | `specialization` is nightly |
| Non-type template parameter | a const generic; a size computed from it becomes another const parameter | `generic_const_exprs` is nightly |
| Variadic template | `macro_rules!`, or a slice or tuple argument | |
| Function-like macro | `#[inline] fn` if it evaluates each argument once with fixed types; else a lowercase `macro_rules!` (per-site `static`, `ret`, `return`, `break`, `__FILE__`/`__LINE__`, stringified arguments) | G 4,106 |
| `OB_LIKELY`/`OB_UNLIKELY`; `OB_INLINE` | the bare condition; `#[inline]` | |
| `LOG_*` with `K(..)` | ob-base's log macros, same text and keys | so logs of the two builds can be compared in Steps 5-6 |
| `LOG_USER_*` | research 02's `user_error!` family | client-visible |
| `DISALLOW_COPY_AND_ASSIGN`; `TO_STRING_KV` | no `Clone`/`Copy`; a `Debug` impl with the same keys, and an exact named function where the text reaches the client | |
| `OB_UNIS_VERSION`, `OB_SERIALIZE_MEMBER` | the core's explicit little-endian encoding | Decision 11 |
| `DEFER`, `ON_SCOPE_EXIT` | a guard whose `Drop` runs the closure (ob-base) | |
| Tracepoints, `ERRSIM_POINT_DEF`, `DEBUG_SYNC` | ob-base APIs keyed by the same numbers and names | pinned |
| `#ifdef ERRSIM`; `#ifndef NDEBUG`, `OB_ASSERT` | `#[cfg(feature = "errsim")]`, off in judge and release; `#[cfg(debug_assertions)]`, `debug_assert!` | matches the reference's build |
| Platform `#if` | a `#[cfg]` arm for every arm the C++ has | Decision 7 |
| X-macro `.def` lists | generator output (research 11 section 9.1) or `macro_rules!` over a Rust copy | |
| Tagged union, tag in the same struct | an `enum` | |
| Union over a flag word with a bitfield struct | one integer field; a getter and setter per C++ member, named after it (`is_null_()`, `set_is_null_(v)`), with masks and shifts; no bitfield crate | 144 of 332 unions; getters like `is_null()` already exist, so member names avoid clashes |
| Type-punning union; `reinterpret_cast` on bytes | `to_bits`/`from_bits`, `to_le_bytes`/`from_le_bytes` (every target is little-endian) | object-pointer casts get inventory rows |
| `goto` to a common exit; backward `goto` | a labeled block with `break 'label`; `loop` with `continue 'label` | |
| Any other `goto` (ob_dtoa.cc) | `loop { match state }` over an enum of the C++ labels | pinned number text, so a pinned-text reviewer checks it |
| `switch` fallthrough | `A \| B` for empty cases; a non-empty case that falls through repeats the next case's code | |
| Default arguments | every call passes every argument; the declaration index records each default | no extra functions, no `Option` parameters |
| `==`, `!=` | `PartialEq`; `Eq` only if reflexive (no float fields) | |
| `<`, `>`, `<=`, `>=` | `PartialOrd`; `Ord` only if total; sorting still uses the ported algorithms | |
| `operator()` | a method named `call`; call sites pass a closure that calls it | implementing `Fn` is nightly |
| `operator=` | `Clone`/`clone_from` if it cannot fail; else the class's fallible `assign` | research 02 |
| `[]`; `++`, `--`, `->`, unary `*` | `Index`/`IndexMut`; `Iterator` for iterators, `Deref`/`DerefMut` for guards and handles | |
| Arithmetic operators; conversions; `operator new/delete` | `std::ops` only when the C++ cannot fail, else named methods returning `Result`; `From` or a named method; removed | |
| `friend`; `protected`; `private` | members a friend uses become `pub(crate)` (`pub` for a friend in another crate; test friends are dropped); `pub(crate)` (`pub` if a subclass is in another crate); private, except friends and split types (section 9) | |
| Unscoped enum, or `enum class` built from integers | `#[derive(Clone, Copy, PartialEq, Eq)] #[repr(transparent)] struct Name(pub <int>)` with a const per enumerator under its C++ name; `switch` becomes `match` on the consts | 739 casts; range tests such as `IS_EXPR_OP` (src/query/api/query/parser/ob_item_type.h:2342); enums shared with C are generated from the header |
| Other `enum class` | a Rust `enum` with the same `#[repr]` | |
| `char` in arithmetic, comparisons or widening | `u8` storage, signed semantics (`b as i8`) wherever compared, widened or computed | section 1.1 |
| Unsigned wraparound the C++ relies on (hashes, checksums, sequences); signed overflow | `wrapping_*`, right under dev's overflow checks too; plain operators, with inventory rows where overflow can happen | PLAN item 4 |
| `at(idx)` or `[idx]` unchecked under NDEBUG; `at(idx, obj)` returning `OB_ARRAY_OUT_OF_RANGE` | `[idx]`, which aborts where C++ was undefined; `get(idx)` mapped to the same code | ob_array_wrap.h:39-54; src/oblib/lib/container/ob_array.h:310-320 |
| `push_back` on `ObArray`/`ObSEArray`; on `ObFixedArray` | `push`; keep `OB_NOT_INIT` and `OB_SIZE_OVERFLOW` | src/oblib/lib/container/ob_fixed_array.h:67-77 |
| Exceptions | at an island edge the C++ shim catches everything and returns a code; `std::bad_alloc` catches go (Decision 12); restore-and-rethrow becomes a `Drop` guard; a local throw and catch becomes an early return of the same code | section 1.3 |
| `setjmp`/`longjmp`; C-variadic callbacks | stays in the C core, with no Rust frame between the parse entry and a callback (PLAN item 7); not allowed across the island ABI | |
| `a*b+c`; float reductions | never `mul_add` on aarch64 (x86 kernels may call `_mm256_fmadd_ps` where the C++ does); the C++'s association order, SIMD lane partial sums included | results are pinned |
| C++ identifier that is a Rust keyword | a raw identifier (`r#type`, `r#in`); `self`, `Self`, `super`, `crate` take a trailing `_`; the `static` directory becomes module `r#static` | |

**The template's BUG, error-recovery and UNKNOWN rules** stay. `BUG(port)` candidates, if the design document calls them defects: DEFLATE writing a gzip stream (ob_select_into_basic.cpp:159-162), and the two comparators that return false after an error (PLAN item 1). `allocation-guard` rows disappear under Decision 12; `precondition-guard` rows stay. The conservative translation is an owned copy, the exact C++ code, no `unsafe`, no `unwrap` where the C++ checks.

## 6. RULEBOOK section 2, continued: allocation failure (Decision 12)

| C++ | Rust |
|---|---|
| `OB_ISNULL(p = alloc(..))`, then `ret = OB_ALLOCATE_MEMORY_FAILED` (the code appears on G 4,974 lines) | the allocation, nothing else |
| The same at a named budget owner or logical limit | the owner's own check and code, unchanged. -11049 comes from `ObMemTrackerGuard::check_status` comparing the context's hold with the limit (src/sql/executor/ob_memory_tracker.cpp:36-55, raised at :48), not from a failing allocation |
| A buffer sized by the client | the C++ size check first, then `try_reserve` with -4013 on failure, as sql-nio does (compress.rs:78) |
| A `Box::try_new` need at an owner | allocator-api2 |

A reviewer rejects `try_reserve` anywhere else (research 03 section 4.4).

## 7. RULEBOOK section 3: unsafe and the markers

### 7.1 Where unsafe may appear (Decision 14)

Enforcement options: (a) `#![forbid(unsafe_code)]` in each crate root; (b) `unsafe_code = "forbid"` in the workspace lints, inherited by `[lints] workspace = true`; (c) both. Option (a) depends on every root having the line; (b) is undone by one Cargo.toml edit. **Recommendation: (c).** The generated lib.rs of each non-named crate carries the attribute, and each inherits the workspace lints. A named crate writes its own `[lints]` without `unsafe_code`, with `unsafe_op_in_unsafe_fn` and clippy's `undocumented_unsafe_blocks` and `multiple_unsafe_ops_per_block` denied, so each `SAFETY` comment covers one operation. A gate script fails if the crates without the attribute differ from the named list.

rustc's `unsafe_code` lint covers "other potentially unsound constructs" (`rustc -W help`), which includes `#[no_mangle]` and `#[export_name]` (assumption, confirmed in Step 2a). So a crate exporting a C symbol must be named: the island crates, and nothing else once sql-nio loses its C ABI.

Decision 14's list covers the island shims (research 01: sql-parser-sys, geo-sys, vsag-sys, icu-regex-sys), the SIMD crate (pointer loads are `unsafe fn`), the IO buffer crate (aligned allocation for direct IO) and the reclamation wrapper. Four needs fall outside it (question 2):
- the `GlobalAlloc` impl over seekdb-jemalloc-sys (research 03, question 4);
- the zlib 1.2.13 and zstd 1.3.8 wrappers (section 4);
- `libc::_exit` in the binary crate: nix 0.31.3 has safe `raise` and `pthread_sigmask` but no `_exit` (research 07, question 2);
- sql-nio's remaining socket-level `unsafe` (research 01 section 5.4).

A script counts `unsafe` keywords outside comments and strings (blocks, functions, impls, traits, extern blocks) per crate and reports every crate at every gate, zeros included.

### 7.2 The markers

| Marker | Exact form | Content |
|---|---|---|
| `TODO(port)` | `// TODO(port): <text>` | the open question and the C++ `file:line` |
| `PERF(port)` | `// PERF(port): <text>` | what the faster version would be |
| `BUG(port)` | `// BUG(port): <text>` | the C++ defect, a repro and the C++ `file:line`; the code reproduces the defect |
| `SAFETY` | `// SAFETY: <text>` | why the one operation in the next `unsafe` block, impl or fn is sound |

- Each marker sits on its own line directly above the code it concerns: never after code on the same line, never in a block or doc comment. Then `rg -c '^\s*// TODO\(port\): '` is exact, and so is PLAN Step 3's check that `grep -c 'TODO(port)'` equals the trailer.
- These four and the status trailer are the only comments allowed: no rustdoc, no explanations (question 3).
- Counts per marker and crate are reported at every gate next to the `unsafe` counts; prompt 06 burns them down after parity.

## 8. The wasm-rule guidelines (PLAN section 3)

| Guideline | Adopt now? | Why |
|---|---|---|
| u64 for every persisted and wire field | yes | the formats are new (Decision 11) and written now; `usize` never appears in an encoded field |
| No 128-bit atomics | yes | already forced: `AtomicU128` is unstable in 1.98.1. `portable-atomic`'s 128-bit types and `AtomicCell<u128>` are banned too. The C++ names them in 4 files: src/oblib/lib/atomic/atomic128.h (the definitions), palf's lsn_allocator.cpp (21 lines), the rate-limit macros (ob_macro_utils.h:850-866, :919) and one include; research 10 rule 6 gives the forms |
| Depth limit on wasm, stack growth on native | yes | every `SMART_CALL` goes through one wrapper, so the wasm arm is one `cfg` branch (research 10 rule 19) |
| Networking behind cargo features | yes, as a crate rule | only sql-nio, the HTTP crate and (if kept) standby open sockets; engine crates never depend on them, and the binary's features choose them |
| SIMD128 chosen at compile time | yes | NEON is always present on aarch64-apple-darwin, so the choice is `cfg(target_arch)`. Each kernel keeps a scalar version with the same results; run-time detection only inside the SIMD crate, for x86_64 later (the C++ detects at run time: 83 lines in 17 files) |
| `panic=abort` for the whole program | yes | already sql-nio's; Rust owns `main` |
| Budget limits from wasm's 2 GiB heap | later | sizing only; limits come from config (research 03 section 4.4) |

## 9. RULEBOOK section 4: naming and output paths

**Names.** (a) Keep C++ names: `ob_join_order.rs`, `ObJoinOrder`, `T_FUN_SYS_HASH`, fields with their trailing `_`. (b) Rust-style names. Option (a) is mechanical (make_manifest's `--sub` pairs do it), keeps every name greppable across both trees, and spares reviewers a check; it costs Rust style and three allowed lints. Option (b) needs a rename table in the declaration index that every implementer applies the same way, and it already collides twice on stems (section 1.4). **Recommendation: (a)** for translated code; core types take the design document's names (question 4). A rename after parity is mechanical.

1. **Tree.** The Rust tree is rust/ (PLAN Step 6). Crates live in rust/<crate>/ with research 01's names. The manifest script places units only by research 01's prefix-to-crate table (crates.tsv, now in migration/design/evidence/01-crates/; the design document should keep a copy in migration/).
2. **Manifest rows**, columns in this order (PLAN section 6): `source`, the unit's defining file (the .cpp if any, else the header); `target`, the one file the unit writes; `unit_id`, the map's key (`src/sql/optimizer/ob_join_order`), plus `.pNN` for split pieces and `core/<crate>/<module path>` for core units; `kind`, one of `stem`, `split`, `subsystem`; `inputs`, every source file, as line ranges for split pieces.
3. **Stems.** `rust/<crate>/src/<dir>/<stem>.rs`, where `<dir>` is the defining file's directory below the crate's source prefix and `<stem>` is the C++ stem as it is. A class declared in query/api or data_plane/api goes where its .cpp is, per the map's class key.
4. **Split pieces.** A file of 4,000 lines or more is cut at class or function boundaries into siblings `<stem>_p01.rs`, `<stem>_p02.rs` and so on, in source order. `<stem>.rs` holds the types and the header's inline functions; each piece holds `impl` blocks for a contiguous range of the .cpp. Members of a split type are `pub(crate)`, since sibling modules cannot see private members.
5. **Core units** follow the same rule: migration/core-manifest.tsv has one row per module file of the frozen core API, with `inputs` listing the C++ stems it replaces.
6. **Generated files.** Every `lib.rs` and `mod.rs` (module lines, `#![forbid(unsafe_code)]`, lint allowances) is written by a script from the manifest, never by a unit. So "target exists" means done, and no two units edit one file. Generator output is checked in with a DO-NOT-EDIT banner and diffed at gates (research 11 section 9.1).
7. **Identifiers.** As in C++ (`Ob` prefixes, trailing `_`, upper-case enumerators and constants), with section 5's keyword rule; macros become lowercase `macro_rules!`.
8. **Imports.** Full paths from the declaration index (migration/decl-index/<unit_id>.txt), which gives each C++ declaration its Rust path and, for core replacements, the core's name. No glob imports in units.
9. **Home modules.** ob-base holds the helpers named here: log macros, the user-error API, the stack wrapper, the `ObHashMap` port, the hash functions, tracepoints and debug sync, the guard type, the sort ports. Units never write local copies.
10. **Tests.** Step 3 units write none; the judge is the test. Core crates keep differential tests in rust/<crate>/tests/ with vectors recorded from the C++ reference in tests/vectors/. Only island crates link C++ in tests.

## 10. RULEBOOK section 6: per-file obligations

1. The last line is `// PORT STATUS: confidence=<high|medium|low> todos=<N>`, N being the number of `// TODO(port): ` lines; every split piece has its own.
2. `rustfmt --edition 2024 <file>` runs clean. rustfmt only parses, so units may run it; a parse failure is a reviewer finding.
3. No `mod`, `#![..]` or `extern crate` lines.
4. `use` names only workspace crates and section 4's crates.
5. `unsafe` only in a named crate, each with `// SAFETY:` above it.
6. No comments besides section 7.2's markers and the trailer.
7. No `println!`, `eprintln!` or `dbg!`.
8. `unwrap`/`expect` only where the C++ dereferences without a check, so a panic stands where the C++ would crash.

## 11. The earlier sql-nio conventions

From abi-naming.md and notes/ffi-mechanics.md (Obsidian, prior art):

| Convention | Verdict | Reason |
|---|---|---|
| Header generated by cbindgen from Rust, checked in, DO NOT EDIT, CI diff | adopt for island APIs designed now (the geo kernel API, the vsag, S2 and ICU shims): Rust declares them in one `unsafe extern "C"` block, cbindgen writes the header, the C++ shim includes it | drift becomes a C++ compile error (ffi-mechanics.md). Research 01 section 5.5 prefers hand-written island headers, which leaves two hand-written copies with no compiler between them, the note's `sm_conn_greeting_info` failure. The design document picks one |
| bindgen only for existing shared C headers | adopt for the parser core (parse_node.h, sql_parser_base.h, parse_malloc.h) and `ObItemType`, output checked in | these stay C; a hand mirror drifts |
| `<tag>_` prefix for Rust-implemented symbols; C++ names for C++-implemented ones | adopt, except callbacks an existing C header names (`parse_malloc` and its helpers) | a flat C namespace |
| ABI structs `<Tag><Name>` on both sides, no rename table | adopt | one grep word |
| Receiver as first parameter, never a global | adopt | the note's test: a global receiver lost 16 of 200 updates |
| No function-pointer tables | adopt; the C++ shim implements vsag's `Allocator`/`Logger` and calls named Rust functions | research 01 and 03 agree |
| `shim` marker for transitional symbols | reject | one cutover; nothing is half-ported |
| No comments; contracts in Obsidian notes | partly: markers are the only comments; island contracts go in the design document, in migration/ | working documents live in migration/ |
| Safe logic plus a thin `extern "C"` adapter | adopt | keeps `unsafe` in named crates |
| Trust C++-supplied pointers and lengths; `&[]` for length 0 | adopt inside island crates | `from_raw_parts` needs a non-null pointer even for length 0 |
| `#[cfg(test)]` stubs for reverse symbols | reject | island crates link the real island in tests; other crates export no C symbols |
| `panic = "abort"` on named profiles; `cmake-debug` | abort in every profile; drop `cmake-debug` | Rust owns `main` |

## 12. Questions only the developer can answer

1. **Physical standby in the first Rust release?** It is live in the reference but untested by any case. If kept, tokio, tonic and prost enter one crate, its runtime owned there and blocking toward the engine. If deferred, the Rust build starts like `OB_ENABLE_STANDBY=OFF` (standby_module_disabled.cpp), and the async ban has no exception.
2. **C code in the Rust binary, and the named-crate list.** zlib 1.2.13 (deps/3rd), zstd 1.3.8 (vendored), SQLite (rusqlite), ring, jemalloc and perhaps libxml2 would be compiled or linked by the Rust tree. Are they acceptable under Decisions 1 and 13? May Decision 14's closed list grow to cover their wrappers, the `GlobalAlloc` impl, `_exit` and sql-nio's sockets? For XML: quick-xml or libxml2?
3. **Comments.** Are the four markers, the trailer and `// SAFETY:` the only comments in translated Rust? Does the frozen core API carry rustdoc for Step 2b's translator B, who gets "public API docs only" (PLAN section 6, departure 5)?
4. **Names.** Keep C++ names in translated code (section 9, option (a)), or use Rust-style names from the start?

## 13. How the counts were made

- **G:** `git grep -hP '<regex>' -- 'src/*.h' 'src/*.hpp' 'src/*.cpp' 'src/*.cc' 'src/*.c' 'src/*.ipp' ':!src/oblib/lib/compress/zstd_1_3_8/zstd_src' | wc -l` from the repository root (`-lP` for files). Regexes include `\bvirtual\b`, `\)\s*(const\s*)?(override\s*)?=\s*0\s*;`, `\btemplate\s*<`, `\btemplate\s*<\s*>`, `\btypename\s*\.\.\.`, `^\s*#\s*define\s+\w+\(`, `\bnew\s*\(\s*[\w&\->\.\[\]\+\*\s]+\)\s*[\w:]`, `(->|\.)~\w+\s*\(`, `\boperator\s*<op>\s*\(`, `\bfriend\b`, `\btry\s*\{|\bcatch\s*\(|\bthrow\b`, and each macro name followed by `\s*\(`. Thread-pool and timer subclasses: `:\s*public\s+(lib::|share::|common::)?(ThreadPool|ObSimpleThreadPool|ObThreadPool|ObTimerTask|ObAsyncTask)\b` over .h and .cpp (114).
- **S:** migration/design/evidence/count_constructs.py (scratch): the same files plus .def, comments and string literals removed, occurrences counted; unions classified by scanning each body. Default arguments: count_defaults.py beside it, a regex over comment-stripped headers, approximate.
- Stems and directory names: Python over `git ls-files src`, zstd_src excluded. Large files: `wc -l` over tracked src files, less 11 generated, data or dead ones (ob_ik_dic.cpp, ob_timezone_info.cpp, share/ob_errno.cpp, the two generated codec files, ob_system_variable_factory.{cpp,h}, ob_system_variable_init.cpp, ob_ctype_uca.cc, standbyservice.pb.{cc,h}).
- Rust feature stability: the 1.98.1 library source under ~/.rustup/toolchains/1.98.1-aarch64-apple-darwin/lib/rustlib/src/rust/library, at the lines cited.
