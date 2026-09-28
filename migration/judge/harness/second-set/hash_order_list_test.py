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

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
HELPER_PATH = HERE / "hash_order_list.py"
RUNNER_PATH = HERE.parents[3] / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load("hash_order_list_under_test", HELPER_PATH)
RUNNER = HELPER.load_runner(RUNNER_PATH)
LISTED = "select c from t2;"
CANDIDATE_TEXT = "select c from t2"
RESULT = "select c from t2;\nc\n1\n2\n"


class ReplaceBeforeNextOutputTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="hash-order-helper-"))
        self.addCleanup(shutil.rmtree, str(self.directory))

    def resolve(self, test_lines, result_text):
        path = self.directory / "case.test"
        path.write_text("\n".join(test_lines) + "\n", encoding="utf-8")
        items = HELPER.read_test_items(path)
        candidate = HELPER.Candidate("case", 1, CANDIDATE_TEXT)
        return HELPER.resolve(RUNNER, candidate, items, result_text.encode("utf-8"))

    def assert_resolved(self, test_lines, result_text, rows=2):
        row = self.resolve(test_lines, result_text)
        self.assertEqual(int(row["rows"]), rows)
        return row

    def assert_gives_up(self, test_lines, result_text, word):
        with self.assertRaises(HELPER.Unresolved) as caught:
            self.resolve(test_lines, result_text)
        self.assertIn("a {} follows".format(word), str(caught.exception))

    def test_regex_that_cannot_match_the_next_echo_is_passed_over(self):
        self.assert_resolved(
            [LISTED, "--replace_regex /xyz/abc/", "select c from t3;"],
            RESULT + "select c from t3;\nc\n3\n",
        )

    def test_regex_that_matches_the_next_echo_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /t3/T3/", "select c from t3;"],
            RESULT + "select c from T3;\nc\n3\n",
            "replace_regex",
        )

    def test_regex_matching_without_regard_to_case_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /T3/x/i", "select c from t3;"],
            RESULT + "select c from x;\nc\n3\n",
            "replace_regex",
        )

    def test_regex_of_several_patterns_gives_up_when_any_matches(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /xyz/abc/ /from/FROM/", "select c from t3;"],
            RESULT + "select c FROM t3;\nc\n3\n",
            "replace_regex",
        )

    def test_regex_the_helper_cannot_judge_gives_up(self):
        for pattern in ("/[[:digit:]]+/x/", "/(z)\\1/x/", "/\\d+/x/", "/$var/x/"):
            with self.subTest(pattern=pattern):
                self.assert_gives_up(
                    [LISTED, "--replace_regex {}".format(pattern), "select c from t3;"],
                    RESULT + "select c from t3;\nc\n3\n",
                    "replace_regex",
                )

    def test_malformed_regex_argument_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /t3", "select c from t3;"],
            RESULT + "select c from t3;\nc\n3\n",
            "replace_regex",
        )

    def test_result_string_that_is_not_in_the_next_echo_is_passed_over(self):
        self.assert_resolved(
            [LISTED, "--replace_result zz yy", "select c from t3;"],
            RESULT + "select c from t3;\nc\n3\n",
        )

    def test_result_string_in_the_next_echo_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_result t3 T3", "select c from t3;"],
            RESULT + "select c from T3;\nc\n3\n",
            "replace_result",
        )

    def test_result_strings_the_helper_cannot_judge_give_up(self):
        for argument in ("t3", "'a b' c", "$v w", "a\\ b c"):
            with self.subTest(argument=argument):
                self.assert_gives_up(
                    [LISTED, "--replace_result {}".format(argument), "select c from t3;"],
                    RESULT + "select c from t3;\nc\n3\n",
                    "replace_result",
                )

    def test_regex_matching_the_next_echo_text_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /done/DONE/", "--echo done"],
            RESULT + "DONE\n",
            "replace_regex",
        )

    def test_regex_not_matching_the_next_echo_text_is_passed_over(self):
        self.assert_resolved(
            [LISTED, "--replace_regex /xyz/abc/", "--echo done"],
            RESULT + "done\n",
        )

    def test_regex_matching_a_line_of_a_multi_line_next_statement_gives_up(self):
        self.assert_gives_up(
            [LISTED, "--replace_regex /t4/T4/", "select c", "  from t4;"],
            RESULT + "select c\nfrom T4;\nc\n4\n",
            "replace_regex",
        )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + sys.argv[1:], verbosity=2)
