"""Forward declarations (class X; / struct X;) inside api headers and lower modules whose complete type is
defined only in an upper module. In Rust there is no cross-crate forward declaration: each becomes a
trait object, a handle type defined low, or a moved definition.
Usage: python3 -I fwd_decls.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
hdrs = [f for f in files if re.search(r"\.(h|hpp|ipp|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
ORDER = ["oblib", "share", "data_plane", "logservice", "objit", "query", "sql", "pl", "storage", "rootserver", "standby", "observer"]


def mod(p):
    return p.split("/")[1]


text = {f: COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read()) for f in hdrs}
DEF = re.compile(r"\b(?:class|struct)\s+(?:[A-Z_]+\s+)?([A-Za-z_]\w*)\b(?:\s+final)?\s*(?:\{|:(?!:))")
FWD = re.compile(r"^\s*(?:class|struct)\s+([A-Za-z_]\w*)\s*;", re.M)
defined_in = collections.defaultdict(set)
for f, t in text.items():
    for c in DEF.findall(t):
        defined_in[c].add(mod(f))
res = collections.defaultdict(set)
for f, t in text.items():
    m = mod(f)
    if m not in ORDER:
        continue
    for c in FWD.findall(t):
        mods = defined_in.get(c, set())
        if not mods or m in mods:
            continue
        lowest = min(mods, key=lambda x: ORDER.index(x) if x in ORDER else 99)
        if lowest in ORDER and ORDER.index(lowest) > ORDER.index(m):
            res[(m, lowest)].add(c)
tot = set()
for (a, b), cs in sorted(res.items(), key=lambda kv: -len(kv[1])):
    tot.update(cs)
    print("%-11s fwd-declares %3d types defined only in %-10s e.g. %s" % (a, len(cs), b, ", ".join(sorted(cs)[:5])))
print("distinct upward forward-declared types:", len(tot))
