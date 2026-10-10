#!/usr/bin/env python3
"""Process-lifecycle scenarios for the parity judge: crash recovery, restarts, persistence.

mysqltest cannot kill or restart the server, so these scenarios use a small
line-based script format and run through the same public interface as the rest
of the judge (process flags, signals, MySQL protocol via obclient):

  start [k=v ...]        fresh instance (optional seekdb parameters), then init_public.sql
  restart [k=v ...]      start again on the same base directory, wait until ready
  stop                   graceful stop (SIGTERM via sdb.py)
  kill9                  SIGKILL the server process
  open NAME              open a persistent session (e.g. to hold an open transaction)
  NAME: SQL              send SQL on a persistent session; output is not recorded
  close NAME             close a persistent session
  sql: SQL               run SQL in a fresh session and record its output and errors
  sql[cols=a,b]: SQL     same, recording only the named result columns
  sleep SECONDS          wait (use only where the original test does the same)
  exec: NAME             build runner/NAME.c against deps' libobclnt, run it with
                         HOST PORT USER PASSWORD DATABASE, record its output
  # ...                  comment

The recorded transcript of a scenario is compared byte for byte (ignoring
trailing whitespace) with suites/lifecycle/<name>.result, which is recorded from
the original binary with --record.

  lifecycle.py run --seekdb BIN --work-dir DIR [--scenarios a,b] [--record]
"""

from __future__ import print_function

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge  # noqa: E402  (shares paths, sdb helpers and the init SQL)

SUITE_DIR = judge.JUDGE_DIR / "suites" / "lifecycle"
USER, PASSWORD, DATABASE = "admin", "admin", "test"
STOP_WAIT = 60.0


class Scenario(object):
    def __init__(self, args, name):
        self.args = args
        self.name = name
        self.sessions = {}
        self.transcript = []

    # --- instance control -------------------------------------------------
    def start(self, parameters, fresh):
        if fresh:
            judge.run_sdb(["destroy", "--base-dir", self.args.base_dir])
        command = ["start", "--binary", self.args.seekdb, "--base-dir", self.args.base_dir,
                   "--port", self.args.port, "--nodaemon"]
        for parameter in parameters:
            command += ["--parameter", parameter]
        judge.run_sdb(command)
        judge.run_sdb(["wait-ready", "--client", self.args.obclient, "--base-dir", self.args.base_dir,
                       "--host", self.args.host, "--port", self.args.port, "--user", "root",
                       "--timeout", judge.READY_TIMEOUT])
        if fresh:
            command = [str(self.args.obclient), "-h", self.args.host, "-P", str(self.args.port),
                       "-uroot", "-A", "-c"]
            with judge.INIT_SQL.open("rb") as sql_input:
                if subprocess.run(command, stdin=sql_input, check=False).returncode != 0:
                    raise judge.JudgeError("init_public.sql failed")

    def server_pid(self):
        pid_file = self.args.base_dir / "run" / "seekdb.pid"
        return int(pid_file.read_text().strip())

    def kill9(self):
        pid = self.server_pid()
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + STOP_WAIT
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.2)
        raise judge.JudgeError("server pid {} survived SIGKILL".format(pid))

    def stop(self):
        judge.run_sdb(["stop", "--base-dir", self.args.base_dir])

    # --- SQL --------------------------------------------------------------
    def client_command(self):
        return [str(self.args.obclient), "-h", self.args.host, "-P", str(self.args.port),
                "-u{}".format(USER), "-p{}".format(PASSWORD), "-D{}".format(DATABASE), "-A", "-c"]

    def sql(self, statement, columns=None):
        result = subprocess.run(self.client_command() + ["-e", statement], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True, check=False)
        output = result.stdout
        if columns and result.returncode == 0 and output:
            # Keep only the named columns of a tab-separated result set, so
            # host-specific columns (IPs, ports) never reach the transcript.
            lines = output.rstrip("\n").split("\n")
            header = lines[0].split("\t")
            missing = [name for name in columns if name not in header]
            if missing:
                raise judge.JudgeError("columns not in result: {}".format(", ".join(missing)))
            keep = [header.index(name) for name in columns]
            output = "".join("\t".join(line.split("\t")[i] for i in keep) + "\n" for line in lines)
        self.transcript.append("> {}\n{}".format(statement, output))
        if result.returncode != 0:
            self.transcript.append("[exit {}]\n".format(result.returncode))

    def exec_helper(self, name):
        """Build (once per run) and run a judge client from runner/<name>.c; record its output."""
        source = judge.JUDGE_DIR / "runner" / (name + ".c")
        binary = self.args.work_dir / "bin" / name
        if not binary.is_file():
            binary.parent.mkdir(parents=True, exist_ok=True)
            obclient = judge.REPO_ROOT / "deps" / "3rd" / "obclient"
            build = ["cc", "-O1", "-I", str(obclient / "include"), "-o", str(binary), str(source),
                     "-L", str(obclient / "lib"), "-lobclnt", "-Wl,-rpath," + str(obclient / "lib")]
            result = subprocess.run(build, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, check=False)
            if result.returncode != 0:
                raise judge.JudgeError("building {} failed:\n{}".format(name, result.stdout))
        result = subprocess.run([str(binary), self.args.host, str(self.args.port), USER, PASSWORD, DATABASE],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, check=False)
        self.transcript.append("[exec {}]\n{}".format(name, result.stdout))
        if result.returncode != 0:
            self.transcript.append("[exit {}]\n".format(result.returncode))

    def open_session(self, name):
        if name in self.sessions:
            raise judge.JudgeError("session {} already open".format(name))
        self.sessions[name] = subprocess.Popen(self.client_command(), stdin=subprocess.PIPE,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               universal_newlines=True)

    def send(self, name, statement):
        session = self.sessions.get(name)
        if session is None:
            raise judge.JudgeError("session {} is not open".format(name))
        session.stdin.write(statement.rstrip(";") + ";\n")
        session.stdin.flush()
        time.sleep(0.5)  # let the statement reach the server before the next step

    def close_all(self):
        for session in self.sessions.values():
            try:
                session.stdin.close()
            except OSError:
                pass
            try:
                session.wait(timeout=10)
            except subprocess.TimeoutExpired:
                session.kill()
        self.sessions = {}

    # --- script -----------------------------------------------------------
    def run(self, path):
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word, _, rest = line.partition(" ")
            try:
                if line.startswith("sql:"):
                    self.sql(line[4:].strip())
                elif line.startswith("sql[cols="):
                    spec, _, statement = line.partition("]:")
                    self.sql(statement.strip(), columns=spec[len("sql[cols="):].split(","))
                elif word in ("start", "restart"):
                    self.start(rest.split(), fresh=(word == "start"))
                    self.transcript.append("[{}]\n".format(word))
                elif word == "stop":
                    self.stop()
                    self.transcript.append("[stop]\n")
                elif word == "kill9":
                    self.kill9()
                    self.transcript.append("[kill9]\n")
                elif word == "open":
                    self.open_session(rest.strip())
                elif word == "close":
                    session = self.sessions.pop(rest.strip())
                    session.stdin.close()
                    session.wait(timeout=30)
                elif word == "sleep":
                    time.sleep(float(rest))
                elif word == "exec:":
                    self.exec_helper(rest.strip())
                elif word.endswith(":") and word[:-1] in self.sessions:
                    self.send(word[:-1], rest)
                else:
                    raise judge.JudgeError("unknown step")
            except (judge.JudgeError, OSError, ValueError, KeyError) as exc:
                raise judge.JudgeError("{}:{}: {} ({})".format(path.name, number, line, exc))
        return "".join(self.transcript)


def command_run(args):
    args.seekdb = judge.absolute_path(args.seekdb)
    client_dir = judge.absolute_path(args.client_dir)
    args.obclient = client_dir / "obclient"
    args.work_dir = judge.absolute_path(args.work_dir)
    args.base_dir = args.work_dir / "instance"
    args.work_dir.mkdir(parents=True, exist_ok=True)
    available = sorted(path.stem for path in SUITE_DIR.glob("*.lifecycle"))
    names = [item for item in args.scenarios.split(",") if item] if args.scenarios else available
    missing = sorted(set(names) - set(available))
    if missing:
        raise judge.JudgeError("unknown scenarios: {}".format(", ".join(missing)))

    report = {"binary": str(args.seekdb), "binary_sha256": judge.sha256_of(args.seekdb),
              "record": args.record, "started": judge.utc_now(), "scenarios": []}
    for name in names:
        print("[ RUN      ] {}".format(name), flush=True)
        scenario = Scenario(args, name)
        started = time.monotonic()
        error = None
        try:
            transcript = scenario.run(SUITE_DIR / (name + ".lifecycle"))
        except judge.JudgeError as exc:
            transcript, error = "".join(scenario.transcript), str(exc)
            log_dir = args.base_dir / "log"
            if log_dir.is_dir():
                keep = args.work_dir / "failures" / name
                if keep.exists():
                    shutil.rmtree(str(keep))
                shutil.copytree(str(log_dir), str(keep))
        finally:
            scenario.close_all()
            judge.run_sdb(["destroy", "--base-dir", args.base_dir], check=False)
        actual = args.work_dir / (name + ".actual")
        actual.write_text(transcript, encoding="utf-8")
        expected = SUITE_DIR / (name + ".result")
        if error is None and args.record:
            expected.write_text(transcript, encoding="utf-8")
            ok = True
        elif error is None:
            ok = expected.is_file() and judge.equal_ignoring_trailing_whitespace(expected, actual)
        else:
            ok = False
        elapsed = time.monotonic() - started
        print("[{}] {} ({:.1f}s){}".format("       OK " if ok else "  FAILED  ", name, elapsed,
                                          "" if error is None else " " + error), flush=True)
        report["scenarios"].append({"name": name, "ok": ok, "error": error, "seconds": round(elapsed, 1)})
    report["finished"] = judge.utc_now()
    report["failed"] = [item["name"] for item in report["scenarios"] if not item["ok"]]
    judge.write_json(args.work_dir / "lifecycle_result.json", report)
    print("lifecycle: selected={} failed={}".format(len(names), len(report["failed"])), flush=True)
    return 0 if not report["failed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run")
    run.add_argument("--seekdb", required=True)
    run.add_argument("--work-dir", required=True)
    run.add_argument("--client-dir", default=str(judge.DEFAULT_CLIENT_DIR))
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=2881)
    run.add_argument("--scenarios", help="comma-separated scenario names (default: all)")
    run.add_argument("--record", action="store_true", help="record transcripts (original binary only)")
    run.set_defaults(handler=command_run)
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    try:
        return args.handler(args)
    except judge.JudgeError as exc:
        print("[lifecycle][ERROR] {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
