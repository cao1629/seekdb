# Class-shape census over cached stripped node files: inheritance, multiple/virtual inheritance, CRTP, depth.
# Names are resolved by simple name (namespaces and template args dropped) -> depth numbers are estimates.
# usage: python3 -I classes.py [ClassName ...]   (prints transitive subclass counts for the given roots)
import collections
import os
import re
import sys

ROOT = '/tmp/conv-survey-7d907abfa-38738/stripped'
HEAD = re.compile(r'\b(class|struct)\s+(?:alignas\s*\([^)]*\)\s*)?(?:[A-Z_][A-Z0-9_]*\s+)?(\w+)\s*(?:final\s*)?(?:<[^;{]*?>\s*)?:\s*(?!:)([^;{]*)\{')
TEMPLATE_BEFORE = re.compile(r'template\s*<[^;{]*>\s*$')


def split_top(s):
    out, cur, d = [], '', 0
    for ch in s:
        if ch in '<(':
            d += 1
        elif ch in '>)':
            d -= 1
        if ch == ',' and d == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


def simple(name):
    n = re.sub(r'<.*', '', name, flags=re.S).strip()
    return n.split('::')[-1].strip()


bases = collections.defaultdict(set)
where = {}
n_with_base = 0
multi = []
virt = 0
crtp = []
template_classes = 0
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        p = os.path.join(dp, f)
        t = open(p, encoding='utf-8').read()
        rel = os.path.relpath(p, ROOT)
        for m in HEAD.finditer(t):
            kind, name, blist = m.groups()
            if name in ('public', 'private', 'protected'):
                continue
            items = split_top(blist)
            cleaned = []
            for it in items:
                v = 'virtual' in it.split('<')[0]
                it2 = re.sub(r'\b(public|private|protected|virtual)\b', '', it).strip()
                if not it2 or not re.match(r'[\w:]', it2):
                    continue
                if v:
                    virt += 1
                cleaned.append(it2)
                if re.search(r'<[^>]*\b' + re.escape(name) + r'\b', it2):
                    crtp.append((rel, t.count('\n', 0, m.start()) + 1, name, it2))
            if not cleaned:
                continue
            n_with_base += 1
            line = t.count('\n', 0, m.start()) + 1
            where.setdefault(name, f'{rel}:{line}')
            for b in cleaned:
                bases[name].add(simple(b))
            if len(cleaned) >= 2:
                multi.append((rel, line, name, cleaned))
            if TEMPLATE_BEFORE.search(t[max(0, m.start() - 300):m.start()]):
                template_classes += 1

children = collections.defaultdict(set)
for c, bs in bases.items():
    for b in bs:
        if b != c:
            children[b].add(c)

memo = {}
def depth(c, stack=()):
    if c in memo:
        return memo[c]
    if c in stack:
        return 0
    bs = [b for b in bases.get(c, ()) if b != c]
    d = 0 if not bs else 1 + max(depth(b, stack + (c,)) for b in bs)
    memo[c] = d
    return d

allc = set(bases) | set(children)
dist = collections.Counter(depth(c) for c in bases)
def desc(root):
    seen = set()
    st = [root]
    while st:
        x = st.pop()
        for y in children.get(x, ()):
            if y not in seen:
                seen.add(y)
                st.append(y)
    return seen

print('class/struct definitions with a base clause:', n_with_base)
print('distinct derived names:', len(bases))
print('depth distribution (derived classes):', sorted(dist.items()))
deep = sorted(bases, key=lambda c: -depth(c))[:8]
print('deepest:', [(c, depth(c), where.get(c)) for c in deep])
print('multiple inheritance (>=2 bases):', len(multi))
mb = collections.Counter()
for _, _, _, cl in multi:
    for b in cl:
        mb[simple(b)] += 1
print('  most common bases in MI:', mb.most_common(12))
print('virtual inheritance specifiers:', virt)
print('CRTP-like (base template arg names the derived class):', len(crtp))
for x in crtp[:6]:
    print('   ', f'{x[0]}:{x[1]}', x[2], ':', x[3][:80])
print('templated class definitions with a base clause:', template_classes)
for r in sys.argv[1:]:
    d = desc(r)
    print(f'transitive subclasses of {r}: {len(d)}')
