#!/usr/bin/env python3
"""Execute standalone iPhone test phases with durable, sanitized evidence."""

from dataclasses import dataclass, field
import datetime as dt
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

import iphone_test_state as state


PHASE_IDS = (
    "inventory",
    "registry-smoke",
    "cpp-device-equivalents",
    "rust-device-runtime",
    "mysqltest",
    "vector",
    "lifecycle-memory",
    "final-matrix",
)
EXECUTION_CLASSES = (
    "device-native", "host-driven-device", "host-only")
APPLICABILITY_VALUES = ("applicable", "not-applicable")
RESULT_VALUES = ("passed", "failed", "blocked", "excluded", "incomplete")
MAX_DIAGNOSTIC_LENGTH = 2048
MAX_PROCESS_OUTPUT_LENGTH = 4096
REDACTED = "[REDACTED]"
_RUNTIME_REDACTION_TOKENS: Dict[str, tuple[str, ...]] = {}
UUID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\b")
SENSITIVE_KEYS = frozenset({
    "account",
    "apple_team_id",
    "certificate",
    "certificate_identity",
    "device_id",
    "identifier",
    "private_key",
    "provisioning_content",
    "provisioning_profile",
    "signing_identity",
    "team_id",
    "udid",
})
SENSITIVE_KEY_EXPRESSIONS = tuple(
    r"[\s_-]+".join(re.escape(part) for part in key.split("_"))
    for key in sorted(SENSITIVE_KEYS, key=len, reverse=True)
)
SENSITIVE_LABEL_PATTERN = re.compile(
    r"(?im)(?<!\w)['\"]?(?:" + "|".join(SENSITIVE_KEY_EXPRESSIONS)
    + r")\b['\"]?\s*[:=]")
APPLE_UDID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-?[0-9a-f]{16}\b")
APPLE_CERTIFICATE_IDENTITY_PATTERN = re.compile(
    r"(?i)\b(?:Apple\s+(?:Development|Distribution)|"
    r"iPhone\s+Distribution)\s*:")
CANONICAL_SIGNING_IDENTITY_PATTERN = re.compile(
    r"(?i)\b(?:Apple\s+(?:Development|Distribution)|"
    r"iPhone\s+Distribution|Developer\s+ID\s+(?:Application|Installer))"
    r"\s*:[^\r\n]*\([A-Z0-9]{10}\)")
class FailureCategory(str, Enum):
    """Classify failures that drive continuation and final reporting."""

    ASSERTION = "assertion"
    CLEANUP = "cleanup"
    EVIDENCE = "evidence"
    INFRASTRUCTURE = "infrastructure"
    TIMEOUT = "timeout"


FAILURE_CATEGORIES = frozenset(category.value for category in FailureCategory)
UNSAFE_FAILURE_CATEGORIES = frozenset({
    FailureCategory.CLEANUP.value,
    FailureCategory.EVIDENCE.value,
    FailureCategory.INFRASTRUCTURE.value,
    FailureCategory.TIMEOUT.value,
})


@dataclass(frozen=True)
class CaseSpec:
    """Describe one stable case and its execution and isolation boundaries."""

    case_id: str
    execution_class: str
    applicability: str = "applicable"
    isolated: bool = True
    exclusion_reason: Optional[str] = None

    def __post_init__(self) -> None:
        """Reject case metadata that cannot be reported deterministically."""
        if not self.case_id:
            raise ValueError("case_id must not be empty")
        if self.execution_class not in EXECUTION_CLASSES:
            raise ValueError(
                f"invalid execution class: {self.execution_class}")
        if self.applicability not in APPLICABILITY_VALUES:
            raise ValueError(f"invalid applicability: {self.applicability}")
        if self.applicability == "not-applicable":
            if (not isinstance(self.exclusion_reason, str)
                    or not self.exclusion_reason.strip()):
                raise ValueError(
                    "not-applicable cases require a nonempty exclusion_reason")
        elif self.exclusion_reason is not None:
            raise ValueError(
                "applicable cases cannot define an exclusion_reason")


@dataclass(frozen=True)
class CaseResult:
    """Represent one terminal adapter result without exposing raw commands."""

    status: str
    category: Optional[str] = None
    diagnostic: str = ""
    exit_status: Optional[int] = None
    evidence_paths: tuple[str, ...] = ()
    retry_safe: bool = True
    clean_state: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Require a supported terminal status and failure category."""
        if self.status not in {"passed", "failed", "blocked", "excluded"}:
            raise ValueError(f"invalid case result status: {self.status}")
        if self.status == "failed" and not self.category:
            raise ValueError("failed results require a category")
        category = (self.category.value
                    if isinstance(self.category, FailureCategory)
                    else self.category)
        if category is not None and category not in FAILURE_CATEGORIES:
            raise ValueError(f"invalid failure category: {self.category}")

    @classmethod
    def passed(cls, **kwargs) -> "CaseResult":
        """Create a successful terminal result."""
        return cls(status="passed", **kwargs)

    @classmethod
    def failed(cls, category: str, diagnostic: str, **kwargs) -> "CaseResult":
        """Create a failed terminal result with retry metadata."""
        return cls(
            status="failed", category=category,
            diagnostic=diagnostic, **kwargs)

    @classmethod
    def blocked(cls, diagnostic: str, **kwargs) -> "CaseResult":
        """Create a blocked terminal result."""
        return cls(status="blocked", diagnostic=diagnostic, **kwargs)

    @classmethod
    def excluded(cls, diagnostic: str, **kwargs) -> "CaseResult":
        """Create an excluded result with its reviewed exact reason."""
        return cls(status="excluded", diagnostic=diagnostic, **kwargs)


@dataclass(frozen=True)
class SanitizedProcessResult:
    """Store bounded subprocess output without device or signing identity."""

    exit_status: int
    stdout: str
    stderr: str

    @classmethod
    def create(
            cls, exit_status: int, stdout: str, stderr: str,
            run_id: str) -> "SanitizedProcessResult":
        """Redact and bound captured streams at the process boundary."""
        if isinstance(exit_status, bool) or not isinstance(exit_status, int):
            raise TypeError("exit_status must be an integer")
        return cls(
            exit_status=exit_status,
            stdout=_redact_text(str(stdout), run_id)[
                :MAX_PROCESS_OUTPUT_LENGTH],
            stderr=_redact_text(str(stderr), run_id)[
                :MAX_PROCESS_OUTPUT_LENGTH],
        )


@dataclass(frozen=True)
class PhaseAdapter:
    """Bind one required phase to stable cases and an execution callback."""

    phase_id: str
    cases: tuple[CaseSpec, ...]
    execute: Callable[[CaseSpec], CaseResult]


def _contract_failure(category: str, diagnostic: str) -> CaseResult:
    """Create an unsafe failure for contradictory adapter result metadata."""
    return CaseResult.failed(
        category=category,
        diagnostic=diagnostic,
        retry_safe=False,
        clean_state=False,
    )


def validate_case_result(spec: CaseSpec, result: CaseResult) -> CaseResult:
    """Enforce applicability and success invariants at one runner boundary."""
    if not isinstance(result, CaseResult):
        return _contract_failure(
            "infrastructure", "adapter returned an invalid result")
    if (not isinstance(result.clean_state, bool)
            or not isinstance(result.retry_safe, bool)):
        return _contract_failure(
            "infrastructure", "result safety flags must be booleans")
    if (result.exit_status is not None
            and (isinstance(result.exit_status, bool)
                 or not isinstance(result.exit_status, int))):
        return _contract_failure(
            "infrastructure", "result exit status must be an integer")

    if result.status == "passed":
        if not result.clean_state:
            return _contract_failure(
                "cleanup", "passed result reported an unclean state")
        if result.exit_status not in (None, 0):
            return _contract_failure(
                "infrastructure",
                "passed result reported a nonzero exit status")
        if not result.retry_safe:
            return _contract_failure(
                "infrastructure",
                "passed result contradicted retry safety")
        if result.category is not None:
            return _contract_failure(
                "infrastructure",
                "passed result reported a failure category")
        if spec.applicability != "applicable":
            return _contract_failure(
                "infrastructure",
                "not-applicable case cannot report passed")

    if result.status == "excluded":
        if spec.applicability != "not-applicable":
            return _contract_failure(
                "infrastructure", "applicable case cannot be excluded")
        if result.diagnostic != spec.exclusion_reason:
            return _contract_failure(
                "infrastructure",
                "excluded result did not match its tracked reason")
        if (not result.clean_state or not result.retry_safe
                or result.exit_status not in (None, 0)
                or result.category is not None):
            category = "cleanup" if not result.clean_state else "infrastructure"
            return _contract_failure(
                category, "excluded result contained contradictory metadata")

    return result


def should_stop_after_result(spec: CaseSpec, result: CaseResult) -> bool:
    """Return whether one terminal result makes successor dispatch unsafe."""
    if not result.clean_state:
        return True
    if result.status != "failed":
        return False
    return (
        result.category in UNSAFE_FAILURE_CATEGORIES
        or not spec.isolated
        or not result.retry_safe
    )


def register_runtime_redaction_tokens(
        run_id: str, tokens: Iterable[str]) -> None:
    """Register nonempty process-local values for one active run."""
    normalized = {
        str(token) for token in tokens
        if token is not None and str(token) and str(token) != run_id
    }
    _RUNTIME_REDACTION_TOKENS[run_id] = tuple(
        sorted(normalized, key=lambda value: (-len(value), value)))


def clear_runtime_redaction_tokens(run_id: str) -> None:
    """Forget process-local values when their run releases its lock."""
    _RUNTIME_REDACTION_TOKENS.pop(run_id, None)


def has_runtime_redaction_tokens() -> bool:
    """Return whether any process-local redaction scope remains active."""
    return bool(_RUNTIME_REDACTION_TOKENS)


def _redact_text(value: str, run_id: str) -> str:
    """Redact UUID-like values other than the runner-owned run ID."""
    for token in _RUNTIME_REDACTION_TOKENS.get(run_id, ()):
        value = value.replace(token, REDACTED)
    if (SENSITIVE_LABEL_PATTERN.search(value)
            or APPLE_UDID_PATTERN.search(value)
            or APPLE_CERTIFICATE_IDENTITY_PATTERN.search(value)
            or CANONICAL_SIGNING_IDENTITY_PATTERN.search(value)):
        return REDACTED
    protected = "__RUNNER_OWNED_RUN_ID__"
    value = value.replace(run_id, protected)
    value = UUID_PATTERN.sub(REDACTED, value)
    return value.replace(protected, run_id)


def sanitize(value: Any, run_id: str) -> Any:
    """Recursively redact sensitive keys and non-runner UUID-like values."""
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            safe_key = _redact_text(key_text, run_id)
            normalized_key = re.sub(
                r"[\s-]+", "_", key_text.strip().lower())
            if normalized_key in SENSITIVE_KEYS:
                sanitized[safe_key] = REDACTED
            else:
                sanitized[safe_key] = sanitize(item, run_id)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize(item, run_id) for item in value]
    if isinstance(value, str):
        return _redact_text(value, run_id)
    return value


def _bounded_diagnostic(value: str, run_id: str) -> str:
    """Return a redacted diagnostic no longer than the public size bound."""
    return _redact_text(str(value), run_id)[:MAX_DIAGNOSTIC_LENGTH]


def sanitize_diagnostic(value: str, run_id: str) -> str:
    """Return one bounded diagnostic using the active runtime token scope."""
    return _bounded_diagnostic(value, run_id)


def _slug(value: str) -> str:
    """Create a bounded readable filesystem component from a stable ID."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "case")[:48].rstrip("-") or "case"


def _failure_filename(run_id: str, phase_id: str, case_id: str) -> str:
    """Create a collision-safe stable failure filename for one case ID."""
    digest = hashlib.sha256(
        f"{run_id}\0{case_id}".encode("utf-8")).hexdigest()[:12]
    return f"failure-{_slug(phase_id)}-{_slug(case_id)}-{digest}.json"


def _case_record(spec: CaseSpec) -> Dict[str, Any]:
    """Create one pending checkpoint record from adapter metadata."""
    return {
        "id": spec.case_id,
        "status": "pending",
        "attempt_count": 0,
        "interruption_count": 0,
        "execution_class": spec.execution_class,
        "applicability": spec.applicability,
        "isolated": spec.isolated,
        "exclusion_reason": spec.exclusion_reason,
    }


def _phase_status(cases: Sequence[Mapping[str, Any]]) -> str:
    """Derive a phase state using the checkpoint module's precedence."""
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


def _refresh_status(checkpoint: Dict[str, Any]) -> None:
    """Update all phase and run aggregates after one case transition."""
    phase_statuses = []
    for phase in checkpoint["phases"]:
        phase["status"] = _phase_status(phase["cases"])
        phase_statuses.append(phase["status"])
    if any(status in {"pending", "running"} for status in phase_statuses):
        checkpoint["status"] = "incomplete"
    elif "failed" in phase_statuses:
        checkpoint["status"] = "failed"
    elif "blocked" in phase_statuses:
        checkpoint["status"] = "blocked"
    else:
        checkpoint["status"] = "passed"


def _adapter_map(adapters: Iterable[PhaseAdapter]) -> Dict[str, PhaseAdapter]:
    """Validate adapter identity and return one explicit phase registry."""
    registry = {}
    case_ids = set()
    for adapter in adapters:
        if adapter.phase_id not in PHASE_IDS:
            raise ValueError(f"unknown phase adapter: {adapter.phase_id}")
        if adapter.phase_id in registry:
            raise ValueError(f"duplicate phase adapter: {adapter.phase_id}")
        for case in adapter.cases:
            if case.case_id in case_ids:
                raise ValueError(f"duplicate case id: {case.case_id}")
            case_ids.add(case.case_id)
        registry[adapter.phase_id] = adapter
    return registry


def _expected_phases(
        registry: Mapping[str, PhaseAdapter],
        phase_ids: Sequence[str]) -> list[Dict[str, Any]]:
    """Create selected ordered phases, including explicit missing adapters."""
    phases = []
    for phase_id in phase_ids:
        adapter = registry.get(phase_id)
        if adapter is None or not adapter.cases:
            cases = [_case_record(CaseSpec(
                f"missing-adapter-{phase_id}",
                "host-only",
                isolated=False,
            ))]
        else:
            cases = [_case_record(case) for case in adapter.cases]
        phases.append({"id": phase_id, "status": "pending", "cases": cases})
    return phases


def _validate_or_initialize_layout(
        checkpoint: Dict[str, Any], registry: Mapping[str, PhaseAdapter],
        phase_ids: Sequence[str]) -> None:
    """Initialize a new layout or reject incompatible resumed case metadata."""
    expected = _expected_phases(registry, phase_ids)
    if not checkpoint["phases"]:
        checkpoint["phases"] = expected
        _refresh_status(checkpoint)
        return
    actual_signature = [
        (phase["id"], [
            (case["id"], case.get("execution_class"),
             case.get("applicability"), case.get("isolated"),
             case.get("exclusion_reason"))
            for case in phase["cases"]])
        for phase in checkpoint["phases"]
    ]
    expected_signature = [
        (phase["id"], [
            (case["id"], case["execution_class"],
             case["applicability"], case["isolated"],
             case["exclusion_reason"])
            for case in phase["cases"]])
        for phase in expected
    ]
    if actual_signature != expected_signature:
        raise state.IncompatibleCheckpointError(
            "checkpoint phase or case registry is incompatible")


def _atomic_write(
        output_root: Path, run_directory: Path,
        filename: str, content: str, *, preserve_existing: bool = False) -> None:
    """Durably write one runner artifact through state path protections."""
    state.save_run_artifact(
        output_root, run_directory, filename,
        content.encode("utf-8"), preserve_existing=preserve_existing)


def _write_failure(
        output_root: Path, run_directory: Path,
        checkpoint: Mapping[str, Any], phase_id: str,
        case: Mapping[str, Any], result: CaseResult,
        later_cases_continued: bool) -> str:
    """Persist one sanitized stable failure file without erasing history."""
    filename = _failure_filename(
        checkpoint["run_id"], phase_id, case["id"])
    record = sanitize({
        "schema_version": checkpoint["schema_version"],
        "source_commit": checkpoint["source_commit"],
        "run_id": checkpoint["run_id"],
        "phase": phase_id,
        "case_id": case["id"],
        "attempt_count": case["attempt_count"],
        "started_at": case.get("started_at"),
        "completed_at": case.get("completed_at"),
        "failure_category": result.category,
        "diagnostic": _bounded_diagnostic(
            result.diagnostic, checkpoint["run_id"]),
        "exit_status": result.exit_status,
        "evidence_paths": _allowlisted_evidence_paths(
            result.evidence_paths, checkpoint["run_id"]),
        "retry_safe": result.retry_safe,
        "later_cases_continued": later_cases_continued,
        "details": result.details,
    }, checkpoint["run_id"])
    _atomic_write(
        output_root, run_directory, filename,
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        preserve_existing=False,
    )
    return filename


def _allowlisted_evidence_paths(
        paths: Iterable[str], run_id: str) -> list[str]:
    """Keep only sanitized direct-child evidence artifact references."""
    allowed = []
    for value in paths:
        path = Path(str(value))
        if (path.name != str(value) or not path.name.startswith("evidence-")
                or path.suffix not in {".json", ".jsonl"}):
            continue
        allowed.append(_redact_text(path.name, run_id))
    return allowed


def _has_later_isolated_runnable_case(
        checkpoint: Mapping[str, Any],
        registry: Mapping[str, PhaseAdapter],
        phase_id: str, case_id: str) -> bool:
    """Return whether the next runnable successor is safe to dispatch."""
    found = False
    for phase in checkpoint["phases"]:
        for case in phase["cases"]:
            if found and case["status"] in {"pending", "failed"}:
                adapter = registry.get(phase["id"])
                if adapter is None:
                    return False
                return _find_spec(adapter, case["id"]).isolated
            if phase["id"] == phase_id and case["id"] == case_id:
                found = True
    return False


def _result_counts(checkpoint: Mapping[str, Any]) -> Dict[str, Dict[str, int]]:
    """Count cases by execution class, applicability, and public result."""
    counts = {
        "execution_class": {name: 0 for name in EXECUTION_CLASSES},
        "applicability": {name: 0 for name in APPLICABILITY_VALUES},
        "result": {name: 0 for name in RESULT_VALUES},
    }
    for phase in checkpoint["phases"]:
        for case in phase["cases"]:
            counts["execution_class"][case["execution_class"]] += 1
            counts["applicability"][case["applicability"]] += 1
            result = case["status"]
            if result in {"pending", "running"}:
                result = "incomplete"
            counts["result"][result] += 1
    return counts


def _failure_links(
        run_directory: Path, checkpoint: Mapping[str, Any]) -> list[str]:
    """Return stable relative failure artifact names for failed cases."""
    links = []
    for phase in checkpoint["phases"]:
        for case in phase["cases"]:
            if case["status"] != "failed":
                continue
            filename = case.get("failure_file")
            if filename and (run_directory / filename).is_file():
                links.append(filename)
    return links


def _summary(checkpoint: Mapping[str, Any], run_directory: Path) -> Dict[str, Any]:
    """Build a bounded deterministic machine-readable run summary."""
    phase_records = []
    resume_skips = []
    for phase in checkpoint["phases"]:
        phase_records.append({
            "id": phase["id"],
            "status": phase["status"],
            "counts": {
                result: sum(
                    1 for case in phase["cases"]
                    if ((case["status"] if case["status"] not in
                         {"pending", "running"} else "incomplete") == result))
                for result in RESULT_VALUES
            },
        })
        for case in phase["cases"]:
            if case.get("resume_skip_count", 0):
                resume_skips.append({
                    "phase_id": phase["id"],
                    "case_id": case["id"],
                    "count": case["resume_skip_count"],
                    "last_skipped_at": case["last_resume_skipped_at"],
                })
    return {
        "schema_version": checkpoint["schema_version"],
        "runner_version": checkpoint["runner_version"],
        "run_id": checkpoint["run_id"],
        "source_commit": checkpoint["source_commit"],
        "status": checkpoint["status"],
        "counts": _result_counts(checkpoint),
        "phases": phase_records,
        "resume_skips": resume_skips,
        "failure_files": _failure_links(run_directory, checkpoint),
    }


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    """Render the deterministic summary JSON as bounded human-readable text."""
    lines = [
        "# iPhone Test Summary",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Source commit: `{summary['source_commit']}`",
        f"- Status: **{summary['status']}**",
        "",
        "## Counts",
        "",
    ]
    for group in ("execution_class", "applicability", "result"):
        values = summary["counts"][group]
        lines.append(f"- {group}: " + ", ".join(
            f"{key}={values[key]}" for key in values))
    lines.extend(["", "## Phases", ""])
    lines.extend(
        f"- `{phase['id']}`: {phase['status']}"
        for phase in summary["phases"])
    if summary["failure_files"]:
        lines.extend(["", "## Failures", ""])
        lines.extend(
            f"- [{filename}]({filename})"
            for filename in summary["failure_files"])
    if summary["resume_skips"]:
        lines.extend(["", "## Resume skips", ""])
        lines.extend(
            f"- `{record['case_id']}`: {record['count']}"
            for record in summary["resume_skips"])
    return "\n".join(lines) + "\n"


def write_reports(
        output_root: Path, run_directory: Path,
        checkpoint: Mapping[str, Any]) -> None:
    """Atomically write deterministic sanitized JSON and Markdown summaries."""
    summary = sanitize(
        _summary(checkpoint, run_directory), checkpoint["run_id"])
    _atomic_write(
        output_root, run_directory, "summary.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _atomic_write(
        output_root, run_directory, "summary.md",
        _summary_markdown(summary))


def _execute_case(
        adapter: PhaseAdapter, spec: CaseSpec) -> CaseResult:
    """Execute one adapter case and convert adapter exceptions to failures."""
    try:
        result = adapter.execute(spec)
    except Exception as error:
        return CaseResult.failed(
            category="infrastructure",
            diagnostic=f"adapter raised {type(error).__name__}: {error}",
            retry_safe=False,
            clean_state=False,
        )
    return validate_case_result(spec, result)


def _find_spec(adapter: PhaseAdapter, case_id: str) -> CaseSpec:
    """Return a case spec from a validated adapter registry."""
    return next(case for case in adapter.cases if case.case_id == case_id)


def _persist_missing_adapter(
        output_root: Path, run_directory: Path,
        checkpoint: Dict[str, Any],
        phase: Dict[str, Any],
        now: Callable[[], dt.datetime]) -> None:
    """Persist one missing adapter only after ordered execution reaches it."""
    case = phase["cases"][0]
    started_at = now()
    case["status"] = "running"
    case["attempt_count"] += 1
    case["started_at"] = started_at.isoformat()
    case.pop("completed_at", None)
    _refresh_status(checkpoint)
    state.save_checkpoint(output_root, run_directory, checkpoint)

    completed_at = now()
    result = CaseResult.failed(
        category="infrastructure",
        diagnostic=f"required adapter is missing: {phase['id']}",
        retry_safe=False,
        clean_state=False,
    )
    case["status"] = "failed"
    case["completed_at"] = completed_at.isoformat()
    case["diagnostic"] = _bounded_diagnostic(
        result.diagnostic, checkpoint["run_id"])
    case["failure_category"] = result.category
    case["exit_status"] = result.exit_status
    case["evidence_paths"] = []
    case["retry_safe"] = result.retry_safe
    case["clean_state"] = result.clean_state
    case["failure_file"] = _write_failure(
        output_root, run_directory, checkpoint,
        phase["id"], case, result,
        later_cases_continued=False,
    )
    _refresh_status(checkpoint)
    state.save_checkpoint(output_root, run_directory, checkpoint)


def run_phase_engine(
        output_root: Path, selection: state.RunSelection,
        adapters: Iterable[PhaseAdapter],
        now: Callable[[], dt.datetime],
        redaction_tokens: Iterable[str] = (),
        phase_ids: Sequence[str] = PHASE_IDS) -> int:
    """Run all required phases, persist transitions, report, and release lock."""
    checkpoint = selection.checkpoint
    run_directory = selection.run_directory
    selected = set(phase_ids)
    if not selected or any(phase_id not in PHASE_IDS for phase_id in selected):
        raise ValueError("phase_ids must select known standalone phases")
    ordered_phase_ids = tuple(
        phase_id for phase_id in PHASE_IDS if phase_id in selected)
    register_runtime_redaction_tokens(
        checkpoint["run_id"], redaction_tokens)
    try:
        selection.ensure_locked()
        registry = _adapter_map(adapters)
        _validate_or_initialize_layout(
            checkpoint, registry, ordered_phase_ids)
        state.save_checkpoint(output_root, run_directory, checkpoint)
        stop_all = False
        isolated_failure_seen = False
        for phase in checkpoint["phases"]:
            if stop_all:
                break
            adapter = registry.get(phase["id"])
            if adapter is None or not adapter.cases:
                if not isolated_failure_seen:
                    _persist_missing_adapter(
                        output_root, run_directory, checkpoint, phase, now)
                stop_all = True
                break
            for case in phase["cases"]:
                if selection.resumed and case["status"] == "passed":
                    case["resume_skip_count"] = (
                        case.get("resume_skip_count", 0) + 1)
                    case["last_resume_skipped_at"] = now().isoformat()
                    state.save_checkpoint(
                        output_root, run_directory, checkpoint)
                    continue
                if case["status"] not in {"pending", "failed"}:
                    continue
                spec = _find_spec(adapter, case["id"])
                if isolated_failure_seen and not spec.isolated:
                    stop_all = True
                    break
                current_time = now()
                case["status"] = "running"
                case["attempt_count"] += 1
                case["started_at"] = current_time.isoformat()
                case.pop("completed_at", None)
                _refresh_status(checkpoint)
                state.save_checkpoint(output_root, run_directory, checkpoint)

                result = _execute_case(adapter, spec)

                completed_time = now()
                case["status"] = result.status
                case["completed_at"] = completed_time.isoformat()
                case["diagnostic"] = _bounded_diagnostic(
                    result.diagnostic, checkpoint["run_id"])
                case["failure_category"] = result.category
                case["exit_status"] = result.exit_status
                case["evidence_paths"] = _allowlisted_evidence_paths(
                    result.evidence_paths, checkpoint["run_id"])
                case["retry_safe"] = result.retry_safe
                case["clean_state"] = result.clean_state
                unsafe_result = should_stop_after_result(spec, result)
                if result.status == "failed":
                    case["failure_file"] = _write_failure(
                        output_root, run_directory, checkpoint,
                        phase["id"], case, result,
                        later_cases_continued=(
                            not unsafe_result
                            and _has_later_isolated_runnable_case(
                                checkpoint, registry,
                                phase["id"], case["id"])),
                    )
                if unsafe_result:
                    _refresh_status(checkpoint)
                    stop_all = True
                else:
                    if result.status == "failed":
                        isolated_failure_seen = True
                    _refresh_status(checkpoint)
                state.save_checkpoint(output_root, run_directory, checkpoint)
                if stop_all:
                    break
        _refresh_status(checkpoint)
        state.save_checkpoint(output_root, run_directory, checkpoint)
        write_reports(output_root, run_directory, checkpoint)
        return 0 if checkpoint["status"] == "passed" else 1
    finally:
        try:
            selection.close()
        finally:
            clear_runtime_redaction_tokens(checkpoint["run_id"])
