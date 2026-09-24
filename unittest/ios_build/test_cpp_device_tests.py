#!/usr/bin/env python3
"""Verify exact iOS mappings for the orphan C++ GoogleTest registrations."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
IOS_BUILD = ROOT / "unittest" / "ios_build"
CPP_SOURCE = IOS_BUILD / "cpp_device_tests.cpp"
MANIFEST = IOS_BUILD / "ios-test-classification.json"

DEVICE_CASE_IDS = {
    "ios.cpp.allocator.backend",
    "ios.cpp.allocator.lifecycle",
    "ios.cpp.allocator.realloc_alignment",
    "ios.cpp.ob_error.mapping",
}

GTEST_IDS = {
    "cpp.gtest.TestJemallocHook.CrossApiAllocationDomain",
    "cpp.gtest.TestJemallocHook.ReallocAndAlignment",
    "cpp.gtest.TestMallocBackend.detect_once",
    "cpp.gtest.TestMallocBackend.direct_jemalloc",
    "cpp.gtest.TestMallocBackend.parse",
    "cpp.gtest.TestMallocBackend.restore_after_fork",
    "cpp.gtest.TestObError.test_adder",
    "cpp.gtest.TestObError.test_mgr",
    "cpp.gtest.TestObError.test_parser",
}

EXCLUDED_GTEST_IDS = {
    "cpp.gtest.TestJemallocHook.CrossApiAllocationDomain",
    "cpp.gtest.TestJemallocHook.ReallocAndAlignment",
    "cpp.gtest.TestMallocBackend.restore_after_fork",
    "cpp.gtest.TestObError.test_adder",
    "cpp.gtest.TestObError.test_mgr",
    "cpp.gtest.TestObError.test_parser",
}

EVIDENCE_PATH = "build_ios_arm64/device-evidence/task3-cpp/final/summary.json"


class TestCppDeviceTests(unittest.TestCase):
    """Keep device behavior, exact exclusions, and App wiring reviewable."""

    @classmethod
    def setUpClass(cls):
        """Load tracked source and classification material once."""
        cls.source = CPP_SOURCE.read_text(encoding="utf-8")
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_registers_stable_individual_device_cases(self):
        """Require every compiled behavior group to retain an individual case ID."""
        registered = set(re.findall(r'\{"(ios\.cpp\.[^"]+)",\s*"cpp"', self.source))
        self.assertEqual(DEVICE_CASE_IDS, registered)
        self.assertIn("make_cpp_device_registry", self.source)

    def test_every_orphan_gtest_has_an_equivalent_or_exact_exclusion(self):
        """Require all nine source registrations to be resolved per ID."""
        overrides = self.manifest["overrides"]
        self.assertTrue(GTEST_IDS.issubset(overrides))
        for test_id in sorted(GTEST_IDS):
            with self.subTest(test_id=test_id):
                override = overrides[test_id]
                if test_id in EXCLUDED_GTEST_IDS:
                    self.assertEqual("excluded", override["applicability"])
                    self.assertIsNone(override["device_equivalent"])
                    self.assertTrue(override["exclusion_reason"])
                else:
                    self.assertEqual("required", override["applicability"])
                    self.assertIn(override["device_equivalent"], DEVICE_CASE_IDS)
                    self.assertIsNone(override["exclusion_reason"])

    def test_exclusions_name_the_unavailable_platform_boundary(self):
        """Reject vague exclusions that could conceal unexecuted behavior."""
        reasons = {
            test_id: self.manifest["overrides"][test_id]["exclusion_reason"]
            for test_id in EXCLUDED_GTEST_IDS
        }
        self.assertRegex(reasons["cpp.gtest.TestJemallocHook.CrossApiAllocationDomain"],
                         r"Linux <malloc\.h>.*hook replacement")
        self.assertRegex(reasons["cpp.gtest.TestJemallocHook.ReallocAndAlignment"],
                         r"libc malloc/realloc/memalign.*malloc-zone hook")
        self.assertRegex(reasons["cpp.gtest.TestMallocBackend.restore_after_fork"],
                         r"fork\(\).*waitpid.*jemalloc background-thread")
        self.assertRegex(reasons["cpp.gtest.TestObError.test_adder"],
                         r"GTEST_SKIP.*Apple.*ObErrorInfoMgr")
        self.assertRegex(reasons["cpp.gtest.TestObError.test_mgr"],
                         r"ob_error CLI-only ObErrorInfoMgr")
        self.assertRegex(reasons["cpp.gtest.TestObError.test_parser"],
                         r"ob_error CLI argument parser")

    def test_device_source_uses_only_ios_available_production_behavior(self):
        """Keep the device suite on real allocator and error-map APIs."""
        for required in (
            "ob_malloc(", "ob_realloc(", "ob_malloc_usable_size(", "ob_free(",
            "parse_ob_malloc_backend(", "get_ob_malloc_backend(",
            "ob_mysql_errno(", "ob_sqlstate(", "ob_error_name(",
        ):
            self.assertIn(required, self.source)
        for forbidden in ("fork(", "waitpid(", "<malloc.h>", "je_mallctl(",
                          "configure_darwin_malloc_zone("):
            self.assertNotIn(forbidden, self.source)
        self.assertEqual(2, self.source.count("verify_allocator_reallocation(context);"))
        for unsupported in ("system", "glibc", "mimalloc", "other", "invalid"):
            self.assertIn(f'parse_ob_malloc_backend("{unsupported}")', self.source)
        self.assertIn("!is_jemalloc_backend(OB_MALLOC_BACKEND_OBMALLOC)", self.source)
        self.assertIn('context.assert_true("selected_backend", is_jemalloc_backend()', self.source)
        self.assertIn("jemalloc_memalign(DEVICE_ALIGNMENT, ALLOCATION_SIZE)", self.source)
        self.assertIn('#include "share/mysql_errno.h"', self.source)
        self.assertIn(
            'context.assert_equal("mysql_errno", ER_WRONG_ARGUMENTS, '
            'ob_mysql_errno(OB_INVALID_ARGUMENT)', self.source)

    def test_direct_jemalloc_maps_to_complete_backend_and_alignment_case(self):
        """Require the direct jemalloc equivalent to include backend selection and memalign."""
        override = self.manifest["overrides"]["cpp.gtest.TestMallocBackend.direct_jemalloc"]
        self.assertEqual("ios.cpp.allocator.realloc_alignment", override["device_equivalent"])

    def test_device_and_mapped_rows_record_current_evidence(self):
        """Require current pass metadata for device cases and their required GTest mappings."""
        overrides = self.manifest["overrides"]
        required_gtests = GTEST_IDS - EXCLUDED_GTEST_IDS
        for test_id in sorted(DEVICE_CASE_IDS | required_gtests):
            with self.subTest(test_id=test_id):
                self.assertEqual("pass", overrides[test_id]["latest_result"])
                self.assertEqual(EVIDENCE_PATH, overrides[test_id]["evidence_path"])

    def test_app_build_and_registry_include_cpp_cases(self):
        """Compile the source and merge its registry into explicit suite selection."""
        cmake = (IOS_BUILD / "app" / "CMakeLists.txt").read_text(encoding="utf-8")
        main = (IOS_BUILD / "app" / "main.mm").read_text(encoding="utf-8")
        header = (IOS_BUILD / "device_test_registry.h").read_text(encoding="utf-8")
        self.assertIn("../cpp_device_tests.cpp", cmake)
        self.assertIn("../../../src", cmake)
        self.assertIn("make_cpp_device_registry", header)
        self.assertIn("make_device_registry", main)

    def test_manifest_states_zero_normal_cpp_target_boundary(self):
        """Keep absence of a normal target distinct from device equivalents."""
        reason = self.manifest["classifications"]["gtest-orphan"]["blocked_reason"]
        self.assertIn("normal C++ target count is zero", reason)
        self.assertIn("build definitions are absent", reason)


if __name__ == "__main__":
    unittest.main()
