#!/usr/bin/env python3
"""Run a foreground socket soak and repeated process starts without attaching a debugger."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time

from framework_stability import validate_stability

ROOT = Path(__file__).resolve().parents[2]


def command(arguments, environment, log, *, timeout=60, required=True):
    """Run a bounded Apple CLI operation and preserve errors in the run's local log."""
    result = subprocess.run([str(value) for value in arguments], env=environment, stdout=log,
                            stderr=subprocess.STDOUT, timeout=timeout)
    log.flush()
    if required and result.returncode != 0:
        raise RuntimeError("Apple CLI failed: " + " ".join(str(value) for value in arguments[:5]))
    return result.returncode


def retrieve_report(simulator, identifier, bundle, destination, environment, log):
    """Fetch only the probe's small atomic JSON, preserving partial reports for diagnosis."""
    if simulator:
        container = subprocess.check_output(["xcrun", "simctl", "get_app_container", identifier, bundle, "data"],
                                            env=environment, text=True, timeout=15).strip()
        source = Path(container) / "Documents/framework-probe.json"
        if not source.is_file():
            return None
        data = source.read_bytes()
        destination.write_bytes(data)
    else:
        code = command(["xcrun", "devicectl", "device", "copy", "from", "--device", identifier,
                        "--domain-type", "appDataContainer", "--domain-identifier", bundle,
                        "--source", "Documents/framework-probe.json", "--destination", destination,
                        "--timeout", "15", "--quiet"], environment, log, timeout=25, required=False)
        if code != 0:
            return None
    try:
        return json.loads(destination.read_text())
    except (ValueError, FileNotFoundError):
        return None


def launched_pid(document):
    """Extract one unambiguous process identity from this launch operation only."""
    found = []

    def collect(value):
        """Walk the operation result without treating unrelated device process lists as ownership."""
        if isinstance(value, dict):
            if type(value.get("processIdentifier")) is int:
                found.append(value["processIdentifier"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(document.get("result", {}))
    if len(set(found)) != 1:
        raise ValueError("Launch did not provide an unambiguous owned process identifier")
    return found[0]


def stop_probe(simulator, identifier, bundle, environment, log, output, owned_pid):
    """Terminate only this runner's probe after evidence collection or an explicit timeout."""
    if simulator:
        command(["xcrun", "simctl", "terminate", identifier, bundle], environment, log, required=False)
        return
    if owned_pid is None:
        return
    snapshot = output / "processes.json"
    code = command(["xcrun", "devicectl", "device", "info", "processes", "--device", identifier,
                    "--json-output", snapshot, "--quiet"], environment, log, required=False)
    if code == 0:
        processes = json.loads(snapshot.read_text()).get("result", {}).get("runningProcesses", [])
        for process in processes:
            if process.get("processIdentifier") == owned_pid and process.get("executable", "").endswith(
                    "/SeekDBFrameworkProbe.app/SeekDBFrameworkProbe"):
                command(["xcrun", "devicectl", "device", "process", "terminate", "--device", identifier,
                         "--pid", process["processIdentifier"]], environment, log, required=False)


def main():
    """Install a signed probe, collect fresh run identities, and validate bounded stability."""
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--simulator", metavar="UDID")
    target.add_argument("--device", metavar="UDID")
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--framework", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if not 60 <= args.duration <= 1800 or not 2 <= args.runs <= 10:
        parser.error("Duration must be 60..1800 seconds and runs must be 2..10")
    app, framework, output = args.app.resolve(strict=True), args.framework.resolve(strict=True), args.output.resolve()
    if any(not path.is_relative_to(ROOT) for path in (app, framework, output)):
        parser.error("All inputs and outputs must remain inside the seekdb repository")
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new output directory to preserve prior evidence")
    output.mkdir(parents=True, exist_ok=True)
    info = plistlib.loads((app / "Info.plist").read_bytes())
    bundle = info["CFBundleIdentifier"]
    probe_revision = info["SeekDBProbeSourceRevision"]
    manifest = json.loads((framework / "build-manifest.json").read_text())
    simulator = args.simulator is not None
    expected_platform = "IOSSIMULATOR" if simulator else "IOS"
    if manifest["platform"] != expected_platform or manifest["source_dirty"] or manifest["test_hooks"] != "disabled":
        parser.error("Require a clean, hooks-disabled matching framework")
    identifier = args.simulator or args.device
    environment = dict(os.environ)
    environment.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    summary = {"framework_revision": manifest["source_revision"], "probe_revision": probe_revision,
               "platform": expected_platform, "requested_duration": args.duration, "requested_runs": args.runs,
               "debugger_attached": False, "passed": False}
    reports = []
    owned_pid = None
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    with (output / "host.log").open("w") as log:
        if simulator:
            command(["xcrun", "simctl", "install", identifier, app], environment, log)
        else:
            command(["xcrun", "devicectl", "device", "install", "app", "--device", identifier, app,
                     "--timeout", "120"], environment, log, timeout=135)
        initial = retrieve_report(simulator, identifier, bundle, output / "initial-report.json", environment, log)
        initial_id = initial.get("run_id") if initial else None
        try:
            for index in range(args.runs):
                seconds = args.duration if index == 0 else 0
                launch_environment = dict(environment)
                if simulator:
                    launch_environment["SIMCTL_CHILD_SEEKDB_FRAMEWORK_STABILITY_SECONDS"] = str(seconds)
                    launch = ["xcrun", "simctl", "launch", "--terminate-running-process", identifier, bundle]
                else:
                    launch = ["xcrun", "devicectl", "device", "process", "launch", "--device", identifier,
                              "--terminate-existing", "--environment-variables",
                              json.dumps({"SEEKDB_FRAMEWORK_STABILITY_SECONDS": str(seconds)}),
                              "--json-output", output / f"launch-{index + 1}.json", "--quiet", bundle]
                command(launch, launch_environment, log)
                if not simulator:
                    owned_pid = launched_pid(json.loads((output / f"launch-{index + 1}.json").read_text()))
                deadline = time.monotonic() + seconds + 240
                path = output / f"run-{index + 1}.json"
                prior_ids = {report["run_id"] for report in reports} | {initial_id}
                while time.monotonic() < deadline:
                    report = retrieve_report(simulator, identifier, bundle, path, environment, log)
                    if report and report.get("run_id") not in prior_ids:
                        state = report.get("stability", {})
                        print(json.dumps({"run": index + 1, "complete": report.get("complete"),
                                          "elapsed": state.get("elapsed_seconds"), "iterations": state.get("iterations"),
                                          "reader_queries": state.get("reader_queries"), "failure": state.get("failure")}), flush=True)
                        if report.get("worker_exit"):
                            if report.get("passed") is not True or state.get("passed") is not True:
                                raise ValueError("Probe failed; inspect " + str(path))
                            if report.get("build_id") != manifest["source_revision"][:12]:
                                raise ValueError("Live framework identity does not match")
                            reports.append(report)
                            break
                    time.sleep(5)
                else:
                    raise TimeoutError("Probe did not finish with clean loading-thread exit")
            validate_stability(reports, manifest["source_revision"], probe_revision, args.duration)
            summary["passed"] = True
            summary["completed_runs"] = len(reports)
        except Exception as error:
            summary["error"] = str(error)
            raise
        finally:
            summary_path.write_text(json.dumps(summary, indent=2) + "\n")
            stop_probe(simulator, identifier, bundle, environment, log, output, owned_pid)
    print("Stability passed:", summary_path, flush=True)


if __name__ == "__main__":
    main()
