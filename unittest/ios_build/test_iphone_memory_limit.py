#!/usr/bin/env python3
"""Require exact victim ownership and current-run identity for deliberate memory exhaustion."""
import json
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_iphone_memory_limit as pressure


class MemoryLimitEvidenceTest(unittest.TestCase):
    """Prevent unrelated Jetsam, ordinary death, or stale progress from certifying exhaustion."""

    def report(self, **changes):
        """Return a minimal two-object Apple-format event with one owned victim."""
        victim = {'pid': 123, 'name': 'SeekDBProbe', 'reason': 'per-process-limit',
                  'rpages': 400000, 'lifetimeMax': 410000, 'states': ['frontmost']}
        victim.update(changes)
        return json.dumps({'bug_type': '298'}) + '\n' + json.dumps({
            'memoryStatus': {'pageSize': 16384}, 'processes': [victim]})

    def test_exact_owned_jetsam_metrics(self):
        """Use the report's page size and exclude raw PID/other process metadata."""
        value = pressure.jetsam_victim(self.report(), 123)
        self.assertEqual(6553600000, value['resident_bytes'])
        self.assertEqual('per-process-limit', value['reason'])
        self.assertNotIn('pid', value)
        self.assertNotIn('processes', value)

    def test_unrelated_or_unterminated_process_is_rejected(self):
        """A mere report mention, another PID, or unknown reason does not prove Jetsam."""
        for changes in ({'pid': 124}, {'name': 'OtherApp'}, {'reason': None},
                        {'reason': 'SIGKILL'}, {'rpages': -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pressure.jetsam_victim(self.report(**changes), 123)
        with self.assertRaises(ValueError):
            pressure.jetsam_victim(self.report().replace('298', '309'), 123)

    def test_stale_and_impossible_pressure_checkpoints_are_rejected(self):
        """Require run/build/database binding and correctly aligned bounded allocation steps."""
        options = types.SimpleNamespace(build_id='a'*12, data_name='owned-db')
        value = {'schema_version': 1, 'run_id': 'current', 'build_id': 'a'*12,
                 'data_name': 'owned-db', 'chunk_bytes': 64*1024**2,
                 'ceiling_bytes': 8*1024**3, 'allocated_bytes': 64*1024**2,
                 'attempted_bytes': 128*1024**2, 'footprint_bytes': 200*1024**2}
        pressure.progress_identity(value, options, 'current')
        for changes in ({'run_id': 'old'}, {'build_id': 'b'*12}, {'data_name': 'other-db'},
                        {'allocated_bytes': 1}, {'attempted_bytes': 9*1024**3},
                        {'chunk_bytes': 128*1024**2}, {'footprint_bytes': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pressure.progress_identity(dict(value, **changes), options, 'current')


if __name__ == '__main__':
    unittest.main()
