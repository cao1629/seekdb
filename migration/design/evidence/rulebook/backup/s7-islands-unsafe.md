# 7. The C++ islands and where `unsafe` may appear

This section turns ARCHITECTURE.md ("ARCH") §8, §9, §11's stack rule for islands and §13's build rules into rules for the people who write the island crates, the named `unsafe` crates and the code that calls them. It settles PLAN §6's bullets "the island ABIs, the kept C++ oblib subset under share/geo and S2, and how island memory is charged on macOS" and "the `unsafe` policy (Decision 14)", and PLAN §8 item 19. Facts are at 834bbee1e. Symbol and header lists were produced with research 08's scripts (linkscan.py, island_first_level.py, depclosure.py) over the reference build at /Users/colin/seekdb-dev/ref-834bbee1e/build_release; their output is in /Users/colin/.claude/jobs/39d4f781/tmp/design/s7/ and should be copied into migration/ with this section. "Must" and "never" are rules; a reviewer rejects a change that breaks one.

## 7.1 What stays C or C++, and which crate owns it

| Crate | Kept code, compiled in place from src/ | Third-party archives (deps/3rd, unchanged) | Header |
|---|---|---|---|
| geo-sys | src/share/geo (180 tracked files, 47,033 hand-written lines), except the MVT encoder (ob_geo_mvt.cpp, ob_geo_mvt_encode_visitor.cpp, ob_vector_tile.pb-c.c) and the JSON-binary writing of ob_wkb_to_json_bin_visitor.cpp | libs2.a, 20 abseil archives, libcrypto.a (S2's 22 `BN_*` symbols) | rust/geo-sys/include/obgeo.h |
| vsag-sys | src/oblib/lib/vector/ob_vsag_adaptor.{h,cpp} and ob_vector_util.{h,cpp}, which holds `ObVsagLogger` (ob_vector_util.h:35-55) | the 14 archives of src/oblib/lib/CMakeLists.txt:111-126, libroaring.a among them | rust/vsag-sys/include/obvsag.h |
| sql-parser-sys | the checked-in generated C of the three grammars, sql_parser_base.c, parse_node.c, pl_parser_base.c, fts_parse.c, fts_base.c, the keyword files | none | bindgen output of the existing headers, checked in |
| ob-clib-sys | zstd 1.3.8 (src/oblib/lib/compress/zstd_1_3_8/zstd_src, 22 .c files) | libicui18n.a, libicuuc.a, libicustubdata.a (ICU 69), libz.a (1.2.13), libxml2.a, liblzma.a | bindgen output, checked in |

New C++ written for the port lives in the crates: the island entry points and bridge classes in rust/geo-sys/cpp/ and rust/vsag-sys/cpp/, and the replacement oblib files of 7.7 in rust/kept-oblib/. Leaving the islands: `ObExprRegexContext` (SQL tier, translated; it includes the expression framework, src/sql/engine/expr/ob_expr_regexp_context.h:32), the 104 GIS expression files, MVT, and GeoJSON's JSON-binary writing (ARCH §9.1, §9.3).

Rules:
1. Kept C and C++ is never edited and never copied. Build scripts compile it from its src/ path; a gate runs `git diff --quiet 834bbee1e -- <kept file list>`. The cutover keeps these files and the frozen headers of 7.7 when it removes the rest of the C++ tree (default, for the developer to confirm).
2. A change to island behavior is new C++ in the crate, never an edit under src/.

## 7.2 Rules every island ABI follows

These restate ARCH §9.3 with the details implementers need. They bind geo-sys and vsag-sys; sql-parser-sys and ob-clib-sys follow 7.5 and 7.6, which keep existing C APIs.

1. **One generated header per island.** cbindgen =0.29.4, run from the crate's build.rs, writes `rust/<crate>/include/<tag>.h` from the Rust `ffi` module: the `unsafe extern "C"` block declares the C++-implemented entries, the `#[unsafe(no_mangle)] extern "C" fn`s are the Rust callbacks. The island C++ is compiled against that same file, so a signature that drifts fails the C++ compile. The file is committed; a gate fails on `git diff --exit-code -- rust/*/include`. cbindgen.toml keeps sql-nio's settings (rust/sql-nio/cbindgen.toml: `language = "C"`, `cpp_compat`, `documentation = false`, `style = "both"`, `no_includes`) with `sys_includes = ["stddef.h", "stdint.h", "stdbool.h"]`, and adds `[fn] postfix = "<TAG>_NOEXCEPT"` (`OBGEO_NOEXCEPT`) with an `after_includes` block defining it as `noexcept` under C++ and as nothing under C, so the C++ definitions can be `noexcept` without contradicting the header (C++ rejects a redeclaration that adds `noexcept`). That cbindgen applies the postfix to foreign-block declarations too is an assumption, checked when the first header is generated.
2. **Names.** C++-implemented entries are `<tag>_<verb>` (`obgeo_area`), Rust callbacks `<tag>_rs_<verb>` (`obgeo_rs_alloc`), structs and opaque handles `<Tag><Name>`, where Name is the C++ type's name after `Ob`, less a leading `Geo` or `Vsag` word the tag already says (`ObgeoSrsItem` for `ObSrsItem`, `ObgeoErrLogInfo` for `ObGeoErrLogInfo`), and a type with no C++ counterpart gets a plain name (`ObgeoAlloc`); fields keep the C++ member names (`value_out_of_range_`), constants `<TAG>_<NAME>`. Registered tags: `obgeo`, `obvsag`. A tag must not collide with the wrapped library's C API (`vsag_` is vsag's: 20 `vsag_*` functions in libvsag_static.a, R08 §2.1).
3. **Types.** Only fixed-width integers, `float`, `double`, `bool`, views `(const T *, int64_t count)` over those, `#[repr(C)]` plain structs of them, and opaque handle pointers cross. Allocation results are `uint8_t *`. Never `size_t`, `long`, `ObString` or any C++ type. `float` and typed views are needed by vsag (7.4) and are not in ARCH §9.3 rule 3; see 7.14.
4. **One entry per C++ call the SQL tier makes today.** An entry takes the same inputs and gives back the same out-values and code as the share/geo, S2 or `obvsag` function the C++ SQL tier calls now. So the Rust SQL tier keeps the C++ order of checks and user errors. An entry never merges two of today's calls when the SQL tier does something between them.
5. **Errors.** Every entry returns an `int32_t` OB code. Island C++, kept and new, takes its codes from the frozen generated header src/share/ob_errno.h that kept code already includes, so no second copy of the codes enters an island; a gate compares every value in that file with ob-errno's. User messages are formatted in Rust from the code and plain out-values (`ObgeoErrLogInfo`). Text the wrapped library produces and the user sees, such as vsag's `Error::message` (ob_vector_index_util.cpp:101-117 puts it in `LOG_USER_ERROR(OB_NOT_SUPPORTED, ...)`), is written through a Rust sink callback. A `LOG_USER_*` inside kept code (one today: ob_vsag_adaptor.cpp:1062, in `add_index` before vsag is entered) reaches Rust through the replacement `ObLogger::log_user_message`, which calls `<tag>_rs_user_message`; that callback writes into the calling thread's warning-buffer slot (ARCH §2), and a call on a thread with no slot is logged and dropped.
6. **Exceptions.** Every C++ entry is `noexcept` and wraps its body in `try`. Where today's call path already has a handler, the entry's `catch (...)` uses it: geo calls `ob_boost_geometry_exception_handle()` (src/share/geo/ob_geo_dispatcher.h:1314-1366, used the same way at :1195-1196), which keeps `std::bad_alloc` as `OB_ERR_STD_BAD_ALLOC_ERROR` and ends in `OB_ERR_GIS_UNKNOWN_EXCEPTION`; vsag's `build_index` keeps `OB_ERR_VSAG_RETURN_ERROR` (ob_vsag_adaptor.cpp:218-235). Where it has none, the `catch (...)` logs and calls `ob_abort()`, because today nothing below the thread's entry catches such an exception (the request-level catch, src/observer/omt/ob_worker_processor.cpp:112-117, takes only `OB_BASE_EXCEPTION`), so `std::terminate` ends the process (default, for the developer to confirm). Rust declares entries `extern "C"`, never `"C-unwind"`; every profile has `panic = "abort"`, so no unwinding crosses in either direction.
7. **Ownership.** Each side frees only what it allocated. Memory an island object keeps after its entry returns comes from `<tag>_rs_alloc(receiver, size)` or lives in an island handle. Each handle has one `<tag>_<thing>_create` and one `<tag>_<thing>_destroy`, called only from the Rust wrapper's `Drop`. An entry that keeps a pointer into an input view after returning, such as a geo build under `GEO_NOT_COPY_WKB`, must be listed in this section, and its Rust wrapper borrows that input for the handle's lifetime; an entry not listed keeps none.
8. **Receivers and threads.** A callback that acts on a run-time object takes it as its first parameter; no callback finds its object through a global. Callbacks that reach only the process-wide state ARCH §7.1 allows (the logging facade) take no receiver. vsag calls back on threads it starts (libvsag_static.a references `pthread_create`, `omp_set_num_threads` and `omp_get_num_threads`: `llvm-nm -u lib/vsag_lib/libvsag_static.a`), so every vsag receiver is `Send + Sync`, and vsag callbacks other than `obvsag_rs_user_message` (rule 5) read no Rust thread-local; none calls `smart_call!`. geo and the parser start no threads; their callbacks run on the caller's thread and may read the thread-locals 7.3 and 7.5 name.
9. **No re-entry.** A callback returns before the island continues and never calls into the same island. No `longjmp` passes over a Rust frame (7.5).
10. **Build.** 7.9.
11. **Library versions stay inside the island.** The ABI does not change when a platform links another vsag (Android lists vsag 0.18.0, R08 §2.1).

Reviewer checks: the header is regenerated and committed; each entry mirrors exactly one of today's calls; codes, catch lists and out-values match the C++ call it replaces; every receiver-less callback touches only allowed process-wide state.

## 7.3 The geo island (geo-sys)

**Entry groups.** About 76 entries (ARCH §9.3): one per distinct function the SQL tier and DAS call today (81 distinct `Class::member` references, 21 of them kernel types, R08 §2.1), minus the WKB header reads Rust does itself.

| Group | Entries mirror | Notes |
|---|---|---|
| Build and parse | `ObGeoTypeUtil::build_geometry`, `construct_geometry`, WKT and SRS-WKT parsing, casts in ob_geometry_cast.cpp | out-values include `ObgeoErrLogInfo`, which mirrors `ObGeoErrLogInfo` (ob_geo_common.h:186-197), each union as its first member |
| Kernels | `ObGeoFunc<ObGeoFuncType::X>::geo_func::eval` for the 22 types (ob_geo_func_register.h:53-79) | take the scratch-context handle below |
| Output | WKB and WKT writers, GeoJSON traversal, the three MVT geometry steps | GeoJSON sends events through `obgeo_rs_json_event`; Rust builds the JSON binary; MVT encoding is Rust |
| SRS | `ObSrsItem` creation from its definition, destruction, the accessors the SQL tier reads | handle `ObgeoSrsItem` |
| Cached geometry | `ObCachedGeom` create, `intersects`/`contains`/`cover`/`within`, destroy | handle `ObgeoCachedGeom` |
| S2 | `ObS2Adapter` construction, `init`, `get_cellids`, `get_cellids_and_unrepeated_ancestors`, `get_inner_cover_cellids`, `get_mbr`; `ObSpatialMBR::filter` | cell ids are written into receiver memory; the coverer options stay inside (ob_s2adapter.h:124-153) |
| Box, tracepoint | `ObGeoBoxUtil` members; `obgeo_tracepoint_set` | the only island tracepoint is `EN_CHECK_SORT_CMP` (2501, ob_tracepoint_def.h:386), read by `lib::ob_sort` (ob_sort.h:30) |

**Memory handles.** `ObgeoAlloc` is a Rust receiver behind the replacement's `ObIAllocator` adapter (`alloc` calls `obgeo_rs_alloc`, `free` does nothing); it serves every share/geo function that takes an `ObIAllocator` (build results, cached geometries, SRS items, written bytes). `ObgeoBoostAllocGuard` is an island handle over `ObGeoBoostAllocGuard` (ob_geo_utils.h:422-440): created by one entry where the C++ constructs the guard, initialized by another where the C++ calls `guard.init()`, destroyed where the C++ guard goes out of scope. Kernels take it, as `ObGeoEvalCtx` takes the guard's `MemoryContext` (ob_geo_func_common.h:66-68), and geometries a kernel returns live in it until it is destroyed.

**Handle types in Rust.** `SrsItem` is `Send + Sync`: `ObSrsItem` has only `const` members (ob_srs_info.h:896-930), and ob_srs_info.h and ob_srs_info.cpp declare no `mutable` field (`grep -n mutable src/share/geo/ob_srs_info.*` prints nothing). `CachedGeom<'a>` is `Send` but not `Sync`: `ObCachedGeomBase` initializes itself lazily (`is_inited_`, ob_geo_cache.h:57-86); one expression context uses it. `Geometry<'a>` borrows the arena behind its `ObgeoAlloc` and, under `GEO_NOT_COPY_WKB`, the WKB bytes. `BoostAllocGuard` is neither: one evaluation on one thread uses it.

**Callbacks (13).** `obgeo_rs_alloc`; `obgeo_rs_log` and `obgeo_rs_user_message`; `obgeo_rs_get_stackattr` and `obgeo_rs_set_stackattr`; `obgeo_rs_strntod` and `obgeo_rs_strntoll` (for `ObCharset::strntod`/`strntoll`); `obgeo_rs_gcvt`, `obgeo_rs_gcvt_strict`, `obgeo_rs_fcvt` (MySQL's dtoa port, used by the WKT and GeoJSON-text writers, ob_geo_to_wkt_visitor.cpp:49, :132); `obgeo_rs_number_from` and `obgeo_rs_number_format` (the only `ObNumber` use left: WKT output rounds through `ObNumber::from` and `format`, ob_geo_to_wkt_visitor.cpp:74-80; the replacement fills and reads the frozen header's `ObNumber` fields); `obgeo_rs_json_event`. Charset, dtoa and `ObNumber` thus have one implementation, the Rust one in ob-values.

**The stack.** Kept geo code keeps its five `SMART_CALL`s and its own stack switch (`jump_call`, ob_smart_call.cpp; `__SMART_CALL_IMPL`, ob_smart_call.h:112-126). The replacement `get_stackattr` and `set_stackattr` read and write ob-base's per-thread stack bounds through the two callbacks (ARCH §11), so a geo call made on a stack stacker grew sees that stack's bounds instead of the thread's (today's macOS code reads `pthread_get_stackaddr_np`, ob_common_utility.cpp:111-119). Rust callbacks run while the island may be on its own extension stack, so they never call `smart_call!` and never recurse. Step 2a makes one geo call on a stack Rust grew (ARCH §15).

**Worked example: ST_Area.** Before, src/sql/engine/expr/ob_expr_st_area.cpp:79-104:

```cpp
ObGeoBoostAllocGuard guard{};
...
} else if (OB_FAIL(ObGeoExprUtils::build_geometry(temp_allocator, wkb, geo, srs, N_ST_AREA, GEO_ALLOW_3D_DEFAULT | GEO_NOT_COPY_WKB))) {
} else if (geo->type() != ObGeoType::POLYGON && geo->type() != ObGeoType::MULTIPOLYGON) {
  ret = OB_ERR_UNEXPECTED_GEOMETRY_TYPE;
  LOG_USER_ERROR(OB_ERR_UNEXPECTED_GEOMETRY_TYPE, "POLYGON/MULTIPOLYGON", ...);
} else if (OB_FAIL(guard.init())) {
} else {
  ObGeoEvalCtx gis_context(*mem_ctx, srs);
  if (OB_FAIL(gis_context.append_geo_arg(geo))) {
  } else if (OB_FAIL(ObGeoFunc<ObGeoFuncType::Area>::geo_func::eval(gis_context, result))) {
    ObGeoExprUtils::geo_func_error_handle(ret, N_ST_AREA);
  } else if (!std::isfinite(result)) { ... } else { res.set_double(result); }
```

After, the island entry (rust/geo-sys/cpp/obgeo_kernels.cpp):

```cpp
int32_t obgeo_area(ObgeoBoostAllocGuard *guard, const ObgeoSrsItem *srs,
                   const ObgeoGeometry *geo, double *result) noexcept
{
  int ret = OB_SUCCESS;
  try {
    lib::MemoryContext *mem_ctx = reinterpret_cast<ObGeoBoostAllocGuard *>(guard)->get_memory_ctx();
    if (OB_ISNULL(mem_ctx)) {
      ret = OB_ERR_NULL_VALUE;
    } else {
      ObGeoEvalCtx gis_context(*mem_ctx, reinterpret_cast<const ObSrsItem *>(srs));
      if (OB_FAIL(gis_context.append_geo_arg(reinterpret_cast<const ObGeometry *>(geo)))) {
      } else {
        ret = ObGeoFunc<ObGeoFuncType::Area>::geo_func::eval(gis_context, *result);
      }
    }
  } catch (...) {
    ret = ob_boost_geometry_exception_handle();
  }
  return ret;
}
```

The Rust side (rust/geo-sys/src/ffi.rs and the safe wrapper beside it):

```rust
unsafe extern "C" {
    pub(crate) fn obgeo_area(guard: *mut ObgeoBoostAllocGuard, srs: *const ObgeoSrsItem,
                             geo: *const ObgeoGeometry, result: *mut f64) -> i32;
}

#[unsafe(no_mangle)]
pub extern "C" fn obgeo_rs_alloc(alloc: *mut ObgeoAlloc, size: i64) -> *mut u8 {
    // SAFETY: 7.2 rule 7: `alloc` is the receiver geo-sys passed into the running entry; it outlives the call.
    let alloc = unsafe { &*alloc };
    alloc.alloc(size)
}

pub fn area(guard: &mut BoostAllocGuard, srs: Option<&SrsItem>, geo: &Geometry<'_>,
            result: &mut f64) -> ObResult {
    // SAFETY: 7.2 rules 3 and 7: every pointer is valid for the call and the entry keeps none of them.
    let ret = unsafe { ffi::obgeo_area(guard.as_ptr(), SrsItem::opt_ptr(srs), geo.as_ptr(), result) };
    if ret == 0 { Ok(()) } else { Err(ObError::new(ret)) }
}
```

and the translated expression keeps the C++ order, a local `ret` and the out-parameter (ARCH §2):

```rust
} else {
    let mut result = 0.0f64;
    ret = geo_sys::area(&mut guard, srs, &geo, &mut result);
    if let Err(e) = ret {
        ObGeoExprUtils::geo_func_error_handle(e, N_ST_AREA);
    } else if !result.is_finite() {
        ret = Err(log_user_error!(OB_OPERATE_OVERFLOW, "Result", N_ST_AREA));
    } else {
        res.set_double(result);
    }
}
```

`ObError::new` stands for ob-errno's constructor from a non-zero code (ARCH §2). The build step (`geo_sys::build_geometry(&arena, &wkb, srs, flags, &mut log_info)`) and the type check (`geo_sys::geometry_type(&geo)`) are separate entries, because the C++ reports a bad WKB before a wrong type (rule 4).

**Per-row calls.** `ObDASSpatialScanIter::filter_by_mbr` calls `ObSpatialMBR::filter` for each index row (src/sql/das/iter/ob_das_spatial_scan_iter.cpp:122-143). `ObgeoSpatialMBR` is a `#[repr(C)]` value (four doubles, the op type as `int32_t`, two bools, mirroring ob_s2adapter.h:112-118), so the per-row call passes two `ObgeoSpatialMBR` values, the op type and a `bool *`, with no handle and no allocation. `ObWkbToS2Visitor::reset` destroys a member and reuses it (ob_geo_to_s2_visitor.cpp:612, reuse at ob_s2adapter.cpp:349); it stays as it is, inside the island.

## 7.4 The vsag island (vsag-sys)

**Entries.** One per `obvsag` function (27 names, ob_vsag_adaptor.h:81-160): index create (dense, sparse) and destroy (`delete_index`), `validate_create_index` (message sink), the two param-string builders (caller buffer view), build and add (dense, sparse), `immutable_optimize`, the counters and bounds, `estimate_memory`, `cal_distance_by_id` (dense, sparse), `get_extra_info_by_ids`, `knn_search` (dense, iterative, sparse) with an iterator-context handle and its destroy (`delete_iter_ctx`), `fserialize`/`fdeserialize`, and the process calls (logger install, log level, block size limit, version). For Rust callers the safe wrapper replaces the `obvectorutil` forwarders of ob_vector_util.cpp; the file itself is compiled unchanged because `ObVsagLogger` subclasses `vsag::Logger`.

**Handles.** `VsagIndex` is `Send + Sync`: the C++ adaptor runs `knn_search` on one index from several threads under a read lock and writes under a write lock (src/observer/vector_index/ob_plugin_vector_index_adaptor.cpp:3402-3404, :3508-3513, :761), so search takes `&self` and build and add take `&mut self`. The iterator context is `Send` only.

**Bridges (new C++ in rust/vsag-sys/cpp/).** One `vsag::Allocator` subclass whose `Allocate`/`Deallocate`/`Reallocate` call `obvsag_rs_alloc`/`_free`/`_realloc` with its receiver and whose `Name()` keeps `"ObVsagAlloc"` and `"ObVsagSearchAlloc"`; one `obvsag::FilterInterface` subclass calling `obvsag_rs_filter_test_id(recv, id)` and `obvsag_rs_filter_test_extra(recv, data, extra_info_size)`; two `std::streambuf` subclasses calling `obvsag_rs_stream_write(recv, data, len)` and `obvsag_rs_stream_read(recv, &data, &len)`, whose data stays valid until the next read (as `ObIStreamBuf`'s callback today, ob_vector_index_serialize.h:83-117); `ObVsagLogger` calling `obvsag_rs_log`. Search results (`dist`, `ids`, `extra_infos`) are allocated through the search receiver today too (`Owner(false)`, ob_vsag_adaptor.cpp:408-410), so the Rust result type borrows the search arena.

**Worked example: index memory.** Before, `ObVsagMemContext::Allocate` (src/storage/allocator/ob_vector_allocator.cpp:54-70) adds a 16-byte header and calls `ObVectorMemContext::alloc` (:131-156), which checks the vector limit every `CHECK_USAGE_INTERVAL` (20) allocations or when the size reaches `CHECK_RESOURCE_UNIT_SIZE` (2 MiB) (ob_vector_allocator.h:36-37), logs `OB_ERR_VSAG_MEM_LIMIT_EXCEEDED` and returns null when `limit <= 0 || hold >= limit || size > limit - hold`; on success it adds the size with its header to `all_vsag_use_mem_` (:62). After, the bridge and the callback:

```cpp
void *Allocate(uint64_t size) override { return obvsag_rs_alloc(alloc_, size); }
```

```rust
#[unsafe(no_mangle)]
pub extern "C" fn obvsag_rs_alloc(alloc: *const ObvsagAlloc, size: u64) -> *mut u8 {
    // SAFETY: 7.2 rules 7-8: the receiver outlives the index or search that holds the bridge, and is Sync.
    let alloc = unsafe { &*alloc };
    alloc.allocate(size)
}
```

`ObvsagAlloc::allocate` keeps the formula line for line: `actual_size = 16 + size`; the check runs when `check_cnt >= 20 || actual_size >= 2 MiB`, reads `vector_memory_limit` and the budget's hold, stores 20 into `check_cnt`, logs and returns null on refusal, else stores 0; after a successful allocation from the global allocator it increments `check_cnt` and adds `actual_size` to the counter. vsag turns null into `NO_ENOUGH_MEMORY` (R08 §4.4), which the adaptor maps to -4013 (ob_vsag_adaptor.cpp:61-62); -7603 never leaves the callback (ARCH §3.2). This formula, not ARCH §3.2's add-compare-undo, is the vector module's, as ARCH §3.2 requires each owner to keep its C++ formula.

**CRoaring.** vsag-sys also binds CRoaring's C API (bindgen output checked in) for seekdb's own users: 108 call lines in ob_plugin_vector_index_adaptor.cpp, 16 in ob_vector_index_adaptor.h, 10 in ob_das_ivf_scan_iter.{h,cpp} (`git grep -c 'roaring64_\|roaring_bitmap_'`). vsag itself calls CRoaring (`llvm-nm -u libvsag_static.a` lists `roaring_bitmap_*`). observer installs the memory hook once at start, as `ObRbMemMgr::init_memory_hook` does (src/share/roaringbitmap/ob_rb_memory_mgr.cpp:153-165); `roaring_memory_t` carries six function pointers and no user data, so the hook reaches the vector budget through process-wide state set at install (ARCH §7.1 allows the hook). Rules: the hook allocates from the global allocator with a size header; it never refuses (a general out-of-memory aborts, Decision 12), so `CROARING_TRY_CATCH`'s -4013 (ob_rb_memory_mgr.h:30-38), which catches the `std::bad_alloc` the C++ hook throws through CRoaring's C frames (ob_rb_memory_mgr.cpp:57), has no Rust counterpart; every allocation counts toward the vector budget. The C++ counts only allocations made under a memory label starting with "VIB" (ob_rb_memory_mgr.cpp:40-48, :79-87; the labels are set around the adaptor's bitmap work, e.g. ob_plugin_vector_index_adaptor.cpp:186), and sends the rest to `ObRbMemMgr`'s own allocator; counting all is a default for the developer to confirm, since Rust has no thread-local label (ARCH §11).

## 7.5 sql-parser-sys: the parser's C core

ARCH §9.2 decides the mechanism; the ABI rules differ from 7.2 because the C already names and calls its callbacks.
1. Names and signatures stay as the existing headers declare them, with the receiver where the C puts it: last in `parse_malloc(nbyte, malloc_pool)` (parse_malloc.h:30), first in `lookup_pl_symbol(pl_ns, ...)` (parse_node.h:463). bindgen output of parse_node.h, sql_parser_base.h, parse_malloc.h and the `ObItemType` header is checked in and regenerated by a gate script, never by build.rs.
2. The Rust callbacks: the 15 `parse_malloc.h` functions (statement arena behind `malloc_pool`), `check_mem_status` and `try_check_mem_status`, `check_stack_overflow_c` and `obpl_parser_check_stack_overflow` (parse_node.h:401, :461), `lookup_pl_symbol` (a trait object behind `pl_ns`), `murmurhash` (parse_node_hash.h), the charset helpers of ob_parser_charset_utils.cpp, and the two parser_utility.h functions. `ob_parse_binary_simd` is needed only on x86_64 (parse_node.c:551-553 calls it under `#if defined(__GNUC__) && defined(__x86_64__)`).
3. `try_check_mem_status(int64_t)` has no receiver (parse_node.c:178, called by `new_node` at :186); the C++ finds the query's tracker through `thread_local ObMemTracker ObMemTrackerGuard::mem_tracker_` (src/sql/executor/ob_memory_tracker.cpp:25). sql-parser-sys sets a per-thread slot pointing at the current parse's call context for exactly the duration of `parse_sql`, cleared by a scope guard; only the receiver-less callbacks read it. The stack checks read ob-base's stack bounds.
4. Every callback returns normally and never calls the parser. The only `longjmp`s, reached from the lexers' `YY_FATAL_ERROR` (sql_parser_mysql_mode.l:31, :1852; pl_parser_mysql_mode.l:30, :672), run in C code, never inside a callback, and jump to `setjmp`s in C (sql_parser_base.c:104-106, pl_parser_base.c:48), so no Rust frame lies between.
5. `parse_realloc(NULL, n, pool)` allocates without the 8-byte header (parse_malloc.cpp:128), so a later `parse_realloc` of that pointer reads 8 bytes before it; the Rust callback does the same, marked `BUG(port)`.

**Worked example.** Before, parse_malloc.cpp:90-105 allocates `8 + nbyte` from the `ObIAllocator` behind `malloc_pool`, stores `nbyte` in the first 8 bytes, zeroes the rest and returns the address after the header, or null for a null pool or `nbyte == 0`. After:

```rust
#[unsafe(no_mangle)]
pub extern "C" fn parse_malloc(nbyte: usize, malloc_pool: *mut c_void) -> *mut c_void {
    if malloc_pool.is_null() || nbyte == 0 {
        return ptr::null_mut();
    }
    // SAFETY: 7.5 rule 2: malloc_pool is the ParsePool sql-parser-sys stored in ParseResult; it lives until parse_sql returns.
    let pool = unsafe { &*malloc_pool.cast::<ParsePool>() };
    pool.alloc_zeroed_with_len_header(nbyte).cast()
}
```

`ParsePool::alloc_zeroed_with_len_header` is safe code: it takes `(nbyte + 15) / 8` zeroed `u64`s from the statement arena, which gives 8-byte alignment, writes `nbyte` into the first and returns the address of the second.

## 7.6 ob-clib-sys: ICU, zlib, zstd 1.3.8, libxml2

1. bindgen output for each library is checked in (ICU's versioned names included: libicui18n.a defines `_uregex_open_69`, R08 §2.1). Each library gets a safe module: handle types with `Drop`, slices in, `Result` or the library's status out, the library's own status values kept for the caller's C++-shaped error mapping.
2. Library callbacks that the C API takes as function pointers (libxml2's SAX handler table, zlib's `zalloc`/`zfree`, zstd's `ZSTD_customMem`) are `extern "C" fn`s in the crate; they recover their Rust object from the library's user-data slot (`xmlParserCtxt::_private`, `opaque`) and forward to a safe trait. Code that reads a library struct's fields (the SAX handler reads `ctxt->input->cur`, `base` and `consumed`, ob_libxml2_sax_handler.cpp:598) lives in the crate behind a safe method.
3. zlib and zstd take, as their user-data allocator, the Rust counterpart of the allocator the C++ passes today (`zstr_.opaque = &allocator_`, ob_select_into_basic.cpp:255-257; `OB_ZSTD_customMem`, ob_zstd_compressor_1_3_8.cpp:38), so their bytes are charged where the C++ charges them.
4. zstd 1.3.8 is compiled from src/ with the reference flags and linked partially (`ld -r`, the reference's "partial linking", src/oblib/lib/compress/zstd_1_3_8/CMakeLists.txt:34) with an export list holding only the `ZSTD_*` functions the crate declares, so its internals (`FSE_*`, `HUF_*`, `POOL_*`, the bundled xxhash) are local and cannot clash with another copy. The reference links it partially the same way and goes further, hiding every `ZSTD_*` symbol behind its C++ wrapper (src/oblib/lib/compress/zstd_1_3_8/CMakeLists.txt:6, :33-38; the reference's zstd_1_3_8_objs.o defines 15 global symbols, all `ObZstdWrapper`, `llvm-nm -gU`).

**Worked example.** Before, ob_expr_regexp_context.cpp:251-254 calls `uregex_open`, `uregex_setStackLimit` and `uregex_setTimeLimit` with one shared `u_error_code`, then checks it once and closes a non-null engine on failure. After:

```rust
pub fn open_with_limits(pattern: &[u16], flags: u32, stack_limit: i32, time_limit: i32,
                        parse_error: &mut UParseError, status: &mut UErrorCode) -> Option<URegex> {
    // SAFETY: pattern is valid for the call; parse_error and status are valid for writes.
    let re = unsafe { sys::uregex_open_69(pattern.as_ptr(), pattern.len() as i32, flags, parse_error, status) };
    // SAFETY: ICU returns at once when *status already holds a failure, so a null `re` is never used.
    unsafe { sys::uregex_setStackLimit_69(re, stack_limit, status) };
    // SAFETY: as the line above.
    unsafe { sys::uregex_setTimeLimit_69(re, time_limit, status) };
    NonNull::new(re).map(URegex)
}
```

`URegex` closes in `Drop` and is `Send` without `Sync`: ICU lets one thread at a time use a `URegularExpression`.

## 7.7 The kept oblib subset

**Headers.** The islands compile against the unchanged 834bbee1e headers their objects reached in the reference build: geo 196 files and 77,558 lines, vsag 118 files and 38,448 lines, every vsag header also in geo's list (depclosure.py over the `*.o.d` depfiles of the 29 geo objects and unity_oblib_lib_oblib_lib_ob_vector_util_0; lists in geo-kept-headers.txt and vsag-kept-headers.txt). They stay at their src/ paths (7.1 rule 1).

**Out-of-line symbols.** The island objects need 124 seekdb symbols (geo) and 38 (vsag); every vsag symbol is also a geo symbol (island_first_level.py; geo-needed-seekdb-symbols.txt, vsag-needed-seekdb-symbols.txt; the three `std::bad_function_call` symbols both lists also show come from libc++). Linking the real files would pull 363 of the 634 reference objects (R08 §2.3), so a few replacement files in rust/kept-oblib/ define them:

| Group | geo / vsag | Examples | Replacement |
|---|---|---|---|
| Logging | 30 / 30 | `ObLogger::alloc_log_item`, `log_head`, `log_tail`, `log_user_message`, `ObRingBufLogWriter::commit`, `logdata_printf`, 7 thread-locals | the finished line goes to `<tag>_rs_log`, user messages to `<tag>_rs_user_message`; `logdata_printf`/`vprintf` copied |
| Memory | 20 / 0 | `g_ob_malloc_backend`, `MemoryContext::root()`, `ObMallocHookAttrGuard`, `ObFIFOAllocator` ctor/dtor, `ObjectSet` ctor, `ObMallocAllocator::get_instance`, `je_malloc`/`je_free`/`je_realloc` | the backend is fixed to jemalloc, so header-inline `ob_malloc` calls `je_*` in the Rust binary's jemalloc, as on today's macOS; `MemoryContext::root()` is an island root; obmalloc-only code paths call `ob_abort()` if reached; small ones copied |
| JSON binary, casts, LOB text, hex | 20 / 0 | `ObJsonBin::*`, `ObObjCaster::to_type`, `ObTextStringIter`, `ObHexUtils::hex` | none: they leave with MVT and the JSON-binary writer (ob_geo_mvt.cpp:19-24; ob_wkb_to_json_bin_visitor.h:21) |
| `ObStringBuffer` | 11 / 0 | `append`, `reserve`, `extend` | copied |
| Small utilities | 11 / 3 | `databuff_printf`, `ObFastFormatInt`, `hash::PRIME_LIST`, `lbt`, `ob_abort`, `alloc_itid`, `get_tp_switch`, `EN_CHECK_SORT_CMP` | copied, except `lbt` (empty string), `ob_abort` (`abort()`) and the tracepoint item (set through `obgeo_tracepoint_set`) |
| Stack | 9 / 0 | `check_stack_overflow`, `get_stackattr`, `set_stackattr`, `jump_call`, `all_stack_size`, `g_stack_allocer` | bounds through the two callbacks (7.3); `jump_call` copied (ob_smart_call.cpp, aarch64 branch); `smart_call_alloc` allocates as today, without a guard page |
| Latches | 8 / 0 | `ObLatch::rdlock`/`wrlock`/`unlock`, `ObLatchMutex` | reimplemented on the header's lock word (spin, then yield); only island-local objects hold them |
| Time | 7 / 5 | `ObTimeUtility::current_time`, `ObTscTimestamp`, `ObClockGenerator::clock_generator_`, `ObBasicTimeGuard` | `clock_gettime`; the guards do nothing |
| Charset, dtoa | 6 / 0 | `ObCharset::strntod`, `strntoll`, `get_system_collation`, `ob_gcvt`, `ob_gcvt_strict`, `ob_fcvt` | forwarded to Rust (7.3); `get_system_collation` leaves with MVT |
| `ObNumber` | 2 / 0 | `from_v3_`, `format_v2` | forwarded to Rust's `ObNumber` |

The replacement files keep the layouts the frozen headers define, since header-inline code reads their fields; the 39 geometry and 21 vector_index configured cases check them.

**Two copies, one per island.** Both islands need the logging, time and utility groups, and two strong definitions of one symbol would clash at the final link, or, with archives, bind silently by link order. So each island crate compiles the replacement files it needs, with its tag passed as a macro that names its callbacks, and links everything partially into one object: `ld -r -arch arm64 -exported_symbols_list <tag>.exports`, where the export list, generated from the header, holds only the C++-implemented entries (Mach-O names with the leading underscore). `ld -r` turns every other global into a local, as in the reference's partial link of zstd (7.6 rule 4). This keeps ARCH §1.1's crate graph: vsag-sys neither depends on geo-sys nor borrows its definitions.

Gates: `llvm-nm -gU` of each partially linked object equals its export list; `llvm-nm -u` lists only `<tag>_rs_*`, `je_*`, libc++, libSystem and the island's deps/3rd archives, so a symbol that should have left (`ObJsonBin`) or a missing replacement fails the gate.

## 7.8 How island memory is charged on macOS

Today (R08 §2.4, checked): the malloc hook is built only on Linux (src/oblib/lib/CMakeLists.txt:34-41); `inner_main` promotes jemalloc's zone so all malloc reaches jemalloc (src/observer/main.cpp:645-653, src/oblib/lib/allocator/ob_malloc.cpp:96-116, the zone kept by `-Wl,-u,_je_zone_register`, src/observer/CMakeLists.txt:112-116); under the jemalloc backend `ob_malloc` ignores labels (ob_malloc.h:118-125), but a `MemoryContext` still counts its allocators' totals (src/oblib/lib/rc/context.h:366-390), which the query tracker reads for -11049 (src/sql/executor/ob_memory_tracker.cpp:36-55).

Rules:
1. Memory an island keeps after an entry returns comes from a receiver that is the thing charged: the caller's per-execution arena for built geometries and written bytes (so -11049 counts it), the expression context's arena for cached geometries, the SRS snapshot's arena for SRS items, the vector budget for vsag index memory (7.4), the per-search arena for vsag results, the statement arena for the parser.
2. A geo kernel's scratch memory lives in the `ObgeoBoostAllocGuard` context, a child of the island's root, freed when Rust destroys the handle. It is not charged. The C++ charges it to the query context while the guard lives, but no -11049 check point can run then: the check points are ob_exec_context.cpp:679, ob_select_resolver.cpp:1006, ob_transform_rule.cpp:287, ob_raw_expr.h:5077 and parse_node.c:186, and a geo kernel calls none of them (default, for the developer to confirm).
3. Library-internal `new` and `malloc` (boost, S2, abseil, ICU, libxml2, vsag outside its allocator, OpenMP, OpenBLAS) use the process malloc, which ob-platform's zone promotion makes the same jemalloc as Rust's global allocator. It is uncharged, as today. There is no malloc interposition and no hook (Decision 8's guideline).
4. C++ never passes receiver memory to `free` or `delete`: the geo adapter's `free` does nothing, and vsag's `Deallocate` hands the pointer back through `obvsag_rs_free`. Rust never passes an island pointer to `free`; handles end through their destroy entries.
5. The build gives the Rust binary the reference's jemalloc: seekdb-jemalloc-sys =0.2.2 with the `stats` feature (deps/external/Cargo.toml:10) and `JEMALLOC_SYS_CONFIGURE_ARGS=--with-jemalloc-prefix=je_` (deps/external/cmake/Jemalloc.cmake:40), set in .cargo/config.toml, so the islands' `je_*` references resolve to it.

## 7.9 Building and linking the islands

1. .cargo/config.toml sets deps/3rd's clang 17.0.6, `SDKROOT` for SDK 26.2 and the common flags (ARCH §13). Each island build.rs adds the rest of the reference command of the unity chunk each source was built in (defines, include paths, `-std=gnu++20`, `-fmax-type-align=8`, `-mmacosx-version-min=27.0`), read from a checked-in extract of compile_commands.json; a gate diffs the extract against the reference file.
2. Kept sources are compiled in the reference's unity chunks, in the same order, less the files that leave the island (the three geo C++ chunks, the 25 separate `ob_geo_func_*` objects, and unity_oblib_lib_oblib_lib_ob_vector_util_0, which includes ob_vector_util.cpp and ob_vsag_adaptor.cpp), so inlining and file-local helpers match the reference. The C chunk of geo held the MVT protobuf-c code and leaves with MVT.
3. The island links the archives the reference links (7.1), libc++ from the SDK, and nothing whole-archive: no constructor attributes or registration macros exist in share/geo or src/oblib/lib/vector (R08 §4.5, by grep; the first link confirms it). The seekdb binary's build.rs passes `-Wl,-u,_je_zone_register`.
4. Island crates test against the real island, never against stubs.

## 7.10 Where `unsafe` may appear (Decision 14)

**Enforcement.** The workspace lint table sets `unsafe_code = "forbid"` and `unsafe_op_in_unsafe_fn = "deny"`, and every crate not named below inherits it with `[lints] workspace = true` and carries `#![forbid(unsafe_code)]` in its generated lib.rs (ARCH §8). A named crate writes its own table instead: the workspace table without `unsafe_code`, plus:

```toml
[lints.clippy]
undocumented_unsafe_blocks = "deny"
multiple_unsafe_ops_per_block = "deny"
```

A gate checks that each named crate's table equals the workspace table apart from these lines.

**The named crates and what each may contain.** The list is closed; a new kind of use needs a rulebook row first.

| Crate | Decision 14 kind | May contain |
|---|---|---|
| geo-sys | island shim | in `ffi`: the entry declarations and calls, the `obgeo_rs_*` callbacks, pointer-to-reference conversions; `unsafe impl Send + Sync` for `SrsItem`, `Send` for `CachedGeom` |
| vsag-sys | island shim | the same for `obvsag`; CRoaring's bindings, its six hook functions and `roaring_init_memory_hook`; `Send + Sync` for `VsagIndex` (7.4), the allocation receivers (7.2 rule 8) and `Roaring64`, whose reads the C++ shares across threads under read locks (ob_plugin_vector_index_adaptor.cpp:3003-3004, :3186-3188) |
| sql-parser-sys | island shim | the bindings; `parse_sql` and the other entries; the callbacks under their C names; the C-tree walk that builds `ParseTree` (ARCH §4.2); the per-thread parse slot (7.5 rule 3) |
| ob-clib-sys | island shim (ICU), plus the zlib, zstd and libxml2 bindings (ARCH default 3) | bindings, calls, the library callbacks of 7.6 rule 2; `Send` for handle types |
| ob-simd | SIMD kernels | `core::arch` loads and stores; later, x86 `#[target_feature]` functions called after run-time detection inside the crate |
| ob-platform | IO buffers, plus ARCH default 3 | aligned IO buffers over `std::alloc`; `unsafe impl GlobalAlloc` over `je_*`; `je_malloc_conf` (a `#[unsafe(no_mangle)]` static, whose pointer wrapper needs `unsafe impl Sync`, with ob_malloc.cpp:38-42's string); and only these `libc` calls: `_exit`, `fork`, `lockf`, `pthread_set_qos_class_self_np`, `setpriority(PRIO_DARWIN_THREAD)`, the zone promotion calls, `statvfs`, `fcntl(F_PREALLOCATE)`, `sysconf(_SC_PAGESIZE)` and `sysconf(_SC_PHYS_PAGES)`, `pthread_threadid_np` (7.14 item 3) |
| ob-epoch | reclamation wrapper | crossbeam-epoch 0.9.21's `unsafe` methods, inside safe types |

**Rules for named crates.**
1. The public API is safe: no `pub unsafe fn`, no raw pointer in a public signature, no `unsafe trait` for other crates to implement. Types that hold C pointers are private-field structs with `Drop`.
2. `unsafe` sits in the `ffi` module of an island or library crate, in the kernel modules of ob-simd, in the libc and allocator modules of ob-platform, and in ob-epoch's type modules. `#[unsafe(no_mangle)]` appears only in a named crate's `ffi` module (ob-platform keeps `je_malloc_conf` in its own), only on names carrying the crate's tag or on the existing C names the kept code or jemalloc reads (the parser callbacks, `je_malloc_conf`).
3. One operation per `unsafe` block, with `// SAFETY: ` on the line above, naming the rule of this section it relies on; `unsafe impl` gets the same. The two clippy lints above enforce both.
4. No `static mut`. `unsafe impl Send`/`Sync` only for the types this section names, with its evidence in the `SAFETY` line.
5. A view from C becomes a slice only through one helper that returns `&[]` for length 0, since `slice::from_raw_parts` needs a non-null pointer even then; callers guarantee valid pointers and lengths (sql-nio's rule since 076eb309b).
6. ob-epoch exposes types, never `Guard`, `Shared` or `Atomic`; each type has a loom test and a rulebook row naming the gate measurement that needed lock-free reads (ARCH §11).
7. ob-simd kernels use the C++'s intrinsics in the C++'s order with the same lane partial sums; they never use `mul_add` or an FMA intrinsic the C++ does not call (ARCH §10); each has a scalar version that reproduces the NEON lanes bit for bit, used where NEON is absent and tested against the NEON version.

**Everything else.** `libc` =0.2.189 (rust/Cargo.lock:328-329) and crossbeam-epoch may appear only in named crates' Cargo.toml files, and nix only in the binary crate (R12 §4); a gate greps the manifests. Because rustc's `unsafe_code` lint covering `#[unsafe(no_mangle)]` and `unsafe extern` blocks is an assumption to confirm in Step 2a (R12 §7.1), the same gate also greps for `no_mangle`, `export_name`, `link_section`, `extern "C"` and `static mut` outside named crates. Decision 14 governs workspace crates; third-party crates enter only through ARCH §13's dependency rows.

**sql-nio.** Once its C ABI goes, sql-nio carries `#![forbid(unsafe_code)]`. Its non-ABI `unsafe` is: `libc::shutdown` (reactor.rs:777), `libc::read` (tls.rs:225), and `libc::read` into a `Vec`'s spare capacity followed by `set_len` (pump.rs:496-502), which become `shutdown(Shutdown::Both)` and `Read::read` into an initialized buffer, with a `PERF(port)` marker if zeroing shows in Step 2a; `frame_payload_in_place` (response.rs:918-960), used only by the dropped C-ABI files, becomes a safe function over `&mut [u8]`; `CppSessionStorage` (session_storage.rs:27-44) and `Handler`'s `Send`/`Sync` (ffi_types.rs:50-51) go with the C++ session; the Windows named-pipe code (transport.rs:176-191) moves to ob-platform.

**The report.** At every gate (Step 2a, the core build's exit, each Step 3 batch gate, Step 4, Step 6) a script prints one row per workspace crate: `unsafe` blocks, `unsafe fn`, `unsafe impl`, `unsafe extern` blocks, `unsafe(...)` attributes, `SAFETY` lines, zeros included, skipping comments and strings. The gate fails when a crate outside the named list has a non-zero count, when a named crate has fewer `SAFETY` lines than blocks plus impls, or when the named list differs from ARCH §8.

**Worked example: a SIMD kernel.** Before, `l2_square_neon` (src/data_plane/api/data_plane/vector/ob_vector_l2_distance.h:1103-1182), chosen on arm64 by src/storage/vector_type/ob_vector_l2_distance.cpp:37-40, sums 16 floats per step in four float lanes and ends with `vaddvq_f32`; unlike `l2_square_normal` (:77-94) it never checks for infinity. After, in ob-simd:

```rust
#[cfg(target_arch = "aarch64")]
pub fn l2_square_neon(x: &[f32], y: &[f32]) -> f64 {
    use core::arch::aarch64::*;
    let dim = x.len();
    let y = &y[..dim];
    let mut sum = vdupq_n_f32(0.0);
    let mut d = dim;
    while d >= 16 {
        let (xs, ys) = (&x[dim - d..dim - d + 16], &y[dim - d..dim - d + 16]);
        // SAFETY: 7.10 rule 7: xs holds the 16 f32s vld1q_f32_x4 reads.
        let a = unsafe { vld1q_f32_x4(xs.as_ptr()) };
        // SAFETY: 7.10 rule 7: ys holds the 16 f32s vld1q_f32_x4 reads.
        let b = unsafe { vld1q_f32_x4(ys.as_ptr()) };
        let (c0, c1) = (vsubq_f32(a.0, b.0), vsubq_f32(a.1, b.1));
        let (c2, c3) = (vsubq_f32(a.2, b.2), vsubq_f32(a.3, b.3));
        let (c0, c1, c2, c3) = (vmulq_f32(c0, c0), vmulq_f32(c1, c1), vmulq_f32(c2, c2), vmulq_f32(c3, c3));
        sum = vaddq_f32(sum, vaddq_f32(vaddq_f32(c0, c1), vaddq_f32(c2, c3)));
        d -= 16;
    }
    // the 8-, 4- and 1-3-element tails follow ob_vector_l2_distance.h:1133-1179 in order
    vaddvq_f32(sum) as f64
}
```

Slicing checks the bounds, so each `unsafe` block holds only the load; NEON arithmetic intrinsics are safe functions in 1.98.1 (R12 §3). `&y[..dim]` aborts where the C++ would read past `y`. The six `*_simd.cpp` files hold no NEON code (`grep -c -E 'arm_neon|__aarch64__|vld1|vceq|uint8x16'` prints 0 for each), so on the arm64 reference their callers run scalar code, and the ports follow that code.

**Worked example: a platform call.** Before, every thread starts with `ob_set_thread_qos(ObThreadQoS::USER_INITIATED)` and `setpriority(PRIO_DARWIN_THREAD, 0, 0)` (src/oblib/lib/thread/thread.cpp:341-345; ob_platform_utils.h:352-376). After, ob-platform, called by the ob-runtime thread spawner:

```rust
pub fn set_current_thread_user_initiated() {
    // SAFETY: 7.10 table: sets only the calling thread's QoS class, with valid constants.
    let _ = unsafe { libc::pthread_set_qos_class_self_np(libc::qos_class_t::QOS_CLASS_USER_INITIATED, 0) };
    // SAFETY: 7.10 table: who = 0 is the calling thread; prio 0 clears its background state.
    let _ = unsafe { libc::setpriority(libc::PRIO_DARWIN_THREAD, 0, 0) };
}
```

## 7.11 What a reviewer checks

For island and named-crate units (PLAN §3's 25-50 island-shim units and the named crates' core units):
1. Each entry mirrors one of today's calls (7.2 rule 4), with the same codes, out-values and catch list, and is `noexcept`.
2. Each callback takes its receiver first or touches only allowed process-wide state; vsag callbacks read no thread-local; no callback re-enters its island or calls `smart_call!`.
3. Ownership: handles have `Drop` and one destroy entry; lifetimes tie handles to the arenas and inputs they point into; C++ frees nothing it did not allocate.
4. Every `unsafe` block holds one operation under a `SAFETY` line that cites this section; the crate's public API is safe; `Send`/`Sync` impls are the ones 7.3, 7.4 and 7.6 name.
5. The header, the export list, the flags extract and the bindgen outputs are regenerated and committed; the kept files are unchanged.

## 7.12 The earlier sql-nio conventions, adopted or rejected

Prior art from abi-naming.md and notes/ffi-mechanics.md, which the developer discarded as a plan (the migrate-seekdb-to-rust skill's status note, 2026-09-24):

| Convention | Verdict | Reason |
|---|---|---|
| Header generated by cbindgen, written by build.rs into `include/<tag>.h`, committed, diffed in CI | adopt | the C++ compiles against the regenerated file, so drift is a compile error; the `sm_conn_greeting_info` failure came from two hand-written copies |
| cbindgen settings of rust/sql-nio/cbindgen.toml | adopt, adding `stdbool.h` and the `noexcept` postfix | 7.2 rule 1 |
| What enters the header: `pub const`s, functions by `#[no_mangle]` regardless of visibility, structs only when used, opaque types without `#[repr(C)]` | adopt as cbindgen 0.29.4's behavior | `#[unsafe(no_mangle)]` only in `ffi` keeps the header's contents deliberate |
| Structs `<Tag><Name>`, same name on both sides, no rename table | adopt | one grep word across languages |
| Rust-implemented functions `<tag>_*`; C++-implemented shims keep their C++ names | change: C++ entries `<tag>_<verb>`, Rust callbacks `<tag>_rs_<verb>` | island entries are new functions with no C name, and C++ implements most of them; the parser keeps its existing C names |
| Tags of 2-5 letters, `sk` before generic ones | reject the length rule; keep uniqueness and registration here | `obvsag` (ARCH §9.3) has 6 letters; the real risk is a clash with the library's own C API |
| `_view`, `_handle` with `_acquire`/`_release`, return 0/-1 | keep views; handles use `_create`/`_destroy`; reject 0/-1 | island objects have one owner and no leases; codes reach the user, so they must be exact |
| Receiver as the first parameter, never a global; none for process singletons | adopt | 7.2 rule 8 |
| No function-pointer tables | adopt for island ABIs | a third-party C API that requires pointers (CRoaring, libxml2 SAX, zlib, zstd) gets them from the named crate |
| All reverse declarations in one file | adopt as the `ffi` module | Decision 14 confines `unsafe` there |
| Two layers: safe logic and a thin `extern "C"` adapter | adopt | |
| Callers guarantee pointers and lengths; `&[]` for length 0 | adopt in both directions | 7.10 rule 5 |
| `#[cfg(test)]` stubs so each crate tests without C++ | reject | an island test against a stub tests nothing; other crates export no C symbols |
| `shim` marker for transitional symbols | reject | the islands stay until after parity on purpose; one cutover leaves nothing half-ported |
| No run-time ABI version check | adopt | same tree, static link |
| bindgen only for existing C APIs, never in reverse | adopt, run by a gate script with output committed | the parser headers and the four libraries already exist; Rust is the source for island headers |
| Contracts in vault notes, no comments in code | change: contracts live in this section | working documents are in migration/; code keeps only ARCH §14's markers |
| `panic = "abort"` on named profiles only; `cmake-debug` profile | change: abort in every profile; drop `cmake-debug` | Rust owns `main` (ARCH §13) |

## 7.13 Defaults this section adds, for the developer to confirm

1. Kept C and C++ stays at its src/ paths through the cutover (7.1).
2. An exception escaping an island entry whose path has no handler today ends the process, as it does today (7.2 rule 6).
3. Every CRoaring allocation counts toward the vector budget; the C++ counts only "VIB"-labelled ones (7.4).
4. Geo kernel scratch memory is not charged, since no -11049 check point can see it (7.8 rule 2).

## 7.14 Objections to ARCHITECTURE.md

1. **§9.4 does not say where the kept oblib subset lives, and a shared copy cannot work in §1.1's graph.** All 38 vsag symbols are among geo's 124, and all 118 vsag headers among geo's 196 (7.7). Two islands defining the same strong symbols clash, and vsag-sys may not use geo-sys (§1.1, row 13). This section compiles one copy per island and links each partially with an export list, which the reference already does for zstd (src/oblib/lib/compress/zstd_1_3_8/CMakeLists.txt:33-38).
2. **CRoaring is missing from §8, §9.4's mechanism and §13.** libroaring.a is linked (src/oblib/CMakeLists.txt:315; the vsag list at src/oblib/lib/CMakeLists.txt:111-126), vsag calls it, and three seekdb files call its C API under a process-wide hook (7.4). The hook has no user-data pointer, so "charged only through allocation callbacks whose receiver is the thing charged" cannot hold for it, and R08's "`ObMallocHookAttrGuard` has no effect on macOS" is wrong for CRoaring (ob_rb_memory_mgr.cpp:40-48). This section puts the binding in vsag-sys. §1.1's sql-das row lacks 13 for ob_das_ivf_scan_iter.cpp's bitmap (:3036-3095): add it, or route that bitmap through storage-search.
3. **§8's ob-platform list is incomplete.** Besides the jemalloc impl, `_exit`, QoS, `fork` and `lockf`, the C++ that ob-platform replaces calls the zone promotion (ob_malloc.cpp:64-116, which §7.3 gives to seekdb but which needs `unsafe`), `setpriority` (thread.cpp:345), `statvfs` (ob_local_device.cpp:1298), `fcntl(F_PREALLOCATE)` (:1421), `sysconf` (ob_platform_utils.h:311, :328) and `pthread_threadid_np` (:447), and `je_malloc_conf` needs a `Sync` impl. Proposed wording: "libc calls with no safe std counterpart, each listed in the rulebook" (7.10's table).
4. **§1.1 misses a use of ob-clib-sys.** sql-engine (28) calls zlib directly for OUTFILE GZIP and DEFLATE (src/sql/engine/basic/ob_select_into_basic.cpp:255-260), but its row lacks 6.
5. **§9.3 rule 3 omits `float` and typed views.** vsag passes `float *` vectors and distances, `int64_t *` ids and `uint32_t *` sparse dims (ob_vsag_adaptor.h:122-160); 7.2 rule 3 allows them.
6. **§11's closed list of thread-locals needs the parser's slot.** `try_check_mem_status` takes no receiver (parse_node.c:178, :186) and the C++ reaches the tracker through a thread-local (ob_memory_tracker.cpp:25). Changing the kept C instead would break 7.1 rule 1.
7. **§9.4's "-11049 keeps counting GIS memory" cannot be observed for kernel scratch.** It holds for built geometries and results, which this section charges; the guard's context lives only during one evaluation (ob_expr_st_area.cpp:79-104), when no check point runs (7.8 rule 2).
8. **§9.2's `ob_parse_binary_simd` is x86_64-only.** parse_node.c:551-553 and ob_parse_simd.cpp:28-216; the arm64 release needs no Rust definition.
9. **§1.1's sql-nio note names two unix `libc` calls; there are three** (pump.rs:496-502 as well), and `frame_payload_in_place` (response.rs:918) and `CppSessionStorage` also go (7.10).
10. **§13's reference flags are a subset.** The geo chunks also use `-fmax-type-align=8`, `-mmacosx-version-min=27.0`, `-std=gnu++20`, `-DNDEBUG` and `-D_NO_EXCEPTION` (compile_commands.json); islands take the full command (7.9 rule 1).
11. **§1.1's ob-simd sources hold no arm64 kernels.** The six `*_simd.cpp` files are x86-only; the first release's NEON kernels are the vector distance functions in src/data_plane/api/data_plane/vector (69 NEON intrinsic lines in the L2 and IP headers, `git grep -c`).
12. **§2's generated "C header for the islands" has no island user.** geo and vsag keep the frozen src/share/ob_errno.h their kept code includes (7.2 rule 5), and the parser's C core has its own 19 codes (src/sql/parser/parse_define.h:26-44), which §2's generator already checks. The generator needs the value check of 7.2 rule 5 instead.
