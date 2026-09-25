#!/usr/bin/env python3
"""Tests for the standalone iPhone test command and secure CLI inputs."""

import contextlib
import datetime as dt
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import run_all_iphone_tests as cli


UTC = dt.timezone.utc


def _try_preparation_lock(output_root, run_directory, result_queue):
    """Report whether a second process can enter the preparation lock."""
    try:
        with cli.state.RunLock(Path(output_root), Path(run_directory)):
            result_queue.put("entered-prepare")
    except cli.state.RunLockedError:
        result_queue.put("locked")


class CompletedCommand:
    """Provide the captured subprocess fields used by device discovery."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        """Initialize one deterministic command result."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunAllIphoneTestsTest(unittest.TestCase):
    """Verify launcher behavior, lifecycle parsing, and secret boundaries."""

    def setUp(self):
        """Keep unrelated CLI tests independent from external host evidence."""
        self.host_gate = mock.patch.object(
            cli, "validate_mysqltest_host_gate", return_value=None)
        self.host_gate.start()
        self.addCleanup(self.host_gate.stop)

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

    def test_unique_existing_profile_infers_process_local_signing_defaults(self):
        """Reuse one valid local profile without serializing its unique values."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app = root / "Probe.app"
            app.mkdir()
            with (app / "Info.plist").open("wb") as plist:
                plistlib.dump({
                    "CFBundleIdentifier": "org.private.probe",
                    "CFBundleExecutable": "Probe",
                }, plist)
            profile = {
                "TeamIdentifier": ["TEAMTOKEN1"],
                "Entitlements": {
                    "application-identifier":
                        "TEAMTOKEN1.org.private.probe"},
                "DeveloperCertificates": [b"certificate-der"],
                "ProvisionedDevices": ["private-device-token"],
                "ExpirationDate": dt.datetime(
                    2030, 1, 1, tzinfo=dt.timezone.utc),
                "Platform": ["iOS"],
            }
            profile_xml = plistlib.dumps(profile, fmt=plistlib.FMT_XML)
            (app / "embedded.mobileprovision").write_bytes(
                b"cms-prefix" + profile_xml + b"cms-suffix")
            configuration = cli.LocalConfiguration(
                device=None, bundle_id=None, team=None,
                signing_identity=None, engine_build=root / "build",
                app_artifact=app, test_hooks=True)

            inferred = cli.infer_signing_configuration(configuration)

        certificate_hash = hashlib.sha1(
            b"certificate-der").hexdigest().upper()
        self.assertEqual("org.private.probe", inferred.bundle_id)
        self.assertEqual("TEAMTOKEN1", inferred.team)
        self.assertEqual(certificate_hash, inferred.signing_identity)
        self.assertEqual(
            ("private-device-token",), inferred.provisioned_devices)
        self.assertIn(certificate_hash, inferred.redaction_tokens())

    def test_ambiguous_existing_profile_requires_explicit_configuration(self):
        """Never guess among multiple profile teams or developer certificates."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app = root / "Probe.app"
            app.mkdir()
            with (app / "Info.plist").open("wb") as plist:
                plistlib.dump({
                    "CFBundleIdentifier": "org.private.probe"}, plist)
            profile = {
                "TeamIdentifier": ["TEAMTOKEN1", "TEAMTOKEN2"],
                "Entitlements": {
                    "application-identifier":
                        "TEAMTOKEN1.org.private.probe"},
                "DeveloperCertificates": [b"first", b"second"],
                "ProvisionedDevices": ["private-device-token"],
                "ExpirationDate": dt.datetime(
                    2030, 1, 1, tzinfo=dt.timezone.utc),
                "Platform": ["iOS"],
            }
            (app / "embedded.mobileprovision").write_bytes(
                plistlib.dumps(profile, fmt=plistlib.FMT_XML))
            configuration = cli.LocalConfiguration(
                device=None, bundle_id=None, team=None,
                signing_identity=None, engine_build=root / "build",
                app_artifact=app, test_hooks=True)

            with self.assertRaisesRegex(
                    cli.IphoneTestCliError, "not uniquely consistent"):
                cli.infer_signing_configuration(configuration)

    def test_resume_rejects_changed_bundle_and_build_configuration(self):
        """Evidence-affecting local inputs must participate in resume identity."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            base = cli.LocalConfiguration(
                device="device-token",
                profile_device="profile-udid",
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
                (cli.replace(base, device="different-device-token"),
                 "c" * 64),
                (cli.replace(base, profile_device="different-profile-udid"),
                 "c" * 64),
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
                    "device-token", "different-device-token",
                    "profile-udid", "different-profile-udid",
                    "first.private.bundle", "TEAMTOKEN1",
                    "private identity", "/private/build-one",
                    "/private/Probe-one.app"):
                self.assertNotIn(raw_value, serialized)

    def test_inventory_only_needs_no_device_signing_or_app_preparation(self):
        """A host-only inventory run must not touch physical-device state."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            selection = mock.Mock()
            selection.run_directory = output_root / "2026-09-24"
            selection.checkpoint = {"run_id": "safe-run"}
            selection.close = mock.Mock()
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "discover_physical_devices") as discover, \
                    mock.patch.object(
                        cli, "prepare_phase_artifacts") as prepare, \
                    mock.patch.object(
                        cli.state, "select_run", return_value=selection), \
                    mock.patch.object(
                        cli, "load_phase_adapters", return_value=()), \
                    mock.patch.object(
                        cli.runner, "run_phase_engine", return_value=0):
                status = cli.main([
                    "--output-root", str(output_root),
                    "--suite", "inventory",
                ], environment={}, stdout=io.StringIO(),
                    clock=lambda: timestamp)

        self.assertEqual(0, status)
        discover.assert_not_called()
        prepare.assert_not_called()

    def test_valid_artifact_still_runs_per_invocation_install_preparation(self):
        """A reusable build must still validate signing and install for the run."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            physical = cli.PhysicalDevice(
                "device", "iPhone", "iOS", "physical", "default",
                "booted", "paired", "profile-device")
            selection = mock.Mock()
            selection.run_directory = output_root / "2026-09-24"
            selection.checkpoint = {"run_id": "safe-run"}
            selection.close = mock.Mock()
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "validate_build_identity", return_value="c" * 64), \
                    mock.patch.object(
                        cli, "discover_physical_devices",
                        return_value=[physical]), \
                    mock.patch.object(
                        cli, "prepare_phase_artifacts",
                        return_value={}) as prepare, \
                    mock.patch.object(
                        cli.state, "select_run", return_value=selection), \
                    mock.patch.object(
                        cli, "load_phase_adapters", return_value=()), \
                    mock.patch.object(
                        cli.runner, "run_phase_engine", return_value=0):
                status = cli.main([
                    "--output-root", str(output_root),
                    "--device", "device", "--bundle-id", "org.test",
                    "--team", "TEAMTOKEN1",
                    "--suite", "registry-smoke",
                ], environment={}, stdout=io.StringIO(),
                    clock=lambda: timestamp)

        self.assertEqual(0, status)
        self.assertTrue(prepare.call_args.kwargs["reuse_build"])

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

    def test_build_identity_binds_cache_archive_and_bundle_executable_bytes(self):
        """Resume identity must change when any actual build input changes."""
        marker = (
            b"SEEKDB_IOS_ARTIFACT_BUILD_ID=aaaaaaaaaaaa;"
            b"SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled")
        with tempfile.TemporaryDirectory() as temporary_directory:
            engine = Path(temporary_directory) / "build"
            archive = engine / "src/observer/libseekdb_ios_runtime.a"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(marker + b"-archive-one")
            cache = engine / "CMakeCache.txt"
            cache.write_text(
                "SEEKDB_IOS_TEST_HOOKS:BOOL=ON\n"
                "CMAKE_OSX_DEPLOYMENT_TARGET:STRING=18.0\n",
                encoding="utf-8",
            )
            app = engine / "Probe.app"
            app.mkdir()
            with (app / "Info.plist").open("wb") as plist:
                plistlib.dump({"CFBundleExecutable": "PrivateProbe"}, plist)
            executable = app / "PrivateProbe"
            executable.write_bytes(marker + b"-app-one")
            configuration = cli.LocalConfiguration(
                device=None, bundle_id=None, team=None, signing_identity=None,
                engine_build=engine, app_artifact=app, test_hooks=True)

            original = cli.validate_build_identity(configuration, "a" * 40)
            archive.write_bytes(marker + b"-archive-two")
            archive_changed = cli.validate_build_identity(
                configuration, "a" * 40)
            archive.write_bytes(marker + b"-archive-one")
            executable.write_bytes(marker + b"-app-two")
            app_changed = cli.validate_build_identity(
                configuration, "a" * 40)
            executable.write_bytes(marker + b"-app-one")
            cache.write_text(
                "SEEKDB_IOS_TEST_HOOKS:BOOL=ON\n"
                "CMAKE_OSX_DEPLOYMENT_TARGET:STRING=19.0\n",
                encoding="utf-8",
            )
            cache_changed = cli.validate_build_identity(
                configuration, "a" * 40)

        self.assertEqual(4, len({
            original, archive_changed, app_changed, cache_changed}))

    def test_existing_app_requires_valid_plist_executable(self):
        """An existing bundle must not silently skip executable identity."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            engine = Path(temporary_directory) / "build"
            app = engine / "Probe.app"
            app.mkdir(parents=True)
            configuration = cli.LocalConfiguration(
                device=None, bundle_id=None, team=None, signing_identity=None,
                engine_build=engine, app_artifact=app, test_hooks=True)
            with self.assertRaisesRegex(cli.IphoneTestCliError, "Info.plist"):
                cli.validate_build_identity(configuration, "a" * 40)

            with (app / "Info.plist").open("wb") as plist:
                plistlib.dump({"CFBundleExecutable": "MissingProbe"}, plist)
            with self.assertRaisesRegex(cli.IphoneTestCliError,
                                        "bundle executable"):
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
                "booted", "paired", "first-profile"),
            cli.PhysicalDevice(
                "second", "iPhone 16", "iOS", "physical", "default",
                "booted", "paired", "second-profile"),
        ]
        with self.assertRaisesRegex(cli.DeviceSelectionError,
                                    "multiple eligible physical iPhones"):
            cli.select_physical_device(None, devices)

    def test_requested_device_must_be_an_eligible_physical_device(self):
        """An arbitrary identifier must not bypass the discovered physical list."""
        devices = [
            cli.PhysicalDevice(
                "physical", "iPhone 16", "iOS",
                "physical", "default", "booted", "paired",
                "profile-udid"),
        ]
        with self.assertRaisesRegex(cli.DeviceSelectionError,
                                    "requested physical iPhone is unavailable"):
            cli.select_physical_device("simulator", devices)
        selected = cli.select_physical_device("physical", devices)
        self.assertEqual("physical", selected.identifier)
        self.assertEqual("profile-udid", selected.profile_identifier)

    def test_discovery_uses_reality_visibility_boot_and_pairing_state(self):
        """Discovery must use CoreDevice physicality and availability fields."""
        payload = {
            "result": {"devices": [
                {
                    "identifier": "physical",
                    "hardwareProperties": {
                        "platform": "iOS", "deviceType": "iPhone",
                        "reality": "physical", "udid": "profile-udid"},
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
        self.assertEqual("profile-udid", devices[0].profile_identifier)
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
                    "udid": "modern-profile-udid",
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
        self.assertEqual("modern-profile-udid", devices[0].profile_identifier)

    def test_missing_or_ambiguous_hardware_udid_is_rejected(self):
        """Never guess the profile-scope identifier for a physical device."""
        record = {
            "identifier": "command-identifier",
            "properties": {
                "hardware": {
                    "platform": "iOS", "deviceType": "iPhone",
                    "reality": "physical"},
                "state": {"visibilityClass": "default", "bootState": "booted"},
                "connection": {"pairingState": "paired"},
            },
        }
        self.assertIsNone(cli._device_from_record(record))
        record["properties"]["hardware"]["udid"] = "current-udid"
        record["hardwareProperties"] = {"udid": "different-legacy-udid"}
        self.assertIsNone(cli._device_from_record(record))

    def test_dry_run_prints_original_directory_and_serializes_no_unique_values(self):
        """Dry-run state and output must omit every process-local unique value."""
        secrets = (
            "00008110-SECRET-DEVICE", "org.private.bundle",
            "PROFILE-SCOPE-UDID", "TEAMSECRET",
            "Apple Development: Private Person",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            stdout = io.StringIO()
            argv = [
                "--dry-run", "--output-root", str(output_root),
                "--device", secrets[0], "--bundle-id", secrets[1],
                "--team", secrets[3], "--signing-identity", secrets[4],
            ]
            physical = cli.PhysicalDevice(
                secrets[0], "iPhone", "iOS",
                "physical", "default", "booted", "paired", secrets[2])
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

    def test_invalid_mysqltest_host_evidence_stops_before_device_or_build(self):
        """Reject host evidence before discovery, build, signing, or install."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "validate_mysqltest_host_gate",
                        side_effect=cli.IphoneTestCliError(
                            "mysqltest host gate evidence is invalid")), \
                    mock.patch.object(
                        cli, "discover_physical_devices") as discover, \
                    mock.patch.object(
                        cli, "prepare_phase_artifacts") as prepare, \
                    mock.patch.object(
                        cli, "infer_signing_configuration") as signing:
                status = cli.main([
                    "--suite", "mysqltest",
                    "--output-root", str(output_root),
                ], environment={}, stdout=io.StringIO(), stderr=io.StringIO())

        self.assertEqual(2, status)
        discover.assert_not_called()
        prepare.assert_not_called()
        signing.assert_not_called()
        self.assertFalse(output_root.exists())

    def test_configuration_fingerprint_binds_complete_host_evidence(self):
        """Changing validated host evidence must make resume incompatible."""
        configuration = cli.resolve_local_configuration(
            cli.parse_args(["--suite", "inventory"]), {})
        first = cli.configuration_fingerprint(
            runner_suites=("mysqltest",), configuration=configuration,
            build_identity="build", host_evidence_identity={
                "evidence_digest": "a" * 64,
                "source_commit": "b" * 40,
                "corpus_digest": "c" * 64,
                "host_build_identity": "d" * 64,
                "run_id": "run-one",
                "case_list_digest": "e" * 64,
            })
        second = cli.configuration_fingerprint(
            runner_suites=("mysqltest",), configuration=configuration,
            build_identity="build", host_evidence_identity={
                "evidence_digest": "f" * 64,
                "source_commit": "b" * 40,
                "corpus_digest": "c" * 64,
                "host_build_identity": "d" * 64,
                "run_id": "run-two",
                "case_list_digest": "e" * 64,
            })
        self.assertNotEqual(first, second)

    def test_passed_resume_revalidates_artifact_without_rerunning_host_cases(self):
        """A passed host gate resumes only from its bound locked-run evidence."""
        identity = {
            "run_id": "12345678-1234-5678-1234-567812345678",
            "evidence_digest": "a" * 64,
        }
        checkpoint = {
            "mysqltest_host_evidence": identity,
            "phases": [{"cases": [{
                "id": "ios.mysqltest.host-gate", "status": "passed"}]}],
        }
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    cli.run_mysqltest_phase, "validate_host_gate",
                    return_value=identity) as validate, \
                mock.patch.object(
                    cli.run_mysqltest_phase,
                    "execute_local_host_gate") as execute:
            actual = cli.prepare_mysqltest_host_evidence(
                {"seekdb": Path("seekdb")}, Path(directory),
                identity["run_id"], checkpoint=checkpoint)

        self.assertEqual(identity, actual)
        validate.assert_called_once()
        execute.assert_not_called()

    def test_pending_or_failed_host_gate_executes_all_host_cases(self):
        """Only unfinished host gates invoke the tracked 272-case runner."""
        identity = {"run_id": "runner-id", "evidence_digest": "a" * 64}
        for status in ("pending", "failed"):
            with self.subTest(status=status), \
                    tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(
                        cli.run_mysqltest_phase, "validate_host_gate") as validate, \
                    mock.patch.object(
                        cli.run_mysqltest_phase, "execute_local_host_gate",
                        return_value=identity) as execute:
                checkpoint = {"phases": [{"cases": [{
                    "id": "ios.mysqltest.host-gate", "status": status}]}]}
                actual = cli.prepare_mysqltest_host_evidence(
                    {"seekdb": Path("seekdb")}, Path(directory),
                    "runner-id", checkpoint=checkpoint)
                self.assertEqual(identity, actual)
                execute.assert_called_once()
                validate.assert_not_called()

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
                    "booted", "paired", "profile-device")
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
                "physical", "default", "booted", "paired",
                "profile-device")]

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
        selection = mock.Mock(
            run_directory=Path("/tmp/iphone_test/2026-09-24"),
            checkpoint=cli.state.create_checkpoint(
                "a" * 40, "b" * 64,
                dt.datetime(2026, 9, 24, 10, tzinfo=UTC)),
        )
        captured = {}

        def load_adapters(
                configuration, suites, run_directory,
                source_revision, run_id, terminal_stream):
            """Capture process-local inputs at the future Task 4 boundary."""
            captured["configuration"] = configuration
            captured["suites"] = suites
            captured["run_directory"] = run_directory
            captured["source_revision"] = source_revision
            captured["run_id"] = run_id
            captured["terminal_stream"] = terminal_stream
            return ("adapter",)

        def run_engine(
                output_root, received_selection, adapters, now,
                redaction_tokens, phase_ids):
            """Model engine lock ownership and return a distinctive status."""
            captured["output_root"] = output_root
            captured["selection"] = received_selection
            captured["adapters"] = adapters
            captured["now"] = now()
            captured["redaction_tokens"] = redaction_tokens
            captured["phase_ids"] = phase_ids
            received_selection.close()
            return 7

        physical = cli.PhysicalDevice(
            "environment-device", "iPhone", "iOS",
            "physical", "default", "booted", "paired",
            "environment-profile")
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        preparation_lock = mock.Mock()
        path_preview = mock.Mock(run_directory=selection.run_directory)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(cli, "source_commit", return_value="a" * 40), \
                mock.patch.object(
                    cli, "validate_build_identity",
                    return_value="c" * 64), \
                mock.patch.object(cli.state, "select_run", return_value=selection), \
                mock.patch.object(
                    cli.state, "preview_run_path", return_value=path_preview), \
                mock.patch.object(
                    cli.state, "RunLock", return_value=preparation_lock), \
                mock.patch.object(cli, "discover_physical_devices",
                                  return_value=[physical]), \
                mock.patch.object(
                    cli, "prepare_phase_artifacts", return_value={
                        "legacy_build_script_cache": "migrated-2"}), \
                mock.patch.object(cli, "load_phase_adapters",
                                  side_effect=load_adapters), \
                mock.patch.object(cli.runner, "run_phase_engine",
                                  side_effect=run_engine):
            status = cli.main(
                ["--output-root", "/tmp/iphone_test"],
                environment=environment,
                stdout=stdout,
                stderr=stderr,
                clock=lambda: timestamp,
            )

        self.assertEqual(7, status)
        self.assertIn(
            "legacy_build_script_cache=migrated-2", stdout.getvalue())
        self.assertEqual(
            cli.LocalConfiguration(
                device="environment-device",
                profile_device="environment-profile",
                bundle_id="environment.bundle",
                team="ENVTEAM001",
                signing_identity="environment identity",
                engine_build=cli.DEFAULT_ENGINE_BUILD,
                app_artifact=cli.DEFAULT_APP_ARTIFACT,
                test_hooks=True),
            captured["configuration"],
        )
        self.assertEqual(tuple(cli.runner.PHASE_IDS), captured["suites"])
        self.assertEqual(tuple(cli.runner.PHASE_IDS), captured["phase_ids"])
        self.assertEqual(selection.run_directory, captured["run_directory"])
        self.assertEqual("a" * 40, captured["source_revision"])
        self.assertEqual(
            selection.checkpoint["run_id"], captured["run_id"])
        self.assertIs(stderr, captured["terminal_stream"])
        self.assertEqual(("adapter",), captured["adapters"])
        self.assertEqual(
            ("environment-device", "environment-profile",
             "environment.bundle", "ENVTEAM001", "environment identity"),
            captured["redaction_tokens"],
        )
        selection.close.assert_called_once_with()

    def test_loaded_adapters_reuse_precheckpoint_test_app(self):
        """Do not rebuild artifacts after their bytes enter run identity."""
        captured = {}

        def create_phase_adapters(**kwargs):
            """Capture the immutable preparation handoff to phase adapters."""
            captured.update(kwargs)
            return ("adapter",)

        module = types.SimpleNamespace(
            create_phase_adapters=create_phase_adapters)
        configuration = cli.LocalConfiguration(
            device="device", bundle_id="bundle", team="TEAMTOKEN1",
            signing_identity=None,
            engine_build=Path("/tmp/build"),
            app_artifact=Path("/tmp/build/Probe.app"),
            test_hooks=True,
        )
        terminal = io.StringIO()
        with mock.patch.dict(sys.modules, {"iphone_test_phases": module}):
            adapters = cli.load_phase_adapters(
                configuration, ("inventory",), Path("/tmp/run"),
                "a" * 40, "runner-id", terminal_stream=terminal)

        self.assertEqual(("adapter",), adapters)
        self.assertTrue(captured["test_app_prepared"])
        self.assertIs(terminal, captured["terminal_stream"])

    def test_adapter_setup_failure_is_redacted_and_releases_all_state(self):
        """Import/factory exits and terminal writes must not leak local values."""
        secrets = (
            "00008110-SECRET-DEVICE", "org.private.bundle",
            "TEAMSECRET", "Apple Development: Private Person",
            "PROFILE-SCOPE-UDID",
        )
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        for setup_kind in ("import", "factory"):
            with self.subTest(setup_kind=setup_kind), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                output_root = Path(temporary_directory) / "iphone_test"
                physical = cli.PhysicalDevice(
                    secrets[0], "iPhone", "iOS", "physical", "default",
                    "booted", "paired", secrets[4])
                stderr = io.StringIO()
                failure = SystemExit(
                    f"{setup_kind} failed for {' '.join(secrets)}")
                original_import = __import__

                def import_module(name, *args, **kwargs):
                    """Fail only the adapter import and delegate all others."""
                    if name == "iphone_test_phases":
                        subprocess.run([
                            sys.executable, "-c",
                            "import os; os.write(2, %r)" % (
                                " ".join(secrets).encode(),),
                        ], check=True)
                        raise failure
                    return original_import(name, *args, **kwargs)

                def create_phase_adapters(**_kwargs):
                    """Model a phase factory that exposes a sensitive error."""
                    subprocess.run([
                        sys.executable, "-c",
                        "import os; os.write(2, %r)" % (
                            " ".join(secrets).encode(),),
                    ], check=True)
                    raise failure

                adapter_module = types.SimpleNamespace(
                    create_phase_adapters=create_phase_adapters)
                setup_patch = (
                    mock.patch("builtins.__import__", side_effect=import_module)
                    if setup_kind == "import" else
                    mock.patch.dict(
                        sys.modules, {"iphone_test_phases": adapter_module}))
                terminal_stderr = io.StringIO()
                with contextlib.redirect_stderr(terminal_stderr), \
                        setup_patch, mock.patch.object(
                        cli, "source_commit", return_value="a" * 40), \
                        mock.patch.object(
                            cli, "validate_build_identity",
                            return_value="c" * 64), \
                        mock.patch.object(
                            cli, "discover_physical_devices",
                            return_value=[physical]):
                    status = cli.main([
                        "--output-root", str(output_root),
                        "--device", secrets[0],
                        "--bundle-id", secrets[1],
                        "--team", secrets[2],
                        "--signing-identity", secrets[3],
                    ], environment={}, stdout=io.StringIO(), stderr=stderr,
                        clock=lambda: timestamp)

                serialized = stderr.getvalue() + terminal_stderr.getvalue()
                if output_root.exists():
                    serialized += "".join(
                        path.read_text(encoding="utf-8", errors="replace")
                        for path in output_root.rglob("*") if path.is_file())
                self.assertEqual(2, status)
                self.assertIn("phase adapter setup failed", serialized)
                for secret in secrets:
                    self.assertNotIn(secret, serialized)
                self.assertFalse(cli.runner.has_runtime_redaction_tokens())
                lock = cli.state.RunLock(
                    output_root, output_root / "2026-09-24")
                lock.acquire()
                lock.release()

    def test_preparation_codes_are_allowlisted_without_exposing_setup_output(self):
        """Expose fixed stage categories but keep arbitrary setup data generic."""
        token = "private-preparation-token"
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        cases = (
            ("build-failed", "failed during build"),
            (f"build failed for {token}", "phase adapter setup failed"),
            (None, "phase adapter setup failed"),
        )
        for issue, expected in cases:
            with self.subTest(issue=issue), \
                    tempfile.TemporaryDirectory() as temporary_directory:
                output_root = Path(temporary_directory) / "iphone_test"
                physical = cli.PhysicalDevice(
                    "command-device", "iPhone", "iOS", "physical",
                    "default", "booted", "paired", "profile-device")

                def prepare(configuration, *_args, **_kwargs):
                    """Emit hostile fd output before returning one issue code."""
                    os.write(2, token.encode("utf-8"))
                    if issue is None:
                        raise RuntimeError(token)
                    return cli.PhasePreparationOutcome(issue, configuration)

                stderr = io.StringIO()
                terminal_stderr = io.StringIO()
                with contextlib.redirect_stderr(terminal_stderr), \
                        mock.patch.object(
                            cli, "source_commit", return_value="a" * 40), \
                        mock.patch.object(
                            cli, "validate_build_identity",
                            return_value="c" * 64), \
                        mock.patch.object(
                            cli, "discover_physical_devices",
                            return_value=[physical]), \
                        mock.patch.object(
                            cli, "prepare_phase_artifacts",
                            side_effect=prepare):
                    status = cli.main([
                        "--output-root", str(output_root),
                        "--device", "command-device",
                        "--bundle-id", "example.bundle",
                        "--team", "TEAMTOKEN1",
                    ], environment={}, stdout=io.StringIO(), stderr=stderr,
                        clock=lambda: timestamp)

                serialized = stderr.getvalue() + terminal_stderr.getvalue()
                if output_root.exists():
                    serialized += "".join(
                        path.read_text(encoding="utf-8", errors="replace")
                        for path in output_root.rglob("*") if path.is_file())
                self.assertEqual(2, status)
                self.assertIn(expected, serialized)
                self.assertNotIn(token, serialized)

    def test_custom_base_exception_is_generic_and_fd_output_is_captured(self):
        """Untrusted BaseException types and inherited fd writes never escape."""
        secret = "private-adapter-token"

        class HostileExit(BaseException):
            """Model an adapter-defined control-flow exception."""

        def create_phase_adapters(**_kwargs):
            """Write through inherited fd 2 before raising an untrusted type."""
            subprocess.run([
                sys.executable, "-c",
                "import os; os.write(2, %r)" % (secret.encode(),),
            ], check=True)
            raise HostileExit(secret)

        module = types.SimpleNamespace(
            create_phase_adapters=create_phase_adapters)
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            physical = cli.PhysicalDevice(
                "device", "iPhone", "iOS", "physical", "default",
                "booted", "paired", "profile-device")
            stderr = io.StringIO()
            terminal_stderr = io.StringIO()
            with contextlib.redirect_stderr(terminal_stderr), \
                    mock.patch.dict(
                        sys.modules, {"iphone_test_phases": module}), \
                    mock.patch.object(
                        cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "validate_build_identity", return_value="c" * 64), \
                    mock.patch.object(
                        cli, "discover_physical_devices",
                        return_value=[physical]):
                status = cli.main([
                    "--output-root", str(output_root), "--device", "device",
                ], environment={}, stdout=io.StringIO(), stderr=stderr,
                    clock=lambda: timestamp)

            serialized = stderr.getvalue() + terminal_stderr.getvalue()
            serialized += "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in output_root.rglob("*") if path.is_file())
            self.assertEqual(2, status)
            self.assertIn("phase adapter setup failed", serialized)
            self.assertNotIn(secret, serialized)
            self.assertNotIn("HostileExit", serialized)

    def test_adapter_keyboard_interrupt_returns_130_without_leaking(self):
        """Preserve interrupt semantics while suppressing sensitive exception text."""
        secret = "private-device-token"
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "iphone_test"
            physical = cli.PhysicalDevice(
                secret, "iPhone", "iOS", "physical", "default",
                "booted", "paired", "private-profile-token")
            stderr = io.StringIO()
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "prepare_phase_artifacts", return_value={}), \
                    mock.patch.object(
                        cli, "validate_build_identity", return_value="c" * 64), \
                    mock.patch.object(
                        cli, "discover_physical_devices",
                        return_value=[physical]), \
                    mock.patch.object(
                        cli, "load_phase_adapters",
                        side_effect=KeyboardInterrupt(secret)):
                status = cli.main([
                    "--output-root", str(output_root), "--device", secret,
                ], environment={}, stdout=io.StringIO(), stderr=stderr,
                    clock=lambda: timestamp)

            serialized = stderr.getvalue() + "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in output_root.rglob("*") if path.is_file())
            self.assertEqual(130, status)
            self.assertNotIn(secret, serialized)
            self.assertFalse(cli.runner.has_runtime_redaction_tokens())

    def test_precheckpoint_build_identity_survives_interrupt_and_resume(self):
        """Artifacts created during setup must already belong to checkpoint identity."""
        timestamp = dt.datetime(2026, 9, 24, 10, tzinfo=UTC)
        marker = (
            b"SEEKDB_IOS_ARTIFACT_BUILD_ID=aaaaaaaaaaaa;"
            b"SEEKDB_IOS_ARTIFACT_HOOK_MODE=enabled")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_root = root / "iphone_test"
            engine = root / "build"
            app = engine / "Probe.app"
            prepare_calls = []

            def prepare(
                    configuration, suites, source_revision, run_id,
                    reuse_build=False):
                """Create deterministic current artifacts before state selection."""
                if not reuse_build:
                    self.assertFalse(
                        (output_root / "2026-09-24/checkpoint.json").exists())
                context = multiprocessing.get_context("spawn")
                queue = context.Queue()
                contender = context.Process(
                    target=_try_preparation_lock,
                    args=(output_root, output_root / "2026-09-24", queue))
                contender.start()
                contender.join(timeout=10)
                self.assertEqual(0, contender.exitcode)
                self.assertEqual("locked", queue.get(timeout=2))
                prepare_calls.append((
                    tuple(suites), source_revision, run_id, reuse_build))
                archive = engine / "src/observer/libseekdb_ios_runtime.a"
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(marker + b"-stable-archive")
                (engine / "CMakeCache.txt").write_text(
                    "SEEKDB_IOS_TEST_HOOKS:BOOL=ON\n", encoding="utf-8")
                app.mkdir(parents=True, exist_ok=True)
                with (app / "Info.plist").open("wb") as plist:
                    plistlib.dump({"CFBundleExecutable": "Probe"}, plist)
                (app / "Probe").write_bytes(marker + b"-stable-app")

            physical = cli.PhysicalDevice(
                "device", "iPhone", "iOS", "physical", "default",
                "booted", "paired", "profile-device")
            engine_statuses = iter((130, 0))
            selected_directories = []

            def run_engine(_root, selection, _adapters, **_kwargs):
                """Model an interruption after selection and a successful resume."""
                selected_directories.append(selection.run_directory)
                selection.close()
                return next(engine_statuses)

            common = [
                "--output-root", str(output_root),
                "--device", "device", "--bundle-id", "org.private.probe",
                "--team", "TEAMTOKEN1", "--engine-build", str(engine),
                "--app-artifact", str(app),
            ]
            with mock.patch.object(
                    cli, "source_commit", return_value="a" * 40), \
                    mock.patch.object(
                        cli, "prepare_phase_artifacts", side_effect=prepare), \
                    mock.patch.object(
                        cli, "discover_physical_devices",
                        return_value=[physical]), \
                    mock.patch.object(
                        cli, "load_phase_adapters", return_value=("adapter",)), \
                    mock.patch.object(
                        cli.runner, "run_phase_engine", side_effect=run_engine):
                first = cli.main(
                    common, environment={}, stdout=io.StringIO(),
                    clock=lambda: timestamp)
                second = cli.main(
                    [*common, "--resume"], environment={}, stdout=io.StringIO(),
                    clock=lambda: timestamp + dt.timedelta(hours=1))

            checkpoint = json.loads((
                selected_directories[0] / "checkpoint.json").read_text(
                    encoding="utf-8"))
            actual_identity = cli.validate_build_identity(
                cli.LocalConfiguration(
                    device="device", profile_device="profile-device",
                    bundle_id="org.private.probe",
                    team="TEAMTOKEN1", signing_identity=None,
                    engine_build=engine, app_artifact=app, test_hooks=True),
                "a" * 40)
            expected_fingerprint = cli.configuration_fingerprint(
                runner_suites=cli.runner.PHASE_IDS,
                configuration=cli.LocalConfiguration(
                    device="device", profile_device="profile-device",
                    bundle_id="org.private.probe",
                    team="TEAMTOKEN1", signing_identity=None,
                    engine_build=engine, app_artifact=app, test_hooks=True),
                build_identity=actual_identity)

        self.assertEqual((130, 0), (first, second))
        self.assertEqual(2, len(prepare_calls))
        self.assertEqual((False, True), tuple(
            call[3] for call in prepare_calls))
        self.assertEqual(selected_directories[0], selected_directories[1])
        self.assertEqual(expected_fingerprint, checkpoint["config_fingerprint"])


if __name__ == "__main__":
    unittest.main()
