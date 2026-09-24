#!/usr/bin/env python3
"""Run and validate deterministic iOS startup-failure cleanup on a device."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_CLEANUP = 0x7


def validate_status(status, expected_run_id, expected_build_id):
    """Validate deterministic post-init failure cleanup evidence."""
    if status.get("run_id") != expected_run_id:
        raise ValueError("cleanup evidence belongs to a different launch")
    if status.get("build_id") != expected_build_id:
        raise ValueError("cleanup evidence belongs to a different engine build")
    if status.get("hook_mode") != "enabled":
        raise ValueError("cleanup evidence was not produced by a hook-enabled runtime")
    if status.get("state") != "Failed" or not isinstance(status.get("result"), int):
        raise ValueError("engine did not finish in the expected failed state")
    if status["result"] == 0:
        raise ValueError("failure injection unexpectedly succeeded")
    if status.get("cleanup_error") != 0:
        raise ValueError("server cleanup reported an error")
    if status.get("cleanup_status", 0) & REQUIRED_CLEANUP != REQUIRED_CLEANUP:
        raise ValueError("required cleanup actions did not complete")
    if status.get("working_directory_restored") is not True:
        raise ValueError("working directory was not restored")


def devicectl(arguments):
    """Run devicectl with the repository's selected Xcode environment."""
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    command = ["xcrun", "devicectl", *arguments]
    return subprocess.run(command, check=False, capture_output=True, text=True, env=environment)


def copy_status(device, bundle_id, destination):
    """Copy the current probe status from the application data container."""
    destination.unlink(missing_ok=True)
    result = devicectl([
        "device", "copy", "from", "--device", device,
        "--source", "Documents/probe-status.json",
        "--destination", str(destination),
        "--domain-type", "appDataContainer",
        "--domain-identifier", bundle_id,
        "--timeout", "30",
    ])
    return result.returncode == 0 and destination.is_file()


def wait_for_status(device, bundle_id, destination, timeout_seconds, run_id):
    """Poll until the probe publishes terminal cleanup evidence."""
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        if copy_status(device, bundle_id, destination):
            last_status = json.loads(destination.read_text())
            if (last_status.get("run_id") == run_id and last_status.get("state") == "Failed"
                    and last_status.get("result") is not None):
                return last_status
        time.sleep(2)
    raise TimeoutError(f"cleanup evidence did not become terminal: {last_status!r}")


def main():
    """Launch the injected failure, collect evidence, and enforce cleanup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build_ios_arm64/device-evidence/startup-failure-cleanup")
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("timeout must be positive")
    options.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    build_id = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
    launch = devicectl([
        "device", "process", "launch", "--device", options.device,
        "--terminate-existing", "--environment-variables",
        json.dumps({"SEEKDB_IOS_TEST_FAIL_DURING_INIT": "1",
                    "SEEKDB_IOS_TEST_RUN_ID": run_id}),
        "--timeout", "60", options.bundle_id,
    ])
    if launch.returncode != 0:
        raise SystemExit(launch.stderr or launch.stdout)
    status_path = options.output_dir / "probe-status.json"
    status = wait_for_status(options.device, options.bundle_id, status_path, options.timeout, run_id)
    validate_status(status, run_id, build_id)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
