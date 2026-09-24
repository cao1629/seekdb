#!/usr/bin/env python3
"""Durable run selection and checkpoint storage for iPhone tests."""

from dataclasses import dataclass
import datetime as dt
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List
import uuid


SCHEMA_VERSION = 1
RUNNER_VERSION = "1"
CHECKPOINT_FILENAME = "checkpoint.json"
REPORT_FILENAMES = ("summary.json", "summary.md")


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


class RunMode(Enum):
    """Select how a runner invocation obtains its checkpoint."""

    DEFAULT = "default"
    RESUME = "resume"
    RESTART = "restart"


@dataclass(frozen=True)
class RunSelection:
    """Describe the selected directory, checkpoint, and resume decision."""

    run_directory: Path
    checkpoint: Dict[str, Any]
    resumed: bool


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
    """Return a safe direct date child, rejecting traversal and symlinks."""
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
    if root.is_symlink() or candidate.is_symlink():
        raise UnsafeRunPathError("output and run directories cannot be symlinks")
    if root.exists() and not root.is_dir():
        raise UnsafeRunPathError("output root must be a directory")
    if candidate.exists() and not candidate.is_dir():
        raise UnsafeRunPathError("run path must be a directory")
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
    if not isinstance(checkpoint["status"], str):
        raise CorruptCheckpointError("status must be a string")
    if not isinstance(checkpoint["phases"], list):
        raise CorruptCheckpointError("phases must be a list")
    return checkpoint


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


def _fsync_directory(directory: Path) -> None:
    """Flush directory metadata after an atomic file operation."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        "status": "incomplete",
        "phases": [],
    }


def load_checkpoint(
        output_root: Path, run_directory: Path) -> Dict[str, Any]:
    """Load and structurally validate one checkpoint from a safe run path."""
    safe_directory = _validate_run_directory(output_root, run_directory)
    checkpoint_path = safe_directory / CHECKPOINT_FILENAME
    if checkpoint_path.is_symlink():
        raise UnsafeRunPathError("checkpoint cannot be a symlink")
    try:
        with checkpoint_path.open(encoding="utf-8") as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
    except (OSError, json.JSONDecodeError) as error:
        raise CorruptCheckpointError(
            f"cannot read checkpoint: {checkpoint_path}") from error
    return _validate_checkpoint_shape(checkpoint)


def save_checkpoint(
        output_root: Path, run_directory: Path,
        checkpoint: Dict[str, Any]) -> None:
    """Atomically and durably save a checkpoint without changing run identity."""
    safe_directory = _validate_run_directory(output_root, run_directory)
    validated = _validate_checkpoint_shape(checkpoint)
    safe_directory.mkdir(parents=True, exist_ok=True)
    checkpoint_path = safe_directory / CHECKPOINT_FILENAME
    if checkpoint_path.exists():
        existing = load_checkpoint(output_root, safe_directory)
        for immutable_field in (
                "schema_version", "runner_version", "run_id", "started_at",
                "source_commit", "config_fingerprint"):
            if existing[immutable_field] != validated[immutable_field]:
                raise ImmutableRunIdentityError(
                    f"cannot change immutable {immutable_field}")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=safe_directory,
                prefix=".checkpoint.", suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(validated, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, checkpoint_path)
        temporary_path = None
        _fsync_directory(safe_directory)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def recover_interrupted_cases(
        checkpoint: Dict[str, Any], now: dt.datetime) -> bool:
    """Reset stale running cases to pending and count their interruptions."""
    _require_aware(now)
    changed = False
    for phase in checkpoint.get("phases", []):
        if not isinstance(phase, dict):
            raise CorruptCheckpointError("phase records must be objects")
        cases = phase.get("cases", [])
        if not isinstance(cases, list):
            raise CorruptCheckpointError("phase cases must be a list")
        for case in cases:
            if not isinstance(case, dict):
                raise CorruptCheckpointError("case records must be objects")
            if case.get("status") == "running":
                case["status"] = "pending"
                case["interruption_count"] = (
                    int(case.get("interruption_count", 0)) + 1)
                case["last_interrupted_at"] = now.isoformat()
                changed = True
    return changed


def retryable_case_ids(checkpoint: Dict[str, Any]) -> List[str]:
    """Return failed and pending case IDs in their declared execution order."""
    retryable = []
    for phase in checkpoint.get("phases", []):
        for case in phase.get("cases", []):
            if case.get("status") in {"failed", "pending"}:
                retryable.append(case["id"])
    return retryable


def _is_incomplete(checkpoint: Dict[str, Any]) -> bool:
    """Return whether a checkpoint still permits unfinished or failed work."""
    return checkpoint["status"] != "passed"


def _backup_existing_artifacts(run_directory: Path, now: dt.datetime) -> None:
    """Move current checkpoint and reports to collision-safe backup names."""
    _require_aware(now)
    suffix = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    moved = False
    for filename in (CHECKPOINT_FILENAME,) + REPORT_FILENAMES:
        source = run_directory / filename
        if not source.exists():
            continue
        source_suffix = source.suffix
        backup = source.with_name(
            f"{source.stem}.backup-{suffix}{source_suffix}")
        counter = 1
        while backup.exists():
            backup = source.with_name(
                f"{source.stem}.backup-{suffix}-{counter}{source_suffix}")
            counter += 1
        os.replace(source, backup)
        moved = True
    if moved:
        _fsync_directory(run_directory)


def _resume_selection(
        output_root: Path, run_directory: Path,
        checkpoint: Dict[str, Any], source_commit: str,
        config_fingerprint: str, now: dt.datetime) -> RunSelection:
    """Validate, recover, persist, and return an existing run selection."""
    _validate_compatibility(checkpoint, source_commit, config_fingerprint)
    checkpoint["last_resumed_at"] = now.isoformat()
    recover_interrupted_cases(checkpoint, now)
    save_checkpoint(output_root, run_directory, checkpoint)
    return RunSelection(run_directory, checkpoint, resumed=True)


def _new_selection(
        output_root: Path, run_directory: Path, source_commit: str,
        config_fingerprint: str, now: dt.datetime) -> RunSelection:
    """Create, persist, and return a new run selection."""
    checkpoint = create_checkpoint(source_commit, config_fingerprint, now)
    save_checkpoint(output_root, run_directory, checkpoint)
    return RunSelection(run_directory, checkpoint, resumed=False)


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
        checkpoint_path = today_directory / CHECKPOINT_FILENAME
        if checkpoint_path.exists():
            checkpoint = load_checkpoint(root, today_directory)
            if _is_incomplete(checkpoint):
                return _resume_selection(
                    root, today_directory, checkpoint, source_commit,
                    config_fingerprint, now)
            _backup_existing_artifacts(today_directory, now)
        return _new_selection(
            root, today_directory, source_commit, config_fingerprint, now)

    if mode == RunMode.RESTART:
        if today_directory.exists():
            _backup_existing_artifacts(today_directory, now)
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
            if not (safe_child / CHECKPOINT_FILENAME).is_file():
                continue
            checkpoint = load_checkpoint(root, safe_child)
            if _is_incomplete(checkpoint):
                started_at = _parse_aware_timestamp(
                    checkpoint["started_at"], "started_at")
                candidates.append((started_at, safe_child, checkpoint))
    if not candidates:
        raise NoResumableRunError("no incomplete checkpoint is available")
    _, run_directory, checkpoint = max(candidates, key=lambda item: item[0])
    return _resume_selection(
        root, run_directory, checkpoint, source_commit,
        config_fingerprint, now)
