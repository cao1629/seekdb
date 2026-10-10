#!/usr/bin/env python3
"""Per-module submodule SCC + greedy feedback-arc-set estimate (policy-clean edges).
Usage: python3 -I submodule_fas.py UNITS_DIR"""
import os, sys, collections
U = sys.argv[1]
VIOL = {("src/sql", "src/storage"), ("src/storage", "src/rootserver"), ("src/storage", "src/sql"), ("src/objit", "src/oblib")}
def top(n):
    p = n.split("/")
    return "src/" + p[1] if p[0] == "src" and len(p) > 2 else p[0]
def sub(n):
    p = n.split("/")
    if p[0] == "src" and len(p) > 3: return "src/%s/%s" % (p[1], p[2])
    if p[0] == "src" and len(p) == 3: return "src/%s/(root)" % p[1]
    return top(n)
w = collections.Counter()
with open(os.path.join(U, "depmap", "edges.tsv")) as f:
    next(f)
    for l in f:
        a, b, k = l.rstrip("\n").split("\t")
        if (top(a), top(b)) in VIOL: continue
        if top(a) == top(b) and sub(a) != sub(b):
            w[(sub(a), sub(b))] += 1
def sccs(ns, es):
    adj = collections.defaultdict(list)
    for a, b in es: adj[a].append(b)
    idx, low, st, on, out, c = {}, {}, [], set(), [], [0]
    def strong(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for x in adj[v]:
            if x not in idx: strong(x); low[v] = min(low[v], low[x])
            elif x in on: low[v] = min(low[v], idx[x])
        if low[v] == idx[v]:
            comp = []
            while True:
                x = st.pop(); on.discard(x); comp.append(x)
                if x == v: break
            out.append(comp)
    for n in ns:
        if n not in idx: strong(n)
    return out
def greedy(mem):
    mem = set(mem); rem = set(mem); s1, s2 = [], []
    ow = lambda n: sum(c for (a, b), c in w.items() if a == n and b in rem)
    iw = lambda n: sum(c for (a, b), c in w.items() if b == n and a in rem)
    while rem:
        ch = True
        while ch:
            ch = False
            for n in sorted(rem):
                if ow(n) == 0: s2.insert(0, n); rem.discard(n); ch = True
            for n in sorted(rem):
                if iw(n) == 0: s1.append(n); rem.discard(n); ch = True
        if rem:
            b = max(sorted(rem), key=lambda n: ow(n) - iw(n)); s1.append(b); rem.discard(b)
    order = s1 + s2; pos = {n: i for i, n in enumerate(order)}
    back = [(a, b, c) for (a, b), c in w.items() if a in mem and b in mem and pos[a] > pos[b]]
    return order, back
for m in ["src/sql", "src/storage", "src/share", "src/observer", "src/rootserver", "src/logservice", "src/pl", "src/standby"]:
    ns = sorted({x for e in w for x in e if top(x) == m})
    es = [(a, b) for (a, b) in w if top(a) == m]
    cyc = sorted([s for s in sccs(ns, es) if len(s) > 1], key=len, reverse=True)
    if not cyc: continue
    big = cyc[0]
    tot = sum(c for (a, b), c in w.items() if a in big and b in big)
    order, back = greedy(big)
    bw = sum(c for _, _, c in back)
    back.sort(key=lambda t: -t[2])
    print("%s: largest submodule SCC %d members; intra-SCC file edges=%d; greedy FAS back edges=%d pairs / %d file edges (%.1f%%)" % (
        m, len(big), tot, len(back), bw, 100.0 * bw / tot))
    print("   top back-edge pairs:", [(a.split('/', 2)[2] + "->" + b.split('/', 2)[2], c) for a, b, c in back[:6]])
