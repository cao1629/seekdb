"""Distinct server_service<T> slot types: uses per T, T's declaring module, using modules, and whether any
use comes from a module below T's declaring module in the include order (would need an interface slot).
Usage: python3 -I service_slots.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
ORDER = ["oblib", "share", "data_plane", "logservice", "objit", "query", "sql", "pl", "storage", "rootserver", "standby", "observer"]
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
PAT = re.compile(r"\bserver_service\s*<\s*((?:::)?[\w:]+(?:\s*<[^<>]*>)?)\s*>")
CLS = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?([A-Za-z_]\w*)\b(?:\s+final)?\s*(?:\{|:(?!:))")
text = {}
for f in files:
    text[f] = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
decl = collections.defaultdict(set)
for f, t in text.items():
    if re.search(r"\.(h|hpp|ipp|inl)$", f):
        for c in CLS.findall(t):
            decl[c].add(f.split("/")[1] + ("/api" if f.split("/")[1] in ("query", "data_plane") and "/api/" in f else ""))
uses = collections.defaultdict(collections.Counter)
for f, t in text.items():
    for m in PAT.findall(t):
        name = re.sub(r"\s+", "", m).lstrip(":").replace("oceanbase::", "")
        if name in ("Service", "type", "T", "common::ObServerObjectPool<T>"):
            continue
        uses[name][f.split("/")[1]] += 1
interface_like = 0
below = []
mods = collections.Counter()
for name, c in sorted(uses.items(), key=lambda kv: -sum(kv[1].values())):
    short = name.split("::")[-1].split("<")[0]
    dm = sorted(decl.get(short, {"?"}))
    mods[dm[0]] += 1
    if re.match(r"Ob?I[A-Z]|I[A-Z]", short):
        interface_like += 1
    dmod = dm[0].split("/")[0]
    if dmod in ORDER:
        lower = [m for m in c if m in ORDER and ORDER.index(m) < ORDER.index(dmod)]
        if lower:
            below.append((name, dmod, lower))
print("distinct slot types:", len(uses), " total uses:", sum(sum(c.values()) for c in uses.values()))
print("interface-named types (I*/ObI*):", interface_like)
print("declaring module of slot types:", dict(mods.most_common()))
print("top 15 slots by uses:")
for name, c in sorted(uses.items(), key=lambda kv: -sum(kv[1].values()))[:15]:
    print("  %4d %-55s %s" % (sum(c.values()), name, dict(c.most_common())))
print("slots used from a module below the type's declaring module:", below[:10])
