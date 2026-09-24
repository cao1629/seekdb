#!/usr/bin/env python3
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

import argparse
from collections import namedtuple
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time


DESCRIPTION = (
    "Run the judge's memory-budget scenarios (PLAN.md section 4, family 12) "
    "against one seekdb binary and record them in the recording format of "
    "mysqltest_for_seekdb.py, so that two recordings can be compared with its "
    "compare subcommand."
)
REPO_ROOT = Path(__file__).resolve().parents[4]
RUNNER_PATH = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
SDB_PATH = RUNNER_PATH.with_name("sdb.py")
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
RECORDER = "migration/judge/families/memory/memory_scenarios.py"
HOST = "127.0.0.1"
DATABASE = "test"
SQL_TIMEOUT = 1200
KILL_EXIT_TIMEOUT = 20
STDERR_TAIL_LINES = 20
RSS_SAMPLE_INTERVAL = 2.0
PS_TIMEOUT = 5
MB = 1 << 20
MEMORY_BUDGET_BYTES = 1 << 30
SERVER_PARAMETERS = ("memory_budget=1G", "cpu_count=4")
QUERY_TIMEOUT_US = 900000000
WORK_AREA_SETTLE_SECONDS = 10
CHECK_CACHE_SETTLE_SECONDS = 1
QUERY_MEMORY_LIMIT_PERCENTAGE = 1
QUERY_MEMORY_LIMIT_BYTES = MEMORY_BUDGET_BYTES // 100 * QUERY_MEMORY_LIMIT_PERCENTAGE
MEMSTORE_REPLAY_RESERVE_MB = 100
MEMSTORE_MARGIN_MB = 16
MEMSTORE_MIN_CHUNKS = 6
MEMSTORE_MAX_CHUNKS = 128
MEMSTORE_CHUNK_PAUSE_SECONDS = 0.2
MEMSTORE_REFUSAL_SLACK_MB = 8
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
CLIENT_ERROR_PATTERN = re.compile(
    r"^ERROR (\d+) \(([0-9A-Za-z]{5})\)(?: at line \d+)?: (.*)$"
)
NOT_RECORDED = "<not recorded>"
NAMED_VARYING_VALUES = {
    11049: (("mem_hold", re.compile(r"(mem_hold=)(\d+)")),),
}
ERROR_ALLOCATE_MEMORY_FAILED = 4013
ERROR_SERVER_RUNTIME_OUT_OF_MEM = 4030
ERROR_EXCEED_QUERY_MEM_LIMIT = 11049

ClientError = namedtuple("ClientError", ("code", "sqlstate", "message", "values"))


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "mysqltest_for_seekdb", str(RUNNER_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


class ScenarioFailed(Exception):
    pass


class StopRequested(Exception):
    pass


class StopState(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.signal_number = None
        self.deferred = True

    def message(self):
        return "stopped by signal {}".format(self.signal_number)


STOP = StopState()


def request_stop(signal_number, frame):
    if STOP.signal_number is not None:
        return
    STOP.signal_number = signal_number
    if not STOP.deferred:
        raise StopRequested(STOP.message())


def allow_stop():
    STOP.deferred = False
    if STOP.signal_number is not None:
        raise StopRequested(STOP.message())


def install_stop_handlers():
    STOP.reset()
    for signal_number in STOP_SIGNALS:
        signal.signal(signal_number, request_stop)


class Recording(object):
    def __init__(self):
        self.content = bytearray()
        self.problems = []

    def line(self, text):
        self.content += text.encode("utf-8") + b"\n"

    def append(self, data):
        self.content += data

    def check(self, description, holds):
        self.line("-- check: {}: {}".format(description, "yes" if holds else "no"))
        if not holds:
            self.problems.append("check failed: {}".format(description))
        return holds


def client_command(args, options):
    return [
        str(args.obclient),
        "-h",
        HOST,
        "-P",
        str(args.port),
        "-uroot",
        "-A",
        "-c",
        "-D{}".format(DATABASE),
    ] + list(options)


def run_client(args, sql, options, timeout=SQL_TIMEOUT):
    command = client_command(args, options)
    try:
        result = subprocess.run(
            command,
            input=sql.encode("utf-8") + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        message = "obclient did not finish within {} seconds\n".format(timeout)
        return None, b"", message.encode("utf-8")
    except OSError as exc:
        raise runner.RunnerError("cannot run obclient: {}".format(exc))
    return result.returncode, result.stdout, result.stderr


def statement_lines(step):
    return [step] if isinstance(step, str) else list(step)


def tail_lines(data):
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return b"".join(line + b"\n" for line in lines[-STDERR_TAIL_LINES:])


def fail_statement(recording, statements, code, output, error_output):
    recording.append(output)
    recording.append(tail_lines(error_output))
    recording.line("-- obclient exit code {}".format(code))
    raise ScenarioFailed("statement failed: {}".format(" ".join(statements)))


def execute(args, recording, step):
    statements = statement_lines(step)
    for statement in statements:
        recording.line(statement)
    code, output, error_output = run_client(args, "\n".join(statements), ("--table",))
    if code != 0:
        fail_statement(recording, statements, code, output, error_output)
    recording.append(output)
    return output


def execute_digest(args, recording, statement):
    recording.line(statement)
    code, output, error_output = run_client(args, statement, ("-N", "-s"))
    if code != 0:
        fail_statement(recording, [statement], code, output, error_output)
    recording.line(
        "-- output: {} lines, sha256 {} (the lines themselves are not recorded)".format(
            output.count(b"\n"), hashlib.sha256(output).hexdigest()
        )
    )
    return output


def table_rows(output):
    rows = []
    for line in output.decode("utf-8", "replace").split("\n"):
        if line.startswith("|") and line.endswith("|"):
            rows.append([cell.strip() for cell in line[1:-1].split("|")])
    return rows[:1], rows[1:]


def execute_table(args, recording, statement):
    output = execute(args, recording, statement)
    header, rows = table_rows(output)
    return (header[0] if header else None), rows


def query_rows(args, sql):
    code, output, _ = run_client(args, sql, ("-N", "-s"))
    if code != 0:
        return None
    lines = output.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.split("\t") for line in lines]


def canonical_message(code, message):
    values = {}
    for name, pattern in NAMED_VARYING_VALUES.get(code, ()):
        match = pattern.search(message)
        if match is not None:
            values[name] = int(match.group(2))
            message = message[: match.start(2)] + NOT_RECORDED + message[match.end(2) :]
    return message, values


def parse_client_error(error_output):
    text = error_output.decode("utf-8", "replace")
    if not text.endswith("\n") or text.count("\n") != 1:
        return None
    match = CLIENT_ERROR_PATTERN.match(text[:-1])
    if match is None:
        return None
    code = int(match.group(1))
    message, values = canonical_message(code, match.group(3))
    return ClientError(code, match.group(2), message, values)


def report_not_recorded(args, values):
    for name in sorted(values):
        print(
            "not recorded: {} {}={}".format(args.scenario_name, name, values[name]),
            flush=True,
        )


def expect_error(args, recording, statement, expected_code):
    recording.line(statement)
    code, output, error_output = run_client(args, statement, ("--table",))
    recording.append(output)
    if code == 0:
        recording.line("-- the statement succeeded")
        recording.check("the statement failed with error {}".format(expected_code), False)
        raise ScenarioFailed("statement succeeded: {}".format(statement))
    error = parse_client_error(error_output)
    if error is None:
        fail_statement(recording, [statement], code, b"", error_output)
    recording.line(
        "ERROR {} ({}): {}".format(error.code, error.sqlstate, error.message)
    )
    report_not_recorded(args, error.values)
    if not recording.check(
        "the statement failed with error {}".format(expected_code),
        error.code == expected_code,
    ):
        raise ScenarioFailed(
            "statement failed with {} instead of {}: {}".format(
                error.code, expected_code, statement
            )
        )
    return error


def wait(recording, seconds, reason):
    time.sleep(seconds)
    recording.line("-- waited {} seconds: {}".format(seconds, reason))


def sdb(command, arguments, description):
    runner.run_sdb(SDB_PATH, command, arguments, description, REPO_ROOT)


def start_server(args):
    arguments = [
        "--binary",
        args.seekdb,
        "--base-dir",
        args.base_dir,
        "--port",
        args.port,
        "--nodaemon",
    ]
    for parameter in SERVER_PARAMETERS:
        arguments += ["--parameter", parameter]
    sdb("start", arguments, "start seekdb")


def wait_ready(args):
    sdb(
        "wait-ready",
        (
            "--client",
            args.obclient,
            "--base-dir",
            args.base_dir,
            "--host",
            HOST,
            "--port",
            args.port,
            "--user",
            "root",
            "--timeout",
            runner.READY_TIMEOUT,
        ),
        "wait for seekdb",
    )


def instance_pid(args):
    sdb_module = runner.load_sdb_module(SDB_PATH)
    base_dir = sdb_module._base_dir(str(args.base_dir))
    try:
        binary = sdb_module.read_instance_binary(base_dir)
        return sdb_module.inspect_instance_process(base_dir, binary)
    except (OSError, RuntimeError, ValueError):
        return None


def server_running(args):
    return instance_pid(args) is not None


def kill_process(pid):
    sdb_module = runner.load_sdb_module(SDB_PATH)
    print("+ kill -KILL {}".format(pid), flush=True)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "seekdb pid={} was gone when SIGKILL was sent".format(pid)
    except OSError as exc:
        return "cannot send SIGKILL to seekdb pid={}: {}".format(pid, exc)
    if not sdb_module.wait_process_exit(pid, KILL_EXIT_TIMEOUT):
        return "seekdb pid={} did not exit within {} seconds of SIGKILL".format(
            pid, KILL_EXIT_TIMEOUT
        )
    return None


def kill_server(args):
    pid = instance_pid(args)
    if pid is None:
        return None
    return kill_process(pid)


def lifecycle_line(done, failed_step):
    steps = list(done)
    if failed_step is not None:
        steps.append("{} failed".format(failed_step))
    return "-- {}".format(", ".join(steps))


def bring_up(args, recording, scenario):
    recording.line(
        "-- start with {}".format(
            " ".join("--parameter {}".format(item) for item in SERVER_PARAMETERS)
        )
    )
    done = []
    for step, action in (
        ("start", lambda: start_server(args)),
        ("ready", lambda: wait_ready(args)),
        ("init", lambda: runner.execute_init_sql(args, DEPLOY_DIR, scenario)),
    ):
        try:
            action()
        except runner.RunnerError:
            recording.line(lifecycle_line(done, step))
            raise
        done.append(step)
    recording.line(lifecycle_line(done, None))


class RssSampler(object):
    def __init__(self, base_dir):
        self.pid_file = base_dir / "run" / "seekdb.pid"
        self.peak_kb = None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stopped.wait(RSS_SAMPLE_INTERVAL):
            self.sample()

    def sample(self):
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
            result = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=PS_TIMEOUT,
                check=False,
            )
            rss_kb = int(result.stdout.decode("ascii", "replace").strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return
        if self.peak_kb is None or rss_kb > self.peak_kb:
            self.peak_kb = rss_kb

    def stop(self):
        self.stopped.set()
        self.thread.join()
        self.sample()


SEQ_SETUP = (
    "create table t_digit (d int not null, primary key (d));",
    "insert into t_digit values (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);",
    "create table t_seq (n int not null, primary key (n));",
    "insert into t_seq select a.d * 100 + b.d * 10 + c.d "
    "from t_digit a, t_digit b, t_digit c;",
    "select count(*), min(n), max(n) from t_seq;",
)


def common_setup(args, recording):
    execute(
        args,
        recording,
        "set global ob_query_timeout = {};".format(QUERY_TIMEOUT_US),
    )
    _, rows = execute_table(args, recording, "select @@ob_query_timeout;")
    if not recording.check(
        "a new session gets ob_query_timeout {}".format(QUERY_TIMEOUT_US),
        rows == [[str(QUERY_TIMEOUT_US)]],
    ):
        raise ScenarioFailed("the global ob_query_timeout did not reach new sessions")
    for step in SEQ_SETUP:
        execute(args, recording, step)


SpillQuery = namedtuple("SpillQuery", ("name", "sql", "plan_pattern", "digest"))
SPILL_SETUP = (
    "create table t_spill (id int not null, k int not null, "
    "pad varchar(120) not null, primary key (id));",
    "insert into t_spill select a.n * 1000 + b.n + 1, "
    "((a.n * 1000 + b.n) * 7919) % 200003, "
    "concat(lpad(((a.n * 1000 + b.n) * 7919) % 200003, 8, '0'), repeat('p', 100)) "
    "from t_seq a, t_seq b where a.n < 200;",
    "select count(*), min(length(pad)), max(length(pad)), sum(id), sum(k) from t_spill;",
)
SPILL_QUERIES = (
    SpillQuery(
        "sort",
        "select id from t_spill order by pad, id;",
        "%from t_spill order by pad, id",
        True,
    ),
    SpillQuery(
        "hash group by",
        "select count(*), sum(c), sum(s), sum(crc32(g)) from "
        "(select /*+ use_hash_aggregation */ pad as g, count(*) as c, sum(id) as s "
        "from t_spill group by pad) x;",
        "%group by pad) x",
        False,
    ),
    SpillQuery(
        "hash join",
        "select /*+ leading(a b) use_hash(b) */ count(*), sum(a.id), sum(b.k) "
        "from t_spill a join t_spill b on a.pad = b.pad;",
        "%on a.pad = b.pad",
        False,
    ),
)
WORK_AREA_WAIT_REASON = (
    "the SQL memory manager recomputes the work area bound every 3 seconds"
)


def workarea_probe(plan_pattern):
    return (
        "select operation_type, sum(total_executions) as executions, "
        "sum(optimal_executions) as optimal, "
        "sum(onepass_executions + multipasses_executions) as spilled, "
        "max(max_tempseg_size) > 0 as dumped "
        "from oceanbase.V$SQL_WORKAREA where sql_id in "
        "(select sql_id from oceanbase.V$OB_PLAN_CACHE_PLAN_STAT "
        "where query_sql like '{0}' or statement like '{0}') "
        "group by operation_type, operation_id order by operation_id;".format(
            plan_pattern
        )
    )


def workarea_counts(rows):
    counts = []
    for row in rows:
        if len(row) != 5:
            return None
        try:
            counts.append((row[0],) + tuple(int(cell) for cell in row[1:]))
        except ValueError:
            return None
    return counts


def run_spill_query(args, recording, query):
    recording.line("-- {}".format(query.name))
    if query.digest:
        output = execute_digest(args, recording, query.sql)
    else:
        output = execute(args, recording, query.sql)
    _, rows = execute_table(args, recording, workarea_probe(query.plan_pattern))
    return output, workarea_counts(rows)


def scenario_work_area_spill(args, recording):
    common_setup(args, recording)
    for step in SPILL_SETUP:
        execute(args, recording, step)
    execute(args, recording, "set global ob_sql_work_area_percentage = 5;")
    wait(recording, WORK_AREA_SETTLE_SECONDS, WORK_AREA_WAIT_REASON)
    limited = []
    for query in SPILL_QUERIES:
        output, counts = run_spill_query(args, recording, query)
        limited.append((output, counts))
        recording.check(
            "{}: V$SQL_WORKAREA lists the query's work area operators, "
            "each with one execution".format(query.name),
            bool(counts) and all(count[1] == 1 for count in counts),
        )
        recording.check(
            "{}: the run at 5% spilled (a one-pass or multi-pass execution)".format(
                query.name
            ),
            bool(counts) and any(count[3] >= 1 for count in counts),
        )
    execute(args, recording, "set global ob_sql_work_area_percentage = 100;")
    wait(recording, WORK_AREA_SETTLE_SECONDS, WORK_AREA_WAIT_REASON)
    for query, (limited_output, limited_counts) in zip(SPILL_QUERIES, limited):
        output, counts = run_spill_query(args, recording, query)
        recording.check(
            "{}: the run at 100% returned the same bytes as the run at 5%".format(
                query.name
            ),
            output == limited_output,
        )
        recording.check(
            "{}: the run at 100% did not spill (each operator's new execution "
            "was optimal)".format(query.name),
            bool(counts)
            and bool(limited_counts)
            and len(counts) == len(limited_counts)
            and all(
                now[0] == before[0]
                and now[1] == before[1] + 1
                and now[2] == before[2] + 1
                and now[3] == before[3]
                for now, before in zip(counts, limited_counts)
            ),
        )
    execute(args, recording, "select count(*) from t_spill;")


QUERY_MEMORY_SETUP = (
    "create table t_qm (id int not null, pad varchar(160) not null, "
    "primary key (id));",
    "insert into t_qm select a.n * 1000 + b.n + 1, "
    "concat(lpad(a.n * 1000 + b.n, 8, '0'), repeat('q', 150)) "
    "from t_seq a, t_seq b where a.n < 200;",
    "select count(*), min(length(pad)), max(length(pad)) from t_qm;",
)
QUERY_MEMORY_QUERY = (
    "select count(*), sum(c) from (select /*+ use_hash_aggregation */ pad, "
    "count(*) as c from t_qm group by pad) x;"
)


def scenario_query_memory_limit(args, recording):
    common_setup(args, recording)
    for step in QUERY_MEMORY_SETUP:
        execute(args, recording, step)
    execute(args, recording, "set global ob_sql_work_area_percentage = 100;")
    wait(recording, WORK_AREA_SETTLE_SECONDS, WORK_AREA_WAIT_REASON)
    execute(
        args,
        recording,
        "alter system set query_memory_limit_percentage = {};".format(
            QUERY_MEMORY_LIMIT_PERCENTAGE
        ),
    )
    error = expect_error(
        args, recording, QUERY_MEMORY_QUERY, ERROR_EXCEED_QUERY_MEM_LIMIT
    )
    recording.line(
        "-- mem_hold is the request's memory hold at the check that failed; it "
        "follows allocator block sizes and thread-local page caches, so it is not "
        "recorded"
    )
    recording.check(
        "the message names mem_limit={}, {}% of memory_budget".format(
            QUERY_MEMORY_LIMIT_BYTES, QUERY_MEMORY_LIMIT_PERCENTAGE
        ),
        "mem_limit={},".format(QUERY_MEMORY_LIMIT_BYTES) in error.message,
    )
    recording.check(
        "the message's mem_hold (not recorded) is at least its mem_limit",
        error.values.get("mem_hold", -1) >= QUERY_MEMORY_LIMIT_BYTES,
    )
    execute(args, recording, "select count(*) from t_qm;")
    execute(args, recording, "alter system set query_memory_limit_percentage = 32;")
    execute(args, recording, QUERY_MEMORY_QUERY)


HASH_JOIN_SETUP = (
    "create table t_hj_build (id int not null, k int not null, "
    "pad varchar(160) not null, primary key (id));",
    "insert into t_hj_build select a.n * 1000 + b.n + 1, 1, repeat('h', 150) "
    "from t_seq a, t_seq b where a.n < 150;",
    "create table t_hj_probe (id int not null, k int not null, primary key (id));",
    "insert into t_hj_probe values (1, 1), (2, 2);",
    "select count(*), count(distinct k), min(length(pad)) from t_hj_build;",
)
HASH_JOIN_DEEP_QUERY = (
    "select /*+ leading(a b) use_hash(b) */ count(*), sum(length(a.pad)) "
    "from t_hj_build a full outer join t_hj_probe b on a.k = b.k;"
)
HASH_JOIN_SHALLOW_QUERY = (
    "select /*+ leading(b a) use_hash(a) */ count(*), sum(length(a.pad)) "
    "from t_hj_build a full outer join t_hj_probe b on a.k = b.k;"
)
HASH_JOIN_DEEP_PATTERN = (
    "%leading(a b) use_hash(b)%from t_hj_build a full outer join t_hj_probe b "
    "on a.k = b.k"
)
HASH_JOIN_WORKAREA_PROBE = (
    "select operation_type, total_executions, optimal_executions, "
    "onepass_executions, multipasses_executions, last_execution, "
    "max_tempseg_size > 0 as dumped from oceanbase.V$SQL_WORKAREA "
    "where sql_id in (select sql_id from oceanbase.V$OB_PLAN_CACHE_PLAN_STAT "
    "where query_sql like '{0}' or statement like '{0}') "
    "order by operation_id;".format(HASH_JOIN_DEEP_PATTERN)
)


def scenario_hash_join_depth(args, recording):
    common_setup(args, recording)
    for step in HASH_JOIN_SETUP:
        execute(args, recording, step)
    execute(args, recording, "set global ob_sql_work_area_percentage = 5;")
    wait(recording, WORK_AREA_SETTLE_SECONDS, WORK_AREA_WAIT_REASON)
    expect_error(
        args, recording, HASH_JOIN_DEEP_QUERY, ERROR_ALLOCATE_MEMORY_FAILED
    )
    _, rows = execute_table(args, recording, HASH_JOIN_WORKAREA_PROBE)
    listed = len(rows) == 1 and len(rows[0]) == 7
    recording.check(
        "V$SQL_WORKAREA lists the failed join's work area once, with one "
        "execution that wrote to temp files",
        listed and rows[0][1] == "1" and rows[0][6] == "1",
    )
    recording.check(
        "that execution was multi-pass: the join dumped rows again after "
        "splitting dumped rows, so its recursion went past the first level "
        "before the error",
        listed and rows[0][4] == "1" and rows[0][5] == "MULTI-PASS",
    )
    execute(args, recording, "select count(*) from t_hj_build;")
    execute(args, recording, HASH_JOIN_SHALLOW_QUERY)


MEMSTORE_WAIT_REASON = (
    "the memstore-full check keeps its answer for 100 ms per worker thread"
)
MEMSTORE_BELOW_RESERVE_SETUP = (
    "create table t_ms (id int not null, pad varchar(100) not null, "
    "primary key (id));",
    "insert into t_ms values (1, 'written before the limit');",
    "alter system set writing_throttling_trigger_percentage = 100;",
)
MEMSTORE_REFUSED_WRITES = (
    "insert into t_ms values (2, 'refused');",
    "update t_ms set pad = 'refused' where id = 1;",
    "delete from t_ms where id = 1;",
)
MEMSTORE_QUERY = (
    "select memstore_used, memstore_limit from oceanbase.__all_virtual_memstore_info;"
)
MEMSTORE_READ_REASON = "the memstore used by inner tables varies"


def memstore_state(args):
    rows = query_rows(args, MEMSTORE_QUERY)
    if not rows or len(rows) != 1 or len(rows[0]) != 2:
        return None
    try:
        return int(rows[0][0]), int(rows[0][1])
    except ValueError:
        return None


def read_memstore(args, recording):
    recording.line(
        "-- read, output not recorded because {}: {}".format(
            MEMSTORE_READ_REASON, MEMSTORE_QUERY
        )
    )
    state = memstore_state(args)
    if state is None:
        raise ScenarioFailed("cannot read {}".format(MEMSTORE_QUERY))
    return state


def scenario_memstore_below_reserve(args, recording):
    common_setup(args, recording)
    for step in MEMSTORE_BELOW_RESERVE_SETUP:
        execute(args, recording, step)
    used, _ = read_memstore(args, recording)
    report_not_recorded(args, {"memstore_used": used})
    execute(args, recording, "alter system set memstore_memory_limit = '64M';")
    for statement in MEMSTORE_REFUSED_WRITES:
        wait(recording, CHECK_CACHE_SETTLE_SECONDS, MEMSTORE_WAIT_REASON)
        expect_error(args, recording, statement, ERROR_SERVER_RUNTIME_OUT_OF_MEM)
    execute(args, recording, "select id, pad from t_ms order by id;")
    execute(args, recording, "alter system set memstore_memory_limit = '0M';")
    wait(recording, CHECK_CACHE_SETTLE_SECONDS, MEMSTORE_WAIT_REASON)
    execute(
        args,
        recording,
        "insert into t_ms values (3, 'written after the limit was restored');",
    )
    execute(args, recording, "select id, pad from t_ms order by id;")


MEMSTORE_FILL_SETUP = (
    "create table t_fill (id int not null, pad varchar(1000) not null, "
    "primary key (id));",
    "alter system set writing_throttling_trigger_percentage = 100;",
    "alter system set freeze_trigger_percentage = 99;",
)
MEMSTORE_CHUNK = (
    "insert into t_fill select {} + n, repeat('m', 1000) from t_seq;"
)


def scenario_memstore_fill(args, recording):
    common_setup(args, recording)
    for step in MEMSTORE_FILL_SETUP:
        execute(args, recording, step)
    used, _ = read_memstore(args, recording)
    limit_mb = (used + MB - 1) // MB + MEMSTORE_REPLAY_RESERVE_MB + MEMSTORE_MARGIN_MB
    user_limit = (limit_mb - MEMSTORE_REPLAY_RESERVE_MB) * MB
    recording.line(
        "-- alter system set memstore_memory_limit = '<n>M'; with n, not recorded, "
        "the memstore_used above rounded up to MB plus {} MB (the replay reserve) "
        "plus {} MB".format(MEMSTORE_REPLAY_RESERVE_MB, MEMSTORE_MARGIN_MB)
    )
    statement = "alter system set memstore_memory_limit = '{}M';".format(limit_mb)
    code, output, error_output = run_client(args, statement, ("--table",))
    if code != 0:
        fail_statement(recording, [statement], code, output, error_output)
    recording.append(output)
    recording.line(
        "-- chunks of 1000 rows of 1000 bytes, for i = 0, 1, 2, ... until one is "
        "refused, at most {}, each after a {} s pause, longer than the 100 ms a "
        "worker thread keeps a 'not full' answer; the number of accepted chunks is "
        "not recorded: {}".format(
            MEMSTORE_MAX_CHUNKS,
            MEMSTORE_CHUNK_PAUSE_SECONDS,
            MEMSTORE_CHUNK.format("1000 * i"),
        )
    )
    accepted = 0
    error = None
    for index in range(MEMSTORE_MAX_CHUNKS):
        time.sleep(MEMSTORE_CHUNK_PAUSE_SECONDS)
        chunk = MEMSTORE_CHUNK.format(1000 * index)
        code, output, error_output = run_client(args, chunk, ("--table",))
        if code == 0:
            accepted += 1
            continue
        error = parse_client_error(error_output)
        if error is None:
            fail_statement(recording, [chunk], code, output, error_output)
        break
    after = memstore_state(args) if error is not None else None
    values = {
        "memstore_used": used,
        "memstore_memory_limit_mb": limit_mb,
        "accepted_chunks": accepted,
    }
    if after is not None:
        values["memstore_used_after_refusal"] = after[0]
        values["memstore_limit_after_refusal"] = after[1]
        values["used_after_refusal_above_limit_minus_reserve"] = after[0] - user_limit
    report_not_recorded(args, values)
    recording.check(
        "at least {} chunks were accepted".format(MEMSTORE_MIN_CHUNKS),
        accepted >= MEMSTORE_MIN_CHUNKS,
    )
    if not recording.check(
        "a chunk was refused within {} chunks".format(MEMSTORE_MAX_CHUNKS),
        error is not None,
    ):
        raise ScenarioFailed("no chunk was refused")
    recording.line(
        "ERROR {} ({}): {}".format(error.code, error.sqlstate, error.message)
    )
    if not recording.check(
        "the refused chunk failed with error {}".format(ERROR_SERVER_RUNTIME_OUT_OF_MEM),
        error.code == ERROR_SERVER_RUNTIME_OUT_OF_MEM,
    ):
        raise ScenarioFailed("the refused chunk failed with {}".format(error.code))
    recording.line(
        "-- read right after the refused chunk, output not recorded because it "
        "follows the memstore used above: {}".format(MEMSTORE_QUERY)
    )
    if after is None:
        raise ScenarioFailed("cannot read {}".format(MEMSTORE_QUERY))
    recording.check(
        "memstore_limit is the n MB set above", after[1] == limit_mb * MB
    )
    recording.check(
        "memstore_used is above memstore_limit minus the {} MB reserve, by at most "
        "{} MB".format(MEMSTORE_REPLAY_RESERVE_MB, MEMSTORE_REFUSAL_SLACK_MB),
        0 < after[0] - user_limit <= MEMSTORE_REFUSAL_SLACK_MB * MB,
    )
    wait(recording, CHECK_CACHE_SETTLE_SECONDS, MEMSTORE_WAIT_REASON)
    expect_error(
        args,
        recording,
        "update t_fill set pad = 'refused' where id = 0;",
        ERROR_SERVER_RUNTIME_OUT_OF_MEM,
    )
    execute(args, recording, "select count(*) > 0 as has_rows from t_fill;")
    execute(args, recording, "alter system set memstore_memory_limit = '0M';")
    wait(recording, CHECK_CACHE_SETTLE_SECONDS, MEMSTORE_WAIT_REASON)
    execute(
        args,
        recording,
        "insert into t_fill values (-1, 'written after the limit was restored');",
    )
    execute(args, recording, "select id, pad from t_fill where id < 0;")


VECTOR_MODULI = (7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67)


def vector_expression(number):
    parts = ", ',', ".join("({}) % {}".format(number, modulus) for modulus in VECTOR_MODULI)
    return "concat('[', {}, ']')".format(parts)


VECTOR_SETUP = (
    "create table t_vec (id int not null, v vector({}), primary key (id), "
    "vector index vidx(v) with (distance=l2, type=hnsw, lib=vsag));".format(
        len(VECTOR_MODULI)
    ),
    "insert into t_vec select n + 1, {} from t_seq where n < 10;".format(
        vector_expression("n")
    ),
    "select count(*) from t_vec;",
)
VECTOR_REFUSED_INSERT = (
    "insert into t_vec select a.n * 1000 + b.n + 11, {} "
    "from t_seq a, t_seq b where a.n < 20;".format(vector_expression("a.n * 1000 + b.n"))
)


def scenario_vector_limit(args, recording):
    common_setup(args, recording)
    for step in VECTOR_SETUP:
        execute(args, recording, step)
    execute(args, recording, "alter system set vector_memory_limit = '1K';")
    wait(
        recording,
        CHECK_CACHE_SETTLE_SECONDS,
        "the vector memory limit is read at each checked allocation",
    )
    expect_error(args, recording, VECTOR_REFUSED_INSERT, ERROR_ALLOCATE_MEMORY_FAILED)
    execute(args, recording, "select count(*) from t_vec;")


SCENARIOS = (
    ("work_area_spill", scenario_work_area_spill),
    ("query_memory_limit", scenario_query_memory_limit),
    ("hash_join_depth", scenario_hash_join_depth),
    ("memstore_below_reserve", scenario_memstore_below_reserve),
    ("memstore_fill", scenario_memstore_fill),
    ("vector_limit", scenario_vector_limit),
)
SCENARIO_NAMES = tuple(name for name, _ in SCENARIOS)


def write_recorded_file(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(str(temporary), str(path))


def join_errors(first, second):
    return "{}; {}".format(first, second) if first else second


def run_scenario(args, name, function):
    recording = Recording()
    recording.line("-- {}".format(name))
    recording.line("-- recorder sha256 {}".format(args.recorder_sha256))
    args.scenario_name = name
    error = None
    print("[ RUN      ] {}".format(name), flush=True)
    started = time.monotonic()
    sampler = RssSampler(args.base_dir)
    sampler.start()
    try:
        allow_stop()
        bring_up(args, recording, name)
        function(args, recording)
        recording.check(
            "seekdb is still running at the end of the scenario",
            server_running(args),
        )
    except ScenarioFailed as exc:
        recording.problems.append(str(exc))
    except Exception as exc:
        error = "{}: {}".format(name, exc)
    finally:
        STOP.deferred = True
        sampler.stop()
        kill_problem = kill_server(args)
        if kill_problem is not None:
            error = join_errors(error, "{}: {}".format(name, kill_problem))
        runner.save_instance_outputs(args, SDB_PATH, name)
        cleanup_error = runner.destroy_instance(
            SDB_PATH, args.base_dir, REPO_ROOT, check=False
        )
        if cleanup_error:
            error = join_errors(error, cleanup_error)
    passed = error is None and not recording.problems
    suffix = ".result" if passed else ".partial"
    write_recorded_file(args.record_dir / (name + suffix), bytes(recording.content))
    problems = list(recording.problems)
    if error is not None:
        problems.append(error)
    elapsed = time.monotonic() - started
    if passed:
        print("[       OK ] {} ({:.3f}s)".format(name, elapsed), flush=True)
    else:
        print("[  FAILED  ] {} ({:.3f}s)".format(name, elapsed), flush=True)
        for problem in problems:
            print("  {}".format(problem), flush=True)
    outcome = {
        "exit_code": 0 if passed else 1,
        "recorded": passed,
        "partial": not passed,
        "problems": problems,
        "elapsed_seconds": round(elapsed, 1),
        "peak_rss_kb": sampler.peak_kb,
    }
    return outcome, error


def write_manifest(args, scenarios):
    manifest_path = args.record_dir / "manifest.json"
    try:
        manifest_path.open("x").close()
    except FileExistsError:
        raise runner.RunnerError(
            "record directory is already in use: {}".format(manifest_path)
        )
    obclient_sha256 = runner.file_sha256(args.obclient)
    args.manifest = {
        "finished": False,
        "recorder": RECORDER,
        "seekdb": str(args.seekdb),
        "seekdb_sha256": runner.file_sha256(args.seekdb),
        "mysqltest": str(args.obclient),
        "mysqltest_sha256": obclient_sha256,
        "obclient": str(args.obclient),
        "obclient_sha256": obclient_sha256,
        "init_sql": str(args.init_sql),
        "init_sql_sha256": runner.file_sha256(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "init_user_sql_sha256": runner.file_sha256(args.init_user_sql),
        "sdb_sha256": runner.file_sha256(SDB_PATH),
        "runner_sha256": args.recorder_sha256,
        "mysqltest_runner_sha256": runner.file_sha256(RUNNER_PATH),
        "repo_head": runner.stripped_or_none(
            runner.git_output(REPO_ROOT, ["rev-parse", "HEAD"])
        ),
        "tools_deploy_tree": runner.stripped_or_none(
            runner.git_output(REPO_ROOT, ["rev-parse", "HEAD:tools/deploy"])
        ),
        "tools_deploy_status": runner.git_output(
            REPO_ROOT, ["status", "--porcelain", "--", "tools/deploy"]
        ),
        "server_parameters": list(SERVER_PARAMETERS),
        "cases": list(scenarios),
        "slice_index": 0,
        "slice_count": 1,
        "case_list": None,
        "max_retries": 0,
        "fresh_instance_per_case": True,
        "work_dir": str(args.work_dir),
        "recorded": "<scenario>.result is the scenario's recording when it ran "
        "and every check held; otherwise <scenario>.partial is what it recorded "
        "up to the failure, and outcomes gives the exit code, the problems, the "
        "elapsed time and the server's peak resident set size",
    }
    runner.write_json(manifest_path, args.manifest)


def save_dir_source(args):
    if args.save_instance_dir:
        return "--save-instance-dir"
    if os.environ.get(runner.INSTANCE_SAVE_ENVIRONMENT):
        return "${}".format(runner.INSTANCE_SAVE_ENVIRONMENT)
    return None


def command_run(args):
    save_source = save_dir_source(args)
    args.seekdb = runner.absolute_path(args.seekdb)
    args.obclient = runner.absolute_path(args.obclient)
    args.base_dir = runner.absolute_path(args.base_dir)
    args.record_dir = runner.absolute_path(args.record_dir)
    args.save_instance_dir = runner.instance_save_dir(args)
    args.init_sql = (
        runner.absolute_path(args.init_sql)
        if args.init_sql
        else DEPLOY_DIR / "init.sql"
    )
    args.init_user_sql = (
        runner.absolute_path(args.init_user_sql)
        if args.init_user_sql
        else DEPLOY_DIR / "init_user.sql"
    )
    args.host = HOST
    args.manifest = None
    args.scenario_name = None
    args.recorder_sha256 = runner.file_sha256(Path(__file__).resolve())
    args.work_dir = Path(tempfile.mkdtemp(prefix="memory-scenarios-"))
    args.entry_gate = {
        "init_sql": str(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "attribution": "each file is piped to one obclient session; a "
        "statement's status is inferred from the 'ERROR ... at line N' lines on "
        "that session's stderr, N being the line where the client started the "
        "statement",
        "preparations": [],
    }
    requested = set(args.scenario or SCENARIO_NAMES)
    selected = [(name, function) for name, function in SCENARIOS if name in requested]
    print("work directory: {}".format(args.work_dir), flush=True)
    if args.save_instance_dir is not None:
        print(
            "instance save directory (from {}): {}".format(
                save_source, args.save_instance_dir
            ),
            flush=True,
        )

    outcomes = {}
    failed_cases = []
    error = None
    try:
        args.record_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(args, [name for name, _ in selected])
        for name, function in selected:
            if error is None and STOP.signal_number is not None:
                error = STOP.message()
            if error is not None:
                outcomes[name] = {
                    "exit_code": None,
                    "recorded": False,
                    "partial": False,
                    "problems": ["not run after an earlier error"],
                }
                continue
            outcome, scenario_error = run_scenario(args, name, function)
            outcomes[name] = outcome
            if outcome["exit_code"] != 0:
                failed_cases.append(name)
            if scenario_error is not None:
                error = scenario_error
    except Exception as exc:
        error = join_errors(error, str(exc))
        print("[memory][ERROR] {}".format(exc), file=sys.stderr)
    if error is None and STOP.signal_number is not None:
        error = STOP.message()

    success = not failed_cases and error is None and args.manifest is not None
    runner.write_entry_gate(args)
    if args.manifest is not None:
        args.manifest.update(
            {
                "success": success,
                "failed_cases": failed_cases,
                "retried_cases": {},
                "error": error,
                "init_failed_statements": runner.count_failed_init_statements(
                    args.entry_gate
                ),
                "outcomes": outcomes,
            }
        )
        args.manifest["finished"] = True
        try:
            runner.write_json(args.record_dir / "manifest.json", args.manifest)
        except OSError as exc:
            print(
                "[memory][ERROR] cannot finish the record manifest: {}".format(exc),
                file=sys.stderr,
            )
            success = False
    print(
        "memory scenarios finished: scenarios={}, failed={}, success={}".format(
            len(selected), len(failed_cases), success
        ),
        flush=True,
    )
    return 0 if success else 1


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--seekdb", required=True, help="seekdb executable")
    parser.add_argument("--obclient", required=True, help="obclient executable")
    parser.add_argument(
        "--base-dir",
        required=True,
        help="seekdb base directory, new or empty; every scenario starts a new "
        "instance in it and destroys it with sdb.py destroy afterwards",
    )
    parser.add_argument(
        "--record-dir",
        required=True,
        help="new or empty directory for manifest.json and one "
        "<scenario>.result (or .partial) per scenario",
    )
    parser.add_argument(
        "--port",
        type=runner.positive_int,
        required=True,
        help="the server's SQL port; required because judge runs share this machine",
    )
    parser.add_argument(
        "--init-sql",
        help="SQL file run in database oceanbase after each scenario's start; "
        "defaults to tools/deploy/init.sql",
    )
    parser.add_argument(
        "--init-user-sql",
        help="SQL file run in database test after --init-sql; "
        "defaults to tools/deploy/init_user.sql",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=SCENARIO_NAMES,
        help="run only this scenario; repeatable; scenarios always run in the "
        "order {}; default: all".format(", ".join(SCENARIO_NAMES)),
    )
    parser.add_argument(
        "--save-instance-dir",
        help="copy the instance's log/ (and seekdb*.profraw) here before every "
        "destroy, as the runner does; defaults to ${}".format(
            runner.INSTANCE_SAVE_ENVIRONMENT
        ),
    )
    return parser


def require_new_or_empty(parser, path, what):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        parser.error("{} must be new or empty: {}".format(what, path))


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    for path, what in ((args.seekdb, "--seekdb"), (args.obclient, "--obclient")):
        if not runner.absolute_path(path).is_file():
            parser.error("{} is not a file: {}".format(what, path))
    for path, what in (
        (args.init_sql, "--init-sql"),
        (args.init_user_sql, "--init-user-sql"),
    ):
        if path is not None and not runner.absolute_path(path).is_file():
            parser.error("{} is not a file: {}".format(what, path))
    require_new_or_empty(
        parser, runner.absolute_path(args.base_dir), "base directory"
    )
    require_new_or_empty(
        parser, runner.absolute_path(args.record_dir), "record directory"
    )
    save_dir = runner.instance_save_dir(args)
    if save_dir is not None:
        require_new_or_empty(
            parser,
            save_dir,
            "instance save directory (from {})".format(save_dir_source(args)),
        )
    install_stop_handlers()
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
