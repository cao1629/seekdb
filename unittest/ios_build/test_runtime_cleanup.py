"""Protect iOS runtime cleanup behavior and its device evidence contract."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "unittest/ios_build/run_device_cleanup_test.py"


class RuntimeCleanupContractTests(unittest.TestCase):
    """Require observable cleanup for every process-owned iOS resource."""

    def test_runtime_exposes_cleanup_evidence(self):
        """Expose server, curl, and working-directory cleanup to the probe."""
        header = (ROOT / "src/observer/ios/seekdb_ios.h").read_text()
        source = (ROOT / "src/observer/ios/seekdb_ios.cpp").read_text()
        app = (ROOT / "unittest/ios_build/app/main.mm").read_text()
        self.assertIn("SEEKDB_IOS_CLEANUP_SERVER", header)
        self.assertIn("SEEKDB_IOS_CLEANUP_CURL", header)
        self.assertIn("SEEKDB_IOS_CLEANUP_WORKING_DIRECTORY", header)
        self.assertIn("seekdb_ios_get_cleanup_status", header)
        self.assertIn("seekdb_ios_get_cleanup_error", header)
        self.assertIn("seekdb_ios_get_build_id", header)
        self.assertIn("SEEKDB_IOS_ARTIFACT_BUILD_ID", source)
        self.assertIn("curl_initialized", source)
        self.assertIn('@"cleanup_status"', app)
        self.assertIn('@"cleanup_error"', app)
        self.assertIn('@"working_directory_restored"', app)
        self.assertIn('@"build_id"', app)
        self.assertIn('@"hook_mode"', app)

    def test_failure_hook_is_test_only(self):
        """Compile deterministic startup failure only when explicitly enabled."""
        cmake = (ROOT / "src/observer/CMakeLists.txt").read_text()
        source = (ROOT / "src/observer/ob_server.cpp").read_text()
        self.assertIn("SEEKDB_IOS_TEST_HOOKS", cmake)
        self.assertIn("#ifdef SEEKDB_IOS_TEST_HOOKS", source)
        server = (ROOT / "src/observer/ob_server.cpp").read_text()
        self.assertIn("SEEKDB_IOS_TEST_FAIL_DURING_INIT", server)
        self.assertIn("if (!in_process_)", server)

    def test_server_cleanup_is_only_reported_after_success(self):
        """Do not report completed server cleanup when the stop path failed."""
        source = (ROOT / "src/observer/ios/seekdb_ios.cpp").read_text()
        success = source.index("if (OB_SUCCESS == cleanup_ret)")
        status = source.index("cleanup_status.fetch_or(SEEKDB_IOS_CLEANUP_SERVER)")
        self.assertLess(success, status)


class DeviceCleanupEvidenceTests(unittest.TestCase):
    """Validate host-driven interpretation of device cleanup evidence."""

    def setUp(self):
        """Load the device runner only after asserting that it exists."""
        self.assertTrue(RUNNER.is_file(), "device cleanup runner is missing")
        spec = importlib.util.spec_from_file_location("run_device_cleanup_test", RUNNER)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)

    def test_accepts_complete_failed_startup_cleanup(self):
        """Accept a nonzero failed run only when every cleanup action completed."""
        status = {"state": "Failed", "result": -4016, "cleanup_error": 0,
                  "cleanup_status": 7, "working_directory_restored": True,
                  "run_id": "current-run", "build_id": "0123456789ab",
                  "hook_mode": "enabled"}
        self.runner.validate_status(status, "current-run", "0123456789ab")

    def test_rejects_incomplete_or_successful_evidence(self):
        """Reject missing cleanup actions, incomplete state, and zero results."""
        invalid = [
            {"state": "Running", "result": None, "cleanup_error": 0,
             "cleanup_status": 0, "working_directory_restored": False, "run_id": "current-run"},
            {"state": "Failed", "result": 0, "cleanup_error": 0,
             "cleanup_status": 7, "working_directory_restored": True, "run_id": "current-run"},
            {"state": "Failed", "result": -4016, "cleanup_error": -1,
             "cleanup_status": 7, "working_directory_restored": True, "run_id": "current-run"},
            {"state": "Failed", "result": -4016, "cleanup_error": 0,
             "cleanup_status": 6, "working_directory_restored": True, "run_id": "current-run"},
            {"state": "Failed", "result": -4016, "cleanup_error": 0,
             "cleanup_status": 7, "working_directory_restored": False, "run_id": "current-run"},
            {"state": "Failed", "result": -4016, "cleanup_error": 0,
             "cleanup_status": 7, "working_directory_restored": True, "run_id": "stale-run"},
        ]
        for status in invalid:
            status.setdefault("build_id", "0123456789ab")
            status.setdefault("hook_mode", "enabled")
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.runner.validate_status(status, "current-run", "0123456789ab")

    def test_rejects_stale_build_or_hookless_evidence(self):
        """Bind cleanup assertions to the linked runtime artifact identity."""
        base = {"state": "Failed", "result": -4016, "cleanup_error": 0,
                "cleanup_status": 7, "working_directory_restored": True,
                "run_id": "current-run", "build_id": "0123456789ab",
                "hook_mode": "enabled"}
        for changes in ({"build_id": "aaaaaaaaaaaa"}, {"hook_mode": "disabled"}):
            status = {**base, **changes}
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.runner.validate_status(status, "current-run", "0123456789ab")

    def test_runner_does_not_persist_raw_device_metadata(self):
        """Keep device identifiers out of the repository-local evidence directory."""
        source = RUNNER.read_text()
        self.assertNotIn('"launch.json"', source)
        self.assertNotIn("output=launch_output", source)


if __name__ == "__main__":
    unittest.main()
