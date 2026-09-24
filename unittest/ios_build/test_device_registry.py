#!/usr/bin/env python3
"""Verify the iOS device registry and device-origin evidence contract."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
from types import SimpleNamespace
import unittest
from unittest import mock


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

    def test_case_timeout_emits_terminal_failure_and_bounds_the_process(self):
        """Terminate a process whose callback exceeds its declared per-case deadline."""
        source = textwrap.dedent(r'''
            #include "device_test_registry.h"
            #include <chrono>
            #include <thread>
            using namespace seekdb::ios_test;
            int block(TestContext &) {
              std::this_thread::sleep_for(std::chrono::seconds(5));
              return 0;
            }
            int main(int, char **argv) {
              DeviceTestRegistry registry;
              registry.add({"smoke.block", "smoke", 1, block});
              return run_device_suite(registry, "smoke", "smoke.*", "run-timeout", "build-1", argv[1]);
            }
        ''')
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            evidence = temp_path / "evidence.jsonl"
            binary = self._compile_harness(source, temp_path)
            started = time.monotonic()
            result = subprocess.run([str(binary), str(evidence)], check=False, capture_output=True,
                                    text=True, timeout=3)
            elapsed = time.monotonic() - started
            records = [json.loads(line) for line in evidence.read_text().splitlines()]
        self.assertEqual(124, result.returncode)
        self.assertLess(elapsed, 4.5)
        self.assertEqual(
            ["run_start", "case_start", "assertion", "case_end", "run_complete"],
            [record["event"] for record in records],
        )
        self.assertFalse(records[2]["passed"])
        self.assertEqual(124, records[3]["result"])
        self.assertEqual(124, records[4]["result"])

    def test_json_writer_replaces_malformed_utf8_without_losing_valid_text(self):
        """Keep valid UTF-8 while deterministically replacing every malformed input byte."""
        source = textwrap.dedent(r'''
            #include "device_evidence.h"
            #include <string>
            using namespace seekdb::ios_test;
            int main(int, char **argv) {
              DeviceEvidenceWriter evidence(argv[1], "run-1", "build-1");
              const std::string malformed("\xf0\x28\x8c\x28", 4);
              return evidence.append({
                  {"event", DeviceEvidenceWriter::json_string("diagnostic")},
                  {"valid", DeviceEvidenceWriter::json_string(u8"你好 \"line\"\n")},
                  {"malformed", DeviceEvidenceWriter::json_string(malformed)},
              }) ? 0 : 1;
            }
        ''')
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "evidence.jsonl"
            self._compile_and_run(source, [str(evidence)])
            record = json.loads(evidence.read_text())
        self.assertEqual('你好 "line"\n', record["valid"])
        self.assertEqual("\ufffd(\ufffd(", record["malformed"])

    def _compile_and_run(self, source, arguments=()):
        """Compile and execute a temporary C++ contract harness."""
        return self._compile_and_run_result(source, arguments).stdout

    def _compile_and_run_result(self, source, arguments=(), check=True, timeout=None):
        """Compile a temporary C++ harness and return its completed process."""
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            binary = self._compile_harness(source, temp_path)
            return subprocess.run([str(binary), *arguments], check=check, capture_output=True,
                                  text=True, timeout=timeout)

    def _compile_harness(self, source, temp_path):
        """Compile one portable registry harness into an existing temporary directory."""
        harness = temp_path / "harness.cpp"
        binary = temp_path / "harness"
        harness.write_text(source)
        subprocess.run(
            ["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(IOS),
             str(harness), str(IOS / "device_test_registry.cpp"),
             str(IOS / "device_evidence.cpp"), "-o", str(binary)],
            check=True, capture_output=True, text=True,
        )
        return binary


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

    def test_sql_restart_gate_requires_36_steps_and_persistence_increment(self):
        """Accept only complete SQL evidence and exact same-directory 0-to-1 state."""
        records = [
            {"step": step, "case": f"case-{step}", "result": 0}
            for step in range(1, 37)
        ] + [{"complete": True, "result": 0}]
        self.runner.validate_sql_records(records)
        status = {
            "run_id": "run-1", "build_id": "build-1",
            "state": "Stopped", "result": 0, "suite_result": None,
            "cleanup_status": 7, "cleanup_error": 0,
            "working_directory_restored": True,
            "data_name": "shared-data", "sql_verified": True,
            "sql_result": 0, "previous_runs": 1, "hook_mode": "disabled",
        }
        self.runner.validate_sql_terminal_status(
            status, "run-1", "build-1", "shared-data", 1)
        with self.assertRaises(ValueError):
            self.runner.validate_sql_records(records[:-1])
        with self.assertRaises(ValueError):
            self.runner.validate_sql_terminal_status(
                {**status, "previous_runs": 0},
                "run-1", "build-1", "shared-data", 1)

    def test_waiters_honor_one_caller_owned_absolute_deadline(self):
        """Do not reset the timeout between evidence and cleanup waits."""
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "evidence.jsonl"
            with mock.patch.object(
                    self.runner.time, "monotonic", return_value=101), \
                    mock.patch.object(
                        self.runner, "copy_evidence") as copy_evidence:
                with self.assertRaises(TimeoutError):
                    self.runner.wait_for_evidence(
                        "device", "bundle", "source", destination, 999,
                        "run-1", "build-1", ["smoke.pass"],
                        "smoke", "smoke.*", deadline=100)
            with mock.patch.object(
                    self.runner.time, "monotonic", return_value=101), \
                    mock.patch.object(
                        self.runner, "copy_probe_status") as copy_status:
                with self.assertRaises(TimeoutError):
                    self.runner.wait_for_terminal_status(
                        "device", "bundle", destination, 999,
                        "run-1", "build-1", deadline=100)
        copy_evidence.assert_not_called()
        copy_status.assert_not_called()

    def test_sql_restart_retry_skips_a_durable_completed_first_round(self):
        """Resume the second round without repeating a passed first SQL round."""
        records = [
            {"step": step, "case": f"case-{step}", "result": 0}
            for step in range(1, 37)
        ] + [{"complete": True, "result": 0}]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "evidence-gate-first.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records))
            options = SimpleNamespace(
                output_dir=output, evidence_prefix="gate",
                device="device", bundle_id="bundle", data_name="shared",
                expected_hook_mode="enabled", runner_run_id="runner-1")
            evidence = output / "evidence-gate-first.jsonl"
            self.runner.write_sql_evidence_metadata(
                evidence, options, "build-1", 0)
            with mock.patch.object(
                    self.runner, "devicectl",
                    return_value=SimpleNamespace(returncode=0)) as devicectl, \
                    mock.patch.object(
                        self.runner, "wait_for_sql_round") as wait_round:
                summary = self.runner.run_sql_restart(
                    options, "build-1", 100)

        self.assertEqual(0, summary["run_result"])
        self.assertEqual(1, devicectl.call_count)
        self.assertEqual(1, wait_round.call_count)
        self.assertEqual(1, wait_round.call_args.args[-2])
        self.assertEqual("enabled", wait_round.call_args.args[-1])

    def test_sql_restart_new_run_never_reuses_old_fixed_evidence(self):
        """A same-day restart must launch even when an old gate file remains."""
        records = [
            {"step": step, "case": f"case-{step}", "result": 0}
            for step in range(1, 37)
        ] + [{"complete": True, "result": 0}]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            evidence = output / "evidence-gate-first.jsonl"
            evidence.write_text(
                "".join(json.dumps(record) + "\n" for record in records))
            old_options = SimpleNamespace(
                output_dir=output, evidence_prefix="gate",
                device="device", bundle_id="bundle", data_name="shared",
                expected_hook_mode="enabled", runner_run_id="old-run")
            self.runner.write_sql_evidence_metadata(
                evidence, old_options, "build-1", 0)
            new_options = SimpleNamespace(
                **{**vars(old_options), "runner_run_id": "new-run"})
            with mock.patch.object(
                    self.runner, "devicectl",
                    return_value=SimpleNamespace(returncode=0)) as devicectl, \
                    mock.patch.object(
                        self.runner, "wait_for_sql_round"):
                self.runner.run_sql_restart(new_options, "build-1", 100)

        self.assertEqual(2, devicectl.call_count)

    def test_complete_invalid_evidence_fails_immediately_while_incomplete_retries(self):
        """Retry only an unfinished prefix and preserve a terminal validation failure."""
        invalid = self.valid_records()
        invalid[-2]["result"] = 9
        invalid[-1]["result"] = 9
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "evidence.jsonl"

            def copy_invalid(*_):
                destination.write_text("".join(json.dumps(record) + "\n" for record in invalid))
                return True

            with mock.patch.object(self.runner, "copy_evidence", side_effect=copy_invalid) as copied:
                with self.assertRaisesRegex(ValueError, "nonzero result"):
                    self.runner.wait_for_evidence(
                        "device", "bundle", "source", destination, 1,
                        "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")
            self.assertEqual(1, copied.call_count)

        incomplete = self.valid_records()[:-1]
        complete = self.valid_records()
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "evidence.jsonl"
            attempts = iter((incomplete, complete))

            def copy_next(*_):
                records = next(attempts)
                destination.write_text("".join(json.dumps(record) + "\n" for record in records))
                return True

            with mock.patch.object(self.runner, "copy_evidence", side_effect=copy_next) as copied, \
                    mock.patch.object(self.runner.time, "sleep", return_value=None):
                summary = self.runner.wait_for_evidence(
                    "device", "bundle", "source", destination, 2,
                    "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")
            self.assertEqual(2, copied.call_count)
            self.assertEqual(0, summary["run_result"])

    def test_failed_assertion_prefix_waits_for_terminal_failure(self):
        """Keep polling after a flushed failed assertion until run completion is visible."""
        incomplete = self.valid_records()[:-2]
        incomplete[2]["passed"] = False
        complete = self.valid_records()
        complete[2]["passed"] = False
        complete[-2]["result"] = 7
        complete[-1]["result"] = 7
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "evidence.jsonl"
            attempts = iter((incomplete, complete))

            def copy_next(*_):
                records = next(attempts)
                destination.write_text("".join(json.dumps(record) + "\n" for record in records))
                return True

            with mock.patch.object(self.runner, "copy_evidence", side_effect=copy_next) as copied, \
                    mock.patch.object(self.runner.time, "sleep", return_value=None):
                with self.assertRaisesRegex(ValueError, "assertion failed"):
                    self.runner.wait_for_evidence(
                        "device", "bundle", "source", destination, 2,
                        "run-1", "build-1", ["smoke.pass"], "smoke", "smoke.*")
            self.assertEqual(2, copied.call_count)

    def test_jsonl_reader_retries_only_an_unterminated_last_record(self):
        """Treat newline-terminated malformed JSON as invalid rather than incomplete."""
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "evidence.jsonl"
            evidence.write_text('{"event":')
            with self.assertRaises(self.runner.IncompleteEvidenceError):
                self.runner.read_jsonl(evidence)
            evidence.write_text('{"event":]\n')
            with self.assertRaises(ValueError) as raised:
                self.runner.read_jsonl(evidence)
            self.assertNotIsInstance(raised.exception, self.runner.IncompleteEvidenceError)


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
