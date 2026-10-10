import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
cfg = (root/"tools/deploy/mysqltest_config.yaml").read_text().splitlines()
enabled, commented = [], []
for l in cfg:
    m = re.match(r"^      - (\S+)\s*$", l)
    if m: enabled.append(m.group(1)); continue
    m = re.match(r"^      # - (\S+)\s*$", l)
    if m: commented.append(m.group(1))
mt = root/"tools/deploy/mysql_test"
avail = {}
for f in sorted((mt/"t").glob("*.test")): avail[f.stem] = f
for f in sorted((mt/"test_suite").glob("*/t/*.test")): avail[f"{f.parent.parent.name}.{f.stem}"] = f
en = set(enabled)
print("enabled in CI config:", len(enabled), " unique:", len(en))
print("  top-level enabled:", sum(1 for e in enabled if '.' not in e), " suite enabled:", sum(1 for e in enabled if '.' in e))
print("commented out (# - ):", len(commented), commented)
print("available .test cases:", len(avail))
missing = sorted(en - set(avail)); print("enabled but no .test:", missing)
notlisted = sorted(set(avail) - en)
print("available but not in CI list:", len(notlisted))
from collections import Counter
c = Counter(n.split('.')[0] if '.' in n else '<top>' for n in notlisted)
for k,v in sorted(c.items(), key=lambda x:-x[1]): print(f"   {k}: {v}")
pathlib.Path(sys.argv[2]).write_text("\n".join(enabled)+"\n")
pathlib.Path(sys.argv[3]).write_text("\n".join(notlisted)+"\n")
# per-suite enabled vs total
tot = Counter(n.split('.')[0] if '.' in n else '<top>' for n in avail)
ena = Counter(n.split('.')[0] if '.' in n else '<top>' for n in en)
print("\nper-suite enabled/total:")
for k in sorted(tot): print(f"   {k}: {ena.get(k,0)}/{tot[k]}")
