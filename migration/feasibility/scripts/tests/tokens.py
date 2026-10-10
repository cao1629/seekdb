import re, sys, pathlib
from collections import Counter
root = pathlib.Path(sys.argv[1]); sub = sys.argv[2]; pat = sys.argv[3]
c = Counter(); files = Counter()
for f in (root/sub).rglob("*"):
    if f.suffix not in (".test", ".inc"): continue
    txt = "\n".join(l for l in f.read_text(errors="replace").splitlines() if not l.strip().startswith("#") and not re.match(r"\s*-*\s*echo\b", l, re.I))
    toks = set(t.lower() for t in re.findall(pat, txt, re.I | re.M))
    for t in toks: files[t] += 1
for t, n in files.most_common(int(sys.argv[4]) if len(sys.argv) > 4 else 40): print(f"{n:4d} {t}")
