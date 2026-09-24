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
set -u
BIN=${1:?path to the ob_error binary}
OUT=${2:?output directory}
H=$(cd "$(dirname "$0")/../../../.." && pwd)
SRC=$H/tools/ob_error/test
[ -x "$BIN" ] || { echo "not executable: $BIN" >&2; exit 2; }
[ "$(basename "$BIN")" = ob_error ] || { echo "the binary must be named ob_error: $BIN" >&2; exit 2; }
mkdir -p "$OUT"
[ -e "$OUT/test.result" ] && { echo "output exists: $OUT/test.result" >&2; exit 2; }
BINDIR=$(cd "$(dirname "$BIN")" && pwd)
RESOLVED=$(PATH="$BINDIR:$PATH" command -v ob_error)
[ "$RESOLVED" = "$BINDIR/ob_error" ] || { echo "ob_error resolves to $RESOLVED, not $BINDIR/ob_error" >&2; exit 2; }
{
  echo "binary=$BINDIR/ob_error"
  echo "binary_sha256=$(shasum -a 256 "$BINDIR/ob_error" | cut -d' ' -f1)"
  echo "test_sha256=$(shasum -a 256 "$SRC/ob_error_test.test" | cut -d' ' -f1)"
  echo "expected_sha256=$(shasum -a 256 "$SRC/expect_result.result" | cut -d' ' -f1)"
} > "$OUT/manifest.txt"
while read -r line; do
  echo "\$$line" >> "$OUT/test.result"
  (PATH="$BINDIR:$PATH" eval "$line") >> "$OUT/test.result" 2>> "$OUT/stderr.log"
done < "$SRC/ob_error_test.test"
if cmp -s "$OUT/test.result" "$SRC/expect_result.result"; then
  echo "status=identical" >> "$OUT/manifest.txt"
  echo "identical to expect_result.result"
  exit 0
fi
echo "status=different" >> "$OUT/manifest.txt"
diff -u "$SRC/expect_result.result" "$OUT/test.result" > "$OUT/diff.txt"
echo "different from expect_result.result: $OUT/diff.txt"
exit 1
