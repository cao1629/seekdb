# List all transitive subclasses of ObIAllocator (and other roots) in the stripped mirror.
# Reuses the class regex of migration/feasibility/scripts/arch/classes.py.
# Usage: python3 -I alloc_classes.py <mirror_root> [root ...]
import os, re, sys, collections
root = sys.argv[1]
KW = re.compile(r'\b(class|struct)\s+((?:(?:alignas\s*\([^)]*\)|__attribute__\s*\(\([^)]*\)\)|[A-Z][A-Z0-9_]*)\s+)*)(\w+)\s*(final\s*)?(:(?!:)[^{;()]*(?:\([^)]*\)[^{;()]*)*)?\{')


def match_brace(s, i):
    d = 0
    for j in range(i, len(s)):
        if s[j] == '{':
            d += 1
        elif s[j] == '}':
            d -= 1
            if d == 0:
                return j
    return len(s) - 1


def split_bases(clause):
    parts, depth, cur = [], 0, []
    for c in clause:
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        if c == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append(''.join(cur))
    res = []
    for p in parts:
        p = re.sub(r'\b(public|protected|private|virtual)\b', ' ', p).strip()
        base = re.sub(r'<.*', '', p, flags=re.S).strip().split('::')[-1].strip()
        if re.match(r'^\w+$', base):
            res.append(base)
    return res


classes = []
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding='utf-8', errors='replace').read()
        for m in KW.finditer(s):
            pre = s[max(0, m.start() - 12):m.start()]
            if re.search(r'\b(enum|friend)\s*$', pre):
                continue
            name = m.group(3)
            if name in ('public', 'private', 'protected'):
                continue
            ob = m.end() - 1
            cb = match_brace(s, ob)
            body = s[ob + 1:cb]
            bases = split_bases(m.group(5)[1:]) if m.group(5) else []
            line = s.count('\n', 0, m.start()) + 1
            fm = re.search(r'\bvoid\s+free\s*\(\s*(const\s+)?void\s*\*\s*(\w+)?\s*\)\s*(override|final|\s)*\{', body)
            free_body = None
            if fm:
                fb0 = fm.end() - 1
                fb1 = match_brace(body, fb0)
                free_body = body[fb0 + 1:fb1].strip()
            classes.append(dict(file=rel, name=name, line=line, bases=bases, free_body=free_body))

children = collections.defaultdict(list)
for c in classes:
    for b in c['bases']:
        children[b].append(c)


def desc(r):
    seen, st, out = set(), [r], []
    while st:
        x = st.pop()
        for c in children.get(x, ()):
            k = (c['file'], c['name'], c['line'])
            if k not in seen:
                seen.add(k)
                out.append(c)
                st.append(c['name'])
    return out


for r in sys.argv[2:] or ['ObIAllocator']:
    d = desc(r)
    direct = [c for c in d if r in c['bases']]
    print('ROOT', r, 'direct', len(direct), 'transitive', len(d))
    mods = collections.Counter(c['file'].split('/')[1] for c in d)
    print('  by module:', dict(mods.most_common()))
    noop = []
    for c in sorted(d, key=lambda c: (c['file'], c['line'])):
        fb = c['free_body']
        tag = ''
        if fb is not None:
            norm = re.sub(r'\s+', '', fb)
            if re.fullmatch(r'(UNUSED\(\w+\);)*(return;)?', norm):
                tag = 'FREE_NOOP'
                noop.append(c)
        elif r in c['bases']:
            tag = '(no inline free)'
        print('  %s:%d %s : %s %s' % (c['file'], c['line'], c['name'], ','.join(c['bases']), tag))
    print('  free() no-op among them:', len(noop))
