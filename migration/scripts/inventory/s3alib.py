#!/usr/bin/env python3
"""s3alib.py -- shared model for sweep set S3a (c02 c03 c04 c05 c19 c20 c22 and types.tsv).

Stdlib only, deterministic (every set is sorted before it reaches an output).  Inputs:
  build_release/inventory-ast/decls.jsonl   libclang declaration database (astdb.py)
  build_release/inventory-ast/files.tsv     files no TU parses (tus = 0) get regex fallbacks
  migration/design/units.tsv                unit, crate and disposition of every file
  first-party sources (stripped with sweeplib) for body-level text rules

Main pieces (see README.md, section "Sweep set S3a", for the rules):
  Model.chain(usr)          class chain without namespaces ('ObFoo::Inner'), anonymous members flattened
  Model.strip_ns(text)      drops namespace qualifiers from a type or name (std::__1:: -> std::)
  Model.type_refs(type)     every named type in a C++ type string with template depth and indirection
  Model.field_edges(field)  what a data member holds: str / ref / ptr / value / fn edges
  Model.modules             module types: GCTX pointer members, server_service<T>(), get_instance()
  Model.ir / plan / exprop  compile-time IR, runtime plan, ObExprOperator hierarchies
  Model.allocators / locks  allocator types and lock primitives derived from the database
  Model.body(usr)           stripped source text of a function definition (AST extent)
"""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import astdb  # noqa: E402
import sweeplib as L  # noqa: E402

REPO = L.REPO
AST_DIR = os.path.join(REPO, 'build_release', 'inventory-ast')
FILES_TSV = os.path.join(AST_DIR, 'files.tsv')
UNITS_TSV = os.path.join(REPO, 'migration', 'design', 'units.tsv')
RK = astdb.RECORD_KINDS
FK = astdb.FUNC_KINDS

# ---------------------------------------------------------------- fixed name lists (documented in README)
IR_ROOTS = ('oceanbase::sql::ObRawExpr', 'oceanbase::sql::ObStmt', 'oceanbase::sql::TableItem',
            'oceanbase::sql::ObLogicalOperator', 'oceanbase::sql::Path')
PLAN_ROOTS = ('oceanbase::sql::ObPhysicalPlan', 'oceanbase::sql::ObOpSpec', 'oceanbase::sql::ObExpr')
EXPROP_ROOT = 'oceanbase::sql::ObExprOperator'
SESSION_ROOTS = ('oceanbase::sql::ObSQLSessionInfo', 'oceanbase::sql::ObBasicSessionInfo')
OBSTRING = 'oceanbase::common::ObString'
IALLOC = 'oceanbase::common::ObIAllocator'
GCTX = 'oceanbase::share::ObGlobalContext'
# tagged hierarchies with downcasts (RULEBOOK §2.13, 0.6 closed sets): root qname -> hierarchy label
ISA_ROOTS = (('oceanbase::sql::ObRawExpr', 'RawExpr'), ('oceanbase::sql::ObStmt', 'Stmt'),
             ('oceanbase::sql::ObLogicalOperator', 'LogOp'), ('oceanbase::sql::Path', 'Path'),
             ('oceanbase::sql::ObOpSpec', 'OpSpec'), ('oceanbase::sql::ObIExprExtraInfo', 'ExtraInfo'),
             ('oceanbase::sql::ObExprOperatorCtx', 'ExprOpCtx'))
# lock primitives named in RULEBOOK §2.7 and amendments #9 (plus everything non-guard in src/oblib/lib/lock/)
LOCK_NAMES = ('ObSpinLock', 'ObMutex', 'ObLatchMutex', 'SpinRWLock', 'ObLatch', 'DRWLock', 'TCRWLock', 'ObQSyncLock',
              'ObBucketLock', 'ObBucketQSyncLock', 'ObRecursiveMutex', 'ObThreadCond', 'ObMonitor', 'ObUtilMutex',
              'SimpleCond', 'ObLightyCond', 'ObFutex', 'LWaitCond', 'ObRowLatch', 'ObSmallSpinLock', 'ObByteLock',
              'ObPtrSpinLock', 'ObRWLock', 'ObCond', 'SCondTemp', 'SCond', 'RingSpinLock', 'CtxLock', 'DIRWLock',
              'Cond', 'Mutex', 'ObQSync', 'ObLatchWaitQueue', 'MemtableMgrLock', 'ObLSLock', 'ObDListWithLock',
              'CombinedBLock', 'ObSimpleLock')
LOCK_CANON_RX = re.compile(r'\b(_opaque_pthread_(?:mutex|rwlock|cond)_t|pthread_(?:mutex|rwlock|cond|spinlock)_t|'
                           r'std::__1::(?:mutex|recursive_mutex|shared_mutex|condition_variable))\b')
S_LOCK_CANON = LOCK_CANON_RX
ATOMIC_CANON_RX = re.compile(r'^(?:const |volatile )*std::__1::atomic\b|^(?:const )?volatile\b')
# containing-class scope hints (c04 ObString pretags, c05 scope tags)
PLAN_EXTRA_ROOTS = ('oceanbase::sql::ObIExprExtraInfo', 'oceanbase::sql::ObDASBaseCtDef')
SCHEMA_DIRS = ('src/share/schema/',)
LONG_LIVED_DIRS = ('src/share/schema/', 'src/sql/session/', 'src/sql/plan_cache/', 'src/storage/meta_mem/',
                   'src/storage/tablet/', 'src/storage/ls/', 'src/storage/tx/', 'src/share/cache/',
                   'src/storage/tx_storage/')
QUERY_DIRS = ('src/sql/resolver/', 'src/sql/rewrite/', 'src/sql/optimizer/', 'src/sql/code_generator/',
              'src/sql/printer/', 'src/sql/parser/', 'src/sql/engine/', 'src/sql/das/', 'src/sql/executor/',
              'src/sql/dtl/', 'src/sql/hybrid_search/', 'src/pl/', 'src/sql/pl/', 'src/query/', 'src/objit/')
RX_CROSS_CALLS = re.compile(r'(Cursor|ResultSet|Result$|Iter$|Iterator|Scanner|Op$|OpInput$|RowStore|DatumStore|'
                            r'Store$|Spi\w*|Fetcher|Reader$)')
# RULEBOOK 0.3 crate graph (direct dependencies between port crates)
CRATE_DEPS = {
    'oblib': (), 'storage_core': ('oblib',), 'share': ('oblib',), 'query': ('oblib', 'share'),
    'logservice': ('oblib', 'share', 'storage_core'), 'data_plane': ('oblib', 'share', 'query'),
    'parser': ('oblib', 'query'), 'sql': ('oblib', 'share', 'data_plane', 'query', 'parser'),
    'storage': ('oblib', 'share', 'data_plane', 'logservice', 'query', 'storage_core'),
    'rootserver': ('oblib', 'storage_core', 'share', 'query', 'logservice', 'data_plane', 'parser', 'sql', 'storage'),
    'standby': ('oblib', 'share', 'logservice', 'storage'),
    'observer': ('oblib', 'storage_core', 'share', 'query', 'logservice', 'data_plane', 'parser', 'sql', 'storage',
                 'rootserver', 'standby'),
}


def crate_sees(a, b):
    """True when crate a may name items of crate b (a == b or b is a transitive dependency of a)."""
    if not a or not b or a == b:
        return True
    seen, todo = set(), [a]
    while todo:
        c = todo.pop()
        for d in CRATE_DEPS.get(c, ()):
            if d == b:
                return True
            if d not in seen:
                seen.add(d)
                todo.append(d)
    return False


RX_IDENT = re.compile(r'[A-Za-z_]\w*')
KEYWORDS = frozenset('const volatile unsigned signed struct class union enum typename long short int char bool void '
                     'float double wchar_t char16_t char32_t char8_t __int128 _Bool __restrict restrict auto '
                     'decltype sizeof noexcept true false nullptr'.split())
BUILTIN_WORDS = frozenset('unsigned signed long short int char bool void float double wchar_t char16_t char32_t '
                          'char8_t __int128 _Bool'.split())

_TOK = re.compile(r'\s*(::|->|\.\.\.|<<|>>|[<>,*&()\[\]{}]|[A-Za-z_][\w]*(?:-[\w]+)*|\d+\w*|\S)')


def tokenize(s):
    return [m.group(1) for m in _TOK.finditer(s)]


def load_units():
    """file -> units.tsv row (dict) for every member of every unit."""
    out = {}
    with open(UNITS_TSV, encoding='utf-8') as fh:
        hdr = fh.readline().rstrip('\n').split('\t')
        for line in fh:
            if not line.strip():
                continue
            row = dict(zip(hdr, line.rstrip('\n').split('\t')))
            for m in row.get('members', '').split(','):
                if m:
                    out[m] = row
    return out


def load_files_tus():
    """file -> (class, tus) from files.tsv."""
    out = {}
    with open(FILES_TSV, encoding='utf-8') as fh:
        next(fh)
        for line in fh:
            parts = line.rstrip('\n').split('\t')
            if len(parts) >= 3:
                out[parts[0]] = (parts[1], int(parts[2]))
    return out


def unparsed_files(exts=L.SOURCE_EXT):
    """First-party source files that no TU of the build parses (files.tsv tus = 0), sorted."""
    tus = load_files_tus()
    fp = set(L.first_party(exts))
    return sorted(f for f, (cls, n) in tus.items() if n == 0 and f in fp)


def snake(name):
    """C++ class name -> snake case (ObINodeWithChild -> ob_i_node_with_child, ObSQLSessionInfo -> ob_sql_session_info)."""
    name = re.sub(r'<.*', '', name).split('::')[-1]
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    return re.sub(r'_+', '_', s).lower().strip('_')


def split_top(s, sep=','):
    """Split s at top-level separators (outside <>, (), [], {})."""
    out, depth, cur = [], 0, []
    for c in s:
        if c in '<([{':
            depth += 1
        elif c in '>)]}':
            depth = max(0, depth - 1)
        if c == sep and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    out.append(''.join(cur))
    return out


def last_component(qname):
    """Last '::' component of a qualified name, template arguments kept."""
    parts = split_qname(qname)
    return parts[-1] if parts else qname


def split_qname(qname):
    parts, depth, cur, i = [], 0, [], 0
    while i < len(qname):
        c = qname[i]
        if c in '<(':
            depth += 1
        elif c in '>)':
            depth -= 1
        if depth == 0 and qname.startswith('::', i):
            parts.append(''.join(cur))
            cur = []
            i += 2
            continue
        cur.append(c)
        i += 1
    parts.append(''.join(cur))
    return [p for p in parts if p != '']


def matching(text, i, open_c='(', close_c=')'):
    """Index of the bracket closing text[i] == open_c (or len(text))."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


class Model(object):
    """Indexes over decls.jsonl plus the derived type sets used by the S3a sweeps."""

    def __init__(self, funcs=False, vars_=False):
        kinds = RK + ('field',) + (FK if funcs else ()) + (('var',) if vars_ else ())
        self.has_funcs = funcs
        self.db = astdb.AstDB(kinds=kinds)
        self.units = load_units()
        self.tus = load_files_tus()
        recs = sorted((r for r in self.db.records if r['kind'] in RK),
                      key=lambda r: (r['file'], r['line'], r.get('col', 0), r['usr']))
        self.rec_any, self.defs = {}, {}
        for r in recs:
            self.rec_any.setdefault(r['usr'], r)
            if r.get('is_definition') and not r.get('explicit_instantiation'):
                self.defs.setdefault(r['usr'], r)
        # every definition record (several per USR when n_defs > 1), first-party or generated
        self.all_defs = [r for r in recs if r.get('is_definition') and not r.get('explicit_instantiation')]
        self.by_qname = collections.defaultdict(list)
        for u in sorted(self.defs):
            self.by_qname[self.defs[u]['qname']].append(u)
        self.by_name = collections.defaultdict(list)
        for u in sorted(self.defs):
            self.by_name[self.defs[u]['name']].append(u)
        self._ns()
        self._ancestors, self._derived, self._fields, self._medges = {}, {}, {}, {}
        self._sets()

    # ------------------------------------------------------------ names
    def _ns(self):
        ns = set()
        for r in self.db.records:
            p = r.get('parent_usr')
            q = r.get('parent_qname')
            if p and q and p not in self.rec_any and r['kind'] in RK + ('field', 'var', 'typedef') + FK:
                if '<' in q or (r['kind'] in FK and p.startswith('c:@') and '@F@' in p):
                    continue
                comps = split_qname(q)
                for i in range(len(comps)):
                    for j in range(i + 1, len(comps) + 1):
                        ns.add('::'.join(comps[i:j]))
        # function USRs as parents (lambdas, local classes) are not namespaces
        self.ns = ns

    _QN = re.compile(r'(?:\(anonymous namespace\)::|\b[A-Za-z_]\w*::)+')

    def strip_ns(self, s):
        """Drop namespace qualifiers (keeps class qualifiers and std::)."""
        if not s:
            return s
        s = s.replace('std::__1::', 'std::')

        def rep(m):
            comps = m.group(0).split('::')[:-1]
            k = 0
            while k < len(comps) and comps[k] != 'std' and (comps[k] in self.ns or '::'.join(comps[:k + 1]) in self.ns):
                k += 1
            return ''.join(c + '::' for c in comps[k:])
        out = self._QN.sub(rep, s)
        return ' '.join(out.split())

    def rec_label(self, r):
        if r.get('is_anonymous') or not r.get('name'):
            return 'anon@%d' % r['line']
        last = last_component(r['qname'])
        if '<' in last:
            return self.strip_ns(last)
        return r['name']

    def chain(self, usr):
        """'Outer::Inner' for a record USR (namespaces dropped, anonymous members flattened)."""
        parts = []
        r = self.rec_any.get(usr)
        seen = set()
        while r is not None and r['kind'] in RK and r['usr'] not in seen:
            seen.add(r['usr'])
            if not (r.get('is_anonymous') and r.get('is_anonymous_member')):
                parts.append(self.rec_label(r))
            r = self.rec_any.get(r.get('parent_usr'))
        return '::'.join(reversed(parts)) if parts else '<unknown>'

    def owner_of_field(self, f):
        """USR of the record that a field belongs to after flattening anonymous members."""
        u = f.get('parent_usr')
        r = self.rec_any.get(u)
        while r is not None and r.get('is_anonymous') and r.get('is_anonymous_member'):
            u = r.get('parent_usr')
            r = self.rec_any.get(u)
        return u

    def short(self, usr):
        r = self.rec_any.get(usr)
        return self.chain(usr) if r else usr

    # ------------------------------------------------------------ hierarchy
    def ancestors(self, usr):
        if usr not in self._ancestors:
            self._ancestors[usr] = self.db.ancestors(usr)
        return self._ancestors[usr]

    def derived(self, usr):
        if usr not in self._derived:
            self._derived[usr] = sorted(self.db.derived(usr))
        return self._derived[usr]

    def resolve_base(self, b):
        return self.db._resolve_base(b)

    def fields(self, usr, inherited=False):
        key = (usr, inherited)
        if key not in self._fields:
            self._fields[key] = self.db.fields(usr, inherited=inherited)
        return self._fields[key]

    def has_data(self, usr):
        """True when the record or any base has a non-static data member."""
        return bool(self.fields(usr, inherited=True))

    def resolve(self, qname):
        """Definition USR for a fully qualified record name (template args ignored), else None."""
        if not qname:
            return None
        q = qname.strip()
        q = re.sub(r'^(?:const |volatile |struct |class |union |enum )+', '', q).strip()
        if q.startswith('::'):
            q = q[2:]
        us = self.by_qname.get(q)
        if us:
            return us[0]
        if '<' in q:
            base = q[:q.index('<')]
            us = self.by_qname.get(base)
            if us:
                return us[0]
        return None

    def resolve_written(self, name):
        """Record USRs for a written (possibly partially qualified) name, e.g. 'storage::ObLSService'."""
        name = re.sub(r'\s+', '', name).lstrip(':')
        name = re.sub(r'<.*', '', name)
        if not name:
            return []
        u = self.resolve(name)
        if u:
            return [u]
        leaf = name.split('::')[-1]
        out = []
        for u in self.by_name.get(leaf, []):
            q = self.defs[u]['qname']
            if q == name or q.endswith('::' + name):
                out.append(u)
        return sorted(set(out))

    # ------------------------------------------------------------ types
    def type_refs(self, t):
        """[(name, depth, indirect, template_name_or_None)] for every named type in a C++ type string.
        depth: template-argument nesting; indirect: followed by * or & at its own level."""
        toks = tokenize(t or '')
        out = []
        stack = [{'last': None, 'tmpl': None}]
        i, n = 0, len(toks)
        while i < n:
            tok = toks[i]
            if RX_IDENT.match(tok) or tok == '::':
                j = i
                parts = []
                if toks[j] == '::':
                    j += 1
                while j < n and RX_IDENT.match(toks[j]):
                    parts.append(toks[j])
                    if j + 1 < n and toks[j + 1] == '::' and j + 2 < n and RX_IDENT.match(toks[j + 2]):
                        j += 2
                        continue
                    j += 1
                    break
                i = j
                if not parts:
                    continue
                if len(parts) == 1 and parts[0] in KEYWORDS:
                    if parts[0] in BUILTIN_WORDS:
                        rec = {'name': parts[0], 'depth': len(stack) - 1, 'indirect': False,
                               'tmpl': stack[-1]['tmpl'], 'builtin': True}
                        # 'unsigned long long' etc.: merge consecutive builtin words
                        last = stack[-1]['last']
                        if last is not None and last.get('builtin') and last['depth'] == rec['depth'] and \
                                out and out[-1] is last and not last.get('closed'):
                            last['name'] += ' ' + parts[0]
                        else:
                            out.append(rec)
                            stack[-1]['last'] = rec
                    continue
                name = '::'.join(parts)
                rec = {'name': name, 'depth': len(stack) - 1, 'indirect': False, 'tmpl': stack[-1]['tmpl'],
                       'builtin': False, 'param': name.startswith('type-parameter-')}
                out.append(rec)
                stack[-1]['last'] = rec
                continue
            if tok == '<':
                stack.append({'last': None, 'tmpl': stack[-1]['last']})
            elif tok in ('>', '>>'):
                for _ in range(len(tok)):
                    if len(stack) > 1:
                        top = stack.pop()
                        if top['tmpl'] is not None:
                            stack[-1]['last'] = top['tmpl']
                            top['tmpl']['closed'] = True
            elif tok in ('*', '&', '&&'):
                if stack[-1]['last'] is not None:
                    stack[-1]['last']['indirect'] = True
                    stack[-1]['last']['closed'] = True
            elif tok == ',':
                stack[-1]['last'] = None
            elif tok == '(':
                # function type or pointer to function: everything inside is a signature, mark it
                k = i
                depth = 0
                while k < n:
                    if toks[k] == '(':
                        depth += 1
                    elif toks[k] == ')':
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if stack[-1]['last'] is not None:
                    stack[-1]['last']['fn'] = True
            i += 1
        for r in out:
            r.pop('closed', None)
        return out

    def is_transparent(self, usr, qname=''):
        """Containers and other replaced infrastructure: only their template arguments count."""
        if qname.startswith('std::'):
            return True
        if usr is None:
            return True
        r = self.defs.get(usr)
        if r is None:
            return True
        row = self.units.get(r['file'])
        if row is not None and row['disposition'].startswith('replace:'):
            return True
        return False

    def field_edges(self, f):
        """What a data member holds.  List of (kind, target, detail):
           kind 'str'   ObString (by value, array element, or container element)
                'ref'   reference member (target = pointee USR or builtin name)
                'ptr'   object pointer, array of pointers, or pointer in a container argument
                'value' record held by value (directly, as array element, or as container element)
                'infra' replaced infrastructure held by value (container, allocator, lock, ...): only its
                        template arguments count, they appear as further edges
                'fn'    function pointer
        target: record USR, or 'builtin:<name>', or 'param:<T>' for template parameters, or None."""
        ck = f.get('canonical_kind')
        ct = f.get('canonical_type', '') or ''
        out = []

        def ptr_target(pq, pk):
            if pk == 'record' or pk == 'template_spec':
                u = self.resolve(pq)
                return u if u else 'unresolved:%s' % pq
            if pk == 'builtin':
                return 'builtin:%s' % (pq or '')
            if pk == 'template_param':
                return 'param:%s' % (pq or '')
            if pk == 'pointer':
                return 'builtin:pointer'
            if pk == 'function_pointer':
                return 'fn'
            return 'other:%s' % (pk or '')
        if ck == 'pointer':
            out.append(('ptr', ptr_target(f.get('pointee_qname'), f.get('pointee_kind')), 'direct'))
            return out
        if ck in ('lvalue_ref', 'rvalue_ref'):
            out.append(('ref', ptr_target(f.get('pointee_qname'), f.get('pointee_kind')), 'direct'))
            return out
        if ck in ('function_pointer', 'member_pointer'):
            out.append(('fn', None, ck))
            return out
        if ck in ('constant_array', 'array', 'incomplete_array'):
            ek = f.get('element_kind')
            et = f.get('element_type', '') or ''
            if ek == 'pointer':
                refs = self.type_refs(et)
                tgt = None
                for r in refs:
                    if r['depth'] == 0 and r['indirect']:
                        tgt = r
                        break
                if tgt is None:
                    out.append(('ptr', 'builtin:unknown', 'array'))
                elif tgt['builtin']:
                    out.append(('ptr', 'builtin:%s' % tgt['name'], 'array'))
                else:
                    u = self.resolve(tgt['name'])
                    out.append(('ptr', u or 'unresolved:%s' % tgt['name'], 'array'))
                return out
            if ek == 'function_pointer':
                out.append(('fn', None, 'array'))
                return out
            if ek in ('record', 'template_spec', 'typedef', 'elaborated') or (et and not ek == 'builtin'):
                return self._record_edges(et, out, 'array')
            return out
        if ck in ('record', 'template_spec', 'dependent', 'template_param'):
            return self._record_edges(ct, out, 'direct')
        return out

    def _record_edges(self, t, out, how):
        refs = self.type_refs(t)
        top = [r for r in refs if r['depth'] == 0 and not r['builtin']]
        if not top:
            return out
        r0 = top[0]
        name0 = r0['name']
        if name0 == OBSTRING:
            out.append(('str', None, how))
            return out
        u0 = self.resolve(name0)
        transparent = self.is_transparent(u0, name0)
        if not transparent:
            out.append(('value', u0, how))
        elif u0 is not None:
            out.append(('infra', u0, how))
        # template arguments (every depth): contained by value or by pointer
        for r in refs:
            if r['depth'] == 0:
                continue
            if r['builtin']:
                if r['indirect']:
                    out.append(('ptr', 'builtin:%s' % r['name'], 'elem'))
                continue
            if r.get('param'):
                if r['indirect']:
                    out.append(('ptr', 'param:%s' % r['name'], 'elem'))
                continue
            if r.get('fn'):
                continue
            if r['name'] == OBSTRING and not r['indirect']:
                out.append(('str', None, 'elem'))
                continue
            u = self.resolve(r['name'])
            if r['indirect']:
                out.append(('ptr', u or 'unresolved:%s' % r['name'], 'elem'))
            elif u is not None and not self.is_transparent(u, r['name']):
                out.append(('value', u, 'elem'))
        return out

    # ------------------------------------------------------------ member graph
    def member_edges(self, usr):
        """[(field_record, edges)] for the record's own fields (anonymous members inlined)."""
        if usr not in self._medges:
            self._medges[usr] = [(f, self.field_edges(f)) for f in self.fields(usr)]
        return self._medges[usr]

    def reach(self, roots, follow_ptr=None, with_ancestors=True):
        """Records reachable from roots by value edges (direct member, array element, container
        element) and, where follow_ptr(target) is true, by pointer/reference edges (the target's
        derived records too).  Ancestors of every reached record are included (base subobjects)."""
        seen = set()
        todo = sorted(set(roots), reverse=True)
        while todo:
            u = todo.pop()
            if u in seen or u not in self.defs:
                continue
            seen.add(u)
            nxt = []
            if with_ancestors:
                nxt.extend(self.ancestors(u))
            for f, es in self.member_edges(u):
                for kind, tgt, how in es:
                    if kind == 'value' and tgt:
                        nxt.append(tgt)
                    elif kind in ('ptr', 'ref') and follow_ptr is not None and tgt in self.defs and follow_ptr(tgt):
                        nxt.append(tgt)
                        nxt.extend(self.derived(tgt))
            for v in sorted(set(nxt), reverse=True):
                if v not in seen:
                    todo.append(v)
        return seen

    # ------------------------------------------------------------ type sets
    def family(self, qname):
        """USR of qname plus every derived record USR (sorted), or [] if qname has no definition."""
        u = self.resolve(qname)
        if u is None:
            return []
        return sorted(set([u] + self.derived(u)))

    def _sets(self):
        self.ir = set()
        self.ir_root_of = {}
        for q in IR_ROOTS:
            for u in self.family(q):
                self.ir.add(u)
                self.ir_root_of.setdefault(u, q.split('::')[-1])
        self.plan_nodes = set()
        for q in PLAN_ROOTS:
            self.plan_nodes.update(self.family(q))
        self.exprop = set(self.family(EXPROP_ROOT))
        self.session = set()
        for q in SESSION_ROOTS:
            self.session.update(self.family(q))
        # allocators: ObIAllocator and derived, plus records named *Alloc*/*Arena* that define alloc()
        self.allocators = set(self.family(IALLOC))
        alloc_methods = set()
        if self.has_funcs:
            for r in self.db.records:
                if r['kind'] in ('method', 'function_template') and r['name'] in ('alloc', 'alloc_align', 'allocate'):
                    alloc_methods.add(r.get('parent_usr'))
        for u, r in self.defs.items():
            if re.search(r'(Alloc|Arena)', r['name']) and (u in alloc_methods or not self.has_funcs):
                if not re.search(r'(Guard|Attr|Mgr|Handle|Wrapper$)', r['name']) or u in alloc_methods:
                    self.allocators.add(u)
        # lock primitives
        self.locks = set()
        for u, r in self.defs.items():
            nm = r['name']
            if r['file'].startswith('src/oblib/lib/lock/') and not re.search(r'(Guard|Type|Mode|State|Item|Proc|'
                                                                            r'Desc|Base|Ids|Policy|Stat)', nm):
                self.locks.add(u)
            elif nm in LOCK_NAMES and (r['file'].startswith('src/oblib/') or nm in ('CtxLock', 'MemtableMgrLock',
                                                                                     'ObLSLock', 'ObRowLatch')):
                self.locks.add(u)
        self.modules, self.module_why = set(), collections.defaultdict(set)
        self._modules_from_ast()

    def _modules_from_ast(self):
        g = self.resolve(GCTX)
        if g:
            self.modules.add(g)
            self.module_why[g].add('GCTX')
            for f in self.fields(g):
                if f.get('canonical_kind') == 'pointer' and f.get('pointee_kind') == 'record':
                    u = self.resolve(f.get('pointee_qname'))
                    if u:
                        self.modules.add(u)
                        self.module_why[u].add('GCTX')
        if self.has_funcs:
            for r in self.db.records:
                if r['kind'] == 'method' and r['name'] == 'get_instance' and r.get('is_static') and \
                        r.get('parent_usr') in self.rec_any:
                    self.modules.add(r['parent_usr'])
                    self.module_why[r['parent_usr']].add('get_instance')

    RX_SVC = re.compile(r'\b(?:un)?bind_server_service\s*<\s*([^<>;()]+(?:<[^<>;()]*>)?)\s*>\s*\(|'
                        r'\bserver_service\s*<\s*([^<>;()]+(?:<[^<>;()]*>)?)\s*>\s*\(|'
                        r'\b(?:UN)?BIND_SERVICE\s*\(\s*(?:\w+\s*,\s*)?([A-Za-z_:][\w:]*)\s*\)')

    def add_service_modules(self):
        """Text scan: types named in server_service<T>() / bind_server_service<T> / BIND_SERVICE(_, T).
        Returns {usr: count of sites}.  Abstract service interfaces also make their derived classes modules."""
        counts = collections.Counter()
        for p in L.first_party():
            if 'SERVICE' not in L.raw(p) and 'server_service' not in L.raw(p):
                continue
            for m in self.RX_SVC.finditer(L.stripped(p)):
                name = m.group(1) or m.group(2) or m.group(3)
                if not name or name.strip() in ('Service', 'type', 'T', 'common::ObServerObjectPool<T>'):
                    continue
                for u in self.resolve_written(name):
                    counts[u] += 1
        for u in sorted(counts):
            self.modules.add(u)
            self.module_why[u].add('server_service')
        for u in sorted(self.modules):
            r = self.defs.get(u)
            if r is not None and r.get('is_abstract') and 'server_service' in self.module_why[u]:
                concrete = [d for d in self.derived(u) if d in self.defs and not self.defs[d].get('is_abstract')]
                if len(concrete) == 1:
                    self.modules.add(concrete[0])
                    self.module_why[concrete[0]].add('implements:%s' % r['name'])
        return counts

    # ------------------------------------------------------------ functions and bodies
    def func_def(self, usr):
        """Definition record of a function USR (is_definition, has a body extent), else None."""
        best = None
        for r in self.db.get(usr):
            if r['kind'] in FK and r.get('is_definition') and not r.get('is_deleted') and not r.get('is_defaulted'):
                if best is None or (r['file'], r['line']) < (best['file'], best['line']):
                    best = r
        return best

    def body(self, rec):
        """Stripped text of a function definition record's extent [line, end_line] (empty if unknown)."""
        if rec is None or 'end_line' not in rec:
            return ''
        f = rec['file']
        if not os.path.exists(os.path.join(REPO, f)):
            return ''
        lines = L.stripped_lines(f)
        a, b = rec['line'], rec.get('end_line', rec['line'])
        if b < a or a < 1:
            return ''
        return '\n'.join(lines[a - 1:b])

    def methods_of(self, usr, names_rx=None):
        """Function records (one per USR, canonical) whose parent is the record usr."""
        out = []
        seen = set()
        for r in self.db.by_parent_index.get(usr, []):
            if r['kind'] in FK and r['usr'] not in seen:
                if names_rx is None or names_rx.match(r['name']):
                    seen.add(r['usr'])
                    out.append(self.db.canonical(r['usr']) or r)
        out.sort(key=lambda r: (r['file'], r['line'], r['usr']))
        return out

    def func_records(self, owner, kinds=('ctor',)):
        """Definition records (one per USR) of the owner's functions of the given kinds, sorted."""
        out, seen = [], set()
        for r in self.db.by_parent_index.get(owner, []):
            if r['kind'] in kinds and r['usr'] not in seen:
                seen.add(r['usr'])
                d = self.func_def(r['usr'])
                if d is not None:
                    out.append(d)
        out.sort(key=lambda r: (r['file'], r['line'], r['usr']))
        return out

    def member_inits(self, owner):
        """{field name: [(how, expr)]} for the record's constructors: init-list items
        ('init-list'), assignments in constructor bodies ('ctor-body'), and default member
        initializers ('default-init').  expr is whitespace-normalised stripped text."""
        key = ('inits', owner)
        if key in self._fields:
            return self._fields[key]
        out = collections.defaultdict(list)
        rec = self.defs.get(owner)
        cname = rec['name'] if rec else ''
        for d in self.func_records(owner, ('ctor',)):
            text = self.body(d)
            if not text:
                continue
            init, body = split_ctor(text, cname)
            for name, args in init:
                out[name.split('::')[-1]].append(('init-list', ' '.join(args.split())))
            for mm in RX_ASSIGN.finditer(body):
                out[mm.group(1)].append(('ctor-body', ' '.join(mm.group(2).split())))
        for f in self.fields(owner):
            if f.get('has_init') and os.path.exists(os.path.join(REPO, f['file'])):
                ln = L.stripped_lines(f['file'])[f['line'] - 1] if f['line'] - 1 < len(L.stripped_lines(f['file'])) else ''
                mm = re.search(r'\b%s\s*(?:=\s*|\{\s*)([^;{}]*)' % re.escape(f['name']), ln)
                if mm:
                    out[f['name']].append(('default-init', ' '.join(mm.group(1).split()).rstrip('}').strip()))
        self._fields[key] = out
        return out

    def member_assignments(self, owner):
        """{field name: [(method name, rhs)]} for plain assignments 'name = rhs;' (or 'this->name = rhs;')
        in the bodies of all the owner's functions (methods, constructors, destructors)."""
        key = ('assign', owner)
        if key not in self._fields:
            out = collections.defaultdict(list)
            for d in self.func_records(owner, ('method', 'ctor', 'dtor', 'function_template', 'conversion')):
                body = self.body(d)
                if '=' not in body:
                    continue
                b = body.find('{')
                for mm in RX_ASSIGN.finditer(body, max(b, 0)):
                    out[mm.group(1)].append((d['name'], ' '.join(mm.group(2).split())))
            self._fields[key] = out
        return self._fields[key]

    def cleanup_bodies(self, owner):
        """Stripped bodies of the owner's destructor and destroy/reset/free/clear/release-like methods."""
        key = ('cleanup', owner)
        if key not in self._fields:
            out = []
            for d in self.func_records(owner, ('dtor', 'method')):
                if d['kind'] == 'dtor' or RX_CLEANUP_NAME.match(d['name']):
                    out.append((d['name'] if d['kind'] != 'dtor' else '~', self.body(d)))
            self._fields[key] = out
        return self._fields[key]

    def unit(self, f):
        return self.units.get(f)

    def crate(self, f):
        row = self.units.get(f)
        return row['crate'] if row else ''

    def disposition(self, f):
        row = self.units.get(f)
        return row['disposition'] if row else ''

    def disp_detail(self, f):
        """'disp=<disposition>' for files whose unit is not translated (replace:, rewrite:, keep-, ...)."""
        d = self.disposition(f)
        return 'disp=%s' % d if d and d != 'translate' else ''

    # ------------------------------------------------------------ containing-class scope hints
    def scope_sets(self):
        """Containing-class sets used by the c04/c05 scope hints (README: 'scope hints')."""
        if hasattr(self, '_scope_sets'):
            return self._scope_sets
        plan_roots = set()
        for q in PLAN_ROOTS + PLAN_EXTRA_ROOTS:
            plan_roots.update(self.family(q))
        for u, r in self.defs.items():
            if re.search(r'CtDef$', r['name']) and r['file'].startswith(('src/sql/', 'src/query/')):
                plan_roots.add(u)
        schema_roots = set(u for u, r in self.defs.items() if r['file'].startswith(SCHEMA_DIRS))
        schema_roots.update(self.family('oceanbase::share::schema::ObSchema'))
        locked = set()
        for u in self.defs:
            for f, es in self.member_edges(u):
                if any(k == 'value' and t in self.locks for k, t, h in es) or \
                        S_LOCK_CANON.search(f.get('canonical_type', '') or ''):
                    locked.add(u)
                    break
        self._scope_sets = {
            'ir': self.reach(self.ir),
            'plan': self.reach(plan_roots),
            'session': self.reach(self.session),
            'schema': self.reach(schema_roots),
            'module': self.reach(self.modules),
            'locked': locked,
        }
        return self._scope_sets

    def scope_hints(self, owner):
        """Hint tags for the lifetime of a containing class: in-ir-node, in-plan, in-session, in-schema,
        long-lived-candidate, query-candidate, crosses-calls (see README)."""
        ss = self.scope_sets()
        r = self.defs.get(owner) or self.rec_any.get(owner) or {}
        f = r.get('file', '')
        tags = []
        if owner in ss['ir']:
            tags.append('in-ir-node')
        if owner in ss['plan']:
            tags.append('in-plan')
        if owner in ss['session']:
            tags.append('in-session')
        if owner in ss['schema']:
            tags.append('in-schema')
        long_lived = (owner in ss['module'] or owner in ss['session'] or owner in ss['schema'] or
                      owner in ss['locked'] or f.startswith(LONG_LIVED_DIRS))
        names = [self.defs[a]['name'] for a in [owner] + list(self.ancestors(owner)) if a in self.defs]
        crosses = any(RX_CROSS_CALLS.search(n) for n in names)
        if long_lived:
            tags.append('long-lived-candidate')
        elif owner in ss['ir'] or (f.startswith(QUERY_DIRS) and owner not in ss['plan']):
            tags.append('query-candidate')
        if crosses:
            tags.append('crosses-calls')
        return tags


RX_ASSIGN = re.compile(r'(?<![\w.>:*])(?:this\s*->\s*)?([A-Za-z_]\w*)\s*=(?!=)\s*([^;]*);')
RX_CLEANUP_NAME = re.compile(r'^(destroy\w*|reset\w*|free\w*|clear\w*|release\w*|deinit\w*|destruct\w*|clean\w*|'
                             r'reuse\w*|fini\w*|uninit\w*)$')
RX_INIT_ITEM = re.compile(r'\s*,?\s*([A-Za-z_][\w:]*(?:\s*<[^(){};]*>)?)\s*([({])')


def split_ctor(text, cname):
    """(init-list items [(name, args)], body text) of a constructor definition's stripped text."""
    m = None
    for mm in re.finditer(r'\b%s\s*\(' % re.escape(cname), text):
        m = mm
        break
    if m is None:
        i = text.find('(')
        if i < 0:
            return [], text
    else:
        i = m.end() - 1
    j = matching(text, i)
    k = j + 1
    rest = text[k:]
    mm = re.match(r'\s*(?:noexcept(?:\s*\([^()]*\))?\s*|throw\s*\(\s*\)\s*|[A-Z_]{3,}\s*)*', rest)
    k += mm.end() if mm else 0
    items = []
    if text[k:k + 1] == ':' and text[k:k + 2] != '::':
        i = k + 1
        n = len(text)
        while i < n:
            mi = RX_INIT_ITEM.match(text, i)
            if not mi:
                break
            open_c = mi.group(2)
            close_c = ')' if open_c == '(' else '}'
            e = matching(text, mi.end(2) - 1, open_c, close_c)
            items.append((re.sub(r'\s+', '', mi.group(1)), text[mi.end(2):e]))
            i = e + 1
            while i < n and text[i] in ' \t\n':
                i += 1
            if text[i:i + 1] == ',':
                continue
            break
        k = i
    b = text.find('{', k)
    body = text[b:] if b >= 0 else ''
    return items, body


def addr_member(expr):
    """'x_' for an init expression '&x_', '&x_.y', '&(x_)', 'x_.get_allocator()'-free forms; 'this' for this."""
    e = expr.strip()
    if re.match(r'^\(?\s*this\s*\)?$', e) or re.match(r'^static_cast<[^<>]*>\s*\(\s*this\s*\)$', e):
        return 'this'
    m = re.match(r'^&\s*\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)', e)
    if m:
        return m.group(1)
    return None


RX_PLACEMENT_RHS = re.compile(r'^(?:\([^()]*\)\s*|static_cast<[^<>]*>\s*\(\s*|reinterpret_cast<[^<>]*>\s*\(\s*)?'
                              r'new\s*\(\s*&?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)')


def free_patterns(n):
    v = r'(?:this\s*->\s*)?%s' % re.escape(n)
    return [
        re.compile(r'\bOB_DELETEx?\s*\([^;]*?[,(]\s*%s\s*\)' % v),
        re.compile(r'\bob_delete\s*\(\s*%s\s*\)' % v),
        re.compile(r'\bdelete\s*(?:\[\s*\]\s*)?%s\b' % v),
        re.compile(r'(?<![\w.>])%s\s*->\s*~\w+\s*\(' % v),
        re.compile(r'\b\w*(?:free|destroy|delete)\w*\s*\(\s*%s\s*[,)]' % v),
        re.compile(r'\b(?:op_reclaim_free|rp_free|sop_return|ob_free_align)\s*\(\s*%s\b' % v),
    ]


def release_patterns(n):
    v = r'(?:this\s*->\s*)?%s' % re.escape(n)
    return [
        re.compile(r'(?<![\w.>])%s\s*->\s*(?:dec_ref\w*|release\w*|revert\w*|dec_\w*ref\w*)\s*\(' % v),
        re.compile(r'\b\w*(?:revert|dec_ref|release|return_)\w*\s*\(\s*%s\s*[,)]' % v),
    ]


class Members(object):
    """Member-level rules shared by c03, c04, c05 and c19 (see README.md)."""

    def __init__(self, m):
        self.m = m
        self._mentions = {}

    def mentions(self, p):
        """{target usr: [member name]} for every record named in the fields of p or its ancestors."""
        if p in self._mentions:
            return self._mentions[p]
        out = collections.defaultdict(list)
        for u in [p] + list(self.m.ancestors(p)):
            for f, es in self.m.member_edges(u):
                for kind, tgt, how in es:
                    if tgt in self.m.defs:
                        out[tgt].append('%s(%s)' % (self.m.chain(u), f['name']))
        self._mentions[p] = out
        return out

    def domain_family(self, owner):
        """owner plus its ancestors that are not replaced infrastructure (ObLink, ObDLinkBase, ...)."""
        out = [owner]
        for a in self.m.ancestors(owner):
            r = self.m.defs.get(a)
            if r is not None and not self.m.is_transparent(a, r['qname']):
                out.append(a)
        return out

    def host_of(self, owner, tgt):
        """'<P>(<member>)' if pointee record tgt holds the owner (or a domain base of it), else None."""
        m = self.m
        if tgt not in m.defs or tgt == owner:
            return None
        fam = set([owner] + list(m.ancestors(owner)) + list(m.derived(owner)))
        if tgt in fam:
            return None
        men = self.mentions(tgt)
        for u in self.domain_family(owner):
            if u in men:
                return sorted(men[u])[0]
        return None

    def value_members(self, owner):
        """Names of the owner's (and its bases') data members held by value (not pointers/references)."""
        return set(f['name'] for f in self.m.fields(owner, inherited=True)
                   if f.get('canonical_kind') not in ('pointer', 'lvalue_ref', 'rvalue_ref', 'function_pointer'))

    def self_sibling(self, owner, fname, is_ref=False):
        """(how, expr) when the member is set to `this`, to the address of a by-value member of the owner
        (constructor init list, constructor body, default member initializer), or to a placement new into
        such a member in any of the owner's functions.  For a reference member: bound to `*this` or to a
        by-value member."""
        inits = self.m.member_inits(owner)
        vals = self.value_members(owner)
        for how, expr in inits.get(fname, []):
            if is_ref:
                e = expr.strip()
                mm = re.match(r'^\(?\s*(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\)?$', e)
                if re.match(r'^\*\s*this$', e) or (mm and mm.group(1) in vals and mm.group(1) != fname):
                    return how, expr
                continue
            a = addr_member(expr)
            if a == 'this' or (a is not None and a in vals and a != fname and '(' not in expr):
                return how, expr
        for meth, rhs in self.m.member_assignments(owner).get(fname, []):
            mm = RX_PLACEMENT_RHS.match(rhs)
            if mm and mm.group(1) in vals:
                return 'placement-new', rhs[:60]
        return None

    def cleanup(self, owner, fname):
        dels, rels = [], []
        fp, rp = free_patterns(fname), release_patterns(fname)
        for meth, body in self.m.cleanup_bodies(owner):
            if not body or fname not in body:
                continue
            if any(p.search(body) for p in fp):
                dels.append(meth)
            elif any(p.search(body) for p in rp):
                rels.append(meth)
        return sorted(set(dels)), sorted(set(rels))



# ---------------------------------------------------------------- regex fallback for unparsed files
RX_MEMBER_STMT = re.compile(r'(?s)^\s*(?:(?:public|private|protected)\s*:\s*)*(.*)$')


def class_scopes(path):
    """(Scope, qualified chain) for class scopes of a file, via sweeplib's scope parser."""
    out = []
    for s in L.scopes(path):
        if s.kind == 'class':
            names = [c.name or 'anon' for c in L.scope_chain(path, s.open + 1) if c.kind == 'class']
            out.append((s, '::'.join(names)))
    return out


def member_decls(path):
    """Regex fallback: data member declarations directly in class bodies of an unparsed file.
    Yields dicts {cls, name, type, ptr, ref, line, fnptr, static}."""
    text = L.stripped(path)
    st = L.structural_text(path)
    for s, chain in class_scopes(path):
        body_start, body_end = s.open + 1, s.close if s.close is not None else len(text)
        # blank nested scopes
        buf = list(st[body_start:body_end])
        for c in L.scopes(path):
            if c is s or c.close is None:
                continue
            if body_start <= c.open < body_end:
                lo = c.open - body_start
                hi = min(c.close, body_end - 1) - body_start
                for k in range(lo, hi + 1):
                    if buf[k] != '\n':
                        buf[k] = ' '
                # keep a marker so that a nested class/function header ends a statement
                buf[lo] = ';'
        seg = ''.join(buf)
        pos = 0
        for stmt in seg.split(';'):
            start = body_start + pos
            pos += len(stmt) + 1
            m = RX_MEMBER_STMT.match(stmt)
            decl = ' '.join(m.group(1).split())
            if not decl or decl.startswith(('typedef', 'using', 'friend', 'template', 'enum ', 'return',
                                            'class ', 'struct ', 'union ', 'namespace', '#')):
                continue
            if re.match(r'^[A-Z_][A-Z0-9_]*\s*\(', decl) or re.search(r'\boperator\b', decl):
                continue  # macro invocation or operator declaration
            is_static = bool(re.match(r'^(?:static|constexpr|inline)\b', decl))
            fnptr = re.search(r'\(\s*\*\s*(\w+)\s*\)\s*\(', decl)
            if '(' in decl and not fnptr:
                if not re.search(r'[{=]', decl.split('(')[0]):
                    continue  # function declaration
            decl = re.sub(r'\b(?:mutable|static|volatile|constexpr|inline)\b\s*', '', decl)
            decl = re.sub(r'\s*(=[^,]*|\{[^{}]*\})\s*$', '', decl) if not fnptr else decl
            off = start + (len(stmt) - len(stmt.lstrip()))
            line = L.line_of(path, off)
            if fnptr:
                yield {'cls': chain, 'name': fnptr.group(1), 'type': decl, 'ptr': True, 'ref': False,
                       'line': line, 'fnptr': True, 'static': is_static}
                continue
            parts = split_top(decl)
            mm = re.match(r'^(.*?)([*&\s]*)\b([A-Za-z_]\w*)\s*(\[[^\]]*\])*\s*(?:=.*|\{.*\})?$', parts[0].strip())
            if not mm:
                continue
            base = mm.group(1).strip()
            if not base or base in ('return', 'else', 'case', 'goto', 'delete', 'throw'):
                continue
            for k, d in enumerate(parts):
                d = d.strip()
                if k == 0:
                    quals, name = mm.group(2), mm.group(3)
                    arr = mm.group(4) or ''
                else:
                    md = re.match(r'^([*&\s]*)\b([A-Za-z_]\w*)\s*(\[[^\]]*\])*', d)
                    if not md:
                        continue
                    quals, name, arr = md.group(1), md.group(2), md.group(3) or ''
                yield {'cls': chain, 'name': name, 'type': (base + ' ' + quals.strip() + arr).strip(),
                       'ptr': '*' in quals or base.endswith('*'), 'ref': '&' in quals or base.endswith('&'),
                       'line': line, 'fnptr': False, 'static': is_static, 'base': base}


def fmt_tags(tags, details=()):
    """'tag1,tag2;k=v;k=v': hint tags (no '=') comma-separated first, then ';'-separated key=value
    details.  Tags are de-duplicated in first-seen order; with no tags the string starts with a detail."""
    seen, ts = set(), []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            ts.append(t)
    s = ','.join(ts)
    ds = [d.replace(';', ' ') for d in details if d]
    if ds:
        s = (s + ';' if s else '') + ';'.join(ds)
    return s


def tags_of(pretag):
    """The hint tags of a pretag string written by fmt_tags()."""
    head = pretag.split(';', 1)[0]
    if '=' in head or not head:
        return []
    return head.split(',')
