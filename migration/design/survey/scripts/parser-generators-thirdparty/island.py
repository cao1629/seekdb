#!/usr/bin/env python3
"""Forward include closure ("island") of a seed set of files over migration/depmap/edges.tsv.
Usage: python3 -I island.py REPO SEEDFILE [STOP_PREFIX ...]
SEEDFILE: one path per line. STOP_PREFIX: paths starting with these are counted but not expanded.
Prints file/line totals of the closure, grouped by crate (from nodes.tsv).
"""
import os, sys, collections
repo, seedfile = sys.argv[1], sys.argv[2]
stops = tuple(sys.argv[3:])
fwd = collections.defaultdict(set)
for line in open(os.path.join(repo, 'migration/depmap/edges.tsv')):
    a, b, *_ = line.rstrip('\n').split('\t')
    if a != 'from':
        fwd[a].add(b)
lines, crate = {}, {}
for line in open(os.path.join(repo, 'migration/depmap/nodes.tsv')):
    p, n, c, u = line.rstrip('\n').split('\t')
    if p != 'path':
        lines[p] = int(n); crate[p] = c
seeds = [l.strip() for l in open(seedfile) if l.strip()]
# also pull the .cpp/.ipp companions of headers in the closure? No: a header-only closure is what the seeds need to compile.
seen = set(seeds); stack = list(seeds)
while stack:
    x = stack.pop()
    if stops and x.startswith(stops) and x not in seeds:
        continue
    for y in fwd.get(x, ()):
        if y not in seen:
            seen.add(y); stack.append(y)
bycrate = collections.Counter(); bylines = collections.Counter()
for f in seen:
    c = crate.get(f, '?').replace('src/', '')
    bycrate[c] += 1; bylines[c] += lines.get(f, 0)
print(f'seeds={len(seeds)} seed_lines={sum(lines.get(s,0) for s in seeds)} closure_files={len(seen)} closure_lines={sum(lines.get(f,0) for f in seen)}')
for c, n in bycrate.most_common():
    print(f'  {c:12s} files={n:5d} lines={bylines[c]:7d}')
