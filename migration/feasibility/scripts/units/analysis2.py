#!/usr/bin/env python3
"""Follow-up graph analysis on depmap outputs (stdlib only).
Usage: python3 -I analysis2.py REPO UNITS_DIR
Reads UNITS_DIR/depmap/edges.tsv; writes UNITS_DIR/analysis2.txt"""
import collections
import os
import statistics
import sys

REPO, U = sys.argv[1], sys.argv[2]
D = os.path.join(U, "depmap")
edges = []
with open(os.path.join(D, "edges.tsv")) as f:
    next(f)
    for line in f:
        a, b, k = line.rstrip("\n").split("\t")
        edges.append((a, b, k))
nodes = sorted({a for a, _, _ in edges} | {b for _, b, _ in edges})
# include isolated nodes from order.txt
with open(os.path.join(D, "order.txt")) as f:
    next(f)
    for line in f:
        for n in line.rstrip("\n").split("\t"):
            if n:
                nodes.append(n)
nodes = sorted(set(nodes))

lines_of = {}
for n in nodes:
    with open(os.path.join(REPO, n), "rb") as fh:
        lines_of[n] = fh.read().count(b"\n")


def tarjan(ns, es):
    adj = {n: [] for n in ns}
    for a, b in es:
        adj[a].append(b)
    for n in adj:
        adj[n].sort()
    idx, low, st, on, out, c = {}, {}, [], set(), [], [0]
    for s in ns:
        if s in idx:
            continue
        work = [(s, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
            rec = False
            for i in range(pi, len(adj[v])):
                w = adj[v][i]
                if w not in idx:
                    work[-1] = (v, i + 1); work.append((w, 0)); rec = True; break
                elif w in on:
                    low[v] = min(low[v], idx[w])
            if rec:
                continue
            if low[v] == idx[v]:
                comp = []
                while True:
                    w = st.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
    return out


def top(n):
    p = n.split("/")
    if p[0] == "src" and len(p) > 2:
        return "src/" + p[1]
    if p[0] in ("tools", "deps", "rust") and len(p) > 2:
        return p[0] + "/" + p[1]
    return p[0]


def sub(n):
    p = n.split("/")
    if p[0] == "src" and len(p) > 3:
        return "src/%s/%s" % (p[1], p[2])
    if p[0] == "src" and len(p) == 3:
        return "src/%s/(root)" % p[1]
    return top(n)


def unit(n):
    return os.path.splitext(n)[0]


VIOL = {("src/sql", "src/storage"), ("src/storage", "src/rootserver"),
        ("src/storage", "src/sql"), ("src/objit", "src/oblib")}
clean = [(a, b) for a, b, k in edges if (top(a), top(b)) not in VIOL]
removed = [(a, b) for a, b, k in edges if (top(a), top(b)) in VIOL]
R = []
R.append("edges=%d; policy-violating file edges removed=%d: %s" % (len(edges), len(removed), removed))


def collapse(es, kf):
    w = collections.Counter()
    for a, b in es:
        x, y = kf(a), kf(b)
        if x != y:
            w[(x, y)] += 1
    return w


for label, es in (("all-edges", [(a, b) for a, b, _ in edges]), ("policy-clean", clean)):
    for g, kf in (("sub", sub), ("unit", unit)):
        w = collapse(es, kf)
        ks = sorted({kf(n) for n in nodes})
        cyc = sorted([s for s in tarjan(ks, sorted(w)) if len(s) > 1], key=len, reverse=True)
        R.append("[%s][%s] nodes=%d cyclic SCCs=%d sizes=%s" % (label, g, len(ks), len(cyc), [len(s) for s in cyc][:12]))
        for s in cyc[:8]:
            mods = collections.Counter(top(x + (".h" if g == "unit" else "/x")) for x in s)
            ln = sum(lines_of[n] for n in nodes if kf(n) in set(s))
            R.append("    size=%d lines=%d modules=%s%s" % (len(s), ln, mods.most_common(),
                     ("" if g == "unit" else " members=" + ",".join(m.split("/", 2)[2] if m.count("/") >= 2 else m for m in s))))

# per-module submodule SCC (policy-clean) and member counts
w = collapse(clean, sub)
mods = sorted({top(n) for n in nodes if n.startswith("src/")})
R.append("per-module submodule structure (policy-clean graph):")
for m in mods:
    ks = sorted({sub(n) for n in nodes if top(n) == m})
    ew = [(a, b) for (a, b) in w if a in ks and b in ks]
    cyc = sorted([s for s in tarjan(ks, sorted(ew)) if len(s) > 1], key=len, reverse=True)
    R.append("  %-16s submodules=%3d  largest submodule SCC=%3d  (cyclic SCCs: %s)" % (
        m, len(ks), len(cyc[0]) if cyc else 1, [len(s) for s in cyc]))

# unit sizes
units = collections.defaultdict(list)
for n in nodes:
    units[unit(n)].append(n)
GEN = set()
with open(os.path.join(U, "generated_checked_in.txt")) as f:
    for line in f:
        if line.strip() and not line.startswith("#"):
            GEN.add(line.split("\t")[0].strip())
DATA = set()
with open(os.path.join(U, "embedded_data_files.txt")) as f:
    for line in f:
        if line.strip() and not line.startswith("#"):
            DATA.add(line.split("\t")[0].strip())


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def summarize(name, us):
    sz = [sum(lines_of[f] for f in fs) for fs in us.values()]
    R.append("%s: units=%d lines=%d median=%d p75=%d p90=%d p99=%d max=%d >2000=%d >5000=%d" % (
        name, len(sz), sum(sz), statistics.median(sz), pct(sz, .75), pct(sz, .90), pct(sz, .99), max(sz),
        sum(1 for x in sz if x > 2000), sum(1 for x in sz if x > 5000)))
    return sz


src_units = {k: v for k, v in units.items() if k.startswith("src/")}
summarize("ALL src stem-units (dir+stem)", src_units)
hand = {k: [f for f in v if f not in GEN and f not in DATA and not f.endswith((".y", ".l"))] for k, v in src_units.items()}
hand = {k: v for k, v in hand.items() if v}
summarize("HAND-WRITTEN src stem-units (excl. checked-in generated, embedded-data, .y/.l)", hand)
kinds = collections.Counter()
for k, fs in hand.items():
    exts = {os.path.splitext(f)[1] for f in fs}
    has_h = bool(exts & {".h", ".hpp"})
    has_c = bool(exts & {".cpp", ".cc", ".c"})
    kinds["h+impl" if has_h and has_c else "header-only" if has_h else "impl-only" if has_c else "other(" + ",".join(sorted(exts)) + ")"] += 1
R.append("hand-written unit composition=%s" % dict(kinds))
for m in mods:
    mu = {k: v for k, v in hand.items() if top(v[0]) == m}
    if mu:
        sz = [sum(lines_of[f] for f in fs) for fs in mu.values()]
        R.append("  %-16s units=%5d lines=%7d median=%4d p90=%5d max=%6d" % (m, len(sz), sum(sz), statistics.median(sz), pct(sz, .9), max(sz)))
# directory units
dirs = collections.defaultdict(int)
for k, fs in hand.items():
    for f in fs:
        dirs[os.path.dirname(f)] += lines_of[f]
dsz = list(dirs.values())
R.append("directory units (hand-written, src): dirs=%d median=%d p90=%d max=%d" % (
    len(dsz), statistics.median(dsz), pct(dsz, .9), max(dsz)))
# largest hand-written units
big = sorted(((sum(lines_of[f] for f in fs), k) for k, fs in hand.items()), reverse=True)[:12]
R.append("largest hand-written units=%s" % big)

# header-level DAG (file SCC) on hand-written headers: batches
with open(os.path.join(U, "analysis2.txt"), "w") as f:
    f.write("\n".join(R) + "\n")
print("\n".join(R))
