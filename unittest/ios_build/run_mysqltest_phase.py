#!/usr/bin/env python3
"""Build and validate the standalone mysqltest phase without merging host/device claims."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping, Sequence

import mysqltest_parser

MAX_HOST_RESULT_BYTES = 8 * 1024 * 1024


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


def validate_host_gate(repo_root: Path, result_path: Path) -> dict:
    """Validate identity-bound exact host mysqltest evidence."""
    selected = [
        case.name for case in mysqltest_parser.discover_active_cases(repo_root)
        if case.ci_selected]
    try:
        payload = json.loads(_read_host_result(result_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
    binaries = payload.get("host_binaries")
    binary_names = ("seekdb", "obclient", "mysqltest")
    valid_binaries = (
        isinstance(binaries, dict) and set(binaries) == set(binary_names))
    if valid_binaries:
        valid_binaries = all(
            isinstance(binaries[name], dict)
            and set(binaries[name]) == {"sha256", "size"}
            and isinstance(binaries[name]["sha256"], str)
            and len(binaries[name]["sha256"]) == 64
            and all(character in "0123456789abcdef"
                    for character in binaries[name]["sha256"])
            and isinstance(binaries[name]["size"], int)
            and binaries[name]["size"] >= 0
            for name in binary_names)
    serialized = json.dumps(
        binaries, sort_keys=True,
        separators=(",", ":")) if valid_binaries else ""
    build_identity = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    try:
        current_commit = host.source_commit(repo_root)
        current_corpus = host.mysqltest_corpus_digest(repo_root)
    except Exception as error:
        raise MysqltestPhaseError(
            "host mysqltest evidence identity is unavailable") from error
    if (not host.verify_evidence_digest(payload)
            or payload.get("schema_version") != host.EVIDENCE_SCHEMA_VERSION
            or payload.get("producer") != host.EVIDENCE_PRODUCER
            or payload.get("source_commit") != current_commit
            or payload.get("corpus_digest") != current_corpus
            or payload.get("host_build_identity") != build_identity
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
    return {
        "execution_class": "host-only",
        "case_count": len(selected),
        "success": True,
    }


def _read_host_result(path: Path) -> bytes:
    """Read one bounded stable regular evidence file without following links."""
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(str(path), flags)
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


def _write_json(path: Path, payload) -> None:
    """Atomically write deterministic phase metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def parse_args(arguments: Sequence[str] = None) -> argparse.Namespace:
    """Parse classification or host-result validation arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(arguments)


def main(
        arguments: Sequence[str] = None,
        environment: Mapping[str, str] = None) -> int:
    """Write either the complete phase plan or a separate host-gate summary."""
    options = parse_args(arguments)
    try:
        local_environment = os.environ if environment is None else environment
        host_result = local_environment.get("SEEKDB_IPHONE_HOST_MYSQLTEST_RESULT")
        if host_result:
            payload = validate_host_gate(options.repo_root, Path(host_result))
        else:
            raise MysqltestPhaseError("host mysqltest result is unavailable")
        _write_json(options.output, payload)
    except MysqltestPhaseError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
