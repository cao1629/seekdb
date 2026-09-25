#!/usr/bin/env python3
"""Select and execute a resumable physical-iPhone validation run."""

import argparse
import contextlib
from dataclasses import dataclass, replace
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import threading
from typing import Callable, Iterable, Mapping, Optional, Sequence, TextIO
import uuid

import iphone_test_runner as runner
import iphone_test_state as state
import run_mysqltest_phase


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
PREPARATION_DIAGNOSTICS = {
    "signing-config": (
        "iPhone test App preparation failed during signing configuration; "
        "set SEEKDB_IPHONE_BUNDLE_ID and SEEKDB_IPHONE_TEAM or provide one "
        "valid existing signed probe App"),
    "build-inputs": (
        "iPhone test App preparation failed during build input validation; "
        "set SEEKDB_IPHONE_DEPS_PREFIX, SEEKDB_IPHONE_HEADERS_PREFIX, CARGO, "
        "RUSTUP, CARGO_HOME, RUSTUP_HOME, and RUST_TARGET_DIR or provide one "
        "valid local CMake cache"),
    "local-profile": (
        "iPhone test App preparation failed during profile validation"),
    "app-output": (
        "iPhone test App preparation failed during App output validation"),
    "build-failed": "iPhone test App preparation failed during build",
    "sign-failed": (
        "iPhone test App preparation failed during signature validation"),
    "sign-install-failed": (
        "iPhone test App preparation failed during signing or installation"),
    "install-failed": (
        "iPhone test App preparation failed during installation"),
}


class IphoneTestCliError(RuntimeError):
    """Indicate a safe, user-facing command-line orchestration failure."""


class DeviceSelectionError(IphoneTestCliError):
    """Indicate that exactly one eligible physical iPhone was not selected."""


def validate_mysqltest_host_gate(
        suites: Sequence[str], environment: Mapping[str, str]):
    """Resolve required local host binaries before any device/build side effect."""
    if "mysqltest" not in suites:
        return None
    try:
        return run_mysqltest_phase.resolve_host_binaries(environment)
    except run_mysqltest_phase.MysqltestPhaseError as error:
        raise IphoneTestCliError(
            "mysqltest host gate prerequisites are unavailable") from error


def prepare_mysqltest_host_evidence(
        binaries, run_directory: Path, run_id: str):
    """Execute the tracked host runner and return its validated identity."""
    if binaries is None:
        return None
    try:
        return run_mysqltest_phase.execute_local_host_gate(
            REPOSITORY_ROOT, run_directory / "mysqltest-host",
            run_id, binaries)
    except run_mysqltest_phase.MysqltestPhaseError as error:
        raise IphoneTestCliError(
            "mysqltest host gate execution failed") from error
    except Exception as error:
        raise IphoneTestCliError(
            "mysqltest host gate execution failed") from error


@dataclass(frozen=True)
class PhysicalDevice:
    """Retain command and profile device identifiers only in process memory."""

    identifier: str
    name: str
    platform: str
    reality: str
    visibility_class: str
    boot_state: str
    pairing_state: str
    profile_identifier: str


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
    profile_device: Optional[str] = None
    provisioned_devices: tuple[str, ...] = ()
    profile_certificate_hashes: tuple[str, ...] = ()

    def redaction_tokens(self) -> tuple[str, ...]:
        """Return unique values that must remain process-local."""
        return tuple(value for value in (
            self.device, self.profile_device, self.bundle_id, self.team,
            self.signing_identity,
            *self.provisioned_devices, *self.profile_certificate_hashes)
            if value)


@dataclass(frozen=True)
class PhasePreparationOutcome:
    """Return safe preparation status with enriched process-local configuration."""

    issue: object
    configuration: LocalConfiguration


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


def _embedded_profile(path: Path) -> Optional[Mapping[str, object]]:
    """Parse one embedded XML provisioning plist without external commands."""
    if not path.is_file():
        return None
    content = path.read_bytes()
    start = content.find(b"<?xml")
    end = content.find(b"</plist>", start)
    if start < 0 or end < 0:
        raise IphoneTestCliError(
            "embedded provisioning profile is not a readable XML plist")
    try:
        profile = plistlib.loads(content[start:end + len(b"</plist>")])
    except plistlib.InvalidFileException as error:
        raise IphoneTestCliError(
            "embedded provisioning profile is invalid") from error
    if not isinstance(profile, Mapping):
        raise IphoneTestCliError("embedded provisioning profile is invalid")
    return profile


def infer_signing_configuration(
        configuration: LocalConfiguration,
        validate_explicit: bool = False) -> LocalConfiguration:
    """Infer unique local bundle/team/profile tokens without external commands."""
    if (configuration.bundle_id and configuration.team
            and not validate_explicit):
        return configuration
    if not configuration.app_artifact.is_dir():
        return configuration
    plist_path = configuration.app_artifact / "Info.plist"
    if not plist_path.is_file():
        return configuration
    try:
        with plist_path.open("rb") as plist_file:
            app_plist = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IphoneTestCliError("existing App Info.plist is invalid") from error
    profile = _embedded_profile(
        configuration.app_artifact / "embedded.mobileprovision")
    if profile is None:
        raise IphoneTestCliError(
            "existing App has no reusable embedded provisioning profile")
    bundle_id = app_plist.get("CFBundleIdentifier") \
        if isinstance(app_plist, Mapping) else None
    team_values = profile.get("TeamIdentifier")
    entitlements = profile.get("Entitlements")
    application_identifier = entitlements.get("application-identifier") \
        if isinstance(entitlements, Mapping) else None
    if (not isinstance(bundle_id, str) or not bundle_id
            or not isinstance(team_values, list) or len(team_values) != 1
            or not isinstance(team_values[0], str)
            or application_identifier != f"{team_values[0]}.{bundle_id}"):
        raise IphoneTestCliError(
            "existing App signing metadata is not uniquely consistent")
    certificates = profile.get("DeveloperCertificates")
    provisioned_devices = profile.get("ProvisionedDevices")
    expiration = profile.get("ExpirationDate")
    platforms = profile.get("Platform")
    if isinstance(expiration, dt.datetime) and expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=dt.timezone.utc)
    if (not isinstance(certificates, list) or len(certificates) != 1
            or not isinstance(certificates[0], bytes)
            or not isinstance(provisioned_devices, list)
            or not provisioned_devices
            or any(not isinstance(item, str) or not item
                   for item in provisioned_devices)
            or not isinstance(expiration, dt.datetime)
            or expiration <= dt.datetime.now(dt.timezone.utc)
            or not isinstance(platforms, list) or "iOS" not in platforms):
        raise IphoneTestCliError(
            "existing App provisioning scope is not uniquely usable")
    certificate_hash = hashlib.sha1(certificates[0]).hexdigest().upper()
    inferred_bundle = configuration.bundle_id or bundle_id
    inferred_team = configuration.team or team_values[0]
    if inferred_bundle != bundle_id or inferred_team != team_values[0]:
        raise IphoneTestCliError(
            "configured signing values do not match the existing App")
    return replace(
        configuration,
        bundle_id=inferred_bundle,
        team=inferred_team,
        signing_identity=(
            configuration.signing_identity or certificate_hash),
        provisioned_devices=tuple(sorted(set(provisioned_devices))),
        profile_certificate_hashes=(certificate_hash,),
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
    profile_identifiers = {
        value for value in (
            _nested_text(record, "properties", "hardware", "udid"),
            _nested_text(record, "hardwareProperties", "udid"),
        ) if value
    }
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
    if len(profile_identifiers) != 1:
        return None
    profile_identifier = next(iter(profile_identifiers))
    return PhysicalDevice(
        identifier, name, platform, reality, visibility_class, boot_state,
        pairing_state, profile_identifier)


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


def _hash_file(path: Path) -> str:
    """Return one streaming SHA-256 digest without retaining artifact bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while True:
            chunk = artifact.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _app_executable(app_artifact: Path) -> Optional[Path]:
    """Resolve an existing bundle executable from its authoritative plist."""
    if not app_artifact.exists():
        return None
    if app_artifact.is_file():
        return app_artifact
    if not app_artifact.is_dir():
        raise IphoneTestCliError("iPhone App artifact has an unsupported type")
    plist_path = app_artifact / "Info.plist"
    if not plist_path.is_file():
        raise IphoneTestCliError("iPhone App Info.plist is missing")
    try:
        with plist_path.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IphoneTestCliError("iPhone App Info.plist is invalid") from error
    executable_name = plist.get("CFBundleExecutable") \
        if isinstance(plist, Mapping) else None
    if (not isinstance(executable_name, str) or not executable_name
            or Path(executable_name).name != executable_name):
        raise IphoneTestCliError(
            "iPhone App Info.plist has an invalid CFBundleExecutable")
    executable = app_artifact / executable_name
    if not executable.is_file():
        raise IphoneTestCliError("iPhone App bundle executable is missing")
    return executable


def validate_build_identity(
        configuration: LocalConfiguration, source_revision: str) -> str:
    """Validate present build outputs and return their opaque aggregate hash."""
    identity = {
        "source_revision": source_revision,
        "test_hooks": configuration.test_hooks,
        "cmake_cache": None,
        "runtime_archive": None,
        "app_executable": None,
    }
    app_executable = _app_executable(configuration.app_artifact)
    cache = configuration.engine_build / "CMakeCache.txt"
    if not cache.is_file():
        raise IphoneTestCliError(
            "iPhone build identity configuration is not prepared")
    identity["cmake_cache"] = _hash_file(cache)
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
    if not archive.is_file():
        raise IphoneTestCliError(
            "iPhone build identity runtime archive is not prepared")
    if _artifact_marker(archive) != expected_marker:
        raise IphoneTestCliError(
            "iPhone build identity does not match source and runner mode")
    identity["runtime_archive"] = _hash_file(archive)
    if app_executable is None:
        raise IphoneTestCliError(
            "iPhone build identity App artifact is not prepared")
    if _artifact_marker(app_executable) != expected_marker:
        raise IphoneTestCliError(
            "iPhone artifact identity does not match source and runner mode")
    identity["app_executable"] = _hash_file(app_executable)
    serialized = json.dumps(
        identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def configuration_fingerprint(
        *, runner_suites: Sequence[str],
        configuration: LocalConfiguration,
        build_identity: str,
        host_evidence_identity=None) -> str:
    """Hash evidence-affecting local inputs into one opaque resume identity."""
    device_identity = json.dumps(
        {
            "command": configuration.device or "host-only",
            "profile": configuration.profile_device or "host-only",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    serialized = json.dumps(
        {
            "runner_version": state.RUNNER_VERSION,
            "suites": list(runner_suites),
            "device_hash": hashlib.sha256(
                device_identity.encode("utf-8")).hexdigest(),
            "bundle_id": configuration.bundle_id,
            "team": configuration.team,
            "signing_identity": configuration.signing_identity,
            "engine_build": str(configuration.engine_build),
            "app_artifact": str(configuration.app_artifact),
            "test_hooks": configuration.test_hooks,
            "build_identity": build_identity,
            "host_evidence_identity": host_evidence_identity or "none",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_phase_adapters(
        configuration: LocalConfiguration,
        suites: Sequence[str],
        run_directory: Path,
        source_revision: str,
        run_id: str,
        terminal_stream: TextIO = sys.stderr) -> Iterable[runner.PhaseAdapter]:
    """Load phase adapters when the standalone phase registry is available."""
    try:
        import iphone_test_phases
    except ModuleNotFoundError as error:
        if error.name != "iphone_test_phases":
            raise
        return ()
    return iphone_test_phases.create_phase_adapters(
        configuration=configuration,
        suites=tuple(suites),
        run_directory=run_directory,
        source_revision=source_revision,
        run_id=run_id,
        test_app_prepared=True,
        terminal_stream=terminal_stream,
    )


def prepare_phase_artifacts(
        configuration: LocalConfiguration,
        suites: Sequence[str],
        source_revision: str,
        run_id: str,
        reuse_build: bool = False):
    """Prepare current artifacts before their identity enters a checkpoint."""
    if reuse_build:
        configuration = infer_signing_configuration(
            configuration, validate_explicit=True)
        runner.register_runtime_redaction_tokens(
            run_id, configuration.redaction_tokens())
    try:
        import iphone_test_phases
    except ModuleNotFoundError as error:
        if error.name != "iphone_test_phases":
            raise
        return None
    prepare = getattr(iphone_test_phases, "prepare_test_app", None)
    if prepare is None:
        return None
    issue = prepare(
        configuration=configuration,
        suites=tuple(suites),
        source_revision=source_revision,
        run_id=run_id,
        reuse_build=reuse_build,
    )
    if isinstance(issue, Mapping):
        configuration = infer_signing_configuration(
            configuration, validate_explicit=True)
        runner.register_runtime_redaction_tokens(
            run_id, configuration.redaction_tokens())
        if not reuse_build:
            verify = getattr(iphone_test_phases, "verify_test_app", None)
            if verify is None:
                raise IphoneTestCliError(
                    "phase adapter cannot verify the prepared App")
            verification_issue = verify(
                configuration=configuration, run_id=run_id)
            if verification_issue is not None:
                issue = verification_issue
    return PhasePreparationOutcome(issue, configuration)


def selected_suites_require_test_app(suites: Sequence[str]) -> bool:
    """Return whether selected phase contracts require a physical test App."""
    return any(suite != "inventory" for suite in suites)


def _safe_setup_call(
        operation: Callable[[], object], run_id: str) -> object:
    """Run adapter setup with captured terminal streams and safe exceptions."""
    del run_id
    saved_descriptors = []
    drain_threads = []

    def drain(descriptor: int) -> None:
        """Discard untrusted setup bytes until the redirected fd closes."""
        try:
            while os.read(descriptor, 65536):
                pass
        finally:
            os.close(descriptor)

    try:
        for descriptor in (1, 2):
            read_descriptor, write_descriptor = os.pipe()
            saved_descriptor = os.dup(descriptor)
            os.dup2(write_descriptor, descriptor)
            os.close(write_descriptor)
            saved_descriptors.append((descriptor, saved_descriptor))
            thread = threading.Thread(
                target=drain, args=(read_descriptor,), daemon=True)
            thread.start()
            drain_threads.append(thread)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return operation()
    except SystemExit as error:
        raise IphoneTestCliError(
            "phase adapter setup failed") from None
    except KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    except BaseException:
        raise IphoneTestCliError(
            "phase adapter setup failed") from None
    finally:
        for descriptor, saved_descriptor in reversed(saved_descriptors):
            os.dup2(saved_descriptor, descriptor)
            os.close(saved_descriptor)
        for thread in drain_threads:
            thread.join(timeout=5)


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
    requested_suites = set(options.suite or runner.PHASE_IDS)
    suites = tuple(
        phase_id for phase_id in runner.PHASE_IDS
        if phase_id in requested_suites)
    selection = None
    preparation_lock = None
    engine_owns_selection = False
    redaction_run_id = None
    bootstrap_redaction_run_id = None
    try:
        requires_test_app = selected_suites_require_test_app(suites)
        revision = source_commit()
        host_binaries = validate_mysqltest_host_gate(
            suites, local_environment)
        if requires_test_app:
            configuration = infer_signing_configuration(configuration)
        bootstrap_redaction_run_id = f"setup-{uuid.uuid4().hex}"
        runner.register_runtime_redaction_tokens(
            bootstrap_redaction_run_id, configuration.redaction_tokens())
        current_time = clock()
        if options.dry_run:
            dry_path = state.preview_run_path(
                options.output_root, _run_mode(options), current_time)
            dry_host_identity = "dry-run-local-host-gate"
            if state._checkpoint_exists(
                    options.output_root, dry_path.run_directory):
                dry_checkpoint = state.load_checkpoint(
                    options.output_root, dry_path.run_directory)
                dry_host_identity = dry_checkpoint.get(
                    "mysqltest_host_evidence", dry_host_identity)
            build_identity = (
                validate_build_identity(configuration, revision)
                if requires_test_app else "host-only-v1")
            fingerprint = configuration_fingerprint(
                runner_suites=suites, configuration=configuration,
                build_identity=build_identity,
                host_evidence_identity=dry_host_identity)
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
            path_preview = state.preview_run_path(
                options.output_root, _run_mode(options), current_time)
            preparation_lock = state.RunLock(
                Path(options.output_root).expanduser().absolute(),
                path_preview.run_directory)
            preparation_lock.acquire()
            locked_preview = state.preview_run_path(
                options.output_root, _run_mode(options), current_time)
            if locked_preview.run_directory != path_preview.run_directory:
                raise IphoneTestCliError(
                    "selected run changed before artifact preparation")
            print(
                f"Selected iPhone test run: {path_preview.run_directory}",
                file=stdout, flush=True)
            existing_checkpoint = None
            if (_run_mode(options) != state.RunMode.RESTART
                    and state._checkpoint_exists(
                        options.output_root, path_preview.run_directory)):
                existing_checkpoint = state.load_checkpoint(
                    options.output_root, path_preview.run_directory)
            selected_run_id = (
                existing_checkpoint["run_id"]
                if (existing_checkpoint is not None
                    and state._is_incomplete(existing_checkpoint))
                else str(uuid.uuid4()))
            host_evidence_identity = prepare_mysqltest_host_evidence(
                host_binaries, path_preview.run_directory,
                selected_run_id)
            if requires_test_app:
                selected_device = select_physical_device(
                    configuration.device, discover_physical_devices())
                configuration = replace(
                    configuration,
                    device=selected_device.identifier,
                    profile_device=selected_device.profile_identifier,
                )
                try:
                    validate_build_identity(configuration, revision)
                except IphoneTestCliError:
                    reuse_build = False
                else:
                    reuse_build = True
                setup_run_id = f"setup-{uuid.uuid4().hex}"
                runner.register_runtime_redaction_tokens(
                    setup_run_id, configuration.redaction_tokens())
                try:
                    preparation_issue = _safe_setup_call(
                        lambda: prepare_phase_artifacts(
                            configuration, suites, revision, setup_run_id,
                            reuse_build=reuse_build),
                        setup_run_id)
                finally:
                    runner.clear_runtime_redaction_tokens(setup_run_id)
                if isinstance(preparation_issue, PhasePreparationOutcome):
                    configuration = preparation_issue.configuration
                    preparation_issue = preparation_issue.issue
                if isinstance(preparation_issue, str):
                    diagnostic = PREPARATION_DIAGNOSTICS.get(
                        preparation_issue)
                    if diagnostic is None:
                        raise IphoneTestCliError(
                            "phase adapter setup failed")
                    raise IphoneTestCliError(diagnostic)
                if isinstance(preparation_issue, Mapping):
                    allowed_sources = {
                        "environment", "cmake-cache", "cargo-sibling",
                        "cargo-parent", "cargo-home-sibling"}
                    invalid_source = any(
                        not isinstance(value, str)
                        or (value not in allowed_sources
                            and not (
                                key == "legacy_build_script_cache"
                                and re.fullmatch(
                                    r"migrated-[0-9]+", value)))
                        for key, value in preparation_issue.items())
                    if (invalid_source
                            or any(not isinstance(key, str)
                                   for key in preparation_issue)):
                        raise IphoneTestCliError(
                            "phase preparation returned invalid provenance")
                    sources = ", ".join(
                        f"{key}={preparation_issue[key]}"
                        for key in sorted(preparation_issue))
                    print(
                        f"Build prerequisite sources: {sources}",
                        file=stdout, flush=True)
                elif preparation_issue is not None:
                    raise IphoneTestCliError("phase adapter setup failed")
                build_identity = validate_build_identity(
                    configuration, revision)
            else:
                build_identity = "host-only-v1"
            fingerprint = configuration_fingerprint(
                runner_suites=suites, configuration=configuration,
                build_identity=build_identity,
                host_evidence_identity=host_evidence_identity)
            selection = state.select_run(
                options.output_root,
                mode=_run_mode(options),
                source_commit=revision,
                config_fingerprint=fingerprint,
                now=current_time,
                preparation_lock=preparation_lock,
                run_id=selected_run_id,
            )
            preparation_lock = None
            if selection.run_directory != path_preview.run_directory:
                raise IphoneTestCliError(
                    "selected run changed during artifact preparation")
            if host_evidence_identity is not None:
                existing_identity = selection.checkpoint.get(
                    "mysqltest_host_evidence")
                if (existing_identity is not None
                        and existing_identity != host_evidence_identity):
                    raise IphoneTestCliError(
                        "mysqltest host evidence changed during resume")
                selection.checkpoint["mysqltest_host_evidence"] = (
                    host_evidence_identity)
                state.save_checkpoint(
                    options.output_root, selection.run_directory,
                    selection.checkpoint)

        if options.dry_run:
            if requires_test_app:
                selected_device = select_physical_device(
                    configuration.device, discover_physical_devices())
                configuration = replace(
                    configuration,
                    device=selected_device.identifier,
                    profile_device=selected_device.profile_identifier,
                )
            print("Dry run completed; no test phase was dispatched.",
                  file=stdout)
            return 0

        redaction_run_id = selection.checkpoint["run_id"]
        runner.register_runtime_redaction_tokens(
            redaction_run_id, configuration.redaction_tokens())
        adapters = _safe_setup_call(
            lambda: load_phase_adapters(
                configuration, suites, selection.run_directory,
                revision, redaction_run_id, terminal_stream=stderr),
            redaction_run_id)
        engine_owns_selection = True
        return runner.run_phase_engine(
            options.output_root, selection, adapters, now=clock,
            redaction_tokens=configuration.redaction_tokens(),
            phase_ids=suites)
    except (IphoneTestCliError, state.IphoneTestStateError) as error:
        print(f"iPhone test runner error: {error}", file=stderr)
        return 2
    except KeyboardInterrupt:
        print("iPhone test runner interrupted.", file=stderr)
        return 130
    finally:
        if selection is not None and not engine_owns_selection:
            selection.close()
        if preparation_lock is not None:
            preparation_lock.release()
        if redaction_run_id is not None:
            runner.clear_runtime_redaction_tokens(redaction_run_id)
        if bootstrap_redaction_run_id is not None:
            runner.clear_runtime_redaction_tokens(
                bootstrap_redaction_run_id)


if __name__ == "__main__":
    raise SystemExit(main())
