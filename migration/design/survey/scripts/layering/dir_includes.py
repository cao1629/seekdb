"""Lines and direct include fan-out (by module) of a set of directories/files.
Usage: python3 -I dir_includes.py <repo> <prefix> [<prefix> ...]"""
import collections, os, sys
repo = os.path.abspath(sys.argv[1])
edges = collections.defaultdict(set)
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/edges.tsv"))):
    if i:
        a, b, kind, cond, ln = l.rstrip("\n").split("\t")
        edges[a].add(b)
lines = {}
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/nodes.tsv"))):
    if i:
        p, n, crate, unit = l.rstrip("\n").split("\t")
        lines[p] = int(n)


def mod(p):
    s = p.split("/")
    if s[1] in ("query", "data_plane") and len(s) > 2 and s[2] == "api":
        return s[1] + "/api"
    return s[1]


for pre in sys.argv[2:]:
    fs = [p for p in lines if p.startswith(pre)]
    c = collections.Counter()
    for f in fs:
        for t in edges.get(f, ()):
            if not t.startswith(pre):
                c[mod(t)] += 1
    print("%s: %d files, %d lines; external include edges by module: %s" % (pre, len(fs), sum(lines[f] for f in fs), dict(c.most_common())))
