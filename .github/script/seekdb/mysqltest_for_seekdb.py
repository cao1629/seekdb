#!/usr/bin/env python3
"""Run and merge SeekDB mysqltest slices without OBD."""

from __future__ import print_function

import argparse
from collections import Counter, namedtuple
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import time


CASE_TIMEOUT = 3600
MAX_CASE_RETRIES = 3
READY_TIMEOUT = 600
MYSQLTEST_USER = "admin"
MYSQLTEST_PASSWORD = "admin"
MYSQLTEST_DATABASE = "test"
RESULT_MISMATCH_MESSAGES = (
    "Result content mismatch",
    "Result length mismatch",
)
EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_PRODUCER = "seekdb.mysqltest.host.v1"
MAX_CORPUS_FILE_BYTES = 64 * 1024 * 1024

MysqltestCase = namedtuple("MysqltestCase", ("name", "test_file", "result_file"))


class RunnerError(RuntimeError):
    pass


def _sha256_regular_file(path, maximum_size=None):
    """Hash one stable regular non-symlink file through a nofollow fd."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise RunnerError("evidence input is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode)
                or (maximum_size is not None and before.st_size > maximum_size)):
            raise RunnerError("evidence input must be a bounded regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if maximum_size is not None and total > maximum_size:
                raise RunnerError("evidence input exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or total != after.st_size):
            raise RunnerError("evidence input changed while hashing")
        return {"sha256": digest.hexdigest(), "size": total}
    finally:
        os.close(descriptor)


def source_commit(repo_root):
    """Return the exact Git revision that owns one host mysqltest run."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("cannot resolve mysqltest source revision") from exc


def mysqltest_corpus_digest(repo_root):
    """Hash every source, include, SQL, result, config, and parser byte."""
    repo_root = Path(repo_root).resolve()
    mysql_root = repo_root / "tools/deploy/mysql_test"
    paths = [
        path for path in mysql_root.rglob("*")
        if path.suffix in {".test", ".inc", ".sql", ".result"}
    ]
    paths.extend((
        repo_root / ".github/script/seekdb/mysqltest_for_seekdb.py",
        repo_root / "tools/deploy/mysqltest_config.yaml",
        repo_root / "unittest/ios_build/mysqltest_parser.py",
    ))
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        identity = _sha256_regular_file(path, MAX_CORPUS_FILE_BYTES)
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(identity["sha256"].encode("ascii"))
        digest.update(b"\0")
        digest.update(str(identity["size"]).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_host_evidence_identity(repo_root, binaries):
    """Bind host evidence to source, corpus, and all executable bytes."""
    required = ("seekdb", "obclient", "mysqltest")
    if set(binaries) != set(required):
        raise RunnerError("host binary identity is incomplete")
    binary_identity = {
        name: _sha256_regular_file(Path(binaries[name]))
        for name in required
    }
    serialized = json.dumps(
        binary_identity, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "producer": EVIDENCE_PRODUCER,
        "source_commit": source_commit(repo_root),
        "corpus_digest": mysqltest_corpus_digest(repo_root),
        "host_build_identity": hashlib.sha256(
            serialized.encode("utf-8")).hexdigest(),
        "host_binaries": binary_identity,
    }


def seal_evidence(payload):
    """Return a copy with a digest over every other evidence field."""
    sealed = dict(payload)
    sealed.pop("evidence_digest", None)
    serialized = json.dumps(
        sealed, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"))
    sealed["evidence_digest"] = hashlib.sha256(
        serialized.encode("utf-8")).hexdigest()
    return sealed


def verify_evidence_digest(payload):
    """Return whether an evidence payload retains its canonical digest."""
    if not isinstance(payload, dict):
        return False
    expected = payload.get("evidence_digest")
    return (isinstance(expected, str)
            and seal_evidence(payload).get("evidence_digest") == expected)


def build_slice_evidence(
        identity, slice_index, slice_count, executed_cases,
        failed_cases, error):
    """Create one identity-bound slice result for later strict merging."""
    payload = dict(identity)
    payload.update({
        "result_kind": "slice",
        "success": not failed_cases and error is None,
        "slice_index": slice_index,
        "slice_count": slice_count,
        "case_count": len(executed_cases),
        "executed_cases": list(executed_cases),
        "failed_cases": list(failed_cases),
        "error": error,
    })
    return seal_evidence(payload)


def build_merged_evidence(
        identity, run_id, slice_count, executed_cases,
        failed_cases, errors):
    """Create the only aggregate schema accepted by the iPhone host gate."""
    payload = dict(identity)
    payload.update({
        "result_kind": "merged",
        "success": not failed_cases and not errors,
        "run_id": str(run_id),
        "slice_count": slice_count,
        "case_count": len(executed_cases),
        "executed_cases": list(executed_cases),
        "failed_cases": list(failed_cases),
        "errors": list(errors),
    })
    return seal_evidence(payload)


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


def execute_init_sql(obclient, host, port, deploy_dir):
    init_files = (
        (deploy_dir / "init.sql", "oceanbase"),
        (deploy_dir / "init_user.sql", "test"),
    )
    for sql_file, database in init_files:
        command = [
            str(obclient),
            "-h",
            host,
            "-P",
            str(port),
            "-uroot",
            "-A",
            "-c",
            "-D{}".format(database),
        ]
        print("+ {} < {}".format(format_command(command), sql_file), flush=True)
        try:
            with sql_file.open("rb") as sql_input:
                result = subprocess.run(
                    command,
                    cwd=str(deploy_dir),
                    stdin=sql_input,
                    check=False,
                )
        except OSError as exc:
            raise RunnerError("failed to execute {}: {}".format(sql_file.name, exc))
        if result.returncode != 0:
            raise RunnerError(
                "{} exited with {}".format(sql_file.name, result.returncode)
            )


def prepare_instance(args, repo_root, sdb_script, deploy_dir):
    destroy_instance(sdb_script, args.base_dir, repo_root)
    run_sdb(
        sdb_script,
        "start",
        (
            "--binary",
            args.seekdb,
            "--base-dir",
            args.base_dir,
            "--port",
            args.port,
        ),
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
    execute_init_sql(args.obclient, args.host, args.port, deploy_dir)


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


def run_case(args, deploy_dir, case, tmp_dir, log_dir):
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
        "--result-file={}".format(case.result_file),
        "--timer-file={}".format(log_dir / "timer"),
        "--tail-lines=20",
    ]
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

    trailing_whitespace_ignored = False
    if (
        return_code != 0
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
    return return_code, output


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


def command_run(args):
    repo_root = Path(__file__).resolve().parents[3]
    deploy_dir = repo_root / "tools" / "deploy"
    sdb_script = Path(__file__).resolve().with_name("sdb.py")
    args.seekdb = absolute_path(args.seekdb)
    args.obclient = absolute_path(args.obclient)
    args.mysqltest = absolute_path(args.mysqltest)
    args.base_dir = absolute_path(args.base_dir)
    args.work_dir = absolute_path(args.work_dir)
    result_path = args.work_dir / "seekdb_result.json"

    selected_cases = []
    failed_cases = []
    error = None
    identity = build_host_evidence_identity(repo_root, {
        "seekdb": args.seekdb,
        "obclient": args.obclient,
        "mysqltest": args.mysqltest,
    })
    args.work_dir.mkdir(parents=True, exist_ok=True)

    try:
        all_cases = discover_cases(repo_root)
        selected_cases = all_cases[args.slice_index :: args.slice_count]
        prepare_instance(args, repo_root, sdb_script, deploy_dir)
        tmp_dir = args.work_dir / "tmp"
        log_dir = args.work_dir / "mysqltest_log"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        for index, case in enumerate(selected_cases):
            return_code = None
            output = ""
            for retry_index in range(MAX_CASE_RETRIES + 1):
                if retry_index > 0:
                    print(
                        "[ RETRY   ] {} ({}/{})".format(
                            case.name, retry_index, MAX_CASE_RETRIES
                        ),
                        flush=True,
                    )
                    prepare_instance(args, repo_root, sdb_script, deploy_dir)
                return_code, output = run_case(
                    args, deploy_dir, case, tmp_dir, log_dir
                )
                if return_code == 0:
                    break
            if return_code != 0:
                failed_cases.append(case.name)
                save_case_failure(args, case.name, output)
                if index + 1 < len(selected_cases):
                    prepare_instance(args, repo_root, sdb_script, deploy_dir)
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
        cleanup_error = destroy_instance(
            sdb_script, args.base_dir, repo_root, check=False
        )
        if cleanup_error:
            error = "{}; {}".format(error, cleanup_error) if error else cleanup_error

    success = not failed_cases and error is None
    payload = build_slice_evidence(
        identity, args.slice_index, args.slice_count,
        [case.name for case in selected_cases], failed_cases, error)
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
    merged_identity = None
    expected_source_commit = source_commit(repo_root)
    expected_corpus_digest = mysqltest_corpus_digest(repo_root)

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

        if (not verify_evidence_digest(result)
                or result.get("schema_version") != EVIDENCE_SCHEMA_VERSION
                or result.get("producer") != EVIDENCE_PRODUCER
                or result.get("result_kind") != "slice"):
            errors.append("slice {} has invalid evidence schema".format(slice_index))
            continue
        identity = {
            key: result.get(key) for key in (
                "schema_version", "producer", "source_commit",
                "corpus_digest", "host_build_identity", "host_binaries")
        }
        if (identity["source_commit"] != expected_source_commit
                or identity["corpus_digest"] != expected_corpus_digest):
            errors.append("slice {} has stale source identity".format(slice_index))
            continue
        if merged_identity is None:
            merged_identity = identity
        elif identity != merged_identity:
            errors.append("slice {} has mismatched host identity".format(slice_index))
            continue

        if result.get("slice_index") != slice_index:
            errors.append("slice {} has invalid slice_index".format(slice_index))
        if result.get("slice_count") != args.slice_count:
            errors.append("slice {} has invalid slice_count".format(slice_index))

        cases = result.get("executed_cases")
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
    if merged_identity is None:
        merged_identity = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "producer": EVIDENCE_PRODUCER,
            "source_commit": expected_source_commit,
            "corpus_digest": expected_corpus_digest,
            "host_build_identity": "",
            "host_binaries": {},
        }
    payload = build_merged_evidence(
        merged_identity, args.run_id, args.slice_count,
        expected_cases, failed_cases, errors)
    write_json(output_path, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if success else 1


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
    run.set_defaults(handler=command_run)

    merge = subparsers.add_parser("merge", help="merge mysqltest slice results")
    merge.add_argument("--results-dir", required=True)
    merge.add_argument("--slice-count", type=positive_int, required=True)
    merge.add_argument("--run-id", default="0")
    merge.add_argument("--output", required=True)
    merge.set_defaults(handler=command_merge)

    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "run" and args.slice_index >= args.slice_count:
        parser.error("slice-index must be smaller than slice-count")
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
