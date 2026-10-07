#!/usr/bin/env python3
"""Validate two independent dynamic-framework SQL runs and their persistence identity."""
import argparse
import json
from pathlib import Path

REQUIRED_STEPS = {
    "dlopen embedded framework", "production hooks disabled", "reject TCP", "open engine",
    "root Unix socket options", "share existing engine", "close shared handle",
    "connect root empty password", "commit transaction", "rollback transaction",
    "rollback invisible and commit visible", "nullable UTF-8, empty string, metadata and row traversal",
    "unsigned and floating results", "value ownership", "framework allocation",
    "SQL errors retain MySQL diagnostics", "disconnect", "close and join engine",
    "stopped and cleaned up", "one startup per process",
}


def validate_reports(reports, revision):
    """Reject partial, mismatched or crashed runs and require a persisted counter increment."""
    if len(reports) != 2:
        raise ValueError("Exactly two process runs are required")
    for report in reports:
        if any(report.get(key) is not True for key in ("complete", "passed", "worker_exit")):
            raise ValueError("Run must pass, complete and survive loading-thread TLS destruction")
        if report.get("build_id") != revision[:12] or report.get("hook_mode") != "disabled":
            raise ValueError("Run revision or hook state does not match")
        if report.get("test_transport") != "Connector/C over actual Unix socket":
            raise ValueError("Run does not use the external socket client")
        steps = report.get("steps", [])
        if not steps or any(step.get("passed") is not True for step in steps):
            raise ValueError("Missing or failed assertions")
        names = {step["name"] for step in steps}
        if not REQUIRED_STEPS.issubset(names):
            raise ValueError("Missing coverage: " + repr(REQUIRED_STEPS - names))
        if len({name for name in names if name.startswith("dlsym ")}) != 30:
            raise ValueError("All 25 desktop functions and 5 diagnostic getters must load")
    if not reports[0].get("run_id") or reports[0]["run_id"] == reports[1].get("run_id"):
        raise ValueError("Persistence must use distinct process runs")
    previous = reports[0].get("previous_runs")
    if type(previous) is not int or previous < 0 or reports[1].get("previous_runs") != previous + 1:
        raise ValueError("Second process must observe the persisted counter increment")


def main():
    """Read evidence files and report only validation backed by both complete process runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("reports", nargs=2, type=Path)
    args = parser.parse_args()
    reports = [json.loads(path.read_text()) for path in args.reports]
    validate_reports(reports, args.revision)
    print("Verified dynamic loading, socket SQL, clean thread exit and process restart persistence:", args.revision)


if __name__ == "__main__":
    main()
