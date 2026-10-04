#!/usr/bin/env python3
"""Require exact victim ownership and current-run identity for deliberate memory exhaustion."""
import json
from pathlib import Path
import sys
import types
import tempfile
from unittest import mock
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

    def test_unverified_termination_still_recovers_without_claiming_success(self):
        """Retain failure and verify recovery even when natural death lacks Jetsam proof."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence"
            configuration = types.SimpleNamespace(device="private-device", bundle_id="org.probe")
            selected = types.SimpleNamespace(identifier="private-device", profile_identifier="profile")

            def stress_failure(options, round_id):
                """Persist a partial sample, then simulate absent system termination proof."""
                pressure.extended.save(options.output_dir / "evidence-memory-limit-progress.json", [])
                raise RuntimeError("no matching report")

            def recovery(options, round_id, previous, label):
                """Model successful validated SQL recovery and provide its retained bytes."""
                self.assertEqual(1, previous)
                name = "evidence-recovery.jsonl"
                (options.output_dir / name).write_text("complete recovery")
                return {"state": "Stopped", "previous_runs": 1}, name

            with mock.patch.object(pressure.sys, "argv", ["probe", "--output-dir", str(output)]), \
                    mock.patch.object(pressure.cli, "REPOSITORY_ROOT", root), \
                    mock.patch.object(pressure.cli, "source_commit", return_value="a"*40), \
                    mock.patch.object(pressure.cli, "resolve_local_configuration", return_value=configuration), \
                    mock.patch.object(pressure.cli, "discover_physical_devices", return_value=[]), \
                    mock.patch.object(pressure.cli, "select_physical_device", return_value=selected), \
                    mock.patch.object(pressure.dataclasses, "replace", return_value=configuration), \
                    mock.patch.object(pressure.cli, "infer_signing_configuration", return_value=configuration), \
                    mock.patch.object(pressure.phases, "prepare_test_app", return_value={}), \
                    mock.patch.object(pressure, "stress", side_effect=stress_failure), \
                    mock.patch.object(pressure.extended, "probe_pid", return_value=None), \
                    mock.patch.object(pressure.extended, "launch") as launch, \
                    mock.patch.object(pressure.extended, "sql_evidence", side_effect=recovery), \
                    self.assertRaisesRegex(RuntimeError, "no matching report"):
                pressure.main()
            launch.assert_called_once()
            result = json.loads((output / "evidence-memory-limit.json").read_text())
            self.assertEqual("not-verified", result["outcome"])
            self.assertTrue(result["recovery_verified"])
            self.assertIn("evidence-recovery.jsonl", result["evidence_sha256"])

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
