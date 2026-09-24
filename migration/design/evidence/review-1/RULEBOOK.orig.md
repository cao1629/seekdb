# Translation Rulebook — C++ → Rust

> **The meta-rule: if two agents could answer a question differently, the
> answer goes in this file, or in the design file it points to.** This document
> is read by every implementer, reviewer, and fixer before they touch code. It is
> read-only inside any loop: amendments are queued for the developer and applied
> between batches.
>
> This port is a redesign, so this file is one part of the design document;
> section 0 lists the others.

Read this whole document before writing any code. Then read the parts of the design sections that section 0's table names for your unit.

## 0. Scope and posture

- **This is a redesign** (Decision 10 (a)). A new Rust core is designed by hand and built first. The SQL tier (optimizer, rewrite, resolvers, operators, expressions, DAS) is rewritten against it keeping its control flow. The other leaf code is translated file by file against it. The parser's C core, vsag, S2, share/geo with boost.geometry and ICU regex stay C or C++ behind a C ABI (Decision 13). One cutover replaces the C++ binary under data version 2.0.0.0 (Decision 11).
- **The design document is these files together:**

  | File | What it holds | Read it when |
  |---|---|---|
  | migration/RULEBOOK.md (this file) | the rules every unit follows | before every unit |
  | migration/design/ARCHITECTURE.md | the decisions; the crate table (§1.1); the defaults and questions for the developer (§17) | a rule here points to it |
  | migration/design/s1-crates-core.md | crates, the core's scope, the server context, the binary crate, target paths, the manifests, identifiers | your unit reaches a service, config, a timer, a thread or process state |
  | migration/design/s2-errors.md | `ObError`, the two function forms, codes as values, log lines, user messages, the warning buffer, the catalog | every unit |
  | migration/design/s3-memory.md | owners, handoff, reference counts, budgets, the query memory tracker, out-of-memory | your unit allocates, hands memory to another thread, holds a handle or charges a budget |
  | migration/design/s4-sql-front.md | the parse tree, IR ids, expression and statement storage, how resolver, rewrite and optimizer code is written | sql-parse-tree, sql-ir, sql-parser, sql-resolver, sql-rewrite, sql-optimizer, and any code that touches the IR |
  | migration/design/s5-execution.md | the datum, frames, operators, batch sizes, storage filters, sorting, hashing | sql-exec, sql-expr, sql-engine, sql-das, storage-api, and any sort or hash |
  | migration/design/s6-storage.md | estimator inputs, scan order, the data version, bootstrap, catalog access, the generators | storage crates, bootstrap, catalog code, generated code |
  | migration/design/s7-islands-unsafe.md | the islands and their ABIs, the kept oblib subset, where `unsafe` may appear | island crates, named `unsafe` crates, callers of an island |
  | migration/design/s8-numerics-platform.md | floats, overflow, profiles, atomics, locks, thread-locals, the stack, the toolchain, crates allowed and banned | numeric code, concurrency, build configuration |
  | migration/design/RESOLUTIONS.md | how the sections' objections to ARCHITECTURE.md were settled | two files seem to disagree |

- **Authority, highest first:** migration/decisions.md, migration/PLAN.md, the feasibility report, ARCHITECTURE.md, the section files, this file, the research reports in migration/design/research/. This file summarizes the design files for implementers; where a summary here and a design file disagree, the design file wins, and the disagreement is a finding queued for the developer.
- **Three kinds of unit.**
  - Core units (migration/core-manifest.tsv, `kind` = `subsystem`) are written against the design, by the developer with Opus 5.5 or by agents under prompt 04's discipline. They make the frozen core API.
  - Step 3 units (migration/manifest.tsv, `kind` = `stem` or `split`) are translated against the frozen core API: the SQL tier keeping its control flow, the other leaves keeping their structure.
  - Everything else (islands, generated, data, vendored, dead, dropped, deferred) is in migration/not-translated.tsv with its reason and is not translated.
- **What a Step 3 unit gets:** its C++ stem (or line range), its entry in migration/decl-index/<unit_id>.txt (the Rust path, overload number and default arguments of every declaration it uses, and the context type each function takes), this document and the frozen core API with its rustdoc, and its rows in migration/inventory.tsv.
- **Keep the control flow.** One Rust function per C++ function, with the same name, in the file section 4 gives. Parameters in the C++ order, after the context parameters of 2.8. Statements in the C++ order. Loops keep their order and re-evaluate their bounds each time round where the C++ does. Early exits (`break`, `continue`, `return`) stay where the C++ has them. The only statements you may add are an id lookup, a field read into a local, a slot copied out and written back (2.4), and a context passed on. The only ones you may remove are a null test on something that cannot be null in Rust (an id, a reference, a context field), an `is_inited_` or `OB_NOT_INIT` check that a constructor makes meaningless (2.1), and a branch that handled a general allocation failure (2.2).
- **The judge compares client output exactly** (Decision 6): plan text (`output(...)` lines, `rowset=`, the `%08X` query-block names), row order (ties under ORDER BY, and the storage and PX scan order of every unordered single-table SELECT), every number's text, error codes, SQLSTATEs, messages and SHOW WARNINGS rows, `DEBUG_SYNC` and tracepoint names, parameter names, plan-cache hits. The only masks are EST.ROWS and EST.TIME in the 40 plan-bearing files and the row order of about 300 declared hash-order SELECTs. So never improve an algorithm, a formula, a constant, a message, a hash, a sort or an iteration order. A C++ behavior that is a bug is kept and marked (the BUG rule, section 2).
- **Drafts do not need to compile. Do not run the compiler.** The loop denies (the kit's settings.json, adapted for seekdb) cover every `cargo` command that compiles (`build`, `check`, `test`, `run`, `clippy`, `doc`, `bench`, `fix`, `rustc`, and any `cargo +<toolchain>` form), `rustc`, `make`, `cmake`, `build.sh`, the mysqltest runner and `sdb.py start`. Builds and judge runs go through the build daemon (migration/scripts/build_daemon.sh), whose outputs are migration/build-output-r<N>.txt. `rustfmt --edition 2024 --check <file>` is allowed: it only parses. Step 4 folds into Step 3 for a crate only if Step 2a measured that crate's check at 60 s or less (PLAN §6).
- **First pass: faithful, not fast.** A known slow but faithful translation is fine: mark it `PERF(port):` with one line on the faster version, and move on.
- **The C++ is frozen at 834bbee1e.** Never edit a file under src/; kept C and C++ is compiled from its src/ path (s7-islands-unsafe.md 7.1).
- **Defaults.** Some choices are the developer's: ARCHITECTURE.md §17 lists them (37 defaults and 2 questions). Follow them as written; a default the developer changes arrives as an amendment.

## 1. Ecosystem adoption — what we use and what we ban

A unit never adds a dependency: manifests are generated, and `use` names only workspace crates on its crate's row (section 4) and the crates below. A new crate needs a row here (crate, pin, where, why) and a Cargo.lock change committed between batches.

| Area | Decision | Why |
|---|---|---|
| Async model | BANNED: no async runtime, executor or `async fn`. Threads and blocking calls as in the C++; sql-nio keeps its mio loop | control flow kept; standby, which needs tokio, is deferred (ARCHITECTURE §7.4) |
| Threads | Only ob-runtime's `ThreadSpawner` starts engine threads (`std::thread::spawn` and `Builder::spawn` are banned elsewhere). `std::thread::scope` only where the caller waits for all workers; PX, DTL, DAG and DAS parallel tasks copy their input | thread names, stacks and QoS; a library host brings its own threads |
| Timers | `ObTimer` on the `ObTimerService` in `RuntimeContext`, with the C++ contract (one task at a time per timer, next run at completion plus the delay, cancel lets a running task finish, a stopped timer returns `OB_CANCELED`) | ARCHITECTURE §7.2 |
| Files and devices | Through ob-runtime's IO layer; aligned buffers from ob-platform; paths from `ctx.base_dir()`, never `getcwd()` | direct IO, budgets, library hosts |
| Strings and bytes | SQL values, names and messages are bytes (`&[u8]`, `&'q [u8]`, `Box<[u8]>`, `Vec<u8>`, `Arc<[u8]>`, `Cow<'q, [u8]>`), never `str` or `String` | collations, invalid UTF-8 |
| Number text | Only the translated formatters (ob_dtoa, ObNumber, decimal-int) and the translated `ob_strtod`. No `Display`, `{:e}`, `{:.N}` or `str::parse::<f64>` of anything that can reach output or a hash | 770,711 compared result lines come from number formatting |
| Errors | `ObError` and `ObResult` only (section 2.1); no `anyhow`, `thiserror` or `Box<dyn Error>` | the exact code must survive |
| Logging | ob-base's log macros, with the C++ text and keys; the `log` crate only as a bridge for third-party crates; no `println!`, `eprintln!` or `dbg!` (banned by clippy; `eprintln!` allowed only in the seekdb crate) | logs of both builds are compared in Steps 5-6 |
| Serialization | Explicit little-endian encoders written in the core; no serde, bincode, rkyv or postcard for anything persisted or sent | Decision 11's new formats |
| Hash maps and sets | An iterated map is ob-base's `ObHashMap`/`ObHashSet` port; an address-keyed map is `IdHashMap`/`IdHashSet`; a lookup-only map may be hashbrown with ob-base's fixed hasher; `std::map`/`std::set` become `BTreeMap`/`BTreeSet` whose `Ord` equals the C++ comparator. std `HashMap`, `HashSet` and `RandomState` are banned | iteration order reaches plans and results |
| Sorting | ob-base's `ob_sort` and heap functions (a transcription of the SDK 26.2 libc++), and OB's own sorts translated. `sort_unstable*`, `select_nth_unstable*` and `BinaryHeap` are banned; `sort_by` only at the 4 `std::stable_sort` sites | ties are not masked |
| Locks | Only ob-base's `sync` module (parking_lot); `Mutex<T>` and `RwLock<T>` own the data they guard; `std::sync::Mutex`, `RwLock`, `Condvar` are banned | timed locks, no poisoning |
| Memory | bumpalo arenas through ob-base (`ObArenaAllocator`, `arena_alloc`); allocator-api2 only at budget owners; no `GlobalAlloc` or `Allocator` impl and no `A: Allocator` parameter in engine crates | Decision 12; section 2.2 |
| `unsafe` | Only in the seven named crates (section 3) | Decision 14 |
| Networking | Only sql-nio and ureq open sockets, through observer's cargo features `mysql` and `http`, which seekdb turns on; no crate below observer except sql-nio depends on mio, socket2, rustls or ureq | the wasm guideline |
| Toolchain | Rust 1.98.1 stable with clippy and rustfmt, edition 2024, `resolver = "3"`; no `#![feature]`, `-Z` flag or `RUSTC_BOOTSTRAP`; Cargo.lock committed; every build `--locked --offline` | Decision 16 |
| Profiles | `release` (the product), `judge` (every parity run: release semantics at opt-level 2, no LTO), `checked` (judge with overflow checks and debug assertions, weekly from Step 5, diagnostic only), `dev`. `panic = "abort"` in every profile. Nothing overrides these keys | s8-numerics-platform.md 3 |
| C and C++ built by cargo | deps/3rd's clang 17.0.6, SDK 26.2, the flags every reference compile command shares, set in rust/.cargo/config.toml; islands add their flags file derived from compile_commands.json | the islands must reproduce the reference's numbers (row 3a) |
| Third-party crates | NONE without a row in the table below | the kit's default |

**Allowed crates** (s8-numerics-platform.md 9.2 has every version and reason; `=` marks a pin whose output bytes are judged):

| Crate | Where |
|---|---|
| parking_lot, crossbeam-utils, crossbeam-channel, crossbeam-queue, arc-swap | ob-base `sync` and above |
| crossbeam-epoch | ob-epoch only |
| stacker; loom (dev) | ob-base's stack module; tests of lock-free code |
| bumpalo (feature `allocator-api2`), allocator-api2 0.2.x, hashbrown 0.17 (`default-features = false`, no `default-hasher`) | arenas; budget owners; lookup-only maps |
| seekdb-jemalloc-sys =0.2.2 (`stats`) | ob-platform |
| libc, windows-sys | named crates only |
| nix | the seekdb crate only (signal mask, `raise`) |
| flate2 =1.1.10 (default features only), miniz_oxide =0.9.1, mio, socket2, slab, rustls (ring, tls12), rustls-pki-types, x509-parser | sql-nio |
| crc32fast; crc32c | `CRC32()`; ob-base's `ob_crc64` |
| xxhash-rust (`xxh64`) | storage and log formats |
| md4, md-5, sha1, sha2, sm3, aes, sm4, des, ecb, cbc, cfb-mode, ofb, ctr, aes-gcm | digest and cipher functions; key folding, padding and IVs ported from src/share/ob_encryption_util.cpp |
| ureq (rustls with ring, blocking) | observer, feature `http` |
| fancy-regex | JSON schema `pattern` only, each site with a `TODO(port)` naming the dialect difference |
| rusqlite (`bundled`) | observer's meta.db |
| roaring | vector-index code only once known-answer vectors match CRoaring 3.0.0's bytes; until then vsag-sys's CRoaring bindings |
| log | ob-base's bridge |
| cc, cbindgen =0.29.4, cmake; bindgen-cli (a tool) | build scripts, island headers, the parser bindings (output checked in) |

**Banned:** tokio, async-std, smol, futures executors, async-trait; serde, bincode, rkyv, postcard (serde only as cbindgen's build dependency); nom, pest, lalrpop, logos, chumsky, peg and the regex crate for SQL; ryu, itoa, dtoa, lexical, fast-float, num-format, rust_decimal, bigdecimal, num-bigint; ahash, foldhash, fxhash, dashmap, scc; zstd, zstd-sys, libz-sys, zlib-rs and flate2's `zlib`, `zlib-ng` and `zlib-rs` features; tikv-jemallocator, jemallocator, mimalloc; rayon, portable-atomic, `AtomicCell<u128>`, crossbeam-skiplist; lazy_static, once_cell, bitflags, chrono, time, encoding_rs, rand.

**C libraries in the product:** zlib 1.2.13 and zstd 1.3.8 (their output bytes are judged), SQLite, libxml2, ICU 69, S2's libcrypto, ring, jemalloc 5.3.1, and the island archives (libs2.a; libvsag_static.a with its companions, libroaring.a among them). Their bindings live in ob-clib-sys, geo-sys and vsag-sys.

## 2. Constructs with no equivalent — one canonical mapping each

Every C++ construct Rust lacks gets exactly one translation, listed here; the design section named in the heading has the detail and the examples. An implementer who invents a second mapping for a listed construct breaks a rule; a reviewer who sees an unlisted construct flags it for this table.

### 2.1 Errors and error codes (s2-errors.md)

| C++ | Rust |
|---|---|
| a function whose `int` return is an OB code | returns `ObResult` (that is `ObResult<()>`); only a hand-designed core API returns `ObResult<T>`. Any other `int` return stays `i32` |
| output parameters (`T &out`, `T *out`) | stay `&mut`, in the C++ order, so a value written before an error stays visible |
| `int ret = OB_SUCCESS;`, `INIT_SUCC(ret)` | `let mut ret: ObResult = Ok(());` (the local-`ret` form) |
| choosing between `?` and the local `ret` | `?` only if, from every point where `ret` can first become an error, nothing but a log runs before the function returns; otherwise the local-`ret` form, every C++ statement in the C++ order, no `?` inside. When unsure, the local-`ret` form, which is always correct |
| `if (OB_FAIL(x)) {A} else if (OB_FAIL(y)) {B}` | `if ob_fail!(ret = x) {A} else if ob_fail!(ret = y) {B}`; empty branches stay |
| `OB_SUCC(x)` with a call in it | `ob_succ!(ret = x)` |
| `OB_SUCC(ret)`, `OB_FAIL(ret)` (and `OB_SUCCESS ==`/`!= ret`) | `ret.is_ok()`, `ret.is_err()` |
| `ret = OB_X;`, `ret = OB_SUCCESS;`, `return OB_X;`, `return ret;` | `ret = Err(OB_X);`, `ret = Ok(());`, `return Err(OB_X);`, `ret` |
| `OB_X == ret`, `OB_X == ret \|\| OB_Y == ret`; an if-chain or `switch` over codes | `matches!(ret, Err(OB_X))`, `matches!(ret, Err(OB_X \| OB_Y))`; `match ret { Err(OB_X) => .., _ => .. }`. A code held in a variable: `ret == Err(v)` |
| renames (`if (OB_HASH_NOT_EXIST == ret) ret = OB_ENTRY_NOT_EXIST;`) and resets | written as the C++ writes them; never `map_err` or `if let Err(e)` |
| `tmp_ret`, `OB_TMP_FAIL(x)` | its own `ObResult` local; `ob_fail!(tmp_ret = x)` |
| `COVER_SUCC(t)`, and any merge meaning "a unless a is success, then b" | `ret = ret.and(t)`; `a.and(b)`. Any other merge as the C++ writes it |
| `CK(a, b)`, `OX(s)`, `OZ(f(), k)`, `OV(c, OB_X, k)`, `OZX1`, `OZX2`, `FALSE_IT(s)` | `ck!(ret, a, b)`, `ox!(ret, s)`, `oz!(ret = f(), k)`, `ov!(ret, c, OB_X, k)`, `ozx1!`, `ozx2!`, `false_it!(s)` |
| `is_schema_error(ret)` and the other 23 predicates of src/share/ob_define.h | `ret.is_err_and(ObError::is_schema_error)` |
| a code the C++ ignores (`(void)f();`, an unused `int`) | `let _ = f();` |
| a code used as a value (`OB_ITER_END`, `OB_HASH_NOT_EXIST`, `OB_ENTRY_NOT_EXIST`, `OB_EAGAIN`, `OB_SIZE_OVERFLOW`, ...) | stays `Err(OB_X)`, returned, tested, renamed and reset at the same place. Never an `Option`, a `bool` or an enum during the port. A core API keeps the codes of the C++ API it replaces (`OB_HASH_NOT_EXIST`, `OB_ITER_END`, `OB_NOT_INIT` from a never-created hash map) |
| a local, parameter or field holding only codes | `ObResult`. One that also holds values that are not codes, or is sent or stored (`WarningItem::code_`, PX's `first_error_code_`, codes in rows and packets) stays `i32` under its C++ name; the inventory row says which |
| a code shared between threads | `AtomicI32` of the raw value, read through `ObError::from_code` |
| a code from outside the catalog (a tracepoint value, an island return, a PL `SIGNAL` number) | `ObError::from_code(x)` where it enters, which turns 0 into `Ok(())` |
| a comparator keeping its error in `int &ret = ret_;` | returns `ObResult<bool>` (`ObResult<Ordering>` where the C++ returns `int`); its "already failed" branch goes; only the port's sort, heap and binary-search functions, which stop at the first error, or a direct call that tests the result at once, may call it |
| `operator=` recording `error_ret_` | `fn assign(&mut self, other: &Self) -> ObResult`, still writing `error_ret_` where `is_valid()` reads it |
| `LOG_WARN(...)`, `LOG_ERROR(...)` and module variants | `log_warn!(ret, "same text", K(key), ...)`: the first argument is the `ret` in scope on the C++ line (`[errcode=N]`); INFO, TRACE and DEBUG take none |
| `ret = OB_X; LOG_USER_ERROR(OB_X, args...)` | `ret = Err(log_user_error!(OB_X, args...));`. Each `%.*s` takes one byte slice; a call whose arguments do not match its format is written as C's `vsnprintf` read it and marked `BUG(port):`. No user message is ever built with `format!` |
| the thread's warning buffer (`ob_setup_tsi_warning_buffer`, `ObWarningBufferIgnoreScope`) | changed only through `WarningBufferScope::install` or `ObWarningBufferIgnoreScope`, at the listed sites (s2-errors.md 2.6); a new site needs an inventory row. Never hold a buffer lock across a call that records a user message |
| `init()` and `is_inited_` | the constructor: `ObResult<Self>` if `init()` can fail with a code other than `OB_INIT_TWICE`, `OB_NOT_INIT` or a general allocation failure, else `Self`. The `OB_INIT_TWICE` and `IS_NOT_INIT` checks go, unless a caller tests the not-initialized state as an outcome |
| `unwrap` or `expect` on an `ObResult` | banned in engine crates |

### 2.2 Memory, ownership and out-of-memory (s3-memory.md)

| C++ | Rust |
|---|---|
| `ObIAllocator &` or `*` for statement or execution data | `&'q Bump` |
| `ObIAllocator` whose data the callee keeps (cache, plan, session, queue) | none: the callee takes an owned value |
| an `ObIAllocator *allocator_` member | nothing when the struct's data become owned collections; `&'q Bump` with a lifetime on a statement- or execution-scoped struct |
| an `ObArenaAllocator` object | `ob_base::ObArenaAllocator`, built with `counted_by(tracker)` exactly when the C++ arena drew from the request's memory contexts (the inventory row says `counted`) |
| `ObSafeArenaAllocator`, one arena shared by parallel tasks | one arena per task; results moved back as owned values; never `Mutex<Bump>` |
| an `ObString` parameter | `&[u8]` |
| an `ObString` member | `&'q [u8]` in statement- or execution-scoped structs; `Box<[u8]>`, `Vec<u8>` or `Arc<[u8]>` elsewhere, by the struct's owner; `Cow<'q, [u8]>` in types that live both inside a statement and beyond it (`RawExpr`'s byte fields, `ObObj`) |
| `assign_ptr`; `ob_write_string(alloc, src, dst)` | a borrow; `bump.alloc_slice_copy(src)`, or `src.to_vec()`, `Box::from(src)`, `Arc::from(src)` |
| placement new; an explicit destructor call | a constructor; `Drop` |
| `OB_NEW*`, `OB_DELETE*`, `ob_malloc`, `ob_free` | `Box::new`, owned collections, drop |
| putting a value into a bump arena | only `arena_alloc` (rejects any type that needs `Drop`), `alloc_slice_copy` or `alloc_str`; the other `Bump::alloc*` methods and `bumpalo::boxed::Box` are banned |
| `CURRENT_CONTEXT`, `WITH_CONTEXT`, `CREATE_WITH_TEMP_CONTEXT`, `CREATE_CONTEXT`/`DESTROY_CONTEXT`, containers bound to the current context | the owner as an argument or a field; a temporary context becomes a local `ObArenaAllocator` or owned collections |
| `ObMemAttr`, labels, ctx ids, `ObMallocHookAttrGuard`, `ObMallocCallbackGuard` | dropped; a label survives only as a `Budget` name |
| `inc_ref`/`dec_ref`, `revert_*`, hand-counted handles | `Arc` clone and drop. A last release that routes the object somewhere (a pool, a GC queue) is a handle with a private `Option<Arc<T>>` whose `Drop` calls `Arc::into_inner` |
| an object pool | goes, unless Decision 12 names its limit (only the KV-cache handle pool) |
| a guard handing out `const T *&` (`ObSchemaGetterGuard`) | returns `&'g T` borrowed from the guard, which holds an `Arc` of the schema version |
| memory handed to another thread, task, timer, queue, channel or cache | owned data: `Vec<u8>`/`Box<[u8]>` for one receiver, `Arc<[u8]>`/`Arc<T>` for several; strings the receiver keeps are copied first. Those APIs take `T: Send + 'static`, so `&'q` data does not compile there. A PX, DAS-parallel or DAG task never receives the requester's `ObMemTracker` or a counted arena |
| `deep_copy(char *buf, int64_t len, T *&out)` into receiver memory | a move of an owned value; the receiver charges its size |
| `OB_ALLOCATE_MEMORY_FAILED` (or `OB_SQL_RESOLVER_NO_MEMORY`) after a general allocation | removed: the allocation aborts the process (Decision 12). Inventory tag `allocation-guard` |
| the same where the size can be 0 or less (`ob_malloc` and the arenas return NULL then) | kept: `if n <= 0 { return Err(OB_ALLOCATE_MEMORY_FAILED); }`, marked `BUG(port):` |
| -4013 at a named budget owner or logical limit; -4013 injected by a tracepoint; a kept library's mapping to -4013 | unchanged, with the C++ formula, parameter, check point, code, message and behavior around it. Inventory tag `precondition-guard` |
| a comparison with `OB_ALLOCATE_MEMORY_FAILED` | kept as written |
| `ObBlockAllocMgr`, a FIFO or slice allocator with a limit, `MemoryUsageTracker` | a `Budget` and `Charge` at a named owner (the charge is taken before the memory and lives beside it); plain allocation elsewhere |
| a fallible allocation (`try_reserve`, `try_reserve_exact`, `Bump::try_alloc*`, allocator-api2 `try_*`) | only at the named budget owners and in client-sized buffers (a length taken from bytes the client sent, before any SQL-level size check), each with `#[allow(clippy::disallowed_methods, reason = "<inventory row>")]` |
| `Box::leak`, `Vec::leak`, `String::leak`, `std::mem::forget` | banned in engine crates |

### 2.3 Classes, templates, macros and other constructs (R12 §5, s4-sql-front.md 2.2 and 3.4, s1-crates-core.md 5.4)

| C++ | Rust |
|---|---|
| a class family held in an IR arena (expressions, DML statements, table items, logical operators, paths, log plans) | one family type: the base class's fields plus `kind`, an enum with one variant per concrete subclass. A subclass struct keeps its C++ name and holds its parent's fields in a field named `base`. `as_<class>()` and `as_<class>_mut()` replace `static_cast` and abort on a wrong variant |
| a class family whose base-class code calls its virtual functions (transform rules, `ObDMLResolver` and its subclasses, physical operators under `ObOperator`, expression operators under `ObExprOperator`, and copiers, replacers and visitors of that shape) | a trait whose provided methods are the base class's functions and whose other methods are the virtual ones; `base()`/`base_mut()` reach the base fields |
| a base class with subclasses in higher crates (`ObTimerTask`, virtual-table iterators), or a pure-virtual interface (`ObI*`) | a trait, used as `Arc<dyn Trait>`, `Box<dyn Trait>` or `&dyn Trait`, keeping the C++ name |
| any other closed family inside one crate | an `enum`, dispatched by `match` |
| `dynamic_cast`, a downcast after a type test | `match` on the variant or `as_<class>()`; for trait objects `as_any()` and `downcast_ref` |
| several base classes | one field per base, named after the base type in snake case |
| a destructor | `Drop` only when it releases something besides field memory |
| a template | generics whose bounds name exactly the operations the body uses |
| a full specialization, `enable_if`, trait dispatch | a trait with one impl per type (`macro_rules!` when repetitive); the primary body as a default method only when no impl overlaps |
| a non-type template parameter; a variadic template | a const generic; `macro_rules!` or a slice or tuple argument |
| a function-like macro | an `#[inline] fn` if it evaluates each argument once with fixed types; otherwise a lowercase `macro_rules!` |
| `OB_LIKELY`, `OB_UNLIKELY`, `OB_INLINE` | the bare condition; `#[inline]` |
| `DISALLOW_COPY_AND_ASSIGN`; `TO_STRING_KV` | no `Clone` or `Copy`; a `Debug` impl with the same keys, and an exact named function where the text reaches the client |
| `OB_UNIS_VERSION`, `OB_SERIALIZE_MEMBER` | the core's explicit little-endian encoding |
| `DEFER`, `ON_SCOPE_EXIT` | ob-base's scope guard |
| tracepoints (`OB_E`, `ERRSIM_POINT_DEF`), `DEBUG_SYNC` | ob-base's APIs, keyed by the same numbers and names (`ob_e!`) |
| `#ifdef ERRSIM`; `#ifdef NDEBUG`/`#ifndef NDEBUG` | `#[cfg(feature = "errsim")]`, off in `judge` and `release`; `#[cfg(not(debug_assertions))]`/`#[cfg(debug_assertions)]` |
| `OB_ASSERT(x)`; `OB_ASSERT_MSG(x, ...)`; `assert(x)`; `ob_assert(x)`, `abort_unless(x)` | `ob_assert!(x)`, which evaluates `x` in every profile (the reference's `-DNDEBUG` makes it `(void)(x)`); evaluate, log when false, then `debug_assert!`; `debug_assert!(x)`; `assert!(x)` |
| a platform `#if` | a `#[cfg]` arm for every arm the C++ has |
| an X-macro `.def` list | generator output, or `macro_rules!` over a Rust copy |
| a tagged union with the tag in the same struct | an `enum` |
| a union over a flag word with a bitfield struct | one integer field; a getter and setter per C++ member, named after it (`is_null_()`, `set_is_null_(v)`), with masks and shifts; no bitfield crate |
| a type-punning union, `reinterpret_cast` on bytes | `to_bits`/`from_bits`, `to_le_bytes`/`from_le_bytes` |
| `goto` to a common exit; a backward `goto` | a labeled block with `break 'label`; `loop` with `continue 'label` |
| any other `goto` (ob_dtoa.cc) | `loop { match state }` over an enum named after the C++ labels |
| `switch` fallthrough | `A \| B` for empty cases; a non-empty case that falls through repeats the next case's code |
| default arguments | every call passes every argument; the declaration index gives each default |
| overloads in one scope | numbered in header order: the first keeps the name, then `_2`, `_3` (constructors `new`, `new_2`); the declaration index gives the numbers |
| a nested type `Outer::Inner` | `Outer_Inner`, in `Outer`'s file |
| `==`, `!=`; `<`, `>`, `<=`, `>=` | `PartialEq` (`Eq` only if reflexive, so never with float fields); `PartialOrd` (`Ord` only if total); sorting still uses the ported algorithms |
| `operator()`; `operator=`; `[]`; `++`, `--`, `->`, unary `*` | a method named `call`, called through a closure; `Clone`/`clone_from` if it cannot fail, else the fallible `assign`; `Index`/`IndexMut`; `Iterator`, `Deref`/`DerefMut` |
| arithmetic operators; conversion operators; `operator new`/`delete` | `std::ops` only when the C++ cannot fail, else named methods returning `ObResult`; `From` or a named method; removed |
| `friend`; `protected`; `private` | members a friend uses become `pub(crate)` (`pub` for a friend in another crate; test friends are dropped); `pub(crate)` (`pub` if a subclass is in another crate); private, except friends and members of split types (`pub(crate)`) |
| an unscoped enum, or an `enum class` built from integers | `#[derive(Clone, Copy, PartialEq, Eq)] #[repr(transparent)] struct Name(pub <int>)` with a `const` per enumerator under its C++ name; `switch` becomes `match` on the consts. Enums shared with C are generated from the header |
| any other `enum class` | a Rust `enum` with the same `#[repr]` |
| an identifier that is a Rust keyword | a raw identifier (`r#type`, `r#in`, `r#gen`); `self`, `Self`, `super` and `crate` take a trailing `_` |
| a pointer or reference the unit ever sets, passes or returns as NULL | `Option<..>`. One that is never NULL is the value, reference or id itself, and an `OB_ISNULL` test on it is dropped with the rest of its `else if` chain kept in order |
| a dereference without a check | `unwrap()` or indexing, so a panic stands where the C++ would crash; never anywhere else |
| `at(idx)` unchecked under `NDEBUG`; `at(idx, obj)` returning `OB_ARRAY_OUT_OF_RANGE` | `[idx]`; `get(idx)` mapped to the same code |
| `push_back` on `ObArray`/`ObSEArray`/`ObFixedArray` | the container keeps its C++ signature and its codes other than out-of-memory (`OB_SIZE_OVERFLOW`, `OB_NOT_INIT`, an element's failing `assign`) |
| exceptions | only at island edges, where the C++ entry catches everything (section 3); restore-and-rethrow becomes a `Drop` guard; a local throw and catch becomes an early return of the same code |
| `setjmp`/`longjmp` | stays inside the parser's C core; no Rust frame ever sits between the parse entry and a callback |
| `std::regex` | fancy-regex for JSON schema `pattern` only, with a `TODO(port)`; plain string matching for the two log-file patterns |

### 2.4 The parse tree and the IR (s4-sql-front.md)

| C++ | Rust |
|---|---|
| a `const ParseNode &` or `ParseNode *` parameter | `node: NodeId`, or `Option<NodeId>` where it can be NULL; the tree comes from `params_.parse_tree_` or a `pt: &ParseTree<'q>` (`&mut` if the function or a callee writes) first after `self` |
| `node->children_[i]`; `node->children_[i] = x` | `pt.child(node, i)`; `pt.set_child(node, i, x)` |
| a field; a bit field; a lane of `value_` | `pt[node].type_`; `pt[node].is_neg_()` / `set_is_neg_(1)`; `pt[node].int16_values_(i)` |
| `str_value_`, `raw_text_`; `NULL == node->str_value_` | `Option<&'q [u8]>`; `pt.str_value_is_null(node)`, `pt.raw_text_is_null(node)`. `ObString(node->str_len_, node->str_value_)` is `pt[node].str()` |
| a node made after the parse (`new_terminal_node`, ...); `memset` or `alloc(sizeof(ParseNode))`; a local `ParseNode` | the `ParseTree` method of the same C name; `new_zeroed_node()` |
| bytes stored into a node | copied into the statement bump with `alloc_bytes` first |
| an object the C++ compares by address, keys a map by address, packs into an integer, or points to from more than one place | a typed id (`ExprId`, `StmtId`, `TableItemId`, `OpId`, `PathId`, `LogPlanId`, ...) into an `Arena` owned by its context. Everything else is a value |
| a new object | a new id only where the C++ allocates (`create_raw_expr` including the copiers' copies, `create_stmt`, `create_table_item`, the plan and operator factories, `alloc_join_path`); a raw allocation followed by placement new is `alloc(T::default())`, then `replace(id, value)` |
| an in-place edit (`assign`, `formalize`, type deduction, flags) | keeps the id |
| pointer equality; `find_item`, `append_array_no_dup`, `remove_item`, `intersect`, `is_subset` | id equality, where the C++ compares pointers; the same bodies |
| a map or set keyed by address | `IdHashMap`/`IdHashSet`: `ObHashMap`'s method names, arguments and codes, a fixed hasher, insertion order |
| `ObRawExpr *&` (and `ObDMLStmt *&`, `TableItem *&`, `ObLogicalOperator *&`) | `&mut ExprId`, or `&mut Option<ExprId>` where the slot can hold NULL. A slot inside an arena is copied into a local, passed as `&mut local`, and written back right after the call on every path where the C++ writes through the reference, error paths included; if the callee reads the slot's container after writing, pass the owner's id and the index instead |
| `ObRawExprFactory &`; a context holding a factory | `&mut ObRawExprFactory<'_, 'q>` (the compilation's expression store plus the arena it creates in); `exec_ctx->get_expr_factory()` is `f.own()`. A local `ObRawExprFactory` is one of four kinds, named by its inventory row (s4-sql-front.md 2.3) |
| a member that points to a context, factory or other arena owner (`params_`, `ctx_`, `optimizer_context_`, `expr_factory_`) | a parameter of the functions that use it, named after the member, first after `self`; never a field |
| a member function of an arena object that follows a pointer to another IR object | an associated function of the same class with `this: <Id>` first and the context next; one that touches only its own fields stays a `&self`/`&mut self` method |
| a virtual function of an arena family | a `match` on `kind` in the base function, calling the subclass's function with the same id |
| back-pointers (`expr_factory_`, `inner_alloc_`, `rt_expr_`, `my_plan_`, `parent_`) | context or id fields (`rt_expr_` a map in the code generator) |
| cycles (`ref_stmt_`, `outer_expr_`, `ref_query_`) | plain id fields |
| an address packed into `int64_t` | `id.to_i64()`, `Id::from_i64(v)` |
| the C++'s own ids (`stmt_id_`, `table_id_`, column ids, operator numbering) | separate fields with their own counters; arena ids are never printed (no `KP(` log keys), sorted on, or hashed into output |

### 2.5 Execution, sorting and hashing (s5-execution.md)

| C++ | Rust |
|---|---|
| `ObDatum` | ob-values' 12-byte `ObDatum` with the C++ getter and setter names. A non-null value of 8 bytes or fewer with no flag lives inside the datum; longer values are read through the frame's `BufTable` (`bufs.bytes(&d)`); only `ObDatum::from_bytes` builds a reference |
| a datum kept past its producer's next batch | copied into a store the operator owns, charged to the work area |
| `ObEvalCtx`, the frames | `EvalFrame`; read arguments and write results through `frame.split(expr)`; copy argument datums out; no borrow of the frame lives across a call that takes `&mut EvalFrame` |
| an expression's result memory | the reserved buffer when the length is at most `res_buf_len`, otherwise the dynamic buffer grown to `next_pow2(size)`, the C++ sizes |
| `MEMMOVE`, a copy whose source may be the destination | `copy_within`, or copy the source first; each site has an inventory row |
| the evaluation state machine (`eval`, `eval_batch`, the default batch function) | translated statement for statement: a failed datum set to NULL, the first failing row stops the batch |
| `ObOperator::get_next_batch` and each operator's `inner_get_next_batch` | the `ObOperator` trait: `get_next_batch` is provided and written once in the core; operators implement `inner_get_next_batch` and never rewrite the wrapper |
| a `sizeof` inside a formula that decides a batch size, a table size, a dump or a bypass | the C++ number as a named constant (`sizeof(ObDatum)` is 12, `sizeof(HTBucket)` is 16); never `size_of` of a Rust type |
| storage calling SQL (black filters, row filters, generated columns, the skip index) | a `ScanHost` call exactly where, and as often as, the C++ calls into SQL |
| `lib::ob_sort`, `std::sort`, `qsort`, `std::make_heap`/`push_heap`/`pop_heap`/`sort_heap` | ob-base's `ob_sort` and heap functions; a fallible comparator returns `ObResult<bool>` and the sort stops at the first error |
| `std::stable_sort` (4 sites) | `sort_by`, with an `#[allow]` citing the row |
| `ObAdaptiveQS`, `ObBinaryHeap`, the partition, prefix, unique and top-N sorts, and the rule that picks among them | translated, never replaced |
| murmurhash64A, murmurhash2, `fnv_hash2`, the datum and collation hashes | ob-base's `hash` module and ob-values' tables, bit for bit, with the C++ seeds (16777213, 99194853094755497, 0); a C `char` read as `i8` |
| `NAN` in a hash or a value | the bits written out (`f64::from_bits(0x7ff8000000000000)`), never `f64::NAN` |
| `ObHashMap`, `ObHashSet`; the hash-join and hash group-by tables | ob-base's ports with the C++ bucket count, bucket choice, chain order and iteration; the C++ layouts, probing and first-seen group order |

### 2.6 Numbers and overflow (s8-numerics-platform.md 1-2)

| C++ | Rust |
|---|---|
| a float expression | the C++ order, grouping and precision; `float` stays `f32`; promotions written out (`x * 0.5` with `x: float` is `(x as f64) * 0.5`) |
| `a*b+c` | a multiply and an add, two roundings. Never `mul_add`, an `algebraic_*` method or an FMA intrinsic |
| libm | one to one in the precision the C++ overload resolves: `pow`→`powf`, `log`→`ln`, `fmod`→`%`, `fabs`→`abs`, `rint`/`lrint`→`round_ties_even`, `round`→`round`, `fmax`/`fmin`→`f64::max`/`min`, `std::max(a, b)` on floats → `if a < b { b } else { a }`; never `powi` |
| a float comparison | ported literally: NaN after every number and equal to NaN, -0.0 equal to 0.0, DOUBLE(M,D)'s tolerance. Never `total_cmp`, `partial_cmp().unwrap()`, `clamp` or `signum` in place of code that does something else |
| `static_cast<int64_t>(double)`; a float passed to a C variadic formatter | `as i64`; `as f64` |
| wraparound the C++ relies on (hashes, checksums, counters, sequences, add-then-test operators) | `wrapping_*`; a plain operator only where overflow cannot happen |
| an overflow check | kept with its code and message text, over the wrapped result; `__builtin_*_overflow` becomes `overflowing_*`, or an `i128` computation when the types differ |
| `/` and `%` | the C++ guard stays in place. An unguarded division that an inventory row shows can reach a zero divisor or `MIN / -1` returns the arm64 result (`checked_div(..).unwrap_or(0)`, `checked_rem(..).unwrap_or(a)`), marked `BUG(port):` |
| a shift | plain for a constant below the width; `wrapping_shl`/`wrapping_shr` for a computed amount that can reach the width |
| `char` read as a number; `long`, `unsigned long`; `size_t` | `i8`; `i64`/`u64` on every target, never `isize` or `c_long`; `usize` for lengths and indexes, `u64` where it reaches a hash, output or an encoded field |
| mixed signed and unsigned operands | the C++ usual arithmetic conversions written out (`(i as u64) < u`) |
| negation or absolute value of a value that can be the minimum | `wrapping_neg`, `wrapping_abs` |

### 2.7 Concurrency and the stack (s8-numerics-platform.md 4-6)

| C++ | Rust |
|---|---|
| a field one thread writes while another reads (anything touched by `ATOMIC_*`, and its plain reads and writes) | an `Atomic*` or a field inside a lock. Every operation is `SeqCst`, except the 29 `ATOMIC_LOAD_ACQ`/`_LOAD_RLX`/`_STORE_REL`/`_STORE_RLX` calls, which keep their order; a plain C++ read or write becomes a `SeqCst` `load` or `store` |
| `ATOMIC_LOAD`/`STORE`, `SET`/`TAS`, `FAA`/`FAS` (`_AF` adds the delta), `INC`/`DEC`, `BCAS`, `VCAS`/`CAS`, `ANDF` | `load`/`store`, `swap`, `fetch_add`/`fetch_sub`, `fetch_add(1)`/`fetch_sub(1)`, `compare_exchange(..).is_ok()`, the `Ok` or `Err` value, `fetch_and` |
| reference counts, lock words, list links, reclamation clocks (QClock, the retire station, hazard versions) | replaced, not retyped: `Arc`, `sync` locks, crossbeam queues, arc-swap, ob-epoch (only where a gate measurement needs lock-free reads) |
| `SCN`, `ObTxSEQ`, LSN | `Copy` types and atomic wrappers keeping the C++ member names; `SCN::inc_update` stays a compare-and-swap loop |
| 128-bit atomics | a `Mutex` or two atomics |
| `AtomicPtr`, fences, `PAUSE`, `volatile` | not outside named crates; `volatile` becomes an atomic where it signals between threads, a plain field otherwise; `CACHE_ALIGNED` becomes `CachePadded` |
| an OB lock (`ObLatch`, `ObSpinLock`, `ObRowLatch`, ...) and its guard | `sync::Mutex<T>` or `RwLock<T>` owning the guarded data; a function the C++ calls with the lock held takes the guarded data or the guard. The guard is bound to a named local or a `match` arm, never a temporary in a condition or a tail expression; callbacks, IO and calls into another crate run after it drops; nested locks keep the C++ order (in the lock's inventory row) |
| a timed lock | `try_lock_until(abs_timeout_to_instant(..))`, returning the site's C++ code |
| `ObThreadCond`/`ObCond`; `ObLightyQueue`, `ObFixedQueue`, `ObLinkQueue`; `ObRecursiveMutex` | `Condvar` with the state in the `Mutex<T>`; a bounded crossbeam channel, `ArrayQueue`, `SegQueue` or a channel; `ReentrantMutex<T>` or a restructured site, per its row |
| a thread-local | state that changes a result, an error or a limit is a parameter or context field. `thread_local!` holds only the closed list: const-initialized `Cell` values (request deadline, trace id, thread name, diagnostics, caches that change no result, the stack bookkeeping), the warning-buffer slot and sql-parser-sys's parse slot |
| `SMART_CALL(f)`, `SMART_CALL_LARGE(f)` | `smart_call!(f)`, `smart_call_large!(f)`, at the same place around the same call; recursive algorithms keep their shape |
| `check_stack_overflow(is_overflow)`; `check_stack_overflow()` | `check_stack_overflow(&mut is_overflow, get_reserved_stack_size())`; `check_stack_overflow_2()` |
| a local over 16 KiB, `SMART_VAR`, `HEAP_VAR` | on the heap or in an arena |
| deep recursive data (JSON, XML, trees) | never dropped, cloned or printed recursively: an id arena or an iterative `Drop` |

### 2.8 Services, contexts, timers and process state (s1-crates-core.md 3-4)

| C++ | Rust |
|---|---|
| `server_service<T>()`, `GCTX.x`, `X::get_instance()`, a singleton macro | a field of the lowest context whose crate may name the service (`RuntimeContext`, `StorageContext`, `SqlContext`, `ServerContext`; each derefs to the one below). A service holds the `Arc`s it uses from construction; an object it owns holds `Weak` of its owner; another function takes `ctx` after `self` (first in a free function) typed as the declaration index says; the SQL tier reaches it through the exec context. A lookup no context can serve goes through a field set at construction or a parameter. Never a `static` or a registry |
| `GCONF.p`, `GMEMCONF` | `ctx.config().p()`, keeping the parameter's name |
| a null check of a slot, `SERVER_MODULE_SCOPE` | dropped, unless the inventory row says the site can run before the module graph exists |
| `getcwd()` then a relative path the engine opens; `getcwd()` in a printed value | `ctx.base_dir().join(..)`; the C++ `"%s/%s"` over `base_dir`'s bytes |
| an `ObTimer` member; an `ObTimerTask` subclass | one Rust `ObTimer` of the same name; an `impl ObTimerTask` holding `Weak<Owner>`, method `runTimerTask`, returning early when the owner is gone; intervals unchanged |
| `THIS_WORKER` deadline calls; other `THIS_WORKER` calls | the thread-local deadline; explicit fields of the request or exec context |
| process exit, `fork`, signal functions, `chdir`, `setlocale` | only in the seekdb crate and ob-platform (clippy bans them elsewhere); engine code ends the process only by panicking |
| a static table | only the process-wide state of s1-crates-core.md 3.6 (tables the same for every engine, the logging facade, tracepoint and `DEBUG_SYNC` registries, third-party hooks, unique-number counters, the closed thread-local list), each in migration/process-statics.tsv; a value computed from one engine's options or config is a context field |

### 2.9 Storage behavior (s6-storage.md)

Storage units follow these, and a reviewer rejects a change that breaks one:
- The estimator's inputs stay identical: the memtable walk of 500 rows from each end with its five DML-flag cases, the `ObKeyBtree` shape (15 keys per node, leaf splits at the running average), the sstable border counting with its 1,000x rule, the table combination and clamps, the per-tablet counts as defined, the row delta, and every freeze trigger with its parameter. Formats, block layout and the rows-per-micro-block cut are free.
- Rows come back in the C++ scan order: rowkey order through the loser tree's (range index, rowkey) order; one rowkey compare everywhere (the value library's null-first compare); tables without a primary key in per-tablet `__pk_increment` order, cached 10,000 at a time; batches end only for the four C++ reasons (capacity, the end of a micro block in block scan, a SINGLE_ROW to BATCH switch, the LIMIT count).
- The schema service and bootstrap read and write inner tables through the typed catalog trait, never through SQL text; other leaf code keeps inner SQL through `ObISQLClient`.
- Generated files are regenerated, never edited; a unit never copies generated data by hand.

### 2.10 The three rules for everything else

**The BUG rule:** when the C++ behavior is itself defective (malformed output, a wrong code, a crash), reproduce it bug for bug and mark the site `BUG(port):` with a repro and the C++ file:line. Behavior matching (Step 6) asserts the defective output; fixes ship only in a flagged post-parity change (prompt 06). The port's job is fidelity; improvement is a separate, reviewable commit.

**The error-recovery rule:** allocation failures and structural errors often share one sentinel in the C++ (a NULL, -4013). They must not share one Rust construct. A general allocation failure has no Rust branch: the allocation aborts the process (Decision 12). A budget owner, a logical limit or a structural error keeps its code at its place and returns whatever partial result the C++ returned. Every guard row in the inventory records which it is, `allocation-guard` or `precondition-guard` (templates/inventory.tsv).

**The UNKNOWN rule:** when neither this document nor the inventory decides a case, translate to the most conservative representation Rust offers (an owned copy, the exact C++ code and control flow, no `unsafe`, no `unwrap` where the C++ checks, the local-`ret` form), mark it `TODO(port):` with the open question and the C++ file:line, and keep moving. UNKNOWN is an answer; a stalled batch is not.

## 3. The sanctioned escape hatch

Translations that cannot be expressed safely get one place to go, used visibly.

**`unsafe` appears only in seven named crates** (Decision 14 (b); ARCHITECTURE §8). Every other crate carries `#![forbid(unsafe_code)]` in its generated lib.rs and inherits `unsafe_code = "forbid"` from the workspace lints; a gate fails if the crates without it differ from this list. The list is closed: a new kind of use needs a row here first.

| Crate | May contain |
|---|---|
| geo-sys | in its `ffi` module: the entry declarations and calls, the `obgeo_rs_*` callbacks, pointer-to-reference conversions; `Send`/`Sync` impls for the handle types s7-islands-unsafe.md 7.3 names |
| vsag-sys | the same for `obvsag`; CRoaring's bindings, its six memory-hook functions and `roaring_init_memory_hook`; the `Send`/`Sync` impls of 7.4 |
| sql-parser-sys | the parser bindings; `parse_sql` and the other entries; the callbacks under their C names (`parse_malloc` family, `check_mem_status`, `try_check_mem_status`, the two stack checks, `lookup_pl_symbol`, `murmurhash`, the charset helpers, the parser_utility.h functions); the C-tree walk that builds `ParseTree`; the per-thread parse slot |
| ob-clib-sys | the zlib, zstd 1.3.8, libxml2 and ICU bindings, calls and library callbacks; `Send` for handle types |
| ob-simd | `core::arch` loads and stores; later, x86 `#[target_feature]` functions after run-time detection inside the crate |
| ob-platform | aligned IO buffers; `unsafe impl GlobalAlloc` over `je_*` and the exported `je_malloc_conf`; only these libc calls: `_exit`, `fork`, `lockf`, `pthread_set_qos_class_self_np`, `setpriority(PRIO_DARWIN_THREAD)`, the malloc-zone promotion, `statvfs`, `fcntl(F_PREALLOCATE)`, `sysconf(_SC_PAGESIZE)`, `sysconf(_SC_PHYS_PAGES)`, `pthread_threadid_np`, and the Windows named-pipe creation |
| ob-epoch | crossbeam-epoch's `unsafe` methods inside safe types; each type has a loom test |

Rules for the named crates (s7-islands-unsafe.md 7.10):
1. The public API is safe: no `pub unsafe fn`, no raw pointer in a public signature, no `unsafe trait` for other crates to implement. Types holding C pointers are private-field structs with `Drop`.
2. `unsafe` sits only in an island's or library's `ffi` module, ob-simd's kernel modules, ob-platform's libc and allocator modules, and ob-epoch's type modules. `#[unsafe(no_mangle)]` appears only there, on names carrying the crate's tag or on existing C names the kept code or jemalloc reads.
3. One operation per `unsafe` block, with `// SAFETY:` on the line above naming the rule it relies on; `unsafe impl` gets the same. clippy's `undocumented_unsafe_blocks` and `multiple_unsafe_ops_per_block` are denied in named crates, and `unsafe_op_in_unsafe_fn` everywhere.
4. No `static mut`. `unsafe impl Send`/`Sync` only for the types the islands section names.
5. A view from C becomes a slice only through one helper that returns `&[]` for length 0.
6. ob-simd kernels use the C++'s intrinsics in the C++'s order with the same lane partial sums, never an FMA the C++ does not call, and each has a scalar version that reproduces the NEON lanes bit for bit.

**Island boundaries** (s7-islands-unsafe.md 7.2; the parser keeps its existing C names and signatures, 7.5):
- One header per island (`obgeo.h`, `obvsag.h`), generated by cbindgen =0.29.4 from the Rust `ffi` module and committed; the island C++ compiles against it, so drift fails the C++ build.
- C++-implemented entries `<tag>_<verb>`, Rust callbacks `<tag>_rs_<verb>`, structs and handles `<Tag><Name>`; tags `obgeo` and `obvsag`.
- Only fixed-width integers, `float`, `double`, `bool`, views `(const T *, int64_t count)` over those, `#[repr(C)]` plain structs and opaque handles cross. Never `size_t`, `long`, `ObString` or a C++ type.
- One entry per call the SQL tier makes today, never merging two calls the SQL tier does something between; each returns an OB code as `int32_t`, which Rust turns into `ObResult` with `ObError::from_code` at once.
- Every C++ entry is `noexcept` with a catch-all that uses the call path's existing handler (geo's `ob_boost_geometry_exception_handle()`), or logs and aborts where the path has none today. Rust declares entries `extern "C"`, never `"C-unwind"`.
- Each side frees only what it allocated; a handle has one create and one destroy entry, called from the Rust wrapper's `Drop`; memory an island keeps after an entry returns comes from a Rust receiver that is the thing charged.
- A callback takes its receiver first, never finds it through a global, returns before the island continues, never re-enters the island and never calls `smart_call!`; vsag callbacks run on vsag's threads and read no Rust thread-local.

**The earlier sql-nio port's conventions** (/Users/colin/obsidian/tech/seekdb/migrate to rust/abi-naming.md and notes/ffi-mechanics.md) are prior art, not rules: each is adopted or rejected with its reason in s7-islands-unsafe.md 7.12 (the island ABI conventions), s3-memory.md 3.8 (memory) and s4-sql-front.md 5 (the parser converter). Among the rejected: C++ leases on Rust buffers, test stubs for island symbols, the `shim` marker, 0/-1 return codes, and `panic = "abort"` only on some profiles.

**The other visible escape:** a banned method or type at a site the design allows (a `try_reserve` at a budget owner, `sort_by` at a stable-sort site, `Builder::spawn` in the spawner) carries `#[allow(clippy::disallowed_methods, reason = "<inventory row>")]` (or `disallowed_types`) on the item, citing its row.

**Markers.** Every deferred decision carries a greppable marker. The format is load-bearing: do not vary it.

| Marker | Exact form | Content |
|---|---|---|
| `TODO(port)` | `// TODO(port): <text>` | the open question and the C++ `file:line` |
| `PERF(port)` | `// PERF(port): <text>` | what the faster version would be, and the C++ `file:line` |
| `BUG(port)` | `// BUG(port): <text>` | the C++ defect, a repro and the C++ `file:line`; the code reproduces the defect |
| `SAFETY` | `// SAFETY: <text>` | why the one operation in the next `unsafe` block or impl is sound, citing the rule |

Each marker sits on its own line directly above the code it concerns: never after code on the same line, never in a block or doc comment. The markers are the queue for later steps; their counts per crate are reported at every gate next to the `unsafe` counts.

## 4. Naming and output paths

Done-ness is detected by output files existing on disk, so these rules are what make the run resumable. They are not style preferences (s1-crates-core.md 5).

- **The tree.** One cargo workspace in rust/; crate `<name>` in rust/<name>/, lib name `<name>` with `-` replaced by `_` (`ob-base` is `ob_base` in paths). The 39 crates and what each may use are ARCHITECTURE.md §1.1's table, the only copy. A crate uses only the crates on its row; crates 7-39 except sql-nio may also use ob-errno and ob-base; sql-nio uses ob-platform only.
- **Placement.** Every tool reads one prefix map (migration/crates.tsv at sign-off; until then migration/design/evidence/rulebook/crates/crates-design.tsv): columns `prefix`, `crate`, optional `dir`; the longest matching prefix wins; a prefix that names no directory matches file names that start with it.
- **Target path.** A unit writes exactly one file, `rust/<crate>/src/<dir>/<stem>.rs`: `<crate>` from the unit's `source` file's longest prefix match; `<dir>` the source's directory below the matched prefix's directory (below its parent for a prefix that names no directory), preceded by the row's `dir` value if any; `<stem>` the C++ stem unchanged. A class declared in query/api or data_plane/api goes where its .cpp is. A directory named after a keyword stays on disk and is declared `mod r#static;`. A stem named `lib`, `main` or `mod` takes a trailing `_` (src/observer/main.cpp becomes rust/seekdb/src/main_.rs). A core unit that replaces exactly one C++ stem takes that stem's path; other core modules take the design's names.

  | Source | Target |
  |---|---|
  | src/sql/optimizer/ob_join_order.cpp | rust/sql-optimizer/src/ob_join_order.rs, then ob_join_order_p01.rs onward |
  | src/sql/engine/ob_operator.cpp (prefix `src/sql/engine/ob_`) | rust/sql-exec/src/ob_operator.rs |
  | src/share/io/ob_io_manager.cpp (prefix `src/share/io`) | rust/ob-runtime/src/ob_io_manager.rs |
  | src/storage/compaction/ob_tablet_merge_task.cpp | rust/storage-engine/src/compaction/ob_tablet_merge_task.rs |

- **Files of 4,000 lines or more** split: the head unit keeps the map's key and writes `<stem>.rs` with the types and the header's inline functions; pieces `<unit_id>.p01`, `.p02` write `<stem>_p01.rs`, `<stem>_p02.rs` in source order, each holding `impl` blocks and functions for one contiguous range of the .cpp, cut at a function boundary, under 4,000 lines. Every member of a split type, and every function a piece defines, is `pub(crate)`. A header of 4,000 lines or more is cut at class boundaries. The declaration index gives each item's piece path.
- **Manifests.** migration/manifest.tsv (Step 3) and migration/core-manifest.tsv (the core) have the columns `source`, `target`, `unit_id`, `kind` (`stem`, `split` or `subsystem`) and `inputs` (the unit's files, with `:<from>-<to>` line ranges for pieces), in that order; the queue runner reads only the first two. Core `unit_id`s are `core/<crate>/<module>`. migration/not-translated.tsv gives every other in-build unit one reason: `island`, `generated`, `data`, `vendored`, `dead`, `dropped` or `deferred`. Every in-build unit appears exactly once across the three files.
- **Generated files.** Every lib.rs, main.rs and mod.rs is written by a script from the manifests (module lines, lint attributes, `#![forbid(unsafe_code)]`). A unit writes no `mod`, `#![..]` or `extern crate` line. Generator output goes to `rust/<crate>/src/<dir>/generated/` with a DO-NOT-EDIT banner, is checked in, and a gate regenerates and diffs it; no unit edits it.
- **Identifiers** stay as in C++: `ObJoinOrder`, `runTimerTask`, fields with their trailing `_`, enumerators and constants in capitals; macros become lowercase `macro_rules!`; keywords, overloads and nested types as in 2.3. A core type that replaces one C++ type keeps its name (`ObDatum`, `ObHashMap`, `ObWarningBuffer`, `ObTimer`, `ObTimerTask`, `ObServerOptions`); one with no single counterpart has a plain name (`ParseTree`, `EvalFrame`, `ScanHost`, `ThreadSpawner`, `RuntimeContext`).
- **Imports** are full paths from migration/decl-index/<unit_id>.txt. No glob imports. Exported macros are imported with `use` like any other item and invoked by their bare name. Two imported items with the same name: the second is written by its full path at each use; no `use .. as`. Error-code constants are imported by name like any other item.
- **Home modules** for every shared type and helper; units never write local copies:

  | Crate (module) | Holds |
  |---|---|
  | ob-errno | `ObError`, `ObResult`, the catalog constants and lookups (`ob_strerror`, `ob_errpkt_errno`, ...), the C formatter `cfmt`, `ob_fail!`, `ob_succ!`; the `ob_error` binary |
  | ob-base | the log macros and `K()`, `log_user_error!`/`warn!`/`note!`, `forward_user_*!`, the check macros (`ck!`, `ox!`, `oz!`, `ov!`, `false_it!`), `ObWarningBuffer` and `WarningBufferScope`; `smart_call!`, `smart_call_large!`, `check_stack_overflow*`, `get_stackattr`/`set_stackattr`; `sync`; `ObHashMap`, `ObHashSet`, `IdHashMap`, `IdHashSet`, murmurhash64a, murmurhash2, `fnv_hash2`, `ob_crc64`; `ob_sort` and the heap functions, `ObBinaryHeap`; tracepoints (`ob_e!`) and `DEBUG_SYNC`; the scope guard; `Id<T>`, `Arena<T>`; `allocator`: `Budget`, `Charge`, `ObArenaAllocator`, `arena_alloc`, `ObMemTracker` |
  | ob-values | `ObDatum`, `DatumVec`, `ObDatumVector`, `ObBitVector`, `BufTable`, `BufId`, `SharedBytes`, `ObObj`, `ObNumber`, decimal-int, dtoa and `ob_strtod`, charsets, the datum compare and hash tables |
  | ob-runtime | `RuntimeContext`, `ThreadSpawner`, `ObTimer`, `ObTimerTask`, `ObTimerService`, config, IO, the KV cache, `ObDataVersionMgr`, `ObSqlRequestOperator` |
  | storage-api | the filter nodes and executors, `ScanHost`, `ScanColumns`, `ObSqlDatumInfo`, the aggregate protocol traits, `ObIStorageEstimator`, `ObIOptimizerStorageService`, `ObPartitionEst` |
  | storage-tablet | `StorageContext` |
  | sql-parse-tree | `ParseTree`, `ParseNode`, `NodeId`, `ParseResult`, `OwnedParseNode`, `ObItemType` |
  | sql-ir | `RawExpr` and `ExprId`, `RawExprStore`, `ObRawExprFactory`, `DMLStmt` and `StmtId`, `ObStmtFactory`, `ObQueryCtx`, the copiers |
  | sql-exec | `SqlContext`, `ObExpr`, `EvalFrame`, `ExprSlot`, `ObEvalInfo`, `ObBatchRows`, the `ObOperator` trait, the row stores |
  | observer | `ServerContext`, `Server`, `ObServerOptions` |

- **Tests.** Step 3 units write none; the judge is the test. Core crates keep differential tests in `rust/<crate>/tests/`, with vectors recorded from the C++ reference; only core and island crates link test-only C++.

## 5. The gap inventory

Decisions about ownership and lifetimes, handoff to other threads, borrowed views, hand-counted handles, atomics on plain fields, error codes used as values, sort and hash order, pointer identity, integer overflow and float contraction are **not made by implementers**. They are looked up in migration/inventory.tsv, one row per site, built next by prompt 02 (PLAN §6, Step 1). If your site is not in the inventory, that is an inventory bug: flag it, apply the UNKNOWN rule, and keep moving.

- **Columns** (templates/inventory.tsv): `file`, `symbol`, `source_construct`, `classification`, `target_translation`, `evidence`, `status`. `evidence` cites where the value is created, changed and escapes; guard rows carry `allocation-guard` or `precondition-guard`; `status` is `confirmed` or `unknown` (an `unknown` row still has a deterministic translation with its `TODO(port)`).
- **The sites** come from the sweep lists in migration/inventory/sweep/ (summary.tsv defines each):

  | Sweep file | What its rows decide |
  |---|---|
  | ret-compare.tsv, reset.tsv, tmp-ret.tsv | what each code means at the site (end of data, lookup outcome, buffer too small, retry, becomes a warning, ignored, renamed); whether a code-holding field is `ObResult` or `i32` (2.1) |
  | ret-alias.tsv | for each `int &ret = ret_` comparator: what consumes it (sort, heap, search, direct call) and whether any caller reads the slice after an error |
  | oom-sites.tsv | `allocation-guard` or `precondition-guard`, and for a budget owner its formula and check point (2.2) |
  | arena-handoff.tsv, borrowed-views.tsv | the owner of each allocation (server context, session, cached plan, compilation, execution, batch, budget owner or receiver), whether an arena is `counted`, and the Rust type of each view member (2.2) |
  | refcount.tsv | which `Arc`, handle or owner replaces each count |
  | server-service-slots.tsv | the context holding each service, and for each lookup whether it reaches the context, a field or a parameter; whether a deleted null check's site can run before the module graph (2.8) |
  | subclassed-bases.tsv | the Rust form of each `ObTimerTask`, `ObDLinkBase` and `ObFuncExprOperator` subclass (2.3, 2.8) |
  | pointer-identity.tsv | which objects get ids; `Option` where a slot or array can hold NULL; the kind of each local `ObRawExprFactory` (2.4) |
  | sort-hash-order.tsv | the sort used at each call and whether its comparator can fail; for each hash iteration, whether the key is a value or an address (2.5) |
  | atomic-fields.tsv | atomic or locked, the order of the 29 weaker calls, and each lock's nesting order (2.7) |
  | const-cast.tsv | the `&mut` path that replaces each cast write |
  | overflow.tsv, float-contraction.tsv | wrapping or checked arithmetic, reachable divisions, and the float order kept at each site (2.6) |

  The sections add rows prompt 02 must sweep too: each `sizeof` in a decision formula, each spilling operator's work-area formula, the 17 `MEMMOVE` sites, operator files that reach into frame internals, storage's `sql::` uses, the tracepoints that move batch ends (s5-execution.md 8); the 41 local expression factories and the arrays that hold NULL (s4-sql-front.md 2.3, 2.5); each new warning-buffer site (s2-errors.md 2.6).
- The row gives the answer; the Rust form follows from this document, so the row does not repeat it.

## 6. Per-file obligations

Every translated file obeys these; a reviewer checks each against the file:

1. Its last line is the status trailer, and every split piece has its own:
   ```
   // PORT STATUS: confidence=[high|medium|low] todos=[N]
   ```
   N is the number of `// TODO(port): ` lines in the file. A trailer that undercounts is a finding.
2. `rustfmt --edition 2024 --check` parses it. A parse failure is a finding.
3. No `mod`, `#![..]` or `extern crate` line (4).
4. `use` names only crates on its crate's row and the crates of section 1.
5. No `unsafe` outside the named crates; inside them, each block and impl has its `// SAFETY:` line (3).
6. The only comments are the four markers and the trailer. The frozen core API's public items also carry rustdoc, since Step 2b's translator B gets public API docs only (ARCHITECTURE default 27).
7. No `println!`, `eprintln!` or `dbg!`; no banned method, type or crate without its `#[allow(..., reason = "<row>")]`.
8. `unwrap`, `expect` and panicking indexing appear only where the C++ dereferences or indexes without a check.
9. Every C++ function of the unit has its Rust function under the same name (or its overload number), with only the statements section 0 allows added or removed. Items appear in the C++ order: the header's types and inline functions first, then the .cpp's functions in its order.

The reviewer checklists of the design sections apply to the units they cover: s1 section 6, s2 2.12, s3 3.9, s4 3.7, s5 the end of each part, s6 2.6, 3.4, 4.4, 5.4 and 6.4, s7 7.11, s8 the end of each section.

## 7. Deviation log

Every departure from the documented process gets one line here — skipped
passes, waived checks, untriggered defaults. ID'd `DEV-001` style: ID, date,
what was skipped or waived, who sanctioned it. Written at gates, by the
human or with the human's sign-off. A deviation nobody logged is a deviation
nobody approved — Run 1's skipped review pass is the model entry.

| ID | Date | Deviation | Sanctioned by |
|---|---|---|---|
| DEV-001 | 2026-09-24 | **00b runs before a signed-off "migrate" verdict.** What departs: `prompts/00b-judge-setup.md` ("When" and "Prerequisites") runs 00b only after the feasibility gate signs off "migrate"; step 0's verdict was "migrate later" (migration/feasibility/feasibility.md section 11), and 00b starts under it, with the go-conditions in migration/PLAN.md section 5 playing the verdict's role before Step 1. Why: the judge adds restart, PS-protocol, concurrency and performance checks the C++ project lacks, so it pays off even if the port never happens (feasibility.md section 11). Recorded in: migration/PLAN.md section 6, "Named departures from the kit", item 1. | The developer: PLAN.md section 6 (commit 040f28a1a, 2026-09-24) says to record it now, and the developer started 00b on 2026-09-24 |
| DEV-002 | 2026-09-24 | **Step 0 ran more builds than its rubric allows.** What departs: `prompts/00-feasibility.md` allows "one build (plus the typecheck, if it's a separate command)"; step 0's timing script also ran three incremental rebuilds of `seekdb` (`make -j14 seekdb`: one no-op, two after `touch`ing a source file) and a second Rust build (`cargo build --release -p sql-nio` after `cargo check --workspace`). Why: the incremental rebuild times are the per-unit referee price that the report's Call 2 needed. Recorded in: migration/PLAN.md section 6, "Named departures from the kit", item 2 (every command is listed in feasibility.md section 12, "What was run"). | The developer: PLAN.md section 6 (commit 040f28a1a, 2026-09-24) says to record it now |
| DEV-003 | 2026-09-24 | **The judge runs init SQL one file per obclient session, not one statement per call.** What departs: migration/PLAN.md section 4, item 1, and section 10, action 6, ask the runner to run init.sql and init_user.sql statement by statement. The runner (.github/script/seekdb/mysqltest_for_seekdb.py, `execute_init_sql`) still sends each file to one obclient session, because statements depend on session state set earlier in the file (`use test`, `ob_query_timeout`), and infers each statement's status (succeeded, failed, not_run, unknown) from obclient's `ERROR ... at line N` lines on stderr; entry-gate.json says so in its `attribution` field. Found by the Opus 5.5 reviewer of runner batch B. Checked once against the real obclient with a copy of init.sql that fails at a known line (migration/judge/harness/README.md). | The developer's goal directive of 2026-09-24 ("直到完成迁移"), under which Claude signs off gates on the evidence and logs it here |
| DEV-004 | 2026-09-24 | **00b agent runs inherited the session's reasoning effort.** What departs: migration/PLAN.md section 6 (settings, from report §8) asked every subagent call to set its effort explicitly, high for reviewers. The 00b workflows before this date (runner fixes, mutation design and review, restart script) set the model on every call but not the effort, so implementers and reviewers ran at the session default. From 2026-09-24 every call sets `effort: 'max'` (decisions.md row 4b), starting with the second-set workflow for items 4, 6 and 7. The earlier outputs are kept: each was checked by a second reviewer on a disjoint batch and then by real runs on the reference (harness/README.md, restart-scenarios.md, the 14 mutations re-checked by Fable 5.1). | The developer, 2026-09-24: "effort全设置成max", "这个goal所有的effort都是max" |
| DEV-005 | 2026-09-24 | **Step 1 starts before 00b's first sign-off.** What departs: `prompts/00b-judge-setup.md` ("Nothing in Step 1 starts until I sign off that the judge is real") and migration/PLAN.md section 6 (Step 1 starts at the first sign-off). The design document, the dependency map and the inventory sweep start while the 14 injected-mutation runs finish (about 5 hours). Why: decisions.md row 5a (speed over tokens); none of the three uses the judge's results, and nothing is translated or built before Step 2a, which still waits for both 00b sign-offs. If a mutation escapes, the judge is fixed and validated again before the first sign-off; Step 1's work is unaffected. | Claude, under the developer's instruction of 2026-09-24 ("我不在乎token消耗 我只想尽快完成迁移"), told to the developer the same day |
