#!/usr/bin/env python3
"""Link-level edges between crates: an undefined symbol in one object resolved by a strong
(non-weak) definition in another, from `nm -m -g` over the reference build (nm/all.txt).

usage: linkgraph.py crates.tsv order.txt
Objects are mapped to member source files through compile_commands.json; an object whose
members fall in more than one crate is attributed to the crate holding most of its lines
(reported as mixed).
"""
import collections, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REF = '/Users/colin/seekdb-dev/ref-834bbee1e'
BUILD = REF + '/build_release'
GEN = BUILD + '/generated'
INC_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', re.M)

g = json.load(open(os.path.join(HERE, 'out/graph.json')))
lines = g['lines']
dirs = set()
for f in lines:
    p = f.split('/')
    for i in range(1, len(p)):
        dirs.add('/'.join(p[:i]))
rules = []
for line in open(sys.argv[1]):
    line = line.split('#')[0].rstrip()
    if line:
        pre, crate = line.split('\t')[:2]
        rules.append((pre.rstrip('/'), crate.strip()))
rules.sort(key=lambda r: -len(r[0]))
order = [l.strip() for l in open(sys.argv[2]) if l.strip()]
idx = {c: i for i, c in enumerate(order)}


def crate_of(f):
    for pre, c in rules:
        if f == pre or f.startswith(pre + '/') or (pre not in dirs and f.startswith(pre)):
            return c
    return 'UNASSIGNED'


def rel(p):
    if p.startswith(GEN + '/'):
        return 'gen/' + p[len(GEN) + 1:]
    if p.startswith(REF + '/'):
        return p[len(REF) + 1:]
    return p


cc = json.load(open(os.path.join(BUILD, 'compile_commands.json')))
obj_members = {}
for c in cc:
    f = c['file']
    out = c['output']
    if '/Unity/' in f:
        obj_members[out] = [rel(i) for i in INC_RE.findall(open(f).read())]
    else:
        obj_members[out] = [rel(f)]
obj_crate = {}; mixed = []
for o, ms in obj_members.items():
    w = collections.Counter()
    for m in ms:
        w[crate_of(m)] += lines.get(m, 1)
    obj_crate[o] = w.most_common(1)[0][0]
    if len(w) > 1:
        mixed.append((o, dict(w)))

defs = collections.defaultdict(list); undefs = collections.defaultdict(set); weak = set()
cur = None
for line in open(os.path.join(HERE, 'nm/all.txt')):
    if line.startswith('### '):
        cur = line[4:].strip(); continue
    parts = line.split()
    if not parts:
        continue
    if line.strip().startswith('(undefined)'):
        undefs[cur].add(parts[2]); continue
    sym = parts[-1]
    if 'weak' in line:
        weak.add(sym)
    else:
        defs[sym].append(cur)
# finer attribution inside objects whose members fall in several crates
import subprocess
mixed_objs = {o for o, _ in mixed}
need = set()
for s, ds in defs.items():
    if len(ds) == 1 and ds[0] in mixed_objs:
        need.add(s)
for o in mixed_objs:
    need |= undefs.get(o, set())
need = sorted(need)
dem = subprocess.run(['c++filt', '-_'], input='\n'.join(need), capture_output=True, text=True).stdout.split('\n')
dmap = dict(zip(need, dem))
text_cache = {}
def member_text(m):
    if m not in text_cache:
        p = os.path.join(GEN, m[4:]) if m.startswith('gen/') else os.path.join(REF, m)
        try:
            text_cache[m] = open(p, errors='replace').read()
        except OSError:
            text_cache[m] = ''
    return text_cache[m]
def key_names(d):
    d = d.replace('vtable for ', '').replace('typeinfo for ', '').replace('typeinfo name for ', '')
    d = d.split('(')[0]
    d = re.sub(r'<[^<>]*>', '', re.sub(r'<[^<>]*>', '', d))
    parts = [p for p in d.split('::') if p]
    return parts[-2:] if len(parts) >= 2 else parts
def attribute(o, sym, defining):
    ms = obj_members[o]
    names = key_names(dmap.get(sym, sym))
    if not names:
        return None
    if defining and len(names) == 2:
        pat = names[0] + '::' + names[1]
    else:
        pat = names[-1]
    hits = [m for m in ms if pat and (pat + '(' in member_text(m) or pat + ' (' in member_text(m) or (defining and 'class ' + pat in member_text(m)))]
    cr = {crate_of(m) for m in hits}
    return cr if cr else None
def_crate = {}
for s, ds in defs.items():
    if len(ds) == 1:
        o = ds[0]
        if o in mixed_objs:
            cr = attribute(o, s, True)
            def_crate[s] = sorted(cr)[0] if cr and len(cr) == 1 else obj_crate.get(o, 'UNKNOWN')
        else:
            def_crate[s] = obj_crate.get(o, 'UNKNOWN')
E = collections.Counter(); ex = collections.defaultdict(collections.Counter)
unknown_objs = set()
for o, syms in undefs.items():
    if o not in obj_crate:
        unknown_objs.add(o); continue
    for s in syms:
        b = def_crate.get(s)
        if b is None:
            continue
        if o in mixed_objs:
            cr = attribute(o, s, False) or {obj_crate[o]}
        else:
            cr = {obj_crate[o]}
        for a in cr:
            if a != b:
                E[(a, b)] += 1
                ex[(a, b)][s] += 1
print('objects', len(obj_members), 'mixed-crate objects', len(mixed), 'objects not in compile commands', len(unknown_objs))
for o, w in mixed:
    print('   mixed', o.split('/')[-1][:70], w)
back = [(a, b, n) for (a, b), n in E.items() if idx.get(b, 999) > idx.get(a, 999)]
back.sort(key=lambda t: -t[2])
print('\ncross-crate symbol edges', sum(E.values()), 'in', len(E), 'pairs; upward:', sum(n for *_, n in back), 'in', len(back), 'pairs')
demangle = {}
try:
    import subprocess
    allsyms = sorted({s for (a, b, n) in back for s in ex[(a, b)]})
    out = subprocess.run(['c++filt', '-_'], input='\n'.join(allsyms), capture_output=True, text=True).stdout.split('\n')
    demangle = dict(zip(allsyms, out))
except Exception:
    pass
for a, b, n in back:
    names = [demangle.get(s, s)[:90] for s, _ in ex[(a, b)].most_common(4)]
    print(f'  {n:5} {a} -> {b}: ' + ' | '.join(names))
json.dump({'edges': [[a, b, n] for (a, b), n in E.items()]}, open(os.path.join(HERE, 'out/link_edges.json'), 'w'))
