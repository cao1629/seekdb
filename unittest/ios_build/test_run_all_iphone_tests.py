#!/usr/bin/env python3
"""Tests for the standalone iPhone test command and secure CLI inputs."""

import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import run_all_iphone_tests as cli


UTC = dt.timezone.utc


class CompletedCommand:
    """Provide the captured subprocess fields used by device discovery."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        """Initialize one deterministic command result."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunAllIphoneTestsTest(unittest.TestCase):
    """Verify launcher behavior, lifecycle parsing, and secret boundaries."""

    def test_shell_resolves_repository_root_forwards_arguments_and_exit(self):
        """The thin launcher must exec Python from any working directory."""
        launcher = REPOSITORY_ROOT / "run.iphone.test.sh"
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            log_path = temporary / "argv.json"
            fake_python = temporary / "python"
            fake_python.write_text(
                "#!/bin/sh\n"
                "python3 -c 'import json, os, sys; "
                "json.dump(sys.argv[1:], open(os.environ[\"ARG_LOG\"], \"w\"))' "
                "\"$@\"\n"
                "exit 37\n",
                encoding="utf-8",
            )
            fake_python.chmod(
                fake_python.stat().st_mode | stat.S_IXUSR)
            environment = dict(os.environ)
            environment["PYTHON"] = str(fake_python)
            environment["ARG_LOG"] = str(log_path)

            result = subprocess.run(
                [str(launcher), "--resume", "--suite", "inventory"],
                cwd=temporary,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            forwarded_arguments = json.loads(
                log_path.read_text(encoding="utf-8"))

        self.assertEqual(37, result.returncode)
        self.assertEqual(
            [str(SCRIPT_DIR / "run_all_iphone_tests.py"),
             "--resume", "--suite", "inventory"],
            forwarded_arguments,
        )

    def test_shell_uses_strict_mode_and_exec(self):
        """The launcher should not mask Python failures or leave a shell parent."""
        source = (REPOSITORY_ROOT / "run.iphone.test.sh").read_text(
            encoding="utf-8")
        self.assertIn("set -eu", source)
        self.assertRegex(source, r"(?m)^exec ")

    def test_lifecycle_modes_are_mutually_exclusive(self):
        """Resume and restart must fail during parsing when combined."""
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.parse_args(["--resume", "--restart"])
        self.assertEqual(2, raised.exception.code)

    def test_default_output_root_is_repository_iphone_test(self):
        """An ordinary invocation should use the tracked checkout's output root."""
        options = cli.parse_args([])
        self.assertEqual(REPOSITORY_ROOT / "iphone_test", options.output_root)

    def test_cli_options_override_environment_without_mutating_environment(self):
        """Device and signing values remain process-local and argv takes priority."""
        environment = {
            "SEEKDB_IPHONE_DEVICE": "environment-device",
            "SEEKDB_IPHONE_BUNDLE_ID": "environment.bundle",
            "SEEKDB_IPHONE_TEAM": "ENVTEAM001",
            "SEEKDB_IPHONE_SIGNING_IDENTITY": "environment identity",
        }
        original = dict(environment)
        options = cli.parse_args([
            "--device", "argument-device",
            "--bundle-id", "argument.bundle",
            "--team", "ARGTEAM001",
            "--signing-identity", "argument identity",
        ])

        configuration = cli.resolve_local_configuration(options, environment)

        self.assertEqual("argument-device", configuration.device)
        self.assertEqual("argument.bundle", configuration.bundle_id)
        self.assertEqual("ARGTEAM001", configuration.team)
        self.assertEqual("argument identity", configuration.signing_identity)
        self.assertEqual(original, environment)

    def test_resume_rejects_changed_bundle_and_build_configuration(self):
        """Evidence-affecting local inputs must participate in resume identity."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            base = cli.LocalConfiguration(
                device="device-token",
                bundle_id="first.private.bundle",
                team="TEAMTOKEN1",
                signing_identity="private identity",
                engine_build=Path("/private/build-one"),
                app_artifact=Path("/private/Probe-one.app"),
                test_hooks=True,
            )
            fingerprint = cli.configuration_fingerprint(
                runner_suites=cli.runner.PHASE_IDS,
                configuration=base,
                build_identity="c" * 64,
            )
            selection = cli.state.select_run(
                output_root, cli.state.RunMode.DEFAULT,
                source_commit="a" * 40,
                config_fingerprint=fingerprint,
                now=timestamp,
            )
            selection.close()

            changed_values = (
                (cli.replace(base, bundle_id="second.private.bundle"),
                 "c" * 64),
                (cli.replace(
                    base, engine_build=Path("/private/build-two")),
                 "d" * 64),
                (cli.replace(
                    base, app_artifact=Path("/private/Probe-two.app")),
                 "e" * 64),
            )
            for changed, build_identity in changed_values:
                with self.subTest(configuration=changed):
                    changed_fingerprint = cli.configuration_fingerprint(
                        runner_suites=cli.runner.PHASE_IDS,
                        configuration=changed,
                        build_identity=build_identity,
                    )
                    with self.assertRaises(
                            cli.state.IncompatibleCheckpointError):
                        cli.state.select_run(
                            output_root, cli.state.RunMode.RESUME,
                            source_commit="a" * 40,
                            config_fingerprint=changed_fingerprint,
                            now=timestamp + dt.timedelta(hours=1),
                        )

            serialized = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in output_root.rglob("*") if path.is_file())
            for raw_value in (
                    "device-token", "first.private.bundle", "TEAMTOKEN1",
                    "private identity", "/private/build-one",
                    "/private/Probe-one.app"):
                self.assertNotIn(raw_value, serialized)

    def test_source_and_build_identity_reject_dirty_or_stale_inputs(self):
        """Dirty tracked source and stale runtime archives must fail early."""
        dirty = mock.Mock(returncode=0, stdout=" M source.cpp\n", stderr="")
        with self.assertRaisesRegex(cli.IphoneTestCliError,
                                    "tracked source changes"):
            cli.source_commit(run_command=lambda *_args, **_kwargs: dirty)

        with tempfile.TemporaryDirectory() as temporary_directory:
            engine_build = Path(temporary_directory)
            archive = engine_build / "src/observer/libseekdb_ios_runtime.a"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(
                b"SEEKDB_IOS_ARTIFACT_BUILD_ID=bbbbbbbbbbbb;"
                b"SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled")
            configuration = cli.LocalConfiguration(
                device=None, bundle_id=None, team=None, signing_identity=None,
                engine_build=engine_build,
                app_artifact=engine_build / "Probe.app",
                test_hooks=True,
            )
            with self.assertRaisesRegex(cli.IphoneTestCliError,
                                        "build identity"):
                cli.validate_build_identity(configuration, "a" * 40)

    def test_no_physical_device_is_rejected_without_simulator_fallback(self):
        """A simulator-only list must fail instead of satisfying device selection."""
        with self.assertRaisesRegex(cli.DeviceSelectionError,
                                    "no eligible physical iPhone"):
            cli.select_physical_device(None, [])

    def test_multiple_physical_devices_require_explicit_selection(self):
        """Ambiguous physical-device selection must fail before phase dispatch."""
        devices = [
            cli.PhysicalDevice(
                "first", "iPhone 15", "iOS", "physical", "default",
                "booted", "paired"),
            cli.PhysicalDevice(
                "second", "iPhone 16", "iOS", "physical", "default",
                "booted", "paired"),
        ]
        with self.assertRaisesRegex(cli.DeviceSelectionError,
                                    "multiple eligible physical iPhones"):
            cli.select_physical_device(None, devices)

    def test_requested_device_must_be_an_eligible_physical_device(self):
        """An arbitrary identifier must not bypass the discovered physical list."""
        devices = [
            cli.PhysicalDevice(
                "physical", "iPhone 16", "iOS",
                "physical", "default", "booted", "paired"),
        ]
        with self.assertRaisesRegex(cli.DeviceSelectionError,
                                    "requested physical iPhone is unavailable"):
            cli.select_physical_device("simulator", devices)
        self.assertEqual(
            "physical", cli.select_physical_device("physical", devices).identifier)

    def test_discovery_uses_reality_visibility_boot_and_pairing_state(self):
        """Discovery must use CoreDevice physicality and availability fields."""
        payload = {
            "result": {"devices": [
                {
                    "identifier": "physical",
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "physical"},
                    "connectionProperties": {
                        "tunnelState": "disconnected",
                        "transportType": "wired",
                        "pairingState": "paired"},
                    "visibilityClass": "default",
                    "deviceProperties": {
                        "name": "iPhone 16", "bootState": "booted"},
                },
                {
                    "identifier": "simulator",
                    "deviceProperties": {
                        "name": "iPhone Simulator", "bootState": "booted"},
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "simulated"},
                    "connectionProperties": {"tunnelState": "connected"},
                    "visibilityClass": "simulators",
                },
                {
                    "identifier": "ipad",
                    "deviceProperties": {
                        "name": "iPad", "bootState": "booted"},
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPad",
                        "reality": "physical"},
                    "connectionProperties": {"tunnelState": "disconnected"},
                    "visibilityClass": "default",
                },
                {
                    "identifier": "offline",
                    "deviceProperties": {
                        "name": "iPhone 14", "bootState": "shutdown"},
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "physical"},
                    "connectionProperties": {"tunnelState": "disconnected"},
                    "visibilityClass": "default",
                },
                {
                    "identifier": "unpaired",
                    "deviceProperties": {
                        "name": "iPhone 13", "bootState": "booted"},
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "physical"},
                    "connectionProperties": {"pairingState": "unpaired"},
                    "visibilityClass": "default",
                },
                {
                    "identifier": "missing-pairing",
                    "deviceProperties": {
                        "name": "iPhone 12", "bootState": "booted"},
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "physical"},
                    "connectionProperties": {},
                    "visibilityClass": "default",
                },
            ]},
        }
        commands = []

        def run_command(command, **kwargs):
            """Return device JSON while retaining the exact tested command."""
            commands.append((command, kwargs))
            return CompletedCommand(stdout=json.dumps(payload))

        sensitive_environment = {
            "SEEKDB_IPHONE_DEVICE": "secret-device",
            "SEEKDB_IPHONE_BUNDLE_ID": "secret.bundle",
            "SEEKDB_IPHONE_TEAM": "SECRETTEAM",
            "SEEKDB_IPHONE_SIGNING_IDENTITY": "secret identity",
        }
        with mock.patch.dict(os.environ, sensitive_environment):
            devices = cli.discover_physical_devices(run_command=run_command)

        self.assertEqual(["physical"], [device.identifier for device in devices])
        self.assertEqual(
            ["xcrun", "devicectl", "list", "devices",
             "--quiet", "--json-output", "/dev/stdout"],
            commands[0][0],
        )
        self.assertTrue(commands[0][1]["capture_output"])
        self.assertEqual(
            "/Applications/Xcode.app/Contents/Developer",
            commands[0][1]["env"]["DEVELOPER_DIR"],
        )
        for variable in (
                "SEEKDB_IPHONE_DEVICE", "SEEKDB_IPHONE_BUNDLE_ID",
                "SEEKDB_IPHONE_TEAM", "SEEKDB_IPHONE_SIGNING_IDENTITY"):
            self.assertNotIn(variable, commands[0][1]["env"])

    def test_discovery_accepts_current_properties_without_deprecated_fields(self):
        """CoreDevice's replacement properties must work without old aliases."""
        payload = {"result": {"devices": [{
            "identifier": "modern-physical",
            "name": "iPhone 16",
            "properties": {
                "hardware": {
                    "platform": "iOS",
                    "deviceType": "iPhone",
                    "reality": "physical",
                },
                "state": {
                    "visibilityClass": "default",
                    "bootState": "booted",
                },
                "connection": {
                    "state": "disconnected",
                    "transportType": "wired",
                    "pairingState": "paired",
                },
            },
        }]}}

        devices = cli.discover_physical_devices(
            run_command=lambda _command, **_kwargs: CompletedCommand(
                stdout=json.dumps(payload)))

        self.assertEqual(
            [("modern-physical", "physical", "default", "booted")],
            [(device.identifier, device.reality,
              device.visibility_class, device.boot_state)
             for device in devices],
        )

    def test_dry_run_prints_original_directory_and_serializes_no_unique_values(self):
        """Dry-run state and output must omit every process-local unique value."""
        secrets = (
            "00008110-SECRET-DEVICE", "org.private.bundle",
            "TEAMSECRET", "Apple Development: Private Person",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            stdout = io.StringIO()
            argv = [
                "--dry-run", "--output-root", str(output_root),
                "--device", secrets[0], "--bundle-id", secrets[1],
                "--team", secrets[2], "--signing-identity", secrets[3],
            ]
            physical = cli.PhysicalDevice(
                secrets[0], "iPhone", "iOS",
                "physical", "default", "booted", "paired")
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "validate_build_identity",
                        return_value="c" * 64), \
                    mock.patch.object(
                        cli, "discover_physical_devices",
                        return_value=[physical]):
                status = cli.main(
                    argv, environment={}, stdout=stdout,
                    clock=lambda: dt.datetime(2026, 9, 24, 10, tzinfo=UTC),
                )

            serialized = stdout.getvalue()

        self.assertEqual(0, status)
        self.assertIn(str(output_root / "2026-09-24"), stdout.getvalue())
        self.assertFalse(output_root.exists())
        for secret in secrets:
            self.assertNotIn(secret, serialized)

    def test_dry_run_is_read_only_for_default_resume_and_restart(self):
        """Every dry-run lifecycle mode must leave the tree byte-for-byte intact."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)

        def snapshot(root):
            """Capture direct tree types, modes, and bytes without changing them."""
            if not root.exists():
                return None
            return [
                (str(path.relative_to(root)), path.is_dir(),
                 path.stat().st_mode,
                 b"" if path.is_dir() else path.read_bytes())
                for path in sorted(root.rglob("*"))
            ]

        for lifecycle in ((), ("--resume",), ("--restart",)):
            with self.subTest(lifecycle=lifecycle), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                output_root = Path(temporary_directory) / "iphone_test"
                if lifecycle:
                    checkpoint = cli.state.create_checkpoint(
                        "a" * 40, "b" * 64, timestamp)
                    cli.state.save_checkpoint(
                        output_root, output_root / "2026-09-24", checkpoint)
                before = snapshot(output_root)
                physical = cli.PhysicalDevice(
                    "device", "iPhone", "iOS", "physical", "default",
                    "booted", "paired")
                arguments = [
                    "--dry-run", "--output-root", str(output_root),
                    *lifecycle,
                ]
                with mock.patch.object(
                        cli, "source_commit", return_value="a" * 40), \
                        mock.patch.object(
                            cli, "validate_build_identity",
                            return_value="c" * 64), \
                        mock.patch.object(
                            cli, "configuration_fingerprint",
                            return_value="b" * 64), \
                        mock.patch.object(
                            cli, "discover_physical_devices",
                            return_value=[physical]):
                    status = cli.main(
                        arguments, environment={}, stdout=io.StringIO(),
                        clock=lambda: timestamp)

                self.assertEqual(0, status)
                self.assertEqual(before, snapshot(output_root))

    def test_selected_directory_is_printed_before_device_discovery(self):
        """The original start-date directory must precede external device work."""
        events = []
        run_directory = Path("/tmp/iphone_test/2026-09-20")
        preview = mock.Mock(run_directory=run_directory)
        stdout = io.StringIO()

        class RecordingOutput(io.StringIO):
            """Record when the selected directory becomes visible."""

            def write(self, value):
                """Record output ordering while preserving StringIO behavior."""
                if str(run_directory) in value:
                    events.append("print")
                return super().write(value)

        stdout = RecordingOutput()

        def discover():
            """Record the external action after selected-directory output."""
            events.append("discover")
            return [cli.PhysicalDevice(
                "physical", "iPhone", "iOS",
                "physical", "default", "booted", "paired")]

        with mock.patch.object(cli, "source_commit", return_value="a" * 40), \
                mock.patch.object(
                    cli, "validate_build_identity",
                    return_value="c" * 64), \
                mock.patch.object(cli.state, "preview_run", return_value=preview), \
                mock.patch.object(cli, "discover_physical_devices",
                                  side_effect=discover):
            status = cli.main(["--dry-run"], environment={}, stdout=stdout)

        self.assertEqual(0, status)
        self.assertEqual(["print", "discover"], events)

    def test_environment_configuration_reaches_adapters_and_engine_exit_propagates(self):
        """Environment-only secrets stay in memory while engine status is returned."""
        environment = {
            "SEEKDB_IPHONE_DEVICE": "environment-device",
            "SEEKDB_IPHONE_BUNDLE_ID": "environment.bundle",
            "SEEKDB_IPHONE_TEAM": "ENVTEAM001",
            "SEEKDB_IPHONE_SIGNING_IDENTITY": "environment identity",
        }
        selection = mock.Mock(run_directory=Path("/tmp/iphone_test/2026-09-24"))
        captured = {}

        def load_adapters(configuration, suites):
            """Capture process-local inputs at the future Task 4 boundary."""
            captured["configuration"] = configuration
            captured["suites"] = suites
            return ("adapter",)

        def run_engine(
                output_root, received_selection, adapters, now,
                redaction_tokens):
            """Model engine lock ownership and return a distinctive status."""
            captured["output_root"] = output_root
            captured["selection"] = received_selection
            captured["adapters"] = adapters
            captured["now"] = now()
            captured["redaction_tokens"] = redaction_tokens
            received_selection.close()
            return 7

        physical = cli.PhysicalDevice(
            "environment-device", "iPhone", "iOS",
            "physical", "default", "booted", "paired")
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with mock.patch.object(cli, "source_commit", return_value="a" * 40), \
                mock.patch.object(
                    cli, "validate_build_identity",
                    return_value="c" * 64), \
                mock.patch.object(cli.state, "select_run", return_value=selection), \
                mock.patch.object(cli, "discover_physical_devices",
                                  return_value=[physical]), \
                mock.patch.object(cli, "load_phase_adapters",
                                  side_effect=load_adapters), \
                mock.patch.object(cli.runner, "run_phase_engine",
                                  side_effect=run_engine):
            status = cli.main(
                ["--output-root", "/tmp/iphone_test"],
                environment=environment,
                stdout=io.StringIO(),
                clock=lambda: timestamp,
            )

        self.assertEqual(7, status)
        self.assertEqual(
            cli.LocalConfiguration(
                device="environment-device",
                bundle_id="environment.bundle",
                team="ENVTEAM001",
                signing_identity="environment identity",
                engine_build=cli.DEFAULT_ENGINE_BUILD,
                app_artifact=cli.DEFAULT_APP_ARTIFACT,
                test_hooks=True),
            captured["configuration"],
        )
        self.assertEqual(tuple(cli.runner.PHASE_IDS), captured["suites"])
        self.assertEqual(("adapter",), captured["adapters"])
        self.assertEqual(
            ("environment-device", "environment.bundle", "ENVTEAM001",
             "environment identity"),
            captured["redaction_tokens"],
        )
        selection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
