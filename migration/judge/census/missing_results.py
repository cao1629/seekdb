#!/usr/bin/env python3
"""Print, comma-separated, the runnable judge cases whose expected .result does not exist yet.

Usage: python3 -I missing_results.py <repo_root> [--only PREFIX] [--skip PREFIX]
"""
import csv
import sys
from pathlib import Path

root = Path(sys.argv[1])
only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
skip = sys.argv[sys.argv.index("--skip") + 1] if "--skip" in sys.argv else None
manifest = root / "migration/judge/suites/mysqltest/cases.tsv"
names = []
with manifest.open(encoding="utf-8", newline="") as handle:
    for row in csv.DictReader(handle, delimiter="\t"):
        if row["status"] in ("quarantined", "pending") or (root / row["result_file"]).is_file():
            continue
        if only and not row["name"].startswith(only):
            continue
        if skip and row["name"].startswith(skip):
            continue
        names.append(row["name"])
print(",".join(names))
print(len(names), file=sys.stderr)
