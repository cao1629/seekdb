"""Protect iOS runtime cleanup behavior and its device evidence contract."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


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
        self.assertIn("curl_initialized", source)
        self.assertIn('@"cleanup_status"', app)
        self.assertIn('@"working_directory_restored"', app)

    def test_failure_hook_is_test_only(self):
        """Compile deterministic startup failure only when explicitly enabled."""
        cmake = (ROOT / "src/observer/CMakeLists.txt").read_text()
        source = (ROOT / "src/observer/ios/seekdb_ios.cpp").read_text()
        self.assertIn("SEEKDB_IOS_TEST_HOOKS", cmake)
        self.assertIn("#ifdef SEEKDB_IOS_TEST_HOOKS", source)
        self.assertIn("SEEKDB_IOS_TEST_FAIL_AFTER_INIT", source)


if __name__ == "__main__":
    unittest.main()
