# Fast regex query over cached stripped node files.
# usage: python3 -I q.py '<regex>' [top=30] [keepstr]
#   prints total matches, files, and top files with first-match line.
import collections
import os
import re
import sys

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
ROOT_S = '/tmp/conv-survey-7d907abfa-38738/stripped_keepstr'
rx = re.compile(sys.argv[1], re.M)
top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
root = ROOT_S if (len(sys.argv) > 3 and sys.argv[3] == 'keepstr') else ROOT
per = collections.Counter()
first = {}
bydir = collections.Counter()
for dp, dn, fn in os.walk(root):
    for f in fn:
        p = os.path.join(dp, f)
        t = open(p, encoding='utf-8').read()
        ms = list(rx.finditer(t))
        if ms:
            rel = os.path.relpath(p, root)
            per[rel] = len(ms)
            first[rel] = t.count('\n', 0, ms[0].start()) + 1
            parts = rel.split('/')
            bydir['/'.join(parts[:2])] += len(ms)
print(f'total={sum(per.values())} files={len(per)}')
print('by module:', ', '.join(f'{d}={c}' for d, c in bydir.most_common()))
for f, c in per.most_common(top):
    print(f'{c:6d}  {f}:{first[f]}')
