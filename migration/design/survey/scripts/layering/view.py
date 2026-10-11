"""Filter classified.tsv rows. Usage: python3 -I view.py <classified.tsv> <from|*> <to|*> [decl_module_filter|*] [--not-api]"""
import sys
path, fr, to = sys.argv[1], sys.argv[2], sys.argv[3]
dm = sys.argv[4] if len(sys.argv) > 4 else "*"
not_api = "--not-api" in sys.argv
for i, line in enumerate(open(path, encoding="utf-8")):
    if i == 0:
        continue
    r = line.rstrip("\n").split("\t")
    if fr != "*" and r[0] != fr:
        continue
    if to != "*" and r[1] != to:
        continue
    if dm != "*" and r[7] != dm:
        continue
    if not_api and r[7] in ("query/api", "data_plane/api"):
        continue
    print("%s->%s | %s::%s | decl=%s | def=%s | nref=%s | %s" % (r[0][4:], r[1][4:], r[3], r[4], r[6], r[8], r[9], r[10]))
