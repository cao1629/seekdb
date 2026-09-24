# The pinned C++ reference

The C++ build every judge run compares against (PLAN.md section 4, item 13).

## What it is

| What | Value |
|---|---|
| Source | 834bbee1e, unchanged |
| Build-file patch | `reference-build.patch` (this directory): skips the `MacOS27.cmake` include (`if(FALSE AND MACOS_MAJOR GREATER_EQUAL 27)`) and adds `add_compile_options(-ffp-contract=off)` after the coverage block in cmake/Env.cmake |
| Worktree | /Users/colin/seekdb-dev/ref-834bbee1e (detached at 834bbee1e, the patch applied, uncommitted) |
| Build command | `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk bash build.sh release --make` (no `--init`; deps/3rd cloned from /Users/colin/seekdb-dev/seekdb with `cp -c -R`) |
| Build type | RelWithDebInfo, unity build, `CMAKE_OSX_DEPLOYMENT_TARGET` 27.0 |
| Compiler | deps/3rd/usr/local/oceanbase/devtools/bin/clang 17.0.6 (ob-deps 38008c2a88a845ca1dbb6fc02d7e47c7d455b196), target arm64-apple-darwin27.0.0 |
| SDK | /Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk, version 26.2 |
| libc++ headers | from the SDK; `_LIBCPP_VERSION` = 200100 |
| Host | macOS 27.0 (26A428), Apple M4 Pro, 14 cores, 24 GiB |
| Flag check | all 364 entries of build_release/compile_commands.json (324 C++ units and 40 C units) carry `-ffp-contract=off` |
| Archived binary | /Users/colin/seekdb-dev/ref-archive-834bbee1e/seekdb, sha256 `db7d918001aa02c45357c37b7bc01179d08a7a01e7e16d32248d25da80282e91`; `seekdb -V` reports revision 834bbee1e |
| Unflagged binary (for the flag check, action 9) | /Users/colin/seekdb-dev/mysqltest-runs/ref-834bbee1e-noflag/seekdb, sha256 `157b8a3aa61bf712bee04e01c9eed74dc0cb7f7644ac2699cc9aa924d4663889`; deleted after action 9 |

Every judge run uses the archived binary, never the worktree's build_release/.

## Build times (2026-09-24)

| Build | Time |
|---|---|
| Without the flag, clean | 317 s |
| With the flag (all objects recompiled) | 303 s |
| After removing two comment lines from the patch | 3 s, nothing recompiled or relinked |

`df -h /` showed 40-41 GiB free after the builds.

## Still to do for item 13

The rest of the archive (deps/3rd as an APFS clone, a copy of the SDK directory, obclient and mysqltest, the recorded outputs) and the rebuild-from-archive check come in action 11. Repeat the rebuild check after any macOS or Command Line Tools update, since the libc++ headers come from the SDK and the runtime libc++ from macOS.
