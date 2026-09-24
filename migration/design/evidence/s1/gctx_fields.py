#!/usr/bin/env python3
import collections, json, os, re, subprocess, sys
REPO = '/Users/colin/seekdb-dev/migrate-to-rust'
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
pat = sys.argv[2]
out = subprocess.run(['git', '-C', REPO, 'grep', '-o', '-P', pat, '--', 'src/*.h', 'src/*.cpp', 'src/*.ipp'], capture_output=True, text=True).stdout
cnt = collections.defaultdict(collections.Counter)
for line in out.splitlines():
    f, _, m = line.partition(':')
    cnt[m][crate_of(f)] += 1
for m, c in sorted(cnt.items(), key=lambda t: -sum(t[1].values())):
    print(f'{sum(c.values()):5} {m:40} ' + ' '.join(f'{k}={v}' for k, v in c.most_common(8)))
