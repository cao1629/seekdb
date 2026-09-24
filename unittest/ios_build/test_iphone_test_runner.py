#!/usr/bin/env python3
"""Tests for standalone iPhone phase orchestration and reporting."""

import datetime as dt
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import iphone_test_runner as runner
import iphone_test_state as state


UTC = dt.timezone.utc


def _try_lock(output_root, run_directory, result_queue):
    """Report whether another process can acquire the active runner lock."""
    try:
        with state.RunLock(Path(output_root), Path(run_directory)):
            result_queue.put("acquired")
    except state.RunLockedError:
        result_queue.put("locked")


class IphoneTestRunnerTest(unittest.TestCase):
    """Verify phase execution, failure artifacts, redaction, and reports."""

    def setUp(self):
        """Create one isolated selected run for each test."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "iphone_test"
        self.timestamp = dt.datetime(2026, 9, 24, 10, 30, tzinfo=UTC)
        self.selection = state.select_run(
            self.output_root,
            mode=state.RunMode.DEFAULT,
            source_commit="a" * 40,
            config_fingerprint="b" * 64,
            now=self.timestamp,
        )
        self._selection_consumed = False

    def tearDown(self):
        """Release an unused selection and remove temporary output."""
        if not self._selection_consumed:
            self.selection.close()
        self.temporary_directory.cleanup()

    def clock(self):
        """Return a deterministic timestamp for persisted transitions."""
        return self.timestamp

    def adapters(self, overrides=None):
        """Build all eight required adapters with one passing case each."""
        overrides = overrides or {}
        adapters = []
        for index, phase_id in enumerate(runner.PHASE_IDS, start=1):
            case_id = f"case-{index}"
            executor = overrides.get(
                phase_id, lambda _case: runner.CaseResult.passed())
            adapters.append(runner.PhaseAdapter(
                phase_id=phase_id,
                cases=(runner.CaseSpec(
                    case_id=case_id,
                    execution_class=(
                        "device-native" if index < 6 else
                        "host-driven-device" if index < 8 else
                        "host-only"),
                ),),
                execute=executor,
            ))
        return adapters

    def run_engine(self, adapters, redaction_tokens=()):
        """Execute and mark this test's selection as consumed by the engine."""
        self._selection_consumed = True
        return runner.run_phase_engine(
            self.output_root, self.selection, adapters, now=self.clock,
            redaction_tokens=redaction_tokens)

    def checkpoint(self):
        """Load the final persisted checkpoint for this test run."""
        return state.load_checkpoint(
            self.output_root, self.selection.run_directory)

    def test_executes_eight_phases_in_declared_order_with_running_transition(self):
        """Cases must be marked running before dispatch and phases stay ordered."""
        observed = []

        def inspect_running(case):
            """Observe the durable case state from inside adapter dispatch."""
            checkpoint = state.load_checkpoint(
                self.output_root, self.selection.run_directory)
            persisted = next(
                item for phase in checkpoint["phases"]
                for item in phase["cases"] if item["id"] == case.case_id)
            observed.append((case.case_id, persisted["status"]))
            return runner.CaseResult.passed()

        adapters = self.adapters({phase: inspect_running
                                  for phase in runner.PHASE_IDS})
        exit_status = self.run_engine(adapters)

        checkpoint = self.checkpoint()
        self.assertEqual(0, exit_status)
        self.assertEqual(list(runner.PHASE_IDS),
                         [phase["id"] for phase in checkpoint["phases"]])
        self.assertEqual([(f"case-{index}", "running")
                          for index in range(1, 9)], observed)
        self.assertTrue(all(
            case["status"] == "passed" for phase in checkpoint["phases"]
            for case in phase["cases"]))
        self.assertTrue(all(
            case["attempt_count"] == 1 for phase in checkpoint["phases"]
            for case in phase["cases"]))

    def test_selection_lock_is_held_during_dispatch_and_released_afterward(self):
        """The engine must own the selected run for its complete lifetime."""
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()

        def inspect_lock(_case):
            """Ask another process whether it can take the runner lock."""
            process = context.Process(
                target=_try_lock,
                args=(self.output_root, self.selection.run_directory, queue),
            )
            process.start()
            process.join(timeout=10)
            self.assertEqual(0, process.exitcode)
            self.assertEqual("locked", queue.get(timeout=2))
            return runner.CaseResult.passed()

        adapters = self.adapters({runner.PHASE_IDS[0]: inspect_lock})
        self.assertEqual(0, self.run_engine(adapters))

        released_queue = context.Queue()
        released = context.Process(
            target=_try_lock,
            args=(self.output_root, self.selection.run_directory,
                  released_queue),
        )
        released.start()
        released.join(timeout=10)
        self.assertEqual(0, released.exitcode)
        self.assertEqual("acquired", released_queue.get(timeout=2))

    def test_isolated_failure_continues_and_writes_one_collision_safe_file(self):
        """An isolated case failure must not prevent later isolated cases."""
        first_phase = runner.PHASE_IDS[0]
        adapters = self.adapters()
        executed = []
        adapters[0] = runner.PhaseAdapter(
            phase_id=first_phase,
            cases=(
                runner.CaseSpec("alpha/beta", "device-native"),
                runner.CaseSpec("alpha beta", "device-native"),
                runner.CaseSpec("later", "device-native"),
            ),
            execute=lambda case: (
                executed.append(case.case_id) or
                (runner.CaseResult.failed(
                    category="assertion", diagnostic="wrong result")
                 if case.case_id != "later" else runner.CaseResult.passed())
            ),
        )

        self.assertEqual(1, self.run_engine(adapters))

        failures = sorted(self.selection.run_directory.glob("failure-*.json"))
        self.assertEqual(["alpha/beta", "alpha beta", "later"], executed)
        self.assertEqual(2, len(failures))
        self.assertNotEqual(failures[0].name, failures[1].name)
        self.assertTrue(all(path.parent == self.selection.run_directory
                            for path in failures))
        self.assertEqual(
            {"alpha/beta", "alpha beta"},
            {json.loads(path.read_text())["case_id"] for path in failures},
        )

    def test_infrastructure_failure_stops_and_leaves_remaining_cases_pending(self):
        """Infrastructure failure must stop while leaving later work resumable."""
        executed = []

        def fail_infrastructure(case):
            """Return a device-connectivity infrastructure failure."""
            executed.append(case.case_id)
            return runner.CaseResult.failed(
                category="infrastructure",
                diagnostic="device disconnected",
                retry_safe=False,
                clean_state=False,
            )

        adapters = self.adapters({runner.PHASE_IDS[0]: fail_infrastructure})
        self.assertEqual(1, self.run_engine(adapters))

        checkpoint = self.checkpoint()
        statuses = [case["status"] for phase in checkpoint["phases"]
                    for case in phase["cases"]]
        self.assertEqual(["case-1"], executed)
        self.assertEqual("failed", statuses[0])
        self.assertTrue(all(status == "pending" for status in statuses[1:]))
        self.assertEqual("incomplete", checkpoint["status"])

    def test_missing_adapter_is_an_infrastructure_failure(self):
        """Omitting a required phase adapter must fail rather than pass silently."""
        self.assertEqual(1, self.run_engine(self.adapters()[:-1]))

        checkpoint = self.checkpoint()
        missing_phase = checkpoint["phases"][-1]
        self.assertEqual(runner.PHASE_IDS[-1], missing_phase["id"])
        self.assertEqual("failed", missing_phase["status"])
        self.assertEqual(
            f"missing-adapter-{runner.PHASE_IDS[-1]}",
            missing_phase["cases"][0]["id"],
        )
        failure = next(self.selection.run_directory.glob(
            f"failure-{runner.PHASE_IDS[-1]}-*.json"))
        self.assertEqual("infrastructure",
                         json.loads(failure.read_text())["failure_category"])

    def test_multiple_missing_adapters_have_phase_qualified_failures(self):
        """Every absent adapter must persist a distinct case and failure file."""
        self.assertEqual(1, self.run_engine(self.adapters()[:-2]))

        checkpoint = self.checkpoint()
        missing_phases = checkpoint["phases"][-2:]
        self.assertEqual(
            [f"missing-adapter-{phase_id}"
             for phase_id in runner.PHASE_IDS[-2:]],
            [phase["cases"][0]["id"] for phase in missing_phases],
        )
        self.assertTrue(all(
            phase["cases"][0]["status"] == "failed"
            for phase in missing_phases))
        failures = list(self.selection.run_directory.glob("failure-*.json"))
        self.assertEqual(2, len(failures))
        summary = json.loads(
            (self.selection.run_directory / "summary.json").read_text())
        self.assertEqual(2, summary["counts"]["result"]["failed"])

    def test_isolated_failure_stops_before_nonisolated_successor(self):
        """A prior failure must not dispatch a non-isolated successor case."""
        executed = []
        adapters = self.adapters()
        adapters[0] = runner.PhaseAdapter(
            phase_id=runner.PHASE_IDS[0],
            cases=(
                runner.CaseSpec("isolated-failure", "device-native"),
                runner.CaseSpec(
                    "shared-state-successor", "device-native",
                    isolated=False),
                runner.CaseSpec("later-isolated", "device-native"),
            ),
            execute=lambda case: (
                executed.append(case.case_id) or
                (runner.CaseResult.failed(
                    category="assertion", diagnostic="isolated failure")
                 if case.case_id == "isolated-failure"
                 else runner.CaseResult.passed())
            ),
        )

        self.assertEqual(1, self.run_engine(adapters))

        checkpoint = self.checkpoint()
        first_cases = checkpoint["phases"][0]["cases"]
        self.assertEqual(["isolated-failure"], executed)
        self.assertEqual(
            ["failed", "pending", "pending"],
            [case["status"] for case in first_cases],
        )
        self.assertEqual("incomplete", checkpoint["status"])

    def test_failure_diagnostics_are_bounded_and_sensitive_values_redacted(self):
        """Artifacts must bound text and redact known keys and foreign UUIDs."""
        foreign_uuid = "123e4567-e89b-12d3-a456-426614174000"
        long_text = "x" * (runner.MAX_DIAGNOSTIC_LENGTH + 50)

        def fail_sensitive(_case):
            """Return deliberately sensitive nested diagnostic metadata."""
            return runner.CaseResult.failed(
                category="assertion",
                diagnostic=(
                    f"device_id=secret-in-text {foreign_uuid} {long_text}"),
                details={
                    "device_id": "secret-device",
                    "nested": {"team_id": "SECRETTEAM", "safe": foreign_uuid},
                    "run_id": self.selection.checkpoint["run_id"],
                },
            )

        adapters = self.adapters({runner.PHASE_IDS[0]: fail_sensitive})
        self.assertEqual(1, self.run_engine(adapters))

        failure = json.loads(next(
            self.selection.run_directory.glob("failure-*.json")).read_text())
        serialized = json.dumps(failure, sort_keys=True)
        self.assertLessEqual(len(failure["diagnostic"]),
                             runner.MAX_DIAGNOSTIC_LENGTH)
        self.assertNotIn("secret-device", serialized)
        self.assertNotIn("secret-in-text", serialized)
        self.assertNotIn("SECRETTEAM", serialized)
        self.assertNotIn(foreign_uuid, serialized)
        self.assertIn(self.selection.checkpoint["run_id"], serialized)
        self.assertIn(runner.REDACTED, serialized)

    def test_sanitized_process_result_bounds_and_redacts_streams(self):
        """Subprocess output must have a typed bounded serialization boundary."""
        foreign_uuid = "123e4567-e89b-12d3-a456-426614174000"
        result = runner.SanitizedProcessResult.create(
            exit_status=7,
            stdout=f"udid: secret-device\n{foreign_uuid}",
            stderr="x" * (runner.MAX_PROCESS_OUTPUT_LENGTH + 20),
            run_id=self.selection.checkpoint["run_id"],
        )

        self.assertEqual(7, result.exit_status)
        self.assertNotIn("secret-device", result.stdout)
        self.assertNotIn(foreign_uuid, result.stdout)
        self.assertLessEqual(len(result.stderr),
                             runner.MAX_PROCESS_OUTPUT_LENGTH)

    def test_runtime_tokens_redact_diagnostics_details_and_process_output(self):
        """Per-run local inputs must be removed at every persistence channel."""
        tokens = (
            "custom-device-token", "custom.bundle.token",
            "CUSTOMTEAM", "custom signing identity",
        )

        def fail_with_runtime_tokens(_case):
            """Return tokens through text, nested details, and process streams."""
            process = runner.SanitizedProcessResult.create(
                9,
                stdout=f"device {tokens[0]} bundle {tokens[1]}",
                stderr=f"team {tokens[2]} identity {tokens[3]}",
                run_id=self.selection.checkpoint["run_id"],
            )
            return runner.CaseResult.failed(
                category="assertion",
                diagnostic=" ".join(tokens),
                details={
                    "ordinary": list(tokens),
                    tokens[1]: "runtime token used as a key",
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                },
            )

        adapters = self.adapters({
            runner.PHASE_IDS[0]: fail_with_runtime_tokens})
        self.assertEqual(
            1, self.run_engine(adapters, redaction_tokens=tokens))

        persisted = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in self.selection.run_directory.iterdir()
            if path.is_file())
        for token in tokens:
            self.assertNotIn(token, persisted)
        self.assertIn(runner.REDACTED, persisted)
        self.assertFalse(runner.has_runtime_redaction_tokens())

    def test_redacts_canonical_versionless_and_v7_uuids_except_run_id(self):
        """Redaction must cover canonical UUID text regardless of version bits."""
        versionless = "123e4567-e89b-02d3-0456-426614174000"
        version_seven = "018f3f5e-7b2c-7abc-b123-426614174000"
        run_id = self.selection.checkpoint["run_id"]

        sanitized = runner.sanitize(
            f"{versionless} {version_seven} {run_id}", run_id)

        self.assertEqual(f"{runner.REDACTED} {runner.REDACTED} {run_id}",
                         sanitized)

    def test_rejects_entire_labeled_multiline_sensitive_diagnostics(self):
        """Labeled secrets must not leak quoted payload continuations or names."""
        run_id = self.selection.checkpoint["run_id"]
        sensitive_diagnostics = (
            (
                "private_key=\"-----BEGIN PRIVATE KEY-----\n"
                "base64-secret-payload\n"
                "-----END PRIVATE KEY-----\"\ncommand failed",
                ("BEGIN PRIVATE KEY", "base64-secret-payload",
                 "END PRIVATE KEY", "command failed"),
            ),
            (
                "provisioning_content: '<plist>\n"
                "<key>DeveloperCertificates</key>\n"
                "<data>profile-secret</data>\n"
                "</plist>'",
                ("plist", "DeveloperCertificates", "profile-secret"),
            ),
            (
                "certificate_identity=\"Apple Development: Jane Doe "
                "(SECRETTEAM)\"\ncodesign failed",
                ("Jane Doe", "SECRETTEAM", "codesign failed"),
            ),
        )
        for diagnostic, leaked_fragments in sensitive_diagnostics:
            with self.subTest(diagnostic=diagnostic.splitlines()[0]):
                sanitized = runner.sanitize(diagnostic, run_id)
                self.assertEqual(runner.REDACTED, sanitized)
                for fragment in leaked_fragments:
                    self.assertNotIn(fragment, sanitized)

        ordinary = "assertion mismatch: expected 1, got 2\ncase remained clean"
        self.assertEqual(ordinary, runner.sanitize(ordinary, run_id))

    def test_sensitive_labels_following_punctuation_are_rejected(self):
        """Sensitive keys after punctuation must not evade field rejection."""
        run_id = self.selection.checkpoint["run_id"]
        diagnostics = (
            "error(private_key=secret-pem)",
            '["team_id":"SECRETTEAM"]',
            "codesign failed --udid=00008110-secret-device",
        )
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(
                    runner.REDACTED, runner.sanitize(diagnostic, run_id))

        ordinary = (
            "candidate_private_key=value "
            "valid_team_id_suffix=value notudid=value")
        self.assertEqual(ordinary, runner.sanitize(ordinary, run_id))

    def test_redacts_normalized_apple_identity_forms_without_false_positives(self):
        """Apple identifiers and normalized labels must redact whole fields."""
        run_id = self.selection.checkpoint["run_id"]
        sensitive = (
            "Device ID: 00008110-001234567890001E",
            "device-id=00008110001234567890001E",
            "identifier: 00008110-001234567890001E",
            "team-id: ABCDE12345",
            "Apple Development: Jane Doe (ABCDE12345)",
            "install failed for 00008110-001234567890001E",
        )
        for diagnostic in sensitive:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(
                    runner.REDACTED, runner.sanitize(diagnostic, run_id))

        ordinary = (
            "device identifier parsing failed; identifier_count=2; "
            "Apple developer tools unavailable; hash=00008110abcdef")
        self.assertEqual(ordinary, runner.sanitize(ordinary, run_id))

    def test_redacts_distribution_and_canonical_signing_identities(self):
        """Distribution and parenthesized-team identities must be rejected."""
        run_id = self.selection.checkpoint["run_id"]
        identities = (
            "Apple Distribution: Jane Doe (ABCDE12345)",
            "iPhone Distribution: Example Company (ZYXWV98765)",
            "Developer ID Application: Build Account (A1B2C3D4E5)",
        )
        for identity in identities:
            with self.subTest(identity=identity):
                self.assertEqual(
                    runner.REDACTED, runner.sanitize(identity, run_id))

        ordinary = "distribution archive rejected (error 17)"
        self.assertEqual(ordinary, runner.sanitize(ordinary, run_id))

    def test_exclusion_contract_requires_exact_tracked_reason(self):
        """Only exact reviewed non-applicability may produce an exclusion."""
        with self.assertRaisesRegex(ValueError, "exclusion_reason"):
            runner.CaseSpec(
                "missing-reason", "host-only",
                applicability="not-applicable")
        with self.assertRaisesRegex(ValueError, "exclusion_reason"):
            runner.CaseSpec(
                "unexpected-reason", "host-only",
                exclusion_reason="requires fork")

        applicable = runner.CaseSpec("applicable", "device-native")
        excluded = runner.validate_case_result(
            applicable, runner.CaseResult.excluded("requires fork"))
        self.assertEqual("failed", excluded.status)
        self.assertEqual("infrastructure", excluded.category)

        not_applicable = runner.CaseSpec(
            "fork-only", "host-only", applicability="not-applicable",
            exclusion_reason="requires fork")
        matching = runner.validate_case_result(
            not_applicable, runner.CaseResult.excluded("requires fork"))
        self.assertEqual("excluded", matching.status)
        for result in (
                runner.CaseResult.excluded("different reason"),
                runner.CaseResult.passed()):
            with self.subTest(status=result.status,
                              diagnostic=result.diagnostic):
                normalized = runner.validate_case_result(
                    not_applicable, result)
                self.assertEqual("failed", normalized.status)
                self.assertEqual("infrastructure", normalized.category)

    def test_inconsistent_pass_is_failure_and_stops_dispatch(self):
        """Contradictory pass metadata must become an unsafe central failure."""
        inconsistent = (
            (runner.CaseResult.passed(exit_status=7), "infrastructure"),
            (runner.CaseResult.passed(clean_state=False), "cleanup"),
            (runner.CaseResult.passed(retry_safe=False), "infrastructure"),
        )
        spec = runner.CaseSpec("case", "device-native")
        for result, category in inconsistent:
            with self.subTest(result=result):
                normalized = runner.validate_case_result(spec, result)
                self.assertEqual("failed", normalized.status)
                self.assertEqual(category, normalized.category)
                self.assertFalse(normalized.retry_safe)

        executed = []
        adapters = self.adapters()
        adapters[0] = runner.PhaseAdapter(
            phase_id=runner.PHASE_IDS[0],
            cases=(
                runner.CaseSpec("contradictory-pass", "device-native"),
                runner.CaseSpec("must-not-run", "device-native"),
            ),
            execute=lambda case: (
                executed.append(case.case_id) or
                runner.CaseResult.passed(exit_status=7)),
        )
        self.assertEqual(1, self.run_engine(adapters))
        self.assertEqual(["contradictory-pass"], executed)
        checkpoint = self.checkpoint()
        self.assertEqual(
            ["failed", "pending"],
            [case["status"]
             for case in checkpoint["phases"][0]["cases"]],
        )

    def test_unsafe_categories_and_unclean_blocked_stop_all_dispatch(self):
        """Unsafe terminal outcomes must stop the next case and later phases."""
        outcomes = (
            runner.CaseResult.failed(
                category="cleanup", diagnostic="cleanup failed"),
            runner.CaseResult.failed(
                category="evidence", diagnostic="evidence malformed"),
            runner.CaseResult.failed(
                category="timeout", diagnostic="case timed out"),
            runner.CaseResult.blocked(
                "manual gate left device dirty", clean_state=False),
        )
        for index, outcome in enumerate(outcomes):
            with self.subTest(outcome=outcome):
                output_root = self.output_root / f"unsafe-{index}"
                selection = state.select_run(
                    output_root,
                    mode=state.RunMode.DEFAULT,
                    source_commit="a" * 40,
                    config_fingerprint="b" * 64,
                    now=self.timestamp,
                )
                executed = []
                adapters = self.adapters()
                adapters[0] = runner.PhaseAdapter(
                    phase_id=runner.PHASE_IDS[0],
                    cases=(
                        runner.CaseSpec("unsafe", "device-native"),
                        runner.CaseSpec("same-phase-next", "device-native"),
                    ),
                    execute=lambda case, result=outcome: (
                        executed.append(case.case_id) or result),
                )

                self.assertEqual(1, runner.run_phase_engine(
                    output_root, selection, adapters, now=self.clock))

                checkpoint = state.load_checkpoint(
                    output_root, selection.run_directory)
                statuses = [
                    case["status"] for phase in checkpoint["phases"]
                    for case in phase["cases"]]
                self.assertEqual(["unsafe"], executed)
                self.assertEqual(outcome.status, statuses[0])
                self.assertTrue(all(status == "pending"
                                    for status in statuses[1:]))

    def test_failure_files_keep_only_allowlisted_relative_evidence_paths(self):
        """Failure artifacts must not serialize arbitrary host filesystem paths."""
        def fail_with_paths(_case):
            """Return one valid and two unsafe evidence references."""
            return runner.CaseResult.failed(
                category="evidence",
                diagnostic="bad evidence",
                evidence_paths=(
                    "evidence-inventory-case-deadbeef.jsonl",
                    "/tmp/raw-device-response.json",
                    "../outside.jsonl",
                ),
            )

        adapters = self.adapters({runner.PHASE_IDS[0]: fail_with_paths})
        self.assertEqual(1, self.run_engine(adapters))

        failure = json.loads(next(
            self.selection.run_directory.glob("failure-*.json")).read_text())
        self.assertEqual(
            ["evidence-inventory-case-deadbeef.jsonl"],
            failure["evidence_paths"],
        )

    def test_reports_have_deterministic_execution_applicability_and_result_counts(self):
        """JSON and Markdown must expose exact grouped counts stably."""
        adapters = self.adapters()
        adapters[0] = runner.PhaseAdapter(
            phase_id=runner.PHASE_IDS[0],
            cases=(
                runner.CaseSpec("pass", "device-native"),
                runner.CaseSpec(
                    "excluded", "host-only", applicability="not-applicable",
                    exclusion_reason="requires fork"),
            ),
            execute=lambda case: (
                runner.CaseResult.excluded("requires fork")
                if case.case_id == "excluded" else runner.CaseResult.passed()),
        )
        self.assertEqual(0, self.run_engine(adapters))

        summary_json = self.selection.run_directory / "summary.json"
        summary_md = self.selection.run_directory / "summary.md"
        before = (summary_json.read_bytes(), summary_md.read_bytes())
        runner.write_reports(
            self.output_root, self.selection.run_directory, self.checkpoint())
        after = (summary_json.read_bytes(), summary_md.read_bytes())
        summary = json.loads(after[0])
        self.assertEqual(before, after)
        self.assertEqual(8, summary["counts"]["result"]["passed"])
        self.assertEqual(1, summary["counts"]["result"]["excluded"])
        self.assertEqual(5,
                         summary["counts"]["execution_class"]["device-native"])
        self.assertEqual(2,
                         summary["counts"]["execution_class"]["host-only"])
        self.assertEqual(8,
                         summary["counts"]["applicability"]["applicable"])
        self.assertEqual(
            1, summary["counts"]["applicability"]["not-applicable"])
        self.assertEqual(list(runner.PHASE_IDS),
                         [phase["id"] for phase in summary["phases"]])

    def test_retry_pass_preserves_prior_failure_file(self):
        """A successful retry must retain its historical failure artifact."""
        adapters = self.adapters({
            runner.PHASE_IDS[0]: lambda _case: runner.CaseResult.failed(
                category="assertion", diagnostic="first attempt"),
        })
        self.assertEqual(1, self.run_engine(adapters))
        failure = next(self.selection.run_directory.glob("failure-*.json"))
        original = failure.read_bytes()

        resumed = state.select_run(
            self.output_root,
            mode=state.RunMode.RESUME,
            source_commit="a" * 40,
            config_fingerprint="b" * 64,
            now=self.timestamp + dt.timedelta(hours=1),
        )
        self.selection = resumed
        self._selection_consumed = False
        self.assertEqual(0, self.run_engine(self.adapters()))

        checkpoint = self.checkpoint()
        retried = checkpoint["phases"][0]["cases"][0]
        self.assertEqual("passed", retried["status"])
        self.assertEqual(2, retried["attempt_count"])
        self.assertTrue(failure.is_file())
        self.assertEqual(original, failure.read_bytes())

    def test_resume_after_infrastructure_failure_runs_pending_cases(self):
        """A clean retry must rerun the failure and reach untouched later work."""
        adapters = self.adapters({
            runner.PHASE_IDS[0]: lambda _case: runner.CaseResult.failed(
                category="infrastructure",
                diagnostic="temporarily disconnected",
                retry_safe=False,
                clean_state=False,
            ),
        })
        self.assertEqual(1, self.run_engine(adapters))

        resumed = state.select_run(
            self.output_root,
            mode=state.RunMode.RESUME,
            source_commit="a" * 40,
            config_fingerprint="b" * 64,
            now=self.timestamp + dt.timedelta(hours=1),
        )
        self.selection = resumed
        self._selection_consumed = False
        self.assertEqual(0, self.run_engine(self.adapters()))

        checkpoint = self.checkpoint()
        cases = [case for phase in checkpoint["phases"]
                 for case in phase["cases"]]
        self.assertEqual("passed", checkpoint["status"])
        self.assertEqual(2, cases[0]["attempt_count"])
        self.assertTrue(all(case["attempt_count"] == 1
                            for case in cases[1:]))

    def test_same_day_restart_preserves_prior_run_failure_file(self):
        """A fresh run must never overwrite an earlier run's case failure."""
        failing = self.adapters({
            runner.PHASE_IDS[0]: lambda _case: runner.CaseResult.failed(
                category="assertion", diagnostic="failed run"),
        })
        first_run_id = self.selection.checkpoint["run_id"]
        self.assertEqual(1, self.run_engine(failing))
        first_failure = next(self.selection.run_directory.glob("failure-*.json"))
        first_bytes = first_failure.read_bytes()

        restarted = state.select_run(
            self.output_root,
            mode=state.RunMode.RESTART,
            source_commit="a" * 40,
            config_fingerprint="b" * 64,
            now=self.timestamp + dt.timedelta(hours=1),
        )
        self.selection = restarted
        self._selection_consumed = False
        second_run_id = restarted.checkpoint["run_id"]
        self.assertEqual(1, self.run_engine(failing))

        failures = sorted(self.selection.run_directory.glob("failure-*.json"))
        self.assertEqual(2, len(failures))
        self.assertNotEqual(first_run_id, second_run_id)
        self.assertEqual(first_bytes, first_failure.read_bytes())
        self.assertEqual(
            {first_run_id, second_run_id},
            {json.loads(path.read_text())["run_id"] for path in failures},
        )


if __name__ == "__main__":
    unittest.main()
