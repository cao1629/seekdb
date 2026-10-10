#!/usr/bin/env python3
"""Size inventory per module/submodule. Usage: python3 -I size_inventory.py REPO OUTDIR"""
import os, sys, csv, collections
repo, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
EXTS = [".cpp", ".h", ".ipp", ".inc", ".c", ".cc", ".hpp", ".def", ".y", ".l"]
BIG = {"sql", "storage", "share", "observer", "oblib"}

def count_lines(p):
    with open(p, "rb") as f:
        return f.read().count(b"\n")

rows = []
for dp, dns, fns in os.walk(repo):
    dns[:] = sorted(d for d in dns if d != ".git")
    for fn in sorted(fns):
        ext = os.path.splitext(fn)[1]
        if ext not in EXTS:
            continue
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, repo).replace(os.sep, "/")
        rows.append((rel, ext, count_lines(p)))

def module_of(rel):
    parts = rel.split("/")
    if parts[0] == "src" and len(parts) > 2:
        return "src/" + parts[1]
    if parts[0] in ("tools", "deps") and len(parts) > 2:
        return parts[0] + "/" + parts[1]
    return parts[0] if len(parts) > 1 else "(repo root)"

def submodule_of(rel):
    parts = rel.split("/")
    if parts[0] == "src" and len(parts) > 2 and parts[1] in BIG:
        return "src/%s/%s" % (parts[1], parts[2] if len(parts) > 3 else "(files at module root)")
    return None

with open(os.path.join(out, "all_files.tsv"), "w") as f:
    f.write("path\text\tlines\n")
    for r in rows:
        f.write("%s\t%s\t%d\n" % r)

def table(keyfn, name):
    agg = collections.defaultdict(lambda: collections.Counter())
    for rel, ext, n in rows:
        k = keyfn(rel)
        if k is None:
            continue
        agg[k]["f" + ext] += 1
        agg[k]["l" + ext] += n
        agg[k]["files"] += 1
        agg[k]["lines"] += n
    keys = sorted(agg, key=lambda k: -agg[k]["lines"])
    with open(os.path.join(out, name), "w") as f:
        hdr = ["module", "files", "lines"] + [x for e in EXTS for x in ("n" + e, "lines" + e)]
        f.write("\t".join(hdr) + "\n")
        for k in keys:
            c = agg[k]
            f.write("\t".join([k, str(c["files"]), str(c["lines"])] +
                              [str(c[p + e]) for e in EXTS for p in ("f", "l")]) + "\n")
    return agg, keys

table(module_of, "size_by_module.tsv")
table(submodule_of, "size_by_submodule.tsv")
top = sorted(rows, key=lambda r: -r[2])[:40]
with open(os.path.join(out, "top_files.tsv"), "w") as f:
    for r in top:
        f.write("%s\t%s\t%d\n" % r)
tot = collections.Counter()
for rel, ext, n in rows:
    tot["f" + ext] += 1; tot["l" + ext] += n
print("totals:", {e: (tot["f" + e], tot["l" + e]) for e in EXTS})
