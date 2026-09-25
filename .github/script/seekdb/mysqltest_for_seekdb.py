#!/usr/bin/env python3
"""Run and merge SeekDB mysqltest slices without OBD."""

from __future__ import print_function

import argparse
from collections import Counter, namedtuple
import datetime
import difflib
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time


CASE_TIMEOUT = 3600
MAX_CASE_RETRIES = 3
INSTANCE_SAVE_ENVIRONMENT = "SEEKDB_COV_PROFRAW_DIR"
INSTANCE_SAVE_COUNTER = 0
READY_TIMEOUT = 600
MYSQLTEST_USER = "admin"
MYSQLTEST_PASSWORD = "admin"
MYSQLTEST_DATABASE = "test"
RESULT_MISMATCH_MESSAGES = (
    "Result content mismatch",
    "Result length mismatch",
)
INIT_ERROR_PATTERN = re.compile(
    r"^ERROR(?: \d+)?(?: \([^)]*\))? at line (\d+)(?: in file: '.*')?: "
)
RECORDING_INPUT_KEYS = (
    "mysqltest_sha256",
    "obclient_sha256",
    "init_sql_sha256",
    "init_user_sql_sha256",
    "sdb_sha256",
    "tools_deploy_tree",
    "fresh_instance_per_case",
    "seekdb_parameters",
    "ps_protocol",
    "compress",
    "plan_cache_stats",
    "plan_cache_read",
    "test_dir_sha256",
)
RECORDING_INPUT_DEFAULTS = {
    "seekdb_parameters": [],
    "ps_protocol": False,
    "compress": False,
    "plan_cache_stats": False,
}
RECORDING_NOTE_KEYS = ("seekdb_sha256", "runner_sha256", "repo_head", "test_dir")
SEEKDB_PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[^,]+$")
PLAN_CACHE_READ_ARGUMENTS = (
    "-uroot",
    "-A",
    "-B",
    "-N",
    "--proxy-mode",
    "--init-command=SET ob_enable_plan_cache = 0",
    "-e",
    "SELECT access_count, hit_count FROM oceanbase.__all_virtual_plan_cache_stat",
)
PLAN_CACHE_READ_TIMEOUT = 60
PLAN_CACHE_CHECK_ATTEMPTS = 3
PLAN_CACHE_CHECK_PAUSE = 1
PLAN_CACHE_LONG_WINDOW_SECONDS = 20
PLAN_CACHE_FILE = "plan_cache.tsv"
PLAN_CACHE_HEADER = "case\thits\tmisses"
PLAN_BEARING_LIST = ("migration", "judge", "lists", "plan-bearing.txt")
PLAN_BEARING_LIST_SHA256 = (
    "8566d1f37b9b9875daa2ecfbdebd44d693823fbdeda520120313660ce7c54972"
)
HASH_ORDER_LIST = ("migration", "judge", "lists", "hash-order-selects.txt")
HASH_ORDER_LIST_SHA256 = None
PLAN_TABLE_COLUMNS = (b"ID", b"OPERATOR", b"NAME", b"EST.ROWS", b"EST.TIME(us)")
EST_VALUE_PATTERN = re.compile(rb"(?:[0-9]+|more than 1\.0e19)\Z")
EST_PLACEHOLDER = b"#"
HASH_ORDER_COLUMNS = (
    "case",
    "occurrence",
    "statement_sha256",
    "rows",
    "next_sha256",
    "test_line",
    "hash_operators",
    "statement",
)
HASH_OPERATOR_WORD = "HASH"
HASH_ORDER_END_OF_FILE = "eof"
DIGEST_LENGTH = 16
DIGEST_PATTERN = re.compile(r"[0-9a-f]{16}\Z")
BOX_BORDER_PATTERN = re.compile(rb"\+(?:-+\+)+\Z")

MysqltestCase = namedtuple("MysqltestCase", ("name", "test_file", "result_file"))
InitStatement = namedtuple("InitStatement", ("line", "client_line", "text"))
HashOrderStatement = namedtuple(
    "HashOrderStatement",
    ("case", "occurrence", "digest", "rows", "next", "test_line", "operators", "text"),
)
CompareMask = namedtuple("CompareMask", ("list_path", "list_sha256", "load", "apply"))


class RunnerError(RuntimeError):
    pass


def absolute_path(value):
    return Path(os.path.abspath(os.path.expanduser(value)))


def format_command(command):
    return " ".join(shlex.quote(str(item)) for item in command)


def decode_output(output):
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", "replace")
    return output


def normalize_trailing_horizontal_whitespace(content):
    lines = content.split(b"\n")
    for index, line in enumerate(lines):
        if line.endswith(b"\r"):
            lines[index] = line[:-1].rstrip(b" \t") + b"\r"
        else:
            lines[index] = line.rstrip(b" \t")
    return b"\n".join(lines)


def files_equal_ignoring_trailing_whitespace(expected_path, actual_path):
    try:
        expected = expected_path.read_bytes()
        actual = actual_path.read_bytes()
    except OSError as exc:
        print(
            "warning: failed to compare mysqltest result files: {}".format(exc),
            file=sys.stderr,
        )
        return False
    return normalize_trailing_horizontal_whitespace(
        expected
    ) == normalize_trailing_horizontal_whitespace(actual)


def run_command(command, description, cwd=None, stdin=None):
    print("+ {}".format(format_command(command)), flush=True)
    try:
        result = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd) if cwd else None,
            stdin=stdin,
            check=False,
        )
    except OSError as exc:
        raise RunnerError("{}: {}".format(description, exc))
    if result.returncode != 0:
        raise RunnerError("{} exited with {}".format(description, result.returncode))


def run_sdb(sdb_script, command, arguments, description, cwd):
    run_command(
        [sys.executable, str(sdb_script), command] + [str(item) for item in arguments],
        description,
        cwd=cwd,
    )


def destroy_instance(sdb_script, base_dir, cwd, check=True):
    command = [
        sys.executable,
        str(sdb_script),
        "destroy",
        "--base-dir",
        str(base_dir),
    ]
    print("+ {}".format(format_command(command)), flush=True)
    try:
        result = subprocess.run(command, cwd=str(cwd), check=False)
    except OSError as exc:
        if check:
            raise RunnerError("failed to destroy seekdb: {}".format(exc))
        return "failed to destroy seekdb: {}".format(exc)
    if result.returncode != 0:
        message = "destroy seekdb exited with {}".format(result.returncode)
        if check:
            raise RunnerError(message)
        return message
    return None


def instance_save_dir(args):
    value = args.save_instance_dir or os.environ.get(INSTANCE_SAVE_ENVIRONMENT)
    return absolute_path(value) if value else None


@functools.lru_cache(maxsize=None)
def load_sdb_module(sdb_script):
    spec = importlib.util.spec_from_file_location("sdb", str(sdb_script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stop_instance_like_destroy(sdb_script, base_dir):
    print(
        "stopping seekdb in {} with sdb.py command_stop and require_match, "
        "as destroy does".format(base_dir),
        flush=True,
    )
    try:
        stop_code = load_sdb_module(sdb_script).command_stop(
            argparse.Namespace(
                base_dir=str(base_dir), quiet=False, require_match=True
            )
        )
    except Exception as exc:
        return "stop seekdb failed: {}".format(exc)
    if stop_code != 0:
        return "stop seekdb returned {}".format(stop_code)
    return None


def save_instance_outputs(args, sdb_script, reason):
    global INSTANCE_SAVE_COUNTER
    if args.save_instance_dir is None or not args.base_dir.is_dir():
        return
    stop_problem = stop_instance_like_destroy(sdb_script, args.base_dir)
    if stop_problem is not None:
        print("warning: {}".format(stop_problem), file=sys.stderr)

    INSTANCE_SAVE_COUNTER += 1
    destination = args.save_instance_dir / "{:05d}-{}{}".format(
        INSTANCE_SAVE_COUNTER, reason, "-stop-failed" if stop_problem else ""
    )
    try:
        destination.mkdir(parents=True)
        if stop_problem is not None:
            (destination / "stop.txt").write_text(
                stop_problem + "\n", encoding="utf-8"
            )
    except OSError as exc:
        print(
            "warning: failed to create {}: {}".format(destination, exc),
            file=sys.stderr,
        )
        return
    log_dir = args.base_dir / "log"
    if log_dir.is_dir():
        try:
            shutil.copytree(str(log_dir), str(destination / "log"))
        except OSError as exc:
            print("warning: failed to copy seekdb logs: {}".format(exc), file=sys.stderr)
    for profile in sorted(args.base_dir.glob("seekdb*.profraw")):
        try:
            shutil.copy2(str(profile), str(destination / profile.name))
        except OSError as exc:
            print("warning: failed to copy {}: {}".format(profile, exc), file=sys.stderr)
    print("instance outputs saved to {}".format(destination), flush=True)


def make_init_statement(text, first_line, client_line):
    stripped = text.strip()
    if not stripped:
        return None
    leading = text[: len(text) - len(text.lstrip())]
    return InitStatement(first_line + leading.count("\n"), client_line, stripped)


def split_sql_statements(content):
    statements = []
    delimiter = ";"
    buffer = ""
    buffer_line = 1
    client_line = 1
    in_string = ""
    in_comment = False
    for line_number, line in enumerate(content.split("\n"), 1):
        if not buffer:
            client_line = line_number
            buffer_line = line_number
            words = line.split()
            if words and words[0].lower() == "delimiter":
                if len(words) > 1:
                    delimiter = words[1]
                continue
            if not line:
                continue
        segment_start = 0
        position = 0
        line_consumed = False
        while position < len(line):
            character = line[position]
            if in_comment:
                if line.startswith("*/", position):
                    in_comment = False
                    position += 2
                else:
                    position += 1
            elif in_string:
                if character == "\\" and in_string != "`":
                    position += 2
                else:
                    if character == in_string:
                        in_string = ""
                    position += 1
            elif line.startswith(delimiter, position):
                statement = make_init_statement(
                    buffer + line[segment_start:position], buffer_line, client_line
                )
                if statement is not None:
                    statements.append(statement)
                position += len(delimiter)
                while position < len(line) and line[position].isspace():
                    position += 1
                rest = line[position:]
                if rest.startswith("#") or (
                    rest.startswith("--") and len(rest) > 2 and rest[2].isspace()
                ):
                    position = len(line)
                buffer = ""
                buffer_line = line_number
                segment_start = position
            elif character == "#" or (
                line.startswith("--", position)
                and (
                    position + 2 == len(line)
                    or line[position + 2].isspace()
                    or not (buffer or line[segment_start:position])
                )
            ):
                current = buffer + line[segment_start:position]
                if current:
                    buffer = current + line[position:] + "\n"
                else:
                    statement = make_init_statement(
                        line[position:], line_number, client_line
                    )
                    if statement is not None:
                        statements.append(statement)
                    buffer = ""
                line_consumed = True
                break
            elif line.startswith("/*", position) and not line.startswith(
                ("/*!", "/*+", "/*M!"), position
            ):
                in_comment = True
                position += 2
            else:
                if character in "'\"`":
                    in_string = character
                position += 1
        if not line_consumed:
            remainder = line[segment_start:]
            if buffer or remainder:
                buffer += remainder + "\n"
    statement = make_init_statement(buffer, buffer_line, client_line)
    if statement is not None:
        statements.append(statement)
    return statements


def attribute_init_errors(statements, error_output):
    failures = {}
    ambiguous = set()
    unattributed = []
    cursor = 0
    for error_line in error_output.splitlines():
        match = INIT_ERROR_PATTERN.match(error_line)
        if match is None:
            if error_line.startswith("ERROR"):
                unattributed.append(error_line)
            continue
        client_line = int(match.group(1))
        indexes = [
            index
            for index in range(cursor, len(statements))
            if statements[index].client_line == client_line
        ]
        if not indexes:
            unattributed.append(error_line)
            continue
        for index in indexes:
            failures[index] = error_line
        if len(indexes) > 1:
            ambiguous.update(indexes)
        cursor = indexes[-1] + 1
    return failures, ambiguous, unattributed


def init_statement_status(index, failures, ambiguous, unattributed, exit_code):
    if index in ambiguous:
        return "unknown"
    if index in failures:
        return "failed"
    if exit_code != 0 and failures and index > max(failures):
        return "not_run"
    if unattributed or (exit_code != 0 and not failures):
        return "unknown"
    return "succeeded"


def write_entry_gate(args):
    write_json(args.work_dir / "entry-gate.json", args.entry_gate)


def not_run_init_file(sql_file, database):
    try:
        content = sql_file.read_bytes()
    except OSError as exc:
        return {"file": str(sql_file), "database": database, "error": str(exc)}
    return {
        "file": str(sql_file),
        "database": database,
        "exit_code": None,
        "unattributed_errors": [],
        "statements": [
            {
                "line": statement.line,
                "text": statement.text,
                "status": "not_run",
                "error": None,
            }
            for statement in split_sql_statements(content.decode("utf-8", "replace"))
        ],
    }


def run_init_file(command, sql_file, deploy_dir):
    error_lines = []
    with sql_file.open("rb") as sql_input:
        process = subprocess.Popen(
            command,
            cwd=str(deploy_dir),
            stdin=sql_input,
            stderr=subprocess.PIPE,
        )
        for error_line in process.stderr:
            error_lines.append(error_line)
            sys.stderr.write(decode_output(error_line))
            sys.stderr.flush()
        process.stderr.close()
        exit_code = process.wait()
    return exit_code, decode_output(b"".join(error_lines))


def execute_init_sql(args, deploy_dir, reason):
    init_files = (
        (args.init_sql, "oceanbase"),
        (args.init_user_sql, "test"),
    )
    preparation = {"reason": reason, "files": []}
    args.entry_gate["preparations"].append(preparation)
    failure = None
    for sql_file, database in init_files:
        if failure is not None:
            preparation["files"].append(not_run_init_file(sql_file, database))
            continue
        command = [
            str(args.obclient),
            "-h",
            args.host,
            "-P",
            str(args.port),
            "-uroot",
            "-A",
            "-c",
            "-D{}".format(database),
        ]
        print("+ {} < {}".format(format_command(command), sql_file), flush=True)
        try:
            content = sql_file.read_bytes()
            exit_code, error_output = run_init_file(command, sql_file, deploy_dir)
        except OSError as exc:
            preparation["files"].append(
                {"file": str(sql_file), "database": database, "error": str(exc)}
            )
            failure = "failed to execute {}: {}".format(sql_file.name, exc)
            continue
        statements = split_sql_statements(content.decode("utf-8", "replace"))
        failures, ambiguous, unattributed = attribute_init_errors(
            statements, error_output
        )
        preparation["files"].append(
            {
                "file": str(sql_file),
                "database": database,
                "exit_code": exit_code,
                "unattributed_errors": unattributed,
                "statements": [
                    {
                        "line": statement.line,
                        "text": statement.text,
                        "status": init_statement_status(
                            index, failures, ambiguous, unattributed, exit_code
                        ),
                        "error": failures.get(index),
                    }
                    for index, statement in enumerate(statements)
                ],
            }
        )
        if exit_code != 0:
            failure = "{} exited with {}".format(sql_file.name, exit_code)
    if failure is not None:
        write_entry_gate(args)
        raise RunnerError(failure)


def prepare_instance(args, repo_root, sdb_script, deploy_dir, reason):
    save_instance_outputs(args, sdb_script, reason)
    destroy_instance(sdb_script, args.base_dir, repo_root)
    start_arguments = [
        "--binary",
        args.seekdb,
        "--base-dir",
        args.base_dir,
        "--port",
        args.port,
        "--nodaemon",
    ]
    for parameter in args.seekdb_parameter:
        start_arguments.extend(("--parameter", parameter))
    run_sdb(
        sdb_script,
        "start",
        start_arguments,
        "start seekdb",
        repo_root,
    )
    run_sdb(
        sdb_script,
        "wait-ready",
        (
            "--client",
            args.obclient,
            "--base-dir",
            args.base_dir,
            "--host",
            args.host,
            "--port",
            args.port,
            "--user",
            "root",
            "--timeout",
            READY_TIMEOUT,
        ),
        "wait for seekdb",
        repo_root,
    )
    execute_init_sql(args, deploy_dir, reason)
    if args.plan_cache_stats:
        check_plan_cache_reads(args, reason)


def load_configured_case_names(config_path):
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            lines = config_file.readlines()
    except OSError as exc:
        raise RunnerError("cannot read mysqltest config {}: {}".format(config_path, exc))

    case_names = []
    in_runtime_configs = False
    in_psmall = False
    in_test_set = False
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_runtime_configs = stripped == "runtime_configs:"
            in_psmall = False
            in_test_set = False
        elif in_runtime_configs and indent == 2:
            in_psmall = stripped == "psmall:"
            in_test_set = False
        elif in_psmall and indent == 4:
            in_test_set = stripped == "test-set:"
        elif in_test_set and indent == 6 and stripped.startswith("- "):
            case_name = stripped[2:].strip()
            if not case_name:
                raise RunnerError(
                    "empty mysqltest case at {}:{}".format(config_path, line_number)
                )
            case_names.append(case_name)

    if not case_names:
        raise RunnerError(
            "runtime_configs.psmall.test-set is empty in {}".format(config_path)
        )
    duplicates = sorted(
        name for name, count in Counter(case_names).items() if count > 1
    )
    if duplicates:
        raise RunnerError(
            "duplicate mysqltest cases in {}: {}".format(
                config_path, ", ".join(duplicates)
            )
        )
    return case_names


def discover_cases(repo_root):
    config_path = repo_root / "tools" / "deploy" / "mysqltest_config.yaml"
    mysql_test_dir = repo_root / "tools" / "deploy" / "mysql_test"
    test_dir = mysql_test_dir / "t"
    result_dir = mysql_test_dir / "r" / "mysql"
    suite_dir = mysql_test_dir / "test_suite"
    if not test_dir.is_dir():
        raise RunnerError("mysqltest case directory does not exist: {}".format(test_dir))
    case_names = load_configured_case_names(config_path)
    available_cases = {}
    top_level_names = set()

    for test_file in sorted(test_dir.glob("*.test")):
        if not test_file.is_file():
            continue
        name = test_file.stem
        available_cases[name] = MysqltestCase(
            name, test_file, result_dir / (name + ".result")
        )
        top_level_names.add(name)

    if suite_dir.is_dir():
        for test_file in sorted(suite_dir.glob("*/t/*.test")):
            if not test_file.is_file():
                continue
            suite_name = test_file.parent.parent.name
            name = "{}.{}".format(suite_name, test_file.stem)
            if name in available_cases:
                raise RunnerError("duplicate mysqltest case name: {}".format(name))
            available_cases[name] = MysqltestCase(
                name,
                test_file,
                test_file.parent.parent / "r" / "mysql" / (test_file.stem + ".result"),
            )

    missing_cases = sorted(set(case_names) - set(available_cases))
    if missing_cases:
        raise RunnerError(
            "mysqltest config references missing cases: {}".format(
                ", ".join(missing_cases)
            )
        )
    unconfigured_top_level_cases = sorted(top_level_names - set(case_names))
    if unconfigured_top_level_cases:
        raise RunnerError(
            "top-level mysqltest cases are missing from {}: {}".format(
                config_path, ", ".join(unconfigured_top_level_cases)
            )
        )
    missing_results = [
        name for name in case_names if not available_cases[name].result_file.is_file()
    ]
    if missing_results:
        raise RunnerError(
            "mysqltest cases have no result files: {}".format(
                ", ".join(missing_results)
            )
        )
    return [available_cases[name] for name in case_names]


def discover_test_dir_cases(test_dir):
    if not test_dir.is_dir():
        raise RunnerError("test directory does not exist: {}".format(test_dir))
    nested_tests = sorted(
        test_file.relative_to(test_dir).as_posix()
        for test_file in test_dir.rglob("*.test")
        if test_file.parent != test_dir and test_file.is_file()
    )
    if nested_tests:
        raise RunnerError(
            "test directory {} has .test files below its top level, which the "
            "runner does not run: {}".format(test_dir, ", ".join(nested_tests))
        )
    test_files = sorted(
        (test_file for test_file in test_dir.glob("*.test") if test_file.is_file()),
        key=lambda test_file: test_file.stem,
    )
    if not test_files:
        raise RunnerError("test directory {} has no .test files".format(test_dir))
    dotted_names = [test_file.name for test_file in test_files if "." in test_file.stem]
    if dotted_names:
        raise RunnerError(
            "test file names in {} must have no dot before .test: {}".format(
                test_dir, ", ".join(dotted_names)
            )
        )
    return [
        MysqltestCase(
            test_file.stem, test_file, test_dir / "r" / (test_file.stem + ".result")
        )
        for test_file in test_files
    ]


def check_test_dir_results(test_dir, discovered_cases, selected_cases):
    missing_results = [
        case.name for case in selected_cases if not case.result_file.is_file()
    ]
    if missing_results:
        raise RunnerError(
            "mysqltest cases have no result files: {}".format(
                ", ".join(missing_results)
            )
        )
    case_names = set(case.name for case in discovered_cases)
    orphaned_results = sorted(
        result_file.name
        for result_file in (test_dir / "r").glob("*.result")
        if result_file.is_file() and result_file.stem not in case_names
    )
    if orphaned_results:
        raise RunnerError(
            "result files in {} have no .test file in {}: {}".format(
                test_dir / "r", test_dir, ", ".join(orphaned_results)
            )
        )


def is_expected_result(test_dir, path):
    return path.parent == test_dir / "r" and path.suffix == ".result"


def test_inputs_sha256(test_dir):
    files = sorted(
        (path.relative_to(test_dir).as_posix(), path)
        for path in test_dir.rglob("*")
        if path.is_file() and not is_expected_result(test_dir, path)
    )
    digest = hashlib.sha256()
    for name, path in files:
        digest.update("{}  {}\n".format(file_sha256(path), name).encode("utf-8"))
    return digest.hexdigest()


def load_case_list(case_list_path, available_cases, unknown_description="configured"):
    try:
        with case_list_path.open("r", encoding="utf-8") as case_list_file:
            lines = case_list_file.readlines()
    except OSError as exc:
        raise RunnerError("cannot read case list {}: {}".format(case_list_path, exc))

    case_names = []
    for raw_line in lines:
        case_name = raw_line.split("#", 1)[0].strip()
        if case_name:
            case_names.append(case_name)

    if not case_names:
        raise RunnerError("case list {} names no cases".format(case_list_path))
    duplicates = sorted(
        name for name, count in Counter(case_names).items() if count > 1
    )
    if duplicates:
        raise RunnerError(
            "duplicate cases in case list {}: {}".format(
                case_list_path, ", ".join(duplicates)
            )
        )
    available_names = set(case.name for case in available_cases)
    unknown_cases = sorted(set(case_names) - available_names)
    if unknown_cases:
        raise RunnerError(
            "case list {} names cases that are not {}: {}".format(
                case_list_path, unknown_description, ", ".join(unknown_cases)
            )
        )
    listed_names = set(case_names)
    return [case for case in available_cases if case.name in listed_names]


def mysqltest_environment(args):
    environment = os.environ.copy()
    client_bin = str(args.obclient.parent)
    environment["PATH"] = client_bin + os.pathsep + environment.get("PATH", "")
    environment.update(
        {
            "OBMYSQL_PORT": str(args.port),
            "OBMYSQL_MS0": args.host,
            "OBMYSQL_MS0_DEV": args.host,
            "OBMYSQL_PWD": MYSQLTEST_PASSWORD,
            "OBMYSQL_USR": MYSQLTEST_USER,
            "OBSERVER_DIR": str(args.base_dir),
            "IS_BUSINESS": "0",
            "TENANT": "mysql",
        }
    )
    return environment


def store_recorded_result(args, case, staged_result, mysqltest_log):
    try:
        recorded = staged_result.read_bytes()
        logged = mysqltest_log.read_bytes()
    except OSError as exc:
        return "cannot check the recorded result of {}: {}".format(case.name, exc)
    if recorded != logged:
        return "recorded result {} differs from mysqltest log {}".format(
            staged_result, mysqltest_log
        )
    try:
        shutil.move(str(staged_result), str(args.record_dir / (case.name + ".result")))
    except OSError as exc:
        return "cannot store the recorded result of {}: {}".format(case.name, exc)
    return None


def store_partial_output(args, case, mysqltest_log):
    if not mysqltest_log.is_file():
        return False
    try:
        shutil.copy2(str(mysqltest_log), str(args.record_dir / (case.name + ".partial")))
    except OSError as exc:
        print(
            "warning: cannot store the partial output of {}: {}".format(
                case.name, exc
            ),
            file=sys.stderr,
        )
        return False
    return True


def run_case(args, deploy_dir, case, tmp_dir, log_dir):
    result_file = case.result_file
    staged_result = None
    mysqltest_log = None
    if args.record_dir is not None:
        record_name = case.name.replace(".", "_")
        staged_result = args.work_dir / "record_tmp" / (record_name + ".result")
        mysqltest_log = log_dir / (record_name + ".log")
        result_file = staged_result
        for stale_file in (staged_result, mysqltest_log):
            try:
                stale_file.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(
                    "warning: failed to remove stale file {}: {}".format(
                        stale_file, exc
                    ),
                    file=sys.stderr,
                )
    command = [
        str(args.mysqltest),
        "--host={}".format(args.host),
        "--port={}".format(args.port),
        "--user={}".format(MYSQLTEST_USER),
        "--password={}".format(MYSQLTEST_PASSWORD),
        "--database={}".format(MYSQLTEST_DATABASE),
        "--tmpdir={}".format(tmp_dir),
        "--logdir={}".format(log_dir),
        "--silent",
        "--test-file={}".format(case.test_file),
        "--result-file={}".format(result_file),
        "--timer-file={}".format(log_dir / "timer"),
        "--tail-lines=20",
    ]
    if args.ps_protocol:
        command.append("--ps-protocol")
    if args.compress:
        command.append("--compress")
    if staged_result is not None:
        command.append("--record")
    case_name = case.name
    reject_file = log_dir / (case.result_file.stem + ".reject")
    try:
        reject_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            "warning: failed to remove stale reject file {}: {}".format(
                reject_file, exc
            ),
            file=sys.stderr,
        )
        reject_file = None
    print("[ RUN      ] {}".format(case_name), flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(deploy_dir),
            env=mysqltest_environment(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=CASE_TIMEOUT,
            universal_newlines=True,
            check=False,
        )
        return_code = result.returncode
        output = decode_output(result.stdout)
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        output = decode_output(exc.stdout)
        output += "\n{} seconds timeout\n".format(CASE_TIMEOUT)
    except OSError as exc:
        return_code = 255
        output = "failed to run mysqltest: {}\n".format(exc)

    if staged_result is not None:
        if return_code == 0:
            problem = store_recorded_result(args, case, staged_result, mysqltest_log)
            if problem is not None:
                return_code = 1
                output += problem + "\n"
        partial = False
        if return_code != 0:
            partial = store_partial_output(args, case, mysqltest_log)
            try:
                staged_result.unlink()
            except OSError:
                pass
        args.record_outcomes[case.name] = {
            "exit_code": return_code,
            "recorded": return_code == 0,
            "partial": partial,
        }

    trailing_whitespace_ignored = False
    if (
        args.ignore_trailing_whitespace
        and return_code != 0
        and reject_file is not None
        and any(message in output for message in RESULT_MISMATCH_MESSAGES)
        and files_equal_ignoring_trailing_whitespace(
            case.result_file, reject_file
        )
    ):
        return_code = 0
        trailing_whitespace_ignored = True
        try:
            reject_file.unlink()
        except OSError as exc:
            print(
                "warning: failed to remove ignored reject file {}: {}".format(
                    reject_file, exc
                ),
                file=sys.stderr,
            )

    if output and not trailing_whitespace_ignored:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    elapsed = time.monotonic() - started
    if return_code == 0:
        suffix = ", trailing whitespace ignored" if trailing_whitespace_ignored else ""
        print(
            "[       OK ] {} ({:.3f}s{})".format(case_name, elapsed, suffix),
            flush=True,
        )
    else:
        print(
            "[  FAILED  ] {} ({:.3f}s, exit={})".format(
                case_name, elapsed, return_code
            ),
            flush=True,
        )
    return return_code, output, trailing_whitespace_ignored


def plan_cache_read_command(args):
    return [
        str(args.obclient),
        "-h",
        args.host,
        "-P",
        str(args.port),
    ] + list(PLAN_CACHE_READ_ARGUMENTS)


def wall_clock():
    return datetime.datetime.now().isoformat(sep=" ", timespec="microseconds")


def read_plan_cache_counters(args):
    command = plan_cache_read_command(args)
    started = wall_clock()
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=PLAN_CACHE_READ_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RunnerError(
            "plan cache read timed out after {} seconds".format(
                PLAN_CACHE_READ_TIMEOUT
            )
        )
    except OSError as exc:
        raise RunnerError("cannot run {}: {}".format(format_command(command), exc))
    finished = wall_clock()
    output = decode_output(result.stdout)
    if result.returncode != 0:
        raise RunnerError(
            "plan cache read exited with {}: {}".format(
                result.returncode, decode_output(result.stderr).strip()
            )
        )
    rows = [line.split("\t") for line in output.splitlines() if line]
    if len(rows) == 1 and len(rows[0]) == 2:
        try:
            return {
                "access_count": int(rows[0][0]),
                "hit_count": int(rows[0][1]),
                "started": started,
                "finished": finished,
            }
        except ValueError:
            pass
    raise RunnerError(
        "plan cache read printed {!r}; expected one row with access_count "
        "and hit_count".format(output)
    )


def plan_cache_delta(before, after):
    hits = after["hit_count"] - before["hit_count"]
    return {
        "hits": hits,
        "misses": after["access_count"] - before["access_count"] - hits,
        "before": before,
        "after": after,
    }


def check_plan_cache_reads(args, reason):
    check = {"instance": reason, "passed": False, "attempts": []}
    args.plan_cache_checks.append(check)
    try:
        for attempt_index in range(PLAN_CACHE_CHECK_ATTEMPTS):
            if attempt_index > 0:
                time.sleep(PLAN_CACHE_CHECK_PAUSE)
            first = read_plan_cache_counters(args)
            second = read_plan_cache_counters(args)
            attempt = {
                "hits": second["hit_count"] - first["hit_count"],
                "accesses": second["access_count"] - first["access_count"],
                "first": first,
                "second": second,
            }
            check["attempts"].append(attempt)
            if attempt["hits"] == 0 and attempt["accesses"] == 1:
                check["passed"] = True
                break
    except RunnerError as exc:
        check["error"] = str(exc)
    measured = ", ".join(
        "hits={} accesses={}".format(attempt["hits"], attempt["accesses"])
        for attempt in check["attempts"]
    )
    if "error" in check:
        measured = "{}{}{}".format(measured, "; " if measured else "", check["error"])
    print(
        "[ PC CHECK ] {} instance {}: {}".format(
            reason, "passed" if check["passed"] else "failed", measured
        ),
        flush=True,
    )
    if not check["passed"]:
        raise RunnerError(
            "plan cache read check failed on the {} instance: two reads back to "
            "back must differ by 0 hits and 1 access; got {}".format(reason, measured)
        )


def run_case_with_plan_cache(args, deploy_dir, case, tmp_dir, log_dir):
    if not args.plan_cache_stats:
        return run_case(args, deploy_dir, case, tmp_dir, log_dir)
    before = None
    before_error = None
    try:
        before = read_plan_cache_counters(args)
    except RunnerError as exc:
        before_error = "before the case: {}".format(exc)
    window_started = time.monotonic()
    outcome = run_case(args, deploy_dir, case, tmp_dir, log_dir)
    window_seconds = round(time.monotonic() - window_started, 3)
    if before_error is not None:
        entry = {"error": before_error}
    else:
        try:
            entry = plan_cache_delta(before, read_plan_cache_counters(args))
        except RunnerError as exc:
            entry = {"error": "after the case: {}".format(exc)}
    entry["seconds"] = window_seconds
    args.plan_cache[case.name] = entry
    if "error" in entry:
        print(
            "[ PC STATS ] {} not measured: {}".format(case.name, entry["error"]),
            flush=True,
        )
    else:
        print(
            "[ PC STATS ] {} hits={} misses={} ({:.3f}s between the reads)".format(
                case.name, entry["hits"], entry["misses"], window_seconds
            ),
            flush=True,
        )
    return outcome


def write_plan_cache_table(path, plan_cache):
    lines = [PLAN_CACHE_HEADER]
    for case_name, entry in plan_cache.items():
        if "error" not in entry:
            lines.append(
                "{}\t{}\t{}".format(case_name, entry["hits"], entry["misses"])
            )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def copy_instance_diagnostics(base_dir, destination):
    destination.mkdir(parents=True, exist_ok=True)
    log_dir = base_dir / "log"
    if log_dir.is_dir():
        try:
            shutil.copytree(str(log_dir), str(destination / "seekdb_log"))
        except OSError as exc:
            print("warning: failed to copy seekdb logs: {}".format(exc), file=sys.stderr)

    core_dir = destination / "core"
    copied = set()
    for pattern in ("core", "core.*", "core-*"):
        if not base_dir.exists():
            break
        for core_file in base_dir.rglob(pattern):
            if not core_file.is_file() or str(core_file) in copied:
                continue
            copied.add(str(core_file))
            core_dir.mkdir(parents=True, exist_ok=True)
            relative_name = "__".join(core_file.relative_to(base_dir).parts)
            try:
                shutil.copy2(str(core_file), str(core_dir / relative_name))
            except OSError as exc:
                print("warning: failed to copy {}: {}".format(core_file, exc), file=sys.stderr)


def save_case_failure(args, case_name, output):
    destination = args.work_dir / "failures" / case_name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "mysqltest.log").write_text(output, encoding="utf-8")
    save_instance_diagnostics(args)


def save_instance_diagnostics(args):
    destination = args.work_dir / "failures" / "instance"
    if not destination.exists():
        copy_instance_diagnostics(args.base_dir, destination)


def save_infrastructure_failure(args, message):
    destination = args.work_dir / "failures" / "infrastructure"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "error.txt").write_text(message + "\n", encoding="utf-8")
    save_instance_diagnostics(args)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    os.replace(str(temporary), str(path))


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(repo_root, arguments):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root)] + arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def stripped_or_none(value):
    return value.strip() if value is not None else None


def write_record_manifest(args, repo_root, sdb_script, cases):
    manifest_path = args.record_dir / "manifest.json"
    try:
        manifest_path.open("x").close()
    except FileExistsError:
        raise RunnerError("record directory is already in use: {}".format(manifest_path))
    args.record_manifest = {
        "finished": False,
        "seekdb": str(args.seekdb),
        "seekdb_sha256": file_sha256(args.seekdb),
        "mysqltest": str(args.mysqltest),
        "mysqltest_sha256": file_sha256(args.mysqltest),
        "obclient": str(args.obclient),
        "obclient_sha256": file_sha256(args.obclient),
        "init_sql": str(args.init_sql),
        "init_sql_sha256": file_sha256(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "init_user_sql_sha256": file_sha256(args.init_user_sql),
        "sdb_sha256": file_sha256(sdb_script),
        "runner_sha256": file_sha256(Path(__file__).resolve()),
        "repo_head": stripped_or_none(git_output(repo_root, ["rev-parse", "HEAD"])),
        "tools_deploy_tree": stripped_or_none(
            git_output(repo_root, ["rev-parse", "HEAD:tools/deploy"])
        ),
        "tools_deploy_status": git_output(
            repo_root, ["status", "--porcelain", "--", "tools/deploy"]
        ),
        "cases": [case.name for case in cases],
        "slice_index": args.slice_index,
        "slice_count": args.slice_count,
        "case_list": str(args.case_list) if args.case_list else None,
        "max_retries": args.max_retries,
        "fresh_instance_per_case": args.fresh_instance_per_case,
        "seekdb_parameters": sorted(args.seekdb_parameter),
        "ps_protocol": args.ps_protocol,
        "compress": args.compress,
        "plan_cache_stats": args.plan_cache_stats,
        "plan_cache_read": (
            list(PLAN_CACHE_READ_ARGUMENTS) if args.plan_cache_stats else None
        ),
        "test_dir": str(args.test_dir) if args.test_dir else None,
        "test_dir_sha256": args.test_dir_sha256,
        "work_dir": str(args.work_dir),
        "recorded": "<case>.result is the output of mysqltest --record when it "
        "exited 0; otherwise <case>.partial is the .log mysqltest left, if any, "
        "and outcomes gives the exit code",
    }
    if args.plan_cache_stats:
        args.record_manifest["plan_cache_recorded"] = (
            "{} holds case, hits and misses for every case whose two counter "
            "reads succeeded; plan_cache_errors names the others; "
            "plan_cache_seconds gives the seconds between each case's two reads; "
            "plan_cache_checks gives the check of two reads back to back on each "
            "instance".format(PLAN_CACHE_FILE)
        )
    write_json(manifest_path, args.record_manifest)


def finish_record_manifest(args, run_outcome):
    args.record_manifest.update(run_outcome)
    args.record_manifest["finished"] = True
    write_json(args.record_dir / "manifest.json", args.record_manifest)


def count_failed_init_statements(entry_gate):
    return len(
        set(
            (init_file["file"], statement["line"])
            for preparation in entry_gate["preparations"]
            for init_file in preparation["files"]
            for statement in init_file.get("statements", [])
            if statement["status"] == "failed"
        )
    )


def command_run(args):
    repo_root = Path(__file__).resolve().parents[3]
    deploy_dir = repo_root / "tools" / "deploy"
    sdb_script = Path(__file__).resolve().with_name("sdb.py")
    args.seekdb = absolute_path(args.seekdb)
    args.obclient = absolute_path(args.obclient)
    args.mysqltest = absolute_path(args.mysqltest)
    args.base_dir = absolute_path(args.base_dir)
    args.work_dir = absolute_path(args.work_dir)
    args.case_list = absolute_path(args.case_list) if args.case_list else None
    args.save_instance_dir = instance_save_dir(args)
    args.init_sql = (
        absolute_path(args.init_sql) if args.init_sql else deploy_dir / "init.sql"
    )
    args.init_user_sql = (
        absolute_path(args.init_user_sql)
        if args.init_user_sql
        else deploy_dir / "init_user.sql"
    )
    args.record_dir = absolute_path(args.record_dir) if args.record_dir else None
    args.test_dir = absolute_path(args.test_dir) if args.test_dir else None
    args.test_dir_sha256 = None
    args.plan_cache = {}
    args.plan_cache_checks = []
    args.record_manifest = None
    args.record_outcomes = {}
    args.entry_gate = {
        "init_sql": str(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "attribution": "each file is piped to one obclient session; a "
        "statement's status is inferred from the 'ERROR ... at line N' lines on "
        "that session's stderr, N being the line where the client started the "
        "statement",
        "preparations": [],
    }
    result_path = args.work_dir / "seekdb_result.json"
    entry_gate_path = args.work_dir / "entry-gate.json"

    selected_cases = []
    failed_cases = []
    retried_cases = {}
    trailing_whitespace_ignored_cases = []
    error = None
    args.work_dir.mkdir(parents=True, exist_ok=True)
    write_entry_gate(args)

    try:
        if args.test_dir is not None:
            discovered_cases = discover_test_dir_cases(args.test_dir)
            unknown_description = "in {}".format(args.test_dir)
        else:
            discovered_cases = discover_cases(repo_root)
            unknown_description = "configured"
        all_cases = discovered_cases
        if args.case_list is not None:
            all_cases = load_case_list(args.case_list, all_cases, unknown_description)
        selected_cases = all_cases[args.slice_index :: args.slice_count]
        if not selected_cases:
            raise RunnerError(
                "no cases selected for slice {} of {}{}".format(
                    args.slice_index,
                    args.slice_count,
                    " from case list {}".format(args.case_list)
                    if args.case_list
                    else "",
                )
            )
        if args.test_dir is not None:
            if args.record_dir is None:
                check_test_dir_results(args.test_dir, discovered_cases, selected_cases)
            args.test_dir_sha256 = test_inputs_sha256(args.test_dir)
        if args.record_dir is not None:
            args.record_dir.mkdir(parents=True, exist_ok=True)
            (args.work_dir / "record_tmp").mkdir(parents=True, exist_ok=True)
            write_record_manifest(args, repo_root, sdb_script, selected_cases)
        prepare_instance(args, repo_root, sdb_script, deploy_dir, "initial")
        write_entry_gate(args)
        tmp_dir = args.work_dir / "tmp"
        log_dir = args.work_dir / "mysqltest_log"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        restart_reason = None
        for case in selected_cases:
            if restart_reason is not None:
                prepare_instance(
                    args, repo_root, sdb_script, deploy_dir, restart_reason
                )
                restart_reason = None
            return_code = None
            output = ""
            attempt_return_codes = []
            for retry_index in range(args.max_retries + 1):
                if retry_index > 0:
                    print(
                        "[ RETRY   ] {} ({}/{})".format(
                            case.name, retry_index, args.max_retries
                        ),
                        flush=True,
                    )
                    prepare_instance(
                        args, repo_root, sdb_script, deploy_dir, "retry-" + case.name
                    )
                return_code, output, whitespace_ignored = run_case_with_plan_cache(
                    args, deploy_dir, case, tmp_dir, log_dir
                )
                if whitespace_ignored:
                    trailing_whitespace_ignored_cases.append(case.name)
                attempt_return_codes.append(return_code)
                if return_code == 0:
                    break
            if len(attempt_return_codes) > 1:
                retried_cases[case.name] = attempt_return_codes
            if return_code != 0:
                failed_cases.append(case.name)
                save_case_failure(args, case.name, output)
                restart_reason = "failed-" + case.name
            elif args.fresh_instance_per_case:
                restart_reason = "passed-" + case.name
    except Exception as exc:
        error = str(exc)
        print("[mysqltest][ERROR] {}".format(error), file=sys.stderr)
        try:
            save_infrastructure_failure(args, error)
        except OSError as save_error:
            print(
                "warning: failed to save infrastructure diagnostics: {}".format(
                    save_error
                ),
                file=sys.stderr,
            )
    finally:
        save_instance_outputs(args, sdb_script, "end")
        cleanup_error = destroy_instance(
            sdb_script, args.base_dir, repo_root, check=False
        )
        if cleanup_error:
            error = "{}; {}".format(error, cleanup_error) if error else cleanup_error

    plan_cache_errors = [
        "{}: {}".format(case_name, entry["error"])
        for case_name, entry in args.plan_cache.items()
        if "error" in entry
    ]
    success = not failed_cases and error is None and not plan_cache_errors
    init_failed_statements = count_failed_init_statements(args.entry_gate)
    write_entry_gate(args)
    if args.record_manifest is not None and args.plan_cache_stats:
        plan_cache_path = args.record_dir / PLAN_CACHE_FILE
        try:
            write_plan_cache_table(plan_cache_path, args.plan_cache)
        except OSError as exc:
            table_error = "cannot write {}: {}".format(plan_cache_path, exc)
            print("[mysqltest][ERROR] {}".format(table_error), file=sys.stderr)
            error = "{}; {}".format(error, table_error) if error else table_error
            success = False
    if args.record_manifest is not None:
        try:
            finish_record_manifest(
                args,
                {
                    "success": success,
                    "failed_cases": failed_cases,
                    "retried_cases": retried_cases,
                    "error": error,
                    "init_failed_statements": init_failed_statements,
                    "outcomes": args.record_outcomes,
                    "plan_cache_errors": plan_cache_errors,
                    "plan_cache_seconds": dict(
                        (case_name, entry["seconds"])
                        for case_name, entry in args.plan_cache.items()
                    ),
                    "plan_cache_checks": args.plan_cache_checks,
                },
            )
        except OSError as exc:
            manifest_error = "cannot finish the record manifest: {}".format(exc)
            print("[mysqltest][ERROR] {}".format(manifest_error), file=sys.stderr)
            error = "{}; {}".format(error, manifest_error) if error else manifest_error
            success = False
    payload = {
        "success": success,
        "slice_index": args.slice_index,
        "slice_count": args.slice_count,
        "case_count": len(selected_cases),
        "cases": [case.name for case in selected_cases],
        "failed_cases": failed_cases,
        "error": error,
        "case_order": (
            "file stems of the .test files in {}, sorted".format(args.test_dir)
            if args.test_dir is not None
            else "runtime_configs.psmall.test-set order in "
            "tools/deploy/mysqltest_config.yaml"
        ),
        "slice_assignment": (
            "cases[slice_index::slice_count] over the {} order, "
            "after the case_list filter".format(
                "sorted" if args.test_dir is not None else "configured"
            )
        ),
        "case_list": str(args.case_list) if args.case_list else None,
        "test_dir": str(args.test_dir) if args.test_dir else None,
        "test_dir_sha256": args.test_dir_sha256,
        "max_retries": args.max_retries,
        "retried_cases": retried_cases,
        "fresh_instance_per_case": args.fresh_instance_per_case,
        "seekdb_parameters": sorted(args.seekdb_parameter),
        "ps_protocol": args.ps_protocol,
        "compress": args.compress,
        "plan_cache_stats": args.plan_cache_stats,
        "plan_cache_read": (
            list(PLAN_CACHE_READ_ARGUMENTS) if args.plan_cache_stats else None
        ),
        "plan_cache": args.plan_cache if args.plan_cache_stats else None,
        "plan_cache_errors": plan_cache_errors,
        "plan_cache_checks": (
            args.plan_cache_checks if args.plan_cache_stats else None
        ),
        "save_instance_dir": (
            str(args.save_instance_dir) if args.save_instance_dir else None
        ),
        "init_sql": str(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "init_failed_statements": init_failed_statements,
        "entry_gate": str(entry_gate_path),
        "record_dir": str(args.record_dir) if args.record_dir else None,
        "ignore_trailing_whitespace": args.ignore_trailing_whitespace,
        "trailing_whitespace_ignored_cases": trailing_whitespace_ignored_cases,
    }
    write_json(result_path, payload)
    print(
        "slice {} finished: cases={}, failed={}, success={}".format(
            args.slice_index, len(selected_cases), len(failed_cases), success
        ),
        flush=True,
    )
    return 0 if success else 1


def unique(items):
    result = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def command_merge(args):
    repo_root = Path(__file__).resolve().parents[3]
    results_dir = absolute_path(args.results_dir)
    output_path = absolute_path(args.output)
    failed_cases = []
    errors = []
    executed_cases = []

    try:
        expected_cases = [case.name for case in discover_cases(repo_root)]
    except RunnerError as exc:
        expected_cases = []
        errors.append(str(exc))

    for slice_index in range(args.slice_count):
        result_path = (
            results_dir / "slice_{}".format(slice_index) / "seekdb_result.json"
        )
        try:
            with result_path.open("r", encoding="utf-8") as result_file:
                result = json.load(result_file)
        except (OSError, ValueError) as exc:
            errors.append("slice {} result unavailable: {}".format(slice_index, exc))
            continue

        if result.get("slice_index") != slice_index:
            errors.append("slice {} has invalid slice_index".format(slice_index))
        if result.get("slice_count") != args.slice_count:
            errors.append("slice {} has invalid slice_count".format(slice_index))

        cases = result.get("cases")
        if not isinstance(cases, list):
            errors.append("slice {} has no case list".format(slice_index))
        else:
            executed_cases.extend(cases)

        failed = result.get("failed_cases")
        if not isinstance(failed, list):
            errors.append("slice {} has invalid failed_cases".format(slice_index))
        else:
            failed_cases.extend(failed)

        run_error = result.get("error")
        if run_error:
            errors.append("slice {}: {}".format(slice_index, run_error))
        if result.get("success") is not True and not failed and not run_error:
            errors.append("slice {} reported failure without details".format(slice_index))

    case_counts = Counter(executed_cases)
    duplicates = sorted(case for case, count in case_counts.items() if count > 1)
    missing = sorted(set(expected_cases) - set(executed_cases))
    unexpected = sorted(set(executed_cases) - set(expected_cases))
    if duplicates:
        errors.append("duplicate cases: {}".format(",".join(duplicates)))
    if missing:
        errors.append("missing cases: {}".format(",".join(missing)))
    if unexpected:
        errors.append("unexpected cases: {}".format(",".join(unexpected)))

    failed_cases = unique(failed_cases)
    success = not failed_cases and not errors
    payload = {
        "success": success,
        "run_id": str(args.run_id),
        "slice_count": args.slice_count,
        "case_count": len(expected_cases),
        "failed_cases": failed_cases,
        "errors": errors,
    }
    write_json(output_path, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if success else 1


def load_recording(directory):
    if not directory.is_dir():
        raise RunnerError("recording directory does not exist: {}".format(directory))
    manifest_path = directory / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError) as exc:
        raise RunnerError("cannot read {}: {}".format(manifest_path, exc))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise RunnerError("{} has no case list".format(manifest_path))
    return manifest, [str(name) for name in manifest["cases"]]


def recording_problems(side, manifest):
    problems = []
    if manifest.get("finished") is not True:
        problems.append("{} recording did not finish".format(side))
    if manifest.get("max_retries") != 0:
        problems.append(
            "{} recording allowed retries: max_retries={}".format(
                side, manifest.get("max_retries")
            )
        )
    if manifest.get("retried_cases"):
        problems.append(
            "{} recording retried cases: {}".format(
                side, ", ".join(sorted(manifest["retried_cases"]))
            )
        )
    if manifest.get("error"):
        problems.append("{} recording failed: {}".format(side, manifest["error"]))
    if manifest.get("failed_cases"):
        problems.append(
            "{} recording has failed cases: {}".format(
                side, ", ".join(manifest["failed_cases"])
            )
        )
    if manifest.get("plan_cache_errors"):
        problems.append(
            "{} recording could not read the plan cache counters: {}".format(
                side, "; ".join(manifest["plan_cache_errors"])
            )
        )
    if manifest.get("plan_cache_stats") is True:
        checks = manifest.get("plan_cache_checks")
        if not isinstance(checks, list) or not checks:
            problems.append(
                "{} recording has no check of its plan cache reads".format(side)
            )
        else:
            failed_checks = [
                str(check.get("instance")) if isinstance(check, dict) else "?"
                for check in checks
                if not isinstance(check, dict) or check.get("passed") is not True
            ]
            if failed_checks:
                problems.append(
                    "{} recording failed the plan cache read check on: {}".format(
                        side, ", ".join(failed_checks)
                    )
                )
    deploy_status = manifest.get("tools_deploy_status")
    if deploy_status is None or manifest.get("tools_deploy_tree") is None:
        problems.append("{} recording does not identify tools/deploy".format(side))
    elif deploy_status:
        problems.append(
            "{} recording ran with local changes in tools/deploy: {}".format(
                side, "; ".join(deploy_status.splitlines())
            )
        )
    return problems


def recording_value(manifest, key):
    return manifest.get(key, RECORDING_INPUT_DEFAULTS.get(key))


def recording_differences(left_manifest, right_manifest, keys):
    return [
        "{} differs: left {}, right {}".format(
            key,
            recording_value(left_manifest, key),
            recording_value(right_manifest, key),
        )
        for key in keys
        if recording_value(left_manifest, key) != recording_value(right_manifest, key)
    ]


def load_plan_cache_table(directory, case_names):
    path = directory / PLAN_CACHE_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RunnerError("cannot read {}: {}".format(path, exc))
    if not lines or lines[0] != PLAN_CACHE_HEADER:
        raise RunnerError(
            "{} does not start with the header {!r}".format(path, PLAN_CACHE_HEADER)
        )
    known_names = set(case_names)
    rows = {}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 3:
            raise RunnerError(
                "{}:{}: expected case, hits and misses".format(path, line_number)
            )
        if fields[0] in rows or fields[0] not in known_names:
            raise RunnerError(
                "{}:{}: case {} is repeated or not in the recording".format(
                    path, line_number, fields[0]
                )
            )
        try:
            rows[fields[0]] = {"hits": int(fields[1]), "misses": int(fields[2])}
        except ValueError:
            raise RunnerError(
                "{}:{}: hits and misses must be integers".format(path, line_number)
            )
    return rows


def plan_cache_seconds(manifest):
    seconds = manifest.get("plan_cache_seconds")
    if not isinstance(seconds, dict):
        return {}
    return dict(
        (case_name, value)
        for case_name, value in seconds.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def compare_plan_cache_case(
    case_name, left_rows, right_rows, left_seconds, right_seconds
):
    entry = {
        "case": case_name,
        "left": left_rows.get(case_name),
        "right": right_rows.get(case_name),
        "seconds": {
            "left": left_seconds.get(case_name),
            "right": right_seconds.get(case_name),
        },
    }
    entry["timing_sensitive"] = any(
        seconds is not None and seconds >= PLAN_CACHE_LONG_WINDOW_SECONDS
        for seconds in entry["seconds"].values()
    )
    missing_from = [side for side in ("left", "right") if entry[side] is None]
    if missing_from:
        entry["status"] = "missing"
        entry["missing_from"] = missing_from
    elif entry["left"] == entry["right"]:
        entry["status"] = "identical"
    else:
        entry["status"] = "different"
    return entry


def summarize_plan_cache(entries):
    counts = Counter(entry["status"] for entry in entries)
    return {
        "cases": len(entries),
        "identical": counts["identical"],
        "different": counts["different"],
        "missing": counts["missing"],
        "different_timing_sensitive": sum(
            1
            for entry in entries
            if entry["status"] == "different" and entry["timing_sensitive"]
        ),
    }


def format_seconds(seconds):
    return "unknown" if seconds is None else "{:.3f}s".format(seconds)


def print_plan_cache_entry(entry):
    print("plan-cache {:<9} {}".format(entry["status"], entry["case"]), flush=True)
    if entry["status"] == "missing":
        print("  missing from {}".format(", ".join(entry["missing_from"])), flush=True)
    elif entry["status"] == "different":
        print(
            "  left hits={} misses={}; right hits={} misses={}".format(
                entry["left"]["hits"],
                entry["left"]["misses"],
                entry["right"]["hits"],
                entry["right"]["misses"],
            ),
            flush=True,
        )
        print(
            "  time between the reads: left {}, right {}{}".format(
                format_seconds(entry["seconds"]["left"]),
                format_seconds(entry["seconds"]["right"]),
                "; timing-sensitive: {} s or longer".format(
                    PLAN_CACHE_LONG_WINDOW_SECONDS
                )
                if entry["timing_sensitive"]
                else "",
            ),
            flush=True,
        )


def read_recorded_file(directory, case_name, suffix):
    path = directory / (case_name + suffix)
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RunnerError("cannot read {}: {}".format(path, exc))


def recorded_exit_code(manifest, case_name):
    outcomes = manifest.get("outcomes")
    if not isinstance(outcomes, dict) or not isinstance(outcomes.get(case_name), dict):
        return None
    return outcomes[case_name].get("exit_code")


def split_result_lines(content):
    lines = content.split(b"\n")
    result = [line + b"\n" for line in lines[:-1]]
    if lines[-1]:
        result.append(lines[-1])
    return result


def unified_result_diff(left_content, right_content, left_label, right_label):
    diff_lines = []
    for line in difflib.diff_bytes(
        difflib.unified_diff,
        split_result_lines(left_content),
        split_result_lines(right_content),
        os.fsencode(left_label),
        os.fsencode(right_label),
    ):
        text = line.decode("utf-8", "backslashreplace")
        if text.endswith("\n"):
            diff_lines.append(text)
        else:
            diff_lines.append(text + "\n\\ No newline at end of file\n")
    return "".join(diff_lines)


def first_difference(left_content, right_content):
    for offset, (left_byte, right_byte) in enumerate(zip(left_content, right_content)):
        if left_byte != right_byte:
            return offset
    return min(len(left_content), len(right_content))


def compare_contents(left_content, right_content, left_label, right_label):
    if left_content == right_content:
        return "identical", None, None
    return (
        "different",
        unified_result_diff(left_content, right_content, left_label, right_label),
        first_difference(left_content, right_content),
    )


def runner_repo_root():
    return Path(__file__).resolve().parents[3]


def short_digest(data):
    return hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH]


def collapse_whitespace(data):
    return b" ".join(data.split())


def split_content_lines(content):
    lines = content.split(b"\n")
    line_count = len(lines) - 1 if lines[-1] == b"" else len(lines)
    return lines, line_count


def read_mask_list(path):
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RunnerError("cannot read mask list {}: {}".format(path, exc))
    lines = [
        (number, line)
        for number, line in enumerate(text.split("\n"), 1)
        if line.strip() and not line.startswith("#")
    ]
    return hashlib.sha256(data).hexdigest(), lines


def load_plan_bearing_list(path, lines):
    names = [line.split("#", 1)[0].strip() for _, line in lines]
    names = [name for name in names if name]
    if not names:
        raise RunnerError("mask list {} names no cases".format(path))
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise RunnerError(
            "duplicate cases in mask list {}: {}".format(path, ", ".join(duplicates))
        )
    return dict((name, None) for name in names)


def parse_hash_order_line(path, number, line):
    fields = line.split("\t")
    where = "{}:{}".format(path, number)
    if len(fields) != len(HASH_ORDER_COLUMNS):
        raise RunnerError(
            "{}: expected {} tab-separated fields ({}), got {}".format(
                where, len(HASH_ORDER_COLUMNS), ", ".join(HASH_ORDER_COLUMNS), len(fields)
            )
        )
    case, occurrence, digest, rows, next_digest, test_line, operators, text = fields
    numbers = {}
    for name, value, smallest in (
        ("occurrence", occurrence, 1),
        ("rows", rows, 0),
        ("test_line", test_line, 1),
    ):
        if not re.match(r"[0-9]+\Z", value) or int(value) < smallest:
            raise RunnerError(
                "{}: {} must be an integer of at least {}, got {!r}".format(
                    where, name, smallest, value
                )
            )
        numbers[name] = int(value)
    if not case or case != case.strip():
        raise RunnerError("{}: the case name is empty or has spaces".format(where))
    statement = text.encode("utf-8")
    if not statement or collapse_whitespace(statement) != statement:
        raise RunnerError(
            "{}: the statement must be its echo with whitespace collapsed to single "
            "spaces".format(where)
        )
    if not DIGEST_PATTERN.match(digest) or digest != short_digest(statement):
        raise RunnerError(
            "{}: statement_sha256 must be the first {} hex digits of the sha256 of "
            "the statement column, {}".format(where, DIGEST_LENGTH, short_digest(statement))
        )
    if next_digest != HASH_ORDER_END_OF_FILE and not DIGEST_PATTERN.match(next_digest):
        raise RunnerError(
            "{}: next_sha256 must be {} hex digits or {}".format(
                where, DIGEST_LENGTH, HASH_ORDER_END_OF_FILE
            )
        )
    if operators != operators.strip() or HASH_OPERATOR_WORD not in operators:
        raise RunnerError(
            "{}: hash_operators must name the operators of the reference's plan "
            "that give the rows their order, as EXPLAIN prints them (such as {} "
            "GROUP BY), got {!r}; the helper leaves it empty for the confirmation "
            "step to fill in".format(where, HASH_OPERATOR_WORD, operators)
        )
    return HashOrderStatement(
        case,
        numbers["occurrence"],
        digest,
        numbers["rows"],
        next_digest,
        numbers["test_line"],
        operators,
        statement,
    )


def load_hash_order_list(path, lines):
    statements = {}
    keys = set()
    for number, line in lines:
        statement = parse_hash_order_line(path, number, line)
        key = (statement.case, statement.occurrence, statement.digest)
        if key in keys:
            raise RunnerError(
                "{}:{}: {} echo {} of {} is listed twice".format(
                    path, number, statement.case, statement.occurrence, statement.digest
                )
            )
        keys.add(key)
        statements.setdefault(statement.case, []).append(statement)
    if not statements:
        raise RunnerError("mask list {} names no statements".format(path))
    return statements


def est_cell_ok(cell, width):
    return len(cell) == width and EST_VALUE_PATTERN.match(cell.rstrip(b" ")) is not None


def plan_table_header_cells(line):
    if len(line) < 2 or not line.startswith(b"|") or not line.endswith(b"|"):
        return None
    cells = line[1:-1].split(b"|")
    if tuple(cell.rstrip(b" ") for cell in cells) != PLAN_TABLE_COLUMNS:
        return None
    if any(cell.strip(b" ") != cell.rstrip(b" ") for cell in cells):
        return None
    return cells


def plan_table_row_prefix(row, rows_width, time_width):
    parts = row.rsplit(b"|", 3)
    if len(parts) != 4 or parts[3] != b"" or not parts[0].startswith(b"|"):
        return None
    if not est_cell_ok(parts[1], rows_width) or not est_cell_ok(parts[2], time_width):
        return None
    return parts[0]


def mask_plan_tables(content):
    lines, line_count = split_content_lines(content)
    masked = list(lines)
    counts = {
        "masked": 0,
        "left_exact": 0,
        "header_lines": sum(
            1
            for line in lines[:line_count]
            if all(name in line for name in PLAN_TABLE_COLUMNS[3:])
        ),
    }
    index = 1
    while index < line_count:
        cells = plan_table_header_cells(lines[index])
        if cells is None:
            index += 1
            continue
        width = len(lines[index])
        end = index + 2
        while end < line_count and lines[end].startswith(b"|"):
            end += 1
        rows = lines[index + 2 : end]
        prefixes = [
            plan_table_row_prefix(row, len(cells[3]), len(cells[4])) for row in rows
        ]
        if not (
            lines[index - 1] == b"=" * width
            and index + 1 < line_count
            and lines[index + 1] == b"-" * width
            and end < line_count
            and lines[end] == b"=" * width
            and None not in prefixes
        ):
            counts["left_exact"] += 1
            index += 1
            continue
        header = b"|".join([b""] + cells[:3] + list(PLAN_TABLE_COLUMNS[3:]) + [b""])
        masked[index - 1] = b"=" * len(header)
        masked[index] = header
        masked[index + 1] = b"-" * len(header)
        for offset, prefix in enumerate(prefixes):
            masked[index + 2 + offset] = b"|".join(
                (
                    prefix,
                    EST_PLACEHOLDER.ljust(len(PLAN_TABLE_COLUMNS[3])),
                    EST_PLACEHOLDER.ljust(len(PLAN_TABLE_COLUMNS[4])),
                    b"",
                )
            )
        masked[end] = b"=" * len(header)
        counts["masked"] += 1
        index = end + 1
    return b"\n".join(masked), counts


def apply_est_mask(case_data, left_content, right_content):
    masked_left, left_counts = mask_plan_tables(left_content)
    masked_right, right_counts = mask_plan_tables(right_content)
    return masked_left, masked_right, {"left": left_counts, "right": right_counts}


def statement_echo_end(lines, line_count, index, statement):
    text = collapse_whitespace(lines[index])
    if not text or not statement.startswith(text):
        return None
    last = index
    while len(text) < len(statement) and last + 1 < line_count:
        last += 1
        part = collapse_whitespace(lines[last])
        if part:
            text = text + b" " + part
        if not statement.startswith(text):
            return None
    return last if text == statement else None


def find_statement_echoes(lines, line_count, statement):
    echoes = []
    index = 0
    while index < line_count:
        last = statement_echo_end(lines, line_count, index, statement)
        if last is None:
            index += 1
        else:
            echoes.append((index, last))
            index = last + 1
    return echoes


def result_header_size(lines, line_count, header):
    if (
        BOX_BORDER_PATTERN.match(lines[header])
        and header + 2 < line_count
        and lines[header + 1].startswith(b"|")
        and lines[header + 2] == lines[header]
    ):
        return 3
    return 1


def locate_listed_rows(lines, line_count, statement):
    echoes = find_statement_echoes(lines, line_count, statement.text)
    if len(echoes) < statement.occurrence:
        return None, "the statement is echoed {} times, the list names echo {}".format(
            len(echoes), statement.occurrence
        )
    header = echoes[statement.occurrence - 1][1] + 1
    if header >= line_count:
        return None, "nothing follows the echo"
    if lines[header].startswith(b"ERROR "):
        return None, "an error follows the echo"
    start = header + result_header_size(lines, line_count, header)
    end = start + statement.rows
    if end > line_count:
        return None, "fewer than {} lines follow the result header".format(
            statement.rows
        )
    if statement.next == HASH_ORDER_END_OF_FILE:
        if end != line_count:
            return None, "the file goes on after {} rows".format(statement.rows)
    elif end == line_count or short_digest(lines[end]) != statement.next:
        return None, "the line after {} rows is not the listed one".format(
            statement.rows
        )
    return (start, end), None


def apply_row_order_mask(case_data, left_content, right_content):
    sides = {}
    for side, content in (("left", left_content), ("right", right_content)):
        lines, line_count = split_content_lines(content)
        sides[side] = {
            "recorded": lines,
            "lines": list(lines),
            "count": line_count,
            "taken": [],
        }
    details = []
    for statement in case_data:
        blocks = {}
        problems = {}
        for side, state in sides.items():
            block, problem = locate_listed_rows(
                state["recorded"], state["count"], statement
            )
            if block is not None and any(
                block[0] < taken_end and taken_start < block[1]
                for taken_start, taken_end in state["taken"]
            ):
                block, problem = None, "the rows overlap another listed statement's rows"
            blocks[side] = block
            if problem is not None:
                problems[side] = problem
        record = {
            "occurrence": statement.occurrence,
            "statement_sha256": statement.digest,
            "test_line": statement.test_line,
            "rows": statement.rows,
            "hash_operators": statement.operators,
            "applied": not problems,
        }
        if problems:
            record["problems"] = problems
        else:
            recorded = {}
            for side, state in sides.items():
                start, end = blocks[side]
                recorded[side] = state["recorded"][start:end]
                state["lines"][start:end] = sorted(recorded[side])
                state["taken"].append((start, end))
            record["reordered"] = recorded["left"] != recorded["right"] and sorted(
                recorded["left"]
            ) == sorted(recorded["right"])
        details.append(record)
    return (
        b"\n".join(sides["left"]["lines"]),
        b"\n".join(sides["right"]["lines"]),
        details,
    )


COMPARE_MASKS = {
    "row-order": CompareMask(
        HASH_ORDER_LIST,
        HASH_ORDER_LIST_SHA256,
        load_hash_order_list,
        apply_row_order_mask,
    ),
    "est": CompareMask(
        PLAN_BEARING_LIST,
        PLAN_BEARING_LIST_SHA256,
        load_plan_bearing_list,
        apply_est_mask,
    ),
}


def load_masks(names):
    loaded = []
    configured_cases = None
    for name, mask in COMPARE_MASKS.items():
        if name not in names:
            continue
        path = runner_repo_root().joinpath(*mask.list_path)
        if mask.list_sha256 is None:
            raise RunnerError(
                "mask {} has no signed-off list yet: COMPARE_MASKS pins no sha256 "
                "for {}".format(name, path)
            )
        list_sha256, lines = read_mask_list(path)
        if list_sha256 != mask.list_sha256:
            raise RunnerError(
                "mask list {} has sha256 {}, but COMPARE_MASKS pins the signed-off "
                "{}; a mask list changes only together with its pinned sha256".format(
                    path, list_sha256, mask.list_sha256
                )
            )
        data = mask.load(path, lines)
        if configured_cases is None:
            configured_cases = set(
                case.name for case in discover_cases(runner_repo_root())
            )
        unknown_cases = sorted(set(data) - configured_cases)
        if unknown_cases:
            raise RunnerError(
                "mask list {} names cases that are not configured: {}".format(
                    path, ", ".join(unknown_cases)
                )
            )
        loaded.append(
            {
                "name": name,
                "list": str(path),
                "data": data,
                "list_sha256": list_sha256,
            }
        )
    return loaded


def apply_masks(masks, case_name, left_content, right_content):
    details = {}
    for mask in masks:
        if case_name not in mask["data"]:
            continue
        left_content, right_content, details[mask["name"]] = COMPARE_MASKS[
            mask["name"]
        ].apply(mask["data"][case_name], left_content, right_content)
    return left_content, right_content, details


def summarize_statuses(entries, status_key):
    counts = Counter(entry[status_key] for entry in entries)
    return {
        "cases": len(entries),
        "identical": counts["identical"],
        "different": counts["different"],
        "missing": counts["missing"],
        "failed_alike": sum(1 for entry in entries if entry.get("failed_alike")),
    }


def compare_case(left_dir, right_dir, left_manifest, right_manifest, case_name, masks):
    left_path = left_dir / (case_name + ".result")
    right_path = right_dir / (case_name + ".result")
    left_content = read_recorded_file(left_dir, case_name, ".result")
    right_content = read_recorded_file(right_dir, case_name, ".result")
    entry = {"case": case_name}
    scope = [mask["name"] for mask in masks if case_name in mask["data"]]
    entry["masks"] = scope
    if left_content is not None and right_content is not None:
        (
            entry["status"],
            entry["diff"],
            entry["first_difference"],
        ) = compare_contents(
            left_content, right_content, str(left_path), str(right_path)
        )
        entry["verdict"] = entry["status"]
        if scope:
            masked_left, masked_right, entry["mask_details"] = apply_masks(
                masks, case_name, left_content, right_content
            )
            entry["mask_changed"] = (
                masked_left != left_content or masked_right != right_content
            )
            (
                entry["masked_status"],
                entry["masked_diff"],
                entry["masked_first_difference"],
            ) = compare_contents(
                masked_left, masked_right, str(left_path), str(right_path)
            )
            entry["verdict"] = entry["masked_status"]
        return entry

    entry["status"] = "missing"
    entry["verdict"] = "missing"
    entry["missing_from"] = [
        side
        for side, content in (("left", left_content), ("right", right_content))
        if content is None
    ]
    entry["diff"] = None
    entry["first_difference"] = None
    entry["exit_codes"] = {
        "left": recorded_exit_code(left_manifest, case_name),
        "right": recorded_exit_code(right_manifest, case_name),
    }
    if scope:
        entry["mask_details"] = {}
        entry["mask_changed"] = False
        entry["masked_status"] = "missing"
        entry["masked_diff"] = None
        entry["masked_first_difference"] = None
    if len(entry["missing_from"]) == 2:
        left_partial = read_recorded_file(left_dir, case_name, ".partial")
        right_partial = read_recorded_file(right_dir, case_name, ".partial")
        (
            entry["partial_status"],
            entry["partial_diff"],
            entry["partial_first_difference"],
        ) = compare_contents(
            left_partial or b"",
            right_partial or b"",
            str(left_dir / (case_name + ".partial")),
            str(right_dir / (case_name + ".partial")),
        )
        entry["failed_alike"] = (
            left_partial is not None
            and right_partial is not None
            and entry["partial_status"] == "identical"
            and entry["exit_codes"]["left"] is not None
            and entry["exit_codes"]["left"] == entry["exit_codes"]["right"]
        )
    return entry


def print_mask_details(entry):
    details = entry.get("mask_details") or {}
    est = details.get("est")
    if est and any(counts["header_lines"] != counts["masked"] for counts in est.values()):
        print(
            "  est: plan tables masked left {}, right {}; plan tables left exact "
            "left {}, right {}; lines naming both EST columns left {}, right {}".format(
                est["left"]["masked"],
                est["right"]["masked"],
                est["left"]["left_exact"],
                est["right"]["left_exact"],
                est["left"]["header_lines"],
                est["right"]["header_lines"],
            ),
            flush=True,
        )
    for record in details.get("row-order", []):
        statement = "  row-order: echo {} of {} (test line {})".format(
            record["occurrence"], record["statement_sha256"], record["test_line"]
        )
        if not record["applied"]:
            print(
                "{} not masked: {}".format(
                    statement,
                    "; ".join(
                        "{} {}".format(side, problem)
                        for side, problem in sorted(record["problems"].items())
                    ),
                ),
                flush=True,
            )
        elif record["reordered"]:
            print(
                "{}: the rows come in a different order on the two sides; "
                "masked".format(statement),
                flush=True,
            )


def print_compare_entry(entry, masks):
    if masks:
        print(
            "{:<9} {:<9} {}".format(
                entry["status"],
                entry["masked_status"] if entry["masks"] else "-",
                entry["case"],
            ),
            flush=True,
        )
        print_mask_details(entry)
    else:
        print("{:<9} {}".format(entry["status"], entry["case"]), flush=True)
    if "missing_from" in entry:
        print(
            "  missing from {}; exit codes: left {}, right {}".format(
                ", ".join(entry["missing_from"]),
                entry["exit_codes"]["left"],
                entry["exit_codes"]["right"],
            ),
            flush=True,
        )
    if "partial_status" in entry:
        print(
            "  both sides failed; partial output {}{}".format(
                entry["partial_status"],
                ", failed alike" if entry["failed_alike"] else "",
            ),
            flush=True,
        )
        if entry["partial_diff"]:
            print(entry["partial_diff"], end="", flush=True)
            print(
                "  partial output: first difference at byte {}".format(
                    entry["partial_first_difference"]
                ),
                flush=True,
            )
    if entry["diff"] is not None:
        print(entry["diff"], end="", flush=True)
        print(
            "  first difference at byte {}".format(entry["first_difference"]),
            flush=True,
        )
    if entry["masks"] and entry["masked_diff"] is not None:
        print("  masked ({}):".format(", ".join(entry["masks"])), flush=True)
        print(entry["masked_diff"], end="", flush=True)
        print(
            "  masked: first difference at byte {}".format(
                entry["masked_first_difference"]
            ),
            flush=True,
        )


def mask_statistics(mask, results):
    compared = [
        entry for entry in results if mask["name"] in entry.get("mask_details", {})
    ]
    not_compared = sorted(
        set(mask["data"]) - set(entry["case"] for entry in compared)
    )
    statistics = {
        "name": mask["name"],
        "list": mask["list"],
        "list_sha256": mask["list_sha256"],
        "listed_cases": len(mask["data"]),
        "compared_cases": len(compared),
        "not_compared_cases": len(not_compared),
    }
    details = [entry["mask_details"][mask["name"]] for entry in compared]
    if mask["name"] == "est":
        for side in ("left", "right"):
            statistics["est_header_lines_" + side] = sum(
                detail[side]["header_lines"] for detail in details
            )
            statistics["tables_masked_" + side] = sum(
                detail[side]["masked"] for detail in details
            )
            statistics["tables_left_exact_" + side] = sum(
                detail[side]["left_exact"] for detail in details
            )
    else:
        records = [record for detail in details for record in detail]
        statistics["listed_statements"] = sum(
            len(statements) for statements in mask["data"].values()
        )
        statistics["statements"] = len(records)
        statistics["not_compared_statements"] = statistics["listed_statements"] - len(
            records
        )
        statistics["masked"] = sum(1 for record in records if record["applied"])
        statistics["not_masked"] = len(records) - statistics["masked"]
        statistics["reordered"] = sum(
            1 for record in records if record.get("reordered")
        )
    statistics["cases_not_compared"] = not_compared
    return statistics


def print_mask_statistics(statistics):
    counts = ", ".join(
        "{}={}".format(key, value)
        for key, value in statistics.items()
        if key not in ("name", "list", "list_sha256", "cases_not_compared")
    )
    print(
        "mask {}: {} (sha256 {}): {}".format(
            statistics["name"], statistics["list"], statistics["list_sha256"], counts
        ),
        flush=True,
    )


def command_compare(args):
    left_dir = absolute_path(args.left)
    right_dir = absolute_path(args.right)
    try:
        masks = load_masks(set(args.mask))
        left_manifest, left_names = load_recording(left_dir)
        right_manifest, right_names = load_recording(right_dir)
        if masks:
            for side, manifest in (("left", left_manifest), ("right", right_manifest)):
                if manifest.get("test_dir") is not None:
                    raise RunnerError(
                        "--mask applies only to recordings of the configured cases; "
                        "the {} recording ran --test-dir {}".format(
                            side, manifest["test_dir"]
                        )
                    )
        case_names = unique(left_names + right_names)
        if not case_names:
            raise RunnerError(
                "no recorded cases in {} or {}".format(left_dir, right_dir)
            )
        problems = recording_problems("left", left_manifest)
        problems.extend(recording_problems("right", right_manifest))
        for side, manifest in (("left", left_manifest), ("right", right_manifest)):
            if args.require_plan_cache and manifest.get("plan_cache_stats") is not True:
                problems.append(
                    "{} recording was not made with --plan-cache-stats".format(side)
                )
            if args.require_ps_protocol and manifest.get("ps_protocol") is not True:
                problems.append(
                    "{} recording was not made with --ps-protocol".format(side)
                )
            if args.require_compress and manifest.get("compress") is not True:
                problems.append(
                    "{} recording was not made with --compress".format(side)
                )
        problems.extend(
            recording_differences(
                left_manifest, right_manifest, RECORDING_INPUT_KEYS
            )
        )
        if left_names != right_names:
            problems.append(
                "the case lists differ: {} only left, {} only right".format(
                    ", ".join(sorted(set(left_names) - set(right_names))) or "none",
                    ", ".join(sorted(set(right_names) - set(left_names))) or "none",
                )
            )
        notes = recording_differences(
            left_manifest, right_manifest, RECORDING_NOTE_KEYS
        )
        results = [
            compare_case(
                left_dir, right_dir, left_manifest, right_manifest, case_name, masks
            )
            for case_name in case_names
        ]
        plan_cache_compared = (
            left_manifest.get("plan_cache_stats") is True
            and right_manifest.get("plan_cache_stats") is True
        )
        plan_cache_results = []
        if plan_cache_compared:
            left_plan_cache = load_plan_cache_table(left_dir, left_names)
            right_plan_cache = load_plan_cache_table(right_dir, right_names)
            left_seconds = plan_cache_seconds(left_manifest)
            right_seconds = plan_cache_seconds(right_manifest)
            plan_cache_results = [
                compare_plan_cache_case(
                    case_name,
                    left_plan_cache,
                    right_plan_cache,
                    left_seconds,
                    right_seconds,
                )
                for case_name in case_names
            ]
    except RunnerError as exc:
        print("[compare][ERROR] {}".format(exc), file=sys.stderr)
        return 2

    for problem in problems:
        print("recording check failed: {}".format(problem), flush=True)
    for note in notes:
        print("note: {}".format(note), flush=True)
    for entry in results:
        print_compare_entry(entry, masks)
    for entry in plan_cache_results:
        print_plan_cache_entry(entry)

    summary = summarize_statuses(results, "status")
    masked_summary = None
    verdict_summary = summary
    mask_names = [mask["name"] for mask in masks]
    mask_reports = [mask_statistics(mask, results) for mask in masks]
    if masks:
        masked_entries = [entry for entry in results if entry["masks"]]
        masked_summary = summarize_statuses(masked_entries, "masked_status")
        exact_summary = summarize_statuses(masked_entries, "status")
        for key in ("identical", "different", "missing"):
            masked_summary["exact_" + key] = exact_summary[key]
        masked_summary["mask_changed"] = sum(
            1 for entry in masked_entries if entry["mask_changed"]
        )
        verdict_summary = summarize_statuses(results, "verdict")
    plan_cache_summary = (
        summarize_plan_cache(plan_cache_results) if plan_cache_compared else None
    )
    success = (
        verdict_summary["different"] == 0
        and verdict_summary["missing"] == 0
        and not problems
    )
    if plan_cache_summary is not None:
        success = (
            success
            and plan_cache_summary["different"] == 0
            and plan_cache_summary["missing"] == 0
        )
    payload = {
        "success": success,
        "left": str(left_dir),
        "right": str(right_dir),
        "masks": mask_names,
        "mask_lists": mask_reports,
        "required": {
            "plan_cache_stats": args.require_plan_cache,
            "ps_protocol": args.require_ps_protocol,
            "compress": args.require_compress,
        },
        "recording_problems": problems,
        "recording_notes": notes,
        "left_manifest": left_manifest,
        "right_manifest": right_manifest,
        "summary": summary,
        "masked_summary": masked_summary,
        "verdict_summary": verdict_summary,
        "cases": results,
        "plan_cache": {
            "compared": plan_cache_compared,
            "summary": plan_cache_summary,
            "cases": plan_cache_results,
        },
    }
    if args.out:
        write_json(absolute_path(args.out), payload)
    print(
        "compare finished: cases={cases}, identical={identical}, "
        "different={different}, missing={missing}, "
        "failed_alike={failed_alike}, recording_problems={problems}".format(
            problems=len(problems), **summary
        ),
        flush=True,
    )
    if masks:
        print(
            "masked ({}): cases={cases}, identical={identical}, "
            "different={different}, missing={missing}, "
            "exact_identical={exact_identical}, exact_different={exact_different}, "
            "exact_missing={exact_missing}, mask_changed={mask_changed}".format(
                ", ".join(mask_names), **masked_summary
            ),
            flush=True,
        )
        print(
            "verdict: cases={cases}, identical={identical}, "
            "different={different}, missing={missing} (the masked result for the "
            "{masked} masked cases, the exact result for the other {exact})".format(
                masked=masked_summary["cases"],
                exact=verdict_summary["cases"] - masked_summary["cases"],
                **verdict_summary
            ),
            flush=True,
        )
        for statistics in mask_reports:
            print_mask_statistics(statistics)
    if plan_cache_summary is not None:
        print(
            "plan cache: cases={cases}, identical={identical}, "
            "different={different}, missing={missing}, "
            "different_timing_sensitive={different_timing_sensitive}".format(
                **plan_cache_summary
            ),
            flush=True,
        )
    return 0 if success else 1


def mask_name(value):
    if value not in COMPARE_MASKS:
        raise argparse.ArgumentTypeError(
            "unknown mask {}; known masks: {}".format(
                value, ", ".join(sorted(COMPARE_MASKS)) or "none"
            )
        )
    return value


def non_negative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def seekdb_parameter(value):
    if not SEEKDB_PARAMETER_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            "expected NAME=VALUE with no comma in VALUE, got {!r}".format(value)
        )
    return value


def create_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="run one mysqltest slice")
    run.add_argument("--seekdb", required=True, help="seekdb executable")
    run.add_argument("--obclient", required=True, help="obclient executable")
    run.add_argument("--mysqltest", required=True, help="mysqltest executable")
    run.add_argument("--base-dir", required=True, help="seekdb base directory")
    run.add_argument("--work-dir", required=True, help="slice output directory")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=positive_int, default=2881)
    run.add_argument("--slice-index", type=non_negative_int, required=True)
    run.add_argument("--slice-count", type=positive_int, required=True)
    run.add_argument(
        "--max-retries",
        type=non_negative_int,
        default=MAX_CASE_RETRIES,
        help="times a failed case is run again on a new instance",
    )
    run.add_argument(
        "--fresh-instance-per-case",
        action="store_true",
        help="prepare a new seekdb instance before every case",
    )
    run.add_argument(
        "--case-list",
        help="file naming the cases to run (configured cases, or cases in "
        "--test-dir), one per line; # starts a comment",
    )
    run.add_argument(
        "--test-dir",
        help="run DIR/<name>.test, sorted by name, instead of the configured "
        "cases; <name> has no dot, and no .test file may sit below the top level; "
        "without --record-dir, every selected case needs its expected output "
        "DIR/r/<name>.result and every DIR/r/*.result needs a .test; mysqltest "
        "still runs in tools/deploy",
    )
    run.add_argument(
        "--ps-protocol",
        action="store_true",
        help="pass --ps-protocol to mysqltest, which then sends every statement "
        "its prepared-statement filter accepts through COM_STMT_PREPARE and "
        "COM_STMT_EXECUTE",
    )
    run.add_argument(
        "--compress",
        action="store_true",
        help="pass --compress (-C) to mysqltest, asking it to use the compressed "
        "client/server protocol on its connections",
    )
    run.add_argument(
        "--plan-cache-stats",
        action="store_true",
        help="read the server's plan cache access and hit counters as root "
        "before and after every case and store each case's hits and misses in "
        "seekdb_result.json and, with --record-dir, in DIR/{}; on every new "
        "instance, first check that two reads back to back differ by 0 hits "
        "and 1 access".format(PLAN_CACHE_FILE),
    )
    run.add_argument(
        "--seekdb-parameter",
        action="append",
        default=[],
        type=seekdb_parameter,
        metavar="NAME=VALUE",
        help="pass --parameter NAME=VALUE to sdb.py start for every instance; "
        "repeatable, one NAME at most once",
    )
    run.add_argument(
        "--save-instance-dir",
        help="copy each instance's log/ and seekdb*.profraw here before it is "
        "destroyed; defaults to ${}".format(INSTANCE_SAVE_ENVIRONMENT),
    )
    run.add_argument(
        "--init-sql",
        help="SQL file run in database oceanbase after every start; "
        "defaults to tools/deploy/init.sql",
    )
    run.add_argument(
        "--init-user-sql",
        help="SQL file run in database test after --init-sql; "
        "defaults to tools/deploy/init_user.sql",
    )
    run.add_argument(
        "--record-dir",
        help="record each case's actual output with mysqltest --record into "
        "DIR/<case>.result (DIR/<case>.partial for a failed case), with "
        "DIR/manifest.json; DIR must be new or empty; needs --max-retries 0",
    )
    run.add_argument(
        "--ignore-trailing-whitespace",
        dest="ignore_trailing_whitespace",
        action="store_true",
        default=True,
        help="pass a case whose .reject differs from its .result only in "
        "trailing spaces and tabs (the default)",
    )
    run.add_argument(
        "--no-ignore-trailing-whitespace",
        dest="ignore_trailing_whitespace",
        action="store_false",
        help="fail such a case like any other mismatch",
    )
    run.set_defaults(handler=command_run)

    merge = subparsers.add_parser("merge", help="merge mysqltest slice results")
    merge.add_argument("--results-dir", required=True)
    merge.add_argument("--slice-count", type=positive_int, required=True)
    merge.add_argument("--run-id", default="0")
    merge.add_argument("--output", required=True)
    merge.set_defaults(handler=command_merge)

    compare = subparsers.add_parser(
        "compare", help="compare two recordings case by case, byte for byte"
    )
    compare.add_argument("--left", required=True, help="recording directory")
    compare.add_argument("--right", required=True, help="recording directory")
    compare.add_argument("--out", help="write the report to this file as JSON")
    compare.add_argument(
        "--mask",
        action="append",
        default=[],
        type=mask_name,
        metavar="NAME",
        help="also compare the cases the mask's list names with the mask applied "
        "to both sides; the exit status follows the masked result for those cases "
        "and the exact result for the others; the list must have the sha256 the "
        "runner pins for it, and recordings made with --test-dir are refused; "
        "repeatable; known masks: {}".format(
            ", ".join(sorted(COMPARE_MASKS)) or "none"
        ),
    )
    compare.add_argument(
        "--require-plan-cache",
        action="store_true",
        help="report a recording problem unless both recordings were made with "
        "--plan-cache-stats",
    )
    compare.add_argument(
        "--require-ps-protocol",
        action="store_true",
        help="report a recording problem unless both recordings were made with "
        "--ps-protocol",
    )
    compare.add_argument(
        "--require-compress",
        action="store_true",
        help="report a recording problem unless both recordings were made with "
        "--compress",
    )
    compare.set_defaults(handler=command_compare)

    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "run" and args.slice_index >= args.slice_count:
        parser.error("slice-index must be smaller than slice-count")
    if args.command == "run":
        parameter_names = [
            parameter.split("=", 1)[0] for parameter in args.seekdb_parameter
        ]
        repeated_names = sorted(
            name for name, count in Counter(parameter_names).items() if count > 1
        )
        if repeated_names:
            parser.error(
                "--seekdb-parameter names a parameter more than once: {}".format(
                    ", ".join(repeated_names)
                )
            )
        save_dir = instance_save_dir(args)
        if save_dir is not None and save_dir.exists():
            if not save_dir.is_dir() or any(save_dir.iterdir()):
                parser.error(
                    "instance save directory must be new or empty: {}".format(
                        save_dir
                    )
                )
        if args.record_dir:
            if args.max_retries != 0:
                parser.error("--record-dir needs --max-retries 0")
            record_dir = absolute_path(args.record_dir)
            if record_dir.exists():
                if not record_dir.is_dir() or any(record_dir.iterdir()):
                    parser.error(
                        "record directory must be new or empty: {}".format(
                            record_dir
                        )
                    )
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
