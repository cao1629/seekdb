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
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


DESCRIPTION = (
    "Run the judge's restart scenarios against one seekdb binary and record "
    "them in the recording format of mysqltest_for_seekdb.py, so that two "
    "recordings can be compared with its compare subcommand."
)
REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
SDB_PATH = RUNNER_PATH.with_name("sdb.py")
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
HOST = "127.0.0.1"
DATABASE = "test"
SQL_TIMEOUT = 600
PROBE_TIMEOUT = 600
PROBE_INTERVAL = 1.0
CLIENT_TRANSACTIONS = 5000
KILL_AFTER_ACKNOWLEDGEMENTS = 200
ACKNOWLEDGEMENT_TIMEOUT = 600
ACKNOWLEDGEMENT_POLL_INTERVAL = 0.01
CLIENT_EXIT_TIMEOUT = 120


def load_runner():
    spec = importlib.util.spec_from_file_location("mysqltest_for_seekdb", str(RUNNER_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


class ScenarioFailed(Exception):
    pass


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


def run_client(args, sql, options):
    command = client_command(args, options)
    try:
        result = subprocess.run(
            command,
            input=sql.encode("utf-8") + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SQL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        message = "obclient did not finish within {} seconds\n".format(SQL_TIMEOUT)
        return None, b"", message.encode("utf-8")
    except OSError as exc:
        raise runner.RunnerError("cannot run obclient: {}".format(exc))
    return result.returncode, result.stdout, result.stderr


def statement_lines(step):
    return [step] if isinstance(step, str) else list(step)


def execute(args, recording, step):
    statements = statement_lines(step)
    start = len(recording.content)
    for statement in statements:
        recording.line(statement)
    code, output, error_output = run_client(args, "\n".join(statements), ("--table",))
    recording.append(output)
    if code != 0:
        recording.append(error_output)
        recording.line("-- obclient exit code {}".format(code))
        raise ScenarioFailed("statement failed: {}".format(" ".join(statements)))
    return bytes(recording.content[start:])


def query_rows(args, sql):
    code, output, _ = run_client(args, sql, ("-N", "-s"))
    if code != 0:
        return None
    lines = output.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.split("\t") for line in lines]


def wait_readable(args, probe):
    deadline = time.monotonic() + PROBE_TIMEOUT
    while True:
        code, _, _ = run_client(args, probe, ("-N", "-s"))
        if code == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(PROBE_INTERVAL)


def sdb(command, arguments, description):
    runner.run_sdb(SDB_PATH, command, arguments, description, REPO_ROOT)


def start_server(args):
    sdb(
        "start",
        (
            "--binary",
            args.seekdb,
            "--base-dir",
            args.base_dir,
            "--port",
            args.port,
            "--nodaemon",
        ),
        "start seekdb",
    )


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


def stop_server(args):
    sdb("stop", ("--base-dir", args.base_dir), "stop seekdb")


def server_running(args):
    sdb_module = runner.load_sdb_module(SDB_PATH)
    base_dir = sdb_module._base_dir(str(args.base_dir))
    try:
        binary = sdb_module.read_instance_binary(base_dir)
        return sdb_module.inspect_instance_process(base_dir, binary) is not None
    except (OSError, RuntimeError, ValueError):
        return False


def lifecycle_line(prefix, done, failed_step):
    steps = list(done)
    if failed_step is not None:
        steps.append("{} failed".format(failed_step))
    return "-- {}{}".format(prefix, ", ".join(steps))


def bring_up(args, recording, scenario):
    done = []
    for step, action in (
        ("start", lambda: start_server(args)),
        ("ready", lambda: wait_ready(args)),
        ("init", lambda: runner.execute_init_sql(args, DEPLOY_DIR, scenario)),
    ):
        try:
            action()
        except runner.RunnerError:
            recording.line(lifecycle_line("", done, step))
            raise
        done.append(step)
    recording.line(lifecycle_line("", done, None))


def restart(args, recording, number, probe, after_stop=None):
    prefix = "restart {}: ".format(number)
    done = []

    def fail(step, message):
        recording.line(lifecycle_line(prefix, done, step))
        raise ScenarioFailed("restart {}: {}".format(number, message))

    if not server_running(args):
        recording.line("-- {}seekdb was not running before the kill".format(prefix))
        raise ScenarioFailed("restart {}: seekdb was not running".format(number))
    try:
        stop_server(args)
    except runner.RunnerError as exc:
        fail("stop (kill)", str(exc))
    done.append("stop (kill)")
    if after_stop is not None:
        problem = after_stop()
        if problem is not None:
            fail(problem, problem)
    try:
        start_server(args)
    except runner.RunnerError as exc:
        fail("start", str(exc))
    done.append("start")
    try:
        wait_ready(args)
    except runner.RunnerError as exc:
        fail("ready", str(exc))
    if probe is not None and not wait_readable(args, probe):
        fail("ready", "{} did not succeed within {} seconds".format(probe, PROBE_TIMEOUT))
    done.append("ready")
    recording.line(lifecycle_line(prefix, done, None))


DATA_WRITES = (
    "create table t_data (id int not null, name varchar(40), amount decimal(12,3), "
    "born date, updated datetime(6), ratio double, note varchar(100), "
    "primary key (id), key idx_name (name));",
    "insert into t_data values "
    "(1, 'alpha', 10.500, '2020-01-31', '2020-01-31 23:59:59.123456', 0.5, 'first row'), "
    "(2, 'bravo', -3.250, '1999-12-31', '1999-12-31 00:00:00.000001', -2.25, null), "
    "(3, null, 0.000, null, null, null, null), "
    "(4, 'delta', 12345678.901, '2024-02-29', '2024-02-29 12:00:00.500000', 10000000000, 'leap day');",
    "insert into t_data values "
    "(5, 'echo', 0.001, '1970-01-01', '1970-01-01 00:00:01.000000', 3, ''), "
    "(6, 'O''Brien', 99.990, '2038-01-19', '2038-01-19 03:14:07.999999', 0.125, 'quote'), "
    r"(7, 'back\\slash', 1.000, '2000-02-29', '2000-02-29 00:00:00.000000', -0.75, 'backslash'), "
    "(8, 'trailing  ', 7.125, '2010-10-10', '2010-10-10 10:10:10.101010', 1.5, 'two trailing spaces');",
    "insert into t_data values "
    "(9, 'golf', 250.000, '2015-06-15', '2015-06-15 06:15:00.000000', 2.5, 'renamed later'), "
    "(10, 'hotel', 42.000, '2012-12-12', '2012-12-12 12:12:12.121212', 4.25, 'deleted by id'), "
    "(11, 'india', null, '2011-11-11', null, null, 'null amount'), "
    "(12, 'juliet', 5.500, '2005-05-05', '2005-05-05 05:05:05.050505', 8, 'deleted by name');",
    "update t_data set amount = amount * 2 where id in (1, 2);",
    "update t_data set note = null where id = 5;",
    "update t_data set name = 'golf-renamed', note = 'renamed' where id = 9;",
    "update t_data set ratio = ratio + 1, updated = '2026-09-24 10:00:00.000000' "
    "where name = 'echo';",
    "delete from t_data where id = 10;",
    "delete from t_data where name = 'juliet';",
    "delete from t_data where id = 4;",
    "insert into t_data values "
    "(4, 'delta-again', 4.400, '2024-03-01', '2024-03-01 00:00:00.000000', 0.25, "
    "'deleted and inserted again');",
    (
        "begin;",
        "insert into t_data values (13, 'kilo', 13.130, '2013-03-13', "
        "'2013-03-13 13:13:13.131313', 13, 'committed in a transaction');",
        "update t_data set amount = amount + 1 where id = 13;",
        "commit;",
    ),
    (
        "begin;",
        "insert into t_data values (14, 'lima', 14.140, '2014-04-14', null, null, "
        "'rolled back');",
        "delete from t_data where id = 1;",
        "rollback;",
    ),
)
DATA_SELECTS = (
    "select * from t_data order by id;",
    "select /*+ index(t_data idx_name) */ id, name from t_data "
    "where name is not null order by name, id;",
    "select /*+ full(t_data) */ id, name from t_data "
    "where name is not null order by name, id;",
    "select count(*), count(name), count(amount), count(note), sum(amount), "
    "min(born), max(updated), sum(ratio) from t_data;",
    "select id, name from t_data where note is null order by id;",
    "select id, amount from t_data where amount between 0 and 100 order by amount, id;",
    "select id, name, note from t_data where name like 'd%' order by id;",
    "select * from t_data where id in (1, 4, 9, 13, 14) order by id;",
)
DATA_PROBE = "select count(*) from t_data;"
DATA_SELECT_SET_LABELS = (
    "before any restart",
    "after restart 1",
    "after restart 2, with no writes since restart 1",
)


def scenario_restart_data(args, recording):
    for step in DATA_WRITES:
        execute(args, recording, step)
    select_sets = []
    for index, label in enumerate(DATA_SELECT_SET_LABELS):
        if index > 0:
            restart(args, recording, index, DATA_PROBE)
        recording.line("-- select set {}: {}".format(index, label))
        select_sets.append(
            b"".join(execute(args, recording, select) for select in DATA_SELECTS)
        )
    for index in range(1, len(select_sets)):
        recording.check(
            "select set {} is byte-identical to select set 0".format(index),
            select_sets[index] == select_sets[0],
        )


PARAMETERS = (
    ("max_syslog_file_count", "4"),
    ("trace_log_slow_query_watermark", "'5s'"),
)
KEPT_PARAMETER_COLUMNS = ("name", "value")


def column_boundaries(border):
    return [index for index, byte in enumerate(border) if byte == ord("+")]


def keep_table_columns(output, wanted):
    lines = output.split(b"\n")
    trailing = lines.pop() if lines and lines[-1] == b"" else None
    if len(lines) < 3 or not lines[0].startswith(b"+"):
        return None
    boundaries = column_boundaries(lines[0])
    header = lines[1]
    names = [
        header[boundaries[index] + 1 : boundaries[index + 1]].strip().decode("ascii", "replace").lower()
        for index in range(len(boundaries) - 1)
    ]
    positions = []
    for name in wanted:
        if names.count(name) != 1:
            return None
        positions.append(names.index(name))
    last = boundaries[max(positions) + 1]
    kept = []
    cells = []
    for line in lines:
        if len(line) <= last:
            return None
        border = line.startswith(b"+")
        separator = b"+" if border else b"|"
        if any(line[boundaries[index]] != separator[0] for index in positions) or (
            line[last] != separator[0]
        ):
            return None
        pieces = [line[boundaries[index] : boundaries[index + 1]] for index in positions]
        kept.append(b"".join(pieces) + separator)
        if not border and line is not header:
            cells.append(tuple(piece[1:].strip().decode("utf-8", "replace") for piece in pieces))
    if trailing is not None:
        kept.append(b"")
    return b"\n".join(kept), cells


def show_parameter(args, recording, name):
    statement = "show parameters like '{}';".format(name)
    recording.line(statement)
    recording.line("-- only the {} columns are kept".format(" and ".join(KEPT_PARAMETER_COLUMNS)))
    code, output, error_output = run_client(args, statement, ("--table",))
    if code != 0:
        recording.append(output)
        recording.append(error_output)
        recording.line("-- obclient exit code {}".format(code))
        raise ScenarioFailed("statement failed: {}".format(statement))
    kept = keep_table_columns(output, KEPT_PARAMETER_COLUMNS)
    if kept is None:
        recording.append(output)
        raise ScenarioFailed(
            "cannot find the {} columns in the output of {}".format(
                " and ".join(KEPT_PARAMETER_COLUMNS), statement
            )
        )
    table, rows = kept
    recording.append(table)
    return tuple(row[1] for row in rows if row[0] == name)


def scenario_restart_parameters(args, recording):
    names = [name for name, _ in PARAMETERS]
    defaults = dict((name, show_parameter(args, recording, name)) for name in names)
    for name, value in PARAMETERS:
        execute(args, recording, "alter system set {} = {};".format(name, value))
    after_set = dict((name, show_parameter(args, recording, name)) for name in names)
    restart(args, recording, 1, None)
    after_restart = dict((name, show_parameter(args, recording, name)) for name in names)
    for name in names:
        recording.check(
            "SHOW PARAMETERS shows exactly one row for {}".format(name),
            len(defaults[name]) == 1
            and len(after_set[name]) == 1
            and len(after_restart[name]) == 1,
        )
        recording.check(
            "ALTER SYSTEM SET changed the value SHOW PARAMETERS shows for {}".format(name),
            after_set[name] != defaults[name],
        )
        recording.check(
            "{} kept its value across restart 1".format(name),
            after_restart[name] == after_set[name],
        )


MID_TABLE = (
    "create table t_mid (seq int not null, payload varchar(64) not null, "
    "amount decimal(12,2) not null, noted date not null);"
)
MID_PROBE = "select count(*) from t_mid;"
MID_READ_BACK = "select seq, payload, amount, noted from t_mid order by seq;"


def mid_row(seq):
    cents = seq * 125
    return (
        "row-{:06d}".format(seq),
        "{}.{:02d}".format(cents // 100, cents % 100),
        "2026-02-{:02d}".format(seq % 28 + 1),
    )


def mid_client_sql():
    lines = []
    for seq in range(1, CLIENT_TRANSACTIONS + 1):
        payload, amount, noted = mid_row(seq)
        lines.append("begin;")
        lines.append(
            "insert into t_mid values ({}, '{}', {}, '{}');".format(
                seq, payload, amount, noted
            )
        )
        lines.append("commit;")
        lines.append("select {};".format(seq))
    return "\n".join(lines) + "\n"


def read_acknowledgements(path):
    try:
        content = path.read_bytes()
    except OSError:
        return None
    lines = content.split(b"\n")
    lines.pop()
    numbers = []
    for line in lines:
        text = line.strip()
        if not text.isdigit():
            return None
        numbers.append(int(text))
    return numbers


def acknowledgement_count(path):
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def wait_for_acknowledgements(client, path):
    deadline = time.monotonic() + ACKNOWLEDGEMENT_TIMEOUT
    while time.monotonic() < deadline:
        if acknowledgement_count(path) >= KILL_AFTER_ACKNOWLEDGEMENTS:
            return True
        if client.poll() is not None:
            return acknowledgement_count(path) >= KILL_AFTER_ACKNOWLEDGEMENTS
        time.sleep(ACKNOWLEDGEMENT_POLL_INTERVAL)
    return False


def wait_client_exit(client):
    try:
        client.wait(timeout=CLIENT_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "client exit"
    return None


def stop_client(client):
    if client.poll() is None:
        client.kill()
        client.wait()


def parse_mid_rows(rows):
    parsed = []
    for row in rows:
        if len(row) != 4 or not row[0].isdigit():
            return None
        parsed.append((int(row[0]), tuple(row[1:])))
    return parsed


def scenario_restart_mid_dml(args, recording):
    execute(args, recording, MID_TABLE)
    recording.line(
        "-- client: one obclient session runs {} numbered transactions (begin; insert; "
        "commit; then select the number as its acknowledgement); the server is killed "
        "after {} acknowledgements".format(CLIENT_TRANSACTIONS, KILL_AFTER_ACKNOWLEDGEMENTS)
    )
    client_sql = args.work_dir / "restart_mid_dml.client.sql"
    acknowledgements = args.work_dir / "restart_mid_dml.acknowledgements"
    client_errors = args.work_dir / "restart_mid_dml.client.stderr"
    client_sql.write_text(mid_client_sql(), encoding="utf-8")
    command = client_command(
        args, ("-N", "-s", "--unbuffered", "--disable-reconnect")
    )
    print("+ {} < {} > {}".format(runner.format_command(command), client_sql, acknowledgements), flush=True)
    with client_sql.open("rb") as stdin, acknowledgements.open("wb") as stdout, client_errors.open("wb") as stderr:
        try:
            client = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr)
        except OSError as exc:
            raise runner.RunnerError("cannot run obclient: {}".format(exc))
    try:
        if not wait_for_acknowledgements(client, acknowledgements):
            recording.line(
                "-- client: fewer than {} acknowledgements".format(
                    KILL_AFTER_ACKNOWLEDGEMENTS
                )
            )
            raise ScenarioFailed(
                "the client did not acknowledge {} transactions".format(
                    KILL_AFTER_ACKNOWLEDGEMENTS
                )
            )
        client_running = client.poll() is None
        restart(args, recording, 1, MID_PROBE, lambda: wait_client_exit(client))
    finally:
        stop_client(client)
    recording.check("the client was still running when the server was killed", client_running)

    acknowledged = read_acknowledgements(acknowledgements)
    recording.check(
        "the acknowledgements are 1..A in order, with A at least {}".format(
            KILL_AFTER_ACKNOWLEDGEMENTS
        ),
        acknowledged is not None
        and len(acknowledged) >= KILL_AFTER_ACKNOWLEDGEMENTS
        and acknowledged == list(range(1, len(acknowledged) + 1)),
    )
    recording.line(
        "-- read back, output not recorded because it depends on when the kill "
        "landed: {}".format(MID_READ_BACK)
    )
    rows = query_rows(args, MID_READ_BACK)
    if rows is None:
        raise ScenarioFailed("the read back failed: {}".format(MID_READ_BACK))
    parsed = parse_mid_rows(rows)
    recording.check("every read-back row has the four columns and a numeric seq", parsed is not None)
    parsed = parsed or []
    sequence = [seq for seq, _ in parsed]
    present = set(sequence)
    recording.check(
        "every acknowledged transaction's row is present",
        acknowledged is not None and all(seq in present for seq in acknowledged),
    )
    recording.check("no row is duplicated", len(sequence) == len(present))
    recording.check(
        "the rows form a gap-free prefix 1..M of the numbering",
        present == set(range(1, len(present) + 1)) and len(present) <= CLIENT_TRANSACTIONS,
    )
    recording.check(
        "the other columns of the present rows are intact",
        all(columns == mid_row(seq) for seq, columns in parsed),
    )


SCENARIOS = (
    ("restart_data", scenario_restart_data),
    ("restart_parameters", scenario_restart_parameters),
    ("restart_mid_dml", scenario_restart_mid_dml),
)
SCENARIO_NAMES = tuple(name for name, _ in SCENARIOS)


def write_recorded_file(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(str(temporary), str(path))


def run_scenario(args, name, function):
    recording = Recording()
    recording.line("-- {}".format(name))
    error = None
    print("[ RUN      ] {}".format(name), flush=True)
    started = time.monotonic()
    try:
        bring_up(args, recording, name)
        function(args, recording)
    except ScenarioFailed as exc:
        recording.problems.append(str(exc))
    except Exception as exc:
        error = "{}: {}".format(name, exc)
    finally:
        runner.save_instance_outputs(args, SDB_PATH, name)
        cleanup_error = runner.destroy_instance(
            SDB_PATH, args.base_dir, REPO_ROOT, check=False
        )
        if cleanup_error:
            error = "{}; {}".format(error, cleanup_error) if error else cleanup_error
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
        "recorder": "migration/judge/harness/restart_scenarios.py",
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
        "runner_sha256": runner.file_sha256(Path(__file__).resolve()),
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
        "cases": list(scenarios),
        "slice_index": 0,
        "slice_count": 1,
        "case_list": None,
        "max_retries": 0,
        "fresh_instance_per_case": True,
        "work_dir": str(args.work_dir),
        "recorded": "<scenario>.result is the scenario's recording when it ran "
        "and every check held; otherwise <scenario>.partial is what it recorded "
        "up to the failure, and outcomes gives the exit code and the problems",
    }
    runner.write_json(manifest_path, args.manifest)


def command_run(args):
    args.seekdb = runner.absolute_path(args.seekdb)
    args.obclient = runner.absolute_path(args.obclient)
    args.base_dir = runner.absolute_path(args.base_dir)
    args.record_dir = runner.absolute_path(args.record_dir)
    args.save_instance_dir = runner.instance_save_dir(args)
    args.init_sql = (
        runner.absolute_path(args.init_sql) if args.init_sql else DEPLOY_DIR / "init.sql"
    )
    args.init_user_sql = (
        runner.absolute_path(args.init_user_sql)
        if args.init_user_sql
        else DEPLOY_DIR / "init_user.sql"
    )
    args.host = HOST
    args.manifest = None
    args.work_dir = Path(tempfile.mkdtemp(prefix="restart-scenarios-"))
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

    outcomes = {}
    failed_cases = []
    error = None
    try:
        args.record_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(args, [name for name, _ in selected])
        for name, function in selected:
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
        error = "{}; {}".format(error, exc) if error else str(exc)
        print("[restart][ERROR] {}".format(exc), file=sys.stderr)

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
                "[restart][ERROR] cannot finish the record manifest: {}".format(exc),
                file=sys.stderr,
            )
            success = False
    print(
        "restart scenarios finished: scenarios={}, failed={}, success={}".format(
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
        help="seekdb base directory, new or empty; reused across the restarts of "
        "one scenario and destroyed with sdb.py destroy after each scenario",
    )
    parser.add_argument(
        "--record-dir",
        required=True,
        help="new or empty directory for manifest.json and one "
        "<scenario>.result (or .partial) per scenario",
    )
    parser.add_argument("--port", type=runner.positive_int, required=True)
    parser.add_argument(
        "--init-sql",
        help="SQL file run in database oceanbase after the first start of each "
        "scenario; defaults to tools/deploy/init.sql",
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
    for path, what in ((args.init_sql, "--init-sql"), (args.init_user_sql, "--init-user-sql")):
        if path is not None and not runner.absolute_path(path).is_file():
            parser.error("{} is not a file: {}".format(what, path))
    require_new_or_empty(parser, runner.absolute_path(args.base_dir), "base directory")
    require_new_or_empty(parser, runner.absolute_path(args.record_dir), "record directory")
    save_dir = runner.instance_save_dir(args)
    if save_dir is not None:
        require_new_or_empty(parser, save_dir, "instance save directory")
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
