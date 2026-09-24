#!/usr/bin/env python3
"""Directory-level include graph of seekdb at 834bbee1e.

Read-only over the worktree. Resolves quoted includes against the CMake -I roots,
follows forwarding headers, marks live files from the reference build's unity
sources, and reports SCCs at header, directory, module and crate level.

Usage: python3 incgraph.py [--crates crates.tsv] [--out DIR]
crates.tsv: directory-prefix<TAB>crate (longest prefix wins).
"""
import argparse, collections, json, os, re, subprocess, sys

WT = '/Users/colin/seekdb-dev/migrate-to-rust'
BUILD = '/Users/colin/seekdb-dev/ref-834bbee1e/build_release'
REF = '/Users/colin/seekdb-dev/ref-834bbee1e'
EXTS = ('.h', '.hpp', '.cpp', '.cc', '.c', '.ipp', '.def')
HDR_EXTS = ('.h', '.hpp', '.ipp', '.def')
ROOTS = ['', 'src', 'src/query/api', 'src/data_plane/api', 'src/objit/include',
         'src/oblib', 'src/oblib/common', 'src/oblib/easy', 'src/oblib/easy/include',
         'rust/sql-nio/include']
INC_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
FWD_RE = re.compile(r'^\s*(#\s*(pragma\s+once|include\s+"[^"]+"|ifndef\s+\w+|define\s+\w+|endif.*)|//.*|/\*.*?\*/)?\s*$', re.M)


def tracked_files():
    out = subprocess.run(['git', '-C', WT, 'ls-files', '-z', '--', 'src', 'rust/sql-nio/include'],
                         capture_output=True, check=True).stdout.decode()
    return sorted(f for f in out.split('\0') if f.endswith(EXTS))


def count_lines(path):
    with open(path, 'rb') as fh:
        return sum(1 for _ in fh)


def is_forwarder(text):
    incs = INC_RE.findall(text)
    if len(incs) != 1:
        return None
    body = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith('//'):
            continue
        if s.startswith('#'):
            continue
        return None
    return incs[0]


def resolve(inc, from_dir, byrel):
    cand = os.path.normpath(os.path.join(from_dir, inc))
    if cand in byrel:
        return cand
    for r in ROOTS:
        cand = os.path.normpath(os.path.join(r, inc)) if r else os.path.normpath(inc)
        if cand in byrel:
            return cand
    return None


def tarjan(nodes, adj):
    index = {}; low = {}; on = set(); st = []; out = []; counter = [0]
    sys.setrecursionlimit(1000000)

    def strong(v):
        index[v] = low[v] = counter[0]; counter[0] += 1
        st.append(v); on.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            out.append(comp)
    for n in nodes:
        if n not in index:
            strong(n)
    return out


def live_sources():
    cc = json.load(open(os.path.join(BUILD, 'compile_commands.json')))
    srcs = set()
    for c in cc:
        f = c['file']
        if f.endswith('.cxx'):
            try:
                t = open(f).read()
            except FileNotFoundError:
                continue
            for inc in INC_RE.findall(t):
                if inc.startswith(REF + '/'):
                    srcs.add(inc[len(REF) + 1:])
                elif inc.startswith('/'):
                    pass
                else:
                    srcs.add(inc)
        elif f.startswith(REF + '/'):
            srcs.add(f[len(REF) + 1:])
    return srcs


def module_of(rel):
    p = rel.split('/')
    if p[0] == 'rust':
        return 'rust'
    if p[1] == 'oblib':
        return 'oblib/' + p[2] if len(p) > 3 else 'oblib'
    return p[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--crates')
    ap.add_argument('--out', default='/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/out')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files = tracked_files()
    byrel = set(files)
    text = {}
    lines = {}
    for f in files:
        with open(os.path.join(WT, f), 'rb') as fh:
            b = fh.read()
        text[f] = b.decode('utf-8', 'replace')
        lines[f] = b.count(b'\n') + (1 if b and not b.endswith(b'\n') else 0)
    fwd = {}
    for f in files:
        if f.endswith(HDR_EXTS):
            t = is_forwarder(text[f])
            if t:
                r = resolve(t, os.path.dirname(f), byrel)
                if r and r != f:
                    fwd[f] = r
    print('files', len(files), 'lines', sum(lines.values()), 'forwarders', len(fwd))

    def follow(x):
        seen = set()
        while x in fwd and x not in seen:
            seen.add(x); x = fwd[x]
        return x

    edges = collections.Counter()   # (from_file, to_file) -> include lines
    unresolved = collections.Counter()
    for f in files:
        for inc in INC_RE.findall(text[f]):
            r = resolve(inc, os.path.dirname(f), byrel)
            if r is None:
                unresolved[inc] += 1
                continue
            r = follow(r)
            if r != f:
                edges[(f, r)] += 1
    print('resolved include lines', sum(edges.values()), 'unresolved distinct', len(unresolved))
    adj = collections.defaultdict(set)
    for (x, y) in edges:
        adj[x].add(y)

    # live set
    srcs = live_sources() & byrel
    live = set()
    stack = list(srcs)
    while stack:
        x = stack.pop()
        if x in live:
            continue
        live.add(x)
        stack.extend(adj.get(x, ()))
    # add same-stem headers of live sources are already via includes; also mark forwarders live if included
    print('compiled sources', len(srcs), 'live files', len(live), 'live lines', sum(lines[f] for f in live))
    dead = [f for f in files if f not in live]
    with open(os.path.join(a.out, 'dead.txt'), 'w') as fh:
        for f in dead:
            fh.write(f'{lines[f]}\t{f}\n')
    print('dead files', len(dead), 'dead lines', sum(lines[f] for f in dead))

    # header-level SCCs (live headers only)
    hadj = {f: {y for y in adj.get(f, ()) if y.endswith(HDR_EXTS)} for f in live if f.endswith(HDR_EXTS)}
    sccs = [c for c in tarjan(sorted(hadj), hadj) if len(c) > 1]
    print('header cycles', len(sccs), 'files', sum(len(c) for c in sccs))
    with open(os.path.join(a.out, 'header_cycles.txt'), 'w') as fh:
        for c in sorted(sccs, key=lambda c: -len(c)):
            fh.write(f'{len(c)}\t' + ' '.join(sorted(c)) + '\n')
    # file-level SCCs including .cpp
    fadj = {f: {y for y in adj.get(f, ()) if y in live} for f in live}
    fsccs = [c for c in tarjan(sorted(fadj), fadj) if len(c) > 1]
    print('file cycles (h+cpp)', len(fsccs), 'files', sum(len(c) for c in fsccs))

    # directory-level graph over live files
    def d(f):
        return os.path.dirname(f)
    dedges = collections.Counter()
    for (x, y), n in edges.items():
        if x in live and y in live and d(x) != d(y):
            dedges[(d(x), d(y))] += n
    dnodes = sorted({d(f) for f in live})
    dadj = collections.defaultdict(set)
    for (x, y) in dedges:
        dadj[x].add(y)
    dsccs = sorted([c for c in tarjan(dnodes, dadj)], key=lambda c: -len(c))
    big = dsccs[0]
    bigfiles = [f for f in live if d(f) in set(big)]
    bymod = collections.Counter(module_of(x) for x in big)
    print('directory nodes', len(dnodes), 'edges', len(dedges), 'largest SCC dirs', len(big), 'files', len(bigfiles),
          'lines', sum(lines[f] for f in bigfiles), dict(bymod))
    print('other multi-dir SCCs:', [(len(c), sorted(c)[:3]) for c in dsccs[1:] if len(c) > 1])
    with open(os.path.join(a.out, 'dir_scc.txt'), 'w') as fh:
        for c in dsccs:
            if len(c) > 1:
                fh.write(f'{len(c)}\n')
                for x in sorted(c):
                    fh.write(f'  {x}\n')
    dl = collections.Counter(); dc = collections.Counter()
    for f in live:
        dl[d(f)] += lines[f]; dc[d(f)] += 1
    with open(os.path.join(a.out, 'dir_lines.tsv'), 'w') as fh:
        for x in sorted(dl, key=lambda k: -dl[k]):
            fh.write(f'{dl[x]}\t{dc[x]}\t{x}\t{"SCC" if x in set(big) else ""}\n')
    with open(os.path.join(a.out, 'dir_edges.tsv'), 'w') as fh:
        for (x, y), n in sorted(dedges.items(), key=lambda kv: -kv[1]):
            fh.write(f'{n}\t{x}\t{y}\n')

    # module level
    medges = collections.Counter()
    mlines = collections.defaultdict(list)
    for (x, y), n in edges.items():
        if x in live and y in live and module_of(x) != module_of(y):
            medges[(module_of(x), module_of(y))] += n
            mlines[(module_of(x), module_of(y))].append(x + ' -> ' + y)
    madj = collections.defaultdict(set)
    for (x, y) in medges:
        madj[x].add(y)
    msccs = [c for c in tarjan(sorted({m for e in medges for m in e}), madj) if len(c) > 1]
    print('module SCCs', msccs)
    for c in msccs:
        cs = set(c)
        for (x, y), n in sorted(medges.items()):
            if x in cs and y in cs and n <= 5:
                print('  ', x, '->', y, n, mlines[(x, y)][:4])

    # crate level
    if a.crates:
        rules = []
        for line in open(a.crates):
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            pre, crate = line.split('\t')[:2]
            rules.append((pre, crate))
        rules.sort(key=lambda r: -len(r[0]))

        def crate_of(f):
            for pre, c in rules:
                if f == pre or f.startswith(pre.rstrip('/') + '/'):
                    return c
            return 'UNASSIGNED:' + d(f)
        cl = collections.Counter(); cf = collections.Counter()
        for f in live:
            cl[crate_of(f)] += lines[f]; cf[crate_of(f)] += 1
        cedges = collections.Counter(); clines = collections.defaultdict(list)
        for (x, y), n in edges.items():
            if x in live and y in live:
                cx, cy = crate_of(x), crate_of(y)
                if cx != cy:
                    cedges[(cx, cy)] += n
                    clines[(cx, cy)].append((x, y))
        cadj = collections.defaultdict(set)
        for (x, y) in cedges:
            cadj[x].add(y)
        csccs = [c for c in tarjan(sorted(cl), cadj) if len(c) > 1]
        print('\n=== crates', len(cl))
        for c in sorted(cl, key=lambda k: -cl[k]):
            print(f'{cl[c]:>9} {cf[c]:>5} {c}')
        print('crate edges', len(cedges), 'crate SCCs', len(csccs))
        with open(os.path.join(a.out, 'crate_edges.tsv'), 'w') as fh:
            for (x, y), n in sorted(cedges.items(), key=lambda kv: -kv[1]):
                fh.write(f'{n}\t{x}\t{y}\n')
        with open(os.path.join(a.out, 'crate_cycles.txt'), 'w') as fh:
            for c in csccs:
                cs = set(c)
                fh.write('SCC ' + ' '.join(sorted(c)) + '\n')
                inside = [(e, n) for e, n in cedges.items() if e[0] in cs and e[1] in cs]
                for (x, y), n in sorted(inside, key=lambda kv: kv[1]):
                    fh.write(f'  {n}\t{x} -> {y}\n')
                    for (fx, fy) in sorted(clines[(x, y)])[:40]:
                        fh.write(f'      {fx} -> {fy}\n')
        # topological order of crates
        indeg = collections.Counter()
        for (x, y) in cedges:
            indeg[y] += 0; indeg[x] += 0
        print('crate SCCs:', [sorted(c) for c in csccs])
        # print a layering (longest path from sources) when acyclic
        if not csccs:
            order = []
            rem = {c: set(v for v in cadj[c]) for c in cl}
            for c in cl:
                rem.setdefault(c, set())
            level = {}
            changed = True
            while rem:
                ready = [c for c, deps in rem.items() if not deps]
                if not ready:
                    break
                for c in ready:
                    level[c] = max([level[x] + 1 for x in cadj[c]] + [0])
                    del rem[c]
                for c in rem:
                    rem[c] -= set(ready)
            for c in sorted(level, key=lambda k: (level[k], k)):
                print(f'  level {level[c]:>2} {cl[c]:>8} {c}  <- ' + ', '.join(sorted(cadj[c])))
    # unresolved summary
    with open(os.path.join(a.out, 'unresolved.txt'), 'w') as fh:
        for inc, n in unresolved.most_common():
            fh.write(f'{n}\t{inc}\n')


if __name__ == '__main__':
    main()
