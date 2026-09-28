#!/bin/bash
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
set -u
if [ $# -lt 3 ]; then
  echo "usage: record.sh RUN_DIR SEEKDB PORT [more runner run options]" >&2
  exit 2
fi
RUN="$1"
SEEKDB="$2"
PORT="$3"
shift 3
HERE="$(cd "$(dirname "$0")" && pwd)"
H="$(cd "$HERE/../../../.." && pwd)"
CLIENT="${PLAN_CACHE_CLIENT_DIR:-/Users/colin/seekdb-dev/ref-archive-834bbee1e/client}"
if [ -e "$RUN" ]; then echo "run directory already exists: $RUN" >&2; exit 3; fi
git -C "$H" diff --quiet 834bbee1e -- tools/deploy .github/script/seekdb/sdb.py || { echo "tools/deploy or sdb.py differ from 834bbee1e" >&2; exit 3; }
if [ -n "$(git -C "$H" status --porcelain -- tools/deploy)" ]; then echo "tools/deploy has local changes" >&2; exit 3; fi
AVAIL_KB=$(df -k / | awk 'NR==2 {print $4}')
if [ "$AVAIL_KB" -lt $((8 * 1024 * 1024)) ]; then echo "less than 8 GiB free on /" >&2; exit 3; fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then echo "port $PORT is in use" >&2; exit 3; fi
HOUR_PLUS_8=$(( (10#$(date -u +%H) + 8) % 24 ))
if [ "$HOUR_PLUS_8" -ge 19 ] && [ "$HOUR_PLUS_8" -lt 23 ]; then
  echo "it is ${HOUR_PLUS_8}:xx at +08:00: a recording started now can reach 22:00-23:00 +08:00, when every new instance runs the day's statistics maintenance window job; start after 23:00 or before 19:00 +08:00" >&2
  exit 3
fi
unset SEEKDB_COV_PROFRAW_DIR
mkdir -p "$RUN"
RUN="$(cd "$RUN" && pwd)"
cd "$H"
shasum -a 256 "$H/.github/script/seekdb/mysqltest_for_seekdb.py" > "$RUN/runner.sha256"
echo "started $(date '+%Y-%m-%d %H:%M:%S') avail_kb=$AVAIL_KB" > "$RUN/times.txt"
python3 "$HERE/logwatch.py" "$RUN/instance/log/seekdb.log" "$RUN/seekdb-log.gz" &
WATCH_PID=$!
echo "$WATCH_PID" > "$RUN/logwatch.pid"
python3 -u "$H/.github/script/seekdb/mysqltest_for_seekdb.py" run \
  --seekdb "$SEEKDB" \
  --obclient "$CLIENT/obclient" \
  --mysqltest "$CLIENT/mysqltest" \
  --base-dir "$RUN/instance" --work-dir "$RUN/work" --port "$PORT" \
  --slice-index 0 --slice-count 1 --max-retries 0 --no-ignore-trailing-whitespace \
  --plan-cache-stats --fresh-instance-per-case \
  --seekdb-parameter plan_cache_evict_interval=1d \
  --record-dir "$RUN/rec" "$@" > "$RUN/runner.log" 2>&1
STATUS=$?
kill -TERM "$WATCH_PID" 2>/dev/null
wait "$WATCH_PID"
echo "finished $(date '+%Y-%m-%d %H:%M:%S') exit=$STATUS avail_kb=$(df -k / | awk 'NR==2 {print $4}')" >> "$RUN/times.txt"
exit $STATUS
