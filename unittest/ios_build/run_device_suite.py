#!/usr/bin/env python3
"""Launch an installed iOS test suite and validate device-produced JSONL evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_EVIDENCE_NAME = re.compile(r"^device-test-[0-9a-f-]{36}\.jsonl$")


class IncompleteEvidenceError(ValueError):
    """Report a structurally valid JSONL prefix that may still become complete."""


def validate_records(records, expected_run_id, expected_build_id, expected_case_ids,
                     expected_suite, expected_filter):
    """Validate identity, event order, complete coverage, and passing device results."""
    if not records:
        raise IncompleteEvidenceError("device evidence is empty")
    expected_ids = list(expected_case_ids)
    if len(expected_ids) != len(set(expected_ids)) or not expected_ids:
        raise ValueError("expected case IDs must be unique and nonempty")
    for record in records:
        if record.get("run_id") != expected_run_id:
            raise ValueError("device evidence belongs to a different run")
        if record.get("build_id") != expected_build_id:
            raise ValueError("device evidence belongs to a different engine build")
        if record.get("origin") != "device":
            raise ValueError("host-generated records cannot satisfy device assertions")

    start = records[0]
    if start.get("event") != "run_start":
        raise ValueError("device evidence does not begin with run_start")
    if start.get("suite") != expected_suite or start.get("filter") != expected_filter:
        raise ValueError("device evidence suite selection does not match the request")
    if start.get("selected_case_ids") != expected_ids:
        raise ValueError("selected device registry coverage is incomplete or reordered")
    registry_ids = start.get("registry_case_ids")
    if not isinstance(registry_ids, list) or len(registry_ids) != len(set(registry_ids)):
        raise ValueError("device registry IDs are missing or duplicated")
    if not set(expected_ids).issubset(registry_ids):
        raise ValueError("expected cases are absent from the device registry")

    active_case = None
    assertion_count = 0
    case_results = {}
    complete = None
    for record in records[1:]:
        event = record.get("event")
        if event == "case_start":
            case_id = record.get("case_id")
            if active_case is not None or case_id in case_results or case_id not in expected_ids:
                raise ValueError("device case start is duplicate, nested, or unexpected")
            if not isinstance(record.get("timeout_seconds"), int) or record["timeout_seconds"] <= 0:
                raise ValueError("device case timeout metadata is invalid")
            active_case = case_id
            assertion_count = 0
        elif event == "assertion":
            if active_case is None or record.get("case_id") != active_case:
                raise ValueError("device assertion is outside its active case")
            if not isinstance(record.get("passed"), bool) or not record["passed"]:
                raise ValueError("device assertion failed")
            assertion_count += 1
        elif event == "case_end":
            case_id = record.get("case_id")
            if active_case is None or case_id != active_case or case_id in case_results:
                raise ValueError("device case completion is missing or duplicated")
            if assertion_count == 0:
                raise ValueError("device case emitted no assertions")
            result = record.get("result")
            if not isinstance(result, int) or result != 0:
                raise ValueError("device case returned a nonzero result")
            case_results[case_id] = result
            active_case = None
        elif event == "run_complete":
            if complete is not None or active_case is not None or record is not records[-1]:
                raise ValueError("run_complete is duplicate, premature, or nonterminal")
            complete = record
        else:
            raise ValueError("device evidence contains an unknown event")

    if active_case is not None or complete is None:
        raise IncompleteEvidenceError("device evidence is missing terminal completion")
    if list(case_results) != expected_ids:
        raise ValueError("device evidence does not cover every expected case exactly once")
    if (complete.get("result") != 0 or complete.get("selected_count") != len(expected_ids)
            or complete.get("completed_count") != len(expected_ids)):
        raise ValueError("device run completion reports failure or incomplete coverage")
    return {"case_results": case_results, "run_result": complete["result"]}


def read_jsonl(path):
    """Read a complete JSON object from every nonempty evidence line."""
    records = []
    with path.open(encoding="utf-8") as stream:
        lines = stream.readlines()
        last_nonempty = max((index for index, line in enumerate(lines, 1) if line.strip()), default=0)
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                if line_number == last_nonempty and not line.endswith("\n"):
                    raise IncompleteEvidenceError(
                        f"incomplete JSONL record at line {line_number}") from error
                raise ValueError(f"invalid JSONL record at line {line_number}") from error
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record at line {line_number} is not an object")
            records.append(record)
    return records


def devicectl(arguments):
    """Run devicectl while retaining raw device metadata only in process memory."""
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    return subprocess.run(["xcrun", "devicectl", *arguments], check=False, capture_output=True,
                          text=True, env=environment)


def copy_evidence(device, bundle_id, source_name, destination):
    """Copy only the allowlisted run-scoped JSONL file from the App container."""
    if ALLOWED_EVIDENCE_NAME.fullmatch(source_name) is None:
        raise ValueError("evidence source name is not allowlisted")
    destination.unlink(missing_ok=True)
    result = devicectl([
        "device", "copy", "from", "--device", device,
        "--source", f"Documents/{source_name}", "--destination", str(destination),
        "--domain-type", "appDataContainer", "--domain-identifier", bundle_id, "--timeout", "30",
    ])
    return result.returncode == 0 and destination.is_file()


def wait_for_evidence(device, bundle_id, source_name, destination, timeout_seconds,
                      run_id, build_id, expected_case_ids, suite, case_filter,
                      deadline=None):
    """Poll until complete current-run evidence validates or the deadline expires."""
    deadline = deadline or time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        if copy_evidence(device, bundle_id, source_name, destination):
            try:
                records = read_jsonl(destination)
                if not any(record.get("event") == "run_complete" for record in records):
                    raise IncompleteEvidenceError("device evidence has not reached run_complete")
                return validate_records(records, run_id, build_id, expected_case_ids, suite, case_filter)
            except IncompleteEvidenceError as error:
                last_error = error
        time.sleep(2)
    raise TimeoutError(f"device suite did not produce complete evidence: {last_error}")


def validate_terminal_status(status, expected_run_id, expected_build_id):
    """Require the current suite process to finish with complete successful cleanup."""
    if status.get("run_id") != expected_run_id or status.get("build_id") != expected_build_id:
        raise ValueError("terminal status belongs to a stale run or engine build")
    if status.get("state") != "Stopped" or status.get("result") != 0 or status.get("suite_result") != 0:
        raise ValueError("device suite engine did not stop successfully")
    if status.get("cleanup_status", 0) & 0x7 != 0x7 or status.get("cleanup_error") != 0:
        raise ValueError("device suite cleanup was incomplete")
    if status.get("working_directory_restored") is not True:
        raise ValueError("device suite did not restore the process working directory")


def copy_probe_status(device, bundle_id, destination):
    """Copy the fixed allowlisted lifecycle status file to a temporary host path."""
    destination.unlink(missing_ok=True)
    result = devicectl([
        "device", "copy", "from", "--device", device,
        "--source", "Documents/probe-status.json", "--destination", str(destination),
        "--domain-type", "appDataContainer", "--domain-identifier", bundle_id, "--timeout", "30",
    ])
    return result.returncode == 0 and destination.is_file()


def wait_for_terminal_status(device, bundle_id, destination, timeout_seconds, run_id, build_id,
                             deadline=None):
    """Poll the device lifecycle file until the current suite stops and validates."""
    deadline = deadline or time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        if copy_probe_status(device, bundle_id, destination):
            status = json.loads(destination.read_text())
            try:
                validate_terminal_status(status, run_id, build_id)
                return
            except ValueError as error:
                last_error = error
        time.sleep(2)
    raise TimeoutError(f"device suite did not reach a clean terminal state: {last_error}")


def validate_sql_records(records):
    """Require exactly 36 ordered passing SQL steps and one success terminator."""
    if len(records) != 37:
        raise ValueError("ordinary SQL evidence must contain 36 steps and completion")
    steps = records[:-1]
    if ([record.get("step") for record in steps] != list(range(1, 37))
            or any(not isinstance(record.get("case"), str)
                   or not record["case"] or record.get("result") != 0
                   for record in steps)
            or len({record["case"] for record in steps}) != 36
            or records[-1] != {"complete": True, "result": 0}):
        raise ValueError("ordinary SQL evidence is incomplete or failed")


def _file_sha256(path):
    """Return the SHA-256 digest of one bounded host evidence file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_evidence_metadata(evidence, options, build_id, previous_runs):
    """Build privacy-preserving identity metadata for one SQL round."""
    return {
        "schema_version": 1,
        "runner_run_id": options.runner_run_id,
        "build_id": build_id,
        "device_hash": hashlib.sha256(
            options.device.encode("utf-8")).hexdigest(),
        "data_name": options.data_name,
        "hook_mode": options.expected_hook_mode,
        "previous_runs": previous_runs,
        "evidence_sha256": _file_sha256(evidence),
    }


def sql_evidence_metadata_path(evidence):
    """Return the run-scoped metadata path adjacent to SQL JSONL evidence."""
    return evidence.with_name(evidence.name + ".meta.json")


def write_sql_evidence_metadata(evidence, options, build_id, previous_runs):
    """Persist validated SQL provenance without retaining raw device identity."""
    metadata = sql_evidence_metadata(
        evidence, options, build_id, previous_runs)
    destination = sql_evidence_metadata_path(evidence)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def validate_sql_evidence_metadata(evidence, options, build_id, previous_runs):
    """Require SQL evidence to match this runner, device, build, and data scope."""
    metadata_path = sql_evidence_metadata_path(evidence)
    if not evidence.is_file() or not metadata_path.is_file():
        return False
    try:
        actual = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return actual == sql_evidence_metadata(
        evidence, options, build_id, previous_runs)


def validate_sql_terminal_status(status, run_id, build_id, data_name, previous_runs,
                                 expected_hook_mode="disabled"):
    """Require one current ordinary SQL run with the expected persistence count."""
    validate_terminal_status(
        {**status, "suite_result": 0}, run_id, build_id)
    if (status.get("data_name") != data_name
            or status.get("sql_verified") is not True
            or status.get("sql_result") != 0
            or status.get("suite_result") is not None
            or status.get("previous_runs") != previous_runs
            or status.get("hook_mode") != expected_hook_mode):
        raise ValueError("ordinary SQL terminal status did not pass restart gate")


def copy_sql_evidence(device, bundle_id, destination):
    """Copy the fixed ordinary SQL evidence file without raw device metadata."""
    destination.unlink(missing_ok=True)
    result = devicectl([
        "device", "copy", "from", "--device", device,
        "--source", "Documents/sql-probe-results.jsonl",
        "--destination", str(destination),
        "--domain-type", "appDataContainer", "--domain-identifier", bundle_id,
        "--timeout", "30",
    ])
    return result.returncode == 0 and destination.is_file()


def wait_for_sql_round(device, bundle_id, output, deadline, run_id, build_id,
                       data_name, previous_runs, expected_hook_mode,
                       options=None):
    """Wait for one current ordinary SQL terminal status and copy its evidence."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        status_path = Path(temporary_directory) / "probe-status.json"
        last_error = None
        while time.monotonic() < deadline:
            if copy_probe_status(device, bundle_id, status_path):
                try:
                    status = json.loads(status_path.read_text())
                    validate_sql_terminal_status(
                        status, run_id, build_id, data_name, previous_runs,
                        expected_hook_mode)
                    if not copy_sql_evidence(device, bundle_id, output):
                        raise IncompleteEvidenceError(
                            "ordinary SQL evidence is not available")
                    validate_sql_records(read_jsonl(output))
                    if options is not None:
                        write_sql_evidence_metadata(
                            output, options, build_id, previous_runs)
                    return
                except (IncompleteEvidenceError, json.JSONDecodeError, ValueError) as error:
                    last_error = error
            time.sleep(2)
    raise TimeoutError(f"ordinary SQL restart gate timed out: {last_error}")


def run_sql_restart(options, build_id, deadline):
    """Run two ordinary SQL probes in one data directory under one deadline."""
    outputs = (
        options.output_dir / f"evidence-{options.evidence_prefix}-first.jsonl",
        options.output_dir / f"evidence-{options.evidence_prefix}-restart.jsonl",
    )
    for previous_runs, output in enumerate(outputs):
        if validate_sql_evidence_metadata(
                output, options, build_id, previous_runs):
            validate_sql_records(read_jsonl(output))
            continue
        run_id = str(uuid.uuid4())
        launch = devicectl([
            "device", "process", "launch", "--device", options.device,
            "--terminate-existing", "--environment-variables", json.dumps({
                "SEEKDB_IOS_TEST_RUN_ID": run_id,
                "SEEKDB_PROBE_DATA_NAME": options.data_name,
                "SEEKDB_PROBE_AUTO_STOP": "1",
            }), "--timeout", "60", options.bundle_id,
        ])
        if launch.returncode != 0:
            raise SystemExit(
                "ordinary SQL launch failed; raw device metadata was not persisted")
        wait_for_sql_round(
            options.device, options.bundle_id, output, deadline, run_id,
            build_id, options.data_name, previous_runs,
            options.expected_hook_mode, options=options)
    return {
        "run_result": 0,
        "first_previous_runs": 0,
        "second_previous_runs": 1,
    }


def crash_snapshot(report_root):
    """Return identities for locally synchronized iOS crash and Jetsam reports."""
    if not report_root.is_dir():
        return set()
    return {(path.resolve(), path.stat().st_mtime_ns, path.stat().st_size)
            for path in report_root.rglob("*.ips") if path.is_file()}


def reject_new_crash_reports(report_root, before, bundle_id):
    """Reject new synchronized crash or Jetsam reports that name the probe App."""
    new_reports = crash_snapshot(report_root) - before
    for path, _, _ in new_reports:
        content = path.read_text(encoding="utf-8", errors="replace")
        if bundle_id in content or "SeekDBProbe" in content:
            raise RuntimeError("the device suite produced a new crash or Jetsam report")


def source_build_id():
    """Return the source identity embedded by the current iOS engine build."""
    result = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True)
    build_id = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{12}", build_id) is None:
        raise ValueError("git did not return a valid source build identity")
    return build_id


def validate_data_name(name):
    """Return a bounded sandbox directory name or reject unsafe path syntax."""
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
        raise ValueError("device data name must contain only letters, digits, underscores, or hyphens")
    return name


def main():
    """Launch one installed suite, validate its allowlisted evidence, and print a safe summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--suite", default="smoke")
    parser.add_argument("--filter", default="ios.registry.*")
    parser.add_argument("--expected-case", action="append")
    parser.add_argument("--data-name", default="ios-device-tests")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--sql-restart", action="store_true",
        help="run two ordinary 36-step SQL rounds in one data directory")
    parser.add_argument(
        "--evidence-prefix", default="sql",
        help="safe filename prefix for ordinary SQL evidence")
    parser.add_argument(
        "--expected-hook-mode", choices=("enabled", "disabled"),
        default="disabled")
    parser.add_argument(
        "--runner-run-id",
        help="safe standalone runner identity for SQL evidence binding")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build_ios_arm64/device-evidence/device-suite")
    parser.add_argument("--crash-report-dir", type=Path,
                        default=Path.home() / "Library/Logs/CrashReporter/MobileDevice")
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        data_name = validate_data_name(options.data_name)
        evidence_prefix = validate_data_name(options.evidence_prefix)
    except ValueError as error:
        parser.error(str(error))
    options.evidence_prefix = evidence_prefix
    if options.sql_restart:
        try:
            options.runner_run_id = validate_data_name(options.runner_run_id)
        except ValueError as error:
            parser.error(str(error))
    options.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    build_id = source_build_id()
    expected_cases = options.expected_case or ["ios.registry.smoke"]
    source_name = f"device-test-{run_id}.jsonl"
    destination = options.output_dir / source_name
    before_crashes = crash_snapshot(options.crash_report_dir)
    deadline = time.monotonic() + options.timeout
    if options.sql_restart:
        summary = run_sql_restart(options, build_id, deadline)
        reject_new_crash_reports(
            options.crash_report_dir, before_crashes, options.bundle_id)
        print(json.dumps(summary, sort_keys=True))
        return
    launch = devicectl([
        "device", "process", "launch", "--device", options.device, "--terminate-existing",
        "--environment-variables", json.dumps({
            "SEEKDB_IOS_TEST_SUITE": options.suite,
            "SEEKDB_IOS_TEST_FILTER": options.filter,
            "SEEKDB_IOS_TEST_RUN_ID": run_id,
            "SEEKDB_PROBE_DATA_NAME": data_name,
            "SEEKDB_PROBE_AUTO_STOP": "1",
        }),
        "--timeout", "60", options.bundle_id,
    ])
    if launch.returncode != 0:
        raise SystemExit("device suite launch failed; raw device metadata was not persisted")
    summary = wait_for_evidence(
        options.device, options.bundle_id, source_name, destination, options.timeout,
        run_id, build_id, expected_cases, options.suite, options.filter,
        deadline=deadline)
    with tempfile.TemporaryDirectory() as temporary_directory:
        wait_for_terminal_status(
            options.device, options.bundle_id, Path(temporary_directory) / "probe-status.json",
            options.timeout, run_id, build_id, deadline=deadline)
    reject_new_crash_reports(options.crash_report_dir, before_crashes, options.bundle_id)
    print(json.dumps({"run_id": run_id, "build_id": build_id, **summary}, sort_keys=True))


if __name__ == "__main__":
    main()
