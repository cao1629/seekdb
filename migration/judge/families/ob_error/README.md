# Family 6: the error catalog through `ob_error`

PLAN.md section 4, family 6. tools/ob_error/test/test.sh runs the 18 `ob_error` lines of
ob_error_test.test and compares the output with expect_result.result, but it writes test.result
into tools/ob_error/test/ (which the judge's working-tree check forbids) and runs whatever `ob_error`
is first on the PATH. `run_ob_error_test.sh` does the same comparison with the output outside the
tree and a named binary:

```
migration/judge/families/ob_error/run_ob_error_test.sh <dir>/ob_error <out-dir>
```

- The binary must be named `ob_error`, because the test lines call it by name; its directory is put
  first on the PATH, and the script checks that `ob_error` resolves to it.
- It writes `<out-dir>/test.result` in test.sh's format, `manifest.txt` (the binary and the input
  checksums, then `status=identical` or `status=different`), `stderr.log`, and `diff.txt` on a
  difference. Exit 0 means identical to expect_result.result.
- C++ against Rust: run it once per build; each must be identical to expect_result.result, and the
  two test.result files must be identical to each other.

## Building `ob_error`

At 834bbee1e the top-level CMakeLists.txt adds tools/ob_error only on Linux (lines 315-317,
`if(CMAKE_SYSTEM_NAME STREQUAL "Linux")`, since 4cb6fb268 of 2026-08-07), so the build on this Mac has
no `ob_error` target, and `make ob_error` in build_release stops with
``No rule to make target `ob_error'``. `build_ob_error.sh` builds the tool outside CMake:

```
migration/judge/families/ob_error/build_ob_error.sh <source-tree> <out-dir>
```

- It compiles the target's three sources (tools/ob_error/src/ob_error.cpp and os_errno.cpp, and
  src/share/ob_errno.cpp) with the tree's deps/3rd clang++ (17.0.6) against the SDK in `$SDKROOT`
  (by default the archive's MacOSX26.2.sdk), and links `<out-dir>/ob_error`.
- The flags are the ones CMake gives an executable target added at that point of the top-level
  CMakeLists.txt on this Mac, read from cmake/Env.cmake, the top-level CMakeLists.txt and the reference
  build's compile_commands.json and link.txt: the global definitions (`DEFAULT_LOG_FILE_SIZE_MB=256`,
  `DEFAULT_LOG_LEVEL=OB_LOG_LEVEL_ERROR`, `OB_BUILD_SYS_VEC_IDX`, `_DARWIN_C_SOURCE`,
  `_GLIBCXX_USE_CXX11_ABI=1`), the target's `-D__ERROR_CODE_PARSER_` and include directories, the
  global include of rust/sql-nio/include, `CMAKE_CXX_FLAGS` as cmake/Env.cmake sets it on Apple
  (`-std=gnu++20`, the two prefix maps, `-ffunction-sections -fdata-sections -fmax-type-align=8`),
  `-O2 -g -DNDEBUG`, `-arch arm64 -isysroot <SDK> -mmacosx-version-min=27.0`, `-fPIE` and
  `-ffp-contract=off`, and on the link line `-Wl,-search_paths_first -Wl,-headerpad_max_install_names`
  and `-Wl,-dead_strip`. The oblib warning set (`-Wall -Wextra -Werror` and its exceptions) is left
  out, because the target does not link oblib_base. The exact lines are in each build's build.txt.
- It writes only into `<out-dir>`, and refuses an output directory inside the source tree or one that
  already holds a build. `<out-dir>/build.txt` records the tree's HEAD and `git status`, the compiler
  and SDK versions, the sha256 of the three sources and the five catalog headers they include, the
  exact compile and link commands, and the binary's sha256. build_release/ and the seekdb binary are
  not touched, so the mutation stage can use it too
  (../../mutations/second-set/03-ob_error-rowid-message-user-format.md).

The reference tool is archived next to the seekdb binary (item 13):
/Users/colin/seekdb-dev/ref-archive-834bbee1e/ob_error, with ob_error.sha256 and the build record
ob_error.build.txt.

## Checked so far (2026-09-24)

Offline only, with a stand-in `ob_error` script: the output format matches expect_result.result line
for line where the stand-in prints the same text, differences land in diff.txt, and a second run into
the same directory is refused. Not yet run with the real tool. One line to watch: `ob_error 13`
expects "Linux Error Code: EACCES(13)", which a macOS build may print differently; if the reference
differs there, that line's handling is decided at the second sign-off, not masked silently.

## Live check, 2026-09-25

Unit 03-oberr of the second set. Outputs: /Users/colin/seekdb-dev/mysqltest-runs/00b/second-set/03-oberr/.
The family starts no seekdb instance and does not use the runner, so ports, `--max-retries` and the
trailing-whitespace switch do not apply; the comparison is `cmp`, byte for byte.

- **The prescribed build does not exist on this Mac.** `SDKROOT=<archive SDK> make -j8 ob_error` in
  /Users/colin/seekdb-dev/ref-834bbee1e/build_release exited 2 with
  ``No rule to make target `ob_error'`` and ran nothing (prescribed-make/). The note of
  2026-09-24 saw the `EXCLUDE_FROM_ALL` on CMakeLists.txt:316 but not the Linux guard around it.
  **Change:** added build_ob_error.sh and rewrote "Building `ob_error`" above.
- **The build.** build_ob_error.sh on the reference worktree (build-ref/): 3 s, no warnings; Clang
  17.0.6 (ob-deps 38008c2a88a845ca1dbb6fc02d7e47c7d455b196), SDK 26.2 from the archive, macOS 27.0
  (26A428), tree at 834bbee1e with only ` M cmake/Env.cmake`. The result is a 419,240-byte Mach-O
  arm64 executable that links only /usr/lib/libc++.1.dylib and /usr/lib/libSystem.B.dylib, sha256
  `6054d899d80c500d7871719de0ae06620db46cbdd93f2d51ceef52d083628752`, copied to the archive with
  ob_error.sha256 and ob_error.build.txt.
- **The reference worktree is untouched.** Afterwards `git -C /Users/colin/seekdb-dev/ref-834bbee1e diff`
  is byte-identical to reference-build.patch, `git status --porcelain` shows only ` M cmake/Env.cmake`,
  and build_release/src/observer/seekdb keeps its mtime (2026-09-24 22:33:01), inode and sha256
  (`e4952fcceb70de7e61d03294c950667f5c9506c933512bc8f508f9be0fe7c4e0`): it was not relinked.
- **Two runs on the archived tool** (run1/, run2/). Before each, the working-tree check of PLAN.md
  section 4 passed, and tools/ob_error in migrate-to-rust equals 834bbee1e. Both exited 0 with
  `status=identical` and an empty stderr.log: 18 commands, 134 lines. run1/test.result,
  run2/test.result and expect_result.result all have sha256
  `a2404ed37098727d82ef4b6d98439478e34f2efe4d8bfdd21136f4873131ed59`.
- **The `ob_error 13` line matches the Linux-recorded file.** The macOS build prints "Linux Error
  Code: EACCES(13)" and "Message: Permission denied" because nothing in that output comes from the
  host: the label is a literal in ob_error.cpp:61, and the name, number and text come from the
  generated table in tools/ob_error/src/os_errno.cpp, which holds the Linux values as literals
  (`OS_ERRNO[-OS_EACCES] = 13`, "Permission denied"), not from the host's errno.h or strerror().
- **Decision: keep both comparisons.** Each build's test.result is compared with expect_result.result,
  and the two builds' files with each other, as the first section says. The C++ reference reproduces
  the checked-in file byte for byte on this Mac, so the two checks give the same verdict; comparing
  with expect_result.result also ties the reference to a tracked file. No line is set aside as not
  comparable.
- **run_ob_error_test.sh is unchanged**: the first real run exposed no bug in it.
- **The family's mutation:** ../../mutations/second-set/03-ob_error-rowid-message-user-format.patch
  (and its .md). The catalog entry -5870 (`OB_ROWID_TYPE_MISMATCH`) gets its user-error text as its
  plain message, which changes test.result line 82 and nothing else. `git apply --check` passes on the
  reference worktree; the mutation stage builds and runs it with build_ob_error.sh.
- **What the family does not cover.** The 18 commands print 15 catalog entries (9 by their own code,
  6 through the MySQL codes 1017, 1210 and 5133), one OS code and four codes that are not found, out
  of the 1,544 nonzero codes in `g_all_ob_errnos`. A regenerated catalog could change the text of any
  other entry without this family noticing, and the 272 cases see only the codes the server raises in
  them. A sweep of `ob_error <n>` over every code in `g_all_ob_errnos`, compared build against build,
  would close that gap in seconds; it is outside family 6 as PLAN.md defines it, so it is left to the
  second sign-off.
