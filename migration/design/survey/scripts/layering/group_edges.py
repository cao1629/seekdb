"""Group classified edge symbols by (declaring header, class/function) per edge, with def files and
referencing-file counts. Usage: python3 -I group_edges.py <classified.tsv> <from> <to>"""
import collections, sys
path, fr, to = sys.argv[1], sys.argv[2], sys.argv[3]
groups = collections.OrderedDict()
for i, line in enumerate(open(path, encoding="utf-8")):
    if i == 0:
        continue
    r = line.rstrip("\n").split("\t")
    if r[0] != fr or r[1] != to:
        continue
    decl = r[6].split(";")[0] if r[6] != "?" else "?"
    key = (decl, r[5] if r[5] != "-" else r[3] + "::" + r[4])
    g = groups.setdefault(key, {"n": 0, "defs": set(), "refs": set(), "names": []})
    g["n"] += 1
    g["defs"].update(x for x in r[8].split(";") if x and x != "?")
    g["refs"].update(x for x in r[10].split(";") if x)
    g["names"].append(r[4])
by_header = collections.Counter()
for (decl, cls), g in groups.items():
    by_header[decl] += g["n"]
print("== %s -> %s : %d symbols, %d groups" % (fr, to, sum(g["n"] for g in groups.values()), len(groups)))
for (decl, cls), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
    print("%3d  %-60s %-45s def=%s refs~%d" % (g["n"], decl, cls[:45], ",".join(sorted(g["defs"]))[:120], len(g["refs"])))
