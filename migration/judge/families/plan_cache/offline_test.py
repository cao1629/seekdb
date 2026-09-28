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
import importlib.util
import io
import json
import shutil
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
READ = [
    "-uroot",
    "-A",
    "-B",
    "-N",
    "--proxy-mode",
    "--init-command=SET ob_enable_plan_cache = 0",
    "-e",
    "SELECT access_count, hit_count FROM oceanbase.__all_virtual_plan_cache_stat",
]


def load_runner(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Side(object):
    def __init__(self, directory, plan_cache_stats=True):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True)
        self.plan_cache_stats = plan_cache_stats
        self.cases = []
        self.rows = []
        self.seconds = {}
        self.checks = []
        self.failed = []
        self.outcomes = {}

    def case(self, name, hits=None, misses=None, seconds=0.05, content=b"select 1;\n1\n"):
        self.cases.append(name)
        (self.directory / (name + ".result")).write_bytes(content)
        self.outcomes[name] = {"exit_code": 0, "recorded": True, "partial": False}
        if hits is not None:
            self.rows.append((name, hits, misses))
            self.seconds[name] = seconds
        self.checks.append({"instance": "passed-" + name, "passed": True, "attempts": []})
        return self

    def finish(self, **changes):
        manifest = {
            "finished": True,
            "max_retries": 0,
            "retried_cases": {},
            "error": None,
            "failed_cases": self.failed,
            "cases": self.cases,
            "mysqltest_sha256": "m",
            "obclient_sha256": "o",
            "init_sql_sha256": "i",
            "init_user_sql_sha256": "u",
            "sdb_sha256": "s",
            "tools_deploy_tree": "t",
            "tools_deploy_status": "",
            "fresh_instance_per_case": True,
            "seekdb_parameters": ["plan_cache_evict_interval=1d"],
            "ps_protocol": False,
            "compress": False,
            "plan_cache_stats": self.plan_cache_stats,
            "plan_cache_read": list(READ) if self.plan_cache_stats else None,
            "test_dir_sha256": None,
            "seekdb_sha256": "b",
            "runner_sha256": "r",
            "repo_head": "h",
            "test_dir": None,
            "outcomes": self.outcomes,
        }
        if self.plan_cache_stats:
            manifest["plan_cache_errors"] = []
            manifest["plan_cache_seconds"] = self.seconds
            manifest["plan_cache_checks"] = self.checks
            lines = ["case\thits\tmisses"] + [
                "{}\t{}\t{}".format(name, hits, misses) for name, hits, misses in self.rows
            ]
            (self.directory / "plan_cache.tsv").write_text("\n".join(lines) + "\n")
        manifest.update(changes)
        (self.directory / "manifest.json").write_text(json.dumps(manifest))
        return self.directory


class NotComparableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner(OPTIONS["runner"], "runner_under_test")
        cls.baseline = (
            load_runner(OPTIONS["baseline"], "runner_baseline")
            if OPTIONS["baseline"]
            else None
        )

    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="pc-offline-")
        self.addCleanup(shutil.rmtree, self.temporary)
        self.counter = 0
        self.pinned = getattr(self.runner, "PLAN_CACHE_NOT_COMPARABLE_SHA256", None)
        self.addCleanup(self.restore_pin)

    def restore_pin(self):
        if self.pinned is not None:
            self.runner.PLAN_CACHE_NOT_COMPARABLE_SHA256 = self.pinned

    def path(self, *parts):
        return Path(self.temporary).joinpath(*parts)

    def side(self, name, plan_cache_stats=True):
        self.counter += 1
        return Side(self.path("{}-{}".format(name, self.counter)), plan_cache_stats)

    def listing(self, text):
        self.counter += 1
        path = self.path("not-comparable-{}.txt".format(self.counter))
        path.write_text(text)
        self.runner.PLAN_CACHE_NOT_COMPARABLE_SHA256 = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
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

    def pair(self, left_counts, right_counts):
        left = self.side("left")
        right = self.side("right")
        for name, counts in left_counts:
            left.case(name, *counts)
        for name, counts in right_counts:
            right.case(name, *counts)
        return left.finish(), right.finish()

    def entry(self, payload, name):
        return [case for case in payload["plan_cache"]["cases"] if case["case"] == name][0]

    def test_without_list_a_hits_difference_fails(self):
        left, right = self.pair([("a", (7, 1))], [("a", (8, 1))])
        code, output, payload = self.compare(left, right, "--require-plan-cache")
        self.assertEqual(code, 1)
        self.assertIn("plan-cache different a", output)
        self.assertNotIn("not_comparable", payload["plan_cache"])
        self.assertNotIn("not_compared", payload["plan_cache"]["summary"])
        self.assertNotIn("listed", self.entry(payload, "a"))
        self.assertNotIn("not_compared=", output)

    def test_listed_hits_difference_is_not_compared(self):
        left, right = self.pair([("a", (7, 1)), ("b", (5, 2))], [("a", (8, 1)), ("b", (5, 2))])
        listing = self.listing("# timers\na hits  # 7 and 8 hits\n")
        code, output, payload = self.compare(
            left, right, "--require-plan-cache", "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0, output)
        entry = self.entry(payload, "a")
        self.assertEqual(entry["status"], "not-compared")
        self.assertEqual(entry["listed"], ["hits"])
        self.assertEqual(entry["not_compared"], ["hits"])
        summary = payload["plan_cache"]["summary"]
        self.assertEqual(
            (summary["identical"], summary["different"], summary["not_compared"]), (1, 0, 1)
        )
        report = payload["plan_cache"]["not_comparable"]
        self.assertEqual(report["not_compared"], ["a"])
        self.assertEqual(report["listed_cases"], 1)
        self.assertEqual(len(report["list_sha256"]), 64)
        self.assertIn("plan-cache not-compared a", output)
        self.assertIn("left hits=7 misses=1; right hits=8 misses=1", output)
        self.assertIn("listed as not comparable: hits; compared: misses", output)
        self.assertIn("not_compared=1", output)
        self.assertIn("plan cache not comparable: {} (sha256 ".format(listing), output)
        self.assertTrue(payload["success"])

    def test_listed_hits_does_not_cover_misses(self):
        left, right = self.pair([("a", (7, 1))], [("a", (7, 2))])
        listing = self.listing("a hits\n")
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "a")["status"], "different")
        self.assertIn("plan-cache different a", output)
        self.assertIn("listed as not comparable: hits; compared: misses", output)
        self.assertEqual(payload["plan_cache"]["not_comparable"]["listed_different"], ["a"])
        left, right = self.pair([("a", (7, 1))], [("a", (6, 2))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "a")["status"], "different")

    def test_listed_misses_does_not_cover_hits(self):
        listing = self.listing("a misses\n")
        left, right = self.pair([("a", (7, 1))], [("a", (7, 3))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.entry(payload, "a")["not_compared"], ["misses"])
        self.assertIn("listed as not comparable: misses; compared: hits", output)
        left, right = self.pair([("a", (7, 1))], [("a", (9, 1))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)

    def test_listed_both(self):
        listing = self.listing("a both\n")
        left, right = self.pair([("a", (7, 1))], [("a", (9, 4))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.entry(payload, "a")["listed"], ["hits", "misses"])
        self.assertEqual(self.entry(payload, "a")["not_compared"], ["hits", "misses"])
        self.assertIn("listed as not comparable: hits, misses; compared: nothing", output)

    def test_listed_identical_case_stays_identical(self):
        listing = self.listing("a both\n")
        left, right = self.pair([("a", (7, 1))], [("a", (7, 1))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.entry(payload, "a")["status"], "identical")
        self.assertEqual(payload["plan_cache"]["not_comparable"]["listed_identical"], ["a"])
        self.assertNotIn("listed as not comparable", output)

    def test_unlisted_difference_still_fails(self):
        listing = self.listing("a both\n")
        left, right = self.pair([("a", (7, 1)), ("b", (1, 1))], [("a", (9, 4)), ("b", (2, 1))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "b")["status"], "different")
        self.assertNotIn("listed", self.entry(payload, "b"))
        self.assertEqual(payload["plan_cache"]["summary"]["different"], 1)
        self.assertEqual(payload["plan_cache"]["summary"]["not_compared"], 1)

    def test_listed_missing_case_still_fails(self):
        listing = self.listing("a both\n")
        left = self.side("l").case("a", 7, 1).finish()
        right = self.side("r").case("a").finish()
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.entry(payload, "a")["status"], "missing")
        self.assertEqual(payload["plan_cache"]["not_comparable"]["listed_missing"], ["a"])

    def test_result_differences_still_fail(self):
        listing = self.listing("a both\n")
        left = self.side("l").case("a", 7, 1).finish()
        right = self.side("r").case("a", 9, 1, content=b"select 1;\n2\n").finish()
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["summary"]["different"], 1)

    def test_recording_problems_still_fail(self):
        listing = self.listing("a both\n")
        left = self.side("l").case("a", 7, 1).finish()
        right = self.side("r").case("a", 9, 1)
        right.checks[0]["passed"] = False
        right = right.finish()
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertIn("right recording failed the plan cache read check on: passed-a", output)
        right = self.side("r2").case("a", 9, 1).finish(finished=False)
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertIn("right recording did not finish", output)

    def test_list_needs_plan_cache_recordings(self):
        listing = self.listing("a both\n")
        left = self.side("l", plan_cache_stats=False).case("a").finish()
        right = self.side("r", plan_cache_stats=False).case("a").finish()
        code, output, payload = self.compare(left, right)
        self.assertEqual(code, 0, output)
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 1)
        self.assertIn(
            "--plan-cache-not-comparable needs two recordings made with --plan-cache-stats",
            output,
        )
        self.assertEqual(payload["plan_cache"]["not_comparable"]["not_in_recordings"], ["a"])

    def test_names_outside_the_recordings(self):
        listing = self.listing("a hits\nno.such_case both\n")
        left, right = self.pair([("a", (7, 1))], [("a", (8, 1))])
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(
            payload["plan_cache"]["not_comparable"]["not_in_recordings"], ["no.such_case"]
        )
        self.assertIn("not_in_recordings=1", output)

    def test_bad_lists_are_refused(self):
        left, right = self.pair([("a", (7, 1))], [("a", (8, 1))])
        for text in (
            "",
            "# only a comment\n",
            "a\n",
            "a hits misses\n",
            "a hit\n",
            "a HITS\n",
            "a hits\na both\n",
        ):
            code, output, payload = self.compare(
                left, right, "--plan-cache-not-comparable", self.listing(text)
            )
            self.assertEqual(code, 2, text)
            self.assertIsNone(payload, text)
        code, output, payload = self.compare(
            left, right, "--plan-cache-not-comparable", self.path("absent.txt")
        )
        self.assertEqual(code, 2)

    def test_list_must_have_the_pinned_sha256(self):
        if self.pinned is None:
            self.skipTest("this runner pins no plan cache not-comparable list")
        left, right = self.pair([("a", (7, 1))], [("a", (8, 1))])
        listing = self.listing("a hits\n")
        code, output, payload = self.compare(left, right, "--plan-cache-not-comparable", listing)
        self.assertEqual(code, 0, output)
        self.restore_pin()
        code, output, payload = self.compare(left, right, "--plan-cache-not-comparable", listing)
        self.assertEqual(code, 2, output)
        self.assertIsNone(payload)
        self.assertIn("PLAN_CACHE_NOT_COMPARABLE_SHA256", output)

    def test_checked_in_list_is_the_pinned_one(self):
        if self.pinned is None:
            self.skipTest("this runner pins no plan cache not-comparable list")
        self.restore_pin()
        loaded = self.runner.load_plan_cache_not_comparable(
            Path(__file__).resolve().parent / "not-comparable.list"
        )
        configured = set(
            case.name for case in self.runner.discover_cases(self.runner.runner_repo_root())
        )
        self.assertEqual(set(loaded["cases"]), configured)
        words = [loaded["cases"][name] for name in sorted(configured)]
        self.assertEqual(words.count(("hits",)), 70)
        self.assertEqual(words.count(("hits", "misses")), 202)
        self.assertNotIn(("misses",), words)

    def test_with_known_failures(self):
        left = self.side("l").case("a", 7, 1)
        right = self.side("r").case("a", 8, 1)
        for side in (left, right):
            side.cases.append("f")
            side.failed.append("f")
            (side.directory / "f.partial").write_bytes(b"x\n")
            side.outcomes["f"] = {
                "exit_code": 1,
                "recorded": False,
                "partial": True,
                "failure_lines": ["mysqltest: At line 1: failed"],
            }
            side.rows.append(("f", 3, 1))
            side.seconds["f"] = 0.1
        left, right = left.finish(), right.finish()
        known = self.listing("f\n")
        listing = self.listing("a hits\n")
        code, output, payload = self.compare(
            left, right, "--known-failures", known, "--plan-cache-not-comparable", listing
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(payload["known_failures"]["accepted"], ["f"])
        code, output, payload = self.compare(left, right, "--known-failures", known)
        self.assertEqual(code, 1)

    def test_default_output_matches_baseline(self):
        if self.baseline is None:
            self.skipTest("no --baseline runner given")
        left = self.side("l").case("same", 7, 1).case("hits", 7, 1).case("gone", 3, 1)
        right = self.side("r").case("same", 7, 1).case("hits", 9, 1, seconds=25.0).case("gone")
        left, right = left.finish(), right.finish()
        new = self.compare(left, right, "--require-plan-cache")
        old = self.compare(left, right, "--require-plan-cache", module=self.baseline)
        self.assertEqual(new[0], old[0])
        self.assertEqual(new[1], old[1])
        new_payload = dict(new[2])
        old_payload = dict(old[2])
        self.assertEqual(new_payload, old_payload)
        plain_left = self.side("pl", plan_cache_stats=False).case("x").finish()
        plain_right = self.side("pr", plan_cache_stats=False).case("x").finish()
        new = self.compare(plain_left, plain_right)
        old = self.compare(plain_left, plain_right, module=self.baseline)
        self.assertEqual((new[0], new[1], new[2]), (old[0], old[1], old[2]))


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
