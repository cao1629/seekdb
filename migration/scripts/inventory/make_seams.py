#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_seams.py -- the RULEBOOK 0.4 seam table (phase 1c, sweep set S4).

One row per cross-crate symbol that the Rust crate graph of RULEBOOK 0.3 cannot express as written:
declared in (or used from) a crate that may not depend on the crate where it is defined.  The rows
come from four populations; every row says which ones in `evidence` (`pop=`):

  link   every symbol behind the depmap's 12 "against" and 3 "across" link edges
         (migration/depmap/crates/union_cycles.txt), re-read with `nm` and mapped to crates
         (pl -> sql, objit -> query, files -> crate through migration/design/units.tsv)
  body   out-of-line member definitions (methods, constructors, destructors, static data members,
         serialize/TO_STRING bodies) compiled in another crate than their class (AST database)
  fwd    forward-declared upward types named in a lower crate's fields, signatures, typedefs or in
         explicit template specializations written there (AST database)
  proto  hand-written prototypes in .cpp files (c13 EXTERN_PROTO) whose definition the caller's
         crate cannot reach

Columns (TSV, header line first):
  seam_id symbol kind decl_file decl_line def_file def_line decl_crate def_crate callers shape
  proposed_split rust_landing evidence
`shape` and `proposed_split` are fixed rules (README, "seams (S4)"); they are proposals for the two
reviewers of RULEBOOK 0.4, not decisions.

Outputs
  migration/design/seams.tsv
  migration/inventory/sweep/seams-summary.tsv   counts per (decl_crate, def_crate, kind, proposed_split)
  migration/inventory/sweep/counts.tsv          row `seams` (sweeplib.update_counts)
and, on stdout, the reconciliation with the depmap's 741 cycle symbols.

Inputs (read-only): build_release/src/**/*.o (nm), build_release/inventory-ast/decls.jsonl (astdb),
migration/design/units.tsv, migration/depmap/{symbols/skipped.tsv,crates/union_cycles.txt,
crates/edges.tsv,edges.tsv}, migration/inventory/sweep/c13-weak.tsv, migration/inventory.c13-weak.tsv, src/.

Link-level data is the macOS arm64 release build only (DEV-007); the nm reader is the Mach-O
`nm -m -g` format.  Deterministic: same inputs give byte-identical outputs.

Usage: python3 -I migration/scripts/inventory/make_seams.py [--jobs N]
"""
import argparse
import collections
import concurrent.futures
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sweeplib  # noqa: E402
import astdb  # noqa: E402

REPO = sweeplib.REPO
BUILD_SRC = os.path.join(REPO, 'build_release', 'src')
UNITS_TSV = os.path.join(REPO, 'migration', 'design', 'units.tsv')
SEAMS_TSV = os.path.join(REPO, 'migration', 'design', 'seams.tsv')
SUMMARY_TSV = os.path.join(sweeplib.SWEEP_DIR, 'seams-summary.tsv')
DEPMAP = os.path.join(REPO, 'migration', 'depmap')
SKIPPED_TSV = os.path.join(DEPMAP, 'symbols', 'skipped.tsv')
UNION_CYCLES = os.path.join(DEPMAP, 'crates', 'union_cycles.txt')
INCLUDE_EDGES = os.path.join(DEPMAP, 'crates', 'edges.tsv')
C13_SWEEP = os.path.join(sweeplib.SWEEP_DIR, 'c13-weak.tsv')
C13_SHARD = os.path.join(REPO, 'migration', 'inventory.c13-weak.tsv')

HEADER = ('seam_id', 'symbol', 'kind', 'decl_file', 'decl_line', 'def_file', 'def_line', 'decl_crate',
          'def_crate', 'callers', 'shape', 'proposed_split', 'rust_landing', 'evidence')

# ------------------------------------------------------------------ crate graph (RULEBOOK 0.3)
MODULE_CRATE = {'pl': 'sql', 'objit': 'query'}          # 0.3: pl merges into sql, objit into query
CRATE_DEPS = {                                           # direct port-crate dependencies, 0.3 table
    'oblib': (),
    'storage_core': ('oblib',),
    'share': ('oblib',),
    'query': ('oblib', 'share'),
    'logservice': ('oblib', 'share', 'storage_core'),
    'data_plane': ('oblib', 'share', 'query'),
    'parser': ('oblib', 'query'),
    'sql': ('oblib', 'share', 'data_plane', 'query', 'parser'),
    'storage': ('oblib', 'share', 'data_plane', 'logservice', 'query', 'storage_core'),
    'rootserver': ('oblib', 'storage_core', 'share', 'query', 'logservice', 'data_plane', 'parser', 'sql',
                   'storage'),
    'standby': ('oblib', 'share', 'logservice', 'storage'),
    'observer': ('oblib', 'storage_core', 'share', 'query', 'logservice', 'data_plane', 'parser', 'sql',
                 'storage', 'rootserver', 'standby'),
}
ORDER = ['oblib', 'storage_core', 'share', 'query', 'logservice', 'data_plane', 'parser', 'sql', 'storage',
         'rootserver', 'standby', 'observer']
RANK = {c: i for i, c in enumerate(ORDER)}


def _closure():
    out = {}

    def go(c):
        if c not in out:
            s = set()
            for d in CRATE_DEPS[c]:
                s.add(d)
                s |= go(d)
            out[c] = s
        return out[c]
    for c in CRATE_DEPS:
        go(c)
    return out


CLOSURE = _closure()


def reaches(a, b):
    """True when crate a may name crate b's items (a == b or b is a transitive dependency of a)."""
    return a == b or b in CLOSURE.get(a, ())


def rank(c):
    return RANK.get(c, 99)


def module_crate(module):
    return MODULE_CRATE.get(module, module)


def verify_dag():
    """Every include-level crate edge of the depmap, mapped to port crates, must be allowed by 0.3."""
    bad = []
    with open(INCLUDE_EDGES, encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            p = line.rstrip('\n').split('\t')
            a, b = module_crate(p[0].split('/')[1]), module_crate(p[1].split('/')[1])
            if not reaches(a, b):
                bad.append('%s -> %s' % (a, b))
    for c, deps in CRATE_DEPS.items():
        for d in deps:
            if rank(d) >= rank(c):
                bad.append('order: %s before %s' % (d, c))
    if bad:
        sys.exit('make_seams: crate graph does not cover the include DAG: ' + ', '.join(bad))


# ------------------------------------------------------------------ units.tsv: file -> unit/crate
FILE_UNIT = {}


def load_units():
    with open(UNITS_TSV, encoding='utf-8') as fh:
        head = fh.readline().rstrip('\n').split('\t')
        for line in fh:
            row = dict(zip(head, line.rstrip('\n').split('\t')))
            for m in row['members'].split(','):
                FILE_UNIT[m] = (row['unit'], row['target'], row['crate'], row['disposition'])


def crate_of(path):
    u = FILE_UNIT.get(path)
    if u:
        return u[2]
    p = path.split('/')
    if p[0] == 'src' and len(p) > 2:
        return module_crate(p[1])
    if p[0] == 'build_release' and len(p) > 3 and p[1] in ('generated', 'src'):
        return module_crate(p[2])
    return '?'


def target_of(path):
    u = FILE_UNIT.get(path)
    return u[1] if u else ''


def disposition_of(path):
    u = FILE_UNIT.get(path)
    return u[3] if u else ''


def module_path(target):
    """rust/port/<crate>/src/a/b.rs -> <crate>::a::b"""
    m = re.match(r'^rust/port/([^/]+)/src/(.*)\.rs$', target or '')
    if not m:
        return ''
    return m.group(1) + '::' + m.group(2).replace('/', '::')


# ------------------------------------------------------------------ nm
NM_MACHO = re.compile(r'^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?'
                      r'(?P<attrs>(?:weak\s+)?(?:private\s+)?)external\s+'
                      r'(?P<flags>(?:automatically hidden\s+)?(?:\[[^\]]*\]\s+)*)(?P<name>\S+)\s*$')
# the reader of migration/scripts/symdeps_seekdb.py: drops lines with flags after `external`
NM_SYMDEPS = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?"
                        r"(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$")
UNITY_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def list_objects():
    skipped = set()
    with open(SKIPPED_TSV, encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            if line.strip():
                skipped.add(line.split('\t')[0])
    objs = []
    for dirpath, dirnames, filenames in os.walk(BUILD_SRC):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.endswith('.o'):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), BUILD_SRC).replace(os.sep, '/')
            if rel in skipped:
                continue
            if '/CMakeFiles/' not in rel:
                sys.exit('make_seams: %s is neither kept by nor listed in %s' % (rel, SKIPPED_TSV))
            objs.append(rel)
    return objs, skipped


def object_sources(rel):
    if '/Unity/' in rel:
        with open(os.path.join(BUILD_SRC, rel[:-2]), encoding='utf-8', errors='replace') as fh:
            return [os.path.relpath(p, REPO).replace(os.sep, '/') for p in UNITY_INCLUDE.findall(fh.read())]
    owner, _, rest = rel.partition('/CMakeFiles/')
    source = rest.split('/', 1)[1][:-2].replace('__/', '../')
    return [os.path.normpath(os.path.join('src', owner, source)).replace(os.sep, '/')]


class NmObject(object):
    """External symbols of one object.  `old_*` are the sets the symdeps_seekdb.py reader sees (it drops
    every line with a `[...]` flag after `external`, e.g. `[no dead strip]` of __attribute__((used)))."""
    __slots__ = ('strong', 'weak', 'undef', 'flags', 'old_strong', 'old_weak', 'old_undef', 'unparsed')


def read_nm(rel):
    out = subprocess.run(['nm', '-m', '-g', os.path.join(BUILD_SRC, rel)], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, check=False).stdout.decode('utf-8', 'replace')
    o = NmObject()
    o.strong, o.weak, o.undef, o.flags, o.unparsed = set(), set(), set(), {}, 0
    o.old_strong, o.old_weak, o.old_undef = set(), set(), set()
    for line in out.splitlines():
        if not line.strip():
            continue
        m = NM_MACHO.match(line)
        if not m:
            o.unparsed += 1
            continue
        name, sect, attrs, flags = m.group('name'), m.group('sect'), m.group('attrs'), m.group('flags')
        seen_old = bool(NM_SYMDEPS.match(line))
        if sect == 'undefined':
            o.undef.add(name)
            if seen_old:
                o.old_undef.add(name)
        elif 'weak' in attrs.split():
            o.weak.add(name)
            if seen_old:
                o.old_weak.add(name)
        else:
            o.strong.add(name)
            if seen_old:
                o.old_strong.add(name)
            if flags.strip():
                o.flags[name] = ' '.join(flags.split())
    return o


def module_pairs(objs, mod_of, sets):
    """symdeps_seekdb.py's module edges.  sets: obj -> (strong, weak, undef).
    -> {(from_module, to_module, kind): {symbol}} with kind ref / weak_override."""
    strong_mods = collections.defaultdict(set)
    weak_mods = collections.defaultdict(set)
    undef_mods = collections.defaultdict(set)
    for o in objs:
        s, w, u = sets[o]
        for x in s:
            strong_mods[x].add(mod_of[o])
        for x in w:
            weak_mods[x].add(mod_of[o])
        for x in u:
            undef_mods[x].add(mod_of[o])
    pairs = collections.defaultdict(set)
    for x, mods in undef_mods.items():
        for a in mods:
            for b in strong_mods.get(x, ()):
                if a != b:
                    pairs[(a, b, 'ref')].add(x)
    for x, mods in weak_mods.items():
        owners = strong_mods.get(x, set())
        for w in mods - owners:
            for b in owners:
                pairs[(w, b, 'weak_override')].add(x)
    return pairs


def demangle(names):
    names = sorted(set(names))
    if not names:
        return {}
    plain = [n[1:] if n.startswith('_') else n for n in names]
    res = subprocess.run(['c++filt'], input='\n'.join(plain) + '\n', stdout=subprocess.PIPE,
                         universal_newlines=True, check=False).stdout.splitlines()
    if len(res) != len(names):
        sys.exit('make_seams: c++filt returned %d lines for %d names' % (len(res), len(names)))
    return dict(zip(names, res))


def read_relations():
    """{(from_module, to_module, kind): relation} from the depmap's crate report."""
    rel = {}
    with open(UNION_CYCLES, encoding='utf-8') as fh:
        for line in fh:
            p = line.rstrip('\n').split('\t')
            if len(p) >= 5 and p[0] in ('with', 'against', 'across'):
                rel[(p[1].split('/')[1], p[2].split('/')[1], p[3])] = (p[0], int(p[4]))
    return rel


# ------------------------------------------------------------------ demangled names
DEM_PREFIX = (('construction vtable for ', 'vtable'), ('vtable for ', 'vtable'), ('VTT for ', 'vtable'),
              ('typeinfo name for ', 'typeinfo'), ('typeinfo for ', 'typeinfo'),
              ('guard variable for ', 'guard'), ('non-virtual thunk to ', 'thunk'),
              ('virtual thunk to ', 'thunk'))


def split_top(s, sep):
    """Split s at top-level occurrences of sep (depth over <>, (), [])."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(s):
        if s.startswith('operator', i) and depth == 0:
            j = i + len('operator')
            m = re.match(r'\s*(\(\)|\[\]|<<=|>>=|<=>|<<|>>|<=|>=|->\*?|[-+*/%^&|~!=<>,]=?|&&|\|\||\+\+|--|'
                         r'new\[\]|delete\[\]|new|delete|""\s*\w+|\s[\w:]+)', s[j:])
            if m:
                cur.append(s[i:j + m.end()])
                i = j + m.end()
                continue
        ch = s[i]
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        if depth == 0 and s.startswith(sep, i):
            out.append(''.join(cur))
            cur = []
            i += len(sep)
            continue
        cur.append(ch)
        i += 1
    out.append(''.join(cur))
    return out


def strip_targs(name):
    """Remove template argument lists from one name component (keeps operator< etc.)."""
    if name.startswith('operator'):
        return name
    out, depth = [], 0
    for ch in name:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return ''.join(out).strip()


def parse_demangled(d):
    """-> dict(form, qual=[components], params=[str] or None, cv=str)."""
    form = 'func'
    for pfx, f in DEM_PREFIX:
        if d.startswith(pfx):
            form = f
            d = d[len(pfx):]
            break
    if form in ('vtable', 'typeinfo'):
        return {'form': form, 'qual': split_top(d, '::'), 'params': None, 'cv': ''}
    m = re.search(r'\)((?:\s*(?:const|volatile|&&|&))*)\s*$', d)
    if not m:
        return {'form': 'var' if form in ('func', 'guard') else form, 'qual': split_top(d, '::'),
                'params': None, 'cv': ''}
    close = m.start()
    depth, i = 0, close
    while i >= 0:
        if d[i] == ')':
            depth += 1
        elif d[i] == '(':
            depth -= 1
            if depth == 0:
                break
        i -= 1
    qual, inner = d[:i], d[i + 1:close]
    params = [p.strip() for p in split_top(inner, ',')] if inner.strip() else []
    if params == ['void']:
        params = []
    return {'form': 'thunk' if form == 'thunk' else 'func', 'qual': split_top(qual, '::'), 'params': params,
            'cv': ' '.join(m.group(1).split())}


def skel(t):
    """Comparable spelling of a parameter type: no cv-qualifiers, tags, inline namespace, literal
    suffixes (c++filt prints `1ll` where clang prints `1`) or blanks."""
    t = t.replace('std::__1::', 'std::')
    t = re.sub(r'\b(?:const|volatile|struct|class|enum|union|__restrict|restrict)\b', ' ', t)
    t = re.sub(r'\b(\d+)(?:ull|ul|ll|l|u)\b', r'\1', t)
    return re.sub(r'\s+', '', t)


def to_cxxfilt(t):
    """clang canonical type spelling -> c++filt spelling (`const T &` -> `T const&`)."""
    t = ' '.join(t.split())
    m = re.match(r'^const (.*?)\s*((?:[*&]\s*)+)$', t)
    if m:
        return m.group(1) + ' const' + m.group(2).replace(' ', '')
    if t.startswith('const ') and not re.search(r'[*&]\s*$', t):
        t = t[len('const '):]
    t = re.sub(r'\s*\*\s*const$', '*', t)
    return re.sub(r'\s+([*&]+)', r'\1', t)


# ------------------------------------------------------------------ AST database helpers
AST_KINDS = astdb.RECORD_KINDS + astdb.FUNC_KINDS + ('field', 'typedef', 'alias', 'var', 'enum',
                                                     'enum_constant')
CPP_WORDS = set('''alignas alignof and asm auto bool break case catch char char16_t char32_t class const
constexpr const_cast continue decltype default delete do double dynamic_cast else enum explicit export
extern false float for friend goto if inline int long mutable namespace new noexcept not nullptr operator
or private protected public register reinterpret_cast return short signed sizeof static static_assert
static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned
using virtual void volatile wchar_t while override final NULL'''.split())
SERVICE_RE = re.compile(r'\bserver_service\s*<|\bGCTX\s*\.|\bOBSERVER\s*\.|\bget_instance\s*\(|\bMTL\s*\(|'
                        r'\bget_\w*(?:manager|service|mgr)\w*\s*\(\s*\)')
QNAME_RE = re.compile(r'(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*')
USR_REF_RE = re.compile(r'\$((?:@(?:N|S|U)@[A-Za-z_]\w*)+)')
FOREIGN_TRAIT = {'serialize': 'ObSerialize', 'deserialize': 'ObSerialize', 'get_serialize_size': 'ObSerialize',
                 'serialize_': 'ObSerialize', 'deserialize_': 'ObSerialize', 'get_serialize_size_': 'ObSerialize',
                 'to_string': 'ObToString', 'operator==': 'PartialEq', 'operator!=': 'PartialEq',
                 'operator<': 'PartialOrd', 'operator<=': 'PartialOrd', 'operator>': 'PartialOrd',
                 'operator>=': 'PartialOrd', 'hash': 'ObHash'}
DEFINE_RE = re.compile(r'^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)', re.M)


def pos(r):
    return (r['file'], r['line'], r.get('col', 0))


class Ast(object):
    def __init__(self):
        self.db = astdb.AstDB(kinds=AST_KINDS)
        self.by_qname = self.db.by_qname_index
        self.by_usr = self.db.by_usr_index
        self.by_parent = self.db.by_parent_index
        self.rec_defs = collections.defaultdict(list)       # usr -> record definitions
        self.rec_defs_q = collections.defaultdict(list)     # qname -> record definitions
        for r in self.db.records:
            if r['kind'] in astdb.RECORD_KINDS and r.get('is_definition') and not r.get('explicit_instantiation'):
                self.rec_defs[r['usr']].append(r)
                self.rec_defs_q[r['qname']].append(r)
        for v in list(self.rec_defs.values()) + list(self.rec_defs_q.values()):
            v.sort(key=pos)
        self.spec_usrs = set(u for u, ds in self.rec_defs.items()
                             if any(d.get('specialization_of') and '<' in d['qname'] for d in ds))
        self.def_crates_q = {q: sorted({crate_of(d['file']) for d in ds}, key=rank)
                             for q, ds in self.rec_defs_q.items()}
        self.def_crates_u = {u: sorted({crate_of(d['file']) for d in ds}, key=rank)
                             for u, ds in self.rec_defs.items()}
        # records that are only ever forward-declared (opaque handles such as ObFTDocWordIterator)
        self.never_defined = {r['qname'] for r in self.db.records if r['kind'] in astdb.RECORD_KINDS and
                              not r.get('is_definition') and r['qname'] not in self.rec_defs_q and
                              r.get('generated', 0) == 0}
        # records defined only in .cpp/.c files: file-local, never named from another file
        self.cpp_local = {q for q, ds in self.rec_defs_q.items()
                          if all(re.search(r'\.(cpp|cc|c|cxx)$', d['file']) for d in ds)}
        # member function definitions by (class short name, member name)
        self.mdefs = collections.defaultdict(list)
        for r in self.db.records:
            if r['kind'] in astdb.METHOD_KINDS and r.get('is_definition') and r.get('parent_qname'):
                cls = strip_targs(split_top(r['parent_qname'], '::')[-1])
                self.mdefs[(cls, strip_targs(r['name']))].append(r)

    def record_defs(self, qname):
        return self.rec_defs_q.get(qname, [])

    def class_defs_for(self, qual):
        """Record definitions for a demangled scope (template arguments stripped)."""
        q = '::'.join(strip_targs(c) for c in qual)
        ds = self.rec_defs_q.get(q)
        if ds:
            return q, ds
        # explicit specialization written with arguments: keep the arguments of the last component
        q2 = '::'.join([strip_targs(c) for c in qual[:-1]] + [qual[-1]])
        return q, self.rec_defs_q.get(q2, [])


# ------------------------------------------------------------------ name maps for body analysis
class Names(object):
    """Where names are declared (types: defined), for the 'body uses only lower names' rule.
    A qualified token (`storage::ObTableScanIterator`) is resolved against fully qualified names; an
    unqualified one by kind precedence types > macros > functions > variables > enum constants (so an
    enum constant spelled like a class does not hide the class)."""

    ORDERED = ('types', 'macros', 'funcs', 'vars', 'consts')

    def __init__(self, ast, files):
        self.maps = {k: collections.defaultdict(set) for k in self.ORDERED}
        self.by_last = collections.defaultdict(set)          # last component -> {(qname, crate)}
        for r in ast.db.records:
            k = r['kind']
            c = crate_of(r['file'])
            nm = strip_targs(r['name'])
            which = None
            if k in astdb.RECORD_KINDS or k == 'enum':
                if r.get('is_definition') and not r.get('explicit_instantiation'):
                    which = 'types'
            elif k in ('typedef', 'alias'):
                which = 'types'
            elif k == 'enum_constant':
                which = 'consts'
            elif k in ('function', 'function_template'):
                if r.get('parent_usr') not in ast.rec_defs:          # namespace-scope functions only
                    which = 'funcs'
            elif k == 'var' and not r.get('is_member'):
                which = 'vars'
            if which:
                self.maps[which][nm].add(c)
                self.by_last[nm].add(('::'.join(strip_targs(x) for x in split_top(r['qname'], '::')), c))
        for f in files:
            c = crate_of(f)
            for nm in DEFINE_RE.findall(sweeplib.stripped(f)):
                self.maps['macros'][nm].add(c)

    def crates(self, qual, name):
        if qual:
            suffix = '::' + qual + '::' + name
            cs = {c for q, c in self.by_last.get(name, ()) if q.endswith(suffix) or q == qual + '::' + name}
            if cs:
                return cs
        for k in self.ORDERED:
            cs = self.maps[k].get(name)
            if cs:
                return cs
        return set()

    def upper_only(self, tokens, low):
        """Tokens ((qualifier, name)) whose every declaration is in a crate `low` cannot reach."""
        out = set()
        for qual, name in tokens:
            cs = self.crates(qual, name)
            if cs and not any(reaches(low, c) for c in cs):
                out.add(name)
        return sorted(out)


def body_text(rec):
    """Stripped text of a definition: [line, end_line] for functions; for variables (no end_line in the
    database) up to the first `;` outside brackets, so a multi-line initializer is included."""
    if not rec:
        return ''
    try:
        lines = sweeplib.stripped_lines(rec['file'])
    except OSError:
        return ''
    a = max(rec['line'] - 1, 0)
    if 'end_line' in rec or rec['kind'] != 'var':
        b = max(rec.get('end_line', rec['line']), rec['line'])
        return '\n'.join(lines[a:b])
    depth, out = 0, []
    for ln in lines[a:a + 400]:
        cut = None
        for i, ch in enumerate(ln):
            if ch in '({[':
                depth += 1
            elif ch in ')}]':
                depth -= 1
            elif ch == ';' and depth <= 0:
                cut = i
                break
        if cut is not None:
            out.append(ln[:cut])
            break
        out.append(ln)
    return '\n'.join(out)


QUAL_TOKEN_RE = re.compile(r'(?<![\w.])(?<!->)((?:[A-Za-z_]\w*\s*::\s*)*)([A-Za-z_]\w*)')


def body_tokens(text, exclude):
    """[(qualifier, name)] of identifiers not reached through `.` or `->`."""
    out = []
    for q, n in QUAL_TOKEN_RE.findall(text):
        if n in CPP_WORDS or n in exclude:
            continue
        q = re.sub(r'\s+', '', q).rstrip(':')
        q = re.sub(r'^(?:::)+', '', q)
        out.append((q, n))
    return out


CALL_UNQ_RE = re.compile(r'(?<![\w.:>])([A-Za-z_]\w*)\s*\(')
CALL_THIS_RE = re.compile(r'\bthis\s*->\s*([A-Za-z_]\w*)\s*\(')
CALL_QUAL_RE = re.compile(r'\b([A-Za-z_]\w*)\s*::\s*([A-Za-z_]\w*)\s*\(')


CALL_RECV_RE = re.compile(r'\b([A-Za-z_]\w*)\s*(?:\[[^\[\]]*\])?\s*(?:\.|->)\s*([A-Za-z_]\w*)\s*\(')
LOCAL_DECL_RE = re.compile(r'\b((?:[A-Za-z_]\w*::)*[A-Z]\w*)\s*(?:<[^;(){}]*>)?\s*(?:const\s*)?[*&]*\s*'
                           r'([a-z_]\w*)\s*(?=[;=({,\[)])')


def record_short(ast, ctype):
    """Short name of the first record type named in a canonical type spelling, or ''."""
    for q in QNAME_RE.findall(ctype or ''):
        if q in ast.def_crates_q or q in ast.never_defined:
            return strip_targs(q.split('::')[-1])
    return ''


_FIELD_TYPES = {}


def field_types(ast, cls_def):
    if cls_def is None:
        return {}
    u = cls_def['usr']
    if u not in _FIELD_TYPES:
        m = {}
        try:
            fs = ast.db.fields(u, inherited=True)
        except Exception:      # noqa: BLE001 - template records without resolvable bases
            fs = []
        for f in fs:
            t = record_short(ast, f.get('canonical_type', ''))
            if t:
                m[f['name']] = t
        _FIELD_TYPES[u] = m
    return _FIELD_TYPES[u]


def body_calls(text, cls_short, varmap):
    """{(scope, name)} called from a body: an unqualified call is a member of the body's own class or a
    namespace-scope function (scope ''), `this->f(` a member of the own class, `X::f(` a member of X or
    a function of namespace X, `v.f(` / `v->f(` / `v[i].f(` a member of v's record type when v is a
    parameter, a field of the class or a local declared in the body."""
    out = set()
    for n in CALL_UNQ_RE.findall(text):
        if n not in CPP_WORDS:
            out.add(('', n))
            if cls_short:
                out.add((cls_short, n))
    if cls_short:
        for n in CALL_THIS_RE.findall(text):
            out.add((cls_short, n))
    for c, n in CALL_QUAL_RE.findall(text):
        out.add((c, n))
    for v, n in CALL_RECV_RE.findall(text):
        t = varmap.get(v)
        if t:
            out.add((t, n))
    return out


# ------------------------------------------------------------------ rows
class Row(object):
    __slots__ = ('key', 'symbol', 'kind', 'decl', 'defn', 'decl_crate', 'def_crate', 'callers', 'shape',
                 'proposed', 'landing', 'pops', 'ev', 'cls', 'cls_def', 'recs', 'name', 'params_recs',
                 'is_static', 'ref_crates', 'body', 'edges', 'objs', 'free', 'fwd')

    def __init__(self, key):
        self.key = key
        self.symbol = self.kind = ''
        self.decl = self.defn = None          # (file, line)
        self.decl_crate = self.def_crate = '?'
        self.callers = (0, [], '')
        self.shape = self.proposed = self.landing = ''
        self.pops = set()
        self.ev = collections.OrderedDict()
        self.cls = None                       # class qname for members
        self.cls_def = None                   # class definition record
        self.recs = []                        # AST records of the symbol
        self.name = ''
        self.params_recs = []
        self.is_static = False
        self.ref_crates = set()
        self.body = None                      # definition record whose text is analysed
        self.edges = set()
        self.objs = set()
        self.free = False
        self.fwd = None


def first(recs):
    return sorted(recs, key=pos)[0] if recs else None


def lowest(recs):
    return sorted(recs, key=lambda r: (rank(crate_of(r['file'])),) + pos(r))[0] if recs else None


# ------------------------------------------------------------------ link population
def match_function(ast, parsed):
    """AST records of a demangled function/method; (records, class_qname or None, class_defs)."""
    qual = parsed['qual']
    name = qual[-1]
    scope = qual[:-1]
    cls_q, cls_defs = (None, [])
    if scope:
        cls_q, cls_defs = ast.class_defs_for(scope)
    cands = []
    # explicit specializations (members of a class template instantiation, function templates) are
    # recorded under their fully written name, e.g. ObCenterWithBuf<oceanbase::common::ObCenterId>::f
    exact_q = [r for r in ast.by_qname.get('::'.join(qual), []) if r['kind'] in astdb.FUNC_KINDS]
    if exact_q and '<' in '::'.join(qual):
        cands = exact_q
    elif cls_defs:
        base = strip_targs(name)
        for d in cls_defs:
            for r in ast.by_parent.get(d['usr'], []):
                if r['kind'] in astdb.FUNC_KINDS and strip_targs(r['name']) == base:
                    cands.append(r)
        # out-of-line definitions are parented to the class usr as well; nothing else to add
    else:
        q = '::'.join([strip_targs(c) for c in scope] + [strip_targs(name)])
        for r in ast.by_qname.get(q, []):
            if r['kind'] in ('function', 'function_template'):
                cands.append(r)
        if not cands:
            for r in ast.by_qname.get('::'.join(scope + [name]), []):
                if r['kind'] in ('function', 'function_template'):
                    cands.append(r)
    params = parsed['params'] or []
    want = [skel(p) for p in params]
    want_t = [skel(strip_all_targs(p)) for p in params]
    is_const = 'const' in parsed['cv'].split()
    by_arity = [r for r in cands if len(r.get('params', [])) == len(params)]
    exact = [r for r in by_arity if [skel(p.get('canonical_type', '')) for p in r.get('params', [])] == want]
    how = 'exact'
    if not exact:
        # clang drops default template arguments that c++filt prints: compare without argument lists
        exact = [r for r in by_arity
                 if [skel(strip_all_targs(p.get('canonical_type', ''))) for p in r.get('params', [])] == want_t]
        how = 'exact-modulo-template-args'
    pool = exact or by_arity
    if cls_defs and pool:
        constq = [r for r in pool if bool(r.get('is_const')) == is_const or r['kind'] in ('ctor', 'dtor')]
        pool = constq or pool
    if not exact and len({r['usr'] for r in pool}) > 1:
        # several overloads with the same arity and no spelling match: ambiguous, keep the first usr
        u = first(pool)['usr']
        pool = [r for r in pool if r['usr'] == u]
    return pool, cls_q, cls_defs, (how if exact else ('arity' if pool else 'unmatched'))


def strip_all_targs(t):
    out, depth = [], 0
    for ch in t:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return ''.join(out)


def symbol_kind(parsed, recs, cls_defs):
    f = parsed['form']
    if f in ('vtable', 'typeinfo'):
        return f
    if f == 'var':
        return 'var'
    kinds = {r['kind'] for r in recs}
    name = strip_targs(parsed['qual'][-1])
    if 'dtor' in kinds or name.startswith('~'):
        return 'dtor'
    if 'ctor' in kinds or (cls_defs and parsed['qual'] and len(parsed['qual']) > 1 and
                           name == strip_targs(parsed['qual'][-2])):
        return 'ctor'
    if name in FOREIGN_TRAIT and (cls_defs or kinds & set(astdb.METHOD_KINDS)):
        return 'impl-body'
    if cls_defs or kinds & {'method', 'conversion'}:
        return 'method'
    return 'function'


def key_function_def(ast, cls_def, def_crate):
    """Definition record of the class's key function (first non-inline, non-pure virtual method)."""
    members = ast.by_parent.get(cls_def['usr'], [])
    decls = sorted([r for r in members if r['kind'] in astdb.METHOD_KINDS and not r.get('out_of_line')],
                   key=pos)
    for r in decls:
        if r.get('is_virtual') and not r.get('is_pure') and not r.get('is_inline') and \
                not r.get('is_defaulted') and not r.get('is_deleted'):
            defs = [x for x in members if x['usr'] == r['usr'] and x.get('is_definition') and x.get('out_of_line')]
            if defs:
                return first(defs)
    outs = [x for x in members if x['kind'] in astdb.METHOD_KINDS and x.get('is_definition') and
            x.get('out_of_line') and crate_of(x['file']) == def_crate]
    return first(outs)


def build_link_rows(ast, objs, nm, relations, report, src_cache):
    mod_of = {o: o.split('/')[0] for o in objs}
    strong_objs = collections.defaultdict(set)
    weak_objs = collections.defaultdict(set)
    undef_objs = collections.defaultdict(set)
    flags = {}
    old_dropped = set()
    for o in objs:
        r = nm[o]
        for s in r.strong:
            strong_objs[s].add(o)
        for s in r.weak:
            weak_objs[s].add(o)
        for s in r.undef:
            undef_objs[s].add(o)
        flags.update(r.flags)
        old_dropped |= (r.strong - r.old_strong) | (r.weak - r.old_weak) | (r.undef - r.old_undef)
    # module-level edges as symdeps_seekdb.py computes them, with this reader and with its own,
    # tagged with the depmap's relation (with / against / across) of the module pair
    pairs = module_pairs(objs, mod_of, {o: (nm[o].strong, nm[o].weak, nm[o].undef) for o in objs})
    pairs_old = module_pairs(objs, mod_of, {o: (nm[o].old_strong, nm[o].old_weak, nm[o].old_undef) for o in objs})
    population = {}
    edge_counts = collections.Counter()
    old_counts = collections.Counter()
    for (a, b, k), syms in pairs.items():
        rel = relations.get((a, b, k), ('?', 0))[0]
        if rel not in ('against', 'across'):
            continue
        edge_counts[(rel, a, b, k)] = len(syms)
        old_counts[(rel, a, b, k)] = len(pairs_old.get((a, b, k), ()))
        for s in syms:
            population.setdefault(s, set()).add((a, b, k, rel))
    for (a, b, k), (rel, n) in relations.items():
        if rel in ('against', 'across') and old_counts[(rel, a, b, k)] != n:
            sys.exit('make_seams: %s -> %s %s: %d symbols with the symdeps reader, depmap says %d'
                     % (a, b, k, old_counts[(rel, a, b, k)], n))
    report['edges'] = edge_counts
    report['edges_old'] = old_counts
    names = demangle(population)
    rows = {}
    for s in sorted(population):
        d = names[s]
        parsed = parse_demangled(d)
        row = Row(('link', s))
        row.symbol = d
        row.pops.add('link')
        row.edges = population[s]
        recs, cls_q, cls_defs, exact = [], None, [], "unmatched"
        form = parsed['form']
        if form in ('func', 'thunk'):
            recs, cls_q, cls_defs, exact = match_function(ast, parsed)
        elif form in ('vtable', 'typeinfo'):
            cls_q, cls_defs = ast.class_defs_for(parsed['qual'])
        elif form == 'var':
            q = '::'.join(strip_targs(c) for c in parsed['qual'])
            recs = [r for r in ast.by_qname.get(q, []) if r['kind'] == 'var']
            if len(parsed['qual']) > 1:
                cls_q, cls_defs = ast.class_defs_for(parsed['qual'][:-1])
        row.kind = symbol_kind(parsed, recs, cls_defs)
        row.recs = recs
        row.name = strip_targs(parsed['qual'][-1])
        row.cls = cls_q if cls_defs else None
        row.cls_def = lowest(cls_defs) if cls_defs else None
        def_mods = sorted({mod_of[o] for o in strong_objs[s]})
        def_objs = sorted(strong_objs[s])
        def_srcs = {x for o in def_objs for x in src_cache.setdefault(o, object_sources(o))}
        # definition record: a non-weak definition compiled into one of the defining objects
        defs = [r for r in recs if r.get('is_definition') and not r.get('weak') and r['file'] in def_srcs]
        if not defs:
            defs = [r for r in recs if r.get('is_definition') and not r.get('weak')]
        defn = first(defs)
        if form in ('vtable', 'typeinfo') and row.cls_def is not None:
            defn = key_function_def(ast, row.cls_def, module_crate(def_mods[0]))
        row.body = defn
        if defn is not None:
            row.defn = (defn['file'], defn['line'])
            row.def_crate = crate_of(defn['file'])
        else:
            srcs = [x for o in def_objs for x in object_sources(o)]
            row.defn = (srcs[0] if srcs else def_objs[0], 0)
            row.def_crate = module_crate(def_mods[0])
        # declaration
        if row.cls_def is not None and row.kind in ('vtable', 'typeinfo'):
            row.decl = (row.cls_def['file'], row.cls_def['line'])
            row.decl_crate = crate_of(row.cls_def['file'])
        elif row.cls_def is not None:
            ins = [r for r in recs if not r.get('out_of_line') and not r.get('is_definition')] or \
                  [r for r in recs if not r.get('out_of_line')]
            d0 = first(ins) or row.cls_def
            row.decl = (d0['file'], d0['line'])
            row.decl_crate = crate_of(row.cls_def['file'])
        else:
            # the declaration callers see: lowest crate first; there a header declaration before a
            # hand-written .cpp prototype before a weak default definition
            cands = [r for r in recs if not r.get('is_definition') or r.get('weak')]
            cands.sort(key=lambda r: (rank(crate_of(r['file'])), bool(r.get('is_definition')),
                                      not re.search(r'\.(h|hpp|ipp|inl)$', r['file'])) + pos(r))
            d0 = cands[0] if cands else defn
            if d0 is not None:
                row.decl = (d0['file'], d0['line'])
                row.decl_crate = crate_of(d0['file'])
            else:
                row.decl = ('?', 0)
                row.decl_crate = '?'
            weak = [r for r in recs if r.get('is_definition') and r.get('weak')]
            if weak:
                row.ev['weak-default'] = '%s:%d' % (first(weak)['file'], first(weak)['line'])
        row.is_static = any(r.get('is_static') for r in recs)
        row.params_recs = (first([r for r in recs if not r.get('out_of_line')]) or first(recs) or {}).get('params', [])
        # callers: referencing objects whose crate cannot reach the definition crate (weak definers too)
        ref_objs = set(undef_objs.get(s, ())) | {o for o in weak_objs.get(s, ()) if mod_of[o] not in def_mods}
        viol = sorted(o for o in ref_objs if not reaches(module_crate(mod_of[o]), row.def_crate))
        row.objs = set(viol)
        row.ref_crates = {module_crate(mod_of[o]) for o in viol}
        ev = row.ev
        ev['pop'] = None
        ev['edges'] = ','.join(sorted('%s->%s%s' % (a, b, '(weak)' if k == 'weak_override' else '')
                                      for a, b, k, _ in population[s]))
        ev['nm-def'] = ','.join(def_objs[:2]) + ('+%d' % (len(def_objs) - 2) if len(def_objs) > 2 else '')
        if s in flags:
            ev['nm-flags'] = flags[s]
        if s in old_dropped:
            ev['symdeps'] = 'dropped-by-symdeps-reader'
        if form in ('func', 'thunk'):
            ev['ast'] = exact
            if recs:
                ev['usr'] = first(recs)['usr']
        elif cls_defs:
            ev['ast'] = 'class'
        else:
            ev['ast'] = 'unmatched'
        rows[row.key] = row
    return rows, undef_objs, strong_objs, weak_objs, mod_of


def caller_names(row):
    cls_short = strip_targs(row.cls.split('::')[-1]) if row.cls else ''
    if row.kind in ('vtable', 'typeinfo', 'ctor', 'dtor'):
        return [cls_short] if cls_short else [row.name.lstrip('~')]
    if row.name.startswith('operator'):
        return [cls_short] if cls_short else []
    return [n for n in (cls_short, row.name) if n]


_TOKENS = {}


IDENT_RE = re.compile(r'[A-Za-z_]\w*')


def file_tokens(f):
    """Every identifier of a file (comments and strings stripped), member names after . and -> included."""
    if f not in _TOKENS:
        _TOKENS[f] = frozenset(IDENT_RE.findall(sweeplib.stripped(f)))
    return _TOKENS[f]


_HEADERS_BY_MODULE = None


def module_headers(module):
    global _HEADERS_BY_MODULE
    if _HEADERS_BY_MODULE is None:
        _HEADERS_BY_MODULE = collections.defaultdict(list)
        for f in sweeplib.first_party(('.h', '.hpp', '.ipp', '.inl')):
            _HEADERS_BY_MODULE[f.split('/')[1]].append(f)
    return _HEADERS_BY_MODULE.get(module, [])


FILE_EDGES = os.path.join(DEPMAP, 'edges.tsv')
_REV_INC = None
_INCLUDERS = {}


def includers(header):
    """Files that include `header` directly or transitively (depmap file-level include edges)."""
    global _REV_INC
    if _REV_INC is None:
        _REV_INC = collections.defaultdict(set)
        with open(FILE_EDGES, encoding='utf-8') as fh:
            next(fh)
            for line in fh:
                p = line.split('\t')
                if 'inc' in p[2].split(',') or 'angle' in p[2].split(','):
                    _REV_INC[p[1]].add(p[0])
    if header not in _INCLUDERS:
        seen, todo = set(), [header]
        while todo:
            for f in _REV_INC.get(todo.pop(), ()):
                if f not in seen:
                    seen.add(f)
                    todo.append(f)
        _INCLUDERS[header] = seen
    return _INCLUDERS[header]


def fill_callers(row, src_cache):
    """Caller files, best evidence first:
      1. sources of the violating referencing objects, and headers of their modules (inline code
         compiled into them), that name the symbol (class and member name for members);
      2. ('via-include') sources that include the declaring header, directly or transitively, and name
         the member (calls through a typedef'd receiver, wrapper templates such as ex_rpc::sync_call<>);
      3. ('via-include') sources that include the declaring header (implicit calls: constructors,
         destructors, serialization macros);
      4. ('objects') the referencing objects themselves."""
    names = caller_names(row)
    member = [row.name] if row.name and not row.name.startswith(('operator', '~')) and \
        row.kind not in ('ctor', 'dtor', 'vtable', 'typeinfo') else []
    decl = (row.decl or ('', 0))[0]
    skip = {(row.defn or ('', 0))[0]} | ({decl} if decl.endswith(('.h', '.hpp', '.ipp', '.inl')) else set())
    srcs = []
    for o in sorted(row.objs):
        for f in src_cache.setdefault(o, object_sources(o)):
            if f not in skip and os.path.exists(os.path.join(REPO, f)):
                srcs.append(f)
    files = {f for f in srcs if names and all(n in file_tokens(f) for n in names)}
    if not files and names:
        for m in sorted({o.split('/')[0] for o in row.objs}):
            for f in module_headers(m):
                if f not in skip and all(n in file_tokens(f) for n in names):
                    files.add(f)
    label = ''
    if not files and decl.endswith(('.h', '.hpp', '.ipp', '.inl')):
        inc = includers(decl)
        cands = [f for f in srcs if f in inc]
        files = {f for f in cands if member and all(n in file_tokens(f) for n in member)} or set(cands)
        label = 'via-include'
    if files:
        fs = sorted(files, key=lambda f: (rank(crate_of(f)), f))
        row.callers = (len(fs), fs[:3], label)
    elif row.objs:
        os_ = sorted(row.objs)
        row.callers = (len(os_), os_[:3], 'objects')
    else:
        row.callers = (0, [], '')


# ------------------------------------------------------------------ body population
def render_symbol(rec):
    q = rec['qname']
    if rec['kind'] == 'var':
        return q
    params = ', '.join(to_cxxfilt(p.get('canonical_type', p.get('type', ''))) for p in rec.get('params', []))
    s = '%s(%s)' % (q, params)
    if rec.get('is_const'):
        s += ' const'
    return s


def build_body_rows(ast, link_rows):
    """Out-of-line member definitions whose class lives in another crate."""
    by_def = {}
    for row in link_rows.values():
        if row.body is not None:
            by_def[(row.body['usr'], row.body['file'], row.body['line'])] = row
    rows = {}
    for r in ast.db.records:
        k = r['kind']
        if k in astdb.METHOD_KINDS:
            if not (r.get('is_definition') and r.get('out_of_line')):
                continue
        elif k == 'var':
            if not (r.get('is_definition') and r.get('is_member')):
                continue
        else:
            continue
        defs = ast.rec_defs.get(r.get('parent_usr'))
        if not defs and '<' in r.get('parent_qname', ''):
            # member of an implicit instantiation (explicit member specialization): the class template
            defs = ast.rec_defs_q.get('::'.join(strip_targs(c) for c in split_top(r['parent_qname'], '::')))
        if not defs:
            continue
        ccr = {crate_of(d['file']) for d in defs}
        bcr = crate_of(r['file'])
        if bcr in ccr:
            continue
        cls_def = lowest(defs)
        hit = by_def.get((r['usr'], r['file'], r['line']))
        if hit is not None:
            hit.pops.add('body')
            continue
        row = Row(('body', r['usr'], r['file'], r['line']))
        row.pops.add('body')
        row.symbol = render_symbol(r)
        name = strip_targs(r['name'])
        if k == 'dtor':
            row.kind = 'dtor'
        elif k == 'ctor':
            row.kind = 'ctor'
        elif k == 'var':
            row.kind = 'var'
        elif name in FOREIGN_TRAIT:
            row.kind = 'impl-body'
        else:
            row.kind = 'method'
        row.name = name
        row.cls = cls_def['qname']
        row.cls_def = cls_def
        decls = [x for x in ast.by_usr.get(r['usr'], []) if not x.get('out_of_line') and
                 x['kind'] == k and not (k == 'var' and x.get('is_definition'))]
        d0 = first(decls) or cls_def
        row.decl = (d0['file'], d0['line'])
        row.decl_crate = crate_of(cls_def['file'])
        row.defn = (r['file'], r['line'])
        row.def_crate = bcr
        row.body = r
        row.recs = [r] + decls
        row.is_static = any(x.get('is_static') for x in row.recs)
        row.params_recs = (d0 if d0 is not cls_def else r).get('params', [])
        row.ev['pop'] = None
        row.ev['usr'] = r['usr']
        if r.get('macro'):
            row.ev['macro'] = r['macro']
        rows[row.key] = row
    return rows


# ------------------------------------------------------------------ fwd population
def type_refs(ast, rec):
    """(qname-or-usr, how) of record types named by one declaration record."""
    k = rec['kind']
    strs = []
    if k in ('field', 'var', 'typedef', 'alias'):
        strs.append(('member' if k in ('field', 'var') else 'signature', rec.get('canonical_type', '')))
    elif k in astdb.FUNC_KINDS:
        strs.append(('signature', rec.get('canonical_return_type', '')))
        for p in rec.get('params', []):
            strs.append(('signature', p.get('canonical_type', '')))
    out = []
    for how, s in strs:
        for q in set(QNAME_RE.findall(s or '')):
            if q in ast.def_crates_q:
                out.append((q, how))
    if k in astdb.RECORD_KINDS and rec.get('specialization_of'):
        for g in USR_REF_RE.findall(rec['usr']):
            u = 'c:' + g
            if u in ast.def_crates_u:
                out.append((ast.rec_defs[u][0]['qname'], 'spec'))
    return out


def in_spec(ast, rec):
    if rec['kind'] in astdb.RECORD_KINDS and rec.get('specialization_of') and '<' in rec['qname']:
        return True
    if rec['kind'] in astdb.FUNC_KINDS and (rec.get('specialization_of') or rec.get('template_args')):
        return True
    u = rec.get('parent_usr')
    for _ in range(4):
        if not u:
            return False
        if u in ast.spec_usrs:
            return True
        ds = ast.rec_defs.get(u)
        u = ds[0].get('parent_usr') if ds else None
    return False


def build_fwd_rows(ast):
    uses = collections.defaultdict(list)      # (T, L) -> [(file, line, how, owner)]
    for r in ast.db.records:
        if r['kind'] not in ('field', 'var', 'typedef', 'alias') + astdb.FUNC_KINDS + astdb.RECORD_KINDS:
            continue
        low = crate_of(r['file'])
        if low == '?':
            continue
        refs = type_refs(ast, r)
        if not refs:
            continue
        spec = in_spec(ast, r)
        for q, how in refs:
            dcs = ast.def_crates_q.get(q, [])
            if not dcs or low in dcs or any(reaches(low, c) for c in dcs):
                continue
            if q in ast.cpp_local:          # e.g. a template parameter spelled like a .cpp-local struct
                continue
            uses[(q, low)].append((r['file'], r['line'], 'spec' if spec or how == 'spec' else how,
                                   r.get('parent_qname', '') or r['qname']))
    rows, findings = {}, collections.defaultdict(list)
    for (q, low), us in uses.items():
        us = sorted(set(us))
        # forward declarations visible to the lower crate: its own first, then those of crates it reaches
        fwd = sorted([x for x in ast.by_qname.get(q, []) if x['kind'] in astdb.RECORD_KINDS and
                      not x.get('is_definition') and reaches(low, crate_of(x['file']))],
                     key=lambda x: (crate_of(x['file']) != low, rank(crate_of(x['file']))) + pos(x))
        if not fwd:
            findings[low].append((q, us))
            continue
        defs = sorted(ast.rec_defs_q[q], key=lambda d: (rank(crate_of(d['file'])),) + pos(d))
        row = Row(('fwd', q, low))
        row.pops.add('fwd')
        row.symbol = q
        row.kind = 'fwd-type'
        row.cls = q
        row.cls_def = defs[0]
        d0 = fwd[0]
        if crate_of(d0['file']) == low:
            row.decl = (d0['file'], d0['line'])
        else:                                   # declared by a crate below: the first use is the decl here
            row.decl = (us[0][0], us[0][1])
        row.decl_crate = low
        row.defn = (defs[0]['file'], defs[0]['line'])
        row.def_crate = crate_of(defs[0]['file'])
        cnt = collections.Counter(u[2] for u in us)
        row.fwd = {'uses': us, 'counts': cnt, 'has_fwd': bool(fwd), 'defs': defs}
        sites = sorted({(u[0], u[1]) for u in us}, key=lambda x: (rank(crate_of(x[0])), x))
        row.callers = (len(sites), ['%s:%d' % s for s in sites[:3]], 'uses')
        row.ev['pop'] = None
        row.ev['uses'] = 'member=%d spec=%d signature=%d' % (cnt['member'], cnt['spec'], cnt['signature'])
        if crate_of(d0['file']) != low:
            row.ev['fwd-decl'] = '%s:%d(%s)' % (d0['file'], d0['line'], crate_of(d0['file']))
        others = sorted({crate_of(d['file']) for d in defs} - {row.def_crate}, key=rank)
        if others:
            row.ev['also-defined-in'] = ','.join(others)
        rows[row.key] = row
    return rows, findings


# ------------------------------------------------------------------ proto population
def build_proto_rows(ast, link_rows):
    sweep = {}
    with open(C13_SWEEP, encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            p = line.rstrip('\n').split('\t')
            sweep[(p[0], p[1], p[2])] = int(p[3])
    by_q = {}
    for row in link_rows.values():
        for r in row.recs:
            by_q.setdefault(r['qname'], row)
    rows, outcome = {}, []                    # outcome: c13 rows that c13 sent to the seam table
    with open(C13_SHARD, encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            p = line.rstrip('\n').split('\t')
            if len(p) < 5 or p[3] != 'EXTERN_PROTO':
                continue
            f, sym, cons = p[0], p[1], p[2]
            flagged = 'not reachable' in p[4]
            ln = sweep.get((f, sym, cons))
            if ln is None:
                if flagged:
                    outcome.append((f, sym, 'not in the c13 sweep file'))
                continue
            caller = crate_of(f)
            want = 'var' if 'variable' in cons else 'function'
            here = [r for r in _records_at(ast, f, ln) if r['kind'] == want and not r.get('is_definition')]
            if not here:
                if flagged:
                    locs = re.findall(r'definition (src/[^ ;]+):(\d+)', p[5] if len(p) > 5 else '')
                    members = [r for fl, l in locs for r in _records_at(ast, fl, int(l))
                               if r['kind'] in astdb.METHOD_KINDS]
                    why = ('c13 matched same-named member functions (%s), the prototype names the C library '
                           'function' % ', '.join(sorted({r['qname'] for r in members})[:3])) if members else \
                        'no declaration at %s:%d in the AST database' % (f, ln)
                    outcome.append((f, sym, 'not a seam: %s; file not compiled on macOS, unit %s' % (
                        why, disposition_of(f) or '?')))
                continue
            q = here[0]['qname']
            defs = [r for r in ast.by_qname.get(q, []) if r['kind'] == here[0]['kind'] and
                    r.get('is_definition') and r['file'] != f]
            dcr = sorted({crate_of(r['file']) for r in defs}, key=rank)
            if not dcr or any(reaches(caller, c) for c in dcr):
                if flagged:
                    outcome.append((f, sym, 'not a seam: %s' % (
                        'no first-party definition of %s (c13 matched a member function by name)' % q if not dcr
                        else 'definition in %s, reachable from %s' % (','.join(dcr), caller))))
                continue
            hit = by_q.get(q)
            if hit is not None:
                hit.pops.add('proto')
                hit.ev.setdefault('proto', '%s:%d' % (f, ln))
                outcome.append((f, sym, 'link row ' + hit.symbol))
                continue
            outcome.append((f, sym, 'proto row %s' % q))
            d = first(defs)
            row = Row(('proto', q))
            row.pops.add('proto')
            row.symbol = render_symbol(d)
            row.kind = 'function' if d['kind'] == 'function' else 'var'
            row.name = d['name']
            row.decl = (f, ln)
            row.decl_crate = caller
            row.defn = (d['file'], d['line'])
            row.def_crate = crate_of(d['file'])
            row.body = d if d['kind'] == 'function' else None
            row.recs = [d] + here
            row.params_recs = d.get('params', [])
            text = sweeplib.stripped(f)
            calls = [m for m in re.finditer(r'\b%s\s*\(' % re.escape(d['name']), text)
                     if sweeplib.line_of(f, m.start()) != ln]
            row.callers = (1, [f], '') if calls else (0, [], '')
            row.ref_crates = {caller} if calls else set()
            row.ev['pop'] = None
            row.ev['c13'] = '%s:%d EXTERN_PROTO' % (f, ln)
            if not calls:
                row.ev['calls'] = 'none (prototype unused; nm: no undefined reference)'
            rows[row.key] = row
    return rows, outcome


_AT = None


def _records_at(ast, f, ln):
    global _AT
    if _AT is None:
        _AT = collections.defaultdict(list)
        for r in ast.db.records:
            _AT[(r['file'], r['line'])].append(r)
    return _AT.get((f, ln), [])


# ------------------------------------------------------------------ classification
VECTOR_TRAITS = (('ObVectorIndexUtil', 'VectorIndexUtil'), ('ObPluginVectorIndexAdaptor', 'VectorIndexAdaptor'))
PUSHDOWN_DECL = 'src/query/api/query/engine/basic/ob_pushdown_filter.h'
EXPR_DECL = 'src/query/api/query/engine/expr/ob_expr.h'


def pimpl_fields(ast, cls_def):
    """Pointer fields of the class that its own crate cannot complete: `void *` or a record defined only
    above (or never defined).  These are the cross-layer pimpl candidates of RULEBOOK 0.4."""
    if cls_def is None:
        return []
    low = crate_of(cls_def['file'])
    out = []
    for f in ast.db.fields(cls_def['usr']):
        if f.get('canonical_kind') == 'pointer' or f.get('type_kind') == 'pointer':
            pq = f.get('pointee_qname', '')
            dcs = ast.def_crates_q.get(pq)
            if pq == 'void' or pq in ast.never_defined or (dcs and not any(reaches(low, c) for c in dcs)):
                out.append(f['name'])
    return sorted(set(out))


def uses_pimpl(ast, row, text):
    """The body touches a pimpl field directly, or (destructors) through the class's own
    reset/destroy/release/clear whose body does."""
    fields = pimpl_fields(ast, row.cls_def)
    if not fields:
        return []
    pat = re.compile(r'\b(?:%s)\b' % '|'.join(re.escape(f) for f in fields))
    if pat.search(text):
        return fields
    # one level of indirection: own-class helpers (reset(), destroy()) and accessor classes
    # (ObTxExecResultAccess::destroy(*this)) whose bodies touch the field
    own = strip_targs(row.cls.split('::')[-1]) if row.cls else ''
    callees = {(own, n) for n in CALL_UNQ_RE.findall(text)} | set(CALL_QUAL_RE.findall(text))
    for key in sorted(callees):
        for r in ast.mdefs.get(key, ()):
            if pat.search(body_text(r)):
                return fields
    return []


def opaque_params(ast, row):
    """Record types of pointer/reference params (and return) that the declaring crate cannot complete."""
    out = []
    low = row.decl_crate
    for p in row.params_recs or []:
        if p.get('kind') in ('pointer', 'lvalue_ref', 'rvalue_ref') or '*' in p.get('canonical_type', '') or \
                '&' in p.get('canonical_type', ''):
            for q in QNAME_RE.findall(p.get('canonical_type', '')):
                dcs = ast.def_crates_q.get(q)
                if (dcs and low not in dcs and not any(reaches(low, c) for c in dcs)) or q in ast.never_defined:
                    out.append(q)
    return sorted(set(out))


def per_row(row):
    n = row.name
    return n in ('das_scan_next_row', 'das_scan_next_rows') or n.startswith('spill_row_store_')


def edge_override(row):
    """RULEBOOK 0.4 per-edge table, symbol- or class-level.  -> (proposed, landing_note, rule) or None."""
    n, cls = row.name, row.cls or ''
    q = row.symbol
    dfile = (row.defn or ('', 0))[0]
    decl_file = (row.decl or ('', 0))[0]
    if n == 'ob_strerror' and row.decl_crate == 'oblib':
        return ('move-down', 'errno table generated into oblib (RULEBOOK 0.13)', 'edge:oblib->share:ob_strerror')
    if n == 'get_cpu_count' and row.decl_crate == 'oblib':
        return ('move-down', 'reads config::cpu_count() directly', 'edge:oblib->share:get_cpu_count')
    if n in ('allow_next_syslog', 'current_resource_owner_id') and row.decl_crate == 'oblib':
        return ('trait-slot', 'OnceLock<fn> hook, default = weak body, share registers',
                'edge:oblib->share:hook')
    if n == 'create_inner_sql_connection_for_proxy':
        return ('trait-slot', 'InnerConnFactory; empty slot -> OB_NOT_SUPPORTED', 'edge:oblib->observer')
    if n == 'request_finish_callback':
        return ('trait-slot', 'oblib hook request_finish_callback (default no-op), storage registers',
                'edge:oblib->storage')
    if cls.endswith('::ObIOManager') and n == 'print_service_status':
        return ('move-down', '', 'edge:share->observer:print_service_status')
    if q.startswith('oceanbase::ex_rpc::'):
        return ('trait-slot', 'TaskDispatcher', 'edge:share->observer:ex_rpc')
    if cls.endswith('::ObSqlEndTransCb'):
        return ('move-down', 'end_trans_cb into sql', 'edge:->observer:end_trans_cb')
    if cls.endswith('::ObReqTimeInfo') or cls.endswith('::ObGlobalReqTimeService'):
        return ('move-down', 'req_time into query', 'edge:->observer:req_time')
    if cls.endswith('::ObInnerSQLConnectionAccess'):
        return ('trait-slot', 'InnerSqlAccess', 'edge:->observer:inner_sql')
    if decl_file.endswith('data_plane/report/ob_tablet_report.h') and row.def_crate == 'observer':
        return ('trait-slot', 'TabletReport', 'edge:storage->observer:tablet_report')
    if decl_file.endswith('data_plane/ddl/ob_ddl_coordinator.h') and row.def_crate == 'rootserver':
        return ('trait-slot', 'DdlCoordinator', 'edge:->rootserver:ddl_coordinator')
    return None


def family(row):
    dfile = (row.defn or ('', 0))[0]
    decl_file = (row.decl or ('', 0))[0]
    cls_file = row.cls_def['file'] if row.cls_def else ''
    if dfile.startswith('src/observer/vector_index/'):
        return 'vector-index'
    if row.def_crate == 'sql' and (PUSHDOWN_DECL in (decl_file, cls_file) or EXPR_DECL in (decl_file, cls_file)):
        return 'pushdown'
    return ''


class Info(object):
    """Per-row body analysis used by the movability fixpoint and by decide()."""
    __slots__ = ('text', 'upper', 'opq', 'calls', 'pimpl', 'service', 'movable', 'why')


def analyze(ast, names, row):
    info = Info()
    info.text = body_text(row.body)
    cls_short = strip_targs(row.cls.split('::')[-1]) if row.cls else ''
    excl = {row.name, row.name.lstrip('~')} | ({cls_short} if cls_short else set())
    toks = body_tokens(info.text, excl)
    info.upper = names.upper_only(toks, row.decl_crate) if row.body is not None else []
    info.opq = opaque_params(ast, row) if row.cls is None or row.kind in ('function',) else []
    varmap = dict(field_types(ast, row.cls_def)) if row.cls_def is not None else {}
    for p in (row.body or {}).get('params', []) or row.params_recs or []:
        t = record_short(ast, p.get('canonical_type', ''))
        if t and p.get('name'):
            varmap[p['name']] = t
    for t, v in LOCAL_DECL_RE.findall(info.text):
        if v not in varmap and t not in CPP_WORDS:
            varmap[v] = strip_targs(t.split('::')[-1])
    info.calls = body_calls(info.text, cls_short, varmap) if row.body is not None else set()
    info.pimpl = uses_pimpl(ast, row, info.text) if row.cls_def is not None else []
    info.service = bool(SERVICE_RE.search(info.text))
    info.movable = row.body is not None and not info.upper and not info.opq
    info.why = ''
    return info


def movability_fixpoint(rows, infos):
    """A body that calls a member (same class unqualified / this->, or X::f) whose own body is an
    unmovable seam row is not movable either.  Iterates to a fixed point."""
    idx = collections.defaultdict(list)
    for row in rows:
        if row.proposed == 'merge':
            continue
        if row.cls and row.kind in ('method', 'ctor', 'var'):
            idx[(strip_targs(row.cls.split('::')[-1]), row.name)].append(row)
        elif not row.cls and row.kind == 'function':
            idx[('', row.name)].append(row)
            ns = row.symbol.split('(')[0].split('::')
            if len(ns) > 1:
                idx[(ns[-2], row.name)].append(row)
    changed = True
    while changed:
        changed = False
        for row in rows:
            info = infos.get(id(row))
            if info is None or not info.movable:
                continue
            for c, n in sorted(info.calls):
                callees = [x for x in idx.get((c, n), ()) if x is not row]
                bad = [x for x in callees if not infos[id(x)].movable]
                if bad:
                    info.movable = False
                    info.why = 'calls-unmovable:%s::%s' % (c, n)
                    changed = True
                    break
    # a vtable / typeinfo moves only with every virtual body of its class
    by_cls = collections.defaultdict(list)
    for row in rows:
        if row.cls and row.kind in ('method', 'impl-body', 'dtor', 'ctor'):
            by_cls[row.cls].append(row)
    for row in rows:
        if row.kind in ('vtable', 'typeinfo') and id(row) in infos:
            members = [x for x in by_cls.get(row.cls, ()) if x.kind in ('method',) and
                       any(r.get('is_virtual') for r in x.recs)]
            stuck = [x for x in members if not infos[id(x)].movable]
            infos[id(row)].movable = bool(members) and not stuck
            infos[id(row)].why = ('virtual-bodies-unmovable:%d/%d' % (len(stuck), len(members)) if members
                                  else 'no-virtual-body-rows')


def decide(ast, row, info):
    ev = row.ev
    unit_target = target_of(row.cls_def['file']) if row.cls_def is not None else target_of(row.decl[0])
    type_target = target_of(row.cls_def['file']) if row.cls_def is not None else ''

    def landing_mod(note=''):
        mp = module_path(unit_target)
        return (mp + ('::' + note if note and re.match(r'^[A-Z]\w*$', note) else '')) or '?'

    # merge: a link symbol with no caller left outside the definition crate's reach once pl is sql
    if 'link' in row.pops and not row.objs and 'proto' not in row.pops:
        if not any('pl' in (a, b) for a, b, _k, _r in row.edges) or row.decl_crate != row.def_crate:
            sys.exit('make_seams: %s has no violating caller after the crate mapping but is not a pl edge'
                     % row.symbol)
        row.shape = 'method-on-lower-type' if row.cls else 'free-fn'
        row.proposed = 'merge'
        row.landing = target_of(row.defn[0]) or target_of(row.decl[0]) or '?'
        ev['rule'] = 'merge:pl->sql'
        return
    if row.body is not None:
        up = info.upper
        ev['body-upper'] = (','.join(up[:6]) + ('+%d' % (len(up) - 6) if len(up) > 6 else '')) or 'none'
    if info.why:
        ev['movable'] = 'no(%s)' % info.why if not info.movable else 'yes'
    if info.pimpl:
        ev['pimpl-fields'] = ','.join(info.pimpl[:4])
    # Drop and foreign traits: always in the type's crate (RULEBOOK 0.4 impl placement)
    if row.kind == 'dtor':
        if info.pimpl:
            row.shape, row.proposed = 'dtor-pimpl', 'pimpl-any'
            ev['rule'] = 'drop:pimpl'
        else:
            row.shape, row.proposed = 'foreign-trait-body', 'impl-in-type-crate'
            ev['rule'] = 'drop'
        row.landing = type_target or '?'
        return
    if row.kind == 'impl-body':
        row.shape, row.proposed = 'foreign-trait-body', 'impl-in-type-crate'
        row.landing = type_target or '?'
        ev['rule'] = 'foreign-trait:' + FOREIGN_TRAIT.get(row.name, '?')
        return
    movable = info.movable
    # shape (descriptive)
    if per_row(row):
        shape = 'per-row-path'
    elif row.kind in ('vtable', 'typeinfo'):
        shape = 'value-type-body' if movable else 'handle-object'
    elif movable and row.cls:
        shape = 'value-type-body'
    elif row.cls and info.pimpl:
        shape = 'handle-object'
    elif row.cls is None and info.opq:
        shape = 'service-singleton' if info.service else 'handle-object'
    elif info.service and (row.cls is None or row.is_static):
        shape = 'service-singleton'
    elif row.cls:
        shape = 'method-on-lower-type'
    else:
        shape = 'free-fn'
    row.shape = shape
    if info.opq:
        ev['opaque'] = ','.join(q.split('::')[-1] for q in info.opq[:4])
    # proposed split
    ov = edge_override(row)
    fam = family(row)
    if ov is not None:
        row.proposed, note, ev['rule'] = ov
        if row.proposed == 'move-down':
            row.landing = unit_target or '?'
        else:
            row.landing = landing_mod(note.split(';')[0].split(' ')[0] if note else '')
        if note:
            ev['note'] = note
        return
    if shape == 'per-row-path':
        row.proposed, ev['rule'] = 'handle-trait', 'edge:per-row'
        row.landing = landing_mod()
        return
    if movable:
        row.proposed, ev['rule'] = 'move-down', 'body-lower-only'
        row.landing = unit_target or '?'
        return
    if fam == 'vector-index':
        tn = ''
        for c, t in VECTOR_TRAITS:
            if (row.cls or '').endswith('::' + c):
                tn = t
        row.proposed, ev['rule'] = 'trait-slot', 'edge:->observer:vector-index'
        row.landing = landing_mod(tn)
        return
    if fam == 'pushdown':
        row.proposed, ev['rule'] = 'handle-trait', 'edge:sql<->storage:pushdown(0.6)'
        row.landing = landing_mod('PushdownFilter')
        return
    if shape == 'handle-object':
        row.proposed, ev['rule'] = 'handle-trait', 'shape:handle-object'
    else:
        row.proposed, ev['rule'] = 'trait-slot', 'shape:' + shape
    row.landing = landing_mod()


def layout_lower(ast, row):
    """The upper type's own fields and bases only name types the lower crate can reach."""
    low = row.decl_crate
    d = row.cls_def
    bad = set()
    for f in ast.db.fields(d['usr'], inherited=False):
        for q in QNAME_RE.findall(f.get('canonical_type', '')):
            dcs = ast.def_crates_q.get(q)
            if dcs and low not in dcs and not any(reaches(low, c) for c in dcs):
                bad.add(q)
    for b in d.get('bases', []):
        dcs = ast.def_crates_q.get(strip_targs(b.get('qname', '')))
        if dcs and low not in dcs and not any(reaches(low, c) for c in dcs):
            bad.add(b.get('qname'))
    return sorted(bad)


OPERATED = collections.defaultdict(set)     # crate -> opaque record qnames used by handle seams there


def classify_fwd(ast, row):
    cnt = row.fwd['counts']
    ev = row.ev
    if cnt['member']:
        shape = 'fwd-type-member'
    elif cnt['spec']:
        shape = 'template-specialization'
    else:
        shape = 'fwd-type-signature'
    row.shape = shape
    lower_target = target_of(row.decl[0])
    if shape == 'template-specialization':
        row.proposed, ev['rule'] = 'impl-in-type-crate', 'fwd:specialization'
        row.landing = target_of(row.defn[0]) or '?'
        return
    bad = layout_lower(ast, row)
    ev['layout-upper'] = (','.join(b.split('::')[-1] for b in bad[:4]) + ('+%d' % (len(bad) - 4) if len(bad) > 4 else '')) or 'none'
    if not bad:
        row.proposed, ev['rule'] = 'move-type-down', 'fwd:layout-lower'
        row.landing = lower_target or '?'
    elif any(row.symbol in qs for c, qs in OPERATED.items() if reaches(row.decl_crate, c)):
        row.proposed, ev['rule'] = 'handle-trait', 'fwd:operated-on-by-handle-seam'
        row.landing = module_path(lower_target) or '?'
    elif shape == 'fwd-type-member':
        row.proposed, ev['rule'] = 'opaque-handle', 'fwd:member'
        mem = [u for u in row.fwd['uses'] if u[2] == 'member']
        row.landing = module_path(target_of(mem[0][0])) if mem else (module_path(lower_target) or '?')
    else:
        row.proposed, ev['rule'] = 'handle-trait', 'fwd:signature'
        row.landing = module_path(lower_target) or '?'
    if row.cls_def is not None:
        n = sum(1 for x in ast.by_parent.get(row.cls_def['usr'], [])
                if x['kind'] in astdb.METHOD_KINDS and x.get('is_definition') and x.get('out_of_line'))
        ev['upper-methods'] = str(n)


# ------------------------------------------------------------------ type users (move-up hint)
def lower_users(rows, files):
    """For member rows: files in crates that cannot reach the definition crate, other than the class's
    own header, that name the class.  0 = no code below the body crate needs the type."""
    want = collections.defaultdict(set)
    for row in rows:
        if row.cls and row.kind not in ('fwd-type',) and row.proposed != 'merge':
            want[strip_targs(row.cls.split('::')[-1])].add(row)
    if not want:
        return
    hits = collections.defaultdict(set)
    wanted = set(want)
    for f in files:
        for name in file_tokens(f) & wanted:
            hits[name].add(f)
    for name, rs in want.items():
        for row in rs:
            own = row.cls_def['file'] if row.cls_def is not None else ''
            n = sum(1 for f in hits.get(name, ()) if f != own and not reaches(crate_of(f), row.def_crate)
                    and f != row.defn[0])
            row.ev['type-lower-files'] = str(n)


# ------------------------------------------------------------------ output
def ev_string(row):
    ev = row.ev
    pop = '+'.join(p for p in ('link', 'body', 'fwd', 'proto') if p in row.pops)
    return '; '.join(['pop=' + pop] + ['%s=%s' % (k, v) for k, v in ev.items()
                                       if k != 'pop' and v not in (None, '')])


def callers_string(row):
    n, ex, what = row.callers
    if not n:
        return '0'
    unit = ' ' + what if what else ''
    return '%d%s: %s' % (n, unit, ', '.join(ex))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--jobs', type=int, default=8)
    args = ap.parse_args()
    if sys.platform != 'darwin':
        sys.exit('make_seams: the nm reader is the Mach-O `nm -m` format (DEV-007); port read_nm() for GNU nm')
    load_units()
    verify_dag()
    objs, skipped = list_objects()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        nm = dict(zip(objs, pool.map(read_nm, objs)))
    unparsed = sum(nm[o].unparsed for o in objs)
    if unparsed:
        sys.exit('make_seams: %d nm lines not understood' % unparsed)
    relations = read_relations()
    report = {}
    print('make_seams: %d objects (%d skipped by the depmap); loading the AST database' % (len(objs), len(skipped)))
    ast = Ast()
    files = sweeplib.first_party()
    names = Names(ast, files)
    src_cache = {}
    link_rows, undef_objs, strong_objs, weak_objs, mod_of = build_link_rows(ast, objs, nm, relations, report,
                                                                             src_cache)
    body_rows = build_body_rows(ast, link_rows)
    fwd_rows, fwd_findings = build_fwd_rows(ast)
    proto_rows, proto_excluded = build_proto_rows(ast, link_rows)
    rows = sorted(list(link_rows.values()) + list(body_rows.values()) + list(proto_rows.values()),
                  key=lambda r: (r.symbol, r.decl, r.defn))
    for row in link_rows.values():
        fill_callers(row, src_cache)
    infos = {}
    for row in rows:
        merged = 'link' in row.pops and not row.objs and 'proto' not in row.pops
        if merged:
            row.proposed = 'merge'
        else:
            infos[id(row)] = analyze(ast, names, row)
    movability_fixpoint(rows, infos)
    for row in rows:
        decide(ast, row, infos.get(id(row)))
        if row.shape in ('handle-object', 'per-row-path') and row.proposed == 'handle-trait':
            for q in infos[id(row)].opq:
                OPERATED[row.decl_crate].add(q)
    for row in sorted(fwd_rows.values(), key=lambda r: r.key):
        classify_fwd(ast, row)
    rows += list(fwd_rows.values())
    lower_users(rows, files)
    out = []
    for row in rows:
        out.append([row.symbol, row.kind, row.decl[0], str(row.decl[1]), row.defn[0], str(row.defn[1]),
                    row.decl_crate, row.def_crate, callers_string(row), row.shape, row.proposed,
                    row.landing, ev_string(row)])
    # seam_id order: (decl_crate, def_crate, symbol) as plain string order, then the remaining columns
    out.sort(key=lambda r: (r[6], r[7], r[0], r[1], r[2], int(r[3]), r[4], int(r[5])))
    keys = set()
    for i, r in enumerate(out):
        k = (r[0], r[1], r[2], r[3])
        if k in keys:
            sys.exit('make_seams: duplicate row %r' % (k,))
        keys.add(k)
        r.insert(0, 'S-%04d' % (i + 1))
    if len(out) > 9999:
        sys.exit('make_seams: more than 9999 rows; widen seam_id')
    sweeplib.write_tsv(SEAMS_TSV, HEADER, out)
    summ = collections.Counter((r[7], r[8], r[2], r[11]) for r in out)
    srows = [[a, b, c, d, str(n)] for (a, b, c, d), n in sorted(summ.items())]
    sweeplib.write_tsv(SUMMARY_TSV, ('decl_crate', 'def_crate', 'kind', 'proposed_split', 'rows'), srows)
    pops = collections.Counter(ev_pop(r[13]) for r in out)
    note = ('RULEBOOK 0.4 seam table migration/design/seams.tsv: link (depmap 12 against + 3 across edges, '
            'nm re-read) + body (out-of-line members in another crate than their class) + fwd (forward-declared '
            'upward types, lower-crate template specializations) + proto (c13 upward prototypes); populations '
            + ' '.join('%s=%d' % kv for kv in sorted(pops.items())) + '; macOS link data only (DEV-007)')
    sweeplib.update_counts({'seams': (len(out), note)})
    print_report(report, out, link_rows, fwd_findings, proto_excluded)
    print('make_seams: %d rows -> %s; summary -> %s' % (len(out), os.path.relpath(SEAMS_TSV, REPO),
                                                        os.path.relpath(SUMMARY_TSV, REPO)))
    return 0


def ev_pop(ev):
    m = re.search(r'(?:^|; )pop=([^;]*)', ev)
    return m.group(1) if m else '?'


def print_report(report, out, link_rows, fwd_findings, proto_excluded):
    e, eo = report['edges'], report['edges_old']
    tot = sum(e.values())
    old = sum(eo.values())
    print('reconciliation with the depmap (01-depmap-report 3.3, RULEBOOK 0.4):')
    for rel in ('against', 'across'):
        s = sum(v for k, v in e.items() if k[0] == rel)
        so = sum(v for k, v in eo.items() if k[0] == rel)
        print('  %-7s edge-symbol pairs: symdeps reader %d, this reader %d' % (rel, so, s))
        for k in sorted(k for k in e if k[0] == rel):
            print('    %s -> %s %-13s %4d%s' % (k[1], k[2], k[3], e[k], '' if e[k] == eo[k] else
                                                  ' (symdeps reader: %d)' % eo[k]))
    print('  total pairs: symdeps reader %d, this reader %d; distinct symbols %d' % (old, tot, len(link_rows)))
    multi = collections.Counter(len(r.edges) for r in link_rows.values())
    print('  pairs per distinct symbol: ' + ', '.join('%d pair(s): %d symbols' % kv for kv in sorted(multi.items())))
    refweak = sum(1 for r in link_rows.values() if {k for _a, _b, k, _r in r.edges} == {'ref', 'weak_override'})
    multimod = sum(1 for r in link_rows.values() if len({a for a, _b, _k, _r in r.edges}) > 1)
    print('    of them: same symbol as ref and weak_override on oblib->share %d; referenced from >1 lower module %d'
          % (refweak, multimod))
    crate_pairs = collections.Counter()
    for r in link_rows.values():
        if r.proposed == 'merge':
            crate_pairs[('(merge) sql', 'sql')] += 1
            continue
        for c in sorted(r.ref_crates):
            crate_pairs[(c, r.def_crate)] += 1
    print('  after the crate mapping (violating caller crate -> definition crate, distinct symbols):')
    for (a, b), n in sorted(crate_pairs.items(), key=lambda kv: (kv[0][0].startswith('('), kv[0])):
        print('    %s -> %s %d' % (a, b, n))
    by_pop = collections.Counter(ev_pop(r[13]) for r in out)
    print('  rows by population: ' + ', '.join('%s=%d' % kv for kv in sorted(by_pop.items())))
    merged = sum(1 for r in out if r[11] == 'merge')
    print('  link rows resolved by the pl->sql merge: %d' % merged)
    for low, items in sorted(fwd_findings.items()):
        print('  %s names %d upper types with no forward declaration it can see (complete types through '
              'includes; not rows):' % (low, len(items)))
        for q, us in sorted(items, key=lambda x: (-len(x[1]), x[0]))[:8]:
            print('    %s (%d uses, e.g. %s:%d)' % (q, len(us), us[0][0], us[0][1]))
    for f, sym, why in proto_excluded:
        print('  c13 upward prototype %s %s -> %s' % (f, sym, why))


if __name__ == '__main__':
    sys.exit(main())
