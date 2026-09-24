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
import contextlib
import hashlib
import io
import json
from pathlib import Path
import random
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

FAMILY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FAMILY_DIR))

import recording_common as common
import parallel_slices
import startup_connect
import sysbench_parity

runner = common.runner
REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen
LISTS_DIR = common.REPO_ROOT / "migration" / "judge" / "lists"


def guarded_run(command, *args, **kwargs):
    if Path(str(command[0])).name != "git":
        raise AssertionError("unexpected command: {}".format(command))
    return REAL_RUN(command, *args, **kwargs)


def guarded_popen(command, *args, **kwargs):
    if Path(str(command[0])).name != "git":
        raise AssertionError("unexpected process: {}".format(command))
    return REAL_POPEN(command, *args, **kwargs)


def guarded_connection(address, *args, **kwargs):
    raise AssertionError("unexpected connection to {}".format(address))


def quiet(function, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return function(*args)


def compare(left, right):
    report = Path(tempfile.mkdtemp(prefix="compare-")) / "report.json"
    code = quiet(
        runner.main,
        ["compare", "--left", str(left), "--right", str(right), "--out", str(report)],
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


def manifest_of(directory):
    return json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="concurrency-offline-"))
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        for name in ("seekdb", "obclient", "mysqltest"):
            path = self.binaries / name
            path.write_bytes(("stand-in " + name + "\n").encode("utf-8"))
            path.chmod(0o755)
        self.checked_ports = []
        self.busy_ports = set()
        patches = [
            mock.patch("subprocess.run", guarded_run),
            mock.patch("subprocess.Popen", guarded_popen),
            mock.patch("socket.create_connection", guarded_connection),
            mock.patch.object(common, "port_problem", self.port_problem),
            mock.patch.dict("os.environ", {runner.INSTANCE_SAVE_ENVIRONMENT: ""}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def port_problem(self, port):
        self.checked_ports.append(port)
        if port in self.busy_ports:
            return "something already answers on port {} before seekdb starts: stand-in".format(port)
        return None

    def path(self, *parts):
        return self.root.joinpath(*parts)

    def fresh(self, prefix):
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(self.root)))


def runner_manifest(sandbox, cases, slice_index, slice_count, case_list, **overrides):
    manifest = {
        "finished": True,
        "seekdb": str(sandbox.binaries / "seekdb"),
        "seekdb_sha256": runner.file_sha256(sandbox.binaries / "seekdb"),
        "mysqltest": str(sandbox.binaries / "mysqltest"),
        "mysqltest_sha256": runner.file_sha256(sandbox.binaries / "mysqltest"),
        "obclient": str(sandbox.binaries / "obclient"),
        "obclient_sha256": runner.file_sha256(sandbox.binaries / "obclient"),
        "init_sql": str(parallel_slices.INIT_FILES["full"][0]),
        "init_sql_sha256": runner.file_sha256(parallel_slices.INIT_FILES["full"][0]),
        "init_user_sql": str(parallel_slices.INIT_FILES["full"][1]),
        "init_user_sql_sha256": runner.file_sha256(parallel_slices.INIT_FILES["full"][1]),
        "sdb_sha256": runner.file_sha256(common.SDB_PATH),
        "runner_sha256": runner.file_sha256(common.RUNNER_PATH),
        "repo_head": "0" * 40,
        "tools_deploy_tree": "1" * 40,
        "tools_deploy_status": "",
        "cases": list(cases),
        "slice_index": slice_index,
        "slice_count": slice_count,
        "case_list": str(case_list) if case_list else None,
        "max_retries": 0,
        "fresh_instance_per_case": False,
        "work_dir": "/tmp/work-{}".format(slice_index),
        "recorded": "<case>.result is the output of mysqltest --record when it exited 0",
        "success": True,
        "failed_cases": [],
        "retried_cases": {},
        "error": None,
        "init_failed_statements": 0,
        "outcomes": dict(
            (case, {"exit_code": 0, "recorded": True, "partial": False}) for case in cases
        ),
    }
    manifest.update(overrides)
    return manifest


def case_content(case):
    return "select '{0}';\n{0}\n".format(case).encode("utf-8")


def write_recording(directory, manifest, contents=None):
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for case in manifest["cases"]:
        outcome = manifest.get("outcomes", {}).get(case)
        if outcome is None:
            continue
        suffix = ".result" if outcome.get("recorded") else ".partial" if outcome.get("partial") else None
        if suffix:
            data = (contents or {}).get(case, case_content(case))
            (directory / (case + suffix)).write_bytes(data)


def selection(case_list):
    cases = runner.discover_cases(common.REPO_ROOT)
    if case_list is not None:
        cases = runner.load_case_list(case_list, cases)
    return [case.name for case in cases]


class ParallelSlicesMergeTest(Sandbox):
    def slices(self, case_list, count=4, change=None):
        cases = selection(case_list)
        parent = self.fresh("slices-")
        directories = []
        for index in range(count):
            manifest = runner_manifest(self, cases[index::count], index, count, case_list)
            if change is not None:
                change(index, manifest)
            directory = parent / "slice_{}".format(index)
            write_recording(directory, manifest)
            directories.append(directory)
        return cases, directories

    def sequential(self, case_list):
        cases = selection(case_list)
        directory = self.fresh("sequential-") / "rec"
        write_recording(directory, runner_manifest(self, cases, 0, 1, case_list))
        return directory

    def test_merged_recording_matches_a_one_slice_recording(self):
        for name in ("send-reap.txt", "multi-connection.txt", None):
            with self.subTest(case_list=name):
                case_list = LISTS_DIR / name if name else None
                cases, directories = self.slices(case_list)
                out = self.fresh("merged-") / "rec"
                merged = parallel_slices.merge(list(reversed(directories)), out)
                self.assertEqual(merged["cases"], cases)
                self.assertTrue(merged["finished"])
                self.assertTrue(merged["success"])
                self.assertEqual(merged["slice_count"], 4)
                self.assertIsNone(merged["slice_index"])
                self.assertEqual(merged["repo_head"], "0" * 40)
                self.assertEqual([entry["repo_head"] for entry in merged["merged_slices"]], ["0" * 40] * 4)
                self.assertEqual(
                    sorted(p.name for p in out.iterdir()),
                    sorted(["manifest.json"] + [case + ".result" for case in cases]),
                )
                code, report = compare(out, self.sequential(case_list))
                self.assertEqual(code, 0, report["recording_problems"])
                self.assertEqual(report["summary"]["identical"], len(cases))
                self.assertEqual(report["recording_notes"], [])

    def test_slices_at_different_repo_heads_merge_with_the_heads_kept_per_slice(self):
        case_list = LISTS_DIR / "send-reap.txt"

        def later_head(index, manifest):
            if index >= 2:
                manifest["repo_head"] = "2" * 40

        cases, directories = self.slices(case_list, change=later_head)
        out = self.fresh("merged-") / "rec"
        merged = parallel_slices.merge(directories, out)
        self.assertTrue(merged["success"])
        self.assertIsNone(merged["repo_head"])
        self.assertEqual(
            [entry["repo_head"] for entry in merged["merged_slices"]],
            ["0" * 40, "0" * 40, "2" * 40, "2" * 40],
        )
        code, report = compare(out, self.sequential(case_list))
        self.assertEqual(code, 0, report["recording_problems"])
        self.assertEqual(report["summary"]["identical"], len(cases))
        self.assertEqual(report["recording_notes"], ["repo_head differs: left None, right {}".format("0" * 40)])

    def test_content_difference_is_reported(self):
        case_list = LISTS_DIR / "send-reap.txt"
        cases, directories = self.slices(case_list)
        target = directories[2] / (cases[2] + ".result")
        target.write_bytes(target.read_bytes() + b"extra\n")
        out = self.fresh("merged-") / "rec"
        parallel_slices.merge(directories, out)
        code, report = compare(out, self.sequential(case_list))
        self.assertEqual(code, 1)
        self.assertEqual(report["summary"]["different"], 1)
        self.assertEqual(
            [entry["case"] for entry in report["cases"] if entry["status"] == "different"],
            [cases[2]],
        )

    def refused(self, change=None, count=4, directories=None, message=""):
        if directories is None:
            _, directories = self.slices(LISTS_DIR / "send-reap.txt", count, change)
        with self.assertRaises(parallel_slices.MergeRefused) as caught:
            parallel_slices.merge(directories, self.fresh("refused-") / "rec")
        self.assertIn(message, str(caught.exception))
        return str(caught.exception)

    def test_refusals(self):
        def other_binary(index, manifest):
            if index == 1:
                manifest["seekdb_sha256"] = "f" * 64

        def other_input(index, manifest):
            if index == 3:
                manifest["init_sql_sha256"] = "e" * 64

        def other_mode(index, manifest):
            if index == 0:
                manifest["fresh_instance_per_case"] = True

        def other_runner(index, manifest):
            if index == 2:
                manifest["runner_sha256"] = "d" * 64

        def other_deploy_tree(index, manifest):
            if index == 1:
                manifest["tools_deploy_tree"] = "3" * 40

        def duplicate_index(index, manifest):
            if index == 3:
                manifest["slice_index"] = 2

        def wrong_count(index, manifest):
            manifest["slice_count"] = 5

        def swapped_cases(index, manifest):
            if index == 0:
                manifest["cases"] = list(reversed(manifest["cases"]))

        self.refused(other_binary, message="seekdb_sha256 differs")
        self.refused(other_input, message="init_sql_sha256 differs")
        self.refused(other_mode, message="fresh_instance_per_case differs")
        self.refused(other_runner, message="runner_sha256 differs")
        self.refused(other_deploy_tree, message="tools_deploy_tree differs")
        self.refused(duplicate_index, message="slice indexes")
        self.refused(wrong_count, message="slice_count")
        self.refused(swapped_cases, message="not the runner's own selection")
        _, directories = self.slices(LISTS_DIR / "send-reap.txt")
        self.refused(directories=directories[:3], message="slice_count")
        (directories[0] / "stray.txt").write_text("x")
        self.refused(directories=directories, message="does not account for")
        (directories[0] / "stray.txt").unlink()
        manifest = manifest_of(directories[1])
        (directories[1] / (manifest["cases"][0] + ".result")).unlink()
        self.refused(directories=directories, message="its outcome says")
        _, directories = self.slices(LISTS_DIR / "send-reap.txt")
        manifest = manifest_of(directories[1])
        manifest["outcomes"][manifest["cases"][0]] = {"exit_code": 1, "recorded": False, "partial": True}
        (directories[1] / "manifest.json").write_text(json.dumps(manifest))
        self.refused(directories=directories, message="its outcome says")
        _, directories = self.slices(LISTS_DIR / "send-reap.txt")
        used = self.fresh("used-")
        (used / "old").write_text("x")
        with self.assertRaises(parallel_slices.MergeRefused) as caught:
            parallel_slices.merge(directories, used)
        self.assertIn("not new or empty", str(caught.exception))

    def test_slice_problems_are_carried_into_the_merged_manifest(self):
        cases = selection(LISTS_DIR / "send-reap.txt")
        variants = {
            "error": lambda index, manifest: manifest.update(
                {"error": "wait for seekdb exited with 1", "success": False}
            ) if index == 2 else None,
            "failed": lambda index, manifest: manifest.update(
                {
                    "failed_cases": [manifest["cases"][0]],
                    "success": False,
                    "outcomes": dict(
                        manifest["outcomes"],
                        **{manifest["cases"][0]: {"exit_code": 1, "recorded": False, "partial": True}}
                    ),
                }
            ) if index == 1 else None,
            "unfinished": lambda index, manifest: [
                manifest.pop(key) for key in ("success", "failed_cases", "retried_cases", "error", "init_failed_statements", "outcomes")
            ] + [manifest.update({"finished": False})] if index == 3 else None,
            "retried": lambda index, manifest: manifest.update(
                {"max_retries": 3, "retried_cases": {manifest["cases"][0]: [1, 0]}}
            ) if index == 0 else None,
        }
        expected_problems = {
            "error": "left recording failed: slice 2: wait for seekdb exited with 1",
            "failed": "left recording has failed cases: {}".format(cases[1]),
            "unfinished": "left recording did not finish",
            "retried": "left recording allowed retries: max_retries=3",
        }
        for name, change in variants.items():
            with self.subTest(variant=name):
                _, directories = self.slices(LISTS_DIR / "send-reap.txt", change=change)
                out = self.fresh("merged-") / "rec"
                merged = parallel_slices.merge(directories, out)
                self.assertEqual(merged["success"], name == "retried")
                code, report = compare(out, self.sequential(LISTS_DIR / "send-reap.txt"))
                self.assertEqual(code, 1)
                self.assertIn(expected_problems[name], report["recording_problems"])


class FakeRunnerProcess(object):
    events = []
    lock = threading.Lock()
    change = None

    def __init__(self, command, cwd=None, env=None, stdout=None, stderr=None):
        self.command = [str(item) for item in command]
        self.env = env
        values = {}
        for index, item in enumerate(self.command):
            if item.startswith("--") and index + 1 < len(self.command) and not self.command[index + 1].startswith("--"):
                values[item] = self.command[index + 1]
        self.values = values
        self.slice_index = int(values["--slice-index"])
        with FakeRunnerProcess.lock:
            FakeRunnerProcess.events.append(("start", self.slice_index))
        stdout.write("fake runner {}\n".format(self.slice_index).encode("utf-8"))
        self.sandbox = FakeRunnerProcess.sandbox

    def wait(self):
        case_list = Path(self.values["--case-list"]) if "--case-list" in self.values else None
        count = int(self.values["--slice-count"])
        cases = selection(case_list)[self.slice_index::count]
        manifest = runner_manifest(self.sandbox, cases, self.slice_index, count, case_list)
        manifest.update(
            {
                "seekdb": self.values["--seekdb"],
                "obclient": self.values["--obclient"],
                "mysqltest": self.values["--mysqltest"],
                "init_sql": self.values["--init-sql"],
                "init_sql_sha256": runner.file_sha256(Path(self.values["--init-sql"])),
                "init_user_sql": self.values["--init-user-sql"],
                "init_user_sql_sha256": runner.file_sha256(Path(self.values["--init-user-sql"])),
                "fresh_instance_per_case": "--fresh-instance-per-case" in self.command,
                "max_retries": int(self.values["--max-retries"]),
            }
        )
        if FakeRunnerProcess.change is not None:
            FakeRunnerProcess.change(self.slice_index, manifest)
        write_recording(Path(self.values["--record-dir"]), manifest)
        with FakeRunnerProcess.lock:
            FakeRunnerProcess.events.append(("finish", self.slice_index))
        return 0 if manifest["success"] else 1


class ParallelSlicesRunTest(Sandbox):
    def run_slices(self, extra, change=None):
        FakeRunnerProcess.events = []
        FakeRunnerProcess.sandbox = self
        FakeRunnerProcess.change = change
        created = []

        def popen(command, **kwargs):
            process = FakeRunnerProcess(command, **kwargs)
            created.append(process)
            return process

        argv = [
            "run",
            "--seekdb", str(self.binaries / "seekdb"),
            "--obclient", str(self.binaries / "obclient"),
            "--mysqltest", str(self.binaries / "mysqltest"),
            "--out-dir", str(self.path("out")),
        ] + extra
        with mock.patch.object(parallel_slices.subprocess, "Popen", popen), mock.patch.dict(
            "os.environ", {runner.INSTANCE_SAVE_ENVIRONMENT: str(self.path("env-save"))}
        ):
            code = quiet(parallel_slices.main, argv)
        return code, created

    def test_parallel_run_builds_four_runner_commands_and_merges(self):
        case_list = LISTS_DIR / "send-reap.txt"
        code, created = self.run_slices(
            ["--init", "reduced", "--case-list", str(case_list), "--save-instance-dir", str(self.path("save"))]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(created), 4)
        self.assertEqual([event[0] for event in FakeRunnerProcess.events[:4]], ["start"] * 4)
        ports = [process.values["--port"] for process in created]
        self.assertEqual(ports, ["3891", "3892", "3893", "3894"])
        for index, process in enumerate(created):
            command = process.command
            self.assertEqual(command[1], str(common.RUNNER_PATH))
            self.assertEqual(command[2], "run")
            self.assertEqual(process.values["--slice-index"], str(index))
            self.assertEqual(process.values["--slice-count"], "4")
            self.assertEqual(process.values["--max-retries"], "0")
            self.assertIn("--no-ignore-trailing-whitespace", command)
            self.assertNotIn("--ignore-trailing-whitespace", command)
            self.assertEqual(process.values["--record-dir"], str(self.path("out", "slice_{}".format(index), "rec")))
            self.assertEqual(process.values["--base-dir"], str(self.path("out", "slice_{}".format(index), "base")))
            self.assertEqual(process.values["--work-dir"], str(self.path("out", "slice_{}".format(index), "work")))
            self.assertEqual(process.values["--init-sql"], str(parallel_slices.INIT_FILES["reduced"][0]))
            self.assertEqual(process.values["--init-user-sql"], str(parallel_slices.INIT_FILES["reduced"][1]))
            self.assertEqual(process.values["--case-list"], str(case_list))
            self.assertEqual(process.values["--save-instance-dir"], str(self.path("save", "slice_{}".format(index))))
            self.assertNotIn(runner.INSTANCE_SAVE_ENVIRONMENT, process.env)
        summary = json.loads(self.path("out", "parallel.json").read_text())
        self.assertEqual(summary["mode"], "parallel")
        self.assertEqual([entry["exit_code"] for entry in summary["slices"]], [0, 0, 0, 0])
        self.assertEqual([entry["not_started"] for entry in summary["slices"]], [None] * 4)
        self.assertEqual(self.checked_ports, [3891, 3892, 3893, 3894])
        write_recording(
            self.path("sequential"),
            runner_manifest(
                self,
                selection(case_list),
                0,
                1,
                case_list,
                init_sql=str(parallel_slices.INIT_FILES["reduced"][0]),
                init_sql_sha256=runner.file_sha256(parallel_slices.INIT_FILES["reduced"][0]),
                init_user_sql=str(parallel_slices.INIT_FILES["reduced"][1]),
                init_user_sql_sha256=runner.file_sha256(parallel_slices.INIT_FILES["reduced"][1]),
            ),
        )
        code, report = compare(self.path("out", "merged"), self.path("sequential"))
        self.assertEqual(code, 0, report["recording_problems"])
        self.assertEqual(report["summary"]["identical"], 6)

    def test_serial_run_runs_one_slice_at_a_time(self):
        code, created = self.run_slices(["--init", "full", "--serial", "--case-list", str(LISTS_DIR / "send-reap.txt")])
        self.assertEqual(code, 0)
        self.assertEqual(
            FakeRunnerProcess.events,
            [(kind, index) for index in range(4) for kind in ("start", "finish")],
        )
        self.assertEqual(json.loads(self.path("out", "parallel.json").read_text())["mode"], "serial")
        self.assertEqual(created[0].values["--save-instance-dir"], str(self.path("env-save", "slice_0")))
        self.assertEqual(self.checked_ports, [3891, 3892, 3893, 3894] * 2)

    def test_a_port_that_answers_stops_the_run_before_any_slice_starts(self):
        self.busy_ports = {3893}
        code, created = self.run_slices(["--init", "full", "--case-list", str(LISTS_DIR / "send-reap.txt")])
        self.assertEqual(code, 1)
        self.assertEqual(created, [])
        self.assertFalse(self.path("out").exists())
        self.assertEqual(self.checked_ports, [3891, 3892, 3893, 3894])

    def test_a_port_taken_during_a_serial_run_stops_the_later_slices(self):
        def occupy(index, manifest):
            if index == 1:
                self.busy_ports.add(3893)

        code, created = self.run_slices(
            ["--init", "full", "--serial", "--case-list", str(LISTS_DIR / "send-reap.txt")], occupy
        )
        self.assertEqual(code, 1)
        self.assertEqual([process.slice_index for process in created], [0, 1])
        summary = json.loads(self.path("out", "parallel.json").read_text())
        self.assertEqual([entry["exit_code"] for entry in summary["slices"]], [0, 0, None, None])
        self.assertIsNone(summary["slices"][1]["not_started"])
        for entry in summary["slices"][2:]:
            self.assertIn("slice 2 was not started: something already answers on port 3893", entry["not_started"])
        self.assertIn("slice_2", summary["merge_refused"])
        self.assertIsNone(summary["merged"])
        self.assertFalse(self.path("out", "merged").exists())

    def test_failed_slice_fails_the_run_and_the_merged_recording(self):
        def fail_one(index, manifest):
            if index == 2:
                case = manifest["cases"][0]
                manifest["failed_cases"] = [case]
                manifest["success"] = False
                manifest["outcomes"][case] = {"exit_code": 1, "recorded": False, "partial": True}

        code, _ = self.run_slices(["--init", "full", "--case-list", str(LISTS_DIR / "send-reap.txt")], fail_one)
        self.assertEqual(code, 1)
        merged = manifest_of(self.path("out", "merged"))
        self.assertFalse(merged["success"])
        self.assertEqual(len(merged["failed_cases"]), 1)

    def test_used_output_directory_is_refused(self):
        self.path("out").mkdir()
        (self.path("out") / "old").write_text("x")
        with self.assertRaises(SystemExit):
            quiet(parallel_slices.main, [
                "run", "--seekdb", str(self.binaries / "seekdb"), "--obclient", str(self.binaries / "obclient"),
                "--mysqltest", str(self.binaries / "mysqltest"), "--out-dir", str(self.path("out")), "--init", "full",
            ])
        with self.assertRaises(SystemExit):
            quiet(parallel_slices.main, [
                "run", "--seekdb", str(self.binaries / "seekdb"), "--obclient", str(self.binaries / "obclient"),
                "--mysqltest", str(self.binaries / "mysqltest"), "--out-dir", str(self.path("out2")), "--init", "full",
                "--ports", "3891,3891",
            ])


def schemata_table(names):
    width = max(len("schema_name"), max(len(name) for name in names))
    border = "+" + "-" * (width + 2) + "+\n"
    rows = "".join("| {} |\n".format(name.ljust(width)) for name in names)
    return (border + "| {} |\n".format("schema_name".ljust(width)) + border + rows + border).encode("utf-8")


class FakeServer(object):
    def __init__(self, up_after=0.3, answering_before_start=False, early_error_window=0.0, drops_probe=False, exits=False, never_up=False, appears_after_port_check=False):
        self.lock = threading.Lock()
        self.started_at = None
        self.up_after = up_after
        self.answering = answering_before_start
        self.early_error_window = early_error_window
        self.drops_probe = drops_probe
        self.exits = exits
        self.never_up = never_up
        self.appears_after_port_check = appears_after_port_check
        self.databases = ["information_schema", "mysql", "oceanbase", "test"]
        self.starts = 0
        self.kills = 0
        self.statements = []
        self.port_checks = []

    def port_problem(self, port):
        with self.lock:
            self.port_checks.append((port, self.starts, self.kills))
            if self.appears_after_port_check:
                self.answering = True
                return None
            if self.answering:
                return "something already answers on port {} before seekdb starts: stand-in".format(port)
            return None

    def start(self, args, parameters=()):
        with self.lock:
            self.started_at = time.monotonic()
            self.starts += 1

    def kill(self, args):
        with self.lock:
            self.started_at = None
            self.answering = False
            self.kills += 1
            if self.drops_probe and "startup_probe" in self.databases:
                self.databases.remove("startup_probe")
        return None

    def running(self, args):
        return self.started_at is not None or self.answering

    def exited(self, args):
        return self.exits and self.started_at is not None

    def run_client(self, args, sql, options, database=None, timeout=None):
        with self.lock:
            self.statements.append(sql)
            if sql.startswith("create database"):
                self.databases.append(sql.split()[2].rstrip(";"))
                return 0, b"", b""
            if self.answering:
                return 0, schemata_table(sorted(self.databases)), b""
            if self.started_at is None or self.never_up:
                return 1, b"", b"ERROR 2003 (HY000): Can't connect to MySQL server on '127.0.0.1:3895' (61)\n"
            elapsed = time.monotonic() - self.started_at
            if elapsed < self.early_error_window:
                return 1, b"", b"ERROR 8001 (08004): Server is initializing\n"
            if elapsed < self.up_after:
                return 1, b"", b"ERROR 2003 (HY000): Can't connect to MySQL server on '127.0.0.1:3895' (61)\n"
            return 0, schemata_table(sorted(self.databases)), b""


class StartupConnectTest(Sandbox):
    def run_script(self, name, server, restarts=2, deadline=None):
        patches = [
            mock.patch.object(common, "run_client", server.run_client),
            mock.patch.object(common, "start_server", server.start),
            mock.patch.object(common, "kill_server", server.kill),
            mock.patch.object(common, "server_running", server.running),
            mock.patch.object(common, "server_exited", server.exited),
            mock.patch.object(common, "port_problem", server.port_problem),
            mock.patch.object(runner, "save_instance_outputs", lambda *args: None),
            mock.patch.object(runner, "destroy_instance", lambda *args, **kwargs: None),
        ]
        if deadline is not None:
            patches.append(mock.patch.object(startup_connect, "SESSION_DEADLINE", deadline))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            code = quiet(startup_connect.main, [
                "--seekdb", str(self.binaries / "seekdb"),
                "--obclient", str(self.binaries / "obclient"),
                "--base-dir", str(self.path(name, "base")),
                "--record-dir", str(self.path(name, "rec")),
                "--port", "3895",
                "--restarts", str(restarts),
            ])
        return code, self.path(name, "rec")

    def test_two_runs_record_identically(self):
        first_server = FakeServer(up_after=0.3)
        code, first = self.run_script("one", first_server)
        self.assertEqual(code, 0, manifest_of(self.path("one", "rec")))
        code, second = self.run_script("two", FakeServer(up_after=0.8))
        self.assertEqual(code, 0)
        code, report = compare(first, second)
        self.assertEqual(code, 0, report["recording_problems"])
        self.assertEqual(report["summary"]["identical"], 3)
        manifest = manifest_of(first)
        self.assertEqual(manifest["cases"], ["first_start", "restart_1", "restart_2"])
        self.assertIsNone(manifest["init_sql_sha256"])
        self.assertGreater(manifest["outcomes"]["first_start"]["details"]["a"]["attempts"], 1)
        self.assertEqual(first_server.kills, 3)
        self.assertEqual(first_server.port_checks, [(3895, 0, 0), (3895, 1, 1), (3895, 2, 2)])
        start = (first / "first_start.result").read_text()
        restart = (first / "restart_1.result").read_text()
        self.assertIn("-- session a: error codes before success: 2003 (HY000)\n", start)
        self.assertIn("-- session b: error codes before success: 2003 (HY000)\n", start)
        self.assertIn("-- check: both sessions got the same result: yes\n", start)
        self.assertIn("create database startup_probe;\n", start)
        self.assertNotIn("startup_probe |", start.split("create database")[0])
        self.assertIn("-- restart 1: stop (kill)\n", restart)
        self.assertIn("-- restart 1: start\n", restart)
        self.assertIn("| startup_probe      |", restart)
        self.assertIn("-- check: the result lists startup_probe, created before the first kill: yes\n", restart)
        self.assertTrue(start.startswith("-- first_start\n-- recorder sha256 startup_connect.py "))
        self.assertNotRegex(start, r"\d+\.\d+|attempts")

    def test_a_different_startup_error_shows_as_a_difference(self):
        code, first = self.run_script("one", FakeServer(up_after=0.3), restarts=0)
        self.assertEqual(code, 0)
        code, second = self.run_script("two", FakeServer(up_after=0.6, early_error_window=0.4), restarts=0)
        self.assertEqual(code, 0)
        self.assertIn("2003 (HY000), 8001 (08004)", (second / "first_start.result").read_text())
        code, report = compare(first, second)
        self.assertEqual(code, 1)
        self.assertEqual(report["summary"]["different"], 1)

    def test_port_already_answering_is_a_run_error(self):
        server = FakeServer(answering_before_start=True)
        code, recording = self.run_script("one", server)
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertIn("something already answers on port 3895 before seekdb starts: stand-in", manifest["error"])
        self.assertIsNone(manifest["outcomes"]["restart_1"]["exit_code"])
        self.assertEqual(server.statements, [])
        self.assertEqual(server.starts, 0)

    def test_listener_that_appears_after_the_port_check_is_still_a_run_error(self):
        server = FakeServer(appears_after_port_check=True)
        code, recording = self.run_script("one", server)
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertIn("something already answers on port 3895 before seekdb starts", manifest["error"])
        self.assertNotIn("stand-in", manifest["error"])
        self.assertGreater(len(server.statements), 0)
        self.assertEqual(server.starts, 0)

    def test_server_that_never_answers_fails_the_first_round(self):
        code, recording = self.run_script("one", FakeServer(never_up=True), deadline=0.5)
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertEqual(manifest["failed_cases"], ["first_start"])
        self.assertTrue((recording / "first_start.partial").is_file())
        self.assertIn("no success within 0.5 seconds", (recording / "first_start.partial").read_text())
        self.assertIsNone(manifest["outcomes"]["restart_2"]["exit_code"])

    def test_server_exit_during_startup_fails_the_round(self):
        code, recording = self.run_script("one", FakeServer(up_after=30, exits=True))
        self.assertEqual(code, 1)
        self.assertIn("seekdb exited before both sessions got an answer", (recording / "first_start.partial").read_text())

    def test_lost_database_after_restart_fails_the_check(self):
        code, recording = self.run_script("one", FakeServer(drops_probe=True))
        self.assertEqual(code, 1)
        self.assertIn(
            "-- check: the result lists startup_probe, created before the first kill: no",
            (recording / "restart_1.partial").read_text(),
        )


class FakeContainer(object):
    def __init__(self, seed, version="sysbench 1.0.20", fatal_in=None, broken_invariant=None, point_select_writes=False, index_mismatch=None, index_plan=True, ignored_in=None, zero_row_write_in=None):
        self.index_plan = index_plan
        self.random = random.Random(seed)
        self.version = version
        self.fatal_in = fatal_in
        self.broken_invariant = broken_invariant
        self.point_select_writes = point_select_writes
        self.index_mismatch = index_mismatch
        self.ignored_in = ignored_in or {}
        self.zero_row_write_in = zero_row_write_in
        self.tables = {}
        self.commands = []
        self.parameters = None
        self.running = False

    def docker(self, args, command, timeout=None):
        self.commands.append(list(command))
        if command[:2] == ["sysbench", "--version"]:
            return 0, (self.version + "\n").encode("utf-8")
        if command[0] == "ldd":
            return 0, (
                b"\tlinux-vdso.so.1 (0x0000ffff9e1e0000)\n"
                b"\tlibluajit-5.1.so.2 => /lib/aarch64-linux-gnu/libluajit-5.1.so.2 (0x0000ffff9df90000)\n"
                b"\t/lib/ld-linux-aarch64.so.1 (0x0000ffff9e1a3000)\n"
            )
        if command[0] == "sha256sum":
            return 0, "".join(
                "{}  {}\n".format(hashlib.sha256(path.encode()).hexdigest(), path) for path in command[1:]
            ).encode("utf-8")
        options = dict(item[2:].split("=", 1) for item in command[2:] if item.startswith("--") and "=" in item)
        workload, action = command[1], command[-1]
        tables = int(options["tables"])
        size = int(options["table-size"])
        if action == "prepare":
            for number in range(1, tables + 1):
                self.tables[number] = {"sum": number * 1000003, "xor": number * 7, "k_min": 1, "rows": size}
            return 0, "".join(
                "Creating table 'sbtest{0}'...\nInserting {1} records into 'sbtest{0}'\nCreating a secondary index on 'sbtest{0}'...\n".format(number, size)
                for number in range(1, tables + 1)
            ).encode("utf-8")
        threads = int(options["threads"])
        events = int(options["events"])
        name = "{}_{}".format(workload, threads)
        lines = ["sysbench 1.0.20 (using system LuaJIT 2.1.0-beta3)", ""]
        lines += ["DEBUG: Worker thread (#{}) started".format(index) for index in range(threads)]
        if name == self.fatal_in:
            lines.append("FATAL: mysql_drv_query() returned error 1062 (Duplicate entry '5' for key 'PRIMARY') for query 'INSERT ...'")
            return 1, ("\n".join(lines) + "\n").encode("utf-8")
        per_event = {"oltp_point_select": (1, 0, 0), "oltp_read_write": (14, 4, 2)}[workload]
        ignored_codes = self.ignored_in.get(name, {})
        ignored = sum(ignored_codes.values())
        for code, count in sorted(ignored_codes.items()):
            lines += ["DEBUG: Ignoring error {} Deadlock found when trying to get lock, ".format(code)] * count
        if workload == "oltp_read_write" and threads > 1:
            for number in self.tables:
                self.tables[number]["sum"] = self.random.randint(1, 10 ** 12)
                self.tables[number]["xor"] = self.random.randint(1, 2 ** 32)
        elif workload == "oltp_read_write":
            for number in self.tables:
                self.tables[number]["sum"] = self.tables[number]["sum"] * 31 + events
        elif self.point_select_writes:
            self.tables[1]["sum"] += 1
        if name == self.broken_invariant:
            self.tables[3]["rows"] -= 1
        read, write, other = (count * events for count in per_event)
        read += 7 * ignored
        if name == self.zero_row_write_in:
            write -= 1
            other += 1
        total = read + write + other
        lines += [
            "SQL statistics:",
            "    queries performed:",
            "        read:                            {}".format(read),
            "        write:                           {}".format(write),
            "        other:                           {}".format(other),
            "        total:                           {}".format(total),
            "    transactions:                        {}  ({:.2f} per sec.)".format(events, self.random.uniform(100, 9000)),
            "    queries:                             {}  ({:.2f} per sec.)".format(total, self.random.uniform(100, 9000)),
            "    ignored errors:                      {}      ({:.2f} per sec.)".format(ignored, self.random.uniform(0, 1)),
            "    reconnects:                          0      (0.00 per sec.)",
            "",
            "General statistics:",
            "    total time:                          {:.4f}s".format(self.random.uniform(1, 100)),
            "    total number of events:              {}".format(events),
        ]
        return 0, ("\n".join(lines) + "\n").encode("utf-8")

    def run_client(self, args, sql, options, database=None, timeout=None):
        if sql.startswith("create database"):
            return 0, b"", b""
        if sql.startswith("explain "):
            name = "k_1" if self.index_plan else "PRIMARY"
            return 0, "0\tTABLE RANGE SCAN\tsbtest1({})\t100000\t4000\n".format(name).encode("utf-8"), b""
        number = int(re.search(r"from sbtest(\d+)", sql).group(1))
        table = self.tables[number]
        size = args.table_size
        if "--table" in options and "crc32_sum" in sql:
            values = ("row_count", "crc32_sum", "crc32_xor"), (table["rows"], table["sum"], table["xor"])
        elif "--table" in options:
            values = (
                ("row_count", "ids", "min_id", "max_id", "bad_c", "bad_pad", "k_at_least_1"),
                (table["rows"], table["rows"], 1, size, 0, 0, 1),
            )
        else:
            mismatch = self.index_mismatch == number and "index(" in sql
            return 0, "{}\t{}\n".format(table["rows"], table["sum"] + (1 if mismatch else 0)).encode("utf-8"), b""
        widths = [max(len(str(a)), len(str(b))) for a, b in zip(*values)]
        border = "+" + "+".join("-" * (width + 2) for width in widths) + "+\n"
        row = lambda cells: "|" + "|".join(" {} ".format(str(cell).rjust(width)) for cell, width in zip(cells, widths)) + "|\n"
        return 0, (border + row(values[0]) + border + row(values[1]) + border).encode("utf-8"), b""


class SysbenchParityTest(Sandbox):
    def run_script(self, name, container, extra=()):
        started = []

        def start(args, parameters=()):
            started.append(tuple(parameters))
            container.running = True

        patches = [
            mock.patch.object(sysbench_parity, "docker", container.docker),
            mock.patch.object(common, "run_client", container.run_client),
            mock.patch.object(common, "start_server", start),
            mock.patch.object(common, "wait_ready", lambda args: None),
            mock.patch.object(common, "kill_server", lambda args: None),
            mock.patch.object(common, "server_running", lambda args: container.running),
            mock.patch.object(runner, "save_instance_outputs", lambda *args: None),
            mock.patch.object(runner, "destroy_instance", lambda *args, **kwargs: None),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            code = quiet(sysbench_parity.main, [
                "--seekdb", str(self.binaries / "seekdb"),
                "--obclient", str(self.binaries / "obclient"),
                "--base-dir", str(self.path(name, "base")),
                "--record-dir", str(self.path(name, "rec")),
                "--port", "3896",
            ] + list(extra))
        container.parameters = started
        return code, self.path(name, "rec")

    def test_two_runs_with_different_interleavings_record_identically(self):
        first_container = FakeContainer(seed=1)
        code, first = self.run_script("one", first_container)
        self.assertEqual(code, 0, manifest_of(self.path("one", "rec")).get("outcomes"))
        second_container = FakeContainer(seed=2)
        code, second = self.run_script("two", second_container)
        self.assertEqual(code, 0)
        code, report = compare(first, second)
        self.assertEqual(code, 0, report["recording_problems"])
        manifest = manifest_of(first)
        self.assertEqual(manifest["cases"], [
            "prepare",
            "oltp_point_select_1", "oltp_point_select_16", "oltp_point_select_64",
            "oltp_read_write_1", "oltp_read_write_16", "oltp_read_write_64",
        ])
        self.assertEqual(report["summary"]["identical"], 7)
        self.assertEqual(first_container.parameters, [sysbench_parity.SERVER_PARAMETERS])
        self.assertEqual(self.checked_ports, [3896, 3896])
        self.assertNotEqual(first_container.tables, second_container.tables)
        details = manifest["outcomes"]["oltp_read_write_64"]["details"]["sysbench"]
        self.assertEqual(details["ignored_errors_by_code"], {})
        self.assertEqual(details["report"]["write"], 4 * 30000)
        prepare = (first / "prepare.result").read_text()
        self.assertIn(
            "-- check: the plan of the index statement for sbtest1 reads k_1 (the plan text is not recorded: "
            "its row and cost estimates can change with background work after prepare): yes\n",
            prepare,
        )
        self.assertNotIn("TABLE RANGE SCAN", prepare)
        rw1 = (first / "oltp_read_write_1.result").read_text()
        rw16 = (first / "oltp_read_write_16.result").read_text()
        ps64 = (first / "oltp_point_select_64.result").read_text()
        self.assertIn(
            "-- sysbench oltp_read_write --db-driver=mysql --mysql-user=root --mysql-db=sbtest --tables=16 "
            "--table-size=100000 --rand-type=uniform --threads=1 prepare\n",
            prepare,
        )
        self.assertIn(
            "-- sysbench oltp_read_write --db-driver=mysql --mysql-user=root --mysql-db=sbtest --tables=16 "
            "--table-size=100000 --rand-type=uniform --db-ps-mode=disable --threads=1 --events=30000 --time=0 "
            "--rand-seed=1 --mysql-ignore-errors=1213,1020,1205 --verbosity=5 run\n",
            rw1,
        )
        rw_report = (
            "-- sysbench report: 30000 transactions; queries: 420000 read, 120000 write, 60000 other, 600000 total; "
            "0 ignored errors, 0 reconnects\n-- sysbench ignored errors by code: none\n"
        )
        rw_counts = (
            "-- check: the query counts are 30000 times one event's statements (14 read, 4 write, 2 other), "
            "with no ignored error and no reconnect: yes\n"
        )
        self.assertIn(rw_report, rw1)
        self.assertIn(rw_counts, rw1)
        self.assertIn(
            "-- sysbench report: 200000 transactions; queries: 200000 read, 0 write, 0 other, 200000 total; "
            "0 ignored errors, 0 reconnects\n-- sysbench ignored errors by code: none\n",
            ps64,
        )
        self.assertIn("-- check: the tables are as prepare left them (the checksums are the prepare case's): yes\n", ps64)
        for text in (rw16, (first / "oltp_read_write_64.result").read_text()):
            self.assertIn(rw_report, text)
            self.assertIn(rw_counts, text)
            self.assertIn("-- check: every table has 100000 rows, with ids 1 to 100000: yes\n", text)
            self.assertIn("-- check: sysbench ran exactly 30000 events, and reports 30000 transactions: yes\n", text)
            self.assertIn("interleave: the table contents (no per-table checksum)\n", text)
            self.assertNotIn("crc32_sum", text)
        self.assertIn("crc32_sum", rw1)
        self.assertIn("-- client: sysbench 1.0.20, sha256 of its binary, Lua scripts and libraries: ", prepare)
        self.assertIn("/lib/aarch64-linux-gnu/libluajit-5.1.so.2", manifest["client"]["files_sha256"])
        run_commands = [command for command in first_container.commands if command[0] == "sysbench" and command[-1] in ("run", "prepare")]
        self.assertEqual(len(run_commands), 7)
        for command in run_commands:
            self.assertIn("--mysql-host=host.docker.internal", command)
            self.assertIn("--mysql-port=3896", command)
            self.assertNotIn("--report-interval", " ".join(command))
            if command[-1] == "run":
                self.assertIn("--time=0", command)
                self.assertEqual("--db-ps-mode=disable" in command, command[1] == "oltp_read_write")
        for text in (prepare, rw1, rw16, ps64):
            self.assertNotIn("3896", text)
            self.assertNotIn("host.docker.internal", text)

    def test_fatal_error_fails_the_case_and_stops_the_run(self):
        code, recording = self.run_script("one", FakeContainer(seed=1, fatal_in="oltp_read_write_16"))
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertEqual(manifest["failed_cases"], ["oltp_read_write_16"])
        self.assertIsNone(manifest["outcomes"]["oltp_read_write_64"]["exit_code"])
        partial = (recording / "oltp_read_write_16.partial").read_text()
        self.assertIn("-- sysbench exit code 1; error codes in its FATAL lines: 1062", partial)

    def test_broken_invariant_changed_tables_and_index_disagreement_fail_checks(self):
        code, recording = self.run_script("one", FakeContainer(seed=1, broken_invariant="oltp_read_write_64"))
        self.assertEqual(code, 1)
        self.assertIn("-- check: every table has 100000 rows, with ids 1 to 100000: no", (recording / "oltp_read_write_64.partial").read_text())
        code, recording = self.run_script("two", FakeContainer(seed=1, point_select_writes=True))
        self.assertEqual(code, 1)
        self.assertIn("the checksums are the prepare case's): no", (recording / "oltp_point_select_1.partial").read_text())
        code, recording = self.run_script("three", FakeContainer(seed=1, index_mismatch=3))
        self.assertEqual(code, 1)
        partial = (recording / "prepare.partial").read_text()
        self.assertIn("gives the row count and crc32 sum a full scan gives: no\n-- differ: sbtest3\n", partial)
        code, recording = self.run_script("four", FakeContainer(seed=1, index_plan=False))
        self.assertEqual(code, 1)
        self.assertIn(
            "-- check: the plan of the index statement for sbtest1 reads k_1 (the plan text is not recorded: "
            "its row and cost estimates can change with background work after prepare): no\n",
            (recording / "prepare.partial").read_text(),
        )

    def test_ignored_errors_and_zero_row_writes_fail_the_multi_thread_cases(self):
        clean_container = FakeContainer(seed=1)
        code, clean = self.run_script("clean", clean_container)
        self.assertEqual(code, 0)
        code, recording = self.run_script(
            "one", FakeContainer(seed=2, ignored_in={"oltp_read_write_16": {"1213": 2, "1205": 1}})
        )
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertEqual(manifest["failed_cases"], ["oltp_read_write_16"])
        self.assertIsNone(manifest["outcomes"]["oltp_read_write_64"]["exit_code"])
        self.assertEqual(
            manifest["outcomes"]["oltp_read_write_16"]["details"]["sysbench"]["ignored_errors_by_code"],
            {"1205": 1, "1213": 2},
        )
        partial = (recording / "oltp_read_write_16.partial").read_text()
        self.assertIn("420021 read, 120000 write, 60000 other, 600021 total; 3 ignored errors, 0 reconnects\n", partial)
        self.assertIn("-- sysbench ignored errors by code: 1205: 1, 1213: 2\n", partial)
        self.assertIn("with no ignored error and no reconnect: no\n", partial)
        code, report = compare(clean, recording)
        self.assertEqual(code, 1)
        self.assertIn("right recording has failed cases: oltp_read_write_16", report["recording_problems"])
        code, recording = self.run_script("two", FakeContainer(seed=3, zero_row_write_in="oltp_read_write_64"))
        self.assertEqual(code, 1)
        self.assertEqual(manifest_of(recording)["failed_cases"], ["oltp_read_write_64"])
        partial = (recording / "oltp_read_write_64.partial").read_text()
        self.assertIn("420000 read, 119999 write, 60001 other, 600000 total; 0 ignored errors, 0 reconnects\n", partial)
        self.assertIn("-- sysbench ignored errors by code: none\n", partial)
        self.assertIn("with no ignored error and no reconnect: no\n", partial)

    def test_a_port_that_answers_is_a_run_error_before_the_server_starts(self):
        self.busy_ports = {3896}
        container = FakeContainer(seed=1)
        code, recording = self.run_script("one", container)
        self.assertEqual(code, 1)
        manifest = manifest_of(recording)
        self.assertIn("something already answers on port 3896 before seekdb starts", manifest["error"])
        self.assertEqual(container.parameters, [])
        self.assertEqual(sorted(p.name for p in recording.iterdir()), ["manifest.json"])
        self.assertTrue(all(outcome["exit_code"] is None for outcome in manifest["outcomes"].values()))

    def test_other_sysbench_version_is_refused_before_anything_runs(self):
        container = FakeContainer(seed=1, version="sysbench 1.1.0")
        code, recording = self.run_script("one", container)
        self.assertEqual(code, 1)
        self.assertFalse((recording / "manifest.json").exists())
        self.assertEqual(container.parameters, [])

    def test_thread_subset_and_event_counts(self):
        code, recording = self.run_script(
            "one", FakeContainer(seed=3), ["--threads", "16,1", "--read-write-events", "500", "--point-select-events", "700"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(manifest_of(recording)["cases"], [
            "prepare", "oltp_point_select_1", "oltp_point_select_16", "oltp_read_write_1", "oltp_read_write_16",
        ])
        self.assertIn("--events=700", (recording / "oltp_point_select_16.result").read_text())


class ParsingTest(unittest.TestCase):
    def test_sysbench_report_from_the_baseline_format(self):
        output = (
            "SQL statistics:\n    queries performed:\n        read:                            2243710\n"
            "        write:                           641060\n        other:                           320530\n"
            "        total:                           3205300\n"
            "    transactions:                        160265 (2670.32 per sec.)\n"
            "    queries:                             3205300 (53406.38 per sec.)\n"
            "    ignored errors:                      0      (0.00 per sec.)\n"
            "    reconnects:                          0      (0.00 per sec.)\n\nGeneral statistics:\n"
            "    total time:                          60.0166s\n    total number of events:              160265\n"
            "DEBUG: Ignoring error 1213 Deadlock found when trying to get lock, \n"
            "DEBUG: Ignoring error 1205 Lock wait timeout exceeded, \n"
            "DEBUG: Ignoring error 1213 Deadlock found when trying to get lock, \n"
        ).encode("utf-8")
        parsed = sysbench_parity.parse_sysbench(output)
        self.assertEqual(parsed["report"], {
            "read": 2243710, "write": 641060, "other": 320530, "total": 3205300, "transactions": 160265,
            "queries": 3205300, "ignored_errors": 0, "reconnects": 0, "events": 160265,
        })
        self.assertEqual(parsed["ignored_errors_by_code"], {"1205": 1, "1213": 2})
        self.assertEqual(parsed["fatal_codes"], [])
        fatal = sysbench_parity.parse_sysbench(
            b"FATAL: mysql_stmt_prepare() failed\nFATAL: MySQL error: 5930 \"maximum open cursors exceeded\"\n"
        )
        self.assertEqual(fatal["fatal_codes"], ["5930"])

    def test_report_check_needs_both_the_report_and_the_debug_lines_free_of_ignored_errors(self):
        report = {
            "read": 14, "write": 4, "other": 2, "total": 20, "transactions": 1,
            "queries": 20, "ignored_errors": 0, "reconnects": 0, "events": 1,
        }
        for by_code, holds in (({}, True), ({"1213": 1}, False)):
            recording = common.Recording()
            parsed = {"report": report, "ignored_errors_by_code": by_code}
            sysbench_parity.record_report(recording, parsed, 1, sysbench_parity.READ_WRITE_EVENT)
            text = bytes(recording.content).decode("utf-8")
            self.assertIn(
                "-- sysbench ignored errors by code: {}\n".format("1213: 1" if by_code else "none"), text
            )
            self.assertIn("with no ignored error and no reconnect: {}\n".format("yes" if holds else "no"), text)
            self.assertEqual(recording.problems == [], holds)

    def test_obclient_errors(self):
        self.assertEqual(
            startup_connect.attempt_error(1, b"ERROR 2003 (HY000): Can't connect to MySQL server on '127.0.0.1:3895' (61)\n"),
            "2003 (HY000)",
        )
        self.assertEqual(startup_connect.attempt_error(1, b"ERROR 2013: Lost connection\n"), "2013")
        self.assertEqual(startup_connect.attempt_error(1, b"Segmentation fault\n"), "exit code 1 without an ERROR line")
        self.assertEqual(startup_connect.attempt_error(None, b""), "no answer within 30 seconds")
        self.assertEqual(
            sorted(["8001 (08004)", "exit code 1 without an ERROR line", "2003 (HY000)", "2013"], key=startup_connect.error_order),
            ["2003 (HY000)", "2013", "8001 (08004)", "exit code 1 without an ERROR line"],
        )

    def test_ldd_paths(self):
        self.assertEqual(
            sysbench_parity.library_paths(
                "\tlinux-vdso.so.1 (0x0000ffff9e1e0000)\n"
                "\tlibmysqlclient.so.21 => /lib/aarch64-linux-gnu/libmysqlclient.so.21 (0x0000ffff9d000000)\n"
                "\tlibmissing.so => not found\n"
                "\t/lib/ld-linux-aarch64.so.1 (0x0000ffff9e1a3000)\n"
            ),
            ["/lib/aarch64-linux-gnu/libmysqlclient.so.21", "/lib/ld-linux-aarch64.so.1"],
        )

    def test_statements(self):
        self.assertEqual(
            sysbench_parity.index_statements("sbtest12"),
            (
                "select /*+ index(sbtest12 k_12) */ count(*), sum(crc32(concat_ws('#', id, k))) from sbtest12 where k > 0;",
                "select /*+ full(sbtest12) */ count(*), sum(crc32(concat_ws('#', id, k))) from sbtest12 where k > 0;",
            ),
        )
        self.assertIn("regexp '^[0-9]{11}(-[0-9]{11}){9} *$'", sysbench_parity.invariant_statement("sbtest1"))
        self.assertEqual(len("###########-###########-###########-###########-###########-###########-###########-###########-###########-###########"), 119)
        self.assertTrue(re.match(sysbench_parity.C_SHAPE, "-".join(["12345678901"] * 10) + " "))
        self.assertTrue(re.match(sysbench_parity.PAD_SHAPE, "-".join(["12345678901"] * 5)))
        self.assertFalse(re.match(sysbench_parity.PAD_SHAPE, "-".join(["12345678901"] * 4)))


class PortCheckTest(unittest.TestCase):
    def test_port_check_on_sockets_opened_by_the_test(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind((common.HOST, 0))
        listener.listen(8)
        listening_port = listener.getsockname()[1]
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind((common.HOST, 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        answering = (
            "something already answers on port {0} before seekdb starts: 127.0.0.1:{0} "
            "accepts TCP connections".format(listening_port)
        )
        self.assertEqual(common.port_problem(listening_port), answering)
        self.assertIsNone(common.port_problem(closed_port))
        common.require_free_ports([closed_port])
        with self.assertRaises(runner.RunnerError) as caught:
            common.require_free_ports([closed_port, listening_port])
        self.assertEqual(str(caught.exception), answering)

    def test_the_readme_check_before_the_one_slice_run(self):
        readme = (FAMILY_DIR / "README.md").read_text(encoding="utf-8").splitlines()
        lines = [line for line in readme if "require_free_ports([3891])" in line]
        self.assertEqual(len(lines), 1)
        words = shlex.split(lines[0].split(" && ")[0])
        self.assertEqual(words[:3] + words[4:], ["python3", "-B", "-c", "$F"])
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind((common.HOST, 0))
        listener.listen(8)
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind((common.HOST, 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        for port, code in ((closed_port, 0), (listener.getsockname()[1], 1)):
            snippet = words[3].replace("[3891]", "[{}]".format(port))
            self.assertNotEqual(snippet, words[3])
            result = REAL_RUN(
                [sys.executable, "-B", "-c", snippet, str(FAMILY_DIR)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            self.assertEqual(result.returncode, code, result.stdout)
            if code:
                self.assertIn("something already answers on port {}".format(port), result.stdout)

    def test_a_connection_that_is_neither_accepted_nor_refused_counts_as_not_free(self):
        def time_out(address, timeout=None):
            raise TimeoutError("timed out")

        with mock.patch.object(common.socket, "create_connection", time_out):
            problem = common.port_problem(3899)
        self.assertEqual(
            problem,
            "cannot tell whether port 3899 is free before seekdb starts: a TCP connection to "
            "127.0.0.1:3899 failed with TimeoutError('timed out') instead of being refused",
        )


class StandInProcessTest(unittest.TestCase):
    def test_pid_check_and_kill_on_a_stand_in_process(self):
        root = Path(tempfile.mkdtemp(prefix="concurrency-stand-in-"))
        sdb = runner.load_sdb_module(common.SDB_PATH)
        base = sdb._base_dir(str(root / "base"))
        (base / "run").mkdir(parents=True)
        stand_in = REAL_POPEN([sys.executable, "-c", "import time; time.sleep(60)", "--base-dir={}".format(base)])
        self.addCleanup(lambda: stand_in.poll() is None and stand_in.kill())
        arguments, previous = [], None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            listing = REAL_RUN(["ps", "-p", str(stand_in.pid), "-ww", "-o", "args="], stdout=subprocess.PIPE, universal_newlines=True).stdout
            arguments = shlex.split(listing.strip()) if listing.strip() else []
            if arguments and arguments == previous and "--base-dir={}".format(base) in arguments:
                break
            previous = arguments
            time.sleep(0.2)
        (base / ".sdb-instance").write_text("seekdb-instance-v1\n{}\n".format(Path(arguments[0]).resolve()))
        (base / "run" / "seekdb.pid").write_text(str(stand_in.pid))
        args = argparse.Namespace(base_dir=base)
        self.assertEqual(common.instance_pid(args), stand_in.pid)
        self.assertFalse(common.server_exited(args))
        other = argparse.Namespace(base_dir=sdb._base_dir(str(root / "other")))
        (other.base_dir / "run").mkdir(parents=True)
        (other.base_dir / ".sdb-instance").write_text("seekdb-instance-v1\n{}\n".format(Path(arguments[0]).resolve()))
        (other.base_dir / "run" / "seekdb.pid").write_text(str(stand_in.pid))
        self.assertIsNone(common.instance_pid(other))
        with mock.patch.object(common, "stop_server") as stop, mock.patch.object(
            common.os, "kill", wraps=common.os.kill
        ) as kill:
            self.assertIsNone(common.kill_server(args))
        stop.assert_called_once_with(args)
        sent = [call.args for call in kill.call_args_list]
        self.assertEqual([signal_number for _, signal_number in sent if signal_number != 0], [signal.SIGKILL])
        self.assertEqual(set(pid for pid, _ in sent), {stand_in.pid})
        with self.assertRaises(ProcessLookupError):
            common.os.kill(stand_in.pid, 0)
        self.assertIsNone(common.instance_pid(args))
        self.assertTrue(common.server_exited(args))
        self.assertIn("was gone", common.kill_process(stand_in.pid))


if __name__ == "__main__":
    unittest.main()
