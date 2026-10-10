#!/usr/bin/env python3
"""Point rewritten cases whose copy does not really differ from the original back at the original.

A rewriter copies every case it reviews. When the copy equals the original once
comment lines (lines whose first non-blank character is '#') are ignored, the
census hits were Tier A or gray constructs and the original is portable as is:
the case becomes `portable`, its paths revert to the original files, and the
copy is deleted. Copies with real edits (including the server-side sleep fix)
stay `rewritten`.

Usage: python3 -I reclassify_unchanged.py <repo_root> [--dry-run]
"""
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
dry_run = "--dry-run" in sys.argv
manifest = root / "migration/judge/suites/mysqltest/cases.tsv"
tool = root / "migration/judge/census/update_manifest.py"


def significant(path):
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    return [line.rstrip() for line in lines if not line.lstrip().startswith("#")]


header, *rows = manifest.read_text(encoding="utf-8").rstrip("\n").split("\n")
fields = header.split("\t")
moved = []
for line in rows:
    row = dict(zip(fields, line.split("\t")))
    if row["status"] != "rewritten" or not row["test_file"].startswith("migration/"):
        continue
    copy, original = root / row["test_file"], root / row["origin"]
    if significant(copy) != significant(original):
        continue
    if original.parent.parent.name == "mysql_test":  # top-level case: t/<name>.test -> r/mysql/<name>.result
        result = original.parent.parent / "r" / "mysql" / (original.stem + ".result")
    else:  # suite case: test_suite/<suite>/t/<name>.test -> test_suite/<suite>/r/mysql/<name>.result
        result = original.parent.parent / "r" / "mysql" / (original.stem + ".result")
    moved.append((row["name"], row["origin"], str(result.relative_to(root)), copy))

for name, test_file, result_file, copy in moved:
    print("portable: {}".format(name))
    if dry_run:
        continue
    subprocess.run([sys.executable, "-I", str(tool), str(root), "set", name, "status=portable",
                    "test_file=" + test_file, "result_file=" + result_file,
                    "note=reviewed by its rewriter: census hits are Tier A or gray constructs, "
                    "so the original runs unchanged (copy deleted)"], check=True, stdout=subprocess.DEVNULL)
    copy.unlink()
print("{} case(s) {}".format(len(moved), "would move" if dry_run else "moved to portable"))
