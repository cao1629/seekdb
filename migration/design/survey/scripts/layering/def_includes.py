"""For the files that define against/across symbols, list which modules they include directly
(depmap edges.tsv, kind inc) — tells whether the bodies could move down into the declaring crate.
Usage: python3 -I def_includes.py <repo> <classified.tsv>"""
import collections, os, sys
repo = os.path.abspath(sys.argv[1])
edges = collections.defaultdict(set)
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/edges.tsv"))):
    if i:
        a, b, kind, cond, ln = l.rstrip("\n").split("\t")
        edges[a].add(b)


def mod(p):
    s = p.split("/")
    if s[1] in ("query", "data_plane") and len(s) > 2 and s[2] == "api":
        return s[1] + "/api"
    return s[1]


rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[2])) if i]
by_edge = collections.defaultdict(lambda: collections.defaultdict(int))
deffiles = collections.defaultdict(set)
for r in rows:
    for d in r[8].split(";"):
        if d and d != "?":
            deffiles[(r[0][4:], r[1][4:])].add(d)
for e, fs in sorted(deffiles.items()):
    print("== %s -> %s : %d defining files" % (e[0], e[1], len(fs)))
    for f in sorted(fs):
        m = collections.Counter(mod(t) for t in edges.get(f, ()))
        own = mod(f)
        upper = {k: v for k, v in m.items() if k not in ("oblib", "share", "data_plane/api", "query/api", "objit", own)}
        print("   %-75s own=%d other=%s" % (f, m.get(own, 0), dict(upper) if upper else "-"))
