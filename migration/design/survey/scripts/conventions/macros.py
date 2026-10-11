# Census of function-like macros that define types or function bodies, and their invocation counts.
# usage: python3 -I macros.py   (run from repo root; uses depmap node files + cached stripped copies)
import collections
import os
import re
import sys

sys.path.insert(0, '/tmp/conv-survey-7d907abfa-38738/scripts')
from common import node_files, read  # noqa: E402

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
DEF = re.compile(r'^[ \t]*#[ \t]*define[ \t]+(\w+)\(([^)]*)\)((?:[^\n]*\\\n)*[^\n]*)', re.M)
TYPE_DEF = re.compile(r'\b(?:class|struct|union)\s+(?:\w|##)+[^;]*?\{|\btemplate\s*<\s*>\s*struct|\benum\s+(?:class\s+)?(?:\w|##)*\s*\{')
FUNC_DEF = re.compile(r'(?:\b(?:int|void|bool|int64_t|uint64_t|int32_t|uint32_t|const\s+char\s*\*|double|ObString|auto)\s+[\w:#]+\s*\([^;{}]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?\{)')
defs = {}
for f in node_files():
    try:
        t = read(f)
    except FileNotFoundError:
        continue
    for m in DEF.finditer(t):
        name, args, body = m.groups()
        body = body.replace('\\\n', '\n')
        kind = None
        if TYPE_DEF.search(body):
            kind = 'type'
        elif FUNC_DEF.search(body):
            kind = 'func'
        if kind:
            line = t.count('\n', 0, m.start()) + 1
            defs.setdefault(name, (kind, f'{f}:{line}'))

names = list(defs)
uses = collections.Counter()
if names:
    # chunk the alternation to keep the regex manageable
    chunks = [names[i:i + 400] for i in range(0, len(names), 400)]
    rxs = [re.compile(r'(?<![\w#])(' + '|'.join(map(re.escape, c)) + r')\s*\(') for c in chunks]
    for dp, dn, fn in os.walk(ROOT):
        for fl in fn:
            t = open(os.path.join(dp, fl), encoding='utf-8').read()
            # drop #define lines so definitions are not counted as uses
            t = re.sub(r'^[ \t]*#[ \t]*define[^\n]*(?:\\\n[^\n]*)*', '', t, flags=re.M)
            for rx in rxs:
                for m in rx.finditer(t):
                    uses[m.group(1)] += 1

kinds = collections.Counter(k for k, _ in defs.values())
print('function-like macros defining types:', kinds['type'], ' defining functions:', kinds['func'])
tu = sum(uses[n] for n in names if defs[n][0] == 'type')
fu = sum(uses[n] for n in names if defs[n][0] == 'func')
print('invocation sites: type-defining', tu, ' function-defining', fu)
used = [(uses[n], n, defs[n][0], defs[n][1]) for n in names]
used.sort(reverse=True)
for u in used[:30]:
    print(f'{u[0]:6d} {u[1]} [{u[2]}] {u[3]}')
