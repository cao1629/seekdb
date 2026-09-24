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

## The archive and its rebuild check (item 13; 00b action 11, 2026-09-24)

/Users/colin/seekdb-dev/ref-archive-834bbee1e/ holds:

| Entry | What |
|---|---|
| seekdb, seekdb.sha256 | the flagged reference binary every judge run uses |
| deps-3rd/ | the unpacked deps (an APFS clone of the main checkout's deps/3rd; holds the Clang 17.0.6 toolchain) |
| MacOSX26.2.sdk/ | a full copy of the SDK the reference was built against (777 MB) |
| client/ | obclient and mysqltest from deps/3rd/u01/obclient/bin, with sha256.txt |
| reference-build.patch | the build-file patch |
| recordings/ | a9-rec-flag (the 40 plan-bearing cases, full init) and a10-rec1, a10-rec2 (the 128 plain-SQL cases, reduced init) |

The rebuild check: the reference worktree's build_release/ was removed and its deps/3rd replaced by a
clone of the archive's deps-3rd/, then built with `SDKROOT=<archive>/MacOSX26.2.sdk bash build.sh
release --make`:
- 381 s, 0 errors; `seekdb -V` reports 834bbee1e;
- the same compiler (Clang 17.0.6, ob-deps 38008c2a88a845ca1dbb6fc02d7e47c7d455b196), the same
  `_LIBCPP_VERSION` (200100), the same SDK version (26.2), and `-ffp-contract=off` on all 364 compile
  commands;
- the 40 plan-bearing cases recorded on the rebuilt binary are identical to the archived recording
  (`compare`: 40 identical, 0 different, 0 missing, 0 recording problems).
The rebuilt binary's sha256 differs from the archived one only because the build embeds its build
time; judge runs keep using the archived binary. Outputs: /Users/colin/seekdb-dev/mysqltest-runs/00b/a11-rebuild/.

Repeat the rebuild check after any macOS or Command Line Tools update, since the runtime libc++ comes
from macOS.
