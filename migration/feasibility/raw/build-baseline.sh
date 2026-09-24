#!/bin/bash
set -u
W=/Users/colin/seekdb-dev/migrate-to-rust
L=/Users/colin/.claude/jobs/39d4f781/tmp/build
T=$L/timings.txt
cd "$W" || exit 1
stamp() { date +%s; }
rec() { echo "$1 rc=$2 secs=$3" >> "$T"; }
if [ ! -e deps/3rd ]; then
  s=$(stamp); cp -c -R /Users/colin/seekdb-dev/seekdb/deps/3rd deps/3rd; rec deps_clone $? $(( $(stamp) - s ))
fi
export SDKROOT=$(xcrun --show-sdk-path)
s=$(stamp); bash build.sh release --make > "$L/full-build.log" 2>&1; rc=$?; rec full_build $rc $(( $(stamp) - s ))
if [ $rc -eq 0 ]; then
  s=$(stamp); (cd build_release && make -j14 seekdb) > "$L/noop.log" 2>&1; rec noop_rebuild $? $(( $(stamp) - s ))
  touch src/sql/engine/expr/ob_expr_add.cpp
  s=$(stamp); (cd build_release && make -j14 seekdb) > "$L/leaf-cpp.log" 2>&1; rec leaf_cpp_rebuild $? $(( $(stamp) - s ))
  touch src/sql/optimizer/ob_log_plan.cpp
  s=$(stamp); (cd build_release && make -j14 seekdb) > "$L/leaf-cpp2.log" 2>&1; rec leaf_cpp2_rebuild $? $(( $(stamp) - s ))
  ls -la build_release/src/observer/seekdb >> "$T" 2>&1
  build_release/src/observer/seekdb -V >> "$T" 2>&1
fi
cd "$W/rust" || exit 1
s=$(stamp); cargo check --workspace > "$L/cargo-check.log" 2>&1; rec cargo_check_warm $? $(( $(stamp) - s ))
cargo clean -p sql-nio > /dev/null 2>&1
s=$(stamp); cargo build --release -p sql-nio > "$L/cargo-build.log" 2>&1; rec cargo_build_sqlnio_release $? $(( $(stamp) - s ))
echo DONE >> "$T"
