#!/usr/bin/env python3
"""Which include root resolves each quoted include (first hit, CMake order).
Usage: python3 -I root_usage.py REPO"""
import os, re, sys, collections
repo = sys.argv[1]
G = ["rust/sql-nio/include", ".", "src", "src/query/api", "src/data_plane/api", "src/objit/include",
     "src/oblib/easy", "src/oblib", "src/oblib/common", "src/oblib/easy/include"]
O = ["rust/sql-nio/include", "src/oblib/easy", "src/oblib", "src/oblib/common", "src/oblib/easy/include"]
INC = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
files = set()
for dp, dns, fns in os.walk(repo):
    dns[:] = [d for d in dns if d != ".git"]
    for fn in fns:
        files.add(os.path.relpath(os.path.join(dp, fn), repo).replace(os.sep, "/"))
c = collections.Counter()
for rel in files:
    if not rel.startswith("src/") or os.path.splitext(rel)[1] not in {".c", ".h", ".cc", ".cpp", ".hpp", ".ipp", ".y", ".l"}:
        continue
    roots = O if rel.startswith("src/oblib/") else G
    for inc in INC.findall(open(os.path.join(repo, rel), encoding="utf-8", errors="replace").read()):
        hit = None
        p = os.path.normpath(os.path.join(os.path.dirname(rel), inc)).replace(os.sep, "/")
        if p in files:
            hit = "(including file's dir)"
        else:
            for r in roots:
                p = os.path.normpath(os.path.join(r, inc)).replace(os.sep, "/")
                if p in files:
                    hit = r; break
        c[hit or "(unresolved)"] += 1
tot = sum(c.values())
for k, v in c.most_common():
    print("%-26s %6d  %5.1f%%" % (k, v, 100.0 * v / tot))
print("total quoted includes (raw regex, comments not stripped):", tot)
