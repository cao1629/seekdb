import json, re, sys, pathlib
sys.path.insert(0, ".")
import os; ROOT = pathlib.Path(os.environ["REPO"])
import importlib.util
spec = importlib.util.spec_from_file_location("cl", "classify.py")
rows = json.load(open("classification.json"))
src = open("classify.py").read()
ns = {}
exec(src.split("SRC_RE =")[0].replace("ROOT = Path(sys.argv[1])", "ROOT=Path('.')").replace("OUT = Path(sys.argv[2])", "OUT=Path('.')"), ns)
CATS = dict(ns["SQL_CATS"]); CATS.update({k:v for k,v in ns["OBT_CATS"].items() if v})
I = re.I | re.M
def find_line(files, pats, cat):
    for idx, f in enumerate(files):
        p = ROOT / f
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or re.match(r"-*\s*echo\b", s, re.I): continue
            for pat in pats:
                if re.search(pat, line, I):
                    return ("" if idx == 0 else f"[case {files[0].split('/')[-1]} via include] ") + f"{f}:{i}: {s[:100]}"
    return None
want = sys.argv[1].split(",")
prefix = sys.argv[2]
seen_suites = {}
for cat in want:
    out = []
    used = set()
    cands = [(k, v) for k, v in rows.items() if k.startswith(prefix) and cat in v["hits"]]
    def own(kv):
        v = kv[1]
        if cat == "error_code_ob_specific": return 0
        return 0 if find_line(v["files"][:1], CATS[cat], cat) else 1
    cands.sort(key=lambda kv: (not kv[1]["ci"], own(kv), kv[0]))
    for k, v in cands:
        suite = v["suite"]
        if suite in used and len(set(x[1]['suite'] for x in cands)) >= 3: continue
        if cat == "error_code_ob_specific":
            tok = v["hits"][cat]
            loc = find_line(v["files"], [rf"^\s*(--)?\s*error\s+.*\b{re.escape(tok)}\b"], cat)
        else:
            loc = find_line(v["files"], CATS[cat], cat)
        if loc:
            out.append(("CI " if v["ci"] else "   ") + loc); used.add(suite)
        if len(out) == 3: break
    n = sum(1 for k,v in rows.items() if k.startswith(prefix) and cat in v["hits"])
    print(f"## {cat} ({n})"); print("\n".join(out))
