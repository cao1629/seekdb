#!/usr/bin/env python3
"""Generate the deterministic layered iOS test inventory for this checkout."""

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


EXECUTION_CLASSES = {"device-native", "host-driven-device", "host-only"}
APPLICABILITY_CLASSES = {"required", "excluded", "blocked"}
MEMORY_CLASSES = {"small", "medium", "large", "unbounded"}
RUST_TEST_PATTERN = re.compile(
    r"(?m)^\s*#\[test\]\s*(?:\n\s*#\[[^\n]+\]\s*)*\n\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)
GTEST_PATTERN = re.compile(
    r"\bTEST(_F)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


class InventoryError(RuntimeError):
    """Report an incomplete or invalid inventory without silently omitting tests."""


def git_tracked_files(repo_root):
    """Return sorted repository-relative paths reported by ``git ls-files -z``."""
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=repo_root
    ).decode("utf-8")
    return sorted(path for path in output.split("\0") if path)


def source_commit(repo_root):
    """Return the current source revision recorded in generated inventory rows."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def _load_mysqltest_discovery(repo_root):
    """Load the existing seekdb mysqltest runner to reuse its selection semantics."""
    script = repo_root / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
    spec = importlib.util.spec_from_file_location("seekdb_mysqltest_inventory", script)
    if spec is None or spec.loader is None:
        raise InventoryError("cannot load mysqltest discovery from {}".format(script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mysqltest_name(path):
    """Convert a tracked active mysqltest path to the runner's stable case name."""
    parts = Path(path).parts
    if parts[:4] != ("tools", "deploy", "mysql_test", "test_suite"):
        return Path(path).stem
    return "{}.{}".format(parts[4], Path(path).stem)


def _base_discoveries(repo_root, tracked_files):
    """Discover all supported tracked test corpora before classification."""
    discoveries = []
    active_prefix = "tools/deploy/mysql_test/"
    active_paths = []
    for path in tracked_files:
        if not path.startswith(active_prefix) or not path.endswith(".test"):
            continue
        parts = Path(path).parts
        if len(parts) == 5 and parts[3] == "t":
            active_paths.append(path)
        elif len(parts) == 7 and parts[3] == "test_suite" and parts[5] == "t":
            active_paths.append(path)

    mysqltest_module = _load_mysqltest_discovery(repo_root)
    selected = {case.name for case in mysqltest_module.discover_cases(repo_root)}
    available_names = {_mysqltest_name(path) for path in active_paths}
    missing_selected = sorted(selected - available_names)
    if missing_selected:
        raise InventoryError(
            "CI-selected mysqltest cases are absent from the active corpus: {}".format(
                ", ".join(missing_selected)
            )
        )
    for path in active_paths:
        name = _mysqltest_name(path)
        discoveries.append(
            {
                "id": "mysqltest.active.{}".format(name),
                "source_path": path,
                "corpus": "mysqltest-active",
                "case_name": name,
                "ci_selected": name in selected,
            }
        )

    for path in tracked_files:
        if path.startswith("tools/obtest/t/") and path.endswith(".test"):
            name = path[len("tools/obtest/t/") : -len(".test")].replace("/", ".")
            discoveries.append(
                {
                    "id": "obtest.legacy.{}".format(name),
                    "source_path": path,
                    "corpus": "obtest-legacy",
                    "case_name": name,
                    "ci_selected": False,
                }
            )

    for path in tracked_files:
        if not path.startswith("rust/") or not path.endswith(".rs"):
            continue
        content = (repo_root / path).read_text(encoding="utf-8")
        for function_name in RUST_TEST_PATTERN.findall(content):
            module_name = path[:-3].replace("/", ".")
            discoveries.append(
                {
                    "id": "rust.{}.{}".format(module_name, function_name),
                    "source_path": path,
                    "corpus": "rust-test",
                    "case_name": function_name,
                    "ci_selected": False,
                }
            )

    cpp_suffixes = (".cc", ".cpp", ".cxx", ".h", ".hpp")
    for path in tracked_files:
        if not path.endswith(cpp_suffixes):
            continue
        content = (repo_root / path).read_text(encoding="utf-8", errors="replace")
        for _, suite_name, case_name in GTEST_PATTERN.findall(content):
            discoveries.append(
                {
                    "id": "cpp.gtest.{}.{}".format(suite_name, case_name),
                    "source_path": path,
                    "corpus": "gtest-orphan",
                    "case_name": "{}.{}".format(suite_name, case_name),
                    "ci_selected": False,
                }
            )

    probe_pattern = re.compile(r"^unittest/ios_build/(?:.*_probe\.(?:c|cpp)|sql_probe\.cpp)$")
    for path in tracked_files:
        if probe_pattern.match(path):
            name = Path(path).stem
            discoveries.append(
                {
                    "id": "ios.probe.{}".format(name),
                    "source_path": path,
                    "corpus": "ios-probe",
                    "case_name": name,
                    "ci_selected": False,
                }
            )

    for path in tracked_files:
        if re.match(r"^unittest/ios_build/test_.*\.py$", path):
            name = Path(path).stem
            discoveries.append(
                {
                    "id": "host.python.{}".format(name),
                    "source_path": path,
                    "corpus": "host-python",
                    "case_name": name,
                    "ci_selected": False,
                }
            )
    return discoveries


def _load_manifest(manifest_path):
    """Load the tracked classification manifest and validate its top-level shape."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InventoryError("cannot load classification manifest: {}".format(exc))
    if manifest.get("schema_version") != 1:
        raise InventoryError("classification manifest schema_version must be 1")
    if not isinstance(manifest.get("classifications"), dict):
        raise InventoryError("classification manifest must define classifications")
    if not isinstance(manifest.get("overrides", {}), dict):
        raise InventoryError("classification manifest overrides must be an object")
    return manifest


def _validate_classification(row):
    """Validate one joined inventory row and its exclusion or blocked reason."""
    if row.get("execution_class") not in EXECUTION_CLASSES:
        raise InventoryError("{} has invalid execution_class".format(row["id"]))
    if row.get("applicability") not in APPLICABILITY_CLASSES:
        raise InventoryError("{} has invalid applicability".format(row["id"]))
    if row.get("memory_class") not in MEMORY_CLASSES:
        raise InventoryError("{} has invalid memory_class".format(row["id"]))
    if not isinstance(row.get("requirements"), list):
        raise InventoryError("{} requirements must be a list".format(row["id"]))
    if not isinstance(row.get("timeout_seconds"), int) or row["timeout_seconds"] <= 0:
        raise InventoryError("{} timeout_seconds must be positive".format(row["id"]))
    if row["applicability"] == "excluded" and not row.get("exclusion_reason"):
        raise InventoryError("{} exclusion requires an exact reason".format(row["id"]))
    if row["applicability"] == "blocked" and not row.get("blocked_reason"):
        raise InventoryError("{} blocked classification requires a reason".format(row["id"]))


def validate_device_equivalents(rows):
    """Reject missing or cyclic device-equivalent references."""
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        duplicates = sorted(
            test_id for test_id, count in Counter(row["id"] for row in rows).items()
            if count > 1
        )
        raise InventoryError("duplicate inventory IDs: {}".format(", ".join(duplicates)))
    for row in rows:
        seen = {row["id"]}
        equivalent = row.get("device_equivalent")
        while equivalent is not None:
            if equivalent not in by_id:
                raise InventoryError(
                    "{} references missing device equivalent {}".format(row["id"], equivalent)
                )
            if equivalent in seen:
                raise InventoryError("device-equivalent cycle includes {}".format(equivalent))
            seen.add(equivalent)
            equivalent = by_id[equivalent].get("device_equivalent")


def build_inventory(repo_root, manifest_path):
    """Discover, classify, validate, and return sorted inventory rows."""
    repo_root = Path(repo_root).resolve()
    tracked_files = git_tracked_files(repo_root)
    manifest = _load_manifest(Path(manifest_path))
    classifications = manifest["classifications"]
    overrides = manifest.get("overrides", {})
    discoveries = _base_discoveries(repo_root, tracked_files)
    discovered_ids = {discovery["id"] for discovery in discoveries}
    unknown_overrides = sorted(set(overrides) - discovered_ids)
    if unknown_overrides:
        raise InventoryError(
            "classification overrides reference undiscovered IDs: {}".format(
                ", ".join(unknown_overrides)
            )
        )
    rows = []
    for discovery in discoveries:
        corpus = discovery["corpus"]
        if corpus not in classifications:
            raise InventoryError("unclassified corpus: {}".format(corpus))
        classification = dict(classifications[corpus])
        classification_source = "classification:{}".format(corpus)
        if discovery["id"] in overrides:
            classification.update(overrides[discovery["id"]])
            classification_source = "override:{}".format(discovery["id"])
        row = dict(discovery)
        row.update(classification)
        row["classification_source"] = classification_source
        row.setdefault("device_equivalent", None)
        row.setdefault("exclusion_reason", None)
        row.setdefault("blocked_reason", None)
        row.setdefault("latest_result", "not-run")
        row.setdefault("evidence_path", None)
        _validate_classification(row)
        rows.append(row)

    rows.sort(key=lambda row: row["id"])
    validate_device_equivalents(rows)
    canonical = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    commit = source_commit(repo_root)
    for row in rows:
        row["source_commit"] = commit
        row["corpus_digest"] = digest
    return rows


def count_by_corpus(rows):
    """Return inventory row counts grouped by corpus name."""
    return Counter(row["corpus"] for row in rows)


def write_inventory(rows, output_path):
    """Write sorted canonical JSON Lines, creating the output directory as needed."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in sorted(rows, key=lambda row: row["id"])
    )
    output_path.write_text(content, encoding="utf-8")


def _parse_args():
    """Parse command-line paths for inventory generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--classification",
        type=Path,
        default=Path(__file__).resolve().with_name("ios-test-classification.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "build_ios_arm64"
        / "generated"
        / "ios-test-inventory.jsonl",
    )
    return parser.parse_args()


def main():
    """Generate the inventory and print its path and corpus counts."""
    args = _parse_args()
    rows = build_inventory(args.repo_root, args.classification)
    write_inventory(rows, args.output)
    counts = count_by_corpus(rows)
    print("wrote {} rows to {}".format(len(rows), args.output))
    for corpus in sorted(counts):
        print("{}={}".format(corpus, counts[corpus]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
