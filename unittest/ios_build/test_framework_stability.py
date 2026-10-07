"""Exercise stability evidence gates without substituting host tests for device execution."""
import copy
import json
from pathlib import Path
import unittest

from framework_stability import GROWTH_LIMIT, validate_stability

FRAMEWORK = "6f902fdab24ccefdde97c991b50faf551928b604"
PROBE = "a" * 40


class FrameworkStabilityTests(unittest.TestCase):
    """Reject plausible-looking success reports when workload or persistence proof is absent."""

    def setUp(self):
        """Extend a genuine basic probe report with a bounded synthetic soak contract."""
        source = Path(__file__).parent / "framework_evidence/device-run-1.json"
        template = json.loads(source.read_text())
        self.reports = [copy.deepcopy(template) for _ in range(5)]
        for index, report in enumerate(self.reports):
            report.update(run_id=f"run-{index}", previous_runs=3 + index, probe_source_revision=PROBE)
            report["stability"] = {"mode": "restart-persistence", "passed": True, "final_counter": 135}
        self.reports[0]["stability"] = {
            "mode": "foreground-soak", "passed": True, "reader_thread_exit": True,
            "requested_seconds": 120, "elapsed_seconds": 120.1,
            "iterations": 250, "reader_queries": 500, "reader_connections": 16,
            "warmup_seconds": 60, "warmup_baseline_phys_footprint": 1000,
            "peak_phys_footprint": 1000, "post_warmup_peak_phys_footprint": 1000,
            "growth_limit_bytes": GROWTH_LIMIT, "previous_counter": 10, "final_counter": 135,
            "samples": [{"elapsed_seconds": second, "phys_footprint": 1000} for second in range(0, 120, 10)],
        }

    def test_complete_workload_passes(self):
        """Accept full duration, joined workers, exact transaction visibility and fresh restarts."""
        validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_short_workload_fails(self):
        """Reject early completion even when aggregate flags claim a pass."""
        self.reports[0]["stability"]["elapsed_seconds"] = 60
        with self.assertRaisesRegex(ValueError, "duration"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_background_sampling_gap_fails(self):
        """Reject a wall-clock soak that spent an unobserved interval suspended."""
        samples = self.reports[0]["stability"]["samples"]
        samples[:] = [sample for sample in samples if sample["elapsed_seconds"] not in (30, 40, 50, 60)]
        with self.assertRaisesRegex(ValueError, "Sampling gap"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_memory_growth_fails(self):
        """Reject excessive measured growth even if reported peak fields agree with it."""
        soak = self.reports[0]["stability"]
        peak = 1000 + GROWTH_LIMIT + 1
        soak["samples"][-1]["phys_footprint"] = peak
        soak["peak_phys_footprint"] = soak["post_warmup_peak_phys_footprint"] = peak
        with self.assertRaisesRegex(ValueError, "footprint growth"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_transaction_visibility_fails(self):
        """Reject a successful SQL loop whose persisted counter includes rolled-back writes."""
        self.reports[0]["stability"]["final_counter"] += 1
        with self.assertRaisesRegex(ValueError, "Commit and rollback"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_restart_data_loss_fails(self):
        """Reject later process runs that lose the committed soak state."""
        self.reports[2]["stability"]["final_counter"] = 0
        with self.assertRaisesRegex(ValueError, "lost across"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_reader_exit_fails(self):
        """Reject cleanup without joining the concurrent connection owner."""
        self.reports[0]["stability"]["reader_thread_exit"] = False
        with self.assertRaisesRegex(ValueError, "cleanup"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)

    def test_stale_probe_revision_fails(self):
        """Reject evidence produced by a different test implementation."""
        self.reports[0]["probe_source_revision"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "Probe source"):
            validate_stability(self.reports, FRAMEWORK, PROBE, 120)


if __name__ == "__main__":
    unittest.main()
