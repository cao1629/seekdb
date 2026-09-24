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

The tool is `EXCLUDE_FROM_ALL` (CMakeLists.txt:316), so the reference build does not make it. In the
reference worktree, after the injected-mutation runs have ended and the worktree is back to
reference-build.patch only:

```
cd /Users/colin/seekdb-dev/ref-834bbee1e/build_release
SDKROOT=/Users/colin/seekdb-dev/ref-archive-834bbee1e/MacOSX26.2.sdk make -j14 ob_error
```

The built tool is then copied into the archive (item 13) next to the seekdb binary, with its sha256.

## Checked so far (2026-09-24)

Offline only, with a stand-in `ob_error` script: the output format matches expect_result.result line
for line where the stand-in prints the same text, differences land in diff.txt, and a second run into
the same directory is refused. Not yet run with the real tool. One line to watch: `ob_error 13`
expects "Linux Error Code: EACCES(13)", which a macOS build may print differently; if the reference
differs there, that line's handling is decided at the second sign-off, not masked silently.
