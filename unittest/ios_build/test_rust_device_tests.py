#!/usr/bin/env python3
"""Verify the test-only Rust device ABI, adapter, and archive selection."""

import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
RUST_WORKSPACE = ROOT / "rust" / "Cargo.toml"
RUST_CRATE = ROOT / "rust" / "sql-nio"
DEVICE_SOURCE = RUST_CRATE / "src" / "device_tests.rs"
ADAPTER = ROOT / "unittest" / "ios_build" / "rust_device_tests.cpp"
BUILD_APP_SPEC = importlib.util.spec_from_file_location(
    "task4_build_app", ROOT / "deps" / "ios-build" / "build_app.py"
)
BUILD_APP = importlib.util.module_from_spec(BUILD_APP_SPEC)
BUILD_APP_SPEC.loader.exec_module(BUILD_APP)

REAL_CASE_IDS = {
    "ios.rust.cert.rejects_truncated_certificate",
    "ios.rust.cert.formats_display_name_for_sql_account",
    "ios.rust.tls.exposes_sql_cipher_names",
}
CONTROL_CASE_IDS = {
    "ios.rust.device.intentional_panic",
    "ios.rust.device.panic_continuation",
}


class TestRustDeviceTests(unittest.TestCase):
    """Keep Rust runtime assertions shared and test symbols out of production."""

    def test_shared_result_cases_back_host_tests(self):
        """Require three Result cases and host wrappers instead of duplicate assertions."""
        source = DEVICE_SOURCE.read_text(encoding="utf-8")
        self.assertEqual(3, len(re.findall(r"pub\(crate\) fn [a-z_]+\(\) -> Result<\(\), String>", source)))
        for case in (
            "rejects_truncated_certificate",
            "formats_display_name_for_sql_account",
            "exposes_sql_cipher_names",
        ):
            self.assertRegex(source, rf"(?s)fn test_{case}\(\).*?{case}\(\)\.unwrap\(\)")
        self.assertNotIn("#[test]", (RUST_CRATE / "src" / "cert.rs").read_text())
        self.assertNotIn("#[test]", (RUST_CRATE / "src" / "tls.rs").read_text())

    def test_feature_profile_and_fixed_abi_are_test_only(self):
        """Require unwind only for the named device profile and cfg-gated exports."""
        workspace = RUST_WORKSPACE.read_text(encoding="utf-8")
        crate = (RUST_CRATE / "Cargo.toml").read_text(encoding="utf-8")
        lib = (RUST_CRATE / "src" / "lib.rs").read_text(encoding="utf-8")
        header = (RUST_CRATE / "include" / "nio.h").read_text(encoding="utf-8")
        self.assertRegex(workspace, r"\[profile\.ios-device-test\][\s\S]*panic\s*=\s*\"unwind\"")
        self.assertRegex(crate, r"\[features\][\s\S]*ios-device-tests\s*=\s*\[\]")
        self.assertIn('feature = "ios-device-tests"', lib)
        self.assertIn("mod device_tests;", lib)
        self.assertIn("SQL_NIO_IOS_DEVICE_TESTS", header)
        self.assertIn("nio_device_test_count", header)
        self.assertIn("nio_device_test_case_info", header)
        self.assertIn("nio_device_test_run", header)

    def test_abi_checks_index_capacity_diagnostics_and_panics(self):
        """Require bounded fixed-width output and containment at the outer FFI boundary."""
        source = DEVICE_SOURCE.read_text(encoding="utf-8")
        for required in (
            "NIO_DEVICE_TEST_INVALID_INDEX",
            "NIO_DEVICE_TEST_INVALID_CAPACITY",
            "NIO_DEVICE_TEST_PANIC",
            "catch_unwind",
            "AssertUnwindSafe",
            "diagnostic",
            "intentional device-test panic",
            "panic_contained",
        ):
            self.assertIn(required, source)
        self.assertRegex(source, r"pub diagnostic: \[u8; (?:[1-9][0-9]*|NIO_DEVICE_TEST_DIAGNOSTIC_CAPACITY)\]")

    def test_cpp_adapter_registers_all_real_and_control_cases(self):
        """Require each Rust case to retain independent device evidence."""
        source = ADAPTER.read_text(encoding="utf-8")
        registered = set(re.findall(r'\{"(ios\.rust\.[^"]+)",\s*"rust"', source))
        self.assertEqual(REAL_CASE_IDS | CONTROL_CASE_IDS, registered)
        self.assertIn("nio_device_test_case_info", source)
        self.assertIn("nio_device_test_run", source)
        self.assertIn("make_rust_device_registry", source)

    def test_app_and_cmake_select_exactly_one_rust_archive_mode(self):
        """Prevent a test App from linking production and test Rust archives together."""
        rust_cmake = (ROOT / "cmake" / "Rust.cmake").read_text(encoding="utf-8")
        observer = (ROOT / "src" / "observer" / "CMakeLists.txt").read_text(encoding="utf-8")
        app_cmake = (ROOT / "unittest" / "ios_build" / "app" / "CMakeLists.txt").read_text(encoding="utf-8")
        build_app = (ROOT / "deps" / "ios-build" / "build_app.py").read_text(encoding="utf-8")
        self.assertIn("ios-device-test", rust_cmake)
        self.assertIn("--features", rust_cmake)
        self.assertIn("ios-device-tests", rust_cmake)
        self.assertIn("SEEKDB_IOS_TEST_HOOKS", rust_cmake)
        self.assertIn("SQL_NIO_IOS_DEVICE_TESTS", observer)
        self.assertIn("../rust_device_tests.cpp", app_cmake)
        self.assertIn("require_rust_archive_mode", build_app)
        self.assertIn("nio_device_test_count", build_app)

    def test_cmake_clean_does_not_own_tracked_rust_header(self):
        """Keep CMake clean from deleting the tracked cbindgen header."""
        rust_cmake = (ROOT / "cmake" / "Rust.cmake").read_text(encoding="utf-8")
        self.assertNotRegex(
            rust_cmake,
            r'BYPRODUCTS\s+"\$\{RUST_INCLUDE_DIR\}/nio\.h"',
        )

    def test_archive_marker_rejects_mixed_or_wrong_modes(self):
        """Validate the runtime marker rather than trusting only CMake cache state."""
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "libsql_nio.a"
            archive.touch()
            with mock.patch.object(BUILD_APP.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout="_nio_device_test_count\n")
                self.assertEqual(archive, BUILD_APP.require_rust_archive_mode([str(archive)], True))
                with self.assertRaises(ValueError):
                    BUILD_APP.require_rust_archive_mode([str(archive)], False)
                with self.assertRaises(ValueError):
                    BUILD_APP.require_rust_archive_mode([str(archive), str(archive)], True)
            with mock.patch.object(BUILD_APP.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout="_nio_start\n")
                self.assertEqual(archive, BUILD_APP.require_rust_archive_mode([str(archive)], False))
                with self.assertRaises(ValueError):
                    BUILD_APP.require_rust_archive_mode([str(archive)], True)


if __name__ == "__main__":
    unittest.main()
