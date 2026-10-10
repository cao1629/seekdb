#!/usr/bin/env python3
"""Find X-macro style includes: #include "f" with a function-like #define in the
preceding 8 lines and an #undef within the next 8 lines (or f is .def/.map).
Usage: python3 -I xmacro.py REPO"""
import os, re, sys, collections
repo = sys.argv[1]
ROOTS = ["rust/sql-nio/include", ".", "src", "src/query/api", "src/data_plane/api", "src/objit/include",
         "src/oblib/easy", "src/oblib", "src/oblib/common", "src/oblib/easy/include"]
INC = re.compile(r'^\s*#\s*include\s*"([^"]+)"')
DEF = re.compile(r'^\s*#\s*define\s+[A-Za-z_][A-Za-z0-9_]*\(')
UND = re.compile(r'^\s*#\s*undef\b')
files = set()
for dp, dns, fns in os.walk(repo):
    dns[:] = [d for d in dns if d != ".git"]
    for fn in fns:
        files.add(os.path.relpath(os.path.join(dp, fn), repo).replace(os.sep, "/"))
def resolve(rel, inc):
    for base in [os.path.dirname(rel)] + ROOTS:
        p = os.path.normpath(os.path.join(base, inc)).replace(os.sep, "/")
        if p in files:
            return p
    return None
sites = collections.defaultdict(list)
for rel in sorted(files):
    if not rel.startswith("src/") or os.path.splitext(rel)[1] not in {".c", ".h", ".cc", ".cpp", ".ipp", ".hpp"}:
        continue
    ls = open(os.path.join(repo, rel), encoding="utf-8", errors="replace").read().split("\n")
    for i, l in enumerate(ls):
        m = INC.match(l)
        if not m:
            continue
        tgt = resolve(rel, m.group(1))
        if not tgt:
            continue
        before = [x for x in ls[max(0, i - 8):i]]
        after = [x for x in ls[i + 1:i + 9]]
        xm = any(DEF.match(x) for x in before) and any(UND.match(x) for x in after)
        if xm or tgt.endswith((".def", ".map")):
            sites[tgt].append(rel)
def lines(r):
    with open(os.path.join(repo, r), "rb") as f:
        return f.read().count(b"\n")
tot = 0
for t in sorted(sites, key=lambda t: -lines(t)):
    n = lines(t); tot += n
    print("%-75s %6d lines, included as table by %d site(s), e.g. %s" % (t, n, len(sites[t]), sites[t][0]))
print("X-macro/table files: %d, lines: %d" % (len(sites), tot))
