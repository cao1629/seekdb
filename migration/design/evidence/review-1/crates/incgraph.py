#!/usr/bin/env python3
"""Include graph of seekdb at 834bbee1e: files, headers, directories, modules, crates.

Read-only over the worktree and the reference build's compile_commands.json.
Quoted includes are resolved like the compiler does: the including file's directory
first, then the -I roots in compile-command order. Forwarding headers (a header whose
only content is one #include) are followed to their target. Build-time generated files
under build_release/generated are nodes too (prefix gen/).

Usage: python3 incgraph.py [--crates crates.tsv] [--out DIR] [--home]
  crates.tsv: <path prefix><TAB><crate>; longest prefix wins.
  --home: move query/api and data_plane/api headers to the directory of their same-stem .cpp.
"""
import argparse, collections, json, os, re, subprocess, sys

WT = '/Users/colin/seekdb-dev/migrate-to-rust'
REF = '/Users/colin/seekdb-dev/ref-834bbee1e'
BUILD = REF + '/build_release'
GEN = BUILD + '/generated'
EXTS = ('.h', '.hpp', '.cpp', '.cc', '.c', '.ipp', '.def', '.cxx')
HDR_EXTS = ('.h', '.hpp', '.ipp', '.def')
# order of the -I flags of an src/sql compile command (compile_commands.json)
ROOTS = ['rust/sql-nio/include', '', 'gen', 'gen/share', 'gen/share/inner_table', 'src',
         'src/query/api', 'src/data_plane/api', 'src/objit/include', 'src/oblib/easy',
         'src/oblib', 'src/oblib/common', 'src/oblib/easy/include']
INC_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', re.M)

DATA = {'src/storage/fts/dict/ob_ik_dic.cpp', 'src/oblib/common/timezone/ob_timezone_info.cpp',
        'src/oblib/lib/charset/ob_ctype_utf8_tab.h', 'src/oblib/lib/charset/ob_ctype_uca.cc'}


def tracked_files():
    out = subprocess.run(['git', '-C', WT, 'ls-files', '-z', '--', 'src', 'rust/sql-nio/include'],
                         capture_output=True, check=True).stdout.decode()
    return sorted(f for f in out.split('\0') if f.endswith(EXTS))


def gen_files():
    res = []
    for d, _, fs in os.walk(GEN):
        for f in fs:
            p = os.path.join(d, f)
            if p.endswith(EXTS):
                res.append('gen/' + os.path.relpath(p, GEN))
    return sorted(res)


def real_path(rel):
    return os.path.join(GEN, rel[4:]) if rel.startswith('gen/') else os.path.join(WT, rel)


def category(f):
    if f.startswith('gen/'):
        return 'generated-build'
    if f in DATA:
        return 'data'
    if '/zstd_1_3_8/zstd_src/' in f or f.startswith('src/oblib/easy/') or '/hash_func/xxhash' in f:
        return 'vendored'
    if '/codec/ob_generated_' in f or f.endswith(('.pb.cc', '.pb.h', '.pb-c.c', '.pb-c.h')) \
            or f in ('src/share/ob_errno.cpp', 'src/share/ob_errno.h', 'src/oblib/lib/ob_errno.h') \
            or '/system_variable/ob_system_variable_init' in f or '/system_variable/ob_system_variable_alias' in f \
            or '/system_variable/ob_sys_var_class_type' in f or '/system_variable/ob_sys_var_meta' in f \
            or 'session/ob_system_variable_factory' in f or f.startswith('rust/'):
        return 'generated-tracked'
    return 'hand'


def is_forwarder(text):
    incs = INC_RE.findall(text)
    if len(incs) != 1:
        return None
    body = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith('//') or s.startswith('#'):
            continue
        return None
    return incs[0]


def tarjan(nodes, adj):
    index, low, on, st, out, counter = {}, {}, set(), [], [], [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        st.append(root); on.add(root)
        while work:
            v, it = work[-1]
            pushed = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]; counter[0] += 1
                    st.append(w); on.add(w)
                    work.append((w, iter(sorted(adj.get(w, ())))))
                    pushed = True
                    break
                elif w in on:
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = st.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def live_roots(byrel):
    cc = json.load(open(os.path.join(BUILD, 'compile_commands.json')))
    srcs = set()
    for c in cc:
        f = c['file']
        if '/Unity/' in f:
            for inc in INC_RE.findall(open(f).read()):
                if inc.startswith(GEN + '/'):
                    srcs.add('gen/' + inc[len(GEN) + 1:])
                elif inc.startswith(REF + '/'):
                    srcs.add(inc[len(REF) + 1:])
                else:
                    srcs.add(inc)
        elif f.startswith(GEN + '/'):
            srcs.add('gen/' + f[len(GEN) + 1:])
        elif f.startswith(REF + '/'):
            srcs.add(f[len(REF) + 1:])
    return srcs & byrel, srcs - byrel


def module_of(rel):
    p = rel.split('/')
    if p[0] == 'rust':
        return 'rust'
    if p[0] == 'gen':
        return p[1]
    if p[1] == 'oblib':
        return 'oblib/' + p[2] if len(p) > 3 else 'oblib'
    return p[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--crates')
    ap.add_argument('--home', action='store_true')
    ap.add_argument('--out', default='/Users/colin/seekdb-dev/migrate-to-rust/migration/design/evidence/01-crates/out')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--raw', action='store_true', help='do not follow forwarding headers')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files = tracked_files() + gen_files()
    byrel = set(files)
    text, lines = {}, {}
    for f in files:
        with open(real_path(f), 'rb') as fh:
            b = fh.read()
        text[f] = b.decode('utf-8', 'replace')
        lines[f] = b.count(b'\n') + (1 if b and not b.endswith(b'\n') else 0)

    def resolve(inc, from_dir):
        cand = os.path.normpath(os.path.join(from_dir, inc))
        hits = []
        if cand in byrel:
            return cand
        for r in ROOTS:
            cand = os.path.normpath(os.path.join(r, inc)) if r else os.path.normpath(inc)
            if cand in byrel:
                return cand
        return None

    fwd = {}
    for f in files:
        if f.endswith(HDR_EXTS):
            t = is_forwarder(text[f])
            if t:
                r = resolve(t, os.path.dirname(f))
                if r and r != f:
                    fwd[f] = r

    def follow(x):
        seen = set()
        while x in fwd and x not in seen:
            seen.add(x); x = fwd[x]
        return x

    raw_edges = collections.Counter()
    edges = collections.Counter()
    unresolved = collections.Counter()
    for f in files:
        for inc in INC_RE.findall(text[f]):
            r = resolve(inc, os.path.dirname(f))
            if r is None:
                unresolved[inc] += 1
                continue
            if r != f:
                raw_edges[(f, r)] += 1
            r2 = r if a.raw else follow(r)
            if r2 != f:
                edges[(f, r2)] += 1
    adj = collections.defaultdict(set)
    for (x, y) in edges:
        adj[x].add(y)
    radj = collections.defaultdict(set)
    for (x, y) in raw_edges:
        radj[x].add(y)

    roots, missing = live_roots(byrel)
    live = set(); stack = list(roots)
    while stack:
        x = stack.pop()
        if x in live:
            continue
        live.add(x)
        stack.extend(radj.get(x, ()))   # raw edges: a forwarder that is included is live
    log = open(os.path.join(a.out, 'summary.txt'), 'w')

    def say(*args):
        s = ' '.join(str(x) for x in args)
        log.write(s + '\n')
        if not a.quiet:
            print(s)
    tracked = [f for f in files if not f.startswith('gen/')]
    say('tracked files', len(tracked), 'lines', sum(lines[f] for f in tracked),
        '| generated-at-build files', len(files) - len(tracked), 'lines', sum(lines[f] for f in files if f.startswith('gen/')))
    say('forwarders', len(fwd), '| resolved include lines', sum(raw_edges.values()),
        '| unresolved include lines', sum(unresolved.values()), 'distinct', len(unresolved))
    say('compiled roots', len(roots), '| compile-command files outside the tracked+gen set', len(missing))
    say('live files', len(live), 'lines', sum(lines[f] for f in live), '| dead tracked files',
        len([f for f in tracked if f not in live]), 'lines', sum(lines[f] for f in tracked if f not in live))
    cat = collections.Counter(); catf = collections.Counter()
    for f in live:
        cat[category(f)] += lines[f]; catf[category(f)] += 1
    say('live lines by category', dict(cat), dict(catf))
    with open(os.path.join(a.out, 'dead.txt'), 'w') as fh:
        for f in tracked:
            if f not in live:
                fh.write(f'{lines[f]}\t{f}\n')

    # header cycles: header -> header edges (forwarders followed), live headers only
    hadj = {f: {y for y in adj.get(f, ()) if y.endswith(HDR_EXTS) and y in live} for f in live if f.endswith(HDR_EXTS)}
    hs = [c for c in tarjan(sorted(hadj), hadj) if len(c) > 1]
    say('header cycles', len(hs), 'files', sum(len(c) for c in hs), 'largest', max(len(c) for c in hs))
    with open(os.path.join(a.out, 'header_cycles.txt'), 'w') as fh:
        for c in sorted(hs, key=lambda c: (-len(c), sorted(c))):
            fh.write(f'{len(c)}\t' + ' '.join(sorted(c)) + '\n')
    fadj = {f: {y for y in adj.get(f, ()) if y in live} for f in live}
    fs = [c for c in tarjan(sorted(fadj), fadj) if len(c) > 1]
    say('file cycles incl .cpp', len(fs), 'files', sum(len(c) for c in fs))

    # home directory of a file (for --home): api headers live with their .cpp
    home = {f: os.path.dirname(f) for f in files}
    if a.home:
        stems = collections.defaultdict(list)
        for f in live:
            if f.endswith(('.cpp', '.cc', '.c')):
                stems[os.path.splitext(os.path.basename(f))[0]].append(f)
        moved = 0
        for f in live:
            if (f.startswith('src/query/api/') or f.startswith('src/data_plane/api/')) and f.endswith('.h'):
                st = os.path.splitext(os.path.basename(f))[0]
                cands = [c for c in stems.get(st, []) if not c.startswith(('src/query/', 'src/data_plane/'))]
                if len(cands) == 1:
                    home[f] = os.path.dirname(cands[0]); moved += 1
        say('api headers moved to the directory of their same-stem .cpp:', moved)

    def d(f):
        return home[f]
    dl = collections.Counter(); dc = collections.Counter()
    for f in live:
        dl[d(f)] += lines[f]; dc[d(f)] += 1
    dedges = collections.Counter(); dexamples = collections.defaultdict(list)
    for (x, y), n in edges.items():
        if x in live and y in live and d(x) != d(y):
            dedges[(d(x), d(y))] += n
            dexamples[(d(x), d(y))].append((x, y))
    dnodes = sorted(dl)
    dadj = collections.defaultdict(set)
    for (x, y) in dedges:
        dadj[x].add(y)
    dsccs = sorted(tarjan(dnodes, dadj), key=lambda c: -len(c))
    multi = [c for c in dsccs if len(c) > 1]
    big = set(dsccs[0])
    bigfiles = [f for f in live if d(f) in big]
    say('directories', len(dnodes), 'dir edges', len(dedges), '| multi-dir SCCs', len(multi),
        '| largest SCC: dirs', len(big), 'files', len(bigfiles), 'lines', sum(lines[f] for f in bigfiles),
        dict(collections.Counter(module_of(x + '/x') for x in big)))
    for c in multi[1:]:
        cf = [f for f in live if d(f) in set(c)]
        say('   other SCC: dirs', len(c), 'files', len(cf), 'lines', sum(lines[f] for f in cf), sorted(c)[:4], '...')
    with open(os.path.join(a.out, 'dir_scc.txt'), 'w') as fh:
        for c in multi:
            fh.write(f'{len(c)}\n')
            for x in sorted(c):
                fh.write(f'  {dl[x]}\t{dc[x]}\t{x}\n')
    with open(os.path.join(a.out, 'dir_lines.tsv'), 'w') as fh:
        for x in sorted(dl, key=lambda k: -dl[k]):
            fh.write(f'{dl[x]}\t{dc[x]}\t{x}\t{"SCC" if x in big else ""}\n')
    with open(os.path.join(a.out, 'dir_edges.tsv'), 'w') as fh:
        for (x, y), n in sorted(dedges.items(), key=lambda kv: -kv[1]):
            fh.write(f'{n}\t{x}\t{y}\n')

    medges = collections.Counter(); mex = collections.defaultdict(list)
    for (x, y), n in edges.items():
        if x in live and y in live and module_of(x) != module_of(y):
            medges[(module_of(x), module_of(y))] += n
            mex[(module_of(x), module_of(y))].append(f'{x} -> {y}')
    madj = collections.defaultdict(set)
    for (x, y) in medges:
        madj[x].add(y)
    msccs = [c for c in tarjan(sorted({m for e in medges for m in e}), madj) if len(c) > 1]
    say('module SCCs', [sorted(c) for c in msccs])
    for c in msccs:
        cs = set(c)
        for (x, y), n in sorted(medges.items()):
            if x in cs and y in cs and n <= 6:
                say('   small module edge', x, '->', y, n, mex[(x, y)][:6])
    with open(os.path.join(a.out, 'module_edges.tsv'), 'w') as fh:
        for (x, y), n in sorted(medges.items(), key=lambda kv: -kv[1]):
            fh.write(f'{n}\t{x}\t{y}\n')

    json.dump({'lines': lines, 'live': sorted(live), 'edges': [[x, y, n] for (x, y), n in edges.items()],
               'home': home}, open(os.path.join(a.out, 'graph.json'), 'w'))

    if a.crates:
        rules = []
        for line in open(a.crates):
            line = line.split('#')[0].rstrip()
            if not line:
                continue
            pre, crate = line.split('\t')[:2]
            rules.append((pre.rstrip('/'), crate.strip()))
        rules.sort(key=lambda r: -len(r[0]))

        def crate_of(f):
            for pre, c in rules:
                if f == pre or f.startswith(pre + '/') or (pre.endswith('*') and f.startswith(pre[:-1])):
                    return c
            return 'UNASSIGNED:' + d(f)
        cl = collections.Counter(); cf = collections.Counter(); chand = collections.Counter()
        cmap = {}
        for f in live:
            c = crate_of(f); cmap[f] = c
            cl[c] += lines[f]; cf[c] += 1
            if category(f) == 'hand':
                chand[c] += lines[f]
        cedges = collections.Counter(); clines = collections.defaultdict(list)
        for (x, y), n in edges.items():
            if x in live and y in live and cmap[x] != cmap[y]:
                cedges[(cmap[x], cmap[y])] += n
                clines[(cmap[x], cmap[y])].append((x, y))
        cadj = collections.defaultdict(set)
        for (x, y) in cedges:
            cadj[x].add(y)
        csccs = [c for c in tarjan(sorted(cl), cadj) if len(c) > 1]
        say('\n=== crates', len(cl), 'crate edges', len(cedges), 'crate SCCs', len(csccs))
        for c in sorted(cl, key=lambda k: -cl[k]):
            say(f'{cl[c]:>9} {chand[c]:>9} {cf[c]:>5} {c}')
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
                    for (fx, fy) in sorted(clines[(x, y)])[:60]:
                        fh.write(f'      {fx} -> {fy}\n')
        for c in csccs:
            say('crate SCC', sorted(c))
        json.dump({'crate_of': cmap, 'cedges': [[x, y, n] for (x, y), n in cedges.items()]},
                  open(os.path.join(a.out, 'crates.json'), 'w'))


if __name__ == '__main__':
    main()
