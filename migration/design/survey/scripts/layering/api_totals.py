"""Totals over against/across symbols: declared in api headers vs elsewhere; methods vs free.
Usage: python3 -I api_totals.py <classified.tsv>"""
import collections, sys
rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[1])) if i]
ACROSS = {("src/pl", "src/storage"), ("src/sql", "src/storage"), ("src/storage", "src/sql")}
c = collections.Counter()
for r in rows:
    kind = "across" if (r[0], r[1]) in ACROSS else "against"
    api = "api" if ("query/api" in r[7] or "data_plane/api" in r[7]) else ("sql/pl" if "/sql/pl/" in r[6] else "other")
    shape = "method" if r[5] != "-" else "free"
    c[(kind, api)] += 1
    c[(kind, "ALL")] += 1
    c[(kind, shape)] += 1
for k in sorted(c):
    print(k, c[k])
