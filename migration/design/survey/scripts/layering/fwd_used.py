"""Of the upward forward-declared types in lower headers, how many are actually used in that header
(appear again after the forward declaration), i.e. cross the seam as opaque pointers/references.
Usage: python3 -I fwd_used.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
hdrs = [f for f in files if re.search(r"\.(h|hpp|ipp|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
ORDER = ["oblib", "share", "data_plane", "logservice", "objit", "query", "sql", "pl", "storage", "rootserver", "standby", "observer"]
text = {f: COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read()) for f in hdrs}
DEF = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?([A-Za-z_]\w*)\b(?:\s+final)?\s*(?:\{|:(?!:))")
FWD = re.compile(r"^\s*(?:class|struct)\s+([A-Za-z_]\w*)\s*;", re.M)
defined_in = collections.defaultdict(set)
for f, t in text.items():
    for c in DEF.findall(t):
        defined_in[c].add(f.split("/")[1])
used = collections.defaultdict(set)
for f, t in text.items():
    m = f.split("/")[1]
    if m not in ORDER:
        continue
    for c in set(FWD.findall(t)):
        mods = defined_in.get(c, set())
        if not mods or m in mods:
            continue
        lowest = min(mods, key=lambda x: ORDER.index(x) if x in ORDER else 99)
        if lowest in ORDER and ORDER.index(lowest) > ORDER.index(m):
            if len(re.findall(r"\b" + re.escape(c) + r"\b", t)) > 1:
                used[(m, lowest)].add(c)
tot = set()
for k, v in sorted(used.items(), key=lambda kv: -len(kv[1])):
    tot |= v
    print(k, len(v), sorted(v)[:6])
print("distinct upward opaque types actually used in lower headers:", len(tot))
