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
BIN=${1:?binary}
OUT=${2:?output dir}
PORT=${3:-3883}
ROUNDS=${4:-5}
WORKLOADS=${5:-oltp_point_select oltp_read_write}
CLIENT=${OBCLIENT:-/Users/colin/seekdb-dev/ref-archive-834bbee1e/client/obclient}
CONTAINER=${SYSBENCH_CONTAINER:-sb}
mkdir -p "$OUT"
[ -z "$(ls -A "$OUT")" ] || { echo "output dir must be empty: $OUT" >&2; exit 2; }
BASE=$OUT/base
mkdir -p "$BASE"
T=$OUT/results.tsv
printf 'round\tworkload\tthreads\tstatus\tqps\tavg_ms\tp95_ms\tload_before\tload_after\tserver_cpu_max\tmemory_pressure\n' > "$T"
now_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }
ready() { "$CLIENT" -h 127.0.0.1 -P "$PORT" -uroot -A -N -s -e 'select 1' >/dev/null 2>&1; }
start_server() {
  "$BIN" --nodaemon --base-dir "$BASE" --port "$PORT" --parameter memory_limit=8G --parameter cpu_count=8 --parameter system_memory=1G --parameter datafile_size=2G --parameter datafile_maxsize=4G --parameter log_disk_size=2G >> "$OUT/server-console.log" 2>&1 &
  PID=$!
  local s=$(now_ms)
  for i in $(seq 1 600); do ready && break; sleep 1; done
  ready || { echo "server did not become ready" >&2; kill -9 $PID; exit 1; }
  ELAPSED=$(( $(now_ms) - s ))
}
start_server
COLD=$ELAPSED
echo "cold_start_ms=$COLD pid=$PID" >> "$OUT/lifecycle.txt"
lsof -nP -iTCP:$PORT -sTCP:LISTEN >> "$OUT/lifecycle.txt" 2>&1
"$CLIENT" -h 127.0.0.1 -P "$PORT" -uroot -A -e 'create database if not exists sbtest' >> "$OUT/lifecycle.txt" 2>&1
COMMON="--db-driver=mysql --mysql-host=host.docker.internal --mysql-port=$PORT --mysql-user=root --mysql-db=sbtest --tables=16 --table-size=100000 --rand-type=uniform"
docker exec "$CONTAINER" sysbench oltp_read_write $COMMON --threads=8 prepare > "$OUT/prepare.log" 2>&1 || { echo "prepare failed" >&2; kill -9 $PID; exit 1; }
sample_cpu() { local max=0; while kill -0 $PID 2>/dev/null && [ -e "$OUT/.sampling" ]; do c=$(ps -o pcpu= -p $PID | tr -d ' '); max=$(python3 -c "print(max(float('$max'), float('${c:-0}')))"); echo $max > "$OUT/.cpumax"; sleep 5; done; }
for r in $(seq 1 $ROUNDS); do
  for w in $WORKLOADS; do
    PS=""; [ "$w" = oltp_read_write ] && PS="--db-ps-mode=disable"
    for th in 1 16 64; do
      docker exec "$CONTAINER" sysbench $w $COMMON $PS --threads=$th --time=15 run > /dev/null 2>&1
      lb=$(sysctl -n vm.loadavg | awk '{print $2}')
      ps -axo pcpu,rss,comm -r | head -11 > "$OUT/top-r$r-$w-$th.txt"
      touch "$OUT/.sampling"; echo 0 > "$OUT/.cpumax"; sample_cpu & SP=$!
      docker exec "$CONTAINER" sysbench $w $COMMON $PS --threads=$th --time=60 --report-interval=10 run > "$OUT/run-r$r-$w-$th.log" 2>&1
      st=ok; grep -q FATAL "$OUT/run-r$r-$w-$th.log" && st=failed
      rm -f "$OUT/.sampling"; wait $SP 2>/dev/null
      la=$(sysctl -n vm.loadavg | awk '{print $2}')
      mp=$(memory_pressure 2>/dev/null | awk -F': ' '/System-wide memory free percentage/{print $2}')
      q=$(awk '/queries:/{gsub(/[()]/,"",$3); print $3}' "$OUT/run-r$r-$w-$th.log")
      a=$(awk '/avg:/{print $2; exit}' "$OUT/run-r$r-$w-$th.log")
      p=$(awk '/95th percentile:/{print $3; exit}' "$OUT/run-r$r-$w-$th.log")
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$r" "$w" "$th" "$st" "$q" "$a" "$p" "$lb" "$la" "$(cat $OUT/.cpumax)" "$mp" >> "$T"
    done
  done
done
kill -9 $PID; wait $PID 2>/dev/null
start_server
RESTART=$ELAPSED
echo "restart_after_kill_ms=$RESTART pid=$PID" >> "$OUT/lifecycle.txt"
kill -9 $PID; wait $PID 2>/dev/null
ls -la "$BIN" >> "$OUT/lifecycle.txt"
rm -f "$OUT/.cpumax"
echo DONE >> "$OUT/lifecycle.txt"
