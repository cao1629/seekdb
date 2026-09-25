#!/usr/bin/env python3
"""Build and validate the standalone mysqltest phase without merging host/device claims."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Mapping, Optional, Sequence

import mysqltest_parser
import macos_lldb_launcher

MAX_HOST_RESULT_BYTES = 8 * 1024 * 1024
HOST_RUN_TIMEOUT_SECONDS = 24 * 60 * 60
HOST_DESTROY_TIMEOUT_SECONDS = 45
HOST_BINARY_ENVIRONMENTS = {
    "seekdb": "SEEKDB_IPHONE_HOST_SEEKDB",
    "obclient": "SEEKDB_IPHONE_HOST_OBCLIENT",
    "mysqltest": "SEEKDB_IPHONE_HOST_MYSQLTEST",
}
HOST_BINARY_CANONICAL_PATHS = {
    "seekdb": Path("build_release/src/observer/seekdb"),
    "obclient": Path("deps/3rd/u01/obclient/bin/obclient"),
    "mysqltest": Path("deps/3rd/u01/obclient/bin/mysqltest"),
}
HOST_BINARY_NAMES = ("seekdb", "obclient", "mysqltest")
MACHO_MAGICS = {b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}
SYSTEM_MACHO_DEPENDENCY_PREFIXES = ("/usr/lib/", "/System/Library/")
MACOS_LLDB_LAUNCHER = Path(__file__).resolve().with_name(
    "macos_lldb_launcher.py")


@dataclass(frozen=True)
class MysqltestPhaseCase:
    """Describe one independently checkpointed mysqltest phase case."""

    case_id: str
    source_name: str
    execution_class: str
    failure_key: str
    requires_sql_restart_followup: bool


@dataclass(frozen=True)
class HostBinarySnapshots:
    """Bind original host tools to one immutable run-local byte snapshot."""

    source_paths: Mapping[str, Path]
    snapshot_paths: Mapping[str, Path]
    source_identity: Mapping[str, dict]
    snapshot_identity: Mapping[str, dict]


class MysqltestPhaseError(RuntimeError):
    """Report invalid host evidence or an unsafe phase selection."""


def build_phase_plan(repo_root: Path) -> tuple[MysqltestPhaseCase, ...]:
    """Build separate device-source cases plus one explicit host mysqltest gate."""
    classified = mysqltest_parser.classify_active_corpus(Path(repo_root))
    device = tuple(
        MysqltestPhaseCase(
            case.device_case_ids[0], case.name, "device-native", case.name, True)
        for case in classified if case.device_case_ids)
    host = MysqltestPhaseCase(
        "ios.mysqltest.host-gate", "ci-selected-host-gate",
        "host-only", "ci-selected-host-gate", False)
    return (*device, host)


def select_resume_cases(
        plan: Sequence[MysqltestPhaseCase],
        statuses: Mapping[str, str]) -> tuple[MysqltestPhaseCase, ...]:
    """Select only failed or pending cases and reject unknown checkpoint states."""
    allowed = {"pending", "running", "passed", "failed", "blocked", "excluded"}
    unknown = set(statuses.values()) - allowed
    if unknown:
        raise MysqltestPhaseError("mysqltest checkpoint has an invalid status")
    return tuple(
        case for case in plan
        if statuses.get(case.case_id, "pending") in {"failed", "pending"})


def failure_filename(case: MysqltestPhaseCase) -> str:
    """Return one collision-safe failure filename bound to a source case."""
    slug = "".join(
        character.lower() if character.isalnum() else "-"
        for character in case.failure_key).strip("-")[:72] or "case"
    digest = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:12]
    return f"failure-mysqltest-{slug}-{digest}.json"


def validate_host_gate(
        repo_root: Path, result_path: Path,
        binaries: Mapping[str, Path],
        source_binaries: Mapping[str, Path] = None) -> dict:
    """Validate identity-bound exact host mysqltest evidence."""
    classified_selection = [
        case.name for case in mysqltest_parser.discover_active_cases(repo_root)
        if case.ci_selected]
    host = mysqltest_parser._load_host_discovery(Path(repo_root))
    selected = [case.name for case in host.discover_cases(Path(repo_root))]
    if (len(classified_selection) != len(selected)
            or set(classified_selection) != set(selected)):
        raise MysqltestPhaseError(
            "host mysqltest classification and execution selection differ")
    try:
        payload = json.loads(_read_host_result(result_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError,
            RecursionError, MemoryError) as error:
        raise MysqltestPhaseError("host mysqltest result is unavailable") from error
    required = {
        "schema_version", "producer", "source_commit", "corpus_digest",
        "host_build_identity", "host_binaries", "result_kind", "success",
        "run_id", "slice_count", "case_count", "executed_cases",
        "failed_cases", "errors", "evidence_digest",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise MysqltestPhaseError("host mysqltest gate did not pass exact coverage")
    recorded_binaries = payload.get("host_binaries")
    binary_names = ("seekdb", "obclient", "mysqltest")
    valid_binaries = (isinstance(recorded_binaries, dict)
                      and set(recorded_binaries) == set(binary_names))
    if valid_binaries:
        valid_binaries = all(
            isinstance(recorded_binaries[name], dict)
            and set(recorded_binaries[name]) == {"sha256", "size"}
            and isinstance(recorded_binaries[name]["sha256"], str)
            and len(recorded_binaries[name]["sha256"]) == 64
            and all(character in "0123456789abcdef"
                    for character in recorded_binaries[name]["sha256"])
            and isinstance(recorded_binaries[name]["size"], int)
            and recorded_binaries[name]["size"] >= 0
            for name in binary_names)
    try:
        actual_identity = host.build_host_evidence_identity(
            repo_root, binaries)
        source_identity = host.build_host_evidence_identity(
            repo_root, source_binaries or binaries)
    except Exception as error:
        raise MysqltestPhaseError(
            "host mysqltest evidence identity is unavailable") from error
    if (not host.verify_evidence_digest(payload)
            or payload.get("schema_version") != host.EVIDENCE_SCHEMA_VERSION
            or payload.get("producer") != host.EVIDENCE_PRODUCER
            or payload.get("source_commit") != actual_identity["source_commit"]
            or payload.get("corpus_digest") != actual_identity["corpus_digest"]
            or payload.get("host_build_identity")
            != actual_identity["host_build_identity"]
            or recorded_binaries != actual_identity["host_binaries"]
            or source_identity["host_binaries"] != recorded_binaries
            or not valid_binaries
            or payload.get("result_kind") != "merged"
            or not isinstance(payload.get("run_id"), str)
            or not payload.get("run_id")
            or not isinstance(payload.get("slice_count"), int)
            or payload.get("slice_count") <= 0
            or payload.get("success") is not True
            or payload.get("failed_cases") != []
            or payload.get("errors") != []
            or payload.get("case_count") != len(selected)
            or payload.get("executed_cases") != selected):
        raise MysqltestPhaseError("host mysqltest gate did not pass exact coverage")
    case_list_digest = hashlib.sha256(
        json.dumps(selected, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "execution_class": "host-only",
        "case_count": len(selected),
        "success": True,
        "evidence_digest": payload["evidence_digest"],
        "source_commit": payload["source_commit"],
        "corpus_digest": payload["corpus_digest"],
        "source_host_binaries": source_identity["host_binaries"],
        "snapshot_host_binaries": payload["host_binaries"],
        "host_build_identity": payload["host_build_identity"],
        "run_id": payload["run_id"],
        "case_list_digest": case_list_digest,
    }


def resolve_host_binaries(
        environment: Mapping[str, str],
        repository_root: Path = None) -> dict[str, Path]:
    """Resolve explicit or canonical current-repository host executables."""
    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[2]
    repository_root = Path(repository_root).expanduser().absolute()
    binaries = {}
    for name, variable in HOST_BINARY_ENVIRONMENTS.items():
        if variable in environment:
            value = environment[variable]
            if not isinstance(value, str) or not value.strip():
                raise MysqltestPhaseError(
                    "host mysqltest binaries are unavailable")
            path = Path(value).expanduser().absolute()
            _validate_explicit_host_binary(path)
        else:
            relative = HOST_BINARY_CANONICAL_PATHS[name]
            _validate_canonical_host_binary(repository_root, relative)
            path = repository_root / relative
        binaries[name] = path
    return binaries


def _validate_explicit_host_binary(path: Path) -> None:
    """Validate one explicit binary through its complete anchored parent chain."""
    parent_fd = None
    descriptor = None
    try:
        parent_fd, descriptor = _open_host_binary(path)
    except (OSError, MysqltestPhaseError) as error:
        raise MysqltestPhaseError(
            "host mysqltest binaries are unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _validate_canonical_host_binary(
        repository_root: Path, relative_path: Path) -> None:
    """Validate one canonical binary below an anchored real repository root."""
    parts = relative_path.parts
    if (relative_path.is_absolute() or not parts
            or any(part in {"", ".", ".."} for part in parts)):
        raise MysqltestPhaseError("host mysqltest binaries are unavailable")
    repository_fd = None
    parent_fd = None
    binary_fd = None
    try:
        repository_fd = _open_anchored_directory(repository_root)
        parent_fd = os.dup(repository_fd)
        for component in parts[:-1]:
            next_fd = _open_directory_at(parent_fd, component)
            os.close(parent_fd)
            parent_fd = next_fd
        flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        binary_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
        metadata = os.fstat(binary_fd)
        path_metadata = os.stat(
            parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        same_file = ((metadata.st_dev, metadata.st_ino)
                     == (path_metadata.st_dev, path_metadata.st_ino))
        if (not stat.S_ISREG(metadata.st_mode)
                or not same_file
                or not os.access(
                    parts[-1], os.X_OK, dir_fd=parent_fd,
                    follow_symlinks=False)):
            raise MysqltestPhaseError(
                "host mysqltest binaries are unavailable")
    except MysqltestPhaseError:
        raise
    except OSError as error:
        raise MysqltestPhaseError(
            "host mysqltest binaries are unavailable") from error
    finally:
        if binary_fd is not None:
            os.close(binary_fd)
        if parent_fd is not None:
            os.close(parent_fd)
        if repository_fd is not None:
            os.close(repository_fd)


def _identity_from_open_file(descriptor: int) -> dict:
    """Hash one stable regular executable from its already anchored file fd."""
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise MysqltestPhaseError("host mysqltest binaries are unavailable")
    digest = hashlib.sha256()
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    after = os.fstat(descriptor)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns)
            or total != after.st_size):
        raise MysqltestPhaseError("host mysqltest binary identity changed")
    return {"sha256": digest.hexdigest(), "size": total}


def _open_host_binary(path: Path) -> tuple[int, int]:
    """Open one binary and its complete real parent chain without symlinks."""
    absolute = Path(path).expanduser().absolute()
    parent_fd = _open_anchored_directory(absolute.parent)
    descriptor = None
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(absolute.name, flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(
            absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
                or not os.access(
                    absolute.name, os.X_OK, dir_fd=parent_fd,
                    follow_symlinks=False)):
            raise MysqltestPhaseError(
                "host mysqltest binaries are unavailable")
        return parent_fd, descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
        raise


def _hash_host_binary(path: Path) -> dict:
    """Return a stable identity after anchored executable validation."""
    parent_fd = None
    descriptor = None
    try:
        parent_fd, descriptor = _open_host_binary(path)
        return _identity_from_open_file(descriptor)
    except MysqltestPhaseError:
        raise
    except OSError as error:
        raise MysqltestPhaseError(
            "host mysqltest binaries are unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _hash_read_only_snapshot(path: Path) -> dict:
    """Hash one executable snapshot and reject restored owner write access."""
    parent_fd = None
    descriptor = None
    try:
        parent_fd, descriptor = _open_host_binary(path)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o222:
            raise MysqltestPhaseError("host mysqltest snapshot is writable")
        return _identity_from_open_file(descriptor)
    except MysqltestPhaseError:
        raise
    except OSError as error:
        raise MysqltestPhaseError("host mysqltest snapshot is unsafe") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _load_host_binary_snapshots(
        source_binaries: Mapping[str, Path],
        work_directory: Path) -> HostBinarySnapshots:
    """Load and validate a complete immutable snapshot directory."""
    if set(source_binaries) != set(HOST_BINARY_NAMES):
        raise MysqltestPhaseError("host mysqltest binaries are unavailable")
    snapshot_directory = Path(work_directory).expanduser().absolute() / "binaries"
    directory_fd = _open_anchored_directory(snapshot_directory)
    try:
        metadata = os.fstat(directory_fd)
        if stat.S_IMODE(metadata.st_mode) & 0o222:
            raise MysqltestPhaseError("host mysqltest snapshot is unsafe")
        if set(os.listdir(directory_fd)) != set(HOST_BINARY_NAMES):
            raise MysqltestPhaseError("host mysqltest snapshot is incomplete")
    finally:
        os.close(directory_fd)
    source_paths = {
        name: Path(source_binaries[name]).expanduser().absolute()
        for name in HOST_BINARY_NAMES
    }
    snapshot_paths = {
        name: snapshot_directory / name for name in HOST_BINARY_NAMES
    }
    source_identity = {
        name: _hash_host_binary(source_paths[name]) for name in HOST_BINARY_NAMES
    }
    snapshot_identity = {
        name: _hash_read_only_snapshot(snapshot_paths[name])
        for name in HOST_BINARY_NAMES
    }
    if source_identity != snapshot_identity:
        raise MysqltestPhaseError("host mysqltest source identity changed")
    return HostBinarySnapshots(
        source_paths, snapshot_paths, source_identity, snapshot_identity)


def _copy_host_binary_snapshot(
        source: Path, snapshot_fd: int, name: str) -> dict:
    """Copy one stable source fd into a durable random temporary file."""
    source_parent_fd = None
    source_fd = None
    output_fd = None
    temporary = ".{}-{}-{}.tmp".format(
        name, os.getpid(), secrets.token_hex(8))
    try:
        source_parent_fd, source_fd = _open_host_binary(source)
        before = os.fstat(source_fd)
        output_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o500, dir_fd=snapshot_fd)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    raise OSError("short snapshot write")
                view = view[written:]
        after = os.fstat(source_fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)
                or total != after.st_size):
            raise MysqltestPhaseError("host mysqltest source identity changed")
        os.fchmod(output_fd, 0o500)
        os.fsync(output_fd)
        os.close(output_fd)
        output_fd = None
        os.rename(
            temporary, name,
            src_dir_fd=snapshot_fd, dst_dir_fd=snapshot_fd)
        os.fsync(snapshot_fd)
        return {"sha256": digest.hexdigest(), "size": total}
    finally:
        if output_fd is not None:
            os.close(output_fd)
        if source_fd is not None:
            os.close(source_fd)
        if source_parent_fd is not None:
            os.close(source_parent_fd)
        try:
            os.unlink(temporary, dir_fd=snapshot_fd)
        except OSError:
            pass


def prepare_host_binary_snapshots(
        source_binaries: Mapping[str, Path],
        work_directory: Path) -> HostBinarySnapshots:
    """Create or reuse one atomic read-only host-tool snapshot bundle."""
    work_directory = Path(work_directory).expanduser().absolute()
    _prepare_host_workspace(work_directory)
    try:
        return _load_host_binary_snapshots(source_binaries, work_directory)
    except MysqltestPhaseError:
        work_fd = _open_anchored_directory(work_directory)
        try:
            try:
                os.stat("binaries", dir_fd=work_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise
        finally:
            os.close(work_fd)
    work_fd = _open_anchored_directory(work_directory)
    temporary = ".binaries-{}-{}.tmp".format(
        os.getpid(), secrets.token_hex(8))
    snapshot_fd = None
    created_names = []
    try:
        os.mkdir(temporary, mode=0o700, dir_fd=work_fd)
        snapshot_fd = _open_directory_at(work_fd, temporary)
        for name in HOST_BINARY_NAMES:
            _copy_host_binary_snapshot(
                Path(source_binaries[name]), snapshot_fd, name)
            created_names.append(name)
        os.fchmod(snapshot_fd, 0o500)
        os.fsync(snapshot_fd)
        os.rename(
            temporary, "binaries", src_dir_fd=work_fd, dst_dir_fd=work_fd)
        os.fsync(work_fd)
    except (KeyError, OSError) as error:
        raise MysqltestPhaseError(
            "host mysqltest snapshot preparation failed") from error
    finally:
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        try:
            temporary_fd = _open_directory_at(work_fd, temporary)
        except OSError:
            temporary_fd = None
        if temporary_fd is not None:
            try:
                os.fchmod(temporary_fd, 0o700)
                for name in created_names:
                    try:
                        os.unlink(name, dir_fd=temporary_fd)
                    except OSError:
                        pass
            finally:
                os.close(temporary_fd)
            try:
                os.rmdir(temporary, dir_fd=work_fd)
            except OSError:
                pass
        os.close(work_fd)
    return _load_host_binary_snapshots(source_binaries, work_directory)


def validate_host_binary_snapshots(bundle: HostBinarySnapshots) -> None:
    """Revalidate current source and snapshot identities against one bundle."""
    current = _load_host_binary_snapshots(
        bundle.source_paths,
        next(iter(bundle.snapshot_paths.values())).parent.parent)
    if (current.source_identity != bundle.source_identity
            or current.snapshot_identity != bundle.snapshot_identity):
        raise MysqltestPhaseError("host mysqltest snapshot identity changed")
    _validate_snapshot_macho_dependencies(current.snapshot_paths)


def _validate_snapshot_macho_dependencies(
        snapshot_paths: Mapping[str, Path]) -> None:
    """Reject relocatable host tools with non-system Mach-O dependencies."""
    for name in HOST_BINARY_NAMES:
        path = snapshot_paths[name]
        parent_fd = None
        descriptor = None
        try:
            parent_fd, descriptor = _open_host_binary(path)
            magic = os.read(descriptor, 4)
        except (OSError, MysqltestPhaseError) as error:
            raise MysqltestPhaseError(
                "host mysqltest snapshot dependency validation failed") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent_fd is not None:
                os.close(parent_fd)
        if magic not in MACHO_MAGICS:
            raise MysqltestPhaseError(
                "host mysqltest snapshot is not a supported 64-bit Mach-O")
        try:
            completed = subprocess.run(
                ["/usr/bin/otool", "-L", str(path)],
                capture_output=True, check=False, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise MysqltestPhaseError(
                "host mysqltest snapshot dependency validation failed") from error
        if completed.returncode != 0:
            raise MysqltestPhaseError(
                "host mysqltest snapshot dependency validation failed")
        try:
            lines = completed.stdout.decode("utf-8", errors="strict").splitlines()[1:]
        except UnicodeDecodeError as error:
            raise MysqltestPhaseError(
                "host mysqltest snapshot dependency validation failed") from error
        dependencies = [line.strip().split(" (", 1)[0] for line in lines]
        if (not dependencies
                or any(not dependency.startswith(
                    SYSTEM_MACHO_DEPENDENCY_PREFIXES)
                    for dependency in dependencies)):
            raise MysqltestPhaseError(
                "host mysqltest snapshot has unsupported dependencies")


def _select_host_executable_launcher(
        binaries: Mapping[str, Path], process_runner=None) -> Optional[Path]:
    """Select tracked LLDB only for macOS 27 or a direct SIGKILL probe."""
    if sys.platform != "darwin":
        return None
    try:
        major_version = int(platform.mac_ver()[0].split(".", 1)[0])
    except (ValueError, IndexError):
        major_version = 0
    use_lldb = major_version >= 27
    if not use_lldb:
        runner = process_runner or _run_controlled_process
        for name in HOST_BINARY_NAMES:
            try:
                completed = runner(
                    [str(binaries[name]), "--help"],
                    Path.cwd(), time.monotonic() + 10)
            except subprocess.TimeoutExpired:
                continue
            if completed.returncode in {-signal.SIGKILL, 128 + signal.SIGKILL}:
                use_lldb = True
                break
    if not use_lldb:
        return None
    try:
        metadata = MACOS_LLDB_LAUNCHER.lstat()
        if (MACOS_LLDB_LAUNCHER.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)):
            raise MysqltestPhaseError("tracked LLDB launcher is unavailable")
        macos_lldb_launcher.validate_lldb()
    except (OSError, macos_lldb_launcher.LauncherError) as error:
        raise MysqltestPhaseError("tracked LLDB launcher is unavailable") from error
    return MACOS_LLDB_LAUNCHER


def execute_local_host_gate(
        repo_root: Path, work_directory: Path, run_id: str,
        binaries: Mapping[str, Path], process_runner=None) -> dict:
    """Run all selected host cases and merge evidence in this runner process."""
    repo_root = Path(repo_root)
    work_directory = Path(work_directory)
    snapshots = prepare_host_binary_snapshots(binaries, work_directory)
    validate_host_binary_snapshots(snapshots)
    execution_binaries = snapshots.snapshot_paths
    launcher = _select_host_executable_launcher(execution_binaries)
    slice_directory = work_directory / "slice_0"
    script = repo_root / ".github/script/seekdb/mysqltest_for_seekdb.py"
    commands = (
        [sys.executable, str(script), "run",
         "--seekdb", str(execution_binaries["seekdb"]),
         "--obclient", str(execution_binaries["obclient"]),
         "--mysqltest", str(execution_binaries["mysqltest"]),
         "--base-dir", str(work_directory / "instance"),
         "--work-dir", str(slice_directory),
         "--slice-index", "0", "--slice-count", "1",
         *(["--launcher", str(launcher)] if launcher else [])],
        [sys.executable, str(script), "merge",
         "--results-dir", str(work_directory),
         "--slice-count", "1", "--run-id", run_id,
         "--output", str(work_directory / "host-result.json")],
    )
    # macOS Python has no fexecve, and a local probe could not execute an
    # O_EXEC descriptor through /dev/fd. The documented same-UID threat model
    # therefore relies on the run lock and checks around path-based execution.
    deadline = time.monotonic() + HOST_RUN_TIMEOUT_SECONDS
    process_runner = process_runner or _run_controlled_process
    try:
        validate_host_binary_snapshots(snapshots)
        completed = process_runner(commands[0], repo_root, deadline)
    except BaseException:
        try:
            _destroy_managed_host_instance(repo_root, work_directory)
        except Exception:
            pass
        raise
    if completed.returncode != 0:
        try:
            _destroy_managed_host_instance(repo_root, work_directory)
        except Exception:
            pass
        raise MysqltestPhaseError("host mysqltest execution failed")
    try:
        validate_host_binary_snapshots(snapshots)
    except BaseException:
        try:
            _destroy_managed_host_instance(repo_root, work_directory)
        except Exception:
            pass
        raise
    try:
        _destroy_managed_host_instance(repo_root, work_directory)
    except (MysqltestPhaseError, OSError, subprocess.SubprocessError) as error:
        raise MysqltestPhaseError(
            "host mysqltest cleanup failed") from error

    try:
        validate_host_binary_snapshots(snapshots)
        completed = process_runner(commands[1], repo_root, deadline)
    except (OSError, subprocess.SubprocessError) as error:
        raise MysqltestPhaseError(
            "host mysqltest execution failed") from error
    if completed.returncode != 0:
        raise MysqltestPhaseError("host mysqltest execution failed")
    try:
        validate_host_binary_snapshots(snapshots)
        return validate_host_gate(
            repo_root, work_directory / "host-result.json",
            execution_binaries, source_binaries=binaries)
    except MysqltestPhaseError:
        raise
    except Exception as error:
        raise MysqltestPhaseError(
            "host mysqltest evidence validation failed") from error


def _destroy_managed_host_instance(
        repo_root: Path, work_directory: Path) -> None:
    """Boundedly destroy only this run's identity-marked sdb instance."""
    repo_root = Path(repo_root).expanduser().absolute()
    work_directory = Path(work_directory).expanduser().absolute()
    sdb_script = repo_root / ".github/script/seekdb/sdb.py"
    try:
        metadata = sdb_script.lstat()
    except OSError as error:
        raise MysqltestPhaseError("tracked sdb cleanup is unavailable") from error
    if sdb_script.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise MysqltestPhaseError("tracked sdb cleanup is unavailable")
    _prepare_host_workspace(work_directory)
    command = [
        sys.executable, str(sdb_script), "destroy", "--base-dir",
        str(work_directory / "instance"),
    ]
    completed = _run_controlled_process(
        command, repo_root,
        time.monotonic() + HOST_DESTROY_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise MysqltestPhaseError("tracked sdb cleanup failed")


def _open_directory_at(parent_fd: int, name: str) -> int:
    """Open one direct real directory component without following a link."""
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0))
    return os.open(name, flags, dir_fd=parent_fd)


def _ensure_directory_at(parent_fd: int, name: str) -> int:
    """Create and open one direct directory or reject an unsafe existing entry."""
    if not name or name in {".", ".."} or "/" in name:
        raise MysqltestPhaseError("host mysqltest workspace is unsafe")
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return _open_directory_at(parent_fd, name)
    except OSError as error:
        raise MysqltestPhaseError(
            "host mysqltest workspace is unsafe") from error


def _open_anchored_directory(path: Path) -> int:
    """Open every absolute directory component with no symlink traversal."""
    absolute = Path(path).expanduser().absolute()
    descriptor = os.open(
        os.path.sep, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0))
    try:
        for component in absolute.parts[1:]:
            next_descriptor = _open_directory_at(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise MysqltestPhaseError(
            "host mysqltest workspace is unsafe") from error


def _prepare_host_workspace(work_directory: Path) -> None:
    """Create every tracked-runner directory below one anchored real parent."""
    work_directory = Path(work_directory).expanduser().absolute()
    try:
        parent_fd = _open_anchored_directory(work_directory.parent)
    except (OSError, MysqltestPhaseError) as error:
        raise MysqltestPhaseError(
            "host mysqltest workspace is unsafe") from error
    opened = []
    try:
        work_fd = _ensure_directory_at(parent_fd, work_directory.name)
        opened.append(work_fd)
        slice_fd = _ensure_directory_at(work_fd, "slice_0")
        opened.append(slice_fd)
        opened.append(_ensure_directory_at(slice_fd, "tmp"))
        opened.append(_ensure_directory_at(slice_fd, "mysqltest_log"))
        try:
            instance_metadata = os.stat(
                "instance", dir_fd=work_fd, follow_symlinks=False)
        except FileNotFoundError:
            instance_metadata = None
        if (instance_metadata is not None
                and not stat.S_ISDIR(instance_metadata.st_mode)):
            raise MysqltestPhaseError(
                "host mysqltest workspace is unsafe")
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(parent_fd)


def _process_group_exists(process_group: int) -> bool:
    """Return whether the original process group still has any members."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_process_group_exit(process_group: int, deadline: float) -> bool:
    """Boundedly wait for every process in one group to disappear."""
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return True
        time.sleep(0.02)
    return not _process_group_exists(process_group)


def _terminate_process_group(process: subprocess.Popen) -> None:
    """TERM, unconditionally KILL, and boundedly reap a whole process group."""
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    _wait_process_group_exit(process_group, time.monotonic() + 2.0)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    if not _wait_process_group_exit(
            process_group, time.monotonic() + 2.0):
        raise subprocess.TimeoutExpired("process-group-cleanup", 6.0)


def _run_controlled_process(
        command: Sequence[str], cwd: Path,
        deadline: float) -> subprocess.CompletedProcess:
    """Run one command in a new process group under a shared absolute deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except BaseException:
        _terminate_process_group(process)
        raise
    if _process_group_exists(process.pid):
        _terminate_process_group(process)
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)


def _read_host_result(path: Path) -> bytes:
    """Read one bounded stable regular evidence file without following links."""
    path = Path(path).expanduser().absolute()
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    parent_fd = _open_anchored_directory(path.parent)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
    except BaseException:
        os.close(parent_fd)
        raise
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_HOST_RESULT_BYTES:
            raise OSError("host result must be a bounded regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_HOST_RESULT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_HOST_RESULT_BYTES:
                raise OSError("host result exceeds the size bound")
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)):
            raise OSError("host result changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)
        os.close(parent_fd)


def _write_json(path: Path, payload) -> None:
    """Durably write deterministic metadata through an anchored directory fd."""
    path = Path(path).expanduser().absolute()
    parent_fd = _open_anchored_directory(path.parent)
    temporary = ".{}.tmp-{}-{}".format(
        path.name, os.getpid(), secrets.token_hex(8))
    descriptor = None
    try:
        try:
            existing = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise MysqltestPhaseError("mysqltest phase output is unsafe")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600, dir_fd=parent_fd)
        content = (json.dumps(
            payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            existing = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise MysqltestPhaseError("mysqltest phase output is unsafe")
        os.rename(
            temporary, path.name,
            src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except MysqltestPhaseError:
        raise
    except OSError as error:
        raise MysqltestPhaseError("mysqltest phase output is unsafe") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        os.close(parent_fd)


def parse_args(arguments: Sequence[str] = None) -> argparse.Namespace:
    """Parse classification or host-result validation arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-work-directory", type=Path, required=True)
    return parser.parse_args(arguments)


def main(
        arguments: Sequence[str] = None,
        environment: Mapping[str, str] = None) -> int:
    """Write either the complete phase plan or a separate host-gate summary."""
    options = parse_args(arguments)
    try:
        local_environment = os.environ if environment is None else environment
        binaries = resolve_host_binaries(
            local_environment, options.repo_root)
        snapshots = _load_host_binary_snapshots(
            binaries, options.host_work_directory)
        validate_host_binary_snapshots(snapshots)
        payload = validate_host_gate(
            options.repo_root,
            options.host_work_directory / "host-result.json",
            snapshots.snapshot_paths, source_binaries=binaries)
        validate_host_binary_snapshots(snapshots)
        _write_json(options.output, payload)
    except (MysqltestPhaseError, RecursionError, MemoryError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
