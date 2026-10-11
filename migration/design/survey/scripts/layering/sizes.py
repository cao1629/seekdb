"""Module and subdirectory sizes (lines, units) from depmap nodes/units, plus subdirectory include
graphs for big modules with a greedy feedback-arc-set ordering (Eades-Lin-Smyth) to estimate how many
include edges would point backwards if a module were split into one crate per subdirectory.
Usage: python3 -I sizes.py <repo> <module> [<module>...]"""
import collections, os, sys
repo = os.path.abspath(sys.argv[1])
lines, crate = {}, {}
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/nodes.tsv"))):
    if i:
        p, n, c, u = l.rstrip("\n").split("\t")
        lines[p] = int(n)
units = collections.Counter()
ulines = collections.Counter()
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/units/units.tsv"))):
    if i:
        u, fs, n, c = l.rstrip("\n").split("\t")
        units[c] += 1
        ulines[c] += int(n)
print("module\tunits\tlines")
for c, n in sorted(ulines.items(), key=lambda kv: -kv[1]):
    print("%s\t%d\t%d" % (c, units[c], n))
print("total\t%d\t%d" % (sum(units.values()), sum(ulines.values())))
edges = collections.Counter()
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/edges.tsv"))):
    if i:
        a, b, kind, cond, ln = l.rstrip("\n").split("\t")
        edges[(a, b)] += 1


def sub(p):
    s = p.split("/")
    return s[2] if len(s) > 3 else "(root)"


for mod in sys.argv[2:]:
    pre = "src/%s/" % mod
    sl = collections.Counter()
    for p, n in lines.items():
        if p.startswith(pre):
            sl[sub(p)] += n
    g = collections.Counter()
    for (a, b), c in edges.items():
        if a.startswith(pre) and b.startswith(pre) and sub(a) != sub(b):
            g[(sub(a), sub(b))] += c
    nodes = set(sl)
    # Eades-Lin-Smyth greedy ordering
    out_w = collections.defaultdict(collections.Counter)
    in_w = collections.defaultdict(collections.Counter)
    for (a, b), c in g.items():
        out_w[a][b] += c
        in_w[b][a] += c
    rem = set(nodes)
    s1, s2 = [], []
    while rem:
        changed = True
        while changed:
            changed = False
            for v in sorted(rem):
                if not any(u in rem for u in out_w[v]):  # sink
                    s2.insert(0, v); rem.discard(v); changed = True
            for v in sorted(rem):
                if not any(u in rem for u in in_w[v]):  # source
                    s1.append(v); rem.discard(v); changed = True
        if rem:
            v = max(sorted(rem), key=lambda x: sum(c for u, c in out_w[x].items() if u in rem) - sum(c for u, c in in_w[x].items() if u in rem))
            s1.append(v); rem.discard(v)
    order = s1 + s2  # sources first: an edge a->b is "forward" if a before b (a depends on b)
    pos = {v: i for i, v in enumerate(order)}
    back = [(a, b, c) for (a, b), c in g.items() if pos[a] > pos[b]]
    fwd = sum(c for (a, b), c in g.items() if pos[a] < pos[b])
    print("\n== %s: %d subdirs, %d lines; cross-subdir include edges %d; greedy order back edges %d (%d pairs)" % (
        mod, len(nodes), sum(sl.values()), sum(g.values()), sum(c for a, b, c in back), len(back)))
    print("  sizes:", ", ".join("%s=%d" % kv for kv in sl.most_common()))
    print("  order (dependents first):", " > ".join(order))
    print("  heaviest back edges:", ", ".join("%s->%s:%d" % (a, b, c) for a, b, c in sorted(back, key=lambda x: -x[2])[:15]))
