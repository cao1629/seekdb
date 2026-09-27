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
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path


DEFAULT_RUNNER = (
    Path(__file__).resolve().parents[4]
    / ".github"
    / "script"
    / "seekdb"
    / "mysqltest_for_seekdb.py"
)
OPTIONS = {"runner": DEFAULT_RUNNER, "baseline": None}
FAILURE_OUTPUT = (
    "[2026-09-25 09:41:25] mysqltest: At line 54: mysql_fetch didn't end with "
    "MYSQL_NO_DATA from statement: error: 101\n"
    "\n"
    "The result from queries just before the failure was:\n"
    "< snip >\n"
    "select * from tx;\n"
    "\n"
    "More results from queries before failure can be found in "
    "/tmp/somewhere/mysqltest_log/expr_func_length.log\n"
)
FAILURE_LINE = (
    "mysqltest: At line 54: mysql_fetch didn't end with MYSQL_NO_DATA from "
    "statement: error: 101"
)
STUB_MYSQLTEST = """#!{python}
import sys
from pathlib import Path
arguments = dict(
    argument[2:].split("=", 1) for argument in sys.argv[1:] if "=" in argument
)
result_file = Path(arguments["result-file"])
log_file = Path(arguments["logdir"]) / (result_file.stem + ".log")
mode = Path(arguments["test-file"]).read_text().strip()
if mode == "pass":
    log_file.write_text("select 1;\\n1\\n")
    result_file.write_text("select 1;\\n1\\n")
    sys.exit(0)
log_file.write_text("select * from tx;\\n")
sys.stdout.write({failure!r})
sys.exit(1)
"""


def load_runner(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_for(cases, failed_cases, outcomes):
    return {
        "finished": True,
        "max_retries": 0,
        "retried_cases": {},
        "error": None,
        "failed_cases": failed_cases,
        "cases": cases,
        "mysqltest_sha256": "m",
        "obclient_sha256": "o",
        "init_sql_sha256": "i",
        "init_user_sql_sha256": "u",
        "sdb_sha256": "s",
        "tools_deploy_tree": "t",
        "tools_deploy_status": "",
        "fresh_instance_per_case": False,
        "seekdb_parameters": [],
        "ps_protocol": True,
        "compress": False,
        "plan_cache_stats": False,
        "plan_cache_read": None,
        "test_dir_sha256": None,
        "seekdb_sha256": "b",
        "runner_sha256": "r",
        "repo_head": "h",
        "test_dir": None,
        "outcomes": outcomes,
    }


class Side(object):
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True)
        self.cases = []
        self.failed = []
        self.outcomes = {}

    def recorded(self, case, content):
        self.cases.append(case)
        (self.directory / (case + ".result")).write_bytes(content)
        self.outcomes[case] = {"exit_code": 0, "recorded": True, "partial": False}
        return self

    def failed_case(self, case, partial, exit_code=1, lines=(FAILURE_LINE,)):
        self.cases.append(case)
        self.failed.append(case)
        if partial is not None:
            (self.directory / (case + ".partial")).write_bytes(partial)
        outcome = {
            "exit_code": exit_code,
            "recorded": False,
            "partial": partial is not None,
        }
        if lines is not None:
            outcome["failure_lines"] = list(lines)
        self.outcomes[case] = outcome
        return self

    def finish(self):
        (self.directory / "manifest.json").write_text(
            json.dumps(manifest_for(self.cases, self.failed, self.outcomes))
        )
        return self.directory


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner(OPTIONS["runner"], "runner_under_test")
        cls.baseline = (
            load_runner(OPTIONS["baseline"], "runner_baseline")
            if OPTIONS["baseline"]
            else None
        )

    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="ps-offline-")
        self.addCleanup(shutil.rmtree, self.temporary)
        self.counter = 0

    def path(self, *parts):
        return Path(self.temporary).joinpath(*parts)

    def side(self, name):
        self.counter += 1
        return Side(self.path("{}-{}".format(name, self.counter)))

    def known_list(self, text):
        self.counter += 1
        path = self.path("known-{}.txt".format(self.counter))
        path.write_text(text)
        return path

    def compare(self, left, right, *extra, module=None):
        module = module or self.runner
        self.counter += 1
        out = self.path("compare-{}.json".format(self.counter))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = module.main(
                    ["compare", "--left", str(left), "--right", str(right), "--out", str(out)]
                    + [str(item) for item in extra]
                )
            except SystemExit as exc:
                code = exc.code
        payload = json.loads(out.read_text()) if out.is_file() else None
        return code, stdout.getvalue() + stderr.getvalue(), payload

    def failing_pair(self, left_lines=(FAILURE_LINE,), right_lines=(FAILURE_LINE,)):
        left = (
            self.side("left")
            .recorded("a", b"select 1;\n1\n")
            .failed_case("expr.func_length", b"select * from tx;\n", lines=left_lines)
            .finish()
        )
        right = (
            self.side("right")
            .recorded("a", b"select 1;\n1\n")
            .failed_case("expr.func_length", b"select * from tx;\n", lines=right_lines)
            .finish()
        )
        return left, right

    def test_failure_lines_single_message(self):
        self.assertEqual(self.runner.mysqltest_failure_lines(FAILURE_OUTPUT), [FAILURE_LINE])

    def test_failure_lines_included_file(self):
        output = (
            "[2026-09-25 10:00:00] mysqltest: In included file "
            "\"./mysql_test/include/a.inc\": \n"
            "included from ./t/x.test at line 5:\n"
            "At line 3: query 'select 1' failed: 1146: Table 'test.t' doesn't exist\n"
            "\n"
            "The result from queries just before the failure was:\n"
            "select 1;\n"
        )
        self.assertEqual(
            self.runner.mysqltest_failure_lines(output),
            [
                "mysqltest: In included file \"./mysql_test/include/a.inc\": ",
                "included from ./t/x.test at line 5:",
                "At line 3: query 'select 1' failed: 1146: Table 'test.t' doesn't exist",
            ],
        )

    def test_failure_lines_without_message(self):
        self.assertEqual(self.runner.mysqltest_failure_lines(""), [])
        self.assertEqual(
            self.runner.mysqltest_failure_lines("\n3600 seconds timeout\n"), []
        )

    def run_stub_case(self, mode):
        stub = self.path("mysqltest")
        stub.write_text(
            STUB_MYSQLTEST.format(python=sys.executable, failure=FAILURE_OUTPUT)
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        test_file = self.path("{}.test".format(mode))
        test_file.write_text(mode)
        work_dir = self.path("work-" + mode)
        record_dir = self.path("rec-" + mode)
        for directory in (work_dir / "record_tmp", record_dir, work_dir / "log", work_dir / "tmp"):
            directory.mkdir(parents=True)
        args = argparse.Namespace(
            record_dir=record_dir,
            work_dir=work_dir,
            host="127.0.0.1",
            port=1,
            mysqltest=stub,
            obclient=stub,
            base_dir=self.path("base"),
            ps_protocol=True,
            compress=False,
            ignore_trailing_whitespace=False,
            record_outcomes={},
        )
        case = self.runner.MysqltestCase("expr.func_length", test_file, self.path("none.result"))
        with contextlib.redirect_stdout(io.StringIO()):
            code, output, ignored = self.runner.run_case(
                args, self.path(), case, work_dir / "tmp", work_dir / "log"
            )
        return code, args.record_outcomes["expr.func_length"], record_dir

    def test_run_case_records_failure_lines(self):
        code, outcome, record_dir = self.run_stub_case("fail")
        self.assertEqual(code, 1)
        self.assertEqual(outcome["failure_lines"], [FAILURE_LINE])
        self.assertTrue(outcome["partial"])
        self.assertEqual(
            (record_dir / "expr.func_length.partial").read_bytes(), b"select * from tx;\n"
        )

    def test_run_case_passing_has_no_failure_lines(self):
        code, outcome, record_dir = self.run_stub_case("pass")
        self.assertEqual(code, 0)
        self.assertEqual(outcome, {"exit_code": 0, "recorded": True, "partial": False})
        self.assertTrue((record_dir / "expr.func_length.result").is_file())

    def test_without_list_failed_alike_still_fails(self):
        left, right = self.failing_pair()
        code, output, payload = self.compare(left, right)
        self.assertEqual(code, 1)
        self.assertIn("left recording has failed cases: expr.func_length", output)
        self.assertIn("failed_alike=1", output)
        self.assertIsNone(payload["known_failures"])

    def test_listed_failure_alike_is_accepted(self):
        left, right = self.failing_pair()
        listing = self.known_list("# reason\nexpr.func_length  # line 54\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 0, output)
        self.assertEqual(payload["recording_problems"], [])
        self.assertEqual(payload["known_failures"]["accepted"], ["expr.func_length"])
        self.assertEqual(payload["verdict_summary"]["missing"], 0)
        self.assertEqual(payload["summary"]["missing"], 1)
        self.assertIn("known failure: accepted; both sides stopped with " + FAILURE_LINE, output)
        self.assertIn("known failures accepted=1", output)
        entry = [case for case in payload["cases"] if case["case"] == "expr.func_length"][0]
        self.assertEqual(entry["verdict"], "failed_alike")

    def test_different_failure_lines_are_not_accepted(self):
        left, right = self.failing_pair(
            right_lines=("mysqltest: At line 54: query 'select * from tx' failed: 1210",)
        )
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["known_failures"]["not_accepted"],
            {"expr.func_length": "the mysqltest failure lines differ"},
        )
        self.assertIn("left recording has failed cases: expr.func_length", output)

    def test_missing_failure_lines_are_not_accepted(self):
        left, right = self.failing_pair(left_lines=None)
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["known_failures"]["not_accepted"]["expr.func_length"],
            "a side recorded no mysqltest failure lines",
        )
        left, right = self.failing_pair(left_lines=(), right_lines=())
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)

    def test_different_partial_output_is_not_accepted(self):
        left = self.side("l").failed_case("expr.func_length", b"select 1;\n").finish()
        right = self.side("r").failed_case("expr.func_length", b"select 2;\n").finish()
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertIn("differ in their exit codes or partial output", output)

    def test_different_exit_codes_are_not_accepted(self):
        left = self.side("l").failed_case("expr.func_length", b"x\n", exit_code=1).finish()
        right = self.side("r").failed_case("expr.func_length", b"x\n", exit_code=124).finish()
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertEqual(payload["known_failures"]["accepted"], [])

    def test_missing_partial_is_not_accepted(self):
        left = self.side("l").failed_case("expr.func_length", None).finish()
        right = self.side("r").failed_case("expr.func_length", None).finish()
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)

    def test_failure_on_one_side_is_not_accepted(self):
        left = self.side("l").failed_case("expr.func_length", b"x\n").finish()
        right = self.side("r").recorded("expr.func_length", b"x\ny\n").finish()
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["known_failures"]["not_accepted"]["expr.func_length"],
            "failed on the left side only",
        )
        self.assertEqual(payload["verdict_summary"]["missing"], 1)

    def test_listed_case_recorded_on_both_sides(self):
        left = self.side("l").recorded("expr.func_length", b"x\n").finish()
        right = self.side("r").recorded("expr.func_length", b"x\n").finish()
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["known_failures"]["not_accepted"]["expr.func_length"],
            "recorded on both sides",
        )
        right = self.side("r2").recorded("expr.func_length", b"y\n").finish()
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)

    def test_unlisted_failure_still_fails(self):
        left = (
            self.side("l")
            .failed_case("expr.func_length", b"x\n")
            .failed_case("b", b"y\n")
            .finish()
        )
        right = (
            self.side("r")
            .failed_case("expr.func_length", b"x\n")
            .failed_case("b", b"y\n")
            .finish()
        )
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["recording_problems"],
            ["left recording has failed cases: b", "right recording has failed cases: b"],
        )

    def test_list_names_outside_the_recordings(self):
        left, right = self.failing_pair()
        listing = self.known_list("expr.func_length\nno.such_case\n")
        code, output, payload = self.compare(left, right, "--known-failures", listing)
        self.assertEqual(code, 0)
        self.assertEqual(payload["known_failures"]["not_in_recordings"], ["no.such_case"])
        self.assertIn("not_in_recordings=1", output)

    def test_bad_lists_are_refused(self):
        left, right = self.failing_pair()
        for text in ("", "# only a comment\n", "a\na\n"):
            code, output, payload = self.compare(
                left, right, "--known-failures", self.known_list(text)
            )
            self.assertEqual(code, 2, text)
        code, output, payload = self.compare(
            left, right, "--known-failures", self.path("absent.txt")
        )
        self.assertEqual(code, 2)

    def test_other_recording_problems_remain(self):
        left, right = self.failing_pair()
        manifest = json.loads((right / "manifest.json").read_text())
        manifest["ps_protocol"] = False
        (right / "manifest.json").write_text(json.dumps(manifest))
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(
            left, right, "--known-failures", listing, "--require-ps-protocol"
        )
        self.assertEqual(code, 1)
        self.assertIn("right recording was not made with --ps-protocol", output)

    def test_known_failures_with_the_est_mask(self):
        root = Path(OPTIONS["runner"]).resolve().parents[3]
        if not (root / "migration" / "judge" / "lists" / "plan-bearing.txt").is_file():
            self.skipTest("the runner's tree has no plan-bearing list")
        table = (
            b"explain select 1;\nQuery Plan\n"
            b"===================================================\n"
            b"|ID|OPERATOR           |NAME|EST.ROWS|EST.TIME(us)|\n"
            b"---------------------------------------------------\n"
            b"|0 |EXPRESSION         |    |1       |1           |\n"
            b"===================================================\n"
        )
        left = (
            self.side("l")
            .recorded("explain", table)
            .failed_case("expr.func_length", b"select * from tx;\n")
            .finish()
        )
        right = (
            self.side("r")
            .recorded("explain", table.replace(b"|1       |1           |", b"|2       |3           |"))
            .failed_case("expr.func_length", b"select * from tx;\n")
            .finish()
        )
        listing = self.known_list("expr.func_length\n")
        code, output, payload = self.compare(
            left, right, "--mask", "est", "--known-failures", listing
        )
        self.assertEqual(code, 0, output)
        self.assertIn("known failures accepted=1", output)
        self.assertEqual(output.count("verdict:"), 1)
        self.assertEqual(payload["verdict_summary"]["different"], 0)
        code, output, payload = self.compare(left, right, "--mask", "est")
        self.assertEqual(code, 1)

    def test_default_output_matches_baseline(self):
        if self.baseline is None:
            self.skipTest("no --baseline runner given")
        left = (
            self.side("l")
            .recorded("same", b"1\n")
            .recorded("changed", b"1\n")
            .failed_case("expr.func_length", b"x\n")
            .failed_case("one_side", b"z\n")
            .finish()
        )
        right = (
            self.side("r")
            .recorded("same", b"1\n")
            .recorded("changed", b"2\n")
            .failed_case("expr.func_length", b"x\n")
            .recorded("one_side", b"z\n")
            .finish()
        )
        new = self.compare(left, right)
        old = self.compare(left, right, module=self.baseline)
        self.assertEqual(new[0], old[0])
        self.assertEqual(new[1].replace(str(self.path()), ""), old[1].replace(str(self.path()), ""))
        new_payload = dict(new[2])
        self.assertIsNone(new_payload.pop("known_failures"))
        self.assertEqual(new_payload, old[2])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", default=str(DEFAULT_RUNNER))
    parser.add_argument("--baseline")
    options, remaining = parser.parse_known_args()
    OPTIONS["runner"] = Path(options.runner).resolve()
    OPTIONS["baseline"] = Path(options.baseline).resolve() if options.baseline else None
    unittest.main(argv=[sys.argv[0]] + remaining, verbosity=2)


if __name__ == "__main__":
    main()
