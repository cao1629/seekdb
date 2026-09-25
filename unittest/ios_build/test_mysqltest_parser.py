#!/usr/bin/env python3
"""Contract tests for lossless active mysqltest classification and adapters."""

import json
from pathlib import Path
import tempfile
import unittest


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parents[1]

import sys

sys.path.insert(0, str(SCRIPT_DIRECTORY))

import mysqltest_parser as parser  # noqa: E402
import run_mysqltest_phase as phase  # noqa: E402


class MysqltestParserTest(unittest.TestCase):
    """Protect corpus coverage, translation fidelity, and phase boundaries."""

    def test_discovers_all_active_and_ci_selected_cases(self):
        """Reuse the supported host runner selection without dropping active files."""
        cases = parser.discover_active_cases(REPOSITORY_ROOT)

        self.assertEqual(283, len(cases))
        self.assertEqual(272, sum(case.ci_selected for case in cases))
        self.assertEqual(len(cases), len({case.name for case in cases}))
        self.assertEqual(sorted(case.name for case in cases),
                         [case.name for case in cases])

    def test_recursive_source_preserves_errors_and_provenance(self):
        """Expand nested sources textually while retaining exact origin metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mysql_root = root / "tools/deploy/mysql_test"
            test = mysql_root / "t/demo.test"
            first = mysql_root / "include/first.inc"
            second = mysql_root / "include/nested/second.inc"
            result = mysql_root / "r/mysql/demo.result"
            second.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True, exist_ok=True)
            first.parent.mkdir(parents=True, exist_ok=True)
            result.parent.mkdir(parents=True, exist_ok=True)
            test.write_text(
                "--source mysql_test/include/first.inc\nSELECT 3;\n",
                encoding="utf-8")
            first.write_text(
                "--error 0,1062,ER_DUP_ENTRY\nSELECT 1;\n"
                "--source nested/second.inc\n",
                encoding="utf-8")
            second.write_text("SELECT 'two;still';\n", encoding="utf-8")
            result.write_text(
                "SELECT 1;\nvalue\n1\n"
                "SELECT 'two;still';\nvalue\ntwo;still\n"
                "SELECT 3;\nvalue\n3\n",
                encoding="utf-8")

            parsed = parser.parse_test_file(
                root, test, "demo", result_path=result)

        self.assertEqual(3, len(parsed.statements))
        self.assertEqual(("0", "1062", "ER_DUP_ENTRY"),
                         parsed.statements[0].expected_errors)
        self.assertEqual("SELECT 'two;still'", parsed.statements[1].sql)
        self.assertEqual("tools/deploy/mysql_test/include/first.inc",
                         parsed.statements[0].provenance.source_path)
        self.assertEqual(
            ("tools/deploy/mysql_test/t/demo.test",
             "tools/deploy/mysql_test/include/first.inc",
             "tools/deploy/mysql_test/include/nested/second.inc"),
            parsed.statements[1].provenance.include_stack)
        self.assertEqual("device-native", parsed.execution_class)
        self.assertEqual("lossless", parsed.device_applicability)
        self.assertEqual(("value", "two;still"),
                         parsed.statements[1].expected_output)

    def test_source_cycle_and_path_escape_are_rejected(self):
        """Fail closed for recursive cycles and includes outside mysql_test."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mysql_root = root / "tools/deploy/mysql_test"
            case = mysql_root / "t/cycle.test"
            include = mysql_root / "include/cycle.inc"
            case.parent.mkdir(parents=True)
            include.parent.mkdir(parents=True)
            case.write_text(
                "--source mysql_test/include/cycle.inc\n", encoding="utf-8")
            include.write_text(
                "--source mysql_test/t/cycle.test\n", encoding="utf-8")
            with self.assertRaisesRegex(parser.MysqltestParseError, "cycle"):
                parser.parse_test_file(root, case, "cycle")

            case.write_text("--source ../../outside.inc\n", encoding="utf-8")
            (root / "tools/outside.inc").write_text("SELECT 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(parser.MysqltestParseError, "escapes"):
                parser.parse_test_file(root, case, "escape")

    def test_unsupported_semantics_are_explicit_not_silently_dropped(self):
        """Classify every non-lossless mysqltest feature with a stable reason."""
        fixtures = {
            "connection": "connect (c,$HOST,u,p,test,1);\n",
            "process-shell": "--exec echo unsafe\n",
            "topology": "ALTER SYSTEM MAJOR FREEZE;\n",
            "result-rewrite": "--replace_regex /a/b/\nSELECT 1;\n",
        }
        for expected_reason, content in fixtures.items():
            with self.subTest(reason=expected_reason), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = root / "tools/deploy/mysql_test/t/demo.test"
                case.parent.mkdir(parents=True)
                case.write_text(content, encoding="utf-8")

                parsed = parser.parse_test_file(root, case, "demo")

                self.assertEqual("host-only", parsed.execution_class)
                self.assertEqual("not-applicable", parsed.device_applicability)
                self.assertIn(expected_reason, parsed.unsupported_reasons)
                self.assertEqual((), parsed.device_case_ids)

    def test_classification_is_complete_and_device_ids_are_stable(self):
        """Promote only cases backed by a reviewed transcript executor."""
        classified = parser.classify_active_corpus(REPOSITORY_ROOT)
        by_name = {case.name: case for case in classified}

        self.assertEqual(283, len(classified))
        self.assertEqual("device-native", by_name["empty_table"].execution_class)
        self.assertEqual(("ios.mysqltest.empty_table",),
                         by_name["empty_table"].device_case_ids)
        active = {case.name: case for case in parser.discover_active_cases(
            REPOSITORY_ROOT)}
        self.assertTrue(parser._reviewed_transcript_supported(
            active["empty_table"]))
        for name in ("distinct", "aggr_bug200109", "limit",
                     "expr.expr_nseq", "sqlancer_optimizer_regressions"):
            with self.subTest(name=name):
                self.assertEqual("host-only", by_name[name].execution_class)
                self.assertIn("device-transcript-unavailable",
                              by_name[name].unsupported_reasons)
        self.assertEqual("host-only", by_name["connection"].execution_class)
        self.assertIn("connection", by_name["connection"].unsupported_reasons)
        self.assertEqual("host-only",
                         by_name["fork_table.fork_table_merge"].execution_class)
        self.assertIn("source-unavailable",
                      by_name["fork_table.fork_table_merge"].unsupported_reasons)
        self.assertEqual(
            ("tools/deploy/mysql_test/include/show_rpl_debug_info.inc",),
            by_name["fork_table.fork_table_merge"].unsupported_details)

    def test_recursive_closure_matches_reviewed_corpus_shape(self):
        """Audit every include edge, including a selected missing dependency."""
        audit = parser.audit_source_closure(REPOSITORY_ROOT)

        self.assertEqual(316, audit.file_count)
        self.assertEqual(302, audit.source_directive_count)
        self.assertEqual(2, audit.maximum_depth)
        self.assertIn(
            "tools/deploy/mysql_test/include/show_rpl_debug_info.inc",
            audit.missing_sources)

    def test_phase_plan_separates_host_gate_and_one_device_case_per_source(self):
        """Keep host mysqltest evidence distinct from independently resumable device cases."""
        plan = phase.build_phase_plan(REPOSITORY_ROOT)
        device = [case for case in plan if case.execution_class == "device-native"]
        host = [case for case in plan if case.case_id == "ios.mysqltest.host-gate"]

        self.assertTrue(device)
        self.assertEqual(["ios.mysqltest.empty_table"],
                         [case.case_id for case in device])
        self.assertEqual(len(device), len({case.source_name for case in device}))
        self.assertEqual(1, len(host))
        self.assertEqual("host-only", host[0].execution_class)
        self.assertTrue(all(case.failure_key == case.source_name for case in device))
        self.assertTrue(all(case.requires_sql_restart_followup for case in device))

    def test_resume_selects_only_failed_and_pending_source_cases(self):
        """Never rerun passed device sources while retrying failed and pending ones."""
        plan = (
            phase.MysqltestPhaseCase("ios.mysqltest.a", "a", "device-native", "a", True),
            phase.MysqltestPhaseCase("ios.mysqltest.b", "b", "device-native", "b", True),
            phase.MysqltestPhaseCase("ios.mysqltest.c", "c", "device-native", "c", True),
            phase.MysqltestPhaseCase("ios.mysqltest.d", "d", "device-native", "d", True),
        )
        selected = phase.select_resume_cases(
            plan, {"ios.mysqltest.a": "passed", "ios.mysqltest.b": "failed",
                   "ios.mysqltest.c": "pending", "ios.mysqltest.d": "running"})

        self.assertEqual(["ios.mysqltest.b", "ios.mysqltest.c"],
                         [case.case_id for case in selected])

    def test_cpp_registry_and_classification_manifest_are_wired(self):
        """Require the generated device suite and reviewed manifest boundary."""
        source = (SCRIPT_DIRECTORY / "mysqltest_device_cases.cpp").read_text(
            encoding="utf-8")
        cmake = (SCRIPT_DIRECTORY / "app/CMakeLists.txt").read_text(
            encoding="utf-8")
        manifest = json.loads((SCRIPT_DIRECTORY / "ios-test-classification.json")
                              .read_text(encoding="utf-8"))

        self.assertIn("make_mysqltest_device_registry", source)
        self.assertIn("expected_transcript", source)
        self.assertIn("expected_affected_rows", source)
        for fragment in (
                "set @@session.explicit_defaults_for_timestamp=off",
                "select count(*) from t1", "count(*)", "nr\\tb\\tstr"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertIn("../mysqltest_device_cases.cpp", cmake)
        self.assertIn("mysqltest-device-lossless", manifest["classifications"])

    def test_host_gate_requires_exact_272_case_success(self):
        """Keep host mysqltest proof separate and exact before accepting it."""
        selected = [
            case.name for case in parser.discover_active_cases(REPOSITORY_ROOT)
            if case.ci_selected]
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "host.json"
            result.write_text(json.dumps({
                "success": True,
                "error": None,
                "failed_cases": [],
                "case_count": 272,
                "cases": selected,
            }), encoding="utf-8")
            summary = phase.validate_host_gate(REPOSITORY_ROOT, result)
            self.assertEqual({
                "execution_class": "host-only",
                "case_count": 272,
                "success": True,
            }, summary)

            payload = json.loads(result.read_text(encoding="utf-8"))
            payload["cases"] = selected[:-1]
            result.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_gate(REPOSITORY_ROOT, result)


if __name__ == "__main__":
    unittest.main()
