"""Validate bounded dynamic-framework soak and independent process-restart evidence."""
import math
from validate_framework_probe import validate_reports

GROWTH_LIMIT = 256 * 1024 * 1024


def validate_stability(reports, framework_revision, probe_revision, duration):
    """Reject stale, suspended, leaking or incomplete workloads and require restart persistence."""
    if len(reports) < 2:
        raise ValueError("At least two independent process runs are required")
    if len({report.get("run_id") for report in reports}) != len(reports):
        raise ValueError("Every process must have a distinct run identity")
    for first, second in zip(reports, reports[1:]):
        validate_reports([first, second], framework_revision)
    if any(report.get("probe_source_revision") != probe_revision for report in reports):
        raise ValueError("Probe source revision does not match")
    soak = reports[0].get("stability", {})
    if soak.get("passed") is not True or soak.get("reader_thread_exit") is not True:
        raise ValueError("Soak or concurrent client cleanup failed")
    if soak.get("mode") != "foreground-soak" or soak.get("requested_seconds") != duration:
        raise ValueError("Requested foreground workload was not executed")
    elapsed = soak.get("elapsed_seconds", -1)
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < duration:
        raise ValueError("Soak duration was incomplete")
    for key, minimum in (("iterations", 100), ("reader_queries", 100), ("reader_connections", 2)):
        if type(soak.get(key)) is not int or soak[key] < minimum:
            raise ValueError("Insufficient workload: " + key)
    if soak.get("growth_limit_bytes") != GROWTH_LIMIT:
        raise ValueError("Unexpected memory growth limit")
    baseline = soak.get("warmup_baseline_phys_footprint", 0)
    samples = soak.get("samples", [])
    warmup = min(60, duration // 2)
    if soak.get("warmup_seconds") != warmup or len(samples) < 3 or baseline <= 0:
        raise ValueError("Missing warmup memory evidence")
    previous = 0.0
    for sample in samples:
        time = sample.get("elapsed_seconds", -1)
        footprint = sample.get("phys_footprint", 0)
        if not isinstance(time, (int, float)) or not math.isfinite(time) or time < previous or time - previous > 30:
            raise ValueError("Sampling gap indicates suspension or a stalled workload")
        if type(footprint) is not int or footprint <= 0:
            raise ValueError("Invalid physical footprint sample")
        previous = time
    if samples[-1]["elapsed_seconds"] < duration - 20:
        raise ValueError("Final memory sampling window was incomplete")
    steady = [sample["phys_footprint"] for sample in samples if sample["elapsed_seconds"] >= warmup]
    if not steady or steady[0] != baseline:
        raise ValueError("Warmup baseline does not match samples")
    if max(steady) != soak.get("post_warmup_peak_phys_footprint") or max(steady) > baseline + GROWTH_LIMIT:
        raise ValueError("Excessive physical footprint growth after warmup")
    if max(sample["phys_footprint"] for sample in samples) != soak.get("peak_phys_footprint"):
        raise ValueError("Sampled peak does not match raw evidence")
    first = soak.get("previous_counter", -1)
    final = soak.get("final_counter", -1)
    if type(first) is not int or first < 0 or final - first != (soak["iterations"] + 1) // 2:
        raise ValueError("Commit and rollback visibility do not match the workload")
    for report in reports[1:]:
        check = report.get("stability", {})
        if check.get("mode") != "restart-persistence" or check.get("passed") is not True:
            raise ValueError("Restart did not execute a persistence check")
        if check.get("final_counter") != final:
            raise ValueError("Committed soak state was lost across process restart")
