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
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


DEFAULT_RUNNER = (
    Path(__file__).resolve().parents[4] / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
)
OPTIONS = {"runner": DEFAULT_RUNNER, "baseline": None}
RECORDER = "migration/judge/families/memory/memory_scenarios.py"
ERROR_LINE = (
    "ERROR 11049 (HY000): Exceed query memory limit (mem_limit=10737418, mem_hold={}),  "
    "please check whether the query_memory_limit_percentage configuration item is reasonable."
)
SCENARIOS = ("work_area_spill", "query_memory_limit", "hash_join_depth")


def load_runner(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario_text(name, mem_hold=11020166, error_line=None, extra=""):
    lines = ["-- {}".format(name), "-- recorder sha256 0000", "select 1;", "1"]
    if name == "query_memory_limit":
        lines.append("select count(*) from t_qm;")
        lines.append(error_line if error_line is not None else ERROR_LINE.format(mem_hold))
        lines.append("-- check: the statement failed with error 11049: yes")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def write_recording(directory, texts, recorder=RECORDER):
    directory.mkdir(parents=True)
    for name, text in texts.items():
        (directory / (name + ".result")).write_text(text)
    manifest = {
        "finished": True,
        "max_retries": 0,
        "retried_cases": {},
        "error": None,
        "failed_cases": [],
        "cases": list(texts),
        "mysqltest_sha256": "m",
        "obclient_sha256": "o",
        "init_sql_sha256": "i",
        "init_user_sql_sha256": "u",
        "sdb_sha256": "s",
        "tools_deploy_tree": "t",
        "tools_deploy_status": "",
        "fresh_instance_per_case": True,
        "seekdb_sha256": "b",
        "runner_sha256": "r",
        "repo_head": "h",
        "outcomes": dict(
            (name, {"exit_code": 0, "recorded": True, "partial": False}) for name in texts
        ),
    }
    if recorder is not None:
        manifest["recorder"] = recorder
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


class MemHoldMaskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner(OPTIONS["runner"], "runner_under_test")
        cls.baseline = (
            load_runner(OPTIONS["baseline"], "runner_baseline") if OPTIONS["baseline"] else None
        )

    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="mem-hold-offline-"))
        self.addCleanup(shutil.rmtree, str(self.temporary))
        self.counter = 0
        self.pinned = self.runner.MEM_HOLD_LIST_SHA256
        self.addCleanup(setattr, self.runner, "MEM_HOLD_LIST_SHA256", self.pinned)
        masks = dict(self.runner.COMPARE_MASKS)
        self.addCleanup(setattr, self.runner, "COMPARE_MASKS", masks)

    def recording(self, changes=None, recorder=RECORDER):
        self.counter += 1
        texts = dict((name, scenario_text(name)) for name in SCENARIOS)
        texts.update(changes or {})
        return write_recording(self.temporary / "rec-{}".format(self.counter), texts, recorder)

    def compare(self, left, right, *extra, module=None):
        module = module or self.runner
        self.counter += 1
        out = self.temporary / "compare-{}.json".format(self.counter)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = module.main(
                    ["compare", "--left", str(left), "--right", str(right), "--out", str(out)]
                    + list(extra)
                )
            except SystemExit as exc:
                code = exc.code
        payload = json.loads(out.read_text()) if out.is_file() else None
        return code, stdout.getvalue() + stderr.getvalue(), payload

    def entry(self, payload, name):
        return [case for case in payload["cases"] if case["case"] == name][0]

    def statistics(self, payload):
        return [item for item in payload["mask_lists"] if item["name"] == "mem-hold"][0]

    def test_only_the_number_differs(self):
        left = self.recording()
        right = self.recording({"query_memory_limit": scenario_text("query_memory_limit", 11085702)})
        code, output, payload = self.compare(left, right)
        self.assertEqual(code, 1)
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 0, output)
        entry = self.entry(payload, "query_memory_limit")
        self.assertEqual(entry["status"], "different")
        self.assertEqual(entry["masked_status"], "identical")
        self.assertEqual(entry["verdict"], "identical")
        statistics = self.statistics(payload)
        self.assertEqual(statistics["lines_masked_left"], 1)
        self.assertEqual(statistics["lines_masked_right"], 1)
        self.assertEqual(statistics["values_differ"], 1)
        self.assertIn("mem_hold= left 11020166, right 11085702; masked", output)
        self.assertEqual(self.entry(payload, "work_area_spill")["masks"], [])

    def test_mem_limit_stays_exact(self):
        left = self.recording()
        right = self.recording(
            {
                "query_memory_limit": scenario_text(
                    "query_memory_limit",
                    error_line=ERROR_LINE.format(11020166).replace("10737418", "21474836"),
                )
            }
        )
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "query_memory_limit")["masked_status"], "different")

    def test_text_after_the_number_stays_exact(self):
        left = self.recording()
        right = self.recording(
            {
                "query_memory_limit": scenario_text(
                    "query_memory_limit",
                    error_line=ERROR_LINE.format(11020166).replace("reasonable.", "reasonable!"),
                )
            }
        )
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 1)

    def test_error_code_and_sqlstate_stay_exact(self):
        for old, new in (("ERROR 11049", "ERROR 4013"), ("(HY000)", "(HY001)")):
            with self.subTest(change=new):
                left = self.recording()
                right = self.recording(
                    {
                        "query_memory_limit": scenario_text(
                            "query_memory_limit",
                            error_line=ERROR_LINE.format(11085702).replace(old, new),
                        )
                    }
                )
                code, output, payload = self.compare(left, right, "--mask", "mem-hold")
                self.assertEqual(code, 1)
                statistics = self.statistics(payload)
                self.assertEqual(statistics["lines_masked_right"], 0)
                self.assertIn("mem-hold: lines masked left 1, right 0", output)

    def test_other_lines_stay_exact(self):
        left = self.recording()
        right = self.recording(
            {"query_memory_limit": scenario_text("query_memory_limit", 11085702, extra="select 2;")}
        )
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 1)

    def test_the_line_in_an_unlisted_scenario_stays_exact(self):
        left = self.recording(
            {"hash_join_depth": scenario_text("hash_join_depth", extra=ERROR_LINE.format(1))}
        )
        right = self.recording(
            {"hash_join_depth": scenario_text("hash_join_depth", extra=ERROR_LINE.format(2))}
        )
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "hash_join_depth")["verdict"], "different")

    def test_a_number_that_is_missing_is_not_masked(self):
        left = self.recording()
        right = self.recording(
            {
                "query_memory_limit": scenario_text(
                    "query_memory_limit", error_line=ERROR_LINE.format("")
                )
            }
        )
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 1)
        self.assertEqual(self.statistics(payload)["lines_masked_right"], 0)

    def test_recordings_of_other_recorders_are_refused(self):
        for recorder in (None, "migration/judge/harness/restart_scenarios.py"):
            with self.subTest(recorder=recorder):
                left = self.recording()
                right = self.recording(recorder=recorder)
                code, output, payload = self.compare(left, right, "--mask", "mem-hold")
                self.assertEqual(code, 2)
                self.assertIsNone(payload)
                self.assertIn("--mask mem-hold applies only to recordings made by", output)

    def test_the_list_must_have_the_pinned_sha256(self):
        left = self.recording()
        right = self.recording({"query_memory_limit": scenario_text("query_memory_limit", 1)})
        masks = dict(self.runner.COMPARE_MASKS)
        masks["mem-hold"] = masks["mem-hold"]._replace(list_sha256="0" * 64)
        self.runner.COMPARE_MASKS = masks
        code, output, payload = self.compare(left, right, "--mask", "mem-hold")
        self.assertEqual(code, 2)
        self.assertIn("pins the signed-off", output)

    def test_the_checked_in_list(self):
        loaded = self.runner.load_masks({"mem-hold"})
        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            loaded[0]["data"],
            {
                "query_memory_limit": [
                    b"ERROR 11049 (HY000): Exceed query memory limit (mem_limit=10737418, mem_hold="
                ]
            },
        )

    def test_bad_list_lines_are_refused(self):
        for text in (
            "query_memory_limit\n",
            "query_memory_limit\tERROR 11049 (HY000): Exceed query memory limit (mem_limit=1, \n",
            "query_memory_limit\ta mem_hold=b mem_hold=\n",
            " query_memory_limit\tx mem_hold=\n",
            "a\tx mem_hold=\na\tx mem_hold=\n",
        ):
            with self.subTest(text=text):
                path = self.temporary / "list-{}.txt".format(self.counter)
                self.counter += 1
                path.write_text(text)
                sha256, lines = self.runner.read_mask_list(path)
                with self.assertRaises(self.runner.RunnerError):
                    self.runner.load_mem_hold_list(path, lines)

    def test_row_order_list_is_pinned_and_loads(self):
        loaded = self.runner.load_masks({"row-order"})
        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(loaded[0]["data"]), 20)
        self.assertEqual(sum(len(rows) for rows in loaded[0]["data"].values()), 92)

    def test_default_output_matches_baseline(self):
        if self.baseline is None:
            self.skipTest("no --baseline runner given")
        left = self.recording()
        right = self.recording({"query_memory_limit": scenario_text("query_memory_limit", 11085702)})
        new = self.compare(left, right)
        old = self.compare(left, right, module=self.baseline)
        self.assertEqual(new[0], old[0])
        self.assertEqual(new[1], old[1])
        self.assertEqual(new[2], old[2])


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
