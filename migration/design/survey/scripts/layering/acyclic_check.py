"""Check the crate graph (include + link edges from union_edges.tsv) for cycles under fix scenarios.
Usage: python3 -I acyclic_check.py <repo>"""
import os, sys
repo = os.path.abspath(sys.argv[1])
E = []
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/crates/union_edges.tsv"))):
    if i == 0:
        continue
    f = l.rstrip("\n").split("\t")
    a, b, inc, link = f[0][4:], f[1][4:], int(f[2] or 0), f[3]
    E.append((a, b, inc, link))


def sccs(nodes, edges):
    idx, low, st, on, res, c = {}, {}, [], set(), [], [0]
    adj = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)

    def dfs(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for w in adj[v]:
            if w not in idx:
                dfs(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            res.append(comp)
    for n in nodes:
        if n not in idx:
            dfs(n)
    return [s for s in res if len(s) > 1]


def run(name, merge=None, drop=set(), keep_link_only_if_include=False):
    merge = merge or {}
    edges = set()
    for a, b, inc, link in E:
        a2, b2 = merge.get(a, a), merge.get(b, b)
        if a2 == b2:
            continue
        if (a, b) in drop and inc == 0:
            continue
        edges.add((a2, b2))
    nodes = sorted({x for e in edges for x in e})
    print("%-60s cycles: %s" % (name, sccs(nodes, edges) or "none"))


AGAINST = {("oblib", "observer"), ("oblib", "share"), ("oblib", "storage"), ("pl", "observer"), ("rootserver", "observer"),
           ("share", "observer"), ("sql", "observer"), ("sql", "pl"), ("sql", "rootserver"), ("storage", "observer"),
           ("storage", "rootserver")}
run("as is (include + link)")
run("drop 12 against edges only", drop=AGAINST)
run("drop against + storage->sql (keep sql->storage)", drop=AGAINST | {("storage", "sql")})
run("drop against + sql->storage + pl->storage (keep storage->sql)", drop=AGAINST | {("sql", "storage"), ("pl", "storage")})
run("drop against + both sql<->storage + pl->storage", drop=AGAINST | {("sql", "storage"), ("storage", "sql"), ("pl", "storage")})
run("merge pl into sql; drop other against + sql<->storage", merge={"pl": "sql"},
    drop=(AGAINST - {("sql", "pl")}) | {("sql", "storage"), ("storage", "sql"), ("pl", "storage")})
