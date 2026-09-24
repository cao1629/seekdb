#!/usr/bin/env python3
"""Crate-level view of the 834bbee1e include graph (out/graph.json from incgraph.py).

usage: crate_analysis.py crates.tsv order.txt [graph.json]
  crates.tsv: prefix<TAB>crate; longest prefix wins; a prefix that is not a directory
              matches file names that start with it.
  order.txt:  crates bottom to top, one per line (a crate may depend only on crates above it
              in this file... i.e. listed earlier).
Prints crate sizes, SCCs, and every include edge that goes from a lower crate to a higher one,
grouped by crate pair and by target header.
"""
import collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from incgraph import category, tarjan

crates_tsv, order_txt = sys.argv[1], sys.argv[2]
gpath = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out/graph.json')
g = json.load(open(gpath))
lines = g['lines']; live = set(g['live'])
dirs = set()
for f in live:
    p = f.split('/')
    for i in range(1, len(p)):
        dirs.add('/'.join(p[:i]))
rules = []
for line in open(crates_tsv):
    line = line.split('#')[0].rstrip()
    if not line:
        continue
    pre, crate = line.split('\t')[:2]
    rules.append((pre.rstrip('/'), crate.strip()))
rules.sort(key=lambda r: -len(r[0]))


def crate_of(f):
    for pre, c in rules:
        if f == pre or f.startswith(pre + '/'):
            return c
        if pre not in dirs and f.startswith(pre):
            return c
    return 'UNASSIGNED'


order = [l.strip() for l in open(order_txt) if l.strip() and not l.startswith('#')]
idx = {c: i for i, c in enumerate(order)}
cmap = {f: crate_of(f) for f in live}
size = collections.Counter(); hand = collections.Counter(); nfiles = collections.Counter()
for f in live:
    size[cmap[f]] += lines[f]; nfiles[cmap[f]] += 1
    if category(f) == 'hand':
        hand[cmap[f]] += lines[f]
un = [f for f in live if cmap[f] == 'UNASSIGNED']
print('unassigned files', len(un), sorted({os.path.dirname(f) for f in un})[:20])
missing = [c for c in size if c not in idx]
print('crates not in order file', missing)
E = collections.Counter(); ex = collections.defaultdict(collections.Counter)
for x, y, n in g['edges']:
    if x in live and y in live and cmap[x] != cmap[y]:
        E[(cmap[x], cmap[y])] += n
        ex[(cmap[x], cmap[y])][y] += n
adj = collections.defaultdict(set)
for (a, b) in E:
    adj[a].add(b)
sccs = [c for c in tarjan(sorted(size), adj) if len(c) > 1]
print('\ncrate  all-lines  hand-lines  files  deps(lower crates it includes)')
for c in order:
    if c in size:
        deps = sorted({b for (a, b) in E if a == c and idx.get(b, 999) < idx[c]}, key=lambda b: idx.get(b, 999))
        print(f'{c:18} {size[c]:>9} {hand[c]:>9} {nfiles[c]:>5}  ' + ' '.join(deps))
print('\ncrates', len([c for c in size if not c.startswith('x-')]), 'total hand', sum(hand.values()))
print('crate SCCs', [sorted(c) for c in sccs])
back = [(a, b, n) for (a, b), n in E.items() if idx.get(b, 999) > idx.get(a, 999)]
back.sort(key=lambda t: (-t[2]))
print('\nback edges (lower crate includes a higher one):', len(back), 'pairs,', sum(n for _, _, n in back), 'include lines')
for a, b, n in back:
    tops = ex[(a, b)].most_common(6)
    print(f'  {n:5} {a} -> {b}: ' + ', '.join(f'{os.path.relpath(h, "src") if h.startswith("src/") else h}({k})' for h, k in tops)
          + (f' ... {len(ex[(a, b)])} headers' if len(ex[(a, b)]) > 6 else ''))
json.dump({'crate_of': cmap, 'size': size, 'hand': hand, 'edges': [[a, b, n] for (a, b), n in E.items()]},
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out/crate_view.json'), 'w'))
