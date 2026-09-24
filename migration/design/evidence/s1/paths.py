#!/usr/bin/env python3
"""Target paths for every map unit under candidate readings of ARCHITECTURE.md section 14 rule 1.
usage: paths.py crates.tsv units.tsv"""
import collections, json, os, sys
g = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out/graph.json')))
dirs = set()
for f in g['lines']:
    p = f.split('/')
    for i in range(1, len(p)):
        dirs.add('/'.join(p[:i]))
rules = []
for line in open(sys.argv[1]):
    line = line.split('#')[0].rstrip()
    if line:
        pre, c = line.split('\t')[:2]
        rules.append((pre.rstrip('/'), c.strip()))
rules.sort(key=lambda r: -len(r[0]))
def match(f):
    for pre, c in rules:
        if f == pre or f.startswith(pre + '/') or (pre not in dirs and f.startswith(pre)):
            return pre, c
    return None, 'UNASSIGNED'
units = [l.rstrip('\n').split('\t') for l in open(sys.argv[2])][1:]
res = collections.defaultdict(lambda: collections.defaultdict(list))
kw = {'as','break','const','continue','crate','else','enum','extern','false','fn','for','if','impl','in','let','loop','match','mod','move','mut','pub','ref','return','self','Self','static','struct','super','trait','true','type','unsafe','use','where','while','async','await','dyn','abstract','become','box','do','final','macro','override','priv','typeof','unsized','virtual','yield','try','gen'}
kwdirs = collections.Counter(); badstem = []
for u, files, lines, ck, ib in units:
    if ib != 'yes':
        continue
    pre, c = match(u + '.cpp') if any(f == u + '.cpp' for f in files.split(',')) else match(files.split(',')[0])
    if c.startswith('x-') or c == 'UNASSIGNED':
        continue
    d, stem = os.path.dirname(u), os.path.basename(u)
    if not stem.replace('_', 'a').isalnum() or stem[0].isdigit() or stem in kw:
        badstem.append(u)
    pdir = pre if pre in dirs else os.path.dirname(pre)
    rel = os.path.relpath(d, pdir) if d != pdir else ''
    rel = '' if rel == '.' else rel
    for comp in rel.split('/'):
        if comp in kw: kwdirs[comp] += 1
    last = os.path.basename(pdir)
    cand = {
        'A: below matched prefix': os.path.join(c, rel, stem),
        'B: prefix dir name + below': os.path.join(c, last, rel, stem),
        'C: full path below src': os.path.join(c, os.path.relpath(d, 'src') if d.startswith('src/') else d, stem),
    }
    for k, t in cand.items():
        res[k][t].append(u)
for k, m in res.items():
    coll = {t: us for t, us in m.items() if len(us) > 1}
    print(f'{k}: {len(m)} targets, {len(coll)} collide, {sum(len(v) for v in coll.values())} units in collisions')
    for t, us in list(coll.items())[:6]:
        print('    ', t, '<-', ', '.join(us))
print('keyword directory components', dict(kwdirs))
print('stems that are not identifiers or are keywords', badstem[:10], len(badstem))
