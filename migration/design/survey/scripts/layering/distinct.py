"""Distinct classes and free functions over against/across symbols, excluding the sql<->pl edge.
Usage: python3 -I distinct.py <classified.tsv>"""
import sys
rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[1])) if i]
cls_all, fn_all, cls_nopl, fn_nopl = set(), set(), set(), set()
for r in rows:
    if r[5] != "-":
        cls_all.add(r[5])
        if not (r[0] == "src/sql" and r[1] == "src/pl"):
            cls_nopl.add(r[5])
    else:
        fn_all.add(r[3] + "::" + r[4])
        if not (r[0] == "src/sql" and r[1] == "src/pl"):
            fn_nopl.add(r[3] + "::" + r[4])
print("all edges: classes", len(cls_all), "free functions", len(fn_all))
print("excluding sql->pl: classes", len(cls_nopl), "free functions", len(fn_nopl))
