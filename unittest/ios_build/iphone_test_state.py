#!/usr/bin/env python3
"""Durable run selection and checkpoint storage for iPhone tests."""

from dataclasses import dataclass, field
import datetime as dt
from enum import Enum
import errno
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any, Dict, List
import uuid


SCHEMA_VERSION = 1
RUNNER_VERSION = "1"
CHECKPOINT_FILENAME = "checkpoint.json"
REPORT_FILENAMES = ("summary.json", "summary.md")
CASE_STATUSES = frozenset(
    {"pending", "running", "passed", "failed", "excluded", "blocked"})
TERMINAL_CASE_STATUSES = frozenset(
    {"passed", "failed", "excluded", "blocked"})
PHASE_STATUSES = CASE_STATUSES
RUN_STATUSES = frozenset({"incomplete", "passed", "failed", "blocked"})
DIRECTORY_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                   | getattr(os, "O_NOFOLLOW", 0)
                   | getattr(os, "O_CLOEXEC", 0))
FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
LOCK_FILENAME = ".runner.lock"


class IphoneTestStateError(RuntimeError):
    """Base error for invalid or unusable iPhone runner state."""


class NoResumableRunError(IphoneTestStateError):
    """Indicate that explicit resume found no incomplete checkpoint."""


class IncompatibleCheckpointError(IphoneTestStateError):
    """Indicate that a checkpoint belongs to incompatible runner inputs."""


class ImmutableRunIdentityError(IphoneTestStateError):
    """Indicate an attempted mutation of an existing run identity."""


class UnsafeRunPathError(IphoneTestStateError):
    """Indicate a run path outside the allowed date-directory layout."""


class CorruptCheckpointError(IphoneTestStateError):
    """Indicate malformed checkpoint JSON or required metadata."""


class RunLockedError(IphoneTestStateError):
    """Indicate that another process already owns a run directory."""


class ConcurrentCheckpointUpdateError(IphoneTestStateError):
    """Indicate that a writer started from a stale checkpoint generation."""


class RunMode(Enum):
    """Select how a runner invocation obtains its checkpoint."""

    DEFAULT = "default"
    RESUME = "resume"
    RESTART = "restart"


@dataclass(frozen=True)
class RunSelection:
    """Describe a selected run while retaining its lifetime lock."""

    run_directory: Path
    checkpoint: Dict[str, Any]
    resumed: bool
    lock: "RunLock" = field(repr=False, compare=False)

    def close(self) -> None:
        """Release this selection's runner-lifetime lock."""
        self.lock.release()

    def ensure_locked(self) -> None:
        """Reject use after the runner-lifetime lock has been released."""
        if not self.lock.is_held:
            raise RunLockedError("run selection no longer holds its lock")

    def __enter__(self):
        """Return a selection whose lock is already held."""
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        """Release the runner-lifetime lock when execution ends."""
        self.close()

    def __del__(self):
        """Best-effort release when a caller drops an unused selection."""
        try:
            self.close()
        except Exception:
            pass


@dataclass(frozen=True)
class RunPreview:
    """Describe a read-only lifecycle selection without creating or locking it."""

    run_directory: Path
    checkpoint: Any
    resumed: bool


class RunLock:
    """Hold an exclusive advisory lock for one runner's entire lifetime."""

    def __init__(self, output_root: Path, run_directory: Path):
        """Remember the safe run path without acquiring its lock yet."""
        self._output_root = Path(output_root)
        self._run_directory = Path(run_directory)
        self._descriptor = None

    def acquire(self) -> None:
        """Acquire the run lock or fail immediately when another owner exists."""
        if self._descriptor is not None:
            return
        root_fd, run_fd, _ = _open_run_directory(
            self._output_root, self._run_directory, create=True)
        try:
            descriptor = _open_file_at(
                run_fd, LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
        finally:
            os.close(run_fd)
            os.close(root_fd)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise RunLockedError(
                    f"run directory is already locked: {self._run_directory}") \
                    from error
            raise
        self._descriptor = descriptor

    @property
    def is_held(self) -> bool:
        """Return whether this object still owns an open lock descriptor."""
        return self._descriptor is not None

    def release(self) -> None:
        """Release the held advisory lock and close its descriptor."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self):
        """Acquire and return this lock for a runner lifetime context."""
        self.acquire()
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        """Release the lock when the runner lifetime context ends."""
        self.release()


def _require_aware(timestamp: dt.datetime) -> None:
    """Reject timestamps that have no usable UTC offset."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _parse_aware_timestamp(value: Any, field_name: str) -> dt.datetime:
    """Parse one required timezone-aware ISO timestamp from checkpoint data."""
    if not isinstance(value, str):
        raise CorruptCheckpointError(f"{field_name} must be an ISO timestamp")
    try:
        timestamp = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise CorruptCheckpointError(
            f"{field_name} must be an ISO timestamp") from error
    try:
        _require_aware(timestamp)
    except ValueError as error:
        raise CorruptCheckpointError(
            f"{field_name} must be timezone-aware") from error
    return timestamp


def _validate_run_directory(output_root: Path, run_directory: Path) -> Path:
    """Return a lexical direct date child for descriptor-anchored access."""
    root = Path(output_root).expanduser().absolute()
    candidate = Path(run_directory).expanduser().absolute()
    if candidate.parent != root:
        raise UnsafeRunPathError(
            "run directory must be a direct child of the output root")
    try:
        parsed_date = dt.date.fromisoformat(candidate.name)
    except ValueError as error:
        raise UnsafeRunPathError(
            "run directory name must use YYYY-MM-DD") from error
    if parsed_date.isoformat() != candidate.name:
        raise UnsafeRunPathError(
            "run directory name must use canonical YYYY-MM-DD")
    return candidate


def _validate_checkpoint_shape(checkpoint: Any) -> Dict[str, Any]:
    """Validate required checkpoint fields without checking current inputs."""
    if not isinstance(checkpoint, dict):
        raise CorruptCheckpointError("checkpoint root must be an object")
    required_fields = (
        "schema_version",
        "runner_version",
        "run_id",
        "started_at",
        "last_resumed_at",
        "source_commit",
        "config_fingerprint",
        "generation",
        "status",
        "phases",
    )
    missing = [field for field in required_fields if field not in checkpoint]
    if missing:
        raise CorruptCheckpointError(
            f"checkpoint is missing required field: {missing[0]}")
    try:
        uuid.UUID(str(checkpoint["run_id"]))
    except (ValueError, AttributeError) as error:
        raise CorruptCheckpointError("run_id must be a UUID") from error
    _parse_aware_timestamp(checkpoint["started_at"], "started_at")
    _parse_aware_timestamp(checkpoint["last_resumed_at"], "last_resumed_at")
    if not isinstance(checkpoint["source_commit"], str):
        raise CorruptCheckpointError("source_commit must be a string")
    if not isinstance(checkpoint["config_fingerprint"], str):
        raise CorruptCheckpointError("config_fingerprint must be a string")
    generation = checkpoint["generation"]
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 0):
        raise CorruptCheckpointError(
            "checkpoint generation must be a nonnegative integer")
    if (not isinstance(checkpoint["status"], str)
            or checkpoint["status"] not in RUN_STATUSES):
        raise CorruptCheckpointError("checkpoint status is invalid")
    if not isinstance(checkpoint["phases"], list):
        raise CorruptCheckpointError("phases must be a list")
    phase_statuses = _validate_phases(checkpoint["phases"])
    _validate_run_status(checkpoint["status"], phase_statuses)
    return checkpoint


def _require_record_id(record: Dict[str, Any], record_type: str) -> str:
    """Return a nonempty record ID or reject the malformed record."""
    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise CorruptCheckpointError(
            f"{record_type} id must be a nonempty string")
    return record_id


def _validate_counter(
        record: Dict[str, Any], field_name: str, case_id: str) -> int:
    """Return one required nonnegative integer case counter."""
    value = record.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CorruptCheckpointError(
            f"case {case_id} {field_name} must be a nonnegative integer")
    return value


def _validate_optional_timestamp(
        case: Dict[str, Any], field_name: str) -> None:
    """Validate a case timestamp when the field contains a value."""
    if case.get(field_name) is not None:
        _parse_aware_timestamp(case[field_name], field_name)


def _validate_case(case: Any) -> str:
    """Validate one case lifecycle record and return its stable ID."""
    if not isinstance(case, dict):
        raise CorruptCheckpointError("case records must be objects")
    case_id = _require_record_id(case, "case")
    status = case.get("status")
    if not isinstance(status, str) or status not in CASE_STATUSES:
        raise CorruptCheckpointError(f"case status is invalid: {case_id}")
    attempt_count = _validate_counter(case, "attempt_count", case_id)
    _validate_counter(case, "interruption_count", case_id)
    _validate_optional_timestamp(case, "started_at")
    _validate_optional_timestamp(case, "completed_at")
    _validate_optional_timestamp(case, "last_interrupted_at")

    if status == "running":
        if attempt_count == 0:
            raise CorruptCheckpointError(
                f"running case must have an attempt: {case_id}")
        if case.get("started_at") is None:
            raise CorruptCheckpointError(
                f"running case must have started_at: {case_id}")
    if status in {"pending", "running"} and case.get("completed_at") is not None:
        raise CorruptCheckpointError(
            f"nonterminal case cannot have completed_at: {case_id}")
    if status in TERMINAL_CASE_STATUSES and case.get("completed_at") is None:
        raise CorruptCheckpointError(
            f"terminal case must have completed_at: {case_id}")
    if status in {"passed", "failed"} and attempt_count == 0:
        raise CorruptCheckpointError(
            f"executed terminal case must have an attempt: {case_id}")
    return case_id


def _aggregate_case_statuses(cases: List[Dict[str, Any]]) -> str:
    """Derive one phase status from its validated case lifecycle states."""
    statuses = [case["status"] for case in cases]
    if "running" in statuses:
        return "running"
    if not statuses or "pending" in statuses:
        return "pending"
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if all(status == "excluded" for status in statuses):
        return "excluded"
    return "passed"


def _validate_phases(phases: List[Any]) -> List[str]:
    """Validate nested records and return their derived aggregate statuses."""
    phase_ids = set()
    case_ids = set()
    derived_statuses = []
    for phase in phases:
        if not isinstance(phase, dict):
            raise CorruptCheckpointError("phase records must be objects")
        phase_id = _require_record_id(phase, "phase")
        if phase_id in phase_ids:
            raise CorruptCheckpointError(f"duplicate phase id: {phase_id}")
        phase_ids.add(phase_id)
        cases = phase.get("cases")
        if not isinstance(cases, list):
            raise CorruptCheckpointError(
                f"phase cases must be a list: {phase_id}")
        for case in cases:
            case_id = _validate_case(case)
            if case_id in case_ids:
                raise CorruptCheckpointError(f"duplicate case id: {case_id}")
            case_ids.add(case_id)
        derived_status = _aggregate_case_statuses(cases)
        derived_statuses.append(derived_status)
        if "status" in phase:
            phase_status = phase["status"]
            if (not isinstance(phase_status, str)
                    or phase_status not in PHASE_STATUSES):
                raise CorruptCheckpointError(
                    f"phase status is invalid: {phase_id}")
            if phase_status != derived_status:
                raise CorruptCheckpointError(
                    f"phase status disagrees with cases: {phase_id}")
    return derived_statuses


def _validate_run_status(run_status: str, phase_statuses: List[str]) -> None:
    """Reject aggregate run states that contradict nested phase outcomes."""
    successful = bool(phase_statuses) and all(
        status in {"passed", "excluded"} for status in phase_statuses)
    has_unfinished = any(
        status in {"pending", "running"} for status in phase_statuses)
    has_failed = "failed" in phase_statuses
    has_blocked = "blocked" in phase_statuses
    if run_status == "passed" and not successful:
        raise CorruptCheckpointError("run status passed disagrees with phases")
    if run_status == "failed" and (has_unfinished or not has_failed):
        raise CorruptCheckpointError("run status failed disagrees with phases")
    if run_status == "blocked" and (
            has_unfinished or has_failed or not has_blocked):
        raise CorruptCheckpointError("run status blocked disagrees with phases")
    if run_status == "incomplete" and successful:
        raise CorruptCheckpointError(
            "run status incomplete disagrees with completed phases")


def _validate_compatibility(
        checkpoint: Dict[str, Any], source_commit: str,
        config_fingerprint: str) -> None:
    """Reject state that cannot safely share evidence with this invocation."""
    if checkpoint["schema_version"] != SCHEMA_VERSION:
        raise IncompatibleCheckpointError(
            "checkpoint schema version is incompatible")
    if checkpoint["runner_version"] != RUNNER_VERSION:
        raise IncompatibleCheckpointError(
            "checkpoint runner version is incompatible")
    if checkpoint["source_commit"] != source_commit:
        raise IncompatibleCheckpointError(
            "checkpoint source commit is incompatible")
    if checkpoint["config_fingerprint"] != config_fingerprint:
        raise IncompatibleCheckpointError(
            "checkpoint configuration fingerprint is incompatible")


def _unsafe_open_error(error: OSError, description: str) -> None:
    """Translate symlink and non-directory open failures to a state error."""
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        raise UnsafeRunPathError(description) from error
    raise error


def _open_directory_at(parent_fd: int, name: str) -> int:
    """Open one child directory without following its final path component."""
    try:
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        _unsafe_open_error(error, f"directory cannot be safely opened: {name}")
    raise AssertionError("unreachable directory open")


def _open_output_root(output_root: Path, create: bool) -> int:
    """Open the output root securely, durably creating it when requested."""
    root = Path(output_root).expanduser().absolute()
    try:
        return os.open(root, DIRECTORY_FLAGS)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as error:
        _unsafe_open_error(error, "output root must be a real directory")

    try:
        parent_fd = os.open(root.parent, DIRECTORY_FLAGS)
    except OSError as error:
        _unsafe_open_error(error, "output parent must be a real directory")
    try:
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        return _open_directory_at(parent_fd, root.name)
    finally:
        os.close(parent_fd)


def _open_run_directory(
        output_root: Path, run_directory: Path,
        create: bool) -> tuple[int, int, Path]:
    """Open root and run directory descriptors anchored against path swaps."""
    safe_directory = _validate_run_directory(output_root, run_directory)
    root_fd = _open_output_root(output_root, create=create)
    try:
        try:
            run_fd = _open_directory_at(root_fd, safe_directory.name)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(safe_directory.name, mode=0o700, dir_fd=root_fd)
                os.fsync(root_fd)
            except FileExistsError:
                pass
            run_fd = _open_directory_at(root_fd, safe_directory.name)
    except BaseException:
        os.close(root_fd)
        raise
    return root_fd, run_fd, safe_directory


def _open_file_at(
        directory_fd: int, filename: str, flags: int,
        mode: int = 0o600) -> int:
    """Open a run file without following a symlink at its final component."""
    try:
        return os.open(
            filename, flags | FILE_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            mode, dir_fd=directory_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise UnsafeRunPathError(
                f"run artifact cannot be a symlink: {filename}") from error
        raise


def _entry_exists(directory_fd: int, filename: str) -> bool:
    """Return whether a non-symlink artifact exists below an open run directory."""
    try:
        entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(entry.st_mode):
        raise UnsafeRunPathError(
            f"run artifact cannot be a symlink: {filename}")
    if not stat.S_ISREG(entry.st_mode):
        raise UnsafeRunPathError(
            f"run artifact must be a regular file: {filename}")
    return True


def _read_checkpoint_at(run_fd: int, display_path: Path) -> Dict[str, Any]:
    """Read and validate checkpoint JSON relative to an anchored directory."""
    try:
        descriptor = _open_file_at(run_fd, CHECKPOINT_FILENAME, os.O_RDONLY)
        with os.fdopen(descriptor, encoding="utf-8") as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
    except (OSError, json.JSONDecodeError) as error:
        raise CorruptCheckpointError(
            f"cannot read checkpoint: {display_path}") from error
    return _validate_checkpoint_shape(checkpoint)


def _close_run_directories(root_fd: int, run_fd: int) -> None:
    """Close a paired run directory and output root descriptor."""
    try:
        os.close(run_fd)
    finally:
        os.close(root_fd)


def create_checkpoint(
        source_commit: str, config_fingerprint: str,
        now: dt.datetime) -> Dict[str, Any]:
    """Create a fresh schema-v1 checkpoint with immutable run identity."""
    _require_aware(now)
    timestamp = now.isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "run_id": str(uuid.uuid4()),
        "started_at": timestamp,
        "last_resumed_at": timestamp,
        "source_commit": source_commit,
        "config_fingerprint": config_fingerprint,
        "generation": 0,
        "status": "incomplete",
        "phases": [],
    }


def load_checkpoint(
        output_root: Path, run_directory: Path) -> Dict[str, Any]:
    """Load and structurally validate one checkpoint from a safe run path."""
    root_fd, run_fd, safe_directory = _open_run_directory(
        output_root, run_directory, create=False)
    try:
        return _read_checkpoint_at(
            run_fd, safe_directory / CHECKPOINT_FILENAME)
    finally:
        _close_run_directories(root_fd, run_fd)


def save_checkpoint(
        output_root: Path, run_directory: Path,
        checkpoint: Dict[str, Any]) -> None:
    """Atomically and durably save a checkpoint without changing run identity."""
    validated = _validate_checkpoint_shape(checkpoint)
    root_fd, run_fd, safe_directory = _open_run_directory(
        output_root, run_directory, create=True)
    temporary_name = None
    try:
        persisted = dict(validated)
        if _entry_exists(run_fd, CHECKPOINT_FILENAME):
            existing = _read_checkpoint_at(
                run_fd, safe_directory / CHECKPOINT_FILENAME)
            for immutable_field in (
                    "schema_version", "runner_version", "run_id", "started_at",
                    "source_commit", "config_fingerprint"):
                if existing[immutable_field] != validated[immutable_field]:
                    raise ImmutableRunIdentityError(
                        f"cannot change immutable {immutable_field}")
            if existing["generation"] != validated["generation"]:
                raise ConcurrentCheckpointUpdateError(
                    "checkpoint changed after it was loaded")
            persisted["generation"] = validated["generation"] + 1
        elif validated["generation"] != 0:
            raise ConcurrentCheckpointUpdateError(
                "new checkpoint must start at generation zero")

        temporary_name = f".checkpoint.{uuid.uuid4().hex}.tmp"
        descriptor = _open_file_at(
            run_fd, temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        with os.fdopen(descriptor, mode="w", encoding="utf-8") as output:
            json.dump(persisted, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary_name, CHECKPOINT_FILENAME,
            src_dir_fd=run_fd, dst_dir_fd=run_fd)
        temporary_name = None
        os.fsync(run_fd)
        checkpoint["generation"] = persisted["generation"]
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=run_fd)
            except FileNotFoundError:
                pass
        _close_run_directories(root_fd, run_fd)


def save_run_artifact(
        output_root: Path, run_directory: Path, filename: str,
        content: bytes, preserve_existing: bool = False) -> None:
    """Atomically save one direct-child artifact while its caller owns the lock."""
    if (not filename or Path(filename).name != filename
            or filename in {".", "..", CHECKPOINT_FILENAME, LOCK_FILENAME}):
        raise UnsafeRunPathError("artifact name must be a safe direct child")
    if not isinstance(content, bytes):
        raise TypeError("artifact content must be bytes")
    root_fd, run_fd, _ = _open_run_directory(
        output_root, run_directory, create=False)
    temporary_name = None
    try:
        if preserve_existing and _entry_exists(run_fd, filename):
            return
        temporary_name = f".{filename}.{uuid.uuid4().hex}.tmp"
        descriptor = _open_file_at(
            run_fd, temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        with os.fdopen(descriptor, mode="wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary_name, filename,
            src_dir_fd=run_fd, dst_dir_fd=run_fd)
        temporary_name = None
        os.fsync(run_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=run_fd)
            except FileNotFoundError:
                pass
        _close_run_directories(root_fd, run_fd)


def recover_interrupted_cases(
        checkpoint: Dict[str, Any], now: dt.datetime) -> bool:
    """Reset stale running cases to pending and count their interruptions."""
    _require_aware(now)
    _validate_checkpoint_shape(checkpoint)
    changed = False
    for phase in checkpoint.get("phases", []):
        cases = phase.get("cases", [])
        phase_changed = False
        for case in cases:
            if case.get("status") == "running":
                case["status"] = "pending"
                case["interruption_count"] = (
                    int(case.get("interruption_count", 0)) + 1)
                case["last_interrupted_at"] = now.isoformat()
                changed = True
                phase_changed = True
        if phase_changed and "status" in phase:
            phase["status"] = _aggregate_case_statuses(cases)
    return changed


def retryable_case_ids(checkpoint: Dict[str, Any]) -> List[str]:
    """Return failed and pending case IDs in their declared execution order."""
    _validate_checkpoint_shape(checkpoint)
    retryable = []
    for phase in checkpoint.get("phases", []):
        for case in phase.get("cases", []):
            if case.get("status") in {"failed", "pending"}:
                retryable.append(case["id"])
    return retryable


def _is_incomplete(checkpoint: Dict[str, Any]) -> bool:
    """Return whether a checkpoint still permits unfinished or failed work."""
    return checkpoint["status"] != "passed"


def _backup_name(run_fd: int, filename: str, suffix: str) -> str:
    """Return a collision-safe backup name below one anchored directory."""
    source = Path(filename)
    backup = f"{source.stem}.backup-{suffix}{source.suffix}"
    counter = 1
    while _entry_exists(run_fd, backup):
        backup = (
            f"{source.stem}.backup-{suffix}-{counter}{source.suffix}")
        counter += 1
    return backup


def _backup_existing_artifacts(
        output_root: Path, run_directory: Path, now: dt.datetime) -> None:
    """Durably move reports first and checkpoint last as the commit point."""
    _require_aware(now)
    suffix = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root_fd, run_fd, _ = _open_run_directory(
        output_root, run_directory, create=True)
    try:
        reports_moved = False
        for filename in REPORT_FILENAMES:
            if not _entry_exists(run_fd, filename):
                continue
            backup = _backup_name(run_fd, filename, suffix)
            os.replace(
                filename, backup, src_dir_fd=run_fd, dst_dir_fd=run_fd)
            reports_moved = True
        if reports_moved:
            os.fsync(run_fd)

        if _entry_exists(run_fd, CHECKPOINT_FILENAME):
            backup = _backup_name(run_fd, CHECKPOINT_FILENAME, suffix)
            os.replace(
                CHECKPOINT_FILENAME, backup,
                src_dir_fd=run_fd, dst_dir_fd=run_fd)
            os.fsync(run_fd)
    finally:
        _close_run_directories(root_fd, run_fd)


def _checkpoint_exists(output_root: Path, run_directory: Path) -> bool:
    """Check for a canonical checkpoint through securely opened directories."""
    try:
        root_fd, run_fd, _ = _open_run_directory(
            output_root, run_directory, create=False)
    except FileNotFoundError:
        return False
    try:
        return _entry_exists(run_fd, CHECKPOINT_FILENAME)
    finally:
        _close_run_directories(root_fd, run_fd)


def _resume_selection(
        output_root: Path, run_directory: Path,
        source_commit: str, config_fingerprint: str,
        now: dt.datetime) -> RunSelection:
    """Validate, recover, persist, and return an existing run selection."""
    lock = RunLock(output_root, run_directory)
    lock.acquire()
    try:
        current = load_checkpoint(output_root, run_directory)
        if not _is_incomplete(current):
            raise NoResumableRunError(
                "selected checkpoint completed before its lock was acquired")
        _validate_compatibility(current, source_commit, config_fingerprint)
        current["last_resumed_at"] = now.isoformat()
        recover_interrupted_cases(current, now)
        save_checkpoint(output_root, run_directory, current)
        return RunSelection(
            run_directory, current, resumed=True, lock=lock)
    except BaseException:
        lock.release()
        raise


def _new_selection(
        output_root: Path, run_directory: Path, source_commit: str,
        config_fingerprint: str, now: dt.datetime) -> RunSelection:
    """Create, persist, and return a new run selection."""
    lock = RunLock(output_root, run_directory)
    lock.acquire()
    try:
        _backup_existing_artifacts(output_root, run_directory, now)
        checkpoint = create_checkpoint(source_commit, config_fingerprint, now)
        save_checkpoint(output_root, run_directory, checkpoint)
        return RunSelection(
            run_directory, checkpoint, resumed=False, lock=lock)
    except BaseException:
        lock.release()
        raise


def select_run(
        output_root: Path, mode: RunMode, source_commit: str,
        config_fingerprint: str, now: dt.datetime) -> RunSelection:
    """Select or create a run according to default, resume, or restart mode."""
    _require_aware(now)
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise UnsafeRunPathError("output root must be a real directory")
    today_directory = _validate_run_directory(
        root, root / now.date().isoformat())

    if mode == RunMode.DEFAULT:
        if _checkpoint_exists(root, today_directory):
            checkpoint = load_checkpoint(root, today_directory)
            if _is_incomplete(checkpoint):
                return _resume_selection(
                    root, today_directory, source_commit,
                    config_fingerprint, now)
        return _new_selection(
            root, today_directory, source_commit, config_fingerprint, now)

    if mode == RunMode.RESTART:
        return _new_selection(
            root, today_directory, source_commit, config_fingerprint, now)

    if mode != RunMode.RESUME:
        raise ValueError(f"unsupported run mode: {mode}")

    candidates = []
    if root.exists():
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                safe_child = _validate_run_directory(root, child)
            except UnsafeRunPathError:
                continue
            if not _checkpoint_exists(root, safe_child):
                continue
            checkpoint = load_checkpoint(root, safe_child)
            if _is_incomplete(checkpoint):
                started_at = _parse_aware_timestamp(
                    checkpoint["started_at"], "started_at")
                candidates.append((started_at, safe_child))
    if not candidates:
        raise NoResumableRunError("no incomplete checkpoint is available")
    _, run_directory = max(candidates, key=lambda item: item[0])
    return _resume_selection(
        root, run_directory, source_commit,
        config_fingerprint, now)


def preview_run(
        output_root: Path, mode: RunMode, source_commit: str,
        config_fingerprint: str, now: dt.datetime) -> RunPreview:
    """Preview run selection without creating, locking, or updating any file."""
    _require_aware(now)
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise UnsafeRunPathError("output root must be a real directory")
    today_directory = _validate_run_directory(
        root, root / now.date().isoformat())

    if mode == RunMode.RESTART:
        return RunPreview(today_directory, checkpoint=None, resumed=False)

    if mode == RunMode.DEFAULT:
        if not _checkpoint_exists(root, today_directory):
            return RunPreview(today_directory, checkpoint=None, resumed=False)
        checkpoint = load_checkpoint(root, today_directory)
        if not _is_incomplete(checkpoint):
            return RunPreview(today_directory, checkpoint=None, resumed=False)
        _validate_compatibility(
            checkpoint, source_commit, config_fingerprint)
        return RunPreview(today_directory, checkpoint=checkpoint, resumed=True)

    if mode != RunMode.RESUME:
        raise ValueError(f"unsupported run mode: {mode}")

    candidates = []
    if root.exists():
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                safe_child = _validate_run_directory(root, child)
            except UnsafeRunPathError:
                continue
            if not _checkpoint_exists(root, safe_child):
                continue
            checkpoint = load_checkpoint(root, safe_child)
            if _is_incomplete(checkpoint):
                started_at = _parse_aware_timestamp(
                    checkpoint["started_at"], "started_at")
                candidates.append((started_at, safe_child, checkpoint))
    if not candidates:
        raise NoResumableRunError("no incomplete checkpoint is available")
    _, run_directory, checkpoint = max(
        candidates, key=lambda item: item[0])
    _validate_compatibility(checkpoint, source_commit, config_fingerprint)
    return RunPreview(run_directory, checkpoint=checkpoint, resumed=True)


def preview_run_path(
        output_root: Path, mode: RunMode,
        now: dt.datetime) -> RunPreview:
    """Select a read-only candidate path before build identity is available."""
    _require_aware(now)
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise UnsafeRunPathError("output root must be a real directory")
    today_directory = _validate_run_directory(
        root, root / now.date().isoformat())
    if mode == RunMode.RESTART:
        return RunPreview(today_directory, checkpoint=None, resumed=False)
    if mode == RunMode.DEFAULT:
        if not _checkpoint_exists(root, today_directory):
            return RunPreview(today_directory, checkpoint=None, resumed=False)
        checkpoint = load_checkpoint(root, today_directory)
        if not _is_incomplete(checkpoint):
            return RunPreview(today_directory, checkpoint=None, resumed=False)
        return RunPreview(today_directory, checkpoint=checkpoint, resumed=True)
    if mode != RunMode.RESUME:
        raise ValueError(f"unsupported run mode: {mode}")
    candidates = []
    if root.exists():
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                safe_child = _validate_run_directory(root, child)
            except UnsafeRunPathError:
                continue
            if not _checkpoint_exists(root, safe_child):
                continue
            checkpoint = load_checkpoint(root, safe_child)
            if _is_incomplete(checkpoint):
                started_at = _parse_aware_timestamp(
                    checkpoint["started_at"], "started_at")
                candidates.append((started_at, safe_child, checkpoint))
    if not candidates:
        raise NoResumableRunError("no incomplete checkpoint is available")
    _, run_directory, checkpoint = max(
        candidates, key=lambda item: item[0])
    return RunPreview(run_directory, checkpoint=checkpoint, resumed=True)
