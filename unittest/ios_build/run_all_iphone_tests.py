#!/usr/bin/env python3
"""Select and execute a resumable physical-iPhone validation run."""

import argparse
from dataclasses import dataclass, replace
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Iterable, Mapping, Optional, Sequence, TextIO

import iphone_test_runner as runner
import iphone_test_state as state


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "iphone_test"
DEFAULT_ENGINE_BUILD = REPOSITORY_ROOT / "build_ios_arm64"
DEFAULT_APP_ARTIFACT = (
    DEFAULT_ENGINE_BUILD / "app/Release-iphoneos/SeekDBProbe.app")
DEVICE_ENVIRONMENT = "SEEKDB_IPHONE_DEVICE"
BUNDLE_ENVIRONMENT = "SEEKDB_IPHONE_BUNDLE_ID"
TEAM_ENVIRONMENT = "SEEKDB_IPHONE_TEAM"
SIGNING_ENVIRONMENT = "SEEKDB_IPHONE_SIGNING_IDENTITY"
ENGINE_BUILD_ENVIRONMENT = "SEEKDB_IPHONE_ENGINE_BUILD"
APP_ARTIFACT_ENVIRONMENT = "SEEKDB_IPHONE_APP_ARTIFACT"
ARTIFACT_MARKER = re.compile(
    rb"SEEKDB_IOS_ARTIFACT_BUILD_ID=([0-9a-f]{12});"
    rb"SEEKDB_IOS_ARTIFACT_HOOK_MODE=(enabled|disabled)")


class IphoneTestCliError(RuntimeError):
    """Indicate a safe, user-facing command-line orchestration failure."""


class DeviceSelectionError(IphoneTestCliError):
    """Indicate that exactly one eligible physical iPhone was not selected."""


@dataclass(frozen=True)
class PhysicalDevice:
    """Retain physical-device discovery fields only in process memory."""

    identifier: str
    name: str
    platform: str
    reality: str
    visibility_class: str
    boot_state: str
    pairing_state: str


@dataclass(frozen=True)
class LocalConfiguration:
    """Hold unique device and signing inputs without serializing them."""

    device: Optional[str]
    bundle_id: Optional[str]
    team: Optional[str]
    signing_identity: Optional[str]
    engine_build: Path
    app_artifact: Path
    test_hooks: bool

    def redaction_tokens(self) -> tuple[str, ...]:
        """Return unique values that must remain process-local."""
        return tuple(value for value in (
            self.device, self.bundle_id, self.team, self.signing_identity)
            if value)


def parse_args(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse lifecycle, suite, and process-local configuration options."""
    parser = argparse.ArgumentParser(description=__doc__)
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument(
        "--resume", action="store_true",
        help="resume the newest incomplete run across start dates")
    lifecycle.add_argument(
        "--restart", action="store_true",
        help="preserve today's prior state and start a fresh run")
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
        help="directory containing start-date run directories")
    parser.add_argument(
        "--suite", action="append", choices=runner.PHASE_IDS,
        help="limit adapter loading to a named phase; may be repeated")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="select state and a physical device without dispatching phases")
    parser.add_argument(
        "--device", help=f"physical iPhone identifier; prefer ${DEVICE_ENVIRONMENT}")
    parser.add_argument(
        "--bundle-id", help=f"application bundle ID; prefer ${BUNDLE_ENVIRONMENT}")
    parser.add_argument(
        "--team", help=f"Apple team identifier; prefer ${TEAM_ENVIRONMENT}")
    parser.add_argument(
        "--signing-identity",
        help=f"code-signing identity; prefer ${SIGNING_ENVIRONMENT}")
    parser.add_argument(
        "--engine-build", type=Path,
        help=f"iOS engine build directory; default ${ENGINE_BUILD_ENVIRONMENT}")
    parser.add_argument(
        "--app-artifact", type=Path,
        help=f"signed App path; default ${APP_ARTIFACT_ENVIRONMENT}")
    return parser.parse_args(arguments)


def resolve_local_configuration(
        options: argparse.Namespace,
        environment: Mapping[str, str]) -> LocalConfiguration:
    """Resolve unique inputs from argv first and the environment second."""
    engine_value = (
        options.engine_build or environment.get(ENGINE_BUILD_ENVIRONMENT)
        or DEFAULT_ENGINE_BUILD)
    engine_build = Path(engine_value).expanduser().absolute()
    artifact_value = (
        options.app_artifact or environment.get(APP_ARTIFACT_ENVIRONMENT))
    app_artifact = (
        Path(artifact_value).expanduser().absolute()
        if artifact_value else
        engine_build / "app/Release-iphoneos/SeekDBProbe.app")
    return LocalConfiguration(
        device=options.device or environment.get(DEVICE_ENVIRONMENT),
        bundle_id=options.bundle_id or environment.get(BUNDLE_ENVIRONMENT),
        team=options.team or environment.get(TEAM_ENVIRONMENT),
        signing_identity=(
            options.signing_identity
            or environment.get(SIGNING_ENVIRONMENT)),
        engine_build=engine_build,
        app_artifact=app_artifact,
        test_hooks=True,
    )


def _nested_text(record: Mapping[str, object], *path: str) -> str:
    """Return one nested scalar as text or an empty value when absent."""
    current: object = record
    for component in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(component)
    return current if isinstance(current, str) else ""


def _first_nested_text(
        record: Mapping[str, object],
        *paths: tuple[str, ...]) -> str:
    """Return the first populated text field among schema-version paths."""
    for path in paths:
        value = _nested_text(record, *path)
        if value:
            return value
    return ""


def _device_from_record(record: object) -> Optional[PhysicalDevice]:
    """Return one available physical iPhone from a devicectl record."""
    if not isinstance(record, Mapping):
        return None
    identifier = record.get("identifier")
    if not isinstance(identifier, str) or not identifier:
        return None
    name_value = record.get("name")
    name = (name_value if isinstance(name_value, str)
            else _nested_text(record, "deviceProperties", "name"))
    platform = _first_nested_text(
        record,
        ("properties", "hardware", "platform"),
        ("hardwareProperties", "platform"),
    )
    device_type = _first_nested_text(
        record,
        ("properties", "hardware", "deviceType"),
        ("hardwareProperties", "deviceType"),
    )
    reality = _first_nested_text(
        record,
        ("properties", "hardware", "reality"),
        ("hardwareProperties", "reality"),
    )
    visibility_class = _first_nested_text(
        record,
        ("properties", "state", "visibilityClass"),
        ("visibilityClass",),
    )
    boot_state = _first_nested_text(
        record,
        ("properties", "state", "bootState"),
        ("deviceProperties", "bootState"),
    )
    pairing_state = _first_nested_text(
        record,
        ("properties", "connection", "pairingState"),
        ("connectionProperties", "pairingState"),
    )
    if reality.lower() != "physical":
        return None
    if platform.lower() not in {"ios", "iphoneos"}:
        return None
    if device_type.lower() != "iphone":
        return None
    if visibility_class.lower() != "default":
        return None
    if boot_state.lower() != "booted":
        return None
    if pairing_state.lower() != "paired":
        return None
    return PhysicalDevice(
        identifier, name, platform, reality, visibility_class, boot_state,
        pairing_state)


def discover_physical_devices(
        run_command: Callable[..., object] = subprocess.run
        ) -> list[PhysicalDevice]:
    """Discover connected physical iPhones without writing raw metadata."""
    command_environment = dict(os.environ)
    command_environment.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    for variable in (
            DEVICE_ENVIRONMENT, BUNDLE_ENVIRONMENT, TEAM_ENVIRONMENT,
            SIGNING_ENVIRONMENT, ENGINE_BUILD_ENVIRONMENT,
            APP_ARTIFACT_ENVIRONMENT):
        command_environment.pop(variable, None)
    result = run_command(
        ["xcrun", "devicectl", "list", "devices",
         "--quiet", "--json-output", "/dev/stdout"],
        check=False,
        capture_output=True,
        text=True,
        env=command_environment,
    )
    if result.returncode != 0:
        raise DeviceSelectionError("physical iPhone discovery failed")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise DeviceSelectionError(
            "physical iPhone discovery returned invalid data") from error
    result_object = payload.get("result", {}) if isinstance(payload, dict) else {}
    records = result_object.get("devices", []) \
        if isinstance(result_object, dict) else []
    devices = []
    for record in records if isinstance(records, list) else []:
        device = _device_from_record(record)
        if device is not None:
            devices.append(device)
    return devices


def select_physical_device(
        requested_identifier: Optional[str],
        devices: Sequence[PhysicalDevice]) -> PhysicalDevice:
    """Select exactly one eligible physical iPhone without simulator fallback."""
    if requested_identifier:
        matches = [
            device for device in devices
            if device.identifier == requested_identifier]
        if len(matches) != 1:
            raise DeviceSelectionError(
                "requested physical iPhone is unavailable")
        return matches[0]
    if not devices:
        raise DeviceSelectionError("no eligible physical iPhone is connected")
    if len(devices) > 1:
        raise DeviceSelectionError(
            "multiple eligible physical iPhones require --device")
    return devices[0]


def source_commit(
        run_command: Callable[..., object] = subprocess.run) -> str:
    """Return HEAD only when no tracked or untracked source change exists."""
    status = run_command(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--"],
        cwd=REPOSITORY_ROOT, check=False, capture_output=True, text=True)
    if status.returncode != 0:
        raise IphoneTestCliError("cannot inspect tracked source changes")
    if status.stdout.strip():
        raise IphoneTestCliError(
            "tracked source changes must be committed before iPhone tests")
    result = run_command(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT,
        check=False, capture_output=True, text=True)
    revision = result.stdout.strip() if result.returncode == 0 else ""
    if (len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)):
        raise IphoneTestCliError("cannot determine the source revision")
    return revision


def _artifact_marker(path: Path) -> Optional[tuple[bytes, bytes]]:
    """Find a bounded embedded build marker without retaining artifact bytes."""
    marker = None
    tail = b""
    with path.open("rb") as artifact:
        while True:
            chunk = artifact.read(1024 * 1024)
            if not chunk:
                break
            match = ARTIFACT_MARKER.search(tail + chunk)
            if marker is None and match is not None:
                marker = (match.group(1), match.group(2))
            tail = (tail + chunk)[-256:]
    return marker


def _app_executable(app_artifact: Path) -> Optional[Path]:
    """Return a configured App executable path when one exists."""
    if app_artifact.is_file():
        return app_artifact
    candidate = app_artifact / app_artifact.stem
    return candidate if candidate.is_file() else None


def validate_build_identity(
        configuration: LocalConfiguration, source_revision: str) -> str:
    """Validate present build outputs and return their opaque aggregate hash."""
    identity = {
        "source_revision": source_revision,
        "test_hooks": configuration.test_hooks,
    }
    cache = configuration.engine_build / "CMakeCache.txt"
    if cache.is_file():
        expected = "ON" if configuration.test_hooks else "OFF"
        setting = f"SEEKDB_IOS_TEST_HOOKS:BOOL={expected}"
        if setting.encode("utf-8") not in cache.read_bytes():
            raise IphoneTestCliError(
                "iPhone build configuration does not match runner mode")
    archive = (
        configuration.engine_build
        / "src/observer/libseekdb_ios_runtime.a")
    expected_mode = b"enabled" if configuration.test_hooks else b"disabled"
    expected_marker = (source_revision[:12].encode("ascii"), expected_mode)
    if archive.is_file():
        if _artifact_marker(archive) != expected_marker:
            raise IphoneTestCliError(
                "iPhone build identity does not match source and runner mode")
    app_executable = _app_executable(configuration.app_artifact)
    if app_executable is not None:
        if _artifact_marker(app_executable) != expected_marker:
            raise IphoneTestCliError(
                "iPhone artifact identity does not match source and runner mode")
    serialized = json.dumps(
        identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def configuration_fingerprint(
        *, runner_suites: Sequence[str],
        configuration: LocalConfiguration,
        build_identity: str) -> str:
    """Hash evidence-affecting local inputs into one opaque resume identity."""
    serialized = json.dumps(
        {
            "runner_version": state.RUNNER_VERSION,
            "suites": list(runner_suites),
            "bundle_id": configuration.bundle_id,
            "team": configuration.team,
            "signing_identity": configuration.signing_identity,
            "engine_build": str(configuration.engine_build),
            "app_artifact": str(configuration.app_artifact),
            "test_hooks": configuration.test_hooks,
            "build_identity": build_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_phase_adapters(
        configuration: LocalConfiguration,
        suites: Sequence[str]) -> Iterable[runner.PhaseAdapter]:
    """Load phase adapters when the standalone phase registry is available."""
    try:
        import iphone_test_phases
    except ModuleNotFoundError as error:
        if error.name != "iphone_test_phases":
            raise
        return ()
    return iphone_test_phases.create_phase_adapters(
        configuration=configuration, suites=tuple(suites))


def _run_mode(options: argparse.Namespace) -> state.RunMode:
    """Map mutually exclusive parser flags to the checkpoint run mode."""
    if options.resume:
        return state.RunMode.RESUME
    if options.restart:
        return state.RunMode.RESTART
    return state.RunMode.DEFAULT


def _now() -> dt.datetime:
    """Return a timezone-aware local timestamp for checkpoint transitions."""
    return dt.datetime.now().astimezone()


def main(
        arguments: Optional[Sequence[str]] = None,
        *,
        environment: Optional[Mapping[str, str]] = None,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
        clock: Callable[[], dt.datetime] = _now) -> int:
    """Select one run and physical iPhone, then dispatch registered phases."""
    options = parse_args(arguments)
    local_environment = os.environ if environment is None else environment
    configuration = resolve_local_configuration(options, local_environment)
    suites = tuple(options.suite or runner.PHASE_IDS)
    selection = None
    engine_owns_selection = False
    try:
        revision = source_commit()
        build_identity = validate_build_identity(configuration, revision)
        fingerprint = configuration_fingerprint(
            runner_suites=suites, configuration=configuration,
            build_identity=build_identity)
        current_time = clock()
        if options.dry_run:
            preview = state.preview_run(
                options.output_root,
                mode=_run_mode(options),
                source_commit=revision,
                config_fingerprint=fingerprint,
                now=current_time,
            )
            print(f"Selected iPhone test run: {preview.run_directory}",
                  file=stdout, flush=True)
        else:
            selection = state.select_run(
                options.output_root,
                mode=_run_mode(options),
                source_commit=revision,
                config_fingerprint=fingerprint,
                now=current_time,
            )
            print(f"Selected iPhone test run: {selection.run_directory}",
                  file=stdout, flush=True)

        selected_device = select_physical_device(
            configuration.device, discover_physical_devices())
        configuration = replace(
            configuration, device=selected_device.identifier)
        if options.dry_run:
            print("Dry run completed; no test phase was dispatched.",
                  file=stdout)
            return 0

        adapters = load_phase_adapters(configuration, suites)
        engine_owns_selection = True
        return runner.run_phase_engine(
            options.output_root, selection, adapters, now=clock,
            redaction_tokens=configuration.redaction_tokens())
    except (IphoneTestCliError, state.IphoneTestStateError) as error:
        print(f"iPhone test runner error: {error}", file=stderr)
        return 2
    finally:
        if selection is not None and not engine_owns_selection:
            selection.close()


if __name__ == "__main__":
    raise SystemExit(main())
