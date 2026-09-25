#!/usr/bin/env python3
"""Build and validate the standalone mysqltest phase without merging host/device claims."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Mapping, Sequence

import mysqltest_parser

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


@dataclass(frozen=True)
class MysqltestPhaseCase:
    """Describe one independently checkpointed mysqltest phase case."""

    case_id: str
    source_name: str
    execution_class: str
    failure_key: str
    requires_sql_restart_followup: bool


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
        binaries: Mapping[str, Path]) -> dict:
    """Validate identity-bound exact host mysqltest evidence."""
    selected = [
        case.name for case in mysqltest_parser.discover_active_cases(repo_root)
        if case.ci_selected]
    try:
        payload = json.loads(_read_host_result(result_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError,
            RecursionError, MemoryError) as error:
        raise MysqltestPhaseError("host mysqltest result is unavailable") from error
    host = mysqltest_parser._load_host_discovery(Path(repo_root))
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
        "host_binaries": payload["host_binaries"],
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
    """Validate one explicit binary while preserving its established contract."""
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MysqltestPhaseError(
            "host mysqltest binaries are unavailable") from error
    if (not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
            or not os.access(path, os.X_OK)):
        raise MysqltestPhaseError("host mysqltest binaries are unavailable")


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


def execute_local_host_gate(
        repo_root: Path, work_directory: Path, run_id: str,
        binaries: Mapping[str, Path], process_runner=None) -> dict:
    """Run all selected host cases and merge evidence in this runner process."""
    repo_root = Path(repo_root)
    work_directory = Path(work_directory)
    _prepare_host_workspace(work_directory)
    slice_directory = work_directory / "slice_0"
    script = repo_root / ".github/script/seekdb/mysqltest_for_seekdb.py"
    commands = (
        [sys.executable, str(script), "run",
         "--seekdb", str(binaries["seekdb"]),
         "--obclient", str(binaries["obclient"]),
         "--mysqltest", str(binaries["mysqltest"]),
         "--base-dir", str(work_directory / "instance"),
         "--work-dir", str(slice_directory),
         "--slice-index", "0", "--slice-count", "1"],
        [sys.executable, str(script), "merge",
         "--results-dir", str(work_directory),
         "--slice-count", "1", "--run-id", run_id,
         "--output", str(work_directory / "host-result.json")],
    )
    deadline = time.monotonic() + HOST_RUN_TIMEOUT_SECONDS
    process_runner = process_runner or _run_controlled_process
    try:
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
        _destroy_managed_host_instance(repo_root, work_directory)
    except (OSError, subprocess.SubprocessError) as error:
        raise MysqltestPhaseError(
            "host mysqltest cleanup failed") from error

    try:
        completed = process_runner(commands[1], repo_root, deadline)
    except (OSError, subprocess.SubprocessError) as error:
        raise MysqltestPhaseError(
            "host mysqltest execution failed") from error
    if completed.returncode != 0:
        raise MysqltestPhaseError("host mysqltest execution failed")
    try:
        return validate_host_gate(
            repo_root, work_directory / "host-result.json", binaries)
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
        payload = validate_host_gate(
            options.repo_root,
            options.host_work_directory / "host-result.json", binaries)
        _write_json(options.output, payload)
    except (MysqltestPhaseError, RecursionError, MemoryError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
