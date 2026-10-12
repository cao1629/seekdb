#!/usr/bin/env python3
"""c13-weak: weak symbol definitions and hand-written extern prototypes in implementation files.
Script-decided category (RULEBOOK 0.4 "weak 符号", §5.2 #13): writes both the sweep
migration/inventory/sweep/c13-weak.tsv and the decided shard migration/inventory.c13-weak.tsv.

Sites (comment/string-stripped first-party text):
  W  `OB_WEAK_SYMBOL` (src/oblib/lib/utility/ob_macro_utils.h:20 = __attribute__((weak))) and
     `__attribute__((weak...))` occurrences outside #define lines (layering memo §2.5: 36 + 5);
     construct: weak definition / weak alias definition / weak declaration
  X  hand-written prototypes at namespace or extern "C" scope of a .cpp/.cc/.c file:
     `extern ...;` declarations, and non-static function prototypes `T f(params);` whose function
     is not defined in the same file; construct: extern function prototype / extern variable
     declaration
Decision (link-level evidence = `nm -m -g` over build_release/src/**/*.o, same reading as
migration/design/survey/scripts/layering/weak_all.py, but generic):
  WEAK_OVERRIDDEN_IN_PROD  the exact weak symbol has a strong definition in another module's object
  WEAK_PLAIN               no strong definition of that symbol elsewhere (incl. same-module override,
                           never-overridden test seams, and code not compiled on the macOS baseline)
  EXTERN_PROTO             every X row
"""
import bisect
import collections
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c13-weak'
SHARD = os.path.join(L.REPO, 'migration', 'inventory.c13-weak.tsv')
BUILD_SRC = os.path.join(L.REPO, 'build_release', 'src')
CRATE_EDGES = os.path.join(L.REPO, 'migration', 'depmap', 'crates', 'edges.tsv')
EXCLUDED = os.path.join(L.REPO, 'migration', 'depmap', 'excluded.tsv')
RX_WEAK = re.compile(r'\bOB_WEAK_SYMBOL\b|__attribute__\s*\(\(\s*weak\b')
NM_LINE = re.compile(r'^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$')
TYPE_WORDS = re.compile(r'\b(void|bool|char|short|int|long|float|double|unsigned|signed|size_t|u?int\d+_t|const|struct|'
                        r'enum|class|volatile)\b|[*&]|\.\.\.')

# RULEBOOK 0.4 table (oblib->share, oblib->observer, oblib->storage rows): the six production overrides.
SEAM_RULES = {
    'ob_strerror': 'seam, move down (RULEBOOK 0.4 oblib->share): the error table is generated into oblib::error from '
                   'ob_errno.def (0.13); oblib owns the only ob_strerror; share\'s generated strong body is not '
                   'translated; callers keep calling ob_strerror',
    'get_cpu_count': 'seam, move down (RULEBOOK 0.4 oblib->share): oblib\'s get_cpu_count reads config::cpu_count() '
                     'directly (the strong body in share); the weak default is not kept',
    'allow_next_syslog': 'seam hook (RULEBOOK 0.4 oblib->share): oblib keeps fn allow_next_syslog backed by a '
                         'OnceLock<fn> hook whose default is this weak body; share registers its strong body at the '
                         'start of main (0.5 step 3); hook name per seams.tsv',
    'current_resource_owner_id': 'seam hook (RULEBOOK 0.4 oblib->share): oblib keeps fn current_resource_owner_id backed '
                                 'by a OnceLock<fn> hook whose default is this weak body; share registers its strong '
                                 'body at the start of main (0.5 step 3); hook name per seams.tsv',
    'create_inner_sql_connection_for_proxy': 'seam trait+slot (RULEBOOK 0.4 oblib->observer): trait InnerConnFactory + '
                                             'Slot<dyn InnerConnFactory> in oblib; observer binds it in the composition '
                                             'root; an empty slot returns OB_NOT_SUPPORTED exactly like this weak body',
    'request_finish_callback': 'seam hook (RULEBOOK 0.4 oblib->storage): oblib hook request_finish_callback (default: '
                               'do nothing, this weak body) registered by storage; observer and sql call the oblib '
                               'wrapper instead of their hand-written prototypes',
}


UNITS_TSV = os.path.join(L.REPO, 'migration', 'design', 'units.tsv')


def load_unit_dispositions():
    """file -> disposition from migration/design/units.tsv (written by make_units.py; run it first)."""
    out = {}
    if not os.path.exists(UNITS_TSV):
        print('warning: %s missing; run make_units.py first for unit-disposition notes' % L.rel(UNITS_TSV))
        return out
    with open(UNITS_TSV, encoding='utf-8') as fh:
        head = fh.readline().rstrip('\n').split('\t')
        im, idp = head.index('members'), head.index('disposition')
        for ln in fh:
            r = ln.rstrip('\n').split('\t')
            for f in r[im].split(','):
                out[f] = r[idp]
    return out


def module_of(path):
    m = path.split('/')
    return m[1] if len(m) > 1 else ''


def crate_of_module(mod):
    return {'pl': 'sql', 'objit': 'query'}.get(mod, mod)


def crate_reach():
    edges = collections.defaultdict(set)
    with open(CRATE_EDGES, encoding='utf-8') as fh:
        next(fh)
        for ln in fh:
            a, b = ln.split('\t')[:2]
            edges[crate_of_module(a.split('/')[1])].add(crate_of_module(b.split('/')[1]))
    reach = {}
    for c in list(edges) + ['oblib']:
        seen, todo = set(), [c]
        while todo:
            x = todo.pop()
            for y in edges.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        reach[c] = seen
    return reach


def statement_around(text, off):
    """(start, end, terminator) of the declaration statement containing off."""
    s = off
    while s > 0 and text[s - 1] not in ';{}':
        s -= 1
    e = off
    depth = 0
    while e < len(text):
        c = text[e]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and c in ';{':
            return s, e, c
        e += 1
    return s, e, ''


def strip_attributes(s):
    """Remove `__attribute__((...))` (balanced) and other `__xxx` tokens such as __THROW."""
    out, i = [], 0
    while i < len(s):
        m = re.compile(r'\b__\w+\b').match(s, i) if (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == '_')) else None
        if m:
            j = m.end()
            k = j
            while k < len(s) and s[k].isspace():
                k += 1
            if m.group(0) == '__attribute__' and k < len(s) and s[k] == '(':
                depth = 0
                while k < len(s):
                    if s[k] == '(':
                        depth += 1
                    elif s[k] == ')':
                        depth -= 1
                        if depth == 0:
                            k += 1
                            break
                    k += 1
                j = k
            out.append(' ')
            i = j
            continue
        out.append(s[i])
        i += 1
    return ''.join(out)


def declarator(stmt):
    """(qualified name, params) of the first function declarator in a statement, else (None, None)."""
    h = ' '.join(stmt.split())
    for o, c in L._top_level_parens(h):
        before = h[:o].rstrip()
        if not before or before.endswith(']'):
            continue
        name = L._name_before(h, o)
        if not name:
            continue
        base = name.split('::')[-1]
        if base in L._KW_BLOCK or base in ('OB_WEAK_SYMBOL', '__THROW', 'alias', 'visibility', 'weak'):
            continue
        rest = strip_attributes(h[c + 1:])
        if not L._valid_suffix(rest):
            continue  # e.g. a macro invocation such as ERRSIM_POINT_DEF(X) in front of the real declarator
        return name.lstrip(':'), ' '.join(h[o + 1:c].split())
    return None, None


def nm_tables():
    objs = []
    for d, _dn, fn in os.walk(BUILD_SRC):
        for f in fn:
            if f.endswith('.o') and '/CMakeFiles/' in os.path.join(d, f):
                objs.append(os.path.relpath(os.path.join(d, f), BUILD_SRC))
    objs.sort()

    def rd(rel):
        out = subprocess.run(['nm', '-m', '-g', os.path.join(BUILD_SRC, rel)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL).stdout.decode('utf-8', 'replace')
        s, w = set(), set()
        for line in out.splitlines():
            m = NM_LINE.match(line)
            if not m or m.group('sect') == 'undefined':
                continue
            (w if 'weak' in m.group('attrs').split() else s).add(m.group('name'))
        return s, w

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(rd, objs))
    strong, weak = collections.defaultdict(set), collections.defaultdict(set)
    for o, (s, w) in zip(objs, res):
        mod = o.split('/')[0]
        for x in s:
            strong[x].add((mod, o))
        for x in w:
            weak[x].add((mod, o))
    names = sorted(set(strong) | set(weak))
    plain = [n[1:] if n.startswith('_') else n for n in names]
    dem = subprocess.run(['c++filt'], input='\n'.join(plain) + '\n', stdout=subprocess.PIPE,
                         universal_newlines=True).stdout.splitlines()
    return strong, weak, dict(zip(names, dem))


_BUILTIN = re.compile(r'^(void|bool|char|short|int|long|float|double|unsigned|signed|size_t|ssize_t|off_t|loff_t|'
                      r'va_list|u?int\d+_t|u?intptr_t|\w+_t)$')


def split_commas(s):
    out, depth, cur = [], 0, []
    for c in s:
        if c in '(<[{':
            depth += 1
        elif c in ')>]}':
            depth -= 1
        if c == ',' and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    out.append(''.join(cur))
    return out


def looks_like_params(params):
    """True if a parenthesised list reads as function parameters rather than constructor arguments."""
    p = params.strip()
    if p in ('', 'void'):
        return True
    for it in split_commas(p):
        it = re.sub(r'\s*=.*$', '', it.strip())
        if it == '...' or re.search(r'\(\s*\*', it):
            continue
        if '(' in it or '"' in it or "'" in it or it.startswith('&') or re.match(r'^[-+]?\d', it) \
                or it in ('true', 'false', 'NULL', 'nullptr'):
            return False
        toks = re.findall(r'[A-Za-z_]\w*(?:::\w+)*|[*&]', re.sub(r'<[^<>]*>', '', it))
        if len(toks) >= 2:
            continue
        if len(toks) == 1 and (_BUILTIN.match(toks[0].split('::')[-1]) or re.match(r'^(\w+::)*[A-Z][a-z]\w*$', toks[0])):
            continue
        return False
    return True


def top_level_eq(s):
    depth = 0
    for i, c in enumerate(s):
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == '=' and depth == 0 and s[i + 1:i + 2] != '=' and s[i - 1:i] not in '=!<>':
            return True
    return False


def def_line(p, s):
    """Line of the function name in its declarator (falls back to the `{` line)."""
    hdr = s.header
    start = s.open - len(hdr)
    m = re.search(re.escape(s.name.split('(')[0].split('::')[-1]) + r'\s*\(', hdr)
    return L.line_of(p, start + m.start()) if m else L.line_of(p, s.open)


def main():
    files = L.first_party()
    impl_files = [p for p in files if p.endswith(('.cpp', '.cc', '.c'))]
    with open(EXCLUDED, encoding='utf-8') as fh:
        next(fh)
        generated = sorted(ln.split('\t')[0] for ln in fh if '\tgenerated\t' in ln and ln.split('\t')[0].endswith(('.cpp', '.cc', '.c')))
    generated = [p for p in generated if os.path.exists(os.path.join(L.REPO, p))]
    strong, weak, dem = nm_tables()
    reach = crate_reach()
    unit_disp = load_unit_dispositions()
    rows, shard = [], []
    seen = collections.Counter()

    def defs_of(name, pool):
        base = name.split('::')[-1]
        out = []
        for p in pool:
            if base not in L.raw(p):
                continue
            for s in L.scopes(p):
                if s.kind != 'function':
                    continue
                fn = L.enclosing_function(p, s.open + 1) or s.name
                fbase = fn.split('::')[-1]
                if fbase != base:
                    continue
                if '::' in name and not (fn == name or fn.endswith('::' + name)):
                    continue
                out.append((p, def_line(p, s), fn))
        return out

    # ---------------- W: weak sites
    for p in files:
        if 'OB_WEAK_SYMBOL' not in L.raw(p) and 'weak' not in L.raw(p):
            continue
        text = L.stripped(p)
        for m in RX_WEAK.finditer(text):
            ls = L.line_start_offset(p, L.line_of(p, m.start()))
            if text[ls:m.start()].lstrip().startswith('#'):
                continue
            s, e, term = statement_around(text, m.start())
            stmt = text[s:e]
            name, params = declarator(stmt)
            if not name:
                continue
            if term == '{':
                cons = 'weak definition'
            elif 'alias' in stmt:
                cons = 'weak alias definition'
            else:
                cons = 'weak declaration'
            sym = '%s(%s)' % (name, params)
            mod = module_of(p)
            base = name.split('::')[-1]
            rx = re.compile(r'(?:^|[\s:])' + re.escape(name) + r'\(')
            mine = sorted(n for n, locs in weak.items() if any(mm == mod for mm, _ in locs)
                          and (rx.search(dem.get(n, '')) or dem.get(n, '') == name))
            if cons == 'weak alias definition' or not mine:
                mine_c = sorted(n for n, locs in strong.items() if any(mm == mod for mm, _ in locs) and dem.get(n, '') == name)
            else:
                mine_c = []
            others, same = set(), set()
            for n in mine:
                for mm, o in strong.get(n, ()):
                    (same if mm == mod else others).add((mm, o))
            line = L.line_of(p, m.start())
            ev = ['weak %s at %s:%d' % ('declaration' if cons == 'weak declaration' else 'body', p, line)]
            if not mine and not mine_c:
                cls = 'WEAK_PLAIN'
                ev.append('no weak symbol for it in any %s object of build_release: not compiled on the macOS baseline' % mod)
            elif others:
                cls = 'WEAK_OVERRIDDEN_IN_PROD'
                omods = sorted({mm for mm, _ in others})
                ev.append('nm: strong definition of the same symbol in %s (%s)' % (
                    ','.join(omods), ', '.join(sorted({o for _, o in others})[:2])))
                sdefs = defs_of(name, [f for f in impl_files if module_of(f) in omods] +
                                [g for g in generated if module_of(g) in omods])
                for sp, sl, _fn in sdefs[:3]:
                    ev.append('strong body %s:%d%s' % (sp, sl, ' (generated file)' if sp in generated else ''))
            else:
                cls = 'WEAK_PLAIN'
                if same:
                    ev.append('nm: strong definition only in the same module (%s)' % ', '.join(sorted({o for _, o in same})[:2]))
                    sdefs = [d for d in defs_of(name, [f for f in impl_files if module_of(f) == mod]) if d[0] != p]
                    for sp, sl, _fn in sdefs[:3]:
                        ev.append('same-crate strong body %s:%d' % (sp, sl))
                else:
                    ev.append('nm: weak symbol %s has no strong definition in any object' % (mine[0] if mine else mine_c[0]))
            pre = cls
            key = (p, sym, cons)
            seen[key] += 1
            if seen[key] > 1:
                sym = '%s#%d' % (sym, seen[key])
            rows.append((p, sym, cons, line, pre))
            if cls == 'WEAK_OVERRIDDEN_IN_PROD':
                tt = SEAM_RULES.get(base, 'seam per RULEBOOK 0.4 (row not in the 0.4 table: TODO(port): add to seams.tsv)')
            else:
                tt = 'ordinary function: drop the weak attribute and translate the body as written (RULEBOOK 0.4 "其余约 34 处 weak 定义按普通函数翻译")'
                if any(e.startswith('same-crate strong body') for e in ev):
                    tt += '; a strong definition of the same symbol in the same crate wins at link time, so callers ' \
                          'must resolve to that strong function (this weak body stays an unused ordinary function)'
                if any('not compiled' in e for e in ev):
                    tt += '; keep the C++ #if condition as the matching cfg (RULEBOOK 0.15)'
            disp = unit_disp.get(p, '')
            if disp and disp != 'translate':
                ev.append('unit disposition %s (migration/design/units.tsv)' % disp)
                if cls != 'WEAK_OVERRIDDEN_IN_PROD':
                    if disp == 'keep-c':
                        tt = 'none: the unit stays C (keep-c, RULEBOOK 0.12); the weak definition is kept as written'
                    else:
                        tt = 'none at this site: the unit is not translated (%s); the replacing module provides the ' \
                             'function if it is still needed, as an ordinary function' % disp
            shard.append((p, sym, cons, cls, tt, '; '.join(ev), 'confirmed'))
    # ---------------- X: hand-written prototypes in implementation files
    for p in impl_files:
        text = L.stripped(p)
        st = L.structural_text(p)
        if ';' not in text:
            continue
        defined = {(L.enclosing_function(p, s.open + 1) or s.name).split('::')[-1]
                   for s in L.scopes(p) if s.kind == 'function'}
        # statements at namespace / linkage scope
        sc = [s for s in L.scopes(p) if s.kind in ('class', 'function', 'enum', 'init', 'lambda', 'block')]
        blocked = []
        for s in sc:
            blocked.append((s.open, s.close))
        blocked.sort()
        # merge intervals
        merged = []
        for a, b in blocked:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        starts = [a for a, _ in merged]

        def inside(off):
            i = bisect.bisect_right(starts, off) - 1
            return i >= 0 and merged[i][0] < off <= merged[i][1]

        pos = 0
        for m in re.finditer(r';', st):
            s0 = pos
            pos = m.end()
            if inside(m.start()):
                continue
            # statement text: from the last boundary at this level
            k = m.start()
            j = k
            while j > 0 and st[j - 1] not in ';{}':
                j -= 1
            stmt = st[j:k]
            flat = ' '.join(stmt.split())
            if not flat or flat.startswith(('using ', 'typedef ', 'template', 'friend ', 'namespace ', 'static_assert',
                                             'STATIC_ASSERT', 'class ', 'struct ', 'union ', 'enum ', 'return')):
                continue
            if re.match(r'^(public|private|protected)\s*:', flat):
                continue
            if RX_WEAK.search(flat) or top_level_eq(flat):
                continue  # weak sites are W rows; `= ...` is a variable definition
            is_extern = bool(re.match(r'^extern\b', flat)) and not re.match(r'^extern\s+template\b', flat)
            body = re.sub(r'^extern\s*(?:"")?\s*', '', flat) if is_extern else flat
            if re.match(r'^static\b', body) or re.search(r'\binline\b', body):
                continue
            name, params = declarator(body)
            off = j + (len(stmt) - len(stmt.lstrip()))
            if name:
                head = body[:body.find('(')]
                if '=' in head:
                    continue
                if not is_extern:
                    base = name.split('::')[-1]
                    if re.match(r'^[A-Z][A-Z0-9_]*$', base):
                        continue  # macro invocation
                    if not head.strip() or head.strip() == name:
                        continue  # no return type: macro invocation or constructor call
                    if not looks_like_params(params):
                        continue  # an object definition with constructor arguments
                    if '::' in name and not name.startswith('::'):
                        continue  # out-of-class member declarations are not prototypes of external functions
                    if base in defined:
                        continue
                if is_extern and name.split('::')[-1] in defined:
                    continue
                cons = 'extern function prototype'
                sym = '%s(%s)' % (name, params)
            else:
                if not is_extern:
                    continue
                vm = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*$', body)
                if not vm:
                    continue
                name = vm.group(1)
                cons = 'extern variable declaration'
                sym = name
            line = L.line_of(p, off)
            key = (p, sym, cons)
            seen[key] += 1
            if seen[key] > 1:
                sym = '%s#%d' % (sym, seen[key])
            rows.append((p, sym, cons, line, 'EXTERN_PROTO'))
            # evidence: where is it defined?
            base = name.split('::')[-1]
            ev = ['hand-written %s at %s:%d' % ('prototype' if cons.startswith('extern function') else 'extern declaration', p, line)]
            tt = None
            disp = unit_disp.get(p, '')
            if disp and disp != 'translate':
                ev.append('unit disposition %s (migration/design/units.tsv)' % disp)
            if cons == 'extern function prototype':
                dd = [d for d in defs_of(name, impl_files) if d[0] != p]
                if not dd:
                    dd = [d for d in defs_of(name, generated)]
                pc = crate_of_module(module_of(p))
                objs = []
                if not dd:
                    rxn = re.compile(r'(?:^|[\s:])' + re.escape(name) + r'\(')
                    objs = sorted({(mm, o) for n2, locs in strong.items()
                                   if dem.get(n2, '') == name or rxn.search(dem.get(n2, '')) for mm, o in locs})
                if dd:
                    for sp, sl, _fn in dd[:3]:
                        ev.append('definition %s:%d%s' % (sp, sl, ' (generated file)' if sp in generated else ''))
                    dcs = sorted({crate_of_module(module_of(d[0])) for d in dd})
                    if base in SEAM_RULES:
                        tt = 'delete the prototype; call the oblib wrapper of the seam: ' + SEAM_RULES[base]
                    elif all(dc == pc or dc in reach.get(pc, ()) for dc in dcs):
                        tt = 'delete the prototype; import the definition with `use %s::%s;` (RULEBOOK §4 imports)' % (
                            dcs[0], base)
                    else:
                        tt = ('delete the prototype; the definition lives in crate %s, not reachable from %s in the '
                              'include DAG: resolve through the seam table (RULEBOOK 0.4, seams.tsv)') % (','.join(dcs), pc)
                    ev.append('crates: caller %s, definition %s' % (pc, ','.join(dcs)))
                elif objs:
                    dcs = sorted({crate_of_module(mm) for mm, _o in objs})
                    ev.append('no definition in tracked sources; nm: defined in %s (generated or build-time source)' % (
                        ', '.join(o for _mm, o in objs[:2])))
                    ev.append('crates: caller %s, definition %s' % (pc, ','.join(dcs)))
                    if all(dc == pc or dc in reach.get(pc, ()) for dc in dcs):
                        tt = ('delete the prototype; the definition is produced by a generator or build step (RULEBOOK '
                              '0.13): import it with `use %s::%s;` from the generated module') % (dcs[0], base)
                    else:
                        tt = ('delete the prototype; generated definition in crate %s not reachable from %s: resolve '
                              'through the seam table (RULEBOOK 0.4, seams.tsv)') % (','.join(dcs), pc)
                else:
                    ev.append('no definition in first-party or generated sources and none in any build_release object '
                              '(C library, system or third-party symbol)')
                    tt = 'delete the prototype; call the external function through its -sys/shim binding or libc (RULEBOOK 0.14/§1 FFI)'
                if re.match(r'^__init_\w+$', base) and any('compil' in e for e in ev):
                    tt = ('delete the prototype: split-compilation part initialiser (RULEBOOK 0.13 deletes the C++-only '
                          'split-compilation generators); in Rust the parts are one module and the initialiser is called '
                          'directly, `use %s::%s;` within the crate') % (pc, base)
                if disp == 'keep-c':
                    tt = 'none: the unit stays C (keep-c, RULEBOOK 0.12), the prototype is kept as written'
                elif disp.startswith(('replace:', 'rewrite:', 'delete', 'keep-cxx')):
                    tt = 'none at this site: the unit is not translated (%s); the replacing module decides. ' % disp + \
                         'If the replacement keeps the call: ' + tt
            else:
                tt = 'delete the declaration; import the owning static with `use <crate>::%s;`, the owner being the unit that defines it (RULEBOOK §4 imports)' % name
            shard.append((p, sym, cons, 'EXTERN_PROTO', tt, '; '.join(ev), 'confirmed'))
    n = L.finish(CAT, rows, 'W weak definitions/declarations (OB_WEAK_SYMBOL + __attribute__((weak))) + X hand-written '
                            'prototypes in .cpp/.cc/.c; script-decided, shard migration/inventory.c13-weak.tsv')
    shard.sort(key=lambda r: (r[0], r[1], r[2]))
    keys = {(r[0], r[1], r[2]) for r in rows}
    assert keys == {(r[0], r[1], r[2]) for r in shard} and len(shard) == len(rows)
    L.write_tsv(SHARD, ['file', 'symbol', 'source_construct', 'classification', 'target_translation', 'evidence', 'status'], shard)
    by = collections.Counter((r[2], r[4]) for r in rows)
    for k, v in sorted(by.items()):
        print('  %-30s %-26s %d' % (k[0], k[1], v))
    print('shard: %s (%d rows)' % (L.rel(SHARD), len(shard)))
    return n


if __name__ == '__main__':
    main()
