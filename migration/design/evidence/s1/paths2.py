#!/usr/bin/env python3
import collections, json, os, sys, re
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
targets = {}; lines_by_unit = {}
for u, files, lines, ck, ib in units:
    if ib != 'yes': continue
    fl = files.split(',')
    cpp = [f for f in fl if f.rsplit('.',1)[0] == u and f.endswith(('.cpp','.cc','.c'))]
    src = cpp[0] if cpp else fl[0]
    pre, c = match(src)
    if c.startswith('x-') or c == 'UNASSIGNED': continue
    d, stem = os.path.dirname(u), os.path.basename(u)
    pdir = pre if pre in dirs else os.path.dirname(pre)
    rel = '' if d == pdir else os.path.relpath(d, pdir)
    t = os.path.join(c, rel, stem)
    targets[u] = (t, src, c)
    lines_by_unit[u] = int(lines)
# stem vs directory at same path
tset = collections.Counter(t for t, _, _ in targets.values())
dset = set()
for t, _, _ in targets.values():
    p = t.split('/')
    for i in range(2, len(p)):
        dset.add('/'.join(p[:i]))
clash = sorted(t for t in tset if t in dset)
print('stem targets that are also directories:', len(clash), clash[:20])
special = [ (u, t) for u, (t, s, c) in targets.items() if os.path.basename(t) in ('lib', 'main', 'mod', 'build')]
print('stems named lib/main/mod/build:', special)
bad = collections.Counter()
for t, _, _ in targets.values():
    for comp in t.split('/')[1:]:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', comp): bad[comp] += 1
print('non-identifier path components', dict(bad))
# ASCII case clash (macOS case-insensitive filesystem)
low = collections.defaultdict(set)
for t in list(tset) + list(dset):
    low[t.lower()].add(t)
print('case-insensitive clashes', [v for v in low.values() if len(v) > 1][:10])
big = [(u, n) for u, n in lines_by_unit.items() if n >= 4000]
print('units >= 4000 lines:', len(big))
print('depth histogram', collections.Counter(t.count('/') for t, _, _ in targets.values()))
json.dump({u: list(v) for u, v in targets.items()}, open('out/targets-ruleA.json', 'w'))
