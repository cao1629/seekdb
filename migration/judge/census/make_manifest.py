#!/usr/bin/env python3
"""Seed migration/judge/suites/mysqltest/cases.tsv from the census.

Read-only toward the repo except for the manifest it writes.
Usage: python3 -I make_manifest.py <repo_root>

CI-run cases only (census/out/ci_enabled.txt). Clean cases become `clean`,
public-but-fragile ones `fragile`, internal-bound ones `pending` (to be
rewritten or quarantined); paths point at the original files. Refuses to
overwrite an existing manifest, since later statuses are edited by hand.
"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
judge = root / "migration/judge"
out = judge / "census/out"
manifest = judge / "suites/mysqltest/cases.tsv"
if manifest.exists():
    sys.exit("{} exists; refusing to overwrite".format(manifest))

classification = json.loads((out / "classification.json").read_text())


def names(list_file):
    return [line[3:] for line in (out / list_file).read_text().split() if line.startswith("mt:")]


def paths(name):
    if "." in name:
        suite, test = name.split(".", 1)
        base = "tools/deploy/mysql_test/test_suite/{}".format(suite)
        return "{}/t/{}.test".format(base, test), "{}/r/mysql/{}.result".format(base, test)
    return ("tools/deploy/mysql_test/t/{}.test".format(name),
            "tools/deploy/mysql_test/r/mysql/{}.result".format(name))


rows = []
for status, list_file in (("clean", "mt_ci_clean.txt"), ("fragile", "mt_ci_tier2only.txt"),
                          ("pending", "mt_ci_tier1.txt")):
    for name in names(list_file):
        entry = classification["mt:" + name]
        categories = sorted(set(entry["hits"]) | {k for k, v in entry["res"].items() if v is True})
        test_file, result_file = paths(name)
        if not (root / test_file).is_file() or not (root / result_file).is_file():
            sys.exit("missing original files for {}".format(name))
        rows.append((name, status, test_file, result_file, test_file, ",".join(categories)))

rows.sort(key=lambda row: row[0])
manifest.parent.mkdir(parents=True, exist_ok=True)
with manifest.open("w", encoding="utf-8") as handle:
    handle.write("name\tstatus\ttest_file\tresult_file\torigin\tnote\n")
    for row in rows:
        handle.write("\t".join(row) + "\n")
counts = {}
for row in rows:
    counts[row[1]] = counts.get(row[1], 0) + 1
print("wrote {} cases: {}".format(len(rows), counts))
