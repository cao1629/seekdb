#!/usr/bin/env python3
"""Verify the iOS device registry and device-origin evidence contract."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "unittest/ios_build"
RUNNER = IOS / "run_device_suite.py"


class DeviceRegistryNativeContractTests(unittest.TestCase):
    """Compile a host harness around the portable registry implementation."""

    def test_registry_is_stable_rejects_duplicates_and_filters_suites(self):
        """Require stable IDs, duplicate rejection, timeout metadata, and filters."""
        source = textwrap.dedent(r'''
            #include "device_test_registry.h"
            #include <iostream>
            using namespace seekdb::ios_test;
            int pass(TestContext &context) {
              context.assert_true("truth", true, "must pass");
              return 0;
            }
            int main() {
              DeviceTestRegistry registry;
              std::cout << registry.add({"z.case", "other", 9, pass}) << "\n";
              std::cout << registry.add({"a.case", "smoke", 7, pass}) << "\n";
              std::cout << registry.add({"a.case", "smoke", 7, pass}) << "\n";
              for (const DeviceTestCase *test : registry.select("smoke", "a.*")) {
                std::cout << test->id << ":" << test->timeout_seconds << "\n";
              }
              for (const std::string &id : registry.case_ids()) std::cout << id << "\n";
            }
        ''')
        output = self._compile_and_run(source)
        self.assertEqual(["1", "1", "0", "a.case:7", "a.case", "z.case"], output.splitlines())

    def test_suite_writes_and_flushes_the_required_event_sequence(self):
        """Require an ordered, device-origin JSONL record for every transition."""
        source = textwrap.dedent(r'''
            #include "device_test_registry.h"
            #include <iostream>
            using namespace seekdb::ios_test;
            int pass(TestContext &context) {
              context.assert_true("first", true, "ok");
              context.assert_equal("second", 4, 4, "equal integers");
              return 0;
            }
            int main(int, char **argv) {
              DeviceTestRegistry registry;
              registry.add({"smoke.pass", "smoke", 12, pass});
              return run_device_suite(registry, "smoke", "smoke.*", "run-1", "build-1", argv[1]);
            }
        ''')
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "evidence.jsonl"
            self._compile_and_run(source, [str(evidence)])
            records = [json.loads(line) for line in evidence.read_text().splitlines()]
        self.assertEqual(
            ["run_start", "case_start", "assertion", "assertion", "case_end", "run_complete"],
            [record["event"] for record in records],
        )
        self.assertTrue(all(record["run_id"] == "run-1" for record in records))
        self.assertTrue(all(record["build_id"] == "build-1" for record in records))
        self.assertTrue(all(record["origin"] == "device" for record in records))
        self.assertEqual(12, records[1]["timeout_seconds"])
        self.assertEqual(0, records[-1]["result"])

    def _compile_and_run(self, source, arguments=()):
        """Compile and execute a temporary C++ contract harness."""
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            harness = temp_path / "harness.cpp"
            binary = temp_path / "harness"
            harness.write_text(source)
            subprocess.run(
                ["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(IOS),
                 str(harness), str(IOS / "device_test_registry.cpp"),
                 str(IOS / "device_evidence.cpp"), "-o", str(binary)],
                check=True, capture_output=True, text=True,
            )
            return subprocess.run([str(binary), *arguments], check=True, capture_output=True,
                                  text=True).stdout


class DeviceEvidenceValidationTests(unittest.TestCase):
    """Reject incomplete, stale, duplicated, or host-authored device evidence."""

    @classmethod
    def setUpClass(cls):
        """Load the host runner as a module for focused validation tests."""
        spec = importlib.util.spec_from_file_location("run_device_suite", RUNNER)
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def valid_records(self):
        """Return one complete passing device run used as a mutation fixture."""
        common = {"run_id": "run-1", "build_id": "build-1", "origin": "device"}
        return [
            {**common, "event": "run_start", "suite": "smoke", "filter": "smoke.*",
             "registry_case_ids": ["smoke.pass"], "selected_case_ids": ["smoke.pass"]},
            {**common, "event": "case_start", "case_id": "smoke.pass", "timeout_seconds": 12},
            {**common, "event": "assertion", "case_id": "smoke.pass", "assertion": "truth",
             "passed": True, "diagnostic": "ok"},
            {**common, "event": "case_end", "case_id": "smoke.pass", "result": 0},
            {**common, "event": "run_complete", "result": 0, "selected_count": 1,
             "completed_count": 1},
        ]

    def test_accepts_complete_current_device_evidence(self):
        """Accept a complete passing run bound to the requested identity and coverage."""
        summary = self.runner.validate_records(
            self.valid_records(), "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")
        self.assertEqual({"smoke.pass": 0}, summary["case_results"])

    def test_rejects_missing_completion_duplicate_stale_and_nonzero_results(self):
        """Reject each required evidence-integrity failure independently."""
        variants = []
        variants.append(self.valid_records()[:-1])
        duplicate = self.valid_records()
        duplicate.insert(-1, dict(duplicate[-2]))
        variants.append(duplicate)
        stale_run = self.valid_records()
        stale_run[2]["run_id"] = "old-run"
        variants.append(stale_run)
        stale_build = self.valid_records()
        stale_build[2]["build_id"] = "old-build"
        variants.append(stale_build)
        case_failure = self.valid_records()
        case_failure[-2]["result"] = 3
        case_failure[-1]["result"] = 3
        variants.append(case_failure)
        for records in variants:
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.runner.validate_records(
                    records, "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")

    def test_rejects_incomplete_coverage_and_host_generated_assertions(self):
        """Require independently expected case coverage and device-authored assertions."""
        incomplete = self.valid_records()
        host_assertion = self.valid_records()
        host_assertion[2]["origin"] = "host"
        with self.assertRaises(ValueError):
            self.runner.validate_records(
                incomplete, "run-1", "build-1", ["smoke.pass", "smoke.missing"],
                "smoke", "smoke.*")
        with self.assertRaises(ValueError):
            self.runner.validate_records(
                host_assertion, "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")

    def test_runner_persists_no_raw_devicectl_metadata(self):
        """Allow only validated JSONL evidence under the repository output directory."""
        source = RUNNER.read_text()
        self.assertNotIn("launch.json", source)
        self.assertNotIn("device-info", source)
        self.assertNotIn("capture_output=False", source)
        self.assertIn("ALLOWED_EVIDENCE_NAME", source)

    def test_runner_accepts_only_bounded_data_directory_names(self):
        """Keep device suites isolated without permitting sandbox path traversal."""
        self.assertEqual("ios-device-tests", self.runner.validate_data_name("ios-device-tests"))
        for invalid in ("", "../seekdb", "with/slash", "a" * 65):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                self.runner.validate_data_name(invalid)

    def test_runner_requires_current_clean_stop_after_suite_completion(self):
        """Do not finish while the engine is still stopping or cleanup failed."""
        status = {"run_id": "run-1", "build_id": "build-1", "state": "Stopped", "result": 0,
                  "suite_result": 0, "cleanup_status": 7, "cleanup_error": 0,
                  "working_directory_restored": True}
        self.runner.validate_terminal_status(status, "run-1", "build-1")
        for changes in ({"run_id": "old"}, {"state": "Stopping"}, {"suite_result": 2},
                        {"cleanup_status": 6}, {"cleanup_error": -1},
                        {"working_directory_restored": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.runner.validate_terminal_status({**status, **changes}, "run-1", "build-1")


class DeviceAppWiringTests(unittest.TestCase):
    """Protect the opt-in suite path and the unchanged ordinary SQL path."""

    def test_app_selects_suite_filter_and_run_id_without_replacing_sql(self):
        """Require all suite selectors while retaining the existing SQL invocation."""
        app = (IOS / "app/main.mm").read_text()
        for name in ("SEEKDB_IOS_TEST_SUITE", "SEEKDB_IOS_TEST_FILTER", "SEEKDB_IOS_TEST_RUN_ID"):
            self.assertIn(name, app)
        self.assertIn("seekdb_ios_probe_sql", app)
        self.assertIn("run_device_suite", app)

    def test_app_target_compiles_registry_and_evidence_sources(self):
        """Link the device-only registry implementation into the signed probe App."""
        cmake = (IOS / "app/CMakeLists.txt").read_text()
        self.assertIn("device_test_registry.cpp", cmake)
        self.assertIn("device_evidence.cpp", cmake)


if __name__ == "__main__":
    unittest.main()
