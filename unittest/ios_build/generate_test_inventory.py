#!/usr/bin/env python3
"""Generate the deterministic layered iOS test inventory for this checkout."""

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess


EXECUTION_CLASSES = {"device-native", "host-driven-device", "host-only"}
APPLICABILITY_CLASSES = {"required", "excluded", "blocked"}
MEMORY_CLASSES = {"small", "medium", "large", "unbounded"}
LATEST_RESULTS = {"pass", "fail", "blocked", "not-run"}
EXPLICIT_REVIEW_CORPORA = {"mysqltest-active", "gtest-orphan"}
REQUIRED_ROW_FIELDS = {
    "id", "source_path", "corpus", "case_name", "ci_selected", "owning_module",
    "framework", "execution_class", "device_equivalent", "requirements",
    "timeout_seconds", "memory_class", "applicability", "exclusion_reason",
    "blocked_reason", "latest_result", "evidence_path", "classification_source",
    "source_commit", "corpus_digest",
}
CLASSIFICATION_FIELDS = {
    "owning_module", "framework", "execution_class", "device_equivalent",
    "requirements", "timeout_seconds", "memory_class", "applicability",
    "exclusion_reason", "blocked_reason", "latest_result", "evidence_path",
}
MANIFEST_FIELDS = {
    "schema_version", "classifications", "overrides", "reviewed_decisions",
    "materialized_ids",
}
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


def _is_active_mysqltest_path(path):
    """Return whether a path is an active top-level or suite mysqltest case."""
    if not path.startswith("tools/deploy/mysql_test/") or not path.endswith(".test"):
        return False
    parts = PurePosixPath(path).parts
    return ((len(parts) == 5 and parts[3] == "t") or
            (len(parts) == 7 and parts[3] == "test_suite" and parts[5] == "t"))


def _is_mysqltest_result_path(path):
    """Return whether a path is a result file consumed by mysqltest discovery."""
    if not path.startswith("tools/deploy/mysql_test/") or not path.endswith(".result"):
        return False
    parts = PurePosixPath(path).parts
    return ((len(parts) == 6 and parts[3:5] == ("r", "mysql")) or
            (len(parts) == 8 and parts[3] == "test_suite" and
             parts[5:7] == ("r", "mysql")))


def reject_untracked_mysqltests(repo_root):
    """Reject filesystem-only mysqltest cases/results used by the existing runner."""
    output = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--",
         "tools/deploy/mysql_test"],
        cwd=repo_root,
    ).decode("utf-8")
    extras = sorted(
        path for path in output.split("\0")
        if _is_active_mysqltest_path(path) or _is_mysqltest_result_path(path)
    )
    if extras:
        raise InventoryError(
            "untracked active mysqltest files would affect filesystem discovery: {}".format(
                ", ".join(extras)
            )
        )


def relevant_inventory_inputs(tracked_files):
    """Return tracked paths whose working-tree contents can affect inventory output."""
    exact = {
        ".github/script/seekdb/mysqltest_for_seekdb.py",
        "tools/deploy/mysqltest_config.yaml",
        "unittest/ios_build/generate_test_inventory.py",
        "unittest/ios_build/ios-test-classification.json",
    }
    relevant = []
    for path in tracked_files:
        if path in exact:
            relevant.append(path)
        elif _is_active_mysqltest_path(path):
            relevant.append(path)
        elif path.startswith("tools/obtest/t/") and path.endswith(".test"):
            relevant.append(path)
        elif path.startswith("rust/") and path.endswith(".rs"):
            relevant.append(path)
        elif path.endswith((".cc", ".cpp", ".cxx", ".h", ".hpp")):
            relevant.append(path)
        elif re.match(r"^unittest/ios_build/.*_probe\.c$", path):
            relevant.append(path)
        elif re.match(r"^unittest/ios_build/test_.*\.py$", path):
            relevant.append(path)
        elif path.startswith("unittest/") and path.endswith("CMakeLists.txt"):
            relevant.append(path)
        elif _is_mysqltest_result_path(path):
            relevant.append(path)
    return sorted(set(relevant))


def assert_relevant_inputs_clean(repo_root, relevant_paths):
    """Reject staged or unstaged changes to inputs that must describe HEAD exactly."""
    if not relevant_paths:
        return
    output = subprocess.check_output(
        ["git", "diff", "HEAD", "--name-only", "-z", "--"],
        cwd=repo_root,
    ).decode("utf-8")
    changed = set(path for path in output.split("\0") if path)
    dirty_relevant = sorted(changed.intersection(relevant_paths))
    if dirty_relevant:
        raise InventoryError(
            "inventory inputs differ from HEAD: {}".format(", ".join(dirty_relevant))
        )


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
    active_paths = []
    for path in tracked_files:
        if _is_active_mysqltest_path(path):
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
    if set(manifest) != MANIFEST_FIELDS:
        raise InventoryError("classification manifest fields differ from schema")
    if manifest.get("schema_version") != 1:
        raise InventoryError("classification manifest schema_version must be 1")
    if not isinstance(manifest.get("classifications"), dict):
        raise InventoryError("classification manifest must define classifications")
    if not isinstance(manifest.get("overrides", {}), dict):
        raise InventoryError("classification manifest overrides must be an object")
    if not isinstance(manifest.get("reviewed_decisions"), dict):
        raise InventoryError("classification manifest must define reviewed_decisions")
    if not isinstance(manifest.get("materialized_ids"), dict):
        raise InventoryError("classification manifest must define materialized_ids")
    for name, classification in manifest["classifications"].items():
        if (not isinstance(name, str) or not name.strip() or
                not isinstance(classification, dict) or
                set(classification) != CLASSIFICATION_FIELDS):
            raise InventoryError("classification {} differs from schema".format(name))
    for test_id, override in manifest["overrides"].items():
        if (not isinstance(test_id, str) or not test_id.strip() or
                not isinstance(override, dict) or
                not set(override).issubset(CLASSIFICATION_FIELDS)):
            raise InventoryError("override {} differs from schema".format(test_id))
    return manifest


def _validate_classification(row):
    """Validate one joined inventory row and its exclusion or blocked reason."""
    if row.get("execution_class") not in EXECUTION_CLASSES:
        raise InventoryError("{} has invalid execution_class".format(row["id"]))
    if row.get("applicability") not in APPLICABILITY_CLASSES:
        raise InventoryError("{} has invalid applicability".format(row["id"]))
    if row.get("memory_class") not in MEMORY_CLASSES:
        raise InventoryError("{} has invalid memory_class".format(row["id"]))
    requirements = row.get("requirements")
    if (not isinstance(requirements, list) or not requirements or
            any(not isinstance(item, str) or not item.strip() for item in requirements)):
        raise InventoryError("{} requirements must be nonempty strings".format(row["id"]))
    timeout = row.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise InventoryError("{} timeout_seconds must be positive".format(row["id"]))
    equivalent = row.get("device_equivalent")
    if equivalent is not None and (not isinstance(equivalent, str) or not equivalent.strip()):
        raise InventoryError("{} device_equivalent must be null or nonempty".format(row["id"]))
    for field in ("exclusion_reason", "blocked_reason"):
        value = row.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InventoryError("{} {} must be null or nonempty".format(row["id"], field))
    exclusion_reason = row.get("exclusion_reason")
    blocked_reason = row.get("blocked_reason")
    if row["applicability"] == "excluded":
        if not exclusion_reason or blocked_reason is not None:
            raise InventoryError("{} exclusion requires only exclusion_reason".format(row["id"]))
    elif row["applicability"] == "blocked":
        if not blocked_reason or exclusion_reason is not None:
            raise InventoryError(
                "{} blocked classification requires only blocked_reason".format(row["id"])
            )
    elif exclusion_reason is not None or blocked_reason is not None:
        raise InventoryError("{} required classification cannot carry a reason".format(row["id"]))
    if row.get("latest_result") not in LATEST_RESULTS:
        raise InventoryError("{} has invalid latest_result".format(row["id"]))


def _require_nonempty_string(row, field):
    """Require one row field to be a nonempty string."""
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InventoryError("{} must be a nonempty string".format(field))


def _validate_relative_path(row, field, allow_null=False):
    """Require a portable repository-relative path, optionally allowing null."""
    value = row.get(field)
    if allow_null and value is None:
        return
    _require_nonempty_string(row, field)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise InventoryError("{} must be repository-relative".format(field))


def validate_inventory_row(row):
    """Validate every required inventory field, type, enum, and reason invariant."""
    if set(row) != REQUIRED_ROW_FIELDS:
        missing = sorted(REQUIRED_ROW_FIELDS - set(row))
        extra = sorted(set(row) - REQUIRED_ROW_FIELDS)
        raise InventoryError("inventory fields differ from schema: missing={} extra={}".format(
            missing, extra
        ))
    for field in (
        "id", "corpus", "case_name", "owning_module", "framework",
        "classification_source",
    ):
        _require_nonempty_string(row, field)
    _validate_relative_path(row, "source_path")
    if not isinstance(row.get("ci_selected"), bool):
        raise InventoryError("ci_selected must be a boolean")
    _validate_classification(row)
    _validate_relative_path(row, "evidence_path", allow_null=True)
    commit = row.get("source_commit")
    if (not isinstance(commit, str) or
            re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None):
        raise InventoryError("source_commit must be a Git object ID")
    digest = row.get("corpus_digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise InventoryError("corpus_digest must be a SHA-256 digest")


def discover_normal_cpp_test_targets(repo_root, tracked_files):
    """Derive normal C++ test targets from tracked unittest CMake definitions."""
    targets = []
    target_pattern = re.compile(
        r"(?im)^\s*(?:add_executable|add_library|add_ob_unittest)\s*\(\s*([^\s\)]+)"
    )
    for path in tracked_files:
        if (not path.startswith("unittest/") or not path.endswith("CMakeLists.txt") or
                path.startswith("unittest/ios_build/")):
            continue
        content = (Path(repo_root) / path).read_text(encoding="utf-8", errors="replace")
        for target in target_pattern.findall(content):
            targets.append("{}:{}".format(path, target))
    return sorted(targets)


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


def build_inventory(repo_root, manifest_path, allow_dirty=False):
    """Discover, classify, validate, and return sorted inventory rows."""
    repo_root = Path(repo_root).resolve()
    tracked_files = git_tracked_files(repo_root)
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest_relative = manifest_path.relative_to(repo_root).as_posix()
    except ValueError:
        raise InventoryError("classification manifest must be inside the repository")
    if manifest_relative not in tracked_files:
        raise InventoryError("classification manifest must be tracked by Git")
    reject_untracked_mysqltests(repo_root)
    if not allow_dirty:
        relevant_paths = set(relevant_inventory_inputs(tracked_files))
        relevant_paths.add(manifest_relative)
        assert_relevant_inputs_clean(repo_root, sorted(relevant_paths))
    manifest = _load_manifest(manifest_path)
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
    reviewed = manifest["reviewed_decisions"]
    expected_reviewed = {
        discovery["id"] for discovery in discoveries
        if discovery["corpus"] in EXPLICIT_REVIEW_CORPORA
    }
    if set(reviewed) != expected_reviewed:
        missing = sorted(expected_reviewed - set(reviewed))
        stale = sorted(set(reviewed) - expected_reviewed)
        raise InventoryError(
            "reviewed decisions must exactly match active mysqltest and orphan GTest IDs; "
            "missing={} stale={}".format(missing, stale)
        )
    discoveries_by_id = {discovery["id"]: discovery for discovery in discoveries}
    for test_id, classification_name in reviewed.items():
        if (not isinstance(classification_name, str) or
                classification_name not in classifications or
                classification_name != discoveries_by_id[test_id]["corpus"]):
            raise InventoryError("{} has invalid reviewed classification".format(test_id))

    if set(manifest["materialized_ids"]) != {"obtest-legacy"}:
        raise InventoryError("materialized_ids must contain only obtest-legacy")
    materialized = manifest["materialized_ids"]["obtest-legacy"]
    if (not isinstance(materialized, list) or
            any(not isinstance(test_id, str) for test_id in materialized) or
            len(materialized) != len(set(materialized))):
        raise InventoryError("obtest-legacy materialized IDs must be a unique list")
    expected_legacy = {
        discovery["id"] for discovery in discoveries
        if discovery["corpus"] == "obtest-legacy"
    }
    if set(materialized) != expected_legacy:
        raise InventoryError("materialized obtest-legacy IDs must exactly match discovery")

    rows = []
    for discovery in discoveries:
        corpus = discovery["corpus"]
        classification_name = reviewed.get(discovery["id"], corpus)
        if classification_name not in classifications:
            raise InventoryError("unclassified corpus: {}".format(corpus))
        classification = dict(classifications[classification_name])
        classification_source = "classification:{}".format(corpus)
        if discovery["id"] in reviewed:
            classification_source = "reviewed:{}".format(discovery["id"])
        elif corpus == "obtest-legacy":
            classification_source = "materialized:{}".format(discovery["id"])
        if discovery["id"] in overrides:
            classification.update(overrides[discovery["id"]])
            classification_source += "+override"
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
        validate_inventory_row(row)
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
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow relevant tracked changes for pre-commit development only",
    )
    return parser.parse_args()


def main():
    """Generate the inventory and print its path and corpus counts."""
    args = _parse_args()
    rows = build_inventory(args.repo_root, args.classification, allow_dirty=args.allow_dirty)
    write_inventory(rows, args.output)
    counts = count_by_corpus(rows)
    print("wrote {} rows to {}".format(len(rows), args.output))
    for corpus in sorted(counts):
        print("{}={}".format(corpus, counts[corpus]))
    print("normal-cpp-target={}".format(
        len(discover_normal_cpp_test_targets(args.repo_root, git_tracked_files(args.repo_root)))
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
