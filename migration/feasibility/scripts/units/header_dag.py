#!/usr/bin/env python3
"""Header-stem graph: SCCs, condensation depth (waves) and wave widths.
Usage: python3 -I header_dag.py UNITS_DIR"""
import os, sys, collections
U = sys.argv[1]
HEXT = (".h", ".hpp", ".ipp", ".def", ".map")
edges = []
with open(os.path.join(U, "depmap", "edges.tsv")) as f:
    next(f)
    for l in f:
        a, b, k = l.rstrip("\n").split("\t")
        edges.append((a, b))
def stem(n): return os.path.splitext(n)[0]
hnodes = set()
for a, b in edges:
    if a.endswith(HEXT): hnodes.add(stem(a))
    if b.endswith(HEXT): hnodes.add(stem(b))
w = set()
for a, b in edges:
    if a.endswith(HEXT) and b.endswith(HEXT) and stem(a) != stem(b):
        w.add((stem(a), stem(b)))
sys.setrecursionlimit(100000)
nodes = sorted(hnodes)
adj = collections.defaultdict(list)
for a, b in sorted(w): adj[a].append(b)
# iterative tarjan
idx, low, st, on, comps, c = {}, {}, [], set(), [], [0]
for s in nodes:
    if s in idx: continue
    work = [(s, 0)]
    while work:
        v, pi = work[-1]
        if pi == 0:
            idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        rec = False
        for i in range(pi, len(adj[v])):
            x = adj[v][i]
            if x not in idx:
                work[-1] = (v, i + 1); work.append((x, 0)); rec = True; break
            elif x in on: low[v] = min(low[v], idx[x])
        if rec: continue
        if low[v] == idx[v]:
            comp = []
            while True:
                x = st.pop(); on.discard(x); comp.append(x)
                if x == v: break
            comps.append(comp)
        work.pop()
        if work: low[work[-1][0]] = min(low[work[-1][0]], low[v])
cid = {n: i for i, comp in enumerate(comps) for n in comp}
cyc = sorted((len(c) for c in comps if len(c) > 1), reverse=True)
print("header-stem nodes=%d edges=%d cyclic SCCs=%d sizes=%s" % (len(nodes), len(w), len(cyc), cyc))
# Tarjan emits SCCs in reverse topological order (sinks first) -> level = 1 + max(level of successors)
level = {}
for i, comp in enumerate(comps):
    lv = 0
    for n in comp:
        for x in adj[n]:
            j = cid[x]
            if j != i: lv = max(lv, level[j] + 1)
    level[i] = lv
depth = max(level.values()) + 1
widths = collections.Counter(level.values())
print("condensation depth (waves, leaves=wave 0)=%d; widths of first 10 waves=%s; max width=%d" % (
    depth, [widths[i] for i in range(min(10, depth))], max(widths.values())))
