"""Probe a sql split into {front = parser,resolver,rewrite,optimizer,code_generator,privilege_check,printer,
plan_cache,(root),monitor,pl,hybrid_search,session,executor,tablelock} and {engine,das,dtl}: which engine/das/dtl
headers do the front files include, and how big is the include closure of those headers inside sql
(the part that would have to move into a lower 'sql-core' crate).
Usage: python3 -I split_probe.py <repo>"""
import collections, os, sys
repo = os.path.abspath(sys.argv[1])
lines = {}
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/nodes.tsv"))):
    if i:
        p, n, c, u = l.rstrip("\n").split("\t")
        lines[p] = int(n)
out = collections.defaultdict(set)
for i, l in enumerate(open(os.path.join(repo, "migration/depmap/edges.tsv"))):
    if i:
        a, b, k, c, ln = l.rstrip("\n").split("\t")
        out[a].add(b)


def sub(p):
    s = p.split("/")
    return s[2] if len(s) > 3 else "(root)"


EXEC = {"engine", "das", "dtl"}
sql = [p for p in lines if p.startswith("src/sql/")]
front = [p for p in sql if sub(p) not in EXEC]
targets = collections.Counter()
for f in front:
    for t in out[f]:
        if t.startswith("src/sql/") and sub(t) in EXEC:
            targets[t] += 1
print("front files:", len(front), "lines:", sum(lines[p] for p in front))
print("distinct exec headers included by front:", len(targets), "edges:", sum(targets.values()))
for t, c in targets.most_common(12):
    print("   %3d %s" % (c, t))
# closure of all those targets within sql (headers only and their .cpp/.ipp companions excluded)
seen = set()
stack = list(targets)
while stack:
    x = stack.pop()
    if x in seen:
        continue
    seen.add(x)
    for y in out[x]:
        if y.startswith("src/sql/") and y not in seen:
            stack.append(y)
cl = [p for p in seen]
print("include closure of those headers inside sql: %d files, %d lines" % (len(cl), sum(lines[p] for p in cl)))
by = collections.Counter(sub(p) for p in cl)
print("   closure by subdir:", dict(by.most_common()))
cl_front = [p for p in cl if sub(p) not in EXEC]
print("   of which front-side files (would be dragged into core too): %d files, %d lines" % (len(cl_front), sum(lines[p] for p in cl_front)))
