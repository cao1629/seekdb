import sys, os
root = "/Users/colin/seekdb-dev/migrate-to-rust/"
for spec in sys.argv[1:]:
    f, rng = spec.rsplit(":", 1)
    a, _, b = rng.partition("-")
    a = int(a); b = int(b) if b else a
    p = f if f.startswith("/") else root + f
    try:
        lines = open(p, encoding="utf-8", errors="replace").read().split("\n")
    except Exception as e:
        print("== %s: %s" % (spec, e)); continue
    print("== %s" % spec)
    for i in range(a, min(b, len(lines)) + 1):
        print("%6d  %s" % (i, lines[i-1][:200]))
