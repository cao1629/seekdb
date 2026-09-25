#!/usr/bin/env python3
"""Build and validate the standalone mysqltest phase without merging host/device claims."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

import mysqltest_parser


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
    """Validate one existing host mysqltest result without promoting it to device."""
    selected = [
        case.name for case in mysqltest_parser.discover_active_cases(repo_root)
        if case.ci_selected]
    try:
        payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MysqltestPhaseError("host mysqltest result is unavailable") from error
    if not isinstance(payload, dict):
        raise MysqltestPhaseError("host mysqltest gate did not pass exact coverage")
    slice_result = (
        payload.get("error") is None
        and payload.get("cases") == selected)
    aggregate_result = (
        isinstance(payload.get("run_id"), str)
        and bool(payload.get("run_id"))
        and isinstance(payload.get("slice_count"), int)
        and payload.get("slice_count") > 0
        and payload.get("errors") == [])
    if (payload.get("success") is not True
            or payload.get("failed_cases") != []
            or payload.get("case_count") != len(selected)
            or not (slice_result or aggregate_result)):
        raise MysqltestPhaseError("host mysqltest gate did not pass exact coverage")
    return {
        "execution_class": "host-only",
        "case_count": len(selected),
        "success": True,
    }


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
    parser.add_argument("--host-result", type=Path)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] = None) -> int:
    """Write either the complete phase plan or a separate host-gate summary."""
    options = parse_args(arguments)
    try:
        if options.host_result is not None:
            payload = validate_host_gate(options.repo_root, options.host_result)
        else:
            payload = {
                "cases": [asdict(case) for case in build_phase_plan(
                    options.repo_root)],
            }
        _write_json(options.output, payload)
    except MysqltestPhaseError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
