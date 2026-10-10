# Heuristic class/struct parser over the comment/string-stripped mirror.
# Finds class/struct definitions, their base lists, whether they declare virtual/override members
# (directly at body depth 1, or via VIRTUAL_TO_STRING_KV / OB_UNIS_VERSION_V / DECLARE_VIRTUAL_TO_STRING
# style macros), builds an inheritance graph (bases keyed by unqualified, de-templated name), and reports
# polymorphic class counts, biggest hierarchies, multiple inheritance, CRTP.
# Usage: python3 -I classes.py <mirror_root> [root names...]
import os, re, sys, collections, json

root = sys.argv[1]
named_roots = sys.argv[2:]
KW = re.compile(r'\b(class|struct)\s+((?:(?:alignas\s*\([^)]*\)|__attribute__\s*\(\([^)]*\)\)|[A-Z][A-Z0-9_]*)\s+)*)(\w+)\s*(final\s*)?(:(?!:)[^{;()]*(?:\([^)]*\)[^{;()]*)*)?\{')
VIRT_MACRO = re.compile(r'\b(VIRTUAL_TO_STRING_KV|DECLARE_VIRTUAL_TO_STRING|OB_UNIS_VERSION_V|OB_UNIS_VERSION_PV|INHERIT_TO_STRING_KV|OB_UNIS_VERSION_V2)\b')
VIRT = re.compile(r'\bvirtual\b|\boverride\b')

def match_brace(s, i):
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1

def depth1_text(body):
    # keep only text at brace depth 0 of body (i.e. depth 1 of class)
    out = []
    d = 0
    for c in body:
        if c == '{':
            d += 1
            continue
        if c == '}':
            d -= 1
            continue
        if d == 0:
            out.append(c)
    return ''.join(out)

def split_bases(clause):
    # clause: text after ':' up to '{'
    parts, depth, cur = [], 0, []
    for c in clause:
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        if c == ',' and depth == 0:
            parts.append(''.join(cur)); cur = []
        else:
            cur.append(c)
    if cur:
        parts.append(''.join(cur))
    res = []
    for p in parts:
        p = re.sub(r'\b(public|protected|private|virtual)\b', ' ', p).strip()
        if not p:
            continue
        full = p
        base = re.sub(r'<.*', '', p, flags=re.S).strip()
        base = base.split('::')[-1].strip()
        if re.match(r'^\w+$', base):
            res.append((base, full))
    return res

classes = {}          # key: (file, name, line) -> info
by_name = collections.defaultdict(list)
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding='utf-8', errors='replace').read()
        for m in KW.finditer(s):
            pre = s[max(0, m.start() - 12):m.start()]
            if re.search(r'\benum\s*$', pre):
                continue
            # skip 'template <class T>'-style false starts are excluded by regex; skip friend
            if re.search(r'\bfriend\s*$', pre):
                continue
            name = m.group(3)
            if name in ('public', 'private', 'protected'):
                continue
            ob = m.end() - 1
            cb = match_brace(s, ob)
            body = s[ob + 1:cb]
            d1 = depth1_text(body)
            nvirt = len(VIRT.findall(d1))
            nvm = len(VIRT_MACRO.findall(d1))
            bases = split_bases(m.group(5)[1:]) if m.group(5) else []
            line = s.count('\n', 0, m.start()) + 1
            info = dict(file=rel, name=name, line=line, kind=m.group(1), bases=bases,
                        virt=nvirt, virt_macro=nvm, final=bool(m.group(4)))
            classes[(rel, name, line)] = info
            by_name[name].append(info)

total = len(classes)
names = set(by_name)
direct_poly = set(n for n, lst in by_name.items() if any(i['virt'] or i['virt_macro'] for i in lst))
children = collections.defaultdict(set)
for info in classes.values():
    for b, full in info['bases']:
        children[b].add(info['name'])
# transitive polymorphic: derived from a polymorphic class
poly = set(direct_poly)
changed = True
while changed:
    changed = False
    for info in classes.values():
        if info['name'] in poly:
            continue
        if any(b in poly for b, _ in info['bases']):
            poly.add(info['name']); changed = True

def descendants(r):
    seen, st = set(), [r]
    while st:
        x = st.pop()
        for c in children.get(x, ()):
            if c not in seen and c != r:
                seen.add(c); st.append(c)
    return seen

mi = [i for i in classes.values() if len(i['bases']) >= 2]
mi_poly = [i for i in mi if sum(1 for b, _ in i['bases'] if b in poly) >= 2]
second = collections.Counter()
for i in mi:
    for b, _ in i['bases'][1:]:
        second[b] += 1
crtp = [i for i in classes.values() if any(re.search(r'<[^>]*\b' + re.escape(i['name']) + r'\b', full) for _, full in i['bases'])]
virtual_inh = 0
print('class/struct definitions parsed:', total, ' distinct names:', len(names))
print('  struct:', sum(1 for i in classes.values() if i['kind'] == 'struct'), ' class:', sum(1 for i in classes.values() if i['kind'] == 'class'))
print('definitions declaring virtual/override (or virtual-generating macro) at depth 1:', sum(1 for i in classes.values() if i['virt'] or i['virt_macro']))
print('  of which only via macro:', sum(1 for i in classes.values() if not i['virt'] and i['virt_macro']))
print('distinct names directly polymorphic:', len(direct_poly), '; incl. inheriting from polymorphic:', len(poly))
print('definitions with >=1 base:', sum(1 for i in classes.values() if i['bases']))
print('multiple inheritance (>=2 bases):', len(mi), '; with >=2 polymorphic bases:', len(mi_poly))
print('  top secondary bases:', second.most_common(15))
print('CRTP-like (own name in base template args):', len(crtp))
for c in crtp[:8]:
    print('   ', c['file'] + ':' + str(c['line']), c['name'], [f for _, f in c['bases']][:2])
# largest hierarchies by transitive descendants among roots (classes with no known parent among parsed)
sizes = []
for n in names | set(children):
    d = descendants(n)
    if len(d) >= 15:
        sizes.append((len(d), len(children.get(n, ())), n))
sizes.sort(reverse=True)
print('largest hierarchies (transitive descendants, direct children, root):')
shown = set()
for s, dch, n in sizes[:40]:
    print('  ', s, dch, n)
print('named roots:')
for r in named_roots:
    d = descendants(r)
    defs = by_name.get(r, [])
    loc = defs[0]['file'] + ':' + str(defs[0]['line']) if defs else '?'
    print('  %-28s direct=%-4d transitive=%-4d def=%s' % (r, len(children.get(r, ())), len(d), loc))
json.dump({'poly': sorted(poly), 'direct_poly': sorted(direct_poly)}, open(os.path.join(os.path.dirname(root.rstrip('/')), 'poly.json'), 'w'))
# virtual bases
vb = 0
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        s = open(os.path.join(dp, f), encoding='utf-8', errors='replace').read()
        vb += len(re.findall(r'\b(class|struct)\s+\w+[^;{]*:\s*[^;{]*\bvirtual\s+(public|protected|private)\b|\b(public|protected|private)\s+virtual\s+\w', s))
print('virtual inheritance occurrences:', vb)
