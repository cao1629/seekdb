#!/usr/bin/env python3
"""List the mysqltest cases CI runs, from the blacklist-style mysqltest_config.yaml.

Read-only. Usage: python3 -I ci_enabled.py <repo_root> <out_dir>
Writes <out_dir>/ci_enabled.txt (one case name per line, sorted) and prints counts.
A case name is `<test>` for tools/deploy/mysql_test/t/<test>.test and
`<suite>.<test>` for tools/deploy/mysql_test/test_suite/<suite>/t/<test>.test,
matching .github/script/seekdb/mysqltest_for_seekdb.py.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
mt = root / "tools/deploy/mysql_test"

available = sorted(
    [f.stem for f in (mt / "t").glob("*.test")]
    + ["{}.{}".format(f.parent.parent.name, f.stem) for f in (mt / "test_suite").glob("*/t/*.test")]
)

excluded = []
in_exclude = False
for line in (root / "tools/deploy/mysqltest_config.yaml").read_text().splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if stripped == "exclude-set:":
        in_exclude = True
        continue
    match = re.match(r"^- (\S+)$", stripped)
    if in_exclude and match:
        excluded.append(match.group(1))
    elif in_exclude:
        in_exclude = False

unknown = sorted(set(excluded) - set(available))
if unknown:
    sys.exit("exclude-set names cases that do not exist: {}".format(", ".join(unknown)))

enabled = [name for name in available if name not in set(excluded)]
out.mkdir(parents=True, exist_ok=True)
(out / "ci_enabled.txt").write_text("\n".join(enabled) + "\n")
print("available cases: {}".format(len(available)))
print("excluded by CI:  {}".format(len(excluded)))
print("run by CI:       {}".format(len(enabled)))
