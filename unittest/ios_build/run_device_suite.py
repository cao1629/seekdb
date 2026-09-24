#!/usr/bin/env python3
"""Launch an installed iOS test suite and validate device-produced JSONL evidence."""
import argparse
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
                      run_id, build_id, expected_case_ids, suite, case_filter):
    """Poll until complete current-run evidence validates or the deadline expires."""
    deadline = time.monotonic() + timeout_seconds
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


def wait_for_terminal_status(device, bundle_id, destination, timeout_seconds, run_id, build_id):
    """Poll the device lifecycle file until the current suite stops and validates."""
    deadline = time.monotonic() + timeout_seconds
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
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build_ios_arm64/device-evidence/device-suite")
    parser.add_argument("--crash-report-dir", type=Path,
                        default=Path.home() / "Library/Logs/CrashReporter/MobileDevice")
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        data_name = validate_data_name(options.data_name)
    except ValueError as error:
        parser.error(str(error))
    options.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    build_id = source_build_id()
    expected_cases = options.expected_case or ["ios.registry.smoke"]
    source_name = f"device-test-{run_id}.jsonl"
    destination = options.output_dir / source_name
    before_crashes = crash_snapshot(options.crash_report_dir)
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
        run_id, build_id, expected_cases, options.suite, options.filter)
    with tempfile.TemporaryDirectory() as temporary_directory:
        wait_for_terminal_status(
            options.device, options.bundle_id, Path(temporary_directory) / "probe-status.json",
            options.timeout, run_id, build_id)
    reject_new_crash_reports(options.crash_report_dir, before_crashes, options.bundle_id)
    print(json.dumps({"run_id": run_id, "build_id": build_id, **summary}, sort_keys=True))


if __name__ == "__main__":
    main()
