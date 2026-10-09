#!/usr/bin/env python3
"""Standalone adapters for iPhone validation phases one through four."""

from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable, Mapping, Optional, Sequence

import iphone_test_runner as runner
import run_mysqltest_phase
import run_extended_iphone_tests as extended
import rustc_lldb_wrapper


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INVENTORY_SCRIPT = SCRIPT_DIRECTORY / "generate_test_inventory.py"
DEVICE_SUITE_SCRIPT = SCRIPT_DIRECTORY / "run_device_suite.py"
MYSQLTEST_PHASE_SCRIPT = SCRIPT_DIRECTORY / "run_mysqltest_phase.py"
BUILD_SCRIPT = REPOSITORY_ROOT / "build.iphone.sh"
PACKAGE_SCRIPT = REPOSITORY_ROOT / "deps/ios-build/build_app.py"
RUSTC_WRAPPER = SCRIPT_DIRECTORY / "rustc_lldb_wrapper.py"
DEPS_PREFIX_ENVIRONMENT = "SEEKDB_IPHONE_DEPS_PREFIX"
HEADERS_PREFIX_ENVIRONMENT = "SEEKDB_IPHONE_HEADERS_PREFIX"
RUST_TARGET_DIR_ENVIRONMENT = "RUST_TARGET_DIR"
DEFAULT_XCODE_DEVELOPER_DIR = "/Applications/Xcode.app/Contents/Developer"
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
LEGACY_QUARANTINE_DIRECTORY = ".seekdb-ios-runner-quarantine"
CARGO_PROFILE_DIRECTORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CARGO_BUILD_DIRECTORY = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.-]*-(?P<hash>[0-9a-f]{8,64})")
TRACKED_LAUNCHER_MARKER = b"--run-build-script"

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
    "mysqltest": "ios.mysqltest.sql.same-directory-restart",
    "vector": "ios.vector.sql.same-directory-restart",
    "lifecycle-memory": "ios.lifecycle.sql.same-directory-restart",
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
SAFE_PROCESS_DIAGNOSTIC_LINES = frozenset((
    "Unlock the iPhone and keep the screen awake; retrying…",
    "device launch failed: iPhone remained locked",
    "device launch failed: iPhone is disconnected",
    "device launch failed: test App is not installed",
    "device launch failed: iPhone trust or pairing is unavailable",
    "device launch failed: iPhone Developer Mode is unavailable",
    "device launch failed",
    "command exceeded its bounded timeout",
))
UNLOCK_RETRY_PROMPT = (
    "Unlock the iPhone and keep the screen awake; retrying…")
PROCESS_TERMINATION_GRACE_SECONDS = 0.5
PIPE_DRAIN_CLEANUP_SECONDS = 1.0


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


def validated_xcode_environment(
        environment: Optional[Mapping[str, str]] = None,
        run_command: Callable[..., object] = subprocess.run
        ) -> Mapping[str, str]:
    """Return an inherited environment bound to one complete Xcode install."""
    if sys.platform != "darwin":
        raise BuildReadinessError("full Xcode tools are unavailable")
    command_environment = dict(
        os.environ if environment is None else environment)
    command_environment.setdefault(
        "DEVELOPER_DIR", DEFAULT_XCODE_DEVELOPER_DIR)
    developer_value = command_environment.get("DEVELOPER_DIR", "")
    developer = Path(developer_value).expanduser()
    if (not developer_value or not developer.is_absolute()
            or not developer.is_dir()):
        raise BuildReadinessError("full Xcode tools are unavailable")
    probes = (
        (["/usr/bin/xcrun", "--find", "devicectl"], "executable"),
        (["/usr/bin/xcrun", "--find", "lldb"], "executable"),
        (["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
         "directory"),
    )
    for command, expected_kind in probes:
        try:
            result = run_command(
                command, check=False, capture_output=True, text=True,
                timeout=10, env=command_environment)
        except (OSError, subprocess.SubprocessError) as error:
            raise BuildReadinessError(
                "full Xcode tools are unavailable") from error
        output = result.stdout.strip()
        resolved = Path(output)
        valid = result.returncode == 0 and bool(output) and (
            (resolved.is_file() and os.access(resolved, os.X_OK))
            if expected_kind == "executable" else resolved.is_dir())
        if not valid:
            raise BuildReadinessError("full Xcode tools are unavailable")
    return command_environment


def _lldb_is_available(
        environment: Optional[Mapping[str, str]] = None) -> bool:
    """Return whether one complete Xcode command environment is available."""
    try:
        validated_xcode_environment(environment)
    except BuildReadinessError:
        return False
    return True


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
            or not _lldb_is_available(environment)
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


def _fsync_directory(directory: Path) -> None:
    """Durably publish changes made to one existing directory."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory(root: Path, relative: Path) -> Path:
    """Create a direct non-symlink directory chain and fsync each parent."""
    current = root
    for component in relative.parts:
        child = current / component
        try:
            child.mkdir(mode=0o700)
        except FileExistsError:
            pass
        status = child.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise BuildReadinessError(
                "legacy build-script quarantine path is unsafe")
        _fsync_directory(current)
        current = child
    return current


def _tracked_launcher_real(candidate: Path) -> Optional[Path]:
    """Return the validated preserved executable named by a tracked launcher."""
    try:
        with candidate.open("rb") as stream:
            source = stream.read(4097)
    except OSError as error:
        raise BuildReadinessError(
            "Cargo build-script launcher is unreadable") from error
    if TRACKED_LAUNCHER_MARKER not in source:
        return None
    if len(source) > 4096:
        raise BuildReadinessError(
            "Cargo build-script launcher exceeds its fixed bound")
    try:
        lines = source.decode("utf-8").splitlines()
        tokens = shlex.split(lines[1]) if len(lines) == 2 else []
    except (UnicodeDecodeError, ValueError) as error:
        raise BuildReadinessError(
            "Cargo build-script launcher is malformed") from error
    if (len(tokens) != 5 or lines[0] != "#!/bin/sh"
            or tokens[0] != "exec"
            or Path(tokens[1]).resolve() != RUSTC_WRAPPER.resolve()
            or tokens[2] != "--run-build-script"
            or tokens[4] != "$@"):
        raise BuildReadinessError(
            "Cargo build-script launcher is not the tracked wrapper")
    preserved = Path(tokens[3])
    if (not preserved.is_absolute() or preserved.is_symlink()
            or preserved.parent.resolve() != candidate.parent.resolve()
            or rustc_lldb_wrapper.REAL_BUILD_SCRIPT_NAME.fullmatch(
                preserved.name) is None
            or not rustc_lldb_wrapper._is_host_macho_executable(preserved)):
        raise BuildReadinessError(
            "Cargo build-script launcher has invalid preserved state")
    return preserved


def legacy_quarantine_path(
        target: Path, run_id: str, candidate: Path) -> Path:
    """Return the recoverable in-target destination for one raw launcher."""
    try:
        relative = candidate.relative_to(target)
    except ValueError as error:
        raise BuildReadinessError(
            "legacy build-script path escaped the Rust target") from error
    scope = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return (target / LEGACY_QUARANTINE_DIRECTORY / scope / "payload"
            / relative)


def _migration_manifest_path(target: Path, run_id: str) -> Path:
    """Return the durable manifest path for one run-scoped migration."""
    scope = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return target / LEGACY_QUARANTINE_DIRECTORY / scope / "migration.json"


def _write_migration_manifest(
        target: Path, manifest: Path,
        migrations: Sequence[tuple[Path, Path]]) -> None:
    """Durably record every source and destination before the first move."""
    manifest_parent = _durable_directory(
        target, manifest.parent.relative_to(target))
    if os.path.lexists(manifest):
        raise BuildReadinessError(
            "legacy build-script migration manifest is occupied")
    payload = {
        "schema_version": 1,
        "entries": [
            {
                "source": str(source.relative_to(target)),
                "destination": str(destination.relative_to(target)),
                "kind": "directory" if source.is_dir() else "file",
            }
            for source, destination in migrations
        ],
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=manifest_parent,
                prefix=".migration-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
        temporary = None
        _fsync_directory(manifest_parent)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _manifest_migrations(
        target: Path, manifest: Path
        ) -> tuple[tuple[Path, Path, str], ...]:
    """Validate and return direct target-relative manifest entries."""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildReadinessError(
            "legacy build-script migration manifest is invalid") from error
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("entries"), list)
            or not payload["entries"]):
        raise BuildReadinessError(
            "legacy build-script migration manifest is invalid")
    migrations = []
    payload_root = manifest.parent / "payload"
    for entry in payload["entries"]:
        if (not isinstance(entry, dict)
                or set(entry) != {"source", "destination", "kind"}
                or not all(isinstance(value, str) for value in entry.values())):
            raise BuildReadinessError(
                "legacy build-script migration manifest is invalid")
        source_relative = Path(entry["source"])
        destination_relative = Path(entry["destination"])
        kind = entry["kind"]
        if (source_relative.is_absolute()
                or destination_relative.is_absolute()
                or ".." in source_relative.parts
                or ".." in destination_relative.parts
                or not source_relative.parts
                or source_relative.parts[0] == LEGACY_QUARANTINE_DIRECTORY
                or kind not in {"file", "directory"}):
            raise BuildReadinessError(
                "legacy build-script migration manifest path is unsafe")
        source = target / source_relative
        destination = target / destination_relative
        try:
            destination.relative_to(payload_root)
        except ValueError as error:
            raise BuildReadinessError(
                "legacy build-script migration destination is unsafe") from error
        if source.parts[:len(target.parts)] != target.parts:
            raise BuildReadinessError(
                "legacy build-script migration source is unsafe")
        migrations.append((source, destination, kind))
    if len(set(migrations)) != len(migrations):
        raise BuildReadinessError(
            "legacy build-script migration manifest has duplicates")
    return tuple(migrations)


def _complete_migration_manifest(target: Path, manifest: Path) -> None:
    """Complete a durable migration transaction or fail on ambiguity."""
    migrations = _manifest_migrations(target, manifest)
    for source, destination, kind in migrations:
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        if source_exists and destination_exists:
            raise BuildReadinessError(
                "legacy build-script migration has duplicate state")
        if not source_exists and not destination_exists:
            raise BuildReadinessError(
                "legacy build-script migration lost an entry")
        if destination_exists:
            continue
        source_status = source.lstat()
        if (stat.S_ISLNK(source_status.st_mode)
                or (kind == "file" and not stat.S_ISREG(source_status.st_mode))
                or (kind == "directory"
                    and not stat.S_ISDIR(source_status.st_mode))):
            raise BuildReadinessError(
                "legacy build-script migration source changed type")
        destination_parent = _durable_directory(
            target, destination.parent.relative_to(target))
        os.replace(source, destination)
        _fsync_directory(source.parent)
        _fsync_directory(destination_parent)
    manifest.unlink()
    _fsync_directory(manifest.parent)


def _recover_pending_migrations(target: Path) -> None:
    """Finish every prior durable transaction before scanning Cargo state."""
    quarantine = target / LEGACY_QUARANTINE_DIRECTORY
    if not os.path.lexists(quarantine):
        return
    quarantine_status = quarantine.lstat()
    if (stat.S_ISLNK(quarantine_status.st_mode)
            or not stat.S_ISDIR(quarantine_status.st_mode)):
        raise BuildReadinessError(
            "legacy build-script quarantine root is unsafe")
    for scope in quarantine.iterdir():
        scope_status = scope.lstat()
        if (re.fullmatch(r"[0-9a-f]{16}", scope.name) is None
                or stat.S_ISLNK(scope_status.st_mode)
                or not stat.S_ISDIR(scope_status.st_mode)):
            raise BuildReadinessError(
                "legacy build-script quarantine scope is unsafe")
        manifest = scope / "migration.json"
        if os.path.lexists(manifest):
            if manifest.is_symlink() or not manifest.is_file():
                raise BuildReadinessError(
                    "legacy build-script migration manifest is unsafe")
            _complete_migration_manifest(target, manifest)


def _unit_migrations(
        target: Path, profile: Path, crate: Path,
        unit_hash: str, run_id: str) -> Optional[tuple[tuple[Path, Path], ...]]:
    """Return every cache entry required to invalidate one stale Cargo unit."""
    final = crate / "build-script-build"
    if not os.path.lexists(final):
        return None
    final_status = final.lstat()
    if (stat.S_ISLNK(final_status.st_mode)
            or not stat.S_ISREG(final_status.st_mode)):
        raise BuildReadinessError(
            "Cargo build-script cache candidate is unsafe")
    hashed = crate / f"build_script_build-{unit_hash}"
    expected_real = hashed.with_name(f"{hashed.name}.real")
    final_real = _tracked_launcher_real(final)
    hashed_real = None
    if os.path.lexists(hashed):
        hashed_status = hashed.lstat()
        if (stat.S_ISLNK(hashed_status.st_mode)
                or not stat.S_ISREG(hashed_status.st_mode)):
            raise BuildReadinessError(
                "Cargo rustc build-script output is unsafe")
        hashed_real = _tracked_launcher_real(hashed)
    if (final_real == expected_real and hashed_real == expected_real):
        return None
    if (final_real is None
            and not rustc_lldb_wrapper._is_host_macho_executable(final)):
        return None
    entries = [final]
    if os.path.lexists(hashed):
        if (hashed_real is None
                and not rustc_lldb_wrapper._is_host_macho_executable(hashed)):
            raise BuildReadinessError(
                "Cargo rustc build-script output is invalid")
        entries.append(hashed)
    if os.path.lexists(expected_real):
        stale_status = expected_real.lstat()
        if (stat.S_ISLNK(stale_status.st_mode)
                or not stat.S_ISREG(stale_status.st_mode)
                or not rustc_lldb_wrapper._is_host_macho_executable(
                    expected_real)):
            raise BuildReadinessError(
                "Cargo rustc build-script preserved output is unsafe")
        entries.append(expected_real)
    fingerprint = profile / ".fingerprint" / crate.name
    if os.path.lexists(fingerprint):
        fingerprint_status = fingerprint.lstat()
        if (stat.S_ISLNK(fingerprint_status.st_mode)
                or not stat.S_ISDIR(fingerprint_status.st_mode)):
            raise BuildReadinessError(
                "Cargo build-script fingerprint is unsafe")
        entries.append(fingerprint)
    return tuple(
        (entry, legacy_quarantine_path(target, run_id, entry))
        for entry in entries)


def migrate_legacy_raw_build_scripts(target: Path, run_id: str) -> int:
    """Transactionally quarantine stale Cargo build-script units."""
    if not target.exists():
        return 0
    if not target.is_absolute() or target.is_symlink() or not target.is_dir():
        raise BuildReadinessError("Rust target path is unsafe")
    resolved_target = target.resolve(strict=True)
    if resolved_target != target:
        raise BuildReadinessError("Rust target path is not canonical")
    _recover_pending_migrations(target)
    migrations = []
    migrated_units = 0
    for profile in target.iterdir():
        if (CARGO_PROFILE_DIRECTORY.fullmatch(profile.name) is None
                or profile.name == LEGACY_QUARANTINE_DIRECTORY):
            continue
        profile_status = profile.lstat()
        if stat.S_ISLNK(profile_status.st_mode):
            raise BuildReadinessError("Cargo profile path is unsafe")
        if not stat.S_ISDIR(profile_status.st_mode):
            continue
        build_root = profile / "build"
        if not os.path.lexists(build_root):
            continue
        build_status = build_root.lstat()
        if (stat.S_ISLNK(build_status.st_mode)
                or not stat.S_ISDIR(build_status.st_mode)):
            raise BuildReadinessError(
                "Cargo build-script cache path is unsafe")
        for crate in build_root.iterdir():
            crate_match = CARGO_BUILD_DIRECTORY.fullmatch(crate.name)
            if crate_match is None:
                continue
            crate_status = crate.lstat()
            if (stat.S_ISLNK(crate_status.st_mode)
                    or not stat.S_ISDIR(crate_status.st_mode)):
                raise BuildReadinessError(
                    "Cargo build-script cache path is unsafe")
            unit = _unit_migrations(
                target, profile, crate, crate_match.group("hash"), run_id)
            if unit is not None:
                migrations.extend(unit)
                migrated_units += 1
    if not migrations:
        return 0
    destinations = [destination for _source, destination in migrations]
    if (len(set(destinations)) != len(destinations)
            or any(os.path.lexists(path) for path in destinations)):
        raise BuildReadinessError(
            "legacy build-script quarantine destination is occupied")
    manifest = _migration_manifest_path(target, run_id)
    _write_migration_manifest(target, manifest, migrations)
    _complete_migration_manifest(target, manifest)
    return migrated_units


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


def _terminate_process_group(process: subprocess.Popen) -> bool:
    """Bound termination and reaping of one isolated process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        process_was_reaped = True
    except subprocess.TimeoutExpired:
        process_was_reaped = False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if not process_was_reaped:
        try:
            process.wait(timeout=PIPE_DRAIN_CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            return False
    return True


def _drain_process_pipes(
        selector: selectors.BaseSelector,
        captured: Mapping[str, bytearray],
        stderr_line_buffer: bytearray,
        deadline: float,
        terminal_stream=None) -> bool:
    """Drain registered binary pipes until EOF or one absolute deadline."""
    prompt = UNLOCK_RETRY_PROMPT.encode("utf-8")
    prompt_was_echoed = terminal_stream is None
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        events = selector.select(timeout=remaining)
        if not events:
            return False
        for key, _mask in events:
            try:
                chunk = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            captured[key.data].extend(chunk)
            if key.data != "stderr" or prompt_was_echoed:
                continue
            stderr_line_buffer.extend(chunk)
            while b"\n" in stderr_line_buffer:
                line, _, remainder = stderr_line_buffer.partition(b"\n")
                stderr_line_buffer[:] = remainder
                if line.endswith(b"\r"):
                    line = line[:-1]
                if line != prompt:
                    continue
                try:
                    terminal_stream.write(f"{UNLOCK_RETRY_PROMPT}\n")
                    terminal_stream.flush()
                except (OSError, ValueError):
                    terminal_stream = None
                prompt_was_echoed = True
                break
    return True


def _close_process_pipes(
        selector: selectors.BaseSelector,
        process: subprocess.Popen) -> None:
    """Unregister and normally close every pipe still owned by the executor."""
    for key in tuple(selector.get_map().values()):
        try:
            selector.unregister(key.fileobj)
        except (KeyError, OSError, ValueError):
            pass
        try:
            key.fileobj.close()
        except (OSError, ValueError):
            pass
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _finish_process_pipe_ownership(
        selector: selectors.BaseSelector,
        process: subprocess.Popen) -> None:
    """Release selector registrations and owner streams exactly once."""
    try:
        _close_process_pipes(selector, process)
    finally:
        selector.close()


def _default_executor(
        run_id: str, terminal_stream=None) -> CommandExecutor:
    """Create a redacting subprocess boundary with one safe live prompt."""
    command_environment = validated_xcode_environment()
    live_terminal = sys.stderr if terminal_stream is None else terminal_stream
    if runner.sanitize_diagnostic(UNLOCK_RETRY_PROMPT, run_id) != (
            UNLOCK_RETRY_PROMPT):
        live_terminal = None

    def execute(
            command: Sequence[str],
            timeout_seconds: int) -> runner.SanitizedProcessResult:
        """Run one bounded command without persisting its secret-bearing argv."""
        process = subprocess.Popen(
            list(command), cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=command_environment, start_new_session=True)
        captured = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        stderr_line_buffer = bytearray()
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        try:
            for stream, name in (
                    (process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if not _drain_process_pipes(
                    selector, captured, stderr_line_buffer,
                    deadline, live_terminal):
                timed_out = True
                _terminate_process_group(process)
                exit_status = 124
            else:
                exit_status = process.wait(
                    timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process)
            exit_status = 124
        except BaseException:
            _terminate_process_group(process)
            try:
                _drain_process_pipes(
                    selector, captured, stderr_line_buffer,
                    time.monotonic() + PIPE_DRAIN_CLEANUP_SECONDS)
            except (OSError, ValueError):
                pass
            _finish_process_pipe_ownership(selector, process)
            raise
        if timed_out:
            try:
                _drain_process_pipes(
                    selector, captured, stderr_line_buffer,
                    time.monotonic() + PIPE_DRAIN_CLEANUP_SECONDS)
            except (OSError, ValueError):
                pass
        _finish_process_pipe_ownership(selector, process)
        stdout = bytes(captured["stdout"]).decode("utf-8", errors="replace")
        stderr = bytes(captured["stderr"]).decode("utf-8", errors="replace")
        if timed_out:
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += "command exceeded its bounded timeout"
        return runner.SanitizedProcessResult.create(
            exit_status, stdout, stderr, run_id)

    return execute


def _process_failure_diagnostic(
        process: runner.SanitizedProcessResult, run_id: str,
        redaction_tokens: Iterable[str], fallback: str) -> str:
    """Return only allowlisted subprocess diagnostics after second redaction."""
    safe_lines = []
    for stream in (process.stderr, process.stdout):
        for line in str(stream).splitlines():
            normalized = line.strip()
            if normalized in SAFE_PROCESS_DIAGNOSTIC_LINES:
                safe_lines.append(normalized)
    diagnostic = "; ".join(safe_lines) if safe_lines else fallback
    for token in redaction_tokens:
        if token:
            diagnostic = diagnostic.replace(str(token), runner.REDACTED)
    return runner.sanitize_diagnostic(diagnostic, run_id)


class TestAppPreparer:
    """Build, package, sign, and install one current-HEAD test-hook App."""

    def __init__(
            self, configuration, execute: CommandExecutor,
            prepared: bool = False,
            run_id: str = "standalone-phase-setup"):
        """Retain process-local configuration and a sanitized command boundary."""
        self._configuration = configuration
        self._execute = execute
        self._prepared = prepared
        self._run_id = run_id
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
                migrated_count = migrate_legacy_raw_build_scripts(
                    build_inputs.rust_target_dir, self._run_id)
            except BuildReadinessError:
                return runner.CaseResult.blocked(
                    diagnostic=(
                        "iOS build prerequisites require explicit environment "
                        "paths or one valid CMake cache"))
            self._build_input_sources = dict(build_inputs.sources)
            self._build_input_sources["legacy_build_script_cache"] = (
                f"migrated-{migrated_count}")
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
        str(BUILD_SCRIPT), "release",
        "--build-dir", str(build_directory),
        "--deps-prefix", str(inputs.deps_prefix),
        "--headers-prefix", str(inputs.headers_prefix),
        "--jobs", "4", "--target", "seekdb_ios_link_check", "--",
        f"-DSEEKDB_IOS_TEST_HOOKS={hook_mode}",
        "-DOB_ENABLE_STANDBY=OFF",
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
        source_revision: str, run_directory: Path, run_id: str) -> Callable[
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
        rust_archive = module.require_rust_archive_mode(arguments, False)
        evidence_name = "evidence-rust-production-symbol-isolation.json"
        evidence = {
            "schema_version": 1,
            "run_id": run_id,
            "source_commit": source_revision,
            "build_id": source_revision[:12],
            "hook_mode": "disabled",
            "runtime_archive_sha256": _sha256_file(archive),
            "rust_archive_sha256": _sha256_file(rust_archive),
            "link_response_sha256": _sha256_file(link_file),
            "rust_device_test_symbols_absent": True,
            "checked_symbols": ["nio_device_test_count", "_nio_device_test_count"],
        }
        (run_directory / evidence_name).write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        return (evidence_name,)

    return validate


def _mysqltest_host_validator(
        output_path: Path) -> Callable[
            [runner.SanitizedProcessResult], tuple[str, ...]]:
    """Require a separate exact-coverage host mysqltest gate summary."""
    def validate(
            _process: runner.SanitizedProcessResult) -> tuple[str, ...]:
        """Validate the bounded host-only summary without merging device claims."""
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PhaseEvidenceError(
                "host mysqltest gate evidence is invalid") from error
        required = {
            "case_count", "execution_class", "success", "evidence_digest",
            "source_commit", "corpus_digest", "source_host_binaries",
            "snapshot_host_binaries",
            "host_build_identity",
            "run_id", "case_list_digest",
        }
        if (not isinstance(payload, dict) or set(payload) != required
                or payload.get("case_count") != 273
                or payload.get("execution_class") != "host-only"
                or payload.get("success") is not True
                or any(not isinstance(payload.get(field), str)
                       or not payload.get(field)
                       for field in required - {
                           "case_count", "execution_class", "success",
                           "source_host_binaries", "snapshot_host_binaries"})):
            raise PhaseEvidenceError(
                "host mysqltest gate evidence is invalid")
        source_binaries = payload["source_host_binaries"]
        snapshot_binaries = payload["snapshot_host_binaries"]
        if (source_binaries != snapshot_binaries
                or not isinstance(source_binaries, dict)
                or set(source_binaries) != {"seekdb", "obclient", "mysqltest"}
                or any(not isinstance(identity, dict)
                       or set(identity) != {"sha256", "size"}
                       for identity in source_binaries.values())):
            raise PhaseEvidenceError(
                "host mysqltest gate evidence is invalid")
        return (output_path.name,)

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
    mysqltest_plan = (
        run_mysqltest_phase.build_phase_plan(REPOSITORY_ROOT)
        if "mysqltest" in selected else ())
    mysqltest_device_cases = tuple(
        case for case in mysqltest_plan
        if case.execution_class == "device-native")
    mysqltest_host_output = run_directory / "evidence-mysqltest-host.json"
    mysqltest_host_command = (
        sys.executable, str(MYSQLTEST_PHASE_SCRIPT),
        "--repo-root", str(REPOSITORY_ROOT),
        "--output", str(mysqltest_host_output),
        "--host-work-directory", str(run_directory / "mysqltest-host"),
    )
    try:
        run_mysqltest_phase.resolve_host_binaries(
            os.environ, REPOSITORY_ROOT)
    except run_mysqltest_phase.MysqltestPhaseError:
        mysqltest_host_readiness = (
            "mysqltest host gate requires local host executables")
    else:
        mysqltest_host_readiness = None
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
                    production_build, source_revision, run_directory, run_id),
                requires_sql_restart_followup=False,
                invalidates_test_app=True,
                readiness_error=production_readiness,
            ),
            _sql_restart_contract(
                "rust-device-runtime", configuration, run_directory,
                source_revision, run_id, production=True),
        ),
        "mysqltest": (
            PhaseCaseContract(
                phase_id="mysqltest",
                case_id="ios.mysqltest.host-gate",
                execution_class="host-only",
                command=mysqltest_host_command,
                timeout_seconds=INVENTORY_TIMEOUT_SECONDS,
                evidence_validator=_mysqltest_host_validator(
                    mysqltest_host_output),
                requires_sql_restart_followup=False,
                readiness_error=mysqltest_host_readiness,
            ),
            *(PhaseCaseContract(
                phase_id="mysqltest",
                case_id=case.case_id,
                execution_class="device-native",
                command=_device_command(
                    configuration, run_directory, "mysqltest", case.case_id),
                timeout_seconds=DEVICE_CASE_TIMEOUT_SECONDS,
                evidence_validator=_device_validator(
                    run_directory, case.case_id),
                requires_sql_restart_followup=True,
                requires_test_app=True,
            ) for case in mysqltest_device_cases),
            _sql_restart_contract(
                "mysqltest", configuration, run_directory,
                source_revision, run_id),
        ),
    }
    for phase_id in ("vector", "lifecycle-memory", "final-matrix"):
        def validate_extended(process, phase_id=phase_id):
            """Bind independently validated extended evidence to the current runner identity."""
            if json.loads(process.stdout) != {"run_result": 0}:
                raise PhaseEvidenceError("extended phase command did not pass")
            try:
                if phase_id != "final-matrix":
                    metadata = json.loads((run_directory / f"evidence-{phase_id}.json").read_text())
                    if metadata.get("device_hash") != hashlib.sha256(
                            (configuration.device or "").encode()).hexdigest():
                        raise ValueError("extended phase device identity mismatch")
                return extended.validate_evidence(
                    run_directory, phase_id, run_id, source_revision)
            except (ValueError, OSError, KeyError) as error:
                raise PhaseEvidenceError(str(error)) from error

        cases = [PhaseCaseContract(
            phase_id=phase_id,
            case_id={"vector": "ios.vector.persistence-transactions",
                     "lifecycle-memory": "ios.lifecycle.background-termination-recovery",
                     "final-matrix": "ios.final-matrix.audit"}[phase_id],
            execution_class="host-only" if phase_id == "final-matrix" else "host-driven-device",
            command=(sys.executable, str(SCRIPT_DIRECTORY / "run_extended_iphone_tests.py"),
                     "--mode", phase_id, "--device", configuration.device or "",
                     "--bundle-id", configuration.bundle_id or "",
                     "--output-dir", str(run_directory), "--run-id", run_id,
                     "--source-commit", source_revision),
            timeout_seconds=1800,
            evidence_validator=validate_extended,
            requires_sql_restart_followup=phase_id != "final-matrix",
            requires_test_app=phase_id != "final-matrix",
        )]
        if phase_id == "lifecycle-memory":
            case_id = "ios.memory.bounded-pressure"
            cases.append(PhaseCaseContract(
                phase_id=phase_id, case_id=case_id, execution_class="device-native",
                command=_device_command(configuration, run_directory, "memory", case_id),
                timeout_seconds=DEVICE_CASE_TIMEOUT_SECONDS,
                evidence_validator=_device_validator(run_directory, case_id),
                requires_sql_restart_followup=True, requires_test_app=True))
        if phase_id != "final-matrix":
            cases.append(_sql_restart_contract(
                phase_id, configuration, run_directory, source_revision, run_id))
        all_contracts[phase_id] = tuple(cases)
    return {
        phase_id: cases for phase_id, cases in all_contracts.items()
        if phase_id in selected
    }


def create_phase_adapters(
        *, configuration, suites: Sequence[str], run_directory: Path,
        source_revision: str, run_id: str = "standalone-phase-setup",
        command_executor: Optional[CommandExecutor] = None,
        test_app_prepared: bool = False,
        terminal_stream=None,
        ) -> Iterable[runner.PhaseAdapter]:
    """Create executable runner adapters without serializing local secrets."""
    execute = command_executor or _default_executor(
        run_id, terminal_stream=terminal_stream)
    contracts = create_phase_contracts(
        configuration=configuration,
        suites=suites,
        run_directory=run_directory,
        source_revision=source_revision,
        run_id=run_id,
    )
    preparer = TestAppPreparer(
        configuration, execute, prepared=test_app_prepared, run_id=run_id)
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
        production_configuration, execute, run_id=run_id)

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
            fallback = (
                "production isolation build failed"
                if contract.case_id == PRODUCTION_ISOLATION_CASE_ID else
                "standalone phase command failed")
            diagnostic = _process_failure_diagnostic(
                process, run_id, configuration.redaction_tokens(), fallback)
            is_launch_failure = any(
                line.startswith("device launch failed")
                and line in diagnostic
                for line in SAFE_PROCESS_DIAGNOSTIC_LINES)
            category = (
                "timeout" if process.exit_status == 124 else
                "evidence" if contract.case_id == "ios.mysqltest.host-gate" else
                "infrastructure" if (
                    is_launch_failure
                    or contract.case_id == PRODUCTION_ISOLATION_CASE_ID) else
                "assertion")
            return runner.CaseResult.failed(
                category=category,
                diagnostic=diagnostic,
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
    try:
        execute = _default_executor(run_id)
    except BuildReadinessError:
        return "build-inputs"
    preparer = TestAppPreparer(
        configuration, execute, run_id=run_id)
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
    try:
        execute = _default_executor(run_id)
    except BuildReadinessError:
        return "build-inputs"
    failure = TestAppPreparer(
        configuration, execute, run_id=run_id).ensure(
            reuse_build=True)
    if failure is None:
        return None
    if failure.status == "blocked":
        return "local-profile"
    failure_code = PREPARATION_FAILURE_CODES.get(failure.diagnostic)
    if failure_code is not None:
        return failure_code
    raise PhasePreparationError("prepared App verification or install failed")
