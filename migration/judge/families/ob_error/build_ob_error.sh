#!/bin/bash
#
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
set -eu
TREE=${1:?path to the C++ source tree}
OUT=${2:?output directory}
[ -d "$TREE" ] || { echo "no source tree: $TREE" >&2; exit 2; }
TREE=$(cd "$TREE" && pwd)
SDK=${SDKROOT:-/Users/colin/seekdb-dev/ref-archive-834bbee1e/MacOSX26.2.sdk}
CXX=$TREE/deps/3rd/usr/local/oceanbase/devtools/bin/clang++
[ -x "$CXX" ] || { echo "no compiler: $CXX" >&2; exit 2; }
[ -d "$SDK" ] || { echo "no SDK: $SDK" >&2; exit 2; }
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
[ -e "$OUT/ob_error" ] && { echo "output exists: $OUT/ob_error" >&2; exit 2; }
[ -e "$OUT/build.txt" ] && { echo "output exists: $OUT/build.txt" >&2; exit 2; }
case "$OUT/" in "$TREE"/*) echo "output directory is inside the source tree: $OUT" >&2; exit 2;; esac
mkdir -p "$OUT/obj"

SOURCES=(tools/ob_error/src/ob_error.cpp tools/ob_error/src/os_errno.cpp src/share/ob_errno.cpp)
HEADERS=(tools/ob_error/src/ob_error.h tools/ob_error/src/os_errno.h src/share/ob_errno.h src/share/mysql_errno.h src/oblib/lib/ob_errno.h)
DEFS=(-DDEFAULT_LOG_FILE_SIZE_MB=256 -DDEFAULT_LOG_LEVEL=OB_LOG_LEVEL_ERROR -DOB_BUILD_SYS_VEC_IDX -D_DARWIN_C_SOURCE -D_GLIBCXX_USE_CXX11_ABI=1 -D__ERROR_CODE_PARSER_)
INCS=("-I$TREE/rust/sql-nio/include" "-I$TREE" "-I$TREE/src" "-I$TREE/src/oblib")
FLAGS=(-std=gnu++20 "-fdebug-prefix-map=$TREE=." "-ffile-prefix-map=$TREE=." -fcolor-diagnostics -ffunction-sections -fdata-sections -fmax-type-align=8 "-Wno-#pragma-messages" -O2 -g -DNDEBUG -arch arm64 -isysroot "$SDK" -mmacosx-version-min=27.0)
LINK=(-Wl,-search_paths_first -Wl,-headerpad_max_install_names -Wl,-dead_strip -Wl,-dead_strip)

{
  echo "tree=$TREE"
  echo "tree_head=$(git -C "$TREE" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "tree_status_begin"
  git -C "$TREE" status --porcelain 2>/dev/null || echo unknown
  echo "tree_status_end"
  echo "compiler=$CXX"
  echo "compiler_version=$("$CXX" --version | head -1)"
  echo "sdk=$SDK"
  echo "sdk_version=$(plutil -extract Version raw -o - "$SDK/SDKSettings.plist" 2>/dev/null || echo unknown)"
  echo "host=$(sw_vers -productVersion) $(sw_vers -buildVersion)"
  for f in "${SOURCES[@]}" "${HEADERS[@]}"; do
    echo "sha256 $(shasum -a 256 "$TREE/$f" | cut -d' ' -f1) $f"
  done
} > "$OUT/build.txt"

OBJS=()
for f in "${SOURCES[@]}"; do
  o="$OUT/obj/$(basename "$f").o"
  cmd=("$CXX" "${DEFS[@]}" "${INCS[@]}" "${FLAGS[@]}" -fPIE -ffp-contract=off -o "$o" -c "$TREE/$f")
  echo "compile ${cmd[*]}" >> "$OUT/build.txt"
  "${cmd[@]}" 2>> "$OUT/build.log"
  OBJS+=("$o")
done
cmd=("$CXX" "${FLAGS[@]}" "${LINK[@]}" "${OBJS[@]}" -o "$OUT/ob_error")
echo "link ${cmd[*]}" >> "$OUT/build.txt"
"${cmd[@]}" 2>> "$OUT/build.log"
echo "ob_error_sha256=$(shasum -a 256 "$OUT/ob_error" | cut -d' ' -f1)" >> "$OUT/build.txt"
echo "built $OUT/ob_error"
