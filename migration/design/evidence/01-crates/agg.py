import json, sys, collections, os
sys.path.insert(0, os.path.dirname(__file__))
from incgraph import category
g = json.load(open('out/graph.json'))
lines = g['lines']; live = set(g['live'])
def agg(depth_rules):
    pass
tot = collections.Counter(); hand = collections.Counter(); nf = collections.Counter()
def key(f, depth):
    p = f.split('/')
    return '/'.join(p[:depth]) if len(p) > depth else os.path.dirname(f) + '/(files)'
prefixes = sys.argv[1:]
for pre in prefixes:
    pre, depth = pre.split(':')
    depth = int(depth)
    t = collections.Counter(); h = collections.Counter(); n = collections.Counter()
    for f in live:
        if f.startswith(pre + '/'):
            k = key(f, depth)
            t[k] += lines[f]; n[k] += 1
            if category(f) == 'hand': h[k] += lines[f]
    print(f'== {pre} total {sum(t.values())} hand {sum(h.values())} files {sum(n.values())}')
    for k in sorted(t, key=lambda k: -t[k]):
        print(f'{t[k]:>9} {h[k]:>9} {n[k]:>5}  {k}')
