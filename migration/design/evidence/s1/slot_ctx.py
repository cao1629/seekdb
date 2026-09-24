#!/usr/bin/env python3
"""For every server_service<T>() lookup: the caller's crate, T's crate, the context that holds T
(the lowest of RuntimeContext/ob-runtime, StorageContext/storage-tablet, SqlContext/sql-exec,
ServerContext/observer at or above T's crate), and whether the caller's crate may name that context."""
import collections, json, re, subprocess, sys
root = '/Users/colin/seekdb-dev/migrate-to-rust'
g = json.load(open('out/graph.json'))
dirs = set()
for f in g['lines']:
    p = f.split('/')
    for i in range(1, len(p)): dirs.add('/'.join(p[:i]))
rules = []
for line in open(sys.argv[1]):
    line = line.split('#')[0].rstrip()
    if line:
        pre, c = line.split('\t')[:2]; rules.append((pre.rstrip('/'), c.strip()))
rules.sort(key=lambda r: -len(r[0]))
def crate_of(f):
    for pre, c in rules:
        if f == pre or f.startswith(pre + '/') or (pre not in dirs and f.startswith(pre)): return c
    return 'UNASSIGNED'
order = [l.strip() for l in open(sys.argv[2]) if l.strip()]
idx = {c: i for i, c in enumerate(order)}
allowed = {}
for line in open(sys.argv[3]):
    a, _, rest = line.rstrip('\n').partition('\t')
    s = set(rest.split())
    if idx[a] >= idx['ob-runtime'] and a != 'sql-nio': s |= {'ob-errno', 'ob-base'}
    allowed[a] = s | {a}
ctxs = [('RuntimeContext', 'ob-runtime'), ('StorageContext', 'storage-tablet'), ('SqlContext', 'sql-exec'), ('ServerContext', 'observer')]
files = [f for f in subprocess.run(['git', '-C', root, 'ls-files', 'src'], capture_output=True, text=True).stdout.split() if f.endswith(('.h', '.hpp', '.cpp', '.cc', '.ipp'))]
look = re.compile(r'(?<![a-z_])server_service<\s*([^;()]*?)>\s*\(\)')
decl = re.compile(r'^\s*(?:class|struct)\s+(?:[A-Z_]+\s+)?(\w+)\b[^;]*$', re.M)
where = collections.defaultdict(set); texts = {}
for f in files:
    s = open(f'{root}/{f}', encoding='utf-8', errors='replace').read(); texts[f] = s
    if f.endswith('.h'):
        for m in decl.finditer(s): where[m.group(1)].add(crate_of(f))
res = collections.Counter(); ex = collections.defaultdict(collections.Counter); unknown = collections.Counter()
for f, s in texts.items():
    cc = crate_of(f)
    for m in look.finditer(s):
        name = re.sub(r'\s+', '', m.group(1)).split('::')[-1]
        cs = [c for c in where.get(name, ()) if c in idx]
        if not cs: unknown[name] += 1; continue
        tc = min(cs, key=lambda c: idx[c])
        hold = next(((n, c) for n, c in ctxs if idx[c] >= idx[tc]), ('ServerContext', 'observer'))
        can = hold[1] in allowed.get(cc, set()) or cc == hold[1]
        k = 'reachable' if can else 'not reachable'
        res[k] += 1; ex[k][(cc, name, hold[0])] += 1
print(dict(res), 'unknown', sum(unknown.values()), unknown.most_common(5))
for (cc, name, h), n in ex['not reachable'].most_common(25): print(f'   {n:4} {cc:14} -> {name} (held by {h})')
agg = collections.Counter()
for (cc, name, h), n in ex['not reachable'].items(): agg[cc] += n
print('not reachable by caller crate', agg.most_common())
