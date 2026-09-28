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
CATALOG=$H/src/share/ob_errno.cpp
[ -x "$BIN" ] || { echo "not executable: $BIN" >&2; exit 2; }
[ "$(basename "$BIN")" = ob_error ] || { echo "the binary must be named ob_error: $BIN" >&2; exit 2; }
mkdir -p "$OUT"
[ -e "$OUT/sweep.result" ] && { echo "output exists: $OUT/sweep.result" >&2; exit 2; }
BINDIR=$(cd "$(dirname "$BIN")" && pwd)
grep -o 'int g_all_ob_errnos\[[0-9]*\] = {[^}]*}' "$CATALOG" | sed 's/.*{//; s/}//' | tr ',' '\n' | tr -d ' -' | grep -v '^0$' | grep -v '^$' > "$OUT/codes.txt"
COUNT=$(wc -l < "$OUT/codes.txt" | tr -d ' ')
[ "$COUNT" -gt 0 ] || { echo "no codes read from $CATALOG" >&2; exit 2; }
{
  echo "binary=$BINDIR/ob_error"
  echo "binary_sha256=$(shasum -a 256 "$BINDIR/ob_error" | cut -d' ' -f1)"
  echo "catalog=$CATALOG"
  echo "catalog_sha256=$(shasum -a 256 "$CATALOG" | cut -d' ' -f1)"
  echo "codes=$COUNT"
} > "$OUT/manifest.txt"
while read -r code; do
  echo "\$ob_error $code" >> "$OUT/sweep.result"
  "$BINDIR/ob_error" "$code" >> "$OUT/sweep.result" 2>> "$OUT/stderr.log"
  echo "exit=$?" >> "$OUT/sweep.result"
done < "$OUT/codes.txt"
echo "sweep_sha256=$(shasum -a 256 "$OUT/sweep.result" | cut -d' ' -f1)" >> "$OUT/manifest.txt"
echo "swept $COUNT codes into $OUT/sweep.result"
