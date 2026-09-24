#!/usr/bin/env python3
"""Standalone adapters for iPhone validation phases one through four."""

from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Iterable, Mapping, Optional, Sequence

import iphone_test_runner as runner


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INVENTORY_SCRIPT = SCRIPT_DIRECTORY / "generate_test_inventory.py"
DEVICE_SUITE_SCRIPT = SCRIPT_DIRECTORY / "run_device_suite.py"
BUILD_SCRIPT = REPOSITORY_ROOT / "build.iphone.sh"
PACKAGE_SCRIPT = REPOSITORY_ROOT / "deps/ios-build/build_app.py"
RUSTC_WRAPPER = SCRIPT_DIRECTORY / "rustc_lldb_wrapper.py"
DEPS_PREFIX_ENVIRONMENT = "SEEKDB_IPHONE_DEPS_PREFIX"
HEADERS_PREFIX_ENVIRONMENT = "SEEKDB_IPHONE_HEADERS_PREFIX"
RUST_TARGET_DIR_ENVIRONMENT = "RUST_TARGET_DIR"
DEVICE_INNER_TIMEOUT_SECONDS = 600
DEVICE_CASE_TIMEOUT_SECONDS = DEVICE_INNER_TIMEOUT_SECONDS + 120
SQL_RESTART_INNER_TIMEOUT_SECONDS = 900
SQL_RESTART_CASE_TIMEOUT_SECONDS = SQL_RESTART_INNER_TIMEOUT_SECONDS + 120
INVENTORY_TIMEOUT_SECONDS = 300
BUILD_TIMEOUT_SECONDS = 7200
PACKAGE_TIMEOUT_SECONDS = 1800
ARTIFACT_MARKER = re.compile(
    rb"SEEKDB_IOS_ARTIFACT_BUILD_ID=([0-9a-f]{12});"
    rb"SEEKDB_IOS_ARTIFACT_HOOK_MODE=(enabled|disabled)")

CPP_CASE_IDS = (
    "ios.cpp.allocator.backend",
    "ios.cpp.allocator.lifecycle",
    "ios.cpp.allocator.realloc_alignment",
    "ios.cpp.ob_error.mapping",
)
RUST_CASE_IDS = (
    "ios.rust.cert.formats_display_name_for_sql_account",
    "ios.rust.cert.rejects_truncated_certificate",
    "ios.rust.device.intentional_panic",
    "ios.rust.device.panic_continuation",
    "ios.rust.tls.exposes_sql_cipher_names",
)
PRODUCTION_ISOLATION_CASE_ID = "ios.rust.production.symbol-isolation"
SQL_RESTART_CASE_IDS = {
    "registry-smoke": "ios.registry.sql.same-directory-restart",
    "cpp-device-equivalents": "ios.cpp.sql.same-directory-restart",
    "rust-device-runtime": "ios.rust.sql.same-directory-restart",
}
REQUIRED_DEPENDENCY_ARTIFACTS = (
    "include",
    "lib/libcrypto.a",
    "lib/libcurl.a",
    "lib/libicudata.a",
    "lib/libicui18n.a",
    "lib/libicuuc.a",
    "lib/libssl.a",
)


class PhaseEvidenceError(RuntimeError):
    """Indicate that a completed command did not produce trusted evidence."""


class PhasePreparationError(RuntimeError):
    """Indicate that current-HEAD test App preparation could not complete."""


PREPARATION_FAILURE_CODES = {
    "current-HEAD iOS engine build failed": "build-failed",
    "existing App signature validation failed": "sign-failed",
    "current-HEAD App signing or installation failed": "sign-install-failed",
    "current-HEAD App installation failed": "install-failed",
}


class BuildReadinessError(RuntimeError):
    """Indicate that portable local build prerequisites are unavailable."""


@dataclass(frozen=True)
class BuildInputs:
    """Hold validated build paths and non-sensitive provenance labels."""

    deps_prefix: Path
    headers_prefix: Path
    cargo: Path
    rustup: Path
    cargo_home: Path
    rustup_home: Path
    rust_target_dir: Path
    sources: Mapping[str, str]


def _tool_has_role(
        tool: Path, role: str,
        environment: Mapping[str, str]) -> bool:
    """Return whether one executable identifies as the requested Rust tool."""
    if not tool.is_file() or not os.access(tool, os.X_OK):
        return False
    try:
        result = subprocess.run(
            [str(tool), "--version"], check=False, capture_output=True,
            text=True, timeout=10, env=dict(environment))
    except (OSError, subprocess.SubprocessError):
        return False
    return (result.returncode == 0
            and result.stdout.strip().lower().startswith(f"{role} "))


def _rust_tool_environment(
        cargo: Path, rustup: Path, cargo_home: Path, rustup_home: Path,
        source_environment: Mapping[str, str]) -> Mapping[str, str]:
    """Build a minimal resolved environment for Rust tool role probes."""
    tool_directories = tuple(dict.fromkeys((
        str(cargo.parent), str(rustup.parent))))
    inherited_path = source_environment.get("PATH") or os.defpath
    return {
        "CARGO_HOME": str(cargo_home),
        "RUSTUP_HOME": str(rustup_home),
        "PATH": os.pathsep.join((*tool_directories, inherited_path)),
    }


def _lldb_is_available() -> bool:
    """Return whether the fixed macOS LLDB launcher is locally available."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/xcrun", "--find", "lldb"], check=False,
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    lldb = Path(result.stdout.strip())
    return lldb.is_file() and os.access(lldb, os.X_OK)


def _cache_values(cache_path: Path) -> Mapping[str, str]:
    """Read exact scalar values from one existing CMake cache."""
    if not cache_path.is_file():
        return {}
    values = {}
    for line in cache_path.read_text(
            encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or ":" not in line.split("=", 1)[0]:
            continue
        key_with_type, value = line.split("=", 1)
        key = key_with_type.split(":", 1)[0]
        values[key] = value
    return values


def resolve_build_inputs(
        configuration,
        environment: Optional[Mapping[str, str]] = None) -> BuildInputs:
    """Resolve explicit or cache-backed portable build prerequisites."""
    environment = os.environ if environment is None else environment
    cache = _cache_values(configuration.engine_build / "CMakeCache.txt")

    def choose(environment_name: str, cache_name: str):
        """Choose an explicit environment path before a cache fallback."""
        if environment.get(environment_name):
            return Path(environment[environment_name]).expanduser().resolve(), "environment"
        if cache.get(cache_name):
            return Path(cache[cache_name]).expanduser().resolve(), "cmake-cache"
        return None, "missing"

    deps_prefix, deps_source = choose(DEPS_PREFIX_ENVIRONMENT, "DEP_DIR")
    headers_prefix, headers_source = choose(
        HEADERS_PREFIX_ENVIRONMENT, "SEEKDB_IOS_HEADER_PREFIX")
    cargo, cargo_source = choose("CARGO", "CARGO")
    rust_target_dir, rust_target_source = choose(
        RUST_TARGET_DIR_ENVIRONMENT, "RUST_TARGET_DIR")
    rustup_value = environment.get("RUSTUP")
    if rustup_value:
        rustup = Path(rustup_value).expanduser().resolve()
        rustup_source = "environment"
    elif cargo is not None and cargo.with_name("rustup").is_file():
        rustup = cargo.with_name("rustup")
        rustup_source = "cargo-sibling"
    else:
        rustup = None
        rustup_source = "missing"
    cargo_home_value = environment.get("CARGO_HOME")
    if cargo_home_value:
        cargo_home = Path(cargo_home_value).expanduser().resolve()
        cargo_home_source = "environment"
    elif cargo is not None and cargo.parent.name == "bin":
        cargo_home = cargo.parent.parent
        cargo_home_source = "cargo-parent"
    else:
        cargo_home = None
        cargo_home_source = "missing"
    rustup_home_value = environment.get("RUSTUP_HOME")
    if rustup_home_value:
        rustup_home = Path(rustup_home_value).expanduser().resolve()
        rustup_home_source = "environment"
    elif cargo_home is not None:
        rustup_home = cargo_home.with_name("rustup")
        rustup_home_source = "cargo-home-sibling"
    else:
        rustup_home = None
        rustup_home_source = "missing"
    if headers_prefix is None and deps_prefix is not None:
        headers_prefix = deps_prefix
        headers_source = deps_source
    paths = (
        deps_prefix, headers_prefix, cargo, rustup, cargo_home,
        rustup_home, rust_target_dir)
    if any(path is None for path in paths):
        raise BuildReadinessError("required iOS build paths are missing")
    probe_environment = _rust_tool_environment(
        cargo, rustup, cargo_home, rustup_home, environment)
    if (cargo_source == "cmake-cache"
            and not _tool_has_role(
                cargo, "cargo", probe_environment)):
        cargo_sibling = cargo.with_name("cargo")
        sibling_environment = _rust_tool_environment(
            cargo_sibling, rustup, cargo_home, rustup_home, environment)
        if _tool_has_role(
                cargo_sibling, "cargo", sibling_environment):
            cargo = cargo_sibling
            cargo_source = "cargo-sibling"
            probe_environment = sibling_environment
    explicit_rust_target = bool(environment.get(RUST_TARGET_DIR_ENVIRONMENT))
    rust_target_ready = (
        (rust_target_dir.is_dir()
         and not _rust_target_is_incompatible(rust_target_dir))
        or (explicit_rust_target
            and not rust_target_dir.exists()
            and rust_target_dir.parent.is_dir()))
    if (not _tool_has_role(cargo, "cargo", probe_environment)
            or not _tool_has_role(
                rustup, "rustup", probe_environment)
            or not cargo_home.is_dir()
            or not rustup_home.is_dir()
            or not RUSTC_WRAPPER.is_file()
            or RUSTC_WRAPPER.is_symlink()
            or not os.access(RUSTC_WRAPPER, os.X_OK)
            or not _lldb_is_available()
            or not rust_target_ready
            or not headers_prefix.is_dir()
            or any(not (deps_prefix / relative).exists()
                   for relative in REQUIRED_DEPENDENCY_ARTIFACTS)):
        raise BuildReadinessError(
            "resolved iOS build paths do not contain required artifacts")
    cache_backed = any(source == "cmake-cache" for source in (
        deps_source, headers_source, cargo_source, rust_target_source))
    if cache_backed and (
            cache.get("CMAKE_SYSTEM_NAME") != "iOS"
            or cache.get("CMAKE_OSX_SYSROOT") != "iphoneos"
            or cache.get("CMAKE_OSX_ARCHITECTURES") != "arm64"):
        raise BuildReadinessError(
            "cached build inputs are not bound to iphoneos arm64")
    return BuildInputs(
        deps_prefix=deps_prefix,
        headers_prefix=headers_prefix,
        cargo=cargo,
        rustup=rustup,
        cargo_home=cargo_home,
        rustup_home=rustup_home,
        rust_target_dir=rust_target_dir,
        sources={
            "deps_prefix": deps_source,
            "headers_prefix": headers_source,
            "cargo": cargo_source,
            "rustup": rustup_source,
            "cargo_home": cargo_home_source,
            "rustup_home": rustup_home_source,
            "rust_target_dir": rust_target_source,
        },
    )


def _rust_target_is_incompatible(target: Path) -> bool:
    """Reject any nonempty target tree without a device ARM64 target."""
    device = target / "aarch64-apple-ios"
    return not device.exists() and next(target.iterdir(), None) is not None


@dataclass(frozen=True)
class PhaseCaseContract:
    """Describe one stable command and its evidence requirements."""

    phase_id: str
    case_id: str
    execution_class: str
    command: tuple[str, ...]
    timeout_seconds: int
    evidence_validator: Callable[[runner.SanitizedProcessResult], tuple[str, ...]]
    requires_sql_restart_followup: bool
    requires_test_app: bool = False
    requires_production_app: bool = False
    invalidates_test_app: bool = False
    readiness_error: Optional[str] = None

    def to_case_spec(self) -> runner.CaseSpec:
        """Convert adapter metadata to the runner's durable case protocol."""
        return runner.CaseSpec(
            case_id=self.case_id,
            execution_class=self.execution_class,
        )


CommandExecutor = Callable[
    [Sequence[str], int], runner.SanitizedProcessResult]


def _slug(case_id: str) -> str:
    """Return a bounded evidence filename component for one stable case ID."""
    return re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")[:80]


def _default_executor(run_id: str) -> CommandExecutor:
    """Create a subprocess boundary that redacts all captured text immediately."""
    def execute(
            command: Sequence[str],
            timeout_seconds: int) -> runner.SanitizedProcessResult:
        """Run one bounded command without persisting its secret-bearing argv."""
        try:
            completed = subprocess.run(
                list(command), cwd=REPOSITORY_ROOT, check=False,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return runner.SanitizedProcessResult(
                124, "", "command exceeded its bounded timeout")
        return runner.SanitizedProcessResult.create(
            completed.returncode, completed.stdout, completed.stderr, run_id)

    return execute


class TestAppPreparer:
    """Build, package, sign, and install one current-HEAD test-hook App."""

    def __init__(
            self, configuration, execute: CommandExecutor,
            prepared: bool = False):
        """Retain process-local configuration and a sanitized command boundary."""
        self._configuration = configuration
        self._execute = execute
        self._prepared = prepared
        self._build_input_sources = {}

    @property
    def build_input_sources(self) -> Mapping[str, str]:
        """Return only non-sensitive prerequisite provenance labels."""
        return dict(self._build_input_sources)

    def ensure(
            self, *, reuse_build: bool = False,
            repackage: bool = False) -> Optional[runner.CaseResult]:
        """Prepare or verify and install the App with local signing checks."""
        if self._prepared:
            return None
        package_output = (
            self._configuration.engine_build
            / "app/Release-iphoneos/SeekDBProbe.app")
        if self._configuration.app_artifact != package_output:
            return runner.CaseResult.blocked(
                diagnostic=(
                    "configured App path does not match the package output"))
        if not all((
                self._configuration.device,
                self._configuration.bundle_id,
                self._configuration.team)):
            return runner.CaseResult.blocked(
                diagnostic=(
                    "test App preparation requires device, bundle, and team "
                    "configuration"))
        if (self._configuration.provisioned_devices
                and (not self._configuration.profile_device
                     or self._configuration.profile_device
                     not in self._configuration.provisioned_devices)):
            return runner.CaseResult.blocked(
                diagnostic="selected device is outside the local profile scope")
        if self._configuration.profile_certificate_hashes:
            identities = subprocess.run(
                ["/usr/bin/security", "find-identity", "-v", "-p",
                 "codesigning"], check=False, capture_output=True, text=True)
            available = identities.stdout.upper() if identities.returncode == 0 else ""
            if any(available.count(identity.upper()) != 1
                   for identity in self._configuration.profile_certificate_hashes):
                return runner.CaseResult.blocked(
                    diagnostic=(
                        "the local provisioning certificate and private key "
                        "are unavailable or ambiguous"))
        if not reuse_build:
            try:
                build_inputs = resolve_build_inputs(self._configuration)
            except BuildReadinessError:
                return runner.CaseResult.blocked(
                    diagnostic=(
                        "iOS build prerequisites require explicit environment "
                        "paths or one valid CMake cache"))
            self._build_input_sources = dict(build_inputs.sources)
            build = self._execute(_build_command(
                self._configuration.engine_build,
                self._configuration.test_hooks, build_inputs),
                BUILD_TIMEOUT_SECONDS)
            if build.exit_status != 0:
                return runner.CaseResult.failed(
                    category="infrastructure",
                    diagnostic="current-HEAD iOS engine build failed",
                    exit_status=build.exit_status,
                    retry_safe=False,
                    clean_state=False,
                )
        if repackage or not reuse_build:
            package_command = [
                sys.executable, str(PACKAGE_SCRIPT),
                "--team", self._configuration.team,
                "--device", self._configuration.device,
                "--bundle-id", self._configuration.bundle_id,
                "--engine-build", str(self._configuration.engine_build),
                "--install",
            ]
            if self._configuration.test_hooks:
                package_command.append("--test-hooks")
            package = self._execute(
                tuple(package_command), PACKAGE_TIMEOUT_SECONDS)
        else:
            verification = self._execute((
                "/usr/bin/codesign", "--verify", "--deep", "--strict",
                str(self._configuration.app_artifact),
            ), PACKAGE_TIMEOUT_SECONDS)
            if verification.exit_status != 0:
                return runner.CaseResult.failed(
                    category="infrastructure",
                    diagnostic="existing App signature validation failed",
                    exit_status=verification.exit_status,
                    retry_safe=False,
                    clean_state=False,
                )
            package = self._execute((
                "xcrun", "devicectl", "device", "install", "app",
                "--device", self._configuration.device,
                "--timeout", "120", str(self._configuration.app_artifact),
            ), PACKAGE_TIMEOUT_SECONDS)
            package_failure_diagnostic = (
                "current-HEAD App installation failed")
        if repackage or not reuse_build:
            package_failure_diagnostic = (
                "current-HEAD App signing or installation failed")
        if package.exit_status != 0:
            return runner.CaseResult.failed(
                category="infrastructure",
                diagnostic=package_failure_diagnostic,
                exit_status=package.exit_status,
                retry_safe=False,
                clean_state=False,
            )
        self._prepared = True
        return None

    def invalidate(self) -> None:
        """Require a new test App after a production-mode archive build."""
        self._prepared = False


def _build_command(
        build_directory: Path, test_hooks: bool,
        inputs: BuildInputs) -> tuple[str, ...]:
    """Create one explicit build command with validated prerequisite paths."""
    hook_mode = "ON" if test_hooks else "OFF"
    return (
        "/usr/bin/env",
        f"CARGO={inputs.cargo}",
        f"RUSTUP={inputs.rustup}",
        f"CARGO_HOME={inputs.cargo_home}",
        f"RUSTUP_HOME={inputs.rustup_home}",
        f"RUST_TARGET_DIR={inputs.rust_target_dir}",
        f"RUSTC_WRAPPER={RUSTC_WRAPPER}",
        str(BUILD_SCRIPT),
        "--build-dir", str(build_directory),
        "--deps-prefix", str(inputs.deps_prefix),
        "--headers-prefix", str(inputs.headers_prefix),
        "--jobs", "4", "--target", "seekdb_ios_link_check", "--",
        f"-DSEEKDB_IOS_TEST_HOOKS={hook_mode}",
        f"-DCARGO={inputs.cargo}",
        f"-DRUST_TARGET_DIR={inputs.rust_target_dir}",
    )


def _inventory_validator(
        output_path: Path,
        source_revision: str) -> Callable[
            [runner.SanitizedProcessResult], tuple[str, ...]]:
    """Create a validator bound to one expected inventory file and revision."""
    def validate(
            _process: runner.SanitizedProcessResult) -> tuple[str, ...]:
        """Require sorted unique rows generated from the current revision."""
        if not output_path.is_file():
            raise PhaseEvidenceError("inventory evidence is missing")
        try:
            rows = [json.loads(line) for line in output_path.read_text(
                encoding="utf-8").splitlines() if line]
        except (OSError, json.JSONDecodeError) as error:
            raise PhaseEvidenceError("inventory evidence is invalid") from error
        identifiers = [row.get("id") for row in rows]
        if (not rows or identifiers != sorted(identifiers)
                or len(set(identifiers)) != len(identifiers)
                or any(row.get("source_commit") != source_revision
                       for row in rows)):
            raise PhaseEvidenceError(
                "inventory evidence does not match current source")
        return (output_path.name,)

    return validate


def _device_validator(
        run_directory: Path,
        case_id: str) -> Callable[
            [runner.SanitizedProcessResult], tuple[str, ...]]:
    """Create a validator for run_device_suite's safe JSON summary."""
    def validate(
            process: runner.SanitizedProcessResult) -> tuple[str, ...]:
        """Require one passing case and retain only allowlisted JSONL evidence."""
        try:
            summary = json.loads(process.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise PhaseEvidenceError("device suite summary is invalid") from error
        run_id = summary.get("run_id") if isinstance(summary, Mapping) else None
        case_results = summary.get("case_results") \
            if isinstance(summary, Mapping) else None
        if (not isinstance(run_id, str)
                or re.fullmatch(r"[A-Za-z0-9-]{1,64}", run_id) is None
                or case_results != {case_id: 0}
                or summary.get("run_result") != 0):
            raise PhaseEvidenceError("device suite evidence did not pass")
        source = run_directory / f"device-test-{run_id}.jsonl"
        if not source.is_file():
            raise PhaseEvidenceError("device suite JSONL evidence is missing")
        destination = run_directory / f"evidence-{_slug(case_id)}.jsonl"
        os.replace(source, destination)
        return (destination.name,)

    return validate


def _sql_restart_validator(
        run_directory: Path, evidence_prefix: str,
        configuration, source_revision: str, run_id: str,
        data_name: str, expected_hook_mode: str) -> Callable[
            [runner.SanitizedProcessResult], tuple[str, ...]]:
    """Create a validator for the two-round ordinary SQL restart gate."""
    def validate(
            process: runner.SanitizedProcessResult) -> tuple[str, ...]:
        """Require both 36-step rounds and same-directory restart success."""
        try:
            summary = json.loads(process.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise PhaseEvidenceError("SQL restart summary is invalid") from error
        expected = {
            "run_result": 0,
            "first_previous_runs": 0,
            "second_previous_runs": 1,
        }
        if summary != expected:
            raise PhaseEvidenceError("SQL restart gate did not pass")
        evidence_names = (
            f"evidence-{evidence_prefix}-first.jsonl",
            f"evidence-{evidence_prefix}-restart.jsonl",
        )
        result_names = []
        for previous_runs, name in enumerate(evidence_names):
            evidence = run_directory / name
            metadata = evidence.with_name(evidence.name + ".meta.json")
            if not evidence.is_file() or not metadata.is_file():
                raise PhaseEvidenceError("SQL restart evidence is missing")
            try:
                actual_metadata = json.loads(metadata.read_text(
                    encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise PhaseEvidenceError(
                    "SQL restart metadata is invalid") from error
            round_id = actual_metadata.get("round_id") \
                if isinstance(actual_metadata, Mapping) else None
            if (not isinstance(round_id, str)
                    or re.fullmatch(r"[A-Za-z0-9-]{1,64}", round_id) is None):
                raise PhaseEvidenceError("SQL restart round identity is invalid")
            expected_metadata = {
                "schema_version": 1,
                "runner_run_id": run_id,
                "build_id": source_revision[:12],
                "device_hash": hashlib.sha256(
                    (configuration.device or "").encode("utf-8")
                ).hexdigest(),
                "data_name": data_name,
                "hook_mode": expected_hook_mode,
                "previous_runs": previous_runs,
                "round_id": round_id,
                "evidence_sha256": _sha256_file(evidence),
            }
            if actual_metadata != expected_metadata:
                raise PhaseEvidenceError(
                    "SQL restart evidence identity does not match the run")
            result_names.extend((name, metadata.name))
        return tuple(result_names)

    return validate


def _sha256_file(path: Path) -> str:
    """Hash one evidence file without retaining its contents."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_marker(path: Path) -> Optional[tuple[bytes, bytes]]:
    """Read the first embedded runtime marker without retaining the archive."""
    tail = b""
    with path.open("rb") as artifact:
        while True:
            chunk = artifact.read(1024 * 1024)
            if not chunk:
                return None
            match = ARTIFACT_MARKER.search(tail + chunk)
            if match is not None:
                return match.group(1), match.group(2)
            tail = (tail + chunk)[-256:]


def _production_validator(
        engine_build: Path,
        source_revision: str) -> Callable[
            [runner.SanitizedProcessResult], tuple[str, ...]]:
    """Create a production marker and Rust-symbol isolation validator."""
    def validate(
            _process: runner.SanitizedProcessResult) -> tuple[str, ...]:
        """Require current hook-off runtime and no Rust device-test symbols."""
        cache = engine_build / "CMakeCache.txt"
        archive = engine_build / "src/observer/libseekdb_ios_runtime.a"
        if (not cache.is_file()
                or b"SEEKDB_IOS_TEST_HOOKS:BOOL=OFF" not in cache.read_bytes()
                or not archive.is_file()
                or _artifact_marker(archive) != (
                    source_revision[:12].encode("ascii"), b"disabled")):
            raise PhaseEvidenceError(
                "production runtime marker validation failed")
        link_file = (
            engine_build
            / "src/observer/CMakeFiles/seekdb_ios_link_check.dir/link.txt")
        if not link_file.is_file():
            raise PhaseEvidenceError("production link response is missing")
        spec = importlib.util.spec_from_file_location(
            "iphone_test_build_app", PACKAGE_SCRIPT)
        if spec is None or spec.loader is None:
            raise PhaseEvidenceError("production symbol validator is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        arguments = module.engine_link_arguments(
            link_file.read_text(encoding="utf-8"),
            engine_build / "src/observer")
        module.require_rust_archive_mode(arguments, False)
        return ()

    return validate


def _device_command(
        configuration, run_directory: Path, suite: str,
        case_id: str) -> tuple[str, ...]:
    """Build one process-local invocation of the established device runner."""
    return (
        sys.executable, str(DEVICE_SUITE_SCRIPT),
        "--device", configuration.device or "",
        "--bundle-id", configuration.bundle_id or "",
        "--suite", suite,
        "--filter", case_id,
        "--expected-case", case_id,
        "--data-name", f"standalone-{_slug(case_id)}"[:64],
        "--timeout", str(DEVICE_INNER_TIMEOUT_SECONDS),
        "--output-dir", str(run_directory),
    )


def _sql_restart_command(
        configuration, run_directory: Path,
        evidence_prefix: str, run_id: str,
        production: bool) -> tuple[str, ...]:
    """Build one two-round ordinary SQL and same-directory restart command."""
    data_name = f"standalone-{evidence_prefix}"[:64]
    return (
        sys.executable, str(DEVICE_SUITE_SCRIPT),
        "--device", configuration.device or "",
        "--bundle-id", configuration.bundle_id or "",
        "--data-name", data_name,
        "--timeout", str(SQL_RESTART_INNER_TIMEOUT_SECONDS),
        "--output-dir", str(run_directory),
        "--evidence-prefix", evidence_prefix,
        "--expected-hook-mode", "disabled" if production else "enabled",
        "--runner-run-id", run_id,
        "--sql-restart",
    )


def _sql_restart_contract(
        phase_id: str, configuration, run_directory: Path,
        source_revision: str, run_id: str,
        *, production: bool = False) -> PhaseCaseContract:
    """Create one non-optional ordinary SQL persistence gate for a phase."""
    run_scope = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    evidence_prefix = f"{phase_id.replace('-', '_')}-{run_scope}"[:64]
    data_name = f"standalone-{evidence_prefix}"[:64]
    expected_hook_mode = "disabled" if production else "enabled"
    return PhaseCaseContract(
        phase_id=phase_id,
        case_id=SQL_RESTART_CASE_IDS[phase_id],
        execution_class="device-native",
        command=_sql_restart_command(
            configuration, run_directory, evidence_prefix, run_id,
            production),
        timeout_seconds=SQL_RESTART_CASE_TIMEOUT_SECONDS,
        evidence_validator=_sql_restart_validator(
            run_directory, evidence_prefix, configuration,
            source_revision=source_revision, run_id=run_id, data_name=data_name,
            expected_hook_mode=expected_hook_mode),
        requires_sql_restart_followup=False,
        requires_test_app=True,
        requires_production_app=production,
    )


def create_phase_contracts(
        *, configuration, suites: Sequence[str], run_directory: Path,
        source_revision: str,
        run_id: str = "contract") -> Mapping[
            str, tuple[PhaseCaseContract, ...]]:
    """Return ordered metadata for requested completed standalone phases."""
    run_directory = Path(run_directory)
    selected = set(suites)
    inventory_output = run_directory / "evidence-inventory.jsonl"
    production_build = configuration.engine_build.with_name(
        f"{configuration.engine_build.name}_production")
    try:
        production_inputs = resolve_build_inputs(configuration)
    except BuildReadinessError:
        production_command = (
            str(BUILD_SCRIPT), "--build-dir", str(production_build),
            "--", "-DSEEKDB_IOS_TEST_HOOKS=OFF")
        production_readiness = (
            "production isolation requires explicit validated build inputs")
    else:
        production_command = _build_command(
            production_build, False, production_inputs)
        production_readiness = None
    all_contracts = {
        "inventory": (
            PhaseCaseContract(
                phase_id="inventory",
                case_id="ios.inventory.generate",
                execution_class="host-only",
                command=(
                    sys.executable, str(INVENTORY_SCRIPT),
                    "--repo-root", str(REPOSITORY_ROOT),
                    "--classification",
                    str(SCRIPT_DIRECTORY / "ios-test-classification.json"),
                    "--output", str(inventory_output),
                ),
                timeout_seconds=INVENTORY_TIMEOUT_SECONDS,
                evidence_validator=_inventory_validator(
                    inventory_output, source_revision),
                requires_sql_restart_followup=False,
            ),
        ),
        "registry-smoke": (
            PhaseCaseContract(
                phase_id="registry-smoke",
                case_id="ios.registry.smoke",
                execution_class="device-native",
                command=_device_command(
                    configuration, run_directory, "smoke",
                    "ios.registry.smoke"),
                timeout_seconds=DEVICE_CASE_TIMEOUT_SECONDS,
                evidence_validator=_device_validator(
                    run_directory, "ios.registry.smoke"),
                requires_sql_restart_followup=True,
                requires_test_app=True,
            ),
            _sql_restart_contract(
                "registry-smoke", configuration, run_directory,
                source_revision, run_id),
        ),
        "cpp-device-equivalents": (
            *(PhaseCaseContract(
                phase_id="cpp-device-equivalents",
                case_id=case_id,
                execution_class="device-native",
                command=_device_command(
                    configuration, run_directory, "cpp", case_id),
                timeout_seconds=DEVICE_CASE_TIMEOUT_SECONDS,
                evidence_validator=_device_validator(run_directory, case_id),
                requires_sql_restart_followup=True,
                requires_test_app=True,
            )
              for case_id in CPP_CASE_IDS),
            _sql_restart_contract(
                "cpp-device-equivalents", configuration, run_directory,
                source_revision, run_id),
        ),
        "rust-device-runtime": (
            *(
                PhaseCaseContract(
                    phase_id="rust-device-runtime",
                    case_id=case_id,
                    execution_class="device-native",
                    command=_device_command(
                        configuration, run_directory, "rust", case_id),
                    timeout_seconds=DEVICE_CASE_TIMEOUT_SECONDS,
                    evidence_validator=_device_validator(
                        run_directory, case_id),
                    requires_sql_restart_followup=True,
                    requires_test_app=True,
                )
                for case_id in RUST_CASE_IDS
            ),
            PhaseCaseContract(
                phase_id="rust-device-runtime",
                case_id=PRODUCTION_ISOLATION_CASE_ID,
                execution_class="host-only",
                command=production_command,
                timeout_seconds=BUILD_TIMEOUT_SECONDS,
                evidence_validator=_production_validator(
                    production_build, source_revision),
                requires_sql_restart_followup=False,
                invalidates_test_app=True,
                readiness_error=production_readiness,
            ),
            _sql_restart_contract(
                "rust-device-runtime", configuration, run_directory,
                source_revision, run_id, production=True),
        ),
    }
    return {
        phase_id: cases for phase_id, cases in all_contracts.items()
        if phase_id in selected
    }


def create_phase_adapters(
        *, configuration, suites: Sequence[str], run_directory: Path,
        source_revision: str, run_id: str = "standalone-phase-setup",
        command_executor: Optional[CommandExecutor] = None,
        test_app_prepared: bool = False,
        ) -> Iterable[runner.PhaseAdapter]:
    """Create executable runner adapters without serializing local secrets."""
    execute = command_executor or _default_executor(run_id)
    contracts = create_phase_contracts(
        configuration=configuration,
        suites=suites,
        run_directory=run_directory,
        source_revision=source_revision,
        run_id=run_id,
    )
    preparer = TestAppPreparer(
        configuration, execute, prepared=test_app_prepared)
    reuse_prepared_build = test_app_prepared
    production_build = configuration.engine_build.with_name(
        f"{configuration.engine_build.name}_production")
    production_configuration = replace(
        configuration,
        engine_build=production_build,
        app_artifact=(
            production_build / "app/Release-iphoneos/SeekDBProbe.app"),
        test_hooks=False,
    )
    production_preparer = TestAppPreparer(
        production_configuration, execute)

    def execute_contract(
            contract: PhaseCaseContract) -> runner.CaseResult:
        """Execute preparation, command, and evidence validation safely."""
        if contract.requires_production_app:
            preparation_failure = production_preparer.ensure(
                reuse_build=True, repackage=True)
            if preparation_failure is not None:
                return preparation_failure
        elif contract.requires_test_app:
            preparation_failure = preparer.ensure(
                reuse_build=reuse_prepared_build)
            if preparation_failure is not None:
                return preparation_failure
        if contract.readiness_error is not None:
            return runner.CaseResult.blocked(
                diagnostic=contract.readiness_error)
        process = execute(contract.command, contract.timeout_seconds)
        if process.exit_status != 0:
            category = "timeout" if process.exit_status == 124 else "assertion"
            return runner.CaseResult.failed(
                category=category,
                diagnostic="standalone phase command failed",
                exit_status=process.exit_status,
                retry_safe=category == "assertion",
                clean_state=category == "assertion",
            )
        try:
            evidence_paths = contract.evidence_validator(process)
        except Exception:
            return runner.CaseResult.failed(
                category="evidence",
                diagnostic="standalone phase evidence validation failed",
                exit_status=process.exit_status,
                retry_safe=False,
                clean_state=False,
            )
        if contract.invalidates_test_app:
            preparer.invalidate()
        return runner.CaseResult.passed(
            exit_status=process.exit_status,
            evidence_paths=evidence_paths,
            details={
                "requires_sql_restart_followup":
                    contract.requires_sql_restart_followup,
            },
        )

    adapters = []
    for phase_id, phase_contracts in contracts.items():
        by_case = {contract.case_id: contract for contract in phase_contracts}

        def dispatch(
                spec: runner.CaseSpec,
                case_contracts=by_case) -> runner.CaseResult:
            """Dispatch one runner case to its immutable command contract."""
            return execute_contract(case_contracts[spec.case_id])

        adapters.append(runner.PhaseAdapter(
            phase_id=phase_id,
            cases=tuple(
                contract.to_case_spec() for contract in phase_contracts),
            execute=dispatch,
        ))
    return tuple(adapters)


def prepare_test_app(
        *, configuration, suites: Sequence[str],
        source_revision: str, run_id: str,
        reuse_build: bool = False):
    """Build and install current-HEAD test artifacts before checkpointing."""
    del suites, source_revision
    preparer = TestAppPreparer(
        configuration, _default_executor(run_id))
    failure = preparer.ensure(reuse_build=reuse_build)
    if failure is None:
        return preparer.build_input_sources
    if failure.status != "blocked":
        failure_code = PREPARATION_FAILURE_CODES.get(failure.diagnostic)
        if failure_code is not None:
            return failure_code
        raise PhasePreparationError("test App preparation command failed")
    if "device, bundle, and team" in failure.diagnostic:
        return "signing-config"
    if "build prerequisites" in failure.diagnostic:
        return "build-inputs"
    if "package output" in failure.diagnostic:
        return "app-output"
    return "local-profile"


def verify_test_app(*, configuration, run_id: str):
    """Revalidate and install a newly packaged App using enriched profile data."""
    failure = TestAppPreparer(
        configuration, _default_executor(run_id)).ensure(reuse_build=True)
    if failure is None:
        return None
    if failure.status == "blocked":
        return "local-profile"
    failure_code = PREPARATION_FAILURE_CODES.get(failure.diagnostic)
    if failure_code is not None:
        return failure_code
    raise PhasePreparationError("prepared App verification or install failed")
