#!/usr/bin/env python3
"""Verify deterministic and complete iOS layered-test inventory generation."""

import copy
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
        cls.rows = generate_test_inventory.build_inventory(
            REPO_ROOT, MANIFEST_PATH, allow_dirty=True
        )
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
        """Require an explicit reviewed manifest decision for every active case."""
        active = [row for row in self.rows if row["corpus"] == "mysqltest-active"]
        self.assertEqual(283, len(active))
        self.assertTrue(all(row["classification_source"].startswith("reviewed:")
                            for row in active))
        self.assertTrue(all(row["id"].startswith("mysqltest.active.") for row in active))

    def test_reviewed_and_materialized_manifest_ids_are_complete(self):
        """Require exact reviewed decisions for active/GTest and materialized legacy IDs."""
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        reviewed = manifest["reviewed_decisions"]
        expected_reviewed = {
            row["id"] for row in self.rows
            if row["corpus"] in {"mysqltest-active", "gtest-orphan"}
        }
        self.assertEqual(expected_reviewed, set(reviewed))
        self.assertTrue(all(
            reviewed[row["id"]] == row["corpus"]
            for row in self.rows
            if row["id"] in reviewed
        ))
        self.assertEqual(
            {row["id"] for row in self.rows if row["corpus"] == "obtest-legacy"},
            set(manifest["materialized_ids"]["obtest-legacy"]),
        )

    def test_normal_cpp_target_count_is_derived_from_tracked_build_files(self):
        """Derive the zero normal C++ target count from tracked source definitions."""
        targets = generate_test_inventory.discover_normal_cpp_test_targets(
            REPO_ROOT, generate_test_inventory.git_tracked_files(REPO_ROOT)
        )
        self.assertEqual([], targets)

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
            rebuilt = generate_test_inventory.build_inventory(
                REPO_ROOT, MANIFEST_PATH, allow_dirty=True
            )
            generate_test_inventory.write_inventory(rebuilt, second_path)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

            decoded = [json.loads(line) for line in first_path.read_text().splitlines()]
            self.assertEqual(sorted(row["id"] for row in decoded),
                             [row["id"] for row in decoded])
            self.assertEqual({generate_test_inventory.source_commit(REPO_ROOT)},
                             {row["source_commit"] for row in decoded})
            self.assertEqual(1, len({row["corpus_digest"] for row in decoded}))

    def test_schema_rejects_wrong_types_enums_and_cross_field_reasons(self):
        """Reject malformed values for every typed or cross-field schema boundary."""
        valid = self.rows[0]
        mutations = (
            ("id", ""),
            ("source_path", "/absolute/test.py"),
            ("corpus", 7),
            ("case_name", ""),
            ("ci_selected", 1),
            ("owning_module", ""),
            ("framework", None),
            ("execution_class", "simulator-only"),
            ("device_equivalent", 7),
            ("device_equivalent", ""),
            ("requirements", []),
            ("requirements", "host"),
            ("requirements", [""]),
            ("requirements", [7]),
            ("timeout_seconds", True),
            ("timeout_seconds", 0),
            ("memory_class", "huge"),
            ("applicability", "conditional"),
            ("latest_result", "unknown"),
            ("latest_result", None),
            ("exclusion_reason", ""),
            ("blocked_reason", ""),
            ("evidence_path", ""),
            ("evidence_path", 7),
            ("evidence_path", "/tmp/evidence.json"),
            ("classification_source", ""),
            ("source_commit", "not-a-commit"),
            ("corpus_digest", "not-a-digest"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                row = copy.deepcopy(valid)
                row[field] = value
                with self.assertRaises(generate_test_inventory.InventoryError):
                    generate_test_inventory.validate_inventory_row(row)

        for field_change in ("missing", "extra"):
            with self.subTest(field_change=field_change):
                row = copy.deepcopy(valid)
                if field_change == "missing":
                    del row["evidence_path"]
                else:
                    row["unexpected"] = "value"
                with self.assertRaises(generate_test_inventory.InventoryError):
                    generate_test_inventory.validate_inventory_row(row)

        for applicability, exclusion_reason, blocked_reason in (
            ("required", "unexpected", None),
            ("required", None, "unexpected"),
            ("excluded", None, None),
            ("excluded", "reason", "unexpected"),
            ("blocked", None, None),
            ("blocked", "unexpected", "reason"),
        ):
            with self.subTest(applicability=applicability):
                row = copy.deepcopy(valid)
                row.update(
                    applicability=applicability,
                    exclusion_reason=exclusion_reason,
                    blocked_reason=blocked_reason,
                )
                with self.assertRaises(generate_test_inventory.InventoryError):
                    generate_test_inventory.validate_inventory_row(row)

    def test_head_binding_covers_manifest_generator_and_selection_inputs(self):
        """Keep every tracked discovery/configuration input in the HEAD clean gate."""
        relevant = set(generate_test_inventory.relevant_inventory_inputs(
            generate_test_inventory.git_tracked_files(REPO_ROOT)
        ))
        self.assertTrue({
            ".github/script/seekdb/mysqltest_for_seekdb.py",
            "tools/deploy/mysqltest_config.yaml",
            "unittest/ios_build/generate_test_inventory.py",
            "unittest/ios_build/ios-test-classification.json",
        }.issubset(relevant))

    def test_dirty_relevant_inputs_and_untracked_mysqltests_are_rejected(self):
        """Bind strict generation to HEAD and reject filesystem-only mysqltests."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            tracked = root / "tools/deploy/mysql_test/t/tracked.test"
            tracked.parent.mkdir(parents=True)
            tracked.write_text("select 1;\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Inventory Test", "-c",
                 "user.email=inventory@example.invalid", "commit", "-qm", "fixture"],
                cwd=root,
                check=True,
            )
            tracked.write_text("select 2;\n", encoding="utf-8")
            with self.assertRaises(generate_test_inventory.InventoryError):
                generate_test_inventory.assert_relevant_inputs_clean(
                    root, [tracked.relative_to(root).as_posix()]
                )

            tracked.write_text("select 1;\n", encoding="utf-8")
            extra = root / "tools/deploy/mysql_test/test_suite/demo/t/extra.test"
            extra.parent.mkdir(parents=True)
            extra.write_text("select 3;\n", encoding="utf-8")
            with self.assertRaises(generate_test_inventory.InventoryError):
                generate_test_inventory.reject_untracked_mysqltests(root)
            extra.unlink()

            result = root / "tools/deploy/mysql_test/r/mysql/tracked.result"
            result.parent.mkdir(parents=True)
            result.write_text("1\n", encoding="utf-8")
            with self.assertRaises(generate_test_inventory.InventoryError):
                generate_test_inventory.reject_untracked_mysqltests(root)

    def test_documented_focused_command_is_executable(self):
        """Keep the plan on the valid direct focused-test command."""
        plan_path = (
            REPO_ROOT / "docs" / "superpowers" / "plans"
            / "2026-09-24-ios-complete-layered-validation.md"
        )
        plan = plan_path.read_text(encoding="utf-8")
        self.assertIn("python3 unittest/ios_build/test_inventory.py -v", plan)
        self.assertNotIn("python3 -m unittest unittest.ios_build.test_inventory -v", plan)


if __name__ == "__main__":
    unittest.main()
