#!/usr/bin/env python3
"""Parity judge for the seekdb C++ -> Rust migration.

Runs the judge's mysqltest cases against any seekdb binary through the public
interface only -- process flags (--base-dir/--port/--parameter) and the MySQL
protocol -- so the original and the port are evaluated on equal terms.
Expected outputs are the .result files recorded from the original binary.

  judge.py run --seekdb BIN --work-dir DIR [--protocol plain|ps|compress] ...
  judge.py run --seekdb ORIGINAL_BIN --work-dir DIR --record --cases NAME,...

Differences from .github/script/seekdb/mysqltest_for_seekdb.py, on purpose:
only init_public.sql is applied (no engine-specific setup), cases come from
suites/mysqltest/cases.tsv, failures are never retried, and the MySQL protocol
variant is selectable. See migration/judge/README.md.
"""

from __future__ import print_function

import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time


JUDGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = JUDGE_DIR.parents[1]
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
SDB_SCRIPT = JUDGE_DIR / "runner" / "sdb.py"
MANIFEST = JUDGE_DIR / "suites" / "mysqltest" / "cases.tsv"
INIT_SQL = JUDGE_DIR / "suites" / "mysqltest" / "init_public.sql"
PROTOCOL_DIR = JUDGE_DIR / "suites" / "mysqltest" / "protocol"
DEFAULT_CLIENT_DIR = REPO_ROOT / "deps" / "3rd" / "u01" / "obclient" / "bin"

CASE_TIMEOUT = 3600
READY_TIMEOUT = 600
MYSQLTEST_USER = "admin"
MYSQLTEST_PASSWORD = "admin"
MYSQLTEST_DATABASE = "test"
STATUSES = ("clean", "portable", "fragile", "rewritten", "scenario", "pending", "quarantined")
RUNNABLE_STATUSES = ("clean", "portable", "fragile", "rewritten", "scenario")
PROTOCOL_FLAGS = {"plain": [], "ps": ["--ps-protocol"], "compress": ["--compress"], "tls": ["--ssl"]}
OPENSSL = "/opt/homebrew/opt/openssl@3/bin/openssl" if Path("/opt/homebrew/opt/openssl@3/bin/openssl").is_file() else "openssl"
RESULT_MISMATCH_MESSAGES = ("Result content mismatch", "Result length mismatch")
MANIFEST_FIELDS = ("name", "status", "test_file", "result_file", "origin", "note")


class JudgeError(RuntimeError):
    pass


def absolute_path(value):
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def format_command(command):
    return " ".join(shlex.quote(str(item)) for item in command)


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_of(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path=MANIFEST):
    """Return the manifest rows in file order, with paths resolved from the repo root."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
                raise JudgeError(
                    "{} must have the header: {}".format(path, "\t".join(MANIFEST_FIELDS))
                )
            rows = list(reader)
    except OSError as exc:
        raise JudgeError("cannot read manifest {}: {}".format(path, exc))

    cases = []
    seen = set()
    for line_number, row in enumerate(rows, 2):
        name = row["name"]
        if not name or name in seen:
            raise JudgeError("{}:{}: empty or duplicate case name {!r}".format(path, line_number, name))
        if row["status"] not in STATUSES:
            raise JudgeError("{}:{}: unknown status {!r}".format(path, line_number, row["status"]))
        seen.add(name)
        case = dict(row)
        case["test_path"] = REPO_ROOT / row["test_file"]
        case["result_path"] = REPO_ROOT / row["result_file"]
        cases.append(case)
    return cases


def protocol_exclusions(protocol):
    """Names listed in protocol/<protocol>/exclude.tsv (header: name, reason)."""
    path = PROTOCOL_DIR / protocol / "exclude.tsv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row["name"] for row in csv.DictReader(handle, delimiter="\t")]


def select_cases(cases, statuses, names, excluded):
    by_name = {case["name"]: case for case in cases}
    if names:
        missing = sorted(set(names) - set(by_name))
        if missing:
            raise JudgeError("cases not in the manifest: {}".format(", ".join(missing)))
        selected = [by_name[name] for name in names]
    else:
        selected = [case for case in cases if case["status"] in statuses]
    selected = [case for case in selected if case["name"] not in excluded]
    not_runnable = [case["name"] for case in selected if case["status"] not in RUNNABLE_STATUSES]
    if not_runnable:
        raise JudgeError("cases with a non-runnable status: {}".format(", ".join(not_runnable)))
    if not selected:
        raise JudgeError("no cases selected")
    return selected


def run_sdb(arguments, check=True):
    command = [sys.executable, str(SDB_SCRIPT)] + [str(item) for item in arguments]
    print("+ {}".format(format_command(command)), flush=True)
    result = subprocess.run(command, cwd=str(REPO_ROOT), check=False)
    if check and result.returncode != 0:
        raise JudgeError("sdb {} exited with {}".format(arguments[0], result.returncode))
    return result.returncode


def prepare_instance(args):
    run_sdb(["destroy", "--base-dir", args.base_dir])
    # --nodaemon: sdb.py already detaches the process; seekdb's own daemonize
    # step exits the forked child on macOS (observed 2026-10-10 at 7d907abfa).
    start = ["start", "--binary", args.seekdb, "--base-dir", args.base_dir, "--port", args.port,
             "--nodaemon"]
    for parameter in args.parameter:
        start += ["--parameter", parameter]
    run_sdb(start)
    run_sdb(
        [
            "wait-ready",
            "--client", args.obclient,
            "--base-dir", args.base_dir,
            "--host", args.host,
            "--port", args.port,
            "--user", "root",
            "--timeout", READY_TIMEOUT,
        ]
    )
    command = [str(args.obclient), "-h", args.host, "-P", str(args.port), "-uroot", "-A", "-c"]
    print("+ {} < {}".format(format_command(command), INIT_SQL), flush=True)
    with INIT_SQL.open("rb") as sql_input:
        result = subprocess.run(command, cwd=str(DEPLOY_DIR), stdin=sql_input, check=False)
    if result.returncode != 0:
        raise JudgeError("init_public.sql exited with {}".format(result.returncode))


def mysqltest_environment(args):
    environment = os.environ.copy()
    environment["PATH"] = str(args.obclient.parent) + os.pathsep + environment.get("PATH", "")
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


def strip_trailing_horizontal_whitespace(content):
    lines = content.split(b"\n")
    for index, line in enumerate(lines):
        if line.endswith(b"\r"):
            lines[index] = line[:-1].rstrip(b" \t") + b"\r"
        else:
            lines[index] = line.rstrip(b" \t")
    return b"\n".join(lines)


def equal_ignoring_trailing_whitespace(expected_path, actual_path):
    try:
        return strip_trailing_horizontal_whitespace(
            expected_path.read_bytes()
        ) == strip_trailing_horizontal_whitespace(actual_path.read_bytes())
    except OSError:
        return False


def run_case(args, case, tmp_dir, log_dir):
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
        "--test-file={}".format(case["test_path"]),
        "--result-file={}".format(case["result_path"]),
        "--timer-file={}".format(log_dir / "timer"),
        "--tail-lines=20",
    ] + PROTOCOL_FLAGS[args.protocol]
    if args.protocol == "tls":
        command.append("--ssl-ca={}".format(args.wallet / "ca.pem"))
    if args.record:
        command.append("--record")
        case["result_path"].parent.mkdir(parents=True, exist_ok=True)
    reject_file = log_dir / (case["result_path"].stem + ".reject")
    if reject_file.exists():
        reject_file.unlink()

    print("[ RUN      ] {}".format(case["name"]), flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(DEPLOY_DIR),
            env=mysqltest_environment(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=getattr(args, "case_timeout", CASE_TIMEOUT),
            universal_newlines=True,
            check=False,
        )
        exit_code, output = result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired as exc:
        exit_code, output = 124, (exc.stdout or "") + "\n{} seconds timeout\n".format(getattr(args, "case_timeout", CASE_TIMEOUT))
    except OSError as exc:
        exit_code, output = 255, "failed to run mysqltest: {}\n".format(exc)

    whitespace_only = False
    if (
        exit_code != 0
        and not args.record
        and any(message in output for message in RESULT_MISMATCH_MESSAGES)
        and equal_ignoring_trailing_whitespace(case["result_path"], reject_file)
    ):
        exit_code, whitespace_only = 0, True
        reject_file.unlink()

    elapsed = time.monotonic() - started
    if exit_code == 0:
        print("[       OK ] {} ({:.3f}s{})".format(
            case["name"], elapsed, ", trailing whitespace ignored" if whitespace_only else ""), flush=True)
    else:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
        print("[  FAILED  ] {} ({:.3f}s, exit={})".format(case["name"], elapsed, exit_code), flush=True)
    return exit_code, output, elapsed


def make_wallet(directory):
    """Self-signed CA plus a server certificate for 127.0.0.1, in seekdb's wallet layout."""
    directory.mkdir(parents=True, exist_ok=True)
    if all((directory / name).is_file() for name in ("ca.pem", "server-cert.pem", "server-key.pem")):
        return directory
    (directory / "san.ext").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\n", encoding="utf-8")
    steps = [
        [OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
         "-subj", "/CN=seekdb-judge-ca", "-keyout", "ca-key.pem", "-out", "ca.pem"],
        [OPENSSL, "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
         "-keyout", "server-key.pem", "-out", "server.csr"],
        [OPENSSL, "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca-key.pem",
         "-CAcreateserial", "-days", "3650", "-extfile", "san.ext", "-out", "server-cert.pem"],
    ]
    for step in steps:
        result = subprocess.run(step, cwd=str(directory), stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, universal_newlines=True, check=False)
        if result.returncode != 0:
            raise JudgeError("openssl failed: {}\n{}".format(format_command(step), result.stderr))
    return directory


def save_failure(args, case_name, output):
    destination = args.work_dir / "failures" / case_name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "mysqltest.log").write_text(output, encoding="utf-8")
    log_dir = args.base_dir / "log"
    if log_dir.is_dir() and not (destination / "seekdb_log").exists():
        shutil.copytree(str(log_dir), str(destination / "seekdb_log"))


def write_json(path, payload):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def command_run(args):
    args.seekdb = absolute_path(args.seekdb)
    client_dir = absolute_path(args.client_dir)
    args.obclient = client_dir / "obclient"
    args.mysqltest = client_dir / "mysqltest"
    args.work_dir = absolute_path(args.work_dir)
    args.base_dir = absolute_path(args.base_dir) if args.base_dir else args.work_dir / "instance"
    for tool in (args.seekdb, args.obclient, args.mysqltest):
        if not tool.is_file():
            raise JudgeError("not found: {}".format(tool))

    statuses = [item for item in args.status.split(",") if item]
    names = [item for item in args.cases.split(",") if item] if args.cases else []
    excluded = set(item for item in args.exclude.split(",") if item) if args.exclude else set()
    if args.protocol != "plain":
        # Cases the original itself cannot run under this protocol are listed,
        # with the reason, in protocol/<protocol>/exclude.tsv.
        excluded |= set(protocol_exclusions(args.protocol))
    cases = select_cases(load_manifest(), statuses, names, excluded)
    if args.record and not names:
        raise JudgeError("--record needs an explicit --cases list (record only from the original binary)")
    if args.protocol != "plain":
        # Where the original's output under this protocol differs from the
        # plain-protocol golden (mysqltest renders results differently), the
        # expected output is protocol/<protocol>/<name>.result, recorded from
        # the original under the same protocol.
        for case in cases:
            override = PROTOCOL_DIR / args.protocol / (case["name"] + ".result")
            if args.record or override.is_file():
                case["result_path"] = override

    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.protocol == "tls":
        # seekdb enables TLS at startup when ssl_client_authentication is on
        # and wallet/{ca,server-cert,server-key}.pem exist in its working directory.
        args.wallet = make_wallet(args.work_dir / "wallet")
        os.environ["SDB_WALLET_DIR"] = str(args.wallet)
        args.parameter = args.parameter + ["ssl_client_authentication=True"]
    tmp_dir = args.work_dir / "tmp"
    log_dir = args.work_dir / "mysqltest_log"
    tmp_dir.mkdir(exist_ok=True)
    log_dir.mkdir(exist_ok=True)

    report = {
        "binary": str(args.seekdb),
        "binary_sha256": sha256_of(args.seekdb),
        "protocol": args.protocol,
        "record": args.record,
        "parameters": args.parameter,
        "started": utc_now(),
        "cases": [],
        "error": None,
    }
    report["isolated"] = not args.shared_instance
    report["jobs"] = 1 if args.shared_instance else args.jobs
    try:
        if not args.shared_instance:
            # Default: every case gets a fresh instance, so its outcome does not
            # depend on which cases ran before it (DDL left behind, global
            # settings, compactions) or on running a subset.
            run_isolated(args, cases, report)
        else:
            prepare_instance(args)
            for index, case in enumerate(cases):
                exit_code, output, elapsed = run_case(args, case, tmp_dir, log_dir)
                report["cases"].append(
                    {"name": case["name"], "status": case["status"], "ok": exit_code == 0,
                     "exit": exit_code, "seconds": round(elapsed, 3)}
                )
                if exit_code != 0:
                    save_failure(args, case["name"], output)
                    if index + 1 < len(cases):
                        prepare_instance(args)
    except (JudgeError, OSError) as exc:
        report["error"] = str(exc)
        print("[judge][ERROR] {}".format(exc), file=sys.stderr, flush=True)
        log_dir = args.base_dir / "log"
        keep = args.work_dir / "failures" / "infrastructure" / "seekdb_log"
        if log_dir.is_dir() and not keep.exists():
            shutil.copytree(str(log_dir), str(keep))
    finally:
        if run_sdb(["destroy", "--base-dir", args.base_dir], check=False) != 0:
            report["error"] = (report["error"] + "; " if report["error"] else "") + "destroy failed"
        report["finished"] = utc_now()
        report["passed"] = sum(1 for item in report["cases"] if item["ok"])
        report["failed"] = [item["name"] for item in report["cases"] if not item["ok"]]
        report["selected"] = len(cases)
        write_json(args.work_dir / "judge_result.json", report)

    print("judge: selected={} passed={} failed={} error={}".format(
        len(cases), report["passed"], len(report["failed"]), report["error"]), flush=True)
    complete = len(report["cases"]) == len(cases)
    return 0 if complete and not report["failed"] and report["error"] is None else 1


def run_isolated(args, cases, report):
    """Run each case on a fresh instance, spread over args.jobs workers.

    Worker i uses port args.port + i and its own base, tmp and log directories.
    A failure to bring an instance up counts against that case only, so a
    broken binary yields failed cases rather than an aborted run.
    """
    pending = queue.Queue()
    for case in cases:
        pending.put(case)
    lock = threading.Lock()
    workers = []
    for index in range(max(1, args.jobs)):
        worker = copy.copy(args)
        worker.port = args.port + index
        suffix = "" if args.jobs <= 1 else "-{}".format(index)
        worker.base_dir = args.base_dir if args.jobs <= 1 else args.work_dir / ("instance" + suffix)
        tmp_dir = args.work_dir / ("tmp" + suffix)
        log_dir = args.work_dir / ("mysqltest_log" + suffix)
        tmp_dir.mkdir(exist_ok=True)
        log_dir.mkdir(exist_ok=True)
        workers.append((worker, tmp_dir, log_dir))

    def work(worker, tmp_dir, log_dir):
        while True:
            try:
                case = pending.get_nowait()
            except queue.Empty:
                return
            try:
                prepare_instance(worker)
                exit_code, output, elapsed = run_case(worker, case, tmp_dir, log_dir)
            except (JudgeError, OSError) as exc:
                exit_code, output, elapsed = 255, "instance setup failed: {}\n".format(exc), 0.0
                print("[  FAILED  ] {} (setup: {})".format(case["name"], exc), flush=True)
            with lock:
                report["cases"].append(
                    {"name": case["name"], "status": case["status"], "ok": exit_code == 0,
                     "exit": exit_code, "seconds": round(elapsed, 3)}
                )
            if exit_code != 0:
                save_failure(worker, case["name"], output)

    threads = [threading.Thread(target=work, args=item) for item in workers]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for worker, _, _ in workers:
            run_sdb(["destroy", "--base-dir", worker.base_dir], check=False)
    order = {case["name"]: position for position, case in enumerate(cases)}
    report["cases"].sort(key=lambda item: order[item["name"]])


def command_try(args):
    """Run one arbitrary .test file on a fresh instance (for authoring new judge cases)."""
    args.seekdb = absolute_path(args.seekdb)
    client_dir = absolute_path(args.client_dir)
    args.obclient = client_dir / "obclient"
    args.mysqltest = client_dir / "mysqltest"
    args.work_dir = absolute_path(args.work_dir)
    args.base_dir = args.work_dir / "instance"
    args.parameter = []
    args.protocol = "plain"
    test_path = absolute_path(args.test)
    result_path = absolute_path(args.result) if args.result else args.work_dir / (test_path.stem + ".result")
    args.record = args.record or not result_path.is_file()
    case = {"name": test_path.stem, "status": "scenario", "test_path": test_path, "result_path": result_path}
    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "tmp").mkdir(exist_ok=True)
    (args.work_dir / "mysqltest_log").mkdir(exist_ok=True)
    try:
        prepare_instance(args)
        exit_code, output, _ = run_case(args, case, args.work_dir / "tmp", args.work_dir / "mysqltest_log")
    finally:
        run_sdb(["destroy", "--base-dir", args.base_dir], check=False)
    if exit_code == 0:
        print("{} {}".format("recorded" if args.record else "matched", result_path))
    return 0 if exit_code == 0 else 1


def command_list(args):
    cases = load_manifest()
    counts = {}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    for status in STATUSES:
        print("{:12s} {}".format(status, counts.get(status, 0)))
    print("{:12s} {}".format("total", len(cases)))
    return 0


def create_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="run judge cases against one seekdb binary")
    run.add_argument("--seekdb", required=True, help="seekdb executable (original or port)")
    run.add_argument("--work-dir", required=True, help="output directory for this run")
    run.add_argument("--client-dir", default=str(DEFAULT_CLIENT_DIR), help="directory with obclient and mysqltest")
    run.add_argument("--base-dir", help="seekdb base directory (default: WORK_DIR/instance)")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=2881)
    run.add_argument("--parameter", action="append", default=[], help="seekdb parameter, repeatable")
    run.add_argument("--protocol", choices=sorted(PROTOCOL_FLAGS), default="plain")
    run.add_argument("--status", default=",".join(RUNNABLE_STATUSES), help="comma-separated statuses to run")
    run.add_argument("--cases", help="comma-separated case names (overrides --status)")
    run.add_argument("--exclude", help="comma-separated case names to skip")
    run.add_argument("--record", action="store_true", help="re-record result files (original binary only)")
    run.add_argument("--case-timeout", type=int, default=CASE_TIMEOUT, help="seconds per case (default 3600)")
    run.add_argument("--shared-instance", action="store_true",
                     help="run all cases on one instance, as CI does (default: a fresh instance per case)")
    run.add_argument("--jobs", type=int, default=1,
                     help="parallel workers for fresh-instance runs; worker i uses port PORT+i")
    run.set_defaults(handler=command_run)

    trial = subparsers.add_parser("try", help="run one .test file on a fresh instance (authoring aid)")
    trial.add_argument("--seekdb", required=True)
    trial.add_argument("--work-dir", required=True)
    trial.add_argument("--test", required=True, help=".test file to run")
    trial.add_argument("--result", help="expected .result (recorded if missing or with --record)")
    trial.add_argument("--record", action="store_true")
    trial.add_argument("--client-dir", default=str(DEFAULT_CLIENT_DIR))
    trial.add_argument("--host", default="127.0.0.1")
    trial.add_argument("--port", type=int, default=2881)
    trial.set_defaults(handler=command_try)

    listing = subparsers.add_parser("list", help="count manifest cases by status")
    listing.set_defaults(handler=command_list)
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    try:
        return args.handler(args)
    except JudgeError as exc:
        print("[judge][ERROR] {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
