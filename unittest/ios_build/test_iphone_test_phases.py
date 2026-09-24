#!/usr/bin/env python3
"""Contract tests for standalone iPhone validation phases one through four."""

import contextlib
import datetime as dt
import io
import json
import hashlib
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import iphone_test_phases as phases
import iphone_test_runner as runner
import iphone_test_state as state
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
SQL_RESTART_CASES = phases.SQL_RESTART_CASE_IDS
CARGO_198_CANDIDATES = tuple(sorted(
    (Path.home() / ".rustup/toolchains").glob(
        "1.98.*-aarch64-apple-darwin/bin/cargo")))
CARGO_198 = CARGO_198_CANDIDATES[-1] if CARGO_198_CANDIDATES else None


def _host_macho_executable_bytes() -> bytes:
    """Return one minimal executable macOS ARM64 Mach-O header."""
    build_version = struct.pack("<IIIIII", 0x32, 24, 1, 0, 0, 0)
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", 0x0100000C, 0, 2, 1, len(build_version), 0, 0)
    return header + build_version


class IphoneTestPhasesTest(unittest.TestCase):
    """Require stable metadata and sanitized execution for completed phases."""

    def configuration(self, root):
        """Return process-local device and signing inputs for one test."""
        engine = root / "build_ios_arm64"
        return cli.LocalConfiguration(
            device="private-device-token",
            profile_device="private-profile-udid",
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
            tool.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = \"--version\" ]; then\n"
                f"  echo '{name} 1.0.0'\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8")
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
                "registry-smoke": (
                    "ios.registry.smoke",
                    SQL_RESTART_CASES["registry-smoke"]),
                "cpp-device-equivalents": (
                    *CPP_CASES,
                    SQL_RESTART_CASES["cpp-device-equivalents"]),
                "rust-device-runtime": (
                    *RUST_CASES, "ios.rust.production.symbol-isolation",
                    SQL_RESTART_CASES["rust-device-runtime"]),
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
                if case.case_id in {
                        "ios.rust.production.symbol-isolation",
                        *SQL_RESTART_CASES.values()}:
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
            environment = self.build_environment(root)

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
                    os.environ, environment, clear=False):
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
        self.assertIn(
            f"-DCARGO={Path(environment['CARGO']).resolve()}",
            build_commands[0])
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
        self.assertIn(
            f"RUSTC_WRAPPER={SCRIPT_DIR / 'rustc_lldb_wrapper.py'}",
            build_commands[0])
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

    def test_default_executor_passes_one_validated_xcode_environment(self):
        """Run every phase command with the resolved full-Xcode selection."""
        process = mock.Mock()
        process.stdout = io.StringIO("safe")
        process.stderr = io.StringIO("")
        process.wait.return_value = 0
        command_environment = {
            "PATH": "/usr/bin:/bin",
            "DEVELOPER_DIR": "/validated/Xcode/Contents/Developer",
        }
        with mock.patch.object(
                phases, "validated_xcode_environment",
                return_value=command_environment) as validate, \
                mock.patch.object(
                    phases.subprocess, "Popen", return_value=process) as popen:
            execute = phases._default_executor("safe-run")
            result = execute(("xcrun", "devicectl", "help"), 17)

        validate.assert_called_once_with()
        self.assertEqual(0, result.exit_status)
        self.assertEqual(command_environment, popen.call_args.kwargs["env"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(1, process.wait.call_count)
        wait_timeout = process.wait.call_args.kwargs["timeout"]
        self.assertGreater(wait_timeout, 16)
        self.assertLessEqual(wait_timeout, 17)

    def test_unlock_prompt_is_visible_before_process_completion_only_once(self):
        """Echo only the exact fixed prompt while continuing to capture stderr."""
        prompt = "Unlock the iPhone and keep the screen awake; retrying…"
        visible = threading.Event()
        completed = threading.Event()

        class ObservedTerminal(io.StringIO):
            """Signal when the executor flushes one terminal message."""

            def flush(self):
                """Expose the write to the test before child completion."""
                visible.set()
                return super().flush()

        terminal = ObservedTerminal()
        environment = dict(os.environ)
        with mock.patch.object(
                phases, "validated_xcode_environment",
                return_value=environment):
            execute = phases._default_executor(
                "safe-live", terminal_stream=terminal)

        script = (
            "import sys,time; "
            f"sys.stderr.write({prompt + chr(10)!r}); sys.stderr.flush(); "
            "time.sleep(0.5); raise SystemExit(1)")
        outcome = []

        def run_child():
            """Run the executor while the test observes its live channel."""
            outcome.append(execute((sys.executable, "-c", script), 5))
            completed.set()

        worker = threading.Thread(target=run_child)
        worker.start()
        self.assertTrue(visible.wait(timeout=2))
        self.assertFalse(completed.is_set())
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(f"{prompt}\n", terminal.getvalue())
        self.assertEqual(1, outcome[0].exit_status)
        self.assertIn(prompt, outcome[0].stderr)

    def test_executor_drains_large_dual_streams_without_deadlock(self):
        """Concurrently drain stdout and stderr before bounding captured text."""
        with mock.patch.object(
                phases, "validated_xcode_environment",
                return_value=dict(os.environ)):
            execute = phases._default_executor(
                "safe-large", terminal_stream=io.StringIO())
        script = (
            "import os; "
            "os.write(1, b'x' * 1000000); "
            "os.write(2, b'y' * 1000000)")

        result = execute((sys.executable, "-c", script), 5)

        self.assertEqual(0, result.exit_status)
        self.assertEqual(runner.MAX_PROCESS_OUTPUT_LENGTH, len(result.stdout))
        self.assertEqual(runner.MAX_PROCESS_OUTPUT_LENGTH, len(result.stderr))

    def test_spoofed_prompt_and_token_are_captured_but_never_echoed(self):
        """Do not expose near-match or token-bearing stderr on the live channel."""
        prompt = "Unlock the iPhone and keep the screen awake; retrying…"
        token = "private-live-device-token"
        terminal = io.StringIO()
        runner.register_runtime_redaction_tokens("safe-spoof", (token,))
        try:
            with mock.patch.object(
                    phases, "validated_xcode_environment",
                    return_value=dict(os.environ)):
                execute = phases._default_executor(
                    "safe-spoof", terminal_stream=terminal)
            script = (
                "import sys; "
                f"sys.stderr.write({prompt + ' now' + chr(10)!r}); "
                f"sys.stderr.write({token + chr(10)!r}); "
                "sys.stderr.flush(); raise SystemExit(1)")
            result = execute((sys.executable, "-c", script), 5)
        finally:
            runner.clear_runtime_redaction_tokens("safe-spoof")

        self.assertEqual("", terminal.getvalue())
        self.assertNotIn(token, result.stderr)
        self.assertIn(runner.REDACTED, result.stderr)
        self.assertEqual(
            "standalone phase command failed",
            phases._process_failure_diagnostic(
                result, "safe-spoof", (token,),
                "standalone phase command failed"))

    def test_registered_exact_prompt_token_disables_live_echo(self):
        """Never echo even the fixed prompt when it is a registered token."""
        prompt = "Unlock the iPhone and keep the screen awake; retrying…"
        terminal = io.StringIO()
        runner.register_runtime_redaction_tokens("safe-prompt-token", (prompt,))
        try:
            with mock.patch.object(
                    phases, "validated_xcode_environment",
                    return_value=dict(os.environ)):
                execute = phases._default_executor(
                    "safe-prompt-token", terminal_stream=terminal)
            script = (
                "import sys; "
                f"sys.stderr.write({prompt + chr(10)!r}); sys.stderr.flush()")
            result = execute((sys.executable, "-c", script), 5)
        finally:
            runner.clear_runtime_redaction_tokens("safe-prompt-token")

        self.assertEqual("", terminal.getvalue())
        self.assertNotIn(prompt, result.stderr)
        self.assertIn(runner.REDACTED, result.stderr)

    def test_executor_timeout_terminates_entire_process_group(self):
        """Terminate, then kill and reap a timed-out subprocess group."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child_pid = root / "child.pid"
            with mock.patch.object(
                    phases, "validated_xcode_environment",
                    return_value=dict(os.environ)):
                execute = phases._default_executor(
                    "safe-timeout", terminal_stream=io.StringIO())
            child_script = (
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)")
            parent_script = (
                "import pathlib,signal,subprocess,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
                f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid)); "
                "time.sleep(30)")

            result = execute((sys.executable, "-c", parent_script), 1)
            pid = int(child_pid.read_text())
            status = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "stat="],
                check=False, capture_output=True, text=True)

        self.assertEqual(124, result.exit_status)
        self.assertTrue(status.returncode != 0 or status.stdout.startswith("Z"))

    def test_executor_deadline_includes_pipes_held_after_leader_exit(self):
        """Treat inherited pipes held past the deadline as command timeout."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child_pid = root / "child.pid"
            with mock.patch.object(
                    phases, "validated_xcode_environment",
                    return_value=dict(os.environ)):
                execute = phases._default_executor(
                    "safe-held-pipes", terminal_stream=io.StringIO())
            child_script = (
                "import os,signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "os.write(1,b'child-out'); os.write(2,b'child-err'); "
                "time.sleep(3)")
            parent_script = (
                "import pathlib,subprocess,sys; "
                f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
                f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid))")

            started = time.monotonic()
            result = execute((sys.executable, "-c", parent_script), 1)
            elapsed = time.monotonic() - started
            pid = int(child_pid.read_text())
            status = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "stat="],
                check=False, capture_output=True, text=True)

        self.assertEqual(124, result.exit_status)
        self.assertGreaterEqual(elapsed, 0.8)
        self.assertLess(elapsed, 2.0)
        self.assertTrue(status.returncode != 0 or status.stdout.startswith("Z"))

    def test_executor_keyboard_interrupt_cleans_process_group(self):
        """Terminate and reap the child before propagating KeyboardInterrupt."""
        process = mock.Mock()
        process.pid = 4242
        process.stdout = io.StringIO("")
        process.stderr = io.StringIO("")
        process.poll.return_value = None
        process.wait.side_effect = (KeyboardInterrupt, 130)
        with mock.patch.object(
                phases, "validated_xcode_environment",
                return_value=dict(os.environ)), \
                mock.patch.object(
                    phases.subprocess, "Popen", return_value=process), \
                mock.patch.object(phases.os, "killpg") as killpg:
            execute = phases._default_executor(
                "safe-interrupt", terminal_stream=io.StringIO())
            with self.assertRaises(KeyboardInterrupt):
                execute(("ignored",), 5)

        killpg.assert_any_call(4242, signal.SIGTERM)
        killpg.assert_any_call(4242, signal.SIGKILL)
        self.assertEqual(2, process.wait.call_count)

    def test_executor_sigint_cleans_descendant_held_pipes(self):
        """Bound SIGINT cleanup when a descendant inherits both output pipes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child_pid = root / "child.pid"
            interrupted = root / "interrupted"
            child_script = (
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)")
            command_script = (
                "import os,pathlib,signal,subprocess,sys,time; "
                f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
                f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid)); "
                "time.sleep(0.1); os.kill(os.getppid(),signal.SIGINT); "
                "time.sleep(30)")
            helper_script = (
                "import io,os,pathlib,sys; "
                f"sys.path.insert(0,{str(SCRIPT_DIR)!r}); "
                "import iphone_test_phases as phases; "
                "phases.validated_xcode_environment=lambda:dict(os.environ); "
                "execute=phases._default_executor('sigint-held-pipes',"
                "terminal_stream=io.StringIO()); "
                "command=(sys.executable,'-c'," + repr(command_script) + "); "
                "\ntry:\n execute(command,10)\n"
                "except KeyboardInterrupt:\n "
                f"pathlib.Path({str(interrupted)!r}).write_text('yes')\n")

            started = time.monotonic()
            completed = subprocess.run(
                [sys.executable, "-c", helper_script], check=False,
                capture_output=True, text=True, timeout=5)
            elapsed = time.monotonic() - started
            interrupted_text = interrupted.read_text()
            pid = int(child_pid.read_text())
            status = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "stat="],
                check=False, capture_output=True, text=True)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("yes", interrupted_text)
        self.assertLess(elapsed, 2.0)
        self.assertTrue(status.returncode != 0 or status.stdout.startswith("Z"))

    def test_xcode_environment_preserves_and_validates_explicit_selection(self):
        """Retain one explicit full-Xcode directory only after all probes pass."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            developer = root / "Explicit Xcode/Contents/Developer"
            developer.mkdir(parents=True)
            devicectl = root / "bin/devicectl"
            lldb = root / "bin/lldb"
            for tool in (devicectl, lldb):
                tool.parent.mkdir(parents=True, exist_ok=True)
                tool.write_text("#!/bin/sh\n", encoding="utf-8")
                tool.chmod(0o755)
            sdk = root / "iPhoneOS.sdk"
            sdk.mkdir()
            responses = iter((devicectl, lldb, sdk))
            calls = []

            def run_command(command, **kwargs):
                """Return one validated tool path for each fixed Xcode probe."""
                calls.append((tuple(command), kwargs))
                return mock.Mock(
                    returncode=0, stdout=f"{next(responses)}\n", stderr="")

            resolved = phases.validated_xcode_environment(
                {"DEVELOPER_DIR": str(developer), "KEEP": "value"},
                run_command=run_command)

        self.assertEqual(str(developer), resolved["DEVELOPER_DIR"])
        self.assertEqual("value", resolved["KEEP"])
        self.assertEqual(3, len(calls))
        self.assertTrue(all(
            call[1]["env"]["DEVELOPER_DIR"] == str(developer)
            for call in calls))

    def test_reuse_install_uses_validated_xcode_environment(self):
        """Keep codesign and devicectl install on the same selected Xcode."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            configuration = cli.replace(
                self.configuration(Path(temporary_directory)),
                provisioned_devices=("private-profile-udid",),
            )
            command_environment = {
                "DEVELOPER_DIR": "/validated/Xcode/Contents/Developer"}
            def command_process(*_args, **_kwargs):
                """Return one independent successful captured command."""
                process = mock.Mock()
                process.stdout = io.StringIO("")
                process.stderr = io.StringIO("")
                process.wait.return_value = 0
                return process

            with mock.patch.object(
                    phases, "validated_xcode_environment",
                    return_value=command_environment), \
                    mock.patch.object(
                        phases.subprocess, "Popen",
                        side_effect=command_process) as popen:
                preparer = phases.TestAppPreparer(
                    configuration, phases._default_executor("safe-run"))
                result = preparer.ensure(reuse_build=True)

        self.assertIsNone(result)
        self.assertEqual(2, popen.call_count)
        self.assertIn("codesign", popen.call_args_list[0].args[0][0])
        self.assertIn("devicectl", popen.call_args_list[1].args[0])
        self.assertTrue(all(
            call.kwargs["env"] == command_environment
            for call in popen.call_args_list))

    def test_invalid_xcode_environment_returns_fixed_build_input_code(self):
        """Map Xcode selection failures to the allowlisted build-input stage."""
        with tempfile.TemporaryDirectory() as temporary_directory, \
                mock.patch.object(
                    phases, "validated_xcode_environment",
                    side_effect=phases.BuildReadinessError(
                        "untrusted local detail")):
            issue = phases.prepare_test_app(
                configuration=self.configuration(Path(temporary_directory)),
                suites=("registry-smoke",),
                source_revision="a" * 40,
                run_id="safe-run",
                reuse_build=True,
            )

        self.assertEqual("build-inputs", issue)

    def test_legacy_raw_build_scripts_move_to_run_scoped_quarantine(self):
        """Migrate only exact raw Cargo host launchers and remain idempotent."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = (Path(temporary_directory) / "rust-target").resolve()
            raw = (target / "ios-device-test/build"
                   / "sql-nio-deadbeef12345678/build-script-build")
            raw.parent.mkdir(parents=True)
            raw.write_bytes(_host_macho_executable_bytes())
            raw.chmod(0o755)
            hashed = raw.with_name(
                "build_script_build-deadbeef12345678")
            hashed.write_bytes(_host_macho_executable_bytes())
            hashed.chmod(0o755)
            stale_real = hashed.with_name(f"{hashed.name}.real")
            stale_real.write_bytes(_host_macho_executable_bytes())
            stale_real.chmod(0o755)
            fingerprint = (target / "ios-device-test/.fingerprint"
                           / "sql-nio-deadbeef12345678")
            fingerprint.mkdir(parents=True)
            (fingerprint / "build-script-build.json").write_text(
                "stale", encoding="utf-8")
            wrapped = (target / "release/build"
                       / "serde-feedface12345678/build-script-build")
            wrapped.parent.mkdir(parents=True)
            wrapped_real = wrapped.with_name(
                "build_script_build-feedface12345678.real")
            wrapped_real.write_bytes(_host_macho_executable_bytes())
            wrapped_real.chmod(0o755)
            launcher_source = phases.rustc_lldb_wrapper._launcher_source(
                wrapped_real)
            wrapped.write_text(launcher_source, encoding="utf-8")
            wrapped.chmod(0o755)
            wrapped_hashed = wrapped.with_name(
                "build_script_build-feedface12345678")
            wrapped_hashed.write_text(launcher_source, encoding="utf-8")
            wrapped_hashed.chmod(0o755)
            device_binary = (
                target / "aarch64-apple-ios/release/deps/build-script-build")
            device_binary.parent.mkdir(parents=True)
            device_binary.write_bytes(_host_macho_executable_bytes())
            device_binary.chmod(0o755)
            unknown = (target / "release/build/not-a-cargo-hash"
                       / "build-script-build")
            unknown.parent.mkdir(parents=True)
            unknown.write_bytes(_host_macho_executable_bytes())
            unknown.chmod(0o755)

            migrated = phases.migrate_legacy_raw_build_scripts(
                target, "safe-run")
            second = phases.migrate_legacy_raw_build_scripts(
                target, "safe-run")
            quarantine = target / phases.LEGACY_QUARANTINE_DIRECTORY
            quarantined = tuple(
                path.relative_to(quarantine)
                for path in quarantine.rglob("*") if path.is_file())
            unit_sources_exist = any(
                path.exists() for path in (
                    raw, hashed, stale_real, fingerprint))
            wrapped_pair_exists = (
                wrapped.exists() and wrapped_hashed.exists()
                and wrapped_real.exists())
            device_binary_exists = device_binary.exists()
            unknown_exists = unknown.exists()

        self.assertEqual(1, migrated)
        self.assertEqual(0, second)
        self.assertFalse(unit_sources_exist)
        self.assertEqual(4, len(quarantined))
        self.assertTrue(wrapped_pair_exists)
        self.assertTrue(device_binary_exists)
        self.assertTrue(unknown_exists)

    def test_legacy_raw_build_script_symlink_and_collision_are_rejected(self):
        """Fail safely on candidate symlinks and occupied quarantine paths."""
        for anomaly in ("symlink", "profile-symlink", "collision"):
            with self.subTest(anomaly=anomaly), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                target = (
                    Path(temporary_directory) / "rust-target").resolve()
                raw = (target / "release/build"
                       / "sql-nio-deadbeef12345678/build-script-build")
                if anomaly == "profile-symlink":
                    outside_profile = target.parent / "outside-profile"
                    outside_raw = (outside_profile / "build"
                                   / "sql-nio-deadbeef12345678"
                                   / "build-script-build")
                    outside_raw.parent.mkdir(parents=True)
                    outside_raw.write_bytes(_host_macho_executable_bytes())
                    outside_raw.chmod(0o755)
                    target.mkdir()
                    (target / "release").symlink_to(outside_profile)
                else:
                    raw.parent.mkdir(parents=True)
                if anomaly == "symlink":
                    outside = target.parent / "outside"
                    outside.write_bytes(_host_macho_executable_bytes())
                    raw.symlink_to(outside)
                elif anomaly == "collision":
                    raw.write_bytes(_host_macho_executable_bytes())
                    raw.chmod(0o755)
                    destination = phases.legacy_quarantine_path(
                        target, "safe-run", raw)
                    destination.parent.mkdir(parents=True)
                    destination.write_bytes(b"occupied")

                with self.assertRaises(phases.BuildReadinessError):
                    phases.migrate_legacy_raw_build_scripts(
                        target, "safe-run")

                self.assertTrue(os.path.lexists(
                    outside_raw if anomaly == "profile-symlink" else raw))

    def test_partial_legacy_unit_migration_is_recovered(self):
        """Complete a durable transaction after interruption between moves."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = (Path(temporary_directory) / "rust-target").resolve()
            final = (target / "release/build"
                     / "sql-nio-deadbeef12345678/build-script-build")
            final.parent.mkdir(parents=True)
            final.write_bytes(_host_macho_executable_bytes())
            final.chmod(0o755)
            hashed = final.with_name(
                "build_script_build-deadbeef12345678")
            hashed.write_bytes(_host_macho_executable_bytes())
            hashed.chmod(0o755)
            real_replace = os.replace
            calls = 0

            def interrupt(source, destination):
                """Interrupt the second atomic move after one durable move."""
                nonlocal calls
                if Path(source).name.startswith("build"):
                    calls += 1
                    if calls == 2:
                        raise OSError("simulated interruption")
                real_replace(source, destination)

            with mock.patch.object(phases.os, "replace", side_effect=interrupt), \
                    self.assertRaises(OSError):
                phases.migrate_legacy_raw_build_scripts(
                    target, "safe-run")

            recovered = phases.migrate_legacy_raw_build_scripts(
                target, "next-run")
            sources_exist = final.exists() or hashed.exists()

        self.assertEqual(0, recovered)
        self.assertFalse(sources_exist)

    def test_wrapped_unit_real_must_match_the_crate_unit_hash(self):
        """Never accept launchers bound to another Cargo unit's real output."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = (Path(temporary_directory) / "rust-target").resolve()
            crate = target / "release/build/pkg-deadbeef12345678"
            crate.mkdir(parents=True)
            final = crate / "build-script-build"
            hashed = crate / "build_script_build-deadbeef12345678"
            foreign_real = crate / "build_script_build-feedface12345678.real"
            foreign_real.write_bytes(_host_macho_executable_bytes())
            foreign_real.chmod(0o755)
            launcher = phases.rustc_lldb_wrapper._launcher_source(
                foreign_real)
            for path in (final, hashed):
                path.write_text(launcher, encoding="utf-8")
                path.chmod(0o755)

            migrated = phases.migrate_legacy_raw_build_scripts(
                target, "mismatch-run")
            final_exists = final.exists()
            hashed_exists = hashed.exists()
            foreign_real_exists = foreign_real.exists()

        self.assertEqual(1, migrated)
        self.assertFalse(final_exists)
        self.assertFalse(hashed_exists)
        self.assertTrue(foreign_real_exists)

    @unittest.skipUnless(
        sys.platform == "darwin" and platform.machine() == "arm64"
        and CARGO_198 is not None,
        "requires the local macOS Cargo 1.98 toolchain")
    def test_real_cargo_recompiles_migrated_build_script_through_wrapper(self):
        """Force Cargo 1.98 to rerun rustc and produce a tracked launcher."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            project = root / "probe"
            (project / "src").mkdir(parents=True)
            (project / "Cargo.toml").write_text(
                "[package]\n"
                "name = \"legacy-cache-probe\"\n"
                "version = \"0.1.0\"\n"
                "edition = \"2021\"\n"
                "build = \"build.rs\"\n",
                encoding="utf-8")
            (project / "src/lib.rs").write_text(
                "pub fn value() -> u8 { 1 }\n", encoding="utf-8")
            (project / "build.rs").write_text(
                "fn main() { println!(\"cargo:rerun-if-changed=build.rs\"); }\n",
                encoding="utf-8")
            target = root / "target"
            cargo_home = root / "cargo-home"
            cargo_home.mkdir()
            environment = dict(os.environ)
            environment.update({
                "CARGO_HOME": str(cargo_home),
                "CARGO_TARGET_DIR": str(target),
                "PATH": os.pathsep.join((
                    str(CARGO_198.parent), environment.get("PATH", ""))),
                "RUSTC": str(CARGO_198.with_name("rustc")),
            })
            command = [str(CARGO_198), "build", "-vv", "--offline"]
            first = subprocess.run(
                command, cwd=project, env=environment, check=False,
                capture_output=True, text=True, timeout=120)
            self.assertEqual(0, first.returncode, first.stderr)
            units = tuple((target / "debug/build").glob(
                "legacy-cache-probe-*/build-script-build"))
            self.assertEqual(1, len(units))
            raw_final = units[0]
            self.assertTrue(
                phases.rustc_lldb_wrapper._is_host_macho_executable(
                    raw_final))

            migrated = phases.migrate_legacy_raw_build_scripts(
                target, "cargo-integration")
            environment["RUSTC_WRAPPER"] = str(phases.RUSTC_WRAPPER)
            second = subprocess.run(
                command, cwd=project, env=environment, check=False,
                capture_output=True, text=True, timeout=120)
            self.assertEqual(0, second.returncode, second.stderr)
            rebuilt_units = tuple((target / "debug/build").glob(
                "legacy-cache-probe-*/build-script-build"))
            self.assertEqual(1, len(rebuilt_units))
            final = rebuilt_units[0]
            unit_hash = final.parent.name.rsplit("-", 1)[1]
            hashed = final.with_name(f"build_script_build-{unit_hash}")
            final_real = phases._tracked_launcher_real(final)
            hashed_real = phases._tracked_launcher_real(hashed)

        self.assertEqual(1, migrated)
        self.assertIn(str(phases.RUSTC_WRAPPER), second.stderr)
        self.assertEqual(final_real, hashed_real)
        self.assertTrue(final_real.name.endswith(".real"))

    def test_preparer_migrates_legacy_cache_before_build(self):
        """Remove raw cached launchers before invoking the locked build step."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self.build_environment(root)
            target = Path(
                environment[phases.RUST_TARGET_DIR_ENVIRONMENT]).resolve()
            (target / "aarch64-apple-ios").mkdir()
            raw = (target / "ios-device-test/build"
                   / "sql-nio-deadbeef12345678/build-script-build")
            raw.parent.mkdir(parents=True)
            raw.write_bytes(_host_macho_executable_bytes())
            raw.chmod(0o755)
            observed_raw_state = []

            def execute(_command, _timeout):
                """Record whether the raw legacy launcher reached the build."""
                observed_raw_state.append(raw.exists())
                return runner.SanitizedProcessResult(0, "", "")

            with mock.patch.dict(os.environ, environment, clear=False):
                preparer = phases.TestAppPreparer(
                    self.configuration(root), execute, run_id="safe-run")
                result = preparer.ensure()

            sources = preparer.build_input_sources

        self.assertIsNone(result)
        self.assertEqual([False, False], observed_raw_state)
        self.assertEqual(
            "migrated-1", sources["legacy_build_script_cache"])

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
                    environment["RUSTUP"],
                    environment[phases.RUST_TARGET_DIR_ENVIRONMENT]),
                encoding="utf-8")

            inputs = phases.resolve_build_inputs(configuration, {})
            (inputs.deps_prefix / "lib/libssl.a").unlink()
            with self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, {})

        self.assertEqual("cmake-cache", inputs.sources["deps_prefix"])
        self.assertEqual(
            Path(environment["CARGO"]).resolve(), inputs.cargo)
        self.assertEqual("cargo-sibling", inputs.sources["cargo"])
        self.assertEqual("cargo-sibling", inputs.sources["rustup"])
        self.assertEqual("cargo-parent", inputs.sources["cargo_home"])
        self.assertEqual(
            "cargo-home-sibling", inputs.sources["rustup_home"])

    def test_cache_rustup_as_cargo_without_cargo_sibling_is_rejected(self):
        """Never execute rustup in Cargo's command role."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            Path(environment["CARGO"]).unlink()
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
                    environment["RUSTUP"],
                    environment[phases.RUST_TARGET_DIR_ENVIRONMENT]),
                encoding="utf-8")

            with self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, {})

    def test_explicit_cargo_and_rustup_roles_cannot_be_mixed(self):
        """Validate tool identity instead of accepting any executable path."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            environment["RUSTUP"] = environment["CARGO"]

            with self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, environment)

    def test_rustup_managed_shims_are_probed_with_resolved_homes_and_path(self):
        """Probe rustup proxies only after injecting their resolved context."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            cargo_home = Path(environment["CARGO_HOME"]).resolve()
            rustup_home = Path(environment["RUSTUP_HOME"]).resolve()
            tools = cargo_home / "bin"
            secret = "private-probe-output"
            for name in ("cargo", "rustup"):
                tool = tools / name
                tool.write_text(
                    "#!/bin/sh\n"
                    f"[ \"$CARGO_HOME\" = \"{cargo_home}\" ] || "
                    f"{{ echo '{secret}' >&2; exit 9; }}\n"
                    f"[ \"$RUSTUP_HOME\" = \"{rustup_home}\" ] || "
                    f"{{ echo '{secret}' >&2; exit 9; }}\n"
                    f"case \"$PATH\" in \"{tools}\":*) ;; *) "
                    f"echo '{secret}' >&2; exit 9;; esac\n"
                    f"echo '{name} 1.0.0'\n",
                    encoding="utf-8")
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
                    environment["RUSTUP"],
                    environment[phases.RUST_TARGET_DIR_ENVIRONMENT]),
                encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                inputs = phases.resolve_build_inputs(configuration, {})

        self.assertEqual(tools / "cargo", inputs.cargo)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())

    def test_rustup_managed_shim_with_wrong_home_is_rejected_silently(self):
        """Reject an unusable managed tool context without exposing output."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            expected_home = Path(environment["RUSTUP_HOME"]).resolve()
            secret = "private-probe-output"
            for name in ("cargo", "rustup"):
                Path(environment[name.upper()]).write_text(
                    "#!/bin/sh\n"
                    f"[ \"$RUSTUP_HOME\" = \"{expected_home}\" ] || "
                    f"{{ echo '{secret}' >&2; exit 9; }}\n"
                    f"echo '{name} 1.0.0'\n",
                    encoding="utf-8")
            wrong_home = root / "wrong-rustup-home"
            wrong_home.mkdir()
            environment["RUSTUP_HOME"] = str(wrong_home)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr), \
                    self.assertRaises(phases.BuildReadinessError) as error:
                phases.resolve_build_inputs(configuration, environment)

        serialized = stdout.getvalue() + stderr.getvalue() + str(error.exception)
        self.assertNotIn(secret, serialized)

    def test_preparation_failure_diagnostics_map_only_to_fixed_codes(self):
        """Translate known internal stages without exposing arbitrary details."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            configuration = self.configuration(Path(temporary_directory))
            expected = {
                "current-HEAD iOS engine build failed": "build-failed",
                "existing App signature validation failed": "sign-failed",
                "current-HEAD App signing or installation failed": (
                    "sign-install-failed"),
                "current-HEAD App installation failed": "install-failed",
            }
            for diagnostic, code in expected.items():
                with self.subTest(diagnostic=diagnostic), mock.patch.object(
                        phases.TestAppPreparer, "ensure",
                        return_value=runner.CaseResult.failed(
                            category="infrastructure",
                            diagnostic=diagnostic,
                            exit_status=1,
                            retry_safe=False,
                            clean_state=False)):
                    self.assertEqual(code, phases.prepare_test_app(
                        configuration=configuration,
                        suites=("registry-smoke",),
                        source_revision="a" * 40,
                        run_id="safe-run"))

    def test_missing_lldb_blocks_before_the_build_command(self):
        """Treat the tracked wrapper runtime as a fixed build prerequisite."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)

            with mock.patch.object(
                    phases, "_lldb_is_available", return_value=False), \
                    self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, environment)

    def test_explicit_fresh_rust_target_is_valid_but_simulator_cache_is_not(self):
        """Allow Cargo to create a fresh target and reject simulator-only state."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            environment = self.build_environment(root)
            fresh_target = root / "fresh-rust-target"
            environment[phases.RUST_TARGET_DIR_ENVIRONMENT] = str(fresh_target)

            inputs = phases.resolve_build_inputs(configuration, environment)
            fresh_target.mkdir()
            (fresh_target / "aarch64-apple-ios-sim").mkdir()
            with self.assertRaises(phases.BuildReadinessError):
                phases.resolve_build_inputs(configuration, environment)

        self.assertEqual(fresh_target.resolve(), inputs.rust_target_dir)

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

    def test_reused_app_validates_signature_and_installs_for_selected_device(self):
        """Reuse build bytes only after profile/key, signature, and install checks."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = cli.replace(
                self.configuration(root),
                provisioned_devices=("private-profile-udid",),
                profile_certificate_hashes=("A" * 40,),
            )
            commands = []

            def execute(command, timeout):
                """Record sanitized external validation commands."""
                commands.append((tuple(command), timeout))
                return runner.SanitizedProcessResult(0, "", "")

            preparer = phases.TestAppPreparer(configuration, execute)
            with mock.patch.object(
                    phases.subprocess, "run",
                    return_value=mock.Mock(
                        returncode=0, stdout=f'1) {"A" * 40}\n')):
                result = preparer.ensure(reuse_build=True)

        self.assertIsNone(result)
        self.assertEqual("/usr/bin/codesign", commands[0][0][0])
        self.assertIn("devicectl", commands[1][0])
        self.assertIn("private-device-token", commands[1][0])
        self.assertNotIn("private-profile-udid", commands[1][0])
        self.assertFalse(any(
            str(REPOSITORY_ROOT / "build.iphone.sh") in command
            for command, _timeout in commands))

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

    def test_device_launch_fixed_diagnostic_survives_secondary_redaction(self):
        """Persist only the safe launch category from captured process output."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            fixed = "device launch failed: iPhone is disconnected"

            for stream_name in ("stderr", "stdout"):
                with self.subTest(stream=stream_name):
                    def fail(command, _timeout_seconds):
                        """Return one fixed line plus hostile process-local data."""
                        if str(phases.DEVICE_SUITE_SCRIPT) not in command:
                            return runner.SanitizedProcessResult(0, "", "")
                        values = {
                            "stdout": "", "stderr": "",
                        }
                        values[stream_name] = (
                            f"{fixed}\n{configuration.device}")
                        return runner.SanitizedProcessResult(
                            1, values["stdout"], values["stderr"])

                    adapters = phases.create_phase_adapters(
                        configuration=configuration,
                        suites=("registry-smoke",),
                        run_directory=root / f"run-{stream_name}",
                        source_revision="a" * 40,
                        run_id=f"safe-{stream_name}",
                        command_executor=fail,
                        test_app_prepared=True,
                    )
                    adapter = tuple(adapters)[0]
                    result = adapter.execute(adapter.cases[0])

                    self.assertEqual("failed", result.status)
                    self.assertEqual("infrastructure", result.category)
                    self.assertFalse(result.retry_safe)
                    self.assertFalse(result.clean_state)
                    self.assertEqual(fixed, result.diagnostic)
                    self.assertNotIn(configuration.device, json.dumps({
                        "diagnostic": result.diagnostic,
                        "details": result.details,
                    }))

    def test_fixed_process_diagnostic_is_safe_in_checkpoint_and_failure(self):
        """Persist the allowlisted category without adjacent hostile output."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_root = root / "output"
            run_id = "safe-diagnostic-run"
            fixed = "device launch failed: iPhone remained locked"
            private_token = "private-diagnostic-token"
            selection = state.select_run(
                output_root,
                mode=state.RunMode.DEFAULT,
                source_commit="a" * 40,
                config_fingerprint="b" * 64,
                now=dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc),
            )
            adapters = phases.create_phase_adapters(
                configuration=self.configuration(root),
                suites=("inventory",),
                run_directory=selection.run_directory,
                source_revision="a" * 40,
                run_id=run_id,
                command_executor=lambda _command, _timeout: (
                    runner.SanitizedProcessResult(
                        1, "", f"{fixed}\n{private_token}")),
            )

            exit_status = runner.run_phase_engine(
                output_root, selection, adapters,
                now=lambda: dt.datetime(
                    2026, 9, 25, tzinfo=dt.timezone.utc),
                redaction_tokens=(private_token,),
                phase_ids=("inventory",),
            )
            checkpoint = state.load_checkpoint(
                output_root, selection.run_directory)
            failure = next(selection.run_directory.glob("failure-*.json"))
            serialized = json.dumps(checkpoint) + failure.read_text()

        self.assertEqual(1, exit_status)
        self.assertEqual(
            fixed, checkpoint["phases"][0]["cases"][0]["diagnostic"])
        self.assertNotIn(private_token, serialized)

    def test_production_isolation_failure_keeps_only_fixed_build_diagnostic(self):
        """Do not persist production build logs when its command exits nonzero."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            private_log = "private-production-build-log"

            def execute(command, _timeout_seconds):
                """Fail only the hook-off production isolation build."""
                if "-DSEEKDB_IOS_TEST_HOOKS=OFF" in command:
                    return runner.SanitizedProcessResult(
                        2, "", private_log)
                return runner.SanitizedProcessResult(0, "", "")

            with mock.patch.dict(
                    os.environ, self.build_environment(root), clear=False):
                adapters = phases.create_phase_adapters(
                    configuration=configuration,
                    suites=("rust-device-runtime",),
                    run_directory=root / "run",
                    source_revision="a" * 40,
                    run_id="safe-production",
                    command_executor=execute,
                    test_app_prepared=True,
                )
                adapter = tuple(adapters)[0]
                production = next(
                    case for case in adapter.cases
                    if case.case_id == phases.PRODUCTION_ISOLATION_CASE_ID)
                result = adapter.execute(production)

        self.assertEqual("failed", result.status)
        self.assertEqual("infrastructure", result.category)
        self.assertEqual("production isolation build failed", result.diagnostic)
        self.assertNotIn(private_log, result.diagnostic)

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
                if (case.execution_class == "device-native"
                    and case.case_id not in SQL_RESTART_CASES.values())]
            self.assertTrue(device_cases)
            self.assertTrue(all(
                case.requires_sql_restart_followup for case in device_cases))

        for phase_id in (
                "registry-smoke", "cpp-device-equivalents",
                "rust-device-runtime"):
            sql_gate = contracts[phase_id][-1]
            self.assertEqual(SQL_RESTART_CASES[phase_id], sql_gate.case_id)
            self.assertIn("--sql-restart", sql_gate.command)
            self.assertFalse(sql_gate.requires_sql_restart_followup)
            mode = sql_gate.command[
                sql_gate.command.index("--expected-hook-mode") + 1]
            self.assertEqual(
                "disabled" if phase_id == "rust-device-runtime"
                else "enabled", mode)
        production = contracts["rust-device-runtime"][-2]
        self.assertEqual("host-only", production.execution_class)
        self.assertIn("-DSEEKDB_IOS_TEST_HOOKS=OFF", production.command)
        production_build = Path(
            production.command[production.command.index("--build-dir") + 1])
        self.assertNotEqual(
            self.configuration(root).engine_build, production_build)

    def test_device_outer_timeout_exceeds_one_shared_inner_deadline(self):
        """The process timeout must leave margin around the shared device deadline."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            contracts = phases.create_phase_contracts(
                configuration=self.configuration(root),
                suites=("registry-smoke",),
                run_directory=root / "run",
                source_revision="a" * 40,
            )
        case = contracts["registry-smoke"][0]
        inner = int(case.command[case.command.index("--timeout") + 1])
        self.assertGreaterEqual(case.timeout_seconds, inner + 60)

    def test_sql_gate_metadata_binds_run_build_device_data_and_hook_mode(self):
        """Reject reusable JSONL whose privacy metadata belongs to another run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            configuration = self.configuration(root)
            prefix = "registry_smoke-run-1"
            data_name = "standalone-registry_smoke-run-1"
            validator = phases._sql_restart_validator(
                root, prefix, configuration, "a" * 40, "run-1",
                data_name, "enabled")
            names = []
            for previous_runs, suffix in enumerate(("first", "restart")):
                evidence = root / f"evidence-{prefix}-{suffix}.jsonl"
                evidence.write_text(
                    '{"complete":true,"result":0}\n', encoding="utf-8")
                metadata = {
                    "schema_version": 1,
                    "runner_run_id": "run-1",
                    "build_id": "a" * 12,
                    "device_hash": hashlib.sha256(
                        b"private-device-token").hexdigest(),
                    "data_name": data_name,
                    "hook_mode": "enabled",
                    "previous_runs": previous_runs,
                    "round_id": f"round-{previous_runs}",
                    "evidence_sha256": hashlib.sha256(
                        evidence.read_bytes()).hexdigest(),
                }
                metadata_path = evidence.with_name(
                    evidence.name + ".meta.json")
                metadata_path.write_text(
                    json.dumps(metadata), encoding="utf-8")
                names.extend((evidence.name, metadata_path.name))
            process = runner.SanitizedProcessResult(
                0, json.dumps({
                    "run_result": 0,
                    "first_previous_runs": 0,
                    "second_previous_runs": 1,
                }), "")

            self.assertEqual(tuple(names), validator(process))
            first_metadata = root / f"evidence-{prefix}-first.jsonl.meta.json"
            changed = json.loads(first_metadata.read_text())
            changed["runner_run_id"] = "old-run"
            first_metadata.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(phases.PhaseEvidenceError):
                validator(process)


if __name__ == "__main__":
    unittest.main()
