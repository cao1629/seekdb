"""Prevent incomplete shutdown evidence from being accepted as a successful framework run."""
import copy
import json
from pathlib import Path
import unittest

from validate_framework_probe import validate_reports

EVIDENCE = Path(__file__).parent / "framework_evidence"
REVISION = "6f902fdab24ccefdde97c991b50faf551928b604"


class FrameworkEvidenceTests(unittest.TestCase):
    """Exercise failure gates using independently captured simulator socket SQL reports."""

    def setUp(self):
        """Load two real process runs without mutating the tracked evidence."""
        self.reports = [json.loads((EVIDENCE / f"simulator-run-{number}.json").read_text())
                        for number in (1, 2)]

    def test_complete_runs_pass(self):
        """Accept both complete runs only when persistent state advanced across processes."""
        validate_reports(self.reports, REVISION)

    def test_sql_success_without_thread_exit_fails(self):
        """Reject the original regression where SQL finished before TLS cleanup crashed."""
        del self.reports[0]["worker_exit"]
        with self.assertRaisesRegex(ValueError, "TLS destruction"):
            validate_reports(self.reports, REVISION)

    def test_failed_cleanup_fails(self):
        """Reject a failed assertion even if aggregate success fields claim completion."""
        step = next(step for step in self.reports[0]["steps"] if step["name"] == "stopped and cleaned up")
        step["passed"] = False
        with self.assertRaisesRegex(ValueError, "failed assertions"):
            validate_reports(self.reports, REVISION)

    def test_wrong_source_identity_fails(self):
        """Reject a stale audit marker as evidence for a committed adaptation."""
        self.reports[0]["build_id"] = "8f73e95b52cd"
        with self.assertRaisesRegex(ValueError, "revision"):
            validate_reports(self.reports, REVISION)

    def test_unchanged_persistence_fails(self):
        """Reject distinct runs that did not observe the previous transaction's counter."""
        self.reports[1]["previous_runs"] = self.reports[0]["previous_runs"]
        with self.assertRaisesRegex(ValueError, "persisted counter"):
            validate_reports(self.reports, REVISION)

    def test_same_process_report_fails(self):
        """Reject reusing the same report as proof of a fresh process restart."""
        self.reports[1] = copy.deepcopy(self.reports[0])
        with self.assertRaisesRegex(ValueError, "distinct process"):
            validate_reports(self.reports, REVISION)


if __name__ == "__main__":
    unittest.main()
