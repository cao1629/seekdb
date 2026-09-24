#!/usr/bin/env python3
"""Tests for resumable standalone iPhone test state."""

import datetime as dt
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import iphone_test_state as state


UTC = dt.timezone.utc


def _try_run_lock(output_root, run_directory, result_queue):
    """Report whether a separate process can acquire one run lock."""
    try:
        with state.RunLock(Path(output_root), Path(run_directory)):
            result_queue.put("acquired")
    except state.RunLockedError:
        result_queue.put("locked")


class IphoneTestStateTest(unittest.TestCase):
    """Verify safe run selection and checkpoint persistence."""

    def setUp(self):
        """Create an isolated output root for each test."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "iphone_test"
        self.source_commit = "a" * 40
        self.config_fingerprint = "b" * 64

    def tearDown(self):
        """Remove the isolated output root after each test."""
        self.temporary_directory.cleanup()

    def now(self, day=24, hour=10):
        """Return a deterministic timezone-aware timestamp."""
        return dt.datetime(2026, 9, day, hour, 30, tzinfo=UTC)

    def new_checkpoint(self, timestamp=None, status="incomplete"):
        """Build a checkpoint with deterministic compatibility metadata."""
        checkpoint = state.create_checkpoint(
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=timestamp or self.now(),
        )
        checkpoint["status"] = status
        if status == "passed":
            checkpoint["phases"] = [{
                "id": "complete",
                "status": "passed",
                "cases": [self.case_record(
                    "complete", "passed", attempt_count=1)],
            }]
        return checkpoint

    def save_for_day(self, day, checkpoint):
        """Persist a checkpoint below one start-date directory."""
        run_directory = self.output_root / day
        state.save_checkpoint(self.output_root, run_directory, checkpoint)
        return run_directory

    def test_preview_run_path_ignores_identity_without_mutating_state(self):
        """Pre-build discovery may print a path before artifact identity exists."""
        older = self.new_checkpoint(self.now(day=20, hour=8))
        older["config_fingerprint"] = "c" * 64
        run_directory = self.save_for_day("2026-09-20", older)
        before = (run_directory / state.CHECKPOINT_FILENAME).read_bytes()

        preview = state.preview_run_path(
            self.output_root, state.RunMode.RESUME, self.now(day=24))

        self.assertEqual(run_directory, preview.run_directory)
        self.assertEqual(before, (
            run_directory / state.CHECKPOINT_FILENAME).read_bytes())

    def case_record(self, case_id, status, attempt_count=0):
        """Build a structurally valid case record for one lifecycle state."""
        record = {
            "id": case_id,
            "status": status,
            "attempt_count": attempt_count,
            "interruption_count": 0,
        }
        if status == "running":
            record["started_at"] = self.now().isoformat()
        if status in {"passed", "failed", "excluded", "blocked"}:
            record["completed_at"] = self.now().isoformat()
        return record

    def test_default_continues_incomplete_same_day_run(self):
        """Default mode should reuse today's incomplete run."""
        original = self.new_checkpoint(self.now(hour=8))
        run_directory = self.save_for_day("2026-09-24", original)

        selected = state.select_run(
            self.output_root,
            mode=state.RunMode.DEFAULT,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(hour=11),
        )

        self.assertTrue(selected.resumed)
        self.assertEqual(run_directory, selected.run_directory)
        self.assertEqual(original["run_id"], selected.checkpoint["run_id"])
        self.assertEqual(original["started_at"], selected.checkpoint["started_at"])
        self.assertEqual(self.now(hour=11).isoformat(),
                         selected.checkpoint["last_resumed_at"])

    def test_resume_selects_latest_incomplete_started_at_across_dates(self):
        """Resume should use started_at rather than directory ordering."""
        older = self.new_checkpoint(self.now(day=22, hour=20))
        newer = self.new_checkpoint(self.now(day=23, hour=7))
        complete = self.new_checkpoint(self.now(day=24, hour=9), status="passed")
        self.save_for_day("2026-09-22", older)
        expected_directory = self.save_for_day("2026-09-20", newer)
        self.save_for_day("2026-09-24", complete)

        selected = state.select_run(
            self.output_root,
            mode=state.RunMode.RESUME,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(day=25),
        )

        self.assertEqual(expected_directory, selected.run_directory)
        self.assertEqual(newer["run_id"], selected.checkpoint["run_id"])

    def test_resume_without_incomplete_candidate_fails(self):
        """Resume should never create a run when no candidate exists."""
        self.save_for_day(
            "2026-09-24", self.new_checkpoint(status="passed"))

        with self.assertRaisesRegex(state.NoResumableRunError,
                                    "no incomplete checkpoint"):
            state.select_run(
                self.output_root,
                mode=state.RunMode.RESUME,
                source_commit=self.source_commit,
                config_fingerprint=self.config_fingerprint,
                now=self.now(day=25),
            )

    def test_restart_backs_up_checkpoint_and_reports_in_same_directory(self):
        """Restart should preserve prior state and reports before replacement."""
        old = self.new_checkpoint(self.now(hour=8))
        run_directory = self.save_for_day("2026-09-24", old)
        (run_directory / "summary.json").write_text("{}\n", encoding="utf-8")
        (run_directory / "summary.md").write_text("old\n", encoding="utf-8")

        selected = state.select_run(
            self.output_root,
            mode=state.RunMode.RESTART,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(hour=12),
        )

        self.assertFalse(selected.resumed)
        self.assertNotEqual(old["run_id"], selected.checkpoint["run_id"])
        self.assertEqual(1, len(list(run_directory.glob("checkpoint.backup-*.json"))))
        self.assertEqual(1, len(list(run_directory.glob("summary.backup-*.json"))))
        self.assertEqual(1, len(list(run_directory.glob("summary.backup-*.md"))))

    def test_run_lock_prevents_concurrent_lost_updates(self):
        """A second runner process must fail before it can overwrite state."""
        run_directory = self.save_for_day(
            "2026-09-24", self.new_checkpoint())
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()

        with state.RunLock(self.output_root, run_directory):
            process = context.Process(
                target=_try_run_lock,
                args=(self.output_root, run_directory, result_queue),
            )
            process.start()
            process.join(timeout=10)

        self.assertEqual(0, process.exitcode)
        self.assertEqual("locked", result_queue.get(timeout=2))
        second_queue = context.Queue()
        second = context.Process(
            target=_try_run_lock,
            args=(self.output_root, run_directory, second_queue),
        )
        second.start()
        second.join(timeout=10)
        self.assertEqual(0, second.exitcode)
        self.assertEqual("acquired", second_queue.get(timeout=2))

    def test_checkpoint_generation_rejects_stale_lost_update(self):
        """Two readers of one generation must not both overwrite checkpoint."""
        run_directory = self.save_for_day(
            "2026-09-24", self.new_checkpoint())
        first = state.load_checkpoint(self.output_root, run_directory)
        stale = state.load_checkpoint(self.output_root, run_directory)
        first["last_resumed_at"] = self.now(hour=11).isoformat()
        stale["last_resumed_at"] = self.now(hour=12).isoformat()

        state.save_checkpoint(self.output_root, run_directory, first)

        with self.assertRaises(state.ConcurrentCheckpointUpdateError):
            state.save_checkpoint(self.output_root, run_directory, stale)

    def test_selected_run_holds_lock_until_runner_closes(self):
        """Run selection should retain exclusive ownership for runner lifetime."""
        selection = state.select_run(
            self.output_root,
            mode=state.RunMode.DEFAULT,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(),
        )
        context = multiprocessing.get_context("spawn")
        locked_queue = context.Queue()
        locked_process = context.Process(
            target=_try_run_lock,
            args=(self.output_root, selection.run_directory, locked_queue),
        )
        locked_process.start()
        locked_process.join(timeout=10)
        self.assertEqual(0, locked_process.exitcode)
        self.assertEqual("locked", locked_queue.get(timeout=2))

        selection.close()
        released_queue = context.Queue()
        released_process = context.Process(
            target=_try_run_lock,
            args=(self.output_root, selection.run_directory, released_queue),
        )
        released_process.start()
        released_process.join(timeout=10)
        self.assertEqual(0, released_process.exitcode)
        self.assertEqual("acquired", released_queue.get(timeout=2))

    def test_restart_moves_reports_before_checkpoint_commit_point(self):
        """An interrupted restart should leave the old checkpoint resumable."""
        run_directory = self.save_for_day(
            "2026-09-24", self.new_checkpoint())
        (run_directory / "summary.json").write_text("{}\n", encoding="utf-8")
        (run_directory / "summary.md").write_text("old\n", encoding="utf-8")
        real_replace = os.replace
        real_fsync = os.fsync
        events = []

        def interrupt_checkpoint_move(source, destination, **kwargs):
            """Fail at the restart commit point after recording move order."""
            events.append(source)
            if source == "checkpoint.json":
                raise OSError("simulated restart interruption")
            return real_replace(source, destination, **kwargs)

        def recording_fsync(descriptor):
            """Record the report durability barrier before the commit point."""
            events.append("fsync")
            return real_fsync(descriptor)

        with mock.patch.object(state.os, "replace",
                               side_effect=interrupt_checkpoint_move), \
                mock.patch.object(state.os, "fsync",
                                  side_effect=recording_fsync):
            with self.assertRaisesRegex(OSError, "simulated restart"):
                state.select_run(
                    self.output_root,
                    mode=state.RunMode.RESTART,
                    source_commit=self.source_commit,
                    config_fingerprint=self.config_fingerprint,
                    now=self.now(hour=12),
                )

        self.assertEqual(
            ["summary.json", "summary.md", "fsync", "checkpoint.json"],
            events,
        )
        self.assertTrue((run_directory / "checkpoint.json").is_file())
        self.assertFalse((run_directory / "summary.json").exists())
        self.assertFalse((run_directory / "summary.md").exists())

    def test_new_run_quarantines_reports_left_without_checkpoint(self):
        """Stale canonical reports must not become part of a fresh run."""
        run_directory = self.output_root / "2026-09-24"
        run_directory.mkdir(parents=True)
        (run_directory / "summary.json").write_text("{}\n", encoding="utf-8")
        (run_directory / "summary.md").write_text("old\n", encoding="utf-8")

        state.select_run(
            self.output_root,
            mode=state.RunMode.DEFAULT,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(hour=12),
        )

        self.assertFalse((run_directory / "summary.json").exists())
        self.assertFalse((run_directory / "summary.md").exists())
        self.assertEqual(1, len(list(run_directory.glob("summary.backup-*.json"))))
        self.assertEqual(1, len(list(run_directory.glob("summary.backup-*.md"))))

    def test_atomic_save_flushes_file_then_replaces_and_fsyncs_directory(self):
        """Checkpoint writes should use a durable same-directory replacement."""
        checkpoint = self.new_checkpoint()
        run_directory = self.output_root / "2026-09-24"
        real_replace = os.replace
        events = []

        def recording_replace(source, destination, **kwargs):
            """Record and perform the tested atomic rename."""
            events.append((source, destination, kwargs))
            real_replace(source, destination, **kwargs)

        with mock.patch.object(state.os, "replace",
                               side_effect=recording_replace) as replace_mock, \
                mock.patch.object(state.os, "fsync",
                                  wraps=os.fsync) as fsync_mock:
            state.save_checkpoint(self.output_root, run_directory, checkpoint)

        self.assertEqual(1, replace_mock.call_count)
        source, destination, replace_kwargs = events[0]
        self.assertRegex(source, r"^\.checkpoint\.[0-9a-f]+\.tmp$")
        self.assertEqual("checkpoint.json", destination)
        self.assertEqual(replace_kwargs["src_dir_fd"],
                         replace_kwargs["dst_dir_fd"])
        self.assertFalse((run_directory / source).exists())
        self.assertGreaterEqual(fsync_mock.call_count, 2)
        self.assertEqual(
            checkpoint,
            json.loads((run_directory / destination).read_text()),
        )

    def test_load_recovers_interrupted_running_case(self):
        """A running case from a dead process should become retryable pending."""
        checkpoint = self.new_checkpoint()
        checkpoint["phases"] = [{
            "id": "device",
            "status": "running",
            "cases": [{
                "id": "startup",
                "status": "running",
                "attempt_count": 1,
                "interruption_count": 2,
                "started_at": self.now(hour=12).isoformat(),
            }],
        }]
        run_directory = self.save_for_day("2026-09-24", checkpoint)

        selected = state.select_run(
            self.output_root,
            mode=state.RunMode.DEFAULT,
            source_commit=self.source_commit,
            config_fingerprint=self.config_fingerprint,
            now=self.now(hour=13),
        )

        recovered = selected.checkpoint["phases"][0]["cases"][0]
        self.assertEqual("pending", recovered["status"])
        self.assertEqual(3, recovered["interruption_count"])
        self.assertEqual(1, recovered["attempt_count"])
        self.assertEqual("pending", selected.checkpoint["phases"][0]["status"])
        self.assertEqual(self.now(hour=13).isoformat(),
                         recovered["last_interrupted_at"])
        self.assertEqual(selected.checkpoint,
                         json.loads((run_directory / "checkpoint.json").read_text()))

    def test_resume_rejects_source_schema_and_config_mismatches(self):
        """Resume should reject every incompatible identity boundary."""
        mutations = (
            ("source_commit", "c" * 40, "source commit"),
            ("schema_version", state.SCHEMA_VERSION + 1, "schema version"),
            ("config_fingerprint", "d" * 64, "configuration fingerprint"),
        )
        for key, value, message in mutations:
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    output_root = Path(temporary_directory) / "iphone_test"
                    checkpoint = self.new_checkpoint()
                    run_directory = output_root / "2026-09-24"
                    state.save_checkpoint(output_root, run_directory, checkpoint)
                    checkpoint[key] = value
                    (run_directory / "checkpoint.json").write_text(
                        json.dumps(checkpoint), encoding="utf-8")
                    with self.assertRaisesRegex(
                            state.IncompatibleCheckpointError, message):
                        state.select_run(
                            output_root,
                            mode=state.RunMode.RESUME,
                            source_commit=self.source_commit,
                            config_fingerprint=self.config_fingerprint,
                            now=self.now(day=25),
                        )

    def test_passed_cases_are_skipped_and_failed_cases_are_retried(self):
        """Only pending and failed cases should be returned for execution."""
        checkpoint = self.new_checkpoint()
        checkpoint["phases"] = [{
            "id": "phase",
            "cases": [
                self.case_record("passed", "passed", attempt_count=1),
                self.case_record("failed", "failed", attempt_count=1),
                self.case_record("pending", "pending"),
                self.case_record("excluded", "excluded"),
                self.case_record("blocked", "blocked"),
            ],
        }]

        retryable = state.retryable_case_ids(checkpoint)

        self.assertEqual(["failed", "pending"], retryable)

    def test_rejects_unknown_case_status_instead_of_silently_skipping(self):
        """Unknown case states must fail checkpoint validation, not disappear."""
        checkpoint = self.new_checkpoint()
        checkpoint["phases"] = [{
            "id": "phase",
            "cases": [{
                "id": "case",
                "status": "paszed",
                "attempt_count": 0,
                "interruption_count": 0,
            }],
        }]

        with self.assertRaisesRegex(state.CorruptCheckpointError,
                                    "case status"):
            state.save_checkpoint(
                self.output_root, self.output_root / "2026-09-24", checkpoint)
        run_directory = self.output_root / "2026-09-24"
        run_directory.mkdir(parents=True)
        (run_directory / "checkpoint.json").write_text(
            json.dumps(checkpoint), encoding="utf-8")
        with self.assertRaisesRegex(state.CorruptCheckpointError,
                                    "case status"):
            state.load_checkpoint(self.output_root, run_directory)
        with self.assertRaisesRegex(state.CorruptCheckpointError,
                                    "case status"):
            state.retryable_case_ids(checkpoint)

    def test_rejects_malformed_and_duplicate_phase_or_case_records(self):
        """Every nested record should be shaped and uniquely identified."""
        invalid_phases = (
            [{"id": "phase", "cases": {}}],
            [{"id": "phase", "cases": ["not-an-object"]}],
            [
                {"id": "same", "cases": []},
                {"id": "same", "cases": []},
            ],
            [
                {"id": "one", "cases": [self.case_record("same", "pending")]},
                {"id": "two", "cases": [self.case_record("same", "pending")]},
            ],
        )
        for index, phases in enumerate(invalid_phases):
            with self.subTest(index=index):
                checkpoint = self.new_checkpoint()
                checkpoint["phases"] = phases
                with self.assertRaises(state.CorruptCheckpointError):
                    state.save_checkpoint(
                        self.output_root,
                        self.output_root / "2026-09-24",
                        checkpoint,
                    )

    def test_rejects_invalid_case_counters_and_terminal_invariants(self):
        """Counters and lifecycle timestamps should agree with case status."""
        invalid_cases = (
            {
                "id": "negative",
                "status": "pending",
                "attempt_count": -1,
                "interruption_count": 0,
            },
            {
                "id": "boolean",
                "status": "pending",
                "attempt_count": False,
                "interruption_count": 0,
            },
            {
                "id": "running-without-start",
                "status": "running",
                "attempt_count": 1,
                "interruption_count": 0,
            },
            {
                "id": "pending-complete",
                "status": "pending",
                "attempt_count": 0,
                "interruption_count": 0,
                "completed_at": self.now().isoformat(),
            },
            {
                "id": "passed-without-completion",
                "status": "passed",
                "attempt_count": 1,
                "interruption_count": 0,
            },
            {
                "id": "failed-without-attempt",
                "status": "failed",
                "attempt_count": 0,
                "interruption_count": 0,
                "completed_at": self.now().isoformat(),
            },
        )
        for case in invalid_cases:
            with self.subTest(case=case["id"]):
                checkpoint = self.new_checkpoint()
                checkpoint["phases"] = [{"id": "phase", "cases": [case]}]
                with self.assertRaises(state.CorruptCheckpointError):
                    state.save_checkpoint(
                        self.output_root,
                        self.output_root / "2026-09-24",
                        checkpoint,
                    )

    def test_rejects_inconsistent_run_and_phase_aggregate_statuses(self):
        """Aggregate statuses must agree with every nested case state."""
        invalid = []
        passed_case = self.case_record("passed", "passed", attempt_count=1)
        failed_case = self.case_record("failed", "failed", attempt_count=1)
        invalid.append(("passed", [{"id": "phase", "cases": [failed_case]}]))
        invalid.append(("incomplete", [{"id": "phase", "cases": [passed_case]}]))
        invalid.append((
            "incomplete",
            [{"id": "phase", "status": "passed", "cases": [failed_case]}],
        ))
        for index, (run_status, phases) in enumerate(invalid):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    output_root = Path(temporary_directory) / "iphone_test"
                    checkpoint = self.new_checkpoint(status=run_status)
                    checkpoint["phases"] = phases
                    with self.assertRaisesRegex(state.CorruptCheckpointError,
                                                "status"):
                        state.save_checkpoint(
                            output_root,
                            output_root / "2026-09-24",
                            checkpoint,
                        )

    def test_creating_date_directory_fsyncs_output_root(self):
        """A new run-directory entry should be durable before checkpoint I/O."""
        checkpoint = self.new_checkpoint()
        real_fsync = os.fsync
        fsynced_directories = []

        def recording_fsync(descriptor):
            """Record each durability barrier while preserving real behavior."""
            stat_result = os.fstat(descriptor)
            fsynced_directories.append((stat_result.st_dev, stat_result.st_ino))
            return real_fsync(descriptor)

        with mock.patch.object(state.os, "fsync", side_effect=recording_fsync):
            state.save_checkpoint(
                self.output_root, self.output_root / "2026-09-24", checkpoint)

        root_stat = self.output_root.stat()
        self.assertIn(
            (root_stat.st_dev, root_stat.st_ino), fsynced_directories)

    def test_directory_fd_anchor_prevents_symlink_swap_redirection(self):
        """A swapped date path must not redirect checkpoint writes outside."""
        self.output_root.mkdir(parents=True)
        run_directory = self.output_root / "2026-09-24"
        run_directory.mkdir()
        anchored_directory = self.output_root / "anchored"
        outside_directory = Path(self.temporary_directory.name) / "outside"
        outside_directory.mkdir()
        real_open = os.open
        swapped = False

        def swap_after_directory_open(path, flags, *args, **kwargs):
            """Replace the visible run path immediately after secure opening."""
            nonlocal swapped
            descriptor = real_open(path, flags, *args, **kwargs)
            if (path == "2026-09-24" and kwargs.get("dir_fd") is not None
                    and not swapped):
                run_directory.rename(anchored_directory)
                run_directory.symlink_to(outside_directory, target_is_directory=True)
                swapped = True
            return descriptor

        with mock.patch.object(state.os, "open",
                               side_effect=swap_after_directory_open):
            state.save_checkpoint(
                self.output_root, run_directory, self.new_checkpoint())

        self.assertTrue((anchored_directory / "checkpoint.json").is_file())
        self.assertFalse((outside_directory / "checkpoint.json").exists())

    def test_save_rejects_mutated_run_identity(self):
        """An existing run ID and start timestamp should remain immutable."""
        checkpoint = self.new_checkpoint()
        run_directory = self.save_for_day("2026-09-24", checkpoint)
        for key, value in (("run_id", "0" * 32),
                           ("started_at", self.now(hour=9).isoformat())):
            with self.subTest(key=key):
                mutated = dict(checkpoint)
                mutated[key] = value
                with self.assertRaisesRegex(state.ImmutableRunIdentityError, key):
                    state.save_checkpoint(
                        self.output_root, run_directory, mutated)

    def test_rejects_traversal_symlinks_and_non_date_run_directories(self):
        """Run paths must be real direct date children of the output root."""
        checkpoint = self.new_checkpoint()
        unsafe_paths = (
            self.output_root / ".." / "escaped",
            self.output_root / "not-a-date",
            self.output_root / "2026-09-24" / "nested",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path):
                with self.assertRaises(state.UnsafeRunPathError):
                    state.save_checkpoint(
                        self.output_root, unsafe_path, checkpoint)

        self.output_root.mkdir(parents=True, exist_ok=True)
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        symlink = self.output_root / "2026-09-24"
        symlink.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(state.UnsafeRunPathError):
            state.save_checkpoint(self.output_root, symlink, checkpoint)

    def test_rejects_checkpoint_symlink(self):
        """Checkpoint reads must not follow a link outside the run directory."""
        run_directory = self.output_root / "2026-09-24"
        run_directory.mkdir(parents=True)
        outside = Path(self.temporary_directory.name) / "outside.json"
        outside.write_text(json.dumps(self.new_checkpoint()), encoding="utf-8")
        (run_directory / "checkpoint.json").symlink_to(outside)

        with self.assertRaises(state.UnsafeRunPathError):
            state.load_checkpoint(self.output_root, run_directory)

    def test_create_checkpoint_requires_timezone_aware_timestamp(self):
        """Run timestamps should never depend on an implicit timezone."""
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            state.create_checkpoint(
                source_commit=self.source_commit,
                config_fingerprint=self.config_fingerprint,
                now=dt.datetime(2026, 9, 24, 10, 30),
            )


if __name__ == "__main__":
    unittest.main()
