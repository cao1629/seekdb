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

import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


FAMILY_DIR = Path(__file__).resolve().parent
REPO_ROOT = FAMILY_DIR.parents[3]
RUNNER_PATH = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
SDB_PATH = RUNNER_PATH.with_name("sdb.py")
HOST = "127.0.0.1"
SQL_TIMEOUT = 600
KILL_EXIT_TIMEOUT = 20
STDERR_TAIL_LINES = 20
PORT_CHECK_TIMEOUT = 2


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "mysqltest_for_seekdb", str(RUNNER_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


class CaseFailed(Exception):
    pass


class Recording(object):
    def __init__(self):
        self.content = bytearray()
        self.problems = []
        self.details = {}

    def line(self, text):
        self.content += text.encode("utf-8") + b"\n"

    def append(self, data):
        self.content += data

    def check(self, description, holds):
        self.line("-- check: {}: {}".format(description, "yes" if holds else "no"))
        if not holds:
            self.problems.append("check failed: {}".format(description))
        return holds


def client_command(args, options, database=None, user="root"):
    command = [
        str(args.obclient),
        "-h",
        HOST,
        "-P",
        str(args.port),
        "-u{}".format(user),
        "-A",
        "-c",
    ]
    if database is not None:
        command.append("-D{}".format(database))
    return command + list(options)


def run_client(args, sql, options, database=None, timeout=SQL_TIMEOUT):
    command = client_command(args, options, database)
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


def execute(args, recording, step, database=None):
    statements = statement_lines(step)
    start = len(recording.content)
    for statement in statements:
        recording.line(statement)
    code, output, error_output = run_client(
        args, "\n".join(statements), ("--table",), database
    )
    recording.append(output)
    if code != 0:
        recording.append(error_output)
        recording.line("-- obclient exit code {}".format(code))
        raise CaseFailed("statement failed: {}".format(" ".join(statements)))
    return bytes(recording.content[start:])


def query_rows(args, sql, database=None):
    code, output, _ = run_client(args, sql, ("-N", "-s"), database)
    if code != 0:
        return None
    lines = output.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.split("\t") for line in lines]


def tail_lines(data):
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return b"".join(line + b"\n" for line in lines[-STDERR_TAIL_LINES:])


def port_problem(port):
    try:
        connection = socket.create_connection((HOST, port), timeout=PORT_CHECK_TIMEOUT)
    except ConnectionRefusedError:
        return None
    except OSError as exc:
        return (
            "cannot tell whether port {0} is free before seekdb starts: a TCP "
            "connection to {1}:{0} failed with {2!r} instead of being refused".format(
                port, HOST, exc
            )
        )
    connection.close()
    return (
        "something already answers on port {0} before seekdb starts: {1}:{0} "
        "accepts TCP connections".format(port, HOST)
    )


def require_free_ports(ports):
    problems = [problem for problem in (port_problem(port) for port in ports) if problem]
    if problems:
        raise runner.RunnerError("; ".join(problems))


def sdb(command, arguments, description):
    runner.run_sdb(SDB_PATH, command, arguments, description, REPO_ROOT)


def start_server(args, parameters=()):
    arguments = [
        "--binary",
        args.seekdb,
        "--base-dir",
        args.base_dir,
        "--port",
        args.port,
        "--nodaemon",
    ]
    for parameter in parameters:
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


def stop_server(args):
    sdb("stop", ("--base-dir", args.base_dir), "stop seekdb")


def instance_binary(args):
    sdb_module = runner.load_sdb_module(SDB_PATH)
    base_dir = sdb_module._base_dir(str(args.base_dir))
    return sdb_module, base_dir, sdb_module.read_instance_binary(base_dir)


def instance_pid(args):
    try:
        sdb_module, base_dir, binary = instance_binary(args)
        return sdb_module.inspect_instance_process(base_dir, binary)
    except (OSError, RuntimeError, ValueError):
        return None


def server_running(args):
    return instance_pid(args) is not None


def server_exited(args):
    try:
        sdb_module, base_dir, binary = instance_binary(args)
    except (OSError, ValueError):
        return False
    try:
        sdb_module.inspect_instance_process(base_dir, binary)
    except (OSError, RuntimeError):
        return True
    return False


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
        return "seekdb was not running before the kill"
    problem = kill_process(pid)
    if problem is not None:
        return problem
    try:
        stop_server(args)
    except runner.RunnerError as exc:
        return str(exc)
    return None


def recorder_digests(script):
    return [
        (Path(script).name, runner.file_sha256(Path(script).resolve())),
        (Path(__file__).name, runner.file_sha256(Path(__file__).resolve())),
    ]


def recorder_line(args):
    return "-- recorder sha256 {}".format(
        ", ".join("{} {}".format(name, digest) for name, digest in args.recorder)
    )


def write_recorded_file(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(str(temporary), str(path))


def prepare_args(args, script, log_name):
    args.log_name = log_name
    args.case_header = []
    args.save_source = save_dir_source(args)
    args.seekdb = runner.absolute_path(args.seekdb)
    args.obclient = runner.absolute_path(args.obclient)
    args.base_dir = runner.absolute_path(args.base_dir)
    args.record_dir = runner.absolute_path(args.record_dir)
    args.save_instance_dir = runner.instance_save_dir(args)
    args.host = HOST
    args.manifest = None
    args.recorder = recorder_digests(script)
    args.recorder_path = str(Path(script).resolve().relative_to(REPO_ROOT))
    args.work_dir = Path(tempfile.mkdtemp(prefix=log_name + "-"))
    print("work directory: {}".format(args.work_dir), flush=True)
    if args.save_instance_dir is not None:
        print(
            "instance save directory (from {}): {}".format(
                args.save_source, args.save_instance_dir
            ),
            flush=True,
        )


def write_manifest(args, cases, recorded, extra):
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
        "recorder": args.recorder_path,
        "seekdb": str(args.seekdb),
        "seekdb_sha256": runner.file_sha256(args.seekdb),
        "mysqltest": str(args.obclient),
        "mysqltest_sha256": obclient_sha256,
        "obclient": str(args.obclient),
        "obclient_sha256": obclient_sha256,
        "init_sql": None,
        "init_sql_sha256": None,
        "init_user_sql": None,
        "init_user_sql_sha256": None,
        "sdb_sha256": runner.file_sha256(SDB_PATH),
        "runner_sha256": args.recorder[0][1],
        "recorder_sha256": dict(args.recorder),
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
        "cases": list(cases),
        "slice_index": 0,
        "slice_count": 1,
        "case_list": None,
        "max_retries": 0,
        "fresh_instance_per_case": False,
        "work_dir": str(args.work_dir),
        "recorded": recorded,
    }
    args.manifest.update(extra)
    runner.write_json(manifest_path, args.manifest)


def not_run_outcome(reason):
    return {
        "exit_code": None,
        "recorded": False,
        "partial": False,
        "problems": [reason],
    }


def run_case(args, name, function):
    recording = Recording()
    recording.line("-- {}".format(name))
    recording.line(recorder_line(args))
    for text in args.case_header:
        recording.line(text)
    error = None
    print("[ RUN      ] {}".format(name), flush=True)
    started = time.monotonic()
    try:
        function(args, recording)
        recording.check(
            "seekdb is still running at the end of the case", server_running(args)
        )
    except CaseFailed as exc:
        recording.problems.append(str(exc))
    except Exception as exc:
        error = "{}: {}".format(name, exc)
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
    if recording.details:
        outcome["details"] = recording.details
    return outcome, error


def run_family(args, cases, recorded, extra, setup=None):
    names = [name for name, _ in cases]
    outcomes = {}
    failed_cases = []
    error = None
    try:
        args.record_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(args, names, recorded, extra)
        if setup is not None:
            setup(args)
        for name, function in cases:
            if error is not None or failed_cases:
                outcomes[name] = not_run_outcome(
                    "not run: the cases share one server, and an earlier case failed"
                )
                continue
            outcome, case_error = run_case(args, name, function)
            outcomes[name] = outcome
            if outcome["exit_code"] != 0:
                failed_cases.append(name)
            if case_error is not None:
                error = case_error
    except Exception as exc:
        error = "{}; {}".format(error, exc) if error else str(exc)
        print("[{}][ERROR] {}".format(args.log_name, exc), file=sys.stderr)
    finally:
        cleanup_error = shut_down(args)
        if cleanup_error:
            error = "{}; {}".format(error, cleanup_error) if error else cleanup_error
    for name in names:
        if name not in outcomes:
            outcomes[name] = not_run_outcome("not run after an earlier error")
    return finish_manifest(args, outcomes, failed_cases, error, len(names))


def shut_down(args):
    problems = []
    if server_running(args):
        problem = kill_server(args)
        if problem is not None:
            problems.append("final kill: {}".format(problem))
    runner.save_instance_outputs(args, SDB_PATH, "end")
    cleanup_error = runner.destroy_instance(
        SDB_PATH, args.base_dir, REPO_ROOT, check=False
    )
    if cleanup_error:
        problems.append(cleanup_error)
    return "; ".join(problems) if problems else None


def finish_manifest(args, outcomes, failed_cases, error, case_count):
    success = not failed_cases and error is None and args.manifest is not None
    if args.manifest is not None:
        args.manifest.update(
            {
                "success": success,
                "failed_cases": failed_cases,
                "retried_cases": {},
                "error": error,
                "init_failed_statements": 0,
                "outcomes": outcomes,
            }
        )
        args.manifest["finished"] = True
        try:
            runner.write_json(args.record_dir / "manifest.json", args.manifest)
        except OSError as exc:
            print(
                "[{}][ERROR] cannot finish the record manifest: {}".format(
                    args.log_name, exc
                ),
                file=sys.stderr,
            )
            success = False
    print(
        "{} finished: cases={}, failed={}, success={}".format(
            args.log_name, case_count, len(failed_cases), success
        ),
        flush=True,
    )
    return 0 if success else 1


def save_dir_source(args):
    if args.save_instance_dir:
        return "--save-instance-dir"
    if os.environ.get(runner.INSTANCE_SAVE_ENVIRONMENT):
        return "${}".format(runner.INSTANCE_SAVE_ENVIRONMENT)
    return None


def require_new_or_empty(parser, path, what):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        parser.error("{} must be new or empty: {}".format(what, path))


def add_server_arguments(parser):
    parser.add_argument("--seekdb", required=True, help="seekdb executable")
    parser.add_argument("--obclient", required=True, help="obclient executable")
    parser.add_argument(
        "--base-dir",
        required=True,
        help="seekdb base directory, new or empty; destroyed with sdb.py destroy "
        "at the end",
    )
    parser.add_argument(
        "--record-dir",
        required=True,
        help="new or empty directory for manifest.json and one <case>.result "
        "(or .partial) per case",
    )
    parser.add_argument(
        "--port",
        type=runner.positive_int,
        required=True,
        help="the server's SQL port; required because judge runs share this machine",
    )
    parser.add_argument(
        "--save-instance-dir",
        help="copy the instance's log/ (and seekdb*.profraw) here before the "
        "destroy, as the runner does; defaults to ${}".format(
            runner.INSTANCE_SAVE_ENVIRONMENT
        ),
    )


def check_server_arguments(parser, args):
    for path, what in ((args.seekdb, "--seekdb"), (args.obclient, "--obclient")):
        if not runner.absolute_path(path).is_file():
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
