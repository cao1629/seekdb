# Multi-pattern totals over cached stripped node files.
# usage: python3 -I mq.py <patterns.tsv>   (each line: name<TAB>regex)
import os
import re
import sys

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
pats = []
for line in open(sys.argv[1], encoding='utf-8'):
    line = line.rstrip('\n')
    if not line or line.startswith('#'):
        continue
    name, rx = line.split('\t', 1)
    pats.append((name, re.compile(rx, re.M)))
tot = {n: 0 for n, _ in pats}
files = {n: 0 for n, _ in pats}
texts = []
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        texts.append(open(os.path.join(dp, f), encoding='utf-8').read())
for n, rx in pats:
    for t in texts:
        c = len(rx.findall(t))
        if c:
            tot[n] += c
            files[n] += 1
for n, _ in pats:
    print(f'{tot[n]:8d} matches {files[n]:6d} files  {n}')
