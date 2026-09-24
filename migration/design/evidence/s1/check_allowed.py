#!/usr/bin/env python3
"""Check crate edges against ARCHITECTURE.md 1.1's 'May also use' lists.
usage: check_allowed.py crates.tsv order.txt allowed.tsv [link_edges.json]
Include edges come from out/graph.json (R01's incgraph.py). A crate numbered 7 or later (ob-runtime on),
except sql-nio, may also use ob-errno and ob-base. Prints per pair: upward, downward-not-listed."""
import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
g = json.load(open(os.path.join(HERE, 'out/graph.json')))
live = set(g['live']); lines = g['lines']
dirs = set()
for f in lines:
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
def crate_of(f):
    for pre, c in rules:
        if f == pre or f.startswith(pre + '/') or (pre not in dirs and f.startswith(pre)):
            return c
    return 'UNASSIGNED'
order = [l.strip() for l in open(sys.argv[2]) if l.strip()]
idx = {c: i for i, c in enumerate(order)}
allowed = {}
for line in open(sys.argv[3]):
    a, _, rest = line.rstrip('\n').partition('\t')
    s = set(rest.split())
    if idx[a] >= idx['ob-runtime'] and a != 'sql-nio':
        s |= {'ob-errno', 'ob-base'}
    allowed[a] = s
def classify(a, b):
    if a.startswith('x-') or b.startswith('x-') or 'standby' in (a, b) or 'UNASSIGNED' in (a, b) or 'UNKNOWN' in (a, b):
        return 'ignored'
    if idx[b] > idx[a]:
        return 'upward'
    return 'listed' if b in allowed.get(a, set()) else 'not-listed'
inc = collections.Counter(); incx = collections.defaultdict(collections.Counter)
for x, y, n in g['edges']:
    if x in live and y in live:
        a, b = crate_of(x), crate_of(y)
        if a != b:
            inc[(a, b)] += n; incx[(a, b)][y] += n
def report(title, E, ex=None):
    tot = collections.Counter(); pairs = collections.Counter()
    for (a, b), n in E.items():
        k = classify(a, b); tot[k] += n; pairs[k] += 1
    print(f'== {title}: ' + ', '.join(f'{k} {tot[k]} in {pairs[k]} pairs' for k in ('upward', 'not-listed', 'listed', 'ignored')))
    for (a, b), n in sorted(E.items(), key=lambda t: -t[1]):
        if classify(a, b) == 'not-listed':
            tops = ''
            if ex:
                tops = ', '.join(f'{h}({k})' for h, k in ex[(a, b)].most_common(3))
            print(f'   not-listed {n:5} {a} -> {b}  {tops}')
report('include lines', inc, incx)
if len(sys.argv) > 4:
    le = json.load(open(sys.argv[4]))
    L = collections.Counter({(a, b): n for a, b, n in le['edges']})
    report('link symbols', L)
