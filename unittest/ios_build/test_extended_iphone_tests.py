#!/usr/bin/env python3
"""Regression coverage for lifecycle ordering and fail-closed final evidence audits."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import run_extended_iphone_tests as extended


class ExtendedEvidenceTest(unittest.TestCase):
    """Reject incomplete device transitions and unproven phase completion."""

    def test_transition_requires_order_and_live_engine(self):
        """Reject reordered callbacks and engine shutdown during backgrounding."""
        events = [{'name': 'background', 'engine_state': 2, 'timestamp': 1},
                  {'name': 'foreground', 'engine_state': 2, 'timestamp': 2}]
        extended.validate_transition({'lifecycle_events': events})
        for invalid in (events[::-1], events[:1],
                        [{'name': 'background', 'engine_state': 4, 'timestamp': 1}, events[1]],
                        [events[0], {**events[1], 'timestamp': 0}]):
            with self.subTest(events=invalid), self.assertRaises(ValueError):
                extended.validate_transition({'lifecycle_events': invalid})

    def checkpoint(self):
        """Return a complete prior-phase checkpoint for corruption regression tests."""
        return {'run_id': 'test', 'source_commit': 'a' * 40, 'phases': [
            {'id': phase, 'status': 'passed', 'cases': [
                {'status': 'passed', 'exit_status': 0, 'clean_state': True,
                 'evidence_paths': ['evidence-example.json']}]} for phase in extended.PRIOR_PHASES]}

    def test_matrix_rejects_missing_failed_and_unsafe_evidence(self):
        """Never promote absent coverage or missing files to a passing matrix."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extended.save(root / 'evidence-example.json', {'passed': True})
            checkpoint = self.checkpoint()
            extended.save(root / 'checkpoint.json', checkpoint)
            self.assertEqual(len(extended.matrix(root, 'test', 'a' * 40)['required_phases']), 7)
            variants = []
            missing = copy.deepcopy(checkpoint)
            missing['phases'].pop()
            variants.append(missing)
            for field, value in [('status', 'excluded'), ('exit_status', 1),
                                 ('clean_state', False), ('evidence_paths', []),
                                 ('evidence_paths', ['../evidence-example.json'])]:
                invalid = copy.deepcopy(checkpoint)
                invalid['phases'][0]['cases'][0][field] = value
                variants.append(invalid)
            for invalid in variants:
                extended.save(root / 'checkpoint.json', invalid)
                with self.assertRaises(ValueError):
                    extended.matrix(root, 'test', 'a' * 40)
            extended.save(root / 'checkpoint.json', checkpoint)
            (root / 'evidence-example.json').unlink()
            with self.assertRaises(ValueError):
                extended.matrix(root, 'test', 'a' * 40)

    def test_matrix_rejects_stale_identity_and_changed_bytes(self):
        """Bind final acceptance to current source, runner identity, and retained bytes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extended.save(root / 'evidence-example.json', {'passed': True})
            extended.save(root / 'checkpoint.json', self.checkpoint())
            extended.save(root / 'evidence-final-matrix.json', extended.matrix(root, 'test', 'a' * 40))
            extended.validate_evidence(root, 'final-matrix', 'test', 'a' * 40)
            for run_id, revision in [('other', 'a' * 40), ('test', 'b' * 40)]:
                with self.assertRaises(ValueError):
                    extended.validate_evidence(root, 'final-matrix', run_id, revision)
            extended.save(root / 'evidence-example.json', {'passed': False})
            with self.assertRaises(ValueError):
                extended.validate_evidence(root, 'final-matrix', 'test', 'a' * 40)


if __name__ == '__main__':
    unittest.main()
