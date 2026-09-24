#!/usr/bin/env python3
"""Verify deterministic and complete iOS layered-test inventory generation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_test_inventory  # noqa: E402


MANIFEST_PATH = REPO_ROOT / "unittest" / "ios_build" / "ios-test-classification.json"


class TestInventory(unittest.TestCase):
    """Validate discovery, classification, and deterministic serialization."""

    @classmethod
    def setUpClass(cls):
        """Discover and classify the tracked test corpus once for all checks."""
        cls.rows = generate_test_inventory.build_inventory(REPO_ROOT, MANIFEST_PATH)
        cls.by_id = {row["id"]: row for row in cls.rows}

    def test_discovers_current_tracked_corpus(self):
        """Require the exact test-corpus counts established for this revision."""
        counts = generate_test_inventory.count_by_corpus(self.rows)
        self.assertEqual(283, counts["mysqltest-active"])
        self.assertEqual(272, sum(row["ci_selected"] for row in self.rows
                                  if row["corpus"] == "mysqltest-active"))
        self.assertEqual(500, counts["obtest-legacy"])
        self.assertEqual(3, counts["rust-test"])
        self.assertEqual(9, counts["gtest-orphan"])

        tracked_python_tests = subprocess.check_output(
            ["git", "ls-files", "unittest/ios_build/test_*.py"],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
        self.assertEqual(len(tracked_python_tests), counts["host-python"])
        self.assertGreaterEqual(counts["ios-probe"], 5)

    def test_all_rows_are_uniquely_and_validly_classified(self):
        """Reject missing paths, duplicate IDs, and invalid classification values."""
        self.assertEqual(len(self.rows), len(self.by_id))
        allowed_execution = {"device-native", "host-driven-device", "host-only"}
        allowed_applicability = {"required", "excluded", "blocked"}
        tracked = set(generate_test_inventory.git_tracked_files(REPO_ROOT))
        for row in self.rows:
            with self.subTest(row=row["id"]):
                self.assertIn(row["source_path"], tracked)
                self.assertIn(row["execution_class"], allowed_execution)
                self.assertIn(row["applicability"], allowed_applicability)
                self.assertGreater(row["timeout_seconds"], 0)
                self.assertIn(row["memory_class"], {"small", "medium", "large", "unbounded"})
                if row["applicability"] == "excluded":
                    self.assertTrue(row["exclusion_reason"])
                if row["applicability"] == "blocked":
                    self.assertTrue(row["blocked_reason"])

    def test_every_active_mysqltest_has_a_classification(self):
        """Prevent active mysqltest cases from disappearing silently."""
        active = [row for row in self.rows if row["corpus"] == "mysqltest-active"]
        self.assertEqual(283, len(active))
        self.assertTrue(all(row["classification_source"] for row in active))
        self.assertTrue(all(row["id"].startswith("mysqltest.active.") for row in active))

    def test_manifest_records_absent_normal_cpp_target(self):
        """Keep the zero-target boundary explicit instead of implying C++ execution."""
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        facts = manifest["corpus_facts"]
        self.assertEqual(0, facts["normal_cpp_test_target_count"])
        self.assertIn("absent at source revision", facts["normal_cpp_test_target_reason"])

    def test_device_equivalents_exist_and_are_acyclic(self):
        """Require device-equivalent references to resolve without cycles."""
        generate_test_inventory.validate_device_equivalents(self.rows)
        for row in self.rows:
            equivalent = row["device_equivalent"]
            if equivalent is not None:
                self.assertIn(equivalent, self.by_id)

    def test_inventory_serialization_is_byte_deterministic(self):
        """Require two generations from one revision to produce identical bytes."""
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path = Path(first) / "inventory.jsonl"
            second_path = Path(second) / "inventory.jsonl"
            generate_test_inventory.write_inventory(self.rows, first_path)
            rebuilt = generate_test_inventory.build_inventory(REPO_ROOT, MANIFEST_PATH)
            generate_test_inventory.write_inventory(rebuilt, second_path)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

            decoded = [json.loads(line) for line in first_path.read_text().splitlines()]
            self.assertEqual(sorted(row["id"] for row in decoded),
                             [row["id"] for row in decoded])
            self.assertEqual({generate_test_inventory.source_commit(REPO_ROOT)},
                             {row["source_commit"] for row in decoded})
            self.assertEqual(1, len({row["corpus_digest"] for row in decoded}))


if __name__ == "__main__":
    unittest.main()
