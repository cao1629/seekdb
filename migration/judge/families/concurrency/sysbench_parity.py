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
from collections import Counter
import hashlib
import os
import re
import subprocess
import sys

import recording_common as common


DESCRIPTION = (
    "Run sysbench oltp_point_select and oltp_read_write from the Docker "
    "container against one seekdb binary running natively, and record what two "
    "runs of the same binary reproduce, in the recording format of "
    "mysqltest_for_seekdb.py."
)
SYSBENCH_VERSION = "sysbench 1.0.20"
DATABASE = "sbtest"
SERVER_PARAMETERS = (
    "memory_limit=8G",
    "cpu_count=8",
    "system_memory=1G",
    "datafile_size=2G",
    "datafile_maxsize=4G",
    "log_disk_size=2G",
)
IGNORED_ERRORS = "1213,1020,1205"
CLIENT_FILES = (
    "/usr/bin/sysbench",
    "/usr/share/sysbench/oltp_common.lua",
    "/usr/share/sysbench/oltp_point_select.lua",
    "/usr/share/sysbench/oltp_read_write.lua",
)
DOCKER_TIMEOUT = 120
SYSBENCH_TIMEOUT = 3600
C_SHAPE = "^[0-9]{11}(-[0-9]{11}){9} *$"
PAD_SHAPE = "^[0-9]{11}(-[0-9]{11}){4} *$"
POINT_SELECT_EVENT = {"read": 1, "write": 0, "other": 0}
READ_WRITE_EVENT = {"read": 14, "write": 4, "other": 2}
REPORT_FIELDS = (
    ("read", re.compile(r"^\s+read:\s+(\d+)\s*$")),
    ("write", re.compile(r"^\s+write:\s+(\d+)\s*$")),
    ("other", re.compile(r"^\s+other:\s+(\d+)\s*$")),
    ("total", re.compile(r"^\s+total:\s+(\d+)\s*$")),
    ("transactions", re.compile(r"^\s+transactions:\s+(\d+)\s")),
    ("queries", re.compile(r"^\s+queries:\s+(\d+)\s")),
    ("ignored_errors", re.compile(r"^\s+ignored errors:\s+(\d+)\s")),
    ("reconnects", re.compile(r"^\s+reconnects:\s+(\d+)\s")),
    ("events", re.compile(r"^\s+total number of events:\s+(\d+)\s*$")),
)
IGNORED_ERROR_LINE = re.compile(r"^DEBUG: Ignoring error (\d+) ")
FATAL_LINE = re.compile(r"^FATAL: ")
FATAL_CODE = re.compile(r"(?:returned error|MySQL error:|^FATAL: error) (\d+)")


def table_names(args):
    return ["sbtest{}".format(number) for number in range(1, args.tables + 1)]


def checksum_statement(table):
    return (
        "select count(*) as row_count, "
        "sum(crc32(concat_ws('#', id, k, c, pad))) as crc32_sum, "
        "bit_xor(crc32(concat_ws('#', id, k, c, pad))) as crc32_xor "
        "from {};".format(table)
    )


def invariant_statement(table):
    return (
        "select count(*) as row_count, count(distinct id) as ids, "
        "min(id) as min_id, max(id) as max_id, "
        "sum(c not regexp '" + C_SHAPE + "') as bad_c, "
        "sum(pad not regexp '" + PAD_SHAPE + "') as bad_pad, "
        "min(k) >= 1 as k_at_least_1 from " + table + ";"
    )


def index_statements(table):
    number = table[len("sbtest"):]
    aggregate = "count(*), sum(crc32(concat_ws('#', id, k)))"
    return (
        "select /*+ index({0} k_{1}) */ {2} from {0} where k > 0;".format(
            table, number, aggregate
        ),
        "select /*+ full({0}) */ {1} from {0} where k > 0;".format(table, aggregate),
    )


def docker(args, command, timeout=DOCKER_TIMEOUT):
    full = [args.docker, "exec", args.container] + list(command)
    print("+ {}".format(common.runner.format_command(full)), flush=True)
    try:
        result = subprocess.run(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or b""
    except OSError as exc:
        raise common.runner.RunnerError("cannot run {}: {}".format(args.docker, exc))
    return result.returncode, result.stdout


def docker_text(args, command):
    code, output = docker(args, command)
    if code != 0:
        raise common.runner.RunnerError(
            "{} in container {} exited with {}: {}".format(
                " ".join(command),
                args.container,
                code,
                output.decode("utf-8", "replace").strip(),
            )
        )
    return output.decode("utf-8", "replace")


def library_paths(ldd_output):
    paths = []
    for line in ldd_output.split("\n"):
        text = line.strip()
        if "=>" in text:
            text = text.split("=>", 1)[1].strip()
        if text.startswith("/"):
            paths.append(text.split(" (", 1)[0].strip())
    return paths


def client_identity(args):
    version = docker_text(args, ["sysbench", "--version"]).strip()
    if version.split("\n")[0].strip() != SYSBENCH_VERSION:
        raise common.runner.RunnerError(
            "container {} runs {!r}, not {}".format(
                args.container, version, SYSBENCH_VERSION
            )
        )
    files = list(CLIENT_FILES) + library_paths(
        docker_text(args, ["ldd", "/usr/bin/sysbench"])
    )
    digests = {}
    for line in docker_text(args, ["sha256sum"] + files).split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2:
            digests[parts[1].strip()] = parts[0]
    missing = [path for path in files if path not in digests]
    if missing:
        raise common.runner.RunnerError(
            "no sha256 for {} in container {}".format(", ".join(missing), args.container)
        )
    listing = "".join(
        "{}  {}\n".format(digests[path], path) for path in sorted(digests)
    )
    return {
        "container": args.container,
        "version": SYSBENCH_VERSION,
        "files_sha256": digests,
        "digest": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
    }


def sysbench_options(args, workload, threads, command, events=None):
    shown = [
        workload,
        "--db-driver=mysql",
        "--mysql-user=root",
        "--mysql-db={}".format(DATABASE),
        "--tables={}".format(args.tables),
        "--table-size={}".format(args.table_size),
        "--rand-type=uniform",
    ]
    if workload == "oltp_read_write" and command == "run":
        shown.append("--db-ps-mode=disable")
    shown.append("--threads={}".format(threads))
    if command == "run":
        shown += [
            "--events={}".format(events),
            "--time=0",
            "--rand-seed={}".format(args.rand_seed),
            "--mysql-ignore-errors={}".format(IGNORED_ERRORS),
            "--verbosity=5",
        ]
    shown.append(command)
    hidden = [
        "--mysql-host={}".format(args.mysql_host),
        "--mysql-port={}".format(args.port),
    ]
    return shown, shown[:1] + hidden + shown[1:]


def parse_sysbench(output):
    text = output.decode("utf-8", "replace")
    report = {}
    ignored = Counter()
    fatal = []
    for line in text.split("\n"):
        for name, pattern in REPORT_FIELDS:
            match = pattern.match(line)
            if match and name not in report:
                report[name] = int(match.group(1))
        match = IGNORED_ERROR_LINE.match(line)
        if match:
            ignored[match.group(1)] += 1
        if FATAL_LINE.match(line):
            fatal.append(line)
    fatal_codes = sorted(
        set(code for line in fatal for code in FATAL_CODE.findall(line)), key=int
    )
    return {
        "report": report,
        "ignored_errors_by_code": dict(sorted(ignored.items(), key=lambda item: int(item[0]))),
        "fatal_lines": fatal[:20],
        "fatal_codes": fatal_codes,
    }


def run_sysbench(args, recording, name, workload, threads, command, events=None):
    shown, options = sysbench_options(args, workload, threads, command, events)
    recording.line("-- sysbench {}".format(" ".join(shown)))
    code, output = docker(args, ["sysbench"] + options, timeout=SYSBENCH_TIMEOUT)
    log_path = args.work_dir / "{}.sysbench.log".format(name)
    log_path.write_bytes(output)
    parsed = parse_sysbench(output)
    recording.details["sysbench"] = {
        "command": options,
        "exit_code": code,
        "log": str(log_path),
    }
    recording.details["sysbench"].update(parsed)
    if not recording.check(
        "sysbench exited with status 0 and printed no FATAL line",
        code == 0 and not parsed["fatal_lines"],
    ):
        recording.line(
            "-- sysbench exit code {}; error codes in its FATAL lines: {}".format(
                code, ", ".join(parsed["fatal_codes"]) or "none"
            )
        )
        recording.append(common.tail_lines(output))
        raise common.CaseFailed("sysbench {} {} failed".format(workload, command))
    return output, parsed


def check_events(recording, parsed, events):
    report = parsed["report"]
    return recording.check(
        "sysbench ran exactly {0} events, and reports {0} transactions".format(events),
        report.get("events") == events and report.get("transactions") == events,
    )


def ignored_errors_text(parsed):
    counts = parsed["ignored_errors_by_code"]
    return ", ".join("{}: {}".format(code, count) for code, count in counts.items()) or "none"


def record_report(recording, parsed, events, per_event):
    report = parsed["report"]
    recording.line(
        "-- sysbench report: {} transactions; queries: {} read, {} write, {} other, "
        "{} total; {} ignored errors, {} reconnects".format(
            report.get("transactions"),
            report.get("read"),
            report.get("write"),
            report.get("other"),
            report.get("total"),
            report.get("ignored_errors"),
            report.get("reconnects"),
        )
    )
    recording.line(
        "-- sysbench ignored errors by code: {}".format(ignored_errors_text(parsed))
    )
    check_events(recording, parsed, events)
    expected = dict((kind, count * events) for kind, count in per_event.items())
    expected["total"] = sum(expected.values())
    recording.check(
        "the query counts are {} times one event's statements ({}), with no "
        "ignored error and no reconnect".format(
            events,
            ", ".join("{} {}".format(per_event[kind], kind) for kind in per_event),
        ),
        all(report.get(kind) == count for kind, count in expected.items())
        and report.get("ignored_errors") == 0
        and not parsed["ignored_errors_by_code"]
        and report.get("reconnects") == 0,
    )


def parse_table(output):
    rows = []
    for line in output.decode("utf-8", "replace").split("\n"):
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    if len(rows) < 2:
        return None
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def record_checksums(args, recording):
    recording.line(
        "-- per-table checksum: the row count, and the sum and the xor of crc32 over "
        "every column of every row"
    )
    return b"".join(
        common.execute(args, recording, checksum_statement(table), DATABASE)
        for table in table_names(args)
    )


def record_invariants(args, recording):
    recording.line(
        "-- per-table values that hold under any interleaving of sysbench's "
        "transactions"
    )
    rows = []
    for table in table_names(args):
        parsed = parse_table(
            common.execute(args, recording, invariant_statement(table), DATABASE)
        )
        rows.append(parsed[0] if parsed and len(parsed) == 1 else {})
    size = str(args.table_size)
    recording.check(
        "every table has {0} rows, with ids 1 to {0}".format(size),
        all(
            row.get("row_count") == size
            and row.get("ids") == size
            and row.get("min_id") == "1"
            and row.get("max_id") == size
            for row in rows
        ),
    )
    recording.check(
        "every c and pad value has the shape of sysbench's template",
        all(row.get("bad_c") == "0" and row.get("bad_pad") == "0" for row in rows),
    )
    recording.check(
        "k is at least 1 in every row",
        all(row.get("k_at_least_1") == "1" for row in rows),
    )


def check_indexes(args, recording):
    differing = []
    for table in table_names(args):
        via_index, via_table = (
            common.query_rows(args, statement, DATABASE)
            for statement in index_statements(table)
        )
        if via_index is None or via_index != via_table:
            differing.append(table)
    if not recording.check(
        "in every table, reading id and k through the secondary index k_<n> gives "
        "the row count and crc32 sum a full scan gives",
        not differing,
    ):
        recording.line("-- differ: {}".format(", ".join(differing)))


def check_index_plan(args, recording):
    table = table_names(args)[0]
    code, output, _ = common.run_client(
        args, "explain " + index_statements(table)[0], ("-N", "-s"), DATABASE
    )
    recording.check(
        "the plan of the index statement for {} reads k_1 (the plan text is not "
        "recorded: its row and cost estimates can change with background work "
        "after prepare)".format(table),
        code == 0 and b"k_1" in output,
    )


def case_prepare(args, recording):
    common.execute(args, recording, "create database {};".format(DATABASE))
    output, _ = run_sysbench(args, recording, "prepare", "oltp_read_write", 1, "prepare")
    text = output.decode("utf-8", "replace")
    recording.check(
        "sysbench created the {} tables and their secondary indexes".format(
            args.tables
        ),
        all(
            "Creating table '{}'...".format(table) in text
            and "Creating a secondary index on '{}'...".format(table) in text
            for table in table_names(args)
        ),
    )
    record_invariants(args, recording)
    args.prepare_checksums = record_checksums(args, recording)
    check_index_plan(args, recording)
    check_indexes(args, recording)


def point_select_case(threads):
    def run(args, recording):
        name = "oltp_point_select_{}".format(threads)
        events = args.point_select_events
        _, parsed = run_sysbench(
            args, recording, name, "oltp_point_select", threads, "run", events
        )
        record_report(recording, parsed, events, POINT_SELECT_EVENT)
        checksums = record_checksums(args, recording)
        recording.check(
            "the tables are as prepare left them (the checksums are the prepare "
            "case's)",
            checksums == getattr(args, "prepare_checksums", None),
        )
        check_indexes(args, recording)

    return run


def read_write_case(threads):
    def run(args, recording):
        name = "oltp_read_write_{}".format(threads)
        events = args.read_write_events
        _, parsed = run_sysbench(
            args, recording, name, "oltp_read_write", threads, "run", events
        )
        record_report(recording, parsed, events, READ_WRITE_EVENT)
        if threads == 1:
            record_invariants(args, recording)
            record_checksums(args, recording)
        else:
            recording.line(
                "-- not recorded, because it depends on how the {} threads "
                "interleave: the table contents (no per-table checksum)".format(
                    threads
                )
            )
            record_invariants(args, recording)
        check_indexes(args, recording)

    return run


def setup(args):
    common.require_free_ports([args.port])
    common.start_server(args, SERVER_PARAMETERS)
    common.wait_ready(args)


def command_run(args):
    common.prepare_args(args, __file__, "sysbench-parity")
    args.threads = sorted(set(args.threads))
    try:
        args.client = client_identity(args)
    except common.runner.RunnerError as exc:
        print("[{}][ERROR] {}".format(args.log_name, exc), file=sys.stderr)
        return 1
    args.case_header = [
        "-- client: {}, sha256 of its binary, Lua scripts and libraries: {}".format(
            SYSBENCH_VERSION, args.client["digest"]
        ),
        "-- server parameters: {}".format(", ".join(SERVER_PARAMETERS)),
    ]
    cases = [("prepare", case_prepare)]
    cases += [
        ("oltp_point_select_{}".format(threads), point_select_case(threads))
        for threads in args.threads
    ]
    cases += [
        ("oltp_read_write_{}".format(threads), read_write_case(threads))
        for threads in args.threads
    ]
    recorded = (
        "<case>.result is the case's recording when sysbench succeeded and every "
        "check held; otherwise <case>.partial is what it recorded up to the "
        "failure; outcomes.<case>.details.sysbench holds sysbench's parsed "
        "report, the ignored errors by code, the FATAL lines and the path of its "
        "log; the table contents after a multi-thread oltp_read_write run depend "
        "on how its threads interleave and are not recorded"
    )
    extra = {
        "client": args.client,
        "server_parameters": list(SERVER_PARAMETERS),
        "sysbench": {
            "tables": args.tables,
            "table_size": args.table_size,
            "threads": args.threads,
            "point_select_events": args.point_select_events,
            "read_write_events": args.read_write_events,
            "rand_seed": args.rand_seed,
            "mysql_ignore_errors": IGNORED_ERRORS,
            "mysql_host": args.mysql_host,
        },
    }
    return common.run_family(args, cases, recorded, extra, setup)


def thread_counts(value):
    counts = []
    for item in value.split(","):
        number = common.runner.positive_int(item.strip())
        counts.append(number)
    if not counts:
        raise argparse.ArgumentTypeError("needs at least one thread count")
    return counts


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    common.add_server_arguments(parser)
    parser.add_argument(
        "--container",
        default=os.environ.get("SYSBENCH_CONTAINER", "sb"),
        help="the Docker container with sysbench 1.0.20 (default $SYSBENCH_CONTAINER or sb)",
    )
    parser.add_argument("--docker", default="docker", help="the docker executable")
    parser.add_argument(
        "--mysql-host",
        default="host.docker.internal",
        help="the address of this Mac seen from the container (default host.docker.internal)",
    )
    parser.add_argument(
        "--threads",
        type=thread_counts,
        default=[1, 16, 64],
        help="comma-separated thread counts (default 1,16,64); cases run in "
        "ascending order",
    )
    parser.add_argument(
        "--tables",
        type=common.runner.positive_int,
        default=16,
        help="sysbench --tables (default 16, as in perf_run.sh)",
    )
    parser.add_argument(
        "--table-size",
        type=common.runner.positive_int,
        default=100000,
        help="sysbench --table-size (default 100000, as in perf_run.sh)",
    )
    parser.add_argument(
        "--point-select-events",
        type=common.runner.positive_int,
        default=200000,
        help="events per oltp_point_select run (default 200000)",
    )
    parser.add_argument(
        "--read-write-events",
        type=common.runner.positive_int,
        default=30000,
        help="events per oltp_read_write run (default 30000)",
    )
    parser.add_argument(
        "--rand-seed",
        type=common.runner.positive_int,
        default=1,
        help="sysbench --rand-seed for every run (default 1; 0 would seed from the clock)",
    )
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    common.check_server_arguments(parser, args)
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
