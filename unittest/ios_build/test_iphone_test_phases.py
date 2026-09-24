#!/usr/bin/env python3
"""Contract tests for standalone iPhone validation phases one through four."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import iphone_test_phases as phases
import iphone_test_runner as runner
import run_all_iphone_tests as cli


CPP_CASES = (
    "ios.cpp.allocator.backend",
    "ios.cpp.allocator.lifecycle",
    "ios.cpp.allocator.realloc_alignment",
    "ios.cpp.ob_error.mapping",
)
RUST_CASES = (
    "ios.rust.cert.formats_display_name_for_sql_account",
    "ios.rust.cert.rejects_truncated_certificate",
    "ios.rust.device.intentional_panic",
    "ios.rust.device.panic_continuation",
    "ios.rust.tls.exposes_sql_cipher_names",
)


class IphoneTestPhasesTest(unittest.TestCase):
    """Require stable metadata and sanitized execution for completed phases."""

    def configuration(self, root):
        """Return process-local device and signing inputs for one test."""
        engine = root / "build_ios_arm64"
        return cli.LocalConfiguration(
            device="private-device-token",
            bundle_id="org.private.probe",
            team="TEAMTOKEN1",
            signing_identity="private signing identity",
            engine_build=engine,
            app_artifact=engine / "app/Release-iphoneos/SeekDBProbe.app",
            test_hooks=True,
        )

    def build_environment(self, root):
        """Create explicit validated build prerequisites for one test."""
        deps = root / "deps"
        for relative in phases.REQUIRED_DEPENDENCY_ARTIFACTS:
            path = deps / relative
            if Path(relative).suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            else:
                path.mkdir(parents=True, exist_ok=True)
        cargo_home = root / "cargo-home"
        tools = cargo_home / "bin"
        tools.mkdir(parents=True)
        for name in ("cargo", "rustup"):
            tool = tools / name
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(0o755)
        rust_target = root / "rust-target"
        rust_target.mkdir()
        rustup_home = root / "rustup"
        rustup_home.mkdir()
        return {
            phases.DEPS_PREFIX_ENVIRONMENT: str(deps),
            phases.HEADERS_PREFIX_ENVIRONMENT: str(deps),
            "CARGO": str(tools / "cargo"),
            "RUSTUP": str(tools / "rustup"),
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_HOME": str(rustup_home),
            phases.RUST_TARGET_DIR_ENVIRONMENT: str(rust_target),
        }

    def test_contracts_register_stable_stage_one_through_four_cases(self):
        """Expose every existing native case plus production Rust isolation."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            contracts = phases.create_phase_contracts(
                configuration=self.configuration(root),
                suites=runner.PHASE_IDS,
                run_directory=root / "iphone_test/2026-09-24",
                source_revision="a" * 40,
            )

        by_phase = {
            phase_id: tuple(case.case_id for case in cases)
            for phase_id, cases in contracts.items()
        }
        self.assertEqual(
            {
                "inventory": ("ios.inventory.generate",),
                "registry-smoke": ("ios.registry.smoke",),
                "cpp-device-equivalents": CPP_CASES,
                "rust-device-runtime": (
                    *RUST_CASES, "ios.rust.production.symbol-isolation"),
            },
            by_phase,
        )
        for phase_id, cases in contracts.items():
            for case in cases:
                with self.subTest(phase=phase_id, case=case.case_id):
                    self.assertIn(
                        case.execution_class, runner.EXECUTION_CLASSES)
                    self.assertTrue(case.command)
                    self.assertGreater(case.timeout_seconds, 0)
                    self.assertTrue(callable(case.evidence_validator))
                    self.assertIsInstance(
                        case.requires_sql_restart_followup, bool)

    def test_commands_reuse_inventory_and_device_suite_entrypoints(self):
        """Drive the established generators instead of duplicating their logic."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            contracts = phases.create_phase_contracts(
                configuration=self.configuration(root),
                suites=runner.PHASE_IDS,
                run_directory=root / "run",
                source_revision="a" * 40,
            )

        inventory = contracts["inventory"][0]
        self.assertIn(
            str(SCRIPT_DIR / "generate_test_inventory.py"), inventory.command)
        for phase_id in (
                "registry-smoke", "cpp-device-equivalents",
                "rust-device-runtime"):
            for case in contracts[phase_id]:
                if case.case_id == "ios.rust.production.symbol-isolation":
                    continue
                with self.subTest(case=case.case_id):
                    self.assertIn(
                        str(SCRIPT_DIR / "run_device_suite.py"), case.command)
                    expected_index = case.command.index("--expected-case") + 1
                    self.assertEqual(
                        case.case_id, case.command[expected_index])
                    self.assertEqual(
                        case.case_id, case.command[case.command.index("--filter") + 1])

    def test_device_execution_prepares_current_test_hook_app_once(self):
        """Build and package current HEAD rather than trusting a stale cache."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_directory = root / "run"
            run_directory.mkdir()
            commands = []

            def execute(command, timeout_seconds):
                """Record commands and synthesize safe validated suite evidence."""
                commands.append((tuple(command), timeout_seconds))
                if str(SCRIPT_DIR / "run_device_suite.py") not in command:
                    return runner.SanitizedProcessResult(0, "", "")
                case_id = command[command.index("--expected-case") + 1]
                output_dir = Path(command[command.index("--output-dir") + 1])
                evidence = output_dir / "device-test-safe-run.jsonl"
                evidence.write_text("{}\n", encoding="utf-8")
                summary = {
                    "run_id": "safe-run",
                    "build_id": "a" * 12,
                    "run_result": 0,
                    "case_results": {case_id: 0},
                }
                return runner.SanitizedProcessResult(
                    0, json.dumps(summary), "")

            with mock.patch.dict(
                    os.environ, self.build_environment(root), clear=False):
                adapters = phases.create_phase_adapters(
                    configuration=self.configuration(root),
                    suites=("registry-smoke",),
                    run_directory=run_directory,
                    source_revision="a" * 40,
                    command_executor=execute,
                )
                adapter = tuple(adapters)[0]
                first = adapter.execute(adapter.cases[0])
                second = adapter.execute(adapter.cases[0])

        self.assertEqual("passed", first.status)
        self.assertEqual("passed", second.status)
        flattened = [command for command, _timeout in commands]
        build_commands = [
            command for command in flattened
            if str(REPOSITORY_ROOT / "build.iphone.sh") in command]
        package_commands = [
            command for command in flattened
            if str(REPOSITORY_ROOT / "deps/ios-build/build_app.py") in command]
        self.assertEqual(1, len(build_commands))
        self.assertEqual(1, len(package_commands))
        self.assertIn("-DSEEKDB_IOS_TEST_HOOKS=ON", build_commands[0])
        self.assertEqual("/usr/bin/env", build_commands[0][0])
        self.assertIn("--deps-prefix", build_commands[0])
        self.assertIn("--headers-prefix", build_commands[0])
        self.assertTrue(any(
            value.startswith("CARGO=") for value in build_commands[0]))
        self.assertTrue(any(
            value.startswith("RUSTUP=") for value in build_commands[0]))
        self.assertTrue(any(
            value.startswith("RUST_TARGET_DIR=")
            for value in build_commands[0]))
        self.assertTrue(any(
            value.startswith("CARGO_HOME=") for value in build_commands[0]))
        self.assertTrue(any(
            value.startswith("RUSTUP_HOME=") for value in build_commands[0]))
        self.assertIn("--test-hooks", package_commands[0])
        self.assertIn("--install", package_commands[0])

    def test_build_inputs_prefer_environment_and_validate_artifacts(self):
        """Explicit portable paths must override an unrelated cached checkout."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            configuration.engine_build.mkdir(parents=True)
            (configuration.engine_build / "CMakeCache.txt").write_text(
                "DEP_DIR:PATH=/another/checkout/deps\n"
                "CARGO:FILEPATH=/another/checkout/cargo\n",
                encoding="utf-8")
            environment = self.build_environment(root)

            inputs = phases.resolve_build_inputs(
                configuration, environment)

        self.assertTrue(all(
            source == "environment"
            for source in inputs.sources.values()))
        self.assertNotIn("another", str(inputs.deps_prefix))

    def test_build_inputs_use_validated_cache_with_nonsecret_sources(self):
        """Cache fallback is explicit and rejects missing target artifacts."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            configuration.engine_build.mkdir(parents=True)
            (configuration.engine_build / "CMakeCache.txt").write_text(
                "DEP_DIR:PATH={}\n"
                "SEEKDB_IOS_HEADER_PREFIX:PATH={}\n"
                "CARGO:FILEPATH={}\n"
                "RUST_TARGET_DIR:PATH={}\n"
                "CMAKE_SYSTEM_NAME:STRING=iOS\n"
                "CMAKE_OSX_SYSROOT:STRING=iphoneos\n"
                "CMAKE_OSX_ARCHITECTURES:STRING=arm64\n".format(
                    environment[phases.DEPS_PREFIX_ENVIRONMENT],
                    environment[phases.HEADERS_PREFIX_ENVIRONMENT],
                    environment["CARGO"],
                    environment[phases.RUST_TARGET_DIR_ENVIRONMENT]),
                encoding="utf-8")

            inputs = phases.resolve_build_inputs(configuration, {})
            (inputs.deps_prefix / "lib/libssl.a").unlink()
            with self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, {})

        self.assertEqual("cmake-cache", inputs.sources["deps_prefix"])
        self.assertEqual("cargo-sibling", inputs.sources["rustup"])
        self.assertEqual("cargo-parent", inputs.sources["cargo_home"])
        self.assertEqual(
            "cargo-home-sibling", inputs.sources["rustup_home"])

    def test_profile_scope_blocks_before_any_build_or_device_command(self):
        """A selected device outside the profile must never reach build/install."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = cli.replace(
                self.configuration(root),
                provisioned_devices=("another-device",),
                profile_certificate_hashes=("A" * 40,),
            )
            commands = []
            preparer = phases.TestAppPreparer(
                configuration,
                lambda command, timeout: commands.append(
                    (command, timeout)))

            result = preparer.ensure()

        self.assertEqual("blocked", result.status)
        self.assertEqual([], commands)

    def test_package_output_mismatch_blocks_before_any_external_command(self):
        """Reject an App path that the fixed package entrypoint cannot produce."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = cli.replace(
                self.configuration(root),
                app_artifact=root / "different/Probe.app",
            )
            commands = []
            preparer = phases.TestAppPreparer(
                configuration,
                lambda command, timeout: commands.append(
                    (command, timeout)))

            result = preparer.ensure()

        self.assertEqual("blocked", result.status)
        self.assertEqual(
            "configured App path does not match the package output",
            result.diagnostic)
        self.assertEqual([], commands)

    def test_profile_certificate_requires_one_local_signing_identity(self):
        """Block before building unless the profile certificate has one key."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = cli.replace(
                self.configuration(root),
                profile_certificate_hashes=("A" * 40,),
            )
            commands = []
            preparer = phases.TestAppPreparer(
                configuration,
                lambda command, timeout: commands.append(
                    (command, timeout)))

            with mock.patch.object(
                    phases.subprocess, "run",
                    return_value=mock.Mock(returncode=0, stdout="")):
                result = preparer.ensure()

        self.assertEqual("blocked", result.status)
        self.assertIn("private key", result.diagnostic)
        self.assertEqual([], commands)

    def test_device_metadata_remains_process_local_on_failure(self):
        """Never copy raw device, bundle, team, or signing values to results."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)

            def fail(_command, _timeout_seconds):
                """Return hostile output containing every process-local token."""
                tokens = " ".join(configuration.redaction_tokens())
                return runner.SanitizedProcessResult(9, tokens, tokens)

            with mock.patch.dict(
                    os.environ, self.build_environment(root), clear=False):
                adapters = phases.create_phase_adapters(
                    configuration=configuration,
                    suites=("registry-smoke",),
                    run_directory=root / "run",
                    source_revision="a" * 40,
                    command_executor=fail,
                )
                adapter = tuple(adapters)[0]
                result = adapter.execute(adapter.cases[0])

        serialized = json.dumps({
            "diagnostic": result.diagnostic,
            "details": result.details,
            "evidence_paths": result.evidence_paths,
        }, sort_keys=True)
        self.assertEqual("failed", result.status)
        for token in configuration.redaction_tokens():
            self.assertNotIn(token, serialized)

    def test_followup_and_rust_production_isolation_are_explicit(self):
        """Mark native suites for SQL/restart and production archive proof."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            contracts = phases.create_phase_contracts(
                configuration=self.configuration(root),
                suites=runner.PHASE_IDS,
                run_directory=root / "run",
                source_revision="a" * 40,
            )

        for phase_id in (
                "registry-smoke", "cpp-device-equivalents",
                "rust-device-runtime"):
            device_cases = [
                case for case in contracts[phase_id]
                if case.execution_class == "device-native"]
            self.assertTrue(device_cases)
            self.assertTrue(all(
                case.requires_sql_restart_followup for case in device_cases))
        production = contracts["rust-device-runtime"][-1]
        self.assertEqual("host-only", production.execution_class)
        self.assertIn("-DSEEKDB_IOS_TEST_HOOKS=OFF", production.command)
        production_build = Path(
            production.command[production.command.index("--build-dir") + 1])
        self.assertNotEqual(
            self.configuration(root).engine_build, production_build)


if __name__ == "__main__":
    unittest.main()
